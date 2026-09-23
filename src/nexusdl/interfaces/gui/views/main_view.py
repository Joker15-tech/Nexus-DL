"""Vue principale (dashboard) pour l'interface graphique NexusDL.

Ce module fournit la vue principale de l'interface graphique NexusDL, servant
de hub central pour naviguer vers toutes les fonctionnalités et afficher un
aperçu en temps réel de l'état du système.

**Sections de la vue** :
    - Header : titre de l'application et version
    - StatsSection : statistiques globales (mangas, chapitres, taille)
    - ContinueReadingSection : mangas en cours de lecture
    - RecentDownloadsSection : derniers téléchargements effectués
    - ActiveTasksSection : tâches de téléchargement en cours
    - QuickActionsSection : boutons d'accès rapide

**Fonctionnalités** :
    - Dashboard avec statistiques en temps réel
    - Liste "Continue Reading" pour reprendre la lecture
    - Liste "Recent Downloads" avec aperçu des derniers téléchargements
    - Liste "Active Tasks" avec progression des téléchargements en cours
    - Boutons d'action rapide (recherche, bibliothèque, paramètres)
    - Mise à jour automatique via EventBus
    - Navigation vers les autres vues
    - Signaux Qt pour communication
    - Traductions i18n
    - Gestion des erreurs
    - Style cyberpunk néon cohérent

**Architecture** :
    MainView (QWidget principal)
        ├── Header (titre + version)
        ├── StatsSection (statistiques globales)
        │   ├── StatCard (mangas)
        │   ├── StatCard (chapitres)
        │   └── StatCard (taille)
        ├── ContinueReadingSection
        │   └── MangaCard (manga en cours)
        ├── RecentDownloadsSection
        │   └── DownloadItem (téléchargement récent)
        ├── ActiveTasksSection
        │   └── ProgressWidget (tâche en cours)
        ├── QuickActionsSection
        │   └── ActionButton (bouton d'action)
        └── Footer (barre de statut)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.gui.views.main_view import MainView
    >>>
    >>> # Dans une fenêtre PyQt6
    >>> main_view = MainView(parent=self)
    >>> main_view.navigate_requested.connect(self.on_navigate)
    >>> layout.addWidget(main_view)

Intégration :
    - core/library/database.py : statistiques de la bibliothèque
    - core/downloader/manager.py : tâches de téléchargement
    - core/events.py : mises à jour en temps réel
    - core/i18n.py : traductions
    - core/logger.py : logs des actions
    - interfaces/gui/components/manga_card.py : MangaCard
    - interfaces/gui/components/progress_widget.py : ProgressWidget
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Final

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
        QBrush,
        QColor,
        QFont,
        QKeyEvent,
        QMouseEvent,
        QPainter,
        QPen,
    )
    from PyQt6.QtWidgets import (
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )
    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False

from nexusdl.core.constants import APP_NAME, APP_VERSION
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t


# ============================================================================
# CONSTANTES
# ============================================================================


# Couleurs du thème cyberpunk néon
COLOR_PRIMARY: Final[str] = "#00ff41"
COLOR_PRIMARY_DIM: Final[str] = "#00cc33"
COLOR_PRIMARY_BG: Final[str] = "#001a0d"
COLOR_SECONDARY: Final[str] = "#00ffff"
COLOR_SECONDARY_DIM: Final[str] = "#00cccc"
COLOR_ACCENT: Final[str] = "#ff00ff"
COLOR_SUCCESS: Final[str] = "#00ff41"
COLOR_WARNING: Final[str] = "#ffff00"
COLOR_ERROR: Final[str] = "#ff0040"
COLOR_INFO: Final[str] = "#00ffff"
COLOR_BACKGROUND: Final[str] = "#000000"
COLOR_SURFACE: Final[str] = "#0d1117"
COLOR_SURFACE_ALT: Final[str] = "#161b22"
COLOR_SURFACE_HOVER: Final[str] = "#1a1f2e"
COLOR_TEXT: Final[str] = "#00ff41"
COLOR_TEXT_BRIGHT: Final[str] = "#ffffff"
COLOR_TEXT_MUTED: Final[str] = "#00aaaa"
COLOR_TEXT_DIM: Final[str] = "#006666"
COLOR_TEXT_DISABLED: Final[str] = "#004444"
COLOR_BORDER: Final[str] = "#00ff41"
COLOR_BORDER_DIM: Final[str] = "#006622"

# Dimensions
HEADER_HEIGHT: Final[int] = 60
STATS_HEIGHT: Final[int] = 120
SECTION_HEIGHT: Final[int] = 200
FOOTER_HEIGHT: Final[int] = 40
STAT_CARD_WIDTH: Final[int] = 200
STAT_CARD_HEIGHT: Final[int] = 100
ACTION_BUTTON_WIDTH: Final[int] = 150
ACTION_BUTTON_HEIGHT: Final[int] = 80


# ============================================================================
# EXCEPTIONS
# ============================================================================


class MainViewError(NexusDLError):
    """Exception de base pour les erreurs de la vue principale."""


class DashboardLoadError(MainViewError):
    """Exception levée lorsque le dashboard ne peut être chargé.

    Attributes:
        section: Section du dashboard.
        reason: Raison de l'échec.
    """

    def __init__(self, section: str, reason: str = "") -> None:
        msg = f"Échec du chargement du dashboard: {section}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.section = section
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class DashboardSection(str, Enum):
    """Sections du dashboard.

    Attributes:
        STATISTICS: Statistiques globales.
        CONTINUE_READING: Mangas en cours de lecture.
        RECENT_DOWNLOADS: Derniers téléchargements.
        ACTIVE_TASKS: Tâches de téléchargement en cours.
    """

    STATISTICS = "statistics"
    CONTINUE_READING = "continue_reading"
    RECENT_DOWNLOADS = "recent_downloads"
    ACTIVE_TASKS = "active_tasks"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            DashboardSection.STATISTICS: t("dashboard.section.statistics", default="Statistics"),
            DashboardSection.CONTINUE_READING: t("dashboard.section.continue_reading", default="Continue Reading"),
            DashboardSection.RECENT_DOWNLOADS: t("dashboard.section.recent_downloads", default="Recent Downloads"),
            DashboardSection.ACTIVE_TASKS: t("dashboard.section.active_tasks", default="Active Tasks"),
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            DashboardSection.STATISTICS: "📊",
            DashboardSection.CONTINUE_READING: "📖",
            DashboardSection.RECENT_DOWNLOADS: "📥",
            DashboardSection.ACTIVE_TASKS: "⚙️",
        }[self]


class QuickAction(str, Enum):
    """Actions rapides disponibles.

    Attributes:
        SEARCH: Recherche de mangas.
        LIBRARY: Bibliothèque locale.
        DOWNLOADS: Gestion des téléchargements.
        SETTINGS: Paramètres.
    """

    SEARCH = "search"
    LIBRARY = "library"
    DOWNLOADS = "downloads"
    SETTINGS = "settings"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            QuickAction.SEARCH: t("dashboard.action.search", default="Search"),
            QuickAction.LIBRARY: t("dashboard.action.library", default="Library"),
            QuickAction.DOWNLOADS: t("dashboard.action.downloads", default="Downloads"),
            QuickAction.SETTINGS: t("dashboard.action.settings", default="Settings"),
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            QuickAction.SEARCH: "🔍",
            QuickAction.LIBRARY: "📚",
            QuickAction.DOWNLOADS: "📥",
            QuickAction.SETTINGS: "⚙️",
        }[self]


# ============================================================================
# MODÈLES DE DONNÉES
# ============================================================================


class DashboardStats:
    """Statistiques du dashboard.

    Attributes:
        total_mangas: Nombre total de mangas dans la bibliothèque.
        total_chapters: Nombre total de chapitres téléchargés.
        total_size_bytes: Taille totale de la bibliothèque en bytes.
        total_reading_time_seconds: Temps total de lecture en secondes.
        currently_reading: Nombre de mangas en cours de lecture.
        completed: Nombre de mangas terminés.
        active_downloads: Nombre de téléchargements actifs.
        last_updated: Timestamp de la dernière mise à jour.
    """

    def __init__(
        self,
        *,
        total_mangas: int = 0,
        total_chapters: int = 0,
        total_size_bytes: int = 0,
        total_reading_time_seconds: float = 0.0,
        currently_reading: int = 0,
        completed: int = 0,
        active_downloads: int = 0,
        last_updated: datetime | None = None,
    ) -> None:
        """Initialise les statistiques."""
        self.total_mangas = total_mangas
        self.total_chapters = total_chapters
        self.total_size_bytes = total_size_bytes
        self.total_reading_time_seconds = total_reading_time_seconds
        self.currently_reading = currently_reading
        self.completed = completed
        self.active_downloads = active_downloads
        self.last_updated = last_updated or datetime.now(UTC)

    @property
    def total_size_human(self) -> str:
        """Taille totale formatée."""
        from nexusdl.core.utils.text import format_size
        return format_size(self.total_size_bytes)

    @property
    def reading_time_human(self) -> str:
        """Temps de lecture formaté."""
        from datetime import timedelta
        from nexusdl.core.utils.time import format_duration
        return format_duration(timedelta(seconds=self.total_reading_time_seconds))


class DashboardState:
    """État du dashboard.

    Attributes:
        stats: Statistiques globales.
        continue_reading: Liste des mangas en cours de lecture.
        recent_downloads: Liste des téléchargements récents.
        active_tasks: Liste des tâches actives.
        loading: Indique si le dashboard est en cours de chargement.
        error: Message d'erreur.
    """

    def __init__(self) -> None:
        """Initialise l'état."""
        self.stats = DashboardStats()
        self.continue_reading: list[Any] = []
        self.recent_downloads: list[Any] = []
        self.active_tasks: list[Any] = []
        self.loading = False
        self.error: str | None = None


