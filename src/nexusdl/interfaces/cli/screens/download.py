"""Écran de gestion des téléchargements de l'interface CLI NexusDL.

Ce module fournit un écran complet de gestion des téléchargements via une
interface TUI (Terminal User Interface) basée sur Textual. Il permet à
l'utilisateur de visualiser toutes les tâches de téléchargement, les contrôler
(pause, reprise, annulation), surveiller la progression en temps réel, et
effectuer des actions globales.

**Fonctionnalités** :
    - Liste des tâches avec progression en temps réel
    - Filtrage par statut (toutes, actives, en attente, terminées, échouées)
    - Tri par date, progression, taille, nom
    - Actions individuelles (pause, reprise, annulation, retry, suppression)
    - Actions globales (tout暂停, tout reprendre, tout supprimer)
    - Statistiques globales (vitesse, temps restant, taille totale)
    - Panneau de détails pour une tâche sélectionnée
    - Barre de progression visuelle
    - Mises à jour temps réel via EventBus
    - Navigation clavier complète (flèches, Enter, p, r, x, d, etc.)
    - Traductions i18n
    - Gestion des erreurs

**Architecture** :
    DownloadScreen (Screen Textual)
        ├── DownloadFilterBar (barre de filtrage)
        │   ├── Select (filtre statut)
        │   ├── Select (tri)
        │   └── Input (recherche)
        ├── DownloadTasksList (liste des tâches)
        │   └── DownloadTaskItem (item individuel avec progression)
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
    >>> from nexusdl.interfaces.cli.screens.download import DownloadScreen
    >>>
    >>> # Dans l'application principale
    >>> app.push_screen(DownloadScreen())
    >>>
    >>> # L'utilisateur peut :
    >>> # 1. Voir toutes les tâches de téléchargement
    >>> # 2. Filtrer par statut (actives, terminées, etc.)
    >>> # 3. Sélectionner une tâche avec les flèches
    >>> # 4. Mettre en pause avec 'p'
    >>> # 5. Reprendre avec 'r'
    >>> # 6. Annuler avec 'x'
    >>> # 7. Retry avec 'e'
    >>> # 8. Supprimer avec 'd'
    >>> # 9. Voir les détails avec Enter
    >>> # 10. Fermer avec Escape

Intégration :
    - core/downloader/manager.py : accès au DownloadManager
    - core/models/download.py : modèles DownloadTask, DownloadStatus
    - core/events.py : abonnement aux événements de progression
    - core/i18n.py : traductions
    - core/utils/text.py : format_size
    - core/utils/time.py : format_duration
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from enum import Enum
from typing import Any, ClassVar, Final

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

try:
    from textual.app import ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal, Vertical, VerticalScroll
    from textual.message import Message
    from textual.reactive import reactive
    from textual.screen import Screen
    from textual.widget import Widget
    from textual.widgets import (
        Button,
        Footer,
        Header,
        Input,
        Label,
        ListItem,
        ListView,
        ProgressBar,
        Select,
        Static,
    )
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False

from nexusdl.core.constants import APP_NAME
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t
from nexusdl.core.models.download import DownloadStatus, DownloadTask, Priority
from nexusdl.core.utils.text import format_size
from nexusdl.core.utils.time import format_duration


# ============================================================================
# EXCEPTIONS
# ============================================================================


class DownloadScreenError(NexusDLError):
    """Exception de base pour les erreurs de l'écran de téléchargement."""


class DownloadManagerNotAvailableError(DownloadScreenError):
    """Exception levée lorsque le DownloadManager n'est pas disponible."""

    def __init__(self) -> None:
        super().__init__(
            "DownloadManager n'est pas disponible. "
            "Assurez-vous que l'application est correctement initialisée."
        )


class TaskActionError(DownloadScreenError):
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
        ACTIVE: Tâches en cours (RUNNING, DOWNLOADING).
        PENDING: Tâches en attente (PENDING, QUEUED).
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

    def matches_status(self, status: DownloadStatus) -> bool:
        """Vérifie si un statut correspond au filtre.

        Args:
            status: Statut à vérifier.

        Returns:
            True si le statut correspond.
        """
        if self == DownloadFilter.ALL:
            return True
        if self == DownloadFilter.ACTIVE:
            return status in (DownloadStatus.RUNNING, DownloadStatus.DOWNLOADING)
        if self == DownloadFilter.PENDING:
            return status in (DownloadStatus.PENDING, DownloadStatus.QUEUED)
        if self == DownloadFilter.COMPLETED:
            return status == DownloadStatus.COMPLETED
        if self == DownloadFilter.FAILED:
            return status == DownloadStatus.FAILED
        if self == DownloadFilter.CANCELLED:
            return status == DownloadStatus.CANCELLED
        if self == DownloadFilter.PAUSED:
            return status == DownloadStatus.PAUSED
        return False


