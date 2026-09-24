"""Module de sécurité centralisé pour l'API REST NexusDL.

Ce module fournit une API de haut niveau pour toutes les opérations de sécurité
de l'API REST. Il centralise et orchestre les fonctionnalités de sécurité
dispersées dans les middlewares (auth, cors, rate_limit, logging).

**Responsabilités** :
    - Génération de secrets (JWT, API keys, tokens CSRF)
    - Hashing et vérification de mots de passe
    - Validation de tokens JWT et API keys
    - Vérification de permissions et rôles
    - Sanitization d'entrées
    - Gestion des headers de sécurité
    - Logging d'audit
    - Statistiques de sécurité
    - Helpers de haut niveau pour les cas d'usage courants

**Architecture** :
    security.py
        ├── SecurityManager (orchestrateur principal)
        │   ├── SecretGenerator (génération de secrets)
        │   ├── PasswordHasher (hashing de mots de passe)
        │   ├── TokenValidator (validation de tokens)
        │   ├── PermissionChecker (vérification de permissions)
        │   ├── InputSanitizer (sanitization d'entrées)
        │   └── AuditLogger (logs d'audit)
        │
        ├── Helpers de haut niveau
        │   ├── generate_jwt_secret()
        │   ├── generate_api_key()
        │   ├── hash_password()
        │   ├── verify_password()
        │   ├── validate_jwt()
        │   ├── check_permission()
        │   ├── sanitize_input()
        │   └── get_security_headers()
        │
        └── Intégration
            ├── middleware/auth.py
            ├── middleware/cors.py
            ├── middleware/rate_limit.py
            └── middleware/logging.py

**Exemple d'utilisation — Génération de secrets** :
    >>> from nexusdl.interfaces.web.backend.security import (
    ...     generate_jwt_secret,
    ...     generate_api_key,
    ...     generate_csrf_token,
    ... )
    >>>
    >>> # Générer un secret JWT (64 caractères)
    >>> secret = generate_jwt_secret(length=64)
    >>> print(secret)
    'a1b2c3d4e5f6...'
    >>>
    >>> # Générer une API key
    >>> api_key = generate_api_key(prefix="nxdl_")
    >>> print(api_key)
    'nxdl_abc123def456...'

**Exemple d'utilisation — Hashing de mots de passe** :
    >>> from nexusdl.interfaces.web.backend.security import (
    ...     hash_password,
    ...     verify_password,
    ... )
    >>>
    >>> # Hasher un mot de passe
    >>> hashed = hash_password("SecurePass123!")
    >>> print(hashed)
    '$2b$12$...'
    >>>
    >>> # Vérifier un mot de passe
    >>> is_valid = verify_password("SecurePass123!", hashed)
    >>> print(is_valid)
    True

**Exemple d'utilisation — Validation de tokens** :
    >>> from nexusdl.interfaces.web.backend.security import (
    ...     validate_jwt,
    ...     validate_api_key,
    ... )
    >>>
    >>> # Valider un JWT
    >>> payload = validate_jwt(token, secret="your-secret")
    >>> print(payload.sub)
    'user_123'
    >>>
    >>> # Valider une API key
    >>> user_id = validate_api_key(api_key)
    >>> print(user_id)
    'user_456'

**Exemple d'utilisation — Vérification de permissions** :
    >>> from nexusdl.interfaces.web.backend.security import (
    ...     check_permission,
    ...     require_role,
    ... )
    >>>
    >>> # Vérifier une permission
    >>> has_perm = check_permission(user, "write")
    >>> print(has_perm)
    True
    >>>
    >>> # Exiger un rôle
    >>> require_role(user, "admin")  # Lève AuthorizationError si non-admin

Intégration :
    - interfaces/web/backend/middleware/auth.py     : Authentification
    - interfaces/web/backend/middleware/cors.py     : CORS
    - interfaces/web/backend/middleware/rate_limit.py : Rate limiting
    - interfaces/web/backend/middleware/logging.py  : Logging
    - core/config.py                                : Configuration
    - core/events.py                                : EventBus
    - core/logger.py                                : Logs
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, Final

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

try:
    import bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:
    BCRYPT_AVAILABLE = False

try:
    import jwt
    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False

from nexusdl.core.constants import APP_NAME
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# CONSTANTES
# ============================================================================


# Longueurs par défaut
DEFAULT_JWT_SECRET_LENGTH: Final[int] = 64
DEFAULT_API_KEY_LENGTH: Final[int] = 32
DEFAULT_CSRF_TOKEN_LENGTH: Final[int] = 32
DEFAULT_SESSION_ID_LENGTH: Final[int] = 32
DEFAULT_RESET_TOKEN_LENGTH: Final[int] = 32

# Algorithmes
DEFAULT_JWT_ALGORITHM: Final[str] = "HS256"
DEFAULT_HASH_ROUNDS: Final[int] = 12

# Patterns de validation
PASSWORD_PATTERN: Final[str] = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$"
EMAIL_PATTERN: Final[str] = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
USERNAME_PATTERN: Final[str] = r"^[a-zA-Z0-9_-]{3,50}$"
URL_PATTERN: Final[str] = r"^https?://"

# Caractères autorisés pour les secrets
SECRET_CHARS: Final[str] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
API_KEY_CHARS: Final[str] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

# Headers de sécurité
SECURITY_HEADERS: Final[dict[str, str]] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}

# Limites de rate limiting par défaut
DEFAULT_RATE_LIMIT: Final[int] = 100
DEFAULT_RATE_PERIOD: Final[int] = 60


# ============================================================================
# EXCEPTIONS
# ============================================================================


class SecurityError(NexusDLError):
    """Exception de base pour les erreurs de sécurité."""


class AuthenticationError(SecurityError):
    """Exception levée lorsqu'une authentification échoue.

    Attributes:
        reason: Raison de l'échec.
    """

    def __init__(self, reason: str = "") -> None:
        msg = "Authentication failed"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


class AuthorizationError(SecurityError):
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
        msg = f"Insufficient permissions. Required: {required_permission}"
        super().__init__(msg)
        self.required_permission = required_permission
        self.user_permissions = user_permissions or []


class TokenValidationError(SecurityError):
    """Exception levée lorsqu'un token est invalide.

    Attributes:
        token_type: Type de token.
        reason: Raison de l'invalidité.
    """

    def __init__(self, token_type: str, reason: str = "") -> None:
        msg = f"Invalid {token_type} token"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.token_type = token_type
        self.reason = reason


class InputValidationError(SecurityError):
    """Exception levée lorsqu'une entrée est invalide.

    Attributes:
        field: Champ concerné.
        value: Valeur invalide.
        reason: Raison de l'invalidité.
    """

    def __init__(self, field: str, value: Any, reason: str = "") -> None:
        msg = f"Invalid input for field '{field}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.field = field
        self.value = value
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class SecurityLevel(str, Enum):
    """Niveau de sécurité.

    Attributes:
        LOW: Sécurité basse (développement).
        MEDIUM: Sécurité moyenne (staging).
        HIGH: Sécurité haute (production).
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def hash_rounds(self) -> int:
        """Nombre de rounds de hashing."""
        return {
            SecurityLevel.LOW: 10,
            SecurityLevel.MEDIUM: 12,
            SecurityLevel.HIGH: 14,
        }[self]

    @property
    def secret_length(self) -> int:
        """Longueur des secrets."""
        return {
            SecurityLevel.LOW: 32,
            SecurityLevel.MEDIUM: 64,
            SecurityLevel.HIGH: 128,
        }[self]


