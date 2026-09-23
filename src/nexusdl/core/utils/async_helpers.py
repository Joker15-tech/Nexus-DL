"""Utilitaires asyncio pour les opérations asynchrones dans NexusDL.

Ce module fournit un ensemble complet de fonctions et classes pour gérer
les opérations asynchrones de manière robuste et efficace. Il est utilisé
dans tout le projet pour :

    - Gestion des tâches asynchrones (création, annulation, timeout)
    - Synchronisation (sémaphores, locks, événements)
    - Files d'attente asynchrones (prioritaires, limitées)
    - Patterns courants (retry, rate limiting, circuit breaker)
    - Conversion sync/async
    - Monitoring et debugging

**Architecture** :
    - Fonctions et classes thread-safe
    - Support complet de asyncio
    - Intégration avec le logger et l'EventBus
    - Gestion robuste des erreurs et timeouts
    - Patterns de concurrence avancés

**Exemples d'utilisation** :
    >>> from nexusdl.core.utils.async_helpers import (
    ...     run_with_timeout, retry_async, gather_with_limit,
    ...     AsyncSemaphore, PriorityAsyncQueue,
    ... )
    >>>
    >>> # Timeout
    >>> result = await run_with_timeout(some_async_func(), timeout=30.0)
    >>>
    >>> # Retry avec backoff
    >>> result = await retry_async(
    ...     some_async_func,
    ...     max_retries=3,
    ...     backoff_factor=2.0,
    ... )
    >>>
    >>> # Limiter la concurrence
    >>> results = await gather_with_limit(
    ...     [task1(), task2(), task3()],
    ...     limit=5,
    ... )
    >>>
    >>> # File prioritaire
    >>> queue = PriorityAsyncQueue()
    >>> await queue.put(item, priority=1)
    >>> item = await queue.get()

Intégration :
    - core/downloader/* : utilise ces utilitaires pour la concurrence
    - core/session/* : utilise ces utilitaires pour le rate limiting
    - core/library/* : utilise ces utilitaires pour le scan asynchrone
    - core/image/* : utilise ces utilitaires pour le traitement parallèle
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Final,
    Generic,
    Iterable,
    TypeVar,
)

from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# CONSTANTES
# ============================================================================


# Timeout par défaut pour les opérations (secondes)
DEFAULT_TIMEOUT: Final[float] = 30.0

# Timeout maximum absolu (secondes)
MAX_TIMEOUT: Final[float] = 3600.0

# Délai initial pour le retry (secondes)
DEFAULT_RETRY_DELAY: Final[float] = 1.0

# Délai maximum pour le retry (secondes)
MAX_RETRY_DELAY: Final[float] = 60.0

# Nombre maximum de retries par défaut
DEFAULT_MAX_RETRIES: Final[int] = 3

# Taille par défaut des files d'attente
DEFAULT_QUEUE_SIZE: Final[int] = 1000


# ============================================================================
# EXCEPTIONS
# ============================================================================


class AsyncError(NexusDLError):
    """Exception de base pour les erreurs asynchrones."""


class TimeoutError(AsyncError):
    """Exception levée lorsqu'une opération dépasse le timeout.

    Attributes:
        operation: Nom de l'opération.
        timeout: Timeout en secondes.
    """

    def __init__(self, operation: str, timeout: float) -> None:
        super().__init__(f"Timeout après {timeout:.1f}s pour l'opération: {operation}")
        self.operation = operation
        self.timeout = timeout


class RetryExhaustedError(AsyncError):
    """Exception levée lorsque toutes les tentatives de retry ont échoué.

    Attributes:
        operation: Nom de l'opération.
        attempts: Nombre de tentatives effectuées.
        last_error: Dernière erreur rencontrée.
    """

    def __init__(
        self,
        operation: str,
        attempts: int,
        last_error: Exception | None = None,
    ) -> None:
        msg = f"Toutes les {attempts} tentatives ont échoué pour: {operation}"
        if last_error:
            msg += f" (dernière erreur: {last_error})"
        super().__init__(msg)
        self.operation = operation
        self.attempts = attempts
        self.last_error = last_error


class QueueFullError(AsyncError):
    """Exception levée lorsqu'une file d'attente est pleine.

    Attributes:
        queue_name: Nom de la file.
        max_size: Taille maximale.
    """

    def __init__(self, queue_name: str, max_size: int) -> None:
        super().__init__(f"File d'attente pleine: {queue_name} (max: {max_size})")
        self.queue_name = queue_name
        self.max_size = max_size


class CircuitBreakerOpenError(AsyncError):
    """Exception levée lorsqu'un circuit breaker est ouvert.

    Attributes:
        operation: Nom de l'opération.
        failures: Nombre d'échecs.
        reset_time: Temps de réinitialisation.
    """

    def __init__(
        self,
        operation: str,
        failures: int,
        reset_time: float,
    ) -> None:
        super().__init__(
            f"Circuit breaker ouvert pour {operation}: "
            f"{failures} échecs, réinitialisation dans {reset_time:.1f}s"
        )
        self.operation = operation
        self.failures = failures
        self.reset_time = reset_time


# ============================================================================
# ENUMS
# ============================================================================


class TaskState(str, Enum):
    """État d'une tâche asynchrone.

    Attributes:
        PENDING: En attente d'exécution.
        RUNNING: En cours d'exécution.
        COMPLETED: Terminée avec succès.
        FAILED: Terminée avec erreur.
        CANCELLED: Annulée.
        TIMEOUT: Timeout dépassé.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class BackoffStrategy(str, Enum):
    """Stratégie de backoff pour les retries.

    Attributes:
        FIXED: Délai fixe entre les tentatives.
        EXPONENTIAL: Délai exponentiel (delay * factor^attempt).
        LINEAR: Délai linéaire (delay * attempt).
        RANDOM: Délai aléatoire entre min_delay et max_delay.
    """

    FIXED = "fixed"
    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    RANDOM = "random"


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class TaskInfo(BaseModel):
    """Informations sur une tâche asynchrone.

    Attributes:
        task_id: Identifiant unique de la tâche.
        name: Nom de la tâche.
        state: État actuel.
        created_at: Date de création.
        started_at: Date de début d'exécution.
        completed_at: Date de fin.
        duration_ms: Durée d'exécution en millisecondes.
        error: Erreur si la tâche a échoué.
    """

    task_id: str = Field(..., description="Identifiant unique.")
    name: str = Field(default="", description="Nom de la tâche.")
    state: TaskState = Field(..., description="État actuel.")
    created_at: datetime = Field(..., description="Date de création.")
    started_at: datetime | None = Field(default=None, description="Date de début.")
    completed_at: datetime | None = Field(default=None, description="Date de fin.")
    duration_ms: float = Field(default=0.0, ge=0.0, description="Durée en ms.")
    error: str | None = Field(default=None, description="Erreur si échec.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class AsyncStats(BaseModel):
    """Statistiques des opérations asynchrones.

    Attributes:
        total_tasks: Nombre total de tâches créées.
        completed_tasks: Nombre de tâches terminées avec succès.
        failed_tasks: Nombre de tâches échouées.
        cancelled_tasks: Nombre de tâches annulées.
        timeout_tasks: Nombre de tâches en timeout.
        total_duration_ms: Durée totale d'exécution.
        average_duration_ms: Durée moyenne d'exécution.
    """

    total_tasks: int = Field(default=0, ge=0)
    completed_tasks: int = Field(default=0, ge=0)
    failed_tasks: int = Field(default=0, ge=0)
    cancelled_tasks: int = Field(default=0, ge=0)
    timeout_tasks: int = Field(default=0, ge=0)
    total_duration_ms: float = Field(default=0.0, ge=0.0)
    average_duration_ms: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# GESTION DES TÂCHES
# ============================================================================


T = TypeVar("T")


async def run_with_timeout(
    coro: Awaitable[T],
    timeout: float = DEFAULT_TIMEOUT,
    *,
    operation_name: str = "operation",
) -> T:
    """Exécute une coroutine avec un timeout.

    Args:
        coro: Coroutine à exécuter.
        timeout: Timeout en secondes.
        operation_name: Nom de l'opération (pour les erreurs).

    Returns:
        Résultat de la coroutine.

    Raises:
        TimeoutError: Si le timeout est dépassé.

    Example:
        >>> result = await run_with_timeout(fetch_data(), timeout=30.0)
    """
    if timeout <= 0 or timeout > MAX_TIMEOUT:
        raise ValueError(f"Timeout invalide: {timeout} (doit être entre 0 et {MAX_TIMEOUT})")

    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError as e:
        raise TimeoutError(operation_name, timeout) from e


async def retry_async(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = DEFAULT_MAX_RETRIES,
    delay: float = DEFAULT_RETRY_DELAY,
    max_delay: float = MAX_RETRY_DELAY,
    backoff_strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL,
    backoff_factor: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    on_retry: Callable[[int, Exception], Awaitable[None]] | None = None,
    **kwargs: Any,
) -> T:
    """Exécute une fonction asynchrone avec retry automatique.

    Args:
        func: Fonction asynchrone à exécuter.
        *args: Arguments positionnels.
        max_retries: Nombre maximum de tentatives.
        delay: Délai initial entre les tentatives.
        max_delay: Délai maximum entre les tentatives.
        backoff_strategy: Stratégie de backoff.
        backoff_factor: Facteur de backoff (pour exponential).
        exceptions: Types d'exceptions pour lesquelles retryer.
        on_retry: Callback appelé avant chaque retry.
        **kwargs: Arguments nommés.

    Returns:
        Résultat de la fonction.

    Raises:
        RetryExhaustedError: Si toutes les tentatives échouent.

    Example:
        >>> result = await retry_async(
        ...     fetch_data,
        ...     "url",
        ...     max_retries=3,
        ...     backoff_strategy=BackoffStrategy.EXPONENTIAL,
        ... )
    """
    if max_retries < 0:
        raise ValueError(f"max_retries doit être >= 0, reçu {max_retries}")

    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except exceptions as e:
            last_error = e

            if attempt == max_retries:
                break

            # Calculer le délai
            current_delay = _calculate_backoff_delay(
                attempt,
                delay,
                max_delay,
                backoff_strategy,
                backoff_factor,
            )

            # Callback de retry
            if on_retry is not None:
                await on_retry(attempt + 1, e)

            # Attendre avant de retryer
            await asyncio.sleep(current_delay)

    raise RetryExhaustedError(func.__name__, max_retries + 1, last_error)


def _calculate_backoff_delay(
    attempt: int,
    delay: float,
    max_delay: float,
    strategy: BackoffStrategy,
    factor: float,
) -> float:
    """Calcule le délai de backoff selon la stratégie.

    Args:
        attempt: Numéro de la tentative (0-based).
        delay: Délai initial.
        max_delay: Délai maximum.
        strategy: Stratégie de backoff.
        factor: Facteur de backoff.

    Returns:
        Délai en secondes.
    """
    import random

    if strategy == BackoffStrategy.FIXED:
        current_delay = delay
    elif strategy == BackoffStrategy.EXPONENTIAL:
        current_delay = delay * (factor ** attempt)
    elif strategy == BackoffStrategy.LINEAR:
        current_delay = delay * (attempt + 1)
    elif strategy == BackoffStrategy.RANDOM:
        current_delay = random.uniform(delay, delay * factor)
    else:
        current_delay = delay

    return min(current_delay, max_delay)


async def gather_with_limit(
    coros: Iterable[Awaitable[T]],
    limit: int,
    *,
    return_exceptions: bool = False,
) -> list[T | Exception]:
    """Exécute plusieurs coroutines avec une limite de concurrence.

    Args:
        coros: Coroutines à exécuter.
        limit: Nombre maximum de coroutines simultanées.
        return_exceptions: Si True, retourne les exceptions au lieu de les lever.

    Returns:
        Liste des résultats (ou exceptions si return_exceptions=True).

    Example:
        >>> results = await gather_with_limit(
        ...     [fetch(url) for url in urls],
        ...     limit=5,
        ... )
    """
    if limit <= 0:
        raise ValueError(f"limit doit être > 0, reçu {limit}")

    semaphore = asyncio.Semaphore(limit)

    async def _limited_coro(coro: Awaitable[T]) -> T | Exception:
        async with semaphore:
            try:
                return await coro
            except Exception as e:
                if return_exceptions:
                    return e
                raise

    tasks = [_limited_coro(coro) for coro in coros]
    return await asyncio.gather(*tasks, return_exceptions=return_exceptions)


async def gather_with_timeout(
    coros: Iterable[Awaitable[T]],
    timeout: float,
    *,
    return_exceptions: bool = False,
) -> list[T | Exception]:
    """Exécute plusieurs coroutines avec un timeout global.

    Args:
        coros: Coroutines à exécuter.
        timeout: Timeout global en secondes.
        return_exceptions: Si True, retourne les exceptions.

    Returns:
        Liste des résultats.

    Raises:
        TimeoutError: Si le timeout est dépassé.
    """
    try:
        return await asyncio.wait_for(
            asyncio.gather(*coros, return_exceptions=return_exceptions),
            timeout=timeout,
        )
    except asyncio.TimeoutError as e:
        raise TimeoutError("gather", timeout) from e


# ============================================================================
# SYNCHRONISATION
# ============================================================================


class AsyncSemaphore:
    """Sémaphore asynchrone amélioré avec monitoring.

    Permet de limiter la concurrence avec des statistiques et un timeout.

    Example:
        >>> sem = AsyncSemaphore(5, name="download")
        >>> async with sem:
        ...     await download_file()
        >>> print(sem.stats)
    """

    def __init__(self, value: int, *, name: str = "") -> None:
        """Initialise le sémaphore.

        Args:
            value: Nombre maximum de permits.
            name: Nom du sémaphore (pour monitoring).
        """
        if value <= 0:
            raise ValueError(f"value doit être > 0, reçu {value}")

        self._semaphore = asyncio.Semaphore(value)
        self._value = value
        self._name = name
        self._acquire_count = 0
        self._release_count = 0
        self._wait_time_total = 0.0
        self._hold_time_total = 0.0

    @property
    def name(self) -> str:
        """Nom du sémaphore."""
        return self._name

    @property
    def available(self) -> int:
        """Nombre de permits disponibles."""
        return self._semaphore._value

    @property
    def stats(self) -> dict[str, Any]:
        """Statistiques du sémaphore."""
        return {
            "name": self._name,
            "max_value": self._value,
            "available": self.available,
            "acquire_count": self._acquire_count,
            "release_count": self._release_count,
            "avg_wait_time_ms": (
                (self._wait_time_total / self._acquire_count * 1000)
                if self._acquire_count > 0
                else 0.0
            ),
            "avg_hold_time_ms": (
                (self._hold_time_total / self._release_count * 1000)
                if self._release_count > 0
                else 0.0
            ),
        }

    async def acquire(self, *, timeout: float | None = None) -> None:
        """Acquiert un permit.

        Args:
            timeout: Timeout en secondes (None = infini).

        Raises:
            TimeoutError: Si le timeout est dépassé.
        """
        start_time = time.perf_counter()

        try:
            if timeout is not None:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=timeout)
            else:
                await self._semaphore.acquire()
        except asyncio.TimeoutError as e:
            raise TimeoutError(f"semaphore.acquire({self._name})", timeout or 0) from e

        wait_time = time.perf_counter() - start_time
        self._acquire_count += 1
        self._wait_time_total += wait_time

    def release(self) -> None:
        """Libère un permit."""
        hold_time = time.perf_counter() - (
            self._last_acquire_time if hasattr(self, "_last_acquire_time") else time.perf_counter()
        )
        self._release_count += 1
        self._hold_time_total += hold_time
        self._semaphore.release()

    async def __aenter__(self) -> AsyncSemaphore:
        self._last_acquire_time = time.perf_counter()
        await self.acquire()
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.release()


