"""Parseur Asura Scans pour NexusDL.

Asura Scans (``https://asurascans.com``) est l'un des groupes de scanlation
anglophones les plus importants, spécialisé dans les manhwa/manhua coréens et
chinois (Solo Leveling, The Beginning After The End, Nano Machine, etc.).

⚠️ **Site à évolution rapide** : Asura Scans change fréquemment de domaine
(``asurascans.com`` → ``asuracomic.net`` → ``asura.nacm.xyz`` → ``asurascans.com``)
et son architecture a migré d'un thème WordPress **MangaThemesia** vers une
**API dédiée** (``api.asurascans.com/api``) avec un système de **mapping de
slugs** et **d'images brouillées**.

Caractéristiques techniques
---------------------------

* **Moteur actuel** : API REST dédiée (``api.asurascans.com/api``) avec
  Data Transfer Objects (DTOs). Le site lui-même reste un frontend Astro/
  Next.js qui consomme cette API.
* **Langue** : Anglais (``en``).
* **Protection** : Cloudflare (AS13335, IP ``104.21.31.111`` /
  ``172.67.176.61``) avec challenge **Managed Challenge**. Le site utilise
  également un **User-Agent strict** et un **rate limiting** (2 requêtes / 2
  secondes signalé par la communauté).
* **Système de slugs** : Asura Scans ajoute des **suffixes aléatoires** aux
  slugs (ex. ``solo-leveling-a1b2c3``) pour contrer les scrapers. Le parseur
  utilise l'API pour récupérer les slugs canoniques et stocke un mapping
  local.
* **Images brouillées** : Les images de chapitres sont servies sous forme de
  **tuiles mélangées** et doivent être réassemblées via un intercepteur
  custom (``scrambledImageInterceptor`` dans l'extension Tachiyomi).
  NexusDL utilise une stratégie de **fallback** : il tente d'abord l'extraction
  directe depuis ``ts_reader``, et si les images sont brouillées, il utilise
  l'URL de composition fournie par l'API.
* **CDN d'images** : ``gg.asuracomic.net/storage/media/...`` — nécessite le
  header ``Referer`` pointant vers ``https://asuracomic.net/``.
* **API endpoints** :
    - ``GET /api/manga`` — recherche et listing.
    - ``GET /api/manga/{slug}`` — détails d'un manga.
    - ``GET /api/manga/{slug}/chapters`` — liste des chapitres.
    - ``GET /api/chapter/{id}`` — détails d'un chapitre.
    - ``GET /api/chapter/{id}/pages`` — images d'un chapitre.
* **Formats de dates** : ISO 8601 avec timezone UTC.

Le parseur combine quatre mixins :

1. :class:`~nexusdl.parsers.mixins.mangathemesia.MangaThemesiaMixin` —
   fallback HTML pour les versions MangaThemesia (ancien ``asuracomic.net``).
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — bypass
   Cloudflare automatique (Playwright / FlareSolverr).
3. :class:`~nexusdl.parsers.mixins.api_based.ApiBasedMixin` — client REST
   pour l'API dédiée ``api.asurascans.com``.
4. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — rendu
   Playwright en dernier recours.

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.en.asurascans import AsuraScansParser
    >>>
    >>> parser = AsuraScansParser(config, session)
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
from nexusdl.parsers.mixins.mangathemesia import MangaThemesiaMixin

__all__ = ["AsuraScansParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://asuracomic.net"
_FALLBACK_DOMAINS: Final[tuple[str, ...]] = (
    "https://asuracomic.net",
    "https://asurascans.com",
    "https://asura.nacm.xyz",
)
_API_URL: Final[str] = "https://api.asurascans.com/api"
_SITE_ID: Final[str] = "asurascans"
_LANGUAGE: Final[Language] = Language.EN
_ADULT: Final[bool] = False

# User-Agent identifiable (le site bloque les UA par défaut).
_USER_AGENT: Final[str] = (
    "NexusDL/1.0 (https://github.com/nexusdl/nexusdl)"
)

# Regex de parsing des chapitres (format Asura : "Chapter 205").
_CHAPTER_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:chapter|ch\.?|episode|ep\.?|ep)\s*(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_VOLUME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:volume|vol\.?)\s*(\d+)", re.IGNORECASE
)

# Statuts Asura → énumération NexusDL.
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

# Mapping statuts API (nombres entiers).
_API_STATUS_MAP: Final[dict[int, MangaStatus]] = {
    1: MangaStatus.ONGOING,
    2: MangaStatus.COMPLETED,
    3: MangaStatus.CANCELLED,
    4: MangaStatus.HIATUS,
    5: MangaStatus.CANCELLED,
}

# Genres adultes (détection de classification).
_ADULT_GENRE_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "hentai", "adult", "ecchi", "smut", "mature", "18+",
        "porn", "erotic", "erotica", "yaoi", "yuri", "gender bender",
    }
)

# Marqueurs de contenu suggestif.
_SUGGESTIVE_MARKERS: Final[frozenset[str]] = frozenset(
    {"ecchi", "mature", "harem", "suggestive", "romance", "action"}
)

# ---------------------------------------------------------------------------
# Sélecteurs MangaThemesia (fallback HTML pour l'ancien asuracomic.net)
# ---------------------------------------------------------------------------

_ASURA_SEARCH_ITEM_SELECTOR: Final[str] = (
    ".listupd .bs, .listupd .bsx, .bs, .bsx, .uta, "
    "div.list-item, div.manga-item, a[href*='/series/']"
)
_ASURA_SEARCH_LINK_SELECTOR: Final[str] = "a"
_ASURA_SEARCH_COVER_SELECTOR: Final[str] = "img"
_ASURA_SEARCH_TITLE_SELECTOR: Final[str] = (
    ".tt, .title, h3, h4, .entry-title, .ntitle"
)

_ASURA_MANGA_TITLE_SELECTOR: Final[str] = (
    "h1.entry-title, h1, .entry-title, .manga-title, .post-title"
)
_ASURA_MANGA_COVER_SELECTOR: Final[str] = (
    ".thumb img, .manga-cover img, .summary_image img, "
    ".cover img, .thumbook img"
)
_ASURA_MANGA_DESCRIPTION_SELECTOR: Final[str] = (
    ".summary__content, .manga-summary, .description, "
    ".entry-content p, .desc"
)
_ASURA_MANGA_AUTHOR_SELECTOR: Final[str] = (
    ".author, .manga-author, a[href*='/author/'], "
    ".tsinfo .imptdt:contains('Author') a"
)
_ASURA_MANGA_ARTIST_SELECTOR: Final[str] = (
    ".artist, .manga-artist, a[href*='/artist/'], "
    ".tsinfo .imptdt:contains('Artist') a"
)
_ASURA_MANGA_GENRES_SELECTOR: Final[str] = (
    ".genres a, .manga-genres a, a[href*='/genre/'], "
    "a[href*='/tag/'], .mgen a"
)
_ASURA_MANGA_STATUS_SELECTOR: Final[str] = (
    ".status, .manga-status, .post-status, "
    ".tsinfo .imptdt:contains('Status') i"
)

_ASURA_CHAPTER_SELECTOR: Final[str] = (
    ".eplister ul li a, .eplister li a, "
    ".chapter-list a, "
    "a[href*='-chapter-'], "
    "a[href*='/chapter/']"
)
_ASURA_PAGE_IMG_SELECTOR: Final[str] = (
    ".chapter-page img, .page-image img, "
    ".reading-content img, .reader img, "
    "#readerarea img, .main-reading-area img"
)
_ASURA_PAGES_VAR_NAMES: Final[tuple[str, ...]] = (
    "ts_reader", "pages", "page_urls", "pageUrls", "images", "chapterImages",
)


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


class AsuraScansParser(
    JsRenderedMixin,
    CloudflareMixin,
    ApiBasedMixin,
    MangaThemesiaMixin,
    BaseParser,
):
    """Parseur Asura Scans (API dédiée + MangaThemesia + Cloudflare).

    Combine le rendu Playwright (fallback), le contournement Cloudflare,
    un client REST pour l'API ``api.asurascans.com`` et les implémentations
    MangaThemesia (pour l'ancien ``asuracomic.net``).

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
    api_url: ClassVar[str] = _API_URL
    fools_base_url: ClassVar[str | None] = None

    # --- Configuration MangaThemesia (fallback HTML) ---
    mt_base_url: ClassVar[str] = _BASE_URL
    mt_search_path: ClassVar[str] = "/"
    mt_search_query_param: ClassVar[str] = "s"
    mt_search_method: ClassVar[str] = "GET"
    mt_series_path: ClassVar[str] = "/series"
    mt_read_path: ClassVar[str] = "/series"

    mt_search_item_selector: ClassVar[str] = _ASURA_SEARCH_ITEM_SELECTOR
    mt_search_link_selector: ClassVar[str] = _ASURA_SEARCH_LINK_SELECTOR
    mt_search_cover_selector: ClassVar[str] = _ASURA_SEARCH_COVER_SELECTOR
    mt_search_title_selector: ClassVar[str] = _ASURA_SEARCH_TITLE_SELECTOR

    mt_manga_title_selector: ClassVar[str] = _ASURA_MANGA_TITLE_SELECTOR
    mt_manga_cover_selector: ClassVar[str] = _ASURA_MANGA_COVER_SELECTOR
    mt_manga_description_selector: ClassVar[str] = _ASURA_MANGA_DESCRIPTION_SELECTOR
    mt_manga_author_selector: ClassVar[str] = _ASURA_MANGA_AUTHOR_SELECTOR
    mt_manga_artist_selector: ClassVar[str] = _ASURA_MANGA_ARTIST_SELECTOR
    mt_manga_genres_selector: ClassVar[str] = _ASURA_MANGA_GENRES_SELECTOR
    mt_manga_status_selector: ClassVar[str] = _ASURA_MANGA_STATUS_SELECTOR

    mt_chapter_selector: ClassVar[str] = _ASURA_CHAPTER_SELECTOR
    mt_page_img_selector: ClassVar[str] = _ASURA_PAGE_IMG_SELECTOR
    mt_pages_var_names: ClassVar[tuple[str, ...]] = _ASURA_PAGES_VAR_NAMES

    # --- Comportement MangaThemesia ---
    mt_requires_js: ClassVar[bool] = False
    mt_api_pages_endpoint: ClassVar[str | None] = None
    mt_default_rating: ClassVar[ContentRating] = ContentRating.SAFE
    mt_concurrent_chapter_requests: ClassVar[int] = 4
    mt_search_pages_limit: ClassVar[int] = 20

    # --- Configuration API ---
    api_base_url: ClassVar[str] = _API_URL
    api_default_headers: ClassVar[dict[str, str]] = {
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
        "Referer": _BASE_URL,
        "Origin": _BASE_URL,
    }
    api_rate_limit_per_second: ClassVar[float] = 1.0  # 2 req / 2s signalé
    api_rate_limit_burst: ClassVar[int] = 2
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

    # --- Cache de mapping slug → ID ---
    _slug_map: ClassVar[dict[str, str]] = {}

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
        """Initialise le parseur Asura Scans.

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

        # Referer requis pour le CDN d'images.
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

    def _asura_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="asurascans"``.
        """
        return self.logger

    @staticmethod
    def _asura_clean(value: str | None) -> str:
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
    def _asura_text(node: Node | None) -> str:
        """Extrait et nettoie le texte d'un nœud.

        Args:
            node: Nœud selectolax ou ``None``.

        Returns:
            Texte nettoyé.
        """
        if node is None:
            return ""
        return AsuraScansParser._asura_clean(
            node.text(deep=True, separator=" ")
        )

    @staticmethod
    def _asura_attr(node: Node | None, name: str) -> str:
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

    def _asura_first(self, tree: HTMLParser, selector: str) -> Node | None:
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

    def _asura_all(self, tree: HTMLParser, selector: str) -> list[Node]:
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

    def _asura_abs(self, url: str, base: str | None = None) -> str:
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
    # Mapping des slugs (Asura Scans ajoute des suffixes aléatoires)
    # ------------------------------------------------------------------

    def _asura_resolve_slug(self, raw_slug: str) -> str:
        """Résout un slug Asura Scans (avec ou sans suffixe aléatoire).

        Asura Scans ajoute des suffixes aléatoires aux slugs (ex.
        ``solo-leveling-a1b2c3`` au lieu de ``solo-leveling``). Cette
        méthode tente de retrouver le slug canonique via le cache ou l'API.

        Args:
            raw_slug: Slug brut extrait de l'URL.

        Returns:
            Slug résolu (ou brut si introuvable).
        """
        # Vérifie le cache.
        if raw_slug in self._slug_map:
            return self._slug_map[raw_slug]

        # Tente de trouver un slug canonique en retirant le suffixe.
        # Format typique : {canonical-slug}-{6-8 chars aléatoires}.
        parts = raw_slug.rsplit("-", 1)
        if len(parts) == 2 and len(parts[1]) <= 8:
            canonical = parts[0]
            self._slug_map[raw_slug] = canonical
            return canonical

        return raw_slug

    # ------------------------------------------------------------------
    # Parsing statut / date / numéro de chapitre
    # ------------------------------------------------------------------

    def _asura_parse_status(self, raw: str | None) -> MangaStatus:
        """Convertit un libellé de statut en énumération NexusDL.

        Args:
            raw: Libellé brut.

        Returns:
            Statut normalisé (``ONGOING`` par défaut).
        """
        if not raw:
            return MangaStatus.ONGOING
        key = self._asura_clean(raw).lower()
        for needle, status in _STATUS_MAP.items():
            if needle in key:
                return status
        return MangaStatus.ONGOING

    @staticmethod
    def _asura_parse_iso_date(raw: str | None) -> datetime | None:
        """Parse une date ISO 8601.

        Args:
            raw: Chaîne de date RFC 3339 (ex. ``"2024-01-15T12:30:00Z"``).

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

    @staticmethod
    def _asura_chapter_number(text: str) -> float | str:
        """Extrait le numéro de chapitre d'un libellé.

        Args:
            text: Libellé (ex. ``"Chapter 205"``).

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = AsuraScansParser._asura_clean(text)
        match = _CHAPTER_NUMBER_RE.search(cleaned)
        if not match:
            return cleaned
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return cleaned

    @staticmethod
    def _asura_chapter_volume(text: str) -> int | None:
        """Extrait le numéro de tome d'un libellé.

        Args:
            text: Libellé contenant éventuellement ``Volume X``.

        Returns:
            Numéro de tome ou ``None``.
        """
        match = _VOLUME_RE.search(text or "")
        return int(match.group(1)) if match else None

    def _asura_detect_rating(self, genres: list[str]) -> ContentRating:
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
        return self.mt_default_rating

    # ------------------------------------------------------------------
    # Construction des objets depuis l'API
    # ------------------------------------------------------------------

    def _asura_build_manga_from_api(self, data: dict[str, Any]) -> Manga:
        """Construit un objet :class:`Manga` depuis l'API Asura Scans.

        Args:
            data: Entité manga retournée par l'API.

        Returns:
            Objet :class:`Manga` hydraté.

        Raises:
            ParseError: Si les données sont inexploitables.
        """
        manga_id = data.get("id")
        slug = data.get("slug")
        if not manga_id or not slug:
            raise ParseError("Entité manga sans 'id' ou 'slug' dans l'API")

        title = data.get("title") or "Untitled"

        # Titres alternatifs.
        alt_titles: list[str] = []
        for alt in data.get("alternative_titles") or []:
            if isinstance(alt, str) and alt not in alt_titles:
                alt_titles.append(alt)

        description = data.get("description")

        # Auteur / artiste.
        author = data.get("author")
        artist = data.get("artist")

        # Genres.
        genres: list[str] = []
        for genre in data.get("genres") or []:
            if isinstance(genre, str):
                genres.append(genre)
            elif isinstance(genre, dict):
                name = genre.get("name")
                if isinstance(name, str) and name not in genres:
                    genres.append(name)

        # Statut.
        status_raw = data.get("status")
        if isinstance(status_raw, int):
            status = _API_STATUS_MAP.get(status_raw, MangaStatus.ONGOING)
        else:
            status = self._asura_parse_status(str(status_raw) if status_raw else None)

        # Année.
        year = data.get("year")
        if not isinstance(year, int):
            year = None

        # Content rating.
        rating_raw = data.get("content_rating") or data.get("rating")
        if rating_raw:
            content_rating = self._asura_detect_rating([str(rating_raw)])
        else:
            content_rating = self._asura_detect_rating(genres)

        # Couverture.
        cover = data.get("cover") or data.get("image")
        cover_url = str(cover) if cover else None

        # Dates.
        updated_at = (
            self._asura_parse_iso_date(data.get("updated_at"))
            or datetime.now(timezone.utc)
        )

        # URL publique.
        url = f"{self.base_url}/series/{slug}"

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
            content_rating=content_rating,
            chapters=[],
            url=url,
            updated_at=updated_at,
        )

    def _asura_build_chapter_from_api(
        self, data: dict[str, Any], *, manga_slug: str
    ) -> Chapter:
        """Construit un objet :class:`Chapter` depuis l'API Asura Scans.

        Args:
            data: Entité chapter retournée par l'API.
            manga_slug: Slug du manga parent.

        Returns:
            Objet :class:`Chapter` hydraté.
        """
        chapter_id = str(data.get("id") or data.get("chapter_id") or "")
        chapter_num_raw = data.get("chapter") or data.get("number") or data.get("name")
        chapter_number = self._asura_chapter_number(str(chapter_num_raw or ""))

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

        published_at = self._asura_parse_iso_date(
            data.get("published_at") or data.get("created_at")
        )

        url = f"{self.base_url}/series/{manga_slug}/chapter/{chapter_id}"

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
        """Recherche des mangas sur Asura Scans.

        Tente d'abord via l'API dédiée (``/api/manga?search=...``), puis
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
            "AsuraScans search: {query} (page {page})",
            query=query,
            page=page,
        )

        # Tentative 1 : API dédiée.
        try:
            payload = await self.api_get(
                "/manga",
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
                            manga = self._asura_build_manga_from_api(item)
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
                            "AsuraScans search (API): {n} résultat(s)",
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
                "Échec recherche AsuraScans pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        all_results = self._asura_parse_search_html(
            rendered.html, base_url=rendered.final_url or url
        )

        # Filtre côté client.
        query_lower = query.lower().strip()
        filtered = [r for r in all_results if query_lower in r.title.lower()]

        self.logger.info(
            "AsuraScans search (DOM): {n} résultat(s)", n=len(filtered)
        )
        return filtered

    def _asura_parse_search_html(
        self, html: str, *, base_url: str
    ) -> list[SearchResult]:
        """Parse le HTML de résultats de recherche (fallback MangaThemesia).

        Args:
            html: HTML rendu.
            base_url: URL de la page (pour résolution relative).

        Returns:
            Liste de :class:`SearchResult`.
        """
        tree = HTMLParser(html)
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for node in self._asura_all(tree, self.mt_search_item_selector):
            link_node: Node | None = None
            if node.tag == "a" and "/series/" in self._asura_attr(node, "href"):
                link_node = node
            else:
                for candidate in node.css("a"):
                    if "/series/" in self._asura_attr(candidate, "href"):
                        link_node = candidate
                        break

            if link_node is None:
                continue

            href = self._asura_attr(link_node, "href")
            if not href:
                continue
            abs_url = self._asura_abs(href, base_url)
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            title_node = node.css_first(self.mt_search_title_selector)
            title = self._asura_text(title_node) or self._asura_attr(
                link_node, "title"
            )
            if not title:
                title = (
                    self._asura_attr(link_node, "href")
                    .rstrip("/")
                    .rsplit("/", 1)[-1]
                )
                title = title.replace("-", " ").title()
            if not title:
                continue

            cover_node = node.css_first(self.mt_search_cover_selector)
            cover_src = (
                self._asura_attr(cover_node, "data-src")
                or self._asura_attr(cover_node, "data-lazy-src")
                or self._asura_attr(cover_node, "src")
            )
            cover_url = (
                self._asura_abs(cover_src, base_url) if cover_src else None
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
        """Récupère la fiche complète d'un manga Asura Scans.

        Tente d'abord via l'API dédiée, puis bascule sur le rendu Playwright
        en fallback.

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
            slug = self._asura_extract_series_slug(url_or_id)
        elif url_or_id.startswith("/"):
            slug = self._asura_extract_series_slug(
                f"{self.base_url}{url_or_id}"
            )
        else:
            slug = url_or_id.strip("/")

        # Résout le slug (retire le suffixe aléatoire si présent).
        canonical_slug = self._asura_resolve_slug(slug)
        self.logger.debug(
            "AsuraScans get_manga: {slug} (canonical: {cs})",
            slug=slug,
            cs=canonical_slug,
        )

        # Tentative 1 : API dédiée.
        try:
            payload = await self.api_get(
                f"/manga/{canonical_slug}",
                timeout=20.0,
            )
            if isinstance(payload, dict):
                data = payload.get("data") or payload
                if isinstance(data, dict) and data.get("id"):
                    return self._asura_build_manga_from_api(data)
        except Exception as exc:  # noqa: BLE001
            if "404" in str(exc):
                raise MangaNotFoundError(
                    f"Manga introuvable : {canonical_slug}"
                ) from exc
            self.logger.debug(
                "API get_manga KO, fallback Playwright : {err}", err=exc
            )

        # Tentative 2 : rendu Playwright (fallback MangaThemesia).
        url = f"{self.base_url}{self.mt_series_path}/{slug}"
        try:
            rendered = await self.fetch_rendered(
                url,
                wait_until="networkidle",
                wait_for_selector="h1, .entry-title, .manga-title",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_manga AsuraScans pour {url!r}: {err}",
                url=url,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {url!r}: {exc}"
            ) from exc

        return self._asura_build_manga_from_dom(
            rendered.html, url=rendered.final_url or url
        )

    def _asura_build_manga_from_dom(self, html: str, *, url: str) -> Manga:
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

        title_node = self._asura_first(tree, self.mt_manga_title_selector)
        title = self._asura_text(title_node)
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        cover_node = self._asura_first(tree, self.mt_manga_cover_selector)
        cover_src = (
            self._asura_attr(cover_node, "data-src")
            or self._asura_attr(cover_node, "data-lazy-src")
            or self._asura_attr(cover_node, "src")
        )
        cover_url = self._asura_abs(cover_src, url) if cover_src else None

        description_node = self._asura_first(
            tree, self.mt_manga_description_selector
        )
        description = self._asura_text(description_node) or None

        author_node = self._asura_first(tree, self.mt_manga_author_selector)
        author = self._asura_text(author_node) or None

        artist_node = self._asura_first(tree, self.mt_manga_artist_selector)
        artist = self._asura_text(artist_node) or None

        genre_nodes = self._asura_all(tree, self.mt_manga_genres_selector)
        genres: list[str] = []
        for node in genre_nodes:
            text = self._asura_text(node)
            if text and text not in genres:
                genres.append(text)

        status_node = self._asura_first(tree, self.mt_manga_status_selector)
        status = self._asura_parse_status(self._asura_text(status_node))

        source_id = self._asura_extract_series_slug(url)
        rating = self._asura_detect_rating(genres)

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
            year=None,
            cover_url=cover_url,
            language=self.language,
            content_rating=rating,
            chapters=[],
            url=url,
            updated_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _asura_extract_series_slug(url: str) -> str:
        """Extrait un identifiant de série depuis une URL Asura Scans.

        Format : ``/series/{slug}`` → retourne ``{slug}``.

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

        Tente d'abord via l'API dédiée, puis bascule sur le rendu Playwright.

        Args:
            manga: Manga dont on veut les chapitres.

        Returns:
            Liste de :class:`Chapter` triés par numéro croissant.

        Raises:
            ChapterNotFoundError: Si aucun chapitre n'est trouvé.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "AsuraScans get_chapters: {title}", title=manga.title
        )

        manga_slug = manga.source_id

        # Tentative 1 : API dédiée.
        try:
            payload = await self.api_get(
                f"/manga/{manga_slug}/chapters",
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
                            chapter = self._asura_build_chapter_from_api(
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
                            "AsuraScans get_chapters (API): {n} chapitre(s)",
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
                wait_for_selector=".eplister ul li a, a[href*='-chapter-']",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_chapters AsuraScans pour {title!r}: {err}",
                title=manga.title,
                err=exc,
            )
            raise ParseError(
                f"get_chapters échoué sur {self.site_id!r} "
                f"pour {manga.title!r}: {exc}"
            ) from exc

        chapters = self._asura_parse_chapters_from_html(
            rendered.html, base_url=rendered.final_url or str(manga.url)
        )
        if not chapters:
            raise ChapterNotFoundError(
                f"Aucun chapitre trouvé pour {manga.url} sur {self.site_id!r}"
            )
        return chapters

    def _asura_parse_chapters_from_html(
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

        for node in self._asura_all(tree, self.mt_chapter_selector):
            href = self._asura_attr(node, "href")
            if not href or ("-chapter-" not in href and "/chapter/" not in href):
                continue
            abs_url = self._asura_abs(href, base_url)
            if abs_url in seen:
                continue
            seen.add(abs_url)

            label = self._asura_text(node) or self._asura_attr(node, "title")
            if not label:
                label = abs_url.rstrip("/").rsplit("/", 1)[-1] or abs_url

            number = self._asura_chapter_number(label)
            volume = self._asura_chapter_volume(label)

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
                    published_at=None,
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
        """Récupère les URLs des pages d'un chapitre Asura Scans.

        Asura Scans sert les images sous forme de **tuiles brouillées** et
        utilise un intercepteur custom côté client. NexusDL utilise la
        stratégie suivante :

        1. API dédiée ``/api/chapter/{id}/pages`` (retourne les URLs
           composées ou les métadonnées de tuiles).
        2. Extraction directe depuis ``ts_reader`` (fallback).
        3. Sélecteur CSS MangaThemesia (dernier recours).

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "AsuraScans get_pages: {url}", url=str(chapter.url)
        )
        chapter_url = str(chapter.url)
        chapter_id = chapter.source_id

        # Tentative 1 : API dédiée.
        if chapter_id:
            try:
                payload = await self.api_get(
                    f"/chapter/{chapter_id}/pages",
                    timeout=20.0,
                )
                if isinstance(payload, dict):
                    urls = self._asura_extract_page_urls_from_api(payload)
                    if urls:
                        return self._asura_build_pages(urls, chapter_url)
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "API get_pages KO, fallback : {err}", err=exc
                )

        # Tentative 2 : rendu Playwright.
        try:
            rendered = await self.fetch_rendered(
                chapter_url,
                wait_until="networkidle",
                wait_for_selector=".chapter-page img, .reading-content img",
                timeout=45.0,
                bypass_cloudflare=True,
                block_resources=False,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_pages AsuraScans pour {url!r}: {err}",
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
        ts_urls = self._asura_extract_ts_reader(html)
        if ts_urls:
            for u in ts_urls:
                abs_url = self._asura_abs(u, chapter_url)
                if abs_url not in urls:
                    urls.append(abs_url)

        # Priorité 2 : sélecteur CSS.
        if not urls:
            for node in self._asura_all(tree, self.mt_page_img_selector):
                src = (
                    self._asura_attr(node, "data-src")
                    or self._asura_attr(node, "data-lazy-src")
                    or self._asura_attr(node, "data-original")
                    or self._asura_attr(node, "src")
                )
                if not src:
                    continue
                abs_url = self._asura_abs(src, chapter_url)
                if abs_url not in urls:
                    urls.append(abs_url)

        if not urls:
            raise ChapterNotFoundError(
                f"Aucune page trouvée pour {chapter_url} sur "
                f"{self.site_id!r}"
            )

        return self._asura_build_pages(urls, chapter_url)

    @staticmethod
    def _asura_extract_ts_reader(html: str) -> list[str]:
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
                urls = AsuraScansParser._asura_walk_ts_reader(parsed)
                if urls:
                    return urls

        # Fallback : extraction par regex.
        urls = re.findall(
            r'"(?:url|image|src)"\s*:\s*"([^"]+)"', html, re.IGNORECASE
        )
        return urls

    @staticmethod
    def _asura_walk_ts_reader(payload: Any) -> list[str]:
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

    @staticmethod
    def _asura_extract_page_urls_from_api(payload: dict[str, Any]) -> list[str]:
        """Extrait les URLs de pages depuis la réponse API Asura Scans.

        Args:
            payload: Charge JSON de ``/api/chapter/{id}/pages``.

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
                    u = entry.get("url") or entry.get("src")
                    if isinstance(u, str):
                        urls.append(u)
        elif isinstance(data, dict):
            # Le format peut être {pages: [...]} ou {urls: [...]}.
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

    def _asura_build_pages(
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
            clean = self._asura_abs(clean, chapter_url)
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
        """Vérifie que Asura Scans est accessible.

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
                        "Health check AsuraScans OK: {base} (status={s})",
                        base=base,
                        s=rendered.status,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Health check AsuraScans échec sur {base}: {err}",
                    base=base,
                    err=exc,
                )
        self.logger.warning("Health check AsuraScans KO (tous domaines)")
        return False
