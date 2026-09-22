"""Système de suivi de progression temps réel pour les téléchargements.

Ce module calcule et émet la progression des téléchargements à quatre niveaux
d'agrégation hiérarchique :

    Page → Chapter → Task → Global

Chaque niveau expose :
    - Pourcentage (0.0 à 1.0)
    - Vitesse instantanée et moyenne (bytes/sec)
    - ETA (temps restant estimé)
    - Compteurs (bytes, pages, chapitres)
    - État (running, paused, completed, failed)

Les calculs utilisent un **lissage exponentiel (EMA)** pour la vitesse,
évitant les pics parasites lors de l'affichage. Une **fenêtre glissante**
des 30 dernières secondes est conservée pour un ETA précis.

Les événements sont émis sur l'EventBus avec un throttling configurable
(par défaut 250ms) pour éviter de saturer les interfaces.

Exemple d'utilisation :
    >>> tracker = ProgressTracker(event_bus=event_bus)
    >>> await tracker.start()
    >>>
    >>> # Démarrer le suivi d'une tâche
    >>> task_progress = await tracker.track_task(task_id)
    >>>
    >>> # Démarrer un chapitre
    >>> chapter_progress = await task_progress.track_chapter(chapter_id, pages_count=20)
    >>>
    >>> # Enregistrer une page téléchargée
    >>> await chapter_progress.record_page(
    ...     page_index=0,
    ...     bytes_downloaded=102400,
    ...     duration_seconds=0.5,
    ... )
    >>>
    >>> # Obtenir un snapshot immuable
    >>> snapshot = await task_progress.snapshot()
    >>> print(f"{snapshot.percentage:.1%} — {snapshot.speed_human}/s — ETA {snapshot.eta_human}")
    45.0% — 2.3 MB/s — ETA 12s
    >>>
    >>> await tracker.stop()
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, Final, Protocol, Self
from uuid import UUID

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.events import EventBus
from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# EXCEPTIONS
# ============================================================================


class ProgressError(NexusDLError):
    """Exception de base pour les erreurs du système de progression."""


class TrackerNotStartedError(ProgressError):
    """Exception levée lorsqu'on utilise le tracker avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "ProgressTracker must be started before use. Call await tracker.start()"
        )


class TaskNotTrackedError(ProgressError):
    """Exception levée lorsqu'une tâche demandée n'est pas suivie."""

    def __init__(self, task_id: UUID) -> None:
        super().__init__(f"Tâche non suivie: {task_id}")
        self.task_id = task_id


class ChapterNotTrackedError(ProgressError):
    """Exception levée lorsqu'un chapitre demandé n'est pas suivi."""

    def __init__(self, chapter_id: str) -> None:
        super().__init__(f"Chapitre non suivi: {chapter_id}")
        self.chapter_id = chapter_id


# ============================================================================
# ENUMS
# ============================================================================


class ProgressState(str, Enum):
    """État d'un élément suivi (page, chapitre, tâche, global)."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProgressUnit(str, Enum):
    """Unité de mesure pour l'affichage humain."""

    BYTES = "bytes"
    PAGES = "pages"
    CHAPTERS = "chapters"


# ============================================================================
# MODÈLES PYDANTIC (Snapshots immuables)
# ============================================================================


