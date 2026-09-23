"""Application principale de l'interface graphique NexusDL.

Ce module fournit le point d'entrée de l'interface graphique basée sur PyQt6.
Il orchestre toutes les vues, initialise les composants du core, gère le cycle
de vie de l'application, et définit la navigation globale.

**Responsabilités** :
    - Initialisation complète au démarrage (paths, config, logger, i18n, etc.)
    - Gestion de la pile de vues (navigation)
    - Raccourcis clavier globaux
    - Thème CSS global cyberpunk néon
    - Gestion des erreurs non capturées
    - Intégration avec l'EventBus pour notifications
    - Gestion de la fermeture gracieuse
    - Barre de menu et barre de statut
    - Système de notifications toast

**Vues disponibles** :
    - MainView     : Dashboard principal (vue par défaut)
    - SearchView   : Recherche de mangas
    - LibraryView  : Bibliothèque locale
    - DownloadView : Gestion des téléchargements
    - SettingsView : Paramètres

**Architecture** :
    NexusDLApp (QMainWindow)
        ├── MenuBar (Fichier, Édition, Affichage, Aide)
        ├── CentralWidget
        │   └── QStackedWidget (pile de vues)
        │       ├── MainView (index 0)
        │       ├── SearchView (index 1)
        │       ├── LibraryView (index 2)
        │       ├── DownloadView (index 3)
        │       └── SettingsView (index 4)
        ├── StatusBar (barre de statut)
        └── NotificationOverlay (notifications toast)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.gui.app import NexusDLApp, run_gui_app
    >>>
    >>> # Lancer l'application GUI
    >>> run_gui_app()
    >>>
    >>> # Ou avec configuration custom
    >>> app = NexusDLApp(config_path=Path("custom_config.yaml"))
    >>> app.run()

Intégration :
    - core/config.py       : Configuration globale
    - core/logger.py       : Système de logging
    - core/i18n.py         : Internationalisation
    - core/events.py       : EventBus pour communication
    - core/paths.py        : Gestion des chemins
    - core/registry/       : Registre des sites
    - core/session/        : Sessions HTTP
    - interfaces/gui/views/* : Toutes les vues
    - interfaces/gui/components/* : Composants réutilisables
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Final

from loguru import logger

try:
    from PyQt6.QtCore import (
        QPoint,
        QRect,
        QSize,
        Qt,
        QTimer,
        pyqtSignal,
        pyqtSlot,
    )
    from PyQt6.QtGui import (
        QAction,
        QColor,
        QFont,
        QIcon,
        QKeyEvent,
        QPalette,
    )
    from PyQt6.QtWidgets import (
        QApplication,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMenu,
        QMenuBar,
        QMessageBox,
        QPushButton,
        QSizePolicy,
        QStackedWidget,
        QStatusBar,
        QVBoxLayout,
        QWidget,
    )
    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False

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
from nexusdl.core.i18n import t
from nexusdl.core.paths import Paths, get_paths, paths


# ============================================================================
# CONSTANTES
# ============================================================================


# Version de l'interface GUI
GUI_VERSION: Final[str] = "0.1.0"

# Titre de la fenêtre
WINDOW_TITLE: Final[str] = f"{APP_NAME} v{APP_VERSION}"

# Dimensions par défaut de la fenêtre
DEFAULT_WINDOW_WIDTH: Final[int] = 1400
DEFAULT_WINDOW_HEIGHT: Final[int] = 900
MIN_WINDOW_WIDTH: Final[int] = 1024
MIN_WINDOW_HEIGHT: Final[int] = 700

# Couleurs du thème cyberpunk néon
COLOR_PRIMARY: Final[str] = "#00ff41"
COLOR_PRIMARY_DIM: Final[str] = "#00cc33"
COLOR_PRIMARY_BG: Final[str] = "#001a0d"
COLOR_SECONDARY: Final[str] = "#00ffff"
COLOR_ACCENT: Final[str] = "#ff00ff"
COLOR_SUCCESS: Final[str] = "#00ff41"
COLOR_WARNING: Final[str] = "#ffff00"
COLOR_ERROR: Final[str] = "#ff0040"
COLOR_INFO: Final[str] = "#00ffff"
COLOR_BACKGROUND: Final[str] = "#000000"
COLOR_SURFACE: Final[str] = "#0d1117"
COLOR_SURFACE_ALT: Final[str] = "#161b22"
COLOR_TEXT: Final[str] = "#00ff41"
COLOR_TEXT_BRIGHT: Final[str] = "#ffffff"
COLOR_TEXT_MUTED: Final[str] = "#00aaaa"
COLOR_TEXT_DIM: Final[str] = "#006666"
COLOR_BORDER: Final[str] = "#00ff41"
COLOR_BORDER_DIM: Final[str] = "#006622"

# Durée des notifications toast (ms)
NOTIFICATION_DURATION_MS: Final[int] = 5000
NOTIFICATION_MAX_VISIBLE: Final[int] = 3


# ============================================================================
# EXCEPTIONS
# ============================================================================


class GUIAppError(NexusDLError):
    """Exception de base pour les erreurs de l'application GUI."""


class AppNotInitializedError(GUIAppError):
    """Exception levée lorsque l'application n'est pas initialisée."""

    def __init__(self) -> None:
        super().__init__(
            "L'application n'est pas initialisée. "
            "Appelez run_gui_app() ou app.run() pour démarrer."
        )


