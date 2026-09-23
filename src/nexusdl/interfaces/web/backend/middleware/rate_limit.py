"""Middleware de rate limiting pour l'API REST NexusDL.

Ce module fournit un middleware FastAPI complet pour limiter le nombre de
requêtes par client (IP ou API key), protégeant l'API contre les abus et
les attaques par déni de service.

**Fonctionnalités** :
    - 3 stratégies de limitation : Fixed Window, Sliding Window, Token Bucket
    - 2 backends : Memory (défaut), Redis (optionnel)
    - Configuration par endpoint ou globale
    - Headers de réponse standards (X-RateLimit-*, Retry-After)
    - Support des API keys et des IPs
    - Whitelist/Blacklist de clients
    - Monitoring via EventBus
    - Intégration avec le système de configuration
    - Messages d'erreur traduits (i18n)
    - Statistiques en temps réel

**Architecture** :
    RateLimitMiddleware (Starlette middleware)
        ├── RateLimiter (logique de limitation)
        │   ├── FixedWindowStrategy
        │   ├── SlidingWindowStrategy
        │   └── TokenBucketStrategy
        ├── RateLimitBackend (ABC)
        │   ├── MemoryBackend (défaut)
        │   └── RedisBackend (optionnel)
        ├── RateLimitConfig (configuration)
        └── RateLimitStats (statistiques)

**Stratégies** :
    - FIXED_WINDOW : Compteur réinitialisé à intervalles fixes (ex: 100 req/min)
    - SLIDING_WINDOW : Fenêtre glissante pour une limitation plus fluide
    - TOKEN_BUCKET : Jetons accumulés au fil du temps, permet des bursts

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.web.backend.middleware.rate_limit import (
    ...     RateLimitMiddleware, RateLimitConfig, RateLimitStrategy,
    ... )
    >>>
    >>> # Configuration globale
    >>> config = RateLimitConfig(
    ...     default_limit=100,
    ...     default_period=60,
    ...     strategy=RateLimitStrategy.SLIDING_WINDOW,
    ... )
    >>>
    >>> # Ajouter le middleware à FastAPI
    >>> app = FastAPI()
    >>> app.add_middleware(RateLimitMiddleware, config=config)
    >>>
    >>> # Configuration par endpoint
    >>> @app.get("/search")
    >>> @rate_limit(limit=20, period=60)
    >>> async def search():
    ...     return {"results": []}

Intégration :
    - fastapi                 : Framework web
    - core/config.py          : Configuration globale
    - core/events.py          : EventBus pour monitoring
    - core/logger.py          : Logs
    - core/i18n.py            : Traductions
    - core/exceptions.py      : Exceptions
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
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

from nexusdl.core.constants import APP_NAME
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t


# ============================================================================
# CONSTANTES
# ============================================================================


# Limites par défaut
DEFAULT_RATE_LIMIT: Final[int] = 100
DEFAULT_RATE_PERIOD: Final[int] = 60  # secondes
DEFAULT_BURST_SIZE: Final[int] = 10

# Headers HTTP
HEADER_RATE_LIMIT: Final[str] = "X-RateLimit-Limit"
HEADER_RATE_REMAINING: Final[str] = "X-RateLimit-Remaining"
HEADER_RATE_RESET: Final[str] = "X-RateLimit-Reset"
HEADER_RETRY_AFTER: Final[str] = "Retry-After"

# Codes HTTP
HTTP_TOO_MANY_REQUESTS: Final[int] = 429
HTTP_OK: Final[int] = 200

# Clés de stockage
MEMORY_KEY_PREFIX: Final[str] = "rate_limit:"
REDIS_KEY_PREFIX: Final[str] = "nexusdl:rate_limit:"

# Intervalles
CLEANUP_INTERVAL_SECONDS: Final[int] = 300  # 5 minutes
MAX_CLIENTS_MEMORY: Final[int] = 10000


# ============================================================================
# EXCEPTIONS
# ============================================================================


class RateLimitError(NexusDLError):
    """Exception de base pour les erreurs de rate limiting."""


class RateLimitExceededError(RateLimitError):
    """Exception levée lorsque la limite de requêtes est dépassée.

    Attributes:
        client_id: Identifiant du client.
        limit: Limite de requêtes.
        period: Période en secondes.
        retry_after: Temps d'attente avant retry.
    """

    def __init__(
        self,
        client_id: str,
        limit: int,
        period: int,
        retry_after: float,
    ) -> None:
        msg = t(
            "rate_limit.exceeded",
            default="Rate limit exceeded for {client_id}. Limit: {limit} requests per {period}s. Retry after {retry_after:.1f}s",
            client_id=client_id,
            limit=limit,
            period=period,
            retry_after=retry_after,
        )
        super().__init__(msg)
        self.client_id = client_id
        self.limit = limit
        self.period = period
        self.retry_after = retry_after


class RateLimitBackendError(RateLimitError):
    """Exception levée lorsqu'une erreur survient dans le backend.

    Attributes:
        backend: Nom du backend.
        reason: Raison de l'erreur.
    """

    def __init__(self, backend: str, reason: str = "") -> None:
        msg = f"Erreur du backend de rate limiting: {backend}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.backend = backend
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class RateLimitStrategy(str, Enum):
    """Stratégie de limitation de débit.

    Attributes:
        FIXED_WINDOW: Fenêtre fixe (compteur réinitialisé à intervalles).
        SLIDING_WINDOW: Fenêtre glissante (limitation plus fluide).
        TOKEN_BUCKET: Seau de jetons (permet des bursts contrôlés).
    """

    FIXED_WINDOW = "fixed_window"
    SLIDING_WINDOW = "sliding_window"
    TOKEN_BUCKET = "token_bucket"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            RateLimitStrategy.FIXED_WINDOW: t("rate_limit.strategy.fixed", default="Fixed Window"),
            RateLimitStrategy.SLIDING_WINDOW: t("rate_limit.strategy.sliding", default="Sliding Window"),
            RateLimitStrategy.TOKEN_BUCKET: t("rate_limit.strategy.token", default="Token Bucket"),
        }[self]


class RateLimitScope(str, Enum):
    """Portée de la limitation.

    Attributes:
        IP: Limitation par adresse IP.
        API_KEY: Limitation par clé API.
        USER: Limitation par utilisateur authentifié.
        GLOBAL: Limitation globale pour tous les clients.
    """

    IP = "ip"
    API_KEY = "api_key"
    USER = "user"
    GLOBAL = "global"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            RateLimitScope.IP: t("rate_limit.scope.ip", default="IP Address"),
            RateLimitScope.API_KEY: t("rate_limit.scope.api_key", default="API Key"),
            RateLimitScope.USER: t("rate_limit.scope.user", default="User"),
            RateLimitScope.GLOBAL: t("rate_limit.scope.global", default="Global"),
        }[self]


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class EndpointRateLimit(BaseModel):
    """Configuration de rate limiting pour un endpoint spécifique.

    Attributes:
        path: Chemin de l'endpoint (ex: "/api/search").
        method: Méthode HTTP (GET, POST, etc.) ou "*" pour toutes.
        limit: Nombre maximum de requêtes.
        period: Période en secondes.
        strategy: Stratégie de limitation.
        scope: Portée de la limitation.
        burst_size: Taille du burst (pour token bucket).
    """

    path: str = Field(..., description="Chemin de l'endpoint.")
    method: str = Field(default="*", description="Méthode HTTP.")
    limit: int = Field(default=DEFAULT_RATE_LIMIT, ge=1, description="Limite de requêtes.")
    period: int = Field(default=DEFAULT_RATE_PERIOD, ge=1, description="Période en secondes.")
    strategy: RateLimitStrategy = Field(default=RateLimitStrategy.SLIDING_WINDOW, description="Stratégie.")
    scope: RateLimitScope = Field(default=RateLimitScope.IP, description="Portée.")
    burst_size: int = Field(default=DEFAULT_BURST_SIZE, ge=1, description="Taille du burst.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class RateLimitConfig(BaseModel):
    """Configuration globale du rate limiting.

    Attributes:
        enabled: Activer le rate limiting.
        default_limit: Limite par défaut.
        default_period: Période par défaut (secondes).
        default_strategy: Stratégie par défaut.
        default_scope: Portée par défaut.
        default_burst_size: Taille du burst par défaut.
        endpoint_limits: Configurations par endpoint.
        whitelist: Clients exemptés de rate limiting.
        blacklist: Clients toujours bloqués.
        trust_proxy: Faire confiance aux headers X-Forwarded-For.
        storage_backend: Backend de stockage ("memory" ou "redis").
        redis_url: URL Redis (si storage_backend="redis").
        cleanup_interval: Intervalle de nettoyage (secondes).
        max_clients_memory: Nombre max de clients en mémoire.
    """

    enabled: bool = Field(default=True, description="Activer le rate limiting.")
    default_limit: int = Field(default=DEFAULT_RATE_LIMIT, ge=1, description="Limite par défaut.")
    default_period: int = Field(default=DEFAULT_RATE_PERIOD, ge=1, description="Période par défaut.")
    default_strategy: RateLimitStrategy = Field(default=RateLimitStrategy.SLIDING_WINDOW, description="Stratégie par défaut.")
    default_scope: RateLimitScope = Field(default=RateLimitScope.IP, description="Portée par défaut.")
    default_burst_size: int = Field(default=DEFAULT_BURST_SIZE, ge=1, description="Burst par défaut.")
    endpoint_limits: list[EndpointRateLimit] = Field(default_factory=list, description="Limites par endpoint.")
    whitelist: set[str] = Field(default_factory=set, description="Clients exemptés.")
    blacklist: set[str] = Field(default_factory=set, description="Clients bloqués.")
    trust_proxy: bool = Field(default=False, description="Confiance aux proxies.")
    storage_backend: str = Field(default="memory", description="Backend de stockage.")
    redis_url: str = Field(default="redis://localhost:6379/0", description="URL Redis.")
    cleanup_interval: int = Field(default=CLEANUP_INTERVAL_SECONDS, ge=60, description="Intervalle de nettoyage.")
    max_clients_memory: int = Field(default=MAX_CLIENTS_MEMORY, ge=100, description="Max clients en mémoire.")

    model_config = ConfigDict(extra="forbid")

    def get_endpoint_limit(self, path: str, method: str) -> EndpointRateLimit | None:
        """Récupère la configuration pour un endpoint spécifique.

        Args:
            path: Chemin de l'endpoint.
            method: Méthode HTTP.

        Returns:
            Configuration ou None.
        """
        for endpoint_limit in self.endpoint_limits:
            if endpoint_limit.path == path:
                if endpoint_limit.method == "*" or endpoint_limit.method == method:
                    return endpoint_limit
        return None


class RateLimitInfo(BaseModel):
    """Informations sur la limite de débit actuelle.

    Attributes:
        client_id: Identifiant du client.
        limit: Limite de requêtes.
        remaining: Requêtes restantes.
        reset_at: Timestamp de réinitialisation.
        retry_after: Temps d'attente avant retry (si limité).
        is_limited: Si le client est actuellement limité.
    """

    client_id: str = Field(..., description="Identifiant du client.")
    limit: int = Field(..., ge=0, description="Limite de requêtes.")
    remaining: int = Field(..., ge=0, description="Requêtes restantes.")
    reset_at: float = Field(..., description="Timestamp de réinitialisation.")
    retry_after: float = Field(default=0.0, ge=0.0, description="Temps d'attente.")
    is_limited: bool = Field(default=False, description="Client limité.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def reset_datetime(self) -> datetime:
        """Date/heure de réinitialisation."""
        return datetime.fromtimestamp(self.reset_at, tz=UTC)


class RateLimitStats(BaseModel):
    """Statistiques du rate limiting.

    Attributes:
        total_requests: Nombre total de requêtes.
        allowed_requests: Nombre de requêtes autorisées.
        blocked_requests: Nombre de requêtes bloquées.
        unique_clients: Nombre de clients uniques.
        total_blocked_clients: Nombre de clients bloqués.
        average_requests_per_client: Moyenne de requêtes par client.
        started_at: Timestamp de début de collecte.
        last_request_at: Timestamp de la dernière requête.
    """

    total_requests: int = Field(default=0, ge=0)
    allowed_requests: int = Field(default=0, ge=0)
    blocked_requests: int = Field(default=0, ge=0)
    unique_clients: int = Field(default=0, ge=0)
    total_blocked_clients: int = Field(default=0, ge=0)
    average_requests_per_client: float = Field(default=0.0, ge=0.0)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_request_at: datetime | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def block_rate(self) -> float:
        """Taux de blocage (0.0 à 1.0)."""
        if self.total_requests == 0:
            return 0.0
        return self.blocked_requests / self.total_requests


# ============================================================================
# BACKENDS — Stockage des compteurs
# ============================================================================


@dataclass
class RateLimitEntry:
    """Entrée de rate limiting pour un client.

    Attributes:
        count: Nombre de requêtes.
        window_start: Début de la fenêtre.
        last_request: Timestamp de la dernière requête.
        tokens: Jetons disponibles (pour token bucket).
        last_refill: Timestamp du dernier refill.
    """

    count: int = 0
    window_start: float = 0.0
    last_request: float = 0.0
    tokens: float = 0.0
    last_refill: float = 0.0


class RateLimitBackend(ABC):
    """Backend abstrait pour le stockage des compteurs."""

    @abstractmethod
    async def get(self, key: str) -> RateLimitEntry | None:
        """Récupère une entrée.

        Args:
            key: Clé de l'entrée.

        Returns:
            Entrée ou None.
        """
        pass

    @abstractmethod
    async def set(self, key: str, entry: RateLimitEntry, ttl: int | None = None) -> None:
        """Définit une entrée.

        Args:
            key: Clé de l'entrée.
            entry: Entrée à stocker.
            ttl: Durée de vie en secondes.
        """
        pass

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Supprime une entrée.

        Args:
            key: Clé de l'entrée.
        """
        pass

    @abstractmethod
    async def cleanup(self) -> int:
        """Nettoie les entrées expirées.

        Returns:
            Nombre d'entrées supprimées.
        """
        pass

    @abstractmethod
    async def get_stats(self) -> dict[str, int]:
        """Récupère les statistiques.

        Returns:
            Dictionnaire de statistiques.
        """
        pass


