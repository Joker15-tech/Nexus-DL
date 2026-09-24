"""Routeur FastAPI pour la gestion des téléchargements.

Ce module fournit un routeur FastAPI complet pour gérer les tâches de
téléchargement : créer, lister, contrôler (pause/resume/cancel), et
supprimer des tâches. Il intègre le DownloadManager pour l'orchestration
réelle des téléchargements.

**Endpoints** :
    - GET /downloads                            : Lister toutes les tâches
    - GET /downloads/stats                      : Statistiques globales
    - GET /downloads/active                     : Tâches actives uniquement
    - GET /downloads/{task_id}                  : Détails d'une tâche
    - POST /downloads                           : Créer une tâche
    - DELETE /downloads/{task_id}               : Supprimer une tâche
    - POST /downloads/{task_id}/pause           : Mettre en pause
    - POST /downloads/{task_id}/resume          : Reprendre
    - POST /downloads/{task_id}/cancel          : Annuler
    - POST /downloads/{task_id}/retry           : Réessayer
    - POST /downloads/pause-all                 : Tout mettre en pause
    - POST /downloads/resume-all                : Tout reprendre
    - POST /downloads/clear-completed           : Effacer les tâches terminées

**Fonctionnalités** :
    - Intégration avec DownloadManager
    - Cache en mémoire pour les lectures fréquentes
    - Filtrage par statut (active, pending, completed, failed)
    - Tri par date, progression, taille, nom
    - Pagination
    - Statistiques globales (vitesse, ETA, taille)
    - Actions globales (pause all, resume all, clear completed)
    - Événements EventBus pour monitoring
    - Logging structuré
    - Validation stricte via Pydantic v2
    - Gestion robuste des erreurs

**Exemple d'utilisation** :
    >>> from fastapi import FastAPI
    >>> from nexusdl.interfaces.web.backend.routers.download import download_router
    >>>
    >>> app = FastAPI()
    >>> app.include_router(download_router, prefix="/api/v1")

**Exemples d'appels API** :
    >>> # Créer une tâche
    >>> POST /api/v1/downloads
    >>> {
    ...     "site_id": "mangadex",
    ...     "manga_id": "12345",
    ...     "chapter_ids": ["ch1", "ch2"],
    ...     "format": "cbz",
    ...     "quality": "original",
    ...     "priority": "normal"
    ... }
    >>>
    >>> # Lister avec filtres
    >>> GET /api/v1/downloads?status=active&sort_by=progress
    >>>
    >>> # Mettre en pause
    >>> POST /api/v1/downloads/task_123/pause
    >>>
    >>> # Statistiques
    >>> GET /api/v1/downloads/stats

Intégration :
    - core/downloader/manager.py     : DownloadManager
    - core/models/download.py        : Modèles DownloadTask, DownloadStatus
    - core/events.py                 : EventBus pour monitoring
    - core/logger.py                 : Logs
    - core/i18n.py                   : Traductions
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Final

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from nexusdl.core.constants import APP_NAME
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t


# ============================================================================
# CONSTANTES
# ============================================================================


# Valeurs par défaut
DEFAULT_DOWNLOAD_LIMIT: Final[int] = 50
MAX_DOWNLOAD_LIMIT: Final[int] = 200
DEFAULT_CACHE_TTL_SECONDS: Final[int] = 30  # 30 secondes
MAX_CACHE_SIZE: Final[int] = 200

# Formats de téléchargement
SUPPORTED_FORMATS: Final[frozenset[str]] = frozenset({
    "cbz", "cbr", "pdf", "zip", "folder",
})

# Qualités d'image
SUPPORTED_QUALITIES: Final[frozenset[str]] = frozenset({
    "original", "high", "medium", "low",
})

# Priorités
SUPPORTED_PRIORITIES: Final[frozenset[str]] = frozenset({
    "low", "normal", "high", "urgent",
})


# ============================================================================
# EXCEPTIONS
# ============================================================================


class DownloadRouterError(NexusDLError):
    """Exception de base pour les erreurs du routeur de téléchargement."""


class TaskNotFoundError(DownloadRouterError):
    """Exception levée lorsqu'une tâche n'est pas trouvée.

    Attributes:
        task_id: ID de la tâche.
    """

    def __init__(self, task_id: str) -> None:
        super().__init__(
            t(
                "download.error.task_not_found",
                default="Download task not found: {task_id}",
                task_id=task_id,
            )
        )
        self.task_id = task_id


class TaskActionError(DownloadRouterError):
    """Exception levée lorsqu'une action sur une tâche échoue.

    Attributes:
        task_id: ID de la tâche.
        action: Action tentée.
        reason: Raison de l'échec.
    """

    def __init__(self, task_id: str, action: str, reason: str = "") -> None:
        msg = t(
            "download.error.action_failed",
            default="Action '{action}' failed on task {task_id}",
            action=action,
            task_id=task_id,
        )
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.task_id = task_id
        self.action = action
        self.reason = reason


class DownloadManagerNotAvailableError(DownloadRouterError):
    """Exception levée lorsque le DownloadManager n'est pas disponible."""

    def __init__(self) -> None:
        super().__init__(
            t(
                "download.error.manager_unavailable",
                default="DownloadManager is not available",
            )
        )


# ============================================================================
# ENUMS
# ============================================================================


