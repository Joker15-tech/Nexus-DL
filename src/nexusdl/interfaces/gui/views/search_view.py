"""Vue de recherche pour l'interface graphique NexusDL.

Ce module fournit une vue PyQt6 complète pour rechercher des mangas/webtoons/comics
sur les sites supportés. Elle offre une expérience utilisateur riche avec recherche
multi-sites, filtrage, tri, sélection multiple, et téléchargement.

**Fonctionnalités** :
    - Recherche multi-sites en parallèle
    - Affichage des résultats en grille de cartes
    - Filtrage par langue, statut, contenu adulte
    - Tri par pertinence, titre, date, statut
    - Sélection multiple pour téléchargement
    - Indicateur de progression de la recherche
    - Gestion des erreurs par site
    - Pagination ou scroll infini
    - Signaux Qt pour communication
    - Traductions i18n
    - Style cyberpunk néon cohérent

**Architecture** :
    SearchView (QWidget principal)
        ├── SearchBar (barre de recherche)
        │   ├── SearchInput (champ de texte)
        │   ├── SiteDropdown (sélection de sites)
        │   └── SearchButton (bouton de recherche)
        ├── SearchFilters (filtres)
        │   ├── LanguageFilter (filtre langue)
        │   ├── StatusFilter (filtre statut)
        │   └── SortFilter (tri)
        ├── SearchProgress (progression de la recherche)
        │   ├── ProgressBar (barre de progression)
        │   └── StatusLabel (statut actuel)
        ├── SearchResults (grille de résultats)
        │   └── MangaCard (carte individuelle)
        └── SearchFooter (statistiques + actions)
            ├── StatsLabel (nombre de résultats)
            └── ActionButtons (télécharger, sélectionner tout)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.gui.views.search_view import SearchView
    >>>
    >>> # Dans une fenêtre PyQt6
    >>> search_view = SearchView(parent=self)
    >>> search_view.mangas_selected.connect(self.on_mangas_selected)
    >>> layout.addWidget(search_view)

Intégration :
    - core/registry/site_registry.py : accès aux sites
    - core/parsers/* : exécution des recherches
    - core/models/manga.py : modèles Manga, SearchResult
    - core/events.py : émission d'événements
    - core/i18n.py : traductions
    - interfaces/gui/components/manga_card.py : MangaCard
    - interfaces/gui/components/site_dropdown.py : SiteDropdown
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
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSpinBox,
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
from nexusdl.core.models.manga import Language, Manga, MangaStatus, SearchResult


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
SEARCH_BAR_HEIGHT: Final[int] = 50
FILTERS_HEIGHT: Final[int] = 40
PROGRESS_HEIGHT: Final[int] = 30
FOOTER_HEIGHT: Final[int] = 40
CARD_WIDTH: Final[int] = 200
CARD_HEIGHT: Final[int] = 300
GRID_SPACING: Final[int] = 12
GRID_MARGIN: Final[int] = 16


# ============================================================================
# EXCEPTIONS
# ============================================================================


class SearchViewError(NexusDLError):
    """Exception de base pour les erreurs de la vue de recherche."""


class SearchExecutionError(SearchViewError):
    """Exception levée lorsqu'une recherche échoue.

    Attributes:
        query: Requête de recherche.
        site_id: ID du site (si applicable).
        reason: Raison de l'échec.
    """

    def __init__(self, query: str, site_id: str | None = None, reason: str = "") -> None:
        msg = f"Échec de la recherche: {query!r}"
        if site_id:
            msg += f" sur {site_id}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.query = query
        self.site_id = site_id
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class SearchSortBy(str, Enum):
    """Critère de tri des résultats.

    Attributes:
        RELEVANCE: Tri par pertinence.
        TITLE: Tri par titre.
        DATE: Tri par date.
        STATUS: Tri par statut.
    """

    RELEVANCE = "relevance"
    TITLE = "title"
    DATE = "date"
    STATUS = "status"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            SearchSortBy.RELEVANCE: t("search.sort.relevance", default="Relevance"),
            SearchSortBy.TITLE: t("search.sort.title", default="Title"),
            SearchSortBy.DATE: t("search.sort.date", default="Date"),
            SearchSortBy.STATUS: t("search.sort.status", default="Status"),
        }[self]


class SearchState(str, Enum):
    """État de la recherche.

    Attributes:
        IDLE: Pas de recherche en cours.
        SEARCHING: Recherche en cours.
        RESULTS: Résultats affichés.
        ERROR: Erreur lors de la recherche.
        NO_RESULTS: Aucun résultat trouvé.
    """

    IDLE = "idle"
    SEARCHING = "searching"
    RESULTS = "results"
    ERROR = "error"
    NO_RESULTS = "no_results"

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            SearchState.IDLE: "⏸️",
            SearchState.SEARCHING: "🔍",
            SearchState.RESULTS: "✅",
            SearchState.ERROR: "❌",
            SearchState.NO_RESULTS: "⚠️",
        }[self]


# ============================================================================
# MODÈLES DE DONNÉES
# ============================================================================


class SearchFilters:
    """Filtres de recherche.

    Attributes:
        language: Langue des résultats.
        status: Statut des mangas.
        content_rating: Classification d'âge.
        sort_by: Critère de tri.
        include_adult: Inclure le contenu adulte.
    """

    def __init__(
        self,
        *,
        language: str | None = None,
        status: str | None = None,
        content_rating: str | None = None,
        sort_by: SearchSortBy = SearchSortBy.RELEVANCE,
        include_adult: bool = False,
    ) -> None:
        """Initialise les filtres."""
        self.language = language
        self.status = status
        self.content_rating = content_rating
        self.sort_by = sort_by
        self.include_adult = include_adult


class SearchQuery:
    """Requête de recherche.

    Attributes:
        query: Texte de recherche.
        site_ids: IDs des sites à rechercher.
        filters: Filtres appliqués.
        page: Numéro de page.
        page_size: Nombre de résultats par page.
    """

    def __init__(
        self,
        *,
        query: str,
        site_ids: list[str] | None = None,
        filters: SearchFilters | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> None:
        """Initialise la requête."""
        self.query = query
        self.site_ids = site_ids or []
        self.filters = filters or SearchFilters()
        self.page = page
        self.page_size = page_size


class SearchResults:
    """Résultats de recherche.

    Attributes:
        query: Requête originale.
        results: Liste des résultats.
        total: Nombre total de résultats.
        page: Page actuelle.
        page_size: Taille de page.
        duration_ms: Durée de la recherche.
        sites_searched: Nombre de sites recherchés.
        errors: Erreurs par site.
    """

    def __init__(
        self,
        *,
        query: str,
        results: list[SearchResult] | None = None,
        total: int = 0,
        page: int = 1,
        page_size: int = 20,
        duration_ms: float = 0.0,
        sites_searched: int = 0,
        errors: dict[str, str] | None = None,
    ) -> None:
        """Initialise les résultats."""
        self.query = query
        self.results = results or []
        self.total = total
        self.page = page
        self.page_size = page_size
        self.duration_ms = duration_ms
        self.sites_searched = sites_searched
        self.errors = errors or {}

    @property
    def has_results(self) -> bool:
        """Indique s'il y a des résultats."""
        return len(self.results) > 0


