"""Module public des routeurs FastAPI de l'API REST NexusDL.

Ce module constitue le point d'entrée unique pour tous les routeurs FastAPI
de l'interface web NexusDL. Il agrège et ré-exporte les symboles publics
des 10 routeurs :

    - `health.py`    : Endpoints de santé (liveness, readiness, metrics)
    - `sites.py`     : Gestion des sites (parsers)
    - `search.py`    : Recherche multi-sites
    - `manga.py`     : Gestion des mangas individuels
    - `library.py`   : Bibliothèque locale
    - `download.py`  : Gestion des téléchargements
    - `chapters.py`  : Gestion des chapitres et streaming d'images
    - `auth.py`      : Authentification et gestion des utilisateurs
    - `settings.py`  : Configuration de l'application
    - `ws.py`        : WebSocket pour communications temps réel

Architecture :
    Tous les routeurs partagent :
        - Style de réponse cohérent (ErrorResponse standardisé)
        - Validation stricte via Pydantic v2
        - Cache en mémoire pour les lectures fréquentes
        - Événements EventBus pour monitoring
        - Logging structuré
        - Traductions i18n
        - Gestion robuste des erreurs (404, 400, 500, 503)

Résolution des conflits de noms :
    Plusieurs routeurs définissent des modèles avec des noms similaires.
    Pour éviter les collisions, nous utilisons des suffixes contextuels :

    Modèles de chapitres :
        - ChapterResponse (sites)      → SitesChapterResponse
        - ChapterResponse (manga)      → MangaChapterResponse
        - ChapterResponse (library)    → LibraryChapterResponse
        - ChapterResponse (chapters)   → ChapterDetailResponse

    Modèles de pages :
        - PageResponse (sites)         → SitesPageResponse
        - PageResponse (manga)         → MangaPageResponse
        - PageResponse (chapters)      → ChapterPageResponse

    Enums de statut :
        - MangaStatus (sites)          → SitesMangaStatus
        - MangaStatus (manga)          → MangaPublicationStatus
        - ReadingStatus (library)      → LibraryReadingStatus
        - ReadingStatus (manga)        → MangaReadingStatus

    Enums de format/qualité :
        - DownloadFormat (manga)       → MangaDownloadFormat
        - DownloadFormat (download)    → DownloadTaskFormat
        - ImageQuality (manga)         → MangaImageQuality
        - ImageQuality (download)      → DownloadImageQuality

    Modèles de réponse d'erreur :
        - ErrorResponse (tous)         → APIErrorResponse (version canonique)

Exemple d'utilisation — Setup complet :
    >>> from fastapi import FastAPI
    >>> from nexusdl.interfaces.web.backend.routers import setup_all_routers
    >>>
    >>> app = FastAPI(
    ...     title="NexusDL API",
    ...     version="0.1.0",
    ...     description="API REST pour NexusDL",
    ... )
    >>>
    >>> # Inclure tous les routeurs en une fois
    >>> setup_all_routers(app, prefix="/api/v1")

Exemple d'utilisation — Inclusion manuelle :
    >>> from fastapi import FastAPI
    >>> from nexusdl.interfaces.web.backend.routers import (
    ...     health_router,
    ...     sites_router,
    ...     search_router,
    ...     manga_router,
    ...     library_router,
    ...     download_router,
    ...     chapters_router,
    ...     auth_router,
    ...     settings_router,
    ...     ws_router,
    ... )
    >>>
    >>> app = FastAPI()
    >>>
    >>> # Ordre recommandé : health en premier (pas d'auth),
    >>> # puis auth, puis les autres, puis ws en dernier
    >>> app.include_router(health_router, prefix="/api/v1")
    >>> app.include_router(auth_router, prefix="/api/v1")
    >>> app.include_router(sites_router, prefix="/api/v1")
    >>> app.include_router(search_router, prefix="/api/v1")
    >>> app.include_router(manga_router, prefix="/api/v1")
    >>> app.include_router(chapters_router, prefix="/api/v1")
    >>> app.include_router(library_router, prefix="/api/v1")
    >>> app.include_router(download_router, prefix="/api/v1")
    >>> app.include_router(settings_router, prefix="/api/v1")
    >>> app.include_router(ws_router, prefix="/api/v1")

Intégration :
    - fastapi                            : Framework web
    - interfaces/web/backend/middleware/*: Middlewares (auth, CORS, etc.)
    - core/config.py                     : Configuration globale
    - core/events.py                     : EventBus pour monitoring
    - core/logger.py                     : Système de logging
    - core/i18n.py                       : Traductions
    - core/registry/                     : Registre des sites
    - core/library/                      : Bibliothèque locale
    - core/downloader/                   : Gestionnaire de téléchargements
"""

