"""Dépendances FastAPI réutilisables pour l'API REST NexusDL.

Ce module fournit toutes les dépendances FastAPI (via `Depends`) utilisées
par les routeurs pour extraire l'utilisateur courant, vérifier les permissions,
récupérer les services, valider les paramètres, et gérer la pagination.

**Catégories de dépendances** :
    1. Sécurité & Authentification
       - get_current_user()        : Extrait l'utilisateur authentifié
       - get_optional_user()       : Utilisateur optionnel (anonyme OK)
       - require_role()            : Exige un rôle spécifique
       - require_permission()      : Exige une permission spécifique
       - require_any_role()        : Exige au moins un rôle parmi une liste
       - require_all_permissions() : Exige toutes les permissions

    2. Pagination & Tri
       - get_pagination()          : Extrait page/page_size
       - get_sorting()             : Extrait sort_by/sort_order
       - PaginationParams          : Modèle de paramètres paginés

    3. Services (injection)
       - get_site_registry()       : Accès au registre des sites
       - get_download_manager()    : Accès au gestionnaire de téléchargements
       - get_library_manager()     : Accès au gestionnaire de bibliothèque
       - get_event_bus()           : Accès à l'EventBus
       - get_config()              : Accès à la configuration

    4. Métadonnées de requête
       - get_request_metadata()    : IP, user-agent, request_id
       - get_rate_limit_info()     : Infos rate limit depuis headers
       - get_client_info()         : Informations client complètes

    5. Validation
       - validate_path_id()        : Valide un ID de ressource
       - validate_query_string()   : Valide une chaîne de requête
       - validate_language()       : Valide un code langue
       - validate_datetime_param() : Valide un paramètre datetime

**Exemple d'utilisation** :
    >>> from fastapi import APIRouter, Depends
    >>> from nexusdl.interfaces.web.backend.dependencies import (
    ...     get_current_user, require_role, get_pagination,
    ...     get_site_registry, UserIdentity, PaginationParams,
    ... )
    >>>
    >>> router = APIRouter()
    >>>
    >>> @router.get("/mangas")
    >>> async def list_mangas(
    ...     user: UserIdentity = Depends(get_current_user),
    ...     pagination: PaginationParams = Depends(get_pagination),
    ...     registry = Depends(get_site_registry),
    ... ):
    ...     mangas = await registry.search("test")
    ...     return {
    ...         "items": mangas[pagination.offset:pagination.offset + pagination.limit],
    ...         "total": len(mangas),
    ...         "page": pagination.page,
    ...     }
    >>>
    >>> @router.delete("/mangas/{manga_id}")
    >>> async def delete_manga(
    ...     manga_id: str,
    ...     user: UserIdentity = Depends(require_role("admin")),
    ... ):
    ...     # ...

Intégration :
    - fastapi                                : Framework web
    - interfaces/web/backend/middleware/auth : UserIdentity, AuthMiddleware
    - interfaces/web/backend/routers/*       : Utilise ces dépendances
    - core/registry/                         : SiteRegistry
    - core/downloader/                       : DownloadManager
    - core/library/                          : LibraryManager
    - core/events.py                         : EventBus
    - core/config.py                         : Configuration
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Callable, Final, Iterator

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from fastapi import Depends, HTTPException, Query, Request, status
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from nexusdl.core.constants import APP_NAME
from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# CONSTANTES
# ============================================================================


# Pagination
DEFAULT_PAGE: Final[int] = 1
DEFAULT_PAGE_SIZE: Final[int] = 20
MIN_PAGE_SIZE: Final[int] = 1
MAX_PAGE_SIZE: Final[int] = 200
MAX_OFFSET: Final[int] = 10000

# Tri
DEFAULT_SORT_BY: Final[str] = "created_at"
DEFAULT_SORT_ORDER: Final[str] = "desc"
SORT_ORDERS: Final[frozenset[str]] = frozenset({"asc", "desc"})

# Validation
MIN_ID_LENGTH: Final[int] = 1
MAX_ID_LENGTH: Final[int] = 200
MIN_QUERY_LENGTH: Final[int] = 1
MAX_QUERY_LENGTH: Final[int] = 500
ID_PATTERN: Final[str] = r"^[a-zA-Z0-9_\-\.]+$"

# Headers
HEADER_REQUEST_ID: Final[str] = "X-Request-ID"
HEADER_CORRELATION_ID: Final[str] = "X-Correlation-ID"
HEADER_RATE_LIMIT: Final[str] = "X-RateLimit-Limit"
HEADER_RATE_REMAINING: Final[str] = "X-RateLimit-Remaining"
HEADER_RATE_RESET: Final[str] = "X-RateLimit-Reset"


# ============================================================================
# EXCEPTIONS
# ============================================================================


class DependencyError(NexusDLError):
    """Exception de base pour les erreurs de dépendance."""


class ServiceUnavailableError(DependencyError):
    """Exception levée lorsqu'un service requis n'est pas disponible.

    Attributes:
        service_name: Nom du service.
        reason: Raison de l'indisponibilité.
    """

    def __init__(self, service_name: str, reason: str = "") -> None:
        msg = f"Service indisponible: {service_name}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.service_name = service_name
        self.reason = reason


class InvalidParameterError(DependencyError):
    """Exception levée lorsqu'un paramètre est invalide.

    Attributes:
        parameter: Nom du paramètre.
        value: Valeur invalide.
        reason: Raison de l'invalidité.
    """

    def __init__(self, parameter: str, value: Any, reason: str = "") -> None:
        msg = f"Paramètre invalide: {parameter}={value!r}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.parameter = parameter
        self.value = value
        self.reason = reason


# ============================================================================
# MODÈLES DE DONNÉES
# ============================================================================


class PaginationParams(BaseModel):
    """Paramètres de pagination.

    Attributes:
        page: Numéro de page (1-indexed).
        page_size: Taille de page.
        offset: Offset calculé (page - 1) * page_size.

    Example:
        >>> params = PaginationParams(page=2, page_size=20)
        >>> params.offset
        20
    """

    page: int = Field(default=DEFAULT_PAGE, ge=1, description="Numéro de page.")
    page_size: int = Field(
        default=DEFAULT_PAGE_SIZE,
        ge=MIN_PAGE_SIZE,
        le=MAX_PAGE_SIZE,
        description="Taille de page.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def offset(self) -> int:
        """Calcule l'offset pour les requêtes."""
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        """Alias pour page_size."""
        return self.page_size

    def slice(self, items: list[Any]) -> list[Any]:
        """Applique la pagination à une liste.

        Args:
            items: Liste à paginer.

        Returns:
            Sous-liste paginée.
        """
        return items[self.offset:self.offset + self.page_size]


