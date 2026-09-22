"""Modèles de domaine pour la bibliothèque locale NexusDL.

Ce module définit les structures de données immuables (Pydantic v2) utilisées
pour représenter la progression de lecture, les listes personnalisées, les
sessions de lecture et les statistiques agrégées. Ces modèles sont au cœur
de la gestion utilisateur de la bibliothèque et sont consommés par :

    - `core/library/database.py` : persistance SQLite (CRUD)
    - `core/library/search.py` : requêtes sur les vues pré-définies
    - `core/library/scanner.py` : synchronisation filesystem ↔ BDD
    - `interfaces/web/backend/routers/library.py` : API REST
    - `interfaces/web/backend/websocket.py` : événements temps réel
    - `interfaces/cli/screens/library.py` : affichage TUI
    - `interfaces/gui/views/library_view.py` : affichage GUI

Architecture :
    ReadingProgress (mutable — état évolutif par manga)
        ├── ReadingStatus (enum) : READING, COMPLETED, ON_HOLD, DROPPED, PLAN_TO_READ, RE_READING
        ├── score : float (0-10)
        ├── notes : str
        └── reread_count : int

    ReadingList (immutable — liste personnalisée)
        ├── name, description, icon
        ├── is_default, is_public
        └── sort_order : int

    ReadingListManga (immutable — association liste ↔ manga)
        ├── list_id, manga_id
        ├── position : int (tri manuel)
        └── notes : str

    ReadingSession (immutable — session de lecture individuelle)
        ├── manga_id, chapter_id
        ├── start_page, end_page, pages_read
        ├── duration_seconds
        └── started_at, ended_at

    LibraryStats (immutable — statistiques agrégées)
        ├── compteurs par statut de lecture
        ├── total_manga, total_chapters, total_pages
        ├── total_reading_time
        └── average_score

Règles d'or :
    1. `ReadingProgress` est le SEUL modèle mutable (son statut évolue).
    2. Tous les autres modèles sont `frozen=True` (immuables, hashables).
    3. Les dépendances circulaires sont évitées via `TYPE_CHECKING`.
    4. Les enums utilisent `str` comme base pour sérialisation JSON native.
    5. Les IDs sont des chaînes (UUID ou slugs) pour compatibilité SQLite TEXT.
    6. Les timestamps sont en UTC et sérialisables en ISO 8601.

Exemple d'utilisation :
    >>> from nexusdl.core.models.library import (
    ...     ReadingProgress, ReadingList, ReadingSession, ReadingStatus,
    ... )
    >>> from datetime import datetime, UTC
    >>>
    >>> # Créer une progression de lecture
    >>> progress = ReadingProgress(
    ...     manga_id="manga_one_piece",
    ...     chapter_id="ch_001",
    ...     page=42,
    ...     reading_status=ReadingStatus.READING,
    ...     score=9.5,
    ... )
    >>> print(progress.reading_status.label)  # "En cours de lecture"
    >>> print(progress.is_active)  # True
    >>>
    >>> # Créer une liste de lecture
    >>> favorites = ReadingList(
    ...     id="list_favorites",
    ...     name="Favoris",
    ...     description="Mes mangas préférés",
    ...     icon="heart",
    ...     is_default=True,
    ... )
    >>>
    >>> # Enregistrer une session de lecture
    >>> session = ReadingSession(
    ...     id="sess_abc123",
    ...     manga_id="manga_one_piece",
    ...     chapter_id="ch_001",
    ...     start_page=1,
    ...     end_page=42,
    ...     pages_read=42,
    ...     duration_seconds=1800.0,  # 30 minutes
    ... )
    >>> print(session.reading_speed_pages_per_hour)  # 84.0
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Final, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nexusdl.core.exceptions import NexusDLError

if TYPE_CHECKING:
    pass  # Pas de dépendances circulaires nécessaires pour ce module


# ============================================================================
# EXCEPTIONS
# ============================================================================


class LibraryModelError(NexusDLError):
    """Exception de base pour les erreurs liées aux modèles de bibliothèque."""


class InvalidReadingStatusError(LibraryModelError):
    """Exception levée lorsqu'une transition de statut de lecture est invalide."""

    def __init__(
        self,
        manga_id: str,
        current_status: ReadingStatus,
        target_status: ReadingStatus,
    ) -> None:
        super().__init__(
            f"Transition de statut invalide pour le manga {manga_id}: "
            f"{current_status.value} → {target_status.value}"
        )
        self.manga_id = manga_id
        self.current_status = current_status
        self.target_status = target_status


class ListValidationError(LibraryModelError):
    """Exception levée lorsqu'une liste de lecture est mal configurée."""


# ============================================================================
# ENUMS
# ============================================================================


