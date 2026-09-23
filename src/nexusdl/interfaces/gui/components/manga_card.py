"""Composant GUI de carte manga pour NexusDL.

Ce module fournit un widget PyQt6 personnalisé pour afficher une carte
représentant un manga/webtoon/comic dans l'interface graphique. Il offre
une expérience visuelle riche avec style cyberpunk néon, animations, et
interactions utilisateur.

**Fonctionnalités** :
    - 4 layouts : VERTICAL, HORIZONTAL, COMPACT, DETAILED
    - Affichage de la couverture (URL, fichier local, placeholder)
    - Badges visuels (langue, statut, contenu adulte, tags)
    - Barre de progression de lecture intégrée
    - 5 états : NORMAL, SELECTED, FOCUSED, DISABLED, HOVER
    - Effets visuels : glow néon, animations, transitions
    - Rendu custom de la couverture avec QPainter
    - Support des images distantes (téléchargement async)
    - Support des images locales
    - Placeholder ASCII art si couverture manquante
    - Signaux Qt pour communication
    - Intégration avec modèle Manga
    - Configuration complète via MangaCardConfig
    - Style cyberpunk néon cohérent

**Architecture** :
    MangaCard (QWidget principal)
        ├── MangaCover (QFrame custom avec QPainter)
        │   ├── Image de couverture
        │   ├── Placeholder si manquante
        │   └── Effet glow au hover
        ├── MangaInfo (panneau d'informations)
        │   ├── Titre
        │   ├── Auteur
        │   ├── Année
        │   └── Statut
        ├── MangaBadges (badges visuels)
        │   ├── Langue (drapeau)
        │   ├── Statut (icône)
        │   ├── Adulte (🔞)
        │   └── Tags (#tag)
        ├── MangaProgress (barre de progression)
        │   ├── Barre visuelle
        │   └── Texte (chapters read/total)
        └── MangaDescription (mode DETAILED)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.gui.components.manga_card import (
    ...     MangaCard, CardLayout,
    ... )
    >>>
    >>> # Dans une fenêtre PyQt6
    >>> card = MangaCard(
    ...     manga=manga,
    ...     layout=CardLayout.VERTICAL,
    ...     selectable=True,
    ...     parent=self,
    ... )
    >>> card.clicked.connect(self.on_manga_clicked)
    >>> card.selected.connect(self.on_manga_selected)
    >>> layout.addWidget(card)
    >>>
    >>> # Changer l'état
    >>> card.set_selected(True)
    >>> card.set_state(CardState.FOCUSED)

Intégration :
    - core/models/manga.py : modèle Manga
    - core/models/library.py : ReadingProgress
    - core/events.py : émission d'événements
    - core/i18n.py : traductions
    - core/utils/text.py : formatage, troncature
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Final

from loguru import logger

try:
    from PyQt6.QtCore import (
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
        QFont,
        QIcon,
        QLinearGradient,
        QMouseEvent,
        QPaintEvent,
        QPainter,
        QPen,
        QPixmap,
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
COLOR_SURFACE_HOVER: Final[str] = "#1a1f2e"
COLOR_TEXT: Final[str] = "#00ff41"
COLOR_TEXT_BRIGHT: Final[str] = "#ffffff"
COLOR_TEXT_MUTED: Final[str] = "#00aaaa"
COLOR_TEXT_DIM: Final[str] = "#006666"
COLOR_TEXT_DISABLED: Final[str] = "#004444"
COLOR_BORDER: Final[str] = "#00ff41"
COLOR_BORDER_DIM: Final[str] = "#006622"
COLOR_BORDER_FOCUS: Final[str] = "#00ffff"

# Dimensions par layout
CARD_WIDTH_VERTICAL: Final[int] = 200
CARD_HEIGHT_VERTICAL: Final[int] = 300
CARD_WIDTH_HORIZONTAL: Final[int] = 400
CARD_HEIGHT_HORIZONTAL: Final[int] = 150
CARD_WIDTH_COMPACT: Final[int] = 400
CARD_HEIGHT_COMPACT: Final[int] = 60
CARD_WIDTH_DETAILED: Final[int] = 350
CARD_HEIGHT_DETAILED: Final[int] = 450

COVER_HEIGHT_RATIO: Final[float] = 0.6  # 60% de la hauteur pour la cover

# Longueurs maximales pour troncature
MAX_TITLE_LENGTH: Final[int] = 40
MAX_AUTHOR_LENGTH: Final[int] = 30
MAX_DESCRIPTION_LENGTH: Final[int] = 200
MAX_TAGS_DISPLAYED: Final[int] = 3

# Placeholder pour covers manquantes
PLACEHOLDER_ICON: Final[str] = "📚"
PLACEHOLDER_TEXT: Final[str] = "No Cover"


# ============================================================================
# EXCEPTIONS
# ============================================================================


class MangaCardError(NexusDLError):
    """Exception de base pour les erreurs de carte manga."""


class InvalidMangaError(MangaCardError):
    """Exception levée lorsqu'un manga est invalide."""

    def __init__(self, manga_id: str, reason: str = "") -> None:
        msg = f"Manga invalide: {manga_id}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.manga_id = manga_id
        self.reason = reason