class SortingParams(BaseModel):
    """Paramètres de tri.

    Attributes:
        sort_by: Champ de tri.
        sort_order: Ordre de tri (asc/desc).

    Example:
        >>> params = SortingParams(sort_by="title", sort_order="asc")
    """

    sort_by: str = Field(default=DEFAULT_SORT_BY, description="Champ de tri.")
    sort_order: str = Field(default=DEFAULT_SORT_ORDER, description="Ordre de tri.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("sort_order")
    @classmethod
    def validate_sort_order(cls, v: str) -> str:
        """Valide l'ordre de tri."""
        v = v.lower()
        if v not in SORT_ORDERS:
            raise ValueError(f"Ordre de tri invalide: {v}. Doit être 'asc' ou 'desc'")
        return v

    @property
    def is_ascending(self) -> bool:
        """Indique si le tri est ascendant."""
        return self.sort_order == "asc"

    @property
    def is_descending(self) -> bool:
        """Indique si le tri est descendant."""
        return self.sort_order == "desc"


class RequestMetadata(BaseModel):
    """Métadonnées d'une requête HTTP.

    Attributes:
        request_id: ID unique de la requête.
        correlation_id: ID de corrélation (traçabilité distribuée).
        client_ip: Adresse IP du client.
        user_agent: User-Agent du client.
        method: Méthode HTTP.
        path: Chemin de la requête.
        timestamp: Timestamp de la requête.
    """

    request_id: str = Field(default="", description="ID de la requête.")
    correlation_id: str | None = Field(default=None, description="ID de corrélation.")
    client_ip: str = Field(default="", description="IP du client.")
    user_agent: str = Field(default="", description="User-Agent.")
    method: str = Field(default="", description="Méthode HTTP.")
    path: str = Field(default="", description="Chemin.")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Timestamp.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class RateLimitInfo(BaseModel):
    """Informations de rate limiting.

    Attributes:
        limit: Limite maximale.
        remaining: Requêtes restantes.
        reset_at: Timestamp de réinitialisation.
        is_limited: Si le client est actuellement limité.
    """

    limit: int = Field(default=0, ge=0, description="Limite maximale.")
    remaining: int = Field(default=0, ge=0, description="Requêtes restantes.")
    reset_at: datetime | None = Field(default=None, description="Timestamp reset.")
    is_limited: bool = Field(default=False, description="Client limité.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def usage_percentage(self) -> float:
        """Pourcentage d'utilisation de la limite."""
        if self.limit == 0:
            return 0.0
        used = self.limit - self.remaining
        return (used / self.limit) * 100.0


class UserIdentity(BaseModel):
    """Identité d'un utilisateur authentifié.

    Modèle simplifié pour usage dans les dépendances.
    Pour le modèle complet, voir middleware/auth.py.

    Attributes:
        user_id: ID unique de l'utilisateur.
        username: Nom d'utilisateur.
        roles: Rôles de l'utilisateur.
        permissions: Permissions de l'utilisateur.
        is_authenticated: Si l'utilisateur est authentifié.
    """

    user_id: str = Field(..., description="ID unique.")
    username: str = Field(default="", description="Nom d'utilisateur.")
    roles: list[str] = Field(default_factory=list, description="Rôles.")
    permissions: list[str] = Field(default_factory=list, description="Permissions.")
    is_authenticated: bool = Field(default=True, description="Authentifié.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    def has_role(self, role: str) -> bool:
        """Vérifie si l'utilisateur a un rôle.

        Args:
            role: Rôle à vérifier.

        Returns:
            True si l'utilisateur a le rôle.
        """
        return role in self.roles

    def has_permission(self, permission: str) -> bool:
        """Vérifie si l'utilisateur a une permission.

        Args:
            permission: Permission à vérifier.

        Returns:
            True si l'utilisateur a la permission.
        """
        return permission in self.permissions

    def has_any_role(self, roles: list[str]) -> bool:
        """Vérifie si l'utilisateur a au moins un rôle.

        Args:
            roles: Liste de rôles.

        Returns:
            True si l'utilisateur a au moins un rôle.
        """
        return any(role in self.roles for role in roles)

    def has_all_permissions(self, permissions: list[str]) -> bool:
        """Vérifie si l'utilisateur a toutes les permissions.

        Args:
            permissions: Liste de permissions.

        Returns:
            True si l'utilisateur a toutes les permissions.
        """
        return all(perm in self.permissions for perm in permissions)


# ============================================================================
# DÉPENDANCES — Sécurité & Authentification
# ============================================================================


if FASTAPI_AVAILABLE:

    async def get_current_user(request: Request) -> UserIdentity:
        """Dépendance pour extraire l'utilisateur courant authentifié.

        Récupère l'utilisateur depuis request.state.user (injecté par AuthMiddleware).

        Args:
            request: Requête HTTP.

        Returns:
            Instance de UserIdentity.

        Raises:
            HTTPException: 401 si non authentifié.

        Example:
            >>> @router.get("/profile")
            >>> async def get_profile(user: UserIdentity = Depends(get_current_user)):
            ...     return {"user_id": user.user_id}
        """
        if not hasattr(request.state, "user") or request.state.user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "unauthorized",
                    "message": "Authentication required",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        auth_user = request.state.user

        # Convertir en UserIdentity si nécessaire
        if isinstance(auth_user, UserIdentity):
            return auth_user

        # Adapter depuis le modèle du middleware
        return UserIdentity(
            user_id=getattr(auth_user, "user_id", "unknown"),
            username=getattr(auth_user, "username", ""),
            roles=getattr(auth_user, "roles", []),
            permissions=getattr(auth_user, "permissions", []),
            is_authenticated=True,
        )

    async def get_optional_user(request: Request) -> UserIdentity | None:
        """Dépendance pour extraire l'utilisateur courant si authentifié.

        Retourne None si l'utilisateur n'est pas authentifié (anonyme autorisé).

        Args:
            request: Requête HTTP.

        Returns:
            Instance de UserIdentity ou None.

        Example:
            >>> @router.get("/public")
            >>> async def public_endpoint(user: UserIdentity | None = Depends(get_optional_user)):
            ...     if user:
            ...         return {"message": f"Hello {user.username}"}
            ...     return {"message": "Hello anonymous"}
        """
        if not hasattr(request.state, "user") or request.state.user is None:
            return None

        auth_user = request.state.user

        if isinstance(auth_user, UserIdentity):
            return auth_user

        return UserIdentity(
            user_id=getattr(auth_user, "user_id", "anonymous"),
            username=getattr(auth_user, "username", "anonymous"),
            roles=getattr(auth_user, "roles", []),
            permissions=getattr(auth_user, "permissions", []),
            is_authenticated=True,
        )

    def require_role(role: str) -> Callable:
        """Dépendance pour exiger un rôle spécifique.

        Args:
            role: Rôle requis.

        Returns:
            Fonction de dépendance.

        Raises:
            HTTPException: 403 si l'utilisateur n'a pas le rôle.

        Example:
            >>> @router.delete("/users/{user_id}")
            >>> async def delete_user(
            ...     user_id: str,
            ...     admin: UserIdentity = Depends(require_role("admin")),
            ... ):
            ...     # Seul un admin peut exécuter cette action
            ...     pass
        """
        async def dependency(user: UserIdentity = Depends(get_current_user)) -> UserIdentity:
            if not user.has_role(role):
                logger.warning(
                    "Permission refusée: user={} n'a pas le rôle {}",
                    user.user_id,
                    role,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "forbidden",
                        "message": f"Role '{role}' required",
                        "required_role": role,
                        "user_roles": user.roles,
                    },
                )
            return user

        return dependency

    def require_permission(permission: str) -> Callable:
        """Dépendance pour exiger une permission spécifique.

        Args:
            permission: Permission requise.

        Returns:
            Fonction de dépendance.

        Raises:
            HTTPException: 403 si l'utilisateur n'a pas la permission.

        Example:
            >>> @router.post("/mangas")
            >>> async def create_manga(
            ...     user: UserIdentity = Depends(require_permission("write")),
            ... ):
            ...     # Seuls les utilisateurs avec permission 'write'
            ...     pass
        """
        async def dependency(user: UserIdentity = Depends(get_current_user)) -> UserIdentity:
            if not user.has_permission(permission):
                logger.warning(
                    "Permission refusée: user={} n'a pas la permission {}",
                    user.user_id,
                    permission,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "forbidden",
                        "message": f"Permission '{permission}' required",
                        "required_permission": permission,
                        "user_permissions": user.permissions,
                    },
                )
            return user

        return dependency

    def require_any_role(roles: list[str]) -> Callable:
        """Dépendance pour exiger au moins un rôle parmi une liste.

        Args:
            roles: Liste de rôles acceptés.

        Returns:
            Fonction de dépendance.

        Raises:
            HTTPException: 403 si l'utilisateur n'a aucun des rôles.

        Example:
            >>> @router.get("/moderation")
            >>> async def moderation_panel(
            ...     user: UserIdentity = Depends(require_any_role(["moderator", "admin"])),
            ... ):
            ...     # Accessible aux modérateurs ET admins
            ...     pass
        """
        async def dependency(user: UserIdentity = Depends(get_current_user)) -> UserIdentity:
            if not user.has_any_role(roles):
                logger.warning(
                    "Permission refusée: user={} n'a aucun des rôles {}",
                    user.user_id,
                    roles,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "forbidden",
                        "message": f"One of these roles required: {', '.join(roles)}",
                        "required_roles": roles,
                        "user_roles": user.roles,
                    },
                )
            return user

        return dependency

    def require_all_permissions(permissions: list[str]) -> Callable:
        """Dépendance pour exiger toutes les permissions d'une liste.

        Args:
            permissions: Liste de permissions requises.

        Returns:
            Fonction de dépendance.

        Raises:
            HTTPException: 403 si l'utilisateur n'a pas toutes les permissions.

        Example:
            >>> @router.post("/sensitive-operation")
            >>> async def sensitive_operation(
            ...     user: UserIdentity = Depends(require_all_permissions(["read", "write", "admin"])),
            ... ):
            ...     # Nécessite TOUTES les permissions listées
            ...     pass
        """
        async def dependency(user: UserIdentity = Depends(get_current_user)) -> UserIdentity:
            if not user.has_all_permissions(permissions):
                missing = [p for p in permissions if not user.has_permission(p)]
                logger.warning(
                    "Permission refusée: user={} manque les permissions {}",
                    user.user_id,
                    missing,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "forbidden",
                        "message": f"All these permissions required: {', '.join(permissions)}",
                        "required_permissions": permissions,
                        "missing_permissions": missing,
                        "user_permissions": user.permissions,
                    },
                )
            return user

        return dependency


