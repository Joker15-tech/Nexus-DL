"""Vue de bibliothèque pour l'interface graphique NexusDL.

Ce module fournit une vue PyQt6 complète pour gérer la bibliothèque locale
de mangas/webtoons/comics. Elle offre une expérience utilisateur riche avec
affichage multi-mode, recherche, filtrage, tri, gestion des listes de lecture,
et panneau de détails avec table de chapitres.

**Fonctionnalités** :
    - Affichage en grille (MangaCard) ou liste
    - 3 modes de vue : GRID, LIST, COMPACT
    - Recherche plein texte (titre, auteur, tags)
    - Filtrage par statut de lecture, langue, statut de publication
    - Tri par titre, date d'ajout, dernière lecture, progression
    - Gestion des listes de lecture (reading lists)
    - Panneau de détails avec métadonnées et table de chapitres
    - Actions rapides (lire, télécharger, supprimer, marquer lu/non-lu)
    - Statistiques de bibliothèque
    - Mise à jour temps réel via EventBus
    - Signaux Qt pour communication
    - Traductions i18n
    - Style cyberpunk néon cohérent

**Architecture** :
    LibraryView (QWidget principal)
        ├── LibrarySidebar (sidebar avec listes de lecture)
        │   ├── ReadingListButton (bouton de liste)
        │   └── StatsWidget (statistiques)
        ├── LibraryContent (contenu principal)
        │   ├── LibraryToolbar (barre de recherche/filtres/tri)
        │   ├── LibraryGrid (grille de MangaCard)
        │   └── LibraryDetailsPanel (panneau de détails)
        │       ├── MangaInfo (métadonnées)
        │       ├── ChapterTableWidget (table de chapitres)
        │       └── ActionButtons (boutons d'action)
        └── LibraryStatusBar (barre de statut)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.gui.views.library_view import LibraryView
    >>>
    >>> # Dans une fenêtre PyQt6
    >>> library_view = LibraryView(parent=self)
    >>> library_view.manga_double_clicked.connect(self.on_manga_open)
    >>> library_view.download_requested.connect(self.on_download)
    >>> layout.addWidget(library_view)

Intégration :
    - core/library/database.py : accès à la bibliothèque
    - core/library/scanner.py : scan de la bibliothèque
    - core/models/manga.py : modèles Manga, Chapter
    - core/models/library.py : ReadingList, ReadingProgress
    - core/events.py : mises à jour temps réel
    - core/i18n.py : traductions
    - interfaces/gui/components/manga_card.py : MangaCard
    - interfaces/gui/components/chapter_table.py : ChapterTableWidget
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
        QComboBox,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSplitter,
        QStackedWidget,
        QStatusBar,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )
    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False

from nexusdl.core.constants import APP_NAME
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t
from nexusdl.core.models.library import ReadingList, ReadingStatus
from nexusdl.core.models.manga import Language, Manga, MangaStatus


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
COLOR_ACCENT_DIM: Final[str] = "#cc00cc"
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
COLOR_BORDER_FOCUS: Final[str] = "#00ffff"

# Dimensions
SIDEBAR_WIDTH: Final[int] = 240
DETAILS_PANEL_WIDTH: Final[int] = 450
TOOLBAR_HEIGHT: Final[int] = 50
STATUSBAR_HEIGHT: Final[int] = 28
CARD_WIDTH: Final[int] = 200
CARD_HEIGHT: Final[int] = 300
GRID_SPACING: Final[int] = 12
GRID_MARGIN: Final[int] = 16


# ============================================================================
# EXCEPTIONS
# ============================================================================


class LibraryViewError(NexusDLError):
    """Exception de base pour les erreurs de la vue bibliothèque."""


class LibraryLoadError(LibraryViewError):
    """Exception levée lorsque la bibliothèque ne peut être chargée.

    Attributes:
        reason: Raison de l'échec.
    """

    def __init__(self, reason: str = "") -> None:
        msg = "Échec du chargement de la bibliothèque"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.reason = reason


class MangaNotFoundError(LibraryViewError):
    """Exception levée lorsqu'un manga est introuvable.

    Attributes:
        manga_id: ID du manga.
    """

    def __init__(self, manga_id: str) -> None:
        super().__init__(f"Manga introuvable: {manga_id}")
        self.manga_id = manga_id


# ============================================================================
# ENUMS
# ============================================================================


class LibraryViewMode(str, Enum):
    """Mode d'affichage de la bibliothèque.

    Attributes:
        GRID: Affichage en grille (cards).
        LIST: Affichage en liste.
        COMPACT: Affichage compact.
    """

    GRID = "grid"
    LIST = "list"
    COMPACT = "compact"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            LibraryViewMode.GRID: t("library.view.grid", default="Grid"),
            LibraryViewMode.LIST: t("library.view.list", default="List"),
            LibraryViewMode.COMPACT: t("library.view.compact", default="Compact"),
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            LibraryViewMode.GRID: "⊞",
            LibraryViewMode.LIST: "☰",
            LibraryViewMode.COMPACT: "≡",
        }[self]


class LibrarySortBy(str, Enum):
    """Critère de tri de la bibliothèque.

    Attributes:
        TITLE: Tri par titre.
        DATE_ADDED: Tri par date d'ajout.
        LAST_READ: Tri par dernière lecture.
        PROGRESS: Tri par progression.
        AUTHOR: Tri par auteur.
        STATUS: Tri par statut.
    """

    TITLE = "title"
    DATE_ADDED = "date_added"
    LAST_READ = "last_read"
    PROGRESS = "progress"
    AUTHOR = "author"
    STATUS = "status"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            LibrarySortBy.TITLE: t("library.sort.title", default="Title"),
            LibrarySortBy.DATE_ADDED: t("library.sort.date_added", default="Date Added"),
            LibrarySortBy.LAST_READ: t("library.sort.last_read", default="Last Read"),
            LibrarySortBy.PROGRESS: t("library.sort.progress", default="Progress"),
            LibrarySortBy.AUTHOR: t("library.sort.author", default="Author"),
            LibrarySortBy.STATUS: t("library.sort.status", default="Status"),
        }[self]


class ReadingStatusFilter(str, Enum):
    """Filtre par statut de lecture.

    Attributes:
        ALL: Tous les mangas.
        READING: En cours de lecture.
        COMPLETED: Terminés.
        PLAN_TO_READ: À lire.
        ON_HOLD: En pause.
        DROPPED: Abandonnés.
    """

    ALL = "all"
    READING = "reading"
    COMPLETED = "completed"
    PLAN_TO_READ = "plan_to_read"
    ON_HOLD = "on_hold"
    DROPPED = "dropped"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ReadingStatusFilter.ALL: t("library.filter.all", default="All"),
            ReadingStatusFilter.READING: t("library.filter.reading", default="Reading"),
            ReadingStatusFilter.COMPLETED: t("library.filter.completed", default="Completed"),
            ReadingStatusFilter.PLAN_TO_READ: t("library.filter.plan_to_read", default="Plan to Read"),
            ReadingStatusFilter.ON_HOLD: t("library.filter.on_hold", default="On Hold"),
            ReadingStatusFilter.DROPPED: t("library.filter.dropped", default="Dropped"),
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            ReadingStatusFilter.ALL: "📚",
            ReadingStatusFilter.READING: "📖",
            ReadingStatusFilter.COMPLETED: "✅",
            ReadingStatusFilter.PLAN_TO_READ: "📋",
            ReadingStatusFilter.ON_HOLD: "⏸️",
            ReadingStatusFilter.DROPPED: "❌",
        }[self]


# ============================================================================
# MODÈLES DE DONNÉES
# ============================================================================


class LibraryFilters:
    """Filtres appliqués à la bibliothèque.

    Attributes:
        search_query: Texte de recherche.
        reading_status: Filtre par statut de lecture.
        language: Filtre par langue.
        manga_status: Filtre par statut de publication.
        sort_by: Critère de tri.
        view_mode: Mode d'affichage.
        reading_list_id: ID de la liste de lecture sélectionnée.
    """

    def __init__(
        self,
        *,
        search_query: str = "",
        reading_status: ReadingStatusFilter = ReadingStatusFilter.ALL,
        language: str | None = None,
        manga_status: str | None = None,
        sort_by: LibrarySortBy = LibrarySortBy.TITLE,
        view_mode: LibraryViewMode = LibraryViewMode.GRID,
        reading_list_id: str | None = None,
    ) -> None:
        """Initialise les filtres."""
        self.search_query = search_query
        self.reading_status = reading_status
        self.language = language
        self.manga_status = manga_status
        self.sort_by = sort_by
        self.view_mode = view_mode
        self.reading_list_id = reading_list_id


class LibraryState:
    """État de la vue bibliothèque.

    Attributes:
        all_mangas: Liste de tous les mangas.
        filtered_mangas: Liste des mangas après filtrage.
        reading_lists: Listes de lecture.
        selected_manga_id: ID du manga sélectionné.
        filters: Filtres actifs.
        loading: Indique si en cours de chargement.
        error: Message d'erreur.
    """

    def __init__(self) -> None:
        """Initialise l'état."""
        self.all_mangas: list[Manga] = []
        self.filtered_mangas: list[Manga] = []
        self.reading_lists: list[ReadingList] = []
        self.selected_manga_id: str | None = None
        self.filters = LibraryFilters()
        self.loading = False
        self.error: str | None = None

    @property
    def selected_manga(self) -> Manga | None:
        """Manga sélectionné."""
        if self.selected_manga_id is None:
            return None
        for manga in self.all_mangas:
            if manga.id == self.selected_manga_id:
                return manga
        return None

    @property
    def total_count(self) -> int:
        """Nombre total de mangas."""
        return len(self.all_mangas)

    @property
    def filtered_count(self) -> int:
        """Nombre de mangas après filtrage."""
        return len(self.filtered_mangas)


