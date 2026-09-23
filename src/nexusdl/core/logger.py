"""Système de logging centralisé basé sur Loguru.

Ce module fournit une configuration complète et centralisée du système de
logging pour NexusDL, basée sur la librairie Loguru. Il remplace le logger
par défaut de Python et offre des fonctionnalités avancées :

    - Rotation automatique des logs (taille + temps)
    - Compression gzip des anciens logs
    - Niveaux de logs configurables par module
    - Formatage structuré (texte coloré, JSON, ou riche)
    - Fichiers séparés : logs généraux + logs d'erreurs
    - Intercepteur des exceptions non gérées
    - Contexte enrichi (version, plateforme, PID, thread)
    - Filtrage intelligent (exclusion de logs bruyants)
    - Métriques de logging (compteurs par niveau)
    - Mode quiet/verbose pour CLI
    - Performance optimisée (lazy evaluation)

**Architecture des handlers** :
    1. Console (stderr) :
       - Format coloré pour TTY, format simple pour pipe
       - Niveau configurable (défaut: INFO)
       - Format riche si `rich` disponible
    
    2. Fichier principal (nexusdl.log) :
       - Rotation : 10 MB ou 1 jour
       - Rétention : 7 jours
       - Compression : gzip optionnel
       - Niveau : DEBUG (tout capturer)
       - Format : texte structuré
    
    3. Fichier d'erreurs (nexusdl.error.log) :
       - Filtre : ERROR et au-dessus uniquement
       - Rotation : 5 MB ou 1 jour
       - Rétention : 30 jours
       - Format : texte avec traceback complet
    
    4. Fichier JSON (optionnel, nexusdl.jsonl) :
       - Format : JSON Lines (une entrée par ligne)
       - Pour intégration avec ELK, Datadog, etc.
       - Rotation : 50 MB ou 1 jour
       - Rétention : 14 jours

**Filtrage par module** :
    Chaque module peut avoir son propre niveau de log via la configuration :
        logging.module_levels:
            core.downloader.worker: DEBUG
            core.session.http_session: INFO
            parsers.en.mangadex: WARNING
    
    Les logs avec `logger.bind(module="xxx")` sont filtrés en conséquence.

**Variables d'environnement** (override de la configuration) :
    NEXUSDL_LOG_LEVEL      : Niveau global (DEBUG, INFO, WARNING, ERROR)
    NEXUSDL_LOG_FILE       : Chemin du fichier de log principal
    NEXUSDL_LOG_DIR        : Répertoire des logs
    NEXUSDL_LOG_FORMAT     : Format (text, json, rich)
    NEXUSDL_LOG_NO_COLOR   : Désactiver les couleurs (1 = oui)
    NEXUSDL_LOG_QUIET      : Mode silencieux (1 = ERROR uniquement)
    NEXUSDL_LOG_VERBOSE    : Mode verbeux (1 = DEBUG partout)

Exemple d'utilisation :
    >>> from nexusdl.core.logger import setup_logging, get_logger
    >>>
    >>> # Configuration au démarrage de l'application
    >>> setup_logging(
    ...     level="INFO",
    ...     log_dir=paths.logs_dir,
    ...     format="rich",
    ...     rotation="10 MB",
    ... )
    >>>
    >>> # Obtenir un logger pour un module
    >>> logger = get_logger("core.downloader.worker")
    >>> logger.info("Tâche démarrée")
    >>> logger.bind(task_id="abc123").debug("Détails internes")
    >>>
    >>> # Logger avec contexte structuré
    >>> logger.bind(
    ...     module="worker",
    ...     task_id="abc123",
    ...     manga_id="one_piece",
    ... ).info("Téléchargement terminé", pages=42, bytes=1024000)
    >>>
    >>> # Statistiques de logging
    >>> from nexusdl.core.logger import get_logging_stats
    >>> stats = get_logging_stats()
    >>> print(f"Logs émis: {stats.total_logs}")
    >>> print(f"Erreurs: {stats.error_count}")

Intégration :
    - Tous les modules utilisent `logger.bind(module="xxx")`
    - La configuration est chargée depuis `config.yaml` (section `logging`)
    - Les interfaces (CLI/Web/GUI) appellent `setup_logging()` au démarrage
    - Les exceptions non gérées sont capturées et loguées automatiquement
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import threading
import time
import traceback
from collections import defaultdict
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Final, Self

from loguru import logger as _loguru_logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

from nexusdl.core.exceptions import NexusDLError

if TYPE_CHECKING:
    from loguru import Logger


# ============================================================================
# CONSTANTES — Identifiants et valeurs par défaut
# ============================================================================


# Niveaux de log personnalisés (ajoutés à Loguru)
_TRACE_LEVEL: Final[int] = 5
_SUCCESS_LEVEL: Final[int] = 25

# Variables d'environnement pour override
_ENV_LOG_LEVEL: Final[str] = "NEXUSDL_LOG_LEVEL"
_ENV_LOG_FILE: Final[str] = "NEXUSDL_LOG_FILE"
_ENV_LOG_DIR: Final[str] = "NEXUSDL_LOG_DIR"
_ENV_LOG_FORMAT: Final[str] = "NEXUSDL_LOG_FORMAT"
_ENV_LOG_NO_COLOR: Final[str] = "NEXUSDL_LOG_NO_COLOR"
_ENV_LOG_QUIET: Final[str] = "NEXUSDL_LOG_QUIET"
_ENV_LOG_VERBOSE: Final[str] = "NEXUSDL_LOG_VERBOSE"
_ENV_LOG_JSON: Final[str] = "NEXUSDL_LOG_JSON"

# Noms de fichiers par défaut
_LOG_FILENAME: Final[str] = "nexusdl.log"
_ERROR_LOG_FILENAME: Final[str] = "nexusdl.error.log"
_JSON_LOG_FILENAME: Final[str] = "nexusdl.jsonl"

# Paramètres de rotation par défaut
_DEFAULT_ROTATION: Final[str] = "10 MB"
_DEFAULT_RETENTION: Final[str] = "7 days"
_DEFAULT_COMPRESSION: Final[str] = "gz"
_ERROR_ROTATION: Final[str] = "5 MB"
_ERROR_RETENTION: Final[str] = "30 days"
_JSON_ROTATION: Final[str] = "50 MB"
_JSON_RETENTION: Final[str] = "14 days"

# Version de l'application (injectée dans le contexte)
_APP_VERSION: Final[str] = "0.1.0"


# ============================================================================
# EXCEPTIONS
# ============================================================================


class LoggerError(NexusDLError):
    """Exception de base pour les erreurs du système de logging."""


class LoggerNotInitializedError(LoggerError):
    """Exception levée lorsqu'on utilise le logger avant initialisation."""

    def __init__(self) -> None:
        super().__init__(
            "Logger must be initialized before use. Call setup_logging() first."
        )


