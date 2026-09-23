"""Vue des paramètres pour l'interface graphique NexusDL.

Ce module fournit une vue PyQt6 complète pour gérer tous les paramètres
de l'application NexusDL. Elle offre une expérience utilisateur riche
avec navigation par sections, édition inline, validation, et sauvegarde
atomique de la configuration.

**Sections de paramètres** :
    - Application : langue, thème, timezone, sens de lecture
    - Network : timeouts, connexions, SSL, HTTP/2
    - Proxy : activation, URL, authentification, rotation
    - Download : concurrence, format, qualité, compression
    - Library : chemin, scan automatique, intervalle
    - Cloudflare : mode bypass, FlareSolverr, Playwright
    - Logging : niveau, format, rotation, rétention
    - Storage : cache, nettoyage, tailles max
    - Interface : Web UI, GUI, CLI

**Architecture** :
    SettingsView (QWidget principal)
        ├── SettingsSidebar (sidebar avec sections)
        │   └── SectionButton (bouton de section)
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
    - Signaux Qt pour communication
    - Traductions i18n
    - Gestion des erreurs
    - Style cyberpunk néon cohérent

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.gui.views.settings_view import SettingsView
    >>>
    >>> # Dans une fenêtre PyQt6
    >>> settings_view = SettingsView(parent=self)
    >>> settings_view.settings_saved.connect(self.on_settings_saved)
    >>> layout.addWidget(settings_view)

Intégration :
    - core/config.py       : lecture/écriture de la configuration
    - core/paths.py        : chemins des fichiers
    - core/i18n.py         : traductions
    - core/events.py       : émission d'événements
    - core/logger.py       : logs des actions
    - core/utils/filesystem.py : atomic_write
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Final

from loguru import logger

try:
    from PyQt6.QtCore import (
        QPoint,
        QRect,
        QSize,
        Qt,
        QTimer,
        pyqtSignal,
        pyqtSlot,
    )
    from PyQt6.QtGui import (
        QAction,
        QBrush,
        QColor,
        QFont,
        QIcon,
        QKeyEvent,
        QMouseEvent,
        QPainter,
        QPen,
    )
    from PyQt6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QFileDialog,
        QFrame,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSpinBox,
        QStackedWidget,
        QVBoxLayout,
        QWidget,
    )
    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False

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
# CONSTANTES
# ============================================================================


# Couleurs du thème cyberpunk néon
COLOR_PRIMARY: Final[str] = "#00ff41"
COLOR_PRIMARY_DIM: Final[str] = "#00cc33"
COLOR_SECONDARY: Final[str] = "#00ffff"
COLOR_ACCENT: Final[str] = "#ff00ff"
COLOR_SUCCESS: Final[str] = "#00ff41"
COLOR_WARNING: Final[str] = "#ffff00"
COLOR_ERROR: Final[str] = "#ff0040"
COLOR_BACKGROUND: Final[str] = "#000000"
COLOR_SURFACE: Final[str] = "#0d1117"
COLOR_SURFACE_ALT: Final[str] = "#161b22"
COLOR_SURFACE_HOVER: Final[str] = "#1a1f2e"
COLOR_TEXT: Final[str] = "#00ff41"
COLOR_TEXT_BRIGHT: Final[str] = "#ffffff"
COLOR_TEXT_MUTED: Final[str] = "#00aaaa"
COLOR_TEXT_DIM: Final[str] = "#006666"
COLOR_BORDER: Final[str] = "#00ff41"
COLOR_BORDER_DIM: Final[str] = "#006622"

# Dimensions
SIDEBAR_WIDTH: Final[int] = 200
SETTING_HEIGHT: Final[int] = 60
FOOTER_HEIGHT: Final[int] = 50


# ============================================================================
# EXCEPTIONS
# ============================================================================


class SettingsViewError(NexusDLError):
    """Exception de base pour les erreurs de la vue des paramètres."""


class SettingsValidationError(SettingsViewError):
    """Exception levée lorsqu'un paramètre est invalide."""

    def __init__(self, key: str, value: Any, reason: str = "") -> None:
        msg = f"Paramètre invalide: {key}={value!r}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.key = key
        self.value = value
        self.reason = reason


