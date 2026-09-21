"""Parseur Kirei Cake pour NexusDL.

Kirei Cake (``https://kireicake.com``) est un groupe de scanlation anglophone
spécialisé dans le **yuri** et le **shoujo-ai** depuis 2010. Le site héberge
un catalogue conséquent de mangas, manhwas et one-shots traduits en anglais,
dont certains titres matures.

Caractéristiques techniques
---------------------------

* **Moteur** : PHP custom (aucun CMS standard — ni WordPress, ni Laravel,
  ni Madara). Le site a son propre système de gestion, une structure DOM
  unique et un reader basé sur JavaScript avec injection AJAX des images.
* **Langue** : Anglais (``en``).
* **Protection** : légère — pas de Cloudflare permanent, mais le site peut
  activer un challenge sous charge. Un ``Referer`` correct est requis pour
  les images.
* **Domaines** : ``kireicake.com`` (principal depuis 2019),
  ``manganeko.com`` (ancien, redirige).
* **Structure des URLs** :
    - Catalogue : ``/directory/`` (liste complète des mangas).
    - Recherche : ``/?s={query}`` ou ``/directory/?s={query}``.
    - Manga : ``/reader/?manga={slug}``.
    - Chapitre : ``/reader/?manga={slug}&chapter={num}``.
    - Liste de chapitres : page manga avec ``<select>`` de sélection.
* **Reader** : les images sont injectées côté client via un endpoint
  ``/reader/pages/?manga={slug}&chapter={num}`` qui retourne un JSON
  contenant les URLs. Un fallback HTML existe (``<img class="reader_image">``).
* **Classification** : contenu yuri/shoujo-ai avec parfois du contenu
  suggestif — le parseur détecte les genres adultes (``yuri``, ``mature``,
  ``smut``…) pour classifier automatiquement.

Le parseur combine trois mixins :

1. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — rendu
   Playwright (reader JavaScript + injection AJAX).
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — bypass
   Cloudflare préventif (challenges intermittents).
3. :class:`~nexusdl.parsers.mixins.api_based.ApiBasedMixin` — client REST
   pour l'endpoint JSON ``/reader/pages/``.

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.en.kireicake import KireiCakeParser
    >>>
    >>> parser = KireiCakeParser(config, session)
    >>> results = await parser.search("whisper me a love song")
    >>> manga = await parser.get_manga(results[0].url)
    >>> chapters = await parser.get_chapters(manga)
    >>> pages = await parser.get_pages(chapters[0])
"""

from __future__ import annotations

import json as _json
import re
from datetime import datetime, timezone
from typing import Any, ClassVar, Final
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

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

__all__ = ["KireiCakeParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://kireicake.com"
_FALLBACK_DOMAINS: Final[tuple[str, ...]] = (
    "https://kireicake.com",
    "https://manganeko.com",
)
_SITE_ID: Final[str] = "kireicake"
_LANGUAGE: Final[Language] = Language.EN
_ADULT: Final[bool] = False  # Contenu suggestif mais pas explicitement 18+

# Regex de parsing des chapitres.
_CHAPTER_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:chapter|ch\.?|episode|ep\.?|oneshot|one[- ]shot|extra|omake)"
    r"\s*(\d+(?:[.,]\d+)?)?",
    re.IGNORECASE,
)
_VOLUME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:volume|vol\.?)\s*(\d+)", re.IGNORECASE
)

# Statuts → énumération NexusDL.
_STATUS_MAP: Final[dict[str, MangaStatus]] = {
    "ongoing": MangaStatus.ONGOING,
    "on going": MangaStatus.ONGOING,
    "completed": MangaStatus.COMPLETED,
    "complete": MangaStatus.COMPLETED,
    "finished": MangaStatus.COMPLETED,
    "hiatus": MangaStatus.HIATUS,
    "paused": MangaStatus.HIATUS,
    "dropped": MangaStatus.CANCELLED,
    "cancelled": MangaStatus.CANCELLED,
    "canceled": MangaStatus.CANCELLED,
    "discontinued": MangaStatus.CANCELLED,
}

# Genres adultes / suggestifs (détection de classification).
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
        "yuri",
        "shoujo-ai",
        "shoujo ai",
        "yuri-ai",
    }
)

