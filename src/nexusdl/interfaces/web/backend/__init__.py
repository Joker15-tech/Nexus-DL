"""Module public du backend web de NexusDL.

Ce module constitue le point d'entrée unique pour l'interface web backend
de NexusDL basée sur FastAPI. Il agrège et ré-exporte les symboles publics
de tous les sous-modules :

    - `main.py`          : Point d'entrée principal (create_app, run_api)
    - `websocket.py`     : Gestionnaire WebSocket de haut niveau
    - `dependencies.py`  : Dépendances FastAPI réutilisables
    - `middleware/`      : Middlewares (auth, cors, rate_limit, logging)
    - `routers/`         : Routeurs FastAPI (10 routeurs, 98 endpoints)
    - `schemas/`         : Schémas Pydantic (~150 modèles)
    - `static/`          : Assets statiques (style cyberpunk néon)

**Architecture complète** :
    interfaces/web/backend/
        ├── __init__.py           : Ce fichier (agrégation publique)
        ├── main.py               : Application FastAPI principale
        ├── websocket.py          : WebSocketManager haut niveau
        ├── dependencies.py       : Dépendances FastAPI
        │
        ├── middleware/           : Middlewares de sécurité
        │   ├── auth.py           : Authentification (JWT, API keys)
        │   ├── cors.py           : Gestion CORS
        │   ├── rate_limit.py     : Rate limiting
        │   ├── logging.py        : Logging structuré
        │   └── __init__.py       : Exports middlewares
        │
        ├── routers/              : Routeurs FastAPI (98 endpoints)
        │   ├── health.py         : Santé API (7 endpoints)
        │   ├── auth.py           : Authentification (16 endpoints)
        │   ├── sites.py          : Sites (8 endpoints)
        │   ├── search.py         : Recherche (6 endpoints)
        │   ├── manga.py          : Mangas (10 endpoints)
        │   ├── chapters.py       : Chapitres (8 endpoints)
        │   ├── library.py        : Bibliothèque (18 endpoints)
        │   ├── download.py       : Téléchargements (13 endpoints)
        │   ├── settings.py       : Configuration (11 endpoints)
        │   ├── ws.py             : WebSocket bas-niveau (1 endpoint)
        │   └── __init__.py       : Exports routeurs
        │
        ├── schemas/              : Schémas Pydantic
        │   ├── common.py         : Types, constantes, domaines
        │   ├── requests.py       : Requêtes (34 modèles)
        │   ├── responses.py      : Réponses (helpers inclus)
        │   └── __init__.py       : Exports schémas
        │
        └── static/               : Assets statiques
            ├── favicon.svg       : Favicon cyberpunk
            ├── logo.svg          : Logo complet
            ├── health.html       : Page de santé
            ├── css/api-docs.css  : Style Swagger custom
            ├── js/api-docs.js    : Extensions Swagger
            └── ...               : Autres assets

**Résolution des conflits de noms** :
    Plusieurs modules définissent des symboles avec des noms identiques.
    Pour éviter les collisions, nous exposons une seule version canonique :

    - UserIdentity         : depuis dependencies.py (modèle simplifié)
    - WebSocketMessage     : depuis websocket.py (haut niveau)
    - APIErrorResponse     : depuis schemas/responses.py (canonique)
    - get_event_bus()      : depuis dependencies.py (version dépendance)
    - get_config()         : depuis dependencies.py (version dépendance)

**Exemple d'utilisation — Démarrage rapide** :
    >>> from nexusdl.interfaces.web.backend import create_app, run_api
    >>>
    >>> # Option 1 : Créer l'app et lancer avec uvicorn
    >>> app = create_app()
    >>> # uvicorn nexusdl.interfaces.web.backend.main:app --reload
    >>>
    >>> # Option 2 : Démarrage direct
    >>> run_api(host="0.0.0.0", port=8000, reload=True)

**Exemple d'utilisation — Personnalisation** :
    >>> from nexusdl.interfaces.web.backend import (
    ...     create_app,
    ...     CorsConfig, CorsMode,
    ...     RateLimitConfig,
    ...     LoggingConfig, LogFormat,
    ...     AuthConfig,
    ... )
    >>>
    >>> app = create_app(
    ...     cors_config=CorsConfig(
    ...         mode=CorsMode.STRICT,
    ...         allowed_origins=["https://nexusdl.dev"],
    ...     ),
    ...     rate_limit_config=RateLimitConfig(default_limit=100),
    ...     logging_config=LoggingConfig(format=LogFormat.JSON),
    ...     auth_config=AuthConfig(jwt_secret="your-secret"),
    ... )

**Exemple d'utilisation — Utilisation des dépendances** :
    >>> from fastapi import APIRouter, Depends
    >>> from nexusdl.interfaces.web.backend import (
    ...     get_current_user, require_role,
    ...     get_pagination, get_site_registry,
    ...     UserIdentity, PaginationParams,
    ... )
    >>>
    >>> router = APIRouter()
    >>>
    >>> @router.get("/mangas")
    >>> async def list_mangas(
    ...     user: UserIdentity = Depends(get_current_user),
    ...     pagination: PaginationParams = Depends(get_pagination),
    ...     registry = Depends(get_site_registry),
    ... ):
    ...     mangas = await registry.search("test")
    ...     return pagination.slice(mangas)

Intégration :
    - fastapi                  : Framework web
    - uvicorn                  : Serveur ASGI
    - pydantic                 : Validation des données
    - core/*                   : Composants du core
    - interfaces/web/backend/* : Tous les sous-modules backend
"""

