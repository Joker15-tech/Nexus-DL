"""Module public des middlewares de l'API REST NexusDL.

Ce module constitue le point d'entrée unique pour tous les middlewares
FastAPI/Starlette de l'interface web NexusDL. Il agrège et ré-exporte
les symboles publics des 4 middlewares :

    - `rate_limit.py` : Limitation de débit (3 stratégies, 2 backends)
    - `logging.py`    : Logging structuré (3 formats, masquage données)
    - `cors.py`       : Gestion CORS (3 modes, validation d'origines)
    - `auth.py`       : Authentification (4 méthodes, JWT, API keys)

Architecture des middlewares :
    Les middlewares sont appliqués dans un ordre spécifique (du plus externe
    au plus interne). En FastAPI/Starlette, l'ordre d'ajout est inversé :
    le dernier middleware ajouté est le plus externe.

    Ordre d'application recommandé :
        Requête HTTP entrante
            │
            ▼
        ┌─────────────────────────────────────────┐
        │  1. LoggingMiddleware (plus externe)    │  ← Loggue TOUTES les requêtes
        │     - Mesure la latence totale          │
        │     - Log les headers, body, status     │
        └─────────────────────────────────────────┘
            │
            ▼
        ┌─────────────────────────────────────────┐
        │  2. CorsMiddleware                      │  ← Gère les requêtes cross-origin
        │     - Valide l'origine                  │
        │     - Gère les preflight (OPTIONS)      │
        │     - Ajoute les headers CORS           │
        └─────────────────────────────────────────┘
            │
            ▼
        ┌─────────────────────────────────────────┐
        │  3. RateLimitMiddleware                 │  ← Limite le débit AVANT auth
        │     - Vérifie les quotas                │
        │     - Retourne 429 si dépassé           │
        │     - Ajoute les headers X-RateLimit-*  │
        └─────────────────────────────────────────┘
            │
            ▼
        ┌─────────────────────────────────────────┐
        │  4. AuthMiddleware (plus interne)       │  ← Valide les credentials
        │     - Extrait l'identité utilisateur    │
        │     - Injecte dans request.state.user   │
        │     - Retourne 401 si non authentifié   │
        └─────────────────────────────────────────┘
            │
            ▼
        Handler FastAPI (endpoint)

Résolution des conflits de noms :
    Les 4 middlewares exposent des symboles avec des noms similaires.
    Pour éviter les collisions, nous utilisons des préfixes/suffixes
    contextuels :

    Fonctions de validation :
        - validate_origin (cors) → validate_cors_origin
        - normalize_origin (cors) → normalize_cors_origin

    Fonctions de statistiques :
        - get_rate_limit_stats() (rate_limit)
        - get_logging_stats() (logging)
        - get_cors_stats() (cors)
        - get_auth_stats() (auth)

    Fonctions de reset :
        - reset_rate_limiter() (rate_limit)
        - reset_logging_stats() (logging)
        - reset_cors_stats() (cors)
        - reset_auth_stats() (auth)

    Tous les autres symboles ont des noms uniques et sont exposés tels quels.

Exemple d'utilisation — Setup complet :
    >>> from fastapi import FastAPI
    >>> from nexusdl.interfaces.web.backend.middleware import (
    ...     setup_middlewares,
    ...     CorsConfig, CorsMode,
    ...     RateLimitConfig, RateLimitStrategy,
    ...     LoggingConfig, LogFormat,
    ...     AuthConfig, AuthMethod,
    ... )
    >>>
    >>> app = FastAPI()
    >>>
    >>> # Configurer tous les middlewares en une fois
    >>> setup_middlewares(
    ...     app,
    ...     cors_config=CorsConfig(
    ...         mode=CorsMode.STRICT,
    ...         allowed_origins=["https://nexusdl.dev"],
    ...     ),
    ...     rate_limit_config=RateLimitConfig(
    ...         default_limit=100,
    ...         default_period=60,
    ...         strategy=RateLimitStrategy.SLIDING_WINDOW,
    ...     ),
    ...     logging_config=LoggingConfig(
    ...         format=LogFormat.JSON,
    ...     ),
    ...     auth_config=AuthConfig(
    ...         jwt_secret="your-secret-key",
    ...         allowed_methods=[AuthMethod.JWT_BEARER],
    ...     ),
    ... )

Exemple d'utilisation — Configuration manuelle :
    >>> from fastapi import FastAPI
    >>> from nexusdl.interfaces.web.backend.middleware import (
    ...     CorsMiddleware, CorsConfig,
    ...     RateLimitMiddleware, RateLimitConfig,
    ...     LoggingMiddleware, LoggingConfig,
    ...     AuthMiddleware, AuthConfig,
    ... )
    >>>
    >>> app = FastAPI()
    >>>
    >>> # Ajouter les middlewares dans l'ordre inverse (le dernier est le plus externe)
    >>> app.add_middleware(AuthMiddleware, config=AuthConfig())
    >>> app.add_middleware(RateLimitMiddleware, config=RateLimitConfig())
    >>> app.add_middleware(CorsMiddleware, config=CorsConfig())
    >>> app.add_middleware(LoggingMiddleware, config=LoggingConfig())

Exemple d'utilisation — Protection d'endpoints :
    >>> from fastapi import Depends
    >>> from nexusdl.interfaces.web.backend.middleware import (
    ...     get_current_user, require_role, require_permission,
    ...     UserIdentity, rate_limit,
    ... )
    >>>
    >>> @app.get("/api/v1/manga")
    >>> @rate_limit(limit=20, period=60)
    >>> async def get_manga(user: UserIdentity = Depends(get_current_user())):
    ...     return {"manga": []}
    >>>
    >>> @app.delete("/api/v1/manga/{id}")
    >>> async def delete_manga(user: UserIdentity = Depends(require_role("admin"))):
    ...     pass

Intégration :
    - fastapi                 : Framework web
    - starlette               : Middleware ASGI
    - core/config.py          : Configuration globale
    - core/events.py          : EventBus pour monitoring
    - core/logger.py          : Système de logging Loguru
    - core/i18n.py            : Traductions
    - core/exceptions.py      : Exceptions
    - core/utils/hash.py      : Hashing des mots de passe
"""

