"""Gestionnaire centralisé des chemins de l'application (XDG-compliant).

Ce module fournit un système unifié de gestion des chemins pour NexusDL,
respectant les standards XDG Base Directory sur Linux et les conventions
natives sur macOS et Windows. Il utilise la librairie `platformdirs` pour
abstraire les différences entre systèmes d'exploitation.

**Standards respectés** :
    - Linux   : XDG Base Directory Specification
                  ~/.config/nexusdl/       (configuration)
                  ~/.local/share/nexusdl/  (données)
                  ~/.cache/nexusdl/        (cache)
                  ~/.local/state/nexusdl/  (état, logs)
    - macOS   : Conventions Apple
                  ~/Library/Application Support/nexusdl/  (config + data)
                  ~/Library/Caches/nexusdl/               (cache)
                  ~/Library/Logs/nexusdl/                 (logs)
    - Windows : Conventions Microsoft
                  %APPDATA%\\nexusdl\\       (config + data)
                  %LOCALAPPDATA%\\nexusdl\\  (cache)

**Répertoire par type** :
    config  : Fichiers de configuration (config.yaml, sites_overrides.yaml, .cookie_key)
    data    : Données persistantes (library.db, dedup.db, cookies.enc)
    cache   : Données temporaires régénérables (thumbnails, downloads temporaires)
    logs    : Fichiers de logs (nexusdl.log, nexusdl.error.log)
    temp    : Fichiers temporaires très court terme (opérations atomiques)
    runtime : PID files, sockets, locks (état d'exécution)

**Priorité de résolution** :
    1. Variable d'environnement (NEXUSDL_CONFIG_DIR, NEXUSDL_DATA_DIR, etc.)
    2. Configuration utilisateur (paths.* dans config.yaml)
    3. Valeurs par défaut (platformdirs)

Architecture :
    Paths (classe principale)
        ├── PathsConfig (Pydantic) : configuration des chemins
        ├── Platform (enum) : détection du système d'exploitation
        ├── PathType (enum) : type de répertoire (config, data, cache, etc.)
        └── Fonctions helpers : ensure_dir, get_temp_file, etc.

Exemple d'utilisation :
    >>> from nexusdl.core.paths import paths
    >>>
    >>> # Accéder aux chemins standards
    >>> print(paths.config_dir)
    /home/user/.config/nexusdl
    >>> print(paths.data_dir)
    /home/user/.local/share/nexusdl
    >>>
    >>> # Obtenir un chemin spécifique
    >>> db_path = paths.get_data_file("library.db")
    >>> print(db_path)
    /home/user/.local/share/nexusdl/library.db
    >>>
    >>> # Créer un répertoire automatiquement
    >>> logs_dir = paths.ensure_dir(paths.logs_dir)
    >>>
    >>> # Obtenir un fichier temporaire unique
    >>> temp_file = paths.get_temp_file(prefix="download_", suffix=".tmp")
    >>>
    >>> # Personnaliser les chemins (via config)
    >>> custom_paths = Paths(config=PathsConfig(data_dir=Path("/custom/data")))
    >>> print(custom_paths.data_dir)
    /custom/data

Intégration :
    - `core/config.py`       : charge config.yaml depuis paths.config_file
    - `core/library/database.py` : utilise paths.get_data_file("library.db")
    - `core/downloader/deduplication.py` : utilise paths.get_data_file("dedup.db")
    - `core/session/cookie_manager.py` : utilise paths.get_data_file("cookies.enc")
    - `core/logger.py`       : écrit les logs dans paths.logs_dir
    - `interfaces/*`         : utilise les chemins par défaut pour l'UI
"""

from __future__ import annotations

import os
import platform
import tempfile
import uuid
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Final, Self

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic import ValidationError as PydanticValidationError

from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# CONSTANTES — Identifiants de l'application
# ============================================================================


# Nom de l'application (utilisé par platformdirs)
_APP_NAME: Final[str] = "NexusDL"
_APP_AUTHOR: Final[str] = "NexusDL Team"
_APP_VERSION: Final[str] = "0.1.0"

# Variables d'environnement pour override des chemins
_ENV_CONFIG_DIR: Final[str] = "NEXUSDL_CONFIG_DIR"
_ENV_DATA_DIR: Final[str] = "NEXUSDL_DATA_DIR"
_ENV_CACHE_DIR: Final[str] = "NEXUSDL_CACHE_DIR"
_ENV_LOGS_DIR: Final[str] = "NEXUSDL_LOGS_DIR"
_ENV_TEMP_DIR: Final[str] = "NEXUSDL_TEMP_DIR"
_ENV_RUNTIME_DIR: Final[str] = "NEXUSDL_RUNTIME_DIR"

# Noms de fichiers standards
_CONFIG_FILENAME: Final[str] = "config.yaml"
_SITES_OVERRIDES_FILENAME: Final[str] = "sites_overrides.yaml"
_COOKIE_KEY_FILENAME: Final[str] = ".cookie_key"
_COOKIES_FILENAME: Final[str] = "cookies.enc"
_LIBRARY_DB_FILENAME: Final[str] = "library.db"
_DEDUP_DB_FILENAME: Final[str] = "dedup.db"
_LOG_FILENAME: Final[str] = "nexusdl.log"
_ERROR_LOG_FILENAME: Final[str] = "nexusdl.error.log"
_PID_FILENAME: Final[str] = "nexusdl.pid"

# Permissions par défaut pour les répertoires (Unix seulement)
_DIR_PERMISSIONS: Final[int] = 0o755
_SECURE_FILE_PERMISSIONS: Final[int] = 0o600


