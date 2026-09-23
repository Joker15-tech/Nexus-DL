"""Widget de barre de progression pour l'interface CLI NexusDL.

Ce module fournit un widget Textual réutilisable pour afficher la progression
des téléchargements et autres opérations longues. Il est utilisé dans plusieurs
écrans :

    - DownloadScreen : progression des tâches de téléchargement
    - MainScreen : progression des tâches actives sur le dashboard
    - LibraryScreen : progression du scan de bibliothèque
    - SearchScreen : progression de la recherche multi-sites

**Fonctionnalités** :
    - Barre de progression visuelle avec remplissage animé
    - Affichage du pourcentage, vitesse, temps restant estimé (ETA)
    - Support de plusieurs états (running, paused, completed, failed, pending)
    - Mode compact (une seule ligne) ou détaillé (multi-lignes)
    - Indicateur de statut avec icônes et couleurs
    - Affichage de la taille téléchargée / taille totale
    - Affichage du nombre de pages/chapitres traités
    - Mise à jour automatique via callback ou polling
    - Animation de la barre de progression
    - Support de la progression indéterminée (spinner)
    - Configuration complète via Pydantic
    - Traductions i18n
    - Gestion des erreurs

**Architecture** :
    DownloadProgressBar (Widget principal)
        ├── ProgressHeader (titre + statut)
        ├── ProgressTrack (barre de progression visuelle)
        │   ├── ProgressFill (partie remplie)
        │   └── ProgressLabel (pourcentage au centre)
        ├── ProgressDetails (vitesse, ETA, taille)
        └── ProgressFooter (message d'état)

**Styles de barre** :
    - BLOCK   : Blocs pleins (████████░░░░)
    - LINE    : Ligne avec curseur (━━━━━━━━▶───)
    - DOTS    : Points (●●●●●○○○)
    - BRAILLE : Caractères Braille pour résolution fine
    - TEXT    : Texte simple ([=====>    ] 50%)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.cli.widgets.progress_bar import (
    ...     DownloadProgressBar, ProgressState, BarStyle,
    ... )
    >>>
    >>> # Dans un écran Textual
    >>> progress = DownloadProgressBar(
    ...     title="Downloading One Piece Ch.123",
    ...     total=42,  # 42 pages
    ...     style=BarStyle.BLOCK,
    ... )
    >>> self.mount(progress)
    >>>
    >>> # Mettre à jour la progression
    >>> progress.update_progress(current=21, speed=1024000)
    >>>
    >>> # Changer l'état
    >>> progress.set_state(ProgressState.COMPLETED)

Intégration :
    - core/models/download.py : modèles DownloadTask, DownloadStatus
    - core/events.py : abonnement aux événements de progression
    - core/i18n.py : traductions
    - core/utils/text.py : format_size
    - core/utils/time.py : format_duration
"""

from __future__ import annotations

import asyncio
import math
import time
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, ClassVar, Final

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

try:
    from textual.app import ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal, Vertical
    from textual.message import Message
    from textual.reactive import reactive
    from textual.widget import Widget
    from textual.widgets import Static
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False

from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t
from nexusdl.core.utils.text import format_size


# ============================================================================
# CONSTANTES
# ============================================================================


# Caractères pour les différents styles de barre
BAR_CHARS_BLOCK: Final[dict[str, str]] = {
    "full": "█",
    "empty": "░",
    "partial_1": "▏",
    "partial_2": "▎",
    "partial_3": "▍",
    "partial_4": "▌",
    "partial_5": "▋",
    "partial_6": "▊",
    "partial_7": "▉",
}

BAR_CHARS_LINE: Final[dict[str, str]] = {
    "full": "━",
    "empty": "─",
    "cursor": "▶",
}

BAR_CHARS_DOTS: Final[dict[str, str]] = {
    "full": "●",
    "empty": "○",
}

BAR_CHARS_BRAILLE: Final[list[str]] = [
    "⠀", "⡀", "⣀", "⣄", "⣤", "⣦", "⣶", "⣷", "⣿",
]

# Largeur par défaut de la barre
DEFAULT_BAR_WIDTH: Final[int] = 40

# Intervalle de mise à jour de l'animation (ms)
ANIMATION_INTERVAL_MS: Final[int] = 100

# Spinner frames pour la progression indéterminée
SPINNER_FRAMES: Final[list[str]] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

SPINNER_FRAMES_DOTS: Final[list[str]] = ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"]


# ============================================================================
# EXCEPTIONS
# ============================================================================


class ProgressBarError(NexusDLError):
    """Exception de base pour les erreurs de la barre de progression."""


