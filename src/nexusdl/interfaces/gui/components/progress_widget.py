"""Composant GUI de widget de progression pour NexusDL.

Ce module fournit un widget PyQt6 personnalisé pour afficher la progression
des téléchargements et autres opérations longues. Il offre une expérience
visuelle riche avec animations néon, gradients, et effets cyberpunk.

**Fonctionnalités** :
    - Barre de progression custom avec animations néon
    - 4 styles : GRADIENT (animé), SOLID, PULSING, NEON (glow)
    - 3 modes d'affichage : COMPACT, NORMAL, DETAILED
    - 7 états : PENDING, RUNNING, PAUSED, COMPLETED, FAILED, CANCELLED, INDETERMINATE
    - Affichage du pourcentage, vitesse, ETA, taille
    - Indicateur de statut avec icônes et couleurs
    - Animation de gradient qui se déplace (effet cyberpunk)
    - Effet de glow néon sur la barre
    - Support de la progression indéterminée (spinner animé)
    - Mise à jour fluide via QTimer
    - Signaux Qt pour communication
    - Intégration avec DownloadTask
    - Configuration complète via ProgressConfig
    - Style cyberpunk néon cohérent

**Architecture** :
    ProgressWidget (QWidget principal)
        ├── ProgressBar (QFrame custom avec QPainter)
        │   ├── Gradient animé
        │   ├── Effet glow néon
        │   └── Texte du pourcentage
        ├── ProgressInfo (panneau d'informations)
        │   ├── Vitesse
        │   ├── ETA
        │   ├── Taille téléchargée/totale
        │   └── Nombre de pages
        ├── ProgressStatus (indicateur de statut)
        │   ├── Icône
        │   └── Label
        └── ProgressAnimation (QTimer pour animations)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.gui.components.progress_widget import (
    ...     ProgressWidget, ProgressState, BarStyle,
    ... )
    >>>
    >>> # Dans une fenêtre PyQt6
    >>> progress = ProgressWidget(
    ...     title="Downloading One Piece Ch.123",
    ...     style=BarStyle.GRADIENT,
    ...     parent=self,
    ... )
    >>> progress.completed.connect(self.on_download_complete)
    >>> layout.addWidget(progress)
    >>>
    >>> # Mettre à jour la progression
    >>> progress.set_progress(current=21, total=42)
    >>> progress.set_speed(1024000)  # 1 MB/s
    >>> progress.set_state(ProgressState.RUNNING)

Intégration :
    - core/models/download.py : modèle DownloadTask
    - core/events.py : abonnement aux événements de progression
    - core/i18n.py : traductions
    - core/utils/text.py : format_size
    - core/utils/time.py : format_duration
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Final

from loguru import logger

try:
    from PyQt6.QtCore import (
        QEasingCurve,
        QPoint,
        QPointF,
        QRect,
        QRectF,
        QSize,
        Qt,
        QTimer,
        pyqtSignal,
        pyqtSlot,
    )
    from PyQt6.QtGui import (
        QBrush,
        QColor,
        QConicalGradient,
        QFont,
        QLinearGradient,
        QPaintEvent,
        QPainter,
        QPen,
        QRadialGradient,
    )
    from PyQt6.QtWidgets import (
        QFrame,
        QHBoxLayout,
        QLabel,
        QSizePolicy,
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
COLOR_PRIMARY_GLOW: Final[str] = "#39ff14"
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
COLOR_TEXT: Final[str] = "#00ff41"
COLOR_TEXT_BRIGHT: Final[str] = "#ffffff"
COLOR_TEXT_MUTED: Final[str] = "#00aaaa"
COLOR_TEXT_DIM: Final[str] = "#006666"
COLOR_BORDER: Final[str] = "#00ff41"
COLOR_BORDER_DIM: Final[str] = "#006622"

# Dimensions
BAR_HEIGHT: Final[int] = 24
BAR_BORDER_RADIUS: Final[int] = 4
WIDGET_MIN_WIDTH: Final[int] = 300
WIDGET_MAX_WIDTH: Final[int] = 600

# Animations
ANIMATION_INTERVAL_MS: Final[int] = 50
GRADIENT_SPEED: Final[float] = 0.02
PULSE_SPEED: Final[float] = 0.05
GLOW_INTENSITY: Final[float] = 0.6

# Couleurs par état
STATE_COLORS: Final[dict[str, str]] = {
    "pending": COLOR_TEXT_DIM,
    "running": COLOR_PRIMARY,
    "paused": COLOR_WARNING,
    "completed": COLOR_SUCCESS,
    "failed": COLOR_ERROR,
    "cancelled": COLOR_TEXT_DIM,
    "indeterminate": COLOR_SECONDARY,
}

# Icônes par état
STATE_ICONS: Final[dict[str, str]] = {
    "pending": "⏳",
    "running": "⚡",
    "paused": "⏸️",
    "completed": "✅",
    "failed": "❌",
    "cancelled": "🚫",
    "indeterminate": "🔄",
}


# ============================================================================
# EXCEPTIONS
# ============================================================================


class ProgressWidgetError(NexusDLError):
    """Exception de base pour les erreurs du widget de progression."""


class InvalidProgressError(ProgressWidgetError):
    """Exception levée lorsqu'une valeur de progression est invalide."""

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
        INDETERMINATE: Progression indéterminée.
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
        return STATE_ICONS[self.value]

    @property
    def color(self) -> str:
        """Couleur hex."""
        return STATE_COLORS[self.value]

    @property
    def is_active(self) -> bool:
        """Indique si l'état est actif."""
        return self in (ProgressState.RUNNING, ProgressState.INDETERMINATE)

    @property
    def is_terminal(self) -> bool:
        """Indique si l'état est terminal."""
        return self in (
            ProgressState.COMPLETED,
            ProgressState.FAILED,
            ProgressState.CANCELLED,
        )


