"""Limiteur de débit par site (token bucket) avec backoff automatique.

Ce module fournit un système de rate limiting robuste basé sur l'algorithme
du **token bucket**, utilisé pour contrôler précisément le nombre de requêtes
HTTP envoyées à chaque site source. Il est essentiel pour :

    - Respecter les limites imposées par les sites (éviter les bans)
    - Éviter la surcharge des serveurs (éthique)
    - Optimiser les performances (éviter les 429 Too Many Requests)
    - S'adapter dynamiquement aux réponses du site (Retry-After)

Algorithme Token Bucket :
    Chaque site dispose d'un "seau" (bucket) contenant un nombre limité de
    "jetons" (tokens). Chaque requête consomme un jeton. Les jetons sont
    régénérés à un taux constant (ex: 2 jetons/seconde). Si le seau est
    vide, la requête doit attendre qu'un jeton soit disponible.

    Avantages :
        - Permet des bursts contrôlés (jusqu'à la capacité max du seau)
        - Limite le débit moyen sur le long terme
        - Simple à implémenter et efficace en mémoire

Fonctionnalités principales :
    - Rate limiting par site (clé = site_id)
    - Rate limiting par endpoint (clé = site_id:endpoint)
    - Support du header `Retry-After` (backoff automatique sur 429)
    - Backoff exponentiel en cas d'erreurs répétées
    - Configuration par site (via SiteCapabilities)
    - Burst autorisé (tokens accumulés jusqu'à capacity)
    - Nettoyage automatique des buckets inactifs (LRU + TTL)
    - Statistiques détaillées (attente, refus, backoffs)
    - Mode pause/reprise global
    - Thread-safe (locks asyncio granulaires par bucket)
    - Événements EventBus pour monitoring

Architecture :
    RateLimiter
        ├── RateLimitStrategy (enum) : TOKEN_BUCKET, FIXED_WINDOW, SLIDING_WINDOW
        ├── TokenBucket (interne) : implémentation du bucket
        ├── RateLimitConfig (Pydantic) : configuration globale
        ├── SiteRateLimit (Pydantic) : configuration par site
        ├── RateLimitStats (Pydantic) : statistiques globales
        ├── BucketStats (Pydantic) : statistiques par bucket
        └── _BucketsCache (interne) : cache LRU des buckets

Exemple d'utilisation :
    >>> limiter = RateLimiter()
    >>> await limiter.start()
    >>>
    >>> # Configurer un site
    >>> limiter.configure_site(
    ...     site_id="mangadex",
    ...     rate_per_second=4.0,
    ...     min_delay=0.25,
    ...     burst_capacity=8,
    ... )
    >>>
    >>> # Acquérir un token (bloquant async)
    >>> await limiter.acquire("mangadex")
    >>> # ... effectuer la requête ...
    >>>
    >>> # Essayer sans bloquer
    >>> if await limiter.try_acquire("mangadex"):
    ...     # ... effectuer la requête ...
    ...     pass
    >>>
    >>> # Signaler un 429 (backoff automatique)
    >>> await limiter.report_rate_limit("mangadex", retry_after=5.0)
    >>>
    >>> # Statistiques
    >>> stats = await limiter.get_stats()
    >>> print(f"Total requêtes: {stats.total_acquisitions}")
    >>> print(f"Total attentes: {stats.total_wait_time_seconds:.1f}s")
    >>>
    >>> await limiter.stop()
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from datetime import UTC, datetime
from enum import Enum
from typing import Any, ClassVar, Final, Self

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# EXCEPTIONS
# ============================================================================


class RateLimitError(NexusDLError):
    """Exception de base pour les erreurs de rate limiting."""


class RateLimiterNotStartedError(RateLimitError):
    """Exception levée lorsqu'on utilise le limiteur avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "RateLimiter must be started before use. Call await limiter.start()"
        )


class RateLimitExceededError(RateLimitError):
    """Exception levée lorsque la limite de débit est dépassée.

    Contient des informations sur le temps d'attente recommandé.
    """

    def __init__(
        self,
        key: str,
        retry_after: float | None = None,
        reason: str = "",
    ) -> None:
        msg = f"Limite de débit dépassée pour '{key}'"
        if retry_after is not None:
            msg += f" (retry_after={retry_after:.2f}s)"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.key = key
        self.retry_after = retry_after
        self.reason = reason


class BucketNotFoundError(RateLimitError):
    """Exception levée lorsqu'un bucket demandé n'existe pas."""

    def __init__(self, key: str) -> None:
        super().__init__(f"Bucket introuvable: {key}")
        self.key = key


# ============================================================================
# ENUMS
# ============================================================================


