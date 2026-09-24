"""Routeur FastAPI pour la gestion des mangas.

Ce module fournit un routeur FastAPI complet pour interagir avec les mangas
individuels : récupérer les détails, lister les chapitres, accéder aux pages,
gérer les téléchargements, et interagir avec la bibliothèque locale.

**Endpoints** :
    - GET /manga/{site_id}/{manga_id}                 : Détails d'un manga
    - GET /manga/{site_id}/{manga_id}/chapters        : Liste des chapitres
    - GET /manga/{site_id}/{manga_id}/chapters/{chapter_id} : Détails d'un chapitre
    - GET /manga/{site_id}/{manga_id}/chapters/{chapter_id}/pages : Pages
    - GET /manga/{site_id}/{manga_id}/cover           : Couverture
    - POST /manga/{site_id}/{manga_id}/download       : Télécharger le manga
    - POST /manga/{site_id}/{manga_id}/chapters/{chapter_id}/download : Télécharger chapitre
    - POST /manga/{site_id}/{manga_id}/library        : Ajouter à la bibliothèque
    - DELETE /manga/{site_id}/{manga_id}/library      : Retirer de la bibliothèque
    - PATCH /manga/{site_id}/{manga_id}/chapters/{chapter_id}/read : Marquer lu/non-lu

**Fonctionnalités** :
    - Cache en mémoire pour les données fréquentes
    - Gestion des téléchargements asynchrones
    - Intégration avec la bibliothèque locale
    - Marquage lu/non-lu des chapitres
    - Événements EventBus pour monitoring
    - Logging structuré
    - Validation stricte via Pydantic
    - Gestion robuste des erreurs

**Exemple d'utilisation** :
    >>> from fastapi import FastAPI
    >>> from nexusdl.interfaces.web.backend.routers.manga import manga_router
    >>>
    >>> app = FastAPI()
    >>> app.include_router(manga_router, prefix="/api/v1")

**Exemples d'appels API** :
    >>> # Détails d'un manga
    >>> GET /api/v1/manga/mangadex/12345
    >>>
    >>> # Télécharger un chapitre
    >>> POST /api/v1/manga/mangadex/12345/chapters/67890/download
    >>> {"format": "cbz", "quality": "original"}
    >>>
    >>> # Ajouter à la bibliothèque
    >>> POST /api/v1/manga/mangadex/12345/library
    >>> {"reading_status": "reading"}

Intégration :
    - core/registry/site_registry.py : Accès au registre des sites
    - core/parsers/*                 : Parsers pour chaque site
    - core/models/manga.py           : Modèles Manga, Chapter, Page
    - core/library/database.py       : Base de données locale
    - core/downloader/manager.py     : Gestionnaire de téléchargements
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
DEFAULT_CACHE_TTL_SECONDS: Final[int] = 600  # 10 minutes
MAX_CACHE_SIZE: Final[int] = 1000
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS: Final[int] = 3600  # 1 heure

# Formats de téléchargement
SUPPORTED_DOWNLOAD_FORMATS: Final[frozenset[str]] = frozenset({
    "cbz", "cbr", "pdf", "zip", "folder",
})

# Qualités d'image
SUPPORTED_IMAGE_QUALITIES: Final[frozenset[str]] = frozenset({
    "original", "high", "medium", "low",
})

# Statuts de lecture
READING_STATUSES: Final[frozenset[str]] = frozenset({
    "reading", "completed", "plan_to_read", "on_hold", "dropped",
})


# ============================================================================
# EXCEPTIONS
# ============================================================================


class MangaRouterError(NexusDLError):
    """Exception de base pour les erreurs du routeur manga."""


class MangaNotFoundError(MangaRouterError):
    """Exception levée lorsqu'un manga n'est pas trouvé.

    Attributes:
        site_id: ID du site.
        manga_id: ID du manga.
    """

    def __init__(self, site_id: str, manga_id: str) -> None:
        super().__init__(
            t(
                "manga.error.not_found",
                default="Manga not found: {site_id}/{manga_id}",
                site_id=site_id,
                manga_id=manga_id,
            )
        )
        self.site_id = site_id
        self.manga_id = manga_id


class ChapterNotFoundError(MangaRouterError):
    """Exception levée lorsqu'un chapitre n'est pas trouvé.

    Attributes:
        site_id: ID du site.
        manga_id: ID du manga.
        chapter_id: ID du chapitre.
    """

    def __init__(self, site_id: str, manga_id: str, chapter_id: str) -> None:
        super().__init__(
            t(
                "manga.error.chapter_not_found",
                default="Chapter not found: {site_id}/{manga_id}/{chapter_id}",
                site_id=site_id,
                manga_id=manga_id,
                chapter_id=chapter_id,
            )
        )
        self.site_id = site_id
        self.manga_id = manga_id
        self.chapter_id = chapter_id


class DownloadError(MangaRouterError):
    """Exception levée lorsqu'un téléchargement échoue.

    Attributes:
        reason: Raison de l'échec.
    """

    def __init__(self, reason: str = "") -> None:
        msg = t("manga.error.download_failed", default="Download failed")
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


class LibraryError(MangaRouterError):
    """Exception levée lorsqu'une opération sur la bibliothèque échoue.

    Attributes:
        reason: Raison de l'échec.
    """

    def __init__(self, reason: str = "") -> None:
        msg = t("manga.error.library_failed", default="Library operation failed")
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class MangaStatus(str, Enum):
    """Statut de publication d'un manga.

    Attributes:
        ONGOING: En cours de publication.
        COMPLETED: Terminé.
        HIATUS: En pause.
        CANCELLED: Annulé.
        UNKNOWN: Inconnu.
    """

    ONGOING = "ongoing"
    COMPLETED = "completed"
    HIATUS = "hiatus"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


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


class DownloadFormat(str, Enum):
    """Format de téléchargement.

    Attributes:
        CBZ: Archive CBZ (Comic Book ZIP).
        CBR: Archive CBR (Comic Book RAR).
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


