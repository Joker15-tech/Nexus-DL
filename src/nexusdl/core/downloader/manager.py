"""Orchestrateur global du système de téléchargement.

Ce module constitue le point d'entrée unique pour tous les téléchargements
dans NexusDL. Il gère le cycle de vie complet des tâches : soumission,
mise en file d'attente, exécution par des workers concurrents, pause,
reprise, annulation, et reporting de progression via l'EventBus.

Architecture :
    DownloadManager (ce fichier)
        ├── DownloadQueue (file prioritaire async)
        ├── DownloadWorker[] (workers individuels)
        ├── DeduplicationCache (éviter les re-téléchargements)
        ├── SiteRegistry (accès aux parsers)
        ├── SessionFactory (création de sessions HTTP)
        ├── PackagerFactory (création de packagers)
        └── EventBus (notification des interfaces)

Règles d'or :
    1. Le manager NE fait PAS le téléchargement lui-même — il délègue aux workers.
    2. Il NE dépend PAS de `interfaces/` ni de `parsers/` (architecture hexagonale).
    3. Tout est async-first (asyncio.TaskGroup, semaphores).
    4. La déduplication est OPTIONNELLE (configurable via strategy).
    5. Les événements sont émis sur l'EventBus à chaque changement d'état.
    6. Le shutdown est GRACEFUL : les workers en cours terminent proprement.

Exemple d'utilisation :
    >>> manager = DownloadManager(
    ...     registry=registry,
    ...     session_factory=session_factory,
    ...     packager_factory=packager_factory,
    ...     event_bus=event_bus,
    ... )
    >>> await manager.start()
    >>>
    >>> # Soumettre une tâche
    >>> task = DownloadTask(manga=manga, chapters=[ch1, ch2], dest=Path("/dl"))
    >>> task_id = manager.submit(task)
    >>>
    >>> # Attendre la fin
    >>> result = await manager.wait_for_task(task_id)
    >>> print(result.output_path)
    /dl/One Piece/Chapitre 001.cbz
    >>>
    >>> await manager.stop()
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Final, Protocol, Self
from uuid import UUID, uuid4

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.downloader.deduplication import (
    DeduplicationCache,
    DeduplicationStrategy,
)
from nexusdl.core.events import EventBus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.models.download import (
    DownloadResult,
    DownloadStatus,
    DownloadTask,
    Priority,
)
from nexusdl.core.models.manga import Chapter, Manga
from nexusdl.core.packaging.base import PackagingFormat

if TYPE_CHECKING:
    from nexusdl.core.downloader.queue import DownloadQueue
    from nexusdl.core.downloader.worker import DownloadWorker
    from nexusdl.core.packaging.base import BasePackager
    from nexusdl.core.registry.site_registry import SiteRegistry
    from nexusdl.core.session.http_session import HttpSession


# ============================================================================
# EXCEPTIONS
# ============================================================================


class DownloadManagerError(NexusDLError):
    """Exception de base pour les erreurs du DownloadManager."""


class TaskNotFoundError(DownloadManagerError):
    """Exception levée lorsqu'une tâche demandée n'existe pas."""

    def __init__(self, task_id: UUID) -> None:
        super().__init__(f"Tâche introuvable: {task_id}")
        self.task_id = task_id


class TaskAlreadyExistsError(DownloadManagerError):
    """Exception levée lorsqu'on soumet une tâche avec un ID déjà utilisé."""

    def __init__(self, task_id: UUID) -> None:
        super().__init__(f"Tâche déjà existante: {task_id}")
        self.task_id = task_id


class ManagerNotStartedError(DownloadManagerError):
    """Exception levée lorsqu'on utilise le manager avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "DownloadManager must be started before use. Call await manager.start()"
        )


class ManagerAlreadyStartedError(DownloadManagerError):
    """Exception levée lorsqu'on appelle start() sur un manager déjà démarré."""

    def __init__(self) -> None:
        super().__init__("DownloadManager is already started")


# ============================================================================
# PROTOCOLS (Dependency Injection)
# ============================================================================


class SessionFactory(Protocol):
    """Protocol pour la factory de sessions HTTP (injection de dépendance)."""

    def create(self, site_id: str) -> HttpSession:
        """Crée une session HTTP pour un site donné."""
        ...