# ============================================================================
# DÉPENDANCES — Pagination & Tri
# ============================================================================


if FASTAPI_AVAILABLE:

    def get_pagination(
        page: int = Query(DEFAULT_PAGE, ge=1, description="Numéro de page"),
        page_size: int = Query(
            DEFAULT_PAGE_SIZE,
            ge=MIN_PAGE_SIZE,
            le=MAX_PAGE_SIZE,
            description="Taille de page",
        ),
    ) -> PaginationParams:
        """Dépendance pour extraire les paramètres de pagination.

        Args:
            page: Numéro de page (1-indexed).
            page_size: Taille de page.

        Returns:
            Instance de PaginationParams.

        Example:
            >>> @router.get("/items")
            >>> async def list_items(pagination: PaginationParams = Depends(get_pagination)):
            ...     items = await get_all_items()
            ...     return {
            ...         "items": pagination.slice(items),
            ...         "total": len(items),
            ...         "page": pagination.page,
            ...     }
        """
        return PaginationParams(page=page, page_size=page_size)

    def get_sorting(
        sort_by: str = Query(DEFAULT_SORT_BY, description="Champ de tri"),
        sort_order: str = Query(DEFAULT_SORT_ORDER, description="Ordre de tri (asc/desc)"),
    ) -> SortingParams:
        """Dépendance pour extraire les paramètres de tri.

        Args:
            sort_by: Champ de tri.
            sort_order: Ordre de tri.

        Returns:
            Instance de SortingParams.

        Example:
            >>> @router.get("/items")
            >>> async def list_items(sorting: SortingParams = Depends(get_sorting)):
            ...     items = await get_all_items()
            ...     return sorted(
            ...         items,
            ...         key=lambda x: getattr(x, sorting.sort_by, ""),
            ...         reverse=sorting.is_descending,
            ...     )
        """
        return SortingParams(sort_by=sort_by, sort_order=sort_order)


