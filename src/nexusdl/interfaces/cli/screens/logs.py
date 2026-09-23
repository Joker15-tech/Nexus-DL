"""Écran de visualisation des logs de l'interface CLI NexusDL.

Ce module fournit un écran complet de visualisation des logs en temps réel
via une interface TUI (Terminal User Interface) basée sur Textual. Il permet
à l'utilisateur de suivre l'activité de l'application, filtrer les logs par
niveau ou module, rechercher du texte, et exporter les logs vers un fichier.

**Fonctionnalités** :
    - Affichage temps réel des logs via sink Loguru personnalisé
    - Buffer circulaire (10000 entrées max) pour limiter la mémoire
    - Filtrage par niveau (TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL)
    - Filtrage par module (ex: "core.downloader.*")
    - Recherche de texte avec highlight
    - Pause/reprise du flux de logs
    - Auto-scroll configurable
    - Coloration syntaxique par niveau
    - Export des logs vers fichier (texte brut ou JSON)
    - Statistiques en temps réel (compteurs par niveau)
    - Navigation clavier complète (flèches, PageUp/Down, Home/End, /, f, p)
    - Traductions i18n
    - Gestion des erreurs

**Architecture** :
    LogsScreen (Screen Textual)
        ├── LogsFilterBar (barre de filtrage)
        │   ├── Select (niveau minimum)
        │   ├── Input (filtre module)
        │   ├── Input (recherche texte)
        │   └── Switch (auto-scroll)
        ├── LogsList (liste scrollable des logs)
        │   └── LogEntryWidget (entrée individuelle colorée)
        └── LogsStatusBar (barre de statut avec statistiques)
                ├── Compteurs par niveau
                ├── État (paused/running)
                └── Nombre de logs affichés/filtrés

**Flux des logs** :
    1. Loguru émet un log
    2. LogsSink intercepte le log
    3. LogsSink poste un NewLogMessage à l'écran
    4. LogsScreen reçoit le message
    5. Si le log passe les filtres → ajout à LogsList
    6. Si auto-scroll activé → scroll vers le bas

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.cli.screens.logs import LogsScreen, install_logs_sink
    >>>
    >>> # Au démarrage de l'application
    >>> install_logs_sink(event_bus)
    >>>
    >>> # Ouvrir l'écran
    >>> app.push_screen(LogsScreen())
    >>>
    >>> # L'utilisateur peut :
    >>> # 1. Voir les logs en temps réel
    >>> # 2. Filtrer par niveau (touche 'l')
    >>> # 3. Filtrer par module (touche 'm')
    >>> # 4. Rechercher du texte (touche '/')
    >>> # 5. Mettre en pause (touche 'p')
    >>> # 6. Exporter (touche 'e')
    >>> # 7. Effacer (touche 'c')

Intégration :
    - core/logger.py : sink Loguru personnalisé
    - core/events.py : EventBus pour communication
    - core/paths.py : chemins des fichiers de logs
    - core/i18n.py : traductions
    - core/utils/filesystem.py : export vers fichier
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import deque
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Final

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

try:
    from textual.app import ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal, Vertical, VerticalScroll
    from textual.message import Message
    from textual.reactive import reactive
    from textual.screen import Screen
    from textual.widget import Widget
    from textual.widgets import (
        Button,
        Footer,
        Header,
        Input,
        Label,
        ListItem,
        ListView,
        Select,
        Static,
        Switch,
    )
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False

from nexusdl.core.constants import APP_NAME
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t
from nexusdl.core.paths import get_paths
from nexusdl.core.utils.filesystem import atomic_write


# ============================================================================
# CONSTANTES
# ============================================================================


# Taille maximale du buffer de logs
MAX_BUFFER_SIZE: Final[int] = 10000

# Taille maximale d'un message de log (tronqué si plus long)
MAX_MESSAGE_LENGTH: Final[int] = 1000

# Intervalle de mise à jour des statistiques (ms)
STATS_UPDATE_INTERVAL_MS: Final[int] = 1000

# Niveaux de log avec leurs couleurs ANSI
LOG_LEVEL_COLORS: Final[dict[str, str]] = {
    "TRACE": "dim",
    "DEBUG": "blue",
    "INFO": "green",
    "SUCCESS": "bright_green",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bright_red",
}

# Niveaux de log avec leurs icônes
LOG_LEVEL_ICONS: Final[dict[str, str]] = {
    "TRACE": "🔍",
    "DEBUG": "🐛",
    "INFO": "ℹ️",
    "SUCCESS": "✅",
    "WARNING": "⚠️",
    "ERROR": "❌",
    "CRITICAL": "💥",
}


# ============================================================================
# EXCEPTIONS
# ============================================================================


class LogsScreenError(NexusDLError):
    """Exception de base pour les erreurs de l'écran de logs."""


