"""Client FlareSolverr pour résolution automatique des challenges Cloudflare.

Ce module fournit un client asynchrone pour communiquer avec le service externe
FlareSolverr, qui résout automatiquement les challenges Cloudflare, DataDome,
et autres protections anti-bot. Il est essentiel pour accéder aux sites protégés
qui nécessitent l'exécution de JavaScript ou la résolution de CAPTCHAs.

**Qu'est-ce que FlareSolverr ?**
    FlareSolverr est un service proxy qui utilise un navigateur headless
    (Playwright/Puppeteer) pour résoudre automatiquement les challenges
    Cloudflare et retourner les cookies de clearance nécessaires pour
    accéder au contenu protégé.

**Architecture** :
    FlareSolverrClient
        ├── FlareSolverrConfig (Pydantic) : configuration du service
        ├── ChallengeType (enum) : type de challenge (CF, DD, etc.)
        ├── ResolutionResult (Pydantic) : résultat d'une résolution
        ├── CachedClearance (interne) : cache des cookies par domaine
        └── FlareSolverrStats (Pydantic) : statistiques

**Flux de résolution** :
    1. Détection d'un challenge Cloudflare (via status code 403/503)
    2. Appel à FlareSolverr avec l'URL bloquée
    3. FlareSolverr résout le challenge (navigateur headless)
    4. Retour des cookies de clearance + contenu HTML
    5. Cache des cookies pour réutilisation future
    6. Injection des cookies dans HttpSession pour requêtes suivantes

Exemple d'utilisation :
    >>> client = FlareSolverrClient(
    ...     url="http://localhost:8191",
    ... )
    >>> await client.start()
    >>>
    >>> # Vérifier la disponibilité
    >>> if await client.is_available():
    ...     print("FlareSolverr est disponible")
    >>>
    >>> # Résoudre un challenge Cloudflare
    >>> result = await client.resolve(
    ...     url="https://protected-site.com/manga/123",
    ...     max_timeout=60,
    ... )
    >>> if result.success:
    ...     print(f"Cookies obtenus: {len(result.cookies)}")
    ...     print(f"Contenu HTML: {len(result.html)} chars")
    >>>
    >>> # Utiliser les cookies dans HttpSession
    >>> session.set_cookies(result.cookies, domain="protected-site.com")
    >>> response = await session.get("https://protected-site.com/manga/123")
    >>>
    >>> # Statistiques
    >>> stats = await client.get_stats()
    >>> print(f"Résolutions: {stats.total_resolutions}")
    >>> print(f"Taux de succès: {stats.success_rate:.1%}")
    >>>
    >>> await client.stop()

Dépendances externes :
    - FlareSolverr doit être installé et démarré séparément
    - Docker : docker run -d -p 8191:8191 flaresolverr/flaresolverr:latest
    - Ou installation manuelle : https://github.com/FlareSolverr/FlareSolverr
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Final, Self
from urllib.parse import urlparse

import httpx
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError

if TYPE_CHECKING:
    from nexusdl.core.events import EventBus


# ============================================================================
# EXCEPTIONS
# ============================================================================


class FlareSolverrError(NexusDLError):
    """Exception de base pour les erreurs FlareSolverr."""


class FlareSolverrNotStartedError(FlareSolverrError):
    """Exception levée lorsqu'on utilise le client avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "FlareSolverrClient must be started before use. Call await client.start()"
        )


class FlareSolverrUnavailableError(FlareSolverrError):
    """Exception levée lorsque FlareSolverr n'est pas disponible."""

    def __init__(self, url: str, reason: str = "") -> None:
        msg = f"FlareSolverr indisponible à {url}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.url = url
        self.reason = reason


class ChallengeResolutionError(FlareSolverrError):
    """Exception levée lorsqu'un challenge ne peut être résolu."""

    def __init__(
        self,
        url: str,
        challenge_type: str,
        reason: str = "",
    ) -> None:
        msg = f"Échec de résolution du challenge {challenge_type} pour {url}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.url = url
        self.challenge_type = challenge_type
        self.reason = reason


