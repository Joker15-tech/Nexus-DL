"""Module public des schémas Pydantic de l'API REST NexusDL.

Ce module constitue le point d'entrée unique pour tous les schémas de données
utilisés par l'API REST NexusDL. Il agrège et ré-exporte les symboles publics
des 3 sous-modules :

    - `common.py`    : Types personnalisés, constantes, validateurs, helpers,
                       modèles de domaine (MangaBase, ChapterBase, etc.)
    - `requests.py`  : Schémas de requête (LoginRequest, SearchRequest, etc.)
    - `responses.py` : Schémas de réponse (APIResponse, APIErrorResponse, etc.)

Architecture :
    schemas/
        ├── common.py       : Fondations partagées
        │   ├── Types        : PositiveInt, EmailStr, LanguageCode, etc.
        │   ├── Constantes   : SUPPORTED_LANGUAGES, DOWNLOAD_FORMATS, etc.
        │   ├── Validateurs  : validate_language(), validate_url(), etc.
        │   ├── Helpers      : to_camel_case(), sanitize_string(), etc.
        │   ├── Domaines     : MangaBase, ChapterBase, SiteBase, etc.
        │   └── Génériques   : IDModel, StatusModel, MessageModel, etc.
        │
        ├── requests.py     : Schémas de requête
        │   ├── Auth         : LoginRequest, RegisterRequest, etc.
        │   ├── Search       : SearchRequest, SearchFilters, etc.
        │   ├── Manga        : DownloadMangaRequest, UpdateProgressRequest, etc.
        │   ├── Download     : CreateDownloadRequest, TaskActionRequest, etc.
        │   ├── Library      : UpdateMangaRequest, CreateReadingListRequest, etc.
        │   ├── Settings     : UpdateSettingsRequest, ExportRequest, etc.
        │   └── WebSocket    : SubscribeRequest, WebSocketMessageRequest, etc.
        │
        ├── responses.py    : Schémas de réponse
        │   ├── Canoniques   : APIResponse, APIErrorResponse, APIPaginatedResponse
        │   ├── Wrappers     : SingleResponse, ListResponse, ActionResponse
        │   ├── Spécifiques  : HealthStatusResponse, AuthTokensResponse, etc.
        │   ├── Métadonnées  : ResponseMeta, PaginationMeta, RateLimitMeta
        │   └── Helpers      : success_response(), error_response(), etc.
        │
        └── __init__.py     : Ce fichier (agrégation)

Exemple d'utilisation — Import depuis le point d'entrée :
    >>> from nexusdl.interfaces.web.backend.schemas import (
    ...     # Types
    ...     PositiveInt, EmailStr, LanguageCode,
    ...     # Modèles de domaine
    ...     MangaBase, ChapterBase, SiteBase,
    ...     # Requêtes
    ...     LoginRequest, SearchRequest, CreateDownloadRequest,
    ...     # Réponses
    ...     APIResponse, APIErrorResponse, success_response, error_response,
    ...     # Helpers
    ...     validate_language, to_camel_case,
    ... )

Exemple d'utilisation — Dans un routeur FastAPI :
    >>> from fastapi import FastAPI, HTTPException
    >>> from nexusdl.interfaces.web.backend.schemas import (
    ...     LoginRequest, success_response, error_response, ErrorCode,
    ... )
    >>>
    >>> app = FastAPI()
    >>>
    >>> @app.post("/auth/login")
    >>> async def login(request: LoginRequest):
    ...     # Pydantic valide automatiquement
    ...     return success_response(
    ...         data={"user_id": "123"},
    ...         message="Login successful",
    ...     )

Intégration :
    - pydantic                             : Validation des données
    - interfaces/web/backend/routers/*     : Utilise ces schémas
    - interfaces/web/backend/middleware/*  : Utilise ces schémas
    - core/models/*                        : Modèles de domaine du core
"""

from __future__ import annotations

# ============================================================================
# SCHÉMAS COMMUNS — common.py
# ============================================================================

# Types personnalisés
from nexusdl.interfaces.web.backend.schemas.common import (
    DateTimeStr,
    EmailStr,
    LanguageCode,
    NonEmptyString,
    NonNegativeInt,
    PositiveInt,
    ResourceId,
    UUIDStr,
    UrlStr,
)

# Constantes
from nexusdl.interfaces.web.backend.schemas.common import (
    DOWNLOAD_FORMATS,
    IMAGE_QUALITIES,
    PRIORITIES,
    PUBLICATION_STATUSES,
    READING_STATUSES,
    SORT_ORDERS,
    SUPPORTED_LANGUAGES,
)