class InvalidLogLevelError(LoggerError):
    """Exception levée lorsqu'un niveau de log est invalide."""

    def __init__(self, level: str) -> None:
        super().__init__(
            f"Niveau de log invalide: {level}. "
            f"Valeurs acceptées: TRACE, DEBUG, INFO, SUCCESS, WARNING, ERROR, CRITICAL"
        )
        self.level = level


class LogDirectoryError(LoggerError):
    """Exception levée lorsqu'un répertoire de logs est inaccessible."""

    def __init__(self, path: Path, reason: str = "") -> None:
        msg = f"Répertoire de logs inaccessible: {path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.path = path
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class LogLevel(str, Enum):
    """Niveaux de log disponibles.

    Les valeurs correspondent aux niveaux standards de Loguru +
    deux niveaux personnalisés (TRACE, SUCCESS).
    """

    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    @property
    def numeric_level(self) -> int:
        """Valeur numérique du niveau (compatible logging stdlib)."""
        return {
            LogLevel.TRACE: _TRACE_LEVEL,
            LogLevel.DEBUG: 10,
            LogLevel.INFO: 20,
            LogLevel.SUCCESS: _SUCCESS_LEVEL,
            LogLevel.WARNING: 30,
            LogLevel.ERROR: 40,
            LogLevel.CRITICAL: 50,
        }[self]

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            LogLevel.TRACE: "Trace",
            LogLevel.DEBUG: "Debug",
            LogLevel.INFO: "Info",
            LogLevel.SUCCESS: "Success",
            LogLevel.WARNING: "Warning",
            LogLevel.ERROR: "Error",
            LogLevel.CRITICAL: "Critical",
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode pour affichage."""
        return {
            LogLevel.TRACE: "🔍",
            LogLevel.DEBUG: "🐛",
            LogLevel.INFO: "ℹ️",
            LogLevel.SUCCESS: "✅",
            LogLevel.WARNING: "⚠️",
            LogLevel.ERROR: "❌",
            LogLevel.CRITICAL: "💥",
        }[self]

    @property
    def color(self) -> str:
        """Code couleur ANSI pour affichage terminal."""
        return {
            LogLevel.TRACE: "cyan",
            LogLevel.DEBUG: "blue",
            LogLevel.INFO: "green",
            LogLevel.SUCCESS: "bright_green",
            LogLevel.WARNING: "yellow",
            LogLevel.ERROR: "red",
            LogLevel.CRITICAL: "bright_red",
        }[self]


class LogFormat(str, Enum):
    """Format de sortie des logs.

    TEXT  : Format texte structuré (défaut, lisible).
    RICH  : Format enrichi avec couleurs et mise en forme (nécessite `rich`).
    JSON  : Format JSON Lines (une entrée par ligne, pour monitoring).
    SIMPLE: Format minimaliste (timestamp + message).
    """

    TEXT = "text"
    RICH = "rich"
    JSON = "json"
    SIMPLE = "simple"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            LogFormat.TEXT: "Texte structuré",
            LogFormat.RICH: "Riche (couleurs)",
            LogFormat.JSON: "JSON Lines",
            LogFormat.SIMPLE: "Simple",
        }[self]

    @property
    def requires_rich(self) -> bool:
        """Indique si le format nécessite la librairie `rich`."""
        return self == LogFormat.RICH


# ============================================================================
# MODÈLES PYDANTIC — Configuration
# ============================================================================


class ModuleLogLevel(BaseModel):
    """Configuration du niveau de log pour un module spécifique.

    Attributes:
        module: Nom du module (ex: "core.downloader.worker").
        level: Niveau de log pour ce module.
    """

    module: str = Field(..., min_length=1, description="Nom du module.")
    level: LogLevel = Field(..., description="Niveau de log.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class LoggerConfig(BaseModel):
    """Configuration complète du système de logging.

    Attributes:
        level: Niveau de log global (défaut: INFO).
        format: Format de sortie (défaut: TEXT).
        log_dir: Répertoire des fichiers de logs.
        log_file: Nom du fichier de log principal.
        error_log_file: Nom du fichier de log d'erreurs.
        enable_console: Activer le handler console.
        enable_file: Activer le handler fichier principal.
        enable_error_file: Activer le handler fichier d'erreurs.
        enable_json_file: Activer le handler fichier JSON.
        rotation: Taille max d'un fichier avant rotation.
        retention: Durée de rétention des anciens logs.
        compression: Compression des anciens logs (gz, bz2, xz, None).
        colorize: Activer les couleurs dans la console.
        diagnose: Activer le diagnostic des variables dans les tracebacks.
        backtrace: Activer le backtrace complet dans les tracebacks.
        catch_exceptions: Intercepter les exceptions non gérées.
        module_levels: Niveaux de log par module (overrides).
        exclude_modules: Modules à exclure du logging.
        quiet: Mode silencieux (ERROR uniquement).
        verbose: Mode verbeux (DEBUG partout).
        serialize_json: Sérialiser les logs en JSON (même pour TEXT).
        app_name: Nom de l'application (pour contexte).
        app_version: Version de l'application (pour contexte).
    """

    level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Niveau de log global.",
    )
    format: LogFormat = Field(
        default=LogFormat.TEXT,
        description="Format de sortie.",
    )
    log_dir: Path | None = Field(
        default=None,
        description="Répertoire des fichiers de logs.",
    )
    log_file: str = Field(
        default=_LOG_FILENAME,
        description="Nom du fichier de log principal.",
    )
    error_log_file: str = Field(
        default=_ERROR_LOG_FILENAME,
        description="Nom du fichier de log d'erreurs.",
    )
    json_log_file: str = Field(
        default=_JSON_LOG_FILENAME,
        description="Nom du fichier JSON.",
    )
    enable_console: bool = Field(
        default=True,
        description="Activer le handler console.",
    )
    enable_file: bool = Field(
        default=True,
        description="Activer le handler fichier principal.",
    )
    enable_error_file: bool = Field(
        default=True,
        description="Activer le handler fichier d'erreurs.",
    )
    enable_json_file: bool = Field(
        default=False,
        description="Activer le handler fichier JSON.",
    )
    rotation: str = Field(
        default=_DEFAULT_ROTATION,
        description="Taille max avant rotation (ex: '10 MB', '1 day').",
    )
    retention: str = Field(
        default=_DEFAULT_RETENTION,
        description="Durée de rétention (ex: '7 days', '1 month').",
    )
    compression: str | None = Field(
        default=_DEFAULT_COMPRESSION,
        description="Compression des anciens logs (gz, bz2, xz, None).",
    )
    colorize: bool = Field(
        default=True,
        description="Activer les couleurs dans la console.",
    )
    diagnose: bool = Field(
        default=False,
        description="Activer le diagnostic dans les tracebacks.",
    )
    backtrace: bool = Field(
        default=True,
        description="Activer le backtrace complet.",
    )
    catch_exceptions: bool = Field(
        default=True,
        description="Intercepter les exceptions non gérées.",
    )
    module_levels: dict[str, LogLevel] = Field(
        default_factory=dict,
        description="Niveaux de log par module (overrides).",
    )
    exclude_modules: set[str] = Field(
        default_factory=set,
        description="Modules à exclure du logging.",
    )
    quiet: bool = Field(
        default=False,
        description="Mode silencieux (ERROR uniquement).",
    )
    verbose: bool = Field(
        default=False,
        description="Mode verbeux (DEBUG partout).",
    )
    serialize_json: bool = Field(
        default=False,
        description="Sérialiser les logs en JSON (même pour TEXT).",
    )
    app_name: str = Field(
        default="NexusDL",
        description="Nom de l'application.",
    )
    app_version: str = Field(
        default=_APP_VERSION,
        description="Version de l'application.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @field_validator("log_dir", mode="before")
    @classmethod
    def _validate_log_dir(cls, v: Any) -> Path | None:
        """Valide et normalise le répertoire de logs."""
        if v is None:
            return None
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return None
            v = Path(v)
        if not isinstance(v, Path):
            raise LogDirectoryError(v, "Doit être un chemin (str ou Path)")
        return v.expanduser().resolve()

    # --------------------------------------------------------------------
    # Méthodes
    # --------------------------------------------------------------------

    def with_env_overrides(self) -> LoggerConfig:
        """Retourne une nouvelle config avec les overrides d'environnement.

        Les variables d'environnement priment sur la configuration.

        Returns:
            Nouvelle instance de LoggerConfig avec overrides appliqués.
        """
        updates: dict[str, Any] = {}

        # NEXUSDL_LOG_LEVEL
        env_level = os.environ.get(_ENV_LOG_LEVEL)
        if env_level:
            try:
                updates["level"] = LogLevel(env_level.upper())
            except ValueError:
                pass

        # NEXUSDL_LOG_FORMAT
        env_format = os.environ.get(_ENV_LOG_FORMAT)
        if env_format:
            try:
                updates["format"] = LogFormat(env_format.lower())
            except ValueError:
                pass

        # NEXUSDL_LOG_DIR
        env_dir = os.environ.get(_ENV_LOG_DIR)
        if env_dir:
            updates["log_dir"] = Path(env_dir).expanduser().resolve()

        # NEXUSDL_LOG_FILE
        env_file = os.environ.get(_ENV_LOG_FILE)
        if env_file:
            updates["log_file"] = env_file

        # NEXUSDL_LOG_NO_COLOR
        if os.environ.get(_ENV_LOG_NO_COLOR) == "1":
            updates["colorize"] = False

        # NEXUSDL_LOG_QUIET
        if os.environ.get(_ENV_LOG_QUIET) == "1":
            updates["quiet"] = True

        # NEXUSDL_LOG_VERBOSE
        if os.environ.get(_ENV_LOG_VERBOSE) == "1":
            updates["verbose"] = True

        # NEXUSDL_LOG_JSON
        if os.environ.get(_ENV_LOG_JSON) == "1":
            updates["enable_json_file"] = True
            updates["format"] = LogFormat.JSON

        if not updates:
            return self

        return self.model_copy(update=updates)

    def get_effective_level(self, module: str | None = None) -> LogLevel:
        """Retourne le niveau de log effectif pour un module.

        Args:
            module: Nom du module (optionnel).

        Returns:
            Niveau de log effectif.
        """
        # Mode quiet/verbose
        if self.quiet:
            return LogLevel.ERROR
        if self.verbose:
            return LogLevel.DEBUG

        # Niveau spécifique au module
        if module and module in self.module_levels:
            return self.module_levels[module]

        # Niveau par défaut
        return self.level


class LoggingStats(BaseModel):
    """Statistiques du système de logging.

    Attributes:
        total_logs: Nombre total de logs émis.
        by_level: Nombre de logs par niveau.
        by_module: Nombre de logs par module.
        error_count: Nombre d'erreurs loguées.
        critical_count: Nombre de logs critiques.
        start_time: Timestamp de démarrage du logger.
        handlers_count: Nombre de handlers actifs.
    """

    total_logs: int = Field(default=0, ge=0)
    by_level: dict[str, int] = Field(default_factory=dict)
    by_module: dict[str, int] = Field(default_factory=dict)
    error_count: int = Field(default=0, ge=0)
    critical_count: int = Field(default=0, ge=0)
    start_time: datetime | None = None
    handlers_count: int = Field(default=0, ge=0)

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# COMPTEUR DE LOGS — Statistiques en temps réel
# ============================================================================


class _LogCounter:
    """Compteur de logs thread-safe.

    Utilisé pour collecter des statistiques sur les logs émis.
    """

    __slots__ = (
        "_total",
        "_by_level",
        "_by_module",
        "_error_count",
        "_critical_count",
        "_start_time",
        "_lock",
    )

    def __init__(self) -> None:
        self._total: int = 0
        self._by_level: dict[str, int] = defaultdict(int)
        self._by_module: dict[str, int] = defaultdict(int)
        self._error_count: int = 0
        self._critical_count: int = 0
        self._start_time: datetime = datetime.now(UTC)
        self._lock = threading.Lock()

    def record(
        self,
        level: str,
        module: str | None,
    ) -> None:
        """Enregistre un log émis.

        Args:
            level: Niveau du log.
            module: Module source (optionnel).
        """
        with self._lock:
            self._total += 1
            self._by_level[level] += 1
            if module:
                self._by_module[module] += 1
            if level == "ERROR":
                self._error_count += 1
            elif level == "CRITICAL":
                self._critical_count += 1

    def get_stats(self) -> LoggingStats:
        """Retourne les statistiques actuelles."""
        with self._lock:
            return LoggingStats(
                total_logs=self._total,
                by_level=dict(self._by_level),
                by_module=dict(self._by_module),
                error_count=self._error_count,
                critical_count=self._critical_count,
                start_time=self._start_time,
            )

    def reset(self) -> None:
        """Réinitialise les compteurs."""
        with self._lock:
            self._total = 0
            self._by_level.clear()
            self._by_module.clear()
            self._error_count = 0
            self._critical_count = 0
            self._start_time = datetime.now(UTC)


# Instance globale du compteur
_log_counter = _LogCounter()


# ============================================================================
# FORMATS DE LOG
# ============================================================================


def _build_text_format(config: LoggerConfig) -> str:
    """Construit le format texte structuré.

    Args:
        config: Configuration du logger.

    Returns:
        Chaîne de format pour Loguru.
    """
    return (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan>"
        "{extra[module_suffix]}"
        " - <level>{message}</level>\n"
        "{exception}"
    )


def _build_simple_format(config: LoggerConfig) -> str:
    """Construit le format simple (timestamp + message).

    Args:
        config: Configuration du logger.

    Returns:
        Chaîne de format pour Loguru.
    """
    return "{time:HH:mm:ss} | {level: <8} | {message}\n{exception}"


def _build_rich_format(config: LoggerConfig) -> str:
    """Construit le format riche (si `rich` disponible).

    Args:
        config: Configuration du logger.

    Returns:
        Chaîne de format pour Loguru.
    """
    # Le format riche utilise les balises de couleur de Loguru
    # avec un rendu amélioré via la librairie `rich` si disponible
    return (
        "<dim>{time:HH:mm:ss.SSS}</dim> | "
        "<level>{level: <8}</level> | "
        "<cyan>{extra[module_name]: <20}</cyan> | "
        "<level>{message}</level>\n"
        "{exception}"
    )


def _build_json_sink(record: dict[str, Any]) -> str:
    """Construit une entrée JSON pour le sink JSON Lines.

    Args:
        record: Record Loguru.

    Returns:
        Chaîne JSON sérialisée.
    """
    # Extraire les informations pertinentes
    entry = {
        "timestamp": record["time"].isoformat(),
        "level": record["level"].name,
        "module": record["extra"].get("module", ""),
        "logger": record["name"],
        "function": record["function"],
        "line": record["line"],
        "message": record["message"],
        "app_name": record["extra"].get("app_name", "NexusDL"),
        "app_version": record["extra"].get("app_version", _APP_VERSION),
        "pid": record["extra"].get("pid", os.getpid()),
        "thread": record["extra"].get("thread", threading.current_thread().name),
    }

    # Ajouter le contexte additionnel
    for key, value in record["extra"].items():
        if key not in entry and key not in ("module_name", "module_suffix"):
            entry[key] = value

    # Ajouter l'exception si présente
    if record["exception"] is not None:
        entry["exception"] = {
            "type": record["exception"].type.__name__,
            "value": str(record["exception"].value),
            "traceback": "".join(
                traceback.format_exception(
                    record["exception"].type,
                    record["exception"].value,
                    record["exception"].traceback,
                )
            ),
        }

    return json.dumps(entry, ensure_ascii=False, default=str) + "\n"


# ============================================================================
# FILTRES — Filtrage par module
# ============================================================================


def _build_module_filter(config: LoggerConfig) -> Callable[[dict[str, Any]], bool]:
    """Construit un filtre pour les logs par module.

    Args:
        config: Configuration du logger.

    Returns:
        Fonction de filtre pour Loguru.
    """

    def _filter(record: dict[str, Any]) -> bool:
        # Récupérer le module depuis le contexte
        module = record["extra"].get("module")

        # Exclure les modules configurés pour exclusion
        if module and module in config.exclude_modules:
            return False

        # Déterminer le niveau effectif
        effective_level = config.get_effective_level(module)

        # Comparer avec le niveau du log
        level_name = record["level"].name
        try:
            record_level = LogLevel(level_name)
        except ValueError:
            # Niveau personnalisé (TRACE, SUCCESS)
            record_level = None

        if record_level is None:
            # Pour les niveaux personnalisés, utiliser la valeur numérique
            level_value = record["level"].no
            return level_value >= effective_level.numeric_level

        return record_level.numeric_level >= effective_level.numeric_level

    return _filter


# ============================================================================
# CLASSE PRINCIPALE — LoggerManager
# ============================================================================


class LoggerManager:
    """Gestionnaire centralisé du système de logging.

    Configure Loguru avec les handlers appropriés selon la configuration,
    gère la rotation, la compression, et le filtrage par module.

    Lifecycle :
        >>> manager = LoggerManager(config=LoggerConfig())
        >>> manager.initialize()
        >>> # ... utilisation ...
        >>> manager.shutdown()

    Thread-safety :
        Cette classe est thread-safe. Les compteurs sont protégés par un lock.
    """

    # IDs des handlers (pour retrait)
    _CONSOLE_HANDLER_ID: ClassVar[int | None] = None
    _FILE_HANDLER_ID: ClassVar[int | None] = None
    _ERROR_HANDLER_ID: ClassVar[int | None] = None
    _JSON_HANDLER_ID: ClassVar[int | None] = None

    def __init__(
        self,
        *,
        config: LoggerConfig | None = None,
    ) -> None:
        """Initialise le gestionnaire de logging.

        Args:
            config: Configuration du logger (défaut: valeurs standards).
        """
        self._config = (config or LoggerConfig()).with_env_overrides()
        self._initialized: bool = False
        self._handler_ids: list[int] = []

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    def initialize(self) -> None:
        """Initialise le système de logging.

        Configure Loguru avec les handlers appropriés selon la configuration.
        Cette méthode peut être appelée plusieurs fois (reconfiguration).

        Raises:
            LogDirectoryError: Si le répertoire de logs est inaccessible.
        """
        # Retirer le handler par défaut de Loguru
        _loguru_logger.remove()

        # Ajouter les niveaux personnalisés
        try:
            _loguru_logger.level("TRACE", no=_TRACE_LEVEL, color="<cyan>", icon="🔍")
        except TypeError:
            pass  # Niveau déjà ajouté
        try:
            _loguru_logger.level("SUCCESS", no=_SUCCESS_LEVEL, color="<green>", icon="✅")
        except TypeError:
            pass  # Niveau déjà ajouté

        # Configurer le contexte global
        _loguru_logger.configure(
            extra={
                "app_name": self._config.app_name,
                "app_version": self._config.app_version,
                "pid": os.getpid(),
                "module": "",
                "module_name": "",
                "module_suffix": "",
            }
        )

        # 1. Handler console
        if self._config.enable_console:
            self._setup_console_handler()

        # 2. Handler fichier principal
        if self._config.enable_file and self._config.log_dir:
            self._setup_file_handler()

        # 3. Handler fichier d'erreurs
        if self._config.enable_error_file and self._config.log_dir:
            self._setup_error_handler()

        # 4. Handler fichier JSON
        if self._config.enable_json_file and self._config.log_dir:
            self._setup_json_handler()

        # 5. Intercepteur d'exceptions
        if self._config.catch_exceptions:
            self._setup_exception_handler()

        # Réinitialiser le compteur
        _log_counter.reset()

        self._initialized = True

        # Log de démarrage
        _loguru_logger.info(
            "Logging initialisé: level={}, format={}, handlers={}",
            self._config.level.value,
            self._config.format.value,
            len(self._handler_ids),
        )

    def shutdown(self) -> None:
        """Arrête le système de logging et retire tous les handlers."""
        if not self._initialized:
            return

        # Retirer tous les handlers
        for handler_id in self._handler_ids:
            try:
                _loguru_logger.remove(handler_id)
            except (ValueError, KeyError):
                pass

        self._handler_ids.clear()

        # Restaurer l'exception handler par défaut
        if self._config.catch_exceptions:
            sys.excepthook = sys.__excepthook__

        self._initialized = False

    def reconfigure(self, config: LoggerConfig) -> None:
        """Reconfigure le logger avec une nouvelle configuration.

        Args:
            config: Nouvelle configuration.
        """
        self._config = config.with_env_overrides()
        self.shutdown()
        self.initialize()

    def __enter__(self) -> Self:
        self.initialize()
        return self

    def __exit__(self, *args: object) -> None:
        self.shutdown()

    # ------------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------------

    @property
    def is_initialized(self) -> bool:
        """Indique si le logger est initialisé."""
        return self._initialized

    @property
    def config(self) -> LoggerConfig:
        """Configuration actuelle."""
        return self._config

    @property
    def handlers_count(self) -> int:
        """Nombre de handlers actifs."""
        return len(self._handler_ids)

    # ------------------------------------------------------------------------
    # Setup des handlers
    # ------------------------------------------------------------------------

    def _setup_console_handler(self) -> None:
        """Configure le handler console."""
        # Déterminer si on est dans un TTY
        is_tty = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
        colorize = self._config.colorize and is_tty

        # Choisir le format
        if self._config.format == LogFormat.RICH:
            fmt = _build_rich_format(self._config)
        elif self._config.format == LogFormat.SIMPLE:
            fmt = _build_simple_format(self._config)
        else:
            fmt = _build_text_format(self._config)

        # Ajouter le handler
        handler_id = _loguru_logger.add(
            sys.stderr,
            format=fmt,
            level=self._config.get_effective_level().value,
            colorize=colorize,
            backtrace=self._config.backtrace,
            diagnose=self._config.diagnose,
            filter=_build_module_filter(self._config),
            enqueue=False,  # Console synchrone pour réactivité
        )
        self._handler_ids.append(handler_id)

    def _setup_file_handler(self) -> None:
        """Configure le handler fichier principal."""
        assert self._config.log_dir is not None

        # Créer le répertoire si nécessaire
        try:
            self._config.log_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise LogDirectoryError(self._config.log_dir, str(e)) from e

        log_path = self._config.log_dir / self._config.log_file

        # Choisir le format
        if self._config.format == LogFormat.JSON:
            fmt = _build_json_sink
            serialize = False  # On gère nous-même la sérialisation
        else:
            fmt = (
                "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
                "{name}:{function}:{line}{extra[module_suffix]} - {message}\n"
                "{exception}"
            )
            serialize = self._config.serialize_json

        # Ajouter le handler
        handler_id = _loguru_logger.add(
            str(log_path),
            format=fmt,
            level="DEBUG",  # Capturer tout
            rotation=self._config.rotation,
            retention=self._config.retention,
            compression=self._config.compression,
            serialize=serialize,
            backtrace=self._config.backtrace,
            diagnose=self._config.diagnose,
            filter=_build_module_filter(self._config),
            enqueue=True,  # Async pour ne pas bloquer
            encoding="utf-8",
        )
        self._handler_ids.append(handler_id)

    def _setup_error_handler(self) -> None:
        """Configure le handler fichier d'erreurs."""
        assert self._config.log_dir is not None

        log_path = self._config.log_dir / self._config.error_log_file

        # Format avec traceback complet
        fmt = (
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
            "{name}:{function}:{line}{extra[module_suffix]}\n"
            "{message}\n"
            "{exception}\n"
        )

        # Ajouter le handler (ERROR et au-dessus uniquement)
        handler_id = _loguru_logger.add(
            str(log_path),
            format=fmt,
            level="ERROR",
            rotation=_ERROR_ROTATION,
            retention=_ERROR_RETENTION,
            compression=self._config.compression,
            backtrace=True,
            diagnose=True,
            filter=_build_module_filter(self._config),
            enqueue=True,
            encoding="utf-8",
        )
        self._handler_ids.append(handler_id)

    def _setup_json_handler(self) -> None:
        """Configure le handler fichier JSON."""
        assert self._config.log_dir is not None

        log_path = self._config.log_dir / self._config.json_log_file

        # Ajouter le handler avec format JSON
        handler_id = _loguru_logger.add(
            str(log_path),
            format=_build_json_sink,
            level="DEBUG",
            rotation=_JSON_ROTATION,
            retention=_JSON_RETENTION,
            compression=self._config.compression,
            serialize=False,  # On gère nous-même
            filter=_build_module_filter(self._config),
            enqueue=True,
            encoding="utf-8",
        )
        self._handler_ids.append(handler_id)

    def _setup_exception_handler(self) -> None:
        """Configure l'intercepteur d'exceptions non gérées."""

        def _exception_handler(
            exc_type: type[BaseException],
            exc_value: BaseException,
            exc_traceback: Any,
        ) -> None:
            """Hook pour sys.excepthook."""
            # Logger l'exception
            _loguru_logger.opt(exception=(exc_type, exc_value, exc_traceback)).critical(
                "Exception non gérée: {}",
                exc_value,
            )

            # Appeler le hook original (pour affichage standard)
            if sys.__excepthook__:
                sys.__excepthook__(exc_type, exc_value, exc_traceback)

        sys.excepthook = _exception_handler

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    def get_stats(self) -> LoggingStats:
        """Retourne les statistiques de logging.

        Returns:
            Instance de LoggingStats avec les compteurs.
        """
        stats = _log_counter.get_stats()
        return stats.model_copy(update={"handlers_count": len(self._handler_ids)})

    def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        _log_counter.reset()


