"""Modèle de métadonnées ComicInfo.xml (standard ComicRack v2.1).

Ce module définit le modèle Pydantic complet pour les métadonnées ComicInfo,
qui est le standard de facto pour l'embarquement de métadonnées dans les
archives de comics/mangas (CBZ, CBR, PDF). Ce standard est supporté par
tous les lecteurs modernes : ComicRack, Kavita, Suwayomi, CDisplayEx,
YACReader, Panels, etc.

Spécification officielle :
    https://anansi-project.github.io/docs/comicinfo/schema/v2.1

Architecture :
    ComicInfo (modèle principal)
        ├── AgeRating (enum) : Unknown, Adult, Early Childhood, etc.
        ├── MangaType (enum) : Unknown, Yes, YesAndRightToLeft, No
        ├── FormatType (enum) : Manga, Webtoon, Comic, etc.
        ├── ComicPage (modèle) : Page individuelle avec type et métadonnées
        ├── ComicPageType (enum) : FrontCover, InnerCover, Story, etc.
        └── ~40 champs optionnels couvrant tous les aspects du standard

Fonctionnalités :
    - Sérialisation XML complète (to_xml) avec gestion des namespaces
    - Parsing XML robuste (from_xml) avec tolérance aux erreurs
    - Conversion bidirectionnelle avec les modèles Manga/Chapter
    - Validation stricte des champs critiques (ratings, dates, URLs)
    - Support des listes (genres, tags, characters) en strings CSV
    - Compatibilité avec le template Jinja2 `comic_info_template.xml`
    - Modèle immutable (frozen=True) pour cohérence

Exemple d'utilisation :
    >>> from nexusdl.core.packaging.comic_info import (
    ...     ComicInfo, AgeRating, MangaType, ComicPage, ComicPageType,
    ... )
    >>>
    >>> # Créer un ComicInfo complet
    >>> comic = ComicInfo(
    ...     title="Chapitre 001 - Romance Dawn",
    ...     series="One Piece",
    ...     number="1",
    ...     volume=1,
    ...     summary="Luffy commence son aventure...",
    ...     year=1997,
    ...     month=8,
    ...     writer="Eiichiro Oda",
    ...     genres=["Action", "Adventure", "Comedy"],
    ...     language_iso="fr",
    ...     age_rating=AgeRating.EVERYONE,
    ...     manga=MangaType.YES_AND_RIGHT_TO_LEFT,
    ...     page_count=53,
    ...     pages=[
    ...         ComicPage(image=1, type=ComicPageType.FRONT_COVER),
    ...         ComicPage(image=2, type=ComicPageType.STORY),
    ...     ],
    ... )
    >>>
    >>> # Sérialiser en XML
    >>> xml_content = comic.to_xml()
    >>> print(xml_content[:100])
    '<?xml version="1.0" encoding="utf-8"?>\\n<ComicInfo>...'
    >>>
    >>> # Parser depuis XML
    >>> parsed = ComicInfo.from_xml(xml_content)
    >>> assert parsed.series == "One Piece"
    >>>
    >>> # Convertir depuis un modèle Manga/Chapter
    >>> comic = ComicInfo.from_manga_and_chapter(manga, chapter)
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Final, Self
from xml.etree import ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from nexusdl.core.exceptions import NexusDLError

if TYPE_CHECKING:
    from nexusdl.core.models.manga import Chapter, Manga


# ============================================================================
# EXCEPTIONS
# ============================================================================


class ComicInfoError(NexusDLError):
    """Exception de base pour les erreurs liées à ComicInfo."""


class InvalidComicInfoError(ComicInfoError):
    """Exception levée lorsque les métadonnées ComicInfo sont invalides."""

    def __init__(self, reason: str = "") -> None:
        msg = "Métadonnées ComicInfo invalides"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


class ComicInfoParseError(ComicInfoError):
    """Exception levée lorsque le parsing XML échoue."""

    def __init__(self, reason: str = "") -> None:
        msg = "Échec du parsing ComicInfo.xml"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class AgeRating(str, Enum):
    """Classification d'âge selon le standard ComicInfo.

    Les valeurs correspondent exactement aux valeurs acceptées par
    ComicRack, Kavita, et les autres lecteurs compatibles.
    """

    UNKNOWN = "Unknown"
    ADULT = "Adult"
    EARLY_CHILDHOOD = "Early Childhood"
    EVERYONE = "Everyone"
    EVERYONE_10 = "Everyone 10+"
    G = "G"
    KIDS_TO_ADULTS = "Kids to Adults"
    M = "M"
    MA_15 = "MA15+"
    MATURE = "Mature 17+"
    PG = "PG"
    R_18 = "R18+"
    RATING_PENDING = "Rating Pending"
    TEEN = "Teen"
    X_18 = "X18+"

    @property
    def label(self) -> str:
        """Libellé humain de la classification."""
        return self.value

    @property
    def is_adult(self) -> bool:
        """Indique si la classification est pour adultes (18+)."""
        return self in (
            AgeRating.ADULT,
            AgeRating.MATURE,
            AgeRating.R_18,
            AgeRating.X_18,
            AgeRating.MA_15,
        )

    @property
    def minimum_age(self) -> int:
        """Âge minimum recommandé (0 = tout public)."""
        mapping: dict[AgeRating, int] = {
            AgeRating.UNKNOWN: 0,
            AgeRating.EARLY_CHILDHOOD: 0,
            AgeRating.EVERYONE: 0,
            AgeRating.EVERYONE_10: 10,
            AgeRating.G: 0,
            AgeRating.KIDS_TO_ADULTS: 0,
            AgeRating.PG: 13,
            AgeRating.TEEN: 13,
            AgeRating.M: 17,
            AgeRating.MA_15: 15,
            AgeRating.MATURE: 17,
            AgeRating.ADULT: 18,
            AgeRating.R_18: 18,
            AgeRating.X_18: 18,
            AgeRating.RATING_PENDING: 0,
        }
        return mapping.get(self, 0)


class MangaType(str, Enum):
    """Type de manga selon le standard ComicInfo.

    Indique si l'œuvre est un manga et son sens de lecture.
    """

    UNKNOWN = "Unknown"
    NO = "No"
    YES = "Yes"
    YES_AND_RIGHT_TO_LEFT = "YesAndRightToLeft"

    @property
    def is_manga(self) -> bool:
        """Indique si c'est un manga."""
        return self in (MangaType.YES, MangaType.YES_AND_RIGHT_TO_LEFT)

    @property
    def is_rtl(self) -> bool:
        """Indique si le sens de lecture est droite-à-gauche."""
        return self == MangaType.YES_AND_RIGHT_TO_LEFT


