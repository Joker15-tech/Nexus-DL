"""Système de thème pour l'interface graphique NexusDL.

Ce module centralise toute la configuration visuelle de l'interface GUI PyQt6,
fournissant un thème cyberpunk néon cohérent avec l'interface CLI. Il définit
les couleurs, polices, dimensions, et stylesheets pour tous les widgets Qt.

**Architecture** :
    theme.py
        ├── Couleurs (palette cyberpunk néon)
        │   ├── COLOR_PRIMARY (vert néon #00ff41)
        │   ├── COLOR_SECONDARY (cyan #00ffff)
        │   ├── COLOR_ACCENT (magenta #ff00ff)
        │   └── ... (20+ couleurs)
        │
        ├── Polices
        │   ├── FONT_FAMILY (JetBrains Mono)
        │   ├── FONT_SIZE_* (10px, 12px, 14px, 16px, 18px)
        │   └── FONT_WEIGHT_* (normal, bold)
        │
        ├── Dimensions
        │   ├── DIMENSION_* (marges, paddings, bordures)
        │   └── SIZE_* (tailles de widgets standards)
        │
        ├── Stylesheets
        │   ├── GLOBAL_STYLESHEET (appliqué à toute l'app)
        │   ├── WIDGET_STYLESHEETS (par type de widget)
        │   └── COMPONENT_STYLESHEETS (pour composants custom)
        │
        ├── Classe Theme
        │   ├── ThemeVariant (DARK, LIGHT, CYBERPUNK)
        │   ├── colors: dict[str, str]
        │   ├── fonts: dict[str, str]
        │   └── dimensions: dict[str, int]
        │
        ├── ThemeManager
        │   ├── current_theme: Theme
        │   ├── set_theme(variant)
        │   └── apply_to_app(qt_app)
        │
        └── Fonctions helpers
            ├── get_theme() -> Theme
            ├── apply_theme(qt_app, theme)
            ├── generate_stylesheet(widget_type) -> str
            └── get_color(name) -> str

**Utilisation** :
    >>> from nexusdl.interfaces.gui.theme import (
    ...     get_theme, apply_theme, ThemeVariant,
    ... )
    >>>
    >>> # Obtenir le thème actuel
    >>> theme = get_theme()
    >>> print(theme.colors["primary"])
    '#00ff41'
    >>>
    >>> # Appliquer le thème à l'application
    >>> from PyQt6.QtWidgets import QApplication
    >>> app = QApplication(sys.argv)
    >>> apply_theme(app, ThemeVariant.CYBERPUNK)
    >>>
    >>> # Générer un stylesheet pour un widget spécifique
    >>> button_style = generate_stylesheet("QPushButton")
    >>> my_button.setStyleSheet(button_style)

Intégration :
    - interfaces/gui/app.py      : applique le thème global
    - interfaces/gui/views/*     : utilise les couleurs et styles
    - interfaces/gui/components/*: utilise les stylesheets par composant
    - interfaces/cli/theme.tcss  : thème CLI (référence pour cohérence)
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Final

from loguru import logger


# ============================================================================
# COULEURS — Palette cyberpunk néon
# ============================================================================


# Couleurs primaires
COLOR_PRIMARY: Final[str] = "#00ff41"
COLOR_PRIMARY_DIM: Final[str] = "#00cc33"
COLOR_PRIMARY_BRIGHT: Final[str] = "#39ff14"
COLOR_PRIMARY_BG: Final[str] = "#001a0d"

# Couleurs secondaires
COLOR_SECONDARY: Final[str] = "#00ffff"
COLOR_SECONDARY_DIM: Final[str] = "#00cccc"
COLOR_SECONDARY_BRIGHT: Final[str] = "#0ff"
COLOR_SECONDARY_BG: Final[str] = "#001a1a"

# Couleurs d'accent
COLOR_ACCENT: Final[str] = "#ff00ff"
COLOR_ACCENT_DIM: Final[str] = "#cc00cc"
COLOR_ACCENT_BRIGHT: Final[str] = "#ff009d"
COLOR_ACCENT_BG: Final[str] = "#1a001a"

# Couleurs de statut
COLOR_SUCCESS: Final[str] = "#00ff41"
COLOR_WARNING: Final[str] = "#ffff00"
COLOR_ERROR: Final[str] = "#ff0040"
COLOR_INFO: Final[str] = "#00ffff"

# Couleurs de fond
COLOR_BACKGROUND: Final[str] = "#000000"
COLOR_BACKGROUND_ALT: Final[str] = "#0a0e27"
COLOR_SURFACE: Final[str] = "#0d1117"
COLOR_SURFACE_ALT: Final[str] = "#161b22"
COLOR_SURFACE_HOVER: Final[str] = "#1a1f2e"
COLOR_SURFACE_ACTIVE: Final[str] = "#1e293b"

# Couleurs de texte
COLOR_TEXT: Final[str] = "#00ff41"
COLOR_TEXT_BRIGHT: Final[str] = "#ffffff"
COLOR_TEXT_MUTED: Final[str] = "#00aaaa"
COLOR_TEXT_DIM: Final[str] = "#006666"
COLOR_TEXT_DISABLED: Final[str] = "#004444"
COLOR_TEXT_ACCENT: Final[str] = "#ff00ff"

# Couleurs de bordure
COLOR_BORDER: Final[str] = "#00ff41"
COLOR_BORDER_DIM: Final[str] = "#006622"
COLOR_BORDER_FOCUS: Final[str] = "#00ffff"
COLOR_BORDER_HOVER: Final[str] = "#ff00ff"
COLOR_BORDER_ACTIVE: Final[str] = "#00ff41"

# Couleurs d'ombre (simulées via border)
COLOR_SHADOW_PRIMARY: Final[str] = "#00ff41"
COLOR_SHADOW_SECONDARY: Final[str] = "#00ffff"
COLOR_SHADOW_ACCENT: Final[str] = "#ff00ff"


# ============================================================================
# POLICES — Configuration typographique
# ============================================================================


# Familles de polices (ordre de préférence)
FONT_FAMILY_PRIMARY: Final[str] = "'JetBrains Mono', 'Fira Code', 'Consolas', monospace"
FONT_FAMILY_SECONDARY: Final[str] = "'Inter', 'Segoe UI', 'Roboto', sans-serif"
FONT_FAMILY_MONO: Final[str] = "'JetBrains Mono', 'Fira Code', 'Consolas', monospace"

# Tailles de police
FONT_SIZE_XS: Final[int] = 10
FONT_SIZE_SM: Final[int] = 11
FONT_SIZE_BASE: Final[int] = 12
FONT_SIZE_MD: Final[int] = 13
FONT_SIZE_LG: Final[int] = 14
FONT_SIZE_XL: Final[int] = 16
FONT_SIZE_2XL: Final[int] = 18
FONT_SIZE_3XL: Final[int] = 20
FONT_SIZE_4XL: Final[int] = 24

# Poids de police
FONT_WEIGHT_NORMAL: Final[str] = "normal"
FONT_WEIGHT_MEDIUM: Final[str] = "500"
FONT_WEIGHT_BOLD: Final[str] = "bold"
FONT_WEIGHT_BLACK: Final[str] = "900"

# Styles de police
FONT_STYLE_NORMAL: Final[str] = "normal"
FONT_STYLE_ITALIC: Final[str] = "italic"


# ============================================================================
# DIMENSIONS — Espacements et tailles
# ============================================================================


# Marges
MARGIN_XS: Final[int] = 4
MARGIN_SM: Final[int] = 8
MARGIN_MD: Final[int] = 12
MARGIN_LG: Final[int] = 16
MARGIN_XL: Final[int] = 24
MARGIN_2XL: Final[int] = 32

# Paddings
PADDING_XS: Final[int] = 4
PADDING_SM: Final[int] = 6
PADDING_MD: Final[int] = 8
PADDING_LG: Final[int] = 12
PADDING_XL: Final[int] = 16
PADDING_2XL: Final[int] = 24

# Tailles de widgets standards
SIZE_BUTTON_HEIGHT: Final[int] = 32
SIZE_BUTTON_MIN_WIDTH: Final[int] = 80
SIZE_INPUT_HEIGHT: Final[int] = 32
SIZE_COMBO_HEIGHT: Final[int] = 32
SIZE_CHECKBOX: Final[int] = 20
SIZE_RADIO: Final[int] = 20
SIZE_ICON_SM: Final[int] = 16
SIZE_ICON_MD: Final[int] = 24
SIZE_ICON_LG: Final[int] = 32
SIZE_ICON_XL: Final[int] = 48

# Bordures
BORDER_WIDTH_THIN: Final[int] = 1
BORDER_WIDTH_NORMAL: Final[int] = 2
BORDER_WIDTH_THICK: Final[int] = 3
BORDER_WIDTH_HEAVY: Final[int] = 4

# Rayons de bordure
BORDER_RADIUS_NONE: Final[int] = 0
BORDER_RADIUS_SM: Final[int] = 2
BORDER_RADIUS_MD: Final[int] = 4
BORDER_RADIUS_LG: Final[int] = 6
BORDER_RADIUS_XL: Final[int] = 8
BORDER_RADIUS_FULL: Final[int] = 9999


# ============================================================================
# ENUMS — Variantes de thème
# ============================================================================


class ThemeVariant(str, Enum):
    """Variantes de thème disponibles.

    Attributes:
        CYBERPUNK: Thème cyberpunk néon (défaut).
        DARK: Thème sombre standard.
        LIGHT: Thème clair.
        HIGH_CONTRAST: Thème haute contraste.
    """

    CYBERPUNK = "cyberpunk"
    DARK = "dark"
    LIGHT = "light"
    HIGH_CONTRAST = "high_contrast"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ThemeVariant.CYBERPUNK: "Cyberpunk Neon",
            ThemeVariant.DARK: "Dark",
            ThemeVariant.LIGHT: "Light",
            ThemeVariant.HIGH_CONTRAST: "High Contrast",
        }[self]


# ============================================================================
# CLASSE — Theme
# ============================================================================


class Theme:
    """Configuration complète d'un thème.

    Attributes:
        variant: Variante du thème.
        colors: Dictionnaire des couleurs.
        fonts: Dictionnaire des polices.
        dimensions: Dictionnaire des dimensions.
    """

    def __init__(
        self,
        variant: ThemeVariant = ThemeVariant.CYBERPUNK,
    ) -> None:
        """Initialise le thème.

        Args:
            variant: Variante du thème.
        """
        self.variant = variant
        self.colors = self._get_colors(variant)
        self.fonts = self._get_fonts()
        self.dimensions = self._get_dimensions()

    def _get_colors(self, variant: ThemeVariant) -> dict[str, str]:
        """Retourne les couleurs pour une variante.

        Args:
            variant: Variante du thème.

        Returns:
            Dictionnaire des couleurs.
        """
        if variant == ThemeVariant.CYBERPUNK:
            return {
                "primary": COLOR_PRIMARY,
                "primary_dim": COLOR_PRIMARY_DIM,
                "primary_bright": COLOR_PRIMARY_BRIGHT,
                "primary_bg": COLOR_PRIMARY_BG,
                "secondary": COLOR_SECONDARY,
                "secondary_dim": COLOR_SECONDARY_DIM,
                "secondary_bright": COLOR_SECONDARY_BRIGHT,
                "secondary_bg": COLOR_SECONDARY_BG,
                "accent": COLOR_ACCENT,
                "accent_dim": COLOR_ACCENT_DIM,
                "accent_bright": COLOR_ACCENT_BRIGHT,
                "accent_bg": COLOR_ACCENT_BG,
                "success": COLOR_SUCCESS,
                "warning": COLOR_WARNING,
                "error": COLOR_ERROR,
                "info": COLOR_INFO,
                "background": COLOR_BACKGROUND,
                "background_alt": COLOR_BACKGROUND_ALT,
                "surface": COLOR_SURFACE,
                "surface_alt": COLOR_SURFACE_ALT,
                "surface_hover": COLOR_SURFACE_HOVER,
                "surface_active": COLOR_SURFACE_ACTIVE,
                "text": COLOR_TEXT,
                "text_bright": COLOR_TEXT_BRIGHT,
                "text_muted": COLOR_TEXT_MUTED,
                "text_dim": COLOR_TEXT_DIM,
                "text_disabled": COLOR_TEXT_DISABLED,
                "text_accent": COLOR_TEXT_ACCENT,
                "border": COLOR_BORDER,
                "border_dim": COLOR_BORDER_DIM,
                "border_focus": COLOR_BORDER_FOCUS,
                "border_hover": COLOR_BORDER_HOVER,
                "border_active": COLOR_BORDER_ACTIVE,
            }
        elif variant == ThemeVariant.DARK:
            # Thème sombre standard (moins néon)
            return {
                "primary": "#4f46e5",
                "primary_dim": "#4338ca",
                "primary_bright": "#6366f1",
                "primary_bg": "#1e1b4b",
                "secondary": "#06b6d4",
                "secondary_dim": "#0891b2",
                "secondary_bright": "#22d3ee",
                "secondary_bg": "#164e63",
                "accent": "#ec4899",
                "accent_dim": "#db2777",
                "accent_bright": "#f472b6",
                "accent_bg": "#831843",
                "success": "#10b981",
                "warning": "#f59e0b",
                "error": "#ef4444",
                "info": "#3b82f6",
                "background": "#0f172a",
                "background_alt": "#1e293b",
                "surface": "#1e293b",
                "surface_alt": "#334155",
                "surface_hover": "#475569",
                "surface_active": "#64748b",
                "text": "#f1f5f9",
                "text_bright": "#ffffff",
                "text_muted": "#94a3b8",
                "text_dim": "#64748b",
                "text_disabled": "#475569",
                "text_accent": "#f472b6",
                "border": "#475569",
                "border_dim": "#334155",
                "border_focus": "#6366f1",
                "border_hover": "#94a3b8",
                "border_active": "#4f46e5",
            }
        elif variant == ThemeVariant.LIGHT:
            # Thème clair
            return {
                "primary": "#4f46e5",
                "primary_dim": "#4338ca",
                "primary_bright": "#6366f1",
                "primary_bg": "#eef2ff",
                "secondary": "#0891b2",
                "secondary_dim": "#0e7490",
                "secondary_bright": "#06b6d4",
                "secondary_bg": "#cffafe",
                "accent": "#db2777",
                "accent_dim": "#be185d",
                "accent_bright": "#ec4899",
                "accent_bg": "#fce7f3",
                "success": "#059669",
                "warning": "#d97706",
                "error": "#dc2626",
                "info": "#2563eb",
                "background": "#ffffff",
                "background_alt": "#f8fafc",
                "surface": "#f8fafc",
                "surface_alt": "#f1f5f9",
                "surface_hover": "#e2e8f0",
                "surface_active": "#cbd5e1",
                "text": "#0f172a",
                "text_bright": "#000000",
                "text_muted": "#64748b",
                "text_dim": "#94a3b8",
                "text_disabled": "#cbd5e1",
                "text_accent": "#be185d",
                "border": "#cbd5e1",
                "border_dim": "#e2e8f0",
                "border_focus": "#6366f1",
                "border_hover": "#94a3b8",
                "border_active": "#4f46e5",
            }
        else:  # HIGH_CONTRAST
            return {
                "primary": "#00ff00",
                "primary_dim": "#00cc00",
                "primary_bright": "#33ff33",
                "primary_bg": "#001a00",
                "secondary": "#00ffff",
                "secondary_dim": "#00cccc",
                "secondary_bright": "#33ffff",
                "secondary_bg": "#001a1a",
                "accent": "#ff00ff",
                "accent_dim": "#cc00cc",
                "accent_bright": "#ff33ff",
                "accent_bg": "#1a001a",
                "success": "#00ff00",
                "warning": "#ffff00",
                "error": "#ff0000",
                "info": "#00ffff",
                "background": "#000000",
                "background_alt": "#0a0a0a",
                "surface": "#000000",
                "surface_alt": "#1a1a1a",
                "surface_hover": "#333333",
                "surface_active": "#4d4d4d",
                "text": "#ffffff",
                "text_bright": "#ffffff",
                "text_muted": "#cccccc",
                "text_dim": "#999999",
                "text_disabled": "#666666",
                "text_accent": "#ff00ff",
                "border": "#ffffff",
                "border_dim": "#666666",
                "border_focus": "#00ffff",
                "border_hover": "#ffff00",
                "border_active": "#00ff00",
            }

    def _get_fonts(self) -> dict[str, str]:
        """Retourne la configuration des polices.

        Returns:
            Dictionnaire des polices.
        """
        return {
            "family_primary": FONT_FAMILY_PRIMARY,
            "family_secondary": FONT_FAMILY_SECONDARY,
            "family_mono": FONT_FAMILY_MONO,
            "size_xs": str(FONT_SIZE_XS),
            "size_sm": str(FONT_SIZE_SM),
            "size_base": str(FONT_SIZE_BASE),
            "size_md": str(FONT_SIZE_MD),
            "size_lg": str(FONT_SIZE_LG),
            "size_xl": str(FONT_SIZE_XL),
            "size_2xl": str(FONT_SIZE_2XL),
            "size_3xl": str(FONT_SIZE_3XL),
            "size_4xl": str(FONT_SIZE_4XL),
            "weight_normal": FONT_WEIGHT_NORMAL,
            "weight_medium": FONT_WEIGHT_MEDIUM,
            "weight_bold": FONT_WEIGHT_BOLD,
            "weight_black": FONT_WEIGHT_BLACK,
            "style_normal": FONT_STYLE_NORMAL,
            "style_italic": FONT_STYLE_ITALIC,
        }

    def _get_dimensions(self) -> dict[str, int]:
        """Retourne la configuration des dimensions.

        Returns:
            Dictionnaire des dimensions.
        """
        return {
            "margin_xs": MARGIN_XS,
            "margin_sm": MARGIN_SM,
            "margin_md": MARGIN_MD,
            "margin_lg": MARGIN_LG,
            "margin_xl": MARGIN_XL,
            "margin_2xl": MARGIN_2XL,
            "padding_xs": PADDING_XS,
            "padding_sm": PADDING_SM,
            "padding_md": PADDING_MD,
            "padding_lg": PADDING_LG,
            "padding_xl": PADDING_XL,
            "padding_2xl": PADDING_2XL,
            "button_height": SIZE_BUTTON_HEIGHT,
            "button_min_width": SIZE_BUTTON_MIN_WIDTH,
            "input_height": SIZE_INPUT_HEIGHT,
            "combo_height": SIZE_COMBO_HEIGHT,
            "checkbox": SIZE_CHECKBOX,
            "radio": SIZE_RADIO,
            "icon_sm": SIZE_ICON_SM,
            "icon_md": SIZE_ICON_MD,
            "icon_lg": SIZE_ICON_LG,
            "icon_xl": SIZE_ICON_XL,
            "border_thin": BORDER_WIDTH_THIN,
            "border_normal": BORDER_WIDTH_NORMAL,
            "border_thick": BORDER_WIDTH_THICK,
            "border_heavy": BORDER_WIDTH_HEAVY,
            "radius_none": BORDER_RADIUS_NONE,
            "radius_sm": BORDER_RADIUS_SM,
            "radius_md": BORDER_RADIUS_MD,
            "radius_lg": BORDER_RADIUS_LG,
            "radius_xl": BORDER_RADIUS_XL,
            "radius_full": BORDER_RADIUS_FULL,
        }

    def get_color(self, name: str) -> str:
        """Retourne une couleur par son nom.

        Args:
            name: Nom de la couleur.

        Returns:
            Code hex de la couleur.

        Raises:
            KeyError: Si la couleur n'existe pas.
        """
        return self.colors[name]

    def get_font(self, name: str) -> str:
        """Retourne une police par son nom.

        Args:
            name: Nom de la police.

        Returns:
            Configuration de la police.

        Raises:
            KeyError: Si la police n'existe pas.
        """
        return self.fonts[name]

    def get_dimension(self, name: str) -> int:
        """Retourne une dimension par son nom.

        Args:
            name: Nom de la dimension.

        Returns:
            Valeur de la dimension en pixels.

        Raises:
            KeyError: Si la dimension n'existe pas.
        """
        return self.dimensions[name]


# ============================================================================
# GESTIONNAIRE — ThemeManager
# ============================================================================


class ThemeManager:
    """Gestionnaire de thème pour l'application.

    Gère le thème actif et permet de changer de thème à la volée.
    """

    _instance: ThemeManager | None = None
    _current_theme: Theme | None = None

    def __new__(cls) -> ThemeManager:
        """Singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialise le gestionnaire."""
        if self._current_theme is None:
            self._current_theme = Theme(ThemeVariant.CYBERPUNK)

    @property
    def current_theme(self) -> Theme:
        """Retourne le thème actuel."""
        if self._current_theme is None:
            self._current_theme = Theme(ThemeVariant.CYBERPUNK)
        return self._current_theme

    def set_theme(self, variant: ThemeVariant) -> None:
        """Change le thème actif.

        Args:
            variant: Variante du thème.
        """
        self._current_theme = Theme(variant)
        logger.info("Thème changé: {}", variant.value)

    def apply_to_app(self, qt_app: Any) -> None:
        """Applique le thème à une application Qt.

        Args:
            qt_app: Instance de QApplication.
        """
        from PyQt6.QtGui import QColor, QPalette

        theme = self.current_theme

        # Appliquer le stylesheet global
        qt_app.setStyleSheet(GLOBAL_STYLESHEET)

        # Configurer la palette
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(theme.colors["background"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(theme.colors["text"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(theme.colors["surface"]))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(theme.colors["surface_alt"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(theme.colors["text"]))
        palette.setColor(QPalette.ColorRole.Button, QColor(theme.colors["surface"]))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(theme.colors["text"]))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(theme.colors["primary"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(theme.colors["background"]))
        qt_app.setPalette(palette)

        logger.debug("Thème appliqué à l'application")


# Instance globale
_theme_manager: ThemeManager | None = None


def get_theme_manager() -> ThemeManager:
    """Retourne l'instance globale du ThemeManager.

    Returns:
        Instance de ThemeManager.
    """
    global _theme_manager
    if _theme_manager is None:
        _theme_manager = ThemeManager()
    return _theme_manager


def get_theme() -> Theme:
    """Retourne le thème actuel.

    Returns:
        Instance de Theme.
    """
    return get_theme_manager().current_theme


def set_theme(variant: ThemeVariant) -> None:
    """Change le thème actif.

    Args:
        variant: Variante du thème.
    """
    get_theme_manager().set_theme(variant)


def apply_theme(qt_app: Any, variant: ThemeVariant | None = None) -> None:
    """Applique le thème à une application Qt.

    Args:
        qt_app: Instance de QApplication.
        variant: Variante du thème (None = thème actuel).
    """
    manager = get_theme_manager()
    if variant is not None:
        manager.set_theme(variant)
    manager.apply_to_app(qt_app)


def get_color(name: str) -> str:
    """Retourne une couleur du thème actuel.

    Args:
        name: Nom de la couleur.

    Returns:
        Code hex de la couleur.
    """
    return get_theme().get_color(name)


# ============================================================================
# STYLESHEETS — Global et par widget
# ============================================================================


def _build_global_stylesheet() -> str:
    """Construit le stylesheet global.

    Returns:
        Stylesheet QSS complet.
    """
    theme = get_theme()
    c = theme.colors
    f = theme.fonts

    return f"""
