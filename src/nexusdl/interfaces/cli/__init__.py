"""Interface CLI (Command Line Interface) de NexusDL.

Ce module fournit l'interface en ligne de commande complète de NexusDL,
basée sur le framework Textual pour une expérience utilisateur riche
dans le terminal.

**Architecture** :
    interfaces/cli/
        ├── app.py              : Application Textual principale (NexusDLApp)
        ├── cli_entry.py        : Point d'entrée CLI (main, parsing args)
        ├── commands.py         : Commandes CLI (pattern Command)
        ├── config.py           : Configuration spécifique à la CLI
        ├── theme.tcss          : Thème Cyberpunk Neon / Hacker Futurist
        │
        ├── screens/            : Écrans de l'interface
        │   ├── main.py         : Dashboard principal
        │   ├── search.py       : Recherche de mangas
        │   ├── library.py      : Bibliothèque locale
        │   ├── download.py     : Gestion des téléchargements
        │   ├── settings.py     : Paramètres
        │   ├── logs.py         : Visualisation des logs
        │   ├── help.py         : Aide et raccourcis
        │   └── __init__.py     : Exports des écrans
        │
        └── widgets/            : Widgets réutilisables
            ├── site_selector.py    : Sélection de sites
            ├── progress_bar.py     : Barre de progression
            ├── manga_card.py       : Carte manga
            ├── log_viewer.py       : Viewer de logs
            ├── chapter_list.py     : Liste de chapitres
            └── __init__.py         : Exports des widgets

**Utilisation** :
    # Lancer l'interface TUI
    >>> from nexusdl.interfaces.cli import run_app
    >>> run_app()

    # Ou via la ligne de commande
    $ nexusdl
    $ nexusdl search "one piece"
    $ nexusdl download https://mangadex.org/title/12345
    $ nexusdl list-sites
    $ nexusdl config show
    $ nexusdl doctor

**Composants principaux** :
    - NexusDLApp          : Application Textual principale
    - CommandRegistry     : Registry des commandes CLI
    - CLIConfig           : Configuration de l'interface CLI
    - run_app()           : Fonction de démarrage de l'application
    - main()              : Point d'entrée CLI (argparse)

**Thème** :
    - Style: Cyberpunk Neon / Hacker Futurist
    - Couleurs: Vert néon (#00ff41), Cyan (#00ffff), Magenta (#ff00ff)
    - Fond: Noir profond (#000000)
    - Ambiance: Terminal hacker futuriste avec effets glow

**Exemples d'utilisation** :
    >>> from nexusdl.interfaces.cli import (
    ...     NexusDLApp,
    ...     CLIConfig,
    ...     CommandRegistry,
    ...     run_app,
    ... )
    >>>
    >>> # Configuration custom
    >>> config = CLIConfig(
    ...     theme_name="nexusdl-dark",
    ...     enable_animations=True,
    ...     log_level="INFO",
    ... )
    >>>
    >>> # Lancer avec configuration
    >>> run_app(config=config)
    >>>
    >>> # Ou utiliser l'application directement
    >>> app = NexusDLApp(config=config)
    >>> app.run()

Intégration :
    - core/config.py       : Configuration globale (parent)
    - core/logger.py       : Système de logging
    - core/i18n.py         : Internationalisation
    - core/events.py       : EventBus pour communication
    - core/paths.py        : Gestion des chemins
    - core/registry/       : Registre des sites
    - core/session/        : Sessions HTTP
    - core/downloader/     : Orchestration des téléchargements
    - core/library/        : Bibliothèque locale
"""

from __future__ import annotations

# ============================================================================
# IMPORTS — Application principale
# ============================================================================

from nexusdl.interfaces.cli.app import (
    NexusDLApp,
    run_app,
    get_app,
    set_app,
    reset_app,
    is_textual_available,
    get_textual_installation_instructions,
)

# ============================================================================
# IMPORTS — Point d'entrée CLI
# ============================================================================