# Marqueurs de contenu suggestif (pas explicite mais classifié ÉROTICA).
_SUGGESTIVE_MARKERS: Final[frozenset[str]] = frozenset(
    {"ecchi", "yuri", "shoujo-ai", "shoujo ai", "mature", "suggestive"}
)

# ---------------------------------------------------------------------------
# Sélecteurs Kirei Cake (PHP custom)
# ---------------------------------------------------------------------------

_KC_SEARCH_ITEM_SELECTOR: Final[str] = (
    "div.manga_item, .manga_item, .series_item, "
    "div.series, div.list_item, "
    "a[href*='/reader/?manga=']"
)
_KC_SEARCH_LINK_SELECTOR: Final[str] = "a"
_KC_SEARCH_COVER_SELECTOR: Final[str] = "img"
_KC_SEARCH_TITLE_SELECTOR: Final[str] = (
    ".title, .series_title, .manga_title, h3, h4"
)

_KC_MANGA_TITLE_SELECTOR: Final[str] = (
    "h1.manga_title, h1.series_title, h1, "
    ".manga_title, .series_title, "
    "#manga_title, .reader_title"
)
_KC_MANGA_COVER_SELECTOR: Final[str] = (
    ".manga_cover img, .series_cover img, "
    ".cover img, .thumbnail img, "
    "#series_cover img"
)
_KC_MANGA_DESCRIPTION_SELECTOR: Final[str] = (
    ".manga_description, .series_description, "
    ".description, .summary, "
    "#manga_description"
)
_KC_MANGA_AUTHOR_SELECTOR: Final[str] = (
    ".manga_author, .author, .series_author, "
    "a[href*='/author/']"
)
_KC_MANGA_ARTIST_SELECTOR: Final[str] = (
    ".manga_artist, .artist, .series_artist, "
    "a[href*='/artist/']"
)
_KC_MANGA_GENRES_SELECTOR: Final[str] = (
    ".manga_genres a, .genres a, "
    ".series_genres a, a[href*='/genre/'], "
    "a[href*='/tag/']"
)
_KC_MANGA_STATUS_SELECTOR: Final[str] = (
    ".manga_status, .status, .series_status"
)

_KC_CHAPTER_SELECTOR: Final[str] = (
    "select#chapter_select option, "
    "select.chapter_select option, "
    "#chapter_list a, "
    ".chapter_list a, "
    "a[href*='/reader/?manga='][href*='chapter=']"
)
_KC_PAGE_IMG_SELECTOR: Final[str] = (
    "img.reader_image, "
    "img.reader-img, "
    "div.reader_container img, "
    "#reader_images img, "
    "#images img"
)
_KC_PAGES_VAR_NAMES: Final[tuple[str, ...]] = (
    "pages",
    "page_urls",
    "pageUrls",
    "images",
    "chapterImages",
    "reader_images",
)


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


