"""Module public de gestion des sessions HTTP et réseau NexusDL.

Ce module constitue le point d'entrée de la couche réseau dans l'architecture
hexagonale. Il expose l'API publique stable utilisée par les parsers, le
DownloadWorker, et les interfaces pour effectuer des requêtes HTTP robustes,
gérer les cookies, contourner les protections anti-bot, et contrôler le débit.

Pipeline de requête HTTP complet :
    Interface / Parser / Downloader
        │
        ▼ session.get(url, site_id="mangadex")
    HttpSession
        │
        ├──► UserAgentsManager.get_random()              → User-Agent rotatif
        ├──► ProxyManager.get_proxy_for_url(url)         → Proxy (optionnel)
        ├──► RateLimiter.acquire(site_id)                → Token bucket (bloquant)
        ├──► CookieManager.to_httpx_cookies(domain)      → Cookies persistants
        │
        ▼ httpx.AsyncClient.request()
        │
        ├──► Si 429 : RateLimiter.report_rate_limit()    → Backoff automatique
        ├──► Si 403/503 (CF) : FlareSolverrClient.resolve() → Cookies clearance
        ├──► Réponse : CookieManager.update_from_response() → Mise à jour cookies
        └──► ProxyManager.report_success/failure()       → Health tracking

Composants exposés :
    - HttpSession        : Session HTTP async centrale (retry, streaming, stats)
    - CookieManager      : Cookies persistants chiffrés (Fernet, Netscape)
    - PlaywrightPool     : Pool de navigateurs headless (4 stratégies fingerprint)
    - RateLimiter        : Limiteur de débit (token bucket, backoff 429)
    - UserAgentsManager  : Rotation des User-Agents (12 catégories, mode sticky)
    - ProxyManager       : Gestionnaire de proxies (5 stratégies, health checks)
    - FlareSolverrClient : Résolution challenges Cloudflare/DataDome (cache)

Règles d'or :
    1. Ce fichier n'expose QUE les symboles publics stables.
    2. Il ne dépend d'aucun module de `interfaces/` ni de `parsers/`.
    3. Tous les exports sont typés strictement et documentés.
    4. Les imports sont organisés par sous-module pour la lisibilité.
    5. Les conflits de noms sont résolus par des aliases explicites.
    6. Tous les composants sont async-first (asyncio).
    7. Le chiffrement des cookies est OBLIGATOIRE par défaut (Fernet).
    8. Le rate limiting est TOUJOURS actif pour respecter les sites.
    9. Les User-Agents sont TOUJOURS rotés pour éviter le fingerprinting.
    10. Les statistiques sont exposées pour monitoring de tous les composants.

Exemple d'utilisation — Initialisation complète :
    >>> from pathlib import Path
    >>> from nexusdl.core.session import (
    ...     HttpSession,
    ...     HttpSessionConfig,
    ...     CookieManager,
    ...     PlaywrightPool,
    ...     RateLimiter,
    ...     UserAgentsManager,
    ...     ProxyManager,
    ...     FlareSolverrClient,
    ... )
    >>>
    >>> # 1. Initialiser les composants
    >>> ua_manager = UserAgentsManager()
    >>> proxy_manager = ProxyManager()
    >>> rate_limiter = RateLimiter()
    >>> cookie_manager = CookieManager()
    >>> playwright_pool = PlaywrightPool(user_agents_manager=ua_manager)
    >>> flaresolverr = FlareSolverrClient()
    >>>
    >>> # 2. Démarrer tous les composants
    >>> await ua_manager.start()
    >>> await proxy_manager.start()
    >>> await rate_limiter.start()
    >>> await cookie_manager.start()
    >>> await playwright_pool.start()
    >>> await flaresolverr.start()
    >>>
    >>> # 3. Créer la session HTTP avec tous les composants
    >>> session = HttpSession(
    ...     user_agents_manager=ua_manager,
    ...     proxy_manager=proxy_manager,
    ...     rate_limiter=rate_limiter,
    ...     cookie_manager=cookie_manager,
    ... )
    >>> await session.start()
    >>>
    >>> # 4. Effectuer une requête
    >>> response = await session.get(
    ...     "https://mangadex.org/api/manga",
    ...     site_id="mangadex",
    ... )
    >>> print(response.status_code)
    200
    >>>
    >>> # 5. Statistiques globales
    >>> session_stats = await session.get_stats()
    >>> print(f"Requêtes: {session_stats.total_requests}")
    >>> print(f"Latence moyenne: {session_stats.average_latency_ms:.1f}ms")
    >>>
    >>> # 6. Arrêter tous les composants
    >>> await session.stop()
    >>> await flaresolverr.stop()
    >>> await playwright_pool.stop()
    >>> await cookie_manager.stop()
    >>> await rate_limiter.stop()
    >>> await proxy_manager.stop()
    >>> await ua_manager.stop()

Exemple d'utilisation — Authentification manuelle via cookies :
    >>> # Importer des cookies depuis un fichier cookies.txt (extension navigateur)
    >>> await cookie_manager.import_from_netscape(Path("cookies.txt"))
    >>>
    >>> # Les cookies seront automatiquement injectés dans les requêtes
    >>> response = await session.get(
    ...     "https://protected-site.com/account",
    ...     site_id="protected_site",
    ... )
    >>>
    >>> # Exporter les cookies pour partage/debugging
    >>> await cookie_manager.export_to_netscape(Path("cookies_export.txt"))

Exemple d'utilisation — Résolution de challenge Cloudflare :
    >>> # Si un site retourne 403/503 avec challenge CF
    >>> result = await flaresolverr.resolve(
    ...     "https://cf-protected-site.com/manga/123",
    ...     max_timeout=60,
    ... )
    >>> if result.success:
    ...     # Les cookies de clearance sont automatiquement injectés
    ...     response = await session.get(
    ...         "https://cf-protected-site.com/manga/123",
    ...         site_id="cf_site",
    ...     )
"""