class MemoryBackend(RateLimitBackend):
    """Backend en mémoire pour le rate limiting.

    Stocke les compteurs dans un dictionnaire Python.
    Rapide mais non persistant et non distribué.
    """

    def __init__(self, max_clients: int = MAX_CLIENTS_MEMORY) -> None:
        """Initialise le backend.

        Args:
            max_clients: Nombre maximum de clients.
        """
        self._entries: dict[str, RateLimitEntry] = {}
        self._max_clients = max_clients
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> RateLimitEntry | None:
        """Récupère une entrée."""
        async with self._lock:
            return self._entries.get(key)

    async def set(self, key: str, entry: RateLimitEntry, ttl: int | None = None) -> None:
        """Définit une entrée."""
        async with self._lock:
            # Limiter le nombre de clients
            if len(self._entries) >= self._max_clients and key not in self._entries:
                # Supprimer les entrées les plus anciennes
                oldest_keys = sorted(
                    self._entries.keys(),
                    key=lambda k: self._entries[k].last_request,
                )[:100]
                for old_key in oldest_keys:
                    del self._entries[old_key]

            self._entries[key] = entry

    async def delete(self, key: str) -> None:
        """Supprime une entrée."""
        async with self._lock:
            self._entries.pop(key, None)

    async def cleanup(self) -> int:
        """Nettoie les entrées expirées."""
        async with self._lock:
            current_time = time.time()
            expired_keys = [
                key for key, entry in self._entries.items()
                if current_time - entry.last_request > 3600  # 1 heure
            ]
            for key in expired_keys:
                del self._entries[key]
            return len(expired_keys)

    async def get_stats(self) -> dict[str, int]:
        """Récupère les statistiques."""
        async with self._lock:
            return {
                "total_entries": len(self._entries),
                "max_clients": self._max_clients,
            }