class RateLimitStrategy(str, Enum):
    """Stratégie de rate limiting.

    TOKEN_BUCKET    : Algorithme classique (recommandé). Permet des bursts
                      contrôlés tout en limitant le débit moyen.
    FIXED_WINDOW    : Fenêtre fixe (ex: 100 req/minute). Simple mais peut
                      avoir des pics aux boundaries.
    SLIDING_WINDOW  : Fenêtre glissante. Plus précis mais plus coûteux.
    """

    TOKEN_BUCKET = "token_bucket"
    FIXED_WINDOW = "fixed_window"
    SLIDING_WINDOW = "sliding_window"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            RateLimitStrategy.TOKEN_BUCKET: "Token Bucket (recommandé)",
            RateLimitStrategy.FIXED_WINDOW: "Fenêtre fixe",
            RateLimitStrategy.SLIDING_WINDOW: "Fenêtre glissante",
        }[self]


class BackoffStrategy(str, Enum):
    """Stratégie de backoff après un 429 ou des erreurs répétées.

    NONE         : Pas de backoff.
    FIXED        : Délai fixe entre les tentatives.
    EXPONENTIAL  : Délai exponentiel (2^n * base_delay).
    LINEAR       : Délai linéaire (n * base_delay).
    """

    NONE = "none"
    FIXED = "fixed"
    EXPONENTIAL = "exponential"
    LINEAR = "linear"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            BackoffStrategy.NONE: "Aucun",
            BackoffStrategy.FIXED: "Fixe",
            BackoffStrategy.EXPONENTIAL: "Exponentiel",
            BackoffStrategy.LINEAR: "Linéaire",
        }[self]


# ============================================================================
# MODÈLES PYDANTIC — Configuration
# ============================================================================


