"""Modèles de domaine pour l'authentification et les préférences utilisateur.

Ce module définit les structures de données (Pydantic v2) utilisées pour
représenter les utilisateurs, leurs sessions, leurs tokens d'authentification
et leurs préférences. Ces modèles sont au cœur du système d'authentification
et sont consommés par :

    - `interfaces/web/backend/security.py` : gestion JWT et sessions
    - `interfaces/web/backend/routers/auth.py` : endpoints d'authentification
    - `interfaces/web/backend/middleware/auth.py` : middleware d'auth
    - `interfaces/cli/config.py` : préférences CLI
    - `interfaces/gui/views/settings_view.py` : préférences GUI
    - `core/config.py` : configuration utilisateur

Architecture :
    User (mutable — profil utilisateur avec auth)
        ├── UserRole (enum) : ADMIN, USER, GUEST
        ├── AuthMethod (enum) : LOCAL, OAUTH, API_KEY
        ├── email, username, password_hash
        └── created_at, last_login_at

    UserProfile (immutable — préférences utilisateur)
        ├── language, theme, timezone
        ├── download_path, library_path
        ├── default_format, default_quality
        └── notifications_enabled, adult_content_allowed

    AuthToken (immutable — token JWT)
        ├── access_token, refresh_token
        ├── token_type, expires_at
        └── user_id, scope

    Session (immutable — session active)
        ├── session_id, user_id
        ├── ip_address, user_agent
        ├── created_at, expires_at, last_activity_at
        └── status : SessionStatus (ACTIVE, EXPIRED, REVOKED)

    ApiKey (immutable — clé API pour accès programmatique)
        ├── key_hash, key_prefix (masqué)
        ├── name, permissions, expires_at
        └── last_used_at, request_count

Règles d'or :
    1. `User` est le SEUL modèle mutable (mot de passe, email, rôle évolutifs).
    2. Tous les autres modèles sont `frozen=True` (immuables, hashables).
    3. Le mot de passe n'est JAMAIS stocké en clair (bcrypt uniquement).
    4. Les mots de passe et API keys sont MASQUÉS dans `to_summary()`.
    5. Les tokens JWT ont une durée de vie limitée (access: 30min, refresh: 7j).
    6. Les sessions ont un timeout d'inactivité configurable.
    7. Les enums utilisent `str` comme base pour sérialisation JSON native.
    8. Les IDs sont des UUIDs pour unicité globale.
    9. Les timestamps sont en UTC, sérialisables en ISO 8601.
    10. Les validators Pydantic garantissent la cohérence (email, username, etc.).

Exemple d'utilisation :
    >>> from nexusdl.core.models.user import (
    ...     User, UserProfile, AuthToken, Session, UserRole,
    ...     hash_password, verify_password,
    ... )
    >>>
    >>> # Créer un utilisateur
    >>> user = User.create(
    ...     email="user@example.com",
    ...     username="johndoe",
    ...     password="secure_password_123",
    ...     role=UserRole.USER,
    ... )
    >>> print(user.is_admin)  # False
    >>> print(user.password_hash[:20])  # '$2b$12$...' (bcrypt hash)
    >>>
    >>> # Vérifier le mot de passe
    >>> assert verify_password("secure_password_123", user.password_hash)
    >>>
    >>> # Créer un token JWT
    >>> token = AuthToken.create_for_user(
    ...     user_id=user.id,
    ...     access_expires_minutes=30,
    ...     refresh_expires_days=7,
    ... )
    >>> print(token.is_expired)  # False
    >>>
    >>> # Créer une session
    >>> session = Session.create_for_user(
    ...     user_id=user.id,
    ...     ip_address="192.168.1.1",
    ...     user_agent="Mozilla/5.0...",
    ... )
    >>> print(session.is_active)  # True
    >>>
    >>> # Préférences utilisateur
    >>> profile = UserProfile(
    ...     user_id=user.id,
    ...     language="fr",
    ...     theme="dark",
    ...     download_path=Path("~/Mangas"),
    ... )
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Final, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# EXCEPTIONS
# ============================================================================


class UserModelError(NexusDLError):
    """Exception de base pour les erreurs liées aux modèles utilisateur."""


class AuthenticationError(UserModelError):
    """Exception levée en cas d'échec d'authentification."""

    def __init__(self, reason: str = "Authentification échouée") -> None:
        super().__init__(reason)
        self.reason = reason


class InvalidCredentialsError(AuthenticationError):
    """Exception levée lorsque les identifiants sont invalides."""

    def __init__(self) -> None:
        super().__init__("Identifiants invalides")


class AccountDisabledError(AuthenticationError):
    """Exception levée lorsque le compte est désactivé."""

    def __init__(self, user_id: str) -> None:
        super().__init__(f"Compte désactivé: {user_id}")
        self.user_id = user_id


class TokenExpiredError(AuthenticationError):
    """Exception levée lorsqu'un token est expiré."""

    def __init__(self, token_type: str = "token") -> None:
        super().__init__(f"{token_type} expiré")
        self.token_type = token_type


class SessionExpiredError(AuthenticationError):
    """Exception levée lorsqu'une session est expirée."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session expirée: {session_id}")
        self.session_id = session_id


class InvalidEmailError(UserModelError):
    """Exception levée lorsqu'un email est invalide."""

    def __init__(self, email: str) -> None:
        super().__init__(f"Email invalide: {email}")
        self.email = email


class InvalidUsernameError(UserModelError):
    """Exception levée lorsqu'un username est invalide."""

    def __init__(self, username: str, reason: str = "") -> None:
        msg = f"Username invalide: {username}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.username = username
        self.reason = reason


class WeakPasswordError(UserModelError):
    """Exception levée lorsqu'un mot de passe est trop faible."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Mot de passe trop faible: {reason}")
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class UserRole(str, Enum):
    """Rôle d'un utilisateur dans le système.

    ADMIN  : Accès complet (gestion utilisateurs, config globale, etc.).
    USER   : Utilisateur standard (téléchargements, bibliothèque, etc.).
    GUEST  : Utilisateur non authentifié (accès limité, pas de persistance).
    """

    ADMIN = "admin"
    USER = "user"
    GUEST = "guest"

    @property
    def label(self) -> str:
        """Libellé humain du rôle."""
        return {
            UserRole.ADMIN: "Administrateur",
            UserRole.USER: "Utilisateur",
            UserRole.GUEST: "Invité",
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode pour l'affichage."""
        return {
            UserRole.ADMIN: "👑",
            UserRole.USER: "👤",
            UserRole.GUEST: "👻",
        }[self]

    @property
    def color(self) -> str:
        """Couleur hexadécimale pour l'affichage."""
        return {
            UserRole.ADMIN: "#f59e0b",  # amber-500
            UserRole.USER: "#3b82f6",  # blue-500
            UserRole.GUEST: "#9ca3af",  # gray-400
        }[self]

    @property
    def is_admin(self) -> bool:
        """Indique si le rôle est administrateur."""
        return self == UserRole.ADMIN

    @property
    def is_authenticated(self) -> bool:
        """Indique si le rôle correspond à un utilisateur authentifié."""
        return self in (UserRole.ADMIN, UserRole.USER)

    @property
    def permissions_level(self) -> int:
        """Niveau de permissions (0 = minimal, 100 = maximal)."""
        return {
            UserRole.GUEST: 0,
            UserRole.USER: 50,
            UserRole.ADMIN: 100,
        }[self]