class SettingsSaveError(SettingsViewError):
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
        APPLICATION: Paramètres généraux.
        NETWORK: Paramètres réseau.
        PROXY: Paramètres de proxy.
        DOWNLOAD: Paramètres de téléchargement.
        LIBRARY: Paramètres de bibliothèque.
        CLOUDFLARE: Paramètres Cloudflare.
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
        """Libellé humain."""
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
        """Icône Unicode."""
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
        BOOL: Paramètre booléen.
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
# MODÈLES DE DONNÉES
# ============================================================================


class SettingDefinition:
    """Définition d'un paramètre.

    Attributes:
        key: Clé unique du paramètre.
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
    """

    def __init__(
        self,
        *,
        key: str,
        label: str,
        description: str = "",
        setting_type: SettingType,
        default: Any = None,
        current: Any = None,
        choices: list[Any] | None = None,
        min_value: float | None = None,
        max_value: float | None = None,
        placeholder: str = "",
        section: SettingsSection,
    ) -> None:
        """Initialise la définition."""
        self.key = key
        self.label = label
        self.description = description
        self.setting_type = setting_type
        self.default = default
        self.current = current if current is not None else default
        self.choices = choices
        self.min_value = min_value
        self.max_value = max_value
        self.placeholder = placeholder
        self.section = section
        self.modified = False

    def validate(self, value: Any) -> bool:
        """Valide une valeur pour ce paramètre.

        Args:
            value: Valeur à valider.

        Returns:
            True si la valeur est valide.
        """
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


class SettingsState:
    """État de la vue des paramètres.

    Attributes:
        definitions: Dictionnaire des définitions de paramètres.
        modified_count: Nombre de paramètres modifiés.
        last_saved_at: Timestamp de la dernière sauvegarde.
        has_unsaved_changes: Indique s'il y a des changements non sauvegardés.
    """

    def __init__(self) -> None:
        """Initialise l'état."""
        self.definitions: dict[str, SettingDefinition] = {}
        self.modified_count = 0
        self.last_saved_at: datetime | None = None
        self.has_unsaved_changes = False

    def mark_modified(self, key: str) -> None:
        """Marque un paramètre comme modifié.

        Args:
            key: Clé du paramètre.
        """
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
# WIDGETS — Composants PyQt6
# ============================================================================