class DownloadStatus(str, Enum):
    """Statut d'une tâche de téléchargement.

    Attributes:
        PENDING: En attente.
        QUEUED: Dans la file d'attente.
        RUNNING: En cours.
        DOWNLOADING: Téléchargement en cours.
        PAUSED: En pause.
        COMPLETED: Terminé.
        FAILED: Échoué.
        CANCELLED: Annulé.
    """

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            DownloadStatus.PENDING: t("download.status.pending", default="Pending"),
            DownloadStatus.QUEUED: t("download.status.queued", default="Queued"),
            DownloadStatus.RUNNING: t("download.status.running", default="Running"),
            DownloadStatus.DOWNLOADING: t("download.status.downloading", default="Downloading"),
            DownloadStatus.PAUSED: t("download.status.paused", default="Paused"),
            DownloadStatus.COMPLETED: t("download.status.completed", default="Completed"),
            DownloadStatus.FAILED: t("download.status.failed", default="Failed"),
            DownloadStatus.CANCELLED: t("download.status.cancelled", default="Cancelled"),
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            DownloadStatus.PENDING: "⏳",
            DownloadStatus.QUEUED: "📋",
            DownloadStatus.RUNNING: "⚡",
            DownloadStatus.DOWNLOADING: "⬇️",
            DownloadStatus.PAUSED: "⏸️",
            DownloadStatus.COMPLETED: "✅",
            DownloadStatus.FAILED: "❌",
            DownloadStatus.CANCELLED: "🚫",
        }[self]

    @property
    def is_active(self) -> bool:
        """Indique si le statut est actif."""
        return self in (DownloadStatus.RUNNING, DownloadStatus.DOWNLOADING)

    @property
    def is_terminal(self) -> bool:
        """Indique si le statut est terminal."""
        return self in (DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.CANCELLED)


class DownloadFilter(str, Enum):
    """Filtre de statut pour la liste des tâches.

    Attributes:
        ALL: Toutes les tâches.
        ACTIVE: Tâches actives (running, downloading).
        PENDING: Tâches en attente (pending, queued).
        COMPLETED: Tâches terminées.
        FAILED: Tâches échouées.
        CANCELLED: Tâches annulées.
        PAUSED: Tâches en pause.
    """

    ALL = "all"
    ACTIVE = "active"
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            DownloadFilter.ALL: t("download.filter.all", default="All"),
            DownloadFilter.ACTIVE: t("download.filter.active", default="Active"),
            DownloadFilter.PENDING: t("download.filter.pending", default="Pending"),
            DownloadFilter.COMPLETED: t("download.filter.completed", default="Completed"),
            DownloadFilter.FAILED: t("download.filter.failed", default="Failed"),
            DownloadFilter.CANCELLED: t("download.filter.cancelled", default="Cancelled"),
            DownloadFilter.PAUSED: t("download.filter.paused", default="Paused"),
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            DownloadFilter.ALL: "📋",
            DownloadFilter.ACTIVE: "⚡",
            DownloadFilter.PENDING: "⏳",
            DownloadFilter.COMPLETED: "✅",
            DownloadFilter.FAILED: "❌",
            DownloadFilter.CANCELLED: "🚫",
            DownloadFilter.PAUSED: "⏸️",
        }[self]

    def matches_status(self, task_status: DownloadStatus) -> bool:
        """Vérifie si un statut correspond au filtre.

        Args:
            task_status: Statut de la tâche.

        Returns:
            True si correspond.
        """
        if self == DownloadFilter.ALL:
            return True
        if self == DownloadFilter.ACTIVE:
            return task_status in (DownloadStatus.RUNNING, DownloadStatus.DOWNLOADING)
        if self == DownloadFilter.PENDING:
            return task_status in (DownloadStatus.PENDING, DownloadStatus.QUEUED)
        if self == DownloadFilter.COMPLETED:
            return task_status == DownloadStatus.COMPLETED
        if self == DownloadFilter.FAILED:
            return task_status == DownloadStatus.FAILED
        if self == DownloadFilter.CANCELLED:
            return task_status == DownloadStatus.CANCELLED
        if self == DownloadFilter.PAUSED:
            return task_status == DownloadStatus.PAUSED
        return False


class DownloadSortBy(str, Enum):
    """Critère de tri des tâches.

    Attributes:
        DATE_ADDED: Tri par date d'ajout.
        PROGRESS: Tri par progression.
        SIZE: Tri par taille.
        NAME: Tri par nom.
        PRIORITY: Tri par priorité.
        STATUS: Tri par statut.
    """

    DATE_ADDED = "date_added"
    PROGRESS = "progress"
    SIZE = "size"
    NAME = "name"
    PRIORITY = "priority"
    STATUS = "status"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            DownloadSortBy.DATE_ADDED: t("download.sort.date_added", default="Date Added"),
            DownloadSortBy.PROGRESS: t("download.sort.progress", default="Progress"),
            DownloadSortBy.SIZE: t("download.sort.size", default="Size"),
            DownloadSortBy.NAME: t("download.sort.name", default="Name"),
            DownloadSortBy.PRIORITY: t("download.sort.priority", default="Priority"),
            DownloadSortBy.STATUS: t("download.sort.status", default="Status"),
        }[self]


class DownloadFormat(str, Enum):
    """Format de téléchargement.

    Attributes:
        CBZ: Archive CBZ.
        CBR: Archive CBR.
        PDF: Document PDF.
        ZIP: Archive ZIP.
        FOLDER: Dossier avec images.
    """

    CBZ = "cbz"
    CBR = "cbr"
    PDF = "pdf"
    ZIP = "zip"
    FOLDER = "folder"


