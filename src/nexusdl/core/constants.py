"""Constantes globales du projet NexusDL.

Ce module centralise toutes les constantes utilisées à travers l'application,
évitant les valeurs magiques et facilitant la maintenance. Les constantes sont
regroupées par domaine thématique et sont immuables (typing.Final).

**Organisation** :
    - Identification de l'application
    - Versions et compatibilité
    - Limites et seuils globaux
    - Formats et extensions supportés
    - Patterns regex réutilisables
    - Valeurs par défaut (configuration)
    - Constantes de performance
    - Constantes de sécurité
    - Constantes temporelles
    - Constantes réseau
    - Constantes de stockage
    - Constantes d'interface utilisateur

**Règles d'utilisation** :
    1. Utiliser `Final` pour toutes les constantes
    2. Documenter chaque constante avec son usage
    3. Regrouper par thème avec des sections claires
    4. Éviter les doublons avec les modules spécialisés
    5. Préférer les frozenset aux sets pour l'immuabilité
    6. Utiliser des types appropriés (str, int, float, tuple, dict)

Exemple d'utilisation :
    >>> from nexusdl.core.constants import APP_NAME, APP_VERSION
    >>> print(f"{APP_NAME} v{APP_VERSION}")
    NexusDL v0.1.0
    >>>
    >>> from nexusdl.core.constants import SUPPORTED_IMAGE_EXTENSIONS
    >>> if file_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
    ...     print("Image supportée")
"""

from __future__ import annotations

from typing import Final


# ============================================================================
# IDENTIFICATION DE L'APPLICATION
# ============================================================================


# Nom officiel de l'application
APP_NAME: Final[str] = "NexusDL"

# Nom court (pour logs, CLI, etc.)
APP_SHORT_NAME: Final[str] = "nexusdl"

# Nom en minuscules (pour chemins, variables d'environnement)
APP_NAME_LOWER: Final[str] = "nexusdl"

# Version actuelle (SemVer)
APP_VERSION: Final[str] = "0.1.0"

# Version majeure (pour compatibilité)
APP_VERSION_MAJOR: Final[int] = 0

# Version mineure
APP_VERSION_MINOR: Final[int] = 1

# Version patch
APP_VERSION_PATCH: Final[int] = 0

# Auteur principal
APP_AUTHOR: Final[str] = "NexusDL Team"

# Email de contact
APP_AUTHOR_EMAIL: Final[str] = "contact@nexusdl.dev"

# URL du site web
APP_URL: Final[str] = "https://nexusdl.dev"

# URL du dépôt source
APP_REPOSITORY: Final[str] = "https://github.com/nexusdl/nexusdl"

# URL de la documentation
APP_DOCUMENTATION: Final[str] = "https://docs.nexusdl.dev"

# URL du rapport de bugs
APP_BUG_TRACKER: Final[str] = "https://github.com/nexusdl/nexusdl/issues"

# URL de la licence
APP_LICENSE_URL: Final[str] = "https://github.com/nexusdl/nexusdl/blob/main/LICENSE"

# Type de licence
APP_LICENSE: Final[str] = "MIT"

# Description courte
APP_DESCRIPTION: Final[str] = "Téléchargeur de mangas, webtoons et comics multi-sites"

# Description longue
APP_LONG_DESCRIPTION: Final[str] = (
    "NexusDL est un téléchargeur avancé pour mangas, webtoons et comics. "
    "Il supporte plus de 60 sites, offre une gestion complète de bibliothèque, "
    "et fournit une interface moderne (CLI, Web, GUI)."
)

# Tagline
APP_TAGLINE: Final[str] = "Your manga, your library, your way."

# User-Agent par défaut pour les requêtes HTTP
APP_USER_AGENT: Final[str] = (
    f"Mozilla/5.0 (compatible; {APP_NAME}/{APP_VERSION}; +{APP_URL})"
)


# ============================================================================
# VERSIONS ET COMPATIBILITÉ
# ============================================================================


# Version minimum de Python requise
PYTHON_MIN_VERSION: Final[tuple[int, int]] = (3, 11)

# Version de Python recommandée
PYTHON_RECOMMENDED_VERSION: Final[tuple[int, int]] = (3, 12)

# Version de Python supportée (maximum)
PYTHON_MAX_VERSION: Final[tuple[int, int]] = (3, 13)

# Chaîne de version Python pour affichage
PYTHON_VERSION_STRING: Final[str] = f"{PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}+"

# Versions minimum des dépendances principales
DEPENDENCY_VERSIONS: Final[dict[str, str]] = {
    "httpx": ">=0.27.0",
    "pydantic": ">=2.5.0",
    "loguru": ">=0.7.0",
    "pyyaml": ">=6.0.0",
    "orjson": ">=3.9.0",
    "pillow": ">=10.0.0",
    "aiosqlite": ">=0.19.0",
    "jsonschema": ">=4.20.0",
    "tenacity": ">=8.2.0",
    "platformdirs": ">=4.0.0",
    "cryptography": ">=41.0.0",
}

# Dépendances optionnelles
OPTIONAL_DEPENDENCIES: Final[dict[str, str]] = {
    "playwright": ">=1.40.0",
    "img2pdf": ">=0.5.0",
    "rarfile": ">=4.1",
    "pikepdf": ">=8.0.0",
    "rich": ">=13.0.0",
    "textual": ">=0.40.0",
    "fastapi": ">=0.100.0",
    "uvicorn": ">=0.24.0",
    "pyqt6": ">=6.5.0",
}


