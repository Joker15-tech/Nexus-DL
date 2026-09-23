"""Module public des widgets réutilisables pour l'interface CLI NexusDL.

Ce module constitue le point d'entrée unique pour tous les widgets Textual
réutilisables de l'interface CLI. Il agrège et ré-exporte les symboles publics
des 5 widgets :

    - `site_selector.py`  : Widget de sélection de sites
    - `progress_bar.py`   : Widget de barre de progression
    - `manga_card.py`     : Widget de carte manga
    - `log_viewer.py`     : Widget de visualisation des logs
    - `chapter_list.py`   : Widget de liste de chapitres

Architecture :
    Tous les widgets héritent de textual.widget.Widget et partagent :
        - CSS intégré (DEFAULT_CSS)
        - Messages Textual pour communication interne
        - Modèles Pydantic pour la configuration et l'état
        - Enums pour les choix discrets
        - Support i18n via t()
        - Fallback TEXTUAL_AVAILABLE si Textual indisponible
        - Callbacks optionnels pour personnalisation

Règles d'or :
    1. Ce fichier n'expose QUE les symboles publics stables.
    2. Il ne dépend d'aucun module de `parsers/` ni de `core/downloader/`.
    3. Tous les exports sont typés strictement et documentés.
    4. Les imports sont organisés par widget pour la lisibilité.
    5. Les conflits de noms sont résolus par des aliases explicites.
    6. Tous les widgets sont conditionnels (TEXTUAL_AVAILABLE).

Exemple d'utilisation — SiteSelector :
    >>> from nexusdl.interfaces.cli.widgets import SiteSelector, SelectionMode
    >>>
    >>> selector = SiteSelector(
    ...     mode=SelectionMode.MULTIPLE,
    ...     on_selection_changed=my_callback,
    ... )
    >>> self.mount(selector)
    >>> selected = selector.get_selected_sites()

Exemple d'utilisation — DownloadProgressBar :
    >>> from nexusdl.interfaces.cli.widgets import (
    ...     DownloadProgressBar, ProgressState, BarStyle,
    ... )
    >>>
    >>> progress = DownloadProgressBar(
    ...     title="Downloading One Piece",
    ...     total=42,
    ...     style=BarStyle.BLOCK,
    ... )
    >>> self.mount(progress)
    >>> progress.update_progress(current=21, speed=1024000)

Exemple d'utilisation — MangaCard :
    >>> from nexusdl.interfaces.cli.widgets import MangaCard, CardLayout
    >>>
    >>> card = MangaCard(
    ...     manga=manga,
    ...     layout=CardLayout.VERTICAL,
    ...     selectable=True,
    ...     on_click=my_callback,
    ... )
    >>> self.mount(card)

Exemple d'utilisation — LogViewer :
    >>> from nexusdl.interfaces.cli.widgets import LogViewer, LogViewerMode
    >>>
    >>> viewer = LogViewer(
    ...     title="Recent Logs",
    ...     mode=LogViewerMode.NORMAL,
    ...     max_lines=100,
    ... )
    >>> self.mount(viewer)

Exemple d'utilisation — ChapterList :
    >>> from nexusdl.interfaces.cli.widgets import ChapterList, ChapterListMode
    >>>
    >>> chapter_list = ChapterList(
    ...     chapters=manga.chapters,
    ...     mode=ChapterListMode.NORMAL,
    ...     selectable=True,
    ...     on_chapter_activated=my_callback,
    ... )
    >>> self.mount(chapter_list)
    >>> selected = chapter_list.get_selected_chapters()
"""

from __future__ import annotations

# ============================================================================
# WIDGET DE SÉLECTION DE SITES — site_selector.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.cli.widgets.site_selector import (
    RegistryNotAvailableError,
    SelectionLimitError,
    SiteNotFoundError,
    SiteSelectorError,
)

# Enums
from nexusdl.interfaces.cli.widgets.site_selector import (
    SelectionMode,
    SiteGroupBy,
    SiteSortBy,
)

# Modèles
from nexusdl.interfaces.cli.widgets.site_selector import (
    SiteSelectorConfig,
    SiteSelectorState,
)

# Helpers
from nexusdl.interfaces.cli.widgets.site_selector import (
    create_site_selector,
    get_selected_sites_from_registry,
    get_site_capabilities_badges,
    get_site_language_label,
    get_site_status_icon,
)

# Widget principal
from nexusdl.interfaces.cli.widgets.site_selector import SiteSelector

# ============================================================================
# WIDGET DE BARRE DE PROGRESSION — progress_bar.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.cli.widgets.progress_bar import (
    InvalidProgressError,
    ProgressBarError,
)

# Enums
from nexusdl.interfaces.cli.widgets.progress_bar import (
    BarStyle,
    ProgressDisplayMode,
    ProgressState,
)

# Modèles
from nexusdl.interfaces.cli.widgets.progress_bar import (
    ProgressBarConfig,
    ProgressBarState,
)

# Helpers de rendu
from nexusdl.interfaces.cli.widgets.progress_bar import (
    create_progress_bar,
    format_progress_line,
    render_bar,
    render_spinner,
)

# Widget principal
from nexusdl.interfaces.cli.widgets.progress_bar import DownloadProgressBar

# ============================================================================
# WIDGET DE CARTE MANGA — manga_card.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.cli.widgets.manga_card import (
    CoverLoadError,
    InvalidMangaError,
    MangaCardError,
)

# Enums
from nexusdl.interfaces.cli.widgets.manga_card import (
    CardLayout,
    CardState,
    CoverSource,
)