class AsyncLockWithTimeout:
    """Lock asynchrone avec timeout.

    Example:
        >>> lock = AsyncLockWithTimeout()
        >>> async with lock.acquire(timeout=5.0):
        ...     # Section critique
        ...     pass
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def acquire(self, *, timeout: float | None = None) -> asyncio.Lock:
        """Acquiert le lock avec timeout.

        Args:
            timeout: Timeout en secondes.

        Returns:
            Lock acquis.

        Raises:
            TimeoutError: Si le timeout est dépassé.
        """
        if timeout is not None:
            try:
                await asyncio.wait_for(self._lock.acquire(), timeout=timeout)
            except asyncio.TimeoutError as e:
                raise TimeoutError("lock.acquire", timeout) from e
        else:
            await self._lock.acquire()

        return self._lock

    def release(self) -> None:
        """Libère le lock."""
        self._lock.release()

    def locked(self) -> bool:
        """Vérifie si le lock est acquis."""
        return self._lock.locked()

    @asynccontextmanager
    async def __call__(self, *, timeout: float | None = None) -> AsyncIterator[None]:
        """Context manager pour le lock.

        Args:
            timeout: Timeout en secondes.
        """
        await self.acquire(timeout=timeout)
        try:
            yield
        finally:
            self.release()


# ============================================================================
# FILES D'ATTENTE
# ============================================================================


class PriorityAsyncQueue(Generic[T]):
    """File d'attente asynchrone avec priorités.

    Les items avec priorité plus basse sont traités en premier.

    Example:
        >>> queue = PriorityAsyncQueue()
        >>> await queue.put("low_priority", priority=10)
        >>> await queue.put("high_priority", priority=1)
        >>> item = await queue.get()  # "high_priority"
    """

    def __init__(self, maxsize: int = 0) -> None:
        """Initialise la file.

        Args:
            maxsize: Taille maximale (0 = illimité).
        """
        self._queue: asyncio.PriorityQueue[tuple[int, int, T]] = asyncio.PriorityQueue(
            maxsize=maxsize
        )
        self._counter = 0  # Pour garantir l'ordre FIFO pour mêmes priorités

    async def put(self, item: T, *, priority: int = 0) -> None:
        """Ajoute un item à la file.

        Args:
            item: Item à ajouter.
            priority: Priorité (plus bas = plus prioritaire).
        """
        self._counter += 1
        await self._queue.put((priority, self._counter, item))

    def put_nowait(self, item: T, *, priority: int = 0) -> None:
        """Ajoute un item sans attendre.

        Args:
            item: Item à ajouter.
            priority: Priorité.

        Raises:
            QueueFullError: Si la file est pleine.
        """
        try:
            self._counter += 1
            self._queue.put_nowait((priority, self._counter, item))
        except asyncio.QueueFull as e:
            raise QueueFullError("PriorityAsyncQueue", self._queue.maxsize) from e

    async def get(self) -> T:
        """Récupère le prochain item.

        Returns:
            Item avec la priorité la plus basse.
        """
        _, _, item = await self._queue.get()
        return item

    def get_nowait(self) -> T:
        """Récupère le prochain item sans attendre.

        Returns:
            Item.

        Raises:
            asyncio.QueueEmpty: Si la file est vide.
        """
        _, _, item = self._queue.get_nowait()
        return item

    def task_done(self) -> None:
        """Indique qu'une tâche est terminée."""
        self._queue.task_done()

    async def join(self) -> None:
        """Attend que toutes les tâches soient terminées."""
        await self._queue.join()

    def qsize(self) -> int:
        """Taille actuelle de la file."""
        return self._queue.qsize()

    def empty(self) -> bool:
        """Vérifie si la file est vide."""
        return self._queue.empty()

    def full(self) -> bool:
        """Vérifie si la file est pleine."""
        return self._queue.full()