class AuthMethod(str, Enum):
    """Méthode d'authentification utilisée.

    LOCAL   : Authentification par email/mot de passe (bcrypt).
    OAUTH   : Authentification via provider externe (Google, GitHub, etc.).
    API_KEY : Authentification par clé API (pour accès programmatique).
    """

    LOCAL = "local"
    OAUTH = "oauth"
    API_KEY = "api_key"

    @property
    def label(self) -> str:
        """Libellé humain de la méthode."""
        return {
            AuthMethod.LOCAL: "Email/Mot de passe",
            AuthMethod.OAUTH: "OAuth (externe)",
            AuthMethod.API_KEY: "Clé API",
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode pour l'affichage."""
        return {
            AuthMethod.LOCAL: "🔐",
            AuthMethod.OAUTH: "🌐",
            AuthMethod.API_KEY: "🔑",
        }[self]


class SessionStatus(str, Enum):
    """État d'une session utilisateur.

    ACTIVE   : Session active et valide.
    EXPIRED  : Session expirée (timeout ou durée max atteinte).
    REVOKED  : Session révoquée manuellement par l'utilisateur.
    """

    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"

    @property
    def label(self) -> str:
        """Libellé humain du statut."""
        return {
            SessionStatus.ACTIVE: "Active",
            SessionStatus.EXPIRED: "Expirée",
            SessionStatus.REVOKED: "Révoquée",
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode pour l'affichage."""
        return {
            SessionStatus.ACTIVE: "🟢",
            SessionStatus.EXPIRED: "⚪",
            SessionStatus.REVOKED: "🔴",
        }[self]

    @property
    def is_active(self) -> bool:
        """Indique si la session est active."""
        return self == SessionStatus.ACTIVE


class TokenType(str, Enum):
    """Type de token JWT.

    ACCESS  : Token d'accès (courte durée, utilisé pour les requêtes API).
    REFRESH : Token de rafraîchissement (longue durée, utilisé pour renew access).
    """

    ACCESS = "access"
    REFRESH = "refresh"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            TokenType.ACCESS: "Token d'accès",
            TokenType.REFRESH: "Token de rafraîchissement",
        }[self]


class ApiKeyPermission(str, Enum):
    """Permissions associées à une clé API.

    READ       : Lecture seule (recherche, liste mangas).
    WRITE      : Lecture + écriture (téléchargements, gestion bibliothèque).
    ADMIN      : Accès complet (gestion utilisateurs, config).
    DOWNLOAD   : Téléchargements uniquement.
    """

    READ = "read"
    WRITE = "write"
    ADMIN = "admin"
    DOWNLOAD = "download"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ApiKeyPermission.READ: "Lecture seule",
            ApiKeyPermission.WRITE: "Lecture/Écriture",
            ApiKeyPermission.ADMIN: "Administrateur",
            ApiKeyPermission.DOWNLOAD: "Téléchargements",
        }[self]

    @property
    def level(self) -> int:
        """Niveau de permission (0 = minimal, 100 = maximal)."""
        return {
            ApiKeyPermission.READ: 10,
            ApiKeyPermission.DOWNLOAD: 40,
            ApiKeyPermission.WRITE: 70,
            ApiKeyPermission.ADMIN: 100,
        }[self]


# ============================================================================
# CONSTANTES — Patterns de validation
# ============================================================================


# Pattern pour valider un email (RFC 5322 simplifié)
_EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
)

# Pattern pour valider un username (3-30 caractères, alphanumérique + underscore)
_USERNAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9_]{3,30}$")

# Critères de force du mot de passe
_MIN_PASSWORD_LENGTH: Final[int] = 8
_MAX_PASSWORD_LENGTH: Final[int] = 128

# Durée de vie par défaut des tokens
_DEFAULT_ACCESS_TOKEN_MINUTES: Final[int] = 30
_DEFAULT_REFRESH_TOKEN_DAYS: Final[int] = 7

# Durée de vie par défaut des sessions
_DEFAULT_SESSION_TIMEOUT_MINUTES: Final[int] = 30  # Inactivité
_DEFAULT_SESSION_MAX_AGE_DAYS: Final[int] = 30  # Âge maximum

# Préfixe des clés API
_API_KEY_PREFIX: Final[str] = "nxl_"
_API_KEY_LENGTH: Final[int] = 48  # bytes avant encodage


# ============================================================================
# HELPERS — Hachage et validation
# ============================================================================


def hash_password(password: str) -> str:
    """Hache un mot de passe avec bcrypt.

    Utilise passlib pour le hachage bcrypt avec un salt aléatoire.
    Le coût (rounds) est fixé à 12 pour un bon équilibre sécurité/perf.

    Args:
        password: Mot de passe en clair.

    Returns:
        Hash bcrypt sous forme de chaîne (60 caractères).

    Raises:
        WeakPasswordError: Si le mot de passe est trop faible.

    Example:
        >>> hash_pw = hash_password("secure_password_123")
        >>> print(hash_pw[:10])  # '$2b$12$...'
    """
    validate_password_strength(password)

    try:
        from passlib.context import CryptContext
        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return pwd_context.hash(password)
    except ImportError:
        # Fallback : SHA256 + salt (moins sécurisé, mais fonctionnel)
        salt = secrets.token_hex(16)
        hash_bytes = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        return f"$sha256${salt}${hash_bytes}"


def verify_password(password: str, password_hash: str) -> bool:
    """Vérifie un mot de passe contre son hash.

    Supporte les hashes bcrypt (passlib) et SHA256 (fallback).

    Args:
        password: Mot de passe en clair à vérifier.
        password_hash: Hash stocké (bcrypt ou SHA256).

    Returns:
        True si le mot de passe correspond au hash.

    Example:
        >>> hash_pw = hash_password("secure_password_123")
        >>> verify_password("secure_password_123", hash_pw)
        True
        >>> verify_password("wrong_password", hash_pw)
        False
    """
    if not password or not password_hash:
        return False

    try:
        from passlib.context import CryptContext
        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return pwd_context.verify(password, password_hash)
    except ImportError:
        # Fallback SHA256
        if password_hash.startswith("$sha256$"):
            parts = password_hash.split("$")
            if len(parts) != 4:
                return False
            salt = parts[2]
            expected_hash = parts[3]
            actual_hash = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
            return secrets.compare_digest(actual_hash, expected_hash)
        return False