class ComponentInitializationError(GUIAppError):
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


class AppView(str, Enum):
    """Vues disponibles dans l'application.

    Attributes:
        MAIN: Dashboard principal.
        SEARCH: Recherche de mangas.
        LIBRARY: Bibliothèque locale.
        DOWNLOADS: Gestion des téléchargements.
        SETTINGS: Paramètres.
    """

    MAIN = "main"
    SEARCH = "search"
    LIBRARY = "library"
    DOWNLOADS = "downloads"
    SETTINGS = "settings"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            AppView.MAIN: t("app.view.main", default="Dashboard"),
            AppView.SEARCH: t("app.view.search", default="Search"),
            AppView.LIBRARY: t("app.view.library", default="Library"),
            AppView.DOWNLOADS: t("app.view.downloads", default="Downloads"),
            AppView.SETTINGS: t("app.view.settings", default="Settings"),
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            AppView.MAIN: "🏠",
            AppView.SEARCH: "🔍",
            AppView.LIBRARY: "📚",
            AppView.DOWNLOADS: "📥",
            AppView.SETTINGS: "⚙️",
        }[self]

    @property
    def index(self) -> int:
        """Index dans le QStackedWidget."""
        return {
            AppView.MAIN: 0,
            AppView.SEARCH: 1,
            AppView.LIBRARY: 2,
            AppView.DOWNLOADS: 3,
            AppView.SETTINGS: 4,
        }[self]


class AppState(str, Enum):
    """État de l'application.

    Attributes:
        NOT_STARTED: Application non démarrée.
        INITIALIZING: Initialisation en cours.
        RUNNING: Application en cours d'exécution.
        SHUTTING_DOWN: Arrêt en cours.
        STOPPED: Application arrêtée.
        ERROR: Erreur critique.
    """

    NOT_STARTED = "not_started"
    INITIALIZING = "initializing"
    RUNNING = "running"
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
            AppState.SHUTTING_DOWN: t("app.state.shutting_down", default="Shutting Down"),
            AppState.STOPPED: t("app.state.stopped", default="Stopped"),
            AppState.ERROR: t("app.state.error", default="Error"),
        }[self]


class NotificationSeverity(str, Enum):
    """Sévérité d'une notification.

    Attributes:
        INFO: Information.
        SUCCESS: Succès.
        WARNING: Avertissement.
        ERROR: Erreur.
    """

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"

    @property
    def color(self) -> str:
        """Couleur associée."""
        return {
            NotificationSeverity.INFO: COLOR_INFO,
            NotificationSeverity.SUCCESS: COLOR_SUCCESS,
            NotificationSeverity.WARNING: COLOR_WARNING,
            NotificationSeverity.ERROR: COLOR_ERROR,
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            NotificationSeverity.INFO: "ℹ️",
            NotificationSeverity.SUCCESS: "✅",
            NotificationSeverity.WARNING: "⚠️",
            NotificationSeverity.ERROR: "❌",
        }[self]


# ============================================================================
# STYLESHEET GLOBAL — Thème cyberpunk néon
# ============================================================================


GLOBAL_STYLESHEET: Final[str] = f"""
/* ============================================================================
   NEXUSDL — Thème Global Cyberpunk Neon
   ============================================================================ */

/* Application */
QMainWindow {{
    background-color: {COLOR_BACKGROUND};
    color: {COLOR_TEXT};
}}

QWidget {{
    background-color: {COLOR_BACKGROUND};
    color: {COLOR_TEXT};
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 12px;
}}

/* Menu Bar */
QMenuBar {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT};
    border-bottom: 2px solid {COLOR_PRIMARY};
    padding: 2px;
}}

QMenuBar::item {{
    padding: 6px 12px;
    border-radius: 3px;
}}

QMenuBar::item:selected {{
    background-color: {COLOR_PRIMARY_BG};
    color: {COLOR_PRIMARY};
}}

QMenu {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER_DIM};
    border-radius: 4px;
    padding: 4px;
}}

QMenu::item {{
    padding: 6px 24px;
    border-radius: 3px;
}}

QMenu::item:selected {{
    background-color: {COLOR_PRIMARY_BG};
    color: {COLOR_PRIMARY};
}}

QMenu::separator {{
    height: 1px;
    background-color: {COLOR_BORDER_DIM};
    margin: 4px 8px;
}}

/* Status Bar */
QStatusBar {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT_MUTED};
    border-top: 2px solid {COLOR_BORDER_DIM};
    padding: 4px;
    font-size: 11px;
}}

/* Tooltips */
QToolTip {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_PRIMARY};
    border-radius: 3px;
    padding: 4px 8px;
    font-size: 11px;
}}

/* Scrollbars */
QScrollBar:vertical {{
    background-color: {COLOR_SURFACE};
    width: 8px;
    border: none;
}}

QScrollBar::handle:vertical {{
    background-color: {COLOR_BORDER_DIM};
    border-radius: 4px;
    min-height: 20px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {COLOR_PRIMARY};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}

QScrollBar:horizontal {{
    background-color: {COLOR_SURFACE};
    height: 8px;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background-color: {COLOR_BORDER_DIM};
    border-radius: 4px;
    min-width: 20px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {COLOR_PRIMARY};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

/* Message Box */
QMessageBox {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT};
}}

QMessageBox QLabel {{
    color: {COLOR_TEXT};
    font-size: 12px;
}}

QMessageBox QPushButton {{
    background-color: {COLOR_SURFACE_ALT};
    color: {COLOR_TEXT};
    border: 2px solid {COLOR_BORDER_DIM};
    border-radius: 4px;
    padding: 6px 16px;
    min-width: 80px;
}}

QMessageBox QPushButton:hover {{
    background-color: {COLOR_PRIMARY_BG};
    border-color: {COLOR_PRIMARY};
    color: {COLOR_PRIMARY};
}}
"""


