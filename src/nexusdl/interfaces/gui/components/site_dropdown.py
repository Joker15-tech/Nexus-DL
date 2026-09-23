"""Composant GUI de dropdown de sélection de sites pour NexusDL.

Ce module fournit un widget PyQt6 personnalisé pour la sélection de sites
dans l'interface graphique de NexusDL. Il offre une expérience utilisateur
riche avec recherche, filtrage, sélection multiple, et un style cyberpunk
néon cohérent avec le reste de l'application.

**Fonctionnalités** :
    - Dropdown avec recherche intégrée (auto-complétion)
    - Popup de liste avec filtrage par langue, statut, capacités
    - Sélection simple ou multiple (configurable)
    - Badges visuels (langue, Cloudflare, auth, adulte)
    - Icônes de statut (activé/désactivé)
    - Tri par nom, langue, priorité
    - Option "Tous les sites" / "Aucun site"
    - Raccourcis clavier (Ctrl+A, Escape, flèches)
    - Style cyberpunk néon (vert/cyan/magenta)
    - Signaux Qt pour communication
    - Intégration avec SiteRegistry
    - Gestion des erreurs et fallback

**Architecture** :
    SiteDropdown (QWidget principal)
        ├── SiteDropdownButton (bouton déclencheur)
        │   ├── Label (texte du site sélectionné)
        │   ├── Badge (indicateur de sélection multiple)
        │   └── Icon (flèche dropdown)
        ├── SiteDropdownPopup (popup de sélection)
        │   ├── SearchInput (champ de recherche)
        │   ├── FilterBar (filtres langue/statut)
        │   ├── SiteListView (liste des sites)
        │   │   └── SiteListItem (item individuel)
        │   └── ActionButtons (sélectionner tout / annuler)
        └── SiteDropdownModel (modèle de données)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.gui.components.site_dropdown import SiteDropdown
    >>>
    >>> # Dans une fenêtre PyQt6
    >>> dropdown = SiteDropdown(
    ...     mode=SelectionMode.MULTIPLE,
    ...     parent=self,
    ... )
    >>> dropdown.site_selected.connect(self.on_site_selected)
    >>> layout.addWidget(dropdown)
    >>>
    >>> # Récupérer la sélection
    >>> selected = dropdown.get_selected_sites()
    >>> print([s.id for s in selected])

Intégration :
    - core/registry/site_registry.py : accès aux sites
    - core/models/site.py : modèles SiteConfig
    - core/events.py : émission d'événements
    - core/i18n.py : traductions
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, Final

from loguru import logger

try:
    from PyQt6.QtCore import (
        Qt,
        QTimer,
        pyqtSignal,
        pyqtSlot,
        QEvent,
        QPoint,
        QSize,
        QSortFilterProxyModel,
        QStringListModel,
    )
    from PyQt6.QtGui import (
        QAction,
        QColor,
        QFont,
        QIcon,
        QKeyEvent,
        QMouseEvent,
        QPalette,
        QPaintEvent,
        QPainter,
        QPen,
        QBrush,
    )
    from PyQt6.QtWidgets import (
        QApplication,
        QComboBox,
        QFrame,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMenu,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QStyle,
        QStyledItemDelegate,
        QVBoxLayout,
        QWidget,
    )
    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False

from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t


# ============================================================================
# CONSTANTES
# ============================================================================


# Couleurs du thème cyberpunk néon
COLOR_PRIMARY: Final[str] = "#00ff41"
COLOR_PRIMARY_DIM: Final[str] = "#00cc33"
COLOR_PRIMARY_BG: Final[str] = "#001a0d"
COLOR_SECONDARY: Final[str] = "#00ffff"
COLOR_SECONDARY_DIM: Final[str] = "#00cccc"
COLOR_ACCENT: Final[str] = "#ff00ff"
COLOR_ACCENT_DIM: Final[str] = "#cc00cc"
COLOR_SUCCESS: Final[str] = "#00ff41"
COLOR_WARNING: Final[str] = "#ffff00"
COLOR_ERROR: Final[str] = "#ff0040"
COLOR_INFO: Final[str] = "#00ffff"
COLOR_BACKGROUND: Final[str] = "#000000"
COLOR_SURFACE: Final[str] = "#0d1117"
COLOR_SURFACE_ALT: Final[str] = "#161b22"
COLOR_SURFACE_HOVER: Final[str] = "#1a1f2e"
COLOR_TEXT: Final[str] = "#00ff41"
COLOR_TEXT_BRIGHT: Final[str] = "#ffffff"
COLOR_TEXT_MUTED: Final[str] = "#00aaaa"
COLOR_TEXT_DIM: Final[str] = "#006666"
COLOR_TEXT_DISABLED: Final[str] = "#004444"
COLOR_BORDER: Final[str] = "#00ff41"
COLOR_BORDER_DIM: Final[str] = "#006622"
COLOR_BORDER_FOCUS: Final[str] = "#00ffff"

# Dimensions
DROPDOWN_MIN_WIDTH: Final[int] = 250
DROPDOWN_MAX_WIDTH: Final[int] = 400
POPUP_MAX_HEIGHT: Final[int] = 400
ITEM_HEIGHT: Final[int] = 36
SEARCH_HEIGHT: Final[int] = 32
BUTTON_HEIGHT: Final[int] = 28


# ============================================================================
# EXCEPTIONS
# ============================================================================


class SiteDropdownError(NexusDLError):
    """Exception de base pour les erreurs du dropdown de sites."""


class RegistryNotAvailableError(SiteDropdownError):
    """Exception levée lorsque le SiteRegistry n'est pas disponible."""

    def __init__(self) -> None:
        super().__init__(
            "SiteRegistry n'est pas disponible. "
            "Assurez-vous que l'application est correctement initialisée."
        )


