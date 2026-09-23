"""Module public des composants GUI réutilisables pour NexusDL.

Ce module constitue le point d'entrée unique pour tous les composants
PyQt6 réutilisables de l'interface graphique NexusDL. Il agrège et
ré-exporte les symboles publics des 4 composants :

    - `site_dropdown.py`    : Dropdown de sélection de sites
    - `progress_widget.py`  : Widget de progression avec animations néon
    - `manga_card.py`       : Carte manga avec 4 layouts
    - `chapter_table.py`    : Table de chapitres avec tri/filtrage/actions

Architecture :
    Tous les composants partagent :
        - Style cyberpunk néon cohérent (vert/cyan/magenta sur noir)
        - Fallback PYQT6_AVAILABLE si PyQt6 indisponible
        - Signaux Qt pour communication
        - Configuration via classes Config dédiées
        - Intégration avec les modèles du core (Manga, Chapter, SiteConfig)
        - Traductions i18n via t()

Résolution des conflits de noms :
    Plusieurs composants définissent des enums et constantes avec des noms
    identiques. Pour éviter les collisions, nous utilisons des préfixes
    contextuels :

        - SelectionMode (site_dropdown) → SiteSelectionMode
        - ProgressState (progress_widget) → ProgressState (unique)
        - BarStyle (progress_widget) → ProgressStyle
        - DisplayMode (progress_widget) → ProgressDisplayMode
        - CardLayout (manga_card) → CardLayout (unique)
        - CardState (manga_card) → CardState (unique)
        - CoverSource (manga_card) → CoverSource (unique)
        - ChapterFilter (chapter_table) → ChapterTableFilter
        - ChapterSortBy (chapter_table) → ChapterTableSortBy
        - ChapterAction (chapter_table) → ChapterTableAction

    Les constantes de couleur (COLOR_PRIMARY, etc.) sont exposées une seule
    fois depuis site_dropdown.py.

Exemple d'utilisation — SiteDropdown :
    >>> from nexusdl.interfaces.gui.components import (
    ...     SiteDropdown, SiteSelectionMode,
    ... )
    >>>
    >>> dropdown = SiteDropdown(
    ...     mode=SiteSelectionMode.MULTIPLE,
    ...     parent=self,
    ... )
    >>> dropdown.site_selected.connect(self.on_site_selected)
    >>> layout.addWidget(dropdown)

Exemple d'utilisation — ProgressWidget :
    >>> from nexusdl.interfaces.gui.components import (
    ...     ProgressWidget, ProgressStyle, ProgressState,
    ... )
    >>>
    >>> progress = ProgressWidget(
    ...     title="Downloading One Piece",
    ...     style=ProgressStyle.GRADIENT,
    ...     parent=self,
    ... )
    >>> progress.set_progress(current=21, total=42)
    >>> layout.addWidget(progress)

Exemple d'utilisation — MangaCard :
    >>> from nexusdl.interfaces.gui.components import (
    ...     MangaCard, CardLayout, manga_to_card_data,
    ... )
    >>>
    >>> card_data = manga_to_card_data(manga)
    >>> card = MangaCard(
    ...     card_data,
    ...     layout=CardLayout.VERTICAL,
    ...     parent=self,
    ... )
    >>> card.clicked.connect(self.on_manga_clicked)
    >>> layout.addWidget(card)

Exemple d'utilisation — ChapterTable :
    >>> from nexusdl.interfaces.gui.components import (
    ...     ChapterTableWidget, chapter_to_table_data,
    ... )
    >>>
    >>> table_data = [chapter_to_table_data(c) for c in manga.chapters]
    >>> table = ChapterTableWidget(
    ...     chapters=table_data,
    ...     parent=self,
    ... )
    >>> table.chapter_selected.connect(self.on_chapter_selected)
    >>> layout.addWidget(table)

Intégration :
    - interfaces/gui/screens/* : utilise ces composants dans les écrans
    - core/models/manga.py     : modèles Manga, Chapter
    - core/models/site.py      : modèle SiteConfig
    - core/registry/           : accès aux sites
    - core/events.py           : émission d'événements
    - core/i18n.py             : traductions
    - core/utils/text.py       : formatage, troncature
    - core/utils/time.py       : formatage des dates
"""