# Validateurs
from nexusdl.interfaces.web.backend.schemas.common import (
    validate_datetime,
    validate_id,
    validate_language,
    validate_non_negative_int,
    validate_positive_int,
    validate_url,
)

# Helpers de conversion
from nexusdl.interfaces.web.backend.schemas.common import (
    format_datetime,
    generate_uuid,
    parse_datetime,
    sanitize_string,
    to_camel_case,
    to_snake_case,
)

# Modèles de domaine
from nexusdl.interfaces.web.backend.schemas.common import (
    ChapterBase,
    DownloadTaskBase,
    MangaBase,
    ReadingProgressBase,
    SiteBase,
    UserBase,
)

# Modèles génériques
from nexusdl.interfaces.web.backend.schemas.common import (
    CountModel,
    IDModel,
    MessageModel,
    StatusModel,
    TimestampModel,
)

# ============================================================================
# SCHÉMAS DE REQUÊTE — requests.py
# ============================================================================

# Enums de requête
from nexusdl.interfaces.web.backend.schemas.requests import (
    DownloadFormat,
    ExportFormat,
    ImageQuality,
    MangaStatus,
    Priority,
    ReadingStatus,
    SortOrder,
)

# Requêtes d'authentification
from nexusdl.interfaces.web.backend.schemas.requests import (
    ChangePasswordRequest,
    CreateApiKeyRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RefreshTokenRequest,
    RegisterRequest,
    ResetPasswordRequest,
    UpdateProfileRequest,
)

# Requêtes de recherche
from nexusdl.interfaces.web.backend.schemas.requests import (
    SearchFilters,
    SearchRequest,
    SiteFilterRequest,
)

# Requêtes manga & chapitres
from nexusdl.interfaces.web.backend.schemas.requests import (
    DownloadChapterRequest,
    DownloadMangaRequest,
    UpdateProgressRequest,
    UpdateReadStatusRequest,
)

# Requêtes de téléchargement
from nexusdl.interfaces.web.backend.schemas.requests import (
    BulkActionRequest,
    CreateDownloadRequest,
    TaskActionRequest,
)

# Requêtes de bibliothèque
from nexusdl.interfaces.web.backend.schemas.requests import (
    AddMangaToListRequest,
    CreateReadingListRequest,
    UpdateMangaRequest,
    UpdateReadingListRequest,
)

# Requêtes de paramètres
from nexusdl.interfaces.web.backend.schemas.requests import (
    ExportRequest,
    ImportRequest,
    ResetRequest,
    UpdateSectionRequest,
    UpdateSettingsRequest,
)

# Requêtes WebSocket
from nexusdl.interfaces.web.backend.schemas.requests import (
    SubscribeRequest,
    UnsubscribeRequest,
    WebSocketMessageRequest,
)

# Helpers de requête
from nexusdl.interfaces.web.backend.schemas.requests import (
    create_test_login_request,
    create_test_search_request,
    extract_request_fields,
    validate_request_data,
)

# Constantes de requête
from nexusdl.interfaces.web.backend.schemas.requests import (
    DEFAULT_DOWNLOAD_FORMAT,
    DEFAULT_DOWNLOAD_PRIORITY,
    DEFAULT_DOWNLOAD_QUALITY,
    DEFAULT_SEARCH_LIMIT,
    EMAIL_PATTERN,
    MAX_CHAPTERS_PER_DOWNLOAD,
    MAX_CONFIG_SIZE_BYTES,
    MAX_EMAIL_LENGTH,
    MAX_MANGAS_PER_READING_LIST,
    MAX_NOTES_LENGTH,
    MAX_PASSWORD_LENGTH,
    MAX_QUERY_LENGTH,
    MAX_READING_LIST_DESCRIPTION_LENGTH,
    MAX_READING_LIST_NAME_LENGTH,
    MAX_SEARCH_LIMIT,
    MAX_SITES_PER_SEARCH,
    MAX_TAGS_PER_MANGA,
    MAX_USERNAME_LENGTH,
    MIN_PASSWORD_LENGTH,
    MIN_QUERY_LENGTH,
    MIN_USERNAME_LENGTH,
    URL_PATTERN,
    USERNAME_PATTERN,
)

# ============================================================================
# SCHÉMAS DE RÉPONSE — responses.py
# ============================================================================