class PageProgressSnapshot(BaseModel):
    """Snapshot immuable de la progression d'une page individuelle."""

    page_index: int = Field(..., ge=0, description="Index de la page (0-based).")
    bytes_total: int = Field(default=0, ge=0, description="Taille totale de la page en bytes.")
    bytes_downloaded: int = Field(default=0, ge=0, description="Bytes déjà téléchargés.")
    percentage: float = Field(default=0.0, ge=0.0, le=1.0, description="Progression (0.0 à 1.0).")
    state: ProgressState = Field(default=ProgressState.PENDING)
    duration_seconds: float = Field(default=0.0, ge=0.0, description="Durée de téléchargement.")
    speed_bytes_per_sec: float = Field(default=0.0, ge=0.0, description="Vitesse instantanée.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ChapterProgressSnapshot(BaseModel):
    """Snapshot immuable de la progression d'un chapitre."""

    chapter_id: str = Field(..., description="Identifiant unique du chapitre.")
    manga_id: str = Field(default="", description="Identifiant du manga parent.")
    manga_title: str = Field(default="", description="Titre du manga (pour affichage).")
    chapter_title: str = Field(default="", description="Titre du chapitre (pour affichage).")
    chapter_number: str = Field(default="", description="Numéro du chapitre (ex: '12.5').")
    pages_total: int = Field(default=0, ge=0, description="Nombre total de pages.")
    pages_completed: int = Field(default=0, ge=0, description="Pages téléchargées avec succès.")
    pages_failed: int = Field(default=0, ge=0, description="Pages ayant échoué.")
    bytes_total: int = Field(default=0, ge=0, description="Taille totale estimée.")
    bytes_downloaded: int = Field(default=0, ge=0, description="Bytes téléchargés.")
    percentage: float = Field(default=0.0, ge=0.0, le=1.0)
    state: ProgressState = Field(default=ProgressState.PENDING)
    speed_bytes_per_sec: float = Field(default=0.0, ge=0.0, description="Vitesse EMA lissée.")
    speed_human: str = Field(default="0 B/s", description="Vitesse formatée (ex: '2.3 MB/s').")
    eta_seconds: float = Field(default=0.0, ge=0.0, description="Temps restant estimé.")
    eta_human: str = Field(default="--:--", description="ETA formaté (ex: '12s', '3m 45s').")
    elapsed_seconds: float = Field(default=0.0, ge=0.0, description="Temps écoulé depuis le début.")
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")


class TaskProgressSnapshot(BaseModel):
    """Snapshot immuable de la progression d'une tâche complète."""

    task_id: UUID = Field(..., description="UUID de la tâche.")
    manga_title: str = Field(default="", description="Titre du manga.")
    site_id: str = Field(default="", description="Site source.")
    priority: str = Field(default="normal", description="Priorité de la tâche.")
    chapters_total: int = Field(default=0, ge=0)
    chapters_completed: int = Field(default=0, ge=0)
    chapters_failed: int = Field(default=0, ge=0)
    pages_total: int = Field(default=0, ge=0)
    pages_completed: int = Field(default=0, ge=0)
    pages_failed: int = Field(default=0, ge=0)
    bytes_total: int = Field(default=0, ge=0)
    bytes_downloaded: int = Field(default=0, ge=0)
    percentage: float = Field(default=0.0, ge=0.0, le=1.0)
    state: ProgressState = Field(default=ProgressState.PENDING)
    speed_bytes_per_sec: float = Field(default=0.0, ge=0.0)
    speed_human: str = Field(default="0 B/s")
    eta_seconds: float = Field(default=0.0, ge=0.0)
    eta_human: str = Field(default="--:--")
    elapsed_seconds: float = Field(default=0.0, ge=0.0)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")


class GlobalProgressSnapshot(BaseModel):
    """Snapshot immuable de la progression globale du DownloadManager."""

    tasks_total: int = Field(default=0, ge=0, description="Nombre total de tâches actives.")
    tasks_running: int = Field(default=0, ge=0)
    tasks_completed: int = Field(default=0, ge=0)
    tasks_failed: int = Field(default=0, ge=0)
    tasks_paused: int = Field(default=0, ge=0)
    tasks_queued: int = Field(default=0, ge=0)
    pages_total: int = Field(default=0, ge=0)
    pages_completed: int = Field(default=0, ge=0)
    bytes_total: int = Field(default=0, ge=0)
    bytes_downloaded: int = Field(default=0, ge=0)
    percentage: float = Field(default=0.0, ge=0.0, le=1.0)
    speed_bytes_per_sec: float = Field(default=0.0, ge=0.0)
    speed_human: str = Field(default="0 B/s")
    eta_seconds: float = Field(default=0.0, ge=0.0)
    eta_human: str = Field(default="--:--")
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# PROTOCOLS
# ============================================================================


class ProgressCallback(Protocol):
    """Protocol pour les callbacks de progression synchrones (interfaces legacy)."""

    def __call__(self, snapshot: TaskProgressSnapshot) -> None:
        """Appelé à chaque mise à jour significative de progression."""
        ...


# ============================================================================
# HELPERS — Calculs de performance
# ============================================================================


class _ExponentialMovingAverage:
    """Calculateur de moyenne mobile exponentielle (EMA).

    L'EMA lisse les variations brutales de vitesse en donnant plus de poids
    aux mesures récentes tout en conservant l'historique. Formule :
        new_avg = alpha * new_sample + (1 - alpha) * old_avg

    Un alpha de 0.3 signifie que la nouvelle mesure pèse 30% et l'historique 70%.
    """

    __slots__ = ("_alpha", "_value", "_initialized")

    def __init__(self, alpha: float = 0.3) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError(f"alpha must be in (0, 1], got {alpha}")
        self._alpha: Final[float] = alpha
        self._value: float = 0.0
        self._initialized: bool = False

    def update(self, sample: float) -> float:
        """Intègre une nouvelle mesure et retourne la moyenne lissée.

        Args:
            sample: Nouvelle mesure (ex: vitesse instantanée en bytes/sec).

        Returns:
            Valeur EMA lissée après intégration.
        """
        if sample < 0:
            raise ValueError(f"Sample must be non-negative, got {sample}")

        if not self._initialized:
            self._value = sample
            self._initialized = True
        else:
            self._value = self._alpha * sample + (1.0 - self._alpha) * self._value
        return self._value

    def reset(self) -> None:
        """Réinitialise l'EMA à zéro."""
        self._value = 0.0
        self._initialized = False

    @property
    def value(self) -> float:
        """Valeur EMA actuelle."""
        return self._value


class _SlidingWindowSpeed:
    """Calculateur de vitesse basé sur une fenêtre glissante temporelle.

    Conserve les (timestamp, bytes) des N dernières secondes pour calculer
    une vitesse moyenne précise, immune aux pics transitoires.
    """

    __slots__ = ("_window_seconds", "_samples", "_total_bytes")

    def __init__(self, window_seconds: float = 30.0) -> None:
        if window_seconds <= 0:
            raise ValueError(f"window_seconds must be positive, got {window_seconds}")
        self._window_seconds: Final[float] = window_seconds
        self._samples: deque[tuple[float, int]] = deque()
        self._total_bytes: int = 0

    def record(self, bytes_delta: int, timestamp: float | None = None) -> float:
        """Enregistre un transfert et retourne la vitesse moyenne sur la fenêtre.

        Args:
            bytes_delta: Nombre de bytes transférés.
            timestamp: Timestamp monotonic (défaut: time.monotonic()).

        Returns:
            Vitesse moyenne en bytes/sec sur la fenêtre glissante.
        """
        if bytes_delta < 0:
            raise ValueError(f"bytes_delta must be non-negative, got {bytes_delta}")

        now = timestamp if timestamp is not None else time.monotonic()
        self._samples.append((now, bytes_delta))
        self._total_bytes += bytes_delta

        # Évincer les échantillons trop anciens
        cutoff = now - self._window_seconds
        while self._samples and self._samples[0][0] < cutoff:
            _, old_bytes = self._samples.popleft()
            self._total_bytes -= old_bytes

        # Calculer la vitesse
        if len(self._samples) < 2:
            return 0.0

        first_ts = self._samples[0][0]
        elapsed = now - first_ts
        if elapsed <= 0:
            return 0.0

        return self._total_bytes / elapsed

    def reset(self) -> None:
        """Réinitialise la fenêtre."""
        self._samples.clear()
        self._total_bytes = 0

    @property
    def total_bytes(self) -> int:
        """Total de bytes dans la fenêtre courante."""
        return self._total_bytes


# ============================================================================
# HELPERS — Formatage humain
# ============================================================================


def format_bytes(bytes_count: int) -> str:
    """Formate un nombre de bytes en chaîne lisible (ex: '2.3 MB').

    Args:
        bytes_count: Nombre de bytes (>= 0).

    Returns:
        Chaîne formatée avec unité appropriée (B, KB, MB, GB, TB).

    Example:
        >>> format_bytes(1234)
        '1.2 KB'
        >>> format_bytes(1234567)
        '1.2 MB'
    """
    if bytes_count < 0:
        raise ValueError(f"bytes_count must be non-negative, got {bytes_count}")

    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(bytes_count)
    for unit in units:
        if value < 1024.0:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} EB"