# ============================================================================
# ENUMS
# ============================================================================


class SelectionMode(str, Enum):
    """Mode de sélection du dropdown.

    Attributes:
        SINGLE: Un seul site à la fois.
        MULTIPLE: Plusieurs sites.
        NONE: Pas de sélection (affichage seul).
    """

    SINGLE = "single"
    MULTIPLE = "multiple"
    NONE = "none"


class SiteFilter(str, Enum):
    """Filtre de sites.

    Attributes:
        ALL: Tous les sites.
        ENABLED: Sites activés uniquement.
        DISABLED: Sites désactivés uniquement.
        NO_CLOUDFLARE: Sites sans Cloudflare.
        NO_AUTH: Sites sans authentification.
    """

    ALL = "all"
    ENABLED = "enabled"
    DISABLED = "disabled"
    NO_CLOUDFLARE = "no_cloudflare"
    NO_AUTH = "no_auth"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            SiteFilter.ALL: t("dropdown.filter.all", default="All Sites"),
            SiteFilter.ENABLED: t("dropdown.filter.enabled", default="Enabled"),
            SiteFilter.DISABLED: t("dropdown.filter.disabled", default="Disabled"),
            SiteFilter.NO_CLOUDFLARE: t("dropdown.filter.no_cf", default="No Cloudflare"),
            SiteFilter.NO_AUTH: t("dropdown.filter.no_auth", default="No Auth"),
        }[self]


class LanguageFilter(str, Enum):
    """Filtre par langue.

    Attributes:
        ALL: Toutes les langues.
        FR: Français.
        EN: Anglais.
        JA: Japonais.
        KO: Coréen.
        ZH: Chinois.
        MULTI: Multilingue.
    """

    ALL = "all"
    FR = "fr"
    EN = "en"
    JA = "ja"
    KO = "ko"
    ZH = "zh"
    MULTI = "multi"

    @property
    def label(self) -> str:
        """Libellé avec drapeau."""
        flags = {
            "all": "🌐",
            "fr": "🇫🇷",
            "en": "🇬🇧",
            "ja": "🇯🇵",
            "ko": "🇰🇷",
            "zh": "🇨🇳",
            "multi": "🌍",
        }
        names = {
            "all": t("dropdown.lang.all", default="All"),
            "fr": "Français",
            "en": "English",
            "ja": "日本語",
            "ko": "한국어",
            "zh": "中文",
            "multi": "Multi",
        }
        return f"{flags.get(self.value, '🏳️')} {names.get(self.value, self.value)}"


# ============================================================================
# STYLESHEET — Thème cyberpunk néon
# ============================================================================