class LogsExportError(LogsScreenError):
    """Exception levée lorsque l'export des logs échoue.

    Attributes:
        path: Chemin du fichier cible.
        reason: Raison de l'échec.
    """

    def __init__(self, path: Path, reason: str = "") -> None:
        msg = f"Échec de l'export des logs vers {path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.path = path
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class LogLevelFilter(str, Enum):
    """Filtre de niveau de log.

    Attributes:
        TRACE: Afficher tous les logs.
        DEBUG: Afficher DEBUG et au-dessus.
        INFO: Afficher INFO et au-dessus.
        SUCCESS: Afficher SUCCESS et au-dessus.
        WARNING: Afficher WARNING et au-dessus.
        ERROR: Afficher ERROR et au-dessus.
        CRITICAL: Afficher uniquement CRITICAL.
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
        """Niveau numérique (pour comparaison)."""
        return {
            LogLevelFilter.TRACE: 5,
            LogLevelFilter.DEBUG: 10,
            LogLevelFilter.INFO: 20,
            LogLevelFilter.SUCCESS: 25,
            LogLevelFilter.WARNING: 30,
            LogLevelFilter.ERROR: 40,
            LogLevelFilter.CRITICAL: 50,
        }[self]

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            LogLevelFilter.TRACE: t("logs.filter.trace", default="Trace+"),
            LogLevelFilter.DEBUG: t("logs.filter.debug", default="Debug+"),
            LogLevelFilter.INFO: t("logs.filter.info", default="Info+"),
            LogLevelFilter.SUCCESS: t("logs.filter.success", default="Success+"),
            LogLevelFilter.WARNING: t("logs.filter.warning", default="Warning+"),
            LogLevelFilter.ERROR: t("logs.filter.error", default="Error+"),
            LogLevelFilter.CRITICAL: t("logs.filter.critical", default="Critical"),
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return LOG_LEVEL_ICONS.get(self.value, "?")

    def includes(self, level: str) -> bool:
        """Vérifie si ce filtre inclut un niveau donné.

        Args:
            level: Niveau à vérifier.

        Returns:
            True si le niveau est inclus.
        """
        try:
            level_enum = LogLevelFilter(level)
            return level_enum.numeric_level >= self.numeric_level
        except ValueError:
            return True


class ExportFormat(str, Enum):
    """Format d'export des logs.

    Attributes:
        TEXT: Format texte lisible.
        JSON: Format JSON Lines.
        CSV: Format CSV.
    """

    TEXT = "text"
    JSON = "json"
    CSV = "csv"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ExportFormat.TEXT: t("logs.export.text", default="Text"),
            ExportFormat.JSON: t("logs.export.json", default="JSON"),
            ExportFormat.CSV: t("logs.export.csv", default="CSV"),
        }[self]

    @property
    def extension(self) -> str:
        """Extension de fichier."""
        return {
            ExportFormat.TEXT: ".log",
            ExportFormat.JSON: ".jsonl",
            ExportFormat.CSV: ".csv",
        }[self]


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class LogEntry(BaseModel):
    """Entrée de log individuelle.

    Attributes:
        timestamp: Timestamp du log.
        level: Niveau du log (TRACE, DEBUG, INFO, etc.).
        module: Module source du log.
        function: Fonction source.
        line: Numéro de ligne.
        message: Message du log.
        exception: Exception associée (optionnel).
        extra: Données additionnelles.
    """

    timestamp: datetime = Field(..., description="Timestamp du log.")
    level: str = Field(..., description="Niveau du log.")
    module: str = Field(default="", description="Module source.")
    function: str = Field(default="", description="Fonction source.")
    line: int = Field(default=0, description="Numéro de ligne.")
    message: str = Field(..., description="Message du log.")
    exception: str | None = Field(default=None, description="Exception associée.")
    extra: dict[str, Any] = Field(default_factory=dict, description="Données additionnelles.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def level_icon(self) -> str:
        """Icône du niveau."""
        return LOG_LEVEL_ICONS.get(self.level, "?")

    @property
    def level_color(self) -> str:
        """Couleur du niveau."""
        return LOG_LEVEL_COLORS.get(self.level, "white")

    @property
    def formatted_timestamp(self) -> str:
        """Timestamp formaté pour affichage."""
        return self.timestamp.strftime("%H:%M:%S.%f")[:-3]

    @property
    def short_module(self) -> str:
        """Module raccourci (dernier segment)."""
        if not self.module:
            return ""
        parts = self.module.split(".")
        return parts[-1] if parts else self.module

    @property
    def truncated_message(self) -> str:
        """Message tronqué si trop long."""
        if len(self.message) <= MAX_MESSAGE_LENGTH:
            return self.message
        return self.message[:MAX_MESSAGE_LENGTH] + "..."

    def matches_module_filter(self, pattern: str) -> bool:
        """Vérifie si le module correspond au pattern de filtre.

        Supporte les wildcards : "core.*", "*.downloader", etc.

        Args:
            pattern: Pattern de filtre.

        Returns:
            True si correspond.
        """
        if not pattern:
            return True
        try:
            regex = pattern.replace(".", r"\.").replace("*", ".*")
            return bool(re.match(f"^{regex}$", self.module))
        except re.error:
            return pattern.lower() in self.module.lower()

    def matches_text_search(self, query: str) -> bool:
        """Vérifie si le log correspond à la recherche textuelle.

        Args:
            query: Texte à rechercher.

        Returns:
            True si correspond.
        """
        if not query:
            return True
        query_lower = query.lower()
        return (
            query_lower in self.message.lower()
            or query_lower in self.module.lower()
            or query_lower in self.function.lower()
        )

    def to_text(self) -> str:
        """Convertit en format texte lisible.

        Returns:
            Chaîne formatée.
        """
        parts = [
            self.formatted_timestamp,
            f"[{self.level:<8}]",
            f"{self.module}:{self.function}:{self.line}",
            "-",
            self.message,
        ]
        result = " ".join(parts)
        if self.exception:
            result += f"\n{self.exception}"
        return result

    def to_dict(self) -> dict[str, Any]:
        """Convertit en dictionnaire pour export JSON.

        Returns:
            Dictionnaire sérialisable.
        """
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level,
            "module": self.module,
            "function": self.function,
            "line": self.line,
            "message": self.message,
            "exception": self.exception,
            "extra": self.extra,
        }


class LogsFilter(BaseModel):
    """Filtres appliqués aux logs.

    Attributes:
        min_level: Niveau minimum à afficher.
        module_pattern: Pattern pour filtrer les modules.
        text_search: Texte à rechercher dans les logs.
        exclude_modules: Modules à exclure.
        exclude_levels: Niveaux à exclure.
    """

    min_level: LogLevelFilter = Field(
        default=LogLevelFilter.TRACE,
        description="Niveau minimum.",
    )
    module_pattern: str = Field(
        default="",
        description="Pattern de module.",
    )
    text_search: str = Field(
        default="",
        description="Recherche textuelle.",
    )
    exclude_modules: set[str] = Field(
        default_factory=set,
        description="Modules à exclure.",
    )
    exclude_levels: set[str] = Field(
        default_factory=set,
        description="Niveaux à exclure.",
    )

    model_config = ConfigDict(extra="forbid")

    def matches(self, entry: LogEntry) -> bool:
        """Vérifie si une entrée correspond aux filtres.

        Args:
            entry: Entrée de log à vérifier.

        Returns:
            True si l'entrée passe tous les filtres.
        """
        # Filtre par niveau
        if not self.min_level.includes(entry.level):
            return False

        # Filtre par niveau exclu
        if entry.level in self.exclude_levels:
            return False

        # Filtre par module
        if self.module_pattern and not entry.matches_module_filter(self.module_pattern):
            return False

        # Filtre par module exclu
        for excluded in self.exclude_modules:
            if entry.matches_module_filter(excluded):
                return False

        # Filtre par recherche textuelle
        if self.text_search and not entry.matches_text_search(self.text_search):
            return False

        return True


class LogsStats(BaseModel):
    """Statistiques des logs.

    Attributes:
        total_received: Nombre total de logs reçus.
        total_displayed: Nombre de logs affichés.
        total_filtered: Nombre de logs filtrés.
        by_level: Compteurs par niveau.
        by_module: Compteurs par module.
        started_at: Timestamp de début de collecte.
        last_log_at: Timestamp du dernier log.
    """

    total_received: int = Field(default=0, ge=0)
    total_displayed: int = Field(default=0, ge=0)
    total_filtered: int = Field(default=0, ge=0)
    by_level: dict[str, int] = Field(default_factory=dict)
    by_module: dict[str, int] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_log_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")

    @property
    def logs_per_second(self) -> float:
        """Nombre moyen de logs par seconde."""
        elapsed = (datetime.now(UTC) - self.started_at).total_seconds()
        if elapsed <= 0:
            return 0.0
        return self.total_received / elapsed


class LogsState(BaseModel):
    """État global de l'écran de logs.

    Attributes:
        filter: Filtres actifs.
        stats: Statistiques.
        paused: Si True, le flux est en pause.
        auto_scroll: Si True, scroll automatique vers le bas.
        buffer_size: Taille actuelle du buffer.
    """

    filter: LogsFilter = Field(default_factory=LogsFilter)
    stats: LogsStats = Field(default_factory=LogsStats)
    paused: bool = Field(default=False)
    auto_scroll: bool = Field(default=True)
    buffer_size: int = Field(default=0, ge=0)

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# BUFFER DE LOGS — Stockage circulaire
# ============================================================================


class LogsBuffer:
    """Buffer circulaire pour stocker les logs.

    Limite la mémoire utilisée en conservant uniquement les N derniers logs.
    Thread-safe via asyncio.Lock.
    """

    def __init__(self, max_size: int = MAX_BUFFER_SIZE) -> None:
        """Initialise le buffer.

        Args:
            max_size: Taille maximale du buffer.
        """
        if max_size <= 0:
            raise ValueError(f"max_size doit être > 0, reçu {max_size}")

        self._max_size = max_size
        self._entries: deque[LogEntry] = deque(maxlen=max_size)
        self._lock = asyncio.Lock()

    @property
    def max_size(self) -> int:
        """Taille maximale du buffer."""
        return self._max_size

    @property
    def size(self) -> int:
        """Taille actuelle du buffer."""
        return len(self._entries)

    async def add(self, entry: LogEntry) -> None:
        """Ajoute une entrée au buffer.

        Args:
            entry: Entrée à ajouter.
        """
        async with self._lock:
            self._entries.append(entry)

    async def get_all(self) -> list[LogEntry]:
        """Retourne toutes les entrées du buffer.

        Returns:
            Liste des entrées (copie).
        """
        async with self._lock:
            return list(self._entries)

    async def get_filtered(self, filter: LogsFilter) -> list[LogEntry]:
        """Retourne les entrées correspondant aux filtres.

        Args:
            filter: Filtres à appliquer.

        Returns:
            Liste des entrées filtrées.
        """
        async with self._lock:
            return [entry for entry in self._entries if filter.matches(entry)]

    async def clear(self) -> int:
        """Vide le buffer.

        Returns:
            Nombre d'entrées supprimées.
        """
        async with self._lock:
            count = len(self._entries)
            self._entries.clear()
            return count

    async def get_last(self, n: int = 100) -> list[LogEntry]:
        """Retourne les N dernières entrées.

        Args:
            n: Nombre d'entrées à retourner.

        Returns:
            Liste des N dernières entrées.
        """
        async with self._lock:
            return list(self._entries)[-n:]


# Buffer global partagé entre le sink et l'écran
_global_buffer: LogsBuffer | None = None


def get_logs_buffer() -> LogsBuffer:
    """Retourne le buffer global de logs.

    Crée le buffer s'il n'existe pas encore.

    Returns:
        Instance de LogsBuffer.
    """
    global _global_buffer
    if _global_buffer is None:
        _global_buffer = LogsBuffer()
    return _global_buffer


def reset_logs_buffer() -> None:
    """Réinitialise le buffer global."""
    global _global_buffer
    _global_buffer = None


# ============================================================================
# SINK LOGURU — Capture des logs
# ============================================================================


class LogsSink:
    """Sink Loguru personnalisé qui capture les logs pour l'écran.

    Peut être installé comme handler Loguru pour intercepter tous les logs
    et les envoyer à l'écran via message Textual ou EventBus.

    Example:
        >>> from loguru import logger
        >>> sink = LogsSink()
        >>> logger.add(sink)
    """

    def __init__(
        self,
        *,
        buffer: LogsBuffer | None = None,
        emit_events: bool = True,
    ) -> None:
        """Initialise le sink.

        Args:
            buffer: Buffer de logs (défaut: buffer global).
            emit_events: Émettre des événements sur l'EventBus.
        """
        self._buffer = buffer or get_logs_buffer()
        self._emit_events = emit_events
        self._screen_ref: Any = None  # Référence à l'écran actif

    def set_screen(self, screen: Any) -> None:
        """Définit la référence à l'écran actif.

        Args:
            screen: Instance de LogsScreen.
        """
        self._screen_ref = screen

    def __call__(self, message: Any) -> None:
        """Appelé par Loguru pour chaque log.

        Args:
            message: Record Loguru.
        """
        try:
            record = message.record

            # Construire l'entrée de log
            entry = LogEntry(
                timestamp=record["time"].astimezone(UTC) if record["time"].tzinfo else record["time"].replace(tzinfo=UTC),
                level=record["level"].name,
                module=record["name"] or "",
                function=record["function"] or "",
                line=record["line"] or 0,
                message=str(record["message"]),
                exception=(
                    self._format_exception(record["exception"])
                    if record["exception"]
                    else None
                ),
                extra=dict(record.get("extra", {})),
            )

            # Ajouter au buffer (synchrone car deque est thread-safe)
            self._buffer._entries.append(entry)

            # Envoyer à l'écran si actif
            if self._screen_ref is not None:
                try:
                    from nexusdl.interfaces.cli.screens.logs import NewLogMessage
                    self._screen_ref.post_message(NewLogMessage(entry))
                except Exception:
                    pass

            # Émettre un événement si configuré
            if self._emit_events:
                try:
                    event_bus = get_event_bus()
                    # Émission non-bloquante
                    asyncio.create_task(
                        event_bus.emit(
                            EventType.LOG_MESSAGE,
                            payload={
                                "level": entry.level,
                                "module": entry.module,
                                "message": entry.message,
                                "timestamp": entry.timestamp.isoformat(),
                            },
                            source="core.logger.sink",
                        )
                    )
                except Exception:
                    pass

        except Exception as e:
            # Ne jamais faire échouer le logging
            try:
                import sys
                print(f"LogsSink error: {e}", file=sys.stderr)
            except Exception:
                pass

    @staticmethod
    def _format_exception(exception: Any) -> str:
        """Formate une exception en chaîne.

        Args:
            exception: Exception à formater.

        Returns:
            Chaîne formatée.
        """
        if exception is None:
            return ""

        try:
            import traceback

            if hasattr(exception, "traceback") and exception.traceback is not None:
                return "".join(
                    traceback.format_exception(
                        exception.type,
                        exception.value,
                        exception.traceback,
                    )
                )
            return str(exception)
        except Exception:
            return str(exception)


# Instance globale du sink
_logs_sink: LogsSink | None = None


def install_logs_sink(
    *,
    buffer: LogsBuffer | None = None,
    emit_events: bool = True,
) -> LogsSink:
    """Installe le sink de logs dans Loguru.

    Args:
        buffer: Buffer de logs (défaut: buffer global).
        emit_events: Émettre des événements sur l'EventBus.

    Returns:
        Instance du sink installé.
    """
    global _logs_sink

    sink = LogsSink(buffer=buffer, emit_events=emit_events)
    logger.add(sink, level="TRACE")
    _logs_sink = sink

    return sink


def uninstall_logs_sink() -> None:
    """Désinstalle le sink de logs."""
    global _logs_sink
    if _logs_sink is not None:
        logger.remove()
        _logs_sink = None


def get_logs_sink() -> LogsSink | None:
    """Retourne le sink de logs actif.

    Returns:
        Instance du sink ou None.
    """
    return _logs_sink


# ============================================================================
# WIDGETS CUSTOM — Composants de l'écran
# ============================================================================


if TEXTUAL_AVAILABLE:

    class LogEntryWidget(ListItem):
        """Widget pour afficher une entrée de log individuelle.

        Coloration syntaxique selon le niveau, avec support de la recherche
        et de l'exception.
        """

        DEFAULT_CSS = """
        LogEntryWidget {
            padding: 0 1;
            height: auto;
            min-height: 1;
        }
        LogEntryWidget > .log-line {
            width: 1fr;
        }
        LogEntryWidget.level-trace {
            opacity: 0.6;
        }
        LogEntryWidget.level-debug {
            opacity: 0.8;
        }
        LogEntryWidget.level-info {
            opacity: 1.0;
        }
        LogEntryWidget.level-success {
            opacity: 1.0;
        }
        LogEntryWidget.level-warning {
            background: $warning 10%;
        }
        LogEntryWidget.level-error {
            background: $error 15%;
        }
        LogEntryWidget.level-critical {
            background: $error 30%;
            text-style: bold;
        }
        LogEntryWidget .highlight {
            background: $accent 50%;
            text-style: bold;
        }
        LogEntryWidget .exception {
            color: $error;
            padding: 0 0 0 4;
        }
        """

        def __init__(
            self,
            entry: LogEntry,
            *,
            search_query: str = "",
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise le widget.

            Args:
                entry: Entrée de log à afficher.
                search_query: Texte de recherche à highlight.
                name: Nom du widget.
                id: ID du widget.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self.entry = entry
            self.search_query = search_query

            # Ajouter la classe de niveau
            self.add_class(f"level-{entry.level.lower()}")

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            # Construire la ligne principale
            timestamp = self.entry.formatted_timestamp
            level = f"{self.entry.level:<8}"
            icon = self.entry.level_icon
            module = self.entry.short_module or "unknown"
            location = f"{module}:{self.entry.function}:{self.entry.line}"
            message = self.entry.truncated_message

            # Appliquer le highlight si recherche active
            if self.search_query:
                message = self._highlight_text(message, self.search_query)

            line = f"[dim]{timestamp}[/dim] {icon} [{self.entry.level_color}]{level}[/] [dim]{location}[/] {message}"

            yield Static(line, classes="log-line", markup=True)

            # Afficher l'exception si présente
            if self.entry.exception:
                yield Static(self.entry.exception, classes="exception")

        def _highlight_text(self, text: str, query: str) -> str:
            """Applique le highlight sur le texte recherché.

            Args:
                text: Texte source.
                query: Texte à rechercher.

            Returns:
                Texte avec highlight.
            """
            if not query:
                return text

            # Échapper les caractères spéciaux regex
            escaped = re.escape(query)
            pattern = re.compile(escaped, re.IGNORECASE)

            def _replace(match: re.Match) -> str:
                return f"[highlight]{match.group(0)}[/highlight]"

            return pattern.sub(_replace, text)

        def update_search(self, query: str) -> None:
            """Met à jour la recherche et rafraîchit l'affichage.

            Args:
                query: Nouvelle requête de recherche.
            """
            self.search_query = query
            self.refresh()

    class LogsFilterBar(Widget):
        """Barre de filtrage des logs.

        Contient les contrôles pour filtrer par niveau, module, et texte.
        """

        DEFAULT_CSS = """
        LogsFilterBar {
            layout: horizontal;
            height: 3;
            padding: 0 1;
            border-bottom: solid $primary;
        }
        LogsFilterBar > Label {
            width: auto;
            content-align: left middle;
            padding: 0 1;
        }
        LogsFilterBar > Select {
            width: 15;
        }
        LogsFilterBar > Input {
            width: 1fr;
        }
        LogsFilterBar > .filter-switch {
            width: auto;
        }
        """

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise la barre de filtrage."""
            super().__init__(name=name, id=id, classes=classes)
            self._filter = LogsFilter()

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Label(t("logs.filter.level", default="Level:"))
            yield Select(
                [(level.value, level.label) for level in LogLevelFilter],
                id="level-select",
                value=LogLevelFilter.TRACE.value,
                allow_blank=False,
            )

            yield Label(t("logs.filter.module", default="Module:"))
            yield Input(
                placeholder=t("logs.filter.module.placeholder", default="e.g., core.*"),
                id="module-input",
            )

            yield Label(t("logs.filter.search", default="Search:"))
            yield Input(
                placeholder=t("logs.filter.search.placeholder", default="Search in logs..."),
                id="search-input",
            )

            yield Label(t("logs.filter.autoscroll", default="Auto-scroll:"))
            yield Switch(value=True, id="autoscroll-switch", classes="filter-switch")

        def get_filter(self) -> LogsFilter:
            """Récupère les filtres actuels.

            Returns:
                Instance de LogsFilter.
            """
            level_select = self.query_one("#level-select", Select)
            module_input = self.query_one("#module-input", Input)
            search_input = self.query_one("#search-input", Input)

            return LogsFilter(
                min_level=LogLevelFilter(level_select.value),
                module_pattern=module_input.value.strip(),
                text_search=search_input.value.strip(),
            )

        def get_auto_scroll(self) -> bool:
            """Récupère l'état de l'auto-scroll.

            Returns:
                True si auto-scroll activé.
            """
            switch = self.query_one("#autoscroll-switch", Switch)
            return switch.value

        def on_select_changed(self, event: Select.Changed) -> None:
            """Gère le changement de sélection."""
            if event.select.id == "level-select":
                self.post_message(FilterChanged(self.get_filter()))

        def on_input_changed(self, event: Input.Changed) -> None:
            """Gère le changement de texte."""
            if event.input.id in ("module-input", "search-input"):
                self.post_message(FilterChanged(self.get_filter()))

        def on_switch_changed(self, event: Switch.Changed) -> None:
            """Gère le changement de switch."""
            if event.switch.id == "autoscroll-switch":
                self.post_message(AutoScrollChanged(event.value))

    class LogsStatusBar(Widget):
        """Barre de statut avec statistiques et contrôles."""

        DEFAULT_CSS = """
        LogsStatusBar {
            layout: horizontal;
            height: 1;
            padding: 0 1;
            background: $primary-background;
        }
        LogsStatusBar > .stat-item {
            padding: 0 1;
        }
        LogsStatusBar > .stat-paused {
            color: $warning;
            text-style: bold;
        }
        LogsStatusBar > .stat-running {
            color: $success;
        }
        LogsStatusBar > .stat-count {
            color: $text-muted;
        }
        """

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise la barre de statut."""
            super().__init__(name=name, id=id, classes=classes)
            self._stats = LogsStats()
            self._paused = False
            self._displayed = 0
            self._total = 0

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static("⏸️ PAUSED", id="status-paused", classes="stat-paused")
            yield Static("▶ RUNNING", id="status-running", classes="stat-running")
            yield Static("", id="status-count", classes="stat-count")
            yield Static("", id="status-stats")

        def update_state(
            self,
            *,
            paused: bool,
            displayed: int,
            total: int,
            stats: LogsStats,
        ) -> None:
            """Met à jour l'état affiché.

            Args:
                paused: Si le flux est en pause.
                displayed: Nombre de logs affichés.
                total: Nombre total de logs.
                stats: Statistiques.
            """
            self._paused = paused
            self._displayed = displayed
            self._total = total
            self._stats = stats

            # Mettre à jour l'UI
            paused_widget = self.query_one("#status-paused", Static)
            running_widget = self.query_one("#status-running", Static)
            count_widget = self.query_one("#status-count", Static)
            stats_widget = self.query_one("#status-stats", Static)

            paused_widget.display = paused
            running_widget.display = not paused

            count_widget.update(
                t(
                    "logs.status.count",
                    default="Showing {displayed}/{total} logs",
                    displayed=displayed,
                    total=total,
                )
            )

            # Construire les stats par niveau
            stats_parts = []
            for level in ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
                count = stats.by_level.get(level, 0)
                if count > 0:
                    icon = LOG_LEVEL_ICONS.get(level, "?")
                    stats_parts.append(f"{icon}{count}")

            stats_widget.update(" | ".join(stats_parts) if stats_parts else "No logs yet")

    class LogsList(Widget):
        """Liste scrollable des logs."""

        DEFAULT_CSS = """
        LogsList {
            height: 1fr;
        }
        LogsList > ListView {
            height: 1fr;
        }
        LogsList > .empty-message {
            text-align: center;
            padding: 2;
            color: $text-muted;
        }
        """

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise la liste."""
            super().__init__(name=name, id=id, classes=classes)
            self._entries: list[LogEntry] = []
            self._search_query = ""

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(
                t("logs.empty", default="No logs to display"),
                id="logs-empty",
                classes="empty-message",
            )
            yield ListView(id="logs-list")

        def add_entry(self, entry: LogEntry) -> None:
            """Ajoute une entrée à la liste.

            Args:
                entry: Entrée à ajouter.
            """
            self._entries.append(entry)
            list_view = self.query_one("#logs-list", ListView)
            empty_msg = self.query_one("#logs-empty", Static)

            empty_msg.display = False
            list_view.display = True

            widget = LogEntryWidget(entry, search_query=self._search_query)
            list_view.append(widget)

        def set_entries(self, entries: list[LogEntry]) -> None:
            """Remplace toutes les entrées.

            Args:
                entries: Nouvelles entrées.
            """
            self._entries = entries
            list_view = self.query_one("#logs-list", ListView)
            empty_msg = self.query_one("#logs-empty", Static)

            list_view.clear()

            if not entries:
                empty_msg.display = True
                list_view.display = False
                return

            empty_msg.display = False
            list_view.display = True

            for entry in entries:
                widget = LogEntryWidget(entry, search_query=self._search_query)
                list_view.append(widget)

        def clear(self) -> None:
            """Vide la liste."""
            self._entries.clear()
            list_view = self.query_one("#logs-list", ListView)
            empty_msg = self.query_one("#logs-empty", Static)
            list_view.clear()
            empty_msg.display = True
            list_view.display = False

        def update_search(self, query: str) -> None:
            """Met à jour la recherche et rafraîchit tous les items.

            Args:
                query: Nouvelle requête de recherche.
            """
            self._search_query = query
            list_view = self.query_one("#logs-list", ListView)
            for child in list_view.children:
                if isinstance(child, LogEntryWidget):
                    child.update_search(query)

        def scroll_to_bottom(self) -> None:
            """Scroll vers le bas de la liste."""
            list_view = self.query_one("#logs-list", ListView)
            if list_view.children:
                list_view.scroll_end(animate=False)

        @property
        def count(self) -> int:
            """Nombre d'entrées affichées."""
            return len(self._entries)