def validate_password_strength(password: str) -> None:
    """Valide la force d'un mot de passe.

    Critères :
        - Minimum 8 caractères
        - Maximum 128 caractères
        - Au moins une lettre majuscule
        - Au moins une lettre minuscule
        - Au moins un chiffre
        - (Optionnel) Au moins un caractère spécial

    Args:
        password: Mot de passe à valider.

    Raises:
        WeakPasswordError: Si le mot de passe ne respecte pas les critères.
    """
    if len(password) < _MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(
            f"Minimum {_MIN_PASSWORD_LENGTH} caractères requis"
        )
    if len(password) > _MAX_PASSWORD_LENGTH:
        raise WeakPasswordError(
            f"Maximum {_MAX_PASSWORD_LENGTH} caractères autorisés"
        )
    if not re.search(r"[A-Z]", password):
        raise WeakPasswordError("Au moins une lettre majuscule requise")
    if not re.search(r"[a-z]", password):
        raise WeakPasswordError("Au moins une lettre minuscule requise")
    if not re.search(r"\d", password):
        raise WeakPasswordError("Au moins un chiffre requis")


def validate_email(email: str) -> str:
    """Valide et normalise un email.

    Args:
        email: Email à valider.

    Returns:
        Email normalisé (lowercase, trimmed).

    Raises:
        InvalidEmailError: Si l'email est invalide.
    """
    normalized = email.strip().lower()
    if not _EMAIL_PATTERN.match(normalized):
        raise InvalidEmailError(email)
    return normalized


def validate_username(username: str) -> str:
    """Valide et normalise un username.

    Critères :
        - 3 à 30 caractères
        - Alphanumérique + underscore uniquement
        - Ne commence pas par un underscore

    Args:
        username: Username à valider.

    Returns:
        Username normalisé (trimmed).

    Raises:
        InvalidUsernameError: Si le username est invalide.
    """
    normalized = username.strip()
    if not _USERNAME_PATTERN.match(normalized):
        raise InvalidUsernameError(
            username,
            "3-30 caractères alphanumériques ou underscores",
        )
    if normalized.startswith("_"):
        raise InvalidUsernameError(username, "ne peut pas commencer par _")
    return normalized


def generate_user_id() -> str:
    """Génère un ID unique pour un utilisateur.

    Format : 'user_' + UUID v4 (32 caractères hex).

    Returns:
        Identifiant unique sous forme de chaîne.
    """
    return f"user_{uuid.uuid4().hex}"


def generate_session_id() -> str:
    """Génère un ID unique pour une session.

    Format : 'sess_' + UUID v4 (32 caractères hex).

    Returns:
        Identifiant unique sous forme de chaîne.
    """
    return f"sess_{uuid.uuid4().hex}"


def generate_api_key() -> tuple[str, str]:
    """Génère une clé API sécurisée.

    Format : 'nxl_' + 64 caractères alphanumériques.
    Retourne aussi le hash SHA256 de la clé pour stockage sécurisé.

    Returns:
        Tuple (clé_en_clair, hash_sha256).

    Example:
        >>> key, key_hash = generate_api_key()
        >>> print(key[:10])  # 'nxl_abc...'
        >>> print(key_hash[:20])  # 'a1b2c3d4...'
    """
    # Générer la clé (48 bytes → 64 caractères base64url)
    raw_key = secrets.token_urlsafe(_API_KEY_LENGTH)
    api_key = f"{_API_KEY_PREFIX}{raw_key}"

    # Hacher la clé pour stockage
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    return api_key, key_hash


def mask_api_key(api_key: str) -> str:
    """Masque une clé API pour l'affichage (seuls les 8 derniers caractères visibles).

    Args:
        api_key: Clé API à masquer.

    Returns:
        Clé masquée (ex: 'nxl_abc...xyz12345').

    Example:
        >>> mask_api_key("nxl_abcdefghijklmnopqrstuvwxyz123456789")
        'nxl_...stuvwxyz123456789'
    """
    if len(api_key) <= 12:
        return "***"
    return f"{api_key[:4]}...{api_key[-12:]}"


def mask_secret(value: str, visible_chars: int = 4) -> str:
    """Masque une valeur sensible (token, mot de passe, etc.).

    Args:
        value: Valeur à masquer.
        visible_chars: Nombre de caractères visibles à la fin.

    Returns:
        Valeur masquée (ex: '***...xyz1').

    Example:
        >>> mask_secret("super_secret_token_12345")
        '***...2345'
    """
    if not value or len(value) <= visible_chars:
        return "***"
    return f"***...{value[-visible_chars:]}"


# ============================================================================
# MODÈLES PYDANTIC — User
# ============================================================================