/* ============================================================================
   NEXUSDL — Stylesheet Global
   Thème: {theme.variant.value}
   ============================================================================ */

/* Application */
QMainWindow {{
    background-color: {c['background']};
    color: {c['text']};
}}

QWidget {{
    background-color: {c['background']};
    color: {c['text']};
    font-family: {f['family_primary']};
    font-size: {f['size_base']}px;
}}

/* Menu Bar */
QMenuBar {{
    background-color: {c['surface']};
    color: {c['text']};
    border-bottom: {BORDER_WIDTH_NORMAL}px solid {c['primary']};
    padding: {PADDING_SM}px;
}}

QMenuBar::item {{
    padding: {PADDING_SM}px {PADDING_LG}px;
    border-radius: {BORDER_RADIUS_SM}px;
}}

QMenuBar::item:selected {{
    background-color: {c['primary_bg']};
    color: {c['primary']};
}}

QMenu {{
    background-color: {c['surface']};
    color: {c['text']};
    border: {BORDER_WIDTH_THIN}px solid {c['border_dim']};
    border-radius: {BORDER_RADIUS_MD}px;
    padding: {PADDING_SM}px;
}}

QMenu::item {{
    padding: {PADDING_SM}px {PADDING_XL}px;
    border-radius: {BORDER_RADIUS_SM}px;
}}

