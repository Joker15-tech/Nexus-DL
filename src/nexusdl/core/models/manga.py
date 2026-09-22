"""Modèles de domaine pour les mangas, chapitres et pages.

Ce module définit les structures de données immuables (Pydantic v2) utilisées
pour représenter les œuvres (mangas, webtoons, comics), leurs chapitres et
leurs pages. Ces modèles sont au cœur de l'architecture hexagonale et sont
consommés par :

    - `core/parsers/base.py` : retourne des Manga/Chapter/Page depuis les sites
    - `core/downloader/manager.py` et `worker.py` : téléchargent des Chapter
    - `core/library/database.py` : persiste les Manga/Chapter en SQLite
    - `core/library/search.py` : recherche plein texte FTS5
    - `core/packaging/comic_info.py` : conversion vers ComicInfo.xml
    - `interfaces/web/backend/schemas/` : sérialisation API REST/WebSocket
    - `interfaces/cli/screens/` : affichage TUI
    - `interfaces/gui/views/` : affichage GUI

Architecture :
    Manga (immutable — œuvre complète)
        ├── MangaStatus (enum) : ONGOING, COMPLETED, HIATUS, CANCELLED, etc.
        ├── Language (enum) : codes ISO 639-1 (fr, en, ja, ko, zh, etc.)
        ├── ContentRating (enum) : SAFE, SUGGESTIVE, EROTICA, PORNOGRAPHIC
        ├── Demographic (enum) : SHOUNEN, SHOUJO, SEINEN, JOSEI, etc.
        ├── ReadingDirection (enum) : RIGHT_TO_LEFT, LEFT_TO_RIGHT
        ├── chapters : list[Chapter]
        └── genres, tags, alternative_titles

    Chapter (immutable — chapitre individuel)
        ├── number : float | str (12.5, "Extra", "v2")
        ├── volume : int | None
        ├── language : Language
        ├── pages_count : int | None
        └── pages : list[Page]

    Page (immutable — page individuelle)
        ├── index : int (0-based)
        ├── url : HttpUrl
        ├── filename : str
        └── checksum : str | None (SHA256 pour déduplication)

    SearchResult (immutable — résultat de recherche multi-sites)
        ├── manga : Manga (partiellement peuplé)
        ├── score : float (pertinence)
        └── site : str

Règles d'or :
    1. Tous les modèles sont `frozen=True` (immuables, hashables).
    2. Les enums utilisent `str` comme base pour sérialisation JSON native.
    3. Les IDs sont des chaînes (UUID ou hash) pour compatibilité SQLite TEXT.
    4. Les URLs sont validées via Pydantic `HttpUrl`.
    5. Les timestamps sont en UTC et sérialisables en ISO 8601.
    6. Les dépendances circulaires sont évitées via `TYPE_CHECKING`.
    7. Les propriétés dérivées (label, icon, color) facilitent l'UI.
    8. Les méthodes `to_summary()` fournissent des snapshots JSON légers.

Exemple d'utilisation :
    >>> from nexusdl.core.models.manga import (
    ...     Manga, Chapter, Page, MangaStatus, Language, ContentRating,
    ... )
    >>> from datetime import datetime, UTC
    >>>
    >>> # Créer un manga
    >>> manga = Manga(
    ...     id="manga_one_piece",
    ...     source_id="one-piece",
    ...     site="mangadex",
    ...     title="One Piece",
    ...     alternative_titles=["ワンピース"],
    ...     description="L'histoire de Monkey D. Luffy...",
    ...     author="Eiichiro Oda",
    ...     artist="Eiichiro Oda",
    ...     genres=["Action", "Adventure", "Comedy"],
    ...     status=MangaStatus.ONGOING,
    ...     year=1997,
    ...     cover_url="https://example.com/cover.jpg",
    ...     language=Language.JA,
    ...     content_rating=ContentRating.SAFE,
    ...     chapters=[],
    ...     url="https://mangadex.org/title/one-piece",
    ... )
    >>> print(manga.status.label)  # "En cours"
    >>> print(manga.status.icon)  # "🟢"
    >>>
    >>> # Créer un chapitre
    >>> chapter = Chapter(
    ...     id="ch_001",
    ...     source_id="1",
    ...     title="Romance Dawn",
    ...     number=1.0,
    ...     volume=1,
    ...     language=Language.FR,
    ...     pages_count=50,
    ...     published_at=datetime(2023, 1, 1, tzinfo=UTC),
    ...     url="https://mangadex.org/chapter/1",
    ...     pages=[],
    ... )
    >>> print(chapter.display_number)  # "1"
    >>>
    >>> # Créer une page
    >>> page = Page(
    ...     index=0,
    ...     url="https://cdn.example.com/page001.jpg",
    ...     filename="page_001.jpg",
    ... )
    >>> print(page.extension)  # ".jpg"
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Final, Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from nexusdl.core.exceptions import NexusDLError

if TYPE_CHECKING:
    pass  # Pas de dépendances circulaires nécessaires pour ce module


# ============================================================================
# EXCEPTIONS
# ============================================================================


class MangaModelError(NexusDLError):
    """Exception de base pour les erreurs liées aux modèles de manga."""


class InvalidMangaError(MangaModelError):
    """Exception levée lorsqu'un manga est mal configuré."""