from __future__ import annotations

from typing import Any

# ============================================================================
# MIDDLEWARE RATE LIMITING — rate_limit.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.web.backend.middleware.rate_limit import (
    RateLimitBackendError,
    RateLimitError,
    RateLimitExceededError,
)

# Enums
from nexusdl.interfaces.web.backend.middleware.rate_limit import (
    RateLimitScope,
    RateLimitStrategy,
)

# Modèles
from nexusdl.interfaces.web.backend.middleware.rate_limit import (
    EndpointRateLimit,
    RateLimitConfig,
    RateLimitEntry,
    RateLimitInfo,
    RateLimitStats,
)

# Backends
from nexusdl.interfaces.web.backend.middleware.rate_limit import (
    MemoryBackend,
    RateLimitBackend,
    RedisBackend,
)

# Stratégies
from nexusdl.interfaces.web.backend.middleware.rate_limit import (
    FixedWindowStrategy,
    RateLimitStrategyBase,
    SlidingWindowStrategy,
    TokenBucketStrategy,
)

# Rate Limiter
from nexusdl.interfaces.web.backend.middleware.rate_limit import (
    RateLimiter,
)

# Middleware
from nexusdl.interfaces.web.backend.middleware.rate_limit import (
    RateLimitMiddleware,
)

# Décorateur
from nexusdl.interfaces.web.backend.middleware.rate_limit import (
    rate_limit,
)

# Instance globale
from nexusdl.interfaces.web.backend.middleware.rate_limit import (
    get_rate_limiter,
    reset_rate_limiter,
    set_rate_limiter,
)

# Fonctions helpers
from nexusdl.interfaces.web.backend.middleware.rate_limit import (
    create_rate_limiter,
    parse_rate_limit_string,
)

# ============================================================================
# MIDDLEWARE LOGGING — logging.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.web.backend.middleware.logging import (
    LogFormatError,
    LoggingMiddlewareError,
)

# Enums
from nexusdl.interfaces.web.backend.middleware.logging import (
    LogFormat,
    LogLevelByStatus,
)

# Modèles
from nexusdl.interfaces.web.backend.middleware.logging import (
    LoggingConfig,
    LoggingStats,
    RequestLog,
)

# Classes
from nexusdl.interfaces.web.backend.middleware.logging import (
    RequestLogger,
)