# ============================================================================
# DÉPENDANCES — Services (injection)
# ============================================================================


if FASTAPI_AVAILABLE:

    async def get_site_registry() -> Any:
        """Dépendance pour obtenir le registre des sites.

        Returns:
            Instance de SiteRegistry.

        Raises:
            HTTPException: 503 si le registre n'est pas disponible.

        Example:
            >>> @router.get("/sites")
            >>> async def list_sites(registry = Depends(get_site_registry)):
            ...     sites = registry.list_sites()
            ...     return {"sites": sites}
        """
        try:
            from nexusdl.core.registry import get_site_registry as _get_registry
            registry = _get_registry()
            if registry is None:
                raise ServiceUnavailableError("site_registry", "Registry not initialized")
            return registry
        except Exception as e:
            logger.error("SiteRegistry indisponible: {}", e)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "service_unavailable",
                    "message": "Site registry is not available",
                    "service": "site_registry",
                },
            ) from e

    async def get_download_manager() -> Any:
        """Dépendance pour obtenir le gestionnaire de téléchargements.

        Returns:
            Instance de DownloadManager.

        Raises:
            HTTPException: 503 si le manager n'est pas disponible.
        """
        try:
            from nexusdl.core.downloader import get_download_manager as _get_manager
            manager = _get_manager()
            if manager is None:
                raise ServiceUnavailableError("download_manager", "Manager not initialized")
            return manager
        except Exception as e:
            logger.error("DownloadManager indisponible: {}", e)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "service_unavailable",
                    "message": "Download manager is not available",
                    "service": "download_manager",
                },
            ) from e

    async def get_library_manager() -> Any:
        """Dépendance pour obtenir le gestionnaire de bibliothèque.

        Returns:
            Instance de LibraryManager.

        Raises:
            HTTPException: 503 si le manager n'est pas disponible.
        """
        try:
            from nexusdl.core.library import get_library_manager as _get_manager
            manager = _get_manager()
            if manager is None:
                raise ServiceUnavailableError("library_manager", "Manager not initialized")
            return manager
        except Exception as e:
            logger.error("LibraryManager indisponible: {}", e)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "service_unavailable",
                    "message": "Library manager is not available",
                    "service": "library_manager",
                },
            ) from e

    async def get_event_bus() -> Any:
        """Dépendance pour obtenir l'EventBus.

        Returns:
            Instance de EventBus.

        Raises:
            HTTPException: 503 si l'EventBus n'est pas disponible.
        """
        try:
            from nexusdl.core.events import get_event_bus as _get_bus
            bus = _get_bus()
            if bus is None:
                raise ServiceUnavailableError("event_bus", "EventBus not initialized")
            return bus
        except Exception as e:
            logger.error("EventBus indisponible: {}", e)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "service_unavailable",
                    "message": "Event bus is not available",
                    "service": "event_bus",
                },
            ) from e

    async def get_config() -> Any:
        """Dépendance pour obtenir la configuration.

        Returns:
            Instance de NexusDLConfig.

        Raises:
            HTTPException: 503 si la configuration n'est pas disponible.
        """
        try:
            from nexusdl.core.config import get_config as _get_config
            config = _get_config()
            if config is None:
                raise ServiceUnavailableError("config", "Configuration not loaded")
            return config
        except Exception as e:
            logger.error("Configuration indisponible: {}", e)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "service_unavailable",
                    "message": "Configuration is not available",
                    "service": "config",
                },
            ) from e


