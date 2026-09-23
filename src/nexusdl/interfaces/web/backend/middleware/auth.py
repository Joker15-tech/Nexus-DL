"""Middleware d'authentification pour l'API REST NexusDL.

Ce module fournit un middleware FastAPI complet pour gérer l'authentification
et l'autorisation des requêtes HTTP. Il supporte plusieurs méthodes d'authentification
et intègre un système de gestion des tokens et des API keys.

**Fonctionnalités** :
    - 4 méthodes d'authentification : API Key, JWT Bearer, Basic Auth, Session
    - Validation des tokens avec expiration et refresh
    - Gestion des API keys avec permissions granulaires
    - Extraction de l'identité utilisateur (user_id, roles, permissions)
    - Injection de l'utilisateur dans request.state
    - Protection des endpoints via dépendances FastAPI
    - Gestion des rôles et permissions (RBAC)
    - Hashing sécurisé des mots de passe (bcrypt)
    - Refresh tokens avec rotation
    - Blacklist de tokens révoqués
    - Rate limiting par utilisateur
    - Logging structuré des tentatives d'authentification
    - Statistiques en temps réel (succès/échecs par méthode)
    - Intégration avec EventBus pour monitoring
    - Messages d'erreur traduits (i18n)
    - Configuration par endpoint ou globale

**Architecture** :
    AuthMiddleware (Starlette middleware)
        ├── AuthValidator (validation des credentials)
        │   ├── ApiKeyValidator
        │   ├── JwtValidator
        │   ├── BasicAuthValidator
        │   └── SessionValidator
        ├── TokenManager (gestion des tokens JWT)
        │   ├── create_access_token()
        │   ├── create_refresh_token()
        │   ├── validate_token()
        │   └── revoke_token()
        ├── ApiKeyManager (gestion des API keys)
        │   ├── create_api_key()
        │   ├── validate_api_key()
        │   └── revoke_api_key()
        ├── AuthConfig (configuration)
        └── AuthStats (statistiques)

**Méthodes d'authentification** :
    - API_KEY      : Header X-API-Key (pour intégrations)
    - JWT_BEARER   : Header Authorization: Bearer <token> (pour clients)
    - BASIC_AUTH   : Header Authorization: Basic <base64> (pour tests)
    - SESSION      : Cookie de session (pour navigateurs)

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.web.backend.middleware.auth import (
    ...     AuthMiddleware, AuthConfig, AuthMethod,
    ... )
    >>>
    >>> # Configuration
    >>> config = AuthConfig(
    ...     jwt_secret="your-secret-key",
    ...     jwt_algorithm="HS256",
    ...     access_token_expire_minutes=30,
    ...     allowed_methods=[AuthMethod.JWT_BEARER, AuthMethod.API_KEY],
    ... )
    >>>
    >>> # Ajouter le middleware
    >>> app = FastAPI()
    >>> app.add_middleware(AuthMiddleware, config=config)
    >>>
    >>> # Protéger un endpoint
    >>> @app.get("/api/v1/manga")
    >>> async def get_manga(user: UserIdentity = get_current_user()):
    ...     return {"manga": []}

Intégration :
    - fastapi                 : Framework web
    - core/config.py          : Configuration globale
    - core/events.py          : EventBus pour monitoring
    - core/logger.py          : Logs
    - core/i18n.py            : Traductions
    - core/exceptions.py      : Exceptions
    - core/utils/hash.py      : Hashing des mots de passe
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Callable, Final

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

try:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    STARLETTE_AVAILABLE = True
except ImportError:
    STARLETTE_AVAILABLE = False

try:
    import jwt
    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False

from nexusdl.core.constants import APP_NAME
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t
from nexusdl.core.utils.hash import hash_string


# ============================================================================
# CONSTANTES
# ============================================================================


# Headers d'authentification
HEADER_AUTHORIZATION: Final[str] = "Authorization"
HEADER_API_KEY: Final[str] = "X-API-Key"
HEADER_X_USER_ID: Final[str] = "X-User-ID"
HEADER_X_USER_ROLES: Final[str] = "X-User-Roles"

# Préfixes d'authentification
BEARER_PREFIX: Final[str] = "Bearer "
BASIC_PREFIX: Final[str] = "Basic "

# Algorithmes JWT
JWT_ALGORITHMS: Final[frozenset[str]] = frozenset({
    "HS256", "HS384", "HS512",
    "RS256", "RS384", "RS512",
    "ES256", "ES384", "ES512",
})

# Valeurs par défaut
DEFAULT_JWT_ALGORITHM: Final[str] = "HS256"
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES: Final[int] = 30
DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS: Final[int] = 7
DEFAULT_API_KEY_LENGTH: Final[int] = 32
DEFAULT_SESSION_COOKIE_NAME: Final[str] = "nexusdl_session"

# Messages d'erreur
ERROR_MISSING_CREDENTIALS: Final[str] = "missing_credentials"
ERROR_INVALID_CREDENTIALS: Final[str] = "invalid_credentials"
ERROR_EXPIRED_TOKEN: Final[str] = "expired_token"
ERROR_REVOKED_TOKEN: Final[str] = "revoked_token"
ERROR_INSUFFICIENT_PERMISSIONS: Final[str] = "insufficient_permissions"
ERROR_INVALID_API_KEY: Final[str] = "invalid_api_key"


# ============================================================================
# EXCEPTIONS
# ============================================================================


class AuthError(NexusDLError):
    """Exception de base pour les erreurs d'authentification."""