# ============================================================================
# WIDGETS — Composants internes
# ============================================================================


if PYQT6_AVAILABLE:

    class NavigationBar(QFrame):
        """Barre de navigation avec boutons pour chaque vue."""

        # Signaux
        view_requested = pyqtSignal(object)  # AppView

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la barre de navigation."""
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedHeight(48)
            self.setStyleSheet(f"""
                NavigationBar {{
                    background-color: {COLOR_SURFACE};
                    border-bottom: 2px solid {COLOR_PRIMARY};
                }}
            """)

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(16, 4, 16, 4)
            layout.setSpacing(4)

            # Logo / Titre
            logo = QLabel(f"⚡ {APP_NAME}")
            logo.setStyleSheet(f"""
                color: {COLOR_PRIMARY};
                font-size: 16px;
                font-weight: bold;
                padding: 0 16px 0 0;
            """)
            layout.addWidget(logo)

            # Séparateur
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.VLine)
            sep.setStyleSheet(f"color: {COLOR_BORDER_DIM};")
            layout.addWidget(sep)

            # Boutons de navigation
            self._buttons: dict[AppView, QPushButton] = {}
            for view in AppView:
                button = QPushButton(f"{view.icon} {view.label}")
                button.setCheckable(True)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setStyleSheet(self._button_style())
                button.clicked.connect(lambda checked, v=view: self._on_view_clicked(v))
                layout.addWidget(button)
                self._buttons[view] = button

            layout.addStretch()

            # Horloge
            self._clock_label = QLabel("")
            self._clock_label.setStyleSheet(f"""
                color: {COLOR_SECONDARY};
                font-size: 12px;
                padding: 0 8px;
            """)
            layout.addWidget(self._clock_label)

            # Timer pour l'horloge
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._update_clock)
            self._timer.start(1000)
            self._update_clock()

            # Sélectionner la vue par défaut
            self.set_active_view(AppView.MAIN)

        def _button_style(self) -> str:
            """Retourne le style pour les boutons de navigation."""
            return f"""
                QPushButton {{
                    background-color: transparent;
                    color: {COLOR_TEXT_MUTED};
                    border: none;
                    border-radius: 4px;
                    padding: 8px 16px;
                    font-size: 12px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {COLOR_SECONDARY};
                }}
                QPushButton:checked {{
                    background-color: {COLOR_PRIMARY_BG};
                    color: {COLOR_PRIMARY};
                    border-bottom: 2px solid {COLOR_PRIMARY};
                }}
            """

        def _update_clock(self) -> None:
            """Met à jour l'horloge."""
            now = datetime.now()
            self._clock_label.setText(now.strftime("%H:%M:%S"))

        def _on_view_clicked(self, view: AppView) -> None:
            """Gère le clic sur un bouton de navigation.

            Args:
                view: Vue demandée.
            """
            self.set_active_view(view)
            self.view_requested.emit(view)

        def set_active_view(self, view: AppView) -> None:
            """Définit la vue active.

            Args:
                view: Vue active.
            """
            for v, button in self._buttons.items():
                button.setChecked(v == view)

    class NotificationToast(QFrame):
        """Notification toast individuelle."""

        # Signaux
        dismissed = pyqtSignal()

        def __init__(
            self,
            message: str,
            *,
            severity: NotificationSeverity = NotificationSeverity.INFO,
            duration_ms: int = NOTIFICATION_DURATION_MS,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la notification.

            Args:
                message: Message à afficher.
                severity: Sévérité de la notification.
                duration_ms: Durée d'affichage en ms.
                parent: Widget parent.
            """
            super().__init__(parent)
            self._duration_ms = duration_ms

            # Configuration visuelle
            self.setFixedHeight(40)
            self.setMinimumWidth(300)
            self.setMaximumWidth(500)
            self.setStyleSheet(f"""
                NotificationToast {{
                    background-color: {COLOR_SURFACE};
                    border: 2px solid {severity.color};
                    border-radius: 6px;
                }}
            """)

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(12, 8, 12, 8)
            layout.setSpacing(8)

            # Icône
            icon_label = QLabel(severity.icon)
            icon_label.setStyleSheet(f"font-size: 16px;")
            layout.addWidget(icon_label)

            # Message
            msg_label = QLabel(message)
            msg_label.setStyleSheet(f"""
                color: {COLOR_TEXT_BRIGHT};
                font-size: 12px;
            """)
            msg_label.setWordWrap(True)
            layout.addWidget(msg_label, stretch=1)

            # Bouton fermer
            btn_close = QPushButton("✕")
            btn_close.setFixedSize(20, 20)
            btn_close.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: {COLOR_TEXT_MUTED};
                    border: none;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    color: {COLOR_ERROR};
                }}
            """)
            btn_close.clicked.connect(self.dismiss)
            layout.addWidget(btn_close)

            # Timer d'auto-dismiss
            self._dismiss_timer = QTimer(self)
            self._dismiss_timer.setSingleShot(True)
            self._dismiss_timer.timeout.connect(self.dismiss)
            self._dismiss_timer.start(duration_ms)

        def dismiss(self) -> None:
            """Ferme la notification."""
            self._dismiss_timer.stop()
            self.dismissed.emit()
            self.deleteLater()

    class NotificationOverlay(QWidget):
        """Overlay de notifications toast."""

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise l'overlay."""
            super().__init__(parent)

            # Configuration
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.setStyleSheet("background: transparent;")

            # Layout vertical (les notifications s'empilent de haut en bas)
            self._layout = QVBoxLayout(self)
            self._layout.setContentsMargins(16, 16, 16, 16)
            self._layout.setSpacing(8)
            self._layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

            # Notifications actives
            self._notifications: list[NotificationToast] = []

        def show_notification(
            self,
            message: str,
            *,
            severity: NotificationSeverity = NotificationSeverity.INFO,
            duration_ms: int = NOTIFICATION_DURATION_MS,
        ) -> None:
            """Affiche une notification toast.

            Args:
                message: Message à afficher.
                severity: Sévérité.
                duration_ms: Durée d'affichage.
            """
            # Limiter le nombre de notifications visibles
            while len(self._notifications) >= NOTIFICATION_MAX_VISIBLE:
                oldest = self._notifications.pop(0)
                oldest.dismiss()

            toast = NotificationToast(
                message,
                severity=severity,
                duration_ms=duration_ms,
                parent=self,
            )
            toast.dismissed.connect(lambda t=toast: self._remove_notification(t))
            self._layout.addWidget(toast)
            self._notifications.append(toast)

        def _remove_notification(self, toast: NotificationToast) -> None:
            """Retire une notification.

            Args:
                toast: Notification à retirer.
            """
            if toast in self._notifications:
                self._notifications.remove(toast)
            self._layout.removeWidget(toast)

        def clear_all(self) -> None:
            """Ferme toutes les notifications."""
            for toast in list(self._notifications):
                toast.dismiss()
            self._notifications.clear()