# ============================================================================
# INSTANCE GLOBALE ET FONCTIONS PRATIQUES
# ============================================================================


# Instance globale du LoggerManager
_logger_manager: LoggerManager | None = None


def setup_logging(
    *,
    level: str | LogLevel = LogLevel.INFO,
    format: str | LogFormat = LogFormat.TEXT,
    log_dir: Path | str | None = None,
    log_file: str = _LOG_FILENAME,
    error_log_file: str = _ERROR_LOG_FILENAME,
    enable_console: bool = True,
    enable_file: bool = True,
    enable_error_file: bool = True,
    enable_json_file: bool = False,
    rotation: str = _DEFAULT_ROTATION,
    retention: str = _DEFAULT_RETENTION,
    compression: str | None = _DEFAULT_COMPRESSION,
    colorize: bool = True,
    diagnose: bool = False,
    backtrace: bool = True,
    catch_exceptions: bool = True,
    module_levels: dict[str, str | LogLevel] | None = None,
    exclude_modules: set[str] | None = None,
    quiet: bool = False,
    verbose: bool = False,
    serialize_json: bool = False,
    app_name: str = "NexusDL",
    app_version: str = _APP_VERSION,
) -> LoggerManager:
    """Configure le système de logging de NexusDL.

    Fonction de haut niveau pour initialiser rapidement le logging.
    Crée une instance globale de LoggerManager et l'initialise.

    Args:
        level: Niveau de log global (défaut: INFO).
        format: Format de sortie (text, rich, json, simple).
        log_dir: Répertoire des fichiers de logs.
        log_file: Nom du fichier de log principal.
        error_log_file: Nom du fichier de log d'erreurs.
        enable_console: Activer le handler console.
        enable_file: Activer le handler fichier principal.
        enable_error_file: Activer le handler fichier d'erreurs.
        enable_json_file: Activer le handler fichier JSON.
        rotation: Taille max avant rotation.
        retention: Durée de rétention.
        compression: Compression des anciens logs.
        colorize: Activer les couleurs.
        diagnose: Activer le diagnostic.
        backtrace: Activer le backtrace complet.
        catch_exceptions: Intercepter les exceptions.
        module_levels: Niveaux par module (dict module → level).
        exclude_modules: Modules à exclure.
        quiet: Mode silencieux.
        verbose: Mode verbeux.
        serialize_json: Sérialiser en JSON.
        app_name: Nom de l'application.
        app_version: Version de l'application.

    Returns:
        Instance de LoggerManager initialisée.

    Example:
        >>> from nexusdl.core.logger import setup_logging
        >>> setup_logging(level="INFO", log_dir=Path("/var/log/nexusdl"))
    """
    global _logger_manager

    # Convertir les niveaux de module
    normalized_module_levels: dict[str, LogLevel] = {}
    if module_levels:
        for module, level_value in module_levels.items():
            if isinstance(level_value, str):
                normalized_module_levels[module] = LogLevel(level_value.upper())
            else:
                normalized_module_levels[module] = level_value

    # Convertir les paramètres
    if isinstance(level, str):
        level_enum = LogLevel(level.upper())
    else:
        level_enum = level

    if isinstance(format, str):
        format_enum = LogFormat(format.lower())
    else:
        format_enum = format

    if isinstance(log_dir, str):
        log_dir_path = Path(log_dir).expanduser().resolve()
    else:
        log_dir_path = log_dir

    # Construire la configuration
    config = LoggerConfig(
        level=level_enum,
        format=format_enum,
        log_dir=log_dir_path,
        log_file=log_file,
        error_log_file=error_log_file,
        enable_console=enable_console,
        enable_file=enable_file,
        enable_error_file=enable_error_file,
        enable_json_file=enable_json_file,
        rotation=rotation,
        retention=retention,
        compression=compression,
        colorize=colorize,
        diagnose=diagnose,
        backtrace=backtrace,
        catch_exceptions=catch_exceptions,
        module_levels=normalized_module_levels,
        exclude_modules=exclude_modules or set(),
        quiet=quiet,
        verbose=verbose,
        serialize_json=serialize_json,
        app_name=app_name,
        app_version=app_version,
    )

    # Créer et initialiser le manager
    _logger_manager = LoggerManager(config=config)
    _logger_manager.initialize()

    return _logger_manager


