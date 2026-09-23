"""Configuration spécifique à l'interface CLI de NexusDL.

Ce module contient toute la configuration spécifique à l'interface CLI,
séparée de la configuration globale (core/config.py). Il gère :

    - Thèmes et styles CSS pour Textual
    - Raccourcis clavier par défaut
    - Configuration des écrans et widgets
    - Constantes spécifiques à la CLI
    - Codes de sortie

**Architecture** :
    CLIConfig (Pydantic Settings)
        ├── theme: ThemeConfig
        ├── keybindings: KeybindingsConfig
        ├── screens: ScreensConfig
        ├── widgets: WidgetsConfig
        └── display: DisplayConfig

**Utilisation** :
    >>> from nexusdl.interfaces.cli.config import cli_config
    >>> print(cli_config.theme.name)
    'nexusdl-dark'
    >>> print(cli_config.keybindings.search)
    's'

Intégration :
    - interfaces/cli/app.py     : utilise cli_config pour le thème CSS
    - interfaces/cli/commands.py: utilise les codes de sortie
    - core/config.py            : configuration globale (parent)
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# CONSTANTES — Codes de sortie
# ============================================================================


EXIT_SUCCESS: Final[int] = 0
EXIT_ERROR: Final[int] = 1
EXIT_USAGE_ERROR: Final[int] = 2
EXIT_CONFIG_ERROR: Final[int] = 3
EXIT_DEPENDENCY_ERROR: Final[int] = 4
EXIT_INTERRUPTED: Final[int] = 130


# ============================================================================
# ENUMS — Thèmes et modes
# ============================================================================


class CLITheme(str, Enum):
    """Thèmes disponibles pour l'interface CLI.

    Attributes:
        NEXUSDL_DARK: Thème sombre NexusDL (défaut).
        NEXUSDL_LIGHT: Thème clair NexusDL.
        SYSTEM: Utiliser le thème du système.
        AUTO: Détection automatique.
    """

    NEXUSDL_DARK = "nexusdl-dark"
    NEXUSDL_LIGHT = "nexusdl-light"
    SYSTEM = "system"
    AUTO = "auto"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            CLITheme.NEXUSDL_DARK: "NexusDL Dark",
            CLITheme.NEXUSDL_LIGHT: "NexusDL Light",
            CLITheme.SYSTEM: "System",
            CLITheme.AUTO: "Auto",
        }[self]


class DisplayMode(str, Enum):
    """Mode d'affichage de l'interface CLI.

    Attributes:
        FULL: Interface complète avec tous les écrans.
        COMPACT: Interface compacte (moins d'informations).
        MINIMAL: Interface minimale (texte seulement).
    """

    FULL = "full"
    COMPACT = "compact"
    MINIMAL = "minimal"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            DisplayMode.FULL: "Full",
            DisplayMode.COMPACT: "Compact",
            DisplayMode.MINIMAL: "Minimal",
        }[self]


# ============================================================================
# MODÈLES PYDANTIC — Configuration
# ============================================================================


class ThemeConfig(BaseModel):
    """Configuration du thème.

    Attributes:
        name: Nom du thème.
        primary: Couleur primaire (hex).
        secondary: Couleur secondaire (hex).
        accent: Couleur d'accent (hex).
        success: Couleur de succès (hex).
        warning: Couleur d'avertissement (hex).
        error: Couleur d'erreur (hex).
        background: Couleur de fond (hex).
        surface: Couleur de surface (hex).
        text: Couleur du texte (hex).
        text_muted: Couleur du texte atténué (hex).
    """

    name: str = Field(default=CLITheme.NEXUSDL_DARK.value, description="Nom du thème.")
    primary: str = Field(default="#6366f1", description="Couleur primaire.")
    secondary: str = Field(default="#8b5cf6", description="Couleur secondaire.")
    accent: str = Field(default="#ec4899", description="Couleur d'accent.")
    success: str = Field(default="#10b981", description="Couleur de succès.")
    warning: str = Field(default="#f59e0b", description="Couleur d'avertissement.")
    error: str = Field(default="#ef4444", description="Couleur d'erreur.")
    background: str = Field(default="#0f172a", description="Couleur de fond.")
    surface: str = Field(default="#1e293b", description="Couleur de surface.")
    text: str = Field(default="#f1f5f9", description="Couleur du texte.")
    text_muted: str = Field(default="#94a3b8", description="Couleur du texte atténué.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class KeybindingsConfig(BaseModel):
    """Configuration des raccourcis clavier.

    Attributes:
        help: Raccourci pour l'aide.
        search: Raccourci pour la recherche.
        library: Raccourci pour la bibliothèque.
        downloads: Raccourci pour les téléchargements.
        settings: Raccourci pour les paramètres.
        logs: Raccourci pour les logs.
        refresh: Raccourci pour rafraîchir.
        quit: Raccourci pour quitter.
        force_quit: Raccourci pour quitter forcé.
        reload_config: Raccourci pour recharger la configuration.
    """

    help: str = Field(default="f1", description="Raccourci pour l'aide.")
    search: str = Field(default="s", description="Raccourci pour la recherche.")
    library: str = Field(default="l", description="Raccourci pour la bibliothèque.")
    downloads: str = Field(default="d", description="Raccourci pour les téléchargements.")
    settings: str = Field(default="p", description="Raccourci pour les paramètres.")
    logs: str = Field(default="g", description="Raccourci pour les logs.")
    refresh: str = Field(default="r", description="Raccourci pour rafraîchir.")
    quit: str = Field(default="q", description="Raccourci pour quitter.")
    force_quit: str = Field(default="ctrl+q", description="Raccourci pour quitter forcé.")
    reload_config: str = Field(default="ctrl+r", description="Raccourci pour recharger la config.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ScreenConfig(BaseModel):
    """Configuration d'un écran.

    Attributes:
        enabled: Si l'écran est activé.
        default_sort: Tri par défaut.
        default_filter: Filtre par défaut.
        page_size: Taille de page.
    """

    enabled: bool = Field(default=True, description="Si l'écran est activé.")
    default_sort: str = Field(default="name", description="Tri par défaut.")
    default_filter: str = Field(default="all", description="Filtre par défaut.")
    page_size: int = Field(default=20, ge=1, le=100, description="Taille de page.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ScreensConfig(BaseModel):
    """Configuration de tous les écrans.

    Attributes:
        main: Configuration de l'écran principal.
        search: Configuration de l'écran de recherche.
        library: Configuration de l'écran de bibliothèque.
        downloads: Configuration de l'écran de téléchargements.
        settings: Configuration de l'écran de paramètres.
        logs: Configuration de l'écran de logs.
        help: Configuration de l'écran d'aide.
    """

    main: ScreenConfig = Field(default_factory=ScreenConfig, description="Écran principal.")
    search: ScreenConfig = Field(
        default_factory=lambda: ScreenConfig(default_sort="relevance"),
        description="Écran de recherche.",
    )
    library: ScreenConfig = Field(
        default_factory=lambda: ScreenConfig(default_sort="title"),
        description="Écran de bibliothèque.",
    )
    downloads: ScreenConfig = Field(
        default_factory=lambda: ScreenConfig(default_sort="date_added"),
        description="Écran de téléchargements.",
    )
    settings: ScreenConfig = Field(description="Écran de paramètres.")
    logs: ScreenConfig = Field(
        default_factory=lambda: ScreenConfig(default_filter="info"),
        description="Écran de logs.",
    )
    help: ScreenConfig = Field(description="Écran d'aide.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class WidgetConfig(BaseModel):
    """Configuration des widgets.

    Attributes:
        progress_bar_style: Style de la barre de progression.
        progress_bar_width: Largeur de la barre de progression.
        manga_card_layout: Layout des cartes manga.
        chapter_list_mode: Mode de la liste de chapitres.
        site_selector_mode: Mode du sélecteur de sites.
        log_viewer_mode: Mode du viewer de logs.
    """

    progress_bar_style: str = Field(default="block", description="Style de la barre de progression.")
    progress_bar_width: int = Field(default=40, ge=10, le=200, description="Largeur de la barre.")
    manga_card_layout: str = Field(default="vertical", description="Layout des cartes manga.")
    chapter_list_mode: str = Field(default="normal", description="Mode de la liste de chapitres.")
    site_selector_mode: str = Field(default="multiple", description="Mode du sélecteur de sites.")
    log_viewer_mode: str = Field(default="normal", description="Mode du viewer de logs.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class DisplayConfig(BaseModel):
    """Configuration de l'affichage.

    Attributes:
        mode: Mode d'affichage.
        show_header: Afficher l'en-tête.
        show_footer: Afficher le pied de page.
        show_clock: Afficher l'horloge.
        show_notifications: Afficher les notifications.
        notification_timeout: Durée des notifications (secondes).
        animation_enabled: Activer les animations.
        animation_speed: Vitesse des animations (ms).
    """

    mode: DisplayMode = Field(default=DisplayMode.FULL, description="Mode d'affichage.")
    show_header: bool = Field(default=True, description="Afficher l'en-tête.")
    show_footer: bool = Field(default=True, description="Afficher le pied de page.")
    show_clock: bool = Field(default=True, description="Afficher l'horloge.")
    show_notifications: bool = Field(default=True, description="Afficher les notifications.")
    notification_timeout: int = Field(default=5, ge=1, le=60, description="Durée des notifications.")
    animation_enabled: bool = Field(default=True, description="Activer les animations.")
    animation_speed: int = Field(default=100, ge=50, le=1000, description="Vitesse des animations.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class CLIConfig(BaseModel):
    """Configuration complète de l'interface CLI.

    Attributes:
        theme: Configuration du thème.
        keybindings: Configuration des raccourcis clavier.
        screens: Configuration des écrans.
        widgets: Configuration des widgets.
        display: Configuration de l'affichage.
    """

    theme: ThemeConfig = Field(default_factory=ThemeConfig, description="Configuration du thème.")
    keybindings: KeybindingsConfig = Field(
        default_factory=KeybindingsConfig,
        description="Configuration des raccourcis.",
    )
    screens: ScreensConfig = Field(
        default_factory=ScreensConfig,
        description="Configuration des écrans.",
    )
    widgets: WidgetConfig = Field(
        default_factory=WidgetConfig,
        description="Configuration des widgets.",
    )
    display: DisplayConfig = Field(
        default_factory=DisplayConfig,
        description="Configuration de l'affichage.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# THÈMES CSS — Styles Textual
# ============================================================================


THEME_CSS_NEXUSDL_DARK: Final[str] = """
/* ============================================================================
   NEXUSDL — Thème Dark
   ============================================================================ */

/* Palette de couleurs */
$primary: #6366f1;
$secondary: #8b5cf6;
$accent: #ec4899;
$success: #10b981;
$warning: #f59e0b;
$error: #ef4444;
$info: #3b82f6;

/* Couleurs de fond */
$background: #0f172a;
$surface: #1e293b;
$primary-background: #1e1b4b;
$success-background: #064e3b;
$warning-background: #78350f;
$error-background: #7f1d1d;

/* Couleurs de texte */
$text: #f1f5f9;
$text-muted: #94a3b8;
$text-disabled: #64748b;

/* Application */
Screen {
    background: $background;
    color: $text;
}

Header {
    background: $primary-background;
    color: $text;
    dock: top;
}

Footer {
    background: $surface;
    color: $text-muted;
    dock: bottom;
}

/* Boutons */
Button {
    margin: 0 1;
}

Button.-primary {
    background: $primary;
    color: $text;
}

Button.-success {
    background: $success;
    color: $text;
}

Button.-warning {
    background: $warning;
    color: $text;
}

Button.-error {
    background: $error;
    color: $text;
}

/* Inputs */
Input {
    border: tall $primary;
    background: $surface;
    color: $text;
}

Input:focus {
    border: tall $accent;
}

/* Select */
Select {
    border: tall $primary;
    background: $surface;
    color: $text;
}

/* ListView */
ListView {
    background: $surface;
}

ListItem {
    padding: 0 1;
}

ListItem:hover {
    background: $primary-background;
}

ListItem.-selected {
    background: $primary 30%;
    border-left: thick $primary;
}

/* Labels */
Label {
    color: $text;
}

/* Static */
Static {
    color: $text;
}

/* Notifications */
Notification {
    background: $surface;
    border: tall $primary;
}

Notification.-information {
    border: tall $info;
}

Notification.-warning {
    border: tall $warning;
}

Notification.-error {
    border: tall $error;
}
"""


THEME_CSS_NEXUSDL_LIGHT: Final[str] = """
/* ============================================================================
   NEXUSDL — Thème Light
   ============================================================================ */

/* Palette de couleurs */
$primary: #4f46e5;
$secondary: #7c3aed;
$accent: #db2777;
$success: #059669;
$warning: #d97706;
$error: #dc2626;
$info: #2563eb;

/* Couleurs de fond */
$background: #ffffff;
$surface: #f8fafc;
$primary-background: #eef2ff;
$success-background: #d1fae5;
$warning-background: #fef3c7;
$error-background: #fee2e2;

/* Couleurs de texte */
$text: #0f172a;
$text-muted: #64748b;
$text-disabled: #94a3b8;

/* Application */
Screen {
    background: $background;
    color: $text;
}

Header {
    background: $primary-background;
    color: $text;
    dock: top;
}

Footer {
    background: $surface;
    color: $text-muted;
    dock: bottom;
}

/* Boutons */
Button {
    margin: 0 1;
}

Button.-primary {
    background: $primary;
    color: white;
}

Button.-success {
    background: $success;
    color: white;
}

Button.-warning {
    background: $warning;
    color: white;
}

Button.-error {
    background: $error;
    color: white;
}

/* Inputs */
Input {
    border: tall $primary;
    background: white;
    color: $text;
}

Input:focus {
    border: tall $accent;
}

/* Select */
Select {
    border: tall $primary;
    background: white;
    color: $text;
}

/* ListView */
ListView {
    background: white;
}

ListItem {
    padding: 0 1;
}

ListItem:hover {
    background: $primary-background;
}

ListItem.-selected {
    background: $primary 20%;
    border-left: thick $primary;
}

/* Labels */
Label {
    color: $text;
}

/* Static */
Static {
    color: $text;
}

/* Notifications */
Notification {
    background: white;
    border: tall $primary;
}

Notification.-information {
    border: tall $info;
}

Notification.-warning {
    border: tall $warning;
}

Notification.-error {
    border: tall $error;
}
"""


# ============================================================================
# INSTANCE GLOBALE
# ============================================================================


_cli_config: CLIConfig | None = None


def get_cli_config() -> CLIConfig:
    """Retourne l'instance globale de la configuration CLI.

    Crée l'instance si elle n'existe pas encore.

    Returns:
        Instance de CLIConfig.
    """
    global _cli_config
    if _cli_config is None:
        _cli_config = CLIConfig()
    return _cli_config


def set_cli_config(config: CLIConfig) -> None:
    """Définit l'instance globale de la configuration CLI.

    Args:
        config: Nouvelle instance de CLIConfig.
    """
    global _cli_config
    _cli_config = config


def reset_cli_config() -> None:
    """Réinitialise l'instance globale de la configuration CLI."""
    global _cli_config
    _cli_config = None


# Alias pratique
cli_config: CLIConfig = property(lambda self: get_cli_config())  # type: ignore[assignment]


# ============================================================================
# FONCTIONS HELPERS
# ============================================================================


def get_theme_css(theme: CLITheme | str | None = None) -> str:
    """Retourne le CSS du thème spécifié.

    Args:
        theme: Thème à utiliser (défaut: thème de la config).

    Returns:
        Chaîne CSS du thème.
    """
    if theme is None:
        theme = get_cli_config().theme.name

    if isinstance(theme, str):
        try:
            theme = CLITheme(theme)
        except ValueError:
            theme = CLITheme.NEXUSDL_DARK

    if theme == CLITheme.NEXUSDL_LIGHT:
        return THEME_CSS_NEXUSDL_LIGHT
    else:
        return THEME_CSS_NEXUSDL_DARK


def get_keybinding(action: str) -> str:
    """Retourne le raccourci clavier pour une action.

    Args:
        action: Nom de l'action (help, search, library, etc.).

    Returns:
        Raccourci clavier.
    """
    config = get_cli_config()
    return getattr(config.keybindings, action, "")


def get_screen_config(screen_name: str) -> ScreenConfig:
    """Retourne la configuration d'un écran.

    Args:
        screen_name: Nom de l'écran (main, search, library, etc.).

    Returns:
        Configuration de l'écran.
    """
    config = get_cli_config()
    return getattr(config.screens, screen_name, ScreenConfig())


def is_screen_enabled(screen_name: str) -> bool:
    """Vérifie si un écran est activé.

    Args:
        screen_name: Nom de l'écran.

    Returns:
        True si l'écran est activé.
    """
    return get_screen_config(screen_name).enabled


def get_widget_config() -> WidgetConfig:
    """Retourne la configuration des widgets.

    Returns:
        Configuration des widgets.
    """
    return get_cli_config().widgets


def get_display_config() -> DisplayConfig:
    """Retourne la configuration de l'affichage.

    Returns:
        Configuration de l'affichage.
    """
    return get_cli_config().display


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "EXIT_SUCCESS",
    "EXIT_ERROR",
    "EXIT_USAGE_ERROR",
    "EXIT_CONFIG_ERROR",
    "EXIT_DEPENDENCY_ERROR",
    "EXIT_INTERRUPTED",
    # Enums
    "CLITheme",
    "DisplayMode",
    # Modèles de configuration
    "ThemeConfig",
    "KeybindingsConfig",
    "ScreenConfig",
    "ScreensConfig",
    "WidgetConfig",
    "DisplayConfig",
    "CLIConfig",
    # Thèmes CSS
    "THEME_CSS_NEXUSDL_DARK",
    "THEME_CSS_NEXUSDL_LIGHT",
    # Instance globale
    "cli_config",
    "get_cli_config",
    "set_cli_config",
    "reset_cli_config",
    # Fonctions helpers
    "get_theme_css",
    "get_keybinding",
    "get_screen_config",
    "is_screen_enabled",
    "get_widget_config",
    "get_display_config",
]
