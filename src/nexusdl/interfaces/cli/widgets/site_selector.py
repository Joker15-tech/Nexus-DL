"""Widget de sélection de sites pour l'interface CLI NexusDL.

Ce module fournit un widget Textual réutilisable permettant à l'utilisateur
de sélectionner un ou plusieurs sites parmi les sites supportés par NexusDL.
Il est utilisé dans plusieurs écrans :

    - SearchScreen : pour choisir le(s) site(s) où effectuer une recherche
    - DownloadScreen : pour ajouter un site spécifique à une tâche
    - SettingsScreen : pour activer/désactiver des sites
    - LibraryScreen : pour filtrer les mangas par site d'origine

**Fonctionnalités** :
    - Affichage de la liste des sites depuis le SiteRegistry
    - Sélection simple ou multiple (configurable)
    - Filtrage/recherche par nom de site
    - Groupement par langue (FR, EN, JP, etc.)
    - Indicateurs visuels (icônes, badges, tags)
    - Sélection "Tous les sites" / "Aucun site"
    - Affichage des capacités (Cloudflare, auth, etc.)
    - Statistiques en temps réel (nombre sélectionnés, total)
    - Navigation clavier complète (flèches, Espace, Enter, /, a)
    - Callbacks pour réactions aux changements
    - Traductions i18n
    - Gestion des erreurs

**Architecture** :
    SiteSelector (Widget principal)
        ├── SiteSelectorHeader (titre + stats)
        ├── SiteSearchBar (recherche)
        ├── SiteGroupsList (liste groupée)
        │   ├── SiteGroupHeader (en-tête de groupe)
        │   └── SiteItem (item individuel)
        └── SiteSelectorFooter (actions globales)

**Modes de sélection** :
    - SINGLE : un seul site à la fois (radio-like)
    - MULTIPLE : plusieurs sites (checkbox-like)
    - NONE : pas de sélection (affichage seul)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.cli.widgets.site_selector import (
    ...     SiteSelector, SelectionMode,
    ... )
    >>>
    >>> # Dans un écran Textual
    >>> selector = SiteSelector(
    ...     mode=SelectionMode.MULTIPLE,
    ...     on_selection_changed=my_callback,
    ... )
    >>> self.mount(selector)
    >>>
    >>> # Récupérer la sélection
    >>> selected = selector.get_selected_sites()
    >>> print([s.id for s in selected])
    ['mangadex', 'asurascans']

Intégration :
    - core/registry/site_registry.py : accès aux sites
    - core/models/site.py : modèles SiteConfig
    - core/events.py : émission d'événements
    - core/i18n.py : traductions
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
        Checkbox,
        Input,
        Label,
        ListItem,
        ListView,
        RadioButton,
        RadioSet,
        Static,
    )
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False

from nexusdl.core.constants import APP_NAME
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t
from nexusdl.core.models.site import SiteCapabilities, SiteConfig


# ============================================================================
# EXCEPTIONS
# ============================================================================


class SiteSelectorError(NexusDLError):
    """Exception de base pour les erreurs du sélecteur de sites."""


class RegistryNotAvailableError(SiteSelectorError):
    """Exception levée lorsque le SiteRegistry n'est pas disponible."""

    def __init__(self) -> None:
        super().__init__(
            "SiteRegistry n'est pas disponible. "
            "Assurez-vous que l'application est correctement initialisée."
        )


class SiteNotFoundError(SiteSelectorError):
    """Exception levée lorsqu'un site est introuvable.

    Attributes:
        site_id: ID du site recherché.
    """

    def __init__(self, site_id: str) -> None:
        super().__init__(f"Site introuvable: {site_id}")
        self.site_id = site_id


class SelectionLimitError(SiteSelectorError):
    """Exception levée lorsque la limite de sélection est atteinte.

    Attributes:
        limit: Limite maximale.
        current: Nombre actuel de sélections.
    """

    def __init__(self, limit: int, current: int) -> None:
        super().__init__(
            f"Limite de sélection atteinte: {current}/{limit}"
        )
        self.limit = limit
        self.current = current


# ============================================================================
# ENUMS
# ============================================================================


class SelectionMode(str, Enum):
    """Mode de sélection du widget.

    Attributes:
        SINGLE: Un seul site à la fois (radio-like).
        MULTIPLE: Plusieurs sites (checkbox-like).
        NONE: Pas de sélection (affichage seul).
    """

    SINGLE = "single"
    MULTIPLE = "multiple"
    NONE = "none"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            SelectionMode.SINGLE: t("selector.mode.single", default="Single Selection"),
            SelectionMode.MULTIPLE: t("selector.mode.multiple", default="Multiple Selection"),
            SelectionMode.NONE: t("selector.mode.none", default="Display Only"),
        }[self]

    @property
    def allows_multiple(self) -> bool:
        """Indique si le mode permet la sélection multiple."""
        return self == SelectionMode.MULTIPLE


