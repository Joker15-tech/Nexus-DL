"""Schémas de requête standardisés pour l'API REST NexusDL.

Ce module centralise tous les modèles de requête Pydantic utilisés par les
routeurs FastAPI. Il fournit des schémas canoniques avec validation stricte,
des helpers pour l'extraction des données, et des constantes pour les limites.

**Architecture** :
    requests.py
        ├── Authentification
        │   ├── LoginRequest           : Connexion username/password
        │   ├── RegisterRequest        : Inscription
        │   ├── RefreshTokenRequest    : Rafraîchir token
        │   ├── ChangePasswordRequest  : Changer mot de passe
        │   └── ForgotPasswordRequest  : Réinitialisation
        │
        ├── Sites & Recherche
        │   ├── SearchRequest          : Recherche multi-sites
        │   ├── SearchFilters          : Filtres de recherche
        │   └── SiteFilterRequest      : Filtrer les sites
        │
        ├── Mangas & Chapitres
        │   ├── DownloadMangaRequest   : Télécharger un manga
        │   ├── DownloadChapterRequest : Télécharger un chapitre
        │   ├── UpdateProgressRequest  : Progression de lecture
        │   └── UpdateReadStatusRequest: Statut lu/non-lu
        │
        ├── Téléchargements
        │   ├── CreateDownloadRequest  : Créer une tâche
        │   ├── TaskActionRequest      : Action sur tâche
        │   └── BulkActionRequest      : Action en masse
        │
        ├── Bibliothèque
        │   ├── UpdateMangaRequest     : Mettre à jour manga
        │   ├── CreateReadingListRequest: Créer liste lecture
        │   └── UpdateReadingListRequest: Modifier liste
        │
        ├── Paramètres
        │   ├── UpdateSettingsRequest  : Configuration complète
        │   ├── UpdateSectionRequest   : Section spécifique
        │   ├── ExportRequest          : Exporter config
        │   └── ImportRequest          : Importer config
        │
        └── WebSocket
            ├── SubscribeRequest       : Abonnement channel
            └── WebSocketMessageRequest: Message WS

**Utilisation** :
    >>> from nexusdl.interfaces.web.backend.schemas.requests import (
    ...     LoginRequest, SearchRequest, CreateDownloadRequest,
    ... )
    >>>
    >>> # Validation automatique via Pydantic
    >>> login_data = LoginRequest(username="admin", password="secret")
    >>> print(login_data.username)
    'admin'
    >>>
    >>> # Requête de recherche
    >>> search = SearchRequest(
    ...     query="one piece",
    ...     site_ids=["mangadex"],
    ...     language="en",
    ...     page=1,
    ... )

Intégration :
    - fastapi                      : Framework web (Depends)
    - pydantic                     : Validation des données
    - interfaces/web/backend/routers/* : Utilise ces schémas
    - core/constants.py            : Limites et défauts
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ============================================================================
# CONSTANTES — Limites et contraintes
# ============================================================================


# Authentification
MIN_USERNAME_LENGTH: int = 3
MAX_USERNAME_LENGTH: int = 50
MIN_PASSWORD_LENGTH: int = 8
MAX_PASSWORD_LENGTH: int = 128
MAX_EMAIL_LENGTH: int = 254

# Recherche
MIN_QUERY_LENGTH: int = 1
MAX_QUERY_LENGTH: int = 200
DEFAULT_SEARCH_LIMIT: int = 20
MAX_SEARCH_LIMIT: int = 100
MAX_SITES_PER_SEARCH: int = 10

# Téléchargements
MAX_CHAPTERS_PER_DOWNLOAD: int = 100
DEFAULT_DOWNLOAD_FORMAT: str = "cbz"
DEFAULT_DOWNLOAD_QUALITY: str = "original"
DEFAULT_DOWNLOAD_PRIORITY: str = "normal"

# Bibliothèque
MAX_TAGS_PER_MANGA: int = 50
MAX_MANGAS_PER_READING_LIST: int = 1000
MAX_READING_LIST_NAME_LENGTH: int = 100
MAX_READING_LIST_DESCRIPTION_LENGTH: int = 500

# Paramètres
MAX_CONFIG_SIZE_BYTES: int = 10485760  # 10 MB
MAX_NOTES_LENGTH: int = 10000

# Patterns de validation
EMAIL_PATTERN: str = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
USERNAME_PATTERN: str = r"^[a-zA-Z0-9_-]+$"
URL_PATTERN: str = r"^https?://"


# ============================================================================
# ENUMS — Valeurs autorisées
# ============================================================================


class DownloadFormat(str, Enum):
    """Format de téléchargement.

    Attributes:
        CBZ: Archive CBZ (Comic Book ZIP).
        CBR: Archive CBR (Comic Book RAR).
        PDF: Document PDF.
        ZIP: Archive ZIP.
        FOLDER: Dossier avec images.
    """

    CBZ = "cbz"
    CBR = "cbr"
    PDF = "pdf"
    ZIP = "zip"
    FOLDER = "folder"


class ImageQuality(str, Enum):
    """Qualité d'image.

    Attributes:
        ORIGINAL: Qualité originale.
        HIGH: Haute qualité.
        MEDIUM: Qualité moyenne.
        LOW: Basse qualité.
    """

    ORIGINAL = "original"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Priority(str, Enum):
    """Priorité de tâche.

    Attributes:
        LOW: Priorité basse.
        NORMAL: Priorité normale.
        HIGH: Priorité haute.
        URGENT: Priorité urgente.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class ReadingStatus(str, Enum):
    """Statut de lecture.

    Attributes:
        READING: En cours de lecture.
        COMPLETED: Terminé.
        PLAN_TO_READ: À lire.
        ON_HOLD: En pause.
        DROPPED: Abandonné.
    """

    READING = "reading"
    COMPLETED = "completed"
    PLAN_TO_READ = "plan_to_read"
    ON_HOLD = "on_hold"
    DROPPED = "dropped"