from __future__ import annotations

# ============================================================================
# APPLICATION PRINCIPALE — main.py
# ============================================================================

# Fonctions principales
from nexusdl.interfaces.web.backend.main import (
    create_app,
    run_api,
    is_fastapi_available,
    get_fastapi_installation_instructions,
    get_api_info,
)

# Instance globale (pour uvicorn)
from nexusdl.interfaces.web.backend.main import app

# Constantes
from nexusdl.interfaces.web.backend.main import (
    API_VERSION,
    API_PREFIX,
    DOCS_URL,
    REDOC_URL,
    OPENAPI_URL,
    DEFAULT_HOST,
    DEFAULT_PORT,
    OPENAPI_TAGS,
    DEFAULT_SECURITY_HEADERS,
)

# Exceptions
from nexusdl.interfaces.web.backend.main import (
    BackendAppError,
    FastAPINotAvailableError,
    ComponentInitializationError,
)

# ============================================================================
# WEBSOCKET — websocket.py
# ============================================================================

# Classe principale
from nexusdl.interfaces.web.backend.websocket import (
    WebSocketManager,
    EventBusBridge,
)

# Modèles
from nexusdl.interfaces.web.backend.websocket import (
    WebSocketMessage,
    BroadcastStats,
    ChannelInfo,
    ConnectionInfo,
)

# Enums
from nexusdl.interfaces.web.backend.websocket import (
    NotificationLevel,
    MessagePriority,
    ConnectionState,
)

# Constantes de canaux
from nexusdl.interfaces.web.backend.websocket import (
    CHANNEL_DOWNLOADS_PROGRESS,
    CHANNEL_DOWNLOADS_COMPLETED,
    CHANNEL_DOWNLOADS_FAILED,
    CHANNEL_DOWNLOADS_STARTED,
    CHANNEL_DOWNLOADS_PAUSED,
    CHANNEL_LIBRARY_ADDED,
    CHANNEL_LIBRARY_REMOVED,
    CHANNEL_LIBRARY_UPDATED,
    CHANNEL_LIBRARY_SCAN,
    CHANNEL_NOTIFICATIONS_INFO,
    CHANNEL_NOTIFICATIONS_WARNING,
    CHANNEL_NOTIFICATIONS_ERROR,
    CHANNEL_NOTIFICATIONS_SUCCESS,
    CHANNEL_LOGS_STREAM,
    CHANNEL_SEARCH_PROGRESS,
    CHANNEL_SEARCH_COMPLETED,
    CHANNEL_SYSTEM_STATUS,
    CHANNEL_SYSTEM_EVENTS,
    CHANNEL_SYSTEM_METRICS,
    CHANNEL_AUTH_LOGIN,
    CHANNEL_AUTH_LOGOUT,
    ALL_CHANNELS,
)

# Exceptions
from nexusdl.interfaces.web.backend.websocket import (
    WebSocketManagerError,
    ChannelNotFoundError,
    BroadcastError,
    ManagerNotStartedError,
)