from nexusdl.interfaces.cli.cli_entry import (
    main,
    CLI_COMMAND_NAME,
    CLI_DESCRIPTION,
    CLI_EPILOG,
)

# ============================================================================
# IMPORTS — Commandes
# ============================================================================

from nexusdl.interfaces.cli.commands import (
    # Classe de base
    BaseCommand,
    # Registry
    CommandRegistry,
    create_default_registry,
    execute_command,
    # Commandes
    TuiCommand,
    SearchCommand,
    DownloadCommand,
    ListSitesCommand,
    ConfigCommand,
    ValidateCommand,
    VersionCommand,
    DoctorCommand,
    CacheCommand,
    CompletionCommand,
)

# ============================================================================
# IMPORTS — Configuration
# ============================================================================

from nexusdl.interfaces.cli.config import (
    # Configuration principale
    CLIConfig,
    get_cli_config,
    set_cli_config,
    reset_cli_config,
    cli_config,
    # Sous-configurations
    ThemeConfig,
    KeybindingsConfig,
    ScreenConfig,
    ScreensConfig,
    WidgetConfig,
    DisplayConfig,
    # Enums
    CLITheme,
    DisplayMode,
    # Thèmes CSS
    THEME_CSS_NEXUSDL_DARK,
    THEME_CSS_NEXUSDL_LIGHT,
    # Fonctions helpers
    get_theme_css,
    get_keybinding,
    get_screen_config,
    is_screen_enabled,
    get_widget_config,
    get_display_config,
)

# ============================================================================
# IMPORTS — Constantes
# ============================================================================

from nexusdl.interfaces.cli.config import (
    EXIT_SUCCESS,
    EXIT_ERROR,
    EXIT_USAGE_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_DEPENDENCY_ERROR,
    EXIT_INTERRUPTED,
)

# ============================================================================
# IMPORTS — Écrans
# ============================================================================

from nexusdl.interfaces.cli.screens import (
    # Écrans principaux
    MainScreen,
    SearchScreen,
    LibraryScreen,
    DownloadScreen,
    SettingsScreen,
    LogsScreen,
    HelpScreen,
    # Enums des écrans
    DashboardSection,
    SearchSortBy,
    SearchState,
    LibraryViewMode,
    LibrarySortBy,
    ReadingStatusFilter,
    SettingsSection,
    SettingType,
    LogLevelFilter,
    ExportFormat,
    HelpSection,
    DownloadFilter,
    DownloadSortBy,
    # Modèles des écrans
    DashboardStats,
    DashboardState,
    SearchFilters,
    SearchQuery,
    SearchResults,
    SearchScreenState,
    LibraryFilters,
    LibraryState,
    SettingDefinition,
    SettingsState,
    LogEntry,
    LogsFilter,
    LogsStats,
    LogsState,
    ShortcutInfo,
    LinkInfo,
    HelpState,
    DownloadFilters,
    DownloadState,
    DownloadStats,
)

# ============================================================================
# IMPORTS — Widgets
# ============================================================================

from nexusdl.interfaces.cli.widgets import (
    # Widgets principaux
    SiteSelector,
    DownloadProgressBar,
    MangaCard,
    LogViewer,
    ChapterList,
    # Enums des widgets
    SelectionMode,
    SiteGroupBy,
    SiteSortBy,
    ProgressState,
    BarStyle,
    ProgressDisplayMode,
    CardLayout,
    CardState,
    CoverSource,
    LogViewerMode,
    ChapterListMode,
    ChapterFilter,
    ChapterSortBy,
    # Modèles des widgets
    SiteSelectorConfig,
    SiteSelectorState,
    ProgressBarConfig,
    ProgressBarState,
    MangaCardConfig,
    MangaCardState,
    LogViewerConfig,
    LogViewerState,
    ChapterListConfig,
    ChapterListFilters,
    ChapterListState,
    # Fonctions helpers des widgets
    create_site_selector,
    create_progress_bar,
    create_manga_card,
    create_log_viewer,
    create_chapter_list,
)