if PYQT6_AVAILABLE:

    class SectionButton(QPushButton):
        """Bouton de section dans la sidebar."""

        def __init__(
            self,
            section: SettingsSection,
            *,
            modified_count: int = 0,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise le bouton.

            Args:
                section: Section représentée.
                modified_count: Nombre de paramètres modifiés.
                parent: Widget parent.
            """
            super().__init__(parent)
            self.section = section
            self.modified_count = modified_count

            # Configuration visuelle
            self.setCheckable(True)
            self.setFixedHeight(40)
            self.setCursor(Qt.CursorShape.PointingHandCursor)

            self._update_text()
            self._update_style()

        def _update_text(self) -> None:
            """Met à jour le texte du bouton."""
            modifier = f" ({self.modified_count})" if self.modified_count > 0 else ""
            self.setText(f"{self.section.icon} {self.section.label}{modifier}")

        def _update_style(self) -> None:
            """Met à jour le style du bouton."""
            if self.isChecked():
                self.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {COLOR_PRIMARY_BG};
                        color: {COLOR_PRIMARY};
                        border: 2px solid {COLOR_PRIMARY};
                        border-radius: 4px;
                        padding: 8px;
                        text-align: left;
                        font-weight: bold;
                        font-family: 'JetBrains Mono', monospace;
                        font-size: 12px;
                    }}
                """)
            else:
                self.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {COLOR_SURFACE};
                        color: {COLOR_TEXT};
                        border: 1px solid {COLOR_BORDER_DIM};
                        border-radius: 4px;
                        padding: 8px;
                        text-align: left;
                        font-family: 'JetBrains Mono', monospace;
                        font-size: 12px;
                    }}
                    QPushButton:hover {{
                        background-color: {COLOR_SURFACE_HOVER};
                        border-color: {COLOR_SECONDARY};
                    }}
                """)

        def set_modified_count(self, count: int) -> None:
            """Définit le nombre de paramètres modifiés.

            Args:
                count: Nombre de paramètres modifiés.
            """
            self.modified_count = count
            self._update_text()

    class SettingsSidebar(QFrame):
        """Sidebar avec les sections de paramètres."""

        # Signaux
        section_selected = pyqtSignal(object)  # SettingsSection

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la sidebar.

            Args:
                parent: Widget parent.
            """
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedWidth(SIDEBAR_WIDTH)
            self.setStyleSheet(f"""
                SettingsSidebar {{
                    background-color: {COLOR_SURFACE};
                    border-right: 2px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout
            layout = QVBoxLayout(self)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(4)

            # Titre
            title = QLabel(t("settings.sidebar.title", default="Settings"))
            title.setStyleSheet(f"""
                color: {COLOR_PRIMARY};
                font-size: 16px;
                font-weight: bold;
                font-family: 'JetBrains Mono', monospace;
                padding: 8px;
            """)
            layout.addWidget(title)

            # Boutons de sections
            self._buttons: dict[SettingsSection, SectionButton] = {}
            for section in SettingsSection:
                button = SectionButton(section, parent=self)
                button.clicked.connect(
                    lambda checked, s=section: self._on_section_clicked(s)
                )
                layout.addWidget(button)
                self._buttons[section] = button

            layout.addStretch()

            # Sélectionner la première section
            if self._buttons:
                first_section = list(self._buttons.keys())[0]
                self._buttons[first_section].setChecked(True)
                self._buttons[first_section]._update_style()

        def _on_section_clicked(self, section: SettingsSection) -> None:
            """Gère le clic sur un bouton de section.

            Args:
                section: Section sélectionnée.
            """
            # Désélectionner tous les boutons
            for button in self._buttons.values():
                button.setChecked(False)
                button._update_style()

            # Sélectionner le bouton cliqué
            self._buttons[section].setChecked(True)
            self._buttons[section]._update_style()

            # Émettre le signal
            self.section_selected.emit(section)

        def update_modified_counts(self, counts: dict[SettingsSection, int]) -> None:
            """Met à jour les compteurs de modifications.

            Args:
                counts: Dictionnaire {section: count}.
            """
            for section, count in counts.items():
                if section in self._buttons:
                    self._buttons[section].set_modified_count(count)

    class BoolSetting(QWidget):
        """Widget pour un paramètre booléen (switch on/off)."""

        # Signaux
        value_changed = pyqtSignal(str, bool)  # key, value

        def __init__(
            self,
            definition: SettingDefinition,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise le widget.

            Args:
                definition: Définition du paramètre.
                parent: Widget parent.
            """
            super().__init__(parent)
            self.definition = definition

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(12)

            # Label et description
            text_layout = QVBoxLayout()
            text_layout.setSpacing(2)

            label = QLabel(definition.label)
            label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 12px; font-weight: bold;")
            text_layout.addWidget(label)

            if definition.description:
                desc = QLabel(definition.description)
                desc.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px;")
                desc.setWordWrap(True)
                text_layout.addWidget(desc)

            text_layout.addStretch()
            layout.addLayout(text_layout)

            # Checkbox
            self._checkbox = QCheckBox()
            self._checkbox.setChecked(bool(definition.current or False))
            self._checkbox.setStyleSheet(f"""
                QCheckBox::indicator {{
                    width: 20px;
                    height: 20px;
                    border: 2px solid {COLOR_BORDER_DIM};
                    border-radius: 3px;
                    background-color: {COLOR_SURFACE};
                }}
                QCheckBox::indicator:checked {{
                    background-color: {COLOR_PRIMARY};
                    border-color: {COLOR_PRIMARY};
                }}
                QCheckBox::indicator:hover {{
                    border-color: {COLOR_SECONDARY};
                }}
            """)
            self._checkbox.stateChanged.connect(self._on_changed)
            layout.addWidget(self._checkbox)

            layout.addStretch()

        def _on_changed(self, state: int) -> None:
            """Gère le changement de valeur.

            Args:
                state: État de la checkbox.
            """
            value = state == Qt.CheckState.Checked.value
            self.definition.current = value
            self.definition.modified = True
            self.value_changed.emit(self.definition.key, value)

    class SelectSetting(QWidget):
        """Widget pour un paramètre avec liste de choix."""

        # Signaux
        value_changed = pyqtSignal(str, object)  # key, value

        def __init__(
            self,
            definition: SettingDefinition,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise le widget.

            Args:
                definition: Définition du paramètre.
                parent: Widget parent.
            """
            super().__init__(parent)
            self.definition = definition

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(12)

            # Label et description
            text_layout = QVBoxLayout()
            text_layout.setSpacing(2)

            label = QLabel(definition.label)
            label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 12px; font-weight: bold;")
            text_layout.addWidget(label)

            if definition.description:
                desc = QLabel(definition.description)
                desc.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px;")
                desc.setWordWrap(True)
                text_layout.addWidget(desc)

            text_layout.addStretch()
            layout.addLayout(text_layout)

            # ComboBox
            self._combo = QComboBox()
            self._combo.setFixedWidth(200)
            self._combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {COLOR_TEXT};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: 3px;
                    padding: 4px 8px;
                    font-size: 11px;
                }}
                QComboBox::drop-down {{
                    border: none;
                    width: 20px;
                }}
                QComboBox QAbstractItemView {{
                    background-color: {COLOR_SURFACE};
                    color: {COLOR_TEXT};
                    border: 1px solid {COLOR_BORDER_DIM};
                    selection-background-color: {COLOR_PRIMARY_DIM};
                    selection-color: {COLOR_BACKGROUND};
                }}
            """)

            if definition.choices:
                for choice in definition.choices:
                    self._combo.addItem(str(choice), choice)

                # Sélectionner la valeur actuelle
                index = self._combo.findData(definition.current)
                if index >= 0:
                    self._combo.setCurrentIndex(index)

            self._combo.currentIndexChanged.connect(self._on_changed)
            layout.addWidget(self._combo)

        def _on_changed(self, index: int) -> None:
            """Gère le changement de sélection.

            Args:
                index: Index sélectionné.
            """
            value = self._combo.itemData(index)
            self.definition.current = value
            self.definition.modified = True
            self.value_changed.emit(self.definition.key, value)

    class TextSetting(QWidget):
        """Widget pour un paramètre texte."""

        # Signaux
        value_changed = pyqtSignal(str, str)  # key, value

        def __init__(
            self,
            definition: SettingDefinition,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise le widget.

            Args:
                definition: Définition du paramètre.
                parent: Widget parent.
            """
            super().__init__(parent)
            self.definition = definition

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(12)

            # Label et description
            text_layout = QVBoxLayout()
            text_layout.setSpacing(2)

            label = QLabel(definition.label)
            label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 12px; font-weight: bold;")
            text_layout.addWidget(label)

            if definition.description:
                desc = QLabel(definition.description)
                desc.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px;")
                desc.setWordWrap(True)
                text_layout.addWidget(desc)

            text_layout.addStretch()
            layout.addLayout(text_layout)

            # Input
            self._input = QLineEdit()
            self._input.setFixedWidth(300)
            self._input.setText(str(definition.current or ""))
            self._input.setPlaceholderText(definition.placeholder)
            self._input.setStyleSheet(f"""
                QLineEdit {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {COLOR_TEXT};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: 3px;
                    padding: 4px 8px;
                    font-family: 'JetBrains Mono', monospace;
                    font-size: 11px;
                }}
                QLineEdit:focus {{
                    border-color: {COLOR_SECONDARY};
                }}
            """)
            self._input.textChanged.connect(self._on_changed)
            layout.addWidget(self._input)

        def _on_changed(self, text: str) -> None:
            """Gère le changement de texte.

            Args:
                text: Nouveau texte.
            """
            self.definition.current = text
            self.definition.modified = True
            self.value_changed.emit(self.definition.key, text)

    class NumberSetting(QWidget):
        """Widget pour un paramètre numérique."""

        # Signaux
        value_changed = pyqtSignal(str, float)  # key, value

        def __init__(
            self,
            definition: SettingDefinition,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise le widget.

            Args:
                definition: Définition du paramètre.
                parent: Widget parent.
            """
            super().__init__(parent)
            self.definition = definition

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(12)

            # Label et description
            text_layout = QVBoxLayout()
            text_layout.setSpacing(2)

            label = QLabel(definition.label)
            label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 12px; font-weight: bold;")
            text_layout.addWidget(label)

            if definition.description:
                desc = QLabel(definition.description)
                desc.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px;")
                desc.setWordWrap(True)
                text_layout.addWidget(desc)

            text_layout.addStretch()
            layout.addLayout(text_layout)

            # SpinBox
            self._spinbox = QSpinBox()
            self._spinbox.setFixedWidth(100)
            if definition.min_value is not None:
                self._spinbox.setMinimum(int(definition.min_value))
            if definition.max_value is not None:
                self._spinbox.setMaximum(int(definition.max_value))
            self._spinbox.setValue(int(definition.current or 0))
            self._spinbox.setStyleSheet(f"""
                QSpinBox {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {COLOR_TEXT};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: 3px;
                    padding: 4px 8px;
                    font-family: 'JetBrains Mono', monospace;
                    font-size: 11px;
                }}
                QSpinBox:focus {{
                    border-color: {COLOR_SECONDARY};
                }}
            """)
            self._spinbox.valueChanged.connect(self._on_changed)
            layout.addWidget(self._spinbox)

        def _on_changed(self, value: int) -> None:
            """Gère le changement de valeur.

            Args:
                value: Nouvelle valeur.
            """
            self.definition.current = float(value)
            self.definition.modified = True
            self.value_changed.emit(self.definition.key, float(value))

    class PathSetting(QWidget):
        """Widget pour un paramètre chemin de fichier."""

        # Signaux
        value_changed = pyqtSignal(str, str)  # key, value

        def __init__(
            self,
            definition: SettingDefinition,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise le widget.

            Args:
                definition: Définition du paramètre.
                parent: Widget parent.
            """
            super().__init__(parent)
            self.definition = definition

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(12)

            # Label et description
            text_layout = QVBoxLayout()
            text_layout.setSpacing(2)

            label = QLabel(definition.label)
            label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 12px; font-weight: bold;")
            text_layout.addWidget(label)

            if definition.description:
                desc = QLabel(definition.description)
                desc.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px;")
                desc.setWordWrap(True)
                text_layout.addWidget(desc)

            text_layout.addStretch()
            layout.addLayout(text_layout)

            # Input + bouton browse
            input_layout = QHBoxLayout()
            input_layout.setSpacing(4)

            self._input = QLineEdit()
            self._input.setFixedWidth(250)
            self._input.setText(str(definition.current or ""))
            self._input.setPlaceholderText(definition.placeholder)
            self._input.setStyleSheet(f"""
                QLineEdit {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {COLOR_TEXT};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: 3px;
                    padding: 4px 8px;
                    font-family: 'JetBrains Mono', monospace;
                    font-size: 11px;
                }}
                QLineEdit:focus {{
                    border-color: {COLOR_SECONDARY};
                }}
            """)
            self._input.textChanged.connect(self._on_changed)
            input_layout.addWidget(self._input)

            btn_browse = QPushButton("📁")
            btn_browse.setFixedSize(28, 28)
            btn_browse.setToolTip(t("settings.browse", default="Browse"))
            btn_browse.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {COLOR_TEXT};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: 3px;
                    font-size: 14px;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_SURFACE_HOVER};
                    border-color: {COLOR_SECONDARY};
                }}
            """)
            btn_browse.clicked.connect(self._on_browse)
            input_layout.addWidget(btn_browse)

            layout.addLayout(input_layout)

        def _on_changed(self, text: str) -> None:
            """Gère le changement de texte.

            Args:
                text: Nouveau texte.
            """
            self.definition.current = text
            self.definition.modified = True
            self.value_changed.emit(self.definition.key, text)

        def _on_browse(self) -> None:
            """Ouvre le dialogue de sélection de fichier."""
            path = QFileDialog.getExistingDirectory(
                self,
                t("settings.select_directory", default="Select Directory"),
                str(self.definition.current or ""),
            )
            if path:
                self._input.setText(path)

    class SettingsContent(QScrollArea):
        """Contenu principal avec les paramètres."""

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise le contenu.

            Args:
                parent: Widget parent.
            """
            super().__init__(parent)

            # Configuration
            self.setWidgetResizable(True)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setStyleSheet(f"""
                QScrollArea {{
                    background-color: {COLOR_BACKGROUND};
                    border: none;
                }}
            """)

            # Widget conteneur
            self._container = QWidget()
            self._container.setStyleSheet(f"background-color: {COLOR_BACKGROUND};")
            self._layout = QVBoxLayout(self._container)
            self._layout.setContentsMargins(16, 16, 16, 16)
            self._layout.setSpacing(8)

            self.setWidget(self._container)

            # Widgets de paramètres
            self._setting_widgets: dict[str, QWidget] = {}

        def set_section(self, section: SettingsSection, definitions: list[SettingDefinition]) -> None:
            """Définit les paramètres pour une section.

            Args:
                section: Section à afficher.
                definitions: Liste de définitions de paramètres.
            """
            # Nettoyer le contenu actuel
            self._clear()

            # Titre de la section
            title = QLabel(f"{section.icon} {section.label}")
            title.setStyleSheet(f"""
                color: {COLOR_PRIMARY};
                font-size: 18px;
                font-weight: bold;
                font-family: 'JetBrains Mono', monospace;
                padding: 8px 0;
            """)
            self._layout.addWidget(title)

            # Séparateur
            separator = QFrame()
            separator.setFrameShape(QFrame.Shape.HLine)
            separator.setStyleSheet(f"color: {COLOR_BORDER_DIM};")
            self._layout.addWidget(separator)

            # Ajouter les paramètres
            for definition in definitions:
                widget = self._create_setting_widget(definition)
                if widget:
                    self._layout.addWidget(widget)
                    self._setting_widgets[definition.key] = widget

            self._layout.addStretch()

        def _clear(self) -> None:
            """Nettoie le contenu."""
            while self._layout.count():
                item = self._layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._setting_widgets.clear()

        def _create_setting_widget(self, definition: SettingDefinition) -> QWidget | None:
            """Crée le widget approprié pour un paramètre.

            Args:
                definition: Définition du paramètre.

            Returns:
                Widget ou None.
            """
            if definition.setting_type == SettingType.BOOL:
                return BoolSetting(definition, parent=self)
            elif definition.setting_type == SettingType.SELECT:
                return SelectSetting(definition, parent=self)
            elif definition.setting_type == SettingType.TEXT:
                return TextSetting(definition, parent=self)
            elif definition.setting_type == SettingType.NUMBER:
                return NumberSetting(definition, parent=self)
            elif definition.setting_type == SettingType.PATH:
                return PathSetting(definition, parent=self)
            return None

    class SettingsView(QWidget):
        """Vue principale des paramètres.

        Combine la sidebar, le contenu, et le footer en un seul widget cohérent.

        Signals:
            settings_saved(): Émis lorsque les paramètres sont sauvegardés.
            settings_reset(): Émis lorsque les paramètres sont réinitialisés.
            setting_changed(str, Any): Émis lorsqu'un paramètre change.
        """

        # Signaux
        settings_saved = pyqtSignal()
        settings_reset = pyqtSignal()
        setting_changed = pyqtSignal(str, object)  # key, value

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la vue.

            Args:
                parent: Widget parent.
            """
            super().__init__(parent)
            self._state = SettingsState()
            self._current_section = SettingsSection.APPLICATION

            # Layout principal
            layout = QHBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            # Sidebar
            self._sidebar = SettingsSidebar(parent=self)
            self._sidebar.section_selected.connect(self._on_section_selected)
            layout.addWidget(self._sidebar)

            # Contenu + Footer
            right_layout = QVBoxLayout()
            right_layout.setContentsMargins(0, 0, 0, 0)
            right_layout.setSpacing(0)

            # Contenu
            self._content = SettingsContent(parent=self)
            right_layout.addWidget(self._content)

            # Footer
            self._footer = self._create_footer()
            right_layout.addWidget(self._footer)

            layout.addLayout(right_layout)

            # Initialiser les paramètres
            self._initialize_settings()
            self._show_section(SettingsSection.APPLICATION)

        def _create_footer(self) -> QFrame:
            """Crée le footer avec les boutons d'action.

            Returns:
                Frame du footer.
            """
            footer = QFrame()
            footer.setFixedHeight(FOOTER_HEIGHT)
            footer.setStyleSheet(f"""
                QFrame {{
                    background-color: {COLOR_SURFACE};
                    border-top: 2px solid {COLOR_BORDER_DIM};
                }}
            """)

            layout = QHBoxLayout(footer)
            layout.setContentsMargins(16, 8, 16, 8)
            layout.setSpacing(8)

            # Bouton Reset
            btn_reset = QPushButton(t("settings.button.reset", default="Reset"))
            btn_reset.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {COLOR_TEXT};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: 3px;
                    padding: 6px 16px;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_SURFACE_HOVER};
                    border-color: {COLOR_WARNING};
                    color: {COLOR_WARNING};
                }}
            """)
            btn_reset.clicked.connect(self._on_reset)
            layout.addWidget(btn_reset)

            layout.addStretch()

            # Bouton Cancel
            btn_cancel = QPushButton(t("settings.button.cancel", default="Cancel"))
            btn_cancel.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {COLOR_TEXT};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: 3px;
                    padding: 6px 16px;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_SURFACE_HOVER};
                    border-color: {COLOR_ERROR};
                    color: {COLOR_ERROR};
                }}
            """)
            btn_cancel.clicked.connect(self._on_cancel)
            layout.addWidget(btn_cancel)

            # Bouton Save
            btn_save = QPushButton(t("settings.button.save", default="Save"))
            btn_save.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_PRIMARY_BG};
                    color: {COLOR_PRIMARY};
                    border: 2px solid {COLOR_PRIMARY};
                    border-radius: 3px;
                    padding: 6px 16px;
                    font-size: 12px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_PRIMARY};
                    color: {COLOR_BACKGROUND};
                }}
            """)
            btn_save.clicked.connect(self._on_save)
            layout.addWidget(btn_save)

            return footer

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

        def _add_setting(self, definition: SettingDefinition) -> None:
            """Ajoute une définition de paramètre.

            Args:
                definition: Définition du paramètre.
            """
            self._state.definitions[definition.key] = definition

        def _show_section(self, section: SettingsSection) -> None:
            """Affiche les paramètres d'une section.

            Args:
                section: Section à afficher.
            """
            self._current_section = section

            # Filtrer les définitions de cette section
            section_definitions = [
                d for d in self._state.definitions.values()
                if d.section == section
            ]

            # Afficher dans le contenu
            self._content.set_section(section, section_definitions)

            # Connecter les signaux des widgets
            for widget in self._content._setting_widgets.values():
                if hasattr(widget, "value_changed"):
                    widget.value_changed.connect(self._on_setting_changed)

        def _on_section_selected(self, section: SettingsSection) -> None:
            """Gère la sélection d'une section.

            Args:
                section: Section sélectionnée.
            """
            self._show_section(section)

        def _on_setting_changed(self, key: str, value: Any) -> None:
            """Gère le changement d'un paramètre.

            Args:
                key: Clé du paramètre.
                value: Nouvelle valeur.
            """
            self._state.mark_modified(key)
            self.setting_changed.emit(key, value)

            # Mettre à jour les compteurs de la sidebar
            counts: dict[SettingsSection, int] = {}
            for definition in self._state.definitions.values():
                if definition.modified:
                    counts[definition.section] = counts.get(definition.section, 0) + 1
            self._sidebar.update_modified_counts(counts)

        def _on_save(self) -> None:
            """Sauvegarde les paramètres."""
            try:
                self._save_settings()
                self.settings_saved.emit()
                logger.info("Paramètres sauvegardés")
            except Exception as e:
                logger.error("Erreur lors de la sauvegarde: {}", e)
                QMessageBox.critical(
                    self,
                    t("settings.error.save_failed", default="Save Failed"),
                    str(e),
                )

        def _on_reset(self) -> None:
            """Réinitialise les paramètres."""
            reply = QMessageBox.question(
                self,
                t("settings.confirm.reset", default="Reset Settings"),
                t("settings.confirm.reset.message", default="Are you sure you want to reset all settings to default values?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )

            if reply == QMessageBox.StandardButton.Yes:
                for definition in self._state.definitions.values():
                    definition.current = definition.default
                    definition.modified = True

                self._show_section(self._current_section)
                self.settings_reset.emit()
                logger.info("Paramètres réinitialisés")

        def _on_cancel(self) -> None:
            """Annule les modifications."""
            if self._state.has_unsaved_changes:
                reply = QMessageBox.question(
                    self,
                    t("settings.confirm.cancel", default="Cancel Changes"),
                    t("settings.confirm.cancel.message", default="You have unsaved changes. Are you sure you want to discard them?"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )

                if reply == QMessageBox.StandardButton.No:
                    return

            # Recharger les paramètres depuis la configuration
            self._state.reset_modified()
            self._initialize_settings()
            self._show_section(self._current_section)

        def _save_settings(self) -> None:
            """Sauvegarde les paramètres dans le fichier de configuration.

            Raises:
                SettingsSaveError: Si la sauvegarde échoue.
                SettingsValidationError: Si un paramètre est invalide.
            """
            # Valider tous les paramètres modifiés
            for definition in self._state.definitions.values():
                if definition.modified:
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
            try:
                event_bus = get_event_bus()
                event_bus.emit(
                    EventType.CONFIG_CHANGED,
                    payload={"path": str(config_path)},
                    source="interfaces.gui.settings",
                )
            except Exception as e:
                logger.debug("Impossible d'émettre l'événement: {}", e)

            # Mettre à jour l'état
            self._state.last_saved_at = datetime.now(UTC)
            self._state.reset_modified()

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
                if not definition.modified:
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
    # Constantes
    "COLOR_PRIMARY",
    "COLOR_SECONDARY",
    "COLOR_ACCENT",
    "COLOR_BACKGROUND",
    "COLOR_SURFACE",
    "COLOR_TEXT",
    "SIDEBAR_WIDTH",
    "SETTING_HEIGHT",
    "FOOTER_HEIGHT",
    # Exceptions
    "SettingsViewError",
    "SettingsValidationError",
    "SettingsSaveError",
    # Enums
    "SettingsSection",
    "SettingType",
    # Modèles
    "SettingDefinition",
    "SettingsState",
    # Widgets
    "SettingsView" if PYQT6_AVAILABLE else None,
    "SettingsSidebar" if PYQT6_AVAILABLE else None,
    "SettingsContent" if PYQT6_AVAILABLE else None,
    "SectionButton" if PYQT6_AVAILABLE else None,
    "BoolSetting" if PYQT6_AVAILABLE else None,
    "SelectSetting" if PYQT6_AVAILABLE else None,
    "TextSetting" if PYQT6_AVAILABLE else None,
    "NumberSetting" if PYQT6_AVAILABLE else None,
    "PathSetting" if PYQT6_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