def get_logger(module: str | None = None) -> Logger:
    """Retourne un logger avec contexte de module.

    Args:
        module: Nom du module (ex: "core.downloader.worker").

    Returns:
        Logger Loguru avec contexte enrichi.

    Example:
        >>> logger = get_logger("core.downloader.worker")
        >>> logger.info("Tâche démarrée")
    """
    if module:
        # Construire le suffixe pour le format texte
        module_suffix = f" [{module}]"

        return _loguru_logger.bind(
            module=module,
            module_name=module,
            module_suffix=module_suffix,
        )

    return _loguru_logger.bind(
        module="",
        module_name="",
        module_suffix="",
    )


def get_logger_manager() -> LoggerManager | None:
    """Retourne l'instance globale du LoggerManager.

    Returns:
        Instance de LoggerManager ou None si non initialisée.
    """
    return _logger_manager


def get_logging_stats() -> LoggingStats:
    """Retourne les statistiques de logging.

    Returns:
        Instance de LoggingStats.
    """
    if _logger_manager is None:
        return _log_counter.get_stats()
    return _logger_manager.get_stats()


def reset_logging() -> None:
    """Réinitialise le système de logging.

    Arrête le logger global et le remet à zéro.
    """
    global _logger_manager
    if _logger_manager is not None:
        _logger_manager.shutdown()
        _logger_manager = None

    # Retirer tous les handlers de Loguru
    _loguru_logger.remove()

    # Réinitialiser le compteur
    _log_counter.reset()


