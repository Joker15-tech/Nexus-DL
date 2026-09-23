"""Module public du core NexusDL.

Ce module constitue le point d'entrée principal de la couche centrale de
l'architecture hexagonale. Il expose l'API publique stable utilisée par
tous les autres modules du projet (parsers, downloader, interfaces, etc.).

Organisation du module core/ :
    FONDATIONS :
        - paths        : Gestionnaire centralisé des chemins (XDG-compliant)
        - config       : Configuration globale (Pydantic Settings v2)
        - logger       : Système de logging (Loguru, rotation, filtres)
        - i18n         : Internationalisation (traductions JSON)
        - exceptions   : Hiérarchie d'exceptions globale (NexusDLError)
        - events       : EventBus (Observer pattern, pub/sub)
        - constants    : Constantes globales (~300 constantes)

    SOUS-MODULES :
        - models       : Modèles de domaine Pydantic (Manga, Chapter, etc.)
        - packaging    : Empaquetage (CBZ, CBR, PDF, ZIP, FOLDER)
        - registry     : Registre des sites supportés
        - session      : Sessions HTTP, cookies, proxies, rate limiting
        - utils        : Utilitaires (URL, temps, texte, hash, filesystem, async)

Architecture :
    Instance globale paths (Paths)
        │
        ├── config (NexusDLConfig)
        │   ├── app, network, logging, download
        │   ├── library, cloudflare, i18n, storage
        │   └── events, interface
        │
        ├── logger (LoggerManager)
        │   └── 4 handlers (console, file, error, json)
        │
        ├── i18n (I18nManager)
        │   └── traductions depuis data/translations/*.json
        │
        ├── event_bus (EventBus)
        │   └── pub/sub avec wildcards, priorités
        │
        └── Sous-modules
            ├── models.*      : Modèles de domaine
            ├── packaging.*   : Empaquetage
            ├── registry.*    : Registre des sites
            ├── session.*     : Sessions HTTP
            └── utils.*       : Utilitaires

Règles d'or :
    1. Ce fichier n'expose QUE les symboles publics stables.
    2. Les instances globales (paths, config, event_bus) sont accessibles directement.
    3. Les fonctions de setup (setup_logging, setup_i18n) initialisent les systèmes.
    4. Les sous-modules sont importables via `from nexusdl.core import models`.
    5. Les conflits de noms sont résolus par des aliases explicites.
    6. NexusDLError est la classe de base de TOUTES les exceptions du projet.
    7. Les constantes sont immuables (typing.Final).
    8. La configuration est validée via Pydantic v2.
    9. L'EventBus est le SEUL moyen de communication inter-modules.
    10. Les chemins sont TOUJOURS absolus et résolus via paths.py.

Exemple d'utilisation — Initialisation complète :
    >>> from nexusdl.core import (
    ...     paths, config, setup_logging, setup_i18n, event_bus,
    ... )
    >>>
    >>> # 1. Initialiser les chemins (automatique au premier accès)
    >>> print(paths.config_dir)
    /home/user/.config/nexusdl
    >>>
    >>> # 2. Charger la configuration
    >>> config.load()
    >>> print(config.app.language)
    'fr'
    >>>
    >>> # 3. Configurer le logging
    >>> setup_logging(level="INFO", format="rich")
    >>>
    >>> # 4. Configurer l'i18n
    >>> setup_i18n(language="fr")
    >>>
    >>> # 5. Démarrer l'EventBus
    >>> await event_bus.start()

Exemple d'utilisation — Accès aux sous-modules :
    >>> from nexusdl.core import models, packaging, registry, session, utils
    >>>
    >>> # Modèles de domaine
    >>> from nexusdl.core.models import Manga, Chapter, DownloadTask
    >>>
    >>> # Empaquetage
    >>> from nexusdl.core.packaging import CbzPackager, ComicInfo
    >>>
    >>> # Registre des sites
    >>> from nexusdl.core.registry import SiteRegistry, SiteConfig
    >>>
    >>> # Sessions HTTP
    >>> from nexusdl.core.session import HttpSession, RateLimiter
    >>>
    >>> # Utilitaires
    >>> from nexusdl.core.utils import normalize_url, hash_file, retry_async

Exemple d'utilisation — Gestion des erreurs :
    >>> from nexusdl.core import NexusDLError, ErrorCode
    >>>
    >>> try:
    ...     # ... opération ...
    ... except NexusDLError as e:
    ...     print(f"Erreur [{e.code.value}]: {e.message}")
    ...     print(f"Contexte: {e.context}")
    ...     # Sérialiser pour API REST
    ...     error_dict = e.to_dict()

Exemple d'utilisation — Événements :
    >>> from nexusdl.core import event_bus, EventType
    >>>
    >>> # S'abonner à un événement
    >>> @event_bus.on("download.task.completed")
    ... async def on_task_completed(event):
    ...     print(f"Tâche terminée: {event.payload['task_id']}")
    >>>
    >>> # Émettre un événement
    >>> await event_bus.emit(
    ...     EventType.DOWNLOAD_TASK_COMPLETED,
    ...     payload={"task_id": "abc123", "pages": 42},
    ... )
"""

