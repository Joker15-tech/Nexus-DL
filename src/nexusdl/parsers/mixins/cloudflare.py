"""Mixin Cloudflare pour les parseurs NexusDL.

Ce module expose :class:`CloudflareMixin`, une brique réutilisable qui
ajoute à un parser NexusDL une gestion complète des protections
**Cloudflare** rencontrées sur la majorité des sites manga modernes :

* Détection fine des challenges : **IUAM** (``Just a moment...``), **JS
  Challenge**, **Managed Challenge**, **Turnstile**, **Block 1020/1006/1015**.
* Bypass automatique via deux backends interchangeables et chaînables :
    - :class:`PlaywrightPool` (navigateur Chromium headless, résolution
      JS/Turnstile automatique, capture du cookie ``cf_clearance``).
    - :class:`FlareSolverrClient` (service HTTP externe, mêmes capacités,
      souvent plus rapide à froid, sans dépendance Chromium locale).
* Persistance et **réutilisation** du cookie ``cf_clearance`` par domaine
  (cache mémoire + :class:`CookieManager` chiffré).
* Rafraîchissement transparent à l'expiration (TTL configurable).
* Wrappers haut-niveau ``cf_get`` / ``cf_post`` / ``cf_get_html`` /
  ``cf_get_json`` qui détectent un blocage, déclenchent le bypass puis
  rejouent la requête d'origine.
* Hooks de personnalisation (``cf_on_challenge_detected``,
  ``cf_on_bypass_success``, ``cf_on_bypass_failure``).
* Évitement de boucle infinie via un compteur de tentatives par URL.
* Support des headers de diagnostic Cloudflare : ``cf-ray``,
  ``cf-mitigated``, ``cf-chl-*``, ``server: cloudflare``.

Contrat implicite du parser hôte
--------------------------------

Le parser hôte doit fournir :

* ``self.config: SiteConfig`` (avec ``id`` et ``domains``).
* ``self.session: HttpSession`` (session httpx typée du projet).
* ``self.playwright_pool: PlaywrightPool | None`` (optionnel — requis pour
  le backend Playwright).
* ``self.cookie_manager: CookieManager | None`` (optionnel — pour la
  persistance chiffrée du clearance).
* ``self.flaresolverr: FlareSolverrClient | None`` (optionnel — requis pour
  le backend FlareSolverr).
* ``self.logger`` (optionnel — sinon :func:`get_logger` est utilisé).

Le mixin **ne dépend jamais** de ``interfaces/`` et reste agnostique vis-à-vis
du backend utilisé tant qu'il expose les mêmes primitives.

Example:
    Utilisation typique :

    >>> class MangaDexParser(CloudflareMixin, BaseParser):
    ...     site_id = "mangadex"
    ...     cf_requires_js = True
    ...
    ...     async def search(self, query: str, *, page: int = 1):
    ...         url = f"https://api.mangadex.org/manga?title={query}"
    ...         data = await self.cf_get_json(url)
    ...         return self._parse_search_json(data)
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Final, Literal, TypeAlias
from urllib.parse import urlparse

import httpx

from nexusdl.core.exceptions import (
    CloudflareBypassError,
    JsRenderingError,
    NexusDLError,
)
from nexusdl.core.logger import get_logger

__all__ = [
    "CloudflareMixin",
    "CloudflareChallengeType",
    "ClearanceEntry",
]


# ---------------------------------------------------------------------------
# Types et constantes
# ---------------------------------------------------------------------------

CloudflareChallengeType: TypeAlias = Literal[
    "none",
    "iuam",  # I'm Under Attack Mode
    "js",  # JS challenge classique
    "managed",  # Managed Challenge
    "turnstile",  # Turnstile widget
    "block",  # Block page (1020, 1006, 1015)
    "unknown",
]

BypassBackend: TypeAlias = Literal["playwright", "flaresolverr"]


_CF_SERVER_MARKERS: Final[frozenset[str]] = frozenset({"cloudflare", "cloudflare-nginx"})

_CF_CHALLENGE_MARKERS: Final[tuple[str, ...]] = (
    "just a moment",
    "checking your browser before accessing",
    "enable javascript and cookies to continue",
    "attention required! | cloudflare",
    "cf-browser-verification",
    "cf-challenge",
    "challenge-platform",
    "challenge-form",
)

_CF_TURNSTILE_MARKERS: Final[tuple[str, ...]] = (
    "challenges.cloudflare.com/turnstile",
    "cf-turnstile",
    "cf_chl_tk",
    "turnstile.render",
)

_CF_BLOCK_MARKERS: Final[tuple[str, ...]] = (
    "error 1020",
    "error 1006",
    "error 1015",
    "you have been blocked",
    "access denied",
    "sorry, you have been blocked",
)

_CF_COOKIE_NAMES_PRIMARY: Final[frozenset[str]] = frozenset({"cf_clearance"})

_CF_COOKIE_NAMES_ANY: Final[frozenset[str]] = frozenset(
    {"cf_clearance", "__cf_bm", "cf_chl_2", "cf_chl_prog", "cf_chl_rc_ni"}
)

_CF_BLOCK_STATUS: Final[frozenset[int]] = frozenset({403, 429, 503})

_CF_RAY_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{16}-[A-Z]{3}$")

_DEFAULT_CLEARANCE_TTL: Final[int] = 1800  # 30 minutes — TTL prudent
_DEFAULT_MAX_ATTEMPTS: Final[int] = 3
_DEFAULT_CHALLENGE_TIMEOUT: Final[float] = 30.0
_DEFAULT_POLL_INTERVAL: Final[float] = 0.5
_DEFAULT_BACKOFF_BASE: Final[float] = 1.5


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClearanceEntry:
    """Entrée de cache pour un cookie ``cf_clearance``.

    Attributes:
        domain: Domaine de rattachement (``example.com``).
        cookies: Mapping ``name -> value`` de tous les cookies CF connus.
        user_agent: User-Agent utilisé lors de l'obtention (obligatoire pour
            que le ``cf_clearance`` reste valide — CF le lie à l'UA).
        issued_at: Timestamp monotone d'obtention.
        ttl: Durée de vie en secondes.
        backend: Backend ayant produit le clearance.
        ray_id: Identifiant ``cf-ray`` observé (debug).
    """

    domain: str
    cookies: dict[str, str]
    user_agent: str
    issued_at: float
    ttl: int = _DEFAULT_CLEARANCE_TTL
    backend: BypassBackend = "playwright"
    ray_id: str | None = None

    @property
    def is_expired(self) -> bool:
        """Indique si l'entrée est expirée."""
        return (time.monotonic() - self.issued_at) >= self.ttl

    @property
    def clearance(self) -> str | None:
        """Retourne la valeur du cookie ``cf_clearance`` si présent."""
        return self.cookies.get("cf_clearance")


