"""Parseur Reaper Scans pour NexusDL.

Reaper Scans (``https://reaperscans.com``) est un groupe de scanlation
anglophone de premier plan, spécialisé dans les **manhwa**, **manhua** et
**webtoons** coréens et chinois. Il diffuse également des light novels via
un sous-domaine dédié (``novels.reaperscans.com``).

Caractéristiques techniques
---------------------------

* **Moteur** : à l'origine WordPress Madara, le site a migré vers une
  application **Next.js** (React côté client, rendu hydraté côté serveur).
  Le parsing s'appuie donc sur :class:`JsRenderedMixin` en priorité, avec
  fallback HTML direct quand les pages sont pré-rendues.
* **Langue** : Anglais (``en``).
* **Protection** : Cloudflare (AS13335, IP ``188.114.96.3``,
  ``172.67.72.131``) avec challenge de niveau élevé. Les utilisateurs
  rapportent des erreurs 1020 fréquentes (firewall rule) et des captchas
  Cloudflare nécessitant une résolution manuelle.
* **Domaines** : ``reaperscans.com`` (principal, enregistré depuis 2020),
  ``reapercomics.com`` (ancien, redirige vers ``reaperscans.com``),
  ``reaperscans.fr`` (miroir français, non géré par ce parseur).
* **Sous-domaines** :
    - ``api.reaperscans.com`` — API REST interne (Next.js).
    - ``media.reaperscans.com`` — CDN images (Backblaze B2 proxifié).
    - ``novels.reaperscans.com`` — light novels (hors scope).
* **Structure des URLs** :
    - Catalogue : ``/comics``.
    - Manga : ``/comics/{id}-{slug}``.
    - Chapitre : ``/comics/{id}-{slug}/chapters/{id}-chapter-{num}``.
* **Junk pages** : Reaper Scans insère parfois des images manquantes (HTTP
  404) en fin de chapitre pour dissuader le scraping. Le parseur détecte et
  filtre ces pages via un test inversé en fin de chapitre.
* **Images** : servies depuis ``media.reaperscans.com`` (CDN Backblaze B2),
  dans des balises ``<source>`` avec ``type="image/avif"`` (le site sert
  du AVIF indépendamment de l'User-Agent).

Le parseur combine trois mixins :

1. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — rendu
   Playwright complet (site Next.js SPA).
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — bypass
   Cloudflare automatique (Playwright / FlareSolverr).
3. :class:`~nexusdl.parsers.mixins.api_based.ApiBasedMixin` — client REST
   pour l'API interne ``api.reaperscans.com`` (fallback pour les endpoints
   JSON quand disponibles).

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.en.reaperscans import ReaperScansParser
    >>>
    >>> parser = ReaperScansParser(config, session)
    >>> results = await parser.search("solo leveling")
    >>> manga = await parser.get_manga(results[0].url)
    >>> chapters = await parser.get_chapters(manga)
    >>> pages = await parser.get_pages(chapters[0])
"""

from __future__ import annotations

import json as _json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Final
from urllib.parse import quote_plus, urljoin, urlparse

from selectolax.parser import HTMLParser, Node

from nexusdl.core.exceptions import (
    ChapterNotFoundError,
    MangaNotFoundError,
    ParseError,
)
from nexusdl.core.logger import get_logger
from nexusdl.core.models.manga import (
    Chapter,
    ContentRating,
    Language,
    Manga,
    MangaStatus,
    Page,
    SearchResult,
)
from nexusdl.parsers.base import BaseParser
from nexusdl.parsers.mixins.api_based import ApiBasedMixin
from nexusdl.parsers.mixins.cloudflare import CloudflareMixin
from nexusdl.parsers.mixins.js_rendered import JsRenderedMixin

__all__ = ["ReaperScansParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://reaperscans.com"
_FALLBACK_DOMAINS: Final[tuple[str, ...]] = (
    "https://reaperscans.com",
    "https://reapercomics.com",
)
_SITE_ID: Final[str] = "reaperscans"
_LANGUAGE: Final[Language] = Language.EN
_ADULT: Final[bool] = False

# Regex de parsing des chapitres (format Reaper Scans : "Chapter 78").
_CHAPTER_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:chapter|ch\.?|episode|ep\.?)\s*(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_VOLUME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:volume|vol\.?)\s*(\d+)", re.IGNORECASE
)
_RELATIVE_DATE_RE: Final[re.Pattern[str]] = re.compile(
    r"(\d+)\s*(second|minute|hour|day|week|month|year)s?\s*(ago)?",
    re.IGNORECASE,
)

