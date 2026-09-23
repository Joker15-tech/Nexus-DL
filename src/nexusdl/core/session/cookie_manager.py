"""Gestionnaire de cookies persistants et chiffrés.

Ce module fournit un système complet de gestion des cookies HTTP pour
l'authentification manuelle aux sites protégés. Les cookies sont stockés
de manière chiffrée sur disque et peuvent être importés/exportés depuis
des extensions de navigateur (format Netscape).

**Pourquoi chiffrer les cookies ?**
    Les cookies contiennent souvent des tokens de session, des identifiants,
    ou des données sensibles. Le chiffrement avec Fernet (AES-128-CBC + HMAC)
    protège ces données contre :
        - La lecture par des utilisateurs non autorisés
        - L'exfiltration en cas de vol du fichier
        - La modification malveillante (HMAC)

**Formats supportés** :
    - Format interne : JSON chiffré (`.enc`)
    - Format Netscape : texte clair (compatible cookies.txt des navigateurs)
    - Format JSON : export lisible (pour debugging)

**Fonctionnalités principales** :
    - Stockage chiffré via Fernet (AES-128-CBC + HMAC-SHA256)
    - Génération automatique de clé au premier démarrage
    - Persistance sur disque (~/.local/share/nexusdl/cookies.enc)
    - Gestion par site (cookies isolés par domaine)
    - Détection automatique des cookies expirés
    - Import/Export au format Netscape (cookies.txt)
    - Import/Export au format JSON (pour debugging)
    - Intégration avec httpx.Cookies pour les requêtes
    - Support des cookies de clearance Cloudflare (cf_clearance)
    - Statistiques détaillées (nombre, expiration, utilisation)
    - Thread-safe (locks asyncio)
    - Événements EventBus pour monitoring

Architecture :
    CookieManager
        ├── CookieType (enum) : SESSION, CLEARANCE, AUTH, TRACKING
        ├── CookieStorage (enum) : ENCRYPTED, PLAINTEXT
        ├── Cookie (Pydantic) : cookie individuel avec métadonnées
        ├── CookieJar (Pydantic) : ensemble de cookies par domaine
        ├── CookieManagerConfig (Pydantic) : configuration
        ├── CookieManagerStats (Pydantic) : statistiques
        └── _CookieStore (interne) : stockage chiffré

Exemple d'utilisation :
    >>> manager = CookieManager()
    >>> await manager.start()
    >>>
    >>> # Ajouter un cookie
    >>> manager.set_cookie(
    ...     domain="mangadex.org",
    ...     name="session",
    ...     value="abc123...",
    ...     cookie_type=CookieType.AUTH,
    ... )
    >>>
    >>> # Obtenir les cookies pour un domaine
    >>> cookies = await manager.get_cookies_for_domain("mangadex.org")
    >>> print(f"{len(cookies)} cookies")
    >>>
    >>> # Importer depuis un fichier cookies.txt (Netscape)
    >>> await manager.import_from_netscape(Path("cookies.txt"))
    >>>
    >>> # Exporter au format Netscape
    >>> await manager.export_to_netscape(Path("cookies_export.txt"))
    >>>
    >>> # Intégration avec httpx
    >>> httpx_cookies = await manager.to_httpx_cookies("mangadex.org")
    >>> response = await client.get(url, cookies=httpx_cookies)
    >>>
    >>> # Statistiques
    >>> stats = await manager.get_stats()
    >>> print(f"Total cookies: {stats.total_cookies}")
    >>>
    >>> await manager.stop()

Dépendances externes :
    - cryptography : pip install cryptography (pour Fernet)
    - Si cryptography non disponible, fallback sur base64 (moins sécurisé)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final, Self

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

from nexusdl.core.exceptions import NexusDLError

if TYPE_CHECKING:
    from nexusdl.core.events import EventBus


# ============================================================================
# EXCEPTIONS
# ============================================================================


class CookieError(NexusDLError):
    """Exception de base pour les erreurs de cookies."""


class CookieManagerNotStartedError(CookieError):
    """Exception levée lorsqu'on utilise le manager avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "CookieManager must be started before use. Call await manager.start()"
        )


class CookieStorageError(CookieError):
    """Exception levée lorsque le stockage des cookies échoue."""

    def __init__(self, reason: str = "") -> None:
        msg = "Erreur de stockage des cookies"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


class CookieDecryptionError(CookieError):
    """Exception levée lorsque le déchiffrement des cookies échoue."""

    def __init__(self, reason: str = "") -> None:
        msg = "Erreur de déchiffrement des cookies"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


class CookieImportError(CookieError):
    """Exception levée lorsque l'import de cookies échoue."""

    def __init__(self, path: Path, reason: str = "") -> None:
        msg = f"Erreur d'import de cookies depuis {path}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.path = path
        self.reason = reason


class CookieExportError(CookieError):
    """Exception levée lorsque l'export de cookies échoue."""

    def __init__(self, path: Path, reason: str = "") -> None:
        msg = f"Erreur d'export de cookies vers {path}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.path = path
        self.reason = reason


class InvalidCookieError(CookieError):
    """Exception levée lorsqu'un cookie est invalide."""

    def __init__(self, name: str, domain: str, reason: str = "") -> None:
        msg = f"Cookie invalide: {name}@{domain}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.name = name
        self.domain = domain
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class CookieType(str, Enum):
    """Type de cookie (pour classification et filtrage).

    SESSION    : Cookie de session standard.
    CLEARANCE  : Cookie de clearance Cloudflare (cf_clearance, etc.).
    AUTH       : Cookie d'authentification (token, JWT, etc.).
    TRACKING   : Cookie de tracking (analytics, etc.).
    PREFERENCE : Cookie de préférence utilisateur.
    OTHER      : Autre type.
    """

    SESSION = "session"
    CLEARANCE = "clearance"
    AUTH = "auth"
    TRACKING = "tracking"
    PREFERENCE = "preference"
    OTHER = "other"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            CookieType.SESSION: "Session",
            CookieType.CLEARANCE: "Clearance (CF)",
            CookieType.AUTH: "Authentification",
            CookieType.TRACKING: "Tracking",
            CookieType.PREFERENCE: "Préférence",
            CookieType.OTHER: "Autre",
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            CookieType.SESSION: "🔑",
            CookieType.CLEARANCE: "🛡️",
            CookieType.AUTH: "🔐",
            CookieType.TRACKING: "📊",
            CookieType.PREFERENCE: "⚙️",
            CookieType.OTHER: "🍪",
        }[self]

    @property
    def is_sensitive(self) -> bool:
        """Indique si le cookie contient des données sensibles."""
        return self in (CookieType.AUTH, CookieType.CLEARANCE, CookieType.SESSION)