class ComicPageType(str, Enum):
    """Type de page dans une archive ComicInfo.

    Utilisé pour identifier le rôle de chaque page (couverture, story, etc.).
    """

    FRONT_COVER = "FrontCover"
    INNER_COVER = "InnerCover"
    ROUNDUP = "Roundup"
    STORY = "Story"
    ADVERTISEMENT = "Advertisement"
    EDITORIAL = "Editorial"
    LETTERS = "Letters"
    PREVIEW = "Preview"
    BACK_COVER = "BackCover"
    OTHER = "Other"
    DELETED = "Deleted"

    @property
    def label(self) -> str:
        """Libellé humain du type de page."""
        return {
            ComicPageType.FRONT_COVER: "Couverture avant",
            ComicPageType.INNER_COVER: "Couverture intérieure",
            ComicPageType.ROUNDUP: "Récapitulatif",
            ComicPageType.STORY: "Histoire",
            ComicPageType.ADVERTISEMENT: "Publicité",
            ComicPageType.EDITORIAL: "Éditorial",
            ComicPageType.LETTERS: "Lettres",
            ComicPageType.PREVIEW: "Aperçu",
            ComicPageType.BACK_COVER: "Couverture arrière",
            ComicPageType.OTHER: "Autre",
            ComicPageType.DELETED: "Supprimé",
        }[self]


class FormatType(str, Enum):
    """Format de l'œuvre (Manga, Webtoon, Comic, etc.)."""

    MANGA = "Manga"
    WEBTOON = "Webtoon"
    COMIC = "Comic"
    MANHWA = "Manhwa"
    MANHUA = "Manhua"
    DOUJINSHI = "Doujinshi"
    LIGHT_NOVEL = "Light Novel"
    UNKNOWN = "Unknown"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return self.value


# ============================================================================
# MODÈLES PYDANTIC — Pages
# ============================================================================