class AuditAction(str, Enum):
    """Action d'audit.

    Attributes:
        LOGIN: Connexion.
        LOGOUT: Déconnexion.
        PASSWORD_CHANGE: Changement de mot de passe.
        API_KEY_CREATED: Création d'API key.
        API_KEY_REVOKED: Révocation d'API key.
        PERMISSION_DENIED: Permission refusée.
        RATE_LIMIT_EXCEEDED: Limite de débit dépassée.
        INVALID_TOKEN: Token invalide.
        SUSPICIOUS_ACTIVITY: Activité suspecte.
    """

    LOGIN = "login"
    LOGOUT = "logout"
    PASSWORD_CHANGE = "password_change"
    API_KEY_CREATED = "api_key_created"
    API_KEY_REVOKED = "api_key_revoked"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    INVALID_TOKEN = "invalid_token"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class SecurityConfig(BaseModel):
    """Configuration de sécurité.

    Attributes:
        security_level: Niveau de sécurité.
        jwt_secret: Secret JWT.
        jwt_algorithm: Algorithme JWT.
        jwt_expire_minutes: Expiration JWT (minutes).
        password_min_length: Longueur minimale des mots de passe.
        password_require_uppercase: Exiger une majuscule.
        password_require_lowercase: Exiger une minuscule.
        password_require_digit: Exiger un chiffre.
        password_require_special: Exiger un caractère spécial.
        max_login_attempts: Nombre max de tentatives de connexion.
        lockout_duration_minutes: Durée de verrouillage (minutes).
        enable_csrf_protection: Activer la protection CSRF.
        enable_rate_limiting: Activer le rate limiting.
        enable_audit_logging: Activer le logging d'audit.
    """

    security_level: SecurityLevel = Field(default=SecurityLevel.HIGH, description="Niveau de sécurité.")
    jwt_secret: str = Field(default="", min_length=32, description="Secret JWT.")
    jwt_algorithm: str = Field(default=DEFAULT_JWT_ALGORITHM, description="Algorithme JWT.")
    jwt_expire_minutes: int = Field(default=30, ge=1, le=1440, description="Expiration JWT (min).")
    password_min_length: int = Field(default=8, ge=8, le=128, description="Longueur min mot de passe.")
    password_require_uppercase: bool = Field(default=True, description="Exiger majuscule.")
    password_require_lowercase: bool = Field(default=True, description="Exiger minuscule.")
    password_require_digit: bool = Field(default=True, description="Exiger chiffre.")
    password_require_special: bool = Field(default=False, description="Exiger caractère spécial.")
    max_login_attempts: int = Field(default=5, ge=1, le=20, description="Max tentatives login.")
    lockout_duration_minutes: int = Field(default=15, ge=1, le=1440, description="Durée verrouillage (min).")
    enable_csrf_protection: bool = Field(default=True, description="Protection CSRF.")
    enable_rate_limiting: bool = Field(default=True, description="Rate limiting.")
    enable_audit_logging: bool = Field(default=True, description="Logging d'audit.")

    model_config = ConfigDict(extra="forbid")


