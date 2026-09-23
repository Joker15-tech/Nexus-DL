"""Middleware de logging pour l'API REST NexusDL.

Ce module fournit un middleware FastAPI complet pour logger toutes les requêtes
HTTP entrantes et les réponses sortantes de manière structurée. Il est conçu
pour le debugging, le monitoring, et l'audit de l'API.

**Fonctionnalités** :
    - Logging structuré de toutes les requêtes/réponses HTTP
    - 3 formats de log : TEXT, JSON, STRUCTURED
    - Génération automatique de request_id (UUID)
    - Propagation du correlation_id pour traçabilité distribuée
    - Masquage des données sensibles (passwords, tokens, API keys)
    - Niveaux de log dynamiques selon le statut HTTP
    - Exclusion de certains endpoints (health, metrics)
    - Mesure précise du temps de traitement (latence)
    - Logging optionnel du corps des requêtes/réponses
    - Intégration avec Loguru pour rotation et compression
    - Intégration avec EventBus pour monitoring
    - Support des headers X-Request-ID et X-Correlation-ID
    - Statistiques en temps réel (requêtes par statut, latence moyenne)
    - Configuration par endpoint ou globale

**Architecture** :
    LoggingMiddleware (Starlette middleware)
        ├── RequestLogger (logique de logging)
        │   ├── LogFormat (TEXT, JSON, STRUCTURED)
        │   ├── mask_sensitive_data()
        │   └── generate_request_id()
        ├── LoggingConfig (configuration Pydantic)
        └── LoggingStats (statistiques)

**Niveaux de log par statut HTTP** :
    - 2xx (Success)  : INFO
    - 3xx (Redirect) : DEBUG
    - 4xx (Client)   : WARNING
    - 5xx (Server)   : ERROR

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.web.backend.middleware.logging import (
    ...     LoggingMiddleware, LoggingConfig, LogFormat,
    ... )
    >>>
    >>> # Configuration globale
    >>> config = LoggingConfig(
    ...     format=LogFormat.JSON,
    ...     log_request_body=True,
    ...     log_response_body=False,
    ...     exclude_paths=["/health", "/metrics"],
    ... )
    >>>
    >>> # Ajouter le middleware à FastAPI
    >>> app = FastAPI()
    >>> app.add_middleware(LoggingMiddleware, config=config)

**Exemple de sortie (JSON)** :
    {
        "timestamp": "2026-09-24T14:30:45.123Z",
        "level": "INFO",
        "request_id": "550e8400-e29b-41d4-a716-446655440000",
        "correlation_id": "abc123",
        "method": "GET",
        "path": "/api/v1/manga/123",
        "query_params": {"include": "chapters"},
        "client_ip": "192.168.1.1",
        "user_agent": "NexusDL/0.1.0",
        "status_code": 200,
        "duration_ms": 42.5,
        "request_size_bytes": 0,
        "response_size_bytes": 1234,
        "module": "api.manga"
    }

Intégration :
    - fastapi                 : Framework web
    - core/logger.py          : Système de logging Loguru
    - core/events.py          : EventBus pour monitoring
    - core/i18n.py            : Traductions
    - core/config.py          : Configuration globale
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, Final

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

try:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.types import Message
    STARLETTE_AVAILABLE = True
except ImportError:
    STARLETTE_AVAILABLE = False

from nexusdl.core.constants import APP_NAME
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# CONSTANTES
# ============================================================================


# Headers pour la traçabilité
HEADER_REQUEST_ID: Final[str] = "X-Request-ID"
HEADER_CORRELATION_ID: Final[str] = "X-Correlation-ID"
HEADER_RESPONSE_TIME: Final[str] = "X-Response-Time"

# Patterns pour masquer les données sensibles
SENSITIVE_KEYS: Final[frozenset[str]] = frozenset({
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "authorization", "auth", "credential", "credentials", "session_id",
    "sessionid", "cookie", "jwt", "bearer", "private_key", "privatekey",
    "access_token", "refresh_token", "csrf_token", "xsrf_token",
})

SENSITIVE_PATTERNS: Final[list[re.Pattern[str]]] = [
    re.compile(r"(password|passwd|pwd|secret|token|api[_-]?key|authorization)\s*[:=]\s*[^\s,}&'\"]+", re.IGNORECASE),
    re.compile(r"bearer\s+[a-zA-Z0-9\-._~+/]+=*", re.IGNORECASE),
    re.compile(r"[a-f0-9]{32,}", re.IGNORECASE),  # Hashes longs
]

# Taille maximale du corps loggué (bytes)
MAX_BODY_LOG_SIZE: Final[int] = 1024  # 1 KB

# Paths exclus par défaut du logging
DEFAULT_EXCLUDED_PATHS: Final[frozenset[str]] = frozenset({
    "/health",
    "/healthz",
    "/readyz",
    "/livez",
    "/metrics",
    "/favicon.ico",
    "/openapi.json",
    "/docs",
    "/redoc",
})

# Seuils de latence pour warning (ms)
LATENCY_WARNING_THRESHOLD_MS: Final[float] = 1000.0
LATENCY_ERROR_THRESHOLD_MS: Final[float] = 5000.0


# ============================================================================
# EXCEPTIONS
# ============================================================================


class LoggingMiddlewareError(NexusDLError):
    """Exception de base pour les erreurs du middleware de logging."""


class LogFormatError(LoggingMiddlewareError):
    """Exception levée lorsqu'un format de log est invalide.

    Attributes:
        format: Format invalide.
        reason: Raison de l'erreur.
    """

    def __init__(self, format: str, reason: str = "") -> None:
        msg = f"Format de log invalide: {format!r}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.format = format
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class LogFormat(str, Enum):
    """Format de log.

    Attributes:
        TEXT: Format texte lisible (pour développement).
        JSON: Format JSON structuré (pour production, intégration ELK).
        STRUCTURED: Format structuré avec tous les détails.
    """

    TEXT = "text"
    JSON = "json"
    STRUCTURED = "structured"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            LogFormat.TEXT: "Text (Human-readable)",
            LogFormat.JSON: "JSON (Machine-readable)",
            LogFormat.STRUCTURED: "Structured (Full details)",
        }[self]


class LogLevelByStatus(str, Enum):
    """Politique de niveau de log par statut HTTP.

    Attributes:
        STRICT: 2xx=INFO, 3xx=DEBUG, 4xx=WARNING, 5xx=ERROR
        LENIENT: 2xx=DEBUG, 3xx=DEBUG, 4xx=INFO, 5xx=WARNING
        MINIMAL: 2xx=DEBUG, 3xx=DEBUG, 4xx=DEBUG, 5xx=ERROR
    """

    STRICT = "strict"
    LENIENT = "lenient"
    MINIMAL = "minimal"

    def get_level(self, status_code: int) -> str:
        """Retourne le niveau de log pour un statut HTTP.

        Args:
            status_code: Code de statut HTTP.

        Returns:
            Niveau de log (DEBUG, INFO, WARNING, ERROR).
        """
        if 200 <= status_code < 300:
            return {
                LogLevelByStatus.STRICT: "INFO",
                LogLevelByStatus.LENIENT: "DEBUG",
                LogLevelByStatus.MINIMAL: "DEBUG",
            }[self]
        elif 300 <= status_code < 400:
            return "DEBUG"
        elif 400 <= status_code < 500:
            return {
                LogLevelByStatus.STRICT: "WARNING",
                LogLevelByStatus.LENIENT: "INFO",
                LogLevelByStatus.MINIMAL: "DEBUG",
            }[self]
        else:  # 5xx
            return {
                LogLevelByStatus.STRICT: "ERROR",
                LogLevelByStatus.LENIENT: "WARNING",
                LogLevelByStatus.MINIMAL: "ERROR",
            }[self]


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class LoggingConfig(BaseModel):
    """Configuration du middleware de logging.

    Attributes:
        enabled: Activer le logging.
        format: Format de log.
        level_policy: Politique de niveau par statut HTTP.
        log_request_body: Logger le corps des requêtes.
        log_response_body: Logger le corps des réponses.
        log_headers: Logger les headers.
        log_query_params: Logger les paramètres de query.
        log_client_ip: Logger l'IP du client.
        log_user_agent: Logger le User-Agent.
        max_body_size: Taille max du corps loggué (bytes).
        exclude_paths: Paths exclus du logging.
        exclude_methods: Méthodes HTTP exclues.
        mask_sensitive_data: Masquer les données sensibles.
        add_request_id: Ajouter un request_id auto-généré.
        propagate_correlation_id: Propager le correlation_id.
        emit_events: Émettre des événements sur l'EventBus.
        latency_warning_threshold_ms: Seuil de latence pour warning.
        latency_error_threshold_ms: Seuil de latence pour error.
        logger_name: Nom du logger Loguru.
    """

    enabled: bool = Field(default=True, description="Activer le logging.")
    format: LogFormat = Field(default=LogFormat.JSON, description="Format de log.")
    level_policy: LogLevelByStatus = Field(
        default=LogLevelByStatus.STRICT,
        description="Politique de niveau par statut.",
    )
    log_request_body: bool = Field(default=False, description="Logger le corps des requêtes.")
    log_response_body: bool = Field(default=False, description="Logger le corps des réponses.")
    log_headers: bool = Field(default=False, description="Logger les headers.")
    log_query_params: bool = Field(default=True, description="Logger les query params.")
    log_client_ip: bool = Field(default=True, description="Logger l'IP du client.")
    log_user_agent: bool = Field(default=True, description="Logger le User-Agent.")
    max_body_size: int = Field(default=MAX_BODY_LOG_SIZE, ge=0, le=1048576, description="Taille max du corps.")
    exclude_paths: set[str] = Field(default_factory=lambda: set(DEFAULT_EXCLUDED_PATHS), description="Paths exclus.")
    exclude_methods: set[str] = Field(default_factory=set, description="Méthodes exclues.")
    mask_sensitive_data: bool = Field(default=True, description="Masquer les données sensibles.")
    add_request_id: bool = Field(default=True, description="Ajouter un request_id.")
    propagate_correlation_id: bool = Field(default=True, description="Propager le correlation_id.")
    emit_events: bool = Field(default=False, description="Émettre des événements.")
    latency_warning_threshold_ms: float = Field(default=LATENCY_WARNING_THRESHOLD_MS, ge=0, description="Seuil warning.")
    latency_error_threshold_ms: float = Field(default=LATENCY_ERROR_THRESHOLD_MS, ge=0, description="Seuil error.")
    logger_name: str = Field(default="nexusdl.api", description="Nom du logger.")

    model_config = ConfigDict(extra="forbid")


class RequestLog(BaseModel):
    """Log structuré d'une requête HTTP.

    Attributes:
        timestamp: Timestamp ISO 8601.
        level: Niveau de log.
        request_id: ID unique de la requête.
        correlation_id: ID de corrélation (optionnel).
        method: Méthode HTTP.
        path: Chemin de la requête.
        query_params: Paramètres de query.
        client_ip: Adresse IP du client.
        user_agent: User-Agent.
        status_code: Code de statut HTTP.
        duration_ms: Durée de traitement en ms.
        request_size_bytes: Taille de la requête.
        response_size_bytes: Taille de la réponse.
        request_body: Corps de la requête (masqué).
        response_body: Corps de la réponse (masqué).
        headers: Headers (masqués).
        error: Message d'erreur (si 5xx).
        module: Module source.
    """

    timestamp: str = Field(..., description="Timestamp ISO 8601.")
    level: str = Field(..., description="Niveau de log.")
    request_id: str = Field(..., description="ID unique de la requête.")
    correlation_id: str | None = Field(default=None, description="ID de corrélation.")
    method: str = Field(..., description="Méthode HTTP.")
    path: str = Field(..., description="Chemin.")
    query_params: dict[str, Any] = Field(default_factory=dict, description="Query params.")
    client_ip: str | None = Field(default=None, description="IP du client.")
    user_agent: str | None = Field(default=None, description="User-Agent.")
    status_code: int = Field(..., description="Code de statut.")
    duration_ms: float = Field(..., ge=0.0, description="Durée en ms.")
    request_size_bytes: int = Field(default=0, ge=0, description="Taille requête.")
    response_size_bytes: int = Field(default=0, ge=0, description="Taille réponse.")
    request_body: str | None = Field(default=None, description="Corps requête.")
    response_body: str | None = Field(default=None, description="Corps réponse.")
    headers: dict[str, str] | None = Field(default=None, description="Headers.")
    error: str | None = Field(default=None, description="Message d'erreur.")
    module: str = Field(default="api", description="Module source.")

    model_config = ConfigDict(extra="forbid")

    def to_text(self) -> str:
        """Convertit en format texte lisible.

        Returns:
            Chaîne formatée.
        """
        parts = [
            f"[{self.timestamp}]",
            f"[{self.level}]",
            f"[{self.request_id[:8]}]",
            f"{self.method} {self.path}",
            f"→ {self.status_code}",
            f"({self.duration_ms:.1f}ms)",
        ]

        if self.client_ip:
            parts.insert(3, f"from {self.client_ip}")

        if self.error:
            parts.append(f"ERROR: {self.error}")

        return " ".join(parts)

    def to_json(self) -> str:
        """Convertit en format JSON.

        Returns:
            Chaîne JSON.
        """
        # Exclure les champs None
        data = {k: v for k, v in self.model_dump().items() if v is not None}
        return json.dumps(data, ensure_ascii=False, default=str)

    def to_structured(self) -> str:
        """Convertit en format structuré (JSON avec tous les détails).

        Returns:
            Chaîne JSON complète.
        """
        return json.dumps(self.model_dump(), ensure_ascii=False, default=str, indent=2)


class LoggingStats(BaseModel):
    """Statistiques du middleware de logging.

    Attributes:
        total_requests: Nombre total de requêtes.
        requests_by_status: Compteur par code de statut.
        requests_by_method: Compteur par méthode HTTP.
        total_duration_ms: Durée totale de traitement.
        max_duration_ms: Durée maximale.
        min_duration_ms: Durée minimale.
        average_duration_ms: Durée moyenne.
        errors_count: Nombre d'erreurs (5xx).
        started_at: Timestamp de début de collecte.
        last_request_at: Timestamp de la dernière requête.
    """

    total_requests: int = Field(default=0, ge=0)
    requests_by_status: dict[int, int] = Field(default_factory=dict)
    requests_by_method: dict[str, int] = Field(default_factory=dict)
    total_duration_ms: float = Field(default=0.0, ge=0.0)
    max_duration_ms: float = Field(default=0.0, ge=0.0)
    min_duration_ms: float = Field(default=float("inf"), ge=0.0)
    average_duration_ms: float = Field(default=0.0, ge=0.0)
    errors_count: int = Field(default=0, ge=0)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_request_at: datetime | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def error_rate(self) -> float:
        """Taux d'erreur (0.0 à 1.0)."""
        if self.total_requests == 0:
            return 0.0
        return self.errors_count / self.total_requests

    @property
    def requests_per_second(self) -> float:
        """Requêtes par seconde."""
        elapsed = (datetime.now(UTC) - self.started_at).total_seconds()
        if elapsed <= 0:
            return 0.0
        return self.total_requests / elapsed


# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================


def generate_request_id() -> str:
    """Génère un ID unique pour une requête.

    Returns:
        UUID v4 sous forme de chaîne.
    """
    return str(uuid.uuid4())


def mask_sensitive_data(data: Any) -> Any:
    """Masque les données sensibles dans une structure.

    Args:
        data: Données à masquer (dict, list, str, etc.).

    Returns:
        Données avec les champs sensibles masqués.
    """
    if isinstance(data, dict):
        masked = {}
        for key, value in data.items():
            if key.lower() in SENSITIVE_KEYS:
                masked[key] = "***MASKED***"
            else:
                masked[key] = mask_sensitive_data(value)
        return masked
    elif isinstance(data, list):
        return [mask_sensitive_data(item) for item in data]
    elif isinstance(data, str):
        # Appliquer les patterns regex
        result = data
        for pattern in SENSITIVE_PATTERNS:
            result = pattern.sub(lambda m: m.group(0)[:10] + "***MASKED***", result)
        return result
    return data


def extract_client_ip(request: Any, *, trust_proxy: bool = False) -> str:
    """Extrait l'adresse IP du client.

    Args:
        request: Requête HTTP.
        trust_proxy: Faire confiance aux headers proxy.

    Returns:
        Adresse IP du client.
    """
    if trust_proxy:
        # Vérifier X-Forwarded-For
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Prendre la première IP (client original)
            return forwarded_for.split(",")[0].strip()

        # Vérifier X-Real-IP
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

    # Fallback sur l'IP directe
    if hasattr(request, "client") and request.client:
        return request.client.host

    return "unknown"