class BoundedAsyncQueue(Generic[T]):
    """File d'attente asynchrone avec limite stricte.

    Lève une exception si on tente d'ajouter quand la file est pleine.

    Example:
        >>> queue = BoundedAsyncQueue(maxsize=100)
        >>> await queue.put(item)  # Lève QueueFullError si pleine
    """

    def __init__(self, maxsize: int) -> None:
        """Initialise la file.

        Args:
            maxsize: Taille maximale.
        """
        if maxsize <= 0:
            raise ValueError(f"maxsize doit être > 0, reçu {maxsize}")

        self._queue: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)
        self._maxsize = maxsize

    async def put(self, item: T) -> None:
        """Ajoute un item à la file.

        Args:
            item: Item à ajouter.

        Raises:
            QueueFullError: Si la file est pleine.
        """
        if self._queue.full():
            raise QueueFullError("BoundedAsyncQueue", self._maxsize)
        await self._queue.put(item)

    def put_nowait(self, item: T) -> None:
        """Ajoute un item sans attendre.

        Args:
            item: Item à ajouter.

        Raises:
            QueueFullError: Si la file est pleine.
        """
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull as e:
            raise QueueFullError("BoundedAsyncQueue", self._maxsize) from e

    async def get(self) -> T:
        """Récupère le prochain item."""
        return await self._queue.get()

    def get_nowait(self) -> T:
        """Récupère le prochain item sans attendre."""
        return self._queue.get_nowait()

    def task_done(self) -> None:
        """Indique qu'une tâche est terminée."""
        self._queue.task_done()

    async def join(self) -> None:
        """Attend que toutes les tâches soient terminées."""
        await self._queue.join()

    def qsize(self) -> int:
        """Taille actuelle."""
        return self._queue.qsize()

    def empty(self) -> bool:
        """Vérifie si vide."""
        return self._queue.empty()

    def full(self) -> bool:
        """Vérifie si pleine."""
        return self._queue.full()