# ============================================================================
# LIMITES ET SEUILS GLOBAUX
# ============================================================================


# Taille maximale d'un fichier téléchargé (500 Mo)
MAX_FILE_SIZE_BYTES: Final[int] = 500 * 1024 * 1024

# Taille maximale d'une image (50 Mo)
MAX_IMAGE_SIZE_BYTES: Final[int] = 50 * 1024 * 1024

# Taille minimale d'une image valide (1 Ko)
MIN_IMAGE_SIZE_BYTES: Final[int] = 1024

# Taille maximale d'un fichier de configuration (10 Mo)
MAX_CONFIG_FILE_SIZE_BYTES: Final[int] = 10 * 1024 * 1024

# Taille maximale d'un fichier de traduction (5 Mo)
MAX_TRANSLATION_FILE_SIZE_BYTES: Final[int] = 5 * 1024 * 1024

# Nombre maximum de tâches de téléchargement simultanées
MAX_CONCURRENT_DOWNLOAD_TASKS: Final[int] = 5

# Nombre maximum de chapitres en téléchargement simultané
MAX_CONCURRENT_CHAPTERS: Final[int] = 3

# Nombre maximum de pages en téléchargement simultané
MAX_CONCURRENT_PAGES: Final[int] = 8

# Nombre maximum de retries par défaut
DEFAULT_MAX_RETRIES: Final[int] = 3

# Nombre maximum de retries (limite absolue)
ABSOLUTE_MAX_RETRIES: Final[int] = 10

# Délai maximum entre retries (secondes)
MAX_RETRY_DELAY_SECONDS: Final[float] = 60.0

# Délai initial entre retries (secondes)
INITIAL_RETRY_DELAY_SECONDS: Final[float] = 1.0

# Timeout global par défaut pour les opérations (secondes)
DEFAULT_OPERATION_TIMEOUT_SECONDS: Final[float] = 300.0

# Timeout maximum absolu (secondes)
ABSOLUTE_MAX_TIMEOUT_SECONDS: Final[float] = 3600.0

# Nombre maximum d'éléments dans une liste (pagination)
MAX_LIST_ITEMS: Final[int] = 1000

# Nombre maximum de résultats de recherche
MAX_SEARCH_RESULTS: Final[int] = 500

# Nombre maximum d'items par page (pagination)
DEFAULT_PAGE_SIZE: Final[int] = 20

# Taille maximum d'une page (pagination)
MAX_PAGE_SIZE: Final[int] = 100

# Nombre maximum de sites supportés
MAX_SITES_COUNT: Final[int] = 200

# Nombre maximum de mangas dans la bibliothèque
MAX_LIBRARY_MANGAS: Final[int] = 100000

# Nombre maximum de chapitres par manga
MAX_CHAPTERS_PER_MANGA: Final[int] = 10000

# Nombre maximum de pages par chapitre
MAX_PAGES_PER_CHAPTER: Final[int] = 1000


# ============================================================================
# FORMATS ET EXTENSIONS SUPPORTÉS
# ============================================================================


# Extensions d'images supportées (pour téléchargement)
SUPPORTED_IMAGE_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".avif",
    ".bmp",
    ".tiff",
    ".tif",
})

# Extensions d'images courantes (prioritaires)
COMMON_IMAGE_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
})

# Formats d'empaquetage supportés
SUPPORTED_PACKAGING_FORMATS: Final[frozenset[str]] = frozenset({
    "cbz",
    "cbr",
    "pdf",
    "zip",
    "folder",
})

# Format d'empaquetage par défaut
DEFAULT_PACKAGING_FORMAT: Final[str] = "cbz"

# Extensions d'archives supportées
SUPPORTED_ARCHIVE_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".cbz",
    ".cbr",
    ".zip",
    ".rar",
    ".cb7",
})

# Extensions de fichiers de configuration
CONFIG_FILE_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".yaml",
    ".yml",
    ".json",
    ".toml",
})

# Extensions de fichiers de traduction
TRANSLATION_FILE_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".json",
})

# Extensions de bases de données
DATABASE_FILE_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".db",
    ".sqlite",
    ".sqlite3",
})

# Extensions de fichiers de log
LOG_FILE_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".log",
    ".jsonl",
})

# Types MIME pour les images
IMAGE_MIME_TYPES: Final[dict[str, str]] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}

# Types MIME pour les archives
ARCHIVE_MIME_TYPES: Final[dict[str, str]] = {
    ".cbz": "application/vnd.comicbook+zip",
    ".cbr": "application/vnd.comicbook-rar",
    ".zip": "application/zip",
    ".rar": "application/vnd.rar",
    ".pdf": "application/pdf",
}


# ============================================================================
# PATTERNS REGEX RÉUTILISABLES
# ============================================================================


# Pattern pour valider un slug (snake_case)
SLUG_PATTERN: Final[str] = r"^[a-z][a-z0-9_]{1,63}$"

# Pattern pour valider un identifiant (alphanumérique + underscore)
ID_PATTERN: Final[str] = r"^[a-zA-Z0-9_]{3,64}$"

# Pattern pour valider un UUID
UUID_PATTERN: Final[str] = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"

