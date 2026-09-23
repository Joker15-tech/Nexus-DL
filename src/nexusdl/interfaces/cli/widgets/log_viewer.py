"""Widget de visualisation des logs pour l'interface CLI NexusDL.

Ce module fournit un widget Textual réutilisable pour afficher les logs en
temps réel. Contrairement à l'écran complet `logs.py`, ce widget est conçu
pour être intégré dans d'autres écrans (dashboard, panneau latéral, etc.).

**Fonctionnalités** :
    - Affichage des logs en temps réel via LogsBuffer
    - Buffer circulaire (limite de mémoire configurable)
    - Filtrage par niveau minimum (TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL)
    - Auto-scroll configurable (activé/désactivé)
    - Coloration syntaxique par niveau
    - 3 modes d'affichage : COMPACT (1 ligne), NORMAL (2 lignes), DETAILED (multi-lignes)
    - Indicateur de pause/reprise
    - Compteur de logs affichés/filtrés
    - Bouton de clear (effacer les logs affichés)
    - Callbacks : on_log_added, on_cleared
    - Support des exceptions avec traceback
    - Troncature intelligente des messages longs
    - Traductions i18n
    - Gestion des erreurs

**Architecture** :
    LogViewer (Widget principal)
        ├── LogViewerHeader (titre + contrôles + stats)
        │   ├── Titre
        │   ├── Bouton pause/reprise
        │   ├── Bouton clear
        │   └── Compteur (affichés/total)
        ├── LogList (liste scrollable des logs)
        │   └── LogLine (ligne individuelle colorée)
        └── LogViewerFooter (filtre niveau + auto-scroll)

**Modes d'affichage** :
    - COMPACT : Une seule ligne par log (timestamp + niveau + message)
    - NORMAL : Deux lignes (timestamp + niveau + module | message)
    - DETAILED : Multi-lignes avec exception complète

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.cli.widgets.log_viewer import (
    ...     LogViewer, LogViewerMode,
    ... )
    >>>
    >>> # Dans un écran Textual
    >>> viewer = LogViewer(
    ...     title="Recent Logs",
    ...     mode=LogViewerMode.NORMAL,
    ...     max_lines=100,
    ...     auto_scroll=True,
    ... )
    >>> self.mount(viewer)
    >>>
    >>> # Le viewer se met à jour automatiquement via LogsBuffer

Intégration :
    - interfaces/cli/screens/logs.py : LogsBuffer, LogEntry
    - core/events.py : abonnement aux événements
    - core/i18n.py : traductions
    - core/utils/time.py : formatage des timestamps
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, ClassVar, Final

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

try:
    from textual.app import ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal, Vertical, VerticalScroll
    from textual.message import Message
    from textual.reactive import reactive
    from textual.widget import Widget
    from textual.widgets import Button, Label, Static, Switch
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False

from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t


# ============================================================================
# CONSTANTES
# ============================================================================


# Taille maximale par défaut du buffer de logs affichés
DEFAULT_MAX_LINES: Final[int] = 500

# Intervalle de mise à jour (ms)
UPDATE_INTERVAL_MS: Final[int] = 100

# Longueur maximale d'un message avant troncature
MAX_MESSAGE_LENGTH: Final[int] = 200

# Couleurs par niveau de log
LEVEL_COLORS: Final[dict[str, str]] = {
    "TRACE": "dim",
    "DEBUG": "blue",
    "INFO": "green",
    "SUCCESS": "bright_green",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bright_red",
}

# Icônes par niveau de log
LEVEL_ICONS: Final[dict[str, str]] = {
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


class LogViewerError(NexusDLError):
    """Exception de base pour les erreurs du viewer de logs."""


class LogBufferNotAvailableError(LogViewerError):
    """Exception levée lorsque le buffer de logs n'est pas disponible."""

    def __init__(self) -> None:
        super().__init__(
            "LogsBuffer n'est pas disponible. "
            "Assurez-vous que le système de logging est initialisé."
        )