def get_log_level_for_status(status_code: int, policy: LogLevelByStatus) -> str:
    """Détermine le niveau de log pour un statut HTTP.

    Args:
        status_code: Code de statut HTTP.
        policy: Politique de niveaux.

    Returns:
        Niveau de log (DEBUG, INFO, WARNING, ERROR).
    """
    return policy.get_level(status_code)


def should_exclude_path(path: str, exclude_paths: set[str]) -> bool:
    """Vérifie si un path doit être exclu du logging.

    Args:
        path: Chemin de la requête.
        exclude_paths: Ensemble de paths exclus.

    Returns:
        True si le path doit être exclu.
    """
    # Match exact
    if path in exclude_paths:
        return True

    # Match par préfixe (pour les paths avec paramètres)
    for excluded in exclude_paths:
        if excluded.endswith("*"):
            prefix = excluded[:-1]
            if path.startswith(prefix):
                return True

    return False


def truncate_body(body: str, max_size: int) -> str:
    """Tronque un corps de requête/réponse.

    Args:
        body: Corps à tronquer.
        max_size: Taille maximale.

    Returns:
        Corps tronqué avec indication si nécessaire.
    """
    if len(body) <= max_size:
        return body
    return body[:max_size] + f"... [TRUNCATED, {len(body)} bytes total]"