# Statuts → énumération NexusDL.
_STATUS_MAP: Final[dict[str, MangaStatus]] = {
    "ongoing": MangaStatus.ONGOING,
    "on going": MangaStatus.ONGOING,
    "completed": MangaStatus.COMPLETED,
    "complete": MangaStatus.COMPLETED,
    "end": MangaStatus.COMPLETED,
    "hiatus": MangaStatus.HIATUS,
    "paused": MangaStatus.HIATUS,
    "cancelled": MangaStatus.CANCELLED,
    "canceled": MangaStatus.CANCELLED,
    "dropped": MangaStatus.CANCELLED,
}

# Genres adultes (détection de classification).
_ADULT_GENRE_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "hentai",
        "adult",
        "ecchi",
        "smut",
        "mature",
        "18+",
        "porn",
        "erotic",
        "erotica",
        "yaoi",
        "yuri",
    }
)

# ---------------------------------------------------------------------------
# Sélecteurs Reaper Scans (Next.js/Tailwind rendu)
# ---------------------------------------------------------------------------

_RS_SEARCH_ITEM_SELECTOR: Final[str] = (
    "a.my-2.text-sm.font-medium.text-white, "
    "a[href*='/comics/'], "
    "div.grid a, "
    "li a[href*='/comics/']"
)
_RS_SEARCH_LINK_SELECTOR: Final[str] = "a"
_RS_SEARCH_COVER_SELECTOR: Final[str] = "img"
_RS_SEARCH_TITLE_SELECTOR: Final[str] = (
    ".text-sm.font-medium, .title, h3, h4, .font-medium"
)

_RS_MANGA_TITLE_SELECTOR: Final[str] = (
    "h1, h1.text-xl, .text-xl, .manga-title, div.overflow-hidden h1"
)
_RS_MANGA_COVER_SELECTOR: Final[str] = (
    "img.w-full, .cover img, .thumbnail img, "
    "main img, div.overflow-hidden img"
)
_RS_MANGA_DESCRIPTION_SELECTOR: Final[str] = (
    ".description, .summary, .manga-summary, "
    "p.text-sm, .prose p, div.mt-4 p"
)
_RS_MANGA_AUTHOR_SELECTOR: Final[str] = (
    ".author, .manga-author, a[href*='/author/'], "
    ".text-sm.font-medium"
)
_RS_MANGA_ARTIST_SELECTOR: Final[str] = (
    ".artist, .manga-artist, a[href*='/artist/']"
)
_RS_MANGA_GENRES_SELECTOR: Final[str] = (
    ".genres a, .manga-genres a, "
    "a[href*='/genre/'], a[href*='/tag/'], "
    "div.flex a.text-xs"
)
_RS_MANGA_STATUS_SELECTOR: Final[str] = (
    ".status, .manga-status, .text-sm"
)

_RS_CHAPTER_SELECTOR: Final[str] = (
    "div[wire\\:id] ul[role] li a, "
    "ul[role='list'] li a, "
    "div.chapters a, "
    "a[href*='/chapters/']"
)
_RS_PAGE_IMG_SELECTOR: Final[str] = (
    "main source.max-w-full, "
    "source.max-w-full.mx-auto.display-block, "
    "picture source, "
    ".reading-content img, "
    "main picture img"
)
_RS_PAGES_VAR_NAMES: Final[tuple[str, ...]] = (
    "pages",
    "page_urls",
    "pageUrls",
    "images",
    "chapterImages",
)


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


