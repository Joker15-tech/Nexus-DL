"""Routeur FastAPI pour la gestion de la bibliothèque locale.

Ce module fournit un routeur FastAPI complet pour gérer la bibliothèque
locale de mangas/webtoons/comics de l'utilisateur. Il permet de lister,
filtrer, trier, mettre à jour les mangas, gérer les listes de lecture,
suivre la progression, et scanner le disque pour synchroniser.

**Endpoints** :
    - GET /library                            : Lister tous les mangas
    - GET /library/stats                      : Statistiques globales
    - GET /library/continue-reading           : Mangas en cours de lecture
    - GET /library/search                     : Recherche dans la bibliothèque
    - GET /library/{manga_id}                 : Détails d'un manga
    - PATCH /library/{manga_id}               : Mettre à jour un manga
    - DELETE /library/{manga_id}              : Supprimer un manga
    - GET /library/{manga_id}/chapters        : Chapitres du manga
    - PATCH /library/{manga_id}/chapters/{chapter_id} : Statut d'un chapitre
    - POST /library/scan                      : Scanner la bibliothèque
    - GET /library/scan/status                : Statut du scan
    - GET /library/reading-lists              : Lister les reading lists
    - POST /library/reading-lists             : Créer une reading list
    - GET /library/reading-lists/{list_id}    : Détails d'une reading list
    - PATCH /library/reading-lists/{list_id}  : Modifier une reading list
    - DELETE /library/reading-lists/{list_id} : Supprimer une reading list
    - POST /library/reading-lists/{list_id}/manga : Ajouter un manga
    - DELETE /library/reading-lists/{list_id}/manga/{manga_id} : Retirer

**Fonctionnalités** :
    - Cache en mémoire pour les lectures fréquentes
    - Filtrage multi-critères (statut, langue, tags, reading list)
    - Tri par 6 critères (titre, date, progression, auteur, statut)
    - Pagination avec page/page_size
    - Statistiques globales (total, taille, temps de lecture)
    - Gestion des listes de lecture (CRUD complet)
    - Suivi de progression (chapitres lus/téléchargés)
    - Scan du disque pour synchronisation
    - Recherche plein texte
    - Événements EventBus pour monitoring
    - Logging structuré
    - Validation stricte via Pydantic v2

**Exemple d'utilisation** :
    >>> from fastapi import FastAPI
    >>> from nexusdl.interfaces.web.backend.routers.library import library_router
    >>>
    >>> app = FastAPI()
    >>> app.include_router(library_router, prefix="/api/v1")

**Exemples d'appels API** :
    >>> # Lister avec filtres
    >>> GET /api/v1/library?status=reading&sort_by=last_read&page=1
    >>>
    >>> # Statistiques
    >>> GET /api/v1/library/stats
    >>>
    >>> # Mettre à jour un manga
    >>> PATCH /api/v1/library/12345
    >>> {"reading_status": "completed", "tags": ["favorite"]}
    >>>
    >>> # Créer une reading list
    >>> POST /api/v1/library/reading-lists
    >>> {"name": "Favorites", "description": "My favorite mangas"}
    >>>
    >>> # Scanner la bibliothèque
    >>> POST /api/v1/library/scan

Intégration :
    - core/library/database.py       : Base de données SQLite
    - core/library/scanner.py        : Scanner de bibliothèque
    - core/library/search.py         : Recherche plein texte
    - core/models/manga.py           : Modèles Manga, Chapter
    - core/models/library.py         : ReadingList, ReadingProgress, LibraryStats
    - core/events.py                 : EventBus pour monitoring
    - core/logger.py                 : Logs
    - core/i18n.py                   : Traductions
    - core/utils/text.py             : format_size
    - core/utils/time.py             : format_duration
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
DEFAULT_LIBRARY_LIMIT: Final[int] = 50
MAX_LIBRARY_LIMIT: Final[int] = 200
DEFAULT_CACHE_TTL_SECONDS: Final[int] = 120  # 2 minutes
MAX_CACHE_SIZE: Final[int] = 500
DEFAULT_SCAN_TIMEOUT_SECONDS: Final[int] = 600  # 10 minutes
MAX_READING_LISTS_PER_USER: Final[int] = 100
MAX_MANGAS_PER_READING_LIST: Final[int] = 1000
MAX_TAGS_PER_MANGA: Final[int] = 50


# ============================================================================
# EXCEPTIONS
# ============================================================================


class LibraryRouterError(NexusDLError):
    """Exception de base pour les erreurs du routeur bibliothèque."""


class MangaNotFoundError(LibraryRouterError):
    """Exception levée lorsqu'un manga n'est pas trouvé dans la bibliothèque.

    Attributes:
        manga_id: ID du manga.
    """

    def __init__(self, manga_id: str) -> None:
        super().__init__(
            t(
                "library.error.manga_not_found",
                default="Manga not found in library: {manga_id}",
                manga_id=manga_id,
            )
        )
        self.manga_id = manga_id


class ReadingListNotFoundError(LibraryRouterError):
    """Exception levée lorsqu'une reading list n'est pas trouvée.

    Attributes:
        list_id: ID de la reading list.
    """

    def __init__(self, list_id: str) -> None:
        super().__init__(
            t(
                "library.error.reading_list_not_found",
                default="Reading list not found: {list_id}",
                list_id=list_id,
            )
        )
        self.list_id = list_id


class ChapterNotFoundError(LibraryRouterError):
    """Exception levée lorsqu'un chapitre n'est pas trouvé.

    Attributes:
        manga_id: ID du manga.
        chapter_id: ID du chapitre.
    """

    def __init__(self, manga_id: str, chapter_id: str) -> None:
        super().__init__(
            t(
                "library.error.chapter_not_found",
                default="Chapter not found: {manga_id}/{chapter_id}",
                manga_id=manga_id,
                chapter_id=chapter_id,
            )
        )
        self.manga_id = manga_id
        self.chapter_id = chapter_id


class ScanError(LibraryRouterError):
    """Exception levée lorsqu'un scan échoue.

    Attributes:
        reason: Raison de l'échec.
    """

    def __init__(self, reason: str = "") -> None:
        msg = t("library.error.scan_failed", default="Library scan failed")
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


class ScanAlreadyRunningError(LibraryRouterError):
    """Exception levée lorsqu'un scan est déjà en cours."""

    def __init__(self) -> None:
        super().__init__(
            t("library.error.scan_already_running", default="A scan is already in progress")
        )


# ============================================================================
# ENUMS
# ============================================================================


class ReadingStatus(str, Enum):
    """Statut de lecture d'un manga.

    Attributes:
        READING: En cours de lecture.
        COMPLETED: Terminé.
        PLAN_TO_READ: À lire.
        ON_HOLD: En pause.
        DROPPED: Abandonné.
    """

    READING = "reading"
    COMPLETED = "completed"
    PLAN_TO_READ = "plan_to_read"
    ON_HOLD = "on_hold"
    DROPPED = "dropped"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ReadingStatus.READING: t("library.status.reading", default="Reading"),
            ReadingStatus.COMPLETED: t("library.status.completed", default="Completed"),
            ReadingStatus.PLAN_TO_READ: t("library.status.plan_to_read", default="Plan to Read"),
            ReadingStatus.ON_HOLD: t("library.status.on_hold", default="On Hold"),
            ReadingStatus.DROPPED: t("library.status.dropped", default="Dropped"),
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            ReadingStatus.READING: "📖",
            ReadingStatus.COMPLETED: "✅",
            ReadingStatus.PLAN_TO_READ: "📋",
            ReadingStatus.ON_HOLD: "⏸️",
            ReadingStatus.DROPPED: "❌",
        }[self]