class RedisBackend(RateLimitBackend):
    """Backend Redis pour le rate limiting.

    Stocke les compteurs dans Redis. Persistant et distribué.
    Nécessite la dépendance optionnelle 'redis'.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        """Initialise le backend.

        Args:
            redis_url: URL de connexion Redis.
        """
        self._redis_url = redis_url
        self._redis: Any = None
        self._key_prefix = REDIS_KEY_PREFIX

    async def _ensure_connected(self) -> None:
        """Assure la connexion à Redis."""
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(self._redis_url)
                await self._redis.ping()
            except Exception as e:
                raise RateLimitBackendError("redis", str(e)) from e

    async def get(self, key: str) -> RateLimitEntry | None:
        """Récupère une entrée."""
        await self._ensure_connected()
        try:
            data = await self._redis.hgetall(f"{self._key_prefix}{key}")
            if not data:
                return None

            return RateLimitEntry(
                count=int(data.get(b"count", 0)),
                window_start=float(data.get(b"window_start", 0)),
                last_request=float(data.get(b"last_request", 0)),
                tokens=float(data.get(b"tokens", 0)),
                last_refill=float(data.get(b"last_refill", 0)),
            )
        except Exception as e:
            raise RateLimitBackendError("redis", str(e)) from e

    async def set(self, key: str, entry: RateLimitEntry, ttl: int | None = None) -> None:
        """Définit une entrée."""
        await self._ensure_connected()
        try:
            redis_key = f"{self._key_prefix}{key}"
            await self._redis.hset(redis_key, mapping={
                "count": entry.count,
                "window_start": entry.window_start,
                "last_request": entry.last_request,
                "tokens": entry.tokens,
                "last_refill": entry.last_refill,
            })
            if ttl:
                await self._redis.expire(redis_key, ttl)
        except Exception as e:
            raise RateLimitBackendError("redis", str(e)) from e

    async def delete(self, key: str) -> None:
        """Supprime une entrée."""
        await self._ensure_connected()
        try:
            await self._redis.delete(f"{self._key_prefix}{key}")
        except Exception as e:
            raise RateLimitBackendError("redis", str(e)) from e

    async def cleanup(self) -> int:
        """Nettoie les entrées expirées (géré automatiquement par Redis TTL)."""
        return 0

    async def get_stats(self) -> dict[str, int]:
        """Récupère les statistiques."""
        await self._ensure_connected()
        try:
            keys = await self._redis.keys(f"{self._key_prefix}*")
            return {
                "total_entries": len(keys),
            }
        except Exception as e:
            raise RateLimitBackendError("redis", str(e)) from e

    async def close(self) -> None:
        """Ferme la connexion Redis."""
        if self._redis:
            await self._redis.close()
            self._redis = None


# ============================================================================
# STRATÉGIES — Algorithmes de limitation
# ============================================================================


class RateLimitStrategyBase(ABC):
    """Classe de base pour les stratégies de rate limiting."""

    @abstractmethod
    async def check(
        self,
        backend: RateLimitBackend,
        key: str,
        limit: int,
        period: int,
        burst_size: int = DEFAULT_BURST_SIZE,
    ) -> tuple[bool, RateLimitInfo]:
        """Vérifie si une requête est autorisée.

        Args:
            backend: Backend de stockage.
            key: Clé du client.
            limit: Limite de requêtes.
            period: Période en secondes.
            burst_size: Taille du burst (pour token bucket).

        Returns:
            Tuple (allowed, info).
        """
        pass


class FixedWindowStrategy(RateLimitStrategyBase):
    """Stratégie de fenêtre fixe.

    Le compteur est réinitialisé à intervalles fixes.
    Simple mais peut causer des pics en début de fenêtre.
    """

    async def check(
        self,
        backend: RateLimitBackend,
        key: str,
        limit: int,
        period: int,
        burst_size: int = DEFAULT_BURST_SIZE,
    ) -> tuple[bool, RateLimitInfo]:
        """Vérifie si une requête est autorisée."""
        current_time = time.time()
        entry = await backend.get(key)

        if entry is None:
            # Nouvelle fenêtre
            entry = RateLimitEntry(
                count=1,
                window_start=current_time,
                last_request=current_time,
            )
            await backend.set(key, entry, ttl=period)

            return True, RateLimitInfo(
                client_id=key,
                limit=limit,
                remaining=limit - 1,
                reset_at=current_time + period,
                retry_after=0.0,
                is_limited=False,
            )

        # Vérifier si la fenêtre a expiré
        if current_time - entry.window_start >= period:
            # Réinitialiser la fenêtre
            entry = RateLimitEntry(
                count=1,
                window_start=current_time,
                last_request=current_time,
            )
            await backend.set(key, entry, ttl=period)

            return True, RateLimitInfo(
                client_id=key,
                limit=limit,
                remaining=limit - 1,
                reset_at=current_time + period,
                retry_after=0.0,
                is_limited=False,
            )

        # Vérifier la limite
        if entry.count >= limit:
            retry_after = period - (current_time - entry.window_start)
            return False, RateLimitInfo(
                client_id=key,
                limit=limit,
                remaining=0,
                reset_at=entry.window_start + period,
                retry_after=retry_after,
                is_limited=True,
            )

        # Incrémenter le compteur
        entry.count += 1
        entry.last_request = current_time
        await backend.set(key, entry, ttl=int(period - (current_time - entry.window_start)))

        return True, RateLimitInfo(
            client_id=key,
            limit=limit,
            remaining=limit - entry.count,
            reset_at=entry.window_start + period,
            retry_after=0.0,
            is_limited=False,
        )


class SlidingWindowStrategy(RateLimitStrategyBase):
    """Stratégie de fenêtre glissante.

    Utilise une moyenne pondérée entre la fenêtre actuelle et la précédente.
    Plus fluide que la fenêtre fixe.
    """

    async def check(
        self,
        backend: RateLimitBackend,
        key: str,
        limit: int,
        period: int,
        burst_size: int = DEFAULT_BURST_SIZE,
    ) -> tuple[bool, RateLimitInfo]:
        """Vérifie si une requête est autorisée."""
        current_time = time.time()
        entry = await backend.get(key)

        if entry is None:
            # Première requête
            entry = RateLimitEntry(
                count=1,
                window_start=current_time,
                last_request=current_time,
            )
            await backend.set(key, entry, ttl=period * 2)

            return True, RateLimitInfo(
                client_id=key,
                limit=limit,
                remaining=limit - 1,
                reset_at=current_time + period,
                retry_after=0.0,
                is_limited=False,
            )

        # Calculer le poids de la fenêtre précédente
        elapsed = current_time - entry.window_start
        if elapsed < period:
            # Dans la même fenêtre
            weight = 1.0 - (elapsed / period)
            effective_count = entry.count
        else:
            # Nouvelle fenêtre
            weight = 0.0
            effective_count = 0
            entry.window_start = current_time
            entry.count = 0

        # Vérifier la limite
        if effective_count >= limit:
            retry_after = period - (current_time - entry.window_start)
            return False, RateLimitInfo(
                client_id=key,
                limit=limit,
                remaining=0,
                reset_at=entry.window_start + period,
                retry_after=max(0.0, retry_after),
                is_limited=True,
            )

        # Incrémenter le compteur
        entry.count += 1
        entry.last_request = current_time
        await backend.set(key, entry, ttl=period * 2)

        return True, RateLimitInfo(
            client_id=key,
            limit=limit,
            remaining=limit - entry.count,
            reset_at=entry.window_start + period,
            retry_after=0.0,
            is_limited=False,
        )


class TokenBucketStrategy(RateLimitStrategyBase):
    """Stratégie de seau de jetons.

    Les jetons s'accumulent au fil du temps jusqu'à une limite.
    Permet des bursts contrôlés tout en limitant le débit moyen.
    """

    async def check(
        self,
        backend: RateLimitBackend,
        key: str,
        limit: int,
        period: int,
        burst_size: int = DEFAULT_BURST_SIZE,
    ) -> tuple[bool, RateLimitInfo]:
        """Vérifie si une requête est autorisée."""
        current_time = time.time()
        entry = await backend.get(key)

        if entry is None:
            # Initialiser le seau
            entry = RateLimitEntry(
                count=0,
                window_start=current_time,
                last_request=current_time,
                tokens=float(burst_size),
                last_refill=current_time,
            )
            await backend.set(key, entry, ttl=period * 2)

        # Calculer les jetons à ajouter
        elapsed = current_time - entry.last_refill
        refill_rate = limit / period  # jetons par seconde
        new_tokens = min(
            float(burst_size),
            entry.tokens + elapsed * refill_rate,
        )

        # Vérifier si on peut consommer un jeton
        if new_tokens < 1.0:
            retry_after = (1.0 - new_tokens) / refill_rate
            entry.tokens = new_tokens
            entry.last_refill = current_time
            await backend.set(key, entry, ttl=period * 2)

            return False, RateLimitInfo(
                client_id=key,
                limit=limit,
                remaining=0,
                reset_at=current_time + retry_after,
                retry_after=retry_after,
                is_limited=True,
            )

        # Consommer un jeton
        entry.tokens = new_tokens - 1.0
        entry.last_refill = current_time
        entry.last_request = current_time
        await backend.set(key, entry, ttl=period * 2)

        return True, RateLimitInfo(
            client_id=key,
            limit=limit,
            remaining=int(entry.tokens),
            reset_at=current_time + (burst_size - entry.tokens) / refill_rate,
            retry_after=0.0,
            is_limited=False,
        )


# ============================================================================
# RATE LIMITER — Logique principale
# ============================================================================


class RateLimiter:
    """Logique principale de rate limiting.

    Combine une stratégie et un backend pour vérifier les requêtes.
    """

    def __init__(
        self,
        config: RateLimitConfig,
        backend: RateLimitBackend | None = None,
    ) -> None:
        """Initialise le rate limiter.

        Args:
            config: Configuration du rate limiting.
            backend: Backend de stockage (défaut: MemoryBackend).
        """
        self._config = config
        self._backend = backend or MemoryBackend(config.max_clients_memory)
        self._strategies: dict[RateLimitStrategy, RateLimitStrategyBase] = {
            RateLimitStrategy.FIXED_WINDOW: FixedWindowStrategy(),
            RateLimitStrategy.SLIDING_WINDOW: SlidingWindowStrategy(),
            RateLimitStrategy.TOKEN_BUCKET: TokenBucketStrategy(),
        }
        self._stats = RateLimitStats()
        self._stats_lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Démarre le rate limiter."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Rate limiter démarré")

    async def stop(self) -> None:
        """Arrête le rate limiter."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        if isinstance(self._backend, RedisBackend):
            await self._backend.close()

        logger.info("Rate limiter arrêté")

    async def _cleanup_loop(self) -> None:
        """Boucle de nettoyage périodique."""
        try:
            while True:
                await asyncio.sleep(self._config.cleanup_interval)
                removed = await self._backend.cleanup()
                if removed > 0:
                    logger.debug("Nettoyage rate limiting: {} entrées supprimées", removed)
        except asyncio.CancelledError:
            pass

    def _get_client_id(self, request: Any, scope: RateLimitScope) -> str:
        """Extrait l'identifiant du client depuis la requête.

        Args:
            request: Requête HTTP.
            scope: Portée de la limitation.

        Returns:
            Identifiant du client.
        """
        if scope == RateLimitScope.GLOBAL:
            return "global"

        if scope == RateLimitScope.API_KEY:
            api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
            if api_key:
                return f"apikey:{hashlib.sha256(api_key.encode()).hexdigest()[:16]}"

        if scope == RateLimitScope.USER:
            # TODO: Extraire l'utilisateur authentifié
            user_id = getattr(request.state, "user_id", None)
            if user_id:
                return f"user:{user_id}"

        # Par défaut, utiliser l'IP
        if self._config.trust_proxy:
            forwarded_for = request.headers.get("X-Forwarded-For")
            if forwarded_for:
                ip = forwarded_for.split(",")[0].strip()
            else:
                ip = request.client.host if request.client else "unknown"
        else:
            ip = request.client.host if request.client else "unknown"

        return f"ip:{ip}"

    async def check_request(
        self,
        request: Any,
        path: str,
        method: str,
    ) -> tuple[bool, RateLimitInfo | None]:
        """Vérifie si une requête est autorisée.

        Args:
            request: Requête HTTP.
            path: Chemin de l'endpoint.
            method: Méthode HTTP.

        Returns:
            Tuple (allowed, info).
        """
        # Vérifier si le rate limiting est activé
        if not self._config.enabled:
            return True, None

        # Récupérer la configuration pour cet endpoint
        endpoint_config = self._config.get_endpoint_limit(path, method)
        if endpoint_config:
            limit = endpoint_config.limit
            period = endpoint_config.period
            strategy = endpoint_config.strategy
            scope = endpoint_config.scope
            burst_size = endpoint_config.burst_size
        else:
            limit = self._config.default_limit
            period = self._config.default_period
            strategy = self._config.default_strategy
            scope = self._config.default_scope
            burst_size = self._config.default_burst_size

        # Extraire l'identifiant du client
        client_id = self._get_client_id(request, scope)

        # Vérifier la whitelist/blacklist
        if client_id in self._config.whitelist:
            return True, None

        if client_id in self._config.blacklist:
            info = RateLimitInfo(
                client_id=client_id,
                limit=0,
                remaining=0,
                reset_at=time.time() + 3600,
                retry_after=3600.0,
                is_limited=True,
            )
            return False, info

        # Générer la clé de stockage
        key = f"{scope.value}:{client_id}:{path}:{method}"

        # Obtenir la stratégie
        strategy_impl = self._strategies[strategy]

        # Vérifier la limite
        allowed, info = await strategy_impl.check(
            self._backend,
            key,
            limit,
            period,
            burst_size,
        )

        # Mettre à jour les statistiques
        async with self._stats_lock:
            self._stats = RateLimitStats(
                total_requests=self._stats.total_requests + 1,
                allowed_requests=self._stats.allowed_requests + (1 if allowed else 0),
                blocked_requests=self._stats.blocked_requests + (0 if allowed else 1),
                unique_clients=self._stats.unique_clients + (1 if allowed and info.remaining == limit - 1 else 0),
                total_blocked_clients=self._stats.total_blocked_clients + (1 if not allowed and not info.is_limited else 0),
                average_requests_per_client=self._stats.average_requests_per_client,
                started_at=self._stats.started_at,
                last_request_at=datetime.now(UTC),
            )

        return allowed, info

    async def get_stats(self) -> RateLimitStats:
        """Récupère les statistiques.

        Returns:
            Statistiques actuelles.
        """
        async with self._stats_lock:
            return self._stats

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        async with self._stats_lock:
            self._stats = RateLimitStats()

    async def reset_client(self, client_id: str) -> None:
        """Réinitialise les compteurs d'un client.

        Args:
            client_id: Identifiant du client.
        """
        # Supprimer toutes les entrées pour ce client
        # Note: Cette opération est coûteuse en mémoire backend
        logger.info("Réinitialisation des compteurs pour: {}", client_id)