class PackagerFactory(Protocol):
    """Protocol pour la factory de packagers (injection de dépendance)."""

    def create(self, fmt: PackagingFormat) -> BasePackager:
        """Crée un packager pour un format donné."""
        ...


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class ManagerState(str, Enum):
    """État interne du DownloadManager."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"


class ManagerStats(BaseModel):
    """Statistiques globales du DownloadManager."""

    state: ManagerState = Field(description="État actuel du manager.")
    total_tasks_submitted: int = Field(default=0, ge=0)
    total_tasks_completed: int = Field(default=0, ge=0)
    total_tasks_failed: int = Field(default=0, ge=0)
    total_tasks_cancelled: int = Field(default=0, ge=0)
    active_tasks: int = Field(default=0, ge=0)
    queued_tasks: int = Field(default=0, ge=0)
    paused_tasks: int = Field(default=0, ge=0)
    uptime_seconds: float = Field(default=0.0, ge=0.0)
    bytes_downloaded_total: int = Field(default=0, ge=0)
    pages_downloaded_total: int = Field(default=0, ge=0)
    average_speed_bytes_per_sec: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")


class TaskState(BaseModel):
    """État interne d'une tâche gérée par le manager."""

    task: DownloadTask
    worker_task: asyncio.Task[None] | None = Field(default=None, exclude=True)
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    bytes_downloaded: int = 0
    pages_downloaded: int = 0

    model_config = ConfigDict(arbitrary_types_allowed=True)


# ============================================================================
# CLASSE PRINCIPALE
# ============================================================================