# Middleware
from nexusdl.interfaces.web.backend.middleware.logging import (
    LoggingMiddleware,
)

# Instance globale
from nexusdl.interfaces.web.backend.middleware.logging import (
    get_request_logger,
    reset_request_logger,
    set_request_logger,
)

# Fonctions helpers
from nexusdl.interfaces.web.backend.middleware.logging import (
    create_request_logger,
    extract_client_ip,
    generate_request_id,
    get_log_level_for_status,
    get_logging_stats,
    log_request_sync,
    mask_sensitive_data,
    reset_logging_stats,
    should_exclude_path,
    truncate_body,
)

# ============================================================================
# MIDDLEWARE CORS — cors.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.web.backend.middleware.cors import (
    CorsConfigError,
    CorsError,
    InvalidOriginError,
    OriginNotAllowedError,
)

# Enums
from nexusdl.interfaces.web.backend.middleware.cors import (
    CorsDecision,
    CorsMode,
    OriginMatchType,
)

# Modèles
from nexusdl.interfaces.web.backend.middleware.cors import (
    CorsConfig,
    CorsRequestLog,
    CorsStats,
    OriginRule,
)

# Classes
from nexusdl.interfaces.web.backend.middleware.cors import (
    CorsValidator,
)

# Middleware
from nexusdl.interfaces.web.backend.middleware.cors import (
    CorsMiddleware,
)

# Instance globale
from nexusdl.interfaces.web.backend.middleware.cors import (
    get_cors_middleware,
    reset_cors_middleware,
    set_cors_middleware,
)

# Fonctions helpers
from nexusdl.interfaces.web.backend.middleware.cors import (
    create_cors_config,
    create_dev_cors_config,
    create_prod_cors_config,
    get_cors_stats,
    normalize_origin as normalize_cors_origin,
    reset_cors_stats,
    validate_origin as validate_cors_origin,
)

# ============================================================================
# MIDDLEWARE AUTH — auth.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.web.backend.middleware.auth import (
    ApiKeyError,
    AuthError,
    AuthenticationError,
    AuthorizationError,
    TokenError,
)

# Enums
from nexusdl.interfaces.web.backend.middleware.auth import (
    AuthDecision,
    AuthMethod,
    TokenType,
)

# Modèles
from nexusdl.interfaces.web.backend.middleware.auth import (
    ApiKeyInfo,
    AuthConfig,
    AuthRequestLog,
    AuthStats,
    TokenPayload,
    UserIdentity,
)

# Classes
from nexusdl.interfaces.web.backend.middleware.auth import (
    ApiKeyManager,
    AuthValidator,
    TokenManager,
)

# Middleware
from nexusdl.interfaces.web.backend.middleware.auth import (
    AuthMiddleware,
)

# Dépendances FastAPI
from nexusdl.interfaces.web.backend.middleware.auth import (
    get_current_user,
    require_permission,
    require_role,
)

# Instance globale
from nexusdl.interfaces.web.backend.middleware.auth import (
    get_auth_middleware,
    reset_auth_middleware,
    set_auth_middleware,
)

# Fonctions helpers
from nexusdl.interfaces.web.backend.middleware.auth import (
    create_auth_config,
    generate_api_key,
    get_auth_stats,
    hash_password,
    reset_auth_stats,
)


# ============================================================================
# CONSTANTES PARTAGÉES — Headers HTTP
# ============================================================================

# Headers de rate limiting
from nexusdl.interfaces.web.backend.middleware.rate_limit import (
    HEADER_RATE_LIMIT,
    HEADER_RATE_REMAINING,
    HEADER_RATE_RESET,
    HEADER_RETRY_AFTER,
)

# Headers de logging/traçabilité
from nexusdl.interfaces.web.backend.middleware.logging import (
    HEADER_CORRELATION_ID,
    HEADER_REQUEST_ID,
    HEADER_RESPONSE_TIME,
)

