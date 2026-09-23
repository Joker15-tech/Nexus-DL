"""Écran d'aide de l'interface CLI NexusDL.

Ce module fournit un écran complet d'aide via une interface TUI (Terminal
User Interface) basée sur Textual. Il affiche les informations sur l'application,
les raccourcis clavier, les commandes disponibles, et les liens utiles.

**Sections de l'aide** :
    - À propos : informations sur l'application (version, auteur, licence)
    - Raccourcis globaux : touches communes à tous les écrans
    - Navigation : touches pour naviguer dans l'interface
    - Commandes : commandes disponibles dans l'écran courant
    - Liens utiles : documentation, site web, bug tracker, etc.
    - Support : informations de contact et de support
    - Crédits : remerciements et licences tierces

**Fonctionnalités** :
    - Navigation par sections (sidebar)
    - Affichage formaté des raccourcis clavier
    - Recherche dans l'aide
    - Liens cliquables (ouverture dans le navigateur)
    - Copie d'informations (version, etc.)
    - Traductions i18n
    - Affichage responsive (s'adapte à la taille du terminal)
    - Navigation clavier complète (Tab, Enter, Escape, flèches, /)

**Architecture** :
    HelpScreen (Screen Textual)
        ├── HelpSidebar (sidebar avec sections)
        │   └── HelpSectionItem (item de section)
        ├── HelpContent (contenu principal)
        │   ├── AboutSection
        │   ├── ShortcutsSection
        │   ├── NavigationSection
        │   ├── CommandsSection
        │   ├── LinksSection
        │   ├── SupportSection
        │   └── CreditsSection
        └── HelpFooter (barre de statut)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.cli.screens.help import HelpScreen
    >>>
    >>> # Dans l'application principale
    >>> app.push_screen(HelpScreen())
    >>>
    >>> # L'utilisateur peut :
    >>> # 1. Naviguer entre les sections avec Tab/Shift+Tab
    >>> # 2. Rechercher avec /
    >>> # 3. Copier des infos avec Ctrl+C
    >>> # 4. Ouvrir un lien avec Enter
    >>> # 5. Fermer avec Escape ou q

Intégration :
    - core/version.py : informations de version
    - core/constants.py : métadonnées du projet
    - core/i18n.py : traductions
    - core/paths.py : chemins pour ouvrir les fichiers
"""

from __future__ import annotations

import webbrowser
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
        Static,
    )
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False

from nexusdl.core.constants import (
    APP_AUTHOR,
    APP_BUG_TRACKER,
    APP_DOCUMENTATION,
    APP_LICENSE,
    APP_NAME,
    APP_REPOSITORY,
    APP_TAGLINE,
    APP_URL,
    APP_VERSION,
    PYTHON_MIN_VERSION,
)
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t
from nexusdl.core.paths import get_paths


# ============================================================================
# EXCEPTIONS
# ============================================================================


class HelpScreenError(NexusDLError):
    """Exception de base pour les erreurs de l'écran d'aide."""


