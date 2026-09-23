"""Écran de paramètres de l'interface CLI NexusDL.

Ce module fournit un écran complet de gestion des paramètres via une
interface TUI (Terminal User Interface) basée sur Textual. Il permet à
l'utilisateur de visualiser, modifier, sauvegarder et réinitialiser tous
les paramètres de l'application.

**Sections de paramètres** :
    - Application : langue, thème, timezone, sens de lecture
    - Réseau : timeouts, connexions, SSL, HTTP/2
    - Proxy : activation, URL, authentification, rotation
    - Téléchargements : concurrence, format, qualité, compression
    - Bibliothèque : chemin, scan automatique, intervalle
    - Cloudflare : mode bypass, FlareSolverr, Playwright
    - Logging : niveau, format, rotation, rétention
    - Stockage : cache, nettoyage, tailles max
    - Interface : Web UI, GUI, CLI

**Architecture** :
    SettingsScreen (Screen Textual)
        ├── SettingsSidebar (sidebar avec sections)
        │   └── SectionItem (item de section)
        ├── SettingsContent (contenu principal)
        │   ├── BoolSetting (switch on/off)
        │   ├── SelectSetting (liste de choix)
        │   ├── TextSetting (champ texte)
        │   ├── NumberSetting (champ numérique)
        │   ├── PathSetting (chemin de fichier)
        │   └── ActionSetting (bouton d'action)
        └── SettingsFooter (boutons Save/Reset/Cancel)

**Fonctionnalités** :
    - Navigation par sections (sidebar)
    - Édition inline avec validation
    - Indicateurs visuels pour les champs modifiés
    - Sauvegarde atomique (via atomic_write)
    - Reset aux valeurs par défaut
    - Export/import de configuration
    - Navigation clavier complète (Tab, Enter, Escape, flèches)
    - Messages de confirmation
    - Gestion des erreurs
    - Traductions i18n

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.cli.screens.settings import SettingsScreen
    >>>
    >>> # Dans l'application principale
    >>> app.push_screen(SettingsScreen())
    >>>
    >>> # L'utilisateur peut :
    >>> # 1. Naviguer entre les sections avec les flèches
    >>> # 2. Modifier les paramètres avec Tab/Enter
    >>> # 3. Sauvegarder avec Ctrl+S
    >>> # 4. Annuler avec Escape

Intégration :
    - core/config.py : lecture/écriture de la configuration
    - core/paths.py : chemins des fichiers
    - core/i18n.py : traductions
    - core/events.py : événements de changement
    - core/logger.py : logs des actions
    - core/constants.py : valeurs par défaut
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, ClassVar, Final

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
        Switch,
    )
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False

from nexusdl.core.config import (
    AppConfig,
    CloudflareConfig,
    DownloadConfig,
    I18nConfig,
    InterfaceConfig,
    LibraryConfig,
    LoggingConfig,
    NetworkConfig,
    NexusDLConfig,
    ProxyConfig,
    StorageConfig,
    get_config,
    get_config_manager,
)
from nexusdl.core.constants import (
    APP_NAME,
    DEFAULT_LANGUAGE,
    DEFAULT_THEME,
    SUPPORTED_PACKAGING_FORMATS,
)
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t
from nexusdl.core.paths import get_paths
from nexusdl.core.utils.filesystem import atomic_write


# ============================================================================
# EXCEPTIONS
# ============================================================================


class SettingsError(NexusDLError):
    """Exception de base pour les erreurs de paramètres."""


class SettingsValidationError(SettingsError):
    """Exception levée lorsqu'un paramètre est invalide."""

    def __init__(self, key: str, value: Any, reason: str = "") -> None:
        msg = f"Paramètre invalide: {key}={value!r}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.key = key
        self.value = value
        self.reason = reason


class SettingsSaveError(SettingsError):
    """Exception levée lorsqu'une sauvegarde échoue."""

    def __init__(self, path: Path, reason: str = "") -> None:
        msg = f"Échec de la sauvegarde des paramètres: {path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.path = path
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class SettingsSection(str, Enum):
    """Sections de paramètres.

    Attributes:
        APPLICATION: Paramètres généraux de l'application.
        NETWORK: Paramètres réseau.
        PROXY: Paramètres de proxy.
        DOWNLOAD: Paramètres de téléchargement.
        LIBRARY: Paramètres de bibliothèque.
        CLOUDFLARE: Paramètres de contournement Cloudflare.
        LOGGING: Paramètres de logging.
        STORAGE: Paramètres de stockage.
        INTERFACE: Paramètres d'interface.
    """

    APPLICATION = "application"
    NETWORK = "network"
    PROXY = "proxy"
    DOWNLOAD = "download"
    LIBRARY = "library"
    CLOUDFLARE = "cloudflare"
    LOGGING = "logging"
    STORAGE = "storage"
    INTERFACE = "interface"

    @property
    def label(self) -> str:
        """Libellé humain de la section."""
        return {
            SettingsSection.APPLICATION: t("settings.section.application", default="Application"),
            SettingsSection.NETWORK: t("settings.section.network", default="Network"),
            SettingsSection.PROXY: t("settings.section.proxy", default="Proxy"),
            SettingsSection.DOWNLOAD: t("settings.section.download", default="Downloads"),
            SettingsSection.LIBRARY: t("settings.section.library", default="Library"),
            SettingsSection.CLOUDFLARE: t("settings.section.cloudflare", default="Cloudflare"),
            SettingsSection.LOGGING: t("settings.section.logging", default="Logging"),
            SettingsSection.STORAGE: t("settings.section.storage", default="Storage"),
            SettingsSection.INTERFACE: t("settings.section.interface", default="Interface"),
        }

    @property
    def icon(self) -> str:
        """Icône Unicode de la section."""
        return {
            SettingsSection.APPLICATION: "⚙️",
            SettingsSection.NETWORK: "🌐",
            SettingsSection.PROXY: "🔀",
            SettingsSection.DOWNLOAD: "📥",
            SettingsSection.LIBRARY: "📚",
            SettingsSection.CLOUDFLARE: "🛡️",
            SettingsSection.LOGGING: "📝",
            SettingsSection.STORAGE: "💾",
            SettingsSection.INTERFACE: "🖥️",
        }[self]