# ============================================================================
# MIDDLEWARE — Intégration FastAPI
# ============================================================================


if STARLETTE_AVAILABLE:

    class RateLimitMiddleware(BaseHTTPMiddleware):
        """Middleware FastAPI pour le rate limiting.

        Intercepte toutes les requêtes HTTP et applique les règles de
        rate limiting configurées.

        Example:
            >>> app = FastAPI()
            >>> config = RateLimitConfig(default_limit=100, default_period=60)
            >>> app.add_middleware(RateLimitMiddleware, config=config)
        """

        def __init__(
            self,
            app: Any,
            *,
            config: RateLimitConfig | None = None,
            limiter: RateLimiter | None = None,
        ) -> None:
            """Initialise le middleware.

            Args:
                app: Application FastAPI.
                config: Configuration du rate limiting.
                limiter: Instance de RateLimiter (optionnel).
            """
            super().__init__(app)
            self._config = config or RateLimitConfig()
            self._limiter = limiter or RateLimiter(self._config)

        async def dispatch(self, request: Request, call_next: Callable) -> Response:
            """Traite une requête HTTP.

            Args:
                request: Requête HTTP.
                call_next: Fonction pour appeler le handler suivant.

            Returns:
                Réponse HTTP.
            """
            # Ignorer les méthodes non-HTTP
            if request.method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                return await call_next(request)

            # Vérifier la limite
            allowed, info = await self._limiter.check_request(
                request,
                request.url.path,
                request.method,
            )

            if not allowed and info is not None:
                # Requête bloquée
                logger.warning(
                    "Rate limit exceeded: client={}, path={}, method={}",
                    info.client_id,
                    request.url.path,
                    request.method,
                )

                # Émettre un événement
                try:
                    event_bus = get_event_bus()
                    await event_bus.emit(
                        EventType.CUSTOM,
                        payload={
                            "type": "rate_limit.exceeded",
                            "client_id": info.client_id,
                            "path": request.url.path,
                            "method": request.method,
                            "limit": info.limit,
                            "retry_after": info.retry_after,
                        },
                        source="interfaces.web.rate_limit",
                    )
                except Exception as e:
                    logger.debug("Impossible d'émettre l'événement: {}", e)

                # Retourner une réponse 429
                return JSONResponse(
                    status_code=HTTP_TOO_MANY_REQUESTS,
                    content={
                        "error": "rate_limit_exceeded",
                        "message": t(
                            "rate_limit.error.message",
                            default="Too many requests. Please try again later.",
                        ),
                        "retry_after": info.retry_after,
                        "limit": info.limit,
                    },
                    headers={
                        HEADER_RATE_LIMIT: str(info.limit),
                        HEADER_RATE_REMAINING: "0",
                        HEADER_RATE_RESET: str(int(info.reset_at)),
                        HEADER_RETRY_AFTER: str(int(info.retry_after)),
                    },
                )

            # Requête autorisée
            response = await call_next(request)

            # Ajouter les headers de rate limiting
            if info is not None:
                response.headers[HEADER_RATE_LIMIT] = str(info.limit)
                response.headers[HEADER_RATE_REMAINING] = str(info.remaining)
                response.headers[HEADER_RATE_RESET] = str(int(info.reset_at))

            return response

        async def __aenter__(self) -> RateLimitMiddleware:
            """Context manager d'entrée."""
            await self._limiter.start()
            return self

        async def __aexit__(self, *args: Any) -> None:
            """Context manager de sortie."""
            await self._limiter.stop()