from __future__ import annotations

# ============================================================================
# COMPOSANT — SiteDropdown (site_dropdown.py)
# ============================================================================

# Exceptions
from nexusdl.interfaces.gui.components.site_dropdown import (
    RegistryNotAvailableError,
    SiteDropdownError,
)

# Enums
from nexusdl.interfaces.gui.components.site_dropdown import (
    LanguageFilter,
    SelectionMode as SiteSelectionMode,
    SiteFilter,
)

# Widgets
from nexusdl.interfaces.gui.components.site_dropdown import (
    SiteDropdown,
    SiteDropdownButton,
    SiteDropdownPopup,
    SiteListItemWidget,
)

# Helpers
from nexusdl.interfaces.gui.components.site_dropdown import (
    create_site_dropdown,
)

# ============================================================================
# COMPOSANT — ProgressWidget (progress_widget.py)
# ============================================================================

# Exceptions
from nexusdl.interfaces.gui.components.progress_widget import (
    InvalidProgressError,
    ProgressWidgetError,
)

# Enums
from nexusdl.interfaces.gui.components.progress_widget import (
    BarStyle as ProgressStyle,
    DisplayMode as ProgressDisplayMode,
    ProgressState,
)

# Modèles
from nexusdl.interfaces.gui.components.progress_widget import (
    ProgressConfig,
    ProgressData,
)

# Widgets
from nexusdl.interfaces.gui.components.progress_widget import (
    ProgressBar,
    ProgressInfo,
    ProgressStatus,
    ProgressWidget,
)

# Helpers
from nexusdl.interfaces.gui.components.progress_widget import (
    create_progress_widget,
)

# ============================================================================
# COMPOSANT — MangaCard (manga_card.py)
# ============================================================================

# Exceptions
from nexusdl.interfaces.gui.components.manga_card import (
    CoverLoadError,
    InvalidMangaError,
    MangaCardError,
)

# Enums
from nexusdl.interfaces.gui.components.manga_card import (
    CardLayout,
    CardState,
    CoverSource,
)

# Modèles
from nexusdl.interfaces.gui.components.manga_card import (
    MangaCardConfig,
    MangaCardData,
)

# Widgets
from nexusdl.interfaces.gui.components.manga_card import (
    MangaCard,
    MangaCover,
)

# Helpers
from nexusdl.interfaces.gui.components.manga_card import (
    create_manga_card,
    manga_to_card_data,
)

# ============================================================================
# COMPOSANT — ChapterTable (chapter_table.py)
# ============================================================================

# Exceptions
from nexusdl.interfaces.gui.components.chapter_table import (
    ChapterTableError,
    InvalidChapterError,
)

# Enums
from nexusdl.interfaces.gui.components.chapter_table import (
    ChapterAction as ChapterTableAction,
    ChapterFilter as ChapterTableFilter,
    ChapterSortBy as ChapterTableSortBy,
)

# Modèles
from nexusdl.interfaces.gui.components.chapter_table import (
    ChapterTableConfig,
    ChapterTableData,
    ChapterTableFilters,
)

# Widgets
from nexusdl.interfaces.gui.components.chapter_table import (
    ChapterTable,
    ChapterTableFooter,
    ChapterTableToolbar,
    ChapterTableWidget,
)

# Helpers
from nexusdl.interfaces.gui.components.chapter_table import (
    chapter_to_table_data,
    create_chapter_table,
)

# ============================================================================
# CONSTANTES PARTAGÉES — Couleurs du thème cyberpunk néon
# ============================================================================

