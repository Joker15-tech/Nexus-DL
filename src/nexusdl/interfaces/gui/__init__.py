"""Module public de l'interface graphique (GUI) NexusDL.

Ce module constitue le point d'entrée unique pour l'interface graphique
PyQt6 de NexusDL. Il agrège et ré-exporte les symboles publics des
4 sous-modules :

    - `app.py`        : Application principale PyQt6 (NexusDLApp)
    - `theme.py`      : Système de thème cyberpunk néon
    - `views/`        : Vues principales (MainView, SearchView, etc.)
    - `components/`   : Composants réutilisables (MangaCard, ProgressWidget, etc.)

Architecture :
    interfaces/gui/
        ├── app.py              : Application principale (NexusDLApp)
        │   ├── NexusDLApp      : QMainWindow orchestrant toutes les vues
        │   ├── NavigationBar   : Barre de navigation
        │   ├── NotificationOverlay : Système de notifications toast
        │   └── run_gui_app()   : Fonction de démarrage
        │
        ├── theme.py            : Système de thème
        │   ├── Theme           : Configuration complète (couleurs, polices, dimensions)
        │   ├── ThemeManager    : Gestionnaire singleton
        │   ├── ThemeVariant    : Enum des variantes (CYBERPUNK, DARK, LIGHT, HIGH_CONTRAST)
        │   ├── COLOR_*         : 30+ constantes de couleur
        │   ├── FONT_*          : Configuration typographique
        │   ├── DIMENSION_*     : Espacements et tailles
        │   └── GLOBAL_STYLESHEET : Stylesheet QSS global
        │
        ├── views/              : Vues principales
        │   ├── main_view.py    : Dashboard avec statistiques
        │   ├── search_view.py  : Recherche multi-sites
        │   ├── library_view.py : Bibliothèque locale
        │   ├── download_view.py: Gestion des téléchargements
        │   └── settings_view.py: Paramètres
        │
        └── components/         : Composants réutilisables
            ├── site_dropdown.py    : Dropdown de sélection de sites
            ├── progress_widget.py  : Barre de progression animée
            ├── manga_card.py       : Carte manga
            └── chapter_table.py    : Table de chapitres

Résolution des conflits de noms :
    Plusieurs modules définissent des constantes et enums avec des noms
    identiques. Pour éviter les collisions, nous exposons une seule fois
    chaque symbole depuis sa source canonique :

    Couleurs (COLOR_*) :
        - Source unique : theme.py
        - Tous les autres modules importent depuis theme.py

    Fonctions utilitaires :
        - is_pyqt6_available() : depuis app.py
        - get_theme(), set_theme(), apply_theme() : depuis theme.py
        - get_color() : depuis theme.py

    Components :
        - Tous les widgets et enums sont exposés depuis components/__init__.py
        - Préfixes ajoutés si nécessaire (SiteSelectionMode, ChapterTableFilter, etc.)

    Views :
        - Toutes les vues sont exposées depuis views/__init__.py
        - Pas de conflits de noms entre vues

Exemple d'utilisation — Lancer l'application GUI :
    >>> from nexusdl.interfaces.gui import run_gui_app
    >>> run_gui_app()

Exemple d'utilisation — Utiliser le thème :
    >>> from nexusdl.interfaces.gui import (
    ...     get_theme, set_theme, ThemeVariant,
    ...     COLOR_PRIMARY, COLOR_SECONDARY,
    ... )
    >>>
    >>> # Obtenir le thème actuel
    >>> theme = get_theme()
    >>> print(theme.colors["primary"])
    '#00ff41'
    >>>
    >>> # Changer de thème
    >>> set_theme(ThemeVariant.DARK)
    >>>
    >>> # Accéder aux couleurs directement
    >>> print(COLOR_PRIMARY)
    '#00ff41'

Exemple d'utilisation — Créer une vue :
    >>> from nexusdl.interfaces.gui import (
    ...     MainView, SearchView, LibraryView,
    ...     DownloadView, SettingsView,
    ... )
    >>>
    >>> # Dans une fenêtre PyQt6
    >>> main_view = MainView(parent=self)
    >>> main_view.navigate_requested.connect(self.on_navigate)
    >>> layout.addWidget(main_view)

Exemple d'utilisation — Utiliser un composant :
    >>> from nexusdl.interfaces.gui import (
    ...     MangaCard, CardLayout,
    ...     ProgressWidget, ProgressStyle,
    ...     SiteDropdown, SiteSelectionMode,
    ...     ChapterTableWidget,
    ... )
    >>>
    >>> # Créer une carte manga
    >>> card = MangaCard(manga_data, layout=CardLayout.VERTICAL)
    >>> card.clicked.connect(self.on_manga_clicked)
    >>> layout.addWidget(card)
    >>>
    >>> # Créer une barre de progression
    >>> progress = ProgressWidget(
    ...     title="Downloading...",
    ...     style=ProgressStyle.GRADIENT,
    ... )
    >>> progress.set_progress(50, 100)
    >>> layout.addWidget(progress)

Intégration :
    - core/config.py       : Configuration globale
    - core/logger.py       : Système de logging
    - core/i18n.py         : Internationalisation
    - core/events.py       : EventBus pour communication
    - core/paths.py        : Gestion des chemins
    - core/registry/       : Registre des sites
    - core/session/        : Sessions HTTP
    - core/models/*        : Modèles de domaine
"""

