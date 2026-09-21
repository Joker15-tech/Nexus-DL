"""Mixin de rendu JavaScript pour les parseurs NexusDL.

Ce module expose :class:`JsRenderedMixin`, une brique réutilisable qui ajoute
à un :class:`~nexusdl.parsers.base.BaseParser` la capacité de rendre des pages
JavaScript complexes via Playwright :

* Exécution complète du DOM/JS (SPA, React, Vue, Angular…).
* Contournement Cloudflare (attente du cookie ``cf_clearance``).
* Interception des requêtes XHR/fetch pour récupérer les charges utiles JSON
  des API internes (indispensable pour HentaiZone, MangaFire, Comick, etc.).
* Synchronisation bidirectionnelle des cookies Playwright ⇄ httpx, afin de
  réutiliser la session authentifiée/CSRF pour les appels HTTP directs.
* Blocage optionnel des ressources non critiques (images, fonts, CSS, trackers)
  pour accélérer le rendu.
* Capture d'écran automatique en cas d'échec (``debug_screenshots_dir``).

Contrat implicite du parser hôte
--------------------------------

Le mixin suppose que le parser expose :

* ``self.config: SiteConfig``
* ``self.session: HttpSession``
* ``self.playwright_pool: PlaywrightPool | None``
* ``self.logger`` (optionnel, sinon créé via :func:`get_logger`).

Le mixin **ne dépend jamais** de ``interfaces/`` et respecte la clean
architecture. Toutes les méthodes publiques sont async, typées et documentées
au format Google.

Example:
    Utilisation typique dans un parseur :

    >>> class HentaiZoneParser(JsRenderedMixin, BaseParser):
    ...     site_id = "hentaizone"
    ...     language = Language.FR
    ...
    ...     async def search(self, query: str, *, page: int = 1):
    ...         url = f"{self.config.domains[0]}/?s={quote_plus(query)}"
    ...         rendered = await self.fetch_rendered(
    ...             url, wait_for_selector=".manga-item"
    ...         )
    ...         return self._parse_search_html(rendered.html)
"""

from __future__ import annotations

import asyncio
import contextlib
import json as _json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Final, Literal, TypeAlias
from urllib.parse import quote_plus, urlparse

from playwright.async_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Response,
    Route,
    TimeoutError as PlaywrightTimeoutError,
)

from nexusdl.core.exceptions import (
    CloudflareBypassError,
    JsRenderingError,
    PageLoadTimeoutError,
)
from nexusdl.core.logger import get_logger
from nexusdl.core.models.site import SiteConfig
from nexusdl.core.session.http_session import HttpSession
from nexusdl.core.session.playwright_pool import PlaywrightPool

__all__ = [
    "JsRenderedMixin",
    "RenderedPage",
    "InterceptedResponse",
    "NetworkCapture",
    "WaitUntil",
]


# ---------------------------------------------------------------------------
# Types et constantes
# ---------------------------------------------------------------------------

WaitUntil: TypeAlias = Literal["commit", "domcontentloaded", "load", "networkidle"]

_BLOCKABLE_RESOURCE_TYPES: Final[frozenset[str]] = frozenset(
    {"image", "media", "font", "stylesheet"}
)

_TRACKER_URL_PATTERNS: Final[tuple[str, ...]] = (
    "google-analytics.com",
    "googletagmanager.com",
    "googlesyndication.com",
    "doubleclick.net",
    "connect.facebook.net",
    "hotjar.com",
    "sentry.io",
    "sentry-cdn.com",
    "clarity.ms",
    "yandex.ru/metrika",
    "matomo",
    "plausible.io",
    "segment.io",
    "mixpanel.com",
    "amplitude.com",
    "intercom.io",
    "crisp.chat",
    "tawk.to",
    "zendesk.com",
)

_CLOUDFLARE_COOKIE_NAMES: Final[frozenset[str]] = frozenset(
    {"cf_clearance", "__cf_bm", "cf_chl_2", "cf_chl_prog", "cf_chl_rc_ni"}
)

_CLOUDFLARE_CHALLENGE_MARKERS: Final[tuple[str, ...]] = (
    "just a moment",
    "checking your browser before accessing",
    "cf-browser-verification",
    "cf-challenge",
    "__cf_chl_",
    "attention required! | cloudflare",
    "enable javascript and cookies to continue",
)

_DEFAULT_DEBUG_DIR: Final[Path] = Path(".nexusdl") / "debug" / "playwright"