def set_log_level(level: str | LogLevel) -> None:
    """Change le niveau de log global à la volée.

    Args:
        level: Nouveau niveau de log.
    """
    if isinstance(level, str):
        level_enum = LogLevel(level.upper())
    else:
        level_enum = level

    if _logger_manager is not None:
        new_config = _logger_manager.config.model_copy(update={"level": level_enum})
        _logger_manager.reconfigure(new_config)


def set_module_level(module: str, level: str | LogLevel) -> None:
    """Change le niveau de log pour un module spécifique.

    Args:
        module: Nom du module.
        level: Nouveau niveau de log.
    """
    if isinstance(level, str):
        level_enum = LogLevel(level.upper())
    else:
        level_enum = level

    if _logger_manager is not None:
        new_module_levels = dict(_logger_manager.config.module_levels)
        new_module_levels[module] = level_enum
        new_config = _logger_manager.config.model_copy(
            update={"module_levels": new_module_levels}
        )
        _logger_manager.reconfigure(new_config)


# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================


def log_exception(
    exception: BaseException,
    *,
    message: str = "Exception capturée",
    level: LogLevel = LogLevel.ERROR,
    module: str | None = None,
    **context: Any,
) -> None:
    """Log une exception avec contexte enrichi.

    Args:
        exception: Exception à logger.
        message: Message descriptif.
        level: Niveau de log.
        module: Module source.
        **context: Contexte additionnel.
    """
    log = get_logger(module)
    log.opt(exception=exception).log(level.value, "{}: {}", message, exception, **context)