SITE_DROPDOWN_STYLESHEET: Final[str] = f"""
/* ============================================================================
   SiteDropdown — Thème Cyberpunk Neon
   ============================================================================ */

/* Bouton principal du dropdown */
SiteDropdownButton {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT};
    border: 2px solid {COLOR_BORDER_DIM};
    border-radius: 4px;
    padding: 6px 12px;
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 13px;
    min-height: 28px;
}}

SiteDropdownButton:hover {{
    background-color: {COLOR_SURFACE_HOVER};
    border-color: {COLOR_SECONDARY};
    color: {COLOR_SECONDARY};
}}

SiteDropdownButton:focus {{
    border-color: {COLOR_BORDER_FOCUS};
    outline: none;
}}

SiteDropdownButton:pressed {{
    background-color: {COLOR_PRIMARY_BG};
    border-color: {COLOR_PRIMARY};
}}

SiteDropdownButton[open="true"] {{
    border-color: {COLOR_PRIMARY};
    background-color: {COLOR_PRIMARY_BG};
    border-bottom-left-radius: 0px;
    border-bottom-right-radius: 0px;
}}

/* Label du site sélectionné */
#site-label {{
    color: {COLOR_TEXT};
    font-weight: bold;
}}

#site-label[multiple="true"] {{
    color: {COLOR_SECONDARY};
}}

/* Badge de compteur */
#site-badge {{
    background-color: {COLOR_PRIMARY};
    color: {COLOR_BACKGROUND};
    border-radius: 8px;
    padding: 1px 6px;
    font-size: 11px;
    font-weight: bold;
    min-width: 16px;
}}

/* Icône de flèche */
#arrow-icon {{
    color: {COLOR_TEXT_MUTED};
    font-size: 10px;
}}

SiteDropdownButton:hover #arrow-icon {{
    color: {COLOR_SECONDARY};
}}

/* Popup de sélection */
SiteDropdownPopup {{
    background-color: {COLOR_SURFACE};
    border: 2px solid {COLOR_PRIMARY};
    border-top: none;
    border-bottom-left-radius: 4px;
    border-bottom-right-radius: 4px;
}}

/* Champ de recherche */
#search-input {{
    background-color: {COLOR_SURFACE_ALT};
    color: {COLOR_PRIMARY};
    border: 1px solid {COLOR_BORDER_DIM};
    border-radius: 3px;
    padding: 4px 8px;
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 12px;
    selection-background-color: {COLOR_PRIMARY};
    selection-color: {COLOR_BACKGROUND};
}}

#search-input:focus {{
    border-color: {COLOR_SECONDARY};
    color: {COLOR_PRIMARY};
}}

#search-input::placeholder {{
    color: {COLOR_TEXT_DIM};
}}

/* Barre de filtres */
#filter-bar {{
    background-color: {COLOR_SURFACE_ALT};
    border-bottom: 1px solid {COLOR_BORDER_DIM};
    padding: 4px;
}}

#filter-combo {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT_MUTED};
    border: 1px solid {COLOR_BORDER_DIM};
    border-radius: 3px;
    padding: 2px 6px;
    font-size: 11px;
}}

#filter-combo::drop-down {{
    border: none;
    width: 16px;
}}

#filter-combo QAbstractItemView {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER_DIM};
    selection-background-color: {COLOR_PRIMARY_BG};
    selection-color: {COLOR_PRIMARY};
}}

/* Liste des sites */
#site-list {{
    background-color: {COLOR_SURFACE};
    border: none;
    outline: none;
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 12px;
}}

#site-list::item {{
    background-color: transparent;
    color: {COLOR_TEXT};
    padding: 6px 8px;
    border-left: 3px solid transparent;
    min-height: {ITEM_HEIGHT - 12}px;
}}

#site-list::item:hover {{
    background-color: {COLOR_SURFACE_HOVER};
    color: {COLOR_PRIMARY};
    border-left-color: {COLOR_SECONDARY_DIM};
}}

#site-list::item:selected {{
    background-color: {COLOR_PRIMARY_BG};
    color: {COLOR_PRIMARY};
    border-left-color: {COLOR_PRIMARY};
    font-weight: bold;
}}

#site-list::item[disabled="true"] {{
    color: {COLOR_TEXT_DISABLED};
    opacity: 0.5;
}}

/* Boutons d'action */
#action-bar {{
    background-color: {COLOR_SURFACE_ALT};
    border-top: 1px solid {COLOR_BORDER_DIM};
    padding: 4px;
}}

#btn-select-all, #btn-deselect-all {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT_MUTED};
    border: 1px solid {COLOR_BORDER_DIM};
    border-radius: 3px;
    padding: 3px 8px;
    font-size: 11px;
}}

#btn-select-all:hover, #btn-deselect-all:hover {{
    background-color: {COLOR_PRIMARY_BG};
    color: {COLOR_PRIMARY};
    border-color: {COLOR_PRIMARY};
}}

/* Indicateurs de site */
.site-enabled {{
    color: {COLOR_SUCCESS};
}}

.site-disabled {{
    color: {COLOR_TEXT_DISABLED};
}}

.site-cloudflare {{
    color: {COLOR_WARNING};
}}

.site-auth {{
    color: {COLOR_ACCENT};
}}

.site-adult {{
    color: {COLOR_ERROR};
}}

/* Scrollbar */
QScrollBar:vertical {{
    background-color: {COLOR_SURFACE};
    width: 8px;
    border: none;
}}

QScrollBar::handle:vertical {{
    background-color: {COLOR_BORDER_DIM};
    border-radius: 4px;
    min-height: 20px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {COLOR_PRIMARY};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}
"""


# ============================================================================
# WIDGETS — Composants PyQt6
# ============================================================================