# ============================================================================
# EXCEPTIONS
# ============================================================================


class PathsError(NexusDLError):
    """Exception de base pour les erreurs liées aux chemins."""


class PathCreationError(PathsError):
    """Exception levée lorsqu'un répertoire ne peut être créé."""

    def __init__(self, path: Path, reason: str = "") -> None:
        msg = f"Impossible de créer le répertoire: {path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.path = path
        self.reason = reason


class PathAccessError(PathsError):
    """Exception levée lorsqu'un chemin n'est pas accessible."""

    def __init__(self, path: Path, reason: str = "") -> None:
        msg = f"Chemin inaccessible: {path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.path = path
        self.reason = reason


class InvalidPathError(PathsError):
    """Exception levée lorsqu'un chemin est invalide."""

    def __init__(self, path: Any, reason: str = "") -> None:
        msg = f"Chemin invalide: {path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.path = path
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class Platform(str, Enum):
    """Système d'exploitation détecté.

    LINUX   : Distribution Linux (XDG).
    MACOS   : macOS (conventions Apple).
    WINDOWS : Windows (conventions Microsoft).
    UNKNOWN : Système non reconnu (fallback Linux).
    """

    LINUX = "linux"
    MACOS = "macos"
    WINDOWS = "windows"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            Platform.LINUX: "Linux",
            Platform.MACOS: "macOS",
            Platform.WINDOWS: "Windows",
            Platform.UNKNOWN: "Inconnu",
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            Platform.LINUX: "🐧",
            Platform.MACOS: "🍎",
            Platform.WINDOWS: "🪟",
            Platform.UNKNOWN: "❓",
        }[self]

    @property
    def is_unix(self) -> bool:
        """Indique si c'est un système Unix-like (Linux ou macOS)."""
        return self in (Platform.LINUX, Platform.MACOS)


class PathType(str, Enum):
    """Type de répertoire dans la structure de l'application.

    CONFIG  : Configuration utilisateur (modifiable manuellement).
    DATA    : Données persistantes (BDD, cookies, etc.).
    CACHE   : Cache régénérable (peut être supprimé sans perte).
    LOGS    : Fichiers de logs.
    TEMP    : Fichiers temporaires court terme.
    RUNTIME : État d'exécution (PID, sockets, locks).
    """

    CONFIG = "config"
    DATA = "data"
    CACHE = "cache"
    LOGS = "logs"
    TEMP = "temp"
    RUNTIME = "runtime"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            PathType.CONFIG: "Configuration",
            PathType.DATA: "Données",
            PathType.CACHE: "Cache",
            PathType.LOGS: "Logs",
            PathType.TEMP: "Temporaire",
            PathType.RUNTIME: "Runtime",
        }[self]

    @property
    def is_persistent(self) -> bool:
        """Indique si le contenu doit être conservé entre les sessions."""
        return self in (PathType.CONFIG, PathType.DATA)

    @property
    def is_regenerable(self) -> bool:
        """Indique si le contenu peut être régénéré (safe to delete)."""
        return self in (PathType.CACHE, PathType.TEMP)


# ============================================================================
# HELPERS — Détection de plateforme
# ============================================================================


def detect_platform() -> Platform:
    """Détecte le système d'exploitation courant.

    Returns:
        Platform détectée.
    """
    system = platform.system().lower()
    if system == "linux":
        return Platform.LINUX
    if system == "darwin":
        return Platform.MACOS
    if system == "windows":
        return Platform.WINDOWS
    return Platform.UNKNOWN


def get_platform_info() -> dict[str, Any]:
    """Retourne des informations détaillées sur la plateforme.

    Returns:
        Dictionnaire avec system, release, machine, python_version, etc.
    """
    return {
        "platform": detect_platform().value,
        "platform_label": detect_platform().label,
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "user": os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
        "home": str(Path.home()),
        "cwd": str(Path.cwd()),
    }


# ============================================================================
# MODÈLES PYDANTIC — Configuration des chemins
# ============================================================================


class PathsConfig(BaseModel):
    """Configuration des chemins de l'application.

    Permet de personnaliser les répertoires standards. Les valeurs None
    utilisent les chemins par défaut (platformdirs ou variables d'env).

    Attributes:
        config_dir: Répertoire de configuration (override).
        data_dir: Répertoire de données (override).
        cache_dir: Répertoire de cache (override).
        logs_dir: Répertoire de logs (override).
        temp_dir: Répertoire temporaire (override).
        runtime_dir: Répertoire runtime (override).
        downloads_dir: Répertoire de téléchargement par défaut.
        library_dir: Répertoire de la bibliothèque locale.
        app_name: Nom de l'application (pour platformdirs).
        app_author: Auteur de l'application (pour platformdirs).
        create_dirs: Créer automatiquement les répertoires manquants.
        secure_permissions: Appliquer les permissions restrictives (Unix).
    """

    config_dir: Path | None = Field(
        default=None,
        description="Répertoire de configuration (override).",
    )
    data_dir: Path | None = Field(
        default=None,
        description="Répertoire de données (override).",
    )
    cache_dir: Path | None = Field(
        default=None,
        description="Répertoire de cache (override).",
    )
    logs_dir: Path | None = Field(
        default=None,
        description="Répertoire de logs (override).",
    )
    temp_dir: Path | None = Field(
        default=None,
        description="Répertoire temporaire (override).",
    )
    runtime_dir: Path | None = Field(
        default=None,
        description="Répertoire runtime (override).",
    )
    downloads_dir: Path | None = Field(
        default=None,
        description="Répertoire de téléchargement par défaut.",
    )
    library_dir: Path | None = Field(
        default=None,
        description="Répertoire de la bibliothèque locale.",
    )
    app_name: str = Field(
        default=_APP_NAME,
        min_length=1,
        max_length=64,
        description="Nom de l'application.",
    )
    app_author: str = Field(
        default=_APP_AUTHOR,
        min_length=1,
        max_length=64,
        description="Auteur de l'application.",
    )
    create_dirs: bool = Field(
        default=True,
        description="Créer automatiquement les répertoires manquants.",
    )
    secure_permissions: bool = Field(
        default=True,
        description="Appliquer les permissions restrictives (Unix).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @field_validator(
        "config_dir",
        "data_dir",
        "cache_dir",
        "logs_dir",
        "temp_dir",
        "runtime_dir",
        "downloads_dir",
        "library_dir",
        mode="before",
    )
    @classmethod
    def _validate_path(cls, v: Any) -> Path | None:
        """Valide et normalise un chemin optionnel."""
        if v is None:
            return None
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return None
            v = Path(v)
        if not isinstance(v, Path):
            raise InvalidPathError(v, "Doit être un chemin (str ou Path)")
        return v.expanduser().resolve()