# ============================================================================
# WIDGETS — Composants PyQt6
# ============================================================================


if PYQT6_AVAILABLE:

    class LibrarySidebar(QFrame):
        """Sidebar avec les listes de lecture et statistiques."""

        # Signaux
        reading_list_selected = pyqtSignal(object)  # str | None (None = all)
        scan_requested = pyqtSignal()

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la sidebar."""
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedWidth(SIDEBAR_WIDTH)
            self.setStyleSheet(f"""
                LibrarySidebar {{
                    background-color: {COLOR_SURFACE};
                    border-right: 2px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout principal
            layout = QVBoxLayout(self)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(8)

            # Titre
            title = QLabel(t("library.sidebar.title", default="Library"))
            title.setStyleSheet(f"""
                color: {COLOR_PRIMARY};
                font-size: 16px;
                font-weight: bold;
                font-family: 'JetBrains Mono', monospace;
                padding: 8px;
            """)
            layout.addWidget(title)

            # Bouton "Tous les mangas"
            self._btn_all = QPushButton(f"📚 {t('library.sidebar.all', default='All Mangas')}")
            self._btn_all.setCheckable(True)
            self._btn_all.setChecked(True)
            self._style_sidebar_button(self._btn_all)
            self._btn_all.clicked.connect(lambda: self.reading_list_selected.emit(None))
            layout.addWidget(self._btn_all)

            # Séparateur
            separator = QFrame()
            separator.setFrameShape(QFrame.Shape.HLine)
            separator.setStyleSheet(f"color: {COLOR_BORDER_DIM};")
            layout.addWidget(separator)

            # Titre des listes
            lists_title = QLabel(t("library.sidebar.reading_lists", default="Reading Lists"))
            lists_title.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 11px;
                font-weight: bold;
                padding: 4px 8px;
            """)
            layout.addWidget(lists_title)

            # Liste des reading lists
            self._lists_widget = QListWidget()
            self._lists_widget.setStyleSheet(f"""
                QListWidget {{
                    background-color: transparent;
                    border: none;
                    outline: none;
                }}
                QListWidget::item {{
                    color: {COLOR_TEXT};
                    padding: 6px 8px;
                    border-radius: 3px;
                }}
                QListWidget::item:hover {{
                    background-color: {COLOR_SURFACE_HOVER};
                    color: {COLOR_SECONDARY};
                }}
                QListWidget::item:selected {{
                    background-color: {COLOR_PRIMARY_BG};
                    color: {COLOR_PRIMARY};
                    border-left: 3px solid {COLOR_PRIMARY};
                }}
            """)
            self._lists_widget.itemClicked.connect(self._on_list_clicked)
            layout.addWidget(self._lists_widget, stretch=1)

            # Bouton Scan
            btn_scan = QPushButton(f"🔄 {t('library.sidebar.scan', default='Scan Library')}")
            self._style_sidebar_button(btn_scan)
            btn_scan.clicked.connect(self.scan_requested.emit)
            layout.addWidget(btn_scan)

            # Statistiques
            self._stats_widget = self._create_stats_widget()
            layout.addWidget(self._stats_widget)

            # Boutons de listes
            self._list_buttons: dict[str, QPushButton] = {}

        def _style_sidebar_button(self, button: QPushButton) -> None:
            """Applique le style à un bouton de sidebar."""
            button.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {COLOR_TEXT};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: 4px;
                    padding: 8px 12px;
                    text-align: left;
                    font-family: 'JetBrains Mono', monospace;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_SURFACE_HOVER};
                    border-color: {COLOR_SECONDARY};
                    color: {COLOR_SECONDARY};
                }}
                QPushButton:checked {{
                    background-color: {COLOR_PRIMARY_BG};
                    border-color: {COLOR_PRIMARY};
                    color: {COLOR_PRIMARY};
                    font-weight: bold;
                }}
            """)

        def _create_stats_widget(self) -> QFrame:
            """Crée le widget de statistiques."""
            stats = QFrame()
            stats.setStyleSheet(f"""
                QFrame {{
                    background-color: {COLOR_SURFACE_ALT};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: 4px;
                }}
            """)

            layout = QVBoxLayout(stats)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(4)

            title = QLabel(t("library.sidebar.stats", default="Statistics"))
            title.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 10px;
                font-weight: bold;
            """)
            layout.addWidget(title)

            self._stats_total = QLabel("📚 Total: 0")
            self._stats_total.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 11px;")
            layout.addWidget(self._stats_total)

            self._stats_reading = QLabel("📖 Reading: 0")
            self._stats_reading.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 11px;")
            layout.addWidget(self._stats_reading)

            self._stats_completed = QLabel("✅ Completed: 0")
            self._stats_completed.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 11px;")
            layout.addWidget(self._stats_completed)

            self._stats_size = QLabel("💾 Size: 0 B")
            self._stats_size.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 11px;")
            layout.addWidget(self._stats_size)

            return stats

        def set_reading_lists(self, lists: list[ReadingList]) -> None:
            """Définit les listes de lecture.

            Args:
                lists: Listes de lecture.
            """
            self._lists_widget.clear()
            for reading_list in lists:
                item = QListWidgetItem(f"📋 {reading_list.name} ({len(reading_list.manga_ids)})")
                item.setData(Qt.ItemDataRole.UserRole, reading_list.id)
                self._lists_widget.addItem(item)

        def update_stats(
            self,
            *,
            total: int,
            reading: int,
            completed: int,
            size_bytes: int,
        ) -> None:
            """Met à jour les statistiques.

            Args:
                total: Nombre total de mangas.
                reading: Nombre de mangas en cours.
                completed: Nombre de mangas terminés.
                size_bytes: Taille totale en bytes.
            """
            from nexusdl.core.utils.text import format_size

            self._stats_total.setText(f"📚 Total: {total}")
            self._stats_reading.setText(f"📖 Reading: {reading}")
            self._stats_completed.setText(f"✅ Completed: {completed}")
            self._stats_size.setText(f"💾 Size: {format_size(size_bytes)}")

        def _on_list_clicked(self, item: QListWidgetItem) -> None:
            """Gère le clic sur une liste de lecture."""
            list_id = item.data(Qt.ItemDataRole.UserRole)
            # Désélectionner le bouton "Tous"
            self._btn_all.setChecked(False)
            self.reading_list_selected.emit(list_id)

    class LibraryToolbar(QFrame):
        """Barre d'outils avec recherche, filtres, et tri."""

        # Signaux
        search_changed = pyqtSignal(str)
        filters_changed = pyqtSignal(object)  # LibraryFilters
        view_mode_changed = pyqtSignal(object)  # LibraryViewMode

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la barre d'outils."""
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedHeight(TOOLBAR_HEIGHT)
            self.setStyleSheet(f"""
                LibraryToolbar {{
                    background-color: {COLOR_SURFACE};
                    border-bottom: 2px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(16, 8, 16, 8)
            layout.setSpacing(12)

            # Champ de recherche
            self._search_input = QLineEdit()
            self._search_input.setPlaceholderText(
                t("library.search.placeholder", default="🔍 Search manga...")
            )
            self._search_input.setFixedWidth(250)
            self._search_input.setStyleSheet(self._input_style())
            self._search_input.textChanged.connect(self.search_changed.emit)
            layout.addWidget(self._search_input)

            # Filtre statut de lecture
            layout.addWidget(self._create_label(t("library.filter.status", default="Status:")))
            self._status_filter = QComboBox()
            self._status_filter.setFixedWidth(160)
            self._status_filter.setStyleSheet(self._combo_style())
            for status in ReadingStatusFilter:
                self._status_filter.addItem(f"{status.icon} {status.label}", status.value)
            self._status_filter.currentIndexChanged.connect(self._on_filter_changed)
            layout.addWidget(self._status_filter)

            # Tri
            layout.addWidget(self._create_label(t("library.filter.sort", default="Sort:")))
            self._sort_filter = QComboBox()
            self._sort_filter.setFixedWidth(140)
            self._sort_filter.setStyleSheet(self._combo_style())
            for sort_by in LibrarySortBy:
                self._sort_filter.addItem(sort_by.label, sort_by.value)
            self._sort_filter.currentIndexChanged.connect(self._on_filter_changed)
            layout.addWidget(self._sort_filter)

            layout.addStretch()

            # Boutons de mode de vue
            self._view_mode_buttons: dict[LibraryViewMode, QToolButton] = {}
            for mode in LibraryViewMode:
                button = QToolButton()
                button.setText(mode.icon)
                button.setToolTip(mode.label)
                button.setCheckable(True)
                button.setFixedSize(32, 32)
                button.setStyleSheet(self._view_button_style())
                button.clicked.connect(lambda checked, m=mode: self._on_view_mode_changed(m))
                layout.addWidget(button)
                self._view_mode_buttons[mode] = button

            # Sélectionner le mode par défaut
            self._view_mode_buttons[LibraryViewMode.GRID].setChecked(True)

        def _create_label(self, text: str) -> QLabel:
            """Crée un label de filtre."""
            label = QLabel(text)
            label.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 11px;
            """)
            return label

        def _input_style(self) -> str:
            """Retourne le style pour les inputs."""
            return f"""
                QLineEdit {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {COLOR_TEXT};
                    border: 2px solid {COLOR_BORDER_DIM};
                    border-radius: 4px;
                    padding: 6px 12px;
                    font-family: 'JetBrains Mono', monospace;
                    font-size: 12px;
                }}
                QLineEdit:focus {{
                    border-color: {COLOR_SECONDARY};
                }}
                QLineEdit::placeholder {{
                    color: {COLOR_TEXT_DIM};
                }}
            """

        def _combo_style(self) -> str:
            """Retourne le style pour les ComboBox."""
            return f"""
                QComboBox {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {COLOR_TEXT};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: 3px;
                    padding: 4px 8px;
                    font-size: 11px;
                }}
                QComboBox::drop-down {{
                    border: none;
                    width: 20px;
                }}
                QComboBox QAbstractItemView {{
                    background-color: {COLOR_SURFACE};
                    color: {COLOR_TEXT};
                    border: 1px solid {COLOR_BORDER_DIM};
                    selection-background-color: {COLOR_PRIMARY_BG};
                    selection-color: {COLOR_PRIMARY};
                }}
            """

        def _view_button_style(self) -> str:
            """Retourne le style pour les boutons de vue."""
            return f"""
                QToolButton {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {COLOR_TEXT_MUTED};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: 3px;
                    font-size: 14px;
                }}
                QToolButton:hover {{
                    background-color: {COLOR_SURFACE_HOVER};
                    border-color: {COLOR_SECONDARY};
                    color: {COLOR_SECONDARY};
                }}
                QToolButton:checked {{
                    background-color: {COLOR_PRIMARY_BG};
                    border-color: {COLOR_PRIMARY};
                    color: {COLOR_PRIMARY};
                }}
            """

        def _on_filter_changed(self) -> None:
            """Gère le changement de filtre."""
            self.filters_changed.emit(self.get_filters())

        def _on_view_mode_changed(self, mode: LibraryViewMode) -> None:
            """Gère le changement de mode de vue."""
            for m, button in self._view_mode_buttons.items():
                button.setChecked(m == mode)
            self.view_mode_changed.emit(mode)

        def get_filters(self) -> LibraryFilters:
            """Récupère les filtres actuels.

            Returns:
                Instance de LibraryFilters.
            """
            return LibraryFilters(
                search_query=self._search_input.text().strip(),
                reading_status=ReadingStatusFilter(self._status_filter.currentData()),
                sort_by=LibrarySortBy(self._sort_filter.currentData()),
            )

    class LibraryGrid(QWidget):
        """Grille de mangas avec MangaCard."""

        # Signaux
        manga_clicked = pyqtSignal(str)  # manga_id
        manga_double_clicked = pyqtSignal(str)  # manga_id
        manga_selected = pyqtSignal(str)  # manga_id

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la grille."""
            super().__init__(parent)

            # Layout
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            # Scroll area
            self._scroll_area = QScrollArea()
            self._scroll_area.setWidgetResizable(True)
            self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._scroll_area.setStyleSheet(f"""
                QScrollArea {{
                    background-color: {COLOR_BACKGROUND};
                    border: none;
                }}
            """)

            # Widget conteneur
            self._container = QWidget()
            self._container.setStyleSheet(f"background-color: {COLOR_BACKGROUND};")
            self._container_layout = QGridLayout(self._container)
            self._container_layout.setContentsMargins(GRID_MARGIN, GRID_MARGIN, GRID_MARGIN, GRID_MARGIN)
            self._container_layout.setSpacing(GRID_SPACING)

            self._scroll_area.setWidget(self._container)
            layout.addWidget(self._scroll_area)

            # Cartes de mangas
            self._cards: dict[str, Any] = {}
            self._selected_id: str | None = None
            self._view_mode = LibraryViewMode.GRID

            # Message vide
            self._empty_label = QLabel(t("library.empty", default="No manga in library"))
            self._empty_label.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 16px;
                padding: 40px;
            """)
            self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._empty_label.setVisible(False)
            layout.addWidget(self._empty_label)

        def set_view_mode(self, mode: LibraryViewMode) -> None:
            """Définit le mode de vue.

            Args:
                mode: Mode de vue.
            """
            self._view_mode = mode
            # TODO: Adapter le layout selon le mode

        def set_mangas(self, mangas: list[Manga]) -> None:
            """Définit les mangas à afficher.

            Args:
                mangas: Liste de mangas.
            """
            self._clear()

            if not mangas:
                self._empty_label.setVisible(True)
                self._scroll_area.setVisible(False)
                return

            self._empty_label.setVisible(False)
            self._scroll_area.setVisible(True)

            # Calculer le nombre de colonnes
            width = self._scroll_area.width() - (2 * GRID_MARGIN)
            columns = max(1, (width + GRID_SPACING) // (CARD_WIDTH + GRID_SPACING))

            # Ajouter les cartes
            from nexusdl.interfaces.gui.components.manga_card import (
                CardLayout,
                MangaCard,
                manga_to_card_data,
            )

            for i, manga in enumerate(mangas):
                row = i // columns
                col = i % columns

                card_data = manga_to_card_data(manga)
                card = MangaCard(
                    card_data,
                    layout=CardLayout.VERTICAL,
                    selectable=False,
                    parent=self._container,
                )
                card.clicked.connect(lambda m=manga: self.manga_clicked.emit(m.id))
                card.double_clicked.connect(lambda m=manga: self.manga_double_clicked.emit(m.id))

                self._container_layout.addWidget(card, row, col)
                self._cards[manga.id] = card

            # Ajouter un stretch à la fin
            self._container_layout.setRowStretch(self._container_layout.rowCount(), 1)

        def set_selected(self, manga_id: str | None) -> None:
            """Définit le manga sélectionné.

            Args:
                manga_id: ID du manga ou None.
            """
            # Désélectionner l'ancien
            if self._selected_id and self._selected_id in self._cards:
                self._cards[self._selected_id].set_selected(False)

            # Sélectionner le nouveau
            self._selected_id = manga_id
            if manga_id and manga_id in self._cards:
                self._cards[manga_id].set_selected(True)
                self.manga_selected.emit(manga_id)

        def _clear(self) -> None:
            """Nettoie la grille."""
            while self._container_layout.count():
                item = self._container_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._cards.clear()
            self._selected_id = None

    class LibraryDetailsPanel(QFrame):
        """Panneau de détails d'un manga avec table de chapitres."""

        # Signaux
        read_requested = pyqtSignal(str)  # manga_id
        download_requested = pyqtSignal(list)  # list of chapter_ids
        delete_requested = pyqtSignal(str)  # manga_id
        mark_read_requested = pyqtSignal(str)  # manga_id
        mark_unread_requested = pyqtSignal(str)  # manga_id

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise le panneau."""
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedWidth(DETAILS_PANEL_WIDTH)
            self.setStyleSheet(f"""
                LibraryDetailsPanel {{
                    background-color: {COLOR_SURFACE};
                    border-left: 2px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout principal
            layout = QVBoxLayout(self)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(12)

            # Message vide
            self._empty_label = QLabel(t("library.details.empty", default="Select a manga to view details"))
            self._empty_label.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 14px;
                padding: 40px;
            """)
            self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self._empty_label)

            # Contenu (caché par défaut)
            self._content = QWidget()
            self._content.setVisible(False)
            content_layout = QVBoxLayout(self._content)
            content_layout.setContentsMargins(0, 0, 0, 0)
            content_layout.setSpacing(8)

            # Titre
            self._title_label = QLabel("")
            self._title_label.setStyleSheet(f"""
                color: {COLOR_PRIMARY};
                font-size: 18px;
                font-weight: bold;
                font-family: 'JetBrains Mono', monospace;
            """)
            self._title_label.setWordWrap(True)
            content_layout.addWidget(self._title_label)

            # Métadonnées
            self._meta_label = QLabel("")
            self._meta_label.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 11px;
            """)
            self._meta_label.setWordWrap(True)
            content_layout.addWidget(self._meta_label)

            # Description
            self._desc_label = QLabel("")
            self._desc_label.setStyleSheet(f"""
                color: {COLOR_TEXT};
                font-size: 12px;
                padding: 8px;
                background-color: {COLOR_SURFACE_ALT};
                border-radius: 4px;
            """)
            self._desc_label.setWordWrap(True)
            content_layout.addWidget(self._desc_label)

            # Boutons d'action
            actions_layout = QHBoxLayout()
            actions_layout.setSpacing(8)

            self._btn_read = QPushButton(f"📖 {t('library.details.read', default='Read')}")
            self._style_action_button(self._btn_read, COLOR_PRIMARY)
            self._btn_read.clicked.connect(self._on_read)
            actions_layout.addWidget(self._btn_read)

            self._btn_download = QPushButton(f"⬇️ {t('library.details.download', default='Download')}")
            self._style_action_button(self._btn_download, COLOR_SECONDARY)
            self._btn_download.clicked.connect(self._on_download)
            actions_layout.addWidget(self._btn_download)

            self._btn_delete = QPushButton(f"🗑️ {t('library.details.delete', default='Delete')}")
            self._style_action_button(self._btn_delete, COLOR_ERROR)
            self._btn_delete.clicked.connect(self._on_delete)
            actions_layout.addWidget(self._btn_delete)

            content_layout.addLayout(actions_layout)

            # Séparateur
            separator = QFrame()
            separator.setFrameShape(QFrame.Shape.HLine)
            separator.setStyleSheet(f"color: {COLOR_BORDER_DIM};")
            content_layout.addWidget(separator)

            # Titre des chapitres
            chapters_title = QLabel(t("library.details.chapters", default="Chapters"))
            chapters_title.setStyleSheet(f"""
                color: {COLOR_PRIMARY};
                font-size: 14px;
                font-weight: bold;
                font-family: 'JetBrains Mono', monospace;
            """)
            content_layout.addWidget(chapters_title)

            # Table de chapitres
            from nexusdl.interfaces.gui.components.chapter_table import ChapterTableWidget
            self._chapter_table = ChapterTableWidget(parent=self._content)
            self._chapter_table.chapters_selected.connect(self._on_chapters_selected)
            content_layout.addWidget(self._chapter_table, stretch=1)

            layout.addWidget(self._content, stretch=1)

            # État
            self._current_manga: Manga | None = None
            self._selected_chapter_ids: list[str] = []

        def _style_action_button(self, button: QPushButton, color: str) -> None:
            """Applique le style à un bouton d'action."""
            button.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {color};
                    border: 2px solid {color};
                    border-radius: 4px;
                    padding: 6px 12px;
                    font-size: 11px;
                    font-weight: bold;
                    font-family: 'JetBrains Mono', monospace;
                }}
                QPushButton:hover {{
                    background-color: {color};
                    color: {COLOR_BACKGROUND};
                }}
            """)

        def set_manga(self, manga: Manga | None) -> None:
            """Définit le manga à afficher.

            Args:
                manga: Manga à afficher ou None.
            """
            self._current_manga = manga

            if manga is None:
                self._empty_label.setVisible(True)
                self._content.setVisible(False)
                return

            self._empty_label.setVisible(False)
            self._content.setVisible(True)

            # Titre
            self._title_label.setText(manga.title)

            # Métadonnées
            meta_parts = []
            if manga.author:
                meta_parts.append(f"by {manga.author}")
            if manga.year:
                meta_parts.append(str(manga.year))
            if manga.status != MangaStatus.UNKNOWN:
                meta_parts.append(f"{manga.status.icon} {manga.status.label}")
            meta_parts.append(f"{manga.language.flag} {manga.language.label}")

            self._meta_label.setText(" • ".join(meta_parts))

            # Description
            if manga.description:
                self._desc_label.setText(manga.description)
                self._desc_label.setVisible(True)
            else:
                self._desc_label.setVisible(False)

            # Chapitres
            if hasattr(manga, "chapters") and manga.chapters:
                from nexusdl.interfaces.gui.components.chapter_table import (
                    ChapterTableData,
                    chapter_to_table_data,
                )
                chapters_data = [chapter_to_table_data(c) for c in manga.chapters]
                self._chapter_table.set_chapters(chapters_data)
            else:
                self._chapter_table.set_chapters([])

        def _on_read(self) -> None:
            """Gère le clic sur le bouton Lire."""
            if self._current_manga:
                self.read_requested.emit(self._current_manga.id)

        def _on_download(self) -> None:
            """Gère le clic sur le bouton Télécharger."""
            if self._current_manga:
                if self._selected_chapter_ids:
                    self.download_requested.emit(self._selected_chapter_ids)
                else:
                    # Télécharger tous les chapitres non téléchargés
                    if hasattr(self._current_manga, "chapters"):
                        chapter_ids = [
                            c.id for c in self._current_manga.chapters
                            if not getattr(c, "is_downloaded", False)
                        ]
                        self.download_requested.emit(chapter_ids)

        def _on_delete(self) -> None:
            """Gère le clic sur le bouton Supprimer."""
            if self._current_manga:
                reply = QMessageBox.question(
                    self,
                    t("library.confirm.delete.title", default="Delete Manga"),
                    t(
                        "library.confirm.delete.message",
                        default="Are you sure you want to delete {title}?",
                        title=self._current_manga.title,
                    ),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self.delete_requested.emit(self._current_manga.id)

        def _on_chapters_selected(self, chapter_ids: list[str]) -> None:
            """Gère la sélection de chapitres.

            Args:
                chapter_ids: IDs des chapitres sélectionnés.
            """
            self._selected_chapter_ids = chapter_ids

    class LibraryStatusBar(QFrame):
        """Barre de statut avec statistiques."""

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la barre de statut."""
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedHeight(STATUSBAR_HEIGHT)
            self.setStyleSheet(f"""
                LibraryStatusBar {{
                    background-color: {COLOR_SURFACE};
                    border-top: 2px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(16, 0, 16, 0)
            layout.setSpacing(16)

            # Statistiques
            self._stats_label = QLabel("")
            self._stats_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
            layout.addWidget(self._stats_label)

            layout.addStretch()

            # Sélection
            self._selection_label = QLabel("")
            self._selection_label.setStyleSheet(f"""
                color: {COLOR_PRIMARY};
                font-size: 11px;
                font-weight: bold;
            """)
            layout.addWidget(self._selection_label)

        def update_stats(
            self,
            *,
            total: int,
            filtered: int,
            selected: int = 0,
        ) -> None:
            """Met à jour les statistiques.

            Args:
                total: Nombre total de mangas.
                filtered: Nombre de mangas filtrés.
                selected: Nombre de mangas sélectionnés.
            """
            self._stats_label.setText(
                t(
                    "library.status.count",
                    default="Showing {filtered}/{total} manga",
                    filtered=filtered,
                    total=total,
                )
            )

            if selected > 0:
                self._selection_label.setText(
                    t(
                        "library.status.selected",
                        default="{count} selected",
                        count=selected,
                    )
                )
            else:
                self._selection_label.setText("")

    class LibraryView(QWidget):
        """Vue principale de la bibliothèque.

        Combine la sidebar, la toolbar, la grille, le panneau de détails,
        et la barre de statut en un seul widget cohérent.

        Signals:
            manga_clicked(str): Émis lorsqu'un manga est cliqué.
            manga_double_clicked(str): Émis lorsqu'un manga est double-cliqué.
            read_requested(str): Émis lorsqu'une lecture est demandée.
            download_requested(list): Émis lorsqu'un téléchargement est demandé.
            delete_requested(str): Émis lorsqu'une suppression est demandée.
            scan_requested(): Émis lorsqu'un scan est demandé.
        """

        # Signaux
        manga_clicked = pyqtSignal(str)
        manga_double_clicked = pyqtSignal(str)
        read_requested = pyqtSignal(str)
        download_requested = pyqtSignal(list)
        delete_requested = pyqtSignal(str)
        scan_requested = pyqtSignal()

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la vue."""
            super().__init__(parent)
            self._state = LibraryState()

            # Layout principal horizontal
            main_layout = QHBoxLayout(self)
            main_layout.setContentsMargins(0, 0, 0, 0)
            main_layout.setSpacing(0)

            # Sidebar
            self._sidebar = LibrarySidebar(parent=self)
            self._sidebar.reading_list_selected.connect(self._on_reading_list_selected)
            self._sidebar.scan_requested.connect(self._on_scan_requested)
            main_layout.addWidget(self._sidebar)

            # Contenu principal (vertical)
            content_widget = QWidget()
            content_layout = QVBoxLayout(content_widget)
            content_layout.setContentsMargins(0, 0, 0, 0)
            content_layout.setSpacing(0)

            # Toolbar
            self._toolbar = LibraryToolbar(parent=self)
            self._toolbar.search_changed.connect(self._on_search_changed)
            self._toolbar.filters_changed.connect(self._on_filters_changed)
            self._toolbar.view_mode_changed.connect(self._on_view_mode_changed)
            content_layout.addWidget(self._toolbar)

            # Splitter (grid + details)
            self._splitter = QSplitter(Qt.Orientation.Horizontal)
            self._splitter.setHandleWidth(2)
            self._splitter.setStyleSheet(f"""
                QSplitter::handle {{
                    background-color: {COLOR_BORDER_DIM};
                }}
                QSplitter::handle:hover {{
                    background-color: {COLOR_SECONDARY};
                }}
            """)

            # Grille
            self._grid = LibraryGrid(parent=self)
            self._grid.manga_clicked.connect(self._on_manga_clicked)
            self._grid.manga_double_clicked.connect(self._on_manga_double_clicked)
            self._grid.manga_selected.connect(self._on_manga_selected)
            self._splitter.addWidget(self._grid)

            # Panneau de détails
            self._details = LibraryDetailsPanel(parent=self)
            self._details.read_requested.connect(self.read_requested.emit)
            self._details.download_requested.connect(self.download_requested.emit)
            self._details.delete_requested.connect(self._on_delete_requested)
            self._splitter.addWidget(self._details)

            # Tailles initiales
            self._splitter.setSizes([800, DETAILS_PANEL_WIDTH])

            content_layout.addWidget(self._splitter, stretch=1)

            # Status bar
            self._status_bar = LibraryStatusBar(parent=self)
            content_layout.addWidget(self._status_bar)

            main_layout.addWidget(content_widget, stretch=1)

        # =====================================================================
        # CHARGEMENT DES DONNÉES
        # =====================================================================

        async def load_library(self) -> None:
            """Charge la bibliothèque depuis la base de données."""
            self._state.loading = True

            try:
                # TODO: Charger les mangas depuis la base de données
                # from nexusdl.core.library import get_all_mangas, get_reading_lists
                # self._state.all_mangas = await get_all_mangas()
                # self._state.reading_lists = await get_reading_lists()

                # Pour l'instant, utiliser des listes vides
                self._state.all_mangas = []
                self._state.reading_lists = []

                # Mettre à jour l'UI
                self._sidebar.set_reading_lists(self._state.reading_lists)
                await self._apply_filters()

                self._state.loading = False
                self._state.error = None

                logger.info("Bibliothèque chargée: {} mangas", len(self._state.all_mangas))

            except Exception as e:
                logger.error("Erreur lors du chargement de la bibliothèque: {}", e)
                self._state.loading = False
                self._state.error = str(e)

        # =====================================================================
        # FILTRAGE ET TRI
        # =====================================================================

        async def _apply_filters(self) -> None:
            """Applique les filtres et met à jour l'affichage."""
            filters = self._state.filters

            # Commencer avec tous les mangas
            filtered = list(self._state.all_mangas)

            # Filtrer par liste de lecture
            if filters.reading_list_id:
                reading_list = next(
                    (rl for rl in self._state.reading_lists if rl.id == filters.reading_list_id),
                    None,
                )
                if reading_list:
                    filtered = [m for m in filtered if m.id in reading_list.manga_ids]

            # Filtrer par statut de lecture
            if filters.reading_status != ReadingStatusFilter.ALL:
                status_map = {
                    ReadingStatusFilter.READING: ReadingStatus.READING,
                    ReadingStatusFilter.COMPLETED: ReadingStatus.COMPLETED,
                    ReadingStatusFilter.PLAN_TO_READ: ReadingStatus.PLAN_TO_READ,
                    ReadingStatusFilter.ON_HOLD: ReadingStatus.ON_HOLD,
                    ReadingStatusFilter.DROPPED: ReadingStatus.DROPPED,
                }
                target_status = status_map.get(filters.reading_status)
                if target_status:
                    filtered = [
                        m for m in filtered
                        if hasattr(m, "reading_progress")
                        and m.reading_progress
                        and m.reading_progress.status == target_status
                    ]

            # Filtrer par langue
            if filters.language:
                filtered = [m for m in filtered if m.language.value == filters.language]

            # Filtrer par statut de publication
            if filters.manga_status:
                filtered = [m for m in filtered if m.status.value == filters.manga_status]

            # Recherche texte
            if filters.search_query:
                query_lower = filters.search_query.lower()
                filtered = [
                    m for m in filtered
                    if query_lower in m.title.lower()
                    or (m.author and query_lower in m.author.lower())
                    or any(query_lower in tag.lower() for tag in getattr(m, "tags", []))
                ]

            # Tri
            if filters.sort_by == LibrarySortBy.TITLE:
                filtered.sort(key=lambda m: m.title.lower())
            elif filters.sort_by == LibrarySortBy.DATE_ADDED:
                filtered.sort(key=lambda m: getattr(m, "added_at", datetime.min.replace(tzinfo=UTC)), reverse=True)
            elif filters.sort_by == LibrarySortBy.LAST_READ:
                filtered.sort(
                    key=lambda m: (
                        m.reading_progress.last_read_at
                        if hasattr(m, "reading_progress") and m.reading_progress and m.reading_progress.last_read_at
                        else datetime.min.replace(tzinfo=UTC)
                    ),
                    reverse=True,
                )
            elif filters.sort_by == LibrarySortBy.PROGRESS:
                filtered.sort(
                    key=lambda m: (
                        m.reading_progress.progress_percentage
                        if hasattr(m, "reading_progress") and m.reading_progress
                        else 0.0
                    ),
                    reverse=True,
                )
            elif filters.sort_by == LibrarySortBy.AUTHOR:
                filtered.sort(key=lambda m: (m.author or "").lower())
            elif filters.sort_by == LibrarySortBy.STATUS:
                filtered.sort(key=lambda m: m.status.value)

            self._state.filtered_mangas = filtered

            # Mettre à jour l'UI
            self._grid.set_mangas(filtered)
            self._grid.set_view_mode(filters.view_mode)
            self._status_bar.update_stats(
                total=len(self._state.all_mangas),
                filtered=len(filtered),
            )

        # =====================================================================
        # GESTION DES ÉVÉNEMENTS
        # =====================================================================

        def _on_search_changed(self, query: str) -> None:
            """Gère le changement de recherche.

            Args:
                query: Texte de recherche.
            """
            self._state.filters.search_query = query
            asyncio.create_task(self._apply_filters())

        def _on_filters_changed(self, filters: LibraryFilters) -> None:
            """Gère le changement de filtres.

            Args:
                filters: Nouveaux filtres.
            """
            self._state.filters.reading_status = filters.reading_status
            self._state.filters.sort_by = filters.sort_by
            asyncio.create_task(self._apply_filters())

        def _on_view_mode_changed(self, mode: LibraryViewMode) -> None:
            """Gère le changement de mode de vue.

            Args:
                mode: Nouveau mode.
            """
            self._state.filters.view_mode = mode
            self._grid.set_view_mode(mode)

        def _on_reading_list_selected(self, list_id: str | None) -> None:
            """Gère la sélection d'une liste de lecture.

            Args:
                list_id: ID de la liste ou None pour "tous".
            """
            self._state.filters.reading_list_id = list_id
            asyncio.create_task(self._apply_filters())

        def _on_manga_clicked(self, manga_id: str) -> None:
            """Gère le clic sur un manga.

            Args:
                manga_id: ID du manga.
            """
            self._state.selected_manga_id = manga_id
            self._grid.set_selected(manga_id)

            # Afficher les détails
            manga = self._state.selected_manga
            self._details.set_manga(manga)

            self.manga_clicked.emit(manga_id)

        def _on_manga_double_clicked(self, manga_id: str) -> None:
            """Gère le double-clic sur un manga.

            Args:
                manga_id: ID du manga.
            """
            self._state.selected_manga_id = manga_id
            self.read_requested.emit(manga_id)

        def _on_manga_selected(self, manga_id: str) -> None:
            """Gère la sélection d'un manga.

            Args:
                manga_id: ID du manga.
            """
            self._state.selected_manga_id = manga_id
            manga = self._state.selected_manga
            self._details.set_manga(manga)

        def _on_delete_requested(self, manga_id: str) -> None:
            """Gère la demande de suppression.

            Args:
                manga_id: ID du manga.
            """
            # Supprimer de l'état local
            self._state.all_mangas = [m for m in self._state.all_mangas if m.id != manga_id]
            asyncio.create_task(self._apply_filters())

            self.delete_requested.emit(manga_id)

        def _on_scan_requested(self) -> None:
            """Gère la demande de scan."""
            self.scan_requested.emit()
            asyncio.create_task(self.load_library())


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
    "SIDEBAR_WIDTH",
    "DETAILS_PANEL_WIDTH",
    "TOOLBAR_HEIGHT",
    "STATUSBAR_HEIGHT",
    # Exceptions
    "LibraryViewError",
    "LibraryLoadError",
    "MangaNotFoundError",
    # Enums
    "LibraryViewMode",
    "LibrarySortBy",
    "ReadingStatusFilter",
    # Modèles
    "LibraryFilters",
    "LibraryState",
    # Widgets
    "LibraryView" if PYQT6_AVAILABLE else None,
    "LibrarySidebar" if PYQT6_AVAILABLE else None,
    "LibraryToolbar" if PYQT6_AVAILABLE else None,
    "LibraryGrid" if PYQT6_AVAILABLE else None,
    "LibraryDetailsPanel" if PYQT6_AVAILABLE else None,
    "LibraryStatusBar" if PYQT6_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