def format_speed(bytes_per_sec: float) -> str:
    """Formate une vitesse en chaîne lisible (ex: '2.3 MB/s').

    Args:
        bytes_per_sec: Vitesse en bytes par seconde (>= 0).

    Returns:
        Chaîne formatée avec unité/s.

    Example:
        >>> format_speed(1234567.0)
        '1.2 MB/s'
    """
    if bytes_per_sec < 0:
        raise ValueError(f"bytes_per_sec must be non-negative, got {bytes_per_sec}")
    if bytes_per_sec == 0:
        return "0 B/s"
    return f"{format_bytes(int(bytes_per_sec))}/s"


def format_eta(seconds: float) -> str:
    """Formate un temps restant en chaîne lisible (ex: '3m 45s').

    Args:
        seconds: Temps en secondes (>= 0).

    Returns:
        Chaîne formatée ('--:--' si infini, '< 1s' si très court).

    Example:
        >>> format_eta(225)
        '3m 45s'
        >>> format_eta(3661)
        '1h 01m'
    """
    if seconds < 0:
        raise ValueError(f"seconds must be non-negative, got {seconds}")
    if seconds == 0:
        return "--:--"
    if seconds < 1:
        return "< 1s"
    if seconds > 86_400:  # Plus d'un jour
        return "> 1j"

    seconds_int = int(seconds)
    if seconds_int < 60:
        return f"{seconds_int}s"
    if seconds_int < 3600:
        minutes = seconds_int // 60
        secs = seconds_int % 60
        return f"{minutes}m {secs:02d}s" if secs else f"{minutes}m"
    hours = seconds_int // 3600
    minutes = (seconds_int % 3600) // 60
    return f"{hours}h {minutes:02d}m"


def format_duration(seconds: float) -> str:
    """Formate une durée écoulée en chaîne lisible.

    Args:
        seconds: Durée en secondes (>= 0).

    Returns:
        Chaîne formatée.

    Example:
        >>> format_duration(65.5)
        '1m 05s'
    """
    if seconds < 0:
        raise ValueError(f"seconds must be non-negative, got {seconds}")
    return format_eta(seconds)


# ============================================================================
# CLASSES DE SUIVI — Page / Chapter / Task
# ============================================================================


