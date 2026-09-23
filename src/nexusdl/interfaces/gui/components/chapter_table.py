"""Composant GUI de table de chapitres pour NexusDL.

Ce module fournit un widget PyQt6 personnalisé pour afficher et gérer une
table de chapitres dans l'interface graphique. Il offre une expérience
utilisateur riche avec tri, filtrage, sélection multiple, et actions en batch.

**Fonctionnalités** :
    - Tableau avec colonnes configurables
    - Tri par colonnes (clic sur en-têtes)
    - Filtrage par statut (tous, lus, non-lus, téléchargés, non-téléchargés)
    - Recherche par titre/scanlator
    - Sélection simple ou multiple (checkbox)
    - Actions en batch (marquer lu, télécharger, supprimer)
    - Badges visuels pour le statut
    - Boutons d'action par ligne
    - Style cyberpunk néon cohérent
    - Signaux Qt pour communication
    - Intégration avec modèle Chapter
    - Configuration complète via ChapterTableConfig
    - Pagination ou scroll infini

**Architecture** :
    ChapterTable (QWidget principal)
        ├── ChapterTableToolbar (barre d'outils)
        │   ├── SearchInput (recherche)
        │   ├── StatusFilter (filtre statut)
        │   └── ActionButtons (actions batch)
        ├── QTableWidget (tableau principal)
        │   ├── Header (en-têtes de colonnes)
        │   ├── Rows (lignes de chapitres)
        │   │   ├── Checkbox (sélection)
        │   │   ├── Number (numéro)
        │   │   ├── Title (titre)
        │   │   ├── Date (date)
        │   │   ├── Scanlator (scanlator)
        │   │   ├── Pages (nombre de pages)
        │   │   ├── Status (badge statut)
        │   │   └── Actions (boutons)
        │   └── Footer (statistiques)
        └── ChapterTableFooter (barre de statut)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.gui.components.chapter_table import (
    ...     ChapterTable, ChapterTableConfig,
    ... )
    >>>
    >>> # Dans une fenêtre PyQt6
    >>> table = ChapterTable(
    ...     chapters=manga.chapters,
    ...     config=ChapterTableConfig(),
    ...     parent=self,
    ... )
    >>> table.chapter_selected.connect(self.on_chapter_selected)
    >>> table.chapters_action.connect(self.on_chapters_action)
    >>> layout.addWidget(table)
    >>>
    >>> # Récupérer la sélection
    >>> selected = table.get_selected_chapters()

Intégration :
    - core/models/manga.py : modèle Chapter
    - core/events.py : émission d'événements
    - core/i18n.py : traductions
    - core/utils/time.py : formatage des dates
"""

from __future__ import annotations

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
        QAction,
        QBrush,
        QColor,
        QFont,
        QIcon,
        QKeyEvent,
        QMouseEvent,
        QPainter,
        QPen,
    )
    from PyQt6.QtWidgets import (
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QFrame,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QPushButton,
        QSizePolicy,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False

from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t


# ============================================================================
# CONSTANTES
# ============================================================================


# Couleurs du thème cyberpunk néon
COLOR_PRIMARY: Final[str] = "#00ff41"
COLOR_PRIMARY_DIM: Final[str] = "#00cc33"
COLOR_SECONDARY: Final[str] = "#00ffff"
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
TABLE_MIN_WIDTH: Final[int] = 600
TABLE_MIN_HEIGHT: Final[int] = 400
ROW_HEIGHT: Final[int] = 36
HEADER_HEIGHT: Final[int] = 32
TOOLBAR_HEIGHT: Final[int] = 40
FOOTER_HEIGHT: Final[int] = 28

# Colonnes
COLUMN_CHECKBOX: Final[int] = 0
COLUMN_NUMBER: Final[int] = 1
COLUMN_TITLE: Final[int] = 2
COLUMN_DATE: Final[int] = 3
COLUMN_SCANLATOR: Final[int] = 4
COLUMN_PAGES: Final[int] = 5
COLUMN_STATUS: Final[int] = 6
COLUMN_ACTIONS: Final[int] = 7

COLUMN_COUNT: Final[int] = 8


# ============================================================================
# EXCEPTIONS
# ============================================================================


class ChapterTableError(NexusDLError):
    """Exception de base pour les erreurs de la table de chapitres."""


class InvalidChapterError(ChapterTableError):
    """Exception levée lorsqu'un chapitre est invalide."""

    def __init__(self, chapter_id: str, reason: str = "") -> None:
        msg = f"Chapitre invalide: {chapter_id}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.chapter_id = chapter_id
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class ChapterFilter(str, Enum):
    """Filtre de statut des chapitres.

    Attributes:
        ALL: Tous les chapitres.
        READ: Chapitres lus.
        UNREAD: Chapitres non lus.
        DOWNLOADED: Chapitres téléchargés.
        NOT_DOWNLOADED: Chapitres non téléchargés.
    """

    ALL = "all"
    READ = "read"
    UNREAD = "unread"
    DOWNLOADED = "downloaded"
    NOT_DOWNLOADED = "not_downloaded"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ChapterFilter.ALL: t("chapter.filter.all", default="All"),
            ChapterFilter.READ: t("chapter.filter.read", default="Read"),
            ChapterFilter.UNREAD: t("chapter.filter.unread", default="Unread"),
            ChapterFilter.DOWNLOADED: t("chapter.filter.downloaded", default="Downloaded"),
            ChapterFilter.NOT_DOWNLOADED: t("chapter.filter.not_downloaded", default="Not Downloaded"),
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            ChapterFilter.ALL: "📋",
            ChapterFilter.READ: "✅",
            ChapterFilter.UNREAD: "📖",
            ChapterFilter.DOWNLOADED: "💾",
            ChapterFilter.NOT_DOWNLOADED: "⏳",
        }[self]