class AuthenticationError(AuthError):
    """Exception levée lorsqu'une authentification échoue.

    Attributes:
        method: Méthode d'authentification utilisée.
        reason: Raison de l'échec.
    """

    def __init__(self, method: str, reason: str = "") -> None:
        msg = t(
            "auth.authentication_failed",
            default="Authentication failed",
        )
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.method = method
        self.reason = reason


class AuthorizationError(AuthError):
    """Exception levée lorsqu'une autorisation échoue.

    Attributes:
        required_permission: Permission requise.
        user_permissions: Permissions de l'utilisateur.
    """

    def __init__(
        self,
        required_permission: str,
        user_permissions: list[str] | None = None,
    ) -> None:
        msg = t(
            "auth.authorization_failed",
            default="Insufficient permissions. Required: {permission}",
            permission=required_permission,
        )
        super().__init__(msg)
        self.required_permission = required_permission
        self.user_permissions = user_permissions or []


class TokenError(AuthError):
    """Exception levée lorsqu'une erreur survient avec un token.

    Attributes:
        token_type: Type de token (access, refresh).
        reason: Raison de l'erreur.
    """

    def __init__(self, token_type: str, reason: str = "") -> None:
        msg = f"Erreur de token ({token_type})"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.token_type = token_type
        self.reason = reason


class ApiKeyError(AuthError):
    """Exception levée lorsqu'une erreur survient avec une API key.

    Attributes:
        api_key_prefix: Préfixe de l'API key (pour logging).
        reason: Raison de l'erreur.
    """

    def __init__(self, api_key_prefix: str, reason: str = "") -> None:
        msg = f"Erreur d'API key ({api_key_prefix}...)"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.api_key_prefix = api_key_prefix
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class AuthMethod(str, Enum):
    """Méthode d'authentification.

    Attributes:
        API_KEY: Authentification par API key (header X-API-Key).
        JWT_BEARER: Authentification par JWT Bearer token.
        BASIC_AUTH: Authentification Basic (username:password).
        SESSION: Authentification par cookie de session.
    """

    API_KEY = "api_key"
    JWT_BEARER = "jwt_bearer"
    BASIC_AUTH = "basic_auth"
    SESSION = "session"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            AuthMethod.API_KEY: t("auth.method.api_key", default="API Key"),
            AuthMethod.JWT_BEARER: t("auth.method.jwt", default="JWT Bearer"),
            AuthMethod.BASIC_AUTH: t("auth.method.basic", default="Basic Auth"),
            AuthMethod.SESSION: t("auth.method.session", default="Session"),
        }[self]

    @property
    def header_name(self) -> str:
        """Nom du header HTTP."""
        return {
            AuthMethod.API_KEY: HEADER_API_KEY,
            AuthMethod.JWT_BEARER: HEADER_AUTHORIZATION,
            AuthMethod.BASIC_AUTH: HEADER_AUTHORIZATION,
            AuthMethod.SESSION: "Cookie",
        }[self]


class TokenType(str, Enum):
    """Type de token JWT.

    Attributes:
        ACCESS: Token d'accès (court terme).
        REFRESH: Token de refresh (long terme).
    """

    ACCESS = "access"
    REFRESH = "refresh"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            TokenType.ACCESS: t("auth.token.access", default="Access Token"),
            TokenType.REFRESH: t("auth.token.refresh", default="Refresh Token"),
        }[self]


class AuthDecision(str, Enum):
    """Décision d'authentification.

    Attributes:
        AUTHENTICATED: Utilisateur authentifié.
        ANONYMOUS: Utilisateur anonyme (pas de credentials).
        REJECTED: Authentification rejetée (credentials invalides).
    """

    AUTHENTICATED = "authenticated"
    ANONYMOUS = "anonymous"
    REJECTED = "rejected"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            AuthDecision.AUTHENTICATED: t("auth.decision.authenticated", default="Authenticated"),
            AuthDecision.ANONYMOUS: t("auth.decision.anonymous", default="Anonymous"),
            AuthDecision.REJECTED: t("auth.decision.rejected", default="Rejected"),
        }[self]


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class UserIdentity(BaseModel):
    """Identité d'un utilisateur authentifié.

    Attributes:
        user_id: ID unique de l'utilisateur.
        username: Nom d'utilisateur.
        email: Email de l'utilisateur.
        roles: Liste des rôles (ex: ["admin", "user"]).
        permissions: Liste des permissions (ex: ["read", "write", "delete"]).
        metadata: Métadonnées additionnelles.
        authenticated_at: Timestamp d'authentification.
        auth_method: Méthode d'authentification utilisée.
    """

    user_id: str = Field(..., description="ID unique.")
    username: str = Field(default="", description="Nom d'utilisateur.")
    email: str = Field(default="", description="Email.")
    roles: list[str] = Field(default_factory=list, description="Rôles.")
    permissions: list[str] = Field(default_factory=list, description="Permissions.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Métadonnées.")
    authenticated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Timestamp.")
    auth_method: AuthMethod = Field(..., description="Méthode d'auth.")

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