from __future__ import annotations

# ============================================================================
# EXCEPTIONS — Hiérarchie complète
# ============================================================================

# cookie_manager.py
from nexusdl.core.session.cookie_manager import (
    CookieDecryptionError,
    CookieError,
    CookieExportError,
    CookieImportError,
    CookieManagerNotStartedError,
    CookieStorageError,
    InvalidCookieError,
)
# flaresolverr.py
from nexusdl.core.session.flaresolverr import (
    ChallengeResolutionError,
    FlareSolverrError,
    FlareSolverrNotStartedError,
    FlareSolverrUnavailableError,
    TimeoutError as FlareSolverrTimeoutError,  # Alias pour éviter conflit avec builtin
)
# http_session.py
from nexusdl.core.session.http_session import (
    HttpRequestError,
    HttpSessionError,
    HttpSessionNotStartedError,
    HttpTimeoutError,
    HttpRateLimitError,
)
# playwright_pool.py
from nexusdl.core.session.playwright_pool import (
    ContextCreationError,
    NoContextAvailableError,
    PageExecutionError,
    PlaywrightNotInstalledError,
    PlaywrightPoolError,
    PlaywrightPoolNotStartedError,
    WarmupError,
)
# proxy_manager.py
from nexusdl.core.session.proxy_manager import (
    HealthCheckError,
    InvalidProxyUrlError,
    NoProxyAvailableError,
    ProxyError,
    ProxyLoadError,
    ProxyManagerNotStartedError,
)
# rate_limiter.py
from nexusdl.core.session.rate_limiter import (
    BucketNotFoundError,
    RateLimitError,
    RateLimitExceededError,
    RateLimiterNotStartedError,
)
# user_agents.py
from nexusdl.core.session.user_agents import (
    NoUserAgentAvailableError,
    UserAgentsError,
    UserAgentsLoadError,
    UserAgentsNotStartedError,
)

# ============================================================================
# ENUMS — États, stratégies et classifications
# ============================================================================

# cookie_manager.py
from nexusdl.core.session.cookie_manager import CookieStorage, CookieType
# flaresolverr.py
from nexusdl.core.session.flaresolverr import (
    ChallengeType,
    ResolutionStatus,
    ServiceStatus,
)
# http_session.py
from nexusdl.core.session.http_session import HttpMethod, RequestOutcome
# playwright_pool.py
from nexusdl.core.session.playwright_pool import (
    BrowserType,
    ContextState,
    FingerprintStrategy,
)
# proxy_manager.py
from nexusdl.core.session.proxy_manager import (
    ProxyHealth,
    ProxyType,
    RotationStrategy,
)
# rate_limiter.py
from nexusdl.core.session.rate_limiter import BackoffStrategy, RateLimitStrategy
# user_agents.py
from nexusdl.core.session.user_agents import (
    UserAgentBrowser,
    UserAgentCategory,
    UserAgentPlatform,
)

# ============================================================================
# MODÈLES PYDANTIC — Configurations
# ============================================================================

# cookie_manager.py
from nexusdl.core.session.cookie_manager import CookieManagerConfig
# flaresolverr.py
from nexusdl.core.session.flaresolverr import FlareSolverrConfig
# http_session.py
from nexusdl.core.session.http_session import HttpSessionConfig, RetryConfig
# playwright_pool.py
from nexusdl.core.session.playwright_pool import PlaywrightPoolConfig
# proxy_manager.py
from nexusdl.core.session.proxy_manager import ProxyConfig, ProxyManagerConfig
# rate_limiter.py
from nexusdl.core.session.rate_limiter import RateLimitConfig, SiteRateLimit