# Enums de réponse
from nexusdl.interfaces.web.backend.schemas.responses import (
    ErrorCode,
    ResponseStatus,
)

# Métadonnées de réponse
from nexusdl.interfaces.web.backend.schemas.responses import (
    PaginationMeta,
    RateLimitMeta,
    ResponseMeta,
)

# Réponses canoniques
from nexusdl.interfaces.web.backend.schemas.responses import (
    APIErrorResponse,
    APIPaginatedResponse,
    APIResponse,
)

# Wrappers génériques
from nexusdl.interfaces.web.backend.schemas.responses import (
    ActionResponse,
    BulkActionResponse,
    ListResponse,
    SingleResponse,
)

# Réponses spécifiques
from nexusdl.interfaces.web.backend.schemas.responses import (
    AuthTokensResponse,
    DetailedHealthResponse,
    HealthStatusResponse,
    MetricsResponse,
    UserProfileResponse,
    ValidationErrorDetail,
    ValidationErrorResponse,
    VersionInfoResponse,
)

# Helpers de réponse
from nexusdl.interfaces.web.backend.schemas.responses import (
    action_response,
    error_response,
    http_status_from_error_code,
    list_response,
    paginated_response,
    success_response,
    validation_error_response,
)

# Constantes de réponse
from nexusdl.interfaces.web.backend.schemas.responses import (
    DEFAULT_ERROR_MESSAGE,
    DEFAULT_FORBIDDEN_MESSAGE,
    DEFAULT_NOT_FOUND_MESSAGE,
    DEFAULT_RATE_LIMITED_MESSAGE,
    DEFAULT_SUCCESS_MESSAGE,
    DEFAULT_UNAUTHORIZED_MESSAGE,
    ERROR_BAD_REQUEST,
    ERROR_CONFLICT,
    ERROR_FORBIDDEN,
    ERROR_INTERNAL,
    ERROR_NOT_FOUND,
    ERROR_RATE_LIMITED,
    ERROR_SERVICE_UNAVAILABLE,
    ERROR_TIMEOUT,
    ERROR_UNAUTHORIZED,
    ERROR_VALIDATION,
)

# ============================================================================
# MÉTADONNÉES DU MODULE
# ============================================================================

__version__: str = "0.1.0"
__author__: str = "NexusDL Team"
__license__: str = "MIT"


# ============================================================================
# EXPORTS PUBLICS
# ============================================================================

