"""Point d'entrée principal de l'API REST NexusDL.

Ce module fournit le point d'entrée de l'API REST basée sur FastAPI.
Il orchestre tous les composants : middlewares, routeurs, WebSocket,
fichiers statiques, et gère le cycle de vie complet de l'application.

**Responsabilités** :
    - Création et configuration de l'application FastAPI
    - Montage des middlewares (auth, cors, rate_limit, logging)
    - Montage des routeurs (10 routeurs, 98 endpoints)
    - Montage des fichiers statiques (style cyberpunk néon)
    - Configuration du WebSocketManager
    - Initialisation des composants du core (config, logger, i18n, events, registry)
    - Gestion du cycle de vie (startup/shutdown)
    - Configuration de la documentation OpenAPI
    - Fonctions de démarrage (create_app, run_api)

**Architecture** :
    main.py (point d'entrée)
        │
        ├── create_app()
        │   ├── Initialisation des composants core
        │   ├── Configuration des middlewares
        │   ├── Montage des routeurs
        │   ├── Montage des fichiers statiques
        │   ├── Configuration WebSocket
        │   └── Configuration documentation
        │
        ├── Lifecycle
        │   ├── on_startup()   : Démarrage des services
        │   └── on_shutdown()  : Arrêt propre des services
        │
        └── run_api()          : Fonction de démarrage direct

Intégration :
    - interfaces/web/backend/middleware/* : Middlewares FastAPI
    - interfaces/web/backend/routers/*    : Routeurs FastAPI
    - interfaces/web/backend/schemas/*    : Schémas Pydantic
    - interfaces/web/backend/static/      : Assets statiques
    - interfaces/web/backend/websocket.py : WebSocketManager
    - core/config.py                      : Configuration
    - core/logger.py                      : Logging
    - core/i18n.py                        : Internationalisation
    - core/events.py                      : EventBus
    - core/paths.py                       : Chemins
    - core/registry/                      : Registre des sites
"""

from __future__ import annotations

import asyncio
import os  # ⚠️ AJOUTÉ : Nécessaire pour lire les variables d'environnement (PORT, HOST, CORS)
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Final

from loguru import logger

try:
    from fastapi import FastAPI, Request, Response
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.middleware.gzip import GZipMiddleware
    from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
    from fastapi.middleware.trustedhost import TrustedHostMiddleware
    from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from nexusdl.core.constants import (
    APP_AUTHOR,
    APP_DESCRIPTION,
    APP_NAME,
    APP_URL,
    APP_VERSION,
    PYTHON_MIN_VERSION,
)
from nexusdl.core.events import EventBus, EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import setup_i18n
from nexusdl.core.logger import setup_logging
from nexusdl.core.paths import get_paths, paths


# ============================================================================
# CONSTANTES
# ============================================================================

# Version de l'API
API_VERSION: Final[str] = "0.1.0"

# Préfixe API
API_PREFIX: Final[str] = "/api/v1"

# Documentation
DOCS_URL: Final[str] = "/docs"
REDOC_URL: Final[str] = "/redoc"
OPENAPI_URL: Final[str] = "/openapi.json"

# ⚠️ CORRECTION CRITIQUE POUR RAILWAY : Lire les variables d'environnement
# Railway injecte automatiquement $PORT et $HOST. Ne pas hardcoder "127.0.0.1" ou "8000".
DEFAULT_HOST: Final[str] = os.getenv("HOST", "0.0.0.0")
DEFAULT_PORT: Final[int] = int(os.getenv("PORT", "8000"))

# Tags OpenAPI
OPENAPI_TAGS: Final[list[dict[str, str]]] = [
    {"name": "health", "description": "Health check endpoints for monitoring"},
    {"name": "auth", "description": "Authentication and user management"},
    {"name": "sites", "description": "Manga site management and search"},
    {"name": "search", "description": "Multi-site manga search"},
    {"name": "manga", "description": "Individual manga management"},
    {"name": "chapters", "description": "Chapter and page management with streaming"},
    {"name": "library", "description": "Local library management"},
    {"name": "download", "description": "Download task management"},
    {"name": "settings", "description": "Application configuration"},
    {"name": "websocket", "description": "WebSocket real-time communications"},
]