# ============================================================================
# CLASSE PRINCIPALE — Paths
# ============================================================================


class Paths:
    """Gestionnaire centralisé des chemins de l'application.

    Fournit un accès unifié à tous les répertoires et fichiers standards
    de NexusDL, avec résolution automatique selon la plateforme et support
    des overrides (variables d'environnement, configuration).

    Lifecycle :
        >>> paths = Paths()
        >>> paths.initialize()
        >>> print(paths.config_dir)
        /home/user/.config/nexusdl
        >>> db_path = paths.get_data_file("library.db")

    Thread-safety :
        Cette classe est thread-safe après initialisation. Les chemins
        sont résolus une seule fois et mis en cache.
    """

    # Constantes de classe
    _DEFAULT_APP_NAME: ClassVar[str] = _APP_NAME
    _DEFAULT_APP_AUTHOR: ClassVar[str] = _APP_AUTHOR

    def __init__(
        self,
        *,
        config: PathsConfig | None = None,
        auto_initialize: bool = True,
    ) -> None:
        """Initialise le gestionnaire de chemins.

        Args:
            config: Configuration des chemins (défaut: valeurs standards).
            auto_initialize: Initialiser automatiquement (résoudre les chemins).
        """
        self._config = config or PathsConfig()
        self._platform = detect_platform()
        self._initialized: bool = False

        # Chemins résolus (peuplés par initialize())
        self._config_dir: Path | None = None
        self._data_dir: Path | None = None
        self._cache_dir: Path | None = None
        self._logs_dir: Path | None = None
        self._temp_dir: Path | None = None
        self._runtime_dir: Path | None = None
        self._downloads_dir: Path | None = None
        self._library_dir: Path | None = None

        # Timestamp d'initialisation
        self._initialized_at: datetime | None = None

        if auto_initialize:
            self.initialize()

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    def initialize(self) -> None:
        """Résout et valide tous les chemins.

        Cette méthode doit être appelée avant d'accéder aux chemins.
        Elle peut être appelée plusieurs fois (idempotente).

        Raises:
            PathCreationError: Si un répertoire ne peut être créé.
        """
        if self._initialized:
            return

        # Résoudre les chemins standards
        self._config_dir = self._resolve_dir(
            override=self._config.config_dir,
            env_var=_ENV_CONFIG_DIR,
            platformdirs_fn=self._get_platform_config_dir,
        )
        self._data_dir = self._resolve_dir(
            override=self._config.data_dir,
            env_var=_ENV_DATA_DIR,
            platformdirs_fn=self._get_platform_data_dir,
        )
        self._cache_dir = self._resolve_dir(
            override=self._config.cache_dir,
            env_var=_ENV_CACHE_DIR,
            platformdirs_fn=self._get_platform_cache_dir,
        )
        self._logs_dir = self._resolve_dir(
            override=self._config.logs_dir,
            env_var=_ENV_LOGS_DIR,
            platformdirs_fn=self._get_platform_logs_dir,
        )
        self._temp_dir = self._resolve_dir(
            override=self._config.temp_dir,
            env_var=_ENV_TEMP_DIR,
            platformdirs_fn=self._get_platform_temp_dir,
        )
        self._runtime_dir = self._resolve_dir(
            override=self._config.runtime_dir,
            env_var=_ENV_RUNTIME_DIR,
            platformdirs_fn=self._get_platform_runtime_dir,
        )

        # Répertoires utilisateur (avec valeurs par défaut spécifiques)
        self._downloads_dir = self._resolve_user_dir(
            override=self._config.downloads_dir,
            default=Path.home() / "Downloads" / "NexusDL",
        )
        self._library_dir = self._resolve_user_dir(
            override=self._config.library_dir,
            default=self._data_dir / "library" if self._data_dir else Path.home() / "NexusDL" / "Library",
        )

        # Créer les répertoires si demandé
        if self._config.create_dirs:
            self._ensure_all_dirs()

        self._initialized = True
        self._initialized_at = datetime.now(UTC)

        logger.debug(
            "Paths initialisés: platform={}, config={}, data={}, cache={}",
            self._platform.value,
            self._config_dir,
            self._data_dir,
            self._cache_dir,
        )

    # ------------------------------------------------------------------------
    # Propriétés — Répertoires standards
    # ------------------------------------------------------------------------

    @property
    def platform(self) -> Platform:
        """Plateforme détectée."""
        return self._platform

    @property
    def config(self) -> PathsConfig:
        """Configuration des chemins."""
        return self._config

    @property
    def is_initialized(self) -> bool:
        """Indique si les chemins ont été initialisés."""
        return self._initialized

    @property
    def config_dir(self) -> Path:
        """Répertoire de configuration.

        Linux   : ~/.config/nexusdl/
        macOS   : ~/Library/Application Support/nexusdl/
        Windows : %APPDATA%\\NexusDL\\

        Contient :
            - config.yaml
            - sites_overrides.yaml
            - .cookie_key
        """
        self._ensure_initialized()
        assert self._config_dir is not None
        return self._config_dir

    @property
    def data_dir(self) -> Path:
        """Répertoire de données persistantes.

        Linux   : ~/.local/share/nexusdl/
        macOS   : ~/Library/Application Support/nexusdl/
        Windows : %APPDATA%\\NexusDL\\

        Contient :
            - library.db
            - dedup.db
            - cookies.enc
            - library/ (fichiers téléchargés)
        """
        self._ensure_initialized()
        assert self._data_dir is not None
        return self._data_dir

    @property
    def cache_dir(self) -> Path:
        """Répertoire de cache (régénérable).

        Linux   : ~/.cache/nexusdl/
        macOS   : ~/Library/Caches/nexusdl/
        Windows : %LOCALAPPDATA%\\NexusDL\\Cache\\

        Peut être supprimé sans perte de données.
        """
        self._ensure_initialized()
        assert self._cache_dir is not None
        return self._cache_dir

    @property
    def logs_dir(self) -> Path:
        """Répertoire des fichiers de logs.

        Linux   : ~/.local/state/nexusdl/logs/
        macOS   : ~/Library/Logs/nexusdl/
        Windows : %APPDATA%\\NexusDL\\logs\\

        Contient :
            - nexusdl.log
            - nexusdl.error.log
        """
        self._ensure_initialized()
        assert self._logs_dir is not None
        return self._logs_dir

    @property
    def temp_dir(self) -> Path:
        """Répertoire temporaire (court terme).

        Utilise le répertoire temporaire système par défaut, avec un
        sous-répertoire nexusdl pour isolation.
        """
        self._ensure_initialized()
        assert self._temp_dir is not None
        return self._temp_dir

    @property
    def runtime_dir(self) -> Path:
        """Répertoire d'état d'exécution.

        Linux   : /run/user/<uid>/nexusdl/ ou ~/.local/state/nexusdl/runtime/
        macOS   : ~/Library/Application Support/nexusdl/runtime/
        Windows : %LOCALAPPDATA%\\NexusDL\\runtime\\

        Contient :
            - nexusdl.pid
            - sockets, locks
        """
        self._ensure_initialized()
        assert self._runtime_dir is not None
        return self._runtime_dir

    @property
    def downloads_dir(self) -> Path:
        """Répertoire de téléchargement par défaut.

        Défaut : ~/Downloads/NexusDL/
        """
        self._ensure_initialized()
        assert self._downloads_dir is not None
        return self._downloads_dir

    @property
    def library_dir(self) -> Path:
        """Répertoire de la bibliothèque locale.

        Défaut : <data_dir>/library/
        """
        self._ensure_initialized()
        assert self._library_dir is not None
        return self._library_dir

    # ------------------------------------------------------------------------
    # Propriétés — Fichiers standards
    # ------------------------------------------------------------------------

    @property
    def config_file(self) -> Path:
        """Chemin vers le fichier de configuration principal."""
        return self.config_dir / _CONFIG_FILENAME

    @property
    def sites_overrides_file(self) -> Path:
        """Chemin vers le fichier d'overrides des sites."""
        return self.config_dir / _SITES_OVERRIDES_FILENAME

    @property
    def cookie_key_file(self) -> Path:
        """Chemin vers le fichier de clé de chiffrement des cookies."""
        return self.config_dir / _COOKIE_KEY_FILENAME

    @property
    def cookies_file(self) -> Path:
        """Chemin vers le fichier de cookies chiffrés."""
        return self.data_dir / _COOKIES_FILENAME

    @property
    def library_db_file(self) -> Path:
        """Chemin vers la base de données de la bibliothèque."""
        return self.data_dir / _LIBRARY_DB_FILENAME

    @property
    def dedup_db_file(self) -> Path:
        """Chemin vers la base de données de déduplication."""
        return self.data_dir / _DEDUP_DB_FILENAME

    @property
    def log_file(self) -> Path:
        """Chemin vers le fichier de log principal."""
        return self.logs_dir / _LOG_FILENAME

    @property
    def error_log_file(self) -> Path:
        """Chemin vers le fichier de log d'erreurs."""
        return self.logs_dir / _ERROR_LOG_FILENAME

    @property
    def pid_file(self) -> Path:
        """Chemin vers le fichier PID."""
        return self.runtime_dir / _PID_FILENAME

    # ------------------------------------------------------------------------
    # API publique — Accès aux chemins
    # ------------------------------------------------------------------------

    def get_config_file(self, filename: str) -> Path:
        """Retourne le chemin d'un fichier dans le répertoire de configuration.

        Args:
            filename: Nom du fichier.

        Returns:
            Chemin absolu vers le fichier.
        """
        self._ensure_initialized()
        return self.config_dir / filename

    def get_data_file(self, filename: str) -> Path:
        """Retourne le chemin d'un fichier dans le répertoire de données.

        Args:
            filename: Nom du fichier.

        Returns:
            Chemin absolu vers le fichier.
        """
        self._ensure_initialized()
        return self.data_dir / filename

    def get_cache_file(self, filename: str) -> Path:
        """Retourne le chemin d'un fichier dans le répertoire de cache.

        Args:
            filename: Nom du fichier.

        Returns:
            Chemin absolu vers le fichier.
        """
        self._ensure_initialized()
        return self.cache_dir / filename

    def get_log_file(self, filename: str) -> Path:
        """Retourne le chemin d'un fichier dans le répertoire de logs.

        Args:
            filename: Nom du fichier.

        Returns:
            Chemin absolu vers le fichier.
        """
        self._ensure_initialized()
        return self.logs_dir / filename

    def get_subdir(self, path_type: PathType, *subpath: str) -> Path:
        """Retourne le chemin d'un sous-répertoire dans un type donné.

        Args:
            path_type: Type de répertoire de base.
            *subpath: Composants du sous-chemin.

        Returns:
            Chemin absolu vers le sous-répertoire.

        Example:
            >>> paths.get_subdir(PathType.DATA, "mangas", "one_piece")
            /home/user/.local/share/nexusdl/mangas/one_piece
        """
        self._ensure_initialized()

        base_dirs = {
            PathType.CONFIG: self.config_dir,
            PathType.DATA: self.data_dir,
            PathType.CACHE: self.cache_dir,
            PathType.LOGS: self.logs_dir,
            PathType.TEMP: self.temp_dir,
            PathType.RUNTIME: self.runtime_dir,
        }

        base = base_dirs[path_type]
        result = base.joinpath(*subpath) if subpath else base

        # Créer le répertoire si nécessaire
        if self._config.create_dirs:
            self.ensure_dir(result)

        return result

    # ------------------------------------------------------------------------
    # API publique — Fichiers temporaires
    # ------------------------------------------------------------------------

    def get_temp_file(
        self,
        *,
        prefix: str = "nexusdl_",
        suffix: str = "",
        directory: Path | None = None,
    ) -> Path:
        """Génère un chemin de fichier temporaire unique.

        Le fichier n'est PAS créé — seul le chemin est généré.
        L'appelant est responsable de la création et du nettoyage.

        Args:
            prefix: Préfixe du nom de fichier.
            suffix: Suffixe (extension) du fichier.
            directory: Répertoire de destination (défaut: temp_dir).

        Returns:
            Chemin unique vers un fichier temporaire.
        """
        self._ensure_initialized()

        target_dir = directory or self.temp_dir
        unique_id = uuid.uuid4().hex[:12]
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"{prefix}{timestamp}_{unique_id}{suffix}"

        return target_dir / filename

    def get_temp_dir(
        self,
        *,
        prefix: str = "nexusdl_",
        parent: Path | None = None,
    ) -> Path:
        """Génère un chemin de répertoire temporaire unique.

        Le répertoire n'est PAS créé — seul le chemin est généré.

        Args:
            prefix: Préfixe du nom de répertoire.
            parent: Répertoire parent (défaut: temp_dir).

        Returns:
            Chemin unique vers un répertoire temporaire.
        """
        self._ensure_initialized()

        target_dir = parent or self.temp_dir
        unique_id = uuid.uuid4().hex[:12]
        dirname = f"{prefix}{unique_id}"

        return target_dir / dirname

    # ------------------------------------------------------------------------
    # API publique — Création et validation
    # ------------------------------------------------------------------------

    def ensure_dir(self, path: Path, *, secure: bool = False) -> Path:
        """Crée un répertoire s'il n'existe pas.

        Args:
            path: Chemin du répertoire à créer.
            secure: Si True, applique les permissions restrictives (0o700).

        Returns:
            Le chemin du répertoire (créé ou existant).

        Raises:
            PathCreationError: Si le répertoire ne peut être créé.
        """
        try:
            path.mkdir(parents=True, exist_ok=True)

            # Appliquer les permissions si demandé et sur Unix
            if secure and self._platform.is_unix and self._config.secure_permissions:
                try:
                    os.chmod(path, 0o700)
                except (OSError, NotImplementedError):
                    pass

            return path

        except OSError as e:
            raise PathCreationError(path, str(e)) from e

    def ensure_parent_dir(self, file_path: Path) -> Path:
        """Crée le répertoire parent d'un fichier s'il n'existe pas.

        Args:
            file_path: Chemin du fichier.

        Returns:
            Le chemin du fichier (inchangé).
        """
        return self.ensure_dir(file_path.parent)

    def is_accessible(self, path: Path, *, write: bool = False) -> bool:
        """Vérifie si un chemin est accessible.

        Args:
            path: Chemin à vérifier.
            write: Si True, vérifie aussi l'accès en écriture.

        Returns:
            True si le chemin est accessible.
        """
        if not path.exists():
            return False

        if not os.access(path, os.R_OK):
            return False

        if write and not os.access(path, os.W_OK):
            return False

        return True

    # ------------------------------------------------------------------------
    # API publique — Nettoyage
    # ------------------------------------------------------------------------

    def clean_cache(self, *, max_age_days: int = 30) -> int:
        """Nettoie les fichiers de cache anciens.

        Args:
            max_age_days: Âge maximum en jours.

        Returns:
            Nombre de fichiers supprimés.
        """
        self._ensure_initialized()
        return self._clean_directory(self.cache_dir, max_age_days)

    def clean_temp(self, *, max_age_hours: int = 24) -> int:
        """Nettoie les fichiers temporaires anciens.

        Args:
            max_age_hours: Âge maximum en heures.

        Returns:
            Nombre de fichiers supprimés.
        """
        self._ensure_initialized()
        max_age_days = max_age_hours / 24.0
        return self._clean_directory(self.temp_dir, max_age_days)

    def _clean_directory(self, directory: Path, max_age_days: float) -> int:
        """Nettoie les fichiers anciens d'un répertoire.

        Args:
            directory: Répertoire à nettoyer.
            max_age_days: Âge maximum en jours.

        Returns:
            Nombre de fichiers supprimés.
        """
        if not directory.exists():
            return 0

        cutoff = datetime.now(UTC).timestamp() - (max_age_days * 86_400)
        removed = 0

        try:
            for item in directory.rglob("*"):
                if not item.is_file():
                    continue

                try:
                    mtime = item.stat().st_mtime
                    if mtime < cutoff:
                        item.unlink()
                        removed += 1
                except OSError:
                    pass

        except OSError as e:
            logger.warning("Erreur lors du nettoyage de {}: {}", directory, e)

        if removed > 0:
            logger.debug(
                "Nettoyage de {}: {} fichiers supprimés",
                directory,
                removed,
            )

        return removed

    # ------------------------------------------------------------------------
    # API publique — Informations
    # ------------------------------------------------------------------------

    def get_summary(self) -> dict[str, Any]:
        """Retourne un résumé de tous les chemins.

        Returns:
            Dictionnaire avec tous les chemins et métadonnées.
        """
        self._ensure_initialized()

        return {
            "platform": {
                "value": self._platform.value,
                "label": self._platform.label,
                "icon": self._platform.icon,
                "is_unix": self._platform.is_unix,
            },
            "directories": {
                "config": str(self.config_dir),
                "data": str(self.data_dir),
                "cache": str(self.cache_dir),
                "logs": str(self.logs_dir),
                "temp": str(self.temp_dir),
                "runtime": str(self.runtime_dir),
                "downloads": str(self.downloads_dir),
                "library": str(self.library_dir),
            },
            "files": {
                "config": str(self.config_file),
                "sites_overrides": str(self.sites_overrides_file),
                "cookie_key": str(self.cookie_key_file),
                "cookies": str(self.cookies_file),
                "library_db": str(self.library_db_file),
                "dedup_db": str(self.dedup_db_file),
                "log": str(self.log_file),
                "error_log": str(self.error_log_file),
                "pid": str(self.pid_file),
            },
            "initialized_at": (
                self._initialized_at.isoformat() if self._initialized_at else None
            ),
            "platform_info": get_platform_info(),
        }

    def get_disk_usage(self) -> dict[str, int]:
        """Calcule l'utilisation disque par répertoire.

        Returns:
            Dictionnaire {path_str: size_bytes}.
        """
        self._ensure_initialized()

        usage: dict[str, int] = {}

        for name, path in [
            ("config", self.config_dir),
            ("data", self.data_dir),
            ("cache", self.cache_dir),
            ("logs", self.logs_dir),
            ("temp", self.temp_dir),
            ("runtime", self.runtime_dir),
        ]:
            usage[name] = self._calculate_dir_size(path)

        return usage

    def _calculate_dir_size(self, directory: Path) -> int:
        """Calcule la taille totale d'un répertoire.

        Args:
            directory: Répertoire à mesurer.

        Returns:
            Taille totale en bytes.
        """
        if not directory.exists():
            return 0

        total = 0
        try:
            for item in directory.rglob("*"):
                if item.is_file():
                    try:
                        total += item.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass

        return total

    # ------------------------------------------------------------------------
    # Méthodes internes — Résolution des chemins
    # ------------------------------------------------------------------------

    def _resolve_dir(
        self,
        *,
        override: Path | None,
        env_var: str,
        platformdirs_fn: Any,
    ) -> Path:
        """Résout un répertoire selon la priorité : override > env > platformdirs.

        Args:
            override: Chemin d'override (config).
            env_var: Nom de la variable d'environnement.
            platformdirs_fn: Fonction platformdirs pour le chemin par défaut.

        Returns:
            Chemin résolu et normalisé.
        """
        # 1. Override explicite
        if override is not None:
            return override.expanduser().resolve()

        # 2. Variable d'environnement
        env_value = os.environ.get(env_var)
        if env_value:
            try:
                return Path(env_value).expanduser().resolve()
            except Exception:
                pass

        # 3. platformdirs
        try:
            result = platformdirs_fn()
            return Path(result).expanduser().resolve()
        except Exception:
            # Fallback ultime : sous-répertoire de home
            return (Path.home() / f".{self._config.app_name.lower()}").resolve()

    def _resolve_user_dir(
        self,
        *,
        override: Path | None,
        default: Path,
    ) -> Path:
        """Résout un répertoire utilisateur (downloads, library).

        Args:
            override: Chemin d'override.
            default: Chemin par défaut.

        Returns:
            Chemin résolu.
        """
        if override is not None:
            return override.expanduser().resolve()
        return default.expanduser().resolve()

    # ------------------------------------------------------------------------
    # Méthodes internes — platformdirs wrappers
    # ------------------------------------------------------------------------

    def _get_platform_config_dir(self) -> str:
        """Retourne le répertoire de configuration via platformdirs."""
        try:
            import platformdirs
            return platformdirs.user_config_dir(
                self._config.app_name,
                self._config.app_author,
            )
        except ImportError:
            return self._fallback_config_dir()

    def _get_platform_data_dir(self) -> str:
        """Retourne le répertoire de données via platformdirs."""
        try:
            import platformdirs
            return platformdirs.user_data_dir(
                self._config.app_name,
                self._config.app_author,
            )
        except ImportError:
            return self._fallback_data_dir()

    def _get_platform_cache_dir(self) -> str:
        """Retourne le répertoire de cache via platformdirs."""
        try:
            import platformdirs
            return platformdirs.user_cache_dir(
                self._config.app_name,
                self._config.app_author,
            )
        except ImportError:
            return self._fallback_cache_dir()

    def _get_platform_logs_dir(self) -> str:
        """Retourne le répertoire de logs via platformdirs."""
        try:
            import platformdirs
            # platformdirs n'a pas de logs_dir dédié, on utilise state_dir
            state_dir = platformdirs.user_state_dir(
                self._config.app_name,
                self._config.app_author,
            )
            return str(Path(state_dir) / "logs")
        except ImportError:
            return self._fallback_logs_dir()

    def _get_platform_temp_dir(self) -> str:
        """Retourne le répertoire temporaire."""
        # Utiliser le temp système avec un sous-répertoire nexusdl
        base_temp = Path(tempfile.gettempdir())
        return str(base_temp / self._config.app_name.lower())

    def _get_platform_runtime_dir(self) -> str:
        """Retourne le répertoire runtime."""
        try:
            import platformdirs
            # Utiliser user_runtime_dir si disponible
            runtime_dir = platformdirs.user_runtime_dir(
                self._config.app_name,
                self._config.app_author,
            )
            return runtime_dir
        except (ImportError, AttributeError):
            # Fallback : sous-répertoire de state
            return self._fallback_runtime_dir()

    # ------------------------------------------------------------------------
    # Méthodes internes — Fallbacks (sans platformdirs)
    # ------------------------------------------------------------------------

    def _fallback_config_dir(self) -> str:
        """Fallback pour config_dir si platformdirs indisponible."""
        home = Path.home()
        if self._platform == Platform.WINDOWS:
            appdata = os.environ.get("APPDATA", str(home / "AppData" / "Roaming"))
            return str(Path(appdata) / self._config.app_name)
        if self._platform == Platform.MACOS:
            return str(home / "Library" / "Application Support" / self._config.app_name)
        # Linux / autre : XDG
        xdg_config = os.environ.get("XDG_CONFIG_HOME", str(home / ".config"))
        return str(Path(xdg_config) / self._config.app_name.lower())

    def _fallback_data_dir(self) -> str:
        """Fallback pour data_dir si platformdirs indisponible."""
        home = Path.home()
        if self._platform == Platform.WINDOWS:
            appdata = os.environ.get("APPDATA", str(home / "AppData" / "Roaming"))
            return str(Path(appdata) / self._config.app_name)
        if self._platform == Platform.MACOS:
            return str(home / "Library" / "Application Support" / self._config.app_name)
        # Linux : XDG
        xdg_data = os.environ.get("XDG_DATA_HOME", str(home / ".local" / "share"))
        return str(Path(xdg_data) / self._config.app_name.lower())

    def _fallback_cache_dir(self) -> str:
        """Fallback pour cache_dir si platformdirs indisponible."""
        home = Path.home()
        if self._platform == Platform.WINDOWS:
            local_appdata = os.environ.get(
                "LOCALAPPDATA", str(home / "AppData" / "Local")
            )
            return str(Path(local_appdata) / self._config.app_name / "Cache")
        if self._platform == Platform.MACOS:
            return str(home / "Library" / "Caches" / self._config.app_name)
        # Linux : XDG
        xdg_cache = os.environ.get("XDG_CACHE_HOME", str(home / ".cache"))
        return str(Path(xdg_cache) / self._config.app_name.lower())

    def _fallback_logs_dir(self) -> str:
        """Fallback pour logs_dir si platformdirs indisponible."""
        home = Path.home()
        if self._platform == Platform.WINDOWS:
            appdata = os.environ.get("APPDATA", str(home / "AppData" / "Roaming"))
            return str(Path(appdata) / self._config.app_name / "logs")
        if self._platform == Platform.MACOS:
            return str(home / "Library" / "Logs" / self._config.app_name)
        # Linux : XDG state
        xdg_state = os.environ.get(
            "XDG_STATE_HOME", str(home / ".local" / "state")
        )
        return str(Path(xdg_state) / self._config.app_name.lower() / "logs")

    def _fallback_runtime_dir(self) -> str:
        """Fallback pour runtime_dir si platformdirs indisponible."""
        home = Path.home()
        if self._platform == Platform.LINUX:
            # XDG runtime dir (souvent /run/user/<uid>/)
            xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
            if xdg_runtime:
                return str(Path(xdg_runtime) / self._config.app_name.lower())
            # Fallback : state dir
            xdg_state = os.environ.get(
                "XDG_STATE_HOME", str(home / ".local" / "state")
            )
            return str(Path(xdg_state) / self._config.app_name.lower() / "runtime")
        if self._platform == Platform.WINDOWS:
            local_appdata = os.environ.get(
                "LOCALAPPDATA", str(home / "AppData" / "Local")
            )
            return str(Path(local_appdata) / self._config.app_name / "runtime")
        # macOS
        return str(home / "Library" / "Application Support" / self._config.app_name / "runtime")

    # ------------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # ------------------------------------------------------------------------

    def _ensure_all_dirs(self) -> None:
        """Crée tous les répertoires standards."""
        dirs_to_create = [
            (self._config_dir, False),
            (self._data_dir, False),
            (self._cache_dir, False),
            (self._logs_dir, False),
            (self._temp_dir, False),
            (self._runtime_dir, True),  # secure
            (self._downloads_dir, False),
            (self._library_dir, False),
        ]

        for path, secure in dirs_to_create:
            if path is not None:
                try:
                    self.ensure_dir(path, secure=secure)
                except PathCreationError as e:
                    logger.warning(
                        "Impossible de créer le répertoire {}: {}",
                        path,
                        e,
                    )

    def _ensure_initialized(self) -> None:
        """Vérifie que les chemins ont été initialisés."""
        if not self._initialized:
            raise PathsError(
                "Paths must be initialized before use. Call paths.initialize()"
            )

    def __repr__(self) -> str:
        status = "initialized" if self._initialized else "not initialized"
        return (
            f"<Paths platform={self._platform.value} "
            f"status={status} "
            f"app={self._config.app_name}>"
        )