# ============================================================================
# PATTERNS DE CONCURRENCE
# ============================================================================


class RateLimiter:
    """Limiteur de débit asynchrone (token bucket).

    Permet de limiter le nombre d'appels par seconde.

    Example:
        >>> limiter = RateLimiter(rate=10.0)  # 10 appels/seconde
        >>> await limiter.acquire()
        >>> await some_api_call()
    """

    def __init__(self, rate: float, *, burst: int = 1) -> None:
        """Initialise le limiteur.

        Args:
            rate: Nombre d'appels par seconde.
            burst: Nombre maximum d'appels en rafale.
        """
        if rate <= 0:
            raise ValueError(f"rate doit être > 0, reçu {rate}")
        if burst <= 0:
            raise ValueError(f"burst doit être > 0, reçu {burst}")

        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Acquiert un token (bloquant si nécessaire)."""
        async with self._lock:
            while True:
                self._update_tokens()

                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return

                # Calculer le temps d'attente
                wait_time = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait_time)

    def _update_tokens(self) -> None:
        """Met à jour le nombre de tokens disponibles."""
        now = time.monotonic()
        elapsed = now - self._last_update
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._last_update = now


class CircuitBreaker:
    """Circuit breaker pour protéger les appels externes.

    Après un certain nombre d'échecs, le circuit s'ouvre et rejette
    immédiatement les appels pendant un temps de réinitialisation.

    Example:
        >>> breaker = CircuitBreaker(failure_threshold=5, reset_timeout=60.0)
        >>> async with breaker:
        ...     await external_api_call()
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout: float = 60.0,
        *,
        name: str = "",
    ) -> None:
        """Initialise le circuit breaker.

        Args:
            failure_threshold: Nombre d'échecs avant ouverture.
            reset_timeout: Temps avant tentative de réinitialisation.
            name: Nom du circuit (pour logging).
        """
        if failure_threshold <= 0:
            raise ValueError(f"failure_threshold doit être > 0")
        if reset_timeout <= 0:
            raise ValueError(f"reset_timeout doit être > 0")

        self._failure_threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._name = name
        self._failures = 0
        self._last_failure_time: float | None = None
        self._state = "closed"  # closed, open, half-open
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        """État actuel du circuit."""
        return self._state

    @property
    def failures(self) -> int:
        """Nombre d'échecs actuels."""
        return self._failures

    async def __aenter__(self) -> CircuitBreaker:
        async with self._lock:
            if self._state == "open":
                # Vérifier si on peut passer en half-open
                if self._last_failure_time is not None:
                    elapsed = time.monotonic() - self._last_failure_time
                    if elapsed >= self._reset_timeout:
                        self._state = "half-open"
                    else:
                        reset_time = self._reset_timeout - elapsed
                        raise CircuitBreakerOpenError(
                            self._name,
                            self._failures,
                            reset_time,
                        )

        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, *args: Any) -> None:
        async with self._lock:
            if exc_type is not None:
                # Échec
                self._failures += 1
                self._last_failure_time = time.monotonic()

                if self._failures >= self._failure_threshold:
                    self._state = "open"
            else:
                # Succès
                if self._state == "half-open":
                    self._state = "closed"
                self._failures = 0

    def reset(self) -> None:
        """Réinitialise manuellement le circuit breaker."""
        self._failures = 0
        self._last_failure_time = None
        self._state = "closed"


