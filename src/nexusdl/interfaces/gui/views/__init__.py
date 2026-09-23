"""Module public des vues de l'interface graphique NexusDL.

Ce module constitue le point d'entrée unique pour toutes les vues
PyQt6 de l'interface graphique NexusDL. Il agrège et ré-exporte les
symboles publics des 5 vues principales :

    - `main_view.py`      : Vue principale (dashboard)
    - `search_view.py`    : Vue de recherche multi-sites
    - `library_view.py`   : Vue de bibliothèque locale
    - `download_view.py`  : Vue de gestion des téléchargements
    - `settings_view.py`  : Vue des paramètres

Architecture :
    Toutes les vues partagent :
        - Style cyberpunk néon cohérent (vert/cyan/magenta sur noir)
        - Fallback PYQT6_AVAILABLE si PyQt6 indisponible
        - Signaux Qt pour communication avec la fenêtre parente
        - Intégration avec les composants réutilisables (MangaCard,
          ProgressWidget, SiteDropdown, ChapterTableWidget)
        - Intégration avec l'EventBus pour mises à jour temps réel
        - Traductions i18n via t()
        - Configuration via classes State/Filters dédiées

Résolution des conflits de noms :
    Plusieurs vues définissent des enums et modèles avec des noms
    similaires. Pour éviter les collisions, nous utilisons des préfixes
    contextuels :

        - SearchSortBy (search_view) → SearchSortBy (unique)
        - SearchState (search_view) → SearchState (unique)
        - SearchFilters (search_view) → SearchFilters (unique)
        - LibraryViewMode (library_view) → LibraryViewMode (unique)
        - LibrarySortBy (library_view) → LibrarySortBy (unique)
        - ReadingStatusFilter (library_view) → ReadingStatusFilter (unique)
        - LibraryFilters (library_view) → LibraryFilters (unique)
        - LibraryState (library_view) → LibraryState (unique)
        - DownloadFilter (download_view) → DownloadFilter (unique)
        - DownloadSortBy (download_view) → DownloadSortBy (unique)
        - DownloadFilters (download_view) → DownloadFilters (unique)
        - DownloadStats (download_view) → DownloadStats (unique)
        - DownloadState (download_view) → DownloadState (unique)
        - SettingsSection (settings_view) → SettingsSection (unique)
        - SettingType (settings_view) → SettingType (unique)
        - DashboardSection (main_view) → DashboardSection (unique)
        - QuickAction (main_view) → QuickAction (unique)

    Note : Les widgets internes des vues (SearchBar, LibraryToolbar, etc.)
    ne sont PAS exposés car ce sont des détails d'implémentation.

Exemple d'utilisation — MainView :
    >>> from nexusdl.interfaces.gui.views import MainView, QuickAction
    >>>
    >>> main_view = MainView(parent=self)
    >>> main_view.navigate_requested.connect(self.on_navigate)
    >>> layout.addWidget(main_view)

Exemple d'utilisation — SearchView :
    >>> from nexusdl.interfaces.gui.views import SearchView, SearchSortBy
    >>>
    >>> search_view = SearchView(parent=self)
    >>> search_view.mangas_selected.connect(self.on_mangas_selected)
    >>> layout.addWidget(search_view)

Exemple d'utilisation — LibraryView :
    >>> from nexusdl.interfaces.gui.views import (
    ...     LibraryView, LibraryViewMode, LibrarySortBy,
    ... )
    >>>
    >>> library_view = LibraryView(parent=self)
    >>> library_view.manga_double_clicked.connect(self.on_manga_open)
    >>> layout.addWidget(library_view)

Exemple d'utilisation — DownloadView :
    >>> from nexusdl.interfaces.gui.views import DownloadView, DownloadFilter
    >>>
    >>> download_view = DownloadView(parent=self)
    >>> download_view.task_action.connect(self.on_task_action)
    >>> layout.addWidget(download_view)

Exemple d'utilisation — SettingsView :
    >>> from nexusdl.interfaces.gui.views import SettingsView, SettingsSection
    >>>
    >>> settings_view = SettingsView(parent=self)
    >>> settings_view.settings_saved.connect(self.on_settings_saved)
    >>> layout.addWidget(settings_view)

Intégration :
    - interfaces/gui/app.py         : fenêtre principale qui instancie les vues
    - interfaces/gui/components/*   : composants réutilisables utilisés
    - core/models/*                 : modèles de domaine
    - core/registry/                : accès aux sites
    - core/library/                 : accès à la bibliothèque
    - core/downloader/              : accès aux tâches de téléchargement
    - core/config.py                : configuration
    - core/events.py                : EventBus pour mises à jour temps réel
    - core/i18n.py                  : traductions
"""

from __future__ import annotations