# ============================================================================
# WIDGETS — Composants PyQt6
# ============================================================================


if PYQT6_AVAILABLE:

    class DashboardHeader(QFrame):
        """En-tête du dashboard avec titre et version."""

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise l'en-tête."""
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedHeight(HEADER_HEIGHT)
            self.setStyleSheet(f"""
                DashboardHeader {{
                    background-color: {COLOR_SURFACE};
                    border-bottom: 2px solid {COLOR_PRIMARY};
                }}
            """)

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(16, 8, 16, 8)
            layout.setSpacing(12)

            # Titre
            title = QLabel(f"{APP_NAME} v{APP_VERSION}")
            title.setStyleSheet(f"""
                color: {COLOR_PRIMARY};
                font-size: 20px;
                font-weight: bold;
                font-family: 'JetBrains Mono', monospace;
            """)
            layout.addWidget(title)

            # Tagline
            tagline = QLabel(t("dashboard.tagline", default="Your manga, your library, your way"))
            tagline.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 12px;
                font-style: italic;
            """)
            layout.addWidget(tagline)

            layout.addStretch()

            # Horloge
            self._clock_label = QLabel("")
            self._clock_label.setStyleSheet(f"""
                color: {COLOR_SECONDARY};
                font-size: 14px;
                font-family: 'JetBrains Mono', monospace;
            """)
            layout.addWidget(self._clock_label)

            # Timer pour mettre à jour l'horloge
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._update_clock)
            self._timer.start(1000)
            self._update_clock()

        def _update_clock(self) -> None:
            """Met à jour l'horloge."""
            now = datetime.now()
            self._clock_label.setText(now.strftime("%H:%M:%S"))

    class StatCard(QFrame):
        """Carte de statistique individuelle."""

        def __init__(
            self,
            icon: str,
            value: str,
            label: str,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la carte.

            Args:
                icon: Icône Unicode.
                value: Valeur à afficher.
                label: Libellé de la statistique.
                parent: Widget parent.
            """
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedSize(STAT_CARD_WIDTH, STAT_CARD_HEIGHT)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setStyleSheet(f"""
                StatCard {{
                    background-color: {COLOR_SURFACE};
                    border: 2px solid {COLOR_BORDER_DIM};
                    border-radius: 6px;
                }}
                StatCard:hover {{
                    border-color: {COLOR_SECONDARY};
                    background-color: {COLOR_SURFACE_HOVER};
                }}
            """)

            # Layout
            layout = QVBoxLayout(self)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(4)

            # Icône
            icon_label = QLabel(icon)
            icon_label.setStyleSheet(f"""
                color: {COLOR_PRIMARY};
                font-size: 24px;
            """)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(icon_label)

            # Valeur
            self._value_label = QLabel(value)
            self._value_label.setStyleSheet(f"""
                color: {COLOR_TEXT_BRIGHT};
                font-size: 18px;
                font-weight: bold;
                font-family: 'JetBrains Mono', monospace;
            """)
            self._value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self._value_label)

            # Libellé
            label_widget = QLabel(label)
            label_widget.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 11px;
            """)
            label_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label_widget)

        def update_value(self, value: str) -> None:
            """Met à jour la valeur affichée.

            Args:
                value: Nouvelle valeur.
            """
            self._value_label.setText(value)

    class StatsSection(QFrame):
        """Section des statistiques globales."""

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la section."""
            super().__init__(parent)

            # Configuration visuelle
            self.setStyleSheet(f"""
                StatsSection {{
                    background-color: {COLOR_BACKGROUND};
                    border-bottom: 1px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout
            layout = QVBoxLayout(self)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(12)

            # Titre
            title = QLabel(f"{DashboardSection.STATISTICS.icon} {DashboardSection.STATISTICS.label}")
            title.setStyleSheet(f"""
                color: {COLOR_PRIMARY};
                font-size: 16px;
                font-weight: bold;
                font-family: 'JetBrains Mono', monospace;
            """)
            layout.addWidget(title)

            # Cartes de statistiques
            cards_layout = QHBoxLayout()
            cards_layout.setSpacing(16)

            self._card_mangas = StatCard("📚", "0", t("dashboard.stats.mangas", default="Mangas"))
            cards_layout.addWidget(self._card_mangas)

            self._card_chapters = StatCard("📄", "0", t("dashboard.stats.chapters", default="Chapters"))
            cards_layout.addWidget(self._card_chapters)

            self._card_size = StatCard("💾", "0 B", t("dashboard.stats.size", default="Library Size"))
            cards_layout.addWidget(self._card_size)

            cards_layout.addStretch()
            layout.addLayout(cards_layout)

        def update_stats(self, stats: DashboardStats) -> None:
            """Met à jour les statistiques affichées.

            Args:
                stats: Statistiques à afficher.
            """
            self._card_mangas.update_value(str(stats.total_mangas))
            self._card_chapters.update_value(str(stats.total_chapters))
            self._card_size.update_value(stats.total_size_human)

    class ContinueReadingSection(QFrame):
        """Section des mangas en cours de lecture."""

        # Signaux
        manga_clicked = pyqtSignal(str)  # manga_id

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la section."""
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedHeight(SECTION_HEIGHT)
            self.setStyleSheet(f"""
                ContinueReadingSection {{
                    background-color: {COLOR_BACKGROUND};
                    border-bottom: 1px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout
            layout = QVBoxLayout(self)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(12)

            # Titre
            title = QLabel(f"{DashboardSection.CONTINUE_READING.icon} {DashboardSection.CONTINUE_READING.label}")
            title.setStyleSheet(f"""
                color: {COLOR_PRIMARY};
                font-size: 16px;
                font-weight: bold;
                font-family: 'JetBrains Mono', monospace;
            """)
            layout.addWidget(title)

            # Grille de mangas
            self._grid_layout = QGridLayout()
            self._grid_layout.setSpacing(12)
            layout.addLayout(self._grid_layout)

            # Message "Aucun manga en cours"
            self._empty_label = QLabel(t("dashboard.continue_reading.empty", default="No manga in progress"))
            self._empty_label.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 14px;
                padding: 20px;
            """)
            self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self._empty_label)

        def set_mangas(self, mangas: list[Any]) -> None:
            """Définit les mangas à afficher.

            Args:
                mangas: Liste de mangas en cours de lecture.
            """
            # Nettoyer la grille
            while self._grid_layout.count():
                item = self._grid_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            if not mangas:
                self._empty_label.setVisible(True)
                return

            self._empty_label.setVisible(False)

            # Ajouter les cartes
            from nexusdl.interfaces.gui.components.manga_card import MangaCard, CardLayout, manga_to_card_data

            for i, manga in enumerate(mangas[:5]):  # Limiter à 5 mangas
                row = 0
                col = i

                card_data = manga_to_card_data(manga)
                card = MangaCard(
                    card_data,
                    layout=CardLayout.VERTICAL,
                    parent=self,
                )
                card.clicked.connect(lambda m=manga: self.manga_clicked.emit(m.id))

                self._grid_layout.addWidget(card, row, col)

    class RecentDownloadsSection(QFrame):
        """Section des téléchargements récents."""

        # Signaux
        download_clicked = pyqtSignal(str)  # task_id

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la section."""
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedHeight(SECTION_HEIGHT)
            self.setStyleSheet(f"""
                RecentDownloadsSection {{
                    background-color: {COLOR_BACKGROUND};
                    border-bottom: 1px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout
            layout = QVBoxLayout(self)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(12)

            # Titre
            title = QLabel(f"{DashboardSection.RECENT_DOWNLOADS.icon} {DashboardSection.RECENT_DOWNLOADS.label}")
            title.setStyleSheet(f"""
                color: {COLOR_PRIMARY};
                font-size: 16px;
                font-weight: bold;
                font-family: 'JetBrains Mono', monospace;
            """)
            layout.addWidget(title)

            # Liste des téléchargements
            self._list_layout = QVBoxLayout()
            self._list_layout.setSpacing(8)
            layout.addLayout(self._list_layout)

            # Message "Aucun téléchargement récent"
            self._empty_label = QLabel(t("dashboard.recent_downloads.empty", default="No recent downloads"))
            self._empty_label.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 14px;
                padding: 20px;
            """)
            self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self._empty_label)

        def set_downloads(self, downloads: list[Any]) -> None:
            """Définit les téléchargements à afficher.

            Args:
                downloads: Liste de téléchargements récents.
            """
            # Nettoyer la liste
            while self._list_layout.count():
                item = self._list_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            if not downloads:
                self._empty_label.setVisible(True)
                return

            self._empty_label.setVisible(False)

            # Ajouter les items
            for task in downloads[:5]:  # Limiter à 5 téléchargements
                item = self._create_download_item(task)
                self._list_layout.addWidget(item)

        def _create_download_item(self, task: Any) -> QFrame:
            """Crée un item de téléchargement.

            Args:
                task: Tâche de téléchargement.

            Returns:
                Frame de l'item.
            """
            item = QFrame()
            item.setStyleSheet(f"""
                QFrame {{
                    background-color: {COLOR_SURFACE};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: 4px;
                }}
                QFrame:hover {{
                    border-color: {COLOR_SECONDARY};
                    background-color: {COLOR_SURFACE_HOVER};
                }}
            """)
            item.setCursor(Qt.CursorShape.PointingHandCursor)

            layout = QHBoxLayout(item)
            layout.setContentsMargins(12, 8, 12, 8)
            layout.setSpacing(12)

            # Icône de statut
            status_icon = "✅" if hasattr(task, "completed_at") and task.completed_at else "⏳"
            icon_label = QLabel(status_icon)
            icon_label.setStyleSheet(f"font-size: 18px;")
            layout.addWidget(icon_label)

            # Informations
            info_layout = QVBoxLayout()
            info_layout.setSpacing(2)

            title = QLabel(task.manga.title if hasattr(task, "manga") else "Unknown")
            title.setStyleSheet(f"""
                color: {COLOR_TEXT};
                font-size: 13px;
                font-weight: bold;
            """)
            info_layout.addWidget(title)

            meta_parts = []
            if hasattr(task, "status"):
                meta_parts.append(task.status.label if hasattr(task.status, "label") else str(task.status))
            if hasattr(task, "completed_at") and task.completed_at:
                meta_parts.append(task.completed_at.strftime("%Y-%m-%d %H:%M"))

            meta = QLabel(" • ".join(meta_parts))
            meta.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 11px;
            """)
            info_layout.addWidget(meta)

            layout.addLayout(info_layout)
            layout.addStretch()

            # Bouton pour ouvrir
            btn_open = QPushButton("📂")
            btn_open.setFixedSize(28, 28)
            btn_open.setToolTip(t("dashboard.open", default="Open"))
            btn_open.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    border: none;
                    font-size: 16px;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_SURFACE_HOVER};
                    border-radius: 3px;
                }}
            """)
            btn_open.clicked.connect(lambda: self.download_clicked.emit(task.id if hasattr(task, "id") else ""))
            layout.addWidget(btn_open)

            return item

    class ActiveTasksSection(QFrame):
        """Section des tâches de téléchargement actives."""

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la section."""
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedHeight(SECTION_HEIGHT)
            self.setStyleSheet(f"""
                ActiveTasksSection {{
                    background-color: {COLOR_BACKGROUND};
                    border-bottom: 1px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout
            layout = QVBoxLayout(self)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(12)

            # Titre
            title = QLabel(f"{DashboardSection.ACTIVE_TASKS.icon} {DashboardSection.ACTIVE_TASKS.label}")
            title.setStyleSheet(f"""
                color: {COLOR_PRIMARY};
                font-size: 16px;
                font-weight: bold;
                font-family: 'JetBrains Mono', monospace;
            """)
            layout.addWidget(title)

            # Liste des tâches
            self._list_layout = QVBoxLayout()
            self._list_layout.setSpacing(8)
            layout.addLayout(self._list_layout)

            # Message "Aucune tâche active"
            self._empty_label = QLabel(t("dashboard.active_tasks.empty", default="No active downloads"))
            self._empty_label.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 14px;
                padding: 20px;
            """)
            self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self._empty_label)

        def set_tasks(self, tasks: list[Any]) -> None:
            """Définit les tâches à afficher.

            Args:
                tasks: Liste de tâches actives.
            """
            # Nettoyer la liste
            while self._list_layout.count():
                item = self._list_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            if not tasks:
                self._empty_label.setVisible(True)
                return

            self._empty_label.setVisible(False)

            # Ajouter les tâches
            for task in tasks[:5]:  # Limiter à 5 tâches
                item = self._create_task_item(task)
                self._list_layout.addWidget(item)

        def _create_task_item(self, task: Any) -> QFrame:
            """Crée un item de tâche.

            Args:
                task: Tâche de téléchargement.

            Returns:
                Frame de l'item.
            """
            item = QFrame()
            item.setStyleSheet(f"""
                QFrame {{
                    background-color: {COLOR_SURFACE};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: 4px;
                }}
            """)

            layout = QVBoxLayout(item)
            layout.setContentsMargins(12, 8, 12, 8)
            layout.setSpacing(4)

            # Titre
            title = QLabel(task.manga.title if hasattr(task, "manga") else "Unknown")
            title.setStyleSheet(f"""
                color: {COLOR_TEXT};
                font-size: 13px;
                font-weight: bold;
            """)
            layout.addWidget(title)

            # Barre de progression
            from nexusdl.interfaces.gui.components.progress_widget import ProgressWidget, ProgressStyle
            progress = ProgressWidget(
                style=ProgressStyle.GRADIENT,
                parent=self,
            )
            progress.setFixedHeight(20)

            # Mettre à jour la progression
            if hasattr(task, "pages_completed") and hasattr(task, "pages_total"):
                progress.set_progress(task.pages_completed, task.pages_total)

            layout.addWidget(progress)

            # Métadonnées
            meta_parts = []
            if hasattr(task, "status"):
                meta_parts.append(task.status.label if hasattr(task.status, "label") else str(task.status))
            if hasattr(task, "pages_completed") and hasattr(task, "pages_total"):
                meta_parts.append(f"{task.pages_completed}/{task.pages_total} pages")

            meta = QLabel(" • ".join(meta_parts))
            meta.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 11px;
            """)
            layout.addWidget(meta)

            return item

    class QuickActionsSection(QFrame):
        """Section des actions rapides."""

        # Signaux
        action_requested = pyqtSignal(object)  # QuickAction

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la section."""
            super().__init__(parent)

            # Configuration visuelle
            self.setStyleSheet(f"""
                QuickActionsSection {{
                    background-color: {COLOR_BACKGROUND};
                }}
            """)

            # Layout
            layout = QVBoxLayout(self)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(12)

            # Titre
            title = QLabel(t("dashboard.quick_actions", default="Quick Actions"))
            title.setStyleSheet(f"""
                color: {COLOR_PRIMARY};
                font-size: 16px;
                font-weight: bold;
                font-family: 'JetBrains Mono', monospace;
            """)
            layout.addWidget(title)

            # Boutons d'action
            buttons_layout = QHBoxLayout()
            buttons_layout.setSpacing(16)

            for action in QuickAction:
                button = self._create_action_button(action)
                buttons_layout.addWidget(button)

            buttons_layout.addStretch()
            layout.addLayout(buttons_layout)

        def _create_action_button(self, action: QuickAction) -> QPushButton:
            """Crée un bouton d'action.

            Args:
                action: Action à créer.

            Returns:
                Bouton créé.
            """
            button = QPushButton(f"{action.icon}\n{action.label}")
            button.setFixedSize(ACTION_BUTTON_WIDTH, ACTION_BUTTON_HEIGHT)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_SURFACE};
                    color: {COLOR_TEXT};
                    border: 2px solid {COLOR_BORDER_DIM};
                    border-radius: 6px;
                    font-size: 14px;
                    font-weight: bold;
                    font-family: 'JetBrains Mono', monospace;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_SURFACE_HOVER};
                    border-color: {COLOR_SECONDARY};
                    color: {COLOR_SECONDARY};
                }}
                QPushButton:pressed {{
                    background-color: {COLOR_PRIMARY_BG};
                    border-color: {COLOR_PRIMARY};
                    color: {COLOR_PRIMARY};
                }}
            """)
            button.clicked.connect(lambda: self.action_requested.emit(action))
            return button

    class MainView(QWidget):
        """Vue principale (dashboard) de l'application.

        Sert de hub central pour naviguer vers toutes les fonctionnalités
        et afficher un aperçu en temps réel de l'état du système.

        Signals:
            navigate_requested(QuickAction): Émis lorsqu'une navigation est demandée.
            manga_clicked(str): Émis lorsqu'un manga est cliqué.
            download_clicked(str): Émis lorsqu'un téléchargement est cliqué.
        """

        # Signaux
        navigate_requested = pyqtSignal(object)  # QuickAction
        manga_clicked = pyqtSignal(str)  # manga_id
        download_clicked = pyqtSignal(str)  # task_id

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la vue."""
            super().__init__(parent)
            self._state = DashboardState()
            self._event_bus_subscription = None

            # Layout principal
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            # En-tête
            self._header = DashboardHeader(parent=self)
            layout.addWidget(self._header)

            # Contenu scrollable
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)
            scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll_area.setStyleSheet(f"""
                QScrollArea {{
                    background-color: {COLOR_BACKGROUND};
                    border: none;
                }}
            """)

            # Widget conteneur
            container = QWidget()
            container.setStyleSheet(f"background-color: {COLOR_BACKGROUND};")
            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(0)

            # Sections
            self._stats_section = StatsSection(parent=container)
            container_layout.addWidget(self._stats_section)

            self._continue_reading_section = ContinueReadingSection(parent=container)
            self._continue_reading_section.manga_clicked.connect(self.manga_clicked.emit)
            container_layout.addWidget(self._continue_reading_section)

            self._recent_downloads_section = RecentDownloadsSection(parent=container)
            self._recent_downloads_section.download_clicked.connect(self.download_clicked.emit)
            container_layout.addWidget(self._recent_downloads_section)

            self._active_tasks_section = ActiveTasksSection(parent=container)
            container_layout.addWidget(self._active_tasks_section)

            self._quick_actions_section = QuickActionsSection(parent=container)
            self._quick_actions_section.action_requested.connect(self.navigate_requested.emit)
            container_layout.addWidget(self._quick_actions_section)

            container_layout.addStretch()

            scroll_area.setWidget(container)
            layout.addWidget(scroll_area, stretch=1)

        def showEvent(self, event: Any) -> None:
            """Gère l'affichage de la vue."""
            super().showEvent(event)
            # Charger le dashboard
            asyncio.create_task(self._load_dashboard())

            # S'abonner aux événements EventBus
            self._subscribe_to_events()

        def hideEvent(self, event: Any) -> None:
            """Gère la dissimulation de la vue."""
            super().hideEvent(event)
            # Se désabonner des événements
            self._unsubscribe_from_events()

        def _subscribe_to_events(self) -> None:
            """S'abonne aux événements EventBus pour mises à jour temps réel."""
            try:
                event_bus = get_event_bus()

                # S'abonner aux événements de téléchargement
                event_bus.on(
                    EventType.DOWNLOAD_TASK_COMPLETED,
                    self._on_download_completed,
                )
                event_bus.on(
                    EventType.DOWNLOAD_TASK_PROGRESS,
                    self._on_download_progress,
                )
                event_bus.on(
                    EventType.LIBRARY_MANGA_ADDED,
                    self._on_manga_added,
                )

                logger.debug("Abonné aux événements EventBus pour le dashboard")

            except Exception as e:
                logger.warning("Impossible de s'abonner aux événements EventBus: {}", e)

        def _unsubscribe_from_events(self) -> None:
            """Se désabonne des événements EventBus."""
            # TODO: Implémenter la désinscription propre
            pass

        async def _on_download_completed(self, event: Any) -> None:
            """Gère l'événement de téléchargement terminé."""
            logger.debug("Téléchargement terminé, rafraîchissement du dashboard")
            await self._load_dashboard()

        async def _on_download_progress(self, event: Any) -> None:
            """Gère l'événement de progression de téléchargement."""
            # TODO: Mettre à jour la tâche active si présente
            pass

        async def _on_manga_added(self, event: Any) -> None:
            """Gère l'événement d'ajout de manga."""
            logger.debug("Manga ajouté, rafraîchissement du dashboard")
            await self._load_dashboard()

        # =====================================================================
        # CHARGEMENT DU DASHBOARD
        # =====================================================================

        async def _load_dashboard(self) -> None:
            """Charge toutes les sections du dashboard."""
            self._state.loading = True

            try:
                # Charger les statistiques
                await self._load_statistics()

                # Charger Continue Reading
                await self._load_continue_reading()

                # Charger Recent Downloads
                await self._load_recent_downloads()

                # Charger Active Tasks
                await self._load_active_tasks()

                self._state.loading = False
                self._state.error = None

                logger.info("Dashboard chargé avec succès")

            except Exception as e:
                logger.error("Erreur lors du chargement du dashboard: {}", e)
                self._state.loading = False
                self._state.error = str(e)

        async def _load_statistics(self) -> None:
            """Charge les statistiques globales."""
            try:
                # TODO: Obtenir les statistiques de la bibliothèque
                # library_stats = await get_library_stats()
                # download_stats = await get_download_stats()

                # Pour l'instant, utiliser des valeurs par défaut
                self._state.stats = DashboardStats(
                    total_mangas=0,
                    total_chapters=0,
                    total_size_bytes=0,
                    total_reading_time_seconds=0.0,
                    currently_reading=0,
                    completed=0,
                    active_downloads=0,
                    last_updated=datetime.now(UTC),
                )

                # Mettre à jour l'UI
                self._stats_section.update_stats(self._state.stats)

            except Exception as e:
                logger.warning("Impossible de charger les statistiques: {}", e)
                raise DashboardLoadError("statistics", str(e)) from e

        async def _load_continue_reading(self) -> None:
            """Charge la liste des mangas en cours de lecture."""
            try:
                # TODO: Obtenir les mangas en cours de lecture
                # entries = await get_continue_reading(limit=5)
                entries = []

                self._state.continue_reading = entries
                self._continue_reading_section.set_mangas(entries)

            except Exception as e:
                logger.warning("Impossible de charger Continue Reading: {}", e)

        async def _load_recent_downloads(self) -> None:
            """Charge la liste des téléchargements récents."""
            try:
                # TODO: Obtenir les téléchargements récents
                # tasks = await get_recent_downloads(limit=5)
                tasks = []

                self._state.recent_downloads = tasks
                self._recent_downloads_section.set_downloads(tasks)

            except Exception as e:
                logger.warning("Impossible de charger Recent Downloads: {}", e)

        async def _load_active_tasks(self) -> None:
            """Charge la liste des tâches de téléchargement actives."""
            try:
                # TODO: Obtenir les tâches actives
                # manager = get_download_manager()
                # tasks = await manager.get_active_tasks()
                tasks = []

                self._state.active_tasks = tasks
                self._active_tasks_section.set_tasks(tasks)

            except Exception as e:
                logger.warning("Impossible de charger Active Tasks: {}", e)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "COLOR_PRIMARY",
    "COLOR_SECONDARY",
    "COLOR_ACCENT",
    "COLOR_BACKGROUND",
    "COLOR_SURFACE",
    "COLOR_TEXT",
    "HEADER_HEIGHT",
    "STATS_HEIGHT",
    "SECTION_HEIGHT",
    "FOOTER_HEIGHT",
    # Exceptions
    "MainViewError",
    "DashboardLoadError",
    # Enums
    "DashboardSection",
    "QuickAction",
    # Modèles
    "DashboardStats",
    "DashboardState",
    # Widgets
    "MainView" if PYQT6_AVAILABLE else None,
    "DashboardHeader" if PYQT6_AVAILABLE else None,
    "StatCard" if PYQT6_AVAILABLE else None,
    "StatsSection" if PYQT6_AVAILABLE else None,
    "ContinueReadingSection" if PYQT6_AVAILABLE else None,
    "RecentDownloadsSection" if PYQT6_AVAILABLE else None,
    "ActiveTasksSection" if PYQT6_AVAILABLE else None,
    "QuickActionsSection" if PYQT6_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
