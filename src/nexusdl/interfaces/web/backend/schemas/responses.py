"""Schémas de réponse standardisés pour l'API REST NexusDL.

Ce module centralise tous les modèles de réponse Pydantic utilisés par les
routeurs FastAPI. Il fournit des schémas canoniques, des wrappers génériques,
et des helpers pour construire des réponses cohérentes.

**Architecture** :
    responses.py
        ├── Réponses canoniques
        │   ├── APIResponse          : Enveloppe générique
        │   ├── APIErrorResponse     : Erreur standardisée
        │   └── APIPaginatedResponse : Réponse paginée
        │
        ├── Wrappers génériques
        │   ├── SingleResponse[T]    : Réponse avec un seul objet
        │   ├── ListResponse[T]      : Réponse avec une liste
        │   ├── PaginatedResponse[T] : Réponse paginée
        │   └── ActionResponse       : Réponse d'action (succès/échec)
        │
        ├── Réponses spécifiques
        │   ├── HealthResponse       : Santé de l'API
        │   ├── VersionResponse      : Informations de version
        │   ├── MetricsResponse      : Métriques système
        │   ├── AuthResponse         : Tokens JWT
        │   └── UserProfileResponse  : Profil utilisateur
        │
        ├── Métadonnées
        │   ├── ResponseMeta         : Métadonnées de réponse
        │   ├── PaginationMeta       : Métadonnées de pagination
        │   └── RateLimitMeta        : Métadonnées de rate limiting
        │
        └── Helpers
            ├── success_response()   : Construire une réponse de succès
            ├── error_response()     : Construire une réponse d'erreur
            ├── paginated_response() : Construire une réponse paginée
            └── list_response()      : Construire une réponse de liste

**Utilisation** :
    >>> from nexusdl.interfaces.web.backend.schemas.responses import (
    ...     APIResponse, APIErrorResponse, success_response, error_response,
    ... )
    >>>
    >>> # Réponse de succès
    >>> return success_response(data={"manga_id": "123"}, message="OK")
    >>>
    >>> # Réponse d'erreur
    >>> return error_response(
    ...     error="not_found",
    ...     message="Manga not found",
    ...     status_code=404,
    ... )
    >>>
    >>> # Réponse paginée
    >>> return paginated_response(
    ...     items=mangas,
    ...     total=100,
    ...     page=1,
    ...     page_size=20,
    ... )

Intégration :
    - fastapi                      : Framework web
    - pydantic                     : Validation des données
    - interfaces/web/backend/routers/* : Utilise ces schémas
    - core/constants.py            : Métadonnées de l'application
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nexusdl.core.constants import APP_AUTHOR, APP_NAME, APP_URL, APP_VERSION


# ============================================================================
# CONSTANTES
# ============================================================================


# Codes d'erreur standards
ERROR_NOT_FOUND: str = "not_found"
ERROR_BAD_REQUEST: str = "bad_request"
ERROR_UNAUTHORIZED: str = "unauthorized"
ERROR_FORBIDDEN: str = "forbidden"
ERROR_CONFLICT: str = "conflict"
ERROR_VALIDATION: str = "validation_error"
ERROR_INTERNAL: str = "internal_error"
ERROR_SERVICE_UNAVAILABLE: str = "service_unavailable"
ERROR_RATE_LIMITED: str = "rate_limited"
ERROR_TIMEOUT: str = "timeout"

# Messages par défaut
DEFAULT_SUCCESS_MESSAGE: str = "Operation completed successfully"
DEFAULT_ERROR_MESSAGE: str = "An error occurred"
DEFAULT_NOT_FOUND_MESSAGE: str = "Resource not found"
DEFAULT_UNAUTHORIZED_MESSAGE: str = "Authentication required"
DEFAULT_FORBIDDEN_MESSAGE: str = "Access denied"
DEFAULT_RATE_LIMITED_MESSAGE: str = "Too many requests"

# Types génériques
T = TypeVar("T")


# ============================================================================
# ENUMS
# ============================================================================


class ResponseStatus(str, Enum):
    """Statut d'une réponse API.

    Attributes:
        SUCCESS: Opération réussie.
        ERROR: Erreur lors de l'opération.
        PARTIAL: Opération partiellement réussie.
    """

    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"


class ErrorCode(str, Enum):
    """Codes d'erreur standards.

    Attributes:
        NOT_FOUND: Ressource non trouvée.
        BAD_REQUEST: Requête invalide.
        UNAUTHORIZED: Non authentifié.
        FORBIDDEN: Accès refusé.
        CONFLICT: Conflit (ex: ressource existe déjà).
        VALIDATION_ERROR: Erreur de validation.
        INTERNAL_ERROR: Erreur interne.
        SERVICE_UNAVAILABLE: Service indisponible.
        RATE_LIMITED: Limite de débit dépassée.
        TIMEOUT: Timeout.
    """

    NOT_FOUND = "not_found"
    BAD_REQUEST = "bad_request"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    CONFLICT = "conflict"
    VALIDATION_ERROR = "validation_error"
    INTERNAL_ERROR = "internal_error"
    SERVICE_UNAVAILABLE = "service_unavailable"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"

    @property
    def http_status_code(self) -> int:
        """Code HTTP associé."""
        return {
            ErrorCode.NOT_FOUND: 404,
            ErrorCode.BAD_REQUEST: 400,
            ErrorCode.UNAUTHORIZED: 401,
            ErrorCode.FORBIDDEN: 403,
            ErrorCode.CONFLICT: 409,
            ErrorCode.VALIDATION_ERROR: 422,
            ErrorCode.INTERNAL_ERROR: 500,
            ErrorCode.SERVICE_UNAVAILABLE: 503,
            ErrorCode.RATE_LIMITED: 429,
            ErrorCode.TIMEOUT: 504,
        }[self]

    @property
    def default_message(self) -> str:
        """Message par défaut."""
        return {
            ErrorCode.NOT_FOUND: DEFAULT_NOT_FOUND_MESSAGE,
            ErrorCode.BAD_REQUEST: "Invalid request",
            ErrorCode.UNAUTHORIZED: DEFAULT_UNAUTHORIZED_MESSAGE,
            ErrorCode.FORBIDDEN: DEFAULT_FORBIDDEN_MESSAGE,
            ErrorCode.CONFLICT: "Resource conflict",
            ErrorCode.VALIDATION_ERROR: "Validation failed",
            ErrorCode.INTERNAL_ERROR: "Internal server error",
            ErrorCode.SERVICE_UNAVAILABLE: "Service unavailable",
            ErrorCode.RATE_LIMITED: DEFAULT_RATE_LIMITED_MESSAGE,
            ErrorCode.TIMEOUT: "Request timeout",
        }[self]


# ============================================================================
# MÉTADONNÉES — Informations additionnelles dans les réponses
# ============================================================================


class ResponseMeta(BaseModel):
    """Métadonnées d'une réponse API.

    Attributes:
        timestamp: Timestamp ISO 8601 de la réponse.
        request_id: ID unique de la requête (pour traçabilité).
        api_version: Version de l'API.
        processing_time_ms: Temps de traitement en millisecondes.
    """

    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="Timestamp ISO 8601.",
    )
    request_id: str | None = Field(default=None, description="ID de la requête.")
    api_version: str = Field(default=APP_VERSION, description="Version API.")
    processing_time_ms: float | None = Field(default=None, ge=0.0, description="Temps traitement (ms).")

    model_config = ConfigDict(frozen=True, extra="forbid")


class PaginationMeta(BaseModel):
    """Métadonnées de pagination.

    Attributes:
        page: Numéro de page actuelle (1-indexed).
        page_size: Taille de page.
        total_items: Nombre total d'éléments.
        total_pages: Nombre total de pages.
        has_next: S'il y a une page suivante.
        has_previous: S'il y a une page précédente.
        next_page: Numéro de la page suivante (si applicable).
        previous_page: Numéro de la page précédente (si applicable).
    """

    page: int = Field(..., ge=1, description="Page actuelle.")
    page_size: int = Field(..., ge=1, description="Taille de page.")
    total_items: int = Field(..., ge=0, description="Total éléments.")
    total_pages: int = Field(..., ge=0, description="Total pages.")
    has_next: bool = Field(default=False, description="Page suivante.")
    has_previous: bool = Field(default=False, description="Page précédente.")
    next_page: int | None = Field(default=None, ge=1, description="N° page suivante.")
    previous_page: int | None = Field(default=None, ge=1, description="N° page précédente.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("total_pages")
    @classmethod
    def validate_total_pages(cls, v: int, info: Any) -> int:
        """Valide le nombre total de pages."""
        if "page_size" in info.data and "total_items" in info.data:
            page_size = info.data["page_size"]
            total_items = info.data["total_items"]
            expected = (total_items + page_size - 1) // page_size if page_size > 0 else 0
            if v != expected:
                raise ValueError(f"total_pages doit être {expected}, reçu {v}")
        return v


class RateLimitMeta(BaseModel):
    """Métadonnées de rate limiting.

    Attributes:
        limit: Limite maximale de requêtes.
        remaining: Requêtes restantes.
        reset_at: Timestamp de réinitialisation.
        retry_after: Secondes à attendre (si limité).
    """

    limit: int = Field(..., ge=0, description="Limite maximale.")
    remaining: int = Field(..., ge=0, description="Requêtes restantes.")
    reset_at: str = Field(..., description="Timestamp reset.")
    retry_after: float | None = Field(default=None, ge=0.0, description="Secondes à attendre.")

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# RÉPONSES CANONIQUES — Modèles de base
# ============================================================================


class APIResponse(BaseModel, Generic[T]):
    """Réponse API générique.

    Enveloppe standard pour toutes les réponses de l'API.

    Attributes:
        status: Statut de la réponse (success, error, partial).
        data: Données de la réponse (type générique T).
        message: Message human-readable.
        meta: Métadonnées additionnelles.

    Example:
        {
            "status": "success",
            "data": {"manga_id": "123", "title": "One Piece"},
            "message": "Manga retrieved successfully",
            "meta": {
                "timestamp": "2026-09-24T14:30:45Z",
                "request_id": "abc123",
                "api_version": "0.1.0"
            }
        }
    """

    status: ResponseStatus = Field(default=ResponseStatus.SUCCESS, description="Statut.")
    data: T | None = Field(default=None, description="Données.")
    message: str = Field(default=DEFAULT_SUCCESS_MESSAGE, description="Message.")
    meta: ResponseMeta | None = Field(default=None, description="Métadonnées.")

    model_config = ConfigDict(extra="forbid")


class APIErrorResponse(BaseModel):
    """Réponse d'erreur standardisée.

    Format canonique pour toutes les erreurs de l'API.

    Attributes:
        status: Toujours "error".
        error: Code d'erreur machine-readable.
        message: Message d'erreur human-readable.
        details: Détails additionnels (optionnel).
        meta: Métadonnées.

    Example:
        {
            "status": "error",
            "error": "not_found",
            "message": "Manga not found",
            "details": {"manga_id": "123"},
            "meta": {
                "timestamp": "2026-09-24T14:30:45Z",
                "request_id": "abc123"
            }
        }
    """

    status: ResponseStatus = Field(default=ResponseStatus.ERROR, description="Statut.")
    error: str = Field(..., description="Code erreur.")
    message: str = Field(..., description="Message erreur.")
    details: dict[str, Any] = Field(default_factory=dict, description="Détails.")
    meta: ResponseMeta | None = Field(default=None, description="Métadonnées.")

    model_config = ConfigDict(extra="forbid")


class APIPaginatedResponse(BaseModel, Generic[T]):
    """Réponse paginée standardisée.

    Attributes:
        status: Statut de la réponse.
        data: Liste des éléments.
        pagination: Métadonnées de pagination.
        message: Message.
        meta: Métadonnées générales.

    Example:
        {
            "status": "success",
            "data": [...],
            "pagination": {
                "page": 1,
                "page_size": 20,
                "total_items": 100,
                "total_pages": 5,
                "has_next": true,
                "has_previous": false
            },
            "message": "Results retrieved successfully"
        }
    """

    status: ResponseStatus = Field(default=ResponseStatus.SUCCESS, description="Statut.")
    data: list[T] = Field(default_factory=list, description="Éléments.")
    pagination: PaginationMeta = Field(..., description="Pagination.")
    message: str = Field(default=DEFAULT_SUCCESS_MESSAGE, description="Message.")
    meta: ResponseMeta | None = Field(default=None, description="Métadonnées.")

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# WRAPPERS GÉNÉRIQUES — Pour faciliter la construction
# ============================================================================


class SingleResponse(BaseModel, Generic[T]):
    """Réponse avec un seul objet.

    Wrapper simplifié pour les réponses contenant un seul élément.

    Attributes:
        item: L'objet retourné.
        message: Message optionnel.

    Example:
        {
            "item": {"id": "123", "name": "MangaDex"},
            "message": "Site retrieved"
        }
    """

    item: T = Field(..., description="Objet.")
    message: str = Field(default=DEFAULT_SUCCESS_MESSAGE, description="Message.")

    model_config = ConfigDict(extra="forbid")


class ListResponse(BaseModel, Generic[T]):
    """Réponse avec une liste d'objets.

    Wrapper simplifié pour les réponses contenant une liste.

    Attributes:
        items: Liste des objets.
        total: Nombre total d'éléments.
        message: Message optionnel.

    Example:
        {
            "items": [...],
            "total": 10,
            "message": "10 sites found"
        }
    """

    items: list[T] = Field(default_factory=list, description="Éléments.")
    total: int = Field(default=0, ge=0, description="Total.")
    message: str = Field(default=DEFAULT_SUCCESS_MESSAGE, description="Message.")

    model_config = ConfigDict(extra="forbid")


class ActionResponse(BaseModel):
    """Réponse d'action (succès/échec).

    Utilisée pour les opérations qui ne retournent pas de données
    (ex: suppression, mise à jour).

    Attributes:
        success: Si l'action a réussi.
        message: Message de confirmation.
        details: Détails additionnels (optionnel).

    Example:
        {
            "success": true,
            "message": "Manga deleted successfully",
            "details": {"manga_id": "123"}
        }
    """

    success: bool = Field(..., description="Succès.")
    message: str = Field(..., description="Message.")
    details: dict[str, Any] = Field(default_factory=dict, description="Détails.")

    model_config = ConfigDict(extra="forbid")


class BulkActionResponse(BaseModel):
    """Réponse d'action en masse.

    Attributes:
        success: Si l'action a réussi.
        message: Message de confirmation.
        affected_count: Nombre d'éléments affectés.
        failed_count: Nombre d'éléments en échec.
        details: Détails additionnels.

    Example:
        {
            "success": true,
            "message": "5 tasks paused",
            "affected_count": 5,
            "failed_count": 0
        }
    """

    success: bool = Field(..., description="Succès.")
    message: str = Field(..., description="Message.")
    affected_count: int = Field(default=0, ge=0, description="Éléments affectés.")
    failed_count: int = Field(default=0, ge=0, description="Éléments en échec.")
    details: dict[str, Any] = Field(default_factory=dict, description="Détails.")

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# RÉPONSES SPÉCIFIQUES — Pour des cas d'usage particuliers
# ============================================================================


class HealthStatusResponse(BaseModel):
    """Réponse de vérification de santé.

    Attributes:
        status: Statut global (healthy, degraded, unhealthy).
        timestamp: Timestamp de la vérification.
        version: Version de l'API.
        uptime_seconds: Durée de fonctionnement.
    """

    status: str = Field(..., description="Statut (healthy/degraded/unhealthy).")
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(), description="Timestamp.")
    version: str = Field(default=APP_VERSION, description="Version.")
    uptime_seconds: float = Field(default=0.0, ge=0.0, description="Uptime.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class DetailedHealthResponse(BaseModel):
    """Réponse de santé détaillée.

    Attributes:
        status: Statut global.
        timestamp: Timestamp.
        components: Statuts des composants.
        version: Version.
        uptime_seconds: Uptime.
        python_version: Version Python.
        platform: Plateforme.
    """

    status: str = Field(..., description="Statut global.")
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(), description="Timestamp.")
    components: dict[str, Any] = Field(default_factory=dict, description="Composants.")
    version: str = Field(default=APP_VERSION, description="Version.")
    uptime_seconds: float = Field(default=0.0, ge=0.0, description="Uptime.")
    python_version: str = Field(default="", description="Version Python.")
    platform: str = Field(default="", description="Plateforme.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class VersionInfoResponse(BaseModel):
    """Réponse avec les informations de version.

    Attributes:
        app_name: Nom de l'application.
        version: Version.
        author: Auteur.
        url: URL du site.
        python_version: Version Python.
        platform: Plateforme.
    """

    app_name: str = Field(default=APP_NAME, description="Nom application.")
    version: str = Field(default=APP_VERSION, description="Version.")
    author: str = Field(default=APP_AUTHOR, description="Auteur.")
    url: str = Field(default=APP_URL, description="URL.")
    python_version: str = Field(default="", description="Version Python.")
    platform: str = Field(default="", description="Plateforme.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class MetricsResponse(BaseModel):
    """Réponse avec les métriques système.

    Attributes:
        uptime_seconds: Durée de fonctionnement.
        memory_usage_mb: Utilisation mémoire (MB).
        cpu_percent: Utilisation CPU (%).
        active_connections: Connexions actives.
        total_requests: Total requêtes.
    """

    uptime_seconds: float = Field(default=0.0, ge=0.0, description="Uptime.")
    memory_usage_mb: float = Field(default=0.0, ge=0.0, description="Mémoire (MB).")
    cpu_percent: float = Field(default=0.0, ge=0.0, le=100.0, description="CPU (%).")
    active_connections: int = Field(default=0, ge=0, description="Connexions.")
    total_requests: int = Field(default=0, ge=0, description="Total requêtes.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class AuthTokensResponse(BaseModel):
    """Réponse avec les tokens d'authentification.

    Attributes:
        access_token: Token d'accès JWT.
        refresh_token: Token de refresh JWT.
        token_type: Type de token (bearer).
        expires_in: Durée de vie en secondes.
        user_id: ID de l'utilisateur.
    """

    access_token: str = Field(..., description="Token d'accès.")
    refresh_token: str = Field(..., description="Token de refresh.")
    token_type: str = Field(default="bearer", description="Type.")
    expires_in: int = Field(..., ge=0, description="Durée vie (s).")
    user_id: str = Field(..., description="ID utilisateur.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class UserProfileResponse(BaseModel):
    """Réponse avec le profil utilisateur.

    Attributes:
        user_id: ID unique.
        username: Nom d'utilisateur.
        email: Email.
        roles: Rôles.
        permissions: Permissions.
        is_active: Si actif.
        created_at: Date de création.
    """

    user_id: str = Field(..., description="ID unique.")
    username: str = Field(..., description="Nom d'utilisateur.")
    email: str = Field(default="", description="Email.")
    roles: list[str] = Field(default_factory=list, description="Rôles.")
    permissions: list[str] = Field(default_factory=list, description="Permissions.")
    is_active: bool = Field(default=True, description="Actif.")
    created_at: str = Field(default="", description="Date création.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ValidationErrorDetail(BaseModel):
    """Détail d'une erreur de validation.

    Attributes:
        field: Champ concerné.
        message: Message d'erreur.
        value: Valeur invalide (optionnel).
    """

    field: str = Field(..., description="Champ.")
    message: str = Field(..., description="Message.")
    value: Any = Field(default=None, description="Valeur.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ValidationErrorResponse(BaseModel):
    """Réponse d'erreur de validation.

    Attributes:
        status: Toujours "error".
        error: Code "validation_error".
        message: Message global.
        errors: Liste des erreurs de validation.
        meta: Métadonnées.
    """

    status: ResponseStatus = Field(default=ResponseStatus.ERROR, description="Statut.")
    error: str = Field(default=ErrorCode.VALIDATION_ERROR.value, description="Code.")
    message: str = Field(default="Validation failed", description="Message.")
    errors: list[ValidationErrorDetail] = Field(default_factory=list, description="Erreurs.")
    meta: ResponseMeta | None = Field(default=None, description="Métadonnées.")

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# HELPERS — Fonctions pour construire les réponses
# ============================================================================


def success_response(
    data: Any = None,
    message: str = DEFAULT_SUCCESS_MESSAGE,
    *,
    request_id: str | None = None,
    processing_time_ms: float | None = None,
) -> dict[str, Any]:
    """Construit une réponse de succès.

    Args:
        data: Données à retourner.
        message: Message de confirmation.
        request_id: ID de la requête.
        processing_time_ms: Temps de traitement.

    Returns:
        Dictionnaire sérialisable.

    Example:
        >>> success_response(data={"id": "123"}, message="Created")
        {
            "status": "success",
            "data": {"id": "123"},
            "message": "Created",
            "meta": {...}
        }
    """
    meta = ResponseMeta(
        request_id=request_id,
        processing_time_ms=processing_time_ms,
    )

    response = APIResponse(
        status=ResponseStatus.SUCCESS,
        data=data,
        message=message,
        meta=meta,
    )

    return response.model_dump(mode="json", exclude_none=True)


def error_response(
    error: str | ErrorCode,
    message: str | None = None,
    *,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
    processing_time_ms: float | None = None,
) -> dict[str, Any]:
    """Construit une réponse d'erreur.

    Args:
        error: Code d'erreur (string ou ErrorCode).
        message: Message d'erreur (défaut selon le code).
        details: Détails additionnels.
        request_id: ID de la requête.
        processing_time_ms: Temps de traitement.

    Returns:
        Dictionnaire sérialisable.

    Example:
        >>> error_response(ErrorCode.NOT_FOUND, "Manga not found", details={"id": "123"})
        {
            "status": "error",
            "error": "not_found",
            "message": "Manga not found",
            "details": {"id": "123"},
            "meta": {...}
        }
    """
    if isinstance(error, ErrorCode):
        error_code = error.value
        default_message = error.default_message
    else:
        error_code = error
        default_message = DEFAULT_ERROR_MESSAGE

    meta = ResponseMeta(
        request_id=request_id,
        processing_time_ms=processing_time_ms,
    )

    response = APIErrorResponse(
        status=ResponseStatus.ERROR,
        error=error_code,
        message=message or default_message,
        details=details or {},
        meta=meta,
    )

    return response.model_dump(mode="json", exclude_none=True)


def paginated_response(
    items: list[Any],
    total: int,
    page: int,
    page_size: int,
    *,
    message: str = DEFAULT_SUCCESS_MESSAGE,
    request_id: str | None = None,
    processing_time_ms: float | None = None,
) -> dict[str, Any]:
    """Construit une réponse paginée.

    Args:
        items: Éléments de la page.
        total: Nombre total d'éléments.
        page: Numéro de page actuelle.
        page_size: Taille de page.
        message: Message.
        request_id: ID de la requête.
        processing_time_ms: Temps de traitement.

    Returns:
        Dictionnaire sérialisable.

    Example:
        >>> paginated_response(items=[...], total=100, page=1, page_size=20)
        {
            "status": "success",
            "data": [...],
            "pagination": {
                "page": 1,
                "page_size": 20,
                "total_items": 100,
                "total_pages": 5,
                "has_next": true,
                "has_previous": false
            },
            "message": "..."
        }
    """
    total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0
    has_next = page < total_pages
    has_previous = page > 1

    pagination = PaginationMeta(
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=total_pages,
        has_next=has_next,
        has_previous=has_previous,
        next_page=page + 1 if has_next else None,
        previous_page=page - 1 if has_previous else None,
    )

    meta = ResponseMeta(
        request_id=request_id,
        processing_time_ms=processing_time_ms,
    )

    response = APIPaginatedResponse(
        status=ResponseStatus.SUCCESS,
        data=items,
        pagination=pagination,
        message=message,
        meta=meta,
    )

    return response.model_dump(mode="json", exclude_none=True)


def list_response(
    items: list[Any],
    total: int | None = None,
    *,
    message: str = DEFAULT_SUCCESS_MESSAGE,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Construit une réponse de liste (non paginée).

    Args:
        items: Éléments.
        total: Nombre total (défaut: len(items)).
        message: Message.
        request_id: ID de la requête.

    Returns:
        Dictionnaire sérialisable.
    """
    if total is None:
        total = len(items)

    meta = ResponseMeta(request_id=request_id)

    response = APIResponse(
        status=ResponseStatus.SUCCESS,
        data={"items": items, "total": total},
        message=message,
        meta=meta,
    )

    return response.model_dump(mode="json", exclude_none=True)


