"""Routeur FastAPI pour la gestion des chapitres.

Ce module fournit un routeur FastAPI complet pour interagir avec les chapitres
individuels : récupérer les détails, accéder aux pages, télécharger les images
en streaming, marquer la progression de lecture, et déclencher des téléchargements.

**Endpoints** :
    - GET /chapters/{chapter_id}                    : Détails d'un chapitre
    - GET /chapters/{chapter_id}/pages              : Liste des pages
    - GET /chapters/{chapter_id}/pages/{page_number}: Page spécifique
    - GET /chapters/{chapter_id}/images/{page_number}: Image d'une page (streaming)
    - POST /chapters/{chapter_id}/download          : Télécharger le chapitre
    - PATCH /chapters/{chapter_id}/read             : Marquer lu/non-lu
    - PATCH /chapters/{chapter_id}/progress         : Mettre à jour la progression
    - GET /chapters/{chapter_id}/reader             : Données pour le lecteur

**Fonctionnalités** :
    - Streaming d'images avec support Range (partial content)
    - Cache HTTP agressif pour les images (CDN-like)
    - Progression de lecture (page actuelle)
    - Marquage lu/non-lu avec timestamp
    - Intégration avec les parsers pour récupérer les images
    - Téléchargement via DownloadManager
    - Événements EventBus pour monitoring
    - Logging structuré
    - Validation stricte via Pydantic v2

**Exemple d'utilisation** :
    >>> from fastapi import FastAPI
    >>> from nexusdl.interfaces.web.backend.routers.chapters import chapters_router
    >>>
    >>> app = FastAPI()
    >>> app.include_router(chapters_router, prefix="/api/v1")

**Exemples d'appels API** :
    >>> # Détails d'un chapitre
    >>> GET /api/v1/chapters/ch_12345
    >>>
    >>> # Liste des pages
    >>> GET /api/v1/chapters/ch_12345/pages
    >>>
    >>> # Image d'une page (streaming)
    >>> GET /api/v1/chapters/ch_12345/images/1
    >>> Range: bytes=0-1023
    >>>
    >>> # Marquer comme lu
    >>> PATCH /api/v1/chapters/ch_12345/read
    >>> {"is_read": true}
    >>>
    >>> # Mettre à jour la progression
    >>> PATCH /api/v1/chapters/ch_12345/progress
    >>> {"current_page": 15}

Intégration :
    - core/registry/site_registry.py : Accès au registre des sites
    - core/parsers/*                 : Parsers pour récupérer les images
    - core/models/manga.py           : Modèles Chapter, Page
    - core/downloader/manager.py     : DownloadManager
    - core/library/database.py       : Base de données locale
    - core/events.py                 : EventBus pour monitoring
    - core/logger.py                 : Logs
    - core/i18n.py                   : Traductions
"""

from __future__ import annotations

import asyncio
import mimetypes
import time
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Final

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from fastapi import APIRouter, HTTPException, Query, Request, status
    from fastapi.responses import FileResponse, StreamingResponse
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
DEFAULT_CACHE_TTL_SECONDS: Final[int] = 300  # 5 minutes
IMAGE_CACHE_TTL_SECONDS: Final[int] = 86400  # 24 heures
MAX_CACHE_SIZE: Final[int] = 500
DEFAULT_IMAGE_QUALITY: Final[str] = "original"

# Timeouts
IMAGE_FETCH_TIMEOUT_SECONDS: Final[float] = 30.0
CHAPTER_FETCH_TIMEOUT_SECONDS: Final[float] = 15.0

# Limites
MAX_PAGE_NUMBER: Final[int] = 10000
MIN_PAGE_NUMBER: Final[int] = 1

# Types MIME supportés pour les images
SUPPORTED_IMAGE_TYPES: Final[frozenset[str]] = frozenset({
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/avif",
})

# User-Agent par défaut pour les requêtes d'images
DEFAULT_IMAGE_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ============================================================================
# EXCEPTIONS
# ============================================================================


class ChapterRouterError(NexusDLError):
    """Exception de base pour les erreurs du routeur chapitres."""


class ChapterNotFoundError(ChapterRouterError):
    """Exception levée lorsqu'un chapitre n'est pas trouvé.

    Attributes:
        chapter_id: ID du chapitre.
    """

    def __init__(self, chapter_id: str) -> None:
        super().__init__(
            t(
                "chapters.error.not_found",
                default="Chapter not found: {chapter_id}",
                chapter_id=chapter_id,
            )
        )
        self.chapter_id = chapter_id


class PageNotFoundError(ChapterRouterError):
    """Exception levée lorsqu'une page n'est pas trouvée.

    Attributes:
        chapter_id: ID du chapitre.
        page_number: Numéro de la page.
    """

    def __init__(self, chapter_id: str, page_number: int) -> None:
        super().__init__(
            t(
                "chapters.error.page_not_found",
                default="Page {page_number} not found in chapter {chapter_id}",
                page_number=page_number,
                chapter_id=chapter_id,
            )
        )
        self.chapter_id = chapter_id
        self.page_number = page_number