# Headers CORS
from nexusdl.interfaces.web.backend.middleware.cors import (
    HEADER_ACCESS_CONTROL_ALLOW_CREDENTIALS,
    HEADER_ACCESS_CONTROL_ALLOW_HEADERS,
    HEADER_ACCESS_CONTROL_ALLOW_METHODS,
    HEADER_ACCESS_CONTROL_ALLOW_ORIGIN,
    HEADER_ACCESS_CONTROL_EXPOSE_HEADERS,
    HEADER_ACCESS_CONTROL_MAX_AGE,
    HEADER_ACCESS_CONTROL_REQUEST_HEADERS,
    HEADER_ACCESS_CONTROL_REQUEST_METHOD,
    HEADER_ORIGIN,
    HEADER_VARY,
)

# Headers d'authentification
from nexusdl.interfaces.web.backend.middleware.auth import (
    HEADER_API_KEY,
    HEADER_AUTHORIZATION,
    HEADER_X_USER_ID,
    HEADER_X_USER_ROLES,
)


# ============================================================================
# FONCTION HELPER — Configuration complète des middlewares
# ============================================================================


def setup_middlewares(
    app: Any,
    *,
    cors_config: CorsConfig | None = None,
    rate_limit_config: RateLimitConfig | None = None,
    logging_config: LoggingConfig | None = None,
    auth_config: AuthConfig | None = None,
    enable_cors: bool = True,
    enable_rate_limit: bool = True,
    enable_logging: bool = True,
    enable_auth: bool = True,
) -> dict[str, Any]:
    """Configure tous les middlewares de l'application en une seule fois.

    Les middlewares sont ajoutés dans l'ordre inverse de leur application :
    le dernier ajouté est le plus externe (LoggingMiddleware).

    Ordre d'application des requêtes :
        Logging → CORS → Rate Limit → Auth → Handler

    Args:
        app: Instance FastAPI.
        cors_config: Configuration CORS (None = désactivé).
        rate_limit_config: Configuration rate limiting (None = désactivé).
        logging_config: Configuration logging (None = désactivé).
        auth_config: Configuration auth (None = désactivé).
        enable_cors: Activer le middleware CORS.
        enable_rate_limit: Activer le middleware de rate limiting.
        enable_logging: Activer le middleware de logging.
        enable_auth: Activer le middleware d'authentification.

    Returns:
        Dictionnaire des instances de middlewares créées.

    Example:
        >>> from fastapi import FastAPI
        >>> app = FastAPI()
        >>> middlewares = setup_middlewares(
        ...     app,
        ...     cors_config=CorsConfig(mode=CorsMode.PERMISSIVE),
        ...     rate_limit_config=RateLimitConfig(default_limit=100),
        ...     logging_config=LoggingConfig(format=LogFormat.JSON),
        ...     auth_config=AuthConfig(jwt_secret="secret"),
        ... )
    """
    from loguru import logger

    middlewares: dict[str, Any] = {}

    # Ordre d'ajout (du plus interne au plus externe) :
    # 1. Auth (le plus interne)
    # 2. Rate Limit
    # 3. CORS
    # 4. Logging (le plus externe)

    # 1. Auth Middleware
    if enable_auth and auth_config is not None:
        try:
            auth_middleware = AuthMiddleware(app, config=auth_config)
            app.add_middleware(AuthMiddleware, config=auth_config)
            set_auth_middleware(auth_middleware)
            middlewares["auth"] = auth_middleware
            logger.debug("AuthMiddleware configuré")
        except Exception as e:
            logger.warning("Impossible de configurer AuthMiddleware: {}", e)

    # 2. Rate Limit Middleware
    if enable_rate_limit and rate_limit_config is not None:
        try:
            rate_limiter = create_rate_limiter(rate_limit_config)
            app.add_middleware(RateLimitMiddleware, config=rate_limit_config, limiter=rate_limiter)
            set_rate_limiter(rate_limiter)
            middlewares["rate_limit"] = rate_limiter
            logger.debug("RateLimitMiddleware configuré")
        except Exception as e:
            logger.warning("Impossible de configurer RateLimitMiddleware: {}", e)

    # 3. CORS Middleware
    if enable_cors and cors_config is not None:
        try:
            cors_middleware = CorsMiddleware(app, config=cors_config)
            app.add_middleware(CorsMiddleware, config=cors_config)
            set_cors_middleware(cors_middleware)
            middlewares["cors"] = cors_middleware
            logger.debug("CorsMiddleware configuré")
        except Exception as e:
            logger.warning("Impossible de configurer CorsMiddleware: {}", e)

    # 4. Logging Middleware (le plus externe)
    if enable_logging and logging_config is not None:
        try:
            request_logger = create_request_logger(logging_config)
            app.add_middleware(LoggingMiddleware, config=logging_config, request_logger=request_logger)
            set_request_logger(request_logger)
            middlewares["logging"] = request_logger
            logger.debug("LoggingMiddleware configuré")
        except Exception as e:
            logger.warning("Impossible de configurer LoggingMiddleware: {}", e)

    logger.info(
        "Middlewares configurés: {} sur 4",
        len(middlewares),
    )

    return middlewares