# Instance globale
from nexusdl.interfaces.web.backend.websocket import (
    get_websocket_manager,
    set_websocket_manager,
    reset_websocket_manager,
)

# Helpers de haut niveau
from nexusdl.interfaces.web.backend.websocket import (
    broadcast_download_progress,
    broadcast_download_completed,
    broadcast_download_failed,
    broadcast_library_update,
    send_notification,
    broadcast_log_entry,
    broadcast_search_progress,
    broadcast_system_status,
    broadcast_system_metrics,
    start_websocket_manager,
    stop_websocket_manager,
    integrate_with_connection_manager,
)

# ============================================================================
# DÉPENDANCES — dependencies.py
# ============================================================================

# Modèles
from nexusdl.interfaces.web.backend.dependencies import (
    PaginationParams,
    SortingParams,
    RequestMetadata,
    RateLimitInfo,
    UserIdentity,
)

# Dépendances — Sécurité
from nexusdl.interfaces.web.backend.dependencies import (
    get_current_user,
    get_optional_user,
    require_role,
    require_permission,
    require_any_role,
    require_all_permissions,
)

# Dépendances — Pagination & Tri
from nexusdl.interfaces.web.backend.dependencies import (
    get_pagination,
    get_sorting,
)

# Dépendances — Services
from nexusdl.interfaces.web.backend.dependencies import (
    get_site_registry,
    get_download_manager,
    get_library_manager,
    get_event_bus,
    get_config,
)

# Dépendances — Métadonnées
from nexusdl.interfaces.web.backend.dependencies import (
    get_request_metadata,
    get_rate_limit_info,
    get_client_info,
)

# Dépendances — Validation
from nexusdl.interfaces.web.backend.dependencies import (
    validate_path_id,
    validate_query_string,
    validate_language_param,
    validate_datetime_param,
)

# Helpers
from nexusdl.interfaces.web.backend.dependencies import (
    create_pagination_dependency,
    create_sorting_dependency,
    create_id_validator,
    ServiceCache,
    get_service_cache,
    cache_service,
)

# Exceptions
from nexusdl.interfaces.web.backend.dependencies import (
    DependencyError,
    ServiceUnavailableError,
    InvalidParameterError,
)

# Constantes
from nexusdl.interfaces.web.backend.dependencies import (
    DEFAULT_PAGE,
    DEFAULT_PAGE_SIZE,
    MIN_PAGE_SIZE,
    MAX_PAGE_SIZE,
    DEFAULT_SORT_BY,
    DEFAULT_SORT_ORDER,
    SORT_ORDERS,
)

# ============================================================================
# MIDDLEWARES — middleware/__init__.py
# ============================================================================

# Middlewares principaux
from nexusdl.interfaces.web.backend.middleware import (
    AuthMiddleware,
    CorsMiddleware,
    RateLimitMiddleware,
    LoggingMiddleware,
)

# Configurations
from nexusdl.interfaces.web.backend.middleware import (
    AuthConfig,
    CorsConfig,
    RateLimitConfig,
    LoggingConfig,
)

# Enums
from nexusdl.interfaces.web.backend.middleware import (
    AuthMethod,
    TokenType,
    AuthDecision,
    CorsMode,
    OriginMatchType,
    CorsDecision,
    RateLimitStrategy,
    RateLimitScope,
    LogFormat,
    LogLevelByStatus,
)

# Fonctions de setup
from nexusdl.interfaces.web.backend.middleware import (
    setup_middlewares,
    setup_dev_middlewares,
    setup_prod_middlewares,
)

# Helpers auth
from nexusdl.interfaces.web.backend.middleware import (
    get_current_user as get_current_user_middleware,
    require_role as require_role_middleware,
    require_permission as require_permission_middleware,
    create_auth_config,
    generate_api_key,
    hash_password,
)

# Helpers cors
from nexusdl.interfaces.web.backend.middleware import (
    validate_cors_origin,
    normalize_cors_origin,
    create_cors_config,
    create_dev_cors_config,
    create_prod_cors_config,
)

# Helpers rate limit
from nexusdl.interfaces.web.backend.middleware import (
    rate_limit,
    create_rate_limiter,
    parse_rate_limit_string,
)