class DownloadSortBy(str, Enum):
    """Critère de tri des tâches.

    Attributes:
        DATE_ADDED: Tri par date d'ajout (plus récent en premier).
        PROGRESS: Tri par progression (plus avancé en premier).
        SIZE: Tri par taille (plus gros en premier).
        NAME: Tri par nom (alphabétique).
        PRIORITY: Tri par priorité (plus haute en premier).
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
# MODÈLES PYDANTIC
# ============================================================================


class DownloadFilters(BaseModel):
    """Filtres appliqués aux tâches de téléchargement.

    Attributes:
        status_filter: Filtre par statut.
        sort_by: Critère de tri.
        search_query: Texte de recherche.
        reverse: Si True, tri inversé.
    """

    status_filter: DownloadFilter = Field(
        default=DownloadFilter.ALL,
        description="Filtre par statut.",
    )
    sort_by: DownloadSortBy = Field(
        default=DownloadSortBy.DATE_ADDED,
        description="Critère de tri.",
    )
    search_query: str = Field(
        default="",
        description="Recherche.",
    )
    reverse: bool = Field(
        default=False,
        description="Tri inversé.",
    )

    model_config = ConfigDict(extra="forbid")


class DownloadStats(BaseModel):
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

    total_tasks: int = Field(default=0, ge=0)
    active_tasks: int = Field(default=0, ge=0)
    pending_tasks: int = Field(default=0, ge=0)
    completed_tasks: int = Field(default=0, ge=0)
    failed_tasks: int = Field(default=0, ge=0)
    total_size_bytes: int = Field(default=0, ge=0)
    downloaded_bytes: int = Field(default=0, ge=0)
    average_speed_bytes_per_sec: float = Field(default=0.0, ge=0.0)
    estimated_time_remaining_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def total_size_human(self) -> str:
        """Taille totale formatée."""
        return format_size(self.total_size_bytes)

    @property
    def downloaded_size_human(self) -> str:
        """Taille téléchargée formatée."""
        return format_size(self.downloaded_bytes)

    @property
    def average_speed_human(self) -> str:
        """Vitesse moyenne formatée."""
        return f"{format_size(int(self.average_speed_bytes_per_sec))}/s"

    @property
    def estimated_time_human(self) -> str:
        """Temps restant estimé formaté."""
        from datetime import timedelta
        return format_duration(timedelta(seconds=self.estimated_time_remaining_seconds))

    @property
    def overall_progress(self) -> float:
        """Progression globale (0.0 à 1.0)."""
        if self.total_size_bytes == 0:
            return 0.0
        return self.downloaded_bytes / self.total_size_bytes


class DownloadState(BaseModel):
    """État de l'écran de téléchargement.

    Attributes:
        tasks: Liste des tâches affichées.
        all_tasks: Liste de toutes les tâches (non filtrées).
        selected_task_id: ID de la tâche sélectionnée.
        filters: Filtres actifs.
        stats: Statistiques globales.
        loading: Indique si en cours de chargement.
        error: Message d'erreur.
    """

    tasks: list[DownloadTask] = Field(default_factory=list, description="Tâches affichées.")
    all_tasks: list[DownloadTask] = Field(default_factory=list, description="Toutes les tâches.")
    selected_task_id: str | None = Field(default=None, description="Sélection.")
    filters: DownloadFilters = Field(default_factory=DownloadFilters, description="Filtres.")
    stats: DownloadStats = Field(default_factory=DownloadStats, description="Stats.")
    loading: bool = Field(default=False, description="Chargement.")
    error: str | None = Field(default=None, description="Erreur.")

    model_config = ConfigDict(extra="forbid")

    @property
    def selected_task(self) -> DownloadTask | None:
        """Tâche sélectionnée."""
        if self.selected_task_id is None:
            return None
        for task in self.tasks:
            if task.id == self.selected_task_id:
                return task
        return None

    @property
    def filtered_count(self) -> int:
        """Nombre de tâches après filtrage."""
        return len(self.tasks)

    @property
    def total_count(self) -> int:
        """Nombre total de tâches."""
        return len(self.all_tasks)


# ============================================================================
# WIDGETS CUSTOM — Composants de l'écran
# ============================================================================


if TEXTUAL_AVAILABLE:

    class DownloadFilterBar(Widget):
        """Barre de filtrage des tâches."""

        DEFAULT_CSS = """
        DownloadFilterBar {
            layout: horizontal;
            height: 3;
            padding: 0 1;
            border-bottom: solid $primary;
        }
        DownloadFilterBar > Label {
            width: auto;
            content-align: left middle;
            padding: 0 1;
        }
        DownloadFilterBar > Select {
            width: 20;
        }
        DownloadFilterBar > Input {
            width: 1fr;
        }
        """

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise la barre de filtres."""
            super().__init__(name=name, id=id, classes=classes)
            self._filters = DownloadFilters()

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Label(t("download.filter.status", default="Status:"))
            yield Select(
                [(f.value, f"{f.icon} {f.label}") for f in DownloadFilter],
                id="status-filter",
                value=DownloadFilter.ALL.value,
                allow_blank=False,
            )

            yield Label(t("download.filter.sort", default="Sort:"))
            yield Select(
                [(s.value, s.label) for s in DownloadSortBy],
                id="sort-filter",
                value=DownloadSortBy.DATE_ADDED.value,
                allow_blank=False,
            )

            yield Label(t("download.filter.search", default="🔍"))
            yield Input(
                placeholder=t("download.filter.search.placeholder", default="Search tasks..."),
                id="download-search",
            )

        def get_filters(self) -> DownloadFilters:
            """Récupère les filtres actuels.

            Returns:
                Instance de DownloadFilters.
            """
            status_select = self.query_one("#status-filter", Select)
            sort_select = self.query_one("#sort-filter", Select)
            search_input = self.query_one("#download-search", Input)

            return DownloadFilters(
                status_filter=DownloadFilter(status_select.value),
                sort_by=DownloadSortBy(sort_select.value),
                search_query=search_input.value.strip(),
            )

        def on_select_changed(self, event: Select.Changed) -> None:
            """Gère le changement de sélection."""
            self.post_message(FilterChanged(self.get_filters()))

        def on_input_changed(self, event: Input.Changed) -> None:
            """Gère le changement de texte."""
            if event.input.id == "download-search":
                self.post_message(FilterChanged(self.get_filters()))

    class DownloadTaskItem(ListItem):
        """Item individuel dans la liste des tâches."""

        DEFAULT_CSS = """
        DownloadTaskItem {
            padding: 1;
            height: auto;
            min-height: 3;
        }
        DownloadTaskItem > .task-title {
            text-style: bold;
        }
        DownloadTaskItem > .task-meta {
            color: $text-muted;
        }
        DownloadTaskItem > .task-progress {
            color: $accent;
        }
        DownloadTaskItem.selected {
            background: $primary-background;
        }
        DownloadTaskItem.status-running {
            border-left: thick $success;
        }
        DownloadTaskItem.status-pending {
            border-left: thick $warning;
        }
        DownloadTaskItem.status-completed {
            border-left: thick $success;
            opacity: 0.7;
        }
        DownloadTaskItem.status-failed {
            border-left: thick $error;
        }
        DownloadTaskItem.status-paused {
            border-left: thick $warning;
            opacity: 0.8;
        }
        """

        def __init__(
            self,
            task: DownloadTask,
            *,
            selected: bool = False,
        ) -> None:
            """Initialise l'item.

            Args:
                task: Tâche de téléchargement.
                selected: Indique si l'item est sélectionné.
            """
            super().__init__()
            self.task = task
            self.selected = selected

            # Ajouter la classe de statut
            self.add_class(f"status-{task.status.value}")
            if selected:
                self.add_class("selected")

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            # Titre
            title = f"{'✓ ' if self.selected else ''}{self.task.manga.title}"
            yield Static(title, classes="task-title")

            # Métadonnées
            meta_parts = [
                f"{self.task.status.icon} {self.task.status.label}",
                f"{self.task.pages_completed}/{self.task.pages_total} pages",
                format_size(self.task.total_size_bytes),
            ]
            if self.task.priority != Priority.NORMAL:
                meta_parts.append(f"⚡ {self.task.priority.label}")

            yield Static(" • ".join(meta_parts), classes="task-meta")

            # Progression
            if self.task.status in (DownloadStatus.RUNNING, DownloadStatus.DOWNLOADING):
                progress_text = f"{self.task.progress:.1%}"
                if self.task.estimated_time_remaining_seconds > 0:
                    from datetime import timedelta
                    eta = format_duration(timedelta(seconds=self.task.estimated_time_remaining_seconds))
                    progress_text += f" • ETA: {eta}"
                yield Static(progress_text, classes="task-progress")

        def update_task(self, task: DownloadTask) -> None:
            """Met à jour la tâche.

            Args:
                task: Nouvelle tâche.
            """
            self.task = task
            self.refresh()

        def set_selected(self, selected: bool) -> None:
            """Définit l'état de sélection.

            Args:
                selected: True si sélectionné.
            """
            self.selected = selected
            if selected:
                self.add_class("selected")
            else:
                self.remove_class("selected")

    class DownloadTasksList(Widget):
        """Liste des tâches de téléchargement."""

        DEFAULT_CSS = """
        DownloadTasksList {
            height: 1fr;
            padding: 1;
        }
        DownloadTasksList > ListView {
            height: 1fr;
        }
        DownloadTasksList > .empty-message {
            text-align: center;
            padding: 2;
            color: $text-muted;
        }
        """

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise la liste."""
            super().__init__(name=name, id=id, classes=classes)
            self._tasks: list[DownloadTask] = []
            self._selected_id: str | None = None

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(
                t("download.empty", default="No downloads"),
                id="downloads-empty",
                classes="empty-message",
            )
            yield ListView(id="downloads-list")

        def update_tasks(self, tasks: list[DownloadTask]) -> None:
            """Met à jour les tâches affichées.

            Args:
                tasks: Liste de tâches.
            """
            self._tasks = tasks
            list_view = self.query_one("#downloads-list", ListView)
            empty_msg = self.query_one("#downloads-empty", Static)

            if not tasks:
                list_view.clear()
                empty_msg.display = True
                list_view.display = False
                return

            empty_msg.display = False
            list_view.display = True
            list_view.clear()

            for task in tasks:
                item = DownloadTaskItem(
                    task,
                    selected=task.id == self._selected_id,
                )
                list_view.append(item)

        def update_task_progress(self, task_id: str, task: DownloadTask) -> None:
            """Met à jour la progression d'une tâche.

            Args:
                task_id: ID de la tâche.
                task: Tâche mise à jour.
            """
            list_view = self.query_one("#downloads-list", ListView)
            for child in list_view.children:
                if isinstance(child, DownloadTaskItem) and child.task.id == task_id:
                    child.update_task(task)
                    break

        def set_selected(self, task_id: str | None) -> None:
            """Définit la tâche sélectionnée.

            Args:
                task_id: ID de la tâche ou None.
            """
            self._selected_id = task_id

            list_view = self.query_one("#downloads-list", ListView)
            for child in list_view.children:
                if isinstance(child, DownloadTaskItem):
                    child.set_selected(child.task.id == task_id)

    class DownloadDetailsPanel(Widget):
        """Panneau de détails d'une tâche."""

        DEFAULT_CSS = """
        DownloadDetailsPanel {
            width: 50;
            border-left: solid $primary;
            padding: 1;
        }
        DownloadDetailsPanel > .panel-title {
            text-style: bold;
            padding: 0 0 1 0;
        }
        DownloadDetailsPanel > .task-info {
            padding: 1;
        }
        DownloadDetailsPanel > .progress-bar {
            height: 3;
            margin: 1 0;
        }
        DownloadDetailsPanel > .chapters-list {
            height: 1fr;
        }
        DownloadDetailsPanel > .actions {
            height: 3;
            layout: horizontal;
        }
        """

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise le panneau."""
            super().__init__(name=name, id=id, classes=classes)
            self._task: DownloadTask | None = None

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(
                t("download.details.title", default="Task Details"),
                classes="panel-title",
            )
            yield Static(
                t("download.details.empty", default="Select a task to view details"),
                id="details-empty",
            )
            with Vertical(id="details-content"):
                yield Static(id="task-info", classes="task-info")
                yield ProgressBar(total=100, id="task-progress", classes="progress-bar")
                yield ListView(id="chapters-list", classes="chapters-list")
                with Horizontal(classes="actions"):
                    yield Button(
                        t("download.details.pause", default="Pause"),
                        id="btn-pause",
                        variant="warning",
                    )
                    yield Button(
                        t("download.details.resume", default="Resume"),
                        id="btn-resume",
                        variant="success",
                    )
                    yield Button(
                        t("download.details.cancel", default="Cancel"),
                        id="btn-cancel",
                        variant="error",
                    )

        def update_task(self, task: DownloadTask | None) -> None:
            """Met à jour la tâche affichée.

            Args:
                task: Tâche à afficher ou None.
            """
            self._task = task

            empty_msg = self.query_one("#details-empty", Static)
            content = self.query_one("#details-content", Vertical)

            if task is None:
                empty_msg.display = True
                content.display = False
                return

            empty_msg.display = False
            content.display = True

            # Mettre à jour les informations
            info_widget = self.query_one("#task-info", Static)
            info_text = f"""