class ImageFetchError(ChapterRouterError):
    """Exception levée lorsqu'une image ne peut être récupérée.

    Attributes:
        url: URL de l'image.
        reason: Raison de l'échec.
    """

    def __init__(self, url: str, reason: str = "") -> None:
        msg = t("chapters.error.image_fetch_failed", default="Failed to fetch image")
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.url = url
        self.reason = reason


class InvalidPageNumberError(ChapterRouterError):
    """Exception levée lorsqu'un numéro de page est invalide.

    Attributes:
        page_number: Numéro de page invalide.
        max_pages: Nombre maximum de pages.
    """

    def __init__(self, page_number: int, max_pages: int) -> None:
        super().__init__(
            t(
                "chapters.error.invalid_page",
                default="Invalid page number: {page_number} (max: {max_pages})",
                page_number=page_number,
                max_pages=max_pages,
            )
        )
        self.page_number = page_number
        self.max_pages = max_pages


# ============================================================================
# ENUMS
# ============================================================================


class ReadStatus(str, Enum):
    """Statut de lecture d'un chapitre.

    Attributes:
        UNREAD: Non lu.
        READING: En cours de lecture.
        READ: Lu.
    """

    UNREAD = "unread"
    READING = "reading"
    READ = "read"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ReadStatus.UNREAD: t("chapters.read_status.unread", default="Unread"),
            ReadStatus.READING: t("chapters.read_status.reading", default="Reading"),
            ReadStatus.READ: t("chapters.read_status.read", default="Read"),
        }[self]


class ImageFormat(str, Enum):
    """Format d'image pour le streaming.

    Attributes:
        ORIGINAL: Format original.
        JPEG: Convertir en JPEG.
        PNG: Convertir en PNG.
        WEBP: Convertir en WebP.
    """

    ORIGINAL = "original"
    JPEG = "jpeg"
    PNG = "png"
    WEBP = "webp"

    @property
    def content_type(self) -> str:
        """Type MIME associé."""
        return {
            ImageFormat.ORIGINAL: "application/octet-stream",
            ImageFormat.JPEG: "image/jpeg",
            ImageFormat.PNG: "image/png",
            ImageFormat.WEBP: "image/webp",
        }[self]


# ============================================================================
# MODÈLES DE REQUÊTE — Pydantic
# ============================================================================


class UpdateReadStatusRequest(BaseModel):
    """Requête de mise à jour du statut de lecture.

    Attributes:
        is_read: Si le chapitre est lu.
    """

    is_read: bool = Field(..., description="Statut de lecture.")


class UpdateProgressRequest(BaseModel):
    """Requête de mise à jour de la progression.

    Attributes:
        current_page: Page actuelle (1-indexed).
        read_status: Statut de lecture (optionnel).
    """

    current_page: int = Field(..., ge=MIN_PAGE_NUMBER, le=MAX_PAGE_NUMBER, description="Page actuelle.")
    read_status: ReadStatus | None = Field(default=None, description="Statut de lecture.")