class MangaStatus(str, Enum):
    """Statut de publication d'un manga.

    Attributes:
        ONGOING: En cours.
        COMPLETED: Terminé.
        HIATUS: En pause.
        CANCELLED: Annulé.
    """

    ONGOING = "ongoing"
    COMPLETED = "completed"
    HIATUS = "hiatus"
    CANCELLED = "cancelled"


class SortOrder(str, Enum):
    """Ordre de tri.

    Attributes:
        ASC: Ascendant.
        DESC: Descendant.
    """

    ASC = "asc"
    DESC = "desc"


class ExportFormat(str, Enum):
    """Format d'export.

    Attributes:
        JSON: Format JSON.
        YAML: Format YAML.
    """

    JSON = "json"
    YAML = "yaml"


# ============================================================================
# AUTHENTIFICATION — Requêtes liées à l'auth
# ============================================================================


class LoginRequest(BaseModel):
    """Requête de connexion.

    Attributes:
        username: Nom d'utilisateur.
        password: Mot de passe.
        remember_me: Se souvenir de la session.

    Example:
        {
            "username": "admin",
            "password": "SecurePass123!",
            "remember_me": true
        }
    """

    username: str = Field(
        ...,
        min_length=MIN_USERNAME_LENGTH,
        max_length=MAX_USERNAME_LENGTH,
        description="Nom d'utilisateur.",
    )
    password: str = Field(
        ...,
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
        description="Mot de passe.",
    )
    remember_me: bool = Field(
        default=False,
        description="Se souvenir de la session.",
    )

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        """Valide le nom d'utilisateur."""
        v = v.strip()
        if not re.match(USERNAME_PATTERN, v):
            raise ValueError(
                "Le nom d'utilisateur ne peut contenir que des lettres, "
                "chiffres, tirets et underscores"
            )
        return v

    model_config = ConfigDict(extra="forbid")


class RegisterRequest(BaseModel):
    """Requête d'inscription.

    Attributes:
        username: Nom d'utilisateur.
        email: Adresse email.
        password: Mot de passe.
        password_confirm: Confirmation du mot de passe.

    Example:
        {
            "username": "newuser",
            "email": "user@example.com",
            "password": "SecurePass123!",
            "password_confirm": "SecurePass123!"
        }
    """

    username: str = Field(
        ...,
        min_length=MIN_USERNAME_LENGTH,
        max_length=MAX_USERNAME_LENGTH,
        description="Nom d'utilisateur.",
    )
    email: str = Field(
        ...,
        max_length=MAX_EMAIL_LENGTH,
        description="Adresse email.",
    )
    password: str = Field(
        ...,
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
        description="Mot de passe.",
    )
    password_confirm: str = Field(
        ...,
        description="Confirmation du mot de passe.",
    )

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        """Valide le nom d'utilisateur."""
        v = v.strip()
        if not re.match(USERNAME_PATTERN, v):
            raise ValueError(
                "Le nom d'utilisateur ne peut contenir que des lettres, "
                "chiffres, tirets et underscores"
            )
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        """Valide l'email."""
        v = v.strip().lower()
        if not re.match(EMAIL_PATTERN, v):
            raise ValueError("Adresse email invalide")
        return v

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """Valide la force du mot de passe."""
        if not any(c.isupper() for c in v):
            raise ValueError("Le mot de passe doit contenir au moins une majuscule")
        if not any(c.islower() for c in v):
            raise ValueError("Le mot de passe doit contenir au moins une minuscule")
        if not any(c.isdigit() for c in v):
            raise ValueError("Le mot de passe doit contenir au moins un chiffre")
        return v

    @model_validator(mode="after")
    def validate_passwords_match(self) -> "RegisterRequest":
        """Vérifie que les mots de passe correspondent."""
        if self.password != self.password_confirm:
            raise ValueError("Les mots de passe ne correspondent pas")
        return self

    model_config = ConfigDict(extra="forbid")