from __future__ import annotations

# ============================================================================
# VERSION
# ============================================================================

__version__: str = "0.1.0"
__author__: str = "NexusDL Team"
__license__: str = "MIT"


# ============================================================================
# FONDATIONS — Instances globales et fonctions de setup
# ============================================================================

# --------------------------------------------------------------------------
# paths.py — Gestionnaire des chemins
# --------------------------------------------------------------------------

from nexusdl.core.paths import (
    # Instance globale
    paths,
    get_paths,
    set_paths,
    reset_paths,
    # Classe principale
    Paths,
    PathsConfig,
    # Enums
    Platform,
    PathType,
    # Helpers
    ensure_app_dirs,
    get_app_info,
    detect_platform,
    expand_path,
    is_path_within,
    safe_join,
    # Exceptions
    PathsError,
    PathCreationError,
    PathAccessError,
    InvalidPathError,
)

# --------------------------------------------------------------------------
# config.py — Configuration globale
# --------------------------------------------------------------------------

from nexusdl.core.config import (
    # Instance globale
    config,
    get_config,
    get_config_manager,
    set_config_manager,
    reset_config_manager,
    reload_config,
    # Classe principale
    ConfigManager,
    NexusDLConfig,
    # Sections de configuration
    AppConfig,
    NetworkConfig,
    ProxyConfig,
    LoggingConfig,
    DownloadConfig,
    LibraryConfig,
    CloudflareConfig,
    I18nConfig,
    StorageConfig,
    EventsConfig,
    InterfaceConfig,
    # Enums
    CloudflareBypassMode,
    ImageQuality,
    # Constantes
    CONFIG_FILENAME,
    ENV_PREFIX,
    CONFIG_SCHEMA_VERSION,
    # Exceptions
    ConfigurationError,
    ConfigNotFoundError,
    ConfigParseError,
)

# --------------------------------------------------------------------------
# logger.py — Système de logging
# --------------------------------------------------------------------------

from nexusdl.core.logger import (
    # Fonctions de setup
    setup_logging,
    get_logger,
    get_logger_manager,
    reset_logging,
    set_log_level,
    set_module_level,
    # Classe principale
    LoggerManager,
    LoggerConfig,
    # Enums
    LogLevel,
    LogFormat,
    # Modèles
    LoggingStats,
    # Helpers
    log_exception,
    log_timing,
    is_debug_enabled,
    is_trace_enabled,
    # Sinks
    EventBusSink,
    # Décorateurs
    log_call,
    # Exceptions
    LoggerError,
    LoggerNotInitializedError,
    InvalidLogLevelError,
    LogDirectoryError,
)

# --------------------------------------------------------------------------
# i18n.py — Internationalisation
# --------------------------------------------------------------------------

from nexusdl.core.i18n import (
    # Instance globale et fonctions
    t,
    translate,
    _,  # Alias de t()
    setup_i18n,
    get_i18n_manager,
    get_i18n_stats,
    set_language,
    get_current_language,
    list_available_languages,
    reset_i18n,
    has_translation,
    # Classe principale
    I18nManager,
    I18nConfig,
    # Enums
    SupportedLanguage,
    # Modèles
    I18nStats,
    # Helpers
    detect_system_language,
    normalize_language_code,
    format_number as i18n_format_number,  # Alias pour éviter conflit
    format_date,
    # Exceptions
    I18nError,
    TranslationNotFoundError,
    TranslationFileError,
    LanguageNotSupportedError,
    I18nNotInitializedError,
    InvalidTranslationKeyError,
)

