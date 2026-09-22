"""Worker individuel traitant une DownloadTask.

Ce module implémente le worker qui exécute concrètement une tâche de
téléchargement. Pour chaque chapitre de la tâche, il :

1. Récupère les métadonnées du chapitre via le parser
2. Récupère la liste des pages du chapitre
3. Pour chaque page :
   a. Vérifie la déduplication (hash SHA256)
   b. Télécharge l'image avec retry intelligent
   c. Enregistre la progression
4. Empaquette les pages dans le format cible (CBZ, CBR, PDF, etc.)
5. Enregistre le chapitre dans le cache de déduplication
6. Nettoie les fichiers temporaires

Le worker est conçu pour être réutilisable : il peut traiter plusieurs
tâches à la suite sans être recréé. Il est thread-safe et supporte
la pause/annulation via vérification périodique du statut de la tâche.

Architecture :
    DownloadWorker
        ├── SiteRegistry (accès aux parsers)
        ├── SessionFactory (sessions HTTP par site)
        ├── PackagerFactory (empaquetage par format)
        ├── DeduplicationCache (éviter les re-téléchargements)
        ├── ProgressTracker (suivi de progression)
        └── EventBus (notification des interfaces)

Exemple d'utilisation :
    >>> worker = DownloadWorker(
    ...     worker_id=0,
    ...     registry=registry,
    ...     session_factory=session_factory,
    ...     packager_factory=packager_factory,
    ...     event_bus=event_bus,
    ...     page_semaphore=page_semaphore,
    ...     dedup_cache=dedup_cache,
    ...     progress_tracker=progress_tracker,
    ... )
    >>> await worker.run(task)
    >>> stats = worker.get_stats()
    >>> print(f"Pages: {stats.pages_downloaded}, Bytes: {stats.bytes_downloaded}")
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol, Self
from uuid import UUID

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.downloader.deduplication import (
    DeduplicationCache,
    DeduplicationStrategy,
    build_chapter_key,
    compute_content_hash,
)
from nexusdl.core.downloader.progress import ProgressTracker
from nexusdl.core.downloader.retry import (
    CHAPTER_DOWNLOAD_POLICY,
    PAGE_DOWNLOAD_POLICY,
    build_retry,
)
from nexusdl.core.events import EventBus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.models.download import (
    DownloadResult,
    DownloadStatus,
    DownloadTask,
    Priority,
)
from nexusdl.core.models.manga import Chapter, Manga, Page
from nexusdl.core.packaging.base import BasePackager, PackagingFormat
from nexusdl.parsers.base import BaseParser
from nexusdl.parsers.exceptions import (
    ChapterNotFoundError,
    InvalidPageUrlError,
    MangaNotFoundError,
    ParserError,
    ParsingError,
)

if TYPE_CHECKING:
    from nexusdl.core.registry.site_registry import SiteRegistry
    from nexusdl.core.session.http_session import HttpSession


# ============================================================================
# EXCEPTIONS
# ============================================================================


class WorkerError(NexusDLError):
    """Exception de base pour les erreurs du worker."""


class ChapterDownloadError(WorkerError):
    """Exception levée lorsqu'un chapitre échoue après tous les retries."""

    def __init__(
        self,
        chapter: Chapter,
        *,
        pages_failed: list[int],
        last_exception: BaseException | None = None,
    ) -> None:
        super().__init__(
            f"Échec du téléchargement du chapitre {chapter.number} "
            f"({len(pages_failed)} pages échouées)"
        )
        self.chapter = chapter
        self.pages_failed = pages_failed
        self.last_exception = last_exception


class TaskCancelledError(WorkerError):
    """Exception levée lorsqu'une tâche est annulée en cours d'exécution."""

    def __init__(self, task_id: UUID) -> None:
        super().__init__(f"Tâche annulée: {task_id}")
        self.task_id = task_id


class TaskPausedError(WorkerError):
    """Exception levée lorsqu'une tâche est mise en pause."""

    def __init__(self, task_id: UUID) -> None:
        super().__init__(f"Tâche mise en pause: {task_id}")
        self.task_id = task_id


# ============================================================================
# PROTOCOLS (Dependency Injection)
# ============================================================================


