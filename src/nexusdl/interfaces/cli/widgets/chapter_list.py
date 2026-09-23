"""Widget de liste de chapitres pour l'interface CLI NexusDL.

Ce module fournit un widget Textual réutilisable pour afficher et gérer une
liste de chapitres d'un manga. Il est utilisé dans plusieurs écrans :

    - LibraryScreen : détails d'un manga (liste des chapitres)
    - DownloadScreen : sélection des chapitres à télécharger
    - SearchScreen : aperçu des chapitres disponibles
    - ReaderScreen : navigation entre les chapitres

**Fonctionnalités** :
    - Affichage des chapitres avec numéro, titre, date, statut
    - Sélection simple ou multiple (configurable)
    - Filtrage par statut (tous, lus, non-lus, téléchargés, non-téléchargés)
    - Tri par numéro, date, titre
    - Recherche par titre
    - Actions : marquer lu/non-lu, télécharger, supprimer
    - Indicateurs visuels (icônes, couleurs, badges)
    - Mode compact ou détaillé
    - Pagination ou scroll infini
    - Callbacks : on_chapter_selected, on_chapter_activated, on_action
    - Traductions i18n
    - Gestion des erreurs

**Architecture** :
    ChapterList (Widget principal)
        ├── ChapterListHeader (titre + contrôles + stats)
        │   ├── Titre
        │   ├── Boutons d'action globale
        │   └── Compteur (affichés/total)
        ├── ChapterListFilters (barre de filtrage)
        │   ├── Input (recherche)
        │   ├── Select (filtre statut)
        │   └── Select (tri)
        ├── ChapterListContent (liste des chapitres)
        │   └── ChapterItem (item individuel)
        └── ChapterListFooter (actions globales)

**Modes d'affichage** :
    - COMPACT : Une seule ligne par chapitre (numéro + titre + statut)
    - NORMAL : Deux lignes (numéro + titre | date + statut)
    - DETAILED : Multi-lignes avec toutes les informations

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.cli.widgets.chapter_list import (
    ...     ChapterList, ChapterListMode,
    ... )
    >>>
    >>> # Dans un écran Textual
    >>> chapter_list = ChapterList(
    ...     chapters=manga.chapters,
    ...     mode=ChapterListMode.NORMAL,
    ...     selectable=True,
    ...     on_chapter_activated=my_callback,
    ... )
    >>> self.mount(chapter_list)
    >>>
    >>> # Récupérer la sélection
    >>> selected = chapter_list.get_selected_chapters()

Intégration :
    - core/models/manga.py : modèle Chapter
    - core/events.py : émission d'événements
    - core/i18n.py : traductions
    - core/utils/time.py : formatage des dates
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
    from textual.widgets import (
        Button,
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

from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t
from nexusdl.core.models.manga import Chapter, ChapterStatus
from nexusdl.core.utils.time import format_datetime


# ============================================================================
# CONSTANTES
# ============================================================================


# Longueur maximale pour troncature
MAX_CHAPTER_TITLE_LENGTH: Final[int] = 50
MAX_CHAPTER_SCANLATOR_LENGTH: Final[int] = 30

# Icônes de statut
STATUS_ICONS: Final[dict[str, str]] = {
    "read": "✅",
    "unread": "📖",
    "downloaded": "💾",
    "downloading": "⏬",
    "pending": "⏳",
    "failed": "❌",
}


# ============================================================================
# EXCEPTIONS
# ============================================================================


class ChapterListError(NexusDLError):
    """Exception de base pour les erreurs de la liste de chapitres."""


class InvalidChapterError(ChapterListError):
    """Exception levée lorsqu'un chapitre est invalide.

    Attributes:
        chapter_id: ID du chapitre invalide.
        reason: Raison de l'invalidité.
    """

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


class ChapterListMode(str, Enum):
    """Mode d'affichage de la liste de chapitres.

    Attributes:
        COMPACT: Une seule ligne par chapitre.
        NORMAL: Deux lignes par chapitre.
        DETAILED: Multi-lignes avec toutes les informations.
    """

    COMPACT = "compact"
    NORMAL = "normal"
    DETAILED = "detailed"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ChapterListMode.COMPACT: t("chapter_list.mode.compact", default="Compact"),
            ChapterListMode.NORMAL: t("chapter_list.mode.normal", default="Normal"),
            ChapterListMode.DETAILED: t("chapter_list.mode.detailed", default="Detailed"),
        }[self]


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
            ChapterFilter.ALL: t("chapter_list.filter.all", default="All"),
            ChapterFilter.READ: t("chapter_list.filter.read", default="Read"),
            ChapterFilter.UNREAD: t("chapter_list.filter.unread", default="Unread"),
            ChapterFilter.DOWNLOADED: t("chapter_list.filter.downloaded", default="Downloaded"),
            ChapterFilter.NOT_DOWNLOADED: t("chapter_list.filter.not_downloaded", default="Not Downloaded"),
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
        NUMBER: Tri par numéro de chapitre.
        DATE: Tri par date de publication.
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
            ChapterSortBy.NUMBER: t("chapter_list.sort.number", default="Number"),
            ChapterSortBy.DATE: t("chapter_list.sort.date", default="Date"),
            ChapterSortBy.TITLE: t("chapter_list.sort.title", default="Title"),
            ChapterSortBy.STATUS: t("chapter_list.sort.status", default="Status"),
        }[self]


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class ChapterListConfig(BaseModel):
    """Configuration de la liste de chapitres.

    Attributes:
        mode: Mode d'affichage.
        show_header: Afficher l'en-tête.
        show_filters: Afficher les filtres.
        show_footer: Afficher le pied de page.
        show_date: Afficher la date.
        show_scanlator: Afficher le scanlator.
        show_pages_count: Afficher le nombre de pages.
        selectable: Permettre la sélection.
        multi_select: Permettre la sélection multiple.
        show_actions: Afficher les boutons d'action.
        max_title_length: Longueur max du titre.
        max_scanlator_length: Longueur max du scanlator.
        reverse_order: Ordre inversé (plus récent en premier).
    """

    mode: ChapterListMode = Field(
        default=ChapterListMode.NORMAL,
        description="Mode d'affichage.",
    )
    show_header: bool = Field(
        default=True,
        description="Afficher l'en-tête.",
    )
    show_filters: bool = Field(
        default=True,
        description="Afficher les filtres.",
    )
    show_footer: bool = Field(
        default=True,
        description="Afficher le pied de page.",
    )
    show_date: bool = Field(
        default=True,
        description="Afficher la date.",
    )
    show_scanlator: bool = Field(
        default=True,
        description="Afficher le scanlator.",
    )
    show_pages_count: bool = Field(
        default=True,
        description="Afficher le nombre de pages.",
    )
    selectable: bool = Field(
        default=True,
        description="Permettre la sélection.",
    )
    multi_select: bool = Field(
        default=True,
        description="Permettre la sélection multiple.",
    )
    show_actions: bool = Field(
        default=True,
        description="Afficher les boutons d'action.",
    )
    max_title_length: int = Field(
        default=MAX_CHAPTER_TITLE_LENGTH,
        ge=10,
        le=200,
        description="Longueur max du titre.",
    )
    max_scanlator_length: int = Field(
        default=MAX_CHAPTER_SCANLATOR_LENGTH,
        ge=10,
        le=100,
        description="Longueur max du scanlator.",
    )
    reverse_order: bool = Field(
        default=True,
        description="Ordre inversé (plus récent en premier).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class ChapterListFilters(BaseModel):
    """Filtres appliqués à la liste de chapitres.

    Attributes:
        status_filter: Filtre par statut.
        sort_by: Critère de tri.
        search_query: Texte de recherche.
        reverse: Si True, tri inversé.
    """

    status_filter: ChapterFilter = Field(
        default=ChapterFilter.ALL,
        description="Filtre par statut.",
    )
    sort_by: ChapterSortBy = Field(
        default=ChapterSortBy.NUMBER,
        description="Critère de tri.",
    )
    search_query: str = Field(
        default="",
        description="Recherche.",
    )
    reverse: bool = Field(
        default=False,
        description="Tri inversé.",
    )

    model_config = ConfigDict(extra="forbid")


class ChapterListState(BaseModel):
    """État de la liste de chapitres.

    Attributes:
        all_chapters: Liste de tous les chapitres.
        filtered_chapters: Liste des chapitres après filtrage.
        selected_ids: IDs des chapitres sélectionnés.
        filters: Filtres actifs.
        loading: Indique si en cours de chargement.
        error: Message d'erreur.
    """

    all_chapters: list[Chapter] = Field(default_factory=list, description="Tous les chapitres.")
    filtered_chapters: list[Chapter] = Field(default_factory=list, description="Chapitres filtrés.")
    selected_ids: set[str] = Field(default_factory=set, description="Sélections.")
    filters: ChapterListFilters = Field(default_factory=ChapterListFilters, description="Filtres.")
    loading: bool = Field(default=False, description="Chargement.")
    error: str | None = Field(default=None, description="Erreur.")

    model_config = ConfigDict(extra="forbid")

    @property
    def selected_count(self) -> int:
        """Nombre de chapitres sélectionnés."""
        return len(self.selected_ids)

    @property
    def total_count(self) -> int:
        """Nombre total de chapitres."""
        return len(self.all_chapters)

    @property
    def filtered_count(self) -> int:
        """Nombre de chapitres après filtrage."""
        return len(self.filtered_chapters)

    def is_selected(self, chapter_id: str) -> bool:
        """Vérifie si un chapitre est sélectionné.

        Args:
            chapter_id: ID du chapitre.

        Returns:
            True si sélectionné.
        """
        return chapter_id in self.selected_ids


# ============================================================================
# HELPERS — Formatage
# ============================================================================


def format_chapter_number(chapter: Chapter) -> str:
    """Formate le numéro d'un chapitre.

    Args:
        chapter: Chapitre à formater.

    Returns:
        Numéro formaté.
    """
    if chapter.number == int(chapter.number):
        return f"Ch. {int(chapter.number)}"
    return f"Ch. {chapter.number}"


def format_chapter_status(chapter: Chapter) -> str:
    """Formate le statut d'un chapitre avec icône.

    Args:
        chapter: Chapitre à formater.

    Returns:
        Statut formaté avec icône.
    """
    if chapter.is_downloaded:
        return f"{STATUS_ICONS['downloaded']} Downloaded"
    if chapter.is_read:
        return f"{STATUS_ICONS['read']} Read"
    return f"{STATUS_ICONS['unread']} Unread"


def format_chapter_date(chapter: Chapter) -> str:
    """Formate la date d'un chapitre.

    Args:
        chapter: Chapitre à formater.

    Returns:
        Date formatée.
    """
    if chapter.published_at:
        return format_datetime(chapter.published_at, style="date")
    return "Unknown date"


# ============================================================================
# WIDGETS CUSTOM — Composants de la liste
# ============================================================================


if TEXTUAL_AVAILABLE:

    class ChapterItem(ListItem):
        """Item individuel représentant un chapitre."""

        DEFAULT_CSS = """
        ChapterItem {
            padding: 1;
            height: auto;
            min-height: 1;
        }
        ChapterItem > .chapter-content {
            layout: horizontal;
            width: 1fr;
        }
        ChapterItem > .chapter-checkbox {
            width: 3;
        }
        ChapterItem > .chapter-number {
            width: 10;
            text-style: bold;
        }
        ChapterItem > .chapter-title {
            width: 1fr;
        }
        ChapterItem > .chapter-meta {
            width: auto;
            color: $text-muted;
        }
        ChapterItem.selected {
            background: $primary-background 30%;
        }
        ChapterItem.read {
            opacity: 0.7;
        }
        ChapterItem.downloaded {
            border-left: thick $success;
        }
        """

        def __init__(
            self,
            chapter: Chapter,
            *,
            selected: bool = False,
            config: ChapterListConfig,
        ) -> None:
            """Initialise l'item.

            Args:
                chapter: Chapitre à afficher.
                selected: Indique si l'item est sélectionné.
                config: Configuration de la liste.
            """
            super().__init__()
            self.chapter = chapter
            self.selected = selected
            self.config = config

            if selected:
                self.add_class("selected")
            if chapter.is_read:
                self.add_class("read")
            if chapter.is_downloaded:
                self.add_class("downloaded")

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            mode = self.config.mode

            if mode == ChapterListMode.COMPACT:
                yield from self._compose_compact()
            elif mode == ChapterListMode.NORMAL:
                yield from self._compose_normal()
            elif mode == ChapterListMode.DETAILED:
                yield from self._compose_detailed()

        def _compose_compact(self) -> ComposeResult:
            """Compose le mode compact."""
            with Horizontal(classes="chapter-content"):
                # Checkbox
                if self.config.selectable:
                    checkbox = "✓" if self.selected else "○"
                    yield Static(checkbox, classes="chapter-checkbox")

                # Numéro
                yield Static(format_chapter_number(self.chapter), classes="chapter-number")

                # Titre
                from nexusdl.core.utils.text import truncate
                title = truncate(self.chapter.title or "No title", self.config.max_title_length)
                yield Static(title, classes="chapter-title")

                # Statut
                yield Static(format_chapter_status(self.chapter), classes="chapter-meta")

        def _compose_normal(self) -> ComposeResult:
            """Compose le mode normal."""
            with Horizontal(classes="chapter-content"):
                # Checkbox
                if self.config.selectable:
                    checkbox = "✓" if self.selected else "○"
                    yield Static(checkbox, classes="chapter-checkbox")

                # Numéro
                yield Static(format_chapter_number(self.chapter), classes="chapter-number")

                # Titre
                from nexusdl.core.utils.text import truncate
                title = truncate(self.chapter.title or "No title", self.config.max_title_length)
                yield Static(title, classes="chapter-title")

            # Deuxième ligne : métadonnées
            meta_parts: list[str] = []

            if self.config.show_date and self.chapter.published_at:
                meta_parts.append(format_chapter_date(self.chapter))

            if self.config.show_scanlator and self.chapter.scanlator:
                from nexusdl.core.utils.text import truncate
                scanlator = truncate(self.chapter.scanlator, self.config.max_scanlator_length)
                meta_parts.append(f"by {scanlator}")

            if self.config.show_pages_count and self.chapter.pages_count:
                meta_parts.append(f"{self.chapter.pages_count} pages")

            meta_parts.append(format_chapter_status(self.chapter))

            if meta_parts:
                yield Static(" • ".join(meta_parts), classes="chapter-meta")

        def _compose_detailed(self) -> ComposeResult:
            """Compose le mode détaillé."""
            # Première ligne : checkbox + numéro + titre
            with Horizontal(classes="chapter-content"):
                if self.config.selectable:
                    checkbox = "✓" if self.selected else "○"
                    yield Static(checkbox, classes="chapter-checkbox")

                yield Static(format_chapter_number(self.chapter), classes="chapter-number")
                yield Static(self.chapter.title or "No title", classes="chapter-title")

            # Deuxième ligne : date + scanlator
            meta_parts: list[str] = []

            if self.config.show_date and self.chapter.published_at:
                meta_parts.append(f"📅 {format_chapter_date(self.chapter)}")

            if self.config.show_scanlator and self.chapter.scanlator:
                meta_parts.append(f"✍️ {self.chapter.scanlator}")

            if meta_parts:
                yield Static(" • ".join(meta_parts), classes="chapter-meta")

            # Troisième ligne : statut + pages
            status_parts: list[str] = []
            status_parts.append(format_chapter_status(self.chapter))

            if self.config.show_pages_count and self.chapter.pages_count:
                status_parts.append(f"📄 {self.chapter.pages_count} pages")

            if self.chapter.language:
                status_parts.append(f"{self.chapter.language.flag} {self.chapter.language.value.upper()}")

            yield Static(" • ".join(status_parts), classes="chapter-meta")

            # Description si présente
            if self.chapter.description:
                yield Static(f"  {self.chapter.description}", classes="chapter-description")

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
            self.refresh()

    class ChapterListHeader(Widget):
        """En-tête de la liste de chapitres."""

        DEFAULT_CSS = """
        ChapterListHeader {
            layout: horizontal;
            height: 3;
            padding: 0 1;
            border-bottom: solid $primary;
        }
        ChapterListHeader > .title {
            width: 1fr;
            text-style: bold;
            content-align: left middle;
        }
        ChapterListHeader > .actions {
            width: auto;
            layout: horizontal;
        }
        ChapterListHeader > .actions > Button {
            margin: 0 1;
        }
        ChapterListHeader > .stats {
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
            self._selected = 0

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(self._title, classes="title")

            with Horizontal(classes="actions"):
                yield Button(
                    t("chapter_list.button.select_all", default="Select All"),
                    id="btn-select-all",
                    variant="default",
                )
                yield Button(
                    t("chapter_list.button.deselect_all", default="Deselect All"),
                    id="btn-deselect-all",
                    variant="default",
                )

            yield Static("", id="chapter-stats", classes="stats")

        def update_stats(self, displayed: int, total: int, selected: int) -> None:
            """Met à jour les statistiques.

            Args:
                displayed: Nombre de chapitres affichés.
                total: Nombre total de chapitres.
                selected: Nombre de chapitres sélectionnés.
            """
            self._displayed = displayed
            self._total = total
            self._selected = selected

            stats_widget = self.query_one("#chapter-stats", Static)
            stats_text = f"{displayed}/{total} chapters"
            if selected > 0:
                stats_text += f" ({selected} selected)"
            stats_widget.update(stats_text)

    class ChapterListFilters(Widget):
        """Barre de filtrage des chapitres."""

        DEFAULT_CSS = """
        ChapterListFilters {
            layout: horizontal;
            height: 3;
            padding: 0 1;
        }
        ChapterListFilters > Label {
            width: auto;
            content-align: left middle;
            padding: 0 1;
        }
        ChapterListFilters > Select {
            width: 20;
        }
        ChapterListFilters > Input {
            width: 1fr;
        }
        """

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
        ) -> None:
            """Initialise la barre de filtres."""
            super().__init__(name=name, id=id)

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Label(t("chapter_list.filter.status", default="Status:"))
            yield Select(
                [(f.value, f"{f.icon} {f.label}") for f in ChapterFilter],
                id="chapter-status-filter",
                value=ChapterFilter.ALL.value,
                allow_blank=False,
            )

            yield Label(t("chapter_list.filter.sort", default="Sort:"))
            yield Select(
                [(s.value, s.label) for s in ChapterSortBy],
                id="chapter-sort-filter",
                value=ChapterSortBy.NUMBER.value,
                allow_blank=False,
            )

            yield Label(t("chapter_list.filter.search", default="🔍"))
            yield Input(
                placeholder=t("chapter_list.filter.search.placeholder", default="Search chapters..."),
                id="chapter-search",
            )

        def get_filters(self) -> ChapterListFilters:
            """Récupère les filtres actuels.

            Returns:
                Instance de ChapterListFilters.
            """
            status_select = self.query_one("#chapter-status-filter", Select)
            sort_select = self.query_one("#chapter-sort-filter", Select)
            search_input = self.query_one("#chapter-search", Input)

            return ChapterListFilters(
                status_filter=ChapterFilter(status_select.value),
                sort_by=ChapterSortBy(sort_select.value),
                search_query=search_input.value.strip(),
            )

        def on_select_changed(self, event: Select.Changed) -> None:
            """Gère le changement de sélection."""
            self.post_message(FiltersChanged(self.get_filters()))

        def on_input_changed(self, event: Input.Changed) -> None:
            """Gère le changement de texte."""
            if event.input.id == "chapter-search":
                self.post_message(FiltersChanged(self.get_filters()))