# Headers de sécurité par défaut
DEFAULT_SECURITY_HEADERS: Final[dict[str, str]] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


# ============================================================================
# EXCEPTIONS
# ============================================================================

class BackendAppError(NexusDLError):
    """Exception de base pour les erreurs de l'application backend."""

class FastAPINotAvailableError(BackendAppError):
    """Exception levée lorsque FastAPI n'est pas installé."""
    def __init__(self) -> None:
        super().__init__(
            "FastAPI n'est pas installé. "
            "Installez-le avec: pip install fastapi uvicorn"
        )

class ComponentInitializationError(BackendAppError):
    """Exception levée lorsqu'un composant ne peut être initialisé."""
    def __init__(self, component: str, reason: str = "") -> None:
        msg = f"Échec de l'initialisation du composant: {component}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.component = component
        self.reason = reason


# ============================================================================
# CONFIGURATION PAR DÉFAUT — Middlewares
# ============================================================================

def _get_default_cors_config() -> Any:
    """Retourne la configuration CORS par défaut."""
    from nexusdl.interfaces.web.backend.middleware.cors import CorsConfig, CorsMode
    return CorsConfig(
        mode=CorsMode.PERMISSIVE,
        allow_credentials=True,
        allow_localhost_dev=True,
    )

def _get_default_rate_limit_config() -> Any:
    """Retourne la configuration rate limiting par défaut."""
    from nexusdl.interfaces.web.backend.middleware.rate_limit import RateLimitConfig, RateLimitStrategy
    return RateLimitConfig(
        default_limit=100,
        default_period=60,
        strategy=RateLimitStrategy.SLIDING_WINDOW,
    )

def _get_default_logging_config() -> Any:
    """Retourne la configuration logging par défaut."""
    from nexusdl.interfaces.web.backend.middleware.logging import LogFormat, LoggingConfig
    return LoggingConfig(
        format=LogFormat.JSON,
        log_request_body=False,
        log_response_body=False,
        mask_sensitive_data=True,
    )

def _get_default_auth_config() -> Any:
    """Retourne la configuration auth par défaut."""
    from nexusdl.interfaces.web.backend.middleware.auth import AuthConfig, AuthMethod
    import secrets
    return AuthConfig(
        jwt_secret=secrets.token_urlsafe(32),
        allowed_methods=[AuthMethod.JWT_BEARER, AuthMethod.API_KEY],
        require_authentication=False,  # Désactivé par défaut pour faciliter le dev
    )


# ============================================================================
# HELPERS — Configuration de l'application
# ============================================================================

def _check_python_version() -> None:
    """Vérifie que la version de Python est compatible."""
    if sys.version_info < PYTHON_MIN_VERSION:
        print(
            f"Error: Python {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}+ is required.\n"
            f"You are using Python {sys.version_info.major}.{sys.version_info.minor}.",
            file=sys.stderr,
        )
        sys.exit(1)

def _check_fastapi_available() -> None:
    """Vérifie que FastAPI est disponible."""
    if not FASTAPI_AVAILABLE:
        raise FastAPINotAvailableError()