# ============================================================================
# INSTANCE GLOBALE
# ============================================================================


# Instance globale par défaut (initialisée au premier import)
# Peut être remplacée par une instance custom via set_paths()
_paths: Paths | None = None


def get_paths() -> Paths:
    """Retourne l'instance globale de Paths.

    Crée l'instance si elle n'existe pas encore.

    Returns:
        Instance globale de Paths.
    """
    global _paths
    if _paths is None:
        _paths = Paths()
    return _paths


def set_paths(paths: Paths) -> None:
    """Remplace l'instance globale de Paths.

    Utile pour les tests ou pour utiliser une configuration custom.

    Args:
        paths: Nouvelle instance de Paths.
    """
    global _paths
    _paths = paths


def reset_paths() -> None:
    """Réinitialise l'instance globale de Paths.

    La prochaine appel à get_paths() créera une nouvelle instance.
    """
    global _paths
    _paths = None


# Alias pratique : l'instance globale accessible directement
# Usage : from nexusdl.core.paths import paths
paths: Paths = property(lambda self: get_paths())  # type: ignore[assignment]


# ============================================================================
# HELPERS PUBLICS — Fonctions utilitaires
# ============================================================================


def ensure_app_dirs() -> Paths:
    """Crée tous les répertoires de l'application et retourne les paths.

    Fonction utilitaire pour initialiser rapidement l'environnement.

    Returns:
        Instance de Paths initialisée.
    """
    p = get_paths()
    if not p.is_initialized:
        p.initialize()
    return p


