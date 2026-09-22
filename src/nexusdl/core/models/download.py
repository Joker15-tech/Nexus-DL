"""Modèles de domaine pour le système de téléchargement.

Ce module définit les structures de données immuables (Pydantic v2) utilisées
pour représenter les tâches de téléchargement, leurs états, leurs résultats
et leurs statistiques. Ces modèles sont au cœur de l'orchestration des
téléchargements dans NexusDL et sont consommés par :

    - `core/downloader/manager.py` : orchestration des tâches
    - `core/downloader/worker.py` : exécution des tâches
    - `core/downloader/queue.py` : file prioritaire
    - `core/downloader/progress.py` : suivi de progression
    - `core/library/database.py` : persistance de l'historique
    - `interfaces/web/backend/schemas/` : sérialisation API REST/WebSocket
    - `interfaces/cli/screens/download.py` : affichage TUI

Architecture :
    DownloadTask (mutable — état évolutif)
        ├── Priority (enum) : LOW, NORMAL, HIGH, URGENT
        ├── DownloadStatus (enum) : PENDING → QUEUED → RUNNING → COMPLETED/FAILED/CANCELLED
        ├── PackagingFormat : CBZ, CBR, PDF, ZIP, FOLDER
        └── Manga + Chapter[] : modèles de domaine (via TYPE_CHECKING)

    DownloadResult (immutable — snapshot final)
        ├── task_id : UUID de la tâche
        ├── chapter : Chapitre traité
        ├── output_path : Chemin du fichier empaqueté
        ├── bytes_downloaded, duration_seconds, pages_failed
        └── success : bool

    DownloadProgress (immutable — snapshot temps réel)
        ├── percentage, speed, eta
        ├── pages_completed, pages_total
        └── chapters_completed, chapters_total

Règles d'or :
    1. `DownloadTask` est le SEUL modèle mutable (son statut évolue).
    2. Tous les autres modèles sont `frozen=True` (immuables, hashables).
    3. Les dépendances circulaires sont évitées via `TYPE_CHECKING`.
    4. Les enums utilisent `str` comme base pour sérialisation JSON native.
    5. Les UUID sont générés via `uuid7()` (tri chronologique) quand disponible.

Exemple d'utilisation :
    >>> from nexusdl.core.models.download import (
    ...     DownloadTask, DownloadResult, Priority, DownloadStatus,
    ... )
    >>> from nexusdl.core.models.manga import Manga, Chapter
    >>> from pathlib import Path
    >>>
    >>> task = DownloadTask(
    ...     manga=manga,
    ...     chapters=[chapter1, chapter2],
    ...     dest=Path("/downloads"),
    ...     priority=Priority.HIGH,
    ... )
    >>> print(task.status)  # DownloadStatus.PENDING
    >>> task.mark_as_running()
    >>> task.update_progress(0.5)
    >>> print(task.status)  # DownloadStatus.RUNNING
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.packaging.base import PackagingFormat

if TYPE_CHECKING:
    from nexusdl.core.models.manga import Chapter, Manga


# ============================================================================
# EXCEPTIONS
# ============================================================================


class DownloadModelError(NexusDLError):
    """Exception de base pour les erreurs liées aux modèles de téléchargement."""


class InvalidTaskStateError(DownloadModelError):
    """Exception levée lorsqu'une transition d'état est invalide."""

    def __init__(
        self,
        task_id: UUID,
        current_state: DownloadStatus,
        target_state: DownloadStatus,
    ) -> None:
        super().__init__(
            f"Transition d'état invalide pour la tâche {task_id}: "
            f"{current_state.value} → {target_state.value}"
        )
        self.task_id = task_id
        self.current_state = current_state
        self.target_state = target_state


class TaskValidationError(DownloadModelError):
    """Exception levée lorsqu'une tâche est mal configurée."""


# ============================================================================
# ENUMS
# ============================================================================