# Pattern pour valider un hash SHA256
SHA256_PATTERN: Final[str] = r"^[a-f0-9]{64}$"

# Pattern pour valider un code langue ISO 639-1
LANGUAGE_CODE_PATTERN: Final[str] = r"^[a-z]{2}$"

# Pattern pour valider un code région ISO 3166-1 alpha-2
REGION_CODE_PATTERN: Final[str] = r"^[A-Z]{2}$"

# Pattern pour valider une URL HTTP/HTTPS
HTTP_URL_PATTERN: Final[str] = r"^https?://[^\s/$.?#].[^\s]*$"

# Pattern pour valider un email (simplifié)
EMAIL_PATTERN: Final[str] = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"

# Pattern pour valider un nom d'utilisateur
USERNAME_PATTERN: Final[str] = r"^[a-zA-Z0-9_]{3,30}$"

# Pattern pour valider un numéro de chapitre (float ou string)
CHAPTER_NUMBER_PATTERN: Final[str] = r"^(\d+(\.\d+)?|[a-zA-Z0-9_-]+)$"

# Pattern pour valider une version SemVer
SEMVER_PATTERN: Final[str] = r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$"

# Pattern pour valider une date ISO 8601
ISO_DATE_PATTERN: Final[str] = r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?)?$"

# Pattern pour extraire un domaine d'une URL
DOMAIN_EXTRACTION_PATTERN: Final[str] = r"^(?:https?://)?(?:www\.)?([^/]+)"

# Pattern pour valider un nom de fichier (sans caractères spéciaux)
FILENAME_PATTERN: Final[str] = r"^[^<>:\"/\\|?*\x00-\x1f]+$"

# Pattern pour valider un chemin relatif (pas de traversal)
RELATIVE_PATH_PATTERN: Final[str] = r"^(?!.*\.\./)[^\x00]+$"


# ============================================================================
# VALEURS PAR DÉFAUT (CONFIGURATION)
# ============================================================================


# Langue par défaut
DEFAULT_LANGUAGE: Final[str] = "en"

# Langue de fallback pour i18n
FALLBACK_LANGUAGE: Final[str] = "en"

# Thème par défaut pour l'interface
DEFAULT_THEME: Final[str] = "system"

# Format de date par défaut
DEFAULT_DATE_FORMAT: Final[str] = "iso"

# Format d'heure par défaut
DEFAULT_TIME_FORMAT: Final[str] = "24h"

# Fuseau horaire par défaut
DEFAULT_TIMEZONE: Final[str] = "auto"

# Sens de lecture par défaut (right-to-left pour manga)
DEFAULT_READING_DIRECTION: Final[str] = "rtl"

# Qualité d'image par défaut
DEFAULT_IMAGE_QUALITY: Final[str] = "original"

# Niveau de compression ZIP par défaut (0-9)
DEFAULT_ZIP_COMPRESSION_LEVEL: Final[int] = 6

# Niveau de compression RAR par défaut (0-5)
DEFAULT_RAR_COMPRESSION_LEVEL: Final[int] = 3

# Qualité JPEG par défaut pour conversions (1-100)
DEFAULT_JPEG_QUALITY: Final[int] = 85

# DPI par défaut pour PDF
DEFAULT_PDF_DPI: Final[int] = 150

# Niveau de log par défaut
DEFAULT_LOG_LEVEL: Final[str] = "INFO"

# Format de log par défaut
DEFAULT_LOG_FORMAT: Final[str] = "text"

# Taille de rotation des logs par défaut
DEFAULT_LOG_ROTATION: Final[str] = "10 MB"

# Rétention des logs par défaut
DEFAULT_LOG_RETENTION: Final[str] = "7 days"

# Intervalle de scan automatique par défaut (secondes)
DEFAULT_SCAN_INTERVAL_SECONDS: Final[float] = 300.0

# Timeout de requête HTTP par défaut (secondes)
DEFAULT_HTTP_TIMEOUT_SECONDS: Final[float] = 30.0

# Timeout de connexion par défaut (secondes)
DEFAULT_CONNECT_TIMEOUT_SECONDS: Final[float] = 10.0

# Nombre maximum de connexions HTTP simultanées
DEFAULT_MAX_HTTP_CONNECTIONS: Final[int] = 100

# Nombre maximum de connexions keep-alive
DEFAULT_MAX_KEEPALIVE_CONNECTIONS: Final[int] = 20

# Stratégie de rotation des User-Agents par défaut
DEFAULT_UA_ROTATION_STRATEGY: Final[str] = "random"

# Stratégie de rotation des proxies par défaut
DEFAULT_PROXY_ROTATION_STRATEGY: Final[str] = "round_robin"

# Intervalle de health check des proxies par défaut (secondes)
DEFAULT_PROXY_HEALTH_CHECK_INTERVAL_SECONDS: Final[float] = 60.0

# Taille du cache de cookies par défaut
DEFAULT_COOKIE_CACHE_SIZE: Final[int] = 1000

# Durée de vie du cache de cookies par défaut (secondes)
DEFAULT_COOKIE_CACHE_TTL_SECONDS: Final[float] = 900.0

# Taille du cache de traductions par défaut
DEFAULT_TRANSLATION_CACHE_SIZE: Final[int] = 10000

# Taille du cache de métadonnées par défaut
DEFAULT_METADATA_CACHE_SIZE: Final[int] = 1000