def get_app_info() -> dict[str, Any]:
    """Retourne des informations sur l'application et ses chemins.

    Returns:
        Dictionnaire avec nom, version, plateforme, chemins, etc.
    """
    p = get_paths()
    return {
        "app_name": _APP_NAME,
        "app_author": _APP_AUTHOR,
        "app_version": _APP_VERSION,
        "platform": p.platform.value,
        "platform_label": p.platform.label,
        "paths": p.get_summary() if p.is_initialized else None,
    }


def validate_paths_config(config_dict: dict[str, Any]) -> PathsConfig:
    """Valide un dictionnaire de configuration de chemins.

    Args:
        config_dict: Dictionnaire de configuration.

    Returns:
        Instance de PathsConfig validée.

    Raises:
        InvalidPathError: Si la configuration est invalide.
    """
    try:
        return PathsConfig.model_validate(config_dict)
    except PydanticValidationError as e:
        raise InvalidPathError(config_dict, str(e)) from e


def expand_path(path: str | Path) -> Path:
    """Étend et résout un chemin (supporte ~, variables d'env, etc.).

    Args:
        path: Chemin à étendre.

    Returns:
        Chemin absolu et résolu.
    """
    if isinstance(path, str):
        path = Path(path)
    return path.expanduser().resolve()