class RefreshTokenRequest(BaseModel):
    """Requête de rafraîchissement de token.

    Attributes:
        refresh_token: Token de refresh.

    Example:
        {
            "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
        }
    """

    refresh_token: str = Field(
        ...,
        min_length=10,
        description="Token de refresh.",
    )

    model_config = ConfigDict(extra="forbid")


class ChangePasswordRequest(BaseModel):
    """Requête de changement de mot de passe.

    Attributes:
        current_password: Mot de passe actuel.
        new_password: Nouveau mot de passe.
        new_password_confirm: Confirmation du nouveau mot de passe.

    Example:
        {
            "current_password": "OldPass123!",
            "new_password": "NewPass456!",
            "new_password_confirm": "NewPass456!"
        }
    """

    current_password: str = Field(
        ...,
        description="Mot de passe actuel.",
    )
    new_password: str = Field(
        ...,
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
        description="Nouveau mot de passe.",
    )
    new_password_confirm: str = Field(
        ...,
        description="Confirmation du nouveau mot de passe.",
    )

    @field_validator("new_password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """Valide la force du mot de passe."""
        if not any(c.isupper() for c in v):
            raise ValueError("Le mot de passe doit contenir au moins une majuscule")
        if not any(c.islower() for c in v):
            raise ValueError("Le mot de passe doit contenir au moins une minuscule")
        if not any(c.isdigit() for c in v):
            raise ValueError("Le mot de passe doit contenir au moins un chiffre")
        return v

    @model_validator(mode="after")
    def validate_passwords_match(self) -> "ChangePasswordRequest":
        """Vérifie que les mots de passe correspondent."""
        if self.new_password != self.new_password_confirm:
            raise ValueError("Les mots de passe ne correspondent pas")
        if self.current_password == self.new_password:
            raise ValueError("Le nouveau mot de passe doit être différent de l'ancien")
        return self

    model_config = ConfigDict(extra="forbid")


class ForgotPasswordRequest(BaseModel):
    """Requête de mot de passe oublié.

    Attributes:
        email: Adresse email du compte.

    Example:
        {
            "email": "user@example.com"
        }
    """

    email: str = Field(
        ...,
        max_length=MAX_EMAIL_LENGTH,
        description="Adresse email.",
    )

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        """Valide l'email."""
        v = v.strip().lower()
        if not re.match(EMAIL_PATTERN, v):
            raise ValueError("Adresse email invalide")
        return v

    model_config = ConfigDict(extra="forbid")


class ResetPasswordRequest(BaseModel):
    """Requête de réinitialisation de mot de passe.

    Attributes:
        token: Token de réinitialisation.
        new_password: Nouveau mot de passe.
        new_password_confirm: Confirmation.

    Example:
        {
            "token": "abc123def456...",
            "new_password": "NewPass789!",
            "new_password_confirm": "NewPass789!"
        }
    """

    token: str = Field(
        ...,
        min_length=10,
        description="Token de réinitialisation.",
    )
    new_password: str = Field(
        ...,
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
        description="Nouveau mot de passe.",
    )
    new_password_confirm: str = Field(
        ...,
        description="Confirmation du nouveau mot de passe.",
    )

    @field_validator("new_password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """Valide la force du mot de passe."""
        if not any(c.isupper() for c in v):
            raise ValueError("Le mot de passe doit contenir au moins une majuscule")
        if not any(c.islower() for c in v):
            raise ValueError("Le mot de passe doit contenir au moins une minuscule")
        if not any(c.isdigit() for c in v):
            raise ValueError("Le mot de passe doit contenir au moins un chiffre")
        return v

    @model_validator(mode="after")
    def validate_passwords_match(self) -> "ResetPasswordRequest":
        """Vérifie que les mots de passe correspondent."""
        if self.new_password != self.new_password_confirm:
            raise ValueError("Les mots de passe ne correspondent pas")
        return self

    model_config = ConfigDict(extra="forbid")


class CreateApiKeyRequest(BaseModel):
    """Requête de création d'API key.

    Attributes:
        name: Nom descriptif de la clé.
        permissions: Permissions associées.
        expires_days: Durée de validité en jours (None = jamais).

    Example:
        {
            "name": "My Integration",
            "permissions": ["read", "download"],
            "expires_days": 90
        }
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Nom descriptif.",
    )
    permissions: list[str] = Field(
        default_factory=list,
        description="Permissions associées.",
    )
    expires_days: int | None = Field(
        default=None,
        ge=1,
        le=365,
        description="Durée de validité (jours).",
    )

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, v: list[str]) -> list[str]:
        """Valide les permissions."""
        valid_permissions = {"read", "write", "delete", "admin", "download", "upload"}
        invalid = [p for p in v if p not in valid_permissions]
        if invalid:
            raise ValueError(f"Permissions invalides: {invalid}")
        return v

    model_config = ConfigDict(extra="forbid")


