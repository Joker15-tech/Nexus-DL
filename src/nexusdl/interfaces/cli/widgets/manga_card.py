"""Widget de carte manga pour l'interface CLI NexusDL.

Ce module fournit un widget Textual réutilisable pour afficher une carte
représentant un manga/webtoon/comic. Il est utilisé dans plusieurs écrans :

    - LibraryScreen : affichage de la bibliothèque en grille
    - SearchScreen : affichage des résultats de recherche
    - MainScreen : affichage des mangas "Continue Reading"
    - DownloadScreen : affichage des mangas en cours de téléchargement

**Fonctionnalités** :
    - 4 layouts : VERTICAL (cover + infos), HORIZONTAL (cover gauche + infos droite),
      COMPACT (texte seul), DETAILED (cover + toutes les infos + description)
    - Affichage de la couverture (URL, fichier local, ou placeholder)
    - Badges visuels (langue, statut, contenu adulte, tags)
    - Barre de progression de lecture intégrée
    - États : NORMAL, SELECTED, FOCUSED, DISABLED
    - Mode sélection (checkbox) pour actions multiples
    - Callbacks : on_click, on_select, on_double_click
    - Tooltip avec description complète au survol
    - Troncature intelligente du titre/auteur
    - Traductions i18n
    - Gestion des erreurs (cover manquante, etc.)

**Architecture** :
    MangaCard (Widget principal)
        ├── MangaCover (couverture ou placeholder)
        ├── MangaInfo (titre, auteur, année)
        ├── MangaBadges (badges de statut/langue/etc.)
        ├── MangaProgressBar (progression de lecture)
        └── MangaDescription (description tronquée, mode DETAILED)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.cli.widgets.manga_card import (
    ...     MangaCard, CardLayout,
    ... )
    >>>
    >>> # Dans un écran Textual
    >>> card = MangaCard(
    ...     manga=manga,
    ...     layout=CardLayout.VERTICAL,
    ...     selectable=True,
    ...     on_click=my_callback,
    ... )
    >>> self.mount(card)
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
    - core/utils/time.py : formatage des dates
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
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
    from textual.widgets import Label, Static
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False

from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t
from nexusdl.core.models.manga import Language, Manga, MangaStatus
from nexusdl.core.utils.text import truncate


# ============================================================================
# CONSTANTES
# ============================================================================


# Dimensions par défaut des cartes
DEFAULT_CARD_WIDTH: Final[int] = 30
DEFAULT_CARD_HEIGHT: Final[int] = 20
COMPACT_CARD_HEIGHT: Final[int] = 3
HORIZONTAL_CARD_HEIGHT: Final[int] = 7
DETAILED_CARD_HEIGHT: Final[int] = 25

# Longueurs maximales pour troncature
MAX_TITLE_LENGTH: Final[int] = 40
MAX_AUTHOR_LENGTH: Final[int] = 30
MAX_DESCRIPTION_LENGTH: Final[int] = 200
MAX_TAGS_DISPLAYED: Final[int] = 3

# Placeholder ASCII art pour les covers manquantes
COVER_PLACEHOLDER: Final[list[str]] = [
    "╔══════════════╗",
    "║              ║",
    "║     📚       ║",
    "║              ║",
    "║   No Cover   ║",
    "║              ║",
    "╚══════════════╝",
]

# Caractères de bordure pour les cartes
CARD_BORDER_CHARS: Final[dict[str, str]] = {
    "normal": "│",
    "selected": "║",
    "focused": "┃",
    "disabled": "┊",
}


# ============================================================================
# EXCEPTIONS
# ============================================================================


class MangaCardError(NexusDLError):
    """Exception de base pour les erreurs de carte manga."""


class InvalidMangaError(MangaCardError):
    """Exception levée lorsqu'un manga est invalide.

    Attributes:
        manga_id: ID du manga invalide.
        reason: Raison de l'invalidité.
    """

    def __init__(self, manga_id: str, reason: str = "") -> None:
        msg = f"Manga invalide: {manga_id}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.manga_id = manga_id
        self.reason = reason


class CoverLoadError(MangaCardError):
    """Exception levée lorsqu'une couverture ne peut être chargée.

    Attributes:
        source: Source de la couverture.
        reason: Raison de l'échec.
    """

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
        VERTICAL: Cover au-dessus, infos en-dessous (grille classique).
        HORIZONTAL: Cover à gauche, infos à droite (liste).
        COMPACT: Texte seul sans cover (liste dense).
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
    def default_width(self) -> int:
        """Largeur par défaut pour ce layout."""
        return {
            CardLayout.VERTICAL: DEFAULT_CARD_WIDTH,
            CardLayout.HORIZONTAL: 60,
            CardLayout.COMPACT: 60,
            CardLayout.DETAILED: 50,
        }[self]

    @property
    def default_height(self) -> int:
        """Hauteur par défaut pour ce layout."""
        return {
            CardLayout.VERTICAL: DEFAULT_CARD_HEIGHT,
            CardLayout.HORIZONTAL: HORIZONTAL_CARD_HEIGHT,
            CardLayout.COMPACT: COMPACT_CARD_HEIGHT,
            CardLayout.DETAILED: DETAILED_CARD_HEIGHT,
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
        SELECTED: Carte sélectionnée (checkbox cochée).
        FOCUSED: Carte a le focus (surlignée).
        DISABLED: Carte désactivée (grisée).
        HOVER: Souris sur la carte.
    """

    NORMAL = "normal"
    SELECTED = "selected"
    FOCUSED = "focused"
    DISABLED = "disabled"
    HOVER = "hover"

    @property
    def border_char(self) -> str:
        """Caractère de bordure pour cet état."""
        return CARD_BORDER_CHARS.get(self.value, CARD_BORDER_CHARS["normal"])

    @property
    def css_class(self) -> str:
        """Classe CSS pour cet état."""
        return f"state-{self.value}"