# ============================================================================
# CLASSE PRINCIPALE — NexusDLApp
# ============================================================================


if PYQT6_AVAILABLE:

    class NexusDLApp(QMainWindow):
        """Application principale de NexusDL.

        Orchestre toutes les vues, initialise les composants du core,
        et gère le cycle de vie de l'application.

        Signals:
            view_changed(AppView): Émis lorsque la vue active change.
            app_started(): Émis lorsque l'application est démarrée.
            app_stopped(): Émis lorsque l'application est arrêtée.
        """

        # Signaux
        view_changed = pyqtSignal(object)  # AppView
        app_started = pyqtSignal()
        app_stopped = pyqtSignal()

        # Instance globale
        _instance: ClassVar[NexusDLApp | None] = None

        def __init__(
            self,
            *,
            config_path: Path | None = None,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise l'application.

            Args:
                config_path: Chemin vers le fichier de configuration.
                parent: Widget parent.
            """
            super().__init__(parent)

            self._config_path = config_path
            self._state = AppState.NOT_STARTED
            self._started_at: datetime | None = None
            self._current_view = AppView.MAIN
            self._event_bus: EventBus | None = None

            # Enregistrer l'instance globale
            NexusDLApp._instance = self

            # Configuration de la fenêtre
            self.setWindowTitle(WINDOW_TITLE)
            self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
            self.resize(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)

            # Appliquer le stylesheet global
            self.setStyleSheet(GLOBAL_STYLESHEET)

            # Créer l'interface
            self._setup_ui()

            # Créer les menus
            self._setup_menus()

            # Créer la barre de statut
            self._setup_status_bar()

        def _setup_ui(self) -> None:
            """Configure l'interface utilisateur."""
            # Widget central
            central_widget = QWidget()
            central_widget.setStyleSheet(f"background-color: {COLOR_BACKGROUND};")
            self.setCentralWidget(central_widget)

            # Layout principal
            layout = QVBoxLayout(central_widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            # Barre de navigation
            self._nav_bar = NavigationBar(parent=self)
            self._nav_bar.view_requested.connect(self._on_view_requested)
            layout.addWidget(self._nav_bar)

            # Pile de vues
            self._stack = QStackedWidget()
            self._stack.setStyleSheet(f"background-color: {COLOR_BACKGROUND};")
            layout.addWidget(self._stack, stretch=1)

            # Créer et ajouter les vues
            from nexusdl.interfaces.gui.views import (
                DownloadView,
                LibraryView,
                MainView,
                SearchView,
                SettingsView,
            )

            self._main_view = MainView(parent=self)
            self._main_view.navigate_requested.connect(self._on_navigate_from_main)
            self._stack.addWidget(self._main_view)

            self._search_view = SearchView(parent=self)
            self._search_view.mangas_selected.connect(self._on_mangas_selected)
            self._stack.addWidget(self._search_view)

            self._library_view = LibraryView(parent=self)
            self._library_view.read_requested.connect(self._on_read_requested)
            self._library_view.download_requested.connect(self._on_download_requested)
            self._library_view.delete_requested.connect(self._on_delete_requested)
            self._library_view.scan_requested.connect(self._on_scan_requested)
            self._stack.addWidget(self._library_view)

            self._download_view = DownloadView(parent=self)
            self._download_view.task_action.connect(self._on_task_action)
            self._stack.addWidget(self._download_view)

            self._settings_view = SettingsView(parent=self)
            self._settings_view.settings_saved.connect(self._on_settings_saved)
            self._stack.addWidget(self._settings_view)

            # Overlay de notifications
            self._notification_overlay = NotificationOverlay(parent=self)
            self._notification_overlay.raise_()

        def _setup_menus(self) -> None:
            """Configure les menus."""
            menubar = self.menuBar()

            # Menu Fichier
            file_menu = menubar.addMenu(t("app.menu.file", default="File"))

            action_settings = QAction(
                t("app.menu.settings", default="Settings"),
                self,
            )
            action_settings.setShortcut("Ctrl+,")
            action_settings.triggered.connect(lambda: self.navigate_to(AppView.SETTINGS))
            file_menu.addAction(action_settings)

            file_menu.addSeparator()

            action_quit = QAction(
                t("app.menu.quit", default="Quit"),
                self,
            )
            action_quit.setShortcut("Ctrl+Q")
            action_quit.triggered.connect(self.close)
            file_menu.addAction(action_quit)

            # Menu Édition
            edit_menu = menubar.addMenu(t("app.menu.edit", default="Edit"))

            action_refresh = QAction(
                t("app.menu.refresh", default="Refresh"),
                self,
            )
            action_refresh.setShortcut("F5")
            action_refresh.triggered.connect(self._on_refresh)
            edit_menu.addAction(action_refresh)

            # Menu Affichage
            view_menu = menubar.addMenu(t("app.menu.view", default="View"))

            for view in AppView:
                action = QAction(f"{view.icon} {view.label}", self)
                action.triggered.connect(lambda checked, v=view: self.navigate_to(v))
                view_menu.addAction(action)

            # Menu Aide
            help_menu = menubar.addMenu(t("app.menu.help", default="Help"))

            action_about = QAction(
                t("app.menu.about", default="About"),
                self,
            )
            action_about.triggered.connect(self._show_about)
            help_menu.addAction(action_about)

        def _setup_status_bar(self) -> None:
            """Configure la barre de statut."""
            status_bar = self.statusBar()

            # Label de statut
            self._status_label = QLabel(t("app.status.ready", default="Ready"))
            self._status_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
            status_bar.addWidget(self._status_label, stretch=1)

            # Label de version
            version_label = QLabel(f"{APP_NAME} v{APP_VERSION}")
            version_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 10px;")
            status_bar.addPermanentWidget(version_label)

        # =====================================================================
        # LIFECYCLE
        # =====================================================================

        async def initialize(self) -> None:
            """Initialise tous les composants du core."""
            self._state = AppState.INITIALIZING
            self._update_status(t("app.status.initializing", default="Initializing..."))
            logger.info("Démarrage de {} v{} (GUI)", APP_NAME, APP_VERSION)

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

                # 7. Installer le sink de logs
                await self._initialize_logs_sink()

                self._state = AppState.RUNNING
                self._started_at = datetime.now(UTC)

                self._update_status(t("app.status.ready", default="Ready"))
                self.app_started.emit()

                # Charger le dashboard
                asyncio.create_task(self._main_view.load_library())

                logger.info("Application GUI initialisée avec succès")

                # Notification de bienvenue
                self.show_notification(
                    t("app.notification.welcome", default="Welcome to NexusDL!"),
                    severity=NotificationSeverity.SUCCESS,
                )

            except Exception as e:
                self._state = AppState.ERROR
                logger.critical("Échec de l'initialisation: {}", e)
                self._update_status(t("app.status.error", default="Error"))
                self.show_notification(
                    t("app.notification.init_failed", default="Initialization failed: {error}", error=str(e)),
                    severity=NotificationSeverity.ERROR,
                    duration_ms=10000,
                )

        async def _initialize_paths(self) -> None:
            """Initialise le gestionnaire de chemins."""
            try:
                paths_instance = get_paths()
                if not paths_instance.is_initialized:
                    paths_instance.initialize()
                logger.debug("Chemins initialisés")
            except Exception as e:
                raise ComponentInitializationError("paths", str(e)) from e

        async def _initialize_config(self) -> None:
            """Initialise la configuration."""
            try:
                from nexusdl.core.config import ConfigManager, set_config_manager

                manager = ConfigManager(config_path=self._config_path)
                set_config_manager(manager)
                logger.debug("Configuration chargée")
            except Exception as e:
                raise ComponentInitializationError("config", str(e)) from e

        async def _initialize_logger(self) -> None:
            """Initialise le système de logging."""
            try:
                from nexusdl.core.config import get_config
                from nexusdl.core.logger import setup_logging

                config = get_config()
                setup_logging(
                    level=config.logging.level,
                    format=config.logging.format,
                    log_dir=get_paths().logs_dir,
                    colorize=config.logging.colorize,
                )
                logger.debug("Logging initialisé")
            except Exception as e:
                raise ComponentInitializationError("logger", str(e)) from e

        async def _initialize_i18n(self) -> None:
            """Initialise le système d'internationalisation."""
            try:
                from nexusdl.core.config import get_config
                from nexusdl.core.i18n import setup_i18n

                config = get_config()

                translations_dir = None
                if config.i18n.translations_dir:
                    translations_dir = Path(config.i18n.translations_dir)
                else:
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
                    translations_dir=translations_dir,
                )
                logger.debug("I18n initialisé")
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
                logger.debug("EventBus démarré")
            except Exception as e:
                raise ComponentInitializationError("event_bus", str(e)) from e

        async def _initialize_registry(self) -> None:
            """Initialise le registre des sites."""
            try:
                from nexusdl.core.registry import (
                    ConfigLoader,
                    SchemaValidator,
                    SiteRegistry,
                    set_site_registry,
                )

                loader = ConfigLoader()
                validator = SchemaValidator()
                registry = SiteRegistry(
                    loader=loader,
                    validator=validator,
                    event_bus=self._event_bus,
                )

                await loader.start()
                await validator.start()
                await registry.start()

                set_site_registry(registry)
                logger.debug("Registre initialisé: {} sites", registry.sites_count)
            except Exception as e:
                logger.warning("Impossible d'initialiser le registre: {}", e)

        async def _initialize_logs_sink(self) -> None:
            """Installe le sink de logs."""
            try:
                from nexusdl.interfaces.cli.screens.logs import install_logs_sink
                install_logs_sink(emit_events=True)
                logger.debug("Sink de logs installé")
            except Exception as e:
                logger.warning("Impossible d'installer le sink de logs: {}", e)

        async def shutdown(self) -> None:
            """Arrête l'application et nettoie les ressources."""
            self._state = AppState.SHUTTING_DOWN
            self._update_status(t("app.status.shutting_down", default="Shutting down..."))
            logger.info("Arrêt de l'application GUI...")

            try:
                # Émettre un événement d'arrêt
                if self._event_bus:
                    await self._event_bus.emit(
                        EventType.SESSION_STOPPED,
                        payload={"app": APP_NAME},
                        source="interfaces.gui.app",
                    )

                # Arrêter l'EventBus
                if self._event_bus:
                    await self._event_bus.stop()
                    self._event_bus = None

                # Désinstaller le sink de logs
                try:
                    from nexusdl.interfaces.cli.screens.logs import uninstall_logs_sink
                    uninstall_logs_sink()
                except Exception:
                    pass

                # Réinitialiser les composants
                for reset_func_name in [
                    "reset_config_manager",
                    "reset_i18n",
                    "reset_logging",
                    "reset_event_bus",
                    "reset_paths",
                ]:
                    try:
                        module_name = reset_func_name.replace("reset_", "")
                        if module_name == "config_manager":
                            from nexusdl.core.config import reset_config_manager
                            reset_config_manager()
                        elif module_name == "i18n":
                            from nexusdl.core.i18n import reset_i18n
                            reset_i18n()
                        elif module_name == "logging":
                            from nexusdl.core.logger import reset_logging
                            reset_logging()
                        elif module_name == "event_bus":
                            from nexusdl.core.events import reset_event_bus
                            reset_event_bus()
                        elif module_name == "paths":
                            from nexusdl.core.paths import reset_paths
                            reset_paths()
                    except Exception:
                        pass

                self._state = AppState.STOPPED
                self.app_stopped.emit()

                if self._started_at:
                    duration = datetime.now(UTC) - self._started_at
                    logger.info("Application arrêtée après {:.1f}s", duration.total_seconds())

            except Exception as e:
                logger.error("Erreur lors de l'arrêt: {}", e)

        def closeEvent(self, event: Any) -> None:
            """Gère la fermeture de la fenêtre."""
            reply = QMessageBox.question(
                self,
                t("app.confirm.quit.title", default="Quit NexusDL"),
                t("app.confirm.quit.message", default="Are you sure you want to quit?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )

            if reply == QMessageBox.StandardButton.Yes:
                asyncio.create_task(self._shutdown_and_close())
                event.accept()
            else:
                event.ignore()

        async def _shutdown_and_close(self) -> None:
            """Arrête l'application puis ferme la fenêtre."""
            await self.shutdown()
            self.deleteLater()
            QApplication.quit()

        # =====================================================================
        # NAVIGATION
        # =====================================================================

        def navigate_to(self, view: AppView) -> None:
            """Navigue vers une vue spécifique.

            Args:
                view: Vue à afficher.
            """
            self._current_view = view
            self._stack.setCurrentIndex(view.index)
            self._nav_bar.set_active_view(view)
            self._update_status(f"{view.icon} {view.label}")
            self.view_changed.emit(view)

            logger.debug("Navigation vers: {}", view.value)

        def _on_view_requested(self, view: AppView) -> None:
            """Gère une demande de navigation depuis la barre de navigation.

            Args:
                view: Vue demandée.
            """
            self.navigate_to(view)

        def _on_navigate_from_main(self, action: Any) -> None:
            """Gère une demande de navigation depuis le dashboard.

            Args:
                action: Action QuickAction.
            """
            from nexusdl.interfaces.gui.views.main_view import QuickAction

            view_map = {
                QuickAction.SEARCH: AppView.SEARCH,
                QuickAction.LIBRARY: AppView.LIBRARY,
                QuickAction.DOWNLOADS: AppView.DOWNLOADS,
                QuickAction.SETTINGS: AppView.SETTINGS,
            }

            view = view_map.get(action)
            if view:
                self.navigate_to(view)

        # =====================================================================
        # GESTION DES ÉVÉNEMENTS DES VUES
        # =====================================================================

        def _on_mangas_selected(self, manga_ids: list[str]) -> None:
            """Gère la sélection de mangas pour téléchargement.

            Args:
                manga_ids: IDs des mangas sélectionnés.
            """
            self.show_notification(
                t(
                    "app.notification.download_queued",
                    default="{count} manga(s) queued for download",
                    count=len(manga_ids),
                ),
                severity=NotificationSeverity.INFO,
            )
            # TODO: Ajouter les tâches au DownloadManager
            self.navigate_to(AppView.DOWNLOADS)

        def _on_read_requested(self, manga_id: str) -> None:
            """Gère une demande de lecture.

            Args:
                manga_id: ID du manga.
            """
            self.show_notification(
                t("app.notification.opening_reader", default="Opening reader..."),
                severity=NotificationSeverity.INFO,
            )
            # TODO: Ouvrir le lecteur

        def _on_download_requested(self, chapter_ids: list[str]) -> None:
            """Gère une demande de téléchargement de chapitres.

            Args:
                chapter_ids: IDs des chapitres à télécharger.
            """
            self.show_notification(
                t(
                    "app.notification.downloading_chapters",
                    default="Downloading {count} chapter(s)",
                    count=len(chapter_ids),
                ),
                severity=NotificationSeverity.INFO,
            )
            # TODO: Ajouter les tâches au DownloadManager

        def _on_delete_requested(self, manga_id: str) -> None:
            """Gère une demande de suppression.

            Args:
                manga_id: ID du manga.
            """
            self.show_notification(
                t("app.notification.manga_deleted", default="Manga deleted"),
                severity=NotificationSeverity.WARNING,
            )
            # TODO: Supprimer le manga de la base de données

        def _on_scan_requested(self) -> None:
            """Gère une demande de scan de bibliothèque."""
            self.show_notification(
                t("app.notification.scanning", default="Scanning library..."),
                severity=NotificationSeverity.INFO,
            )
            # TODO: Lancer le scan

        def _on_task_action(self, task_id: str, action: str) -> None:
            """Gère une action sur une tâche de téléchargement.

            Args:
                task_id: ID de la tâche.
                action: Action à effectuer.
            """
            logger.info("Action {} sur tâche {}", action, task_id)
            # TODO: Implémenter les actions sur les tâches

        def _on_settings_saved(self) -> None:
            """Gère la sauvegarde des paramètres."""
            self.show_notification(
                t("app.notification.settings_saved", default="Settings saved successfully"),
                severity=NotificationSeverity.SUCCESS,
            )

        def _on_refresh(self) -> None:
            """Gère le rafraîchissement."""
            current = self._current_view
            if current == AppView.MAIN:
                asyncio.create_task(self._main_view.load_library())
            elif current == AppView.LIBRARY:
                asyncio.create_task(self._library_view.load_library())
            elif current == AppView.DOWNLOADS:
                asyncio.create_task(self._download_view._load_tasks())

            self.show_notification(
                t("app.notification.refreshed", default="Refreshed"),
                severity=NotificationSeverity.INFO,
                duration_ms=2000,
            )

        # =====================================================================
        # NOTIFICATIONS
        # =====================================================================

        def show_notification(
            self,
            message: str,
            *,
            severity: NotificationSeverity = NotificationSeverity.INFO,
            duration_ms: int = NOTIFICATION_DURATION_MS,
        ) -> None:
            """Affiche une notification toast.

            Args:
                message: Message à afficher.
                severity: Sévérité.
                duration_ms: Durée d'affichage.
            """
            self._notification_overlay.show_notification(
                message,
                severity=severity,
                duration_ms=duration_ms,
            )

        # =====================================================================
        # DIVERS
        # =====================================================================

        def _update_status(self, text: str) -> None:
            """Met à jour le texte de la barre de statut.

            Args:
                text: Texte à afficher.
            """
            self._status_label.setText(text)

        def _show_about(self) -> None:
            """Affiche la boîte de dialogue À propos."""
            QMessageBox.about(
                self,
                t("app.about.title", default="About NexusDL"),
                f"<h2 style='color: {COLOR_PRIMARY};'>{APP_NAME} v{APP_VERSION}</h2>"
                f"<p>{APP_DESCRIPTION}</p>"
                f"<p><b>Author:</b> {APP_AUTHOR}</p>"
                f"<p><b>Website:</b> <a href='{APP_URL}'>{APP_URL}</a></p>"
                f"<p><b>Python:</b> {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}</p>"
                f"<p style='color: {COLOR_TEXT_MUTED};'>Your manga, your library, your way.</p>",
            )

        def keyPressEvent(self, event: QKeyEvent) -> None:
            """Gère les raccourcis clavier globaux."""
            key = event.key()
            modifiers = event.modifiers()

            # Ctrl+Q : Quitter
            if key == Qt.Key.Key_Q and modifiers & Qt.KeyboardModifier.ControlModifier:
                self.close()
            # F1 : Aide (About)
            elif key == Qt.Key.Key_F1:
                self._show_about()
            # F5 : Rafraîchir
            elif key == Qt.Key.Key_F5:
                self._on_refresh()
            # Ctrl+1-5 : Navigation
            elif modifiers & Qt.KeyboardModifier.ControlModifier:
                view_map = {
                    Qt.Key.Key_1: AppView.MAIN,
                    Qt.Key.Key_2: AppView.SEARCH,
                    Qt.Key.Key_3: AppView.LIBRARY,
                    Qt.Key.Key_4: AppView.DOWNLOADS,
                    Qt.Key.Key_5: AppView.SETTINGS,
                }
                view = view_map.get(key)
                if view:
                    self.navigate_to(view)
            else:
                super().keyPressEvent(event)

        def resizeEvent(self, event: Any) -> None:
            """Gère le redimensionnement de la fenêtre."""
            super().resizeEvent(event)
            # Repositionner l'overlay de notifications
            self._notification_overlay.setGeometry(
                self.width() - 520,
                60,
                500,
                self.height() - 100,
            )

        # =====================================================================
        # API PUBLIQUE
        # =====================================================================

        def run(self) -> None:
            """Lance l'application (bloque jusqu'à la fermeture)."""
            # Initialiser les composants
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.initialize())

            # Afficher la fenêtre
            self.show()

        @property
        def state(self) -> AppState:
            """État actuel de l'application."""
            return self._state

        @property
        def current_view(self) -> AppView:
            """Vue actuellement affichée."""
            return self._current_view

        @property
        def uptime_seconds(self) -> float:
            """Durée de fonctionnement en secondes."""
            if self._started_at is None:
                return 0.0
            return (datetime.now(UTC) - self._started_at).total_seconds()


# ============================================================================
# INSTANCE GLOBALE ET FONCTIONS PRATIQUES
# ============================================================================


_app_instance: NexusDLApp | None = None


def get_gui_app() -> NexusDLApp | None:
    """Retourne l'instance globale de l'application GUI.

    Returns:
        Instance de NexusDLApp ou None.
    """
    if _app_instance is not None:
        return _app_instance
    if PYQT6_AVAILABLE:
        return NexusDLApp._instance
    return None


def set_gui_app(app: NexusDLApp) -> None:
    """Définit l'instance globale de l'application GUI.

    Args:
        app: Instance de NexusDLApp.
    """
    global _app_instance
    _app_instance = app
    if PYQT6_AVAILABLE:
        NexusDLApp._instance = app


def reset_gui_app() -> None:
    """Réinitialise l'instance globale de l'application GUI."""
    global _app_instance
    _app_instance = None
    if PYQT6_AVAILABLE:
        NexusDLApp._instance = None


def run_gui_app(
    *,
    config_path: Path | None = None,
) -> None:
    """Lance l'application GUI.

    Fonction de haut niveau pour démarrer rapidement l'application.

    Args:
        config_path: Chemin vers le fichier de configuration.

    Raises:
        GUIAppError: Si PyQt6 n'est pas disponible.
    """
    if not PYQT6_AVAILABLE:
        raise GUIAppError(
            "PyQt6 n'est pas installé. "
            "Installez-le avec: pip install PyQt6"
        )

    # Vérifier la version de Python
    if sys.version_info < PYTHON_MIN_VERSION:
        print(
            f"Error: Python {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}+ is required.\n"
            f"You are using Python {sys.version_info.major}.{sys.version_info.minor}.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Créer l'application Qt
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(APP_NAME)
    qt_app.setApplicationVersion(APP_VERSION)
    qt_app.setOrganizationName(APP_AUTHOR)

    # Configurer la palette sombre
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLOR_BACKGROUND))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLOR_TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLOR_SURFACE))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLOR_SURFACE_ALT))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLOR_TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLOR_SURFACE))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLOR_TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLOR_PRIMARY))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(COLOR_BACKGROUND))
    qt_app.setPalette(palette)

    # Créer et lancer l'application
    app = NexusDLApp(config_path=config_path)
    set_gui_app(app)

    logger.info("Lancement de {} v{} (GUI)", APP_NAME, APP_VERSION)

    try:
        app.run()
        qt_app.exec()
    except KeyboardInterrupt:
        logger.info("Interruption clavier")
    except Exception as e:
        logger.critical("Erreur fatale: {}", e, exc_info=True)
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        reset_gui_app()


