"""Application principale de l'interface CLI NexusDL.

Ce module fournit le point d'entrée de l'interface CLI basée sur Textual.
Il orchestre tous les écrans, initialise les composants du core, gère le
cycle de vie de l'application, et définit les bindings globaux.

**Responsabilités** :
    - Initialisation complète au démarrage (paths, config, logger, i18n, etc.)
    - Gestion de la pile d'écrans (screen stack)
    - Bindings clavier globaux (F1, q, Ctrl+Q, etc.)
    - Thème CSS global
    - Gestion des erreurs non capturées
    - Intégration avec l'EventBus pour notifications
    - Gestion des signaux système (SIGINT, SIGTERM)
    - Mode daemon (background) optionnel
    - Accès aux composants via l'instance globale

**Écrans disponibles** :
    - MainScreen     : Dashboard principal (écran par défaut)
    - SearchScreen   : Recherche de mangas
    - LibraryScreen  : Bibliothèque locale
    - DownloadScreen : Gestion des téléchargements
    - SettingsScreen : Paramètres de l'application
    - LogsScreen     : Visualisation des logs
    - HelpScreen     : Aide et raccourcis

**Architecture** :
    NexusDLApp (textual.app.App)
        ├── Lifecycle
        │   ├── on_mount()      : Initialisation des composants
        │   ├── on_ready()      : Écran principal poussé
        │   └── on_unmount()    : Nettoyage des ressources
        │
        ├── Screen Stack
        │   ├── MainScreen      (écran par défaut)
        │   ├── SearchScreen    (pushed via 's')
        │   ├── LibraryScreen   (pushed via 'l')
        │   ├── DownloadScreen  (pushed via 'd')
        │   ├── SettingsScreen  (pushed via 'p')
        │   ├── LogsScreen      (pushed via 'g')
        │   └── HelpScreen      (pushed via 'F1' ou '?')
        │
        ├── Global Bindings
        │   ├── F1 / ?          : Aide
        │   ├── s               : Recherche
        │   ├── l               : Bibliothèque
        │   ├── d               : Téléchargements
        │   ├── p               : Paramètres
        │   ├── g               : Logs
        │   ├── r               : Refresh
        │   ├── q               : Quitter
        │   └── Ctrl+Q          : Quitter forcé
        │
        └── Global CSS
            └── Thème NexusDL (couleurs, styles)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.cli.app import NexusDLApp, run_app
    >>>
    >>> # Lancer l'application
    >>> run_app()
    >>>
    >>> # Ou avec configuration custom
    >>> app = NexusDLApp(theme="dark")
    >>> app.run()

Intégration :
    - core/config.py       : Configuration globale
    - core/logger.py       : Système de logging
    - core/i18n.py         : Internationalisation
    - core/events.py       : EventBus
    - core/paths.py        : Gestion des chemins
    - core/registry/       : Registre des sites
    - core/session/        : Sessions HTTP
    - interfaces/cli/screens/* : Tous les écrans
    - interfaces/cli/widgets/* : Tous les widgets
"""

from __future__ import annotations

import asyncio
import signal
import sys
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Final

from loguru import logger

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.css.query import NoMatches
    from textual.screen import Screen
    from textual.widgets import Footer, Header
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False

from nexusdl.core.constants import APP_NAME, APP_VERSION
from nexusdl.core.events import EventBus, EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import get_i18n_manager, setup_i18n, t
from nexusdl.core.logger import get_logger_manager, setup_logging
from nexusdl.core.paths import Paths, get_paths, paths


# ============================================================================
# CONSTANTES
# ============================================================================


# Version de l'interface CLI
CLI_VERSION: Final[str] = "0.1.0"

# Titre de l'application
APP_TITLE: Final[str] = f"{APP_NAME} v{APP_VERSION}"

# Sous-titre
APP_SUB_TITLE: Final[str] = t("app.subtitle", default="Your manga, your library, your way")

# Nom du fichier CSS global
GLOBAL_CSS_FILE: Final[str] = "global.css"


# ============================================================================
# EXCEPTIONS
# ============================================================================


class AppError(NexusDLError):
    """Exception de base pour les erreurs de l'application."""


class AppNotInitializedError(AppError):
    """Exception levée lorsque l'application n'est pas initialisée."""

    def __init__(self) -> None:
        super().__init__(
            "L'application n'est pas initialisée. "
            "Appelez run_app() ou app.run() pour démarrer."
        )