def log_timing(
    message: str,
    *,
    level: LogLevel = LogLevel.INFO,
    module: str | None = None,
) -> Callable[[], None]:
    """Crée un contexte de mesure de temps.

    Args:
        message: Message à logger au début.
        level: Niveau de log.
        module: Module source.

    Returns:
        Fonction à appeler pour logger la fin avec la durée.

    Example:
        >>> finish = log_timing("Traitement démarré")
        >>> # ... traitement ...
        >>> finish()  # Log : "Traitement terminé en 1.23s"
    """
    log = get_logger(module)
    start_time = time.perf_counter()
    log.log(level.value, "{}", message)

    def _finish() -> None:
        elapsed = time.perf_counter() - start_time
        log.log(level.value, "{} terminé en {:.2f}s", message, elapsed)

    return _finish


def is_debug_enabled(module: str | None = None) -> bool:
    """Vérifie si le mode DEBUG est activé pour un module.

    Args:
        module: Nom du module (optionnel).

    Returns:
        True si DEBUG est activé.
    """
    if _logger_manager is None:
        return False

    effective_level = _logger_manager.config.get_effective_level(module)
    return effective_level.numeric_level <= LogLevel.DEBUG.numeric_level


def is_trace_enabled(module: str | None = None) -> bool:
    """Vérifie si le mode TRACE est activé pour un module.

    Args:
        module: Nom du module (optionnel).

    Returns:
        True si TRACE est activé.
    """
    if _logger_manager is None:
        return False

    effective_level = _logger_manager.config.get_effective_level(module)
    return effective_level.numeric_level <= LogLevel.TRACE.numeric_level