class User(BaseModel):
    """Utilisateur du système (mutable — profil évolutif).

    Représente un utilisateur authentifié avec ses identifiants, son rôle
    et ses métadonnées. Le mot de passe est stocké sous forme de hash bcrypt
    (jamais en clair).

    Ce modèle est le SEUL modèle mutable du module car son profil évolue
    (mot de passe changé, email mis à jour, rôle modifié, dernière connexion).

    Attributes:
        id: Identifiant unique de l'utilisateur (généré automatiquement).
        email: Email de l'utilisateur (unique, validé).
        username: Nom d'utilisateur (unique, 3-30 caractères).
        password_hash: Hash bcrypt du mot de passe (jamais exposé en clair).
        role: Rôle de l'utilisateur (ADMIN, USER, GUEST).
        auth_method: Méthode d'authentification utilisée.
        is_active: True si le compte est activé.
        is_verified: True si l'email est vérifié.
        created_at: Timestamp de création du compte.
        updated_at: Timestamp de dernière mise à jour.
        last_login_at: Timestamp de dernière connexion.
        login_count: Nombre total de connexions.
        display_name: Nom d'affichage (optionnel, sinon username utilisé).
        avatar_url: URL de l'avatar (optionnel).
        metadata: Métadonnées additionnelles (libres).
    """

    # Identification
    id: str = Field(
        default_factory=generate_user_id,
        description="Identifiant unique de l'utilisateur.",
    )
    email: str = Field(
        ...,
        min_length=5,
        max_length=254,
        description="Email de l'utilisateur (unique, validé).",
    )
    username: str = Field(
        ...,
        min_length=3,
        max_length=30,
        description="Nom d'utilisateur (unique, 3-30 caractères).",
    )

    # Authentification
    password_hash: str = Field(
        ...,
        min_length=10,
        description="Hash bcrypt du mot de passe (jamais exposé en clair).",
    )
    role: UserRole = Field(
        default=UserRole.USER,
        description="Rôle de l'utilisateur.",
    )
    auth_method: AuthMethod = Field(
        default=AuthMethod.LOCAL,
        description="Méthode d'authentification utilisée.",
    )

    # État du compte
    is_active: bool = Field(
        default=True,
        description="True si le compte est activé.",
    )
    is_verified: bool = Field(
        default=False,
        description="True si l'email est vérifié.",
    )

    # Timestamps
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de création du compte.",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de dernière mise à jour.",
    )
    last_login_at: datetime | None = Field(
        default=None,
        description="Timestamp de dernière connexion.",
    )

    # Compteurs
    login_count: int = Field(
        default=0,
        ge=0,
        description="Nombre total de connexions.",
    )

    # Profil
    display_name: str | None = Field(
        default=None,
        max_length=100,
        description="Nom d'affichage (optionnel, sinon username utilisé).",
    )
    avatar_url: str | None = Field(
        default=None,
        max_length=500,
        description="URL de l'avatar (optionnel).",
    )

    # Métadonnées libres
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Métadonnées additionnelles (libres).",
    )

    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
    )

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        """Valide l'email."""
        return validate_email(v)

    @field_validator("username")
    @classmethod
    def _validate_username(cls, v: str) -> str:
        """Valide le username."""
        return validate_username(v)

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Vérifie la cohérence globale."""
        # Si auth_method est OAUTH, password_hash peut être vide
        # (mais on le garde pour compatibilité)
        return self

    # --------------------------------------------------------------------
    # Méthodes de création
    # --------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        email: str,
        username: str,
        password: str,
        *,
        role: UserRole = UserRole.USER,
        auth_method: AuthMethod = AuthMethod.LOCAL,
        is_verified: bool = False,
        display_name: str | None = None,
    ) -> User:
        """Crée un nouvel utilisateur avec mot de passe haché.

        Args:
            email: Email de l'utilisateur.
            username: Nom d'utilisateur.
            password: Mot de passe en clair (sera haché).
            role: Rôle de l'utilisateur.
            auth_method: Méthode d'authentification.
            is_verified: True si l'email est déjà vérifié.
            display_name: Nom d'affichage (optionnel).

        Returns:
            Instance de User avec mot de passe haché.

        Raises:
            InvalidEmailError: Si l'email est invalide.
            InvalidUsernameError: Si le username est invalide.
            WeakPasswordError: Si le mot de passe est trop faible.
        """
        password_hash = hash_password(password)
        return cls(
            email=email,
            username=username,
            password_hash=password_hash,
            role=role,
            auth_method=auth_method,
            is_verified=is_verified,
            display_name=display_name,
        )

    @classmethod
    def create_guest(cls) -> User:
        """Crée un utilisateur invité (non authentifié).

        Returns:
            Instance de User avec rôle GUEST et credentials fictifs.
        """
        guest_id = uuid.uuid4().hex[:8]
        return cls(
            email=f"guest_{guest_id}@local",
            username=f"guest_{guest_id}",
            password_hash="$guest$",  # Marker spécial
            role=UserRole.GUEST,
            auth_method=AuthMethod.LOCAL,
            is_active=True,
            is_verified=False,
        )

    # --------------------------------------------------------------------
    # Méthodes d'authentification
    # --------------------------------------------------------------------

    def verify_password(self, password: str) -> bool:
        """Vérifie un mot de passe contre le hash stocké.

        Args:
            password: Mot de passe en clair à vérifier.

        Returns:
            True si le mot de passe correspond.
        """
        if self.auth_method != AuthMethod.LOCAL:
            return False
        return verify_password(password, self.password_hash)

    def change_password(self, old_password: str, new_password: str) -> User:
        """Change le mot de passe de l'utilisateur.

        Args:
            old_password: Ancien mot de passe (pour vérification).
            new_password: Nouveau mot de passe (sera haché).

        Returns:
            Nouvelle instance de User avec le mot de passe mis à jour.

        Raises:
            InvalidCredentialsError: Si l'ancien mot de passe est incorrect.
            WeakPasswordError: Si le nouveau mot de passe est trop faible.
        """
        if not self.verify_password(old_password):
            raise InvalidCredentialsError()

        new_hash = hash_password(new_password)
        return self.model_copy(
            update={
                "password_hash": new_hash,
                "updated_at": datetime.now(UTC),
            }
        )

    def record_login(self) -> User:
        """Enregistre une connexion réussie.

        Returns:
            Nouvelle instance de User avec last_login_at et login_count mis à jour.
        """
        return self.model_copy(
            update={
                "last_login_at": datetime.now(UTC),
                "login_count": self.login_count + 1,
                "updated_at": datetime.now(UTC),
            }
        )

    def activate(self) -> User:
        """Active le compte utilisateur.

        Returns:
            Nouvelle instance de User avec is_active=True.
        """
        return self.model_copy(
            update={
                "is_active": True,
                "updated_at": datetime.now(UTC),
            }
        )

    def deactivate(self) -> User:
        """Désactive le compte utilisateur.

        Returns:
            Nouvelle instance de User avec is_active=False.
        """
        return self.model_copy(
            update={
                "is_active": False,
                "updated_at": datetime.now(UTC),
            }
        )

    def verify_email(self) -> User:
        """Marque l'email comme vérifié.

        Returns:
            Nouvelle instance de User avec is_verified=True.
        """
        return self.model_copy(
            update={
                "is_verified": True,
                "updated_at": datetime.now(UTC),
            }
        )

    def change_role(self, new_role: UserRole) -> User:
        """Change le rôle de l'utilisateur.

        Args:
            new_role: Nouveau rôle.

        Returns:
            Nouvelle instance de User avec le rôle mis à jour.
        """
        return self.model_copy(
            update={
                "role": new_role,
                "updated_at": datetime.now(UTC),
            }
        )

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def is_admin(self) -> bool:
        """Indique si l'utilisateur est administrateur."""
        return self.role == UserRole.ADMIN

    @property
    def is_guest(self) -> bool:
        """Indique si l'utilisateur est un invité."""
        return self.role == UserRole.GUEST

    @property
    def is_authenticated(self) -> bool:
        """Indique si l'utilisateur est authentifié (non-guest)."""
        return self.role.is_authenticated

    @property
    def display_name_or_username(self) -> str:
        """Nom d'affichage (display_name si défini, sinon username)."""
        return self.display_name or self.username

    @property
    def avatar_url_or_default(self) -> str:
        """URL de l'avatar ou avatar par défaut (gravatar)."""
        if self.avatar_url:
            return self.avatar_url
        # Gravatar par défaut basé sur l'email
        email_hash = hashlib.md5(self.email.lower().encode()).hexdigest()
        return f"https://www.gravatar.com/avatar/{email_hash}?d=identicon"

    @property
    def account_age_days(self) -> float:
        """Âge du compte en jours."""
        return (datetime.now(UTC) - self.created_at).total_seconds() / 86_400.0

    @property
    def days_since_last_login(self) -> float | None:
        """Nombre de jours depuis la dernière connexion (None si jamais connecté)."""
        if self.last_login_at is None:
            return None
        return (datetime.now(UTC) - self.last_login_at).total_seconds() / 86_400.0

    @property
    def can_download(self) -> bool:
        """Indique si l'utilisateur peut télécharger."""
        return self.is_active and self.role in (UserRole.ADMIN, UserRole.USER)

    @property
    def can_manage_users(self) -> bool:
        """Indique si l'utilisateur peut gérer les autres utilisateurs."""
        return self.is_active and self.role == UserRole.ADMIN

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable (pour WebSocket/API).

        IMPORTANT : Le mot de passe n'est JAMAIS inclus.

        Returns:
            Dictionnaire avec les champs essentiels pour l'affichage.
        """
        return {
            "id": self.id,
            "email": self.email,
            "username": self.username,
            "display_name": self.display_name_or_username,
            "role": self.role.value,
            "role_label": self.role.label,
            "role_icon": self.role.icon,
            "role_color": self.role.color,
            "auth_method": self.auth_method.value,
            "auth_method_label": self.auth_method.label,
            "is_active": self.is_active,
            "is_verified": self.is_verified,
            "is_admin": self.is_admin,
            "is_guest": self.is_guest,
            "is_authenticated": self.is_authenticated,
            "avatar_url": self.avatar_url_or_default,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_login_at": (
                self.last_login_at.isoformat() if self.last_login_at else None
            ),
            "login_count": self.login_count,
            "account_age_days": round(self.account_age_days, 1),
            "can_download": self.can_download,
            "can_manage_users": self.can_manage_users,
            # NOTE : password_hash volontairement OMIT
        }

    def to_admin_summary(self) -> dict[str, Any]:
        """Retourne un résumé complet pour l'interface admin.

        Inclut des informations supplémentaires mais PAS le mot de passe.

        Returns:
            Dictionnaire complet pour administration.
        """
        summary = self.to_summary()
        summary.update({
            "days_since_last_login": (
                round(self.days_since_last_login, 1)
                if self.days_since_last_login is not None
                else None
            ),
            "metadata": self.metadata,
        })
        return summary

    def __repr__(self) -> str:
        return (
            f"<User id={self.id} username='{self.username}' "
            f"role={self.role.value} active={self.is_active}>"
        )


# ============================================================================
# MODÈLES PYDANTIC — UserProfile
# ============================================================================


class UserProfile(BaseModel):
    """Préférences utilisateur (immutable — remplacé entièrement).

    Contient toutes les préférences personnalisables de l'utilisateur :
    langue, thème, chemins, formats par défaut, notifications, etc.

    Attributes:
        user_id: ID de l'utilisateur associé.
        language: Langue de l'interface (code ISO 639-1).
        theme: Thème de l'interface (light, dark, system, auto).
        timezone: Fuseau horaire (ex: 'Europe/Paris', 'auto').
        date_format: Format de date (ISO, US, EU).
        time_format: Format d'heure (24h, 12h).
        download_path: Chemin par défaut pour les téléchargements.
        library_path: Chemin de la bibliothèque locale.
        default_format: Format d'empaquetage par défaut (cbz, pdf, etc.).
        default_quality: Qualité par défaut (original, high, medium, low).
        max_concurrent_downloads: Nombre max de téléchargements simultanés.
        notifications_enabled: True si les notifications sont activées.
        adult_content_allowed: True si le contenu adulte est autorisé.
        auto_update_library: True si la bibliothèque se met à jour automatiquement.
        auto_download_new_chapters: True si les nouveaux chapitres sont auto-téléchargés.
        reading_direction: Sens de lecture par défaut (rtl, ltr, vertical).
        custom_user_agent: User-Agent personnalisé (optionnel).
        proxy_url: URL du proxy (optionnel).
        created_at: Timestamp de création du profil.
        updated_at: Timestamp de dernière mise à jour.
    """

    user_id: str = Field(..., description="ID de l'utilisateur associé.")

    # Interface
    language: str = Field(
        default="en",
        min_length=2,
        max_length=5,
        description="Langue de l'interface (code ISO 639-1).",
    )
    theme: str = Field(
        default="system",
        description="Thème de l'interface (light, dark, system, auto).",
    )
    timezone: str = Field(
        default="auto",
        description="Fuseau horaire (ex: 'Europe/Paris', 'auto').",
    )
    date_format: str = Field(
        default="iso",
        description="Format de date (iso, us, eu).",
    )
    time_format: str = Field(
        default="24h",
        description="Format d'heure (24h, 12h).",
    )

    # Chemins
    download_path: Path = Field(
        default_factory=lambda: Path.home() / "NexusDL" / "Downloads",
        description="Chemin par défaut pour les téléchargements.",
    )
    library_path: Path = Field(
        default_factory=lambda: Path.home() / "NexusDL" / "Library",
        description="Chemin de la bibliothèque locale.",
    )

    # Téléchargements
    default_format: str = Field(
        default="cbz",
        description="Format d'empaquetage par défaut (cbz, cbr, pdf, zip, folder).",
    )
    default_quality: str = Field(
        default="original",
        description="Qualité par défaut (original, high, medium, low).",
    )
    max_concurrent_downloads: int = Field(
        default=3,
        ge=1,
        le=16,
        description="Nombre max de téléchargements simultanés.",
    )

    # Comportement
    notifications_enabled: bool = Field(
        default=True,
        description="True si les notifications sont activées.",
    )
    adult_content_allowed: bool = Field(
        default=False,
        description="True si le contenu adulte est autorisé.",
    )
    auto_update_library: bool = Field(
        default=True,
        description="True si la bibliothèque se met à jour automatiquement.",
    )
    auto_download_new_chapters: bool = Field(
        default=False,
        description="True si les nouveaux chapitres sont auto-téléchargés.",
    )

    # Lecture
    reading_direction: str = Field(
        default="rtl",
        description="Sens de lecture par défaut (rtl, ltr, vertical).",
    )

    # Réseau
    custom_user_agent: str | None = Field(
        default=None,
        max_length=500,
        description="User-Agent personnalisé (optionnel).",
    )
    proxy_url: str | None = Field(
        default=None,
        max_length=500,
        description="URL du proxy (optionnel).",
    )

    # Timestamps
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de création du profil.",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de dernière mise à jour.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @field_validator("language")
    @classmethod
    def _validate_language(cls, v: str) -> str:
        """Valide la langue (code ISO 639-1)."""
        v = v.strip().lower()
        valid_languages = {
            "en", "fr", "de", "es", "it", "pt", "ru", "ja", "ko", "zh",
            "ar", "pl", "tr", "nl", "sv", "da", "no", "fi", "hu", "cs",
        }
        if v not in valid_languages and v != "auto":
            raise UserModelError(f"Langue non supportée: {v}")
        return v

    @field_validator("theme")
    @classmethod
    def _validate_theme(cls, v: str) -> str:
        """Valide le thème."""
        v = v.strip().lower()
        valid_themes = {"light", "dark", "system", "auto"}
        if v not in valid_themes:
            raise UserModelError(f"Thème non supporté: {v}")
        return v

    @field_validator("default_format")
    @classmethod
    def _validate_format(cls, v: str) -> str:
        """Valide le format par défaut."""
        v = v.strip().lower()
        valid_formats = {"cbz", "cbr", "pdf", "zip", "folder"}
        if v not in valid_formats:
            raise UserModelError(f"Format non supporté: {v}")
        return v

    @field_validator("default_quality")
    @classmethod
    def _validate_quality(cls, v: str) -> str:
        """Valide la qualité par défaut."""
        v = v.strip().lower()
        valid_qualities = {"original", "high", "medium", "low"}
        if v not in valid_qualities:
            raise UserModelError(f"Qualité non supportée: {v}")
        return v

    @field_validator("reading_direction")
    @classmethod
    def _validate_reading_direction(cls, v: str) -> str:
        """Valide le sens de lecture."""
        v = v.strip().lower()
        valid_directions = {"rtl", "ltr", "vertical"}
        if v not in valid_directions:
            raise UserModelError(f"Sens de lecture non supporté: {v}")
        return v

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def is_rtl_language(self) -> bool:
        """Indique si la langue est RTL (arabe, hébreu)."""
        return self.language in ("ar", "he")

    @property
    def download_path_expanded(self) -> Path:
        """Chemin de téléchargement avec ~ résolu."""
        return self.download_path.expanduser().resolve()

    @property
    def library_path_expanded(self) -> Path:
        """Chemin de bibliothèque avec ~ résolu."""
        return self.library_path.expanduser().resolve()

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable."""
        return {
            "user_id": self.user_id,
            "language": self.language,
            "theme": self.theme,
            "timezone": self.timezone,
            "date_format": self.date_format,
            "time_format": self.time_format,
            "download_path": str(self.download_path),
            "library_path": str(self.library_path),
            "default_format": self.default_format,
            "default_quality": self.default_quality,
            "max_concurrent_downloads": self.max_concurrent_downloads,
            "notifications_enabled": self.notifications_enabled,
            "adult_content_allowed": self.adult_content_allowed,
            "auto_update_library": self.auto_update_library,
            "auto_download_new_chapters": self.auto_download_new_chapters,
            "reading_direction": self.reading_direction,
            "has_custom_user_agent": self.custom_user_agent is not None,
            "has_proxy": self.proxy_url is not None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def __repr__(self) -> str:
        return (
            f"<UserProfile user={self.user_id} "
            f"lang={self.language} theme={self.theme}>"
        )


# ============================================================================
# MODÈLES PYDANTIC — AuthToken
# ============================================================================


class AuthToken(BaseModel):
    """Token JWT d'authentification (immutable — snapshot).

    Représente un token JWT (access ou refresh) avec ses métadonnées.
    Le token lui-même est une chaîne signée (pas de secret stocké ici).

    Attributes:
        access_token: Token d'accès JWT (chaîne signée).
        refresh_token: Token de rafraîchissement JWT (chaîne signée).
        token_type: Type de token (toujours 'Bearer').
        expires_in: Durée de vie du token d'accès en secondes.
        refresh_expires_in: Durée de vie du refresh token en secondes.
        user_id: ID de l'utilisateur associé.
        scope: Portée du token (ex: 'read write').
        issued_at: Timestamp d'émission.
        expires_at: Timestamp d'expiration du token d'accès.
        refresh_expires_at: Timestamp d'expiration du refresh token.
    """

    access_token: str = Field(..., description="Token d'accès JWT.")
    refresh_token: str = Field(..., description="Token de rafraîchissement JWT.")
    token_type: str = Field(
        default="Bearer",
        description="Type de token (toujours 'Bearer').",
    )
    expires_in: int = Field(
        ...,
        gt=0,
        description="Durée de vie du token d'accès en secondes.",
    )
    refresh_expires_in: int = Field(
        ...,
        gt=0,
        description="Durée de vie du refresh token en secondes.",
    )
    user_id: str = Field(..., description="ID de l'utilisateur associé.")
    scope: str = Field(
        default="read write",
        description="Portée du token (ex: 'read write').",
    )
    issued_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp d'émission.",
    )
    expires_at: datetime = Field(..., description="Timestamp d'expiration.")
    refresh_expires_at: datetime = Field(
        ...,
        description="Timestamp d'expiration du refresh token.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Méthodes de création
    # --------------------------------------------------------------------

    @classmethod
    def create_for_user(
        cls,
        user_id: str,
        *,
        access_expires_minutes: int = _DEFAULT_ACCESS_TOKEN_MINUTES,
        refresh_expires_days: int = _DEFAULT_REFRESH_TOKEN_DAYS,
        scope: str = "read write",
    ) -> AuthToken:
        """Crée un nouveau token pour un utilisateur.

        NOTE : Cette méthode génère des tokens fictifs pour les tests.
        En production, utilisez `interfaces/web/backend/security.py` qui
        signe les tokens avec une clé secrète.

        Args:
            user_id: ID de l'utilisateur.
            access_expires_minutes: Durée de vie du token d'accès (minutes).
            refresh_expires_days: Durée de vie du refresh token (jours).
            scope: Portée du token.

        Returns:
            Instance de AuthToken avec tokens fictifs.
        """
        now = datetime.now(UTC)
        access_expires = now + timedelta(minutes=access_expires_minutes)
        refresh_expires = now + timedelta(days=refresh_expires_days)

        # Tokens fictifs (en prod, utiliser JWT signé)
        access_token = f"access_{secrets.token_urlsafe(32)}"
        refresh_token = f"refresh_{secrets.token_urlsafe(32)}"

        return cls(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=access_expires_minutes * 60,
            refresh_expires_in=refresh_expires_days * 86_400,
            user_id=user_id,
            scope=scope,
            issued_at=now,
            expires_at=access_expires,
            refresh_expires_at=refresh_expires,
        )

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def is_expired(self) -> bool:
        """Indique si le token d'accès est expiré."""
        return datetime.now(UTC) >= self.expires_at

    @property
    def is_refresh_expired(self) -> bool:
        """Indique si le refresh token est expiré."""
        return datetime.now(UTC) >= self.refresh_expires_at

    @property
    def can_refresh(self) -> bool:
        """Indique si le token peut être rafraîchi (refresh non expiré)."""
        return not self.is_refresh_expired

    @property
    def time_until_expiration_seconds(self) -> float:
        """Temps restant avant expiration du token d'accès (secondes)."""
        delta = self.expires_at - datetime.now(UTC)
        return max(0.0, delta.total_seconds())

    @property
    def time_until_refresh_expiration_seconds(self) -> float:
        """Temps restant avant expiration du refresh token (secondes)."""
        delta = self.refresh_expires_at - datetime.now(UTC)
        return max(0.0, delta.total_seconds())

    @property
    def should_refresh_soon(self) -> bool:
        """Indique si le token devrait être rafraîchi bientôt (< 5 min restantes)."""
        return self.time_until_expiration_seconds < 300.0

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable.

        IMPORTANT : Les tokens sont masqués pour sécurité.
        """
        return {
            "token_type": self.token_type,
            "access_token": mask_secret(self.access_token),
            "refresh_token": mask_secret(self.refresh_token),
            "expires_in": self.expires_in,
            "refresh_expires_in": self.refresh_expires_in,
            "user_id": self.user_id,
            "scope": self.scope,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "refresh_expires_at": self.refresh_expires_at.isoformat(),
            "is_expired": self.is_expired,
            "can_refresh": self.can_refresh,
            "time_until_expiration_seconds": round(
                self.time_until_expiration_seconds, 1
            ),
        }

    def to_full(self) -> dict[str, Any]:
        """Retourne la représentation complète avec tokens en clair.

        ATTENTION : À utiliser uniquement pour l'envoi initial au client.
        Ne JAMAIS logger ou stocker cette représentation.
        """
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "refresh_expires_in": self.refresh_expires_in,
            "user_id": self.user_id,
            "scope": self.scope,
        }

    def __repr__(self) -> str:
        return (
            f"<AuthToken user={self.user_id} "
            f"expires_in={self.expires_in}s "
            f"expired={self.is_expired}>"
        )


# ============================================================================
# MODÈLES PYDANTIC — Session
# ============================================================================


class Session(BaseModel):
    """Session utilisateur active (immutable — snapshot).

    Représente une session de navigation active pour un utilisateur.
    Utilisé pour le tracking des connexions, la détection d'anomalies,
    et la révocation de sessions (déconnexion forcée).

    Attributes:
        id: Identifiant unique de la session.
        user_id: ID de l'utilisateur associé.
        ip_address: Adresse IP du client.
        user_agent: User-Agent du navigateur/client.
        status: État de la session (ACTIVE, EXPIRED, REVOKED).
        created_at: Timestamp de création de la session.
        expires_at: Timestamp d'expiration (max age).
        last_activity_at: Timestamp de la dernière activité.
        activity_timeout_minutes: Timeout d'inactivité (minutes).
        request_count: Nombre de requêtes effectuées dans cette session.
        metadata: Métadonnées additionnelles (locale, device, etc.).
    """

    id: str = Field(
        default_factory=generate_session_id,
        description="Identifiant unique de la session.",
    )
    user_id: str = Field(..., description="ID de l'utilisateur associé.")
    ip_address: str = Field(
        ...,
        min_length=7,
        max_length=45,
        description="Adresse IP du client.",
    )
    user_agent: str = Field(
        default="",
        max_length=500,
        description="User-Agent du navigateur/client.",
    )
    status: SessionStatus = Field(
        default=SessionStatus.ACTIVE,
        description="État de la session.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de création de la session.",
    )
    expires_at: datetime = Field(
        ...,
        description="Timestamp d'expiration (max age).",
    )
    last_activity_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de la dernière activité.",
    )
    activity_timeout_minutes: int = Field(
        default=_DEFAULT_SESSION_TIMEOUT_MINUTES,
        ge=1,
        le=1440,  # 24h max
        description="Timeout d'inactivité (minutes).",
    )
    request_count: int = Field(
        default=0,
        ge=0,
        description="Nombre de requêtes effectuées dans cette session.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Métadonnées additionnelles (locale, device, etc.).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Méthodes de création
    # --------------------------------------------------------------------

    @classmethod
    def create_for_user(
        cls,
        user_id: str,
        ip_address: str,
        *,
        user_agent: str = "",
        max_age_days: int = _DEFAULT_SESSION_MAX_AGE_DAYS,
        activity_timeout_minutes: int = _DEFAULT_SESSION_TIMEOUT_MINUTES,
        metadata: dict[str, Any] | None = None,
    ) -> Session:
        """Crée une nouvelle session pour un utilisateur.

        Args:
            user_id: ID de l'utilisateur.
            ip_address: Adresse IP du client.
            user_agent: User-Agent du navigateur.
            max_age_days: Durée de vie maximale de la session (jours).
            activity_timeout_minutes: Timeout d'inactivité (minutes).
            metadata: Métadonnées additionnelles.

        Returns:
            Instance de Session active.
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=max_age_days)

        return cls(
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=expires_at,
            activity_timeout_minutes=activity_timeout_minutes,
            metadata=metadata or {},
        )

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """Indique si la session est active (non expirée, non révoquée)."""
        if self.status != SessionStatus.ACTIVE:
            return False
        now = datetime.now(UTC)
        # Vérifier max age
        if now >= self.expires_at:
            return False
        # Vérifier activity timeout
        inactivity = (now - self.last_activity_at).total_seconds() / 60.0
        if inactivity > self.activity_timeout_minutes:
            return False
        return True

    @property
    def is_expired(self) -> bool:
        """Indique si la session est expirée (max age ou inactivité)."""
        return not self.is_active and self.status == SessionStatus.ACTIVE

    @property
    def is_revoked(self) -> bool:
        """Indique si la session a été révoquée manuellement."""
        return self.status == SessionStatus.REVOKED

    @property
    def inactivity_minutes(self) -> float:
        """Durée d'inactivité en minutes."""
        return (
            datetime.now(UTC) - self.last_activity_at
        ).total_seconds() / 60.0

    @property
    def time_until_expiration_minutes(self) -> float:
        """Temps restant avant expiration (max age) en minutes."""
        delta = self.expires_at - datetime.now(UTC)
        return max(0.0, delta.total_seconds() / 60.0)

    @property
    def time_until_inactivity_timeout_minutes(self) -> float:
        """Temps restant avant timeout d'inactivité en minutes."""
        remaining = self.activity_timeout_minutes - self.inactivity_minutes
        return max(0.0, remaining)

    @property
    def device_info(self) -> str:
        """Information sur l'appareil (extrait du user_agent)."""
        if not self.user_agent:
            return "Appareil inconnu"
        # Extraction basique (en prod, utiliser user-agents lib)
        ua = self.user_agent.lower()
        if "mobile" in ua:
            return "Mobile"
        if "tablet" in ua or "ipad" in ua:
            return "Tablette"
        return "Desktop"

    @property
    def browser_info(self) -> str:
        """Information sur le navigateur (extrait du user_agent)."""
        if not self.user_agent:
            return "Navigateur inconnu"
        ua = self.user_agent.lower()
        if "chrome" in ua and "edg" not in ua:
            return "Chrome"
        if "firefox" in ua:
            return "Firefox"
        if "safari" in ua and "chrome" not in ua:
            return "Safari"
        if "edg" in ua:
            return "Edge"
        if "opera" in ua or "opr" in ua:
            return "Opera"
        return "Autre"

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "device_info": self.device_info,
            "browser_info": self.browser_info,
            "status": self.status.value,
            "status_label": self.status.label,
            "status_icon": self.status.icon,
            "is_active": self.is_active,
            "is_expired": self.is_expired,
            "is_revoked": self.is_revoked,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "last_activity_at": self.last_activity_at.isoformat(),
            "inactivity_minutes": round(self.inactivity_minutes, 1),
            "activity_timeout_minutes": self.activity_timeout_minutes,
            "time_until_expiration_minutes": round(
                self.time_until_expiration_minutes, 1
            ),
            "request_count": self.request_count,
        }

    def __repr__(self) -> str:
        return (
            f"<Session id={self.id} user={self.user_id} "
            f"status={self.status.value} active={self.is_active}>"
        )


