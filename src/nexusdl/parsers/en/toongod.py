"""Parseur ToonGod pour NexusDL.

ToonGod (``https://www.toongod.org``) est un agrégateur de manhwa/manhua
anglophone utilisant le thème WordPress **Madara** (MangaBooth) — le même
moteur que Toonily, MangaBuddy, etc. Le site héberge principalement du
contenu adulte/ecchi (manhwa coréens traduits).

Caractéristiques techniques
---------------------------

* **Moteur** : WordPress Madara (MangaBooth) — thème premium pour sites
  manga.
* **Langue** : Anglais (``en``).
* **Protection** : Cloudflare (IP ``104.21.64.1`` / ``172.67.135.233``,
  AS13335 Cloudflare) — contournement nécessaire (FlareSolverr ou
  Playwright). Le site présente également un **captcha infini** signalé
  par la communauté Kotatsu/Tachiyomi[reference:0].
* **Domaines** : ``toongod.org`` (principal), ``toongod.com`` (ancien),
  ``cdn.toongod.com`` (CDN images)[reference:1].
* **Structure des URLs** :
    - Recherche : ``/?s={query}`` (paramètre standard WordPress).
    - Manga : ``/webtoon/{slug}/`` ou ``/manga/{slug}/``.
    - Chapitre : ``/webtoon/{slug}/chapter-{num}/``.
* **CDN images** : ``cdn.toongod.com`` — nécessite le cookie
  ``cf_clearance`` et le header ``Referer`` correct[reference:2].
* **Sélecteurs Madara** : ``.page-item-detail.manga``,
  ``div.listing-chapters_wrap > ul li``,
  ``div.reading-content div.page-break.no-gaps img``.

Le parseur combine trois mixins :

1. :class:`~nexusdl.parsers.mixins.madara.MadaraMixin` — implémentations
   par défaut pour le thème WordPress Madara.
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — pour le
   contournement Cloudflare automatique (FlareSolverr / Playwright).
3. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — pour le
   rendu Playwright si nécessaire (fallback pour les pages dynamiques).

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.en.toongod import ToonGodParser
    >>>
    >>> parser = ToonGodParser(config, session)
    >>> results = await parser.search("solo leveling")
    >>> manga = await parser.get_manga(results[0].url)
    >>> chapters = await parser.get_chapters(manga)
    >>> pages = await parser.get_pages(chapters[0])
"""

from __future__ import annotations

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

__all__ = ["ToonGodParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://www.toongod.org"
_FALLBACK_DOMAINS: Final[tuple[str, ...]] = (
    "https://www.toongod.org",
    "https://www.toongod.com",
)
_SITE_ID: Final[str] = "toongod"
_LANGUAGE: Final[Language] = Language.EN
_ADULT: Final[bool] = True  # Site mature (contenu adulte/ecchi)

# Regex de parsing des chapitres (format Madara : "Chapter 159").
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
    "end": MangaStatus.COMPLETED,  # ToonGod utilise "End"
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
        "manhwa",
    }
)

# ---------------------------------------------------------------------------
# Sélecteurs Madara spécifiques à ToonGod
# ---------------------------------------------------------------------------

_TOONGOD_SEARCH_ITEM_SELECTOR: Final[str] = (
    ".page-item-detail.manga, .page-item-detail, "
    ".c-tabs-item__content, .bs, .bsx"
)
_TOONGOD_CHAPTER_SELECTOR: Final[str] = (
    "div.listing-chapters_wrap > ul li, "
    ".listing-chapters_wrap li, "
    ".eplister li:not(.ch-price-side)"
)
_TOONGOD_PAGE_IMG_SELECTOR: Final[str] = (
    "div.reading-content div.page-break.no-gaps img, "
    ".reading-content img, "
    ".page-break img, "
    "#readerarea img"
)


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