# ============================================================================
# DÉPENDANCES — Métadonnées de requête
# ============================================================================


if FASTAPI_AVAILABLE:

    async def get_request_metadata(request: Request) -> RequestMetadata:
        """Dépendance pour extraire les métadonnées de la requête.

        Args:
            request: Requête HTTP.

        Returns:
            Instance de RequestMetadata.

        Example:
            >>> @router.post("/audit")
            >>> async def audit_log(metadata: RequestMetadata = Depends(get_request_metadata)):
            ...     logger.info("Request from {}: {} {}", metadata.client_ip, metadata.method, metadata.path)
        """
        # Extraire le request_id
        request_id = request.headers.get(HEADER_REQUEST_ID, "")
        if not request_id and hasattr(request.state, "request_id"):
            request_id = request.state.request_id

        # Extraire le correlation_id
        correlation_id = request.headers.get(HEADER_CORRELATION_ID)
        if not correlation_id and hasattr(request.state, "correlation_id"):
            correlation_id = request.state.correlation_id

        # Extraire l'IP client
        client_ip = ""
        if request.client:
            client_ip = request.client.host

        # Vérifier X-Forwarded-For
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()

        # Extraire le User-Agent
        user_agent = request.headers.get("User-Agent", "")

        return RequestMetadata(
            request_id=request_id,
            correlation_id=correlation_id,
            client_ip=client_ip,
            user_agent=user_agent,
            method=request.method,
            path=request.url.path,
        )

    async def get_rate_limit_info(request: Request) -> RateLimitInfo:
        """Dépendance pour extraire les informations de rate limiting.

        Récupère les headers de rate limiting ajoutés par RateLimitMiddleware.

        Args:
            request: Requête HTTP.

        Returns:
            Instance de RateLimitInfo.

        Example:
            >>> @router.get("/status")
            >>> async def rate_limit_status(rate_info: RateLimitInfo = Depends(get_rate_limit_info)):
            ...     return {
            ...         "remaining": rate_info.remaining,
            ...         "usage": rate_info.usage_percentage,
            ...     }
        """
        limit_str = request.headers.get(HEADER_RATE_LIMIT, "0")
        remaining_str = request.headers.get(HEADER_RATE_REMAINING, "0")
        reset_str = request.headers.get(HEADER_RATE_RESET, "")

        try:
            limit = int(limit_str)
            remaining = int(remaining_str)
            reset_at = datetime.fromtimestamp(int(reset_str), tz=UTC) if reset_str else None
            is_limited = remaining == 0 and limit > 0
        except (ValueError, TypeError):
            limit = 0
            remaining = 0
            reset_at = None
            is_limited = False

        return RateLimitInfo(
            limit=limit,
            remaining=remaining,
            reset_at=reset_at,
            is_limited=is_limited,
        )

    async def get_client_info(request: Request) -> dict[str, Any]:
        """Dépendance pour obtenir toutes les informations client.

        Combine les métadonnées de requête et les informations utilisateur.

        Args:
            request: Requête HTTP.

        Returns:
            Dictionnaire d'informations client.

        Example:
            >>> @router.get("/client-info")
            >>> async def client_info(info: dict = Depends(get_client_info)):
            ...     return info
        """
        metadata = await get_request_metadata(request)
        user = await get_optional_user(request)

        return {
            "request_id": metadata.request_id,
            "correlation_id": metadata.correlation_id,
            "client_ip": metadata.client_ip,
            "user_agent": metadata.user_agent,
            "method": metadata.method,
            "path": metadata.path,
            "timestamp": metadata.timestamp.isoformat(),
            "user_id": user.user_id if user else None,
            "username": user.username if user else None,
            "is_authenticated": user is not None,
        }