class InvalidProgressError(ProgressBarError):
    """Exception levée lorsqu'une valeur de progression est invalide.

    Attributes:
        value: Valeur invalide.
        reason: Raison de l'invalidité.
    """

    def __init__(self, value: Any, reason: str = "") -> None:
        msg = f"Valeur de progression invalide: {value!r}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.value = value
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class ProgressState(str, Enum):
    """État de la progression.

    Attributes:
        PENDING: En attente de démarrage.
        RUNNING: En cours d'exécution.
        PAUSED: En pause.
        COMPLETED: Terminé avec succès.
        FAILED: Échoué.
        CANCELLED: Annulé.
        INDETERMINATE: Progression indéterminée (spinner).
    """

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INDETERMINATE = "indeterminate"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ProgressState.PENDING: t("progress.state.pending", default="Pending"),
            ProgressState.RUNNING: t("progress.state.running", default="Running"),
            ProgressState.PAUSED: t("progress.state.paused", default="Paused"),
            ProgressState.COMPLETED: t("progress.state.completed", default="Completed"),
            ProgressState.FAILED: t("progress.state.failed", default="Failed"),
            ProgressState.CANCELLED: t("progress.state.cancelled", default="Cancelled"),
            ProgressState.INDETERMINATE: t("progress.state.indeterminate", default="Processing"),
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            ProgressState.PENDING: "⏳",
            ProgressState.RUNNING: "⚡",
            ProgressState.PAUSED: "⏸️",
            ProgressState.COMPLETED: "✅",
            ProgressState.FAILED: "❌",
            ProgressState.CANCELLED: "🚫",
            ProgressState.INDETERMINATE: "🔄",
        }[self]

    @property
    def color(self) -> str:
        """Couleur CSS."""
        return {
            ProgressState.PENDING: "$text-muted",
            ProgressState.RUNNING: "$success",
            ProgressState.PAUSED: "$warning",
            ProgressState.COMPLETED: "$success",
            ProgressState.FAILED: "$error",
            ProgressState.CANCELLED: "$text-muted",
            ProgressState.INDETERMINATE: "$accent",
        }[self]

    @property
    def is_active(self) -> bool:
        """Indique si l'état est actif (en cours)."""
        return self in (ProgressState.RUNNING, ProgressState.INDETERMINATE)

    @property
    def is_terminal(self) -> bool:
        """Indique si l'état est terminal (fini)."""
        return self in (
            ProgressState.COMPLETED,
            ProgressState.FAILED,
            ProgressState.CANCELLED,
        )


class BarStyle(str, Enum):
    """Style visuel de la barre de progression.

    Attributes:
        BLOCK: Blocs pleins (████████░░░░).
        LINE: Ligne avec curseur (━━━━━━━━▶───).
        DOTS: Points (●●●●●○○○).
        BRAILLE: Caractères Braille pour résolution fine.
        TEXT: Texte simple ([=====>    ] 50%).
    """

    BLOCK = "block"
    LINE = "line"
    DOTS = "dots"
    BRAILLE = "braille"
    TEXT = "text"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            BarStyle.BLOCK: t("progress.style.block", default="Block"),
            BarStyle.LINE: t("progress.style.line", default="Line"),
            BarStyle.DOTS: t("progress.style.dots", default="Dots"),
            BarStyle.BRAILLE: t("progress.style.braille", default="Braille"),
            BarStyle.TEXT: t("progress.style.text", default="Text"),
        }[self]


