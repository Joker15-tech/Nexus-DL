"""Schémas communs partagés pour l'API REST NexusDL.

Ce module centralise tous les modèles de données, types personnalisés,
validateurs et helpers partagés entre les schémas de requête et de réponse.
Il fournit une base commune pour assurer la cohérence dans toute l'API.

**Architecture** :
    common.py
        ├── Types personnalisés
        │   ├── PositiveInt          : Entier positif
        │   ├── NonNegativeInt       : Entier non-négatif
        │   ├── NonEmptyString       : Chaîne non-vide
        │   ├── EmailStr             : Adresse email valide
        │   ├── UrlStr               : URL valide
        │   └── DateTimeStr          : Timestamp ISO 8601
        │
        ├── Modèles de domaine
        │   ├── MangaBase            : Manga (champs communs)
        │   ├── ChapterBase          : Chapitre (champs communs)
        │   ├── SiteBase             : Site (champs communs)
        │   ├── UserBase             : Utilisateur (champs communs)
        │   ├── DownloadTaskBase     : Tâche de téléchargement
        │   └── ReadingProgressBase  : Progression de lecture
        │
        ├── Validateurs partagés
        │   ├── validate_language    : Code langue ISO 639-1
        │   ├── validate_url         : URL valide
        │   ├── validate_datetime    : Timestamp ISO 8601
        │   └── validate_id          : ID valide (UUID ou string)
        │
        ├── Helpers de conversion
        │   ├── to_camel_case        : snake_case → camelCase
        │   ├── to_snake_case        : camelCase → snake_case
        │   ├── sanitize_string      : Nettoyer une chaîne
        │   └── parse_datetime       : Parser un timestamp
        │
        └── Constantes communes
            ├── LANGUAGES            : Codes langue supportés
            ├── SORT_ORDERS          : Ordres de tri
            └── STATUS_VALUES        : Valeurs de statut

**Utilisation** :
    >>> from nexusdl.interfaces.web.backend.schemas.common import (
    ...     MangaBase, SiteBase, validate_language, to_camel_case,
    ... )
    >>>
    >>> # Modèle de domaine
    >>> manga = MangaBase(
    ...     id="123",
    ...     title="One Piece",
    ...     author="Eiichiro Oda",
    ...     language="en",
    ... )
    >>>
    >>> # Validateur
    >>> lang = validate_language("fr")  # ✓ Valide
    >>> lang = validate_language("invalid")  # ✗ Erreur
    >>>
    >>> # Helper
    >>> camel = to_camel_case("manga_title")  # "mangaTitle"

Intégration :
    - pydantic                     : Validation des données
    - interfaces/web/backend/schemas/* : Utilisé par requests.py et responses.py
    - core/models/*                : Modèles de domaine du core
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic.functional_validators import AfterValidator


# ============================================================================
# TYPES PERSONNALISÉS — Annotations Pydantic réutilisables
# ============================================================================


# Entier positif (> 0)
PositiveInt = Annotated[int, Field(gt=0, description="Entier positif")]

# Entier non-négatif (>= 0)
NonNegativeInt = Annotated[int, Field(ge=0, description="Entier non-négatif")]

# Chaîne non-vide (après strip)
NonEmptyString = Annotated[
    str,
    StringConstraints(min_length=1, strip_whitespace=True),
]

# Adresse email valide
EMAIL_REGEX = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
EmailStr = Annotated[
    str,
    StringConstraints(pattern=EMAIL_REGEX),
]

# URL valide
URL_REGEX = r"^https?://"
UrlStr = Annotated[
    str,
    StringConstraints(pattern=URL_REGEX),
]

# Timestamp ISO 8601
DateTimeStr = Annotated[
    str,
    StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"),
]

# UUID valide
UUIDStr = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]

# Code langue ISO 639-1 (2 lettres)
LanguageCode = Annotated[
    str,
    StringConstraints(min_length=2, max_length=2, pattern=r"^[a-z]{2}$"),
]

# ID de ressource (string ou UUID)
ResourceId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=100),
]


# ============================================================================
# CONSTANTES COMMUNES — Valeurs partagées
# ============================================================================


# Codes langue supportés (ISO 639-1)
SUPPORTED_LANGUAGES: frozenset[str] = frozenset({
    "en",  # Anglais
    "fr",  # Français
    "es",  # Espagnol
    "de",  # Allemand
    "it",  # Italien
    "pt",  # Portugais
    "ru",  # Russe
    "ja",  # Japonais
    "ko",  # Coréen
    "zh",  # Chinois
    "ar",  # Arabe
    "pl",  # Polonais
    "tr",  # Turc
    "nl",  # Néerlandais
    "vi",  # Vietnamien
    "th",  # Thaï
    "id",  # Indonésien
    "hi",  # Hindi
    "sv",  # Suédois
    "da",  # Danois
    "fi",  # Finnois
    "no",  # Norvégien
    "cs",  # Tchèque
    "hu",  # Hongrois
    "ro",  # Roumain
    "el",  # Grec
    "he",  # Hébreu
    "uk",  # Ukrainien
    "bg",  # Bulgare
    "hr",  # Croate
    "sk",  # Slovaque
    "lt",  # Lituanien
    "lv",  # Letton
    "et",  # Estonien
    "sl",  # Slovène
    "sr",  # Serbe
    "ms",  # Malais
    "tl",  # Tagalog
    "multi",  # Multilingue
})

# Ordres de tri
SORT_ORDERS: frozenset[str] = frozenset({
    "asc",
    "desc",
})

# Statuts de publication
PUBLICATION_STATUSES: frozenset[str] = frozenset({
    "ongoing",
    "completed",
    "hiatus",
    "cancelled",
    "unknown",
})

# Statuts de lecture
READING_STATUSES: frozenset[str] = frozenset({
    "reading",
    "completed",
    "plan_to_read",
    "on_hold",
    "dropped",
})

# Formats de téléchargement
DOWNLOAD_FORMATS: frozenset[str] = frozenset({
    "cbz",
    "cbr",
    "pdf",
    "zip",
    "folder",
})

# Qualités d'image
IMAGE_QUALITIES: frozenset[str] = frozenset({
    "original",
    "high",
    "medium",
    "low",
})

# Priorités
PRIORITIES: frozenset[str] = frozenset({
    "low",
    "normal",
    "high",
    "urgent",
})


# ============================================================================
# VALIDATEURS PARTAGÉS — Fonctions de validation réutilisables
# ============================================================================


def validate_language(value: str) -> str:
    """Valide un code langue ISO 639-1.

    Args:
        value: Code langue à valider.

    Returns:
        Code langue validé (en minuscules).

    Raises:
        ValueError: Si le code n'est pas supporté.

    Example:
        >>> validate_language("fr")
        'fr'
        >>> validate_language("EN")
        'en'
        >>> validate_language("invalid")
        ValueError: Code langue non supporté: invalid
    """
    value = value.strip().lower()
    if value not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Code langue non supporté: {value}")
    return value


def validate_url(value: str) -> str:
    """Valide une URL.

    Args:
        value: URL à valider.

    Returns:
        URL validée.

    Raises:
        ValueError: Si l'URL est invalide.

    Example:
        >>> validate_url("https://example.com")
        'https://example.com'
        >>> validate_url("not-a-url")
        ValueError: URL invalide: not-a-url
    """
    value = value.strip()
    if not re.match(URL_REGEX, value):
        raise ValueError(f"URL invalide: {value}")
    return value


def validate_datetime(value: str | datetime) -> datetime:
    """Valide et parse un timestamp.

    Args:
        value: Timestamp (string ISO 8601 ou datetime).

    Returns:
        Instance datetime validée.

    Raises:
        ValueError: Si le timestamp est invalide.

    Example:
        >>> validate_datetime("2026-09-24T14:30:45Z")
        datetime.datetime(2026, 9, 24, 14, 30, 45, tzinfo=datetime.timezone.utc)
    """
    if isinstance(value, datetime):
        return value

    try:
        # Essayer différents formats
        formats = [
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(value, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt
            except ValueError:
                continue

        raise ValueError(f"Format de timestamp invalide: {value}")

    except Exception as e:
        raise ValueError(f"Timestamp invalide: {value}") from e


def validate_id(value: str) -> str:
    """Valide un ID de ressource.

    Args:
        value: ID à valider.

    Returns:
        ID validé.

    Raises:
        ValueError: Si l'ID est invalide.

    Example:
        >>> validate_id("123")
        '123'
        >>> validate_id("abc-def-ghi")
        'abc-def-ghi'
        >>> validate_id("")
        ValueError: ID ne peut pas être vide
    """
    value = value.strip()
    if not value:
        raise ValueError("ID ne peut pas être vide")
    if len(value) > 100:
        raise ValueError(f"ID trop long: {len(value)} caractères (max: 100)")
    return value


def validate_positive_int(value: int) -> int:
    """Valide un entier positif.

    Args:
        value: Entier à valider.

    Returns:
        Entier validé.

    Raises:
        ValueError: Si l'entier n'est pas positif.

    Example:
        >>> validate_positive_int(5)
        5
        >>> validate_positive_int(0)
        ValueError: Doit être positif, reçu: 0
    """
    if value <= 0:
        raise ValueError(f"Doit être positif, reçu: {value}")
    return value


def validate_non_negative_int(value: int) -> int:
    """Valide un entier non-négatif.

    Args:
        value: Entier à valider.

    Returns:
        Entier validé.

    Raises:
        ValueError: Si l'entier est négatif.

    Example:
        >>> validate_non_negative_int(0)
        0
        >>> validate_non_negative_int(-1)
        ValueError: Doit être non-négatif, reçu: -1
    """
    if value < 0:
        raise ValueError(f"Doit être non-négatif, reçu: {value}")
    return value


# ============================================================================
# HELPERS DE CONVERSION — Fonctions utilitaires
# ============================================================================


def to_camel_case(snake_str: str) -> str:
    """Convertit snake_case en camelCase.

    Args:
        snake_str: Chaîne en snake_case.

    Returns:
        Chaîne en camelCase.

    Example:
        >>> to_camel_case("manga_title")
        'mangaTitle'
        >>> to_camel_case("user_id")
        'userId'
    """
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def to_snake_case(camel_str: str) -> str:
    """Convertit camelCase en snake_case.

    Args:
        camel_str: Chaîne en camelCase.

    Returns:
        Chaîne en snake_case.

    Example:
        >>> to_snake_case("mangaTitle")
        'manga_title'
        >>> to_snake_case("userId")
        'user_id'
    """
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", camel_str)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def sanitize_string(value: str) -> str:
    """Nettoie une chaîne (strip, normalise les espaces).

    Args:
        value: Chaîne à nettoyer.

    Returns:
        Chaîne nettoyée.

    Example:
        >>> sanitize_string("  hello   world  ")
        'hello world'
    """
    value = value.strip()
    value = re.sub(r"\s+", " ", value)
    return value


def parse_datetime(value: str | datetime | None) -> datetime | None:
    """Parse un timestamp de manière tolérante.

    Args:
        value: Timestamp à parser.

    Returns:
        Instance datetime ou None.

    Example:
        >>> parse_datetime("2026-09-24T14:30:45Z")
        datetime.datetime(2026, 9, 24, 14, 30, 45, tzinfo=datetime.timezone.utc)
        >>> parse_datetime(None)
        None
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return validate_datetime(value)
    except ValueError:
        return None