class PageProgress:
    """Suivi de progression d'une page individuelle.

    Non exposé publiquement — utilisé en interne par ChapterProgress.
    """

    __slots__ = (
        "_index",
        "_bytes_total",
        "_bytes_downloaded",
        "_state",
        "_start_time",
        "_end_time",
        "_lock",
    )

    def __init__(self, page_index: int, bytes_total: int = 0) -> None:
        self._index = page_index
        self._bytes_total = bytes_total
        self._bytes_downloaded = 0
        self._state = ProgressState.PENDING
        self._start_time: float | None = None
        self._end_time: float | None = None
        self._lock = asyncio.Lock()

    async def start(self, bytes_total: int | None = None) -> None:
        """Marque la page comme en cours de téléchargement."""
        async with self._lock:
            if bytes_total is not None:
                self._bytes_total = bytes_total
            self._state = ProgressState.RUNNING
            self._start_time = time.monotonic()

    async def update(self, bytes_delta: int) -> None:
        """Enregistre un transfert partiel."""
        async with self._lock:
            self._bytes_downloaded += bytes_delta

    async def complete(self) -> None:
        """Marque la page comme terminée avec succès."""
        async with self._lock:
            self._state = ProgressState.COMPLETED
            self._end_time = time.monotonic()
            if self._bytes_total > 0:
                self._bytes_downloaded = self._bytes_total

    async def fail(self) -> None:
        """Marque la page comme échouée."""
        async with self._lock:
            self._state = ProgressState.FAILED
            self._end_time = time.monotonic()

    async def snapshot(self) -> PageProgressSnapshot:
        """Retourne un snapshot immuable de l'état actuel."""
        async with self._lock:
            percentage = (
                self._bytes_downloaded / self._bytes_total
                if self._bytes_total > 0
                else 0.0
            )
            duration = 0.0
            speed = 0.0
            if self._start_time is not None:
                end = self._end_time if self._end_time is not None else time.monotonic()
                duration = end - self._start_time
                if duration > 0:
                    speed = self._bytes_downloaded / duration

            return PageProgressSnapshot(
                page_index=self._index,
                bytes_total=self._bytes_total,
                bytes_downloaded=self._bytes_downloaded,
                percentage=min(percentage, 1.0),
                state=self._state,
                duration_seconds=duration,
                speed_bytes_per_sec=speed,
            )