class UpdateProfileRequest(BaseModel):
    """Requête de mise à jour du profil.

    Attributes:
        email: Nouvel email (optionnel).
        metadata: Métadonnées à mettre à jour.

    Example:
        {
            "email": "newemail@example.com",
            "metadata": {"theme": "dark", "language": "fr"}
        }
    """

    email: str | None = Field(
        default=None,
        max_length=MAX_EMAIL_LENGTH,
        description="Nouvel email.",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Métadonnées.",
    )

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        """Valide l'email."""
        if v is not None:
            v = v.strip().lower()
            if not re.match(EMAIL_PATTERN, v):
                raise ValueError("Adresse email invalide")
        return v

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# SITES & RECHERCHE — Requêtes liées aux sites
# ============================================================================


class SearchFilters(BaseModel):
    """Filtres de recherche.

    Attributes:
        language: Code langue (ISO 639-1).
        status: Statut de publication.
        include_adult: Inclure contenu adulte.
        tags: Tags à filtrer.

    Example:
        {
            "language": "en",
            "status": "ongoing",
            "include_adult": false,
            "tags": ["action", "adventure"]
        }
    """

    language: str | None = Field(
        default=None,
        description="Code langue (ISO 639-1).",
    )
    status: MangaStatus | None = Field(
        default=None,
        description="Statut de publication.",
    )
    include_adult: bool = Field(
        default=False,
        description="Inclure contenu adulte.",
    )
    tags: list[str] | None = Field(
        default=None,
        description="Tags à filtrer.",
    )

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str | None) -> str | None:
        """Valide le code langue."""
        if v is not None:
            v = v.strip().lower()
            if len(v) != 2:
                raise ValueError("Le code langue doit être sur 2 caractères (ISO 639-1)")
        return v

    model_config = ConfigDict(extra="forbid")


class SearchRequest(BaseModel):
    """Requête de recherche multi-sites.

    Attributes:
        query: Texte de recherche.
        site_ids: IDs des sites à rechercher (None = tous).
        filters: Filtres de recherche.
        sort_by: Critère de tri.
        sort_order: Ordre de tri.
        page: Numéro de page.
        page_size: Taille de page.
        timeout: Timeout en secondes.

    Example:
        {
            "query": "one piece",
            "site_ids": ["mangadex", "asurascans"],
            "filters": {
                "language": "en",
                "status": "ongoing"
            },
            "sort_by": "relevance",
            "page": 1,
            "page_size": 20
        }
    """

    query: str = Field(
        ...,
        min_length=MIN_QUERY_LENGTH,
        max_length=MAX_QUERY_LENGTH,
        description="Texte de recherche.",
    )
    site_ids: list[str] | None = Field(
        default=None,
        description="IDs des sites (None = tous).",
    )
    filters: SearchFilters | None = Field(
        default=None,
        description="Filtres de recherche.",
    )
    sort_by: str = Field(
        default="relevance",
        description="Critère de tri.",
    )
    sort_order: SortOrder = Field(
        default=SortOrder.DESC,
        description="Ordre de tri.",
    )
    page: int = Field(
        default=1,
        ge=1,
        description="Numéro de page.",
    )
    page_size: int = Field(
        default=DEFAULT_SEARCH_LIMIT,
        ge=1,
        le=MAX_SEARCH_LIMIT,
        description="Taille de page.",
    )
    timeout: float = Field(
        default=30.0,
        ge=1.0,
        le=120.0,
        description="Timeout (secondes).",
    )

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        """Valide la requête."""
        v = v.strip()
        if not v:
            raise ValueError("La requête ne peut pas être vide")
        return v

    @field_validator("site_ids")
    @classmethod
    def validate_site_ids(cls, v: list[str] | None) -> list[str] | None:
        """Valide les IDs de sites."""
        if v is not None and len(v) > MAX_SITES_PER_SEARCH:
            raise ValueError(f"Maximum {MAX_SITES_PER_SEARCH} sites par recherche")
        return v

    model_config = ConfigDict(extra="forbid")


class SiteFilterRequest(BaseModel):
    """Requête de filtrage des sites.

    Attributes:
        language: Code langue (ISO 639-1).
        enabled_only: Inclure uniquement les sites activés.
        include_adult: Inclure les sites adultes.
        tags: Tags à filtrer.

    Example:
        {
            "language": "fr",
            "enabled_only": true,
            "include_adult": false
        }
    """

    language: str | None = Field(
        default=None,
        description="Code langue.",
    )
    enabled_only: bool = Field(
        default=True,
        description="Sites activés uniquement.",
    )
    include_adult: bool = Field(
        default=False,
        description="Inclure sites adultes.",
    )
    tags: list[str] | None = Field(
        default=None,
        description="Tags à filtrer.",
    )

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str | None) -> str | None:
        """Valide le code langue."""
        if v is not None:
            v = v.strip().lower()
            if len(v) != 2:
                raise ValueError("Le code langue doit être sur 2 caractères")
        return v

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# MANGAS & CHAPITRES — Requêtes liées aux mangas
# ============================================================================