QMenu::item:selected {{
    background-color: {c['primary_bg']};
    color: {c['primary']};
}}

QMenu::separator {{
    height: {BORDER_WIDTH_THIN}px;
    background-color: {c['border_dim']};
    margin: {PADDING_SM}px {PADDING_MD}px;
}}

/* Status Bar */
QStatusBar {{
    background-color: {c['surface']};
    color: {c['text_muted']};
    border-top: {BORDER_WIDTH_NORMAL}px solid {c['border_dim']};
    padding: {PADDING_SM}px;
    font-size: {f['size_sm']}px;
}}

/* Tooltips */
QToolTip {{
    background-color: {c['surface']};
    color: {c['text']};
    border: {BORDER_WIDTH_THIN}px solid {c['primary']};
    border-radius: {BORDER_RADIUS_SM}px;
    padding: {PADDING_SM}px {PADDING_MD}px;
    font-size: {f['size_sm']}px;
}}

/* Scrollbars */
QScrollBar:vertical {{
    background-color: {c['surface']};
    width: {MARGIN_MD}px;
    border: none;
}}

QScrollBar::handle:vertical {{
    background-color: {c['border_dim']};
    border-radius: {BORDER_RADIUS_MD}px;
    min-height: 20px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {c['primary']};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}

QScrollBar:horizontal {{
    background-color: {c['surface']};
    height: {MARGIN_MD}px;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background-color: {c['border_dim']};
    border-radius: {BORDER_RADIUS_MD}px;
    min-width: 20px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {c['primary']};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

/* Message Box */
QMessageBox {{
    background-color: {c['surface']};
    color: {c['text']};
}}

QMessageBox QLabel {{
    color: {c['text']};
    font-size: {f['size_base']}px;
}}

QMessageBox QPushButton {{
    background-color: {c['surface_alt']};
    color: {c['text']};
    border: {BORDER_WIDTH_NORMAL}px solid {c['border_dim']};
    border-radius: {BORDER_RADIUS_MD}px;
    padding: {PADDING_SM}px {PADDING_XL}px;
    min-width: {SIZE_BUTTON_MIN_WIDTH}px;
}}

QMessageBox QPushButton:hover {{
    background-color: {c['primary_bg']};
    border-color: {c['primary']};
    color: {c['primary']};
}}
"""


GLOBAL_STYLESHEET: Final[str] = _build_global_stylesheet()


def generate_widget_stylesheet(widget_type: str) -> str:
    """Génère un stylesheet pour un type de widget spécifique.

    Args:
        widget_type: Type de widget (QPushButton, QLabel, etc.).

    Returns:
        Stylesheet QSS pour le widget.
    """
    theme = get_theme()
    c = theme.colors
    f = theme.fonts

    stylesheets = {
        "QPushButton": f"""
            QPushButton {{
                background-color: {c['surface_alt']};
                color: {c['text']};
                border: {BORDER_WIDTH_NORMAL}px solid {c['border_dim']};
                border-radius: {BORDER_RADIUS_MD}px;
                padding: {PADDING_SM}px {PADDING_LG}px;
                font-size: {f['size_base']}px;
                font-weight: {f['weight_medium']};
                min-height: {SIZE_BUTTON_HEIGHT}px;
            }}
            QPushButton:hover {{
                background-color: {c['surface_hover']};
                border-color: {c['border_hover']};
                color: {c['text_bright']};
            }}
            QPushButton:pressed {{
                background-color: {c['primary_bg']};
                border-color: {c['primary']};
                color: {c['primary']};
            }}
            QPushButton:disabled {{
                background-color: {c['surface']};
                color: {c['text_disabled']};
                border-color: {c['border_dim']};
            }}
            QPushButton:checked {{
                background-color: {c['primary_bg']};
                border-color: {c['primary']};
                color: {c['primary']};
                font-weight: {f['weight_bold']};
            }}
        """,
        "QLineEdit": f"""
            QLineEdit {{
                background-color: {c['surface_alt']};
                color: {c['text']};
                border: {BORDER_WIDTH_NORMAL}px solid {c['border_dim']};
                border-radius: {BORDER_RADIUS_MD}px;
                padding: {PADDING_SM}px {PADDING_MD}px;
                font-family: {f['family_mono']};
                font-size: {f['size_base']}px;
                min-height: {SIZE_INPUT_HEIGHT}px;
            }}
            QLineEdit:focus {{
                border-color: {c['border_focus']};
            }}
            QLineEdit::placeholder {{
                color: {c['text_dim']};
            }}
            QLineEdit:disabled {{
                background-color: {c['surface']};
                color: {c['text_disabled']};
            }}
        """,
        "QComboBox": f"""
            QComboBox {{
                background-color: {c['surface_alt']};
                color: {c['text']};
                border: {BORDER_WIDTH_THIN}px solid {c['border_dim']};
                border-radius: {BORDER_RADIUS_SM}px;
                padding: {PADDING_SM}px {PADDING_MD}px;
                font-size: {f['size_sm']}px;
                min-height: {SIZE_COMBO_HEIGHT}px;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {c['surface']};
                color: {c['text']};
                border: {BORDER_WIDTH_THIN}px solid {c['border_dim']};
                selection-background-color: {c['primary_bg']};
                selection-color: {c['primary']};
            }}
        """,
        "QLabel": f"""
            QLabel {{
                color: {c['text']};
                font-size: {f['size_base']}px;
            }}
        """,
        "QCheckBox": f"""
            QCheckBox {{
                color: {c['text']};
                spacing: {MARGIN_SM}px;
            }}
            QCheckBox::indicator {{
                width: {SIZE_CHECKBOX}px;
                height: {SIZE_CHECKBOX}px;
                border: {BORDER_WIDTH_NORMAL}px solid {c['border_dim']};
                border-radius: {BORDER_RADIUS_SM}px;
                background-color: {c['surface']};
            }}
            QCheckBox::indicator:checked {{
                background-color: {c['primary']};
                border-color: {c['primary']};
            }}
            QCheckBox::indicator:hover {{
                border-color: {c['border_hover']};
            }}
        """,
        "QListWidget": f"""
            QListWidget {{
                background-color: {c['surface']};
                border: {BORDER_WIDTH_THIN}px solid {c['border_dim']};
                border-radius: {BORDER_RADIUS_MD}px;
                color: {c['text']};
                font-family: {f['family_mono']};
                font-size: {f['size_sm']}px;
            }}
            QListWidget::item {{
                padding: {PADDING_SM}px {PADDING_MD}px;
                border-left: {BORDER_WIDTH_THICK}px solid transparent;
            }}
            QListWidget::item:hover {{
                background-color: {c['surface_hover']};
                color: {c['primary']};
                border-left-color: {c['border_hover']};
            }}
            QListWidget::item:selected {{
                background-color: {c['primary_bg']};
                color: {c['primary']};
                border-left-color: {c['primary']};
                font-weight: {f['weight_bold']};
            }}
        """,
        "QTableWidget": f"""
            QTableWidget {{
                background-color: {c['surface']};
                color: {c['text']};
                border: none;
                gridline-color: {c['border_dim']};
                font-family: {f['family_mono']};
                font-size: {f['size_sm']}px;
            }}
            QTableWidget::item {{
                padding: {PADDING_SM}px {PADDING_MD}px;
                border-bottom: {BORDER_WIDTH_THIN}px solid {c['border_dim']};
            }}
            QTableWidget::item:selected {{
                background-color: {c['primary_bg']};
                color: {c['primary']};
            }}
            QTableWidget::item:hover {{
                background-color: {c['surface_hover']};
            }}
            QHeaderView::section {{
                background-color: {c['surface_alt']};
                color: {c['text']};
                padding: {PADDING_SM}px;
                border: none;
                border-bottom: {BORDER_WIDTH_NORMAL}px solid {c['primary']};
                font-weight: {f['weight_bold']};
                font-size: {f['size_sm']}px;
            }}
            QHeaderView::section:hover {{
                background-color: {c['surface_hover']};
            }}
        """,
    }

    return stylesheets.get(widget_type, "")


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Couleurs
    "COLOR_PRIMARY",
    "COLOR_PRIMARY_DIM",
    "COLOR_PRIMARY_BRIGHT",
    "COLOR_PRIMARY_BG",
    "COLOR_SECONDARY",
    "COLOR_SECONDARY_DIM",
    "COLOR_SECONDARY_BRIGHT",
    "COLOR_SECONDARY_BG",
    "COLOR_ACCENT",
    "COLOR_ACCENT_DIM",
    "COLOR_ACCENT_BRIGHT",
    "COLOR_ACCENT_BG",
    "COLOR_SUCCESS",
    "COLOR_WARNING",
    "COLOR_ERROR",
    "COLOR_INFO",
    "COLOR_BACKGROUND",
    "COLOR_BACKGROUND_ALT",
    "COLOR_SURFACE",
    "COLOR_SURFACE_ALT",
    "COLOR_SURFACE_HOVER",
    "COLOR_SURFACE_ACTIVE",
    "COLOR_TEXT",
    "COLOR_TEXT_BRIGHT",
    "COLOR_TEXT_MUTED",
    "COLOR_TEXT_DIM",
    "COLOR_TEXT_DISABLED",
    "COLOR_TEXT_ACCENT",
    "COLOR_BORDER",
    "COLOR_BORDER_DIM",
    "COLOR_BORDER_FOCUS",
    "COLOR_BORDER_HOVER",
    "COLOR_BORDER_ACTIVE",
    # Polices
    "FONT_FAMILY_PRIMARY",
    "FONT_FAMILY_SECONDARY",
    "FONT_FAMILY_MONO",
    "FONT_SIZE_XS",
    "FONT_SIZE_SM",
    "FONT_SIZE_BASE",
    "FONT_SIZE_MD",
    "FONT_SIZE_LG",
    "FONT_SIZE_XL",
    "FONT_SIZE_2XL",
    "FONT_SIZE_3XL",
    "FONT_SIZE_4XL",
    "FONT_WEIGHT_NORMAL",
    "FONT_WEIGHT_MEDIUM",
    "FONT_WEIGHT_BOLD",
    "FONT_WEIGHT_BLACK",
    # Dimensions
    "MARGIN_XS",
    "MARGIN_SM",
    "MARGIN_MD",
    "MARGIN_LG",
    "MARGIN_XL",
    "MARGIN_2XL",
    "PADDING_XS",
    "PADDING_SM",
    "PADDING_MD",
    "PADDING_LG",
    "PADDING_XL",
    "PADDING_2XL",
    "SIZE_BUTTON_HEIGHT",
    "SIZE_BUTTON_MIN_WIDTH",
    "SIZE_INPUT_HEIGHT",
    "SIZE_COMBO_HEIGHT",
    "SIZE_CHECKBOX",
    "SIZE_RADIO",
    "SIZE_ICON_SM",
    "SIZE_ICON_MD",
    "SIZE_ICON_LG",
    "SIZE_ICON_XL",
    "BORDER_WIDTH_THIN",
    "BORDER_WIDTH_NORMAL",
    "BORDER_WIDTH_THICK",
    "BORDER_WIDTH_HEAVY",
    "BORDER_RADIUS_NONE",
    "BORDER_RADIUS_SM",
    "BORDER_RADIUS_MD",
    "BORDER_RADIUS_LG",
    "BORDER_RADIUS_XL",
    "BORDER_RADIUS_FULL",
    # Enums
    "ThemeVariant",
    # Classes
    "Theme",
    "ThemeManager",
    # Fonctions
    "get_theme_manager",
    "get_theme",
    "set_theme",
    "apply_theme",
    "get_color",
    "generate_widget_stylesheet",
    # Stylesheets
    "GLOBAL_STYLESHEET",
]
