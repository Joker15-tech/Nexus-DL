"""Écran principal de l'interface CLI NexusDL.

Ce module fournit l'écran principal (dashboard) de l'interface CLI NexusDL,
basé sur le framework Textual. Il sert de hub central pour naviguer vers
toutes les fonctionnalités de l'application et affiche un aperçu en temps
réel de l'état du système.

**Sections de l'écran** :
    - Header : titre de l'application et version
    - Dashboard : statistiques globales (mangas, chapitres, taille)
    - Continue Reading : mangas en cours de lecture
    - Recent Downloads : derniers téléchargements effectués
    - Active Tasks : tâches de téléchargement en cours
    - Quick Actions : boutons d'accès rapide aux fonctionnalités
    - Footer : barre de statut avec raccourcis clavier

**Fonctionnalités** :
    - Dashboard avec statistiques en temps réel
    - Liste "Continue Reading" pour reprendre la lecture
    - Liste "Recent Downloads" avec aperçu des derniers téléchargements
    - Liste "Active Tasks" avec progression des téléchargements en cours
    - Boutons d'action rapide (recherche, bibliothèque, paramètres)
    - Mise à jour automatique via EventBus
    - Navigation clavier complète (1-9 pour actions rapides)
    - Traductions i18n
    - Gestion des erreurs

**Architecture** :
    MainScreen (Screen Textual)
        ├── Header (titre + version)
        ├── Dashboard (statistiques globales)
        │   ├── StatCard (mangas)
        │   ├── StatCard (chapitres)
        │   └── StatCard (taille)
        ├── ContinueReadingSection
        │   └── ContinueReadingItem (manga en cours)
        ├── RecentDownloadsSection
        │   └── RecentDownloadItem (téléchargement récent)
        ├── ActiveTasksSection
        │   └── ActiveTaskItem (tâche en cours)
        ├── QuickActions (boutons d'action)
        └── Footer (barre de statut)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.cli.screens.main import MainScreen
    >>>
    >>> # Dans l'application principale
    >>> app.push_screen(MainScreen())
    >>>
    >>> # L'utilisateur peut :
    >>> # 1. Voir les statistiques globales
    >>> # 2. Reprendre la lecture d'un manga en cours
    >>> # 3. Voir les derniers téléchargements
    >>> # 4. Surveiller les tâches en cours
    >>> # 5. Accéder rapidement aux fonctionnalités via les boutons
    >>> # 6. Naviguer avec les touches 1-9

Intégration :
    - core/library/database.py : statistiques de la bibliothèque
    - core/downloader/manager.py : tâches de téléchargement
    - core/events.py : mises à jour en temps réel
    - core/i18n.py : traductions
    - core/logger.py : logs des actions
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
        Label,
        ListItem,
        ListView,
        Static,
    )
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False

from nexusdl.core.constants import APP_NAME, APP_VERSION
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t
from nexusdl.core.library import get_library_stats
from nexusdl.core.downloader import get_download_manager, get_download_stats
from nexusdl.core.models.download import DownloadTask, DownloadStatus
from nexusdl.core.models.library import ContinueReadingEntry


# ============================================================================
# EXCEPTIONS
# ============================================================================


class MainScreenError(NexusDLError):
    """Exception de base pour les erreurs de l'écran principal."""