# --------------------------------------------------------------------------
# exceptions.py — Hiérarchie d'exceptions
# --------------------------------------------------------------------------

from nexusdl.core.exceptions import (
    # Classe de base
    NexusDLError,
    # Enum des codes d'erreur
    ErrorCode,
    # Exceptions — Configuration
    ConfigurationError as CoreConfigurationError,  # Alias pour éviter conflit
    ConfigNotFoundError as CoreConfigNotFoundError,  # Alias
    ConfigParseError as CoreConfigParseError,  # Alias
    # Exceptions — Validation
    ValidationError,
    InvalidInputError,
    MissingFieldError,
    DuplicateError,
    # Exceptions — Réseau
    NetworkError,
    ConnectionError as CoreConnectionError,  # Alias pour éviter conflit
    NetworkTimeoutError,
    ConnectionRefusedError as CoreConnectionRefusedError,  # Alias
    DnsError,
    SSLError,
    HttpError,
    RateLimitError,
    ProxyError,
    CloudflareBlockedError,
    # Exceptions — Stockage
    StorageError,
    FileNotFoundError as CoreFileNotFoundError,  # Alias
    FileExistsError as CoreFileExistsError,  # Alias
    FileCorruptedError,
    DiskFullError,
    DatabaseError,
    DatabaseLockedError,
    # Exceptions — Authentification
    AuthenticationError,
    InvalidCredentialsError,
    TokenExpiredError,
    TokenInvalidError,
    SessionExpiredError,
    ForbiddenError,
    AccountDisabledError,
    # Exceptions — Ressources
    NotFoundError,
    MangaNotFoundError,
    ChapterNotFoundError,
    SiteNotFoundError,
    UserNotFoundError,
    TaskNotFoundError,
    # Exceptions — Permissions
    PermissionDeniedError,
    AdminRequiredError,
    # Exceptions — Opérations
    OperationCancelledError,
    OperationTimeoutError,
    OperationFailedError,
    DownloadFailedError,
    ParsingFailedError,
    PackagingFailedError,
    ConversionFailedError,
    # Exceptions — Internes
    InternalError,
    NotImplementedError as CoreNotImplementedError,  # Alias
    DependencyError,
    MigrationError,
    # Helpers
    is_nexusdl_error,
    get_error_code,
    format_error,
    error_to_dict,
    wrap_exception,
    translate_error,
    log_exception as core_log_exception,  # Alias pour éviter conflit
    # Décorateurs
    catch_and_log,
)

# --------------------------------------------------------------------------
# events.py — EventBus
# --------------------------------------------------------------------------

from nexusdl.core.events import (
    # Instance globale
    event_bus,
    get_event_bus,
    set_event_bus,
    reset_event_bus,
    # Classe principale
    EventBus,
    EventBusConfig,
    # Modèles
    Event,
    Subscription,
    EventBusStats,
    # Enums
    EventType,
    EventPriority,
    EventState,
    # Helpers
    emit_event,
    subscribe,
    subscribe_once,
    unsubscribe,
    wait_for,
    # Décorateurs
    event_handler,
    # Exceptions
    EventError,
    EventBusNotStartedError,
    EventBusAlreadyStartedError,
    InvalidEventTypeError,
    HandlerError,
    HandlerTimeoutError,
    SubscriptionNotFoundError,
    EventQueueFullError,
)

# --------------------------------------------------------------------------
# constants.py — Constantes globales
# --------------------------------------------------------------------------