# Taille du cache de déduplication par défaut
DEFAULT_DEDUP_CACHE_SIZE: Final[int] = 100000

# Intervalle de nettoyage du cache par défaut (secondes)
DEFAULT_CACHE_CLEANUP_INTERVAL_SECONDS: Final[float] = 3600.0

# Âge maximum du cache avant nettoyage (secondes)
DEFAULT_CACHE_MAX_AGE_SECONDS: Final[float] = 86400.0


# ============================================================================
# CONSTANTES DE PERFORMANCE
# ============================================================================


# Taille du buffer pour lecture/écriture de fichiers (64 Ko)
FILE_BUFFER_SIZE: Final[int] = 65536

# Taille du buffer pour streaming HTTP (64 Ko)
HTTP_STREAM_BUFFER_SIZE: Final[int] = 65536

# Taille du buffer pour compression (128 Ko)
COMPRESSION_BUFFER_SIZE: Final[int] = 131072

# Nombre maximum de workers pour opérations I/O
MAX_IO_WORKERS: Final[int] = 8

# Nombre maximum de workers pour opérations CPU
MAX_CPU_WORKERS: Final[int] = 4

# Taille de la file d'événements par défaut
DEFAULT_EVENT_QUEUE_SIZE: Final[int] = 10000

# Nombre de workers pour l'EventBus par défaut
DEFAULT_EVENT_BUS_WORKERS: Final[int] = 1

# Timeout pour les handlers d'événements par défaut (secondes)
DEFAULT_EVENT_HANDLER_TIMEOUT_SECONDS: Final[float] = 30.0

# Taille du pool de connexions SQLite
SQLITE_POOL_SIZE: Final[int] = 5

# Taille du cache SQLite (en Ko, négatif = Ko)
SQLITE_CACHE_SIZE_KB: Final[int] = -8000

# Timeout SQLite pour busy (millisecondes)
SQLITE_BUSY_TIMEOUT_MS: Final[int] = 5000

# Taille maximale du journal WAL SQLite (Mo)
SQLITE_WAL_SIZE_LIMIT_MB: Final[int] = 10

# Nombre maximum d'items dans le cache LRU
DEFAULT_LRU_CACHE_SIZE: Final[int] = 1000

# Seuil de mémoire pour déclencher le garbage collection (Mo)
MEMORY_GC_THRESHOLD_MB: Final[int] = 512

# Intervalle de vérification de mémoire (secondes)
MEMORY_CHECK_INTERVAL_SECONDS: Final[float] = 60.0


# ============================================================================
# CONSTANTES DE SÉCURITÉ
# ============================================================================


# Longueur minimum d'un mot de passe
MIN_PASSWORD_LENGTH: Final[int] = 8

# Longueur maximum d'un mot de passe
MAX_PASSWORD_LENGTH: Final[int] = 128

# Nombre de rounds pour bcrypt
BCRYPT_ROUNDS: Final[int] = 12

# Durée de vie d'un token d'accès JWT (minutes)
ACCESS_TOKEN_LIFETIME_MINUTES: Final[int] = 30

# Durée de vie d'un token de refresh JWT (jours)
REFRESH_TOKEN_LIFETIME_DAYS: Final[int] = 7

# Durée de vie d'une session (jours)
SESSION_LIFETIME_DAYS: Final[int] = 30

# Timeout d'inactivité d'une session (minutes)
SESSION_INACTIVITY_TIMEOUT_MINUTES: Final[int] = 30

# Longueur d'une clé API (bytes)
API_KEY_LENGTH_BYTES: Final[int] = 48

# Préfixe des clés API
API_KEY_PREFIX: Final[str] = "nxl_"

# Permissions de fichier pour les fichiers sensibles (Unix)
SECURE_FILE_PERMISSIONS: Final[int] = 0o600

# Permissions de répertoire pour les répertoires sensibles (Unix)
SECURE_DIR_PERMISSIONS: Final[int] = 0o700

# Permissions de fichier par défaut (Unix)
DEFAULT_FILE_PERMISSIONS: Final[int] = 0o644

# Permissions de répertoire par défaut (Unix)
DEFAULT_DIR_PERMISSIONS: Final[int] = 0o755

# Nombre maximum de tentatives de connexion avant blocage
MAX_LOGIN_ATTEMPTS: Final[int] = 5

# Durée de blocage après échecs de connexion (minutes)
LOGIN_BLOCK_DURATION_MINUTES: Final[int] = 15

# Longueur minimum d'un nom d'utilisateur
MIN_USERNAME_LENGTH: Final[int] = 3

# Longueur maximum d'un nom d'utilisateur
MAX_USERNAME_LENGTH: Final[int] = 30

# Longueur maximum d'un email
MAX_EMAIL_LENGTH: Final[int] = 254


# ============================================================================
# CONSTANTES TEMPORELLES
# ============================================================================


# Secondes par minute
SECONDS_PER_MINUTE: Final[int] = 60

# Secondes par heure
SECONDS_PER_HOUR: Final[int] = 3600

# Secondes par jour
SECONDS_PER_DAY: Final[int] = 86400

# Secondes par semaine
SECONDS_PER_WEEK: Final[int] = 604800

# Secondes par mois (30 jours)
SECONDS_PER_MONTH: Final[int] = 2592000