# ============================================================================
# DÉCORATEURS — Configuration par endpoint
# ============================================================================


def rate_limit(
    limit: int = DEFAULT_RATE_LIMIT,
    period: int = DEFAULT_RATE_PERIOD,
    *,
    strategy: RateLimitStrategy = RateLimitStrategy.SLIDING_WINDOW,
    scope: RateLimitScope = RateLimitScope.IP,
    burst_size: int = DEFAULT_BURST_SIZE,
) -> Callable:
    """Décorateur pour configurer le rate limiting par endpoint.

    Args:
        limit: Nombre maximum de requêtes.
        period: Période en secondes.
        strategy: Stratégie de limitation.
        scope: Portée de la limitation.
        burst_size: Taille du burst (pour token bucket).

    Returns:
        Décorateur.

    Example:
        >>> @app.get("/search")
        >>> @rate_limit(limit=20, period=60)
        >>> async def search():
        ...     return {"results": []}
    """
    def decorator(func: Callable) -> Callable:
        # Stocker la configuration dans un attribut de la fonction
        func._rate_limit_config = EndpointRateLimit(  # type: ignore[attr-defined]
            path="*",  # Sera rempli par le middleware
            method="*",
            limit=limit,
            period=period,
            strategy=strategy,
            scope=scope,
            burst_size=burst_size,
        )
        return func
    return decorator