# ============================================================================
# REQUEST LOGGER — Logique de logging
# ============================================================================


class RequestLogger:
    """Logger de requêtes HTTP.

    Gère le formatage et l'écriture des logs de requêtes.
    """

    def __init__(self, config: LoggingConfig) -> None:
        """Initialise le logger.

        Args:
            config: Configuration du logging.
        """
        self._config = config
        self._logger = logger.bind(module=config.logger_name)
        self._stats = LoggingStats()
        self._stats_lock = __import__("asyncio").Lock()

    async def log_request(
        self,
        request: Any,
        response: Any,
        duration_ms: float,
        request_body: str | None = None,
        response_body: str | None = None,
        error: str | None = None,
    ) -> RequestLog:
        """Log une requête HTTP complète.

        Args:
            request: Requête HTTP.
            response: Réponse HTTP.
            duration_ms: Durée de traitement.
            request_body: Corps de la requête (optionnel).
            response_body: Corps de la réponse (optionnel).
            error: Message d'erreur (si 5xx).

        Returns:
            Instance de RequestLog créée.
        """
        # Extraire les informations de la requête
        request_id = self._get_request_id(request)
        correlation_id = self._get_correlation_id(request)
        method = request.method
        path = request.url.path
        query_params = dict(request.query_params) if self._config.log_query_params else {}
        client_ip = extract_client_ip(request, trust_proxy=True) if self._config.log_client_ip else None
        user_agent = request.headers.get("User-Agent") if self._config.log_user_agent else None
        status_code = response.status_code

        # Calculer les tailles
        request_size = len(request_body.encode()) if request_body else 0
        response_size = getattr(response, "content_length", 0) or 0
        if response_body:
            response_size = max(response_size, len(response_body.encode()))

        # Masquer les données sensibles
        if self._config.mask_sensitive_data:
            query_params = mask_sensitive_data(query_params)
            if request_body:
                request_body = mask_sensitive_data(request_body)
            if response_body:
                response_body = mask_sensitive_data(response_body)

        # Tronquer les corps si nécessaire
        if request_body:
            request_body = truncate_body(request_body, self._config.max_body_size)
        if response_body:
            response_body = truncate_body(response_body, self._config.max_body_size)

        # Headers (optionnel)
        headers = None
        if self._config.log_headers:
            headers = dict(request.headers)
            if self._config.mask_sensitive_data:
                headers = mask_sensitive_data(headers)

        # Déterminer le niveau de log
        level = get_log_level_for_status(status_code, self._config.level_policy)

        # Ajuster le niveau selon la latence
        if duration_ms >= self._config.latency_error_threshold_ms:
            level = "ERROR"
        elif duration_ms >= self._config.latency_warning_threshold_ms and level not in ("ERROR",):
            level = "WARNING"

        # Construire le log
        log_entry = RequestLog(
            timestamp=datetime.now(UTC).isoformat(),
            level=level,
            request_id=request_id,
            correlation_id=correlation_id,
            method=method,
            path=path,
            query_params=query_params,
            client_ip=client_ip,
            user_agent=user_agent,
            status_code=status_code,
            duration_ms=duration_ms,
            request_size_bytes=request_size,
            response_size_bytes=response_size,
            request_body=request_body if self._config.log_request_body else None,
            response_body=response_body if self._config.log_response_body else None,
            headers=headers,
            error=error,
            module=self._extract_module(path),
        )

        # Écrire le log
        self._write_log(log_entry)

        # Mettre à jour les statistiques
        await self._update_stats(log_entry)

        # Émettre un événement si configuré
        if self._config.emit_events:
            await self._emit_event(log_entry)

        return log_entry

    def _get_request_id(self, request: Any) -> str:
        """Récupère ou génère le request_id.

        Args:
            request: Requête HTTP.

        Returns:
            Request ID.
        """
        # Vérifier le header X-Request-ID
        request_id = request.headers.get(HEADER_REQUEST_ID)
        if request_id:
            return request_id

        # Vérifier l'état de la requête
        if hasattr(request.state, "request_id"):
            return request.state.request_id

        # Générer un nouveau request_id
        if self._config.add_request_id:
            new_id = generate_request_id()
            try:
                request.state.request_id = new_id
            except Exception:
                pass
            return new_id

        return "no-id"

    def _get_correlation_id(self, request: Any) -> str | None:
        """Récupère le correlation_id.

        Args:
            request: Requête HTTP.

        Returns:
            Correlation ID ou None.
        """
        if not self._config.propagate_correlation_id:
            return None

        # Vérifier le header X-Correlation-ID
        correlation_id = request.headers.get(HEADER_CORRELATION_ID)
        if correlation_id:
            return correlation_id

        # Vérifier l'état de la requête
        if hasattr(request.state, "correlation_id"):
            return request.state.correlation_id

        return None

    def _extract_module(self, path: str) -> str:
        """Extrait le module source depuis le path.

        Args:
            path: Chemin de la requête.

        Returns:
            Nom du module.
        """
        # Enlever le préfixe /api/v1/
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "api":
            return f"api.{parts[2]}" if len(parts) > 2 else "api"
        return ".".join(parts[:2]) if parts else "api"

    def _write_log(self, log_entry: RequestLog) -> None:
        """Écrit le log dans le système de logging.

        Args:
            log_entry: Entrée de log à écrire.
        """
        # Formater selon le format configuré
        if self._config.format == LogFormat.TEXT:
            message = log_entry.to_text()
        elif self._config.format == LogFormat.JSON:
            message = log_entry.to_json()
        else:  # STRUCTURED
            message = log_entry.to_structured()

        # Écrire avec le niveau approprié
        level = log_entry.level.lower()
        log_method = getattr(self._logger, level, self._logger.info)
        log_method(message)

    async def _update_stats(self, log_entry: RequestLog) -> None:
        """Met à jour les statistiques.

        Args:
            log_entry: Entrée de log.
        """
        async with self._stats_lock:
            # Copier les stats actuelles
            new_stats = self._stats.model_dump()

            # Incrémenter les compteurs
            new_stats["total_requests"] += 1
            new_stats["requests_by_status"][log_entry.status_code] = (
                new_stats["requests_by_status"].get(log_entry.status_code, 0) + 1
            )
            new_stats["requests_by_method"][log_entry.method] = (
                new_stats["requests_by_method"].get(log_entry.method, 0) + 1
            )

            # Mettre à jour les durées
            new_stats["total_duration_ms"] += log_entry.duration_ms
            new_stats["max_duration_ms"] = max(
                new_stats["max_duration_ms"],
                log_entry.duration_ms,
            )
            new_stats["min_duration_ms"] = min(
                new_stats["min_duration_ms"],
                log_entry.duration_ms,
            )
            new_stats["average_duration_ms"] = (
                new_stats["total_duration_ms"] / new_stats["total_requests"]
            )

            # Compter les erreurs
            if 500 <= log_entry.status_code < 600:
                new_stats["errors_count"] += 1

            new_stats["last_request_at"] = datetime.now(UTC)

            # Créer la nouvelle instance
            self._stats = LoggingStats(**new_stats)

    async def _emit_event(self, log_entry: RequestLog) -> None:
        """Émet un événement sur l'EventBus.

        Args:
            log_entry: Entrée de log.
        """
        try:
            event_bus = get_event_bus()
            await event_bus.emit(
                EventType.CUSTOM,
                payload={
                    "type": "http.request.logged",
                    "request_id": log_entry.request_id,
                    "method": log_entry.method,
                    "path": log_entry.path,
                    "status_code": log_entry.status_code,
                    "duration_ms": log_entry.duration_ms,
                    "level": log_entry.level,
                },
                source="interfaces.web.logging",
            )
        except Exception as e:
            logger.debug("Impossible d'émettre l'événement de log: {}", e)

    async def get_stats(self) -> LoggingStats:
        """Récupère les statistiques.

        Returns:
            Statistiques actuelles.
        """
        async with self._stats_lock:
            return self._stats

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        async with self._stats_lock:
            self._stats = LoggingStats()