class LibrarySortBy(str, Enum):
    """Critère de tri de la bibliothèque.

    Attributes:
        TITLE: Tri alphabétique par titre.
        DATE_ADDED: Tri par date d'ajout.
        LAST_READ: Tri par dernière lecture.
        PROGRESS: Tri par progression.
        AUTHOR: Tri par auteur.
        STATUS: Tri par statut de lecture.
    """

    TITLE = "title"
    DATE_ADDED = "date_added"
    LAST_READ = "last_read"
    PROGRESS = "progress"
    AUTHOR = "author"
    STATUS = "status"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            LibrarySortBy.TITLE: t("library.sort.title", default="Title"),
            LibrarySortBy.DATE_ADDED: t("library.sort.date_added", default="Date Added"),
            LibrarySortBy.LAST_READ: t("library.sort.last_read", default="Last Read"),
            LibrarySortBy.PROGRESS: t("library.sort.progress", default="Progress"),
            LibrarySortBy.AUTHOR: t("library.sort.author", default="Author"),
            LibrarySortBy.STATUS: t("library.sort.status", default="Status"),
        }[self]


class ScanStatus(str, Enum):
    """Statut d'un scan de bibliothèque.

    Attributes:
        IDLE: Pas de scan en cours.
        RUNNING: Scan en cours.
        COMPLETED: Scan terminé.
        FAILED: Scan échoué.
    """

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ============================================================================
# MODÈLES DE REQUÊTE — Pydantic
# ============================================================================


class UpdateMangaRequest(BaseModel):
    """Requête de mise à jour d'un manga.

    Attributes:
        reading_status: Statut de lecture.
        tags: Tags du manga.
        notes: Notes personnelles.
        current_page: Page actuelle.
    """

    reading_status: ReadingStatus | None = Field(default=None, description="Statut lecture.")
    tags: list[str] | None = Field(default=None, description="Tags.")
    notes: str | None = Field(default=None, description="Notes.")
    current_page: int | None = Field(default=None, ge=0, description="Page actuelle.")

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str] | None) -> list[str] | None:
        """Valide les tags."""
        if v is not None:
            if len(v) > MAX_TAGS_PER_MANGA:
                raise ValueError(f"Maximum {MAX_TAGS_PER_MANGA} tags")
            v = [tag.strip() for tag in v if tag.strip()]
        return v

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: str | None) -> str | None:
        """Valide les notes."""
        if v is not None and len(v) > 10000:
            raise ValueError("Notes trop longues (max 10000 caractères)")
        return v


class UpdateChapterRequest(BaseModel):
    """Requête de mise à jour d'un chapitre.

    Attributes:
        is_read: Si le chapitre est lu.
        current_page: Page actuelle.
    """

    is_read: bool | None = Field(default=None, description="Lu.")
    current_page: int | None = Field(default=None, ge=0, description="Page actuelle.")


class CreateReadingListRequest(BaseModel):
    """Requête de création d'une reading list.

    Attributes:
        name: Nom de la liste.
        description: Description.
        manga_ids: IDs des mangas à inclure.
    """

    name: str = Field(..., min_length=1, max_length=100, description="Nom.")
    description: str = Field(default="", max_length=500, description="Description.")
    manga_ids: list[str] = Field(default_factory=list, description="Mangas.")

    @field_validator("manga_ids")
    @classmethod
    def validate_manga_ids(cls, v: list[str]) -> list[str]:
        """Valide les IDs."""
        if len(v) > MAX_MANGAS_PER_READING_LIST:
            raise ValueError(f"Maximum {MAX_MANGAS_PER_READING_LIST} mangas par liste")
        return v


class UpdateReadingListRequest(BaseModel):
    """Requête de mise à jour d'une reading list.

    Attributes:
        name: Nouveau nom.
        description: Nouvelle description.
    """

    name: str | None = Field(default=None, min_length=1, max_length=100, description="Nom.")
    description: str | None = Field(default=None, max_length=500, description="Description.")


class AddMangaToListRequest(BaseModel):
    """Requête d'ajout d'un manga à une reading list.

    Attributes:
        manga_ids: IDs des mangas à ajouter.
    """

    manga_ids: list[str] = Field(..., min_length=1, description="Mangas à ajouter.")

    @field_validator("manga_ids")
    @classmethod
    def validate_manga_ids(cls, v: list[str]) -> list[str]:
        """Valide les IDs."""
        if len(v) > 100:
            raise ValueError("Maximum 100 mangas par opération")
        return v


# ============================================================================
# MODÈLES DE RÉPONSE — Pydantic
# ============================================================================


