"""Écran de recherche de l'interface CLI NexusDL.

Ce module fournit un écran complet de recherche de mangas/webtoons/comics
via une interface TUI (Terminal User Interface) basée sur Textual. Il permet
à l'utilisateur de rechercher sur un ou plusieurs sites, filtrer et trier
les résultats, et sélectionner des mangas pour téléchargement.

**Fonctionnalités** :
    - Recherche multi-sites (sélection du site ou tous les sites)
    - Recherche asynchrone avec indicateur de progression
    - Affichage des résultats avec métadonnées riches (cover, titre, auteur, etc.)
    - Filtrage par langue, statut, type de contenu
    - Tri par pertinence, date, titre
    - Sélection multiple pour téléchargement
    - Pagination ou scroll infini
    - Navigation clavier complète (Tab, Enter, Escape, flèches)
    - Messages de confirmation
    - Gestion des erreurs
    - Traductions i18n

**Architecture** :
    SearchScreen (Screen Textual)
        ├── SearchBar (barre de recherche)
        │   ├── Input (champ de recherche)
        │   └── Select (sélection du site)
        ├── SearchFilters (filtres)
        │   ├── Select (langue)
        │   ├── Select (statut)
        │   └── Select (tri)
        ├── SearchResults (liste de résultats)
        │   └── SearchResultItem (item individuel)
        └── SearchFooter (boutons d'action)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.cli.screens.search import SearchScreen
    >>>
    >>> # Dans l'application principale
    >>> app.push_screen(SearchScreen())
    >>>
    >>> # L'utilisateur peut :
    >>> # 1. Saisir une requête dans la barre de recherche
    >>> # 2. Sélectionner un site (ou "Tous les sites")
    >>> # 3. Appliquer des filtres (langue, statut)
    >>> # 4. Naviguer dans les résultats avec les flèches
    >>> # 5. Sélectionner des mangas avec Espace
    >>> # 6. Télécharger avec Ctrl+D
    >>> # 7. Annuler avec Escape

Intégration :
    - core/registry/site_registry.py : accès aux sites supportés
    - core/parsers/* : exécution des recherches
    - core/events.py : émission d'événements
    - core/i18n.py : traductions
    - core/logger.py : logs des actions
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from enum import Enum
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
    )
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False

from nexusdl.core.constants import APP_NAME
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t
from nexusdl.core.models.manga import Language, Manga, MangaStatus, SearchResult
from nexusdl.core.registry import get_site_registry


# ============================================================================
# EXCEPTIONS
# ============================================================================


class SearchScreenError(NexusDLError):
    """Exception de base pour les erreurs de l'écran de recherche."""


class SearchExecutionError(SearchScreenError):
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


class NoResultsError(SearchScreenError):
    """Exception levée lorsqu'aucun résultat n'est trouvé."""

    def __init__(self, query: str) -> None:
        super().__init__(f"Aucun résultat pour: {query!r}")
        self.query = query


# ============================================================================
# ENUMS
# ============================================================================