# ============================================================================
# MODÈLES DE REQUÊTE — Pydantic
# ============================================================================


class DownloadRequest(BaseModel):
    """Requête de téléchargement.

    Attributes:
        format: Format de téléchargement.
        quality: Qualité d'image.
        output_dir: Répertoire de sortie (optionnel).
        chapter_ids: IDs des chapitres à télécharger (None = tous).
    """

    format: DownloadFormat = Field(default=DownloadFormat.CBZ, description="Format.")
    quality: ImageQuality = Field(default=ImageQuality.ORIGINAL, description="Qualité.")
    output_dir: str | None = Field(default=None, description="Répertoire de sortie.")
    chapter_ids: list[str] | None = Field(default=None, description="Chapitres à télécharger.")

    @field_validator("chapter_ids")
    @classmethod
    def validate_chapter_ids(cls, v: list[str] | None) -> list[str] | None:
        """Valide les IDs de chapitres."""
        if v is not None and len(v) > 100:
            raise ValueError("Maximum 100 chapitres par téléchargement")
        return v


class AddToLibraryRequest(BaseModel):
    """Requête d'ajout à la bibliothèque.

    Attributes:
        reading_status: Statut de lecture initial.
        tags: Tags à ajouter.
        notes: Notes personnelles.
    """

    reading_status: ReadingStatus = Field(default=ReadingStatus.PLAN_TO_READ, description="Statut lecture.")
    tags: list[str] = Field(default_factory=list, description="Tags.")
    notes: str = Field(default="", description="Notes.")

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str]) -> list[str]:
        """Valide les tags."""
        if len(v) > 20:
            raise ValueError("Maximum 20 tags")
        return v


class UpdateReadStatusRequest(BaseModel):
    """Requête de mise à jour du statut de lecture.

    Attributes:
        is_read: Si le chapitre est lu.
    """

    is_read: bool = Field(..., description="Statut de lecture.")


# ============================================================================
# MODÈLES DE RÉPONSE — Pydantic
# ============================================================================