class ChapterProgress:
    """Suivi de progression d'un chapitre (agrège les pages)."""

    __slots__ = (
        "_chapter_id",
        "_manga_id",
        "_manga_title",
        "_chapter_title",
        "_chapter_number",
        "_pages",
        "_pages_total",
        "_pages_completed",
        "_pages_failed",
        "_bytes_total",
        "_bytes_downloaded",
        "_state",
        "_start_time",
        "_end_time",
        "_speed_ema",
        "_speed_window",
        "_lock",
        "_logger",
    )

    def __init__(
        self,
        chapter_id: str,
        *,
        manga_id: str = "",
        manga_title: str = "",
        chapter_title: str = "",
        chapter_number: str = "",
        pages_total: int = 0,
    ) -> None:
        self._chapter_id = chapter_id
        self._manga_id = manga_id
        self._manga_title = manga_title
        self._chapter_title = chapter_title
        self._chapter_number = chapter_number
        self._pages_total = pages_total
        self._pages: dict[int, PageProgress] = {}
        self._pages_completed = 0
        self._pages_failed = 0
        self._bytes_total = 0
        self._bytes_downloaded = 0
        self._state = ProgressState.PENDING
        self._start_time: float | None = None
        self._end_time: float | None = None
        self._speed_ema = _ExponentialMovingAverage(alpha=0.3)
        self._speed_window = _SlidingWindowSpeed(window_seconds=30.0)
        self._lock = asyncio.Lock()
        self._logger = logger.bind(module="progress", chapter_id=chapter_id)

    async def start(self) -> None:
        """Démarre le suivi du chapitre."""
        async with self._lock:
            self._state = ProgressState.RUNNING
            self._start_time = time.monotonic()

    async def create_page(self, page_index: int, bytes_total: int = 0) -> PageProgress:
        """Crée et enregistre le suivi d'une page.

        Args:
            page_index: Index de la page (0-based).
            bytes_total: Taille totale estimée de la page (0 si inconnue).

        Returns:
            L'objet PageProgress créé.
        """
        async with self._lock:
            page = PageProgress(page_index, bytes_total)
            self._pages[page_index] = page
            if bytes_total > 0:
                self._bytes_total += bytes_total
            return page

    async def record_page_complete(
        self,
        page_index: int,
        bytes_downloaded: int,
    ) -> None:
        """Enregistre le téléchargement complet d'une page.

        Args:
            page_index: Index de la page.
            bytes_downloaded: Taille réelle téléchargée.
        """
        async with self._lock:
            page = self._pages.get(page_index)
            if page is None:
                self._logger.warning(
                    "Page {} non enregistrée, création à la volée", page_index
                )
                page = await self.create_page(page_index, bytes_downloaded)

            await page.update(bytes_downloaded - page._bytes_downloaded)
            await page.complete()

            self._pages_completed += 1
            self._bytes_downloaded += bytes_downloaded

            # Mise à jour de la vitesse
            self._speed_window.record(bytes_downloaded)
            self._speed_ema.update(
                bytes_downloaded / max(time.monotonic() - (page._start_time or time.monotonic()), 0.001)
            )

    async def record_page_failed(self, page_index: int) -> None:
        """Enregistre l'échec d'une page."""
        async with self._lock:
            page = self._pages.get(page_index)
            if page is not None:
                await page.fail()
            self._pages_failed += 1

    async def complete(self) -> None:
        """Marque le chapitre comme terminé avec succès."""
        async with self._lock:
            self._state = ProgressState.COMPLETED
            self._end_time = time.monotonic()

    async def fail(self, error: str | None = None) -> None:
        """Marque le chapitre comme échoué."""
        async with self._lock:
            self._state = ProgressState.FAILED
            self._end_time = time.monotonic()
            if error:
                self._logger.error("Chapitre échoué: {}", error)

    async def pause(self) -> None:
        """Met le chapitre en pause."""
        async with self._lock:
            if self._state == ProgressState.RUNNING:
                self._state = ProgressState.PAUSED

    async def resume(self) -> None:
        """Reprend le chapitre après pause."""
        async with self._lock:
            if self._state == ProgressState.PAUSED:
                self._state = ProgressState.RUNNING

    async def snapshot(self) -> ChapterProgressSnapshot:
        """Retourne un snapshot immuable de l'état actuel."""
        async with self._lock:
            percentage = (
                self._bytes_downloaded / self._bytes_total
                if self._bytes_total > 0
                else (
                    self._pages_completed / self._pages_total
                    if self._pages_total > 0
                    else 0.0
                )
            )
            elapsed = 0.0
            if self._start_time is not None:
                end = self._end_time if self._end_time is not None else time.monotonic()
                elapsed = end - self._start_time

            speed = self._speed_ema.value
            bytes_remaining = max(self._bytes_total - self._bytes_downloaded, 0)
            eta = bytes_remaining / speed if speed > 0 else 0.0

            started_at = None
            completed_at = None
            if self._start_time is not None:
                started_at = datetime.now(UTC)
            if self._end_time is not None:
                completed_at = datetime.now(UTC)

            return ChapterProgressSnapshot(
                chapter_id=self._chapter_id,
                manga_id=self._manga_id,
                manga_title=self._manga_title,
                chapter_title=self._chapter_title,
                chapter_number=self._chapter_number,
                pages_total=self._pages_total,
                pages_completed=self._pages_completed,
                pages_failed=self._pages_failed,
                bytes_total=self._bytes_total,
                bytes_downloaded=self._bytes_downloaded,
                percentage=min(percentage, 1.0),
                state=self._state,
                speed_bytes_per_sec=speed,
                speed_human=format_speed(speed),
                eta_seconds=eta,
                eta_human=format_eta(eta) if self._state == ProgressState.RUNNING else "--:--",
                elapsed_seconds=elapsed,
                started_at=started_at,
                completed_at=completed_at,
            )