class Priority(int, Enum):
    """Priorité d'une tâche de téléchargement.

    Les valeurs entières permettent le tri naturel dans les files
    prioritaires (plus la valeur est basse, plus la priorité est haute).

    LOW      (3) : Tâche de fond, traitée en dernier.
    NORMAL   (2) : Priorité par défaut.
    HIGH     (1) : Tâche importante, passe devant les normales.
    URGENT   (0) : Tâche critique, traitée en premier.
    """

    URGENT = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3

    @property
    def label(self) -> str:
        """Libellé humain de la priorité."""
        return {
            Priority.URGENT: "Urgente",
            Priority.HIGH: "Haute",
            Priority.NORMAL: "Normale",
            Priority.LOW: "Basse",
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode pour l'affichage TUI/GUI."""
        return {
            Priority.URGENT: "🔴",
            Priority.HIGH: "🟠",
            Priority.NORMAL: "🟢",
            Priority.LOW: "🔵",
        }[self]


class DownloadStatus(str, Enum):
    """État d'une tâche de téléchargement dans son cycle de vie.

    Transitions autorisées :
        PENDING    → QUEUED, CANCELLED
        QUEUED     → RUNNING, CANCELLED, PAUSED
        RUNNING    → PAUSED, COMPLETED, FAILED, CANCELLED
        PAUSED     → QUEUED, CANCELLED
        COMPLETED  → (état terminal)
        FAILED     → QUEUED (retry), CANCELLED
        CANCELLED  → (état terminal)
    """

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """Indique si l'état est terminal (pas de transition possible sauf retry)."""
        return self in (
            DownloadStatus.COMPLETED,
            DownloadStatus.CANCELLED,
        )

    @property
    def is_active(self) -> bool:
        """Indique si la tâche est en cours d'exécution (non terminée)."""
        return self in (
            DownloadStatus.PENDING,
            DownloadStatus.QUEUED,
            DownloadStatus.RUNNING,
            DownloadStatus.PAUSED,
        )

    @property
    def is_success(self) -> bool:
        """Indique si la tâche s'est terminée avec succès."""
        return self == DownloadStatus.COMPLETED

    @property
    def is_failure(self) -> bool:
        """Indique si la tâche a échoué ou a été annulée."""
        return self in (DownloadStatus.FAILED, DownloadStatus.CANCELLED)

    @property
    def label(self) -> str:
        """Libellé humain du statut."""
        return {
            DownloadStatus.PENDING: "En attente",
            DownloadStatus.QUEUED: "En file",
            DownloadStatus.RUNNING: "En cours",
            DownloadStatus.PAUSED: "En pause",
            DownloadStatus.COMPLETED: "Terminé",
            DownloadStatus.FAILED: "Échoué",
            DownloadStatus.CANCELLED: "Annulé",
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode pour l'affichage TUI/GUI."""
        return {
            DownloadStatus.PENDING: "⏳",
            DownloadStatus.QUEUED: "📋",
            DownloadStatus.RUNNING: "⚙️",
            DownloadStatus.PAUSED: "⏸️",
            DownloadStatus.COMPLETED: "✅",
            DownloadStatus.FAILED: "❌",
            DownloadStatus.CANCELLED: "🚫",
        }[self]


# Transitions d'état autorisées (matrice de validité)
_VALID_TRANSITIONS: Final[dict[DownloadStatus, frozenset[DownloadStatus]]] = {
    DownloadStatus.PENDING: frozenset(
        {DownloadStatus.QUEUED, DownloadStatus.CANCELLED}
    ),
    DownloadStatus.QUEUED: frozenset(
        {DownloadStatus.RUNNING, DownloadStatus.CANCELLED, DownloadStatus.PAUSED}
    ),
    DownloadStatus.RUNNING: frozenset(
        {
            DownloadStatus.PAUSED,
            DownloadStatus.COMPLETED,
            DownloadStatus.FAILED,
            DownloadStatus.CANCELLED,
        }
    ),
    DownloadStatus.PAUSED: frozenset(
        {DownloadStatus.QUEUED, DownloadStatus.CANCELLED}
    ),
    DownloadStatus.COMPLETED: frozenset(),  # État terminal
    DownloadStatus.FAILED: frozenset(
        {DownloadStatus.QUEUED, DownloadStatus.CANCELLED}
    ),
    DownloadStatus.CANCELLED: frozenset(),  # État terminal
}


# ============================================================================
# HELPERS — Génération d'identifiants
# ============================================================================


def generate_task_id() -> UUID:
    """Génère un UUID v7 (tri chronologique) pour une tâche.

    UUIDv7 est préféré à UUIDv4 car il permet un tri naturel par date
    de création, ce qui est utile pour l'affichage et le debugging.
    Fallback sur UUIDv4 si uuid7 n'est pas disponible (Python < 3.12).

    Returns:
        UUID unique pour la tâche.
    """
    try:
        # Python 3.12+ supporte uuid7 nativement
        return uuid.uuid7()
    except AttributeError:
        # Fallback pour Python < 3.12
        return uuid.uuid4()


def generate_download_id() -> str:
    """Génère un identifiant court pour un téléchargement individuel.

    Format : 'dl_' + 16 caractères hex (8 bytes).
    Plus court qu'un UUID pour l'affichage TUI/GUI.

    Returns:
        Identifiant unique sous forme de chaîne.
    """
    return f"dl_{uuid.uuid4().hex[:16]}"


# ============================================================================
# MODÈLES PYDANTIC — Tâches
# ============================================================================


class DownloadTask(BaseModel):
    """Tâche de téléchargement (mutable — état évolutif).

    Représente une unité de travail complète : un manga avec une sélection
    de chapitres à télécharger vers une destination donnée, dans un format
    spécifique, avec une priorité et un état évolutif.

    Ce modèle est le SEUL modèle mutable du module car son état évolue
    au cours du cycle de vie de la tâche (PENDING → RUNNING → COMPLETED).
    Toutes les mutations passent par des méthodes explicites qui valident
    les transitions d'état via `_VALID_TRANSITIONS`.

    Attributes:
        id: UUID unique de la tâche (généré automatiquement).
        manga: Manga à télécharger (référence, pas de copie).
        chapters: Liste des chapitres à télécharger.
        dest: Répertoire de destination (créé si nécessaire).
        fmt: Format d'empaquetage (CBZ, PDF, etc.).
        priority: Priorité de la tâche (défaut: NORMAL).
        status: État courant de la tâche (défaut: PENDING).
        progress: Progression globale (0.0 à 1.0).
        error: Message d'erreur si la tâche a échoué.
        created_at: Timestamp de création.
        started_at: Timestamp de début d'exécution.
        completed_at: Timestamp de fin (succès ou échec).
        retry_count: Nombre de tentatives effectuées.
        max_retries: Nombre maximum de tentatives autorisées.
        metadata: Métadonnées additionnelles (libres).
    """

    # Identifiants
    id: UUID = Field(
        default_factory=generate_task_id,
        description="UUID unique de la tâche (v7 si possible).",
    )
    download_id: str = Field(
        default_factory=generate_download_id,
        description="Identifiant court pour l'affichage (dl_xxxx).",
    )

    # Contenu
    manga: Manga = Field(..., description="Manga à télécharger.")
    chapters: list[Chapter] = Field(
        ...,
        min_length=1,
        description="Liste des chapitres à télécharger (au moins 1).",
    )
    dest: Path = Field(..., description="Répertoire de destination.")
    fmt: PackagingFormat = Field(
        default=PackagingFormat.CBZ,
        description="Format d'empaquetage.",
    )

    # Priorité et état
    priority: Priority = Field(
        default=Priority.NORMAL,
        description="Priorité de la tâche.",
    )
    status: DownloadStatus = Field(
        default=DownloadStatus.PENDING,
        description="État courant de la tâche.",
    )
    progress: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Progression globale (0.0 à 1.0).",
    )

    # Erreurs et retries
    error: str | None = Field(
        default=None,
        description="Message d'erreur si la tâche a échoué.",
    )
    retry_count: int = Field(
        default=0,
        ge=0,
        description="Nombre de tentatives effectuées.",
    )
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Nombre maximum de tentatives autorisées.",
    )

    # Timestamps
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de création.",
    )
    started_at: datetime | None = Field(
        default=None,
        description="Timestamp de début d'exécution.",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="Timestamp de fin (succès ou échec).",
    )

    # Compteurs
    pages_completed: int = Field(default=0, ge=0)
    pages_total: int = Field(default=0, ge=0)
    chapters_completed: int = Field(default=0, ge=0)
    bytes_downloaded: int = Field(default=0, ge=0)

    # Métadonnées libres (pour extensions/plugins)
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Métadonnées additionnelles (libres).",
    )

    model_config = ConfigDict(
        arbitrary_types_allowed=True,  # Pour Manga, Chapter, Path
        validate_assignment=True,
        extra="forbid",
    )

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @field_validator("dest")
    @classmethod
    def _validate_dest(cls, v: Path) -> Path:
        """Valide le répertoire de destination."""
        if not isinstance(v, Path):
            v = Path(v)
        return v.resolve()

    @model_validator(mode="after")
    def _validate_chapters_consistency(self) -> Self:
        """Vérifie que tous les chapitres appartiennent au manga."""
        manga_id = self.manga.id
        for chapter in self.chapters:
            # Les chapitres doivent avoir un manga_id cohérent si présent
            if hasattr(chapter, "manga_id") and chapter.manga_id != manga_id:
                raise TaskValidationError(
                    f"Le chapitre {chapter.id} n'appartient pas au manga {manga_id}"
                )
        return self

    # --------------------------------------------------------------------
    # Transitions d'état
    # --------------------------------------------------------------------

    def transition_to(self, new_status: DownloadStatus) -> None:
        """Effectue une transition d'état avec validation.

        Args:
            new_status: Nouvel état cible.

        Raises:
            InvalidTaskStateError: Si la transition n'est pas autorisée.
        """
        if new_status == self.status:
            return  # Pas de transition

        allowed = _VALID_TRANSITIONS.get(self.status, frozenset())
        if new_status not in allowed:
            raise InvalidTaskStateError(self.id, self.status, new_status)

        # Mettre à jour les timestamps associés
        now = datetime.now(UTC)
        if new_status == DownloadStatus.RUNNING and self.started_at is None:
            self.started_at = now
        elif new_status in (
            DownloadStatus.COMPLETED,
            DownloadStatus.FAILED,
            DownloadStatus.CANCELLED,
        ):
            self.completed_at = now

        self.status = new_status

    def mark_as_queued(self) -> None:
        """Marque la tâche comme en file d'attente."""
        self.transition_to(DownloadStatus.QUEUED)

    def mark_as_running(self) -> None:
        """Marque la tâche comme en cours d'exécution."""
        self.transition_to(DownloadStatus.RUNNING)

    def mark_as_paused(self) -> None:
        """Met la tâche en pause."""
        self.transition_to(DownloadStatus.PAUSED)

    def mark_as_completed(self) -> None:
        """Marque la tâche comme terminée avec succès."""
        self.progress = 1.0
        self.transition_to(DownloadStatus.COMPLETED)

    def mark_as_failed(self, error: str) -> None:
        """Marque la tâche comme échouée avec un message d'erreur.

        Args:
            error: Message d'erreur descriptif.
        """
        self.error = error
        self.transition_to(DownloadStatus.FAILED)

    def mark_as_cancelled(self) -> None:
        """Annule la tâche."""
        self.transition_to(DownloadStatus.CANCELLED)

    # --------------------------------------------------------------------
    # Progression
    # --------------------------------------------------------------------

    def update_progress(self, progress: float) -> None:
        """Met à jour la progression globale.

        Args:
            progress: Nouvelle progression (0.0 à 1.0).

        Raises:
            ValueError: Si la progression est hors limites.
        """
        if not 0.0 <= progress <= 1.0:
            raise ValueError(f"Progress must be in [0.0, 1.0], got {progress}")
        self.progress = progress

    def increment_pages_completed(self, count: int = 1) -> None:
        """Incrémente le compteur de pages téléchargées.

        Args:
            count: Nombre de pages à ajouter.
        """
        self.pages_completed += count
        if self.pages_total > 0:
            self.progress = self.pages_completed / self.pages_total

    def increment_chapters_completed(self, count: int = 1) -> None:
        """Incrémente le compteur de chapitres téléchargés.

        Args:
            count: Nombre de chapitres à ajouter.
        """
        self.chapters_completed += count

    def add_bytes_downloaded(self, bytes_count: int) -> None:
        """Ajoute des bytes téléchargés au compteur.

        Args:
            bytes_count: Nombre de bytes à ajouter.
        """
        if bytes_count < 0:
            raise ValueError(f"bytes_count must be non-negative, got {bytes_count}")
        self.bytes_downloaded += bytes_count

    def increment_retry(self) -> None:
        """Incrémente le compteur de tentatives."""
        self.retry_count += 1

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        """Indique si la tâche est dans un état terminal."""
        return self.status.is_terminal

    @property
    def is_active(self) -> bool:
        """Indique si la tâche est encore active (non terminée)."""
        return self.status.is_active

    @property
    def can_retry(self) -> bool:
        """Indique si la tâche peut être relancée (retry)."""
        return (
            self.status == DownloadStatus.FAILED
            and self.retry_count < self.max_retries
        )

    @property
    def duration_seconds(self) -> float:
        """Durée d'exécution en secondes (0.0 si pas encore démarrée)."""
        if self.started_at is None:
            return 0.0
        end = self.completed_at or datetime.now(UTC)
        return (end - self.started_at).total_seconds()

    @property
    def manga_title(self) -> str:
        """Titre du manga (raccourci)."""
        return self.manga.title

    @property
    def chapters_count(self) -> int:
        """Nombre de chapitres à télécharger."""
        return len(self.chapters)

    @property
    def site_id(self) -> str:
        """Identifiant du site source."""
        return self.manga.site

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable de la tâche (pour WebSocket/API).

        Returns:
            Dictionnaire avec les champs essentiels pour l'affichage.
        """
        return {
            "id": str(self.id),
            "download_id": self.download_id,
            "manga_title": self.manga.title,
            "site": self.manga.site,
            "chapters_count": self.chapters_count,
            "priority": self.priority.value,
            "status": self.status.value,
            "progress": self.progress,
            "pages_completed": self.pages_completed,
            "pages_total": self.pages_total,
            "bytes_downloaded": self.bytes_downloaded,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "error": self.error,
        }

    def __repr__(self) -> str:
        return (
            f"<DownloadTask id={self.download_id} "
            f"manga='{self.manga.title[:30]}' "
            f"status={self.status.value} "
            f"progress={self.progress:.1%}>"
        )


# ============================================================================
# MODÈLES PYDANTIC — Résultats (immutables)
# ============================================================================


class PageDownloadResult(BaseModel):
    """Résultat du téléchargement d'une page individuelle.

    Immutable — snapshot d'un événement passé.
    """

    page_index: int = Field(..., ge=0, description="Index de la page (0-based).")
    url: str = Field(..., description="URL source de la page.")
    output_path: Path = Field(..., description="Chemin du fichier téléchargé.")
    bytes_downloaded: int = Field(..., ge=0, description="Taille en bytes.")
    duration_seconds: float = Field(..., ge=0.0, description="Durée du téléchargement.")
    sha256: str | None = Field(
        default=None,
        description="Hash SHA256 du contenu (pour déduplication).",
        pattern=r"^[a-f0-9]{64}$",
    )
    success: bool = Field(..., description="True si le téléchargement a réussi.")
    error: str | None = Field(
        default=None,
        description="Message d'erreur si échec.",
    )
    retries_count: int = Field(
        default=0,
        ge=0,
        description="Nombre de tentatives effectuées.",
    )
    was_deduplicated: bool = Field(
        default=False,
        description="True si la page a été évitée grâce à la déduplication.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class DownloadResult(BaseModel):
    """Résultat final du téléchargement d'un chapitre (immutable).

    Représente un snapshot immuable du résultat d'une opération de
    téléchargement. Utilisé pour la persistance en BDD et l'affichage
    dans les interfaces.

    Attributes:
        task_id: UUID de la tâche parente.
        chapter: Chapitre traité.
        output_path: Chemin du fichier empaqueté final.
        bytes_downloaded: Taille totale téléchargée.
        duration_seconds: Durée totale du téléchargement.
        pages_failed: Index des pages ayant échoué.
        pages_deduplicated: Nombre de pages évitées (déduplication).
        success: True si le téléchargement a réussi.
        page_results: Détails par page (optionnel, pour debug).
    """

    task_id: UUID = Field(..., description="UUID de la tâche parente.")
    chapter: Chapter = Field(..., description="Chapitre traité.")
    output_path: Path = Field(..., description="Chemin du fichier empaqueté final.")
    bytes_downloaded: int = Field(
        ...,
        ge=0,
        description="Taille totale téléchargée en bytes.",
    )
    duration_seconds: float = Field(
        ...,
        ge=0.0,
        description="Durée totale du téléchargement en secondes.",
    )
    pages_failed: list[int] = Field(
        default_factory=list,
        description="Index des pages ayant échoué (0-based).",
    )
    pages_deduplicated: int = Field(
        default=0,
        ge=0,
        description="Nombre de pages évitées grâce à la déduplication.",
    )
    success: bool = Field(..., description="True si le téléchargement a réussi.")
    page_results: list[PageDownloadResult] = Field(
        default_factory=list,
        description="Détails par page (optionnel, pour debug).",
    )
    error: str | None = Field(
        default=None,
        description="Message d'erreur global si échec.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de création du résultat.",
    )

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    @property
    def pages_count(self) -> int:
        """Nombre total de pages traitées (succès + échec + dédup)."""
        return len(self.page_results)

    @property
    def pages_success_count(self) -> int:
        """Nombre de pages téléchargées avec succès."""
        return sum(1 for p in self.page_results if p.success and not p.was_deduplicated)

    @property
    def average_speed_bytes_per_sec(self) -> float:
        """Vitesse moyenne de téléchargement en bytes/seconde."""
        if self.duration_seconds <= 0:
            return 0.0
        return self.bytes_downloaded / self.duration_seconds

    @property
    def has_failures(self) -> bool:
        """Indique si des pages ont échoué."""
        return len(self.pages_failed) > 0

    @property
    def failure_rate(self) -> float:
        """Taux d'échec (0.0 à 1.0)."""
        total = self.pages_count
        if total == 0:
            return 0.0
        return len(self.pages_failed) / total

    def __repr__(self) -> str:
        status = "✅" if self.success else "❌"
        return (
            f"<DownloadResult {status} chapter={self.chapter.number} "
            f"pages={self.pages_success_count}/{self.pages_count} "
            f"size={self.bytes_downloaded}>"
        )