# Secondes par an (365 jours)
SECONDS_PER_YEAR: Final[int] = 31536000

# Millisecondes par seconde
MILLISECONDS_PER_SECOND: Final[int] = 1000

# Microsecondes par seconde
MICROSECONDS_PER_SECOND: Final[int] = 1000000

# Minutes par heure
MINUTES_PER_HOUR: Final[int] = 60

# Minutes par jour
MINUTES_PER_DAY: Final[int] = 1440

# Heures par jour
HOURS_PER_DAY: Final[int] = 24

# Jours par semaine
DAYS_PER_WEEK: Final[int] = 7

# Jours par mois (moyenne)
DAYS_PER_MONTH: Final[float] = 30.44

# Jours par an
DAYS_PER_YEAR: Final[int] = 365


# ============================================================================
# CONSTANTES RÉSEAU
# ============================================================================


# Ports standards
HTTP_PORT: Final[int] = 80
HTTPS_PORT: Final[int] = 443
SSH_PORT: Final[int] = 22
FTP_PORT: Final[int] = 21

# Ports par défaut pour les services NexusDL
DEFAULT_WEB_UI_PORT: Final[int] = 8080
DEFAULT_API_PORT: Final[int] = 8081
DEFAULT_WEBSOCKET_PORT: Final[int] = 8082
DEFAULT_FLARESOLVERR_PORT: Final[int] = 8191

# Timeout DNS par défaut (secondes)
DEFAULT_DNS_TIMEOUT_SECONDS: Final[float] = 5.0

# Nombre maximum de redirections HTTP
MAX_HTTP_REDIRECTS: Final[int] = 10

# Taille maximum d'un header HTTP (Ko)
MAX_HTTP_HEADER_SIZE_KB: Final[int] = 8

# Taille maximum d'un corps de requête HTTP (Mo)
MAX_HTTP_BODY_SIZE_MB: Final[int] = 100

# Codes HTTP courants
HTTP_OK: Final[int] = 200
HTTP_CREATED: Final[int] = 201
HTTP_NO_CONTENT: Final[int] = 204
HTTP_BAD_REQUEST: Final[int] = 400
HTTP_UNAUTHORIZED: Final[int] = 401
HTTP_FORBIDDEN: Final[int] = 403
HTTP_NOT_FOUND: Final[int] = 404
HTTP_METHOD_NOT_ALLOWED: Final[int] = 405
HTTP_CONFLICT: Final[int] = 409
HTTP_GONE: Final[int] = 410
HTTP_TOO_MANY_REQUESTS: Final[int] = 429
HTTP_INTERNAL_SERVER_ERROR: Final[int] = 500
HTTP_NOT_IMPLEMENTED: Final[int] = 501
HTTP_BAD_GATEWAY: Final[int] = 502
HTTP_SERVICE_UNAVAILABLE: Final[int] = 503
HTTP_GATEWAY_TIMEOUT: Final[int] = 504

# Codes HTTP de succès
HTTP_SUCCESS_CODES: Final[frozenset[int]] = frozenset({200, 201, 202, 204})

# Codes HTTP de redirection
HTTP_REDIRECT_CODES: Final[frozenset[int]] = frozenset({301, 302, 303, 307, 308})

# Codes HTTP d'erreur client
HTTP_CLIENT_ERROR_CODES: Final[frozenset[int]] = frozenset(range(400, 500))

# Codes HTTP d'erreur serveur
HTTP_SERVER_ERROR_CODES: Final[frozenset[int]] = frozenset(range(500, 600))

# Codes HTTP retryables
HTTP_RETRYABLE_CODES: Final[frozenset[int]] = frozenset({
    408,  # Request Timeout
    425,  # Too Early
    429,  # Too Many Requests
    500,  # Internal Server Error
    502,  # Bad Gateway
    503,  # Service Unavailable
    504,  # Gateway Timeout
})


# ============================================================================
# CONSTANTES DE STOCKAGE
# ============================================================================


# Unités de stockage (en bytes)
BYTE: Final[int] = 1
KILOBYTE: Final[int] = 1024
MEGABYTE: Final[int] = 1024 * 1024
GIGABYTE: Final[int] = 1024 * 1024 * 1024
TERABYTE: Final[int] = 1024 * 1024 * 1024 * 1024

# Tailles de fichiers courantes
SMALL_FILE_THRESHOLD_BYTES: Final[int] = 100 * KILOBYTE  # 100 Ko
MEDIUM_FILE_THRESHOLD_BYTES: Final[int] = 10 * MEGABYTE  # 10 Mo
LARGE_FILE_THRESHOLD_BYTES: Final[int] = 100 * MEGABYTE  # 100 Mo

# Espace disque minimum requis pour fonctionner (Mo)
MIN_DISK_SPACE_MB: Final[int] = 100

# Espace disque minimum recommandé (Go)
RECOMMENDED_DISK_SPACE_GB: Final[int] = 10

# Seuil d'alerte d'espace disque faible (Mo)
LOW_DISK_SPACE_THRESHOLD_MB: Final[int] = 500

# Seuil critique d'espace disque (Mo)
CRITICAL_DISK_SPACE_THRESHOLD_MB: Final[int] = 100


# ============================================================================
# CONSTANTES D'INTERFACE UTILISATEUR
# ============================================================================