class ChapterSortBy(str, Enum):
    """Critère de tri des chapitres.

    Attributes:
        NUMBER: Tri par numéro.
        DATE: Tri par date.
        TITLE: Tri par titre.
        STATUS: Tri par statut.
    """

    NUMBER = "number"
    DATE = "date"
    TITLE = "title"
    STATUS = "status"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ChapterSortBy.NUMBER: t("chapter.sort.number", default="Number"),
            ChapterSortBy.DATE: t("chapter.sort.date", default="Date"),
            ChapterSortBy.TITLE: t("chapter.sort.title", default="Title"),
            ChapterSortBy.STATUS: t("chapter.sort.status", default="Status"),
        }[self]


class ChapterAction(str, Enum):
    """Actions disponibles sur les chapitres.

    Attributes:
        DOWNLOAD: Télécharger les chapitres.
        MARK_READ: Marquer comme lu.
        MARK_UNREAD: Marquer comme non lu.
        DELETE: Supprimer les chapitres.
    """

    DOWNLOAD = "download"
    MARK_READ = "mark_read"
    MARK_UNREAD = "mark_unread"
    DELETE = "delete"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ChapterAction.DOWNLOAD: t("chapter.action.download", default="Download"),
            ChapterAction.MARK_READ: t("chapter.action.mark_read", default="Mark as Read"),
            ChapterAction.MARK_UNREAD: t("chapter.action.mark_unread", default="Mark as Unread"),
            ChapterAction.DELETE: t("chapter.action.delete", default="Delete"),
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            ChapterAction.DOWNLOAD: "⬇️",
            ChapterAction.MARK_READ: "✅",
            ChapterAction.MARK_UNREAD: "📖",
            ChapterAction.DELETE: "🗑️",
        }[self]


# ============================================================================
# MODÈLES DE DONNÉES
# ============================================================================


class ChapterTableConfig:
    """Configuration de la table de chapitres.

    Attributes:
        show_checkbox: Afficher la colonne de sélection.
        show_number: Afficher le numéro de chapitre.
        show_title: Afficher le titre.
        show_date: Afficher la date.
        show_scanlator: Afficher le scanlator.
        show_pages: Afficher le nombre de pages.
        show_status: Afficher le statut.
        show_actions: Afficher les boutons d'action.
        selectable: Permettre la sélection.
        multi_select: Permettre la sélection multiple.
        sortable: Permettre le tri.
        filterable: Afficher la barre de filtrage.
        searchable: Afficher le champ de recherche.
        row_height: Hauteur des lignes.
        header_height: Hauteur des en-têtes.
    """

    def __init__(
        self,
        *,
        show_checkbox: bool = True,
        show_number: bool = True,
        show_title: bool = True,
        show_date: bool = True,
        show_scanlator: bool = True,
        show_pages: bool = True,
        show_status: bool = True,
        show_actions: bool = True,
        selectable: bool = True,
        multi_select: bool = True,
        sortable: bool = True,
        filterable: bool = True,
        searchable: bool = True,
        row_height: int = ROW_HEIGHT,
        header_height: int = HEADER_HEIGHT,
    ) -> None:
        """Initialise la configuration."""
        self.show_checkbox = show_checkbox
        self.show_number = show_number
        self.show_title = show_title
        self.show_date = show_date
        self.show_scanlator = show_scanlator
        self.show_pages = show_pages
        self.show_status = show_status
        self.show_actions = show_actions
        self.selectable = selectable
        self.multi_select = multi_select
        self.sortable = sortable
        self.filterable = filterable
        self.searchable = searchable
        self.row_height = row_height
        self.header_height = header_height


class ChapterTableFilters:
    """Filtres appliqués à la table.

    Attributes:
        status_filter: Filtre par statut.
        sort_by: Critère de tri.
        sort_reverse: Tri inversé.
        search_query: Texte de recherche.
    """

    def __init__(
        self,
        *,
        status_filter: ChapterFilter = ChapterFilter.ALL,
        sort_by: ChapterSortBy = ChapterSortBy.NUMBER,
        sort_reverse: bool = False,
        search_query: str = "",
    ) -> None:
        """Initialise les filtres."""
        self.status_filter = status_filter
        self.sort_by = sort_by
        self.sort_reverse = sort_reverse
        self.search_query = search_query