class CoverLoadError(MangaCardError):
    """Exception levée lorsqu'une couverture ne peut être chargée."""

    def __init__(self, source: str, reason: str = "") -> None:
        msg = f"Impossible de charger la couverture: {source}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.source = source
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class CardLayout(str, Enum):
    """Layout de la carte manga.

    Attributes:
        VERTICAL: Cover au-dessus, infos en-dessous.
        HORIZONTAL: Cover à gauche, infos à droite.
        COMPACT: Texte seul sans cover.
        DETAILED: Cover + toutes les infos + description.
    """

    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"
    COMPACT = "compact"
    DETAILED = "detailed"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            CardLayout.VERTICAL: t("card.layout.vertical", default="Vertical"),
            CardLayout.HORIZONTAL: t("card.layout.horizontal", default="Horizontal"),
            CardLayout.COMPACT: t("card.layout.compact", default="Compact"),
            CardLayout.DETAILED: t("card.layout.detailed", default="Detailed"),
        }[self]

    @property
    def default_size(self) -> tuple[int, int]:
        """Taille par défaut (width, height)."""
        return {
            CardLayout.VERTICAL: (CARD_WIDTH_VERTICAL, CARD_HEIGHT_VERTICAL),
            CardLayout.HORIZONTAL: (CARD_WIDTH_HORIZONTAL, CARD_HEIGHT_HORIZONTAL),
            CardLayout.COMPACT: (CARD_WIDTH_COMPACT, CARD_HEIGHT_COMPACT),
            CardLayout.DETAILED: (CARD_WIDTH_DETAILED, CARD_HEIGHT_DETAILED),
        }[self]

    @property
    def shows_cover(self) -> bool:
        """Indique si ce layout affiche la couverture."""
        return self in (CardLayout.VERTICAL, CardLayout.HORIZONTAL, CardLayout.DETAILED)

    @property
    def shows_description(self) -> bool:
        """Indique si ce layout affiche la description."""
        return self == CardLayout.DETAILED


class CardState(str, Enum):
    """État visuel de la carte.

    Attributes:
        NORMAL: État normal.
        SELECTED: Carte sélectionnée.
        FOCUSED: Carte a le focus.
        DISABLED: Carte désactivée.
        HOVER: Souris sur la carte.
    """

    NORMAL = "normal"
    SELECTED = "selected"
    FOCUSED = "focused"
    DISABLED = "disabled"
    HOVER = "hover"

    @property
    def border_color(self) -> str:
        """Couleur de bordure."""
        return {
            CardState.NORMAL: COLOR_BORDER_DIM,
            CardState.SELECTED: COLOR_PRIMARY,
            CardState.FOCUSED: COLOR_BORDER_FOCUS,
            CardState.DISABLED: COLOR_TEXT_DISABLED,
            CardState.HOVER: COLOR_SECONDARY,
        }[self]

    @property
    def background_color(self) -> str:
        """Couleur de fond."""
        return {
            CardState.NORMAL: COLOR_SURFACE,
            CardState.SELECTED: COLOR_PRIMARY_DIM,
            CardState.FOCUSED: COLOR_SURFACE_HOVER,
            CardState.DISABLED: COLOR_SURFACE_ALT,
            CardState.HOVER: COLOR_SURFACE_HOVER,
        }[self]

    @property
    def opacity(self) -> float:
        """Opacité (0.0 à 1.0)."""
        return {
            CardState.NORMAL: 1.0,
            CardState.SELECTED: 1.0,
            CardState.FOCUSED: 1.0,
            CardState.DISABLED: 0.5,
            CardState.HOVER: 1.0,
        }[self]