class ImageQuality(str, Enum):
    """Qualité d'image.

    Attributes:
        ORIGINAL: Qualité originale.
        HIGH: Haute qualité.
        MEDIUM: Qualité moyenne.
        LOW: Basse qualité.
    """

    ORIGINAL = "original"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Priority(str, Enum):
    """Priorité de la tâche.

    Attributes:
        LOW: Priorité basse.
        NORMAL: Priorité normale.
        HIGH: Priorité haute.
        URGENT: Priorité urgente.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

    @property
    def value_int(self) -> int:
        """Valeur numérique (pour tri)."""
        return {
            Priority.LOW: 0,
            Priority.NORMAL: 1,
            Priority.HIGH: 2,
            Priority.URGENT: 3,
        }[self]


# ============================================================================
# MODÈLES DE REQUÊTE — Pydantic
# ============================================================================


class CreateDownloadRequest(BaseModel):
    """Requête de création d'une tâche de téléchargement.

    Attributes:
        site_id: ID du site.
        manga_id: ID du manga.
        chapter_ids: IDs des chapitres à télécharger (None = tous).
        format: Format de téléchargement.
        quality: Qualité d'image.
        priority: Priorité de la tâche.
        output_dir: Répertoire de sortie (optionnel).
    """

    site_id: str = Field(..., min_length=1, description="ID du site.")
    manga_id: str = Field(..., min_length=1, description="ID du manga.")
    chapter_ids: list[str] | None = Field(default=None, description="Chapitres (None = tous).")
    format: DownloadFormat = Field(default=DownloadFormat.CBZ, description="Format.")
    quality: ImageQuality = Field(default=ImageQuality.ORIGINAL, description="Qualité.")
    priority: Priority = Field(default=Priority.NORMAL, description="Priorité.")
    output_dir: str | None = Field(default=None, description="Répertoire de sortie.")

    @field_validator("chapter_ids")
    @classmethod
    def validate_chapter_ids(cls, v: list[str] | None) -> list[str] | None:
        """Valide les IDs de chapitres."""
        if v is not None and len(v) > 100:
            raise ValueError("Maximum 100 chapitres par tâche")
        return v


class TaskActionRequest(BaseModel):
    """Requête d'action sur une tâche.

    Attributes:
        reason: Raison de l'action (optionnel).
    """

    reason: str | None = Field(default=None, max_length=500, description="Raison.")


# ============================================================================
# MODÈLES DE RÉPONSE — Pydantic
# ============================================================================


class DownloadTaskResponse(BaseModel):
    """Réponse avec les détails d'une tâche.

    Attributes:
        id: ID unique.
        site_id: ID du site.
        manga_id: ID du manga.
        manga_title: Titre du manga.
        status: Statut de la tâche.
        progress: Progression (0.0 à 1.0).
        pages_completed: Pages téléchargées.
        pages_total: Pages totales.
        chapters_count: Nombre de chapitres.
        total_size_bytes: Taille totale.
        downloaded_bytes: Taille téléchargée.
        speed_bytes_per_sec: Vitesse actuelle.
        estimated_time_remaining_seconds: Temps restant estimé.
        format: Format de téléchargement.
        quality: Qualité d'image.
        priority: Priorité.
        created_at: Date de création.
        started_at: Date de début.
        completed_at: Date de fin.
        error_message: Message d'erreur (si failed).
        output_path: Chemin de sortie.
    """

    id: str = Field(..., description="ID unique.")
    site_id: str = Field(..., description="ID du site.")
    manga_id: str = Field(..., description="ID du manga.")
    manga_title: str = Field(default="", description="Titre manga.")
    status: DownloadStatus = Field(..., description="Statut.")
    progress: float = Field(default=0.0, ge=0.0, le=1.0, description="Progression.")
    pages_completed: int = Field(default=0, ge=0, description="Pages téléchargées.")
    pages_total: int = Field(default=0, ge=0, description="Pages totales.")
    chapters_count: int = Field(default=0, ge=0, description="Nombre chapitres.")
    total_size_bytes: int = Field(default=0, ge=0, description="Taille totale.")
    downloaded_bytes: int = Field(default=0, ge=0, description="Taille téléchargée.")
    speed_bytes_per_sec: float = Field(default=0.0, ge=0.0, description="Vitesse.")
    estimated_time_remaining_seconds: float = Field(default=0.0, ge=0.0, description="ETA.")
    format: str = Field(default="cbz", description="Format.")
    quality: str = Field(default="original", description="Qualité.")
    priority: str = Field(default="normal", description="Priorité.")
    created_at: datetime = Field(..., description="Création.")
    started_at: datetime | None = Field(default=None, description="Début.")
    completed_at: datetime | None = Field(default=None, description="Fin.")
    error_message: str | None = Field(default=None, description="Erreur.")
    output_path: str = Field(default="", description="Chemin sortie.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class DownloadListResponse(BaseModel):
    """Réponse avec la liste des tâches.

    Attributes:
        tasks: Liste des tâches.
        total: Nombre total.
        page: Page actuelle.
        page_size: Taille de page.
        has_next: Page suivante.
        has_previous: Page précédente.
    """

    tasks: list[DownloadTaskResponse] = Field(default_factory=list, description="Tâches.")
    total: int = Field(default=0, ge=0, description="Total.")
    page: int = Field(default=1, ge=1, description="Page.")
    page_size: int = Field(default=DEFAULT_DOWNLOAD_LIMIT, ge=1, description="Taille page.")
    has_next: bool = Field(default=False, description="Page suivante.")
    has_previous: bool = Field(default=False, description="Page précédente.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class DownloadStatsResponse(BaseModel):
    """Réponse avec les statistiques globales.

    Attributes:
        total_tasks: Nombre total de tâches.
        active_tasks: Nombre de tâches actives.
        pending_tasks: Nombre de tâches en attente.
        completed_tasks: Nombre de tâches terminées.
        failed_tasks: Nombre de tâches échouées.
        total_size_bytes: Taille totale.
        downloaded_bytes: Taille téléchargée.
        average_speed_bytes_per_sec: Vitesse moyenne.
        estimated_time_remaining_seconds: Temps restant estimé.
    """

    total_tasks: int = Field(default=0, ge=0, description="Total tâches.")
    active_tasks: int = Field(default=0, ge=0, description="Tâches actives.")
    pending_tasks: int = Field(default=0, ge=0, description="Tâches en attente.")
    completed_tasks: int = Field(default=0, ge=0, description="Tâches terminées.")
    failed_tasks: int = Field(default=0, ge=0, description="Tâches échouées.")
    total_size_bytes: int = Field(default=0, ge=0, description="Taille totale.")
    downloaded_bytes: int = Field(default=0, ge=0, description="Taille téléchargée.")
    average_speed_bytes_per_sec: float = Field(default=0.0, ge=0.0, description="Vitesse moy.")
    estimated_time_remaining_seconds: float = Field(default=0.0, ge=0.0, description="ETA.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class TaskActionResponse(BaseModel):
    """Réponse après une action sur une tâche.

    Attributes:
        success: Si l'action a réussi.
        message: Message de confirmation.
        task_id: ID de la tâche.
        new_status: Nouveau statut.
    """

    success: bool = Field(..., description="Succès.")
    message: str = Field(..., description="Message.")
    task_id: str = Field(..., description="ID tâche.")
    new_status: DownloadStatus | None = Field(default=None, description="Nouveau statut.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class BulkActionResponse(BaseModel):
    """Réponse après une action globale.

    Attributes:
        success: Si l'action a réussi.
        message: Message de confirmation.
        affected_count: Nombre de tâches affectées.
    """

    success: bool = Field(..., description="Succès.")
    message: str = Field(..., description="Message.")
    affected_count: int = Field(default=0, ge=0, description="Tâches affectées.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ErrorResponse(BaseModel):
    """Réponse d'erreur.

    Attributes:
        error: Code d'erreur.
        message: Message d'erreur.
        details: Détails additionnels.
    """

    error: str = Field(..., description="Code erreur.")
    message: str = Field(..., description="Message.")
    details: dict[str, Any] = Field(default_factory=dict, description="Détails.")

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# CACHE — Cache en mémoire pour les tâches
# ============================================================================


class DownloadCache:
    """Cache en mémoire pour les tâches de téléchargement.

    Évite les appels répétés au DownloadManager.
    """

    def __init__(self, max_size: int = MAX_CACHE_SIZE, default_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS) -> None:
        """Initialise le cache.

        Args:
            max_size: Taille maximale.
            default_ttl_seconds: TTL par défaut.
        """
        self._cache: dict[str, tuple[Any, datetime]] = {}
        self._max_size = max_size
        self._default_ttl = timedelta(seconds=default_ttl_seconds)
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        """Récupère une valeur.

        Args:
            key: Clé.

        Returns:
            Valeur ou None.
        """
        async with self._lock:
            if key not in self._cache:
                return None
            value, expires_at = self._cache[key]
            if datetime.now(UTC) > expires_at:
                del self._cache[key]
                return None
            return value

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """Stocke une valeur.

        Args:
            key: Clé.
            value: Valeur.
            ttl_seconds: TTL.
        """
        async with self._lock:
            if len(self._cache) >= self._max_size:
                oldest_keys = sorted(self._cache.keys(), key=lambda k: self._cache[k][1])[:20]
                for old_key in oldest_keys:
                    del self._cache[old_key]
            ttl = timedelta(seconds=ttl_seconds) if ttl_seconds else self._default_ttl
            expires_at = datetime.now(UTC) + ttl
            self._cache[key] = (value, expires_at)

    async def delete(self, key: str) -> None:
        """Supprime une valeur.

        Args:
            key: Clé.
        """
        async with self._lock:
            self._cache.pop(key, None)

    async def invalidate_pattern(self, pattern: str) -> int:
        """Invalide toutes les entrées correspondant à un pattern.

        Args:
            pattern: Pattern (préfixe).

        Returns:
            Nombre d'entrées invalidées.
        """
        async with self._lock:
            keys_to_delete = [k for k in self._cache.keys() if k.startswith(pattern)]
            for key in keys_to_delete:
                del self._cache[key]
            return len(keys_to_delete)

    async def clear(self) -> None:
        """Vide le cache."""
        async with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        """Taille actuelle."""
        return len(self._cache)


# Instance globale du cache
_download_cache = DownloadCache()


def get_download_cache() -> DownloadCache:
    """Retourne l'instance globale du cache."""
    return _download_cache


# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================


def _get_user_id_from_request(request: Any) -> str:
    """Extrait l'ID utilisateur.

    Args:
        request: Requête HTTP.

    Returns:
        ID utilisateur ou "anonymous".
    """
    if hasattr(request.state, "user") and request.state.user:
        return request.state.user.user_id
    return "anonymous"


def _task_to_response(task: Any) -> DownloadTaskResponse:
    """Convertit une DownloadTask en DownloadTaskResponse.

    Args:
        task: Instance de DownloadTask.

    Returns:
        Instance de DownloadTaskResponse.
    """
    return DownloadTaskResponse(
        id=task.id,
        site_id=task.site_id if hasattr(task, "site_id") else "",
        manga_id=task.manga_id if hasattr(task, "manga_id") else "",
        manga_title=task.manga.title if hasattr(task, "manga") and hasattr(task.manga, "title") else "",
        status=DownloadStatus(task.status.value) if hasattr(task.status, "value") else DownloadStatus.PENDING,
        progress=task.progress if hasattr(task, "progress") else 0.0,
        pages_completed=task.pages_completed if hasattr(task, "pages_completed") else 0,
        pages_total=task.pages_total if hasattr(task, "pages_total") else 0,
        chapters_count=len(task.chapters) if hasattr(task, "chapters") and task.chapters else 0,
        total_size_bytes=task.total_size_bytes if hasattr(task, "total_size_bytes") else 0,
        downloaded_bytes=task.downloaded_bytes if hasattr(task, "downloaded_bytes") else 0,
        speed_bytes_per_sec=task.speed_bytes_per_sec if hasattr(task, "speed_bytes_per_sec") else 0.0,
        estimated_time_remaining_seconds=task.estimated_time_remaining_seconds if hasattr(task, "estimated_time_remaining_seconds") else 0.0,
        format=task.format.value if hasattr(task, "format") and hasattr(task.format, "value") else "cbz",
        quality=task.quality.value if hasattr(task, "quality") and hasattr(task.quality, "value") else "original",
        priority=task.priority.value if hasattr(task, "priority") and hasattr(task.priority, "value") else "normal",
        created_at=task.created_at if hasattr(task, "created_at") else datetime.now(UTC),
        started_at=task.started_at if hasattr(task, "started_at") else None,
        completed_at=task.completed_at if hasattr(task, "completed_at") else None,
        error_message=task.error_message if hasattr(task, "error_message") else None,
        output_path=task.output_path if hasattr(task, "output_path") else "",
    )


def _paginate_list(
    items: list[Any],
    page: int,
    page_size: int,
) -> tuple[list[Any], bool, bool]:
    """Paginer une liste.

    Args:
        items: Liste à paginer.
        page: Numéro de page.
        page_size: Taille de page.

    Returns:
        Tuple (items de la page, has_next, has_previous).
    """
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    paginated = items[start:end]
    has_next = end < total
    has_previous = page > 1
    return paginated, has_next, has_previous