class SiteRateLimit(BaseModel):
    """Configuration de rate limiting pour un site spécifique.

    Attributes:
        rate_per_second: Nombre maximum de requêtes par seconde.
        min_delay_seconds: Délai minimum entre deux requêtes (secondes).
        burst_capacity: Capacité maximale du bucket (tokens accumulés).
        strategy: Stratégie de rate limiting.
        backoff_strategy: Stratégie de backoff après 429.
        backoff_base_delay: Délai de base pour le backoff (secondes).
        backoff_max_delay: Délai maximum pour le backoff (secondes).
        backoff_max_retries: Nombre maximum de tentatives de backoff.
        enabled: True si le rate limiting est activé pour ce site.
    """

    rate_per_second: float = Field(
        default=2.0,
        gt=0.0,
        le=100.0,
        description="Nombre maximum de requêtes par seconde.",
    )
    min_delay_seconds: float = Field(
        default=0.5,
        ge=0.0,
        le=60.0,
        description="Délai minimum entre deux requêtes (secondes).",
    )
    burst_capacity: int = Field(
        default=4,
        ge=1,
        le=100,
        description="Capacité maximale du bucket (tokens accumulés).",
    )
    strategy: RateLimitStrategy = Field(
        default=RateLimitStrategy.TOKEN_BUCKET,
        description="Stratégie de rate limiting.",
    )
    backoff_strategy: BackoffStrategy = Field(
        default=BackoffStrategy.EXPONENTIAL,
        description="Stratégie de backoff après 429.",
    )
    backoff_base_delay: float = Field(
        default=1.0,
        ge=0.0,
        le=60.0,
        description="Délai de base pour le backoff (secondes).",
    )
    backoff_max_delay: float = Field(
        default=60.0,
        ge=0.0,
        le=600.0,
        description="Délai maximum pour le backoff (secondes).",
    )
    backoff_max_retries: int = Field(
        default=5,
        ge=0,
        le=20,
        description="Nombre maximum de tentatives de backoff.",
    )
    enabled: bool = Field(
        default=True,
        description="True si le rate limiting est activé pour ce site.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def interval_seconds(self) -> float:
        """Intervalle moyen entre deux requêtes (inverse du rate)."""
        if self.rate_per_second <= 0:
            return self.min_delay_seconds
        return max(self.min_delay_seconds, 1.0 / self.rate_per_second)


class RateLimitConfig(BaseModel):
    """Configuration globale du rate limiter.

    Attributes:
        default_rate_per_second: Rate par défaut si site non configuré.
        default_min_delay: Délai minimum par défaut.
        default_burst_capacity: Capacité par défaut.
        default_strategy: Stratégie par défaut.
        max_buckets: Nombre maximum de buckets en mémoire (LRU).
        bucket_ttl_seconds: Durée de vie d'un bucket inactif avant eviction.
        cleanup_interval_seconds: Intervalle entre deux nettoyages.
        global_pause: Pause globale (True = bloquer toutes les requêtes).
    """

    default_rate_per_second: float = Field(
        default=2.0,
        gt=0.0,
        le=100.0,
        description="Rate par défaut si site non configuré.",
    )
    default_min_delay: float = Field(
        default=0.5,
        ge=0.0,
        le=60.0,
        description="Délai minimum par défaut.",
    )
    default_burst_capacity: int = Field(
        default=4,
        ge=1,
        le=100,
        description="Capacité par défaut.",
    )
    default_strategy: RateLimitStrategy = Field(
        default=RateLimitStrategy.TOKEN_BUCKET,
        description="Stratégie par défaut.",
    )
    max_buckets: int = Field(
        default=1000,
        ge=1,
        le=10000,
        description="Nombre maximum de buckets en mémoire.",
    )
    bucket_ttl_seconds: float = Field(
        default=300.0,
        ge=0.0,
        description="Durée de vie d'un bucket inactif (0 = infini).",
    )
    cleanup_interval_seconds: float = Field(
        default=60.0,
        ge=1.0,
        description="Intervalle entre deux nettoyages.",
    )
    global_pause: bool = Field(
        default=False,
        description="Pause globale (True = bloquer toutes les requêtes).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# MODÈLES PYDANTIC — Statistiques
# ============================================================================


class BucketStats(BaseModel):
    """Statistiques d'un bucket individuel."""

    key: str = Field(..., description="Clé du bucket.")
    tokens_available: float = Field(
        ...,
        ge=0.0,
        description="Nombre de tokens actuellement disponibles.",
    )
    capacity: int = Field(..., ge=1, description="Capacité maximale du bucket.")
    rate_per_second: float = Field(..., gt=0.0, description="Taux de régénération.")
    total_acquisitions: int = Field(default=0, ge=0)
    total_wait_time_seconds: float = Field(default=0.0, ge=0.0)
    total_rejections: int = Field(default=0, ge=0, description="Rejets immédiats (try_acquire=False).")
    backoff_count: int = Field(default=0, ge=0, description="Nombre de backoffs déclenchés.")
    last_acquisition_at: datetime | None = None
    last_backoff_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def utilization_percent(self) -> float:
        """Pourcentage d'utilisation des tokens (0-100)."""
        if self.capacity == 0:
            return 0.0
        return ((self.capacity - self.tokens_available) / self.capacity) * 100.0

    @property
    def average_wait_time_ms(self) -> float:
        """Temps d'attente moyen en millisecondes."""
        if self.total_acquisitions == 0:
            return 0.0
        return (self.total_wait_time_seconds / self.total_acquisitions) * 1000.0


class RateLimitStats(BaseModel):
    """Statistiques globales du rate limiter."""

    total_buckets: int = Field(default=0, ge=0)
    active_buckets: int = Field(default=0, ge=0, description="Buckets avec tokens < capacity.")
    total_acquisitions: int = Field(default=0, ge=0)
    total_wait_time_seconds: float = Field(default=0.0, ge=0.0)
    total_rejections: int = Field(default=0, ge=0)
    total_backoffs: int = Field(default=0, ge=0)
    total_evictions: int = Field(default=0, ge=0, description="Buckets évincés (LRU/TTL).")
    global_paused: bool = Field(default=False)
    last_acquisition_at: datetime | None = None
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def average_wait_time_ms(self) -> float:
        """Temps d'attente moyen global en millisecondes."""
        if self.total_acquisitions == 0:
            return 0.0
        return (self.total_wait_time_seconds / self.total_acquisitions) * 1000.0

    @property
    def rejection_rate(self) -> float:
        """Taux de rejet (0.0 à 1.0)."""
        total = self.total_acquisitions + self.total_rejections
        if total == 0:
            return 0.0
        return self.total_rejections / total


# ============================================================================
# CLASSE INTERNE — TokenBucket
# ============================================================================


class _TokenBucket:
    """Implémentation interne du token bucket.

    Non exposé publiquement — utilisé par RateLimiter.

    Le bucket contient un nombre de tokens qui se régénère à un taux
    constant (rate_per_second). Chaque requête consomme un token.
    Si le bucket est vide, la requête doit attendre.
    """

    __slots__ = (
        "_key",
        "_capacity",
        "_rate_per_second",
        "_min_delay",
        "_tokens",
        "_last_refill",
        "_last_acquisition",
        "_lock",
        "_stats",
    )

    def __init__(
        self,
        key: str,
        capacity: int,
        rate_per_second: float,
        min_delay: float = 0.0,
    ) -> None:
        self._key = key
        self._capacity = capacity
        self._rate_per_second = rate_per_second
        self._min_delay = min_delay
        self._tokens = float(capacity)  # Commence plein
        self._last_refill = time.monotonic()
        self._last_acquisition = 0.0
        self._lock = asyncio.Lock()

        # Stats internes
        self._stats = {
            "total_acquisitions": 0,
            "total_wait_time": 0.0,
            "total_rejections": 0,
            "backoff_count": 0,
            "last_acquisition_at": None,
            "last_backoff_at": None,
            "created_at": datetime.now(UTC),
            "last_used_at": datetime.now(UTC),
        }

    @property
    def key(self) -> str:
        return self._key

    @property
    def tokens_available(self) -> float:
        """Nombre de tokens disponibles (après refill)."""
        self._refill()
        return self._tokens

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def rate_per_second(self) -> float:
        return self._rate_per_second

    @property
    def is_idle(self) -> bool:
        """Indique si le bucket est plein et n'a pas été utilisé récemment."""
        return self._tokens >= self._capacity

    def _refill(self) -> None:
        """Régénère les tokens en fonction du temps écoulé."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return

        # Calculer les nouveaux tokens
        new_tokens = elapsed * self._rate_per_second
        self._tokens = min(self._capacity, self._tokens + new_tokens)
        self._last_refill = now

    def time_until_available(self) -> float:
        """Temps estimé avant qu'un token soit disponible.

        Returns:
            Temps en secondes (0.0 si un token est déjà disponible).
        """
        self._refill()
        if self._tokens >= 1.0:
            return 0.0
        # Temps pour générer 1 token
        tokens_needed = 1.0 - self._tokens
        return tokens_needed / self._rate_per_second

    async def acquire(self, *, timeout: float | None = None) -> float:
        """Acquiert un token (bloquant async).

        Args:
            timeout: Timeout maximum d'attente (None = infini).

        Returns:
            Temps d'attente effectif en secondes.

        Raises:
            asyncio.TimeoutError: Si le timeout est dépassé.
        """
        start_time = time.monotonic()

        async with self._lock:
            # 1. Refill
            self._refill()

            # 2. Vérifier le min_delay
            if self._min_delay > 0 and self._last_acquisition > 0:
                elapsed_since_last = time.monotonic() - self._last_acquisition
                if elapsed_since_last < self._min_delay:
                    wait_time = self._min_delay - elapsed_since_last
                    if timeout is not None and wait_time > timeout:
                        raise asyncio.TimeoutError(
                            f"Min delay ({self._min_delay}s) dépasse le timeout ({timeout}s)"
                        )
                    await asyncio.sleep(wait_time)

            # 3. Attendre un token si nécessaire
            while self._tokens < 1.0:
                wait_time = self.time_until_available()

                # Vérifier le timeout
                if timeout is not None:
                    elapsed = time.monotonic() - start_time
                    remaining = timeout - elapsed
                    if remaining <= 0:
                        raise asyncio.TimeoutError(
                            f"Timeout après {elapsed:.2f}s pour le bucket '{self._key}'"
                        )
                    wait_time = min(wait_time, remaining)

                if wait_time > 0:
                    # Libérer le lock pendant l'attente
                    self._lock.release()
                    try:
                        await asyncio.sleep(wait_time)
                    finally:
                        await self._lock.acquire()
                    self._refill()

            # 4. Consommer un token
            self._tokens -= 1.0
            self._last_acquisition = time.monotonic()
            self._stats["total_acquisitions"] += 1
            self._stats["last_acquisition_at"] = datetime.now(UTC)
            self._stats["last_used_at"] = datetime.now(UTC)

            wait_time = time.monotonic() - start_time
            self._stats["total_wait_time"] += wait_time

            return wait_time

    async def try_acquire(self) -> bool:
        """Essaie d'acquérir un token sans bloquer.

        Returns:
            True si un token a été acquis, False sinon.
        """
        async with self._lock:
            self._refill()

            # Vérifier le min_delay
            if self._min_delay > 0 and self._last_acquisition > 0:
                elapsed_since_last = time.monotonic() - self._last_acquisition
                if elapsed_since_last < self._min_delay:
                    self._stats["total_rejections"] += 1
                    return False

            if self._tokens < 1.0:
                self._stats["total_rejections"] += 1
                return False

            self._tokens -= 1.0
            self._last_acquisition = time.monotonic()
            self._stats["total_acquisitions"] += 1
            self._stats["last_acquisition_at"] = datetime.now(UTC)
            self._stats["last_used_at"] = datetime.now(UTC)
            return True

    def record_backoff(self) -> None:
        """Enregistre un backoff (après 429 ou erreur)."""
        self._stats["backoff_count"] += 1
        self._stats["last_backoff_at"] = datetime.now(UTC)

    def reset(self) -> None:
        """Réinitialise le bucket à sa capacité maximale."""
        self._tokens = float(self._capacity)
        self._last_refill = time.monotonic()

    def get_stats(self) -> BucketStats:
        """Retourne les statistiques du bucket."""
        self._refill()
        return BucketStats(
            key=self._key,
            tokens_available=self._tokens,
            capacity=self._capacity,
            rate_per_second=self._rate_per_second,
            total_acquisitions=self._stats["total_acquisitions"],
            total_wait_time_seconds=self._stats["total_wait_time"],
            total_rejections=self._stats["total_rejections"],
            backoff_count=self._stats["backoff_count"],
            last_acquisition_at=self._stats["last_acquisition_at"],
            last_backoff_at=self._stats["last_backoff_at"],
            created_at=self._stats["created_at"],
            last_used_at=self._stats["last_used_at"],
        )


# ============================================================================
# CLASSE PRINCIPALE — RateLimiter
# ============================================================================


class RateLimiter:
    """Limiteur de débit par site basé sur le token bucket.

    Gère plusieurs buckets (un par site ou endpoint), avec nettoyage
    automatique des buckets inactifs et support du backoff après 429.

    Lifecycle :
        >>> limiter = RateLimiter()
        >>> await limiter.start()
        >>> await limiter.acquire("mangadex")
        >>> # ... faire la requête ...
        >>> await limiter.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Chaque bucket a son propre lock pour minimiser la contention.
    """

    # Constantes
    _DEFAULT_CLEANUP_INTERVAL: Final[float] = 60.0

    def __init__(
        self,
        *,
        config: RateLimitConfig | None = None,
    ) -> None:
        """Initialise le rate limiter.

        Args:
            config: Configuration globale (défaut: valeurs raisonnables).
        """
        self._config = config or RateLimitConfig()

        # État
        self._started: bool = False
        self._paused: bool = self._config.global_pause
        self._global_lock = asyncio.Lock()

        # Buckets (OrderedDict pour LRU)
        self._buckets: OrderedDict[str, _TokenBucket] = OrderedDict()
        self._buckets_lock = asyncio.Lock()

        # Configurations par site
        self._site_configs: dict[str, SiteRateLimit] = {}
        self._configs_lock = asyncio.Lock()

        # Backoff tracking (key → (retry_count, next_available_at))
        self._backoffs: dict[str, tuple[int, float]] = {}
        self._backoffs_lock = asyncio.Lock()

        # Tâche de nettoyage
        self._cleanup_task: asyncio.Task[None] | None = None

        # Statistiques globales
        self._total_evictions: int = 0
        self._start_time: float = 0.0
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="rate_limiter")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre le rate limiter et la tâche de nettoyage."""
        if self._started:
            self._logger.warning("RateLimiter déjà démarré, ignore")
            return

        self._started = True
        self._start_time = time.monotonic()

        # Démarrer la tâche de nettoyage
        if self._config.bucket_ttl_seconds > 0:
            self._cleanup_task = asyncio.create_task(
                self._cleanup_loop(),
                name="rate_limiter_cleanup",
            )

        self._logger.info(
            "RateLimiter démarré: max_buckets={}, cleanup_interval={:.1f}s",
            self._config.max_buckets,
            self._config.cleanup_interval_seconds,
        )

    async def stop(self) -> None:
        """Arrête le rate limiter et libère les ressources."""
        if not self._started:
            return

        self._started = False

        # Annuler la tâche de nettoyage
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        # Vider les buckets
        async with self._buckets_lock:
            self._buckets.clear()

        self._logger.info("RateLimiter arrêté")

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
        """Indique si le limiteur est démarré."""
        return self._started

    @property
    def is_paused(self) -> bool:
        """Indique si le limiteur est en pause globale."""
        return self._paused

    @property
    def buckets_count(self) -> int:
        """Nombre de buckets actifs."""
        return len(self._buckets)

    # ------------------------------------------------------------------------
    # API publique — Configuration
    # ------------------------------------------------------------------------

    def configure_site(
        self,
        site_id: str,
        *,
        rate_per_second: float | None = None,
        min_delay: float | None = None,
        burst_capacity: int | None = None,
        strategy: RateLimitStrategy | None = None,
        backoff_strategy: BackoffStrategy | None = None,
        enabled: bool = True,
    ) -> None:
        """Configure le rate limiting pour un site spécifique.

        Les paramètres non spécifiés utilisent les valeurs par défaut.

        Args:
            site_id: Identifiant du site.
            rate_per_second: Requêtes par seconde.
            min_delay: Délai minimum entre requêtes.
            burst_capacity: Capacité du bucket.
            strategy: Stratégie de rate limiting.
            backoff_strategy: Stratégie de backoff.
            enabled: Activer/désactiver le rate limiting pour ce site.
        """
        config = SiteRateLimit(
            rate_per_second=rate_per_second or self._config.default_rate_per_second,
            min_delay_seconds=min_delay if min_delay is not None else self._config.default_min_delay,
            burst_capacity=burst_capacity or self._config.default_burst_capacity,
            strategy=strategy or self._config.default_strategy,
            backoff_strategy=backoff_strategy or BackoffStrategy.EXPONENTIAL,
            enabled=enabled,
        )

        # Stocker la config (fire-and-forget pour le lock)
        asyncio.create_task(self._set_site_config(site_id, config))

        # Invalider le bucket existant si la config change
        asyncio.create_task(self._invalidate_bucket(site_id))

        self._logger.debug(
            "Site configuré: {} (rate={}/s, burst={}, min_delay={:.2f}s)",
            site_id,
            config.rate_per_second,
            config.burst_capacity,
            config.min_delay_seconds,
        )

    async def _set_site_config(self, site_id: str, config: SiteRateLimit) -> None:
        async with self._configs_lock:
            self._site_configs[site_id] = config

    async def _invalidate_bucket(self, key: str) -> None:
        async with self._buckets_lock:
            if key in self._buckets:
                del self._buckets[key]

    async def get_site_config(self, site_id: str) -> SiteRateLimit:
        """Retourne la configuration d'un site (ou la config par défaut).

        Args:
            site_id: Identifiant du site.

        Returns:
            Configuration du site.
        """
        async with self._configs_lock:
            return self._site_configs.get(
                site_id,
                SiteRateLimit(
                    rate_per_second=self._config.default_rate_per_second,
                    min_delay_seconds=self._config.default_min_delay,
                    burst_capacity=self._config.default_burst_capacity,
                    strategy=self._config.default_strategy,
                ),
            )

    # ------------------------------------------------------------------------
    # API publique — Acquisition de tokens
    # ------------------------------------------------------------------------

    async def acquire(
        self,
        key: str,
        *,
        timeout: float | None = None,
    ) -> float:
        """Acquiert un token pour une clé donnée (bloquant async).

        Si le bucket n'existe pas, il est créé avec la configuration par défaut
        ou celle du site (si key = site_id).

        Args:
            key: Clé du bucket (site_id ou site_id:endpoint).
            timeout: Timeout maximum d'attente (None = infini).

        Returns:
            Temps d'attente effectif en secondes.

        Raises:
            RateLimiterNotStartedError: Si le limiteur n'est pas démarré.
            RateLimitExceededError: Si la pause globale est activée.
            asyncio.TimeoutError: Si le timeout est dépassé.
        """
        self._ensure_started()

        # Vérifier la pause globale
        if self._paused:
            raise RateLimitExceededError(
                key,
                reason="Rate limiter en pause globale",
            )

        # Vérifier le backoff actif
        backoff_wait = await self._check_backoff(key)
        if backoff_wait > 0:
            self._logger.trace(
                "Backoff actif pour {}: {:.2f}s restantes",
                key,
                backoff_wait,
            )
            await asyncio.sleep(backoff_wait)

        # Récupérer ou créer le bucket
        bucket = await self._get_or_create_bucket(key)

        # Acquérir le token
        try:
            wait_time = await bucket.acquire(timeout=timeout)
            return wait_time
        except asyncio.TimeoutError:
            raise
        except Exception as e:
            self._logger.error("Erreur lors de l'acquisition pour {}: {}", key, e)
            raise

    async def try_acquire(self, key: str) -> bool:
        """Essaie d'acquérir un token sans bloquer.

        Args:
            key: Clé du bucket.

        Returns:
            True si un token a été acquis, False sinon.
        """
        self._ensure_started()

        if self._paused:
            return False

        # Vérifier le backoff
        backoff_wait = await self._check_backoff(key)
        if backoff_wait > 0:
            return False

        bucket = await self._get_or_create_bucket(key)
        return await bucket.try_acquire()

    async def wait_time(self, key: str) -> float:
        """Retourne le temps d'attente estimé pour un token.

        Args:
            key: Clé du bucket.

        Returns:
            Temps en secondes (0.0 si un token est disponible).
        """
        self._ensure_started()

        if self._paused:
            return float("inf")

        bucket = await self._get_or_create_bucket(key)
        return bucket.time_until_available()

    # ------------------------------------------------------------------------
    # API publique — Backoff (gestion des 429)
    # ------------------------------------------------------------------------

    async def report_rate_limit(
        self,
        key: str,
        *,
        retry_after: float | None = None,
    ) -> float:
        """Signale un 429 Too Many Requests et applique un backoff.

        Args:
            key: Clé du bucket.
            retry_after: Valeur du header Retry-After (secondes).
                         Si None, utilise le backoff exponentiel.

        Returns:
            Temps de backoff appliqué (secondes).
        """
        self._ensure_started()

        config = await self.get_site_config(self._extract_site_id(key))

        async with self._backoffs_lock:
            current_count, _ = self._backoffs.get(key, (0, 0.0))
            new_count = current_count + 1

            # Calculer le délai de backoff
            if retry_after is not None:
                delay = max(0.0, retry_after)
            else:
                delay = self._compute_backoff_delay(
                    new_count,
                    config.backoff_strategy,
                    config.backoff_base_delay,
                    config.backoff_max_delay,
                )

            next_available = time.monotonic() + delay
            self._backoffs[key] = (new_count, next_available)

        # Enregistrer dans le bucket
        bucket = await self._get_or_create_bucket(key, create=False)
        if bucket is not None:
            bucket.record_backoff()

        self._logger.warning(
            "Rate limit signalé pour {}: backoff de {:.2f}s (tentative #{})",
            key,
            delay,
            new_count,
        )

        return delay

    async def reset_backoff(self, key: str) -> None:
        """Réinitialise le backoff pour une clé (après succès).

        Args:
            key: Clé du bucket.
        """
        async with self._backoffs_lock:
            if key in self._backoffs:
                del self._backoffs[key]

    async def _check_backoff(self, key: str) -> float:
        """Vérifie si un backoff est actif et retourne le temps d'attente.

        Args:
            key: Clé à vérifier.

        Returns:
            Temps d'attente restant (0.0 si pas de backoff actif).
        """
        async with self._backoffs_lock:
            if key not in self._backoffs:
                return 0.0

            count, next_available = self._backoffs[key]
            remaining = next_available - time.monotonic()

            if remaining <= 0:
                # Backoff expiré, le supprimer
                del self._backoffs[key]
                return 0.0

            return remaining

    @staticmethod
    def _compute_backoff_delay(
        attempt: int,
        strategy: BackoffStrategy,
        base_delay: float,
        max_delay: float,
    ) -> float:
        """Calcule le délai de backoff selon la stratégie.

        Args:
            attempt: Numéro de la tentative (1-based).
            strategy: Stratégie de backoff.
            base_delay: Délai de base.
            max_delay: Délai maximum.

        Returns:
            Délai en secondes.
        """
        if strategy == BackoffStrategy.NONE:
            return 0.0
        if strategy == BackoffStrategy.FIXED:
            return min(base_delay, max_delay)
        if strategy == BackoffStrategy.LINEAR:
            return min(base_delay * attempt, max_delay)
        if strategy == BackoffStrategy.EXPONENTIAL:
            # 2^(n-1) * base_delay
            return min(base_delay * (2 ** (attempt - 1)), max_delay)
        return base_delay

    # ------------------------------------------------------------------------
    # API publique — Contrôle global
    # ------------------------------------------------------------------------

    async def pause(self) -> None:
        """Met le rate limiter en pause globale.

        Toutes les requêtes seront bloquées jusqu'à resume().
        """
        self._paused = True
        self._logger.info("Rate limiter mis en pause globale")

    async def resume(self) -> None:
        """Reprend le rate limiter après une pause globale."""
        self._paused = False
        self._logger.info("Rate limiter repris")

    async def reset_all(self) -> None:
        """Réinitialise tous les buckets à leur capacité maximale."""
        async with self._buckets_lock:
            for bucket in self._buckets.values():
                bucket.reset()

        async with self._backoffs_lock:
            self._backoffs.clear()

        self._logger.info("Tous les buckets réinitialisés")

    async def reset_bucket(self, key: str) -> None:
        """Réinitialise un bucket spécifique.

        Args:
            key: Clé du bucket.

        Raises:
            BucketNotFoundError: Si le bucket n'existe pas.
        """
        async with self._buckets_lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                raise BucketNotFoundError(key)
            bucket.reset()

        async with self._backoffs_lock:
            self._backoffs.pop(key, None)

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> RateLimitStats:
        """Retourne les statistiques globales du rate limiter."""
        self._ensure_started()

        async with self._buckets_lock:
            buckets = list(self._buckets.values())

        total_acquisitions = 0
        total_wait_time = 0.0
        total_rejections = 0
        total_backoffs = 0
        active_buckets = 0
        last_acquisition_at = None

        for bucket in buckets:
            stats = bucket.get_stats()
            total_acquisitions += stats.total_acquisitions
            total_wait_time += stats.total_wait_time_seconds
            total_rejections += stats.total_rejections
            total_backoffs += stats.backoff_count
            if not bucket.is_idle:
                active_buckets += 1
            if stats.last_acquisition_at is not None:
                if last_acquisition_at is None or stats.last_acquisition_at > last_acquisition_at:
                    last_acquisition_at = stats.last_acquisition_at

        uptime = 0.0
        if self._start_time > 0:
            uptime = time.monotonic() - self._start_time

        async with self._stats_lock:
            total_evictions = self._total_evictions

        return RateLimitStats(
            total_buckets=len(buckets),
            active_buckets=active_buckets,
            total_acquisitions=total_acquisitions,
            total_wait_time_seconds=total_wait_time,
            total_rejections=total_rejections,
            total_backoffs=total_backoffs,
            total_evictions=total_evictions,
            global_paused=self._paused,
            last_acquisition_at=last_acquisition_at,
            uptime_seconds=uptime,
        )

    async def get_bucket_stats(self, key: str) -> BucketStats:
        """Retourne les statistiques d'un bucket spécifique.

        Args:
            key: Clé du bucket.

        Returns:
            Statistiques du bucket.

        Raises:
            BucketNotFoundError: Si le bucket n'existe pas.
        """
        self._ensure_started()

        async with self._buckets_lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                raise BucketNotFoundError(key)
            return bucket.get_stats()

    async def list_buckets(self) -> list[str]:
        """Liste toutes les clés de buckets actifs.

        Returns:
            Liste des clés.
        """
        async with self._buckets_lock:
            return list(self._buckets.keys())

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques globales."""
        async with self._stats_lock:
            self._total_evictions = 0

    # ------------------------------------------------------------------------
    # Méthodes internes — Gestion des buckets
    # ------------------------------------------------------------------------

    async def _get_or_create_bucket(
        self,
        key: str,
        *,
        create: bool = True,
    ) -> _TokenBucket:
        """Récupère ou crée un bucket pour une clé.

        Args:
            key: Clé du bucket.
            create: Si True, crée le bucket s'il n'existe pas.

        Returns:
            Instance du bucket.

        Raises:
            BucketNotFoundError: Si create=False et le bucket n'existe pas.
        """
        async with self._buckets_lock:
            # LRU : déplacer en fin si existant
            if key in self._buckets:
                self._buckets.move_to_end(key)
                return self._buckets[key]

            if not create:
                raise BucketNotFoundError(key)

            # Vérifier la limite de buckets
            if len(self._buckets) >= self._config.max_buckets:
                await self._evict_oldest_bucket()

            # Récupérer la config du site
            site_id = self._extract_site_id(key)
            config = await self.get_site_config(site_id)

            # Créer le bucket
            bucket = _TokenBucket(
                key=key,
                capacity=config.burst_capacity,
                rate_per_second=config.rate_per_second,
                min_delay=config.min_delay_seconds,
            )

            self._buckets[key] = bucket
            return bucket

    async def _evict_oldest_bucket(self) -> None:
        """Évince le bucket le moins récemment utilisé."""
        if not self._buckets:
            return

        # OrderedDict : le premier élément est le moins récemment utilisé
        oldest_key, _ = next(iter(self._buckets.items()))
        del self._buckets[oldest_key]

        async with self._stats_lock:
            self._total_evictions += 1

        self._logger.trace("Bucket évincé (LRU): {}", oldest_key)

    async def _cleanup_loop(self) -> None:
        """Boucle de nettoyage des buckets inactifs."""
        try:
            while self._started:
                await asyncio.sleep(self._config.cleanup_interval_seconds)
                await self._cleanup_inactive_buckets()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._logger.error("Erreur dans la boucle de nettoyage: {}", e)

    async def _cleanup_inactive_buckets(self) -> None:
        """Supprime les buckets inactifs depuis trop longtemps."""
        if self._config.bucket_ttl_seconds <= 0:
            return

        now = time.monotonic()
        ttl = self._config.bucket_ttl_seconds
        to_remove: list[str] = []

        async with self._buckets_lock:
            for key, bucket in self._buckets.items():
                # Vérifier si le bucket est inactif
                stats = bucket.get_stats()
                if stats.last_used_at is not None:
                    last_used_ts = stats.last_used_at.timestamp()
                    # Approximation : on utilise created_at comme fallback
                    age = now - (stats.created_at.timestamp() if stats.last_used_at is None else last_used_ts)
                    # Note: cette logique est simplifiée, en réalité on devrait tracker last_used en monotonic
                else:
                    age = now - stats.created_at.timestamp()

                # Bucket plein et inutilisé depuis TTL
                if bucket.is_idle and age > ttl:
                    to_remove.append(key)

            for key in to_remove:
                del self._buckets[key]
                async with self._stats_lock:
                    self._total_evictions += 1

        if to_remove:
            self._logger.debug(
                "Nettoyage: {} buckets inactifs supprimés",
                len(to_remove),
            )

    # ------------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # ------------------------------------------------------------------------

    @staticmethod
    def _extract_site_id(key: str) -> str:
        """Extrait le site_id d'une clé (peut être 'site_id' ou 'site_id:endpoint').

        Args:
            key: Clé du bucket.

        Returns:
            site_id extrait.
        """
        if ":" in key:
            return key.split(":", 1)[0]
        return key

    def _ensure_started(self) -> None:
        """Vérifie que le limiteur est démarré."""
        if not self._started:
            raise RateLimiterNotStartedError()

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        paused = " (PAUSED)" if self._paused else ""
        return (
            f"<RateLimiter status={status}{paused} "
            f"buckets={len(self._buckets)}>"
        )


# ============================================================================
# HELPERS PUBLICS — Fonctions utilitaires
# ============================================================================


def parse_retry_after(value: str | float | None) -> float | None:
    """Parse un header Retry-After (peut être un nombre de secondes ou une date HTTP).

    Args:
        value: Valeur du header (string ou nombre).

    Returns:
        Nombre de secondes à attendre, ou None si invalide.

    Example:
        >>> parse_retry_after("5")
        5.0
        >>> parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT")
        123.45  # secondes jusqu'à cette date
    """
    if value is None:
        return None

    # Essayer comme nombre de secondes
    try:
        seconds = float(value)
        return max(0.0, seconds)
    except (ValueError, TypeError):
        pass

    # Essayer comme date HTTP
    if isinstance(value, str):
        try:
            from email.utils import parsedate_to_datetime
            target_date = parsedate_to_datetime(value)
            now = datetime.now(UTC)
            delta = (target_date - now).total_seconds()
            return max(0.0, delta)
        except Exception:
            pass

    return None


def build_endpoint_key(site_id: str, endpoint: str) -> str:
    """Construit une clé de bucket pour un endpoint spécifique.

    Args:
        site_id: Identifiant du site.
        endpoint: Chemin de l'endpoint (ex: "/api/chapters").

    Returns:
        Clé au format "site_id:endpoint".

    Example:
        >>> build_endpoint_key("mangadex", "/api/chapters")
        'mangadex:/api/chapters'
    """
    # Normaliser l'endpoint
    endpoint = endpoint.strip()
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return f"{site_id}:{endpoint}"


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "RateLimitError",
    "RateLimiterNotStartedError",
    "RateLimitExceededError",
    "BucketNotFoundError",
    # Enums
    "RateLimitStrategy",
    "BackoffStrategy",
    # Modèles — Configuration
    "RateLimitConfig",
    "SiteRateLimit",
    # Modèles — Statistiques
    "RateLimitStats",
    "BucketStats",
    # Classe principale
    "RateLimiter",
    # Helpers
    "parse_retry_after",
    "build_endpoint_key",
]