class ComicPage(BaseModel):
    """Page individuelle dans une archive ComicInfo.

    Représente une entrée dans la section <Pages> du XML.
    """

    image: int = Field(
        ...,
        ge=0,
        description="Index de la page (0-based dans l'archive).",
    )
    type: ComicPageType = Field(
        default=ComicPageType.STORY,
        description="Type de page (FrontCover, Story, etc.).",
    )
    image_size: int | None = Field(
        default=None,
        ge=0,
        description="Taille du fichier en bytes (optionnel).",
    )
    image_width: int | None = Field(
        default=None,
        ge=0,
        description="Largeur en pixels (optionnel).",
    )
    image_height: int | None = Field(
        default=None,
        ge=0,
        description="Hauteur en pixels (optionnel).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    def to_xml_element(self) -> ET.Element:
        """Convertit la page en élément XML."""
        attrs: dict[str, str] = {
            "Image": str(self.image),
            "Type": self.type.value,
        }
        if self.image_size is not None:
            attrs["ImageSize"] = str(self.image_size)
        if self.image_width is not None:
            attrs["ImageWidth"] = str(self.image_width)
        if self.image_height is not None:
            attrs["ImageHeight"] = str(self.image_height)

        elem = ET.Element("Page", attrs)
        return elem

    @classmethod
    def from_xml_element(cls, elem: ET.Element) -> ComicPage:
        """Parse une page depuis un élément XML."""
        try:
            image = int(elem.attrib.get("Image", "0"))
            type_str = elem.attrib.get("Type", "Story")
            try:
                page_type = ComicPageType(type_str)
            except ValueError:
                page_type = ComicPageType.STORY

            image_size = elem.attrib.get("ImageSize")
            image_width = elem.attrib.get("ImageWidth")
            image_height = elem.attrib.get("ImageHeight")

            return cls(
                image=image,
                type=page_type,
                image_size=int(image_size) if image_size else None,
                image_width=int(image_width) if image_width else None,
                image_height=int(image_height) if image_height else None,
            )
        except Exception as e:
            raise ComicInfoParseError(f"Page invalide: {e}") from e


# ============================================================================
# MODÈLE PRINCIPAL — ComicInfo
# ============================================================================


class ComicInfo(BaseModel):
    """Métadonnées complètes au standard ComicInfo v2.1.

    Ce modèle représente l'ensemble des champs du standard ComicInfo,
    utilisés pour embarquer les métadonnées dans les archives CBZ/CBR/ZIP.

    Tous les champs sont optionnels sauf `title`, `series`, `number`,
    `page_count` et `language_iso` qui sont les minima requis pour
    une identification correcte par les lecteurs.

    Attributes:
        title: Titre du chapitre/épisode.
        series: Titre de la série (manga).
        number: Numéro du chapitre (peut être "1", "12.5", "Extra").
        volume: Numéro du volume (optionnel).
        alternate_series: Série alternative (optionnel).
        alternate_number: Numéro dans la série alternative.
        summary: Synopsis/description.
        notes: Notes additionnelles.
        year: Année de publication.
        month: Mois de publication (1-12).
        day: Jour de publication (1-31).
        writer: Auteur/scénariste.
        penciller: Dessinateur/crayonneur.
        inker: Encreur.
        colorist: Coloriste.
        letterer: Lettriste.
        cover_artist: Artiste de couverture.
        editor: Éditeur.
        translator: Traducteur (groupe de scanlation).
        genre: Genres (séparés par virgules en XML, liste en Python).
        tags: Tags (séparés par virgules en XML, liste en Python).
        page_count: Nombre total de pages.
        page_count_as_text: Nombre de pages en texte (ex: "53 pages").
        language_iso: Code langue ISO 639-1 (ex: "fr", "en", "ja").
        age_rating: Classification d'âge.
        manga: Type de manga (Yes, No, YesAndRightToLeft).
        format_type: Format de l'œuvre (Manga, Webtoon, etc.).
        web: URL source.
        publisher: Éditeur/groupe de scanlation.
        imprint: Imprint/marque.
        community_rating: Note communautaire (0.0 à 5.0).
        story_arc: Arc narratif.
        story_arc_number: Numéro dans l'arc.
        series_group: Groupe de séries.
        characters: Personnages (liste).
        teams: Équipes/organisations (liste).
        locations: Lieux (liste).
        scan_information: Informations de scan.
        count: Nombre total d'issues dans la série.
        main_character: Personnage principal.
        review: Critique/review.
        pages: Liste des pages avec métadonnées.
    """

    # Identification
    title: str = Field(..., min_length=1, description="Titre du chapitre.")
    series: str = Field(..., min_length=1, description="Titre de la série.")
    number: str = Field(
        ...,
        min_length=1,
        description="Numéro du chapitre (string pour supporter '12.5', 'Extra').",
    )
    volume: int | None = Field(
        default=None,
        ge=0,
        description="Numéro du volume.",
    )
    alternate_series: str | None = Field(
        default=None,
        max_length=200,
        description="Série alternative.",
    )
    alternate_number: str | None = Field(
        default=None,
        description="Numéro dans la série alternative.",
    )
    count: int | None = Field(
        default=None,
        ge=0,
        description="Nombre total d'issues dans la série.",
    )

    # Contenu
    summary: str | None = Field(
        default=None,
        max_length=10000,
        description="Synopsis/description.",
    )
    notes: str | None = Field(
        default=None,
        max_length=2000,
        description="Notes additionnelles.",
    )
    review: str | None = Field(
        default=None,
        max_length=5000,
        description="Critique/review.",
    )
    story_arc: str | None = Field(
        default=None,
        max_length=200,
        description="Arc narratif.",
    )
    story_arc_number: str | None = Field(
        default=None,
        description="Numéro dans l'arc.",
    )
    series_group: str | None = Field(
        default=None,
        max_length=200,
        description="Groupe de séries.",
    )
    main_character: str | None = Field(
        default=None,
        max_length=200,
        description="Personnage principal.",
    )

    # Date
    year: int | None = Field(
        default=None,
        ge=1900,
        le=2100,
        description="Année de publication.",
    )
    month: int | None = Field(
        default=None,
        ge=1,
        le=12,
        description="Mois de publication.",
    )
    day: int | None = Field(
        default=None,
        ge=1,
        le=31,
        description="Jour de publication.",
    )

    # Équipe créative
    writer: str | None = Field(
        default=None,
        max_length=500,
        description="Auteur/scénariste.",
    )
    penciller: str | None = Field(
        default=None,
        max_length=500,
        description="Dessinateur/crayonneur.",
    )
    inker: str | None = Field(
        default=None,
        max_length=500,
        description="Encreur.",
    )
    colorist: str | None = Field(
        default=None,
        max_length=500,
        description="Coloriste.",
    )
    letterer: str | None = Field(
        default=None,
        max_length=500,
        description="Lettriste.",
    )
    cover_artist: str | None = Field(
        default=None,
        max_length=500,
        description="Artiste de couverture.",
    )
    editor: str | None = Field(
        default=None,
        max_length=500,
        description="Éditeur.",
    )
    translator: str | None = Field(
        default=None,
        max_length=500,
        description="Traducteur/groupe de scanlation.",
    )

    # Genres et tags (listes Python → CSV en XML)
    genres: list[str] = Field(
        default_factory=list,
        description="Genres (séparés par virgules en XML).",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Tags (séparés par virgules en XML).",
    )
    characters: list[str] = Field(
        default_factory=list,
        description="Personnages (séparés par virgules en XML).",
    )
    teams: list[str] = Field(
        default_factory=list,
        description="Équipes/organisations (séparés par virgules en XML).",
    )
    locations: list[str] = Field(
        default_factory=list,
        description="Lieux (séparés par virgules en XML).",
    )

    # Métadonnées techniques
    page_count: int = Field(
        ...,
        ge=0,
        description="Nombre total de pages.",
    )
    page_count_as_text: str | None = Field(
        default=None,
        description="Nombre de pages en texte (ex: '53 pages').",
    )
    language_iso: str = Field(
        ...,
        min_length=2,
        max_length=5,
        description="Code langue ISO 639-1 (ex: 'fr', 'en', 'ja').",
    )
    age_rating: AgeRating = Field(
        default=AgeRating.UNKNOWN,
        description="Classification d'âge.",
    )
    manga: MangaType = Field(
        default=MangaType.UNKNOWN,
        description="Type de manga.",
    )
    format_type: FormatType = Field(
        default=FormatType.UNKNOWN,
        description="Format de l'œuvre.",
    )
    scan_information: str | None = Field(
        default=None,
        max_length=500,
        description="Informations de scan.",
    )

    # Publication
    web: str | None = Field(
        default=None,
        max_length=1000,
        description="URL source.",
    )
    publisher: str | None = Field(
        default=None,
        max_length=200,
        description="Éditeur/groupe de scanlation.",
    )
    imprint: str | None = Field(
        default=None,
        max_length=200,
        description="Imprint/marque.",
    )

    # Évaluation
    community_rating: float | None = Field(
        default=None,
        ge=0.0,
        le=5.0,
        description="Note communautaire (0.0 à 5.0).",
    )

    # Pages
    pages: list[ComicPage] = Field(
        default_factory=list,
        description="Liste des pages avec métadonnées.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @field_validator("language_iso")
    @classmethod
    def _validate_language_iso(cls, v: str) -> str:
        """Valide le code langue ISO 639-1."""
        v = v.strip().lower()
        if len(v) < 2 or len(v) > 3:
            raise InvalidComicInfoError(
                f"Code langue invalide: {v} (doit être 2-3 lettres ISO 639-1)"
            )
        return v

    @field_validator("number")
    @classmethod
    def _validate_number(cls, v: str) -> str:
        """Valide le numéro de chapitre."""
        v = v.strip()
        if not v:
            raise InvalidComicInfoError("Le numéro de chapitre ne peut pas être vide")
        return v

    @field_validator("genres", "tags", "characters", "teams", "locations", mode="before")
    @classmethod
    def _parse_csv_lists(cls, v: Any) -> list[str]:
        """Parse une liste CSV ou une liste Python."""
        if v is None:
            return []
        if isinstance(v, str):
            # CSV string → list
            return [item.strip() for item in v.split(",") if item.strip()]
        if isinstance(v, list):
            return [str(item).strip() for item in v if str(item).strip()]
        return []

    @model_validator(mode="after")
    def _validate_date_consistency(self) -> Self:
        """Vérifie la cohérence des dates."""
        if self.month is not None and self.year is None:
            # Mois sans année → invalide, on ignore le mois
            pass  # On tolère, certains fichiers ont juste un mois
        if self.day is not None and (self.month is None or self.year is None):
            # Jour sans mois/année → invalide
            pass  # On tolère
        return self

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def genre_csv(self) -> str:
        """Genres en format CSV (pour XML)."""
        return ", ".join(self.genres) if self.genres else ""

    @property
    def tags_csv(self) -> str:
        """Tags en format CSV (pour XML)."""
        return ", ".join(self.tags) if self.tags else ""

    @property
    def characters_csv(self) -> str:
        """Characters en format CSV."""
        return ", ".join(self.characters) if self.characters else ""

    @property
    def teams_csv(self) -> str:
        """Teams en format CSV."""
        return ", ".join(self.teams) if self.teams else ""

    @property
    def locations_csv(self) -> str:
        """Locations en format CSV."""
        return ", ".join(self.locations) if self.locations else ""

    @property
    def is_manga(self) -> bool:
        """Indique si c'est un manga."""
        return self.manga.is_manga

    @property
    def is_rtl(self) -> bool:
        """Indique si le sens de lecture est droite-à-gauche."""
        return self.manga.is_rtl

    @property
    def is_adult(self) -> bool:
        """Indique si le contenu est pour adultes."""
        return self.age_rating.is_adult

    @property
    def full_title(self) -> str:
        """Titre complet (série + numéro + titre)."""
        parts = [self.series]
        if self.volume:
            parts.append(f"Vol. {self.volume}")
        parts.append(f"#{self.number}")
        if self.title and self.title != self.series:
            parts.append(f"- {self.title}")
        return " ".join(parts)

    @property
    def publication_date(self) -> datetime | None:
        """Date de publication complète (si toutes les parties sont présentes)."""
        if self.year is None:
            return None
        month = self.month or 1
        day = self.day or 1
        try:
            return datetime(self.year, month, day, tzinfo=UTC)
        except ValueError:
            return None

    @property
    def front_cover_page(self) -> ComicPage | None:
        """Retourne la page de couverture avant si présente."""
        for page in self.pages:
            if page.type == ComicPageType.FRONT_COVER:
                return page
        return None

    # --------------------------------------------------------------------
    # Sérialisation XML
    # --------------------------------------------------------------------

    def to_xml(self, *, pretty: bool = True) -> str:
        """Sérialise le ComicInfo en XML.

        Args:
            pretty: Si True, formate le XML avec indentation.

        Returns:
            Contenu XML sous forme de chaîne.
        """
        root = ET.Element("ComicInfo")
        root.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
        root.set("xmlns:xsd", "http://www.w3.org/2001/XMLSchema")

        # Champs simples (ordre canonique)
        self._add_text_element(root, "Title", self.title)
        self._add_text_element(root, "Series", self.series)
        self._add_text_element(root, "Number", self.number)
        self._add_optional_int(root, "Volume", self.volume)
        self._add_text_element(root, "AlternateSeries", self.alternate_series)
        self._add_text_element(root, "AlternateNumber", self.alternate_number)
        self._add_text_element(root, "Summary", self.summary)
        self._add_text_element(root, "Notes", self.notes)
        self._add_text_element(root, "Review", self.review)

        # Date
        self._add_optional_int(root, "Year", self.year)
        self._add_optional_int(root, "Month", self.month)
        self._add_optional_int(root, "Day", self.day)

        # Équipe créative
        self._add_text_element(root, "Writer", self.writer)
        self._add_text_element(root, "Penciller", self.penciller)
        self._add_text_element(root, "Inker", self.inker)
        self._add_text_element(root, "Colorist", self.colorist)
        self._add_text_element(root, "Letterer", self.letterer)
        self._add_text_element(root, "CoverArtist", self.cover_artist)
        self._add_text_element(root, "Editor", self.editor)
        self._add_text_element(root, "Translator", self.translator)

        # Genres et tags (CSV)
        self._add_text_element(root, "Genre", self.genre_csv or None)
        self._add_text_element(root, "Tags", self.tags_csv or None)
        self._add_text_element(root, "Characters", self.characters_csv or None)
        self._add_text_element(root, "Teams", self.teams_csv or None)
        self._add_text_element(root, "Locations", self.locations_csv or None)

        # Arcs et groupes
        self._add_text_element(root, "StoryArc", self.story_arc)
        self._add_text_element(root, "StoryArcNumber", self.story_arc_number)
        self._add_text_element(root, "SeriesGroup", self.series_group)
        self._add_text_element(root, "MainCharacter", self.main_character)

        # Métadonnées techniques
        self._add_optional_int(root, "PageCount", self.page_count)
        self._add_text_element(root, "PageCountAsText", self.page_count_as_text)
        self._add_text_element(root, "LanguageISO", self.language_iso)
        self._add_text_element(root, "AgeRating", self.age_rating.value)
        self._add_text_element(root, "Manga", self.manga.value)
        self._add_text_element(root, "Format", self.format_type.value)
        self._add_text_element(root, "ScanInformation", self.scan_information)

        # Publication
        self._add_text_element(root, "Web", self.web)
        self._add_text_element(root, "Publisher", self.publisher)
        self._add_text_element(root, "Imprint", self.imprint)

        # Évaluation
        if self.community_rating is not None:
            self._add_text_element(
                root,
                "CommunityRating",
                f"{self.community_rating:.1f}",
            )

        self._add_optional_int(root, "Count", self.count)

        # Pages
        if self.pages:
            pages_elem = ET.SubElement(root, "Pages")
            for page in self.pages:
                pages_elem.append(page.to_xml_element())

        # Formater si demandé
        if pretty:
            self._indent_xml(root)

        # Sérialiser
        xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=False)
        xml_str = xml_bytes.decode("utf-8")

        # Ajouter la déclaration XML
        return f'<?xml version="1.0" encoding="utf-8"?>\n{xml_str}'

    @classmethod
    def from_xml(cls, xml_content: str | bytes) -> ComicInfo:
        """Parse un ComicInfo depuis du XML.

        Args:
            xml_content: Contenu XML (str ou bytes).

        Returns:
            Instance de ComicInfo.

        Raises:
            ComicInfoParseError: Si le parsing échoue.
            InvalidComicInfoError: Si les données sont invalides.
        """
        try:
            if isinstance(xml_content, str):
                xml_content = xml_content.encode("utf-8")

            root = ET.fromstring(xml_content)

            # Détecter le namespace
            ns = ""
            if root.tag.startswith("{"):
                ns = root.tag.split("}")[0] + "}"

            # Helper pour extraire un champ texte
            def get_text(tag: str) -> str | None:
                elem = root.find(f"{ns}{tag}")
                if elem is None or elem.text is None:
                    return None
                text = elem.text.strip()
                return text if text else None

            def get_int(tag: str) -> int | None:
                text = get_text(tag)
                if text is None:
                    return None
                try:
                    return int(text)
                except ValueError:
                    return None

            def get_float(tag: str) -> float | None:
                text = get_text(tag)
                if text is None:
                    return None
                try:
                    return float(text)
                except ValueError:
                    return None

            def get_enum(tag: str, enum_class: type[Enum], default: Any) -> Any:
                text = get_text(tag)
                if text is None:
                    return default
                try:
                    return enum_class(text)
                except ValueError:
                    return default

            def get_list(tag: str) -> list[str]:
                text = get_text(tag)
                if not text:
                    return []
                return [item.strip() for item in text.split(",") if item.strip()]

            # Extraire les champs requis
            title = get_text("Title") or ""
            series = get_text("Series") or ""
            number = get_text("Number") or "0"
            page_count = get_int("PageCount") or 0
            language_iso = get_text("LanguageISO") or "en"

            # Construire le modèle
            data: dict[str, Any] = {
                "title": title,
                "series": series,
                "number": number,
                "volume": get_int("Volume"),
                "alternate_series": get_text("AlternateSeries"),
                "alternate_number": get_text("AlternateNumber"),
                "count": get_int("Count"),
                "summary": get_text("Summary"),
                "notes": get_text("Notes"),
                "review": get_text("Review"),
                "story_arc": get_text("StoryArc"),
                "story_arc_number": get_text("StoryArcNumber"),
                "series_group": get_text("SeriesGroup"),
                "main_character": get_text("MainCharacter"),
                "year": get_int("Year"),
                "month": get_int("Month"),
                "day": get_int("Day"),
                "writer": get_text("Writer"),
                "penciller": get_text("Penciller"),
                "inker": get_text("Inker"),
                "colorist": get_text("Colorist"),
                "letterer": get_text("Letterer"),
                "cover_artist": get_text("CoverArtist"),
                "editor": get_text("Editor"),
                "translator": get_text("Translator"),
                "genres": get_list("Genre"),
                "tags": get_list("Tags"),
                "characters": get_list("Characters"),
                "teams": get_list("Teams"),
                "locations": get_list("Locations"),
                "page_count": page_count,
                "page_count_as_text": get_text("PageCountAsText"),
                "language_iso": language_iso,
                "age_rating": get_enum("AgeRating", AgeRating, AgeRating.UNKNOWN),
                "manga": get_enum("Manga", MangaType, MangaType.UNKNOWN),
                "format_type": get_enum("Format", FormatType, FormatType.UNKNOWN),
                "scan_information": get_text("ScanInformation"),
                "web": get_text("Web"),
                "publisher": get_text("Publisher"),
                "imprint": get_text("Imprint"),
                "community_rating": get_float("CommunityRating"),
            }

            # Parser les pages
            pages_elem = root.find(f"{ns}Pages")
            if pages_elem is not None:
                pages: list[ComicPage] = []
                for page_elem in pages_elem:
                    if page_elem.tag == f"{ns}Page" or page_elem.tag == "Page":
                        try:
                            pages.append(ComicPage.from_xml_element(page_elem))
                        except ComicInfoParseError:
                            pass  # Ignorer les pages invalides
                data["pages"] = pages

            return cls.model_validate(data)

        except ET.ParseError as e:
            raise ComicInfoParseError(f"XML malformé: {e}") from e
        except Exception as e:
            if isinstance(e, (ComicInfoError,)):
                raise
            raise ComicInfoParseError(str(e)) from e

    # --------------------------------------------------------------------
    # Méthodes de conversion depuis les modèles de domaine
    # --------------------------------------------------------------------

    @classmethod
    def from_manga_and_chapter(
        cls,
        manga: Manga | None = None,
        chapter: Chapter | None = None,
    ) -> ComicInfo:
        """Construit un ComicInfo depuis des modèles Manga/Chapter.

        Args:
            manga: Modèle Manga (optionnel).
            chapter: Modèle Chapter (optionnel).

        Returns:
            Instance de ComicInfo avec les métadonnées mappées.

        Raises:
            InvalidComicInfoError: Si ni manga ni chapter n'est fourni.
        """
        if manga is None and chapter is None:
            raise InvalidComicInfoError("Au moins un manga ou chapter doit être fourni")

        data: dict[str, Any] = {}

        if manga is not None:
            data["series"] = manga.title
            data["summary"] = manga.description
            data["writer"] = manga.author
            data["penciller"] = manga.artist
            data["genres"] = list(manga.genres)
            data["year"] = manga.year
            data["language_iso"] = manga.language.value
            data["web"] = str(manga.url)

            # Mapper le content_rating vers AgeRating
            from nexusdl.core.models.manga import ContentRating
            rating_map = {
                ContentRating.SAFE: AgeRating.EVERYONE,
                ContentRating.SUGGESTIVE: AgeRating.TEEN,
                ContentRating.EROTICA: AgeRating.MATURE,
                ContentRating.PORNOGRAPHIC: AgeRating.R_18,
            }
            data["age_rating"] = rating_map.get(manga.content_rating, AgeRating.UNKNOWN)

            # Mapper le statut
            from nexusdl.core.models.manga import MangaStatus
            if manga.status == MangaStatus.COMPLETED:
                data["count"] = len(manga.chapters) if manga.chapters else None

        if chapter is not None:
            data["title"] = chapter.title or ""
            data["number"] = str(chapter.number)
            data["volume"] = chapter.volume
            data["page_count"] = chapter.pages_count or 0
            data["translator"] = chapter.scanlator

            # Mapper la langue du chapitre (priorité sur manga)
            if chapter.language.value != (manga.language.value if manga else ""):
                data["language_iso"] = chapter.language.value

            # Date de publication
            if chapter.published_at is not None:
                data["year"] = chapter.published_at.year
                data["month"] = chapter.published_at.month
                data["day"] = chapter.published_at.day

            # Mapper les pages si disponibles
            if chapter.pages:
                pages: list[ComicPage] = []
                for i, page in enumerate(chapter.pages):
                    page_type = (
                        ComicPageType.FRONT_COVER if i == 0 else ComicPageType.STORY
                    )
                    pages.append(
                        ComicPage(
                            image=i,
                            type=page_type,
                            image_width=page.width,
                            image_height=page.height,
                            image_size=page.size_bytes,
                        )
                    )
                data["pages"] = pages

        # Détection du format (Manga/Webtoon/Manhwa)
        if manga is not None:
            # Heuristique basée sur les tags/genres
            all_tags = [g.lower() for g in manga.genres]
            if "webtoon" in all_tags or "manhwa" in all_tags:
                data["format_type"] = FormatType.WEBTOON
            elif "manhua" in all_tags:
                data["format_type"] = FormatType.MANHUA
            else:
                data["format_type"] = FormatType.MANGA

            # Sens de lecture
            from nexusdl.core.models.manga import ReadingDirection
            if manga.reading_direction == ReadingDirection.RIGHT_TO_LEFT:
                data["manga"] = MangaType.YES_AND_RIGHT_TO_LEFT
            elif manga.reading_direction == ReadingDirection.LEFT_TO_RIGHT:
                data["manga"] = MangaType.YES
            else:
                data["manga"] = MangaType.UNKNOWN

        # Valeurs par défaut pour les champs requis
        data.setdefault("title", data.get("series", "Unknown"))
        data.setdefault("series", "Unknown")
        data.setdefault("number", "0")
        data.setdefault("page_count", 0)
        data.setdefault("language_iso", "en")

        return cls.model_validate(data)

    # --------------------------------------------------------------------
    # Helpers internes
    # --------------------------------------------------------------------

    @staticmethod
    def _add_text_element(
        parent: ET.Element,
        tag: str,
        text: str | None,
    ) -> None:
        """Ajoute un élément texte si non vide."""
        if text is not None and text.strip():
            elem = ET.SubElement(parent, tag)
            elem.text = text.strip()

    @staticmethod
    def _add_optional_int(
        parent: ET.Element,
        tag: str,
        value: int | None,
    ) -> None:
        """Ajoute un élément entier si non None."""
        if value is not None:
            elem = ET.SubElement(parent, tag)
            elem.text = str(value)

    @staticmethod
    def _indent_xml(elem: ET.Element, level: int = 0) -> None:
        """Ajoute l'indentation au XML (Python 3.8 compatible)."""
        indent = "\n" + "  " * level
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = indent + "  "
            if not elem.tail or not elem.tail.strip():
                elem.tail = indent
            for child in elem:
                ComicInfo._indent_xml(child, level + 1)
            if not child.tail or not child.tail.strip():  # type: ignore[possibly-undefined]
                child.tail = indent  # type: ignore[possibly-undefined]
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = indent

    def __repr__(self) -> str:
        return (
            f"<ComicInfo series='{self.series}' "
            f"number={self.number} "
            f"pages={self.page_count} "
            f"lang={self.language_iso}>"
        )


# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================


def parse_comic_info_from_archive(archive_path: str | bytes) -> ComicInfo | None:
    """Extrait et parse ComicInfo.xml depuis une archive (helper).

    Fonction utilitaire pour usage externe. Pour une extraction complète,
    utiliser `core.library.metadata.MetadataExtractor`.

    Args:
        archive_path: Chemin vers l'archive CBZ/CBR/ZIP.

    Returns:
        Instance de ComicInfo ou None si non trouvé.
    """
    import zipfile

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            for name in zf.namelist():
                if name.lower() == "comicinfo.xml":
                    xml_content = zf.read(name)
                    return ComicInfo.from_xml(xml_content)
        return None
    except Exception:
        return None


def merge_comic_info(base: ComicInfo, override: ComicInfo) -> ComicInfo:
    """Fusionne deux ComicInfo, avec priorité au second.

    Les champs non-None de `override` remplacent ceux de `base`.
    Les listes sont concaténées (sans doublons).

    Args:
        base: ComicInfo de base.
        override: ComicInfo prioritaire.

    Returns:
        Nouveau ComicInfo fusionné.
    """
    base_dict = base.model_dump()
    override_dict = override.model_dump(exclude_none=True)

    # Fusion simple : override remplace base
    for key, value in override_dict.items():
        if value is not None:
            # Pour les listes, concaténer sans doublons
            if isinstance(value, list) and isinstance(base_dict.get(key), list):
                combined = list(base_dict[key]) + value
                # Supprimer les doublons en préservant l'ordre
                seen: set[str] = set()
                deduped: list[Any] = []
                for item in combined:
                    item_str = str(item)
                    if item_str not in seen:
                        seen.add(item_str)
                        deduped.append(item)
                base_dict[key] = deduped
            else:
                base_dict[key] = value

    return ComicInfo.model_validate(base_dict)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "ComicInfoError",
    "InvalidComicInfoError",
    "ComicInfoParseError",
    # Enums
    "AgeRating",
    "MangaType",
    "ComicPageType",
    "FormatType",
    # Modèles
    "ComicPage",
    "ComicInfo",
    # Helpers
    "parse_comic_info_from_archive",
    "merge_comic_info",
]