[b]{task.manga.title}[/b]
{t('download.details.status', default='Status')}: {task.status.icon} {task.status.label}
{t('download.details.progress', default='Progress')}: {task.progress:.1%}
{t('download.details.pages', default='Pages')}: {task.pages_completed}/{task.pages_total}
{t('download.details.size', default='Size')}: {format_size(task.total_size_bytes)}
{t('download.details.priority', default='Priority')}: {task.priority.label}
{t('download.details.format', default='Format')}: {task.format.value.upper()}

{t('download.details.destination', default='Destination')}: {task.destination_path}
"""
            info_widget.update(info_text)

            # Mettre à jour la barre de progression
            progress_bar = self.query_one("#task-progress", ProgressBar)
            progress_bar.update(progress=task.progress * 100)

            # Mettre à jour la liste des chapitres
            chapters_list = self.query_one("#chapters-list", ListView)
            chapters_list.clear()

            if task.chapters:
                for chapter in task.chapters[:20]:  # Limiter à 20 chapitres
                    status_icon = "✅" if chapter.downloaded else "⏳"
                    chapters_list.append(
                        ListItem(Static(f"{status_icon} Ch. {chapter.number}: {chapter.title}"))
                    )

    class DownloadStatsBar(Widget):
        """Barre de statistiques globales."""

        DEFAULT_CSS = """
        DownloadStatsBar {
            layout: horizontal;
            height: 1;
            padding: 0 1;
            background: $primary-background;
        }
        DownloadStatsBar > .stat-item {
            padding: 0 1;
        }
        """

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise la barre de statistiques."""
            super().__init__(name=name, id=id, classes=classes)
            self._stats = DownloadStats()

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static("", id="stats-tasks", classes="stat-item")
            yield Static("", id="stats-speed", classes="stat-item")
            yield Static("", id="stats-eta", classes="stat-item")
            yield Static("", id="stats-progress", classes="stat-item")

        def update_stats(self, stats: DownloadStats, filtered_count: int, total_count: int) -> None:
            """Met à jour les statistiques affichées.

            Args:
                stats: Statistiques.
                filtered_count: Nombre de tâches affichées.
                total_count: Nombre total de tâches.
            """
            self._stats = stats

            tasks_widget = self.query_one("#stats-tasks", Static)
            speed_widget = self.query_one("#stats-speed", Static)
            eta_widget = self.query_one("#stats-eta", Static)
            progress_widget = self.query_one("#stats-progress", Static)

            tasks_widget.update(
                t(
                    "download.stats.tasks",
                    default="{filtered}/{total} tasks",
                    filtered=filtered_count,
                    total=total_count,
                )
            )

            if stats.active_tasks > 0:
                speed_widget.update(f"⚡ {stats.average_speed_human}")
                eta_widget.update(f"⏱️ {stats.estimated_time_human}")
            else:
                speed_widget.update("")
                eta_widget.update("")

            progress_widget.update(f"📊 {stats.overall_progress:.1%}")

    class DownloadActionsBar(Widget):
        """Barre d'actions globales."""

        DEFAULT_CSS = """
        DownloadActionsBar {
            layout: horizontal;
            height: 3;
            padding: 0 1;
            align: right middle;
        }
        DownloadActionsBar > Button {
            margin: 0 1;
        }
        """

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Button(
                t("download.actions.pause_all", default="Pause All"),
                id="btn-pause-all",
                variant="warning",
            )
            yield Button(
                t("download.actions.resume_all", default="Resume All"),
                id="btn-resume-all",
                variant="success",
            )
            yield Button(
                t("download.actions.clear_completed", default="Clear Completed"),
                id="btn-clear-completed",
                variant="default",
            )
            yield Button(
                t("download.actions.cancel_all", default="Cancel All"),
                id="btn-cancel-all",
                variant="error",
            )