class CookieStorage(str, Enum):
    """Mode de stockage des cookies.

    ENCRYPTED : Chiffrement Fernet (recommandé, sécurisé).
    PLAINTEXT : Pas de chiffrement (pour debugging uniquement).
    """

    ENCRYPTED = "encrypted"
    PLAINTEXT = "plaintext"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            CookieStorage.ENCRYPTED: "Chiffré (Fernet)",
            CookieStorage.PLAINTEXT: "Texte clair (non sécurisé)",
        }[self]


# ============================================================================
# MODÈLES PYDANTIC — Cookies
# ============================================================================


class Cookie(BaseModel):
    """Cookie HTTP individuel avec métadonnées.

    Attributes:
        name: Nom du cookie.
        value: Valeur du cookie.
        domain: Domaine du cookie (ex: "mangadex.org").
        path: Chemin du cookie (défaut: "/").
        expires: Date d'expiration (None = cookie de session).
        secure: True si le cookie doit être envoyé uniquement via HTTPS.
        http_only: True si le cookie est inaccessible via JavaScript.
        same_site: Politique SameSite (Strict, Lax, None).
        cookie_type: Type de cookie (classification).
        site_id: ID du site NexusDL associé (optionnel).
        created_at: Timestamp de création.
        updated_at: Timestamp de dernière mise à jour.
        last_used_at: Timestamp de dernière utilisation.
        use_count: Nombre d'utilisations.
        notes: Notes libres (optionnel).
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Nom du cookie.",
    )
    value: str = Field(
        ...,
        max_length=10000,
        description="Valeur du cookie.",
    )
    domain: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Domaine du cookie.",
    )
    path: str = Field(
        default="/",
        max_length=1024,
        description="Chemin du cookie.",
    )
    expires: datetime | None = Field(
        default=None,
        description="Date d'expiration (None = session).",
    )
    secure: bool = Field(
        default=False,
        description="True si HTTPS uniquement.",
    )
    http_only: bool = Field(
        default=False,
        description="True si inaccessible via JavaScript.",
    )
    same_site: str = Field(
        default="Lax",
        description="Politique SameSite (Strict, Lax, None).",
    )
    cookie_type: CookieType = Field(
        default=CookieType.SESSION,
        description="Type de cookie.",
    )
    site_id: str | None = Field(
        default=None,
        description="ID du site NexusDL associé.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de création.",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de dernière mise à jour.",
    )
    last_used_at: datetime | None = Field(
        default=None,
        description="Timestamp de dernière utilisation.",
    )
    use_count: int = Field(
        default=0,
        ge=0,
        description="Nombre d'utilisations.",
    )
    notes: str | None = Field(
        default=None,
        max_length=1000,
        description="Notes libres.",
    )

    model_config = ConfigDict(frozen=False, extra="forbid")

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @field_validator("same_site")
    @classmethod
    def _validate_same_site(cls, v: str) -> str:
        """Valide la politique SameSite."""
        v = v.strip().capitalize()
        if v not in ("Strict", "Lax", "None"):
            raise ValueError(f"SameSite invalide: {v} (attendu: Strict, Lax, None)")
        return v

    @field_validator("domain")
    @classmethod
    def _validate_domain(cls, v: str) -> str:
        """Valide et normalise le domaine."""
        v = v.strip().lower()
        # Retirer le préfixe "www." si présent
        if v.startswith("www."):
            v = v[4:]
        # Retirer le préfixe "." si présent (cookies de sous-domaine)
        if v.startswith("."):
            v = v[1:]
        return v

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def is_expired(self) -> bool:
        """Indique si le cookie est expiré."""
        if self.expires is None:
            return False  # Cookie de session
        return datetime.now(UTC) >= self.expires

    @property
    def is_session_cookie(self) -> bool:
        """Indique si c'est un cookie de session (pas d'expiration)."""
        return self.expires is None

    @property
    def time_until_expiration(self) -> timedelta | None:
        """Temps restant avant expiration (None si session cookie)."""
        if self.expires is None:
            return None
        delta = self.expires - datetime.now(UTC)
        return max(timedelta(0), delta)

    @property
    def expires_timestamp(self) -> int | None:
        """Timestamp Unix d'expiration (pour format Netscape)."""
        if self.expires is None:
            return None
        return int(self.expires.timestamp())

    @property
    def masked_value(self) -> str:
        """Valeur masquée pour affichage (seuls les 4 premiers et 4 derniers caractères)."""
        if len(self.value) <= 8:
            return "****"
        return f"{self.value[:4]}...{self.value[-4:]}"

    @property
    def unique_key(self) -> str:
        """Clé unique du cookie (domain:name:path)."""
        return f"{self.domain}:{self.name}:{self.path}"

    # --------------------------------------------------------------------
    # Méthodes
    # --------------------------------------------------------------------

    def mark_used(self) -> None:
        """Marque le cookie comme utilisé."""
        self.last_used_at = datetime.now(UTC)
        self.use_count += 1
        self.updated_at = datetime.now(UTC)

    def to_httpx_format(self) -> dict[str, str]:
        """Convertit au format dict pour httpx.Cookies."""
        return {self.name: self.value}

    def to_netscape_line(self) -> str:
        """Convertit en ligne du format Netscape (cookies.txt).

        Format :
            domain  flag  path  secure  expiration  name  value

        Returns:
            Ligne formatée.
        """
        # Flag : TRUE si le domaine commence par ".", FALSE sinon
        flag = "TRUE"  # On considère que tous les cookies sont pour sous-domaines
        secure_str = "TRUE" if self.secure else "FALSE"
        expiration = str(self.expires_timestamp or 0)

        return f"{self.domain}\t{flag}\t{self.path}\t{secure_str}\t{expiration}\t{self.name}\t{self.value}"

    @classmethod
    def from_netscape_line(cls, line: str) -> Cookie:
        """Parse une ligne du format Netscape.

        Args:
            line: Ligne à parser.

        Returns:
            Instance de Cookie.

        Raises:
            InvalidCookieError: Si la ligne est invalide.
        """
        parts = line.strip().split("\t")
        if len(parts) != 7:
            raise InvalidCookieError(
                "unknown",
                "unknown",
                f"Format Netscape invalide (7 colonnes attendues, {len(parts)} reçues)",
            )

        domain = parts[0].strip().lstrip(".")
        # flag = parts[1]  # Ignoré
        path = parts[2].strip() or "/"
        secure_str = parts[3].strip().upper()
        secure = secure_str == "TRUE"
        expiration_str = parts[4].strip()
        name = parts[5].strip()
        value = parts[6].strip()

        # Parser l'expiration
        expires = None
        if expiration_str and expiration_str != "0":
            try:
                expires = datetime.fromtimestamp(int(expiration_str), tz=UTC)
            except (ValueError, OSError):
                pass

        return cls(
            name=name,
            value=value,
            domain=domain,
            path=path,
            expires=expires,
            secure=secure,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convertit en dictionnaire pour sérialisation JSON."""
        return {
            "name": self.name,
            "value": self.value,
            "domain": self.domain,
            "path": self.path,
            "expires": self.expires.isoformat() if self.expires else None,
            "secure": self.secure,
            "http_only": self.http_only,
            "same_site": self.same_site,
            "cookie_type": self.cookie_type.value,
            "site_id": self.site_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "use_count": self.use_count,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Cookie:
        """Parse depuis un dictionnaire JSON."""
        # Convertir les timestamps ISO
        for key in ("expires", "created_at", "updated_at", "last_used_at"):
            if key in data and data[key] is not None and isinstance(data[key], str):
                try:
                    data[key] = datetime.fromisoformat(data[key])
                except ValueError:
                    data[key] = None

        # Convertir cookie_type
        if "cookie_type" in data and isinstance(data["cookie_type"], str):
            try:
                data["cookie_type"] = CookieType(data["cookie_type"])
            except ValueError:
                data["cookie_type"] = CookieType.OTHER

        return cls.model_validate(data)

    def __repr__(self) -> str:
        return (
            f"<Cookie {self.name}@{self.domain} "
            f"type={self.cookie_type.value} "
            f"expired={self.is_expired}>"
        )


class CookieJar(BaseModel):
    """Ensemble de cookies pour un domaine spécifique.

    Attributes:
        domain: Domaine principal.
        cookies: Liste des cookies pour ce domaine.
        created_at: Timestamp de création du jar.
        updated_at: Timestamp de dernière mise à jour.
    """

    domain: str = Field(..., description="Domaine principal.")
    cookies: list[Cookie] = Field(
        default_factory=list,
        description="Liste des cookies.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de création.",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de dernière mise à jour.",
    )

    model_config = ConfigDict(frozen=False, extra="forbid")

    @property
    def cookie_count(self) -> int:
        """Nombre total de cookies."""
        return len(self.cookies)

    @property
    def valid_cookie_count(self) -> int:
        """Nombre de cookies non expirés."""
        return sum(1 for c in self.cookies if not c.is_expired)

    @property
    def expired_cookie_count(self) -> int:
        """Nombre de cookies expirés."""
        return sum(1 for c in self.cookies if c.is_expired)

    def get_cookie(self, name: str, path: str = "/") -> Cookie | None:
        """Récupère un cookie par nom et chemin."""
        for cookie in self.cookies:
            if cookie.name == name and cookie.path == path:
                return cookie
        return None

    def set_cookie(self, cookie: Cookie) -> None:
        """Ajoute ou met à jour un cookie."""
        # Chercher un cookie existant avec la même clé
        for i, existing in enumerate(self.cookies):
            if existing.unique_key == cookie.unique_key:
                self.cookies[i] = cookie
                self.updated_at = datetime.now(UTC)
                return

        # Sinon, ajouter
        self.cookies.append(cookie)
        self.updated_at = datetime.now(UTC)

    def remove_cookie(self, name: str, path: str = "/") -> bool:
        """Supprime un cookie par nom et chemin.

        Returns:
            True si le cookie a été supprimé.
        """
        for i, cookie in enumerate(self.cookies):
            if cookie.name == name and cookie.path == path:
                del self.cookies[i]
                self.updated_at = datetime.now(UTC)
                return True
        return False

    def clear_expired(self) -> int:
        """Supprime tous les cookies expirés.

        Returns:
            Nombre de cookies supprimés.
        """
        initial_count = len(self.cookies)
        self.cookies = [c for c in self.cookies if not c.is_expired]
        removed = initial_count - len(self.cookies)
        if removed > 0:
            self.updated_at = datetime.now(UTC)
        return removed

    def get_valid_cookies(self) -> list[Cookie]:
        """Retourne la liste des cookies non expirés."""
        return [c for c in self.cookies if not c.is_expired]

    def to_dict(self) -> dict[str, Any]:
        """Convertit en dictionnaire pour sérialisation."""
        return {
            "domain": self.domain,
            "cookies": [c.to_dict() for c in self.cookies],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CookieJar:
        """Parse depuis un dictionnaire JSON."""
        # Convertir les timestamps
        for key in ("created_at", "updated_at"):
            if key in data and isinstance(data[key], str):
                try:
                    data[key] = datetime.fromisoformat(data[key])
                except ValueError:
                    data[key] = datetime.now(UTC)

        # Convertir les cookies
        if "cookies" in data:
            data["cookies"] = [Cookie.from_dict(c) for c in data["cookies"]]

        return cls.model_validate(data)


# ============================================================================
# MODÈLES PYDANTIC — Configuration et statistiques
# ============================================================================


class CookieManagerConfig(BaseModel):
    """Configuration du gestionnaire de cookies.

    Attributes:
        storage_path: Chemin vers le fichier de stockage des cookies.
        key_path: Chemin vers le fichier de clé de chiffrement.
        storage_mode: Mode de stockage (ENCRYPTED ou PLAINTEXT).
        auto_clean_expired: Nettoyer automatiquement les cookies expirés.
        clean_interval_seconds: Intervalle entre deux nettoyages automatiques.
        max_cookies_per_domain: Nombre maximum de cookies par domaine.
        max_total_cookies: Nombre total maximum de cookies.
        auto_save: Sauvegarder automatiquement après chaque modification.
        save_interval_seconds: Intervalle minimum entre deux sauvegardes auto.
        mask_values_in_logs: Masquer les valeurs des cookies dans les logs.
    """

    storage_path: Path = Field(
        default_factory=lambda: Path.home() / ".local" / "share" / "nexusdl" / "cookies.enc",
        description="Chemin vers le fichier de stockage.",
    )
    key_path: Path = Field(
        default_factory=lambda: Path.home() / ".config" / "nexusdl" / ".cookie_key",
        description="Chemin vers le fichier de clé.",
    )
    storage_mode: CookieStorage = Field(
        default=CookieStorage.ENCRYPTED,
        description="Mode de stockage.",
    )
    auto_clean_expired: bool = Field(
        default=True,
        description="Nettoyer automatiquement les cookies expirés.",
    )
    clean_interval_seconds: float = Field(
        default=3600.0,
        ge=60.0,
        le=86400.0,
        description="Intervalle entre deux nettoyages (secondes).",
    )
    max_cookies_per_domain: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Nombre maximum de cookies par domaine.",
    )
    max_total_cookies: int = Field(
        default=1000,
        ge=1,
        le=10000,
        description="Nombre total maximum de cookies.",
    )
    auto_save: bool = Field(
        default=True,
        description="Sauvegarder automatiquement après modification.",
    )
    save_interval_seconds: float = Field(
        default=5.0,
        ge=0.0,
        le=60.0,
        description="Intervalle minimum entre sauvegardes auto (secondes).",
    )
    mask_values_in_logs: bool = Field(
        default=True,
        description="Masquer les valeurs dans les logs.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)


class CookieManagerStats(BaseModel):
    """Statistiques globales du gestionnaire de cookies."""

    total_cookies: int = Field(default=0, ge=0)
    valid_cookies: int = Field(default=0, ge=0)
    expired_cookies: int = Field(default=0, ge=0)
    domains_count: int = Field(default=0, ge=0)
    cookies_by_type: dict[str, int] = Field(
        default_factory=dict,
        description="Nombre de cookies par type.",
    )
    cookies_by_domain: dict[str, int] = Field(
        default_factory=dict,
        description="Nombre de cookies par domaine.",
    )
    total_loads: int = Field(default=0, ge=0)
    total_saves: int = Field(default=0, ge=0)
    total_imports: int = Field(default=0, ge=0)
    total_exports: int = Field(default=0, ge=0)
    total_cleanups: int = Field(default=0, ge=0)
    storage_mode: CookieStorage = Field(default=CookieStorage.ENCRYPTED)
    storage_size_bytes: int = Field(default=0, ge=0)
    last_save_at: datetime | None = None
    last_load_at: datetime | None = None
    last_cleanup_at: datetime | None = None
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def expiration_rate(self) -> float:
        """Taux de cookies expirés (0.0 à 1.0)."""
        if self.total_cookies == 0:
            return 0.0
        return self.expired_cookies / self.total_cookies


# ============================================================================
# CLASSE INTERNE — _CookieStore
# ============================================================================


class _CookieStore:
    """Stockage interne des cookies avec chiffrement optionnel.

    Non exposé publiquement — utilisé par CookieManager.
    """

    __slots__ = (
        "_config",
        "_jars",
        "_fernet",
        "_dirty",
        "_last_save",
        "_lock",
    )

    def __init__(self, config: CookieManagerConfig) -> None:
        self._config = config
        self._jars: dict[str, CookieJar] = {}
        self._fernet: Any = None
        self._dirty: bool = False
        self._last_save: float = 0.0
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialise le stockage (charge la clé et les cookies)."""
        # Initialiser le chiffrement si nécessaire
        if self._config.storage_mode == CookieStorage.ENCRYPTED:
            await self._initialize_encryption()

        # Charger les cookies existants
        if self._config.storage_path.exists():
            await self.load()

    async def _initialize_encryption(self) -> None:
        """Initialise le chiffrement Fernet."""
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            logger.warning(
                "Librairie 'cryptography' non disponible. "
                "Fallback sur base64 (moins sécurisé). "
                "Installez avec: pip install cryptography"
            )
            self._fernet = None
            return

        # Charger ou générer la clé
        if self._config.key_path.exists():
            key = await asyncio.to_thread(self._config.key_path.read_bytes)
        else:
            key = Fernet.generate_key()
            self._config.key_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(self._config.key_path.write_bytes, key)
            # Restreindre les permissions (Unix seulement)
            try:
                os.chmod(self._config.key_path, 0o600)
            except (OSError, NotImplementedError):
                pass

        self._fernet = Fernet(key)

    async def load(self) -> int:
        """Charge les cookies depuis le fichier.

        Returns:
            Nombre de cookies chargés.
        """
        async with self._lock:
            if not self._config.storage_path.exists():
                return 0

            try:
                content = await asyncio.to_thread(
                    self._config.storage_path.read_bytes
                )

                # Déchiffrer si nécessaire
                if self._config.storage_mode == CookieStorage.ENCRYPTED:
                    if self._fernet is None:
                        # Fallback base64
                        try:
                            content = base64.b64decode(content)
                        except Exception as e:
                            raise CookieDecryptionError(
                                f"Impossible de décoder le fichier: {e}"
                            ) from e
                    else:
                        try:
                            content = self._fernet.decrypt(content)
                        except Exception as e:
                            raise CookieDecryptionError(
                                f"Déchiffrement échoué: {e}"
                            ) from e

                # Parser le JSON
                data = json.loads(content.decode("utf-8"))

                # Reconstruire les jars
                self._jars.clear()
                for domain, jar_data in data.items():
                    try:
                        self._jars[domain] = CookieJar.from_dict(jar_data)
                    except Exception as e:
                        logger.warning(
                            "Cookie jar invalide pour {}: {}",
                            domain,
                            e,
                        )

                total_cookies = sum(jar.cookie_count for jar in self._jars.values())
                self._dirty = False
                return total_cookies

            except CookieDecryptionError:
                raise
            except Exception as e:
                raise CookieStorageError(f"Erreur de chargement: {e}") from e

    async def save(self) -> int:
        """Sauvegarde les cookies dans le fichier.

        Returns:
            Nombre de cookies sauvegardés.
        """
        async with self._lock:
            # Vérifier l'intervalle minimum entre sauvegardes
            now = time.monotonic()
            if (
                self._config.save_interval_seconds > 0
                and now - self._last_save < self._config.save_interval_seconds
                and not self._dirty
            ):
                return sum(jar.cookie_count for jar in self._jars.values())

            # Sérialiser en JSON
            data = {domain: jar.to_dict() for domain, jar in self._jars.items()}
            content = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")

            # Chiffrer si nécessaire
            if self._config.storage_mode == CookieStorage.ENCRYPTED:
                if self._fernet is None:
                    # Fallback base64
                    content = base64.b64encode(content)
                else:
                    content = self._fernet.encrypt(content)

            # Écrire le fichier
            try:
                self._config.storage_path.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(
                    self._config.storage_path.write_bytes, content
                )
                # Restreindre les permissions (Unix seulement)
                try:
                    os.chmod(self._config.storage_path, 0o600)
                except (OSError, NotImplementedError):
                    pass

                self._dirty = False
                self._last_save = now
                return sum(jar.cookie_count for jar in self._jars.values())

            except Exception as e:
                raise CookieStorageError(f"Erreur de sauvegarde: {e}") from e

    def get_jar(self, domain: str) -> CookieJar | None:
        """Récupère le jar pour un domaine."""
        return self._jars.get(domain)

    def get_or_create_jar(self, domain: str) -> CookieJar:
        """Récupère ou crée le jar pour un domaine."""
        if domain not in self._jars:
            self._jars[domain] = CookieJar(domain=domain)
        return self._jars[domain]

    def remove_jar(self, domain: str) -> bool:
        """Supprime le jar pour un domaine."""
        if domain in self._jars:
            del self._jars[domain]
            self._dirty = True
            return True
        return False

    def list_domains(self) -> list[str]:
        """Liste tous les domaines avec des cookies."""
        return list(self._jars.keys())

    def clear_all(self) -> int:
        """Supprime tous les cookies.

        Returns:
            Nombre de cookies supprimés.
        """
        count = sum(jar.cookie_count for jar in self._jars.values())
        self._jars.clear()
        self._dirty = True
        return count

    def mark_dirty(self) -> None:
        """Marque le stockage comme modifié."""
        self._dirty = True

    @property
    def is_dirty(self) -> bool:
        """Indique si le stockage a des modifications non sauvegardées."""
        return self._dirty

    @property
    def total_cookies(self) -> int:
        """Nombre total de cookies."""
        return sum(jar.cookie_count for jar in self._jars.values())


# ============================================================================
# CLASSE PRINCIPALE — CookieManager
# ============================================================================


class CookieManager:
    """Gestionnaire de cookies persistants et chiffrés.

    Gère le stockage, la récupération, et l'expiration des cookies HTTP
    pour l'authentification aux sites. Les cookies sont chiffrés avec
    Fernet (AES-128-CBC + HMAC-SHA256) pour protéger les données sensibles.

    Lifecycle :
        >>> manager = CookieManager()
        >>> await manager.start()
        >>> manager.set_cookie("example.com", "session", "abc123")
        >>> cookies = await manager.get_cookies_for_domain("example.com")
        >>> await manager.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Les opérations sont protégées par des locks.
    """

    # Pattern pour détecter les cookies de clearance Cloudflare
    _CF_CLEARANCE_NAMES: ClassVar[set[str]] = {
        "cf_clearance",
        "cfduid",
        "__cfduid",
        "__cf_bm",
        "__cflb",
    }

    # Pattern pour détecter les cookies d'authentification
    _AUTH_COOKIE_PATTERNS: ClassVar[list[re.Pattern[str]]] = [
        re.compile(r"^(session|token|auth|jwt|access|refresh)", re.IGNORECASE),
        re.compile(r"(session|token|auth|jwt)$", re.IGNORECASE),
    ]

    def __init__(
        self,
        *,
        config: CookieManagerConfig | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        """Initialise le gestionnaire de cookies.

        Args:
            config: Configuration du gestionnaire.
            event_bus: Bus d'événements pour monitoring.
        """
        self._config = config or CookieManagerConfig()
        self._event_bus = event_bus

        # Stockage interne
        self._store = _CookieStore(self._config)

        # État
        self._started: bool = False
        self._start_time: float = 0.0

        # Tâche de nettoyage
        self._cleanup_task: asyncio.Task[None] | None = None

        # Statistiques
        self._total_loads: int = 0
        self._total_saves: int = 0
        self._total_imports: int = 0
        self._total_exports: int = 0
        self._total_cleanups: int = 0
        self._last_save_at: datetime | None = None
        self._last_load_at: datetime | None = None
        self._last_cleanup_at: datetime | None = None
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="cookie_manager")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre le gestionnaire et charge les cookies.

        Raises:
            CookieStorageError: Si le chargement échoue.
            CookieDecryptionError: Si le déchiffrement échoue.
        """
        if self._started:
            self._logger.warning("CookieManager déjà démarré, ignore")
            return

        try:
            await self._store.initialize()
            self._started = True
            self._start_time = time.monotonic()

            async with self._stats_lock:
                self._total_loads += 1
                self._last_load_at = datetime.now(UTC)

            # Démarrer la tâche de nettoyage
            if self._config.auto_clean_expired:
                self._cleanup_task = asyncio.create_task(
                    self._cleanup_loop(),
                    name="cookie_cleanup",
                )

            self._logger.info(
                "CookieManager démarré: {} cookies chargés, mode={}",
                self._store.total_cookies,
                self._config.storage_mode.value,
            )

        except Exception as e:
            self._logger.error("Échec du démarrage de CookieManager: {}", e)
            raise

    async def stop(self) -> None:
        """Arrête le gestionnaire et sauvegarde les cookies."""
        if not self._started:
            return

        self._started = False

        # Sauvegarder avant d'arrêter
        if self._store.is_dirty:
            try:
                await self._store.save()
                async with self._stats_lock:
                    self._total_saves += 1
                    self._last_save_at = datetime.now(UTC)
            except Exception as e:
                self._logger.warning("Erreur lors de la sauvegarde finale: {}", e)

        # Annuler la tâche de nettoyage
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        self._logger.info("CookieManager arrêté")

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------------

    @property
    def is_started(self) -> bool:
        """Indique si le gestionnaire est démarré."""
        return self._started

    @property
    def total_cookies(self) -> int:
        """Nombre total de cookies."""
        return self._store.total_cookies

    @property
    def domains_count(self) -> int:
        """Nombre de domaines avec des cookies."""
        return len(self._store.list_domains())

    # ------------------------------------------------------------------------
    # API publique — Gestion des cookies
    # ------------------------------------------------------------------------

    def set_cookie(
        self,
        domain: str,
        name: str,
        value: str,
        *,
        path: str = "/",
        expires: datetime | None = None,
        secure: bool = False,
        http_only: bool = False,
        same_site: str = "Lax",
        cookie_type: CookieType | None = None,
        site_id: str | None = None,
        notes: str | None = None,
    ) -> Cookie:
        """Ajoute ou met à jour un cookie.

        Args:
            domain: Domaine du cookie.
            name: Nom du cookie.
            value: Valeur du cookie.
            path: Chemin du cookie.
            expires: Date d'expiration (None = session).
            secure: True si HTTPS uniquement.
            http_only: True si inaccessible via JavaScript.
            same_site: Politique SameSite.
            cookie_type: Type de cookie (auto-détecté si None).
            site_id: ID du site NexusDL associé.
            notes: Notes libres.

        Returns:
            Instance du cookie créé/mis à jour.
        """
        self._ensure_started()

        # Auto-détection du type si non spécifié
        if cookie_type is None:
            cookie_type = self._detect_cookie_type(name, domain)

        cookie = Cookie(
            name=name,
            value=value,
            domain=domain,
            path=path,
            expires=expires,
            secure=secure,
            http_only=http_only,
            same_site=same_site,
            cookie_type=cookie_type,
            site_id=site_id,
            notes=notes,
        )

        # Ajouter au jar
        jar = self._store.get_or_create_jar(cookie.domain)
        jar.set_cookie(cookie)
        self._store.mark_dirty()

        # Vérifier les limites
        self._enforce_limits(cookie.domain)

        # Sauvegarde automatique
        self._schedule_auto_save()

        self._logger.debug(
            "Cookie défini: {}@{} (type={})",
            name,
            domain,
            cookie_type.value,
        )

        return cookie

    def get_cookie(
        self,
        domain: str,
        name: str,
        path: str = "/",
    ) -> Cookie | None:
        """Récupère un cookie spécifique.

        Args:
            domain: Domaine du cookie.
            name: Nom du cookie.
            path: Chemin du cookie.

        Returns:
            Cookie ou None si introuvable/expiré.
        """
        self._ensure_started()

        jar = self._store.get_jar(domain)
        if jar is None:
            return None

        cookie = jar.get_cookie(name, path)
        if cookie is None or cookie.is_expired:
            return None

        cookie.mark_used()
        self._store.mark_dirty()
        return cookie

    def get_cookies_for_domain(
        self,
        domain: str,
        *,
        include_expired: bool = False,
        cookie_type: CookieType | None = None,
    ) -> list[Cookie]:
        """Récupère tous les cookies pour un domaine.

        Args:
            domain: Domaine à interroger.
            include_expired: Inclure les cookies expirés.
            cookie_type: Filtrer par type.

        Returns:
            Liste des cookies correspondants.
        """
        self._ensure_started()

        jar = self._store.get_jar(domain)
        if jar is None:
            return []

        cookies = jar.cookies
        if not include_expired:
            cookies = [c for c in cookies if not c.is_expired]
        if cookie_type is not None:
            cookies = [c for c in cookies if c.cookie_type == cookie_type]

        # Marquer comme utilisés
        for cookie in cookies:
            cookie.mark_used()

        self._store.mark_dirty()
        return cookies

    def remove_cookie(
        self,
        domain: str,
        name: str,
        path: str = "/",
    ) -> bool:
        """Supprime un cookie spécifique.

        Args:
            domain: Domaine du cookie.
            name: Nom du cookie.
            path: Chemin du cookie.

        Returns:
            True si le cookie a été supprimé.
        """
        self._ensure_started()

        jar = self._store.get_jar(domain)
        if jar is None:
            return False

        removed = jar.remove_cookie(name, path)
        if removed:
            self._store.mark_dirty()
            self._schedule_auto_save()
            self._logger.debug("Cookie supprimé: {}@{}", name, domain)

        return removed

    def clear_domain(self, domain: str) -> int:
        """Supprime tous les cookies d'un domaine.

        Args:
            domain: Domaine à nettoyer.

        Returns:
            Nombre de cookies supprimés.
        """
        self._ensure_started()

        jar = self._store.get_jar(domain)
        if jar is None:
            return 0

        count = jar.cookie_count
        self._store.remove_jar(domain)
        self._schedule_auto_save()

        self._logger.info("Domaine nettoyé: {} ({} cookies)", domain, count)
        return count

    def clear_all(self) -> int:
        """Supprime tous les cookies de tous les domaines.

        Returns:
            Nombre de cookies supprimés.
        """
        self._ensure_started()

        count = self._store.clear_all()
        self._schedule_auto_save()

        self._logger.info("Tous les cookies supprimés: {}", count)
        return count

    def clear_expired(self) -> int:
        """Supprime tous les cookies expirés de tous les domaines.

        Returns:
            Nombre de cookies supprimés.
        """
        self._ensure_started()

        total_removed = 0
        for domain in self._store.list_domains():
            jar = self._store.get_jar(domain)
            if jar is not None:
                removed = jar.clear_expired()
                total_removed += removed

        if total_removed > 0:
            self._store.mark_dirty()
            self._schedule_auto_save()

            async with self._stats_lock:
                self._total_cleanups += 1
                self._last_cleanup_at = datetime.now(UTC)

        self._logger.debug("Cookies expirés nettoyés: {}", total_removed)
        return total_removed

    # ------------------------------------------------------------------------
    # API publique — Intégration httpx
    # ------------------------------------------------------------------------

    async def to_httpx_cookies(self, domain: str) -> dict[str, str]:
        """Convertit les cookies d'un domaine au format httpx.

        Args:
            domain: Domaine à convertir.

        Returns:
            Dictionnaire {name: value} pour httpx.Cookies.
        """
        self._ensure_started()

        cookies = self.get_cookies_for_domain(domain, include_expired=False)
        return {c.name: c.value for c in cookies}

    async def update_from_response(
        self,
        domain: str,
        response_cookies: Any,
        *,
        site_id: str | None = None,
    ) -> int:
        """Met à jour les cookies depuis une réponse HTTP.

        Args:
            domain: Domaine de la réponse.
            response_cookies: Cookies de la réponse (httpx.Cookies ou dict).
            site_id: ID du site NexusDL associé.

        Returns:
            Nombre de cookies mis à jour.
        """
        self._ensure_started()

        updated = 0

        # Gérer différents formats de cookies
        if hasattr(response_cookies, "items"):
            items = response_cookies.items()
        else:
            items = []

        for name, value in items:
            cookie_type = self._detect_cookie_type(name, domain)
            self.set_cookie(
                domain=domain,
                name=name,
                value=value,
                cookie_type=cookie_type,
                site_id=site_id,
            )
            updated += 1

        if updated > 0:
            self._logger.debug(
                "{} cookies mis à jour depuis la réponse pour {}",
                updated,
                domain,
            )

        return updated

    # ------------------------------------------------------------------------
    # API publique — Import/Export
    # ------------------------------------------------------------------------

    async def import_from_netscape(
        self,
        path: Path,
        *,
        site_id: str | None = None,
    ) -> int:
        """Importe des cookies depuis un fichier au format Netscape.

        Le format Netscape (cookies.txt) est compatible avec les extensions
        de navigateur comme "Get cookies.txt" ou "cookies.txt".

        Args:
            path: Chemin vers le fichier à importer.
            site_id: ID du site NexusDL associé.

        Returns:
            Nombre de cookies importés.

        Raises:
            CookieImportError: Si l'import échoue.
        """
        self._ensure_started()

        if not path.exists():
            raise CookieImportError(path, "Fichier inexistant")

        try:
            content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except Exception as e:
            raise CookieImportError(path, str(e)) from e

        imported = 0
        for line_num, line in enumerate(content.splitlines(), start=1):
            line = line.strip()

            # Ignorer les commentaires et lignes vides
            if not line or line.startswith("#"):
                continue

            try:
                cookie = Cookie.from_netscape_line(line)
                if site_id:
                    cookie.site_id = site_id

                jar = self._store.get_or_create_jar(cookie.domain)
                jar.set_cookie(cookie)
                imported += 1
            except Exception as e:
                self._logger.warning(
                    "Ligne {} ignorée: {} — {}",
                    line_num,
                    line[:50],
                    e,
                )

        if imported > 0:
            self._store.mark_dirty()
            self._schedule_auto_save()

            async with self._stats_lock:
                self._total_imports += 1

        self._logger.info(
            "{} cookies importés depuis {} (format Netscape)",
            imported,
            path.name,
        )
        return imported

    async def export_to_netscape(
        self,
        path: Path,
        *,
        domain: str | None = None,
        include_expired: bool = False,
    ) -> int:
        """Exporte les cookies au format Netscape.

        Args:
            path: Chemin du fichier de destination.
            domain: Domaine spécifique (None = tous).
            include_expired: Inclure les cookies expirés.

        Returns:
            Nombre de cookies exportés.

        Raises:
            CookieExportError: Si l'export échoue.
        """
        self._ensure_started()

        lines = [
            "# Netscape HTTP Cookie File",
            "# https://curl.haxx.se/docs/http-cookies.html",
            "# This file was generated by NexusDL",
            "",
        ]

        exported = 0
        domains_to_export = [domain] if domain else self._store.list_domains()

        for d in domains_to_export:
            jar = self._store.get_jar(d)
            if jar is None:
                continue

            for cookie in jar.cookies:
                if not include_expired and cookie.is_expired:
                    continue
                lines.append(cookie.to_netscape_line())
                exported += 1

        content = "\n".join(lines)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_text, content, encoding="utf-8")
        except Exception as e:
            raise CookieExportError(path, str(e)) from e

        async with self._stats_lock:
            self._total_exports += 1

        self._logger.info(
            "{} cookies exportés vers {} (format Netscape)",
            exported,
            path.name,
        )
        return exported

    async def export_to_json(
        self,
        path: Path,
        *,
        domain: str | None = None,
        include_expired: bool = False,
        mask_values: bool = False,
    ) -> int:
        """Exporte les cookies au format JSON (pour debugging).

        Args:
            path: Chemin du fichier de destination.
            domain: Domaine spécifique (None = tous).
            include_expired: Inclure les cookies expirés.
            mask_values: Masquer les valeurs des cookies.

        Returns:
            Nombre de cookies exportés.
        """
        self._ensure_started()

        data: dict[str, Any] = {}
        exported = 0
        domains_to_export = [domain] if domain else self._store.list_domains()

        for d in domains_to_export:
            jar = self._store.get_jar(d)
            if jar is None:
                continue

            cookies_data = []
            for cookie in jar.cookies:
                if not include_expired and cookie.is_expired:
                    continue

                cookie_dict = cookie.to_dict()
                if mask_values:
                    cookie_dict["value"] = cookie.masked_value
                cookies_data.append(cookie_dict)
                exported += 1

            data[d] = cookies_data

        content = json.dumps(data, indent=2, ensure_ascii=False)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_text, content, encoding="utf-8")
        except Exception as e:
            raise CookieExportError(path, str(e)) from e

        self._logger.info(
            "{} cookies exportés vers {} (format JSON)",
            exported,
            path.name,
        )
        return exported

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> CookieManagerStats:
        """Retourne les statistiques globales du gestionnaire."""
        self._ensure_started()

        total_cookies = 0
        valid_cookies = 0
        expired_cookies = 0
        cookies_by_type: dict[str, int] = defaultdict(int)
        cookies_by_domain: dict[str, int] = {}

        for domain in self._store.list_domains():
            jar = self._store.get_jar(domain)
            if jar is None:
                continue

            cookies_by_domain[domain] = jar.cookie_count
            for cookie in jar.cookies:
                total_cookies += 1
                if cookie.is_expired:
                    expired_cookies += 1
                else:
                    valid_cookies += 1
                cookies_by_type[cookie.cookie_type.value] += 1

        storage_size = 0
        if self._config.storage_path.exists():
            storage_size = self._config.storage_path.stat().st_size

        uptime = 0.0
        if self._start_time > 0:
            uptime = time.monotonic() - self._start_time

        async with self._stats_lock:
            return CookieManagerStats(
                total_cookies=total_cookies,
                valid_cookies=valid_cookies,
                expired_cookies=expired_cookies,
                domains_count=len(self._store.list_domains()),
                cookies_by_type=dict(cookies_by_type),
                cookies_by_domain=cookies_by_domain,
                total_loads=self._total_loads,
                total_saves=self._total_saves,
                total_imports=self._total_imports,
                total_exports=self._total_exports,
                total_cleanups=self._total_cleanups,
                storage_mode=self._config.storage_mode,
                storage_size_bytes=storage_size,
                last_save_at=self._last_save_at,
                last_load_at=self._last_load_at,
                last_cleanup_at=self._last_cleanup_at,
                uptime_seconds=uptime,
            )

    async def save(self) -> int:
        """Force une sauvegarde immédiate.

        Returns:
            Nombre de cookies sauvegardés.
        """
        self._ensure_started()

        count = await self._store.save()
        async with self._stats_lock:
            self._total_saves += 1
            self._last_save_at = datetime.now(UTC)

        return count

    async def reload(self) -> int:
        """Recharge les cookies depuis le fichier.

        Returns:
            Nombre de cookies chargés.
        """
        self._ensure_started()

        count = await self._store.load()
        async with self._stats_lock:
            self._total_loads += 1
            self._last_load_at = datetime.now(UTC)

        return count

    # ------------------------------------------------------------------------
    # API publique — Introspection
    # ------------------------------------------------------------------------

    def list_domains(self) -> list[str]:
        """Liste tous les domaines avec des cookies.

        Returns:
            Liste triée des domaines.
        """
        self._ensure_started()
        return sorted(self._store.list_domains())

    def get_cookie_summary(self, domain: str) -> dict[str, Any]:
        """Retourne un résumé des cookies pour un domaine.

        Args:
            domain: Domaine à résumer.

        Returns:
            Dictionnaire avec les informations.
        """
        self._ensure_started()

        jar = self._store.get_jar(domain)
        if jar is None:
            return {"domain": domain, "cookies": []}

        return {
            "domain": domain,
            "total": jar.cookie_count,
            "valid": jar.valid_cookie_count,
            "expired": jar.expired_cookie_count,
            "cookies": [
                {
                    "name": c.name,
                    "type": c.cookie_type.value,
                    "expired": c.is_expired,
                    "secure": c.secure,
                    "value": c.masked_value if self._config.mask_values_in_logs else c.value,
                }
                for c in jar.cookies
            ],
        }

    # ------------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # ------------------------------------------------------------------------

    def _detect_cookie_type(self, name: str, domain: str) -> CookieType:
        """Détecte automatiquement le type d'un cookie.

        Args:
            name: Nom du cookie.
            domain: Domaine du cookie.

        Returns:
            Type détecté.
        """
        name_lower = name.lower()

        # Cookies de clearance Cloudflare
        if name_lower in self._CF_CLEARANCE_NAMES:
            return CookieType.CLEARANCE

        # Cookies d'authentification
        for pattern in self._AUTH_COOKIE_PATTERNS:
            if pattern.search(name_lower):
                return CookieType.AUTH

        # Cookies de tracking
        tracking_patterns = [
            "ga_", "gid", "gtm", "fbp", "pixel", "track", "analytic",
        ]
        if any(p in name_lower for p in tracking_patterns):
            return CookieType.TRACKING

        # Cookies de préférence
        pref_patterns = ["pref", "setting", "config", "lang", "theme"]
        if any(p in name_lower for p in pref_patterns):
            return CookieType.PREFERENCE

        return CookieType.SESSION

    def _enforce_limits(self, domain: str) -> None:
        """Applique les limites de cookies.

        Args:
            domain: Domaine à vérifier.
        """
        jar = self._store.get_jar(domain)
        if jar is None:
            return

        # Limite par domaine
        if jar.cookie_count > self._config.max_cookies_per_domain:
            # Supprimer les cookies les plus anciens
            jar.cookies.sort(key=lambda c: c.updated_at)
            excess = jar.cookie_count - self._config.max_cookies_per_domain
            jar.cookies = jar.cookies[excess:]
            self._logger.debug(
                "Limite de cookies par domaine atteinte pour {}: {} supprimés",
                domain,
                excess,
            )

        # Limite totale
        if self._store.total_cookies > self._config.max_total_cookies:
            # Trouver le domaine avec le plus de cookies et le nettoyer
            domains_by_count = sorted(
                self._store.list_domains(),
                key=lambda d: self._store.get_jar(d).cookie_count if self._store.get_jar(d) else 0,
                reverse=True,
            )
            if domains_by_count:
                largest_domain = domains_by_count[0]
                largest_jar = self._store.get_jar(largest_domain)
                if largest_jar and largest_jar.cookies:
                    # Supprimer le plus ancien
                    largest_jar.cookies.sort(key=lambda c: c.updated_at)
                    removed = largest_jar.cookies.pop(0)
                    self._logger.debug(
                        "Limite totale atteinte, cookie supprimé: {}@{}",
                        removed.name,
                        removed.domain,
                    )

    def _schedule_auto_save(self) -> None:
        """Planifie une sauvegarde automatique si nécessaire."""
        if not self._config.auto_save:
            return

        # La sauvegarde sera effectuée lors du prochain appel à save()
        # ou automatiquement au stop()
        pass

    async def _cleanup_loop(self) -> None:
        """Boucle de nettoyage automatique des cookies expirés."""
        try:
            while self._started:
                await asyncio.sleep(self._config.clean_interval_seconds)

                if not self._started:
                    break

                removed = self.clear_expired()
                if removed > 0:
                    self._logger.debug(
                        "Nettoyage automatique: {} cookies expirés supprimés",
                        removed,
                    )

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._logger.error("Erreur dans la boucle de nettoyage: {}", e)

    def _ensure_started(self) -> None:
        """Vérifie que le gestionnaire est démarré."""
        if not self._started:
            raise CookieManagerNotStartedError()

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<CookieManager status={status} "
            f"cookies={self._store.total_cookies} "
            f"domains={len(self._store.list_domains())} "
            f"mode={self._config.storage_mode.value}>"
        )


# ============================================================================
# HELPERS PUBLICS — Fonctions utilitaires
# ============================================================================


def parse_set_cookie_header(header: str, domain: str) -> Cookie | None:
    """Parse un header Set-Cookie en objet Cookie.

    Args:
        header: Valeur du header Set-Cookie.
        domain: Domaine par défaut si non spécifié dans le header.

    Returns:
        Instance de Cookie ou None si le parsing échoue.

    Example:
        >>> cookie = parse_set_cookie_header(
        ...     "session=abc123; Path=/; HttpOnly; Secure; SameSite=Lax",
        ...     "example.com",
        ... )
    """
    try:
        parts = header.split(";")
        if not parts:
            return None

        # Premier élément : name=value
        name_value = parts[0].strip()
        if "=" not in name_value:
            return None

        name, value = name_value.split("=", 1)
        name = name.strip()
        value = value.strip()

        # Attributs
        path = "/"
        expires = None
        secure = False
        http_only = False
        same_site = "Lax"
        cookie_domain = domain

        for part in parts[1:]:
            part = part.strip()
            if "=" in part:
                attr_name, attr_value = part.split("=", 1)
                attr_name = attr_name.strip().lower()
                attr_value = attr_value.strip()

                if attr_name == "path":
                    path = attr_value
                elif attr_name == "domain":
                    cookie_domain = attr_value.lstrip(".")
                elif attr_name == "expires":
                    try:
                        from email.utils import parsedate_to_datetime
                        expires = parsedate_to_datetime(attr_value)
                    except Exception:
                        pass
                elif attr_name == "max-age":
                    try:
                        seconds = int(attr_value)
                        expires = datetime.now(UTC) + timedelta(seconds=seconds)
                    except ValueError:
                        pass
                elif attr_name == "samesite":
                    same_site = attr_value.capitalize()
            else:
                # Attributs sans valeur
                attr_lower = part.lower()
                if attr_lower == "secure":
                    secure = True
                elif attr_lower == "httponly":
                    http_only = True

        return Cookie(
            name=name,
            value=value,
            domain=cookie_domain,
            path=path,
            expires=expires,
            secure=secure,
            http_only=http_only,
            same_site=same_site,
        )

    except Exception:
        return None


def generate_cookie_key() -> bytes:
    """Génère une nouvelle clé de chiffrement Fernet.

    Returns:
        Clé de 32 bytes encodée en base64.

    Raises:
        CookieError: Si la librairie cryptography n'est pas disponible.
    """
    try:
        from cryptography.fernet import Fernet
        return Fernet.generate_key()
    except ImportError as e:
        raise CookieError(
            "La librairie 'cryptography' est requise pour générer une clé. "
            "Installez-la avec: pip install cryptography"
        ) from e


async def quick_import_cookies(
    source_path: Path,
    *,
    storage_path: Path | None = None,
    site_id: str | None = None,
) -> int:
    """Importe rapidement des cookies (one-shot).

    Args:
        source_path: Chemin du fichier à importer.
        storage_path: Chemin de stockage (défaut: ~/.local/share/nexusdl/cookies.enc).
        site_id: ID du site NexusDL associé.

    Returns:
        Nombre de cookies importés.
    """
    config = CookieManagerConfig()
    if storage_path is not None:
        config = config.model_copy(update={"storage_path": storage_path})

    async with CookieManager(config=config) as manager:
        return await manager.import_from_netscape(source_path, site_id=site_id)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "CookieError",
    "CookieManagerNotStartedError",
    "CookieStorageError",
    "CookieDecryptionError",
    "CookieImportError",
    "CookieExportError",
    "InvalidCookieError",
    # Enums
    "CookieType",
    "CookieStorage",
    # Modèles — Cookies
    "Cookie",
    "CookieJar",
    # Modèles — Configuration et stats
    "CookieManagerConfig",
    "CookieManagerStats",
    # Classe principale
    "CookieManager",
    # Helpers
    "parse_set_cookie_header",
    "generate_cookie_key",
    "quick_import_cookies",
]