__all__ = [
    # ========================================================================
    # Métadonnées
    # ========================================================================
    "__version__",
    "__author__",
    "__license__",
    # ========================================================================
    # TYPES PERSONNALISÉS — common.py
    # ========================================================================
    "PositiveInt",
    "NonNegativeInt",
    "NonEmptyString",
    "EmailStr",
    "UrlStr",
    "DateTimeStr",
    "UUIDStr",
    "LanguageCode",
    "ResourceId",
    # ========================================================================
    # CONSTANTES COMMUNES — common.py
    # ========================================================================
    "SUPPORTED_LANGUAGES",
    "SORT_ORDERS",
    "PUBLICATION_STATUSES",
    "READING_STATUSES",
    "DOWNLOAD_FORMATS",
    "IMAGE_QUALITIES",
    "PRIORITIES",
    # ========================================================================
    # VALIDATEURS — common.py
    # ========================================================================
    "validate_language",
    "validate_url",
    "validate_datetime",
    "validate_id",
    "validate_positive_int",
    "validate_non_negative_int",
    # ========================================================================
    # HELPERS DE CONVERSION — common.py
    # ========================================================================
    "to_camel_case",
    "to_snake_case",
    "sanitize_string",
    "parse_datetime",
    "generate_uuid",
    "format_datetime",
    # ========================================================================
    # MODÈLES DE DOMAINE — common.py
    # ========================================================================
    "MangaBase",
    "ChapterBase",
    "SiteBase",
    "UserBase",
    "DownloadTaskBase",
    "ReadingProgressBase",
    # ========================================================================
    # MODÈLES GÉNÉRIQUES — common.py
    # ========================================================================
    "IDModel",
    "StatusModel",
    "MessageModel",
    "CountModel",
    "TimestampModel",
    # ========================================================================
    # ENUMS DE REQUÊTE — requests.py
    # ========================================================================
    "DownloadFormat",
    "ImageQuality",
    "Priority",
    "ReadingStatus",
    "MangaStatus",
    "SortOrder",
    "ExportFormat",
    # ========================================================================
    # REQUÊTES D'AUTHENTIFICATION — requests.py
    # ========================================================================
    "LoginRequest",
    "RegisterRequest",
    "RefreshTokenRequest",
    "ChangePasswordRequest",
    "ForgotPasswordRequest",
    "ResetPasswordRequest",
    "CreateApiKeyRequest",
    "UpdateProfileRequest",
    # ========================================================================
    # REQUÊTES DE RECHERCHE — requests.py
    # ========================================================================
    "SearchFilters",
    "SearchRequest",
    "SiteFilterRequest",
    # ========================================================================
    # REQUÊTES MANGA & CHAPITRES — requests.py
    # ========================================================================
    "DownloadMangaRequest",
    "DownloadChapterRequest",
    "UpdateProgressRequest",
    "UpdateReadStatusRequest",
    # ========================================================================
    # REQUÊTES DE TÉLÉCHARGEMENT — requests.py
    # ========================================================================
    "CreateDownloadRequest",
    "TaskActionRequest",
    "BulkActionRequest",
    # ========================================================================
    # REQUÊTES DE BIBLIOTHÈQUE — requests.py
    # ========================================================================
    "UpdateMangaRequest",
    "CreateReadingListRequest",
    "UpdateReadingListRequest",
    "AddMangaToListRequest",
    # ========================================================================
    # REQUÊTES DE PARAMÈTRES — requests.py
    # ========================================================================
    "UpdateSettingsRequest",
    "UpdateSectionRequest",
    "ExportRequest",
    "ImportRequest",
    "ResetRequest",
    # ========================================================================
    # REQUÊTES WEBSOCKET — requests.py
    # ========================================================================
    "SubscribeRequest",
    "UnsubscribeRequest",
    "WebSocketMessageRequest",
    # ========================================================================
    # HELPERS DE REQUÊTE — requests.py
    # ========================================================================
    "validate_request_data",
    "extract_request_fields",
    "create_test_login_request",
    "create_test_search_request",
    # ========================================================================
    # CONSTANTES DE REQUÊTE — requests.py
    # ========================================================================
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
    "EMAIL_PATTERN",
    "USERNAME_PATTERN",
    "URL_PATTERN",
    # ========================================================================
    # ENUMS DE RÉPONSE — responses.py
    # ========================================================================
    "ResponseStatus",
    "ErrorCode",
    # ========================================================================
    # MÉTADONNÉES DE RÉPONSE — responses.py
    # ========================================================================
    "ResponseMeta",
    "PaginationMeta",
    "RateLimitMeta",
    # ========================================================================
    # RÉPONSES CANONIQUES — responses.py
    # ========================================================================
    "APIResponse",
    "APIErrorResponse",
    "APIPaginatedResponse",
    # ========================================================================
    # WRAPPERS GÉNÉRIQUES — responses.py
    # ========================================================================
    "SingleResponse",
    "ListResponse",
    "ActionResponse",
    "BulkActionResponse",
    # ========================================================================
    # RÉPONSES SPÉCIFIQUES — responses.py
    # ========================================================================
    "HealthStatusResponse",
    "DetailedHealthResponse",
    "VersionInfoResponse",
    "MetricsResponse",
    "AuthTokensResponse",
    "UserProfileResponse",
    "ValidationErrorDetail",
    "ValidationErrorResponse",
    # ========================================================================
    # HELPERS DE RÉPONSE — responses.py
    # ========================================================================
    "success_response",
    "error_response",
    "paginated_response",
    "list_response",
    "action_response",
    "validation_error_response",
    "http_status_from_error_code",
    # ========================================================================
    # CONSTANTES DE RÉPONSE — responses.py
    # ========================================================================
    "ERROR_NOT_FOUND",
    "ERROR_BAD_REQUEST",
    "ERROR_UNAUTHORIZED",
    "ERROR_FORBIDDEN",
    "ERROR_CONFLICT",
    "ERROR_VALIDATION",
    "ERROR_INTERNAL",
    "ERROR_SERVICE_UNAVAILABLE",
    "ERROR_RATE_LIMITED",
    "ERROR_TIMEOUT",
    "DEFAULT_SUCCESS_MESSAGE",
    "DEFAULT_ERROR_MESSAGE",
    "DEFAULT_NOT_FOUND_MESSAGE",
    "DEFAULT_UNAUTHORIZED_MESSAGE",
    "DEFAULT_FORBIDDEN_MESSAGE",
    "DEFAULT_RATE_LIMITED_MESSAGE",
]
