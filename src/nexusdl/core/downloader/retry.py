"""Stratégies de retry intelligentes pour les téléchargements.

Ce module fournit un système de retry unifié et configurable pour gérer
les erreurs transitoires lors des téléchargements. Il classifie les erreurs
en trois catégories et applique des stratégies de backoff adaptées :

1. **Erreurs toujours retryables** : erreurs réseau (ConnectionError,
   TimeoutError, DNS), erreurs HTTP 5xx, erreurs I/O disque.
2. **Erreurs conditionnellement retryables** : HTTP 429 (rate limit),
   503 (maintenance), CloudflareBypassError, AuthenticationRequiredError
   (après refresh des cookies).
3. **Erreurs non retryables** : HTTP 404, 403, 401, ParsingError,
   MangaNotFoundError, ChapterNotFoundError.

Le module fournit :
    - Des **retry policies** prédéfinies (NETWORK, PARSER, DOWNLOAD, etc.)
    - Des **décorateurs** (@network_retry, @download_retry, etc.)
    - Un **context manager** (retry_context) pour les cas complexes
    - Des **callbacks** de logging et de notification EventBus
    - Un **jitter** aléatoire pour éviter le thundering herd

Exemple d'utilisation — Décorateur :
    >>> from nexusdl.core.downloader.retry import network_retry
    >>>
    >>> @network_retry(max_attempts=5)
    ... async def fetch_page(url: str) -> httpx.Response:
    ...     return await session.get(url)

Exemple d'utilisation — Context manager :
    >>> from nexusdl.core.downloader.retry import retry_context, NETWORK_POLICY
    >>>
    >>> async with retry_context(NETWORK_POLICY) as ctx:
    ...     async for attempt in ctx:
    ...         try:
    ...             result = await do_something()
    ...             break
    ...         except ConnectionError as e:
    ...             if not ctx.should_retry(e, attempt):
    ...                 raise

Exemple d'utilisation — Policy custom :
    >>> from nexusdl.core.downloader.retry import RetryConfig, RetryStrategy, build_retry
    >>>
    >>> config = RetryConfig(
    ...     max_attempts=4,
    ...     strategy=RetryStrategy.EXPONENTIAL,
    ...     initial_delay=1.0,
    ...     max_delay=30.0,
    ...     multiplier=2.0,
    ...     jitter=0.2,
    ... )
    >>> retry_decorator = build_retry(config, retryable_exceptions=(ConnectionError,))
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, ClassVar, Final, Self
from uuid import UUID

import tenacity
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.events import EventBus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.parsers.exceptions import (
    AuthenticationRequiredError,
    ChapterNotFoundError,
    CloudflareBypassError,
    InvalidPageUrlError,
    MangaNotFoundError,
    ParserError,
    ParsingError,
    RateLimitExceededError,
    SiteUnavailableError,
)


# ============================================================================
# EXCEPTIONS
# ============================================================================


class RetryError(NexusDLError):
    """Exception de base pour les erreurs du système de retry."""


class MaxRetriesExceededError(RetryError):
    """Exception levée lorsque le nombre maximum de tentatives est dépassé.

    Contient la dernière exception rencontrée et les statistiques de retry.
    """

    def __init__(
        self,
        message: str,
        *,
        last_exception: BaseException | None = None,
        attempts: int = 0,
        total_delay_seconds: float = 0.0,
    ) -> None:
        super().__init__(message)
        self.last_exception = last_exception
        self.attempts = attempts
        self.total_delay_seconds = total_delay_seconds


class InvalidRetryConfigError(RetryError):
    """Exception levée lorsqu'une configuration de retry est invalide."""


# ============================================================================
# ENUMS
# ============================================================================


class RetryStrategy(str, Enum):
    """Stratégie de backoff pour les retries.

    FIXED        : délai constant entre chaque tentative.
    LINEAR       : délai linéaire (delay * attempt_number).
    EXPONENTIAL  : délai exponentiel (delay * multiplier^attempt).
    FIBONACCI    : délai basé sur la suite de Fibonacci.
    """

    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    FIBONACCI = "fibonacci"