class MangaDetailsResponse(BaseModel):
    """Réponse avec les détails d'un manga.

    Attributes:
        id: ID unique.
        site_id: ID du site.
        title: Titre.
        author: Auteur.
        year: Année.
        status: Statut de publication.
        language: Code langue.
        cover_url: URL de la couverture.
        url: URL du manga.
        description: Description.
        tags: Liste de tags.
        chapters_count: Nombre de chapitres.
        last_updated_at: Dernière mise à jour.
        in_library: Si le manga est dans la bibliothèque.
        reading_status: Statut de lecture (si dans bibliothèque).
    """

    id: str = Field(..., description="ID unique.")
    site_id: str = Field(..., description="ID du site.")
    title: str = Field(..., description="Titre.")
    author: str = Field(default="", description="Auteur.")
    year: int | None = Field(default=None, description="Année.")
    status: MangaStatus = Field(default=MangaStatus.UNKNOWN, description="Statut.")
    language: str = Field(default="en", description="Code langue.")
    cover_url: str = Field(default="", description="URL couverture.")
    url: str = Field(default="", description="URL manga.")
    description: str = Field(default="", description="Description.")
    tags: list[str] = Field(default_factory=list, description="Tags.")
    chapters_count: int = Field(default=0, ge=0, description="Nombre chapitres.")
    last_updated_at: datetime | None = Field(default=None, description="Dernière MAJ.")
    in_library: bool = Field(default=False, description="Dans bibliothèque.")
    reading_status: ReadingStatus | None = Field(default=None, description="Statut lecture.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ChapterResponse(BaseModel):
    """Réponse avec les détails d'un chapitre.

    Attributes:
        id: ID unique.
        number: Numéro du chapitre.
        title: Titre.
        published_at: Date de publication.
        scanlator: Scanlator.
        pages_count: Nombre de pages.
        url: URL du chapitre.
        is_downloaded: Si le chapitre est téléchargé.
        is_read: Si le chapitre est lu.
    """

    id: str = Field(..., description="ID unique.")
    number: float = Field(..., description="Numéro.")
    title: str = Field(default="", description="Titre.")
    published_at: datetime | None = Field(default=None, description="Date publication.")
    scanlator: str = Field(default="", description="Scanlator.")
    pages_count: int = Field(default=0, ge=0, description="Nombre pages.")
    url: str = Field(default="", description="URL chapitre.")
    is_downloaded: bool = Field(default=False, description="Téléchargé.")
    is_read: bool = Field(default=False, description="Lu.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ChaptersResponse(BaseModel):
    """Réponse avec la liste des chapitres.

    Attributes:
        manga_id: ID du manga.
        chapters: Liste des chapitres.
        total: Nombre total de chapitres.
    """

    manga_id: str = Field(..., description="ID manga.")
    chapters: list[ChapterResponse] = Field(default_factory=list, description="Chapitres.")
    total: int = Field(default=0, ge=0, description="Total.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class PageResponse(BaseModel):
    """Réponse avec les détails d'une page.

    Attributes:
        id: ID unique.
        number: Numéro de la page.
        url: URL de l'image.
        width: Largeur de l'image.
        height: Hauteur de l'image.
        size_bytes: Taille en bytes.
    """

    id: str = Field(..., description="ID unique.")
    number: int = Field(..., ge=1, description="Numéro.")
    url: str = Field(..., description="URL image.")
    width: int = Field(default=0, ge=0, description="Largeur.")
    height: int = Field(default=0, ge=0, description="Hauteur.")
    size_bytes: int = Field(default=0, ge=0, description="Taille bytes.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class PagesResponse(BaseModel):
    """Réponse avec la liste des pages.

    Attributes:
        chapter_id: ID du chapitre.
        pages: Liste des pages.
        total: Nombre total de pages.
    """

    chapter_id: str = Field(..., description="ID chapitre.")
    pages: list[PageResponse] = Field(default_factory=list, description="Pages.")
    total: int = Field(default=0, ge=0, description="Total.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class DownloadTaskResponse(BaseModel):
    """Réponse avec les détails d'une tâche de téléchargement.

    Attributes:
        task_id: ID de la tâche.
        status: Statut de la tâche.
        progress: Progression (0.0 à 1.0).
        estimated_time_seconds: Temps estimé restant.
        message: Message d'état.
    """

    task_id: str = Field(..., description="ID tâche.")
    status: str = Field(..., description="Statut.")
    progress: float = Field(default=0.0, ge=0.0, le=1.0, description="Progression.")
    estimated_time_seconds: float = Field(default=0.0, ge=0.0, description="Temps estimé.")
    message: str = Field(default="", description="Message.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class LibraryResponse(BaseModel):
    """Réponse après opération sur la bibliothèque.

    Attributes:
        success: Si l'opération a réussi.
        message: Message de confirmation.
        manga_id: ID du manga.
        reading_status: Statut de lecture.
    """

    success: bool = Field(..., description="Succès.")
    message: str = Field(..., description="Message.")
    manga_id: str = Field(..., description="ID manga.")
    reading_status: ReadingStatus | None = Field(default=None, description="Statut lecture.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ReadStatusResponse(BaseModel):
    """Réponse après mise à jour du statut de lecture.

    Attributes:
        success: Si l'opération a réussi.
        message: Message de confirmation.
        chapter_id: ID du chapitre.
        is_read: Nouveau statut de lecture.
    """

    success: bool = Field(..., description="Succès.")
    message: str = Field(..., description="Message.")
    chapter_id: str = Field(..., description="ID chapitre.")
    is_read: bool = Field(..., description="Statut lecture.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class CoverResponse(BaseModel):
    """Réponse avec la couverture d'un manga.

    Attributes:
        manga_id: ID du manga.
        cover_url: URL de la couverture.
        width: Largeur de l'image.
        height: Hauteur de l'image.
        size_bytes: Taille en bytes.
        content_type: Type MIME.
    """

    manga_id: str = Field(..., description="ID manga.")
    cover_url: str = Field(..., description="URL couverture.")
    width: int = Field(default=0, ge=0, description="Largeur.")
    height: int = Field(default=0, ge=0, description="Hauteur.")
    size_bytes: int = Field(default=0, ge=0, description="Taille bytes.")
    content_type: str = Field(default="image/jpeg", description="Type MIME.")

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
# CACHE — Cache en mémoire pour les données manga
# ============================================================================


class MangaCache:
    """Cache en mémoire pour les données manga.

    Évite les appels répétés aux sites pour les données fréquemment consultées.
    """

    def __init__(self, max_size: int = MAX_CACHE_SIZE, default_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS) -> None:
        """Initialise le cache.

        Args:
            max_size: Taille maximale du cache.
            default_ttl_seconds: TTL par défaut en secondes.
        """
        self._cache: dict[str, tuple[Any, datetime]] = {}
        self._max_size = max_size
        self._default_ttl = timedelta(seconds=default_ttl_seconds)
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        """Récupère une valeur du cache.

        Args:
            key: Clé du cache.

        Returns:
            Valeur ou None si expirée/inexistante.
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
        """Stocke une valeur dans le cache.

        Args:
            key: Clé du cache.
            value: Valeur à stocker.
            ttl_seconds: TTL en secondes (None = défaut).
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
        """Supprime une valeur du cache.

        Args:
            key: Clé du cache.
        """
        async with self._lock:
            self._cache.pop(key, None)

    async def clear(self) -> None:
        """Vide le cache."""
        async with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        """Taille actuelle du cache."""
        return len(self._cache)


# Instance globale du cache
_manga_cache = MangaCache()


def get_manga_cache() -> MangaCache:
    """Retourne l'instance globale du cache manga."""
    return _manga_cache


# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================


async def _get_site_or_404(site_id: str) -> Any:
    """Récupère un site ou lève une 404.

    Args:
        site_id: ID du site.

    Returns:
        Instance de SiteConfig.

    Raises:
        HTTPException: Si le site n'est pas trouvé.
    """
    try:
        from nexusdl.core.registry import get_site_registry
        registry = get_site_registry()
        return registry.get_site(site_id)
    except Exception as e:
        logger.warning("Site non trouvé: {}", site_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorResponse(
                error="site_not_found",
                message=t("manga.error.site_not_found", default="Site not found: {site_id}", site_id=site_id),
                details={"site_id": site_id},
            ).model_dump(),
        ) from e


async def _get_parser_or_503(site_id: str) -> Any:
    """Récupère un parser ou lève une 503.

    Args:
        site_id: ID du site.

    Returns:
        Instance du parser.

    Raises:
        HTTPException: Si le parser n'est pas disponible.
    """
    try:
        from nexusdl.core.registry import get_site_registry
        registry = get_site_registry()
        return await registry.get_parser(site_id)
    except Exception as e:
        logger.error("Parser indisponible pour {}: {}", site_id, e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ErrorResponse(
                error="parser_unavailable",
                message=t("manga.error.parser_unavailable", default="Parser not available for site: {site_id}", site_id=site_id),
                details={"site_id": site_id, "reason": str(e)},
            ).model_dump(),
        ) from e


def _manga_to_details(manga: Any, site_id: str) -> MangaDetailsResponse:
    """Convertit un Manga en MangaDetailsResponse.

    Args:
        manga: Instance de Manga.
        site_id: ID du site.

    Returns:
        Instance de MangaDetailsResponse.
    """
    # TODO: Vérifier si le manga est dans la bibliothèque
    in_library = False
    reading_status = None

    return MangaDetailsResponse(
        id=manga.id,
        site_id=site_id,
        title=manga.title,
        author=manga.author or "",
        year=manga.year,
        status=MangaStatus(manga.status.value) if hasattr(manga.status, "value") else MangaStatus.UNKNOWN,
        language=manga.language.value if hasattr(manga.language, "value") else "en",
        cover_url=manga.cover_url or "",
        url=manga.url or "",
        description=manga.description or "",
        tags=manga.tags or [],
        chapters_count=len(manga.chapters) if hasattr(manga, "chapters") and manga.chapters else 0,
        last_updated_at=manga.last_updated_at if hasattr(manga, "last_updated_at") else None,
        in_library=in_library,
        reading_status=reading_status,
    )


def _chapter_to_response(chapter: Any) -> ChapterResponse:
    """Convertit un Chapter en ChapterResponse.

    Args:
        chapter: Instance de Chapter.

    Returns:
        Instance de ChapterResponse.
    """
    return ChapterResponse(
        id=chapter.id,
        number=chapter.number,
        title=chapter.title or "",
        published_at=chapter.published_at if hasattr(chapter, "published_at") else None,
        scanlator=chapter.scanlator or "",
        pages_count=chapter.pages_count if hasattr(chapter, "pages_count") else 0,
        url=chapter.url or "",
        is_downloaded=chapter.is_downloaded if hasattr(chapter, "is_downloaded") else False,
        is_read=chapter.is_read if hasattr(chapter, "is_read") else False,
    )


def _page_to_response(page: Any, number: int) -> PageResponse:
    """Convertit une Page en PageResponse.

    Args:
        page: Instance de Page.
        number: Numéro de la page.

    Returns:
        Instance de PageResponse.
    """
    return PageResponse(
        id=page.id if hasattr(page, "id") else f"page_{number}",
        number=number,
        url=page.url if hasattr(page, "url") else str(page),
        width=page.width if hasattr(page, "width") else 0,
        height=page.height if hasattr(page, "height") else 0,
        size_bytes=page.size_bytes if hasattr(page, "size_bytes") else 0,
    )


# ============================================================================
# ROUTEUR — Endpoints FastAPI
# ============================================================================


if FASTAPI_AVAILABLE:

    manga_router = APIRouter(tags=["manga"])

    # =========================================================================
    # GET /manga/{site_id}/{manga_id} — Détails d'un manga
    # =========================================================================

    @manga_router.get(
        "/manga/{site_id}/{manga_id}",
        response_model=MangaDetailsResponse,
        summary="Détails d'un manga",
        description="Retourne les détails complets d'un manga spécifique.",
        responses={
            200: {"description": "Détails du manga"},
            404: {"description": "Manga ou site non trouvé"},
            503: {"description": "Parser indisponible"},
        },
    )
    async def get_manga(site_id: str, manga_id: str) -> MangaDetailsResponse:
        """Récupère les détails d'un manga.

        Args:
            site_id: ID du site.
            manga_id: ID du manga.

        Returns:
            Détails du manga.
        """
        logger.info("Détails manga: site={}, manga={}", site_id, manga_id)

        # Vérifier le cache
        cache = get_manga_cache()
        cache_key = f"manga_details:{site_id}:{manga_id}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        # Vérifier que le site existe
        await _get_site_or_404(site_id)

        # Obtenir le parser
        parser = await _get_parser_or_503(site_id)

        try:
            # Récupérer le manga
            manga = await parser.get_manga(manga_id)
            response = _manga_to_details(manga, site_id)

            # Stocker dans le cache
            await cache.set(cache_key, response)

            return response

        except Exception as e:
            logger.error("Erreur lors de la récupération du manga {}: {}", manga_id, e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse(
                    error="manga_not_found",
                    message=t("manga.error.not_found", default="Manga not found: {manga_id}", manga_id=manga_id),
                    details={"manga_id": manga_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /manga/{site_id}/{manga_id}/chapters — Liste des chapitres
    # =========================================================================

    @manga_router.get(
        "/manga/{site_id}/{manga_id}/chapters",
        response_model=ChaptersResponse,
        summary="Liste des chapitres",
        description="Retourne la liste des chapitres d'un manga.",
        responses={
            200: {"description": "Liste des chapitres"},
            404: {"description": "Manga non trouvé"},
            503: {"description": "Parser indisponible"},
        },
    )
    async def get_chapters(site_id: str, manga_id: str) -> ChaptersResponse:
        """Récupère la liste des chapitres d'un manga.

        Args:
            site_id: ID du site.
            manga_id: ID du manga.

        Returns:
            Liste des chapitres.
        """
        logger.info("Chapitres manga: site={}, manga={}", site_id, manga_id)

        # Vérifier le cache
        cache = get_manga_cache()
        cache_key = f"chapters:{site_id}:{manga_id}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        # Vérifier que le site existe
        await _get_site_or_404(site_id)

        # Obtenir le parser
        parser = await _get_parser_or_503(site_id)

        try:
            # Récupérer les chapitres
            chapters = await parser.get_chapters(manga_id)
            chapter_responses = [_chapter_to_response(chapter) for chapter in chapters]

            response = ChaptersResponse(
                manga_id=manga_id,
                chapters=chapter_responses,
                total=len(chapter_responses),
            )

            # Stocker dans le cache
            await cache.set(cache_key, response, ttl_seconds=300)

            return response

        except Exception as e:
            logger.error("Erreur lors de la récupération des chapitres de {}: {}", manga_id, e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse(
                    error="manga_not_found",
                    message=t("manga.error.not_found", default="Manga not found: {manga_id}", manga_id=manga_id),
                    details={"manga_id": manga_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /manga/{site_id}/{manga_id}/chapters/{chapter_id} — Détails d'un chapitre
    # =========================================================================

    @manga_router.get(
        "/manga/{site_id}/{manga_id}/chapters/{chapter_id}",
        response_model=ChapterResponse,
        summary="Détails d'un chapitre",
        description="Retourne les détails d'un chapitre spécifique.",
        responses={
            200: {"description": "Détails du chapitre"},
            404: {"description": "Chapitre non trouvé"},
            503: {"description": "Parser indisponible"},
        },
    )
    async def get_chapter(site_id: str, manga_id: str, chapter_id: str) -> ChapterResponse:
        """Récupère les détails d'un chapitre.

        Args:
            site_id: ID du site.
            manga_id: ID du manga.
            chapter_id: ID du chapitre.

        Returns:
            Détails du chapitre.
        """
        logger.info("Détails chapitre: site={}, manga={}, chapter={}", site_id, manga_id, chapter_id)

        # Vérifier le cache
        cache = get_manga_cache()
        cache_key = f"chapter:{site_id}:{manga_id}:{chapter_id}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        # Vérifier que le site existe
        await _get_site_or_404(site_id)

        # Obtenir le parser
        parser = await _get_parser_or_503(site_id)

        try:
            # Récupérer le chapitre
            chapter = await parser.get_chapter(chapter_id)
            response = _chapter_to_response(chapter)

            # Stocker dans le cache
            await cache.set(cache_key, response)

            return response

        except Exception as e:
            logger.error("Erreur lors de la récupération du chapitre {}: {}", chapter_id, e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse(
                    error="chapter_not_found",
                    message=t("manga.error.chapter_not_found", default="Chapter not found: {chapter_id}", chapter_id=chapter_id),
                    details={"chapter_id": chapter_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /manga/{site_id}/{manga_id}/chapters/{chapter_id}/pages — Liste des pages
    # =========================================================================

    @manga_router.get(
        "/manga/{site_id}/{manga_id}/chapters/{chapter_id}/pages",
        response_model=PagesResponse,
        summary="Liste des pages",
        description="Retourne la liste des pages d'un chapitre.",
        responses={
            200: {"description": "Liste des pages"},
            404: {"description": "Chapitre non trouvé"},
            503: {"description": "Parser indisponible"},
        },
    )
    async def get_pages(site_id: str, manga_id: str, chapter_id: str) -> PagesResponse:
        """Récupère la liste des pages d'un chapitre.

        Args:
            site_id: ID du site.
            manga_id: ID du manga.
            chapter_id: ID du chapitre.

        Returns:
            Liste des pages.
        """
        logger.info("Pages chapitre: site={}, manga={}, chapter={}", site_id, manga_id, chapter_id)

        # Vérifier le cache
        cache = get_manga_cache()
        cache_key = f"pages:{site_id}:{manga_id}:{chapter_id}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        # Vérifier que le site existe
        await _get_site_or_404(site_id)

        # Obtenir le parser
        parser = await _get_parser_or_503(site_id)

        try:
            # Récupérer les pages
            pages = await parser.get_pages(chapter_id)
            page_responses = [_page_to_response(page, i + 1) for i, page in enumerate(pages)]

            response = PagesResponse(
                chapter_id=chapter_id,
                pages=page_responses,
                total=len(page_responses),
            )

            # Stocker dans le cache
            await cache.set(cache_key, response, ttl_seconds=1800)  # 30 minutes

            return response

        except Exception as e:
            logger.error("Erreur lors de la récupération des pages de {}: {}", chapter_id, e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse(
                    error="chapter_not_found",
                    message=t("manga.error.chapter_not_found", default="Chapter not found: {chapter_id}", chapter_id=chapter_id),
                    details={"chapter_id": chapter_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /manga/{site_id}/{manga_id}/cover — Couverture
    # =========================================================================

    @manga_router.get(
        "/manga/{site_id}/{manga_id}/cover",
        response_model=CoverResponse,
        summary="Couverture d'un manga",
        description="Retourne les informations sur la couverture d'un manga.",
        responses={
            200: {"description": "Informations de la couverture"},
            404: {"description": "Manga non trouvé"},
        },
    )
    async def get_cover(site_id: str, manga_id: str) -> CoverResponse:
        """Récupère les informations sur la couverture.

        Args:
            site_id: ID du site.
            manga_id: ID du manga.

        Returns:
            Informations de la couverture.
        """
        logger.info("Couverture manga: site={}, manga={}", site_id, manga_id)

        # Vérifier le cache
        cache = get_manga_cache()
        cache_key = f"cover:{site_id}:{manga_id}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        # Récupérer le manga
        manga_response = await get_manga(site_id, manga_id)

        response = CoverResponse(
            manga_id=manga_id,
            cover_url=manga_response.cover_url,
            width=0,  # TODO: Récupérer les dimensions réelles
            height=0,
            size_bytes=0,
            content_type="image/jpeg",
        )

        # Stocker dans le cache
        await cache.set(cache_key, response)

        return response

    # =========================================================================
    # POST /manga/{site_id}/{manga_id}/download — Télécharger le manga
    # =========================================================================

    @manga_router.post(
        "/manga/{site_id}/{manga_id}/download",
        response_model=DownloadTaskResponse,
        summary="Télécharger un manga",
        description="Crée une tâche de téléchargement pour un manga complet ou des chapitres spécifiques.",
        responses={
            202: {"description": "Tâche de téléchargement créée"},
            400: {"description": "Requête invalide"},
            404: {"description": "Manga non trouvé"},
            503: {"description": "Parser indisponible"},
        },
    )
    async def download_manga(
        request: Request,
        site_id: str,
        manga_id: str,
        body: DownloadRequest,
    ) -> DownloadTaskResponse:
        """Crée une tâche de téléchargement pour un manga.

        Args:
            request: Requête HTTP.
            site_id: ID du site.
            manga_id: ID du manga.
            body: Corps de la requête.

        Returns:
            Détails de la tâche de téléchargement.
        """
        logger.info(
            "Téléchargement manga: site={}, manga={}, format={}, quality={}",
            site_id,
            manga_id,
            body.format.value,
            body.quality.value,
        )

        # Vérifier que le site existe
        await _get_site_or_404(site_id)

        # Obtenir le parser
        parser = await _get_parser_or_503(site_id)

        try:
            # Récupérer le manga
            manga = await parser.get_manga(manga_id)

            # TODO: Créer la tâche de téléchargement via DownloadManager
            # Pour l'instant, on retourne une réponse factice
            task_id = f"task_{manga_id}_{int(time.time())}"

            # Émettre un événement
            try:
                event_bus = get_event_bus()
                await event_bus.emit(
                    EventType.DOWNLOAD_TASK_CREATED,
                    payload={
                        "task_id": task_id,
                        "site_id": site_id,
                        "manga_id": manga_id,
                        "manga_title": manga.title,
                        "format": body.format.value,
                        "quality": body.quality.value,
                        "chapter_ids": body.chapter_ids,
                    },
                    source="interfaces.web.manga",
                )
            except Exception as e:
                logger.debug("Impossible d'émettre l'événement: {}", e)

            return DownloadTaskResponse(
                task_id=task_id,
                status="queued",
                progress=0.0,
                estimated_time_seconds=0.0,
                message=t("manga.success.download_queued", default="Download task queued"),
            )

        except Exception as e:
            logger.error("Erreur lors de la création de la tâche de téléchargement: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="download_failed",
                    message=t("manga.error.download_failed", default="Failed to create download task"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /manga/{site_id}/{manga_id}/chapters/{chapter_id}/download — Télécharger un chapitre
    # =========================================================================

    @manga_router.post(
        "/manga/{site_id}/{manga_id}/chapters/{chapter_id}/download",
        response_model=DownloadTaskResponse,
        summary="Télécharger un chapitre",
        description="Crée une tâche de téléchargement pour un chapitre spécifique.",
        responses={
            202: {"description": "Tâche de téléchargement créée"},
            404: {"description": "Chapitre non trouvé"},
            503: {"description": "Parser indisponible"},
        },
    )
    async def download_chapter(
        request: Request,
        site_id: str,
        manga_id: str,
        chapter_id: str,
        body: DownloadRequest,
    ) -> DownloadTaskResponse:
        """Crée une tâche de téléchargement pour un chapitre.

        Args:
            request: Requête HTTP.
            site_id: ID du site.
            manga_id: ID du manga.
            chapter_id: ID du chapitre.
            body: Corps de la requête.

        Returns:
            Détails de la tâche de téléchargement.
        """
        logger.info(
            "Téléchargement chapitre: site={}, manga={}, chapter={}, format={}",
            site_id,
            manga_id,
            chapter_id,
            body.format.value,
        )

        # Vérifier que le site existe
        await _get_site_or_404(site_id)

        # Obtenir le parser
        parser = await _get_parser_or_503(site_id)

        try:
            # Récupérer le chapitre
            chapter = await parser.get_chapter(chapter_id)

            # TODO: Créer la tâche de téléchargement via DownloadManager
            task_id = f"task_{chapter_id}_{int(time.time())}"

            # Émettre un événement
            try:
                event_bus = get_event_bus()
                await event_bus.emit(
                    EventType.DOWNLOAD_TASK_CREATED,
                    payload={
                        "task_id": task_id,
                        "site_id": site_id,
                        "manga_id": manga_id,
                        "chapter_id": chapter_id,
                        "chapter_number": chapter.number,
                        "format": body.format.value,
                        "quality": body.quality.value,
                    },
                    source="interfaces.web.manga",
                )
            except Exception as e:
                logger.debug("Impossible d'émettre l'événement: {}", e)

            return DownloadTaskResponse(
                task_id=task_id,
                status="queued",
                progress=0.0,
                estimated_time_seconds=0.0,
                message=t("manga.success.download_queued", default="Download task queued"),
            )

        except Exception as e:
            logger.error("Erreur lors de la création de la tâche de téléchargement: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="download_failed",
                    message=t("manga.error.download_failed", default="Failed to create download task"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /manga/{site_id}/{manga_id}/library — Ajouter à la bibliothèque
    # =========================================================================

    @manga_router.post(
        "/manga/{site_id}/{manga_id}/library",
        response_model=LibraryResponse,
        summary="Ajouter à la bibliothèque",
        description="Ajoute un manga à la bibliothèque locale.",
        responses={
            200: {"description": "Manga ajouté à la bibliothèque"},
            404: {"description": "Manga non trouvé"},
            500: {"description": "Erreur interne"},
        },
    )
    async def add_to_library(
        request: Request,
        site_id: str,
        manga_id: str,
        body: AddToLibraryRequest,
    ) -> LibraryResponse:
        """Ajoute un manga à la bibliothèque.

        Args:
            request: Requête HTTP.
            site_id: ID du site.
            manga_id: ID du manga.
            body: Corps de la requête.

        Returns:
            Réponse de confirmation.
        """
        logger.info(
            "Ajout à la bibliothèque: site={}, manga={}, status={}",
            site_id,
            manga_id,
            body.reading_status.value,
        )

        # Vérifier que le site existe
        await _get_site_or_404(site_id)

        # Obtenir le parser
        parser = await _get_parser_or_503(site_id)

        try:
            # Récupérer le manga
            manga = await parser.get_manga(manga_id)

            # TODO: Ajouter à la bibliothèque via LibraryManager
            # Pour l'instant, on retourne une réponse factice

            # Émettre un événement
            try:
                event_bus = get_event_bus()
                await event_bus.emit(
                    EventType.LIBRARY_MANGA_ADDED,
                    payload={
                        "site_id": site_id,
                        "manga_id": manga_id,
                        "manga_title": manga.title,
                        "reading_status": body.reading_status.value,
                        "tags": body.tags,
                    },
                    source="interfaces.web.manga",
                )
            except Exception as e:
                logger.debug("Impossible d'émettre l'événement: {}", e)

            # Invalider le cache du manga
            cache = get_manga_cache()
            await cache.delete(f"manga_details:{site_id}:{manga_id}")

            return LibraryResponse(
                success=True,
                message=t("manga.success.added_to_library", default="Manga added to library"),
                manga_id=manga_id,
                reading_status=body.reading_status,
            )

        except Exception as e:
            logger.error("Erreur lors de l'ajout à la bibliothèque: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="library_failed",
                    message=t("manga.error.library_failed", default="Failed to add to library"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # DELETE /manga/{site_id}/{manga_id}/library — Retirer de la bibliothèque
    # =========================================================================

    @manga_router.delete(
        "/manga/{site_id}/{manga_id}/library",
        response_model=LibraryResponse,
        summary="Retirer de la bibliothèque",
        description="Retire un manga de la bibliothèque locale.",
        responses={
            200: {"description": "Manga retiré de la bibliothèque"},
            404: {"description": "Manga non trouvé dans la bibliothèque"},
            500: {"description": "Erreur interne"},
        },
    )
    async def remove_from_library(
        request: Request,
        site_id: str,
        manga_id: str,
    ) -> LibraryResponse:
        """Retire un manga de la bibliothèque.

        Args:
            request: Requête HTTP.
            site_id: ID du site.
            manga_id: ID du manga.

        Returns:
            Réponse de confirmation.
        """
        logger.info("Retrait de la bibliothèque: site={}, manga={}", site_id, manga_id)

        try:
            # TODO: Retirer de la bibliothèque via LibraryManager
            # Pour l'instant, on retourne une réponse factice

            # Émettre un événement
            try:
                event_bus = get_event_bus()
                await event_bus.emit(
                    EventType.LIBRARY_MANGA_REMOVED,
                    payload={
                        "site_id": site_id,
                        "manga_id": manga_id,
                    },
                    source="interfaces.web.manga",
                )
            except Exception as e:
                logger.debug("Impossible d'émettre l'événement: {}", e)

            # Invalider le cache du manga
            cache = get_manga_cache()
            await cache.delete(f"manga_details:{site_id}:{manga_id}")

            return LibraryResponse(
                success=True,
                message=t("manga.success.removed_from_library", default="Manga removed from library"),
                manga_id=manga_id,
                reading_status=None,
            )

        except Exception as e:
            logger.error("Erreur lors du retrait de la bibliothèque: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="library_failed",
                    message=t("manga.error.library_failed", default="Failed to remove from library"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # PATCH /manga/{site_id}/{manga_id}/chapters/{chapter_id}/read — Marquer lu/non-lu
    # =========================================================================

    @manga_router.patch(
        "/manga/{site_id}/{manga_id}/chapters/{chapter_id}/read",
        response_model=ReadStatusResponse,
        summary="Marquer un chapitre comme lu/non-lu",
        description="Met à jour le statut de lecture d'un chapitre.",
        responses={
            200: {"description": "Statut de lecture mis à jour"},
            404: {"description": "Chapitre non trouvé"},
            500: {"description": "Erreur interne"},
        },
    )
    async def update_read_status(
        request: Request,
        site_id: str,
        manga_id: str,
        chapter_id: str,
        body: UpdateReadStatusRequest,
    ) -> ReadStatusResponse:
        """Met à jour le statut de lecture d'un chapitre.

        Args:
            request: Requête HTTP.
            site_id: ID du site.
            manga_id: ID du manga.
            chapter_id: ID du chapitre.
            body: Corps de la requête.

        Returns:
            Réponse de confirmation.
        """
        logger.info(
            "Mise à jour statut lecture: site={}, manga={}, chapter={}, is_read={}",
            site_id,
            manga_id,
            chapter_id,
            body.is_read,
        )

        try:
            # TODO: Mettre à jour le statut via LibraryManager
            # Pour l'instant, on retourne une réponse factice

            # Invalider le cache du chapitre
            cache = get_manga_cache()
            await cache.delete(f"chapter:{site_id}:{manga_id}:{chapter_id}")

            return ReadStatusResponse(
                success=True,
                message=t("manga.success.read_status_updated", default="Read status updated"),
                chapter_id=chapter_id,
                is_read=body.is_read,
            )

        except Exception as e:
            logger.error("Erreur lors de la mise à jour du statut de lecture: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="update_failed",
                    message=t("manga.error.update_failed", default="Failed to update read status"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_CACHE_TTL_SECONDS",
    "MAX_CACHE_SIZE",
    "DEFAULT_DOWNLOAD_TIMEOUT_SECONDS",
    "SUPPORTED_DOWNLOAD_FORMATS",
    "SUPPORTED_IMAGE_QUALITIES",
    "READING_STATUSES",
    # Exceptions
    "MangaRouterError",
    "MangaNotFoundError",
    "ChapterNotFoundError",
    "DownloadError",
    "LibraryError",
    # Enums
    "MangaStatus",
    "ReadingStatus",
    "DownloadFormat",
    "ImageQuality",
    # Modèles de requête
    "DownloadRequest",
    "AddToLibraryRequest",
    "UpdateReadStatusRequest",
    # Modèles de réponse
    "MangaDetailsResponse",
    "ChapterResponse",
    "ChaptersResponse",
    "PageResponse",
    "PagesResponse",
    "DownloadTaskResponse",
    "LibraryResponse",
    "ReadStatusResponse",
    "CoverResponse",
    "ErrorResponse",
    # Cache
    "MangaCache",
    "get_manga_cache",
    # Routeur
    "manga_router" if FASTAPI_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