# Exposer les constantes de couleur depuis site_dropdown.py (source unique)
from nexusdl.interfaces.gui.components.site_dropdown import (
    COLOR_ACCENT,
    COLOR_ACCENT_DIM,
    COLOR_BACKGROUND,
    COLOR_BORDER,
    COLOR_BORDER_DIM,
    COLOR_BORDER_FOCUS,
    COLOR_ERROR,
    COLOR_INFO,
    COLOR_PRIMARY,
    COLOR_PRIMARY_BG,
    COLOR_PRIMARY_DIM,
    COLOR_SECONDARY,
    COLOR_SECONDARY_DIM,
    COLOR_SUCCESS,
    COLOR_SURFACE,
    COLOR_SURFACE_ALT,
    COLOR_SURFACE_HOVER,
    COLOR_TEXT,
    COLOR_TEXT_BRIGHT,
    COLOR_TEXT_DIM,
    COLOR_TEXT_DISABLED,
    COLOR_TEXT_MUTED,
    COLOR_WARNING,
)

# ============================================================================
# FONCTIONS GLOBALES
# ============================================================================

from nexusdl.interfaces.gui.components.site_dropdown import (
    is_pyqt6_available,
)


# ============================================================================
# EXPORTS PUBLICS
# ============================================================================

__all__ = [
    # ========================================================================
    # CONSTANTES PARTAGÉES — Couleurs du thème cyberpunk néon
    # ========================================================================
    "COLOR_PRIMARY",
    "COLOR_PRIMARY_DIM",
    "COLOR_PRIMARY_BG",
    "COLOR_SECONDARY",
    "COLOR_SECONDARY_DIM",
    "COLOR_ACCENT",
    "COLOR_ACCENT_DIM",
    "COLOR_SUCCESS",
    "COLOR_WARNING",
    "COLOR_ERROR",
    "COLOR_INFO",
    "COLOR_BACKGROUND",
    "COLOR_SURFACE",
    "COLOR_SURFACE_ALT",
    "COLOR_SURFACE_HOVER",
    "COLOR_TEXT",
    "COLOR_TEXT_BRIGHT",
    "COLOR_TEXT_MUTED",
    "COLOR_TEXT_DIM",
    "COLOR_TEXT_DISABLED",
    "COLOR_BORDER",
    "COLOR_BORDER_DIM",
    "COLOR_BORDER_FOCUS",
    # ========================================================================
    # FONCTIONS GLOBALES
    # ========================================================================
    "is_pyqt6_available",
    # ========================================================================
    # COMPOSANT — SiteDropdown
    # ========================================================================
    # Exceptions
    "SiteDropdownError",
    "RegistryNotAvailableError",
    # Enums
    "SiteSelectionMode",
    "SiteFilter",
    "LanguageFilter",
    # Widgets
    "SiteDropdown",
    "SiteDropdownButton",
    "SiteDropdownPopup",
    "SiteListItemWidget",
    # Helpers
    "create_site_dropdown",
    # ========================================================================
    # COMPOSANT — ProgressWidget
    # ========================================================================
    # Exceptions
    "ProgressWidgetError",
    "InvalidProgressError",
    # Enums
    "ProgressState",
    "ProgressStyle",
    "ProgressDisplayMode",
    # Modèles
    "ProgressConfig",
    "ProgressData",
    # Widgets
    "ProgressWidget",
    "ProgressBar",
    "ProgressInfo",
    "ProgressStatus",
    # Helpers
    "create_progress_widget",
    # ========================================================================
    # COMPOSANT — MangaCard
    # ========================================================================
    # Exceptions
    "MangaCardError",
    "InvalidMangaError",
    "CoverLoadError",
    # Enums
    "CardLayout",
    "CardState",
    "CoverSource",
    # Modèles
    "MangaCardConfig",
    "MangaCardData",
    # Widgets
    "MangaCard",
    "MangaCover",
    # Helpers
    "create_manga_card",
    "manga_to_card_data",
    # ========================================================================
    # COMPOSANT — ChapterTable
    # ========================================================================
    # Exceptions
    "ChapterTableError",
    "InvalidChapterError",
    # Enums
    "ChapterTableFilter",
    "ChapterTableSortBy",
    "ChapterTableAction",
    # Modèles
    "ChapterTableConfig",
    "ChapterTableFilters",
    "ChapterTableData",
    # Widgets
    "ChapterTableWidget",
    "ChapterTable",
    "ChapterTableToolbar",
    "ChapterTableFooter",
    # Helpers
    "create_chapter_table",
    "chapter_to_table_data",
]

__version__: str = "0.1.0"
