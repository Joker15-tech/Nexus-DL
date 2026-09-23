"""Écran de bibliothèque de l'interface CLI NexusDL.

Ce module fournit un écran complet de gestion de la bibliothèque locale
via une interface TUI (Terminal User Interface) basée sur Textual. Il permet
à l'utilisateur de visualiser sa collection de mangas, filtrer et trier,
rechercher, gérer les listes de lecture, et accéder aux détails des mangas.

**Fonctionnalités** :
    - Affichage de la bibliothèque en grille (cards) ou liste
    - Recherche plein texte (titre, auteur, tags)
    - Filtrage par statut de lecture, langue, statut de publication
    - Tri par titre, date d'ajout, dernière lecture, progression
    - Gestion des listes de lecture (reading lists)
    - Vue détaillée d'un manga (métadonnées, chapitres, progression)
    - Actions rapides (lire, télécharger, supprimer, marquer lu/non-lu)
    - Statistiques de bibliothèque (total, taille, temps de lecture)
    - Navigation clavier complète (flèches, Enter, Escape, /, f, etc.)
    - Traductions i18n
    - Gestion des erreurs

**Architecture** :
    LibraryScreen (Screen Textual)
        ├── LibraryFilterBar (barre de recherche/filtres)
        │   ├── Input (recherche)
        │   ├── Select (filtre statut lecture)
        │   ├── Select (filtre langue)
        │   ├── Select (tri)
        │   └── Select (vue: grille/liste)
        ├── ReadingListsSidebar (sidebar avec listes)
        │   └── ReadingListItem (liste individuelle)
        ├── MangaGrid (grille de mangas)
        │   └── MangaCard (carte individuelle)
        ├── MangaDetailsPanel (panneau de détails, optionnel)
        │   ├── MangaInfo (métadonnées)
        │   ├── ChaptersList (liste des chapitres)
        │   └── ReadingProgress (progression)
        └── LibraryStatusBar (barre de statut)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.cli.screens.library import LibraryScreen
    >>>
    >>> # Dans l'application principale
    >>> app.push_screen(LibraryScreen())
    >>>
    >>> # L'utilisateur peut :
    >>> # 1. Rechercher un manga avec '/'
    >>> # 2. Filtrer par statut de lecture avec 'f'
    >>> # 3. Naviguer avec les flèches
    >>> # 4. Ouvrir les détails avec Enter
    >>> # 5. Télécharger avec 'd'
    >>> # 6. Supprimer avec 'x'
    >>> # 7. Retour avec Escape

Intégration :
    - core/library/database.py : accès aux mangas et chapitres
    - core/library/search.py : recherche plein texte
    - core/library/scanner.py : scan de la bibliothèque
    - core/models/library.py : modèles de données
    - core/events.py : mises à jour temps réel
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
from nexusdl.core.models.library import (
    LibraryStats,
    Manga,
    ReadingList,
    ReadingProgress,
    ReadingStatus,
)
from nexusdl.core.models.manga import Language, MangaStatus
from nexusdl.core.utils.text import format_size


# ============================================================================
# EXCEPTIONS
# ============================================================================


class LibraryScreenError(NexusDLError):
    """Exception de base pour les erreurs de l'écran de bibliothèque."""


class LibraryLoadError(LibraryScreenError):
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


class MangaNotFoundError(LibraryScreenError):
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
        COMPACT: Affichage compact (liste dense).
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
        TITLE: Tri par titre alphabétique.
        DATE_ADDED: Tri par date d'ajout.
        LAST_READ: Tri par dernière lecture.
        PROGRESS: Tri par progression.
        AUTHOR: Tri par auteur.
        STATUS: Tri par statut de publication.
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
# MODÈLES PYDANTIC
# ============================================================================