def _initialize_core_components(
    *,
    config_path: Path | None = None,
    log_level: str = "INFO",
    language: str | None = None,
) -> None:
    """Initialise les composants du core."""
    # 1. Initialiser les chemins
    paths_instance = get_paths()
    if not paths_instance.is_initialized:
        paths_instance.initialize()
    logger.debug("Chemins initialisés")

    # 2. Charger la configuration
    try:
        from nexusdl.core.config import ConfigManager, set_config_manager
        manager = ConfigManager(config_path=config_path)
        set_config_manager(manager)
        logger.debug("Configuration chargée")
    except Exception as e:
        logger.warning("Impossible de charger la configuration: {}", e)

    # 3. Configurer le logging
    try:
        setup_logging(
            level=log_level,
            format="text",  # "text" est plus lisible dans les logs Railway que "json"
            log_dir=paths_instance.logs_dir,
            colorize=False,
        )
        logger.debug("Logging initialisé: level={}", log_level)
    except Exception as e:
        logger.warning("Impossible de configurer le logging: {}", e)

    # 4. Configurer l'i18n
    try:
        effective_language = language or "en"
        try:
            from nexusdl.core.config import get_config
            effective_language = get_config().i18n.language
        except Exception:
            pass

        translations_dir = None
        try:
            import nexusdl
            package_dir = Path(nexusdl.__file__).parent
            default_dir = package_dir.parent / "data" / "translations"
            if default_dir.exists():
                translations_dir = default_dir
        except Exception:
            pass

        setup_i18n(language=effective_language, translations_dir=translations_dir)
        logger.debug("I18n initialisé: language={}", effective_language)
    except Exception as e:
        logger.warning("Impossible de configurer l'i18n: {}", e)

    # 5. Démarrer l'EventBus
    try:
        from nexusdl.core.config import get_config
        from nexusdl.core.events import EventBus, EventBusConfig, set_event_bus

        config = get_config()
        bus_config = EventBusConfig(
            queue_size=config.events.queue_size,
            worker_count=config.events.worker_count,
            handler_timeout=config.events.handler_timeout,
            dead_letter_enabled=config.events.dead_letter_enabled,
        )

        event_bus = EventBus(config=bus_config)
        set_event_bus(event_bus)
        logger.debug("EventBus créé")
    except Exception as e:
        logger.warning("Impossible de créer l'EventBus: {}", e)

    # 6. Initialiser le registre des sites
    try:
        from nexusdl.core.registry import (
            ConfigLoader,
            SchemaValidator,
            SiteRegistry,
            set_site_registry,
        )

        loader = ConfigLoader()
        validator = SchemaValidator()
        registry = SiteRegistry(loader=loader, validator=validator)

        # Initialisation synchrone minimale
        asyncio.run(loader.start())
        asyncio.run(validator.start())
        asyncio.run(registry.start())

        set_site_registry(registry)
        logger.debug("Registre initialisé: {} sites", registry.sites_count)
    except Exception as e:
        logger.warning("Impossible d'initialiser le registre: {}", e)


# ============================================================================
# APPLICATION FACTORY — Création de l'application FastAPI
# ============================================================================