from __future__ import annotations

from typing import Any

# ============================================================================
# ROUTEUR HEALTH — health.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.web.backend.routers.health import (
    ComponentCheckError,
    HealthCheckError,
)

# Enums
from nexusdl.interfaces.web.backend.routers.health import (
    ComponentType,
    HealthStatus,
)

# Modèles de réponse
from nexusdl.interfaces.web.backend.routers.health import (
    ComponentHealthResponse,
    DetailedHealthResponse,
    HealthResponse,
    MetricsResponse,
    VersionResponse,
)

# Cache
from nexusdl.interfaces.web.backend.routers.health import (
    HealthCache,
    get_health_cache,
)

# Routeur
from nexusdl.interfaces.web.backend.routers.health import health_router

# ============================================================================
# ROUTEUR SITES — sites.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.web.backend.routers.sites import (
    ParserNotAvailableError,
    SearchError as SitesSearchError,
    SiteNotFoundError as SitesSiteNotFoundError,
    SitesRouterError,
)

# Enums
from nexusdl.interfaces.web.backend.routers.sites import (
    MangaStatus as SitesMangaStatus,
    SiteHealthStatus,
)

# Modèles de réponse
from nexusdl.interfaces.web.backend.routers.sites import (
    ChapterResponse as SitesChapterResponse,
    ChaptersResponse as SitesChaptersResponse,
    MangaDetailsResponse as SitesMangaDetailsResponse,
    MangaSummaryResponse,
    PageResponse as SitesPageResponse,
    PagesResponse as SitesPagesResponse,
    SearchResponse as SitesSearchResponse,
    SiteCapabilitiesResponse,
    SiteHealthResponse,
    SiteListResponse,
    SiteResponse,
)

# Cache
from nexusdl.interfaces.web.backend.routers.sites import (
    ResponseCache as SitesResponseCache,
    get_response_cache,
)

# Routeur
from nexusdl.interfaces.web.backend.routers.sites import sites_router

# ============================================================================
# ROUTEUR SEARCH — search.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.web.backend.routers.search import (
    InvalidQueryError,
    SearchExecutionError,
    SearchRouterError,
    SearchTimeoutError,
)

# Enums
from nexusdl.interfaces.web.backend.routers.search import (
    MangaStatusFilter,
    SearchSortBy,
)

# Modèles de requête
from nexusdl.interfaces.web.backend.routers.search import SearchRequest

# Modèles de réponse
from nexusdl.interfaces.web.backend.routers.search import (
    PopularSearchResponse,
    SearchHistoryEntry,
    SearchHistoryResponse,
    SearchResponse,
    SearchResultItem,
    SiteSearchError,
    SuggestionItem,
    SuggestionsResponse,
)

# Cache et historique
from nexusdl.interfaces.web.backend.routers.search import (
    SearchCache,
    SearchHistory,
    get_search_cache,
    get_search_history,
)

# Routeur
from nexusdl.interfaces.web.backend.routers.search import search_router

# ============================================================================
# ROUTEUR MANGA — manga.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.web.backend.routers.manga import (
    ChapterNotFoundError as MangaChapterNotFoundError,
    DownloadError,
    LibraryError,
    MangaNotFoundError as MangaMangaNotFoundError,
    MangaRouterError,
)

# Enums
from nexusdl.interfaces.web.backend.routers.manga import (
    DownloadFormat as MangaDownloadFormat,
    ImageQuality as MangaImageQuality,
    MangaStatus as MangaPublicationStatus,
    ReadingStatus as MangaReadingStatus,
)

# Modèles de requête
from nexusdl.interfaces.web.backend.routers.manga import (
    AddToLibraryRequest,
    DownloadRequest as MangaDownloadRequest,
    UpdateReadStatusRequest as MangaUpdateReadStatusRequest,
)

# Modèles de réponse
from nexusdl.interfaces.web.backend.routers.manga import (
    ChapterResponse as MangaChapterResponse,
    ChaptersResponse as MangaChaptersResponse,
    CoverResponse,
    DownloadTaskResponse as MangaDownloadTaskResponse,
    LibraryResponse,
    MangaDetailsResponse,
    PageResponse as MangaPageResponse,
    PagesResponse as MangaPagesResponse,
    ReadStatusResponse,
)

# Cache
from nexusdl.interfaces.web.backend.routers.manga import (
    MangaCache,
    get_manga_cache,
)

