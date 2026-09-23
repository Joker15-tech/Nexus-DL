"""Vue de gestion des téléchargements pour l'interface graphique NexusDL.

Ce module fournit une vue PyQt6 complète pour visualiser, contrôler et surveiller
toutes les tâches de téléchargement. Elle offre une expérience utilisateur riche
avec progression en temps réel, filtrage, tri, actions individuelles et globales.

**Fonctionnalités** :
    - Liste des tâches avec progression en temps réel
    - Filtrage par statut (toutes, actives, en attente, terminées, échouées)
    - Tri par date, progression, taille, nom, priorité
    - Actions individuelles (pause, reprise, annulation, retry, suppression)
    - Actions globales (pause all, resume all, clear completed, cancel all)
    - Statistiques globales (vitesse, temps restant, taille totale)
    - Panneau de détails pour une tâche sélectionnée
    - Barre de progression visuelle avec ProgressWidget
    - Mises à jour temps réel via EventBus
    - Signaux Qt pour communication
    - Traductions i18n
    - Style cyberpunk néon cohérent

**Architecture** :
    DownloadView (QWidget principal)
        ├── DownloadToolbar (barre de filtrage)
        │   ├── SearchInput (recherche)
        │   ├── StatusFilter (filtre statut)
        │   └── SortFilter (tri)
        ├── DownloadTasksList (liste des tâches)
        │   └── DownloadTaskItem (item individuel avec ProgressWidget)
        ├── DownloadDetailsPanel (panneau de détails)
        │   ├── TaskInfo (métadonnées)
        │   ├── ProgressBar (barre de progression)
        │   ├── ChaptersList (liste des chapitres)
        │   └── TaskActions (boutons d'action)
        ├── DownloadStatsBar (barre de statistiques)
        │   ├── Vitesse moyenne
        │   ├── Temps restant estimé
        │   └── Taille totale
        └── DownloadActionsBar (barre d'actions globales)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.gui.views.download_view import DownloadView
    >>>
    >>> # Dans une fenêtre PyQt6
    >>> download_view = DownloadView(parent=self)
    >>> download_view.task_action.connect(self.on_task_action)
    >>> layout.addWidget(download_view)

Intégration :
    - core/downloader/manager.py : accès au DownloadManager
    - core/models/download.py : modèles DownloadTask, DownloadStatus
    - core/events.py : abonnement aux événements de progression
    - core/i18n.py : traductions
    - core/utils/text.py : format_size
    - core/utils/time.py : format_duration
    - interfaces/gui/components/progress_widget.py : ProgressWidget
    - interfaces/gui/components/chapter_table.py : ChapterTableWidget
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Final

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
        QBrush,
        QColor,
        QFont,
        QKeyEvent,
        QMouseEvent,
        QPainter,
        QPen,
    )
    from PyQt6.QtWidgets import (
        QComboBox,
        QFrame,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSplitter,
        QVBoxLayout,
        QWidget,
    )
    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False

from nexusdl.core.constants import APP_NAME
from nexusdl.core.events import EventType, get_event_bus
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

# Dimensions
TOOLBAR_HEIGHT: Final[int] = 50
TASK_ITEM_HEIGHT: Final[int] = 100
DETAILS_PANEL_WIDTH: Final[int] = 450
STATS_BAR_HEIGHT: Final[int] = 28
ACTIONS_BAR_HEIGHT: Final[int] = 50


# ============================================================================
# EXCEPTIONS
# ============================================================================


class DownloadViewError(NexusDLError):
    """Exception de base pour les erreurs de la vue de téléchargement."""


class DownloadManagerNotAvailableError(DownloadViewError):
    """Exception levée lorsque le DownloadManager n'est pas disponible."""

    def __init__(self) -> None:
        super().__init__(
            "DownloadManager n'est pas disponible. "
            "Assurez-vous que l'application est correctement initialisée."
        )


class TaskActionError(DownloadViewError):
    """Exception levée lorsqu'une action sur une tâche échoue.

    Attributes:
        task_id: ID de la tâche.
        action: Action tentée.
        reason: Raison de l'échec.
    """

    def __init__(self, task_id: str, action: str, reason: str = "") -> None:
        msg = f"Échec de l'action '{action}' sur la tâche {task_id}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.task_id = task_id
        self.action = action
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class DownloadFilter(str, Enum):
    """Filtre de statut des tâches.

    Attributes:
        ALL: Toutes les tâches.
        ACTIVE: Tâches en cours.
        PENDING: Tâches en attente.
        COMPLETED: Tâches terminées.
        FAILED: Tâches échouées.
        CANCELLED: Tâches annulées.
        PAUSED: Tâches en pause.
    """

    ALL = "all"
    ACTIVE = "active"
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            DownloadFilter.ALL: t("download.filter.all", default="All"),
            DownloadFilter.ACTIVE: t("download.filter.active", default="Active"),
            DownloadFilter.PENDING: t("download.filter.pending", default="Pending"),
            DownloadFilter.COMPLETED: t("download.filter.completed", default="Completed"),
            DownloadFilter.FAILED: t("download.filter.failed", default="Failed"),
            DownloadFilter.CANCELLED: t("download.filter.cancelled", default="Cancelled"),
            DownloadFilter.PAUSED: t("download.filter.paused", default="Paused"),
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            DownloadFilter.ALL: "📋",
            DownloadFilter.ACTIVE: "⚡",
            DownloadFilter.PENDING: "⏳",
            DownloadFilter.COMPLETED: "✅",
            DownloadFilter.FAILED: "❌",
            DownloadFilter.CANCELLED: "🚫",
            DownloadFilter.PAUSED: "⏸️",
        }[self]