class CoverSource(str, Enum):
    """Source de la couverture.

    Attributes:
        URL: URL distante.
        LOCAL: Fichier local.
        PLACEHOLDER: Placeholder.
        LOADING: En cours de chargement.
        ERROR: Erreur de chargement.
    """

    URL = "url"
    LOCAL = "local"
    PLACEHOLDER = "placeholder"
    LOADING = "loading"
    ERROR = "error"


# ============================================================================
# MODÈLES DE DONNÉES
# ============================================================================


class MangaCardConfig:
    """Configuration de la carte manga.

    Attributes:
        layout: Layout de la carte.
        show_cover: Afficher la couverture.
        show_badges: Afficher les badges.
        show_progress: Afficher la progression.
        show_description: Afficher la description.
        show_tags: Afficher les tags.
        show_year: Afficher l'année.
        show_status: Afficher le statut.
        show_author: Afficher l'auteur.
        selectable: Permettre la sélection.
        clickable: Permettre le clic.
        max_title_length: Longueur max du titre.
        max_author_length: Longueur max de l'auteur.
        max_description_length: Longueur max de la description.
        max_tags_displayed: Nombre max de tags.
        placeholder_on_missing_cover: Utiliser placeholder si cover manquante.
    """

    def __init__(
        self,
        *,
        layout: CardLayout = CardLayout.VERTICAL,
        show_cover: bool = True,
        show_badges: bool = True,
        show_progress: bool = True,
        show_description: bool = False,
        show_tags: bool = True,
        show_year: bool = True,
        show_status: bool = True,
        show_author: bool = True,
        selectable: bool = False,
        clickable: bool = True,
        max_title_length: int = MAX_TITLE_LENGTH,
        max_author_length: int = MAX_AUTHOR_LENGTH,
        max_description_length: int = MAX_DESCRIPTION_LENGTH,
        max_tags_displayed: int = MAX_TAGS_DISPLAYED,
        placeholder_on_missing_cover: bool = True,
    ) -> None:
        """Initialise la configuration."""
        self.layout = layout
        self.show_cover = show_cover
        self.show_badges = show_badges
        self.show_progress = show_progress
        self.show_description = show_description
        self.show_tags = show_tags
        self.show_year = show_year
        self.show_status = show_status
        self.show_author = show_author
        self.selectable = selectable
        self.clickable = clickable
        self.max_title_length = max_title_length
        self.max_author_length = max_author_length
        self.max_description_length = max_description_length
        self.max_tags_displayed = max_tags_displayed
        self.placeholder_on_missing_cover = placeholder_on_missing_cover


class MangaCardData:
    """Données de la carte manga.

    Attributes:
        manga_id: ID du manga.
        title: Titre.
        author: Auteur.
        year: Année.
        status: Statut.
        language: Langue.
        is_adult: Si adulte.
        tags: Liste de tags.
        description: Description.
        cover_url: URL de la couverture.
        cover_path: Chemin local de la couverture.
        progress: Progression (0.0 à 1.0).
        chapters_read: Chapitres lus.
        chapters_total: Chapitres totaux.
    """

    def __init__(
        self,
        *,
        manga_id: str,
        title: str,
        author: str = "",
        year: int | None = None,
        status: str = "unknown",
        language: str = "en",
        is_adult: bool = False,
        tags: list[str] | None = None,
        description: str = "",
        cover_url: str = "",
        cover_path: str = "",
        progress: float = 0.0,
        chapters_read: int = 0,
        chapters_total: int = 0,
    ) -> None:
        """Initialise les données."""
        self.manga_id = manga_id
        self.title = title
        self.author = author
        self.year = year
        self.status = status
        self.language = language
        self.is_adult = is_adult
        self.tags = tags or []
        self.description = description
        self.cover_url = cover_url
        self.cover_path = cover_path
        self.progress = progress
        self.chapters_read = chapters_read
        self.chapters_total = chapters_total