# ============================================================================
# MESSAGES — Événements Textual
# ============================================================================


if TEXTUAL_AVAILABLE:

    class NewLogMessage(Message):
        """Message émis lorsqu'un nouveau log est reçu."""

        def __init__(self, entry: LogEntry) -> None:
            """Initialise le message.

            Args:
                entry: Entrée de log.
            """
            super().__init__()
            self.entry = entry

    class FilterChanged(Message):
        """Message émis lorsque les filtres changent."""

        def __init__(self, filter: LogsFilter) -> None:
            """Initialise le message.

            Args:
                filter: Nouveaux filtres.
            """
            super().__init__()
            self.filter = filter

    class AutoScrollChanged(Message):
        """Message émis lorsque l'auto-scroll change."""

        def __init__(self, enabled: bool) -> None:
            """Initialise le message.

            Args:
                enabled: Nouvel état.
            """
            super().__init__()
            self.enabled = enabled

    class LogsCleared(Message):
        """Message émis lorsque les logs sont effacés."""

    class LogsExported(Message):
        """Message émis lorsque les logs sont exportés."""

        def __init__(self, path: Path, count: int) -> None:
            """Initialise le message.

            Args:
                path: Chemin du fichier exporté.
                count: Nombre de logs exportés.
            """
            super().__init__()
            self.path = path
            self.count = count