class ProgressDisplayMode(str, Enum):
    """Mode d'affichage de la barre de progression.

    Attributes:
        COMPACT: Une seule ligne (barre + pourcentage).
        NORMAL: Barre + détails (vitesse, ETA).
        DETAILED: Multi-lignes avec toutes les informations.
        MINIMAL: Pourcentage seul.
    """

    COMPACT = "compact"
    NORMAL = "normal"
    DETAILED = "detailed"
    MINIMAL = "minimal"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ProgressDisplayMode.COMPACT: t("progress.display.compact", default="Compact"),
            ProgressDisplayMode.NORMAL: t("progress.display.normal", default="Normal"),
            ProgressDisplayMode.DETAILED: t("progress.display.detailed", default="Detailed"),
            ProgressDisplayMode.MINIMAL: t("progress.display.minimal", default="Minimal"),
        }[self]


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class ProgressBarConfig(BaseModel):
    """Configuration de la barre de progression.

    Attributes:
        style: Style visuel de la barre.
        display_mode: Mode d'affichage.
        bar_width: Largeur de la barre en caractères.
        show_percentage: Afficher le pourcentage.
        show_speed: Afficher la vitesse.
        show_eta: Afficher le temps restant estimé.
        show_size: Afficher la taille téléchargée/totale.
        show_count: Afficher le compteur (pages/chapitres).
        show_title: Afficher le titre.
        show_state: Afficher l'icône de statut.
        animate: Animer la barre (spinner pour indéterminé).
        animation_interval_ms: Intervalle d'animation en ms.
        unit_label: Libellé de l'unité (pages, chapitres, etc.).
        color_running: Couleur pour l'état running.
        color_completed: Couleur pour l'état completed.
        color_failed: Couleur pour l'état failed.
    """

    style: BarStyle = Field(
        default=BarStyle.BLOCK,
        description="Style visuel de la barre.",
    )
    display_mode: ProgressDisplayMode = Field(
        default=ProgressDisplayMode.NORMAL,
        description="Mode d'affichage.",
    )
    bar_width: int = Field(
        default=DEFAULT_BAR_WIDTH,
        ge=10,
        le=200,
        description="Largeur de la barre en caractères.",
    )
    show_percentage: bool = Field(
        default=True,
        description="Afficher le pourcentage.",
    )
    show_speed: bool = Field(
        default=True,
        description="Afficher la vitesse.",
    )
    show_eta: bool = Field(
        default=True,
        description="Afficher le temps restant estimé.",
    )
    show_size: bool = Field(
        default=False,
        description="Afficher la taille téléchargée/totale.",
    )
    show_count: bool = Field(
        default=False,
        description="Afficher le compteur.",
    )
    show_title: bool = Field(
        default=True,
        description="Afficher le titre.",
    )
    show_state: bool = Field(
        default=True,
        description="Afficher l'icône de statut.",
    )
    animate: bool = Field(
        default=True,
        description="Animer la barre.",
    )
    animation_interval_ms: int = Field(
        default=ANIMATION_INTERVAL_MS,
        ge=50,
        le=1000,
        description="Intervalle d'animation en ms.",
    )
    unit_label: str = Field(
        default="pages",
        description="Libellé de l'unité.",
    )
    color_running: str = Field(
        default="green",
        description="Couleur pour l'état running.",
    )
    color_completed: str = Field(
        default="bright_green",
        description="Couleur pour l'état completed.",
    )
    color_failed: str = Field(
        default="red",
        description="Couleur pour l'état failed.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class ProgressBarState(BaseModel):
    """État de la barre de progression.

    Attributes:
        current: Valeur actuelle de la progression.
        total: Valeur totale de la progression.
        state: État actuel.
        speed_bytes_per_sec: Vitesse actuelle en bytes/seconde.
        downloaded_bytes: Taille téléchargée en bytes.
        total_bytes: Taille totale en bytes.
        started_at: Timestamp de début.
        last_updated_at: Timestamp de dernière mise à jour.
        error_message: Message d'erreur (si state=FAILED).
    """

    current: float = Field(default=0.0, ge=0.0, description="Valeur actuelle.")
    total: float = Field(default=100.0, gt=0.0, description="Valeur totale.")
    state: ProgressState = Field(default=ProgressState.PENDING, description="État.")
    speed_bytes_per_sec: float = Field(default=0.0, ge=0.0, description="Vitesse.")
    downloaded_bytes: int = Field(default=0, ge=0, description="Taille téléchargée.")
    total_bytes: int = Field(default=0, ge=0, description="Taille totale.")
    started_at: datetime | None = Field(default=None, description="Début.")
    last_updated_at: datetime | None = Field(default=None, description="Dernière MAJ.")
    error_message: str | None = Field(default=None, description="Erreur.")

    model_config = ConfigDict(extra="forbid")

    @property
    def progress(self) -> float:
        """Progression en pourcentage (0.0 à 1.0)."""
        if self.total <= 0:
            return 0.0
        return min(1.0, self.current / self.total)

    @property
    def percentage(self) -> float:
        """Progression en pourcentage (0.0 à 100.0)."""
        return self.progress * 100.0

    @property
    def estimated_time_remaining(self) -> float:
        """Temps restant estimé en secondes."""
        if self.speed_bytes_per_sec <= 0:
            return 0.0
        remaining_bytes = self.total_bytes - self.downloaded_bytes
        if remaining_bytes <= 0:
            return 0.0
        return remaining_bytes / self.speed_bytes_per_sec

    @property
    def elapsed_seconds(self) -> float:
        """Temps écoulé en secondes."""
        if self.started_at is None:
            return 0.0
        ref = self.last_updated_at or datetime.now(UTC)
        return (ref - self.started_at).total_seconds()

    @property
    def speed_human(self) -> str:
        """Vitesse formatée."""
        return f"{format_size(int(self.speed_bytes_per_sec))}/s"

    @property
    def eta_human(self) -> str:
        """Temps restant formaté."""
        from datetime import timedelta
        from nexusdl.core.utils.time import format_duration

        eta = self.estimated_time_remaining
        if eta <= 0:
            return "--:--"
        return format_duration(timedelta(seconds=eta))

    @property
    def downloaded_human(self) -> str:
        """Taille téléchargée formatée."""
        return format_size(self.downloaded_bytes)

    @property
    def total_human(self) -> str:
        """Taille totale formatée."""
        return format_size(self.total_bytes)


# ============================================================================
# HELPERS — Rendu de la barre
# ============================================================================


def render_bar(
    progress: float,
    width: int,
    style: BarStyle,
) -> str:
    """Rend une barre de progression textuelle.

    Args:
        progress: Progression (0.0 à 1.0).
        width: Largeur de la barre en caractères.
        style: Style visuel.

    Returns:
        Chaîne représentant la barre.

    Example:
        >>> render_bar(0.5, 20, BarStyle.BLOCK)
        '██████████░░░░░░░░░░'
        >>> render_bar(0.75, 10, BarStyle.TEXT)
        '[======>  ]'
    """
    progress = max(0.0, min(1.0, progress))

    if style == BarStyle.BLOCK:
        return _render_block_bar(progress, width)
    elif style == BarStyle.LINE:
        return _render_line_bar(progress, width)
    elif style == BarStyle.DOTS:
        return _render_dots_bar(progress, width)
    elif style == BarStyle.BRAILLE:
        return _render_braille_bar(progress, width)
    elif style == BarStyle.TEXT:
        return _render_text_bar(progress, width)
    else:
        return _render_block_bar(progress, width)


def _render_block_bar(progress: float, width: int) -> str:
    """Rend une barre en style blocs."""
    filled_exact = progress * width
    filled_full = int(filled_exact)
    partial = filled_exact - filled_full

    chars = BAR_CHARS_BLOCK
    bar = chars["full"] * filled_full

    # Caractère partiel
    if partial > 0 and filled_full < width:
        partial_index = int(partial * 7) + 1
        partial_key = f"partial_{partial_index}"
        bar += chars.get(partial_key, chars["full"])
        bar += chars["empty"] * (width - filled_full - 1)
    else:
        bar += chars["empty"] * (width - filled_full)

    return bar


def _render_line_bar(progress: float, width: int) -> str:
    """Rend une barre en style ligne."""
    chars = BAR_CHARS_LINE
    filled = int(progress * (width - 1))

    bar = chars["full"] * filled
    if filled < width:
        bar += chars["cursor"]
        bar += chars["empty"] * (width - filled - 1)

    return bar


def _render_dots_bar(progress: float, width: int) -> str:
    """Rend une barre en style points."""
    chars = BAR_CHARS_DOTS
    filled = int(progress * width)

    bar = chars["full"] * filled
    bar += chars["empty"] * (width - filled)

    return bar


def _render_braille_bar(progress: float, width: int) -> str:
    """Rend une barre en style Braille (haute résolution)."""
    # Chaque caractère Braille représente 8 niveaux de remplissage
    total_segments = width * 8
    filled_segments = int(progress * total_segments)

    bar = ""
    for i in range(width):
        segment_start = i * 8
        segment_end = segment_start + 8
        filled_in_segment = max(0, min(8, filled_segments - segment_start))
        bar += BAR_CHARS_BRAILLE[filled_in_segment]

    return bar


def _render_text_bar(progress: float, width: int) -> str:
    """Rend une barre en style texte simple."""
    inner_width = width - 2  # Pour les crochets
    filled = int(progress * inner_width)

    bar = "["
    bar += "=" * filled
    if filled < inner_width:
        bar += ">"
        bar += " " * (inner_width - filled - 1)
    bar += "]"

    return bar


def render_spinner(frame_index: int, style: str = "default") -> str:
    """Rend un frame de spinner pour la progression indéterminée.

    Args:
        frame_index: Index du frame.
        style: Style du spinner.

    Returns:
        Caractère du spinner.
    """
    if style == "dots":
        frames = SPINNER_FRAMES_DOTS
    else:
        frames = SPINNER_FRAMES

    return frames[frame_index % len(frames)]


# ============================================================================
# WIDGETS CUSTOM — Composants de la barre
# ============================================================================


if TEXTUAL_AVAILABLE:

    class ProgressHeader(Widget):
        """En-tête de la barre de progression (titre + statut)."""

        DEFAULT_CSS = """
        ProgressHeader {
            layout: horizontal;
            height: 1;
        }
        ProgressHeader > .progress-icon {
            width: 3;
        }
        ProgressHeader > .progress-title {
            width: 1fr;
            text-style: bold;
        }
        ProgressHeader > .progress-percentage {
            width: 8;
            text-align: right;
            text-style: bold;
        }
        """

        def __init__(
            self,
            title: str = "",
            *,
            name: str | None = None,
            id: str | None = None,
        ) -> None:
            """Initialise l'en-tête."""
            super().__init__(name=name, id=id)
            self._title = title
            self._icon = ProgressState.PENDING.icon
            self._percentage = 0.0

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static(self._icon, id="progress-icon", classes="progress-icon")
            yield Static(self._title, id="progress-title", classes="progress-title")
            yield Static("0%", id="progress-percentage", classes="progress-percentage")

        def update_display(
            self,
            *,
            title: str | None = None,
            icon: str | None = None,
            percentage: float | None = None,
        ) -> None:
            """Met à jour l'affichage.

            Args:
                title: Nouveau titre.
                icon: Nouvelle icône.
                percentage: Nouveau pourcentage.
            """
            if title is not None:
                self._title = title
                self.query_one("#progress-title", Static).update(title)

            if icon is not None:
                self._icon = icon
                self.query_one("#progress-icon", Static).update(icon)

            if percentage is not None:
                self._percentage = percentage
                self.query_one("#progress-percentage", Static).update(
                    f"{percentage:.1f}%"
                )

    class ProgressTrack(Widget):
        """Barre de progression visuelle."""

        DEFAULT_CSS = """
        ProgressTrack {
            height: 1;
            width: 1fr;
        }
        ProgressTrack > .bar-fill {
            color: $success;
        }
        ProgressTrack > .bar-empty {
            color: $text-muted;
            opacity: 0.3;
        }
        """

        def __init__(
            self,
            *,
            width: int = DEFAULT_BAR_WIDTH,
            style: BarStyle = BarStyle.BLOCK,
            name: str | None = None,
            id: str | None = None,
        ) -> None:
            """Initialise la barre."""
            super().__init__(name=name, id=id)
            self._width = width
            self._style = style
            self._progress = 0.0
            self._spinner_frame = 0

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            bar = render_bar(0.0, self._width, self._style)
            yield Static(bar, id="bar-display")

        def update_progress(self, progress: float) -> None:
            """Met à jour la progression.

            Args:
                progress: Progression (0.0 à 1.0).
            """
            self._progress = max(0.0, min(1.0, progress))
            bar = render_bar(self._progress, self._width, self._style)
            self.query_one("#bar-display", Static).update(bar)

        def update_spinner(self) -> None:
            """Met à jour le spinner (progression indéterminée)."""
            self._spinner_frame += 1
            spinner = render_spinner(self._spinner_frame)
            bar = f"{spinner} " + render_bar(0.0, self._width - 2, self._style)
            self.query_one("#bar-display", Static).update(bar)

    class ProgressDetails(Widget):
        """Détails de la progression (vitesse, ETA, taille)."""

        DEFAULT_CSS = """
        ProgressDetails {
            layout: horizontal;
            height: 1;
        }
        ProgressDetails > .detail-item {
            padding: 0 1;
            color: $text-muted;
        }
        """

        def __init__(
            self,
            *,
            name: str | None = None,
            id: str | None = None,
        ) -> None:
            """Initialise les détails."""
            super().__init__(name=name, id=id)

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            yield Static("", id="detail-speed", classes="detail-item")
            yield Static("", id="detail-eta", classes="detail-item")
            yield Static("", id="detail-size", classes="detail-item")
            yield Static("", id="detail-count", classes="detail-item")

        def update_display(
            self,
            *,
            speed: str = "",
            eta: str = "",
            size: str = "",
            count: str = "",
        ) -> None:
            """Met à jour l'affichage des détails.

            Args:
                speed: Vitesse formatée.
                eta: Temps restant formaté.
                size: Taille formatée.
                count: Compteur formaté.
            """
            self.query_one("#detail-speed", Static).update(
                f"⚡ {speed}" if speed else ""
            )
            self.query_one("#detail-eta", Static).update(
                f"⏱️ {eta}" if eta else ""
            )
            self.query_one("#detail-size", Static).update(
                f"💾 {size}" if size else ""
            )
            self.query_one("#detail-count", Static).update(
                f"📄 {count}" if count else ""
            )


# ============================================================================
# MESSAGES — Événements Textual
# ============================================================================


if TEXTUAL_AVAILABLE:

    class ProgressUpdated(Message):
        """Message émis lorsque la progression est mise à jour."""

        def __init__(self, state: ProgressBarState) -> None:
            """Initialise le message.

            Args:
                state: Nouvel état de la progression.
            """
            super().__init__()
            self.state = state

    class ProgressCompleted(Message):
        """Message émis lorsque la progression est terminée."""

        def __init__(self, state: ProgressBarState) -> None:
            """Initialise le message.

            Args:
                state: État final.
            """
            super().__init__()
            self.state = state

    class ProgressFailed(Message):
        """Message émis lorsque la progression échoue."""

        def __init__(self, state: ProgressBarState) -> None:
            """Initialise le message.

            Args:
                state: État final.
            """
            super().__init__()
            self.state = state


# ============================================================================
# CLASSE PRINCIPALE — DownloadProgressBar
# ============================================================================


if TEXTUAL_AVAILABLE:

    class DownloadProgressBar(Widget):
        """Widget de barre de progression pour les téléchargements.

        Widget réutilisable affichant la progression d'une opération
        avec barre visuelle, pourcentage, vitesse, ETA, et état.
        """

        # CSS du widget
        DEFAULT_CSS = """
        DownloadProgressBar {
            height: auto;
            layout: vertical;
            padding: 0 1;
        }
        DownloadProgressBar.compact {
            height: 1;
            layout: horizontal;
        }
        DownloadProgressBar.minimal {
            height: 1;
            layout: horizontal;
        }
        DownloadProgressBar > #progress-header {
            height: auto;
        }
        DownloadProgressBar > #progress-track {
            height: 1;
        }
        DownloadProgressBar > #progress-details {
            height: auto;
        }
        DownloadProgressBar > #progress-status {
            height: 1;
            color: $text-muted;
        }
        DownloadProgressBar.state-completed > #progress-track > .bar-fill {
            color: $success;
        }
        DownloadProgressBar.state-failed > #progress-track > .bar-fill {
            color: $error;
        }
        DownloadProgressBar.state-paused > #progress-track > .bar-fill {
            color: $warning;
        }
        """

        # État réactif
        progress: reactive[float] = reactive(0.0)

        def __init__(
            self,
            *,
            title: str = "",
            total: float = 100.0,
            config: ProgressBarConfig | None = None,
            on_completed: Callable[[], None] | None = None,
            on_failed: Callable[[str], None] | None = None,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise la barre de progression.

            Args:
                title: Titre de la barre.
                total: Valeur totale de la progression.
                config: Configuration de la barre.
                on_completed: Callback lorsque terminé.
                on_failed: Callback en cas d'échec.
                name: Nom du widget.
                id: ID du widget.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)
            self._title = title
            self._config = config or ProgressBarConfig()
            self._state = ProgressBarState(total=total)
            self._on_completed = on_completed
            self._on_failed = on_failed
            self._animation_task: asyncio.Task[None] | None = None

            # Ajouter la classe de mode d'affichage
            if self._config.display_mode == ProgressDisplayMode.COMPACT:
                self.add_class("compact")
            elif self._config.display_mode == ProgressDisplayMode.MINIMAL:
                self.add_class("minimal")

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            mode = self._config.display_mode

            # Header (titre + pourcentage)
            if mode in (ProgressDisplayMode.NORMAL, ProgressDisplayMode.DETAILED):
                if self._config.show_title or self._config.show_percentage:
                    yield ProgressHeader(
                        title=self._title,
                        id="progress-header",
                    )

            # Barre de progression
            if mode != ProgressDisplayMode.MINIMAL:
                yield ProgressTrack(
                    width=self._config.bar_width,
                    style=self._config.style,
                    id="progress-track",
                )

            # Détails (vitesse, ETA, taille)
            if mode in (ProgressDisplayMode.NORMAL, ProgressDisplayMode.DETAILED):
                yield ProgressDetails(id="progress-details")

            # Message de statut
            if mode == ProgressDisplayMode.DETAILED:
                yield Static("", id="progress-status")

            # Mode compact : tout sur une ligne
            if mode == ProgressDisplayMode.COMPACT:
                yield Static("", id="compact-display")

            # Mode minimal : pourcentage seul
            if mode == ProgressDisplayMode.MINIMAL:
                yield Static("0%", id="minimal-display")

        async def on_mount(self) -> None:
            """Appelé lors du montage."""
            # Démarrer l'animation si configuré
            if self._config.animate:
                self._animation_task = asyncio.create_task(
                    self._animation_loop(),
                    name=f"progress_animation_{self.id}",
                )

        async def on_unmount(self) -> None:
            """Appelé lors du démontage."""
            if self._animation_task is not None:
                self._animation_task.cancel()
                try:
                    await self._animation_task
                except asyncio.CancelledError:
                    pass
                self._animation_task = None

        # =====================================================================
        # API PUBLIQUE — Mise à jour
        # =====================================================================

        def update_progress(
            self,
            current: float | None = None,
            *,
            speed: float = 0.0,
            downloaded_bytes: int = 0,
            total_bytes: int = 0,
        ) -> None:
            """Met à jour la progression.

            Args:
                current: Valeur actuelle (None = ne pas changer).
                speed: Vitesse en bytes/seconde.
                downloaded_bytes: Taille téléchargée.
                total_bytes: Taille totale.
            """
            if current is not None:
                if current < 0 or current > self._state.total:
                    raise InvalidProgressError(
                        current,
                        f"Doit être entre 0 et {self._state.total}",
                    )
                self._state.current = current

            self._state.speed_bytes_per_sec = speed
            self._state.downloaded_bytes = downloaded_bytes
            self._state.total_bytes = total_bytes
            self._state.last_updated_at = datetime.now(UTC)

            if self._state.started_at is None:
                self._state.started_at = datetime.now(UTC)

            # Mettre à jour l'état si en cours
            if self._state.state == ProgressState.PENDING:
                self._state.state = ProgressState.RUNNING

            # Vérifier si terminé
            if self._state.current >= self._state.total:
                self._state.state = ProgressState.COMPLETED

            # Mettre à jour l'UI
            self._update_display()

            # Émettre un message
            self.post_message(ProgressUpdated(self._state))

            # Vérifier si terminé
            if self._state.state == ProgressState.COMPLETED:
                self.post_message(ProgressCompleted(self._state))
                if self._on_completed is not None:
                    try:
                        self._on_completed()
                    except Exception as e:
                        logger.error("Erreur dans le callback completed: {}", e)

        def set_state(self, state: ProgressState, *, error_message: str = "") -> None:
            """Définit l'état de la progression.

            Args:
                state: Nouvel état.
                error_message: Message d'erreur (si FAILED).
            """
            old_state = self._state.state
            self._state.state = state

            if error_message:
                self._state.error_message = error_message

            # Retirer les anciennes classes d'état
            for s in ProgressState:
                self.remove_class(f"state-{s.value}")

            # Ajouter la nouvelle classe
            self.add_class(f"state-{state.value}")

            # Mettre à jour l'UI
            self._update_display()

            # Émettre des messages pour les états terminaux
            if state == ProgressState.COMPLETED:
                self.post_message(ProgressCompleted(self._state))
                if self._on_completed is not None:
                    try:
                        self._on_completed()
                    except Exception as e:
                        logger.error("Erreur dans le callback completed: {}", e)

            elif state == ProgressState.FAILED:
                self.post_message(ProgressFailed(self._state))
                if self._on_failed is not None:
                    try:
                        self._on_failed(error_message)
                    except Exception as e:
                        logger.error("Erreur dans le callback failed: {}", e)

        def set_title(self, title: str) -> None:
            """Définit le titre de la barre.

            Args:
                title: Nouveau titre.
            """
            self._title = title
            self._update_display()

        def reset(self) -> None:
            """Réinitialise la barre de progression."""
            self._state = ProgressBarState(total=self._state.total)
            self._update_display()

        # =====================================================================
        # API PUBLIQUE — Accès aux données
        # =====================================================================

        @property
        def state(self) -> ProgressBarState:
            """État actuel de la progression."""
            return self._state

        @property
        def config(self) -> ProgressBarConfig:
            """Configuration de la barre."""
            return self._config

        @property
        def is_completed(self) -> bool:
            """Indique si la progression est terminée."""
            return self._state.state == ProgressState.COMPLETED

        @property
        def is_failed(self) -> bool:
            """Indique si la progression a échoué."""
            return self._state.state == ProgressState.FAILED

        @property
        def is_active(self) -> bool:
            """Indique si la progression est active."""
            return self._state.state.is_active

        @property
        def percentage(self) -> float:
            """Progression en pourcentage (0.0 à 100.0)."""
            return self._state.percentage

        # =====================================================================
        # MISE À JOUR DE L'AFFICHAGE
        # =====================================================================

        def _update_display(self) -> None:
            """Met à jour tout l'affichage."""
            mode = self._config.display_mode
            state = self._state

            if mode == ProgressDisplayMode.MINIMAL:
                self._update_minimal()
            elif mode == ProgressDisplayMode.COMPACT:
                self._update_compact()
            elif mode == ProgressDisplayMode.NORMAL:
                self._update_normal()
            elif mode == ProgressDisplayMode.DETAILED:
                self._update_detailed()

        def _update_minimal(self) -> None:
            """Met à jour l'affichage minimal (pourcentage seul)."""
            try:
                display = self.query_one("#minimal-display", Static)
                display.update(f"{self._state.percentage:.0f}%")
            except Exception:
                pass

        def _update_compact(self) -> None:
            """Met à jour l'affichage compact (une ligne)."""
            try:
                display = self.query_one("#compact-display", Static)

                icon = self._state.state.icon if self._config.show_state else ""
                bar = render_bar(
                    self._state.progress,
                    self._config.bar_width,
                    self._config.style,
                )
                pct = f"{self._state.percentage:.1f}%" if self._config.show_percentage else ""

                parts = [p for p in [icon, bar, pct] if p]
                display.update(" ".join(parts))
            except Exception:
                pass

        def _update_normal(self) -> None:
            """Met à jour l'affichage normal."""
            # Header
            if self._config.show_title or self._config.show_percentage:
                try:
                    header = self.query_one("#progress-header", ProgressHeader)
                    header.update_display(
                        title=self._title if self._config.show_title else None,
                        icon=self._state.state.icon if self._config.show_state else None,
                        percentage=self._state.percentage if self._config.show_percentage else None,
                    )
                except Exception:
                    pass

            # Barre
            try:
                track = self.query_one("#progress-track", ProgressTrack)
                if self._state.state == ProgressState.INDETERMINATE:
                    track.update_spinner()
                else:
                    track.update_progress(self._state.progress)
            except Exception:
                pass

            # Détails
            try:
                details = self.query_one("#progress-details", ProgressDetails)
                details.update_display(
                    speed=self._state.speed_human if self._config.show_speed else "",
                    eta=self._state.eta_human if self._config.show_eta else "",
                    size=(
                        f"{self._state.downloaded_human}/{self._state.total_human}"
                        if self._config.show_size
                        else ""
                    ),
                    count=(
                        f"{int(self._state.current)}/{int(self._state.total)} {self._config.unit_label}"
                        if self._config.show_count
                        else ""
                    ),
                )
            except Exception:
                pass

        def _update_detailed(self) -> None:
            """Met à jour l'affichage détaillé."""
            # D'abord mettre à jour le mode normal
            self._update_normal()

            # Puis le message de statut
            try:
                status = self.query_one("#progress-status", Static)
                status_text = f"{self._state.state.icon} {self._state.state.label}"
                if self._state.error_message:
                    status_text += f": {self._state.error_message}"
                status.update(status_text)
            except Exception:
                pass

        # =====================================================================
        # ANIMATION
        # =====================================================================

        async def _animation_loop(self) -> None:
            """Boucle d'animation pour le spinner."""
            try:
                while True:
                    await asyncio.sleep(self._config.animation_interval_ms / 1000)

                    if self._state.state == ProgressState.INDETERMINATE:
                        try:
                            track = self.query_one("#progress-track", ProgressTrack)
                            track.update_spinner()
                        except Exception:
                            pass

                    # Arrêter l'animation si l'état est terminal
                    if self._state.state.is_terminal:
                        break

            except asyncio.CancelledError:
                pass


# ============================================================================
# HELPERS PUBLICS
# ============================================================================


def create_progress_bar(
    title: str = "",
    total: float = 100.0,
    *,
    style: BarStyle = BarStyle.BLOCK,
    display_mode: ProgressDisplayMode = ProgressDisplayMode.NORMAL,
    bar_width: int = DEFAULT_BAR_WIDTH,
    **kwargs: Any,
) -> Any:
    """Crée une barre de progression avec une configuration simplifiée.

    Fonction utilitaire pour créer rapidement une DownloadProgressBar.

    Args:
        title: Titre de la barre.
        total: Valeur totale.
        style: Style visuel.
        display_mode: Mode d'affichage.
        bar_width: Largeur de la barre.
        **kwargs: Arguments additionnels.

    Returns:
        Instance de DownloadProgressBar.
    """
    config = ProgressBarConfig(
        style=style,
        display_mode=display_mode,
        bar_width=bar_width,
        **kwargs,
    )
    return DownloadProgressBar(
        title=title,
        total=total,
        config=config,
    )


def format_progress_line(
    current: float,
    total: float,
    *,
    width: int = 40,
    style: BarStyle = BarStyle.BLOCK,
    show_percentage: bool = True,
    prefix: str = "",
) -> str:
    """Formate une ligne de progression (pour usage hors TUI).

    Args:
        current: Valeur actuelle.
        total: Valeur totale.
        width: Largeur de la barre.
        style: Style visuel.
        show_percentage: Afficher le pourcentage.
        prefix: Préfixe à ajouter.

    Returns:
        Chaîne formatée.

    Example:
        >>> format_progress_line(50, 100, width=20)
        '██████████░░░░░░░░░░ 50.0%'
    """
    progress = current / total if total > 0 else 0.0
    bar = render_bar(progress, width, style)

    parts = []
    if prefix:
        parts.append(prefix)
    parts.append(bar)
    if show_percentage:
        parts.append(f"{progress * 100:.1f}%")

    return " ".join(parts)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_BAR_WIDTH",
    "ANIMATION_INTERVAL_MS",
    "SPINNER_FRAMES",
    "SPINNER_FRAMES_DOTS",
    # Exceptions
    "ProgressBarError",
    "InvalidProgressError",
    # Enums
    "ProgressState",
    "BarStyle",
    "ProgressDisplayMode",
    # Modèles
    "ProgressBarConfig",
    "ProgressBarState",
    # Helpers de rendu
    "render_bar",
    "render_spinner",
    "format_progress_line",
    "create_progress_bar",
    # Widget principal
    "DownloadProgressBar" if TEXTUAL_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