# ============================================================================
# MODÈLES PYDANTIC — Progression (snapshots immuables)
# ============================================================================


class DownloadProgress(BaseModel):
    """Snapshot immuable de la progression d'une tâche.

    Émis périodiquement via l'EventBus pour alimenter les interfaces
    en temps réel (WebSocket, TUI, GUI).
    """

    task_id: UUID = Field(..., description="UUID de la tâche.")
    status: DownloadStatus = Field(..., description="État courant.")
    progress: float = Field(..., ge=0.0, le=1.0, description="Progression globale.")

    # Compteurs
    pages_completed: int = Field(default=0, ge=0)
    pages_total: int = Field(default=0, ge=0)
    chapters_completed: int = Field(default=0, ge=0)
    chapters_total: int = Field(default=0, ge=0)
    bytes_downloaded: int = Field(default=0, ge=0)
    bytes_total: int = Field(default=0, ge=0)

    # Performance
    speed_bytes_per_sec: float = Field(default=0.0, ge=0.0)
    eta_seconds: float = Field(default=0.0, ge=0.0)
    elapsed_seconds: float = Field(default=0.0, ge=0.0)

    # Contexte
    manga_title: str = Field(default="", description="Titre du manga.")
    current_chapter: str | None = Field(
        default=None,
        description="Numéro du chapitre en cours.",
    )
    error: str | None = Field(default=None, description="Message d'erreur.")

    # Timestamp
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp du snapshot.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def pages_percentage(self) -> float:
        """Pourcentage de pages téléchargées."""
        if self.pages_total == 0:
            return 0.0
        return self.pages_completed / self.pages_total

    @property
    def chapters_percentage(self) -> float:
        """Pourcentage de chapitres téléchargés."""
        if self.chapters_total == 0:
            return 0.0
        return self.chapters_completed / self.chapters_total

    @property
    def bytes_percentage(self) -> float:
        """Pourcentage de bytes téléchargés."""
        if self.bytes_total == 0:
            return 0.0
        return self.bytes_downloaded / self.bytes_total

    @property
    def speed_human(self) -> str:
        """Vitesse formatée (ex: '2.3 MB/s')."""
        from nexusdl.core.downloader.progress import format_speed
        return format_speed(self.speed_bytes_per_sec)

    @property
    def eta_human(self) -> str:
        """ETA formaté (ex: '3m 45s')."""
        from nexusdl.core.downloader.progress import format_eta
        return format_eta(self.eta_seconds)

    def __repr__(self) -> str:
        return (
            f"<DownloadProgress task={self.task_id} "
            f"status={self.status.value} "
            f"progress={self.progress:.1%}>"
        )