# Helpers logging
from nexusdl.interfaces.web.backend.middleware import (
    generate_request_id,
    mask_sensitive_data,
    extract_client_ip,
    get_log_level_for_status,
    create_request_logger,
    log_request_sync,
)

# ============================================================================
# ROUTEURS — routers/__init__.py
# ============================================================================

# Routeurs principaux
from nexusdl.interfaces.web.backend.routers import (
    health_router,
    auth_router,
    sites_router,
    search_router,
    manga_router,
    library_router,
    download_router,
    chapters_router,
    settings_router,
    ws_router,
)

# Fonctions de setup
from nexusdl.interfaces.web.backend.routers import (
    setup_all_routers,
    setup_dev_routers,
    setup_prod_routers,
)

# ============================================================================
# SCHÉMAS — schemas/__init__.py
# ============================================================================

# Types personnalisés
from nexusdl.interfaces.web.backend.schemas import (
    PositiveInt,
    NonNegativeInt,
    NonEmptyString,
    EmailStr,
    UrlStr,
    DateTimeStr,
    UUIDStr,
    LanguageCode,
    ResourceId,
)

# Modèles de domaine
from nexusdl.interfaces.web.backend.schemas import (
    MangaBase,
    ChapterBase,
    SiteBase,
    UserBase,
    DownloadTaskBase,
    ReadingProgressBase,
)

# Réponses canoniques
from nexusdl.interfaces.web.backend.schemas import (
    APIResponse,
    APIErrorResponse,
    APIPaginatedResponse,
)

# Wrappers génériques
from nexusdl.interfaces.web.backend.schemas import (
    SingleResponse,
    ListResponse,
    ActionResponse,
    BulkActionResponse,
)

# Helpers de réponse
from nexusdl.interfaces.web.backend.schemas import (
    success_response,
    error_response,
    paginated_response,
    list_response,
    action_response,
    validation_error_response,
    http_status_from_error_code,
)

# Enums
from nexusdl.interfaces.web.backend.schemas import (
    ResponseStatus,
    ErrorCode,
    DownloadFormat,
    ImageQuality,
    Priority,
    ReadingStatus,
    MangaStatus,
    SortOrder,
    ExportFormat,
)

# Requêtes principales
from nexusdl.interfaces.web.backend.schemas import (
    LoginRequest,
    RegisterRequest,
    SearchRequest,
    CreateDownloadRequest,
    UpdateMangaRequest,
    UpdateSettingsRequest,
)

# ============================================================================
# STATIC — static/__init__.py
# ============================================================================

# Fonctions de montage
from nexusdl.interfaces.web.backend.static import (
    mount_static_files,
    add_cache_headers_middleware,
    get_static_dir,
    get_static_url,
    get_static_url_for,
)

# Constantes
from nexusdl.interfaces.web.backend.static import (
    STATIC_DIR,
    STATIC_URL,
)

# ============================================================================
# MÉTADONNÉES DU MODULE
# ============================================================================

__version__: str = "0.1.0"
__author__: str = "NexusDL Team"
__license__: str = "MIT"


# ============================================================================
# FONCTIONS HELPERS DE HAUT NIVEAU
# ============================================================================


def get_backend_info() -> dict[str, Any]:
    """Retourne les informations complètes du backend.

    Returns:
        Dictionnaire d'informations.

    Example:
        >>> info = get_backend_info()
        >>> print(info["api_version"])
        '0.1.0'
    """
    api_info = get_api_info()
    return {
        **api_info,
        "backend_version": __version__,
        "fastapi_available": is_fastapi_available(),
        "total_endpoints": 98,
        "total_routers": 10,
        "total_middlewares": 4,
        "websocket_channels": len(ALL_CHANNELS),
    }


def is_backend_available() -> bool:
    """Vérifie si le backend est disponible (FastAPI installé).

    Returns:
        True si FastAPI est installé.
    """
    return is_fastapi_available()