class ErrorClass(str, Enum):
    """Classification des erreurs pour déterminer si un retry est pertinent.

    RETRYABLE           : Toujours retryable (erreurs réseau, I/O).
    CONDITIONALLY_RETRYABLE : Retryable sous conditions (rate limit, Cloudflare).
    NON_RETRYABLE       : Jamais retryable (404, 403, parsing errors).
    """

    RETRYABLE = "retryable"
    CONDITIONALLY_RETRYABLE = "conditionally_retryable"
    NON_RETRYABLE = "non_retryable"


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class RetryConfig(BaseModel):
    """Configuration complète d'une stratégie de retry.

    Cette configuration est utilisée par `build_retry()` pour construire
    un décorateur tenacity ou un context manager de retry.
    """

    max_attempts: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Nombre maximum de tentatives (incluant la première).",
    )
    strategy: RetryStrategy = Field(
        default=RetryStrategy.EXPONENTIAL,
        description="Stratégie de backoff à appliquer.",
    )
    initial_delay: float = Field(
        default=1.0,
        ge=0.0,
        le=60.0,
        description="Délai initial en secondes avant la première retry.",
    )
    max_delay: float = Field(
        default=60.0,
        ge=0.0,
        le=600.0,
        description="Délai maximum entre deux tentatives (plafond).",
    )
    multiplier: float = Field(
        default=2.0,
        gt=0.0,
        le=10.0,
        description="Multiplicateur pour la stratégie exponentielle.",
    )
    jitter: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Fraction de jitter aléatoire (0.0 = aucun, 0.2 = ±20%).",
    )
    timeout_per_attempt: float | None = Field(
        default=None,
        gt=0.0,
        description="Timeout par tentative en secondes (None = infini).",
    )
    overall_timeout: float | None = Field(
        default=None,
        gt=0.0,
        description="Timeout global pour toutes les tentatives (None = infini).",
    )
    retry_on_exceptions: tuple[str, ...] = Field(
        default=(),
        description="Noms qualifiés des exceptions sur lesquelles retryer.",
    )
    log_level: str = Field(
        default="WARNING",
        description="Niveau de log pour les tentatives échouées.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    def validate_strategy_params(self) -> None:
        """Valide la cohérence des paramètres selon la stratégie.

        Raises:
            InvalidRetryConfigError: Si les paramètres sont incohérents.
        """
        if self.initial_delay > self.max_delay:
            raise InvalidRetryConfigError(
                f"initial_delay ({self.initial_delay}s) > max_delay ({self.max_delay}s)"
            )
        if self.strategy == RetryStrategy.EXPONENTIAL and self.multiplier <= 1.0:
            raise InvalidRetryConfigError(
                f"multiplier must be > 1.0 for EXPONENTIAL strategy, got {self.multiplier}"
            )


class RetryAttempt(BaseModel):
    """Snapshot immuable d'une tentative de retry.

    Émis via l'EventBus à chaque tentative pour permettre aux interfaces
    d'afficher la progression des retries.
    """

    attempt_number: int = Field(..., ge=1, description="Numéro de la tentative (1-based).")
    max_attempts: int = Field(..., ge=1, description="Nombre maximum de tentatives.")
    exception_type: str = Field(..., description="Nom du type d'exception rencontrée.")
    exception_message: str = Field(default="", description="Message de l'exception.")
    delay_before_next: float = Field(
        default=0.0, ge=0.0, description="Délai avant la prochaine tentative (secondes)."
    )
    elapsed_seconds: float = Field(default=0.0, ge=0.0, description="Temps écoulé total.")
    context: dict[str, Any] = Field(
        default_factory=dict, description="Contexte additionnel (task_id, url, etc.)."
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de la tentative.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def is_last_attempt(self) -> bool:
        """Indique si c'est la dernière tentative."""
        return self.attempt_number >= self.max_attempts


class RetryStats(BaseModel):
    """Statistiques agrégées des retries sur une opération.

    Utile pour le monitoring et les rapports de performance.
    """

    total_attempts: int = Field(default=0, ge=0)
    successful: bool = Field(default=False)
    total_delay_seconds: float = Field(default=0.0, ge=0.0)
    total_duration_seconds: float = Field(default=0.0, ge=0.0)
    exceptions_encountered: list[str] = Field(default_factory=list)
    final_exception: str | None = Field(default=None)

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# CLASSIFICATION DES ERREURS
# ============================================================================


# Exceptions toujours retryables (erreurs réseau, I/O, timeouts)
_RETRYABLE_EXCEPTIONS: Final[tuple[type[BaseException], ...]] = (
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
    OSError,
    SiteUnavailableError,
)

# Exceptions conditionnellement retryables (rate limit, Cloudflare, auth)
_CONDITIONALLY_RETRYABLE_EXCEPTIONS: Final[tuple[type[BaseException], ...]] = (
    RateLimitExceededError,
    CloudflareBypassError,
    AuthenticationRequiredError,
)

# Exceptions jamais retryables (erreurs définitives)
_NON_RETRYABLE_EXCEPTIONS: Final[tuple[type[BaseException], ...]] = (
    MangaNotFoundError,
    ChapterNotFoundError,
    InvalidPageUrlError,
    ParsingError,
    KeyboardInterrupt,
    asyncio.CancelledError,
)


def classify_error(error: BaseException) -> ErrorClass:
    """Classifie une erreur pour déterminer si un retry est pertinent.

    La classification suit une hiérarchie stricte :
        1. Non-retryable (404, parsing, cancel) → NON_RETRYABLE
        2. Conditionnellement retryable (429, Cloudflare) → CONDITIONALLY_RETRYABLE
        3. Retryable (réseau, I/O) → RETRYABLE
        4. Inconnu → CONDITIONALLY_RETRYABLE (principe de précaution)

    Args:
        error: Exception à classifier.

    Returns:
        La classe d'erreur déterminée.

    Example:
        >>> classify_error(ConnectionError("timeout"))
        <ErrorClass.RETRYABLE: 'retryable'>
        >>> classify_error(MangaNotFoundError("..."))
        <ErrorClass.NON_RETRYABLE: 'non_retryable'>
    """
    # Toujours respecter l'annulation
    if isinstance(error, (KeyboardInterrupt, asyncio.CancelledError)):
        return ErrorClass.NON_RETRYABLE

    # Vérifier les non-retryables en premier (plus spécifique)
    if isinstance(error, _NON_RETRYABLE_EXCEPTIONS):
        return ErrorClass.NON_RETRYABLE

    # Puis les conditionnellement retryables
    if isinstance(error, _CONDITIONALLY_RETRYABLE_EXCEPTIONS):
        # Pour RateLimitExceededError, vérifier le retry_after
        if isinstance(error, RateLimitExceededError):
            if error.details.get("retry_after") is not None:
                return ErrorClass.CONDITIONALLY_RETRYABLE
            # Pas de retry_after → retryable quand même
            return ErrorClass.CONDITIONALLY_RETRYABLE
        return ErrorClass.CONDITIONALLY_RETRYABLE

    # Enfin les retryables
    if isinstance(error, _RETRYABLE_EXCEPTIONS):
        return ErrorClass.RETRYABLE

    # Cas par défaut : exceptions HTTP génériques
    error_name = type(error).__name__.lower()
    if "timeout" in error_name or "connection" in error_name or "network" in error_name:
        return ErrorClass.RETRYABLE

    # Principe de précaution : si inconnu, retryable conditionnellement
    return ErrorClass.CONDITIONALLY_RETRYABLE


def is_retryable(error: BaseException) -> bool:
    """Détermine si une erreur justifie un retry.

    Args:
        error: Exception à tester.

    Returns:
        True si l'erreur est retryable ou conditionnellement retryable.
    """
    classification = classify_error(error)
    return classification in (ErrorClass.RETRYABLE, ErrorClass.CONDITIONALLY_RETRYABLE)


# ============================================================================
# CALCUL DU DÉLAI (BACKOFF + JITTER)
# ============================================================================


def compute_delay(
    attempt: int,
    config: RetryConfig,
) -> float:
    """Calcule le délai avant la prochaine tentative avec jitter.

    Args:
        attempt: Numéro de la tentative (1-based, 1 = première retry).
        config: Configuration de retry.

    Returns:
        Délai en secondes, borné par `max_delay`.

    Example:
        >>> config = RetryConfig(strategy=RetryStrategy.EXPONENTIAL, initial_delay=1.0, multiplier=2.0)
        >>> compute_delay(1, config)  # 1.0 * 2^0 = 1.0s (±jitter)
        >>> compute_delay(2, config)  # 1.0 * 2^1 = 2.0s (±jitter)
        >>> compute_delay(3, config)  # 1.0 * 2^2 = 4.0s (±jitter)
    """
    if attempt <= 0:
        raise ValueError(f"attempt must be >= 1, got {attempt}")

    # Calcul du délai de base selon la stratégie
    if config.strategy == RetryStrategy.FIXED:
        base_delay = config.initial_delay

    elif config.strategy == RetryStrategy.LINEAR:
        base_delay = config.initial_delay * attempt

    elif config.strategy == RetryStrategy.EXPONENTIAL:
        base_delay = config.initial_delay * (config.multiplier ** (attempt - 1))

    elif config.strategy == RetryStrategy.FIBONACCI:
        # Suite de Fibonacci : 1, 1, 2, 3, 5, 8, 13, ...
        a, b = 1, 1
        for _ in range(attempt - 1):
            a, b = b, a + b
        base_delay = config.initial_delay * a

    else:
        base_delay = config.initial_delay

    # Plafonnement
    base_delay = min(base_delay, config.max_delay)

    # Ajout du jitter (±jitter%)
    if config.jitter > 0:
        jitter_range = base_delay * config.jitter
        base_delay += random.uniform(-jitter_range, jitter_range)

    # Garantir un délai positif
    return max(0.0, base_delay)


# ============================================================================
# CALLBACKS DE LOGGING
# ============================================================================


def _log_retry_attempt(
    retry_state: tenacity.RetryCallState,
    *,
    log_level: str = "WARNING",
    context: dict[str, Any] | None = None,
) -> None:
    """Callback tenacity pour logger chaque tentative échouée.

    Args:
        retry_state: État interne tenacity de la retry.
        log_level: Niveau de log à utiliser.
        context: Contexte additionnel pour le log.
    """
    attempt_number = retry_state.attempt_number
    outcome = retry_state.outcome
    if outcome is None:
        return

    exception = outcome.exception() if outcome.failed else None
    if exception is None:
        return

    # Calcul du délai avant la prochaine tentative
    sleep_time = retry_state.next_action.sleep if retry_state.next_action else 0.0

    # Construction du message
    exc_type = type(exception).__name__
    exc_msg = str(exception)[:200]  # Tronquer pour éviter les logs trop longs

    fn_name = retry_state.fn.__name__ if retry_state.fn else "unknown"

    log_method = getattr(logger, log_level.lower(), logger.warning)
    log_method.bind(
        module="retry",
        function=fn_name,
        attempt=attempt_number,
        exception_type=exc_type,
        sleep_time=round(sleep_time, 2),
        **(context or {}),
    ).warning(
        "Retry attempt {}/{} for {} — {} — sleeping {:.2f}s",
        attempt_number,
        retry_state.retry_object.stop.max_attempt_number
        if hasattr(retry_state.retry_object.stop, "max_attempt_number")
        else "?",
        fn_name,
        exc_msg,
        sleep_time,
    )


def build_retry_callback(
    *,
    event_bus: EventBus | None = None,
    task_id: UUID | str | None = None,
    context: dict[str, Any] | None = None,
) -> Callable[[tenacity.RetryCallState], None]:
    """Construit un callback de retry qui log ET émet un événement EventBus.

    Args:
        event_bus: EventBus optionnel pour notifier les interfaces.
        task_id: ID de tâche pour le contexte de l'événement.
        context: Contexte additionnel.

    Returns:
        Callback compatible avec tenacity `before_sleep`.
    """
    merged_context = dict(context or {})
    if task_id is not None:
        merged_context["task_id"] = str(task_id)

    def _callback(retry_state: tenacity.RetryCallState) -> None:
        # Log structuré
        _log_retry_attempt(retry_state, context=merged_context)

        # Émission EventBus (non-bloquante)
        if event_bus is not None:
            outcome = retry_state.outcome
            if outcome is not None and outcome.failed:
                exception = outcome.exception()
                if exception is not None:
                    attempt = RetryAttempt(
                        attempt_number=retry_state.attempt_number,
                        max_attempts=(
                            retry_state.retry_object.stop.max_attempt_number
                            if hasattr(retry_state.retry_object.stop, "max_attempt_number")
                            else 0
                        ),
                        exception_type=type(exception).__name__,
                        exception_message=str(exception)[:500],
                        delay_before_next=(
                            retry_state.next_action.sleep if retry_state.next_action else 0.0
                        ),
                        context=merged_context,
                    )
                    # Fire-and-forget : on ne bloque pas le retry
                    asyncio.create_task(
                        event_bus.emit("retry.attempt", {"attempt": attempt.model_dump(mode="json")})
                    )

    return _callback


# ============================================================================
# CONSTRUCTION DE RETRY (tenacity)
# ============================================================================


def _build_wait_strategy(config: RetryConfig) -> tenacity.wait_base:
    """Construit la stratégie d'attente tenacity à partir de la config.

    Args:
        config: Configuration de retry.

    Returns:
        Stratégie d'attente tenacity.
    """
    if config.strategy == RetryStrategy.FIXED:
        return tenacity.wait_fixed(config.initial_delay)

    if config.strategy == RetryStrategy.LINEAR:
        # tenacity n'a pas de wait_linear natif, on utilise wait_exponential
        # avec multiplier=1.0 et min=initial_delay, max=max_delay
        return tenacity.wait_exponential(
            multiplier=1.0,
            min=config.initial_delay,
            max=config.max_delay,
        )

    if config.strategy == RetryStrategy.EXPONENTIAL:
        return tenacity.wait_exponential(
            multiplier=config.multiplier,
            min=config.initial_delay,
            max=config.max_delay,
        )

    if config.strategy == RetryStrategy.FIBONACCI:
        # tenacity n'a pas de wait_fibonacci, on émule avec wait_exponential
        # en ajustant le multiplier pour approximer Fibonacci
        return tenacity.wait_exponential(
            multiplier=1.618,  # Nombre d'or ≈ ratio Fibonacci
            min=config.initial_delay,
            max=config.max_delay,
        )

    return tenacity.wait_exponential(
        multiplier=config.multiplier,
        min=config.initial_delay,
        max=config.max_delay,
    )


def _build_retry_exceptions(
    retryable_exceptions: Sequence[type[BaseException]] | None,
) -> tenacity.retry_base:
    """Construit la condition de retry tenacity à partir des exceptions.

    Args:
        retryable_exceptions: Liste d'exceptions sur lesquelles retryer.
                              Si None, utilise la classification automatique.

    Returns:
        Condition de retry tenacity.
    """
    if retryable_exceptions is not None:
        return tenacity.retry_if_exception_type(tuple(retryable_exceptions))

    # Classification automatique : retry sur RETRYABLE + CONDITIONALLY_RETRYABLE
    all_retryable = _RETRYABLE_EXCEPTIONS + _CONDITIONALLY_RETRYABLE_EXCEPTIONS
    return tenacity.retry_if_exception_type(all_retryable)


def build_retry(
    config: RetryConfig,
    *,
    retryable_exceptions: Sequence[type[BaseException]] | None = None,
    event_bus: EventBus | None = None,
    task_id: UUID | str | None = None,
    context: dict[str, Any] | None = None,
    reraise: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Construit un décorateur de retry à partir d'une configuration.

    C'est le point d'entrée principal pour créer des retry decorators
    personnalisés. Il encapsule la complexité de tenacity et fournit
    une API cohérente.

    Args:
        config: Configuration de retry.
        retryable_exceptions: Exceptions sur lesquelles retryer.
                              Si None, utilise la classification automatique.
        event_bus: EventBus optionnel pour notifier les interfaces.
        task_id: ID de tâche pour le contexte.
        context: Contexte additionnel pour les logs/événements.
        reraise: Si True, relance la dernière exception après épuisement.

    Returns:
        Décorateur applicable à une fonction synchrone ou asynchrone.

    Example:
        >>> config = RetryConfig(max_attempts=5, strategy=RetryStrategy.EXPONENTIAL)
        >>> @build_retry(config, retryable_exceptions=(ConnectionError,))
        ... async def fetch(url: str) -> httpx.Response:
        ...     return await session.get(url)
    """
    config.validate_strategy_params()

    wait_strategy = _build_wait_strategy(config)
    retry_condition = _build_retry_exceptions(retryable_exceptions)
    before_sleep = build_retry_callback(
        event_bus=event_bus,
        task_id=task_id,
        context=context,
    )

    stop_strategy: tenacity.stop_base = tenacity.stop_after_attempt(config.max_attempts)

    # Timeout global si configuré
    if config.overall_timeout is not None:
        stop_strategy = tenacity.stop_any(
            stop_strategy,
            tenacity.stop_after_delay(config.overall_timeout),
        )

    return tenacity.retry(
        retry_condition,
        stop=stop_strategy,
        wait=wait_strategy,
        before_sleep=before_sleep,
        reraise=reraise,
    )


# ============================================================================
# RETRY POLICIES PRÉDÉFINIES
# ============================================================================


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Politique de retry prédéfinie pour un cas d'usage spécifique.

    Une policy regroupe :
        - Une configuration de retry (RetryConfig)
        - Les exceptions retryables
        - Un nom descriptif pour le logging
    """

    name: str
    config: RetryConfig
    retryable_exceptions: tuple[type[BaseException], ...]
    description: str = ""

    def build_decorator(
        self,
        *,
        event_bus: EventBus | None = None,
        task_id: UUID | str | None = None,
        context: dict[str, Any] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Construit le décorateur tenacity associé à cette policy.

        Args:
            event_bus: EventBus optionnel.
            task_id: ID de tâche.
            context: Contexte additionnel.

        Returns:
            Décorateur applicable.
        """
        return build_retry(
            self.config,
            retryable_exceptions=self.retryable_exceptions,
            event_bus=event_bus,
            task_id=task_id,
            context=context,
        )


# Policy pour les requêtes HTTP réseau (rapide, 3 tentatives)
NETWORK_POLICY: Final[RetryPolicy] = RetryPolicy(
    name="network",
    config=RetryConfig(
        max_attempts=3,
        strategy=RetryStrategy.EXPONENTIAL,
        initial_delay=0.5,
        max_delay=10.0,
        multiplier=2.0,
        jitter=0.1,
    ),
    retryable_exceptions=(
        ConnectionError,
        TimeoutError,
        asyncio.TimeoutError,
        OSError,
        SiteUnavailableError,
    ),
    description="Retry pour erreurs réseau transitoires (connexion, timeout, DNS).",
)

# Policy pour les téléchargements de pages (plus tolérant, 5 tentatives)
PAGE_DOWNLOAD_POLICY: Final[RetryPolicy] = RetryPolicy(
    name="page_download",
    config=RetryConfig(
        max_attempts=5,
        strategy=RetryStrategy.EXPONENTIAL,
        initial_delay=1.0,
        max_delay=30.0,
        multiplier=2.0,
        jitter=0.2,
    ),
    retryable_exceptions=(
        ConnectionError,
        TimeoutError,
        asyncio.TimeoutError,
        OSError,
        SiteUnavailableError,
        RateLimitExceededError,
        CloudflareBypassError,
    ),
    description="Retry pour téléchargement de pages (réseau + rate limit + Cloudflare).",
)

# Policy pour les téléchargements de chapitres complets (très tolérant, 4 tentatives)
CHAPTER_DOWNLOAD_POLICY: Final[RetryPolicy] = RetryPolicy(
    name="chapter_download",
    config=RetryConfig(
        max_attempts=4,
        strategy=RetryStrategy.EXPONENTIAL,
        initial_delay=2.0,
        max_delay=60.0,
        multiplier=2.0,
        jitter=0.2,
        overall_timeout=300.0,  # 5 minutes max pour un chapitre
    ),
    retryable_exceptions=(
        ConnectionError,
        TimeoutError,
        asyncio.TimeoutError,
        OSError,
        SiteUnavailableError,
        RateLimitExceededError,
        CloudflareBypassError,
        ParserError,  # Retry sur erreurs de parsing (peut être transitoire)
    ),
    description="Retry pour téléchargement de chapitres complets (très tolérant).",
)

# Policy pour les opérations de parsing (modérée, 3 tentatives)
PARSER_POLICY: Final[RetryPolicy] = RetryPolicy(
    name="parser",
    config=RetryConfig(
        max_attempts=3,
        strategy=RetryStrategy.EXPONENTIAL,
        initial_delay=1.0,
        max_delay=15.0,
        multiplier=2.0,
        jitter=0.15,
    ),
    retryable_exceptions=(
        ConnectionError,
        TimeoutError,
        asyncio.TimeoutError,
        SiteUnavailableError,
        CloudflareBypassError,
        ParserError,
    ),
    description="Retry pour opérations de parsing HTML/JSON.",
)

# Policy pour les opérations critiques (conservateur, 2 tentatives)
CRITICAL_POLICY: Final[RetryPolicy] = RetryPolicy(
    name="critical",
    config=RetryConfig(
        max_attempts=2,
        strategy=RetryStrategy.FIXED,
        initial_delay=2.0,
        max_delay=2.0,
        jitter=0.0,
    ),
    retryable_exceptions=(
        ConnectionError,
        TimeoutError,
        asyncio.TimeoutError,
    ),
    description="Retry conservateur pour opérations critiques (auth, cookies).",
)

# Policy pour les opérations I/O disque (rapide, 3 tentatives)
DISK_IO_POLICY: Final[RetryPolicy] = RetryPolicy(
    name="disk_io",
    config=RetryConfig(
        max_attempts=3,
        strategy=RetryStrategy.FIXED,
        initial_delay=0.2,
        max_delay=0.2,
        jitter=0.1,
    ),
    retryable_exceptions=(
        OSError,
        IOError,
    ),
    description="Retry pour erreurs I/O disque transitoires.",
)

# Policy pour le bypass Cloudflare (spécialisé, 3 tentatives avec long délai)
CLOUDFLARE_BYPASS_POLICY: Final[RetryPolicy] = RetryPolicy(
    name="cloudflare_bypass",
    config=RetryConfig(
        max_attempts=3,
        strategy=RetryStrategy.EXPONENTIAL,
        initial_delay=5.0,
        max_delay=60.0,
        multiplier=3.0,
        jitter=0.1,
        overall_timeout=180.0,  # 3 minutes max
    ),
    retryable_exceptions=(
        CloudflareBypassError,
        ConnectionError,
        TimeoutError,
    ),
    description="Retry pour bypass Cloudflare/anti-bot (délais longs).",
)

# Policy pour le rate limit (respecte le retry_after)
RATE_LIMIT_POLICY: Final[RetryPolicy] = RetryPolicy(
    name="rate_limit",
    config=RetryConfig(
        max_attempts=5,
        strategy=RetryStrategy.EXPONENTIAL,
        initial_delay=10.0,
        max_delay=300.0,  # 5 minutes max
        multiplier=2.0,
        jitter=0.1,
        overall_timeout=600.0,  # 10 minutes max
    ),
    retryable_exceptions=(
        RateLimitExceededError,
    ),
    description="Retry pour rate limit (délais longs, respect du retry_after).",
)

# Registry des policies pour accès par nom
RETRY_POLICIES: Final[dict[str, RetryPolicy]] = {
    "network": NETWORK_POLICY,
    "page_download": PAGE_DOWNLOAD_POLICY,
    "chapter_download": CHAPTER_DOWNLOAD_POLICY,
    "parser": PARSER_POLICY,
    "critical": CRITICAL_POLICY,
    "disk_io": DISK_IO_POLICY,
    "cloudflare_bypass": CLOUDFLARE_BYPASS_POLICY,
    "rate_limit": RATE_LIMIT_POLICY,
}


# ============================================================================
# DÉCORATEURS PRATIQUES
# ============================================================================


def network_retry(
    max_attempts: int = 3,
    *,
    initial_delay: float = 0.5,
    max_delay: float = 10.0,
    event_bus: EventBus | None = None,
    task_id: UUID | str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Décorateur pratique pour les retries réseau.

    Args:
        max_attempts: Nombre maximum de tentatives.
        initial_delay: Délai initial en secondes.
        max_delay: Délai maximum en secondes.
        event_bus: EventBus optionnel.
        task_id: ID de tâche.

    Returns:
        Décorateur applicable.

    Example:
        >>> @network_retry(max_attempts=5)
        ... async def fetch(url: str) -> httpx.Response:
        ...     return await session.get(url)
    """
    config = RetryConfig(
        max_attempts=max_attempts,
        strategy=RetryStrategy.EXPONENTIAL,
        initial_delay=initial_delay,
        max_delay=max_delay,
        multiplier=2.0,
        jitter=0.1,
    )
    return build_retry(
        config,
        retryable_exceptions=NETWORK_POLICY.retryable_exceptions,
        event_bus=event_bus,
        task_id=task_id,
    )


def download_retry(
    max_attempts: int = 5,
    *,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    event_bus: EventBus | None = None,
    task_id: UUID | str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Décorateur pratique pour les retries de téléchargement.

    Args:
        max_attempts: Nombre maximum de tentatives.
        initial_delay: Délai initial en secondes.
        max_delay: Délai maximum en secondes.
        event_bus: EventBus optionnel.
        task_id: ID de tâche.

    Returns:
        Décorateur applicable.
    """
    config = RetryConfig(
        max_attempts=max_attempts,
        strategy=RetryStrategy.EXPONENTIAL,
        initial_delay=initial_delay,
        max_delay=max_delay,
        multiplier=2.0,
        jitter=0.2,
    )
    return build_retry(
        config,
        retryable_exceptions=PAGE_DOWNLOAD_POLICY.retryable_exceptions,
        event_bus=event_bus,
        task_id=task_id,
    )


# ============================================================================
# CONTEXT MANAGER POUR RETRY MANUEL
# ============================================================================


@dataclass(slots=True)
class RetryContext:
    """Contexte de retry pour usage manuel dans une boucle.

    Fournit des méthodes utilitaires pour gérer les retries manuellement
    quand les décorateurs ne suffisent pas (ex: logique conditionnelle
    complexe, streaming, etc.).

    Example:
        >>> async with retry_context(NETWORK_POLICY) as ctx:
        ...     async for attempt in ctx:
        ...         try:
        ...             result = await do_something()
        ...             ctx.record_success()
        ...             break
        ...         except ConnectionError as e:
        ...             if not ctx.should_retry(e, attempt):
        ...                 raise
        ...             await ctx.wait_before_retry(attempt)
    """

    policy: RetryPolicy
    _start_time: float = field(default_factory=time.monotonic)
    _attempts: list[RetryAttempt] = field(default_factory=list)
    _exceptions: list[BaseException] = field(default_factory=list)
    _success: bool = False
    _event_bus: EventBus | None = None
    _context: dict[str, Any] = field(default_factory=dict)

    @property
    def max_attempts(self) -> int:
        """Nombre maximum de tentatives."""
        return self.policy.config.max_attempts

    @property
    def attempts_count(self) -> int:
        """Nombre de tentatives effectuées."""
        return len(self._attempts)

    @property
    def elapsed_seconds(self) -> float:
        """Temps écoulé depuis le début du contexte."""
        return time.monotonic() - self._start_time

    @property
    def stats(self) -> RetryStats:
        """Statistiques agrégées du contexte."""
        return RetryStats(
            total_attempts=len(self._attempts),
            successful=self._success,
            total_delay_seconds=sum(a.delay_before_next for a in self._attempts),
            total_duration_seconds=self.elapsed_seconds,
            exceptions_encountered=[type(e).__name__ for e in self._exceptions],
            final_exception=(
                type(self._exceptions[-1]).__name__ if self._exceptions else None
            ),
        )

    def should_retry(self, error: BaseException, attempt: int) -> bool:
        """Détermine si on doit retryer après cette erreur.

        Args:
            error: Exception rencontrée.
            attempt: Numéro de la tentative (1-based).

        Returns:
            True si on peut retryer (pas la dernière tentative + erreur retryable).
        """
        if attempt >= self.max_attempts:
            return False
        return is_retryable(error)

    async def wait_before_retry(self, attempt: int) -> float:
        """Attend le délai calculé avant la prochaine tentative.

        Args:
            attempt: Numéro de la tentative actuelle (1-based).

        Returns:
            Délai effectivement attendu (secondes).
        """
        delay = compute_delay(attempt, self.policy.config)
        if delay > 0:
            await asyncio.sleep(delay)
        return delay

    def record_attempt(
        self,
        attempt: int,
        exception: BaseException | None,
        delay_before_next: float = 0.0,
    ) -> RetryAttempt:
        """Enregistre une tentative (succès ou échec).

        Args:
            attempt: Numéro de la tentative.
            exception: Exception rencontrée (None si succès).
            delay_before_next: Délai avant la prochaine tentative.

        Returns:
            L'attempt enregistré.
        """
        retry_attempt = RetryAttempt(
            attempt_number=attempt,
            max_attempts=self.max_attempts,
            exception_type=type(exception).__name__ if exception else "",
            exception_message=str(exception)[:500] if exception else "",
            delay_before_next=delay_before_next,
            elapsed_seconds=self.elapsed_seconds,
            context=self._context,
        )
        self._attempts.append(retry_attempt)
        if exception is not None:
            self._exceptions.append(exception)
        return retry_attempt

    def record_success(self) -> None:
        """Marque le contexte comme réussi."""
        self._success = True

    def __aiter__(self) -> AsyncIterator[int]:
        """Itère sur les numéros de tentative (1 à max_attempts)."""
        return self._async_iterator()

    async def _async_iterator(self) -> AsyncIterator[int]:
        """Génère les numéros de tentative."""
        for attempt in range(1, self.max_attempts + 1):
            yield attempt


@asynccontextmanager
async def retry_context(
    policy: RetryPolicy,
    *,
    event_bus: EventBus | None = None,
    context: dict[str, Any] | None = None,
) -> AsyncIterator[RetryContext]:
    """Context manager pour retry manuel avec statistiques.

    Fournit un `RetryContext` qui peut être itéré pour gérer les tentatives
    manuellement. Les statistiques sont automatiquement collectées.

    Args:
        policy: Politique de retry à appliquer.
        event_bus: EventBus optionnel pour notifier les interfaces.
        context: Contexte additionnel pour les logs/événements.

    Yields:
        RetryContext pour gérer les tentatives.

    Example:
        >>> async with retry_context(NETWORK_POLICY) as ctx:
        ...     async for attempt in ctx:
        ...         try:
        ...             result = await risky_operation()
        ...             ctx.record_success()
        ...             break
        ...         except ConnectionError as e:
        ...             if not ctx.should_retry(e, attempt):
        ...                 raise MaxRetriesExceededError(
        ...                     "Max retries exceeded",
        ...                     last_exception=e,
        ...                     attempts=ctx.attempts_count,
        ...                     total_delay_seconds=ctx.stats.total_delay_seconds,
        ...                 ) from e
        ...             delay = await ctx.wait_before_retry(attempt)
        ...             ctx.record_attempt(attempt, e, delay)
    """
    ctx = RetryContext(
        policy=policy,
        _event_bus=event_bus,
        _context=dict(context or {}),
    )

    try:
        yield ctx
    except Exception as e:
        # Si le contexte n'a pas été marqué comme réussi, enregistrer l'échec
        if not ctx._success:
            ctx.record_attempt(ctx.attempts_count + 1, e)
        raise
    finally:
        # Émettre les statistiques finales si EventBus disponible
        if event_bus is not None:
            await event_bus.emit(
                "retry.completed",
                {
                    "policy": policy.name,
                    "stats": ctx.stats.model_dump(mode="json"),
                    "context": ctx._context,
                },
            )


# ============================================================================
# HELPERS POUR RETRY AVEC RATE LIMIT
# ============================================================================


async def retry_with_respect_to_rate_limit(
    operation: Callable[[], Awaitable[Any]],
    *,
    policy: RetryPolicy | None = None,
    event_bus: EventBus | None = None,
    context: dict[str, Any] | None = None,
) -> Any:
    """Exécute une opération en respectant le retry_after des rate limits.

    Si l'opération lève un RateLimitExceededError avec un retry_after,
    attend exactement ce délai avant de retryer (au lieu du backoff standard).

    Args:
        operation: Callable async à exécuter.
        policy: Politique de retry (défaut: RATE_LIMIT_POLICY).
        event_bus: EventBus optionnel.
        context: Contexte additionnel.

    Returns:
        Résultat de l'opération.

    Raises:
        MaxRetriesExceededError: Si le nombre max de tentatives est dépassé.
        BaseException: Si l'erreur n'est pas retryable.
    """
    effective_policy = policy or RATE_LIMIT_POLICY
    max_attempts = effective_policy.config.max_attempts
    total_delay = 0.0
    exceptions: list[BaseException] = []
    start_time = time.monotonic()

    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()
        except RateLimitExceededError as e:
            exceptions.append(e)

            # Respecter le retry_after si fourni
            retry_after = e.details.get("retry_after")
            if retry_after is not None:
                delay = float(retry_after)
            else:
                delay = compute_delay(attempt, effective_policy.config)

            # Vérifier le timeout global
            if (
                effective_policy.config.overall_timeout is not None
                and total_delay + delay > effective_policy.config.overall_timeout
            ):
                raise MaxRetriesExceededError(
                    f"Rate limit retry timeout exceeded after {attempt} attempts",
                    last_exception=e,
                    attempts=attempt,
                    total_delay_seconds=total_delay,
                ) from e

            if attempt >= max_attempts:
                raise MaxRetriesExceededError(
                    f"Rate limit retry exhausted after {attempt} attempts",
                    last_exception=e,
                    attempts=attempt,
                    total_delay_seconds=total_delay,
                ) from e

            logger.warning(
                "Rate limit hit, waiting {:.2f}s before retry {}/{}",
                delay,
                attempt,
                max_attempts,
            )

            if event_bus is not None:
                await event_bus.emit(
                    "retry.rate_limit",
                    {
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "retry_after": delay,
                        "context": context or {},
                    },
                )

            await asyncio.sleep(delay)
            total_delay += delay

        except Exception as e:
            if not is_retryable(e):
                raise

            exceptions.append(e)
            delay = compute_delay(attempt, effective_policy.config)

            if attempt >= max_attempts:
                raise MaxRetriesExceededError(
                    f"Retry exhausted after {attempt} attempts",
                    last_exception=e,
                    attempts=attempt,
                    total_delay_seconds=total_delay,
                ) from e

            logger.warning(
                "Retryable error, waiting {:.2f}s before retry {}/{}: {}",
                delay,
                attempt,
                max_attempts,
                e,
            )

            await asyncio.sleep(delay)
            total_delay += delay

    # Ne devrait jamais arriver, mais au cas où
    raise MaxRetriesExceededError(
        "Retry loop exited unexpectedly",
        attempts=max_attempts,
        total_delay_seconds=total_delay,
    )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "RetryError",
    "MaxRetriesExceededError",
    "InvalidRetryConfigError",
    # Enums
    "RetryStrategy",
    "ErrorClass",
    # Modèles
    "RetryConfig",
    "RetryAttempt",
    "RetryStats",
    "RetryPolicy",
    # Classification
    "classify_error",
    "is_retryable",
    # Calcul
    "compute_delay",
    # Construction
    "build_retry",
    "build_retry_callback",
    # Policies prédéfinies
    "NETWORK_POLICY",
    "PAGE_DOWNLOAD_POLICY",
    "CHAPTER_DOWNLOAD_POLICY",
    "PARSER_POLICY",
    "CRITICAL_POLICY",
    "DISK_IO_POLICY",
    "CLOUDFLARE_BYPASS_POLICY",
    "RATE_LIMIT_POLICY",
    "RETRY_POLICIES",
    # Décorateurs pratiques
    "network_retry",
    "download_retry",
    # Context manager
    "RetryContext",
    "retry_context",
    # Helpers
    "retry_with_respect_to_rate_limit",
]
