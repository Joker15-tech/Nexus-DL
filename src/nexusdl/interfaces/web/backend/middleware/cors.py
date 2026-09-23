"""Middleware CORS (Cross-Origin Resource Sharing) pour l'API REST NexusDL.

Ce module fournit un middleware FastAPI complet pour gérer les politiques CORS,
permettant de contrôler précisément les origines, méthodes, et headers autorisés
pour les requêtes cross-origin. Il étend le CORSMiddleware standard de Starlette
avec des fonctionnalités avancées de validation, monitoring, et sécurité.

**Fonctionnalités** :
    - 3 modes CORS : STRICT (whitelist), PERMISSIVE (allow all), CUSTOM (config fine)
    - Validation d'origines avec patterns (wildcards, regex)
    - Configuration granulaire des méthodes, headers, et credentials
    - Preflight caching avec max-age configurable
    - Exposition de headers personnalisés
    - Protection contre DNS rebinding attacks
    - Logging structuré des requêtes CORS
    - Statistiques en temps réel (autorisées/bloquées par origine)
    - Intégration avec EventBus pour monitoring
    - Messages d'erreur traduits (i18n)
    - Configuration par origine spécifique
    - Support des credentials (cookies, Authorization)
    - Headers de sécurité additionnels (X-Content-Type-Options, etc.)

**Architecture** :
    CorsMiddleware (Starlette middleware)
        ├── CorsValidator (validation des origines)
        │   ├── ExactMatch (ex: "https://example.com")
        │   ├── WildcardMatch (ex: "*.example.com")
        │   └── RegexMatch (ex: r"^https://.*\\.example\\.com$")
        ├── CorsConfig (configuration Pydantic)
        ├── CorsStats (statistiques)
        └── CorsRequest (log de requête CORS)

**Modes CORS** :
    - STRICT   : Seules les origines explicitement autorisées sont acceptées
    - PERMISSIVE : Toutes les origines sont acceptées (développement uniquement)
    - CUSTOM   : Configuration fine avec règles par origine

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.web.backend.middleware.cors import (
    ...     CorsMiddleware, CorsConfig, CorsMode,
    ... )
    >>>
    >>> # Configuration stricte (production)
    >>> config = CorsConfig(
    ...     mode=CorsMode.STRICT,
    ...     allowed_origins=[
    ...         "https://nexusdl.dev",
    ...         "https://app.nexusdl.dev",
    ...         "*.nexusdl.com",
    ...     ],
    ...     allow_credentials=True,
    ... )
    >>>
    >>> # Ajouter le middleware à FastAPI
    >>> app = FastAPI()
    >>> app.add_middleware(CorsMiddleware, config=config)
    >>>
    >>> # Configuration permissive (développement)
    >>> dev_config = CorsConfig(mode=CorsMode.PERMISSIVE)
    >>> app.add_middleware(CorsMiddleware, config=dev_config)

Intégration :
    - fastapi                 : Framework web
    - core/config.py          : Configuration globale
    - core/events.py          : EventBus pour monitoring
    - core/logger.py          : Logs
    - core/i18n.py            : Traductions
    - core/exceptions.py      : Exceptions
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, Final
from urllib.parse import urlparse

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.middleware.cors import CORSMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    STARLETTE_AVAILABLE = True
except ImportError:
    STARLETTE_AVAILABLE = False

from nexusdl.core.constants import APP_NAME
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t


# ============================================================================
# CONSTANTES
# ============================================================================


# Headers CORS standards
HEADER_ORIGIN: Final[str] = "Origin"
HEADER_ACCESS_CONTROL_ALLOW_ORIGIN: Final[str] = "Access-Control-Allow-Origin"
HEADER_ACCESS_CONTROL_ALLOW_CREDENTIALS: Final[str] = "Access-Control-Allow-Credentials"
HEADER_ACCESS_CONTROL_ALLOW_METHODS: Final[str] = "Access-Control-Allow-Methods"
HEADER_ACCESS_CONTROL_ALLOW_HEADERS: Final[str] = "Access-Control-Allow-Headers"
HEADER_ACCESS_CONTROL_EXPOSE_HEADERS: Final[str] = "Access-Control-Expose-Headers"
HEADER_ACCESS_CONTROL_MAX_AGE: Final[str] = "Access-Control-Max-Age"
HEADER_ACCESS_CONTROL_REQUEST_METHOD: Final[str] = "Access-Control-Request-Method"
HEADER_ACCESS_CONTROL_REQUEST_HEADERS: Final[str] = "Access-Control-Request-Headers"
HEADER_VARY: Final[str] = "Vary"

# Headers de sécurité additionnels
HEADER_X_CONTENT_TYPE_OPTIONS: Final[str] = "X-Content-Type-Options"
HEADER_X_FRAME_OPTIONS: Final[str] = "X-Frame-Options"
HEADER_X_XSS_PROTECTION: Final[str] = "X-XSS-Protection"
HEADER_REFERRER_POLICY: Final[str] = "Referrer-Policy"

# Méthodes HTTP standards
HTTP_METHODS: Final[frozenset[str]] = frozenset({
    "GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD",
})

# Méthodes autorisées par défaut
DEFAULT_ALLOWED_METHODS: Final[frozenset[str]] = frozenset({
    "GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS",
})

# Headers autorisés par défaut
DEFAULT_ALLOWED_HEADERS: Final[frozenset[str]] = frozenset({
    "Accept",
    "Accept-Language",
    "Content-Language",
    "Content-Type",
    "Authorization",
    "X-Requested-With",
    "X-Request-ID",
    "X-Correlation-ID",
    "X-API-Key",
})

# Headers exposés par défaut
DEFAULT_EXPOSED_HEADERS: Final[frozenset[str]] = frozenset({
    "X-Request-ID",
    "X-Correlation-ID",
    "X-Response-Time",
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
})

# Valeurs par défaut
DEFAULT_MAX_AGE: Final[int] = 600  # 10 minutes
DEFAULT_WILDCARD: Final[str] = "*"

# Patterns pour validation d'origines
ORIGIN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^https?://"
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+"
    r"(?:[A-Z]{2,6}|[A-Z0-9-]{2,})\.?|"
    r"localhost|"
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
    r"(?::\d+)?"
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)

# Origines de développement courantes
DEV_ORIGINS: Final[frozenset[str]] = frozenset({
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8080",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8080",
    "http://127.0.0.1:8000",
})


# ============================================================================
# EXCEPTIONS
# ============================================================================


class CorsError(NexusDLError):
    """Exception de base pour les erreurs CORS."""


class OriginNotAllowedError(CorsError):
    """Exception levée lorsqu'une origine n'est pas autorisée.

    Attributes:
        origin: Origine rejetée.
        allowed_origins: Liste des origines autorisées.
    """

    def __init__(self, origin: str, allowed_origins: list[str] | None = None) -> None:
        msg = t(
            "cors.origin_not_allowed",
            default="Origin '{origin}' is not allowed",
            origin=origin,
        )
        super().__init__(msg)
        self.origin = origin
        self.allowed_origins = allowed_origins or []


class InvalidOriginError(CorsError):
    """Exception levée lorsqu'une origine est invalide.

    Attributes:
        origin: Origine invalide.
        reason: Raison de l'invalidité.
    """

    def __init__(self, origin: str, reason: str = "") -> None:
        msg = f"Origine invalide: {origin!r}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.origin = origin
        self.reason = reason


class CorsConfigError(CorsError):
    """Exception levée lorsqu'une configuration CORS est invalide.

    Attributes:
        field: Champ de configuration invalide.
        reason: Raison de l'erreur.
    """

    def __init__(self, field: str, reason: str = "") -> None:
        msg = f"Configuration CORS invalide: {field}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.field = field
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class CorsMode(str, Enum):
    """Mode de configuration CORS.

    Attributes:
        STRICT: Seules les origines explicitement autorisées sont acceptées.
        PERMISSIVE: Toutes les origines sont acceptées (développement).
        CUSTOM: Configuration fine avec règles par origine.
    """

    STRICT = "strict"
    PERMISSIVE = "permissive"
    CUSTOM = "custom"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            CorsMode.STRICT: t("cors.mode.strict", default="Strict (Whitelist)"),
            CorsMode.PERMISSIVE: t("cors.mode.permissive", default="Permissive (Allow All)"),
            CorsMode.CUSTOM: t("cors.mode.custom", default="Custom Rules"),
        }[self]

    @property
    def is_secure(self) -> bool:
        """Indique si le mode est sécurisé."""
        return self in (CorsMode.STRICT, CorsMode.CUSTOM)


class OriginMatchType(str, Enum):
    """Type de correspondance d'origine.

    Attributes:
        EXACT: Correspondance exacte.
        WILDCARD: Correspondance avec wildcard (ex: "*.example.com").
        REGEX: Correspondance par expression régulière.
        SUBDOMAIN: Correspondance de sous-domaine.
    """

    EXACT = "exact"
    WILDCARD = "wildcard"
    REGEX = "regex"
    SUBDOMAIN = "subdomain"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            OriginMatchType.EXACT: t("cors.match.exact", default="Exact Match"),
            OriginMatchType.WILDCARD: t("cors.match.wildcard", default="Wildcard"),
            OriginMatchType.REGEX: t("cors.match.regex", default="Regex"),
            OriginMatchType.SUBDOMAIN: t("cors.match.subdomain", default="Subdomain"),
        }[self]


class CorsDecision(str, Enum):
    """Décision CORS pour une requête.

    Attributes:
        ALLOWED: Requête autorisée.
        BLOCKED: Requête bloquée.
        PREFLIGHT: Requête preflight (OPTIONS).
        NO_ORIGIN: Pas de header Origin (pas une requête CORS).
    """

    ALLOWED = "allowed"
    BLOCKED = "blocked"
    PREFLIGHT = "preflight"
    NO_ORIGIN = "no_origin"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            CorsDecision.ALLOWED: t("cors.decision.allowed", default="Allowed"),
            CorsDecision.BLOCKED: t("cors.decision.blocked", default="Blocked"),
            CorsDecision.PREFLIGHT: t("cors.decision.preflight", default="Preflight"),
            CorsDecision.NO_ORIGIN: t("cors.decision.no_origin", default="No Origin"),
        }[self]


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class OriginRule(BaseModel):
    """Règle de correspondance pour une origine.

    Attributes:
        pattern: Pattern de l'origine (exact, wildcard, ou regex).
        match_type: Type de correspondance.
        allowed_methods: Méthodes HTTP autorisées pour cette origine.
        allowed_headers: Headers autorisés pour cette origine.
        exposed_headers: Headers exposés pour cette origine.
        allow_credentials: Autoriser les credentials pour cette origine.
        max_age: Max-age du preflight pour cette origine.
        description: Description de la règle.
    """

    pattern: str = Field(..., description="Pattern de l'origine.")
    match_type: OriginMatchType = Field(default=OriginMatchType.EXACT, description="Type de match.")
    allowed_methods: set[str] | None = Field(default=None, description="Méthodes autorisées.")
    allowed_headers: set[str] | None = Field(default=None, description="Headers autorisés.")
    exposed_headers: set[str] | None = Field(default=None, description="Headers exposés.")
    allow_credentials: bool | None = Field(default=None, description="Autoriser credentials.")
    max_age: int | None = Field(default=None, ge=0, description="Max-age preflight.")
    description: str = Field(default="", description="Description.")

    model_config = ConfigDict(extra="forbid")

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, v: str) -> str:
        """Valide le pattern."""
        if not v:
            raise ValueError("Pattern ne peut pas être vide")
        return v

    def matches(self, origin: str) -> bool:
        """Vérifie si une origine correspond à cette règle.

        Args:
            origin: Origine à vérifier.

        Returns:
            True si correspond.
        """
        if self.match_type == OriginMatchType.EXACT:
            return origin == self.pattern

        if self.match_type == OriginMatchType.WILDCARD:
            return _match_wildcard(self.pattern, origin)

        if self.match_type == OriginMatchType.REGEX:
            try:
                return bool(re.match(self.pattern, origin))
            except re.error:
                return False

        if self.match_type == OriginMatchType.SUBDOMAIN:
            return _match_subdomain(self.pattern, origin)

        return False


class CorsConfig(BaseModel):
    """Configuration complète du middleware CORS.

    Attributes:
        enabled: Activer le middleware CORS.
        mode: Mode de configuration.
        allowed_origins: Liste des origines autorisées (patterns acceptés).
        allowed_methods: Méthodes HTTP autorisées.
        allowed_headers: Headers autorisés.
        exposed_headers: Headers exposés au client.
        allow_credentials: Autoriser les credentials.
        max_age: Max-age du preflight en secondes.
        origin_rules: Règles spécifiques par origine.
        add_security_headers: Ajouter des headers de sécurité.
        vary_on_origin: Ajouter 'Origin' au header Vary.
        log_cors_requests: Logger les requêtes CORS.
        emit_events: Émettre des événements sur l'EventBus.
        block_private_networks: Bloquer les réseaux privés.
        allow_localhost_dev: Autoriser localhost en développement.
    """

    enabled: bool = Field(default=True, description="Activer le middleware.")
    mode: CorsMode = Field(default=CorsMode.STRICT, description="Mode CORS.")
    allowed_origins: list[str] = Field(
        default_factory=list,
        description="Origines autorisées.",
    )
    allowed_methods: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_METHODS),
        description="Méthodes autorisées.",
    )
    allowed_headers: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_HEADERS),
        description="Headers autorisés.",
    )
    exposed_headers: list[str] = Field(
        default_factory=lambda: list(DEFAULT_EXPOSED_HEADERS),
        description="Headers exposés.",
    )
    allow_credentials: bool = Field(default=False, description="Autoriser credentials.")
    max_age: int = Field(default=DEFAULT_MAX_AGE, ge=0, le=86400, description="Max-age preflight.")
    origin_rules: list[OriginRule] = Field(default_factory=list, description="Règles par origine.")
    add_security_headers: bool = Field(default=True, description="Ajouter headers sécurité.")
    vary_on_origin: bool = Field(default=True, description="Vary sur Origin.")
    log_cors_requests: bool = Field(default=True, description="Logger requêtes CORS.")
    emit_events: bool = Field(default=False, description="Émettre événements.")
    block_private_networks: bool = Field(default=False, description="Bloquer réseaux privés.")
    allow_localhost_dev: bool = Field(default=False, description="Autoriser localhost dev.")

    model_config = ConfigDict(extra="forbid")

    @field_validator("allowed_methods")
    @classmethod
    def validate_methods(cls, v: list[str]) -> list[str]:
        """Valide les méthodes HTTP."""
        methods = [m.upper() for m in v]
        invalid = [m for m in methods if m not in HTTP_METHODS]
        if invalid:
            raise ValueError(f"Méthodes HTTP invalides: {invalid}")
        return methods

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, v: list[str]) -> list[str]:
        """Valide les origines."""
        validated = []
        for origin in v:
            if origin == DEFAULT_WILDCARD:
                validated.append(origin)
                continue

            # Valider le pattern
            if origin.startswith("*."):
                # Wildcard de sous-domaine
                validated.append(origin)
            elif origin.startswith("^") or origin.endswith("$") or ".*" in origin:
                # Regex
                try:
                    re.compile(origin)
                    validated.append(origin)
                except re.error as e:
                    raise ValueError(f"Regex invalide: {origin} ({e})")
            else:
                # Origine exacte
                if not _is_valid_origin(origin):
                    raise ValueError(f"Origine invalide: {origin}")
                validated.append(origin)

        return validated

    def get_origin_rule(self, origin: str) -> OriginRule | None:
        """Récupère la règle spécifique pour une origine.

        Args:
            origin: Origine à vérifier.

        Returns:
            Règle correspondante ou None.
        """
        for rule in self.origin_rules:
            if rule.matches(origin):
                return rule
        return None

    def is_origin_allowed(self, origin: str) -> bool:
        """Vérifie si une origine est autorisée.

        Args:
            origin: Origine à vérifier.

        Returns:
            True si autorisée.
        """
        if self.mode == CorsMode.PERMISSIVE:
            return True

        if self.mode == CorsMode.STRICT:
            # Vérifier les patterns
            for pattern in self.allowed_origins:
                if pattern == DEFAULT_WILDCARD:
                    return True
                if pattern.startswith("*."):
                    if _match_wildcard(pattern, origin):
                        return True
                elif pattern.startswith("^") or pattern.endswith("$") or ".*" in pattern:
                    try:
                        if re.match(pattern, origin):
                            return True
                    except re.error:
                        continue
                elif pattern == origin:
                    return True

            # Vérifier les règles spécifiques
            if self.get_origin_rule(origin) is not None:
                return True

            # Vérifier localhost en développement
            if self.allow_localhost_dev and _is_localhost(origin):
                return True

            return False

        # Mode CUSTOM : vérifier les règles spécifiques d'abord
        rule = self.get_origin_rule(origin)
        if rule is not None:
            return True

        # Puis les patterns globaux
        return self.is_origin_allowed_global(origin)

    def is_origin_allowed_global(self, origin: str) -> bool:
        """Vérifie si une origine est autorisée par les règles globales.

        Args:
            origin: Origine à vérifier.

        Returns:
            True si autorisée.
        """
        for pattern in self.allowed_origins:
            if pattern == DEFAULT_WILDCARD:
                return True
            if pattern.startswith("*."):
                if _match_wildcard(pattern, origin):
                    return True
            elif pattern.startswith("^") or pattern.endswith("$") or ".*" in pattern:
                try:
                    if re.match(pattern, origin):
                        return True
                except re.error:
                    continue
            elif pattern == origin:
                return True
        return False


class CorsStats(BaseModel):
    """Statistiques du middleware CORS.

    Attributes:
        total_requests: Nombre total de requêtes CORS.
        allowed_requests: Nombre de requêtes autorisées.
        blocked_requests: Nombre de requêtes bloquées.
        preflight_requests: Nombre de requêtes preflight.
        requests_by_origin: Compteur par origine.
        blocked_origins: Liste des origines bloquées.
        started_at: Timestamp de début de collecte.
        last_request_at: Timestamp de la dernière requête.
    """

    total_requests: int = Field(default=0, ge=0)
    allowed_requests: int = Field(default=0, ge=0)
    blocked_requests: int = Field(default=0, ge=0)
    preflight_requests: int = Field(default=0, ge=0)
    requests_by_origin: dict[str, int] = Field(default_factory=dict)
    blocked_origins: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_request_at: datetime | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def block_rate(self) -> float:
        """Taux de blocage (0.0 à 1.0)."""
        if self.total_requests == 0:
            return 0.0
        return self.blocked_requests / self.total_requests

    @property
    def unique_origins(self) -> int:
        """Nombre d'origines uniques."""
        return len(self.requests_by_origin)