# ============================================================================
# DÉPENDANCES — Validation
# ============================================================================


if FASTAPI_AVAILABLE:

    def validate_path_id(
        value: str,
        *,
        param_name: str = "id",
        min_length: int = MIN_ID_LENGTH,
        max_length: int = MAX_ID_LENGTH,
        pattern: str = ID_PATTERN,
    ) -> str:
        """Valide un ID de ressource depuis les path parameters.

        Args:
            value: Valeur à valider.
            param_name: Nom du paramètre (pour messages d'erreur).
            min_length: Longueur minimale.
            max_length: Longueur maximale.
            pattern: Pattern regex.

        Returns:
            ID validé.

        Raises:
            HTTPException: 400 si l'ID est invalide.

        Example:
            >>> @router.get("/mangas/{manga_id}")
            >>> async def get_manga(
            ...     manga_id: str = Depends(lambda: validate_path_id(manga_id, param_name="manga_id")),
            ... ):
            ...     pass
        """
        value = value.strip()

        if len(value) < min_length:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_parameter",
                    "message": f"{param_name} is too short (min: {min_length})",
                    "parameter": param_name,
                    "value": value,
                },
            )

        if len(value) > max_length:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_parameter",
                    "message": f"{param_name} is too long (max: {max_length})",
                    "parameter": param_name,
                    "value": value,
                },
            )

        if pattern and not re.match(pattern, value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_parameter",
                    "message": f"{param_name} contains invalid characters",
                    "parameter": param_name,
                    "value": value,
                    "pattern": pattern,
                },
            )

        return value

    def validate_query_string(
        value: str,
        *,
        param_name: str = "query",
        min_length: int = MIN_QUERY_LENGTH,
        max_length: int = MAX_QUERY_LENGTH,
    ) -> str:
        """Valide une chaîne de requête.

        Args:
            value: Valeur à valider.
            param_name: Nom du paramètre.
            min_length: Longueur minimale.
            max_length: Longueur maximale.

        Returns:
            Chaîne validée et nettoyée.

        Raises:
            HTTPException: 400 si la chaîne est invalide.
        """
        value = value.strip()

        if len(value) < min_length:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_parameter",
                    "message": f"{param_name} is too short (min: {min_length})",
                    "parameter": param_name,
                },
            )

        if len(value) > max_length:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_parameter",
                    "message": f"{param_name} is too long (max: {max_length})",
                    "parameter": param_name,
                },
            )

        return value

    def validate_language_param(
        value: str | None,
        *,
        param_name: str = "language",
    ) -> str | None:
        """Valide un code langue ISO 639-1.

        Args:
            value: Code langue à valider.
            param_name: Nom du paramètre.

        Returns:
            Code langue validé (en minuscules) ou None.

        Raises:
            HTTPException: 400 si le code est invalide.
        """
        if value is None:
            return None

        value = value.strip().lower()

        if len(value) != 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_parameter",
                    "message": f"{param_name} must be a 2-letter ISO 639-1 code",
                    "parameter": param_name,
                    "value": value,
                },
            )

        if not value.isalpha():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_parameter",
                    "message": f"{param_name} must contain only letters",
                    "parameter": param_name,
                    "value": value,
                },
            )

        return value

    def validate_datetime_param(
        value: str | None,
        *,
        param_name: str = "datetime",
    ) -> datetime | None:
        """Valide un paramètre datetime (ISO 8601).

        Args:
            value: String datetime à valider.
            param_name: Nom du paramètre.

        Returns:
            Instance datetime ou None.

        Raises:
            HTTPException: 400 si le format est invalide.
        """
        if value is None:
            return None

        try:
            # Essayer différents formats ISO 8601
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

            raise ValueError(f"Format invalide: {value}")

        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_parameter",
                    "message": f"{param_name} must be a valid ISO 8601 datetime",
                    "parameter": param_name,
                    "value": value,
                    "reason": str(e),
                },
            ) from e