def setup_dev_middlewares(app: Any) -> dict[str, Any]:
    """Configure les middlewares pour le développement.

    Configuration permissive pour faciliter le développement :
        - CORS : mode permissif avec localhost autorisé
        - Rate Limit : limites élevées (1000 req/min)
        - Logging : format texte lisible
        - Auth : désactivé par défaut (require_authentication=False)

    Args:
        app: Instance FastAPI.

    Returns:
        Dictionnaire des instances de middlewares créées.
    """
    return setup_middlewares(
        app,
        cors_config=create_dev_cors_config(),
        rate_limit_config=RateLimitConfig(
            default_limit=1000,
            default_period=60,
        ),
        logging_config=LoggingConfig(
            format=LogFormat.TEXT,
            log_request_body=True,
            log_response_body=True,
        ),
        auth_config=create_auth_config(development=True),
    )


def setup_prod_middlewares(
    app: Any,
    *,
    allowed_origins: list[str],
    jwt_secret: str,
    rate_limit: int = 100,
    rate_period: int = 60,
) -> dict[str, Any]:
    """Configure les middlewares pour la production.

    Configuration stricte et sécurisée :
        - CORS : mode strict avec whitelist d'origines
        - Rate Limit : limites basses (100 req/min par défaut)
        - Logging : format JSON pour intégration ELK
        - Auth : JWT Bearer + API Key

    Args:
        app: Instance FastAPI.
        allowed_origins: Liste des origines autorisées.
        jwt_secret: Secret JWT (min 32 caractères).
        rate_limit: Limite de requêtes par défaut.
        rate_period: Période de la limite en secondes.

    Returns:
        Dictionnaire des instances de middlewares créées.
    """
    return setup_middlewares(
        app,
        cors_config=create_prod_cors_config(allowed_origins),
        rate_limit_config=RateLimitConfig(
            default_limit=rate_limit,
            default_period=rate_period,
            strategy=RateLimitStrategy.SLIDING_WINDOW,
        ),
        logging_config=LoggingConfig(
            format=LogFormat.JSON,
            log_request_body=False,
            log_response_body=False,
            emit_events=True,
        ),
        auth_config=create_auth_config(
            jwt_secret=jwt_secret,
            allowed_methods=[AuthMethod.JWT_BEARER, AuthMethod.API_KEY],
        ),
    )


# ============================================================================
# VERSION ET MÉTADONNÉES
# ============================================================================

__version__: str = "0.1.0"


# ============================================================================
# EXPORTS PUBLICS
# ============================================================================

