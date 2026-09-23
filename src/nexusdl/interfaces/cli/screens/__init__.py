"""Module public des écrans de l'interface CLI NexusDL.

Ce module constitue le point d'entrée unique pour tous les écrans de
l'interface CLI basée sur Textual. Il agrège et ré-exporte les symboles
publics des 7 écrans :

    - `main.py`      : Écran principal (dashboard) avec statistiques
    - `search.py`    : Écran de recherche multi-sites
    - `library.py`   : Écran de bibliothèque locale
    - `settings.py`  : Écran de paramètres
    - `logs.py`      : Écran de visualisation des logs
    - `help.py`      : Écran d'aide
    - `download.py`  : Écran de gestion des téléchargements

Architecture :
    Tous les écrans héritent de textual.screen.Screen et partagent :
        - Bindings clavier (BINDINGS)
        - CSS intégré (DEFAULT_CSS)
        - Messages Textual pour communication interne
        - Modèles Pydantic pour l'état
        - Enums pour les choix discrets
        - Support i18n via t()
        - Fallback TEXTUAL_AVAILABLE si Textual indisponible

Règles d'or :
    1. Ce fichier n'expose QUE les symboles publics stables.
    2. Il ne dépend d'aucun module de `parsers/` ni de `core/downloader/`.
    3. Tous les exports sont typés strictement et documentés.
    4. Les imports sont organisés par écran pour la lisibilité.
    5. Les conflits de noms sont résolus par des aliases explicites.
    6. Tous les écrans sont conditionnels (TEXTUAL_AVAILABLE).

Exemple d'utilisation :
    >>> from nexusdl.interfaces.cli.screens import (
    ...     MainScreen,
    ...     SearchScreen,
    ...     LibraryScreen,
    ...     SettingsScreen,
    ...     LogsScreen,
    ...     HelpScreen,
    ...     DownloadScreen,
    ... )
    >>>
    >>> # Dans l'application Textual
    >>> app.push_screen(MainScreen())
    >>> app.push_screen(SearchScreen())
    >>> app.push_screen(LibraryScreen())
"""

from __future__ import annotations

# ============================================================================
# ÉCRAN PRINCIPAL — main.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.cli.screens.main import (
    DashboardLoadError,
    MainScreenError,
)

# Enums
from nexusdl.interfaces.cli.screens.main import DashboardSection

# Modèles
from nexusdl.interfaces.cli.screens.main import DashboardState, DashboardStats

# Écran principal
from nexusdl.interfaces.cli.screens.main import MainScreen

# ============================================================================
# ÉCRAN DE RECHERCHE — search.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.cli.screens.search import (
    NoResultsError,
    SearchExecutionError,
    SearchScreenError,
)

# Enums
from nexusdl.interfaces.cli.screens.search import SearchSortBy, SearchState

# Modèles
from nexusdl.interfaces.cli.screens.search import (
    SearchFilters,
    SearchQuery,
    SearchResults,
    SearchScreenState,
)

# Écran principal
from nexusdl.interfaces.cli.screens.search import SearchScreen

# ============================================================================
# ÉCRAN DE BIBLIOTHÈQUE — library.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.cli.screens.library import (
    LibraryLoadError,
    LibraryScreenError,
    MangaNotFoundError,
)

# Enums
from nexusdl.interfaces.cli.screens.library import (
    LibrarySortBy,
    LibraryViewMode,
    ReadingStatusFilter,
)

# Modèles
from nexusdl.interfaces.cli.screens.library import LibraryFilters, LibraryState

# Écran principal
from nexusdl.interfaces.cli.screens.library import LibraryScreen

# ============================================================================
# ÉCRAN DE PARAMÈTRES — settings.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.cli.screens.settings import (
    SettingsError,
    SettingsSaveError,
    SettingsValidationError,
)

# Enums
from nexusdl.interfaces.cli.screens.settings import SettingsSection, SettingType

# Modèles
from nexusdl.interfaces.cli.screens.settings import SettingDefinition, SettingsState

# Écran principal
from nexusdl.interfaces.cli.screens.settings import SettingsScreen

# ============================================================================
# ÉCRAN DE LOGS — logs.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.cli.screens.logs import (
    LogsExportError,
    LogsScreenError,
)

# Enums
from nexusdl.interfaces.cli.screens.logs import ExportFormat, LogLevelFilter

# Modèles
from nexusdl.interfaces.cli.screens.logs import (
    LogEntry,
    LogsFilter,
    LogsState,
    LogsStats,
)

# Classes
from nexusdl.interfaces.cli.screens.logs import LogsBuffer, LogsSink

# Fonctions
from nexusdl.interfaces.cli.screens.logs import (
    clear_logs_buffer,
    export_logs_to_file,
    get_logs_buffer,
    get_logs_buffer_size,
    get_logs_sink,
    install_logs_sink,
    reset_logs_buffer,
    uninstall_logs_sink,
)