# ============================================================================
# MIDDLEWARE — Intégration FastAPI
# ============================================================================


if STARLETTE_AVAILABLE:

    class LoggingMiddleware(BaseHTTPMiddleware):
        """Middleware FastAPI pour le logging des requêtes HTTP.

        Intercepte toutes les requêtes HTTP et log les informations
        de requête et réponse de manière structurée.

        Example:
            >>> app = FastAPI()
            >>> config = LoggingConfig(format=LogFormat.JSON)
            >>> app.add_middleware(LoggingMiddleware, config=config)
        """

        def __init__(
            self,
            app: Any,
            *,
            config: LoggingConfig | None = None,
            request_logger: RequestLogger | None = None,
        ) -> None:
            """Initialise le middleware.

            Args:
                app: Application FastAPI.
                config: Configuration du logging.
                request_logger: Instance de RequestLogger (optionnel).
            """
            super().__init__(app)
            self._config = config or LoggingConfig()
            self._logger = request_logger or RequestLogger(self._config)

        async def dispatch(self, request: Request, call_next: Callable) -> Response:
            """Traite une requête HTTP.

            Args:
                request: Requête HTTP.
                call_next: Fonction pour appeler le handler suivant.

            Returns:
                Réponse HTTP.
            """
            # Vérifier si le logging est activé
            if not self._config.enabled:
                return await call_next(request)

            # Vérifier si le path est exclu
            if should_exclude_path(request.url.path, self._config.exclude_paths):
                return await call_next(request)

            # Vérifier si la méthode est exclue
            if request.method in self._config.exclude_methods:
                return await call_next(request)

            # Générer/propager le request_id
            request_id = self._get_or_generate_request_id(request)
            try:
                request.state.request_id = request_id
            except Exception:
                pass

            # Propager le correlation_id
            correlation_id = request.headers.get(HEADER_CORRELATION_ID)
            if correlation_id:
                try:
                    request.state.correlation_id = correlation_id
                except Exception:
                    pass

            # Capturer le corps de la requête si configuré
            request_body = None
            if self._config.log_request_body:
                try:
                    body_bytes = await request.body()
                    if body_bytes:
                        request_body = body_bytes.decode("utf-8", errors="replace")
                except Exception as e:
                    logger.debug("Impossible de lire le corps de la requête: {}", e)

            # Mesurer le temps de traitement
            start_time = time.perf_counter()
            error_message = None

            try:
                # Appeler le handler suivant
                response = await call_next(request)

                # Capturer le corps de la réponse si configuré
                response_body = None
                if self._config.log_response_body:
                    try:
                        # Lire le corps sans le consommer
                        response_body = await self._read_response_body(response)
                    except Exception as e:
                        logger.debug("Impossible de lire le corps de la réponse: {}", e)

                return response

            except Exception as e:
                error_message = str(e)
                logger.exception("Erreur lors du traitement de la requête: {}", e)
                raise

            finally:
                # Calculer la durée
                duration_ms = (time.perf_counter() - start_time) * 1000

                # Logger la requête
                try:
                    # Créer une réponse factice en cas d'erreur
                    if error_message:
                        fake_response = JSONResponse(
                            status_code=500,
                            content={"error": error_message},
                        )
                        await self._logger.log_request(
                            request,
                            fake_response,
                            duration_ms,
                            request_body=request_body,
                            error=error_message,
                        )
                    else:
                        await self._logger.log_request(
                            request,
                            response,
                            duration_ms,
                            request_body=request_body,
                            response_body=response_body if self._config.log_response_body else None,
                        )

                    # Ajouter le header X-Response-Time
                    if not error_message:
                        response.headers[HEADER_RESPONSE_TIME] = f"{duration_ms:.2f}ms"

                        # Ajouter le request_id dans la réponse
                        response.headers[HEADER_REQUEST_ID] = request_id

                except Exception as e:
                    logger.error("Impossible de logger la requête: {}", e)

        def _get_or_generate_request_id(self, request: Request) -> str:
            """Récupère ou génère un request_id.

            Args:
                request: Requête HTTP.

            Returns:
                Request ID.
            """
            # Vérifier le header
            request_id = request.headers.get(HEADER_REQUEST_ID)
            if request_id:
                return request_id

            # Générer un nouveau
            if self._config.add_request_id:
                return generate_request_id()

            return "no-id"

        async def _read_response_body(self, response: Response) -> str | None:
            """Lit le corps d'une réponse sans le consommer.

            Args:
                response: Réponse HTTP.

            Returns:
                Corps de la réponse ou None.
            """
            # Pour les JSONResponse, on peut accéder directement au contenu
            if hasattr(response, "body"):
                body = response.body
                if isinstance(body, bytes):
                    return body.decode("utf-8", errors="replace")
                return str(body)

            # Pour les autres types de réponse, on ne peut pas lire sans consommer
            return None