# Routeur
from nexusdl.interfaces.web.backend.routers.manga import manga_router

# ============================================================================
# ROUTEUR LIBRARY — library.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.web.backend.routers.library import (
    ChapterNotFoundError as LibraryChapterNotFoundError,
    LibraryRouterError,
    MangaNotFoundError as LibraryMangaNotFoundError,
    ReadingListNotFoundError,
    ScanAlreadyRunningError,
    ScanError,
)

# Enums
from nexusdl.interfaces.web.backend.routers.library import (
    LibrarySortBy,
    ReadingStatus as LibraryReadingStatus,
    ScanStatus,
)

# Modèles de requête
from nexusdl.interfaces.web.backend.routers.library import (
    AddMangaToListRequest,
    CreateReadingListRequest,
    UpdateChapterRequest as LibraryUpdateChapterRequest,
    UpdateMangaRequest,
    UpdateReadingListRequest,
)

# Modèles de réponse
from nexusdl.interfaces.web.backend.routers.library import (
    LibraryChapterResponse,
    LibraryChaptersResponse,
    LibraryListResponse,
    LibraryMangaResponse,
    LibraryStatsResponse,
    ReadingListDetailResponse,
    ReadingListResponse,
    ReadingListsResponse,
    ScanResponse,
    ScanStatusResponse,
    UpdateResponse as LibraryUpdateResponse,
)

# Cache et scan state
from nexusdl.interfaces.web.backend.routers.library import (
    LibraryCache,
    ScanState,
    get_library_cache,
    get_scan_state,
)

# Routeur
from nexusdl.interfaces.web.backend.routers.library import library_router

# ============================================================================
# ROUTEUR DOWNLOAD — download.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.web.backend.routers.download import (
    DownloadManagerNotAvailableError,
    DownloadRouterError,
    TaskActionError,
    TaskNotFoundError,
)

# Enums
from nexusdl.interfaces.web.backend.routers.download import (
    DownloadFilter,
    DownloadFormat as DownloadTaskFormat,
    DownloadSortBy,
    DownloadStatus,
    ImageQuality as DownloadImageQuality,
    Priority,
)

# Modèles de requête
from nexusdl.interfaces.web.backend.routers.download import (
    CreateDownloadRequest,
    TaskActionRequest,
)

# Modèles de réponse
from nexusdl.interfaces.web.backend.routers.download import (
    BulkActionResponse,
    DownloadListResponse,
    DownloadStatsResponse,
    DownloadTaskResponse,
    TaskActionResponse as DownloadTaskActionResponse,
)

# Cache
from nexusdl.interfaces.web.backend.routers.download import (
    DownloadCache,
    get_download_cache,
)

# Routeur
from nexusdl.interfaces.web.backend.routers.download import download_router

# ============================================================================
# ROUTEUR CHAPTERS — chapters.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.web.backend.routers.chapters import (
    ChapterNotFoundError as ChaptersChapterNotFoundError,
    ChapterRouterError,
    ImageFetchError,
    InvalidPageNumberError,
    PageNotFoundError,
)

# Enums
from nexusdl.interfaces.web.backend.routers.chapters import (
    ImageFormat,
    ReadStatus,
)

# Modèles de requête
from nexusdl.interfaces.web.backend.routers.chapters import (
    DownloadChapterRequest,
    UpdateProgressRequest,
    UpdateReadStatusRequest,
)

# Modèles de réponse
from nexusdl.interfaces.web.backend.routers.chapters import (
    ChapterDetailResponse,
    DownloadTaskResponse as ChapterDownloadTaskResponse,
    PageResponse as ChapterPageResponse,
    PagesListResponse,
    ProgressResponse,
    ReaderDataResponse,
    UpdateResponse as ChapterUpdateResponse,
)

# Cache
from nexusdl.interfaces.web.backend.routers.chapters import (
    ChapterCache,
    get_chapter_cache,
)

# Helpers
from nexusdl.interfaces.web.backend.routers.chapters import (
    fetch_image_bytes,
    parse_range_header,
)

# Routeur
from nexusdl.interfaces.web.backend.routers.chapters import chapters_router

# ============================================================================
# ROUTEUR AUTH — auth.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.web.backend.routers.auth import (
    AccountLockedError,
    AuthRouterError,
    AuthenticationFailedError,
    InvalidTokenError,
    UserAlreadyExistsError,
    UserNotFoundError,
    WeakPasswordError,
)

# Enums
from nexusdl.interfaces.web.backend.routers.auth import (
    ApiKeyStatus,
    SessionStatus,
    TokenType,
)