class DownloadSortBy(str, Enum):
    """Critère de tri des tâches.

    Attributes:
        DATE_ADDED: Tri par date d'ajout.
        PROGRESS: Tri par progression.
        SIZE: Tri par taille.
        NAME: Tri par nom.
        PRIORITY: Tri par priorité.
        STATUS: Tri par statut.
    """

    DATE_ADDED = "date_added"
    PROGRESS = "progress"
    SIZE = "size"
    NAME = "name"
    PRIORITY = "priority"
    STATUS = "status"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            DownloadSortBy.DATE_ADDED: t("download.sort.date_added", default="Date Added"),
            DownloadSortBy.PROGRESS: t("download.sort.progress", default="Progress"),
            DownloadSortBy.SIZE: t("download.sort.size", default="Size"),
            DownloadSortBy.NAME: t("download.sort.name", default="Name"),
            DownloadSortBy.PRIORITY: t("download.sort.priority", default="Priority"),
            DownloadSortBy.STATUS: t("download.sort.status", default="Status"),
        }[self]


# ============================================================================
# MODÈLES DE DONNÉES
# ============================================================================


class DownloadFilters:
    """Filtres appliqués aux tâches de téléchargement.

    Attributes:
        status_filter: Filtre par statut.
        sort_by: Critère de tri.
        search_query: Texte de recherche.
        reverse: Si True, tri inversé.
    """

    def __init__(
        self,
        *,
        status_filter: DownloadFilter = DownloadFilter.ALL,
        sort_by: DownloadSortBy = DownloadSortBy.DATE_ADDED,
        search_query: str = "",
        reverse: bool = False,
    ) -> None:
        """Initialise les filtres."""
        self.status_filter = status_filter
        self.sort_by = sort_by
        self.search_query = search_query
        self.reverse = reverse


class DownloadStats:
    """Statistiques globales des téléchargements.

    Attributes:
        total_tasks: Nombre total de tâches.
        active_tasks: Nombre de tâches actives.
        pending_tasks: Nombre de tâches en attente.
        completed_tasks: Nombre de tâches terminées.
        failed_tasks: Nombre de tâches échouées.
        total_size_bytes: Taille totale en bytes.
        downloaded_bytes: Taille téléchargée en bytes.
        average_speed_bytes_per_sec: Vitesse moyenne en bytes/seconde.
        estimated_time_remaining_seconds: Temps restant estimé en secondes.
    """

    def __init__(
        self,
        *,
        total_tasks: int = 0,
        active_tasks: int = 0,
        pending_tasks: int = 0,
        completed_tasks: int = 0,
        failed_tasks: int = 0,
        total_size_bytes: int = 0,
        downloaded_bytes: int = 0,
        average_speed_bytes_per_sec: float = 0.0,
        estimated_time_remaining_seconds: float = 0.0,
    ) -> None:
        """Initialise les statistiques."""
        self.total_tasks = total_tasks
        self.active_tasks = active_tasks
        self.pending_tasks = pending_tasks
        self.completed_tasks = completed_tasks
        self.failed_tasks = failed_tasks
        self.total_size_bytes = total_size_bytes
        self.downloaded_bytes = downloaded_bytes
        self.average_speed_bytes_per_sec = average_speed_bytes_per_sec
        self.estimated_time_remaining_seconds = estimated_time_remaining_seconds

    @property
    def total_size_human(self) -> str:
        """Taille totale formatée."""
        from nexusdl.core.utils.text import format_size
        return format_size(self.total_size_bytes)

    @property
    def downloaded_size_human(self) -> str:
        """Taille téléchargée formatée."""
        from nexusdl.core.utils.text import format_size
        return format_size(self.downloaded_bytes)

    @property
    def average_speed_human(self) -> str:
        """Vitesse moyenne formatée."""
        from nexusdl.core.utils.text import format_size
        return f"{format_size(int(self.average_speed_bytes_per_sec))}/s"

    @property
    def estimated_time_human(self) -> str:
        """Temps restant estimé formaté."""
        from nexusdl.core.utils.time import format_duration
        return format_duration(timedelta(seconds=self.estimated_time_remaining_seconds))

    @property
    def overall_progress(self) -> float:
        """Progression globale (0.0 à 1.0)."""
        if self.total_size_bytes == 0:
            return 0.0
        return self.downloaded_bytes / self.total_size_bytes