# ============================================================================
# INSTANCE GLOBALE
# ============================================================================


_request_logger: RequestLogger | None = None


def get_request_logger() -> RequestLogger | None:
    """Retourne l'instance globale du RequestLogger.

    Returns:
        Instance de RequestLogger ou None.
    """
    return _request_logger


def set_request_logger(request_logger: RequestLogger) -> None:
    """Définit l'instance globale du RequestLogger.

    Args:
        request_logger: Instance de RequestLogger.
    """
    global _request_logger
    _request_logger = request_logger


def reset_request_logger() -> None:
    """Réinitialise l'instance globale du RequestLogger."""
    global _request_logger
    _request_logger = None


# ============================================================================
# FONCTIONS HELPERS
# ============================================================================


def create_request_logger(
    config: LoggingConfig | None = None,
) -> RequestLogger:
    """Crée un RequestLogger avec la configuration donnée.

    Args:
        config: Configuration du logging.

    Returns:
        Instance de RequestLogger.
    """
    config = config or LoggingConfig()
    return RequestLogger(config)


async def get_logging_stats() -> LoggingStats:
    """Récupère les statistiques de logging.

    Returns:
        Statistiques actuelles.
    """
    request_logger = get_request_logger()
    if request_logger is None:
        return LoggingStats()
    return await request_logger.get_stats()