class LinkOpenError(HelpScreenError):
    """Exception levée lorsqu'un lien ne peut être ouvert.

    Attributes:
        url: URL du lien.
        reason: Raison de l'échec.
    """

    def __init__(self, url: str, reason: str = "") -> None:
        msg = f"Impossible d'ouvrir le lien: {url}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.url = url
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class HelpSection(str, Enum):
    """Sections de l'aide.

    Attributes:
        ABOUT: Informations sur l'application.
        SHORTCUTS: Raccourcis clavier globaux.
        NAVIGATION: Touches de navigation.
        COMMANDS: Commandes disponibles.
        LINKS: Liens utiles.
        SUPPORT: Informations de support.
        CREDITS: Remerciements et licences.
    """

    ABOUT = "about"
    SHORTCUTS = "shortcuts"
    NAVIGATION = "navigation"
    COMMANDS = "commands"
    LINKS = "links"
    SUPPORT = "support"
    CREDITS = "credits"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            HelpSection.ABOUT: t("help.section.about", default="About"),
            HelpSection.SHORTCUTS: t("help.section.shortcuts", default="Keyboard Shortcuts"),
            HelpSection.NAVIGATION: t("help.section.navigation", default="Navigation"),
            HelpSection.COMMANDS: t("help.section.commands", default="Commands"),
            HelpSection.LINKS: t("help.section.links", default="Useful Links"),
            HelpSection.SUPPORT: t("help.section.support", default="Support"),
            HelpSection.CREDITS: t("help.section.credits", default="Credits"),
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            HelpSection.ABOUT: "ℹ️",
            HelpSection.SHORTCUTS: "⌨️",
            HelpSection.NAVIGATION: "🧭",
            HelpSection.COMMANDS: "💻",
            HelpSection.LINKS: "🔗",
            HelpSection.SUPPORT: "💬",
            HelpSection.CREDITS: "🙏",
        }[self]


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class ShortcutInfo(BaseModel):
    """Information sur un raccourci clavier.

    Attributes:
        keys: Touches du raccourci (ex: "Ctrl+S", "F1").
        description: Description de l'action.
        context: Contexte d'utilisation (écran ou global).
        category: Catégorie du raccourci.
    """

    keys: str = Field(..., description="Touches du raccourci.")
    description: str = Field(..., description="Description de l'action.")
    context: str = Field(default="global", description="Contexte d'utilisation.")
    category: str = Field(default="general", description="Catégorie.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class LinkInfo(BaseModel):
    """Information sur un lien.

    Attributes:
        label: Libellé du lien.
        url: URL du lien.
        description: Description du lien.
        icon: Icône Unicode.
    """

    label: str = Field(..., description="Libellé du lien.")
    url: str = Field(..., description="URL du lien.")
    description: str = Field(default="", description="Description.")
    icon: str = Field(default="🔗", description="Icône.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class HelpState(BaseModel):
    """État de l'écran d'aide.

    Attributes:
        current_section: Section actuellement affichée.
        search_query: Texte de recherche.
        search_results: Résultats de recherche.
    """

    current_section: HelpSection = Field(default=HelpSection.ABOUT, description="Section courante.")
    search_query: str = Field(default="", description="Recherche.")
    search_results: list[str] = Field(default_factory=list, description="Résultats.")

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# DONNÉES D'AIDE — Raccourcis, liens, etc.
# ============================================================================


# Raccourcis globaux (disponibles partout)
GLOBAL_SHORTCUTS: Final[list[ShortcutInfo]] = [
    ShortcutInfo(keys="F1", description=t("help.shortcut.f1", default="Show this help screen"), context="global"),
    ShortcutInfo(keys="?", description=t("help.shortcut.question", default="Show this help screen"), context="global"),
    ShortcutInfo(keys="q", description=t("help.shortcut.q", default="Quit application"), context="global"),
    ShortcutInfo(keys="Ctrl+Q", description=t("help.shortcut.ctrl_q", default="Force quit"), context="global"),
    ShortcutInfo(keys="Ctrl+C", description=t("help.shortcut.ctrl_c", default="Copy selected text"), context="global"),
    ShortcutInfo(keys="Ctrl+V", description=t("help.shortcut.ctrl_v", default="Paste from clipboard"), context="global"),
    ShortcutInfo(keys="Escape", description=t("help.shortcut.escape", default="Go back / Close screen"), context="global"),
    ShortcutInfo(keys="Tab", description=t("help.shortcut.tab", default="Next element"), context="global"),
    ShortcutInfo(keys="Shift+Tab", description=t("help.shortcut.shift_tab", default="Previous element"), context="global"),
]

# Raccourcis de navigation
NAVIGATION_SHORTCUTS: Final[list[ShortcutInfo]] = [
    ShortcutInfo(keys="↑/↓", description=t("help.shortcut.arrows", default="Navigate up/down"), context="navigation"),
    ShortcutInfo(keys="←/→", description=t("help.shortcut.left_right", default="Navigate left/right"), context="navigation"),
    ShortcutInfo(keys="Home", description=t("help.shortcut.home", default="Go to beginning"), context="navigation"),
    ShortcutInfo(keys="End", description=t("help.shortcut.end", default="Go to end"), context="navigation"),
    ShortcutInfo(keys="PageUp", description=t("help.shortcut.pageup", default="Page up"), context="navigation"),
    ShortcutInfo(keys="PageDown", description=t("help.shortcut.pagedown", default="Page down"), context="navigation"),
    ShortcutInfo(keys="Enter", description=t("help.shortcut.enter", default="Select / Open"), context="navigation"),
    ShortcutInfo(keys="Space", description=t("help.shortcut.space", default="Toggle selection"), context="navigation"),
]

# Raccourcis par écran
SCREEN_SHORTCUTS: Final[dict[str, list[ShortcutInfo]]] = {
    "main": [
        ShortcutInfo(keys="s", description=t("help.shortcut.main.search", default="Open search screen"), context="main"),
        ShortcutInfo(keys="l", description=t("help.shortcut.main.library", default="Open library screen"), context="main"),
        ShortcutInfo(keys="d", description=t("help.shortcut.main.downloads", default="Open downloads screen"), context="main"),
        ShortcutInfo(keys="p", description=t("help.shortcut.main.settings", default="Open settings screen"), context="main"),
        ShortcutInfo(keys="r", description=t("help.shortcut.main.refresh", default="Refresh dashboard"), context="main"),
    ],
    "search": [
        ShortcutInfo(keys="Ctrl+D", description=t("help.shortcut.search.download", default="Download selected"), context="search"),
        ShortcutInfo(keys="Ctrl+A", description=t("help.shortcut.search.select_all", default="Select all"), context="search"),
        ShortcutInfo(keys="Ctrl+Shift+A", description=t("help.shortcut.search.deselect_all", default="Deselect all"), context="search"),
        ShortcutInfo(keys="Space", description=t("help.shortcut.search.toggle", default="Toggle selection"), context="search"),
    ],
    "library": [
        ShortcutInfo(keys="/", description=t("help.shortcut.library.search", default="Focus search"), context="library"),
        ShortcutInfo(keys="f", description=t("help.shortcut.library.filter", default="Focus filter"), context="library"),
        ShortcutInfo(keys="d", description=t("help.shortcut.library.download", default="Download selected"), context="library"),
        ShortcutInfo(keys="r", description=t("help.shortcut.library.read", default="Read selected"), context="library"),
        ShortcutInfo(keys="x", description=t("help.shortcut.library.delete", default="Delete selected"), context="library"),
    ],
    "settings": [
        ShortcutInfo(keys="Ctrl+S", description=t("help.shortcut.settings.save", default="Save settings"), context="settings"),
        ShortcutInfo(keys="Ctrl+R", description=t("help.shortcut.settings.reset", default="Reset to defaults"), context="settings"),
    ],
    "logs": [
        ShortcutInfo(keys="p", description=t("help.shortcut.logs.pause", default="Pause/Resume logs"), context="logs"),
        ShortcutInfo(keys="c", description=t("help.shortcut.logs.clear", default="Clear logs"), context="logs"),
        ShortcutInfo(keys="e", description=t("help.shortcut.logs.export", default="Export logs"), context="logs"),
        ShortcutInfo(keys="/", description=t("help.shortcut.logs.search", default="Search in logs"), context="logs"),
        ShortcutInfo(keys="a", description=t("help.shortcut.logs.autoscroll", default="Toggle auto-scroll"), context="logs"),
    ],
}

# Liens utiles
USEFUL_LINKS: Final[list[LinkInfo]] = [
    LinkInfo(
        label=t("help.link.website", default="Official Website"),
        url=APP_URL,
        description=t("help.link.website.desc", default="Visit the official NexusDL website"),
        icon="🌐",
    ),
    LinkInfo(
        label=t("help.link.documentation", default="Documentation"),
        url=APP_DOCUMENTATION,
        description=t("help.link.documentation.desc", default="Read the full documentation"),
        icon="📚",
    ),
    LinkInfo(
        label=t("help.link.repository", default="Source Code"),
        url=APP_REPOSITORY,
        description=t("help.link.repository.desc", default="View the source code on GitHub"),
        icon="💻",
    ),
    LinkInfo(
        label=t("help.link.bugs", default="Report a Bug"),
        url=APP_BUG_TRACKER,
        description=t("help.link.bugs.desc", default="Report issues or request features"),
        icon="🐛",
    ),
]


# ============================================================================
# WIDGETS CUSTOM — Composants de l'aide
# ============================================================================


if TEXTUAL_AVAILABLE:

    class HelpSectionItem(ListItem):
        """Item de section dans la sidebar."""

        DEFAULT_CSS = """
        HelpSectionItem {
            padding: 1;
            height: auto;
        }
        HelpSectionItem.selected {
            background: $primary-background;
            text-style: bold;
        }
        """

        def __init__(
            self,
            section: HelpSection,
            *,
            selected: bool = False,
        ) -> None:
            """Initialise l'item.

            Args:
                section: Section représentée.
                selected: Indique si l'item est sélectionné.
            """
            super().__init__()
            self.section = section
            self.selected = selected

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(f"{self.section.icon} {self.section.label}")

    class HelpSidebar(Widget):
        """Sidebar avec les sections de l'aide."""

        DEFAULT_CSS = """
        HelpSidebar {
            width: 30;
            border-right: solid $primary;
        }
        HelpSidebar > .sidebar-title {
            text-style: bold;
            padding: 1;
        }
        HelpSidebar > ListView {
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
            self._current_section: HelpSection = HelpSection.ABOUT

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(
                t("help.sidebar.title", default="📖 Help Sections"),
                classes="sidebar-title",
            )
            yield ListView(id="help-sections-list")

        def on_mount(self) -> None:
            """Appelé lors du montage."""
            list_view = self.query_one("#help-sections-list", ListView)

            for section in HelpSection:
                item = HelpSectionItem(section)
                list_view.append(item)

            # Sélectionner la première section
            if list_view.children:
                list_view.index = 0

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            """Gère la sélection d'une section."""
            if isinstance(event.item, HelpSectionItem):
                self._current_section = event.item.section
                self.post_message(SectionSelected(event.item.section))

    class ShortcutWidget(Widget):
        """Widget pour afficher un raccourci clavier."""

        DEFAULT_CSS = """
        ShortcutWidget {
            layout: horizontal;
            height: 1;
            padding: 0 1;
        }
        ShortcutWidget > .shortcut-keys {
            width: 20;
            background: $primary-background;
            padding: 0 1;
            text-style: bold;
        }
        ShortcutWidget > .shortcut-desc {
            width: 1fr;
            padding: 0 1;
        }
        """

        def __init__(
            self,
            shortcut: ShortcutInfo,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise le widget.

            Args:
                shortcut: Information sur le raccourci.
                name: Nom du widget.
                id: ID du widget.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self.shortcut = shortcut

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(f"[{self.shortcut.keys}]", classes="shortcut-keys")
            yield Static(self.shortcut.description, classes="shortcut-desc")

    class LinkWidget(Widget):
        """Widget pour afficher un lien cliquable."""

        DEFAULT_CSS = """
        LinkWidget {
            layout: horizontal;
            height: 3;
            padding: 0 1;
        }
        LinkWidget > .link-icon {
            width: 3;
        }
        LinkWidget > .link-content {
            width: 1fr;
        }
        LinkWidget > .link-label {
            text-style: underline;
            color: $accent;
        }
        LinkWidget > .link-url {
            color: $text-muted;
        }
        LinkWidget > .link-desc {
            color: $text-muted;
        }
        """

        def __init__(
            self,
            link: LinkInfo,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise le widget.

            Args:
                link: Information sur le lien.
                name: Nom du widget.
                id: ID du widget.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self.link = link

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(self.link.icon, classes="link-icon")
            with Vertical(classes="link-content"):
                yield Static(self.link.label, classes="link-label")
                yield Static(self.link.url, classes="link-url")
                if self.link.description:
                    yield Static(self.link.description, classes="link-desc")

        def on_click(self) -> None:
            """Gère le clic sur le lien."""
            self.post_message(LinkClicked(self.link))

    class HelpContent(Widget):
        """Contenu principal de l'aide."""

        DEFAULT_CSS = """
        HelpContent {
            width: 1fr;
            padding: 1;
        }
        HelpContent > .section-title {
            text-style: bold;
            padding: 1 0;
        }
        HelpContent > .section-content {
            padding: 0 1;
        }
        HelpContent > .info-block {
            padding: 1;
            background: $surface;
            margin: 1 0;
        }
        HelpContent > .search-results {
            padding: 1;
        }
        HelpContent > .no-results {
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
            """Initialise le contenu."""
            super().__init__(name=name, id=id, classes=classes)
            self._current_section: HelpSection = HelpSection.ABOUT

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield VerticalScroll(id="help-content-scroll")

        def update_section(self, section: HelpSection) -> None:
            """Met à jour la section affichée.

            Args:
                section: Section à afficher.
            """
            self._current_section = section
            scroll = self.query_one("#help-content-scroll", VerticalScroll)
            scroll.remove_children()

            if section == HelpSection.ABOUT:
                self._render_about_section(scroll)
            elif section == HelpSection.SHORTCUTS:
                self._render_shortcuts_section(scroll)
            elif section == HelpSection.NAVIGATION:
                self._render_navigation_section(scroll)
            elif section == HelpSection.COMMANDS:
                self._render_commands_section(scroll)
            elif section == HelpSection.LINKS:
                self._render_links_section(scroll)
            elif section == HelpSection.SUPPORT:
                self._render_support_section(scroll)
            elif section == HelpSection.CREDITS:
                self._render_credits_section(scroll)

        def _render_about_section(self, container: VerticalScroll) -> None:
            """Rend la section À propos."""
            container.mount(Static(
                f"{HelpSection.ABOUT.icon} {HelpSection.ABOUT.label}",
                classes="section-title",
            ))

            about_text = f"""
[b]{APP_NAME}[/b] v{APP_VERSION}
[i]{APP_TAGLINE}[/i]

{t('help.about.description', default='NexusDL is an advanced downloader for manga, webtoons, and comics. It supports 60+ sites, provides complete library management, and offers modern interfaces (CLI, Web, GUI).')}

[b]{t('help.about.author', default='Author')}:[/b] {APP_AUTHOR}
[b]{t('help.about.license', default='License')}:[/b] {APP_LICENSE}
[b]{t('help.about.python', default='Python')}:[/b] {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}+
[b]{t('help.about.platform', default='Platform')}:[/b] {self._get_platform_info()}
[b]{t('help.about.config_dir', default='Config Directory')}:[/b] {get_paths().config_dir}
[b]{t('help.about.data_dir', default='Data Directory')}:[/b] {get_paths().data_dir}
"""
            container.mount(Static(about_text, classes="info-block", markup=True))

        def _render_shortcuts_section(self, container: VerticalScroll) -> None:
            """Rend la section Raccourcis."""
            container.mount(Static(
                f"{HelpSection.SHORTCUTS.icon} {HelpSection.SHORTCUTS.label}",
                classes="section-title",
            ))

            container.mount(Static(
                t("help.shortcuts.description", default="Global keyboard shortcuts available in all screens:"),
                classes="section-content",
            ))

            for shortcut in GLOBAL_SHORTCUTS:
                container.mount(ShortcutWidget(shortcut))

        def _render_navigation_section(self, container: VerticalScroll) -> None:
            """Rend la section Navigation."""
            container.mount(Static(
                f"{HelpSection.NAVIGATION.icon} {HelpSection.NAVIGATION.label}",
                classes="section-title",
            ))

            container.mount(Static(
                t("help.navigation.description", default="Navigation keys for moving through the interface:"),
                classes="section-content",
            ))

            for shortcut in NAVIGATION_SHORTCUTS:
                container.mount(ShortcutWidget(shortcut))

        def _render_commands_section(self, container: VerticalScroll) -> None:
            """Rend la section Commandes."""
            container.mount(Static(
                f"{HelpSection.COMMANDS.icon} {HelpSection.COMMANDS.label}",
                classes="section-title",
            ))

            container.mount(Static(
                t("help.commands.description", default="Screen-specific commands:"),
                classes="section-content",
            ))

            for screen_name, shortcuts in SCREEN_SHORTCUTS.items():
                container.mount(Static(
                    f"\n[b]{screen_name.upper()}[/b]",
                    markup=True,
                ))
                for shortcut in shortcuts:
                    container.mount(ShortcutWidget(shortcut))

        def _render_links_section(self, container: VerticalScroll) -> None:
            """Rend la section Liens."""
            container.mount(Static(
                f"{HelpSection.LINKS.icon} {HelpSection.LINKS.label}",
                classes="section-title",
            ))

            container.mount(Static(
                t("help.links.description", default="Useful links (click to open in browser):"),
                classes="section-content",
            ))

            for link in USEFUL_LINKS:
                container.mount(LinkWidget(link))

        def _render_support_section(self, container: VerticalScroll) -> None:
            """Rend la section Support."""
            container.mount(Static(
                f"{HelpSection.SUPPORT.icon} {HelpSection.SUPPORT.label}",
                classes="section-title",
            ))

            support_text = f"""
[b]{t('help.support.getting_help', default='Getting Help')}[/b]

{t('help.support.documentation', default='• Read the documentation at:')} {APP_DOCUMENTATION}
{t('help.support.bugs', default='• Report bugs at:')} {APP_BUG_TRACKER}
{t('help.support.email', default='• Contact us at:')} {APP_AUTHOR_EMAIL if hasattr(self, 'APP_AUTHOR_EMAIL') else 'contact@nexusdl.dev'}

[b]{t('help.support.faq', default='Frequently Asked Questions')}[/b]

[b]Q: {t('help.support.q1', default='How do I add a new site?')}[/b]
A: {t('help.support.a1', default='Create a parser in nexusdl/parsers/ and add it to sites.yaml')}

[b]Q: {t('help.support.q2', default='How do I configure proxies?')}[/b]
A: {t('help.support.a2', default='Edit the proxy section in your config file or use environment variables')}

[b]Q: {t('help.support.q3', default='Where are my downloads saved?')}[/b]
A: {t('help.support.a3', default='By default in:')} {get_paths().downloads_dir}
"""
            container.mount(Static(support_text, classes="info-block", markup=True))

        def _render_credits_section(self, container: VerticalScroll) -> None:
            """Rend la section Crédits."""
            container.mount(Static(
                f"{HelpSection.CREDITS.icon} {HelpSection.CREDITS.label}",
                classes="section-title",
            ))

            credits_text = f"""
[b]{t('help.credits.thanks', default='Thanks to')}[/b]

{t('help.credits.contributors', default='• All contributors to the NexusDL project')}
{t('help.credits.translators', default='• All translators who helped with i18n')}
{t('help.credits.testers', default='• All beta testers who provided feedback')}

[b]{t('help.credits.libraries', default='Third-party Libraries')}[/b]

• Textual - Terminal UI framework (MIT License)
• Pydantic - Data validation (MIT License)
• Loguru - Logging library (MIT License)
• httpx - HTTP client (BSD License)
• Pillow - Image processing (HPND License)
• aiosqlite - Async SQLite (MIT License)
• platformdirs - Platform-specific directories (MIT License)
• cryptography - Encryption (Apache/BSD License)
• PyYAML - YAML parser (MIT License)
• orjson - Fast JSON (Apache/MIT License)

[b]{APP_NAME}[/b] v{APP_VERSION}
{APP_LICENSE} License
Copyright © 2024-{datetime.now(UTC).year} {APP_AUTHOR}
"""
            container.mount(Static(credits_text, classes="info-block", markup=True))

        @staticmethod
        def _get_platform_info() -> str:
            """Retourne des informations sur la plateforme."""
            import platform

            system = platform.system()
            release = platform.release()
            machine = platform.machine()
            python_version = platform.python_version()

            return f"{system} {release} ({machine}) - Python {python_version}"

    class HelpSearchBar(Widget):
        """Barre de recherche dans l'aide."""

        DEFAULT_CSS = """
        HelpSearchBar {
            layout: horizontal;
            height: 3;
            padding: 0 1;
            border-bottom: solid $primary;
        }
        HelpSearchBar > Label {
            width: auto;
            content-align: left middle;
            padding: 0 1;
        }
        HelpSearchBar > Input {
            width: 1fr;
        }
        HelpSearchBar > .search-count {
            width: auto;
            content-align: right middle;
            padding: 0 1;
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
            """Initialise la barre de recherche."""
            super().__init__(name=name, id=id, classes=classes)

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Label("🔍")
            yield Input(
                placeholder=t("help.search.placeholder", default="Search in help..."),
                id="help-search",
            )
            yield Static("", id="search-count", classes="search-count")

        def on_input_changed(self, event: Input.Changed) -> None:
            """Gère le changement de texte."""
            if event.input.id == "help-search":
                self.post_message(SearchChanged(event.value))

        def update_count(self, count: int) -> None:
            """Met à jour le compteur de résultats.

            Args:
                count: Nombre de résultats.
            """
            count_widget = self.query_one("#search-count", Static)
            if count > 0:
                count_widget.update(f"{count} {t('help.search.results', default='results')}")
            else:
                count_widget.update("")


# ============================================================================
# MESSAGES — Événements Textual
# ============================================================================


if TEXTUAL_AVAILABLE:

    class SectionSelected(Message):
        """Message émis lorsqu'une section est sélectionnée."""

        def __init__(self, section: HelpSection) -> None:
            """Initialise le message.

            Args:
                section: Section sélectionnée.
            """
            super().__init__()
            self.section = section

    class SearchChanged(Message):
        """Message émis lorsque la recherche change."""

        def __init__(self, query: str) -> None:
            """Initialise le message.

            Args:
                query: Texte de recherche.
            """
            super().__init__()
            self.query = query

    class LinkClicked(Message):
        """Message émis lorsqu'un lien est cliqué."""

        def __init__(self, link: LinkInfo) -> None:
            """Initialise le message.

            Args:
                link: Lien cliqué.
            """
            super().__init__()
            self.link = link


# ============================================================================
# CLASSE PRINCIPALE — HelpScreen
# ============================================================================


if TEXTUAL_AVAILABLE:

    class HelpScreen(Screen):
        """Écran d'aide de l'application.

        Affiche les informations sur l'application, les raccourcis clavier,
        les commandes disponibles, et les liens utiles.
        """

        # Bindings clavier
        BINDINGS = [
            Binding("/", "focus_search", "Search"),
            Binding("escape", "close", "Close"),
            Binding("q", "close", "Close"),
            Binding("tab", "next_section", "Next Section"),
            Binding("shift+tab", "prev_section", "Previous Section"),
            Binding("ctrl+c", "copy_info", "Copy Info"),
        ]

        # CSS de l'écran
        DEFAULT_CSS = """
        HelpScreen {
            layout: vertical;
        }

        #help-container {
            height: 1fr;
            layout: horizontal;
        }

        #help-sidebar {
            width: 30;
        }

        #help-main {
            width: 1fr;
            layout: vertical;
        }

        #help-search-bar {
            height: auto;
        }

        #help-content {
            height: 1fr;
        }
        """

        # État réactif
        current_section: reactive[HelpSection] = reactive(HelpSection.ABOUT)

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
            self._state = HelpState()

        def compose(self) -> ComposeResult:
            """Compose l'écran."""
            yield Header(show_clock=True)

            with Horizontal(id="help-container"):
                # Sidebar
                with Vertical(id="help-sidebar"):
                    yield HelpSidebar(id="help-sidebar-widget")

                # Contenu principal
                with Vertical(id="help-main"):
                    yield HelpSearchBar(id="help-search-bar")
                    yield HelpContent(id="help-content")

            yield Footer()

        def on_mount(self) -> None:
            """Appelé lors du montage."""
            # Afficher la section par défaut
            content = self.query_one("#help-content", HelpContent)
            content.update_section(self.current_section)

        # =====================================================================
        # GESTION DES MESSAGES
        # =====================================================================

        def on_section_selected(self, message: SectionSelected) -> None:
            """Gère la sélection d'une section.

            Args:
                message: Message avec la section sélectionnée.
            """
            self.current_section = message.section
            content = self.query_one("#help-content", HelpContent)
            content.update_section(message.section)

        def on_search_changed(self, message: SearchChanged) -> None:
            """Gère le changement de recherche.

            Args:
                message: Message avec le texte de recherche.
            """
            self._state.search_query = message.query

            if not message.query:
                # Pas de recherche, afficher la section courante
                content = self.query_one("#help-content", HelpContent)
                content.update_section(self.current_section)
                return

            # Effectuer la recherche
            results = self._search_in_help(message.query)
            self._state.search_results = results

            # Afficher les résultats
            search_bar = self.query_one("#help-search-bar", HelpSearchBar)
            search_bar.update_count(len(results))

            # TODO: Afficher les résultats dans le contenu

        def on_link_clicked(self, message: LinkClicked) -> None:
            """Gère le clic sur un lien.

            Args:
                message: Message avec le lien cliqué.
            """
            try:
                webbrowser.open(message.link.url)
                self.notify(
                    t(
                        "help.notify.link_opened",
                        default="Opened: {url}",
                        url=message.link.url,
                    ),
                    severity="information",
                )
            except Exception as e:
                logger.error("Impossible d'ouvrir le lien: {}", e)
                self.notify(
                    t(
                        "help.notify.link_failed",
                        default="Failed to open link: {error}",
                        error=str(e),
                    ),
                    severity="error",
                )

        # =====================================================================
        # RECHERCHE
        # =====================================================================

        def _search_in_help(self, query: str) -> list[str]:
            """Recherche dans le contenu de l'aide.

            Args:
                query: Texte à rechercher.

            Returns:
                Liste des résultats (descriptions de raccourcis, liens, etc.).
            """
            if not query:
                return []

            query_lower = query.lower()
            results: list[str] = []

            # Rechercher dans les raccourcis globaux
            for shortcut in GLOBAL_SHORTCUTS:
                if query_lower in shortcut.description.lower() or query_lower in shortcut.keys.lower():
                    results.append(f"{shortcut.keys}: {shortcut.description}")

            # Rechercher dans les raccourcis de navigation
            for shortcut in NAVIGATION_SHORTCUTS:
                if query_lower in shortcut.description.lower() or query_lower in shortcut.keys.lower():
                    results.append(f"{shortcut.keys}: {shortcut.description}")

            # Rechercher dans les raccourcis par écran
            for screen_name, shortcuts in SCREEN_SHORTCUTS.items():
                for shortcut in shortcuts:
                    if query_lower in shortcut.description.lower() or query_lower in shortcut.keys.lower():
                        results.append(f"[{screen_name}] {shortcut.keys}: {shortcut.description}")

            # Rechercher dans les liens
            for link in USEFUL_LINKS:
                if (
                    query_lower in link.label.lower()
                    or query_lower in link.url.lower()
                    or query_lower in link.description.lower()
                ):
                    results.append(f"{link.icon} {link.label}: {link.url}")

            return results

        # =====================================================================
        # ACTIONS — Bindings clavier
        # =====================================================================

        def action_focus_search(self) -> None:
            """Action : focus sur le champ de recherche."""
            search_input = self.query_one("#help-search", Input)
            search_input.focus()

        def action_close(self) -> None:
            """Action : fermer l'écran."""
            self.app.pop_screen()

        def action_next_section(self) -> None:
            """Action : passer à la section suivante."""
            sections = list(HelpSection)
            current_index = sections.index(self.current_section)
            next_index = (current_index + 1) % len(sections)
            self.current_section = sections[next_index]

            # Mettre à jour la sidebar
            sidebar_list = self.query_one("#help-sections-list", ListView)
            sidebar_list.index = next_index

            # Mettre à jour le contenu
            content = self.query_one("#help-content", HelpContent)
            content.update_section(self.current_section)

        def action_prev_section(self) -> None:
            """Action : passer à la section précédente."""
            sections = list(HelpSection)
            current_index = sections.index(self.current_section)
            prev_index = (current_index - 1) % len(sections)
            self.current_section = sections[prev_index]

            # Mettre à jour la sidebar
            sidebar_list = self.query_one("#help-sections-list", ListView)
            sidebar_list.index = prev_index

            # Mettre à jour le contenu
            content = self.query_one("#help-content", HelpContent)
            content.update_section(self.current_section)

        def action_copy_info(self) -> None:
            """Action : copier les informations dans le presse-papiers."""
            # Construire le texte à copier
            info_text = f"""
{APP_NAME} v{APP_VERSION}
{APP_TAGLINE}

Author: {APP_AUTHOR}
License: {APP_LICENSE}
Website: {APP_URL}
Documentation: {APP_DOCUMENTATION}
Repository: {APP_REPOSITORY}
Bug Tracker: {APP_BUG_TRACKER}

Python: {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}+
Platform: {HelpContent._get_platform_info()}
Config Dir: {get_paths().config_dir}
Data Dir: {get_paths().data_dir}
"""

            # Copier dans le presse-papiers
            try:
                import pyperclip
                pyperclip.copy(info_text.strip())
                self.notify(
                    t("help.notify.copied", default="Information copied to clipboard"),
                    severity="information",
                )
            except ImportError:
                # pyperclip non disponible
                self.notify(
                    t("help.notify.copy_failed", default="Clipboard not available"),
                    severity="warning",
                )
            except Exception as e:
                logger.error("Erreur lors de la copie: {}", e)
                self.notify(
                    t("help.notify.copy_failed", default="Copy failed: {error}", error=str(e)),
                    severity="error",
                )


# ============================================================================
# HELPERS PUBLICS
# ============================================================================


def get_help_text() -> str:
    """Retourne le texte d'aide complet (pour affichage hors TUI).

    Returns:
        Texte d'aide formaté.
    """
    lines = [
        f"{APP_NAME} v{APP_VERSION} - {APP_TAGLINE}",
        "=" * 70,
        "",
        t("help.text.global_shortcuts", default="GLOBAL SHORTCUTS"),
        "-" * 70,
    ]

    for shortcut in GLOBAL_SHORTCUTS:
        lines.append(f"  {shortcut.keys:<15} {shortcut.description}")

    lines.extend([
        "",
        t("help.text.navigation", default="NAVIGATION"),
        "-" * 70,
    ])

    for shortcut in NAVIGATION_SHORTCUTS:
        lines.append(f"  {shortcut.keys:<15} {shortcut.description}")

    lines.extend([
        "",
        t("help.text.screen_commands", default="SCREEN-SPECIFIC COMMANDS"),
        "-" * 70,
    ])

    for screen_name, shortcuts in SCREEN_SHORTCUTS.items():
        lines.append(f"\n  [{screen_name.upper()}]")
        for shortcut in shortcuts:
            lines.append(f"    {shortcut.keys:<15} {shortcut.description}")

    lines.extend([
        "",
        t("help.text.useful_links", default="USEFUL LINKS"),
        "-" * 70,
    ])

    for link in USEFUL_LINKS:
        lines.append(f"  {link.icon} {link.label:<20} {link.url}")

    lines.extend([
        "",
        "=" * 70,
        f"{APP_NAME} - {APP_LICENSE} License",
        f"Copyright © 2024-{datetime.now(UTC).year} {APP_AUTHOR}",
    ])

    return "\n".join(lines)


def open_url(url: str) -> bool:
    """Ouvre une URL dans le navigateur par défaut.

    Args:
        url: URL à ouvrir.

    Returns:
        True si l'ouverture a réussi.
    """
    try:
        webbrowser.open(url)
        return True
    except Exception as e:
        logger.error("Impossible d'ouvrir l'URL {}: {}", url, e)
        return False


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "HelpScreenError",
    "LinkOpenError",
    # Enums
    "HelpSection",
    # Modèles
    "ShortcutInfo",
    "LinkInfo",
    "HelpState",
    # Données
    "GLOBAL_SHORTCUTS",
    "NAVIGATION_SHORTCUTS",
    "SCREEN_SHORTCUTS",
    "USEFUL_LINKS",
    # Écran principal
    "HelpScreen" if TEXTUAL_AVAILABLE else None,
    # Helpers
    "get_help_text",
    "open_url",
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