class DownloadMangaRequest(BaseModel):
    """Requête de téléchargement d'un manga.

    Attributes:
        chapter_ids: IDs des chapitres à télécharger (None = tous).
        format: Format de téléchargement.
        quality: Qualité d'image.
        priority: Priorité de la tâche.
        output_dir: Répertoire de sortie (optionnel).

    Example:
        {
            "chapter_ids": ["ch1", "ch2", "ch3"],
            "format": "cbz",
            "quality": "original",
            "priority": "normal"
        }
    """

    chapter_ids: list[str] | None = Field(
        default=None,
        description="Chapitres à télécharger (None = tous).",
    )
    format: DownloadFormat = Field(
        default=DownloadFormat.CBZ,
        description="Format de téléchargement.",
    )
    quality: ImageQuality = Field(
        default=ImageQuality.ORIGINAL,
        description="Qualité d'image.",
    )
    priority: Priority = Field(
        default=Priority.NORMAL,
        description="Priorité.",
    )
    output_dir: str | None = Field(
        default=None,
        description="Répertoire de sortie.",
    )

    @field_validator("chapter_ids")
    @classmethod
    def validate_chapter_ids(cls, v: list[str] | None) -> list[str] | None:
        """Valide les IDs de chapitres."""
        if v is not None and len(v) > MAX_CHAPTERS_PER_DOWNLOAD:
            raise ValueError(f"Maximum {MAX_CHAPTERS_PER_DOWNLOAD} chapitres par téléchargement")
        return v

    model_config = ConfigDict(extra="forbid")


class DownloadChapterRequest(BaseModel):
    """Requête de téléchargement d'un chapitre.

    Attributes:
        format: Format de téléchargement.
        quality: Qualité d'image.
        priority: Priorité de la tâche.

    Example:
        {
            "format": "cbz",
            "quality": "high",
            "priority": "high"
        }
    """

    format: DownloadFormat = Field(
        default=DownloadFormat.CBZ,
        description="Format de téléchargement.",
    )
    quality: ImageQuality = Field(
        default=ImageQuality.ORIGINAL,
        description="Qualité d'image.",
    )
    priority: Priority = Field(
        default=Priority.NORMAL,
        description="Priorité.",
    )

    model_config = ConfigDict(extra="forbid")


class UpdateProgressRequest(BaseModel):
    """Requête de mise à jour de la progression.

    Attributes:
        current_page: Page actuelle (1-indexed).
        read_status: Statut de lecture (optionnel).

    Example:
        {
            "current_page": 15,
            "read_status": "reading"
        }
    """

    current_page: int = Field(
        ...,
        ge=1,
        le=10000,
        description="Page actuelle.",
    )
    read_status: ReadingStatus | None = Field(
        default=None,
        description="Statut de lecture.",
    )

    model_config = ConfigDict(extra="forbid")