class TaskProgress:
    """Suivi de progression d'une tâche complète (agrège les chapitres)."""

    __slots__ = (
        "_task_id",
        "_manga_title",
        "_site_id",
        "_priority",
        "_chapters",
        "_chapters_total",
        "_chapters_completed",
        "_chapters_failed",
        "_bytes_total",
        "_bytes_downloaded",
        "_pages_total",
        "_pages_completed",
        "_pages_failed",
        "_state",
        "_start_time",
        "_end_time",
        "_speed_ema",
        "_speed_window",
        "_error",
        "_lock",
        "_logger",
    )

    def __init__(
        self,
        task_id: UUID,
        *,
        manga_title: str = "",
        site_id: str = "",
        priority: str = "normal",
        chapters_total: int = 0,
    ) -> None:
        self._task_id = task_id
        self._manga_title = manga_title
        self._site_id = site_id
        self._priority = priority
        self._chapters_total = chapters_total
        self._chapters: dict[str, ChapterProgress] = {}
        self._chapters_completed = 0
        self._chapters_failed = 0
        self._bytes_total = 0
        self._bytes_downloaded = 0
        self._pages_total = 0
        self._pages_completed = 0
        self._pages_failed = 0
        self._state = ProgressState.PENDING
        self._start_time: float | None = None
        self._end_time: float | None = None
        self._speed_ema = _ExponentialMovingAverage(alpha=0.3)
        self._speed_window = _SlidingWindowSpeed(window_seconds=30.0)
        self._error: str | None = None
        self._lock = asyncio.Lock()
        self._logger = logger.bind(module="progress", task_id=str(task_id))

    async def start(self) -> None:
        """Démarre le suivi de la tâche."""
        async with self._lock:
            self._state = ProgressState.RUNNING
            self._start_time = time.monotonic()

    async def track_chapter(
        self,
        chapter_id: str,
        *,
        pages_count: int = 0,
        manga_id: str = "",
        manga_title: str = "",
        chapter_title: str = "",
        chapter_number: str = "",
    ) -> ChapterProgress:
        """Crée et enregistre le suivi d'un chapitre.

        Args:
            chapter_id: Identifiant unique du chapitre.
            pages_count: Nombre de pages du chapitre.
            manga_id: ID du manga parent.
            manga_title: Titre du manga.
            chapter_title: Titre du chapitre.
            chapter_number: Numéro du chapitre.

        Returns:
            L'objet ChapterProgress créé.
        """
        async with self._lock:
            chapter = ChapterProgress(
                chapter_id,
                manga_id=manga_id,
                manga_title=manga_title,
                chapter_title=chapter_title,
                chapter_number=chapter_number,
                pages_total=pages_count,
            )
            self._chapters[chapter_id] = chapter
            self._pages_total += pages_count
            return chapter

    async def record_chapter_complete(
        self,
        chapter_id: str,
        bytes_downloaded: int,
        pages_downloaded: int,
    ) -> None:
        """Enregistre le téléchargement complet d'un chapitre."""
        async with self._lock:
            chapter = self._chapters.get(chapter_id)
            if chapter is not None:
                await chapter.complete()

            self._chapters_completed += 1
            self._bytes_downloaded += bytes_downloaded
            self._pages_completed += pages_downloaded

            # Mise à jour de la vitesse globale
            self._speed_window.record(bytes_downloaded)
            if self._start_time is not None:
                elapsed = time.monotonic() - self._start_time
                if elapsed > 0:
                    self._speed_ema.update(self._bytes_downloaded / elapsed)

    async def record_chapter_failed(self, chapter_id: str, error: str | None = None) -> None:
        """Enregistre l'échec d'un chapitre."""
        async with self._lock:
            chapter = self._chapters.get(chapter_id)
            if chapter is not None:
                await chapter.fail(error)
            self._chapters_failed += 1

    async def complete(self) -> None:
        """Marque la tâche comme terminée avec succès."""
        async with self._lock:
            self._state = ProgressState.COMPLETED
            self._end_time = time.monotonic()

    async def fail(self, error: str | None = None) -> None:
        """Marque la tâche comme échouée."""
        async with self._lock:
            self._state = ProgressState.FAILED
            self._end_time = time.monotonic()
            self._error = error

    async def cancel(self) -> None:
        """Annule la tâche."""
        async with self._lock:
            self._state = ProgressState.CANCELLED
            self._end_time = time.monotonic()

    async def pause(self) -> None:
        """Met la tâche en pause."""
        async with self._lock:
            if self._state == ProgressState.RUNNING:
                self._state = ProgressState.PAUSED
                for chapter in self._chapters.values():
                    await chapter.pause()

    async def resume(self) -> None:
        """Reprend la tâche après pause."""
        async with self._lock:
            if self._state == ProgressState.PAUSED:
                self._state = ProgressState.RUNNING
                for chapter in self._chapters.values():
                    await chapter.resume()

    async def snapshot(self) -> TaskProgressSnapshot:
        """Retourne un snapshot immuable de l'état actuel."""
        async with self._lock:
            percentage = (
                self._bytes_downloaded / self._bytes_total
                if self._bytes_total > 0
                else (
                    self._chapters_completed / self._chapters_total
                    if self._chapters_total > 0
                    else 0.0
                )
            )
            elapsed = 0.0
            if self._start_time is not None:
                end = self._end_time if self._end_time is not None else time.monotonic()
                elapsed = end - self._start_time

            speed = self._speed_ema.value
            bytes_remaining = max(self._bytes_total - self._bytes_downloaded, 0)
            eta = bytes_remaining / speed if speed > 0 else 0.0

            started_at = None
            completed_at = None
            if self._start_time is not None:
                started_at = datetime.now(UTC)
            if self._end_time is not None:
                completed_at = datetime.now(UTC)

            return TaskProgressSnapshot(
                task_id=self._task_id,
                manga_title=self._manga_title,
                site_id=self._site_id,
                priority=self._priority,
                chapters_total=self._chapters_total,
                chapters_completed=self._chapters_completed,
                chapters_failed=self._chapters_failed,
                pages_total=self._pages_total,
                pages_completed=self._pages_completed,
                pages_failed=self._pages_failed,
                bytes_total=self._bytes_total,
                bytes_downloaded=self._bytes_downloaded,
                percentage=min(percentage, 1.0),
                state=self._state,
                speed_bytes_per_sec=speed,
                speed_human=format_speed(speed),
                eta_seconds=eta,
                eta_human=format_eta(eta) if self._state == ProgressState.RUNNING else "--:--",
                elapsed_seconds=elapsed,
                started_at=started_at,
                completed_at=completed_at,
                error=self._error,
            )


# ============================================================================
# CLASSE PRINCIPALE — ProgressTracker
# ============================================================================