class ScreenNotFoundError(AppError):
    """Exception levée lorsqu'un écran est introuvable.

    Attributes:
        screen_name: Nom de l'écran recherché.
    """

    def __init__(self, screen_name: str) -> None:
        super().__init__(f"Écran introuvable: {screen_name}")
        self.screen_name = screen_name


class ComponentInitializationError(AppError):
    """Exception levée lorsqu'un composant ne peut être initialisé.

    Attributes:
        component: Nom du composant.
        reason: Raison de l'échec.
    """

    def __init__(self, component: str, reason: str = "") -> None:
        msg = f"Échec de l'initialisation du composant: {component}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.component = component
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class AppTheme(str, Enum):
    """Thèmes disponibles pour l'application.

    Attributes:
        LIGHT: Thème clair.
        DARK: Thème sombre.
        SYSTEM: Utiliser le thème du système.
        AUTO: Détection automatique.
    """

    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"
    AUTO = "auto"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            AppTheme.LIGHT: t("app.theme.light", default="Light"),
            AppTheme.DARK: t("app.theme.dark", default="Dark"),
            AppTheme.SYSTEM: t("app.theme.system", default="System"),
            AppTheme.AUTO: t("app.theme.auto", default="Auto"),
        }[self]


class AppState(str, Enum):
    """État de l'application.

    Attributes:
        NOT_STARTED: Application non démarrée.
        INITIALIZING: Initialisation en cours.
        RUNNING: Application en cours d'exécution.
        PAUSED: Application en pause.
        SHUTTING_DOWN: Arrêt en cours.
        STOPPED: Application arrêtée.
        ERROR: Erreur critique.
    """

    NOT_STARTED = "not_started"
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"
    ERROR = "error"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            AppState.NOT_STARTED: t("app.state.not_started", default="Not Started"),
            AppState.INITIALIZING: t("app.state.initializing", default="Initializing"),
            AppState.RUNNING: t("app.state.running", default="Running"),
            AppState.PAUSED: t("app.state.paused", default="Paused"),
            AppState.SHUTTING_DOWN: t("app.state.shutting_down", default="Shutting Down"),
            AppState.STOPPED: t("app.state.stopped", default="Stopped"),
            AppState.ERROR: t("app.state.error", default="Error"),
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            AppState.NOT_STARTED: "⏹️",
            AppState.INITIALIZING: "⏳",
            AppState.RUNNING: "▶️",
            AppState.PAUSED: "⏸️",
            AppState.SHUTTING_DOWN: "⏹️",
            AppState.STOPPED: "⏹️",
            AppState.ERROR: "❌",
        }[self]

    @property
    def is_active(self) -> bool:
        """Indique si l'application est active."""
        return self in (AppState.RUNNING, AppState.PAUSED)


# ============================================================================
# CSS GLOBAL — Thème NexusDL
# ============================================================================


GLOBAL_CSS: Final[str] = """
/* ============================================================================
   NEXUSDL — Thème global
   ============================================================================ */

/* Palette de couleurs */
$primary: #6366f1;
$secondary: #8b5cf6;
$accent: #ec4899;
$success: #10b981;
$warning: #f59e0b;
$error: #ef4444;
$info: #3b82f6;

/* Couleurs de fond */
$background: #0f172a;
$surface: #1e293b;
$primary-background: #1e1b4b;
$success-background: #064e3b;
$warning-background: #78350f;
$error-background: #7f1d1d;

/* Couleurs de texte */
$text: #f1f5f9;
$text-muted: #94a3b8;
$text-disabled: #64748b;

/* Application */
Screen {
    background: $background;
    color: $text;
}

Header {
    background: $primary-background;
    color: $text;
    dock: top;
}

Footer {
    background: $surface;
    color: $text-muted;
    dock: bottom;
}

/* Boutons */
Button {
    margin: 0 1;
}

Button.-primary {
    background: $primary;
    color: $text;
}

Button.-success {
    background: $success;
    color: $text;
}

Button.-warning {
    background: $warning;
    color: $text;
}

Button.-error {
    background: $error;
    color: $text;
}

/* Inputs */
Input {
    border: tall $primary;
    background: $surface;
    color: $text;
}

Input:focus {
    border: tall $accent;
}

/* Select */
Select {
    border: tall $primary;
    background: $surface;
    color: $text;
}

/* ListView */
ListView {
    background: $surface;
}

ListItem {
    padding: 0 1;
}

ListItem:hover {
    background: $primary-background;
}

ListItem.-selected {
    background: $primary 30%;
    border-left: thick $primary;
}

/* Labels */
Label {
    color: $text;
}

/* Static */
Static {
    color: $text;
}

/* Notifications */
Notification {
    background: $surface;
    border: tall $primary;
}

Notification.-information {
    border: tall $info;
}

Notification.-warning {
    border: tall $warning;
}

Notification.-error {
    border: tall $error;
}
"""