# ============================================================================
# MODÈLES PYDANTIC — Statistiques
# ============================================================================


class DownloadStats(BaseModel):
    """Statistiques agrégées du système de téléchargement.

    Immutable — snapshot à un instant T.
    """

    total_tasks: int = Field(default=0, ge=0)
    pending_tasks: int = Field(default=0, ge=0)
    queued_tasks: int = Field(default=0, ge=0)
    running_tasks: int = Field(default=0, ge=0)
    paused_tasks: int = Field(default=0, ge=0)
    completed_tasks: int = Field(default=0, ge=0)
    failed_tasks: int = Field(default=0, ge=0)
    cancelled_tasks: int = Field(default=0, ge=0)

    total_bytes_downloaded: int = Field(default=0, ge=0)
    total_pages_downloaded: int = Field(default=0, ge=0)
    total_pages_failed: int = Field(default=0, ge=0)
    total_pages_deduplicated: int = Field(default=0, ge=0)

    average_speed_bytes_per_sec: float = Field(default=0.0, ge=0.0)
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def active_tasks(self) -> int:
        """Nombre de tâches actives (non terminées)."""
        return (
            self.pending_tasks
            + self.queued_tasks
            + self.running_tasks
            + self.paused_tasks
        )

    @property
    def success_rate(self) -> float:
        """Taux de succès des tâches terminées (0.0 à 1.0)."""
        finished = self.completed_tasks + self.failed_tasks + self.cancelled_tasks
        if finished == 0:
            return 0.0
        return self.completed_tasks / finished

    @property
    def page_success_rate(self) -> float:
        """Taux de succès des pages téléchargées (0.0 à 1.0)."""
        total = self.total_pages_downloaded + self.total_pages_failed
        if total == 0:
            return 0.0
        return self.total_pages_downloaded / total

    @property
    def deduplication_savings_percent(self) -> float:
        """Pourcentage de pages économisées par déduplication."""
        total = self.total_pages_downloaded + self.total_pages_deduplicated
        if total == 0:
            return 0.0
        return (self.total_pages_deduplicated / total) * 100.0