class ProgressTracker:
    """Tracker central de progression pour tous les téléchargements.

    Gère le cycle de vie des suivis de tâches, agrège les statistiques
    globales, et émet des événements sur l'EventBus avec throttling.

    Lifecycle :
        >>> tracker = ProgressTracker(event_bus=event_bus)
        >>> await tracker.start()
        >>> # ... utilisation ...
        >>> await tracker.stop()
    """

    # Seuil minimum de variation pour émettre un événement (évite le spam)
    _MIN_PERCENTAGE_DELTA: Final[float] = 0.005  # 0.5%
    _MIN_TIME_BETWEEN_EVENTS: Final[float] = 0.25  # 250ms

    def __init__(
        self,
        event_bus: EventBus,
        *,
        emit_interval: float = 0.25,
        max_tracked_tasks: int = 1000,
    ) -> None:
        """Initialise le tracker de progression.

        Args:
            event_bus: Bus d'événements pour émettre les mises à jour.
            emit_interval: Intervalle minimum entre deux émissions d'événements
                           pour une même tâche (secondes). Défaut: 250ms.
            max_tracked_tasks: Nombre maximum de tâches suivies simultanément.
        """
        if emit_interval < 0:
            raise ValueError(f"emit_interval must be non-negative, got {emit_interval}")
        if max_tracked_tasks <= 0:
            raise ValueError(f"max_tracked_tasks must be positive, got {max_tracked_tasks}")

        self._event_bus = event_bus
        self._emit_interval = emit_interval
        self._max_tracked_tasks = max_tracked_tasks

        self._tasks: dict[UUID, TaskProgress] = {}
        self._last_emit_time: dict[UUID, float] = {}
        self._last_emit_percentage: dict[UUID, float] = {}
        self._lock = asyncio.Lock()

        self._started = False
        self._start_time: float | None = None

        # Statistiques globales
        self._global_completed = 0
        self._global_failed = 0

        self._logger = logger.bind(module="progress_tracker")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre le tracker."""
        if self._started:
            self._logger.warning("ProgressTracker déjà démarré, ignore")
            return

        self._started = True
        self._start_time = time.monotonic()
        self._logger.info("ProgressTracker démarré")

    async def stop(self) -> None:
        """Arrête le tracker et libère les ressources."""
        if not self._started:
            return

        async with self._lock:
            self._tasks.clear()
            self._last_emit_time.clear()
            self._last_emit_percentage.clear()

        self._started = False
        self._start_time = None
        self._logger.info("ProgressTracker arrêté")

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------------

    @property
    def is_started(self) -> bool:
        """Indique si le tracker est démarré."""
        return self._started

    @property
    def tracked_tasks_count(self) -> int:
        """Nombre de tâches actuellement suivies."""
        return len(self._tasks)

    # ------------------------------------------------------------------------
    # API publique — Suivi de tâches
    # ------------------------------------------------------------------------

    async def track_task(
        self,
        task_id: UUID,
        *,
        manga_title: str = "",
        site_id: str = "",
        priority: str = "normal",
        chapters_total: int = 0,
    ) -> TaskProgress:
        """Crée et enregistre le suivi d'une tâche.

        Args:
            task_id: UUID de la tâche.
            manga_title: Titre du manga.
            site_id: Site source.
            priority: Priorité de la tâche.
            chapters_total: Nombre total de chapitres.

        Returns:
            L'objet TaskProgress créé.

        Raises:
            TrackerNotStartedError: Si le tracker n'est pas démarré.
            ProgressError: Si la limite de tâches suivies est atteinte.
        """
        self._ensure_started()

        async with self._lock:
            if len(self._tasks) >= self._max_tracked_tasks:
                raise ProgressError(
                    f"Limite de tâches suivies atteinte ({self._max_tracked_tasks})"
                )

            task = TaskProgress(
                task_id,
                manga_title=manga_title,
                site_id=site_id,
                priority=priority,
                chapters_total=chapters_total,
            )
            self._tasks[task_id] = task
            self._last_emit_percentage[task_id] = 0.0

            self._logger.debug(
                "Suivi démarré pour tâche {}: {} chapitres",
                task_id,
                chapters_total,
            )
            return task

    async def untrack_task(self, task_id: UUID) -> None:
        """Retire une tâche du suivi (après complétion ou échec).

        Les statistiques globales sont conservées.

        Args:
            task_id: UUID de la tâche à retirer.
        """
        async with self._lock:
            task = self._tasks.pop(task_id, None)
            self._last_emit_time.pop(task_id, None)
            self._last_emit_percentage.pop(task_id, None)

            if task is not None:
                snapshot = await task.snapshot()
                if snapshot.state == ProgressState.COMPLETED:
                    self._global_completed += 1
                elif snapshot.state == ProgressState.FAILED:
                    self._global_failed += 1

                self._logger.debug("Suivi terminé pour tâche {}", task_id)

    def get_task(self, task_id: UUID) -> TaskProgress | None:
        """Récupère un tracker de tâche par son ID.

        Args:
            task_id: UUID de la tâche.

        Returns:
            Le TaskProgress si trouvé, None sinon.
        """
        return self._tasks.get(task_id)

    def list_tracked_tasks(self) -> list[UUID]:
        """Liste les IDs des tâches actuellement suivies."""
        return list(self._tasks.keys())

    # ------------------------------------------------------------------------
    # API publique — Émission d'événements
    # ------------------------------------------------------------------------

    async def emit_task_progress(self, task_id: UUID, *, force: bool = False) -> None:
        """Émet un événement de progression pour une tâche (avec throttling).

        L'émission est throttled pour éviter de saturer l'EventBus et les
        interfaces. Un événement est émis si :
            - `force` est True
            - OU la variation de pourcentage dépasse 0.5%
            - OU plus de 250ms se sont écoulées depuis la dernière émission

        Args:
            task_id: UUID de la tâche.
            force: Force l'émission même si le throttling s'applique.
        """
        self._ensure_started()

        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return

            snapshot = await task.snapshot()
            now = time.monotonic()
            last_emit = self._last_emit_time.get(task_id, 0.0)
            last_pct = self._last_emit_percentage.get(task_id, 0.0)

            # Vérifier le throttling
            pct_delta = abs(snapshot.percentage - last_pct)
            time_delta = now - last_emit

            should_emit = (
                force
                or pct_delta >= self._MIN_PERCENTAGE_DELTA
                or time_delta >= self._MIN_TIME_BETWEEN_EVENTS
                or snapshot.state in (
                    ProgressState.COMPLETED,
                    ProgressState.FAILED,
                    ProgressState.CANCELLED,
                )
            )

            if not should_emit:
                return

            # Émettre l'événement
            await self._event_bus.emit(
                "progress.task.updated",
                {
                    "task_id": str(task_id),
                    "snapshot": snapshot.model_dump(mode="json"),
                },
            )

            self._last_emit_time[task_id] = now
            self._last_emit_percentage[task_id] = snapshot.percentage

    async def emit_global_progress(self) -> None:
        """Émet un événement de progression globale agrégée."""
        self._ensure_started()

        snapshot = await self.global_snapshot()
        await self._event_bus.emit(
            "progress.global.updated",
            {"snapshot": snapshot.model_dump(mode="json")},
        )

    # ------------------------------------------------------------------------
    # API publique — Snapshots
    # ------------------------------------------------------------------------

    async def task_snapshot(self, task_id: UUID) -> TaskProgressSnapshot:
        """Retourne un snapshot immuable d'une tâche.

        Args:
            task_id: UUID de la tâche.

        Returns:
            Snapshot de la progression.

        Raises:
            TaskNotTrackedError: Si la tâche n'est pas suivie.
        """
        self._ensure_started()

        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotTrackedError(task_id)
            return await task.snapshot()

    async def global_snapshot(self) -> GlobalProgressSnapshot:
        """Retourne un snapshot immuable de la progression globale.

        Agrège les statistiques de toutes les tâches suivies.

        Returns:
            Snapshot global.
        """
        self._ensure_started()

        async with self._lock:
            tasks_total = len(self._tasks)
            tasks_running = 0
            tasks_paused = 0
            tasks_queued = 0
            pages_total = 0
            pages_completed = 0
            bytes_total = 0
            bytes_downloaded = 0
            total_speed = 0.0

            for task in self._tasks.values():
                snapshot = await task.snapshot()
                if snapshot.state == ProgressState.RUNNING:
                    tasks_running += 1
                elif snapshot.state == ProgressState.PAUSED:
                    tasks_paused += 1
                elif snapshot.state == ProgressState.PENDING:
                    tasks_queued += 1

                pages_total += snapshot.pages_total
                pages_completed += snapshot.pages_completed
                bytes_total += snapshot.bytes_total
                bytes_downloaded += snapshot.bytes_downloaded
                total_speed += snapshot.speed_bytes_per_sec

            percentage = (
                bytes_downloaded / bytes_total
                if bytes_total > 0
                else 0.0
            )
            bytes_remaining = max(bytes_total - bytes_downloaded, 0)
            eta = bytes_remaining / total_speed if total_speed > 0 else 0.0

            uptime = 0.0
            if self._start_time is not None:
                uptime = time.monotonic() - self._start_time

            return GlobalProgressSnapshot(
                tasks_total=tasks_total,
                tasks_running=tasks_running,
                tasks_completed=self._global_completed,
                tasks_failed=self._global_failed,
                tasks_paused=tasks_paused,
                tasks_queued=tasks_queued,
                pages_total=pages_total,
                pages_completed=pages_completed,
                bytes_total=bytes_total,
                bytes_downloaded=bytes_downloaded,
                percentage=min(percentage, 1.0),
                speed_bytes_per_sec=total_speed,
                speed_human=format_speed(total_speed),
                eta_seconds=eta,
                eta_human=format_eta(eta) if tasks_running > 0 else "--:--",
                uptime_seconds=uptime,
            )

    # ------------------------------------------------------------------------
    # Méthodes internes
    # ------------------------------------------------------------------------

    def _ensure_started(self) -> None:
        """Vérifie que le tracker est démarré."""
        if not self._started:
            raise TrackerNotStartedError()

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<ProgressTracker status={status} "
            f"tracked_tasks={len(self._tasks)}>"
        )