# Écran principal
from nexusdl.interfaces.cli.screens.logs import LogsScreen

# ============================================================================
# ÉCRAN D'AIDE — help.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.cli.screens.help import (
    HelpScreenError,
    LinkOpenError,
)

# Enums
from nexusdl.interfaces.cli.screens.help import HelpSection

# Modèles
from nexusdl.interfaces.cli.screens.help import (
    HelpState,
    LinkInfo,
    ShortcutInfo,
)

# Données
from nexusdl.interfaces.cli.screens.help import (
    GLOBAL_SHORTCUTS,
    NAVIGATION_SHORTCUTS,
    SCREEN_SHORTCUTS,
    USEFUL_LINKS,
)

# Fonctions
from nexusdl.interfaces.cli.screens.help import get_help_text, open_url

# Écran principal
from nexusdl.interfaces.cli.screens.help import HelpScreen

# ============================================================================
# ÉCRAN DE TÉLÉCHARGEMENTS — download.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.cli.screens.download import (
    DownloadManagerNotAvailableError,
    DownloadScreenError,
    TaskActionError,
)

# Enums
from nexusdl.interfaces.cli.screens.download import DownloadFilter, DownloadSortBy

# Modèles
from nexusdl.interfaces.cli.screens.download import (
    DownloadFilters,
    DownloadState,
    DownloadStats,
)

# Écran principal
from nexusdl.interfaces.cli.screens.download import DownloadScreen

# ============================================================================
# EXPORTS PUBLICS
# ============================================================================

__all__ = [
    # ========================================================================
    # ÉCRAN PRINCIPAL — main.py
    # ========================================================================
    # Écran
    "MainScreen",
    # Enums
    "DashboardSection",
    # Modèles
    "DashboardStats",
    "DashboardState",
    # Exceptions
    "MainScreenError",
    "DashboardLoadError",
    # ========================================================================
    # ÉCRAN DE RECHERCHE — search.py
    # ========================================================================
    # Écran
    "SearchScreen",
    # Enums
    "SearchSortBy",
    "SearchState",
    # Modèles
    "SearchFilters",
    "SearchQuery",
    "SearchResults",
    "SearchScreenState",
    # Exceptions
    "SearchScreenError",
    "SearchExecutionError",
    "NoResultsError",
    # ========================================================================
    # ÉCRAN DE BIBLIOTHÈQUE — library.py
    # ========================================================================
    # Écran
    "LibraryScreen",
    # Enums
    "LibraryViewMode",
    "LibrarySortBy",
    "ReadingStatusFilter",
    # Modèles
    "LibraryFilters",
    "LibraryState",
    # Exceptions
    "LibraryScreenError",
    "LibraryLoadError",
    "MangaNotFoundError",
    # ========================================================================
    # ÉCRAN DE PARAMÈTRES — settings.py
    # ========================================================================
    # Écran
    "SettingsScreen",
    # Enums
    "SettingsSection",
    "SettingType",
    # Modèles
    "SettingDefinition",
    "SettingsState",
    # Exceptions
    "SettingsError",
    "SettingsValidationError",
    "SettingsSaveError",
    # ========================================================================
    # ÉCRAN DE LOGS — logs.py
    # ========================================================================
    # Écran
    "LogsScreen",
    # Enums
    "LogLevelFilter",
    "ExportFormat",
    # Modèles
    "LogEntry",
    "LogsFilter",
    "LogsStats",
    "LogsState",
    # Classes
    "LogsBuffer",
    "LogsSink",
    # Fonctions
    "get_logs_buffer",
    "reset_logs_buffer",
    "install_logs_sink",
    "uninstall_logs_sink",
    "get_logs_sink",
    "get_logs_buffer_size",
    "clear_logs_buffer",
    "export_logs_to_file",
    # Exceptions
    "LogsScreenError",
    "LogsExportError",
    # ========================================================================
    # ÉCRAN D'AIDE — help.py
    # ========================================================================
    # Écran
    "HelpScreen",
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
    # Fonctions
    "get_help_text",
    "open_url",
    # Exceptions
    "HelpScreenError",
    "LinkOpenError",
    # ========================================================================
    # ÉCRAN DE TÉLÉCHARGEMENTS — download.py
    # ========================================================================
    # Écran
    "DownloadScreen",
    # Enums
    "DownloadFilter",
    "DownloadSortBy",
    # Modèles
    "DownloadFilters",
    "DownloadStats",
    "DownloadState",
    # Exceptions
    "DownloadScreenError",
    "DownloadManagerNotAvailableError",
    "TaskActionError",
]

__version__: str = "0.1.0"