# ============================================================================
# MODÈLES PYDANTIC — File d'attente
# ============================================================================


class QueuePosition(BaseModel):
    """Position d'une tâche dans la file d'attente.

    Utilisé par l'interface pour afficher "Position 3/12 dans la file".
    """

    task_id: UUID = Field(..., description="UUID de la tâche.")
    position: int = Field(..., ge=1, description="Position dans la file (1-based).")
    total_queued: int = Field(..., ge=0, description="Nombre total de tâches en file.")
    estimated_wait_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Temps d'attente estimé avant exécution.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def position_label(self) -> str:
        """Libellé de position (ex: '3/12')."""
        return f"{self.position}/{self.total_queued}"


# ============================================================================
# MODÈLES PYDANTIC — Historique
# ============================================================================


class DownloadHistoryEntry(BaseModel):
    """Entrée d'historique de téléchargement (pour persistance BDD).

    Représente un enregistrement dans la table `download_history`.
    """

    id: UUID = Field(..., description="UUID de la tâche originale.")
    manga_id: str | None = Field(default=None, description="ID du manga (nullable).")
    chapter_id: str | None = Field(default=None, description="ID du chapitre (nullable).")
    manga_title: str = Field(default="", description="Titre du manga (dénormalisé).")
    chapter_number: str = Field(default="", description="Numéro du chapitre (dénormalisé).")
    site: str = Field(default="", description="Site source.")
    status: DownloadStatus = Field(..., description="Statut final.")
    bytes_downloaded: int = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0.0, ge=0.0)
    error_message: str | None = Field(default=None)
    completed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de complétion.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "DownloadModelError",
    "InvalidTaskStateError",
    "TaskValidationError",
    # Enums
    "Priority",
    "DownloadStatus",
    # Modèles — Tâches
    "DownloadTask",
    # Modèles — Résultats
    "DownloadResult",
    "PageDownloadResult",
    "DownloadProgress",
    # Modèles — Statistiques
    "DownloadStats",
    # Modèles — File d'attente
    "QueuePosition",
    # Modèles — Historique
    "DownloadHistoryEntry",
    # Helpers
    "generate_task_id",
    "generate_download_id",
    # Constantes
    "_VALID_TRANSITIONS",
]