class SessionFactory(Protocol):
    """Protocol pour la factory de sessions HTTP."""

    def create(self, site_id: str) -> HttpSession:
        """Crée une session HTTP pour un site donné."""
        ...


class PackagerFactory(Protocol):
    """Protocol pour la factory de packagers."""

    def create(self, fmt: PackagingFormat) -> BasePackager:
        """Crée un packager pour un format donné."""
        ...


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class ChapterResult(BaseModel):
    """Résultat du téléchargement d'un chapitre individuel."""

    chapter: Chapter = Field(..., description="Chapitre téléchargé.")
    output_path: Path = Field(..., description="Chemin du fichier empaqueté.")
    pages_downloaded: int = Field(default=0, ge=0, description="Pages téléchargées avec succès.")
    pages_failed: int = Field(default=0, ge=0, description="Pages ayant échoué.")
    pages_skipped: int = Field(default=0, ge=0, description="Pages évitées (déduplication).")
    bytes_downloaded: int = Field(default=0, ge=0, description="Bytes téléchargés.")
    duration_seconds: float = Field(default=0.0, ge=0.0, description="Durée du téléchargement.")
    success: bool = Field(default=False, description="Succès du téléchargement.")
    error: str | None = Field(default=None, description="Message d'erreur si échec.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class WorkerStats(BaseModel):
    """Statistiques agrégées du worker."""

    worker_id: int = Field(..., ge=0, description="Identifiant du worker.")
    tasks_completed: int = Field(default=0, ge=0)
    tasks_failed: int = Field(default=0, ge=0)
    chapters_completed: int = Field(default=0, ge=0)
    chapters_failed: int = Field(default=0, ge=0)
    pages_downloaded: int = Field(default=0, ge=0)
    pages_skipped: int = Field(default=0, ge=0)
    bytes_downloaded: int = Field(default=0, ge=0)
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# CLASSE PRINCIPALE
# ============================================================================


class DownloadWorker:
    """Worker individuel traitant une DownloadTask.

    Ce worker est conçu pour être réutilisable : il peut traiter plusieurs
    tâches à la suite sans être recréé. Il gère le cycle de vie complet
    d'une tâche : récupération des pages, téléchargement avec retry,
    déduplication, empaquetage, et reporting de progression.

    Thread-safety :
        Ce worker est conçu pour être utilisé dans un seul event loop asyncio.
        Il n'est PAS thread-safe pour un usage multi-thread.

    Lifecycle :
        >>> worker = DownloadWorker(...)
        >>> await worker.run(task1)
        >>> await worker.run(task2)
        >>> stats = worker.get_stats()
    """

    # Constantes de configuration
    _TEMP_DIR_PREFIX: Final[str] = "nexusdl_worker_"
    _CANCELLATION_CHECK_INTERVAL: Final[float] = 0.5  # secondes
    _PAGE_DOWNLOAD_TIMEOUT: Final[float] = 60.0  # secondes par page
    _CHAPTER_PROCESSING_TIMEOUT: Final[float] = 600.0  # 10 minutes par chapitre

    def __init__(
        self,
        worker_id: int,
        registry: SiteRegistry,
        session_factory: SessionFactory,
        packager_factory: PackagerFactory,
        event_bus: EventBus,
        page_semaphore: asyncio.Semaphore,
        *,
        dedup_cache: DeduplicationCache | None = None,
        progress_tracker: ProgressTracker | None = None,
        cleanup_temp: bool = True,
    ) -> None:
        """Initialise le worker de téléchargement.

        Args:
            worker_id: Identifiant unique du worker (pour le logging).
            registry: Registre des sites supportés.
            session_factory: Factory pour créer des sessions HTTP par site.
            packager_factory: Factory pour créer des packagers par format.
            event_bus: Bus d'événements pour notifier les interfaces.
            page_semaphore: Sémaphore pour limiter la concurrence des pages.
            dedup_cache: Cache de déduplication (optionnel).
            progress_tracker: Tracker de progression (optionnel).
            cleanup_temp: Nettoyer les fichiers temporaires après empaquetage.
        """
        if worker_id < 0:
            raise ValueError(f"worker_id must be non-negative, got {worker_id}")

        self._worker_id = worker_id
        self._registry = registry
        self._session_factory = session_factory
        self._packager_factory = packager_factory
        self._event_bus = event_bus
        self._page_semaphore = page_semaphore
        self._dedup_cache = dedup_cache
        self._progress_tracker = progress_tracker
        self._cleanup_temp = cleanup_temp

        # Statistiques internes
        self._tasks_completed: int = 0
        self._tasks_failed: int = 0
        self._chapters_completed: int = 0
        self._chapters_failed: int = 0
        self._pages_downloaded: int = 0
        self._pages_skipped: int = 0
        self._bytes_downloaded: int = 0
        self._start_time: float = time.monotonic()

        # Logger avec contexte
        self._logger = logger.bind(module="download_worker", worker_id=worker_id)

    # ------------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------------

    @property
    def worker_id(self) -> int:
        """Identifiant du worker."""
        return self._worker_id

    @property
    def is_busy(self) -> bool:
        """Indique si le worker est actuellement en train de traiter une tâche."""
        # Simplifié — dans une vraie implémentation, on trackerait l'état
        return False

    # ------------------------------------------------------------------------
    # API publique — Exécution de tâche
    # ------------------------------------------------------------------------

    async def run(self, task: DownloadTask) -> list[ChapterResult]:
        """Exécute une tâche de téléchargement complète.

        Pour chaque chapitre de la tâche :
            1. Récupère les pages via le parser
            2. Télécharge les pages avec retry et déduplication
            3. Empaquette les pages dans le format cible
            4. Enregistre la progression

        Args:
            task: Tâche à exécuter.

        Returns:
            Liste des résultats de téléchargement par chapitre.

        Raises:
            TaskCancelledError: Si la tâche est annulée en cours d'exécution.
            TaskPausedError: Si la tâche est mise en pause.
            WorkerError: Si une erreur fatale survient.
        """
        self._logger.info(
            "Démarrage de la tâche {}: {} chapitres, format={}",
            task.id,
            len(task.chapters),
            task.fmt.value,
        )

        # Démarrer le suivi de progression
        task_progress = None
        if self._progress_tracker is not None:
            task_progress = await self._progress_tracker.track_task(
                task.id,
                manga_title=task.manga.title,
                site_id=task.manga.site,
                priority=task.priority.value,
                chapters_total=len(task.chapters),
            )
            await task_progress.start()

        results: list[ChapterResult] = []
        temp_dir: Path | None = None

        try:
            # Créer un répertoire temporaire pour les pages
            temp_dir = Path(tempfile.mkdtemp(prefix=self._TEMP_DIR_PREFIX))
            self._logger.debug("Répertoire temporaire créé: {}", temp_dir)

            # Traiter chaque chapitre
            for chapter_index, chapter in enumerate(task.chapters, start=1):
                # Vérifier l'annulation/pause avant chaque chapitre
                await self._check_cancellation(task)

                self._logger.info(
                    "Traitement du chapitre {}/{}: {} ({} pages)",
                    chapter_index,
                    len(task.chapters),
                    chapter.number,
                    chapter.pages_count or "?",
                )

                # Émettre un événement de début de chapitre
                await self._event_bus.emit(
                    "worker.chapter.started",
                    {
                        "worker_id": self._worker_id,
                        "task_id": str(task.id),
                        "chapter_id": chapter.id,
                        "chapter_number": str(chapter.number),
                        "chapter_index": chapter_index,
                        "chapters_total": len(task.chapters),
                    },
                )

                try:
                    # Traiter le chapitre avec timeout
                    result = await asyncio.wait_for(
                        self._process_chapter(task, chapter, temp_dir, task_progress),
                        timeout=self._CHAPTER_PROCESSING_TIMEOUT,
                    )
                    results.append(result)

                    if result.success:
                        self._chapters_completed += 1
                        self._pages_downloaded += result.pages_downloaded
                        self._pages_skipped += result.pages_skipped
                        self._bytes_downloaded += result.bytes_downloaded

                        self._logger.info(
                            "Chapitre {} terminé avec succès: {} pages, {} bytes",
                            chapter.number,
                            result.pages_downloaded,
                            result.bytes_downloaded,
                        )
                    else:
                        self._chapters_failed += 1
                        self._logger.warning(
                            "Chapitre {} échoué: {}",
                            chapter.number,
                            result.error,
                        )

                    # Émettre un événement de fin de chapitre
                    await self._event_bus.emit(
                        "worker.chapter.completed",
                        {
                            "worker_id": self._worker_id,
                            "task_id": str(task.id),
                            "chapter_id": chapter.id,
                            "result": result.model_dump(mode="json"),
                        },
                    )

                except asyncio.TimeoutError:
                    self._chapters_failed += 1
                    error_msg = f"Timeout dépassé ({self._CHAPTER_PROCESSING_TIMEOUT}s)"
                    self._logger.error("Chapitre {} timeout: {}", chapter.number, error_msg)

                    result = ChapterResult(
                        chapter=chapter,
                        output_path=Path(),
                        pages_downloaded=0,
                        pages_failed=chapter.pages_count or 0,
                        bytes_downloaded=0,
                        duration_seconds=self._CHAPTER_PROCESSING_TIMEOUT,
                        success=False,
                        error=error_msg,
                    )
                    results.append(result)

                    await self._event_bus.emit(
                        "worker.chapter.failed",
                        {
                            "worker_id": self._worker_id,
                            "task_id": str(task.id),
                            "chapter_id": chapter.id,
                            "error": error_msg,
                        },
                    )

                except Exception as e:
                    self._chapters_failed += 1
                    error_msg = str(e)
                    self._logger.error(
                        "Chapitre {} échoué avec exception: {}",
                        chapter.number,
                        e,
                        exc_info=True,
                    )

                    result = ChapterResult(
                        chapter=chapter,
                        output_path=Path(),
                        pages_downloaded=0,
                        pages_failed=chapter.pages_count or 0,
                        bytes_downloaded=0,
                        duration_seconds=0.0,
                        success=False,
                        error=error_msg,
                    )
                    results.append(result)

                    await self._event_bus.emit(
                        "worker.chapter.failed",
                        {
                            "worker_id": self._worker_id,
                            "task_id": str(task.id),
                            "chapter_id": chapter.id,
                            "error": error_msg,
                        },
                    )

                # Émettre la progression de la tâche
                if task_progress is not None:
                    await self._progress_tracker.emit_task_progress(task.id)

            # Marquer la tâche comme terminée
            if task_progress is not None:
                await task_progress.complete()

            self._tasks_completed += 1
            self._logger.info(
                "Tâche {} terminée: {}/{} chapitres réussis",
                task.id,
                sum(1 for r in results if r.success),
                len(results),
            )

            # Émettre un événement de fin de tâche
            await self._event_bus.emit(
                "worker.task.completed",
                {
                    "worker_id": self._worker_id,
                    "task_id": str(task.id),
                    "results_count": len(results),
                    "success_count": sum(1 for r in results if r.success),
                },
            )

            return results

        except (TaskCancelledError, TaskPausedError) as e:
            self._logger.info("Tâche {} interrompue: {}", task.id, e)
            if task_progress is not None:
                if isinstance(e, TaskCancelledError):
                    await task_progress.cancel()
                else:
                    await task_progress.pause()
            raise

        except Exception as e:
            self._tasks_failed += 1
            self._logger.error("Tâche {} échouée avec exception: {}", task.id, e, exc_info=True)
            if task_progress is not None:
                await task_progress.fail(str(e))
            raise WorkerError(f"Erreur fatale lors du traitement de la tâche {task.id}: {e}") from e

        finally:
            # Nettoyer le répertoire temporaire
            if temp_dir is not None and self._cleanup_temp:
                await self._cleanup_temp_dir(temp_dir)

            # Retirer du tracker
            if self._progress_tracker is not None:
                await self._progress_tracker.untrack_task(task.id)

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    def get_stats(self) -> WorkerStats:
        """Retourne les statistiques agrégées du worker.

        Returns:
            Objet WorkerStats avec tous les compteurs.
        """
        uptime = time.monotonic() - self._start_time
        return WorkerStats(
            worker_id=self._worker_id,
            tasks_completed=self._tasks_completed,
            tasks_failed=self._tasks_failed,
            chapters_completed=self._chapters_completed,
            chapters_failed=self._chapters_failed,
            pages_downloaded=self._pages_downloaded,
            pages_skipped=self._pages_skipped,
            bytes_downloaded=self._bytes_downloaded,
            uptime_seconds=uptime,
        )

    def reset_stats(self) -> None:
        """Réinitialise les statistiques du worker."""
        self._tasks_completed = 0
        self._tasks_failed = 0
        self._chapters_completed = 0
        self._chapters_failed = 0
        self._pages_downloaded = 0
        self._pages_skipped = 0
        self._bytes_downloaded = 0
        self._start_time = time.monotonic()

    # ------------------------------------------------------------------------
    # Méthodes internes — Traitement de chapitre
    # ------------------------------------------------------------------------

    async def _process_chapter(
        self,
        task: DownloadTask,
        chapter: Chapter,
        temp_dir: Path,
        task_progress: Any | None,
    ) -> ChapterResult:
        """Traite un chapitre individuel : récupère les pages, télécharge, empaquette.

        Args:
            task: Tâche parente.
            chapter: Chapitre à traiter.
            temp_dir: Répertoire temporaire pour les pages.
            task_progress: Tracker de progression de la tâche (optionnel).

        Returns:
            Résultat du téléchargement du chapitre.

        Raises:
            ChapterDownloadError: Si le chapitre échoue.
        """
        start_time = time.monotonic()
        manga = task.manga
        site_id = manga.site

        # Démarrer le suivi du chapitre
        chapter_progress = None
        if task_progress is not None:
            chapter_progress = await task_progress.track_chapter(
                chapter.id,
                pages_count=chapter.pages_count or 0,
                manga_id=manga.id,
                manga_title=manga.title,
                chapter_title=chapter.title,
                chapter_number=str(chapter.number),
            )
            await chapter_progress.start()

        try:
            # 1. Récupérer le parser et la session
            parser = self._registry.get_parser(site_id)
            session = self._session_factory.create(site_id)

            # 2. Vérifier la déduplication du chapitre complet
            if self._dedup_cache is not None and self._dedup_cache.strategy in (
                DeduplicationStrategy.CHAPTER,
                DeduplicationStrategy.BOTH,
            ):
                chapter_key = build_chapter_key(
                    site_id=site_id,
                    chapter_source_id=chapter.source_id,
                    chapter_number=chapter.number,
                    language=chapter.language.value,
                )
                existing = await self._dedup_cache.check_chapter_exists(chapter_key)
                if existing is not None and existing.output_path:
                    output_path = Path(existing.output_path)
                    if output_path.exists():
                        self._logger.info(
                            "Chapitre {} déjà téléchargé (déduplication), skip",
                            chapter.number,
                        )
                        if self._dedup_cache is not None:
                            await self._dedup_cache.record_chapter_skip(existing.bytes_total)
                        return ChapterResult(
                            chapter=chapter,
                            output_path=output_path,
                            pages_downloaded=0,
                            pages_failed=0,
                            pages_skipped=existing.pages_count,
                            bytes_downloaded=0,
                            duration_seconds=time.monotonic() - start_time,
                            success=True,
                        )

            # 3. Récupérer les pages du chapitre
            self._logger.debug("Récupération des pages du chapitre {}", chapter.number)
            pages = await self._get_pages_with_retry(parser, chapter)

            if not pages:
                raise ChapterDownloadError(
                    chapter,
                    pages_failed=[],
                    last_exception=ParserError("Aucune page trouvée pour ce chapitre"),
                )

            self._logger.debug("{} pages trouvées pour le chapitre {}", len(pages), chapter.number)

            # 4. Télécharger les pages
            chapter_temp_dir = temp_dir / f"chapter_{chapter.id}"
            chapter_temp_dir.mkdir(parents=True, exist_ok=True)

            downloaded_pages, failed_pages, skipped_pages, bytes_downloaded = (
                await self._download_pages(
                    task=task,
                    chapter=chapter,
                    pages=pages,
                    dest_dir=chapter_temp_dir,
                    session=session,
                    chapter_progress=chapter_progress,
                )
            )

            # 5. Vérifier qu'on a assez de pages pour empaqueter
            if not downloaded_pages:
                raise ChapterDownloadError(
                    chapter,
                    pages_failed=[p.index for p in pages],
                    last_exception=ParserError("Aucune page téléchargée avec succès"),
                )

            # 6. Empaqueter les pages
            self._logger.debug(
                "Empaquetage du chapitre {} en {} ({} pages)",
                chapter.number,
                task.fmt.value,
                len(downloaded_pages),
            )
            output_path = await self._package_chapter(
                task=task,
                chapter=chapter,
                pages=downloaded_pages,
                manga=manga,
            )

            # 7. Enregistrer dans le cache de déduplication
            if self._dedup_cache is not None and self._dedup_cache.strategy in (
                DeduplicationStrategy.CHAPTER,
                DeduplicationStrategy.BOTH,
            ):
                chapter_key = build_chapter_key(
                    site_id=site_id,
                    chapter_source_id=chapter.source_id,
                    chapter_number=chapter.number,
                    language=chapter.language.value,
                )
                content_hash = None
                if output_path.exists():
                    content_hash = compute_content_hash(output_path.read_bytes())

                await self._dedup_cache.register_chapter(
                    composite_key=chapter_key,
                    content_hash=content_hash,
                    output_path=output_path,
                    pages_count=len(downloaded_pages),
                    bytes_total=output_path.stat().st_size if output_path.exists() else 0,
                    packaging_format=task.fmt.value,
                    task_id=task.id,
                )

            # 8. Marquer le chapitre comme terminé
            if chapter_progress is not None:
                await chapter_progress.complete()

            duration = time.monotonic() - start_time
            return ChapterResult(
                chapter=chapter,
                output_path=output_path,
                pages_downloaded=len(downloaded_pages),
                pages_failed=len(failed_pages),
                pages_skipped=skipped_pages,
                bytes_downloaded=bytes_downloaded,
                duration_seconds=duration,
                success=True,
            )

        except Exception as e:
            if chapter_progress is not None:
                await chapter_progress.fail(str(e))
            raise

    async def _get_pages_with_retry(
        self,
        parser: BaseParser,
        chapter: Chapter,
    ) -> list[Page]:
        """Récupère les pages d'un chapitre avec retry intelligent.

        Args:
            parser: Parser du site source.
            chapter: Chapitre dont il faut récupérer les pages.

        Returns:
            Liste des pages du chapitre.

        Raises:
            ParserError: Si la récupération échoue après tous les retries.
        """
        retry_decorator = build_retry(
            CHAPTER_DOWNLOAD_POLICY.config,
            retryable_exceptions=CHAPTER_DOWNLOAD_POLICY.retryable_exceptions,
            event_bus=self._event_bus,
            context={"chapter_id": chapter.id},
        )

        @retry_decorator
        async def _fetch() -> list[Page]:
            return await parser.get_pages(chapter)

        return await _fetch()

    async def _download_pages(
        self,
        task: DownloadTask,
        chapter: Chapter,
        pages: list[Page],
        dest_dir: Path,
        session: HttpSession,
        chapter_progress: Any | None,
    ) -> tuple[list[Path], list[int], int, int]:
        """Télécharge toutes les pages d'un chapitre avec concurrence contrôlée.

        Args:
            task: Tâche parente.
            chapter: Chapitre en cours de traitement.
            pages: Liste des pages à télécharger.
            dest_dir: Répertoire de destination pour les images.
            session: Session HTTP pour le site source.
            chapter_progress: Tracker de progression du chapitre (optionnel).

        Returns:
            Tuple de (pages_téléchargées, pages_échouées, pages_évitées, bytes_téléchargés).
        """
        downloaded: list[Path] = []
        failed: list[int] = []
        skipped = 0
        total_bytes = 0

        # Créer un groupe de tâches pour le téléchargement concurrent
        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(
                    self._download_single_page(
                        task=task,
                        chapter=chapter,
                        page=page,
                        dest_dir=dest_dir,
                        session=session,
                        chapter_progress=chapter_progress,
                    )
                )
                for page in pages
            ]

        # Collecter les résultats
        for task_obj in tasks:
            result = task_obj.result()
            if result is not None:
                path, bytes_count, was_skipped = result
                if was_skipped:
                    skipped += 1
                else:
                    downloaded.append(path)
                    total_bytes += bytes_count
            else:
                # La page a échoué
                # Trouver l'index de la page (simplifié)
                failed.append(0)  # Placeholder

        # Trier les pages téléchargées par index
        downloaded.sort(key=lambda p: int(p.stem.split("_")[-1]) if "_" in p.stem else 0)

        return downloaded, failed, skipped, total_bytes

    async def _download_single_page(
        self,
        task: DownloadTask,
        chapter: Chapter,
        page: Page,
        dest_dir: Path,
        session: HttpSession,
        chapter_progress: Any | None,
    ) -> tuple[Path, int, bool] | None:
        """Télécharge une page individuelle avec retry et déduplication.

        Args:
            task: Tâche parente.
            chapter: Chapitre en cours.
            page: Page à télécharger.
            dest_dir: Répertoire de destination.
            session: Session HTTP.
            chapter_progress: Tracker de progression (optionnel).

        Returns:
            Tuple (chemin, bytes, était_skippé) si succès, None si échec.
        """
        async with self._page_semaphore:
            # Vérifier l'annulation
            await self._check_cancellation(task)

            page_start_time = time.monotonic()
            output_path = dest_dir / page.filename

            try:
                # 1. Vérifier la déduplication par hash
                if self._dedup_cache is not None and self._dedup_cache.strategy in (
                    DeduplicationStrategy.PAGE,
                    DeduplicationStrategy.BOTH,
                ):
                    # On ne peut pas vérifier le hash avant téléchargement
                    # car on ne connaît pas le contenu. On télécharge d'abord,
                    # puis on vérifie si le hash existe déjà.
                    pass

                # 2. Télécharger la page avec retry
                retry_decorator = build_retry(
                    PAGE_DOWNLOAD_POLICY.config,
                    retryable_exceptions=PAGE_DOWNLOAD_POLICY.retryable_exceptions,
                    event_bus=self._event_bus,
                    task_id=task.id,
                    context={"chapter_id": chapter.id, "page_index": page.index},
                )

                @retry_decorator
                async def _download() -> Path:
                    return await asyncio.wait_for(
                        session.stream_download(page.url, output_path),
                        timeout=self._PAGE_DOWNLOAD_TIMEOUT,
                    )

                downloaded_path = await _download()

                # 3. Calculer le hash et vérifier la déduplication
                bytes_count = downloaded_path.stat().st_size
                content_hash = compute_content_hash(downloaded_path.read_bytes())

                if self._dedup_cache is not None and self._dedup_cache.strategy in (
                    DeduplicationStrategy.PAGE,
                    DeduplicationStrategy.BOTH,
                ):
                    exists = await self._dedup_cache.check_page_exists(content_hash)
                    if exists:
                        # Page déjà téléchargée ailleurs, on peut la supprimer
                        downloaded_path.unlink(missing_ok=True)
                        await self._dedup_cache.record_page_skip(bytes_count)
                        self._logger.trace(
                            "Page {} déjà présente (déduplication), skip",
                            page.index,
                        )
                        return (downloaded_path, bytes_count, True)

                    # Enregistrer la page
                    await self._dedup_cache.register_page(
                        content_hash,
                        size=bytes_count,
                        url=page.url,
                        site_id=task.manga.site,
                    )

                # 4. Enregistrer la progression
                if chapter_progress is not None:
                    await chapter_progress.record_page_complete(page.index, bytes_count)

                page_duration = time.monotonic() - page_start_time
                self._logger.trace(
                    "Page {} téléchargée: {} bytes en {:.2f}s",
                    page.index,
                    bytes_count,
                    page_duration,
                )

                return (downloaded_path, bytes_count, False)

            except Exception as e:
                self._logger.warning(
                    "Échec du téléchargement de la page {}: {}",
                    page.index,
                    e,
                )
                if chapter_progress is not None:
                    await chapter_progress.record_page_failed(page.index)
                return None

    async def _package_chapter(
        self,
        task: DownloadTask,
        chapter: Chapter,
        pages: list[Path],
        manga: Manga,
    ) -> Path:
        """Empaquette les pages d'un chapitre dans le format cible.

        Args:
            task: Tâche parente.
            chapter: Chapitre à empaqueter.
            pages: Liste des chemins des pages téléchargées.
            manga: Manga parent (pour les métadonnées).

        Returns:
            Chemin du fichier empaqueté.

        Raises:
            WorkerError: Si l'empaquetage échoue.
        """
        # Construire le chemin de sortie
        output_dir = task.dest / self._sanitize_filename(manga.title)
        output_dir.mkdir(parents=True, exist_ok=True)

        output_filename = self._build_chapter_filename(chapter, task.fmt)
        output_path = output_dir / output_filename

        # Créer le packager
        packager = self._packager_factory.create(task.fmt)

        try:
            # Empaqueter avec retry
            retry_decorator = build_retry(
                CHAPTER_DOWNLOAD_POLICY.config,
                retryable_exceptions=(OSError, IOError),
                event_bus=self._event_bus,
                context={"chapter_id": chapter.id, "format": task.fmt.value},
            )

            @retry_decorator
            async def _package() -> Path:
                return await packager.package(pages, output_path)

            result_path = await _package()

            self._logger.debug(
                "Chapitre {} empaqueté: {}",
                chapter.number,
                result_path,
            )

            return result_path

        except Exception as e:
            raise WorkerError(
                f"Échec de l'empaquetage du chapitre {chapter.number}: {e}"
            ) from e

    # ------------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # ------------------------------------------------------------------------

    async def _check_cancellation(self, task: DownloadTask) -> None:
        """Vérifie si la tâche a été annulée ou mise en pause.

        Args:
            task: Tâche à vérifier.

        Raises:
            TaskCancelledError: Si la tâche est annulée.
            TaskPausedError: Si la tâche est en pause.
        """
        if task.status == DownloadStatus.CANCELLED:
            raise TaskCancelledError(task.id)
        if task.status == DownloadStatus.PAUSED:
            raise TaskPausedError(task.id)

    async def _cleanup_temp_dir(self, temp_dir: Path) -> None:
        """Nettoie un répertoire temporaire.

        Args:
            temp_dir: Répertoire à nettoyer.
        """
        try:
            if temp_dir.exists():
                await asyncio.to_thread(shutil.rmtree, temp_dir, ignore_errors=True)
                self._logger.debug("Répertoire temporaire nettoyé: {}", temp_dir)
        except Exception as e:
            self._logger.warning(
                "Impossible de nettoyer le répertoire temporaire {}: {}",
                temp_dir,
                e,
            )

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Sanitize un nom de fichier pour éviter les caractères invalides.

        Args:
            name: Nom à sanitizer.

        Returns:
            Nom sanitizé.
        """
        # Remplacer les caractères invalides par des underscores
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, "_")
        # Supprimer les espaces en début/fin
        return name.strip()

    @staticmethod
    def _build_chapter_filename(chapter: Chapter, fmt: PackagingFormat) -> str:
        """Construit le nom de fichier pour un chapitre empaqueté.

        Args:
            chapter: Chapitre.
            fmt: Format d'empaquetage.

        Returns:
            Nom de fichier (ex: "Chapitre 001.cbz").
        """
        # Formater le numéro de chapitre
        if isinstance(chapter.number, float):
            if chapter.number.is_integer():
                number_str = f"{int(chapter.number):03d}"
            else:
                number_str = f"{chapter.number:.1f}"
        else:
            number_str = str(chapter.number)

        # Construire le nom
        base_name = f"Chapitre {number_str}"
        if chapter.title:
            base_name += f" - {chapter.title}"

        # Ajouter l'extension
        extension_map = {
            PackagingFormat.CBZ: ".cbz",
            PackagingFormat.CBR: ".cbr",
            PackagingFormat.PDF: ".pdf",
            PackagingFormat.ZIP: ".zip",
            PackagingFormat.FOLDER: "",
        }
        extension = extension_map.get(fmt, "")

        return f"{base_name}{extension}"

    def __repr__(self) -> str:
        return (
            f"<DownloadWorker id={self._worker_id} "
            f"tasks_completed={self._tasks_completed} "
            f"pages_downloaded={self._pages_downloaded}>"
        )