# ============================================================================
# CLASSE PRINCIPALE — NexusDLApp
# ============================================================================


if TEXTUAL_AVAILABLE:

    class NexusDLApp(App):
        """Application principale de NexusDL.

        Orchestre tous les écrans, initialise les composants du core,
        et gère le cycle de vie de l'application.
        """

        # Métadonnées de l'application
        TITLE = APP_TITLE
        SUB_TITLE = APP_SUB_TITLE
        CSS = GLOBAL_CSS

        # Bindings clavier globaux
        BINDINGS = [
            Binding("f1", "help", t("app.binding.help", default="Help"), show=True),
            Binding("?", "help", t("app.binding.help", default="Help"), show=False),
            Binding("s", "search", t("app.binding.search", default="Search"), show=True, priority=True),
            Binding("l", "library", t("app.binding.library", default="Library"), show=True, priority=True),
            Binding("d", "downloads", t("app.binding.downloads", default="Downloads"), show=True, priority=True),
            Binding("p", "settings", t("app.binding.settings", default="Settings"), show=True, priority=True),
            Binding("g", "logs", t("app.binding.logs", default="Logs"), show=True, priority=True),
            Binding("r", "refresh", t("app.binding.refresh", default="Refresh"), show=True),
            Binding("q", "quit", t("app.binding.quit", default="Quit"), show=True),
            Binding("ctrl+q", "force_quit", t("app.binding.force_quit", default="Force Quit"), show=False),
            Binding("ctrl+r", "reload_config", t("app.binding.reload_config", default="Reload Config"), show=False),
            Binding("ctrl+l", "clear_notifications", t("app.binding.clear_notifications", default="Clear"), show=False),
        ]

        # Instance globale de l'application
        _instance: ClassVar[NexusDLApp | None] = None

        def __init__(
            self,
            *,
            theme: AppTheme = AppTheme.DARK,
            config_path: Path | None = None,
            daemon: bool = False,
            **kwargs: Any,
        ) -> None:
            """Initialise l'application.

            Args:
                theme: Thème de l'application.
                config_path: Chemin vers le fichier de configuration.
                daemon: Si True, exécuter en mode daemon.
                **kwargs: Arguments additionnels pour textual.app.App.
            """
            super().__init__(**kwargs)

            self._theme = theme
            self._config_path = config_path
            self._daemon = daemon
            self._state = AppState.NOT_STARTED
            self._started_at: datetime | None = None
            self._event_bus: EventBus | None = None

            # Enregistrer l'instance globale
            NexusDLApp._instance = self

            # Configurer le thème
            self.theme = theme.value if theme != AppTheme.AUTO else "dark"

        # =====================================================================
        # LIFECYCLE
        # =====================================================================

        async def on_mount(self) -> None:
            """Appelé au montage de l'application.

            Initialise tous les composants du core dans l'ordre :
                1. Paths (gestion des chemins)
                2. Config (configuration)
                3. Logger (logging)
                4. I18n (internationalisation)
                5. EventBus (communication)
                6. Registry (sites)
            """
            self._state = AppState.INITIALIZING
            logger.info("Démarrage de {} v{}", APP_NAME, APP_VERSION)

            try:
                # 1. Initialiser les chemins
                await self._initialize_paths()

                # 2. Charger la configuration
                await self._initialize_config()

                # 3. Configurer le logging
                await self._initialize_logger()

                # 4. Configurer l'i18n
                await self._initialize_i18n()

                # 5. Démarrer l'EventBus
                await self._initialize_event_bus()

                # 6. Initialiser le registre des sites
                await self._initialize_registry()

                # 7. Installer le sink de logs pour l'écran logs
                await self._initialize_logs_sink()

                # 8. Configurer les gestionnaires de signaux
                self._setup_signal_handlers()

                self._state = AppState.RUNNING
                self._started_at = datetime.now(UTC)

                logger.info("Application initialisée avec succès")

                # Émettre un événement de démarrage
                if self._event_bus:
                    await self._event_bus.emit(
                        EventType.SESSION_STARTED,
                        payload={"app": APP_NAME, "version": APP_VERSION},
                        source="interfaces.cli.app",
                    )

            except Exception as e:
                self._state = AppState.ERROR
                logger.critical("Échec de l'initialisation de l'application: {}", e)
                self.notify(
                    t(
                        "app.error.initialization_failed",
                        default="Initialization failed: {error}",
                        error=str(e),
                    ),
                    severity="error",
                    timeout=10,
                )
                raise ComponentInitializationError("app", str(e)) from e

        async def on_ready(self) -> None:
            """Appelé lorsque l'application est prête.

            Pousse l'écran principal (MainScreen) sur la pile.
            """
            logger.debug("Application prête, affichage de l'écran principal")

            try:
                from nexusdl.interfaces.cli.screens.main import MainScreen
                await self.push_screen(MainScreen(id="main-screen"))
            except Exception as e:
                logger.error("Impossible de charger l'écran principal: {}", e)
                self.notify(
                    t(
                        "app.error.screen_load_failed",
                        default="Failed to load main screen: {error}",
                        error=str(e),
                    ),
                    severity="error",
                )

        async def on_unmount(self) -> None:
            """Appelé au démontage de l'application.

            Nettoie toutes les ressources dans l'ordre inverse de l'initialisation.
            """
            self._state = AppState.SHUTTING_DOWN
            logger.info("Arrêt de l'application...")

            try:
                # Émettre un événement d'arrêt
                if self._event_bus:
                    await self._event_bus.emit(
                        EventType.SESSION_STOPPED,
                        payload={"app": APP_NAME},
                        source="interfaces.cli.app",
                    )

                # Arrêter l'EventBus
                if self._event_bus:
                    await self._event_bus.stop()
                    self._event_bus = None

                # Désinstaller le sink de logs
                try:
                    from nexusdl.interfaces.cli.screens.logs import uninstall_logs_sink
                    uninstall_logs_sink()
                except Exception as e:
                    logger.debug("Erreur lors de la désinstallation du sink: {}", e)

                # Réinitialiser les composants
                try:
                    from nexusdl.core.config import reset_config_manager
                    reset_config_manager()
                except Exception:
                    pass

                try:
                    from nexusdl.core.i18n import reset_i18n
                    reset_i18n()
                except Exception:
                    pass

                try:
                    from nexusdl.core.logger import reset_logging
                    reset_logging()
                except Exception:
                    pass

                try:
                    from nexusdl.core.events import reset_event_bus
                    reset_event_bus()
                except Exception:
                    pass

                try:
                    from nexusdl.core.paths import reset_paths
                    reset_paths()
                except Exception:
                    pass

                self._state = AppState.STOPPED

                # Calculer la durée de session
                if self._started_at:
                    duration = datetime.now(UTC) - self._started_at
                    logger.info(
                        "Application arrêtée après {:.1f}s",
                        duration.total_seconds(),
                    )

            except Exception as e:
                logger.error("Erreur lors de l'arrêt de l'application: {}", e)

        # =====================================================================
        # INITIALISATION DES COMPOSANTS
        # =====================================================================

        async def _initialize_paths(self) -> None:
            """Initialise le gestionnaire de chemins."""
            try:
                # L'instance globale est déjà créée au premier import
                paths.initialize()
                logger.debug("Chemins initialisés: config={}, data={}", paths.config_dir, paths.data_dir)
            except Exception as e:
                raise ComponentInitializationError("paths", str(e)) from e

        async def _initialize_config(self) -> None:
            """Initialise la configuration."""
            try:
                from nexusdl.core.config import ConfigManager, set_config_manager

                manager = ConfigManager(config_path=self._config_path)
                set_config_manager(manager)

                logger.debug(
                    "Configuration chargée: language={}, theme={}",
                    manager.config.app.language,
                    manager.config.app.theme,
                )
            except Exception as e:
                raise ComponentInitializationError("config", str(e)) from e

        async def _initialize_logger(self) -> None:
            """Initialise le système de logging."""
            try:
                from nexusdl.core.config import get_config

                config = get_config()
                setup_logging(
                    level=config.logging.level,
                    format=config.logging.format,
                    log_dir=paths.logs_dir,
                    rotation=config.logging.rotation,
                    retention=config.logging.retention,
                    colorize=config.logging.colorize,
                )

                logger.debug("Logging initialisé: level={}, format={}", config.logging.level, config.logging.format)
            except Exception as e:
                raise ComponentInitializationError("logger", str(e)) from e

        async def _initialize_i18n(self) -> None:
            """Initialise le système d'internationalisation."""
            try:
                from nexusdl.core.config import get_config

                config = get_config()

                # Déterminer le répertoire des traductions
                translations_dir = None
                if config.i18n.translations_dir:
                    translations_dir = Path(config.i18n.translations_dir)
                else:
                    # Essayer de trouver data/translations dans le package
                    try:
                        import nexusdl
                        package_dir = Path(nexusdl.__file__).parent
                        default_dir = package_dir.parent / "data" / "translations"
                        if default_dir.exists():
                            translations_dir = default_dir
                    except Exception:
                        pass

                setup_i18n(
                    language=config.i18n.language,
                    fallback_language=config.i18n.fallback,
                    translations_dir=translations_dir,
                    auto_detect=config.i18n.auto_detect,
                )

                from nexusdl.core.i18n import get_current_language
                logger.debug("I18n initialisé: language={}", get_current_language())
            except Exception as e:
                raise ComponentInitializationError("i18n", str(e)) from e

        async def _initialize_event_bus(self) -> None:
            """Initialise l'EventBus."""
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

                self._event_bus = EventBus(config=bus_config)
                set_event_bus(self._event_bus)
                await self._event_bus.start()

                logger.debug("EventBus démarré: queue_size={}, workers={}", bus_config.queue_size, bus_config.worker_count)
            except Exception as e:
                raise ComponentInitializationError("event_bus", str(e)) from e

        async def _initialize_registry(self) -> None:
            """Initialise le registre des sites."""
            try:
                from nexusdl.core.registry import ConfigLoader, SchemaValidator, SiteRegistry

                loader = ConfigLoader()
                validator = SchemaValidator()
                registry = SiteRegistry(loader=loader, validator=validator, event_bus=self._event_bus)

                await loader.start()
                await validator.start()
                await registry.start()

                # Enregistrer le registre globalement
                from nexusdl.core.registry import set_site_registry
                set_site_registry(registry)

                logger.debug("Registre initialisé: {} sites", registry.sites_count)
            except Exception as e:
                # Le registre n'est pas critique, on continue sans
                logger.warning("Impossible d'initialiser le registre: {}. Certaines fonctionnalités seront indisponibles.", e)

        async def _initialize_logs_sink(self) -> None:
            """Installe le sink de logs pour l'écran logs."""
            try:
                from nexusdl.interfaces.cli.screens.logs import install_logs_sink
                install_logs_sink(emit_events=True)
                logger.debug("Sink de logs installé")
            except Exception as e:
                logger.warning("Impossible d'installer le sink de logs: {}", e)

        # =====================================================================
        # GESTION DES SIGNAUX
        # =====================================================================

        def _setup_signal_handlers(self) -> None:
            """Configure les gestionnaires de signaux système."""
            try:
                # SIGINT (Ctrl+C)
                if hasattr(signal, "SIGINT"):
                    signal.signal(signal.SIGINT, self._handle_sigint)

                # SIGTERM
                if hasattr(signal, "SIGTERM"):
                    signal.signal(signal.SIGTERM, self._handle_sigterm)

                logger.debug("Gestionnaires de signaux configurés")
            except Exception as e:
                logger.warning("Impossible de configurer les gestionnaires de signaux: {}", e)

        def _handle_sigint(self, signum: int, frame: Any) -> None:
            """Gère le signal SIGINT (Ctrl+C)."""
            logger.info("Signal SIGINT reçu, arrêt gracieux...")
            self.call_later(self.action_quit)

        def _handle_sigterm(self, signum: int, frame: Any) -> None:
            """Gère le signal SIGTERM."""
            logger.info("Signal SIGTERM reçu, arrêt gracieux...")
            self.call_later(self.action_quit)

        # =====================================================================
        # ACTIONS GLOBALES — Bindings clavier
        # =====================================================================

        def action_help(self) -> None:
            """Action : ouvrir l'écran d'aide."""
            try:
                from nexusdl.interfaces.cli.screens.help import HelpScreen
                self.push_screen(HelpScreen(id="help-screen"))
            except Exception as e:
                logger.error("Impossible d'ouvrir l'écran d'aide: {}", e)
                self.notify(
                    t("app.error.help_failed", default="Failed to open help: {error}", error=str(e)),
                    severity="error",
                )

        def action_search(self) -> None:
            """Action : ouvrir l'écran de recherche."""
            try:
                from nexusdl.interfaces.cli.screens.search import SearchScreen
                self.push_screen(SearchScreen(id="search-screen"))
            except Exception as e:
                logger.error("Impossible d'ouvrir l'écran de recherche: {}", e)
                self.notify(
                    t("app.error.search_failed", default="Failed to open search: {error}", error=str(e)),
                    severity="error",
                )

        def action_library(self) -> None:
            """Action : ouvrir l'écran de bibliothèque."""
            try:
                from nexusdl.interfaces.cli.screens.library import LibraryScreen
                self.push_screen(LibraryScreen(id="library-screen"))
            except Exception as e:
                logger.error("Impossible d'ouvrir l'écran de bibliothèque: {}", e)
                self.notify(
                    t("app.error.library_failed", default="Failed to open library: {error}", error=str(e)),
                    severity="error",
                )

        def action_downloads(self) -> None:
            """Action : ouvrir l'écran de téléchargements."""
            try:
                from nexusdl.interfaces.cli.screens.download import DownloadScreen
                self.push_screen(DownloadScreen(id="download-screen"))
            except Exception as e:
                logger.error("Impossible d'ouvrir l'écran de téléchargements: {}", e)
                self.notify(
                    t("app.error.downloads_failed", default="Failed to open downloads: {error}", error=str(e)),
                    severity="error",
                )

        def action_settings(self) -> None:
            """Action : ouvrir l'écran de paramètres."""
            try:
                from nexusdl.interfaces.cli.screens.settings import SettingsScreen
                self.push_screen(SettingsScreen(id="settings-screen"))
            except Exception as e:
                logger.error("Impossible d'ouvrir l'écran de paramètres: {}", e)
                self.notify(
                    t("app.error.settings_failed", default="Failed to open settings: {error}", error=str(e)),
                    severity="error",
                )

        def action_logs(self) -> None:
            """Action : ouvrir l'écran de logs."""
            try:
                from nexusdl.interfaces.cli.screens.logs import LogsScreen
                self.push_screen(LogsScreen(id="logs-screen"))
            except Exception as e:
                logger.error("Impossible d'ouvrir l'écran de logs: {}", e)
                self.notify(
                    t("app.error.logs_failed", default="Failed to open logs: {error}", error=str(e)),
                    severity="error",
                )

        def action_refresh(self) -> None:
            """Action : rafraîchir l'écran courant."""
            try:
                current_screen = self.screen
                if hasattr(current_screen, "_refresh_dashboard"):
                    asyncio.create_task(current_screen._refresh_dashboard())
                    self.notify(
                        t("app.notify.refreshed", default="Refreshed"),
                        severity="information",
                        timeout=2,
                    )
                elif hasattr(current_screen, "_load_tasks"):
                    asyncio.create_task(current_screen._load_tasks())
                    self.notify(
                        t("app.notify.refreshed", default="Refreshed"),
                        severity="information",
                        timeout=2,
                    )
                elif hasattr(current_screen, "_load_library"):
                    asyncio.create_task(current_screen._load_library())
                    self.notify(
                        t("app.notify.refreshed", default="Refreshed"),
                        severity="information",
                        timeout=2,
                    )
                else:
                    self.notify(
                        t("app.notify.no_refresh", default="No refresh available for this screen"),
                        severity="warning",
                        timeout=2,
                    )
            except Exception as e:
                logger.error("Erreur lors du rafraîchissement: {}", e)

        def action_quit(self) -> None:
            """Action : quitter l'application."""
            logger.info("Demande de fermeture de l'application")
            self.exit()

        def action_force_quit(self) -> None:
            """Action : forcer la fermeture de l'application."""
            logger.warning("Fermeture forcée de l'application")
            sys.exit(0)

        def action_reload_config(self) -> None:
            """Action : recharger la configuration."""
            try:
                from nexusdl.core.config import reload_config
                reload_config()
                self.notify(
                    t("app.notify.config_reloaded", default="Configuration reloaded"),
                    severity="information",
                )
                logger.info("Configuration rechargée")
            except Exception as e:
                logger.error("Erreur lors du rechargement de la configuration: {}", e)
                self.notify(
                    t("app.error.config_reload_failed", default="Failed to reload config: {error}", error=str(e)),
                    severity="error",
                )

        def action_clear_notifications(self) -> None:
            """Action : effacer toutes les notifications."""
            try:
                self.bell()  # Effet sonore pour confirmer
                self.notify(
                    t("app.notify.notifications_cleared", default="Notifications cleared"),
                    severity="information",
                    timeout=1,
                )
            except Exception as e:
                logger.debug("Erreur lors de l'effacement des notifications: {}", e)

        # =====================================================================
        # NAVIGATION ENTRE ÉCRANS
        # =====================================================================

        def go_to_main(self) -> None:
            """Retourne à l'écran principal."""
            try:
                # Fermer tous les écrans jusqu'à MainScreen
                while len(self.screen_stack) > 1:
                    self.pop_screen()
            except Exception as e:
                logger.error("Erreur lors du retour à l'écran principal: {}", e)

        def go_to_screen(self, screen_name: str) -> None:
            """Navigue vers un écran spécifique.

            Args:
                screen_name: Nom de l'écran (main, search, library, etc.).

            Raises:
                ScreenNotFoundError: Si l'écran n'existe pas.
            """
            screen_map = {
                "main": self.action_library,  # Pas d'action dédiée, on pousse MainScreen
                "search": self.action_search,
                "library": self.action_library,
                "downloads": self.action_downloads,
                "settings": self.action_settings,
                "logs": self.action_logs,
                "help": self.action_help,
            }

            action = screen_map.get(screen_name)
            if action is None:
                raise ScreenNotFoundError(screen_name)

            action()

        # =====================================================================
        # GESTION DES ÉVÉNEMENTS GLOBAUX
        # =====================================================================

        def on_key(self, event: Any) -> None:
            """Gère les événements clavier globaux."""
            # Log des touches pour debugging
            if logger.level("TRACE").enabled:
                logger.trace("Touche pressée: {}", event.key)

        def on_error(self, error: Exception) -> None:
            """Gère les erreurs non capturées.

            Args:
                error: Exception non capturée.
            """
            logger.error("Erreur non capturée: {}", error, exc_info=True)
            self.notify(
                t(
                    "app.error.unexpected",
                    default="Unexpected error: {error}",
                    error=str(error),
                ),
                severity="error",
                timeout=10,
            )

        # =====================================================================
        # API PUBLIQUE — Accès aux composants
        # =====================================================================

        @property
        def state(self) -> AppState:
            """État actuel de l'application."""
            return self._state

        @property
        def theme(self) -> AppTheme:
            """Thème de l'application."""
            return self._theme

        @property
        def event_bus(self) -> EventBus | None:
            """EventBus de l'application."""
            return self._event_bus

        @property
        def started_at(self) -> datetime | None:
            """Timestamp de démarrage."""
            return self._started_at

        @property
        def uptime_seconds(self) -> float:
            """Durée de fonctionnement en secondes."""
            if self._started_at is None:
                return 0.0
            return (datetime.now(UTC) - self._started_at).total_seconds()

        @property
        def current_screen_name(self) -> str:
            """Nom de l'écran courant."""
            try:
                screen = self.screen
                return screen.id or screen.__class__.__name__
            except Exception:
                return "unknown"

        def notify_user(
            self,
            message: str,
            *,
            severity: str = "information",
            timeout: int = 5,
        ) -> None:
            """Affiche une notification utilisateur.

            Args:
                message: Message à afficher.
                severity: Sévérité (information, warning, error).
                timeout: Durée d'affichage en secondes.
            """
            self.notify(message, severity=severity, timeout=timeout)

        def show_error(self, error: Exception | str) -> None:
            """Affiche une erreur à l'utilisateur.

            Args:
                error: Exception ou message d'erreur.
            """
            message = str(error)
            logger.error("Erreur affichée: {}", message)
            self.notify(message, severity="error", timeout=10)

        def show_success(self, message: str) -> None:
            """Affiche un message de succès.

            Args:
                message: Message à afficher.
            """
            self.notify(message, severity="information", timeout=3)