__all__ = [
    # ========================================================================
    # Métadonnées
    # ========================================================================
    "__version__",
    # ========================================================================
    # Fonctions de setup
    # ========================================================================
    "setup_middlewares",
    "setup_dev_middlewares",
    "setup_prod_middlewares",
    # ========================================================================
    # HEADERS HTTP — Constantes partagées
    # ========================================================================
    # Rate limiting
    "HEADER_RATE_LIMIT",
    "HEADER_RATE_REMAINING",
    "HEADER_RATE_RESET",
    "HEADER_RETRY_AFTER",
    # Logging/traçabilité
    "HEADER_REQUEST_ID",
    "HEADER_CORRELATION_ID",
    "HEADER_RESPONSE_TIME",
    # CORS
    "HEADER_ORIGIN",
    "HEADER_ACCESS_CONTROL_ALLOW_ORIGIN",
    "HEADER_ACCESS_CONTROL_ALLOW_CREDENTIALS",
    "HEADER_ACCESS_CONTROL_ALLOW_METHODS",
    "HEADER_ACCESS_CONTROL_ALLOW_HEADERS",
    "HEADER_ACCESS_CONTROL_EXPOSE_HEADERS",
    "HEADER_ACCESS_CONTROL_MAX_AGE",
    "HEADER_ACCESS_CONTROL_REQUEST_METHOD",
    "HEADER_ACCESS_CONTROL_REQUEST_HEADERS",
    "HEADER_VARY",
    # Auth
    "HEADER_AUTHORIZATION",
    "HEADER_API_KEY",
    "HEADER_X_USER_ID",
    "HEADER_X_USER_ROLES",
    # ========================================================================
    # MIDDLEWARE RATE LIMITING — rate_limit.py
    # ========================================================================
    # Exceptions
    "RateLimitError",
    "RateLimitExceededError",
    "RateLimitBackendError",
    # Enums
    "RateLimitStrategy",
    "RateLimitScope",
    # Modèles
    "EndpointRateLimit",
    "RateLimitConfig",
    "RateLimitInfo",
    "RateLimitStats",
    "RateLimitEntry",
    # Backends
    "RateLimitBackend",
    "MemoryBackend",
    "RedisBackend",
    # Stratégies
    "RateLimitStrategyBase",
    "FixedWindowStrategy",
    "SlidingWindowStrategy",
    "TokenBucketStrategy",
    # Rate Limiter
    "RateLimiter",
    # Middleware
    "RateLimitMiddleware",
    # Décorateur
    "rate_limit",
    # Instance globale
    "get_rate_limiter",
    "set_rate_limiter",
    "reset_rate_limiter",
    # Fonctions helpers
    "create_rate_limiter",
    "parse_rate_limit_string",
    # ========================================================================
    # MIDDLEWARE LOGGING — logging.py
    # ========================================================================
    # Exceptions
    "LoggingMiddlewareError",
    "LogFormatError",
    # Enums
    "LogFormat",
    "LogLevelByStatus",
    # Modèles
    "LoggingConfig",
    "RequestLog",
    "LoggingStats",
    # Classes
    "RequestLogger",
    # Middleware
    "LoggingMiddleware",
    # Instance globale
    "get_request_logger",
    "set_request_logger",
    "reset_request_logger",
    # Fonctions helpers
    "create_request_logger",
    "generate_request_id",
    "mask_sensitive_data",
    "extract_client_ip",
    "get_log_level_for_status",
    "should_exclude_path",
    "truncate_body",
    "get_logging_stats",
    "reset_logging_stats",
    "log_request_sync",
    # ========================================================================
    # MIDDLEWARE CORS — cors.py
    # ========================================================================
    # Exceptions
    "CorsError",
    "OriginNotAllowedError",
    "InvalidOriginError",
    "CorsConfigError",
    # Enums
    "CorsMode",
    "OriginMatchType",
    "CorsDecision",
    # Modèles
    "OriginRule",
    "CorsConfig",
    "CorsStats",
    "CorsRequestLog",
    # Classes
    "CorsValidator",
    # Middleware
    "CorsMiddleware",
    # Instance globale
    "get_cors_middleware",
    "set_cors_middleware",
    "reset_cors_middleware",
    # Fonctions helpers
    "validate_cors_origin",
    "normalize_cors_origin",
    "create_cors_config",
    "create_dev_cors_config",
    "create_prod_cors_config",
    "get_cors_stats",
    "reset_cors_stats",
    # ========================================================================
    # MIDDLEWARE AUTH — auth.py
    # ========================================================================
    # Exceptions
    "AuthError",
    "AuthenticationError",
    "AuthorizationError",
    "TokenError",
    "ApiKeyError",
    # Enums
    "AuthMethod",
    "TokenType",
    "AuthDecision",
    # Modèles
    "UserIdentity",
    "TokenPayload",
    "ApiKeyInfo",
    "AuthConfig",
    "AuthStats",
    "AuthRequestLog",
    # Classes
    "TokenManager",
    "ApiKeyManager",
    "AuthValidator",
    # Middleware
    "AuthMiddleware",
    # Dépendances FastAPI
    "get_current_user",
    "require_role",
    "require_permission",
    # Instance globale
    "get_auth_middleware",
    "set_auth_middleware",
    "reset_auth_middleware",
    # Fonctions helpers
    "create_auth_config",
    "generate_api_key",
    "hash_password",
    "get_auth_stats",
    "reset_auth_stats",
]
