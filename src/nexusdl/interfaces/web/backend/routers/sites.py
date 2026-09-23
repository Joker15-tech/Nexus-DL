"""Routeur FastAPI pour la gestion des sites (parsers).

Ce module fournit un routeur FastAPI complet pour interagir avec les sites
supportés par NexusDL. Il permet de lister les sites, rechercher des mangas,
récupérer les détails, et accéder aux chapitres/pages.

**Endpoints** :
    - GET /sites                    : Lister tous les sites
    - GET /sites/{site_id}          : Détails d'un site
    - GET /sites/{site_id}/health   : Santé d'un site
    - GET /sites/{site_id}/capabilities : Capacités d'un site
    - GET /sites/{site_id}/search   : Rechercher des mangas
    - GET /sites/{site_id}/manga/{manga_id} : Détails d'un manga
    - GET /sites/{site_id}/manga/{manga_id}/chapters : Chapitres
    - GET /sites/{site_id}/manga/{manga_id}/chapters/{chapter_id}/pages : Pages

**Fonctionnalités** :
    - Cache en mémoire pour les réponses fréquentes
    - Rate limiting par endpoint
    - Pagination pour les résultats
    - Filtrage par langue, statut, tags
    - Gestion robuste des erreurs
    - Validation des paramètres
    - Support des formats JSON
    - Intégration avec EventBus pour monitoring
    - Logging structuré

**Exemple d'utilisation** :
    >>> from fastapi import FastAPI
    >>> from nexusdl.interfaces.web.backend.routers.sites import sites_router
    >>>
    >>> app = FastAPI()
    >>> app.include_router(sites_router, prefix="/api/v1")

Intégration :
    - core/registry/site_registry.py : Accès au registre des sites
    - core/parsers/*                 : Parsers pour chaque site
    - core/models/site.py            : Modèles SiteConfig
    - core/models/manga.py           : Modèles Manga, Chapter, Page
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
DEFAULT_SEARCH_LIMIT: Final[int] = 20
MAX_SEARCH_LIMIT: Final[int] = 100
DEFAULT_CACHE_TTL_SECONDS: Final[int] = 300  # 5 minutes
MAX_CACHE_SIZE: Final[int] = 1000
DEFAULT_TIMEOUT_SECONDS: Final[int] = 30

# Endpoints
ENDPOINT_LIST_SITES: Final[str] = "/sites"
ENDPOINT_GET_SITE: Final[str] = "/sites/{site_id}"
ENDPOINT_SITE_HEALTH: Final[str] = "/sites/{site_id}/health"
ENDPOINT_SITE_CAPABILITIES: Final[str] = "/sites/{site_id}/capabilities"
ENDPOINT_SEARCH: Final[str] = "/sites/{site_id}/search"
ENDPOINT_GET_MANGA: Final[str] = "/sites/{site_id}/manga/{manga_id}"
ENDPOINT_GET_CHAPTERS: Final[str] = "/sites/{site_id}/manga/{manga_id}/chapters"
ENDPOINT_GET_PAGES: Final[str] = "/sites/{site_id}/manga/{manga_id}/chapters/{chapter_id}/pages"


# ============================================================================
# EXCEPTIONS
# ============================================================================


class SitesRouterError(NexusDLError):
    """Exception de base pour les erreurs du routeur sites."""


class SiteNotFoundError(SitesRouterError):
    """Exception levée lorsqu'un site n'est pas trouvé.

    Attributes:
        site_id: ID du site.
    """

    def __init__(self, site_id: str) -> None:
        super().__init__(
            t("sites.error.not_found", default="Site not found: {site_id}", site_id=site_id)
        )
        self.site_id = site_id


class ParserNotAvailableError(SitesRouterError):
    """Exception levée lorsqu'un parser n'est pas disponible.

    Attributes:
        site_id: ID du site.
        reason: Raison de l'indisponibilité.
    """

    def __init__(self, site_id: str, reason: str = "") -> None:
        msg = t("sites.error.parser_unavailable", default="Parser not available for site: {site_id}", site_id=site_id)
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.site_id = site_id
        self.reason = reason


class SearchError(SitesRouterError):
    """Exception levée lorsqu'une recherche échoue.

    Attributes:
        site_id: ID du site.
        query: Requête de recherche.
        reason: Raison de l'échec.
    """

    def __init__(self, site_id: str, query: str, reason: str = "") -> None:
        msg = t("sites.error.search_failed", default="Search failed on site: {site_id}", site_id=site_id)
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.site_id = site_id
        self.query = query
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class SiteHealthStatus(str, Enum):
    """Statut de santé d'un site.

    Attributes:
        HEALTHY: Site opérationnel.
        DEGRADED: Site dégradé (lent ou partiellement indisponible).
        DOWN: Site indisponible.
        UNKNOWN: Statut inconnu.
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            SiteHealthStatus.HEALTHY: t("sites.health.healthy", default="Healthy"),
            SiteHealthStatus.DEGRADED: t("sites.health.degraded", default="Degraded"),
            SiteHealthStatus.DOWN: t("sites.health.down", default="Down"),
            SiteHealthStatus.UNKNOWN: t("sites.health.unknown", default="Unknown"),
        }[self]


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