# ============================================================================
# SINK PERSONNALISÉ — Callback pour EventBus
# ============================================================================


class EventBusSink:
    """Sink Loguru qui émet les logs sur un EventBus.

    Permet d'intégrer les logs avec le système d'événements de NexusDL
    pour affichage dans les interfaces (CLI, Web, GUI).

    Example:
        >>> from nexusdl.core.events import EventBus
        >>> event_bus = EventBus()
        >>> sink = EventBusSink(event_bus)
        >>> _loguru_logger.add(sink)
    """

    def __init__(
        self,
        event_bus: Any,
        *,
        min_level: LogLevel = LogLevel.INFO,
        event_name: str = "log.message",
    ) -> None:
        """Initialise le sink.

        Args:
            event_bus: Instance de EventBus.
            min_level: Niveau minimum pour émettre.
            event_name: Nom de l'événement à émettre.
        """
        self._event_bus = event_bus
        self._min_level = min_level
        self._event_name = event_name

    def __call__(self, message: Any) -> None:
        """Appelé par Loguru pour chaque log.

        Args:
            message: Record Loguru.
        """
        # Vérifier le niveau
        level_name = message.record["level"].name
        try:
            level = LogLevel(level_name)
        except ValueError:
            return

        if level.numeric_level < self._min_level.numeric_level:
            return

        # Construire le payload
        payload = {
            "timestamp": message.record["time"].isoformat(),
            "level": level_name,
            "module": message.record["extra"].get("module", ""),
            "message": str(message),
        }

        # Émettre l'événement (non-bloquant)
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(
                    self._event_bus.emit(self._event_name, payload)
                )
            else:
                loop.run_until_complete(
                    self._event_bus.emit(self._event_name, payload)
                )
        except Exception:
            # Silencieux : ne pas faire échouer le log
            pass