class DashboardLoadError(MainScreenError):
    """Exception levée lorsque le dashboard ne peut être chargé.

    Attributes:
        section: Section du dashboard.
        reason: Raison de l'échec.
    """

    def __init__(self, section: str, reason: str = "") -> None:
        msg = f"Échec du chargement du dashboard: {section}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.section = section
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class DashboardSection(str, Enum):
    """Sections du dashboard.

    Attributes:
        STATISTICS: Statistiques globales.
        CONTINUE_READING: Mangas en cours de lecture.
        RECENT_DOWNLOADS: Derniers téléchargements.
        ACTIVE_TASKS: Tâches de téléchargement en cours.
    """

    STATISTICS = "statistics"
    CONTINUE_READING = "continue_reading"
    RECENT_DOWNLOADS = "recent_downloads"
    ACTIVE_TASKS = "active_tasks"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            DashboardSection.STATISTICS: t("dashboard.section.statistics", default="Statistics"),
            DashboardSection.CONTINUE_READING: t("dashboard.section.continue_reading", default="Continue Reading"),
            DashboardSection.RECENT_DOWNLOADS: t("dashboard.section.recent_downloads", default="Recent Downloads"),
            DashboardSection.ACTIVE_TASKS: t("dashboard.section.active_tasks", default="Active Tasks"),
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            DashboardSection.STATISTICS: "📊",
            DashboardSection.CONTINUE_READING: "📖",
            DashboardSection.RECENT_DOWNLOADS: "📥",
            DashboardSection.ACTIVE_TASKS: "⚙️",
        }[self]


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class DashboardStats(BaseModel):
    """Statistiques du dashboard.

    Attributes:
        total_mangas: Nombre total de mangas dans la bibliothèque.
        total_chapters: Nombre total de chapitres téléchargés.
        total_size_bytes: Taille totale de la bibliothèque en bytes.
        total_reading_time_seconds: Temps total de lecture en secondes.
        currently_reading: Nombre de mangas en cours de lecture.
        completed: Nombre de mangas terminés.
        active_downloads: Nombre de téléchargements actifs.
        last_updated: Timestamp de la dernière mise à jour.
    """

    total_mangas: int = Field(default=0, ge=0, description="Total mangas.")
    total_chapters: int = Field(default=0, ge=0, description="Total chapitres.")
    total_size_bytes: int = Field(default=0, ge=0, description="Taille totale.")
    total_reading_time_seconds: float = Field(default=0.0, ge=0.0, description="Temps lecture.")
    currently_reading: int = Field(default=0, ge=0, description="En cours.")
    completed: int = Field(default=0, ge=0, description="Terminés.")
    active_downloads: int = Field(default=0, ge=0, description="Téléchargements actifs.")
    last_updated: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Dernière MAJ.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def total_size_human(self) -> str:
        """Taille totale formatée."""
        from nexusdl.core.utils.text import format_size
        return format_size(self.total_size_bytes)

    @property
    def reading_time_human(self) -> str:
        """Temps de lecture formaté."""
        from nexusdl.core.utils.time import format_duration
        from datetime import timedelta
        return format_duration(timedelta(seconds=self.total_reading_time_seconds))


class DashboardState(BaseModel):
    """État du dashboard.

    Attributes:
        stats: Statistiques globales.
        continue_reading: Liste des mangas en cours de lecture.
        recent_downloads: Liste des téléchargements récents.
        active_tasks: Liste des tâches actives.
        loading: Indique si le dashboard est en cours de chargement.
        error: Message d'erreur (si loading échoué).
    """

    stats: DashboardStats = Field(default_factory=DashboardStats, description="Statistiques.")
    continue_reading: list[ContinueReadingEntry] = Field(default_factory=list, description="En cours.")
    recent_downloads: list[DownloadTask] = Field(default_factory=list, description="Récents.")
    active_tasks: list[DownloadTask] = Field(default_factory=list, description="Actifs.")
    loading: bool = Field(default=False, description="Chargement.")
    error: str | None = Field(default=None, description="Erreur.")

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# WIDGETS CUSTOM — Composants du dashboard
# ============================================================================