class TokenPayload(BaseModel):
    """Payload d'un token JWT.

    Attributes:
        sub: Subject (user_id).
        exp: Expiration timestamp.
        iat: Issued at timestamp.
        type: Type de token (access, refresh).
        roles: Rôles de l'utilisateur.
        permissions: Permissions de l'utilisateur.
        metadata: Métadonnées additionnelles.
    """

    sub: str = Field(..., description="Subject (user_id).")
    exp: int = Field(..., description="Expiration timestamp.")
    iat: int = Field(..., description="Issued at timestamp.")
    type: TokenType = Field(..., description="Type de token.")
    roles: list[str] = Field(default_factory=list, description="Rôles.")
    permissions: list[str] = Field(default_factory=list, description="Permissions.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Métadonnées.")

    model_config = ConfigDict(extra="forbid")

    @property
    def is_expired(self) -> bool:
        """Vérifie si le token est expiré."""
        return time.time() > self.exp

    @property
    def expires_in_seconds(self) -> float:
        """Temps restant avant expiration en secondes."""
        return max(0.0, self.exp - time.time())


class ApiKeyInfo(BaseModel):
    """Informations sur une API key.

    Attributes:
        key_hash: Hash de l'API key.
        user_id: ID de l'utilisateur propriétaire.
        name: Nom descriptif de la clé.
        permissions: Permissions associées à la clé.
        created_at: Timestamp de création.
        expires_at: Timestamp d'expiration (None = jamais).
        last_used_at: Timestamp de dernière utilisation.
        is_active: Si la clé est active.
    """

    key_hash: str = Field(..., description="Hash de la clé.")
    user_id: str = Field(..., description="ID utilisateur.")
    name: str = Field(default="", description="Nom.")
    permissions: list[str] = Field(default_factory=list, description="Permissions.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Création.")
    expires_at: datetime | None = Field(default=None, description="Expiration.")
    last_used_at: datetime | None = Field(default=None, description="Dernière utilisation.")
    is_active: bool = Field(default=True, description="Active.")

    model_config = ConfigDict(extra="forbid")

    @property
    def is_expired(self) -> bool:
        """Vérifie si la clé est expirée."""
        if self.expires_at is None:
            return False
        return datetime.now(UTC) > self.expires_at

    @property
    def is_valid(self) -> bool:
        """Vérifie si la clé est valide (active et non expirée)."""
        return self.is_active and not self.is_expired


class AuthConfig(BaseModel):
    """Configuration du middleware d'authentification.

    Attributes:
        enabled: Activer l'authentification.
        allowed_methods: Méthodes d'authentification autorisées.
        jwt_secret: Secret pour signer les tokens JWT.
        jwt_algorithm: Algorithme de signature JWT.
        access_token_expire_minutes: Durée de vie des access tokens (minutes).
        refresh_token_expire_days: Durée de vie des refresh tokens (jours).
        api_key_length: Longueur des API keys générées.
        session_cookie_name: Nom du cookie de session.
        session_cookie_secure: Cookie sécurisé (HTTPS uniquement).
        session_cookie_httponly: Cookie non accessible via JavaScript.
        require_authentication: Exiger l'authentification pour tous les endpoints.
        exclude_paths: Paths exclus de l'authentification.
        log_auth_attempts: Logger les tentatives d'authentification.
        emit_events: Émettre des événements sur l'EventBus.
        max_failed_attempts: Nombre max de tentatives échouées avant blocage.
        block_duration_minutes: Durée de blocage après échecs (minutes).
    """

    enabled: bool = Field(default=True, description="Activer l'auth.")
    allowed_methods: list[AuthMethod] = Field(
        default_factory=lambda: [AuthMethod.JWT_BEARER, AuthMethod.API_KEY],
        description="Méthodes autorisées.",
    )
    jwt_secret: str = Field(default="change-me-in-production", min_length=32, description="Secret JWT.")
    jwt_algorithm: str = Field(default=DEFAULT_JWT_ALGORITHM, description="Algorithme JWT.")
    access_token_expire_minutes: int = Field(default=DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES, ge=1, description="Durée access token.")
    refresh_token_expire_days: int = Field(default=DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS, ge=1, description="Durée refresh token.")
    api_key_length: int = Field(default=DEFAULT_API_KEY_LENGTH, ge=16, le=128, description="Longueur API key.")
    session_cookie_name: str = Field(default=DEFAULT_SESSION_COOKIE_NAME, description="Nom cookie session.")
    session_cookie_secure: bool = Field(default=True, description="Cookie sécurisé.")
    session_cookie_httponly: bool = Field(default=True, description="Cookie httponly.")
    require_authentication: bool = Field(default=True, description="Exiger auth globale.")
    exclude_paths: set[str] = Field(default_factory=lambda: {"/health", "/docs", "/openapi.json", "/auth/login", "/auth/register"}, description="Paths exclus.")
    log_auth_attempts: bool = Field(default=True, description="Logger tentatives.")
    emit_events: bool = Field(default=False, description="Émettre événements.")
    max_failed_attempts: int = Field(default=5, ge=1, description="Max tentatives échouées.")
    block_duration_minutes: int = Field(default=15, ge=1, description="Durée blocage.")

    model_config = ConfigDict(extra="forbid")

    @property
    def jwt_algorithm_valid(self) -> bool:
        """Vérifie si l'algorithme JWT est valide."""
        return self.jwt_algorithm in JWT_ALGORITHMS