# ============================================================================
# HELPERS DIVERS
# ============================================================================


async def sleep_until(condition: Callable[[], bool], *, timeout: float | None = None) -> None:
    """Attend jusqu'à ce qu'une condition soit vraie.

    Args:
        condition: Fonction qui retourne True quand l'attente doit se terminer.
        timeout: Timeout en secondes (None = infini).

    Raises:
        TimeoutError: Si le timeout est dépassé.

    Example:
        >>> await sleep_until(lambda: is_ready(), timeout=30.0)
    """
    start_time = time.monotonic()

    while not condition():
        if timeout is not None:
            elapsed = time.monotonic() - start_time
            if elapsed >= timeout:
                raise TimeoutError("sleep_until", timeout)

        await asyncio.sleep(0.1)


async def debounce(
    func: Callable[..., Awaitable[T]],
    wait: float,
) -> Callable[..., Awaitable[T]]:
    """Décorateur debounce pour fonctions asynchrones.

    Retarde l'exécution jusqu'à ce qu'un certain temps se soit écoulé
    sans nouvel appel.

    Args:
        func: Fonction à débouncer.
        wait: Temps d'attente en secondes.

    Returns:
        Fonction décorée.

    Example:
        >>> @debounce
        ... async def search(query: str):
        ...     await perform_search(query)
    """
    task: asyncio.Task[T] | None = None

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        nonlocal task

        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        async def _delayed_call() -> T:
            await asyncio.sleep(wait)
            return await func(*args, **kwargs)

        task = asyncio.create_task(_delayed_call())
        return await task

    return wrapper