# Largeur minimum du terminal pour l'interface CLI
MIN_TERMINAL_WIDTH: Final[int] = 80

# Hauteur minimum du terminal pour l'interface CLI
MIN_TERMINAL_HEIGHT: Final[int] = 24

# Largeur par défaut du terminal
DEFAULT_TERMINAL_WIDTH: Final[int] = 120

# Nombre maximum de lignes dans un tableau CLI
MAX_CLI_TABLE_ROWS: Final[int] = 50

# Nombre maximum de caractères par ligne dans un tableau CLI
MAX_CLI_TABLE_COLUMN_WIDTH: Final[int] = 50

# Caractères de dessin pour les bordures de tableaux CLI
CLI_TABLE_BORDER_CHARS: Final[dict[str, str]] = {
    "horizontal": "─",
    "vertical": "│",
    "top_left": "┌",
    "top_right": "┐",
    "bottom_left": "└",
    "bottom_right": "┘",
    "top_tee": "┬",
    "bottom_tee": "┴",
    "left_tee": "├",
    "right_tee": "┤",
    "cross": "┼",
}

# Caractères pour les barres de progression CLI
CLI_PROGRESS_CHARS: Final[dict[str, str]] = {
    "empty": "░",
    "fill": "█",
    "complete": "✔",
}

# Symboles pour les statuts CLI
CLI_STATUS_SYMBOLS: Final[dict[str, str]] = {
    "success": "✅",
    "error": "❌",
    "warning": "⚠️",
    "info": "ℹ️",
    "debug": "🐛",
    "pending": "⏳",
    "running": "⚙️",
    "paused": "⏸️",
    "stopped": "⏹️",
}

# Couleurs ANSI pour le terminal
ANSI_COLORS: Final[dict[str, str]] = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "italic": "\033[3m",
    "underline": "\033[4m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
    "bright_red": "\033[91m",
    "bright_green": "\033[92m",
    "bright_yellow": "\033[93m",
    "bright_blue": "\033[94m",
    "bright_magenta": "\033[95m",
    "bright_cyan": "\033[96m",
    "bright_white": "\033[97m",
}

# Largeur des colonnes pour l'affichage des listes
LIST_COLUMN_WIDTHS: Final[dict[str, int]] = {
    "id": 8,
    "title": 40,
    "status": 12,
    "language": 5,
    "chapters": 10,
    "date": 12,
}

# Nombre maximum d'items affichés dans une liste CLI
MAX_CLI_LIST_ITEMS: Final[int] = 20

# Caractère de troncature pour les textes longs
TRUNCATION_CHAR: Final[str] = "…"

# Longueur maximum d'un texte avant troncature
MAX_TEXT_LENGTH: Final[int] = 100


# ============================================================================
# CONSTANTES DIVERSES
# ============================================================================


# Séparateur de chemin universel
PATH_SEPARATOR: Final[str] = "/"

# Séparateur de chemin Windows
WINDOWS_PATH_SEPARATOR: Final[str] = "\\"

# Séparateur de ligne universel
LINE_SEPARATOR: Final[str] = "\n"

# Séparateur de ligne Windows
WINDOWS_LINE_SEPARATOR: Final[str] = "\r\n"

# Encodage de caractères par défaut
DEFAULT_ENCODING: Final[str] = "utf-8"

# Encodage de caractères pour les fichiers de configuration
CONFIG_ENCODING: Final[str] = "utf-8"

# Caractères interdits dans les noms de fichiers
INVALID_FILENAME_CHARS: Final[frozenset[str]] = frozenset({
    "<", ">", ":", '"', "/", "\\", "|", "?", "*",
})

# Caractères de contrôle ASCII
CONTROL_CHARS: Final[frozenset[str]] = frozenset(
    chr(i) for i in range(32)
)

# Nombres premiers pour les algorithmes de hash (optimisation)
PRIME_NUMBERS: Final[tuple[int, ...]] = (
    2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71,
    73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149, 151,
    157, 163, 167, 173, 179, 181, 191, 193, 197, 199, 211, 223, 227, 229, 233,
    239, 241, 251, 257, 263, 269, 271, 277, 281, 283, 293, 307, 311, 313, 317,
)

# Alphabet pour la génération d'IDs aléatoires
RANDOM_ID_ALPHABET: Final[str] = "abcdefghijklmnopqrstuvwxyz0123456789"

# Longueur par défaut des IDs aléatoires
RANDOM_ID_LENGTH: Final[int] = 16

# Alphabet hexadécimal
HEX_ALPHABET: Final[str] = "0123456789abcdef"

# Alphabet base64
BASE64_ALPHABET: Final[str] = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
)