from __future__ import annotations

# ============================================================================
# APPLICATION PRINCIPALE — app.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.gui.app import (
    AppNotInitializedError,
    ComponentInitializationError,
    GUIAppError,
)

# Enums
from nexusdl.interfaces.gui.app import (
    AppView,
    AppState,
    NotificationSeverity,
)

# Classe principale
from nexusdl.interfaces.gui.app import NexusDLApp

# Fonctions
from nexusdl.interfaces.gui.app import (
    get_gui_app,
    get_pyqt6_installation_instructions,
    is_pyqt6_available,
    reset_gui_app,
    run_gui_app,
    set_gui_app,
)

# ============================================================================
# SYSTÈME DE THÈME — theme.py
# ============================================================================

# Enums
from nexusdl.interfaces.gui.theme import ThemeVariant

# Classes
from nexusdl.interfaces.gui.theme import Theme, ThemeManager

# Fonctions
from nexusdl.interfaces.gui.theme import (
    apply_theme,
    generate_widget_stylesheet,
    get_color,
    get_theme,
    get_theme_manager,
    set_theme,
)

# Couleurs (source unique depuis theme.py)
from nexusdl.interfaces.gui.theme import (
    COLOR_ACCENT,
    COLOR_ACCENT_BG,
    COLOR_ACCENT_BRIGHT,
    COLOR_ACCENT_DIM,
    COLOR_BACKGROUND,
    COLOR_BACKGROUND_ALT,
    COLOR_BORDER,
    COLOR_BORDER_ACTIVE,
    COLOR_BORDER_DIM,
    COLOR_BORDER_FOCUS,
    COLOR_BORDER_HOVER,
    COLOR_ERROR,
    COLOR_INFO,
    COLOR_PRIMARY,
    COLOR_PRIMARY_BG,
    COLOR_PRIMARY_BRIGHT,
    COLOR_PRIMARY_DIM,
    COLOR_SECONDARY,
    COLOR_SECONDARY_BG,
    COLOR_SECONDARY_BRIGHT,
    COLOR_SECONDARY_DIM,
    COLOR_SUCCESS,
    COLOR_SURFACE,
    COLOR_SURFACE_ACTIVE,
    COLOR_SURFACE_ALT,
    COLOR_SURFACE_HOVER,
    COLOR_TEXT,
    COLOR_TEXT_ACCENT,
    COLOR_TEXT_BRIGHT,
    COLOR_TEXT_DIM,
    COLOR_TEXT_DISABLED,
    COLOR_TEXT_MUTED,
    COLOR_WARNING,
)

# Polices
from nexusdl.interfaces.gui.theme import (
    FONT_FAMILY_MONO,
    FONT_FAMILY_PRIMARY,
    FONT_FAMILY_SECONDARY,
    FONT_SIZE_2XL,
    FONT_SIZE_3XL,
    FONT_SIZE_4XL,
    FONT_SIZE_BASE,
    FONT_SIZE_LG,
    FONT_SIZE_MD,
    FONT_SIZE_SM,
    FONT_SIZE_XL,
    FONT_SIZE_XS,
    FONT_STYLE_ITALIC,
    FONT_STYLE_NORMAL,
    FONT_WEIGHT_BLACK,
    FONT_WEIGHT_BOLD,
    FONT_WEIGHT_MEDIUM,
    FONT_WEIGHT_NORMAL,
)