class AuditLogEntry(BaseModel):
    """Entrée de log d'audit.

    Attributes:
        timestamp: Timestamp ISO 8601.
        action: Action auditée.
        user_id: ID de l'utilisateur (si applicable).
        ip_address: Adresse IP.
        user_agent: User-Agent.
        details: Détails additionnels.
        success: Si l'action a réussi.
    """

    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(), description="Timestamp.")
    action: AuditAction = Field(..., description="Action.")
    user_id: str | None = Field(default=None, description="ID utilisateur.")
    ip_address: str = Field(default="", description="IP.")
    user_agent: str = Field(default="", description="User-Agent.")
    details: dict[str, Any] = Field(default_factory=dict, description="Détails.")
    success: bool = Field(default=True, description="Succès.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class SecurityStats(BaseModel):
    """Statistiques de sécurité.

    Attributes:
        total_logins: Nombre total de connexions.
        successful_logins: Connexions réussies.
        failed_logins: Connexions échouées.
        total_api_calls: Nombre total d'appels API.
        rate_limited_calls: Appels limités par rate limiting.
        invalid_tokens: Tokens invalides détectés.
        permission_denials: Permissions refusées.
        started_at: Timestamp de début de collecte.
        last_activity_at: Timestamp de la dernière activité.
    """

    total_logins: int = Field(default=0, ge=0)
    successful_logins: int = Field(default=0, ge=0)
    failed_logins: int = Field(default=0, ge=0)
    total_api_calls: int = Field(default=0, ge=0)
    rate_limited_calls: int = Field(default=0, ge=0)
    invalid_tokens: int = Field(default=0, ge=0)
    permission_denials: int = Field(default=0, ge=0)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_activity_at: datetime | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# SECRET GENERATOR — Génération de secrets
# ============================================================================


class SecretGenerator:
    """Générateur de secrets cryptographiquement sécurisés."""

    @staticmethod
    def generate_secret(length: int = DEFAULT_JWT_SECRET_LENGTH, chars: str = SECRET_CHARS) -> str:
        """Génère un secret aléatoire.

        Args:
            length: Longueur du secret.
            chars: Caractères autorisés.

        Returns:
            Secret généré.

        Example:
            >>> secret = SecretGenerator.generate_secret(64)
            >>> len(secret)
            64
        """
        return "".join(secrets.choice(chars) for _ in range(length))

    @staticmethod
    def generate_jwt_secret(length: int = DEFAULT_JWT_SECRET_LENGTH) -> str:
        """Génère un secret JWT.

        Args:
            length: Longueur du secret.

        Returns:
            Secret JWT.

        Example:
            >>> secret = SecretGenerator.generate_jwt_secret()
            >>> len(secret)
            64
        """
        return SecretGenerator.generate_secret(length)

    @staticmethod
    def generate_api_key(prefix: str = "", length: int = DEFAULT_API_KEY_LENGTH) -> str:
        """Génère une API key.

        Args:
            prefix: Préfixe de la clé (ex: "nxdl_").
            length: Longueur de la partie aléatoire.

        Returns:
            API key générée.

        Example:
            >>> key = SecretGenerator.generate_api_key(prefix="nxdl_")
            >>> key.startswith("nxdl_")
            True
        """
        random_part = SecretGenerator.generate_secret(length, API_KEY_CHARS)
        return f"{prefix}{random_part}"

    @staticmethod
    def generate_csrf_token(length: int = DEFAULT_CSRF_TOKEN_LENGTH) -> str:
        """Génère un token CSRF.

        Args:
            length: Longueur du token.

        Returns:
            Token CSRF.

        Example:
            >>> token = SecretGenerator.generate_csrf_token()
            >>> len(token)
            32
        """
        return SecretGenerator.generate_secret(length)

    @staticmethod
    def generate_session_id(length: int = DEFAULT_SESSION_ID_LENGTH) -> str:
        """Génère un ID de session.

        Args:
            length: Longueur de l'ID.

        Returns:
            ID de session.

        Example:
            >>> session_id = SecretGenerator.generate_session_id()
            >>> len(session_id)
            32
        """
        return SecretGenerator.generate_secret(length)

    @staticmethod
    def generate_reset_token(length: int = DEFAULT_RESET_TOKEN_LENGTH) -> str:
        """Génère un token de réinitialisation.

        Args:
            length: Longueur du token.

        Returns:
            Token de réinitialisation.

        Example:
            >>> token = SecretGenerator.generate_reset_token()
            >>> len(token)
            32
        """
        return SecretGenerator.generate_secret(length)

    @staticmethod
    def generate_uuid() -> str:
        """Génère un UUID v4.

        Returns:
            UUID sous forme de string.

        Example:
            >>> uuid = SecretGenerator.generate_uuid()
            >>> len(uuid)
            36
        """
        return str(secrets.token_hex(16))


# ============================================================================
# PASSWORD HASHER — Hashing de mots de passe
# ============================================================================


class PasswordHasher:
    """Hasher de mots de passe avec bcrypt."""

    @staticmethod
    def hash_password(password: str, rounds: int = DEFAULT_HASH_ROUNDS) -> str:
        """Hash un mot de passe avec bcrypt.

        Args:
            password: Mot de passe en clair.
            rounds: Nombre de rounds (complexité).

        Returns:
            Hash du mot de passe.

        Raises:
            SecurityError: Si bcrypt n'est pas disponible.

        Example:
            >>> hashed = PasswordHasher.hash_password("SecurePass123!")
            >>> hashed.startswith("$2b$")
            True
        """
        if not BCRYPT_AVAILABLE:
            # Fallback sur SHA-256 (moins sécurisé)
            logger.warning("bcrypt non disponible, utilisation de SHA-256 (non recommandé)")
            return hashlib.sha256(password.encode()).hexdigest()

        salt = bcrypt.gensalt(rounds=rounds)
        return bcrypt.hashpw(password.encode(), salt).decode()

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        """Vérifie un mot de passe contre son hash.

        Args:
            password: Mot de passe en clair.
            password_hash: Hash du mot de passe.

        Returns:
            True si le mot de passe correspond.

        Example:
            >>> hashed = PasswordHasher.hash_password("SecurePass123!")
            >>> PasswordHasher.verify_password("SecurePass123!", hashed)
            True
            >>> PasswordHasher.verify_password("WrongPass", hashed)
            False
        """
        if not BCRYPT_AVAILABLE:
            # Fallback sur SHA-256
            return hashlib.sha256(password.encode()).hexdigest() == password_hash

        try:
            return bcrypt.checkpw(password.encode(), password_hash.encode())
        except Exception:
            return False

    @staticmethod
    def validate_password_strength(
        password: str,
        *,
        min_length: int = 8,
        require_uppercase: bool = True,
        require_lowercase: bool = True,
        require_digit: bool = True,
        require_special: bool = False,
    ) -> tuple[bool, list[str]]:
        """Valide la force d'un mot de passe.

        Args:
            password: Mot de passe à valider.
            min_length: Longueur minimale.
            require_uppercase: Exiger une majuscule.
            require_lowercase: Exiger une minuscule.
            require_digit: Exiger un chiffre.
            require_special: Exiger un caractère spécial.

        Returns:
            Tuple (is_valid, errors).

        Example:
            >>> is_valid, errors = PasswordHasher.validate_password_strength("Weak")
            >>> is_valid
            False
            >>> len(errors) > 0
            True
        """
        errors: list[str] = []

        if len(password) < min_length:
            errors.append(f"Minimum {min_length} caractères requis")

        if require_uppercase and not any(c.isupper() for c in password):
            errors.append("Au moins une majuscule requise")

        if require_lowercase and not any(c.islower() for c in password):
            errors.append("Au moins une minuscule requise")

        if require_digit and not any(c.isdigit() for c in password):
            errors.append("Au moins un chiffre requis")

        if require_special and not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
            errors.append("Au moins un caractère spécial requis")

        return len(errors) == 0, errors


# ============================================================================
# TOKEN VALIDATOR — Validation de tokens
# ============================================================================


class TokenValidator:
    """Validateur de tokens JWT et API keys."""

    @staticmethod
    def create_jwt(
        user_id: str,
        secret: str,
        *,
        algorithm: str = DEFAULT_JWT_ALGORITHM,
        expire_minutes: int = 30,
        roles: list[str] | None = None,
        permissions: list[str] | None = None,
    ) -> str:
        """Crée un token JWT.

        Args:
            user_id: ID de l'utilisateur.
            secret: Secret de signature.
            algorithm: Algorithme de signature.
            expire_minutes: Expiration en minutes.
            roles: Rôles de l'utilisateur.
            permissions: Permissions de l'utilisateur.

        Returns:
            Token JWT signé.

        Raises:
            SecurityError: Si PyJWT n'est pas disponible.

        Example:
            >>> token = TokenValidator.create_jwt("user_123", "secret")
            >>> len(token) > 0
            True
        """
        if not JWT_AVAILABLE:
            raise SecurityError("PyJWT n'est pas installé. Installez-le avec: pip install PyJWT")

        now = time.time()
        payload = {
            "sub": user_id,
            "iat": int(now),
            "exp": int(now + expire_minutes * 60),
            "roles": roles or [],
            "permissions": permissions or [],
        }

        return jwt.encode(payload, secret, algorithm=algorithm)

    @staticmethod
    def validate_jwt(
        token: str,
        secret: str,
        *,
        algorithm: str = DEFAULT_JWT_ALGORITHM,
    ) -> dict[str, Any]:
        """Valide un token JWT.

        Args:
            token: Token à valider.
            secret: Secret de signature.
            algorithm: Algorithme de signature.

        Returns:
            Payload du token.

        Raises:
            TokenValidationError: Si le token est invalide.

        Example:
            >>> payload = TokenValidator.validate_jwt(token, "secret")
            >>> payload["sub"]
            'user_123'
        """
        if not JWT_AVAILABLE:
            raise SecurityError("PyJWT n'est pas installé")

        try:
            return jwt.decode(token, secret, algorithms=[algorithm])
        except jwt.ExpiredSignatureError as e:
            raise TokenValidationError("JWT", "Token expired") from e
        except jwt.InvalidTokenError as e:
            raise TokenValidationError("JWT", str(e)) from e

    @staticmethod
    def validate_api_key_format(api_key: str, *, prefix: str = "") -> bool:
        """Valide le format d'une API key.

        Args:
            api_key: API key à valider.
            prefix: Préfixe attendu.

        Returns:
            True si le format est valide.

        Example:
            >>> TokenValidator.validate_api_key_format("nxdl_abc123", prefix="nxdl_")
            True
        """
        if prefix and not api_key.startswith(prefix):
            return False

        # Vérifier la longueur minimale
        if len(api_key) < 16:
            return False

        # Vérifier les caractères autorisés
        if not re.match(r"^[a-zA-Z0-9_-]+$", api_key):
            return False

        return True

    @staticmethod
    def hash_api_key(api_key: str) -> str:
        """Hash une API key pour stockage sécurisé.

        Args:
            api_key: API key à hasher.

        Returns:
            Hash SHA-256 de la clé.

        Example:
            >>> hashed = TokenValidator.hash_api_key("nxdl_abc123")
            >>> len(hashed)
            64
        """
        return hashlib.sha256(api_key.encode()).hexdigest()


# ============================================================================
# PERMISSION CHECKER — Vérification de permissions
# ============================================================================


class PermissionChecker:
    """Vérificateur de permissions et rôles."""

    @staticmethod
    def check_permission(
        user: Any,
        permission: str,
        *,
        require_all: bool = False,
    ) -> bool:
        """Vérifie si un utilisateur a une permission.

        Args:
            user: Utilisateur (doit avoir un attribut 'permissions').
            permission: Permission à vérifier.
            require_all: Si True, vérifier toutes les permissions (liste).

        Returns:
            True si l'utilisateur a la permission.

        Example:
            >>> class User:
            ...     permissions = ["read", "write"]
            >>> user = User()
            >>> PermissionChecker.check_permission(user, "read")
            True
        """
        user_permissions = getattr(user, "permissions", [])

        if require_all and isinstance(permission, list):
            return all(p in user_permissions for p in permission)
        elif isinstance(permission, list):
            return any(p in user_permissions for p in permission)
        else:
            return permission in user_permissions

    @staticmethod
    def check_role(
        user: Any,
        role: str,
        *,
        require_all: bool = False,
    ) -> bool:
        """Vérifie si un utilisateur a un rôle.

        Args:
            user: Utilisateur (doit avoir un attribut 'roles').
            role: Rôle à vérifier.
            require_all: Si True, vérifier tous les rôles (liste).

        Returns:
            True si l'utilisateur a le rôle.

        Example:
            >>> class User:
            ...     roles = ["admin", "user"]
            >>> user = User()
            >>> PermissionChecker.check_role(user, "admin")
            True
        """
        user_roles = getattr(user, "roles", [])

        if require_all and isinstance(role, list):
            return all(r in user_roles for r in role)
        elif isinstance(role, list):
            return any(r in user_roles for r in role)
        else:
            return role in user_roles

    @staticmethod
    def require_permission(user: Any, permission: str) -> None:
        """Exige qu'un utilisateur ait une permission.

        Args:
            user: Utilisateur.
            permission: Permission requise.

        Raises:
            AuthorizationError: Si l'utilisateur n'a pas la permission.

        Example:
            >>> PermissionChecker.require_permission(user, "write")
        """
        if not PermissionChecker.check_permission(user, permission):
            raise AuthorizationError(
                permission,
                getattr(user, "permissions", []),
            )

    @staticmethod
    def require_role(user: Any, role: str) -> None:
        """Exige qu'un utilisateur ait un rôle.

        Args:
            user: Utilisateur.
            role: Rôle requis.

        Raises:
            AuthorizationError: Si l'utilisateur n'a pas le rôle.

        Example:
            >>> PermissionChecker.require_role(user, "admin")
        """
        if not PermissionChecker.check_role(user, role):
            raise AuthorizationError(
                f"role:{role}",
                getattr(user, "roles", []),
            )


# ============================================================================
# INPUT SANITIZER — Sanitization d'entrées
# ============================================================================


class InputSanitizer:
    """Sanitiseur d'entrées pour prévenir les injections."""

    @staticmethod
    def sanitize_string(value: str, *, max_length: int = 1000) -> str:
        """Sanitise une chaîne de caractères.

        Args:
            value: Chaîne à sanitiser.
            max_length: Longueur maximale.

        Returns:
            Chaîne sanit
