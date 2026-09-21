"""Parseur Batoto (bato.to) pour NexusDL.

Batoto (``https://bato.to``) est l'un des plus anciens lecteurs de mangas en
ligne, né en 2012 après la fermeture du Batoto original. Le site héberge une
vaste collection de mangas, manhwas et manhuas en plusieurs langues, avec un
accent particulier sur les traductions anglaises.

Caractéristiques techniques
---------------------------

* **Moteur** : **PHP custom** (pas de WordPress/Madara/MangaStream). Le site
  utilise un système de templates propre avec des IDs et classes CSS stables.
* **Langue** : Multi-langue (``en``, ``fr``, ``es``, ``pt``, ``ru``…).
* **Protection** : Cloudflare (AS13335, IP ``104.21.16.85``) avec challenge
  modéré. Le site a été « semi-privé » par le passé, mais est redevenu
  public depuis 2023.
* **Domaines** : ``bato.to`` (principal), ``batoto.com`` (ancien).
* **Structure des URLs** :
    - Recherche : ``/search?word={query}`` (paramètre ``word``).
    - Manga : ``/series/{id}`` (ex. ``/series/12345``).
    - Chapitre : ``/chapter/{id}`` (ex. ``/chapter/67890``).
    - Reader AJAX : ``/areader?id={chapter_id}&p={page}``.
* **Système de pagination par tabs** : les séries de plus de 100 chapitres
  sont découpées en tabs de 100 entrées. Chaque tab a une URL de la forme
  ``/series/{id}?tab=2``. Le parseur itère sur tous les tabs pour reconstituer
  la liste complète.
* **Images** : chargées via AJAX depuis ``/areader?id={chapter_id}&p={page}``.
  Le premier appel retourne le HTML de la page 1, et les appels suivants
  incrémentent ``p`` jusqu'à obtenir une page vide.
* **CDN d'images** : les URLs pointent vers des sous-domaines CDN (ex.
  ``i*.bato.to``), nécessitant un header ``Referer`` correct.

Le parseur combine deux mixins :

1. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — pour le
   contournement Cloudflare automatique (Playwright / FlareSolverr).
2. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — pour le
   rendu Playwright si nécessaire (fallback pour les pages dynamiques et les
   appels AJAX).

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.en.batoto import BatotoParser
    >>>
    >>> parser = BatotoParser(config, session)
    >>> results = await parser.search("demon slayer")
    >>> manga = await parser.get_manga(results[0].url)
    >>> chapters = await parser.get_chapters(manga)
    >>> pages = await parser.get_pages(chapters[0])
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
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

__all__ = ["BatotoParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://bato.to"
_FALLBACK_DOMAINS: Final[tuple[str, ...]] = (
    "https://bato.to",
    "https://batoto.com",
)
_SITE_ID: Final[str] = "batoto"
_LANGUAGE: Final[Language] = Language.EN
_ADULT: Final[bool] = False

# Paramètre de recherche : Batoto utilise "word" (pas "s" ni "q").
_SEARCH_PARAM: Final[str] = "word"

# Regex de parsing des chapitres (format Batoto : "Chapter 205").
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

# Extraction de l'ID de série depuis l'URL.
_SERIES_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"/series/(\d+)", re.IGNORECASE
)
_CHAPTER_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"/chapter/(\d+)", re.IGNORECASE
)

# Extraction de l'URL d'image depuis la réponse AJAX.
_AJAX_IMG_RE: Final[re.Pattern[str]] = re.compile(
    r'<img[^>]+src="([^"]+)"', re.IGNORECASE
)
_AJAX_DATA_ATTR_RE: Final[re.Pattern[str]] = re.compile(
    r'data-src="([^"]+)"', re.IGNORECASE
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
        "gender bender",
    }
)

# Marqueurs de contenu suggestif.
_SUGGESTIVE_MARKERS: Final[frozenset[str]] = frozenset(
    {"ecchi", "mature", "harem", "suggestive", "romance", "action"}
)

# ---------------------------------------------------------------------------
# Sélecteurs Batoto (PHP custom)
# ---------------------------------------------------------------------------

_BATO_SEARCH_ITEM_SELECTOR: Final[str] = (
    "div#series-list div.item, "
    "div.item.col, "
    ".series-list .item, "
    "a[href*='/series/']"
)
_BATO_SEARCH_LINK_SELECTOR: Final[str] = "a"
_BATO_SEARCH_COVER_SELECTOR: Final[str] = "img"
_BATO_SEARCH_TITLE_SELECTOR: Final[str] = (
    ".item-title, .title, h3, h4, .text-truncate"
)

_BATO_MANGA_TITLE_SELECTOR: Final[str] = (
    "h3.item-title, h1, .item-title, .series-title"
)
_BATO_MANGA_COVER_SELECTOR: Final[str] = (
    ".item-cover img, .cover img, .series-cover img, "
    "img[src*='bato.to']"
)
_BATO_MANGA_DESCRIPTION_SELECTOR: Final[str] = (
    ".description, .summary, .series-desc, "
    ".item-summary, .prose p"
)
_BATO_MANGA_AUTHOR_SELECTOR: Final[str] = (
    ".author, .item-author, a[href*='/author/'], "
    ".series-author"
)
_BATO_MANGA_ARTIST_SELECTOR: Final[str] = (
    ".artist, .item-artist, a[href*='/artist/']"
)
_BATO_MANGA_GENRES_SELECTOR: Final[str] = (
    ".genres a, .item-genres a, "
    "a[href*='/genre/'], a[href*='/tag/']"
)
_BATO_MANGA_STATUS_SELECTOR: Final[str] = (
    ".status, .item-status, .series-status"
)

_BATO_CHAPTER_SELECTOR: Final[str] = (
    "div#chapter-list a, "
    ".chapter-list a, "
    "a[href*='/chapter/']"
)
_BATO_PAGE_IMG_SELECTOR: Final[str] = (
    "img.comic_page, "
    "#comic_page, "
    ".reader img, "
    "#viewer img"
)
_BATO_PAGES_VAR_NAMES: Final[tuple[str, ...]] = (
    "pages",
    "page_urls",
    "pageUrls",
    "images",
)


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


class BatotoParser(
    JsRenderedMixin,
    CloudflareMixin,
    BaseParser,
):
    """Parseur Batoto (PHP custom + Cloudflare + AJAX reader).

    Combine le rendu Playwright (fallback), le contournement Cloudflare et
    des implémentations inline adaptées au DOM PHP custom du site.

    Attributes:
        site_id: Identifiant interne du site.
        language: Langue principale (anglais par défaut, configurable).
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

    # --- Chemins Batoto ---
    bato_search_path: ClassVar[str] = "/search"
    bato_series_path: ClassVar[str] = "/series"
    bato_chapter_path: ClassVar[str] = "/chapter"
    bato_reader_endpoint: ClassVar[str] = "/areader"

    # --- Sélecteurs Batoto ---
    bato_search_item_selector: ClassVar[str] = _BATO_SEARCH_ITEM_SELECTOR
    bato_search_link_selector: ClassVar[str] = _BATO_SEARCH_LINK_SELECTOR
    bato_search_cover_selector: ClassVar[str] = _BATO_SEARCH_COVER_SELECTOR
    bato_search_title_selector: ClassVar[str] = _BATO_SEARCH_TITLE_SELECTOR

    bato_manga_title_selector: ClassVar[str] = _BATO_MANGA_TITLE_SELECTOR
    bato_manga_cover_selector: ClassVar[str] = _BATO_MANGA_COVER_SELECTOR
    bato_manga_description_selector: ClassVar[str] = _BATO_MANGA_DESCRIPTION_SELECTOR
    bato_manga_author_selector: ClassVar[str] = _BATO_MANGA_AUTHOR_SELECTOR
    bato_manga_artist_selector: ClassVar[str] = _BATO_MANGA_ARTIST_SELECTOR
    bato_manga_genres_selector: ClassVar[str] = _BATO_MANGA_GENRES_SELECTOR
    bato_manga_status_selector: ClassVar[str] = _BATO_MANGA_STATUS_SELECTOR

    bato_chapter_selector: ClassVar[str] = _BATO_CHAPTER_SELECTOR
    bato_page_img_selector: ClassVar[str] = _BATO_PAGE_IMG_SELECTOR
    bato_pages_var_names: ClassVar[tuple[str, ...]] = _BATO_PAGES_VAR_NAMES

    # --- Comportement ---
    bato_requires_js: ClassVar[bool] = True
    bato_default_rating: ClassVar[ContentRating] = ContentRating.SAFE

    # --- Pagination par tabs (Batoto) ---
    # Au-delà de 100 chapitres, Batoto découpe la liste en tabs de 100.
    bato_max_tabs: ClassVar[int] = 20  # sécurité (2000 chapitres max)

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
        language: Language | None = None,
    ) -> None:
        """Initialise le parseur Batoto.

        Args:
            config: Configuration du site (:class:`SiteConfig`).
            session: Session HTTP async du projet (:class:`HttpSession`).
            playwright_pool: Pool Playwright partagé (requis — Cloudflare).
            cookie_manager: Gestionnaire de cookies chiffrés (optionnel).
            flaresolverr: Client FlareSolverr (optionnel).
            language: Langue cible (par défaut : anglais).
        """
        super().__init__(
            config=config,
            session=session,
            playwright_pool=playwright_pool,
        )
        self.cookie_manager = cookie_manager
        self.flaresolverr = flaresolverr
        if language is not None:
            self.language = language
        self.logger = get_logger(f"{self.__class__.__module__}.{self.site_id}")

        # Injecte le header Referer requis par le CDN d'images.
        self._inject_cdn_referer()

    def _inject_cdn_referer(self) -> None:
        """Injecte le header ``Referer`` requis par le CDN d'images."""
        target = getattr(self.session, "headers", None)
        if isinstance(target, dict):
            target.setdefault("Referer", f"{self.base_url}/")
            self.logger.debug("Header Referer CDN injecté dans la session")
            return
        config_headers = getattr(self.config, "default_headers", None)
        if isinstance(config_headers, dict):
            config_headers.setdefault("Referer", f"{self.base_url}/")

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------

    def _bato_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="batoto"``.
        """
        return self.logger

    @staticmethod
    def _bato_clean(value: str | None) -> str:
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
    def _bato_text(node: Node | None) -> str:
        """Extrait et nettoie le texte d'un nœud.

        Args:
            node: Nœud selectolax ou ``None``.

        Returns:
            Texte nettoyé.
        """
        if node is None:
            return ""
        return BatotoParser._bato_clean(
            node.text(deep=True, separator=" ")
        )

    @staticmethod
    def _bato_attr(node: Node | None, name: str) -> str:
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

    def _bato_first(self, tree: HTMLParser, selector: str) -> Node | None:
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

    def _bato_all(self, tree: HTMLParser, selector: str) -> list[Node]:
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

    def _bato_abs(self, url: str, base: str | None = None) -> str:
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

    def _bato_parse_status(self, raw: str | None) -> MangaStatus:
        """Convertit un libellé de statut en énumération NexusDL.

        Args:
            raw: Libellé brut.

        Returns:
            Statut normalisé (``ONGOING`` par défaut).
        """
        if not raw:
            return MangaStatus.ONGOING
        key = self._bato_clean(raw).lower()
        for needle, status in _STATUS_MAP.items():
            if needle in key:
                return status
        return MangaStatus.ONGOING

    @staticmethod
    def _bato_parse_year(raw: str | None) -> int | None:
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
    def _bato_parse_relative_date(raw: str | None) -> datetime | None:
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
        from datetime import timedelta

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
    def _bato_chapter_number(text: str) -> float | str:
        """Extrait le numéro de chapitre d'un libellé.

        Args:
            text: Libellé (ex. ``"Chapter 205"``).

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = BatotoParser._bato_clean(text)
        match = _CHAPTER_NUMBER_RE.search(cleaned)
        if not match:
            return cleaned
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return cleaned

    @staticmethod
    def _bato_chapter_volume(text: str) -> int | None:
        """Extrait le numéro de tome d'un libellé.

        Args:
            text: Libellé contenant éventuellement ``Volume X``.

        Returns:
            Numéro de tome ou ``None``.
        """
        match = _VOLUME_RE.search(text or "")
        return int(match.group(1)) if match else None

    def _bato_detect_rating(self, genres: list[str]) -> ContentRating:
        """Détermine la classification de contenu depuis les genres.

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
        return self.bato_default_rating

    @staticmethod
    def _bato_extract_series_id(url: str) -> str | None:
        """Extrait l'ID de série depuis une URL Batoto.

        Format : ``/series/{id}`` → retourne ``{id}``.

        Args:
            url: URL de la série.

        Returns:
            ID de série ou ``None``.
        """
        match = _SERIES_ID_RE.search(url)
        return match.group(1) if match else None

    @staticmethod
    def _bato_extract_chapter_id(url: str) -> str | None:
        """Extrait l'ID de chapitre depuis une URL Batoto.

        Format : ``/chapter/{id}`` → retourne ``{id}``.

        Args:
            url: URL du chapitre.

        Returns:
            ID de chapitre ou ``None``.
        """
        match = _CHAPTER_ID_RE.search(url)
        return match.group(1) if match else None

    # ------------------------------------------------------------------
    # Méthodes abstraites de BaseParser
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des mangas sur Batoto.

        Batoto utilise ``/search?word={query}`` (paramètre ``word``).

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.

        Raises:
            ParseError: Si le HTML de résultats est inexploitable.
        """
        self.logger.debug(
            "Batoto search: {query} (page {page})",
            query=query,
            page=page,
        )

        base = self.base_url.rstrip("/")
        url = f"{base}{self.bato_search_path}?{_SEARCH_PARAM}={quote_plus(query)}"
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
                "Échec recherche Batoto pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        return self._bato_parse_search_html(
            rendered.html, base_url=rendered.final_url or url
        )

    def _bato_parse_search_html(
        self,
        html: str,
        *,
        base_url: str,
    ) -> list[SearchResult]:
        """Parse le HTML de résultats de recherche.

        Args:
            html: HTML rendu.
            base_url: URL de la page (pour résolution relative).

        Returns:
            Liste de :class:`SearchResult`.
        """
        tree = HTMLParser(html)
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for node in self._bato_all(tree, self.bato_search_item_selector):
            link_node: Node | None = None
            if node.tag == "a" and "/series/" in self._bato_attr(node, "href"):
                link_node = node
            else:
                for candidate in node.css("a"):
                    if "/series/" in self._bato_attr(candidate, "href"):
                        link_node = candidate
                        break

            if link_node is None:
                continue

            href = self._bato_attr(link_node, "href")
            if not href:
                continue
            abs_url = self._bato_abs(href, base_url)
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            title_node = node.css_first(self.bato_search_title_selector)
            title = self._bato_text(title_node) or self._bato_attr(
                link_node, "title"
            )
            if not title:
                title = (
                    self._bato_attr(link_node, "href")
                    .rstrip("/")
                    .rsplit("/", 1)[-1]
                )
                title = title.replace("-", " ").title()
            if not title:
                continue

            cover_node = node.css_first(self.bato_search_cover_selector)
            cover_src = (
                self._bato_attr(cover_node, "data-src")
                or self._bato_attr(cover_node, "data-lazy-src")
                or self._bato_attr(cover_node, "src")
            )
            cover_url = (
                self._bato_abs(cover_src, base_url) if cover_src else None
            )

            results.append(
                SearchResult(
                    title=title,
                    url=abs_url,
                    site_id=self.config.id,
                    cover_url=cover_url,
                    author=None,
                )
            )

        self.logger.info(
            "Batoto search: {n} résultat(s)", n=len(results)
        )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga Batoto.

        Args:
            url_or_id: URL absolue ou ID de série.

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
            url = f"{self.base_url}{self.bato_series_path}/{url_or_id}"

        self.logger.debug("Batoto get_manga: {url}", url=url)

        try:
            rendered = await self.fetch_rendered(
                url,
                wait_until="networkidle",
                wait_for_selector="h3.item-title, h1, .item-title",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_manga Batoto pour {url!r}: {err}",
                url=url,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {url!r}: {exc}"
            ) from exc

        html = rendered.html
        tree = HTMLParser(html)

        title_node = self._bato_first(tree, self.bato_manga_title_selector)
        title = self._bato_text(title_node)
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        cover_node = self._bato_first(tree, self.bato_manga_cover_selector)
        cover_src = (
            self._bato_attr(cover_node, "data-src")
            or self._bato_attr(cover_node, "data-lazy-src")
            or self._bato_attr(cover_node, "src")
        )
        cover_url = self._bato_abs(cover_src, url) if cover_src else None

        description_node = self._bato_first(
            tree, self.bato_manga_description_selector
        )
        description = self._bato_text(description_node) or None

        author_node = self._bato_first(tree, self.bato_manga_author_selector)
        author = self._bato_text(author_node) or None

        artist_node = self._bato_first(tree, self.bato_manga_artist_selector)
        artist = self._bato_text(artist_node) or None

        genre_nodes = self._bato_all(tree, self.bato_manga_genres_selector)
        genres: list[str] = []
        for node in genre_nodes:
            text = self._bato_text(node)
            if text and text not in genres:
                genres.append(text)

        status_node = self._bato_first(tree, self.bato_manga_status_selector)
        status = self._bato_parse_status(self._bato_text(status_node))

        source_id = (
            self._bato_extract_series_id(url)
            or self._bato_extract_series_slug(url)
        )
        rating = self._bato_detect_rating(genres)

        chapters = await self._bato_parse_chapters_from_html(
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
            year=None,
            cover_url=cover_url,
            language=self.language,
            content_rating=rating,
            chapters=chapters,
            url=url,
            updated_at=datetime.now(timezone.utc),
        )
        self.logger.info(
            "Batoto get_manga OK: {title} ({n} chapitres)",
            title=title,
            n=len(chapters),
        )
        return manga

    @staticmethod
    def _bato_extract_series_slug(url: str) -> str:
        """Extrait un identifiant de série depuis une URL Batoto (fallback).

        Args:
            url: URL de la série.

        Returns:
            Slug nettoyé.
        """
        parts = [p for p in urlparse(url).path.split("/") if p]
        if "series" in parts:
            idx = parts.index("series")
            remaining = parts[idx + 1:]
            if remaining:
                return remaining[0]
        if parts:
            return parts[-1]
        return url

    async def get_chapters(self, manga: Manga) -> list[Chapter]:
        """Récupère la liste des chapitres d'un manga.

        Batoto découpe les séries de plus de 100 chapitres en tabs de 100
        entrées. Cette méthode itère sur tous les tabs pour reconstituer la
        liste complète.

        Args:
            manga: Manga dont on veut les chapitres.

        Returns:
            Liste de :class:`Chapter` triés par numéro croissant.

        Raises:
            ChapterNotFoundError: Si aucun chapitre n'est trouvé.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "Batoto get_chapters: {title}", title=manga.title
        )

        series_id = manga.source_id
        all_chapters: list[Chapter] = []
        seen: set[str] = set()

        # Itère sur les tabs (1 tab = 100 chapitres).
        for tab in range(1, self.bato_max_tabs + 1):
            tab_url = f"{self.base_url}{self.bato_series_path}/{series_id}"
            if tab > 1:
                tab_url = f"{tab_url}?tab={tab}"

            try:
                rendered = await self.fetch_rendered(
                    tab_url,
                    wait_until="networkidle",
                    wait_for_selector="a[href*='/chapter/']",
                    timeout=45.0,
                    bypass_cloudflare=True,
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Tab {tab} inaccessible: {err}", tab=tab, err=exc
                )
                break

            chapters = self._bato_parse_chapters_from_html(
                rendered.html, base_url=tab_url
            )
            if not chapters:
                break

            # Filtre les doublons entre tabs.
            new_chapters = [c for c in chapters if c.source_id not in seen]
            if not new_chapters:
                break

            for c in new_chapters:
                seen.add(c.source_id)
                all_chapters.append(c)

            self.logger.debug(
                "Tab {tab}: {n} nouveaux chapitres",
                tab=tab,
                n=len(new_chapters),
            )

        if not all_chapters:
            raise ChapterNotFoundError(
                f"Aucun chapitre trouvé pour {manga.url} sur {self.site_id!r}"
            )

        # Tri par numéro croissant.
        def _sort_key(ch: Chapter) -> tuple[int, float, str]:
            num = ch.number if isinstance(ch.number, (int, float)) else 0.0
            return (int(isinstance(ch.number, str)), float(num), ch.title.lower())

        all_chapters.sort(key=_sort_key)
        self.logger.info(
            "Batoto get_chapters OK: {n} chapitre(s)", n=len(all_chapters)
        )
        return all_chapters

    def _bato_parse_chapters_from_html(
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
        chapters: list[Chapter] = []
        language = self.language

        for node in self._bato_all(tree, self.bato_chapter_selector):
            href = self._bato_attr(node, "href")
            if not href or "/chapter/" not in href:
                continue
            abs_url = self._bato_abs(href, base_url)

            # Extrait l'ID de chapitre depuis l'URL.
            chapter_id = self._bato_extract_chapter_id(abs_url)
            if not chapter_id:
                continue

            label = self._bato_text(node) or self._bato_attr(node, "title")
            if not label:
                label = abs_url.rstrip("/").rsplit("/", 1)[-1] or abs_url

            number = self._bato_chapter_number(label)
            volume = self._bato_chapter_volume(label)

            date_text = self._bato_attr(node, "data-date") or None
            published = (
                self._bato_parse_relative_date(date_text)
                if date_text
                else self._bato_parse_relative_date(label)
            )

            source_id = chapter_id
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

        return chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre Batoto.

        Batoto charge les images via AJAX depuis l'endpoint
        ``/areader?id={chapter_id}&p={page}``. Chaque appel retourne le HTML
        d'une page, et les appels suivants incrémentent ``p`` jusqu'à
        obtenir une page vide (ou un code 404).

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si la réponse AJAX est inexploitable.
        """
        self.logger.debug(
            "Batoto get_pages: {url}", url=str(chapter.url)
        )
        chapter_id = chapter.source_id

        if not chapter_id:
            raise ChapterNotFoundError(
                f"ID de chapitre introuvable dans {chapter.url!r}"
            )

        # Stratégie 1 : rendu Playwright du chapitre (extraction DOM directe).
        urls: list[str] = []
        try:
            rendered = await self.fetch_rendered(
                str(chapter.url),
                wait_until="networkidle",
                wait_for_selector="img.comic_page, #comic_page, .reader img",
                timeout=45.0,
                bypass_cloudflare=True,
                block_resources=False,
            )
            html = rendered.html
            tree = HTMLParser(html)

            for node in self._bato_all(tree, self.bato_page_img_selector):
                src = (
                    self._bato_attr(node, "data-src")
                    or self._bato_attr(node, "data-lazy-src")
                    or self._bato_attr(node, "src")
                )
                if not src:
                    continue
                abs_url = self._bato_abs(src, str(chapter.url))
                if abs_url not in urls:
                    urls.append(abs_url)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(
                "Rendu Playwright KO, fallback AJAX : {err}", err=exc
            )

        # Stratégie 2 : appels AJAX séquentiels (méthode historique Batoto).
        if not urls:
            urls = await self._bato_fetch_pages_via_ajax(chapter_id)

        if not urls:
            raise ChapterNotFoundError(
                f"Aucune page trouvée pour {chapter.url} sur {self.site_id!r}"
            )

        pages: list[Page] = []
        for idx, url in enumerate(urls):
            clean = url.strip()
            if not clean:
                continue
            clean = self._bato_abs(clean, str(chapter.url))
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
            "Batoto get_pages: {n} page(s) extraite(s)", n=len(pages)
        )
        return pages

    async def _bato_fetch_pages_via_ajax(
        self, chapter_id: str
    ) -> list[str]:
        """Récupère les pages via les appels AJAX séquentiels.

        Chaque appel à ``/areader?id={chapter_id}&p={page}`` retourne le HTML
        d'une page. Le parseur incrémente ``p`` jusqu'à obtenir une réponse
        vide ou une erreur.

        Args:
            chapter_id: ID du chapitre.

        Returns:
            Liste ordonnée d'URLs d'images.
        """
        urls: list[str] = []
        page_num = 1
        max_pages = 500  # sécurité

        while page_num <= max_pages:
            endpoint = (
                f"{self.base_url}{self.bato_reader_endpoint}"
                f"?id={chapter_id}&p={page_num}"
            )

            try:
                response = await self.cf_get(
                    endpoint,
                    headers={"X-Requested-With": "XMLHttpRequest"},
                    timeout=15.0,
                )
            except Exception:  # noqa: BLE001
                break

            if response.status_code >= 400:
                break

            body = response.text.strip()
            if not body:
                break

            # Extrait l'URL de l'image depuis le HTML retourné.
            img_url = self._bato_extract_img_from_ajax(body)
            if not img_url:
                break

            if img_url not in urls:
                urls.append(img_url)

            page_num += 1

        return urls

    @staticmethod
    def _bato_extract_img_from_ajax(body: str) -> str | None:
        """Extrait l'URL d'image depuis la réponse AJAX de Batoto.

        Args:
            body: Corps HTML de la réponse AJAX.

        Returns:
            URL d'image ou ``None``.
        """
        # Priorité 1 : data-src (lazy-loading).
        match = _AJAX_DATA_ATTR_RE.search(body)
        if match:
            return match.group(1)

        # Priorité 2 : src classique.
        match = _AJAX_IMG_RE.search(body)
        if match:
            return match.group(1)

        return None

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que Batoto est accessible.

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
                        "Health check Batoto OK: {base} (status={s})",
                        base=base,
                        s=rendered.status,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Health check Batoto échec sur {base}: {err}",
                    base=base,
                    err=exc,
                )
        self.logger.warning("Health check Batoto KO (tous domaines)")
        return False