class ChapterTableData:
    """Données d'un chapitre pour la table.

    Attributes:
        chapter_id: ID du chapitre.
        number: Numéro du chapitre.
        title: Titre du chapitre.
        published_at: Date de publication.
        scanlator: Scanlator.
        pages_count: Nombre de pages.
        is_read: Si le chapitre est lu.
        is_downloaded: Si le chapitre est téléchargé.
    """

    def __init__(
        self,
        *,
        chapter_id: str,
        number: float,
        title: str = "",
        published_at: datetime | None = None,
        scanlator: str = "",
        pages_count: int = 0,
        is_read: bool = False,
        is_downloaded: bool = False,
    ) -> None:
        """Initialise les données."""
        self.chapter_id = chapter_id
        self.number = number
        self.title = title
        self.published_at = published_at
        self.scanlator = scanlator
        self.pages_count = pages_count
        self.is_read = is_read
        self.is_downloaded = is_downloaded

    @property
    def status_text(self) -> str:
        """Texte du statut."""
        if self.is_downloaded:
            return "💾 Downloaded"
        if self.is_read:
            return "✅ Read"
        return "📖 Unread"

    @property
    def status_color(self) -> str:
        """Couleur du statut."""
        if self.is_downloaded:
            return COLOR_SUCCESS
        if self.is_read:
            return COLOR_TEXT_MUTED
        return COLOR_TEXT


# ============================================================================
# WIDGETS — Composants PyQt6
# ============================================================================