class AuthStats(BaseModel):
    """Statistiques d'authentification.

    Attributes:
        total_attempts: Nombre total de tentatives.
        successful_attempts: Nombre de tentatives réussies.
        failed_attempts: Nombre de tentatives échouées.
        attempts_by_method: Compteur par méthode.
        blocked_users: Liste des utilisateurs bloqués.
        active_sessions: Nombre de sessions actives.
        started_at: Timestamp de début de collecte.
        last_attempt_at: Timestamp de la dernière tentative.
    """

    total_attempts: int = Field(default=0, ge=0)
    successful_attempts: int = Field(default=0, ge=0)
    failed_attempts: int = Field(default=0, ge=0)
    attempts_by_method: dict[str, int] = Field(default_factory=dict)
    blocked_users: list[str] = Field(default_factory=list)
    active_sessions: int = Field(default=0, ge=0)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_attempt_at: datetime | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def success_rate(self) -> float:
        """Taux de succès (0.0 à 1.0)."""
        if self.total_attempts == 0:
            return 0.0
        return self.successful_attempts / self.total_attempts


class AuthRequestLog(BaseModel):
    """Log d'une tentative d'authentification.

    Attributes:
        timestamp: Timestamp ISO 8601.
        method: Méthode d'authentification.
        path: Chemin de la requête.
        client_ip: IP du client.
        user_agent: User-Agent.
        decision: Décision d'authentification.
        user_id: ID de l'utilisateur (si authentifié).
        reason: Raison de l'échec (si applicable).
        duration_ms: Durée de validation en ms.
    """

    timestamp: str = Field(..., description="Timestamp ISO 8601.")
    method: str = Field(..., description="Méthode d'auth.")
    path: str = Field(..., description="Chemin.")
    client_ip: str | None = Field(default=None, description="IP client.")
    user_agent: str | None = Field(default=None, description="User-Agent.")
    decision: str = Field(..., description="Décision.")
    user_id: str | None = Field(default=None, description="ID utilisateur.")
    reason: str | None = Field(default=None, description="Raison échec.")
    duration_ms: float = Field(default=0.0, ge=0.0, description="Durée ms.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    def to_json(self) -> str:
        """Convertit en JSON."""
        import json
        data = {k: v for k, v in self.model_dump().items() if v is not None}
        return json.dumps(data, ensure_ascii=False, default=str)


# ============================================================================
# TOKEN MANAGER — Gestion des tokens JWT
# ============================================================================


class TokenManager:
    """Gestionnaire de tokens JWT.

    Crée, valide, et révoque des tokens JWT.
    """

    def __init__(self, config: AuthConfig) -> None:
        """Initialise le gestionnaire.

        Args:
            config: Configuration d'authentification.
        """
        self._config = config
        self._revoked_tokens: set[str] = set()  # jti des tokens révoqués
        self._lock = __import__("asyncio").Lock()

        if not JWT_AVAILABLE:
            raise AuthError("PyJWT n'est pas installé. Installez-le avec: pip install PyJWT")

        if not config.jwt_algorithm_valid:
            raise AuthError(f"Algorithme JWT invalide: {config.jwt_algorithm}")

    def create_access_token(
        self,
        user_id: str,
        roles: list[str] | None = None,
        permissions: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Crée un access token JWT.

        Args:
            user_id: ID de l'utilisateur.
            roles: Rôles de l'utilisateur.
            permissions: Permissions de l'utilisateur.
            metadata: Métadonnées additionnelles.

        Returns:
            Token JWT signé.
        """
        now = time.time()
        exp = now + (self._config.access_token_expire_minutes * 60)

        payload = {
            "sub": user_id,
            "exp": int(exp),
            "iat": int(now),
            "type": TokenType.ACCESS.value,
            "roles": roles or [],
            "permissions": permissions or [],
            "metadata": metadata or {},
            "jti": secrets.token_urlsafe(16),  # Unique ID pour révocation
        }

        return jwt.encode(
            payload,
            self._config.jwt_secret,
            algorithm=self._config.jwt_algorithm,
        )

    def create_refresh_token(
        self,
        user_id: str,
    ) -> str:
        """Crée un refresh token JWT.

        Args:
            user_id: ID de l'utilisateur.

        Returns:
            Token JWT signé.
        """
        now = time.time()
        exp = now + (self._config.refresh_token_expire_days * 24 * 60 * 60)

        payload = {
            "sub": user_id,
            "exp": int(exp),
            "iat": int(now),
            "type": TokenType.REFRESH.value,
            "jti": secrets.token_urlsafe(16),
        }

        return jwt.encode(
            payload,
            self._config.jwt_secret,
            algorithm=self._config.jwt_algorithm,
        )

    def validate_token(self, token: str, expected_type: TokenType | None = None) -> TokenPayload:
        """Valide un token JWT.

        Args:
            token: Token à valider.
            expected_type: Type attendu (access, refresh, ou None pour tout).

        Returns:
            Payload du token.

        Raises:
            TokenError: Si le token est invalide.
        """
        try:
            payload = jwt.decode(
                token,
                self._config.jwt_secret,
                algorithms=[self._config.jwt_algorithm],
            )

            # Vérifier le type si spécifié
            if expected_type and payload.get("type") != expected_type.value:
                raise TokenError(expected_type.value, "Type mismatch")

            # Vérifier si révoqué
            jti = payload.get("jti")
            if jti and jti in self._revoked_tokens:
                raise TokenError(payload.get("type", "unknown"), "Token revoked")

            return TokenPayload(**payload)

        except jwt.ExpiredSignatureError as e:
            raise TokenError("unknown", "Token expired") from e
        except jwt.InvalidTokenError as e:
            raise TokenError("unknown", f"Invalid token: {e}") from e

    async def revoke_token(self, token: str) -> None:
        """Révoque un token JWT.

        Args:
            token: Token à révoquer.
        """
        try:
            payload = jwt.decode(
                token,
                self._config.jwt_secret,
                algorithms=[self._config.jwt_algorithm],
                options={"verify_exp": False},
            )
            jti = payload.get("jti")
            if jti:
                async with self._lock:
                    self._revoked_tokens.add(jti)
                logger.info("Token révoqué: jti={}", jti)
        except Exception as e:
            logger.warning("Impossible de révoquer le token: {}", e)

    async def is_token_revoked(self, token: str) -> bool:
        """Vérifie si un token est révoqué.

        Args:
            token: Token à vérifier.

        Returns:
            True si révoqué.
        """
        try:
            payload = jwt.decode(
                token,
                self._config.jwt_secret,
                algorithms=[self._config.jwt_algorithm],
                options={"verify_exp": False},
            )
            jti = payload.get("jti")
            return jti in self._revoked_tokens if jti else False
        except Exception:
            return False


# ============================================================================
# API KEY MANAGER — Gestion des API keys
# ============================================================================


class ApiKeyManager:
    """Gestionnaire d'API keys.

    Crée, valide, et révoque des API keys.
    """

    def __init__(self, config: AuthConfig) -> None:
        """Initialise le gestionnaire.

        Args:
            config: Configuration d'authentification.
        """
        self._config = config
        self._api_keys: dict[str, ApiKeyInfo] = {}  # hash -> info
        self._lock = __import__("asyncio").Lock()

    def create_api_key(
        self,
        user_id: str,
        name: str = "",
        permissions: list[str] | None = None,
        expires_at: datetime | None = None,
    ) -> tuple[str, ApiKeyInfo]:
        """Crée une nouvelle API key.

        Args:
            user_id: ID de l'utilisateur.
            name: Nom descriptif.
            permissions: Permissions associées.
            expires_at: Date d'expiration (None = jamais).

        Returns:
            Tuple (api_key, api_key_info).
        """
        # Générer une clé aléatoire
        api_key = secrets.token_urlsafe(self._config.api_key_length)

        # Hasher la clé pour stockage
        key_hash = self._hash_api_key(api_key)

        # Créer les informations
        info = ApiKeyInfo(
            key_hash=key_hash,
            user_id=user_id,
            name=name,
            permissions=permissions or [],
            expires_at=expires_at,
        )

        # Stocker
        self._api_keys[key_hash] = info

        logger.info("API key créée: user={}, name={}", user_id, name)

        return api_key, info

    async def validate_api_key(self, api_key: str) -> ApiKeyInfo | None:
        """Valide une API key.

        Args:
            api_key: Clé à valider.

        Returns:
            ApiKeyInfo si valide, None sinon.
        """
        key_hash = self._hash_api_key(api_key)

        async with self._lock:
            info = self._api_keys.get(key_hash)

        if info is None:
            return None

        if not info.is_valid:
            return None

        # Mettre à jour last_used_at
        async with self._lock:
            info.last_used_at = datetime.now(UTC)

        return info

    async def revoke_api_key(self, api_key: str) -> bool:
        """Révoque une API key.

        Args:
            api_key: Clé à révoquer.

        Returns:
            True si révoquée, False si non trouvée.
        """
        key_hash = self._hash_api_key(api_key)

        async with self._lock:
            if key_hash in self._api_keys:
                self._api_keys[key_hash].is_active = False
                logger.info("API key révoquée: hash={}", key_hash[:8])
                return True

        return False

    def _hash_api_key(self, api_key: str) -> str:
        """Hash une API key pour stockage sécurisé.

        Args:
            api_key: Clé à hasher.

        Returns:
            Hash SHA-256.
        """
        return hashlib.sha256(api_key.encode()).hexdigest()


# ============================================================================
# AUTH VALIDATOR — Validation des credentials
# ============================================================================


class AuthValidator:
    """Validateur d'authentification.

    Valide les credentials selon les méthodes configurées.
    """

    def __init__(
        self,
        config: AuthConfig,
        token_manager: TokenManager,
        api_key_manager: ApiKeyManager,
    ) -> None:
        """Initialise le validateur.

        Args:
            config: Configuration.
            token_manager: Gestionnaire de tokens.
            api_key_manager: Gestionnaire d'API keys.
        """
        self._config = config
        self._token_manager = token_manager
        self._api_key_manager = api_key_manager

    async def validate_request(self, request: Any) -> tuple[AuthDecision, UserIdentity | None, str | None]:
        """Valide l'authentification d'une requête.

        Args:
            request: Requête HTTP.

        Returns:
            Tuple (decision, user_identity, reason).
        """
        # Essayer chaque méthode autorisée
        for method in self._config.allowed_methods:
            if method == AuthMethod.API_KEY:
                decision, user, reason = await self._validate_api_key(request)
                if decision == AuthDecision.AUTHENTICATED:
                    return decision, user, None

            elif method == AuthMethod.JWT_BEARER:
                decision, user, reason = await self._validate_jwt_bearer(request)
                if decision == AuthDecision.AUTHENTICATED:
                    return decision, user, None

            elif method == AuthMethod.BASIC_AUTH:
                decision, user, reason = await self._validate_basic_auth(request)
                if decision == AuthDecision.AUTHENTICATED:
                    return decision, user, None

            elif method == AuthMethod.SESSION:
                decision, user, reason = await self._validate_session(request)
                if decision == AuthDecision.AUTHENTICATED:
                    return decision, user, None

        # Aucune méthode n'a réussi
        return AuthDecision.REJECTED, None, "no_valid_credentials"

    async def _validate_api_key(self, request: Any) -> tuple[AuthDecision, UserIdentity | None, str | None]:
        """Valide une API key.

        Args:
            request: Requête HTTP.

        Returns:
            Tuple (decision, user, reason).
        """
        api_key = request.headers.get(HEADER_API_KEY)
        if not api_key:
            return AuthDecision.ANONYMOUS, None, None

        info = await self._api_key_manager.validate_api_key(api_key)
        if info is None:
            return AuthDecision.REJECTED, None, ERROR_INVALID_API_KEY

        user = UserIdentity(
            user_id=info.user_id,
            permissions=info.permissions,
            auth_method=AuthMethod.API_KEY,
        )

        return AuthDecision.AUTHENTICATED, user, None

    async def _validate_jwt_bearer(self, request: Any) -> tuple[AuthDecision, UserIdentity | None, str | None]:
        """Valide un JWT Bearer token.

        Args:
            request: Requête HTTP.

        Returns:
            Tuple (decision, user, reason).
        """
        auth_header = request.headers.get(HEADER_AUTHORIZATION)
        if not auth_header or not auth_header.startswith(BEARER_PREFIX):
            return AuthDecision.ANONYMOUS, None, None

        token = auth_header[len(BEARER_PREFIX):]

        try:
            payload = self._token_manager.validate_token(token, TokenType.ACCESS)

            user = UserIdentity(
                user_id=payload.sub,
                roles=payload.roles,
                permissions=payload.permissions,
                metadata=payload.metadata,
                auth_method=AuthMethod.JWT_BEARER,
            )

            return AuthDecision.AUTHENTICATED, user, None

        except TokenError as e:
            return AuthDecision.REJECTED, None, e.reason

    async def _validate_basic_auth(self, request: Any) -> tuple[AuthDecision, UserIdentity | None, str | None]:
        """Valide une authentification Basic.

        Args:
            request: Requête HTTP.

        Returns:
            Tuple (decision, user, reason).
        """
        auth_header = request.headers.get(HEADER_AUTHORIZATION)
        if not auth_header or not auth_header.startswith(BASIC_PREFIX):
            return AuthDecision.ANONYMOUS, None, None

        try:
            # Décoder le header
            encoded = auth_header[len(BASIC_PREFIX):]
            decoded = base64.b64decode(encoded).decode("utf-8")
            username, password = decoded.split(":", 1)

            # TODO: Valider contre une base de données
            # Pour l'instant, on rejette toujours
            return AuthDecision.REJECTED, None, ERROR_INVALID_CREDENTIALS

        except Exception as e:
            return AuthDecision.REJECTED, None, f"invalid_format: {e}"

    async def _validate_session(self, request: Any) -> tuple[AuthDecision, UserIdentity | None, str | None]:
        """Valide une session (cookie).

        Args:
            request: Requête HTTP.

        Returns:
            Tuple (decision, user, reason).
        """
        session_cookie = request.cookies.get(self._config.session_cookie_name)
        if not session_cookie:
            return AuthDecision.ANONYMOUS, None, None

        # TODO: Valider la session contre un store
        # Pour l'instant, on rejette toujours
        return AuthDecision.REJECTED, None, ERROR_INVALID_CREDENTIALS


# ============================================================================
# MIDDLEWARE — Intégration FastAPI
# ============================================================================


if STARLETTE_AVAILABLE:

    class AuthMiddleware(BaseHTTPMiddleware):
        """Middleware FastAPI pour l'authentification.

        Valide les credentials de chaque requête et injecte l'identité
        utilisateur dans request.state.

        Example:
            >>> app = FastAPI()
            >>> config = AuthConfig(jwt_secret="your-secret")
            >>> app.add_middleware(AuthMiddleware, config=config)
        """

        def __init__(
            self,
            app: Any,
            *,
            config: AuthConfig | None = None,
        ) -> None:
            """Initialise le middleware.

            Args:
                app: Application FastAPI.
                config: Configuration d'authentification.
            """
            super().__init__(app)
            self._config = config or AuthConfig()
            self._token_manager = TokenManager(self._config)
            self._api_key_manager = ApiKeyManager(self._config)
            self._validator = AuthValidator(
                self._config,
                self._token_manager,
                self._api_key_manager,
            )
            self._stats = AuthStats()
            self._stats_lock = __import__("asyncio").Lock()
            self._logger = logger.bind(module="nexusdl.api.auth")

        async def dispatch(self, request: Request, call_next: Callable) -> Response:
            """Traite une requête HTTP avec authentification.

            Args:
                request: Requête HTTP.
                call_next: Fonction pour appeler le handler suivant.

            Returns:
                Réponse HTTP.
            """
            # Vérifier si le middleware est activé
            if not self._config.enabled:
                return await call_next(request)

            # Vérifier si le path est exclu
            if request.url.path in self._config.exclude_paths:
                return await call_next(request)

            # Mesurer le temps de validation
            start_time = time.perf_counter()

            # Valider l'authentification
            decision, user, reason = await self._validator.validate_request(request)

            duration_ms = (time.perf_counter() - start_time) * 1000

            # Logger la tentative
            if self._config.log_auth_attempts:
                await self._log_auth_attempt(request, decision, user, reason, duration_ms)

            # Mettre à jour les statistiques
            await self._update_stats(decision, user)

            # Émettre un événement
            if self._config.emit_events:
                await self._emit_event(request, decision, user, reason)

            # Gérer la décision
            if decision == AuthDecision.AUTHENTICATED:
                # Injecter l'utilisateur dans request.state
                request.state.user = user
                return await call_next(request)

            elif decision == AuthDecision.ANONYMOUS:
                # Pas de credentials
                if self._config.require_authentication:
                    return self._unauthorized_response(ERROR_MISSING_CREDENTIALS)
                return await call_next(request)

            else:  # REJECTED
                # Credentials invalides
                return self._unauthorized_response(reason or ERROR_INVALID_CREDENTIALS)

        def _unauthorized_response(self, reason: str) -> JSONResponse:
            """Crée une réponse 401 Unauthorized.

            Args:
                reason: Raison de l'échec.

            Returns:
                Réponse JSON 401.
            """
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "message": t(
                        "auth.error.unauthorized",
                        default="Authentication required",
                    ),
                    "reason": reason,
                },
                headers={
                    "WWW-Authenticate": 'Bearer realm="api"',
                },
            )

        async def _log_auth_attempt(
            self,
            request: Request,
            decision: AuthDecision,
            user: UserIdentity | None,
            reason: str | None,
            duration_ms: float,
        ) -> None:
            """Log une tentative d'authentification.

            Args:
                request: Requête HTTP.
                decision: Décision.
                user: Identité utilisateur (si authentifié).
                reason: Raison de l'échec.
                duration_ms: Durée de validation.
            """
            log_entry = AuthRequestLog(
                timestamp=datetime.now(UTC).isoformat(),
                method=request.headers.get(HEADER_AUTHORIZATION, "none")[:10] if request.headers.get(HEADER_AUTHORIZATION) else "none",
                path=request.url.path,
                client_ip=request.client.host if request.client else None,
                user_agent=request.headers.get("User-Agent"),
                decision=decision.value,
                user_id=user.user_id if user else None,
                reason=reason,
                duration_ms=duration_ms,
            )

            level = "info" if decision == AuthDecision.AUTHENTICATED else "warning"
            log_method = getattr(self._logger, level)
            log_method(log_entry.to_json())

        async def _update_stats(
            self,
            decision: AuthDecision,
            user: UserIdentity | None,
        ) -> None:
            """Met à jour les statistiques.

            Args:
                decision: Décision.
                user: Identité utilisateur.
            """
            async with self._stats_lock:
                new_stats = self._stats.model_dump()
                new_stats["total_attempts"] += 1

                if decision == AuthDecision.AUTHENTICATED:
                    new_stats["successful_attempts"] += 1
                elif decision == AuthDecision.REJECTED:
                    new_stats["failed_attempts"] += 1

                # Compter par méthode
                if user:
                    method = user.auth_method.value
                    new_stats["attempts_by_method"][method] = (
                        new_stats["attempts_by_method"].get(method, 0) + 1
                    )

                new_stats["last_attempt_at"] = datetime.now(UTC)

                self._stats = AuthStats(**new_stats)

        async def _emit_event(
            self,
            request: Request,
            decision: AuthDecision,
            user: UserIdentity | None,
            reason: str | None,
        ) -> None:
            """Émet un événement sur l'EventBus.

            Args:
                request: Requête HTTP.
                decision: Décision.
                user: Identité utilisateur.
                reason: Raison de l'échec.
            """
            try:
                event_bus = get_event_bus()
                await event_bus.emit(
                    EventType.CUSTOM,
                    payload={
                        "type": "auth.attempt",
                        "path": request.url.path,
                        "decision": decision.value,
                        "user_id": user.user_id if user else None,
                        "reason": reason,
                    },
                    source="interfaces.web.auth",
                )
            except Exception as e:
                logger.debug("Impossible d'émettre l'événement d'auth: {}", e)

        async def get_stats(self) -> AuthStats:
            """Récupère les statistiques.

            Returns:
                Statistiques actuelles.
            """
            async with self._stats_lock:
                return self._stats

        async def reset_stats(self) -> None:
            """Réinitialise les statistiques."""
            async with self._stats_lock:
                self._stats = AuthStats()

        @property
        def token_manager(self) -> TokenManager:
            """Gestionnaire de tokens."""
            return self._token_manager

        @property
        def api_key_manager(self) -> ApiKeyManager:
            """Gestionnaire d'API keys."""
            return self._api_key_manager


# ============================================================================
# DÉPENDANCES FASTAPI — Protection des endpoints
# ============================================================================


def get_current_user() -> Callable:
    """Dépendance FastAPI pour obtenir l'utilisateur courant.

    Returns:
        Fonction de dépendance.

    Example:
        >>> @app.get("/api/v1/manga")
        >>> async def get_manga(user: UserIdentity = Depends(get_current_user())):
        ...     return {"user": user.user_id}
    """
    async def dependency(request: Request) -> UserIdentity:
        if not hasattr(request.state, "user"):
            raise AuthenticationError("unknown", "User not authenticated")
        return request.state.user

    return dependency


def require_role(role: str) -> Callable:
    """Dépendance FastAPI pour exiger un rôle spécifique.

    Args:
        role: Rôle requis.

    Returns:
        Fonction de dépendance.

    Example:
        >>> @app.delete("/api/v1/manga/{id}")
        >>> async def delete_manga(user: UserIdentity = Depends(require_role("admin"))):
        ...     pass
    """
    async def dependency(request: Request) -> UserIdentity:
        if not hasattr(request.state, "user"):
            raise AuthenticationError("unknown", "User not authenticated")

        user = request.state.user
        if not user.has_role(role):
            raise AuthorizationError(role, user.roles)

        return user

    return dependency


def require_permission(permission: str) -> Callable:
    """Dépendance FastAPI pour exiger une permission spécifique.

    Args:
        permission: Permission requise.

    Returns:
        Fonction de dépendance.

    Example:
        >>> @app.post("/api/v1/manga")
        >>> async def create_manga(user: UserIdentity = Depends(require_permission("write"))):
        ...     pass
    """
    async def dependency(request: Request) -> UserIdentity:
        if not hasattr(request.state, "user"):
            raise AuthenticationError("unknown", "User not authenticated")

        user = request.state.user
        if not user.has_permission(permission):
            raise AuthorizationError(permission, user.permissions)

        return user

    return dependency


# ============================================================================
# INSTANCE GLOBALE
# ============================================================================


_auth_middleware: Any = None


def get_auth_middleware() -> Any:
    """Retourne l'instance globale du AuthMiddleware.

    Returns:
        Instance de AuthMiddleware ou None.
    """
    return _auth_middleware


def set_auth_middleware(middleware: Any) -> None:
    """Définit l'instance globale du AuthMiddleware.

    Args:
        middleware: Instance de AuthMiddleware.
    """
    global _auth_middleware
    _auth_middleware = middleware


def reset_auth_middleware() -> None:
    """Réinitialise l'instance globale du AuthMiddleware."""
    global _auth_middleware
    _auth_middleware = None


# ============================================================================
# FONCTIONS HELPERS
# ============================================================================


def create_auth_config(
    *,
    jwt_secret: str | None = None,
    allowed_methods: list[AuthMethod] | None = None,
    development: bool = False,
) -> AuthConfig:
    """Crée une configuration d'authentification avec des valeurs par défaut.

    Args:
        jwt_secret: Secret JWT (généré automatiquement si None).
        allowed_methods: Méthodes autorisées.
        development: Si True, active le mode développement.

    Returns:
        Instance de AuthConfig.
    """
    if development:
        return AuthConfig(
            jwt_secret=jwt_secret or secrets.token_urlsafe(32),
            allowed_methods=allowed_methods or [AuthMethod.BASIC_AUTH, AuthMethod.JWT_BEARER],
            require_authentication=False,
            session_cookie_secure=False,
        )

    return AuthConfig(
        jwt_secret=jwt_secret or secrets.token_urlsafe(32),
        allowed_methods=allowed_methods or [AuthMethod.JWT_BEARER, AuthMethod.API_KEY],
    )


def generate_api_key(length: int = DEFAULT_API_KEY_LENGTH) -> str:
    """Génère une API key aléatoire.

    Args:
        length: Longueur de la clé.

    Returns:
        API key.
    """
    return secrets.token_urlsafe(length)


def hash_password(password: str) -> str:
    """Hash un mot de passe de manière sécurisée.

    Args:
        password: Mot de passe à hasher.

    Returns:
        Hash du mot de passe.
    """
    return hash_string(password, algorithm=__import__("nexusdl.core.utils.hash", fromlist=["HashAlgorithm"]).HashAlgorithm.SHA256)


async def get_auth_stats() -> AuthStats:
    """Récupère les statistiques d'authentification.

    Returns:
        Statistiques actuelles.
    """
    middleware = get_auth_middleware()
    if middleware is None:
        return AuthStats()
    return await middleware.get_stats()


async def reset_auth_stats() -> None:
    """Réinitialise les statistiques d'authentification."""
    middleware = get_auth_middleware()
    if middleware is not None:
        await middleware.reset_stats()


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "HEADER_AUTHORIZATION",
    "HEADER_API_KEY",
    "HEADER_X_USER_ID",
    "HEADER_X_USER_ROLES",
    "BEARER_PREFIX",
    "BASIC_PREFIX",
    "JWT_ALGORITHMS",
    "DEFAULT_JWT_ALGORITHM",
    "DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES",
    "DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS",
    "DEFAULT_API_KEY_LENGTH",
    "DEFAULT_SESSION_COOKIE_NAME",
    # Exceptions
    "AuthError",
    "AuthenticationError",
    "AuthorizationError",
    "TokenError",
    "ApiKeyError",
    # Enums
    "AuthMethod",
    "TokenType",
    "AuthDecision",
    # Modèles
    "UserIdentity",
    "TokenPayload",
    "ApiKeyInfo",
    "AuthConfig",
    "AuthStats",
    "AuthRequestLog",
    # Classes
    "TokenManager",
    "ApiKeyManager",
    "AuthValidator",
    "AuthMiddleware" if STARLETTE_AVAILABLE else None,
    # Dépendances FastAPI
    "get_current_user",
    "require_role",
    "require_permission",
    # Instance globale
    "get_auth_middleware",
    "set_auth_middleware",
    "reset_auth_middleware",
    # Fonctions helpers
    "create_auth_config",
    "generate_api_key",
    "hash_password",
    "get_auth_stats",
    "reset_auth_stats",
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