# ============================================================================
# MODÈLES PYDANTIC — Résultats et statistiques
# ============================================================================

# cookie_manager.py
from nexusdl.core.session.cookie_manager import (
    Cookie,
    CookieJar,
    CookieManagerStats,
)
# flaresolverr.py
from nexusdl.core.session.flaresolverr import (
    ClearanceCookie,
    FlareSolverrStats,
    ResolutionResult,
)
# http_session.py
from nexusdl.core.session.http_session import RequestStats, SessionStats
# playwright_pool.py
from nexusdl.core.session.playwright_pool import (
    BrowserFingerprint,
    ContextInfo,
    PoolStats,
)
# proxy_manager.py
from nexusdl.core.session.proxy_manager import (
    ProxyHealthCheck,
    ProxyManagerStats,
    ProxyStats,
)
# rate_limiter.py
from nexusdl.core.session.rate_limiter import BucketStats, RateLimitStats
# user_agents.py
from nexusdl.core.session.user_agents import UserAgentEntry, UserAgentsStats

# ============================================================================
# CLASSES PRINCIPALES — Orchestration réseau
# ============================================================================

from nexusdl.core.session.cookie_manager import CookieManager
from nexusdl.core.session.flaresolverr import FlareSolverrClient
from nexusdl.core.session.http_session import HttpSession, StreamDownloadContext
from nexusdl.core.session.playwright_pool import PlaywrightPool
from nexusdl.core.session.proxy_manager import ProxyManager
from nexusdl.core.session.rate_limiter import RateLimiter
from nexusdl.core.session.user_agents import UserAgentsManager

# ============================================================================
# HELPERS — Cookies
# ============================================================================

from nexusdl.core.session.cookie_manager import (
    generate_cookie_key,
    parse_set_cookie_header,
    quick_import_cookies,
)

# ============================================================================
# HELPERS — FlareSolverr
# ============================================================================

from nexusdl.core.session.flaresolverr import (
    get_flaresolverr_installation_instructions,
    is_flaresolverr_available,
    quick_resolve,
)

# ============================================================================
# HELPERS — Session HTTP
# ============================================================================

from nexusdl.core.session.http_session import download_file, quick_get

# ============================================================================
# HELPERS — Playwright
# ============================================================================

from nexusdl.core.session.playwright_pool import (
    generate_random_fingerprint,
    generate_realistic_fingerprint,
    generate_sticky_fingerprint,
    get_playwright_installation_instructions,
    is_playwright_installed,
    quick_page_execution,
)

# ============================================================================
# HELPERS — Proxies
# ============================================================================

from nexusdl.core.session.proxy_manager import (
    build_proxies_dict,
    load_proxies_from_file,
    parse_proxy_url,
)

# ============================================================================
# HELPERS — Rate Limiter
# ============================================================================

from nexusdl.core.session.rate_limiter import build_endpoint_key, parse_retry_after

# ============================================================================
# HELPERS — User-Agents
# ============================================================================

from nexusdl.core.session.user_agents import (
    detect_browser,
    detect_category,
    detect_platform,
    detect_user_agent_info,
    is_likely_bot,
    load_user_agents_from_file,
)

# ============================================================================
# EXPORTS PUBLICS
# ============================================================================