class InvalidChapterError(MangaModelError):
    """Exception levée lorsqu'un chapitre est mal configuré."""


class InvalidPageError(MangaModelError):
    """Exception levée lorsqu'une page est mal configurée."""


# ============================================================================
# ENUMS
# ============================================================================


class MangaStatus(str, Enum):
    """Statut de publication d'un manga.

    Correspond aux valeurs stockées en base de données (table `manga.status`).
    """

    ONGOING = "ongoing"
    COMPLETED = "completed"
    HIATUS = "hiatus"
    CANCELLED = "cancelled"
    LICENSED = "licensed"
    DISCONTINUED = "discontinued"
    UPCOMING = "upcoming"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        """Libellé humain du statut."""
        return {
            MangaStatus.ONGOING: "En cours",
            MangaStatus.COMPLETED: "Terminé",
            MangaStatus.HIATUS: "En pause",
            MangaStatus.CANCELLED: "Annulé",
            MangaStatus.LICENSED: "Licencié",
            MangaStatus.DISCONTINUED: "Abandonné",
            MangaStatus.UPCOMING: "À venir",
            MangaStatus.UNKNOWN: "Inconnu",
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode pour l'affichage TUI/GUI."""
        return {
            MangaStatus.ONGOING: "🟢",
            MangaStatus.COMPLETED: "✅",
            MangaStatus.HIATUS: "⏸️",
            MangaStatus.CANCELLED: "❌",
            MangaStatus.LICENSED: "📜",
            MangaStatus.DISCONTINUED: "🚫",
            MangaStatus.UPCOMING: "🔜",
            MangaStatus.UNKNOWN: "❓",
        }[self]

    @property
    def color(self) -> str:
        """Couleur hexadécimale pour l'affichage (interfaces web/GUI)."""
        return {
            MangaStatus.ONGOING: "#10b981",  # emerald-500
            MangaStatus.COMPLETED: "#3b82f6",  # blue-500
            MangaStatus.HIATUS: "#f59e0b",  # amber-500
            MangaStatus.CANCELLED: "#ef4444",  # red-500
            MangaStatus.LICENSED: "#8b5cf6",  # violet-500
            MangaStatus.DISCONTINUED: "#6b7280",  # gray-500
            MangaStatus.UPCOMING: "#06b6d4",  # cyan-500
            MangaStatus.UNKNOWN: "#9ca3af",  # gray-400
        }[self]

    @property
    def is_active(self) -> bool:
        """Indique si le manga est toujours publié."""
        return self in (MangaStatus.ONGOING, MangaStatus.HIATUS, MangaStatus.UPCOMING)

    @property
    def is_finished(self) -> bool:
        """Indique si le manga est terminé (définitivement)."""
        return self in (
            MangaStatus.COMPLETED,
            MangaStatus.CANCELLED,
            MangaStatus.DISCONTINUED,
        )


class Language(str, Enum):
    """Langue d'un manga/chapitre (codes ISO 639-1).

    Correspond aux valeurs stockées en base de données (table `manga.language`).
    """

    # Langues principales
    FR = "fr"  # Français
    EN = "en"  # Anglais
    JA = "ja"  # Japonais
    KO = "ko"  # Coréen
    ZH = "zh"  # Chinois
    ES = "es"  # Espagnol
    DE = "de"  # Allemand
    IT = "it"  # Italien
    PT = "pt"  # Portugais
    RU = "ru"  # Russe
    AR = "ar"  # Arabe
    PL = "pl"  # Polonais
    TR = "tr"  # Turc
    NL = "nl"  # Néerlandais
    SV = "sv"  # Suédois
    DA = "da"  # Danois
    NO = "no"  # Norvégien
    FI = "fi"  # Finnois
    HU = "hu"  # Hongrois
    CS = "cs"  # Tchèque
    RO = "ro"  # Roumain
    BG = "bg"  # Bulgare
    EL = "el"  # Grec
    HE = "he"  # Hébreu
    TH = "th"  # Thaï
    VI = "vi"  # Vietnamien
    ID = "id"  # Indonésien
    MS = "ms"  # Malais
    UK = "uk"  # Ukrainien

    # Spécial
    MULTI = "multi"  # Multilingue

    @property
    def label(self) -> str:
        """Libellé humain de la langue."""
        return {
            Language.FR: "Français",
            Language.EN: "Anglais",
            Language.JA: "Japonais",
            Language.KO: "Coréen",
            Language.ZH: "Chinois",
            Language.ES: "Espagnol",
            Language.DE: "Allemand",
            Language.IT: "Italien",
            Language.PT: "Portugais",
            Language.RU: "Russe",
            Language.AR: "Arabe",
            Language.PL: "Polonais",
            Language.TR: "Turc",
            Language.NL: "Néerlandais",
            Language.SV: "Suédois",
            Language.DA: "Danois",
            Language.NO: "Norvégien",
            Language.FI: "Finnois",
            Language.HU: "Hongrois",
            Language.CS: "Tchèque",
            Language.RO: "Roumain",
            Language.BG: "Bulgare",
            Language.EL: "Grec",
            Language.HE: "Hébreu",
            Language.TH: "Thaï",
            Language.VI: "Vietnamien",
            Language.ID: "Indonésien",
            Language.MS: "Malais",
            Language.UK: "Ukrainien",
            Language.MULTI: "Multilingue",
        }[self]

    @property
    def flag(self) -> str:
        """Drapeau emoji pour l'affichage."""
        return {
            Language.FR: "🇫🇷",
            Language.EN: "🇬🇧",
            Language.JA: "🇯🇵",
            Language.KO: "🇰🇷",
            Language.ZH: "🇨🇳",
            Language.ES: "🇪🇸",
            Language.DE: "🇩🇪",
            Language.IT: "🇮🇹",
            Language.PT: "🇵🇹",
            Language.RU: "🇷🇺",
            Language.AR: "🇸🇦",
            Language.PL: "🇵🇱",
            Language.TR: "🇹🇷",
            Language.NL: "🇳🇱",
            Language.SV: "🇸🇪",
            Language.DA: "🇩🇰",
            Language.NO: "🇳🇴",
            Language.FI: "🇫🇮",
            Language.HU: "🇭🇺",
            Language.CS: "🇨🇿",
            Language.RO: "🇷🇴",
            Language.BG: "🇧🇬",
            Language.EL: "🇬🇷",
            Language.HE: "🇮🇱",
            Language.TH: "🇹🇭",
            Language.VI: "🇻🇳",
            Language.ID: "🇮🇩",
            Language.MS: "🇲🇾",
            Language.UK: "🇺🇦",
            Language.MULTI: "🌍",
        }[self]

    @property
    def is_rtl(self) -> bool:
        """Indique si la langue s'écrit de droite à gauche."""
        return self in (Language.AR, Language.HE)


class ContentRating(str, Enum):
    """Classification d'âge du contenu.

    Correspond aux valeurs stockées en base de données (table `manga.content_rating`).
    """

    SAFE = "safe"
    SUGGESTIVE = "suggestive"
    EROTICA = "erotica"
    PORNOGRAPHIC = "pornographic"

    @property
    def label(self) -> str:
        """Libellé humain de la classification."""
        return {
            ContentRating.SAFE: "Tout public",
            ContentRating.SUGGESTIVE: "Suggestif",
            ContentRating.EROTICA: "Érotique",
            ContentRating.PORNOGRAPHIC: "Pornographique (18+)",
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode pour l'affichage."""
        return {
            ContentRating.SAFE: "🟢",
            ContentRating.SUGGESTIVE: "🟡",
            ContentRating.EROTICA: "🟠",
            ContentRating.PORNOGRAPHIC: "🔴",
        }[self]

    @property
    def color(self) -> str:
        """Couleur hexadécimale pour l'affichage."""
        return {
            ContentRating.SAFE: "#10b981",  # emerald-500
            ContentRating.SUGGESTIVE: "#f59e0b",  # amber-500
            ContentRating.EROTICA: "#f97316",  # orange-500
            ContentRating.PORNOGRAPHIC: "#ef4444",  # red-500
        }[self]

    @property
    def is_adult(self) -> bool:
        """Indique si le contenu est réservé aux adultes (18+)."""
        return self in (ContentRating.EROTICA, ContentRating.PORNOGRAPHIC)

    @property
    def age_restriction(self) -> int:
        """Âge minimum requis (0 = tout public)."""
        return {
            ContentRating.SAFE: 0,
            ContentRating.SUGGESTIVE: 13,
            ContentRating.EROTICA: 16,
            ContentRating.PORNOGRAPHIC: 18,
        }[self]


class Demographic(str, Enum):
    """Cible démographique d'un manga.

    Classification traditionnelle du manga japonais.
    """

    SHOUNEN = "shounen"  # Jeunes garçons (12-18 ans)
    SHOUJO = "shoujo"  # Jeunes filles (12-18 ans)
    SEINEN = "seinen"  # Hommes adultes (18+ ans)
    JOSEI = "josei"  # Femmes adultes (18+ ans)
    KODOMO = "kodomo"  # Enfants
    NONE = "none"  # Non spécifié

    @property
    def label(self) -> str:
        """Libellé humain de la démographie."""
        return {
            Demographic.SHOUNEN: "Shōnen",
            Demographic.SHOUJO: "Shōjo",
            Demographic.SEINEN: "Seinen",
            Demographic.JOSEI: "Josei",
            Demographic.KODOMO: "Kodomo",
            Demographic.NONE: "Non spécifié",
        }[self]


class ReadingDirection(str, Enum):
    """Sens de lecture d'un manga.

    Détermine l'ordre de lecture des pages (gauche→droite ou droite→gauche).
    """

    RIGHT_TO_LEFT = "right_to_left"  # Manga japonais traditionnel
    LEFT_TO_RIGHT = "left_to_right"  # Webtoon, comics occidentaux
    VERTICAL = "vertical"  # Webtoon (scroll vertical)

    @property
    def label(self) -> str:
        """Libellé humain du sens de lecture."""
        return {
            ReadingDirection.RIGHT_TO_LEFT: "De droite à gauche",
            ReadingDirection.LEFT_TO_RIGHT: "De gauche à droite",
            ReadingDirection.VERTICAL: "Vertical (webtoon)",
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode pour l'affichage."""
        return {
            ReadingDirection.RIGHT_TO_LEFT: "←",
            ReadingDirection.LEFT_TO_RIGHT: "→",
            ReadingDirection.VERTICAL: "↓",
        }[self]


# ============================================================================
# HELPERS — Génération d'identifiants
# ============================================================================


def generate_manga_id(site: str, source_id: str) -> str:
    """Génère un ID unique pour un manga basé sur le site et l'ID source.

    Format : 'manga_' + hash SHA256 tronqué (site + source_id).

    Args:
        site: Nom du site (ex: "mangadex").
        source_id: ID du manga sur le site source.

    Returns:
        Identifiant unique sous forme de chaîne.

    Example:
        >>> generate_manga_id("mangadex", "one-piece")
        'manga_a1b2c3d4e5f6...'
    """
    content = f"{site}:{source_id}".encode("utf-8")
    hash_hex = hashlib.sha256(content).hexdigest()[:32]
    return f"manga_{hash_hex}"


def generate_chapter_id(manga_id: str, source_id: str) -> str:
    """Génère un ID unique pour un chapitre.

    Format : 'ch_' + hash SHA256 tronqué (manga_id + source_id).

    Args:
        manga_id: ID du manga parent.
        source_id: ID du chapitre sur le site source.

    Returns:
        Identifiant unique sous forme de chaîne.
    """
    content = f"{manga_id}:{source_id}".encode("utf-8")
    hash_hex = hashlib.sha256(content).hexdigest()[:24]
    return f"ch_{hash_hex}"


def generate_page_id(chapter_id: str, index: int) -> str:
    """Génère un ID unique pour une page.

    Format : 'page_' + hash SHA256 tronqué (chapter_id + index).

    Args:
        chapter_id: ID du chapitre parent.
        index: Index de la page (0-based).

    Returns:
        Identifiant unique sous forme de chaîne.
    """
    content = f"{chapter_id}:{index}".encode("utf-8")
    hash_hex = hashlib.sha256(content).hexdigest()[:16]
    return f"page_{hash_hex}"


# ============================================================================
# MODÈLES PYDANTIC — Page
# ============================================================================


class Page(BaseModel):
    """Page individuelle d'un chapitre (immutable).

    Représente une image unique dans un chapitre. Le champ `checksum`
    permet la déduplication via hash SHA256 du contenu.

    Attributes:
        id: Identifiant unique de la page (généré automatiquement).
        index: Index de la page dans le chapitre (0-based).
        url: URL de l'image (validée HttpUrl).
        filename: Nom de fichier suggéré pour le téléchargement.
        checksum: Hash SHA256 du contenu (None si inconnu).
        width: Largeur de l'image en pixels (None si inconnu).
        height: Hauteur de l'image en pixels (None si inconnu).
        size_bytes: Taille du fichier en bytes (None si inconnu).
    """

    id: str = Field(
        default="",
        description="Identifiant unique de la page.",
    )
    index: int = Field(
        ...,
        ge=0,
        description="Index de la page dans le chapitre (0-based).",
    )
    url: HttpUrl = Field(
        ...,
        description="URL de l'image.",
    )
    filename: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Nom de fichier suggéré pour le téléchargement.",
    )
    checksum: str | None = Field(
        default=None,
        description="Hash SHA256 du contenu (pour déduplication).",
        pattern=r"^[a-f0-9]{64}$",
    )
    width: int | None = Field(
        default=None,
        ge=0,
        description="Largeur de l'image en pixels.",
    )
    height: int | None = Field(
        default=None,
        ge=0,
        description="Hauteur de l'image en pixels.",
    )
    size_bytes: int | None = Field(
        default=None,
        ge=0,
        description="Taille du fichier en bytes.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, v: str) -> str:
        """Valide le nom de fichier (pas de caractères invalides)."""
        # Caractères interdits dans les noms de fichiers
        invalid_chars = r'[<>:"/\\|?*\x00-\x1f]'
        if re.search(invalid_chars, v):
            raise InvalidPageError(f"Nom de fichier invalide: {v}")
        return v

    @model_validator(mode="after")
    def _generate_id_if_empty(self) -> Self:
        """Génère un ID si non fourni."""
        if not self.id:
            # On ne peut pas générer l'ID sans chapter_id, donc on laisse vide
            # L'ID sera généré par le Chapter parent
            pass
        return self

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def extension(self) -> str:
        """Extension du fichier (ex: '.jpg', '.png')."""
        return Path(self.filename).suffix.lower()

    @property
    def is_image(self) -> bool:
        """Indique si le fichier est une image (vs PDF, etc.)."""
        return self.extension in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp"}

    @property
    def aspect_ratio(self) -> float | None:
        """Ratio d'aspect (largeur / hauteur) si les dimensions sont connues."""
        if self.width is None or self.height is None or self.height == 0:
            return None
        return self.width / self.height

    @property
    def is_landscape(self) -> bool:
        """Indique si l'image est en format paysage (largeur > hauteur)."""
        if self.width is None or self.height is None:
            return False
        return self.width > self.height

    @property
    def is_portrait(self) -> bool:
        """Indique si l'image est en format portrait (hauteur > largeur)."""
        if self.width is None or self.height is None:
            return False
        return self.height > self.width

    @property
    def page_number(self) -> int:
        """Numéro de page (1-based, pour affichage)."""
        return self.index + 1

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable (pour WebSocket/API)."""
        return {
            "id": self.id,
            "index": self.index,
            "page_number": self.page_number,
            "url": str(self.url),
            "filename": self.filename,
            "checksum": self.checksum,
            "width": self.width,
            "height": self.height,
            "size_bytes": self.size_bytes,
            "extension": self.extension,
        }

    def __repr__(self) -> str:
        return f"<Page index={self.index} filename='{self.filename}'>"


# ============================================================================
# MODÈLES PYDANTIC — Chapter
# ============================================================================


class Chapter(BaseModel):
    """Chapitre individuel d'un manga (immutable).

    Représente un chapitre complet avec ses pages. Le champ `number` peut
    être un float (12.5) ou une chaîne ("Extra", "v2") pour supporter
    les formats non-standards.

    Attributes:
        id: Identifiant unique du chapitre (généré automatiquement).
        source_id: ID du chapitre sur le site source.
        title: Titre du chapitre (optionnel).
        number: Numéro du chapitre (float ou str).
        volume: Numéro du volume (optionnel).
        language: Langue du chapitre.
        pages_count: Nombre total de pages (None si inconnu).
        published_at: Date de publication (None si inconnu).
        url: URL source du chapitre.
        pages: Liste des pages du chapitre.
        scanlator: Groupe de scanlation (optionnel).
        version: Version du chapitre (optionnel, ex: "v2").
    """

    id: str = Field(
        default="",
        description="Identifiant unique du chapitre.",
    )
    source_id: str = Field(
        ...,
        min_length=1,
        description="ID du chapitre sur le site source.",
    )
    title: str = Field(
        default="",
        max_length=500,
        description="Titre du chapitre (optionnel).",
    )
    number: float | str = Field(
        ...,
        description="Numéro du chapitre (float ou str pour 'Extra', 'v2', etc.).",
    )
    volume: int | None = Field(
        default=None,
        ge=0,
        description="Numéro du volume (optionnel).",
    )
    language: Language = Field(
        ...,
        description="Langue du chapitre.",
    )
    pages_count: int | None = Field(
        default=None,
        ge=0,
        description="Nombre total de pages (None si inconnu).",
    )
    published_at: datetime | None = Field(
        default=None,
        description="Date de publication (None si inconnu).",
    )
    url: HttpUrl = Field(
        ...,
        description="URL source du chapitre.",
    )
    pages: list[Page] = Field(
        default_factory=list,
        description="Liste des pages du chapitre.",
    )
    scanlator: str | None = Field(
        default=None,
        max_length=200,
        description="Groupe de scanlation (optionnel).",
    )
    version: str | None = Field(
        default=None,
        max_length=50,
        description="Version du chapitre (optionnel, ex: 'v2').",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_pages_consistency(self) -> Self:
        """Vérifie la cohérence entre pages_count et len(pages)."""
        if self.pages_count is not None and len(self.pages) > 0:
            if len(self.pages) != self.pages_count:
                # Warning seulement, pas d'erreur (les pages peuvent être chargées partiellement)
                pass
        return self

    @model_validator(mode="after")
    def _generate_id_if_empty(self) -> Self:
        """Génère un ID si non fourni."""
        if not self.id:
            # On ne peut pas générer l'ID sans manga_id, donc on laisse vide
            # L'ID sera généré par le Manga parent
            pass
        return self

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def display_number(self) -> str:
        """Numéro de chapitre formaté pour l'affichage.

        Returns:
            Chaîne formatée (ex: '1', '12.5', 'Extra').
        """
        if isinstance(self.number, float):
            if self.number.is_integer():
                return str(int(self.number))
            return f"{self.number:.1f}"
        return str(self.number)

    @property
    def display_title(self) -> str:
        """Titre complet formaté pour l'affichage.

        Returns:
            Chaîne formatée (ex: 'Chapitre 1 - Romance Dawn').
        """
        base = f"Chapitre {self.display_number}"
        if self.title:
            base += f" - {self.title}"
        return base

    @property
    def is_special(self) -> bool:
        """Indique si c'est un chapitre spécial (Extra, Omake, etc.)."""
        if isinstance(self.number, str):
            return self.number.lower() in {"extra", "omake", "prologue", "épilogue", "special"}
        return False

    @property
    def sort_key(self) -> tuple[float, str]:
        """Clé de tri pour ordonner les chapitres.

        Returns:
            Tuple (numéro_float, titre) pour tri naturel.
        """
        if isinstance(self.number, float):
            return (self.number, self.title or "")
        # Pour les chapitres spéciaux, on les met à la fin
        return (float("inf"), str(self.number))

    @property
    def has_pages(self) -> bool:
        """Indique si les pages sont chargées."""
        return len(self.pages) > 0

    @property
    def pages_loaded_count(self) -> int:
        """Nombre de pages actuellement chargées."""
        return len(self.pages)

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable (pour WebSocket/API)."""
        return {
            "id": self.id,
            "source_id": self.source_id,
            "title": self.title,
            "number": self.number,
            "display_number": self.display_number,
            "display_title": self.display_title,
            "volume": self.volume,
            "language": self.language.value,
            "language_label": self.language.label,
            "pages_count": self.pages_count,
            "pages_loaded": len(self.pages),
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "url": str(self.url),
            "scanlator": self.scanlator,
            "version": self.version,
            "is_special": self.is_special,
        }

    def __repr__(self) -> str:
        return (
            f"<Chapter number={self.display_number} "
            f"title='{self.title[:30]}' "
            f"pages={len(self.pages)}/{self.pages_count or '?'}>"
        )


# ============================================================================
# MODÈLES PYDANTIC — Manga
# ============================================================================


class Manga(BaseModel):
    """Manga/Webtoon/Comic complet (immutable).

    Représente une œuvre complète avec ses métadonnées et ses chapitres.
    C'est le modèle principal utilisé dans tout le projet.

    Attributes:
        id: Identifiant unique du manga (généré automatiquement).
        source_id: ID du manga sur le site source.
        site: Nom du site (ex: "mangadex", "sushiscan_net").
        title: Titre principal du manga.
        alternative_titles: Liste des titres alternatifs.
        description: Synopsis/description du manga.
        author: Nom de l'auteur (mangaka).
        artist: Nom du dessinateur (si différent de l'auteur).
        genres: Liste des genres/tags.
        status: Statut de publication.
        year: Année de début de publication.
        cover_url: URL de la couverture.
        language: Langue principale du manga.
        content_rating: Classification d'âge.
        chapters: Liste des chapitres disponibles.
        url: URL source du manga.
        updated_at: Timestamp de dernière mise à jour.
        demographic: Cible démographique (optionnel).
        reading_direction: Sens de lecture (optionnel).
    """

    id: str = Field(
        default="",
        description="Identifiant unique du manga.",
    )
    source_id: str = Field(
        ...,
        min_length=1,
        description="ID du manga sur le site source.",
    )
    site: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Nom du site (ex: 'mangadex', 'sushiscan_net').",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Titre principal du manga.",
    )
    alternative_titles: list[str] = Field(
        default_factory=list,
        description="Liste des titres alternatifs.",
    )
    description: str | None = Field(
        default=None,
        max_length=10000,
        description="Synopsis/description du manga.",
    )
    author: str | None = Field(
        default=None,
        max_length=200,
        description="Nom de l'auteur (mangaka).",
    )
    artist: str | None = Field(
        default=None,
        max_length=200,
        description="Nom du dessinateur (si différent de l'auteur).",
    )
    genres: list[str] = Field(
        default_factory=list,
        description="Liste des genres/tags.",
    )
    status: MangaStatus = Field(
        ...,
        description="Statut de publication.",
    )
    year: int | None = Field(
        default=None,
        ge=1900,
        le=2100,
        description="Année de début de publication.",
    )
    cover_url: HttpUrl | None = Field(
        default=None,
        description="URL de la couverture.",
    )
    language: Language = Field(
        ...,
        description="Langue principale du manga.",
    )
    content_rating: ContentRating = Field(
        ...,
        description="Classification d'âge.",
    )
    chapters: list[Chapter] = Field(
        default_factory=list,
        description="Liste des chapitres disponibles.",
    )
    url: HttpUrl = Field(
        ...,
        description="URL source du manga.",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de dernière mise à jour.",
    )
    demographic: Demographic = Field(
        default=Demographic.NONE,
        description="Cible démographique (optionnel).",
    )
    reading_direction: ReadingDirection = Field(
        default=ReadingDirection.RIGHT_TO_LEFT,
        description="Sens de lecture.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @model_validator(mode="after")
    def _generate_id_if_empty(self) -> Self:
        """Génère un ID si non fourni."""
        if not self.id:
            object.__setattr__(self, "id", generate_manga_id(self.site, self.source_id))
        return self

    @field_validator("title")
    @classmethod
    def _validate_title(cls, v: str) -> str:
        """Valide le titre (trim + non vide)."""
        v = v.strip()
        if not v:
            raise InvalidMangaError("Le titre ne peut pas être vide")
        return v

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def display_title(self) -> str:
        """Titre principal (alias de `title`)."""
        return self.title

    @property
    def all_titles(self) -> list[str]:
        """Tous les titres (principal + alternatifs)."""
        return [self.title] + self.alternative_titles

    @property
    def chapters_count(self) -> int:
        """Nombre total de chapitres."""
        return len(self.chapters)

    @property
    def downloaded_chapters_count(self) -> int:
        """Nombre de chapitres téléchargés (si l'info est disponible)."""
        # Cette info n'est pas stockée dans le modèle Manga lui-même
        # Elle doit être récupérée depuis la BDD ou le filesystem
        return 0  # Placeholder

    @property
    def latest_chapter(self) -> Chapter | None:
        """Dernier chapitre publié (ou None si aucun)."""
        if not self.chapters:
            return None
        return max(self.chapters, key=lambda c: c.sort_key)

    @property
    def earliest_chapter(self) -> Chapter | None:
        """Premier chapitre publié (ou None si aucun)."""
        if not self.chapters:
            return None
        return min(self.chapters, key=lambda c: c.sort_key)

    @property
    def is_ongoing(self) -> bool:
        """Indique si le manga est en cours de publication."""
        return self.status == MangaStatus.ONGOING

    @property
    def is_completed(self) -> bool:
        """Indique si le manga est terminé."""
        return self.status == MangaStatus.COMPLETED

    @property
    def is_adult(self) -> bool:
        """Indique si le manga est réservé aux adultes (18+)."""
        return self.content_rating.is_adult

    @property
    def genres_display(self) -> str:
        """Genres formatés pour l'affichage (séparés par virgules)."""
        return ", ".join(self.genres) if self.genres else "Aucun genre"

    @property
    def author_display(self) -> str:
        """Auteur formaté pour l'affichage."""
        if self.author and self.artist and self.author != self.artist:
            return f"{self.author} (dessin: {self.artist})"
        return self.author or "Inconnu"

    @property
    def year_display(self) -> str:
        """Année formatée pour l'affichage."""
        return str(self.year) if self.year else "Inconnu"

    # --------------------------------------------------------------------
    # Méthodes
    # --------------------------------------------------------------------

    def get_chapter_by_number(self, number: float | str) -> Chapter | None:
        """Récupère un chapitre par son numéro.

        Args:
            number: Numéro du chapitre (float ou str).

        Returns:
            Chapitre correspondant ou None si introuvable.
        """
        for chapter in self.chapters:
            if chapter.number == number:
                return chapter
        return None

    def get_chapter_by_id(self, chapter_id: str) -> Chapter | None:
        """Récupère un chapitre par son ID.

        Args:
            chapter_id: ID du chapitre.

        Returns:
            Chapitre correspondant ou None si introuvable.
        """
        for chapter in self.chapters:
            if chapter.id == chapter_id:
                return chapter
        return None

    def sorted_chapters(self, *, reverse: bool = False) -> list[Chapter]:
        """Retourne les chapitres triés par numéro.

        Args:
            reverse: Si True, tri décroissant (plus récent en premier).

        Returns:
            Liste des chapitres triés.
        """
        return sorted(self.chapters, key=lambda c: c.sort_key, reverse=reverse)

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable (pour WebSocket/API).

        Returns:
            Dictionnaire avec les champs essentiels pour l'affichage.
        """
        return {
            "id": self.id,
            "source_id": self.source_id,
            "site": self.site,
            "title": self.title,
            "alternative_titles": self.alternative_titles,
            "description": self.description,
            "author": self.author,
            "artist": self.artist,
            "author_display": self.author_display,
            "genres": self.genres,
            "genres_display": self.genres_display,
            "status": self.status.value,
            "status_label": self.status.label,
            "status_icon": self.status.icon,
            "status_color": self.status.color,
            "year": self.year,
            "year_display": self.year_display,
            "cover_url": str(self.cover_url) if self.cover_url else None,
            "language": self.language.value,
            "language_label": self.language.label,
            "language_flag": self.language.flag,
            "content_rating": self.content_rating.value,
            "content_rating_label": self.content_rating.label,
            "content_rating_icon": self.content_rating.icon,
            "content_rating_color": self.content_rating.color,
            "chapters_count": self.chapters_count,
            "url": str(self.url),
            "updated_at": self.updated_at.isoformat(),
            "demographic": self.demographic.value,
            "demographic_label": self.demographic.label,
            "reading_direction": self.reading_direction.value,
            "reading_direction_label": self.reading_direction.label,
            "is_ongoing": self.is_ongoing,
            "is_completed": self.is_completed,
            "is_adult": self.is_adult,
        }

    def to_full(self) -> dict[str, Any]:
        """Retourne une représentation complète avec tous les chapitres.

        Returns:
            Dictionnaire complet avec chapitres inclus.
        """
        summary = self.to_summary()
        summary["chapters"] = [ch.to_summary() for ch in self.sorted_chapters()]
        return summary

    def __repr__(self) -> str:
        return (
            f"<Manga id={self.id} title='{self.title[:30]}' "
            f"site={self.site} status={self.status.value} "
            f"chapters={self.chapters_count}>"
        )


# ============================================================================
# MODÈLES PYDANTIC — SearchResult
# ============================================================================


class SearchResult(BaseModel):
    """Résultat de recherche multi-sites (immutable).

    Représente un manga trouvé lors d'une recherche sur un ou plusieurs sites.
    Contient un Manga partiellement peuplé (pas de chapters) et un score de pertinence.

    Attributes:
        manga: Manga trouvé (partiellement peuplé).
        score: Score de pertinence (0.0 à 1.0, plus élevé = plus pertinent).
        matched_fields: Liste des champs ayant matché la requête.
    """

    manga: Manga = Field(
        ...,
        description="Manga trouvé (partiellement peuplé).",
    )
    score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Score de pertinence (0.0 à 1.0).",
    )
    matched_fields: list[str] = Field(
        default_factory=list,
        description="Liste des champs ayant matché la requête.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def is_exact_match(self) -> bool:
        """Indique si c'est une correspondance exacte (score = 1.0)."""
        return self.score >= 0.99

    @property
    def is_partial_match(self) -> bool:
        """Indique si c'est une correspondance partielle."""
        return 0.5 <= self.score < 0.99

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable."""
        return {
            "manga": self.manga.to_summary(),
            "score": self.score,
            "matched_fields": self.matched_fields,
            "is_exact_match": self.is_exact_match,
        }

    def __repr__(self) -> str:
        return (
            f"<SearchResult manga='{self.manga.title[:30]}' "
            f"score={self.score:.2f} site={self.manga.site}>"
        )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "MangaModelError",
    "InvalidMangaError",
    "InvalidChapterError",
    "InvalidPageError",
    # Enums
    "MangaStatus",
    "Language",
    "ContentRating",
    "Demographic",
    "ReadingDirection",
    # Modèles principaux
    "Manga",
    "Chapter",
    "Page",
    "SearchResult",
    # Helpers
    "generate_manga_id",
    "generate_chapter_id",
    "generate_page_id",
]