class DownloadState:
    """État de la vue de téléchargement.

    Attributes:
        all_tasks: Liste de toutes les tâches.
        filtered_tasks: Liste des tâches après filtrage.
        selected_task_id: ID de la tâche sélectionnée.
        filters: Filtres actifs.
        stats: Statistiques globales.
        loading: Indique si en cours de chargement.
        error: Message d'erreur.
    """

    def __init__(self) -> None:
        """Initialise l'état."""
        self.all_tasks: list[Any] = []
        self.filtered_tasks: list[Any] = []
        self.selected_task_id: str | None = None
        self.filters = DownloadFilters()
        self.stats = DownloadStats()
        self.loading = False
        self.error: str | None = None

    @property
    def selected_task(self) -> Any | None:
        """Tâche sélectionnée."""
        if self.selected_task_id is None:
            return None
        for task in self.all_tasks:
            if task.id == self.selected_task_id:
                return task
        return None

    @property
    def filtered_count(self) -> int:
        """Nombre de tâches après filtrage."""
        return len(self.filtered_tasks)

    @property
    def total_count(self) -> int:
        """Nombre total de tâches."""
        return len(self.all_tasks)


# ============================================================================
# WIDGETS — Composants PyQt6
# ============================================================================


if PYQT6_AVAILABLE:

    class DownloadToolbar(QFrame):
        """Barre de filtrage des tâches."""

        # Signaux
        filters_changed = pyqtSignal(object)  # DownloadFilters

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la barre de filtrage."""
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedHeight(TOOLBAR_HEIGHT)
            self.setStyleSheet(f"""
                DownloadToolbar {{
                    background-color: {COLOR_SURFACE};
                    border-bottom: 2px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(16, 8, 16, 8)
            layout.setSpacing(12)

            # Champ de recherche
            self._search_input = QLineEdit()
            self._search_input.setPlaceholderText(
                t("download.search.placeholder", default="🔍 Search tasks...")
            )
            self._search_input.setFixedWidth(250)
            self._search_input.setStyleSheet(self._input_style())
            self._search_input.textChanged.connect(self._on_filters_changed)
            layout.addWidget(self._search_input)

            # Filtre de statut
            layout.addWidget(self._create_label(t("download.filter.status", default="Status:")))
            self._status_filter = QComboBox()
            self._status_filter.setFixedWidth(160)
            self._status_filter.setStyleSheet(self._combo_style())
            for filter_type in DownloadFilter:
                self._status_filter.addItem(f"{filter_type.icon} {filter_type.label}", filter_type.value)
            self._status_filter.currentIndexChanged.connect(self._on_filters_changed)
            layout.addWidget(self._status_filter)

            # Tri
            layout.addWidget(self._create_label(t("download.filter.sort", default="Sort:")))
            self._sort_filter = QComboBox()
            self._sort_filter.setFixedWidth(140)
            self._sort_filter.setStyleSheet(self._combo_style())
            for sort_by in DownloadSortBy:
                self._sort_filter.addItem(sort_by.label, sort_by.value)
            self._sort_filter.currentIndexChanged.connect(self._on_filters_changed)
            layout.addWidget(self._sort_filter)

            layout.addStretch()

        def _create_label(self, text: str) -> QLabel:
            """Crée un label de filtre."""
            label = QLabel(text)
            label.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 11px;
            """)
            return label

        def _input_style(self) -> str:
            """Retourne le style pour les inputs."""
            return f"""
                QLineEdit {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {COLOR_TEXT};
                    border: 2px solid {COLOR_BORDER_DIM};
                    border-radius: 4px;
                    padding: 6px 12px;
                    font-family: 'JetBrains Mono', monospace;
                    font-size: 12px;
                }}
                QLineEdit:focus {{
                    border-color: {COLOR_SECONDARY};
                }}
                QLineEdit::placeholder {{
                    color: {COLOR_TEXT_DIM};
                }}
            """

        def _combo_style(self) -> str:
            """Retourne le style pour les ComboBox."""
            return f"""
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
                    selection-background-color: {COLOR_PRIMARY_BG};
                    selection-color: {COLOR_PRIMARY};
                }}
            """

        def _on_filters_changed(self) -> None:
            """Gère le changement de filtres."""
            self.filters_changed.emit(self.get_filters())

        def get_filters(self) -> DownloadFilters:
            """Récupère les filtres actuels.

            Returns:
                Instance de DownloadFilters.
            """
            return DownloadFilters(
                status_filter=DownloadFilter(self._status_filter.currentData()),
                sort_by=DownloadSortBy(self._sort_filter.currentData()),
                search_query=self._search_input.text().strip(),
            )

    class DownloadTaskItem(QFrame):
        """Item individuel dans la liste des tâches."""

        # Signaux
        clicked = pyqtSignal(str)  # task_id
        action_requested = pyqtSignal(str, str)  # task_id, action

        def __init__(
            self,
            task: Any,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise l'item.

            Args:
                task: Tâche de téléchargement.
                parent: Widget parent.
            """
            super().__init__(parent)
            self.task = task

            # Configuration visuelle
            self.setFixedHeight(TASK_ITEM_HEIGHT)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setStyleSheet(f"""
                DownloadTaskItem {{
                    background-color: {COLOR_SURFACE};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: 4px;
                }}
                DownloadTaskItem:hover {{
                    background-color: {COLOR_SURFACE_HOVER};
                    border-color: {COLOR_SECONDARY};
                }}
            """)

            # Layout principal
            layout = QHBoxLayout(self)
            layout.setContentsMargins(12, 8, 12, 8)
            layout.setSpacing(12)

            # Informations de la tâche
            info_layout = QVBoxLayout()
            info_layout.setSpacing(4)

            # Titre
            title = task.manga.title if hasattr(task, "manga") else "Unknown"
            title_label = QLabel(title)
            title_label.setStyleSheet(f"""
                color: {COLOR_TEXT};
                font-size: 13px;
                font-weight: bold;
            """)
            info_layout.addWidget(title_label)

            # Métadonnées
            meta_parts = []
            if hasattr(task, "status"):
                status_icon = "⚡" if task.status.value == "running" else "⏳"
                meta_parts.append(f"{status_icon} {task.status.label}")
            if hasattr(task, "pages_completed") and hasattr(task, "pages_total"):
                meta_parts.append(f"{task.pages_completed}/{task.pages_total} pages")
            if hasattr(task, "total_size_bytes"):
                from nexusdl.core.utils.text import format_size
                meta_parts.append(format_size(task.total_size_bytes))

            meta_label = QLabel(" • ".join(meta_parts))
            meta_label.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 11px;
            """)
            info_layout.addWidget(meta_label)

            # Barre de progression
            from nexusdl.interfaces.gui.components.progress_widget import (
                ProgressWidget,
                ProgressStyle,
            )
            self._progress_widget = ProgressWidget(
                style=ProgressStyle.GRADIENT,
                parent=self,
            )
            self._progress_widget.setFixedHeight(20)

            # Mettre à jour la progression
            if hasattr(task, "pages_completed") and hasattr(task, "pages_total"):
                self._progress_widget.set_progress(task.pages_completed, task.pages_total)

            info_layout.addWidget(self._progress_widget)

            layout.addLayout(info_layout, stretch=1)

            # Boutons d'action
            actions_layout = QVBoxLayout()
            actions_layout.setSpacing(4)

            # Bouton Pause/Resume
            if hasattr(task, "status") and task.status.value in ("running", "downloading"):
                btn_pause = QPushButton("⏸️")
                btn_pause.setFixedSize(28, 28)
                btn_pause.setToolTip(t("download.action.pause", default="Pause"))
                self._style_action_button(btn_pause, COLOR_WARNING)
                btn_pause.clicked.connect(lambda: self.action_requested.emit(task.id, "pause"))
                actions_layout.addWidget(btn_pause)
            elif hasattr(task, "status") and task.status.value == "paused":
                btn_resume = QPushButton("▶️")
                btn_resume.setFixedSize(28, 28)
                btn_resume.setToolTip(t("download.action.resume", default="Resume"))
                self._style_action_button(btn_resume, COLOR_SUCCESS)
                btn_resume.clicked.connect(lambda: self.action_requested.emit(task.id, "resume"))
                actions_layout.addWidget(btn_resume)

            # Bouton Cancel
            btn_cancel = QPushButton("❌")
            btn_cancel.setFixedSize(28, 28)
            btn_cancel.setToolTip(t("download.action.cancel", default="Cancel"))
            self._style_action_button(btn_cancel, COLOR_ERROR)
            btn_cancel.clicked.connect(lambda: self.action_requested.emit(task.id, "cancel"))
            actions_layout.addWidget(btn_cancel)

            layout.addLayout(actions_layout)

        def _style_action_button(self, button: QPushButton, color: str) -> None:
            """Applique le style à un bouton d'action."""
            button.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    border: none;
                    font-size: 16px;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_SURFACE_HOVER};
                    border-radius: 3px;
                }}
            """)

        def update_task(self, task: Any) -> None:
            """Met à jour la tâche.

            Args:
                task: Nouvelle tâche.
            """
            self.task = task
            # TODO: Mettre à jour les widgets enfants

        def set_selected(self, selected: bool) -> None:
            """Définit l'état de sélection.

            Args:
                selected: True si sélectionné.
            """
            if selected:
                self.setStyleSheet(f"""
                    DownloadTaskItem {{
                        background-color: {COLOR_PRIMARY_BG};
                        border: 2px solid {COLOR_PRIMARY};
                        border-radius: 4px;
                    }}
                """)
            else:
                self.setStyleSheet(f"""
                    DownloadTaskItem {{
                        background-color: {COLOR_SURFACE};
                        border: 1px solid {COLOR_BORDER_DIM};
                        border-radius: 4px;
                    }}
                    DownloadTaskItem:hover {{
                        background-color: {COLOR_SURFACE_HOVER};
                        border-color: {COLOR_SECONDARY};
                    }}
                """)

    class DownloadTasksList(QScrollArea):
        """Liste scrollable des tâches de téléchargement."""

        # Signaux
        task_selected = pyqtSignal(str)  # task_id
        task_action = pyqtSignal(str, str)  # task_id, action

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la liste."""
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

            # Items
            self._items: dict[str, DownloadTaskItem] = {}
            self._selected_id: str | None = None

            # Message vide
            self._empty_label = QLabel(t("download.empty", default="No downloads"))
            self._empty_label.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 16px;
                padding: 40px;
            """)
            self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._empty_label.setVisible(False)
            self._layout.addWidget(self._empty_label)

        def set_tasks(self, tasks: list[Any]) -> None:
            """Définit les tâches à afficher.

            Args:
                tasks: Liste de tâches.
            """
            self._clear()

            if not tasks:
                self._empty_label.setVisible(True)
                return

            self._empty_label.setVisible(False)

            for task in tasks:
                item = DownloadTaskItem(task, parent=self._container)
                item.clicked.connect(self._on_item_clicked)
                item.action_requested.connect(self.task_action.emit)
                self._layout.addWidget(item)
                self._items[task.id] = item

            self._layout.addStretch()

        def set_selected(self, task_id: str | None) -> None:
            """Définit la tâche sélectionnée.

            Args:
                task_id: ID de la tâche ou None.
            """
            # Désélectionner l'ancien
            if self._selected_id and self._selected_id in self._items:
                self._items[self._selected_id].set_selected(False)

            # Sélectionner le nouveau
            self._selected_id = task_id
            if task_id and task_id in self._items:
                self._items[task_id].set_selected(True)
                self.task_selected.emit(task_id)

        def _clear(self) -> None:
            """Nettoie la liste."""
            while self._layout.count():
                item = self._layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._items.clear()
            self._selected_id = None

        def _on_item_clicked(self, task_id: str) -> None:
            """Gère le clic sur un item.

            Args:
                task_id: ID de la tâche.
            """
            self.set_selected(task_id)

    class DownloadDetailsPanel(QFrame):
        """Panneau de détails d'une tâche."""

        # Signaux
        task_action = pyqtSignal(str, str)  # task_id, action

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise le panneau."""
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedWidth(DETAILS_PANEL_WIDTH)
            self.setStyleSheet(f"""
                DownloadDetailsPanel {{
                    background-color: {COLOR_SURFACE};
                    border-left: 2px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout principal
            layout = QVBoxLayout(self)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(12)

            # Message vide
            self._empty_label = QLabel(t("download.details.empty", default="Select a task to view details"))
            self._empty_label.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 14px;
                padding: 40px;
            """)
            self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self._empty_label)

            # Contenu (caché par défaut)
            self._content = QWidget()
            self._content.setVisible(False)
            content_layout = QVBoxLayout(self._content)
            content_layout.setContentsMargins(0, 0, 0, 0)
            content_layout.setSpacing(8)

            # Titre
            self._title_label = QLabel("")
            self._title_label.setStyleSheet(f"""
                color: {COLOR_PRIMARY};
                font-size: 16px;
                font-weight: bold;
                font-family: 'JetBrains Mono', monospace;
            """)
            self._title_label.setWordWrap(True)
            content_layout.addWidget(self._title_label)

            # Informations
            self._info_label = QLabel("")
            self._info_label.setStyleSheet(f"""
                color: {COLOR_TEXT_MUTED};
                font-size: 11px;
            """)
            self._info_label.setWordWrap(True)
            content_layout.addWidget(self._info_label)

            # Barre de progression
            from nexusdl.interfaces.gui.components.progress_widget import (
                ProgressWidget,
                ProgressStyle,
                DisplayMode,
            )
            self._progress_widget = ProgressWidget(
                style=ProgressStyle.GRADIENT,
                mode=DisplayMode.DETAILED,
                parent=self._content,
            )
            content_layout.addWidget(self._progress_widget)

            # Séparateur
            separator = QFrame()
            separator.setFrameShape(QFrame.Shape.HLine)
            separator.setStyleSheet(f"color: {COLOR_BORDER_DIM};")
            content_layout.addWidget(separator)

            # Titre des chapitres
            chapters_title = QLabel(t("download.details.chapters", default="Chapters"))
            chapters_title.setStyleSheet(f"""
                color: {COLOR_PRIMARY};
                font-size: 14px;
                font-weight: bold;
                font-family: 'JetBrains Mono', monospace;
            """)
            content_layout.addWidget(chapters_title)

            # Liste des chapitres
            self._chapters_list = QListWidget()
            self._chapters_list.setStyleSheet(f"""
                QListWidget {{
                    background-color: {COLOR_SURFACE_ALT};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: 4px;
                    color: {COLOR_TEXT};
                    font-family: 'JetBrains Mono', monospace;
                    font-size: 11px;
                }}
                QListWidget::item {{
                    padding: 4px 8px;
                }}
                QListWidget::item:selected {{
                    background-color: {COLOR_PRIMARY_BG};
                    color: {COLOR_PRIMARY};
                }}
            """)
            content_layout.addWidget(self._chapters_list, stretch=1)

            # Boutons d'action
            actions_layout = QHBoxLayout()
            actions_layout.setSpacing(8)

            self._btn_pause = QPushButton(f"⏸️ {t('download.details.pause', default='Pause')}")
            self._style_action_button(self._btn_pause, COLOR_WARNING)
            self._btn_pause.clicked.connect(lambda: self._on_action("pause"))
            actions_layout.addWidget(self._btn_pause)

            self._btn_resume = QPushButton(f"▶️ {t('download.details.resume', default='Resume')}")
            self._style_action_button(self._btn_resume, COLOR_SUCCESS)
            self._btn_resume.clicked.connect(lambda: self._on_action("resume"))
            actions_layout.addWidget(self._btn_resume)

            self._btn_cancel = QPushButton(f"❌ {t('download.details.cancel', default='Cancel')}")
            self._style_action_button(self._btn_cancel, COLOR_ERROR)
            self._btn_cancel.clicked.connect(lambda: self._on_action("cancel"))
            actions_layout.addWidget(self._btn_cancel)

            content_layout.addLayout(actions_layout)

            layout.addWidget(self._content, stretch=1)

            # État
            self._current_task: Any = None

        def _style_action_button(self, button: QPushButton, color: str) -> None:
            """Applique le style à un bouton d'action."""
            button.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {color};
                    border: 2px solid {color};
                    border-radius: 4px;
                    padding: 6px 12px;
                    font-size: 11px;
                    font-weight: bold;
                    font-family: 'JetBrains Mono', monospace;
                }}
                QPushButton:hover {{
                    background-color: {color};
                    color: {COLOR_BACKGROUND};
                }}
            """)

        def set_task(self, task: Any | None) -> None:
            """Définit la tâche à afficher.

            Args:
                task: Tâche à afficher ou None.
            """
            self._current_task = task

            if task is None:
                self._empty_label.setVisible(True)
                self._content.setVisible(False)
                return

            self._empty_label.setVisible(False)
            self._content.setVisible(True)

            # Titre
            self._title_label.setText(task.manga.title if hasattr(task, "manga") else "Unknown")

            # Informations
            info_parts = []
            if hasattr(task, "status"):
                info_parts.append(f"Status: {task.status.label}")
            if hasattr(task, "pages_completed") and hasattr(task, "pages_total"):
                info_parts.append(f"Pages: {task.pages_completed}/{task.pages_total}")
            if hasattr(task, "total_size_bytes"):
                from nexusdl.core.utils.text import format_size
                info_parts.append(f"Size: {format_size(task.total_size_bytes)}")
            if hasattr(task, "priority"):
                info_parts.append(f"Priority: {task.priority.label}")
            if hasattr(task, "format"):
                info_parts.append(f"Format: {task.format.value.upper()}")

            self._info_label.setText("\n".join(info_parts))

            # Progression
            if hasattr(task, "pages_completed") and hasattr(task, "pages_total"):
                self._progress_widget.set_progress(task.pages_completed, task.pages_total)

            # Chapitres
            self._chapters_list.clear()
            if hasattr(task, "chapters") and task.chapters:
                for chapter in task.chapters[:20]:  # Limiter à 20 chapitres
                    status_icon = "✅" if chapter.downloaded else "⏳"
                    self._chapters_list.addItem(f"{status_icon} Ch. {chapter.number}: {chapter.title}")

        def _on_action(self, action: str) -> None:
            """Gère une action sur la tâche.

            Args:
                action: Action à effectuer.
            """
            if self._current_task:
                self.task_action.emit(self._current_task.id, action)

    class DownloadStatsBar(QFrame):
        """Barre de statistiques globales."""

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la barre de statistiques."""
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedHeight(STATS_BAR_HEIGHT)
            self.setStyleSheet(f"""
                DownloadStatsBar {{
                    background-color: {COLOR_SURFACE};
                    border-top: 1px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(16, 0, 16, 0)
            layout.setSpacing(16)

            # Statistiques
            self._stats_label = QLabel("")
            self._stats_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
            layout.addWidget(self._stats_label)

            self._speed_label = QLabel("")
            self._speed_label.setStyleSheet(f"color: {COLOR_SECONDARY}; font-size: 11px;")
            layout.addWidget(self._speed_label)

            self._eta_label = QLabel("")
            self._eta_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
            layout.addWidget(self._eta_label)

            layout.addStretch()

            self._progress_label = QLabel("")
            self._progress_label.setStyleSheet(f"""
                color: {COLOR_PRIMARY};
                font-size: 11px;
                font-weight: bold;
            """)
            layout.addWidget(self._progress_label)

        def update_stats(self, stats: DownloadStats, filtered_count: int, total_count: int) -> None:
            """Met à jour les statistiques affichées.

            Args:
                stats: Statistiques.
                filtered_count: Nombre de tâches affichées.
                total_count: Nombre total de tâches.
            """
            self._stats_label.setText(
                t(
                    "download.stats.tasks",
                    default="{filtered}/{total} tasks",
                    filtered=filtered_count,
                    total=total_count,
                )
            )

            if stats.active_tasks > 0:
                self._speed_label.setText(f"⚡ {stats.average_speed_human}")
                self._eta_label.setText(f"⏱️ {stats.estimated_time_human}")
            else:
                self._speed_label.setText("")
                self._eta_label.setText("")

            self._progress_label.setText(f"📊 {stats.overall_progress:.1%}")

    class DownloadActionsBar(QFrame):
        """Barre d'actions globales."""

        # Signaux
        action_triggered = pyqtSignal(str)  # action

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la barre d'actions."""
            super().__init__(parent)

            # Configuration visuelle
            self.setFixedHeight(ACTIONS_BAR_HEIGHT)
            self.setStyleSheet(f"""
                DownloadActionsBar {{
                    background-color: {COLOR_SURFACE};
                    border-top: 2px solid {COLOR_BORDER_DIM};
                }}
            """)

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(16, 8, 16, 8)
            layout.setSpacing(8)

            layout.addStretch()

            # Bouton Pause All
            btn_pause_all = QPushButton(f"⏸️ {t('download.actions.pause_all', default='Pause All')}")
            self._style_action_button(btn_pause_all, COLOR_WARNING)
            btn_pause_all.clicked.connect(lambda: self.action_triggered.emit("pause_all"))
            layout.addWidget(btn_pause_all)

            # Bouton Resume All
            btn_resume_all = QPushButton(f"▶️ {t('download.actions.resume_all', default='Resume All')}")
            self._style_action_button(btn_resume_all, COLOR_SUCCESS)
            btn_resume_all.clicked.connect(lambda: self.action_triggered.emit("resume_all"))
            layout.addWidget(btn_resume_all)

            # Bouton Clear Completed
            btn_clear = QPushButton(f"🗑️ {t('download.actions.clear_completed', default='Clear Completed')}")
            self._style_action_button(btn_clear, COLOR_TEXT_MUTED)
            btn_clear.clicked.connect(lambda: self.action_triggered.emit("clear_completed"))
            layout.addWidget(btn_clear)

            # Bouton Cancel All
            btn_cancel_all = QPushButton(f"❌ {t('download.actions.cancel_all', default='Cancel All')}")
            self._style_action_button(btn_cancel_all, COLOR_ERROR)
            btn_cancel_all.clicked.connect(lambda: self.action_triggered.emit("cancel_all"))
            layout.addWidget(btn_cancel_all)

        def _style_action_button(self, button: QPushButton, color: str) -> None:
            """Applique le style à un bouton d'action."""
            button.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_SURFACE_ALT};
                    color: {color};
                    border: 2px solid {color};
                    border-radius: 4px;
                    padding: 6px 16px;
                    font-size: 12px;
                    font-weight: bold;
                    font-family: 'JetBrains Mono', monospace;
                }}
                QPushButton:hover {{
                    background-color: {color};
                    color: {COLOR_BACKGROUND};
                }}
            """)

    class DownloadView(QWidget):
        """Vue principale de gestion des téléchargements.

        Combine la toolbar, la liste des tâches, le panneau de détails,
        la barre de statistiques, et la barre d'actions en un seul widget cohérent.

        Signals:
            task_action(str, str): Émis lorsqu'une action est effectuée sur une tâche.
            task_selected(str): Émis lorsqu'une tâche est sélectionnée.
        """

        # Signaux
        task_action = pyqtSignal(str, str)  # task_id, action
        task_selected = pyqtSignal(str)  # task_id

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la vue."""
            super().__init__(parent)
            self._state = DownloadState()
            self._event_bus_subscription = None

            # Layout principal
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            # Toolbar
            self._toolbar = DownloadToolbar(parent=self)
            self._toolbar.filters_changed.connect(self._on_filters_changed)
            layout.addWidget(self._toolbar)

            # Splitter (tasks list + details)
            self._splitter = QSplitter(Qt.Orientation.Horizontal)
            self._splitter.setHandleWidth(2)
            self._splitter.setStyleSheet(f"""
                QSplitter::handle {{
                    background-color: {COLOR_BORDER_DIM};
                }}
                QSplitter::handle:hover {{
                    background-color: {COLOR_SECONDARY};
                }}
            """)

            # Liste des tâches
            self._tasks_list = DownloadTasksList(parent=self)
            self._tasks_list.task_selected.connect(self._on_task_selected)
            self._tasks_list.task_action.connect(self._on_task_action)
            self._splitter.addWidget(self._tasks_list)

            # Panneau de détails
            self._details_panel = DownloadDetailsPanel(parent=self)
            self._details_panel.task_action.connect(self._on_task_action)
            self._splitter.addWidget(self._details_panel)

            # Tailles initiales
            self._splitter.setSizes([800, DETAILS_PANEL_WIDTH])

            layout.addWidget(self._splitter, stretch=1)

            # Stats bar
            self._stats_bar = DownloadStatsBar(parent=self)
            layout.addWidget(self._stats_bar)

            # Actions bar
            self._actions_bar = DownloadActionsBar(parent=self)
            self._actions_bar.action_triggered.connect(self._on_global_action)
            layout.addWidget(self._actions_bar)

        def showEvent(self, event: Any) -> None:
            """Gère l'affichage de la vue."""
            super().showEvent(event)
            # Charger les tâches
            asyncio.create_task(self._load_tasks())

            # S'abonner aux événements EventBus
            self._subscribe_to_events()

        def hideEvent(self, event: Any) -> None:
            """Gère la dissimulation de la vue."""
            super().hideEvent(event)
            # Se désabonner des événements
            self._unsubscribe_from_events()

        def _subscribe_to_events(self) -> None:
            """S'abonne aux événements EventBus pour mises à jour temps réel."""
            try:
                event_bus = get_event_bus()

                # S'abonner aux événements de progression
                event_bus.on(
                    EventType.DOWNLOAD_TASK_PROGRESS,
                    self._on_task_progress,
                )
                event_bus.on(
                    EventType.DOWNLOAD_TASK_COMPLETED,
                    self._on_task_completed,
                )
                event_bus.on(
                    EventType.DOWNLOAD_TASK_FAILED,
                    self._on_task_failed,
                )

                logger.debug("Abonné aux événements de téléchargement")

            except Exception as e:
                logger.warning("Impossible de s'abonner aux événements: {}", e)

        def _unsubscribe_from_events(self) -> None:
            """Se désabonne des événements EventBus."""
            # TODO: Implémenter la désinscription propre
            pass

        async def _on_task_progress(self, event: Any) -> None:
            """Gère l'événement de progression d'une tâche."""
            task_id = event.payload.get("task_id")
            if task_id:
                # TODO: Mettre à jour la tâche dans la liste
                pass

        async def _on_task_completed(self, event: Any) -> None:
            """Gère l'événement de tâche terminée."""
            logger.debug("Tâche terminée, rafraîchissement")
            await self._load_tasks()

        async def _on_task_failed(self, event: Any) -> None:
            """Gère l'événement de tâche échouée."""
            logger.debug("Tâche échouée, rafraîchissement")
            await self._load_tasks()

        # =====================================================================
        # CHARGEMENT DES TÂCHES
        # =====================================================================

        async def _load_tasks(self) -> None:
            """Charge les tâches de téléchargement."""
            self._state.loading = True

            try:
                # TODO: Obtenir le DownloadManager
                # from nexusdl.core.downloader import get_download_manager
                # manager = get_download_manager()
                # self._state.all_tasks = await manager.get_all_tasks()

                # Pour l'instant, utiliser une liste vide
                self._state.all_tasks = []

                # Appliquer les filtres
                await self._apply_filters()

                self._state.loading = False
                self._state.error = None

                logger.info("Tâches chargées: {} tâches", len(self._state.all_tasks))

            except Exception as e:
                logger.error("Erreur lors du chargement des tâches: {}", e)
                self._state.loading = False
                self._state.error = str(e)

        # =====================================================================
        # FILTRAGE ET TRI
        # =====================================================================

        async def _apply_filters(self) -> None:
            """Applique les filtres et met à jour l'affichage."""
            filters = self._state.filters

            # Commencer avec toutes les tâches
            filtered = list(self._state.all_tasks)

            # Filtrer par statut
            if filters.status_filter != DownloadFilter.ALL:
                # TODO: Implémenter le filtrage par statut
                pass

            # Recherche texte
            if filters.search_query:
                query_lower = filters.search_query.lower()
                filtered = [
                    t for t in filtered
                    if query_lower in (t.manga.title if hasattr(t, "manga") else "").lower()
                ]

            # Tri
            if filters.sort_by == DownloadSortBy.DATE_ADDED:
                filtered.sort(key=lambda t: getattr(t, "created_at", datetime.min.replace(tzinfo=UTC)), reverse=not filters.reverse)
            elif filters.sort_by == DownloadSortBy.PROGRESS:
                filtered.sort(key=lambda t: getattr(t, "progress", 0.0), reverse=not filters.reverse)
            elif filters.sort_by == DownloadSortBy.SIZE:
                filtered.sort(key=lambda t: getattr(t, "total_size_bytes", 0), reverse=not filters.reverse)
            elif filters.sort_by == DownloadSortBy.NAME:
                filtered.sort(key=lambda t: (t.manga.title if hasattr(t, "manga") else "").lower(), reverse=not filters.reverse)

            self._state.filtered_tasks = filtered

            # Mettre à jour l'UI
            self._tasks_list.set_tasks(filtered)
            self._stats_bar.update_stats(
                self._state.stats,
                filtered_count=len(filtered),
                total_count=len(self._state.all_tasks),
            )

        # =====================================================================
        # GESTION DES ÉVÉNEMENTS
        # =====================================================================

        def _on_filters_changed(self, filters: DownloadFilters) -> None:
            """Gère le changement de filtres.

            Args:
                filters: Nouveaux filtres.
            """
            self._state.filters = filters
            asyncio.create_task(self._apply_filters())

        def _on_task_selected(self, task_id: str) -> None:
            """Gère la sélection d'une tâche.

            Args:
                task_id: ID de la tâche.
            """
            self._state.selected_task_id = task_id
            task = self._state.selected_task
            self._details_panel.set_task(task)
            self.task_selected.emit(task_id)

        def _on_task_action(self, task_id: str, action: str) -> None:
            """Gère une action sur une tâche.

            Args:
                task_id: ID de la tâche.
                action: Action à effectuer.
            """
            self.task_action.emit(task_id, action)

        def _on_global_action(self, action: str) -> None:
            """Gère une action globale.

            Args:
                action: Action à effectuer.
            """
            # TODO: Implémenter les actions globales
            logger.info("Action globale: {}", action)


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
    "TOOLBAR_HEIGHT",
    "TASK_ITEM_HEIGHT",
    "DETAILS_PANEL_WIDTH",
    # Exceptions
    "DownloadViewError",
    "DownloadManagerNotAvailableError",
    "TaskActionError",
    # Enums
    "DownloadFilter",
    "DownloadSortBy",
    # Modèles
    "DownloadFilters",
    "DownloadStats",
    "DownloadState",
    # Widgets
    "DownloadView" if PYQT6_AVAILABLE else None,
    "DownloadToolbar" if PYQT6_AVAILABLE else None,
    "DownloadTaskItem" if PYQT6_AVAILABLE else None,
    "DownloadTasksList" if PYQT6_AVAILABLE else None,
    "DownloadDetailsPanel" if PYQT6_AVAILABLE else None,
    "DownloadStatsBar" if PYQT6_AVAILABLE else None,
    "DownloadActionsBar" if PYQT6_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