class UpdateReadStatusRequest(BaseModel):
    """Requête de mise à jour du statut de lecture.

    Attributes:
        is_read: Si le chapitre est lu.

    Example:
        {
            "is_read": true
        }
    """

    is_read: bool = Field(
        ...,
        description="Statut de lecture.",
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# TÉLÉCHARGEMENTS — Requêtes liées aux tâches
# ============================================================================


class CreateDownloadRequest(BaseModel):
    """Requête de création d'une tâche de téléchargement.

    Attributes:
        site_id: ID du site.
        manga_id: ID du manga.
        chapter_ids: IDs des chapitres (None = tous).
        format: Format de téléchargement.
        quality: Qualité d'image.
        priority: Priorité.
        output_dir: Répertoire de sortie.

    Example:
        {
            "site_id": "mangadex",
            "manga_id": "12345",
            "chapter_ids": ["ch1", "ch2"],
            "format": "cbz",
            "quality": "original",
            "priority": "normal"
        }
    """

    site_id: str = Field(
        ...,
        min_length=1,
        description="ID du site.",
    )
    manga_id: str = Field(
        ...,
        min_length=1,
        description="ID du manga.",
    )
    chapter_ids: list[str] | None = Field(
        default=None,
        description="Chapitres à télécharger.",
    )
    format: DownloadFormat = Field(
        default=DownloadFormat.CBZ,
        description="Format.",
    )
    quality: ImageQuality = Field(
        default=ImageQuality.ORIGINAL,
        description="Qualité.",
    )
    priority: Priority = Field(
        default=Priority.NORMAL,
        description="Priorité.",
    )
    output_dir: str | None = Field(
        default=None,
        description="Répertoire de sortie.",
    )

    @field_validator("chapter_ids")
    @classmethod
    def validate_chapter_ids(cls, v: list[str] | None) -> list[str] | None:
        """Valide les IDs de chapitres."""
        if v is not None and len(v) > MAX_CHAPTERS_PER_DOWNLOAD:
            raise ValueError(f"Maximum {MAX_CHAPTERS_PER_DOWNLOAD} chapitres")
        return v

    model_config = ConfigDict(extra="forbid")


class TaskActionRequest(BaseModel):
    """Requête d'action sur une tâche.

    Attributes:
        reason: Raison de l'action (optionnel).

    Example:
        {
            "reason": "User requested pause"
        }
    """

    reason: str | None = Field(
        default=None,
        max_length=500,
        description="Raison de l'action.",
    )

    model_config = ConfigDict(extra="forbid")


class BulkActionRequest(BaseModel):
    """Requête d'action en masse.

    Attributes:
        task_ids: IDs des tâches (None = toutes).
        reason: Raison de l'action.

    Example:
        {
            "task_ids": ["task1", "task2"],
            "reason": "Batch operation"
        }
    """

    task_ids: list[str] | None = Field(
        default=None,
        description="IDs des tâches (None = toutes).",
    )
    reason: str | None = Field(
        default=None,
        max_length=500,
        description="Raison.",
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# BIBLIOTHÈQUE — Requêtes liées à la bibliothèque
# ============================================================================


class UpdateMangaRequest(BaseModel):
    """Requête de mise à jour d'un manga.

    Attributes:
        reading_status: Statut de lecture.
        tags: Tags du manga.
        notes: Notes personnelles.
        current_page: Page actuelle.

    Example:
        {
            "reading_status": "reading",
            "tags": ["favorite", "action"],
            "notes": "Great manga!",
            "current_page": 42
        }
    """

    reading_status: ReadingStatus | None = Field(
        default=None,
        description="Statut de lecture.",
    )
    tags: list[str] | None = Field(
        default=None,
        description="Tags.",
    )
    notes: str | None = Field(
        default=None,
        max_length=MAX_NOTES_LENGTH,
        description="Notes.",
    )
    current_page: int | None = Field(
        default=None,
        ge=0,
        description="Page actuelle.",
    )

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str] | None) -> list[str] | None:
        """Valide les tags."""
        if v is not None:
            if len(v) > MAX_TAGS_PER_MANGA:
                raise ValueError(f"Maximum {MAX_TAGS_PER_MANGA} tags")
            v = [tag.strip() for tag in v if tag.strip()]
        return v

    model_config = ConfigDict(extra="forbid")


class CreateReadingListRequest(BaseModel):
    """Requête de création d'une liste de lecture.

    Attributes:
        name: Nom de la liste.
        description: Description.
        manga_ids: IDs des mangas à inclure.

    Example:
        {
            "name": "Favorites",
            "description": "My favorite mangas",
            "manga_ids": ["manga1", "manga2"]
        }
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=MAX_READING_LIST_NAME_LENGTH,
        description="Nom de la liste.",
    )
    description: str = Field(
        default="",
        max_length=MAX_READING_LIST_DESCRIPTION_LENGTH,
        description="Description.",
    )
    manga_ids: list[str] = Field(
        default_factory=list,
        description="Mangas à inclure.",
    )

    @field_validator("manga_ids")
    @classmethod
    def validate_manga_ids(cls, v: list[str]) -> list[str]:
        """Valide les IDs de mangas."""
        if len(v) > MAX_MANGAS_PER_READING_LIST:
            raise ValueError(f"Maximum {MAX_MANGAS_PER_READING_LIST} mangas par liste")
        return v

    model_config = ConfigDict(extra="forbid")


class UpdateReadingListRequest(BaseModel):
    """Requête de mise à jour d'une liste de lecture.

    Attributes:
        name: Nouveau nom.
        description: Nouvelle description.

    Example:
        {
            "name": "Updated Favorites",
            "description": "My updated list"
        }
    """

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_READING_LIST_NAME_LENGTH,
        description="Nouveau nom.",
    )
    description: str | None = Field(
        default=None,
        max_length=MAX_READING_LIST_DESCRIPTION_LENGTH,
        description="Nouvelle description.",
    )

    model_config = ConfigDict(extra="forbid")


class AddMangaToListRequest(BaseModel):
    """Requête d'ajout de mangas à une liste.

    Attributes:
        manga_ids: IDs des mangas à ajouter.

    Example:
        {
            "manga_ids": ["manga1", "manga2", "manga3"]
        }
    """

    manga_ids: list[str] = Field(
        ...,
        min_length=1,
        description="Mangas à ajouter.",
    )

    @field_validator("manga_ids")
    @classmethod
    def validate_manga_ids(cls, v: list[str]) -> list[str]:
        """Valide les IDs de mangas."""
        if len(v) > 100:
            raise ValueError("Maximum 100 mangas par opération")
        return v

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# PARAMÈTRES — Requêtes liées à la configuration
# ============================================================================


class UpdateSettingsRequest(BaseModel):
    """Requête de mise à jour complète de la configuration.

    Attributes:
        config: Configuration complète.

    Example:
        {
            "config": {
                "app": {"language": "fr", "theme": "dark"},
                "network": {"timeout": 60}
            }
        }
    """

    config: dict[str, Any] = Field(
        ...,
        description="Configuration complète.",
    )

    @field_validator("config")
    @classmethod
    def validate_config_size(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Valide la taille de la configuration."""
        import json
        size = len(json.dumps(v).encode())
        if size > MAX_CONFIG_SIZE_BYTES:
            raise ValueError(f"Configuration trop volumineuse: {size} bytes (max: {MAX_CONFIG_SIZE_BYTES})")
        return v

    model_config = ConfigDict(extra="forbid")