@dataclass(slots=True)
class _BypassStats:
    """Compteur interne de tentatives (anti-boucle)."""

    attempts: dict[str, int] = field(default_factory=dict)

    def increment(self, key: str) -> int:
        """Incrémente et retourne le compteur pour une clé."""
        self.attempts[key] = self.attempts.get(key, 0) + 1
        return self.attempts[key]

    def reset(self, key: str) -> None:
        """Réinitialise le compteur pour une clé."""
        self.attempts.pop(key, None)


# ---------------------------------------------------------------------------
# Mixin principal
# ---------------------------------------------------------------------------


class CloudflareMixin:
    """Mixin de contournement Cloudflare pour les parseurs NexusDL.

    Fournit une détection fine des challenges et un pipeline de bypass
    automatique basé sur Playwright et/ou FlareSolverr, avec cache et
    persistance du cookie ``cf_clearance``.

    Class Attributes:
        cf_enabled: Active/désactive globalement le contournement.
        cf_requires_js: Force le passage par navigateur (site SPA protégé).
        cf_preferred_backend: Backend privilégié (``"playwright"`` ou
            ``"flaresolverr"``).
        cf_use_playwright_fallback: Autorise Playwright en secours.
        cf_use_flaresolverr_fallback: Autorise FlareSolverr en secours.
        cf_max_attempts: Nombre max de tentatives par URL.
        cf_challenge_timeout: Durée max d'attente de résolution d'un challenge.
        cf_poll_interval: Intervalle de poll des cookies CF.
        cf_clearance_ttl: Durée de vie par défaut du clearance (secondes).
        cf_backoff_base: Base du backoff exponentiel entre tentatives.
        cf_blocked_status_codes: Codes HTTP déclenchant une analyse.
        cf_detect_turnstile: Active la détection Turnstile.
        cf_persist_clearance: Persiste le clearance dans le CookieManager.
        cf_debug_dir: Dossier des captures de debug en cas d'échec.
    """

    # --- Configuration globale ---
    cf_enabled: ClassVar[bool] = True
    cf_requires_js: ClassVar[bool] = False
    cf_preferred_backend: ClassVar[BypassBackend] = "playwright"
    cf_use_playwright_fallback: ClassVar[bool] = True
    cf_use_flaresolverr_fallback: ClassVar[bool] = True
    cf_max_attempts: ClassVar[int] = _DEFAULT_MAX_ATTEMPTS
    cf_challenge_timeout: ClassVar[float] = _DEFAULT_CHALLENGE_TIMEOUT
    cf_poll_interval: ClassVar[float] = _DEFAULT_POLL_INTERVAL
    cf_clearance_ttl: ClassVar[int] = _DEFAULT_CLEARANCE_TTL
    cf_backoff_base: ClassVar[float] = _DEFAULT_BACKOFF_BASE
    cf_blocked_status_codes: ClassVar[frozenset[int]] = _CF_BLOCK_STATUS
    cf_detect_turnstile: ClassVar[bool] = True
    cf_persist_clearance: ClassVar[bool] = True
    cf_debug_dir: ClassVar[Path] = Path(".nexusdl") / "debug" / "cloudflare"
    cf_max_cached_domains: ClassVar[int] = 128

    # --- Contrat du parser hôte (annotations uniquement) ---
    config: Any
    session: Any
    playwright_pool: Any | None = None
    cookie_manager: Any | None = None
    flaresolverr: Any | None = None

    # --- Cache interne par instance (lazy) ---
    _cf_cache: ClassVar[dict[str, ClearanceEntry]] = {}
    _cf_stats: ClassVar[_BypassStats] = _BypassStats()
    _cf_lock: ClassVar[asyncio.Lock | None] = None

    # ------------------------------------------------------------------
    # Setup / helpers internes
    # ------------------------------------------------------------------

    def _cf_logger(self) -> Any:
        """Retourne un logger loguru contextualisé.

        Returns:
            Logger bindé avec ``site_id`` et ``mixin="cloudflare"``.
        """
        existing = getattr(self, "logger", None)
        if existing is not None:
            return existing
        logger = get_logger(self.__class__.__module__)
        site_id = getattr(self.config, "id", "unknown")
        return logger.bind(site_id=site_id, mixin="cloudflare")

    def _cf_get_lock(self) -> asyncio.Lock:
        """Retourne le verrou global (créé à la demande dans la boucle courante).

        Returns:
            Verrou asyncio partagé pour sérialiser les bypass par domaine.
        """
        lock = CloudflareMixin._cf_lock
        if lock is None:
            lock = asyncio.Lock()
            CloudflareMixin._cf_lock = lock
        return lock

    def _cf_domain_for(self, url: str) -> str:
        """Extrait le domaine (registrable-ish) d'une URL.

        Args:
            url: URL absolue.

        Returns:
            Domaine sous forme ``example.com``.

        Raises:
            CloudflareBypassError: Si l'URL ne contient pas de netloc.
        """
        parsed = urlparse(url)
        netloc = parsed.netloc
        if not netloc:
            raise CloudflareBypassError(f"URL sans domaine exploitable : {url!r}")
        return netloc.split(":", 1)[0].lower()

    def _cf_user_agent(self) -> str:
        """Récupère l'User-Agent courant (UA obligatoire pour cf_clearance).

        Returns:
            Chaîne User-Agent ; valeur par défaut générique si aucune.
        """
        ua = getattr(self.session, "user_agent", None)
        if ua:
            return str(ua)
        headers = getattr(self.config, "default_headers", None) or {}
        for key in ("User-Agent", "user-agent"):
            if key in headers:
                return str(headers[key])
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )

    def _cf_proxy(self) -> str | None:
        """Récupère le proxy courant (session ou proxy manager).

        Returns:
            URL de proxy ou ``None``.
        """
        proxy = getattr(self.session, "proxy", None)
        if proxy:
            return str(proxy)
        manager = getattr(self.session, "proxy_manager", None)
        if manager is not None:
            try:
                return manager.get_current()  # type: ignore[no-any-return]
            except Exception:  # noqa: BLE001
                return None
        return None

    # ------------------------------------------------------------------
    # Détection des challenges
    # ------------------------------------------------------------------

    def cf_is_challenge_html(self, html: str | None) -> bool:
        """Détecte un challenge Cloudflare depuis un corps HTML.

        Args:
            html: Contenu HTML de la réponse.

        Returns:
            ``True`` si un marqueur de challenge est présent.
        """
        if not html:
            return False
        sample = html[:8192].lower()
        if any(marker in sample for marker in _CF_CHALLENGE_MARKERS):
            return True
        if self.cf_detect_turnstile and any(
            marker in sample for marker in _CF_TURNSTILE_MARKERS
        ):
            return True
        if any(marker in sample for marker in _CF_BLOCK_MARKERS):
            return True
        return False

    def cf_detect_type(
        self,
        *,
        html: str | None = None,
        status: int | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> CloudflareChallengeType:
        """Classe précisément le type de challenge Cloudflare rencontré.

        Args:
            html: Corps HTML de la réponse.
            status: Code HTTP de la réponse.
            headers: En-têtes HTTP de la réponse (insensibles à la casse).

        Returns:
            Type de challenge détecté (``"none"`` si aucun).
        """
        lowered_headers = (
            {k.lower(): str(v) for k, v in (headers or {}).items()}
        )
        server = lowered_headers.get("server", "")
        ray = lowered_headers.get("cf-ray", "")
        mitigated = lowered_headers.get("cf-mitigated", "").lower()

        is_cloudflare = (
            any(marker in server for marker in _CF_SERVER_MARKERS)
            or bool(ray and _CF_RAY_RE.match(ray))
            or "cf-cache-status" in lowered_headers
            or bool(mitigated)
        )

        sample = (html or "")[:8192].lower()

        if any(marker in sample for marker in _CF_BLOCK_MARKERS):
            return "block"
        if self.cf_detect_turnstile and any(
            marker in sample for marker in _CF_TURNSTILE_MARKERS
        ):
            return "turnstile"
        if any(marker in sample for marker in _CF_CHALLENGE_MARKERS):
            if "just a moment" in sample or "iuam" in sample:
                return "iuam"
            if "managed" in sample or "challenge-platform" in sample:
                return "managed"
            return "js"
        if not is_cloudflare:
            return "none"
        if status is not None and status in self.cf_blocked_status_codes:
            return "unknown"
        return "none"

    def cf_is_challenge_response(self, response: httpx.Response) -> bool:
        """Détecte un challenge Cloudflare depuis une réponse httpx.

        Args:
            response: Réponse httpx à analyser.

        Returns:
            ``True`` si la réponse est très probablement un challenge CF.
        """
        status = response.status_code
        headers = dict(response.headers)
        server = headers.get("server", "").lower()
        mitigated = headers.get("cf-mitigated", "").lower()

        if status in self.cf_blocked_status_codes:
            if any(marker in server for marker in _CF_SERVER_MARKERS):
                return True
            if "cf-ray" in {k.lower() for k in headers}:
                return True

        if status == 429 and "cf-ray" in {k.lower() for k in headers}:
            return True

        if mitigated:
            return True

        try:
            snippet = response.text[:8192]
        except Exception:  # noqa: BLE001 — corps non décodable
            return False

        if self.cf_is_challenge_html(snippet):
            return True

        if "cf-chl-" in snippet.lower():
            return True

        return False

    # ------------------------------------------------------------------
    # Gestion du cache clearance
    # ------------------------------------------------------------------

    def _cf_cache_get(self, domain: str) -> ClearanceEntry | None:
        """Retourne une entrée de cache valide pour un domaine.

        Args:
            domain: Domaine cible.

        Returns:
            Entrée non expirée, ou ``None``.
        """
        entry = CloudflareMixin._cf_cache.get(domain)
        if entry is None:
            return None
        if entry.is_expired:
            CloudflareMixin._cf_cache.pop(domain, None)
            self._cf_logger().debug(
                "Clearance expiré pour {domain}", domain=domain
            )
            return None
        if entry.user_agent != self._cf_user_agent():
            # cf_clearance est lié à l'UA : toute divergence invalide le cookie.
            CloudflareMixin._cf_cache.pop(domain, None)
            self._cf_logger().debug(
                "Clearance invalidé (UA différent) pour {domain}", domain=domain
            )
            return None
        return entry

    def _cf_cache_put(self, entry: ClearanceEntry) -> None:
        """Insère une entrée de cache en respectant la limite de taille.

        Args:
            entry: Entrée à stocker.
        """
        cache = CloudflareMixin._cf_cache
        cache[entry.domain] = entry
        if len(cache) > self.cf_max_cached_domains:
            oldest = min(cache.items(), key=lambda kv: kv[1].issued_at)
            cache.pop(oldest[0], None)

    def cf_clear_cache(self, domain: str | None = None) -> None:
        """Invalide le cache clearance.

        Args:
            domain: Domaine précis ; ``None`` pour vider entièrement le cache.
        """
        if domain is None:
            CloudflareMixin._cf_cache.clear()
            self._cf_logger().debug("Cache clearance vidé intégralement")
            return
        CloudflareMixin._cf_cache.pop(domain, None)
        self._cf_logger().debug("Cache clearance vidé pour {domain}", domain=domain)

    def _cf_apply_clearance_to_session(
        self, domain: str, cookies: Mapping[str, str]
    ) -> None:
        """Fusionne les cookies clearance dans la session HTTP du parser.

        Args:
            domain: Domaine cible (pour log).
            cookies: Cookies à appliquer.
        """
        target = getattr(self.session, "cookies", None)
        if isinstance(target, dict):
            target.update(cookies)
            return
        update = getattr(self.session, "update_cookies", None)
        if callable(update):
            with contextlib.suppress(Exception):
                update(dict(cookies))
        self._cf_logger().debug(
            "Cookies clearance appliqués à la session pour {domain} ({n})",
            domain=domain,
            n=len(cookies),
        )

    def _cf_persist_clearance(
        self, domain: str, cookies: Mapping[str, str]
    ) -> None:
        """Persiste les cookies clearance via le CookieManager du parser.

        Args:
            domain: Domaine cible.
            cookies: Cookies à persister.
        """
        if not self.cf_persist_clearance:
            return
        manager = getattr(self, "cookie_manager", None)
        if manager is None:
            return
        try:
            setter = getattr(manager, "set_cookies", None)
            if callable(setter):
                setter(self.config.id, dict(cookies))
                return
            updater = getattr(manager, "update_cookies", None)
            if callable(updater):
                updater(self.config.id, dict(cookies))
        except Exception as exc:  # noqa: BLE001 — persistance best-effort
            self._cf_logger().debug(
                "Persistance clearance KO pour {domain}: {err}",
                domain=domain,
                err=exc,
            )

    def _cf_load_persisted_clearance(self, domain: str) -> dict[str, str]:
        """Charge un éventuel clearance persisté pour un domaine.

        Args:
            domain: Domaine cible.

        Returns:
            Mapping cookies (vide si rien de trouvé).
        """
        if not self.cf_persist_clearance:
            return {}
        manager = getattr(self, "cookie_manager", None)
        if manager is None:
            return {}
        try:
            getter = getattr(manager, "get_cookies", None)
            if not callable(getter):
                return {}
            raw = getter(self.config.id)
        except Exception:  # noqa: BLE001
            return {}
        if not isinstance(raw, dict):
            return {}
        filtered = {
            str(k): str(v) for k, v in raw.items() if k in _CF_COOKIE_NAMES_ANY
        }
        if filtered.get("cf_clearance"):
            self._cf_logger().debug(
                "Clearance persisté rechargé pour {domain}", domain=domain
            )
        return filtered

    # ------------------------------------------------------------------
    # Backends de bypass
    # ------------------------------------------------------------------

    async def _cf_bypass_via_playwright(
        self,
        url: str,
        *,
        timeout: float | None = None,
    ) -> dict[str, str]:
        """Résout un challenge CF via PlaywrightPool.

        Args:
            url: URL protégée à charger.
            timeout: Timeout global du challenge.

        Returns:
            Cookies CF récupérés (dont ``cf_clearance``).

        Raises:
            CloudflareBypassError: Si le pool est absent ou le challenge échoue.
        """
        pool = getattr(self, "playwright_pool", None)
        if pool is None:
            raise CloudflareBypassError(
                f"PlaywrightPool indisponible pour {self.config.id!r}"
            )
        timeout = timeout or self.cf_challenge_timeout
        deadline = time.monotonic() + timeout
        ua = self._cf_user_agent()
        proxy = self._cf_proxy()

        async with pool.acquire(
            user_agent=ua,
            proxy=proxy,
            locale=getattr(self, "locale", "fr-FR"),
            timezone_id=getattr(self, "timezone_id", "Europe/Paris"),
        ) as context:
            page = await context.new_page()
            page.set_default_navigation_timeout(timeout * 1000)
            try:
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=timeout * 1000
                )

                while time.monotonic() < deadline:
                    cookies = {
                        c["name"]: c["value"]
                        for c in await context.cookies()
                        if c.get("name") in _CF_COOKIE_NAMES_ANY
                    }
                    if "cf_clearance" in cookies:
                        self._cf_logger().info(
                            "Challenge résolu via Playwright pour {url}",
                            url=url,
                        )
                        return cookies
                    await asyncio.sleep(self.cf_poll_interval)

            except JsRenderingError:
                raise
            except Exception as exc:  # noqa: BLE001 — Playwright best-effort
                raise CloudflareBypassError(
                    f"Échec Playwright sur {url}: {exc}"
                ) from exc
            finally:
                with contextlib.suppress(Exception):
                    await page.close()

        raise CloudflareBypassError(
            f"Challenge Cloudflare non résolu via Playwright sur {url!r} "
            f"({timeout}s)"
        )

    async def _cf_bypass_via_flaresolverr(
        self,
        url: str,
        *,
        timeout: float | None = None,
    ) -> dict[str, str]:
        """Résout un challenge CF via le client FlareSolverr.

        Args:
            url: URL protégée à charger.
            timeout: Timeout global du challenge.

        Returns:
            Cookies CF récupérés.

        Raises:
            CloudflareBypassError: Si le client est absent ou échoue.
        """
        client = getattr(self, "flaresolverr", None)
        if client is None:
            raise CloudflareBypassError(
                f"FlareSolverrClient indisponible pour {self.config.id!r}"
            )
        timeout = timeout or self.cf_challenge_timeout
        try:
            solver = getattr(client, "solve", None) or getattr(
                client, "request_get", None
            )
            if not callable(solver):
                raise CloudflareBypassError(
                    "FlareSolverrClient sans méthode 'solve' ou 'request_get'"
                )
            payload = await solver(url, timeout=timeout)  # type: ignore[misc]
        except CloudflareBypassError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CloudflareBypassError(
                f"Échec FlareSolverr sur {url}: {exc}"
            ) from exc

        cookies = self._cf_extract_flaresolverr_cookies(payload)
        if "cf_clearance" not in cookies:
            raise CloudflareBypassError(
                f"FlareSolverr n'a pas retourné de cf_clearance pour {url!r}"
            )
        self._cf_logger().info(
            "Challenge résolu via FlareSolverr pour {url}", url=url
        )
        return cookies

    @staticmethod
    def _cf_extract_flaresolverr_cookies(payload: Any) -> dict[str, str]:
        """Extrait les cookies CF depuis la réponse FlareSolverr.

        Args:
            payload: Charge retournée par le client (dict ou objet).

        Returns:
            Mapping ``name -> value`` filtré sur les cookies CF.
        """
        cookies_raw: Any = None
        if isinstance(payload, dict):
            solution = payload.get("solution") or payload
            if isinstance(solution, dict):
                cookies_raw = solution.get("cookies")
        else:
            solution = getattr(payload, "solution", None) or payload
            cookies_raw = getattr(solution, "cookies", None)

        result: dict[str, str] = {}
        if isinstance(cookies_raw, list):
            for c in cookies_raw:
                if not isinstance(c, dict):
                    continue
                name = c.get("name")
                value = c.get("value")
                if name and value is not None and name in _CF_COOKIE_NAMES_ANY:
                    result[str(name)] = str(value)
        return result

    async def _cf_bypass_chain(
        self,
        url: str,
        *,
        timeout: float | None = None,
    ) -> dict[str, str]:
        """Enchaîne les backends selon la stratégie configurée.

        Args:
            url: URL protégée.
            timeout: Timeout global.

        Returns:
            Cookies CF obtenus.

        Raises:
            CloudflareBypassError: Si tous les backends échouent.
        """
        backends: list[BypassBackend] = [self.cf_preferred_backend]
        if (
            self.cf_use_playwright_fallback
            and "playwright" not in backends
            and getattr(self, "playwright_pool", None) is not None
        ):
            backends.append("playwright")
        if (
            self.cf_use_flaresolverr_fallback
            and "flaresolverr" not in backends
            and getattr(self, "flaresolverr", None) is not None
        ):
            backends.append("flaresolverr")

        last_error: Exception | None = None
        for backend in backends:
            try:
                if backend == "playwright":
                    return await self._cf_bypass_via_playwright(url, timeout=timeout)
                if backend == "flaresolverr":
                    return await self._cf_bypass_via_flaresolverr(
                        url, timeout=timeout
                    )
            except CloudflareBypassError as exc:
                last_error = exc
                self._cf_logger().warning(
                    "Backend {b} a échoué pour {url}: {err}",
                    b=backend,
                    url=url,
                    err=exc,
                )
                continue

        raise CloudflareBypassError(
            f"Aucun backend n'a pu résoudre le challenge pour {url!r} "
            f"(dernière erreur : {last_error})"
        )

    # ------------------------------------------------------------------
    # Résolution haut niveau
    # ------------------------------------------------------------------

    async def cf_ensure_clearance(
        self,
        url: str,
        *,
        force: bool = False,
        timeout: float | None = None,
    ) -> ClearanceEntry:
        """S'assure qu'un clearance valide existe pour le domaine d'une URL.

        Args:
            url: URL cible.
            force: Force le rafraîchissement même si un clearance est en cache.
            timeout: Timeout de résolution du challenge.

        Returns:
            L'entrée :class:`ClearanceEntry` valide.

        Raises:
            CloudflareBypassError: Si la résolution échoue.
        """
        if not self.cf_enabled:
            raise CloudflareBypassError(
                "Contournement Cloudflare désactivé (cf_enabled=False)"
            )

        domain = self._cf_domain_for(url)
        ua = self._cf_user_agent()

        if not force:
            cached = self._cf_cache_get(domain)
            if cached is not None:
                self._cf_apply_clearance_to_session(domain, cached.cookies)
                return cached

            persisted = self._cf_load_persisted_clearance(domain)
            if persisted.get("cf_clearance"):
                entry = ClearanceEntry(
                    domain=domain,
                    cookies=persisted,
                    user_agent=ua,
                    issued_at=time.monotonic(),
                    ttl=self.cf_clearance_ttl,
                    backend=self.cf_preferred_backend,
                )
                self._cf_cache_put(entry)
                self._cf_apply_clearance_to_session(domain, persisted)
                return entry

        # Sérialise les bypass par domaine pour éviter les tempêtes.
        async with self._cf_get_lock():
            cached = None if force else self._cf_cache_get(domain)
            if cached is not None:
                self._cf_apply_clearance_to_session(domain, cached.cookies)
                return cached

            self._cf_logger().info(
                "Résolution d'un challenge Cloudflare pour {domain} via {b}",
                domain=domain,
                b=self.cf_preferred_backend,
            )
            cookies = await self._cf_bypass_chain(url, timeout=timeout)

            entry = ClearanceEntry(
                domain=domain,
                cookies=cookies,
                user_agent=ua,
                issued_at=time.monotonic(),
                ttl=self.cf_clearance_ttl,
                backend=self.cf_preferred_backend,
            )
            self._cf_cache_put(entry)
            self._cf_apply_clearance_to_session(domain, cookies)
            self._cf_persist_clearance(domain, cookies)
            return entry

    # ------------------------------------------------------------------
    # Wrappers requêtes HTTP avec auto-bypass
    # ------------------------------------------------------------------

    async def _cf_http_get(
        self, url: str, *, kwargs: dict[str, Any]
    ) -> httpx.Response:
        """Exécute un GET httpx brut (délégué à la session du parser).

        Args:
            url: URL cible.
            kwargs: Arguments transmis à la session.

        Returns:
            Réponse httpx.
        """
        getter = getattr(self.session, "get")
        result = getter(url, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result  # type: ignore[return-value]

    async def _cf_http_post(
        self, url: str, *, kwargs: dict[str, Any]
    ) -> httpx.Response:
        """Exécute un POST httpx brut via la session du parser.

        Args:
            url: URL cible.
            kwargs: Arguments transmis à la session.

        Returns:
            Réponse httpx.
        """
        poster = getattr(self.session, "post")
        result = poster(url, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result  # type: ignore[return-value]

    async def cf_request(
        self,
        method: str,
        url: str,
        *,
        max_attempts: int | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Exécute une requête HTTP avec détection et bypass Cloudflare.

        Args:
            method: Méthode HTTP (``GET``, ``POST``…).
            url: URL cible.
            max_attempts: Nombre max de tentatives (défaut :
                ``cf_max_attempts``).
            **kwargs: Arguments transmis à la session httpx.

        Returns:
            Réponse httpx finale (post-bypass si nécessaire).

        Raises:
            CloudflareBypassError: Si le bypass échoue après toutes les
                tentatives.
        """
        if not self.cf_enabled:
            if method.upper() == "GET":
                return await self._cf_http_get(url, kwargs=kwargs)
            if method.upper() == "POST":
                return await self._cf_http_post(url, kwargs=kwargs)
            raise CloudflareBypassError(
                f"Méthode HTTP non gérée par CloudflareMixin : {method!r}"
            )

        max_attempts = max_attempts or self.cf_max_attempts
        domain = self._cf_domain_for(url)
        attempt_key = f"{method.upper()} {url}"

        # Injecte un clearance valide en amont si disponible.
        cached = self._cf_cache_get(domain)
        if cached is not None:
            self._cf_apply_clearance_to_session(domain, cached.cookies)

        last_response: httpx.Response | None = None

        for attempt in range(1, max_attempts + 1):
            if method.upper() == "GET":
                response = await self._cf_http_get(url, kwargs=kwargs)
            elif method.upper() == "POST":
                response = await self._cf_http_post(url, kwargs=kwargs)
            else:
                raise CloudflareBypassError(
                    f"Méthode HTTP non supportée : {method!r}"
                )
            last_response = response

            if not self.cf_is_challenge_response(response):
                CloudflareMixin._cf_stats.reset(attempt_key)
                return response

            challenge_type = self.cf_detect_type(
                html=self._cf_safe_text(response),
                status=response.status_code,
                headers=dict(response.headers),
            )
            self._cf_logger().warning(
                "Challenge CF détecté ({t}) sur {url} (tentative {n}/{m})",
                t=challenge_type,
                url=url,
                n=attempt,
                m=max_attempts,
            )

            stats = CloudflareMixin._cf_stats.increment(attempt_key)
            if stats > max_attempts:
                raise CloudflareBypassError(
                    f"Boucle de bypass détectée sur {url!r} "
                    f"({stats} tentatives cumulées)"
                )

            with contextlib.suppress(Exception):
                await self.cf_on_challenge_detected(response, url, challenge_type)

            # Invalide le cache : le clearance courant ne suffit pas.
            self.cf_clear_cache(domain)

            # Backoff exponentiel avant nouvelle tentative.
            if attempt > 1:
                await asyncio.sleep(self.cf_backoff_base ** (attempt - 1))

            try:
                await self.cf_ensure_clearance(url, force=True)
            except CloudflareBypassError as exc:
                with contextlib.suppress(Exception):
                    await self.cf_on_bypass_failure(url, exc)
                if attempt >= max_attempts:
                    raise
                continue

            with contextlib.suppress(Exception):
                await self.cf_on_bypass_success(url)

        # Toutes les tentatives ont échoué.
        snippet = (
            self._cf_safe_text(last_response)[:256] if last_response else "N/A"
        )
        raise CloudflareBypassError(
            f"Échec du contournement Cloudflare pour {url!r} après "
            f"{max_attempts} tentatives. Extrait : {snippet!r}"
        )

    async def cf_get(self, url: str, **kwargs: Any) -> httpx.Response:
        """GET HTTP avec bypass Cloudflare automatique.

        Args:
            url: URL cible.
            **kwargs: Arguments transmis à la session httpx.

        Returns:
            Réponse httpx.
        """
        return await self.cf_request("GET", url, **kwargs)

    async def cf_post(self, url: str, **kwargs: Any) -> httpx.Response:
        """POST HTTP avec bypass Cloudflare automatique.

        Args:
            url: URL cible.
            **kwargs: Arguments transmis à la session httpx.

        Returns:
            Réponse httpx.
        """
        return await self.cf_request("POST", url, **kwargs)

    async def cf_get_html(self, url: str, **kwargs: Any) -> str:
        """GET renvoyant directement le texte HTML (post-bypass).

        Args:
            url: URL cible.
            **kwargs: Arguments transmis à la session httpx.

        Returns:
            Contenu HTML.

        Raises:
            CloudflareBypassError: Si le contournement échoue.
        """
        response = await self.cf_get(url, **kwargs)
        return response.text

    async def cf_get_json(self, url: str, **kwargs: Any) -> Any:
        """GET renvoyant le JSON décodé (post-bypass).

        Args:
            url: URL cible.
            **kwargs: Arguments transmis à la session httpx.

        Returns:
            Objet Python désérialisé.

        Raises:
            CloudflareBypassError: Si le contournement échoue.
            ValueError: Si le corps n'est pas un JSON valide.
        """
        response = await self.cf_get(url, **kwargs)
        try:
            return response.json()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"Réponse non-JSON depuis {url!r} (status={response.status_code})"
            ) from exc

    async def cf_render(
        self,
        url: str,
        *,
        wait_for_selector: str | None = None,
        timeout: float | None = None,
        **render_kwargs: Any,
    ) -> Any:
        """Rend une page via Playwright en réutilisant le clearance courant.

        Nécessite que le parser hôte expose ``fetch_rendered`` (via
        :class:`JsRenderedMixin`). Cette méthode est un pont pratique pour
        les sites 100 % SPA protégés par CF.

        Args:
            url: URL cible.
            wait_for_selector: Sélecteur CSS à attendre.
            timeout: Timeout global.
            **render_kwargs: Arguments transmis à ``fetch_rendered``.

        Returns:
            Objet ``RenderedPage`` retourné par ``fetch_rendered``.

        Raises:
            CloudflareBypassError: Si le parser ne supporte pas le rendu.
        """
        fetch = getattr(self, "fetch_rendered", None)
        if not callable(fetch):
            raise CloudflareBypassError(
                f"Le parser {self.config.id!r} n'expose pas fetch_rendered "
                "(JsRenderedMixin requis)"
            )
        return await fetch(
            url,
            wait_for_selector=wait_for_selector,
            timeout=timeout or self.cf_challenge_timeout,
            bypass_cloudflare=True,
            **render_kwargs,
        )

    # ------------------------------------------------------------------
    # Hooks surchargeables
    # ------------------------------------------------------------------

    async def cf_on_challenge_detected(
        self,
        response: httpx.Response,
        url: str,
        challenge_type: CloudflareChallengeType,
    ) -> None:
        """Hook appelé lorsqu'un challenge est détecté (surchargeable).

        Args:
            response: Réponse bloquée.
            url: URL concernée.
            challenge_type: Type de challenge détecté.
        """

    async def cf_on_bypass_success(self, url: str) -> None:
        """Hook appelé après un bypass réussi (surchargeable).

        Args:
            url: URL pour laquelle le bypass a réussi.
        """

    async def cf_on_bypass_failure(
        self, url: str, error: NexusDLError
    ) -> None:
        """Hook appelé après un échec de bypass (surchargeable).

        Args:
            url: URL concernée.
            error: Exception remontée.
        """

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    async def cf_health_check(self, url: str | None = None) -> bool:
        """Vérifie la capacité du parser à contourner Cloudflare.

        Args:
            url: URL à tester (par défaut : première URL du domaine configuré).

        Returns:
            ``True`` si un clearance valide est obtenu.
        """
        if url is None:
            domains = getattr(self.config, "domains", None) or []
            if not domains:
                return False
            first = domains[0]
            url = str(first) if not isinstance(first, str) else first

        try:
            await self.cf_ensure_clearance(url, force=True)
        except CloudflareBypassError as exc:
            self._cf_logger().warning(
                "Health check Cloudflare KO : {err}", err=exc
            )
            return False
        return True

    def cf_debug_info(self) -> dict[str, Any]:
        """Retourne un dictionnaire de diagnostic du mixin.

        Returns:
            Informations sur le cache et les compteurs de tentatives.
        """
        cache = CloudflareMixin._cf_cache
        return {
            "enabled": self.cf_enabled,
            "preferred_backend": self.cf_preferred_backend,
            "playwright_available": getattr(self, "playwright_pool", None)
            is not None,
            "flaresolverr_available": getattr(self, "flaresolverr", None)
            is not None,
            "cached_domains": sorted(cache.keys()),
            "clearance_ttl": self.cf_clearance_ttl,
            "attempt_counters": dict(CloudflareMixin._cf_stats.attempts),
        }

    # ------------------------------------------------------------------
    # Helpers statiques
    # ------------------------------------------------------------------

    @staticmethod
    def _cf_safe_text(response: httpx.Response | None) -> str:
        """Retourne le texte d'une réponse en avalant les erreurs de décodage.

        Args:
            response: Réponse httpx ou ``None``.

        Returns:
            Texte décodé (chaîne vide si indisponible).
        """
        if response is None:
            return ""
        try:
            return response.text
        except Exception:  # noqa: BLE001
            try:
                return response.content.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                return ""

    @staticmethod
    def cf_fingerprint_url(url: str) -> str:
        """Calcule une empreinte stable d'une URL (pour les compteurs).

        Args:
            url: URL à hasher.

        Returns:
            Empreinte SHA-256 hexadécimale tronquée à 16 caractères.
        """
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def cf_clearance_cookie_ok(cookies: Mapping[str, str]) -> bool:
        """Vérifie qu'un mapping contient un ``cf_clearance`` exploitable.

        Args:
            cookies: Mapping de cookies.

        Returns:
            ``True`` si ``cf_clearance`` est présent et non vide.
        """
        return bool(cookies.get("cf_clearance"))