class TimeoutError(FlareSolverrError):
    """Exception levée lorsque la résolution dépasse le timeout."""

    def __init__(self, url: str, timeout_seconds: float) -> None:
        super().__init__(
            f"Timeout dépassé pour {url} après {timeout_seconds:.1f}s"
        )
        self.url = url
        self.timeout_seconds = timeout_seconds


# ============================================================================
# ENUMS
# ============================================================================


class ChallengeType(str, Enum):
    """Type de challenge à résoudre.

    CLOUDFLARE  : Challenge Cloudflare (JS challenge, CAPTCHA).
    DATADOME    : Challenge DataDome.
    PERIMX      : Challenge PerimeterX/HUMAN.
    UNKNOWN     : Type inconnu (détection automatique).
    """

    CLOUDFLARE = "cloudflare"
    DATADOME = "datadome"
    PERIMX = "perimeterx"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ChallengeType.CLOUDFLARE: "Cloudflare",
            ChallengeType.DATADOME: "DataDome",
            ChallengeType.PERIMX: "PerimeterX",
            ChallengeType.UNKNOWN: "Inconnu",
        }[self]


class ResolutionStatus(str, Enum):
    """Statut d'une résolution de challenge.

    SUCCESS : Challenge résolu avec succès.
    FAILED  : Échec de la résolution.
    TIMEOUT : Timeout dépassé.
    CACHED  : Résolution obtenue depuis le cache.
    """

    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CACHED = "cached"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ResolutionStatus.SUCCESS: "Succès",
            ResolutionStatus.FAILED: "Échec",
            ResolutionStatus.TIMEOUT: "Timeout",
            ResolutionStatus.CACHED: "Cache",
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            ResolutionStatus.SUCCESS: "✅",
            ResolutionStatus.FAILED: "❌",
            ResolutionStatus.TIMEOUT: "⏱️",
            ResolutionStatus.CACHED: "💾",
        }[self]


class ServiceStatus(str, Enum):
    """État du service FlareSolverr.

    AVAILABLE   : Service disponible et fonctionnel.
    UNAVAILABLE : Service indisponible (down ou non démarré).
    DEGRADED    : Service fonctionnel mais lent ou avec erreurs.
    UNKNOWN     : État inconnu (pas encore vérifié).
    """

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ServiceStatus.AVAILABLE: "Disponible",
            ServiceStatus.UNAVAILABLE: "Indisponible",
            ServiceStatus.DEGRADED: "Dégradé",
            ServiceStatus.UNKNOWN: "Inconnu",
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            ServiceStatus.AVAILABLE: "🟢",
            ServiceStatus.UNAVAILABLE: "🔴",
            ServiceStatus.DEGRADED: "🟡",
            ServiceStatus.UNKNOWN: "⚪",
        }[self]

    @property
    def is_usable(self) -> bool:
        """Indique si le service peut être utilisé."""
        return self in (ServiceStatus.AVAILABLE, ServiceStatus.DEGRADED)


# ============================================================================
# MODÈLES PYDANTIC — Configuration
# ============================================================================