class BarStyle(str, Enum):
    """Style de la barre de progression.

    Attributes:
        GRADIENT: Gradient animé (défaut).
        SOLID: Couleur unie.
        PULSING: Pulsation.
        NEON: Effet néon avec glow.
    """

    GRADIENT = "gradient"
    SOLID = "solid"
    PULSING = "pulsing"
    NEON = "neon"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            BarStyle.GRADIENT: t("progress.style.gradient", default="Gradient"),
            BarStyle.SOLID: t("progress.style.solid", default="Solid"),
            BarStyle.PULSING: t("progress.style.pulsing", default="Pulsing"),
            BarStyle.NEON: t("progress.style.neon", default="Neon"),
        }[self]


class DisplayMode(str, Enum):
    """Mode d'affichage du widget.

    Attributes:
        COMPACT: Barre seule avec pourcentage.
        NORMAL: Barre + informations de base.
        DETAILED: Barre + toutes les informations.
    """

    COMPACT = "compact"
    NORMAL = "normal"
    DETAILED = "detailed"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            DisplayMode.COMPACT: t("progress.display.compact", default="Compact"),
            DisplayMode.NORMAL: t("progress.display.normal", default="Normal"),
            DisplayMode.DETAILED: t("progress.display.detailed", default="Detailed"),
        }[self]


# ============================================================================
# MODÈLES DE DONNÉES
# ============================================================================