# ============================================================================
# MESSAGES — Événements Textual
# ============================================================================


if TEXTUAL_AVAILABLE:

    class ChapterSelected(Message):
        """Message émis lorsqu'un chapitre est sélectionné."""

        def __init__(self, chapter_id: str, selected: bool) -> None:
            """Initialise le message.

            Args:
                chapter_id: ID du chapitre.
                selected: True si sélectionné, False si désélectionné.
            """
            super().__init__()
            self.chapter_id = chapter_id
            self.selected = selected

    class ChapterActivated(Message):
        """Message émis lorsqu'un chapitre est activé (Enter)."""

        def __init__(self, chapter_id: str) -> None:
            """Initialise le message.

            Args:
                chapter_id: ID du chapitre.
            """
            super().__init__()
            self.chapter_id = chapter_id

    class FiltersChanged(Message):
        """Message émis lorsque les filtres changent."""

        def __init__(self, filters: ChapterListFilters) -> None:
            """Initialise le message.

            Args:
                filters: Nouveaux filtres.
            """
            super().__init__()
            self.filters = filters

    class ChapterAction(Message):
        """Message émis lorsqu'une action est effectuée sur un chapitre."""

        def __init__(self, chapter_id: str, action: str) -> None:
            """Initialise le message.

            Args:
                chapter_id: ID du chapitre.
                action: Action effectuée.
            """
            super().__init__()
            self.chapter_id = chapter_id
            self.action = action