class ReadingStatus(str, Enum):
    """Statut de lecture d'un manga dans la bibliothèque utilisateur.

    Inspiré de MAL/Anilist/Kitsu pour une expérience familière.

    Transitions autorisées :
        PLAN_TO_READ → READING, DROPPED
        READING      → COMPLETED, ON_HOLD, DROPPED, RE_READING
        ON_HOLD      → READING, DROPPED
        DROPPED      → PLAN_TO_READ, READING
        COMPLETED    → RE_READING, DROPPED
        RE_READING   → COMPLETED, DROPPED
    """

    READING = "reading"
    COMPLETED = "completed"
    ON_HOLD = "on_hold"
    DROPPED = "dropped"
    PLAN_TO_READ = "plan_to_read"
    RE_READING = "re_reading"

    @property
    def label(self) -> str:
        """Libellé humain du statut."""
        return {
            ReadingStatus.READING: "En cours de lecture",
            ReadingStatus.COMPLETED: "Terminé",
            ReadingStatus.ON_HOLD: "En pause",
            ReadingStatus.DROPPED: "Abandonné",
            ReadingStatus.PLAN_TO_READ: "À lire",
            ReadingStatus.RE_READING: "Relecture",
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode pour l'affichage TUI/GUI."""
        return {
            ReadingStatus.READING: "📖",
            ReadingStatus.COMPLETED: "✅",
            ReadingStatus.ON_HOLD: "⏸️",
            ReadingStatus.DROPPED: "❌",
            ReadingStatus.PLAN_TO_READ: "📋",
            ReadingStatus.RE_READING: "🔄",
        }[self]

    @property
    def color(self) -> str:
        """Couleur hexadécimale pour l'affichage (interfaces web/GUI)."""
        return {
            ReadingStatus.READING: "#3b82f6",  # blue-500
            ReadingStatus.COMPLETED: "#10b981",  # emerald-500
            ReadingStatus.ON_HOLD: "#f59e0b",  # amber-500
            ReadingStatus.DROPPED: "#ef4444",  # red-500
            ReadingStatus.PLAN_TO_READ: "#8b5cf6",  # violet-500
            ReadingStatus.RE_READING: "#06b6d4",  # cyan-500
        }[self]

    @property
    def is_active(self) -> bool:
        """Indique si le statut représente une lecture active."""
        return self in (ReadingStatus.READING, ReadingStatus.RE_READING)

    @property
    def is_terminal(self) -> bool:
        """Indique si le statut est terminal (lecture terminée ou abandonnée)."""
        return self in (ReadingStatus.COMPLETED, ReadingStatus.DROPPED)


# Transitions d'état autorisées pour ReadingStatus
_VALID_STATUS_TRANSITIONS: Final[dict[ReadingStatus, frozenset[ReadingStatus]]] = {
    ReadingStatus.PLAN_TO_READ: frozenset(
        {ReadingStatus.READING, ReadingStatus.DROPPED}
    ),
    ReadingStatus.READING: frozenset(
        {
            ReadingStatus.COMPLETED,
            ReadingStatus.ON_HOLD,
            ReadingStatus.DROPPED,
            ReadingStatus.RE_READING,
        }
    ),
    ReadingStatus.ON_HOLD: frozenset(
        {ReadingStatus.READING, ReadingStatus.DROPPED}
    ),
    ReadingStatus.DROPPED: frozenset(
        {ReadingStatus.PLAN_TO_READ, ReadingStatus.READING}
    ),
    ReadingStatus.COMPLETED: frozenset(
        {ReadingStatus.RE_READING, ReadingStatus.DROPPED}
    ),
    ReadingStatus.RE_READING: frozenset(
        {ReadingStatus.COMPLETED, ReadingStatus.DROPPED}
    ),
}


class ListVisibility(str, Enum):
    """Visibilité d'une liste de lecture.

    PRIVATE : visible uniquement par le propriétaire.
    PUBLIC  : visible par tous (pour partage futur).
    """

    PRIVATE = "private"
    PUBLIC = "public"


class SortOrder(str, Enum):
    """Ordre de tri pour les listes et requêtes."""

    ASC = "asc"
    DESC = "desc"


# ============================================================================
# HELPERS — Génération d'identifiants
# ============================================================================


def generate_reading_list_id(name: str) -> str:
    """Génère un ID slugifié pour une liste de lecture.

    Format : 'list_' + slug du nom + '_' + 8 caractères hex.

    Args:
        name: Nom de la liste (sera slugifié).

    Returns:
        Identifiant unique sous forme de chaîne.

    Example:
        >>> generate_reading_list_id("Mes Favoris")
        'list_mes_favoris_a1b2c3d4'
    """
    # Slugifier le nom (simplifié — en prod, utiliser python-slugify)
    slug = (
        name.lower()
        .strip()
        .replace(" ", "_")
        .replace("'", "")
        .replace('"', "")
    )
    # Garder uniquement les caractères alphanumériques et underscores
    slug = "".join(c for c in slug if c.isalnum() or c == "_")
    # Limiter la longueur
    slug = slug[:40]
    # Ajouter un suffixe unique
    suffix = uuid.uuid4().hex[:8]
    return f"list_{slug}_{suffix}"


def generate_session_id() -> str:
    """Génère un ID unique pour une session de lecture.

    Format : 'sess_' + 16 caractères hex.

    Returns:
        Identifiant unique sous forme de chaîne.
    """
    return f"sess_{uuid.uuid4().hex[:16]}"


# ============================================================================
# MODÈLES PYDANTIC — Progression de lecture
# ============================================================================


class ReadingProgress(BaseModel):
    """Progression de lecture d'un manga (mutable — état évolutif).

    Représente l'état de lecture actuel d'un manga dans la bibliothèque
    de l'utilisateur. Une seule entrée par manga (le chapitre et la page
    les plus avancés).

    Ce modèle est le SEUL modèle mutable du module car son statut évolue
    au cours du temps (PLAN_TO_READ → READING → COMPLETED). Toutes les
    mutations passent par des méthodes explicites qui valident les
    transitions d'état via `_VALID_STATUS_TRANSITIONS`.

    Attributes:
        manga_id: ID du manga (référence vers `manga.id`).
        chapter_id: ID du chapitre courant (le plus avancé).
        page: Numéro de page courant (1-based, 0 = non commencé).
        reading_status: Statut de lecture actuel.
        score: Note utilisateur (0.0 à 10.0, None = non noté).
        notes: Notes personnelles (markdown supporté).
        reread_count: Nombre de relectures effectuées.
        started_at: Timestamp de début de lecture (premier passage à READING).
        completed_at: Timestamp de complétion (passage à COMPLETED).
        last_read_at: Timestamp de la dernière activité de lecture.
        updated_at: Timestamp de dernière mise à jour (auto-géré par trigger SQL).
    """

    # Identifiants
    manga_id: str = Field(..., description="ID du manga (référence FK).")
    chapter_id: str = Field(..., description="ID du chapitre courant.")
    page: int = Field(
        default=1,
        ge=0,
        description="Numéro de page courant (1-based, 0 = non commencé).",
    )

    # Statut et métadonnées utilisateur
    reading_status: ReadingStatus = Field(
        default=ReadingStatus.PLAN_TO_READ,
        description="Statut de lecture actuel.",
    )
    score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="Note utilisateur (0.0 à 10.0, None = non noté).",
    )
    notes: str | None = Field(
        default=None,
        max_length=5000,
        description="Notes personnelles (markdown supporté).",
    )
    reread_count: int = Field(
        default=0,
        ge=0,
        description="Nombre de relectures effectuées.",
    )

    # Timestamps
    started_at: datetime | None = Field(
        default=None,
        description="Timestamp de début de lecture.",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="Timestamp de complétion.",
    )
    last_read_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de la dernière activité de lecture.",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de dernière mise à jour.",
    )

    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
    )

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @field_validator("notes")
    @classmethod
    def _validate_notes(cls, v: str | None) -> str | None:
        """Valide les notes (trim + limite de longueur)."""
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        return v

    @model_validator(mode="after")
    def _validate_timestamps_consistency(self) -> Self:
        """Vérifie la cohérence des timestamps."""
        # Si completed_at est défini, started_at doit l'être aussi
        if self.completed_at is not None and self.started_at is None:
            # Auto-corriger : définir started_at = completed_at
            self.started_at = self.completed_at

        # started_at ne peut pas être après completed_at
        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.started_at > self.completed_at
        ):
            raise LibraryModelError(
                f"started_at ({self.started_at}) ne peut pas être après "
                f"completed_at ({self.completed_at}) pour le manga {self.manga_id}"
            )

        return self

    # --------------------------------------------------------------------
    # Transitions d'état
    # --------------------------------------------------------------------

    def transition_to(self, new_status: ReadingStatus) -> None:
        """Effectue une transition de statut avec validation.

        Met à jour automatiquement les timestamps associés :
            - READING (depuis PLAN_TO_READ) : définit started_at
            - COMPLETED : définit completed_at
            - RE_READING (depuis COMPLETED) : incrémente reread_count, efface completed_at

        Args:
            new_status: Nouveau statut cible.

        Raises:
            InvalidReadingStatusError: Si la transition n'est pas autorisée.
        """
        if new_status == self.reading_status:
            return  # Pas de transition

        allowed = _VALID_STATUS_TRANSITIONS.get(self.reading_status, frozenset())
        if new_status not in allowed:
            raise InvalidReadingStatusError(self.manga_id, self.reading_status, new_status)

        now = datetime.now(UTC)

        # Actions spécifiques selon la transition
        if new_status == ReadingStatus.READING and self.reading_status == ReadingStatus.PLAN_TO_READ:
            # Premier démarrage de lecture
            if self.started_at is None:
                self.started_at = now

        elif new_status == ReadingStatus.COMPLETED:
            self.completed_at = now

        elif new_status == ReadingStatus.RE_READING and self.reading_status == ReadingStatus.COMPLETED:
            # Nouvelle relecture
            self.reread_count += 1
            self.completed_at = None
            self.started_at = now

        elif new_status == ReadingStatus.DROPPED:
            # Abandon : effacer completed_at si présent
            self.completed_at = None

        self.reading_status = new_status
        self.last_read_at = now
        self.updated_at = now

    def mark_as_reading(self) -> None:
        """Marque le manga comme en cours de lecture."""
        self.transition_to(ReadingStatus.READING)

    def mark_as_completed(self) -> None:
        """Marque le manga comme terminé."""
        self.transition_to(ReadingStatus.COMPLETED)

    def mark_as_on_hold(self) -> None:
        """Met le manga en pause."""
        self.transition_to(ReadingStatus.ON_HOLD)

    def mark_as_dropped(self) -> None:
        """Marque le manga comme abandonné."""
        self.transition_to(ReadingStatus.DROPPED)

    def mark_as_plan_to_read(self) -> None:
        """Ajoute le manga à la liste 'À lire'."""
        self.transition_to(ReadingStatus.PLAN_TO_READ)

    def mark_as_re_reading(self) -> None:
        """Démarre une relecture du manga."""
        self.transition_to(ReadingStatus.RE_READING)

    # --------------------------------------------------------------------
    # Progression
    # --------------------------------------------------------------------

    def update_progress(self, chapter_id: str, page: int) -> None:
        """Met à jour la progression (chapitre et page).

        Ne met à jour que si la nouvelle progression est strictement
        supérieure à l'actuelle (pas de retour en arrière).

        Args:
            chapter_id: ID du nouveau chapitre.
            page: Numéro de page (1-based).

        Raises:
            ValueError: Si page < 0.
        """
        if page < 0:
            raise ValueError(f"page must be non-negative, got {page}")

        # Mettre à jour seulement si progression > actuelle
        # (comparaison simple : si chapitre différent, on considère que c'est un progrès)
        if chapter_id != self.chapter_id or page > self.page:
            self.chapter_id = chapter_id
            self.page = page
            self.last_read_at = datetime.now(UTC)
            self.updated_at = datetime.now(UTC)

    def set_score(self, score: float | None) -> None:
        """Définit la note utilisateur.

        Args:
            score: Note (0.0 à 10.0) ou None pour retirer.

        Raises:
            ValueError: Si score est hors limites.
        """
        if score is not None and not 0.0 <= score <= 10.0:
            raise ValueError(f"score must be in [0.0, 10.0], got {score}")
        self.score = score
        self.updated_at = datetime.now(UTC)

    def set_notes(self, notes: str | None) -> None:
        """Définit les notes personnelles.

        Args:
            notes: Notes (markdown) ou None pour effacer.
        """
        self.notes = notes.strip() if notes else None
        self.updated_at = datetime.now(UTC)

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """Indique si le manga est en cours de lecture active."""
        return self.reading_status.is_active

    @property
    def is_terminal(self) -> bool:
        """Indique si le manga est dans un état terminal."""
        return self.reading_status.is_terminal

    @property
    def is_completed(self) -> bool:
        """Indique si le manga est terminé."""
        return self.reading_status == ReadingStatus.COMPLETED

    @property
    def is_reading(self) -> bool:
        """Indique si le manga est en cours de lecture."""
        return self.reading_status == ReadingStatus.READING

    @property
    def is_in_wishlist(self) -> bool:
        """Indique si le manga est dans la wishlist (À lire)."""
        return self.reading_status == ReadingStatus.PLAN_TO_READ

    @property
    def reading_duration_days(self) -> float | None:
        """Durée de lecture en jours (depuis started_at jusqu'à maintenant ou completed_at).

        Returns:
            Durée en jours, ou None si started_at n'est pas défini.
        """
        if self.started_at is None:
            return None
        end = self.completed_at or datetime.now(UTC)
        return (end - self.started_at).total_seconds() / 86_400.0

    @property
    def score_stars(self) -> str:
        """Représentation visuelle de la note en étoiles (0 à 5 étoiles).

        Returns:
            Chaîne d'étoiles (ex: '★★★★☆' pour 8/10).
        """
        if self.score is None:
            return "☆☆☆☆☆"
        # Convertir 0-10 → 0-5
        stars = round(self.score / 2.0)
        return "★" * stars + "☆" * (5 - stars)

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable (pour WebSocket/API).

        Returns:
            Dictionnaire avec les champs essentiels.
        """
        return {
            "manga_id": self.manga_id,
            "chapter_id": self.chapter_id,
            "page": self.page,
            "reading_status": self.reading_status.value,
            "reading_status_label": self.reading_status.label,
            "reading_status_icon": self.reading_status.icon,
            "score": self.score,
            "score_stars": self.score_stars,
            "reread_count": self.reread_count,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "last_read_at": self.last_read_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "is_active": self.is_active,
            "is_completed": self.is_completed,
        }

    def __repr__(self) -> str:
        return (
            f"<ReadingProgress manga={self.manga_id} "
            f"status={self.reading_status.value} "
            f"chapter={self.chapter_id} page={self.page}>"
        )


# ============================================================================
# MODÈLES PYDANTIC — Listes de lecture
# ============================================================================


class ReadingList(BaseModel):
    """Liste de lecture personnalisée (immutable).

    Représente une collection nommée de mangas créée par l'utilisateur
    (ex: "Favoris", "À lire cet été", "Classiques du seinen").

    Attributes:
        id: Identifiant unique de la liste (slug + suffixe aléatoire).
        name: Nom de la liste (unique).
        description: Description optionnelle (markdown).
        icon: Nom d'icône (ex: "heart", "star", "book").
        is_default: True si c'est une liste système (non supprimable).
        visibility: Visibilité (PRIVATE ou PUBLIC).
        sort_order: Ordre de tri dans l'interface.
        created_at: Timestamp de création.
        updated_at: Timestamp de dernière mise à jour.
    """

    id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Identifiant unique de la liste.",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Nom de la liste (unique).",
    )
    description: str | None = Field(
        default=None,
        max_length=500,
        description="Description optionnelle (markdown).",
    )
    icon: str | None = Field(
        default=None,
        max_length=50,
        description="Nom d'icône (ex: 'heart', 'star', 'book').",
    )
    is_default: bool = Field(
        default=False,
        description="True si c'est une liste système (non supprimable).",
    )
    visibility: ListVisibility = Field(
        default=ListVisibility.PRIVATE,
        description="Visibilité de la liste.",
    )
    sort_order: int = Field(
        default=0,
        description="Ordre de tri dans l'interface.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de création.",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de dernière mise à jour.",
    )

    # Champ dénormalisé (peuplé par la vue SQL v_reading_lists_with_count)
    manga_count: int = Field(
        default=0,
        ge=0,
        description="Nombre de mangas dans la liste (dénormalisé depuis la vue).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        """Valide le nom de la liste."""
        v = v.strip()
        if not v:
            raise ListValidationError("Le nom de la liste ne peut pas être vide")
        return v

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str | None) -> str | None:
        """Valide la description."""
        if v is None:
            return None
        v = v.strip()
        return v if v else None

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def is_system_list(self) -> bool:
        """Indique si c'est une liste système (non supprimable)."""
        return self.is_default

    @property
    def is_private(self) -> bool:
        """Indique si la liste est privée."""
        return self.visibility == ListVisibility.PRIVATE

    @property
    def is_public(self) -> bool:
        """Indique si la liste est publique."""
        return self.visibility == ListVisibility.PUBLIC

    @property
    def has_manga(self) -> bool:
        """Indique si la liste contient au moins un manga."""
        return self.manga_count > 0

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "is_default": self.is_default,
            "visibility": self.visibility.value,
            "sort_order": self.sort_order,
            "manga_count": self.manga_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def __repr__(self) -> str:
        return (
            f"<ReadingList id={self.id} name='{self.name}' "
            f"manga_count={self.manga_count}>"
        )