if FASTAPI_AVAILABLE:

    def _configure_middlewares(
        app: FastAPI,
        *,
        cors_config: Any | None = None,
        rate_limit_config: Any | None = None,
        logging_config: Any | None = None,
        auth_config: Any | None = None,
        enable_cors: bool = True,
        enable_rate_limit: bool = True,
        enable_logging: bool = True,
        enable_auth: bool = True,
        enable_gzip: bool = True,
        enable_https_redirect: bool = False,
        trusted_hosts: list[str] | None = None,
    ) -> dict[str, Any]:
        """Configure tous les middlewares de l'application."""
        from nexusdl.interfaces.web.backend.middleware import (
            AuthMiddleware,
            CorsMiddleware,
            LoggingMiddleware,
            RateLimitMiddleware,
            setup_middlewares,
        )

        middlewares: dict[str, Any] = {}

        # ⚠️ CORRECTION CRITIQUE : Middleware CORS standard FastAPI (Infaillible)
        # Lit la variable d'environnement configurée sur Railway
        if enable_cors:
            cors_origins_str = os.getenv(
                "NEXUSDL_CORS_ORIGINS", 
                "https://nexusdldev.netlify.app,http://localhost:3000"
            )
            cors_origins = [origin.strip() for origin in cors_origins_str.split(",") if origin.strip()]
            
            app.add_middleware(
                CORSMiddleware,
                allow_origins=cors_origins,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )
            middlewares["cors"] = cors_origins
            logger.info("✅ CORS configuré pour les origines: {}", cors_origins)

        # 1. Trusted hosts (le plus externe si activé)
        if trusted_hosts:
            try:
                app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)
                middlewares["trusted_hosts"] = True
                logger.debug("TrustedHostMiddleware configuré")
            except Exception as e:
                logger.warning("Impossible de configurer TrustedHostMiddleware: {}", e)

        # 2. HTTPS redirect (si activé)
        if enable_https_redirect:
            try:
                app.add_middleware(HTTPSRedirectMiddleware)
                middlewares["https_redirect"] = True
                logger.debug("HTTPSRedirectMiddleware configuré")
            except Exception as e:
                logger.warning("Impossible de configurer HTTPSRedirectMiddleware: {}", e)

        # 3. GZip compression
        if enable_gzip:
            try:
                app.add_middleware(GZipMiddleware, minimum_size=1000)
                middlewares["gzip"] = True
                logger.debug("GZipMiddleware configuré")
            except Exception as e:
                logger.warning("Impossible de configurer GZipMiddleware: {}", e)

        # 4. Middlewares NexusDL (ordre inverse)
        try:
            configured = setup_middlewares(
                app,
                cors_config=cors_config or _get_default_cors_config(),
                rate_limit_config=rate_limit_config or _get_default_rate_limit_config(),
                logging_config=logging_config or _get_default_logging_config(),
                auth_config=auth_config or _get_default_auth_config(),
                enable_cors=enable_cors,
                enable_rate_limit=enable_rate_limit,
                enable_logging=enable_logging,
                enable_auth=enable_auth,
            )
            middlewares.update(configured)
        except Exception as e:
            logger.warning("Impossible de configurer les middlewares NexusDL: {}", e)

        logger.info("Middlewares configurés: {} actifs", len(middlewares))
        return middlewares

    def _configure_routers(
        app: FastAPI,
        *,
        prefix: str = API_PREFIX,
        include_all: bool = True,
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
    ) -> dict[str, Any]:
        """Configure tous les routeurs de l'application."""
        from nexusdl.interfaces.web.backend.routers import (
            auth_router,
            chapters_router,
            download_router,
            health_router,
            library_router,
            manga_router,
            search_router,
            settings_router,
            sites_router,
            ws_router,
        )

        included: dict[str, Any] = {}

        if include_all or include_health:
            app.include_router(health_router, prefix=prefix)
            included["health"] = health_router

        if include_all or include_auth:
            app.include_router(auth_router, prefix=prefix)
            included["auth"] = auth_router

        if include_all or include_sites:
            app.include_router(sites_router, prefix=prefix)
            included["sites"] = sites_router

        if include_all or include_search:
            app.include_router(search_router, prefix=prefix)
            included["search"] = search_router

        if include_all or include_manga:
            app.include_router(manga_router, prefix=prefix)
            included["manga"] = manga_router

        if include_all or include_chapters:
            app.include_router(chapters_router, prefix=prefix)
            included["chapters"] = chapters_router

        if include_all or include_library:
            app.include_router(library_router, prefix=prefix)
            included["library"] = library_router

        if include_all or include_download:
            app.include_router(download_router, prefix=prefix)
            included["download"] = download_router

        if include_all or include_settings:
            app.include_router(settings_router, prefix=prefix)
            included["settings"] = settings_router

        if include_all or include_ws:
            # WebSocket a son propre préfixe
            ws_prefix = "/ws"
            app.include_router(ws_router, prefix=ws_prefix)
            included["ws"] = ws_router

        logger.info("Routeurs configurés: {} sur 10 (prefix={})", len(included), prefix)
        return included

    def _configure_static_files(app: FastAPI) -> None:
        """Configure les fichiers statiques."""
        try:
            from nexusdl.interfaces.web.backend.static import (
                STATIC_DIR,
                add_cache_headers_middleware,
                mount_static_files,
            )

            if STATIC_DIR.exists():
                mount_static_files(app)
                add_cache_headers_middleware(app)
                logger.debug("Fichiers statiques montés depuis {}", STATIC_DIR)
            else:
                logger.warning("Dossier static introuvable: {}", STATIC_DIR)

        except Exception as e:
            logger.warning("Impossible de monter les fichiers statiques: {}", e)

    def _configure_websocket(app: FastAPI) -> None:
        """Configure le WebSocketManager."""
        try:
            from nexusdl.interfaces.web.backend.routers.ws import (
                get_connection_manager,
                init_websocket_router,
            )
            from nexusdl.interfaces.web.backend.websocket import (
                get_websocket_manager,
                integrate_with_connection_manager,
            )

            # Initialiser le routeur WebSocket (crée le ConnectionManager)
            init_websocket_router()

            # Intégrer avec le WebSocketManager de haut niveau
            connection_manager = get_connection_manager()
            if connection_manager:
                integrate_with_connection_manager(connection_manager)
                logger.debug("WebSocketManager intégré avec ConnectionManager")

        except Exception as e:
            logger.warning("Impossible de configurer le WebSocket: {}", e)

    def _configure_documentation(app: FastAPI) -> None:
        """Configure la documentation OpenAPI (Swagger/ReDoc)."""
        @app.get(DOCS_URL, include_in_schema=False)
        async def custom_swagger_ui_html() -> HTMLResponse:
            """Page Swagger UI personnalisée."""
            return get_swagger_ui_html(
                openapi_url=OPENAPI_URL,
                title=f"{APP_NAME} API - Swagger UI",
                swagger_css_url="/static/css/api-docs.css",
                swagger_js_url="/static/js/api-docs.js",
                swagger_favicon_url="/static/favicon.svg",
            )

        @app.get(REDOC_URL, include_in_schema=False)
        async def custom_redoc_html() -> HTMLResponse:
            """Page ReDoc personnalisée."""
            return get_redoc_html(
                openapi_url=OPENAPI_URL,
                title=f"{APP_NAME} API - ReDoc",
                redoc_js_url="https://cdn.jsdelivr.net/npm/redoc@latest/bundles/redoc.standalone.js",
                redoc_favicon_url="/static/favicon.svg",
            )

        logger.debug("Documentation configurée: {} et {}", DOCS_URL, REDOC_URL)

    def _add_security_headers_middleware(app: FastAPI) -> None:
        """Ajoute un middleware pour les headers de sécurité."""
        @app.middleware("http")
        async def add_security_headers(request: Request, call_next: Any) -> Response:
            response = await call_next(request)
            for header, value in DEFAULT_SECURITY_HEADERS.items():
                response.headers[header] = value
            return response

    def _add_root_endpoint(app: FastAPI) -> None:
        """Ajoute un endpoint racine."""
        @app.get("/", include_in_schema=False)
        async def root() -> dict[str, Any]:
            """Endpoint racine avec informations de l'API."""
            return {
                "name": APP_NAME,
                "version": APP_VERSION,
                "api_version": API_VERSION,
                "description": APP_DESCRIPTION,
                "author": APP_AUTHOR,
                "docs_url": DOCS_URL,
                "redoc_url": REDOC_URL,
                "openapi_url": OPENAPI_URL,
                "health_url": f"{API_PREFIX}/health",
            }

    def create_app(
        *,
        config_path: Path | None = None,
        log_level: str = "INFO",
        language: str | None = None,
        title: str | None = None,
        description: str | None = None,
        version: str | None = None,
        cors_config: Any | None = None,
        rate_limit_config: Any | None = None,
        logging_config: Any | None = None,
        auth_config: Any | None = None,
        enable_cors: bool = True,
        enable_rate_limit: bool = True,
        enable_logging: bool = True,
        enable_auth: bool = True,
        enable_gzip: bool = True,
        enable_https_redirect: bool = False,
        enable_websocket: bool = True,
        enable_static_files: bool = True,
        trusted_hosts: list[str] | None = None,
        debug: bool = False,
    ) -> FastAPI:
        """Crée et configure l'application FastAPI complète."""
        _check_fastapi_available()

        # Initialiser les composants du core
        _initialize_core_components(
            config_path=config_path,
            log_level=log_level,
            language=language,
        )

        # Créer l'application FastAPI avec lifespan
        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            """Gestionnaire de cycle de vie."""
            await _on_startup(app)
            try:
                yield
            finally:
                await _on_shutdown(app)

        app = FastAPI(
            title=title or APP_NAME,
            description=description or APP_DESCRIPTION,
            version=version or APP_VERSION,
            openapi_url=OPENAPI_URL,
            docs_url=DOCS_URL,
            redoc_url=REDOC_URL,
            openapi_tags=OPENAPI_TAGS,
            lifespan=lifespan,
            debug=debug,
        )

        # Stocker la configuration dans l'app
        app.state.nexusdl_config = {
            "config_path": config_path,
            "log_level": log_level,
            "language": language,
            "debug": debug,
            "started_at": datetime.now(UTC),
        }

        # Configurer les middlewares
        _configure_middlewares(
            app,
            cors_config=cors_config,
            rate_limit_config=rate_limit_config,
            logging_config=logging_config,
            auth_config=auth_config,
            enable_cors=enable_cors,
            enable_rate_limit=enable_rate_limit,
            enable_logging=enable_logging,
            enable_auth=enable_auth,
            enable_gzip=enable_gzip,
            enable_https_redirect=enable_https_redirect,
            trusted_hosts=trusted_hosts,
        )

        # Headers de sécurité
        _add_security_headers_middleware(app)

        # Configurer les routeurs
        _configure_routers(
            app,
            include_ws=enable_websocket,
        )

        # Configurer les fichiers statiques
        if enable_static_files:
            _configure_static_files(app)

        # Configurer WebSocket
        if enable_websocket:
            _configure_websocket(app)

        # Configurer la documentation
        _configure_documentation(app)

        # Ajouter l'endpoint racine
        _add_root_endpoint(app)

        # Ajouter des handlers d'erreurs globaux
        _add_exception_handlers(app)

        logger.info(
            "Application FastAPI créée: {} v{} (debug={})",
            APP_NAME,
            APP_VERSION,
            debug,
        )

        return app

    def _add_exception_handlers(app: FastAPI) -> None:
        """Ajoute des handlers d'erreurs globaux."""
        @app.exception_handler(NexusDLError)
        async def nexusdl_error_handler(request: Request, exc: NexusDLError) -> JSONResponse:
            """Handler pour les erreurs NexusDL."""
            logger.warning("Erreur NexusDL: {} - {}", type(exc).__name__, exc)
            return JSONResponse(
                status_code=400,
                content={
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
            )

        @app.exception_handler(Exception)
        async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
            """Handler pour les erreurs non gérées."""
            logger.exception("Erreur non gérée: {}", exc)
            return JSONResponse(
                status_code=500,
                content={
                    "error": "internal_error",
                    "message": "An unexpected error occurred",
                    "details": str(exc) if app.state.nexusdl_config.get("debug") else None,
                },
            )

    async def _on_startup(app: FastAPI) -> None:
        """Gère le démarrage de l'application."""
        logger.info("Démarrage de {} v{} (API REST)", APP_NAME, APP_VERSION)

        # Démarrer l'EventBus
        try:
            event_bus = get_event_bus()
            if event_bus and not event_bus.is_started:
                await event_bus.start()
                logger.debug("EventBus démarré")
        except Exception as e:
            logger.warning("Impossible de démarrer l'EventBus: {}", e)

        # Démarrer le WebSocketManager
        try:
            from nexusdl.interfaces.web.backend.websocket import get_websocket_manager
            manager = get_websocket_manager()
            if not manager.is_started:
                await manager.start()
                logger.debug("WebSocketManager démarré")
        except Exception as e:
            logger.warning("Impossible de démarrer le WebSocketManager: {}", e)

        # Démarrer le ConnectionManager
        try:
            from nexusdl.interfaces.web.backend.routers.ws import get_connection_manager
            connection_manager = get_connection_manager()
            if connection_manager:
                await connection_manager.start()
                logger.debug("ConnectionManager démarré")
        except Exception as e:
            logger.warning("Impossible de démarrer le ConnectionManager: {}", e)

        # Émettre un événement de démarrage
        try:
            event_bus = get_event_bus()
            await event_bus.emit(
                EventType.SESSION_STARTED,
                payload={
                    "app": APP_NAME,
                    "version": APP_VERSION,
                    "api_version": API_VERSION,
                    "mode": "api",
                },
                source="interfaces.web.backend",
            )
        except Exception as e:
            logger.debug("Impossible d'émettre l'événement de démarrage: {}", e)

        # Notification de démarrage
        try:
            from nexusdl.interfaces.web.backend.websocket import broadcast_system_status
            await broadcast_system_status(
                "running",
                details={
                    "api_version": API_VERSION,
                    "started_at": datetime.now(UTC).isoformat(),
                },
            )
        except Exception as e:
            logger.debug("Impossible de diffuser le statut de démarrage: {}", e)

        logger.info("API REST démarrée avec succès")

    async def _on_shutdown(app: FastAPI) -> None:
        """Gère l'arrêt de l'application."""
        logger.info("Arrêt de l'API REST...")

        # Émettre un événement d'arrêt
        try:
            event_bus = get_event_bus()
            await event_bus.emit(
                EventType.SESSION_STOPPED,
                payload={
                    "app": APP_NAME,
                    "mode": "api",
                },
                source="interfaces.web.backend",
            )
        except Exception as e:
            logger.debug("Impossible d'émettre l'événement d'arrêt: {}", e)

        # Notification d'arrêt
        try:
            from nexusdl.interfaces.web.backend.websocket import broadcast_system_status
            await broadcast_system_status(
                "stopping",
                details={
                    "stopped_at": datetime.now(UTC).isoformat(),
                },
            )
        except Exception as e:
            logger.debug("Impossible de diffuser le statut d'arrêt: {}", e)

        # Arrêter le ConnectionManager
        try:
            from nexusdl.interfaces.web.backend.routers.ws import get_connection_manager
            connection_manager = get_connection_manager()
            if connection_manager:
                await connection_manager.stop()
                logger.debug("ConnectionManager arrêté")
        except Exception as e:
            logger.warning("Erreur lors de l'arrêt du ConnectionManager: {}", e)

        # Arrêter le WebSocketManager
        try:
            from nexusdl.interfaces.web.backend.websocket import get_websocket_manager
            manager = get_websocket_manager()
            if manager.is_started:
                await manager.stop()
                logger.debug("WebSocketManager arrêté")
        except Exception as e:
            logger.warning("Erreur lors de l'arrêt du WebSocketManager: {}", e)

        # Arrêter l'EventBus
        try:
            event_bus = get_event_bus()
            if event_bus and event_bus.is_started:
                await event_bus.stop()
                logger.debug("EventBus arrêté")
        except Exception as e:
            logger.warning("Erreur lors de l'arrêt de l'EventBus: {}", e)

        # Calculer la durée de session
        started_at = app.state.nexusdl_config.get("started_at")
        if started_at:
            duration = datetime.now(UTC) - started_at
            logger.info("API REST arrêtée après {:.1f}s", duration.total_seconds())

        logger.info("API REST arrêtée")


# ============================================================================
# FONCTIONS DE DÉMARRAGE
# ============================================================================

def run_api(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    reload: bool = False,
    workers: int = 1,
    log_level: str = "info",
    config_path: Path | None = None,
    debug: bool = False,
    **kwargs: Any,
) -> None:
    """Lance l'API REST avec uvicorn."""
    _check_python_version()
    _check_fastapi_available()

    try:
        import uvicorn
    except ImportError as e:
        raise BackendAppError(
            "uvicorn n'est pas installé. "
            "Installez-le avec: pip install uvicorn"
        ) from e

    logger.info(
        "Démarrage de {} v{} sur {}:{} (reload={}, workers={})",
        APP_NAME,
        APP_VERSION,
        host,
        port,
        reload,
        workers,
    )

    # Configurer uvicorn
    uvicorn_config = {
        "app": "nexusdl.interfaces.web.backend.main:app",
        "host": host,
        "port": port,
        "reload": reload,
        "workers": workers if not reload else 1,
        "log_level": log_level,
        # ⚠️ CRITIQUE POUR RAILWAY : Accepter les headers du reverse proxy
        "proxy_headers": True,
        "forwarded_allow_ips": "*",
        **kwargs,
    }

    try:
        uvicorn.run(**uvicorn_config)
    except KeyboardInterrupt:
        logger.info("Interruption clavier, arrêt de l'API")
    except Exception as e:
        logger.critical("Erreur fatale: {}", e, exc_info=True)
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)