class CoverSource(str, Enum):
    """Source de la couverture.

    Attributes:
        URL: URL distante (à télécharger).
        LOCAL: Fichier local.
        PLACEHOLDER: Placeholder ASCII.
        LOADING: En cours de chargement.
        ERROR: Erreur de chargement.
    """

    URL = "url"
    LOCAL = "local"
    PLACEHOLDER = "placeholder"
    LOADING = "loading"
    ERROR = "error"


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class MangaCardConfig(BaseModel):
    """Configuration de la carte manga.

    Attributes:
        layout: Layout de la carte.
        width: Largeur de la carte.
        height: Hauteur de la carte.
        show_cover: Afficher la couverture.
        show_badges: Afficher les badges.
        show_progress: Afficher la barre de progression.
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
        max_tags_displayed: Nombre max de tags affichés.
        placeholder_on_missing_cover: Utiliser un placeholder si cover manquante.
    """

    layout: CardLayout = Field(
        default=CardLayout.VERTICAL,
        description="Layout de la carte.",
    )
    width: int = Field(
        default=DEFAULT_CARD_WIDTH,
        ge=15,
        le=200,
        description="Largeur de la carte.",
    )
    height: int = Field(
        default=DEFAULT_CARD_HEIGHT,
        ge=3,
        le=100,
        description="Hauteur de la carte.",
    )
    show_cover: bool = Field(
        default=True,
        description="Afficher la couverture.",
    )
    show_badges: bool = Field(
        default=True,
        description="Afficher les badges.",
    )
    show_progress: bool = Field(
        default=True,
        description="Afficher la progression.",
    )
    show_description: bool = Field(
        default=False,
        description="Afficher la description.",
    )
    show_tags: bool = Field(
        default=True,
        description="Afficher les tags.",
    )
    show_year: bool = Field(
        default=True,
        description="Afficher l'année.",
    )
    show_status: bool = Field(
        default=True,
        description="Afficher le statut.",
    )
    show_author: bool = Field(
        default=True,
        description="Afficher l'auteur.",
    )
    selectable: bool = Field(
        default=False,
        description="Permettre la sélection.",
    )
    clickable: bool = Field(
        default=True,
        description="Permettre le clic.",
    )
    max_title_length: int = Field(
        default=MAX_TITLE_LENGTH,
        ge=5,
        le=200,
        description="Longueur max du titre.",
    )
    max_author_length: int = Field(
        default=MAX_AUTHOR_LENGTH,
        ge=5,
        le=200,
        description="Longueur max de l'auteur.",
    )
    max_description_length: int = Field(
        default=MAX_DESCRIPTION_LENGTH,
        ge=10,
        le=1000,
        description="Longueur max de la description.",
    )
    max_tags_displayed: int = Field(
        default=MAX_TAGS_DISPLAYED,
        ge=0,
        le=10,
        description="Nombre max de tags.",
    )
    placeholder_on_missing_cover: bool = Field(
        default=True,
        description="Utiliser un placeholder si cover manquante.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class MangaCardState(BaseModel):
    """État de la carte manga.

    Attributes:
        state: État visuel actuel.
        selected: Si la carte est sélectionnée.
        focused: Si la carte a le focus.
        hovered: Si la souris est sur la carte.
        cover_source: Source de la couverture.
        cover_loaded: Si la couverture est chargée.
        last_clicked_at: Timestamp du dernier clic.
    """

    state: CardState = Field(default=CardState.NORMAL, description="État visuel.")
    selected: bool = Field(default=False, description="Sélectionnée.")
    focused: bool = Field(default=False, description="Focus.")
    hovered: bool = Field(default=False, description="Survolée.")
    cover_source: CoverSource = Field(default=CoverSource.PLACEHOLDER, description="Source cover.")
    cover_loaded: bool = Field(default=False, description="Cover chargée.")
    last_clicked_at: datetime | None = Field(default=None, description="Dernier clic.")

    model_config = ConfigDict(extra="forbid")

    @property
    def effective_state(self) -> CardState:
        """État effectif (priorité : disabled > selected > focused > hovered > normal)."""
        if self.state == CardState.DISABLED:
            return CardState.DISABLED
        if self.selected:
            return CardState.SELECTED
        if self.focused:
            return CardState.FOCUSED
        if self.hovered:
            return CardState.HOVER
        return CardState.NORMAL


# ============================================================================
# HELPERS — Formatage et badges
# ============================================================================


def get_status_badge(status: MangaStatus) -> str:
    """Retourne le badge de statut d'un manga.

    Args:
        status: Statut du manga.

    Returns:
        Badge formaté avec icône.
    """
    badges = {
        MangaStatus.ONGOING: "📖 " + t("manga.status.ongoing", default="Ongoing"),
        MangaStatus.COMPLETED: "✅ " + t("manga.status.completed", default="Completed"),
        MangaStatus.HIATUS: "⏸️ " + t("manga.status.hiatus", default="Hiatus"),
        MangaStatus.CANCELLED: "❌ " + t("manga.status.cancelled", default="Cancelled"),
        MangaStatus.UNKNOWN: "❓ " + t("manga.status.unknown", default="Unknown"),
    }
    return badges.get(status, badges[MangaStatus.UNKNOWN])


def get_language_badge(language: Language) -> str:
    """Retourne le badge de langue d'un manga.

    Args:
        language: Langue du manga.

    Returns:
        Badge formaté avec drapeau.
    """
    return f"{language.flag} {language.value.upper()}"


def get_content_rating_badge(is_adult: bool) -> str:
    """Retourne le badge de classification d'un manga.

    Args:
        is_adult: Si le manga est adulte.

    Returns:
        Badge formaté.
    """
    if is_adult:
        return "🔞 " + t("manga.rating.adult", default="Adult")
    return ""


def format_manga_metadata(manga: Manga, *, compact: bool = False) -> str:
    """Formate les métadonnées d'un manga en une chaîne.

    Args:
        manga: Manga à formater.
        compact: Si True, format compact.

    Returns:
        Chaîne formatée.
    """
    parts: list[str] = []

    if manga.author and not compact:
        parts.append(f"by {manga.author}")

    if manga.year:
        parts.append(str(manga.year))

    if manga.status != MangaStatus.UNKNOWN:
        parts.append(manga.status.label)

    if manga.language != Language.EN:
        parts.append(f"{manga.language.flag} {manga.language.value.upper()}")

    return " • ".join(parts)


def format_tags(tags: list[str], *, max_count: int = MAX_TAGS_DISPLAYED) -> str:
    """Formate une liste de tags pour affichage.

    Args:
        tags: Liste de tags.
        max_count: Nombre maximum de tags à afficher.

    Returns:
        Chaîne formatée.
    """
    if not tags:
        return ""

    displayed = tags[:max_count]
    result = " ".join(f"#{tag}" for tag in displayed)

    if len(tags) > max_count:
        result += f" +{len(tags) - max_count}"

    return result


def render_progress_bar(progress: float, width: int = 20) -> str:
    """Rend une petite barre de progression.

    Args:
        progress: Progression (0.0 à 1.0).
        width: Largeur de la barre.

    Returns:
        Chaîne représentant la barre.
    """
    progress = max(0.0, min(1.0, progress))
    filled = int(progress * width)
    return "█" * filled + "░" * (width - filled)


def render_cover_placeholder(width: int, height: int) -> list[str]:
    """Rend un placeholder ASCII pour une couverture manquante.

    Args:
        width: Largeur du placeholder.
        height: Hauteur du placeholder.

    Returns:
        Liste de lignes ASCII.
    """
    lines: list[str] = []

    # Bordure supérieure
    lines.append("┌" + "─" * (width - 2) + "┐")

    # Lignes vides avec icône centrée
    icon_line = height // 2
    for i in range(1, height - 1):
        if i == icon_line:
            icon = "📚"
            padding_left = (width - 2 - 2) // 2  # -2 pour l'emoji (largeur 2)
            padding_right = width - 2 - 2 - padding_left
            lines.append("│" + " " * padding_left + icon + " " * padding_right + "│")
        elif i == icon_line + 2:
            text = "No Cover"
            padding_left = (width - 2 - len(text)) // 2
            padding_right = width - 2 - len(text) - padding_left
            lines.append("│" + " " * padding_left + text + " " * padding_right + "│")
        else:
            lines.append("│" + " " * (width - 2) + "│")

    # Bordure inférieure
    lines.append("└" + "─" * (width - 2) + "┘")

    return lines


# ============================================================================
# WIDGETS INTERNES — Composants de la carte
# ============================================================================


if TEXTUAL_AVAILABLE:

    class MangaCover(Widget):
        """Widget pour afficher la couverture d'un manga.

        Supporte les URLs distantes, fichiers locaux, et placeholders.
        """

        DEFAULT_CSS = """
        MangaCover {
            width: 1fr;
            height: auto;
            content-align: center middle;
        }
        MangaCover > .cover-image {
            width: 1fr;
            content-align: center middle;
        }
        MangaCover > .cover-placeholder {
            color: $text-muted;
            width: 1fr;
            content-align: center middle;
        }
        MangaCover > .cover-loading {
            color: $text-muted;
        }
        """

        def __init__(
            self,
            cover_url: str | None = None,
            *,
            width: int = 28,
            height: int = 14,
            name: str | None = None,
            id: str | None = None,
        ) -> None:
            """Initialise la couverture.

            Args:
                cover_url: URL ou chemin de la couverture.
                width: Largeur de la couverture.
                height: Hauteur de la couverture.
                name: Nom du widget.
                id: ID du widget.
            """
            super().__init__(name=name, id=id)
            self._cover_url = cover_url
            self._width = width
            self._height = height
            self._loaded = False

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            if self._cover_url:
                # Pour l'instant, afficher un placeholder avec l'URL
                # TODO: Implémenter le chargement d'image réel via Pillow/textual
                placeholder_lines = render_cover_placeholder(self._width, self._height)
                yield Static(
                    "\n".join(placeholder_lines),
                    classes="cover-placeholder",
                )
            else:
                placeholder_lines = render_cover_placeholder(self._width, self._height)
                yield Static(
                    "\n".join(placeholder_lines),
                    classes="cover-placeholder",
                )

        async def load_cover(self) -> bool:
            """Charge la couverture depuis l'URL.

            Returns:
                True si le chargement a réussi.
            """
            if not self._cover_url:
                return False

            try:
                # TODO: Implémenter le chargement réel via httpx + Pillow
                # Pour l'instant, on marque comme chargé
                self._loaded = True
                self.refresh()
                return True
            except Exception as e:
                logger.warning("Impossible de charger la couverture {}: {}", self._cover_url, e)
                return False

    class MangaInfo(Widget):
        """Widget pour afficher les informations du manga (titre, auteur, etc.)."""

        DEFAULT_CSS = """
        MangaInfo {
            width: 1fr;
            height: auto;
            padding: 0 1;
        }
        MangaInfo > .manga-title {
            text-style: bold;
            width: 1fr;
        }
        MangaInfo > .manga-author {
            color: $text-muted;
            width: 1fr;
        }
        MangaInfo > .manga-meta {
            color: $text-muted;
            width: 1fr;
        }
        """

        def __init__(
            self,
            manga: Manga,
            *,
            config: MangaCardConfig,
            name: str | None = None,
            id: str | None = None,
        ) -> None:
            """Initialise le widget.

            Args:
                manga: Manga à afficher.
                config: Configuration de la carte.
                name: Nom du widget.
                id: ID du widget.
            """
            super().__init__(name=name, id=id)
            self._manga = manga
            self._config = config

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            # Titre
            title = truncate(self._manga.title, self._config.max_title_length)
            yield Static(title, classes="manga-title")

            # Auteur
            if self._config.show_author and self._manga.author:
                author = truncate(self._manga.author, self._config.max_author_length)
                yield Static(f"by {author}", classes="manga-author")

            # Métadonnées
            meta_parts: list[str] = []
            if self._config.show_year and self._manga.year:
                meta_parts.append(str(self._manga.year))
            if self._config.show_status and self._manga.status != MangaStatus.UNKNOWN:
                meta_parts.append(self._manga.status.label)

            if meta_parts:
                yield Static(" • ".join(meta_parts), classes="manga-meta")

    class MangaBadges(Widget):
        """Widget pour afficher les badges (langue, statut, contenu adulte, tags)."""

        DEFAULT_CSS = """
        MangaBadges {
            layout: horizontal;
            height: 1;
            padding: 0 1;
        }
        MangaBadges > .badge {
            padding: 0 1;
            margin: 0 1 0 0;
        }
        MangaBadges > .badge-language {
            background: $primary 30%;
        }
        MangaBadges > .badge-status {
            background: $success 30%;
        }
        MangaBadges > .badge-adult {
            background: $error 30%;
        }
        MangaBadges > .badge-tag {
            background: $accent 30%;
            color: $text-muted;
        }
        """

        def __init__(
            self,
            manga: Manga,
            *,
            config: MangaCardConfig,
            name: str | None = None,
            id: str | None = None,
        ) -> None:
            """Initialise le widget.

            Args:
                manga: Manga à afficher.
                config: Configuration de la carte.
                name: Nom du widget.
                id: ID du widget.
            """
            super().__init__(name=name, id=id)
            self._manga = manga
            self._config = config

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            if not self._config.show_badges:
                return

            # Badge de langue
            if self._manga.language != Language.EN:
                yield Static(
                    get_language_badge(self._manga.language),
                    classes="badge badge-language",
                )

            # Badge de statut
            if self._config.show_status and self._manga.status != MangaStatus.UNKNOWN:
                yield Static(
                    get_status_badge(self._manga.status),
                    classes="badge badge-status",
                )

            # Badge adulte
            if self._manga.is_adult:
                yield Static(
                    get_content_rating_badge(True),
                    classes="badge badge-adult",
                )

            # Tags
            if self._config.show_tags and self._manga.tags:
                tags_displayed = self._manga.tags[:self._config.max_tags_displayed]
                for tag in tags_displayed:
                    yield Static(f"#{tag}", classes="badge badge-tag")

    class MangaProgressBar(Widget):
        """Widget pour afficher la progression de lecture."""

        DEFAULT_CSS = """
        MangaProgressBar {
            layout: horizontal;
            height: 1;
            padding: 0 1;
        }
        MangaProgressBar > .progress-bar {
            width: 1fr;
            color: $success;
        }
        MangaProgressBar > .progress-text {
            width: auto;
            padding: 0 1;
            color: $text-muted;
        }
        """

        def __init__(
            self,
            progress: float,
            chapters_read: int = 0,
            total_chapters: int = 0,
            *,
            name: str | None = None,
            id: str | None = None,
        ) -> None:
            """Initialise le widget.

            Args:
                progress: Progression (0.0 à 1.0).
                chapters_read: Nombre de chapitres lus.
                total_chapters: Nombre total de chapitres.
                name: Nom du widget.
                id: ID du widget.
            """
            super().__init__(name=name, id=id)
            self._progress = progress
            self._chapters_read = chapters_read
            self._total_chapters = total_chapters

        def compose(self) -> ComposeResult:
            """Compose le widget."""
            bar = render_progress_bar(self._progress, width=20)
            yield Static(bar, classes="progress-bar")

            text = f"{self._chapters_read}/{self._total_chapters} ch. ({self._progress:.0%})"
            yield Static(text, classes="progress-text")

        def update_progress(
            self,
            progress: float,
            chapters_read: int,
            total_chapters: int,
        ) -> None:
            """Met à jour la progression.

            Args:
                progress: Nouvelle progression.
                chapters_read: Nouveau nombre de chapitres lus.
                total_chapters: Nouveau nombre total de chapitres.
            """
            self._progress = progress
            self._chapters_read = chapters_read
            self._total_chapters = total_chapters
            self.refresh()


# ============================================================================
# MESSAGES — Événements Textual
# ============================================================================


if TEXTUAL_AVAILABLE:

    class MangaCardClicked(Message):
        """Message émis lorsqu'une carte est cliquée.

        Attributes:
            manga_id: ID du manga.
            manga: Instance du manga.
        """

        def __init__(self, manga_id: str, manga: Manga) -> None:
            """Initialise le message.

            Args:
                manga_id: ID du manga.
                manga: Instance du manga.
            """
            super().__init__()
            self.manga_id = manga_id
            self.manga = manga

    class MangaCardSelected(Message):
        """Message émis lorsqu'une carte est sélectionnée/désélectionnée.

        Attributes:
            manga_id: ID du manga.
            selected: True si sélectionné, False si désélectionné.
        """

        def __init__(self, manga_id: str, selected: bool) -> None:
            """Initialise le message.

            Args:
                manga_id: ID du manga.
                selected: État de sélection.
            """
            super().__init__()
            self.manga_id = manga_id
            self.selected = selected

    class MangaCardDoubleClicked(Message):
        """Message émis lorsqu'une carte est double-cliquée.

        Attributes:
            manga_id: ID du manga.
            manga: Instance du manga.
        """

        def __init__(self, manga_id: str, manga: Manga) -> None:
            """Initialise le message.

            Args:
                manga_id: ID du manga.
                manga: Instance du manga.
            """
            super().__init__()
            self.manga_id = manga_id
            self.manga = manga


# ============================================================================
# CLASSE PRINCIPALE — MangaCard
# ============================================================================


if TEXTUAL_AVAILABLE:

    class MangaCard(Widget):
        """Widget de carte manga.

        Affiche une carte représentant un manga avec cover, titre, auteur,
        badges, barre de progression, et description optionnelle.
        """

        # Bindings clavier
        BINDINGS = [
            Binding("enter", "activate", "Open"),
            Binding("space", "toggle_select", "Select"),
        ]

        # CSS du widget
        DEFAULT_CSS = """
        MangaCard {
            width: auto;
            height: auto;
            border: solid $primary;
            padding: 0;
            layout: vertical;
        }
        MangaCard:hover {
            border: solid $accent;
        }
        MangaCard.state-selected {
            border: thick $success;
            background: $success 10%;
        }
        MangaCard.state-focused {
            border: thick $accent;
        }
        MangaCard.state-disabled {
            opacity: 0.5;
            border: dashed $text-muted;
        }
        MangaCard > .selection-indicator {
            width: 3;
            height: 1;
            content-align: center middle;
        }
        MangaCard > .selection-indicator.selected {
            color: $success;
        }
        MangaCard.layout-horizontal {
            layout: horizontal;
        }
        MangaCard.layout-compact {
            layout: horizontal;
            height: 3;
        }
        """

        # État réactif
        state: reactive[CardState] = reactive(CardState.NORMAL)

        def __init__(
            self,
            manga: Manga,
            *,
            config: MangaCardConfig | None = None,
            selected: bool = False,
            disabled: bool = False,
            on_click: Callable[[Manga], None] | None = None,
            on_select: Callable[[Manga, bool], None] | None = None,
            on_double_click: Callable[[Manga], None] | None = None,
            name: str | None = None,
            id: str | None = None,
            classes: str | None = None,
        ) -> None:
            """Initialise la carte.

            Args:
                manga: Manga à afficher.
                config: Configuration de la carte.
                selected: État initial de sélection.
                disabled: État initial désactivé.
                on_click: Callback lors du clic.
                on_select: Callback lors de la sélection.
                on_double_click: Callback lors du double-clic.
                name: Nom du widget.
                id: ID du widget.
                classes: Classes CSS.
            """
            super().__init__(name=name, id=id, classes=classes)

            if not manga or not manga.id:
                raise InvalidMangaError("unknown", "Manga is None or has no ID")

            self._manga = manga
            self._config = config or MangaCardConfig()
            self._card_state = MangaCardState(
                selected=selected,
                state=CardState.DISABLED if disabled else CardState.NORMAL,
            )
            self._on_click = on_click
            self._on_select = on_select
            self._on_double_click = on_double_click
            self._last_click_time: float = 0.0

            # Ajouter les classes de layout
            self.add_class(f"layout-{self._config.layout.value}")
            if selected:
                self.add_class("state-selected")
            if disabled:
                self.add_class("state-disabled")

        def compose(self) -> ComposeResult:
            """Compose le widget selon le layout."""
            layout = self._config.layout

            if layout == CardLayout.VERTICAL:
                yield from self._compose_vertical()
            elif layout == CardLayout.HORIZONTAL:
                yield from self._compose_horizontal()
            elif layout == CardLayout.COMPACT:
                yield from self._compose_compact()
            elif layout == CardLayout.DETAILED:
                yield from self._compose_detailed()

        def _compose_vertical(self) -> ComposeResult:
            """Compose le layout vertical (cover au-dessus, infos en-dessous)."""
            # Indicateur de sélection
            if self._config.selectable:
                indicator = "✓" if self._card_state.selected else "○"
                yield Static(indicator, classes="selection-indicator")

            # Couverture
            if self._config.show_cover:
                yield MangaCover(
                    self._manga.cover_url,
                    width=self._config.width - 2,
                    height=12,
                    id="card-cover",
                )

            # Informations
            yield MangaInfo(self._manga, config=self._config, id="card-info")

            # Badges
            if self._config.show_badges:
                yield MangaBadges(self._manga, config=self._config, id="card-badges")

            # Progression
            if self._config.show_progress and self._manga.reading_progress:
                progress = self._manga.reading_progress
                yield MangaProgressBar(
                    progress=progress.progress_percentage,
                    chapters_read=progress.chapters_read,
                    total_chapters=progress.total_chapters,
                    id="card-progress",
                )

        def _compose_horizontal(self) -> ComposeResult:
            """Compose le layout horizontal (cover à gauche, infos à droite)."""
            with Horizontal():
                # Couverture
                if self._config.show_cover:
                    yield MangaCover(
                        self._manga.cover_url,
                        width=15,
                        height=5,
                        id="card-cover",
                    )

                # Informations
                with Vertical():
                    # Indicateur de sélection
                    if self._config.selectable:
                        indicator = "✓" if self._card_state.selected else "○"
                        yield Static(indicator, classes="selection-indicator")

                    yield MangaInfo(self._manga, config=self._config, id="card-info")

                    # Badges
                    if self._config.show_badges:
                        yield MangaBadges(self._manga, config=self._config, id="card-badges")

                    # Progression
                    if self._config.show_progress and self._manga.reading_progress:
                        progress = self._manga.reading_progress
                        yield MangaProgressBar(
                            progress=progress.progress_percentage,
                            chapters_read=progress.chapters_read,
                            total_chapters=progress.total_chapters,
                            id="card-progress",
                        )

        def _compose_compact(self) -> ComposeResult:
            """Compose le layout compact (texte seul)."""
            with Horizontal():
                # Indicateur de sélection
                if self._config.selectable:
                    indicator = "✓" if self._card_state.selected else "○"
                    yield Static(indicator, classes="selection-indicator")

                # Titre
                title = truncate(self._manga.title, self._config.max_title_length)
                yield Static(title, classes="manga-title")

                # Métadonnées
                meta = format_manga_metadata(self._manga, compact=True)
                if meta:
                    yield Static(f"  {meta}", classes="manga-meta")

        def _compose_detailed(self) -> ComposeResult:
            """Compose le layout détaillé (cover + toutes les infos + description)."""
            # Indicateur de sélection
            if self._config.selectable:
                indicator = "✓" if self._card_state.selected else "○"
                yield Static(indicator, classes="selection-indicator")

            # Couverture
            if self._config.show_cover:
                yield MangaCover(
                    self._manga.cover_url,
                    width=self._config.width - 2,
                    height=14,
                    id="card-cover",
                )

            # Informations
            yield MangaInfo(self._manga, config=self._config, id="card-info")

            # Badges
            if self._config.show_badges:
                yield MangaBadges(self._manga, config=self._config, id="card-badges")

            # Progression
            if self._config.show_progress and self._manga.reading_progress:
                progress = self._manga.reading_progress
                yield MangaProgressBar(
                    progress=progress.progress_percentage,
                    chapters_read=progress.chapters_read,
                    total_chapters=progress.total_chapters,
                    id="card-progress",
                )

            # Description
            if self._config.show_description and self._manga.description:
                description = truncate(
                    self._manga.description,
                    self._config.max_description_length,
                )
                yield Static(description, id="card-description")

        # =====================================================================
        # GESTION DES ÉVÉNEMENTS
        # =====================================================================

        def on_click(self) -> None:
            """Gère le clic sur la carte."""
            if not self._config.clickable:
                return

            if self._card_state.state == CardState.DISABLED:
                return

            current_time = asyncio.get_event_loop().time()

            # Détecter le double-clic
            if current_time - self._last_click_time < 0.5:
                self._handle_double_click()
            else:
                self._handle_single_click()

            self._last_click_time = current_time

        def _handle_single_click(self) -> None:
            """Gère un clic simple."""
            if self._config.selectable:
                self.toggle_selection()
            else:
                # Émettre le message de clic
                self.post_message(MangaCardClicked(self._manga.id, self._manga))

                # Callback
                if self._on_click is not None:
                    try:
                        self._on_click(self._manga)
                    except Exception as e:
                        logger.error("Erreur dans le callback on_click: {}", e)

        def _handle_double_click(self) -> None:
            """Gère un double-clic."""
            self.post_message(MangaCardDoubleClicked(self._manga.id, self._manga))

            if self._on_double_click is not None:
                try:
                    self._on_double_click(self._manga)
                except Exception as e:
                    logger.error("Erreur dans le callback on_double_click: {}", e)

        def on_enter(self) -> None:
            """Gère l'entrée de la souris sur la carte."""
            self._card_state.hovered = True
            self.state = CardState.HOVER

        def on_leave(self) -> None:
            """Gère la sortie de la souris de la carte."""
            self._card_state.hovered = False
            self.state = (
                CardState.SELECTED if self._card_state.selected else CardState.NORMAL
            )

        # =====================================================================
        # API PUBLIQUE — État
        # =====================================================================

        def set_selected(self, selected: bool) -> None:
            """Définit l'état de sélection.

            Args:
                selected: True si sélectionné.
            """
            if not self._config.selectable:
                return

            self._card_state.selected = selected

            if selected:
                self.add_class("state-selected")
                self.remove_class("state-normal")
            else:
                self.remove_class("state-selected")
                if not self._card_state.hovered:
                    self.add_class("state-normal")

            # Mettre à jour l'indicateur de sélection
            try:
                indicator = self.query_one(".selection-indicator", Static)
                indicator.update("✓" if selected else "○")
                if selected:
                    indicator.add_class("selected")
                else:
                    indicator.remove_class("selected")
            except Exception:
                pass

            # Émettre un message
            self.post_message(MangaCardSelected(self._manga.id, selected))

            # Callback
            if self._on_select is not None:
                try:
                    self._on_select(self._manga, selected)
                except Exception as e:
                    logger.error("Erreur dans le callback on_select: {}", e)

        def toggle_selection(self) -> None:
            """Bascule l'état de sélection."""
            self.set_selected(not self._card_state.selected)

        def set_state(self, state: CardState) -> None:
            """Définit l'état visuel.

            Args:
                state: Nouvel état.
            """
            # Retirer les anciennes classes
            for s in CardState:
                self.remove_class(s.css_class)

            # Définir le nouvel état
            self._card_state.state = state
            self.state = state

            # Ajouter la nouvelle classe
            self.add_class(state.css_class)

        def set_disabled(self, disabled: bool) -> None:
            """Définit l'état désactivé.

            Args:
                disabled: True si désactivé.
            """
            if disabled:
                self.set_state(CardState.DISABLED)
            else:
                self.set_state(CardState.NORMAL)

        def update_manga(self, manga: Manga) -> None:
            """Met à jour le manga affiché.

            Args:
                manga: Nouveau manga.
            """
            self._manga = manga
            # TODO: Mettre à jour les widgets enfants
            self.refresh()

        # =====================================================================
        # API PUBLIQUE — Accès aux données
        # =====================================================================

        @property
        def manga(self) -> Manga:
            """Manga affiché."""
            return self._manga

        @property
        def manga_id(self) -> str:
            """ID du manga."""
            return self._manga.id

        @property
        def config(self) -> MangaCardConfig:
            """Configuration de la carte."""
            return self._config

        @property
        def card_state(self) -> MangaCardState:
            """État de la carte."""
            return self._card_state

        @property
        def is_selected(self) -> bool:
            """Indique si la carte est sélectionnée."""
            return self._card_state.selected

        @property
        def is_disabled(self) -> bool:
            """Indique si la carte est désactivée."""
            return self._card_state.state == CardState.DISABLED

        # =====================================================================
        # ACTIONS — Bindings clavier
        # =====================================================================

        def action_activate(self) -> None:
            """Action : activer la carte (Enter)."""
            self._handle_single_click()

        def action_toggle_select(self) -> None:
            """Action : basculer la sélection (Space)."""
            if self._config.selectable:
                self.toggle_selection()


# ============================================================================
# HELPERS PUBLICS
# ============================================================================


def create_manga_card(
    manga: Manga,
    *,
    layout: CardLayout = CardLayout.VERTICAL,
    selectable: bool = False,
    on_click: Callable[[Manga], None] | None = None,
    on_select: Callable[[Manga, bool], None] | None = None,
    **kwargs: Any,
) -> Any:
    """Crée une carte manga avec une configuration simplifiée.

    Fonction utilitaire pour créer rapidement une MangaCard.

    Args:
        manga: Manga à afficher.
        layout: Layout de la carte.
        selectable: Permettre la sélection.
        on_click: Callback lors du clic.
        on_select: Callback lors de la sélection.
        **kwargs: Arguments additionnels pour MangaCardConfig.

    Returns:
        Instance de MangaCard.
    """
    config = MangaCardConfig(
        layout=layout,
        selectable=selectable,
        **kwargs,
    )
    return MangaCard(
        manga=manga,
        config=config,
        on_click=on_click,
        on_select=on_select,
    )


def format_manga_summary(manga: Manga, *, max_length: int = 200) -> str:
    """Formate un résumé court d'un manga.

    Args:
        manga: Manga à formater.
        max_length: Longueur maximale.

    Returns:
        Résumé formaté.
    """
    parts: list[str] = []

    # Titre
    parts.append(f"[b]{manga.title}[/b]")

    # Auteur
    if manga.author:
        parts.append(f"by {manga.author}")

    # Année
    if manga.year:
        parts.append(f"({manga.year})")

    # Statut
    if manga.status != MangaStatus.UNKNOWN:
        parts.append(f"[{manga.status.label}]")

    # Langue
    if manga.language != Language.EN:
        parts.append(f"{manga.language.flag} {manga.language.value.upper()}")

    result = " ".join(parts)

    # Description
    if manga.description:
        desc = truncate(manga.description, max_length)
        result += f"\n\n{desc}"

    return result


def get_card_dimensions(layout: CardLayout, *, custom_width: int | None = None, custom_height: int | None = None) -> tuple[int, int]:
    """Retourne les dimensions recommandées pour un layout.

    Args:
        layout: Layout de la carte.
        custom_width: Largeur personnalisée (optionnelle).
        custom_height: Hauteur personnalisée (optionnelle).

    Returns:
        Tuple (width, height).
    """
    width = custom_width or layout.default_width
    height = custom_height or layout.default_height
    return (width, height)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_CARD_WIDTH",
    "DEFAULT_CARD_HEIGHT",
    "MAX_TITLE_LENGTH",
    "MAX_AUTHOR_LENGTH",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_TAGS_DISPLAYED",
    "COVER_PLACEHOLDER",
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
    "MangaCardState",
    # Helpers
    "get_status_badge",
    "get_language_badge",
    "get_content_rating_badge",
    "format_manga_metadata",
    "format_tags",
    "render_progress_bar",
    "render_cover_placeholder",
    "create_manga_card",
    "format_manga_summary",
    "get_card_dimensions",
    # Widget principal
    "MangaCard" if TEXTUAL_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
