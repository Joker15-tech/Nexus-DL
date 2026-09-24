"""Routeur FastAPI pour l'authentification et la gestion des utilisateurs.

Ce module fournit un routeur FastAPI complet pour gérer l'authentification,
les tokens JWT, les API keys, les sessions, et les profils utilisateur.
Il s'intègre avec le middleware d'authentification pour fournir une API
cohérente et sécurisée.

**Endpoints** :
    - POST /auth/login                    : Connexion (username/password)
    - POST /auth/register                 : Inscription
    - POST /auth/logout                   : Déconnexion
    - POST /auth/refresh                  : Rafraîchir le token JWT
    - POST /auth/revoke                   : Révoquer un token
    - POST /auth/api-keys                 : Créer une API key
    - GET /auth/api-keys                  : Lister les API keys
    - DELETE /auth/api-keys/{key_id}      : Révoquer une API key
    - GET /auth/me                        : Profil utilisateur courant
    - PATCH /auth/me                      : Mettre à jour le profil
    - POST /auth/change-password          : Changer le mot de passe
    - POST /auth/forgot-password          : Demande de réinitialisation
    - POST /auth/reset-password           : Réinitialiser le mot de passe
    - GET /auth/sessions                  : Lister les sessions actives
    - DELETE /auth/sessions/{session_id}  : Révoquer une session
    - DELETE /auth/sessions               : Révoquer toutes les sessions

**Fonctionnalités** :
    - Authentification par username/password avec JWT
    - Refresh tokens avec rotation automatique
    - Gestion des API keys avec permissions granulaires
    - Sessions multiples par utilisateur
    - Hashing sécurisé des mots de passe (bcrypt)
    - Réinitialisation de mot de passe par email
    - Validation stricte des entrées
    - Rate limiting sur les endpoints sensibles
    - Logging structuré des tentatives d'authentification
    - Événements EventBus pour monitoring
    - Intégration avec le middleware auth
    - Traductions i18n

**Exemple d'utilisation** :
    >>> from fastapi import FastAPI
    >>> from nexusdl.interfaces.web.backend.routers.auth import auth_router
    >>>
    >>> app = FastAPI()
    >>> app.include_router(auth_router, prefix="/api/v1")

**Exemples d'appels API** :
    >>> # Connexion
    >>> POST /api/v1/auth/login
    >>> {"username": "admin", "password": "secret"}
    >>> → {"access_token": "eyJ...", "refresh_token": "eyJ...", "expires_in": 1800}
    >>>
    >>> # Rafraîchir le token
    >>> POST /api/v1/auth/refresh
    >>> {"refresh_token": "eyJ..."}
    >>>
    >>> # Créer une API key
    >>> POST /api/v1/auth/api-keys
    >>> {"name": "My Integration", "permissions": ["read", "write"]}
    >>>
    >>> # Profil utilisateur
    >>> GET /api/v1/auth/me
    >>> Authorization: Bearer eyJ...

Intégration :
    - interfaces/web/backend/middleware/auth.py : TokenManager, ApiKeyManager
    - core/utils/hash.py                        : Hashing des mots de passe
    - core/events.py                            : EventBus pour monitoring
    - core/logger.py                            : Logs
    - core/i18n.py                              : Traductions
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Final

from loguru import logger
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

try:
    import bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:
    BCRYPT_AVAILABLE = False

from nexusdl.core.constants import APP_NAME
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t


# ============================================================================
# CONSTANTES
# ============================================================================


# Valeurs par défaut
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES: Final[int] = 30
DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS: Final[int] = 7
DEFAULT_SESSION_EXPIRE_HOURS: Final[int] = 24
DEFAULT_API_KEY_EXPIRE_DAYS: Final[int] = 90
DEFAULT_RESET_TOKEN_EXPIRE_MINUTES: Final[int] = 30

# Limites
MIN_PASSWORD_LENGTH: Final[int] = 8
MAX_PASSWORD_LENGTH: Final[int] = 128
MIN_USERNAME_LENGTH: Final[int] = 3
MAX_USERNAME_LENGTH: Final[int] = 50
MAX_SESSIONS_PER_USER: Final[int] = 10
MAX_API_KEYS_PER_USER: Final[int] = 20
MAX_FAILED_ATTEMPTS: Final[int] = 5
LOCKOUT_DURATION_MINUTES: Final[int] = 15

# Permissions disponibles
AVAILABLE_PERMISSIONS: Final[frozenset[str]] = frozenset({
    "read",
    "write",
    "delete",
    "admin",
    "download",
    "upload",
    "manage_users",
    "manage_settings",
})

# Rôles disponibles
AVAILABLE_ROLES: Final[frozenset[str]] = frozenset({
    "user",
    "moderator",
    "admin",
    "superadmin",
})


# ============================================================================
# EXCEPTIONS
# ============================================================================


class AuthRouterError(NexusDLError):
    """Exception de base pour les erreurs du routeur auth."""


class AuthenticationFailedError(AuthRouterError):
    """Exception levée lorsqu'une authentification échoue.

    Attributes:
        reason: Raison de l'échec.
    """

    def __init__(self, reason: str = "") -> None:
        msg = t("auth.error.authentication_failed", default="Authentication failed")
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


class UserNotFoundError(AuthRouterError):
    """Exception levée lorsqu'un utilisateur n'est pas trouvé.

    Attributes:
        username: Nom d'utilisateur.
    """

    def __init__(self, username: str) -> None:
        super().__init__(
            t("auth.error.user_not_found", default="User not found: {username}", username=username)
        )
        self.username = username


class UserAlreadyExistsError(AuthRouterError):
    """Exception levée lorsqu'un utilisateur existe déjà.

    Attributes:
        username: Nom d'utilisateur.
    """

    def __init__(self, username: str) -> None:
        super().__init__(
            t("auth.error.user_already_exists", default="User already exists: {username}", username=username)
        )
        self.username = username


class InvalidTokenError(AuthRouterError):
    """Exception levée lorsqu'un token est invalide.

    Attributes:
        token_type: Type de token.
        reason: Raison de l'invalidité.
    """

    def __init__(self, token_type: str, reason: str = "") -> None:
        msg = f"Token invalide ({token_type})"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.token_type = token_type
        self.reason = reason


class AccountLockedError(AuthRouterError):
    """Exception levée lorsqu'un compte est verrouillé.

    Attributes:
        username: Nom d'utilisateur.
        unlock_at: Timestamp de déblocage.
    """

    def __init__(self, username: str, unlock_at: datetime) -> None:
        super().__init__(
            t(
                "auth.error.account_locked",
                default="Account locked. Try again at {unlock_at}",
                unlock_at=unlock_at.isoformat(),
            )
        )
        self.username = username
        self.unlock_at = unlock_at


class WeakPasswordError(AuthRouterError):
    """Exception levée lorsqu'un mot de passe est trop faible.

    Attributes:
        reason: Raison de la faiblesse.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(
            t("auth.error.weak_password", default="Password too weak: {reason}", reason=reason)
        )
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class TokenType(str, Enum):
    """Type de token.

    Attributes:
        ACCESS: Token d'accès (court terme).
        REFRESH: Token de refresh (long terme).
        RESET: Token de réinitialisation de mot de passe.
    """

    ACCESS = "access"
    REFRESH = "refresh"
    RESET = "reset"


class SessionStatus(str, Enum):
    """Statut d'une session.

    Attributes:
        ACTIVE: Session active.
        EXPIRED: Session expirée.
        REVOKED: Session révoquée.
    """

    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ApiKeyStatus(str, Enum):
    """Statut d'une API key.

    Attributes:
        ACTIVE: Clé active.
        EXPIRED: Clé expirée.
        REVOKED: Clé révoquée.
    """

    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


# ============================================================================
# MODÈLES DE DONNÉES — Stockage
# ============================================================================


class StoredUser(BaseModel):
    """Utilisateur stocké.

    Attributes:
        user_id: ID unique.
        username: Nom d'utilisateur.
        email: Email.
        password_hash: Hash du mot de passe.
        roles: Rôles de l'utilisateur.
        permissions: Permissions de l'utilisateur.
        is_active: Si l'utilisateur est actif.
        created_at: Date de création.
        updated_at: Dernière modification.
        last_login_at: Dernière connexion.
        failed_attempts: Nombre de tentatives échouées.
        locked_until: Verrouillé jusqu'à (si applicable).
        metadata: Métadonnées additionnelles.
    """

    user_id: str = Field(..., description="ID unique.")
    username: str = Field(..., description="Nom d'utilisateur.")
    email: str = Field(default="", description="Email.")
    password_hash: str = Field(..., description="Hash mot de passe.")
    roles: list[str] = Field(default_factory=lambda: ["user"], description="Rôles.")
    permissions: list[str] = Field(default_factory=lambda: ["read"], description="Permissions.")
    is_active: bool = Field(default=True, description="Actif.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Création.")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="MAJ.")
    last_login_at: datetime | None = Field(default=None, description="Dernière connexion.")
    failed_attempts: int = Field(default=0, ge=0, description="Tentatives échouées.")
    locked_until: datetime | None = Field(default=None, description="Verrouillé jusqu'à.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Métadonnées.")

    model_config = ConfigDict(extra="forbid")


