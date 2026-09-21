"""Parseur Galaxy Scans (GD Scans) pour NexusDL.

Galaxy Degen Scans (``https://gdscans.com``), communément appelé **GD Scans**,
est un groupe de scanlation anglophone de premier plan, actif depuis 2020 et
spécialisé dans les mangas japonais, manhwas coréens et manhuas chinois. Le
groupe a traduit plus de 2 800 chapitres sur plus de 200 séries.

Caractéristiques techniques
---------------------------

* **Moteur** : WordPress Madara (thème premium Mangabooth), le même moteur
  que Toonily, MangaBuddy, etc.
* **Langue** : Anglais (``en``).
* **Protection** : Cloudflare (AS13335, IP ``104.21.16.34`` /
  ``172.67.182.191``). Le site utilise également un **système de paywall**
  (chapitres premium) signalé par la communauté — les chapitres récents
  peuvent être verrouillés.
* **Domaines** : ``gdscans.com`` (principal actuel), ``gdstmp.site``
  (ancien domaine temporaire).
* **Structure des URLs** (format Madara standard) :
    - Recherche : ``/?s={query}&post_type=wp-manga``.
    - Manga : ``/manga/{slug}/`` ou ``/series/{slug}/``.
    - Chapitre : ``/manga/{slug}/{chapter-slug}/``.
* **Sélecteurs Madara** : ``.page-item-detail.manga``,
  ``.listing-chapters_wrap ul.main.version-chap li``,
  ``.reading-content .page-break img``.
* **Objet JS ``ts_reader``** : le thème Madara de GD Scans expose également
  les pages dans un objet JavaScript ``ts_reader`` (utilisé par de nombreux
  thèmes modernes), ce qui fournit une seconde source d'extraction fiable.
* **Contenu adulte** : certains titres sont classés « mature » ou « ecchi »
  (le groupe traduit des séries comme *Kichiku Eiyuu*, *Silent Miyashita-san's
  Sexy Channel*) — la classification est détectée automatiquement.

Le parseur combine trois mixins :

1. :class:`~nexusdl.parsers.mixins.madara.MadaraMixin` — implémentations
   par défaut pour le thème WordPress Madara.
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — pour le
   contournement Cloudflare automatique (Playwright / FlareSolverr).
3. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — pour le
   rendu Playwright si nécessaire (fallback pour les pages dynamiques).

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.en.galaxy_scans import GalaxyScansParser
    >>>
    >>> parser = GalaxyScansParser(config, session)
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
from nexusdl.parsers.mixins.cloudflare import CloudflareMixin
from nexusdl.parsers.mixins.js_rendered import JsRenderedMixin
from nexusdl.parsers.mixins.madara import MadaraMixin

__all__ = ["GalaxyScansParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://gdscans.com"
_FALLBACK_DOMAINS: Final[tuple[str, ...]] = (
    "https://gdscans.com",
    "https://gdstmp.site",
)
_SITE_ID: Final[str] = "galaxy_scans"
_LANGUAGE: Final[Language] = Language.EN
_ADULT: Final[bool] = False  # Contenu mature présent mais pas 18+ permanent

# Regex de parsing des chapitres (format Madara : "Chapter 40.1").
_CHAPTER_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:chapter|ch\.?|episode|ep\.?|ep)\s*(\d+(?:[.,]\d+)?)",
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
    "axed": MangaStatus.CANCELLED,
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
        "gender bender",
        "harem",
    }
)

# Marqueurs de contenu suggestif (pas explicite).
_SUGGESTIVE_MARKERS: Final[frozenset[str]] = frozenset(
    {"ecchi", "mature", "harem", "suggestive", "romance"}
)

# ---------------------------------------------------------------------------
# Sélecteurs Madara spécifiques à GD Scans
# ---------------------------------------------------------------------------

_GD_SEARCH_ITEM_SELECTOR: Final[str] = (
    ".page-item-detail.manga, .page-item-detail, "
    ".c-tabs-item__content, .bs, .bsx, "
    ".manga-item, .item-summary"
)
_GD_CHAPTER_SELECTOR: Final[str] = (
    "div.listing-chapters_wrap > ul li, "
    ".listing-chapters_wrap li, "
    "ul.main.version-chap li, "
    ".eplister li:not(.ch-price-side), "
    ".wp-manga-chapter"
)
_GD_PAGE_IMG_SELECTOR: Final[str] = (
    "div.reading-content div.page-break.no-gaps img, "
    ".reading-content img, "
    ".page-break img, "
    "#readerarea img, "
    ".main-reading-area img"
)
_GD_PAGES_VAR_NAMES: Final[tuple[str, ...]] = (
    "ts_reader",
    "pages",
    "page_urls",
    "pageUrls",
    "images",
    "chapterImages",
)


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


class GalaxyScansParser(
    JsRenderedMixin,
    CloudflareMixin,
    MadaraMixin,
    BaseParser,
):
    """Parseur Galaxy Scans / GD Scans (Madara + Cloudflare).

    Combine le rendu Playwright (fallback), le contournement Cloudflare et
    les implémentations Madara pour fournir une couverture complète du site.

    Attributes:
        site_id: Identifiant interne du site.
        language: Langue principale (anglais).
        adult: Contenu adulte (``False`` — contenu mature ponctuel).
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

    # --- Configuration Madara ---
    madara_base_url: ClassVar[str] = _BASE_URL
    madara_search_path: ClassVar[str] = "/"
    madara_search_query_param: ClassVar[str] = "s"
    madara_search_method: ClassVar[str] = "GET"
    madara_series_path: ClassVar[str] = "/manga"
    madara_read_path: ClassVar[str] = "/manga"

    # --- Sélecteurs Madara (standards + ajustements GD Scans) ---
    madara_search_item_selector: ClassVar[str] = _GD_SEARCH_ITEM_SELECTOR
    madara_search_link_selector: ClassVar[str] = (
        ".item-summary .post-title a, .post-title a, a"
    )
    madara_search_cover_selector: ClassVar[str] = "img"
    madara_search_title_selector: ClassVar[str] = (
        ".item-summary .post-title, .post-title, h3, h4"
    )
    madara_search_author_selector: ClassVar[str] = ".author, .artist"

    madara_manga_title_selector: ClassVar[str] = (
        "h1, .entry-title, .post-title, .manga-title"
    )
    madara_manga_cover_selector: ClassVar[str] = (
        ".summary_image img, .thumb img, .manga-cover img, .cover img"
    )
    madara_manga_description_selector: ClassVar[str] = (
        ".summary__content, .description, .manga-summary, "
        ".entry-content p, .c-content"
    )
    madara_manga_author_selector: ClassVar[str] = (
        ".author, .manga-author, a[href*='/author/'], "
        ".post-content_item .author-content a"
    )
    madara_manga_artist_selector: ClassVar[str] = (
        ".artist, .manga-artist, a[href*='/artist/'], "
        ".post-content_item .artist-content a"
    )
    madara_manga_genres_selector: ClassVar[str] = (
        ".genres a, .manga-genres a, a[href*='/genre/'], "
        "a[href*='/tag/'], .wp-manga-tags-list a"
    )
    madara_manga_status_selector: ClassVar[str] = (
        ".status, .manga-status, .post-status, "
        ".post-content_item .summary-content"
    )
    madara_manga_year_selector: ClassVar[str] = (
        ".year, .manga-year, .post-year, "
        ".post-content_item .summary-content"
    )

    madara_chapter_selector: ClassVar[str] = _GD_CHAPTER_SELECTOR
    madara_page_img_selector: ClassVar[str] = _GD_PAGE_IMG_SELECTOR
    madara_pages_var_names: ClassVar[tuple[str, ...]] = _GD_PAGES_VAR_NAMES

    # --- Comportement Madara ---
    madara_requires_js: ClassVar[bool] = False
    madara_api_pages_endpoint: ClassVar[str | None] = None
    madara_default_rating: ClassVar[ContentRating] = ContentRating.SUGGESTIVE
    madara_concurrent_chapter_requests: ClassVar[int] = 4
    madara_search_pages_limit: ClassVar[int] = 20

    # --- Configuration Cloudflare ---
    cf_enabled: ClassVar[bool] = True
    cf_requires_js: ClassVar[bool] = False
    cf_preferred_backend: ClassVar[str] = "playwright"
    cf_use_playwright_fallback: ClassVar[bool] = True
    cf_use_flaresolverr_fallback: ClassVar[bool] = True
    cf_max_attempts: ClassVar[int] = 3
    cf_challenge_timeout: ClassVar[float] = 45.0
    cf_clearance_ttl: ClassVar[int] = 1800

    # --- Configuration JsRendered ---
    js_rendered: ClassVar[bool] = True
    default_wait_until: ClassVar[str] = "networkidle"
    default_render_timeout: ClassVar[float] = 45.0
    default_navigation_timeout: ClassVar[float] = 60.0
    block_resources_by_default: ClassVar[bool] = False
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
        """Initialise le parseur Galaxy Scans.

        Args:
            config: Configuration du site (:class:`SiteConfig`).
            session: Session HTTP async du projet (:class:`HttpSession`).
            playwright_pool: Pool Playwright partagé (requis — Cloudflare).
            cookie_manager: Gestionnaire de cookies chiffrés (optionnel).
            flaresolverr: Client FlareSolverr (optionnel).
        """
        super().__init__(
            config=config,
            session=session,
            playwright_pool=playwright_pool,
        )
        self.cookie_manager = cookie_manager
        self.flaresolverr = flaresolverr
        self.logger = get_logger(f"{self.__class__.__module__}.{self.site_id}")

        # Injecte le header Referer requis pour le CDN d'images.
        self._inject_cdn_referer()

    def _inject_cdn_referer(self) -> None:
        """Injecte le header ``Referer`` requis pour le CDN d'images."""
        target = getattr(self.session, "headers", None)
        if isinstance(target, dict):
            target.setdefault("Referer", f"{self.base_url}/")
            self.logger.debug("Header Referer CDN injecté dans la session")
            return
        config_headers = getattr(self.config, "default_headers", None)
        if isinstance(config_headers, dict):
            config_headers.setdefault("Referer", f"{self.base_url}/")

    # ------------------------------------------------------------------
    # Helpers internes (Madara)
    # ------------------------------------------------------------------

    def _madara_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="madara"``.
        """
        return self.logger

    @staticmethod
    def _madara_clean(value: str | None) -> str:
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
    def _madara_text(node: Node | None) -> str:
        """Extrait et nettoie le texte d'un nœud.

        Args:
            node: Nœud selectolax ou ``None``.

        Returns:
            Texte nettoyé.
        """
        if node is None:
            return ""
        return GalaxyScansParser._madara_clean(
            node.text(deep=True, separator=" ")
        )

    @staticmethod
    def _madara_attr(node: Node | None, name: str) -> str:
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

    def _madara_first(self, tree: HTMLParser, selector: str) -> Node | None:
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

    def _madara_all(self, tree: HTMLParser, selector: str) -> list[Node]:
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

    def _madara_abs(self, url: str, base: str | None = None) -> str:
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

    def _madara_parse_status(self, raw: str | None) -> MangaStatus:
        """Convertit un libellé de statut Madara en énumération.

        Args:
            raw: Libellé brut.

        Returns:
            Statut normalisé (``ONGOING`` par défaut).
        """
        if not raw:
            return MangaStatus.ONGOING
        key = self._madara_clean(raw).lower()
        for needle, status in _STATUS_MAP.items():
            if needle in key:
                return status
        return MangaStatus.ONGOING

    @staticmethod
    def _madara_parse_year(raw: str | None) -> int | None:
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
    def _madara_parse_relative_date(raw: str | None) -> datetime | None:
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
    def _madara_chapter_number(text: str) -> float | str:
        """Extrait le numéro de chapitre d'un libellé.

        Gère les numéros décimaux (``40.1``, ``12.5``) ainsi que les labels
        spéciaux (``Oneshot``, ``Extra``).

        Args:
            text: Libellé (ex. ``"Chapter 40.1"``).

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = GalaxyScansParser._madara_clean(text)
        match = _CHAPTER_NUMBER_RE.search(cleaned)
        if not match:
            return cleaned
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return cleaned

    @staticmethod
    def _madara_chapter_volume(text: str) -> int | None:
        """Extrait le numéro de tome d'un libellé.

        Args:
            text: Libellé contenant éventuellement ``Volume X``.

        Returns:
            Numéro de tome ou ``None``.
        """
        match = _VOLUME_RE.search(text or "")
        return int(match.group(1)) if match else None

    def _madara_detect_rating(self, genres: list[str]) -> ContentRating:
        """Détermine la classification de contenu depuis les genres.

        GD Scans traduit un mélange de séries généralistes et de séries
        matures (*Kichiku Eiyuu*, *Silent Miyashita-san's Sexy Channel*).
        La classification est détectée automatiquement.

        Args:
            genres: Liste de genres du manga.

        Returns:
            Classification détectée.
        """
        lowered = {g.lower() for g in genres}
        if lowered & _ADULT_GENRE_MARKERS:
            if {"hentai", "porn", "18+", "explicit"} & lowered:
                return ContentRating.PORNOGRAPHIC
            if {"smut", "mature", "erotica", "ecchi"} & lowered:
                return ContentRating.EROTICA
        if lowered & _SUGGESTIVE_MARKERS:
            return ContentRating.SUGGESTIVE
        return self.madara_default_rating

    # ------------------------------------------------------------------
    # Extraction de l'objet JS ts_reader
    # ------------------------------------------------------------------

    @staticmethod
    def _gd_extract_ts_reader(html: str) -> list[str]:
        """Extrait les URLs d'images depuis l'objet JS ``ts_reader``.

        Le thème Madara de GD Scans (comme de nombreux thèmes modernes)
        expose les pages dans un objet JavaScript ``ts_reader`` de la forme
        ``ts_reader.run({"sources":[{"images":["url1","url2",...]}]})``.
        C'est la méthode d'extraction principale utilisée par les
        extensions Tachiyomi/Mihon pour ce type de site.

        Args:
            html: HTML de la page chapitre.

        Returns:
            Liste ordonnée d'URLs d'images (vide si introuvable).
        """
        # Pattern 1 : ts_reader.run({...}).
        patterns = [
            re.compile(r"ts_reader\.run\(\s*(\{.*?\})\s*\)", re.DOTALL),
            re.compile(
                r"(?:var|let|const)\s+ts_reader\s*=\s*(\{.*?\})\s*;",
                re.DOTALL,
            ),
        ]

        for pattern in patterns:
            for match in pattern.finditer(html):
                raw = match.group(1)
                try:
                    parsed = _json.loads(raw)
                except ValueError:
                    continue
                urls = GalaxyScansParser._gd_walk_ts_reader(parsed)
                if urls:
                    return urls

        # Fallback : extraction par regex de toutes les chaînes.
        urls = re.findall(
            r'"(?:url|image|src)"\s*:\s*"([^"]+)"', html, re.IGNORECASE
        )
        return urls

    @staticmethod
    def _gd_walk_ts_reader(payload: Any) -> list[str]:
        """Parcourt récursivement un objet ``ts_reader`` pour en extraire les URLs.

        Args:
            payload: Objet Python issu du décodage JSON.

        Returns:
            Liste d'URLs trouvées dans l'ordre.
        """
        urls: list[str] = []

        def _walk(obj: Any) -> None:
            if isinstance(obj, str):
                if obj.startswith(("http://", "https://", "/")):
                    urls.append(obj)
            elif isinstance(obj, list):
                for item in obj:
                    _walk(item)
            elif isinstance(obj, dict):
                for key in ("images", "url", "src", "image"):
                    if key in obj:
                        _walk(obj[key])
                for value in obj.values():
                    if isinstance(value, (list, dict)):
                        _walk(value)

        _walk(payload)
        return urls

    # ------------------------------------------------------------------
    # Méthodes abstraites de BaseParser
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des mangas sur GD Scans.

        GD Scans utilise ``/?s={query}&post_type=wp-manga`` (paramètre
        WordPress standard pour le CPT ``wp-manga`` de Madara).

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.

        Raises:
            ParseError: Si le HTML de résultats est inexploitable.
        """
        self.logger.debug(
            "GalaxyScans search: {query} (page {page})",
            query=query,
            page=page,
        )

        base = self.base_url.rstrip("/")
        url = f"{base}/?s={quote_plus(query)}&post_type=wp-manga"
        if page > 1:
            url = f"{url}&page={page}"

        try:
            rendered = await self.fetch_rendered(
                url,
                wait_until="networkidle",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec recherche GalaxyScans pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        return self._madara_parse_search_html(
            rendered.html, base_url=rendered.final_url or url
        )

    def _madara_parse_search_html(
        self,
        html: str,
        *,
        base_url: str,
    ) -> list[SearchResult]:
        """Parse le HTML de résultats de recherche Madara.

        Args:
            html: HTML rendu.
            base_url: URL de la page (pour résolution relative).

        Returns:
            Liste de :class:`SearchResult`.
        """
        tree = HTMLParser(html)
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for node in self._madara_all(
            tree, self.madara_search_item_selector
        ):
            link_node: Node | None = None
            if node.tag == "a" and (
                "/manga/" in self._madara_attr(node, "href")
                or "/series/" in self._madara_attr(node, "href")
            ):
                link_node = node
            else:
                for candidate in node.css("a"):
                    href = self._madara_attr(candidate, "href")
                    if "/manga/" in href or "/series/" in href:
                        link_node = candidate
                        break

            if link_node is None:
                continue

            href = self._madara_attr(link_node, "href")
            if not href:
                continue
            abs_url = self._madara_abs(href, base_url)
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            title_node = node.css_first(self.madara_search_title_selector)
            title = self._madara_text(title_node) or self._madara_attr(
                link_node, "title"
            )
            if not title:
                title = (
                    self._madara_attr(link_node, "href")
                    .rstrip("/")
                    .rsplit("/", 1)[-1]
                )
                title = title.replace("-", " ").title()
            if not title:
                continue

            cover_node = node.css_first(self.madara_search_cover_selector)
            cover_src = (
                self._madara_attr(cover_node, "data-src")
                or self._madara_attr(cover_node, "data-lazy-src")
                or self._madara_attr(cover_node, "src")
            )
            cover_url = (
                self._madara_abs(cover_src, base_url) if cover_src else None
            )

            author_node = node.css_first(self.madara_search_author_selector)
            author = self._madara_text(author_node) or None

            results.append(
                SearchResult(
                    title=title,
                    url=abs_url,
                    site_id=self.config.id,
                    cover_url=cover_url,
                    author=author,
                )
            )

        self.logger.info(
            "GalaxyScans search: {n} résultat(s)", n=len(results)
        )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga GD Scans.

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
            url = f"{self.base_url}{self.madara_series_path}/{url_or_id.strip('/')}/"

        self.logger.debug("GalaxyScans get_manga: {url}", url=url)

        try:
            rendered = await self.fetch_rendered(
                url,
                wait_until="networkidle",
                wait_for_selector="h1, .entry-title, .post-title, .manga-title",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_manga GalaxyScans pour {url!r}: {err}",
                url=url,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {url!r}: {exc}"
            ) from exc

        html = rendered.html
        tree = HTMLParser(html)

        title_node = self._madara_first(
            tree, self.madara_manga_title_selector
        )
        title = self._madara_text(title_node)
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        cover_node = self._madara_first(
            tree, self.madara_manga_cover_selector
        )
        cover_src = (
            self._madara_attr(cover_node, "data-src")
            or self._madara_attr(cover_node, "data-lazy-src")
            or self._madara_attr(cover_node, "src")
        )
        cover_url = self._madara_abs(cover_src, url) if cover_src else None

        description_node = self._madara_first(
            tree, self.madara_manga_description_selector
        )
        description = self._madara_text(description_node) or None

        author_node = self._madara_first(
            tree, self.madara_manga_author_selector
        )
        author = self._madara_text(author_node) or None

        artist_node = self._madara_first(
            tree, self.madara_manga_artist_selector
        )
        artist = self._madara_text(artist_node) or None

        genre_nodes = self._madara_all(
            tree, self.madara_manga_genres_selector
        )
        genres: list[str] = []
        for node in genre_nodes:
            text = self._madara_text(node)
            if text and text not in genres:
                genres.append(text)

        status_node = self._madara_first(
            tree, self.madara_manga_status_selector
        )
        status = self._madara_parse_status(
            self._madara_text(status_node)
        )

        year_node = self._madara_first(
            tree, self.madara_manga_year_selector
        )
        year = self._madara_parse_year(self._madara_text(year_node))

        source_id = self._madara_extract_series_slug(url)
        rating = self._madara_detect_rating(genres)

        chapters = self._madara_parse_chapters_from_html(
            html, base_url=url
        )

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
            year=year,
            cover_url=cover_url,
            language=self.language,
            content_rating=rating,
            chapters=chapters,
            url=url,
            updated_at=datetime.now(timezone.utc),
        )
        self.logger.info(
            "GalaxyScans get_manga OK: {title} ({n} chapitres)",
            title=title,
            n=len(chapters),
        )
        return manga

    @staticmethod
    def _madara_extract_series_slug(url: str) -> str:
        """Extrait un identifiant de série depuis une URL Madara.

        Args:
            url: URL de la série.

        Returns:
            Slug nettoyé.
        """
        parts = [p for p in urlparse(url).path.split("/") if p]
        if not parts:
            return url
        for candidate in reversed(parts):
            if candidate and not candidate.isdigit():
                return candidate
        return parts[-1]

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
            "GalaxyScans get_chapters: {title}", title=manga.title
        )

        try:
            rendered = await self.fetch_rendered(
                str(manga.url),
                wait_until="networkidle",
                wait_for_selector=".listing-chapters_wrap li, .wp-manga-chapter, a[href*='chapter']",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_chapters GalaxyScans pour {title!r}: {err}",
                title=manga.title,
                err=exc,
            )
            raise ParseError(
                f"get_chapters échoué sur {self.site_id!r} "
                f"pour {manga.title!r}: {exc}"
            ) from exc

        chapters = self._madara_parse_chapters_from_html(
            rendered.html, base_url=rendered.final_url or str(manga.url)
        )
        if not chapters:
            raise ChapterNotFoundError(
                f"Aucun chapitre trouvé pour {manga.url} sur {self.site_id!r}"
            )
        return chapters

    def _madara_parse_chapters_from_html(
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

        for node in self._madara_all(tree, self.madara_chapter_selector):
            # Filtre les chapitres verrouillés (premium) : pas de <a> actif.
            link_node = node.css_first("a")
            href = self._madara_attr(link_node, "href")
            if not href or href.startswith("#"):
                continue
            abs_url = self._madara_abs(href, base_url)
            if abs_url in seen:
                continue
            seen.add(abs_url)

            label = self._madara_text(node) or self._madara_attr(
                link_node, "title"
            )
            if not label:
                label = abs_url.rstrip("/").rsplit("/", 1)[-1] or abs_url

            number = self._madara_chapter_number(label)
            volume = self._madara_chapter_volume(label)

            date_text = self._madara_attr(node, "data-date") or None
            published = (
                self._madara_parse_relative_date(date_text)
                if date_text
                else self._madara_parse_relative_date(label)
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
            "GalaxyScans chapitres: {n}", n=len(chapters)
        )
        return chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre GD Scans.

        Le site utilise Cloudflare et du lazy-loading : le rendu Playwright
        est nécessaire. Les pages sont extraites en priorité depuis l'objet
        JS ``ts_reader`` (méthode Tachiyomi/Mihon), avec fallback sur les
        sélecteurs CSS Madara.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "GalaxyScans get_pages: {url}", url=str(chapter.url)
        )
        chapter_url = str(chapter.url)

        try:
            rendered = await self.fetch_rendered(
                chapter_url,
                wait_until="networkidle",
                wait_for_selector=".reading-content img, .page-break img, #readerarea img",
                timeout=45.0,
                bypass_cloudflare=True,
                block_resources=False,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_pages GalaxyScans pour {url!r}: {err}",
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

        # Priorité 1 : objet JS ts_reader (méthode Tachiyomi/Mihon).
        ts_urls = self._gd_extract_ts_reader(html)
        if ts_urls:
            for u in ts_urls:
                abs_url = self._madara_abs(u, chapter_url)
                if abs_url not in urls:
                    urls.append(abs_url)

        # Priorité 2 : sélecteur CSS Madara.
        if not urls:
            for node in self._madara_all(tree, self.madara_page_img_selector):
                src = (
                    self._madara_attr(node, "data-src")
                    or self._madara_attr(node, "data-lazy-src")
                    or self._madara_attr(node, "data-original")
                    or self._madara_attr(node, "src")
                )
                if not src:
                    continue
                abs_url = self._madara_abs(src, chapter_url)
                if abs_url not in urls:
                    urls.append(abs_url)

        # Priorité 3 : variable JS embarquée (fallback).
        if not urls:
            for var_name in self.madara_pages_var_names:
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
            clean = self._madara_abs(clean, chapter_url)
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

        self.logger.info(
            "GalaxyScans get_pages: {n} page(s) extraite(s)", n=len(pages)
        )
        return pages

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que GD Scans est accessible.

        Teste le domaine principal puis les domaines de fallback.

        Returns:
            ``True`` si un domaine répond correctement.
        """
        for base in self.fallback_domains:
            try:
                rendered = await self.fetch_rendered(
                    base,
                    wait_until="networkidle",
                    timeout=30.0,
                    bypass_cloudflare=True,
                    screenshot=False,
                )
                if rendered.ok:
                    self.logger.info(
                        "Health check GalaxyScans OK: {base} (status={s})",
                        base=base,
                        s=rendered.status,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Health check GalaxyScans échec sur {base}: {err}",
                    base=base,
                    err=exc,
                )
        self.logger.warning("Health check GalaxyScans KO (tous domaines)")
        return False