class ToonGodParser(
    JsRenderedMixin,
    CloudflareMixin,
    MadaraMixin,
    BaseParser,
):
    """Parseur ToonGod (Madara + Cloudflare).

    Combine le rendu Playwright (fallback), le contournement Cloudflare et
    les implémentations Madara pour fournir une couverture complète du site.

    Attributes:
        site_id: Identifiant interne du site.
        language: Langue principale (anglais).
        adult: Contenu adulte (``True`` — site mature).
        base_url: URL racine du site.
    """

    # --- Métadonnées du parser ---
    site_id: ClassVar[str] = _SITE_ID
    language: ClassVar[Language] = _LANGUAGE
    adult: ClassVar[bool] = _ADULT

    # --- URLs de base ---
    base_url: ClassVar[str] = _BASE_URL
    fallback_domains: ClassVar[tuple[str, ...]] = _FALLBACK_DOMAINS
    fools_base_url: ClassVar[str | None] = None  # Non utilisé (pas FoolSlide)

    # --- Configuration Madara ---
    madara_base_url: ClassVar[str] = _BASE_URL
    madara_search_path: ClassVar[str] = "/"
    madara_search_query_param: ClassVar[str] = "s"
    madara_search_method: ClassVar[str] = "GET"
    madara_series_path: ClassVar[str] = "/webtoon"
    madara_read_path: ClassVar[str] = "/webtoon"

    # --- Sélecteurs Madara (standards + ajustements ToonGod) ---
    madara_search_item_selector: ClassVar[str] = _TOONGOD_SEARCH_ITEM_SELECTOR
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

    madara_chapter_selector: ClassVar[str] = _TOONGOD_CHAPTER_SELECTOR
    madara_page_img_selector: ClassVar[str] = _TOONGOD_PAGE_IMG_SELECTOR
    madara_pages_var_names: ClassVar[tuple[str, ...]] = (
        "pages",
        "page_urls",
        "pageUrls",
        "images",
        "chapterImages",
        "ts_reader",
    )

    # --- Comportement Madara ---
    madara_requires_js: ClassVar[bool] = False
    madara_api_pages_endpoint: ClassVar[str | None] = None
    madara_default_rating: ClassVar[ContentRating] = ContentRating.EROTICA
    madara_concurrent_chapter_requests: ClassVar[int] = 4
    madara_search_pages_limit: ClassVar[int] = 20

    # --- Configuration Cloudflare ---
    cf_enabled: ClassVar[bool] = True
    cf_requires_js: ClassVar[bool] = False
    cf_preferred_backend: ClassVar[str] = "flaresolverr"
    cf_use_playwright_fallback: ClassVar[bool] = True
    cf_use_flaresolverr_fallback: ClassVar[bool] = True
    cf_max_attempts: ClassVar[int] = 3
    cf_challenge_timeout: ClassVar[float] = 45.0  # Captcha infini → timeout plus long
    cf_clearance_ttl: ClassVar[int] = 1800

    # --- Configuration JsRendered ---
    js_rendered: ClassVar[bool] = True
    default_wait_until: ClassVar[str] = "domcontentloaded"
    default_render_timeout: ClassVar[float] = 45.0
    default_navigation_timeout: ClassVar[float] = 60.0
    block_resources_by_default: ClassVar[bool] = True
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
        """Initialise le parseur ToonGod.

        Args:
            config: Configuration du site (:class:`SiteConfig`).
            session: Session HTTP async du projet (:class:`HttpSession`).
            playwright_pool: Pool Playwright partagé (optionnel, pour le
                contournement Cloudflare et/ou le rendu JS).
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

        # Injecte le referer CDN si possible.
        self._inject_cdn_referer()

    def _inject_cdn_referer(self) -> None:
        """Injecte le header ``Referer`` requis pour le CDN ToonGod.

        Le CDN ``cdn.toongod.com`` exige un header ``Referer`` pointant vers
        ``https://www.toongod.com/`` pour servir les images[reference:3].
        """
        target = getattr(self.session, "headers", None)
        if isinstance(target, dict):
            target.setdefault("Referer", "https://www.toongod.com/")
            self.logger.debug("Header Referer CDN injecté dans la session")
            return
        # Fallback : tente d'ajouter via default_headers de la config.
        config_headers = getattr(self.config, "default_headers", None)
        if isinstance(config_headers, dict):
            config_headers.setdefault("Referer", "https://www.toongod.com/")

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
        return ToonGodParser._madara_clean(node.text(deep=True, separator=" "))

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

        Args:
            text: Libellé (ex. ``"Chapter 159"``).

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = ToonGodParser._madara_clean(text)
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
        return self.madara_default_rating

    # ------------------------------------------------------------------
    # Méthodes abstraites de BaseParser
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des mangas sur ToonGod.

        ToonGod utilise ``/?s={query}`` (paramètre standard WordPress).

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.

        Raises:
            ParseError: Si le HTML de résultats est inexploitable.
        """
        self.logger.debug(
            "ToonGod search: {query} (page {page})",
            query=query,
            page=page,
        )

        base = self.base_url.rstrip("/")
        url = f"{base}/?s={quote_plus(query)}"
        if page > 1:
            url = f"{url}&page={page}"

        try:
            html = await self.cf_get_html(url)
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec recherche ToonGod pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        return self._madara_parse_search_html(html, base_url=url)

    def _madara_parse_search_html(
        self,
        html: str,
        *,
        base_url: str,
    ) -> list[SearchResult]:
        """Parse le HTML de résultats de recherche Madara.

        Args:
            html: HTML brut.
            base_url: URL de la page (pour résolution relative).

        Returns:
            Liste de :class:`SearchResult`.
        """
        tree = HTMLParser(html)
        results: list[SearchResult] = []
        for node in self._madara_all(tree, self.madara_search_item_selector):
            link_node = node.css_first(self.madara_search_link_selector)
            href = self._madara_attr(link_node, "href")
            if not href:
                continue
            abs_url = self._madara_abs(href, base_url)

            title_node = node.css_first(self.madara_search_title_selector)
            title = self._madara_text(title_node) or self._madara_attr(
                link_node, "title"
            )
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
            "ToonGod search: {n} résultat(s)", n=len(results)
        )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga ToonGod.

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
        else:
            base = self.base_url.rstrip("/")
            url = f"{base}{self.madara_series_path}/{url_or_id.strip('/')}/"

        self.logger.debug("ToonGod get_manga: {url}", url=url)

        try:
            response = await self.cf_get(url)
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_manga ToonGod pour {url!r}: {err}",
                url=url,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {url!r}: {exc}"
            ) from exc

        if response.status_code == 404:
            raise MangaNotFoundError(f"Manga introuvable : {url}")
        if response.status_code >= 400:
            raise ParseError(
                f"HTTP {response.status_code} sur {url} ({self.site_id!r})"
            )

        html = response.text
        tree = HTMLParser(html)

        title_node = self._madara_first(tree, self.madara_manga_title_selector)
        title = self._madara_text(title_node)
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        cover_node = self._madara_first(tree, self.madara_manga_cover_selector)
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

        author_node = self._madara_first(tree, self.madara_manga_author_selector)
        author = self._madara_text(author_node) or None

        artist_node = self._madara_first(tree, self.madara_manga_artist_selector)
        artist = self._madara_text(artist_node) or None

        genre_nodes = self._madara_all(tree, self.madara_manga_genres_selector)
        genres: list[str] = []
        for node in genre_nodes:
            text = self._madara_text(node)
            if text and text not in genres:
                genres.append(text)

        status_node = self._madara_first(
            tree, self.madara_manga_status_selector
        )
        status = self._madara_parse_status(self._madara_text(status_node))

        year_node = self._madara_first(tree, self.madara_manga_year_selector)
        year = self._madara_parse_year(self._madara_text(year_node))

        source_id = self._madara_extract_series_slug(url)
        rating = self._madara_detect_rating(genres)

        chapters = self._madara_parse_chapters_from_html(html, base_url=url)

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
            "ToonGod get_manga OK: {title} ({n} chapitres)",
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
            "ToonGod get_chapters: {title}", title=manga.title
        )
        try:
            html = await self.cf_get_html(str(manga.url))
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_chapters ToonGod pour {title!r}: {err}",
                title=manga.title,
                err=exc,
            )
            raise ParseError(
                f"get_chapters échoué sur {self.site_id!r} "
                f"pour {manga.title!r}: {exc}"
            ) from exc

        chapters = self._madara_parse_chapters_from_html(
            html, base_url=str(manga.url)
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
            html: HTML de la page manga.
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

            # Extrait l'ID depuis l'URL : /webtoon/{slug}/chapter-{num}/
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
            num = ch.number if isinstance(ch.number, (int, float)) else 0.0
            return (
                int(isinstance(ch.number, str)),
                float(num),
                ch.title.lower(),
            )

        chapters.sort(key=_sort_key)
        self.logger.debug(
            "ToonGod chapitres: {n}", n=len(chapters)
        )
        return chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre ToonGod.

        Sélecteur Madara : ``div.reading-content div.page-break.no-gaps img``
        avec l'attribut ``data-src``. Les images sont servies depuis
        ``cdn.toongod.com`` et nécessitent le header ``Referer`` correct.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "ToonGod get_pages: {url}", url=str(chapter.url)
        )
        chapter_url = str(chapter.url)

        try:
            html = await self.cf_get_html(chapter_url)
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_pages ToonGod pour {url!r}: {err}",
                url=chapter_url,
                err=exc,
            )
            raise ParseError(
                f"get_pages échoué sur {self.site_id!r} "
                f"pour {chapter_url!r}: {exc}"
            ) from exc

        # Madara : les pages sont directement dans le HTML.
        tree = HTMLParser(html)
        urls: list[str] = []
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

        if not urls:
            # Tentative fallback : variable JS.
            import json as _json

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
                f"Aucune page trouvée pour {chapter_url} sur {self.site_id!r}"
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
        return pages

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que ToonGod est accessible.

        Teste le domaine principal puis les domaines de fallback.

        Returns:
            ``True`` si un domaine répond correctement.
        """
        for base in self.fallback_domains:
            try:
                response = await self.cf_get(base, timeout=15.0)
                if response.status_code < 500:
                    self.logger.info(
                        "Health check ToonGod OK: {base} (status={s})",
                        base=base,
                        s=response.status_code,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Health check ToonGod échec sur {base}: {err}",
                    base=base,
                    err=exc,
                )
        self.logger.warning("Health check ToonGod KO (tous domaines)")
        return False