# ============================================================================
# DÉCORATEURS — Logging automatique
# ============================================================================


def log_call(
    *,
    level: LogLevel = LogLevel.DEBUG,
    include_args: bool = False,
    include_result: bool = False,
    module: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Décorateur pour logger automatiquement les appels de fonction.

    Args:
        level: Niveau de log.
        include_args: Inclure les arguments dans le log.
        include_result: Inclure le résultat dans le log.
        module: Module source (défaut: nom du module de la fonction).

    Returns:
        Décorateur.

    Example:
        >>> @log_call(level=LogLevel.DEBUG, include_args=True)
        ... def my_function(x, y):
        ...     return x + y
    """

    def _decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func_module = module or func.__module__
        log = get_logger(func_module)

        if asyncio.iscoroutinefunction(func):

            async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start_time = time.perf_counter()

                if include_args:
                    log.log(
                        level.value,
                        "→ {}(args={}, kwargs={})",
                        func.__name__,
                        args,
                        kwargs,
                    )
                else:
                    log.log(level.value, "→ {}", func.__name__)

                try:
                    result = await func(*args, **kwargs)
                    elapsed = time.perf_counter() - start_time

                    if include_result:
                        log.log(
                            level.value,
                            "← {} = {} ({:.2f}ms)",
                            func.__name__,
                            result,
                            elapsed * 1000,
                        )
                    else:
                        log.log(
                            level.value,
                            "← {} ({:.2f}ms)",
                            func.__name__,
                            elapsed * 1000,
                        )

                    return result

                except Exception as e:
                    elapsed = time.perf_counter() - start_time
                    log.log(
                        LogLevel.ERROR.value,
                        "✗ {} raised {} ({:.2f}ms): {}",
                        func.__name__,
                        type(e).__name__,
                        elapsed * 1000,
                        e,
                    )
                    raise

            return _async_wrapper

        else:

            def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                start_time = time.perf_counter()

                if include_args:
                    log.log(
                        level.value,
                        "→ {}(args={}, kwargs={})",
                        func.__name__,
                        args,
                        kwargs,
                    )
                else:
                    log.log(level.value, "→ {}", func.__name__)

                try:
                    result = func(*args, **kwargs)
                    elapsed = time.perf_counter() - start_time

                    if include_result:
                        log.log(
                            level.value,
                            "← {} = {} ({:.2f}ms)",
                            func.__name__,
                            result,
                            elapsed * 1000,
                        )
                    else:
                        log.log(
                            level.value,
                            "← {} ({:.2f}ms)",
                            func.__name__,
                            elapsed * 1000,
                        )

                    return result

                except Exception as e:
                    elapsed = time.perf_counter() - start_time
                    log.log(
                        LogLevel.ERROR.value,
                        "✗ {} raised {} ({:.2f}ms): {}",
                        func.__name__,
                        type(e).__name__,
                        elapsed * 1000,
                        e,
                    )
                    raise

            return _sync_wrapper

    return _decorator


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "_APP_VERSION",
    # Exceptions
    "LoggerError",
    "LoggerNotInitializedError",
    "InvalidLogLevelError",
    "LogDirectoryError",
    # Enums
    "LogLevel",
    "LogFormat",
    # Modèles
    "LoggerConfig",
    "ModuleLogLevel",
    "LoggingStats",
    # Classe principale
    "LoggerManager",
    # Fonctions principales
    "setup_logging",
    "get_logger",
    "get_logger_manager",
    "get_logging_stats",
    "reset_logging",
    "set_log_level",
    "set_module_level",
    # Helpers
    "log_exception",
    "log_timing",
    "is_debug_enabled",
    "is_trace_enabled",
    # Sinks
    "EventBusSink",
    # Décorateurs
    "log_call",
]