# ============================================================================
# MESSAGES — Événements Textual
# ============================================================================


if TEXTUAL_AVAILABLE:

    class FilterChanged(Message):
        """Message émis lorsque les filtres changent."""

        def __init__(self, filters: DownloadFilters) -> None:
            """Initialise le message.

            Args:
                filters: Nouveaux filtres.
            """
            super().__init__()
            self.filters = filters

    class TaskSelected(Message):
        """Message émis lorsqu'une tâche est sélectionnée."""

        def __init__(self, task_id: str) -> None:
            """Initialise le message.

            Args:
                task_id: ID de la tâche.
            """
            super().__init__()
            self.task_id = task_id

    class TaskAction(Message):
        """Message émis lorsqu'une action est effectuée sur une tâche."""

        def __init__(self, task_id: str, action: str) -> None:
            """Initialise le message.

            Args:
                task_id: ID de la tâche.
                action: Action effectuée.
            """
            super().__init__()
            self.task_id = task_id
            self.action = action


# ============================================================================
# CLASSE PRINCIPALE — DownloadScreen
# ============================================================================


if TEXTUAL_AVAILABLE:

    class DownloadScreen(Screen):
        """Écran de gestion des téléchargements."""

        # Bindings clavier
        BINDINGS = [
            Binding("p", "pause_selected", "Pause"),
            Binding("r", "resume_selected", "Resume"),
            Binding("x", "cancel_selected", "Cancel"),
            Binding("e", "retry_selected", "Retry"),
            Binding("d", "delete_selected", "Delete"),
            Binding("enter", "view_details", "View Details"),
            Binding("escape", "close", "Close"),
            Binding("ctrl+p", "pause_all", "Pause All"),
            Binding("ctrl+r", "resume_all", "Resume All"),
        ]

        # CSS de l'écran
        DEFAULT_CSS = """
        DownloadScreen {
            layout: vertical;
        }

        #download-container {
            height: 1fr;
            layout: horizontal;
        }

        #main-content {
            width: 1fr;
            layout: vertical;
        }

        #filter-bar {
            height: auto;
        }

        #tasks-area {
            height: 1fr;
            layout: horizontal;
        }

        #tasks-list-container {
            width: 1fr;
        }

        #details-panel {
            width: 50;
            display: none;
        }

        #details-panel.visible {
            display: block;
        }

        #stats-bar {
            height: 1;
        }

        #actions-bar {
            height: 3;
        }
        """

        # État réactif
        selected_task_id: reactive[str | None] = reactive(None)

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise l'écran.

            Args:
                name: Nom de l'écran.
                id: ID de l'écran.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self._state = DownloadState()
            self._event_bus_subscription = None

        def compose(self) -> ComposeResult:
            """Compose l'écran."""
            yield Header(show_clock=True)

            with Horizontal(id="download-container"):
                # Contenu principal
                with Vertical(id="main-content"):
                    yield DownloadFilterBar(id="filter-bar")

                    with Horizontal(id="tasks-area"):
                        yield DownloadTasksList(id="tasks-list-container")
                        yield DownloadDetailsPanel(id="details-panel")

            yield DownloadStatsBar(id="stats-bar")
            yield DownloadActionsBar(id="actions-bar")
            yield Footer()

        def on_mount(self) -> None:
            """Appelé lors du montage."""
            # Charger les tâches
            asyncio.create_task(self._load_tasks())

            # S'abonner aux événements EventBus
            self._subscribe_to_events()

        def on_unmount(self) -> None:
            """Appelé lors du démontage."""
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
                # Mettre à jour la tâche dans la liste
                tasks_list = self.query_one("#tasks-list-container", DownloadTasksList)
                # TODO: Récupérer la tâche mise à jour depuis le manager
                # tasks_list.update_task_progress(task_id, updated_task)

        async def _on_task_completed(self, event: Any) -> None:
            """Gère l'événement de tâche terminée."""
            logger.debug("Tâche terminée, rafraîchissement")
            await self._load_tasks()

        async def _on_task_failed(self, event: Any) -> None:
            """Gère l'événement de tâche échouée."""
            logger.debug("Tâche échouée, rafraîchissement")
            await self._load_tasks()

        # =====================================================================
        # CHARGEMENT
        # =====================================================================

        async def _load_tasks(self) -> None:
            """Charge les tâches de téléchargement."""
            self._state.loading = True

            try:
                # Obtenir le DownloadManager
                from nexusdl.core.downloader import get_download_manager
                manager = get_download_manager()

                # Charger toutes les tâches
                all_tasks = await manager.get_all_tasks()
                self._state.all_tasks = all_tasks

                # Charger les statistiques
                stats = await manager.get_stats()
                self._state.stats = DownloadStats(
                    total_tasks=stats.total_tasks,
                    active_tasks=stats.running_tasks,
                    pending_tasks=stats.pending_tasks,
                    completed_tasks=stats.completed_tasks,
                    failed_tasks=stats.failed_tasks,
                    total_size_bytes=stats.total_size_bytes,
                    downloaded_bytes=stats.downloaded_bytes,
                    average_speed_bytes_per_sec=stats.average_speed_bytes_per_sec,
                    estimated_time_remaining_seconds=stats.estimated_time_remaining_seconds,
                )

                # Appliquer les filtres
                await self._apply_filters()

                self._state.loading = False
                self._state.error = None

                logger.info("Tâches chargées: {} tâches", len(all_tasks))

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
                filtered = [
                    t for t in filtered
                    if filters.status_filter.matches_status(t.status)
                ]

            # Recherche texte
            if filters.search_query:
                query_lower = filters.search_query.lower()
                filtered = [
                    t for t in filtered
                    if query_lower in t.manga.title.lower()
                ]

            # Tri
            if filters.sort_by == DownloadSortBy.DATE_ADDED:
                filtered.sort(key=lambda t: t.created_at, reverse=not filters.reverse)
            elif filters.sort_by == DownloadSortBy.PROGRESS:
                filtered.sort(key=lambda t: t.progress, reverse=not filters.reverse)
            elif filters.sort_by == DownloadSortBy.SIZE:
                filtered.sort(key=lambda t: t.total_size_bytes, reverse=not filters.reverse)
            elif filters.sort_by == DownloadSortBy.NAME:
                filtered.sort(key=lambda t: t.manga.title.lower(), reverse=not filters.reverse)
            elif filters.sort_by == DownloadSortBy.PRIORITY:
                filtered.sort(key=lambda t: t.priority.value, reverse=not filters.reverse)
            elif filters.sort_by == DownloadSortBy.STATUS:
                filtered.sort(key=lambda t: t.status.value, reverse=not filters.reverse)

            self._state.tasks = filtered

            # Mettre à jour l'UI
            tasks_list = self.query_one("#tasks-list-container", DownloadTasksList)
            tasks_list.update_tasks(filtered)

            # Mettre à jour la barre de statistiques
            stats_bar = self.query_one("#stats-bar", DownloadStatsBar)
            stats_bar.update_stats(
                self._state.stats,
                filtered_count=len(filtered),
                total_count=len(self._state.all_tasks),
            )

        # =====================================================================
        # GESTION DES MESSAGES
        # =====================================================================

        def on_filter_changed(self, message: FilterChanged) -> None:
            """Gère un changement de filtres.

            Args:
                message: Message avec les nouveaux filtres.
            """
            self._state.filters = message.filters
            asyncio.create_task(self._apply_filters())

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            """Gère la sélection d'un item dans la liste."""
            if isinstance(event.item, DownloadTaskItem):
                self.selected_task_id = event.item.task.id
                self._show_details(event.item.task)

        # =====================================================================
        # ACTIONS — Bindings clavier
        # =====================================================================

        def action_pause_selected(self) -> None:
            """Action : mettre en pause la tâche sélectionnée."""
            if not self.selected_task_id:
                self.notify(
                    t("download.notify.no_selection", default="No task selected"),
                    severity="warning",
                )
                return

            asyncio.create_task(self._pause_task(self.selected_task_id))

        def action_resume_selected(self) -> None:
            """Action : reprendre la tâche sélectionnée."""
            if not self.selected_task_id:
                self.notify(
                    t("download.notify.no_selection", default="No task selected"),
                    severity="warning",
                )
                return

            asyncio.create_task(self._resume_task(self.selected_task_id))

        def action_cancel_selected(self) -> None:
            """Action : annuler la tâche sélectionnée."""
            if not self.selected_task_id:
                self.notify(
                    t("download.notify.no_selection", default="No task selected"),
                    severity="warning",
                )
                return

            asyncio.create_task(self._cancel_task(self.selected_task_id))

        def action_retry_selected(self) -> None:
            """Action : retry la tâche sélectionnée."""
            if not self.selected_task_id:
                self.notify(
                    t("download.notify.no_selection", default="No task selected"),
                    severity="warning",
                )
                return

            asyncio.create_task(self._retry_task(self.selected_task_id))

        def action_delete_selected(self) -> None:
            """Action : supprimer la tâche sélectionnée."""
            if not self.selected_task_id:
                self.notify(
                    t("download.notify.no_selection", default="No task selected"),
                    severity="warning",
                )
                return

            # TODO: Afficher un dialogue de confirmation
            asyncio.create_task(self._delete_task(self.selected_task_id))

        def action_view_details(self) -> None:
            """Action : afficher/masquer les détails."""
            details_panel = self.query_one("#details-panel")

            if "visible" in details_panel.classes:
                details_panel.remove_class("visible")
            else:
                details_panel.add_class("visible")

                # Afficher les détails de la tâche sélectionnée
                if self.selected_task_id:
                    task = self._state.selected_task
                    if task:
                        details_widget = self.query_one("#details-panel", DownloadDetailsPanel)
                        details_widget.update_task(task)

        def action_pause_all(self) -> None:
            """Action : mettre en pause toutes les tâches actives."""
            asyncio.create_task(self._pause_all_tasks())

        def action_resume_all(self) -> None:
            """Action : reprendre toutes les tâches en pause."""
            asyncio.create_task(self._resume_all_tasks())

        def action_close(self) -> None:
            """Action : fermer l'écran."""
            self.app.pop_screen()

        # =====================================================================
        # ACTIONS SUR LES TÂCHES
        # =====================================================================

        async def _pause_task(self, task_id: str) -> None:
            """Met en pause une tâche.

            Args:
                task_id: ID de la tâche.
            """
            try:
                from nexusdl.core.downloader import get_download_manager
                manager = get_download_manager()
                await manager.pause_task(task_id)

                self.notify(
                    t("download.notify.task_paused", default="Task paused"),
                    severity="information",
                )

                await self._load_tasks()

            except Exception as e:
                logger.error("Erreur lors de la mise en pause: {}", e)
                self.notify(
                    t("download.notify.pause_failed", default="Pause failed: {error}", error=str(e)),
                    severity="error",
                )

        async def _resume_task(self, task_id: str) -> None:
            """Reprend une tâche.

            Args:
                task_id: ID de la tâche.
            """
            try:
                from nexusdl.core.downloader import get_download_manager
                manager = get_download_manager()
                await manager.resume_task(task_id)

                self.notify(
                    t("download.notify.task_resumed", default="Task resumed"),
                    severity="information",
                )

                await self._load_tasks()

            except Exception as e:
                logger.error("Erreur lors de la reprise: {}", e)
                self.notify(
                    t("download.notify.resume_failed", default="Resume failed: {error}", error=str(e)),
                    severity="error",
                )

        async def _cancel_task(self, task_id: str) -> None:
            """Annule une tâche.

            Args:
                task_id: ID de la tâche.
            """
            try:
                from nexusdl.core.downloader import get_download_manager
                manager = get_download_manager()
                await manager.cancel_task(task_id)

                self.notify(
                    t("download.notify.task_cancelled", default="Task cancelled"),
                    severity="information",
                )

                await self._load_tasks()

            except Exception as e:
                logger.error("Erreur lors de l'annulation: {}", e)
                self.notify(
                    t("download.notify.cancel_failed", default="Cancel failed: {error}", error=str(e)),
                    severity="error",
                )

        async def _retry_task(self, task_id: str) -> None:
            """Retry une tâche échouée.

            Args:
                task_id: ID de la tâche.
            """
            try:
                from nexusdl.core.downloader import get_download_manager
                manager = get_download_manager()
                await manager.retry_task(task_id)

                self.notify(
                    t("download.notify.task_retried", default="Task retrying"),
                    severity="information",
                )

                await self._load_tasks()

            except Exception as e:
                logger.error("Erreur lors du retry: {}", e)
                self.notify(
                    t("download.notify.retry_failed", default="Retry failed: {error}", error=str(e)),
                    severity="error",
                )

        async def _delete_task(self, task_id: str) -> None:
            """Supprime une tâche.

            Args:
                task_id: ID de la tâche.
            """
            try:
                from nexusdl.core.downloader import get_download_manager
                manager = get_download_manager()
                await manager.delete_task(task_id)

                self.notify(
                    t("download.notify.task_deleted", default="Task deleted"),
                    severity="information",
                )

                await self._load_tasks()

            except Exception as e:
                logger.error("Erreur lors de la suppression: {}", e)
                self.notify(
                    t("download.notify.delete_failed", default="Delete failed: {error}", error=str(e)),
                    severity="error",
                )

        async def _pause_all_tasks(self) -> None:
            """Met en pause toutes les tâches actives."""
            try:
                from nexusdl.core.downloader import get_download_manager
                manager = get_download_manager()
                await manager.pause_all_tasks()

                self.notify(
                    t("download.notify.all_paused", default="All tasks paused"),
                    severity="information",
                )

                await self._load_tasks()

            except Exception as e:
                logger.error("Erreur lors de la mise en pause globale: {}", e)
                self.notify(
                    t("download.notify.pause_all_failed", default="Pause all failed: {error}", error=str(e)),
                    severity="error",
                )

        async def _resume_all_tasks(self) -> None:
            """Reprend toutes les tâches en pause."""
            try:
                from nexusdl.core.downloader import get_download_manager
                manager = get_download_manager()
                await manager.resume_all_tasks()

                self.notify(
                    t("download.notify.all_resumed", default="All tasks resumed"),
                    severity="information",
                )

                await self._load_tasks()

            except Exception as e:
                logger.error("Erreur lors de la reprise globale: {}", e)
                self.notify(
                    t("download.notify.resume_all_failed", default="Resume all failed: {error}", error=str(e)),
                    severity="error",
                )

        # =====================================================================
        # AFFICHAGE DES DÉTAILS
        # =====================================================================

        def _show_details(self, task: DownloadTask) -> None:
            """Affiche les détails d'une tâche.

            Args:
                task: Tâche à afficher.
            """
            details_panel = self.query_one("#details-panel", DownloadDetailsPanel)
            details_panel.update_task(task)
            details_panel.add_class("visible")


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "DownloadScreenError",
    "DownloadManagerNotAvailableError",
    "TaskActionError",
    # Enums
    "DownloadFilter",
    "DownloadSortBy",
    # Modèles
    "DownloadFilters",
    "DownloadStats",
    "DownloadState",
    # Écran principal
    "DownloadScreen" if TEXTUAL_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