class LibraryFilters(BaseModel):
    """Filtres appliqués à la bibliothèque.

    Attributes:
        search_query: Texte de recherche.
        reading_status: Filtre par statut de lecture.
        language: Filtre par langue.
        manga_status: Filtre par statut de publication.
        sort_by: Critère de tri.
        view_mode: Mode d'affichage.
        reading_list_id: ID de la liste de lecture sélectionnée (None = toutes).
    """

    search_query: str = Field(default="", description="Recherche.")
    reading_status: ReadingStatusFilter = Field(
        default=ReadingStatusFilter.ALL,
        description="Statut de lecture.",
    )
    language: str | None = Field(default=None, description="Langue.")
    manga_status: str | None = Field(default=None, description="Statut publication.")
    sort_by: LibrarySortBy = Field(default=LibrarySortBy.TITLE, description="Tri.")
    view_mode: LibraryViewMode = Field(default=LibraryViewMode.GRID, description="Vue.")
    reading_list_id: str | None = Field(default=None, description="Liste sélectionnée.")

    model_config = ConfigDict(extra="forbid")


class LibraryState(BaseModel):
    """État de l'écran de bibliothèque.

    Attributes:
        mangas: Liste des mangas affichés.
        all_mangas: Liste de tous les mangas (non filtrés).
        reading_lists: Listes de lecture.
        selected_manga_id: ID du manga sélectionné.
        filters: Filtres actifs.
        stats: Statistiques de la bibliothèque.
        loading: Indique si en cours de chargement.
        error: Message d'erreur.
    """

    mangas: list[Manga] = Field(default_factory=list, description="Mangas affichés.")
    all_mangas: list[Manga] = Field(default_factory=list, description="Tous les mangas.")
    reading_lists: list[ReadingList] = Field(default_factory=list, description="Listes.")
    selected_manga_id: str | None = Field(default=None, description="Sélection.")
    filters: LibraryFilters = Field(default_factory=LibraryFilters, description="Filtres.")
    stats: LibraryStats = Field(default_factory=LibraryStats, description="Stats.")
    loading: bool = Field(default=False, description="Chargement.")
    error: str | None = Field(default=None, description="Erreur.")

    model_config = ConfigDict(extra="forbid")

    @property
    def selected_manga(self) -> Manga | None:
        """Manga sélectionné."""
        if self.selected_manga_id is None:
            return None
        for manga in self.mangas:
            if manga.id == self.selected_manga_id:
                return manga
        return None

    @property
    def filtered_count(self) -> int:
        """Nombre de mangas après filtrage."""
        return len(self.mangas)

    @property
    def total_count(self) -> int:
        """Nombre total de mangas."""
        return len(self.all_mangas)


# ============================================================================
# WIDGETS CUSTOM — Composants de la bibliothèque
# ============================================================================