class KireiCakeParser(
    JsRenderedMixin,
    CloudflareMixin,
    ApiBasedMixin,
    BaseParser,
):
    """Parseur Kirei Cake (PHP custom + reader JS + Cloudflare préventif).

    Combine le rendu Playwright (obligatoire — reader JS avec injection
    AJAX des images), le contournement Cloudflare préventif et un client
    REST pour l'endpoint ``/reader/pages/``.

    Attributes:
        site_id: Identifiant interne du site.
        language: Langue principale (anglais).
        adult: Contenu adulte (``False`` — contenu suggestif yuri).
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

    # --- Chemins Kirei Cake ---
    kc_directory_path: ClassVar[str] = "/directory/"
    kc_reader_path: ClassVar[str] = "/reader/"
    kc_pages_endpoint: ClassVar[str] = "/reader/pages/"

    # --- Sélecteurs Kirei Cake ---
    kc_search_item_selector: ClassVar[str] = _KC_SEARCH_ITEM_SELECTOR
    kc_search_link_selector: ClassVar[str] = _KC_SEARCH_LINK_SELECTOR
    kc_search_cover_selector: ClassVar[str] = _KC_SEARCH_COVER_SELECTOR
    kc_search_title_selector: ClassVar[str] = _KC_SEARCH_TITLE_SELECTOR

    kc_manga_title_selector: ClassVar[str] = _KC_MANGA_TITLE_SELECTOR
    kc_manga_cover_selector: ClassVar[str] = _KC_MANGA_COVER_SELECTOR
    kc_manga_description_selector: ClassVar[str] = _KC_MANGA_DESCRIPTION_SELECTOR
    kc_manga_author_selector: ClassVar[str] = _KC_MANGA_AUTHOR_SELECTOR
    kc_manga_artist_selector: ClassVar[str] = _KC_MANGA_ARTIST_SELECTOR
    kc_manga_genres_selector: ClassVar[str] = _KC_MANGA_GENRES_SELECTOR
    kc_manga_status_selector: ClassVar[str] = _KC_MANGA_STATUS_SELECTOR

    kc_chapter_selector: ClassVar[str] = _KC_CHAPTER_SELECTOR
    kc_page_img_selector: ClassVar[str] = _KC_PAGE_IMG_SELECTOR
    kc_pages_var_names: ClassVar[tuple[str, ...]] = _KC_PAGES_VAR_NAMES

    # --- Comportement ---
    kc_requires_js: ClassVar[bool] = True  # Reader JS
    kc_default_rating: ClassVar[ContentRating] = ContentRating.SUGGESTIVE

    # --- Configuration API (endpoint reader pages) ---
    api_base_url: ClassVar[str] = _BASE_URL
    api_default_headers: ClassVar[dict[str, str]] = {
        "Accept": "application/json",
        "X-Referer": _BASE_URL,
        "Origin": _BASE_URL,
    }
    api_rate_limit_per_second: ClassVar[float] = 2.0
    api_rate_limit_burst: ClassVar[int] = 3
    api_timeout: ClassVar[float] = 15.0
    api_cache_enabled: ClassVar[bool] = True
    api_cache_ttl: ClassVar[int] = 300

    # --- Configuration Cloudflare (préventif) ---
    cf_enabled: ClassVar[bool] = True
    cf_requires_js: ClassVar[bool] = False
    cf_preferred_backend: ClassVar[str] = "playwright"
    cf_use_playwright_fallback: ClassVar[bool] = True
    cf_use_flaresolverr_fallback: ClassVar[bool] = True
    cf_max_attempts: ClassVar[int] = 3
    cf_challenge_timeout: ClassVar[float] = 30.0
    cf_clearance_ttl: ClassVar[int] = 1800

    # --- Configuration JsRendered ---
    js_rendered: ClassVar[bool] = True
    default_wait_until: ClassVar[str] = "networkidle"
    default_render_timeout: ClassVar[float] = 30.0
    default_navigation_timeout: ClassVar[float] = 45.0
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
        """Initialise le parseur Kirei Cake.

        Args:
            config: Configuration du site (:class:`SiteConfig`).
            session: Session HTTP async du projet (:class:`HttpSession`).
            playwright_pool: Pool Playwright partagé (requis — reader JS).
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

    def _kc_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="kireicake"``.
        """
        return self.logger

    @staticmethod
    def _kc_clean(value: str | None) -> str:
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
    def _kc_text(node: Node | None) -> str:
        """Extrait et nettoie le texte d'un nœud.

        Args:
            node: Nœud selectolax ou ``None``.

        Returns:
            Texte nettoyé.
        """
        if node is None:
            return ""
        return KireiCakeParser._kc_clean(
            node.text(deep=True, separator=" ")
        )

    @staticmethod
    def _kc_attr(node: Node | None, name: str) -> str:
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

    def _kc_first(self, tree: HTMLParser, selector: str) -> Node | None:
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

    def _kc_all(self, tree: HTMLParser, selector: str) -> list[Node]:
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

    def _kc_abs(self, url: str, base: str | None = None) -> str:
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

    @staticmethod
    def _kc_query_param(url: str, key: str) -> str | None:
        """Extrait un paramètre de query string d'une URL.

        Args:
            url: URL absolue ou relative.
            key: Nom du paramètre.

        Returns:
            Valeur du paramètre ou ``None``.
        """
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            values = params.get(key)
            return values[0] if values else None
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Parsing statut / numéro de chapitre
    # ------------------------------------------------------------------

    def _kc_parse_status(self, raw: str | None) -> MangaStatus:
        """Convertit un libellé de statut en énumération NexusDL.

        Args:
            raw: Libellé brut.

        Returns:
            Statut normalisé (``ONGOING`` par défaut).
        """
        if not raw:
            return MangaStatus.ONGOING
        key = self._kc_clean(raw).lower()
        for needle, status in _STATUS_MAP.items():
            if needle in key:
                return status
        return MangaStatus.ONGOING

    @staticmethod
    def _kc_chapter_number(text: str) -> float | str:
        """Extrait le numéro de chapitre d'un libellé.

        Gère les cas particuliers Kirei Cake : ``Oneshot``, ``Extra``,
        ``Omake`` — qui n'ont pas de numéro mais doivent être conservés.

        Args:
            text: Libellé (ex. ``"Chapter 12.5"``, ``"Oneshot"``).

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = KireiCakeParser._kc_clean(text)
        match = _CHAPTER_NUMBER_RE.search(cleaned)
        if not match:
            return cleaned
        num = match.group(1)
        if not num:
            return cleaned
        try:
            return float(num.replace(",", "."))
        except ValueError:
            return cleaned

    @staticmethod
    def _kc_chapter_volume(text: str) -> int | None:
        """Extrait le numéro de tome d'un libellé.

        Args:
            text: Libellé contenant éventuellement ``Volume X``.

        Returns:
            Numéro de tome ou ``None``.
        """
        match = _VOLUME_RE.search(text or "")
        return int(match.group(1)) if match else None

    def _kc_detect_rating(self, genres: list[str]) -> ContentRating:
        """Détermine la classification de contenu depuis les genres.

        Kirei Cake est spécialisé dans le yuri/shoujo-ai. Les mangas avec
        des tags explicitement adultes (``hentai``, ``smut``, ``mature``)
        sont classifiés ``EROTICA`` ou ``PORNOGRAPHIC`` ; les autres sont
        ``SUGGESTIVE`` (genre yuri par défaut).

        Args:
            genres: Liste de genres du manga.

        Returns:
            Classification détectée.
        """
        lowered = {g.lower() for g in genres}
        if lowered & _ADULT_GENRE_MARKERS:
            if {"hentai", "porn", "18+", "explicit"} & lowered:
                return ContentRating.PORNOGRAPHIC
            if {"smut", "mature", "erotica"} & lowered:
                return ContentRating.EROTICA
        if lowered & _SUGGESTIVE_MARKERS:
            return ContentRating.SUGGESTIVE
        return self.kc_default_rating

    # ------------------------------------------------------------------
    # Méthodes abstraites de BaseParser
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des mangas sur Kirei Cake.

        Kirei Cake utilise ``/?s={query}`` (paramètre WordPress-like) ou
        ``/directory/?s={query}``. La recherche est effectuée via le rendu
        Playwright pour capturer les résultats dynamiques.

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.

        Raises:
            ParseError: Si le HTML de résultats est inexploitable.
        """
        self.logger.debug(
            "KireiCake search: {query} (page {page})",
            query=query,
            page=page,
        )

        base = self.base_url.rstrip("/")
        url = f"{base}/?s={quote_plus(query)}"
        if page > 1:
            url = f"{url}&page={page}"

        try:
            rendered = await self.fetch_rendered(
                url,
                wait_until="networkidle",
                timeout=30.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec recherche KireiCake pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        return self._kc_parse_search_html(
            rendered.html, base_url=rendered.final_url or url
        )

    def _kc_parse_search_html(
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
        seen_slugs: set[str] = set()

        for node in self._kc_all(tree, self.kc_search_item_selector):
            link_node: Node | None = None
            if node.tag == "a" and "manga=" in self._kc_attr(node, "href"):
                link_node = node
            else:
                for candidate in node.css("a"):
                    if "manga=" in self._kc_attr(candidate, "href"):
                        link_node = candidate
                        break

            if link_node is None:
                continue

            href = self._kc_attr(link_node, "href")
            if not href:
                continue

            # Extrait le slug de manga depuis le paramètre `manga=`.
            slug = self._kc_query_param(href, "manga")
            if not slug:
                continue
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            abs_url = self._kc_abs(href, base_url)

            title_node = node.css_first(self.kc_search_title_selector)
            title = self._kc_text(title_node) or self._kc_attr(
                link_node, "title"
            )
            if not title:
                title = slug.replace("-", " ").replace("_", " ").title()
            if not title:
                continue

            cover_node = node.css_first(self.kc_search_cover_selector)
            cover_src = (
                self._kc_attr(cover_node, "data-src")
                or self._kc_attr(cover_node, "data-lazy-src")
                or self._kc_attr(cover_node, "src")
            )
            cover_url = self._kc_abs(cover_src, base_url) if cover_src else None

            # Construit une URL canonique de manga.
            canonical = (
                f"{self.base_url}{self.kc_reader_path}?manga={slug}"
            )

            results.append(
                SearchResult(
                    title=title,
                    url=canonical,
                    site_id=self.config.id,
                    cover_url=cover_url,
                    author=None,
                )
            )

        self.logger.info(
            "KireiCake search: {n} résultat(s)", n=len(results)
        )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga Kirei Cake.

        Args:
            url_or_id: URL absolue ou slug de la série.

        Returns:
            Objet :class:`Manga` hydraté.

        Raises:
            MangaNotFoundError: Si le manga est inaccessible.
            ParseError: Si le HTML est inexploitable.
        """
        url = self._kc_normalize_manga_url(url_or_id)
        self.logger.debug("KireiCake get_manga: {url}", url=url)

        try:
            rendered = await self.fetch_rendered(
                url,
                wait_until="networkidle",
                wait_for_selector="h1, .manga_title, #manga_title, select",
                timeout=30.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_manga KireiCake pour {url!r}: {err}",
                url=url,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {url!r}: {exc}"
            ) from exc

        html = rendered.html
        tree = HTMLParser(html)

        title_node = self._kc_first(tree, self.kc_manga_title_selector)
        title = self._kc_text(title_node)
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        cover_node = self._kc_first(tree, self.kc_manga_cover_selector)
        cover_src = (
            self._kc_attr(cover_node, "data-src")
            or self._kc_attr(cover_node, "data-lazy-src")
            or self._kc_attr(cover_node, "src")
        )
        cover_url = self._kc_abs(cover_src, url) if cover_src else None

        description_node = self._kc_first(
            tree, self.kc_manga_description_selector
        )
        description = self._kc_text(description_node) or None

        author_node = self._kc_first(tree, self.kc_manga_author_selector)
        author = self._kc_text(author_node) or None

        artist_node = self._kc_first(tree, self.kc_manga_artist_selector)
        artist = self._kc_text(artist_node) or None

        genre_nodes = self._kc_all(tree, self.kc_manga_genres_selector)
        genres: list[str] = []
        for node in genre_nodes:
            text = self._kc_text(node)
            if text and text not in genres:
                genres.append(text)

        status_node = self._kc_first(tree, self.kc_manga_status_selector)
        status = self._kc_parse_status(self._kc_text(status_node))

        source_id = (
            self._kc_query_param(url, "manga")
            or self._kc_extract_series_slug(url)
        )
        rating = self._kc_detect_rating(genres)

        chapters = self._kc_parse_chapters_from_html(html, base_url=url)

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
            "KireiCake get_manga OK: {title} ({n} chapitres)",
            title=title,
            n=len(chapters),
        )
        return manga

    def _kc_normalize_manga_url(self, url_or_id: str) -> str:
        """Normalise une URL ou un slug en URL canonique de manga.

        Args:
            url_or_id: URL absolue, URL relative, ou slug.

        Returns:
            URL canonique ``{base}/reader/?manga={slug}``.
        """
        # Slug brut.
        if not url_or_id.startswith(("http://", "https://", "/")):
            return f"{self.base_url}{self.kc_reader_path}?manga={url_or_id}"

        # URL absolue.
        if url_or_id.startswith(("http://", "https://")):
            slug = self._kc_query_param(url_or_id, "manga")
            if slug:
                return (
                    f"{self.base_url}{self.kc_reader_path}?manga={slug}"
                )
            return url_or_id

        # URL relative.
        slug = self._kc_query_param(url_or_id, "manga")
        if slug:
            return f"{self.base_url}{self.kc_reader_path}?manga={slug}"
        return f"{self.base_url}{url_or_id}"

    @staticmethod
    def _kc_extract_series_slug(url: str) -> str:
        """Extrait un identifiant de série depuis une URL Kirei Cake.

        Format : ``/reader/?manga={slug}`` → retourne ``{slug}``.

        Args:
            url: URL de la série.

        Returns:
            Slug nettoyé.
        """
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if "manga" in params and params["manga"]:
            return params["manga"][0]
        parts = [p for p in parsed.path.split("/") if p]
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
            "KireiCake get_chapters: {title}", title=manga.title
        )

        try:
            rendered = await self.fetch_rendered(
                str(manga.url),
                wait_until="networkidle",
                wait_for_selector="select option, a[href*='chapter=']",
                timeout=30.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_chapters KireiCake pour {title!r}: {err}",
                title=manga.title,
                err=exc,
            )
            raise ParseError(
                f"get_chapters échoué sur {self.site_id!r} "
                f"pour {manga.title!r}: {exc}"
            ) from exc

        chapters = self._kc_parse_chapters_from_html(
            rendered.html, base_url=rendered.final_url or str(manga.url)
        )
        if not chapters:
            raise ChapterNotFoundError(
                f"Aucun chapitre trouvé pour {manga.url} sur {self.site_id!r}"
            )
        return chapters

    def _kc_parse_chapters_from_html(
        self,
        html: str,
        *,
        base_url: str,
    ) -> list[Chapter]:
        """Parse la liste des chapitres depuis le HTML d'une fiche manga.

        Kirei Cake expose les chapitres de deux manières :

        1. Un ``<select id="chapter_select">`` avec des ``<option>``
           contenant chacun le numéro et l'URL relative.
        2. Des liens ``<a href="/reader/?manga=...&chapter=...">``.

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

        # Extrait le slug du manga depuis l'URL de base.
        manga_slug = (
            self._kc_query_param(base_url, "manga")
            or self._kc_extract_series_slug(base_url)
        )

        for node in self._kc_all(tree, self.kc_chapter_selector):
            # Cas 1 : balise <option> avec attribut value="chapter_num".
            if node.tag == "option":
                value = self._kc_attr(node, "value")
                if not value:
                    continue
                label = self._kc_text(node) or f"Chapter {value}"
                chapter_num = value
                abs_url = (
                    f"{self.base_url}{self.kc_reader_path}"
                    f"?manga={manga_slug}&chapter={chapter_num}"
                )
            else:
                # Cas 2 : balise <a href>.
                href = self._kc_attr(node, "href")
                if not href or "chapter=" not in href:
                    continue
                chapter_num = self._kc_query_param(href, "chapter")
                if not chapter_num:
                    continue
                label = self._kc_text(node) or f"Chapter {chapter_num}"
                abs_url = self._kc_abs(href, base_url)

            if abs_url in seen:
                continue
            seen.add(abs_url)

            number = self._kc_chapter_number(label)
            volume = self._kc_chapter_volume(label)

            source_id = f"{manga_slug}-ch-{chapter_num}"
            chapters.append(
                Chapter(
                    id=f"{self.config.id}:{source_id}",
                    source_id=source_id,
                    title=label,
                    number=number,
                    volume=volume,
                    language=language,
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
        self.logger.debug(
            "KireiCake chapitres: {n}", n=len(chapters)
        )
        return chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre Kirei Cake.

        Le reader Kirei Cake injecte les images côté client via AJAX. Le
        parseur utilise trois stratégies par ordre de priorité :

        1. Appel direct à l'endpoint JSON ``/reader/pages/``.
        2. Rendu Playwright avec attente des images injectées.
        3. Extraction via variable JS embarquée.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "KireiCake get_pages: {url}", url=str(chapter.url)
        )
        chapter_url = str(chapter.url)
        urls: list[str] = []

        # Priorité 1 : endpoint JSON direct.
        try:
            urls = await self._kc_fetch_pages_via_api(chapter_url)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(
                "API reader KO, fallback Playwright : {err}", err=exc
            )

        # Priorité 2 : rendu Playwright + extraction DOM.
        if not urls:
            try:
                rendered = await self.fetch_rendered(
                    chapter_url,
                    wait_until="networkidle",
                    wait_for_selector="img.reader_image, #images img, .reader_container img",
                    timeout=30.0,
                    bypass_cloudflare=True,
                    block_resources=False,
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.error(
                    "Échec get_pages KireiCake pour {url!r}: {err}",
                    url=chapter_url,
                    err=exc,
                )
                raise ParseError(
                    f"get_pages échoué sur {self.site_id!r} "
                    f"pour {chapter_url!r}: {exc}"
                ) from exc

            html = rendered.html
            tree = HTMLParser(html)

            # Extraction DOM.
            for node in self._kc_all(tree, self.kc_page_img_selector):
                src = (
                    self._kc_attr(node, "data-src")
                    or self._kc_attr(node, "data-lazy-src")
                    or self._kc_attr(node, "data-original")
                    or self._kc_attr(node, "src")
                )
                if not src:
                    continue
                abs_url = self._kc_abs(src, chapter_url)
                if abs_url not in urls:
                    urls.append(abs_url)

            # Fallback : variable JS embarquée.
            if not urls:
                urls = self._kc_extract_pages_from_js(html, base_url=chapter_url)

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
            clean = self._kc_abs(clean, chapter_url)
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
            "KireiCake get_pages: {n} page(s) extraite(s)", n=len(pages)
        )
        return pages

    async def _kc_fetch_pages_via_api(
        self, chapter_url: str
    ) -> list[str]:
        """Récupère les URLs des pages via l'endpoint JSON interne.

        Args:
            chapter_url: URL du chapitre.

        Returns:
            Liste d'URLs d'images (vide si l'endpoint ne répond pas).
        """
        manga_slug = self._kc_query_param(chapter_url, "manga")
        chapter_num = self._kc_query_param(chapter_url, "chapter")
        if not manga_slug or not chapter_num:
            return []

        endpoint = self.kc_pages_endpoint
        params = {
            "manga": manga_slug,
            "chapter": chapter_num,
        }

        try:
            response = await self.cf_get(
                f"{self.base_url}{endpoint}",
                params=params,
                timeout=15.0,
            )
        except Exception:  # noqa: BLE001
            return []

        if response.status_code >= 400:
            return []

        try:
            payload = response.json()
        except ValueError:
            return []

        return self._kc_flatten_page_urls(payload)

    @staticmethod
    def _kc_flatten_page_urls(payload: Any) -> list[str]:
        """Aplatit une charge utile JSON en liste d'URLs.

        Args:
            payload: Charge JSON quelconque.

        Returns:
            Liste d'URLs trouvées.
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
                for key in ("url", "src", "image", "page", "images"):
                    if key in obj:
                        _walk(obj[key])
                for value in obj.values():
                    if isinstance(value, (list, dict)):
                        _walk(value)

        _walk(payload)
        return urls

    def _kc_extract_pages_from_js(
        self,
        html: str,
        *,
        base_url: str,
    ) -> list[str]:
        """Extrait les URLs de pages depuis une variable JS embarquée.

        Args:
            html: HTML du chapitre.
            base_url: URL de la page.

        Returns:
            Liste d'URLs résolues (vide si introuvable).
        """
        for var_name in self.kc_pages_var_names:
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
                if not isinstance(parsed, list):
                    continue
                urls: list[str] = []
                for entry in parsed:
                    if isinstance(entry, str):
                        urls.append(self._kc_abs(entry, base_url))
                    elif isinstance(entry, dict):
                        for key in ("url", "src", "image"):
                            val = entry.get(key)
                            if isinstance(val, str):
                                urls.append(self._kc_abs(val, base_url))
                                break
                if urls:
                    return urls
        return []

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que Kirei Cake est accessible.

        Teste le domaine principal puis les domaines de fallback.

        Returns:
            ``True`` si un domaine répond correctement.
        """
        for base in self.fallback_domains:
            try:
                rendered = await self.fetch_rendered(
                    base,
                    wait_until="domcontentloaded",
                    timeout=20.0,
                    bypass_cloudflare=True,
                    screenshot=False,
                )
                if rendered.ok:
                    self.logger.info(
                        "Health check KireiCake OK: {base} (status={s})",
                        base=base,
                        s=rendered.status,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Health check KireiCake échec sur {base}: {err}",
                    base=base,
                    err=exc,
                )
        self.logger.warning("Health check KireiCake KO (tous domaines)")
        return False