class StoredSession(BaseModel):
    """Session stockée.

    Attributes:
        session_id: ID unique.
        user_id: ID de l'utilisateur.
        created_at: Date de création.
        expires_at: Date d'expiration.
        ip_address: Adresse IP.
        user_agent: User-Agent.
        is_active: Si la session est active.
    """

    session_id: str = Field(..., description="ID unique.")
    user_id: str = Field(..., description="ID utilisateur.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Création.")
    expires_at: datetime = Field(..., description="Expiration.")
    ip_address: str = Field(default="", description="IP.")
    user_agent: str = Field(default="", description="User-Agent.")
    is_active: bool = Field(default=True, description="Active.")

    model_config = ConfigDict(extra="forbid")

    @property
    def is_expired(self) -> bool:
        """Vérifie si la session est expirée."""
        return datetime.now(UTC) > self.expires_at


class StoredApiKey(BaseModel):
    """API key stockée.

    Attributes:
        key_id: ID unique.
        user_id: ID de l'utilisateur.
        name: Nom descriptif.
        key_hash: Hash de la clé.
        key_prefix: Préfixe de la clé (pour identification).
        permissions: Permissions associées.
        created_at: Date de création.
        expires_at: Date d'expiration.
        last_used_at: Dernière utilisation.
        is_active: Si la clé est active.
    """

    key_id: str = Field(..., description="ID unique.")
    user_id: str = Field(..., description="ID utilisateur.")
    name: str = Field(default="", description="Nom.")
    key_hash: str = Field(..., description="Hash.")
    key_prefix: str = Field(..., description="Préfixe.")
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


class PasswordResetToken(BaseModel):
    """Token de réinitialisation de mot de passe.

    Attributes:
        token: Token unique.
        user_id: ID de l'utilisateur.
        created_at: Date de création.
        expires_at: Date d'expiration.
        used: Si le token a été utilisé.
    """

    token: str = Field(..., description="Token.")
    user_id: str = Field(..., description="ID utilisateur.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Création.")
    expires_at: datetime = Field(..., description="Expiration.")
    used: bool = Field(default=False, description="Utilisé.")

    model_config = ConfigDict(extra="forbid")

    @property
    def is_expired(self) -> bool:
        """Vérifie si le token est expiré."""
        return datetime.now(UTC) > self.expires_at


# ============================================================================
# MODÈLES DE REQUÊTE — Pydantic
# ============================================================================


class LoginRequest(BaseModel):
    """Requête de connexion.

    Attributes:
        username: Nom d'utilisateur.
        password: Mot de passe.
    """

    username: str = Field(..., min_length=MIN_USERNAME_LENGTH, max_length=MAX_USERNAME_LENGTH, description="Nom d'utilisateur.")
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH, description="Mot de passe.")


class RegisterRequest(BaseModel):
    """Requête d'inscription.

    Attributes:
        username: Nom d'utilisateur.
        email: Email.
        password: Mot de passe.
        password_confirm: Confirmation du mot de passe.
    """

    username: str = Field(..., min_length=MIN_USERNAME_LENGTH, max_length=MAX_USERNAME_LENGTH, description="Nom d'utilisateur.")
    email: str = Field(..., description="Email.")
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH, description="Mot de passe.")
    password_confirm: str = Field(..., description="Confirmation.")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        """Valide l'email."""
        if "@" not in v or "." not in v:
            raise ValueError("Email invalide")
        return v.lower()

    @field_validator("password_confirm")
    @classmethod
    def validate_password_match(cls, v: str, info: Any) -> str:
        """Valide que les mots de passe correspondent."""
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("Les mots de passe ne correspondent pas")
        return v


class RefreshTokenRequest(BaseModel):
    """Requête de rafraîchissement de token.

    Attributes:
        refresh_token: Token de refresh.
    """

    refresh_token: str = Field(..., min_length=10, description="Token de refresh.")


class RevokeTokenRequest(BaseModel):
    """Requête de révocation de token.

    Attributes:
        token: Token à révoquer.
    """

    token: str = Field(..., min_length=10, description="Token à révoquer.")


class CreateApiKeyRequest(BaseModel):
    """Requête de création d'API key.

    Attributes:
        name: Nom descriptif.
        permissions: Permissions associées.
        expires_days: Durée de validité en jours (None = jamais).
    """

    name: str = Field(..., min_length=1, max_length=100, description="Nom.")
    permissions: list[str] = Field(default_factory=list, description="Permissions.")
    expires_days: int | None = Field(default=None, ge=1, le=365, description="Durée (jours).")

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, v: list[str]) -> list[str]:
        """Valide les permissions."""
        invalid = [p for p in v if p not in AVAILABLE_PERMISSIONS]
        if invalid:
            raise ValueError(f"Permissions invalides: {invalid}")
        return v


class UpdateProfileRequest(BaseModel):
    """Requête de mise à jour du profil.

    Attributes:
        email: Nouvel email.
        metadata: Métadonnées à mettre à jour.
    """

    email: str | None = Field(default=None, description="Email.")
    metadata: dict[str, Any] | None = Field(default=None, description="Métadonnées.")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        """Valide l'email."""
        if v is not None:
            if "@" not in v or "." not in v:
                raise ValueError("Email invalide")
            return v.lower()
        return v


class ChangePasswordRequest(BaseModel):
    """Requête de changement de mot de passe.

    Attributes:
        current_password: Mot de passe actuel.
        new_password: Nouveau mot de passe.
        new_password_confirm: Confirmation.
    """

    current_password: str = Field(..., description="Mot de passe actuel.")
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH, description="Nouveau mot de passe.")
    new_password_confirm: str = Field(..., description="Confirmation.")

    @field_validator("new_password_confirm")
    @classmethod
    def validate_password_match(cls, v: str, info: Any) -> str:
        """Valide que les mots de passe correspondent."""
        if "new_password" in info.data and v != info.data["new_password"]:
            raise ValueError("Les mots de passe ne correspondent pas")
        return v


class ForgotPasswordRequest(BaseModel):
    """Requête de mot de passe oublié.

    Attributes:
        email: Email de l'utilisateur.
    """

    email: str = Field(..., description="Email.")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        """Valide l'email."""
        if "@" not in v or "." not in v:
            raise ValueError("Email invalide")
        return v.lower()


class ResetPasswordRequest(BaseModel):
    """Requête de réinitialisation de mot de passe.

    Attributes:
        token: Token de réinitialisation.
        new_password: Nouveau mot de passe.
        new_password_confirm: Confirmation.
    """

    token: str = Field(..., min_length=10, description="Token.")
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH, description="Nouveau mot de passe.")
    new_password_confirm: str = Field(..., description="Confirmation.")

    @field_validator("new_password_confirm")
    @classmethod
    def validate_password_match(cls, v: str, info: Any) -> str:
        """Valide que les mots de passe correspondent."""
        if "new_password" in info.data and v != info.data["new_password"]:
            raise ValueError("Les mots de passe ne correspondent pas")
        return v


# ============================================================================
# MODÈLES DE RÉPONSE — Pydantic
# ============================================================================