# ============================================================================
# INSTANCE GLOBALE ET FONCTIONS PRATIQUES
# ============================================================================


_app_instance: NexusDLApp | None = None


def get_app() -> NexusDLApp | None:
    """Retourne l'instance globale de l'application.

    Returns:
        Instance de NexusDLApp ou None.
    """
    if _app_instance is not None:
        return _app_instance
    if TEXTUAL_AVAILABLE:
        return NexusDLApp._instance
    return None


def set_app(app: NexusDLApp) -> None:
    """Définit l'instance globale de l'application.

    Args:
        app: Instance de NexusDLApp.
    """
    global _app_instance
    _app_instance = app
    if TEXTUAL_AVAILABLE:
        NexusDLApp._instance = app


def reset_app() -> None:
    """Réinitialise l'instance globale de l'application."""
    global _app_instance
    _app_instance = None
    if TEXTUAL_AVAILABLE:
        NexusDLApp._instance = None


def run_app(
    *,
    theme: AppTheme = AppTheme.DARK,
    config_path: Path | None = None,
    daemon: bool = False,
    headless: bool = False,
) -> None:
    """Lance l'application CLI.

    Fonction de haut niveau pour démarrer rapidement l'application.

    Args:
        theme: Thème de l'application.
        config_path: Chemin vers le fichier de configuration.
        daemon: Si True, exécuter en mode daemon.
        headless: Si True, exécuter sans interface (pour tests).

    Raises:
        AppError: Si Textual n'est pas disponible.
    """
    if not TEXTUAL_AVAILABLE:
        raise AppError(
            "Textual n'est pas installé. "
            "Installez-le avec: pip install textual"
        )

    # Vérifier la version de Python
    from nexusdl.core.constants import PYTHON_MIN_VERSION
    if sys.version_info < PYTHON_MIN_VERSION:
        print(
            f"Erreur: Python {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}+ est requis. "
            f"Vous utilisez Python {sys.version_info.major}.{sys.version_info.minor}.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Créer et lancer l'application
    app = NexusDLApp(
        theme=theme,
        config_path=config_path,
        daemon=daemon,
    )
    set_app(app)

    logger.info("Lancement de {} v{}", APP_NAME, APP_VERSION)

    try:
        if headless:
            # Mode headless pour les tests
            asyncio.run(app.run_async(headless=True))
        else:
            app.run()
    except KeyboardInterrupt:
        logger.info("Interruption clavier, arrêt de l'application")
    except Exception as e:
        logger.critical("Erreur fatale: {}", e, exc_info=True)
        print(f"Erreur fatale: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        reset_app()


def is_textual_available() -> bool:
    """Vérifie si Textual est disponible.

    Returns:
        True si Textual est installé.
    """
    return TEXTUAL_AVAILABLE


def get_textual_installation_instructions() -> str:
    """Retourne les instructions d'installation de Textual.

    Returns:
        Instructions d'installation.
    """
    return """
Pour utiliser l'interface CLI de NexusDL, vous devez installer Textual :

    pip install textual

Pour une expérience optimale, installez également les dépendances recommandées :

    pip install textual[syntax]

Après installation, relancez NexusDL.
""".strip()


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "CLI_VERSION",
    "APP_TITLE",
    "APP_SUB_TITLE",
    "GLOBAL_CSS_FILE",
    "GLOBAL_CSS",
    # Exceptions
    "AppError",
    "AppNotInitializedError",
    "ScreenNotFoundError",
    "ComponentInitializationError",
    # Enums
    "AppTheme",
    "AppState",
    # Classe principale
    "NexusDLApp" if TEXTUAL_AVAILABLE else None,
    # Instance globale
    "get_app",
    "set_app",
    "reset_app",
    # Fonctions
    "run_app",
    "is_textual_available",
    "get_textual_installation_instructions",
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