from nexusdl.core.constants import (
    # Identification
    APP_NAME,
    APP_SHORT_NAME,
    APP_NAME_LOWER,
    APP_VERSION,
    APP_AUTHOR,
    APP_URL,
    APP_REPOSITORY,
    APP_DOCUMENTATION,
    APP_USER_AGENT,
    # Versions
    PYTHON_MIN_VERSION,
    DEPENDENCY_VERSIONS,
    # Limites
    MAX_FILE_SIZE_BYTES,
    MAX_IMAGE_SIZE_BYTES,
    MAX_CONCURRENT_DOWNLOAD_TASKS,
    MAX_CONCURRENT_CHAPTERS,
    MAX_CONCURRENT_PAGES,
    DEFAULT_MAX_RETRIES,
    DEFAULT_OPERATION_TIMEOUT_SECONDS,
    # Formats
    SUPPORTED_IMAGE_EXTENSIONS,
    SUPPORTED_PACKAGING_FORMATS,
    DEFAULT_PACKAGING_FORMAT,
    IMAGE_MIME_TYPES,
    # Valeurs par défaut
    DEFAULT_LANGUAGE,
    DEFAULT_THEME,
    DEFAULT_LOG_LEVEL,
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_READING_DIRECTION,
    # Performance
    FILE_BUFFER_SIZE,
    MAX_IO_WORKERS,
    SQLITE_CACHE_SIZE_KB,
    # Sécurité
    MIN_PASSWORD_LENGTH,
    BCRYPT_ROUNDS,
    SECURE_FILE_PERMISSIONS,
    # Temps
    SECONDS_PER_MINUTE,
    SECONDS_PER_HOUR,
    SECONDS_PER_DAY,
    # Réseau
    HTTP_OK,
    HTTP_NOT_FOUND,
    HTTP_TOO_MANY_REQUESTS,
    HTTP_RETRYABLE_CODES,
    # Stockage
    KILOBYTE,
    MEGABYTE,
    GIGABYTE,
)


# ============================================================================
# SOUS-MODULES — Importation pour accès direct
# ============================================================================

# Les sous-modules sont importés pour permettre :
#   from nexusdl.core import models
#   from nexusdl.core.models import Manga

from nexusdl.core import models
from nexusdl.core import packaging
from nexusdl.core import registry
from nexusdl.core import session
from nexusdl.core import utils


# ============================================================================
# FONCTIONS DE SETUP — Initialisation complète
# ============================================================================


def setup_core(
    *,
    language: str | None = None,
    log_level: str = "INFO",
    log_format: str = "text",
    config_path: Path | None = None,
) -> None:
    """Initialise tous les systèmes du core en une seule fois.

    Fonction de haut niveau pour initialiser rapidement l'application :
        1. Charge la configuration (config.yaml + env vars)
        2. Initialise les chemins (XDG-compliant)
        3. Configure le logging (Loguru)
        4. Configure l'i18n (traductions)
        5. Démarre l'EventBus

    Args:
        language: Langue de l'interface (défaut: auto-détection).
        log_level: Niveau de log (TRACE, DEBUG, INFO, WARNING, ERROR).
        log_format: Format de log (text, rich, json, simple).
        config_path: Chemin vers config.yaml (défaut: ~/.config/nexusdl/config.yaml).

    Example:
        >>> from nexusdl.core import setup_core
        >>> setup_core(language="fr", log_level="INFO")
    """
    from pathlib import Path

    # 1. Initialiser les chemins
    if config_path is not None:
        from nexusdl.core.config import ConfigManager
        manager = ConfigManager(config_path=config_path)
        set_config_manager(manager)

    # 2. Charger la configuration
    config.load()

    # 3. Configurer le logging
    setup_logging(
        level=log_level,
        format=log_format,
        log_dir=paths.logs_dir,
    )

    # 4. Configurer l'i18n
    setup_i18n(
        language=language or config.i18n.language,
        translations_dir=paths.config_dir.parent.parent / "data" / "translations",
    )

    # 5. Démarrer l'EventBus (synchrone pour setup)
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        # Déjà dans un event loop, planifier le démarrage
        asyncio.create_task(event_bus.start())
    except RuntimeError:
        # Pas d'event loop, démarrage synchrone impossible
        # L'EventBus sera démarré plus tard
        pass


