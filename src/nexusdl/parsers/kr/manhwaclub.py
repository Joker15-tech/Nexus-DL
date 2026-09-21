"""Parseur ManhwaClub pour NexusDL.

ManhwaClub (``https://manhwaclub.net``) est un site de lecture de **manhwas**
coréens et **manhuas** chinois en anglais, avec un accent particulier sur le
contenu **NSFW/adult** (pornhwa). Le site est accessible gratuitement et
héberge plusieurs milliers de titres.

⚠️ **Site à évolution rapide** : ManhwaClub a subi plusieurs migrations
architecturales :

* **Phase 1 (historique)** : thème WordPress **Madara** (Mangabooth).
* **Phase 2 (2022)** : migration vers une **custom API** interne
  (``/api/comics``) — le thème Madara a été abandonné au profit d'un
  frontend JS custom.
* **Phase 3 (2024+)** : redirections fréquentes vers ``manhwahentai.to``
  (le domaine ``manhwa.club`` redirige désormais vers un réseau adulte).

Ce parseur implémente une **stratégie hybride** :

1. **API interne** (``/api/comics``) — priorité 1, rapide et structurée.
2. **Rendu Playwright** — priorité 2, avec parsing DOM.
3. **Fallback Madara** — priorité 3, si le site revient à son ancien thème.

Caractéristiques techniques
---------------------------

* **Moteur actuel** : custom API JS (post-Madara) — endpoints ``/api/comics``.
* **Moteur historique** : WordPress Madara (fallback).
* **Langue** : Anglais (``en``) + Coréen (``ko``) + Japonais (``ja``).
* **Protection** : Cloudflare (AS13335, IP ``172.67.177.225`` /
  ``188.114.97.3``) avec challenge **Managed Challenge**.
* **Domaines** : ``manhwaclub.net`` (principal actuel),
  ``manhwa.club`` (historique, redirige), ``manhwahentai.to`` (nouveau
  domaine adulte).
* **Structure des URLs** (post-Madara) :
    - Catalogue : ``/`` ou ``/api/comics`` (JSON).
    - Manga : ``/manga/{slug}/``.
    - Chapitre : ``/manga/{slug}/{chapter-slug}/`` ou ``/view/{id}``.
* **Contenu adulte** : site 18+/NSFW (pornhwa, ecchi, hentai).
* **API interne** : endpoints ``/api/comics`` et ``/api/comics/{id}`` pour
  les métadonnées et les pages de chapitre.
* **Images** : servies depuis des CDN (``bkcdn.net``, ``magsrv.com``),
  nécessitant le header ``Referer`` pointant vers ``https://manhwaclub.net/``.

Le parseur combine quatre mixins :

1. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — rendu
   Playwright obligatoire (SPA custom + Cloudflare).
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — bypass
   Cloudflare automatique (Playwright / FlareSolverr).
3. :class:`~nexusdl.parsers.mixins.api_based.ApiBasedMixin` — client REST
   pour l'API interne ``/api/comics``.
4. :class:`~nexusdl.parsers.mixins.madara.MadaraMixin` — fallback si le site
   revient au thème Madara.

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.kr.manhwaclub import ManhwaClubParser
    >>>
    >>> parser = ManhwaClubParser(config, session)
    >>> results = await parser.search("solo leveling")
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
from nexusdl.parsers.mixins.madara import MadaraMixin

__all__ = ["ManhwaClubParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://manhwaclub.net"
_FALLBACK_DOMAINS: Final[tuple[str, ...]] = (
    "https://manhwaclub.net",
    "https://manhwa.club",
    "https://manhwahentai.to",
)
_SITE_ID: Final[str] = "manhwaclub"
_LANGUAGE: Final[Language] = Language.EN
_ADULT: Final[bool] = True  # Site 18+/NSFW (pornhwa)

# User-Agent identifiable (le site bloque les UA par défaut).
_USER_AGENT: Final[str] = (
    "NexusDL/1.0 (https://github.com/nexusdl/nexusdl)"
)

# API interne (post-Madara).
_API_PATH: Final[str] = "/api/comics"

# Regex de parsing des chapitres (format ManhwaClub : "Chapter 45").
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
        "pornhwa",
        "erotic",
        "erotica",
        "yaoi",
        "yuri",
        "gender bender",
        "pornographic",
        "nsfw",
    }
)

# Marqueurs de contenu suggestif.
_SUGGESTIVE_MARKERS: Final[frozenset[str]] = frozenset(
    {"ecchi", "mature", "harem", "suggestive", "romance", "action"}
)

# ---------------------------------------------------------------------------
# Sélecteurs ManhwaClub (Madara fallback + custom JS)
# ---------------------------------------------------------------------------

_MC_SEARCH_ITEM_SELECTOR: Final[str] = (
    # Sélecteurs Madara (fallback).
    ".page-item-detail.manga, .page-item-detail, "
    ".c-tabs-item__content, .bs, .bsx, "
    ".manga-item, .item-summary, "
    # Sélecteurs custom JS.
    "div.comic-item, div.manga-card, "
    "a[href*='/manga/'], "
    "div.list-item a[href*='/manga/']"
)
_MC_SEARCH_LINK_SELECTOR: Final[str] = "a"
_MC_SEARCH_COVER_SELECTOR: Final[str] = "img"
_MC_SEARCH_TITLE_SELECTOR: Final[str] = (
    ".item-summary .post-title, .post-title, h3, h4, "
    ".comic-title, .manga-title, .title"
)

_MC_MANGA_TITLE_SELECTOR: Final[str] = (
    "h1, .entry-title, .post-title, .manga-title, "
    ".comic-title, .title"
)
_MC_MANGA_COVER_SELECTOR: Final[str] = (
    ".summary_image img, .thumb img, .manga-cover img, "
    ".cover img, .comic-cover img"
)
_MC_MANGA_DESCRIPTION_SELECTOR: Final[str] = (
    ".summary__content, .description, .manga-summary, "
    ".entry-content p, .c-content, .comic-description"
)
_MC_MANGA_AUTHOR_SELECTOR: Final[str] = (
    ".author, .manga-author, a[href*='/author/'], "
    ".post-content_item .author-content a"
)
_MC_MANGA_ARTIST_SELECTOR: Final[str] = (
    ".artist, .manga-artist, a[href*='/artist/'], "
    ".post-content_item .artist-content a"
)
_MC_MANGA_GENRES_SELECTOR: Final[str] = (
    ".genres a, .manga-genres a, a[href*='/genre/'], "
    "a[href*='/tag/'], .wp-manga-tags-list a, .comic-genres a"
)
_MC_MANGA_STATUS_SELECTOR: Final[str] = (
    ".status, .manga-status, .post-status, "
    ".post-content_item .summary-content"
)
_MC_MANGA_YEAR_SELECTOR: Final[str] = (
    ".year, .manga-year, .post-year, "
    ".post-content_item .summary-content"
)

_MC_CHAPTER_SELECTOR: Final[str] = (
    # Sélecteurs Madara (fallback).
    "div.listing-chapters_wrap > ul li, "
    ".listing-chapters_wrap li, "
    ".eplister li:not(.ch-price-side), "
    ".wp-manga-chapter, "
    # Sélecteurs custom JS.
    "a[href*='/chapter/'], "
    "div.chapter-item a, "
    "li.chapter a"
)
_MC_PAGE_IMG_SELECTOR: Final[str] = (
    "div.reading-content div.page-break.no-gaps img, "
    ".reading-content img, "
    ".page-break img, "
    "#readerarea img, "
    ".main-reading-area img, "
    ".chapter-image img, "
    ".viewer img"
)
_MC_PAGES_VAR_NAMES: Final[tuple[str, ...]] = (
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


class ManhwaClubParser(
    JsRenderedMixin,
    CloudflareMixin,
    ApiBasedMixin,
    MadaraMixin,
    BaseParser,
):
    """Parseur ManhwaClub (custom API + Madara fallback + Cloudflare).

    Combine le rendu Playwright (obligatoire — SPA custom + Cloudflare),
    le contournement Cloudflare, un client REST pour l'API interne
    ``/api/comics`` et les implémentations Madara (fallback historique).

    Attributes:
        site_id: Identifiant interne du site.
        language: Langue principale (anglais).
        adult: Contenu adulte (``True`` — site 18+/NSFW).
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

    # --- Configuration Madara (fallback) ---
    madara_base_url: ClassVar[str] = _BASE_URL
    madara_search_path: ClassVar[str] = "/"
    madara_search_query_param: ClassVar[str] = "s"
    madara_search_method: ClassVar[str] = "GET"
    madara_series_path: ClassVar[str] = "/manga"
    madara_read_path: ClassVar[str] = "/manga"

    # --- Sélecteurs Madara (fallback) ---
    madara_search_item_selector: ClassVar[str] = _MC_SEARCH_ITEM_SELECTOR
    madara_search_link_selector: ClassVar[str] = (
        ".item-summary .post-title a, .post-title a, a"
    )
    madara_search_cover_selector: ClassVar[str] = "img"
    madara_search_title_selector: ClassVar[str] = (
        ".item-summary .post-title, .post-title, h3, h4"
    )
    madara_search_author_selector: ClassVar[str] = ".author, .artist"

    madara_manga_title_selector: ClassVar[str] = _MC_MANGA_TITLE_SELECTOR
    madara_manga_cover_selector: ClassVar[str] = _MC_MANGA_COVER_SELECTOR
    madara_manga_description_selector: ClassVar[str] = _MC_MANGA_DESCRIPTION_SELECTOR
    madara_manga_author_selector: ClassVar[str] = _MC_MANGA_AUTHOR_SELECTOR
    madara_manga_artist_selector: ClassVar[str] = _MC_MANGA_ARTIST_SELECTOR
    madara_manga_genres_selector: ClassVar[str] = _MC_MANGA_GENRES_SELECTOR
    madara_manga_status_selector: ClassVar[str] = _MC_MANGA_STATUS_SELECTOR
    madara_manga_year_selector: ClassVar[str] = _MC_MANGA_YEAR_SELECTOR

    madara_chapter_selector: ClassVar[str] = _MC_CHAPTER_SELECTOR
    madara_page_img_selector: ClassVar[str] = _MC_PAGE_IMG_SELECTOR
    madara_pages_var_names: ClassVar[tuple[str, ...]] = _MC_PAGES_VAR_NAMES

    # --- Comportement Madara ---
    madara_requires_js: ClassVar[bool] = True
    madara_api_pages_endpoint: ClassVar[str | None] = None
    madara_default_rating: ClassVar[ContentRating] = ContentRating.EROTICA
    madara_concurrent_chapter_requests: ClassVar[int] = 4
    madara_search_pages_limit: ClassVar[int] = 20

    # --- Configuration API (custom /api/comics) ---
    api_base_url: ClassVar[str] = _BASE_URL
    api_default_headers: ClassVar[dict[str, str]] = {
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
        "Referer": _BASE_URL,
        "Origin": _BASE_URL,
    }
    api_rate_limit_per_second: ClassVar[float] = 2.0
    api_rate_limit_burst: ClassVar[int] = 3
    api_timeout: ClassVar[float] = 20.0
    api_cache_enabled: ClassVar[bool] = True
    api_cache_ttl: ClassVar[int] = 300
    api_raise_on_error_status: ClassVar[bool] = True

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
    ) -> None:
        """Initialise le parseur ManhwaClub.

        Args:
            config: Configuration du site (:class:`SiteConfig`).
            session: Session HTTP async du projet (:class:`HttpSession`).
            playwright_pool: Pool Playwright partagé (requis — SPA custom).
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
    # Helpers internes
    # ------------------------------------------------------------------

    def _mc_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="manhwaclub"``.
        """
        return self.logger

    @staticmethod
    def _mc_clean(value: str | None) -> str:
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
    def _mc_text(node: Node | None) -> str:
        """Extrait et nettoie le texte d'un nœud.

        Args:
            node: Nœud selectolax ou ``None``.

        Returns:
            Texte nettoyé.
        """
        if node is None:
            return ""
        return ManhwaClubParser._mc_clean(
            node.text(deep=True, separator=" ")
        )

    @staticmethod
    def _mc_attr(node: Node | None, name: str) -> str:
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

    def _mc_first(self, tree: HTMLParser, selector: str) -> Node | None:
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

    def _mc_all(self, tree: HTMLParser, selector: str) -> list[Node]:
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

    def _mc_abs(self, url: str, base: str | None = None) -> str:
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

    def _mc_parse_status(self, raw: str | None) -> MangaStatus:
        """Convertit un libellé de statut en énumération NexusDL.

        Args:
            raw: Libellé brut.

        Returns:
            Statut normalisé (``ONGOING`` par défaut).
        """
        if not raw:
            return MangaStatus.ONGOING
        key = self._mc_clean(raw).lower()
        for needle, status in _STATUS_MAP.items():
            if needle in key:
                return status
        return MangaStatus.ONGOING

    @staticmethod
    def _mc_parse_year(raw: str | None) -> int | None:
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
    def _mc_parse_relative_date(raw: str | None) -> datetime | None:
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
        from datetime import timedelta

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
    def _mc_chapter_number(text: str) -> float | str:
        """Extrait le numéro de chapitre d'un libellé.

        Args:
            text: Libellé (ex. ``"Chapter 45"``).

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = ManhwaClubParser._mc_clean(text)
        match = _CHAPTER_NUMBER_RE.search(cleaned)
        if not match:
            return cleaned
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return cleaned

    @staticmethod
    def _mc_chapter_volume(text: str) -> int | None:
        """Extrait le numéro de tome d'un libellé.

        Args:
            text: Libellé contenant éventuellement ``Volume X``.

        Returns:
            Numéro de tome ou ``None``.
        """
        match = _VOLUME_RE.search(text or "")
        return int(match.group(1)) if match else None

    def _mc_detect_rating(self, genres: list[str]) -> ContentRating:
        """Détermine la classification de contenu depuis les genres.

        ManhwaClub est un site 18+/NSFW (pornhwa). La classification est
        détectée automatiquement à partir des genres.

        Args:
            genres: Liste de genres du manga.

        Returns:
            Classification détectée.
        """
        lowered = {g.lower() for g in genres}
        if lowered & _ADULT_GENRE_MARKERS:
            if {"hentai", "porn", "18+", "explicit", "pornhwa"} & lowered:
                return ContentRating.PORNOGRAPHIC
            if {"smut", "mature", "erotica", "ecchi"} & lowered:
                return ContentRating.EROTICA
        if lowered & _SUGGESTIVE_MARKERS:
            return ContentRating.SUGGESTIVE
        return self.madara_default_rating

    @staticmethod
    def _mc_extract_iso_date(raw: str | None) -> datetime | None:
        """Parse une date ISO 8601.

        Args:
            raw: Chaîne de date RFC 3339.

        Returns:
            Datetime UTC ou ``None``.
        """
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # Construction depuis l'API custom
    # ------------------------------------------------------------------

    def _mc_build_manga_from_api(self, data: dict[str, Any]) -> Manga:
        """Construit un objet :class:`Manga` depuis l'API custom.

        Args:
            data: Entité manga retournée par ``/api/comics``.

        Returns:
            Objet :class:`Manga` hydraté.

        Raises:
            ParseError: Si les données sont inexploitables.
        """
        manga_id = data.get("id")
        slug = data.get("slug") or data.get("url")
        if not manga_id or not slug:
            raise ParseError("Entité manga sans 'id' ou 'slug' dans l'API")

        title = data.get("title") or data.get("name") or "Untitled"

        alt_titles: list[str] = []
        for alt in data.get("alternative_titles") or data.get("alt_titles") or []:
            if isinstance(alt, str) and alt not in alt_titles:
                alt_titles.append(alt)

        description = data.get("description") or data.get("summary")

        author = data.get("author")
        artist = data.get("artist")

        genres: list[str] = []
        for genre in data.get("genres") or data.get("tags") or []:
            if isinstance(genre, str):
                genres.append(genre)
            elif isinstance(genre, dict):
                name = genre.get("name") or genre.get("title")
                if isinstance(name, str) and name not in genres:
                    genres.append(name)

        status_raw = data.get("status")
        if isinstance(status_raw, str):
            status = self._mc_parse_status(status_raw)
        else:
            status = MangaStatus.ONGOING

        year = data.get("year")
        if not isinstance(year, int):
            year = None

        rating = self._mc_detect_rating(genres)

        cover = data.get("cover") or data.get("image") or data.get("thumbnail")
        cover_url = str(cover) if cover else None

        updated_at = (
            self._mc_extract_iso_date(data.get("updated_at"))
            or datetime.now(timezone.utc)
        )

        url = f"{self.base_url}/manga/{slug}"

        return Manga(
            id=f"{self.config.id}:{slug}",
            source_id=str(slug),
            site=self.config.id,
            title=str(title),
            alternative_titles=alt_titles,
            description=str(description) if description else None,
            author=str(author) if author else None,
            artist=str(artist) if artist else None,
            genres=genres,
            status=status,
            year=year,
            cover_url=cover_url,
            language=self.language,
            content_rating=rating,
            chapters=[],
            url=url,
            updated_at=updated_at,
        )

    def _mc_build_chapter_from_api(
        self, data: dict[str, Any], *, manga_slug: str
    ) -> Chapter:
        """Construit un objet :class:`Chapter` depuis l'API custom.

        Args:
            data: Entité chapter retournée par l'API.
            manga_slug: Slug du manga parent.

        Returns:
            Objet :class:`Chapter` hydraté.
        """
        chapter_id = str(data.get("id") or data.get("chapter_id") or "")
        chapter_num_raw = data.get("chapter") or data.get("number") or data.get("name")
        chapter_number = self._mc_chapter_number(str(chapter_num_raw or ""))

        title_raw = data.get("title")
        if title_raw:
            title = str(title_raw)
        else:
            title = f"Chapter {chapter_num_raw or '?'}"

        volume = data.get("volume")
        if not isinstance(volume, int):
            volume = None

        pages_count = data.get("pages")
        if not isinstance(pages_count, int):
            pages_count = None

        published_at = self._mc_extract_iso_date(
            data.get("published_at") or data.get("created_at")
        )

        url = f"{self.base_url}/manga/{manga_slug}/{chapter_id}"

        return Chapter(
            id=f"{self.config.id}:{chapter_id}",
            source_id=chapter_id,
            title=title,
            number=chapter_number,
            volume=volume,
            language=self.language,
            pages_count=pages_count,
            published_at=published_at,
            url=url,
            pages=[],
        )

    # ------------------------------------------------------------------
    # Méthodes abstraites de BaseParser
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des mangas sur ManhwaClub.

        Tente d'abord via l'API interne (``/api/comics?search=...``), puis
        bascule sur le rendu Playwright avec filtrage client en fallback.

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.

        Raises:
            ParseError: Si le HTML de résultats est inexploitable.
        """
        self.logger.debug(
            "ManhwaClub search: {query} (page {page})",
            query=query,
            page=page,
        )

        # Tentative 1 : API interne.
        try:
            payload = await self.api_get(
                _API_PATH,
                params={"search": query, "page": page},
                timeout=20.0,
            )
            if isinstance(payload, dict):
                items = payload.get("data") or payload.get("results") or []
                if isinstance(items, list) and items:
                    results: list[SearchResult] = []
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        try:
                            manga = self._mc_build_manga_from_api(item)
                        except ParseError:
                            continue
                        results.append(
                            SearchResult(
                                title=manga.title,
                                url=manga.url,
                                site_id=self.config.id,
                                cover_url=manga.cover_url,
                                author=manga.author,
                            )
                        )
                    if results:
                        self.logger.info(
                            "ManhwaClub search (API): {n} résultat(s)",
                            n=len(results),
                        )
                        return results
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(
                "API search KO, fallback Playwright : {err}", err=exc
            )

        # Tentative 2 : rendu Playwright + filtrage client.
        url = f"{self.base_url}/?s={quote_plus(query)}"
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
                "Échec recherche ManhwaClub pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        all_results = self._mc_parse_search_html(
            rendered.html, base_url=rendered.final_url or url
        )

        # Filtre côté client.
        query_lower = query.lower().strip()
        filtered = [r for r in all_results if query_lower in r.title.lower()]

        self.logger.info(
            "ManhwaClub search (DOM): {n} résultat(s)", n=len(filtered)
        )
        return filtered

    def _mc_parse_search_html(
        self, html: str, *, base_url: str
    ) -> list[SearchResult]:
        """Parse le HTML de résultats de recherche (fallback Madara/custom).

        Args:
            html: HTML rendu.
            base_url: URL de la page (pour résolution relative).

        Returns:
            Liste de :class:`SearchResult`.
        """
        tree = HTMLParser(html)
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for node in self._mc_all(tree, self.madara_search_item_selector):
            link_node: Node | None = None
            if node.tag == "a" and "/manga/" in self._mc_attr(node, "href"):
                link_node = node
            else:
                for candidate in node.css("a"):
                    if "/manga/" in self._mc_attr(candidate, "href"):
                        link_node = candidate
                        break

            if link_node is None:
                continue

            href = self._mc_attr(link_node, "href")
            if not href:
                continue
            abs_url = self._mc_abs(href, base_url)
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            title_node = node.css_first(self.madara_search_title_selector)
            title = self._mc_text(title_node) or self._mc_attr(
                link_node, "title"
            )
            if not title:
                title = (
                    self._mc_attr(link_node, "href")
                    .rstrip("/")
                    .rsplit("/", 1)[-1]
                )
                title = title.replace("-", " ").title()
            if not title:
                continue

            cover_node = node.css_first(self.madara_search_cover_selector)
            cover_src = (
                self._mc_attr(cover_node, "data-src")
                or self._mc_attr(cover_node, "data-lazy-src")
                or self._mc_attr(cover_node, "src")
            )
            cover_url = (
                self._mc_abs(cover_src, base_url) if cover_src else None
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

        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga ManhwaClub.

        Tente d'abord via l'API interne, puis bascule sur le rendu Playwright.

        Args:
            url_or_id: URL absolue ou slug de la série.

        Returns:
            Objet :class:`Manga` hydraté.

        Raises:
            MangaNotFoundError: Si le manga est inaccessible.
            ParseError: Si le HTML est inexploitable.
        """
        # Extrait le slug.
        if url_or_id.startswith(("http://", "https://")):
            slug = self._mc_extract_series_slug(url_or_id)
        elif url_or_id.startswith("/"):
            slug = self._mc_extract_series_slug(
                f"{self.base_url}{url_or_id}"
            )
        else:
            slug = url_or_id.strip("/")

        self.logger.debug("ManhwaClub get_manga: {slug}", slug=slug)

        # Tentative 1 : API interne.
        try:
            payload = await self.api_get(
                f"{_API_PATH}/{slug}",
                timeout=20.0,
            )
            if isinstance(payload, dict):
                data = payload.get("data") or payload
                if isinstance(data, dict) and data.get("id"):
                    return self._mc_build_manga_from_api(data)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(
                "API get_manga KO, fallback Playwright : {err}", err=exc
            )

        # Tentative 2 : rendu Playwright (fallback Madara/custom).
        url = f"{self.base_url}{self.madara_series_path}/{slug}"
        try:
            rendered = await self.fetch_rendered(
                url,
                wait_until="networkidle",
                wait_for_selector="h1, .entry-title, .manga-title, .comic-title",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_manga ManhwaClub pour {url!r}: {err}",
                url=url,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {url!r}: {exc}"
            ) from exc

        return self._mc_build_manga_from_dom(
            rendered.html, url=rendered.final_url or url
        )

    def _mc_build_manga_from_dom(self, html: str, *, url: str) -> Manga:
        """Construit un objet :class:`Manga` depuis le DOM (fallback).

        Args:
            html: HTML rendu.
            url: URL de la page manga.

        Returns:
            Objet :class:`Manga` hydraté.

        Raises:
            ParseError: Si le titre est absent.
        """
        tree = HTMLParser(html)

        title_node = self._mc_first(tree, self.madara_manga_title_selector)
        title = self._mc_text(title_node)
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        cover_node = self._mc_first(tree, self.madara_manga_cover_selector)
        cover_src = (
            self._mc_attr(cover_node, "data-src")
            or self._mc_attr(cover_node, "data-lazy-src")
            or self._mc_attr(cover_node, "src")
        )
        cover_url = self._mc_abs(cover_src, url) if cover_src else None

        description_node = self._mc_first(
            tree, self.madara_manga_description_selector
        )
        description = self._mc_text(description_node) or None

        author_node = self._mc_first(tree, self.madara_manga_author_selector)
        author = self._mc_text(author_node) or None

        artist_node = self._mc_first(tree, self.madara_manga_artist_selector)
        artist = self._mc_text(artist_node) or None

        genre_nodes = self._mc_all(tree, self.madara_manga_genres_selector)
        genres: list[str] = []
        for node in genre_nodes:
            text = self._mc_text(node)
            if text and text not in genres:
                genres.append(text)

        status_node = self._mc_first(tree, self.madara_manga_status_selector)
        status = self._mc_parse_status(self._mc_text(status_node))

        year_node = self._mc_first(tree, self.madara_manga_year_selector)
        year = self._mc_parse_year(self._mc_text(year_node))

        source_id = self._mc_extract_series_slug(url)
        rating = self._mc_detect_rating(genres)

        return Manga(
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
            chapters=[],
            url=url,
            updated_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _mc_extract_series_slug(url: str) -> str:
        """Extrait un identifiant de série depuis une URL ManhwaClub.

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

        Tente d'abord via l'API interne, puis bascule sur le rendu Playwright.

        Args:
            manga: Manga dont on veut les chapitres.

        Returns:
            Liste de :class:`Chapter` triés par numéro croissant.

        Raises:
            ChapterNotFoundError: Si aucun chapitre n'est trouvé.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "ManhwaClub get_chapters: {title}", title=manga.title
        )

        manga_slug = manga.source_id

        # Tentative 1 : API interne.
        try:
            payload = await self.api_get(
                f"{_API_PATH}/{manga_slug}/chapters",
                timeout=25.0,
            )
            if isinstance(payload, dict):
                items = payload.get("data") or payload.get("chapters") or []
                if isinstance(items, list) and items:
                    chapters: list[Chapter] = []
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        try:
                            chapter = self._mc_build_chapter_from_api(
                                item, manga_slug=manga_slug
                            )
                        except (ParseError, KeyError, ValueError):
                            continue
                        chapters.append(chapter)

                    if chapters:
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
                        self.logger.info(
                            "ManhwaClub get_chapters (API): {n} chapitre(s)",
                            n=len(chapters),
                        )
                        return chapters
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(
                "API get_chapters KO, fallback Playwright : {err}", err=exc
            )

        # Tentative 2 : rendu Playwright.
        try:
            rendered = await self.fetch_rendered(
                str(manga.url),
                wait_until="networkidle",
                wait_for_selector="a[href*='/chapter/'], .eplister ul li a",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_chapters ManhwaClub pour {title!r}: {err}",
                title=manga.title,
                err=exc,
            )
            raise ParseError(
                f"get_chapters échoué sur {self.site_id!r} "
                f"pour {manga.title!r}: {exc}"
            ) from exc

        chapters = self._mc_parse_chapters_from_html(
            rendered.html, base_url=rendered.final_url or str(manga.url)
        )
        if not chapters:
            raise ChapterNotFoundError(
                f"Aucun chapitre trouvé pour {manga.url} sur {self.site_id!r}"
            )
        return chapters

    def _mc_parse_chapters_from_html(
        self, html: str, *, base_url: str
    ) -> list[Chapter]:
        """Parse la liste des chapitres depuis le HTML (fallback).

        Args:
            html: HTML rendu.
            base_url: URL de la page manga.

        Returns:
            Liste de :class:`Chapter` triés.
        """
        tree = HTMLParser(html)
        seen: set[str] = set()
        chapters: list[Chapter] = []

        for node in self._mc_all(tree, self.madara_chapter_selector):
            href = self._mc_attr(node, "href")
            if not href or "/chapter/" not in href:
                continue
            abs_url = self._mc_abs(href, base_url)
            if abs_url in seen:
                continue
            seen.add(abs_url)

            label = self._mc_text(node) or self._mc_attr(node, "title")
            if not label:
                label = abs_url.rstrip("/").rsplit("/", 1)[-1] or abs_url

            number = self._mc_chapter_number(label)
            volume = self._mc_chapter_volume(label)

            date_text = self._mc_attr(node, "data-date") or None
            published = (
                self._mc_parse_relative_date(date_text)
                if date_text
                else self._mc_parse_relative_date(label)
            )

            source_id = abs_url.rstrip("/").rsplit("/", 1)[-1] or label
            chapters.append(
                Chapter(
                    id=f"{self.config.id}:{source_id}",
                    source_id=source_id,
                    title=label,
                    number=number,
                    volume=volume,
                    language=self.language,
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
        return chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre ManhwaClub.

        ManhwaClub utilise une API interne pour les pages de chapitre. Le
        parseur tente l'API, puis bascule sur le rendu Playwright.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "ManhwaClub get_pages: {url}", url=str(chapter.url)
        )
        chapter_url = str(chapter.url)
        chapter_id = chapter.source_id

        # Tentative 1 : API interne.
        if chapter_id:
            try:
                payload = await self.api_get(
                    f"{_API_PATH}/{chapter_id}/pages",
                    timeout=20.0,
                )
                if isinstance(payload, dict):
                    urls = self._mc_extract_page_urls_from_api(payload)
                    if urls:
                        return self._mc_build_pages(urls, chapter_url)
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "API get_pages KO, fallback Playwright : {err}", err=exc
                )

        # Tentative 2 : rendu Playwright.
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
                "Échec get_pages ManhwaClub pour {url!r}: {err}",
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

        # Priorité 1 : objet JS ts_reader.
        ts_urls = self._mc_extract_ts_reader(html)
        if ts_urls:
            for u in ts_urls:
                abs_url = self._mc_abs(u, chapter_url)
                if abs_url not in urls:
                    urls.append(abs_url)

        # Priorité 2 : sélecteur CSS.
        if not urls:
            for node in self._mc_all(tree, self.madara_page_img_selector):
                src = (
                    self._mc_attr(node, "data-src")
                    or self._mc_attr(node, "data-lazy-src")
                    or self._mc_attr(node, "data-original")
                    or self._mc_attr(node, "src")
                )
                if not src:
                    continue
                abs_url = self._mc_abs(src, chapter_url)
                if abs_url not in urls:
                    urls.append(abs_url)

        if not urls:
            raise ChapterNotFoundError(
                f"Aucune page trouvée pour {chapter_url} sur "
                f"{self.site_id!r}"
            )

        return self._mc_build_pages(urls, chapter_url)

    @staticmethod
    def _mc_extract_page_urls_from_api(payload: dict[str, Any]) -> list[str]:
        """Extrait les URLs de pages depuis la réponse API.

        Args:
            payload: Charge JSON de ``/api/comics/{id}/pages``.

        Returns:
            Liste d'URLs (vide si introuvable).
        """
        data = payload.get("data") or payload
        urls: list[str] = []

        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, str):
                    urls.append(entry)
                elif isinstance(entry, dict):
                    u = entry.get("url") or entry.get("src") or entry.get("image")
                    if isinstance(u, str):
                        urls.append(u)
        elif isinstance(data, dict):
            for key in ("pages", "urls", "images"):
                items = data.get(key)
                if isinstance(items, list):
                    for entry in items:
                        if isinstance(entry, str):
                            urls.append(entry)
                        elif isinstance(entry, dict):
                            u = entry.get("url") or entry.get("src")
                            if isinstance(u, str):
                                urls.append(u)

        return urls

    @staticmethod
    def _mc_extract_ts_reader(html: str) -> list[str]:
        """Extrait les URLs d'images depuis l'objet JS ``ts_reader``.

        Args:
            html: HTML de la page chapitre.

        Returns:
            Liste ordonnée d'URLs d'images (vide si introuvable).
        """
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
                urls = ManhwaClubParser._mc_walk_ts_reader(parsed)
                if urls:
                    return urls

        # Fallback : extraction par regex.
        urls = re.findall(
            r'"(?:url|image|src)"\s*:\s*"([^"]+)"', html, re.IGNORECASE
        )
        return urls

    @staticmethod
    def _mc_walk_ts_reader(payload: Any) -> list[str]:
        """Parcourt récursivement un objet ``ts_reader``.

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

    def _mc_build_pages(
        self, urls: list[str], chapter_url: str
    ) -> list[Page]:
        """Construit les objets :class:`Page` depuis une liste d'URLs.

        Args:
            urls: Liste d'URLs d'images.
            chapter_url: URL du chapitre.

        Returns:
            Liste de :class:`Page`.
        """
        pages: list[Page] = []
        for idx, url in enumerate(urls):
            clean = url.strip()
            if not clean:
                continue
            clean = self._mc_abs(clean, chapter_url)
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
        """Vérifie que ManhwaClub est accessible.

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
                        "Health check ManhwaClub OK: {base} (status={s})",
                        base=base,
                        s=rendered.status,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Health check ManhwaClub échec sur {base}: {err}",
                    base=base,
                    err=exc,
                )
        self.logger.warning("Health check ManhwaClub KO (tous domaines)")
        return False