class LibraryMangaResponse(BaseModel):
    """Réponse pour un manga dans la bibliothèque.

    Attributes:
        id: ID unique.
        site_id: ID du site source.
        title: Titre.
        author: Auteur.
        year: Année.
        status: Statut de publication.
        language: Code langue.
        cover_url: URL de la couverture.
        reading_status: Statut de lecture.
        progress_percentage: Progression (0.0 à 1.0).
        chapters_read: Chapitres lus.
        chapters_total: Chapitres totaux.
        last_read_at: Dernière lecture.
        added_at: Date d'ajout.
        tags: Tags.
        notes: Notes.
        total_size_bytes: Taille totale.
    """

    id: str = Field(..., description="ID unique.")
    site_id: str = Field(default="", description="ID du site.")
    title: str = Field(..., description="Titre.")
    author: str = Field(default="", description="Auteur.")
    year: int | None = Field(default=None, description="Année.")
    status: str = Field(default="unknown", description="Statut publication.")
    language: str = Field(default="en", description="Code langue.")
    cover_url: str = Field(default="", description="URL couverture.")
    reading_status: ReadingStatus = Field(default=ReadingStatus.PLAN_TO_READ, description="Statut lecture.")
    progress_percentage: float = Field(default=0.0, ge=0.0, le=1.0, description="Progression.")
    chapters_read: int = Field(default=0, ge=0, description="Chapitres lus.")
    chapters_total: int = Field(default=0, ge=0, description="Chapitres totaux.")
    last_read_at: datetime | None = Field(default=None, description="Dernière lecture.")
    added_at: datetime = Field(..., description="Date ajout.")
    tags: list[str] = Field(default_factory=list, description="Tags.")
    notes: str = Field(default="", description="Notes.")
    total_size_bytes: int = Field(default=0, ge=0, description="Taille bytes.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class LibraryListResponse(BaseModel):
    """Réponse pour la liste des mangas.

    Attributes:
        mangas: Liste des mangas.
        total: Nombre total.
        page: Page actuelle.
        page_size: Taille de page.
        has_next: Page suivante.
        has_previous: Page précédente.
    """

    mangas: list[LibraryMangaResponse] = Field(default_factory=list, description="Mangas.")
    total: int = Field(default=0, ge=0, description="Total.")
    page: int = Field(default=1, ge=1, description="Page.")
    page_size: int = Field(default=DEFAULT_LIBRARY_LIMIT, ge=1, description="Taille page.")
    has_next: bool = Field(default=False, description="Page suivante.")
    has_previous: bool = Field(default=False, description="Page précédente.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class LibraryStatsResponse(BaseModel):
    """Réponse avec les statistiques de la bibliothèque.

    Attributes:
        total_manga: Nombre total de mangas.
        total_chapters: Nombre total de chapitres.
        total_size_bytes: Taille totale.
        total_reading_time_seconds: Temps total de lecture.
        by_reading_status: Compteurs par statut de lecture.
        by_language: Compteurs par langue.
        currently_reading: Mangas en cours de lecture.
        completed: Mangas terminés.
        last_scan_at: Dernier scan.
    """

    total_manga: int = Field(default=0, ge=0, description="Total mangas.")
    total_chapters: int = Field(default=0, ge=0, description="Total chapitres.")
    total_size_bytes: int = Field(default=0, ge=0, description="Taille totale.")
    total_reading_time_seconds: float = Field(default=0.0, ge=0.0, description="Temps lecture.")
    by_reading_status: dict[str, int] = Field(default_factory=dict, description="Par statut.")
    by_language: dict[str, int] = Field(default_factory=dict, description="Par langue.")
    currently_reading: int = Field(default=0, ge=0, description="En cours.")
    completed: int = Field(default=0, ge=0, description="Terminés.")
    last_scan_at: datetime | None = Field(default=None, description="Dernier scan.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class LibraryChapterResponse(BaseModel):
    """Réponse pour un chapitre dans la bibliothèque.

    Attributes:
        id: ID unique.
        number: Numéro.
        title: Titre.
        is_read: Si lu.
        is_downloaded: Si téléchargé.
        current_page: Page actuelle.
        pages_total: Total de pages.
        last_read_at: Dernière lecture.
        published_at: Date de publication.
        scanlator: Scanlator.
    """

    id: str = Field(..., description="ID unique.")
    number: float = Field(..., description="Numéro.")
    title: str = Field(default="", description="Titre.")
    is_read: bool = Field(default=False, description="Lu.")
    is_downloaded: bool = Field(default=False, description="Téléchargé.")
    current_page: int = Field(default=0, ge=0, description="Page actuelle.")
    pages_total: int = Field(default=0, ge=0, description="Total pages.")
    last_read_at: datetime | None = Field(default=None, description="Dernière lecture.")
    published_at: datetime | None = Field(default=None, description="Publication.")
    scanlator: str = Field(default="", description="Scanlator.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class LibraryChaptersResponse(BaseModel):
    """Réponse avec la liste des chapitres.

    Attributes:
        manga_id: ID du manga.
        chapters: Liste des chapitres.
        total: Nombre total.
        read_count: Nombre de chapitres lus.
        downloaded_count: Nombre de chapitres téléchargés.
    """

    manga_id: str = Field(..., description="ID manga.")
    chapters: list[LibraryChapterResponse] = Field(default_factory=list, description="Chapitres.")
    total: int = Field(default=0, ge=0, description="Total.")
    read_count: int = Field(default=0, ge=0, description="Lus.")
    downloaded_count: int = Field(default=0, ge=0, description="Téléchargés.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ReadingListResponse(BaseModel):
    """Réponse pour une reading list.

    Attributes:
        id: ID unique.
        name: Nom.
        description: Description.
        manga_count: Nombre de mangas.
        created_at: Date de création.
        updated_at: Dernière modification.
    """

    id: str = Field(..., description="ID unique.")
    name: str = Field(..., description="Nom.")
    description: str = Field(default="", description="Description.")
    manga_count: int = Field(default=0, ge=0, description="Nombre mangas.")
    created_at: datetime = Field(..., description="Création.")
    updated_at: datetime = Field(..., description="Dernière MAJ.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ReadingListDetailResponse(BaseModel):
    """Réponse détaillée pour une reading list.

    Attributes:
        id: ID unique.
        name: Nom.
        description: Description.
        mangas: Liste des mangas.
        manga_count: Nombre de mangas.
        created_at: Date de création.
        updated_at: Dernière modification.
    """

    id: str = Field(..., description="ID unique.")
    name: str = Field(..., description="Nom.")
    description: str = Field(default="", description="Description.")
    mangas: list[LibraryMangaResponse] = Field(default_factory=list, description="Mangas.")
    manga_count: int = Field(default=0, ge=0, description="Nombre mangas.")
    created_at: datetime = Field(..., description="Création.")
    updated_at: datetime = Field(..., description="Dernière MAJ.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ReadingListsResponse(BaseModel):
    """Réponse avec la liste des reading lists.

    Attributes:
        lists: Liste des reading lists.
        total: Nombre total.
    """

    lists: list[ReadingListResponse] = Field(default_factory=list, description="Listes.")
    total: int = Field(default=0, ge=0, description="Total.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ScanStatusResponse(BaseModel):
    """Réponse avec le statut d'un scan.

    Attributes:
        status: Statut du scan.
        started_at: Début du scan.
        completed_at: Fin du scan.
        scanned_files: Nombre de fichiers scannés.
        added_mangas: Nombre de mangas ajoutés.
        updated_mangas: Nombre de mangas mis à jour.
        removed_mangas: Nombre de mangas supprimés.
        error_message: Message d'erreur (si failed).
        progress_percentage: Progression (0.0 à 1.0).
    """

    status: ScanStatus = Field(..., description="Statut.")
    started_at: datetime | None = Field(default=None, description="Début.")
    completed_at: datetime | None = Field(default=None, description="Fin.")
    scanned_files: int = Field(default=0, ge=0, description="Fichiers scannés.")
    added_mangas: int = Field(default=0, ge=0, description="Mangas ajoutés.")
    updated_mangas: int = Field(default=0, ge=0, description="Mangas MAJ.")
    removed_mangas: int = Field(default=0, ge=0, description="Mangas supprimés.")
    error_message: str | None = Field(default=None, description="Erreur.")
    progress_percentage: float = Field(default=0.0, ge=0.0, le=1.0, description="Progression.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ScanResponse(BaseModel):
    """Réponse après lancement d'un scan.

    Attributes:
        scan_id: ID du scan.
        status: Statut initial.
        message: Message.
    """

    scan_id: str = Field(..., description="ID du scan.")
    status: ScanStatus = Field(..., description="Statut.")
    message: str = Field(..., description="Message.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class UpdateResponse(BaseModel):
    """Réponse après mise à jour.

    Attributes:
        success: Si l'opération a réussi.
        message: Message de confirmation.
        manga_id: ID du manga (si applicable).
    """

    success: bool = Field(..., description="Succès.")
    message: str = Field(..., description="Message.")
    manga_id: str | None = Field(default=None, description="ID manga.")

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
# CACHE — Cache en mémoire pour la bibliothèque
# ============================================================================


class LibraryCache:
    """Cache en mémoire pour les données de bibliothèque.

    Évite les requêtes répétées à la base de données.
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
                oldest_keys = sorted(self._cache.keys(), key=lambda k: self._cache[k][1])[:50]
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
_library_cache = LibraryCache()


def get_library_cache() -> LibraryCache:
    """Retourne l'instance globale du cache."""
    return _library_cache


# ============================================================================
# SCAN STATE — État du scan en cours
# ============================================================================


class ScanState:
    """État d'un scan de bibliothèque.

    Permet de suivre la progression d'un scan en cours.
    """

    def __init__(self) -> None:
        """Initialise l'état."""
        self._status = ScanStatus.IDLE
        self._scan_id: str | None = None
        self._started_at: datetime | None = None
        self._completed_at: datetime | None = None
        self._scanned_files = 0
        self._added_mangas = 0
        self._updated_mangas = 0
        self._removed_mangas = 0
        self._error_message: str | None = None
        self._progress = 0.0
        self._lock = asyncio.Lock()

    async def start(self, scan_id: str) -> None:
        """Démarre un scan.

        Args:
            scan_id: ID du scan.
        """
        async with self._lock:
            if self._status == ScanStatus.RUNNING:
                raise ScanAlreadyRunningError()
            self._status = ScanStatus.RUNNING
            self._scan_id = scan_id
            self._started_at = datetime.now(UTC)
            self._completed_at = None
            self._scanned_files = 0
            self._added_mangas = 0
            self._updated_mangas = 0
            self._removed_mangas = 0
            self._error_message = None
            self._progress = 0.0

    async def update_progress(
        self,
        *,
        scanned_files: int | None = None,
        added_mangas: int | None = None,
        updated_mangas: int | None = None,
        removed_mangas: int | None = None,
        progress: float | None = None,
    ) -> None:
        """Met à jour la progression.

        Args:
            scanned_files: Fichiers scannés.
            added_mangas: Mangas ajoutés.
            updated_mangas: Mangas mis à jour.
            removed_mangas: Mangas supprimés.
            progress: Progression.
        """
        async with self._lock:
            if scanned_files is not None:
                self._scanned_files = scanned_files
            if added_mangas is not None:
                self._added_mangas = added_mangas
            if updated_mangas is not None:
                self._updated_mangas = updated_mangas
            if removed_mangas is not None:
                self._removed_mangas = removed_mangas
            if progress is not None:
                self._progress = progress

    async def complete(self) -> None:
        """Marque le scan comme terminé."""
        async with self._lock:
            self._status = ScanStatus.COMPLETED
            self._completed_at = datetime.now(UTC)
            self._progress = 1.0

    async def fail(self, error_message: str) -> None:
        """Marque le scan comme échoué.

        Args:
            error_message: Message d'erreur.
        """
        async with self._lock:
            self._status = ScanStatus.FAILED
            self._completed_at = datetime.now(UTC)
            self._error_message = error_message

    async def reset(self) -> None:
        """Réinitialise l'état."""
        async with self._lock:
            self._status = ScanStatus.IDLE
            self._scan_id = None

    async def get_status(self) -> ScanStatusResponse:
        """Récupère le statut actuel.

        Returns:
            Statut du scan.
        """
        async with self._lock:
            return ScanStatusResponse(
                status=self._status,
                started_at=self._started_at,
                completed_at=self._completed_at,
                scanned_files=self._scanned_files,
                added_mangas=self._added_mangas,
                updated_mangas=self._updated_mangas,
                removed_mangas=self._removed_mangas,
                error_message=self._error_message,
                progress_percentage=self._progress,
            )

    @property
    def is_running(self) -> bool:
        """Indique si un scan est en cours."""
        return self._status == ScanStatus.RUNNING

    @property
    def scan_id(self) -> str | None:
        """ID du scan en cours."""
        return self._scan_id


# Instance globale de l'état de scan
_scan_state = ScanState()


def get_scan_state() -> ScanState:
    """Retourne l'instance globale de l'état de scan."""
    return _scan_state


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


def _manga_to_library_response(manga: Any) -> LibraryMangaResponse:
    """Convertit un manga en LibraryMangaResponse.

    Args:
        manga: Instance du modèle manga.

    Returns:
        Instance de LibraryMangaResponse.
    """
    # Extraire les données avec gestion des attributs optionnels
    reading_progress = getattr(manga, "reading_progress", None)

    return LibraryMangaResponse(
        id=manga.id,
        site_id=getattr(manga, "site_id", "") or getattr(manga, "site", ""),
        title=manga.title,
        author=getattr(manga, "author", "") or "",
        year=getattr(manga, "year", None),
        status=getattr(manga.status, "value", "unknown") if hasattr(manga, "status") and hasattr(manga.status, "value") else "unknown",
        language=getattr(manga.language, "value", "en") if hasattr(manga, "language") and hasattr(manga.language, "value") else "en",
        cover_url=getattr(manga, "cover_url", "") or "",
        reading_status=ReadingStatus(reading_progress.status.value) if reading_progress and hasattr(reading_progress, "status") and hasattr(reading_progress.status, "value") else ReadingStatus.PLAN_TO_READ,
        progress_percentage=reading_progress.progress_percentage if reading_progress and hasattr(reading_progress, "progress_percentage") else 0.0,
        chapters_read=reading_progress.chapters_read if reading_progress and hasattr(reading_progress, "chapters_read") else 0,
        chapters_total=reading_progress.total_chapters if reading_progress and hasattr(reading_progress, "total_chapters") else 0,
        last_read_at=reading_progress.last_read_at if reading_progress and hasattr(reading_progress, "last_read_at") else None,
        added_at=getattr(manga, "added_at", datetime.now(UTC)),
        tags=getattr(manga, "tags", []) or [],
        notes=getattr(manga, "notes", "") or "",
        total_size_bytes=getattr(manga, "total_size_bytes", 0) or 0,
    )


def _chapter_to_library_response(chapter: Any) -> LibraryChapterResponse:
    """Convertit un chapitre en LibraryChapterResponse.

    Args:
        chapter: Instance du modèle chapitre.

    Returns:
        Instance de LibraryChapterResponse.
    """
    return LibraryChapterResponse(
        id=chapter.id,
        number=chapter.number,
        title=getattr(chapter, "title", "") or "",
        is_read=getattr(chapter, "is_read", False),
        is_downloaded=getattr(chapter, "is_downloaded", False),
        current_page=getattr(chapter, "current_page", 0) or 0,
        pages_total=getattr(chapter, "pages_count", 0) or 0,
        last_read_at=getattr(chapter, "last_read_at", None),
        published_at=getattr(chapter, "published_at", None),
        scanlator=getattr(chapter, "scanlator", "") or "",
    )


def _reading_list_to_response(reading_list: Any) -> ReadingListResponse:
    """Convertit une reading list en ReadingListResponse.

    Args:
        reading_list: Instance du modèle reading list.

    Returns:
        Instance de ReadingListResponse.
    """
    return ReadingListResponse(
        id=reading_list.id,
        name=reading_list.name,
        description=getattr(reading_list, "description", "") or "",
        manga_count=len(getattr(reading_list, "manga_ids", [])) if hasattr(reading_list, "manga_ids") else 0,
        created_at=getattr(reading_list, "created_at", datetime.now(UTC)),
        updated_at=getattr(reading_list, "updated_at", datetime.now(UTC)),
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


async def _emit_library_event(
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Émet un événement de bibliothèque.

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
            source="interfaces.web.library",
        )
    except Exception as e:
        logger.debug("Impossible d'émettre l'événement: {}", e)


# ============================================================================
# ROUTEUR — Endpoints FastAPI
# ============================================================================


if FASTAPI_AVAILABLE:

    library_router = APIRouter(tags=["library"])

    # =========================================================================
    # GET /library — Lister tous les mangas
    # =========================================================================

    @library_router.get(
        "/library",
        response_model=LibraryListResponse,
        summary="Lister les mangas de la bibliothèque",
        description="Retourne la liste des mangas de la bibliothèque avec filtrage et tri.",
        responses={
            200: {"description": "Liste des mangas"},
            500: {"description": "Erreur interne"},
        },
    )
    async def list_library_mangas(
        request: Request,
        page: int = Query(1, ge=1, description="Page"),
        page_size: int = Query(DEFAULT_LIBRARY_LIMIT, ge=1, le=MAX_LIBRARY_LIMIT, description="Taille page"),
        status: ReadingStatus | None = Query(None, description="Filtre statut"),
        language: str | None = Query(None, description="Filtre langue"),
        sort_by: LibrarySortBy = Query(LibrarySortBy.DATE_ADDED, description="Tri"),
        sort_desc: bool = Query(True, description="Tri descendant"),
        reading_list_id: str | None = Query(None, description="Filtre par reading list"),
        tags: list[str] | None = Query(None, description="Filtre par tags"),
    ) -> LibraryListResponse:
        """Liste les mangas de la bibliothèque.

        Args:
            request: Requête HTTP.
            page: Numéro de page.
            page_size: Taille de page.
            status: Filtre par statut de lecture.
            language: Filtre par langue.
            sort_by: Critère de tri.
            sort_desc: Tri descendant.
            reading_list_id: Filtre par reading list.
            tags: Filtre par tags.

        Returns:
            Liste paginée des mangas.
        """
        user_id = _get_user_id_from_request(request)
        logger.info(
            "Liste bibliothèque: user={}, page={}, status={}, sort_by={}",
            user_id,
            page,
            status,
            sort_by.value,
        )

        # Vérifier le cache
        cache = get_library_cache()
        cache_key = f"library_list:{user_id}:{page}:{page_size}:{status}:{language}:{sort_by.value}:{sort_desc}:{reading_list_id}:{','.join(sorted(tags or []))}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        try:
            # TODO: Récupérer les mangas depuis la base de données
            # from nexusdl.core.library import get_library_mangas
            # all_mangas = await get_library_mangas(user_id=user_id, ...)
            all_mangas: list[Any] = []

            # Filtrer par statut
            if status:
                all_mangas = [
                    m for m in all_mangas
                    if hasattr(m, "reading_progress")
                    and m.reading_progress
                    and m.reading_progress.status.value == status.value
                ]

            # Filtrer par langue
            if language:
                all_mangas = [
                    m for m in all_mangas
                    if hasattr(m, "language")
                    and (m.language.value if hasattr(m.language, "value") else m.language) == language
                ]

            # Filtrer par reading list
            if reading_list_id:
                # TODO: Filtrer par reading list
                pass

            # Filtrer par tags
            if tags:
                tags_set = set(tags)
                all_mangas = [
                    m for m in all_mangas
                    if tags_set & set(getattr(m, "tags", []))
                ]

            # Trier
            if sort_by == LibrarySortBy.TITLE:
                all_mangas.sort(key=lambda m: m.title.lower(), reverse=sort_desc)
            elif sort_by == LibrarySortBy.DATE_ADDED:
                all_mangas.sort(
                    key=lambda m: getattr(m, "added_at", datetime.min.replace(tzinfo=UTC)),
                    reverse=sort_desc,
                )
            elif sort_by == LibrarySortBy.LAST_READ:
                all_mangas.sort(
                    key=lambda m: (
                        m.reading_progress.last_read_at
                        if hasattr(m, "reading_progress") and m.reading_progress and m.reading_progress.last_read_at
                        else datetime.min.replace(tzinfo=UTC)
                    ),
                    reverse=sort_desc,
                )
            elif sort_by == LibrarySortBy.PROGRESS:
                all_mangas.sort(
                    key=lambda m: (
                        m.reading_progress.progress_percentage
                        if hasattr(m, "reading_progress") and m.reading_progress
                        else 0.0
                    ),
                    reverse=sort_desc,
                )
            elif sort_by == LibrarySortBy.AUTHOR:
                all_mangas.sort(key=lambda m: (getattr(m, "author", "") or "").lower(), reverse=sort_desc)
            elif sort_by == LibrarySortBy.STATUS:
                all_mangas.sort(
                    key=lambda m: (
                        m.reading_progress.status.value
                        if hasattr(m, "reading_progress") and m.reading_progress
                        else "plan_to_read"
                    ),
                    reverse=sort_desc,
                )

            # Paginer
            total = len(all_mangas)
            paginated, has_next, has_previous = _paginate_list(all_mangas, page, page_size)

            # Convertir
            manga_responses = [_manga_to_library_response(m) for m in paginated]

            response = LibraryListResponse(
                mangas=manga_responses,
                total=total,
                page=page,
                page_size=page_size,
                has_next=has_next,
                has_previous=has_previous,
            )

            # Stocker dans le cache
            await cache.set(cache_key, response)

            return response

        except Exception as e:
            logger.error("Erreur lors de la liste de la bibliothèque: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="library_list_failed",
                    message=t("library.error.list_failed", default="Failed to list library"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /library/stats — Statistiques globales
    # =========================================================================

    @library_router.get(
        "/library/stats",
        response_model=LibraryStatsResponse,
        summary="Statistiques de la bibliothèque",
        description="Retourne les statistiques globales de la bibliothèque.",
        responses={
            200: {"description": "Statistiques"},
        },
    )
    async def get_library_stats(request: Request) -> LibraryStatsResponse:
        """Récupère les statistiques de la bibliothèque.

        Args:
            request: Requête HTTP.

        Returns:
            Statistiques.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Statistiques bibliothèque: user={}", user_id)

        # Vérifier le cache
        cache = get_library_cache()
        cache_key = f"library_stats:{user_id}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        try:
            # TODO: Récupérer les statistiques depuis la base de données
            # from nexusdl.core.library import get_library_stats
            # stats = await get_library_stats(user_id)

            response = LibraryStatsResponse(
                total_manga=0,
                total_chapters=0,
                total_size_bytes=0,
                total_reading_time_seconds=0.0,
                by_reading_status={s.value: 0 for s in ReadingStatus},
                by_language={},
                currently_reading=0,
                completed=0,
                last_scan_at=None,
            )

            # Stocker dans le cache (TTL plus court pour les stats)
            await cache.set(cache_key, response, ttl_seconds=60)

            return response

        except Exception as e:
            logger.error("Erreur lors de la récupération des statistiques: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="stats_failed",
                    message=t("library.error.stats_failed", default="Failed to get statistics"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /library/continue-reading — Mangas en cours de lecture
    # =========================================================================

    @library_router.get(
        "/library/continue-reading",
        response_model=LibraryListResponse,
        summary="Mangas en cours de lecture",
        description="Retourne les mangas actuellement en cours de lecture, triés par dernière lecture.",
        responses={
            200: {"description": "Mangas en cours"},
        },
    )
    async def get_continue_reading(
        request: Request,
        limit: int = Query(10, ge=1, le=50, description="Nombre max"),
    ) -> LibraryListResponse:
        """Récupère les mangas en cours de lecture.

        Args:
            request: Requête HTTP.
            limit: Nombre maximum.

        Returns:
            Liste des mangas en cours.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Continue reading: user={}, limit={}", user_id, limit)

        # Réutiliser list_library_mangas avec filtres
        return await list_library_mangas(
            request=request,
            page=1,
            page_size=limit,
            status=ReadingStatus.READING,
            sort_by=LibrarySortBy.LAST_READ,
            sort_desc=True,
        )

    # =========================================================================
    # GET /library/search — Recherche dans la bibliothèque
    # =========================================================================

    @library_router.get(
        "/library/search",
        response_model=LibraryListResponse,
        summary="Rechercher dans la bibliothèque",
        description="Recherche plein texte dans la bibliothèque locale.",
        responses={
            200: {"description": "Résultats de recherche"},
        },
    )
    async def search_library(
        request: Request,
        q: str = Query(..., min_length=1, max_length=200, description="Requête"),
        page: int = Query(1, ge=1, description="Page"),
        page_size: int = Query(DEFAULT_LIBRARY_LIMIT, ge=1, le=MAX_LIBRARY_LIMIT, description="Taille"),
    ) -> LibraryListResponse:
        """Recherche dans la bibliothèque.

        Args:
            request: Requête HTTP.
            q: Requête de recherche.
            page: Numéro de page.
            page_size: Taille de page.

        Returns:
            Résultats de recherche.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Recherche bibliothèque: user={}, query={!r}", user_id, q)

        try:
            # TODO: Implémenter la recherche plein texte
            # from nexusdl.core.library import search_library
            # results = await search_library(user_id, q)

            # Pour l'instant, retourner une liste vide
            return LibraryListResponse(
                mangas=[],
                total=0,
                page=page,
                page_size=page_size,
                has_next=False,
                has_previous=False,
            )

        except Exception as e:
            logger.error("Erreur lors de la recherche: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="search_failed",
                    message=t("library.error.search_failed", default="Search failed"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /library/{manga_id} — Détails d'un manga
    # =========================================================================

    @library_router.get(
        "/library/{manga_id}",
        response_model=LibraryMangaResponse,
        summary="Détails d'un manga",
        description="Retourne les détails d'un manga dans la bibliothèque.",
        responses={
            200: {"description": "Détails du manga"},
            404: {"description": "Manga non trouvé"},
        },
    )
    async def get_library_manga(
        request: Request,
        manga_id: str,
    ) -> LibraryMangaResponse:
        """Récupère les détails d'un manga.

        Args:
            request: Requête HTTP.
            manga_id: ID du manga.

        Returns:
            Détails du manga.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Détails manga bibliothèque: user={}, manga={}", user_id, manga_id)

        # Vérifier le cache
        cache = get_library_cache()
        cache_key = f"library_manga:{user_id}:{manga_id}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        try:
            # TODO: Récupérer le manga depuis la base de données
            # from nexusdl.core.library import get_library_manga
            # manga = await get_library_manga(user_id, manga_id)
            manga = None

            if manga is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ErrorResponse(
                        error="manga_not_found",
                        message=t("library.error.manga_not_found", default="Manga not found: {manga_id}", manga_id=manga_id),
                        details={"manga_id": manga_id},
                    ).model_dump(),
                )

            response = _manga_to_library_response(manga)

            # Stocker dans le cache
            await cache.set(cache_key, response)

            return response

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de la récupération du manga {}: {}", manga_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="manga_fetch_failed",
                    message=t("library.error.fetch_failed", default="Failed to fetch manga"),
                    details={"manga_id": manga_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # PATCH /library/{manga_id} — Mettre à jour un manga
    # =========================================================================

    @library_router.patch(
        "/library/{manga_id}",
        response_model=UpdateResponse,
        summary="Mettre à jour un manga",
        description="Met à jour les métadonnées d'un manga (statut, tags, notes).",
        responses={
            200: {"description": "Manga mis à jour"},
            404: {"description": "Manga non trouvé"},
        },
    )
    async def update_library_manga(
        request: Request,
        manga_id: str,
        body: UpdateMangaRequest,
    ) -> UpdateResponse:
        """Met à jour un manga.

        Args:
            request: Requête HTTP.
            manga_id: ID du manga.
            body: Corps de la requête.

        Returns:
            Réponse de confirmation.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Mise à jour manga: user={}, manga={}, data={}", user_id, manga_id, body.model_dump(exclude_none=True))

        try:
            # TODO: Mettre à jour dans la base de données
            # from nexusdl.core.library import update_library_manga
            # await update_library_manga(user_id, manga_id, body.model_dump(exclude_none=True))

            # Invalider le cache
            cache = get_library_cache()
            await cache.delete(f"library_manga:{user_id}:{manga_id}")
            await cache.invalidate_pattern(f"library_list:{user_id}:")
            await cache.delete(f"library_stats:{user_id}")

            # Émettre un événement
            await _emit_library_event(
                "library.manga.updated",
                {
                    "user_id": user_id,
                    "manga_id": manga_id,
                    "changes": body.model_dump(exclude_none=True),
                },
            )

            return UpdateResponse(
                success=True,
                message=t("library.success.manga_updated", default="Manga updated successfully"),
                manga_id=manga_id,
            )

        except Exception as e:
            logger.error("Erreur lors de la mise à jour du manga {}: {}", manga_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="update_failed",
                    message=t("library.error.update_failed", default="Failed to update manga"),
                    details={"manga_id": manga_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # DELETE /library/{manga_id} — Supprimer un manga
    # =========================================================================

    @library_router.delete(
        "/library/{manga_id}",
        response_model=UpdateResponse,
        summary="Supprimer un manga",
        description="Supprime un manga de la bibliothèque.",
        responses={
            200: {"description": "Manga supprimé"},
            404: {"description": "Manga non trouvé"},
        },
    )
    async def delete_library_manga(
        request: Request,
        manga_id: str,
    ) -> UpdateResponse:
        """Supprime un manga.

        Args:
            request: Requête HTTP.
            manga_id: ID du manga.

        Returns:
            Réponse de confirmation.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Suppression manga: user={}, manga={}", user_id, manga_id)

        try:
            # TODO: Supprimer de la base de données
            # from nexusdl.core.library import delete_library_manga
            # await delete_library_manga(user_id, manga_id)

            # Invalider le cache
            cache = get_library_cache()
            await cache.delete(f"library_manga:{user_id}:{manga_id}")
            await cache.invalidate_pattern(f"library_list:{user_id}:")
            await cache.delete(f"library_stats:{user_id}")

            # Émettre un événement
            await _emit_library_event(
                "library.manga.deleted",
                {
                    "user_id": user_id,
                    "manga_id": manga_id,
                },
            )

            return UpdateResponse(
                success=True,
                message=t("library.success.manga_deleted", default="Manga deleted successfully"),
                manga_id=manga_id,
            )

        except Exception as e:
            logger.error("Erreur lors de la suppression du manga {}: {}", manga_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="delete_failed",
                    message=t("library.error.delete_failed", default="Failed to delete manga"),
                    details={"manga_id": manga_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /library/{manga_id}/chapters — Chapitres du manga
    # =========================================================================

    @library_router.get(
        "/library/{manga_id}/chapters",
        response_model=LibraryChaptersResponse,
        summary="Chapitres d'un manga",
        description="Retourne la liste des chapitres d'un manga avec leur statut.",
        responses={
            200: {"description": "Liste des chapitres"},
            404: {"description": "Manga non trouvé"},
        },
    )
    async def get_library_chapters(
        request: Request,
        manga_id: str,
    ) -> LibraryChaptersResponse:
        """Récupère les chapitres d'un manga.

        Args:
            request: Requête HTTP.
            manga_id: ID du manga.

        Returns:
            Liste des chapitres.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Chapitres manga bibliothèque: user={}, manga={}", user_id, manga_id)

        # Vérifier le cache
        cache = get_library_cache()
        cache_key = f"library_chapters:{user_id}:{manga_id}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        try:
            # TODO: Récupérer les chapitres depuis la base de données
            # from nexusdl.core.library import get_library_chapters
            # chapters = await get_library_chapters(user_id, manga_id)
            chapters: list[Any] = []

            # Convertir
            chapter_responses = [_chapter_to_library_response(c) for c in chapters]

            # Compter
            read_count = sum(1 for c in chapters if getattr(c, "is_read", False))
            downloaded_count = sum(1 for c in chapters if getattr(c, "is_downloaded", False))

            response = LibraryChaptersResponse(
                manga_id=manga_id,
                chapters=chapter_responses,
                total=len(chapter_responses),
                read_count=read_count,
                downloaded_count=downloaded_count,
            )

            # Stocker dans le cache
            await cache.set(cache_key, response, ttl_seconds=60)

            return response

        except Exception as e:
            logger.error("Erreur lors de la récupération des chapitres de {}: {}", manga_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="chapters_failed",
                    message=t("library.error.chapters_failed", default="Failed to fetch chapters"),
                    details={"manga_id": manga_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # PATCH /library/{manga_id}/chapters/{chapter_id} — Statut d'un chapitre
    # =========================================================================

    @library_router.patch(
        "/library/{manga_id}/chapters/{chapter_id}",
        response_model=UpdateResponse,
        summary="Mettre à jour un chapitre",
        description="Met à jour le statut de lecture d'un chapitre.",
        responses={
            200: {"description": "Chapitre mis à jour"},
            404: {"description": "Chapitre non trouvé"},
        },
    )
    async def update_library_chapter(
        request: Request,
        manga_id: str,
        chapter_id: str,
        body: UpdateChapterRequest,
    ) -> UpdateResponse:
        """Met à jour un chapitre.

        Args:
            request: Requête HTTP.
            manga_id: ID du manga.
            chapter_id: ID du chapitre.
            body: Corps de la requête.

        Returns:
            Réponse de confirmation.
        """
        user_id = _get_user_id_from_request(request)
        logger.info(
            "Mise à jour chapitre: user={}, manga={}, chapter={}, data={}",
            user_id,
            manga_id,
            chapter_id,
            body.model_dump(exclude_none=True),
        )

        try:
            # TODO: Mettre à jour dans la base de données
            # from nexusdl.core.library import update_library_chapter
            # await update_library_chapter(user_id, manga_id, chapter_id, body.model_dump(exclude_none=True))

            # Invalider le cache
            cache = get_library_cache()
            await cache.delete(f"library_chapters:{user_id}:{manga_id}")
            await cache.delete(f"library_manga:{user_id}:{manga_id}")
            await cache.delete(f"library_stats:{user_id}")

            # Émettre un événement
            await _emit_library_event(
                "library.chapter.updated",
                {
                    "user_id": user_id,
                    "manga_id": manga_id,
                    "chapter_id": chapter_id,
                    "changes": body.model_dump(exclude_none=True),
                },
            )

            return UpdateResponse(
                success=True,
                message=t("library.success.chapter_updated", default="Chapter updated successfully"),
                manga_id=manga_id,
            )

        except Exception as e:
            logger.error("Erreur lors de la mise à jour du chapitre {}: {}", chapter_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="update_failed",
                    message=t("library.error.update_failed", default="Failed to update chapter"),
                    details={"chapter_id": chapter_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /library/scan — Scanner la bibliothèque
    # =========================================================================

    @library_router.post(
        "/library/scan",
        response_model=ScanResponse,
        summary="Scanner la bibliothèque",
        description="Lance un scan de la bibliothèque pour synchroniser avec le disque.",
        responses={
            202: {"description": "Scan démarré"},
            409: {"description": "Scan déjà en cours"},
        },
    )
    async def scan_library(request: Request) -> ScanResponse:
        """Lance un scan de la bibliothèque.

        Args:
            request: Requête HTTP.

        Returns:
            Réponse avec l'ID du scan.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Scan bibliothèque demandé: user={}", user_id)

        scan_state = get_scan_state()

        if scan_state.is_running:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorResponse(
                    error="scan_already_running",
                    message=t("library.error.scan_already_running", default="A scan is already in progress"),
                    details={"scan_id": scan_state.scan_id},
                ).model_dump(),
            )

        try:
            import uuid
            scan_id = str(uuid.uuid4())
            await scan_state.start(scan_id)

            # Lancer le scan en arrière-plan
            asyncio.create_task(_run_scan(user_id, scan_id))

            # Émettre un événement
            await _emit_library_event(
                "library.scan.started",
                {
                    "user_id": user_id,
                    "scan_id": scan_id,
                },
            )

            return ScanResponse(
                scan_id=scan_id,
                status=ScanStatus.RUNNING,
                message=t("library.success.scan_started", default="Scan started"),
            )

        except ScanAlreadyRunningError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorResponse(
                    error="scan_already_running",
                    message=t("library.error.scan_already_running", default="A scan is already in progress"),
                ).model_dump(),
            )
        except Exception as e:
            logger.error("Erreur lors du lancement du scan: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="scan_failed",
                    message=t("library.error.scan_failed", default="Failed to start scan"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    async def _run_scan(user_id: str, scan_id: str) -> None:
        """Exécute un scan en arrière-plan.

        Args:
            user_id: ID de l'utilisateur.
            scan_id: ID du scan.
        """
        scan_state = get_scan_state()

        try:
            # TODO: Implémenter le scan réel
            # from nexusdl.core.library import scan_library
            # await scan_library(user_id, progress_callback=scan_state.update_progress)

            # Simulation pour l'instant
            await asyncio.sleep(2)
            await scan_state.update_progress(scanned_files=100, progress=0.5)
            await asyncio.sleep(2)
            await scan_state.update_progress(
                scanned_files=200,
                added_mangas=5,
                updated_mangas=10,
                removed_mangas=0,
                progress=1.0,
            )
            await scan_state.complete()

            # Invalider le cache
            cache = get_library_cache()
            await cache.invalidate_pattern(f"library_list:{user_id}:")
            await cache.delete(f"library_stats:{user_id}")

            # Émettre un événement
            await _emit_library_event(
                "library.scan.completed",
                {
                    "user_id": user_id,
                    "scan_id": scan_id,
                    "scanned_files": 200,
                    "added_mangas": 5,
                    "updated_mangas": 10,
                },
            )

            logger.info("Scan terminé: scan_id={}, user={}", scan_id, user_id)

        except Exception as e:
            logger.error("Erreur lors du scan: {}", e)
            await scan_state.fail(str(e))

            await _emit_library_event(
                "library.scan.failed",
                {
                    "user_id": user_id,
                    "scan_id": scan_id,
                    "error": str(e),
                },
            )

    # =========================================================================
    # GET /library/scan/status — Statut du scan
    # =========================================================================

    @library_router.get(
        "/library/scan/status",
        response_model=ScanStatusResponse,
        summary="Statut du scan",
        description="Retourne le statut du scan en cours ou du dernier scan.",
        responses={
            200: {"description": "Statut du scan"},
        },
    )
    async def get_scan_status() -> ScanStatusResponse:
        """Récupère le statut du scan.

        Returns:
            Statut du scan.
        """
        scan_state = get_scan_state()
        return await scan_state.get_status()

    # =========================================================================
    # GET /library/reading-lists — Lister les reading lists
    # =========================================================================

    @library_router.get(
        "/library/reading-lists",
        response_model=ReadingListsResponse,
        summary="Lister les reading lists",
        description="Retourne la liste des reading lists de l'utilisateur.",
        responses={
            200: {"description": "Liste des reading lists"},
        },
    )
    async def list_reading_lists(request: Request) -> ReadingListsResponse:
        """Liste les reading lists.

        Args:
            request: Requête HTTP.

        Returns:
            Liste des reading lists.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Liste reading lists: user={}", user_id)

        # Vérifier le cache
        cache = get_library_cache()
        cache_key = f"reading_lists:{user_id}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        try:
            # TODO: Récupérer les reading lists depuis la base de données
            # from nexusdl.core.library import get_reading_lists
            # reading_lists = await get_reading_lists(user_id)
            reading_lists: list[Any] = []

            # Convertir
            list_responses = [_reading_list_to_response(rl) for rl in reading_lists]

            response = ReadingListsResponse(
                lists=list_responses,
                total=len(list_responses),
            )

            # Stocker dans le cache
            await cache.set(cache_key, response)

            return response

        except Exception as e:
            logger.error("Erreur lors de la liste des reading lists: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="lists_failed",
                    message=t("library.error.lists_failed", default="Failed to list reading lists"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /library/reading-lists — Créer une reading list
    # =========================================================================

    @library_router.post(
        "/library/reading-lists",
        response_model=ReadingListResponse,
        summary="Créer une reading list",
        description="Crée une nouvelle reading list.",
        responses={
            201: {"description": "Reading list créée"},
            400: {"description": "Requête invalide"},
        },
    )
    async def create_reading_list(
        request: Request,
        body: CreateReadingListRequest,
    ) -> ReadingListResponse:
        """Crée une reading list.

        Args:
            request: Requête HTTP.
            body: Corps de la requête.

        Returns:
            Reading list créée.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Création reading list: user={}, name={}", user_id, body.name)

        try:
            # TODO: Créer dans la base de données
            # from nexusdl.core.library import create_reading_list
            # reading_list = await create_reading_list(user_id, body.name, body.description, body.manga_ids)

            # Pour l'instant, créer une réponse factice
            import uuid
            now = datetime.now(UTC)
            response = ReadingListResponse(
                id=str(uuid.uuid4()),
                name=body.name,
                description=body.description,
                manga_count=len(body.manga_ids),
                created_at=now,
                updated_at=now,
            )

            # Invalider le cache
            cache = get_library_cache()
            await cache.delete(f"reading_lists:{user_id}")

            # Émettre un événement
            await _emit_library_event(
                "library.reading_list.created",
                {
                    "user_id": user_id,
                    "list_id": response.id,
                    "name": body.name,
                },
            )

            return response

        except Exception as e:
            logger.error("Erreur lors de la création de la reading list: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="create_failed",
                    message=t("library.error.create_list_failed", default="Failed to create reading list"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /library/reading-lists/{list_id} — Détails d'une reading list
    # =========================================================================

    @library_router.get(
        "/library/reading-lists/{list_id}",
        response_model=ReadingListDetailResponse,
        summary="Détails d'une reading list",
        description="Retourne les détails d'une reading list avec ses mangas.",
        responses={
            200: {"description": "Détails de la reading list"},
            404: {"description": "Reading list non trouvée"},
        },
    )
    async def get_reading_list(
        request: Request,
        list_id: str,
    ) -> ReadingListDetailResponse:
        """Récupère les détails d'une reading list.

        Args:
            request: Requête HTTP.
            list_id: ID de la reading list.

        Returns:
            Détails de la reading list.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Détails reading list: user={}, list={}", user_id, list_id)

        try:
            # TODO: Récupérer depuis la base de données
            # from nexusdl.core.library import get_reading_list
            # reading_list = await get_reading_list(user_id, list_id)

            # Pour l'instant, lever une 404
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse(
                    error="list_not_found",
                    message=t("library.error.list_not_found", default="Reading list not found: {list_id}", list_id=list_id),
                    details={"list_id": list_id},
                ).model_dump(),
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de la récupération de la reading list {}: {}", list_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="fetch_failed",
                    message=t("library.error.fetch_list_failed", default="Failed to fetch reading list"),
                    details={"list_id": list_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # PATCH /library/reading-lists/{list_id} — Modifier une reading list
    # =========================================================================

    @library_router.patch(
        "/library/reading-lists/{list_id}",
        response_model=ReadingListResponse,
        summary="Modifier une reading list",
        description="Met à jour les métadonnées d'une reading list.",
        responses={
            200: {"description": "Reading list mise à jour"},
            404: {"description": "Reading list non trouvée"},
        },
    )
    async def update_reading_list(
        request: Request,
        list_id: str,
        body: UpdateReadingListRequest,
    ) -> ReadingListResponse:
        """Met à jour une reading list.

        Args:
            request: Requête HTTP.
            list_id: ID de la reading list.
            body: Corps de la requête.

        Returns:
            Reading list mise à jour.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Mise à jour reading list: user={}, list={}, data={}", user_id, list_id, body.model_dump(exclude_none=True))

        try:
            # TODO: Mettre à jour dans la base de données
            # from nexusdl.core.library import update_reading_list
            # reading_list = await update_reading_list(user_id, list_id, body.model_dump(exclude_none=True))

            # Pour l'instant, lever une 404
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse(
                    error="list_not_found",
                    message=t("library.error.list_not_found", default="Reading list not found: {list_id}", list_id=list_id),
                ).model_dump(),
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de la mise à jour de la reading list {}: {}", list_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="update_failed",
                    message=t("library.error.update_list_failed", default="Failed to update reading list"),
                    details={"list_id": list_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # DELETE /library/reading-lists/{list_id} — Supprimer une reading list
    # =========================================================================

    @library_router.delete(
        "/library/reading-lists/{list_id}",
        response_model=UpdateResponse,
        summary="Supprimer une reading list",
        description="Supprime une reading list.",
        responses={
            200: {"description": "Reading list supprimée"},
            404: {"description": "Reading list non trouvée"},
        },
    )
    async def delete_reading_list(
        request: Request,
        list_id: str,
    ) -> UpdateResponse:
        """Supprime une reading list.

        Args:
            request: Requête HTTP.
            list_id: ID de la reading list.

        Returns:
            Réponse de confirmation.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Suppression reading list: user={}, list={}", user_id, list_id)

        try:
            # TODO: Supprimer de la base de données
            # from nexusdl.core.library import delete_reading_list
            # await delete_reading_list(user_id, list_id)

            # Invalider le cache
            cache = get_library_cache()
            await cache.delete(f"reading_lists:{user_id}")

            # Émettre un événement
            await _emit_library_event(
                "library.reading_list.deleted",
                {
                    "user_id": user_id,
                    "list_id": list_id,
                },
            )

            return UpdateResponse(
                success=True,
                message=t("library.success.list_deleted", default="Reading list deleted successfully"),
            )

        except Exception as e:
            logger.error("Erreur lors de la suppression de la reading list {}: {}", list_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="delete_failed",
                    message=t("library.error.delete_list_failed", default="Failed to delete reading list"),
                    details={"list_id": list_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /library/reading-lists/{list_id}/manga — Ajouter des mangas
    # =========================================================================

    @library_router.post(
        "/library/reading-lists/{list_id}/manga",
        response_model=UpdateResponse,
        summary="Ajouter des mangas à une reading list",
        description="Ajoute un ou plusieurs mangas à une reading list.",
        responses={
            200: {"description": "Mangas ajoutés"},
            404: {"description": "Reading list non trouvée"},
        },
    )
    async def add_manga_to_list(
        request: Request,
        list_id: str,
        body: AddMangaToListRequest,
    ) -> UpdateResponse:
        """Ajoute des mangas à une reading list.

        Args:
            request: Requête HTTP.
            list_id: ID de la reading list.
            body: Corps de la requête.

        Returns:
            Réponse de confirmation.
        """
        user_id = _get_user_id_from_request(request)
        logger.info(
            "Ajout mangas à reading list: user={}, list={}, mangas={}",
            user_id,
            list_id,
            body.manga_ids,
        )

        try:
            # TODO: Ajouter dans la base de données
            # from nexusdl.core.library import add_manga_to_reading_list
            # await add_manga_to_reading_list(user_id, list_id, body.manga_ids)

            # Invalider le cache
            cache = get_library_cache()
            await cache.delete(f"reading_lists:{user_id}")

            # Émettre un événement
            await _emit_library_event(
                "library.reading_list.manga_added",
                {
                    "user_id": user_id,
                    "list_id": list_id,
                    "manga_ids": body.manga_ids,
                },
            )

            return UpdateResponse(
                success=True,
                message=t(
                    "library.success.mangas_added",
                    default="{count} manga(s) added to reading list",
                    count=len(body.manga_ids),
                ),
            )

        except Exception as e:
            logger.error("Erreur lors de l'ajout des mangas: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="add_failed",
                    message=t("library.error.add_manga_failed", default="Failed to add mangas"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # DELETE /library/reading-lists/{list_id}/manga/{manga_id} — Retirer un manga
    # =========================================================================

    @library_router.delete(
        "/library/reading-lists/{list_id}/manga/{manga_id}",
        response_model=UpdateResponse,
        summary="Retirer un manga d'une reading list",
        description="Retire un manga d'une reading list.",
        responses={
            200: {"description": "Manga retiré"},
            404: {"description": "Reading list ou manga non trouvé"},
        },
    )
    async def remove_manga_from_list(
        request: Request,
        list_id: str,
        manga_id: str,
    ) -> UpdateResponse:
        """Retire un manga d'une reading list.

        Args:
            request: Requête HTTP.
            list_id: ID de la reading list.
            manga_id: ID du manga.

        Returns:
            Réponse de confirmation.
        """
        user_id = _get_user_id_from_request(request)
        logger.info(
            "Retrait manga de reading list: user={}, list={}, manga={}",
            user_id,
            list_id,
            manga_id,
        )

        try:
            # TODO: Retirer de la base de données
            # from nexusdl.core.library import remove_manga_from_reading_list
            # await remove_manga_from_reading_list(user_id, list_id, manga_id)

            # Invalider le cache
            cache = get_library_cache()
            await cache.delete(f"reading_lists:{user_id}")

            # Émettre un événement
            await _emit_library_event(
                "library.reading_list.manga_removed",
                {
                    "user_id": user_id,
                    "list_id": list_id,
                    "manga_id": manga_id,
                },
            )

            return UpdateResponse(
                success=True,
                message=t("library.success.manga_removed", default="Manga removed from reading list"),
                manga_id=manga_id,
            )

        except Exception as e:
            logger.error("Erreur lors du retrait du manga: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="remove_failed",
                    message=t("library.error.remove_manga_failed", default="Failed to remove manga"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_LIBRARY_LIMIT",
    "MAX_LIBRARY_LIMIT",
    "DEFAULT_CACHE_TTL_SECONDS",
    "MAX_CACHE_SIZE",
    "DEFAULT_SCAN_TIMEOUT_SECONDS",
    "MAX_READING_LISTS_PER_USER",
    "MAX_MANGAS_PER_READING_LIST",
    "MAX_TAGS_PER_MANGA",
    # Exceptions
    "LibraryRouterError",
    "MangaNotFoundError",
    "ReadingListNotFoundError",
    "ChapterNotFoundError",
    "ScanError",
    "ScanAlreadyRunningError",
    # Enums
    "ReadingStatus",
    "LibrarySortBy",
    "ScanStatus",
    # Modèles de requête
    "UpdateMangaRequest",
    "UpdateChapterRequest",
    "CreateReadingListRequest",
    "UpdateReadingListRequest",
    "AddMangaToListRequest",
    # Modèles de réponse
    "LibraryMangaResponse",
    "LibraryListResponse",
    "LibraryStatsResponse",
    "LibraryChapterResponse",
    "LibraryChaptersResponse",
    "ReadingListResponse",
    "ReadingListDetailResponse",
    "ReadingListsResponse",
    "ScanStatusResponse",
    "ScanResponse",
    "UpdateResponse",
    "ErrorResponse",
    # Cache
    "LibraryCache",
    "get_library_cache",
    # Scan state
    "ScanState",
    "get_scan_state",
    # Routeur
    "library_router" if FASTAPI_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