# ============================================================================
# VUE PRINCIPALE — main_view.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.gui.views.main_view import (
    DashboardLoadError,
    MainViewError,
)

# Enums
from nexusdl.interfaces.gui.views.main_view import (
    DashboardSection,
    QuickAction,
)

# Modèles
from nexusdl.interfaces.gui.views.main_view import (
    DashboardState,
    DashboardStats,
)

# Vue principale
from nexusdl.interfaces.gui.views.main_view import MainView

# ============================================================================
# VUE DE RECHERCHE — search_view.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.gui.views.search_view import (
    SearchExecutionError,
    SearchViewError,
)

# Enums
from nexusdl.interfaces.gui.views.search_view import (
    SearchSortBy,
    SearchState,
)

# Modèles
from nexusdl.interfaces.gui.views.search_view import (
    SearchFilters,
    SearchQuery,
    SearchResults,
    SearchViewState,
)

# Vue principale
from nexusdl.interfaces.gui.views.search_view import SearchView

# ============================================================================
# VUE DE BIBLIOTHÈQUE — library_view.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.gui.views.library_view import (
    LibraryLoadError,
    LibraryViewError,
    MangaNotFoundError,
)

# Enums
from nexusdl.interfaces.gui.views.library_view import (
    LibrarySortBy,
    LibraryViewMode,
    ReadingStatusFilter,
)

# Modèles
from nexusdl.interfaces.gui.views.library_view import (
    LibraryFilters,
    LibraryState,
)

# Vue principale
from nexusdl.interfaces.gui.views.library_view import LibraryView

# ============================================================================
# VUE DE TÉLÉCHARGEMENTS — download_view.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.gui.views.download_view import (
    DownloadManagerNotAvailableError,
    DownloadViewError,
    TaskActionError,
)

# Enums
from nexusdl.interfaces.gui.views.download_view import (
    DownloadFilter,
    DownloadSortBy,
)

# Modèles
from nexusdl.interfaces.gui.views.download_view import (
    DownloadFilters,
    DownloadState,
    DownloadStats,
)

# Vue principale
from nexusdl.interfaces.gui.views.download_view import DownloadView

# ============================================================================
# VUE DES PARAMÈTRES — settings_view.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.gui.views.settings_view import (
    SettingsSaveError,
    SettingsValidationError,
    SettingsViewError,
)

# Enums
from nexusdl.interfaces.gui.views.settings_view import (
    SettingsSection,
    SettingType,
)

# Modèles
from nexusdl.interfaces.gui.views.settings_view import (
    SettingDefinition,
    SettingsState,
)

# Vue principale
from nexusdl.interfaces.gui.views.settings_view import SettingsView

# ============================================================================
# EXPORTS PUBLICS
# ============================================================================

__all__ = [
    # ========================================================================
    # VUE PRINCIPALE — main_view.py
    # ========================================================================
    # Vue
    "MainView",
    # Enums
    "DashboardSection",
    "QuickAction",
    # Modèles
    "DashboardStats",
    "DashboardState",
    # Exceptions
    "MainViewError",
    "DashboardLoadError",
    # ========================================================================
    # VUE DE RECHERCHE — search_view.py
    # ========================================================================
    # Vue
    "SearchView",
    # Enums
    "SearchSortBy",
    "SearchState",
    # Modèles
    "SearchFilters",
    "SearchQuery",
    "SearchResults",
    "SearchViewState",
    # Exceptions
    "SearchViewError",
    "SearchExecutionError",
    # ========================================================================
    # VUE DE BIBLIOTHÈQUE — library_view.py
    # ========================================================================
    # Vue
    "LibraryView",
    # Enums
    "LibraryViewMode",
    "LibrarySortBy",
    "ReadingStatusFilter",
    # Modèles
    "LibraryFilters",
    "LibraryState",
    # Exceptions
    "LibraryViewError",
    "LibraryLoadError",
    "MangaNotFoundError",
    # ========================================================================
    # VUE DE TÉLÉCHARGEMENTS — download_view.py
    # ========================================================================
    # Vue
    "DownloadView",
    # Enums
    "DownloadFilter",
    "DownloadSortBy",
    # Modèles
    "DownloadFilters",
    "DownloadStats",
    "DownloadState",
    # Exceptions
    "DownloadViewError",
    "DownloadManagerNotAvailableError",
    "TaskActionError",
    # ========================================================================
    # VUE DES PARAMÈTRES — settings_view.py
    # ========================================================================
    # Vue
    "SettingsView",
    # Enums
    "SettingsSection",
    "SettingType",
    # Modèles
    "SettingDefinition",
    "SettingsState",
    # Exceptions
    "SettingsViewError",
    "SettingsValidationError",
    "SettingsSaveError",
]

__version__: str = "0.1.0"