# ============================================================================
# ENUMS
# ============================================================================


class LogViewerMode(str, Enum):
    """Mode d'affichage du viewer de logs.

    Attributes:
        COMPACT: Une seule ligne par log.
        NORMAL: Deux lignes par log.
        DETAILED: Multi-lignes avec exceptions complètes.
    """

    COMPACT = "compact"
    NORMAL = "normal"
    DETAILED = "detailed"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            LogViewerMode.COMPACT: t("log_viewer.mode.compact", default="Compact"),
            LogViewerMode.NORMAL: t("log_viewer.mode.normal", default="Normal"),
            LogViewerMode.DETAILED: t("log_viewer.mode.detailed", default="Detailed"),
        }[self]

    @property
    def line_height(self) -> int:
        """Hauteur d'une ligne de log dans ce mode."""
        return {
            LogViewerMode.COMPACT: 1,
            LogViewerMode.NORMAL: 2,
            LogViewerMode.DETAILED: 5,
        }[self]


class LogLevelFilter(str, Enum):
    """Filtre de niveau de log.

    Attributes:
        TRACE: Afficher tous les logs.
        DEBUG: Afficher DEBUG et au-dessus.
        INFO: Afficher INFO et au-dessus.
        WARNING: Afficher WARNING et au-dessus.
        ERROR: Afficher ERROR et au-dessus.
        CRITICAL: Afficher uniquement CRITICAL.
    """

    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
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
            LogLevelFilter.WARNING: 30,
            LogLevelFilter.ERROR: 40,
            LogLevelFilter.CRITICAL: 50,
        }[self]

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            LogLevelFilter.TRACE: t("log_viewer.filter.trace", default="Trace+"),
            LogLevelFilter.DEBUG: t("log_viewer.filter.debug", default="Debug+"),
            LogLevelFilter.INFO: t("log_viewer.filter.info", default="Info+"),
            LogLevelFilter.WARNING: t("log_viewer.filter.warning", default="Warning+"),
            LogLevelFilter.ERROR: t("log_viewer.filter.error", default="Error+"),
            LogLevelFilter.CRITICAL: t("log_viewer.filter.critical", default="Critical"),
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return LEVEL_ICONS.get(self.value, "?")

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


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class LogViewerConfig(BaseModel):
    """Configuration du viewer de logs.

    Attributes:
        title: Titre du viewer.
        mode: Mode d'affichage.
        max_lines: Nombre maximum de lignes affichées.
        auto_scroll: Activer l'auto-scroll.
        show_header: Afficher l'en-tête.
        show_footer: Afficher le pied de page.
        show_timestamp: Afficher le timestamp.
        show_level: Afficher le niveau.
        show_module: Afficher le module.
        show_location: Afficher la localisation (fichier:ligne).
        truncate_messages: Tronquer les messages longs.
        max_message_length: Longueur maximale des messages.
        update_interval_ms: Intervalle de mise à jour (ms).
        min_level: Niveau minimum à afficher.
        colorize: Activer la coloration.
        show_exceptions: Afficher les exceptions complètes.
    """

    title: str = Field(
        default="",
        description="Titre du viewer.",
    )
    mode: LogViewerMode = Field(
        default=LogViewerMode.NORMAL,
        description="Mode d'affichage.",
    )
    max_lines: int = Field(
        default=DEFAULT_MAX_LINES,
        ge=10,
        le=10000,
        description="Nombre maximum de lignes.",
    )
    auto_scroll: bool = Field(
        default=True,
        description="Activer l'auto-scroll.",
    )
    show_header: bool = Field(
        default=True,
        description="Afficher l'en-tête.",
    )
    show_footer: bool = Field(
        default=True,
        description="Afficher le pied de page.",
    )
    show_timestamp: bool = Field(
        default=True,
        description="Afficher le timestamp.",
    )
    show_level: bool = Field(
        default=True,
        description="Afficher le niveau.",
    )
    show_module: bool = Field(
        default=True,
        description="Afficher le module.",
    )
    show_location: bool = Field(
        default=False,
        description="Afficher la localisation.",
    )
    truncate_messages: bool = Field(
        default=True,
        description="Tronquer les messages longs.",
    )
    max_message_length: int = Field(
        default=MAX_MESSAGE_LENGTH,
        ge=20,
        le=1000,
        description="Longueur maximale des messages.",
    )
    update_interval_ms: int = Field(
        default=UPDATE_INTERVAL_MS,
        ge=50,
        le=5000,
        description="Intervalle de mise à jour (ms).",
    )
    min_level: LogLevelFilter = Field(
        default=LogLevelFilter.INFO,
        description="Niveau minimum à afficher.",
    )
    colorize: bool = Field(
        default=True,
        description="Activer la coloration.",
    )
    show_exceptions: bool = Field(
        default=True,
        description="Afficher les exceptions complètes.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class LogViewerState(BaseModel):
    """État du viewer de logs.

    Attributes:
        paused: Si True, le viewer est en pause.
        total_received: Nombre total de logs reçus.
        total_displayed: Nombre de logs affichés.
        total_filtered: Nombre de logs filtrés.
        last_log_at: Timestamp du dernier log.
        min_level: Niveau minimum actuel.
        auto_scroll: Auto-scroll activé.
    """

    paused: bool = Field(default=False, description="En pause.")
    total_received: int = Field(default=0, ge=0, description="Total reçus.")
    total_displayed: int = Field(default=0, ge=0, description="Total affichés.")
    total_filtered: int = Field(default=0, ge=0, description="Total filtrés.")
    last_log_at: datetime | None = Field(default=None, description="Dernier log.")
    min_level: LogLevelFilter = Field(default=LogLevelFilter.INFO, description="Niveau min.")
    auto_scroll: bool = Field(default=True, description="Auto-scroll.")

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# WIDGETS INTERNES — Composants du viewer
# ============================================================================


if TEXTUAL_AVAILABLE:

    class LogLine(Widget):
        """Widget pour afficher une ligne de log individuelle.

        Coloration syntaxique selon le niveau, avec support des exceptions.
        """

        DEFAULT_CSS = """
        LogLine {
            padding: 0 1;
            height: auto;
            min-height: 1;
        }
        LogLine > .log-content {
            width: 1fr;
        }
        LogLine.level-trace {
            opacity: 0.6;
        }
        LogLine.level-debug {
            opacity: 0.8;
        }
        LogLine.level-info {
            opacity: 1.0;
        }
        LogLine.level-success {
            opacity: 1.0;
        }
        LogLine.level-warning {
            background: $warning 10%;
        }
        LogLine.level-error {
            background: $error 15%;
        }
        LogLine.level-critical {
            background: $error 30%;
            text-style: bold;
        }
        LogLine .exception {
            color: $error;
            padding: 0 0 0 4;
        }
        """

        def __init__(
            self,
            log_entry: Any,  # LogEntry de screens/logs.py
            *,
            config: LogViewerConfig,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise la ligne de log.

            Args:
                log_entry: Entrée de log à afficher.
                config: Configuration du viewer.
                name: Nom du widget.
                id: ID du widget.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self._log_entry = log_entry
            self._config = config

            # Ajouter la classe de niveau
            self.add_class(f"level-{log_entry.level.lower()}")

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            entry = self._log_entry
            mode = self._config.mode

            if mode == LogViewerMode.COMPACT:
                yield from self._compose_compact(entry)
            elif mode == LogViewerMode.NORMAL:
                yield from self._compose_normal(entry)
            elif mode == LogViewerMode.DETAILED:
                yield from self._compose_detailed(entry)

        def _compose_compact(self, entry: Any) -> ComposeResult:
            """Compose le mode compact (une ligne)."""
            timestamp = entry.formatted_timestamp if self._config.show_timestamp else ""
            icon = LEVEL_ICONS.get(entry.level, "?") if self._config.show_level else ""
            message = entry.truncated_message if self._config.truncate_messages else entry.message

            # Tronquer si nécessaire
            if self._config.truncate_messages and len(message) > self._config.max_message_length:
                message = message[:self._config.max_message_length] + "..."

            line = f"[dim]{timestamp}[/dim] {icon} {message}"

            if self._config.colorize:
                color = LEVEL_COLORS.get(entry.level, "white")
                line = f"[{color}]{line}[/]"

            yield Static(line, classes="log-content", markup=True)

        def _compose_normal(self, entry: Any) -> ComposeResult:
            """Compose le mode normal (deux lignes)."""
            # Première ligne : timestamp + niveau + module
            timestamp = entry.formatted_timestamp if self._config.show_timestamp else ""
            icon = LEVEL_ICONS.get(entry.level, "?") if self._config.show_level else ""
            level = f"[{entry.level:<8}]" if self._config.show_level else ""
            module = entry.short_module if self._config.show_module else ""

            header = f"[dim]{timestamp}[/dim] {icon} {level} [dim]{module}[/dim]"

            if self._config.colorize:
                color = LEVEL_COLORS.get(entry.level, "white")
                header = f"[{color}]{header}[/]"

            yield Static(header, classes="log-content", markup=True)

            # Deuxième ligne : message
            message = entry.truncated_message if self._config.truncate_messages else entry.message

            if self._config.truncate_messages and len(message) > self._config.max_message_length:
                message = message[:self._config.max_message_length] + "..."

            yield Static(f"  {message}", classes="log-content")

        def _compose_detailed(self, entry: Any) -> ComposeResult:
            """Compose le mode détaillé (multi-lignes)."""
            # En-tête complet
            timestamp = entry.formatted_timestamp if self._config.show_timestamp else ""
            icon = LEVEL_ICONS.get(entry.level, "?") if self._config.show_level else ""
            level = f"[{entry.level:<8}]" if self._config.show_level else ""
            module = entry.module if self._config.show_module else ""
            location = f"{entry.function}:{entry.line}" if self._config.show_location else ""

            header = f"[dim]{timestamp}[/dim] {icon} {level} [dim]{module}[/dim] [dim]{location}[/dim]"

            if self._config.colorize:
                color = LEVEL_COLORS.get(entry.level, "white")
                header = f"[{color}]{header}[/]"

            yield Static(header, classes="log-content", markup=True)

            # Message complet (pas de troncature en mode détaillé)
            yield Static(f"  {entry.message}", classes="log-content")

            # Exception si présente
            if self._config.show_exceptions and entry.exception:
                yield Static(entry.exception, classes="exception")

    class LogViewerHeader(Widget):
        """En-tête du viewer de logs."""

        DEFAULT_CSS = """
        LogViewerHeader {
            layout: horizontal;
            height: 3;
            padding: 0 1;
            border-bottom: solid $primary;
        }
        LogViewerHeader > .title {
            width: 1fr;
            text-style: bold;
            content-align: left middle;
        }
        LogViewerHeader > .controls {
            width: auto;
            layout: horizontal;
        }
        LogViewerHeader > .controls > Button {
            margin: 0 1;
            min-width: 10;
        }
        LogViewerHeader > .stats {
            width: auto;
            content-align: right middle;
            padding: 0 1;
            color: $text-muted;
        }
        """

        def __init__(
            self,
            title: str = "",
            *,
            name: str | None = None,
            id: str | None = None,
        ) -> None:
            """Initialise l'en-tête."""
            super().__init__(name=name, id=id)
            self._title = title
            self._displayed = 0
            self._total = 0
            self._paused = False

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(self._title, classes="title")

            with Horizontal(classes="controls"):
                yield Button(
                    "⏸️ Pause" if not self._paused else "▶️ Resume",
                    id="viewer-pause-btn",
                    variant="warning" if not self._paused else "success",
                )
                yield Button(
                    "🗑️ Clear",
                    id="viewer-clear-btn",
                    variant="default",
                )

            yield Static("", id="viewer-stats", classes="stats")

        def update_stats(self, displayed: int, total: int, paused: bool) -> None:
            """Met à jour les statistiques.

            Args:
                displayed: Nombre de logs affichés.
                total: Nombre total de logs.
                paused: Si le viewer est en pause.
            """
            self._displayed = displayed
            self._total = total
            self._paused = paused

            stats_widget = self.query_one("#viewer-stats", Static)
            stats_widget.update(f"{displayed}/{total} logs")

            # Mettre à jour le bouton pause
            pause_btn = self.query_one("#viewer-pause-btn", Button)
            if paused:
                pause_btn.label = "▶️ Resume"
                pause_btn.variant = "success"
            else:
                pause_btn.label = "⏸️ Pause"
                pause_btn.variant = "warning"

    class LogViewerFooter(Widget):
        """Pied de page du viewer de logs avec contrôles."""

        DEFAULT_CSS = """
        LogViewerFooter {
            layout: horizontal;
            height: 3;
            padding: 0 1;
            border-top: solid $primary;
        }
        LogViewerFooter > Label {
            width: auto;
            content-align: left middle;
            padding: 0 1;
        }
        LogViewerFooter > Select {
            width: 15;
        }
        LogViewerFooter > .autoscroll-switch {
            width: auto;
        }
        """

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
        ) -> None:
            """Initialise le pied de page."""
            super().__init__(name=name, id=id)

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Label(t("log_viewer.footer.level", default="Level:"))
            from textual.widgets import Select
            yield Select(
                [(level.value, f"{level.icon} {level.label}") for level in LogLevelFilter],
                id="viewer-level-select",
                value=LogLevelFilter.INFO.value,
                allow_blank=False,
            )

            yield Label(t("log_viewer.footer.autoscroll", default="Auto-scroll:"))
            yield Switch(value=True, id="viewer-autoscroll-switch", classes="autoscroll-switch")


# ============================================================================
# MESSAGES — Événements Textual
# ============================================================================


if TEXTUAL_AVAILABLE:

    class LogAdded(Message):
        """Message émis lorsqu'un log est ajouté."""

        def __init__(self, count: int) -> None:
            """Initialise le message.

            Args:
                count: Nombre de logs ajoutés.
            """
            super().__init__()
            self.count = count

    class LogCleared(Message):
        """Message émis lorsque les logs sont effacés."""

    class LogLevelChanged(Message):
        """Message émis lorsque le niveau minimum change."""

        def __init__(self, level: LogLevelFilter) -> None:
            """Initialise le message.

            Args:
                level: Nouveau niveau minimum.
            """
            super().__init__()
            self.level = level


# ============================================================================
# CLASSE PRINCIPALE — LogViewer
# ============================================================================


if TEXTUAL_AVAILABLE:

    class LogViewer(Widget):
        """Widget de visualisation des logs en temps réel.

        Widget réutilisable affichant les logs depuis LogsBuffer avec
        filtrage, coloration, et auto-scroll.
        """

        # Bindings clavier
        BINDINGS = [
            Binding("p", "toggle_pause", "Pause/Resume"),
            Binding("c", "clear_logs", "Clear"),
            Binding("a", "toggle_autoscroll", "Auto-scroll"),
        ]

        # CSS du widget
        DEFAULT_CSS = """
        LogViewer {
            height: 1fr;
            layout: vertical;
            border: solid $primary;
        }
        LogViewer > #viewer-header {
            height: auto;
        }
        LogViewer > #viewer-content {
            height: 1fr;
        }
        LogViewer > #viewer-content > VerticalScroll {
            height: 1fr;
        }
        LogViewer > #viewer-content > .empty-message {
            text-align: center;
            padding: 2;
            color: $text-muted;
        }
        LogViewer > #viewer-footer {
            height: auto;
        }
        LogViewer.paused > #viewer-content {
            opacity: 0.7;
        }
        """

        # État réactif
        paused: reactive[bool] = reactive(False)
        auto_scroll: reactive[bool] = reactive(True)

        def __init__(
            self,
            *,
            title: str = "",
            config: LogViewerConfig | None = None,
            on_log_added: Callable[[int], None] | None = None,
            on_cleared: Callable[[], None] | None = None,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise le viewer.

            Args:
                title: Titre du viewer.
                config: Configuration du viewer.
                on_log_added: Callback lorsqu'un log est ajouté.
                on_cleared: Callback lorsque les logs sont effacés.
                name: Nom du widget.
                id: ID du widget.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self._config = config or LogViewerConfig(title=title)
            self._state = LogViewerState(
                min_level=self._config.min_level,
                auto_scroll=self._config.auto_scroll,
            )
            self._on_log_added = on_log_added
            self._on_cleared = on_cleared
            self._update_task: asyncio.Task[None] | None = None
            self._last_processed_index = 0

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            # En-tête
            if self._config.show_header:
                yield LogViewerHeader(
                    title=self._config.title,
                    id="viewer-header",
                )

            # Contenu
            with Vertical(id="viewer-content"):
                yield Static(
                    t("log_viewer.empty", default="No logs to display"),
                    id="viewer-empty",
                    classes="empty-message",
                )
                yield VerticalScroll(id="viewer-scroll")

            # Pied de page
            if self._config.show_footer:
                yield LogViewerFooter(id="viewer-footer")

        async def on_mount(self) -> None:
            """Appelé lors du montage."""
            # Démarrer la tâche de mise à jour
            self._update_task = asyncio.create_task(
                self._update_loop(),
                name=f"log_viewer_update_{self.id}",
            )

            # Charger les logs initiaux
            await self._load_initial_logs()

        async def on_unmount(self) -> None:
            """Appelé lors du démontage."""
            if self._update_task is not None:
                self._update_task.cancel()
                try:
                    await self._update_task
                except asyncio.CancelledError:
                    pass
                self._update_task = None

        # =====================================================================
        # CHARGEMENT DES LOGS
        # =====================================================================

        async def _load_initial_logs(self) -> None:
            """Charge les logs initiaux depuis le buffer."""
            try:
                from nexusdl.interfaces.cli.screens.logs import get_logs_buffer

                buffer = get_logs_buffer()
                entries = await buffer.get_all()

                # Filtrer par niveau
                filtered = [
                    entry for entry in entries
                    if self._state.min_level.includes(entry.level)
                ]

                # Limiter au max_lines
                if len(filtered) > self._config.max_lines:
                    filtered = filtered[-self._config.max_lines:]

                # Afficher
                await self._display_logs(filtered)

                self._state.total_received = len(entries)
                self._state.total_displayed = len(filtered)
                self._state.total_filtered = len(entries) - len(filtered)

                self._update_stats()

            except Exception as e:
                logger.error("Erreur lors du chargement des logs initiaux: {}", e)

        async def _update_loop(self) -> None:
            """Boucle de mise à jour périodique."""
            try:
                while True:
                    await asyncio.sleep(self._config.update_interval_ms / 1000)

                    if not self.paused:
                        await self._check_for_new_logs()

            except asyncio.CancelledError:
                pass

        async def _check_for_new_logs(self) -> None:
            """Vérifie s'il y a de nouveaux logs."""
            try:
                from nexusdl.interfaces.cli.screens.logs import get_logs_buffer

                buffer = get_logs_buffer()
                all_entries = await buffer.get_all()

                # Récupérer les nouveaux logs
                new_entries = all_entries[self._last_processed_index:]

                if not new_entries:
                    return

                self._last_processed_index = len(all_entries)

                # Filtrer par niveau
                filtered = [
                    entry for entry in new_entries
                    if self._state.min_level.includes(entry.level)
                ]

                if filtered:
                    await self._append_logs(filtered)

                    self._state.total_received += len(new_entries)
                    self._state.total_displayed += len(filtered)
                    self._state.total_filtered += len(new_entries) - len(filtered)

                    if filtered:
                        self._state.last_log_at = filtered[-1].timestamp

                    self._update_stats()

                    # Callback
                    if self._on_log_added is not None:
                        try:
                            self._on_log_added(len(filtered))
                        except Exception as e:
                            logger.error("Erreur dans le callback on_log_added: {}", e)

            except Exception as e:
                logger.error("Erreur lors de la vérification des nouveaux logs: {}", e)

        async def _display_logs(self, entries: list[Any]) -> None:
            """Affiche une liste de logs.

            Args:
                entries: Liste d'entrées de log.
            """
            scroll = self.query_one("#viewer-scroll", VerticalScroll)
            empty_msg = self.query_one("#viewer-empty", Static)

            scroll.remove_children()

            if not entries:
                empty_msg.display = True
                scroll.display = False
                return

            empty_msg.display = False
            scroll.display = True

            for entry in entries:
                line = LogLine(entry, config=self._config)
                scroll.mount(line)

            # Auto-scroll
            if self.auto_scroll:
                scroll.scroll_end(animate=False)

        async def _append_logs(self, entries: list[Any]) -> None:
            """Ajoute des logs à la fin.

            Args:
                entries: Liste d'entrées de log.
            """
            scroll = self.query_one("#viewer-scroll", VerticalScroll)
            empty_msg = self.query_one("#viewer-empty", Static)

            empty_msg.display = False
            scroll.display = True

            for entry in entries:
                line = LogLine(entry, config=self._config)
                scroll.mount(line)

            # Limiter le nombre de lignes
            children = list(scroll.children)
            if len(children) > self._config.max_lines:
                to_remove = children[:len(children) - self._config.max_lines]
                for child in to_remove:
                    child.remove()

            # Auto-scroll
            if self.auto_scroll:
                scroll.scroll_end(animate=False)

        # =====================================================================
        # CONTRÔLES
        # =====================================================================

        def _update_stats(self) -> None:
            """Met à jour les statistiques affichées."""
            if self._config.show_header:
                try:
                    header = self.query_one("#viewer-header", LogViewerHeader)
                    header.update_stats(
                        displayed=self._state.total_displayed,
                        total=self._state.total_received,
                        paused=self.paused,
                    )
                except Exception:
                    pass

        def action_toggle_pause(self) -> None:
            """Action : basculer la pause."""
            self.paused = not self.paused
            self._state.paused = self.paused

            if self.paused:
                self.add_class("paused")
            else:
                self.remove_class("paused")

            self._update_stats()

        def action_clear_logs(self) -> None:
            """Action : effacer les logs affichés."""
            scroll = self.query_one("#viewer-scroll", VerticalScroll)
            scroll.remove_children()

            empty_msg = self.query_one("#viewer-empty", Static)
            empty_msg.display = True
            scroll.display = False

            self._state.total_displayed = 0
            self._update_stats()

            self.post_message(LogCleared())

            if self._on_cleared is not None:
                try:
                    self._on_cleared()
                except Exception as e:
                    logger.error("Erreur dans le callback on_cleared: {}", e)

        def action_toggle_autoscroll(self) -> None:
            """Action : basculer l'auto-scroll."""
            self.auto_scroll = not self.auto_scroll
            self._state.auto_scroll = self.auto_scroll

            if self.auto_scroll:
                scroll = self.query_one("#viewer-scroll", VerticalScroll)
                scroll.scroll_end(animate=False)

        # =====================================================================
        # GESTION DES ÉVÉNEMENTS
        # =====================================================================

        def on_button_pressed(self, event: Button.Pressed) -> None:
            """Gère les clics sur les boutons."""
            if event.button.id == "viewer-pause-btn":
                self.action_toggle_pause()
            elif event.button.id == "viewer-clear-btn":
                self.action_clear_logs()

        def on_switch_changed(self, event: Switch.Changed) -> None:
            """Gère le changement de switch."""
            if event.switch.id == "viewer-autoscroll-switch":
                self.auto_scroll = event.value
                self._state.auto_scroll = event.value

                if event.value:
                    scroll = self.query_one("#viewer-scroll", VerticalScroll)
                    scroll.scroll_end(animate=False)

        def on_select_changed(self, event: Any) -> None:
            """Gère le changement de sélection."""
            if hasattr(event, "select") and event.select.id == "viewer-level-select":
                new_level = LogLevelFilter(event.value)
                self._state.min_level = new_level
                self._last_processed_index = 0  # Réinitialiser pour recharger

                # Recharger les logs avec le nouveau filtre
                asyncio.create_task(self._load_initial_logs())

                self.post_message(LogLevelChanged(new_level))

        # =====================================================================
        # API PUBLIQUE
        # =====================================================================

        @property
        def state(self) -> LogViewerState:
            """État actuel du viewer."""
            return self._state

        @property
        def config(self) -> LogViewerConfig:
            """Configuration du viewer."""
            return self._config

        def set_min_level(self, level: LogLevelFilter) -> None:
            """Définit le niveau minimum.

            Args:
                level: Nouveau niveau minimum.
            """
            self._state.min_level = level
            self._last_processed_index = 0
            asyncio.create_task(self._load_initial_logs())

        def set_auto_scroll(self, enabled: bool) -> None:
            """Définit l'auto-scroll.

            Args:
                enabled: True pour activer.
            """
            self.auto_scroll = enabled
            self._state.auto_scroll = enabled

            if enabled:
                scroll = self.query_one("#viewer-scroll", VerticalScroll)
                scroll.scroll_end(animate=False)


# ============================================================================
# HELPERS PUBLICS
# ============================================================================


def create_log_viewer(
    title: str = "",
    *,
    mode: LogViewerMode = LogViewerMode.NORMAL,
    max_lines: int = DEFAULT_MAX_LINES,
    auto_scroll: bool = True,
    min_level: LogLevelFilter = LogLevelFilter.INFO,
    **kwargs: Any,
) -> Any:
    """Crée un viewer de logs avec une configuration simplifiée.

    Fonction utilitaire pour créer rapidement un LogViewer.

    Args:
        title: Titre du viewer.
        mode: Mode d'affichage.
        max_lines: Nombre maximum de lignes.
        auto_scroll: Activer l'auto-scroll.
        min_level: Niveau minimum.
        **kwargs: Arguments additionnels.

    Returns:
        Instance de LogViewer.
    """
    config = LogViewerConfig(
        title=title,
        mode=mode,
        max_lines=max_lines,
        auto_scroll=auto_scroll,
        min_level=min_level,
        **kwargs,
    )
    return LogViewer(config=config)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_MAX_LINES",
    "UPDATE_INTERVAL_MS",
    "MAX_MESSAGE_LENGTH",
    "LEVEL_COLORS",
    "LEVEL_ICONS",
    # Exceptions
    "LogViewerError",
    "LogBufferNotAvailableError",
    # Enums
    "LogViewerMode",
    "LogLevelFilter",
    # Modèles
    "LogViewerConfig",
    "LogViewerState",
    # Helper
    "create_log_viewer",
    # Widget principal
    "LogViewer" if TEXTUAL_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
