"""Session HTTP async centrale avec retry, streaming, et intégration complète.

Ce module fournit la session HTTP principale de NexusDL, utilisée par tous les
parsers et le downloader pour effectuer des requêtes HTTP. Elle orchestre
l'intégration de tous les composants du module `session/` :

    - UserAgentsManager : rotation automatique des User-Agents
    - ProxyManager : routing via proxies (si configuré)
    - RateLimiter : contrôle du débit par site (token bucket)
    - CookieManager : gestion des cookies persistants et chiffrés

Fonctionnalités principales :
    - API async complète (get, post, head, stream_download, etc.)
    - Retry automatique avec backoff exponentiel
    - Streaming pour téléchargements volumineux
    - Détection et gestion des erreurs HTTP (429, 5xx, etc.)
    - Headers personnalisés par site (Referer, Origin, etc.)
    - Pool de connexions (keep-alive)
    - Timeout configurable par requête
    - Statistiques détaillées (requêtes, erreurs, latence, débit)
    - Support des sessions par site (cookies isolés)
    - Intégration EventBus pour monitoring
    - Thread-safe (locks asyncio)

Architecture :
    HttpSession
        ├── httpx.AsyncClient (backend HTTP)
        ├── UserAgentsManager (rotation UA)
        ├── ProxyManager (routing proxy)
        ├── RateLimiter (contrôle débit)
        ├── CookieManager (cookies persistants)
        ├── RetryConfig (backoff exponentiel)
        └── SessionStats (statistiques)

Exemple d'utilisation :
    >>> session = HttpSession(
    ...     user_agents_manager=ua_manager,
    ...     proxy_manager=proxy_manager,
    ...     rate_limiter=rate_limiter,
    ... )
    >>> await session.start()
    >>>
    >>> # Requête simple
    >>> response = await session.get(
    ...     "https://mangadex.org/api/manga",
    ...     site_id="mangadex",
    ... )
    >>> print(response.status_code)
    200
    >>>
    >>> # Téléchargement streaming
    >>> async with session.stream_download(
    ...     "https://cdn.example.com/image.jpg",
    ...     dest=Path("/tmp/image.jpg"),
    ...     site_id="example",
    ... ) as stream:
    ...     print(f"Taille: {stream.total_bytes} bytes")
    >>>
    >>> # Statistiques
    >>> stats = await session.get_stats()
    >>> print(f"Requêtes: {stats.total_requests}")
    >>> print(f"Latence moyenne: {stats.average_latency_ms:.1f}ms")
    >>>
    >>> await session.stop()
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, ClassVar, Final, Self

import httpx
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError

if TYPE_CHECKING:
    from nexusdl.core.events import EventBus
    from nexusdl.core.session.cookie_manager import CookieManager
    from nexusdl.core.session.proxy_manager import ProxyManager
    from nexusdl.core.session.rate_limiter import RateLimiter
    from nexusdl.core.session.user_agents import UserAgentsManager


# ============================================================================
# EXCEPTIONS
# ============================================================================


class HttpSessionError(NexusDLError):
    """Exception de base pour les erreurs de session HTTP."""


class HttpSessionNotStartedError(HttpSessionError):
    """Exception levée lorsqu'on utilise la session avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "HttpSession must be started before use. Call await session.start()"
        )


class HttpRequestError(HttpSessionError):
    """Exception levée lorsqu'une requête HTTP échoue après tous les retries."""

    def __init__(
        self,
        url: str,
        status_code: int | None = None,
        reason: str = "",
    ) -> None:
        msg = f"Requête HTTP échouée pour {url}"
        if status_code is not None:
            msg += f" (HTTP {status_code})"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.url = url
        self.status_code = status_code
        self.reason = reason


class HttpTimeoutError(HttpSessionError):
    """Exception levée lorsqu'une requête dépasse le timeout."""

    def __init__(self, url: str, timeout_seconds: float) -> None:
        super().__init__(
            f"Timeout dépassé pour {url} après {timeout_seconds:.1f}s"
        )
        self.url = url
        self.timeout_seconds = timeout_seconds


class HttpRateLimitError(HttpSessionError):
    """Exception levée lorsque le rate limit est dépassé (429)."""

    def __init__(
        self,
        url: str,
        retry_after: float | None = None,
    ) -> None:
        msg = f"Rate limit dépassé pour {url}"
        if retry_after is not None:
            msg += f" (retry_after={retry_after:.1f}s)"
        super().__init__(msg)
        self.url = url
        self.retry_after = retry_after


# ============================================================================
# ENUMS
# ============================================================================