class LoginResponse(BaseModel):
    """Réponse de connexion.

    Attributes:
        access_token: Token d'accès JWT.
        refresh_token: Token de refresh JWT.
        token_type: Type de token (bearer).
        expires_in: Durée de vie en secondes.
        user_id: ID de l'utilisateur.
        username: Nom d'utilisateur.
        roles: Rôles de l'utilisateur.
        session_id: ID de la session créée.
    """

    access_token: str = Field(..., description="Token d'accès.")
    refresh_token: str = Field(..., description="Token de refresh.")
    token_type: str = Field(default="bearer", description="Type.")
    expires_in: int = Field(..., ge=0, description="Durée vie (s).")
    user_id: str = Field(..., description="ID utilisateur.")
    username: str = Field(..., description="Nom d'utilisateur.")
    roles: list[str] = Field(default_factory=list, description="Rôles.")
    session_id: str = Field(..., description="ID session.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class RegisterResponse(BaseModel):
    """Réponse d'inscription.

    Attributes:
        user_id: ID de l'utilisateur créé.
        username: Nom d'utilisateur.
        email: Email.
        message: Message de confirmation.
    """

    user_id: str = Field(..., description="ID utilisateur.")
    username: str = Field(..., description="Nom d'utilisateur.")
    email: str = Field(default="", description="Email.")
    message: str = Field(..., description="Message.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class RefreshTokenResponse(BaseModel):
    """Réponse de rafraîchissement de token.

    Attributes:
        access_token: Nouveau token d'accès.
        refresh_token: Nouveau token de refresh.
        token_type: Type de token.
        expires_in: Durée de vie en secondes.
    """

    access_token: str = Field(..., description="Nouveau token d'accès.")
    refresh_token: str = Field(..., description="Nouveau token de refresh.")
    token_type: str = Field(default="bearer", description="Type.")
    expires_in: int = Field(..., ge=0, description="Durée vie (s).")

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
        last_login_at: Dernière connexion.
        metadata: Métadonnées.
    """

    user_id: str = Field(..., description="ID unique.")
    username: str = Field(..., description="Nom d'utilisateur.")
    email: str = Field(default="", description="Email.")
    roles: list[str] = Field(default_factory=list, description="Rôles.")
    permissions: list[str] = Field(default_factory=list, description="Permissions.")
    is_active: bool = Field(default=True, description="Actif.")
    created_at: datetime = Field(..., description="Création.")
    last_login_at: datetime | None = Field(default=None, description="Dernière connexion.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Métadonnées.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ApiKeyResponse(BaseModel):
    """Réponse avec une API key.

    Attributes:
        key_id: ID unique.
        name: Nom.
        key_prefix: Préfixe (pour identification).
        api_key: Clé complète (seulement à la création).
        permissions: Permissions.
        created_at: Date de création.
        expires_at: Date d'expiration.
        last_used_at: Dernière utilisation.
        status: Statut.
    """

    key_id: str = Field(..., description="ID unique.")
    name: str = Field(..., description="Nom.")
    key_prefix: str = Field(..., description="Préfixe.")
    api_key: str | None = Field(default=None, description="Clé complète.")
    permissions: list[str] = Field(default_factory=list, description="Permissions.")
    created_at: datetime = Field(..., description="Création.")
    expires_at: datetime | None = Field(default=None, description="Expiration.")
    last_used_at: datetime | None = Field(default=None, description="Dernière utilisation.")
    status: ApiKeyStatus = Field(..., description="Statut.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ApiKeyListResponse(BaseModel):
    """Réponse avec la liste des API keys.

    Attributes:
        api_keys: Liste des clés.
        total: Nombre total.
    """

    api_keys: list[ApiKeyResponse] = Field(default_factory=list, description="Clés.")
    total: int = Field(default=0, ge=0, description="Total.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class SessionResponse(BaseModel):
    """Réponse avec une session.

    Attributes:
        session_id: ID unique.
        created_at: Date de création.
        expires_at: Date d'expiration.
        ip_address: Adresse IP.
        user_agent: User-Agent.
        is_current: Si c'est la session courante.
        status: Statut.
    """

    session_id: str = Field(..., description="ID unique.")
    created_at: datetime = Field(..., description="Création.")
    expires_at: datetime = Field(..., description="Expiration.")
    ip_address: str = Field(default="", description="IP.")
    user_agent: str = Field(default="", description="User-Agent.")
    is_current: bool = Field(default=False, description="Courante.")
    status: SessionStatus = Field(..., description="Statut.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class SessionListResponse(BaseModel):
    """Réponse avec la liste des sessions.

    Attributes:
        sessions: Liste des sessions.
        total: Nombre total.
    """

    sessions: list[SessionResponse] = Field(default_factory=list, description="Sessions.")
    total: int = Field(default=0, ge=0, description="Total.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ActionResponse(BaseModel):
    """Réponse après une action.

    Attributes:
        success: Si l'action a réussi.
        message: Message de confirmation.
    """

    success: bool = Field(..., description="Succès.")
    message: str = Field(..., description="Message.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ErrorResponse(BaseModel):
    """Réponse d'erreur.

    Attributes:
        error: Code d'erreur.
        message: Message d'erreur.
        details: Détails additionnels.
    """

    error: str = Field(..., description="Code erreur.")
    message: str = Field(..., description="Message.")
    details: dict[str, Any] = Field(default_factory=dict, description="Détails.")

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# AUTH STORE — Abstraction du stockage
# ============================================================================


class AuthStore:
    """Abstraction pour le stockage des utilisateurs, sessions, et API keys.

    Cette classe définit l'interface pour le stockage persistant.
    Les implémentations concrètes peuvent utiliser SQLite, PostgreSQL, etc.
    """

    async def get_user_by_username(self, username: str) -> StoredUser | None:
        """Récupère un utilisateur par son nom."""
        raise NotImplementedError

    async def get_user_by_email(self, email: str) -> StoredUser | None:
        """Récupère un utilisateur par son email."""
        raise NotImplementedError

    async def get_user_by_id(self, user_id: str) -> StoredUser | None:
        """Récupère un utilisateur par son ID."""
        raise NotImplementedError

    async def create_user(self, user: StoredUser) -> StoredUser:
        """Crée un nouvel utilisateur."""
        raise NotImplementedError

    async def update_user(self, user: StoredUser) -> StoredUser:
        """Met à jour un utilisateur."""
        raise NotImplementedError

    async def create_session(self, session: StoredSession) -> StoredSession:
        """Crée une nouvelle session."""
        raise NotImplementedError

    async def get_session(self, session_id: str) -> StoredSession | None:
        """Récupère une session."""
        raise NotImplementedError

    async def get_user_sessions(self, user_id: str) -> list[StoredSession]:
        """Récupère toutes les sessions d'un utilisateur."""
        raise NotImplementedError

    async def revoke_session(self, session_id: str) -> bool:
        """Révoque une session."""
        raise NotImplementedError

    async def revoke_all_user_sessions(self, user_id: str, except_session_id: str | None = None) -> int:
        """Révoque toutes les sessions d'un utilisateur."""
        raise NotImplementedError

    async def create_api_key(self, api_key: StoredApiKey) -> StoredApiKey:
        """Crée une nouvelle API key."""
        raise NotImplementedError

    async def get_api_key_by_hash(self, key_hash: str) -> StoredApiKey | None:
        """Récupère une API key par son hash."""
        raise NotImplementedError

    async def get_user_api_keys(self, user_id: str) -> list[StoredApiKey]:
        """Récupère toutes les API keys d'un utilisateur."""
        raise NotImplementedError

    async def revoke_api_key(self, key_id: str) -> bool:
        """Révoque une API key."""
        raise NotImplementedError

    async def update_api_key_last_used(self, key_id: str) -> None:
        """Met à jour la dernière utilisation d'une API key."""
        raise NotImplementedError

    async def create_reset_token(self, token: PasswordResetToken) -> PasswordResetToken:
        """Crée un token de réinitialisation."""
        raise NotImplementedError

    async def get_reset_token(self, token: str) -> PasswordResetToken | None:
        """Récupère un token de réinitialisation."""
        raise NotImplementedError

    async def mark_reset_token_used(self, token: str) -> bool:
        """Marque un token de réinitialisation comme utilisé."""
        raise NotImplementedError


# ============================================================================
# IN-MEMORY AUTH STORE — Implémentation pour développement
# ============================================================================


class InMemoryAuthStore(AuthStore):
    """Implémentation en mémoire du AuthStore.

    Pour développement et tests uniquement. Les données sont perdues au redémarrage.
    """

    def __init__(self) -> None:
        """Initialise le store."""
        self._users: dict[str, StoredUser] = {}  # user_id -> user
        self._users_by_username: dict[str, str] = {}  # username -> user_id
        self._users_by_email: dict[str, str] = {}  # email -> user_id
        self._sessions: dict[str, StoredSession] = {}  # session_id -> session
        self._api_keys: dict[str, StoredApiKey] = {}  # key_id -> api_key
        self._api_keys_by_hash: dict[str, str] = {}  # key_hash -> key_id
        self._reset_tokens: dict[str, PasswordResetToken] = {}  # token -> token_data
        self._lock = asyncio.Lock()

    async def get_user_by_username(self, username: str) -> StoredUser | None:
        """Récupère un utilisateur par son nom."""
        async with self._lock:
            user_id = self._users_by_username.get(username.lower())
            if user_id:
                return self._users.get(user_id)
            return None

    async def get_user_by_email(self, email: str) -> StoredUser | None:
        """Récupère un utilisateur par son email."""
        async with self._lock:
            user_id = self._users_by_email.get(email.lower())
            if user_id:
                return self._users.get(user_id)
            return None

    async def get_user_by_id(self, user_id: str) -> StoredUser | None:
        """Récupère un utilisateur par son ID."""
        async with self._lock:
            return self._users.get(user_id)

    async def create_user(self, user: StoredUser) -> StoredUser:
        """Crée un nouvel utilisateur."""
        async with self._lock:
            if user.username.lower() in self._users_by_username:
                raise UserAlreadyExistsError(user.username)
            if user.email and user.email.lower() in self._users_by_email:
                raise UserAlreadyExistsError(user.email)

            self._users[user.user_id] = user
            self._users_by_username[user.username.lower()] = user.user_id
            if user.email:
                self._users_by_email[user.email.lower()] = user.user_id
            return user

    async def update_user(self, user: StoredUser) -> StoredUser:
        """Met à jour un utilisateur."""
        async with self._lock:
            if user.user_id not in self._users:
                raise UserNotFoundError(user.username)
            user.updated_at = datetime.now(UTC)
            self._users[user.user_id] = user
            return user

    async def create_session(self, session: StoredSession) -> StoredSession:
        """Crée une nouvelle session."""
        async with self._lock:
            self._sessions[session.session_id] = session
            return session

    async def get_session(self, session_id: str) -> StoredSession | None:
        """Récupère une session."""
        async with self._lock:
            return self._sessions.get(session_id)

    async def get_user_sessions(self, user_id: str) -> list[StoredSession]:
        """Récupère toutes les sessions d'un utilisateur."""
        async with self._lock:
            return [s for s in self._sessions.values() if s.user_id == user_id]

    async def revoke_session(self, session_id: str) -> bool:
        """Révoque une session."""
        async with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].is_active = False
                return True
            return False

    async def revoke_all_user_sessions(self, user_id: str, except_session_id: str | None = None) -> int:
        """Révoque toutes les sessions d'un utilisateur."""
        async with self._lock:
            count = 0
            for session in self._sessions.values():
                if session.user_id == user_id and session.session_id != except_session_id:
                    session.is_active = False
                    count += 1
            return count

    async def create_api_key(self, api_key: StoredApiKey) -> StoredApiKey:
        """Crée une nouvelle API key."""
        async with self._lock:
            self._api_keys[api_key.key_id] = api_key
            self._api_keys_by_hash[api_key.key_hash] = api_key.key_id
            return api_key

    async def get_api_key_by_hash(self, key_hash: str) -> StoredApiKey | None:
        """Récupère une API key par son hash."""
        async with self._lock:
            key_id = self._api_keys_by_hash.get(key_hash)
            if key_id:
                return self._api_keys.get(key_id)
            return None

    async def get_user_api_keys(self, user_id: str) -> list[StoredApiKey]:
        """Récupère toutes les API keys d'un utilisateur."""
        async with self._lock:
            return [k for k in self._api_keys.values() if k.user_id == user_id]

    async def revoke_api_key(self, key_id: str) -> bool:
        """Révoque une API key."""
        async with self._lock:
            if key_id in self._api_keys:
                self._api_keys[key_id].is_active = False
                return True
            return False

    async def update_api_key_last_used(self, key_id: str) -> None:
        """Met à jour la dernière utilisation d'une API key."""
        async with self._lock:
            if key_id in self._api_keys:
                self._api_keys[key_id].last_used_at = datetime.now(UTC)

    async def create_reset_token(self, token: PasswordResetToken) -> PasswordResetToken:
        """Crée un token de réinitialisation."""
        async with self._lock:
            self._reset_tokens[token.token] = token
            return token

    async def get_reset_token(self, token: str) -> PasswordResetToken | None:
        """Récupère un token de réinitialisation."""
        async with self._lock:
            return self._reset_tokens.get(token)

    async def mark_reset_token_used(self, token: str) -> bool:
        """Marque un token de réinitialisation comme utilisé."""
        async with self._lock:
            if token in self._reset_tokens:
                self._reset_tokens[token].used = True
                return True
            return False


# Instance globale du store
_auth_store: AuthStore | None = None


def get_auth_store() -> AuthStore:
    """Retourne l'instance globale du AuthStore.

    Crée un InMemoryAuthStore si aucun store n'est configuré.

    Returns:
        Instance de AuthStore.
    """
    global _auth_store
    if _auth_store is None:
        _auth_store = InMemoryAuthStore()
    return _auth_store


def set_auth_store(store: AuthStore) -> None:
    """Définit l'instance globale du AuthStore.

    Args:
        store: Instance de AuthStore.
    """
    global _auth_store
    _auth_store = store


# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================


def _hash_password(password: str) -> str:
    """Hash un mot de passe avec bcrypt.

    Args:
        password: Mot de passe en clair.

    Returns:
        Hash du mot de passe.

    Raises:
        AuthRouterError: Si bcrypt n'est pas disponible.
    """
    if not BCRYPT_AVAILABLE:
        # Fallback sur SHA-256 (moins sécurisé, pour développement uniquement)
        logger.warning("bcrypt non disponible, utilisation de SHA-256 (non sécurisé)")
        return hashlib.sha256(password.encode()).hexdigest()

    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode(), salt).decode()