class UpdateSectionRequest(BaseModel):
    """Requête de mise à jour d'une section de configuration.

    Attributes:
        data: Données de la section.

    Example:
        {
            "data": {
                "timeout": 60,
                "max_connections": 200
            }
        }
    """

    data: dict[str, Any] = Field(
        ...,
        description="Données de la section.",
    )

    model_config = ConfigDict(extra="forbid")


class ExportRequest(BaseModel):
    """Requête d'export de configuration.

    Attributes:
        format: Format d'export.
        include_metadata: Inclure les métadonnées.
        sections: Sections à exporter (None = toutes).

    Example:
        {
            "format": "json",
            "include_metadata": true,
            "sections": ["app", "network"]
        }
    """

    format: ExportFormat = Field(
        default=ExportFormat.JSON,
        description="Format d'export.",
    )
    include_metadata: bool = Field(
        default=True,
        description="Inclure métadonnées.",
    )
    sections: list[str] | None = Field(
        default=None,
        description="Sections à exporter.",
    )

    @field_validator("sections")
    @classmethod
    def validate_sections(cls, v: list[str] | None) -> list[str] | None:
        """Valide les sections."""
        if v is not None:
            valid_sections = {
                "app", "network", "proxy", "logging", "download",
                "library", "cloudflare", "i18n", "storage", "events", "interface",
            }
            invalid = [s for s in v if s not in valid_sections]
            if invalid:
                raise ValueError(f"Sections invalides: {invalid}")
        return v

    model_config = ConfigDict(extra="forbid")