class FlareSolverrConfig(BaseModel):
    """Configuration du client FlareSolverr.

    Attributes:
        url: URL du service FlareSolverr (ex: http://localhost:8191).
        api_key: Clé API si authentification requise (optionnel).
        timeout: Timeout par défaut pour les résolutions (secondes).
        max_timeout: Timeout maximum autorisé (secondes).
        cache_enabled: Activer le cache des cookies de clearance.
        cache_ttl_seconds: Durée de vie du cache (secondes).
        max_cache_size: Nombre maximum d'entrées dans le cache.
        health_check_interval: Intervalle entre deux health checks (secondes).
        health_check_timeout: Timeout pour un health check (secondes).
        retry_on_failure: Retryer automatiquement en cas d'échec.
        max_retries: Nombre maximum de tentatives.
        auto_start: Démarrer automatiquement le service si indisponible.
    """

    url: str = Field(
        default="http://localhost:8191",
        min_length=10,
        max_length=500,
        description="URL du service FlareSolverr.",
    )
    api_key: str | None = Field(
        default=None,
        max_length=200,
        description="Clé API si authentification requise.",
    )
    timeout: float = Field(
        default=60.0,
        gt=0.0,
        le=300.0,
        description="Timeout par défaut pour les résolutions (secondes).",
    )
    max_timeout: float = Field(
        default=120.0,
        gt=0.0,
        le=600.0,
        description="Timeout maximum autorisé (secondes).",
    )
    cache_enabled: bool = Field(
        default=True,
        description="Activer le cache des cookies de clearance.",
    )
    cache_ttl_seconds: float = Field(
        default=900.0,  # 15 minutes
        ge=60.0,
        le=86400.0,
        description="Durée de vie du cache (secondes).",
    )
    max_cache_size: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Nombre maximum d'entrées dans le cache.",
    )
    health_check_interval: float = Field(
        default=60.0,
        ge=10.0,
        le=3600.0,
        description="Intervalle entre deux health checks (secondes).",
    )
    health_check_timeout: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
        description="Timeout pour un health check (secondes).",
    )
    retry_on_failure: bool = Field(
        default=True,
        description="Retryer automatiquement en cas d'échec.",
    )
    max_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Nombre maximum de tentatives.",
    )
    auto_start: bool = Field(
        default=False,
        description="Démarrer automatiquement le service si indisponible.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# MODÈLES PYDANTIC — Résultats
# ============================================================================


class ClearanceCookie(BaseModel):
    """Cookie de clearance obtenu après résolution d'un challenge."""

    name: str = Field(..., description="Nom du cookie.")
    value: str = Field(..., description="Valeur du cookie.")
    domain: str = Field(..., description="Domaine du cookie.")
    path: str = Field(default="/", description="Chemin du cookie.")
    expires: datetime | None = Field(
        default=None,
        description="Date d'expiration du cookie.",
    )
    secure: bool = Field(default=False, description="Cookie sécurisé (HTTPS uniquement).")
    http_only: bool = Field(default=False, description="Cookie HTTP-only.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def is_expired(self) -> bool:
        """Indique si le cookie est expiré."""
        if self.expires is None:
            return False
        return datetime.now(UTC) >= self.expires


class ResolutionResult(BaseModel):
    """Résultat d'une résolution de challenge."""

    url: str = Field(..., description="URL résolue.")
    status: ResolutionStatus = Field(..., description="Statut de la résolution.")
    challenge_type: ChallengeType = Field(
        default=ChallengeType.UNKNOWN,
        description="Type de challenge résolu.",
    )
    cookies: list[ClearanceCookie] = Field(
        default_factory=list,
        description="Cookies de clearance obtenus.",
    )
    html: str | None = Field(
        default=None,
        description="Contenu HTML de la page résolue.",
    )
    user_agent: str | None = Field(
        default=None,
        description="User-Agent utilisé pour la résolution.",
    )
    latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Latence de la résolution en millisecondes.",
    )
    from_cache: bool = Field(
        default=False,
        description="True si le résultat vient du cache.",
    )
    error: str | None = Field(
        default=None,
        description="Message d'erreur si échec.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de la résolution.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def success(self) -> bool:
        """Indique si la résolution a réussi."""
        return self.status in (ResolutionStatus.SUCCESS, ResolutionStatus.CACHED)

    @property
    def cookies_dict(self) -> dict[str, str]:
        """Cookies au format dict {name: value} pour httpx."""
        return {c.name: c.value for c in self.cookies}


class FlareSolverrStats(BaseModel):
    """Statistiques globales du client FlareSolverr."""

    total_resolutions: int = Field(default=0, ge=0)
    successful_resolutions: int = Field(default=0, ge=0)
    failed_resolutions: int = Field(default=0, ge=0)
    cached_resolutions: int = Field(default=0, ge=0)
    total_latency_ms: float = Field(default=0.0, ge=0.0)
    cache_hits: int = Field(default=0, ge=0)
    cache_misses: int = Field(default=0, ge=0)
    cache_size: int = Field(default=0, ge=0)
    service_status: ServiceStatus = Field(default=ServiceStatus.UNKNOWN)
    last_health_check_at: datetime | None = None
    last_resolution_at: datetime | None = None
    resolutions_by_type: dict[str, int] = Field(
        default_factory=dict,
        description="Nombre de résolutions par type de challenge.",
    )
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def success_rate(self) -> float:
        """Taux de succès (0.0 à 1.0)."""
        if self.total_resolutions == 0:
            return 0.0
        return self.successful_resolutions / self.total_resolutions

    @property
    def cache_hit_rate(self) -> float:
        """Taux de hit du cache (0.0 à 1.0)."""
        total = self.cache_hits + self.cache_misses
        if total == 0:
            return 0.0
        return self.cache_hits / total

    @property
    def average_latency_ms(self) -> float:
        """Latence moyenne en millisecondes."""
        if self.total_resolutions == 0:
            return 0.0
        return self.total_latency_ms / self.total_resolutions


# ============================================================================
# CLASSE INTERNE — CachedClearance
# ============================================================================


class _CachedClearance:
    """Entrée de cache pour les cookies de clearance.

    Non exposé publiquement — utilisé par FlareSolverrClient.
    """

    __slots__ = (
        "_domain",
        "_cookies",
        "_user_agent",
        "_created_at",
        "_expires_at",
        "_use_count",
    )

    def __init__(
        self,
        domain: str,
        cookies: list[ClearanceCookie],
        user_agent: str | None = None,
        ttl_seconds: float = 900.0,
    ) -> None:
        self._domain = domain
        self._cookies = cookies
        self._user_agent = user_agent
        self._created_at = datetime.now(UTC)
        self._expires_at = self._created_at + timedelta(seconds=ttl_seconds)
        self._use_count = 0

    @property
    def domain(self) -> str:
        return self._domain

    @property
    def cookies(self) -> list[ClearanceCookie]:
        return self._cookies

    @property
    def user_agent(self) -> str | None:
        return self._user_agent

    @property
    def is_expired(self) -> bool:
        """Indique si l'entrée de cache est expirée."""
        return datetime.now(UTC) >= self._expires_at

    @property
    def has_valid_cookies(self) -> bool:
        """Indique si tous les cookies sont encore valides."""
        return all(not c.is_expired for c in self._cookies)

    def record_use(self) -> None:
        """Enregistre une utilisation du cache."""
        self._use_count += 1

    @property
    def use_count(self) -> int:
        return self._use_count


# ============================================================================
# CLASSE PRINCIPALE — FlareSolverrClient
# ============================================================================


class FlareSolverrClient:
    """Client asynchrone pour le service FlareSolverr.

    Communique avec l'API REST de FlareSolverr pour résoudre les challenges
    Cloudflare et autres protections anti-bot. Gère le cache des cookies de
    clearance, les health checks périodiques, et l'intégration avec HttpSession.

    Lifecycle :
        >>> client = FlareSolverrClient(url="http://localhost:8191")
        >>> await client.start()
        >>> result = await client.resolve("https://protected-site.com")
        >>> await client.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Les opérations sont protégées par des locks granulaires.
    """

    # Endpoints de l'API FlareSolverr
    _ENDPOINT_HEALTH: ClassVar[str] = "/health"
    _ENDPOINT_V1: ClassVar[str] = "/v1"

    # Commandes API
    _CMD_REQUEST_GET: ClassVar[str] = "request.get"
    _CMD_REQUEST_POST: ClassVar[str] = "request.post"
    _CMD_BROWSER_CLOSE: ClassVar[str] = "browser.close"

    def __init__(
        self,
        *,
        config: FlareSolverrConfig | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        """Initialise le client FlareSolverr.

        Args:
            config: Configuration du client.
            event_bus: Bus d'événements pour monitoring.
        """
        self._config = config or FlareSolverrConfig()
        self._event_bus = event_bus

        # Client HTTP
        self._http_client: httpx.AsyncClient | None = None

        # Cache des clearances (domain → _CachedClearance)
        self._cache: OrderedDict[str, _CachedClearance] = OrderedDict()
        self._cache_lock = asyncio.Lock()

        # État du service
        self._service_status: ServiceStatus = ServiceStatus.UNKNOWN
        self._last_health_check: datetime | None = None

        # État
        self._started: bool = False
        self._start_time: float = 0.0

        # Tâche de health check
        self._health_check_task: asyncio.Task[None] | None = None

        # Statistiques
        self._total_resolutions: int = 0
        self._successful_resolutions: int = 0
        self._failed_resolutions: int = 0
        self._cached_resolutions: int = 0
        self._total_latency_ms: float = 0.0
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._resolutions_by_type: dict[str, int] = {}
        self._last_resolution_at: datetime | None = None
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="flaresolverr_client")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre le client et vérifie la disponibilité du service.

        Raises:
            FlareSolverrUnavailableError: Si le service n'est pas disponible.
        """
        if self._started:
            self._logger.warning("FlareSolverrClient déjà démarré, ignore")
            return

        # Créer le client HTTP
        self._http_client = httpx.AsyncClient(
            base_url=self._config.url,
            timeout=httpx.Timeout(self._config.health_check_timeout),
        )

        self._started = True
        self._start_time = time.monotonic()

        # Vérifier la disponibilité initiale
        await self._check_health()

        # Démarrer la tâche de health check périodique
        self._health_check_task = asyncio.create_task(
            self._health_check_loop(),
            name="flaresolverr_health_check",
        )

        self._logger.info(
            "FlareSolverrClient démarré: url={}, status={}",
            self._config.url,
            self._service_status.value,
        )

    async def stop(self) -> None:
        """Arrête le client et libère les ressources."""
        if not self._started:
            return

        self._started = False

        # Annuler la tâche de health check
        if self._health_check_task is not None:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
            self._health_check_task = None

        # Fermer le client HTTP
        if self._http_client is not None:
            try:
                await self._http_client.aclose()
            except Exception as e:
                self._logger.warning("Erreur lors de la fermeture du client HTTP: {}", e)
            self._http_client = None

        # Vider le cache
        async with self._cache_lock:
            self._cache.clear()

        self._logger.info("FlareSolverrClient arrêté")

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
        """Indique si le client est démarré."""
        return self._started

    @property
    def service_status(self) -> ServiceStatus:
        """État actuel du service FlareSolverr."""
        return self._service_status

    @property
    def is_available(self) -> bool:
        """Indique si le service est disponible."""
        return self._service_status.is_usable

    @property
    def cache_size(self) -> int:
        """Nombre d'entrées dans le cache."""
        return len(self._cache)

    # ------------------------------------------------------------------------
    # API publique — Résolution de challenges
    # ------------------------------------------------------------------------

    async def resolve(
        self,
        url: str,
        *,
        max_timeout: float | None = None,
        challenge_type: ChallengeType = ChallengeType.UNKNOWN,
        force_refresh: bool = False,
        session_id: str | None = None,
    ) -> ResolutionResult:
        """Résout un challenge pour une URL donnée.

        Args:
            url: URL protégée à résoudre.
            max_timeout: Timeout maximum (secondes, défaut: config.max_timeout).
            challenge_type: Type de challenge (détection auto si UNKNOWN).
            force_refresh: Ignorer le cache et forcer une nouvelle résolution.
            session_id: ID de session pour réutilisation (optionnel).

        Returns:
            Résultat de la résolution avec cookies et contenu HTML.

        Raises:
            FlareSolverrNotStartedError: Si le client n'est pas démarré.
            FlareSolverrUnavailableError: Si le service est indisponible.
            ChallengeResolutionError: Si la résolution échoue.
            TimeoutError: Si le timeout est dépassé.
        """
        self._ensure_started()

        if not self.is_available:
            raise FlareSolverrUnavailableError(
                self._config.url,
                "Service indisponible",
            )

        start_time = time.monotonic()
        timeout = min(
            max_timeout or self._config.timeout,
            self._config.max_timeout,
        )

        # Extraire le domaine pour le cache
        domain = self._extract_domain(url)

        # Vérifier le cache (si activé et non forcé)
        if self._config.cache_enabled and not force_refresh:
            cached = await self._get_cached_clearance(domain)
            if cached is not None:
                async with self._stats_lock:
                    self._cache_hits += 1
                    self._cached_resolutions += 1

                latency_ms = (time.monotonic() - start_time) * 1000.0
                return ResolutionResult(
                    url=url,
                    status=ResolutionStatus.CACHED,
                    challenge_type=challenge_type,
                    cookies=cached.cookies,
                    user_agent=cached.user_agent,
                    latency_ms=latency_ms,
                    from_cache=True,
                )

            async with self._stats_lock:
                self._cache_misses += 1

        # Résoudre via FlareSolverr
        result = await self._resolve_with_retry(
            url=url,
            timeout=timeout,
            challenge_type=challenge_type,
            session_id=session_id,
        )

        # Mettre en cache si succès
        if result.success and self._config.cache_enabled:
            await self._cache_clearance(
                domain=domain,
                cookies=result.cookies,
                user_agent=result.user_agent,
            )

        # Enregistrer les stats
        latency_ms = (time.monotonic() - start_time) * 1000.0
        await self._record_resolution(result, latency_ms)

        return result

    async def _resolve_with_retry(
        self,
        url: str,
        timeout: float,
        challenge_type: ChallengeType,
        session_id: str | None,
    ) -> ResolutionResult:
        """Résout un challenge avec retry automatique.

        Args:
            url: URL à résoudre.
            timeout: Timeout en secondes.
            challenge_type: Type de challenge.
            session_id: ID de session.

        Returns:
            Résultat de la résolution.
        """
        assert self._http_client is not None

        retries = 0
        last_error: Exception | None = None

        while retries <= self._config.max_retries:
            try:
                # Construire la payload
                payload = {
                    "cmd": self._CMD_REQUEST_GET,
                    "url": url,
                    "maxTimeout": int(timeout * 1000),  # FlareSolverr attend des ms
                }

                if session_id:
                    payload["session"] = session_id

                # Headers
                headers: dict[str, str] = {
                    "Content-Type": "application/json",
                }
                if self._config.api_key:
                    headers["Authorization"] = f"Bearer {self._config.api_key}"

                # Appel API
                response = await asyncio.wait_for(
                    self._http_client.post(
                        self._ENDPOINT_V1,
                        json=payload,
                        headers=headers,
                    ),
                    timeout=timeout + 10.0,  # Marge de sécurité
                )

                # Parser la réponse
                if response.status_code != 200:
                    raise ChallengeResolutionError(
                        url,
                        challenge_type.value,
                        f"HTTP {response.status_code}",
                    )

                data = response.json()

                # Vérifier le statut de la résolution
                if data.get("status") != "ok":
                    error_msg = data.get("message", "Erreur inconnue")
                    raise ChallengeResolutionError(
                        url,
                        challenge_type.value,
                        error_msg,
                    )

                # Extraire les résultats
                solution = data.get("solution", {})
                cookies_data = solution.get("cookies", [])
                html = solution.get("response", "")
                user_agent = solution.get("userAgent")

                # Convertir les cookies
                cookies = [
                    ClearanceCookie(
                        name=c["name"],
                        value=c["value"],
                        domain=c.get("domain", self._extract_domain(url)),
                        path=c.get("path", "/"),
                        expires=(
                            datetime.fromtimestamp(c["expires"], tz=UTC)
                            if c.get("expires")
                            else None
                        ),
                        secure=c.get("secure", False),
                        http_only=c.get("httpOnly", False),
                    )
                    for c in cookies_data
                ]

                return ResolutionResult(
                    url=url,
                    status=ResolutionStatus.SUCCESS,
                    challenge_type=challenge_type,
                    cookies=cookies,
                    html=html,
                    user_agent=user_agent,
                    latency_ms=0.0,  # Sera mis à jour par l'appelant
                    from_cache=False,
                )

            except asyncio.TimeoutError as e:
                last_error = e
                if retries < self._config.max_retries and self._config.retry_on_failure:
                    self._logger.warning(
                        "Timeout pour {}, retry {}/{}",
                        url,
                        retries + 1,
                        self._config.max_retries,
                    )
                    retries += 1
                    await asyncio.sleep(2.0)
                    continue
                raise TimeoutError(url, timeout) from e

            except Exception as e:
                last_error = e
                if retries < self._config.max_retries and self._config.retry_on_failure:
                    self._logger.warning(
                        "Erreur pour {}, retry {}/{}: {}",
                        url,
                        retries + 1,
                        self._config.max_retries,
                        e,
                    )
                    retries += 1
                    await asyncio.sleep(2.0)
                    continue
                raise ChallengeResolutionError(
                    url,
                    challenge_type.value,
                    str(e),
                ) from e

        # Ne devrait jamais arriver
        raise ChallengeResolutionError(
            url,
            challenge_type.value,
            f"Max retries atteint ({self._config.max_retries})",
        )

    # ------------------------------------------------------------------------
    # API publique — Health checks
    # ------------------------------------------------------------------------

    async def check_health(self) -> bool:
        """Vérifie la disponibilité du service FlareSolverr.

        Returns:
            True si le service est disponible.
        """
        return await self._check_health()

    async def _check_health(self) -> bool:
        """Effectue un health check sur le service.

        Returns:
            True si le service est sain.
        """
        assert self._http_client is not None

        try:
            response = await asyncio.wait_for(
                self._http_client.get(self._ENDPOINT_HEALTH),
                timeout=self._config.health_check_timeout,
            )

            if response.status_code == 200:
                self._service_status = ServiceStatus.AVAILABLE
                self._last_health_check = datetime.now(UTC)
                return True
            else:
                self._service_status = ServiceStatus.DEGRADED
                self._last_health_check = datetime.now(UTC)
                return False

        except Exception as e:
            self._logger.warning("Health check échoué: {}", e)
            self._service_status = ServiceStatus.UNAVAILABLE
            self._last_health_check = datetime.now(UTC)
            return False

    async def _health_check_loop(self) -> None:
        """Boucle de health checks périodiques."""
        try:
            while self._started:
                await asyncio.sleep(self._config.health_check_interval)
                await self._check_health()
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------------
    # API publique — Cache
    # ------------------------------------------------------------------------

    async def _get_cached_clearance(
        self,
        domain: str,
    ) -> _CachedClearance | None:
        """Récupère une clearance depuis le cache.

        Args:
            domain: Domaine à rechercher.

        Returns:
            Clearance cachée ou None si absente/expirée.
        """
        async with self._cache_lock:
            cached = self._cache.get(domain)
            if cached is None:
                return None

            # Vérifier l'expiration
            if cached.is_expired or not cached.has_valid_cookies:
                del self._cache[domain]
                return None

            # Déplacer en fin (LRU)
            self._cache.move_to_end(domain)
            cached.record_use()
            return cached

    async def _cache_clearance(
        self,
        domain: str,
        cookies: list[ClearanceCookie],
        user_agent: str | None = None,
    ) -> None:
        """Met en cache une clearance.

        Args:
            domain: Domaine.
            cookies: Cookies de clearance.
            user_agent: User-Agent utilisé.
        """
        async with self._cache_lock:
            # Vérifier la limite de taille
            if len(self._cache) >= self._config.max_cache_size:
                # Évincer le plus ancien
                self._cache.popitem(last=False)

            # Créer l'entrée
            entry = _CachedClearance(
                domain=domain,
                cookies=cookies,
                user_agent=user_agent,
                ttl_seconds=self._config.cache_ttl_seconds,
            )

            self._cache[domain] = entry

    async def clear_cache(self, domain: str | None = None) -> int:
        """Vide le cache des clearances.

        Args:
            domain: Domaine spécifique à vider (None = tout).

        Returns:
            Nombre d'entrées supprimées.
        """
        async with self._cache_lock:
            if domain is None:
                count = len(self._cache)
                self._cache.clear()
                return count
            else:
                if domain in self._cache:
                    del self._cache[domain]
                    return 1
                return 0

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> FlareSolverrStats:
        """Retourne les statistiques globales du client."""
        async with self._stats_lock:
            uptime = 0.0
            if self._start_time > 0:
                uptime = time.monotonic() - self._start_time

            async with self._cache_lock:
                cache_size = len(self._cache)

            return FlareSolverrStats(
                total_resolutions=self._total_resolutions,
                successful_resolutions=self._successful_resolutions,
                failed_resolutions=self._failed_resolutions,
                cached_resolutions=self._cached_resolutions,
                total_latency_ms=self._total_latency_ms,
                cache_hits=self._cache_hits,
                cache_misses=self._cache_misses,
                cache_size=cache_size,
                service_status=self._service_status,
                last_health_check_at=self._last_health_check,
                last_resolution_at=self._last_resolution_at,
                resolutions_by_type=dict(self._resolutions_by_type),
                uptime_seconds=uptime,
            )

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        async with self._stats_lock:
            self._total_resolutions = 0
            self._successful_resolutions = 0
            self._failed_resolutions = 0
            self._cached_resolutions = 0
            self._total_latency_ms = 0.0
            self._cache_hits = 0
            self._cache_misses = 0
            self._resolutions_by_type.clear()
            self._last_resolution_at = None

    # ------------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # ------------------------------------------------------------------------

    async def _record_resolution(
        self,
        result: ResolutionResult,
        latency_ms: float,
    ) -> None:
        """Enregistre une résolution dans les statistiques."""
        async with self._stats_lock:
            self._total_resolutions += 1
            self._total_latency_ms += latency_ms
            self._last_resolution_at = datetime.now(UTC)

            if result.success:
                self._successful_resolutions += 1
            else:
                self._failed_resolutions += 1

            # Par type
            type_key = result.challenge_type.value
            self._resolutions_by_type[type_key] = (
                self._resolutions_by_type.get(type_key, 0) + 1
            )

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extrait le domaine d'une URL.

        Args:
            url: URL complète.

        Returns:
            Domaine (ex: "example.com").
        """
        parsed = urlparse(url)
        return parsed.netloc or parsed.path

    def _ensure_started(self) -> None:
        """Vérifie que le client est démarré."""
        if not self._started:
            raise FlareSolverrNotStartedError()

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<FlareSolverrClient status={status} "
            f"url={self._config.url} "
            f"service={self._service_status.value} "
            f"cache={len(self._cache)}>"
        )


# ============================================================================
# HELPERS PUBLICS — Fonctions utilitaires
# ============================================================================


async def is_flaresolverr_available(
    url: str = "http://localhost:8191",
    *,
    timeout: float = 5.0,
) -> bool:
    """Vérifie rapidement si FlareSolverr est disponible.

    Args:
        url: URL du service.
        timeout: Timeout pour le check.

    Returns:
        True si le service est disponible.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{url}/health")
            return response.status_code == 200
    except Exception:
        return False


def get_flaresolverr_installation_instructions() -> str:
    """Retourne les instructions d'installation de FlareSolverr.

    Returns:
        Chaîne de texte avec les instructions.
    """
    return """
Pour utiliser FlareSolverr, vous devez installer et démarrer le service :

**Option 1 : Docker (recommandé)**
    docker run -d --name flaresolverr \\
      -p 8191:8191 \\
      -e LOG_LEVEL=info \\
      flaresolverr/flaresolverr:latest

**Option 2 : Installation manuelle**
    git clone https://github.com/FlareSolverr/FlareSolverr.git
    cd FlareSolverr
    pip install -r requirements.txt
    python flaresolverr.py

**Vérification**
    curl http://localhost:8191/health

**Configuration dans NexusDL**
    # Dans ~/.config/nexusdl/config.yaml
    cloudflare:
      bypass_mode: "flaresolverr"
      flaresolverr_url: "http://localhost:8191"

Après installation, redémarrez NexusDL pour détecter le service.
""".strip()


async def quick_resolve(
    url: str,
    *,
    flaresolverr_url: str = "http://localhost:8191",
    timeout: float = 60.0,
) -> ResolutionResult:
    """Résout rapidement un challenge (one-shot, sans cache).

    Args:
        url: URL à résoudre.
        flaresolverr_url: URL du service FlareSolverr.
        timeout: Timeout en secondes.

    Returns:
        Résultat de la résolution.
    """
    config = FlareSolverrConfig(
        url=flaresolverr_url,
        timeout=timeout,
        cache_enabled=False,
    )

    async with FlareSolverrClient(config=config) as client:
        return await client.resolve(url)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "FlareSolverrError",
    "FlareSolverrNotStartedError",
    "FlareSolverrUnavailableError",
    "ChallengeResolutionError",
    "TimeoutError",
    # Enums
    "ChallengeType",
    "ResolutionStatus",
    "ServiceStatus",
    # Modèles — Configuration
    "FlareSolverrConfig",
    # Modèles — Résultats
    "ClearanceCookie",
    "ResolutionResult",
    "FlareSolverrStats",
    # Classe principale
    "FlareSolverrClient",
    # Helpers
    "is_flaresolverr_available",
    "get_flaresolverr_installation_instructions",
    "quick_resolve",
]