def _verify_password(password: str, password_hash: str) -> bool:
    """Vérifie un mot de passe contre son hash.

    Args:
        password: Mot de passe en clair.
        password_hash: Hash du mot de passe.

    Returns:
        True si le mot de passe correspond.
    """
    if not BCRYPT_AVAILABLE:
        # Fallback sur SHA-256
        return hashlib.sha256(password.encode()).hexdigest() == password_hash

    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except Exception:
        return False


def _validate_password_strength(password: str) -> None:
    """Valide la force d'un mot de passe.

    Args:
        password: Mot de passe à valider.

    Raises:
        WeakPasswordError: Si le mot de passe est trop faible.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(f"Minimum {MIN_PASSWORD_LENGTH} caractères")

    # Vérifier la complexité
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)

    if not (has_upper and has_lower and has_digit):
        raise WeakPasswordError("Doit contenir au moins une majuscule, une minuscule et un chiffre")


def _generate_token(length: int = 32) -> str:
    """Génère un token aléatoire sécurisé.

    Args:
        length: Longueur du token.

    Returns:
        Token aléatoire.
    """
    return secrets.token_urlsafe(length)


def _get_client_ip(request: Any) -> str:
    """Extrait l'IP du client.

    Args:
        request: Requête HTTP.

    Returns:
        Adresse IP.
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _get_user_agent(request: Any) -> str:
    """Extrait le User-Agent.

    Args:
        request: Requête HTTP.

    Returns:
        User-Agent.
    """
    return request.headers.get("User-Agent", "")