# Modèles
from nexusdl.interfaces.cli.widgets.manga_card import (
    MangaCardConfig,
    MangaCardState,
)

# Helpers
from nexusdl.interfaces.cli.widgets.manga_card import (
    create_manga_card,
    format_manga_metadata,
    format_manga_summary,
    format_tags,
    get_card_dimensions,
    get_content_rating_badge,
    get_language_badge,
    get_status_badge,
    render_cover_placeholder,
    render_progress_bar as render_manga_progress_bar,
)

# Widget principal
from nexusdl.interfaces.cli.widgets.manga_card import MangaCard

# ============================================================================
# WIDGET DE VISUALISATION DES LOGS — log_viewer.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.cli.widgets.log_viewer import (
    LogBufferNotAvailableError,
    LogViewerError,
)

# Enums
from nexusdl.interfaces.cli.widgets.log_viewer import (
    LogLevelFilter as LogViewerLevelFilter,
    LogViewerMode,
)

# Modèles
from nexusdl.interfaces.cli.widgets.log_viewer import (
    LogViewerConfig,
    LogViewerState,
)

# Helpers
from nexusdl.interfaces.cli.widgets.log_viewer import create_log_viewer

# Widget principal
from nexusdl.interfaces.cli.widgets.log_viewer import LogViewer

# ============================================================================
# WIDGET DE LISTE DE CHAPITRES — chapter_list.py
# ============================================================================

# Exceptions
from nexusdl.interfaces.cli.widgets.chapter_list import (
    ChapterListError,
    InvalidChapterError,
)

# Enums
from nexusdl.interfaces.cli.widgets.chapter_list import (
    ChapterFilter,
    ChapterListMode,
    ChapterSortBy,
)

# Modèles
from nexusdl.interfaces.cli.widgets.chapter_list import (
    ChapterListConfig,
    ChapterListFilters,
    ChapterListState,
)

# Helpers
from nexusdl.interfaces.cli.widgets.chapter_list import (
    create_chapter_list,
    format_chapter_date,
    format_chapter_number,
    format_chapter_status,
)

# Widget principal
from nexusdl.interfaces.cli.widgets.chapter_list import ChapterList

# ============================================================================
# RÉSOLUTION DES CONFLITS DE NOMS
# ============================================================================

# render_progress_bar existe dans progress_bar.py et manga_card.py
# On utilise render_progress_bar de progress_bar.py par défaut
# et render_manga_progress_bar pour celui de manga_card.py

# LogLevelFilter existe dans log_viewer.py et screens/logs.py
# On utilise LogViewerLevelFilter pour celui de log_viewer.py

# ============================================================================
# EXPORTS PUBLICS
# ============================================================================

__all__ = [
    # ========================================================================
    # WIDGET DE SÉLECTION DE SITES — site_selector.py
    # ========================================================================
    # Widget
    "SiteSelector",
    # Enums
    "SelectionMode",
    "SiteGroupBy",
    "SiteSortBy",
    # Modèles
    "SiteSelectorConfig",
    "SiteSelectorState",
    # Helpers
    "create_site_selector",
    "get_selected_sites_from_registry",
    "get_site_language_label",
    "get_site_capabilities_badges",
    "get_site_status_icon",
    # Exceptions
    "SiteSelectorError",
    "RegistryNotAvailableError",
    "SiteNotFoundError",
    "SelectionLimitError",
    # ========================================================================
    # WIDGET DE BARRE DE PROGRESSION — progress_bar.py
    # ========================================================================
    # Widget
    "DownloadProgressBar",
    # Enums
    "ProgressState",
    "BarStyle",
    "ProgressDisplayMode",
    # Modèles
    "ProgressBarConfig",
    "ProgressBarState",
    # Helpers
    "render_bar",
    "render_spinner",
    "format_progress_line",
    "create_progress_bar",
    # Exceptions
    "ProgressBarError",
    "InvalidProgressError",
    # ========================================================================
    # WIDGET DE CARTE MANGA — manga_card.py
    # ========================================================================
    # Widget
    "MangaCard",
    # Enums
    "CardLayout",
    "CardState",
    "CoverSource",
    # Modèles
    "MangaCardConfig",
    "MangaCardState",
    # Helpers
    "get_status_badge",
    "get_language_badge",
    "get_content_rating_badge",
    "format_manga_metadata",
    "format_tags",
    "render_cover_placeholder",
    "render_manga_progress_bar",
    "create_manga_card",
    "format_manga_summary",
    "get_card_dimensions",
    # Exceptions
    "MangaCardError",
    "InvalidMangaError",
    "CoverLoadError",
    # ========================================================================
    # WIDGET DE VISUALISATION DES LOGS — log_viewer.py
    # ========================================================================
    # Widget
    "LogViewer",
    # Enums
    "LogViewerMode",
    "LogViewerLevelFilter",
    # Modèles
    "LogViewerConfig",
    "LogViewerState",
    # Helpers
    "create_log_viewer",
    # Exceptions
    "LogViewerError",
    "LogBufferNotAvailableError",
    # ========================================================================
    # WIDGET DE LISTE DE CHAPITRES — chapter_list.py
    # ========================================================================
    # Widget
    "ChapterList",
    # Enums
    "ChapterListMode",
    "ChapterFilter",
    "ChapterSortBy",
    # Modèles
    "ChapterListConfig",
    "ChapterListFilters",
    "ChapterListState",
    # Helpers
    "format_chapter_number",
    "format_chapter_status",
    "format_chapter_date",
    "create_chapter_list",
    # Exceptions
    "ChapterListError",
    "InvalidChapterError",
]

__version__: str = "0.1.0"