class ProgressConfig:
    """Configuration du widget de progression.

    Attributes:
        style: Style de la barre.
        mode: Mode d'affichage.
        show_percentage: Afficher le pourcentage.
        show_speed: Afficher la vitesse.
        show_eta: Afficher l'ETA.
        show_size: Afficher la taille.
        show_count: Afficher le compteur.
        show_status: Afficher le statut.
        animate: Activer les animations.
        animation_speed: Vitesse de l'animation.
        bar_height: Hauteur de la barre.
        border_radius: Rayon des bordures.
    """

    def __init__(
        self,
        *,
        style: BarStyle = BarStyle.GRADIENT,
        mode: DisplayMode = DisplayMode.NORMAL,
        show_percentage: bool = True,
        show_speed: bool = True,
        show_eta: bool = True,
        show_size: bool = False,
        show_count: bool = False,
        show_status: bool = True,
        animate: bool = True,
        animation_speed: float = GRADIENT_SPEED,
        bar_height: int = BAR_HEIGHT,
        border_radius: int = BAR_BORDER_RADIUS,
    ) -> None:
        """Initialise la configuration."""
        self.style = style
        self.mode = mode
        self.show_percentage = show_percentage
        self.show_speed = show_speed
        self.show_eta = show_eta
        self.show_size = show_size
        self.show_count = show_count
        self.show_status = show_status
        self.animate = animate
        self.animation_speed = animation_speed
        self.bar_height = bar_height
        self.border_radius = border_radius


class ProgressData:
    """Données de progression.

    Attributes:
        current: Valeur actuelle.
        total: Valeur totale.
        state: État actuel.
        speed_bytes_per_sec: Vitesse en bytes/seconde.
        downloaded_bytes: Taille téléchargée.
        total_bytes: Taille totale.
        started_at: Timestamp de début.
        error_message: Message d'erreur.
    """

    def __init__(
        self,
        *,
        current: float = 0.0,
        total: float = 100.0,
        state: ProgressState = ProgressState.PENDING,
        speed_bytes_per_sec: float = 0.0,
        downloaded_bytes: int = 0,
        total_bytes: int = 0,
        started_at: datetime | None = None,
        error_message: str = "",
    ) -> None:
        """Initialise les données."""
        self.current = current
        self.total = total
        self.state = state
        self.speed_bytes_per_sec = speed_bytes_per_sec
        self.downloaded_bytes = downloaded_bytes
        self.total_bytes = total_bytes
        self.started_at = started_at
        self.error_message = error_message

    @property
    def progress(self) -> float:
        """Progression (0.0 à 1.0)."""
        if self.total <= 0:
            return 0.0
        return min(1.0, max(0.0, self.current / self.total))

    @property
    def percentage(self) -> float:
        """Progression en pourcentage (0.0 à 100.0)."""
        return self.progress * 100.0

    @property
    def eta_seconds(self) -> float:
        """Temps restant estimé en secondes."""
        if self.speed_bytes_per_sec <= 0:
            return 0.0
        remaining = self.total_bytes - self.downloaded_bytes
        if remaining <= 0:
            return 0.0
        return remaining / self.speed_bytes_per_sec

    @property
    def speed_human(self) -> str:
        """Vitesse formatée."""
        from nexusdl.core.utils.text import format_size
        return f"{format_size(int(self.speed_bytes_per_sec))}/s"

    @property
    def eta_human(self) -> str:
        """ETA formaté."""
        from nexusdl.core.utils.time import format_duration
        if self.eta_seconds <= 0:
            return "--:--"
        return format_duration(timedelta(seconds=self.eta_seconds))

    @property
    def downloaded_human(self) -> str:
        """Taille téléchargée formatée."""
        from nexusdl.core.utils.text import format_size
        return format_size(self.downloaded_bytes)

    @property
    def total_human(self) -> str:
        """Taille totale formatée."""
        from nexusdl.core.utils.text import format_size
        return format_size(self.total_bytes)


# ============================================================================
# WIDGETS — Composants PyQt6
# ============================================================================