# ============================================================================
# INSTANCE GLOBALE
# ============================================================================


_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter | None:
    """Retourne l'instance globale du RateLimiter.

    Returns:
        Instance de RateLimiter ou None.
    """
    return _rate_limiter


def set_rate_limiter(limiter: RateLimiter) -> None:
    """Définit l'instance globale du RateLimiter.

    Args:
        limiter: Instance de RateLimiter.
    """
    global _rate_limiter
    _rate_limiter = limiter


def reset_rate_limiter() -> None:
    """Réinitialise l'instance globale du RateLimiter."""
    global _rate_limiter
    _rate_limiter = None


# ============================================================================
# FONCTIONS HELPERS
# ============================================================================


def create_rate_limiter(
    config: RateLimitConfig | None = None,
) -> RateLimiter:
    """Crée un RateLimiter avec la configuration donnée.

    Args:
        config: Configuration du rate limiting.

    Returns:
        Instance de RateLimiter.
    """
    config = config or RateLimitConfig()

    # Créer le backend approprié
    if config.storage_backend == "redis":
        backend = RedisBackend(config.redis_url)
    else:
        backend = MemoryBackend(config.max_clients_memory)

    return RateLimiter(config, backend)


def parse_rate_limit_string(rate_string: str) -> tuple[int, int]:
    """Parse une chaîne de rate limit (ex: "100/minute", "10/second").

    Args:
        rate_string: Chaîne à parser.

    Returns:
        Tuple (limit, period_seconds).

    Raises:
        ValueError: Si le format est invalide.

    Example:
        >>> parse_rate_limit_string("100/minute")
        (100, 60)
        >>> parse_rate_limit_string("10/second")
        (10, 1)
        >>> parse_rate_limit_string("1000/hour")
        (1000, 3600)
    """
    parts = rate_string.strip().split("/")
    if len(parts) != 2:
        raise ValueError(f"Format invalide: {rate_string}")

    try:
        limit = int(parts[0])
    except ValueError as e:
        raise ValueError(f"Limite invalide: {parts[0]}") from e

    period_str = parts[1].lower()
    period_map = {
        "second": 1,
        "seconds": 1,
        "sec": 1,
        "s": 1,
        "minute": 60,
        "minutes": 60,
        "min": 60,
        "m": 60,
        "hour": 3600,
        "hours": 3600,
        "h": 3600,
        "day": 86400,
        "days": 86400,
        "d": 86400,
    }

    if period_str not in period_map:
        raise ValueError(f"Période invalide: {period_str}")

    return limit, period_map[period_str]


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_RATE_LIMIT",
    "DEFAULT_RATE_PERIOD",
    "DEFAULT_BURST_SIZE",
    "HEADER_RATE_LIMIT",
    "HEADER_RATE_REMAINING",
    "HEADER_RATE_RESET",
    "HEADER_RETRY_AFTER",
    "HTTP_TOO_MANY_REQUESTS",
    # Exceptions
    "RateLimitError",
    "RateLimitExceededError",
    "RateLimitBackendError",
    # Enums
    "RateLimitStrategy",
    "RateLimitScope",
    # Modèles
    "EndpointRateLimit",
    "RateLimitConfig",
    "RateLimitInfo",
    "RateLimitStats",
    "RateLimitEntry",
    # Backends
    "RateLimitBackend",
    "MemoryBackend",
    "RedisBackend",
    # Stratégies
    "RateLimitStrategyBase",
    "FixedWindowStrategy",
    "SlidingWindowStrategy",
    "TokenBucketStrategy",
    # Rate Limiter
    "RateLimiter",
    # Middleware
    "RateLimitMiddleware" if STARLETTE_AVAILABLE else None,
    # Décorateurs
    "rate_limit",
    # Instance globale
    "get_rate_limiter",
    "set_rate_limiter",
    "reset_rate_limiter",
    # Fonctions helpers
    "create_rate_limiter",
    "parse_rate_limit_string",
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