async def reset_logging_stats() -> None:
    """Réinitialise les statistiques de logging."""
    request_logger = get_request_logger()
    if request_logger is not None:
        await request_logger.reset_stats()


def log_request_sync(
    method: str,
    path: str,
    status_code: int,
    duration_ms: float,
    *,
    client_ip: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
    error: str | None = None,
) -> None:
    """Log une requête de manière synchrone (pour usage hors middleware).

    Args:
        method: Méthode HTTP.
        path: Chemin.
        status_code: Code de statut.
        duration_ms: Durée en ms.
        client_ip: IP du client.
        user_agent: User-Agent.
        request_id: ID de la requête.
        error: Message d'erreur.
    """
    log_entry = RequestLog(
        timestamp=datetime.now(UTC).isoformat(),
        level=get_log_level_for_status(status_code, LogLevelByStatus.STRICT),
        request_id=request_id or generate_request_id(),
        method=method,
        path=path,
        status_code=status_code,
        duration_ms=duration_ms,
        client_ip=client_ip,
        user_agent=user_agent,
        error=error,
    )

    level = log_entry.level.lower()
    log_method = getattr(logger, level, logger.info)
    log_method(log_entry.to_json())


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "HEADER_REQUEST_ID",
    "HEADER_CORRELATION_ID",
    "HEADER_RESPONSE_TIME",
    "MAX_BODY_LOG_SIZE",
    "DEFAULT_EXCLUDED_PATHS",
    "LATENCY_WARNING_THRESHOLD_MS",
    "LATENCY_ERROR_THRESHOLD_MS",
    # Exceptions
    "LoggingMiddlewareError",
    "LogFormatError",
    # Enums
    "LogFormat",
    "LogLevelByStatus",
    # Modèles
    "LoggingConfig",
    "RequestLog",
    "LoggingStats",
    # Helpers
    "generate_request_id",
    "mask_sensitive_data",
    "extract_client_ip",
    "get_log_level_for_status",
    "should_exclude_path",
    "truncate_body",
    # Classes
    "RequestLogger",
    "LoggingMiddleware" if STARLETTE_AVAILABLE else None,
    # Instance globale
    "get_request_logger",
    "set_request_logger",
    "reset_request_logger",
    # Fonctions helpers
    "create_request_logger",
    "get_logging_stats",
    "reset_logging_stats",
    "log_request_sync",
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