if PYQT6_AVAILABLE:

    class ProgressBar(QFrame):
        """Barre de progression custom avec animations néon.

        Dessinée avec QPainter pour un contrôle total sur le rendu.
        Supporte 4 styles : GRADIENT, SOLID, PULSING, NEON.
        """

        def __init__(
            self,
            *,
            config: ProgressConfig,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la barre.

            Args:
                config: Configuration.
                parent: Widget parent.
            """
            super().__init__(parent)
            self._config = config
            self._data = ProgressData()
            self._animation_offset = 0.0
            self._pulse_phase = 0.0

            # Configuration visuelle
            self.setFixedHeight(config.bar_height)
            self.setMinimumWidth(WIDGET_MIN_WIDTH)
            self.setMaximumWidth(WIDGET_MAX_WIDTH)
            self.setStyleSheet(f"""
                ProgressBar {{
                    background-color: {COLOR_SURFACE_ALT};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: {config.border_radius}px;
                }}
            """)

            # Timer pour animations
            if config.animate:
                self._animation_timer = QTimer(self)
                self._animation_timer.timeout.connect(self._update_animation)
                self._animation_timer.start(ANIMATION_INTERVAL_MS)

        def set_data(self, data: ProgressData) -> None:
            """Définit les données de progression.

            Args:
                data: Données de progression.
            """
            self._data = data
            self.update()

        def _update_animation(self) -> None:
            """Met à jour l'animation."""
            if self._config.style == BarStyle.GRADIENT:
                self._animation_offset += self._config.animation_speed
                if self._animation_offset >= 1.0:
                    self._animation_offset = 0.0
            elif self._config.style == BarStyle.PULSING:
                self._pulse_phase += 0.05
                if self._pulse_phase >= 2 * math.pi:
                    self._pulse_phase = 0.0
            self.update()

        def paintEvent(self, event: QPaintEvent) -> None:
            """Dessine la barre de progression."""
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            width = self.width()
            height = self.height()
            progress = self._data.progress

            # Couleur de base selon l'état
            state_color = QColor(self._data.state.color)

            # Dessiner la barre de fond
            bg_rect = QRectF(0, 0, width, height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(COLOR_SURFACE_ALT)))
            painter.drawRoundedRect(bg_rect, self._config.border_radius, self._config.border_radius)

            # Dessiner la barre de progression
            if progress > 0 or self._data.state == ProgressState.INDETERMINATE:
                fill_width = width * progress

                if self._data.state == ProgressState.INDETERMINATE:
                    # Animation indéterminée
                    fill_width = width * 0.3
                    offset = (self._animation_offset * width * 2) % (width + fill_width) - fill_width
                    fill_rect = QRectF(offset, 0, fill_width, height)
                else:
                    fill_rect = QRectF(0, 0, fill_width, height)

                # Appliquer le style
                if self._config.style == BarStyle.GRADIENT:
                    # Gradient animé
                    gradient = QLinearGradient(QPointF(0, 0), QPointF(fill_width, 0))
                    offset = self._animation_offset
                    gradient.setColorAt(0.0, QColor(COLOR_PRIMARY_DIM))
                    gradient.setColorAt(offset, QColor(COLOR_PRIMARY))
                    gradient.setColorAt(min(1.0, offset + 0.3), QColor(COLOR_PRIMARY_GLOW))
                    gradient.setColorAt(1.0, QColor(COLOR_PRIMARY))
                    painter.setBrush(QBrush(gradient))

                elif self._config.style == BarStyle.SOLID:
                    # Couleur unie
                    painter.setBrush(QBrush(state_color))

                elif self._config.style == BarStyle.PULSING:
                    # Pulsation
                    alpha = int(255 * (0.5 + 0.5 * math.sin(self._pulse_phase)))
                    pulsing_color = QColor(state_color)
                    pulsing_color.setAlpha(alpha)
                    painter.setBrush(QBrush(pulsing_color))

                elif self._config.style == BarStyle.NEON:
                    # Effet néon avec glow
                    gradient = QLinearGradient(QPointF(0, 0), QPointF(fill_width, 0))
                    gradient.setColorAt(0.0, QColor(COLOR_PRIMARY_DIM))
                    gradient.setColorAt(0.5, QColor(COLOR_PRIMARY_GLOW))
                    gradient.setColorAt(1.0, QColor(COLOR_PRIMARY))
                    painter.setBrush(QBrush(gradient))

                    # Dessiner avec glow
                    painter.setPen(QPen(QColor(COLOR_PRIMARY_GLOW), 2))
                    painter.drawRoundedRect(fill_rect, self._config.border_radius, self._config.border_radius)

                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(fill_rect, self._config.border_radius, self._config.border_radius)

            # Dessiner le texte du pourcentage
            if self._config.show_percentage and self._data.state != ProgressState.INDETERMINATE:
                text = f"{self._data.percentage:.1f}%"
                font = QFont("JetBrains Mono", 10, QFont.Weight.Bold)
                painter.setFont(font)
                painter.setPen(QPen(QColor(COLOR_TEXT_BRIGHT)))

                text_rect = QRectF(0, 0, width, height)
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)

            painter.end()

    class ProgressInfo(QWidget):
        """Panneau d'informations de progression.

        Affiche la vitesse, l'ETA, la taille, et le compteur.
        """

        def __init__(
            self,
            *,
            config: ProgressConfig,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise le panneau.

            Args:
                config: Configuration.
                parent: Widget parent.
            """
            super().__init__(parent)
            self._config = config

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(0, 4, 0, 0)
            layout.setSpacing(12)

            # Vitesse
            self._speed_label = QLabel("")
            self._speed_label.setStyleSheet(f"color: {COLOR_SECONDARY}; font-size: 11px;")
            self._speed_label.setVisible(config.show_speed)
            layout.addWidget(self._speed_label)

            # ETA
            self._eta_label = QLabel("")
            self._eta_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
            self._eta_label.setVisible(config.show_eta)
            layout.addWidget(self._eta_label)

            # Taille
            self._size_label = QLabel("")
            self._size_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
            self._size_label.setVisible(config.show_size)
            layout.addWidget(self._size_label)

            # Compteur
            self._count_label = QLabel("")
            self._count_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
            self._count_label.setVisible(config.show_count)
            layout.addWidget(self._count_label)

            layout.addStretch()

        def update_data(self, data: ProgressData) -> None:
            """Met à jour les informations.

            Args:
                data: Données de progression.
            """
            if self._config.show_speed:
                self._speed_label.setText(f"⚡ {data.speed_human}")

            if self._config.show_eta:
                self._eta_label.setText(f"⏱️ {data.eta_human}")

            if self._config.show_size:
                self._size_label.setText(f"💾 {data.downloaded_human} / {data.total_human}")

            if self._config.show_count:
                self._count_label.setText(f"📄 {int(data.current)}/{int(data.total)}")

    class ProgressStatus(QWidget):
        """Indicateur de statut avec icône et label."""

        def __init__(
            self,
            *,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise l'indicateur.

            Args:
                parent: Widget parent.
            """
            super().__init__(parent)

            # Layout
            layout = QHBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)

            # Icône
            self._icon_label = QLabel(ProgressState.PENDING.icon)
            self._icon_label.setStyleSheet(f"font-size: 14px; color: {COLOR_TEXT_DIM};")
            layout.addWidget(self._icon_label)

            # Label
            self._text_label = QLabel(ProgressState.PENDING.label)
            self._text_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
            layout.addWidget(self._text_label)

            layout.addStretch()

        def set_state(self, state: ProgressState, error_message: str = "") -> None:
            """Définit l'état.

            Args:
                state: Nouvel état.
                error_message: Message d'erreur (si FAILED).
            """
            self._icon_label.setText(state.icon)
            self._icon_label.setStyleSheet(f"font-size: 14px; color: {state.color};")

            if state == ProgressState.FAILED and error_message:
                self._text_label.setText(f"{state.label}: {error_message}")
            else:
                self._text_label.setText(state.label)

            self._text_label.setStyleSheet(f"color: {state.color}; font-size: 11px;")

    class ProgressWidget(QWidget):
        """Widget principal de progression.

        Combine la barre, les informations, et le statut en un seul
        widget cohérent avec style cyberpunk néon.

        Signals:
            progress_changed(float, float): current, total
            state_changed(ProgressState): nouvel état
            completed(): progression terminée
            failed(str): progression échouée avec message d'erreur
        """

        # Signaux
        progress_changed = pyqtSignal(float, float)
        state_changed = pyqtSignal(object)
        completed = pyqtSignal()
        failed = pyqtSignal(str)

        def __init__(
            self,
            *,
            title: str = "",
            config: ProgressConfig | None = None,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise le widget.

            Args:
                title: Titre du widget.
                config: Configuration.
                parent: Widget parent.
            """
            super().__init__(parent)
            self._title = title
            self._config = config or ProgressConfig()
            self._data = ProgressData()

            # Layout principal
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)

            # Titre (optionnel)
            if title:
                self._title_label = QLabel(title)
                self._title_label.setStyleSheet(f"""
                    color: {COLOR_TEXT};
                    font-size: 12px;
                    font-weight: bold;
                    font-family: 'JetBrains Mono', monospace;
                """)
                layout.addWidget(self._title_label)

            # Barre de progression
            self._progress_bar = ProgressBar(config=self._config, parent=self)
            layout.addWidget(self._progress_bar)

            # Informations (mode NORMAL ou DETAILED)
            if self._config.mode in (DisplayMode.NORMAL, DisplayMode.DETAILED):
                self._progress_info = ProgressInfo(config=self._config, parent=self)
                layout.addWidget(self._progress_info)

            # Statut
            if self._config.show_status:
                self._progress_status = ProgressStatus(parent=self)
                layout.addWidget(self._progress_status)

            # Taille minimale
            self.setMinimumWidth(WIDGET_MIN_WIDTH)
            self.setMaximumWidth(WIDGET_MAX_WIDTH)

        # =====================================================================
        # API PUBLIQUE — Mise à jour
        # =====================================================================

        def set_progress(self, current: float, total: float | None = None) -> None:
            """Définit la progression.

            Args:
                current: Valeur actuelle.
                total: Valeur totale (optionnel, garde l'ancienne si None).
            """
            if total is not None:
                self._data.total = total

            if current < 0 or current > self._data.total:
                raise InvalidProgressError(current, f"Must be between 0 and {self._data.total}")

            self._data.current = current
            self._progress_bar.set_data(self._data)

            if hasattr(self, "_progress_info"):
                self._progress_info.update_data(self._data)

            self.progress_changed.emit(current, self._data.total)

            # Vérifier si terminé
            if current >= self._data.total and self._data.state == ProgressState.RUNNING:
                self.set_state(ProgressState.COMPLETED)

        def set_state(self, state: ProgressState, error_message: str = "") -> None:
            """Définit l'état.

            Args:
                state: Nouvel état.
                error_message: Message d'erreur (si FAILED).
            """
            old_state = self._data.state
            self._data.state = state
            self._data.error_message = error_message

            self._progress_bar.set_data(self._data)

            if hasattr(self, "_progress_status"):
                self._progress_status.set_state(state, error_message)

            self.state_changed.emit(state)

            # Émettre les signaux de terminaison
            if state == ProgressState.COMPLETED and old_state != ProgressState.COMPLETED:
                self.completed.emit()
            elif state == ProgressState.FAILED:
                self.failed.emit(error_message)

        def set_speed(self, speed_bytes_per_sec: float) -> None:
            """Définit la vitesse.

            Args:
                speed_bytes_per_sec: Vitesse en bytes/seconde.
            """
            self._data.speed_bytes_per_sec = speed_bytes_per_sec

            if hasattr(self, "_progress_info"):
                self._progress_info.update_data(self._data)

        def set_size(self, downloaded_bytes: int, total_bytes: int) -> None:
            """Définit la taille.

            Args:
                downloaded_bytes: Taille téléchargée.
                total_bytes: Taille totale.
            """
            self._data.downloaded_bytes = downloaded_bytes
            self._data.total_bytes = total_bytes

            if hasattr(self, "_progress_info"):
                self._progress_info.update_data(self._data)

        def set_count(self, current: int, total: int) -> None:
            """Définit le compteur.

            Args:
                current: Valeur actuelle.
                total: Valeur totale.
            """
            self.set_progress(float(current), float(total))

        def reset(self) -> None:
            """Réinitialise le widget."""
            self._data = ProgressData()
            self._progress_bar.set_data(self._data)

            if hasattr(self, "_progress_info"):
                self._progress_info.update_data(self._data)

            if hasattr(self, "_progress_status"):
                self._progress_status.set_state(ProgressState.PENDING)

        def update_from_task(self, task: Any) -> None:
            """Met à jour depuis un DownloadTask.

            Args:
                task: Instance de DownloadTask.
            """
            # Mapper l'état
            state_map = {
                "pending": ProgressState.PENDING,
                "queued": ProgressState.PENDING,
                "running": ProgressState.RUNNING,
                "downloading": ProgressState.RUNNING,
                "paused": ProgressState.PAUSED,
                "completed": ProgressState.COMPLETED,
                "failed": ProgressState.FAILED,
                "cancelled": ProgressState.CANCELLED,
            }

            task_state = getattr(task, "status", "pending")
            if isinstance(task_state, str):
                state = state_map.get(task_state, ProgressState.PENDING)
            else:
                state = state_map.get(task_state.value, ProgressState.PENDING)

            # Mettre à jour les données
            self.set_progress(task.pages_completed, task.pages_total)
            self.set_state(state, getattr(task, "error_message", ""))
            self.set_speed(getattr(task, "speed_bytes_per_sec", 0.0))
            self.set_size(task.downloaded_bytes, task.total_size_bytes)

        # =====================================================================
        # API PUBLIQUE — Accès aux données
        # =====================================================================

        @property
        def data(self) -> ProgressData:
            """Données de progression."""
            return self._data

        @property
        def config(self) -> ProgressConfig:
            """Configuration."""
            return self._config

        @property
        def progress(self) -> float:
            """Progression (0.0 à 1.0)."""
            return self._data.progress

        @property
        def percentage(self) -> float:
            """Progression en pourcentage."""
            return self._data.percentage

        @property
        def state(self) -> ProgressState:
            """État actuel."""
            return self._data.state

        @property
        def is_completed(self) -> bool:
            """Indique si terminé."""
            return self._data.state == ProgressState.COMPLETED

        @property
        def is_failed(self) -> bool:
            """Indique si échoué."""
            return self._data.state == ProgressState.FAILED

        @property
        def is_active(self) -> bool:
            """Indique si actif."""
            return self._data.state.is_active


# ============================================================================
# FONCTIONS HELPERS
# ============================================================================


def create_progress_widget(
    title: str = "",
    *,
    style: BarStyle = BarStyle.GRADIENT,
    mode: DisplayMode = DisplayMode.NORMAL,
    parent: Any = None,
) -> Any:
    """Crée un widget de progression avec configuration simplifiée.

    Args:
        title: Titre du widget.
        style: Style de la barre.
        mode: Mode d'affichage.
        parent: Widget parent.

    Returns:
        Instance de ProgressWidget.
    """
    if not PYQT6_AVAILABLE:
        raise ProgressWidgetError(
            "PyQt6 n'est pas installé. Installez-le avec: pip install PyQt6"
        )

    config = ProgressConfig(style=style, mode=mode)
    return ProgressWidget(title=title, config=config, parent=parent)


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
    "BAR_HEIGHT",
    "WIDGET_MIN_WIDTH",
    "WIDGET_MAX_WIDTH",
    "STATE_COLORS",
    "STATE_ICONS",
    # Exceptions
    "ProgressWidgetError",
    "InvalidProgressError",
    # Enums
    "ProgressState",
    "BarStyle",
    "DisplayMode",
    # Modèles
    "ProgressConfig",
    "ProgressData",
    # Widgets
    "ProgressWidget" if PYQT6_AVAILABLE else None,
    "ProgressBar" if PYQT6_AVAILABLE else None,
    "ProgressInfo" if PYQT6_AVAILABLE else None,
    "ProgressStatus" if PYQT6_AVAILABLE else None,
    # Helpers
    "create_progress_widget",
    "is_pyqt6_available",
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