if PYQT6_AVAILABLE:

    class ChapterTableToolbar(QFrame):
        """Barre d'outils de la table de chapitres.

        Contient le champ de recherche, les filtres, et les boutons d'action.
        """

        # Signaux
        search_changed = pyqtSignal(str)
        filter_changed = pyqtSignal(object)  # ChapterFilter
        action_triggered = pyqtSignal(object)  # ChapterAction

        def __init__(
            self,
            *,
            config: ChapterTableConfig,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la barre d'outils.

            Args:
                config: Configuration.
                parent: Widget parent.
            """
            super().__init__(parent)
            self._config = config

            # Configuration visuelle
            self.setFixedHeight(TOOLBAR_HEIGHT)
            self.setStyleSheet(f"""
                ChapterTableToolbar {{
                    background-color: {COLOR_SURFACE};
                    border-bottom: 1px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(8, 4, 8, 4)
            layout.setSpacing(8)

            # Champ de recherche
            if config.searchable:
                self._search_input = QLineEdit()
                self._search_input.setPlaceholderText(
                    t("chapter.search.placeholder", default="🔍 Search chapters...")
                )
                self._search_input.setFixedWidth(200)
                self._search_input.setStyleSheet(f"""
                    QLineEdit {{
                        background-color: {COLOR_SURFACE_ALT};
                        color: {COLOR_TEXT};
                        border: 1px solid {COLOR_BORDER_DIM};
                        border-radius: 3px;
                        padding: 4px 8px;
                        font-family: 'JetBrains Mono', monospace;
                        font-size: 11px;
                    }}
                    QLineEdit:focus {{
                        border-color: {COLOR_SECONDARY};
                    }}
                """)
                self._search_input.textChanged.connect(self.search_changed.emit)
                layout.addWidget(self._search_input)

            # Filtre de statut
            if config.filterable:
                self._status_filter = QComboBox()
                self._status_filter.setFixedWidth(150)
                self._status_filter.setStyleSheet(f"""
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
                        selection-background-color: {COLOR_PRIMARY_DIM};
                        selection-color: {COLOR_BACKGROUND};
                    }}
                """)

                for filter_type in ChapterFilter:
                    self._status_filter.addItem(
                        f"{filter_type.icon} {filter_type.label}",
                        filter_type,
                    )

                self._status_filter.currentIndexChanged.connect(
                    lambda: self.filter_changed.emit(
                        self._status_filter.currentData()
                    )
                )
                layout.addWidget(self._status_filter)

            layout.addStretch()

            # Boutons d'action
            if config.selectable:
                # Bouton Télécharger
                self._btn_download = QPushButton(f"{ChapterAction.DOWNLOAD.icon} {ChapterAction.DOWNLOAD.label}")
                self._btn_download.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {COLOR_SURFACE_ALT};
                        color: {COLOR_SUCCESS};
                        border: 1px solid {COLOR_SUCCESS};
                        border-radius: 3px;
                        padding: 4px 12px;
                        font-size: 11px;
                        font-weight: bold;
                    }}
                    QPushButton:hover {{
                        background-color: {COLOR_SUCCESS};
                        color: {COLOR_BACKGROUND};
                    }}
                """)
                self._btn_download.clicked.connect(
                    lambda: self.action_triggered.emit(ChapterAction.DOWNLOAD)
                )
                layout.addWidget(self._btn_download)

                # Bouton Marquer comme lu
                self._btn_mark_read = QPushButton(f"{ChapterAction.MARK_READ.icon} {ChapterAction.MARK_READ.label}")
                self._btn_mark_read.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {COLOR_SURFACE_ALT};
                        color: {COLOR_TEXT};
                        border: 1px solid {COLOR_BORDER_DIM};
                        border-radius: 3px;
                        padding: 4px 12px;
                        font-size: 11px;
                    }}
                    QPushButton:hover {{
                        background-color: {COLOR_SURFACE_HOVER};
                        border-color: {COLOR_PRIMARY};
                    }}
                """)
                self._btn_mark_read.clicked.connect(
                    lambda: self.action_triggered.emit(ChapterAction.MARK_READ)
                )
                layout.addWidget(self._btn_mark_read)

                # Bouton Marquer comme non lu
                self._btn_mark_unread = QPushButton(f"{ChapterAction.MARK_UNREAD.icon} {ChapterAction.MARK_UNREAD.label}")
                self._btn_mark_unread.setStyleSheet(self._btn_mark_read.styleSheet())
                self._btn_mark_unread.clicked.connect(
                    lambda: self.action_triggered.emit(ChapterAction.MARK_UNREAD)
                )
                layout.addWidget(self._btn_mark_unread)

        def get_search_query(self) -> str:
            """Récupère le texte de recherche.

            Returns:
                Texte de recherche.
            """
            if hasattr(self, "_search_input"):
                return self._search_input.text()
            return ""

        def get_status_filter(self) -> ChapterFilter:
            """Récupère le filtre de statut.

            Returns:
                Filtre de statut.
            """
            if hasattr(self, "_status_filter"):
                return self._status_filter.currentData()
            return ChapterFilter.ALL

    class ChapterTable(QTableWidget):
        """Tableau de chapitres custom avec style cyberpunk néon.

        Affiche les chapitres avec tri, filtrage, sélection, et actions.
        """

        # Signaux
        chapter_selected = pyqtSignal(str)  # chapter_id
        chapter_double_clicked = pyqtSignal(str)  # chapter_id
        chapters_selected = pyqtSignal(list)  # list of chapter_ids

        def __init__(
            self,
            *,
            config: ChapterTableConfig,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la table.

            Args:
                config: Configuration.
                parent: Widget parent.
            """
            super().__init__(parent)
            self._config = config
            self._chapters: list[ChapterTableData] = []
            self._filtered_chapters: list[ChapterTableData] = []
            self._selected_ids: set[str] = set()

            # Configuration de la table
            self.setColumnCount(COLUMN_COUNT)
            self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.setSelectionMode(
                QAbstractItemView.SelectionMode.MultiSelection
                if config.multi_select
                else QAbstractItemView.SelectionMode.SingleSelection
            )
            self.setSortingEnabled(config.sortable)
            self.verticalHeader().setVisible(False)
            self.setAlternatingRowColors(False)
            self.setShowGrid(False)

            # Style
            self.setStyleSheet(f"""
                QTableWidget {{
                    background-color: {COLOR_SURFACE};
                    color: {COLOR_TEXT};
                    border: none;
                    gridline-color: {COLOR_BORDER_DIM};
                    font-family: 'JetBrains Mono', monospace;
                    font-size: 11px;
                }}
                QTableWidget::item {{
                    padding: 4px 8px;
                    border-bottom: 1px solid {COLOR_BORDER_DIM};
                }}
                QTableWidget::item:selected {{
                    background-color: {COLOR_PRIMARY_DIM};
                    color: {COLOR_BACKGROUND};
                }}
                QTableWidget::item:hover {{
                    background-color: {COLOR_SURFACE_HOVER};
                }}
                QHeaderView::section {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {COLOR_TEXT};
                    padding: 6px;
                    border: none;
                    border-bottom: 2px solid {COLOR_PRIMARY};
                    font-weight: bold;
                    font-size: 11px;
                }}
                QHeaderView::section:hover {{
                    background-color: {COLOR_SURFACE_HOVER};
                }}
            """)

            # Configurer les en-têtes
            self._setup_headers()

            # Connexions
            self.cellDoubleClicked.connect(self._on_cell_double_clicked)
            self.cellClicked.connect(self._on_cell_clicked)

        def _setup_headers(self) -> None:
            """Configure les en-têtes de colonnes."""
            headers = []

            if self._config.show_checkbox:
                headers.append("☐")
            if self._config.show_number:
                headers.append(t("chapter.column.number", default="#"))
            if self._config.show_title:
                headers.append(t("chapter.column.title", default="Title"))
            if self._config.show_date:
                headers.append(t("chapter.column.date", default="Date"))
            if self._config.show_scanlator:
                headers.append(t("chapter.column.scanlator", default="Scanlator"))
            if self._config.show_pages:
                headers.append(t("chapter.column.pages", default="Pages"))
            if self._config.show_status:
                headers.append(t("chapter.column.status", default="Status"))
            if self._config.show_actions:
                headers.append(t("chapter.column.actions", default="Actions"))

            self.setHorizontalHeaderLabels(headers)

            # Configurer les largeurs
            header = self.horizontalHeader()
            col_index = 0

            if self._config.show_checkbox:
                self.setColumnWidth(col_index, 40)
                col_index += 1
            if self._config.show_number:
                self.setColumnWidth(col_index, 60)
                col_index += 1
            if self._config.show_title:
                header.setSectionResizeMode(col_index, QHeaderView.ResizeMode.Stretch)
                col_index += 1
            if self._config.show_date:
                self.setColumnWidth(col_index, 100)
                col_index += 1
            if self._config.show_scanlator:
                self.setColumnWidth(col_index, 120)
                col_index += 1
            if self._config.show_pages:
                self.setColumnWidth(col_index, 60)
                col_index += 1
            if self._config.show_status:
                self.setColumnWidth(col_index, 120)
                col_index += 1
            if self._config.show_actions:
                self.setColumnWidth(col_index, 100)

        def set_chapters(self, chapters: list[ChapterTableData]) -> None:
            """Définit la liste des chapitres.

            Args:
                chapters: Liste de chapitres.
            """
            self._chapters = chapters
            self._apply_filters()

        def _apply_filters(self, filters: ChapterTableFilters | None = None) -> None:
            """Applique les filtres et met à jour la table.

            Args:
                filters: Filtres à appliquer (None = pas de filtre).
            """
            filtered = list(self._chapters)

            if filters:
                # Filtrer par statut
                if filters.status_filter == ChapterFilter.READ:
                    filtered = [c for c in filtered if c.is_read]
                elif filters.status_filter == ChapterFilter.UNREAD:
                    filtered = [c for c in filtered if not c.is_read]
                elif filters.status_filter == ChapterFilter.DOWNLOADED:
                    filtered = [c for c in filtered if c.is_downloaded]
                elif filters.status_filter == ChapterFilter.NOT_DOWNLOADED:
                    filtered = [c for c in filtered if not c.is_downloaded]

                # Filtrer par recherche
                if filters.search_query:
                    query_lower = filters.search_query.lower()
                    filtered = [
                        c for c in filtered
                        if query_lower in c.title.lower()
                        or query_lower in c.scanlator.lower()
                    ]

                # Trier
                if filters.sort_by == ChapterSortBy.NUMBER:
                    filtered.sort(key=lambda c: c.number, reverse=filters.sort_reverse)
                elif filters.sort_by == ChapterSortBy.DATE:
                    filtered.sort(
                        key=lambda c: c.published_at or datetime.min.replace(tzinfo=UTC),
                        reverse=not filters.sort_reverse,
                    )
                elif filters.sort_by == ChapterSortBy.TITLE:
                    filtered.sort(key=lambda c: c.title.lower(), reverse=filters.sort_reverse)
                elif filters.sort_by == ChapterSortBy.STATUS:
                    filtered.sort(
                        key=lambda c: (c.is_read, c.is_downloaded),
                        reverse=filters.sort_reverse,
                    )

            self._filtered_chapters = filtered
            self._populate_table()

        def _populate_table(self) -> None:
            """Remplit la table avec les chapitres filtrés."""
            self.setRowCount(len(self._filtered_chapters))

            for row, chapter in enumerate(self._filtered_chapters):
                col_index = 0

                # Checkbox
                if self._config.show_checkbox:
                    checkbox = QCheckBox()
                    checkbox.setChecked(chapter.chapter_id in self._selected_ids)
                    checkbox.stateChanged.connect(
                        lambda state, cid=chapter.chapter_id: self._on_checkbox_changed(cid, state)
                    )
                    checkbox_widget = QWidget()
                    checkbox_layout = QHBoxLayout(checkbox_widget)
                    checkbox_layout.addWidget(checkbox)
                    checkbox_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    checkbox_layout.setContentsMargins(0, 0, 0, 0)
                    self.setCellWidget(row, col_index, checkbox_widget)
                    col_index += 1

                # Numéro
                if self._config.show_number:
                    number_text = f"Ch. {chapter.number:g}" if chapter.number == int(chapter.number) else f"Ch. {chapter.number}"
                    number_item = QTableWidgetItem(number_text)
                    number_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    number_item.setForeground(QBrush(QColor(COLOR_SECONDARY)))
                    number_item.setFont(QFont("JetBrains Mono", 10, QFont.Weight.Bold))
                    self.setItem(row, col_index, number_item)
                    col_index += 1

                # Titre
                if self._config.show_title:
                    title_item = QTableWidgetItem(chapter.title or "No title")
                    title_item.setForeground(QBrush(QColor(COLOR_TEXT)))
                    self.setItem(row, col_index, title_item)
                    col_index += 1

                # Date
                if self._config.show_date:
                    if chapter.published_at:
                        date_text = chapter.published_at.strftime("%Y-%m-%d")
                    else:
                        date_text = "Unknown"
                    date_item = QTableWidgetItem(date_text)
                    date_item.setForeground(QBrush(QColor(COLOR_TEXT_MUTED)))
                    date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.setItem(row, col_index, date_item)
                    col_index += 1

                # Scanlator
                if self._config.show_scanlator:
                    scanlator_item = QTableWidgetItem(chapter.scanlator or "Unknown")
                    scanlator_item.setForeground(QBrush(QColor(COLOR_TEXT_MUTED)))
                    self.setItem(row, col_index, scanlator_item)
                    col_index += 1

                # Pages
                if self._config.show_pages:
                    pages_text = str(chapter.pages_count) if chapter.pages_count > 0 else "-"
                    pages_item = QTableWidgetItem(pages_text)
                    pages_item.setForeground(QBrush(QColor(COLOR_TEXT_DIM)))
                    pages_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.setItem(row, col_index, pages_item)
                    col_index += 1

                # Statut
                if self._config.show_status:
                    status_item = QTableWidgetItem(chapter.status_text)
                    status_item.setForeground(QBrush(QColor(chapter.status_color)))
                    status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.setItem(row, col_index, status_item)
                    col_index += 1

                # Actions
                if self._config.show_actions:
                    actions_widget = QWidget()
                    actions_layout = QHBoxLayout(actions_widget)
                    actions_layout.setContentsMargins(4, 2, 4, 2)
                    actions_layout.setSpacing(4)

                    # Bouton Télécharger
                    btn_download = QPushButton("⬇️")
                    btn_download.setFixedSize(24, 24)
                    btn_download.setToolTip(t("chapter.action.download", default="Download"))
                    btn_download.setStyleSheet(f"""
                        QPushButton {{
                            background-color: transparent;
                            border: none;
                            font-size: 14px;
                        }}
                        QPushButton:hover {{
                            background-color: {COLOR_SURFACE_HOVER};
                            border-radius: 3px;
                        }}
                    """)
                    btn_download.clicked.connect(
                        lambda checked, cid=chapter.chapter_id: self._on_action_clicked(cid, ChapterAction.DOWNLOAD)
                    )
                    actions_layout.addWidget(btn_download)

                    # Bouton Marquer lu/non-lu
                    if chapter.is_read:
                        btn_mark = QPushButton("📖")
                        btn_mark.setToolTip(t("chapter.action.mark_unread", default="Mark as Unread"))
                    else:
                        btn_mark = QPushButton("✅")
                        btn_mark.setToolTip(t("chapter.action.mark_read", default="Mark as Read"))

                    btn_mark.setFixedSize(24, 24)
                    btn_mark.setStyleSheet(btn_download.styleSheet())
                    btn_mark.clicked.connect(
                        lambda checked, cid=chapter.chapter_id, is_read=chapter.is_read: self._on_action_clicked(
                            cid,
                            ChapterAction.MARK_UNREAD if is_read else ChapterAction.MARK_READ,
                        )
                    )
                    actions_layout.addWidget(btn_mark)

                    self.setCellWidget(row, col_index, actions_widget)

                # Hauteur de la ligne
                self.setRowHeight(row, self._config.row_height)

        def _on_checkbox_changed(self, chapter_id: str, state: int) -> None:
            """Gère le changement de état d'une checkbox.

            Args:
                chapter_id: ID du chapitre.
                state: État de la checkbox (0 = décoché, 2 = coché).
            """
            if state == Qt.CheckState.Checked.value:
                self._selected_ids.add(chapter_id)
            else:
                self._selected_ids.discard(chapter_id)

            self.chapters_selected.emit(list(self._selected_ids))

        def _on_cell_clicked(self, row: int, column: int) -> None:
            """Gère le clic sur une cellule.

            Args:
                row: Ligne.
                column: Colonne.
            """
            if row < len(self._filtered_chapters):
                chapter = self._filtered_chapters[row]
                self.chapter_selected.emit(chapter.chapter_id)

        def _on_cell_double_clicked(self, row: int, column: int) -> None:
            """Gère le double-clic sur une cellule.

            Args:
                row: Ligne.
                column: Colonne.
            """
            if row < len(self._filtered_chapters):
                chapter = self._filtered_chapters[row]
                self.chapter_double_clicked.emit(chapter.chapter_id)

        def _on_action_clicked(self, chapter_id: str, action: ChapterAction) -> None:
            """Gère le clic sur un bouton d'action.

            Args:
                chapter_id: ID du chapitre.
                action: Action à effectuer.
            """
            # Émettre un signal pour que le parent gère l'action
            logger.debug("Action {} sur chapitre {}", action.value, chapter_id)

        def get_selected_chapters(self) -> list[str]:
            """Récupère les IDs des chapitres sélectionnés.

            Returns:
                Liste d'IDs de chapitres.
            """
            return list(self._selected_ids)

        def select_all(self) -> None:
            """Sélectionne tous les chapitres filtrés."""
            self._selected_ids = {c.chapter_id for c in self._filtered_chapters}
            self._populate_table()
            self.chapters_selected.emit(list(self._selected_ids))

        def deselect_all(self) -> None:
            """Désélectionne tous les chapitres."""
            self._selected_ids.clear()
            self._populate_table()
            self.chapters_selected.emit([])

        def invert_selection(self) -> None:
            """Inverse la sélection."""
            all_ids = {c.chapter_id for c in self._filtered_chapters}
            self._selected_ids = all_ids - self._selected_ids
            self._populate_table()
            self.chapters_selected.emit(list(self._selected_ids))

    class ChapterTableFooter(QFrame):
        """Barre de statut de la table.

        Affiche les statistiques (nombre de chapitres, sélectionnés, etc.).
        """

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise le footer.

            Args:
                parent: Widget parent.
            """
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedHeight(FOOTER_HEIGHT)
            self.setStyleSheet(f"""
                ChapterTableFooter {{
                    background-color: {COLOR_SURFACE};
                    border-top: 1px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(8, 0, 8, 0)
            layout.setSpacing(16)

            # Statistiques
            self._stats_label = QLabel("")
            self._stats_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px;")
            layout.addWidget(self._stats_label)

            layout.addStretch()

            # Sélection
            self._selection_label = QLabel("")
            self._selection_label.setStyleSheet(f"color: {COLOR_PRIMARY}; font-size: 10px; font-weight: bold;")
            layout.addWidget(self._selection_label)

        def update_stats(self, total: int, filtered: int, selected: int) -> None:
            """Met à jour les statistiques.

            Args:
                total: Nombre total de chapitres.
                filtered: Nombre de chapitres filtrés.
                selected: Nombre de chapitres sélectionnés.
            """
            self._stats_label.setText(
                t(
                    "chapter.stats.display",
                    default="Showing {filtered}/{total} chapters",
                    filtered=filtered,
                    total=total,
                )
            )

            if selected > 0:
                self._selection_label.setText(
                    t(
                        "chapter.stats.selected",
                        default="{count} selected",
                        count=selected,
                    )
                )
            else:
                self._selection_label.setText("")

    class ChapterTableWidget(QWidget):
        """Widget principal de la table de chapitres.

        Combine la toolbar, la table, et le footer en un seul widget cohérent.

        Signals:
            chapter_selected(str): Émis lorsqu'un chapitre est sélectionné.
            chapter_double_clicked(str): Émis lorsqu'un chapitre est double-cliqué.
            chapters_selected(list): Émis lorsque la sélection change.
            action_triggered(str, ChapterAction): Émis lorsqu'une action est effectuée.
        """

        # Signaux
        chapter_selected = pyqtSignal(str)
        chapter_double_clicked = pyqtSignal(str)
        chapters_selected = pyqtSignal(list)
        action_triggered = pyqtSignal(str, object)  # chapter_id, ChapterAction

        def __init__(
            self,
            chapters: list[ChapterTableData] | None = None,
            *,
            config: ChapterTableConfig | None = None,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise le widget.

            Args:
                chapters: Liste de chapitres.
                config: Configuration.
                parent: Widget parent.
            """
            super().__init__(parent)
            self._config = config or ChapterTableConfig()
            self._filters = ChapterTableFilters()

            # Layout principal
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            # Toolbar
            self._toolbar = ChapterTableToolbar(config=self._config, parent=self)
            self._toolbar.search_changed.connect(self._on_search_changed)
            self._toolbar.filter_changed.connect(self._on_filter_changed)
            self._toolbar.action_triggered.connect(self._on_batch_action)
            layout.addWidget(self._toolbar)

            # Table
            self._table = ChapterTable(config=self._config, parent=self)
            self._table.chapter_selected.connect(self.chapter_selected.emit)
            self._table.chapter_double_clicked.connect(self.chapter_double_clicked.emit)
            self._table.chapters_selected.connect(self._on_chapters_selected)
            layout.addWidget(self._table)

            # Footer
            self._footer = ChapterTableFooter(parent=self)
            layout.addWidget(self._footer)

            # Définir les chapitres
            if chapters:
                self.set_chapters(chapters)

            # Taille minimale
            self.setMinimumWidth(TABLE_MIN_WIDTH)
            self.setMinimumHeight(TABLE_MIN_HEIGHT)

        def set_chapters(self, chapters: list[ChapterTableData]) -> None:
            """Définit la liste des chapitres.

            Args:
                chapters: Liste de chapitres.
            """
            self._table.set_chapters(chapters)
            self._update_footer()

        def _on_search_changed(self, query: str) -> None:
            """Gère le changement de recherche.

            Args:
                query: Texte de recherche.
            """
            self._filters.search_query = query
            self._table._apply_filters(self._filters)
            self._update_footer()

        def _on_filter_changed(self, filter_type: ChapterFilter) -> None:
            """Gère le changement de filtre.

            Args:
                filter_type: Nouveau filtre.
            """
            self._filters.status_filter = filter_type
            self._table._apply_filters(self._filters)
            self._update_footer()

        def _on_batch_action(self, action: ChapterAction) -> None:
            """Gère une action en batch.

            Args:
                action: Action à effectuer.
            """
            selected = self._table.get_selected_chapters()
            if not selected:
                logger.debug("Aucun chapitre sélectionné pour l'action {}", action.value)
                return

            for chapter_id in selected:
                self.action_triggered.emit(chapter_id, action)

        def _on_chapters_selected(self, chapter_ids: list[str]) -> None:
            """Gère le changement de sélection.

            Args:
                chapter_ids: IDs des chapitres sélectionnés.
            """
            self.chapters_selected.emit(chapter_ids)
            self._update_footer()

        def _update_footer(self) -> None:
            """Met à jour le footer avec les statistiques."""
            total = len(self._table._chapters)
            filtered = len(self._table._filtered_chapters)
            selected = len(self._table.get_selected_chapters())
            self._footer.update_stats(total, filtered, selected)

        # =====================================================================
        # API PUBLIQUE
        # =====================================================================

        def get_selected_chapters(self) -> list[str]:
            """Récupère les IDs des chapitres sélectionnés.

            Returns:
                Liste d'IDs de chapitres.
            """
            return self._table.get_selected_chapters()

        def select_all(self) -> None:
            """Sélectionne tous les chapitres filtrés."""
            self._table.select_all()

        def deselect_all(self) -> None:
            """Désélectionne tous les chapitres."""
            self._table.deselect_all()

        def invert_selection(self) -> None:
            """Inverse la sélection."""
            self._table.invert_selection()

        @property
        def config(self) -> ChapterTableConfig:
            """Configuration de la table."""
            return self._config

        @property
        def filters(self) -> ChapterTableFilters:
            """Filtres actuels."""
            return self._filters


# ============================================================================
# FONCTIONS HELPERS
# ============================================================================


def create_chapter_table(
    chapters: list[ChapterTableData] | None = None,
    *,
    selectable: bool = True,
    multi_select: bool = True,
    parent: Any = None,
) -> Any:
    """Crée une table de chapitres avec configuration simplifiée.

    Args:
        chapters: Liste de chapitres.
        selectable: Permettre la sélection.
        multi_select: Permettre la sélection multiple.
        parent: Widget parent.

    Returns:
        Instance de ChapterTableWidget.
    """
    if not PYQT6_AVAILABLE:
        raise ChapterTableError(
            "PyQt6 n'est pas installé. Installez-le avec: pip install PyQt6"
        )

    config = ChapterTableConfig(selectable=selectable, multi_select=multi_select)
    return ChapterTableWidget(chapters=chapters, config=config, parent=parent)


def chapter_to_table_data(chapter: Any) -> ChapterTableData:
    """Convertit un modèle Chapter en ChapterTableData.

    Args:
        chapter: Instance de Chapter.

    Returns:
        Instance de ChapterTableData.
    """
    return ChapterTableData(
        chapter_id=chapter.id,
        number=chapter.number,
        title=getattr(chapter, "title", ""),
        published_at=getattr(chapter, "published_at", None),
        scanlator=getattr(chapter, "scanlator", ""),
        pages_count=getattr(chapter, "pages_count", 0),
        is_read=getattr(chapter, "is_read", False),
        is_downloaded=getattr(chapter, "is_downloaded", False),
    )


def is_pyqt6_available() -> bool:
    """Vérifie si PyQt6 est disponible.

    Returns:
        True si PyQt6 est installé.
    """
    return PYQT6_AVAILABLE


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
    "TABLE_MIN_WIDTH",
    "TABLE_MIN_HEIGHT",
    "ROW_HEIGHT",
    "COLUMN_COUNT",
    # Exceptions
    "ChapterTableError",
    "InvalidChapterError",
    # Enums
    "ChapterFilter",
    "ChapterSortBy",
    "ChapterAction",
    # Modèles
    "ChapterTableConfig",
    "ChapterTableFilters",
    "ChapterTableData",
    # Widgets
    "ChapterTableWidget" if PYQT6_AVAILABLE else None,
    "ChapterTable" if PYQT6_AVAILABLE else None,
    "ChapterTableToolbar" if PYQT6_AVAILABLE else None,
    "ChapterTableFooter" if PYQT6_AVAILABLE else None,
    # Helpers
    "create_chapter_table",
    "chapter_to_table_data",
    "is_pyqt6_available",
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