def generate_uuid() -> str:
    """Génère un UUID v4.

    Returns:
        UUID sous forme de string.

    Example:
        >>> uuid = generate_uuid()
        >>> len(uuid)
        36
    """
    return str(uuid.uuid4())


def format_datetime(dt: datetime, format: str = "iso") -> str:
    """Formate un datetime en string.

    Args:
        dt: Instance datetime.
        format: Format de sortie ("iso", "date", "time").

    Returns:
        String formatée.

    Example:
        >>> from datetime import datetime
        >>> dt = datetime(2026, 9, 24, 14, 30, 45, tzinfo=UTC)
        >>> format_datetime(dt, "iso")
        '2026-09-24T14:30:45+00:00'
    """
    if format == "iso":
        return dt.isoformat()
    elif format == "date":
        return dt.strftime("%Y-%m-%d")
    elif format == "time":
        return dt.strftime("%H:%M:%S")
    else:
        return dt.isoformat()


# ============================================================================
# MODÈLES DE DOMAINE — Bases partagées
# ============================================================================


class MangaBase(BaseModel):
    """Modèle de base pour un manga.

    Champs communs utilisés dans les requêtes et réponses.

    Attributes:
        id: ID unique du manga.
        title: Titre du manga.
        author: Auteur.
        year: Année de publication.
        language: Code langue (ISO 639-1).
        status: Statut de publication.
        cover_url: URL de la couverture.
        url: URL du manga sur le site source.
        description: Description.
        tags: Liste de tags.

    Example:
        >>> manga = MangaBase(
        ...     id="123",
        ...     title="One Piece",
        ...     author="Eiichiro Oda",
        ...     language="en",
        ... )
    """

    id: ResourceId = Field(..., description="ID unique.")
    title: NonEmptyString = Field(..., description="Titre.")
    author: str = Field(default="", description="Auteur.")
    year: int | None = Field(default=None, ge=1900, le=2100, description="Année.")
    language: LanguageCode = Field(default="en", description="Code langue.")
    status: str = Field(default="unknown", description="Statut publication.")
    cover_url: str = Field(default="", description="URL couverture.")
    url: str = Field(default="", description="URL manga.")
    description: str = Field(default="", description="Description.")
    tags: list[str] = Field(default_factory=list, description="Tags.")

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        """Valide le code langue."""
        return validate_language(v)

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        """Valide le statut."""
        v = v.strip().lower()
        if v not in PUBLICATION_STATUSES:
            raise ValueError(f"Statut invalide: {v}")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str]) -> list[str]:
        """Valide les tags."""
        return [sanitize_string(tag) for tag in v if tag.strip()]

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class ChapterBase(BaseModel):
    """Modèle de base pour un chapitre.

    Attributes:
        id: ID unique du chapitre.
        number: Numéro du chapitre.
        title: Titre du chapitre.
        published_at: Date de publication.
        scanlator: Scanlator.
        pages_count: Nombre de pages.
        url: URL du chapitre.
        is_downloaded: Si le chapitre est téléchargé.
        is_read: Si le chapitre est lu.

    Example:
        >>> chapter = ChapterBase(
        ...     id="ch1",
        ...     number=1.0,
        ...     title="Chapter 1",
        ... )
    """

    id: ResourceId = Field(..., description="ID unique.")
    number: float = Field(..., ge=0, description="Numéro.")
    title: str = Field(default="", description="Titre.")
    published_at: datetime | None = Field(default=None, description="Publication.")
    scanlator: str = Field(default="", description="Scanlator.")
    pages_count: NonNegativeInt = Field(default=0, description="Nombre pages.")
    url: str = Field(default="", description="URL chapitre.")
    is_downloaded: bool = Field(default=False, description="Téléchargé.")
    is_read: bool = Field(default=False, description="Lu.")

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class SiteBase(BaseModel):
    """Modèle de base pour un site.

    Attributes:
        id: ID unique du site.
        name: Nom du site.
        language: Code langue principal.
        url: URL du site.
        enabled: Si le site est activé.
        adult: Si le site est pour adultes.
        supports_search: Supporte la recherche.
        supports_download: Supporte le téléchargement.

    Example:
        >>> site = SiteBase(
        ...     id="mangadex",
        ...     name="MangaDex",
        ...     language="en",
        ...     url="https://mangadex.org",
        ... )
    """

    id: ResourceId = Field(..., description="ID unique.")
    name: NonEmptyString = Field(..., description="Nom.")
    language: LanguageCode = Field(default="en", description="Code langue.")
    url: str = Field(default="", description="URL.")
    enabled: bool = Field(default=True, description="Activé.")
    adult: bool = Field(default=False, description="Adulte.")
    supports_search: bool = Field(default=True, description="Recherche.")
    supports_download: bool = Field(default=True, description="Téléchargement.")

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        """Valide le code langue."""
        return validate_language(v)

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class UserBase(BaseModel):
    """Modèle de base pour un utilisateur.

    Attributes:
        id: ID unique de l'utilisateur.
        username: Nom d'utilisateur.
        email: Adresse email.
        is_active: Si l'utilisateur est actif.
        created_at: Date de création.

    Example:
        >>> user = UserBase(
        ...     id="user123",
        ...     username="admin",
        ...     email="admin@example.com",
        ... )
    """

    id: ResourceId = Field(..., description="ID unique.")
    username: NonEmptyString = Field(..., description="Nom d'utilisateur.")
    email: EmailStr = Field(default="", description="Email.")
    is_active: bool = Field(default=True, description="Actif.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Création.")

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class DownloadTaskBase(BaseModel):
    """Modèle de base pour une tâche de téléchargement.

    Attributes:
        id: ID unique de la tâche.
        site_id: ID du site.
        manga_id: ID du manga.
        status: Statut de la tâche.
        progress: Progression (0.0 à 1.0).
        format: Format de téléchargement.
        quality: Qualité d'image.
        priority: Priorité.
        created_at: Date de création.

    Example:
        >>> task = DownloadTaskBase(
        ...     id="task123",
        ...     site_id="mangadex",
        ...     manga_id="123",
        ...     status="running",
        ...     progress=0.5,
        ... )
    """

    id: ResourceId = Field(..., description="ID unique.")
    site_id: ResourceId = Field(..., description="ID du site.")
    manga_id: ResourceId = Field(..., description="ID du manga.")
    status: str = Field(default="pending", description="Statut.")
    progress: float = Field(default=0.0, ge=0.0, le=1.0, description="Progression.")
    format: str = Field(default="cbz", description="Format.")
    quality: str = Field(default="original", description="Qualité.")
    priority: str = Field(default="normal", description="Priorité.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Création.")

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        """Valide le statut."""
        valid_statuses = {"pending", "running", "completed", "failed", "cancelled", "paused"}
        v = v.strip().lower()
        if v not in valid_statuses:
            raise ValueError(f"Statut invalide: {v}")
        return v

    @field_validator("format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        """Valide le format."""
        v = v.strip().lower()
        if v not in DOWNLOAD_FORMATS:
            raise ValueError(f"Format invalide: {v}")
        return v

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, v: str) -> str:
        """Valide la qualité."""
        v = v.strip().lower()
        if v not in IMAGE_QUALITIES:
            raise ValueError(f"Qualité invalide: {v}")
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        """Valide la priorité."""
        v = v.strip().lower()
        if v not in PRIORITIES:
            raise ValueError(f"Priorité invalide: {v}")
        return v

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class ReadingProgressBase(BaseModel):
    """Modèle de base pour la progression de lecture.

    Attributes:
        manga_id: ID du manga.
        status: Statut de lecture.
        chapters_read: Nombre de chapitres lus.
        total_chapters: Nombre total de chapitres.
        current_page: Page actuelle.
        last_read_at: Dernière lecture.

    Example:
        >>> progress = ReadingProgressBase(
        ...     manga_id="123",
        ...     status="reading",
        ...     chapters_read=50,
        ...     total_chapters=100,
        ... )
    """

    manga_id: ResourceId = Field(..., description="ID du manga.")
    status: str = Field(default="plan_to_read", description="Statut lecture.")
    chapters_read: NonNegativeInt = Field(default=0, description="Chapitres lus.")
    total_chapters: NonNegativeInt = Field(default=0, description="Total chapitres.")
    current_page: NonNegativeInt = Field(default=0, description="Page actuelle.")
    last_read_at: datetime | None = Field(default=None, description="Dernière lecture.")

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        """Valide le statut."""
        v = v.strip().lower()
        if v not in READING_STATUSES:
            raise ValueError(f"Statut invalide: {v}")
        return v

    @model_validator(mode="after")
    def validate_chapters(self) -> "ReadingProgressBase":
        """Valide que chapters_read <= total_chapters."""
        if self.chapters_read > self.total_chapters and self.total_chapters > 0:
            raise ValueError(
                f"chapters_read ({self.chapters_read}) ne peut pas être supérieur "
                f"à total_chapters ({self.total_chapters})"
            )
        return self

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


# ============================================================================
# MODÈLES GÉNÉRIQUES — Wrappers réutilisables
# ============================================================================


class IDModel(BaseModel):
    """Modèle avec un seul champ ID.

    Attributes:
        id: ID de la ressource.

    Example:
        >>> model = IDModel(id="123")
    """

    id: ResourceId = Field(..., description="ID.")

    model_config = ConfigDict(extra="forbid")


class StatusModel(BaseModel):
    """Modèle avec un seul champ statut.

    Attributes:
        status: Statut.

    Example:
        >>> model = StatusModel(status="active")
    """

    status: NonEmptyString = Field(..., description="Statut.")

    model_config = ConfigDict(extra="forbid")


class MessageModel(BaseModel):
    """Modèle avec un seul champ message.

    Attributes:
        message: Message.

    Example:
        >>> model = MessageModel(message="Operation successful")
    """

    message: NonEmptyString = Field(..., description="Message.")

    model_config = ConfigDict(extra="forbid")


class CountModel(BaseModel):
    """Modèle avec un seul champ count.

    Attributes:
        count: Nombre.

    Example:
        >>> model = CountModel(count=42)
    """

    count: NonNegativeInt = Field(..., description="Nombre.")

    model_config = ConfigDict(extra="forbid")


class TimestampModel(BaseModel):
    """Modèle avec un seul champ timestamp.

    Attributes:
        timestamp: Timestamp ISO 8601.

    Example:
        >>> model = TimestampModel(timestamp="2026-09-24T14:30:45Z")
    """

    timestamp: DateTimeStr = Field(..., description="Timestamp.")

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Types personnalisés
    "PositiveInt",
    "NonNegativeInt",
    "NonEmptyString",
    "EmailStr",
    "UrlStr",
    "DateTimeStr",
    "UUIDStr",
    "LanguageCode",
    "ResourceId",
    # Constantes
    "SUPPORTED_LANGUAGES",
    "SORT_ORDERS",
    "PUBLICATION_STATUSES",
    "READING_STATUSES",
    "DOWNLOAD_FORMATS",
    "IMAGE_QUALITIES",
    "PRIORITIES",
    # Validateurs
    "validate_language",
    "validate_url",
    "validate_datetime",
    "validate_id",
    "validate_positive_int",
    "validate_non_negative_int",
    # Helpers
    "to_camel_case",
    "to_snake_case",
    "sanitize_string",
    "parse_datetime",
    "generate_uuid",
    "format_datetime",
    # Modèles de domaine
    "MangaBase",
    "ChapterBase",
    "SiteBase",
    "UserBase",
    "DownloadTaskBase",
    "ReadingProgressBase",
    # Modèles génériques
    "IDModel",
    "StatusModel",
    "MessageModel",
    "CountModel",
    "TimestampModel",
]