async def _emit_auth_event(
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Émet un événement d'authentification.

    Args:
        event_type: Type d'événement.
        payload: Données de l'événement.
    """
    try:
        event_bus = get_event_bus()
        await event_bus.emit(
            EventType.CUSTOM,
            payload={
                "type": event_type,
                **payload,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            source="interfaces.web.auth",
        )
    except Exception as e:
        logger.debug("Impossible d'émettre l'événement: {}", e)


def _api_key_to_response(api_key: StoredApiKey, include_full_key: bool = False) -> ApiKeyResponse:
    """Convertit une StoredApiKey en ApiKeyResponse.

    Args:
        api_key: API key stockée.
        include_full_key: Inclure la clé complète.

    Returns:
        ApiKeyResponse.
    """
    if api_key.is_expired:
        status_val = ApiKeyStatus.EXPIRED
    elif not api_key.is_active:
        status_val = ApiKeyStatus.REVOKED
    else:
        status_val = ApiKeyStatus.ACTIVE

    return ApiKeyResponse(
        key_id=api_key.key_id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        api_key=None,  # Ne jamais exposer la clé complète sauf à la création
        permissions=api_key.permissions,
        created_at=api_key.created_at,
        expires_at=api_key.expires_at,
        last_used_at=api_key.last_used_at,
        status=status_val,
    )


def _session_to_response(session: StoredSession, current_session_id: str | None = None) -> SessionResponse:
    """Convertit une StoredSession en SessionResponse.

    Args:
        session: Session stockée.
        current_session_id: ID de la session courante.

    Returns:
        SessionResponse.
    """
    if not session.is_active:
        status_val = SessionStatus.REVOKED
    elif session.is_expired:
        status_val = SessionStatus.EXPIRED
    else:
        status_val = SessionStatus.ACTIVE

    return SessionResponse(
        session_id=session.session_id,
        created_at=session.created_at,
        expires_at=session.expires_at,
        ip_address=session.ip_address,
        user_agent=session.user_agent,
        is_current=(session.session_id == current_session_id),
        status=status_val,
    )


# ============================================================================
# ROUTEUR — Endpoints FastAPI
# ============================================================================


if FASTAPI_AVAILABLE:

    auth_router = APIRouter(tags=["auth"])

    # =========================================================================
    # POST /auth/login — Connexion
    # =========================================================================

    @auth_router.post(
        "/auth/login",
        response_model=LoginResponse,
        summary="Connexion",
        description="Authentifie un utilisateur et retourne des tokens JWT.",
        responses={
            200: {"description": "Connexion réussie"},
            401: {"description": "Credentials invalides"},
            423: {"description": "Compte verrouillé"},
        },
    )
    async def login(
        request: Request,
        body: LoginRequest,
    ) -> LoginResponse:
        """Authentifie un utilisateur.

        Args:
            request: Requête HTTP.
            body: Corps de la requête.

        Returns:
            Tokens JWT.
        """
        logger.info("Tentative de connexion: username={}", body.username)

        store = get_auth_store()

        try:
            # Récupérer l'utilisateur
            user = await store.get_user_by_username(body.username)
            if user is None:
                logger.warning("Connexion échouée: utilisateur inconnu {}", body.username)
                await _emit_auth_event("auth.login.failed", {"username": body.username, "reason": "user_not_found"})
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=ErrorResponse(
                        error="invalid_credentials",
                        message=t("auth.error.invalid_credentials", default="Invalid username or password"),
                    ).model_dump(),
                )

            # Vérifier le verrouillage
            if user.locked_until and user.locked_until > datetime.now(UTC):
                logger.warning("Connexion échouée: compte verrouillé {}", body.username)
                raise HTTPException(
                    status_code=status.HTTP_423_LOCKED,
                    detail=ErrorResponse(
                        error="account_locked",
                        message=t("auth.error.account_locked", default="Account locked. Try again later."),
                        details={"unlock_at": user.locked_until.isoformat()},
                    ).model_dump(),
                )

            # Vérifier le mot de passe
            if not _verify_password(body.password, user.password_hash):
                # Incrémenter les tentatives échouées
                user.failed_attempts += 1
                if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
                    user.locked_until = datetime.now(UTC) + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
                    logger.warning("Compte verrouillé après {} tentatives: {}", user.failed_attempts, body.username)

                await store.update_user(user)

                logger.warning("Connexion échouée: mot de passe incorrect {}", body.username)
                await _emit_auth_event("auth.login.failed", {"username": body.username, "reason": "invalid_password"})

                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=ErrorResponse(
                        error="invalid_credentials",
                        message=t("auth.error.invalid_credentials", default="Invalid username or password"),
                    ).model_dump(),
                )

            # Vérifier que le compte est actif
            if not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=ErrorResponse(
                        error="account_inactive",
                        message=t("auth.error.account_inactive", default="Account is inactive"),
                    ).model_dump(),
                )

            # Réinitialiser les tentatives échouées
            user.failed_attempts = 0
            user.locked_until = None
            user.last_login_at = datetime.now(UTC)
            await store.update_user(user)

            # Créer les tokens
            from nexusdl.interfaces.web.backend.middleware.auth import TokenManager, TokenType
            from nexusdl.core.config import get_config

            config = get_config()
            token_manager = TokenManager(config.auth)

            access_token = token_manager.create_access_token(
                user_id=user.user_id,
                roles=user.roles,
                permissions=user.permissions,
            )
            refresh_token = token_manager.create_refresh_token(user_id=user.user_id)

            # Créer une session
            session_id = _generate_token(16)
            session = StoredSession(
                session_id=session_id,
                user_id=user.user_id,
                expires_at=datetime.now(UTC) + timedelta(hours=DEFAULT_SESSION_EXPIRE_HOURS),
                ip_address=_get_client_ip(request),
                user_agent=_get_user_agent(request),
            )
            await store.create_session(session)

            # Limiter le nombre de sessions
            all_sessions = await store.get_user_sessions(user.user_id)
            active_sessions = [s for s in all_sessions if s.is_active and not s.is_expired]
            if len(active_sessions) > MAX_SESSIONS_PER_USER:
                # Révoquer les sessions les plus anciennes
                active_sessions.sort(key=lambda s: s.created_at)
                sessions_to_revoke = active_sessions[:len(active_sessions) - MAX_SESSIONS_PER_USER]
                for old_session in sessions_to_revoke:
                    await store.revoke_session(old_session.session_id)

            logger.info("Connexion réussie: user={}, session={}", user.username, session_id)
            await _emit_auth_event("auth.login.success", {
                "user_id": user.user_id,
                "username": user.username,
                "session_id": session_id,
                "ip_address": session.ip_address,
            })

            return LoginResponse(
                access_token=access_token,
                refresh_token=refresh_token,
                token_type="bearer",
                expires_in=config.auth.access_token_expire_minutes * 60,
                user_id=user.user_id,
                username=user.username,
                roles=user.roles,
                session_id=session_id,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de la connexion: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="login_failed",
                    message=t("auth.error.login_failed", default="Login failed"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /auth/register — Inscription
    # =========================================================================

    @auth_router.post(
        "/auth/register",
        response_model=RegisterResponse,
        summary="Inscription",
        description="Crée un nouvel utilisateur.",
        responses={
            201: {"description": "Utilisateur créé"},
            400: {"description": "Données invalides"},
            409: {"description": "Utilisateur existe déjà"},
        },
        status_code=status.HTTP_201_CREATED,
    )
    async def register(
        request: Request,
        body: RegisterRequest,
    ) -> RegisterResponse:
        """Crée un nouvel utilisateur.

        Args:
            request: Requête HTTP.
            body: Corps de la requête.

        Returns:
            Informations de l'utilisateur créé.
        """
        logger.info("Tentative d'inscription: username={}, email={}", body.username, body.email)

        store = get_auth_store()

        try:
            # Vérifier que l'utilisateur n'existe pas déjà
            existing = await store.get_user_by_username(body.username)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=ErrorResponse(
                        error="user_already_exists",
                        message=t("auth.error.user_already_exists", default="Username already taken"),
                        details={"username": body.username},
                    ).model_dump(),
                )

            existing = await store.get_user_by_email(body.email)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=ErrorResponse(
                        error="email_already_exists",
                        message=t("auth.error.email_already_exists", default="Email already registered"),
                        details={"email": body.email},
                    ).model_dump(),
                )

            # Valider la force du mot de passe
            _validate_password_strength(body.password)

            # Créer l'utilisateur
            import uuid
            user = StoredUser(
                user_id=str(uuid.uuid4()),
                username=body.username,
                email=body.email,
                password_hash=_hash_password(body.password),
                roles=["user"],
                permissions=["read"],
            )

            await store.create_user(user)

            logger.info("Inscription réussie: user={}, email={}", user.username, user.email)
            await _emit_auth_event("auth.register.success", {
                "user_id": user.user_id,
                "username": user.username,
                "email": user.email,
            })

            return RegisterResponse(
                user_id=user.user_id,
                username=user.username,
                email=user.email,
                message=t("auth.success.registered", default="User registered successfully"),
            )

        except HTTPException:
            raise
        except WeakPasswordError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorResponse(
                    error="weak_password",
                    message=str(e),
                ).model_dump(),
            ) from e
        except UserAlreadyExistsError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorResponse(
                    error="user_already_exists",
                    message=str(e),
                ).model_dump(),
            ) from e
        except Exception as e:
            logger.error("Erreur lors de l'inscription: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="register_failed",
                    message=t("auth.error.register_failed", default="Registration failed"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /auth/logout — Déconnexion
    # =========================================================================

    @auth_router.post(
        "/auth/logout",
        response_model=ActionResponse,
        summary="Déconnexion",
        description="Révoque la session courante.",
        responses={
            200: {"description": "Déconnexion réussie"},
        },
    )
    async def logout(
        request: Request,
    ) -> ActionResponse:
        """Déconnecte l'utilisateur.

        Args:
            request: Requête HTTP.

        Returns:
            Confirmation.
        """
        # Récupérer l'utilisateur authentifié
        if not hasattr(request.state, "user"):
            return ActionResponse(
                success=True,
                message=t("auth.success.logged_out", default="Logged out successfully"),
            )

        user = request.state.user
        store = get_auth_store()

        try:
            # Révoquer la session courante si présente
            session_id = request.headers.get("X-Session-ID")
            if session_id:
                await store.revoke_session(session_id)

            logger.info("Déconnexion: user={}", user.user_id)
            await _emit_auth_event("auth.logout.success", {"user_id": user.user_id})

            return ActionResponse(
                success=True,
                message=t("auth.success.logged_out", default="Logged out successfully"),
            )

        except Exception as e:
            logger.error("Erreur lors de la déconnexion: {}", e)
            return ActionResponse(
                success=True,
                message=t("auth.success.logged_out", default="Logged out successfully"),
            )

    # =========================================================================
    # POST /auth/refresh — Rafraîchir le token
    # =========================================================================

    @auth_router.post(
        "/auth/refresh",
        response_model=RefreshTokenResponse,
        summary="Rafraîchir le token",
        description="Échange un refresh token contre de nouveaux tokens.",
        responses={
            200: {"description": "Tokens rafraîchis"},
            401: {"description": "Refresh token invalide"},
        },
    )
    async def refresh_token(
        request: Request,
        body: RefreshTokenRequest,
    ) -> RefreshTokenResponse:
        """Rafraîchit les tokens JWT.

        Args:
            request: Requête HTTP.
            body: Corps de la requête.

        Returns:
            Nouveaux tokens.
        """
        logger.info("Rafraîchissement de token")

        try:
            from nexusdl.interfaces.web.backend.middleware.auth import TokenManager, TokenType
            from nexusdl.core.config import get_config

            config = get_config()
            token_manager = TokenManager(config.auth)

            # Valider le refresh token
            payload = token_manager.validate_token(body.refresh_token, TokenType.REFRESH)

            # Récupérer l'utilisateur
            store = get_auth_store()
            user = await store.get_user_by_id(payload.sub)
            if user is None or not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=ErrorResponse(
                        error="invalid_token",
                        message=t("auth.error.invalid_token", default="Invalid refresh token"),
                    ).model_dump(),
                )

            # Révoquer l'ancien refresh token (rotation)
            await token_manager.revoke_token(body.refresh_token)

            # Créer de nouveaux tokens
            access_token = token_manager.create_access_token(
                user_id=user.user_id,
                roles=user.roles,
                permissions=user.permissions,
            )
            new_refresh_token = token_manager.create_refresh_token(user_id=user.user_id)

            logger.info("Token rafraîchi: user={}", user.username)
            await _emit_auth_event("auth.token.refreshed", {"user_id": user.user_id})

            return RefreshTokenResponse(
                access_token=access_token,
                refresh_token=new_refresh_token,
                token_type="bearer",
                expires_in=config.auth.access_token_expire_minutes * 60,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.warning("Rafraîchissement de token échoué: {}", e)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ErrorResponse(
                    error="invalid_token",
                    message=t("auth.error.invalid_token", default="Invalid refresh token"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /auth/revoke — Révoquer un token
    # =========================================================================

    @auth_router.post(
        "/auth/revoke",
        response_model=ActionResponse,
        summary="Révoquer un token",
        description="Révoque un token JWT (access ou refresh).",
        responses={
            200: {"description": "Token révoqué"},
        },
    )
    async def revoke_token(
        request: Request,
        body: RevokeTokenRequest,
    ) -> ActionResponse:
        """Révoque un token JWT.

        Args:
            request: Requête HTTP.
            body: Corps de la requête.

        Returns:
            Confirmation.
        """
        try:
            from nexusdl.interfaces.web.backend.middleware.auth import TokenManager

            token_manager = TokenManager(__import__("nexusdl.core.config", fromlist=["get_config"]).get_config().auth)
            await token_manager.revoke_token(body.token)

            logger.info("Token révoqué")
            return ActionResponse(
                success=True,
                message=t("auth.success.token_revoked", default="Token revoked successfully"),
            )

        except Exception as e:
            logger.error("Erreur lors de la révocation du token: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="revoke_failed",
                    message=t("auth.error.revoke_failed", default="Failed to revoke token"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /auth/api-keys — Créer une API key
    # =========================================================================

    @auth_router.post(
        "/auth/api-keys",
        response_model=ApiKeyResponse,
        summary="Créer une API key",
        description="Crée une nouvelle API key pour l'utilisateur courant.",
        responses={
            201: {"description": "API key créée"},
            400: {"description": "Requête invalide"},
            429: {"description": "Limite atteinte"},
        },
        status_code=status.HTTP_201_CREATED,
    )
    async def create_api_key(
        request: Request,
        body: CreateApiKeyRequest,
    ) -> ApiKeyResponse:
        """Crée une nouvelle API key.

        Args:
            request: Requête HTTP.
            body: Corps de la requête.

        Returns:
            API key créée (avec la clé complète visible une seule fois).
        """
        if not hasattr(request.state, "user"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ErrorResponse(
                    error="unauthorized",
                    message=t("auth.error.unauthorized", default="Authentication required"),
                ).model_dump(),
            )

        user = request.state.user
        store = get_auth_store()

        try:
            # Vérifier la limite
            existing_keys = await store.get_user_api_keys(user.user_id)
            active_keys = [k for k in existing_keys if k.is_active and not k.is_expired]
            if len(active_keys) >= MAX_API_KEYS_PER_USER:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=ErrorResponse(
                        error="api_key_limit_reached",
                        message=t("auth.error.api_key_limit", default="API key limit reached ({limit})", limit=MAX_API_KEYS_PER_USER),
                    ).model_dump(),
                )

            # Générer la clé
            api_key_value = _generate_token(32)
            key_hash = hashlib.sha256(api_key_value.encode()).hexdigest()
            key_prefix = api_key_value[:8]

            # Calculer l'expiration
            expires_at = None
            if body.expires_days:
                expires_at = datetime.now(UTC) + timedelta(days=body.expires_days)

            # Créer l'entrée
            import uuid
            stored_key = StoredApiKey(
                key_id=str(uuid.uuid4()),
                user_id=user.user_id,
                name=body.name,
                key_hash=key_hash,
                key_prefix=key_prefix,
                permissions=body.permissions or ["read"],
                expires_at=expires_at,
            )

            await store.create_api_key(stored_key)

            logger.info("API key créée: user={}, name={}", user.username, body.name)
            await _emit_auth_event("auth.api_key.created", {
                "user_id": user.user_id,
                "key_id": stored_key.key_id,
                "name": body.name,
            })

            # Retourner avec la clé complète (visible une seule fois)
            response = _api_key_to_response(stored_key)
            return response.model_copy(update={"api_key": api_key_value})

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de la création de l'API key: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="create_failed",
                    message=t("auth.error.create_api_key_failed", default="Failed to create API key"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /auth/api-keys — Lister les API keys
    # =========================================================================

    @auth_router.get(
        "/auth/api-keys",
        response_model=ApiKeyListResponse,
        summary="Lister les API keys",
        description="Retourne toutes les API keys de l'utilisateur courant.",
        responses={
            200: {"description": "Liste des API keys"},
        },
    )
    async def list_api_keys(request: Request) -> ApiKeyListResponse:
        """Liste les API keys de l'utilisateur.

        Args:
            request: Requête HTTP.

        Returns:
            Liste des API keys.
        """
        if not hasattr(request.state, "user"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ErrorResponse(
                    error="unauthorized",
                    message=t("auth.error.unauthorized", default="Authentication required"),
                ).model_dump(),
            )

        user = request.state.user
        store = get_auth_store()

        try:
            api_keys = await store.get_user_api_keys(user.user_id)
            responses = [_api_key_to_response(k) for k in api_keys]

            return ApiKeyListResponse(
                api_keys=responses,
                total=len(responses),
            )

        except Exception as e:
            logger.error("Erreur lors de la liste des API keys: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="list_failed",
                    message=t("auth.error.list_api_keys_failed", default="Failed to list API keys"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # DELETE /auth/api-keys/{key_id} — Révoquer une API key
    # =========================================================================

    @auth_router.delete(
        "/auth/api-keys/{key_id}",
        response_model=ActionResponse,
        summary="Révoquer une API key",
        description="Révoque une API key spécifique.",
        responses={
            200: {"description": "API key révoquée"},
            404: {"description": "API key non trouvée"},
        },
    )
    async def revoke_api_key(
        request: Request,
        key_id: str,
    ) -> ActionResponse:
        """Révoque une API key.

        Args:
            request: Requête HTTP.
            key_id: ID de la clé.

        Returns:
            Confirmation.
        """
        if not hasattr(request.state, "user"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ErrorResponse(
                    error="unauthorized",
                    message=t("auth.error.unauthorized", default="Authentication required"),
                ).model_dump(),
            )

        user = request.state.user
        store = get_auth_store()

        try:
            # Vérifier que la clé appartient à l'utilisateur
            api_keys = await store.get_user_api_keys(user.user_id)
            key = next((k for k in api_keys if k.key_id == key_id), None)

            if key is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ErrorResponse(
                        error="api_key_not_found",
                        message=t("auth.error.api_key_not_found", default="API key not found"),
                        details={"key_id": key_id},
                    ).model_dump(),
                )

            await store.revoke_api_key(key_id)

            logger.info("API key révoquée: user={}, key_id={}", user.username, key_id)
            await _emit_auth_event("auth.api_key.revoked", {
                "user_id": user.user_id,
                "key_id": key_id,
            })

            return ActionResponse(
                success=True,
                message=t("auth.success.api_key_revoked", default="API key revoked successfully"),
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de la révocation de l'API key: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="revoke_failed",
                    message=t("auth.error.revoke_api_key_failed", default="Failed to revoke API key"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /auth/me — Profil utilisateur
    # =========================================================================

    @auth_router.get(
        "/auth/me",
        response_model=UserProfileResponse,
        summary="Profil utilisateur",
        description="Retourne le profil de l'utilisateur courant.",
        responses={
            200: {"description": "Profil utilisateur"},
            401: {"description": "Non authentifié"},
        },
    )
    async def get_profile(request: Request) -> UserProfileResponse:
        """Récupère le profil de l'utilisateur courant.

        Args:
            request: Requête HTTP.

        Returns:
            Profil utilisateur.
        """
        if not hasattr(request.state, "user"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ErrorResponse(
                    error="unauthorized",
                    message=t("auth.error.unauthorized", default="Authentication required"),
                ).model_dump(),
            )

        user = request.state.user
        store = get_auth_store()

        try:
            # Récupérer les données complètes depuis le store
            stored_user = await store.get_user_by_id(user.user_id)
            if stored_user is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ErrorResponse(
                        error="user_not_found",
                        message=t("auth.error.user_not_found", default="User not found"),
                    ).model_dump(),
                )

            return UserProfileResponse(
                user_id=stored_user.user_id,
                username=stored_user.username,
                email=stored_user.email,
                roles=stored_user.roles,
                permissions=stored_user.permissions,
                is_active=stored_user.is_active,
                created_at=stored_user.created_at,
                last_login_at=stored_user.last_login_at,
                metadata=stored_user.metadata,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de la récupération du profil: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="profile_failed",
                    message=t("auth.error.profile_failed", default="Failed to fetch profile"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # PATCH /auth/me — Mettre à jour le profil
    # =========================================================================

    @auth_router.patch(
        "/auth/me",
        response_model=UserProfileResponse,
        summary="Mettre à jour le profil",
        description="Met à jour le profil de l'utilisateur courant.",
        responses={
            200: {"description": "Profil mis à jour"},
        },
    )
    async def update_profile(
        request: Request,
        body: UpdateProfileRequest,
    ) -> UserProfileResponse:
        """Met à jour le profil.

        Args:
            request: Requête HTTP.
            body: Corps de la requête.

        Returns:
            Profil mis à jour.
        """
        if not hasattr(request.state, "user"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ErrorResponse(
                    error="unauthorized",
                    message=t("auth.error.unauthorized", default="Authentication required"),
                ).model_dump(),
            )

        user = request.state.user
        store = get_auth_store()

        try:
            stored_user = await store.get_user_by_id(user.user_id)
            if stored_user is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ErrorResponse(
                        error="user_not_found",
                        message=t("auth.error.user_not_found", default="User not found"),
                    ).model_dump(),
                )

            # Mettre à jour les champs
            if body.email is not None:
                # Vérifier que l'email n'est pas déjà utilisé
                existing = await store.get_user_by_email(body.email)
                if existing and existing.user_id != stored_user.user_id:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=ErrorResponse(
                            error="email_already_exists",
                            message=t("auth.error.email_already_exists", default="Email already registered"),
                        ).model_dump(),
                    )
                stored_user.email = body.email

            if body.metadata is not None:
                stored_user.metadata.update(body.metadata)

            await store.update_user(stored_user)

            logger.info("Profil mis à jour: user={}", stored_user.username)
            await _emit_auth_event("auth.profile.updated", {"user_id": stored_user.user_id})

            return UserProfileResponse(
                user_id=stored_user.user_id,
                username=stored_user.username,
                email=stored_user.email,
                roles=stored_user.roles,
                permissions=stored_user.permissions,
                is_active=stored_user.is_active,
                created_at=stored_user.created_at,
                last_login_at=stored_user.last_login_at,
                metadata=stored_user.metadata,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de la mise à jour du profil: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="update_failed",
                    message=t("auth.error.update_profile_failed", default="Failed to update profile"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /auth/change-password — Changer le mot de passe
    # =========================================================================

    @auth_router.post(
        "/auth/change-password",
        response_model=ActionResponse,
        summary="Changer le mot de passe",
        description="Change le mot de passe de l'utilisateur courant.",
        responses={
            200: {"description": "Mot de passe changé"},
            400: {"description": "Mot de passe actuel incorrect"},
        },
    )
    async def change_password(
        request: Request,
        body: ChangePasswordRequest,
    ) -> ActionResponse:
        """Change le mot de passe.

        Args:
            request: Requête HTTP.
            body: Corps de la requête.

        Returns:
            Confirmation.
        """
        if not hasattr(request.state, "user"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ErrorResponse(
                    error="unauthorized",
                    message=t("auth.error.unauthorized", default="Authentication required"),
                ).model_dump(),
            )

        user = request.state.user
        store = get_auth_store()

        try:
            stored_user = await store.get_user_by_id(user.user_id)
            if stored_user is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ErrorResponse(
                        error="user_not_found",
                        message=t("auth.error.user_not_found", default="User not found"),
                    ).model_dump(),
                )

            # Vérifier le mot de passe actuel
            if not _verify_password(body.current_password, stored_user.password_hash):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ErrorResponse(
                        error="invalid_current_password",
                        message=t("auth.error.invalid_current_password", default="Current password is incorrect"),
                    ).model_dump(),
                )

            # Valider le nouveau mot de passe
            _validate_password_strength(body.new_password)

            # Mettre à jour le mot de passe
            stored_user.password_hash = _hash_password(body.new_password)
            await store.update_user(stored_user)

            # Révoquer toutes les autres sessions
            session_id = request.headers.get("X-Session-ID")
            await store.revoke_all_user_sessions(stored_user.user_id, except_session_id=session_id)

            logger.info("Mot de passe changé: user={}", stored_user.username)
            await _emit_auth_event("auth.password.changed", {"user_id": stored_user.user_id})

            return ActionResponse(
                success=True,
                message=t("auth.success.password_changed", default="Password changed successfully"),
            )

        except HTTPException:
            raise
        except WeakPasswordError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorResponse(
                    error="weak_password",
                    message=str(e),
                ).model_dump(),
            ) from e
        except Exception as e:
            logger.error("Erreur lors du changement de mot de passe: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="change_password_failed",
                    message=t("auth.error.change_password_failed", default="Failed to change password"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /auth/forgot-password — Mot de passe oublié
    # =========================================================================

    @auth_router.post(
        "/auth/forgot-password",
        response_model=ActionResponse,
        summary="Mot de passe oublié",
        description="Envoie un email de réinitialisation (simulation).",
        responses={
            200: {"description": "Email envoyé (si l'email existe)"},
        },
    )
    async def forgot_password(
        request: Request,
        body: ForgotPasswordRequest,
    ) -> ActionResponse:
        """Demande une réinitialisation de mot de passe.

        Args:
            request: Requête HTTP.
            body: Corps de la requête.

        Returns:
            Confirmation (toujours succès pour éviter l'énumération).
        """
        logger.info("Demande de réinitialisation: email={}", body.email)

        store = get_auth_store()

        try:
            user = await store.get_user_by_email(body.email)

            if user:
                # Générer un token de réinitialisation
                reset_token = _generate_token(32)
                token_data = PasswordResetToken(
                    token=reset_token,
                    user_id=user.user_id,
                    expires_at=datetime.now(UTC) + timedelta(minutes=DEFAULT_RESET_TOKEN_EXPIRE_MINUTES),
                )
                await store.create_reset_token(token_data)

                # TODO: Envoyer l'email avec le token
                # Pour l'instant, logger le token (à retirer en production)
                logger.info(
                    "Token de réinitialisation généré pour {}: {} (expire dans {} minutes)",
                    body.email,
                    reset_token,
                    DEFAULT_RESET_TOKEN_EXPIRE_MINUTES,
                )

                await _emit_auth_event("auth.password.reset_requested", {
                    "email": body.email,
                    "user_id": user.user_id,
                })

            # Toujours retourner succès pour éviter l'énumération d'emails
            return ActionResponse(
                success=True,
                message=t(
                    "auth.success.reset_email_sent",
                    default="If an account exists with this email, a reset link has been sent",
                ),
            )

        except Exception as e:
            logger.error("Erreur lors de la demande de réinitialisation: {}", e)
            # Toujours retourner succès
            return ActionResponse(
                success=True,
                message=t(
                    "auth.success.reset_email_sent",
                    default="If an account exists with this email, a reset link has been sent",
                ),
            )

    # =========================================================================
    # POST /auth/reset-password — Réinitialiser le mot de passe
    # =========================================================================

    @auth_router.post(
        "/auth/reset-password",
        response_model=ActionResponse,
        summary="Réinitialiser le mot de passe",
        description="Réinitialise le mot de passe avec un token.",
        responses={
            200: {"description": "Mot de passe réinitialisé"},
            400: {"description": "Token invalide ou expiré"},
        },
    )
    async def reset_password(
        request: Request,
        body: ResetPasswordRequest,
    ) -> ActionResponse:
        """Réinitialise le mot de passe.

        Args:
            request: Requête HTTP.
            body: Corps de la requête.

        Returns:
            Confirmation.
        """
        logger.info("Réinitialisation de mot de passe")

        store = get_auth_store()

        try:
            # Récupérer le token
            token_data = await store.get_reset_token(body.token)
            if token_data is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ErrorResponse(
                        error="invalid_token",
                        message=t("auth.error.invalid_reset_token", default="Invalid or expired reset token"),
                    ).model_dump(),
                )

            if token_data.used:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ErrorResponse(
                        error="token_already_used",
                        message=t("auth.error.token_already_used", default="Reset token already used"),
                    ).model_dump(),
                )

            if token_data.is_expired:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ErrorResponse(
                        error="token_expired",
                        message=t("auth.error.reset_token_expired", default="Reset token has expired"),
                    ).model_dump(),
                )

            # Récupérer l'utilisateur
            user = await store.get_user_by_id(token_data.user_id)
            if user is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ErrorResponse(
                        error="invalid_token",
                        message=t("auth.error.invalid_reset_token", default="Invalid reset token"),
                    ).model_dump(),
                )

            # Valider le nouveau mot de passe
            _validate_password_strength(body.new_password)

            # Mettre à jour le mot de passe
            user.password_hash = _hash_password(body.new_password)
            user.failed_attempts = 0
            user.locked_until = None
            await store.update_user(user)

            # Marquer le token comme utilisé
            await store.mark_reset_token_used(body.token)

            # Révoquer toutes les sessions
            await store.revoke_all_user_sessions(user.user_id)

            logger.info("Mot de passe réinitialisé: user={}", user.username)
            await _emit_auth_event("auth.password.reset_completed", {"user_id": user.user_id})

            return ActionResponse(
                success=True,
                message=t("auth.success.password_reset", default="Password reset successfully"),
            )

        except HTTPException:
            raise
        except WeakPasswordError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorResponse(
                    error="weak_password",
                    message=str(e),
                ).model_dump(),
            ) from e
        except Exception as e:
            logger.error("Erreur lors de la réinitialisation du mot de passe: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="reset_failed",
                    message=t("auth.error.reset_failed", default="Failed to reset password"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /auth/sessions — Lister les sessions
    # =========================================================================

    @auth_router.get(
        "/auth/sessions",
        response_model=SessionListResponse,
        summary="Lister les sessions",
        description="Retourne toutes les sessions actives de l'utilisateur courant.",
        responses={
            200: {"description": "Liste des sessions"},
        },
    )
    async def list_sessions(request: Request) -> SessionListResponse:
        """Liste les sessions de l'utilisateur.

        Args:
            request: Requête HTTP.

        Returns:
            Liste des sessions.
        """
        if not hasattr(request.state, "user"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ErrorResponse(
                    error="unauthorized",
                    message=t("auth.error.unauthorized", default="Authentication required"),
                ).model_dump(),
            )

        user = request.state.user
        store = get_auth_store()

        try:
            sessions = await store.get_user_sessions(user.user_id)

            # Session courante
            current_session_id = request.headers.get("X-Session-ID")

            responses = [_session_to_response(s, current_session_id) for s in sessions]

            return SessionListResponse(
                sessions=responses,
                total=len(responses),
            )

        except Exception as e:
            logger.error("Erreur lors de la liste des sessions: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="list_failed",
                    message=t("auth.error.list_sessions_failed", default="Failed to list sessions"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # DELETE /auth/sessions/{session_id} — Révoquer une session
    # =========================================================================

    @auth_router.delete(
        "/auth/sessions/{session_id}",
        response_model=ActionResponse,
        summary="Révoquer une session",
        description="Révoque une session spécifique.",
        responses={
            200: {"description": "Session révoquée"},
            404: {"description": "Session non trouvée"},
        },
    )
    async def revoke_session(
        request: Request,
        session_id: str,
    ) -> ActionResponse:
        """Révoque une session.

        Args:
            request: Requête HTTP.
            session_id: ID de la session.

        Returns:
            Confirmation.
        """
        if not hasattr(request.state, "user"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ErrorResponse(
                    error="unauthorized",
                    message=t("auth.error.unauthorized", default="Authentication required"),
                ).model_dump(),
            )

        user = request.state.user
        store = get_auth_store()

        try:
            # Vérifier que la session appartient à l'utilisateur
            session = await store.get_session(session_id)
            if session is None or session.user_id != user.user_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ErrorResponse(
                        error="session_not_found",
                        message=t("auth.error.session_not_found", default="Session not found"),
                    ).model_dump(),
                )

            await store.revoke_session(session_id)

            logger.info("Session révoquée: user={}, session={}", user.username, session_id)
            await _emit_auth_event("auth.session.revoked", {
                "user_id": user.user_id,
                "session_id": session_id,
            })

            return ActionResponse(
                success=True,
                message=t("auth.success.session_revoked", default="Session revoked successfully"),
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de la révocation de la session: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="revoke_failed",
                    message=t("auth.error.revoke_session_failed", default="Failed to revoke session"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # DELETE /auth/sessions — Révoquer toutes les sessions
    # =========================================================================

    @auth_router.delete(
        "/auth/sessions",
        response_model=ActionResponse,
        summary="Révoquer toutes les sessions",
        description="Révoque toutes les sessions sauf la courante.",
        responses={
            200: {"description": "Sessions révoquées"},
        },
    )
    async def revoke_all_sessions(request: Request) -> ActionResponse:
        """Révoque toutes les sessions sauf la courante.

        Args:
            request: Requête HTTP.

        Returns:
            Confirmation.
        """
        if not hasattr(request.state, "user"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ErrorResponse(
                    error="unauthorized",
                    message=t("auth.error.unauthorized", default="Authentication required"),
                ).model_dump(),
            )

        user = request.state.user
        store = get_auth_store()

        try:
            current_session_id = request.headers.get("X-Session-ID")
            count = await store.revoke_all_user_sessions(user.user_id, except_session_id=current_session_id)

            logger.info("Toutes les sessions révoquées: user={}, count={}", user.username, count)
            await _emit_auth_event("auth.sessions.revoked_all", {
                "user_id": user.user_id,
                "count": count,
            })

            return ActionResponse(
                success=True,
                message=t(
                    "auth.success.sessions_revoked",
                    default="{count} session(s) revoked",
                    count=count,
                ),
            )

        except Exception as e:
            logger.error("Erreur lors de la révocation des sessions: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="revoke_failed",
                    message=t("auth.error.revoke_sessions_failed", default="Failed to revoke sessions"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES",
    "DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS",
    "DEFAULT_SESSION_EXPIRE_HOURS",
    "DEFAULT_API_KEY_EXPIRE_DAYS",
    "DEFAULT_RESET_TOKEN_EXPIRE_MINUTES",
    "MIN_PASSWORD_LENGTH",
    "MAX_PASSWORD_LENGTH",
    "MIN_USERNAME_LENGTH",
    "MAX_USERNAME_LENGTH",
    "MAX_SESSIONS_PER_USER",
    "MAX_API_KEYS_PER_USER",
    "MAX_FAILED_ATTEMPTS",
    "LOCKOUT_DURATION_MINUTES",
    "AVAILABLE_PERMISSIONS",
    "AVAILABLE_ROLES",
    # Exceptions
    "AuthRouterError",
    "AuthenticationFailedError",
    "UserNotFoundError",
    "UserAlreadyExistsError",
    "InvalidTokenError",
    "AccountLockedError",
    "WeakPasswordError",
    # Enums
    "TokenType",
    "SessionStatus",
    "ApiKeyStatus",
    # Modèles de stockage
    "StoredUser",
    "StoredSession",
    "StoredApiKey",
    "PasswordResetToken",
    # Modèles de requête
    "LoginRequest",
    "RegisterRequest",
    "RefreshTokenRequest",
    "RevokeTokenRequest",
    "CreateApiKeyRequest",
    "UpdateProfileRequest",
    "ChangePasswordRequest",
    "ForgotPasswordRequest",
    "ResetPasswordRequest",
    # Modèles de réponse
    "LoginResponse",
    "RegisterResponse",
    "RefreshTokenResponse",
    "UserProfileResponse",
    "ApiKeyResponse",
    "ApiKeyListResponse",
    "SessionResponse",
    "SessionListResponse",
    "ActionResponse",
    "ErrorResponse",
    # Auth Store
    "AuthStore",
    "InMemoryAuthStore",
    "get_auth_store",
    "set_auth_store",
    # Helpers
    "hash_password",
    "verify_password",
    "validate_password_strength",
    "generate_token",
    # Routeur
    "auth_router" if FASTAPI_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]

# Exports des fonctions helpers (sans underscore)
if FASTAPI_AVAILABLE:
    hash_password = _hash_password
    verify_password = _verify_password
    validate_password_strength = _validate_password_strength
    generate_token = _generate_token