# ============================================================================
# MODÈLES DE RÉPONSE — Pydantic
# ============================================================================


class SiteResponse(BaseModel):
    """Réponse pour les détails d'un site.

    Attributes:
        id: ID unique du site.
        name: Nom du site.
        language: Code langue (ISO 639-1).
        domains: Liste des domaines.
        enabled: Si le site est activé.
        adult: Si le site est pour adultes.
        capabilities: Capacités du site.
        priority: Priorité du site.
        tags: Tags du site.
    """

    id: str = Field(..., description="ID unique.")
    name: str = Field(..., description="Nom.")
    language: str = Field(..., description="Code langue.")
    domains: list[str] = Field(default_factory=list, description="Domaines.")
    enabled: bool = Field(default=True, description="Activé.")
    adult: bool = Field(default=False, description="Adulte.")
    capabilities: dict[str, bool] = Field(default_factory=dict, description="Capacités.")
    priority: int = Field(default=0, ge=0, description="Priorité.")
    tags: list[str] = Field(default_factory=list, description="Tags.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class SiteListResponse(BaseModel):
    """Réponse pour la liste des sites.

    Attributes:
        sites: Liste des sites.
        total: Nombre total de sites.
        filtered_count: Nombre de sites après filtrage.
    """

    sites: list[SiteResponse] = Field(..., description="Sites.")
    total: int = Field(..., ge=0, description="Total.")
    filtered_count: int = Field(..., ge=0, description="Filtrés.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class SiteHealthResponse(BaseModel):
    """Réponse pour la santé d'un site.

    Attributes:
        site_id: ID du site.
        status: Statut de santé.
        response_time_ms: Temps de réponse en ms.
        last_checked_at: Timestamp de la dernière vérification.
        error_message: Message d'erreur (si down).
    """

    site_id: str = Field(..., description="ID du site.")
    status: SiteHealthStatus = Field(..., description="Statut.")
    response_time_ms: float = Field(default=0.0, ge=0.0, description="Temps réponse.")
    last_checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Dernière vérif.")
    error_message: str | None = Field(default=None, description="Message erreur.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class SiteCapabilitiesResponse(BaseModel):
    """Réponse pour les capacités d'un site.

    Attributes:
        site_id: ID du site.
        supports_search: Supporte la recherche.
        supports_download: Supporte le téléchargement.
        supports_cloudflare_bypass: Supporte le bypass Cloudflare.
        requires_auth: Nécessite une authentification.
        requires_javascript: Nécessite JavaScript.
        max_concurrent_requests: Requêtes concurrentes max.
        rate_limit_requests_per_minute: Limite de requêtes par minute.
    """

    site_id: str = Field(..., description="ID du site.")
    supports_search: bool = Field(default=False, description="Recherche.")
    supports_download: bool = Field(default=False, description="Téléchargement.")
    supports_cloudflare_bypass: bool = Field(default=False, description="Bypass CF.")
    requires_auth: bool = Field(default=False, description="Auth requise.")
    requires_javascript: bool = Field(default=False, description="JS requis.")
    max_concurrent_requests: int = Field(default=1, ge=1, description="Requêtes concurrentes.")
    rate_limit_requests_per_minute: int = Field(default=60, ge=1, description="Limite req/min.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class MangaSummaryResponse(BaseModel):
    """Réponse pour un résumé de manga.

    Attributes:
        id: ID unique du manga.
        title: Titre.
        author: Auteur.
        year: Année.
        status: Statut de publication.
        language: Code langue.
        cover_url: URL de la couverture.
        url: URL du manga sur le site.
        score: Score de pertinence (pour recherche).
    """

    id: str = Field(..., description="ID unique.")
    title: str = Field(..., description="Titre.")
    author: str = Field(default="", description="Auteur.")
    year: int | None = Field(default=None, description="Année.")
    status: MangaStatus = Field(default=MangaStatus.UNKNOWN, description="Statut.")
    language: str = Field(default="en", description="Code langue.")
    cover_url: str = Field(default="", description="URL couverture.")
    url: str = Field(default="", description="URL manga.")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="Score pertinence.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class SearchResponse(BaseModel):
    """Réponse pour une recherche.

    Attributes:
        site_id: ID du site.
        query: Requête de recherche.
        results: Liste des résultats.
        total: Nombre total de résultats.
        page: Page actuelle.
        page_size: Taille de page.
        has_next: S'il y a une page suivante.
        duration_ms: Durée de la recherche en ms.
    """

    site_id: str = Field(..., description="ID du site.")
    query: str = Field(..., description="Requête.")
    results: list[MangaSummaryResponse] = Field(default_factory=list, description="Résultats.")
    total: int = Field(default=0, ge=0, description="Total.")
    page: int = Field(default=1, ge=1, description="Page.")
    page_size: int = Field(default=DEFAULT_SEARCH_LIMIT, ge=1, le=MAX_SEARCH_LIMIT, description="Taille page.")
    has_next: bool = Field(default=False, description="Page suivante.")
    duration_ms: float = Field(default=0.0, ge=0.0, description="Durée ms.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class MangaDetailsResponse(BaseModel):
    """Réponse pour les détails d'un manga.

    Attributes:
        id: ID unique.
        title: Titre.
        author: Auteur.
        year: Année.
        status: Statut.
        language: Code langue.
        cover_url: URL couverture.
        url: URL manga.
        description: Description.
        tags: Liste de tags.
        chapters_count: Nombre de chapitres.
        last_updated_at: Dernière mise à jour.
    """

    id: str = Field(..., description="ID unique.")
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

    model_config = ConfigDict(frozen=True, extra="forbid")


class ChapterResponse(BaseModel):
    """Réponse pour un chapitre.

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
    """Réponse pour la liste des chapitres.

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
    """Réponse pour une page.

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
    """Réponse pour la liste des pages.

    Attributes:
        chapter_id: ID du chapitre.
        pages: Liste des pages.
        total: Nombre total de pages.
    """

    chapter_id: str = Field(..., description="ID chapitre.")
    pages: list[PageResponse] = Field(default_factory=list, description="Pages.")
    total: int = Field(default=0, ge=0, description="Total.")

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
# CACHE — Cache en mémoire pour les réponses
# ============================================================================


class ResponseCache:
    """Cache en mémoire pour les réponses API.

    Utilise un dictionnaire avec TTL (time-to-live) pour éviter
    de surcharger les sites avec des requêtes répétées.
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
            # Limiter la taille du cache
            if len(self._cache) >= self._max_size:
                # Supprimer les entrées les plus anciennes
                oldest_keys = sorted(
                    self._cache.keys(),
                    key=lambda k: self._cache[k][1],
                )[:100]
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

    async def cleanup(self) -> int:
        """Nettoie les entrées expirées.

        Returns:
            Nombre d'entrées supprimées.
        """
        async with self._lock:
            now = datetime.now(UTC)
            expired_keys = [
                key for key, (_, expires_at) in self._cache.items()
                if now > expires_at
            ]
            for key in expired_keys:
                del self._cache[key]
            return len(expired_keys)

    @property
    def size(self) -> int:
        """Taille actuelle du cache."""
        return len(self._cache)


# Instance globale du cache
_response_cache = ResponseCache()


def get_response_cache() -> ResponseCache:
    """Retourne l'instance globale du cache.

    Returns:
        Instance de ResponseCache.
    """
    return _response_cache


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
                message=t("sites.error.not_found", default="Site not found: {site_id}", site_id=site_id),
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
                message=t("sites.error.parser_unavailable", default="Parser not available for site: {site_id}", site_id=site_id),
                details={"site_id": site_id, "reason": str(e)},
            ).model_dump(),
        ) from e


def _site_to_response(site: Any) -> SiteResponse:
    """Convertit un SiteConfig en SiteResponse.

    Args:
        site: Instance de SiteConfig.

    Returns:
        Instance de SiteResponse.
    """
    return SiteResponse(
        id=site.id,
        name=site.name,
        language=site.language.value,
        domains=[d.url for d in site.domains],
        enabled=site.enabled,
        adult=site.adult,
        capabilities={
            "search": site.capabilities.supports_search,
            "download": site.capabilities.supports_download,
            "cloudflare_bypass": site.capabilities.requires_cloudflare_bypass,
            "auth": site.capabilities.requires_auth,
            "javascript": site.capabilities.requires_javascript_rendering,
        },
        priority=site.priority,
        tags=site.tags,
    )


def _manga_to_summary(manga: Any, score: float = 0.0) -> MangaSummaryResponse:
    """Convertit un Manga en MangaSummaryResponse.

    Args:
        manga: Instance de Manga.
        score: Score de pertinence.

    Returns:
        Instance de MangaSummaryResponse.
    """
    return MangaSummaryResponse(
        id=manga.id,
        title=manga.title,
        author=manga.author or "",
        year=manga.year,
        status=MangaStatus(manga.status.value) if hasattr(manga.status, "value") else MangaStatus.UNKNOWN,
        language=manga.language.value if hasattr(manga.language, "value") else "en",
        cover_url=manga.cover_url or "",
        url=manga.url or "",
        score=score,
    )


def _manga_to_details(manga: Any) -> MangaDetailsResponse:
    """Convertit un Manga en MangaDetailsResponse.

    Args:
        manga: Instance de Manga.

    Returns:
        Instance de MangaDetailsResponse.
    """
    return MangaDetailsResponse(
        id=manga.id,
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

    sites_router = APIRouter(tags=["sites"])

    # =========================================================================
    # GET /sites — Lister tous les sites
    # =========================================================================

    @sites_router.get(
        ENDPOINT_LIST_SITES,
        response_model=SiteListResponse,
        summary="Lister tous les sites supportés",
        description="Retourne la liste de tous les sites supportés par NexusDL, avec filtrage optionnel.",
        responses={
            200: {"description": "Liste des sites"},
            500: {"description": "Erreur interne"},
        },
    )
    async def list_sites(
        language: str | None = Query(None, description="Filtrer par langue (ISO 639-1)"),
        enabled_only: bool = Query(True, description="Inclure uniquement les sites activés"),
        include_adult: bool = Query(False, description="Inclure les sites pour adultes"),
        tag: str | None = Query(None, description="Filtrer par tag"),
    ) -> SiteListResponse:
        """Liste tous les sites supportés.

        Args:
            language: Filtre par langue.
            enabled_only: Inclure uniquement les sites activés.
            include_adult: Inclure les sites pour adultes.
            tag: Filtre par tag.

        Returns:
            Liste des sites.
        """
        logger.info("Liste des sites: language={}, enabled_only={}, include_adult={}", language, enabled_only, include_adult)

        try:
            from nexusdl.core.registry import get_site_registry
            registry = get_site_registry()

            # Récupérer tous les sites
            sites = registry.list_sites(
                enabled_only=enabled_only,
                include_adult=include_adult,
            )

            # Filtrer par langue
            if language:
                sites = [s for s in sites if s.language.value == language]

            # Filtrer par tag
            if tag:
                sites = [s for s in sites if tag in s.tags]

            # Convertir en réponses
            site_responses = [_site_to_response(site) for site in sites]

            # Émettre un événement
            try:
                event_bus = get_event_bus()
                await event_bus.emit(
                    EventType.CUSTOM,
                    payload={
                        "type": "api.sites.listed",
                        "count": len(site_responses),
                        "filters": {
                            "language": language,
                            "enabled_only": enabled_only,
                            "include_adult": include_adult,
                            "tag": tag,
                        },
                    },
                    source="interfaces.web.sites",
                )
            except Exception as e:
                logger.debug("Impossible d'émettre l'événement: {}", e)

            return SiteListResponse(
                sites=site_responses,
                total=len(registry.list_sites(enabled_only=False, include_adult=True)),
                filtered_count=len(site_responses),
            )

        except Exception as e:
            logger.error("Erreur lors de la liste des sites: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="internal_error",
                    message=t("sites.error.list_failed", default="Failed to list sites"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /sites/{site_id} — Détails d'un site
    # =========================================================================

    @sites_router.get(
        ENDPOINT_GET_SITE,
        response_model=SiteResponse,
        summary="Détails d'un site",
        description="Retourne les détails complets d'un site spécifique.",
        responses={
            200: {"description": "Détails du site"},
            404: {"description": "Site non trouvé"},
        },
    )
    async def get_site(site_id: str) -> SiteResponse:
        """Récupère les détails d'un site.

        Args:
            site_id: ID du site.

        Returns:
            Détails du site.
        """
        logger.info("Détails du site: {}", site_id)

        # Vérifier le cache
        cache_key = f"site:{site_id}"
        cached = await _response_cache.get(cache_key)
        if cached:
            return cached

        site = await _get_site_or_404(site_id)
        response = _site_to_response(site)

        # Stocker dans le cache
        await _response_cache.set(cache_key, response, ttl_seconds=600)

        return response

    # =========================================================================
    # GET /sites/{site_id}/health — Santé d'un site
    # =========================================================================

    @sites_router.get(
        ENDPOINT_SITE_HEALTH,
        response_model=SiteHealthResponse,
        summary="Vérifier la santé d'un site",
        description="Vérifie si un site est opérationnel et mesure son temps de réponse.",
        responses={
            200: {"description": "Statut de santé"},
            404: {"description": "Site non trouvé"},
        },
    )
    async def check_site_health(site_id: str) -> SiteHealthResponse:
        """Vérifie la santé d'un site.

        Args:
            site_id: ID du site.

        Returns:
            Statut de santé.
        """
        logger.info("Vérification santé site: {}", site_id)

        site = await _get_site_or_404(site_id)

        start_time = time.perf_counter()
        try:
            parser = await _get_parser_or_503(site_id)
            # TODO: Implémenter une méthode health_check() dans les parsers
            # Pour l'instant, on considère que si le parser est disponible, le site est healthy
            response_time_ms = (time.perf_counter() - start_time) * 1000

            return SiteHealthResponse(
                site_id=site_id,
                status=SiteHealthStatus.HEALTHY,
                response_time_ms=response_time_ms,
            )

        except Exception as e:
            response_time_ms = (time.perf_counter() - start_time) * 1000
            logger.warning("Site {} indisponible: {}", site_id, e)

            return SiteHealthResponse(
                site_id=site_id,
                status=SiteHealthStatus.DOWN,
                response_time_ms=response_time_ms,
                error_message=str(e),
            )

    # =========================================================================
    # GET /sites/{site_id}/capabilities — Capacités d'un site
    # =========================================================================

    @sites_router.get(
        ENDPOINT_SITE_CAPABILITIES,
        response_model=SiteCapabilitiesResponse,
        summary="Capacités d'un site",
        description="Retourne les capacités techniques d'un site.",
        responses={
            200: {"description": "Capacités du site"},
            404: {"description": "Site non trouvé"},
        },
    )
    async def get_site_capabilities(site_id: str) -> SiteCapabilitiesResponse:
        """Récupère les capacités d'un site.

        Args:
            site_id: ID du site.

        Returns:
            Capacités du site.
        """
        logger.info("Capacités du site: {}", site_id)

        # Vérifier le cache
        cache_key = f"site_capabilities:{site_id}"
        cached = await _response_cache.get(cache_key)
        if cached:
            return cached

        site = await _get_site_or_404(site_id)

        response = SiteCapabilitiesResponse(
            site_id=site_id,
            supports_search=site.capabilities.supports_search,
            supports_download=site.capabilities.supports_download,
            supports_cloudflare_bypass=site.capabilities.supports_cloudflare_bypass,
            requires_auth=site.capabilities.requires_auth,
            requires_javascript=site.capabilities.requires_javascript_rendering,
            max_concurrent_requests=site.capabilities.max_concurrent_requests,
            rate_limit_requests_per_minute=site.capabilities.rate_limit_requests_per_minute,
        )

        # Stocker dans le cache
        await _response_cache.set(cache_key, response, ttl_seconds=3600)

        return response

    # =========================================================================
    # GET /sites/{site_id}/search — Rechercher des mangas
    # =========================================================================

    @sites_router.get(
        ENDPOINT_SEARCH,
        response_model=SearchResponse,
        summary="Rechercher des mangas",
        description="Recherche des mangas sur un site spécifique.",
        responses={
            200: {"description": "Résultats de recherche"},
            404: {"description": "Site non trouvé"},
            503: {"description": "Parser indisponible"},
        },
    )
    async def search_manga(
        site_id: str,
        query: str = Query(..., min_length=1, max_length=200, description="Requête de recherche"),
        page: int = Query(1, ge=1, description="Numéro de page"),
        page_size: int = Query(DEFAULT_SEARCH_LIMIT, ge=1, le=MAX_SEARCH_LIMIT, description="Taille de page"),
        language: str | None = Query(None, description="Filtrer par langue"),
        status: MangaStatus | None = Query(None, description="Filtrer par statut"),
    ) -> SearchResponse:
        """Recherche des mangas sur un site.

        Args:
            site_id: ID du site.
            query: Requête de recherche.
            page: Numéro de page.
            page_size: Taille de page.
            language: Filtre par langue.
            status: Filtre par statut.

        Returns:
            Résultats de recherche.
        """
        logger.info("Recherche sur {}: query={!r}, page={}, page_size={}", site_id, query, page, page_size)

        # Vérifier le cache
        cache_key = f"search:{site_id}:{query}:{page}:{page_size}:{language}:{status}"
        cached = await _response_cache.get(cache_key)
        if cached:
            return cached

        # Vérifier que le site existe
        await _get_site_or_404(site_id)

        # Obtenir le parser
        parser = await _get_parser_or_503(site_id)

        start_time = time.perf_counter()

        try:
            # Exécuter la recherche
            results = await parser.search(query)

            # Appliquer les filtres
            if language:
                results = [r for r in results if r.manga.language.value == language]

            if status:
                results = [r for r in results if r.manga.status.value == status.value]

            # Paginer
            total = len(results)
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            paginated_results = results[start_idx:end_idx]

            # Convertir en réponses
            manga_responses = [
                _manga_to_summary(result.manga, score=result.score)
                for result in paginated_results
            ]

            duration_ms = (time.perf_counter() - start_time) * 1000

            response = SearchResponse(
                site_id=site_id,
                query=query,
                results=manga_responses,
                total=total,
                page=page,
                page_size=page_size,
                has_next=end_idx < total,
                duration_ms=duration_ms,
            )

            # Stocker dans le cache
            await _response_cache.set(cache_key, response, ttl_seconds=300)

            # Émettre un événement
            try:
                event_bus = get_event_bus()
                await event_bus.emit(
                    EventType.CUSTOM,
                    payload={
                        "type": "api.sites.search",
                        "site_id": site_id,
                        "query": query,
                        "results_count": len(manga_responses),
                        "duration_ms": duration_ms,
                    },
                    source="interfaces.web.sites",
                )
            except Exception as e:
                logger.debug("Impossible d'émettre l'événement: {}", e)

            return response

        except Exception as e:
            logger.error("Erreur lors de la recherche sur {}: {}", site_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="search_failed",
                    message=t("sites.error.search_failed", default="Search failed on site: {site_id}", site_id=site_id),
                    details={"query": query, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /sites/{site_id}/manga/{manga_id} — Détails d'un manga
    # =========================================================================

    @sites_router.get(
        ENDPOINT_GET_MANGA,
        response_model=MangaDetailsResponse,
        summary="Détails d'un manga",
        description="Retourne les détails complets d'un manga spécifique.",
        responses={
            200: {"description": "Détails du manga"},
            404: {"description": "Manga non trouvé"},
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
        cache_key = f"manga:{site_id}:{manga_id}"
        cached = await _response_cache.get(cache_key)
        if cached:
            return cached

        # Vérifier que le site existe
        await _get_site_or_404(site_id)

        # Obtenir le parser
        parser = await _get_parser_or_503(site_id)

        try:
            # Récupérer le manga
            manga = await parser.get_manga(manga_id)

            response = _manga_to_details(manga)

            # Stocker dans le cache
            await _response_cache.set(cache_key, response, ttl_seconds=600)

            return response

        except Exception as e:
            logger.error("Erreur lors de la récupération du manga {}: {}", manga_id, e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse(
                    error="manga_not_found",
                    message=t("sites.error.manga_not_found", default="Manga not found: {manga_id}", manga_id=manga_id),
                    details={"manga_id": manga_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /sites/{site_id}/manga/{manga_id}/chapters — Liste des chapitres
    # =========================================================================

    @sites_router.get(
        ENDPOINT_GET_CHAPTERS,
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
        cache_key = f"chapters:{site_id}:{manga_id}"
        cached = await _response_cache.get(cache_key)
        if cached:
            return cached

        # Vérifier que le site existe
        await _get_site_or_404(site_id)

        # Obtenir le parser
        parser = await _get_parser_or_503(site_id)

        try:
            # Récupérer les chapitres
            chapters = await parser.get_chapters(manga_id)

            # Convertir en réponses
            chapter_responses = [_chapter_to_response(chapter) for chapter in chapters]

            response = ChaptersResponse(
                manga_id=manga_id,
                chapters=chapter_responses,
                total=len(chapter_responses),
            )

            # Stocker dans le cache
            await _response_cache.set(cache_key, response, ttl_seconds=300)

            return response

        except Exception as e:
            logger.error("Erreur lors de la récupération des chapitres de {}: {}", manga_id, e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse(
                    error="manga_not_found",
                    message=t("sites.error.manga_not_found", default="Manga not found: {manga_id}", manga_id=manga_id),
                    details={"manga_id": manga_id, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /sites/{site_id}/manga/{manga_id}/chapters/{chapter_id}/pages — Liste des pages
    # =========================================================================

    @sites_router.get(
        ENDPOINT_GET_PAGES,
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
        cache_key = f"pages:{site_id}:{manga_id}:{chapter_id}"
        cached = await _response_cache.get(cache_key)
        if cached:
            return cached

        # Vérifier que le site existe
        await _get_site_or_404(site_id)

        # Obtenir le parser
        parser = await _get_parser_or_503(site_id)

        try:
            # Récupérer les pages
            pages = await parser.get_pages(chapter_id)

            # Convertir en réponses
            page_responses = [_page_to_response(page, i + 1) for i, page in enumerate(pages)]

            response = PagesResponse(
                chapter_id=chapter_id,
                pages=page_responses,
                total=len(page_responses),
            )

            # Stocker dans le cache
            await _response_cache.set(cache_key, response, ttl_seconds=600)

            return response

        except Exception as e:
            logger.error("Erreur lors de la récupération des pages de {}: {}", chapter_id, e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse(
                    error="chapter_not_found",
                    message=t("sites.error.chapter_not_found", default="Chapter not found: {chapter_id}", chapter_id=chapter_id),
                    details={"chapter_id": chapter_id, "reason": str(e)},
                ).model_dump(),
            ) from e


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_SEARCH_LIMIT",
    "MAX_SEARCH_LIMIT",
    "DEFAULT_CACHE_TTL_SECONDS",
    "MAX_CACHE_SIZE",
    "DEFAULT_TIMEOUT_SECONDS",
    "ALL_CHANNELS",
    # Endpoints
    "ENDPOINT_LIST_SITES",
    "ENDPOINT_GET_SITE",
    "ENDPOINT_SITE_HEALTH",
    "ENDPOINT_SITE_CAPABILITIES",
    "ENDPOINT_SEARCH",
    "ENDPOINT_GET_MANGA",
    "ENDPOINT_GET_CHAPTERS",
    "ENDPOINT_GET_PAGES",
    # Exceptions
    "SitesRouterError",
    "SiteNotFoundError",
    "ParserNotAvailableError",
    "SearchError",
    # Enums
    "SiteHealthStatus",
    "MangaStatus",
    # Modèles de réponse
    "SiteResponse",
    "SiteListResponse",
    "SiteHealthResponse",
    "SiteCapabilitiesResponse",
    "MangaSummaryResponse",
    "SearchResponse",
    "MangaDetailsResponse",
    "ChapterResponse",
    "ChaptersResponse",
    "PageResponse",
    "PagesResponse",
    "ErrorResponse",
    # Cache
    "ResponseCache",
    "get_response_cache",
    # Routeur
    "sites_router" if FASTAPI_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
