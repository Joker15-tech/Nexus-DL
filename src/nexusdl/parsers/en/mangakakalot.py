"""Parseur Mangakakalot pour NexusDL.

Mangakakalot (``https://www.mangakakalot.gg``) est un agrégateur anglophone
majeur de mangas, manhwas et manhuas, faisant partie de la même famille que
**Manganato** (``manganato.gg``), **Mangabat** (``mangabat.com``) et
**Mangairo** (``mangairo.com``). Ces sites partagent le même moteur de rendu
(MangaBox Family) et sont exploités par le même opérateur.

Caractéristiques techniques
---------------------------

* **Moteur** : MangaBox Family — la structure du DOM est héritée du thème
  Manganelo/Mangakakalot original, avec une section ``tags`` unique sur
  Mangakakalot pour distinguer les « marques » (Nato, Kakalot, Bat, Airo).
* **Langue** : Anglais (``en``).
* **Protection** : Cloudflare (AS13335, IP ``104.21.16.85`` /
  ``172.67.172.115``) avec challenge de niveau élevé. Les utilisateurs
  rapportent des erreurs **1020** fréquentes et des blocages même avec un
  navigateur (contournement nécessaire).
* **Domaines** : ``mangakakalot.gg`` (principal actuel), ``mangakakalot.com``
  (ancien, redirige), ``manganato.gg`` (miroir), ``nelomanga.com``
  (rebranding), ``natomanga.com`` (nouveau), ``mangakakalove.com`` (miroir).
* **Structure des URLs** :
    - Recherche : ``/search/story/{slug}`` (slug avec underscores).
    - Manga : ``/manga/{slug}``.
    - Chapitre : ``/chapter/{slug}/chapter_{num}``.
* **Images** : servies depuis un CDN protégé par hotlink, nécessitant le
  header ``Referer`` pointant vers le site d'origine.
* **Objet JS ``ts_reader``** : les pages sont exposées dans un objet JavaScript
  ``ts_reader`` (format ``{"sources":[{"images":[...]}]}``) injecté dans le
  HTML — c'est la méthode d'extraction principale utilisée par Hakuneko et
  les extensions Tachiyomi.

Le parseur combine trois mixins :

1. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — rendu
   Playwright obligatoire (site protégé par Cloudflare et images en lazy-load).
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — bypass
   Cloudflare automatique (Playwright / FlareSolverr).
3. :class:`~nexusdl.parsers.mixins.api_based.ApiBasedMixin` — client REST
   pour les endpoints JSON internes si disponibles.

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.en.mangakakalot import MangakakalotParser
    >>>
    >>> parser = MangakakalotParser(config, session)
    >>> results = await parser.search("one piece")
    >>> manga = await parser.get_manga(results[0].url)
    >>> chapters = await parser.get_chapters(manga)
    >>> pages = await parser.get_pages(chapters[0])
"""

from __future__ import annotations

import json as _json
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
from nexusdl.parsers.mixins.api_based import ApiBasedMixin
from nexusdl.parsers.mixins.cloudflare import CloudflareMixin
from nexusdl.parsers.mixins.js_rendered import JsRenderedMixin