class SearchSortBy(str, Enum):
    """Critère de tri des résultats.

    Attributes:
        RELEVANCE: Tri par pertinence (défaut).
        TITLE: Tri par titre alphabétique.
        DATE: Tri par date de publication.
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
# MODÈLES PYDANTIC
# ============================================================================


class SearchFilters(BaseModel):
    """Filtres de recherche.

    Attributes:
        language: Langue des résultats (None = toutes).
        status: Statut des mangas (None = tous).
        content_rating: Classification d'âge (None = toutes).
        sort_by: Critère de tri.
        include_adult: Inclure le contenu adulte.
    """

    language: str | None = Field(default=None, description="Langue (ISO 639-1).")
    status: str | None = Field(default=None, description="Statut.")
    content_rating: str | None = Field(default=None, description="Classification.")
    sort_by: SearchSortBy = Field(default=SearchSortBy.RELEVANCE, description="Tri.")
    include_adult: bool = Field(default=False, description="Inclure contenu adulte.")

    model_config = ConfigDict(extra="forbid")


class SearchQuery(BaseModel):
    """Requête de recherche.

    Attributes:
        query: Texte de recherche.
        site_id: ID du site (None = tous les sites).
        filters: Filtres appliqués.
        page: Numéro de page (pour pagination).
        page_size: Nombre de résultats par page.
    """

    query: str = Field(..., min_length=1, max_length=200, description="Texte de recherche.")
    site_id: str | None = Field(default=None, description="ID du site.")
    filters: SearchFilters = Field(default_factory=SearchFilters, description="Filtres.")
    page: int = Field(default=1, ge=1, description="Numéro de page.")
    page_size: int = Field(default=20, ge=1, le=100, description="Résultats par page.")

    model_config = ConfigDict(extra="forbid")


class SearchResults(BaseModel):
    """Résultats de recherche.

    Attributes:
        query: Requête originale.
        results: Liste des résultats.
        total: Nombre total de résultats.
        page: Page actuelle.
        page_size: Taille de page.
        has_next: Indique s'il y a une page suivante.
        has_previous: Indique s'il y a une page précédente.
        duration_ms: Durée de la recherche en millisecondes.
        sites_searched: Nombre de sites recherchés.
    """

    query: str = Field(..., description="Requête originale.")
    results: list[SearchResult] = Field(default_factory=list, description="Résultats.")
    total: int = Field(default=0, ge=0, description="Nombre total.")
    page: int = Field(default=1, ge=1, description="Page actuelle.")
    page_size: int = Field(default=20, ge=1, description="Taille de page.")
    has_next: bool = Field(default=False, description="Page suivante.")
    has_previous: bool = Field(default=False, description="Page précédente.")
    duration_ms: float = Field(default=0.0, ge=0.0, description="Durée en ms.")
    sites_searched: int = Field(default=0, ge=0, description="Sites recherchés.")

    model_config = ConfigDict(extra="forbid")

    @property
    def total_pages(self) -> int:
        """Nombre total de pages."""
        if self.page_size == 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size


class SearchScreenState(BaseModel):
    """État de l'écran de recherche.

    Attributes:
        state: État actuel.
        query: Requête actuelle.
        results: Résultats actuels.
        selected_indices: Indices des résultats sélectionnés.
        error_message: Message d'erreur (si state=ERROR).
        search_started_at: Timestamp de début de recherche.
    """

    state: SearchState = Field(default=SearchState.IDLE, description="État.")
    query: SearchQuery | None = Field(default=None, description="Requête.")
    results: SearchResults | None = Field(default=None, description="Résultats.")
    selected_indices: set[int] = Field(default_factory=set, description="Sélections.")
    error_message: str | None = Field(default=None, description="Erreur.")
    search_started_at: datetime | None = Field(default=None, description="Début.")

    model_config = ConfigDict(extra="forbid")

    @property
    def selected_count(self) -> int:
        """Nombre de résultats sélectionnés."""
        return len(self.selected_indices)

    @property
    def is_searching(self) -> bool:
        """Indique si une recherche est en cours."""
        return self.state == SearchState.SEARCHING


# ============================================================================
# WIDGETS CUSTOM — Composants de recherche
# ============================================================================


if TEXTUAL_AVAILABLE:

    class SearchBar(Widget):
        """Barre de recherche avec champ de texte et sélection de site."""

        DEFAULT_CSS = """
        SearchBar {
            layout: horizontal;
            height: 3;
            padding: 0 1;
        }
        SearchBar > Input {
            width: 1fr;
        }
        SearchBar > Select {
            width: 30;
        }
        SearchBar > Button {
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
            """Initialise la barre de recherche."""
            super().__init__(name=name, id=id, classes=classes)
            self._sites: list[tuple[str, str]] = []

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Input(
                placeholder=t("search.input.placeholder", default="Search for manga, webtoon, comics..."),
                id="search-input",
            )
            yield Select(
                self._sites,
                id="site-select",
                value="all",
                allow_blank=False,
            )
            yield Button(
                t("search.button.search", default="Search"),
                id="search-button",
                variant="primary",
            )

        def on_mount(self) -> None:
            """Appelé lors du montage."""
            self._load_sites()

        def _load_sites(self) -> None:
            """Charge la liste des sites depuis le registre."""
            try:
                registry = get_site_registry()
                sites = registry.list_sites(enabled_only=True, include_adult=False)

                # Construire les options
                self._sites = [("all", t("search.site.all", default="All Sites"))]
                for site in sites:
                    self._sites.append((site.id, site.name))

                # Mettre à jour le Select
                select = self.query_one("#site-select", Select)
                select.set_options(self._sites)

            except Exception as e:
                logger.error("Impossible de charger les sites: {}", e)
                self._sites = [("all", "All Sites")]

        def get_query(self) -> SearchQuery:
            """Récupère la requête actuelle.

            Returns:
                Instance de SearchQuery.
            """
            input_widget = self.query_one("#search-input", Input)
            select_widget = self.query_one("#site-select", Select)

            query_text = input_widget.value.strip()
            site_id = select_widget.value if select_widget.value != "all" else None

            return SearchQuery(
                query=query_text,
                site_id=site_id,
            )

    class SearchFiltersWidget(Widget):
        """Widget de filtres de recherche."""

        DEFAULT_CSS = """
        SearchFiltersWidget {
            layout: horizontal;
            height: 3;
            padding: 0 1;
        }
        SearchFiltersWidget > Label {
            width: auto;
            content-align: left middle;
            padding: 0 1;
        }
        SearchFiltersWidget > Select {
            width: 20;
        }
        """

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Label(t("search.filter.language", default="Language:"))
            yield Select(
                [
                    ("all", t("search.filter.all", default="All")),
                    ("fr", "Français"),
                    ("en", "English"),
                    ("ja", "日本語"),
                    ("ko", "한국어"),
                    ("zh", "中文"),
                ],
                id="language-filter",
                value="all",
                allow_blank=False,
            )

            yield Label(t("search.filter.status", default="Status:"))
            yield Select(
                [
                    ("all", t("search.filter.all", default="All")),
                    ("ongoing", t("search.filter.ongoing", default="Ongoing")),
                    ("completed", t("search.filter.completed", default="Completed")),
                    ("hiatus", t("search.filter.hiatus", default="Hiatus")),
                ],
                id="status-filter",
                value="all",
                allow_blank=False,
            )

            yield Label(t("search.filter.sort", default="Sort:"))
            yield Select(
                [(sort.value, sort.label) for sort in SearchSortBy],
                id="sort-filter",
                value=SearchSortBy.RELEVANCE.value,
                allow_blank=False,
            )

        def get_filters(self) -> SearchFilters:
            """Récupère les filtres actuels.

            Returns:
                Instance de SearchFilters.
            """
            lang_select = self.query_one("#language-filter", Select)
            status_select = self.query_one("#status-filter", Select)
            sort_select = self.query_one("#sort-filter", Select)

            language = lang_select.value if lang_select.value != "all" else None
            status = status_select.value if status_select.value != "all" else None
            sort_by = SearchSortBy(sort_select.value)

            return SearchFilters(
                language=language,
                status=status,
                sort_by=sort_by,
            )

    class SearchResultItem(ListItem):
        """Item individuel dans la liste de résultats."""

        DEFAULT_CSS = """
        SearchResultItem {
            padding: 1;
            height: auto;
        }
        SearchResultItem > .result-title {
            text-style: bold;
            color: $text;
        }
        SearchResultItem > .result-meta {
            color: $text-muted;
        }
        SearchResultItem > .result-site {
            color: $accent;
        }
        SearchResultItem.selected {
            background: $primary-background;
        }
        """

        def __init__(
            self,
            result: SearchResult,
            index: int,
            *,
            selected: bool = False,
        ) -> None:
            """Initialise l'item.

            Args:
                result: Résultat de recherche.
                index: Index dans la liste.
                selected: Indique si l'item est sélectionné.
            """
            super().__init__()
            self.result = result
            self.index = index
            self.selected = selected

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            manga = self.result.manga

            # Titre
            title_text = f"{'✓ ' if self.selected else ''}{manga.title}"
            yield Static(title_text, classes="result-title")

            # Métadonnées
            meta_parts = []
            if manga.author:
                meta_parts.append(f"by {manga.author}")
            if manga.year:
                meta_parts.append(str(manga.year))
            if manga.status != MangaStatus.UNKNOWN:
                meta_parts.append(manga.status.label)
            if manga.language != Language.EN:
                meta_parts.append(manga.language.flag)

            if meta_parts:
                yield Static(" • ".join(meta_parts), classes="result-meta")

            # Site
            yield Static(f"on {manga.site}", classes="result-site")

            # Description (tronquée)
            if manga.description:
                desc = manga.description[:150]
                if len(manga.description) > 150:
                    desc += "..."
                yield Static(desc, classes="result-description")

        def toggle_selection(self) -> None:
            """Bascule la sélection."""
            self.selected = not self.selected
            self.refresh()

    class SearchResultsWidget(Widget):
        """Widget d'affichage des résultats de recherche."""

        DEFAULT_CSS = """
        SearchResultsWidget {
            height: 1fr;
            padding: 1;
        }
        SearchResultsWidget > ListView {
            height: 1fr;
        }
        SearchResultsWidget > .no-results {
            text-align: center;
            padding: 2;
            color: $text-muted;
        }
        SearchResultsWidget > .searching {
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
            """Initialise le widget."""
            super().__init__(name=name, id=id, classes=classes)
            self._results: SearchResults | None = None
            self._selected_indices: set[int] = set()

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(
                t("search.results.empty", default="Enter a search query to begin"),
                id="results-placeholder",
                classes="no-results",
            )
            yield ListView(id="results-list")

        def update_results(self, results: SearchResults | None) -> None:
            """Met à jour les résultats affichés.

            Args:
                results: Nouveaux résultats ou None pour effacer.
            """
            self._results = results
            list_view = self.query_one("#results-list", ListView)
            placeholder = self.query_one("#results-placeholder", Static)

            if results is None or not results.results:
                list_view.clear()
                placeholder.update(
                    t("search.results.no_results", default="No results found")
                )
                placeholder.display = True
                list_view.display = False
                return

            placeholder.display = False
            list_view.display = True
            list_view.clear()

            for i, result in enumerate(results.results):
                item = SearchResultItem(
                    result,
                    i,
                    selected=i in self._selected_indices,
                )
                list_view.append(item)

        def toggle_selection(self, index: int) -> None:
            """Bascule la sélection d'un résultat.

            Args:
                index: Index du résultat.
            """
            if index in self._selected_indices:
                self._selected_indices.remove(index)
            else:
                self._selected_indices.add(index)

            # Rafraîchir l'item
            list_view = self.query_one("#results-list", ListView)
            if 0 <= index < len(list_view.children):
                item = list_view.children[index]
                if isinstance(item, SearchResultItem):
                    item.toggle_selection()

        def select_all(self) -> None:
            """Sélectionne tous les résultats."""
            if self._results is None:
                return

            self._selected_indices = set(range(len(self._results.results)))
            self.update_results(self._results)

        def deselect_all(self) -> None:
            """Désélectionne tous les résultats."""
            self._selected_indices.clear()
            if self._results:
                self.update_results(self._results)

        def get_selected_results(self) -> list[SearchResult]:
            """Récupère les résultats sélectionnés.

            Returns:
                Liste de SearchResult sélectionnés.
            """
            if self._results is None:
                return []

            return [
                self._results.results[i]
                for i in sorted(self._selected_indices)
                if i < len(self._results.results)
            ]

        @property
        def selected_count(self) -> int:
            """Nombre de résultats sélectionnés."""
            return len(self._selected_indices)


# ============================================================================
# MESSAGES — Événements Textual
# ============================================================================


if TEXTUAL_AVAILABLE:

    class SearchStarted(Message):
        """Message émis lorsqu'une recherche démarre."""

        def __init__(self, query: SearchQuery) -> None:
            """Initialise le message.

            Args:
                query: Requête de recherche.
            """
            super().__init__()
            self.query = query

    class SearchCompleted(Message):
        """Message émis lorsqu'une recherche se termine."""

        def __init__(self, results: SearchResults) -> None:
            """Initialise le message.

            Args:
                results: Résultats de la recherche.
            """
            super().__init__()
            self.results = results

    class SearchFailed(Message):
        """Message émis lorsqu'une recherche échoue."""

        def __init__(self, error: str) -> None:
            """Initialise le message.

            Args:
                error: Message d'erreur.
            """
            super().__init__()
            self.error = error

    class ResultsSelected(Message):
        """Message émis lorsque des résultats sont sélectionnés pour téléchargement."""

        def __init__(self, results: list[SearchResult]) -> None:
            """Initialise le message.

            Args:
                results: Résultats sélectionnés.
            """
            super().__init__()
            self.results = results


# ============================================================================
# CLASSE PRINCIPALE — SearchScreen
# ============================================================================


if TEXTUAL_AVAILABLE:

    class SearchScreen(Screen):
        """Écran de recherche de mangas.

        Permet à l'utilisateur de rechercher des mangas sur les sites supportés,
        filtrer et trier les résultats, et sélectionner des mangas pour téléchargement.
        """

        # Bindings clavier
        BINDINGS = [
            Binding("ctrl+d", "download_selected", "Download Selected"),
            Binding("ctrl+a", "select_all", "Select All"),
            Binding("ctrl+shift+a", "deselect_all", "Deselect All"),
            Binding("space", "toggle_selection", "Toggle Selection"),
            Binding("escape", "cancel", "Cancel"),
            Binding("ctrl+right", "next_page", "Next Page"),
            Binding("ctrl+left", "prev_page", "Previous Page"),
        ]

        # CSS de l'écran
        DEFAULT_CSS = """
        SearchScreen {
            layout: vertical;
        }

        #search-container {
            height: auto;
            padding: 1;
        }

        #filters-container {
            height: auto;
            padding: 1;
            border-top: solid $primary;
        }

        #results-container {
            height: 1fr;
            padding: 1;
        }

        #footer-container {
            height: 3;
            layout: horizontal;
            padding: 0 2;
            align: right middle;
        }

        .status-bar {
            height: 1;
            padding: 0 1;
            background: $primary-background;
        }
        """

        # État réactif
        search_state: reactive[SearchState] = reactive(SearchState.IDLE)

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise l'écran de recherche.

            Args:
                name: Nom de l'écran.
                id: ID de l'écran.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self._state = SearchScreenState()

        def compose(self) -> ComposeResult:
            """Compose l'écran."""
            yield Header()

            # Barre de recherche
            with Vertical(id="search-container"):
                yield SearchBar(id="search-bar")

            # Filtres
            with Vertical(id="filters-container"):
                yield SearchFiltersWidget(id="search-filters")

            # Résultats
            with Vertical(id="results-container"):
                yield Static(id="status-bar", classes="status-bar")
                yield SearchResultsWidget(id="search-results")

            # Footer avec boutons
            with Horizontal(id="footer-container"):
                yield Button(
                    t("search.button.deselect_all", default="Deselect All"),
                    id="btn-deselect-all",
                    variant="default",
                )
                yield Button(
                    t("search.button.select_all", default="Select All"),
                    id="btn-select-all",
                    variant="default",
                )
                yield Button(
                    t("search.button.download", default="Download Selected"),
                    id="btn-download",
                    variant="success",
                )

            yield Footer()

        def on_mount(self) -> None:
            """Appelé lors du montage."""
            self._update_status_bar()

        def _update_status_bar(self) -> None:
            """Met à jour la barre de statut."""
            status_bar = self.query_one("#status-bar", Static)

            if self._state.is_searching:
                status_bar.update(
                    f"{SearchState.SEARCHING.icon} "
                    + t("search.status.searching", default="Searching...")
                )
            elif self._state.state == SearchState.RESULTS and self._state.results:
                results = self._state.results
                selected = self._state.selected_count
                status_bar.update(
                    f"{SearchState.RESULTS.icon} "
                    + t(
                        "search.status.results",
                        default="{total} results (page {page}/{pages}) - {selected} selected",
                        total=results.total,
                        page=results.page,
                        pages=results.total_pages,
                        selected=selected,
                    )
                )
            elif self._state.state == SearchState.NO_RESULTS:
                status_bar.update(
                    f"{SearchState.NO_RESULTS.icon} "
                    + t("search.status.no_results", default="No results found")
                )
            elif self._state.state == SearchState.ERROR:
                status_bar.update(
                    f"{SearchState.ERROR.icon} "
                    + t("search.status.error", default="Error: {error}", error=self._state.error_message or "Unknown")
                )
            else:
                status_bar.update(
                    f"{SearchState.IDLE.icon} "
                    + t("search.status.idle", default="Ready to search")
                )

        # =====================================================================
        # GESTION DES ÉVÉNEMENTS
        # =====================================================================

        def on_button_pressed(self, event: Button.Pressed) -> None:
            """Gère les clics sur les boutons."""
            if event.button.id == "search-button":
                self._execute_search()
            elif event.button.id == "btn-select-all":
                self.action_select_all()
            elif event.button.id == "btn-deselect-all":
                self.action_deselect_all()
            elif event.button.id == "btn-download":
                self.action_download_selected()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            """Gère la soumission du champ de recherche."""
            if event.input.id == "search-input":
                self._execute_search()

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            """Gère la sélection d'un item dans la liste."""
            if isinstance(event.item, SearchResultItem):
                self.action_toggle_selection()

        # =====================================================================
        # ACTIONS — Bindings clavier
        # =====================================================================

        def action_toggle_selection(self) -> None:
            """Action : basculer la sélection de l'item courant."""
            results_widget = self.query_one("#search-results", SearchResultsWidget)
            list_view = results_widget.query_one("#results-list", ListView)

            if list_view.index is not None:
                results_widget.toggle_selection(list_view.index)
                self._state.selected_indices = results_widget._selected_indices
                self._update_status_bar()

        def action_select_all(self) -> None:
            """Action : sélectionner tous les résultats."""
            results_widget = self.query_one("#search-results", SearchResultsWidget)
            results_widget.select_all()
            self._state.selected_indices = results_widget._selected_indices
            self._update_status_bar()

        def action_deselect_all(self) -> None:
            """Action : désélectionner tous les résultats."""
            results_widget = self.query_one("#search-results", SearchResultsWidget)
            results_widget.deselect_all()
            self._state.selected_indices = results_widget._selected_indices
            self._update_status_bar()

        def action_download_selected(self) -> None:
            """Action : télécharger les résultats sélectionnés."""
            results_widget = self.query_one("#search-results", SearchResultsWidget)
            selected = results_widget.get_selected_results()

            if not selected:
                self.notify(
                    t("search.notify.no_selection", default="No results selected"),
                    severity="warning",
                )
                return

            # Émettre un message
            self.post_message(ResultsSelected(selected))

            self.notify(
                t(
                    "search.notify.download_started",
                    default="Downloading {count} manga(s)",
                    count=len(selected),
                ),
                severity="information",
            )

            # Fermer l'écran
            self.app.pop_screen()

        def action_cancel(self) -> None:
            """Action : annuler et fermer l'écran."""
            self.app.pop_screen()

        def action_next_page(self) -> None:
            """Action : page suivante."""
            if self._state.results and self._state.results.has_next:
                # TODO: Implémenter la pagination
                self.notify(
                    t("search.notify.pagination_not_implemented", default="Pagination not yet implemented"),
                    severity="warning",
                )

        def action_prev_page(self) -> None:
            """Action : page précédente."""
            if self._state.results and self._state.results.has_previous:
                # TODO: Implémenter la pagination
                self.notify(
                    t("search.notify.pagination_not_implemented", default="Pagination not yet implemented"),
                    severity="warning",
                )

        # =====================================================================
        # MÉTHODES INTERNES — Recherche
        # =====================================================================

        def _execute_search(self) -> None:
            """Exécute une recherche."""
            # Récupérer la requête et les filtres
            search_bar = self.query_one("#search-bar", SearchBar)
            filters_widget = self.query_one("#search-filters", SearchFiltersWidget)

            query = search_bar.get_query()
            filters = filters_widget.get_filters()

            # Appliquer les filtres à la requête
            query.filters = filters

            # Valider la requête
            if not query.query:
                self.notify(
                    t("search.notify.empty_query", default="Please enter a search query"),
                    severity="warning",
                )
                return

            # Lancer la recherche asynchrone
            self._state.state = SearchState.SEARCHING
            self._state.query = query
            self._state.search_started_at = datetime.now(UTC)
            self._update_status_bar()

            # Émettre un événement
            self.post_message(SearchStarted(query))

            # Exécuter la recherche en arrière-plan
            asyncio.create_task(self._perform_search(query))

        async def _perform_search(self, query: SearchQuery) -> None:
            """Effectue la recherche asynchrone.

            Args:
                query: Requête de recherche.
            """
            start_time = datetime.now(UTC)

            try:
                # Obtenir le registre de sites
                registry = get_site_registry()

                # Déterminer les sites à rechercher
                if query.site_id:
                    sites = [registry.get_site(query.site_id)]
                else:
                    sites = registry.list_sites(enabled_only=True, include_adult=False)

                # Rechercher sur tous les sites
                all_results: list[SearchResult] = []
                tasks = []

                for site in sites:
                    task = self._search_on_site(site.id, query)
                    tasks.append(task)

                # Exécuter toutes les recherches en parallèle
                results_list = await asyncio.gather(*tasks, return_exceptions=True)

                # Collecter les résultats
                for result in results_list:
                    if isinstance(result, Exception):
                        logger.warning("Erreur lors de la recherche: {}", result)
                    elif isinstance(result, list):
                        all_results.extend(result)

                # Appliquer les filtres
                filtered_results = self._apply_filters(all_results, query.filters)

                # Trier les résultats
                sorted_results = self._sort_results(filtered_results, query.filters.sort_by)

                # Paginer
                paginated_results = self._paginate_results(
                    sorted_results,
                    query.page,
                    query.page_size,
                )

                # Calculer la durée
                duration_ms = (datetime.now(UTC) - start_time).total_seconds() * 1000

                # Construire l'objet SearchResults
                search_results = SearchResults(
                    query=query.query,
                    results=paginated_results,
                    total=len(sorted_results),
                    page=query.page,
                    page_size=query.page_size,
                    has_next=query.page * query.page_size < len(sorted_results),
                    has_previous=query.page > 1,
                    duration_ms=duration_ms,
                    sites_searched=len(sites),
                )

                # Mettre à jour l'état
                self._state.state = SearchState.RESULTS if search_results.results else SearchState.NO_RESULTS
                self._state.results = search_results
                self._state.selected_indices.clear()

                # Mettre à jour l'UI
                results_widget = self.query_one("#search-results", SearchResultsWidget)
                results_widget.update_results(search_results)
                self._update_status_bar()

                # Émettre un événement
                self.post_message(SearchCompleted(search_results))

                # Émettre un événement global
                event_bus = get_event_bus()
                await event_bus.emit(
                    EventType.CUSTOM,
                    payload={
                        "type": "search.completed",
                        "query": query.query,
                        "results_count": len(search_results.results),
                        "duration_ms": duration_ms,
                    },
                    source="interfaces.cli.search",
                )

                logger.info(
                    "Recherche terminée: {} résultats en {:.1f}ms",
                    len(search_results.results),
                    duration_ms,
                )

            except Exception as e:
                logger.error("Erreur lors de la recherche: {}", e)
                self._state.state = SearchState.ERROR
                self._state.error_message = str(e)
                self._update_status_bar()
                self.post_message(SearchFailed(str(e)))

        async def _search_on_site(
            self,
            site_id: str,
            query: SearchQuery,
        ) -> list[SearchResult]:
            """Recherche sur un site spécifique.

            Args:
                site_id: ID du site.
                query: Requête de recherche.

            Returns:
                Liste de SearchResult.
            """
            try:
                registry = get_site_registry()
                parser = await registry.get_parser(site_id)

                # Exécuter la recherche
                results = await parser.search(query.query)

                # Ajouter le score de pertinence (simplifié)
                for i, result in enumerate(results):
                    result.score = 1.0 - (i / len(results)) if results else 0.0

                return results

            except Exception as e:
                logger.warning("Erreur lors de la recherche sur {}: {}", site_id, e)
                return []

        def _apply_filters(
            self,
            results: list[SearchResult],
            filters: SearchFilters,
        ) -> list[SearchResult]:
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

        def _sort_results(
            self,
            results: list[SearchResult],
            sort_by: SearchSortBy,
        ) -> list[SearchResult]:
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

        def _paginate_results(
            self,
            results: list[SearchResult],
            page: int,
            page_size: int,
        ) -> list[SearchResult]:
            """Paginer les résultats.

            Args:
                results: Résultats à paginer.
                page: Numéro de page.
                page_size: Taille de page.

            Returns:
                Résultats de la page demandée.
            """
            start = (page - 1) * page_size
            end = start + page_size
            return results[start:end]


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "SearchScreenError",
    "SearchExecutionError",
    "NoResultsError",
    # Enums
    "SearchSortBy",
    "SearchState",
    # Modèles
    "SearchFilters",
    "SearchQuery",
    "SearchResults",
    "SearchScreenState",
    # Écran principal
    "SearchScreen" if TEXTUAL_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