# Modèles de stockage
from nexusdl.interfaces.web.backend.routers.auth import (
    PasswordResetToken,
    StoredApiKey,
    StoredSession,
    StoredUser,
)

# Modèles de requête
from nexusdl.interfaces.web.backend.routers.auth import (
    ChangePasswordRequest,
    CreateApiKeyRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RefreshTokenRequest,
    RegisterRequest,
    ResetPasswordRequest,
    RevokeTokenRequest,
    UpdateProfileRequest,
)

# Modèles de réponse
from nexusdl.interfaces.web.backend.routers.auth import (
    ActionResponse,
    ApiKeyListResponse,
    ApiKeyResponse,
    ErrorResponse as AuthErrorResponse,
    LoginResponse,
    RefreshTokenResponse,
    RegisterResponse,
    SessionListResponse,
    SessionResponse,
    UserProfileResponse,
)

# Auth Store
from nexusdl.interfaces.web.backend.routers.auth import (
    AuthStore,
    InMemoryAuthStore,
    get_auth_store,
    set_auth_store,
)

# Helpers
from nexusdl.interfaces.web.backend.routers.auth import (
    generate_token,
    hash_password,
    validate_password_strength,
    verify_password,
)

# Routeur
from nexusdl.interfaces.web.backend.routers.auth import auth_router

# ============================================================================
# ROUTEUR SETTINGS — settings.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.web.backend.routers.settings import (
    ConfigLoadError,
    ConfigSaveError,
    ConfigValidationError,
    SectionNotFoundError,
    SettingsRouterError,
)

# Enums
from nexusdl.interfaces.web.backend.routers.settings import (
    ChangeAction,
    ExportFormat,
)

# Modèles de requête
from nexusdl.interfaces.web.backend.routers.settings import (
    ExportRequest,
    ImportRequest,
    ResetRequest,
    UpdateSectionRequest,
    UpdateSettingsRequest,
)

# Modèles de réponse
from nexusdl.interfaces.web.backend.routers.settings import (
    ChangeEntry,
    DefaultsResponse,
    ErrorResponse as SettingsErrorResponse,
    ExportResponse,
    HistoryResponse,
    ImportResponse,
    ResetResponse,
    SchemaResponse,
    SectionResponse,
    SectionsListResponse,
    SettingsResponse,
    UpdateResponse as SettingsUpdateResponse,
)

# Cache et historique
from nexusdl.interfaces.web.backend.routers.settings import (
    ChangeHistory,
    SettingsCache,
    get_change_history,
    get_settings_cache,
)

# Routeur
from nexusdl.interfaces.web.backend.routers.settings import settings_router

# ============================================================================
# ROUTEUR WEBSOCKET — ws.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.web.backend.routers.ws import (
    WebSocketAuthError,
    WebSocketChannelError,
    WebSocketError,
    WebSocketMessageError,
    WebSocketRateLimitError,
)

# Enums
from nexusdl.interfaces.web.backend.routers.ws import (
    ClientState,
    MessageType,
)

# Modèles
from nexusdl.interfaces.web.backend.routers.ws import (
    WebSocketClient,
    WebSocketClientInfo,
    WebSocketConfig,
    WebSocketMessage,
    WebSocketStats,
)

# Classes
from nexusdl.interfaces.web.backend.routers.ws import (
    ConnectionManager,
    WebSocketAuthHandler,
)

# Constantes de channels
from nexusdl.interfaces.web.backend.routers.ws import (
    ALL_CHANNELS,
    CHANNEL_DOWNLOADS_COMPLETED,
    CHANNEL_DOWNLOADS_FAILED,
    CHANNEL_DOWNLOADS_PROGRESS,
    CHANNEL_LIBRARY_CHANGES,
    CHANNEL_LOGS_STREAM,
    CHANNEL_NOTIFICATIONS_ERROR,
    CHANNEL_NOTIFICATIONS_INFO,
    CHANNEL_NOTIFICATIONS_WARNING,
    CHANNEL_SEARCH_RESULTS,
    CHANNEL_SYSTEM_EVENTS,
    CHANNEL_SYSTEM_STATUS,
)