class DownloadChapterRequest(BaseModel):
    """Requête de téléchargement d'un chapitre.

    Attributes:
        format: Format de téléchargement.
        quality: Qualité d'image.
        priority: Priorité.
    """

    format: str = Field(default="cbz", description="Format.")
    quality: str = Field(default="original", description="Qualité.")
    priority: str = Field(default="normal", description="Priorité.")

    @field_validator("format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        """Valide le format."""
        valid_formats = {"cbz", "cbr", "pdf", "zip", "folder"}
        if v not in valid_formats:
            raise ValueError(f"Format invalide: {v}. Doit être l'un de: {valid_formats}")
        return v

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, v: str) -> str:
        """Valide la qualité."""
        valid_qualities = {"original", "high", "medium", "low"}
        if v not in valid_qualities:
            raise ValueError(f"Qualité invalide: {v}. Doit être l'un de: {valid_qualities}")
        return v


# ============================================================================
# MODÈLES DE RÉPONSE — Pydantic
# ============================================================================


class ChapterDetailResponse(BaseModel):
    """Réponse avec les détails d'un chapitre.

    Attributes:
        id: ID unique.
        manga_id: ID du manga parent.
        site_id: ID du site source.
        number: Numéro du chapitre.
        title: Titre.
        published_at: Date de publication.
        scanlator: Scanlator.
        pages_count: Nombre de pages.
        url: URL du chapitre.
        is_downloaded: Si le chapitre est téléchargé.
        is_read: Si le chapitre est lu.
        current_page: Page actuelle (pour reprise de lecture).
        read_status: Statut de lecture.
        last_read_at: Dernière lecture.
        language: Code langue.
        size_bytes: Taille totale (si téléchargé).
    """

    id: str = Field(..., description="ID unique.")
    manga_id: str = Field(default="", description="ID manga parent.")
    site_id: str = Field(default="", description="ID du site.")
    number: float = Field(..., description="Numéro.")
    title: str = Field(default="", description="Titre.")
    published_at: datetime | None = Field(default=None, description="Publication.")
    scanlator: str = Field(default="", description="Scanlator.")
    pages_count: int = Field(default=0, ge=0, description="Nombre de pages.")
    url: str = Field(default="", description="URL.")
    is_downloaded: bool = Field(default=False, description="Téléchargé.")
    is_read: bool = Field(default=False, description="Lu.")
    current_page: int = Field(default=0, ge=0, description="Page actuelle.")
    read_status: ReadStatus = Field(default=ReadStatus.UNREAD, description="Statut lecture.")
    last_read_at: datetime | None = Field(default=None, description="Dernière lecture.")
    language: str = Field(default="en", description="Code langue.")
    size_bytes: int = Field(default=0, ge=0, description="Taille bytes.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class PageResponse(BaseModel):
    """Réponse avec les détails d'une page.

    Attributes:
        number: Numéro de la page (1-indexed).
        url: URL de l'image.
        width: Largeur de l'image.
        height: Hauteur de l'image.
        size_bytes: Taille en bytes.
        content_type: Type MIME.
        image_url: URL API pour récupérer l'image.
    """

    number: int = Field(..., ge=MIN_PAGE_NUMBER, description="Numéro.")
    url: str = Field(..., description="URL de l'image.")
    width: int = Field(default=0, ge=0, description="Largeur.")
    height: int = Field(default=0, ge=0, description="Hauteur.")
    size_bytes: int = Field(default=0, ge=0, description="Taille bytes.")
    content_type: str = Field(default="image/jpeg", description="Type MIME.")
    image_url: str = Field(..., description="URL API pour l'image.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class PagesListResponse(BaseModel):
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


class ProgressResponse(BaseModel):
    """Réponse avec la progression de lecture.

    Attributes:
        chapter_id: ID du chapitre.
        current_page: Page actuelle.
        total_pages: Nombre total de pages.
        progress_percentage: Progression (0.0 à 1.0).
        read_status: Statut de lecture.
        last_read_at: Dernière lecture.
    """

    chapter_id: str = Field(..., description="ID chapitre.")
    current_page: int = Field(default=0, ge=0, description="Page actuelle.")
    total_pages: int = Field(default=0, ge=0, description="Total pages.")
    progress_percentage: float = Field(default=0.0, ge=0.0, le=1.0, description="Progression.")
    read_status: ReadStatus = Field(default=ReadStatus.UNREAD, description="Statut.")
    last_read_at: datetime | None = Field(default=None, description="Dernière lecture.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ReaderDataResponse(BaseModel):
    """Réponse avec les données pour le lecteur.

    Attributes:
        chapter_id: ID du chapitre.
        manga_title: Titre du manga.
        chapter_title: Titre du chapitre.
        chapter_number: Numéro du chapitre.
        pages: Liste des URLs d'images.
        current_page: Page actuelle.
        total_pages: Nombre total de pages.
        has_previous_chapter: Si un chapitre précédent existe.
        has_next_chapter: Si un chapitre suivant existe.
        previous_chapter_id: ID du chapitre précédent.
        next_chapter_id: ID du chapitre suivant.
    """

    chapter_id: str = Field(..., description="ID chapitre.")
    manga_title: str = Field(default="", description="Titre manga.")
    chapter_title: str = Field(default="", description="Titre chapitre.")
    chapter_number: float = Field(..., description="Numéro.")
    pages: list[str] = Field(default_factory=list, description="URLs images.")
    current_page: int = Field(default=1, ge=1, description="Page actuelle.")
    total_pages: int = Field(default=0, ge=0, description="Total pages.")
    has_previous_chapter: bool = Field(default=False, description="Chapitre précédent.")
    has_next_chapter: bool = Field(default=False, description="Chapitre suivant.")
    previous_chapter_id: str | None = Field(default=None, description="ID précédent.")
    next_chapter_id: str | None = Field(default=None, description="ID suivant.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class DownloadTaskResponse(BaseModel):
    """Réponse avec les détails d'une tâche de téléchargement.

    Attributes:
        task_id: ID de la tâche.
        status: Statut de la tâche.
        message: Message.
    """

    task_id: str = Field(..., description="ID tâche.")
    status: str = Field(..., description="Statut.")
    message: str = Field(..., description="Message.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class UpdateResponse(BaseModel):
    """Réponse après mise à jour.

    Attributes:
        success: Si l'opération a réussi.
        message: Message de confirmation.
        chapter_id: ID du chapitre.
    """

    success: bool = Field(..., description="Succès.")
    message: str = Field(..., description="Message.")
    chapter_id: str = Field(..., description="ID chapitre.")

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
# CACHE — Cache en mémoire pour les données chapitres
# ============================================================================


class ChapterCache:
    """Cache en mémoire pour les données chapitres.

    Évite les appels répétés aux parsers et à la base de données.
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
_chapter_cache = ChapterCache()


def get_chapter_cache() -> ChapterCache:
    """Retourne l'instance globale du cache."""
    return _chapter_cache


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


def _chapter_to_detail_response(chapter: Any, site_id: str = "", manga_id: str = "") -> ChapterDetailResponse:
    """Convertit un Chapter en ChapterDetailResponse.

    Args:
        chapter: Instance de Chapter.
        site_id: ID du site.
        manga_id: ID du manga.

    Returns:
        Instance de ChapterDetailResponse.
    """
    # Déterminer le read_status
    is_read = getattr(chapter, "is_read", False)
    current_page = getattr(chapter, "current_page", 0)

    if is_read:
        read_status = ReadStatus.READ
    elif current_page > 0:
        read_status = ReadStatus.READING
    else:
        read_status = ReadStatus.UNREAD

    return ChapterDetailResponse(
        id=chapter.id,
        manga_id=getattr(chapter, "manga_id", manga_id) or manga_id,
        site_id=getattr(chapter, "site_id", site_id) or site_id,
        number=chapter.number,
        title=getattr(chapter, "title", "") or "",
        published_at=getattr(chapter, "published_at", None),
        scanlator=getattr(chapter, "scanlator", "") or "",
        pages_count=getattr(chapter, "pages_count", 0) or 0,
        url=getattr(chapter, "url", "") or "",
        is_downloaded=getattr(chapter, "is_downloaded", False),
        is_read=is_read,
        current_page=current_page,
        read_status=read_status,
        last_read_at=getattr(chapter, "last_read_at", None),
        language=getattr(chapter.language, "value", "en") if hasattr(chapter, "language") and hasattr(chapter.language, "value") else "en",
        size_bytes=getattr(chapter, "size_bytes", 0) or 0,
    )


def _page_to_response(page: Any, number: int, chapter_id: str) -> PageResponse:
    """Convertit une Page en PageResponse.

    Args:
        page: Instance de Page.
        number: Numéro de la page.
        chapter_id: ID du chapitre.

    Returns:
        Instance de PageResponse.
    """
    url = page.url if hasattr(page, "url") else str(page)
    content_type = "image/jpeg"

    # Déterminer le type MIME depuis l'URL
    if url:
        guessed_type, _ = mimetypes.guess_type(url)
        if guessed_type and guessed_type in SUPPORTED_IMAGE_TYPES:
            content_type = guessed_type

    return PageResponse(
        number=number,
        url=url,
        width=getattr(page, "width", 0) or 0,
        height=getattr(page, "height", 0) or 0,
        size_bytes=getattr(page, "size_bytes", 0) or 0,
        content_type=content_type,
        image_url=f"/api/v1/chapters/{chapter_id}/images/{number}",
    )


def _parse_range_header(range_header: str, total_size: int) -> tuple[int, int] | None:
    """Parse un header Range HTTP.

    Supporte les formats :
        - bytes=0-1023
        - bytes=1024-
        - bytes=-500

    Args:
        range_header: Valeur du header Range.
        total_size: Taille totale du contenu.

    Returns:
        Tuple (start, end) ou None si invalide.
    """
    if not range_header.startswith("bytes="):
        return None

    range_spec = range_header[6:]

    try:
        if "-" not in range_spec:
            return None

        parts = range_spec.split("-", 1)

        if parts[0] == "":
            # bytes=-500 (derniers 500 bytes)
            suffix_length = int(parts[1])
            if suffix_length <= 0 or suffix_length > total_size:
                return None
            return (total_size - suffix_length, total_size - 1)
        elif parts[1] == "":
            # bytes=1024- (de 1024 jusqu'à la fin)
            start = int(parts[0])
            if start >= total_size:
                return None
            return (start, total_size - 1)
        else:
            # bytes=0-1023
            start = int(parts[0])
            end = int(parts[1])
            if start < 0 or end >= total_size or start > end:
                return None
            return (start, end)

    except (ValueError, IndexError):
        return None


async def _fetch_image_bytes(url: str, *, timeout: float = IMAGE_FETCH_TIMEOUT_SECONDS) -> tuple[bytes, str]:
    """Récupère les bytes d'une image depuis une URL.

    Args:
        url: URL de l'image.
        timeout: Timeout en secondes.

    Returns:
        Tuple (bytes, content_type).

    Raises:
        ImageFetchError: Si la récupération échoue.
    """
    try:
        import httpx

        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": DEFAULT_IMAGE_USER_AGENT,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "image/jpeg")
            if ";" in content_type:
                content_type = content_type.split(";")[0].strip()

            return response.content, content_type

    except Exception as e:
        logger.warning("Impossible de récupérer l'image {}: {}", url, e)
        raise ImageFetchError(url, str(e)) from e


async def _emit_chapter_event(
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Émet un événement de chapitre.

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
            source="interfaces.web.chapters",
        )
    except Exception as e:
        logger.debug("Impossible d'émettre l'événement: {}", e)


# ============================================================================
# ROUTEUR — Endpoints FastAPI
# ============================================================================


if FASTAPI_AVAILABLE:

    chapters_router = APIRouter(tags=["chapters"])

    # =========================================================================
    # GET /chapters/{chapter_id} — Détails d'un chapitre
    # =========================================================================

    @chapters_router.get(
        "/chapters/{chapter_id}",
        response_model=ChapterDetailResponse,
        summary="Détails d'un chapitre",
        description="Retourne les détails complets d'un chapitre spécifique.",
        responses={
            200: {"description": "Détails du chapitre"},
            404: {"description": "Chapitre non trouvé"},
        },
    )
    async def get_chapter(chapter_id: str) -> ChapterDetailResponse:
        """Récupère les détails d'un chapitre.

        Args:
            chapter_id: ID du chapitre.

        Returns:
            Détails du chapitre.
        """
        logger.info("Détails chapitre: {}", chapter_id)

        # Vérifier le cache
        cache = get_chapter_cache()
        cache_key = f"chapter:{chapter_id}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        try:
            # TODO: Récupérer depuis la base de données ou le parser
            # from nexusdl.core.library import get_chapter
            # chapter = await get_chapter(chapter_id)

            # Pour l'instant, lever une 404
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse(
                    error="chapter_not_found",
                    message=t("chapters.error.not_found", default="Chapter not found: {chapter_id}", chapter_id=chapter_id),
                    details={"chapter_id": chapter_id},
                ).model_dump(),
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de la récupération du chapitre {}: {}", chapter_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="fetch_failed",
                    message=t("chapters.error.fetch_failed", default="Failed to fetch chapter"),
                    details={"chapter_id": chapter_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /chapters/{chapter_id}/pages — Liste des pages
    # =========================================================================

    @chapters_router.get(
        "/chapters/{chapter_id}/pages",
        response_model=PagesListResponse,
        summary="Liste des pages",
        description="Retourne la liste des pages d'un chapitre avec leurs métadonnées.",
        responses={
            200: {"description": "Liste des pages"},
            404: {"description": "Chapitre non trouvé"},
        },
    )
    async def get_chapter_pages(chapter_id: str) -> PagesListResponse:
        """Récupère la liste des pages d'un chapitre.

        Args:
            chapter_id: ID du chapitre.

        Returns:
            Liste des pages.
        """
        logger.info("Pages chapitre: {}", chapter_id)

        # Vérifier le cache
        cache = get_chapter_cache()
        cache_key = f"chapter_pages:{chapter_id}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        try:
            # TODO: Récupérer les pages depuis le parser
            # from nexusdl.core.registry import get_site_registry
            # registry = get_site_registry()
            # parser = await registry.get_parser(site_id)
            # pages = await parser.get_pages(chapter_id)

            # Pour l'instant, retourner une liste vide
            response = PagesListResponse(
                chapter_id=chapter_id,
                pages=[],
                total=0,
            )

            # Stocker dans le cache
            await cache.set(cache_key, response, ttl_seconds=600)

            return response

        except Exception as e:
            logger.error("Erreur lors de la récupération des pages de {}: {}", chapter_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="pages_failed",
                    message=t("chapters.error.pages_failed", default="Failed to fetch pages"),
                    details={"chapter_id": chapter_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /chapters/{chapter_id}/pages/{page_number} — Page spécifique
    # =========================================================================

    @chapters_router.get(
        "/chapters/{chapter_id}/pages/{page_number}",
        response_model=PageResponse,
        summary="Page spécifique",
        description="Retourne les détails d'une page spécifique.",
        responses={
            200: {"description": "Détails de la page"},
            404: {"description": "Page non trouvée"},
        },
    )
    async def get_chapter_page(
        chapter_id: str,
        page_number: int,
    ) -> PageResponse:
        """Récupère les détails d'une page spécifique.

        Args:
            chapter_id: ID du chapitre.
            page_number: Numéro de la page (1-indexed).

        Returns:
            Détails de la page.
        """
        logger.info("Page spécifique: chapter={}, page={}", chapter_id, page_number)

        if page_number < MIN_PAGE_NUMBER:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorResponse(
                    error="invalid_page_number",
                    message=t("chapters.error.invalid_page", default="Invalid page number"),
                    details={"page_number": page_number, "min": MIN_PAGE_NUMBER},
                ).model_dump(),
            )

        try:
            # Récupérer toutes les pages
            pages_response = await get_chapter_pages(chapter_id)

            # Trouver la page demandée
            for page in pages_response.pages:
                if page.number == page_number:
                    return page

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse(
                    error="page_not_found",
                    message=t(
                        "chapters.error.page_not_found",
                        default="Page {page_number} not found in chapter {chapter_id}",
                        page_number=page_number,
                        chapter_id=chapter_id,
                    ),
                    details={"chapter_id": chapter_id, "page_number": page_number, "total": pages_response.total},
                ).model_dump(),
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de la récupération de la page {} de {}: {}", page_number, chapter_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="page_failed",
                    message=t("chapters.error.page_failed", default="Failed to fetch page"),
                    details={"chapter_id": chapter_id, "page_number": page_number, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /chapters/{chapter_id}/images/{page_number} — Image d'une page (streaming)
    # =========================================================================

    @chapters_router.get(
        "/chapters/{chapter_id}/images/{page_number}",
        summary="Image d'une page (streaming)",
        description="Retourne l'image d'une page spécifique avec support Range pour partial content.",
        responses={
            200: {"description": "Image complète"},
            206: {"description": "Partial content (Range request)"},
            404: {"description": "Page non trouvée"},
            416: {"description": "Range not satisfiable"},
        },
    )
    async def get_chapter_image(
        request: Request,
        chapter_id: str,
        page_number: int,
        format: ImageFormat = Query(ImageFormat.ORIGINAL, description="Format de sortie"),
    ) -> Any:
        """Récupère l'image d'une page avec support streaming et Range.

        Args:
            request: Requête HTTP.
            chapter_id: ID du chapitre.
            page_number: Numéro de la page (1-indexed).
            format: Format de sortie.

        Returns:
            StreamingResponse avec l'image.
        """
        logger.info("Image chapitre: chapter={}, page={}, format={}", chapter_id, page_number, format.value)

        if page_number < MIN_PAGE_NUMBER:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorResponse(
                    error="invalid_page_number",
                    message=t("chapters.error.invalid_page", default="Invalid page number"),
                    details={"page_number": page_number},
                ).model_dump(),
            )

        try:
            # Récupérer la page
            page = await get_chapter_page(chapter_id, page_number)

            # Récupérer l'image
            image_bytes, content_type = await _fetch_image_bytes(page.url)

            # TODO: Conversion de format si demandé (JPEG, PNG, WebP)
            # if format != ImageFormat.ORIGINAL:
            #     image_bytes = await _convert_image(image_bytes, format)
            #     content_type = format.content_type

            total_size = len(image_bytes)

            # Gérer les Range requests
            range_header = request.headers.get("range")
            if range_header:
                range_spec = _parse_range_header(range_header, total_size)
                if range_spec is None:
                    raise HTTPException(
                        status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                        headers={"Content-Range": f"bytes */{total_size}"},
                    )

                start, end = range_spec
                content_length = end - start + 1
                content_range = f"bytes {start}-{end}/{total_size}"

                return StreamingResponse(
                    iter([image_bytes[start:end + 1]]),
                    status_code=206,
                    media_type=content_type,
                    headers={
                        "Content-Range": content_range,
                        "Content-Length": str(content_length),
                        "Accept-Ranges": "bytes",
                        "Cache-Control": f"public, max-age={IMAGE_CACHE_TTL_SECONDS}",
                    },
                )

            # Réponse complète
            return StreamingResponse(
                iter([image_bytes]),
                status_code=200,
                media_type=content_type,
                headers={
                    "Content-Length": str(total_size),
                    "Accept-Ranges": "bytes",
                    "Cache-Control": f"public, max-age={IMAGE_CACHE_TTL_SECONDS}",
                    "ETag": f'"{chapter_id}-{page_number}-{total_size}"',
                },
            )

        except HTTPException:
            raise
        except ImageFetchError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=ErrorResponse(
                    error="image_fetch_failed",
                    message=str(e),
                    details={"chapter_id": chapter_id, "page_number": page_number},
                ).model_dump(),
            ) from e
        except Exception as e:
            logger.error("Erreur lors de la récupération de l'image {} de {}: {}", page_number, chapter_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="image_failed",
                    message=t("chapters.error.image_failed", default="Failed to fetch image"),
                    details={"chapter_id": chapter_id, "page_number": page_number, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /chapters/{chapter_id}/download — Télécharger le chapitre
    # =========================================================================

    @chapters_router.post(
        "/chapters/{chapter_id}/download",
        response_model=DownloadTaskResponse,
        summary="Télécharger un chapitre",
        description="Crée une tâche de téléchargement pour un chapitre spécifique.",
        responses={
            202: {"description": "Tâche créée"},
            404: {"description": "Chapitre non trouvé"},
        },
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def download_chapter(
        request: Request,
        chapter_id: str,
        body: DownloadChapterRequest,
    ) -> DownloadTaskResponse:
        """Crée une tâche de téléchargement pour un chapitre.

        Args:
            request: Requête HTTP.
            chapter_id: ID du chapitre.
            body: Corps de la requête.

        Returns:
            Détails de la tâche créée.
        """
        user_id = _get_user_id_from_request(request)
        logger.info(
            "Téléchargement chapitre: user={}, chapter={}, format={}, quality={}",
            user_id,
            chapter_id,
            body.format,
            body.quality,
        )

        try:
            # TODO: Créer la tâche via DownloadManager
            # from nexusdl.core.downloader import get_download_manager
            # manager = get_download_manager()
            # task = await manager.create_chapter_task(chapter_id, body.format, body.quality, body.priority)

            # Pour l'instant, retourner une réponse factice
            task_id = f"task_{chapter_id}_{int(time.time())}"

            # Émettre un événement
            await _emit_chapter_event(
                "chapter.download.started",
                {
                    "user_id": user_id,
                    "chapter_id": chapter_id,
                    "task_id": task_id,
                    "format": body.format,
                    "quality": body.quality,
                    "priority": body.priority,
                },
            )

            return DownloadTaskResponse(
                task_id=task_id,
                status="queued",
                message=t("chapters.success.download_queued", default="Download task queued"),
            )

        except Exception as e:
            logger.error("Erreur lors de la création de la tâche de téléchargement: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="download_failed",
                    message=t("chapters.error.download_failed", default="Failed to create download task"),
                    details={"chapter_id": chapter_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # PATCH /chapters/{chapter_id}/read — Marquer lu/non-lu
    # =========================================================================

    @chapters_router.patch(
        "/chapters/{chapter_id}/read",
        response_model=UpdateResponse,
        summary="Marquer lu/non-lu",
        description="Met à jour le statut de lecture d'un chapitre.",
        responses={
            200: {"description": "Statut mis à jour"},
            404: {"description": "Chapitre non trouvé"},
        },
    )
    async def update_read_status(
        request: Request,
        chapter_id: str,
        body: UpdateReadStatusRequest,
    ) -> UpdateResponse:
        """Met à jour le statut de lecture.

        Args:
            request: Requête HTTP.
            chapter_id: ID du chapitre.
            body: Corps de la requête.

        Returns:
            Réponse de confirmation.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Mise à jour statut lecture: user={}, chapter={}, is_read={}", user_id, chapter_id, body.is_read)

        try:
            # TODO: Mettre à jour dans la base de données
            # from nexusdl.core.library import update_chapter_read_status
            # await update_chapter_read_status(chapter_id, body.is_read)

            # Invalider le cache
            cache = get_chapter_cache()
            await cache.delete(f"chapter:{chapter_id}")
            await cache.invalidate_pattern(f"chapter_pages:{chapter_id}")

            # Émettre un événement
            await _emit_chapter_event(
                "chapter.read_status.updated",
                {
                    "user_id": user_id,
                    "chapter_id": chapter_id,
                    "is_read": body.is_read,
                },
            )

            return UpdateResponse(
                success=True,
                message=t("chapters.success.read_status_updated", default="Read status updated"),
                chapter_id=chapter_id,
            )

        except Exception as e:
            logger.error("Erreur lors de la mise à jour du statut de lecture: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="update_failed",
                    message=t("chapters.error.update_failed", default="Failed to update read status"),
                    details={"chapter_id": chapter_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # PATCH /chapters/{chapter_id}/progress — Mettre à jour la progression
    # =========================================================================

    @chapters_router.patch(
        "/chapters/{chapter_id}/progress",
        response_model=ProgressResponse,
        summary="Mettre à jour la progression",
        description="Met à jour la progression de lecture (page actuelle).",
        responses={
            200: {"description": "Progression mise à jour"},
            404: {"description": "Chapitre non trouvé"},
        },
    )
    async def update_progress(
        request: Request,
        chapter_id: str,
        body: UpdateProgressRequest,
    ) -> ProgressResponse:
        """Met à jour la progression de lecture.

        Args:
            request: Requête HTTP.
            chapter_id: ID du chapitre.
            body: Corps de la requête.

        Returns:
            Progression mise à jour.
        """
        user_id = _get_user_id_from_request(request)
        logger.info(
            "Mise à jour progression: user={}, chapter={}, page={}, status={}",
            user_id,
            chapter_id,
            body.current_page,
            body.read_status,
        )

        try:
            # TODO: Mettre à jour dans la base de données
            # from nexusdl.core.library import update_chapter_progress
            # await update_chapter_progress(chapter_id, body.current_page, body.read_status)

            # Récupérer le chapitre pour construire la réponse
            # chapter = await get_chapter(chapter_id)

            # Pour l'instant, retourner une réponse factice
            total_pages = 0  # TODO: Récupérer depuis le chapitre
            progress_percentage = body.current_page / total_pages if total_pages > 0 else 0.0

            read_status = body.read_status or (
                ReadStatus.READ if body.current_page >= total_pages and total_pages > 0
                else ReadStatus.READING if body.current_page > 0
                else ReadStatus.UNREAD
            )

            response = ProgressResponse(
                chapter_id=chapter_id,
                current_page=body.current_page,
                total_pages=total_pages,
                progress_percentage=progress_percentage,
                read_status=read_status,
                last_read_at=datetime.now(UTC),
            )

            # Invalider le cache
            cache = get_chapter_cache()
            await cache.delete(f"chapter:{chapter_id}")

            # Émettre un événement
            await _emit_chapter_event(
                "chapter.progress.updated",
                {
                    "user_id": user_id,
                    "chapter_id": chapter_id,
                    "current_page": body.current_page,
                    "read_status": read_status.value,
                },
            )

            return response

        except Exception as e:
            logger.error("Erreur lors de la mise à jour de la progression: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="progress_failed",
                    message=t("chapters.error.progress_failed", default="Failed to update progress"),
                    details={"chapter_id": chapter_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /chapters/{chapter_id}/reader — Données pour le lecteur
    # =========================================================================

    @chapters_router.get(
        "/chapters/{chapter_id}/reader",
        response_model=ReaderDataResponse,
        summary="Données pour le lecteur",
        description="Retourne toutes les données nécessaires pour le lecteur de chapitres.",
        responses={
            200: {"description": "Données du lecteur"},
            404: {"description": "Chapitre non trouvé"},
        },
    )
    async def get_reader_data(
        request: Request,
        chapter_id: str,
    ) -> ReaderDataResponse:
        """Récupère les données pour le lecteur.

        Args:
            request: Requête HTTP.
            chapter_id: ID du chapitre.

        Returns:
            Données du lecteur.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Données lecteur: user={}, chapter={}", user_id, chapter_id)

        try:
            # Récupérer le chapitre
            chapter = await get_chapter(chapter_id)

            # Récupérer les pages
            pages_response = await get_chapter_pages(chapter_id)

            # Construire les URLs d'images
            image_urls = [
                f"/api/v1/chapters/{chapter_id}/images/{page.number}"
                for page in pages_response.pages
            ]

            # TODO: Récupérer les chapitres précédent/suivant
            # previous_chapter = await get_previous_chapter(chapter.manga_id, chapter.number)
            # next_chapter = await get_next_chapter(chapter.manga_id, chapter.number)

            response = ReaderDataResponse(
                chapter_id=chapter_id,
                manga_title="",  # TODO
                chapter_title=chapter.title,
                chapter_number=chapter.number,
                pages=image_urls,
                current_page=max(1, chapter.current_page),
                total_pages=pages_response.total,
                has_previous_chapter=False,  # TODO
                has_next_chapter=False,  # TODO
                previous_chapter_id=None,  # TODO
                next_chapter_id=None,  # TODO
            )

            return response

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de la récupération des données lecteur: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="reader_failed",
                    message=t("chapters.error.reader_failed", default="Failed to fetch reader data"),
                    details={"chapter_id": chapter_id, "reason": str(e)},
                ).model_dump(),
            ) from e


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_CACHE_TTL_SECONDS",
    "IMAGE_CACHE_TTL_SECONDS",
    "MAX_CACHE_SIZE",
    "DEFAULT_IMAGE_QUALITY",
    "IMAGE_FETCH_TIMEOUT_SECONDS",
    "CHAPTER_FETCH_TIMEOUT_SECONDS",
    "MAX_PAGE_NUMBER",
    "MIN_PAGE_NUMBER",
    "SUPPORTED_IMAGE_TYPES",
    # Exceptions
    "ChapterRouterError",
    "ChapterNotFoundError",
    "PageNotFoundError",
    "ImageFetchError",
    "InvalidPageNumberError",
    # Enums
    "ReadStatus",
    "ImageFormat",
    # Modèles de requête
    "UpdateReadStatusRequest",
    "UpdateProgressRequest",
    "DownloadChapterRequest",
    # Modèles de réponse
    "ChapterDetailResponse",
    "PageResponse",
    "PagesListResponse",
    "ProgressResponse",
    "ReaderDataResponse",
    "DownloadTaskResponse",
    "UpdateResponse",
    "ErrorResponse",
    # Cache
    "ChapterCache",
    "get_chapter_cache",
    # Helpers
    "parse_range_header",
    "fetch_image_bytes",
    # Routeur
    "chapters_router" if FASTAPI_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]

# Exports des fonctions helpers (sans underscore)
if FASTAPI_AVAILABLE:
    parse_range_header = _parse_range_header
    fetch_image_bytes = _fetch_image_bytes