async def throttle(
    func: Callable[..., Awaitable[T]],
    period: float,
) -> Callable[..., Awaitable[T]]:
    """Décorateur throttle pour fonctions asynchrones.

    Limite l'exécution à une fois par période.

    Args:
        func: Fonction à throttler.
        period: Période minimale entre les appels.

    Returns:
        Fonction décorée.

    Example:
        >>> @throttle(period=1.0)
        ... async def update_status():
        ...     await send_status_update()
    """
    last_call = 0.0
    lock = asyncio.Lock()

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        nonlocal last_call

        async with lock:
            now = time.monotonic()
            elapsed = now - last_call

            if elapsed < period:
                await asyncio.sleep(period - elapsed)

            last_call = time.monotonic()
            return await func(*args, **kwargs)

    return wrapper


def run_sync(coro: Awaitable[T]) -> T:
    """Exécute une coroutine de manière synchrone.

    Utile pour appeler du code async depuis du code sync.

    Args:
        coro: Coroutine à exécuter.

    Returns:
        Résultat de la coroutine.

    Example:
        >>> result = run_sync(fetch_data())
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # Déjà dans un event loop, utiliser nest_asyncio ou créer un nouveau thread
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    else:
        return asyncio.run(coro)


async def to_thread(
    func: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    """Exécute une fonction synchrone dans un thread.

    Wrapper autour de asyncio.to_thread avec support des kwargs.

    Args:
        func: Fonction synchrone.
        *args: Arguments positionnels.
        **kwargs: Arguments nommés.

    Returns:
        Résultat de la fonction.

    Example:
        >>> result = await to_thread(cpu_intensive_function, data)
    """
    if kwargs:
        # asyncio.to_thread ne supporte pas les kwargs nativement
        wrapped = functools.partial(func, **kwargs)
        return await asyncio.to_thread(wrapped, *args)
    else:
        return await asyncio.to_thread(func, *args)


# ============================================================================
# MONITORING
# ============================================================================


class AsyncTaskTracker:
    """Tracker pour les tâches asynchrones avec statistiques.

    Example:
        >>> tracker = AsyncTaskTracker()
        >>> task = tracker.track(some_async_func(), name="download")
        >>> await task
        >>> print(tracker.stats)
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskInfo] = {}
        self._task_counter = 0
        self._lock = asyncio.Lock()

    def track(
        self,
        coro: Awaitable[T],
        *,
        name: str = "",
    ) -> asyncio.Task[T]:
        """Crée une tâche trackée.

        Args:
            coro: Coroutine à exécuter.
            name: Nom de la tâche.

        Returns:
            Tâche asyncio.
        """
        self._task_counter += 1
        task_id = f"task_{self._task_counter}"

        task_info = TaskInfo(
            task_id=task_id,
            name=name,
            state=TaskState.PENDING,
            created_at=datetime.now(UTC),
        )

        self._tasks[task_id] = task_info

        async def _wrapper() -> T:
            async with self._lock:
                self._tasks[task_id] = task_info.model_copy(
                    update={
                        "state": TaskState.RUNNING,
                        "started_at": datetime.now(UTC),
                    }
                )

            try:
                result = await coro
                async with self._lock:
                    self._tasks[task_id] = task_info.model_copy(
                        update={
                            "state": TaskState.COMPLETED,
                            "completed_at": datetime.now(UTC),
                            "duration_ms": (
                                (datetime.now(UTC) - task_info.started_at).total_seconds() * 1000
                                if task_info.started_at
                                else 0.0
                            ),
                        }
                    )
                return result
            except asyncio.CancelledError:
                async with self._lock:
                    self._tasks[task_id] = task_info.model_copy(
                        update={
                            "state": TaskState.CANCELLED,
                            "completed_at": datetime.now(UTC),
                        }
                    )
                raise
            except TimeoutError as e:
                async with self._lock:
                    self._tasks[task_id] = task_info.model_copy(
                        update={
                            "state": TaskState.TIMEOUT,
                            "completed_at": datetime.now(UTC),
                            "error": str(e),
                        }
                    )
                raise
            except Exception as e:
                async with self._lock:
                    self._tasks[task_id] = task_info.model_copy(
                        update={
                            "state": TaskState.FAILED,
                            "completed_at": datetime.now(UTC),
                            "error": str(e),
                        }
                    )
                raise

        return asyncio.create_task(_wrapper(), name=task_id)

    @property
    def stats(self) -> AsyncStats:
        """Statistiques des tâches."""
        tasks = list(self._tasks.values())

        total = len(tasks)
        completed = sum(1 for t in tasks if t.state == TaskState.COMPLETED)
        failed = sum(1 for t in tasks if t.state == TaskState.FAILED)
        cancelled = sum(1 for t in tasks if t.state == TaskState.CANCELLED)
        timeout = sum(1 for t in tasks if t.state == TaskState.TIMEOUT)

        durations = [t.duration_ms for t in tasks if t.duration_ms > 0]
        total_duration = sum(durations)
        avg_duration = total_duration / len(durations) if durations else 0.0

        return AsyncStats(
            total_tasks=total,
            completed_tasks=completed,
            failed_tasks=failed,
            cancelled_tasks=cancelled,
            timeout_tasks=timeout,
            total_duration_ms=total_duration,
            average_duration_ms=avg_duration,
        )

    def get_task(self, task_id: str) -> TaskInfo | None:
        """Récupère les informations d'une tâche.

        Args:
            task_id: Identifiant de la tâche.

        Returns:
            TaskInfo ou None.
        """
        return self._tasks.get(task_id)

    def list_tasks(self, *, state: TaskState | None = None) -> list[TaskInfo]:
        """Liste les tâches.

        Args:
            state: Filtrer par état (None = toutes).

        Returns:
            Liste des TaskInfo.
        """
        tasks = list(self._tasks.values())
        if state is not None:
            tasks = [t for t in tasks if t.state == state]
        return tasks