class SearchViewState:
    """État de la vue de recherche.

    Attributes:
        state: État actuel.
        query: Requête actuelle.
        results: Résultats actuels.
        selected_ids: IDs des mangas sélectionnés.
        error_message: Message d'erreur.
        search_started_at: Timestamp de début de recherche.
    """

    def __init__(self) -> None:
        """Initialise l'état."""
        self.state = SearchState.IDLE
        self.query: SearchQuery | None = None
        self.results: SearchResults | None = None
        self.selected_ids: set[str] = set()
        self.error_message: str | None = None
        self.search_started_at: datetime | None = None

    @property
    def selected_count(self) -> int:
        """Nombre de mangas sélectionnés."""
        return len(self.selected_ids)

    @property
    def is_searching(self) -> bool:
        """Indique si une recherche est en cours."""
        return self.state == SearchState.SEARCHING


# ============================================================================
# WIDGETS — Composants PyQt6
# ============================================================================


if PYQT6_AVAILABLE:

    class SearchBar(QFrame):
        """Barre de recherche avec champ de texte et sélection de sites."""

        # Signaux
        search_requested = pyqtSignal(str, list)  # query, site_ids

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la barre de recherche."""
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedHeight(SEARCH_BAR_HEIGHT)
            self.setStyleSheet(f"""
                SearchBar {{
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
                t("search.input.placeholder", default="🔍 Search for manga, webtoon, comics...")
            )
            self._search_input.setStyleSheet(f"""
                QLineEdit {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {COLOR_TEXT};
                    border: 2px solid {COLOR_BORDER_DIM};
                    border-radius: 4px;
                    padding: 8px 12px;
                    font-family: 'JetBrains Mono', monospace;
                    font-size: 13px;
                }}
                QLineEdit:focus {{
                    border-color: {COLOR_SECONDARY};
                }}
                QLineEdit::placeholder {{
                    color: {COLOR_TEXT_DIM};
                }}
            """)
            self._search_input.returnPressed.connect(self._on_search)
            layout.addWidget(self._search_input, stretch=1)

            # Dropdown de sites
            from nexusdl.interfaces.gui.components.site_dropdown import SiteDropdown, SelectionMode
            self._site_dropdown = SiteDropdown(
                mode=SelectionMode.MULTIPLE,
                include_all_option=True,
                parent=self,
            )
            self._site_dropdown.setFixedWidth(300)
            layout.addWidget(self._site_dropdown)

            # Bouton de recherche
            self._search_button = QPushButton(t("search.button.search", default="Search"))
            self._search_button.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_PRIMARY_BG};
                    color: {COLOR_PRIMARY};
                    border: 2px solid {COLOR_PRIMARY};
                    border-radius: 4px;
                    padding: 8px 24px;
                    font-size: 13px;
                    font-weight: bold;
                    font-family: 'JetBrains Mono', monospace;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_PRIMARY};
                    color: {COLOR_BACKGROUND};
                }}
                QPushButton:pressed {{
                    background-color: {COLOR_PRIMARY_DIM};
                }}
                QPushButton:disabled {{
                    background-color: {COLOR_SURFACE};
                    color: {COLOR_TEXT_DISABLED};
                    border-color: {COLOR_BORDER_DIM};
                }}
            """)
            self._search_button.clicked.connect(self._on_search)
            layout.addWidget(self._search_button)

        def _on_search(self) -> None:
            """Gère le clic sur le bouton de recherche."""
            query = self._search_input.text().strip()
            if not query:
                return

            site_ids = self._site_dropdown.get_selected_sites()
            self.search_requested.emit(query, site_ids)

        def set_searching(self, is_searching: bool) -> None:
            """Définit l'état de recherche.

            Args:
                is_searching: True si recherche en cours.
            """
            self._search_button.setEnabled(not is_searching)
            self._search_input.setEnabled(not is_searching)
            self._site_dropdown.setEnabled(not is_searching)

            if is_searching:
                self._search_button.setText(t("search.button.searching", default="Searching..."))
            else:
                self._search_button.setText(t("search.button.search", default="Search"))

    class SearchFiltersBar(QFrame):
        """Barre de filtres de recherche."""

        # Signaux
        filters_changed = pyqtSignal(object)  # SearchFilters

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la barre de filtres."""
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedHeight(FILTERS_HEIGHT)
            self.setStyleSheet(f"""
                SearchFiltersBar {{
                    background-color: {COLOR_SURFACE_ALT};
                    border-bottom: 1px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(16, 4, 16, 4)
            layout.setSpacing(12)

            # Filtre langue
            layout.addWidget(QLabel(t("search.filter.language", default="Language:")))
            self._lang_filter = QComboBox()
            self._lang_filter.setFixedWidth(150)
            self._lang_filter.setStyleSheet(self._combo_style())
            self._lang_filter.addItem(t("search.filter.all", default="All"), None)
            self._lang_filter.addItem("🇫🇷 Français", "fr")
            self._lang_filter.addItem("🇬🇧 English", "en")
            self._lang_filter.addItem("🇯🇵 日本語", "ja")
            self._lang_filter.addItem("🇰🇷 한국어", "ko")
            self._lang_filter.addItem("🇨🇳 中文", "zh")
            self._lang_filter.currentIndexChanged.connect(self._on_filters_changed)
            layout.addWidget(self._lang_filter)

            # Filtre statut
            layout.addWidget(QLabel(t("search.filter.status", default="Status:")))
            self._status_filter = QComboBox()
            self._status_filter.setFixedWidth(150)
            self._status_filter.setStyleSheet(self._combo_style())
            self._status_filter.addItem(t("search.filter.all", default="All"), None)
            self._status_filter.addItem(t("search.filter.ongoing", default="Ongoing"), "ongoing")
            self._status_filter.addItem(t("search.filter.completed", default="Completed"), "completed")
            self._status_filter.addItem(t("search.filter.hiatus", default="Hiatus"), "hiatus")
            self._status_filter.currentIndexChanged.connect(self._on_filters_changed)
            layout.addWidget(self._status_filter)

            # Tri
            layout.addWidget(QLabel(t("search.filter.sort", default="Sort:")))
            self._sort_filter = QComboBox()
            self._sort_filter.setFixedWidth(150)
            self._sort_filter.setStyleSheet(self._combo_style())
            for sort_by in SearchSortBy:
                self._sort_filter.addItem(sort_by.label, sort_by.value)
            self._sort_filter.currentIndexChanged.connect(self._on_filters_changed)
            layout.addWidget(self._sort_filter)

            layout.addStretch()

        def _combo_style(self) -> str:
            """Retourne le style pour les ComboBox."""
            return f"""
                QComboBox {{
                    background-color: {COLOR_SURFACE};
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
            """

        def _on_filters_changed(self) -> None:
            """Gère le changement de filtres."""
            filters = SearchFilters(
                language=self._lang_filter.currentData(),
                status=self._status_filter.currentData(),
                sort_by=SearchSortBy(self._sort_filter.currentData()),
            )
            self.filters_changed.emit(filters)

        def get_filters(self) -> SearchFilters:
            """Récupère les filtres actuels.

            Returns:
                Instance de SearchFilters.
            """
            return SearchFilters(
                language=self._lang_filter.currentData(),
                status=self._status_filter.currentData(),
                sort_by=SearchSortBy(self._sort_filter.currentData()),
            )

    class SearchProgress(QFrame):
        """Barre de progression de la recherche."""

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la barre de progression."""
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedHeight(PROGRESS_HEIGHT)
            self.setStyleSheet(f"""
                SearchProgress {{
                    background-color: {COLOR_SURFACE};
                    border-bottom: 1px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(16, 4, 16, 4)
            layout.setSpacing(12)

            # Label de statut
            self._status_label = QLabel("")
            self._status_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
            layout.addWidget(self._status_label)

            # Barre de progression
            from nexusdl.interfaces.gui.components.progress_widget import ProgressWidget, ProgressStyle
            self._progress_bar = ProgressWidget(
                style=ProgressStyle.GRADIENT,
                parent=self,
            )
            self._progress_bar.setFixedHeight(20)
            layout.addWidget(self._progress_bar, stretch=1)

            # Cacher par défaut
            self.setVisible(False)

        def set_progress(self, current: int, total: int, status: str = "") -> None:
            """Définit la progression.

            Args:
                current: Valeur actuelle.
                total: Valeur totale.
                status: Message de statut.
            """
            self.setVisible(True)
            self._progress_bar.set_progress(current, total)
            self._status_label.setText(status)

        def set_completed(self, status: str = "") -> None:
            """Marque la progression comme terminée.

            Args:
                status: Message de statut.
            """
            self._progress_bar.set_state(
                __import__('nexusdl.interfaces.gui.components.progress_widget', fromlist=['ProgressState']).ProgressState.COMPLETED
            )
            self._status_label.setText(status)

        def hide_progress(self) -> None:
            """Cache la barre de progression."""
            self.setVisible(False)
            self._progress_bar.reset()

    class SearchResultsGrid(QScrollArea):
        """Grille de résultats de recherche."""

        # Signaux
        manga_selected = pyqtSignal(str)  # manga_id
        manga_double_clicked = pyqtSignal(str)  # manga_id
        mangas_selection_changed = pyqtSignal(list)  # list of manga_ids

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la grille."""
            super().__init__(parent)

            # Configuration
            self.setWidgetResizable(True)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setStyleSheet(f"""
                QScrollArea {{
                    background-color: {COLOR_BACKGROUND};
                    border: none;
                }}
            """)

            # Widget conteneur
            self._container = QWidget()
            self._container.setStyleSheet(f"background-color: {COLOR_BACKGROUND};")
            self._layout = QGridLayout(self._container)
            self._layout.setContentsMargins(GRID_MARGIN, GRID_MARGIN, GRID_MARGIN, GRID_MARGIN)
            self._layout.setSpacing(GRID_SPACING)

            self.setWidget(self._container)

            # Cartes de mangas
            self._cards: dict[str, Any] = {}  # manga_id -> MangaCard
            self._selected_ids: set[str] = set()

        def set_results(self, results: list[SearchResult]) -> None:
            """Définit les résultats à afficher.

            Args:
                results: Liste de SearchResult.
            """
            self._clear()

            if not results:
                # Afficher un message "Aucun résultat"
                no_results_label = QLabel(t("search.no_results", default="No results found"))
                no_results_label.setStyleSheet(f"""
                    color: {COLOR_TEXT_MUTED};
                    font-size: 16px;
                    padding: 40px;
                """)
                no_results_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._layout.addWidget(no_results_label, 0, 0, 1, 4)
                return

            # Calculer le nombre de colonnes
            width = self.width() - (2 * GRID_MARGIN)
            columns = max(1, (width + GRID_SPACING) // (CARD_WIDTH + GRID_SPACING))

            # Ajouter les cartes
            from nexusdl.interfaces.gui.components.manga_card import MangaCard, CardLayout, manga_to_card_data
            for i, result in enumerate(results):
                row = i // columns
                col = i % columns

                card_data = manga_to_card_data(result.manga)
                card = MangaCard(
                    card_data,
                    layout=CardLayout.VERTICAL,
                    selectable=True,
                    parent=self._container,
                )
                card.clicked.connect(lambda m=result.manga: self._on_card_clicked(m.id))
                card.double_clicked.connect(lambda m=result.manga: self._on_card_double_clicked(m.id))
                card.selected.connect(lambda selected, m=result.manga: self._on_card_selected(m.id, selected))

                self._layout.addWidget(card, row, col)
                self._cards[result.manga.id] = card

            # Ajouter un stretch à la fin
            self._layout.setRowStretch(self._layout.rowCount(), 1)

        def _clear(self) -> None:
            """Nettoie la grille."""
            while self._layout.count():
                item = self._layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._cards.clear()

        def _on_card_clicked(self, manga_id: str) -> None:
            """Gère le clic sur une carte.

            Args:
                manga_id: ID du manga.
            """
            self.manga_selected.emit(manga_id)

        def _on_card_double_clicked(self, manga_id: str) -> None:
            """Gère le double-clic sur une carte.

            Args:
                manga_id: ID du manga.
            """
            self.manga_double_clicked.emit(manga_id)

        def _on_card_selected(self, manga_id: str, selected: bool) -> None:
            """Gère la sélection d'une carte.

            Args:
                manga_id: ID du manga.
                selected: True si sélectionné.
            """
            if selected:
                self._selected_ids.add(manga_id)
            else:
                self._selected_ids.discard(manga_id)

            self.mangas_selection_changed.emit(list(self._selected_ids))

        def get_selected_mangas(self) -> list[str]:
            """Récupère les IDs des mangas sélectionnés.

            Returns:
                Liste d'IDs de mangas.
            """
            return list(self._selected_ids)

        def select_all(self) -> None:
            """Sélectionne tous les mangas."""
            for manga_id, card in self._cards.items():
                card.set_selected(True)
                self._selected_ids.add(manga_id)
            self.mangas_selection_changed.emit(list(self._selected_ids))

        def deselect_all(self) -> None:
            """Désélectionne tous les mangas."""
            for card in self._cards.values():
                card.set_selected(False)
            self._selected_ids.clear()
            self.mangas_selection_changed.emit([])

    class SearchFooter(QFrame):
        """Pied de page avec statistiques et actions."""

        # Signaux
        download_requested = pyqtSignal(list)  # list of manga_ids
        select_all_requested = pyqtSignal()
        deselect_all_requested = pyqtSignal()

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise le footer."""
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedHeight(FOOTER_HEIGHT)
            self.setStyleSheet(f"""
                SearchFooter {{
                    background-color: {COLOR_SURFACE};
                    border-top: 2px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(16, 8, 16, 8)
            layout.setSpacing(12)

            # Statistiques
            self._stats_label = QLabel("")
            self._stats_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
            layout.addWidget(self._stats_label)

            layout.addStretch()

            # Bouton Sélectionner tout
            self._btn_select_all = QPushButton(t("search.button.select_all", default="Select All"))
            self._btn_select_all.setStyleSheet(self._button_style())
            self._btn_select_all.clicked.connect(self.select_all_requested.emit)
            layout.addWidget(self._btn_select_all)

            # Bouton Désélectionner tout
            self._btn_deselect_all = QPushButton(t("search.button.deselect_all", default="Deselect All"))
            self._btn_deselect_all.setStyleSheet(self._button_style())
            self._btn_deselect_all.clicked.connect(self.deselect_all_requested.emit)
            layout.addWidget(self._btn_deselect_all)

            # Bouton Télécharger
            self._btn_download = QPushButton(t("search.button.download", default="Download Selected"))
            self._btn_download.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_PRIMARY_BG};
                    color: {COLOR_PRIMARY};
                    border: 2px solid {COLOR_PRIMARY};
                    border-radius: 4px;
                    padding: 6px 16px;
                    font-size: 12px;
                    font-weight: bold;
                    font-family: 'JetBrains Mono', monospace;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_PRIMARY};
                    color: {COLOR_BACKGROUND};
                }}
                QPushButton:disabled {{
                    background-color: {COLOR_SURFACE};
                    color: {COLOR_TEXT_DISABLED};
                    border-color: {COLOR_BORDER_DIM};
                }}
            """)
            self._btn_download.clicked.connect(self._on_download)
            self._btn_download.setEnabled(False)
            layout.addWidget(self._btn_download)

            self._selected_count = 0

        def _button_style(self) -> str:
            """Retourne le style pour les boutons."""
            return f"""
                QPushButton {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {COLOR_TEXT};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: 3px;
                    padding: 6px 12px;
                    font-size: 11px;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_SURFACE_HOVER};
                    border-color: {COLOR_SECONDARY};
                }}
            """

        def _on_download(self) -> None:
            """Gère le clic sur le bouton de téléchargement."""
            # Le signal download_requested sera émis par le parent avec les IDs
            pass

        def update_stats(self, total: int, selected: int, duration_ms: float = 0.0) -> None:
            """Met à jour les statistiques.

            Args:
                total: Nombre total de résultats.
                selected: Nombre de mangas sélectionnés.
                duration_ms: Durée de la recherche en ms.
            """
            self._selected_count = selected

            stats_text = t(
                "search.stats.results",
                default="{total} results",
                total=total,
            )

            if duration_ms > 0:
                stats_text += f" ({duration_ms:.0f}ms)"

            if selected > 0:
                stats_text += f" • {selected} selected"

            self._stats_label.setText(stats_text)
            self._btn_download.setEnabled(selected > 0)

    class SearchView(QWidget):
        """Vue principale de recherche.

        Combine la barre de recherche, les filtres, la grille de résultats,
        et le footer en un seul widget cohérent.

        Signals:
            mangas_selected(list): Émis lorsque des mangas sont sélectionnés pour téléchargement.
            search_completed(SearchResults): Émis lorsque la recherche est terminée.
            search_failed(str): Émis lorsque la recherche échoue.
        """

        # Signaux
        mangas_selected = pyqtSignal(list)  # list of manga_ids
        search_completed = pyqtSignal(object)  # SearchResults
        search_failed = pyqtSignal(str)  # error message

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la vue."""
            super().__init__(parent)
            self._state = SearchViewState()

            # Layout principal
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            # Barre de recherche
            self._search_bar = SearchBar(parent=self)
            self._search_bar.search_requested.connect(self._on_search_requested)
            layout.addWidget(self._search_bar)

            # Barre de filtres
            self._filters_bar = SearchFiltersBar(parent=self)
            self._filters_bar.filters_changed.connect(self._on_filters_changed)
            layout.addWidget(self._filters_bar)

            # Barre de progression
            self._progress = SearchProgress(parent=self)
            layout.addWidget(self._progress)

            # Grille de résultats
            self._results_grid = SearchResultsGrid(parent=self)
            self._results_grid.mangas_selection_changed.connect(self._on_selection_changed)
            layout.addWidget(self._results_grid, stretch=1)

            # Footer
            self._footer = SearchFooter(parent=self)
            self._footer.select_all_requested.connect(self._results_grid.select_all)
            self._footer.deselect_all_requested.connect(self._results_grid.deselect_all)
            self._footer.download_requested.connect(self._on_download_requested)
            layout.addWidget(self._footer)

        def _on_search_requested(self, query: str, site_ids: list[str]) -> None:
            """Gère la demande de recherche.

            Args:
                query: Texte de recherche.
                site_ids: IDs des sites à rechercher.
            """
            filters = self._filters_bar.get_filters()
            search_query = SearchQuery(
                query=query,
                site_ids=site_ids,
                filters=filters,
            )

            # Lancer la recherche asynchrone
            asyncio.create_task(self._perform_search(search_query))

        async def _perform_search(self, query: SearchQuery) -> None:
            """Effectue la recherche.

            Args:
                query: Requête de recherche.
            """
            self._state.state = SearchState.SEARCHING
            self._state.query = query
            self._state.search_started_at = datetime.now(UTC)

            self._search_bar.set_searching(True)
            self._progress.set_progress(0, 100, t("search.progress.starting", default="Starting search..."))

            try:
                from nexusdl.core.registry import get_site_registry

                registry = get_site_registry()

                # Déterminer les sites à rechercher
                if query.site_ids:
                    sites = [registry.get_site(site_id) for site_id in query.site_ids]
                else:
                    sites = registry.list_sites(enabled_only=True, include_adult=False)[:5]

                # Rechercher sur tous les sites en parallèle
                all_results: list[SearchResult] = []
                errors: dict[str, str] = {}
                completed_sites = 0

                async def search_site(site: Any) -> None:
                    nonlocal completed_sites
                    try:
                        parser = await registry.get_parser(site.id)
                        results = await parser.search(query.query)

                        # Ajouter le nom du site aux résultats
                        for result in results:
                            result._site_name = site.name  # type: ignore[attr-defined]

                        all_results.extend(results)
                    except Exception as e:
                        logger.warning("Erreur lors de la recherche sur {}: {}", site.id, e)
                        errors[site.id] = str(e)
                    finally:
                        completed_sites += 1
                        self._progress.set_progress(
                            completed_sites,
                            len(sites),
                            t("search.progress.searching", default="Searching on {site}...", site=site.name),
                        )

                # Exécuter toutes les recherches en parallèle
                tasks = [search_site(site) for site in sites]
                await asyncio.gather(*tasks, return_exceptions=True)

                # Appliquer les filtres
                filtered_results = self._apply_filters(all_results, query.filters)

                # Trier les résultats
                sorted_results = self._sort_results(filtered_results, query.filters.sort_by)

                # Paginer
                paginated_results = sorted_results[:query.page_size]

                # Calculer la durée
                duration_ms = (datetime.now(UTC) - self._state.search_started_at).total_seconds() * 1000

                # Construire l'objet SearchResults
                search_results = SearchResults(
                    query=query.query,
                    results=paginated_results,
                    total=len(sorted_results),
                    page=query.page,
                    page_size=query.page_size,
                    duration_ms=duration_ms,
                    sites_searched=len(sites),
                    errors=errors,
                )

                # Mettre à jour l'état
                self._state.state = SearchState.RESULTS if search_results.has_results else SearchState.NO_RESULTS
                self._state.results = search_results

                # Mettre à jour l'UI
                self._results_grid.set_results(search_results.results)
                self._progress.set_completed(
                    t("search.progress.completed", default="Search completed in {duration}ms", duration=int(duration_ms))
                )
                self._footer.update_stats(
                    total=search_results.total,
                    selected=0,
                    duration_ms=duration_ms,
                )

                # Émettre le signal
                self.search_completed.emit(search_results)

                # Émettre un événement
                try:
                    event_bus = get_event_bus()
                    await event_bus.emit(
                        EventType.CUSTOM,
                        payload={
                            "type": "search.completed",
                            "query": query.query,
                            "results_count": len(search_results.results),
                            "duration_ms": duration_ms,
                        },
                        source="interfaces.gui.search",
                    )
                except Exception as e:
                    logger.debug("Impossible d'émettre l'événement: {}", e)

                logger.info(
                    "Recherche terminée: {} résultats en {:.1f}ms",
                    len(search_results.results),
                    duration_ms,
                )

            except Exception as e:
                logger.error("Erreur lors de la recherche: {}", e)
                self._state.state = SearchState.ERROR
                self._state.error_message = str(e)

                self._progress.hide_progress()
                self.search_failed.emit(str(e))

                QMessageBox.critical(
                    self,
                    t("search.error.title", default="Search Error"),
                    str(e),
                )

            finally:
                self._search_bar.set_searching(False)

        def _apply_filters(self, results: list[SearchResult], filters: SearchFilters) -> list[SearchResult]:
            """Applique les filtres aux résultats.

            Args:
                results: Résultats à filtrer.
                filters: Filtres à appliquer.

            Returns:
                Résultats filtrés.
            """
            filtered = results

            # Filtre par langue
            if filters.language:
                filtered = [
                    r for r in filtered
                    if r.manga.language.value == filters.language
                ]

            # Filtre par statut
            if filters.status:
                filtered = [
                    r for r in filtered
                    if r.manga.status.value == filters.status
                ]

            # Filtre par contenu adulte
            if not filters.include_adult:
                filtered = [
                    r for r in filtered
                    if not r.manga.is_adult
                ]

            return filtered

        def _sort_results(self, results: list[SearchResult], sort_by: SearchSortBy) -> list[SearchResult]:
            """Trie les résultats.

            Args:
                results: Résultats à trier.
                sort_by: Critère de tri.

            Returns:
                Résultats triés.
            """
            if sort_by == SearchSortBy.RELEVANCE:
                return sorted(results, key=lambda r: r.score, reverse=True)
            elif sort_by == SearchSortBy.TITLE:
                return sorted(results, key=lambda r: r.manga.title.lower())
            elif sort_by == SearchSortBy.DATE:
                return sorted(
                    results,
                    key=lambda r: r.manga.year or 0,
                    reverse=True,
                )
            elif sort_by == SearchSortBy.STATUS:
                return sorted(results, key=lambda r: r.manga.status.value)

            return results

        def _on_filters_changed(self, filters: SearchFilters) -> None:
            """Gère le changement de filtres.

            Args:
                filters: Nouveaux filtres.
            """
            # Relancer la recherche avec les nouveaux filtres
            if self._state.query:
                self._state.query.filters = filters
                asyncio.create_task(self._perform_search(self._state.query))

        def _on_selection_changed(self, manga_ids: list[str]) -> None:
            """Gère le changement de sélection.

            Args:
                manga_ids: IDs des mangas sélectionnés.
            """
            self._state.selected_ids = set(manga_ids)
            self._footer.update_stats(
                total=len(self._state.results.results) if self._state.results else 0,
                selected=len(manga_ids),
                duration_ms=self._state.results.duration_ms if self._state.results else 0.0,
            )

        def _on_download_requested(self) -> None:
            """Gère la demande de téléchargement."""
            selected = self._results_grid.get_selected_mangas()
            if selected:
                self.mangas_selected.emit(selected)


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
    "SEARCH_BAR_HEIGHT",
    "FILTERS_HEIGHT",
    "PROGRESS_HEIGHT",
    "FOOTER_HEIGHT",
    "CARD_WIDTH",
    "CARD_HEIGHT",
    # Exceptions
    "SearchViewError",
    "SearchExecutionError",
    # Enums
    "SearchSortBy",
    "SearchState",
    # Modèles
    "SearchFilters",
    "SearchQuery",
    "SearchResults",
    "SearchViewState",
    # Widgets
    "SearchView" if PYQT6_AVAILABLE else None,
    "SearchBar" if PYQT6_AVAILABLE else None,
    "SearchFiltersBar" if PYQT6_AVAILABLE else None,
    "SearchProgress" if PYQT6_AVAILABLE else None,
    "SearchResultsGrid" if PYQT6_AVAILABLE else None,
    "SearchFooter" if PYQT6_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