# ---------------------------------------------------------------------------
# Structures de données
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class RenderedPage:
    """Résultat d'un rendu JavaScript complet d'une page.

    Attributes:
        url: URL demandée initialement.
        final_url: URL finale après redirections éventuelles.
        html: Contenu HTML complet du DOM après exécution JS.
        status: Code de statut HTTP de la réponse principale.
        cookies: Cookies présents dans le contexte au moment du retour.
        headers: En-têtes de la réponse principale.
        elapsed_seconds: Durée totale du rendu.
        screenshot_path: Chemin de la capture d'écran (si demandée).
    """

    url: str
    final_url: str
    html: str
    status: int
    cookies: dict[str, str]
    headers: dict[str, str]
    elapsed_seconds: float
    screenshot_path: Path | None = None

    @property
    def ok(self) -> bool:
        """Indique si la réponse est un succès HTTP (2xx/3xx)."""
        return 200 <= self.status < 400

    @property
    def is_cloudflare_challenge(self) -> bool:
        """Heuristique : détecte une page de challenge Cloudflare non résolue."""
        haystack = self.html[:4096].lower()
        return any(marker in haystack for marker in _CLOUDFLARE_CHALLENGE_MARKERS)


@dataclass(slots=True, frozen=True)
class InterceptedResponse:
    """Réponse réseau interceptée (XHR/fetch/document).

    Attributes:
        url: URL de la requête.
        method: Méthode HTTP.
        status: Code HTTP.
        content_type: En-tête ``Content-Type``.
        body: Corps brut de la réponse.
        headers: En-têtes de réponse.
        request_headers: En-têtes de requête envoyés.
        resource_type: Type de ressource Playwright.
    """

    url: str
    method: str
    status: int
    content_type: str
    body: bytes
    headers: dict[str, str]
    request_headers: dict[str, str]
    resource_type: str

    def json(self, *, default: Any = None) -> Any:
        """Décode le corps comme JSON.

        Args:
            default: Valeur retournée si le décodage échoue.

        Returns:
            Objet Python désérialisé ou ``default``.
        """
        try:
            return _json.loads(self.body)
        except (ValueError, UnicodeDecodeError):
            if default is not None:
                return default
            raise

    def text(self, encoding: str = "utf-8") -> str:
        """Décode le corps en chaîne.

        Args:
            encoding: Encodage utilisé.

        Returns:
            Corps textuel.
        """
        return self.body.decode(encoding, errors="replace")


@dataclass(slots=True)
class NetworkCapture:
    """État mutable partagé pendant une session d'interception réseau.

    Attributes:
        responses: Réponses capturées correspondant au motif.
        matched_urls: URLs ayant matché.
        stop_event: Signal de fin pour le consommateur.
    """

    responses: list[InterceptedResponse] = field(default_factory=list)
    matched_urls: list[str] = field(default_factory=list)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)


# ---------------------------------------------------------------------------
# Mixin principal
# ---------------------------------------------------------------------------