class DownloadManager:
    """Orchestrateur global du système de téléchargement.

    Gère le cycle de vie complet des tâches de téléchargement : soumission,
    mise en file d'attente, exécution par des workers concurrents, pause,
    reprise, annulation, et reporting de progression via l'EventBus.

    Lifecycle :
        >>> manager = DownloadManager(...)
        >>> await manager.start()   # Démarre les workers et la queue
        >>> # ... soumission de tâches ...
        >>> await manager.stop()    # Arrêt gracieux
    """

    # Limites par défaut
    _DEFAULT_MAX_CONCURRENT_TASKS: Final[int] = 3
    _DEFAULT_MAX_CONCURRENT_PAGES: Final[int] = 8
    _DEFAULT_TASK_TIMEOUT_SECONDS: Final[float] = 3600.0  # 1 heure
    _DEFAULT_STATS_CACHE_TTL_SECONDS: Final[float] = 1.0

    def __init__(
        self,
        registry: SiteRegistry,
        session_factory: SessionFactory,
        packager_factory: PackagerFactory,
        event_bus: EventBus,
        *,
        max_concurrent_tasks: int = _DEFAULT_MAX_CONCURRENT_TASKS,
        max_concurrent_pages: int = _DEFAULT_MAX_CONCURRENT_PAGES,
        dedup_db_path: Path | None = None,
        dedup_strategy: DeduplicationStrategy = DeduplicationStrategy.BOTH,
        task_timeout_seconds: float = _DEFAULT_TASK_TIMEOUT_SECONDS,
    ) -> None:
        """Initialise le DownloadManager.

        Args:
            registry: Registre des sites supportés.
            session_factory: Factory pour créer des sessions HTTP par site.
            packager_factory: Factory pour créer des packagers par format.
            event_bus: Bus d'événements pour notifier les interfaces.
            max_concurrent_tasks: Nombre max de tâches exécutées simultanément.
            max_concurrent_pages: Nombre max de pages téléchargées simultanément
                                  par tâche.
            dedup_db_path: Chemin vers la BDD SQLite de déduplication.
                           Si None, la déduplication est désactivée.
            dedup_strategy: Stratégie de déduplication (NONE, PAGE, CHAPTER, BOTH).
            task_timeout_seconds: Timeout global d'une tâche (secondes).
        """
        if max_concurrent_tasks <= 0:
            raise ValueError("max_concurrent_tasks must be positive")
        if max_concurrent_pages <= 0:
            raise ValueError("max_concurrent_pages must be positive")

        self._registry = registry
        self._session_factory = session_factory
        self._packager_factory = packager_factory
        self._event_bus = event_bus
        self._max_concurrent_tasks = max_concurrent_tasks
        self._max_concurrent_pages = max_concurrent_pages
        self._task_timeout_seconds = task_timeout_seconds

        # État interne
        self._state: ManagerState = ManagerState.STOPPED
        self._tasks: OrderedDict[UUID, TaskState] = OrderedDict()
        self._tasks_lock = asyncio.Lock()

        # Sémaphores pour la concurrence
        self._task_semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrent_tasks)
        self._page_semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrent_pages)

        # Queue et workers (initialisés dans start())
        self._queue: DownloadQueue | None = None
        self._workers: list[DownloadWorker] = []
        self._orchestrator_task: asyncio.Task[None] | None = None

        # Cache de déduplication (optionnel)
        self._dedup_cache: DeduplicationCache | None = None
        if dedup_db_path is not None:
            self._dedup_cache = DeduplicationCache(
                db_path=dedup_db_path,
                strategy=dedup_strategy,
            )

        # Statistiques
        self._start_time: float | None = None
        self._total_bytes_downloaded: int = 0
        self._total_pages_downloaded: int = 0
        self._stats_lock = asyncio.Lock()

        # Logger avec contexte
        self._logger = logger.bind(module="download_manager")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre le DownloadManager.

        Initialise la queue, les workers, le cache de déduplication, et
        lance la tâche d'orchestration qui traite les tâches en attente.

        Raises:
            ManagerAlreadyStartedError: Si le manager est déjà démarré.
            DownloadManagerError: Si l'initialisation échoue.
        """
        if self._state != ManagerState.STOPPED:
            raise ManagerAlreadyStartedError()

        self._state = ManagerState.STARTING
        self._logger.info("Démarrage du DownloadManager...")

        try:
            # Import dynamique pour éviter les imports circulaires
            from nexusdl.core.downloader.queue import DownloadQueue
            from nexusdl.core.downloader.worker import DownloadWorker

            # Initialiser la queue
            self._queue = DownloadQueue()
            await self._queue.start()

            # Initialiser le cache de déduplication si configuré
            if self._dedup_cache is not None:
                await self._dedup_cache.start()

            # Créer les workers
            for i in range(self._max_concurrent_tasks):
                worker = DownloadWorker(
                    worker_id=i,
                    registry=self._registry,
                    session_factory=self._session_factory,
                    packager_factory=self._packager_factory,
                    event_bus=self._event_bus,
                    page_semaphore=self._page_semaphore,
                    dedup_cache=self._dedup_cache,
                )
                self._workers.append(worker)

            # Lancer la tâche d'orchestration
            self._orchestrator_task = asyncio.create_task(
                self._orchestrate(),
                name="download_manager_orchestrator",
            )

            self._state = ManagerState.RUNNING
            self._start_time = time.monotonic()

            self._logger.info(
                "DownloadManager démarré: max_tasks={}, max_pages={}, dedup={}",
                self._max_concurrent_tasks,
                self._max_concurrent_pages,
                self._dedup_cache is not None,
            )

            # Émettre un événement de démarrage
            await self._event_bus.emit("manager.started", {"state": self._state.value})

        except Exception as e:
            self._state = ManagerState.STOPPED
            self._logger.error("Échec du démarrage du DownloadManager: {}", e)
            raise DownloadManagerError(f"Impossible de démarrer le manager: {e}") from e

    async def stop(self, *, timeout: float = 30.0) -> None:
        """Arrête gracieusement le DownloadManager.

        Attend que les tâches en cours se terminent (ou timeout), puis
        arrête les workers, la queue et le cache de déduplication.

        Args:
            timeout: Temps maximum d'attente pour les tâches en cours (secondes).

        Raises:
            ManagerNotStartedError: Si le manager n'est pas démarré.
        """
        if self._state == ManagerState.STOPPED:
            self._logger.warning("DownloadManager déjà arrêté, ignore")
            return

        self._state = ManagerState.STOPPING
        self._logger.info("Arrêt du DownloadManager...")

        try:
            # Émettre un événement d'arrêt
            await self._event_bus.emit("manager.stopping", {"timeout": timeout})

            # Annuler les tâches en attente dans la queue
            if self._queue is not None:
                await self._queue.cancel_all()

            # Attendre que les tâches en cours se terminent
            if self._orchestrator_task is not None:
                try:
                    await asyncio.wait_for(self._orchestrator_task, timeout=timeout)
                except asyncio.TimeoutError:
                    self._logger.warning(
                        "Timeout lors de l'arrêt, annulation forcée des tâches en cours"
                    )
                    self._orchestrator_task.cancel()
                    try:
                        await self._orchestrator_task
                    except asyncio.CancelledError:
                        pass

            # Arrêter les workers
            for worker in self._workers:
                await worker.stop()

            # Arrêter la queue
            if self._queue is not None:
                await self._queue.stop()

            # Arrêter le cache de déduplication
            if self._dedup_cache is not None:
                await self._dedup_cache.stop()

            self._state = ManagerState.STOPPED
            self._start_time = None

            self._logger.info("DownloadManager arrêté")

            # Émettre un événement d'arrêt terminé
            await self._event_bus.emit("manager.stopped", {})

        except Exception as e:
            self._logger.error("Erreur lors de l'arrêt du DownloadManager: {}", e)
            raise DownloadManagerError(f"Erreur lors de l'arrêt: {e}") from e

    async def __aenter__(self) -> Self:
        """Support du contexte async (with statement)."""
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        """Support du contexte async (with statement)."""
        await self.stop()

    # ------------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------------

    @property
    def state(self) -> ManagerState:
        """État actuel du manager."""
        return self._state

    @property
    def is_running(self) -> bool:
        """Indique si le manager est en cours d'exécution."""
        return self._state == ManagerState.RUNNING

    @property
    def max_concurrent_tasks(self) -> int:
        """Nombre maximum de tâches concurrentes."""
        return self._max_concurrent_tasks

    @property
    def max_concurrent_pages(self) -> int:
        """Nombre maximum de pages concurrentes par tâche."""
        return self._max_concurrent_pages

    # ------------------------------------------------------------------------
    # API publique — Soumission et contrôle
    # ------------------------------------------------------------------------

    def submit(self, task: DownloadTask) -> UUID:
        """Soumet une tâche de téléchargement à la queue.

        Args:
            task: Tâche à soumettre (doit avoir un ID unique).

        Returns:
            UUID de la tâche soumise.

        Raises:
            ManagerNotStartedError: Si le manager n'est pas démarré.
            TaskAlreadyExistsError: Si une tâche avec cet ID existe déjà.
        """
        self._ensure_running()

        if task.id in self._tasks:
            raise TaskAlreadyExistsError(task.id)

        # Ajouter à la liste interne
        task_state = TaskState(task=task)
        self._tasks[task.id] = task_state

        # Ajouter à la queue
        assert self._queue is not None
        self._queue.put(task)

        self._logger.info(
            "Tâche soumise: id={}, manga={}, chapters={}",
            task.id,
            task.manga.title,
            len(task.chapters),
        )

        # Émettre un événement
        asyncio.create_task(
            self._event_bus.emit(
                "task.submitted",
                {"task_id": str(task.id), "manga_title": task.manga.title},
            )
        )

        return task.id

    def cancel(self, task_id: UUID) -> None:
        """Annule une tâche en cours ou en attente.

        Args:
            task_id: UUID de la tâche à annuler.

        Raises:
            ManagerNotStartedError: Si le manager n'est pas démarré.
            TaskNotFoundError: Si la tâche n'existe pas.
        """
        self._ensure_running()

        if task_id not in self._tasks:
            raise TaskNotFoundError(task_id)

        task_state = self._tasks[task_id]
        task_state.task.status = DownloadStatus.CANCELLED

        # Annuler le worker task si en cours
        if task_state.worker_task is not None:
            task_state.worker_task.cancel()

        self._logger.info("Tâche annulée: id={}", task_id)

        # Émettre un événement
        asyncio.create_task(
            self._event_bus.emit("task.cancelled", {"task_id": str(task_id)})
        )

    def pause(self, task_id: UUID) -> None:
        """Met en pause une tâche en cours.

        Args:
            task_id: UUID de la tâche à mettre en pause.

        Raises:
            ManagerNotStartedError: Si le manager n'est pas démarré.
            TaskNotFoundError: Si la tâche n'existe pas.
        """
        self._ensure_running()

        if task_id not in self._tasks:
            raise TaskNotFoundError(task_id)

        task_state = self._tasks[task_id]
        if task_state.task.status == DownloadStatus.RUNNING:
            task_state.task.status = DownloadStatus.PAUSED
            self._logger.info("Tâche mise en pause: id={}", task_id)

            # Émettre un événement
            asyncio.create_task(
                self._event_bus.emit("task.paused", {"task_id": str(task_id)})
            )

    def resume(self, task_id: UUID) -> None:
        """Reprend une tâche en pause.

        Args:
            task_id: UUID de la tâche à reprendre.

        Raises:
            ManagerNotStartedError: Si le manager n'est pas démarré.
            TaskNotFoundError: Si la tâche n'existe pas.
        """
        self._ensure_running()

        if task_id not in self._tasks:
            raise TaskNotFoundError(task_id)

        task_state = self._tasks[task_id]
        if task_state.task.status == DownloadStatus.PAUSED:
            task_state.task.status = DownloadStatus.PENDING
            self._logger.info("Tâche reprise: id={}", task_id)

            # Réajouter à la queue
            assert self._queue is not None
            self._queue.put(task_state.task)

            # Émettre un événement
            asyncio.create_task(
                self._event_bus.emit("task.resumed", {"task_id": str(task_id)})
            )

    # ------------------------------------------------------------------------
    # API publique — Query
    # ------------------------------------------------------------------------

    def get_task(self, task_id: UUID) -> DownloadTask | None:
        """Récupère une tâche par son ID.

        Args:
            task_id: UUID de la tâche.

        Returns:
            La tâche si trouvée, None sinon.
        """
        task_state = self._tasks.get(task_id)
        return task_state.task if task_state is not None else None

    def list_tasks(
        self,
        *,
        status: DownloadStatus | None = None,
        limit: int | None = None,
    ) -> list[DownloadTask]:
        """Liste les tâches avec filtrage optionnel.

        Args:
            status: Filtrer par statut (None = tous).
            limit: Nombre maximum de tâches à retourner (None = toutes).

        Returns:
            Liste des tâches correspondantes, triées par date de soumission.
        """
        tasks = list(self._tasks.values())

        # Filtrer par statut
        if status is not None:
            tasks = [ts for ts in tasks if ts.task.status == status]

        # Trier par date de soumission (plus récent en premier)
        tasks.sort(key=lambda ts: ts.submitted_at, reverse=True)

        # Limiter
        if limit is not None:
            tasks = tasks[:limit]

        return [ts.task for ts in tasks]

    async def wait_for_task(self, task_id: UUID, *, timeout: float | None = None) -> DownloadResult:
        """Attend qu'une tâche se termine et retourne son résultat.

        Args:
            task_id: UUID de la tâche.
            timeout: Timeout en secondes (None = infini).

        Returns:
            Résultat du téléchargement.

        Raises:
            TaskNotFoundError: Si la tâche n'existe pas.
            asyncio.TimeoutError: Si le timeout est dépassé.
            DownloadManagerError: Si la tâche échoue.
        """
        if task_id not in self._tasks:
            raise TaskNotFoundError(task_id)

        task_state = self._tasks[task_id]

        # Attendre que le worker task se termine
        if task_state.worker_task is not None:
            try:
                await asyncio.wait_for(task_state.worker_task, timeout=timeout)
            except asyncio.TimeoutError:
                raise

        # Vérifier le statut final
        if task_state.task.status == DownloadStatus.FAILED:
            raise DownloadManagerError(
                f"Tâche échouée: {task_id} — {task_state.task.error}"
            )

        # Construire le résultat (simplifié — à améliorer avec un vrai système de résultats)
        return DownloadResult(
            task_id=task_id,
            chapter=task_state.task.chapters[0] if task_state.task.chapters else None,
            output_path=task_state.task.dest / "output.cbz",  # Placeholder
            bytes_downloaded=task_state.bytes_downloaded,
            duration_seconds=(
                (task_state.completed_at - task_state.started_at).total_seconds()
                if task_state.started_at and task_state.completed_at
                else 0.0
            ),
            pages_failed=[],
            success=task_state.task.status == DownloadStatus.COMPLETED,
        )

    # ------------------------------------------------------------------------
    # API publique — Téléchargement direct
    # ------------------------------------------------------------------------

    async def download_manga(
        self,
        manga: Manga,
        *,
        chapters: list[Chapter] | None = None,
        dest: Path,
        fmt: PackagingFormat = PackagingFormat.CBZ,
        priority: Priority = Priority.NORMAL,
        on_progress: Callable[[float], None] | None = None,
    ) -> AsyncIterator[DownloadResult]:
        """Télécharge un manga complet ou une sélection de chapitres.

        Méthode de haut niveau qui crée automatiquement une DownloadTask,
        la soumet, et yield les résultats au fur et à mesure.

        Args:
            manga: Manga à télécharger.
            chapters: Liste de chapitres spécifiques (None = tous).
            dest: Dossier de destination.
            fmt: Format d'empaquetage.
            priority: Priorité de la tâche.
            on_progress: Callback de progression (0.0 à 1.0).

        Yields:
            Résultats de téléchargement pour chaque chapitre.

        Raises:
            ManagerNotStartedError: Si le manager n'est pas démarré.
            DownloadManagerError: Si le téléchargement échoue.
        """
        self._ensure_running()

        # Sélectionner les chapitres
        chapters_to_download = chapters if chapters is not None else manga.chapters

        if not chapters_to_download:
            self._logger.warning("Aucun chapitre à télécharger pour {}", manga.title)
            return

        # Créer la tâche
        task = DownloadTask(
            id=uuid4(),
            manga=manga,
            chapters=chapters_to_download,
            dest=dest,
            fmt=fmt,
            priority=priority,
        )

        # Soumettre
        task_id = self.submit(task)

        # Attendre et yield les résultats
        # (Simplifié — dans une vraie implémentation, on utiliserait un système
        # de résultats par chapitre via l'EventBus ou une queue de résultats)
        result = await self.wait_for_task(task_id)
        yield result

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> ManagerStats:
        """Retourne les statistiques globales du manager.

        Returns:
            Objet ManagerStats avec tous les compteurs à jour.
        """
        uptime = 0.0
        if self._start_time is not None:
            uptime = time.monotonic() - self._start_time

        async with self._stats_lock:
            total_bytes = self._total_bytes_downloaded
            total_pages = self._total_pages_downloaded

        avg_speed = total_bytes / uptime if uptime > 0 else 0.0

        # Compter les tâches par statut
        async with self._tasks_lock:
            active = sum(1 for ts in self._tasks.values() if ts.task.status == DownloadStatus.RUNNING)
            queued = sum(1 for ts in self._tasks.values() if ts.task.status == DownloadStatus.PENDING)
            paused = sum(1 for ts in self._tasks.values() if ts.task.status == DownloadStatus.PAUSED)
            completed = sum(1 for ts in self._tasks.values() if ts.task.status == DownloadStatus.COMPLETED)
            failed = sum(1 for ts in self._tasks.values() if ts.task.status == DownloadStatus.FAILED)
            cancelled = sum(1 for ts in self._tasks.values() if ts.task.status == DownloadStatus.CANCELLED)

        return ManagerStats(
            state=self._state,
            total_tasks_submitted=len(self._tasks),
            total_tasks_completed=completed,
            total_tasks_failed=failed,
            total_tasks_cancelled=cancelled,
            active_tasks=active,
            queued_tasks=queued,
            paused_tasks=paused,
            uptime_seconds=uptime,
            bytes_downloaded_total=total_bytes,
            pages_downloaded_total=total_pages,
            average_speed_bytes_per_sec=avg_speed,
        )

    # ------------------------------------------------------------------------
    # Méthodes internes — Orchestration
    # ------------------------------------------------------------------------

    async def _orchestrate(self) -> None:
        """Boucle principale d'orchestration des tâches.

        Récupère les tâches de la queue et les assigne aux workers disponibles.
        Tourne en continu jusqu'à l'arrêt du manager.
        """
        assert self._queue is not None
        self._logger.debug("Orchestrateur démarré")

        try:
            while self._state == ManagerState.RUNNING:
                # Récupérer la prochaine tâche de la queue
                try:
                    task = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                # Vérifier si la tâche n'a pas été annulée
                if task.status == DownloadStatus.CANCELLED:
                    self._logger.debug("Tâche annulée avant exécution: {}", task.id)
                    continue

                # Acquérir le sémaphore de tâches
                await self._task_semaphore.acquire()

                # Trouver un worker disponible
                worker = self._get_available_worker()
                if worker is None:
                    self._logger.warning("Aucun worker disponible, réattente")
                    self._task_semaphore.release()
                    await asyncio.sleep(0.1)
                    continue

                # Lancer le worker sur cette tâche
                worker_task = asyncio.create_task(
                    self._run_worker(worker, task),
                    name=f"worker_{worker.worker_id}_task_{task.id}",
                )

                # Enregistrer le worker task
                async with self._tasks_lock:
                    if task.id in self._tasks:
                        self._tasks[task.id].worker_task = worker_task
                        self._tasks[task.id].started_at = datetime.now(UTC)
                        self._tasks[task.id].task.status = DownloadStatus.RUNNING

                # Émettre un événement
                await self._event_bus.emit(
                    "task.started",
                    {"task_id": str(task.id), "worker_id": worker.worker_id},
                )

        except asyncio.CancelledError:
            self._logger.debug("Orchestrateur annulé")
        except Exception as e:
            self._logger.error("Erreur fatale dans l'orchestrateur: {}", e)
            raise

    async def _run_worker(self, worker: DownloadWorker, task: DownloadTask) -> None:
        """Exécute une tâche sur un worker et gère le cycle de vie.

        Args:
            worker: Worker à utiliser.
            task: Tâche à exécuter.
        """
        try:
            # Exécuter la tâche avec timeout
            await asyncio.wait_for(
                worker.run(task),
                timeout=self._task_timeout_seconds,
            )

            # Marquer comme terminée
            async with self._tasks_lock:
                if task.id in self._tasks:
                    self._tasks[task.id].task.status = DownloadStatus.COMPLETED
                    self._tasks[task.id].completed_at = datetime.now(UTC)

            self._logger.info("Tâche terminée avec succès: {}", task.id)

            # Émettre un événement
            await self._event_bus.emit("task.completed", {"task_id": str(task.id)})

        except asyncio.TimeoutError:
            self._logger.error("Tâche timeout: {}", task.id)
            async with self._tasks_lock:
                if task.id in self._tasks:
                    self._tasks[task.id].task.status = DownloadStatus.FAILED
                    self._tasks[task.id].task.error = "Timeout dépassé"

            await self._event_bus.emit(
                "task.failed",
                {"task_id": str(task.id), "error": "Timeout"},
            )

        except asyncio.CancelledError:
            self._logger.info("Tâche annulée: {}", task.id)
            async with self._tasks_lock:
                if task.id in self._tasks:
                    self._tasks[task.id].task.status = DownloadStatus.CANCELLED

            await self._event_bus.emit("task.cancelled", {"task_id": str(task.id)})

        except Exception as e:
            self._logger.error("Tâche échouée: {} — {}", task.id, e)
            async with self._tasks_lock:
                if task.id in self._tasks:
                    self._tasks[task.id].task.status = DownloadStatus.FAILED
                    self._tasks[task.id].task.error = str(e)

            await self._event_bus.emit(
                "task.failed",
                {"task_id": str(task.id), "error": str(e)},
            )

        finally:
            # Libérer le sémaphore
            self._task_semaphore.release()

            # Nettoyer le worker task
            async with self._tasks_lock:
                if task.id in self._tasks:
                    self._tasks[task.id].worker_task = None

    def _get_available_worker(self) -> DownloadWorker | None:
        """Trouve un worker disponible (non occupé).

        Returns:
            Un worker disponible, ou None si tous sont occupés.
        """
        # Simplifié — dans une vraie implémentation, on trackerait l'état de chaque worker
        return self._workers[0] if self._workers else None

    # ------------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # ------------------------------------------------------------------------

    def _ensure_running(self) -> None:
        """Vérifie que le manager est en cours d'exécution.

        Raises:
            ManagerNotStartedError: Si le manager n'est pas démarré.
        """
        if self._state != ManagerState.RUNNING:
            raise ManagerNotStartedError()

    def __repr__(self) -> str:
        return (
            f"<DownloadManager state={self._state.value} "
            f"tasks={len(self._tasks)} "
            f"max_concurrent={self._max_concurrent_tasks}>"
        )