class CorsRequestLog(BaseModel):
    """Log d'une requête CORS.

    Attributes:
        timestamp: Timestamp ISO 8601.
        origin: Origine de la requête.
        method: Méthode HTTP.
        path: Chemin de la requête.
        decision: Décision CORS.
        is_preflight: Si c'est une requête preflight.
        allowed_methods: Méthodes autorisées.
        allowed_headers: Headers autorisés.
        client_ip: IP du client.
        user_agent: User-Agent.
        reason: Raison de la décision (si bloqué).
    """

    timestamp: str = Field(..., description="Timestamp ISO 8601.")
    origin: str = Field(..., description="Origine.")
    method: str = Field(..., description="Méthode HTTP.")
    path: str = Field(..., description="Chemin.")
    decision: CorsDecision = Field(..., description="Décision.")
    is_preflight: bool = Field(default=False, description="Preflight.")
    allowed_methods: list[str] = Field(default_factory=list, description="Méthodes autorisées.")
    allowed_headers: list[str] = Field(default_factory=list, description="Headers autorisés.")
    client_ip: str | None = Field(default=None, description="IP client.")
    user_agent: str | None = Field(default=None, description="User-Agent.")
    reason: str | None = Field(default=None, description="Raison.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    def to_text(self) -> str:
        """Convertit en format texte.

        Returns:
            Chaîne formatée.
        """
        parts = [
            f"[{self.timestamp}]",
            f"[CORS]",
            f"[{self.decision.value.upper()}]",
            f"{self.method} {self.path}",
            f"from {self.origin}",
        ]
        if self.reason:
            parts.append(f"({self.reason})")
        return " ".join(parts)

    def to_json(self) -> str:
        """Convertit en format JSON.

        Returns:
            Chaîne JSON.
        """
        import json
        data = {k: v for k, v in self.model_dump().items() if v is not None}
        return json.dumps(data, ensure_ascii=False, default=str)


# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================


def _is_valid_origin(origin: str) -> bool:
    """Vérifie si une origine est valide.

    Args:
        origin: Origine à vérifier.

    Returns:
        True si valide.
    """
    if not origin:
        return False

    # Vérifier le format
    if not ORIGIN_PATTERN.match(origin):
        return False

    # Vérifier le scheme
    parsed = urlparse(origin)
    if parsed.scheme not in ("http", "https"):
        return False

    return True


def _is_localhost(origin: str) -> bool:
    """Vérifie si une origine est localhost.

    Args:
        origin: Origine à vérifier.

    Returns:
        True si localhost.
    """
    parsed = urlparse(origin)
    hostname = parsed.hostname or ""
    return hostname in ("localhost", "127.0.0.1", "::1")


def _is_private_network(origin: str) -> bool:
    """Vérifie si une origine est sur un réseau privé.

    Args:
        origin: Origine à vérifier.

    Returns:
        True si réseau privé.
    """
    parsed = urlparse(origin)
    hostname = parsed.hostname or ""

    # Localhost
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return True

    # Réseaux privés IPv4
    if hostname.startswith(("10.", "192.168.", "172.")):
        parts = hostname.split(".")
        if len(parts) == 4:
            try:
                if parts[0] == "172" and 16 <= int(parts[1]) <= 31:
                    return True
            except ValueError:
                pass

    # IPv6 link-local
    if hostname.startswith("fe80:"):
        return True

    return False


def _match_wildcard(pattern: str, origin: str) -> bool:
    """Vérifie si une origine correspond à un pattern wildcard.

    Supporte :
        - "*.example.com" → "sub.example.com", "a.b.example.com"
        - "https://*.example.com" → "https://sub.example.com"

    Args:
        pattern: Pattern avec wildcard.
        origin: Origine à vérifier.

    Returns:
        True si correspond.
    """
    # Extraire le domaine du pattern
    if "://" in pattern:
        parsed_pattern = urlparse(pattern)
        pattern_domain = parsed_pattern.netloc
        pattern_scheme = parsed_pattern.scheme
    else:
        pattern_domain = pattern
        pattern_scheme = None

    # Extraire le domaine de l'origine
    parsed_origin = urlparse(origin)
    origin_domain = parsed_origin.netloc
    origin_scheme = parsed_origin.scheme

    # Vérifier le scheme si spécifié
    if pattern_scheme and pattern_scheme != origin_scheme:
        return False

    # Vérifier le wildcard
    if pattern_domain.startswith("*."):
        base_domain = pattern_domain[2:]
        # L'origine doit se terminer par le domaine de base
        return origin_domain.endswith(f".{base_domain}") or origin_domain == base_domain

    # Match exact
    return origin_domain == pattern_domain


def _match_subdomain(base_domain: str, origin: str) -> bool:
    """Vérifie si une origine est un sous-domaine d'un domaine de base.

    Args:
        base_domain: Domaine de base (ex: "example.com").
        origin: Origine à vérifier.

    Returns:
        True si sous-domaine.
    """
    parsed = urlparse(origin)
    hostname = parsed.hostname or ""

    if hostname == base_domain:
        return True

    return hostname.endswith(f".{base_domain}")


def _parse_origin(origin: str) -> tuple[str, str, int | None]:
    """Parse une origine en ses composants.

    Args:
        origin: Origine à parser.

    Returns:
        Tuple (scheme, hostname, port).
    """
    parsed = urlparse(origin)
    return parsed.scheme, parsed.hostname or "", parsed.port


def validate_origin(origin: str) -> bool:
    """Valide une origine (fonction publique).

    Args:
        origin: Origine à valider.

    Returns:
        True si valide.
    """
    return _is_valid_origin(origin)


def normalize_origin(origin: str) -> str:
    """Normalise une origine (minuscules, pas de slash final).

    Args:
        origin: Origine à normaliser.

    Returns:
        Origine normalisée.
    """
    origin = origin.strip().lower()
    if origin.endswith("/"):
        origin = origin[:-1]
    return origin


# ============================================================================
# CORS VALIDATOR — Validation des origines
# ============================================================================


class CorsValidator:
    """Validateur d'origines CORS.

    Gère la validation des origines selon la configuration.
    """

    def __init__(self, config: CorsConfig) -> None:
        """Initialise le validateur.

        Args:
            config: Configuration CORS.
        """
        self._config = config
        self._compiled_regex: dict[str, re.Pattern[str]] = {}

        # Pré-compiler les regex
        for pattern in config.allowed_origins:
            if pattern.startswith("^") or pattern.endswith("$") or ".*" in pattern:
                try:
                    self._compiled_regex[pattern] = re.compile(pattern)
                except re.error:
                    pass

    def validate(self, origin: str) -> tuple[bool, str | None]:
        """Valide une origine.

        Args:
            origin: Origine à valider.

        Returns:
            Tuple (is_allowed, reason).
        """
        # Vérifier le format
        if not _is_valid_origin(origin):
            return False, "invalid_format"

        # Vérifier les réseaux privés si bloqués
        if self._config.block_private_networks and _is_private_network(origin):
            return False, "private_network_blocked"

        # Mode permissif : tout est autorisé
        if self._config.mode == CorsMode.PERMISSIVE:
            return True, None

        # Vérifier les règles spécifiques
        rule = self._config.get_origin_rule(origin)
        if rule is not None:
            return True, None

        # Vérifier les patterns globaux
        for pattern in self._config.allowed_origins:
            if pattern == DEFAULT_WILDCARD:
                return True, None

            if pattern.startswith("*."):
                if _match_wildcard(pattern, origin):
                    return True, None
            elif pattern in self._compiled_regex:
                if self._compiled_regex[pattern].match(origin):
                    return True, None
            elif pattern == origin:
                return True, None

        # Vérifier localhost en développement
        if self._config.allow_localhost_dev and _is_localhost(origin):
            return True, None

        return False, "origin_not_allowed"

    def get_rule_for_origin(self, origin: str) -> OriginRule | None:
        """Récupère la règle spécifique pour une origine.

        Args:
            origin: Origine.

        Returns:
            Règle ou None.
        """
        return self._config.get_origin_rule(origin)


# ============================================================================
# MIDDLEWARE — Intégration FastAPI
# ============================================================================


if STARLETTE_AVAILABLE:

    class CorsMiddleware(BaseHTTPMiddleware):
        """Middleware FastAPI pour la gestion CORS.

        Étend le CORSMiddleware standard de Starlette avec des fonctionnalités
        avancées de validation, monitoring, et sécurité.

        Example:
            >>> app = FastAPI()
            >>> config = CorsConfig(
            ...     mode=CorsMode.STRICT,
            ...     allowed_origins=["https://nexusdl.dev"],
            ...     allow_credentials=True,
            ... )
            >>> app.add_middleware(CorsMiddleware, config=config)
        """

        def __init__(
            self,
            app: Any,
            *,
            config: CorsConfig | None = None,
        ) -> None:
            """Initialise le middleware.

            Args:
                app: Application FastAPI.
                config: Configuration CORS.
            """
            super().__init__(app)
            self._config = config or CorsConfig()
            self._validator = CorsValidator(self._config)
            self._stats = CorsStats()
            self._stats_lock = __import__("asyncio").Lock()

            # Logger
            self._logger = logger.bind(module="nexusdl.api.cors")

        async def dispatch(self, request: Request, call_next: Callable) -> Response:
            """Traite une requête HTTP avec gestion CORS.

            Args:
                request: Requête HTTP.
                call_next: Fonction pour appeler le handler suivant.

            Returns:
                Réponse HTTP avec headers CORS.
            """
            # Vérifier si le middleware est activé
            if not self._config.enabled:
                return await call_next(request)

            # Extraire l'origine
            origin = request.headers.get(HEADER_ORIGIN)

            # Pas de header Origin : pas une requête CORS
            if not origin:
                response = await call_next(request)
                if self._config.add_security_headers:
                    self._add_security_headers(response)
                return response

            # Normaliser l'origine
            origin = normalize_origin(origin)

            # Valider l'origine
            is_allowed, reason = self._validator.validate(origin)

            # Requêtes preflight (OPTIONS)
            if request.method == "OPTIONS":
                return await self._handle_preflight(request, origin, is_allowed, reason)

            # Requêtes simples
            response = await call_next(request)

            # Ajouter les headers CORS
            self._add_cors_headers(response, origin, is_allowed)

            # Ajouter les headers de sécurité
            if self._config.add_security_headers:
                self._add_security_headers(response)

            # Logger la requête
            if self._config.log_cors_requests:
                await self._log_cors_request(request, origin, is_allowed, reason)

            # Mettre à jour les statistiques
            await self._update_stats(origin, is_allowed, False)

            # Émettre un événement
            if self._config.emit_events:
                await self._emit_event(request, origin, is_allowed, reason)

            return response

        async def _handle_preflight(
            self,
            request: Request,
            origin: str,
            is_allowed: bool,
            reason: str | None,
        ) -> Response:
            """Gère une requête preflight (OPTIONS).

            Args:
                request: Requête HTTP.
                origin: Origine de la requête.
                is_allowed: Si l'origine est autorisée.
                reason: Raison du blocage (si applicable).

            Returns:
                Réponse preflight avec headers CORS.
            """
            # Mettre à jour les statistiques
            await self._update_stats(origin, is_allowed, True)

            if not is_allowed:
                # Logger le blocage
                if self._config.log_cors_requests:
                    await self._log_cors_request(request, origin, False, reason, preflight=True)

                # Émettre un événement
                if self._config.emit_events:
                    await self._emit_event(request, origin, False, reason, preflight=True)

                # Retourner 403
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "cors_origin_not_allowed",
                        "message": t(
                            "cors.error.preflight_blocked",
                            default="Origin '{origin}' is not allowed",
                            origin=origin,
                        ),
                    },
                )

            # Déterminer les méthodes et headers autorisés
            rule = self._validator.get_rule_for_origin(origin)
            allowed_methods = rule.allowed_methods if rule and rule.allowed_methods else self._config.allowed_methods
            allowed_headers = rule.allowed_headers if rule and rule.allowed_headers else self._config.allowed_headers
            max_age = rule.max_age if rule and rule.max_age is not None else self._config.max_age
            allow_credentials = rule.allow_credentials if rule and rule.allow_credentials is not None else self._config.allow_credentials

            # Construire la réponse
            response = Response(status_code=204)

            # Headers CORS
            if self._config.mode == CorsMode.PERMISSIVE and not allow_credentials:
                response.headers[HEADER_ACCESS_CONTROL_ALLOW_ORIGIN] = DEFAULT_WILDCARD
            else:
                response.headers[HEADER_ACCESS_CONTROL_ALLOW_ORIGIN] = origin

            response.headers[HEADER_ACCESS_CONTROL_ALLOW_METHODS] = ", ".join(allowed_methods)
            response.headers[HEADER_ACCESS_CONTROL_ALLOW_HEADERS] = ", ".join(allowed_headers)
            response.headers[HEADER_ACCESS_CONTROL_MAX_AGE] = str(max_age)

            if allow_credentials:
                response.headers[HEADER_ACCESS_CONTROL_ALLOW_CREDENTIALS] = "true"

            if self._config.vary_on_origin:
                response.headers[HEADER_VARY] = "Origin"

            # Headers de sécurité
            if self._config.add_security_headers:
                self._add_security_headers(response)

            # Logger
            if self._config.log_cors_requests:
                await self._log_cors_request(request, origin, True, None, preflight=True)

            # Émettre un événement
            if self._config.emit_events:
                await self._emit_event(request, origin, True, None, preflight=True)

            return response

        def _add_cors_headers(
            self,
            response: Response,
            origin: str,
            is_allowed: bool,
        ) -> None:
            """Ajoute les headers CORS à la réponse.

            Args:
                response: Réponse HTTP.
                origin: Origine de la requête.
                is_allowed: Si l'origine est autorisée.
            """
            if not is_allowed:
                return

            # Déterminer les valeurs
            rule = self._validator.get_rule_for_origin(origin)
            exposed_headers = rule.exposed_headers if rule and rule.exposed_headers else self._config.exposed_headers
            allow_credentials = rule.allow_credentials if rule and rule.allow_credentials is not None else self._config.allow_credentials

            # Access-Control-Allow-Origin
            if self._config.mode == CorsMode.PERMISSIVE and not allow_credentials:
                response.headers[HEADER_ACCESS_CONTROL_ALLOW_ORIGIN] = DEFAULT_WILDCARD
            else:
                response.headers[HEADER_ACCESS_CONTROL_ALLOW_ORIGIN] = origin

            # Access-Control-Allow-Credentials
            if allow_credentials:
                response.headers[HEADER_ACCESS_CONTROL_ALLOW_CREDENTIALS] = "true"

            # Access-Control-Expose-Headers
            if exposed_headers:
                response.headers[HEADER_ACCESS_CONTROL_EXPOSE_HEADERS] = ", ".join(exposed_headers)

            # Vary
            if self._config.vary_on_origin:
                existing_vary = response.headers.get(HEADER_VARY, "")
                if "Origin" not in existing_vary:
                    if existing_vary:
                        response.headers[HEADER_VARY] = f"{existing_vary}, Origin"
                    else:
                        response.headers[HEADER_VARY] = "Origin"

        def _add_security_headers(self, response: Response) -> None:
            """Ajoute les headers de sécurité à la réponse.

            Args:
                response: Réponse HTTP.
            """
            response.headers[HEADER_X_CONTENT_TYPE_OPTIONS] = "nosniff"
            response.headers[HEADER_X_FRAME_OPTIONS] = "DENY"
            response.headers[HEADER_X_XSS_PROTECTION] = "1; mode=block"
            response.headers[HEADER_REFERRER_POLICY] = "strict-origin-when-cross-origin"

        async def _log_cors_request(
            self,
            request: Request,
            origin: str,
            is_allowed: bool,
            reason: str | None,
            preflight: bool = False,
        ) -> None:
            """Log une requête CORS.

            Args:
                request: Requête HTTP.
                origin: Origine.
                is_allowed: Si autorisée.
                reason: Raison du blocage.
                preflight: Si preflight.
            """
            decision = (
                CorsDecision.PREFLIGHT if preflight
                else CorsDecision.ALLOWED if is_allowed
                else CorsDecision.BLOCKED
            )

            log_entry = CorsRequestLog(
                timestamp=datetime.now(UTC).isoformat(),
                origin=origin,
                method=request.method,
                path=request.url.path,
                decision=decision,
                is_preflight=preflight,
                client_ip=request.client.host if request.client else None,
                user_agent=request.headers.get("User-Agent"),
                reason=reason,
            )

            level = "info" if is_allowed else "warning"
            log_method = getattr(self._logger, level)
            log_method(log_entry.to_json())

        async def _update_stats(
            self,
            origin: str,
            is_allowed: bool,
            is_preflight: bool,
        ) -> None:
            """Met à jour les statistiques.

            Args:
                origin: Origine.
                is_allowed: Si autorisée.
                is_preflight: Si preflight.
            """
            async with self._stats_lock:
                new_stats = self._stats.model_dump()
                new_stats["total_requests"] += 1

                if is_preflight:
                    new_stats["preflight_requests"] += 1

                if is_allowed:
                    new_stats["allowed_requests"] += 1
                else:
                    new_stats["blocked_requests"] += 1
                    if origin not in new_stats["blocked_origins"]:
                        new_stats["blocked_origins"].append(origin)

                new_stats["requests_by_origin"][origin] = (
                    new_stats["requests_by_origin"].get(origin, 0) + 1
                )
                new_stats["last_request_at"] = datetime.now(UTC)

                self._stats = CorsStats(**new_stats)

        async def _emit_event(
            self,
            request: Request,
            origin: str,
            is_allowed: bool,
            reason: str | None,
            preflight: bool = False,
        ) -> None:
            """Émet un événement sur l'EventBus.

            Args:
                request: Requête HTTP.
                origin: Origine.
                is_allowed: Si autorisée.
                reason: Raison du blocage.
                preflight: Si preflight.
            """
            try:
                event_bus = get_event_bus()
                await event_bus.emit(
                    EventType.CUSTOM,
                    payload={
                        "type": "cors.request",
                        "origin": origin,
                        "method": request.method,
                        "path": request.url.path,
                        "allowed": is_allowed,
                        "preflight": preflight,
                        "reason": reason,
                    },
                    source="interfaces.web.cors",
                )
            except Exception as e:
                logger.debug("Impossible d'émettre l'événement CORS: {}", e)

        async def get_stats(self) -> CorsStats:
            """Récupère les statistiques.

            Returns:
                Statistiques actuelles.
            """
            async with self._stats_lock:
                return self._stats

        async def reset_stats(self) -> None:
            """Réinitialise les statistiques."""
            async with self._stats_lock:
                self._stats = CorsStats()