# Codes de fermeture WebSocket
from nexusdl.interfaces.web.backend.routers.ws import (
    WS_CLOSE_ABNORMAL,
    WS_CLOSE_AUTH_FAILED,
    WS_CLOSE_AUTH_REQUIRED,
    WS_CLOSE_CHANNEL_NOT_FOUND,
    WS_CLOSE_GOING_AWAY,
    WS_CLOSE_INTERNAL_ERROR,
    WS_CLOSE_INVALID_PAYLOAD,
    WS_CLOSE_MANDATORY_EXTENSION,
    WS_CLOSE_MESSAGE_TOO_BIG,
    WS_CLOSE_NORMAL,
    WS_CLOSE_PERMISSION_DENIED,
    WS_CLOSE_POLICY_VIOLATION,
    WS_CLOSE_PROTOCOL_ERROR,
    WS_CLOSE_RATE_LIMITED,
    WS_CLOSE_SERVICE_RESTART,
    WS_CLOSE_TRY_AGAIN_LATER,
    WS_CLOSE_UNSUPPORTED_DATA,
)

# Instance globale
from nexusdl.interfaces.web.backend.routers.ws import (
    get_auth_handler,
    get_connection_manager,
    set_auth_handler,
    set_connection_manager,
)

# Fonctions helpers
from nexusdl.interfaces.web.backend.routers.ws import (
    broadcast_to_channel,
    get_ws_stats,
    init_websocket_router,
    send_notification,
    send_to_user,
)

# Routeur
from nexusdl.interfaces.web.backend.routers.ws import ws_router

# ============================================================================
# MODÈLE D'ERREUR CANONIQUE — ErrorResponse standardisé
# ============================================================================