class JsRenderedMixin:
    """Mixin à combiner avec :class:`~nexusdl.parsers.base.BaseParser`.

    Fournit des primitives haut-niveau pour piloter Chromium via le
    :class:`PlaywrightPool` partagé. Toutes les méthodes respectent les
    timeouts, gèrent l'annulation proprement et libèrent systématiquement
    leurs ressources.

    Class Attributes:
        js_rendered: Marqueur informatif — ``True``.
        default_wait_until: Stratégie d'attente par défaut.
        default_render_timeout: Timeout global par défaut (secondes).
        default_navigation_timeout: Timeout de navigation Playwright.
        default_post_load_delay: Délai additionnel post-navigation.
        cloudflare_max_wait: Durée maximale d'attente d'un challenge CF.
        cloudflare_poll_interval: Intervalle de poll pour les cookies CF.
        block_resources_by_default: Bloque images/fonts/CSS/trackers par défaut.
        debug_screenshots_dir: Dossier des captures d'échec.
        locale: Locale navigateur par défaut.
        timezone_id: Timezone navigateur par défaut.
        viewport_width: Largeur du viewport.
        viewport_height: Hauteur du viewport.
    """

    js_rendered: ClassVar[bool] = True
    default_wait_until: ClassVar[WaitUntil] = "domcontentloaded"
    default_render_timeout: ClassVar[float] = 30.0
    default_navigation_timeout: ClassVar[float] = 45.0
    default_post_load_delay: ClassVar[float] = 0.0
    cloudflare_max_wait: ClassVar[float] = 20.0
    cloudflare_poll_interval: ClassVar[float] = 0.5
    block_resources_by_default: ClassVar[bool] = True
    debug_screenshots_dir: ClassVar[Path] = _DEFAULT_DEBUG_DIR
    locale: ClassVar[str] = "fr-FR"
    timezone_id: ClassVar[str] = "Europe/Paris"
    viewport_width: ClassVar[int] = 1366
    viewport_height: ClassVar[int] = 900

    # Contrat attendu du parser hôte
    config: SiteConfig
    session: HttpSession
    playwright_pool: PlaywrightPool | None

    # ------------------------------------------------------------------
    # Setup / helpers internes
    # ------------------------------------------------------------------

    def _get_logger(self) -> Any:
        """Retourne un logger loguru contextualisé pour le parser.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin``.
        """
        existing = getattr(self, "logger", None)
        if existing is not None:
            return existing
        logger = get_logger(self.__class__.__module__)
        site_id = getattr(self.config, "id", "unknown")
        return logger.bind(site_id=site_id, mixin="js_rendered")

    def _require_pool(self) -> PlaywrightPool:
        """Vérifie la présence du pool Playwright.

        Returns:
            Le pool Playwright du parser.

        Raises:
            JsRenderingError: Si aucun pool n'est configuré.
        """
        pool = getattr(self, "playwright_pool", None)
        if pool is None:
            raise JsRenderingError(
                f"PlaywrightPool requis pour le parser {self.config.id!r} "
                "mais aucun n'a été fourni au constructeur."
            )
        return pool

    def _resolve_user_agent(self) -> str | None:
        """Récupère l'User-Agent à utiliser.

        Returns:
            Chaîne User-Agent ou ``None``.
        """
        ua = getattr(self.session, "user_agent", None)
        if ua:
            return str(ua)
        headers = getattr(self.config, "default_headers", None) or {}
        candidate = headers.get("User-Agent") or headers.get("user-agent")
        return str(candidate) if candidate else None

    def _resolve_proxy(self) -> str | None:
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
            except Exception:  # noqa: BLE001 — proxy best-effort
                return None
        return None

    def _origin(self, url: str) -> str:
        """Retourne l'origine (scheme + netloc) d'une URL.

        Args:
            url: URL absolue.

        Returns:
            Origine sous forme ``https://example.com``.
        """
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    # ------------------------------------------------------------------
    # Cookies : sync httpx ⇄ Playwright
    # ------------------------------------------------------------------

    async def _push_http_cookies(
        self,
        context: BrowserContext,
        url: str,
    ) -> int:
        """Injecte les cookies de la HttpSession dans le contexte Playwright.

        Args:
            context: Contexte navigateur cible.
            url: URL de référence pour le domaine.

        Returns:
            Nombre de cookies injectés.
        """
        cookies_map = getattr(self.session, "cookies", None)
        if not cookies_map:
            return 0
        origin = self._origin(url)
        pw_cookies = [
            {"name": name, "value": value, "url": origin}
            for name, value in dict(cookies_map).items()
        ]
        if not pw_cookies:
            return 0
        try:
            await context.add_cookies(pw_cookies)
        except (PlaywrightError, ValueError) as exc:
            self._get_logger().debug(
                "Échec d'injection de cookies dans Playwright : {err}", err=exc
            )
            return 0
        return len(pw_cookies)

    async def _pull_context_cookies(
        self,
        context: BrowserContext,
    ) -> dict[str, str]:
        """Lit les cookies du contexte Playwright.

        Args:
            context: Contexte navigateur source.

        Returns:
            Mapping ``name -> value``.
        """
        try:
            raw = await context.cookies()
        except PlaywrightError:
            return {}
        return {c["name"]: c["value"] for c in raw if c.get("name")}

    def _apply_cookies_to_session(self, cookies: dict[str, str]) -> None:
        """Applique un mapping de cookies à la HttpSession du parser.

        Args:
            cookies: Cookies à fusionner.
        """
        if not cookies:
            return
        target = getattr(self.session, "cookies", None)
        if isinstance(target, dict):
            target.update(cookies)
            return
        update = getattr(self.session, "update_cookies", None)
        if callable(update):
            try:
                update(cookies)
            except Exception as exc:  # noqa: BLE001 — best-effort
                self._get_logger().debug("Cookie update KO : {err}", err=exc)

    # ------------------------------------------------------------------
    # Routage / blocage de ressources
    # ------------------------------------------------------------------

    async def _default_route_handler(self, route: Route) -> None:
        """Handler par défaut : bloque trackers + ressources lourdes.

        Args:
            route: Route Playwright interceptée.
        """
        request = route.request
        resource_type = request.resource_type
        url = request.url.lower()

        if any(tracker in url for tracker in _TRACKER_URL_PATTERNS):
            await route.abort()
            return

        if resource_type in _BLOCKABLE_RESOURCE_TYPES:
            await route.abort()
            return

        await route.continue_()

    # ------------------------------------------------------------------
    # Primaire : fetch_rendered
    # ------------------------------------------------------------------

    async def fetch_rendered(
        self,
        url: str,
        *,
        wait_until: WaitUntil | None = None,
        wait_for_selector: str | None = None,
        wait_for_function: str | None = None,
        wait_for_url: str | None = None,
        timeout: float | None = None,
        navigation_timeout: float | None = None,
        post_load_delay: float | None = None,
        js_prelude: str | None = None,
        js_postlude: str | None = None,
        block_resources: bool | None = None,
        extra_headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        locale: str | None = None,
        screenshot: bool = False,
        screenshot_path: Path | None = None,
        bypass_cloudflare: bool = False,
    ) -> RenderedPage:
        """Charge une URL dans un navigateur et retourne le DOM rendu.

        Args:
            url: URL à charger.
            wait_until: Stratégie d'attente de navigation.
            wait_for_selector: Sélecteur CSS à attendre après navigation.
            wait_for_function: Expression JS à attendre (truthy).
            wait_for_url: Motif d'URL attendu après redirections.
            timeout: Timeout global (secondes).
            navigation_timeout: Timeout de navigation Playwright.
            post_load_delay: Délai additionnel post-navigation (SPAs).
            js_prelude: Script JS exécuté **avant** la navigation.
            js_postlude: Script JS exécuté **après** la navigation.
            block_resources: Force/annule le blocage des ressources.
            extra_headers: En-têtes HTTP additionnels.
            cookies: Cookies additionnels à injecter.
            locale: Locale navigateur à forcer.
            screenshot: Capture d'écran après rendu.
            screenshot_path: Chemin explicite de la capture.
            bypass_cloudflare: Attend la résolution d'un challenge CF.

        Returns:
            :class:`RenderedPage` avec HTML complet et métadonnées.

        Raises:
            JsRenderingError: Erreur Playwright générique.
            PageLoadTimeoutError: Timeout de navigation/attente.
            CloudflareBypassError: Challenge CF non résolu si demandé.
        """
        log = self._get_logger()
        pool = self._require_pool()
        timeout = timeout or self.default_render_timeout
        navigation_timeout = navigation_timeout or self.default_navigation_timeout
        wait_until = wait_until or self.default_wait_until
        post_load_delay = (
            post_load_delay
            if post_load_delay is not None
            else self.default_post_load_delay
        )
        block_resources = (
            block_resources
            if block_resources is not None
            else self.block_resources_by_default
        )
        locale = locale or self.locale

        started = time.monotonic()
        log.debug("fetch_rendered: {url} (wait_until={w})", url=url, w=wait_until)

        try:
            async with pool.acquire(
                user_agent=self._resolve_user_agent(),
                proxy=self._resolve_proxy(),
                locale=locale,
                timezone_id=self.timezone_id,
                viewport={
                    "width": self.viewport_width,
                    "height": self.viewport_height,
                },
            ) as context:
                await self._push_http_cookies(context, url)
                if cookies:
                    origin = self._origin(url)
                    await context.add_cookies(
                        [
                            {"name": k, "value": v, "url": origin}
                            for k, v in cookies.items()
                        ]
                    )

                if block_resources:
                    await context.route("**/*", self._default_route_handler)

                page = await context.new_page()
                page.set_default_navigation_timeout(navigation_timeout * 1000)
                page.set_default_timeout(timeout * 1000)

                if extra_headers:
                    await page.set_extra_http_headers(extra_headers)

                if js_prelude is None:
                    js_prelude = (
                        "Object.defineProperty(navigator, 'webdriver', "
                        "{get: () => undefined});"
                    )
                await page.add_init_script(js_prelude)

                response = await self._navigate(
                    page, url, wait_until=wait_until, timeout=navigation_timeout
                )

                if bypass_cloudflare:
                    await self._wait_cloudflare(page, context, url=url)

                await self._apply_waiters(
                    page,
                    wait_for_selector=wait_for_selector,
                    wait_for_function=wait_for_function,
                    wait_for_url=wait_for_url,
                    timeout=timeout,
                )

                if js_postlude:
                    await page.evaluate(js_postlude)

                if post_load_delay > 0:
                    await asyncio.sleep(post_load_delay)

                html = await page.content()
                final_url = page.url
                status = response.status if response is not None else 0
                headers = dict(response.headers) if response is not None else {}
                ctx_cookies = await self._pull_context_cookies(context)
                self._apply_cookies_to_session(ctx_cookies)

                shot_path: Path | None = None
                if screenshot or not (200 <= status < 400):
                    shot_path = await self._maybe_screenshot(
                        page, url, explicit_path=screenshot_path
                    )

                elapsed = time.monotonic() - started
                log.info(
                    "fetch_rendered OK: {url} status={s} en {d:.2f}s",
                    url=url,
                    s=status,
                    d=elapsed,
                )
                return RenderedPage(
                    url=url,
                    final_url=final_url,
                    html=html,
                    status=status,
                    cookies=ctx_cookies,
                    headers=headers,
                    elapsed_seconds=elapsed,
                    screenshot_path=shot_path,
                )

        except PlaywrightTimeoutError as exc:
            elapsed = time.monotonic() - started
            log.warning("Timeout rendu {url} après {d:.2f}s", url=url, d=elapsed)
            raise PageLoadTimeoutError(
                f"Timeout lors du rendu de {url!r} ({elapsed:.2f}s)"
            ) from exc
        except PlaywrightError as exc:
            log.error("Erreur Playwright sur {url}: {err}", url=url, err=exc)
            raise JsRenderingError(f"Erreur Playwright sur {url!r}: {exc}") from exc

    # ------------------------------------------------------------------
    # Sous-étapes de fetch_rendered
    # ------------------------------------------------------------------

    async def _navigate(
        self,
        page: Page,
        url: str,
        *,
        wait_until: WaitUntil,
        timeout: float,
    ) -> Response | None:
        """Effectue la navigation principale et retourne la réponse.

        Args:
            page: Page Playwright.
            url: URL cible.
            wait_until: Stratégie d'attente.
            timeout: Timeout en secondes.

        Returns:
            La réponse principale ou ``None`` si indisponible.
        """
        try:
            return await page.goto(
                url,
                wait_until=wait_until,
                timeout=timeout * 1000,
            )
        except PlaywrightTimeoutError:
            self._get_logger().debug(
                "Navigation timeout toléré sur {url}, tentative DOM",
                url=url,
            )
            with contextlib.suppress(Exception):
                return await page.reload(
                    wait_until="domcontentloaded", timeout=15000
                )
            return None

    async def _apply_waiters(
        self,
        page: Page,
        *,
        wait_for_selector: str | None,
        wait_for_function: str | None,
        wait_for_url: str | None,
        timeout: float,
    ) -> None:
        """Applique séquentiellement les conditions d'attente.

        Args:
            page: Page Playwright.
            wait_for_selector: Sélecteur à attendre.
            wait_for_function: Fonction JS à attendre.
            wait_for_url: Motif d'URL à attendre.
            timeout: Timeout global en secondes.

        Raises:
            PageLoadTimeoutError: Si une condition n'est jamais satisfaite.
        """
        try:
            async with asyncio.timeout(timeout):
                if wait_for_selector:
                    await page.wait_for_selector(wait_for_selector, state="attached")
                if wait_for_function:
                    await page.wait_for_function(wait_for_function)
                if wait_for_url:
                    await page.wait_for_url(wait_for_url)
        except TimeoutError as exc:
            raise PageLoadTimeoutError(
                f"Waiter non satisfait (selector={wait_for_selector!r}, "
                f"function={bool(wait_for_function)}, url={wait_for_url!r})"
            ) from exc

    async def _wait_cloudflare(
        self,
        page: Page,
        context: BrowserContext,
        *,
        url: str,
    ) -> None:
        """Attend la résolution d'un challenge Cloudflare.

        Args:
            page: Page Playwright.
            context: Contexte navigateur.
            url: URL d'origine.

        Raises:
            CloudflareBypassError: Challenge non résolu dans le délai.
        """
        log = self._get_logger()
        deadline = time.monotonic() + self.cloudflare_max_wait
        while time.monotonic() < deadline:
            cookies = await self._pull_context_cookies(context)
            if any(name in cookies for name in _CLOUDFLARE_COOKIE_NAMES):
                log.info("Challenge Cloudflare résolu sur {url}", url=url)
                return
            try:
                title = (await page.title()) or ""
            except PlaywrightError:
                title = ""
            if not any(m in title.lower() for m in _CLOUDFLARE_CHALLENGE_MARKERS):
                html = await page.content()
                if not any(
                    m in html[:4096].lower() for m in _CLOUDFLARE_CHALLENGE_MARKERS
                ):
                    return
            await asyncio.sleep(self.cloudflare_poll_interval)

        raise CloudflareBypassError(
            f"Challenge Cloudflare non résolu après {self.cloudflare_max_wait}s "
            f"sur {url!r}"
        )

    async def _maybe_screenshot(
        self,
        page: Page,
        url: str,
        *,
        explicit_path: Path | None,
    ) -> Path | None:
        """Sauvegarde une capture d'écran (best-effort).

        Args:
            page: Page Playwright.
            url: URL source.
            explicit_path: Chemin explicite si fourni.

        Returns:
            Chemin de la capture ou ``None`` si échec.
        """
        try:
            if explicit_path is None:
                slug = urlparse(url).netloc.replace(":", "_") or "page"
                stamp = time.strftime("%Y%m%d-%H%M%S")
                self.debug_screenshots_dir.mkdir(parents=True, exist_ok=True)
                explicit_path = self.debug_screenshots_dir / f"{slug}-{stamp}.png"
            else:
                explicit_path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(explicit_path), full_page=True)
            return explicit_path
        except (PlaywrightError, OSError) as exc:
            self._get_logger().debug("Screenshot KO : {err}", err=exc)
            return None

    # ------------------------------------------------------------------
    # Raccourcis haut-niveau
    # ------------------------------------------------------------------

    async def fetch_with_selector(
        self,
        url: str,
        selector: str,
        *,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> RenderedPage:
        """Raccourci : ``fetch_rendered`` avec attente de sélecteur.

        Args:
            url: URL à charger.
            selector: Sélecteur CSS à attendre.
            timeout: Timeout global.
            **kwargs: Arguments transmis à :meth:`fetch_rendered`.

        Returns:
            La page rendue.
        """
        return await self.fetch_rendered(
            url,
            wait_for_selector=selector,
            timeout=timeout,
            **kwargs,
        )

    async def fetch_with_network_idle(
        self,
        url: str,
        *,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> RenderedPage:
        """Raccourci : ``fetch_rendered`` avec attente ``networkidle``.

        Args:
            url: URL à charger.
            timeout: Timeout global.
            **kwargs: Arguments transmis à :meth:`fetch_rendered`.

        Returns:
            La page rendue.
        """
        return await self.fetch_rendered(
            url,
            wait_until="networkidle",
            timeout=timeout or 45.0,
            **kwargs,
        )

    async def evaluate_js(
        self,
        url: str,
        script: str,
        *,
        wait_for_selector: str | None = None,
        timeout: float | None = None,
        block_resources: bool | None = None,
    ) -> Any:
        """Charge une page puis exécute un script JS et retourne son résultat.

        Args:
            url: URL à charger.
            script: Script JS à évaluer.
            wait_for_selector: Sélecteur à attendre avant évaluation.
            timeout: Timeout global.
            block_resources: Force/annule le blocage des ressources.

        Returns:
            Résultat sérialisé de l'évaluation JS.
        """
        pool = self._require_pool()
        timeout = timeout or self.default_render_timeout
        block_resources = (
            block_resources
            if block_resources is not None
            else self.block_resources_by_default
        )
        async with pool.acquire(
            user_agent=self._resolve_user_agent(),
            proxy=self._resolve_proxy(),
            locale=self.locale,
            timezone_id=self.timezone_id,
        ) as context:
            await self._push_http_cookies(context, url)
            if block_resources:
                await context.route("**/*", self._default_route_handler)
            page = await context.new_page()
            page.set_default_navigation_timeout(timeout * 1000)
            try:
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=timeout * 1000
                )
                if wait_for_selector:
                    await page.wait_for_selector(wait_for_selector)
                result = await page.evaluate(script)
                self._apply_cookies_to_session(
                    await self._pull_context_cookies(context)
                )
                return result
            except PlaywrightTimeoutError as exc:
                raise PageLoadTimeoutError(
                    f"evaluate_js timeout sur {url!r}"
                ) from exc
            except PlaywrightError as exc:
                raise JsRenderingError(
                    f"evaluate_js échec sur {url!r}: {exc}"
                ) from exc

    # ------------------------------------------------------------------
    # Interception réseau (XHR / fetch / API JSON)
    # ------------------------------------------------------------------

    async def intercept_responses(
        self,
        url: str,
        url_pattern: str,
        *,
        method_filter: str | None = None,
        max_responses: int = 10,
        timeout: float | None = None,
        trigger: Callable[[Page], Awaitable[None]] | None = None,
        wait_until: WaitUntil = "domcontentloaded",
        extra_headers: dict[str, str] | None = None,
        block_resources: bool | None = None,
    ) -> list[InterceptedResponse]:
        """Navigue vers une URL et capture les réponses matchant un motif.

        Idéal pour extraire les charges JSON des API internes appelées par une
        SPA (MangaFire, Comick, HentaiZone…).

        Args:
            url: URL à charger.
            url_pattern: Sous-chaîne à matcher dans l'URL des requêtes.
            method_filter: Filtre méthode HTTP.
            max_responses: Nombre max de réponses à capturer.
            timeout: Timeout global de la session.
            trigger: Coroutine optionnelle déclenchée après navigation.
            wait_until: Stratégie d'attente de navigation.
            extra_headers: En-têtes additionnels.
            block_resources: Force/annule le blocage des ressources.

        Returns:
            Liste des :class:`InterceptedResponse` capturées.
        """
        pool = self._require_pool()
        timeout = timeout or self.default_render_timeout
        block_resources = (
            block_resources
            if block_resources is not None
            else self.block_resources_by_default
        )
        capture = NetworkCapture()
        method_filter_upper = method_filter.upper() if method_filter else None

        async with pool.acquire(
            user_agent=self._resolve_user_agent(),
            proxy=self._resolve_proxy(),
            locale=self.locale,
            timezone_id=self.timezone_id,
        ) as context:
            await self._push_http_cookies(context, url)
            if block_resources:
                await context.route("**/*", self._default_route_handler)

            page = await context.new_page()
            page.set_default_navigation_timeout(timeout * 1000)

            if extra_headers:
                await page.set_extra_http_headers(extra_headers)

            async def _on_response(response: Response) -> None:
                try:
                    if url_pattern not in response.url:
                        return
                    request = response.request
                    if (
                        method_filter_upper
                        and request.method.upper() != method_filter_upper
                    ):
                        return
                    body = await response.body()
                    headers = dict(response.headers)
                    capture.responses.append(
                        InterceptedResponse(
                            url=response.url,
                            method=request.method,
                            status=response.status,
                            content_type=headers.get("content-type", ""),
                            body=body,
                            headers=headers,
                            request_headers=dict(request.headers),
                            resource_type=request.resource_type,
                        )
                    )
                    capture.matched_urls.append(response.url)
                    if len(capture.responses) >= max_responses:
                        capture.stop_event.set()
                except (PlaywrightError, asyncio.CancelledError):
                    return

            page.on("response", lambda r: asyncio.create_task(_on_response(r)))

            try:
                await page.goto(
                    url, wait_until=wait_until, timeout=timeout * 1000
                )
                if trigger is not None:
                    await trigger(page)

                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(timeout):
                        await capture.stop_event.wait()
            except PlaywrightTimeoutError as exc:
                raise PageLoadTimeoutError(
                    f"intercept_responses timeout sur {url!r}"
                ) from exc
            except PlaywrightError as exc:
                raise JsRenderingError(
                    f"intercept_responses échec sur {url!r}: {exc}"
                ) from exc
            finally:
                self._apply_cookies_to_session(
                    await self._pull_context_cookies(context)
                )

        self._get_logger().debug(
            "intercept_responses: {n} réponse(s) capturée(s) sur {url}",
            n=len(capture.responses),
            url=url,
        )
        return capture.responses

    @asynccontextmanager
    async def intercept_stream(
        self,
        url: str,
        url_pattern: str,
        *,
        method_filter: str | None = None,
        timeout: float | None = None,
        wait_until: WaitUntil = "domcontentloaded",
        extra_headers: dict[str, str] | None = None,
        block_resources: bool | None = None,
    ) -> AsyncIterator[AsyncIterator[InterceptedResponse]]:
        """Version streaming de :meth:`intercept_responses`.

        Ouvre un contexte navigateur et yield un itérateur asynchrone qui
        produit les réponses matchant le motif au fur et à mesure.

        Args:
            url: URL à charger.
            url_pattern: Sous-chaîne à matcher dans l'URL.
            method_filter: Filtre méthode HTTP.
            timeout: Timeout global.
            wait_until: Stratégie d'attente.
            extra_headers: En-têtes additionnels.
            block_resources: Force/annule le blocage des ressources.

        Yields:
            Un itérateur asynchrone de :class:`InterceptedResponse`.
        """
        pool = self._require_pool()
        timeout = timeout or self.default_render_timeout
        block_resources = (
            block_resources
            if block_resources is not None
            else self.block_resources_by_default
        )
        queue: asyncio.Queue[InterceptedResponse | None] = asyncio.Queue()
        method_filter_upper = method_filter.upper() if method_filter else None

        async with pool.acquire(
            user_agent=self._resolve_user_agent(),
            proxy=self._resolve_proxy(),
            locale=self.locale,
            timezone_id=self.timezone_id,
        ) as context:
            await self._push_http_cookies(context, url)
            if block_resources:
                await context.route("**/*", self._default_route_handler)

            page = await context.new_page()
            page.set_default_navigation_timeout(timeout * 1000)
            if extra_headers:
                await page.set_extra_http_headers(extra_headers)

            async def _on_response(response: Response) -> None:
                try:
                    if url_pattern not in response.url:
                        return
                    request = response.request
                    if (
                        method_filter_upper
                        and request.method.upper() != method_filter_upper
                    ):
                        return
                    body = await response.body()
                    headers = dict(response.headers)
                    await queue.put(
                        InterceptedResponse(
                            url=response.url,
                            method=request.method,
                            status=response.status,
                            content_type=headers.get("content-type", ""),
                            body=body,
                            headers=headers,
                            request_headers=dict(request.headers),
                            resource_type=request.resource_type,
                        )
                    )
                except (PlaywrightError, asyncio.CancelledError):
                    return

            page.on("response", lambda r: asyncio.create_task(_on_response(r)))

            try:
                await page.goto(
                    url, wait_until=wait_until, timeout=timeout * 1000
                )
            except PlaywrightTimeoutError as exc:
                await queue.put(None)
                raise PageLoadTimeoutError(
                    f"intercept_stream timeout sur {url!r}"
                ) from exc

            async def _consumer() -> AsyncIterator[InterceptedResponse]:
                try:
                    while True:
                        item = await queue.get()
                        if item is None:
                            return
                        yield item
                finally:
                    pass

            try:
                yield _consumer()
            finally:
                await queue.put(None)
                self._apply_cookies_to_session(
                    await self._pull_context_cookies(context)
                )

    # ------------------------------------------------------------------
    # Helpers statiques exposés aux parseurs
    # ------------------------------------------------------------------

    @staticmethod
    def extract_script_json(
        html: str,
        script_id: str | None = None,
        *,
        script_substring: str | None = None,
    ) -> Any:
        """Extrait un objet JSON depuis une balise ``<script>`` du HTML.

        Args:
            html: HTML source.
            script_id: Attribut ``id`` de la balise ``<script>``.
            script_substring: Sous-chaîne distinctive du script (fallback).

        Returns:
            Objet Python désérialisé, ou ``None`` si introuvable.

        Raises:
            JsRenderingError: Si aucun marqueur n'est fourni.
        """
        from selectolax.parser import HTMLParser

        if not script_id and not script_substring:
            raise JsRenderingError(
                "extract_script_json: fournir 'script_id' ou 'script_substring'."
            )
        tree = HTMLParser(html)
        candidates = tree.css("script")
        for node in candidates:
            if script_id and node.attributes.get("id") != script_id:
                continue
            content = node.text() or ""
            if script_substring and script_substring not in content:
                continue
            payload = content.strip()
            if payload.startswith("window.") and "=" in payload:
                payload = payload.split("=", 1)[1].strip().rstrip(";")
            try:
                return _json.loads(payload)
            except ValueError:
                continue
        return None

    @staticmethod
    def build_search_url(base: str, query: str, *, param: str = "s") -> str:
        """Construit une URL de recherche standard.

        Args:
            base: URL de base du site (ex. ``https://example.com``).
            query: Terme de recherche.
            param: Nom du paramètre de requête.

        Returns:
            URL complète encodée.
        """
        joiner = "&" if "?" in base else "?"
        return f"{base.rstrip('/')}/{joiner}{param}={quote_plus(query)}"