# ============================================================================
# CLASSE PRINCIPALE — LogsScreen
# ============================================================================


if TEXTUAL_AVAILABLE:

    class LogsScreen(Screen):
        """Écran de visualisation des logs en temps réel.

        Permet à l'utilisateur de suivre l'activité de l'application,
        filtrer les logs, rechercher du texte, et exporter vers fichier.
        """

        # Bindings clavier
        BINDINGS = [
            Binding("p", "toggle_pause", "Pause/Resume"),
            Binding("c", "clear_logs", "Clear"),
            Binding("e", "export_logs", "Export"),
            Binding("/", "focus_search", "Search"),
            Binding("f", "focus_filter", "Filter"),
            Binding("a", "toggle_autoscroll", "Auto-scroll"),
            Binding("escape", "close", "Close"),
            Binding("ctrl+end", "scroll_bottom", "Scroll to Bottom"),
            Binding("ctrl+home", "scroll_top", "Scroll to Top"),
        ]

        # CSS de l'écran
        DEFAULT_CSS = """
        LogsScreen {
            layout: vertical;
        }

        #logs-container {
            height: 1fr;
        }

        #filter-bar {
            height: auto;
        }

        #logs-list-container {
            height: 1fr;
        }

        #status-bar {
            height: 1;
        }
        """

        # État réactif
        paused: reactive[bool] = reactive(False)
        auto_scroll: reactive[bool] = reactive(True)

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise l'écran.

            Args:
                name: Nom de l'écran.
                id: ID de l'écran.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self._state = LogsState()
            self._buffer = get_logs_buffer()
            self._stats_update_task: asyncio.Task[None] | None = None

        def compose(self) -> ComposeResult:
            """Compose l'écran."""
            yield Header(show_clock=True)

            # Barre de filtrage
            yield LogsFilterBar(id="filter-bar")

            # Liste des logs
            with Vertical(id="logs-container"):
                yield LogsList(id="logs-list-container")

            # Barre de statut
            yield LogsStatusBar(id="status-bar")

            yield Footer()

        def on_mount(self) -> None:
            """Appelé lors du montage."""
            # Charger les logs du buffer
            asyncio.create_task(self._load_initial_logs())

            # Démarrer la tâche de mise à jour des stats
            self._stats_update_task = asyncio.create_task(self._stats_update_loop())

            # Enregistrer cet écran auprès du sink
            sink = get_logs_sink()
            if sink is not None:
                sink.set_screen(self)

            logger.debug("LogsScreen monté")

        def on_unmount(self) -> None:
            """Appelé lors du démontage."""
            # Annuler la tâche de stats
            if self._stats_update_task is not None:
                self._stats_update_task.cancel()
                self._stats_update_task = None

            # Désenregistrer du sink
            sink = get_logs_sink()
            if sink is not None:
                sink.set_screen(None)

            logger.debug("LogsScreen démonté")

        # =====================================================================
        # CHARGEMENT INITIAL
        # =====================================================================

        async def _load_initial_logs(self) -> None:
            """Charge les logs initiaux depuis le buffer."""
            try:
                entries = await self._buffer.get_filtered(self._state.filter)
                logs_list = self.query_one("#logs-list-container", LogsList)
                logs_list.set_entries(entries)

                # Mettre à jour les stats
                self._state.stats.total_received = self._buffer.size
                self._state.stats.total_displayed = len(entries)
                self._state.stats.total_filtered = self._buffer.size - len(entries)

                # Calculer les compteurs par niveau
                for entry in entries:
                    self._state.stats.by_level[entry.level] = (
                        self._state.stats.by_level.get(entry.level, 0) + 1
                    )
                    self._state.stats.by_module[entry.module] = (
                        self._state.stats.by_module.get(entry.module, 0) + 1
                    )

                self._update_status_bar()

                if self.auto_scroll:
                    logs_list.scroll_to_bottom()

            except Exception as e:
                logger.error("Erreur lors du chargement des logs initiaux: {}", e)

        # =====================================================================
        # GESTION DES MESSAGES
        # =====================================================================

        def on_new_log_message(self, message: NewLogMessage) -> None:
            """Gère un nouveau log reçu.

            Args:
                message: Message avec l'entrée de log.
            """
            if self.paused:
                return

            entry = message.entry

            # Mettre à jour les stats
            self._state.stats.total_received += 1
            self._state.stats.by_level[entry.level] = (
                self._state.stats.by_level.get(entry.level, 0) + 1
            )
            self._state.stats.by_module[entry.module] = (
                self._state.stats.by_module.get(entry.module, 0) + 1
            )
            self._state.stats.last_log_at = entry.timestamp

            # Vérifier les filtres
            if not self._state.filter.matches(entry):
                self._state.stats.total_filtered += 1
                self._update_status_bar()
                return

            # Ajouter à la liste
            logs_list = self.query_one("#logs-list-container", LogsList)
            logs_list.add_entry(entry)
            self._state.stats.total_displayed += 1

            # Auto-scroll si activé
            if self.auto_scroll:
                logs_list.scroll_to_bottom()

            self._update_status_bar()

        def on_filter_changed(self, message: FilterChanged) -> None:
            """Gère un changement de filtres.

            Args:
                message: Message avec les nouveaux filtres.
            """
            self._state.filter = message.filter
            asyncio.create_task(self._apply_filters())

        def on_auto_scroll_changed(self, message: AutoScrollChanged) -> None:
            """Gère un changement d'auto-scroll.

            Args:
                message: Message avec le nouvel état.
            """
            self.auto_scroll = message.enabled
            if message.enabled:
                logs_list = self.query_one("#logs-list-container", LogsList)
                logs_list.scroll_to_bottom()

        # =====================================================================
        # FILTRAGE
        # =====================================================================

        async def _apply_filters(self) -> None:
            """Applique les filtres actuels aux logs."""
            try:
                entries = await self._buffer.get_filtered(self._state.filter)
                logs_list = self.query_one("#logs-list-container", LogsList)
                logs_list.set_entries(entries)

                self._state.stats.total_displayed = len(entries)
                self._state.stats.total_filtered = self._buffer.size - len(entries)
                self._update_status_bar()

                if self.auto_scroll:
                    logs_list.scroll_to_bottom()

            except Exception as e:
                logger.error("Erreur lors de l'application des filtres: {}", e)

        # =====================================================================
        # MISE À JOUR DES STATS
        # =====================================================================

        async def _stats_update_loop(self) -> None:
            """Boucle de mise à jour périodique des statistiques."""
            try:
                while True:
                    await asyncio.sleep(STATS_UPDATE_INTERVAL_MS / 1000)
                    self._update_status_bar()
            except asyncio.CancelledError:
                pass

        def _update_status_bar(self) -> None:
            """Met à jour la barre de statut."""
            status_bar = self.query_one("#status-bar", LogsStatusBar)
            status_bar.update_state(
                paused=self.paused,
                displayed=self._state.stats.total_displayed,
                total=self._state.stats.total_received,
                stats=self._state.stats,
            )

        # =====================================================================
        # ACTIONS — Bindings clavier
        # =====================================================================

        def action_toggle_pause(self) -> None:
            """Action : basculer la pause."""
            self.paused = not self.paused
            self._state.paused = self.paused

            if self.paused:
                self.notify(
                    t("logs.notify.paused", default="Logs paused"),
                    severity="information",
                )
            else:
                self.notify(
                    t("logs.notify.resumed", default="Logs resumed"),
                    severity="information",
                )
                # Rattraper les logs reçus pendant la pause
                asyncio.create_task(self._catch_up_logs())

            self._update_status_bar()

        async def _catch_up_logs(self) -> None:
            """Rattrape les logs reçus pendant la pause."""
            try:
                entries = await self._buffer.get_filtered(self._state.filter)
                logs_list = self.query_one("#logs-list-container", LogsList)

                # Remplacer toute la liste
                logs_list.set_entries(entries)

                self._state.stats.total_displayed = len(entries)
                self._update_status_bar()

                if self.auto_scroll:
                    logs_list.scroll_to_bottom()

            except Exception as e:
                logger.error("Erreur lors du rattrapage des logs: {}", e)

        def action_clear_logs(self) -> None:
            """Action : effacer les logs affichés."""
            logs_list = self.query_one("#logs-list-container", LogsList)
            logs_list.clear()

            # Réinitialiser les stats d'affichage
            self._state.stats.total_displayed = 0
            self._state.stats.by_level.clear()
            self._state.stats.by_module.clear()
            self._update_status_bar()

            self.post_message(LogsCleared())
            self.notify(
                t("logs.notify.cleared", default="Logs cleared"),
                severity="information",
            )

        def action_export_logs(self) -> None:
            """Action : exporter les logs vers un fichier."""
            asyncio.create_task(self._export_logs())

        async def _export_logs(self, format: ExportFormat = ExportFormat.TEXT) -> None:
            """Exporte les logs vers un fichier.

            Args:
                format: Format d'export.
            """
            try:
                # Obtenir les logs filtrés
                entries = await self._buffer.get_filtered(self._state.filter)

                if not entries:
                    self.notify(
                        t("logs.notify.no_logs_to_export", default="No logs to export"),
                        severity="warning",
                    )
                    return

                # Construire le chemin du fichier
                paths = get_paths()
                timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
                filename = f"nexusdl_logs_{timestamp}{format.extension}"
                export_path = paths.logs_dir / filename

                # Formater le contenu
                if format == ExportFormat.TEXT:
                    content = self._format_logs_text(entries)
                elif format == ExportFormat.JSON:
                    content = self._format_logs_json(entries)
                elif format == ExportFormat.CSV:
                    content = self._format_logs_csv(entries)
                else:
                    content = self._format_logs_text(entries)

                # Écrire le fichier
                await asyncio.to_thread(atomic_write, export_path, content)

                self.notify(
                    t(
                        "logs.notify.exported",
                        default="Exported {count} logs to {path}",
                        count=len(entries),
                        path=str(export_path),
                    ),
                    severity="information",
                )

                self.post_message(LogsExported(export_path, len(entries)))
                logger.info("Logs exportés vers: {}", export_path)

            except Exception as e:
                logger.error("Erreur lors de l'export des logs: {}", e)
                self.notify(
                    t(
                        "logs.notify.export_failed",
                        default="Export failed: {error}",
                        error=str(e),
                    ),
                    severity="error",
                )

        def _format_logs_text(self, entries: list[LogEntry]) -> str:
            """Formate les logs en texte lisible.

            Args:
                entries: Entrées à formater.

            Returns:
                Contenu texte.
            """
            lines = [
                f"# {APP_NAME} Logs Export",
                f"# Generated: {datetime.now(UTC).isoformat()}",
                f"# Total entries: {len(entries)}",
                "",
            ]
            for entry in entries:
                lines.append(entry.to_text())
            return "\n".join(lines)

        def _format_logs_json(self, entries: list[LogEntry]) -> str:
            """Formate les logs en JSON Lines.

            Args:
                entries: Entrées à formater.

            Returns:
                Contenu JSON Lines.
            """
            lines = []
            for entry in entries:
                lines.append(json.dumps(entry.to_dict(), ensure_ascii=False))
            return "\n".join(lines)

        def _format_logs_csv(self, entries: list[LogEntry]) -> str:
            """Formate les logs en CSV.

            Args:
                entries: Entrées à formater.

            Returns:
                Contenu CSV.
            """
            lines = ["timestamp,level,module,function,line,message"]
            for entry in entries:
                # Échapper les guillemets et virgules
                message = entry.message.replace('"', '""')
                line = (
                    f'"{entry.timestamp.isoformat()}",'
                    f'"{entry.level}",'
                    f'"{entry.module}",'
                    f'"{entry.function}",'
                    f'{entry.line},'
                    f'"{message}"'
                )
                lines.append(line)
            return "\n".join(lines)

        def action_focus_search(self) -> None:
            """Action : focus sur le champ de recherche."""
            search_input = self.query_one("#search-input", Input)
            search_input.focus()

        def action_focus_filter(self) -> None:
            """Action : focus sur le champ de filtre module."""
            module_input = self.query_one("#module-input", Input)
            module_input.focus()

        def action_toggle_autoscroll(self) -> None:
            """Action : basculer l'auto-scroll."""
            self.auto_scroll = not self.auto_scroll
            switch = self.query_one("#autoscroll-switch", Switch)
            switch.value = self.auto_scroll

            if self.auto_scroll:
                logs_list = self.query_one("#logs-list-container", LogsList)
                logs_list.scroll_to_bottom()
                self.notify(
                    t("logs.notify.autoscroll_enabled", default="Auto-scroll enabled"),
                    severity="information",
                )
            else:
                self.notify(
                    t("logs.notify.autoscroll_disabled", default="Auto-scroll disabled"),
                    severity="information",
                )

        def action_close(self) -> None:
            """Action : fermer l'écran."""
            self.app.pop_screen()

        def action_scroll_bottom(self) -> None:
            """Action : scroll vers le bas."""
            logs_list = self.query_one("#logs-list-container", LogsList)
            logs_list.scroll_to_bottom()

        def action_scroll_top(self) -> None:
            """Action : scroll vers le haut."""
            list_view = self.query_one("#logs-list", ListView)
            list_view.scroll_home(animate=False)