class APIErrorResponse:
    """Modèle d'erreur canonique pour toute l'API.

    Ce modèle est utilisé comme référence pour toutes les réponses d'erreur
    de l'API. Il est compatible avec tous les routeurs.

    Attributes:
        error: Code d'erreur machine-readable.
        message: Message d'erreur human-readable.
        details: Détails additionnels (optionnel).

    Example:
        {
            "error": "not_found",
            "message": "Resource not found",
            "details": {"resource_id": "123"}
        }
    """

    def __init__(
        self,
        error: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialise la réponse d'erreur.

        Args:
            error: Code d'erreur.
            message: Message d'erreur.
            details: Détails additionnels.
        """
        self.error = error
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Convertit en dictionnaire.

        Returns:
            Dictionnaire sérialisable.
        """
        return {
            "error": self.error,
            "message": self.message,
            "details": self.details,
        }


# ============================================================================
# FONCTION HELPER — Configuration complète des routeurs
# ============================================================================


def setup_all_routers(
    app: Any,
    *,
    prefix: str = "/api/v1",
    include_health: bool = True,
    include_auth: bool = True,
    include_sites: bool = True,
    include_search: bool = True,
    include_manga: bool = True,
    include_chapters: bool = True,
    include_library: bool = True,
    include_download: bool = True,
    include_settings: bool = True,
    include_ws: bool = True,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Configure tous les routeurs de l'application en une seule fois.

    L'ordre d'inclusion est important :
        1. health (pas d'auth requis, pour monitoring)
        2. auth (pour les tokens)
        3. sites, search, manga, chapters, library, download, settings
        4. ws (WebSocket en dernier)

    Args:
        app: Instance FastAPI.
        prefix: Préfixe commun pour tous les routeurs.
        include_health: Inclure le routeur health.
        include_auth: Inclure le routeur auth.
        include_sites: Inclure le routeur sites.
        include_search: Inclure le routeur search.
        include_manga: Inclure le routeur manga.
        include_chapters: Inclure le routeur chapters.
        include_library: Inclure le routeur library.
        include_download: Inclure le routeur download.
        include_settings: Inclure le routeur settings.
        include_ws: Inclure le routeur WebSocket.
        tags: Tags OpenAPI additionnels.

    Returns:
        Dictionnaire des routeurs inclus.

    Example:
        >>> from fastapi import FastAPI
        >>> app = FastAPI()
        >>> setup_all_routers(app, prefix="/api/v1")
    """
    from loguru import logger

    included: dict[str, Any] = {}

    # 1. Health (monitoring, pas d'auth)
    if include_health:
        app.include_router(health_router, prefix=prefix)
        included["health"] = health_router
        logger.debug("Routeur health inclus")

    # 2. Auth (tokens, sessions)
    if include_auth:
        app.include_router(auth_router, prefix=prefix)
        included["auth"] = auth_router
        logger.debug("Routeur auth inclus")

    # 3. Sites (parsers)
    if include_sites:
        app.include_router(sites_router, prefix=prefix)
        included["sites"] = sites_router
        logger.debug("Routeur sites inclus")

    # 4. Search (recherche multi-sites)
    if include_search:
        app.include_router(search_router, prefix=prefix)
        included["search"] = search_router
        logger.debug("Routeur search inclus")

    # 5. Manga (gestion des mangas)
    if include_manga:
        app.include_router(manga_router, prefix=prefix)
        included["manga"] = manga_router
        logger.debug("Routeur manga inclus")

    # 6. Chapters (chapitres et streaming)
    if include_chapters:
        app.include_router(chapters_router, prefix=prefix)
        included["chapters"] = chapters_router
        logger.debug("Routeur chapters inclus")

    # 7. Library (bibliothèque locale)
    if include_library:
        app.include_router(library_router, prefix=prefix)
        included["library"] = library_router
        logger.debug("Routeur library inclus")

    # 8. Download (téléchargements)
    if include_download:
        app.include_router(download_router, prefix=prefix)
        included["download"] = download_router
        logger.debug("Routeur download inclus")

    # 9. Settings (configuration)
    if include_settings:
        app.include_router(settings_router, prefix=prefix)
        included["settings"] = settings_router
        logger.debug("Routeur settings inclus")

    # 10. WebSocket (en dernier)
    if include_ws:
        # Le routeur WebSocket a son propre préfixe /ws
        ws_prefix = prefix.replace("/api/v1", "") + "/ws" if prefix.startswith("/api/v1") else "/ws"
        app.include_router(ws_router, prefix=ws_prefix)
        included["ws"] = ws_router
        logger.debug("Routeur WebSocket inclus")

    logger.info(
        "Routeurs configurés: {} sur 10 (prefix={})",
        len(included),
        prefix,
    )

    return included


def setup_dev_routers(app: Any, *, prefix: str = "/api/v1") -> dict[str, Any]:
    """Configure les routeurs pour le développement.

    Inclut tous les routeurs avec des configurations permissives.

    Args:
        app: Instance FastAPI.
        prefix: Préfixe commun.

    Returns:
        Dictionnaire des routeurs inclus.
    """
    return setup_all_routers(app, prefix=prefix)


def setup_prod_routers(
    app: Any,
    *,
    prefix: str = "/api/v1",
    enable_ws: bool = True,
) -> dict[str, Any]:
    """Configure les routeurs pour la production.

    Args:
        app: Instance FastAPI.
        prefix: Préfixe commun.
        enable_ws: Activer le WebSocket.

    Returns:
        Dictionnaire des routeurs inclus.
    """
    return setup_all_routers(
        app,
        prefix=prefix,
        include_ws=enable_ws,
    )


# ============================================================================
# MÉTADONNÉES
# ============================================================================

__version__: str = "0.1.0"
__author__: str = "NexusDL Team"
__license__: str = "MIT"


# ============================================================================
# EXPORTS PUBLICS
# ============================================================================

__all__ = [
    # ========================================================================
    # Métadonnées
    # ========================================================================
    "__version__",
    "__author__",
    "__license__",
    # ========================================================================
    # Fonctions de setup
    # ========================================================================
    "setup_all_routers",
    "setup_dev_routers",
    "setup_prod_routers",
    # ========================================================================
    # Modèle d'erreur canonique
    # ========================================================================
    "APIErrorResponse",
    # ========================================================================
    # ROUTEURS — Les 10 routeurs principaux
    # ========================================================================
    "health_router",
    "sites_router",
    "search_router",
    "manga_router",
    "library_router",
    "download_router",
    "chapters_router",
    "auth_router",
    "settings_router",
    "ws_router",
    # ========================================================================
    # ROUTEUR HEALTH — health.py
    # ========================================================================
    # Exceptions
    "HealthCheckError",
    "ComponentCheckError",
    # Enums
    "HealthStatus",
    "ComponentType",
    # Modèles
    "HealthResponse",
    "ComponentHealthResponse",
    "DetailedHealthResponse",
    "VersionResponse",
    "MetricsResponse",
    # Cache
    "HealthCache",
    "get_health_cache",
    # ========================================================================
    # ROUTEUR SITES — sites.py
    # ========================================================================
    # Exceptions
    "SitesRouterError",
    "SitesSiteNotFoundError",
    "ParserNotAvailableError",
    "SitesSearchError",
    # Enums
    "SiteHealthStatus",
    "SitesMangaStatus",
    # Modèles
    "SiteResponse",
    "SiteListResponse",
    "SiteHealthResponse",
    "SiteCapabilitiesResponse",
    "MangaSummaryResponse",
    "SitesSearchResponse",
    "SitesMangaDetailsResponse",
    "SitesChapterResponse",
    "SitesChaptersResponse",
    "SitesPageResponse",
    "SitesPagesResponse",
    # Cache
    "SitesResponseCache",
    "get_response_cache",
    # ========================================================================
    # ROUTEUR SEARCH — search.py
    # ========================================================================
    # Exceptions
    "SearchRouterError",
    "SearchExecutionError",
    "SearchTimeoutError",
    "InvalidQueryError",
    # Enums
    "SearchSortBy",
    "MangaStatusFilter",
    # Modèles de requête
    "SearchRequest",
    # Modèles de réponse
    "SearchResultItem",
    "SiteSearchError",
    "SearchResponse",
    "SuggestionItem",
    "SuggestionsResponse",
    "SearchHistoryEntry",
    "SearchHistoryResponse",
    "PopularSearchResponse",
    # Cache et historique
    "SearchCache",
    "SearchHistory",
    "get_search_cache",
    "get_search_history",
    # ========================================================================
    # ROUTEUR MANGA — manga.py
    # ========================================================================
    # Exceptions
    "MangaRouterError",
    "MangaMangaNotFoundError",
    "MangaChapterNotFoundError",
    "DownloadError",
    "LibraryError",
    # Enums
    "MangaPublicationStatus",
    "MangaReadingStatus",
    "MangaDownloadFormat",
    "MangaImageQuality",
    # Modèles de requête
    "MangaDownloadRequest",
    "AddToLibraryRequest",
    "MangaUpdateReadStatusRequest",
    # Modèles de réponse
    "MangaDetailsResponse",
    "MangaChapterResponse",
    "MangaChaptersResponse",
    "MangaPageResponse",
    "MangaPagesResponse",
    "MangaDownloadTaskResponse",
    "LibraryResponse",
    "ReadStatusResponse",
    "CoverResponse",
    # Cache
    "MangaCache",
    "get_manga_cache",
    # ========================================================================
    # ROUTEUR LIBRARY — library.py
    # ========================================================================
    # Exceptions
    "LibraryRouterError",
    "LibraryMangaNotFoundError",
    "ReadingListNotFoundError",
    "LibraryChapterNotFoundError",
    "ScanError",
    "ScanAlreadyRunningError",
    # Enums
    "LibraryReadingStatus",
    "LibrarySortBy",
    "ScanStatus",
    # Modèles de requête
    "UpdateMangaRequest",
    "LibraryUpdateChapterRequest",
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
    "LibraryUpdateResponse",
    # Cache et scan state
    "LibraryCache",
    "ScanState",
    "get_library_cache",
    "get_scan_state",
    # ========================================================================
    # ROUTEUR DOWNLOAD — download.py
    # ========================================================================
    # Exceptions
    "DownloadRouterError",
    "TaskNotFoundError",
    "TaskActionError",
    "DownloadManagerNotAvailableError",
    # Enums
    "DownloadStatus",
    "DownloadFilter",
    "DownloadSortBy",
    "DownloadTaskFormat",
    "DownloadImageQuality",
    "Priority",
    # Modèles de requête
    "CreateDownloadRequest",
    "TaskActionRequest",
    # Modèles de réponse
    "DownloadTaskResponse",
    "DownloadListResponse",
    "DownloadStatsResponse",
    "DownloadTaskActionResponse",
    "BulkActionResponse",
    # Cache
    "DownloadCache",
    "get_download_cache",
    # ========================================================================
    # ROUTEUR CHAPTERS — chapters.py
    # ========================================================================
    # Exceptions
    "ChapterRouterError",
    "ChaptersChapterNotFoundError",
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
    "ChapterPageResponse",
    "PagesListResponse",
    "ProgressResponse",
    "ReaderDataResponse",
    "ChapterDownloadTaskResponse",
    "ChapterUpdateResponse",
    # Cache
    "ChapterCache",
    "get_chapter_cache",
    # Helpers
    "parse_range_header",
    "fetch_image_bytes",
    # ========================================================================
    # ROUTEUR AUTH — auth.py
    # ========================================================================
    # Exceptions
    "AuthRouterError",
    "AuthenticationFailedError",
    "UserNotFoundError",
    "UserAlreadyExistsError",
    "InvalidTokenError",
    "AccountLockedError",
    "WeakPasswordError",
    # Enums
    "TokenType",
    "SessionStatus",
    "ApiKeyStatus",
    # Modèles de stockage
    "StoredUser",
    "StoredSession",
    "StoredApiKey",
    "PasswordResetToken",
    # Modèles de requête
    "LoginRequest",
    "RegisterRequest",
    "RefreshTokenRequest",
    "RevokeTokenRequest",
    "CreateApiKeyRequest",
    "UpdateProfileRequest",
    "ChangePasswordRequest",
    "ForgotPasswordRequest",
    "ResetPasswordRequest",
    # Modèles de réponse
    "LoginResponse",
    "RegisterResponse",
    "RefreshTokenResponse",
    "UserProfileResponse",
    "ApiKeyResponse",
    "ApiKeyListResponse",
    "SessionResponse",
    "SessionListResponse",
    "ActionResponse",
    "AuthErrorResponse",
    # Auth Store
    "AuthStore",
    "InMemoryAuthStore",
    "get_auth_store",
    "set_auth_store",
    # Helpers
    "hash_password",
    "verify_password",
    "validate_password_strength",
    "generate_token",
    # ========================================================================
    # ROUTEUR SETTINGS — settings.py
    # ========================================================================
    # Exceptions
    "SettingsRouterError",
    "ConfigLoadError",
    "ConfigSaveError",
    "ConfigValidationError",
    "SectionNotFoundError",
    # Enums
    "ExportFormat",
    "ChangeAction",
    # Modèles de requête
    "UpdateSettingsRequest",
    "UpdateSectionRequest",
    "ExportRequest",
    "ImportRequest",
    "ResetRequest",
    # Modèles de réponse
    "SettingsResponse",
    "SectionResponse",
    "SectionsListResponse",
    "SettingsUpdateResponse",
    "ExportResponse",
    "ImportResponse",
    "ResetResponse",
    "SchemaResponse",
    "DefaultsResponse",
    "ChangeEntry",
    "HistoryResponse",
    "SettingsErrorResponse",
    # Cache et historique
    "SettingsCache",
    "ChangeHistory",
    "get_settings_cache",
    "get_change_history",
    # ========================================================================
    # ROUTEUR WEBSOCKET — ws.py
    # ========================================================================
    # Exceptions
    "WebSocketError",
    "WebSocketAuthError",
    "WebSocketChannelError",
    "WebSocketMessageError",
    "WebSocketRateLimitError",
    # Enums
    "MessageType",
    "ClientState",
    # Modèles
    "WebSocketMessage",
    "WebSocketConfig",
    "WebSocketClientInfo",
    "WebSocketStats",
    # Classes
    "WebSocketClient",
    "ConnectionManager",
    "WebSocketAuthHandler",
    # Constantes de channels
    "ALL_CHANNELS",
    "CHANNEL_DOWNLOADS_PROGRESS",
    "CHANNEL_DOWNLOADS_COMPLETED",
    "CHANNEL_DOWNLOADS_FAILED",
    "CHANNEL_NOTIFICATIONS_INFO",
    "CHANNEL_NOTIFICATIONS_WARNING",
    "CHANNEL_NOTIFICATIONS_ERROR",
    "CHANNEL_LOGS_STREAM",
    "CHANNEL_SEARCH_RESULTS",
    "CHANNEL_LIBRARY_CHANGES",
    "CHANNEL_SYSTEM_STATUS",
    "CHANNEL_SYSTEM_EVENTS",
    # Codes de fermeture WebSocket
    "WS_CLOSE_NORMAL",
    "WS_CLOSE_GOING_AWAY",
    "WS_CLOSE_PROTOCOL_ERROR",
    "WS_CLOSE_UNSUPPORTED_DATA",
    "WS_CLOSE_NO_STATUS_RECEIVED",
    "WS_CLOSE_ABNORMAL",
    "WS_CLOSE_INVALID_PAYLOAD",
    "WS_CLOSE_POLICY_VIOLATION",
    "WS_CLOSE_MESSAGE_TOO_BIG",
    "WS_CLOSE_MANDATORY_EXTENSION",
    "WS_CLOSE_INTERNAL_ERROR",
    "WS_CLOSE_SERVICE_RESTART",
    "WS_CLOSE_TRY_AGAIN_LATER",
    "WS_CLOSE_AUTH_REQUIRED",
    "WS_CLOSE_AUTH_FAILED",
    "WS_CLOSE_RATE_LIMITED",
    "WS_CLOSE_CHANNEL_NOT_FOUND",
    "WS_CLOSE_PERMISSION_DENIED",
    # Instance globale
    "get_connection_manager",
    "set_connection_manager",
    "get_auth_handler",
    "set_auth_handler",
    # Fonctions helpers
    "init_websocket_router",
    "broadcast_to_channel",
    "send_to_user",
    "send_notification",
    "get_ws_stats",
]