class SettingType(str, Enum):
    """Type de paramètre.

    Attributes:
        BOOL: Paramètre booléen (on/off).
        SELECT: Paramètre avec liste de choix.
        TEXT: Paramètre texte.
        NUMBER: Paramètre numérique.
        PATH: Paramètre chemin de fichier.
        ACTION: Bouton d'action.
    """

    BOOL = "bool"
    SELECT = "select"
    TEXT = "text"
    NUMBER = "number"
    PATH = "path"
    ACTION = "action"


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class SettingDefinition(BaseModel):
    """Définition d'un paramètre.

    Attributes:
        key: Clé unique du paramètre (ex: "app.language").
        label: Libellé affiché.
        description: Description du paramètre.
        setting_type: Type du paramètre.
        default: Valeur par défaut.
        current: Valeur actuelle.
        choices: Liste de choix (pour SELECT).
        min_value: Valeur minimum (pour NUMBER).
        max_value: Valeur maximum (pour NUMBER).
        placeholder: Texte placeholder (pour TEXT/PATH).
        section: Section du paramètre.
        modified: Indique si le paramètre a été modifié.
        validator: Fonction de validation (optionnelle).
    """

    key: str = Field(..., description="Clé unique du paramètre.")
    label: str = Field(..., description="Libellé affiché.")
    description: str = Field(default="", description="Description.")
    setting_type: SettingType = Field(..., description="Type du paramètre.")
    default: Any = Field(default=None, description="Valeur par défaut.")
    current: Any = Field(default=None, description="Valeur actuelle.")
    choices: list[Any] | None = Field(default=None, description="Liste de choix.")
    min_value: float | None = Field(default=None, description="Valeur minimum.")
    max_value: float | None = Field(default=None, description="Valeur maximum.")
    placeholder: str = Field(default="", description="Texte placeholder.")
    section: SettingsSection = Field(..., description="Section du paramètre.")
    modified: bool = Field(default=False, description="Indique si modifié.")
    validator: Callable[[Any], bool] | None = Field(
        default=None,
        description="Fonction de validation.",
        exclude=True,
    )

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    def validate(self, value: Any) -> bool:
        """Valide une valeur pour ce paramètre.

        Args:
            value: Valeur à valider.

        Returns:
            True si la valeur est valide.
        """
        # Validation personnalisée
        if self.validator is not None:
            try:
                return self.validator(value)
            except Exception:
                return False

        # Validation par type
        if self.setting_type == SettingType.BOOL:
            return isinstance(value, bool)

        if self.setting_type == SettingType.SELECT:
            return self.choices is not None and value in self.choices

        if self.setting_type == SettingType.NUMBER:
            try:
                num = float(value)
                if self.min_value is not None and num < self.min_value:
                    return False
                if self.max_value is not None and num > self.max_value:
                    return False
                return True
            except (ValueError, TypeError):
                return False

        if self.setting_type == SettingType.TEXT:
            return isinstance(value, str) and len(value) > 0

        if self.setting_type == SettingType.PATH:
            try:
                Path(str(value))
                return True
            except Exception:
                return False

        return True


class SettingsState(BaseModel):
    """État global des paramètres.

    Attributes:
        definitions: Liste des définitions de paramètres.
        modified_count: Nombre de paramètres modifiés.
        last_saved_at: Timestamp de la dernière sauvegarde.
        has_unsaved_changes: Indique s'il y a des changements non sauvegardés.
    """

    definitions: dict[str, SettingDefinition] = Field(
        default_factory=dict,
        description="Définitions des paramètres.",
    )
    modified_count: int = Field(default=0, ge=0)
    last_saved_at: datetime | None = Field(default=None)
    has_unsaved_changes: bool = Field(default=False)

    model_config = ConfigDict(extra="forbid")

    def mark_modified(self, key: str) -> None:
        """Marque un paramètre comme modifié."""
        if key in self.definitions:
            self.definitions[key].modified = True
            self.modified_count = sum(
                1 for d in self.definitions.values() if d.modified
            )
            self.has_unsaved_changes = self.modified_count > 0

    def reset_modified(self) -> None:
        """Réinitialise les indicateurs de modification."""
        for definition in self.definitions.values():
            definition.modified = False
        self.modified_count = 0
        self.has_unsaved_changes = False


# ============================================================================
# WIDGETS CUSTOM — Composants de paramètres
# ============================================================================