# Dimensions
from nexusdl.interfaces.gui.theme import (
    BORDER_RADIUS_FULL,
    BORDER_RADIUS_LG,
    BORDER_RADIUS_MD,
    BORDER_RADIUS_NONE,
    BORDER_RADIUS_SM,
    BORDER_RADIUS_XL,
    BORDER_WIDTH_HEAVY,
    BORDER_WIDTH_NORMAL,
    BORDER_WIDTH_THICK,
    BORDER_WIDTH_THIN,
    MARGIN_2XL,
    MARGIN_LG,
    MARGIN_MD,
    MARGIN_SM,
    MARGIN_XL,
    MARGIN_XS,
    PADDING_2XL,
    PADDING_LG,
    PADDING_MD,
    PADDING_SM,
    PADDING_XL,
    PADDING_XS,
    SIZE_BUTTON_HEIGHT,
    SIZE_BUTTON_MIN_WIDTH,
    SIZE_CHECKBOX,
    SIZE_COMBO_HEIGHT,
    SIZE_ICON_LG,
    SIZE_ICON_MD,
    SIZE_ICON_SM,
    SIZE_ICON_XL,
    SIZE_INPUT_HEIGHT,
    SIZE_RADIO,
)

# Stylesheet global
from nexusdl.interfaces.gui.theme import GLOBAL_STYLESHEET

# ============================================================================
# VUES — views/__init__.py
# ============================================================================

# Vue principale
from nexusdl.interfaces.gui.views import MainView

# Vue de recherche
from nexusdl.interfaces.gui.views import SearchView

# Vue de bibliothèque
from nexusdl.interfaces.gui.views import LibraryView

# Vue de téléchargements
from nexusdl.interfaces.gui.views import DownloadView

# Vue des paramètres
from nexusdl.interfaces.gui.views import SettingsView

# Enums des vues
from nexusdl.interfaces.gui.views import (
    DashboardSection,
    DownloadFilter,
    DownloadSortBy,
    LibrarySortBy,
    LibraryViewMode,
    QuickAction,
    ReadingStatusFilter,
    SearchSortBy,
    SearchState,
    SettingsSection,
    SettingType,
)

# Modèles des vues
from nexusdl.interfaces.gui.views import (
    DashboardState,
    DashboardStats,
    DownloadFilters,
    DownloadState,
    DownloadStats,
    LibraryFilters,
    LibraryState,
    SearchFilters,
    SearchQuery,
    SearchResults,
    SearchViewState,
    SettingDefinition,
    SettingsState,
)

# Exceptions des vues
from nexusdl.interfaces.gui.views import (
    DashboardLoadError,
    DownloadManagerNotAvailableError,
    DownloadViewError,
    LibraryLoadError,
    LibraryViewError,
    MainViewError,
    MangaNotFoundError,
    SearchExecutionError,
    SearchViewError,
    SettingsSaveError,
    SettingsValidationError,
    SettingsViewError,
    TaskActionError,
)

# ============================================================================
# COMPOSANTS — components/__init__.py
# ============================================================================

# SiteDropdown
from nexusdl.interfaces.gui.components import (
    LanguageFilter,
    RegistryNotAvailableError,
    SelectionLimitError,
    SiteDropdown,
    SiteDropdownButton,
    SiteDropdownError,
    SiteDropdownPopup,
    SiteFilter,
    SiteListItemWidget,
    SiteSelectionMode,
)

# ProgressWidget
from nexusdl.interfaces.gui.components import (
    InvalidProgressError,
    ProgressBar,
    ProgressConfig,
    ProgressData,
    ProgressDisplayMode,
    ProgressInfo,
    ProgressState,
    ProgressStatus,
    ProgressStyle,
    ProgressWidget,
    ProgressWidgetError,
)

# MangaCard
from nexusdl.interfaces.gui.components import (
    CardLayout,
    CardState,
    CoverLoadError,
    CoverSource,
    InvalidMangaError,
    MangaCard,
    MangaCardConfig,
    MangaCardData,
    MangaCardError,
    MangaCardState,
    MangaCover,
)

# ChapterTable
from nexusdl.interfaces.gui.components import (
    ChapterList,
    ChapterListConfig,
    ChapterListError,
    ChapterListFilters,
    ChapterListMode,
    ChapterListState,
    ChapterTableAction,
    ChapterTableConfig,
    ChapterTableData,
    ChapterTableError,
    ChapterTableFilter,
    ChapterTableFilters,
    ChapterTableSortBy,
    ChapterTableWidget,
    InvalidChapterError,
)

# Fonctions helpers des composants
from nexusdl.interfaces.gui.components import (
    chapter_to_table_data,
    create_chapter_list,
    create_chapter_table,
    create_manga_card,
    create_progress_widget,
    create_site_dropdown,
    manga_to_card_data,
)

# ============================================================================
# VERSION ET MÉTADONNÉES
# ============================================================================