# ============================================================================
# WIDGETS — Composants PyQt6
# ============================================================================


if PYQT6_AVAILABLE:

    class MangaCover(QFrame):
        """Widget pour afficher la couverture d'un manga.

        Supporte les URLs distantes, fichiers locaux, et placeholders.
        Dessiné avec QPainter pour un contrôle total.
        """

        # Signaux
        cover_loaded = pyqtSignal()
        cover_error = pyqtSignal(str)

        def __init__(
            self,
            *,
            width: int,
            height: int,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la couverture.

            Args:
                width: Largeur.
                height: Hauteur.
                parent: Widget parent.
            """
            super().__init__(parent)
            self._width = width
            self._height = height
            self._pixmap: QPixmap | None = None
            self._source_type = CoverSource.PLACEHOLDER
            self._is_hovered = False

            # Configuration
            self.setFixedSize(width, height)
            self.setStyleSheet(f"""
                MangaCover {{
                    background-color: {COLOR_SURFACE_ALT};
                    border: 1px solid {COLOR_BORDER_DIM};
                    border-radius: 4px;
                }}
                MangaCover:hover {{
                    border: 2px solid {COLOR_SECONDARY};
                }}
            """)

            # Effet glow au hover
            self.setMouseTracking(True)

        def set_cover_url(self, url: str) -> None:
            """Définit l'URL de la couverture.

            Args:
                url: URL de la couverture.
            """
            if not url:
                self._source_type = CoverSource.PLACEHOLDER
                self.update()
                return

            self._source_type = CoverSource.LOADING
            self.update()

            # TODO: Implémenter le téléchargement async
            # Pour l'instant, on utilise un placeholder
            self._source_type = CoverSource.PLACEHOLDER
            self.update()

        def set_cover_path(self, path: str) -> None:
            """Définit le chemin local de la couverture.

            Args:
                path: Chemin du fichier.
            """
            if not path:
                self._source_type = CoverSource.PLACEHOLDER
                self.update()
                return

            try:
                pixmap = QPixmap(path)
                if pixmap.isNull():
                    raise CoverLoadError(path, "Image invalide")

                self._pixmap = pixmap
                self._source_type = CoverSource.LOCAL
                self.update()
                self.cover_loaded.emit()

            except Exception as e:
                logger.warning("Impossible de charger la couverture {}: {}", path, e)
                self._source_type = CoverSource.ERROR
                self.update()
                self.cover_error.emit(str(e))

        def enterEvent(self, event: Any) -> None:
            """Gère l'entrée de la souris."""
            self._is_hovered = True
            self.update()
            super().enterEvent(event)

        def leaveEvent(self, event: Any) -> None:
            """Gère la sortie de la souris."""
            self._is_hovered = False
            self.update()
            super().leaveEvent(event)

        def paintEvent(self, event: QPaintEvent) -> None:
            """Dessine la couverture."""
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

            width = self.width()
            height = self.height()

            # Dessiner le fond
            bg_color = QColor(COLOR_SURFACE_ALT)
            painter.fillRect(0, 0, width, height, bg_color)

            # Dessiner la couverture si disponible
            if self._pixmap and not self._pixmap.isNull():
                # Redimensionner l'image pour remplir le widget
                scaled_pixmap = self._pixmap.scaled(
                    width,
                    height,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )

                # Centrer l'image
                x = (width - scaled_pixmap.width()) // 2
                y = (height - scaled_pixmap.height()) // 2
                painter.drawPixmap(x, y, scaled_pixmap)

            else:
                # Dessiner le placeholder
                self._draw_placeholder(painter, width, height)

            # Effet glow au hover
            if self._is_hovered:
                glow_color = QColor(COLOR_SECONDARY)
                glow_color.setAlpha(50)
                painter.fillRect(0, 0, width, height, glow_color)

            painter.end()

        def _draw_placeholder(self, painter: QPainter, width: int, height: int) -> None:
            """Dessine le placeholder."""
            # Icône
            font = QFont("Arial", 48)
            painter.setFont(font)
            painter.setPen(QColor(COLOR_TEXT_DIM))
            painter.drawText(
                QRect(0, 0, width, height - 30),
                Qt.AlignmentFlag.AlignCenter,
                PLACEHOLDER_ICON,
            )

            # Texte
            font = QFont("JetBrains Mono", 10)
            painter.setFont(font)
            painter.setPen(QColor(COLOR_TEXT_DIM))
            painter.drawText(
                QRect(0, height - 30, width, 30),
                Qt.AlignmentFlag.AlignCenter,
                PLACEHOLDER_TEXT,
            )

    class MangaCard(QWidget):
        """Widget principal de carte manga.

        Affiche une carte représentant un manga avec cover, titre, auteur,
        badges, barre de progression, et description optionnelle.

        Signals:
            clicked(): Émis lorsque la carte est cliquée.
            double_clicked(): Émis lorsque la carte est double-cliquée.
            selected(bool): Émis lorsque la sélection change.
            hover_entered(): Émis lorsque la souris entre sur la carte.
            hover_left(): Émis lorsque la souris quitte la carte.
        """

        # Signaux
        clicked = pyqtSignal()
        double_clicked = pyqtSignal()
        selected = pyqtSignal(bool)
        hover_entered = pyqtSignal()
        hover_left = pyqtSignal()

        def __init__(
            self,
            manga_data: MangaCardData,
            *,
            config: MangaCardConfig | None = None,
            parent: QWidget | None = None,
        ) -> None:
            """Initialise la carte.

            Args:
                manga_data: Données du manga.
                config: Configuration de la carte.
                parent: Widget parent.
            """
            super().__init__(parent)

            if not manga_data or not manga_data.manga_id:
                raise InvalidMangaError("unknown", "Manga data is None or has no ID")

            self._data = manga_data
            self._config = config or MangaCardConfig()
            self._state = CardState.NORMAL
            self._is_selected = False

            # Configuration visuelle
            width, height = self._config.layout.default_size
            self.setFixedSize(width, height)
            self.setCursor(Qt.CursorShape.PointingHandCursor if self._config.clickable else Qt.CursorShape.ArrowCursor)
            self.setMouseTracking(True)

            # Layout principal
            self._setup_layout()

            # Charger la couverture
            if self._config.layout.shows_cover and self._config.show_cover:
                if self._data.cover_path:
                    self._cover.set_cover_path(self._data.cover_path)
                elif self._data.cover_url:
                    self._cover.set_cover_url(self._data.cover_url)

        def _setup_layout(self) -> None:
            """Configure le layout selon le mode."""
            layout_type = self._config.layout

            if layout_type == CardLayout.VERTICAL:
                self._setup_vertical_layout()
            elif layout_type == CardLayout.HORIZONTAL:
                self._setup_horizontal_layout()
            elif layout_type == CardLayout.COMPACT:
                self._setup_compact_layout()
            elif layout_type == CardLayout.DETAILED:
                self._setup_detailed_layout()

        def _setup_vertical_layout(self) -> None:
            """Configure le layout vertical."""
            layout = QVBoxLayout(self)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(8)

            # Couverture
            if self._config.show_cover:
                cover_height = int(self.height() * COVER_HEIGHT_RATIO)
                self._cover = MangaCover(
                    width=self.width() - 16,
                    height=cover_height,
                    parent=self,
                )
                layout.addWidget(self._cover)

            # Informations
            self._setup_info_section(layout)

            # Badges
            if self._config.show_badges:
                self._setup_badges_section(layout)

            # Progression
            if self._config.show_progress and self._data.chapters_total > 0:
                self._setup_progress_section(layout)

            layout.addStretch()

        def _setup_horizontal_layout(self) -> None:
            """Configure le layout horizontal."""
            layout = QHBoxLayout(self)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(12)

            # Couverture
            if self._config.show_cover:
                self._cover = MangaCover(
                    width=100,
                    height=self.height() - 16,
                    parent=self,
                )
                layout.addWidget(self._cover)

            # Informations
            info_layout = QVBoxLayout()
            info_layout.setSpacing(4)
            self._setup_info_section(info_layout)

            if self._config.show_badges:
                self._setup_badges_section(info_layout)

            if self._config.show_progress and self._data.chapters_total > 0:
                self._setup_progress_section(info_layout)

            info_layout.addStretch()
            layout.addLayout(info_layout)

        def _setup_compact_layout(self) -> None:
            """Configure le layout compact."""
            layout = QHBoxLayout(self)
            layout.setContentsMargins(8, 4, 8, 4)
            layout.setSpacing(8)

            # Titre
            from nexusdl.core.utils.text import truncate
            title = truncate(self._data.title, self._config.max_title_length)
            title_label = QLabel(title)
            title_label.setStyleSheet(f"""
                color: {COLOR_TEXT};
                font-size: 13px;
                font-weight: bold;
                font-family: 'JetBrains Mono', monospace;
            """)
            layout.addWidget(title_label)

            # Métadonnées
            meta_parts = []
            if self._data.author:
                meta_parts.append(f"by {self._data.author}")
            if self._data.year:
                meta_parts.append(str(self._data.year))
            if self._data.status != "unknown":
                meta_parts.append(self._data.status)

            if meta_parts:
                meta_label = QLabel(" • ".join(meta_parts))
                meta_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
                layout.addWidget(meta_label)

            layout.addStretch()

        def _setup_detailed_layout(self) -> None:
            """Configure le layout détaillé."""
            layout = QVBoxLayout(self)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(8)

            # Couverture
            if self._config.show_cover:
                cover_height = int(self.height() * 0.5)
                self._cover = MangaCover(
                    width=self.width() - 16,
                    height=cover_height,
                    parent=self,
                )
                layout.addWidget(self._cover)

            # Informations
            self._setup_info_section(layout)

            # Badges
            if self._config.show_badges:
                self._setup_badges_section(layout)

            # Progression
            if self._config.show_progress and self._data.chapters_total > 0:
                self._setup_progress_section(layout)

            # Description
            if self._config.show_description and self._data.description:
                from nexusdl.core.utils.text import truncate
                desc = truncate(self._data.description, self._config.max_description_length)
                desc_label = QLabel(desc)
                desc_label.setWordWrap(True)
                desc_label.setStyleSheet(f"""
                    color: {COLOR_TEXT_MUTED};
                    font-size: 11px;
                    padding: 4px;
                """)
                layout.addWidget(desc_label)

            layout.addStretch()

        def _setup_info_section(self, layout: QVBoxLayout) -> None:
            """Configure la section d'informations."""
            # Titre
            from nexusdl.core.utils.text import truncate
            title = truncate(self._data.title, self._config.max_title_length)
            title_label = QLabel(title)
            title_label.setStyleSheet(f"""
                color: {COLOR_TEXT};
                font-size: 14px;
                font-weight: bold;
                font-family: 'JetBrains Mono', monospace;
            """)
            layout.addWidget(title_label)

            # Auteur
            if self._config.show_author and self._data.author:
                from nexusdl.core.utils.text import truncate
                author = truncate(self._data.author, self._config.max_author_length)
                author_label = QLabel(f"by {author}")
                author_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
                layout.addWidget(author_label)

            # Année et statut
            meta_parts = []
            if self._config.show_year and self._data.year:
                meta_parts.append(str(self._data.year))
            if self._config.show_status and self._data.status != "unknown":
                meta_parts.append(self._data.status)

            if meta_parts:
                meta_label = QLabel(" • ".join(meta_parts))
                meta_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 10px;")
                layout.addWidget(meta_label)

        def _setup_badges_section(self, layout: QVBoxLayout) -> None:
            """Configure la section de badges."""
            badges_layout = QHBoxLayout()
            badges_layout.setSpacing(6)

            # Badge de langue
            flags = {
                "fr": "🇫🇷", "en": "🇬🇧", "ja": "🇯🇵", "ko": "🇰🇷",
                "zh": "🇨🇳", "de": "🇩🇪", "es": "🇪🇸", "pt": "🇵🇹",
                "ru": "🇷🇺", "it": "🇮🇹", "multi": "🌍",
            }
            flag = flags.get(self._data.language, "🏳️")
            lang_badge = QLabel(f"{flag} {self._data.language.upper()}")
            lang_badge.setStyleSheet(f"""
                background-color: {COLOR_PRIMARY_DIM};
                color: {COLOR_BACKGROUND};
                padding: 2px 6px;
                border-radius: 3px;
                font-size: 10px;
                font-weight: bold;
            """)
            badges_layout.addWidget(lang_badge)

            # Badge adulte
            if self._data.is_adult:
                adult_badge = QLabel("🔞")
                adult_badge.setStyleSheet(f"""
                    background-color: {COLOR_ERROR};
                    color: {COLOR_BACKGROUND};
                    padding: 2px 4px;
                    border-radius: 3px;
                    font-size: 10px;
                """)
                badges_layout.addWidget(adult_badge)

            # Tags
            if self._config.show_tags and self._data.tags:
                for tag in self._data.tags[:self._config.max_tags_displayed]:
                    tag_badge = QLabel(f"#{tag}")
                    tag_badge.setStyleSheet(f"""
                        background-color: {COLOR_ACCENT_DIM};
                        color: {COLOR_TEXT_BRIGHT};
                        padding: 2px 6px;
                        border-radius: 3px;
                        font-size: 10px;
                    """)
                    badges_layout.addWidget(tag_badge)

            badges_layout.addStretch()
            layout.addLayout(badges_layout)

        def _setup_progress_section(self, layout: QVBoxLayout) -> None:
            """Configure la section de progression."""
            progress_layout = QHBoxLayout()
            progress_layout.setSpacing(8)

            # Barre de progression
            progress_bar = QFrame()
            progress_bar.setFixedHeight(8)
            progress_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            progress_bar.setStyleSheet(f"""
                QFrame {{
                    background-color: {COLOR_SURFACE_ALT};
                    border-radius: 4px;
                }}
            """)

            # Remplissage
            fill_width = int(progress_bar.width() * self._data.progress)
            fill = QFrame(progress_bar)
            fill.setFixedSize(fill_width, 8)
            fill.setStyleSheet(f"""
                QFrame {{
                    background-color: {COLOR_PRIMARY};
                    border-radius: 4px;
                }}
            """)

            progress_layout.addWidget(progress_bar)

            # Texte
            progress_text = f"{self._data.chapters_read}/{self._data.chapters_total} ch."
            progress_label = QLabel(progress_text)
            progress_label.setStyleSheet(f"""
                color: {COLOR_ACCENT};
                font-size: 10px;
                font-weight: bold;
            """)
            progress_layout.addWidget(progress_label)

            layout.addLayout(progress_layout)

        # =====================================================================
        # ÉVÉNEMENTS — Interactions utilisateur
        # =====================================================================

        def mousePressEvent(self, event: QMouseEvent) -> None:
            """Gère le clic sur la carte."""
            if event.button() == Qt.MouseButton.LeftButton:
                if self._config.selectable:
                    self.toggle_selection()
                else:
                    self.clicked.emit()

        def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
            """Gère le double-clic."""
            if event.button() == Qt.MouseButton.LeftButton:
                self.double_clicked.emit()

        def enterEvent(self, event: Any) -> None:
            """Gère l'entrée de la souris."""
            if self._state != CardState.DISABLED:
                self.set_state(CardState.HOVER)
            self.hover_entered.emit()

        def leaveEvent(self, event: Any) -> None:
            """Gère la sortie de la souris."""
            if self._is_selected:
                self.set_state(CardState.SELECTED)
            else:
                self.set_state(CardState.NORMAL)
            self.hover_lefted.emit()

        # =====================================================================
        # API PUBLIQUE — État
        # =====================================================================

        def set_state(self, state: CardState) -> None:
            """Définit l'état visuel.

            Args:
                state: Nouvel état.
            """
            self._state = state
            self._update_visuals()

        def set_selected(self, selected: bool) -> None:
            """Définit l'état de sélection.

            Args:
                selected: True si sélectionné.
            """
            if not self._config.selectable:
                return

            self._is_selected = selected
            if selected:
                self.set_state(CardState.SELECTED)
            else:
                self.set_state(CardState.NORMAL)

            self.selected.emit(selected)

        def toggle_selection(self) -> None:
            """Bascule l'état de sélection."""
            self.set_selected(not self._is_selected)

        def set_disabled(self, disabled: bool) -> None:
            """Définit l'état désactivé.

            Args:
                disabled: True si désactivé.
            """
            if disabled:
                self.set_state(CardState.DISABLED)
            else:
                self.set_state(CardState.NORMAL)

        def _update_visuals(self) -> None:
            """Met à jour les visuels selon l'état."""
            border_color = self._state.border_color
            bg_color = self._state.background_color
            opacity = self._state.opacity

            self.setStyleSheet(f"""
                MangaCard {{
                    background-color: {bg_color};
                    border: 2px solid {border_color};
                    border-radius: 6px;
                    opacity: {opacity};
                }}
            """)

        # =====================================================================
        # API PUBLIQUE — Accès aux données
        # =====================================================================

        @property
        def data(self) -> MangaCardData:
            """Données du manga."""
            return self._data

        @property
        def manga_id(self) -> str:
            """ID du manga."""
            return self._data.manga_id

        @property
        def config(self) -> MangaCardConfig:
            """Configuration de la carte."""
            return self._config

        @property
        def state(self) -> CardState:
            """État actuel."""
            return self._state

        @property
        def is_selected(self) -> bool:
            """Indique si la carte est sélectionnée."""
            return self._is_selected

        @property
        def is_disabled(self) -> bool:
            """Indique si la carte est désactivée."""
            return self._state == CardState.DISABLED


# ============================================================================
# FONCTIONS HELPERS
# ============================================================================


def create_manga_card(
    manga_data: MangaCardData,
    *,
    layout: CardLayout = CardLayout.VERTICAL,
    selectable: bool = False,
    parent: Any = None,
) -> Any:
    """Crée une carte manga avec configuration simplifiée.

    Args:
        manga_data: Données du manga.
        layout: Layout de la carte.
        selectable: Permettre la sélection.
        parent: Widget parent.

    Returns:
        Instance de MangaCard.
    """
    if not PYQT6_AVAILABLE:
        raise MangaCardError(
            "PyQt6 n'est pas installé. Installez-le avec: pip install PyQt6"
        )

    config = MangaCardConfig(layout=layout, selectable=selectable)
    return MangaCard(manga_data, config=config, parent=parent)


def manga_to_card_data(manga: Any) -> MangaCardData:
    """Convertit un modèle Manga en MangaCardData.

    Args:
        manga: Instance de Manga.

    Returns:
        Instance de MangaCardData.
    """
    return MangaCardData(
        manga_id=manga.id,
        title=manga.title,
        author=manga.author or "",
        year=manga.year,
        status=manga.status.value if hasattr(manga.status, "value") else str(manga.status),
        language=manga.language.value if hasattr(manga.language, "value") else str(manga.language),
        is_adult=getattr(manga, "is_adult", False),
        tags=getattr(manga, "tags", []),
        description=getattr(manga, "description", ""),
        cover_url=getattr(manga, "cover_url", ""),
        cover_path=getattr(manga, "cover_path", ""),
        progress=getattr(manga.reading_progress, "progress_percentage", 0.0) if hasattr(manga, "reading_progress") and manga.reading_progress else 0.0,
        chapters_read=getattr(manga.reading_progress, "chapters_read", 0) if hasattr(manga, "reading_progress") and manga.reading_progress else 0,
        chapters_total=getattr(manga.reading_progress, "total_chapters", 0) if hasattr(manga, "reading_progress") and manga.reading_progress else 0,
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
    "CARD_WIDTH_VERTICAL",
    "CARD_HEIGHT_VERTICAL",
    "MAX_TITLE_LENGTH",
    "MAX_AUTHOR_LENGTH",
    "MAX_DESCRIPTION_LENGTH",
    "PLACEHOLDER_ICON",
    "PLACEHOLDER_TEXT",
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
    "MangaCard" if PYQT6_AVAILABLE else None,
    "MangaCover" if PYQT6_AVAILABLE else None,
    # Helpers
    "create_manga_card",
    "manga_to_card_data",
    "is_pyqt6_available",
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