# ============================================================================
# INSTANCE GLOBALE — Pour uvicorn
# ============================================================================

# Instance globale de l'application (utilisée par uvicorn)
# Peut être importée directement : from nexusdl.interfaces.web.backend.main import app
try:
    app: FastAPI = create_app() if FASTAPI_AVAILABLE else None  # type: ignore[no-redef]
except Exception as e:
    logger.error("Impossible de créer l'application FastAPI par défaut: {}", e)
    if FASTAPI_AVAILABLE:
        # Créer une app minimale en cas d'erreur
        app = FastAPI(title=APP_NAME, version=APP_VERSION)

        @app.get("/")
        async def error_root() -> dict[str, str]:
            return {"error": f"Application initialization failed: {e}"}
    else:
        app = None  # type: ignore[assignment]


# ============================================================================
# FONCTIONS HELPERS PUBLIQUES
# ============================================================================

def is_fastapi_available() -> bool:
    """Vérifie si FastAPI est disponible."""
    return FASTAPI_AVAILABLE

def get_fastapi_installation_instructions() -> str:
    """Retourne les instructions d'installation de FastAPI."""
    return """
Pour utiliser l'API REST de NexusDL, vous devez installer FastAPI et uvicorn :

    pip install fastapi uvicorn

Pour une expérience optimale, installez également les dépendances recommandées :

    pip install fastapi uvicorn[standard] python-multipart pyjwt bcrypt

Après installation, lancez l'API avec :

    nexusdl-api

Ou depuis Python :

    from nexusdl.interfaces.web.backend.main import run_api
    run_api(host="0.0.0.0", port=8000)
""".strip()

def get_api_info() -> dict[str, Any]:
    """Retourne les informations de l'API."""
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "api_version": API_VERSION,
        "description": APP_DESCRIPTION,
        "author": APP_AUTHOR,
        "url": APP_URL,
        "docs_url": DOCS_URL,
        "redoc_url": REDOC_URL,
        "openapi_url": OPENAPI_URL,
        "api_prefix": API_PREFIX,
        "default_host": DEFAULT_HOST,
        "default_port": DEFAULT_PORT,
        "fastapi_available": FASTAPI_AVAILABLE,
    }


# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
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
    # Fonctions principales
    "create_app",
    "run_api",
    # Instance globale
    "app",
    # Fonctions helpers
    "is_fastapi_available",
    "get_fastapi_installation_instructions",
    "get_api_info",
]