def is_path_within(path: Path, parent: Path) -> bool:
    """Vérifie si un chemin est contenu dans un répertoire parent.

    Utile pour la sécurité (empêcher les path traversal attacks).

    Args:
        path: Chemin à vérifier.
        parent: Répertoire parent attendu.

    Returns:
        True si path est dans parent.
    """
    try:
        path_resolved = path.resolve()
        parent_resolved = parent.resolve()
        path_resolved.relative_to(parent_resolved)
        return True
    except (ValueError, OSError):
        return False


def safe_join(base: Path, *parts: str) -> Path:
    """Joint des composants de chemin de manière sécurisée.

    Empêche les path traversal attacks en vérifiant que le résultat
    est bien dans le répertoire de base.

    Args:
        base: Répertoire de base.
        *parts: Composants à joindre.

    Returns:
        Chemin joint et validé.

    Raises:
        InvalidPathError: Si le résultat sort du répertoire de base.
    """
    result = base.joinpath(*parts).resolve()
    if not is_path_within(result, base):
        raise InvalidPathError(
            str(result),
            f"Path traversal détecté : {result} n'est pas dans {base}",
        )
    return result


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "_APP_NAME",
    "_APP_AUTHOR",
    "_APP_VERSION",
    # Exceptions
    "PathsError",
    "PathCreationError",
    "PathAccessError",
    "InvalidPathError",
    # Enums
    "Platform",
    "PathType",
    # Modèles
    "PathsConfig",
    # Classe principale
    "Paths",
    # Instance globale
    "paths",
    "get_paths",
    "set_paths",
    "reset_paths",
    # Helpers — Détection
    "detect_platform",
    "get_platform_info",
    # Helpers — Utilitaires
    "ensure_app_dirs",
    "get_app_info",
    "validate_paths_config",
    "expand_path",
    "is_path_within",
    "safe_join",
]