class ReadingListManga(BaseModel):
    """Association entre une liste de lecture et un manga (immutable).

    Représente une entrée dans la table de jointure `reading_list_manga`.
    Permet à un manga d'appartenir à plusieurs listes simultanément.

    Attributes:
        list_id: ID de la liste parente.
        manga_id: ID du manga.
        added_at: Timestamp d'ajout à la liste.
        position: Position dans la liste (pour tri manuel, 0 = non trié).
        notes: Notes spécifiques à cette association (optionnel).
    """

    list_id: str = Field(..., description="ID de la liste parente.")
    manga_id: str = Field(..., description="ID du manga.")
    added_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp d'ajout à la liste.",
    )
    position: int = Field(
        default=0,
        ge=0,
        description="Position dans la liste (pour tri manuel, 0 = non trié).",
    )
    notes: str | None = Field(
        default=None,
        max_length=1000,
        description="Notes spécifiques à cette association.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("notes")
    @classmethod
    def _validate_notes(cls, v: str | None) -> str | None:
        """Valide les notes."""
        if v is None:
            return None
        v = v.strip()
        return v if v else None

    def __repr__(self) -> str:
        return (
            f"<ReadingListManga list={self.list_id} manga={self.manga_id} "
            f"position={self.position}>"
        )


# ============================================================================
# MODÈLES PYDANTIC — Sessions de lecture
# ============================================================================


class ReadingSession(BaseModel):
    """Session de lecture individuelle (immutable).

    Représente une session de lecture complète (ouverture → fermeture
    du lecteur). Utilisé pour les statistiques avancées (temps total,
    pages lues, durée moyenne, activité récente).

    Attributes:
        id: Identifiant unique de la session.
        manga_id: ID du manga lu.
        chapter_id: ID du chapitre lu.
        start_page: Page de début (1-based).
        end_page: Page de fin (1-based).
        pages_read: Nombre de pages lues durant cette session.
        duration_seconds: Durée de la session en secondes.
        started_at: Timestamp de début.
        ended_at: Timestamp de fin.
    """

    id: str = Field(
        default_factory=generate_session_id,
        description="Identifiant unique de la session.",
    )
    manga_id: str = Field(..., description="ID du manga lu.")
    chapter_id: str = Field(..., description="ID du chapitre lu.")
    start_page: int = Field(..., ge=1, description="Page de début (1-based).")
    end_page: int = Field(..., ge=1, description="Page de fin (1-based).")
    pages_read: int = Field(
        ...,
        ge=0,
        description="Nombre de pages lues durant cette session.",
    )
    duration_seconds: float = Field(
        ...,
        ge=0.0,
        description="Durée de la session en secondes.",
    )
    started_at: datetime = Field(..., description="Timestamp de début.")
    ended_at: datetime = Field(..., description="Timestamp de fin.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_session_consistency(self) -> Self:
        """Vérifie la cohérence de la session."""
        if self.ended_at < self.started_at:
            raise LibraryModelError(
                f"ended_at ({self.ended_at}) ne peut pas être avant "
                f"started_at ({self.started_at}) pour la session {self.id}"
            )

        if self.end_page < self.start_page:
            raise LibraryModelError(
                f"end_page ({self.end_page}) ne peut pas être avant "
                f"start_page ({self.start_page}) pour la session {self.id}"
            )

        # Vérifier la cohérence pages_read vs (end_page - start_page + 1)
        expected_pages = self.end_page - self.start_page + 1
        if self.pages_read > expected_pages:
            raise LibraryModelError(
                f"pages_read ({self.pages_read}) ne peut pas être supérieur à "
                f"end_page - start_page + 1 ({expected_pages}) pour la session {self.id}"
            )

        return self

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def duration_minutes(self) -> float:
        """Durée de la session en minutes."""
        return self.duration_seconds / 60.0

    @property
    def duration_hours(self) -> float:
        """Durée de la session en heures."""
        return self.duration_seconds / 3600.0

    @property
    def reading_speed_pages_per_hour(self) -> float:
        """Vitesse de lecture en pages par heure.

        Returns:
            Vitesse de lecture, ou 0.0 si la durée est nulle.
        """
        if self.duration_hours <= 0:
            return 0.0
        return self.pages_read / self.duration_hours

    @property
    def reading_speed_pages_per_minute(self) -> float:
        """Vitesse de lecture en pages par minute."""
        if self.duration_minutes <= 0:
            return 0.0
        return self.pages_read / self.duration_minutes

    @property
    def average_time_per_page_seconds(self) -> float:
        """Temps moyen par page en secondes.

        Returns:
            Temps moyen, ou 0.0 si aucune page lue.
        """
        if self.pages_read <= 0:
            return 0.0
        return self.duration_seconds / self.pages_read

    @property
    def duration_human(self) -> str:
        """Durée formatée de manière humaine (ex: '1h 23m', '45s')."""
        seconds = int(self.duration_seconds)
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}m {secs:02d}s" if secs else f"{minutes}m"
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes:02d}m"

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable."""
        return {
            "id": self.id,
            "manga_id": self.manga_id,
            "chapter_id": self.chapter_id,
            "start_page": self.start_page,
            "end_page": self.end_page,
            "pages_read": self.pages_read,
            "duration_seconds": self.duration_seconds,
            "duration_human": self.duration_human,
            "reading_speed_pages_per_hour": round(
                self.reading_speed_pages_per_hour, 1
            ),
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
        }

    def __repr__(self) -> str:
        return (
            f"<ReadingSession id={self.id} manga={self.manga_id} "
            f"pages={self.pages_read} duration={self.duration_human}>"
        )


# ============================================================================
# MODÈLES PYDANTIC — Statistiques
# ============================================================================


class LibraryStats(BaseModel):
    """Statistiques agrégées de la bibliothèque (immutable).

    Snapshot à un instant T des statistiques globales de la bibliothèque
    et de l'activité de lecture de l'utilisateur. Correspond à la vue
    SQL `v_reading_stats` + `v_library_stats`.

    Attributes:
        total_manga: Nombre total de mangas dans la bibliothèque.
        total_chapters: Nombre total de chapitres.
        total_downloaded_chapters: Nombre de chapitres téléchargés.
        total_pages: Nombre total de pages (estimé).
        total_size_bytes: Taille totale de la bibliothèque en bytes.
        currently_reading: Nombre de mangas en cours de lecture.
        completed: Nombre de mangas terminés.
        on_hold: Nombre de mangas en pause.
        dropped: Nombre de mangas abandonnés.
        plan_to_read: Nombre de mangas dans la wishlist.
        re_reading: Nombre de mangas en relecture.
        total_tracked: Nombre total de mangas avec progression.
        average_score: Note moyenne (0-10, None si aucune note).
        total_rereads: Nombre total de relectures.
        total_sessions: Nombre total de sessions de lecture.
        total_reading_time_seconds: Temps total de lecture en secondes.
    """

    # Statistiques de la bibliothèque
    total_manga: int = Field(default=0, ge=0)
    total_chapters: int = Field(default=0, ge=0)
    total_downloaded_chapters: int = Field(default=0, ge=0)
    total_pages: int = Field(default=0, ge=0)
    total_size_bytes: int = Field(default=0, ge=0)

    # Statistiques de lecture par statut
    currently_reading: int = Field(default=0, ge=0)
    completed: int = Field(default=0, ge=0)
    on_hold: int = Field(default=0, ge=0)
    dropped: int = Field(default=0, ge=0)
    plan_to_read: int = Field(default=0, ge=0)
    re_reading: int = Field(default=0, ge=0)
    total_tracked: int = Field(default=0, ge=0)

    # Statistiques utilisateur
    average_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="Note moyenne (0-10, None si aucune note).",
    )
    total_rereads: int = Field(default=0, ge=0)
    total_sessions: int = Field(default=0, ge=0)
    total_reading_time_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Propriétés dérivées
    # --------------------------------------------------------------------

    @property
    def completion_rate(self) -> float:
        """Taux de complétion (0.0 à 1.0) des mangas suivis.

        Calculé comme : completed / total_tracked.
        """
        if self.total_tracked == 0:
            return 0.0
        return self.completed / self.total_tracked

    @property
    def drop_rate(self) -> float:
        """Taux d'abandon (0.0 à 1.0) des mangas suivis."""
        if self.total_tracked == 0:
            return 0.0
        return self.dropped / self.total_tracked

    @property
    def active_reading_rate(self) -> float:
        """Taux de lecture active (0.0 à 1.0).

        Calculé comme : (currently_reading + re_reading) / total_tracked.
        """
        if self.total_tracked == 0:
            return 0.0
        return (self.currently_reading + self.re_reading) / self.total_tracked

    @property
    def download_completion_rate(self) -> float:
        """Taux de complétion des téléchargements (0.0 à 1.0)."""
        if self.total_chapters == 0:
            return 0.0
        return self.total_downloaded_chapters / self.total_chapters

    @property
    def total_reading_time_hours(self) -> float:
        """Temps total de lecture en heures."""
        return self.total_reading_time_seconds / 3600.0

    @property
    def total_reading_time_days(self) -> float:
        """Temps total de lecture en jours."""
        return self.total_reading_time_seconds / 86_400.0

    @property
    def average_session_duration_seconds(self) -> float:
        """Durée moyenne d'une session en secondes."""
        if self.total_sessions == 0:
            return 0.0
        return self.total_reading_time_seconds / self.total_sessions

    @property
    def average_sessions_per_manga(self) -> float:
        """Nombre moyen de sessions par manga suivi."""
        if self.total_tracked == 0:
            return 0.0
        return self.total_sessions / self.total_tracked

    @property
    def total_size_human(self) -> str:
        """Taille totale formatée (ex: '2.3 GB', '456 MB')."""
        size = self.total_size_bytes
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024.0:
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
            size /= 1024.0
        return f"{size:.1f} PB"

    @property
    def total_reading_time_human(self) -> str:
        """Temps total de lecture formaté (ex: '12j 5h', '3h 45m')."""
        seconds = int(self.total_reading_time_seconds)
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            minutes = seconds // 60
            return f"{minutes}m"
        if seconds < 86_400:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes:02d}m" if minutes else f"{hours}h"
        days = seconds // 86_400
        hours = (seconds % 86_400) // 3600
        return f"{days}j {hours}h" if hours else f"{days}j"

    @property
    def average_score_stars(self) -> str:
        """Note moyenne en étoiles (0 à 5)."""
        if self.average_score is None:
            return "☆☆☆☆☆"
        stars = round(self.average_score / 2.0)
        return "★" * stars + "☆" * (5 - stars)

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable."""
        return {
            "library": {
                "total_manga": self.total_manga,
                "total_chapters": self.total_chapters,
                "total_downloaded_chapters": self.total_downloaded_chapters,
                "total_pages": self.total_pages,
                "total_size_bytes": self.total_size_bytes,
                "total_size_human": self.total_size_human,
            },
            "reading": {
                "currently_reading": self.currently_reading,
                "completed": self.completed,
                "on_hold": self.on_hold,
                "dropped": self.dropped,
                "plan_to_read": self.plan_to_read,
                "re_reading": self.re_reading,
                "total_tracked": self.total_tracked,
                "completion_rate": round(self.completion_rate, 3),
                "drop_rate": round(self.drop_rate, 3),
                "active_reading_rate": round(self.active_reading_rate, 3),
            },
            "user": {
                "average_score": self.average_score,
                "average_score_stars": self.average_score_stars,
                "total_rereads": self.total_rereads,
                "total_sessions": self.total_sessions,
                "total_reading_time_seconds": self.total_reading_time_seconds,
                "total_reading_time_human": self.total_reading_time_human,
                "average_session_duration_seconds": round(
                    self.average_session_duration_seconds, 1
                ),
            },
        }

    def __repr__(self) -> str:
        return (
            f"<LibraryStats manga={self.total_manga} "
            f"reading={self.currently_reading} "
            f"completed={self.completed} "
            f"time={self.total_reading_time_human}>"
        )


# ============================================================================
# MODÈLES PYDANTIC — Activité de lecture
# ============================================================================


class ReadingActivity(BaseModel):
    """Activité de lecture récente (immutable).

    Représente une entrée de la vue `v_recent_activity` : une session
    de lecture avec le contexte du manga et du chapitre.

    Utilisé pour afficher le flux d'activité dans les interfaces.
    """

    session_id: str = Field(..., description="ID de la session.")
    manga_id: str = Field(..., description="ID du manga.")
    manga_title: str = Field(..., description="Titre du manga.")
    cover_url: str | None = Field(default=None, description="URL de la couverture.")
    chapter_number: str = Field(..., description="Numéro du chapitre.")
    chapter_title: str | None = Field(default=None, description="Titre du chapitre.")
    pages_read: int = Field(..., ge=0, description="Nombre de pages lues.")
    duration_seconds: float = Field(..., ge=0.0, description="Durée de la session.")
    started_at: datetime = Field(..., description="Timestamp de début.")
    ended_at: datetime = Field(..., description="Timestamp de fin.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def duration_human(self) -> str:
        """Durée formatée de manière humaine."""
        seconds = int(self.duration_seconds)
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}m {secs:02d}s" if secs else f"{minutes}m"
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes:02d}m"

    @property
    def chapter_display(self) -> str:
        """Affichage du chapitre (ex: 'Ch. 42 - Titre')."""
        if self.chapter_title:
            return f"Ch. {self.chapter_number} - {self.chapter_title}"
        return f"Ch. {self.chapter_number}"

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable."""
        return {
            "session_id": self.session_id,
            "manga_id": self.manga_id,
            "manga_title": self.manga_title,
            "cover_url": self.cover_url,
            "chapter_display": self.chapter_display,
            "pages_read": self.pages_read,
            "duration_seconds": self.duration_seconds,
            "duration_human": self.duration_human,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
        }

    def __repr__(self) -> str:
        return (
            f"<ReadingActivity manga='{self.manga_title[:30]}' "
            f"chapter={self.chapter_number} "
            f"pages={self.pages_read} duration={self.duration_human}>"
        )