# ============================================================================
# VERSION
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
    # Application principale
    # ========================================================================
    "NexusDLApp",
    "run_app",
    "get_app",
    "set_app",
    "reset_app",
    "is_textual_available",
    "get_textual_installation_instructions",
    # ========================================================================
    # Point d'entrée CLI
    # ========================================================================
    "main",
    "CLI_COMMAND_NAME",
    "CLI_DESCRIPTION",
    "CLI_EPILOG",
    # ========================================================================
    # Commandes
    # ========================================================================
    "BaseCommand",
    "CommandRegistry",
    "create_default_registry",
    "execute_command",
    "TuiCommand",
    "SearchCommand",
    "DownloadCommand",
    "ListSitesCommand",
    "ConfigCommand",
    "ValidateCommand",
    "VersionCommand",
    "DoctorCommand",
    "CacheCommand",
    "CompletionCommand",
    # ========================================================================
    # Configuration
    # ========================================================================
    "CLIConfig",
    "get_cli_config",
    "set_cli_config",
    "reset_cli_config",
    "cli_config",
    "ThemeConfig",
    "KeybindingsConfig",
    "ScreenConfig",
    "ScreensConfig",
    "WidgetConfig",
    "DisplayConfig",
    "CLITheme",
    "DisplayMode",
    "THEME_CSS_NEXUSDL_DARK",
    "THEME_CSS_NEXUSDL_LIGHT",
    "get_theme_css",
    "get_keybinding",
    "get_screen_config",
    "is_screen_enabled",
    "get_widget_config",
    "get_display_config",
    # ========================================================================
    # Constantes
    # ========================================================================
    "EXIT_SUCCESS",
    "EXIT_ERROR",
    "EXIT_USAGE_ERROR",
    "EXIT_CONFIG_ERROR",
    "EXIT_DEPENDENCY_ERROR",
    "EXIT_INTERRUPTED",
    # ========================================================================
    # Écrans
    # ========================================================================
    "MainScreen",
    "SearchScreen",
    "LibraryScreen",
    "DownloadScreen",
    "SettingsScreen",
    "LogsScreen",
    "HelpScreen",
    # Enums des écrans
    "DashboardSection",
    "SearchSortBy",
    "SearchState",
    "LibraryViewMode",
    "LibrarySortBy",
    "ReadingStatusFilter",
    "SettingsSection",
    "SettingType",
    "LogLevelFilter",
    "ExportFormat",
    "HelpSection",
    "DownloadFilter",
    "DownloadSortBy",
    # Modèles des écrans
    "DashboardStats",
    "DashboardState",
    "SearchFilters",
    "SearchQuery",
    "SearchResults",
    "SearchScreenState",
    "LibraryFilters",
    "LibraryState",
    "SettingDefinition",
    "SettingsState",
    "LogEntry",
    "LogsFilter",
    "LogsStats",
    "LogsState",
    "ShortcutInfo",
    "LinkInfo",
    "HelpState",
    "DownloadFilters",
    "DownloadState",
    "DownloadStats",
    # ========================================================================
    # Widgets
    # ========================================================================
    "SiteSelector",
    "DownloadProgressBar",
    "MangaCard",
    "LogViewer",
    "ChapterList",
    # Enums des widgets
    "SelectionMode",
    "SiteGroupBy",
    "SiteSortBy",
    "ProgressState",
    "BarStyle",
    "ProgressDisplayMode",
    "CardLayout",
    "CardState",
    "CoverSource",
    "LogViewerMode",
    "ChapterListMode",
    "ChapterFilter",
    "ChapterSortBy",
    # Modèles des widgets
    "SiteSelectorConfig",
    "SiteSelectorState",
    "ProgressBarConfig",
    "ProgressBarState",
    "MangaCardConfig",
    "MangaCardState",
    "LogViewerConfig",
    "LogViewerState",
    "ChapterListConfig",
    "ChapterListFilters",
    "ChapterListState",
    # Fonctions helpers des widgets
    "create_site_selector",
    "create_progress_bar",
    "create_manga_card",
    "create_log_viewer",
    "create_chapter_list",
]