__all__ = [
    # ========================================================================
    # Classes principales — Orchestration réseau
    # ========================================================================
    "HttpSession",
    "StreamDownloadContext",
    "CookieManager",
    "PlaywrightPool",
    "RateLimiter",
    "UserAgentsManager",
    "ProxyManager",
    "FlareSolverrClient",
    # ========================================================================
    # Enums — Cookies
    # ========================================================================
    "CookieType",
    "CookieStorage",
    # ========================================================================
    # Enums — FlareSolverr
    # ========================================================================
    "ChallengeType",
    "ResolutionStatus",
    "ServiceStatus",
    # ========================================================================
    # Enums — Session HTTP
    # ========================================================================
    "HttpMethod",
    "RequestOutcome",
    # ========================================================================
    # Enums — Playwright
    # ========================================================================
    "BrowserType",
    "FingerprintStrategy",
    "ContextState",
    # ========================================================================
    # Enums — Proxies
    # ========================================================================
    "ProxyType",
    "RotationStrategy",
    "ProxyHealth",
    # ========================================================================
    # Enums — Rate Limiter
    # ========================================================================
    "RateLimitStrategy",
    "BackoffStrategy",
    # ========================================================================
    # Enums — User-Agents
    # ========================================================================
    "UserAgentCategory",
    "UserAgentPlatform",
    "UserAgentBrowser",
    # ========================================================================
    # Modèles — Configurations
    # ========================================================================
    "HttpSessionConfig",
    "RetryConfig",
    "CookieManagerConfig",
    "PlaywrightPoolConfig",
    "RateLimitConfig",
    "SiteRateLimit",
    "ProxyConfig",
    "ProxyManagerConfig",
    "FlareSolverrConfig",
    # ========================================================================
    # Modèles — Cookies
    # ========================================================================
    "Cookie",
    "CookieJar",
    "CookieManagerStats",
    # ========================================================================
    # Modèles — FlareSolverr
    # ========================================================================
    "ClearanceCookie",
    "ResolutionResult",
    "FlareSolverrStats",
    # ========================================================================
    # Modèles — Session HTTP
    # ========================================================================
    "RequestStats",
    "SessionStats",
    # ========================================================================
    # Modèles — Playwright
    # ========================================================================
    "BrowserFingerprint",
    "ContextInfo",
    "PoolStats",
    # ========================================================================
    # Modèles — Proxies
    # ========================================================================
    "ProxyHealthCheck",
    "ProxyStats",
    "ProxyManagerStats",
    # ========================================================================
    # Modèles — Rate Limiter
    # ========================================================================
    "BucketStats",
    "RateLimitStats",
    # ========================================================================
    # Modèles — User-Agents
    # ========================================================================
    "UserAgentEntry",
    "UserAgentsStats",
    # ========================================================================
    # Helpers — Cookies
    # ========================================================================
    "parse_set_cookie_header",
    "generate_cookie_key",
    "quick_import_cookies",
    # ========================================================================
    # Helpers — FlareSolverr
    # ========================================================================
    "is_flaresolverr_available",
    "get_flaresolverr_installation_instructions",
    "quick_resolve",
    # ========================================================================
    # Helpers — Session HTTP
    # ========================================================================
    "quick_get",
    "download_file",
    # ========================================================================
    # Helpers — Playwright
    # ========================================================================
    "generate_realistic_fingerprint",
    "generate_random_fingerprint",
    "generate_sticky_fingerprint",
    "is_playwright_installed",
    "get_playwright_installation_instructions",
    "quick_page_execution",
    # ========================================================================
    # Helpers — Proxies
    # ========================================================================
    "parse_proxy_url",
    "build_proxies_dict",
    "load_proxies_from_file",
    # ========================================================================
    # Helpers — Rate Limiter
    # ========================================================================
    "parse_retry_after",
    "build_endpoint_key",
    # ========================================================================
    # Helpers — User-Agents
    # ========================================================================
    "detect_platform",
    "detect_browser",
    "detect_category",
    "detect_user_agent_info",
    "is_likely_bot",
    "load_user_agents_from_file",
    # ========================================================================
    # Exceptions — Cookies
    # ========================================================================
    "CookieError",
    "CookieManagerNotStartedError",
    "CookieStorageError",
    "CookieDecryptionError",
    "CookieImportError",
    "CookieExportError",
    "InvalidCookieError",
    # ========================================================================
    # Exceptions — FlareSolverr
    # ========================================================================
    "FlareSolverrError",
    "FlareSolverrNotStartedError",
    "FlareSolverrUnavailableError",
    "ChallengeResolutionError",
    "FlareSolverrTimeoutError",  # Alias pour éviter conflit avec builtin TimeoutError
    # ========================================================================
    # Exceptions — Session HTTP
    # ========================================================================
    "HttpSessionError",
    "HttpSessionNotStartedError",
    "HttpRequestError",
    "HttpTimeoutError",
    "HttpRateLimitError",
    # ========================================================================
    # Exceptions — Playwright
    # ========================================================================
    "PlaywrightPoolError",
    "PlaywrightNotInstalledError",
    "PlaywrightPoolNotStartedError",
    "NoContextAvailableError",
    "ContextCreationError",
    "PageExecutionError",
    "WarmupError",
    # ========================================================================
    # Exceptions — Proxies
    # ========================================================================
    "ProxyError",
    "ProxyManagerNotStartedError",
    "NoProxyAvailableError",
    "InvalidProxyUrlError",
    "ProxyLoadError",
    "HealthCheckError",
    # ========================================================================
    # Exceptions — Rate Limiter
    # ========================================================================
    "RateLimitError",
    "RateLimiterNotStartedError",
    "RateLimitExceededError",
    "BucketNotFoundError",
    # ========================================================================
    # Exceptions — User-Agents
    # ========================================================================
    "UserAgentsError",
    "UserAgentsNotStartedError",
    "UserAgentsLoadError",
    "NoUserAgentAvailableError",
]

__version__: str = "0.1.0"