async def _emit_download_event(
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Émet un événement de téléchargement.

    Args:
        event_type: Type d'événement.
        payload: Données de l'événement.
    """
    try:
        event_bus = get_event_bus()
        await event_bus.emit(
            EventType.CUSTOM,
            payload={
                "type": event_type,
                **payload,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            source="interfaces.web.download",
        )
    except Exception as e:
        logger.debug("Impossible d'émettre l'événement: {}", e)


async def _get_download_manager() -> Any:
    """Récupère le DownloadManager.

    Returns:
        Instance de DownloadManager.

    Raises:
        HTTPException: Si le manager n'est pas disponible.
    """
    try:
        from nexusdl.core.downloader import get_download_manager
        return get_download_manager()
    except Exception as e:
        logger.error("DownloadManager indisponible: {}", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ErrorResponse(
                error="manager_unavailable",
                message=t("download.error.manager_unavailable", default="DownloadManager is not available"),
                details={"reason": str(e)},
            ).model_dump(),
        ) from e


# ============================================================================
# ROUTEUR — Endpoints FastAPI
# ============================================================================


if FASTAPI_AVAILABLE:

    download_router = APIRouter(tags=["download"])

    # =========================================================================
    # GET /downloads — Lister toutes les tâches
    # =========================================================================

    @download_router.get(
        "/downloads",
        response_model=DownloadListResponse,
        summary="Lister les tâches de téléchargement",
        description="Retourne la liste des tâches de téléchargement avec filtrage et tri.",
        responses={
            200: {"description": "Liste des tâches"},
            503: {"description": "DownloadManager indisponible"},
        },
    )
    async def list_downloads(
        request: Request,
        page: int = Query(1, ge=1, description="Page"),
        page_size: int = Query(DEFAULT_DOWNLOAD_LIMIT, ge=1, le=MAX_DOWNLOAD_LIMIT, description="Taille page"),
        status_filter: DownloadFilter = Query(DownloadFilter.ALL, description="Filtre statut"),
        sort_by: DownloadSortBy = Query(DownloadSortBy.DATE_ADDED, description="Tri"),
        sort_desc: bool = Query(True, description="Tri descendant"),
    ) -> DownloadListResponse:
        """Liste les tâches de téléchargement.

        Args:
            request: Requête HTTP.
            page: Numéro de page.
            page_size: Taille de page.
            status_filter: Filtre par statut.
            sort_by: Critère de tri.
            sort_desc: Tri descendant.

        Returns:
            Liste paginée des tâches.
        """
        user_id = _get_user_id_from_request(request)
        logger.info(
            "Liste téléchargements: user={}, page={}, filter={}, sort={}",
            user_id,
            page,
            status_filter.value,
            sort_by.value,
        )

        # Vérifier le cache
        cache = get_download_cache()
        cache_key = f"downloads_list:{user_id}:{page}:{page_size}:{status_filter.value}:{sort_by.value}:{sort_desc}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        try:
            manager = await _get_download_manager()

            # Récupérer toutes les tâches
            all_tasks = await manager.get_all_tasks()

            # Filtrer par statut
            if status_filter != DownloadFilter.ALL:
                all_tasks = [t for t in all_tasks if status_filter.matches_status(DownloadStatus(t.status.value) if hasattr(t.status, "value") else DownloadStatus.PENDING)]

            # Trier
            if sort_by == DownloadSortBy.DATE_ADDED:
                all_tasks.sort(key=lambda t: getattr(t, "created_at", datetime.min.replace(tzinfo=UTC)), reverse=sort_desc)
            elif sort_by == DownloadSortBy.PROGRESS:
                all_tasks.sort(key=lambda t: getattr(t, "progress", 0.0), reverse=sort_desc)
            elif sort_by == DownloadSortBy.SIZE:
                all_tasks.sort(key=lambda t: getattr(t, "total_size_bytes", 0), reverse=sort_desc)
            elif sort_by == DownloadSortBy.NAME:
                all_tasks.sort(key=lambda t: (t.manga.title if hasattr(t, "manga") and hasattr(t.manga, "title") else "").lower(), reverse=sort_desc)
            elif sort_by == DownloadSortBy.PRIORITY:
                all_tasks.sort(key=lambda t: (t.priority.value_int if hasattr(t, "priority") and hasattr(t.priority, "value_int") else 1), reverse=sort_desc)
            elif sort_by == DownloadSortBy.STATUS:
                all_tasks.sort(key=lambda t: (t.status.value if hasattr(t.status, "value") else "pending"), reverse=sort_desc)

            # Paginer
            total = len(all_tasks)
            paginated, has_next, has_previous = _paginate_list(all_tasks, page, page_size)

            # Convertir
            task_responses = [_task_to_response(t) for t in paginated]

            response = DownloadListResponse(
                tasks=task_responses,
                total=total,
                page=page,
                page_size=page_size,
                has_next=has_next,
                has_previous=has_previous,
            )

            # Stocker dans le cache
            await cache.set(cache_key, response, ttl_seconds=10)

            return response

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de la liste des téléchargements: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="list_failed",
                    message=t("download.error.list_failed", default="Failed to list downloads"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /downloads/stats — Statistiques globales
    # =========================================================================

    @download_router.get(
        "/downloads/stats",
        response_model=DownloadStatsResponse,
        summary="Statistiques des téléchargements",
        description="Retourne les statistiques globales des téléchargements.",
        responses={
            200: {"description": "Statistiques"},
            503: {"description": "DownloadManager indisponible"},
        },
    )
    async def get_download_stats(request: Request) -> DownloadStatsResponse:
        """Récupère les statistiques des téléchargements.

        Args:
            request: Requête HTTP.

        Returns:
            Statistiques.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Statistiques téléchargements: user={}", user_id)

        # Vérifier le cache
        cache = get_download_cache()
        cache_key = f"downloads_stats:{user_id}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        try:
            manager = await _get_download_manager()
            stats = await manager.get_stats()

            response = DownloadStatsResponse(
                total_tasks=stats.total_tasks,
                active_tasks=stats.running_tasks,
                pending_tasks=stats.pending_tasks,
                completed_tasks=stats.completed_tasks,
                failed_tasks=stats.failed_tasks,
                total_size_bytes=stats.total_size_bytes,
                downloaded_bytes=stats.downloaded_bytes,
                average_speed_bytes_per_sec=stats.average_speed_bytes_per_sec,
                estimated_time_remaining_seconds=stats.estimated_time_remaining_seconds,
            )

            # Stocker dans le cache
            await cache.set(cache_key, response, ttl_seconds=5)

            return response

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de la récupération des statistiques: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="stats_failed",
                    message=t("download.error.stats_failed", default="Failed to get statistics"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /downloads/active — Tâches actives uniquement
    # =========================================================================

    @download_router.get(
        "/downloads/active",
        response_model=DownloadListResponse,
        summary="Tâches actives",
        description="Retourne uniquement les tâches en cours d'exécution.",
        responses={
            200: {"description": "Tâches actives"},
        },
    )
    async def get_active_downloads(
        request: Request,
        limit: int = Query(20, ge=1, le=100, description="Nombre max"),
    ) -> DownloadListResponse:
        """Récupère les tâches actives.

        Args:
            request: Requête HTTP.
            limit: Nombre maximum.

        Returns:
            Liste des tâches actives.
        """
        return await list_downloads(
            request=request,
            page=1,
            page_size=limit,
            status_filter=DownloadFilter.ACTIVE,
            sort_by=DownloadSortBy.PROGRESS,
            sort_desc=True,
        )

    # =========================================================================
    # GET /downloads/{task_id} — Détails d'une tâche
    # =========================================================================

    @download_router.get(
        "/downloads/{task_id}",
        response_model=DownloadTaskResponse,
        summary="Détails d'une tâche",
        description="Retourne les détails complets d'une tâche de téléchargement.",
        responses={
            200: {"description": "Détails de la tâche"},
            404: {"description": "Tâche non trouvée"},
            503: {"description": "DownloadManager indisponible"},
        },
    )
    async def get_download_task(
        request: Request,
        task_id: str,
    ) -> DownloadTaskResponse:
        """Récupère les détails d'une tâche.

        Args:
            request: Requête HTTP.
            task_id: ID de la tâche.

        Returns:
            Détails de la tâche.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Détails tâche: user={}, task={}", user_id, task_id)

        # Vérifier le cache
        cache = get_download_cache()
        cache_key = f"download_task:{task_id}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        try:
            manager = await _get_download_manager()
            task = await manager.get_task(task_id)

            if task is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ErrorResponse(
                        error="task_not_found",
                        message=t("download.error.task_not_found", default="Download task not found: {task_id}", task_id=task_id),
                        details={"task_id": task_id},
                    ).model_dump(),
                )

            response = _task_to_response(task)

            # Stocker dans le cache
            await cache.set(cache_key, response, ttl_seconds=10)

            return response

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de la récupération de la tâche {}: {}", task_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="fetch_failed",
                    message=t("download.error.fetch_failed", default="Failed to fetch task"),
                    details={"task_id": task_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /downloads — Créer une tâche
    # =========================================================================

    @download_router.post(
        "/downloads",
        response_model=DownloadTaskResponse,
        summary="Créer une tâche de téléchargement",
        description="Crée une nouvelle tâche de téléchargement.",
        responses={
            201: {"description": "Tâche créée"},
            400: {"description": "Requête invalide"},
            503: {"description": "DownloadManager indisponible"},
        },
        status_code=status.HTTP_201_CREATED,
    )
    async def create_download(
        request: Request,
        body: CreateDownloadRequest,
    ) -> DownloadTaskResponse:
        """Crée une tâche de téléchargement.

        Args:
            request: Requête HTTP.
            body: Corps de la requête.

        Returns:
            Tâche créée.
        """
        user_id = _get_user_id_from_request(request)
        logger.info(
            "Création tâche: user={}, site={}, manga={}, format={}",
            user_id,
            body.site_id,
            body.manga_id,
            body.format.value,
        )

        try:
            manager = await _get_download_manager()

            # TODO: Créer la tâche via DownloadManager
            # task = await manager.create_task(
            #     site_id=body.site_id,
            #     manga_id=body.manga_id,
            #     chapter_ids=body.chapter_ids,
            #     format=body.format.value,
            #     quality=body.quality.value,
            #     priority=body.priority.value,
            #     output_dir=body.output_dir,
            # )

            # Pour l'instant, lever une exception
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=ErrorResponse(
                    error="not_implemented",
                    message=t("download.error.not_implemented", default="Download creation not yet implemented"),
                ).model_dump(),
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de la création de la tâche: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="create_failed",
                    message=t("download.error.create_failed", default="Failed to create download task"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # DELETE /downloads/{task_id} — Supprimer une tâche
    # =========================================================================

    @download_router.delete(
        "/downloads/{task_id}",
        response_model=TaskActionResponse,
        summary="Supprimer une tâche",
        description="Supprime une tâche de téléchargement.",
        responses={
            200: {"description": "Tâche supprimée"},
            404: {"description": "Tâche non trouvée"},
            503: {"description": "DownloadManager indisponible"},
        },
    )
    async def delete_download(
        request: Request,
        task_id: str,
    ) -> TaskActionResponse:
        """Supprime une tâche.

        Args:
            request: Requête HTTP.
            task_id: ID de la tâche.

        Returns:
            Réponse de confirmation.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Suppression tâche: user={}, task={}", user_id, task_id)

        try:
            manager = await _get_download_manager()
            await manager.delete_task(task_id)

            # Invalider le cache
            cache = get_download_cache()
            await cache.delete(f"download_task:{task_id}")
            await cache.invalidate_pattern("downloads_list:")
            await cache.invalidate_pattern("downloads_stats:")

            # Émettre un événement
            await _emit_download_event(
                "download.task.deleted",
                {
                    "user_id": user_id,
                    "task_id": task_id,
                },
            )

            return TaskActionResponse(
                success=True,
                message=t("download.success.task_deleted", default="Download task deleted"),
                task_id=task_id,
                new_status=None,
            )

        except Exception as e:
            logger.error("Erreur lors de la suppression de la tâche {}: {}", task_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="delete_failed",
                    message=t("download.error.delete_failed", default="Failed to delete task"),
                    details={"task_id": task_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /downloads/{task_id}/pause — Mettre en pause
    # =========================================================================

    @download_router.post(
        "/downloads/{task_id}/pause",
        response_model=TaskActionResponse,
        summary="Mettre en pause une tâche",
        description="Met en pause une tâche de téléchargement.",
        responses={
            200: {"description": "Tâche mise en pause"},
            404: {"description": "Tâche non trouvée"},
            503: {"description": "DownloadManager indisponible"},
        },
    )
    async def pause_download(
        request: Request,
        task_id: str,
        body: TaskActionRequest | None = None,
    ) -> TaskActionResponse:
        """Met en pause une tâche.

        Args:
            request: Requête HTTP.
            task_id: ID de la tâche.
            body: Corps de la requête (optionnel).

        Returns:
            Réponse de confirmation.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Pause tâche: user={}, task={}", user_id, task_id)

        try:
            manager = await _get_download_manager()
            await manager.pause_task(task_id)

            # Invalider le cache
            cache = get_download_cache()
            await cache.delete(f"download_task:{task_id}")
            await cache.invalidate_pattern("downloads_list:")
            await cache.invalidate_pattern("downloads_stats:")

            # Émettre un événement
            await _emit_download_event(
                "download.task.paused",
                {
                    "user_id": user_id,
                    "task_id": task_id,
                    "reason": body.reason if body else None,
                },
            )

            return TaskActionResponse(
                success=True,
                message=t("download.success.task_paused", default="Download task paused"),
                task_id=task_id,
                new_status=DownloadStatus.PAUSED,
            )

        except Exception as e:
            logger.error("Erreur lors de la mise en pause de la tâche {}: {}", task_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="pause_failed",
                    message=t("download.error.pause_failed", default="Failed to pause task"),
                    details={"task_id": task_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /downloads/{task_id}/resume — Reprendre
    # =========================================================================

    @download_router.post(
        "/downloads/{task_id}/resume",
        response_model=TaskActionResponse,
        summary="Reprendre une tâche",
        description="Reprend une tâche de téléchargement en pause.",
        responses={
            200: {"description": "Tâche reprise"},
            404: {"description": "Tâche non trouvée"},
            503: {"description": "DownloadManager indisponible"},
        },
    )
    async def resume_download(
        request: Request,
        task_id: str,
    ) -> TaskActionResponse:
        """Reprend une tâche.

        Args:
            request: Requête HTTP.
            task_id: ID de la tâche.

        Returns:
            Réponse de confirmation.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Reprise tâche: user={}, task={}", user_id, task_id)

        try:
            manager = await _get_download_manager()
            await manager.resume_task(task_id)

            # Invalider le cache
            cache = get_download_cache()
            await cache.delete(f"download_task:{task_id}")
            await cache.invalidate_pattern("downloads_list:")
            await cache.invalidate_pattern("downloads_stats:")

            # Émettre un événement
            await _emit_download_event(
                "download.task.resumed",
                {
                    "user_id": user_id,
                    "task_id": task_id,
                },
            )

            return TaskActionResponse(
                success=True,
                message=t("download.success.task_resumed", default="Download task resumed"),
                task_id=task_id,
                new_status=DownloadStatus.RUNNING,
            )

        except Exception as e:
            logger.error("Erreur lors de la reprise de la tâche {}: {}", task_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="resume_failed",
                    message=t("download.error.resume_failed", default="Failed to resume task"),
                    details={"task_id": task_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /downloads/{task_id}/cancel — Annuler
    # =========================================================================

    @download_router.post(
        "/downloads/{task_id}/cancel",
        response_model=TaskActionResponse,
        summary="Annuler une tâche",
        description="Annule une tâche de téléchargement.",
        responses={
            200: {"description": "Tâche annulée"},
            404: {"description": "Tâche non trouvée"},
            503: {"description": "DownloadManager indisponible"},
        },
    )
    async def cancel_download(
        request: Request,
        task_id: str,
        body: TaskActionRequest | None = None,
    ) -> TaskActionResponse:
        """Annule une tâche.

        Args:
            request: Requête HTTP.
            task_id: ID de la tâche.
            body: Corps de la requête (optionnel).

        Returns:
            Réponse de confirmation.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Annulation tâche: user={}, task={}", user_id, task_id)

        try:
            manager = await _get_download_manager()
            await manager.cancel_task(task_id)

            # Invalider le cache
            cache = get_download_cache()
            await cache.delete(f"download_task:{task_id}")
            await cache.invalidate_pattern("downloads_list:")
            await cache.invalidate_pattern("downloads_stats:")

            # Émettre un événement
            await _emit_download_event(
                "download.task.cancelled",
                {
                    "user_id": user_id,
                    "task_id": task_id,
                    "reason": body.reason if body else None,
                },
            )

            return TaskActionResponse(
                success=True,
                message=t("download.success.task_cancelled", default="Download task cancelled"),
                task_id=task_id,
                new_status=DownloadStatus.CANCELLED,
            )

        except Exception as e:
            logger.error("Erreur lors de l'annulation de la tâche {}: {}", task_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="cancel_failed",
                    message=t("download.error.cancel_failed", default="Failed to cancel task"),
                    details={"task_id": task_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /downloads/{task_id}/retry — Réessayer
    # =========================================================================

    @download_router.post(
        "/downloads/{task_id}/retry",
        response_model=TaskActionResponse,
        summary="Réessayer une tâche",
        description="Relance une tâche de téléchargement échouée.",
        responses={
            200: {"description": "Tâche relancée"},
            404: {"description": "Tâche non trouvée"},
            503: {"description": "DownloadManager indisponible"},
        },
    )
    async def retry_download(
        request: Request,
        task_id: str,
    ) -> TaskActionResponse:
        """Relance une tâche.

        Args:
            request: Requête HTTP.
            task_id: ID de la tâche.

        Returns:
            Réponse de confirmation.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Retry tâche: user={}, task={}", user_id, task_id)

        try:
            manager = await _get_download_manager()
            await manager.retry_task(task_id)

            # Invalider le cache
            cache = get_download_cache()
            await cache.delete(f"download_task:{task_id}")
            await cache.invalidate_pattern("downloads_list:")
            await cache.invalidate_pattern("downloads_stats:")

            # Émettre un événement
            await _emit_download_event(
                "download.task.retried",
                {
                    "user_id": user_id,
                    "task_id": task_id,
                },
            )

            return TaskActionResponse(
                success=True,
                message=t("download.success.task_retried", default="Download task retried"),
                task_id=task_id,
                new_status=DownloadStatus.RUNNING,
            )

        except Exception as e:
            logger.error("Erreur lors du retry de la tâche {}: {}", task_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="retry_failed",
                    message=t("download.error.retry_failed", default="Failed to retry task"),
                    details={"task_id": task_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /downloads/pause-all — Tout mettre en pause
    # =========================================================================

    @download_router.post(
        "/downloads/pause-all",
        response_model=BulkActionResponse,
        summary="Mettre toutes les tâches en pause",
        description="Met en pause toutes les tâches actives.",
        responses={
            200: {"description": "Tâches mises en pause"},
            503: {"description": "DownloadManager indisponible"},
        },
    )
    async def pause_all_downloads(request: Request) -> BulkActionResponse:
        """Met en pause toutes les tâches.

        Args:
            request: Requête HTTP.

        Returns:
            Réponse de confirmation.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Pause toutes les tâches: user={}", user_id)

        try:
            manager = await _get_download_manager()
            count = await manager.pause_all_tasks()

            # Invalider le cache
            cache = get_download_cache()
            await cache.invalidate_pattern("downloads_list:")
            await cache.invalidate_pattern("downloads_stats:")
            await cache.invalidate_pattern("download_task:")

            # Émettre un événement
            await _emit_download_event(
                "download.tasks.paused_all",
                {
                    "user_id": user_id,
                    "count": count,
                },
            )

            return BulkActionResponse(
                success=True,
                message=t("download.success.all_paused", default="All download tasks paused"),
                affected_count=count,
            )

        except Exception as e:
            logger.error("Erreur lors de la pause de toutes les tâches: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="pause_all_failed",
                    message=t("download.error.pause_all_failed", default="Failed to pause all tasks"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /downloads/resume-all — Tout reprendre
    # =========================================================================

    @download_router.post(
        "/downloads/resume-all",
        response_model=BulkActionResponse,
        summary="Reprendre toutes les tâches",
        description="Reprend toutes les tâches en pause.",
        responses={
            200: {"description": "Tâches reprises"},
            503: {"description": "DownloadManager indisponible"},
        },
    )
    async def resume_all_downloads(request: Request) -> BulkActionResponse:
        """Reprend toutes les tâches.

        Args:
            request: Requête HTTP.

        Returns:
            Réponse de confirmation.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Reprise toutes les tâches: user={}", user_id)

        try:
            manager = await _get_download_manager()
            count = await manager.resume_all_tasks()

            # Invalider le cache
            cache = get_download_cache()
            await cache.invalidate_pattern("downloads_list:")
            await cache.invalidate_pattern("downloads_stats:")
            await cache.invalidate_pattern("download_task:")

            # Émettre un événement
            await _emit_download_event(
                "download.tasks.resumed_all",
                {
                    "user_id": user_id,
                    "count": count,
                },
            )

            return BulkActionResponse(
                success=True,
                message=t("download.success.all_resumed", default="All download tasks resumed"),
                affected_count=count,
            )

        except Exception as e:
            logger.error("Erreur lors de la reprise de toutes les tâches: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="resume_all_failed",
                    message=t("download.error.resume_all_failed", default="Failed to resume all tasks"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /downloads/clear-completed — Effacer les tâches terminées
    # =========================================================================

    @download_router.post(
        "/downloads/clear-completed",
        response_model=BulkActionResponse,
        summary="Effacer les tâches terminées",
        description="Supprime toutes les tâches terminées de la liste.",
        responses={
            200: {"description": "Tâches effacées"},
            503: {"description": "DownloadManager indisponible"},
        },
    )
    async def clear_completed_downloads(request: Request) -> BulkActionResponse:
        """Efface les tâches terminées.

        Args:
            request: Requête HTTP.

        Returns:
            Réponse de confirmation.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Effacement tâches terminées: user={}", user_id)

        try:
            manager = await _get_download_manager()
            count = await manager.clear_completed_tasks()

            # Invalider le cache
            cache = get_download_cache()
            await cache.invalidate_pattern("downloads_list:")
            await cache.invalidate_pattern("downloads_stats:")

            # Émettre un événement
            await _emit_download_event(
                "download.tasks.cleared_completed",
                {
                    "user_id": user_id,
                    "count": count,
                },
            )

            return BulkActionResponse(
                success=True,
                message=t("download.success.completed_cleared", default="Completed download tasks cleared"),
                affected_count=count,
            )

        except Exception as e:
            logger.error("Erreur lors de l'effacement des tâches terminées: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="clear_failed",
                    message=t("download.error.clear_failed", default="Failed to clear completed tasks"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_DOWNLOAD_LIMIT",
    "MAX_DOWNLOAD_LIMIT",
    "DEFAULT_CACHE_TTL_SECONDS",
    "MAX_CACHE_SIZE",
    "SUPPORTED_FORMATS",
    "SUPPORTED_QUALITIES",
    "SUPPORTED_PRIORITIES",
    # Exceptions
    "DownloadRouterError",
    "TaskNotFoundError",
    "TaskActionError",
    "DownloadManagerNotAvailableError",
    # Enums
    "DownloadStatus",
    "DownloadFilter",
    "DownloadSortBy",
    "DownloadFormat",
    "ImageQuality",
    "Priority",
    # Modèles de requête
    "CreateDownloadRequest",
    "TaskActionRequest",
    # Modèles de réponse
    "DownloadTaskResponse",
    "DownloadListResponse",
    "DownloadStatsResponse",
    "TaskActionResponse",
    "BulkActionResponse",
    "ErrorResponse",
    # Cache
    "DownloadCache",
    "get_download_cache",
    # Routeur
    "download_router" if FASTAPI_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