if PYQT6_AVAILABLE:

    class SiteDropdownButton(QPushButton):
        """Bouton déclencheur du dropdown de sites.

        Affiche le site sélectionné (ou le nombre de sites sélectionnés)
        et ouvre le popup de sélection au clic.
        """

        # Signaux
        dropdown_opened = pyqtSignal()
        dropdown_closed = pyqtSignal()

        def __init__(
            self,
            *,
            mode: SelectionMode = SelectionMode.SINGLE,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise le bouton.

            Args:
                mode: Mode de sélection.
                parent: Widget parent.
            """
            super().__init__(parent)
            self._mode = mode
            self._is_open = False
            self._display_text = t("dropdown.placeholder", default="Select a site...")
            self._selected_count = 0

            # Configuration du bouton
            self.setObjectName("SiteDropdownButton")
            self.setMinimumWidth(DROPDOWN_MIN_WIDTH)
            self.setMaximumWidth(DROPDOWN_MAX_WIDTH)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

            # Layout interne
            layout = QHBoxLayout(self)
            layout.setContentsMargins(8, 4, 8, 4)
            layout.setSpacing(6)

            # Label du site
            self._label = QLabel(self._display_text)
            self._label.setObjectName("site-label")
            self._label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            layout.addWidget(self._label)

            # Badge de compteur (mode multiple)
            self._badge = QLabel("")
            self._badge.setObjectName("site-badge")
            self._badge.setVisible(False)
            layout.addWidget(self._badge)

            # Icône de flèche
            self._arrow = QLabel("▼")
            self._arrow.setObjectName("arrow-icon")
            layout.addWidget(self._arrow)

        def set_display_text(self, text: str) -> None:
            """Définit le texte affiché.

            Args:
                text: Texte à afficher.
            """
            self._display_text = text
            self._label.setText(text)

        def set_selected_count(self, count: int) -> None:
            """Définit le nombre de sites sélectionnés.

            Args:
                count: Nombre de sites.
            """
            self._selected_count = count

            if self._mode == SelectionMode.MULTIPLE and count > 0:
                self._badge.setText(str(count))
                self._badge.setVisible(True)
                self._label.setProperty("multiple", "true")
            else:
                self._badge.setVisible(False)
                self._label.setProperty("multiple", "false")

            self._label.style().unpolish(self._label)
            self._label.style().polish(self._label)

        def set_open(self, is_open: bool) -> None:
            """Définit l'état d'ouverture.

            Args:
                is_open: True si le popup est ouvert.
            """
            self._is_open = is_open
            self.setProperty("open", "true" if is_open else "false")
            self._arrow.setText("▲" if is_open else "▼")
            self.style().unpolish(self)
            self.style().polish(self)

            if is_open:
                self.dropdown_opened.emit()
            else:
                self.dropdown_closed.emit()

        @property
        def is_open(self) -> bool:
            """Indique si le popup est ouvert."""
            return self._is_open

    class SiteListItemWidget(QWidget):
        """Widget personnalisé pour un item de site dans la liste.

        Affiche le nom du site, la langue, et les badges de capacités.
        """

        def __init__(
            self,
            site_id: str,
            site_name: str,
            language: str = "en",
            *,
            enabled: bool = True,
            has_cloudflare: bool = False,
            has_auth: bool = False,
            is_adult: bool = False,
            selected: bool = False,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise l'item.

            Args:
                site_id: ID du site.
                site_name: Nom du site.
                language: Code langue.
                enabled: Si le site est activé.
                has_cloudflare: Si le site utilise Cloudflare.
                has_auth: Si le site nécessite une authentification.
                is_adult: Si le site est adulte.
                selected: Si l'item est sélectionné.
                parent: Widget parent.
            """
            super().__init__(parent)
            self.site_id = site_id
            self.site_name = site_name
            self.language = language
            self.enabled = enabled
            self.has_cloudflare = has_cloudflare
            self.has_auth = has_auth
            self.is_adult = is_adult
            self.selected = selected

            # Configuration
            self.setMinimumHeight(ITEM_HEIGHT)
            self.setMaximumHeight(ITEM_HEIGHT)

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(8, 4, 8, 4)
            layout.setSpacing(8)

            # Checkbox (mode multiple)
            self._checkbox_label = QLabel("☐" if not selected else "☑")
            self._checkbox_label.setFixedWidth(16)
            self._checkbox_label.setStyleSheet(f"color: {COLOR_PRIMARY}; font-size: 14px;")
            layout.addWidget(self._checkbox_label)

            # Drapeau de langue
            flags = {
                "fr": "🇫🇷", "en": "🇬🇧", "ja": "🇯🇵", "ko": "🇰🇷",
                "zh": "🇨🇳", "de": "🇩🇪", "es": "🇪🇸", "pt": "🇵🇹",
                "ru": "🇷🇺", "it": "🇮🇹", "multi": "🌍",
            }
            flag = flags.get(language, "🏳️")
            self._flag_label = QLabel(flag)
            self._flag_label.setFixedWidth(20)
            layout.addWidget(self._flag_label)

            # Nom du site
            self._name_label = QLabel(site_name)
            self._name_label.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
            name_color = COLOR_TEXT if enabled else COLOR_TEXT_DISABLED
            name_style = "font-weight: bold;" if enabled else ""
            self._name_label.setStyleSheet(f"color: {name_color}; {name_style}")
            layout.addWidget(self._name_label)

            # Badges
            badges: list[str] = []
            if has_cloudflare:
                badges.append(f'<span style="color: {COLOR_WARNING};">🛡️</span>')
            if has_auth:
                badges.append(f'<span style="color: {COLOR_ACCENT};">🔐</span>')
            if is_adult:
                badges.append(f'<span style="color: {COLOR_ERROR};">🔞</span>')

            if badges:
                self._badges_label = QLabel(" ".join(badges))
                self._badges_label.setTextFormat(Qt.TextFormat.RichText)
                layout.addWidget(self._badges_label)

            # Statut
            status_icon = "●" if enabled else "○"
            status_color = COLOR_SUCCESS if enabled else COLOR_TEXT_DISABLED
            self._status_label = QLabel(status_icon)
            self._status_label.setStyleSheet(f"color: {status_color}; font-size: 10px;")
            layout.addWidget(self._status_label)

        def set_selected(self, selected: bool) -> None:
            """Définit l'état de sélection.

            Args:
                selected: True si sélectionné.
            """
            self.selected = selected
            self._checkbox_label.setText("☑" if selected else "☐")

    class SiteDropdownPopup(QFrame):
        """Popup de sélection des sites.

        Contient le champ de recherche, les filtres, et la liste des sites.
        """

        # Signaux
        site_selected = pyqtSignal(str)  # site_id
        site_toggled = pyqtSignal(str, bool)  # site_id, selected
        closed = pyqtSignal()

        def __init__(
            self,
            *,
            mode: SelectionMode = SelectionMode.SINGLE,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise le popup.

            Args:
                mode: Mode de sélection.
                parent: Widget parent.
            """
            super().__init__(parent)
            self._mode = mode
            self._sites: list[dict[str, Any]] = []
            self._selected_ids: set[str] = set()

            # Configuration du popup
            self.setObjectName("SiteDropdownPopup")
            self.setFrameShape(QFrame.Shape.StyledPanel)
            self.setWindowFlags(
                Qt.WindowType.Popup
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.NoDropShadowWindowHint
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
            self.setMinimumWidth(DROPDOWN_MIN_WIDTH)
            self.setMaximumWidth(DROPDOWN_MAX_WIDTH)
            self.setMaximumHeight(POPUP_MAX_HEIGHT)

            # Layout principal
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            # Champ de recherche
            self._search_input = QLineEdit()
            self._search_input.setObjectName("search-input")
            self._search_input.setPlaceholderText(
                t("dropdown.search.placeholder", default="🔍 Search sites...")
            )
            self._search_input.setFixedHeight(SEARCH_HEIGHT)
            self._search_input.textChanged.connect(self._on_search_changed)
            layout.addWidget(self._search_input)

            # Barre de filtres
            filter_layout = QHBoxLayout()
            filter_layout.setContentsMargins(4, 4, 4, 4)
            filter_layout.setSpacing(4)

            filter_widget = QWidget()
            filter_widget.setObjectName("filter-bar")
            filter_widget.setLayout(filter_layout)

            # Filtre langue
            self._lang_filter = QComboBox()
            self._lang_filter.setObjectName("filter-combo")
            for lang in LanguageFilter:
                self._lang_filter.addItem(lang.label, lang.value)
            self._lang_filter.currentIndexChanged.connect(self._on_filter_changed)
            filter_layout.addWidget(self._lang_filter)

            # Filtre statut
            self._status_filter = QComboBox()
            self._status_filter.setObjectName("filter-combo")
            for status in SiteFilter:
                self._status_filter.addItem(status.label, status.value)
            self._status_filter.currentIndexChanged.connect(self._on_filter_changed)
            filter_layout.addWidget(self._status_filter)

            filter_layout.addStretch()
            layout.addWidget(filter_widget)

            # Liste des sites
            self._site_list = QListWidget()
            self._site_list.setObjectName("site-list")
            self._site_list.setSelectionMode(
                QListWidget.SelectionMode.MultiSelection
                if mode == SelectionMode.MULTIPLE
                else QListWidget.SelectionMode.SingleSelection
            )
            self._site_list.itemClicked.connect(self._on_item_clicked)
            layout.addWidget(self._site_list)

            # Barre d'actions (mode multiple)
            if mode == SelectionMode.MULTIPLE:
                action_layout = QHBoxLayout()
                action_layout.setContentsMargins(4, 4, 4, 4)
                action_layout.setSpacing(4)

                action_widget = QWidget()
                action_widget.setObjectName("action-bar")
                action_widget.setLayout(action_layout)

                self._btn_select_all = QPushButton(
                    t("dropdown.select_all", default="Select All")
                )
                self._btn_select_all.setObjectName("btn-select-all")
                self._btn_select_all.clicked.connect(self._on_select_all)
                action_layout.addWidget(self._btn_select_all)

                self._btn_deselect_all = QPushButton(
                    t("dropdown.deselect_all", default="Deselect All")
                )
                self._btn_deselect_all.setObjectName("btn-deselect-all")
                self._btn_deselect_all.clicked.connect(self._on_deselect_all)
                action_layout.addWidget(self._btn_deselect_all)

                action_layout.addStretch()
                layout.addWidget(action_widget)

        def set_sites(self, sites: list[dict[str, Any]]) -> None:
            """Définit la liste des sites.

            Args:
                sites: Liste de dictionnaires avec les informations des sites.
                    Chaque dict doit contenir : id, name, language, enabled,
                    has_cloudflare, has_auth, is_adult.
            """
            self._sites = sites
            self._refresh_list()

        def set_selected_ids(self, ids: set[str]) -> None:
            """Définit les IDs des sites sélectionnés.

            Args:
                ids: Set d'IDs de sites.
            """
            self._selected_ids = set(ids)
            self._refresh_list()

        def get_selected_ids(self) -> set[str]:
            """Récupère les IDs des sites sélectionnés.

            Returns:
                Set d'IDs de sites.
            """
            return set(self._selected_ids)

        def _refresh_list(self, search_query: str = "") -> None:
            """Rafraîchit la liste des sites avec les filtres actuels.

            Args:
                search_query: Texte de recherche.
            """
            self._site_list.clear()

            # Récupérer les filtres
            lang_filter = self._lang_filter.currentData()
            status_filter = self._status_filter.currentData()

            for site in self._sites:
                # Filtrer par recherche
                if search_query:
                    query_lower = search_query.lower()
                    if (
                        query_lower not in site.get("name", "").lower()
                        and query_lower not in site.get("id", "").lower()
                    ):
                        continue

                # Filtrer par langue
                if lang_filter and lang_filter != "all":
                    if site.get("language", "en") != lang_filter:
                        continue

                # Filtrer par statut
                if status_filter == "enabled" and not site.get("enabled", True):
                    continue
                elif status_filter == "disabled" and site.get("enabled", True):
                    continue
                elif status_filter == "no_cloudflare" and site.get("has_cloudflare", False):
                    continue
                elif status_filter == "no_auth" and site.get("has_auth", False):
                    continue

                # Créer l'item
                site_id = site.get("id", "")
                is_selected = site_id in self._selected_ids

                item_widget = SiteListItemWidget(
                    site_id=site_id,
                    site_name=site.get("name", site_id),
                    language=site.get("language", "en"),
                    enabled=site.get("enabled", True),
                    has_cloudflare=site.get("has_cloudflare", False),
                    has_auth=site.get("has_auth", False),
                    is_adult=site.get("is_adult", False),
                    selected=is_selected,
                )

                item = QListWidgetItem()
                item.setSizeHint(QSize(DROPDOWN_MIN_WIDTH, ITEM_HEIGHT))
                item.setData(Qt.ItemDataRole.UserRole, site_id)

                self._site_list.addItem(item)
                self._site_list.setItemWidget(item, item_widget)

        def _on_search_changed(self, text: str) -> None:
            """Gère le changement de texte de recherche."""
            self._refresh_list(search_query=text)

        def _on_filter_changed(self) -> None:
            """Gère le changement de filtre."""
            search_query = self._search_input.text()
            self._refresh_list(search_query=search_query)

        def _on_item_clicked(self, item: QListWidgetItem) -> None:
            """Gère le clic sur un item."""
            site_id = item.data(Qt.ItemDataRole.UserRole)
            if not site_id:
                return

            if self._mode == SelectionMode.SINGLE:
                self._selected_ids = {site_id}
                self.site_selected.emit(site_id)
                self._refresh_list(self._search_input.text())
                self.closed.emit()

            elif self._mode == SelectionMode.MULTIPLE:
                if site_id in self._selected_ids:
                    self._selected_ids.discard(site_id)
                    self.site_toggled.emit(site_id, False)
                else:
                    self._selected_ids.add(site_id)
                    self.site_toggled.emit(site_id, True)
                self._refresh_list(self._search_input.text())

        def _on_select_all(self) -> None:
            """Sélectionne tous les sites filtrés."""
            for i in range(self._site_list.count()):
                item = self._site_list.item(i)
                site_id = item.data(Qt.ItemDataRole.UserRole)
                if site_id:
                    self._selected_ids.add(site_id)
            self._refresh_list(self._search_input.text())

        def _on_deselect_all(self) -> None:
            """Désélectionne tous les sites."""
            self._selected_ids.clear()
            self._refresh_list(self._search_input.text())

        def show_at(self, pos: QPoint) -> None:
            """Affiche le popup à une position donnée.

            Args:
                pos: Position globale.
            """
            self.move(pos)
            self.show()
            self._search_input.setFocus()

        def keyPressEvent(self, event: QKeyEvent) -> None:
            """Gère les événements clavier."""
            if event.key() == Qt.Key.Key_Escape:
                self.closed.emit()
            elif event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
                # Sélectionner l'item courant
                current = self._site_list.currentItem()
                if current:
                    self._on_item_clicked(current)
            else:
                super().keyPressEvent(event)

    class SiteDropdown(QWidget):
        """Widget principal de dropdown de sélection de sites.

        Combine le bouton déclencheur et le popup de sélection en un
        seul widget cohérent.

        Signals:
            site_selected(str): Émis lorsqu'un site est sélectionné (mode single).
            sites_changed(list): Émis lorsque la sélection change (mode multiple).
            dropdown_opened(): Émis lorsque le dropdown s'ouvre.
            dropdown_closed(): Émis lorsque le dropdown se ferme.
        """

        # Signaux
        site_selected = pyqtSignal(str)
        sites_changed = pyqtSignal(list)
        dropdown_opened = pyqtSignal()
        dropdown_closed = pyqtSignal()

        def __init__(
            self,
            *,
            mode: SelectionMode = SelectionMode.SINGLE,
            include_all_option: bool = True,
            include_disabled: bool = False,
            include_adult: bool = False,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise le dropdown.

            Args:
                mode: Mode de sélection.
                include_all_option: Inclure l'option "Tous les sites".
                include_disabled: Inclure les sites désactivés.
                include_adult: Inclure les sites adultes.
                parent: Widget parent.
            """
            super().__init__(parent)
            self._mode = mode
            self._include_all_option = include_all_option
            self._include_disabled = include_disabled
            self._include_adult = include_adult
            self._selected_ids: set[str] = set()
            self._sites: list[dict[str, Any]] = []

            # Appliquer le stylesheet
            self.setStyleSheet(SITE_DROPDOWN_STYLESHEET)

            # Layout principal
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            # Bouton déclencheur
            self._button = SiteDropdownButton(mode=mode, parent=self)
            self._button.clicked.connect(self._toggle_popup)
            layout.addWidget(self._button)

            # Popup de sélection
            self._popup = SiteDropdownPopup(mode=mode, parent=self)
            self._popup.site_selected.connect(self._on_site_selected)
            self._popup.site_toggled.connect(self._on_site_toggled)
            self._popup.closed.connect(self._close_popup)

            # Charger les sites
            self._load_sites()

        def _load_sites(self) -> None:
            """Charge les sites depuis le registre."""
            try:
                from nexusdl.core.registry import get_site_registry

                registry = get_site_registry()
                sites = registry.list_sites(
                    enabled_only=not self._include_disabled,
                    include_adult=self._include_adult,
                )

                self._sites = [
                    {
                        "id": site.id,
                        "name": site.name,
                        "language": site.language.value,
                        "enabled": site.enabled,
                        "has_cloudflare": site.capabilities.requires_cloudflare_bypass,
                        "has_auth": site.capabilities.requires_auth,
                        "is_adult": site.adult,
                    }
                    for site in sites
                ]

                # Ajouter l'option "Tous les sites"
                if self._include_all_option and self._mode == SelectionMode.MULTIPLE:
                    self._sites.insert(0, {
                        "id": "__all__",
                        "name": t("dropdown.all_sites", default="All Sites"),
                        "language": "multi",
                        "enabled": True,
                        "has_cloudflare": False,
                        "has_auth": False,
                        "is_adult": False,
                    })

                self._popup.set_sites(self._sites)

                logger.debug("Sites chargés dans le dropdown: {}", len(self._sites))

            except Exception as e:
                logger.warning("Impossible de charger les sites pour le dropdown: {}", e)
                self._sites = []
                self._popup.set_sites([])

        def _toggle_popup(self) -> None:
            """Bascule l'état d'ouverture du popup."""
            if self._button.is_open:
                self._close_popup()
            else:
                self._open_popup()

        def _open_popup(self) -> None:
            """Ouvre le popup de sélection."""
            self._button.set_open(True)
            self._popup.set_selected_ids(self._selected_ids)

            # Positionner le popup sous le bouton
            button_rect = self._button.geometry()
            global_pos = self._button.mapToGlobal(
                QPoint(0, button_rect.height())
            )
            self._popup.show_at(global_pos)
            self.dropdown_opened.emit()

        def _close_popup(self) -> None:
            """Ferme le popup de sélection."""
            self._button.set_open(False)
            self._popup.hide()
            self.dropdown_closed.emit()

        def _on_site_selected(self, site_id: str) -> None:
            """Gère la sélection d'un site (mode single)."""
            self._selected_ids = {site_id}
            self._update_display()
            self.site_selected.emit(site_id)

        def _on_site_toggled(self, site_id: str, selected: bool) -> None:
            """Gère le toggle d'un site (mode multiple)."""
            if selected:
                self._selected_ids.add(site_id)
            else:
                self._selected_ids.discard(site_id)

            self._update_display()
            self.sites_changed.emit(list(self._selected_ids))

        def _update_display(self) -> None:
            """Met à jour l'affichage du bouton."""
            if not self._selected_ids:
                self._button.set_display_text(
                    t("dropdown.placeholder", default="Select a site...")
                )
                self._button.set_selected_count(0)
                return

            if self._mode == SelectionMode.SINGLE:
                site_id = next(iter(self._selected_ids))
                site_name = self._get_site_name(site_id)
                self._button.set_display_text(site_name)
                self._button.set_selected_count(0)

            elif self._mode == SelectionMode.MULTIPLE:
                count = len(self._selected_ids)
                if count == 1:
                    site_id = next(iter(self._selected_ids))
                    site_name = self._get_site_name(site_id)
                    self._button.set_display_text(site_name)
                else:
                    self._button.set_display_text(
                        t(
                            "dropdown.multiple_selected",
                            default="{count} sites selected",
                            count=count,
                        )
                    )
                self._button.set_selected_count(count)

        def _get_site_name(self, site_id: str) -> str:
            """Récupère le nom d'un site par son ID.

            Args:
                site_id: ID du site.

            Returns:
                Nom du site ou ID si introuvable.
            """
            for site in self._sites:
                if site.get("id") == site_id:
                    return site.get("name", site_id)
            return site_id

        # =====================================================================
        # API PUBLIQUE
        # =====================================================================

        def get_selected_sites(self) -> list[str]:
            """Récupère les IDs des sites sélectionnés.

            Returns:
                Liste d'IDs de sites.
            """
            return list(self._selected_ids)

        def set_selected_sites(self, site_ids: list[str]) -> None:
            """Définit les sites sélectionnés.

            Args:
                site_ids: Liste d'IDs de sites.
            """
            if self._mode == SelectionMode.SINGLE:
                self._selected_ids = {site_ids[0]} if site_ids else set()
            else:
                self._selected_ids = set(site_ids)

            self._popup.set_selected_ids(self._selected_ids)
            self._update_display()

        def clear_selection(self) -> None:
            """Efface la sélection."""
            self._selected_ids.clear()
            self._popup.set_selected_ids(self._selected_ids)
            self._update_display()

        def reload_sites(self) -> None:
            """Recharge les sites depuis le registre."""
            self._load_sites()
            self._update_display()

        @property
        def mode(self) -> SelectionMode:
            """Mode de sélection."""
            return self._mode

        @property
        def selected_count(self) -> int:
            """Nombre de sites sélectionnés."""
            return len(self._selected_ids)

        def focusInEvent(self, event: Any) -> None:
            """Gère le focus entrant."""
            self._button.setFocus()
            super().focusInEvent(event)

        def hideEvent(self, event: Any) -> None:
            """Gère la dissimulation du widget."""
            self._close_popup()
            super().hideEvent(event)


# ============================================================================
# FONCTIONS HELPERS
# ============================================================================


def create_site_dropdown(
    *,
    mode: SelectionMode = SelectionMode.SINGLE,
    include_all_option: bool = True,
    include_disabled: bool = False,
    include_adult: bool = False,
    parent: Any = None,
) -> Any:
    """Crée un dropdown de sites avec une configuration simplifiée.

    Args:
        mode: Mode de sélection.
        include_all_option: Inclure l'option "Tous les sites".
        include_disabled: Inclure les sites désactivés.
        include_adult: Inclure les sites adultes.
        parent: Widget parent.

    Returns:
        Instance de SiteDropdown.
    """
    if not PYQT6_AVAILABLE:
        raise SiteDropdownError(
            "PyQt6 n'est pas installé. Installez-le avec: pip install PyQt6"
        )

    return SiteDropdown(
        mode=mode,
        include_all_option=include_all_option,
        include_disabled=include_disabled,
        include_adult=include_adult,
        parent=parent,
    )


def is_pyqt6_available() -> bool:
    """Vérifie si PyQt6 est disponible.

    Returns:
        True si PyQt6 est installé.
    """
    return PYQT6_AVAILABLE


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "COLOR_PRIMARY",
    "COLOR_SECONDARY",
    "COLOR_ACCENT",
    "COLOR_BACKGROUND",
    "COLOR_SURFACE",
    "COLOR_TEXT",
    "DROPDOWN_MIN_WIDTH",
    "DROPDOWN_MAX_WIDTH",
    "POPUP_MAX_HEIGHT",
    "ITEM_HEIGHT",
    "SITE_DROPDOWN_STYLESHEET",
    # Exceptions
    "SiteDropdownError",
    "RegistryNotAvailableError",
    # Enums
    "SelectionMode",
    "SiteFilter",
    "LanguageFilter",
    # Widgets
    "SiteDropdown" if PYQT6_AVAILABLE else None,
    "SiteDropdownButton" if PYQT6_AVAILABLE else None,
    "SiteDropdownPopup" if PYQT6_AVAILABLE else None,
    "SiteListItemWidget" if PYQT6_AVAILABLE else None,
    # Helpers
    "create_site_dropdown",
    "is_pyqt6_available",
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