if TEXTUAL_AVAILABLE:

    class LibraryFilterBar(Widget):
        """Barre de recherche et filtres."""

        DEFAULT_CSS = """
        LibraryFilterBar {
            layout: horizontal;
            height: 3;
            padding: 0 1;
            border-bottom: solid $primary;
        }
        LibraryFilterBar > Label {
            width: auto;
            content-align: left middle;
            padding: 0 1;
        }
        LibraryFilterBar > Input {
            width: 1fr;
        }
        LibraryFilterBar > Select {
            width: 20;
        }
        """

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise la barre de filtres."""
            super().__init__(name=name, id=id, classes=classes)
            self._filters = LibraryFilters()

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Label(t("library.filter.search", default="🔍"))
            yield Input(
                placeholder=t("library.filter.search.placeholder", default="Search manga..."),
                id="library-search",
            )

            yield Label(t("library.filter.status", default="Status:"))
            yield Select(
                [(status.value, f"{status.icon} {status.label}") for status in ReadingStatusFilter],
                id="status-filter",
                value=ReadingStatusFilter.ALL.value,
                allow_blank=False,
            )

            yield Label(t("library.filter.sort", default="Sort:"))
            yield Select(
                [(sort.value, sort.label) for sort in LibrarySortBy],
                id="sort-filter",
                value=LibrarySortBy.TITLE.value,
                allow_blank=False,
            )

            yield Label(t("library.filter.view", default="View:"))
            yield Select(
                [(view.value, f"{view.icon} {view.label}") for view in LibraryViewMode],
                id="view-filter",
                value=LibraryViewMode.GRID.value,
                allow_blank=False,
            )

        def get_filters(self) -> LibraryFilters:
            """Récupère les filtres actuels.

            Returns:
                Instance de LibraryFilters.
            """
            search_input = self.query_one("#library-search", Input)
            status_select = self.query_one("#status-filter", Select)
            sort_select = self.query_one("#sort-filter", Select)
            view_select = self.query_one("#view-filter", Select)

            return LibraryFilters(
                search_query=search_input.value.strip(),
                reading_status=ReadingStatusFilter(status_select.value),
                sort_by=LibrarySortBy(sort_select.value),
                view_mode=LibraryViewMode(view_select.value),
            )

        def on_input_changed(self, event: Input.Changed) -> None:
            """Gère le changement de texte."""
            if event.input.id == "library-search":
                self.post_message(FilterChanged(self.get_filters()))

        def on_select_changed(self, event: Select.Changed) -> None:
            """Gère le changement de sélection."""
            self.post_message(FilterChanged(self.get_filters()))

    class ReadingListItem(ListItem):
        """Item dans la sidebar des listes de lecture."""

        DEFAULT_CSS = """
        ReadingListItem {
            padding: 1;
            height: auto;
        }
        ReadingListItem > .list-name {
            text-style: bold;
        }
        ReadingListItem > .list-count {
            color: $text-muted;
        }
        ReadingListItem.selected {
            background: $primary-background;
        }
        """

        def __init__(
            self,
            reading_list: ReadingList,
            *,
            selected: bool = False,
        ) -> None:
            """Initialise l'item.

            Args:
                reading_list: Liste de lecture.
                selected: Indique si l'item est sélectionné.
            """
            super().__init__()
            self.reading_list = reading_list
            self.selected = selected

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(
                f"{'✓ ' if self.selected else ''}{self.reading_list.name}",
                classes="list-name",
            )
            yield Static(
                f"{len(self.reading_list.manga_ids)} mangas",
                classes="list-count",
            )

    class ReadingListsSidebar(Widget):
        """Sidebar avec les listes de lecture."""

        DEFAULT_CSS = """
        ReadingListsSidebar {
            width: 30;
            border-right: solid $primary;
        }
        ReadingListsSidebar > .sidebar-title {
            text-style: bold;
            padding: 1;
        }
        ReadingListsSidebar > ListView {
            height: 1fr;
        }
        """

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise la sidebar."""
            super().__init__(name=name, id=id, classes=classes)
            self._reading_lists: list[ReadingList] = []

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(
                t("library.sidebar.reading_lists", default="📋 Reading Lists"),
                classes="sidebar-title",
            )
            yield ListView(id="reading-lists-list")

        def update_lists(self, reading_lists: list[ReadingList]) -> None:
            """Met à jour les listes affichées.

            Args:
                reading_lists: Listes de lecture.
            """
            self._reading_lists = reading_lists
            list_view = self.query_one("#reading-lists-list", ListView)
            list_view.clear()

            # Ajouter l'option "Toutes"
            all_item = ReadingListItem(
                ReadingList(id="all", name=t("library.sidebar.all", default="All Mangas"), manga_ids=[]),
                selected=True,
            )
            list_view.append(all_item)

            # Ajouter les listes
            for reading_list in reading_lists:
                item = ReadingListItem(reading_list)
                list_view.append(item)

    class MangaCard(Widget):
        """Carte individuelle pour un manga (mode grille)."""

        DEFAULT_CSS = """
        MangaCard {
            width: 30;
            height: 20;
            padding: 1;
            border: solid $primary;
            background: $surface;
        }
        MangaCard:hover {
            border: solid $accent;
        }
        MangaCard.selected {
            border: solid $success;
            background: $success-background;
        }
        MangaCard > .manga-cover {
            height: 10;
            content-align: center middle;
        }
        MangaCard > .manga-title {
            text-style: bold;
            height: 2;
        }
        MangaCard > .manga-author {
            color: $text-muted;
            height: 1;
        }
        MangaCard > .manga-status {
            height: 1;
        }
        MangaCard > .manga-progress {
            height: 1;
            color: $accent;
        }
        """

        def __init__(
            self,
            manga: Manga,
            *,
            selected: bool = False,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise la carte.

            Args:
                manga: Manga à afficher.
                selected: Indique si la carte est sélectionnée.
                name: Nom du widget.
                id: ID du widget.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self.manga = manga
            self.selected = selected

            if selected:
                self.add_class("selected")

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            # Cover (placeholder si pas d'image)
            if self.manga.cover_url:
                yield Static(f"[Image: {self.manga.cover_url}]", classes="manga-cover")
            else:
                yield Static("📚", classes="manga-cover")

            # Titre
            title = self.manga.title
            if len(title) > 30:
                title = title[:27] + "..."
            yield Static(title, classes="manga-title")

            # Auteur
            if self.manga.author:
                author = self.manga.author
                if len(author) > 25:
                    author = author[:22] + "..."
                yield Static(author, classes="manga-author")

            # Statut
            yield Static(
                f"{self.manga.status.icon} {self.manga.status.label}",
                classes="manga-status",
            )

            # Progression
            if self.manga.reading_progress:
                progress = self.manga.reading_progress
                yield Static(
                    f"{progress.chapters_read}/{progress.total_chapters} ch.",
                    classes="manga-progress",
                )

        def set_selected(self, selected: bool) -> None:
            """Définit l'état de sélection.

            Args:
                selected: True si sélectionné.
            """
            self.selected = selected
            if selected:
                self.add_class("selected")
            else:
                self.remove_class("selected")

    class MangaListItem(ListItem):
        """Item dans la liste de mangas (mode liste)."""

        DEFAULT_CSS = """
        MangaListItem {
            padding: 1;
            height: auto;
        }
        MangaListItem > .manga-title {
            text-style: bold;
        }
        MangaListItem > .manga-meta {
            color: $text-muted;
        }
        MangaListItem.selected {
            background: $primary-background;
        }
        """

        def __init__(
            self,
            manga: Manga,
            *,
            selected: bool = False,
        ) -> None:
            """Initialise l'item.

            Args:
                manga: Manga à afficher.
                selected: Indique si l'item est sélectionné.
            """
            super().__init__()
            self.manga = manga
            self.selected = selected

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            # Titre
            yield Static(
                f"{'✓ ' if self.selected else ''}{self.manga.title}",
                classes="manga-title",
            )

            # Métadonnées
            meta_parts = []
            if self.manga.author:
                meta_parts.append(f"by {self.manga.author}")
            if self.manga.year:
                meta_parts.append(str(self.manga.year))
            meta_parts.append(f"{self.manga.status.icon} {self.manga.status.label}")

            if self.manga.reading_progress:
                progress = self.manga.reading_progress
                meta_parts.append(f"{progress.chapters_read}/{progress.total_chapters} ch.")

            yield Static(" • ".join(meta_parts), classes="manga-meta")

    class MangaGrid(Widget):
        """Grille de mangas."""

        DEFAULT_CSS = """
        MangaGrid {
            height: 1fr;
            padding: 1;
        }
        MangaGrid > .empty-message {
            text-align: center;
            padding: 2;
            color: $text-muted;
        }
        MangaGrid > ListView {
            height: 1fr;
        }
        MangaGrid > .grid-container {
            layout: grid;
            grid-size: 4;
            grid-gutter: 1;
            height: 1fr;
        }
        """

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise la grille."""
            super().__init__(name=name, id=id, classes=classes)
            self._mangas: list[Manga] = []
            self._view_mode = LibraryViewMode.GRID
            self._selected_id: str | None = None

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(
                t("library.empty", default="No manga in library"),
                id="library-empty",
                classes="empty-message",
            )
            yield ListView(id="manga-list")
            with Vertical(id="manga-grid", classes="grid-container"):
                pass

        def update_mangas(
            self,
            mangas: list[Manga],
            view_mode: LibraryViewMode,
        ) -> None:
            """Met à jour les mangas affichés.

            Args:
                mangas: Liste de mangas.
                view_mode: Mode d'affichage.
            """
            self._mangas = mangas
            self._view_mode = view_mode

            empty_msg = self.query_one("#library-empty", Static)
            list_view = self.query_one("#manga-list", ListView)
            grid_container = self.query_one("#manga-grid", Vertical)

            if not mangas:
                empty_msg.display = True
                list_view.display = False
                grid_container.display = False
                return

            empty_msg.display = False

            if view_mode == LibraryViewMode.GRID:
                list_view.display = False
                grid_container.display = True
                grid_container.remove_children()

                for manga in mangas:
                    card = MangaCard(
                        manga,
                        selected=manga.id == self._selected_id,
                    )
                    grid_container.mount(card)
            else:
                list_view.display = True
                grid_container.display = False
                list_view.clear()

                for manga in mangas:
                    item = MangaListItem(
                        manga,
                        selected=manga.id == self._selected_id,
                    )
                    list_view.append(item)

        def set_selected(self, manga_id: str | None) -> None:
            """Définit le manga sélectionné.

            Args:
                manga_id: ID du manga ou None.
            """
            self._selected_id = manga_id

            # Mettre à jour les widgets
            if self._view_mode == LibraryViewMode.GRID:
                grid_container = self.query_one("#manga-grid", Vertical)
                for child in grid_container.children:
                    if isinstance(child, MangaCard):
                        child.set_selected(child.manga.id == manga_id)
            else:
                # TODO: Mettre à jour la sélection dans ListView
                pass

    class MangaDetailsPanel(Widget):
        """Panneau de détails d'un manga."""

        DEFAULT_CSS = """
        MangaDetailsPanel {
            width: 50;
            border-left: solid $primary;
            padding: 1;
        }
        MangaDetailsPanel > .panel-title {
            text-style: bold;
            padding: 0 0 1 0;
        }
        MangaDetailsPanel > .manga-info {
            padding: 1;
        }
        MangaDetailsPanel > .chapters-list {
            height: 1fr;
        }
        MangaDetailsPanel > .actions {
            height: 3;
            layout: horizontal;
        }
        """

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise le panneau."""
            super().__init__(name=name, id=id, classes=classes)
            self._manga: Manga | None = None

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(
                t("library.details.title", default="Manga Details"),
                classes="panel-title",
            )
            yield Static(
                t("library.details.empty", default="Select a manga to view details"),
                id="details-empty",
            )
            with Vertical(id="details-content"):
                yield Static(id="manga-info", classes="manga-info")
                yield ListView(id="chapters-list", classes="chapters-list")
                with Horizontal(classes="actions"):
                    yield Button(
                        t("library.details.read", default="Read"),
                        id="btn-read",
                        variant="primary",
                    )
                    yield Button(
                        t("library.details.download", default="Download"),
                        id="btn-download",
                        variant="success",
                    )

        def update_manga(self, manga: Manga | None) -> None:
            """Met à jour le manga affiché.

            Args:
                manga: Manga à afficher ou None.
            """
            self._manga = manga

            empty_msg = self.query_one("#details-empty", Static)
            content = self.query_one("#details-content", Vertical)

            if manga is None:
                empty_msg.display = True
                content.display = False
                return

            empty_msg.display = False
            content.display = True

            # Mettre à jour les informations
            info_widget = self.query_one("#manga-info", Static)
            info_text = f"""
[b]{manga.title}[/b]
{t('library.details.author', default='Author')}: {manga.author or 'Unknown'}
{t('library.details.year', default='Year')}: {manga.year or 'Unknown'}
{t('library.details.status', default='Status')}: {manga.status.icon} {manga.status.label}
{t('library.details.language', default='Language')}: {manga.language.flag} {manga.language.label}

{manga.description or ''}
"""
            info_widget.update(info_text)

            # Mettre à jour la liste des chapitres
            chapters_list = self.query_one("#chapters-list", ListView)
            chapters_list.clear()

            if manga.chapters:
                for chapter in manga.chapters[:20]:  # Limiter à 20 chapitres
                    chapters_list.append(
                        ListItem(Static(f"Ch. {chapter.number}: {chapter.title}"))
                    )

    class LibraryStatusBar(Widget):
        """Barre de statut avec statistiques."""

        DEFAULT_CSS = """
        LibraryStatusBar {
            layout: horizontal;
            height: 1;
            padding: 0 1;
            background: $primary-background;
        }
        LibraryStatusBar > .stat-item {
            padding: 0 1;
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
            self._stats = LibraryStats()
            self._filtered_count = 0
            self._total_count = 0

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static("", id="status-count", classes="stat-item")
            yield Static("", id="status-stats", classes="stat-item")

        def update_state(
            self,
            *,
            filtered_count: int,
            total_count: int,
            stats: LibraryStats,
        ) -> None:
            """Met à jour l'état affiché.

            Args:
                filtered_count: Nombre de mangas affichés.
                total_count: Nombre total de mangas.
                stats: Statistiques.
            """
            self._filtered_count = filtered_count
            self._total_count = total_count
            self._stats = stats

            count_widget = self.query_one("#status-count", Static)
            stats_widget = self.query_one("#status-stats", Static)

            count_widget.update(
                t(
                    "library.status.count",
                    default="Showing {filtered}/{total} manga",
                    filtered=filtered_count,
                    total=total_count,
                )
            )

            stats_text = (
                f"📚 {stats.total_manga} | "
                f"💾 {format_size(stats.total_size_bytes)} | "
                f"⏱️ {stats.total_reading_time_hours:.1f}h"
            )
            stats_widget.update(stats_text)


# ============================================================================
# MESSAGES — Événements Textual
# ============================================================================


if TEXTUAL_AVAILABLE:

    class MangaSelected(Message):
        """Message émis lorsqu'un manga est sélectionné."""

        def __init__(self, manga_id: str) -> None:
            """Initialise le message.

            Args:
                manga_id: ID du manga sélectionné.
            """
            super().__init__()
            self.manga_id = manga_id

    class FilterChanged(Message):
        """Message émis lorsque les filtres changent."""

        def __init__(self, filters: LibraryFilters) -> None:
            """Initialise le message.

            Args:
                filters: Nouveaux filtres.
            """
            super().__init__()
            self.filters = filters

    class ReadingListSelected(Message):
        """Message émis lorsqu'une liste de lecture est sélectionnée."""

        def __init__(self, list_id: str | None) -> None:
            """Initialise le message.

            Args:
                list_id: ID de la liste ou None pour "toutes".
            """
            super().__init__()
            self.list_id = list_id


# ============================================================================
# CLASSE PRINCIPALE — LibraryScreen
# ============================================================================


if TEXTUAL_AVAILABLE:

    class LibraryScreen(Screen):
        """Écran de bibliothèque."""

        # Bindings clavier
        BINDINGS = [
            Binding("/", "focus_search", "Search"),
            Binding("f", "focus_filter", "Filter"),
            Binding("enter", "view_details", "View Details"),
            Binding("d", "download_selected", "Download"),
            Binding("r", "read_selected", "Read"),
            Binding("x", "delete_selected", "Delete"),
            Binding("escape", "close", "Close"),
        ]

        # CSS de l'écran
        DEFAULT_CSS = """
        LibraryScreen {
            layout: vertical;
        }

        #library-container {
            height: 1fr;
            layout: horizontal;
        }

        #sidebar {
            width: 30;
        }

        #main-content {
            width: 1fr;
            layout: vertical;
        }

        #filter-bar {
            height: auto;
        }

        #manga-area {
            height: 1fr;
            layout: horizontal;
        }

        #manga-grid-container {
            width: 1fr;
        }

        #details-panel {
            width: 50;
            display: none;
        }

        #details-panel.visible {
            display: block;
        }

        #status-bar {
            height: 1;
        }
        """

        # État réactif
        selected_manga_id: reactive[str | None] = reactive(None)

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
            self._state = LibraryState()

        def compose(self) -> ComposeResult:
            """Compose l'écran."""
            yield Header(show_clock=True)

            with Horizontal(id="library-container"):
                # Sidebar
                with Vertical(id="sidebar"):
                    yield ReadingListsSidebar(id="reading-lists-sidebar")

                # Contenu principal
                with Vertical(id="main-content"):
                    yield LibraryFilterBar(id="filter-bar")

                    with Horizontal(id="manga-area"):
                        yield MangaGrid(id="manga-grid-container")
                        yield MangaDetailsPanel(id="details-panel")

            yield LibraryStatusBar(id="status-bar")
            yield Footer()

        def on_mount(self) -> None:
            """Appelé lors du montage."""
            asyncio.create_task(self._load_library())

        # =====================================================================
        # CHARGEMENT
        # =====================================================================

        async def _load_library(self) -> None:
            """Charge la bibliothèque."""
            self._state.loading = True

            try:
                # Charger les mangas
                from nexusdl.core.library import get_all_mangas
                all_mangas = await get_all_mangas()
                self._state.all_mangas = all_mangas

                # Charger les listes de lecture
                from nexusdl.core.library import get_reading_lists
                reading_lists = await get_reading_lists()
                self._state.reading_lists = reading_lists

                # Charger les statistiques
                from nexusdl.core.library import get_library_stats
                stats = await get_library_stats()
                self._state.stats = stats

                # Appliquer les filtres
                await self._apply_filters()

                # Mettre à jour l'UI
                sidebar = self.query_one("#reading-lists-sidebar", ReadingListsSidebar)
                sidebar.update_lists(reading_lists)

                self._state.loading = False
                self._state.error = None

                logger.info("Bibliothèque chargée: {} mangas", len(all_mangas))

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
            if filters.reading_list_id and filters.reading_list_id != "all":
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
                        if m.reading_progress and m.reading_progress.status == target_status
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
                    or any(query_lower in tag.lower() for tag in m.tags)
                ]

            # Tri
            if filters.sort_by == LibrarySortBy.TITLE:
                filtered.sort(key=lambda m: m.title.lower())
            elif filters.sort_by == LibrarySortBy.DATE_ADDED:
                filtered.sort(key=lambda m: m.added_at, reverse=True)
            elif filters.sort_by == LibrarySortBy.LAST_READ:
                filtered.sort(
                    key=lambda m: m.reading_progress.last_read_at if m.reading_progress else datetime.min.replace(tzinfo=UTC),
                    reverse=True,
                )
            elif filters.sort_by == LibrarySortBy.PROGRESS:
                filtered.sort(
                    key=lambda m: m.reading_progress.progress_percentage if m.reading_progress else 0.0,
                    reverse=True,
                )
            elif filters.sort_by == LibrarySortBy.AUTHOR:
                filtered.sort(key=lambda m: (m.author or "").lower())
            elif filters.sort_by == LibrarySortBy.STATUS:
                filtered.sort(key=lambda m: m.status.value)

            self._state.mangas = filtered

            # Mettre à jour l'UI
            manga_grid = self.query_one("#manga-grid-container", MangaGrid)
            manga_grid.update_mangas(filtered, filters.view_mode)

            # Mettre à jour la barre de statut
            status_bar = self.query_one("#status-bar", LibraryStatusBar)
            status_bar.update_state(
                filtered_count=len(filtered),
                total_count=len(self._state.all_mangas),
                stats=self._state.stats,
            )

        # =====================================================================
        # GESTION DES MESSAGES
        # =====================================================================

        def on_filter_changed(self, message: FilterChanged) -> None:
            """Gère un changement de filtres.

            Args:
                message: Message avec les nouveaux filtres.
            """
            self._state.filters = message.filters
            asyncio.create_task(self._apply_filters())

        def on_reading_list_selected(self, message: ReadingListSelected) -> None:
            """Gère la sélection d'une liste de lecture.

            Args:
                message: Message avec l'ID de la liste.
            """
            self._state.filters.reading_list_id = message.list_id
            asyncio.create_task(self._apply_filters())

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            """Gère la sélection d'un item dans une liste."""
            if isinstance(event.item, MangaListItem):
                self.selected_manga_id = event.item.manga.id
                self._show_details(event.item.manga)
            elif isinstance(event.item, ReadingListItem):
                list_id = event.item.reading_list.id if event.item.reading_list.id != "all" else None
                self.post_message(ReadingListSelected(list_id))

        # =====================================================================
        # ACTIONS — Bindings clavier
        # =====================================================================

        def action_focus_search(self) -> None:
            """Action : focus sur le champ de recherche."""
            search_input = self.query_one("#library-search", Input)
            search_input.focus()

        def action_focus_filter(self) -> None:
            """Action : focus sur le filtre de statut."""
            status_select = self.query_one("#status-filter", Select)
            status_select.focus()

        def action_view_details(self) -> None:
            """Action : afficher/masquer les détails."""
            details_panel = self.query_one("#details-panel")

            if "visible" in details_panel.classes:
                details_panel.remove_class("visible")
            else:
                details_panel.add_class("visible")

                # Afficher les détails du manga sélectionné
                if self.selected_manga_id:
                    manga = self._state.selected_manga
                    if manga:
                        details_widget = self.query_one("#details-panel", MangaDetailsPanel)
                        details_widget.update_manga(manga)

        def _show_details(self, manga: Manga) -> None:
            """Affiche les détails d'un manga.

            Args:
                manga: Manga à afficher.
            """
            details_panel = self.query_one("#details-panel", MangaDetailsPanel)
            details_panel.update_manga(manga)
            details_panel.add_class("visible")

        def action_download_selected(self) -> None:
            """Action : télécharger le manga sélectionné."""
            if not self.selected_manga_id:
                self.notify(
                    t("library.notify.no_selection", default="No manga selected"),
                    severity="warning",
                )
                return

            manga = self._state.selected_manga
            if manga:
                self.notify(
                    t(
                        "library.notify.download_started",
                        default="Downloading: {title}",
                        title=manga.title,
                    ),
                    severity="information",
                )
                # TODO: Émettre un événement pour démarrer le téléchargement

        def action_read_selected(self) -> None:
            """Action : lire le manga sélectionné."""
            if not self.selected_manga_id:
                self.notify(
                    t("library.notify.no_selection", default="No manga selected"),
                    severity="warning",
                )
                return

            manga = self._state.selected_manga
            if manga:
                self.notify(
                    t(
                        "library.notify.reading",
                        default="Opening: {title}",
                        title=manga.title,
                    ),
                    severity="information",
                )
                # TODO: Ouvrir le lecteur

        def action_delete_selected(self) -> None:
            """Action : supprimer le manga sélectionné."""
            if not self.selected_manga_id:
                self.notify(
                    t("library.notify.no_selection", default="No manga selected"),
                    severity="warning",
                )
                return

            manga = self._state.selected_manga
            if manga:
                # TODO: Afficher un dialogue de confirmation
                self.notify(
                    t(
                        "library.notify.delete_confirm",
                        default="Delete {title}?",
                        title=manga.title,
                    ),
                    severity="warning",
                )

        def action_close(self) -> None:
            """Action : fermer l'écran."""
            self.app.pop_screen()


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "LibraryScreenError",
    "LibraryLoadError",
    "MangaNotFoundError",
    # Enums
    "LibraryViewMode",
    "LibrarySortBy",
    "ReadingStatusFilter",
    # Modèles
    "LibraryFilters",
    "LibraryState",
    # Écran principal
    "LibraryScreen" if TEXTUAL_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