def get_core_info() -> dict[str, Any]:
    """Retourne des informations sur le core NexusDL.

    Returns:
        Dictionnaire avec version, plateforme, chemins, etc.

    Example:
        >>> info = get_core_info()
        >>> print(info["version"])
        '0.1.0'
    """
    return {
        "version": __version__,
        "author": __author__,
        "license": __license__,
        "app_name": APP_NAME,
        "platform": paths.platform.value,
        "config_dir": str(paths.config_dir),
        "data_dir": str(paths.data_dir),
        "cache_dir": str(paths.cache_dir),
        "logs_dir": str(paths.logs_dir),
        "config_loaded": config.load_count > 0,
        "logger_initialized": get_logger_manager() is not None,
        "i18n_initialized": get_i18n_manager() is not None,
        "event_bus_started": event_bus.is_started,
    }


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
    "setup_core",
    "get_core_info",
    # ========================================================================
    # FONDATIONS — Instances globales
    # ========================================================================
    "paths",
    "config",
    "event_bus",
    # ========================================================================
    # FONDATIONS — paths.py
    # ========================================================================
    "Paths",
    "PathsConfig",
    "Platform",
    "PathType",
    "get_paths",
    "set_paths",
    "reset_paths",
    "ensure_app_dirs",
    "get_app_info",
    "detect_platform",
    "expand_path",
    "is_path_within",
    "safe_join",
    "PathsError",
    "PathCreationError",
    "PathAccessError",
    "InvalidPathError",
    # ========================================================================
    # FONDATIONS — config.py
    # ========================================================================
    "ConfigManager",
    "NexusDLConfig",
    "AppConfig",
    "NetworkConfig",
    "ProxyConfig",
    "LoggingConfig",
    "DownloadConfig",
    "LibraryConfig",
    "CloudflareConfig",
    "I18nConfig",
    "StorageConfig",
    "EventsConfig",
    "InterfaceConfig",
    "CloudflareBypassMode",
    "ImageQuality",
    "CONFIG_FILENAME",
    "ENV_PREFIX",
    "CONFIG_SCHEMA_VERSION",
    "get_config",
    "get_config_manager",
    "set_config_manager",
    "reset_config_manager",
    "reload_config",
    "ConfigurationError",
    "ConfigNotFoundError",
    "ConfigParseError",
    # ========================================================================
    # FONDATIONS — logger.py
    # ========================================================================
    "LoggerManager",
    "LoggerConfig",
    "LogLevel",
    "LogFormat",
    "LoggingStats",
    "setup_logging",
    "get_logger",
    "get_logger_manager",
    "reset_logging",
    "set_log_level",
    "set_module_level",
    "log_exception",
    "log_timing",
    "is_debug_enabled",
    "is_trace_enabled",
    "EventBusSink",
    "log_call",
    "LoggerError",
    "LoggerNotInitializedError",
    "InvalidLogLevelError",
    "LogDirectoryError",
    # ========================================================================
    # FONDATIONS — i18n.py
    # ========================================================================
    "I18nManager",
    "I18nConfig",
    "SupportedLanguage",
    "I18nStats",
    "t",
    "translate",
    "_",
    "setup_i18n",
    "get_i18n_manager",
    "get_i18n_stats",
    "set_language",
    "get_current_language",
    "list_available_languages",
    "reset_i18n",
    "has_translation",
    "detect_system_language",
    "normalize_language_code",
    "i18n_format_number",
    "format_date",
    "I18nError",
    "TranslationNotFoundError",
    "TranslationFileError",
    "LanguageNotSupportedError",
    "I18nNotInitializedError",
    "InvalidTranslationKeyError",
    # ========================================================================
    # FONDATIONS — exceptions.py
    # ========================================================================
    "NexusDLError",
    "ErrorCode",
    # Exceptions — Configuration
    "CoreConfigurationError",
    "CoreConfigNotFoundError",
    "CoreConfigParseError",
    # Exceptions — Validation
    "ValidationError",
    "InvalidInputError",
    "MissingFieldError",
    "DuplicateError",
    # Exceptions — Réseau
    "NetworkError",
    "CoreConnectionError",
    "NetworkTimeoutError",
    "CoreConnectionRefusedError",
    "DnsError",
    "SSLError",
    "HttpError",
    "RateLimitError",
    "ProxyError",
    "CloudflareBlockedError",
    # Exceptions — Stockage
    "StorageError",
    "CoreFileNotFoundError",
    "CoreFileExistsError",
    "FileCorruptedError",
    "DiskFullError",
    "DatabaseError",
    "DatabaseLockedError",
    # Exceptions — Authentification
    "AuthenticationError",
    "InvalidCredentialsError",
    "TokenExpiredError",
    "TokenInvalidError",
    "SessionExpiredError",
    "ForbiddenError",
    "AccountDisabledError",
    # Exceptions — Ressources
    "NotFoundError",
    "MangaNotFoundError",
    "ChapterNotFoundError",
    "SiteNotFoundError",
    "UserNotFoundError",
    "TaskNotFoundError",
    # Exceptions — Permissions
    "PermissionDeniedError",
    "AdminRequiredError",
    # Exceptions — Opérations
    "OperationCancelledError",
    "OperationTimeoutError",
    "OperationFailedError",
    "DownloadFailedError",
    "ParsingFailedError",
    "PackagingFailedError",
    "ConversionFailedError",
    # Exceptions — Internes
    "InternalError",
    "CoreNotImplementedError",
    "DependencyError",
    "MigrationError",
    # Helpers
    "is_nexusdl_error",
    "get_error_code",
    "format_error",
    "error_to_dict",
    "wrap_exception",
    "translate_error",
    "core_log_exception",
    "catch_and_log",
    # ========================================================================
    # FONDATIONS — events.py
    # ========================================================================
    "EventBus",
    "EventBusConfig",
    "Event",
    "Subscription",
    "EventBusStats",
    "EventType",
    "EventPriority",
    "EventState",
    "get_event_bus",
    "set_event_bus",
    "reset_event_bus",
    "emit_event",
    "subscribe",
    "subscribe_once",
    "unsubscribe",
    "wait_for",
    "event_handler",
    "EventError",
    "EventBusNotStartedError",
    "EventBusAlreadyStartedError",
    "InvalidEventTypeError",
    "HandlerError",
    "HandlerTimeoutError",
    "SubscriptionNotFoundError",
    "EventQueueFullError",
    # ========================================================================
    # FONDATIONS — constants.py (sélection)
    # ========================================================================
    "APP_NAME",
    "APP_SHORT_NAME",
    "APP_NAME_LOWER",
    "APP_VERSION",
    "APP_AUTHOR",
    "APP_URL",
    "APP_REPOSITORY",
    "APP_DOCUMENTATION",
    "APP_USER_AGENT",
    "PYTHON_MIN_VERSION",
    "DEPENDENCY_VERSIONS",
    "MAX_FILE_SIZE_BYTES",
    "MAX_IMAGE_SIZE_BYTES",
    "MAX_CONCURRENT_DOWNLOAD_TASKS",
    "MAX_CONCURRENT_CHAPTERS",
    "MAX_CONCURRENT_PAGES",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_OPERATION_TIMEOUT_SECONDS",
    "SUPPORTED_IMAGE_EXTENSIONS",
    "SUPPORTED_PACKAGING_FORMATS",
    "DEFAULT_PACKAGING_FORMAT",
    "IMAGE_MIME_TYPES",
    "DEFAULT_LANGUAGE",
    "DEFAULT_THEME",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_HTTP_TIMEOUT_SECONDS",
    "DEFAULT_READING_DIRECTION",
    "FILE_BUFFER_SIZE",
    "MAX_IO_WORKERS",
    "SQLITE_CACHE_SIZE_KB",
    "MIN_PASSWORD_LENGTH",
    "BCRYPT_ROUNDS",
    "SECURE_FILE_PERMISSIONS",
    "SECONDS_PER_MINUTE",
    "SECONDS_PER_HOUR",
    "SECONDS_PER_DAY",
    "HTTP_OK",
    "HTTP_NOT_FOUND",
    "HTTP_TOO_MANY_REQUESTS",
    "HTTP_RETRYABLE_CODES",
    "KILOBYTE",
    "MEGABYTE",
    "GIGABYTE",
    # ========================================================================
    # SOUS-MODULES — Importation directe
    # ========================================================================
    "models",
    "packaging",
    "registry",
    "session",
    "utils",
]