def is_pyqt6_available() -> bool:
    """Vérifie si PyQt6 est disponible.

    Returns:
        True si PyQt6 est installé.
    """
    return PYQT6_AVAILABLE


def get_pyqt6_installation_instructions() -> str:
    """Retourne les instructions d'installation de PyQt6.

    Returns:
        Instructions d'installation.
    """
    return """
Pour utiliser l'interface graphique de NexusDL, vous devez installer PyQt6 :

    pip install PyQt6

Après installation, relancez NexusDL avec :

    nexusdl-gui

Ou depuis Python :

    from nexusdl.interfaces.gui.app import run_gui_app
    run_gui_app()
""".strip()


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "GUI_VERSION",
    "WINDOW_TITLE",
    "DEFAULT_WINDOW_WIDTH",
    "DEFAULT_WINDOW_HEIGHT",
    "COLOR_PRIMARY",
    "COLOR_SECONDARY",
    "COLOR_ACCENT",
    "COLOR_BACKGROUND",
    "COLOR_SURFACE",
    "COLOR_TEXT",
    "GLOBAL_STYLESHEET",
    # Exceptions
    "GUIAppError",
    "AppNotInitializedError",
    "ComponentInitializationError",
    # Enums
    "AppView",
    "AppState",
    "NotificationSeverity",
    # Classe principale
    "NexusDLApp" if PYQT6_AVAILABLE else None,
    # Instance globale
    "get_gui_app",
    "set_gui_app",
    "reset_gui_app",
    # Fonctions
    "run_gui_app",
    "is_pyqt6_available",
    "get_pyqt6_installation_instructions",
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