class ImportRequest(BaseModel):
    """Requête d'import de configuration.

    Attributes:
        config: Configuration à importer.
        overwrite: Écraser la configuration existante.
        validate: Valider avant import.

    Example:
        {
            "config": {...},
            "overwrite": true,
            "validate": true
        }
    """

    config: dict[str, Any] = Field(
        ...,
        description="Configuration à importer.",
    )
    overwrite: bool = Field(
        default=True,
        description="Écraser existante.",
    )
    validate: bool = Field(
        default=True,
        description="Valider avant import.",
    )

    @field_validator("config")
    @classmethod
    def validate_config_size(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Valide la taille de la configuration."""
        import json
        size = len(json.dumps(v).encode())
        if size > MAX_CONFIG_SIZE_BYTES:
            raise ValueError(f"Configuration trop volumineuse: {size} bytes")
        return v

    model_config = ConfigDict(extra="forbid")


class ResetRequest(BaseModel):
    """Requête de réinitialisation de configuration.

    Attributes:
        sections: Sections à réinitialiser (None = toutes).
        confirm: Confirmation explicite.

    Example:
        {
            "sections": ["network", "proxy"],
            "confirm": true
        }
    """

    sections: list[str] | None = Field(
        default=None,
        description="Sections à réinitialiser.",
    )
    confirm: bool = Field(
        default=False,
        description="Confirmation.",
    )

    @field_validator("confirm")
    @classmethod
    def validate_confirm(cls, v: bool) -> bool:
        """Valide la confirmation."""
        if not v:
            raise ValueError("Confirmation requise")
        return v

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# WEBSOCKET — Requêtes liées au WebSocket
# ============================================================================


class SubscribeRequest(BaseModel):
    """Requête d'abonnement à un channel WebSocket.

    Attributes:
        channel: Nom du channel.

    Example:
        {
            "channel": "downloads.progress"
        }
    """

    channel: str = Field(
        ...,
        min_length=1,
        description="Nom du channel.",
    )

    model_config = ConfigDict(extra="forbid")


class UnsubscribeRequest(BaseModel):
    """Requête de désabonnement d'un channel WebSocket.

    Attributes:
        channel: Nom du channel.

    Example:
        {
            "channel": "downloads.progress"
        }
    """

    channel: str = Field(
        ...,
        min_length=1,
        description="Nom du channel.",
    )

    model_config = ConfigDict(extra="forbid")


class WebSocketMessageRequest(BaseModel):
    """Requête de message WebSocket.

    Attributes:
        type: Type de message.
        channel: Channel cible.
        payload: Contenu du message.

    Example:
        {
            "type": "message",
            "channel": "notifications.info",
            "payload": {"message": "Hello"}
        }
    """

    type: str = Field(
        ...,
        min_length=1,
        description="Type de message.",
    )
    channel: str | None = Field(
        default=None,
        description="Channel cible.",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Contenu.",
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================


def validate_request_data(data: dict[str, Any], model_class: type[BaseModel]) -> BaseModel:
    """Valide des données de requête contre un modèle.

    Args:
        data: Données à valider.
        model_class: Classe du modèle Pydantic.

    Returns:
        Instance validée du modèle.

    Raises:
        ValidationError: Si la validation échoue.

    Example:
        >>> data = {"username": "admin", "password": "secret"}
        >>> login = validate_request_data(data, LoginRequest)
        >>> print(login.username)
        'admin'
    """
    return model_class.model_validate(data)


def extract_request_fields(request: BaseModel, fields: list[str] | None = None) -> dict[str, Any]:
    """Extrait des champs spécifiques d'une requête.

    Args:
        request: Instance de requête.
        fields: Champs à extraire (None = tous).

    Returns:
        Dictionnaire des champs extraits.

    Example:
        >>> login = LoginRequest(username="admin", password="secret")
        >>> fields = extract_request_fields(login, ["username"])
        >>> print(fields)
        {'username': 'admin'}
    """
    data = request.model_dump()
    if fields is None:
        return data
    return {k: v for k, v in data.items() if k in fields}


def create_test_login_request(
    username: str = "testuser",
    password: str = "TestPass123!",
) -> LoginRequest:
    """Crée une requête de connexion de test.

    Args:
        username: Nom d'utilisateur.
        password: Mot de passe.

    Returns:
        Instance de LoginRequest.

    Example:
        >>> login = create_test_login_request()
        >>> print(login.username)
        'testuser'
    """
    return LoginRequest(username=username, password=password)


def create_test_search_request(
    query: str = "test query",
    site_ids: list[str] | None = None,
) -> SearchRequest:
    """Crée une requête de recherche de test.

    Args:
        query: Texte de recherche.
        site_ids: IDs des sites.

    Returns:
        Instance de SearchRequest.

    Example:
        >>> search = create_test_search_request("one piece")
        >>> print(search.query)
        'one piece'
    """
    return SearchRequest(query=query, site_ids=site_ids)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "MIN_USERNAME_LENGTH",
    "MAX_USERNAME_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "MAX_PASSWORD_LENGTH",
    "MAX_EMAIL_LENGTH",
    "MIN_QUERY_LENGTH",
    "MAX_QUERY_LENGTH",
    "DEFAULT_SEARCH_LIMIT",
    "MAX_SEARCH_LIMIT",
    "MAX_SITES_PER_SEARCH",
    "MAX_CHAPTERS_PER_DOWNLOAD",
    "DEFAULT_DOWNLOAD_FORMAT",
    "DEFAULT_DOWNLOAD_QUALITY",
    "DEFAULT_DOWNLOAD_PRIORITY",
    "MAX_TAGS_PER_MANGA",
    "MAX_MANGAS_PER_READING_LIST",
    "MAX_READING_LIST_NAME_LENGTH",
    "MAX_READING_LIST_DESCRIPTION_LENGTH",
    "MAX_CONFIG_SIZE_BYTES",
    "MAX_NOTES_LENGTH",
    # Patterns
    "EMAIL_PATTERN",
    "USERNAME_PATTERN",
    "URL_PATTERN",
    # Enums
    "DownloadFormat",
    "ImageQuality",
    "Priority",
    "ReadingStatus",
    "MangaStatus",
    "SortOrder",
    "ExportFormat",
    # Authentification
    "LoginRequest",
    "RegisterRequest",
    "RefreshTokenRequest",
    "ChangePasswordRequest",
    "ForgotPasswordRequest",
    "ResetPasswordRequest",
    "CreateApiKeyRequest",
    "UpdateProfileRequest",
    # Sites & Recherche
    "SearchFilters",
    "SearchRequest",
    "SiteFilterRequest",
    # Mangas & Chapitres
    "DownloadMangaRequest",
    "DownloadChapterRequest",
    "UpdateProgressRequest",
    "UpdateReadStatusRequest",
    # Téléchargements
    "CreateDownloadRequest",
    "TaskActionRequest",
    "BulkActionRequest",
    # Bibliothèque
    "UpdateMangaRequest",
    "CreateReadingListRequest",
    "UpdateReadingListRequest",
    "AddMangaToListRequest",
    # Paramètres
    "UpdateSettingsRequest",
    "UpdateSectionRequest",
    "ExportRequest",
    "ImportRequest",
    "ResetRequest",
    # WebSocket
    "SubscribeRequest",
    "UnsubscribeRequest",
    "WebSocketMessageRequest",
    # Helpers
    "validate_request_data",
    "extract_request_fields",
    "create_test_login_request",
    "create_test_search_request",
]