class ReaperScansParser(
    JsRenderedMixin,
    CloudflareMixin,
    ApiBasedMixin,
    BaseParser,
):
    """Parseur Reaper Scans (Next.js + Cloudflare + API).

    Combine le rendu Playwright (obligatoire — le site est une SPA Next.js),
    le contournement Cloudflare et un client API REST pour couvrir
    l'ensemble des cas d'usage.

    Attributes:
        site_id: Identifiant interne du site.
        language: Langue principale (anglais).
        adult: Contenu adulte (``False``).
        base_url: URL racine du site.
    """

    # --- Métadonnées du parser ---
    site_id: ClassVar[str] = _SITE_ID
    language: ClassVar[Language] = _LANGUAGE
    adult: ClassVar[bool] = _ADULT

    # --- URLs de base ---
    base_url: ClassVar[str] = _BASE_URL
    fallback_domains: ClassVar[tuple[str, ...]] = _FALLBACK_DOMAINS
    fools_base_url: ClassVar[str | None] = None

    # --- Chemins Reaper Scans ---
    rs_comics_path: ClassVar[str] = "/comics"
    rs_chapters_path: ClassVar[str] = "/chapters"

    # --- Sélecteurs Reaper Scans ---
    rs_search_item_selector: ClassVar[str] = _RS_SEARCH_ITEM_SELECTOR
    rs_search_link_selector: ClassVar[str] = _RS_SEARCH_LINK_SELECTOR
    rs_search_cover_selector: ClassVar[str] = _RS_SEARCH_COVER_SELECTOR
    rs_search_title_selector: ClassVar[str] = _RS_SEARCH_TITLE_SELECTOR

    rs_manga_title_selector: ClassVar[str] = _RS_MANGA_TITLE_SELECTOR
    rs_manga_cover_selector: ClassVar[str] = _RS_MANGA_COVER_SELECTOR
    rs_manga_description_selector: ClassVar[str] = _RS_MANGA_DESCRIPTION_SELECTOR
    rs_manga_author_selector: ClassVar[str] = _RS_MANGA_AUTHOR_SELECTOR
    rs_manga_artist_selector: ClassVar[str] = _RS_MANGA_ARTIST_SELECTOR
    rs_manga_genres_selector: ClassVar[str] = _RS_MANGA_GENRES_SELECTOR
    rs_manga_status_selector: ClassVar[str] = _RS_MANGA_STATUS_SELECTOR

    rs_chapter_selector: ClassVar[str] = _RS_CHAPTER_SELECTOR
    rs_page_img_selector: ClassVar[str] = _RS_PAGE_IMG_SELECTOR
    rs_pages_var_names: ClassVar[tuple[str, ...]] = _RS_PAGES_VAR_NAMES

    # --- Comportement ---
    rs_requires_js: ClassVar[bool] = True  # Site Next.js (SPA)
    rs_default_rating: ClassVar[ContentRating] = ContentRating.SAFE
    rs_junk_page_filter: ClassVar[bool] = True  # Filtre les images junk
    rs_junk_probe_depth: ClassVar[int] = 5  # Nombre de pages testées en fin

    # --- Configuration API ---
    api_base_url: ClassVar[str] = "https://api.reaperscans.com"
    api_default_headers: ClassVar[dict[str, str]] = {
        "Accept": "application/json",
        "X-Referer": _BASE_URL,
    }
    api_rate_limit_per_second: ClassVar[float] = 3.0
    api_rate_limit_burst: ClassVar[int] = 3
    api_timeout: ClassVar[float] = 20.0
    api_cache_enabled: ClassVar[bool] = True
    api_cache_ttl: ClassVar[int] = 300

    # --- Configuration Cloudflare ---
    cf_enabled: ClassVar[bool] = True
    cf_requires_js: ClassVar[bool] = True
    cf_preferred_backend: ClassVar[str] = "playwright"
    cf_use_playwright_fallback: ClassVar[bool] = True
    cf_use_flaresolverr_fallback: ClassVar[bool] = True
    cf_max_attempts: ClassVar[int] = 3
    cf_challenge_timeout: ClassVar[float] = 45.0
    cf_clearance_ttl: ClassVar[int] = 1800

    # --- Configuration JsRendered ---
    js_rendered: ClassVar[bool] = True
    default_wait_until: ClassVar[str] = "networkidle"  # Next.js : attendre le réseau
    default_render_timeout: ClassVar[float] = 45.0
    default_navigation_timeout: ClassVar[float] = 60.0
    block_resources_by_default: ClassVar[bool] = False  # Autoriser les images (SPA)
    locale: ClassVar[str] = "en-US"
    timezone_id: ClassVar[str] = "America/New_York"
    viewport_width: ClassVar[int] = 1366
    viewport_height: ClassVar[int] = 900

    # ------------------------------------------------------------------
    # Constructeur
    # ------------------------------------------------------------------

    def __init__(
        self,
        config: Any,
        session: Any,
        *,
        playwright_pool: Any | None = None,
        cookie_manager: Any | None = None,
        flaresolverr: Any | None = None,
    ) -> None:
        """Initialise le parseur Reaper Scans.

        Args:
            config: Configuration du site (:class:`SiteConfig`).
            session: Session HTTP async du projet (:class:`HttpSession`).
            playwright_pool: Pool Playwright partagé (requis — le site est
                une SPA Next.js).
            cookie_manager: Gestionnaire de cookies chiffrés (optionnel,
                pour la persistance du ``cf_clearance``).
            flaresolverr: Client FlareSolverr (optionnel, backend de bypass
                alternatif).
        """
        super().__init__(
            config=config,
            session=session,
            playwright_pool=playwright_pool,
        )
        self.cookie_manager = cookie_manager
        self.flaresolverr = flaresolverr
        self.logger = get_logger(f"{self.__class__.__module__}.{self.site_id}")

        # Injecte le header X-Referer requis par l'API et le CDN.
        self._inject_referer_header()

    def _inject_referer_header(self) -> None:
        """Injecte le header ``X-Referer`` requis par l'API Reaper Scans.

        L'API interne (``api.reaperscans.com``) et le CDN
        (``media.reaperscans.com``) exigent un header ``X-Referer`` ou
        ``Referer`` pointant vers ``https://reaperscans.com/``.
        """
        target = getattr(self.session, "headers", None)
        if isinstance(target, dict):
            target.setdefault("X-Referer", self.base_url)
            target.setdefault("Referer", self.base_url + "/")
            self.logger.debug("Header X-Referer injecté dans la session")
            return
        config_headers = getattr(self.config, "default_headers", None)
        if isinstance(config_headers, dict):
            config_headers.setdefault("X-Referer", self.base_url)
            config_headers.setdefault("Referer", self.base_url + "/")

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------

    def _rs_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="reaperscans"``.
        """
        return self.logger

    @staticmethod
    def _rs_clean(value: str | None) -> str:
        """Nettoie une chaîne (strip, espaces multiples, NBSP).

        Args:
            value: Chaîne à nettoyer.

        Returns:
            Chaîne nettoyée (chaîne vide si ``None``).
        """
        if not value:
            return ""
        normalized = value.replace("\xa0", " ").replace("\u200b", "")
        return " ".join(normalized.split()).strip()

    @staticmethod
    def _rs_text(node: Node | None) -> str:
        """Extrait et nettoie le texte d'un nœud.

        Args:
            node: Nœud selectolax ou ``None``.

        Returns:
            Texte nettoyé.
        """
        if node is None:
            return ""
        return ReaperScansParser._rs_clean(
            node.text(deep=True, separator=" ")
        )

    @staticmethod
    def _rs_attr(node: Node | None, name: str) -> str:
        """Lit un attribut HTML sur un nœud.

        Args:
            node: Nœud selectolax ou ``None``.
            name: Nom de l'attribut.

        Returns:
            Valeur de l'attribut (chaîne vide si absent).
        """
        if node is None:
            return ""
        return node.attributes.get(name) or ""

    def _rs_first(self, tree: HTMLParser, selector: str) -> Node | None:
        """Retourne le premier nœud matchant un sélecteur.

        Args:
            tree: Arbre HTML parsé.
            selector: Sélecteur CSS (multi-sélecteurs séparés par virgule).

        Returns:
            Premier :class:`Node` trouvé ou ``None``.
        """
        for candidate in (s.strip() for s in selector.split(",") if s.strip()):
            node = tree.css_first(candidate)
            if node is not None:
                return node
        return None

    def _rs_all(self, tree: HTMLParser, selector: str) -> list[Node]:
        """Retourne tous les nœuds matchant un sélecteur (multi-sélecteurs).

        Args:
            tree: Arbre HTML parsé.
            selector: Sélecteur CSS avec virgules.

        Returns:
            Liste dédupliquée de :class:`Node`.
        """
        seen: set[int] = set()
        out: list[Node] = []
        for candidate in (s.strip() for s in selector.split(",") if s.strip()):
            for node in tree.css(candidate):
                marker = id(node)
                if marker in seen:
                    continue
                seen.add(marker)
                out.append(node)
        return out

    def _rs_abs(self, url: str, base: str | None = None) -> str:
        """Convertit une URL relative en URL absolue.

        Args:
            url: URL relative ou absolue.
            base: Base alternative.

        Returns:
            URL absolue normalisée.
        """
        if not url:
            return ""
        if url.startswith(("http://", "https://")):
            return url
        if url.startswith("//"):
            return f"https:{url}"
        root = base or self.base_url
        return urljoin(root + "/", url.lstrip("/"))

    # ------------------------------------------------------------------
    # Parsing statut / date / numéro de chapitre
    # ------------------------------------------------------------------

    def _rs_parse_status(self, raw: str | None) -> MangaStatus:
        """Convertit un libellé de statut en énumération NexusDL.

        Args:
            raw: Libellé brut.

        Returns:
            Statut normalisé (``ONGOING`` par défaut).
        """
        if not raw:
            return MangaStatus.ONGOING
        key = self._rs_clean(raw).lower()
        for needle, status in _STATUS_MAP.items():
            if needle in key:
                return status
        return MangaStatus.ONGOING

    @staticmethod
    def _rs_parse_year(raw: str | None) -> int | None:
        """Extrait une année à 4 chiffres d'une chaîne.

        Args:
            raw: Chaîne contenant potentiellement une année.

        Returns:
            Année ou ``None``.
        """
        if not raw:
            return None
        match = re.search(r"\b(19|20)\d{2}\b", raw)
        return int(match.group(0)) if match else None

    @staticmethod
    def _rs_parse_relative_date(raw: str | None) -> datetime | None:
        """Parse une date relative type ``"3 days ago"``.

        Args:
            raw: Chaîne de date relative.

        Returns:
            Datetime UTC correspondant ou ``None``.
        """
        if not raw:
            return None
        match = _RELATIVE_DATE_RE.search(raw.lower())
        if not match:
            return None
        amount = int(match.group(1))
        unit = match.group(2).lower()
        now = datetime.now(timezone.utc)
        table: dict[str, timedelta] = {
            "second": timedelta(seconds=amount),
            "minute": timedelta(minutes=amount),
            "hour": timedelta(hours=amount),
            "day": timedelta(days=amount),
            "week": timedelta(weeks=amount),
            "month": timedelta(days=amount * 30),
            "year": timedelta(days=amount * 365),
        }
        return now - table.get(unit, timedelta(0))

    @staticmethod
    def _rs_chapter_number(text: str) -> float | str:
        """Extrait le numéro de chapitre d'un libellé.

        Args:
            text: Libellé (ex. ``"Chapter 78"``).

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = ReaperScansParser._rs_clean(text)
        match = _CHAPTER_NUMBER_RE.search(cleaned)
        if not match:
            return cleaned
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return cleaned

    @staticmethod
    def _rs_chapter_volume(text: str) -> int | None:
        """Extrait le numéro de tome d'un libellé.

        Args:
            text: Libellé contenant éventuellement ``Volume X``.

        Returns:
            Numéro de tome ou ``None``.
        """
        match = _VOLUME_RE.search(text or "")
        return int(match.group(1)) if match else None

    def _rs_detect_rating(self, genres: list[str]) -> ContentRating:
        """Détermine la classification de contenu depuis les genres.

        Args:
            genres: Liste de genres du manga.

        Returns:
            Classification détectée.
        """
        lowered = {g.lower() for g in genres}
        if lowered & _ADULT_GENRE_MARKERS:
            if {"hentai", "porn", "18+"} & lowered:
                return ContentRating.PORNOGRAPHIC
            return ContentRating.EROTICA
        return self.rs_default_rating

    # ------------------------------------------------------------------
    # Filtrage des junk pages
    # ------------------------------------------------------------------

    async def _rs_filter_junk_pages(
        self, pages: list[Page]
    ) -> list[Page]:
        """Filtre les images junk (404) insérées par Reaper Scans.

        Reaper Scans place délibérément des images manquantes (HTTP 404) en
        fin de chapitre pour dissuader le scraping. L'algorithme teste les
        pages depuis la fin et s'arrête à la première page valide.

        Args:
            pages: Liste de pages candidates.

        Returns:
            Liste de pages sans les junk en fin de chapitre.
        """
        if not self.rs_junk_page_filter or not pages:
            return pages

        probe_count = min(self.rs_junk_probe_depth, len(pages))
        first_junk_index: int | None = None

        for offset in range(1, probe_count + 1):
            idx = len(pages) - offset
            page = pages[idx]
            if await self._rs_probe_page(page):
                first_junk_index = idx + 1
                break

        if first_junk_index is not None and first_junk_index < len(pages):
            filtered = pages[:first_junk_index]
            removed = len(pages) - len(filtered)
            self.logger.info(
                "ReaperScans: {n} junk page(s) filtrée(s) en fin de chapitre",
                n=removed,
            )
            return filtered
        return pages

    async def _rs_probe_page(self, page: Page) -> bool:
        """Vérifie qu'une page est accessible (HTTP 200).

        Args:
            page: Page à tester.

        Returns:
            ``True`` si la page renvoie 200.
        """
        try:
            response = await self.session.head(str(page.url), timeout=8.0)
            return response.status_code == 200
        except Exception:  # noqa: BLE001 — best-effort
            return False

    # ------------------------------------------------------------------
    # Méthodes abstraites de BaseParser
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des mangas sur Reaper Scans.

        Reaper Scans expose une page catalogue ``/comics`` avec filtres
        côté client. Le parseur tente d'abord l'API REST interne, puis
        bascule sur le rendu Playwright et filtre côté client.

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-based, ignoré si API).

        Returns:
            Liste de :class:`SearchResult`.

        Raises:
            ParseError: Si le HTML de résultats est inexploitable.
        """
        self.logger.debug(
            "ReaperScans search: {query} (page {page})",
            query=query,
            page=page,
        )

        # Tentative 1 : API REST interne.
        try:
            api_results = await self._rs_search_via_api(query, page=page)
            if api_results:
                self.logger.info(
                    "ReaperScans search API: {n} résultat(s)",
                    n=len(api_results),
                )
                return api_results
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(
                "API search KO, fallback Playwright : {err}", err=exc
            )

        # Tentative 2 : rendu Playwright + filtrage client.
        url = f"{self.base_url}{self.rs_comics_path}"
        if page > 1:
            url = f"{url}?page={page}"

        try:
            rendered = await self.fetch_rendered(
                url,
                wait_until="networkidle",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec search ReaperScans pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        all_results = self._rs_parse_search_html(
            rendered.html, base_url=rendered.final_url or url
        )

        # Filtre côté client sur la requête.
        query_lower = query.lower().strip()
        filtered = [
            r for r in all_results
            if query_lower in r.title.lower()
        ]

        self.logger.info(
            "ReaperScans search: {n} résultat(s) pour {query!r}",
            n=len(filtered),
            query=query,
        )
        return filtered

    async def _rs_search_via_api(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche via l'API REST interne de Reaper Scans.

        Args:
            query: Terme de recherche.
            page: Numéro de page.

        Returns:
            Liste de :class:`SearchResult` (vide si l'API ne répond pas).
        """
        # Endpoint supposé (à ajuster selon l'API réelle).
        endpoint = "/comics/search"
        try:
            payload = await self.api_get(
                endpoint,
                params={"q": query, "page": page},
                timeout=15.0,
            )
        except Exception:
            return []

        results: list[SearchResult] = []
        items = payload if isinstance(payload, list) else payload.get("data", [])
        for item in items:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("name")
            url = item.get("url") or item.get("slug")
            if not title or not url:
                continue
            if not url.startswith("http"):
                url = f"{self.base_url}{self.rs_comics_path}/{url}"
            results.append(
                SearchResult(
                    title=str(title),
                    url=url,
                    site_id=self.config.id,
                    cover_url=item.get("cover") or item.get("thumbnail"),
                    author=item.get("author"),
                )
            )
        return results

    def _rs_parse_search_html(
        self,
        html: str,
        *,
        base_url: str,
    ) -> list[SearchResult]:
        """Parse le HTML de la page catalogue.

        Args:
            html: HTML rendu.
            base_url: URL de la page (pour résolution relative).

        Returns:
            Liste de :class:`SearchResult`.
        """
        tree = HTMLParser(html)
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for node in self._rs_all(tree, self.rs_search_item_selector):
            link_node: Node | None = None
            if node.tag == "a" and "/comics/" in self._rs_attr(node, "href"):
                link_node = node
            else:
                for candidate in node.css("a"):
                    if "/comics/" in self._rs_attr(candidate, "href"):
                        link_node = candidate
                        break

            if link_node is None:
                continue

            href = self._rs_attr(link_node, "href")
            if not href:
                continue
            abs_url = self._rs_abs(href, base_url)
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            title_node = node.css_first(self.rs_search_title_selector)
            title = self._rs_text(title_node) or self._rs_attr(
                link_node, "title"
            )
            if not title:
                title = self._rs_attr(link_node, "href").rstrip("/").rsplit("/", 1)[-1]
                title = title.replace("-", " ").title()
            if not title:
                continue

            cover_node = node.css_first(self.rs_search_cover_selector)
            cover_src = (
                self._rs_attr(cover_node, "data-src")
                or self._rs_attr(cover_node, "data-lazy-src")
                or self._rs_attr(cover_node, "src")
            )
            cover_url = self._rs_abs(cover_src, base_url) if cover_src else None

            results.append(
                SearchResult(
                    title=title,
                    url=abs_url,
                    site_id=self.config.id,
                    cover_url=cover_url,
                    author=None,
                )
            )

        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga Reaper Scans.

        Args:
            url_or_id: URL absolue ou slug de la série.

        Returns:
            Objet :class:`Manga` hydraté.

        Raises:
            MangaNotFoundError: Si le manga est inaccessible.
            ParseError: Si le HTML est inexploitable.
        """
        if url_or_id.startswith(("http://", "https://")):
            url = url_or_id
        elif url_or_id.startswith("/"):
            url = f"{self.base_url}{url_or_id}"
        else:
            url = f"{self.base_url}{self.rs_comics_path}/{url_or_id.strip('/')}"

        self.logger.debug("ReaperScans get_manga: {url}", url=url)

        try:
            rendered = await self.fetch_rendered(
                url,
                wait_until="networkidle",
                wait_for_selector="h1, .text-xl, .manga-title",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_manga ReaperScans pour {url!r}: {err}",
                url=url,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {url!r}: {exc}"
            ) from exc

        html = rendered.html
        tree = HTMLParser(html)

        title_node = self._rs_first(tree, self.rs_manga_title_selector)
        title = self._rs_text(title_node)
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        cover_node = self._rs_first(tree, self.rs_manga_cover_selector)
        cover_src = (
            self._rs_attr(cover_node, "data-src")
            or self._rs_attr(cover_node, "data-lazy-src")
            or self._rs_attr(cover_node, "src")
        )
        cover_url = self._rs_abs(cover_src, url) if cover_src else None

        description_node = self._rs_first(
            tree, self.rs_manga_description_selector
        )
        description = self._rs_text(description_node) or None

        author_node = self._rs_first(tree, self.rs_manga_author_selector)
        author = self._rs_text(author_node) or None

        artist_node = self._rs_first(tree, self.rs_manga_artist_selector)
        artist = self._rs_text(artist_node) or None

        genre_nodes = self._rs_all(tree, self.rs_manga_genres_selector)
        genres: list[str] = []
        for node in genre_nodes:
            text = self._rs_text(node)
            if text and text not in genres:
                genres.append(text)

        status_node = self._rs_first(tree, self.rs_manga_status_selector)
        status = self._rs_parse_status(self._rs_text(status_node))

        source_id = self._rs_extract_series_slug(url)
        rating = self._rs_detect_rating(genres)

        chapters = self._rs_parse_chapters_from_html(html, base_url=url)

        manga = Manga(
            id=f"{self.config.id}:{source_id}",
            source_id=source_id,
            site=self.config.id,
            title=title,
            alternative_titles=[],
            description=description,
            author=author,
            artist=artist,
            genres=genres,
            status=status,
            year=None,
            cover_url=cover_url,
            language=self.language,
            content_rating=rating,
            chapters=chapters,
            url=url,
            updated_at=datetime.now(timezone.utc),
        )
        self.logger.info(
            "ReaperScans get_manga OK: {title} ({n} chapitres)",
            title=title,
            n=len(chapters),
        )
        return manga

    @staticmethod
    def _rs_extract_series_slug(url: str) -> str:
        """Extrait un identifiant de série depuis une URL Reaper Scans.

        Format : ``/comics/{id}-{slug}`` → retourne ``{id}-{slug}``.

        Args:
            url: URL de la série.

        Returns:
            Identifiant composite ``{id}-{slug}`` ou slug seul.
        """
        parts = [p for p in urlparse(url).path.split("/") if p]
        if "comics" in parts:
            idx = parts.index("comics")
            remaining = parts[idx + 1:]
            if remaining:
                return remaining[0]
        if parts:
            return parts[-1]
        return url

    async def get_chapters(self, manga: Manga) -> list[Chapter]:
        """Récupère la liste des chapitres d'un manga.

        Args:
            manga: Manga dont on veut les chapitres.

        Returns:
            Liste de :class:`Chapter` triés par numéro croissant.

        Raises:
            ChapterNotFoundError: Si aucun chapitre n'est trouvé.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "ReaperScans get_chapters: {title}", title=manga.title
        )

        try:
            rendered = await self.fetch_rendered(
                str(manga.url),
                wait_until="networkidle",
                wait_for_selector="a[href*='/chapters/'], ul[role] li a",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_chapters ReaperScans pour {title!r}: {err}",
                title=manga.title,
                err=exc,
            )
            raise ParseError(
                f"get_chapters échoué sur {self.site_id!r} "
                f"pour {manga.title!r}: {exc}"
            ) from exc

        chapters = self._rs_parse_chapters_from_html(
            rendered.html, base_url=rendered.final_url or str(manga.url)
        )
        if not chapters:
            raise ChapterNotFoundError(
                f"Aucun chapitre trouvé pour {manga.url} sur {self.site_id!r}"
            )
        return chapters

    def _rs_parse_chapters_from_html(
        self,
        html: str,
        *,
        base_url: str,
    ) -> list[Chapter]:
        """Parse la liste des chapitres depuis le HTML d'une fiche manga.

        Args:
            html: HTML rendu.
            base_url: URL de la page manga.

        Returns:
            Liste de :class:`Chapter` triés.
        """
        tree = HTMLParser(html)
        seen: set[str] = set()
        chapters: list[Chapter] = []
        language = self.language

        for node in self._rs_all(tree, self.rs_chapter_selector):
            href = self._rs_attr(node, "href")
            if not href or "/chapters/" not in href:
                continue
            abs_url = self._rs_abs(href, base_url)
            if abs_url in seen:
                continue
            seen.add(abs_url)

            label = self._rs_text(node) or self._rs_attr(node, "title")
            if not label:
                label = abs_url.rstrip("/").rsplit("/", 1)[-1] or abs_url

            number = self._rs_chapter_number(label)
            volume = self._rs_chapter_volume(label)

            date_text = self._rs_attr(node, "data-date") or None
            published = (
                self._rs_parse_relative_date(date_text)
                if date_text
                else self._rs_parse_relative_date(label)
            )

            source_id = abs_url.rstrip("/").rsplit("/", 1)[-1] or label
            chapters.append(
                Chapter(
                    id=f"{self.config.id}:{source_id}",
                    source_id=source_id,
                    title=label,
                    number=number,
                    volume=volume,
                    language=language,
                    pages_count=None,
                    published_at=published,
                    url=abs_url,
                    pages=[],
                )
            )

        def _sort_key(ch: Chapter) -> tuple[int, float, str]:
            num = (
                ch.number
                if isinstance(ch.number, (int, float))
                else 0.0
            )
            return (
                int(isinstance(ch.number, str)),
                float(num),
                ch.title.lower(),
            )

        chapters.sort(key=_sort_key)
        self.logger.debug(
            "ReaperScans chapitres: {n}", n=len(chapters)
        )
        return chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre Reaper Scans.

        Le site est une SPA Next.js : le rendu Playwright est nécessaire.
        Les images sont dans des balises ``<source>`` (format AVIF) et
        ``<img>`` (fallback).

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page` (junk pages filtrées).

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "ReaperScans get_pages: {url}", url=str(chapter.url)
        )
        chapter_url = str(chapter.url)

        try:
            rendered = await self.fetch_rendered(
                chapter_url,
                wait_until="networkidle",
                wait_for_selector="main source, main picture img, source.max-w-full",
                timeout=45.0,
                bypass_cloudflare=True,
                block_resources=False,  # Les images sont nécessaires
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_pages ReaperScans pour {url!r}: {err}",
                url=chapter_url,
                err=exc,
            )
            raise ParseError(
                f"get_pages échoué sur {self.site_id!r} "
                f"pour {chapter_url!r}: {exc}"
            ) from exc

        html = rendered.html
        tree = HTMLParser(html)
        urls: list[str] = []

        # Priorité 1 : balises <source> (format AVIF servi par Reaper Scans).
        for source in tree.css("main source, source.max-w-full, picture source"):
            srcset = self._rs_attr(source, "srcset") or self._rs_attr(
                source, "src"
            )
            if not srcset:
                continue
            # Prend la première URL du srcset.
            first_url = srcset.split(",")[0].strip().split(" ")[0]
            if first_url and first_url not in urls:
                urls.append(self._rs_abs(first_url, chapter_url))

        # Priorité 2 : balises <img> dans <picture> ou sélecteur fallback.
        if not urls:
            for node in self._rs_all(tree, self.rs_page_img_selector):
                src = (
                    self._rs_attr(node, "data-src")
                    or self._rs_attr(node, "data-lazy-src")
                    or self._rs_attr(node, "data-original")
                    or self._rs_attr(node, "src")
                )
                if not src:
                    continue
                abs_url = self._rs_abs(src, chapter_url)
                if abs_url not in urls:
                    urls.append(abs_url)

        # Priorité 3 : variable JS embarquée.
        if not urls:
            for var_name in self.rs_pages_var_names:
                pattern = re.compile(
                    rf"(?:var|let|const)\s+{re.escape(var_name)}\s*=\s*"
                    rf"(\[.*?\])\s*;",
                    re.DOTALL,
                )
                for match in pattern.finditer(html):
                    raw = match.group(1)
                    try:
                        parsed = _json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(parsed, list):
                        for entry in parsed:
                            if isinstance(entry, str):
                                urls.append(entry)
                            elif isinstance(entry, dict):
                                for key in ("url", "src", "image"):
                                    val = entry.get(key)
                                    if isinstance(val, str):
                                        urls.append(val)
                                        break
                    if urls:
                        break
                if urls:
                    break

        if not urls:
            raise ChapterNotFoundError(
                f"Aucune page trouvée pour {chapter_url} sur "
                f"{self.site_id!r}"
            )

        pages: list[Page] = []
        for idx, url in enumerate(urls):
            clean = url.strip()
            if not clean:
                continue
            clean = self._rs_abs(clean, chapter_url)
            filename = (
                clean.rsplit("/", 1)[-1].split("?", 1)[0]
                or f"page_{idx:04d}.jpg"
            )
            if "." not in filename:
                filename = f"{filename}.jpg"
            pages.append(
                Page(
                    index=idx + 1,
                    url=clean,
                    filename=filename,
                    checksum=None,
                )
            )

        # Filtre les junk pages (404) en fin de chapitre.
        pages = await self._rs_filter_junk_pages(pages)
        return pages

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que Reaper Scans est accessible.

        Teste le domaine principal puis les domaines de fallback.

        Returns:
            ``True`` si un domaine répond correctement.
        """
        for base in self.fallback_domains:
            try:
                rendered = await self.fetch_rendered(
                    f"{base}{self.rs_comics_path}",
                    wait_until="networkidle",
                    timeout=30.0,
                    bypass_cloudflare=True,
                    screenshot=False,
                )
                if rendered.ok:
                    self.logger.info(
                        "Health check ReaperScans OK: {base} (status={s})",
                        base=base,
                        s=rendered.status,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Health check ReaperScans échec sur {base}: {err}",
                    base=base,
                    err=exc,
                )
        self.logger.warning("Health check ReaperScans KO (tous domaines)")
        return False