class SiteGroupBy(str, Enum):
    """Critère de groupement des sites.

    Attributes:
        LANGUAGE: Groupement par langue.
        CAPABILITY: Groupement par capacité (avec/sans Cloudflare).
        STATUS: Groupement par statut (activé/désactivé).
        NONE: Pas de groupement.
    """

    LANGUAGE = "language"
    CAPABILITY = "capability"
    STATUS = "status"
    NONE = "none"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            SiteGroupBy.LANGUAGE: t("selector.group.language", default="By Language"),
            SiteGroupBy.CAPABILITY: t("selector.group.capability", default="By Capability"),
            SiteGroupBy.STATUS: t("selector.group.status", default="By Status"),
            SiteGroupBy.NONE: t("selector.group.none", default="No Grouping"),
        }[self]


class SiteSortBy(str, Enum):
    """Critère de tri des sites.

    Attributes:
        NAME: Tri alphabétique par nom.
        LANGUAGE: Tri par langue puis nom.
        PRIORITY: Tri par priorité.
        POPULARITY: Tri par popularité (nombre de mangas).
    """

    NAME = "name"
    LANGUAGE = "language"
    PRIORITY = "priority"
    POPULARITY = "popularity"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            SiteSortBy.NAME: t("selector.sort.name", default="Name"),
            SiteSortBy.LANGUAGE: t("selector.sort.language", default="Language"),
            SiteSortBy.PRIORITY: t("selector.sort.priority", default="Priority"),
            SiteSortBy.POPULARITY: t("selector.sort.popularity", default="Popularity"),
        }[self]


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class SiteSelectorConfig(BaseModel):
    """Configuration du sélecteur de sites.

    Attributes:
        mode: Mode de sélection.
        group_by: Critère de groupement.
        sort_by: Critère de tri.
        show_disabled: Afficher les sites désactivés.
        show_adult: Afficher les sites adultes.
        max_selections: Nombre maximum de sélections (0 = illimité).
        default_selection: IDs des sites sélectionnés par défaut.
        show_capabilities: Afficher les badges de capacités.
        show_search: Afficher la barre de recherche.
        show_stats: Afficher les statistiques.
        compact_mode: Mode compact (moins d'informations).
        placeholder: Texte placeholder pour la recherche.
    """

    mode: SelectionMode = Field(
        default=SelectionMode.MULTIPLE,
        description="Mode de sélection.",
    )
    group_by: SiteGroupBy = Field(
        default=SiteGroupBy.LANGUAGE,
        description="Critère de groupement.",
    )
    sort_by: SiteSortBy = Field(
        default=SiteSortBy.NAME,
        description="Critère de tri.",
    )
    show_disabled: bool = Field(
        default=False,
        description="Afficher les sites désactivés.",
    )
    show_adult: bool = Field(
        default=False,
        description="Afficher les sites adultes.",
    )
    max_selections: int = Field(
        default=0,
        ge=0,
        description="Nombre maximum de sélections (0 = illimité).",
    )
    default_selection: list[str] = Field(
        default_factory=list,
        description="IDs des sites sélectionnés par défaut.",
    )
    show_capabilities: bool = Field(
        default=True,
        description="Afficher les badges de capacités.",
    )
    show_search: bool = Field(
        default=True,
        description="Afficher la barre de recherche.",
    )
    show_stats: bool = Field(
        default=True,
        description="Afficher les statistiques.",
    )
    compact_mode: bool = Field(
        default=False,
        description="Mode compact.",
    )
    placeholder: str = Field(
        default="",
        description="Texte placeholder pour la recherche.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class SiteSelectorState(BaseModel):
    """État du sélecteur de sites.

    Attributes:
        all_sites: Liste de tous les sites chargés.
        filtered_sites: Liste des sites après filtrage.
        selected_ids: IDs des sites sélectionnés.
        search_query: Texte de recherche.
        loading: Indique si en cours de chargement.
        error: Message d'erreur.
        last_updated: Timestamp de la dernière mise à jour.
    """

    all_sites: list[SiteConfig] = Field(default_factory=list, description="Tous les sites.")
    filtered_sites: list[SiteConfig] = Field(default_factory=list, description="Sites filtrés.")
    selected_ids: set[str] = Field(default_factory=set, description="Sélections.")
    search_query: str = Field(default="", description="Recherche.")
    loading: bool = Field(default=False, description="Chargement.")
    error: str | None = Field(default=None, description="Erreur.")
    last_updated: datetime | None = Field(default=None, description="Dernière MAJ.")

    model_config = ConfigDict(extra="forbid")

    @property
    def selected_count(self) -> int:
        """Nombre de sites sélectionnés."""
        return len(self.selected_ids)

    @property
    def total_count(self) -> int:
        """Nombre total de sites."""
        return len(self.all_sites)

    @property
    def filtered_count(self) -> int:
        """Nombre de sites après filtrage."""
        return len(self.filtered_sites)

    def is_selected(self, site_id: str) -> bool:
        """Vérifie si un site est sélectionné.

        Args:
            site_id: ID du site.

        Returns:
            True si sélectionné.
        """
        return site_id in self.selected_ids


# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================


def get_site_language_label(language: str) -> str:
    """Retourne le libellé d'une langue avec drapeau.

    Args:
        language: Code langue (ISO 639-1).

    Returns:
        Libellé avec drapeau.
    """
    language_info: dict[str, tuple[str, str]] = {
        "fr": ("🇫🇷", "Français"),
        "en": ("🇬🇧", "English"),
        "de": ("🇩🇪", "Deutsch"),
        "es": ("🇪🇸", "Español"),
        "it": ("🇮🇹", "Italiano"),
        "pt": ("🇵🇹", "Português"),
        "ru": ("🇷🇺", "Русский"),
        "ja": ("🇯🇵", "日本語"),
        "ko": ("🇰🇷", "한국어"),
        "zh": ("🇨🇳", "中文"),
        "ar": ("🇸🇦", "العربية"),
        "pl": ("🇵🇱", "Polski"),
        "tr": ("🇹🇷", "Türkçe"),
        "nl": ("🇳🇱", "Nederlands"),
        "multi": ("🌍", "Multilingual"),
    }

    flag, label = language_info.get(language, ("🏳️", language.upper()))
    return f"{flag} {label}"


def get_site_capabilities_badges(site: SiteConfig) -> str:
    """Retourne les badges de capacités d'un site.

    Args:
        site: Configuration du site.

    Returns:
        Chaîne de badges.
    """
    badges: list[str] = []

    if site.capabilities.requires_cloudflare_bypass:
        badges.append("🛡️")
    if site.capabilities.requires_auth:
        badges.append("🔐")
    if site.capabilities.requires_javascript_rendering:
        badges.append("⚡")
    if site.adult:
        badges.append("🔞")

    return " ".join(badges) if badges else ""


def get_site_status_icon(site: SiteConfig) -> str:
    """Retourne l'icône de statut d'un site.

    Args:
        site: Configuration du site.

    Returns:
        Icône Unicode.
    """
    if not site.enabled:
        return "⏸️"
    if site.capabilities.requires_cloudflare_bypass:
        return "🛡️"
    return "✅"


# ============================================================================
# WIDGETS CUSTOM — Composants du sélecteur
# ============================================================================


if TEXTUAL_AVAILABLE:

    class SiteItem(ListItem):
        """Item individuel représentant un site.

        Affiche le nom, la langue, les capacités, et l'état de sélection.
        """

        DEFAULT_CSS = """
        SiteItem {
            padding: 0 1;
            height: auto;
            min-height: 1;
        }
        SiteItem > .site-content {
            layout: horizontal;
            width: 1fr;
        }
        SiteItem > .site-checkbox {
            width: 3;
        }
        SiteItem > .site-name {
            width: 1fr;
        }
        SiteItem > .site-badges {
            width: auto;
            color: $text-muted;
        }
        SiteItem.selected {
            background: $primary-background 30%;
        }
        SiteItem.disabled {
            opacity: 0.5;
        }
        SiteItem > .site-language {
            width: auto;
            color: $text-muted;
            padding: 0 1;
        }
        """

        def __init__(
            self,
            site: SiteConfig,
            *,
            selected: bool = False,
            show_checkbox: bool = True,
            compact: bool = False,
        ) -> None:
            """Initialise l'item.

            Args:
                site: Configuration du site.
                selected: Indique si le site est sélectionné.
                show_checkbox: Afficher la checkbox/radio.
                compact: Mode compact.
            """
            super().__init__()
            self.site = site
            self.selected = selected
            self.show_checkbox = show_checkbox
            self.compact = compact

            if selected:
                self.add_class("selected")
            if not site.enabled:
                self.add_class("disabled")

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            with Horizontal(classes="site-content"):
                # Checkbox/radio
                if self.show_checkbox:
                    checkbox_symbol = "✓" if self.selected else "○"
                    yield Static(checkbox_symbol, classes="site-checkbox")

                # Nom du site
                name = self.site.name
                if not self.site.enabled:
                    name = f"[dim]{name} (disabled)[/]"
                yield Static(name, classes="site-name", markup=True)

                # Langue (si non compact)
                if not self.compact:
                    lang_label = get_site_language_label(self.site.language.value)
                    yield Static(lang_label, classes="site-language")

                # Badges de capacités
                if not self.compact:
                    badges = get_site_capabilities_badges(self.site)
                    if badges:
                        yield Static(badges, classes="site-badges")

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

    class SiteGroupHeader(Widget):
        """En-tête de groupe de sites."""

        DEFAULT_CSS = """
        SiteGroupHeader {
            padding: 0 1;
            height: 1;
            background: $primary-background;
            text-style: bold;
        }
        SiteGroupHeader > .group-icon {
            width: 3;
        }
        SiteGroupHeader > .group-name {
            width: 1fr;
        }
        SiteGroupHeader > .group-count {
            width: auto;
            color: $text-muted;
        }
        """

        def __init__(
            self,
            group_name: str,
            group_icon: str,
            count: int,
        ) -> None:
            """Initialise l'en-tête.

            Args:
                group_name: Nom du groupe.
                group_icon: Icône du groupe.
                count: Nombre de sites dans le groupe.
            """
            super().__init__()
            self.group_name = group_name
            self.group_icon = group_icon
            self.count = count

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(self.group_icon, classes="group-icon")
            yield Static(self.group_name, classes="group-name")
            yield Static(f"({self.count})", classes="group-count")

    class SiteSelectorHeader(Widget):
        """En-tête du sélecteur avec titre et statistiques."""

        DEFAULT_CSS = """
        SiteSelectorHeader {
            layout: horizontal;
            height: 1;
            padding: 0 1;
            background: $surface;
        }
        SiteSelectorHeader > .title {
            width: 1fr;
            text-style: bold;
        }
        SiteSelectorHeader > .stats {
            width: auto;
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
            """Initialise l'en-tête.

            Args:
                title: Titre du sélecteur.
                name: Nom du widget.
                id: ID du widget.
            """
            super().__init__(name=name, id=id)
            self.title = title
            self.selected_count = 0
            self.total_count = 0

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(self.title, classes="title")
            yield Static("", id="selector-stats", classes="stats")

        def update_stats(self, selected: int, total: int) -> None:
            """Met à jour les statistiques.

            Args:
                selected: Nombre de sites sélectionnés.
                total: Nombre total de sites.
            """
            self.selected_count = selected
            self.total_count = total

            stats_widget = self.query_one("#selector-stats", Static)
            stats_widget.update(f"{selected}/{total} selected")

    class SiteSearchBar(Widget):
        """Barre de recherche pour filtrer les sites."""

        DEFAULT_CSS = """
        SiteSearchBar {
            layout: horizontal;
            height: 3;
            padding: 0 1;
        }
        SiteSearchBar > Label {
            width: auto;
            content-align: left middle;
            padding: 0 1;
        }
        SiteSearchBar > Input {
            width: 1fr;
        }
        SiteSearchBar > .clear-button {
            width: auto;
        }
        """

        def __init__(
            self,
            placeholder: str = "",
            *,
            name: str | None = None,
            id: str | None = None,
        ) -> None:
            """Initialise la barre de recherche.

            Args:
                placeholder: Texte placeholder.
                name: Nom du widget.
                id: ID du widget.
            """
            super().__init__(name=name, id=id)
            self.placeholder = placeholder

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Label("🔍")
            yield Input(
                placeholder=self.placeholder or t("selector.search.placeholder", default="Search sites..."),
                id="site-search-input",
            )
            yield Button("✕", id="clear-search", classes="clear-button", variant="default")

        def on_input_changed(self, event: Input.Changed) -> None:
            """Gère le changement de texte."""
            if event.input.id == "site-search-input":
                self.post_message(SearchQueryChanged(event.value))

        def on_button_pressed(self, event: Button.Pressed) -> None:
            """Gère le clic sur le bouton clear."""
            if event.button.id == "clear-search":
                input_widget = self.query_one("#site-search-input", Input)
                input_widget.value = ""
                self.post_message(SearchQueryChanged(""))


# ============================================================================
# MESSAGES — Événements Textual
# ============================================================================


if TEXTUAL_AVAILABLE:

    class SiteSelectionChanged(Message):
        """Message émis lorsque la sélection change."""

        def __init__(self, selected_ids: set[str]) -> None:
            """Initialise le message.

            Args:
                selected_ids: IDs des sites sélectionnés.
            """
            super().__init__()
            self.selected_ids = selected_ids

    class SiteActivated(Message):
        """Message émis lorsqu'un site est activé (Enter)."""

        def __init__(self, site_id: str) -> None:
            """Initialise le message.

            Args:
                site_id: ID du site activé.
            """
            super().__init__()
            self.site_id = site_id

    class SearchQueryChanged(Message):
        """Message émis lorsque la recherche change."""

        def __init__(self, query: str) -> None:
            """Initialise le message.

            Args:
                query: Texte de recherche.
            """
            super().__init__()
            self.query = query

    class SitesLoaded(Message):
        """Message émis lorsque les sites sont chargés."""

        def __init__(self, count: int) -> None:
            """Initialise le message.

            Args:
                count: Nombre de sites chargés.
            """
            super().__init__()
            self.count = count


# ============================================================================
# CLASSE PRINCIPALE — SiteSelector
# ============================================================================


if TEXTUAL_AVAILABLE:

    class SiteSelector(Widget):
        """Widget de sélection de sites.

        Widget réutilisable permettant de sélectionner un ou plusieurs sites
        parmi les sites supportés par NexusDL.
        """

        # Bindings clavier
        BINDINGS = [
            Binding("a", "select_all", "Select All"),
            Binding("n", "select_none", "Select None"),
            Binding("i", "invert_selection", "Invert"),
            Binding("/", "focus_search", "Search"),
            Binding("enter", "activate_site", "Activate"),
        ]

        # CSS du widget
        DEFAULT_CSS = """
        SiteSelector {
            height: auto;
            max-height: 1fr;
            layout: vertical;
            border: solid $primary;
        }
        SiteSelector > #selector-header {
            height: auto;
        }
        SiteSelector > #selector-search {
            height: auto;
        }
        SiteSelector > #selector-list-container {
            height: 1fr;
        }
        SiteSelector > #selector-list-container > ListView {
            height: 1fr;
        }
        SiteSelector > #selector-empty {
            text-align: center;
            padding: 2;
            color: $text-muted;
        }
        SiteSelector > #selector-footer {
            height: 3;
            layout: horizontal;
            padding: 0 1;
            align: right middle;
        }
        SiteSelector > #selector-footer > Button {
            margin: 0 1;
        }
        .group-separator {
            height: 1;
            background: $primary-background;
        }
        """

        # État réactif
        search_query: reactive[str] = reactive("")

        def __init__(
            self,
            *,
            config: SiteSelectorConfig | None = None,
            title: str = "",
            on_selection_changed: Callable[[set[str]], None] | None = None,
            on_site_activated: Callable[[str], None] | None = None,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise le sélecteur.

            Args:
                config: Configuration du sélecteur.
                title: Titre du sélecteur.
                on_selection_changed: Callback lors du changement de sélection.
                on_site_activated: Callback lors de l'activation d'un site.
                name: Nom du widget.
                id: ID du widget.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self._config = config or SiteSelectorConfig()
            self._title = title or t("selector.title", default="Select Sites")
            self._on_selection_changed = on_selection_changed
            self._on_site_activated = on_site_activated
            self._state = SiteSelectorState()

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            # En-tête
            if self._config.show_stats:
                yield SiteSelectorHeader(self._title, id="selector-header")

            # Barre de recherche
            if self._config.show_search:
                yield SiteSearchBar(
                    placeholder=self._config.placeholder,
                    id="selector-search",
                )

            # Liste des sites
            yield Static(
                t("selector.loading", default="Loading sites..."),
                id="selector-empty",
            )
            with Vertical(id="selector-list-container"):
                yield ListView(id="sites-list")

            # Footer avec actions
            if self._config.mode == SelectionMode.MULTIPLE:
                with Horizontal(id="selector-footer"):
                    yield Button(
                        t("selector.button.all", default="All"),
                        id="btn-select-all",
                        variant="default",
                    )
                    yield Button(
                        t("selector.button.none", default="None"),
                        id="btn-select-none",
                        variant="default",
                    )
                    yield Button(
                        t("selector.button.invert", default="Invert"),
                        id="btn-invert",
                        variant="default",
                    )

        async def on_mount(self) -> None:
            """Appelé lors du montage."""
            # Charger les sites
            await self._load_sites()

            # Appliquer la sélection par défaut
            if self._config.default_selection:
                self.set_selection(set(self._config.default_selection))

        # =====================================================================
        # CHARGEMENT DES SITES
        # =====================================================================

        async def _load_sites(self) -> None:
            """Charge les sites depuis le registre."""
            self._state.loading = True

            try:
                # Obtenir le registre
                from nexusdl.core.registry import get_site_registry
                registry = get_site_registry()

                # Charger tous les sites
                all_sites = registry.list_sites(
                    enabled_only=not self._config.show_disabled,
                    include_adult=self._config.show_adult,
                )

                self._state.all_sites = all_sites

                # Appliquer les filtres initiaux
                await self._apply_filters()

                # Mettre à jour l'UI
                self._render_sites()

                self._state.loading = False
                self._state.last_updated = datetime.now(UTC)

                # Émettre un message
                self.post_message(SitesLoaded(len(all_sites)))

                logger.debug("Sites chargés: {} sites", len(all_sites))

            except Exception as e:
                logger.error("Erreur lors du chargement des sites: {}", e)
                self._state.loading = False
                self._state.error = str(e)

                empty_msg = self.query_one("#selector-empty", Static)
                empty_msg.update(
                    t("selector.error", default="Error loading sites: {error}", error=str(e))
                )
                empty_msg.display = True

        # =====================================================================
        # FILTRAGE ET TRI
        # =====================================================================

        async def _apply_filters(self) -> None:
            """Applique les filtres et le tri aux sites."""
            filtered = list(self._state.all_sites)

            # Filtrer par recherche
            if self.search_query:
                query_lower = self.search_query.lower()
                filtered = [
                    s for s in filtered
                    if query_lower in s.name.lower()
                    or query_lower in s.language.value.lower()
                    or any(query_lower in tag.lower() for tag in s.tags)
                ]

            # Trier
            if self._config.sort_by == SiteSortBy.NAME:
                filtered.sort(key=lambda s: s.name.lower())
            elif self._config.sort_by == SiteSortBy.LANGUAGE:
                filtered.sort(key=lambda s: (s.language.value, s.name.lower()))
            elif self._config.sort_by == SiteSortBy.PRIORITY:
                filtered.sort(key=lambda s: s.priority)
            elif self._config.sort_by == SiteSortBy.POPULARITY:
                # TODO: Implémenter le tri par popularité
                filtered.sort(key=lambda s: s.name.lower())

            self._state.filtered_sites = filtered

        def _render_sites(self) -> None:
            """Rend la liste des sites dans l'UI."""
            list_view = self.query_one("#sites-list", ListView)
            empty_msg = self.query_one("#selector-empty", Static)
            list_view.clear()

            if not self._state.filtered_sites:
                empty_msg.update(
                    t("selector.no_sites", default="No sites match your criteria")
                )
                empty_msg.display = True
                list_view.display = False
                return

            empty_msg.display = False
            list_view.display = True

            # Grouper les sites si nécessaire
            if self._config.group_by != SiteGroupBy.NONE:
                self._render_grouped_sites(list_view)
            else:
                self._render_flat_sites(list_view)

            # Mettre à jour les statistiques
            if self._config.show_stats:
                header = self.query_one("#selector-header", SiteSelectorHeader)
                header.update_stats(
                    selected=self._state.selected_count,
                    total=len(self._state.filtered_sites),
                )

        def _render_flat_sites(self, list_view: ListView) -> None:
            """Rend les sites sans groupement."""
            for site in self._state.filtered_sites:
                item = SiteItem(
                    site,
                    selected=self._state.is_selected(site.id),
                    show_checkbox=self._config.mode != SelectionMode.NONE,
                    compact=self._config.compact_mode,
                )
                list_view.append(item)

        def _render_grouped_sites(self, list_view: ListView) -> None:
            """Rend les sites groupés."""
            groups: dict[str, list[SiteConfig]] = {}

            for site in self._state.filtered_sites:
                group_key = self._get_group_key(site)
                if group_key not in groups:
                    groups[group_key] = []
                groups[group_key].append(site)

            # Trier les groupes
            sorted_groups = sorted(groups.items(), key=lambda x: x[0])

            for group_key, sites in sorted_groups:
                # Ajouter l'en-tête de groupe
                group_label, group_icon = self._get_group_label(group_key)
                header = SiteGroupHeader(group_label, group_icon, len(sites))
                list_view.append(header)

                # Ajouter les sites du groupe
                for site in sites:
                    item = SiteItem(
                        site,
                        selected=self._state.is_selected(site.id),
                        show_checkbox=self._config.mode != SelectionMode.NONE,
                        compact=self._config.compact_mode,
                    )
                    list_view.append(item)

        def _get_group_key(self, site: SiteConfig) -> str:
            """Retourne la clé de groupement pour un site.

            Args:
                site: Configuration du site.

            Returns:
                Clé de groupement.
            """
            if self._config.group_by == SiteGroupBy.LANGUAGE:
                return site.language.value
            if self._config.group_by == SiteGroupBy.CAPABILITY:
                if site.capabilities.requires_cloudflare_bypass:
                    return "cloudflare"
                if site.capabilities.requires_auth:
                    return "auth"
                return "standard"
            if self._config.group_by == SiteGroupBy.STATUS:
                return "enabled" if site.enabled else "disabled"
            return "all"

        def _get_group_label(self, group_key: str) -> tuple[str, str]:
            """Retourne le libellé et l'icône d'un groupe.

            Args:
                group_key: Clé du groupe.

            Returns:
                Tuple (libellé, icône).
            """
            if self._config.group_by == SiteGroupBy.LANGUAGE:
                label = get_site_language_label(group_key)
                # Extraire l'icône (drapeau)
                icon = label.split(" ")[0] if " " in label else "🌐"
                return label, icon

            if self._config.group_by == SiteGroupBy.CAPABILITY:
                capability_labels = {
                    "cloudflare": ("🛡️ Cloudflare Protected", "🛡️"),
                    "auth": ("🔐 Authentication Required", "🔐"),
                    "standard": ("✅ Standard Access", "✅"),
                }
                return capability_labels.get(group_key, (group_key, "📦"))

            if self._config.group_by == SiteGroupBy.STATUS:
                status_labels = {
                    "enabled": ("✅ Enabled Sites", "✅"),
                    "disabled": ("⏸️ Disabled Sites", "⏸️"),
                }
                return status_labels.get(group_key, (group_key, "📦"))

            return (group_key, "📦")

        # =====================================================================
        # GESTION DE LA SÉLECTION
        # =====================================================================

        def toggle_site(self, site_id: str) -> None:
            """Bascule la sélection d'un site.

            Args:
                site_id: ID du site.
            """
            if self._config.mode == SelectionMode.NONE:
                return

            if self._config.mode == SelectionMode.SINGLE:
                # Mode simple : désélectionner tous les autres
                self._state.selected_ids.clear()
                self._state.selected_ids.add(site_id)
            else:
                # Mode multiple : toggle
                if site_id in self._state.selected_ids:
                    self._state.selected_ids.remove(site_id)
                else:
                    # Vérifier la limite
                    if (
                        self._config.max_selections > 0
                        and len(self._state.selected_ids) >= self._config.max_selections
                    ):
                        self.notify(
                            t(
                                "selector.notify.limit_reached",
                                default="Selection limit reached: {limit}",
                                limit=self._config.max_selections,
                            ),
                            severity="warning",
                        )
                        return
                    self._state.selected_ids.add(site_id)

            # Mettre à jour l'UI
            self._update_selection_ui()
            self._notify_selection_changed()

        def set_selection(self, site_ids: set[str]) -> None:
            """Définit la sélection complète.

            Args:
                site_ids: IDs des sites à sélectionner.
            """
            if self._config.mode == SelectionMode.NONE:
                return

            if self._config.mode == SelectionMode.SINGLE:
                # Mode simple : garder uniquement le premier
                if site_ids:
                    first_id = next(iter(site_ids))
                    self._state.selected_ids = {first_id}
                else:
                    self._state.selected_ids.clear()
            else:
                # Mode multiple : vérifier la limite
                if self._config.max_selections > 0 and len(site_ids) > self._config.max_selections:
                    raise SelectionLimitError(self._config.max_selections, len(site_ids))
                self._state.selected_ids = set(site_ids)

            # Mettre à jour l'UI
            self._update_selection_ui()
            self._notify_selection_changed()

        def select_all(self) -> None:
            """Sélectionne tous les sites filtrés."""
            if self._config.mode != SelectionMode.MULTIPLE:
                return

            site_ids = {s.id for s in self._state.filtered_sites}

            if self._config.max_selections > 0 and len(site_ids) > self._config.max_selections:
                self.notify(
                    t(
                        "selector.notify.limit_reached",
                        default="Selection limit reached: {limit}",
                        limit=self._config.max_selections,
                    ),
                    severity="warning",
                )
                return

            self._state.selected_ids = site_ids
            self._update_selection_ui()
            self._notify_selection_changed()

        def select_none(self) -> None:
            """Désélectionne tous les sites."""
            if self._config.mode == SelectionMode.NONE:
                return

            self._state.selected_ids.clear()
            self._update_selection_ui()
            self._notify_selection_changed()

        def invert_selection(self) -> None:
            """Inverse la sélection."""
            if self._config.mode != SelectionMode.MULTIPLE:
                return

            all_ids = {s.id for s in self._state.filtered_sites}
            new_selection = all_ids - self._state.selected_ids

            if self._config.max_selections > 0 and len(new_selection) > self._config.max_selections:
                self.notify(
                    t(
                        "selector.notify.limit_reached",
                        default="Selection limit reached: {limit}",
                        limit=self._config.max_selections,
                    ),
                    severity="warning",
                )
                return

            self._state.selected_ids = new_selection
            self._update_selection_ui()
            self._notify_selection_changed()

        def _update_selection_ui(self) -> None:
            """Met à jour l'UI pour refléter la sélection."""
            list_view = self.query_one("#sites-list", ListView)

            for child in list_view.children:
                if isinstance(child, SiteItem):
                    child.set_selected(self._state.is_selected(child.site.id))

            # Mettre à jour les statistiques
            if self._config.show_stats:
                try:
                    header = self.query_one("#selector-header", SiteSelectorHeader)
                    header.update_stats(
                        selected=self._state.selected_count,
                        total=len(self._state.filtered_sites),
                    )
                except Exception:
                    pass

        def _notify_selection_changed(self) -> None:
            """Notifie le changement de sélection."""
            # Message Textual
            self.post_message(SiteSelectionChanged(set(self._state.selected_ids)))

            # Callback
            if self._on_selection_changed is not None:
                try:
                    self._on_selection_changed(set(self._state.selected_ids))
                except Exception as e:
                    logger.error("Erreur dans le callback de sélection: {}", e)

            # Événement EventBus
            try:
                event_bus = get_event_bus()
                asyncio.create_task(
                    event_bus.emit(
                        EventType.CUSTOM,
                        payload={
                            "type": "site_selector.selection_changed",
                            "selected_ids": list(self._state.selected_ids),
                            "count": len(self._state.selected_ids),
                        },
                        source="interfaces.cli.widgets.site_selector",
                    )
                )
            except Exception as e:
                logger.debug("Impossible d'émettre l'événement: {}", e)

        # =====================================================================
        # API PUBLIQUE — Accès aux données
        # =====================================================================

        def get_selected_sites(self) -> list[SiteConfig]:
            """Retourne les sites sélectionnés.

            Returns:
                Liste de SiteConfig sélectionnés.
            """
            return [
                site for site in self._state.all_sites
                if site.id in self._state.selected_ids
            ]

        def get_selected_ids(self) -> set[str]:
            """Retourne les IDs des sites sélectionnés.

            Returns:
                Set d'IDs.
            """
            return set(self._state.selected_ids)

        def get_all_sites(self) -> list[SiteConfig]:
            """Retourne tous les sites chargés.

            Returns:
                Liste de SiteConfig.
            """
            return list(self._state.all_sites)

        def get_site(self, site_id: str) -> SiteConfig | None:
            """Retourne un site par son ID.

            Args:
                site_id: ID du site.

            Returns:
                SiteConfig ou None.
            """
            for site in self._state.all_sites:
                if site.id == site_id:
                    return site
            return None

        def is_selected(self, site_id: str) -> bool:
            """Vérifie si un site est sélectionné.

            Args:
                site_id: ID du site.

            Returns:
                True si sélectionné.
            """
            return self._state.is_selected(site_id)

        @property
        def state(self) -> SiteSelectorState:
            """État actuel du sélecteur."""
            return self._state

        @property
        def config(self) -> SiteSelectorConfig:
            """Configuration du sélecteur."""
            return self._config

        # =====================================================================
        # GESTION DES ÉVÉNEMENTS
        # =====================================================================

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            """Gère la sélection d'un item dans la liste."""
            if isinstance(event.item, SiteItem):
                if self._config.mode != SelectionMode.NONE:
                    self.toggle_site(event.item.site.id)

        def on_search_query_changed(self, event: SearchQueryChanged) -> None:
            """Gère le changement de recherche."""
            self.search_query = event.query
            asyncio.create_task(self._apply_filters_and_render())

        async def _apply_filters_and_render(self) -> None:
            """Applique les filtres et rafraîchit l'UI."""
            await self._apply_filters()
            self._render_sites()

        def on_button_pressed(self, event: Button.Pressed) -> None:
            """Gère les clics sur les boutons."""
            if event.button.id == "btn-select-all":
                self.action_select_all()
            elif event.button.id == "btn-select-none":
                self.action_select_none()
            elif event.button.id == "btn-invert":
                self.action_invert_selection()

        # =====================================================================
        # ACTIONS — Bindings clavier
        # =====================================================================

        def action_select_all(self) -> None:
            """Action : sélectionner tous les sites."""
            self.select_all()

        def action_select_none(self) -> None:
            """Action : désélectionner tous les sites."""
            self.select_none()

        def action_invert_selection(self) -> None:
            """Action : inverser la sélection."""
            self.invert_selection()

        def action_focus_search(self) -> None:
            """Action : focus sur la barre de recherche."""
            if self._config.show_search:
                search_input = self.query_one("#site-search-input", Input)
                search_input.focus()

        def action_activate_site(self) -> None:
            """Action : activer le site sélectionné."""
            list_view = self.query_one("#sites-list", ListView)
            if list_view.index is not None:
                # Trouver le SiteItem à cet index
                items = [child for child in list_view.children if isinstance(child, SiteItem)]
                if 0 <= list_view.index < len(items):
                    site = items[list_view.index].site
                    self.post_message(SiteActivated(site.id))

                    if self._on_site_activated is not None:
                        try:
                            self._on_site_activated(site.id)
                        except Exception as e:
                            logger.error("Erreur dans le callback d'activation: {}", e)

        # =====================================================================
        # RECHARGEMENT
        # =====================================================================

        async def reload(self) -> None:
            """Recharge les sites depuis le registre."""
            await self._load_sites()

        def refresh_display(self) -> None:
            """Rafraîchit l'affichage sans recharger les données."""
            self._render_sites()


# ============================================================================
# HELPERS PUBLICS
# ============================================================================


def create_site_selector(
    *,
    mode: SelectionMode = SelectionMode.MULTIPLE,
    title: str = "",
    on_selection_changed: Callable[[set[str]], None] | None = None,
    **kwargs: Any,
) -> Any:
    """Crée un sélecteur de sites avec une configuration simplifiée.

    Fonction utilitaire pour créer rapidement un SiteSelector.

    Args:
        mode: Mode de sélection.
        title: Titre du sélecteur.
        on_selection_changed: Callback lors du changement.
        **kwargs: Arguments additionnels pour SiteSelectorConfig.

    Returns:
        Instance de SiteSelector.
    """
    config = SiteSelectorConfig(mode=mode, **kwargs)
    return SiteSelector(
        config=config,
        title=title,
        on_selection_changed=on_selection_changed,
    )


async def get_selected_sites_from_registry(
    site_ids: set[str],
) -> list[SiteConfig]:
    """Récupère les configurations de sites depuis le registre.

    Fonction utilitaire pour usage externe.

    Args:
        site_ids: IDs des sites à récupérer.

    Returns:
        Liste de SiteConfig.
    """
    try:
        from nexusdl.core.registry import get_site_registry
        registry = get_site_registry()

        sites: list[SiteConfig] = []
        for site_id in site_ids:
            site = registry.get_site_or_none(site_id)
            if site is not None:
                sites.append(site)

        return sites

    except Exception as e:
        logger.error("Erreur lors de la récupération des sites: {}", e)
        return []


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "SiteSelectorError",
    "RegistryNotAvailableError",
    "SiteNotFoundError",
    "SelectionLimitError",
    # Enums
    "SelectionMode",
    "SiteGroupBy",
    "SiteSortBy",
    # Modèles
    "SiteSelectorConfig",
    "SiteSelectorState",
    # Helpers
    "get_site_language_label",
    "get_site_capabilities_badges",
    "get_site_status_icon",
    "create_site_selector",
    "get_selected_sites_from_registry",
    # Widget principal
    "SiteSelector" if TEXTUAL_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