class HttpMethod(str, Enum):
    """Méthodes HTTP supportées."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


class RequestOutcome(str, Enum):
    """Résultat d'une requête HTTP."""

    SUCCESS = "success"
    RETRY = "retry"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    HTTP_ERROR = "http_error"
    CANCELLED = "cancelled"


# ============================================================================
# MODÈLES PYDANTIC — Configuration
# ============================================================================


class RetryConfig(BaseModel):
    """Configuration du retry automatique.

    Attributes:
        max_retries: Nombre maximum de tentatives.
        base_delay: Délai de base pour le backoff (secondes).
        max_delay: Délai maximum pour le backoff (secondes).
        retry_on_status: Codes HTTP pour lesquels retryer.
        retry_on_exceptions: Types d'exceptions pour lesquels retryer.
        exponential_backoff: Utiliser un backoff exponentiel.
        jitter: Ajouter du jitter aléatoire (0.0 à 1.0).
    """

    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Nombre maximum de tentatives.",
    )
    base_delay: float = Field(
        default=1.0,
        ge=0.0,
        le=60.0,
        description="Délai de base pour le backoff (secondes).",
    )
    max_delay: float = Field(
        default=30.0,
        ge=0.0,
        le=300.0,
        description="Délai maximum pour le backoff (secondes).",
    )
    retry_on_status: list[int] = Field(
        default_factory=lambda: [429, 500, 502, 503, 504],
        description="Codes HTTP pour lesquels retryer.",
    )
    retry_on_exceptions: bool = Field(
        default=True,
        description="Retryer sur exceptions réseau (timeout, connection error).",
    )
    exponential_backoff: bool = Field(
        default=True,
        description="Utiliser un backoff exponentiel.",
    )
    jitter: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Jitter aléatoire (0.0 à 1.0).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class HttpSessionConfig(BaseModel):
    """Configuration globale de la session HTTP.

    Attributes:
        timeout: Timeout par défaut pour les requêtes (secondes).
        connect_timeout: Timeout de connexion (secondes).
        read_timeout: Timeout de lecture (secondes).
        write_timeout: Timeout d'écriture (secondes).
        pool_timeout: Timeout pour obtenir une connexion du pool (secondes).
        max_connections: Nombre maximum de connexions simultanées.
        max_keepalive_connections: Nombre maximum de connexions keep-alive.
        follow_redirects: Suivre les redirections HTTP.
        verify_ssl: Vérifier les certificats SSL.
        http2: Activer HTTP/2 si supporté.
        default_headers: Headers HTTP par défaut.
        user_agent_rotation: Activer la rotation des User-Agents.
        proxy_enabled: Activer l'utilisation de proxies.
        rate_limiting_enabled: Activer le rate limiting.
        cookies_enabled: Activer la gestion des cookies.
        retry_config: Configuration du retry.
    """

    timeout: float = Field(
        default=30.0,
        gt=0.0,
        le=600.0,
        description="Timeout par défaut pour les requêtes (secondes).",
    )
    connect_timeout: float = Field(
        default=10.0,
        gt=0.0,
        le=60.0,
        description="Timeout de connexion (secondes).",
    )
    read_timeout: float = Field(
        default=60.0,
        gt=0.0,
        le=600.0,
        description="Timeout de lecture (secondes).",
    )
    write_timeout: float = Field(
        default=30.0,
        gt=0.0,
        le=300.0,
        description="Timeout d'écriture (secondes).",
    )
    pool_timeout: float = Field(
        default=10.0,
        gt=0.0,
        le=60.0,
        description="Timeout pour obtenir une connexion du pool (secondes).",
    )
    max_connections: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Nombre maximum de connexions simultanées.",
    )
    max_keepalive_connections: int = Field(
        default=20,
        ge=0,
        le=100,
        description="Nombre maximum de connexions keep-alive.",
    )
    follow_redirects: bool = Field(
        default=True,
        description="Suivre les redirections HTTP.",
    )
    verify_ssl: bool = Field(
        default=True,
        description="Vérifier les certificats SSL.",
    )
    http2: bool = Field(
        default=False,
        description="Activer HTTP/2 si supporté.",
    )
    default_headers: dict[str, str] = Field(
        default_factory=lambda: {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        },
        description="Headers HTTP par défaut.",
    )
    user_agent_rotation: bool = Field(
        default=True,
        description="Activer la rotation des User-Agents.",
    )
    proxy_enabled: bool = Field(
        default=False,
        description="Activer l'utilisation de proxies.",
    )
    rate_limiting_enabled: bool = Field(
        default=True,
        description="Activer le rate limiting.",
    )
    cookies_enabled: bool = Field(
        default=True,
        description="Activer la gestion des cookies.",
    )
    retry_config: RetryConfig = Field(
        default_factory=RetryConfig,
        description="Configuration du retry.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# MODÈLES PYDANTIC — Statistiques
# ============================================================================


class RequestStats(BaseModel):
    """Statistiques d'une requête individuelle."""

    url: str = Field(..., description="URL de la requête.")
    method: HttpMethod = Field(..., description="Méthode HTTP.")
    status_code: int | None = Field(default=None, description="Code HTTP de réponse.")
    outcome: RequestOutcome = Field(..., description="Résultat de la requête.")
    latency_ms: float = Field(..., ge=0.0, description="Latence en millisecondes.")
    bytes_sent: int = Field(default=0, ge=0, description="Bytes envoyés.")
    bytes_received: int = Field(default=0, ge=0, description="Bytes reçus.")
    retries_count: int = Field(default=0, ge=0, description="Nombre de retries.")
    site_id: str | None = Field(default=None, description="ID du site (si applicable).")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de la requête.",
    )
    error: str | None = Field(default=None, description="Message d'erreur si échec.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class SessionStats(BaseModel):
    """Statistiques globales de la session HTTP."""

    total_requests: int = Field(default=0, ge=0)
    successful_requests: int = Field(default=0, ge=0)
    failed_requests: int = Field(default=0, ge=0)
    total_retries: int = Field(default=0, ge=0)
    total_bytes_sent: int = Field(default=0, ge=0)
    total_bytes_received: int = Field(default=0, ge=0)
    total_latency_ms: float = Field(default=0.0, ge=0.0)
    requests_by_site: dict[str, int] = Field(
        default_factory=dict,
        description="Nombre de requêtes par site.",
    )
    requests_by_status: dict[int, int] = Field(
        default_factory=dict,
        description="Nombre de requêtes par code HTTP.",
    )
    requests_by_outcome: dict[str, int] = Field(
        default_factory=dict,
        description="Nombre de requêtes par résultat.",
    )
    last_request_at: datetime | None = None
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def success_rate(self) -> float:
        """Taux de succès (0.0 à 1.0)."""
        if self.total_requests == 0:
            return 0.0
        return self.successful_requests / self.total_requests

    @property
    def average_latency_ms(self) -> float:
        """Latence moyenne en millisecondes."""
        if self.total_requests == 0:
            return 0.0
        return self.total_latency_ms / self.total_requests

    @property
    def retry_rate(self) -> float:
        """Taux de retry (0.0 à 1.0)."""
        if self.total_requests == 0:
            return 0.0
        return self.total_retries / self.total_requests


# ============================================================================
# CLASSE — StreamDownloadContext
# ============================================================================


class StreamDownloadContext:
    """Context manager pour le téléchargement streaming.

    Gère l'ouverture et la fermeture automatique du stream, ainsi que
    l'écriture progressive dans le fichier de destination.
    """

    __slots__ = (
        "_session",
        "_url",
        "_dest",
        "_site_id",
        "_chunk_size",
        "_response",
        "_file",
        "_total_bytes",
        "_start_time",
    )

    def __init__(
        self,
        session: HttpSession,
        url: str,
        dest: Path,
        *,
        site_id: str | None = None,
        chunk_size: int = 65536,
    ) -> None:
        self._session = session
        self._url = url
        self._dest = dest
        self._site_id = site_id
        self._chunk_size = chunk_size
        self._response: httpx.Response | None = None
        self._file: Any = None
        self._total_bytes: int = 0
        self._start_time: float = 0.0

    @property
    def total_bytes(self) -> int:
        """Nombre total de bytes téléchargés."""
        return self._total_bytes

    @property
    def elapsed_seconds(self) -> float:
        """Temps écoulé depuis le début du téléchargement."""
        if self._start_time == 0:
            return 0.0
        return time.monotonic() - self._start_time

    @property
    def download_speed_bytes_per_sec(self) -> float:
        """Vitesse de téléchargement en bytes/seconde."""
        elapsed = self.elapsed_seconds
        if elapsed <= 0:
            return 0.0
        return self._total_bytes / elapsed

    async def __aenter__(self) -> Self:
        """Ouvre le stream et le fichier de destination."""
        self._start_time = time.monotonic()

        # Créer le répertoire parent si nécessaire
        self._dest.parent.mkdir(parents=True, exist_ok=True)

        # Ouvrir le stream HTTP
        self._response = await self._session._client.stream(
            "GET",
            self._url,
            headers=self._session._build_headers(site_id=self._site_id),
        )
        await self._response.__aenter__()

        # Vérifier le status code
        if self._response.status_code >= 400:
            await self._response.__aexit__(None, None, None)
            raise HttpRequestError(
                self._url,
                status_code=self._response.status_code,
                reason=f"HTTP {self._response.status_code}",
            )

        # Ouvrir le fichier de destination
        self._file = await asyncio.to_thread(self._dest.open, "wb")

        return self

    async def __aexit__(self, *args: object) -> None:
        """Ferme le stream et le fichier."""
        if self._file is not None:
            await asyncio.to_thread(self._file.close)

        if self._response is not None:
            await self._response.__aexit__(*args)

    async def download(self) -> int:
        """Télécharge le contenu complet dans le fichier.

        Returns:
            Nombre total de bytes téléchargés.
        """
        if self._response is None or self._file is None:
            raise HttpSessionError("Stream non initialisé")

        async for chunk in self._response.aiter_bytes(chunk_size=self._chunk_size):
            await asyncio.to_thread(self._file.write, chunk)
            self._total_bytes += len(chunk)

        return self._total_bytes


# ============================================================================
# CLASSE PRINCIPALE — HttpSession
# ============================================================================


class HttpSession:
    """Session HTTP async centrale avec retry, streaming, et intégration complète.

    Orchestre tous les composants du module `session/` pour effectuer des
    requêtes HTTP robustes avec rotation des User-Agents, routing via proxies,
    contrôle du débit, et gestion des cookies.

    Lifecycle :
        >>> session = HttpSession(
        ...     user_agents_manager=ua_manager,
        ...     proxy_manager=proxy_manager,
        ...     rate_limiter=rate_limiter,
        ... )
        >>> await session.start()
        >>> response = await session.get("https://example.com")
        >>> await session.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Les opérations sont protégées par des locks granulaires.
    """

    # Constantes
    _DEFAULT_CHUNK_SIZE: Final[int] = 65536  # 64 KB

    def __init__(
        self,
        *,
        config: HttpSessionConfig | None = None,
        user_agents_manager: UserAgentsManager | None = None,
        proxy_manager: ProxyManager | None = None,
        rate_limiter: RateLimiter | None = None,
        cookie_manager: CookieManager | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        """Initialise la session HTTP.

        Args:
            config: Configuration de la session.
            user_agents_manager: Manager de rotation des User-Agents.
            proxy_manager: Manager de proxies.
            rate_limiter: Limiteur de débit.
            cookie_manager: Manager de cookies.
            event_bus: Bus d'événements pour monitoring.
        """
        self._config = config or HttpSessionConfig()
        self._user_agents_manager = user_agents_manager
        self._proxy_manager = proxy_manager
        self._rate_limiter = rate_limiter
        self._cookie_manager = cookie_manager
        self._event_bus = event_bus

        # Client httpx (initialisé dans start())
        self._client: httpx.AsyncClient | None = None

        # État
        self._started: bool = False
        self._start_time: float = 0.0

        # Headers par site (cache)
        self._site_headers: dict[str, dict[str, str]] = {}
        self._headers_lock = asyncio.Lock()

        # Statistiques
        self._total_requests: int = 0
        self._successful_requests: int = 0
        self._failed_requests: int = 0
        self._total_retries: int = 0
        self._total_bytes_sent: int = 0
        self._total_bytes_received: int = 0
        self._total_latency_ms: float = 0.0
        self._requests_by_site: dict[str, int] = defaultdict(int)
        self._requests_by_status: dict[int, int] = defaultdict(int)
        self._requests_by_outcome: dict[str, int] = defaultdict(int)
        self._last_request_at: datetime | None = None
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="http_session")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre la session HTTP et initialise le client httpx.

        Raises:
            HttpSessionError: Si l'initialisation échoue.
        """
        if self._started:
            self._logger.warning("HttpSession déjà démarrée, ignore")
            return

        try:
            # Construire le timeout httpx
            timeout = httpx.Timeout(
                connect=self._config.connect_timeout,
                read=self._config.read_timeout,
                write=self._config.write_timeout,
                pool=self._config.pool_timeout,
            )

            # Construire les limits
            limits = httpx.Limits(
                max_connections=self._config.max_connections,
                max_keepalive_connections=self._config.max_keepalive_connections,
            )

            # Créer le client httpx
            self._client = httpx.AsyncClient(
                timeout=timeout,
                limits=limits,
                follow_redirects=self._config.follow_redirects,
                verify=self._config.verify_ssl,
                http2=self._config.http2,
                headers=self._config.default_headers,
            )

            self._started = True
            self._start_time = time.monotonic()

            self._logger.info(
                "HttpSession démarrée: timeout={}s, max_connections={}",
                self._config.timeout,
                self._config.max_connections,
            )

        except Exception as e:
            self._logger.error("Échec du démarrage de HttpSession: {}", e)
            raise HttpSessionError(f"Impossible de démarrer la session: {e}") from e

    async def stop(self) -> None:
        """Arrête la session HTTP et libère les ressources."""
        if not self._started:
            return

        self._started = False

        # Fermer le client httpx
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as e:
                self._logger.warning("Erreur lors de la fermeture du client httpx: {}", e)
            self._client = None

        self._logger.info("HttpSession arrêtée")

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
        """Indique si la session est démarrée."""
        return self._started

    @property
    def client(self) -> httpx.AsyncClient | None:
        """Instance du client httpx (ou None)."""
        return self._client

    # ------------------------------------------------------------------------
    # API publique — Requêtes HTTP
    # ------------------------------------------------------------------------

    async def get(
        self,
        url: str,
        *,
        site_id: str | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Effectue une requête GET avec retry automatique.

        Args:
            url: URL de la requête.
            site_id: ID du site (pour rate limiting et headers).
            headers: Headers HTTP additionnels.
            params: Paramètres de query string.
            timeout: Timeout pour cette requête (override du défaut).
            **kwargs: Arguments additionnels pour httpx.

        Returns:
            Réponse HTTP.

        Raises:
            HttpSessionNotStartedError: Si la session n'est pas démarrée.
            HttpRequestError: Si la requête échoue après tous les retries.
            HttpTimeoutError: Si le timeout est dépassé.
            HttpRateLimitError: Si le rate limit est dépassé (429).
        """
        return await self._request_with_retry(
            method=HttpMethod.GET,
            url=url,
            site_id=site_id,
            headers=headers,
            params=params,
            timeout=timeout,
            **kwargs,
        )

    async def post(
        self,
        url: str,
        *,
        site_id: str | None = None,
        headers: dict[str, str] | None = None,
        data: Any = None,
        json: Any = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Effectue une requête POST avec retry automatique.

        Args:
            url: URL de la requête.
            site_id: ID du site.
            headers: Headers HTTP additionnels.
            data: Données de formulaire.
            json: Données JSON.
            timeout: Timeout pour cette requête.
            **kwargs: Arguments additionnels pour httpx.

        Returns:
            Réponse HTTP.
        """
        return await self._request_with_retry(
            method=HttpMethod.POST,
            url=url,
            site_id=site_id,
            headers=headers,
            data=data,
            json=json,
            timeout=timeout,
            **kwargs,
        )

    async def head(
        self,
        url: str,
        *,
        site_id: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Effectue une requête HEAD avec retry automatique.

        Args:
            url: URL de la requête.
            site_id: ID du site.
            headers: Headers HTTP additionnels.
            timeout: Timeout pour cette requête.
            **kwargs: Arguments additionnels pour httpx.

        Returns:
            Réponse HTTP.
        """
        return await self._request_with_retry(
            method=HttpMethod.HEAD,
            url=url,
            site_id=site_id,
            headers=headers,
            timeout=timeout,
            **kwargs,
        )

    # ------------------------------------------------------------------------
    # API publique — Streaming
    # ------------------------------------------------------------------------

    def stream_download(
        self,
        url: str,
        dest: Path,
        *,
        site_id: str | None = None,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
    ) -> StreamDownloadContext:
        """Télécharge un fichier en streaming.

        Args:
            url: URL du fichier à télécharger.
            dest: Chemin du fichier de destination.
            site_id: ID du site (pour rate limiting).
            chunk_size: Taille des chunks (défaut: 64 KB).

        Returns:
            Context manager pour le streaming.

        Example:
            >>> async with session.stream_download(url, dest) as stream:
            ...     await stream.download()
            ...     print(f"Téléchargé: {stream.total_bytes} bytes")
        """
        self._ensure_started()
        return StreamDownloadContext(
            session=self,
            url=url,
            dest=dest,
            site_id=site_id,
            chunk_size=chunk_size,
        )

    # ------------------------------------------------------------------------
    # API publique — Configuration par site
    # ------------------------------------------------------------------------

    async def set_site_headers(
        self,
        site_id: str,
        headers: dict[str, str],
    ) -> None:
        """Définit les headers par défaut pour un site spécifique.

        Args:
            site_id: ID du site.
            headers: Headers à ajouter pour ce site.
        """
        async with self._headers_lock:
            self._site_headers[site_id] = headers

    async def get_site_headers(self, site_id: str) -> dict[str, str]:
        """Récupère les headers par défaut pour un site.

        Args:
            site_id: ID du site.

        Returns:
            Dictionnaire de headers.
        """
        async with self._headers_lock:
            return dict(self._site_headers.get(site_id, {}))

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> SessionStats:
        """Retourne les statistiques globales de la session."""
        async with self._stats_lock:
            uptime = 0.0
            if self._start_time > 0:
                uptime = time.monotonic() - self._start_time

            return SessionStats(
                total_requests=self._total_requests,
                successful_requests=self._successful_requests,
                failed_requests=self._failed_requests,
                total_retries=self._total_retries,
                total_bytes_sent=self._total_bytes_sent,
                total_bytes_received=self._total_bytes_received,
                total_latency_ms=self._total_latency_ms,
                requests_by_site=dict(self._requests_by_site),
                requests_by_status=dict(self._requests_by_status),
                requests_by_outcome=dict(self._requests_by_outcome),
                last_request_at=self._last_request_at,
                uptime_seconds=uptime,
            )

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        async with self._stats_lock:
            self._total_requests = 0
            self._successful_requests = 0
            self._failed_requests = 0
            self._total_retries = 0
            self._total_bytes_sent = 0
            self._total_bytes_received = 0
            self._total_latency_ms = 0.0
            self._requests_by_site.clear()
            self._requests_by_status.clear()
            self._requests_by_outcome.clear()
            self._last_request_at = None

    # ------------------------------------------------------------------------
    # Méthodes internes — Requêtes avec retry
    # ------------------------------------------------------------------------

    async def _request_with_retry(
        self,
        method: HttpMethod,
        url: str,
        *,
        site_id: str | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        data: Any = None,
        json: Any = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Effectue une requête HTTP avec retry automatique.

        Args:
            method: Méthode HTTP.
            url: URL de la requête.
            site_id: ID du site.
            headers: Headers additionnels.
            params: Paramètres de query string.
            data: Données de formulaire.
            json: Données JSON.
            timeout: Timeout pour cette requête.
            **kwargs: Arguments additionnels pour httpx.

        Returns:
            Réponse HTTP.

        Raises:
            HttpRequestError: Si la requête échoue après tous les retries.
        """
        self._ensure_started()
        assert self._client is not None

        start_time = time.monotonic()
        retries = 0
        last_exception: Exception | None = None

        # Construire les headers
        request_headers = self._build_headers(site_id=site_id, extra=headers)

        # Obtenir le proxy si activé
        proxy_url = None
        if self._config.proxy_enabled and self._proxy_manager is not None and site_id:
            try:
                proxy_url = await self._proxy_manager.get_proxy_for_url(url)
            except Exception as e:
                self._logger.warning("Erreur lors de l'obtention du proxy: {}", e)

        # Rate limiting
        if self._config.rate_limiting_enabled and self._rate_limiter is not None and site_id:
            try:
                await self._rate_limiter.acquire(site_id)
            except Exception as e:
                self._logger.warning("Erreur lors de l'acquisition du rate limit: {}", e)

        # Boucle de retry
        while retries <= self._config.retry_config.max_retries:
            try:
                # Effectuer la requête
                response = await asyncio.wait_for(
                    self._client.request(
                        method=method.value,
                        url=url,
                        headers=request_headers,
                        params=params,
                        data=data,
                        json=json,
                        timeout=timeout or self._config.timeout,
                        **kwargs,
                    ),
                    timeout=timeout or self._config.timeout,
                )

                # Vérifier le status code
                if response.status_code in self._config.retry_config.retry_on_status:
                    # Retry sur certains codes HTTP
                    if retries < self._config.retry_config.max_retries:
                        delay = self._compute_retry_delay(retries)
                        self._logger.debug(
                            "Retry {}/{} pour {} (HTTP {}), attente {:.1f}s",
                            retries + 1,
                            self._config.retry_config.max_retries,
                            url,
                            response.status_code,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        retries += 1
                        async with self._stats_lock:
                            self._total_retries += 1

                        # Signaler au rate limiter si 429
                        if response.status_code == 429 and self._rate_limiter is not None and site_id:
                            retry_after = self._parse_retry_after(response.headers.get("Retry-After"))
                            await self._rate_limiter.report_rate_limit(site_id, retry_after=retry_after)

                        continue
                    else:
                        # Max retries atteint
                        raise HttpRequestError(
                            url,
                            status_code=response.status_code,
                            reason=f"Max retries atteint ({self._config.retry_config.max_retries})",
                        )

                # Succès
                latency_ms = (time.monotonic() - start_time) * 1000.0
                await self._record_request(
                    url=url,
                    method=method,
                    status_code=response.status_code,
                    outcome=RequestOutcome.SUCCESS,
                    latency_ms=latency_ms,
                    bytes_sent=0,  # httpx ne expose pas facilement
                    bytes_received=len(response.content),
                    retries_count=retries,
                    site_id=site_id,
                )

                # Signaler succès au proxy manager
                if proxy_url and self._proxy_manager is not None:
                    await self._proxy_manager.report_success(proxy_url, latency_ms=latency_ms)

                return response

            except asyncio.TimeoutError as e:
                last_exception = e
                if retries < self._config.retry_config.max_retries:
                    delay = self._compute_retry_delay(retries)
                    self._logger.debug(
                        "Timeout pour {}, retry {}/{} après {:.1f}s",
                        url,
                        retries + 1,
                        self._config.retry_config.max_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    retries += 1
                    async with self._stats_lock:
                        self._total_retries += 1
                    continue
                else:
                    latency_ms = (time.monotonic() - start_time) * 1000.0
                    await self._record_request(
                        url=url,
                        method=method,
                        status_code=None,
                        outcome=RequestOutcome.TIMEOUT,
                        latency_ms=latency_ms,
                        retries_count=retries,
                        site_id=site_id,
                        error=str(e),
                    )
                    raise HttpTimeoutError(url, timeout or self._config.timeout) from e

            except httpx.HTTPError as e:
                last_exception = e
                if self._config.retry_config.retry_on_exceptions and retries < self._config.retry_config.max_retries:
                    delay = self._compute_retry_delay(retries)
                    self._logger.debug(
                        "Erreur réseau pour {}, retry {}/{} après {:.1f}s: {}",
                        url,
                        retries + 1,
                        self._config.retry_config.max_retries,
                        delay,
                        e,
                    )
                    await asyncio.sleep(delay)
                    retries += 1
                    async with self._stats_lock:
                        self._total_retries += 1
                    continue
                else:
                    latency_ms = (time.monotonic() - start_time) * 1000.0
                    await self._record_request(
                        url=url,
                        method=method,
                        status_code=None,
                        outcome=RequestOutcome.NETWORK_ERROR,
                        latency_ms=latency_ms,
                        retries_count=retries,
                        site_id=site_id,
                        error=str(e),
                    )
                    raise HttpRequestError(url, reason=str(e)) from e

            except Exception as e:
                last_exception = e
                latency_ms = (time.monotonic() - start_time) * 1000.0
                await self._record_request(
                    url=url,
                    method=method,
                    status_code=None,
                    outcome=RequestOutcome.HTTP_ERROR,
                    latency_ms=latency_ms,
                    retries_count=retries,
                    site_id=site_id,
                    error=str(e),
                )
                raise HttpRequestError(url, reason=str(e)) from e

        # Ne devrait jamais arriver
        raise HttpRequestError(
            url,
            reason=f"Max retries atteint ({self._config.retry_config.max_retries})",
        )

    def _build_headers(
        self,
        *,
        site_id: str | None = None,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Construit les headers pour une requête.

        Combine les headers par défaut, les headers par site, et les headers
        additionnels. Ajoute également le User-Agent rotatif si activé.

        Args:
            site_id: ID du site (pour headers spécifiques).
            extra: Headers additionnels.

        Returns:
            Dictionnaire de headers complet.
        """
        headers: dict[str, str] = {}

        # 1. Headers par défaut de la config
        headers.update(self._config.default_headers)

        # 2. Headers par site (si disponibles)
        if site_id and site_id in self._site_headers:
            headers.update(self._site_headers[site_id])

        # 3. User-Agent rotatif (si activé)
        if self._config.user_agent_rotation and self._user_agents_manager is not None:
            try:
                ua = self._user_agents_manager.get_random()
                headers["User-Agent"] = ua
            except Exception as e:
                self._logger.trace("Erreur lors de la rotation de l'UA: {}", e)

        # 4. Headers additionnels (priorité maximale)
        if extra:
            headers.update(extra)

        return headers

    def _compute_retry_delay(self, attempt: int) -> float:
        """Calcule le délai avant le prochain retry.

        Args:
            attempt: Numéro de la tentative (0-based).

        Returns:
            Délai en secondes.
        """
        config = self._config.retry_config

        if config.exponential_backoff:
            # Backoff exponentiel : base_delay * 2^attempt
            delay = config.base_delay * (2 ** attempt)
        else:
            # Backoff fixe
            delay = config.base_delay

        # Plafonner au max_delay
        delay = min(delay, config.max_delay)

        # Ajouter du jitter
        if config.jitter > 0:
            jitter_range = delay * config.jitter
            delay += random.uniform(-jitter_range, jitter_range)

        return max(0.0, delay)

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        """Parse le header Retry-After.

        Args:
            value: Valeur du header (secondes ou date HTTP).

        Returns:
            Nombre de secondes à attendre, ou None.
        """
        if value is None:
            return None

        try:
            return float(value)
        except ValueError:
            # Essayer de parser comme date HTTP
            try:
                from email.utils import parsedate_to_datetime
                target = parsedate_to_datetime(value)
                now = datetime.now(UTC)
                delta = (target - now).total_seconds()
                return max(0.0, delta)
            except Exception:
                return None

    # ------------------------------------------------------------------------
    # Méthodes internes — Statistiques
    # ------------------------------------------------------------------------

    async def _record_request(
        self,
        *,
        url: str,
        method: HttpMethod,
        status_code: int | None,
        outcome: RequestOutcome,
        latency_ms: float,
        bytes_sent: int = 0,
        bytes_received: int = 0,
        retries_count: int = 0,
        site_id: str | None = None,
        error: str | None = None,
    ) -> None:
        """Enregistre une requête dans les statistiques."""
        async with self._stats_lock:
            self._total_requests += 1
            self._total_latency_ms += latency_ms
            self._total_bytes_sent += bytes_sent
            self._total_bytes_received += bytes_received
            self._last_request_at = datetime.now(UTC)

            if outcome == RequestOutcome.SUCCESS:
                self._successful_requests += 1
            else:
                self._failed_requests += 1

            if site_id:
                self._requests_by_site[site_id] += 1

            if status_code is not None:
                self._requests_by_status[status_code] += 1

            self._requests_by_outcome[outcome.value] += 1

        # Émettre un événement si EventBus disponible
        if self._event_bus is not None:
            try:
                await self._event_bus.emit(
                    "http.request.completed",
                    {
                        "url": url,
                        "method": method.value,
                        "status_code": status_code,
                        "outcome": outcome.value,
                        "latency_ms": latency_ms,
                        "site_id": site_id,
                    },
                )
            except Exception as e:
                self._logger.trace("Erreur lors de l'émission de l'événement: {}", e)

    # ------------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # ------------------------------------------------------------------------

    def _ensure_started(self) -> None:
        """Vérifie que la session est démarrée."""
        if not self._started:
            raise HttpSessionNotStartedError()

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<HttpSession status={status} "
            f"requests={self._total_requests} "
            f"success_rate={self._successful_requests / max(1, self._total_requests):.1%}>"
        )


# ============================================================================
# HELPERS PUBLICS — Fonctions utilitaires
# ============================================================================


async def quick_get(
    url: str,
    *,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Effectue une requête GET rapide (one-shot, sans session).

    Fonction utilitaire pour des requêtes ponctuelles sans gestion
    de lifecycle.

    Args:
        url: URL de la requête.
        timeout: Timeout en secondes.
        headers: Headers HTTP additionnels.

    Returns:
        Réponse HTTP.
    """
    async with HttpSession() as session:
        return await session.get(url, timeout=timeout, headers=headers)


async def download_file(
    url: str,
    dest: Path,
    *,
    timeout: float = 300.0,
    chunk_size: int = 65536,
) -> int:
    """Télécharge un fichier (one-shot, sans session).

    Args:
        url: URL du fichier à télécharger.
        dest: Chemin du fichier de destination.
        timeout: Timeout en secondes.
        chunk_size: Taille des chunks.

    Returns:
        Nombre de bytes téléchargés.
    """
    async with HttpSession() as session:
        async with session.stream_download(url, dest, chunk_size=chunk_size) as stream:
            return await stream.download()


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "HttpSessionError",
    "HttpSessionNotStartedError",
    "HttpRequestError",
    "HttpTimeoutError",
    "HttpRateLimitError",
    # Enums
    "HttpMethod",
    "RequestOutcome",
    # Modèles — Configuration
    "RetryConfig",
    "HttpSessionConfig",
    # Modèles — Statistiques
    "RequestStats",
    "SessionStats",
    # Classes
    "StreamDownloadContext",
    "HttpSession",
    # Helpers
    "quick_get",
    "download_file",
]