# ============================================================================
# HELPERS PUBLICS
# ============================================================================


def get_logs_buffer_size() -> int:
    """Retourne la taille actuelle du buffer de logs.

    Returns:
        Nombre d'entrées dans le buffer.
    """
    return get_logs_buffer().size


async def clear_logs_buffer() -> int:
    """Vide le buffer de logs.

    Returns:
        Nombre d'entrées supprimées.
    """
    return await get_logs_buffer().clear()


async def export_logs_to_file(
    path: Path,
    *,
    format: ExportFormat = ExportFormat.TEXT,
    filter: LogsFilter | None = None,
) -> int:
    """Exporte les logs du buffer vers un fichier.

    Fonction utilitaire pour usage externe (scripts, API).

    Args:
        path: Chemin du fichier de destination.
        format: Format d'export.
        filter: Filtres à appliquer (None = tous).

    Returns:
        Nombre de logs exportés.
    """
    buffer = get_logs_buffer()
    active_filter = filter or LogsFilter()

    entries = await buffer.get_filtered(active_filter)

    if format == ExportFormat.TEXT:
        lines = [entry.to_text() for entry in entries]
        content = "\n".join(lines)
    elif format == ExportFormat.JSON:
        lines = [json.dumps(entry.to_dict(), ensure_ascii=False) for entry in entries]
        content = "\n".join(lines)
    elif format == ExportFormat.CSV:
        lines = ["timestamp,level,module,function,line,message"]
        for entry in entries:
            message = entry.message.replace('"', '""')
            line = (
                f'"{entry.timestamp.isoformat()}",'
                f'"{entry.level}",'
                f'"{entry.module}",'
                f'"{entry.function}",'
                f'{entry.line},'
                f'"{message}"'
            )
            lines.append(line)
        content = "\n".join(lines)
    else:
        raise ValueError(f"Format non supporté: {format}")

    await asyncio.to_thread(atomic_write, path, content)
    return len(entries)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "MAX_BUFFER_SIZE",
    "MAX_MESSAGE_LENGTH",
    "LOG_LEVEL_COLORS",
    "LOG_LEVEL_ICONS",
    # Exceptions
    "LogsScreenError",
    "LogsExportError",
    # Enums
    "LogLevelFilter",
    "ExportFormat",
    # Modèles
    "LogEntry",
    "LogsFilter",
    "LogsStats",
    "LogsState",
    # Buffer
    "LogsBuffer",
    "get_logs_buffer",
    "reset_logs_buffer",
    # Sink
    "LogsSink",
    "install_logs_sink",
    "uninstall_logs_sink",
    "get_logs_sink",
    # Écran principal
    "LogsScreen" if TEXTUAL_AVAILABLE else None,
    # Helpers
    "get_logs_buffer_size",
    "clear_logs_buffer",
    "export_logs_to_file",
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