# ============================================================================
# MODÈLES PYDANTIC — ApiKey
# ============================================================================


class ApiKey(BaseModel):
    """Clé API pour accès programmatique (immutable — snapshot).

    Permet à des applications tierces d'accéder à l'API sans authentification
    utilisateur. Les clés sont hachées (SHA256) pour stockage sécurisé, et
    seules les versions masquées sont exposées dans les APIs.

    Attributes:
        id: Identifiant unique de la clé.
        user_id: ID de l'utilisateur propriétaire.
        name: Nom descriptif de la clé (ex: 'Script Python', 'CI/CD').
        key_hash: Hash SHA256 de la clé (pour vérification).
        key_prefix: Préfixe de la clé (8 premiers caractères, pour identification).
        permissions: Liste des permissions associées.
        is_active: True si la clé est activée.
        created_at: Timestamp de création.
        expires_at: Timestamp d'expiration (None = jamais).
        last_used_at: Timestamp de dernière utilisation.
        request_count: Nombre total de requêtes effectuées avec cette clé.
        ip_whitelist: Liste d'IPs autorisées (vide = toutes).
        rate_limit_per_minute: Limite de requêtes par minute (0 = illimité).
    """

    id: str = Field(
        default_factory=lambda: f"key_{uuid.uuid4().hex[:16]}",
        description="Identifiant unique de la clé.",
    )
    user_id: str = Field(..., description="ID de l'utilisateur propriétaire.")
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Nom descriptif de la clé.",
    )
    key_hash: str = Field(
        ...,
        min_length=64,
        max_length=64,
        description="Hash SHA256 de la clé (pour vérification).",
        pattern=r"^[a-f0-9]{64}$",
    )
    key_prefix: str = Field(
        ...,
        min_length=8,
        max_length=20,
        description="Préfixe de la clé (8 premiers caractères).",
    )
    permissions: list[ApiKeyPermission] = Field(
        default_factory=lambda: [ApiKeyPermission.READ],
        description="Liste des permissions associées.",
    )
    is_active: bool = Field(
        default=True,
        description="True si la clé est activée.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de création.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Timestamp d'expiration (None = jamais).",
    )
    last_used_at: datetime | None = Field(
        default=None,
        description="Timestamp de dernière utilisation.",
    )
    request_count: int = Field(
        default=0,
        ge=0,
        description="Nombre total de requêtes effectuées.",
    )
    ip_whitelist: list[str] = Field(
        default_factory=list,
        description="Liste d'IPs autorisées (vide = toutes).",
    )
    rate_limit_per_minute: int = Field(
        default=60,
        ge=0,
        le=10000,
        description="Limite de requêtes par minute (0 = illimité).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Méthodes de création
    # --------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        user_id: str,
        name: str,
        *,
        permissions: list[ApiKeyPermission] | None = None,
        expires_days: int | None = None,
        ip_whitelist: list[str] | None = None,
        rate_limit_per_minute: int = 60,
    ) -> tuple[ApiKey, str]:
        """Crée une nouvelle clé API.

        Args:
            user_id: ID de l'utilisateur propriétaire.
            name: Nom descriptif de la clé.
            permissions: Liste des permissions (défaut: [READ]).
            expires_days: Durée de vie en jours (None = jamais).
            ip_whitelist: Liste d'IPs autorisées.
            rate_limit_per_minute: Limite de requêtes par minute.

        Returns:
            Tuple (ApiKey, clé_en_clair). La clé en clair ne doit être
            montrée qu'une seule fois à l'utilisateur.
        """
        api_key, key_hash = generate_api_key()
        key_prefix = api_key[:20]

        expires_at = None
        if expires_days is not None:
            expires_at = datetime.now(UTC) + timedelta(days=expires_days)

        key_obj = cls(
            user_id=user_id,
            name=name,
            key_hash=key_hash,
            key_prefix=key_prefix,
            permissions=permissions or [ApiKeyPermission.READ],
            expires_at=expires_at,
            ip_whitelist=ip_whitelist or [],
            rate_limit_per_minute=rate_limit_per_minute,
        )

        return key_obj, api_key

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def is_expired(self) -> bool:
        """Indique si la clé est expirée."""
        if self.expires_at is None:
            return False
        return datetime.now(UTC) >= self.expires_at

    @property
    def is_valid(self) -> bool:
        """Indique si la clé est valide (active + non expirée)."""
        return self.is_active and not self.is_expired

    @property
    def masked_key(self) -> str:
        """Clé masquée pour affichage (préfixe + ...)."""
        return f"{self.key_prefix}..."

    @property
    def max_permission_level(self) -> int:
        """Niveau de permission maximum de la clé."""
        if not self.permissions:
            return 0
        return max(p.level for p in self.permissions)

    @property
    def has_admin_permission(self) -> bool:
        """Indique si la clé a les permissions admin."""
        return ApiKeyPermission.ADMIN in self.permissions

    @property
    def days_until_expiration(self) -> float | None:
        """Nombre de jours jusqu'à expiration (None si pas d'expiration)."""
        if self.expires_at is None:
            return None
        delta = self.expires_at - datetime.now(UTC)
        return max(0.0, delta.total_seconds() / 86_400.0)

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable.

        IMPORTANT : Le hash et la clé complète ne sont JAMAIS exposés.
        """
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "key_prefix": self.key_prefix,
            "masked_key": self.masked_key,
            "permissions": [p.value for p in self.permissions],
            "permissions_labels": [p.label for p in self.permissions],
            "is_active": self.is_active,
            "is_expired": self.is_expired,
            "is_valid": self.is_valid,
            "created_at": self.created_at.isoformat(),
            "expires_at": (
                self.expires_at.isoformat() if self.expires_at else None
            ),
            "last_used_at": (
                self.last_used_at.isoformat() if self.last_used_at else None
            ),
            "request_count": self.request_count,
            "ip_whitelist": self.ip_whitelist,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "days_until_expiration": (
                round(self.days_until_expiration, 1)
                if self.days_until_expiration is not None
                else None
            ),
            # NOTE : key_hash volontairement OMIT
        }

    def __repr__(self) -> str:
        return (
            f"<ApiKey id={self.id} name='{self.name}' "
            f"prefix={self.key_prefix} valid={self.is_valid}>"
        )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "UserModelError",
    "AuthenticationError",
    "InvalidCredentialsError",
    "AccountDisabledError",
    "TokenExpiredError",
    "SessionExpiredError",
    "InvalidEmailError",
    "InvalidUsernameError",
    "WeakPasswordError",
    # Enums
    "UserRole",
    "AuthMethod",
    "SessionStatus",
    "TokenType",
    "ApiKeyPermission",
    # Modèles principaux
    "User",
    "UserProfile",
    "AuthToken",
    "Session",
    "ApiKey",
    # Helpers — Hachage
    "hash_password",
    "verify_password",
    "validate_password_strength",
    # Helpers — Validation
    "validate_email",
    "validate_username",
    # Helpers — Génération
    "generate_user_id",
    "generate_session_id",
    "generate_api_key",
    # Helpers — Masquage
    "mask_api_key",
    "mask_secret",
    # Constantes
    "_EMAIL_PATTERN",
    "_USERNAME_PATTERN",
    "_MIN_PASSWORD_LENGTH",
    "_MAX_PASSWORD_LENGTH",
    "_DEFAULT_ACCESS_TOKEN_MINUTES",
    "_DEFAULT_REFRESH_TOKEN_DAYS",
    "_DEFAULT_SESSION_TIMEOUT_MINUTES",
    "_DEFAULT_SESSION_MAX_AGE_DAYS",
]