# ============================================================================
# HELPERS — Fonctions utilitaires pour les dépendances
# ============================================================================


def create_pagination_dependency(
    *,
    default_page: int = DEFAULT_PAGE,
    default_page_size: int = DEFAULT_PAGE_SIZE,
    max_page_size: int = MAX_PAGE_SIZE,
) -> Callable:
    """Crée une dépendance de pagination personnalisée.

    Args:
        default_page: Page par défaut.
        default_page_size: Taille de page par défaut.
        max_page_size: Taille de page maximale.

    Returns:
        Fonction de dépendance.

    Example:
        >>> custom_pagination = create_pagination_dependency(
        ...     default_page_size=50,
        ...     max_page_size=500,
        ... )
        >>>
        >>> @router.get("/large-list")
        >>> async def list_large(pagination: PaginationParams = Depends(custom_pagination)):
        ...     pass
    """
    if not FASTAPI_AVAILABLE:
        raise DependencyError("FastAPI n'est pas installé")

    def dependency(
        page: int = Query(default_page, ge=1, description="Numéro de page"),
        page_size: int = Query(
            default_page_size,
            ge=MIN_PAGE_SIZE,
            le=max_page_size,
            description="Taille de page",
        ),
    ) -> PaginationParams:
        return PaginationParams(page=page, page_size=page_size)

    return dependency


def create_sorting_dependency(
    *,
    default_sort_by: str = DEFAULT_SORT_BY,
    allowed_fields: list[str] | None = None,
) -> Callable:
    """Crée une dépendance de tri personnalisée.

    Args:
        default_sort_by: Champ de tri par défaut.
        allowed_fields: Liste des champs de tri autorisés.

    Returns:
        Fonction de dépendance.

    Example:
        >>> custom_sorting = create_sorting_dependency(
        ...     allowed_fields=["title", "created_at", "updated_at"],
        ... )
        >>>
        >>> @router.get("/items")
        >>> async def list_items(sorting: SortingParams = Depends(custom_sorting)):
        ...     pass
    """
    if not FASTAPI_AVAILABLE:
        raise DependencyError("FastAPI n'est pas installé")

    def dependency(
        sort_by: str = Query(default_sort_by, description="Champ de tri"),
        sort_order: str = Query(DEFAULT_SORT_ORDER, description="Ordre de tri"),
    ) -> SortingParams:
        if allowed_fields and sort_by not in allowed_fields:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_parameter",
                    "message": f"sort_by must be one of: {', '.join(allowed_fields)}",
                    "parameter": "sort_by",
                    "value": sort_by,
                    "allowed": allowed_fields,
                },
            )
        return SortingParams(sort_by=sort_by, sort_order=sort_order)

    return dependency


def create_id_validator(
    *,
    param_name: str = "id",
    min_length: int = MIN_ID_LENGTH,
    max_length: int = MAX_ID_LENGTH,
    pattern: str = ID_PATTERN,
) -> Callable:
    """Crée un validateur d'ID personnalisé.

    Args:
        param_name: Nom du paramètre.
        min_length: Longueur minimale.
        max_length: Longueur maximale.
        pattern: Pattern regex.

    Returns:
        Fonction de validation.

    Example:
        >>> validate_manga_id = create_id_validator(
        ...     param_name="manga_id",
        ...     max_length=100,
        ... )
        >>>
        >>> @router.get("/mangas/{manga_id}")
        >>> async def get_manga(
        ...     manga_id: str = Depends(validate_manga_id),
        ... ):
        ...     pass
    """
    if not FASTAPI_AVAILABLE:
        raise DependencyError("FastAPI n'est pas installé")

    def dependency(value: str) -> str:
        return validate_path_id(
            value,
            param_name=param_name,
            min_length=min_length,
            max_length=max_length,
            pattern=pattern,
        )

    return dependency


# ============================================================================
# INSTANCE GLOBALE — Cache de services
# ============================================================================