if TEXTUAL_AVAILABLE:

    class SectionItem(ListItem):
        """Item de section dans la sidebar.

        Affiche l'icône et le libellé de la section, avec indication
        visuelle si des paramètres de cette section ont été modifiés.
        """

        def __init__(
            self,
            section: SettingsSection,
            *,
            modified_count: int = 0,
        ) -> None:
            """Initialise l'item de section.

            Args:
                section: Section représentée.
                modified_count: Nombre de paramètres modifiés dans cette section.
            """
            super().__init__()
            self.section = section
            self.modified_count = modified_count

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            modifier = f" ({self.modified_count})" if self.modified_count > 0 else ""
            yield Static(
                f"{self.section.icon} {self.section.label}{modifier}",
                classes="section-item-label",
            )

    class BoolSetting(Widget):
        """Widget pour un paramètre booléen (switch on/off)."""

        DEFAULT_CSS = """
        BoolSetting {
            layout: horizontal;
            height: 3;
            padding: 0 1;
        }
        BoolSetting > Label {
            width: 1fr;
            content-align: left middle;
        }
        BoolSetting > Switch {
            width: auto;
        }
        """

        def __init__(
            self,
            definition: SettingDefinition,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise le widget.

            Args:
                definition: Définition du paramètre.
                name: Nom du widget.
                id: ID du widget.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self.definition = definition

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Label(self.definition.label)
            yield Switch(value=bool(self.definition.current or False))

        def on_switch_changed(self, event: Switch.Changed) -> None:
            """Gère le changement de valeur du switch."""
            self.definition.current = event.value
            self.definition.modified = True
            self.post_message(SettingChanged(self.definition.key, event.value))

    class SelectSetting(Widget):
        """Widget pour un paramètre avec liste de choix."""

        DEFAULT_CSS = """
        SelectSetting {
            layout: horizontal;
            height: 3;
            padding: 0 1;
        }
        SelectSetting > Label {
            width: 1fr;
            content-align: left middle;
        }
        SelectSetting > Select {
            width: 30;
        }
        """

        def __init__(
            self,
            definition: SettingDefinition,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise le widget."""
            super().__init__(name=name, id=id, classes=classes)
            self.definition = definition

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Label(self.definition.label)
            choices = self.definition.choices or []
            yield Select(
                [(str(c), c) for c in choices],
                value=self.definition.current,
                allow_blank=False,
            )

        def on_select_changed(self, event: Select.Changed) -> None:
            """Gère le changement de sélection."""
            self.definition.current = event.value
            self.definition.modified = True
            self.post_message(SettingChanged(self.definition.key, event.value))

    class TextSetting(Widget):
        """Widget pour un paramètre texte."""

        DEFAULT_CSS = """
        TextSetting {
            layout: horizontal;
            height: 3;
            padding: 0 1;
        }
        TextSetting > Label {
            width: 1fr;
            content-align: left middle;
        }
        TextSetting > Input {
            width: 40;
        }
        """

        def __init__(
            self,
            definition: SettingDefinition,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise le widget."""
            super().__init__(name=name, id=id, classes=classes)
            self.definition = definition

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Label(self.definition.label)
            yield Input(
                value=str(self.definition.current or ""),
                placeholder=self.definition.placeholder,
            )

        def on_input_changed(self, event: Input.Changed) -> None:
            """Gère le changement de valeur."""
            self.definition.current = event.value
            self.definition.modified = True
            self.post_message(SettingChanged(self.definition.key, event.value))

    class NumberSetting(Widget):
        """Widget pour un paramètre numérique."""

        DEFAULT_CSS = """
        NumberSetting {
            layout: horizontal;
            height: 3;
            padding: 0 1;
        }
        NumberSetting > Label {
            width: 1fr;
            content-align: left middle;
        }
        NumberSetting > Input {
            width: 20;
        }
        """

        def __init__(
            self,
            definition: SettingDefinition,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise le widget."""
            super().__init__(name=name, id=id, classes=classes)
            self.definition = definition

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Label(self.definition.label)
            yield Input(
                value=str(self.definition.current or 0),
                type="number",
                placeholder=self.definition.placeholder,
            )

        def on_input_changed(self, event: Input.Changed) -> None:
            """Gère le changement de valeur."""
            try:
                value = float(event.value)
                if self.definition.validate(value):
                    self.definition.current = value
                    self.definition.modified = True
                    self.post_message(SettingChanged(self.definition.key, value))
            except ValueError:
                pass

    class PathSetting(Widget):
        """Widget pour un paramètre chemin de fichier."""

        DEFAULT_CSS = """
        PathSetting {
            layout: horizontal;
            height: 3;
            padding: 0 1;
        }
        PathSetting > Label {
            width: 1fr;
            content-align: left middle;
        }
        PathSetting > Input {
            width: 50;
        }
        """

        def __init__(
            self,
            definition: SettingDefinition,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise le widget."""
            super().__init__(name=name, id=id, classes=classes)
            self.definition = definition

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Label(self.definition.label)
            yield Input(
                value=str(self.definition.current or ""),
                placeholder=self.definition.placeholder,
            )

        def on_input_changed(self, event: Input.Changed) -> None:
            """Gère le changement de valeur."""
            self.definition.current = event.value
            self.definition.modified = True
            self.post_message(SettingChanged(self.definition.key, event.value))

    class ActionSetting(Widget):
        """Widget pour un bouton d'action."""

        DEFAULT_CSS = """
        ActionSetting {
            layout: horizontal;
            height: 3;
            padding: 0 1;
        }
        ActionSetting > Label {
            width: 1fr;
            content-align: left middle;
        }
        ActionSetting > Button {
            width: auto;
        }
        """

        def __init__(
            self,
            definition: SettingDefinition,
            *,
            action_callback: Callable[[], None] | None = None,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise le widget."""
            super().__init__(name=name, id=id, classes=classes)
            self.definition = definition
            self.action_callback = action_callback

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Label(self.definition.label)
            yield Button(self.definition.label, variant="primary")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            """Gère le clic sur le bouton."""
            if self.action_callback is not None:
                self.action_callback()
            self.post_message(SettingAction(self.definition.key))


# ============================================================================
# MESSAGES — Événements Textual
# ============================================================================


if TEXTUAL_AVAILABLE:

    class SettingChanged(Message):
        """Message émis lorsqu'un paramètre est modifié."""

        def __init__(self, key: str, value: Any) -> None:
            """Initialise le message.

            Args:
                key: Clé du paramètre modifié.
                value: Nouvelle valeur.
            """
            super().__init__()
            self.key = key
            self.value = value

    class SettingAction(Message):
        """Message émis lorsqu'une action est déclenchée."""

        def __init__(self, key: str) -> None:
            """Initialise le message.

            Args:
                key: Clé de l'action.
            """
            super().__init__()
            self.key = key

    class SettingsSaved(Message):
        """Message émis lorsque les paramètres sont sauvegardés."""

        def __init__(self, path: Path) -> None:
            """Initialise le message.

            Args:
                path: Chemin du fichier sauvegardé.
            """
            super().__init__()
            self.path = path

    class SettingsReset(Message):
        """Message émis lorsque les paramètres sont réinitialisés."""


# ============================================================================
# CLASSE PRINCIPALE — SettingsScreen
# ============================================================================


if TEXTUAL_AVAILABLE:

    class SettingsScreen(Screen):
        """Écran de paramètres de l'application.

        Permet à l'utilisateur de visualiser et modifier tous les paramètres
        de NexusDL via une interface TUI complète.
        """

        # Bindings clavier
        BINDINGS = [
            Binding("ctrl+s", "save", "Save"),
            Binding("ctrl+r", "reset", "Reset"),
            Binding("escape", "cancel", "Cancel"),
            Binding("tab", "next_section", "Next Section"),
            Binding("shift+tab", "prev_section", "Previous Section"),
        ]

        # CSS de l'écran
        DEFAULT_CSS = """
        SettingsScreen {
            layout: vertical;
        }

        #settings-container {
            layout: horizontal;
            height: 1fr;
        }

        #settings-sidebar {
            width: 30;
            border-right: solid $primary;
        }

        #settings-content {
            width: 1fr;
            padding: 1 2;
        }

        #settings-footer {
            height: 3;
            layout: horizontal;
            padding: 0 2;
            align: right middle;
        }

        .section-title {
            text-style: bold;
            padding: 1 0;
        }

        .setting-description {
            color: $text-muted;
            padding: 0 0 1 0;
        }

        .modified-indicator {
            color: $warning;
        }
        """

        # État réactif
        current_section: reactive[SettingsSection] = reactive(SettingsSection.APPLICATION)

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise l'écran de paramètres.

            Args:
                name: Nom de l'écran.
                id: ID de l'écran.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self._state = SettingsState()
            self._initialize_settings()

        def _initialize_settings(self) -> None:
            """Initialise les définitions de paramètres depuis la configuration."""
            config = get_config()

            # Section APPLICATION
            self._add_setting(SettingDefinition(
                key="app.language",
                label=t("settings.app.language", default="Language"),
                description=t("settings.app.language.desc", default="Interface language"),
                setting_type=SettingType.SELECT,
                default=DEFAULT_LANGUAGE,
                current=config.app.language,
                choices=["en", "fr", "de", "es", "it", "pt", "ru", "ja", "ko", "zh"],
                section=SettingsSection.APPLICATION,
            ))
            self._add_setting(SettingDefinition(
                key="app.theme",
                label=t("settings.app.theme", default="Theme"),
                description=t("settings.app.theme.desc", default="Interface theme"),
                setting_type=SettingType.SELECT,
                default=DEFAULT_THEME,
                current=config.app.theme,
                choices=["light", "dark", "system", "auto"],
                section=SettingsSection.APPLICATION,
            ))
            self._add_setting(SettingDefinition(
                key="app.check_updates",
                label=t("settings.app.check_updates", default="Check for updates"),
                description=t("settings.app.check_updates.desc", default="Check for updates at startup"),
                setting_type=SettingType.BOOL,
                default=True,
                current=config.app.check_updates,
                section=SettingsSection.APPLICATION,
            ))

            # Section NETWORK
            self._add_setting(SettingDefinition(
                key="network.timeout",
                label=t("settings.network.timeout", default="HTTP Timeout (s)"),
                description=t("settings.network.timeout.desc", default="Default timeout for HTTP requests"),
                setting_type=SettingType.NUMBER,
                default=30.0,
                current=config.network.timeout,
                min_value=1.0,
                max_value=600.0,
                section=SettingsSection.NETWORK,
            ))
            self._add_setting(SettingDefinition(
                key="network.max_connections",
                label=t("settings.network.max_connections", default="Max Connections"),
                description=t("settings.network.max_connections.desc", default="Maximum concurrent HTTP connections"),
                setting_type=SettingType.NUMBER,
                default=100,
                current=config.network.max_connections,
                min_value=1,
                max_value=1000,
                section=SettingsSection.NETWORK,
            ))
            self._add_setting(SettingDefinition(
                key="network.verify_ssl",
                label=t("settings.network.verify_ssl", default="Verify SSL"),
                description=t("settings.network.verify_ssl.desc", default="Verify SSL certificates"),
                setting_type=SettingType.BOOL,
                default=True,
                current=config.network.verify_ssl,
                section=SettingsSection.NETWORK,
            ))
            self._add_setting(SettingDefinition(
                key="network.http2",
                label=t("settings.network.http2", default="Enable HTTP/2"),
                description=t("settings.network.http2.desc", default="Enable HTTP/2 protocol"),
                setting_type=SettingType.BOOL,
                default=False,
                current=config.network.http2,
                section=SettingsSection.NETWORK,
            ))
            self._add_setting(SettingDefinition(
                key="network.rotate_user_agent",
                label=t("settings.network.rotate_user_agent", default="Rotate User-Agent"),
                description=t("settings.network.rotate_user_agent.desc", default="Rotate User-Agent for each request"),
                setting_type=SettingType.BOOL,
                default=True,
                current=config.network.rotate_user_agent,
                section=SettingsSection.NETWORK,
            ))

            # Section PROXY
            self._add_setting(SettingDefinition(
                key="network.proxy.enabled",
                label=t("settings.proxy.enabled", default="Enable Proxy"),
                description=t("settings.proxy.enabled.desc", default="Enable proxy for HTTP requests"),
                setting_type=SettingType.BOOL,
                default=False,
                current=config.network.proxy.enabled,
                section=SettingsSection.PROXY,
            ))
            self._add_setting(SettingDefinition(
                key="network.proxy.url",
                label=t("settings.proxy.url", default="Proxy URL"),
                description=t("settings.proxy.url.desc", default="Proxy URL (e.g., http://proxy:8080)"),
                setting_type=SettingType.TEXT,
                default="",
                current=config.network.proxy.url or "",
                placeholder="http://proxy:8080",
                section=SettingsSection.PROXY,
            ))

            # Section DOWNLOAD
            self._add_setting(SettingDefinition(
                key="download.max_concurrent_tasks",
                label=t("settings.download.max_concurrent_tasks", default="Max Concurrent Tasks"),
                description=t("settings.download.max_concurrent_tasks.desc", default="Maximum concurrent download tasks"),
                setting_type=SettingType.NUMBER,
                default=5,
                current=config.download.max_concurrent_tasks,
                min_value=1,
                max_value=20,
                section=SettingsSection.DOWNLOAD,
            ))
            self._add_setting(SettingDefinition(
                key="download.max_concurrent_pages",
                label=t("settings.download.max_concurrent_pages", default="Max Concurrent Pages"),
                description=t("settings.download.max_concurrent_pages.desc", default="Maximum concurrent page downloads"),
                setting_type=SettingType.NUMBER,
                default=8,
                current=config.download.max_concurrent_pages,
                min_value=1,
                max_value=32,
                section=SettingsSection.DOWNLOAD,
            ))
            self._add_setting(SettingDefinition(
                key="download.default_format",
                label=t("settings.download.default_format", default="Default Format"),
                description=t("settings.download.default_format.desc", default="Default packaging format"),
                setting_type=SettingType.SELECT,
                default="cbz",
                current=config.download.default_format,
                choices=list(SUPPORTED_PACKAGING_FORMATS),
                section=SettingsSection.DOWNLOAD,
            ))
            self._add_setting(SettingDefinition(
                key="download.output_dir",
                label=t("settings.download.output_dir", default="Output Directory"),
                description=t("settings.download.output_dir.desc", default="Default download directory"),
                setting_type=SettingType.PATH,
                default="~/Downloads/NexusDL",
                current=config.download.output_dir,
                placeholder="~/Downloads/NexusDL",
                section=SettingsSection.DOWNLOAD,
            ))
            self._add_setting(SettingDefinition(
                key="download.image_quality",
                label=t("settings.download.image_quality", default="Image Quality"),
                description=t("settings.download.image_quality.desc", default="Default image quality"),
                setting_type=SettingType.SELECT,
                default="original",
                current=config.download.image_quality,
                choices=["original", "high", "medium", "low"],
                section=SettingsSection.DOWNLOAD,
            ))
            self._add_setting(SettingDefinition(
                key="download.dedup_enabled",
                label=t("settings.download.dedup_enabled", default="Enable Deduplication"),
                description=t("settings.download.dedup_enabled.desc", default="Enable image deduplication"),
                setting_type=SettingType.BOOL,
                default=True,
                current=config.download.dedup_enabled,
                section=SettingsSection.DOWNLOAD,
            ))

            # Section LIBRARY
            self._add_setting(SettingDefinition(
                key="library.path",
                label=t("settings.library.path", default="Library Path"),
                description=t("settings.library.path.desc", default="Path to local library"),
                setting_type=SettingType.PATH,
                default="~/.local/share/nexusdl/library",
                current=config.library.path,
                placeholder="~/.local/share/nexusdl/library",
                section=SettingsSection.LIBRARY,
            ))
            self._add_setting(SettingDefinition(
                key="library.auto_scan",
                label=t("settings.library.auto_scan", default="Auto Scan"),
                description=t("settings.library.auto_scan.desc", default="Automatically scan library at startup"),
                setting_type=SettingType.BOOL,
                default=True,
                current=config.library.auto_scan,
                section=SettingsSection.LIBRARY,
            ))
            self._add_setting(SettingDefinition(
                key="library.scan_interval",
                label=t("settings.library.scan_interval", default="Scan Interval (s)"),
                description=t("settings.library.scan_interval.desc", default="Interval between automatic scans"),
                setting_type=SettingType.NUMBER,
                default=300.0,
                current=config.library.scan_interval,
                min_value=30.0,
                max_value=86400.0,
                section=SettingsSection.LIBRARY,
            ))

            # Section CLOUDFLARE
            self._add_setting(SettingDefinition(
                key="cloudflare.bypass_mode",
                label=t("settings.cloudflare.bypass_mode", default="Bypass Mode"),
                description=t("settings.cloudflare.bypass_mode.desc", default="Cloudflare bypass mode"),
                setting_type=SettingType.SELECT,
                default="auto",
                current=config.cloudflare.bypass_mode,
                choices=["auto", "flaresolverr", "playwright", "none"],
                section=SettingsSection.CLOUDFLARE,
            ))
            self._add_setting(SettingDefinition(
                key="cloudflare.flaresolverr_url",
                label=t("settings.cloudflare.flaresolverr_url", default="FlareSolverr URL"),
                description=t("settings.cloudflare.flaresolverr_url.desc", default="FlareSolverr service URL"),
                setting_type=SettingType.TEXT,
                default="http://localhost:8191",
                current=config.cloudflare.flaresolverr_url,
                placeholder="http://localhost:8191",
                section=SettingsSection.CLOUDFLARE,
            ))
            self._add_setting(SettingDefinition(
                key="cloudflare.playwright_enabled",
                label=t("settings.cloudflare.playwright_enabled", default="Enable Playwright"),
                description=t("settings.cloudflare.playwright_enabled.desc", default="Enable Playwright for bypass"),
                setting_type=SettingType.BOOL,
                default=True,
                current=config.cloudflare.playwright_enabled,
                section=SettingsSection.CLOUDFLARE,
            ))
            self._add_setting(SettingDefinition(
                key="cloudflare.playwright_headless",
                label=t("settings.cloudflare.playwright_headless", default="Playwright Headless"),
                description=t("settings.cloudflare.playwright_headless.desc", default="Run Playwright in headless mode"),
                setting_type=SettingType.BOOL,
                default=True,
                current=config.cloudflare.playwright_headless,
                section=SettingsSection.CLOUDFLARE,
            ))

            # Section LOGGING
            self._add_setting(SettingDefinition(
                key="logging.level",
                label=t("settings.logging.level", default="Log Level"),
                description=t("settings.logging.level.desc", default="Logging level"),
                setting_type=SettingType.SELECT,
                default="INFO",
                current=config.logging.level,
                choices=["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                section=SettingsSection.LOGGING,
            ))
            self._add_setting(SettingDefinition(
                key="logging.format",
                label=t("settings.logging.format", default="Log Format"),
                description=t("settings.logging.format.desc", default="Logging format"),
                setting_type=SettingType.SELECT,
                default="text",
                current=config.logging.format,
                choices=["text", "rich", "json", "simple"],
                section=SettingsSection.LOGGING,
            ))
            self._add_setting(SettingDefinition(
                key="logging.rotation",
                label=t("settings.logging.rotation", default="Log Rotation"),
                description=t("settings.logging.rotation.desc", default="Log file rotation size"),
                setting_type=SettingType.TEXT,
                default="10 MB",
                current=config.logging.rotation,
                placeholder="10 MB",
                section=SettingsSection.LOGGING,
            ))
            self._add_setting(SettingDefinition(
                key="logging.retention",
                label=t("settings.logging.retention", default="Log Retention"),
                description=t("settings.logging.retention.desc", default="Log file retention period"),
                setting_type=SettingType.TEXT,
                default="7 days",
                current=config.logging.retention,
                placeholder="7 days",
                section=SettingsSection.LOGGING,
            ))

            # Section STORAGE
            self._add_setting(SettingDefinition(
                key="storage.cache_ttl",
                label=t("settings.storage.cache_ttl", default="Cache TTL (s)"),
                description=t("settings.storage.cache_ttl.desc", default="Cache time-to-live in seconds"),
                setting_type=SettingType.NUMBER,
                default=86400.0,
                current=config.storage.cache_ttl,
                min_value=60.0,
                max_value=604800.0,
                section=SettingsSection.STORAGE,
            ))
            self._add_setting(SettingDefinition(
                key="storage.max_cache_size_mb",
                label=t("settings.storage.max_cache_size_mb", default="Max Cache Size (MB)"),
                description=t("settings.storage.max_cache_size_mb.desc", default="Maximum cache size in MB"),
                setting_type=SettingType.NUMBER,
                default=500,
                current=config.storage.max_cache_size_mb,
                min_value=10,
                max_value=10000,
                section=SettingsSection.STORAGE,
            ))
            self._add_setting(SettingDefinition(
                key="storage.cleanup_interval",
                label=t("settings.storage.cleanup_interval", default="Cleanup Interval (s)"),
                description=t("settings.storage.cleanup_interval.desc", default="Interval between cache cleanups"),
                setting_type=SettingType.NUMBER,
                default=3600.0,
                current=config.storage.cleanup_interval,
                min_value=60.0,
                max_value=86400.0,
                section=SettingsSection.STORAGE,
            ))

            # Section INTERFACE
            self._add_setting(SettingDefinition(
                key="interface.web_enabled",
                label=t("settings.interface.web_enabled", default="Enable Web UI"),
                description=t("settings.interface.web_enabled.desc", default="Enable web interface"),
                setting_type=SettingType.BOOL,
                default=False,
                current=config.interface.web_enabled,
                section=SettingsSection.INTERFACE,
            ))
            self._add_setting(SettingDefinition(
                key="interface.web_port",
                label=t("settings.interface.web_port", default="Web UI Port"),
                description=t("settings.interface.web_port.desc", default="Port for web interface"),
                setting_type=SettingType.NUMBER,
                default=8080,
                current=config.interface.web_port,
                min_value=1024,
                max_value=65535,
                section=SettingsSection.INTERFACE,
            ))
            self._add_setting(SettingDefinition(
                key="interface.gui_enabled",
                label=t("settings.interface.gui_enabled", default="Enable GUI"),
                description=t("settings.interface.gui_enabled.desc", default="Enable graphical interface"),
                setting_type=SettingType.BOOL,
                default=False,
                current=config.interface.gui_enabled,
                section=SettingsSection.INTERFACE,
            ))

            # Actions
            self._add_setting(SettingDefinition(
                key="action.export_config",
                label=t("settings.action.export_config", default="Export Configuration"),
                description=t("settings.action.export_config.desc", default="Export configuration to file"),
                setting_type=SettingType.ACTION,
                section=SettingsSection.APPLICATION,
            ))
            self._add_setting(SettingDefinition(
                key="action.import_config",
                label=t("settings.action.import_config", default="Import Configuration"),
                description=t("settings.action.import_config.desc", default="Import configuration from file"),
                setting_type=SettingType.ACTION,
                section=SettingsSection.APPLICATION,
            ))
            self._add_setting(SettingDefinition(
                key="action.reset_all",
                label=t("settings.action.reset_all", default="Reset All to Defaults"),
                description=t("settings.action.reset_all.desc", default="Reset all settings to default values"),
                setting_type=SettingType.ACTION,
                section=SettingsSection.APPLICATION,
            ))

        def _add_setting(self, definition: SettingDefinition) -> None:
            """Ajoute une définition de paramètre.

            Args:
                definition: Définition du paramètre.
            """
            self._state.definitions[definition.key] = definition

        def compose(self) -> ComposeResult:
            """Compose l'écran."""
            yield Header()
            with Horizontal(id="settings-container"):
                with Vertical(id="settings-sidebar"):
                    yield Static(
                        t("settings.title", default="Settings"),
                        classes="section-title",
                    )
                    yield ListView(id="sections-list")
                with VerticalScroll(id="settings-content"):
                    yield Static(id="section-title", classes="section-title")
                    yield Vertical(id="settings-list")
            with Horizontal(id="settings-footer"):
                yield Button(
                    t("settings.button.reset", default="Reset"),
                    id="btn-reset",
                    variant="default",
                )
                yield Button(
                    t("settings.button.cancel", default="Cancel"),
                    id="btn-cancel",
                    variant="error",
                )
                yield Button(
                    t("settings.button.save", default="Save"),
                    id="btn-save",
                    variant="success",
                )
            yield Footer()

        def on_mount(self) -> None:
            """Appelé lorsque l'écran est monté."""
            self._populate_sidebar()
            self._populate_content()

        def _populate_sidebar(self) -> None:
            """Remplit la sidebar avec les sections."""
            sidebar = self.query_one("#sections-list", ListView)
            sidebar.clear()

            for section in SettingsSection:
                # Compter les paramètres modifiés dans cette section
                modified_count = sum(
                    1 for d in self._state.definitions.values()
                    if d.section == section and d.modified
                )
                item = SectionItem(section, modified_count=modified_count)
                sidebar.append(item)

            # Sélectionner la première section
            if sidebar.children:
                sidebar.index = 0

        def _populate_content(self) -> None:
            """Remplit le contenu avec les paramètres de la section courante."""
            title = self.query_one("#section-title", Static)
            title.update(f"{self.current_section.icon} {self.current_section.label}")

            content = self.query_one("#settings-list", Vertical)
            content.clear()

            # Filtrer les paramètres de la section courante
            section_definitions = [
                d for d in self._state.definitions.values()
                if d.section == self.current_section
            ]

            for definition in section_definitions:
                widget = self._create_setting_widget(definition)
                if widget is not None:
                    # Ajouter la description si présente
                    if definition.description:
                        content.mount(Static(definition.description, classes="setting-description"))
                    content.mount(widget)

        def _create_setting_widget(self, definition: SettingDefinition) -> Widget | None:
            """Crée le widget approprié pour un paramètre.

            Args:
                definition: Définition du paramètre.

            Returns:
                Widget Textual ou None.
            """
            if definition.setting_type == SettingType.BOOL:
                return BoolSetting(definition, id=f"setting-{definition.key}")
            elif definition.setting_type == SettingType.SELECT:
                return SelectSetting(definition, id=f"setting-{definition.key}")
            elif definition.setting_type == SettingType.TEXT:
                return TextSetting(definition, id=f"setting-{definition.key}")
            elif definition.setting_type == SettingType.NUMBER:
                return NumberSetting(definition, id=f"setting-{definition.key}")
            elif definition.setting_type == SettingType.PATH:
                return PathSetting(definition, id=f"setting-{definition.key}")
            elif definition.setting_type == SettingType.ACTION:
                callback = self._get_action_callback(definition.key)
                return ActionSetting(
                    definition,
                    action_callback=callback,
                    id=f"setting-{definition.key}",
                )
            return None

        def _get_action_callback(self, key: str) -> Callable[[], None] | None:
            """Retourne le callback pour une action.

            Args:
                key: Clé de l'action.

            Returns:
                Fonction callback ou None.
            """
            if key == "action.export_config":
                return self._action_export_config
            elif key == "action.import_config":
                return self._action_import_config
            elif key == "action.reset_all":
                return self._action_reset_all
            return None

        def _action_export_config(self) -> None:
            """Action : exporter la configuration."""
            paths = get_paths()
            export_path = paths.config_dir / f"config_export_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.yaml"
            try:
                config = get_config()
                atomic_write(export_path, config.to_yaml())
                self.notify(
                    t("settings.notify.export_success", default="Configuration exported to {path}", path=str(export_path)),
                    severity="information",
                )
                logger.info("Configuration exportée vers: {}", export_path)
            except Exception as e:
                self.notify(
                    t("settings.notify.export_failed", default="Export failed: {error}", error=str(e)),
                    severity="error",
                )
                logger.error("Échec de l'export de configuration: {}", e)

        def _action_import_config(self) -> None:
            """Action : importer la configuration."""
            self.notify(
                t("settings.notify.import_not_implemented", default="Import not yet implemented"),
                severity="warning",
            )

        def _action_reset_all(self) -> None:
            """Action : réinitialiser tous les paramètres."""
            # Réinitialiser toutes les définitions à leurs valeurs par défaut
            for definition in self._state.definitions.values():
                if definition.default is not None:
                    definition.current = definition.default
                    definition.modified = True

            self._state.has_unsaved_changes = True
            self._populate_content()
            self.notify(
                t("settings.notify.reset_success", default="All settings reset to defaults"),
                severity="information",
            )
            self.post_message(SettingsReset())

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            """Gère la sélection d'une section dans la sidebar."""
            if isinstance(event.item, SectionItem):
                self.current_section = event.item.section
                self._populate_content()

        def on_setting_changed(self, event: SettingChanged) -> None:
            """Gère le changement d'un paramètre."""
            self._state.mark_modified(event.key)
            self._populate_sidebar()  # Mettre à jour les compteurs

        def on_setting_action(self, event: SettingAction) -> None:
            """Gère une action de paramètre."""
            logger.debug("Action déclenchée: {}", event.key)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            """Gère les clics sur les boutons du footer."""
            if event.button.id == "btn-save":
                self.action_save()
            elif event.button.id == "btn-cancel":
                self.action_cancel()
            elif event.button.id == "btn-reset":
                self._action_reset_all()

        # =====================================================================
        # ACTIONS — Bindings clavier
        # =====================================================================

        def action_save(self) -> None:
            """Action : sauvegarder les paramètres."""
            try:
                self._save_settings()
                self.notify(
                    t("settings.notify.save_success", default="Settings saved successfully"),
                    severity="information",
                )
                self.app.pop_screen()
            except Exception as e:
                self.notify(
                    t("settings.notify.save_failed", default="Save failed: {error}", error=str(e)),
                    severity="error",
                )
                logger.error("Échec de la sauvegarde des paramètres: {}", e)

        def action_cancel(self) -> None:
            """Action : annuler et fermer l'écran."""
            if self._state.has_unsaved_changes:
                # TODO: Afficher un dialogue de confirmation
                pass
            self.app.pop_screen()

        def action_reset(self) -> None:
            """Action : réinitialiser tous les paramètres."""
            self._action_reset_all()

        def action_next_section(self) -> None:
            """Action : passer à la section suivante."""
            sections = list(SettingsSection)
            current_index = sections.index(self.current_section)
            next_index = (current_index + 1) % len(sections)
            self.current_section = sections[next_index]
            self._populate_content()

            # Mettre à jour la sidebar
            sidebar = self.query_one("#sections-list", ListView)
            sidebar.index = next_index

        def action_prev_section(self) -> None:
            """Action : passer à la section précédente."""
            sections = list(SettingsSection)
            current_index = sections.index(self.current_section)
            prev_index = (current_index - 1) % len(sections)
            self.current_section = sections[prev_index]
            self._populate_content()

            # Mettre à jour la sidebar
            sidebar = self.query_one("#sections-list", ListView)
            sidebar.index = prev_index

        # =====================================================================
        # MÉTHODES INTERNES — Sauvegarde
        # =====================================================================

        def _save_settings(self) -> None:
            """Sauvegarde les paramètres dans le fichier de configuration.

            Raises:
                SettingsSaveError: Si la sauvegarde échoue.
                SettingsValidationError: Si un paramètre est invalide.
            """
            # Valider tous les paramètres modifiés
            for definition in self._state.definitions.values():
                if definition.modified and definition.setting_type != SettingType.ACTION:
                    if not definition.validate(definition.current):
                        raise SettingsValidationError(
                            definition.key,
                            definition.current,
                            "Validation failed",
                        )

            # Construire la nouvelle configuration
            config = get_config()
            new_config = self._build_new_config(config)

            # Sauvegarder de manière atomique
            paths = get_paths()
            config_path = paths.config_file

            try:
                atomic_write(config_path, new_config.to_yaml())
            except Exception as e:
                raise SettingsSaveError(config_path, str(e)) from e

            # Recharger la configuration
            manager = get_config_manager()
            manager.load()

            # Émettre un événement
            event_bus = get_event_bus()
            asyncio.create_task(event_bus.emit(
                EventType.CONFIG_CHANGED,
                payload={"path": str(config_path)},
                source="interfaces.cli.settings",
            ))

            # Mettre à jour l'état
            self._state.last_saved_at = datetime.now(UTC)
            self._state.reset_modified()

            self.post_message(SettingsSaved(config_path))
            logger.info("Paramètres sauvegardés: {}", config_path)

        def _build_new_config(self, base_config: NexusDLConfig) -> NexusDLConfig:
            """Construit une nouvelle configuration à partir des paramètres modifiés.

            Args:
                base_config: Configuration de base.

            Returns:
                Nouvelle configuration.
            """
            data = base_config.model_dump(mode="json")

            # Appliquer les modifications
            for definition in self._state.definitions.values():
                if not definition.modified or definition.setting_type == SettingType.ACTION:
                    continue

                # Naviguer dans le dictionnaire
                keys = definition.key.split(".")
                current = data
                for key in keys[:-1]:
                    if key not in current:
                        current[key] = {}
                    current = current[key]

                # Définir la nouvelle valeur
                current[keys[-1]] = definition.current

            # Valider et construire la nouvelle configuration
            return NexusDLConfig.model_validate(data)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "SettingsError",
    "SettingsValidationError",
    "SettingsSaveError",
    # Enums
    "SettingsSection",
    "SettingType",
    # Modèles
    "SettingDefinition",
    "SettingsState",
    # Écran principal
    "SettingsScreen" if TEXTUAL_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