# ============================================================================
# INSTANCE GLOBALE
# ============================================================================


_cors_middleware: Any = None


def get_cors_middleware() -> Any:
    """Retourne l'instance globale du CorsMiddleware.

    Returns:
        Instance de CorsMiddleware ou None.
    """
    return _cors_middleware


def set_cors_middleware(middleware: Any) -> None:
    """Définit l'instance globale du CorsMiddleware.

    Args:
        middleware: Instance de CorsMiddleware.
    """
    global _cors_middleware
    _cors_middleware = middleware


def reset_cors_middleware() -> None:
    """Réinitialise l'instance globale du CorsMiddleware."""
    global _cors_middleware
    _cors_middleware = None


# ============================================================================
# FONCTIONS HELPERS
# ============================================================================


def create_cors_config(
    *,
    mode: CorsMode = CorsMode.STRICT,
    allowed_origins: list[str] | None = None,
    allow_credentials: bool = False,
    development: bool = False,
) -> CorsConfig:
    """Crée une configuration CORS avec des valeurs par défaut intelligentes.

    Args:
        mode: Mode CORS.
        allowed_origins: Origines autorisées.
        allow_credentials: Autoriser les credentials.
        development: Si True, active le mode développement.

    Returns:
        Instance de CorsConfig.
    """
    if development:
        return CorsConfig(
            mode=CorsMode.PERMISSIVE,
            allowed_origins=list(DEV_ORIGINS),
            allow_credentials=True,
            allow_localhost_dev=True,
            max_age=60,
        )

    return CorsConfig(
        mode=mode,
        allowed_origins=allowed_origins or [],
        allow_credentials=allow_credentials,
    )