class ServiceCache:
    """Cache pour les instances de services.

    Évite de récupérer les services à chaque requête.
    """

    def __init__(self) -> None:
        """Initialise le cache."""
        self._cache: dict[str, Any] = {}
        self._ttl: dict[str, datetime] = {}
        self._default_ttl_seconds = 300  # 5 minutes

    def get(self, key: str) -> Any | None:
        """Récupère une valeur du cache.

        Args:
            key: Clé du cache.

        Returns:
            Valeur ou None si expirée/inexistante.
        """
        if key not in self._cache:
            return None

        if key in self._ttl and datetime.now(UTC) > self._ttl[key]:
            del self._cache[key]
            del self._ttl[key]
            return None

        return self._cache[key]

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """Stocke une valeur dans le cache.

        Args:
            key: Clé du cache.
            value: Valeur à stocker.
            ttl_seconds: Durée de vie (défaut: 5 minutes).
        """
        self._cache[key] = value
        ttl = ttl_seconds or self._default_ttl_seconds
        self._ttl[key] = datetime.now(UTC) + __import__("datetime").timedelta(seconds=ttl)

    def invalidate(self, key: str | None = None) -> None:
        """Invalide une entrée ou tout le cache.

        Args:
            key: Clé à invalider (None = tout).
        """
        if key is None:
            self._cache.clear()
            self._ttl.clear()
        else:
            self._cache.pop(key, None)
            self._ttl.pop(key, None)

    @property
    def size(self) -> int:
        """Taille actuelle du cache."""
        return len(self._cache)


# Instance globale du cache
_service_cache = ServiceCache()


def get_service_cache() -> ServiceCache:
    """Retourne l'instance globale du cache de services.

    Returns:
        Instance de ServiceCache.
    """
    return _service_cache


# ============================================================================
# DÉCORATEURS — Helpers pour les dépendances
# ============================================================================


def cache_service(key: str, ttl_seconds: int = 300) -> Callable:
    """Décorateur pour mettre en cache le résultat d'une dépendance.

    Args:
        key: Clé du cache.
        ttl_seconds: Durée de vie en secondes.

    Returns:
        Décorateur.

    Example:
        >>> @cache_service("expensive_service", ttl_seconds=600)
        >>> async def get_expensive_service() -> Any:
        ...     # Calcul coûteux
        ...     return result
    """
    def decorator(func: Callable) -> Callable:
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            cache = get_service_cache()
            cached = cache.get(key)
            if cached is not None:
                return cached

            result = await func(*args, **kwargs)
            cache.set(key, result, ttl_seconds)
            return result

        return wrapper

    return decorator


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_PAGE",
    "DEFAULT_PAGE_SIZE",
    "MIN_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "MAX_OFFSET",
    "DEFAULT_SORT_BY",
    "DEFAULT_SORT_ORDER",
    "SORT_ORDERS",
    "MIN_ID_LENGTH",
    "MAX_ID_LENGTH",
    "MIN_QUERY_LENGTH",
    "MAX_QUERY_LENGTH",
    "ID_PATTERN",
    "HEADER_REQUEST_ID",
    "HEADER_CORRELATION_ID",
    "HEADER_RATE_LIMIT",
    "HEADER_RATE_REMAINING",
    "HEADER_RATE_RESET",
    # Exceptions
    "DependencyError",
    "ServiceUnavailableError",
    "InvalidParameterError",
    # Modèles
    "PaginationParams",
    "SortingParams",
    "RequestMetadata",
    "RateLimitInfo",
    "UserIdentity",
    # Dépendances — Sécurité
    "get_current_user" if FASTAPI_AVAILABLE else None,
    "get_optional_user" if FASTAPI_AVAILABLE else None,
    "require_role" if FASTAPI_AVAILABLE else None,
    "require_permission" if FASTAPI_AVAILABLE else None,
    "require_any_role" if FASTAPI_AVAILABLE else None,
    "require_all_permissions" if FASTAPI_AVAILABLE else None,
    # Dépendances — Pagination & Tri
    "get_pagination" if FASTAPI_AVAILABLE else None,
    "get_sorting" if FASTAPI_AVAILABLE else None,
    # Dépendances — Services
    "get_site_registry" if FASTAPI_AVAILABLE else None,
    "get_download_manager" if FASTAPI_AVAILABLE else None,
    "get_library_manager" if FASTAPI_AVAILABLE else None,
    "get_event_bus" if FASTAPI_AVAILABLE else None,
    "get_config" if FASTAPI_AVAILABLE else None,
    # Dépendances — Métadonnées
    "get_request_metadata" if FASTAPI_AVAILABLE else None,
    "get_rate_limit_info" if FASTAPI_AVAILABLE else None,
    "get_client_info" if FASTAPI_AVAILABLE else None,
    # Dépendances — Validation
    "validate_path_id" if FASTAPI_AVAILABLE else None,
    "validate_query_string" if FASTAPI_AVAILABLE else None,
    "validate_language_param" if FASTAPI_AVAILABLE else None,
    "validate_datetime_param" if FASTAPI_AVAILABLE else None,
    # Helpers
    "create_pagination_dependency" if FASTAPI_AVAILABLE else None,
    "create_sorting_dependency" if FASTAPI_AVAILABLE else None,
    "create_id_validator" if FASTAPI_AVAILABLE else None,
    # Cache
    "ServiceCache",
    "get_service_cache",
    # Décorateurs
    "cache_service",
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