# Alphabet base64 URL-safe
BASE64_URL_ALPHABET: Final[str] = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Identification
    "APP_NAME",
    "APP_SHORT_NAME",
    "APP_NAME_LOWER",
    "APP_VERSION",
    "APP_VERSION_MAJOR",
    "APP_VERSION_MINOR",
    "APP_VERSION_PATCH",
    "APP_AUTHOR",
    "APP_AUTHOR_EMAIL",
    "APP_URL",
    "APP_REPOSITORY",
    "APP_DOCUMENTATION",
    "APP_BUG_TRACKER",
    "APP_LICENSE_URL",
    "APP_LICENSE",
    "APP_DESCRIPTION",
    "APP_LONG_DESCRIPTION",
    "APP_TAGLINE",
    "APP_USER_AGENT",
    # Versions
    "PYTHON_MIN_VERSION",
    "PYTHON_RECOMMENDED_VERSION",
    "PYTHON_MAX_VERSION",
    "PYTHON_VERSION_STRING",
    "DEPENDENCY_VERSIONS",
    "OPTIONAL_DEPENDENCIES",
    # Limites
    "MAX_FILE_SIZE_BYTES",
    "MAX_IMAGE_SIZE_BYTES",
    "MIN_IMAGE_SIZE_BYTES",
    "MAX_CONFIG_FILE_SIZE_BYTES",
    "MAX_TRANSLATION_FILE_SIZE_BYTES",
    "MAX_CONCURRENT_DOWNLOAD_TASKS",
    "MAX_CONCURRENT_CHAPTERS",
    "MAX_CONCURRENT_PAGES",
    "DEFAULT_MAX_RETRIES",
    "ABSOLUTE_MAX_RETRIES",
    "MAX_RETRY_DELAY_SECONDS",
    "INITIAL_RETRY_DELAY_SECONDS",
    "DEFAULT_OPERATION_TIMEOUT_SECONDS",
    "ABSOLUTE_MAX_TIMEOUT_SECONDS",
    "MAX_LIST_ITEMS",
    "MAX_SEARCH_RESULTS",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "MAX_SITES_COUNT",
    "MAX_LIBRARY_MANGAS",
    "MAX_CHAPTERS_PER_MANGA",
    "MAX_PAGES_PER_CHAPTER",
    # Formats
    "SUPPORTED_IMAGE_EXTENSIONS",
    "COMMON_IMAGE_EXTENSIONS",
    "SUPPORTED_PACKAGING_FORMATS",
    "DEFAULT_PACKAGING_FORMAT",
    "SUPPORTED_ARCHIVE_EXTENSIONS",
    "CONFIG_FILE_EXTENSIONS",
    "TRANSLATION_FILE_EXTENSIONS",
    "DATABASE_FILE_EXTENSIONS",
    "LOG_FILE_EXTENSIONS",
    "IMAGE_MIME_TYPES",
    "ARCHIVE_MIME_TYPES",
    # Patterns
    "SLUG_PATTERN",
    "ID_PATTERN",
    "UUID_PATTERN",
    "SHA256_PATTERN",
    "LANGUAGE_CODE_PATTERN",
    "REGION_CODE_PATTERN",
    "HTTP_URL_PATTERN",
    "EMAIL_PATTERN",
    "USERNAME_PATTERN",
    "CHAPTER_NUMBER_PATTERN",
    "SEMVER_PATTERN",
    "ISO_DATE_PATTERN",
    "DOMAIN_EXTRACTION_PATTERN",
    "FILENAME_PATTERN",
    "RELATIVE_PATH_PATTERN",
    # Valeurs par défaut
    "DEFAULT_LANGUAGE",
    "FALLBACK_LANGUAGE",
    "DEFAULT_THEME",
    "DEFAULT_DATE_FORMAT",
    "DEFAULT_TIME_FORMAT",
    "DEFAULT_TIMEZONE",
    "DEFAULT_READING_DIRECTION",
    "DEFAULT_IMAGE_QUALITY",
    "DEFAULT_ZIP_COMPRESSION_LEVEL",
    "DEFAULT_RAR_COMPRESSION_LEVEL",
    "DEFAULT_JPEG_QUALITY",
    "DEFAULT_PDF_DPI",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_LOG_FORMAT",
    "DEFAULT_LOG_ROTATION",
    "DEFAULT_LOG_RETENTION",
    "DEFAULT_SCAN_INTERVAL_SECONDS",
    "DEFAULT_HTTP_TIMEOUT_SECONDS",
    "DEFAULT_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_MAX_HTTP_CONNECTIONS",
    "DEFAULT_MAX_KEEPALIVE_CONNECTIONS",
    "DEFAULT_UA_ROTATION_STRATEGY",
    "DEFAULT_PROXY_ROTATION_STRATEGY",
    "DEFAULT_PROXY_HEALTH_CHECK_INTERVAL_SECONDS",
    "DEFAULT_COOKIE_CACHE_SIZE",
    "DEFAULT_COOKIE_CACHE_TTL_SECONDS",
    "DEFAULT_TRANSLATION_CACHE_SIZE",
    "DEFAULT_METADATA_CACHE_SIZE",
    "DEFAULT_DEDUP_CACHE_SIZE",
    "DEFAULT_CACHE_CLEANUP_INTERVAL_SECONDS",
    "DEFAULT_CACHE_MAX_AGE_SECONDS",
    # Performance
    "FILE_BUFFER_SIZE",
    "HTTP_STREAM_BUFFER_SIZE",
    "COMPRESSION_BUFFER_SIZE",
    "MAX_IO_WORKERS",
    "MAX_CPU_WORKERS",
    "DEFAULT_EVENT_QUEUE_SIZE",
    "DEFAULT_EVENT_BUS_WORKERS",
    "DEFAULT_EVENT_HANDLER_TIMEOUT_SECONDS",
    "SQLITE_POOL_SIZE",
    "SQLITE_CACHE_SIZE_KB",
    "SQLITE_BUSY_TIMEOUT_MS",
    "SQLITE_WAL_SIZE_LIMIT_MB",
    "DEFAULT_LRU_CACHE_SIZE",
    "MEMORY_GC_THRESHOLD_MB",
    "MEMORY_CHECK_INTERVAL_SECONDS",
    # Sécurité
    "MIN_PASSWORD_LENGTH",
    "MAX_PASSWORD_LENGTH",
    "BCRYPT_ROUNDS",
    "ACCESS_TOKEN_LIFETIME_MINUTES",
    "REFRESH_TOKEN_LIFETIME_DAYS",
    "SESSION_LIFETIME_DAYS",
    "SESSION_INACTIVITY_TIMEOUT_MINUTES",
    "API_KEY_LENGTH_BYTES",
    "API_KEY_PREFIX",
    "SECURE_FILE_PERMISSIONS",
    "SECURE_DIR_PERMISSIONS",
    "DEFAULT_FILE_PERMISSIONS",
    "DEFAULT_DIR_PERMISSIONS",
    "MAX_LOGIN_ATTEMPTS",
    "LOGIN_BLOCK_DURATION_MINUTES",
    "MIN_USERNAME_LENGTH",
    "MAX_USERNAME_LENGTH",
    "MAX_EMAIL_LENGTH",
    # Temps
    "SECONDS_PER_MINUTE",
    "SECONDS_PER_HOUR",
    "SECONDS_PER_DAY",
    "SECONDS_PER_WEEK",
    "SECONDS_PER_MONTH",
    "SECONDS_PER_YEAR",
    "MILLISECONDS_PER_SECOND",
    "MICROSECONDS_PER_SECOND",
    "MINUTES_PER_HOUR",
    "MINUTES_PER_DAY",
    "HOURS_PER_DAY",
    "DAYS_PER_WEEK",
    "DAYS_PER_MONTH",
    "DAYS_PER_YEAR",
    # Réseau
    "HTTP_PORT",
    "HTTPS_PORT",
    "SSH_PORT",
    "FTP_PORT",
    "DEFAULT_WEB_UI_PORT",
    "DEFAULT_API_PORT",
    "DEFAULT_WEBSOCKET_PORT",
    "DEFAULT_FLARESOLVERR_PORT",
    "DEFAULT_DNS_TIMEOUT_SECONDS",
    "MAX_HTTP_REDIRECTS",
    "MAX_HTTP_HEADER_SIZE_KB",
    "MAX_HTTP_BODY_SIZE_MB",
    "HTTP_OK",
    "HTTP_CREATED",
    "HTTP_NO_CONTENT",
    "HTTP_BAD_REQUEST",
    "HTTP_UNAUTHORIZED",
    "HTTP_FORBIDDEN",
    "HTTP_NOT_FOUND",
    "HTTP_METHOD_NOT_ALLOWED",
    "HTTP_CONFLICT",
    "HTTP_GONE",
    "HTTP_TOO_MANY_REQUESTS",
    "HTTP_INTERNAL_SERVER_ERROR",
    "HTTP_NOT_IMPLEMENTED",
    "HTTP_BAD_GATEWAY",
    "HTTP_SERVICE_UNAVAILABLE",
    "HTTP_GATEWAY_TIMEOUT",
    "HTTP_SUCCESS_CODES",
    "HTTP_REDIRECT_CODES",
    "HTTP_CLIENT_ERROR_CODES",
    "HTTP_SERVER_ERROR_CODES",
    "HTTP_RETRYABLE_CODES",
    # Stockage
    "BYTE",
    "KILOBYTE",
    "MEGABYTE",
    "GIGABYTE",
    "TERABYTE",
    "SMALL_FILE_THRESHOLD_BYTES",
    "MEDIUM_FILE_THRESHOLD_BYTES",
    "LARGE_FILE_THRESHOLD_BYTES",
    "MIN_DISK_SPACE_MB",
    "RECOMMENDED_DISK_SPACE_GB",
    "LOW_DISK_SPACE_THRESHOLD_MB",
    "CRITICAL_DISK_SPACE_THRESHOLD_MB",
    # Interface
    "MIN_TERMINAL_WIDTH",
    "MIN_TERMINAL_HEIGHT",
    "DEFAULT_TERMINAL_WIDTH",
    "MAX_CLI_TABLE_ROWS",
    "MAX_CLI_TABLE_COLUMN_WIDTH",
    "CLI_TABLE_BORDER_CHARS",
    "CLI_PROGRESS_CHARS",
    "CLI_STATUS_SYMBOLS",
    "ANSI_COLORS",
    "LIST_COLUMN_WIDTHS",
    "MAX_CLI_LIST_ITEMS",
    "TRUNCATION_CHAR",
    "MAX_TEXT_LENGTH",
    # Divers
    "PATH_SEPARATOR",
    "WINDOWS_PATH_SEPARATOR",
    "LINE_SEPARATOR",
    "WINDOWS_LINE_SEPARATOR",
    "DEFAULT_ENCODING",
    "CONFIG_ENCODING",
    "INVALID_FILENAME_CHARS",
    "CONTROL_CHARS",
    "PRIME_NUMBERS",
    "RANDOM_ID_ALPHABET",
    "RANDOM_ID_LENGTH",
    "HEX_ALPHABET",
    "BASE64_ALPHABET",
    "BASE64_URL_ALPHABET",
]