def create_dev_cors_config() -> CorsConfig:
    """Crée une configuration CORS pour le développement.

    Returns:
        CorsConfig permissive avec localhost autorisé.
    """
    return create_cors_config(development=True)


def create_prod_cors_config(
    allowed_origins: list[str],
    *,
    allow_credentials: bool = True,
) -> CorsConfig:
    """Crée une configuration CORS pour la production.

    Args:
        allowed_origins: Origines autorisées.
        allow_credentials: Autoriser les credentials.

    Returns:
        CorsConfig stricte.
    """
    return CorsConfig(
        mode=CorsMode.STRICT,
        allowed_origins=allowed_origins,
        allow_credentials=allow_credentials,
        max_age=3600,
        block_private_networks=True,
    )


async def get_cors_stats() -> CorsStats:
    """Récupère les statistiques CORS.

    Returns:
        Statistiques actuelles.
    """
    middleware = get_cors_middleware()
    if middleware is None:
        return CorsStats()
    return await middleware.get_stats()


async def reset_cors_stats() -> None:
    """Réinitialise les statistiques CORS."""
    middleware = get_cors_middleware()
    if middleware is not None:
        await middleware.reset_stats()


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "HEADER_ORIGIN",
    "HEADER_ACCESS_CONTROL_ALLOW_ORIGIN",
    "HEADER_ACCESS_CONTROL_ALLOW_CREDENTIALS",
    "HEADER_ACCESS_CONTROL_ALLOW_METHODS",
    "HEADER_ACCESS_CONTROL_ALLOW_HEADERS",
    "HEADER_ACCESS_CONTROL_EXPOSE_HEADERS",
    "HEADER_ACCESS_CONTROL_MAX_AGE",
    "HTTP_METHODS",
    "DEFAULT_ALLOWED_METHODS",
    "DEFAULT_ALLOWED_HEADERS",
    "DEFAULT_EXPOSED_HEADERS",
    "DEFAULT_MAX_AGE",
    "DEV_ORIGINS",
    # Exceptions
    "CorsError",
    "OriginNotAllowedError",
    "InvalidOriginError",
    "CorsConfigError",
    # Enums
    "CorsMode",
    "OriginMatchType",
    "CorsDecision",
    # Modèles
    "OriginRule",
    "CorsConfig",
    "CorsStats",
    "CorsRequestLog",
    # Helpers
    "validate_origin",
    "normalize_origin",
    # Classes
    "CorsValidator",
    "CorsMiddleware" if STARLETTE_AVAILABLE else None,
    # Instance globale
    "get_cors_middleware",
    "set_cors_middleware",
    "reset_cors_middleware",
    # Fonctions helpers
    "create_cors_config",
    "create_dev_cors_config",
    "create_prod_cors_config",
    "get_cors_stats",
    "reset_cors_stats",
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