# ============================================================================
# CLASSE PRINCIPALE — ChapterList
# ============================================================================


if TEXTUAL_AVAILABLE:

    class ChapterList(Widget):
        """Widget de liste de chapitres.

        Widget réutilisable affichant une liste de chapitres avec filtrage,
        tri, sélection, et actions.
        """

        # Bindings clavier
        BINDINGS = [
            Binding("a", "select_all", "Select All"),
            Binding("n", "deselect_all", "Deselect All"),
            Binding("i", "invert_selection", "Invert"),
            Binding("enter", "activate_chapter", "Open"),
            Binding("space", "toggle_selection", "Toggle"),
        ]

        # CSS du widget
        DEFAULT_CSS = """
        ChapterList {
            height: 1fr;
            layout: vertical;
            border: solid $primary;
        }
        ChapterList > #chapter-header {
            height: auto;
        }
        ChapterList > #chapter-filters {
            height: auto;
        }
        ChapterList > #chapter-content {
            height: 1fr;
        }
        ChapterList > #chapter-content > ListView {
            height: 1fr;
        }
        ChapterList > #chapter-empty {
            text-align: center;
            padding: 2;
            color: $text-muted;
        }
        ChapterList > #chapter-footer {
            height: 3;
            layout: horizontal;
            padding: 0 1;
            align: right middle;
        }
        ChapterList > #chapter-footer > Button {
            margin: 0 1;
        }
        """

        def __init__(
            self,
            chapters: list[Chapter] | None = None,
            *,
            config: ChapterListConfig | None = None,
            title: str = "",
            on_chapter_selected: Callable[[str, bool], None] | None = None,
            on_chapter_activated: Callable[[str], None] | None = None,
            on_action: Callable[[str, str], None] | None = None,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise la liste.

            Args:
                chapters: Liste de chapitres à afficher.
                config: Configuration de la liste.
                title: Titre de la liste.
                on_chapter_selected: Callback lors de la sélection.
                on_chapter_activated: Callback lors de l'activation.
                on_action: Callback lors d'une action.
                name: Nom du widget.
                id: ID du widget.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self._config = config or ChapterListConfig()
            self._title = title or t("chapter_list.title", default="Chapters")
            self._state = ChapterListState()
            self._on_chapter_selected = on_chapter_selected
            self._on_chapter_activated = on_chapter_activated
            self._on_action = on_action

            if chapters:
                self.set_chapters(chapters)

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            # En-tête
            if self._config.show_header:
                yield ChapterListHeader(self._title, id="chapter-header")

            # Filtres
            if self._config.show_filters:
                yield ChapterListFilters(id="chapter-filters")

            # Contenu
            with Vertical(id="chapter-content"):
                yield Static(
                    t("chapter_list.empty", default="No chapters"),
                    id="chapter-empty",
                )
                yield ListView(id="chapter-list")

            # Pied de page
            if self._config.show_footer and self._config.show_actions:
                with Horizontal(id="chapter-footer"):
                    yield Button(
                        t("chapter_list.action.download", default="Download Selected"),
                        id="btn-download",
                        variant="success",
                    )
                    yield Button(
                        t("chapter_list.action.mark_read", default="Mark as Read"),
                        id="btn-mark-read",
                        variant="default",
                    )
                    yield Button(
                        t("chapter_list.action.mark_unread", default="Mark as Unread"),
                        id="btn-mark-unread",
                        variant="default",
                    )

        # =====================================================================
        # API PUBLIQUE — Gestion des chapitres
        # =====================================================================

        def set_chapters(self, chapters: list[Chapter]) -> None:
            """Définit la liste des chapitres.

            Args:
                chapters: Liste de chapitres.
            """
            self._state.all_chapters = list(chapters)
            asyncio.create_task(self._apply_filters())

        def add_chapter(self, chapter: Chapter) -> None:
            """Ajoute un chapitre à la liste.

            Args:
                chapter: Chapitre à ajouter.
            """
            self._state.all_chapters.append(chapter)
            asyncio.create_task(self._apply_filters())

        def remove_chapter(self, chapter_id: str) -> bool:
            """Supprime un chapitre de la liste.

            Args:
                chapter_id: ID du chapitre à supprimer.

            Returns:
                True si le chapitre a été supprimé.
            """
            initial_count = len(self._state.all_chapters)
            self._state.all_chapters = [
                c for c in self._state.all_chapters if c.id != chapter_id
            ]
            removed = len(self._state.all_chapters) < initial_count

            if removed:
                asyncio.create_task(self._apply_filters())

            return removed

        def update_chapter(self, chapter: Chapter) -> None:
            """Met à jour un chapitre dans la liste.

            Args:
                chapter: Chapitre mis à jour.
            """
            for i, c in enumerate(self._state.all_chapters):
                if c.id == chapter.id:
                    self._state.all_chapters[i] = chapter
                    break

            asyncio.create_task(self._apply_filters())

        def get_chapter(self, chapter_id: str) -> Chapter | None:
            """Récupère un chapitre par son ID.

            Args:
                chapter_id: ID du chapitre.

            Returns:
                Chapitre ou None.
            """
            for chapter in self._state.all_chapters:
                if chapter.id == chapter_id:
                    return chapter
            return None

        # =====================================================================
        # API PUBLIQUE — Sélection
        # =====================================================================

        def toggle_chapter(self, chapter_id: str) -> None:
            """Bascule la sélection d'un chapitre.

            Args:
                chapter_id: ID du chapitre.
            """
            if not self._config.selectable:
                return

            if not self._config.multi_select:
                # Mode sélection simple : désélectionner tous les autres
                self._state.selected_ids.clear()
                self._state.selected_ids.add(chapter_id)
            else:
                # Mode sélection multiple : toggle
                if chapter_id in self._state.selected_ids:
                    self._state.selected_ids.remove(chapter_id)
                else:
                    self._state.selected_ids.add(chapter_id)

            # Mettre à jour l'UI
            self._update_selection_ui()
            self._notify_chapter_selected(chapter_id, chapter_id in self._state.selected_ids)

        def select_chapter(self, chapter_id: str) -> None:
            """Sélectionne un chapitre.

            Args:
                chapter_id: ID du chapitre.
            """
            if not self._config.selectable:
                return

            self._state.selected_ids.add(chapter_id)
            self._update_selection_ui()
            self._notify_chapter_selected(chapter_id, True)

        def deselect_chapter(self, chapter_id: str) -> None:
            """Désélectionne un chapitre.

            Args:
                chapter_id: ID du chapitre.
            """
            if not self._config.selectable:
                return

            self._state.selected_ids.discard(chapter_id)
            self._update_selection_ui()
            self._notify_chapter_selected(chapter_id, False)

        def select_all(self) -> None:
            """Sélectionne tous les chapitres filtrés."""
            if not self._config.selectable or not self._config.multi_select:
                return

            self._state.selected_ids = {c.id for c in self._state.filtered_chapters}
            self._update_selection_ui()

        def deselect_all(self) -> None:
            """Désélectionne tous les chapitres."""
            if not self._config.selectable:
                return

            self._state.selected_ids.clear()
            self._update_selection_ui()

        def invert_selection(self) -> None:
            """Inverse la sélection."""
            if not self._config.selectable or not self._config.multi_select:
                return

            all_ids = {c.id for c in self._state.filtered_chapters}
            self._state.selected_ids = all_ids - self._state.selected_ids
            self._update_selection_ui()

        def get_selected_chapters(self) -> list[Chapter]:
            """Récupère les chapitres sélectionnés.

            Returns:
                Liste de Chapter sélectionnés.
            """
            return [
                c for c in self._state.all_chapters
                if c.id in self._state.selected_ids
            ]

        def get_selected_ids(self) -> set[str]:
            """Récupère les IDs des chapitres sélectionnés.

            Returns:
                Set d'IDs.
            """
            return set(self._state.selected_ids)

        def is_selected(self, chapter_id: str) -> bool:
            """Vérifie si un chapitre est sélectionné.

            Args:
                chapter_id: ID du chapitre.

            Returns:
                True si sélectionné.
            """
            return self._state.is_selected(chapter_id)

        # =====================================================================
        # FILTRAGE ET TRI
        # =====================================================================

        async def _apply_filters(self) -> None:
            """Applique les filtres et met à jour l'affichage."""
            filters = self._state.filters

            # Commencer avec tous les chapitres
            filtered = list(self._state.all_chapters)

            # Filtrer par statut
            if filters.status_filter == ChapterFilter.READ:
                filtered = [c for c in filtered if c.is_read]
            elif filters.status_filter == ChapterFilter.UNREAD:
                filtered = [c for c in filtered if not c.is_read]
            elif filters.status_filter == ChapterFilter.DOWNLOADED:
                filtered = [c for c in filtered if c.is_downloaded]
            elif filters.status_filter == ChapterFilter.NOT_DOWNLOADED:
                filtered = [c for c in filtered if not c.is_downloaded]

            # Recherche texte
            if filters.search_query:
                query_lower = filters.search_query.lower()
                filtered = [
                    c for c in filtered
                    if query_lower in (c.title or "").lower()
                    or query_lower in (c.scanlator or "").lower()
                ]

            # Tri
            if filters.sort_by == ChapterSortBy.NUMBER:
                filtered.sort(key=lambda c: c.number, reverse=filters.reverse)
            elif filters.sort_by == ChapterSortBy.DATE:
                filtered.sort(
                    key=lambda c: c.published_at or datetime.min.replace(tzinfo=UTC),
                    reverse=not filters.reverse,
                )
            elif filters.sort_by == ChapterSortBy.TITLE:
                filtered.sort(key=lambda c: (c.title or "").lower(), reverse=filters.reverse)
            elif filters.sort_by == ChapterSortBy.STATUS:
                filtered.sort(key=lambda c: (c.is_read, c.is_downloaded), reverse=filters.reverse)

            # Ordre inversé si configuré
            if self._config.reverse_order and filters.sort_by == ChapterSortBy.NUMBER:
                filtered.reverse()

            self._state.filtered_chapters = filtered

            # Mettre à jour l'UI
            self._render_chapters()

        def _render_chapters(self) -> None:
            """Rend la liste des chapitres dans l'UI."""
            list_view = self.query_one("#chapter-list", ListView)
            empty_msg = self.query_one("#chapter-empty", Static)

            list_view.clear()

            if not self._state.filtered_chapters:
                empty_msg.display = True
                list_view.display = False
                return

            empty_msg.display = False
            list_view.display = True

            for chapter in self._state.filtered_chapters:
                item = ChapterItem(
                    chapter,
                    selected=self._state.is_selected(chapter.id),
                    config=self._config,
                )
                list_view.append(item)

            # Mettre à jour les statistiques
            if self._config.show_header:
                header = self.query_one("#chapter-header", ChapterListHeader)
                header.update_stats(
                    displayed=len(self._state.filtered_chapters),
                    total=len(self._state.all_chapters),
                    selected=self._state.selected_count,
                )

        def _update_selection_ui(self) -> None:
            """Met à jour l'UI pour refléter la sélection."""
            list_view = self.query_one("#chapter-list", ListView)

            for child in list_view.children:
                if isinstance(child, ChapterItem):
                    child.set_selected(self._state.is_selected(child.chapter.id))

            # Mettre à jour les statistiques
            if self._config.show_header:
                try:
                    header = self.query_one("#chapter-header", ChapterListHeader)
                    header.update_stats(
                        displayed=len(self._state.filtered_chapters),
                        total=len(self._state.all_chapters),
                        selected=self._state.selected_count,
                    )
                except Exception:
                    pass

        def _notify_chapter_selected(self, chapter_id: str, selected: bool) -> None:
            """Notifie la sélection d'un chapitre."""
            # Message Textual
            self.post_message(ChapterSelected(chapter_id, selected))

            # Callback
            if self._on_chapter_selected is not None:
                try:
                    self._on_chapter_selected(chapter_id, selected)
                except Exception as e:
                    logger.error("Erreur dans le callback on_chapter_selected: {}", e)

        # =====================================================================
        # GESTION DES ÉVÉNEMENTS
        # =====================================================================

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            """Gère la sélection d'un item dans la liste."""
            if isinstance(event.item, ChapterItem):
                if self._config.selectable:
                    self.toggle_chapter(event.item.chapter.id)

        def on_filters_changed(self, event: FiltersChanged) -> None:
            """Gère le changement de filtres."""
            self._state.filters = event.filters
            asyncio.create_task(self._apply_filters())

        def on_button_pressed(self, event: Button.Pressed) -> None:
            """Gère les clics sur les boutons."""
            if event.button.id == "btn-select-all":
                self.action_select_all()
            elif event.button.id == "btn-deselect-all":
                self.action_deselect_all()
            elif event.button.id == "btn-download":
                self._action_download_selected()
            elif event.button.id == "btn-mark-read":
                self._action_mark_read_selected()
            elif event.button.id == "btn-mark-unread":
                self._action_mark_unread_selected()

        # =====================================================================
        # ACTIONS — Bindings clavier
        # =====================================================================

        def action_select_all(self) -> None:
            """Action : sélectionner tous les chapitres."""
            self.select_all()

        def action_deselect_all(self) -> None:
            """Action : désélectionner tous les chapitres."""
            self.deselect_all()

        def action_invert_selection(self) -> None:
            """Action : inverser la sélection."""
            self.invert_selection()

        def action_activate_chapter(self) -> None:
            """Action : activer le chapitre sélectionné."""
            list_view = self.query_one("#chapter-list", ListView)
            if list_view.index is not None:
                items = [child for child in list_view.children if isinstance(child, ChapterItem)]
                if 0 <= list_view.index < len(items):
                    chapter = items[list_view.index].chapter
                    self.post_message(ChapterActivated(chapter.id))

                    if self._on_chapter_activated is not None:
                        try:
                            self._on_chapter_activated(chapter.id)
                        except Exception as e:
                            logger.error("Erreur dans le callback on_chapter_activated: {}", e)

        def action_toggle_selection(self) -> None:
            """Action : basculer la sélection du chapitre courant."""
            list_view = self.query_one("#chapter-list", ListView)
            if list_view.index is not None:
                items = [child for child in list_view.children if isinstance(child, ChapterItem)]
                if 0 <= list_view.index < len(items):
                    chapter = items[list_view.index].chapter
                    self.toggle_chapter(chapter.id)

        def _action_download_selected(self) -> None:
            """Action : télécharger les chapitres sélectionnés."""
            selected = self.get_selected_chapters()
            if not selected:
                self.notify(
                    t("chapter_list.notify.no_selection", default="No chapters selected"),
                    severity="warning",
                )
                return

            # Émettre un message pour chaque chapitre
            for chapter in selected:
                self.post_message(ChapterAction(chapter.id, "download"))

            if self._on_action is not None:
                for chapter in selected:
                    try:
                        self._on_action(chapter.id, "download")
                    except Exception as e:
                        logger.error("Erreur dans le callback on_action: {}", e)

            self.notify(
                t(
                    "chapter_list.notify.download_started",
                    default="Downloading {count} chapter(s)",
                    count=len(selected),
                ),
                severity="information",
            )

        def _action_mark_read_selected(self) -> None:
            """Action : marquer les chapitres sélectionnés comme lus."""
            selected = self.get_selected_chapters()
            if not selected:
                self.notify(
                    t("chapter_list.notify.no_selection", default="No chapters selected"),
                    severity="warning",
                )
                return

            for chapter in selected:
                chapter.is_read = True
                self.post_message(ChapterAction(chapter.id, "mark_read"))

                if self._on_action is not None:
                    try:
                        self._on_action(chapter.id, "mark_read")
                    except Exception as e:
                        logger.error("Erreur dans le callback on_action: {}", e)

            self._render_chapters()

            self.notify(
                t(
                    "chapter_list.notify.marked_read",
                    default="Marked {count} chapter(s) as read",
                    count=len(selected),
                ),
                severity="information",
            )

        def _action_mark_unread_selected(self) -> None:
            """Action : marquer les chapitres sélectionnés comme non lus."""
            selected = self.get_selected_chapters()
            if not selected:
                self.notify(
                    t("chapter_list.notify.no_selection", default="No chapters selected"),
                    severity="warning",
                )
                return

            for chapter in selected:
                chapter.is_read = False
                self.post_message(ChapterAction(chapter.id, "mark_unread"))

                if self._on_action is not None:
                    try:
                        self._on_action(chapter.id, "mark_unread")
                    except Exception as e:
                        logger.error("Erreur dans le callback on_action: {}", e)

            self._render_chapters()

            self.notify(
                t(
                    "chapter_list.notify.marked_unread",
                    default="Marked {count} chapter(s) as unread",
                    count=len(selected),
                ),
                severity="information",
            )

        # =====================================================================
        # API PUBLIQUE — Accès aux données
        # =====================================================================

        @property
        def state(self) -> ChapterListState:
            """État actuel de la liste."""
            return self._state

        @property
        def config(self) -> ChapterListConfig:
            """Configuration de la liste."""
            return self._config

        @property
        def chapters(self) -> list[Chapter]:
            """Tous les chapitres."""
            return list(self._state.all_chapters)

        @property
        def filtered_chapters(self) -> list[Chapter]:
            """Chapitres filtrés."""
            return list(self._state.filtered_chapters)


# ============================================================================
# HELPERS PUBLICS
# ============================================================================


def create_chapter_list(
    chapters: list[Chapter] | None = None,
    *,
    mode: ChapterListMode = ChapterListMode.NORMAL,
    selectable: bool = True,
    title: str = "",
    on_chapter_activated: Callable[[str], None] | None = None,
    **kwargs: Any,
) -> Any:
    """Crée une liste de chapitres avec une configuration simplifiée.

    Fonction utilitaire pour créer rapidement une ChapterList.

    Args:
        chapters: Liste de chapitres.
        mode: Mode d'affichage.
        selectable: Permettre la sélection.
        title: Titre de la liste.
        on_chapter_activated: Callback lors de l'activation.
        **kwargs: Arguments additionnels.

    Returns:
        Instance de ChapterList.
    """
    config = ChapterListConfig(
        mode=mode,
        selectable=selectable,
        **kwargs,
    )
    return ChapterList(
        chapters=chapters,
        config=config,
        title=title,
        on_chapter_activated=on_chapter_activated,
    )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "MAX_CHAPTER_TITLE_LENGTH",
    "MAX_CHAPTER_SCANLATOR_LENGTH",
    "STATUS_ICONS",
    # Exceptions
    "ChapterListError",
    "InvalidChapterError",
    # Enums
    "ChapterListMode",
    "ChapterFilter",
    "ChapterSortBy",
    # Modèles
    "ChapterListConfig",
    "ChapterListFilters",
    "ChapterListState",
    # Helpers
    "format_chapter_number",
    "format_chapter_status",
    "format_chapter_date",
    "create_chapter_list",
    # Widget principal
    "ChapterList" if TEXTUAL_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