def action_response(
    success: bool,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Construit une réponse d'action.

    Args:
        success: Si l'action a réussi.
        message: Message.
        details: Détails.
        request_id: ID de la requête.

    Returns:
        Dictionnaire sérialisable.
    """
    meta = ResponseMeta(request_id=request_id)

    response = APIResponse(
        status=ResponseStatus.SUCCESS if success else ResponseStatus.ERROR,
        data=ActionResponse(
            success=success,
            message=message,
            details=details or {},
        ).model_dump(),
        message=message,
        meta=meta,
    )

    return response.model_dump(mode="json", exclude_none=True)


def validation_error_response(
    errors: list[dict[str, Any]],
    *,
    message: str = "Validation failed",
    request_id: str | None = None,
) -> dict[str, Any]:
    """Construit une réponse d'erreur de validation.

    Args:
        errors: Liste d'erreurs (champs avec messages).
        message: Message global.
        request_id: ID de la requête.

    Returns:
        Dictionnaire sérialisable.
    """
    error_details = [
        ValidationErrorDetail(
            field=e.get("field", "unknown"),
            message=e.get("message", "Invalid value"),
            value=e.get("value"),
        )
        for e in errors
    ]

    meta = ResponseMeta(request_id=request_id)

    response = ValidationErrorResponse(
        status=ResponseStatus.ERROR,
        error=ErrorCode.VALIDATION_ERROR.value,
        message=message,
        errors=error_details,
        meta=meta,
    )

    return response.model_dump(mode="json", exclude_none=True)


def http_status_from_error_code(error_code: str | ErrorCode) -> int:
    """Retourne le code HTTP associé à un code d'erreur.

    Args:
        error_code: Code d'erreur (string ou ErrorCode).

    Returns:
        Code HTTP.
    """
    if isinstance(error_code, ErrorCode):
        return error_code.http_status_code

    # Mapping manuel pour les strings
    mapping = {
        "not_found": 404,
        "bad_request": 400,
        "unauthorized": 401,
        "forbidden": 403,
        "conflict": 409,
        "validation_error": 422,
        "internal_error": 500,
        "service_unavailable": 503,
        "rate_limited": 429,
        "timeout": 504,
    }

    return mapping.get(error_code, 500)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
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
    # Enums
    "ResponseStatus",
    "ErrorCode",
    # Métadonnées
    "ResponseMeta",
    "PaginationMeta",
    "RateLimitMeta",
    # Réponses canoniques
    "APIResponse",
    "APIErrorResponse",
    "APIPaginatedResponse",
    # Wrappers génériques
    "SingleResponse",
    "ListResponse",
    "ActionResponse",
    "BulkActionResponse",
    # Réponses spécifiques
    "HealthStatusResponse",
    "DetailedHealthResponse",
    "VersionInfoResponse",
    "MetricsResponse",
    "AuthTokensResponse",
    "UserProfileResponse",
    "ValidationErrorDetail",
    "ValidationErrorResponse",
    # Helpers
    "success_response",
    "error_response",
    "paginated_response",
    "list_response",
    "action_response",
    "validation_error_response",
    "http_status_from_error_code",
]