__all__ = ["MangakakalotParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://www.mangakakalot.gg"
_FALLBACK_DOMAINS: Final[tuple[str, ...]] = (
    "https://www.mangakakalot.gg",
    "https://www.mangakakalot.com",
    "https://www.manganato.gg",
    "https://www.nelomanga.com",
    "https://www.natomanga.com",
    "https://www.mangakakalove.com",
)
_SITE_ID: Final[str] = "mangakakalot"
_LANGUAGE: Final[Language] = Language.EN
_ADULT: Final[bool] = False

# Regex de parsing des chapitres (format MangaBox : "Chapter 1090").
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

# Regex pour l'extraction de l'objet JS ts_reader.
_TS_READER_RE: Final[re.Pattern[str]] = re.compile(
    r"ts_reader\.run\(\s*(\{.*?\})\s*\)", re.DOTALL
)
_TS_READER_JSON_RE: Final[re.Pattern[str]] = re.compile(
    r'"(?:url|image|src)"\s*:\s*"([^"]+)"', re.IGNORECASE
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
# Sélecteurs MangaBox (Mangakakalot/Manganato)
# ---------------------------------------------------------------------------

_MK_SEARCH_ITEM_SELECTOR: Final[str] = (
    "div.story_item, "
    ".story_item, "
    "div.list-story-item, "
    ".panel_story_list .story_item"
)
_MK_SEARCH_LINK_SELECTOR: Final[str] = "a"
_MK_SEARCH_COVER_SELECTOR: Final[str] = "img"
_MK_SEARCH_TITLE_SELECTOR: Final[str] = (
    ".story_name, h3, h4, .title"
)

_MK_MANGA_TITLE_SELECTOR: Final[str] = (
    "h1, .story-info-right h1, .manga-title, .entry-title"
)
_MK_MANGA_COVER_SELECTOR: Final[str] = (
    ".story-info-left img, .manga-cover img, .cover img, "
    ".info-image img"
)
_MK_MANGA_DESCRIPTION_SELECTOR: Final[str] = (
    ".panel-story-info-description, .story-info-right .panel-story-info-description, "
    ".description, .summary"
)
_MK_MANGA_AUTHOR_SELECTOR: Final[str] = (
    ".story-info-right .table-value a, "
    "a[href*='/author/'], .author"
)
_MK_MANGA_ARTIST_SELECTOR: Final[str] = (
    ".story-info-right .table-value a[href*='/artist/'], .artist"
)
_MK_MANGA_GENRES_SELECTOR: Final[str] = (
    ".story-info-right .table-value a[href*='/genre/'], "
    ".genres a, a[href*='/genre/']"
)
_MK_MANGA_STATUS_SELECTOR: Final[str] = (
    ".story-info-right .table-value, .manga-status, .status"
)

_MK_CHAPTER_SELECTOR: Final[str] = (
    "ul.row-content-chapter li a, "
    ".row-content-chapter li a, "
    ".chapter-list a, "
    "a[href*='/chapter/']"
)
_MK_PAGE_IMG_SELECTOR: Final[str] = (
    ".container-chapter-reader img, "
    ".chapter-content img, "
    "#chapter-content img, "
    ".reading-content img"
)
_MK_PAGES_VAR_NAMES: Final[tuple[str, ...]] = (
    "ts_reader",
    "pages",
    "page_urls",
    "pageUrls",
    "images",
)


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


class MangakakalotParser(
    JsRenderedMixin,
    CloudflareMixin,
    ApiBasedMixin,
    BaseParser,
):
    """Parseur Mangakakalot (MangaBox + Cloudflare).

    Combine le rendu Playwright (obligatoire — le site est protégé par
    Cloudflare et utilise du lazy-loading d'images), le contournement
    Cloudflare et un client API REST pour couvrir l'ensemble des cas
    d'usage.

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

    # --- Chemins Mangakakalot ---
    mk_search_path: ClassVar[str] = "/search/story"
    mk_series_path: ClassVar[str] = "/manga"
    mk_read_path: ClassVar[str] = "/chapter"

    # --- Sélecteurs Mangakakalot ---
    mk_search_item_selector: ClassVar[str] = _MK_SEARCH_ITEM_SELECTOR
    mk_search_link_selector: ClassVar[str] = _MK_SEARCH_LINK_SELECTOR
    mk_search_cover_selector: ClassVar[str] = _MK_SEARCH_COVER_SELECTOR
    mk_search_title_selector: ClassVar[str] = _MK_SEARCH_TITLE_SELECTOR

    mk_manga_title_selector: ClassVar[str] = _MK_MANGA_TITLE_SELECTOR
    mk_manga_cover_selector: ClassVar[str] = _MK_MANGA_COVER_SELECTOR
    mk_manga_description_selector: ClassVar[str] = _MK_MANGA_DESCRIPTION_SELECTOR
    mk_manga_author_selector: ClassVar[str] = _MK_MANGA_AUTHOR_SELECTOR
    mk_manga_artist_selector: ClassVar[str] = _MK_MANGA_ARTIST_SELECTOR
    mk_manga_genres_selector: ClassVar[str] = _MK_MANGA_GENRES_SELECTOR
    mk_manga_status_selector: ClassVar[str] = _MK_MANGA_STATUS_SELECTOR

    mk_chapter_selector: ClassVar[str] = _MK_CHAPTER_SELECTOR
    mk_page_img_selector: ClassVar[str] = _MK_PAGE_IMG_SELECTOR
    mk_pages_var_names: ClassVar[tuple[str, ...]] = _MK_PAGES_VAR_NAMES

    # --- Comportement ---
    mk_requires_js: ClassVar[bool] = True  # Site protégé par Cloudflare
    mk_default_rating: ClassVar[ContentRating] = ContentRating.SAFE

    # --- Configuration API ---
    api_base_url: ClassVar[str] = _BASE_URL
    api_default_headers: ClassVar[dict[str, str]] = {
        "Accept": "application/json",
        "X-Referer": _BASE_URL,
        "Origin": _BASE_URL,
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
    default_wait_until: ClassVar[str] = "networkidle"
    default_render_timeout: ClassVar[float] = 45.0
    default_navigation_timeout: ClassVar[float] = 60.0
    block_resources_by_default: ClassVar[bool] = False  # Les images sont nécessaires
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
        """Initialise le parseur Mangakakalot.

        Args:
            config: Configuration du site (:class:`SiteConfig`).
            session: Session HTTP async du projet (:class:`HttpSession`).
            playwright_pool: Pool Playwright partagé (requis — le site est
                protégé par Cloudflare et utilise du lazy-loading).
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

        # Injecte le header Referer requis par le CDN d'images.
        self._inject_cdn_referer()

    def _inject_cdn_referer(self) -> None:
        """Injecte le header ``Referer`` requis par le CDN d'images.

        Le CDN d'images de Mangakakalot est protégé par hotlink et exige
        un header ``Referer`` pointant vers le site d'origine.
        """
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

    def _mk_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="mangakakalot"``.
        """
        return self.logger

    @staticmethod
    def _mk_clean(value: str | None) -> str:
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
    def _mk_text(node: Node | None) -> str:
        """Extrait et nettoie le texte d'un nœud.

        Args:
            node: Nœud selectolax ou ``None``.

        Returns:
            Texte nettoyé.
        """
        if node is None:
            return ""
        return MangakakalotParser._mk_clean(
            node.text(deep=True, separator=" ")
        )

    @staticmethod
    def _mk_attr(node: Node | None, name: str) -> str:
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

    def _mk_first(self, tree: HTMLParser, selector: str) -> Node | None:
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

    def _mk_all(self, tree: HTMLParser, selector: str) -> list[Node]:
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

    def _mk_abs(self, url: str, base: str | None = None) -> str:
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

    def _mk_parse_status(self, raw: str | None) -> MangaStatus:
        """Convertit un libellé de statut en énumération NexusDL.

        Args:
            raw: Libellé brut.

        Returns:
            Statut normalisé (``ONGOING`` par défaut).
        """
        if not raw:
            return MangaStatus.ONGOING
        key = self._mk_clean(raw).lower()
        for needle, status in _STATUS_MAP.items():
            if needle in key:
                return status
        return MangaStatus.ONGOING

    @staticmethod
    def _mk_parse_year(raw: str | None) -> int | None:
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
    def _mk_parse_relative_date(raw: str | None) -> datetime | None:
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
    def _mk_chapter_number(text: str) -> float | str:
        """Extrait le numéro de chapitre d'un libellé.

        Args:
            text: Libellé (ex. ``"Chapter 1090"``).

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = MangakakalotParser._mk_clean(text)
        match = _CHAPTER_NUMBER_RE.search(cleaned)
        if not match:
            return cleaned
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return cleaned

    @staticmethod
    def _mk_chapter_volume(text: str) -> int | None:
        """Extrait le numéro de tome d'un libellé.

        Args:
            text: Libellé contenant éventuellement ``Volume X``.

        Returns:
            Numéro de tome ou ``None``.
        """
        match = _VOLUME_RE.search(text or "")
        return int(match.group(1)) if match else None

    def _mk_detect_rating(self, genres: list[str]) -> ContentRating:
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
        return self.mk_default_rating

    # ------------------------------------------------------------------
    # Extraction de l'objet JS ts_reader
    # ------------------------------------------------------------------

    @staticmethod
    def _mk_extract_ts_reader(html: str) -> list[str]:
        """Extrait les URLs d'images depuis l'objet JS ``ts_reader``.

        Mangakakalot (MangaBox Family) injecte les pages du chapitre dans un
        objet JavaScript ``ts_reader`` de la forme :
        ``ts_reader.run({"sources":[{"images":["url1","url2",...]}]})``.
        C'est la méthode d'extraction principale utilisée par Hakuneko et
        les extensions Tachiyomi.

        Args:
            html: HTML de la page chapitre.

        Returns:
            Liste ordonnée d'URLs d'images (vide si introuvable).
        """
        match = _TS_READER_RE.search(html)
        if not match:
            return []

        raw = match.group(1)
        try:
            parsed = _json.loads(raw)
        except ValueError:
            # Fallback : extraction par regex des URLs.
            return _TS_READER_JSON_RE.findall(raw)

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

        _walk(parsed)
        return urls

    # ------------------------------------------------------------------
    # Méthodes abstraites de BaseParser
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des mangas sur Mangakakalot.

        Mangakakalot utilise ``/search/story/{slug}`` avec le slug en
        minuscules et séparé par des underscores (convention MangaBox
        Family).

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.

        Raises:
            ParseError: Si le HTML de résultats est inexploitable.
        """
        self.logger.debug(
            "Mangakakalot search: {query} (page {page})",
            query=query,
            page=page,
        )

        # MangaBox Family : slug en minuscules avec underscores.
        slug = (
            query.strip().lower()
            .replace(" ", "_")
            .replace("-", "_")
        )
        url = f"{self.base_url}{self.mk_search_path}/{quote_plus(slug)}"
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
                "Échec recherche Mangakakalot pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        return self._mk_parse_search_html(
            rendered.html, base_url=rendered.final_url or url
        )

    def _mk_parse_search_html(
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

        for node in self._mk_all(tree, self.mk_search_item_selector):
            link_node: Node | None = None
            if node.tag == "a" and "/manga/" in self._mk_attr(node, "href"):
                link_node = node
            else:
                for candidate in node.css("a"):
                    if "/manga/" in self._mk_attr(candidate, "href"):
                        link_node = candidate
                        break

            if link_node is None:
                continue

            href = self._mk_attr(link_node, "href")
            if not href:
                continue
            abs_url = self._mk_abs(href, base_url)
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            title_node = node.css_first(self.mk_search_title_selector)
            title = self._mk_text(title_node) or self._mk_attr(
                link_node, "title"
            )
            if not title:
                title = self._mk_attr(link_node, "href").rstrip("/").rsplit("/", 1)[-1]
                title = title.replace("_", " ").replace("-", " ").title()
            if not title:
                continue

            cover_node = node.css_first(self.mk_search_cover_selector)
            cover_src = (
                self._mk_attr(cover_node, "data-src")
                or self._mk_attr(cover_node, "src")
            )
            cover_url = self._mk_abs(cover_src, base_url) if cover_src else None

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
            "Mangakakalot search: {n} résultat(s)", n=len(results)
        )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga Mangakakalot.

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
            url = f"{self.base_url}{self.mk_series_path}/{url_or_id.strip('/')}"

        self.logger.debug("Mangakakalot get_manga: {url}", url=url)

        try:
            rendered = await self.fetch_rendered(
                url,
                wait_until="networkidle",
                wait_for_selector="h1, .story-info-right h1, .manga-title",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_manga Mangakakalot pour {url!r}: {err}",
                url=url,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {url!r}: {exc}"
            ) from exc

        html = rendered.html
        tree = HTMLParser(html)

        title_node = self._mk_first(tree, self.mk_manga_title_selector)
        title = self._mk_text(title_node)
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        cover_node = self._mk_first(tree, self.mk_manga_cover_selector)
        cover_src = (
            self._mk_attr(cover_node, "data-src")
            or self._mk_attr(cover_node, "src")
        )
        cover_url = self._mk_abs(cover_src, url) if cover_src else None

        description_node = self._mk_first(
            tree, self.mk_manga_description_selector
        )
        description = self._mk_text(description_node) or None

        author_node = self._mk_first(tree, self.mk_manga_author_selector)
        author = self._mk_text(author_node) or None

        artist_node = self._mk_first(tree, self.mk_manga_artist_selector)
        artist = self._mk_text(artist_node) or None

        genre_nodes = self._mk_all(tree, self.mk_manga_genres_selector)
        genres: list[str] = []
        for node in genre_nodes:
            text = self._mk_text(node)
            if text and text not in genres:
                genres.append(text)

        status_node = self._mk_first(tree, self.mk_manga_status_selector)
        status = self._mk_parse_status(self._mk_text(status_node))

        source_id = self._mk_extract_series_slug(url)
        rating = self._mk_detect_rating(genres)

        chapters = self._mk_parse_chapters_from_html(html, base_url=url)

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
            "Mangakakalot get_manga OK: {title} ({n} chapitres)",
            title=title,
            n=len(chapters),
        )
        return manga

    @staticmethod
    def _mk_extract_series_slug(url: str) -> str:
        """Extrait un identifiant de série depuis une URL Mangakakalot.

        Format : ``/manga/{slug}`` → retourne ``{slug}``.

        Args:
            url: URL de la série.

        Returns:
            Slug nettoyé.
        """
        parts = [p for p in urlparse(url).path.split("/") if p]
        if "manga" in parts:
            idx = parts.index("manga")
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
            "Mangakakalot get_chapters: {title}", title=manga.title
        )

        try:
            rendered = await self.fetch_rendered(
                str(manga.url),
                wait_until="networkidle",
                wait_for_selector="ul.row-content-chapter li a, .chapter-list a",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_chapters Mangakakalot pour {title!r}: {err}",
                title=manga.title,
                err=exc,
            )
            raise ParseError(
                f"get_chapters échoué sur {self.site_id!r} "
                f"pour {manga.title!r}: {exc}"
            ) from exc

        chapters = self._mk_parse_chapters_from_html(
            rendered.html, base_url=rendered.final_url or str(manga.url)
        )
        if not chapters:
            raise ChapterNotFoundError(
                f"Aucun chapitre trouvé pour {manga.url} sur {self.site_id!r}"
            )
        return chapters

    def _mk_parse_chapters_from_html(
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

        for node in self._mk_all(tree, self.mk_chapter_selector):
            href = self._mk_attr(node, "href")
            if not href or "/chapter/" not in href:
                continue
            abs_url = self._mk_abs(href, base_url)
            if abs_url in seen:
                continue
            seen.add(abs_url)

            label = self._mk_text(node) or self._mk_attr(node, "title")
            if not label:
                label = abs_url.rstrip("/").rsplit("/", 1)[-1] or abs_url

            number = self._mk_chapter_number(label)
            volume = self._mk_chapter_volume(label)

            date_text = self._mk_attr(node, "data-date") or None
            published = (
                self._mk_parse_relative_date(date_text)
                if date_text
                else self._mk_parse_relative_date(label)
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
            "Mangakakalot chapitres: {n}", n=len(chapters)
        )
        return chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre Mangakakalot.

        Le site est protégé par Cloudflare et utilise du lazy-loading
        d'images : le rendu Playwright est nécessaire. Les pages sont
        extraites en priorité depuis l'objet JS ``ts_reader`` (méthode
        Hakuneko/Tachiyomi), avec fallback sur les sélecteurs CSS.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "Mangakakalot get_pages: {url}", url=str(chapter.url)
        )
        chapter_url = str(chapter.url)

        try:
            rendered = await self.fetch_rendered(
                chapter_url,
                wait_until="networkidle",
                wait_for_selector=".container-chapter-reader img, .chapter-content img",
                timeout=45.0,
                bypass_cloudflare=True,
                block_resources=False,  # Les images sont nécessaires
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_pages Mangakakalot pour {url!r}: {err}",
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

        # Priorité 1 : objet JS ts_reader (méthode fiable).
        ts_urls = self._mk_extract_ts_reader(html)
        if ts_urls:
            for u in ts_urls:
                abs_url = self._mk_abs(u, chapter_url)
                if abs_url not in urls:
                    urls.append(abs_url)

        # Priorité 2 : sélecteur CSS fallback.
        if not urls:
            for node in self._mk_all(tree, self.mk_page_img_selector):
                src = (
                    self._mk_attr(node, "data-src")
                    or self._mk_attr(node, "data-lazy-src")
                    or self._mk_attr(node, "data-original")
                    or self._mk_attr(node, "src")
                )
                if not src:
                    continue
                abs_url = self._mk_abs(src, chapter_url)
                if abs_url not in urls:
                    urls.append(abs_url)

        # Priorité 3 : variable JS embarquée.
        if not urls:
            for var_name in self.mk_pages_var_names:
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
            clean = self._mk_abs(clean, chapter_url)
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
            "Mangakakalot get_pages: {n} page(s) extraite(s)", n=len(pages)
        )
        return pages

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que Mangakakalot est accessible.

        Teste le domaine principal puis les domaines de fallback (la famille
        MangaBox change régulièrement de domaine).

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
                        "Health check Mangakakalot OK: {base} (status={s})",
                        base=base,
                        s=rendered.status,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Health check Mangakakalot échec sur {base}: {err}",
                    base=base,
                    err=exc,
                )
        self.logger.warning("Health check Mangakakalot KO (tous domaines)")
        return False