def get_backend_installation_instructions() -> str:
    """Retourne les instructions d'installation du backend.

    Returns:
        Instructions d'installation.
    """
    return """
Pour utiliser l'interface web backend de NexusDL, vous devez installer :

    pip install fastapi uvicorn

Pour une expérience complète, installez également :

    pip install fastapi uvicorn[standard] python-multipart pyjwt bcrypt httpx

Après installation, lancez l'API avec :

    nexusdl-api

Ou depuis Python :

    from nexusdl.interfaces.web.backend import run_api
    run_api(host="0.0.0.0", port=8000)
""".strip()


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
    # APPLICATION PRINCIPALE — main.py
    # ========================================================================
    # Fonctions principales
    "create_app",
    "run_api",
    "is_fastapi_available",
    "get_fastapi_installation_instructions",
    "get_api_info",
    # Instance globale
    "app",
    # Constantes
    "API_VERSION",
    "API_PREFIX",
    "DOCS_URL",
    "REDOC_URL",
    "OPENAPI_URL",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "OPENAPI_TAGS",
    "DEFAULT_SECURITY_HEADERS",
    # Exceptions
    "BackendAppError",
    "FastAPINotAvailableError",
    "ComponentInitializationError",
    # ========================================================================
    # WEBSOCKET — websocket.py
    # ========================================================================
    # Classes
    "WebSocketManager",
    "EventBusBridge",
    # Modèles
    "WebSocketMessage",
    "BroadcastStats",
    "ChannelInfo",
    "ConnectionInfo",
    # Enums
    "NotificationLevel",
    "MessagePriority",
    "ConnectionState",
    # Constantes de canaux
    "CHANNEL_DOWNLOADS_PROGRESS",
    "CHANNEL_DOWNLOADS_COMPLETED",
    "CHANNEL_DOWNLOADS_FAILED",
    "CHANNEL_DOWNLOADS_STARTED",
    "CHANNEL_DOWNLOADS_PAUSED",
    "CHANNEL_LIBRARY_ADDED",
    "CHANNEL_LIBRARY_REMOVED",
    "CHANNEL_LIBRARY_UPDATED",
    "CHANNEL_LIBRARY_SCAN",
    "CHANNEL_NOTIFICATIONS_INFO",
    "CHANNEL_NOTIFICATIONS_WARNING",
    "CHANNEL_NOTIFICATIONS_ERROR",
    "CHANNEL_NOTIFICATIONS_SUCCESS",
    "CHANNEL_LOGS_STREAM",
    "CHANNEL_SEARCH_PROGRESS",
    "CHANNEL_SEARCH_COMPLETED",
    "CHANNEL_SYSTEM_STATUS",
    "CHANNEL_SYSTEM_EVENTS",
    "CHANNEL_SYSTEM_METRICS",
    "CHANNEL_AUTH_LOGIN",
    "CHANNEL_AUTH_LOGOUT",
    "ALL_CHANNELS",
    # Exceptions
    "WebSocketManagerError",
    "ChannelNotFoundError",
    "BroadcastError",
    "ManagerNotStartedError",
    # Instance globale
    "get_websocket_manager",
    "set_websocket_manager",
    "reset_websocket_manager",
    # Helpers
    "broadcast_download_progress",
    "broadcast_download_completed",
    "broadcast_download_failed",
    "broadcast_library_update",
    "send_notification",
    "broadcast_log_entry",
    "broadcast_search_progress",
    "broadcast_system_status",
    "broadcast_system_metrics",
    "start_websocket_manager",
    "stop_websocket_manager",
    "integrate_with_connection_manager",
    # ========================================================================
    # DÉPENDANCES — dependencies.py
    # ========================================================================
    # Modèles
    "PaginationParams",
    "SortingParams",
    "RequestMetadata",
    "RateLimitInfo",
    "UserIdentity",
    # Dépendances — Sécurité
    "get_current_user",
    "get_optional_user",
    "require_role",
    "require_permission",
    "require_any_role",
    "require_all_permissions",
    # Dépendances — Pagination & Tri
    "get_pagination",
    "get_sorting",
    # Dépendances — Services
    "get_site_registry",
    "get_download_manager",
    "get_library_manager",
    "get_event_bus",
    "get_config",
    # Dépendances — Métadonnées
    "get_request_metadata",
    "get_rate_limit_info",
    "get_client_info",
    # Dépendances — Validation
    "validate_path_id",
    "validate_query_string",
    "validate_language_param",
    "validate_datetime_param",
    # Helpers
    "create_pagination_dependency",
    "create_sorting_dependency",
    "create_id_validator",
    "ServiceCache",
    "get_service_cache",
    "cache_service",
    # Exceptions
    "DependencyError",
    "ServiceUnavailableError",
    "InvalidParameterError",
    # Constantes
    "DEFAULT_PAGE",
    "DEFAULT_PAGE_SIZE",
    "MIN_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "DEFAULT_SORT_BY",
    "DEFAULT_SORT_ORDER",
    "SORT_ORDERS",
    # ========================================================================
    # MIDDLEWARES — middleware/__init__.py
    # ========================================================================
    # Middlewares principaux
    "AuthMiddleware",
    "CorsMiddleware",
    "RateLimitMiddleware",
    "LoggingMiddleware",
    # Configurations
    "AuthConfig",
    "CorsConfig",
    "RateLimitConfig",
    "LoggingConfig",
    # Enums
    "AuthMethod",
    "TokenType",
    "AuthDecision",
    "CorsMode",
    "OriginMatchType",
    "CorsDecision",
    "RateLimitStrategy",
    "RateLimitScope",
    "LogFormat",
    "LogLevelByStatus",
    # Fonctions de setup
    "setup_middlewares",
    "setup_dev_middlewares",
    "setup_prod_middlewares",
    # Helpers auth
    "get_current_user_middleware",
    "require_role_middleware",
    "require_permission_middleware",
    "create_auth_config",
    "generate_api_key",
    "hash_password",
    # Helpers cors
    "validate_cors_origin",
    "normalize_cors_origin",
    "create_cors_config",
    "create_dev_cors_config",
    "create_prod_cors_config",
    # Helpers rate limit
    "rate_limit",
    "create_rate_limiter",
    "parse_rate_limit_string",
    # Helpers logging
    "generate_request_id",
    "mask_sensitive_data",
    "extract_client_ip",
    "get_log_level_for_status",
    "create_request_logger",
    "log_request_sync",
    # ========================================================================
    # ROUTEURS — routers/__init__.py
    # ========================================================================
    # Routeurs principaux
    "health_router",
    "auth_router",
    "sites_router",
    "search_router",
    "manga_router",
    "library_router",
    "download_router",
    "chapters_router",
    "settings_router",
    "ws_router",
    # Fonctions de setup
    "setup_all_routers",
    "setup_dev_routers",
    "setup_prod_routers",
    # ========================================================================
    # SCHÉMAS — schemas/__init__.py
    # ========================================================================
    # Types personnalisés
    "PositiveInt",
    "NonNegativeInt",
    "NonEmptyString",
    "EmailStr",
    "UrlStr",
    "DateTimeStr",
    "UUIDStr",
    "LanguageCode",
    "ResourceId",
    # Modèles de domaine
    "MangaBase",
    "ChapterBase",
    "SiteBase",
    "UserBase",
    "DownloadTaskBase",
    "ReadingProgressBase",
    # Réponses canoniques
    "APIResponse",
    "APIErrorResponse",
    "APIPaginatedResponse",
    # Wrappers génériques
    "SingleResponse",
    "ListResponse",
    "ActionResponse",
    "BulkActionResponse",
    # Helpers de réponse
    "success_response",
    "error_response",
    "paginated_response",
    "list_response",
    "action_response",
    "validation_error_response",
    "http_status_from_error_code",
    # Enums
    "ResponseStatus",
    "ErrorCode",
    "DownloadFormat",
    "ImageQuality",
    "Priority",
    "ReadingStatus",
    "MangaStatus",
    "SortOrder",
    "ExportFormat",
    # Requêtes principales
    "LoginRequest",
    "RegisterRequest",
    "SearchRequest",
    "CreateDownloadRequest",
    "UpdateMangaRequest",
    "UpdateSettingsRequest",
    # ========================================================================
    # STATIC — static/__init__.py
    # ========================================================================
    # Fonctions
    "mount_static_files",
    "add_cache_headers_middleware",
    "get_static_dir",
    "get_static_url",
    "get_static_url_for",
    # Constantes
    "STATIC_DIR",
    "STATIC_URL",
    # ========================================================================
    # FONCTIONS HELPERS DE HAUT NIVEAU
    # ========================================================================
    "get_backend_info",
    "is_backend_available",
    "get_backend_installation_instructions",
]