__version__: str = "0.1.0"
__author__: str = "NexusDL Team"
__license__: str = "MIT"


# ============================================================================
# EXPORTS PUBLICS
# ============================================================================

__all__ = [
    # ========================================================================
    # Métadonnées
    # ========================================================================
    "__version__",
    "__author__",
    "__license__",
    # ========================================================================
    # APPLICATION PRINCIPALE — app.py
    # ========================================================================
    # Classe principale
    "NexusDLApp",
    # Enums
    "AppView",
    "AppState",
    "NotificationSeverity",
    # Fonctions
    "run_gui_app",
    "get_gui_app",
    "set_gui_app",
    "reset_gui_app",
    "is_pyqt6_available",
    "get_pyqt6_installation_instructions",
    # Exceptions
    "GUIAppError",
    "AppNotInitializedError",
    "ComponentInitializationError",
    # ========================================================================
    # SYSTÈME DE THÈME — theme.py
    # ========================================================================
    # Classes
    "Theme",
    "ThemeManager",
    # Enums
    "ThemeVariant",
    # Fonctions
    "get_theme",
    "set_theme",
    "apply_theme",
    "get_theme_manager",
    "get_color",
    "generate_widget_stylesheet",
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
    "FONT_STYLE_NORMAL",
    "FONT_STYLE_ITALIC",
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
    # Stylesheet
    "GLOBAL_STYLESHEET",
    # ========================================================================
    # VUES — views/__init__.py
    # ========================================================================
    # Vues principales
    "MainView",
    "SearchView",
    "LibraryView",
    "DownloadView",
    "SettingsView",
    # Enums des vues
    "DashboardSection",
    "QuickAction",
    "SearchSortBy",
    "SearchState",
    "LibraryViewMode",
    "LibrarySortBy",
    "ReadingStatusFilter",
    "DownloadFilter",
    "DownloadSortBy",
    "SettingsSection",
    "SettingType",
    # Modèles des vues
    "DashboardStats",
    "DashboardState",
    "SearchFilters",
    "SearchQuery",
    "SearchResults",
    "SearchViewState",
    "LibraryFilters",
    "LibraryState",
    "DownloadFilters",
    "DownloadStats",
    "DownloadState",
    "SettingDefinition",
    "SettingsState",
    # Exceptions des vues
    "MainViewError",
    "DashboardLoadError",
    "SearchViewError",
    "SearchExecutionError",
    "LibraryViewError",
    "LibraryLoadError",
    "MangaNotFoundError",
    "DownloadViewError",
    "DownloadManagerNotAvailableError",
    "TaskActionError",
    "SettingsViewError",
    "SettingsValidationError",
    "SettingsSaveError",
    # ========================================================================
    # COMPOSANTS — components/__init__.py
    # ========================================================================
    # SiteDropdown
    "SiteDropdown",
    "SiteDropdownButton",
    "SiteDropdownPopup",
    "SiteListItemWidget",
    "SiteSelectionMode",
    "SiteFilter",
    "LanguageFilter",
    "SiteDropdownError",
    "RegistryNotAvailableError",
    "SelectionLimitError",
    # ProgressWidget
    "ProgressWidget",
    "ProgressBar",
    "ProgressInfo",
    "ProgressStatus",
    "ProgressState",
    "ProgressStyle",
    "ProgressDisplayMode",
    "ProgressConfig",
    "ProgressData",
    "ProgressWidgetError",
    "InvalidProgressError",
    # MangaCard
    "MangaCard",
    "MangaCover",
    "CardLayout",
    "CardState",
    "CoverSource",
    "MangaCardConfig",
    "MangaCardState",
    "MangaCardError",
    "InvalidMangaError",
    "CoverLoadError",
    # ChapterTable
    "ChapterTableWidget",
    "ChapterTable",
    "ChapterTableToolbar",
    "ChapterTableFooter",
    "ChapterTableFilter",
    "ChapterTableSortBy",
    "ChapterTableAction",
    "ChapterTableConfig",
    "ChapterTableFilters",
    "ChapterTableData",
    "ChapterTableError",
    "InvalidChapterError",
    # ChapterList (alias pour compatibilité)
    "ChapterList",
    "ChapterListConfig",
    "ChapterListFilters",
    "ChapterListState",
    "ChapterListMode",
    "ChapterListError",
    # Fonctions helpers des composants
    "create_site_dropdown",
    "create_progress_widget",
    "create_manga_card",
    "manga_to_card_data",
    "create_chapter_table",
    "create_chapter_list",
    "chapter_to_table_data",
]