# ============================================================================
# CONTEXT MANAGERS
# ============================================================================


@asynccontextmanager
async def timeout_context(
    timeout: float,
    *,
    operation_name: str = "operation",
) -> AsyncIterator[None]:
    """Context manager pour timeout.

    Args:
        timeout: Timeout en secondes.
        operation_name: Nom de l'opération.

    Raises:
        TimeoutError: Si le timeout est dépassé.

    Example:
        >>> async with timeout_context(30.0, operation_name="download"):
        ...     await download_file()
    """
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("timeout_context must be used within an async function")

    timeout_handle = asyncio.get_event_loop().call_later(
        timeout,
        task.cancel,
    )

    try:
        yield
    except asyncio.CancelledError:
        raise TimeoutError(operation_name, timeout)
    finally:
        timeout_handle.cancel()


@asynccontextmanager
async def semaphore_context(
    semaphore: asyncio.Semaphore,
    *,
    timeout: float | None = None,
) -> AsyncIterator[None]:
    """Context manager pour sémaphore avec timeout optionnel.

    Args:
        semaphore: Sémaphore à acquérir.
        timeout: Timeout en secondes.

    Raises:
        TimeoutError: Si le timeout est dépassé.

    Example:
        >>> sem = asyncio.Semaphore(5)
        >>> async with semaphore_context(sem, timeout=10.0):
        ...     await limited_operation()
    """
    if timeout is not None:
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
        except asyncio.TimeoutError as e:
            raise TimeoutError("semaphore.acquire", timeout) from e
    else:
        await semaphore.acquire()

    try:
        yield
    finally:
        semaphore.release()


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_TIMEOUT",
    "MAX_TIMEOUT",
    "DEFAULT_RETRY_DELAY",
    "MAX_RETRY_DELAY",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_QUEUE_SIZE",
    # Exceptions
    "AsyncError",
    "TimeoutError",
    "RetryExhaustedError",
    "QueueFullError",
    "CircuitBreakerOpenError",
    # Enums
    "TaskState",
    "BackoffStrategy",
    # Modèles
    "TaskInfo",
    "AsyncStats",
    # Gestion des tâches
    "run_with_timeout",
    "retry_async",
    "gather_with_limit",
    "gather_with_timeout",
    # Synchronisation
    "AsyncSemaphore",
    "AsyncLockWithTimeout",
    # Files d'attente
    "PriorityAsyncQueue",
    "BoundedAsyncQueue",
    # Patterns de concurrence
    "RateLimiter",
    "CircuitBreaker",
    # Helpers divers
    "sleep_until",
    "debounce",
    "throttle",
    "run_sync",
    "to_thread",
    # Monitoring
    "AsyncTaskTracker",
    # Context managers
    "timeout_context",
    "semaphore_context",
]