# ============================================================================
# MODÈLES PYDANTIC — Continuer la lecture
# ============================================================================


class ContinueReadingEntry(BaseModel):
    """Entrée de la liste 'Continuer la lecture' (immutable).

    Représente un manga en cours de lecture avec le contexte du chapitre
    et de la page courants. Correspond à la vue `v_continue_reading`.
    """

    manga_id: str = Field(..., description="ID du manga.")
    title: str = Field(..., description="Titre du manga.")
    cover_url: str | None = Field(default=None, description="URL de la couverture.")
    site: str = Field(..., description="Site d'origine.")
    chapter_id: str = Field(..., description="ID du chapitre courant.")
    chapter_number: str = Field(..., description="Numéro du chapitre.")
    chapter_title: str | None = Field(default=None, description="Titre du chapitre.")
    page: int = Field(..., ge=0, description="Page courante.")
    last_read_at: datetime = Field(..., description="Timestamp de dernière lecture.")
    reading_status: ReadingStatus = Field(..., description="Statut de lecture.")
    score: float | None = Field(default=None, description="Note utilisateur.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def chapter_display(self) -> str:
        """Affichage du chapitre."""
        if self.chapter_title:
            return f"Ch. {self.chapter_number} - {self.chapter_title}"
        return f"Ch. {self.chapter_number}"

    @property
    def progress_label(self) -> str:
        """Libellé de progression (ex: 'Page 42')."""
        if self.page <= 0:
            return "Non commencé"
        return f"Page {self.page}"

    @property
    def status_icon(self) -> str:
        """Icône du statut de lecture."""
        return self.reading_status.icon

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable."""
        return {
            "manga_id": self.manga_id,
            "title": self.title,
            "cover_url": self.cover_url,
            "site": self.site,
            "chapter_display": self.chapter_display,
            "progress_label": self.progress_label,
            "page": self.page,
            "last_read_at": self.last_read_at.isoformat(),
            "reading_status": self.reading_status.value,
            "reading_status_label": self.reading_status.label,
            "reading_status_icon": self.reading_status.icon,
            "score": self.score,
        }

    def __repr__(self) -> str:
        return (
            f"<ContinueReadingEntry manga='{self.title[:30]}' "
            f"chapter={self.chapter_number} page={self.page}>"
        )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "LibraryModelError",
    "InvalidReadingStatusError",
    "ListValidationError",
    # Enums
    "ReadingStatus",
    "ListVisibility",
    "SortOrder",
    # Modèles — Progression
    "ReadingProgress",
    # Modèles — Listes
    "ReadingList",
    "ReadingListManga",
    # Modèles — Sessions
    "ReadingSession",
    # Modèles — Statistiques
    "LibraryStats",
    # Modèles — Activité
    "ReadingActivity",
    "ContinueReadingEntry",
    # Helpers
    "generate_reading_list_id",
    "generate_session_id",
    # Constantes
    "_VALID_STATUS_TRANSITIONS",
]