if TEXTUAL_AVAILABLE:

    class StatCard(Widget):
        """Carte de statistique individuelle.

        Affiche une statistique avec icône, label et valeur.
        """

        DEFAULT_CSS = """
        StatCard {
            width: 1fr;
            height: 5;
            padding: 1;
            border: solid $primary;
            background: $surface;
        }
        StatCard > .stat-icon {
            text-align: center;
            text-style: bold;
        }
        StatCard > .stat-value {
            text-align: center;
            text-style: bold;
            color: $text;
        }
        StatCard > .stat-label {
            text-align: center;
            color: $text-muted;
        }
        """

        def __init__(
            self,
            icon: str,
            value: str,
            label: str,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise la carte de statistique.

            Args:
                icon: Icône Unicode.
                value: Valeur à afficher.
                label: Libellé de la statistique.
                name: Nom du widget.
                id: ID du widget.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self.icon = icon
            self.value = value
            self.label = label

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(self.icon, classes="stat-icon")
            yield Static(self.value, classes="stat-value")
            yield Static(self.label, classes="stat-label")

        def update_value(self, value: str) -> None:
            """Met à jour la valeur affichée.

            Args:
                value: Nouvelle valeur.
            """
            self.value = value
            value_widget = self.query_one(".stat-value", Static)
            value_widget.update(value)

    class ContinueReadingItem(ListItem):
        """Item dans la liste "Continue Reading".

        Affiche un manga en cours de lecture avec progression.
        """

        DEFAULT_CSS = """
        ContinueReadingItem {
            padding: 1;
            height: auto;
        }
        ContinueReadingItem > .manga-title {
            text-style: bold;
            color: $text;
        }
        ContinueReadingItem > .manga-progress {
            color: $text-muted;
        }
        ContinueReadingItem > .manga-chapter {
            color: $accent;
        }
        """

        def __init__(
            self,
            entry: ContinueReadingEntry,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise l'item.

            Args:
                entry: Entrée de continuation de lecture.
                name: Nom du widget.
                id: ID du widget.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self.entry = entry

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(self.entry.title, classes="manga-title")
            yield Static(
                f"{self.entry.chapter_display} • Page {self.entry.page}",
                classes="manga-chapter",
            )
            yield Static(
                f"Last read: {self.entry.last_read_at.strftime('%Y-%m-%d %H:%M')}",
                classes="manga-progress",
            )

    class RecentDownloadItem(ListItem):
        """Item dans la liste "Recent Downloads".

        Affiche un téléchargement récent avec statut.
        """

        DEFAULT_CSS = """
        RecentDownloadItem {
            padding: 1;
            height: auto;
        }
        RecentDownloadItem > .download-title {
            text-style: bold;
            color: $text;
        }
        RecentDownloadItem > .download-status {
            color: $text-muted;
        }
        RecentDownloadItem > .download-date {
            color: $accent;
        }
        """

        def __init__(
            self,
            task: DownloadTask,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise l'item.

            Args:
                task: Tâche de téléchargement.
                name: Nom du widget.
                id: ID du widget.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self.task = task

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(self.task.manga.title, classes="download-title")
            yield Static(
                f"{self.task.status.icon} {self.task.status.label}",
                classes="download-status",
            )
            yield Static(
                f"Completed: {self.task.completed_at.strftime('%Y-%m-%d %H:%M') if self.task.completed_at else 'N/A'}",
                classes="download-date",
            )

    class ActiveTaskItem(ListItem):
        """Item dans la liste "Active Tasks".

        Affiche une tâche de téléchargement en cours avec progression.
        """

        DEFAULT_CSS = """
        ActiveTaskItem {
            padding: 1;
            height: auto;
        }
        ActiveTaskItem > .task-title {
            text-style: bold;
            color: $text;
        }
        ActiveTaskItem > .task-progress {
            color: $accent;
        }
        ActiveTaskItem > .task-status {
            color: $text-muted;
        }
        """

        def __init__(
            self,
            task: DownloadTask,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise l'item.

            Args:
                task: Tâche de téléchargement.
                name: Nom du widget.
                id: ID du widget.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self.task = task

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(self.task.manga.title, classes="task-title")
            yield Static(
                f"Progress: {self.task.progress:.1%}",
                classes="task-progress",
            )
            yield Static(
                f"{self.task.status.icon} {self.task.status.label} • {self.task.pages_completed}/{self.task.pages_total} pages",
                classes="task-status",
            )

        def update_progress(self, progress: float) -> None:
            """Met à jour la progression.

            Args:
                progress: Nouvelle progression (0.0 à 1.0).
            """
            self.task.progress = progress
            progress_widget = self.query_one(".task-progress", Static)
            progress_widget.update(f"Progress: {progress:.1%}")


# ============================================================================
# MESSAGES — Événements Textual
# ============================================================================


if TEXTUAL_AVAILABLE:

    class DashboardRefreshRequested(Message):
        """Message émis lorsqu'un rafraîchissement du dashboard est demandé."""

    class DashboardRefreshed(Message):
        """Message émis lorsque le dashboard a été rafraîchi."""

        def __init__(self, state: DashboardState) -> None:
            """Initialise le message.

            Args:
                state: Nouvel état du dashboard.
            """
            super().__init__()
            self.state = state


# ============================================================================
# CLASSE PRINCIPALE — MainScreen
# ============================================================================


if TEXTUAL_AVAILABLE:

    class MainScreen(Screen):
        """Écran principal (dashboard) de l'application.

        Sert de hub central pour naviguer vers toutes les fonctionnalités
        et affiche un aperçu en temps réel de l'état du système.
        """

        # Bindings clavier
        BINDINGS = [
            Binding("s", "search", "Search"),
            Binding("l", "library", "Library"),
            Binding("d", "downloads", "Downloads"),
            Binding("p", "settings", "Settings"),
            Binding("r", "refresh", "Refresh"),
            Binding("q", "quit", "Quit"),
            Binding("1", "action_1", "Quick Action 1"),
            Binding("2", "action_2", "Quick Action 2"),
            Binding("3", "action_3", "Quick Action 3"),
        ]

        # CSS de l'écran
        DEFAULT_CSS = """
        MainScreen {
            layout: vertical;
        }

        #dashboard-container {
            height: 1fr;
            padding: 1;
        }

        #stats-section {
            height: 7;
            layout: horizontal;
            padding: 1;
        }

        #continue-reading-section {
            height: auto;
            max-height: 15;
            padding: 1;
            border-top: solid $primary;
        }

        #recent-downloads-section {
            height: auto;
            max-height: 15;
            padding: 1;
            border-top: solid $primary;
        }

        #active-tasks-section {
            height: auto;
            max-height: 15;
            padding: 1;
            border-top: solid $primary;
        }

        #quick-actions {
            height: 3;
            layout: horizontal;
            padding: 1;
            align: center middle;
        }

        #quick-actions > Button {
            margin: 0 1;
        }

        .section-title {
            text-style: bold;
            padding: 0 0 1 0;
        }

        .empty-message {
            text-align: center;
            padding: 2;
            color: $text-muted;
        }

        .loading-message {
            text-align: center;
            padding: 2;
            color: $text-muted;
        }
        """

        # État réactif
        dashboard_state: reactive[DashboardState] = reactive(DashboardState())

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise l'écran principal.

            Args:
                name: Nom de l'écran.
                id: ID de l'écran.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self._state = DashboardState()
            self._event_bus_subscription = None

        def compose(self) -> ComposeResult:
            """Compose l'écran."""
            yield Header(show_clock=True)

            with VerticalScroll(id="dashboard-container"):
                # Section Statistiques
                with Vertical(id="stats-section"):
                    yield Static(
                        f"{DashboardSection.STATISTICS.icon} {DashboardSection.STATISTICS.label}",
                        classes="section-title",
                    )
                    yield StatCard(
                        icon="📚",
                        value="0",
                        label=t("dashboard.stats.mangas", default="Mangas"),
                        id="stat-mangas",
                    )
                    yield StatCard(
                        icon="📄",
                        value="0",
                        label=t("dashboard.stats.chapters", default="Chapters"),
                        id="stat-chapters",
                    )
                    yield StatCard(
                        icon="💾",
                        value="0 B",
                        label=t("dashboard.stats.size", default="Library Size"),
                        id="stat-size",
                    )

                # Section Continue Reading
                with Vertical(id="continue-reading-section"):
                    yield Static(
                        f"{DashboardSection.CONTINUE_READING.icon} {DashboardSection.CONTINUE_READING.label}",
                        classes="section-title",
                    )
                    yield Static(
                        t("dashboard.loading", default="Loading..."),
                        id="continue-reading-placeholder",
                        classes="loading-message",
                    )
                    yield ListView(id="continue-reading-list")

                # Section Recent Downloads
                with Vertical(id="recent-downloads-section"):
                    yield Static(
                        f"{DashboardSection.RECENT_DOWNLOADS.icon} {DashboardSection.RECENT_DOWNLOADS.label}",
                        classes="section-title",
                    )
                    yield Static(
                        t("dashboard.loading", default="Loading..."),
                        id="recent-downloads-placeholder",
                        classes="loading-message",
                    )
                    yield ListView(id="recent-downloads-list")

                # Section Active Tasks
                with Vertical(id="active-tasks-section"):
                    yield Static(
                        f"{DashboardSection.ACTIVE_TASKS.icon} {DashboardSection.ACTIVE_TASKS.label}",
                        classes="section-title",
                    )
                    yield Static(
                        t("dashboard.loading", default="Loading..."),
                        id="active-tasks-placeholder",
                        classes="loading-message",
                    )
                    yield ListView(id="active-tasks-list")

                # Quick Actions
                with Horizontal(id="quick-actions"):
                    yield Button(
                        f"🔍 {t('dashboard.action.search', default='Search')}",
                        id="btn-search",
                        variant="primary",
                    )
                    yield Button(
                        f"📚 {t('dashboard.action.library', default='Library')}",
                        id="btn-library",
                        variant="default",
                    )
                    yield Button(
                        f"📥 {t('dashboard.action.downloads', default='Downloads')}",
                        id="btn-downloads",
                        variant="default",
                    )
                    yield Button(
                        f"⚙️ {t('dashboard.action.settings', default='Settings')}",
                        id="btn-settings",
                        variant="default",
                    )

            yield Footer()

        def on_mount(self) -> None:
            """Appelé lors du montage de l'écran."""
            # Charger le dashboard
            asyncio.create_task(self._load_dashboard())

            # S'abonner aux événements EventBus
            self._subscribe_to_events()

        def on_unmount(self) -> None:
            """Appelé lors du démontage de l'écran."""
            # Se désabonner des événements
            self._unsubscribe_from_events()

        def _subscribe_to_events(self) -> None:
            """S'abonne aux événements EventBus pour mises à jour temps réel."""
            try:
                event_bus = get_event_bus()

                # S'abonner aux événements de téléchargement
                self._event_bus_subscription = event_bus.on(
                    EventType.DOWNLOAD_TASK_COMPLETED,
                    self._on_download_completed,
                )
                event_bus.on(
                    EventType.DOWNLOAD_TASK_PROGRESS,
                    self._on_download_progress,
                )
                event_bus.on(
                    EventType.LIBRARY_MANGA_ADDED,
                    self._on_manga_added,
                )

                logger.debug("Abonné aux événements EventBus pour le dashboard")

            except Exception as e:
                logger.warning("Impossible de s'abonner aux événements EventBus: {}", e)

        def _unsubscribe_from_events(self) -> None:
            """Se désabonne des événements EventBus."""
            if self._event_bus_subscription is not None:
                try:
                    event_bus = get_event_bus()
                    event_bus.off(self._event_bus_subscription)
                    logger.debug("Désabonné des événements EventBus")
                except Exception as e:
                    logger.warning("Impossible de se désabonner des événements: {}", e)

        async def _on_download_completed(self, event: Any) -> None:
            """Gère l'événement de téléchargement terminé."""
            logger.debug("Téléchargement terminé, rafraîchissement du dashboard")
            await self._refresh_dashboard()

        async def _on_download_progress(self, event: Any) -> None:
            """Gère l'événement de progression de téléchargement."""
            # Mettre à jour la tâche active si présente
            task_id = event.payload.get("task_id")
            progress = event.payload.get("progress", 0.0)

            # Trouver et mettre à jour l'item dans la liste
            tasks_list = self.query_one("#active-tasks-list", ListView)
            for item in tasks_list.children:
                if isinstance(item, ActiveTaskItem) and item.task.id == task_id:
                    item.update_progress(progress)
                    break

        async def _on_manga_added(self, event: Any) -> None:
            """Gère l'événement d'ajout de manga."""
            logger.debug("Manga ajouté, rafraîchissement du dashboard")
            await self._refresh_dashboard()

        # =====================================================================
        # CHARGEMENT DU DASHBOARD
        # =====================================================================

        async def _load_dashboard(self) -> None:
            """Charge toutes les sections du dashboard."""
            self._state.loading = True
            self._show_loading()

            try:
                # Charger les statistiques
                await self._load_statistics()

                # Charger Continue Reading
                await self._load_continue_reading()

                # Charger Recent Downloads
                await self._load_recent_downloads()

                # Charger Active Tasks
                await self._load_active_tasks()

                self._state.loading = False
                self._state.error = None
                self.post_message(DashboardRefreshed(self._state))

                logger.info("Dashboard chargé avec succès")

            except Exception as e:
                logger.error("Erreur lors du chargement du dashboard: {}", e)
                self._state.loading = False
                self._state.error = str(e)
                self._show_error(str(e))

        async def _load_statistics(self) -> None:
            """Charge les statistiques globales."""
            try:
                # Obtenir les statistiques de la bibliothèque
                library_stats = await get_library_stats()

                # Obtenir les statistiques de téléchargement
                download_stats = await get_download_stats()

                # Construire l'objet DashboardStats
                self._state.stats = DashboardStats(
                    total_mangas=library_stats.total_manga,
                    total_chapters=library_stats.total_downloaded_chapters,
                    total_size_bytes=library_stats.total_size_bytes,
                    total_reading_time_seconds=library_stats.total_reading_time_seconds,
                    currently_reading=library_stats.currently_reading,
                    completed=library_stats.completed,
                    active_downloads=download_stats.running_tasks,
                    last_updated=datetime.now(UTC),
                )

                # Mettre à jour les cartes de statistiques
                self._update_stat_cards()

            except Exception as e:
                logger.warning("Impossible de charger les statistiques: {}", e)
                raise DashboardLoadError("statistics", str(e)) from e

        def _update_stat_cards(self) -> None:
            """Met à jour les cartes de statistiques."""
            stats = self._state.stats

            # Mettre à jour les valeurs
            mangas_card = self.query_one("#stat-mangas", StatCard)
            mangas_card.update_value(str(stats.total_mangas))

            chapters_card = self.query_one("#stat-chapters", StatCard)
            chapters_card.update_value(str(stats.total_chapters))

            size_card = self.query_one("#stat-size", StatCard)
            size_card.update_value(stats.total_size_human)

        async def _load_continue_reading(self) -> None:
            """Charge la liste des mangas en cours de lecture."""
            try:
                # Obtenir les mangas en cours de lecture
                from nexusdl.core.library import get_continue_reading
                entries = await get_continue_reading(limit=5)

                self._state.continue_reading = entries

                # Mettre à jour l'UI
                placeholder = self.query_one("#continue-reading-placeholder", Static)
                list_view = self.query_one("#continue-reading-list", ListView)

                if not entries:
                    placeholder.update(
                        t("dashboard.continue_reading.empty", default="No manga in progress")
                    )
                    placeholder.display = True
                    list_view.display = False
                else:
                    placeholder.display = False
                    list_view.display = True
                    list_view.clear()

                    for entry in entries:
                        item = ContinueReadingItem(entry)
                        list_view.append(item)

            except Exception as e:
                logger.warning("Impossible de charger Continue Reading: {}", e)
                placeholder = self.query_one("#continue-reading-placeholder", Static)
                placeholder.update(
                    t("dashboard.error", default="Error loading data")
                )
                placeholder.display = True

        async def _load_recent_downloads(self) -> None:
            """Charge la liste des téléchargements récents."""
            try:
                # Obtenir les téléchargements récents
                from nexusdl.core.downloader import get_recent_downloads
                tasks = await get_recent_downloads(limit=5)

                self._state.recent_downloads = tasks

                # Mettre à jour l'UI
                placeholder = self.query_one("#recent-downloads-placeholder", Static)
                list_view = self.query_one("#recent-downloads-list", ListView)

                if not tasks:
                    placeholder.update(
                        t("dashboard.recent_downloads.empty", default="No recent downloads")
                    )
                    placeholder.display = True
                    list_view.display = False
                else:
                    placeholder.display = False
                    list_view.display = True
                    list_view.clear()

                    for task in tasks:
                        item = RecentDownloadItem(task)
                        list_view.append(item)

            except Exception as e:
                logger.warning("Impossible de charger Recent Downloads: {}", e)
                placeholder = self.query_one("#recent-downloads-placeholder", Static)
                placeholder.update(
                    t("dashboard.error", default="Error loading data")
                )
                placeholder.display = True

        async def _load_active_tasks(self) -> None:
            """Charge la liste des tâches de téléchargement actives."""
            try:
                # Obtenir les tâches actives
                manager = get_download_manager()
                tasks = await manager.get_active_tasks()

                self._state.active_tasks = tasks

                # Mettre à jour l'UI
                placeholder = self.query_one("#active-tasks-placeholder", Static)
                list_view = self.query_one("#active-tasks-list", ListView)

                if not tasks:
                    placeholder.update(
                        t("dashboard.active_tasks.empty", default="No active downloads")
                    )
                    placeholder.display = True
                    list_view.display = False
                else:
                    placeholder.display = False
                    list_view.display = True
                    list_view.clear()

                    for task in tasks:
                        item = ActiveTaskItem(task)
                        list_view.append(item)

            except Exception as e:
                logger.warning("Impossible de charger Active Tasks: {}", e)
                placeholder = self.query_one("#active-tasks-placeholder", Static)
                placeholder.update(
                    t("dashboard.error", default="Error loading data")
                )
                placeholder.display = True

        def _show_loading(self) -> None:
            """Affiche les messages de chargement."""
            for section_id in [
                "#continue-reading-placeholder",
                "#recent-downloads-placeholder",
                "#active-tasks-placeholder",
            ]:
                placeholder = self.query_one(section_id, Static)
                placeholder.update(t("dashboard.loading", default="Loading..."))
                placeholder.display = True

        def _show_error(self, error: str) -> None:
            """Affiche un message d'erreur."""
            for section_id in [
                "#continue-reading-placeholder",
                "#recent-downloads-placeholder",
                "#active-tasks-placeholder",
            ]:
                placeholder = self.query_one(section_id, Static)
                placeholder.update(
                    t("dashboard.error", default="Error: {error}", error=error)
                )
                placeholder.display = True

        # =====================================================================
        # RAFRAÎCHISSEMENT
        # =====================================================================

        async def _refresh_dashboard(self) -> None:
            """Rafraîchit toutes les sections du dashboard."""
            logger.debug("Rafraîchissement du dashboard")
            await self._load_dashboard()

        # =====================================================================
        # GESTION DES ÉVÉNEMENTS
        # =====================================================================

        def on_button_pressed(self, event: Button.Pressed) -> None:
            """Gère les clics sur les boutons."""
            if event.button.id == "btn-search":
                self.action_search()
            elif event.button.id == "btn-library":
                self.action_library()
            elif event.button.id == "btn-downloads":
                self.action_downloads()
            elif event.button.id == "btn-settings":
                self.action_settings()

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            """Gère la sélection d'un item dans une liste."""
            # TODO: Ouvrir les détails du manga/téléchargement sélectionné
            logger.debug("Item sélectionné: {}", event.item)

        # =====================================================================
        # ACTIONS — Bindings clavier
        # =====================================================================

        def action_search(self) -> None:
            """Action : ouvrir l'écran de recherche."""
            from nexusdl.interfaces.cli.screens.search import SearchScreen
            self.app.push_screen(SearchScreen())

        def action_library(self) -> None:
            """Action : ouvrir l'écran de bibliothèque."""
            from nexusdl.interfaces.cli.screens.library import LibraryScreen
            self.app.push_screen(LibraryScreen())

        def action_downloads(self) -> None:
            """Action : ouvrir l'écran de téléchargements."""
            from nexusdl.interfaces.cli.screens.download import DownloadScreen
            self.app.push_screen(DownloadScreen())

        def action_settings(self) -> None:
            """Action : ouvrir l'écran de paramètres."""
            from nexusdl.interfaces.cli.screens.settings import SettingsScreen
            self.app.push_screen(SettingsScreen())

        def action_refresh(self) -> None:
            """Action : rafraîchir le dashboard."""
            asyncio.create_task(self._refresh_dashboard())
            self.notify(
                t("dashboard.notify.refreshing", default="Refreshing dashboard..."),
                severity="information",
            )

        def action_quit(self) -> None:
            """Action : quitter l'application."""
            self.app.exit()

        def action_action_1(self) -> None:
            """Action rapide 1 : recherche."""
            self.action_search()

        def action_action_2(self) -> None:
            """Action rapide 2 : bibliothèque."""
            self.action_library()

        def action_action_3(self) -> None:
            """Action rapide 3 : paramètres."""
            self.action_settings()


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "MainScreenError",
    "DashboardLoadError",
    # Enums
    "DashboardSection",
    # Modèles
    "DashboardStats",
    "DashboardState",
    # Écran principal
    "MainScreen" if TEXTUAL_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
