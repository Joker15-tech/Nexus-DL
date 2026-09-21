"""Parser complet pour Pururin (https://pururin.to).

Site adulte international de doujinshi hentai, fonctionnant sous
Vue.js (frontend) + Laravel (backend). Contrairement à la majorité des
sites du projet (thème WordPress Madara), Pururin expose une **API JSON
interne** non documentée qu'il est préférable d'utiliser pour :

    - La recherche (``/api/search``).
    - Les métadonnées d'un livre (``/api/book/{id}``).
    - La liste des images (incluses dans la réponse ``/api/book/{id}``).

Un fallback HTML est implémenté si l'API est indisponible.

Statut juridique
================

Pururin a été **saisi** (seized) en décembre 2024 — les domaines
``pururin.io`` et ``pururin.com`` pointaient vers ``ns1.seizedservers.com``.
Le domaine ``pururin.to`` semble fonctionner mais son statut est
**incertain**. Ce parser est fourni à titre technique uniquement ;
l'utilisateur reste responsable du respect des lois de son pays.

Fonctionnalités intégrées
=========================

    - **Recherche** : via API ``/api/search?q=...&page=N`` avec fallback HTML.
    - **Livre** : via API ``/api/book/{id}`` (retourne métadonnées + images).
    - **Images** : URLs du CDN ``cdn.pururin.to`` — directes, pas de scraping
      nécessaire grâce à l'API.
    - **Cloudflare** : bypass via Playwright si challenge détecté.
    - **Téléchargement** : retry, validation taille/MIME, écriture atomique.
    - **Health check** : distingue API fonctionnelle / challenge / domaine down.

Différences avec les parsers Madara
===================================

    - **Pas de chapitres multiples** : un "livre" Pururin = un doujinshi
      complet. Le parser crée un chapitre unique virtuel.
    - **API JSON** : les données sont récupérées via des endpoints JSON,
      pas via du parsing HTML (sauf fallback).
    - **CDN dédié** : les images sont servies depuis ``cdn.pururin.to``,
      pas depuis le domaine principal.
    - **IDs numériques** : un livre est identifié par un entier (``61119``),
      pas par un slug.

Example:
    Utilisation directe::

        from nexusdl.parsers.adult.pururin import PururinParser
        from nexusdl.core.session.http_session import HttpSession

        async with HttpSession(site_config) as session:
            parser = PururinParser(site_config, session)
            # Recherche
            results = await parser.search("reitama")
            # Détails d'un livre
            manga = await parser.get_manga("61119")

Warning:
    Ce parser est marqué ``adult = True``. Il ne sera **pas** chargé si
    ``registry.include_adult`` est à ``False`` (défaut).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import re
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final
from urllib.parse import urljoin, urlparse

from loguru import logger

from nexusdl.core.constants import (
    ContentRating,
    Language,
    MangaStatus,
)
from nexusdl.core.exceptions import (
    ChapterDownloadError,
    MangaNotFoundError,
    ParserError,
    SiteUnreachableError,
)
from nexusdl.core.models.manga import Chapter, Manga, Page
from nexusdl.parsers.base import BaseParser, SearchResult

if TYPE_CHECKING:
    from nexusdl.core.models.site import SiteConfig
    from nexusdl.core.session.http_session import HttpSession
    from nexusdl.core.session.playwright_pool import PlaywrightPool

# ============================================================================
#  Constantes du site
# ============================================================================

#: Domaines miroirs connus, par ordre de préférence.
#:
#: Historique :
#:   - ``pururin.io``  : saisi en décembre 2024 (seizedservers.com)
#:   - ``pururin.com`` : down (DNS non résolu)
#:   - ``pururin.to``  : domaine actuel (statut incertain)
#:   - ``pururin.net`` : ancien domaine, redirige
MIRROR_DOMAINS: Final[tuple[str, ...]] = (
    "https://pururin.to",
    "https://pururin.net",
)

#: Domaine canonique du parser.
BASE_URL: Final[str] = MIRROR_DOMAINS[0]

#: Marqueurs de domaines historiques à réécrire automatiquement.
REDIRECT_MARKERS: Final[tuple[str, ...]] = (
    "pururin.io",
    "pururin.com",
    "pururin.net",
)

#: Base du CDN pour les images.
CDN_BASE: Final[str] = "https://cdn.pururin.to"

#: Chemins URL (routes Laravel).
SEARCH_PATH: Final[str] = "/search"
GALLERY_PATH_TEMPLATE: Final[str] = "/gallery/{book_id}/{slug}"
BOOK_API_TEMPLATE: Final[str] = "/api/book/{book_id}"
SEARCH_API_PATH: Final[str] = "/api/search"

#: Paramètres API.
API_PAGE_PARAM: Final[str] = "page"
API_QUERY_PARAM: Final[str] = "q"

#: Sélecteurs CSS (fallback HTML si l'API est indisponible).
SELECTORS: Final[dict[str, str]] = {
    # Recherche HTML
    "search_item": "div.gallery-item, div.card",
    "search_title": "a.gallery-title, h3.card-title a",
    "search_cover": "img.gallery-cover, img.card-img-top",
    "search_link": "a.gallery-title, h3.card-title a",
    # Page livre HTML
    "book_title": "h1.gallery-title, h1.book-title",
    "book_cover": "img.gallery-cover, div.cover img",
    "book_tags": "a.tag, span.tag",
    "book_artist": "a.artist, span.artist",
    # Fallback images HTML (si l'API ne les fournit pas)
    "page_image": "div.gallery-images img, div.reader img",
}

#: Timeouts.
HTTP_TIMEOUT: Final[float] = 30.0
PLAYWRIGHT_TIMEOUT: Final[float] = 45.0
PAGE_DOWNLOAD_TIMEOUT: Final[float] = 45.0

#: Validation d'images.
MIN_IMAGE_BYTES: Final[int] = 1024                # 1 KiB
MAX_IMAGE_BYTES: Final[int] = 32 * 1024 * 1024    # 32 MiB

#: Extensions acceptées.
ALLOWED_IMAGE_EXTS: Final[frozenset[str]] = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"},
)

#: MIME types acceptés.
ALLOWED_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/avif",
    },
)

#: Marqueurs de challenge Cloudflare.
CLOUDFLARE_MARKERS: Final[tuple[str, ...]] = (
    "Just a moment",
    "cf-chl-",
    "Checking your browser",
    "cf_chl_",
    "__cf_bm",
    "turnstile",
    "challenge-platform",
)

#: Domaines de tracking à exclure.
TRACKING_DOMAINS: Final[tuple[str, ...]] = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "facebook.com/tr",
    "analytics.",
    "pixel.",
    "stats.",
)

#: User-Agent de secours.
DEFAULT_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ============================================================================
#  Regex patterns
# ============================================================================

#: Extraction d'un ID de livre depuis une URL.
BOOK_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"/gallery/(\d+)",
)

#: Détection des blobs JS.
BLOB_URL_PATTERN: Final[re.Pattern[str]] = re.compile(r"^blob:")

#: Détection de marqueurs premium (rare sur Pururin, mais possible).
PREMIUM_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(premium|vip|locked|payant)\b",
    re.IGNORECASE,
)


# ============================================================================
#  Helpers internes
# ============================================================================


def _clean_text(text: str | None) -> str:
    """Nettoie un texte (entités HTML, whitespace).

    Args:
        text: Texte brut ou None.

    Returns:
        Texte nettoyé, ou chaîne vide.
    """
    if not text:
        return ""
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_blob_url(url: str) -> bool:
    """Détecte une URL ``blob:``.

    Args:
        url: URL à tester.

    Returns:
        True si blob.
    """
    return bool(BLOB_URL_PATTERN.match(url))


def _is_tracking_url(url: str) -> bool:
    """Détecte une URL de tracking.

    Args:
        url: URL à tester.

    Returns:
        True si tracking.
    """
    url_lower = url.lower()
    return any(td in url_lower for td in TRACKING_DOMAINS)


def _is_cloudflare_challenge(html: str) -> bool:
    """Détecte une page de challenge Cloudflare.

    Args:
        html: Contenu HTML.

    Returns:
        True si challenge détecté.
    """
    html_lower = html.lower()
    return any(marker.lower() in html_lower for marker in CLOUDFLARE_MARKERS)


def _normalize_image_url(url: str, base: str) -> str:
    """Normalise une URL d'image.

    Args:
        url: URL brute.
        base: URL de base pour résolution.

    Returns:
        URL absolue.
    """
    url = url.strip()
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith(("http://", "https://")):
        return url
    return urljoin(base, url)


def _extract_book_id(url: str) -> str | None:
    """Extrait l'ID numérique d'un livre depuis une URL.

    Args:
        url: URL du livre.

    Returns:
        ID numérique, ou None.
    """
    match = BOOK_ID_PATTERN.search(url)
    return match.group(1) if match else None


def _compute_sha256(data: bytes) -> str:
    """Calcule le hash SHA256.

    Args:
        data: Données binaires.

    Returns:
        Hash hexadécimal.
    """
    return hashlib.sha256(data).hexdigest()


def _guess_slug(title: str) -> str:
    """Génère un slug à partir d'un titre (fallback si absent de l'API).

    Args:
        title: Titre brut.

    Returns:
        Slug en kebab-case ASCII.
    """
    import unicodedata  # noqa: PLC0415

    normalized = unicodedata.normalize("NFKD", title)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    return slug or "book"


# ============================================================================
#  Parser
# ============================================================================


class PururinParser(BaseParser):
    """Parser complet pour Pururin (https://pururin.to).

    Contrairement aux parsers Madara, Pururin expose une API JSON interne
    qui fournit directement les métadonnées et la liste des images. Le
    parser tente l'API en priorité et bascule sur le parsing HTML si
    nécessaire.

    Structure des données
    ---------------------

    Un "livre" Pururin correspond à un doujinshi complet. Le parser crée
    un **chapitre unique virtuel** qui contient toutes les pages :

        - ``Manga.title`` = titre du livre
        - ``Manga.chapters`` = [Chapter unique avec toutes les pages]
        - ``Chapter.number`` = ``"1"`` (virtuel)
        - ``Chapter.pages`` = liste des pages du CDN

    L'appelant peut traiter ce doujinshi comme un manga à un seul chapitre.

    Attributes:
        site_id: Identifiant du parser (``"pururin"``).
        language: Langue (``"en"`` — site international, contenu souvent
            sans dialogue ou en japonais).
        adult: Contenu 18+ (``True``).
        base_url: URL canonique.
        mirror_domains: Miroirs.
        cloudflare_strategy: Stratégie de bypass.
    """

    # --- Identité ---------------------------------------------------------
    site_id: ClassVar[str] = "pururin"
    language: ClassVar[str] = "en"
    adult: ClassVar[bool] = True

    # --- URLs -------------------------------------------------------------
    base_url: ClassVar[str] = BASE_URL
    mirror_domains: ClassVar[tuple[str, ...]] = MIRROR_DOMAINS
    cdn_base: ClassVar[str] = CDN_BASE

    # --- Options ----------------------------------------------------------
    search_path: ClassVar[str] = SEARCH_PATH
    gallery_path_template: ClassVar[str] = GALLERY_PATH_TEMPLATE
    book_api_template: ClassVar[str] = BOOK_API_TEMPLATE
    search_api_path: ClassVar[str] = SEARCH_API_PATH

    # --- Sélecteurs (fallback HTML) --------------------------------------
    selectors: ClassVar[dict[str, str]] = SELECTORS

    # --- Cloudflare -------------------------------------------------------
    cloudflare_strategy: ClassVar[str] = "playwright"
    cloudflare_cookie_name: ClassVar[str] = "cf_clearance"

    # --- Timeouts ---------------------------------------------------------
    playwright_timeout: ClassVar[float] = PLAYWRIGHT_TIMEOUT
    http_timeout: ClassVar[float] = HTTP_TIMEOUT
    page_download_timeout: ClassVar[float] = PAGE_DOWNLOAD_TIMEOUT

    # --- Content rating ---------------------------------------------------
    content_rating: ClassVar[ContentRating] = ContentRating.PORNOGRAPHIC

    # ------------------------------------------------------------------------
    #  Construction
    # ------------------------------------------------------------------------

    def __init__(
        self,
        config: SiteConfig,
        session: HttpSession,
        *,
        playwright_pool: PlaywrightPool | None = None,
    ) -> None:
        """Initialise le parser Pururin.

        Args:
            config: Configuration du site.
            session: Session HTTP configurée (cookies, proxy, rate limit).
            playwright_pool: Pool Playwright. Recommandé si Cloudflare
                bloque les requêtes API.
        """
        super().__init__(config, session, playwright_pool=playwright_pool)

        self._mirror_index: int = 0
        self._active_base: str = self.base_url
        self._closed: bool = False
        # Cache des livres (id → dict de données API)
        self._book_cache: dict[str, dict[str, Any]] = {}

        logger.bind(site=self.site_id).debug(
            "Parser Pururin initialisé (playwright={}, cdn={})",
            playwright_pool is not None,
            self.cdn_base,
        )

    # ------------------------------------------------------------------------
    #  Contexte async
    # ------------------------------------------------------------------------

    async def __aenter__(self) -> PururinParser:
        """Entre dans le contexte async.

        Returns:
            Le parser.
        """
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        """Sort du contexte async.

        Args:
            *exc_info: Infos sur l'exception.
        """
        await self.close()

    async def close(self) -> None:
        """Libère les ressources (idempotent)."""
        if self._closed:
            return
        self._closed = True
        self._book_cache.clear()
        logger.bind(site=self.site_id).debug("Parser Pururin fermé")

    # ------------------------------------------------------------------------
    #  API publique — recherche
    # ------------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        page: int = 1,
    ) -> list[SearchResult]:
        """Recherche des livres sur Pururin.

        Tente d'abord l'API JSON (``/api/search``). Si elle échoue ou
        retourne un résultat vide, bascule sur le parsing HTML.

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-indexé).

        Returns:
            Liste de résultats.

        Raises:
            ParserError: Si la query est vide ou la page invalide.
            SiteUnreachableError: Si aucun miroir ne répond.
        """
        query = query.strip()
        if not query:
            msg = "query vide après normalisation"
            raise ParserError(msg)
        if page < 1:
            msg = f"page doit être >= 1, reçu {page}"
            raise ParserError(msg)

        logger.bind(site=self.site_id).debug(
            "Recherche '{}' page {} (miroir: {})",
            query,
            page,
            self._active_base,
        )

        # Tentative API JSON
        results = await self._search_via_api(query, page=page)

        if results:
            logger.bind(site=self.site_id).info(
                "{} résultat(s) API pour '{}' (page {})",
                len(results),
                query,
                page,
            )
            return results

        # Fallback HTML
        logger.bind(site=self.site_id).debug(
            "API vide, bascule sur parsing HTML pour '{}'", query
        )
        results = await self._search_via_html(query, page=page)

        logger.bind(site=self.site_id).info(
            "{} résultat(s) HTML pour '{}' (page {})",
            len(results),
            query,
            page,
        )
        return results

    async def _search_via_api(
        self,
        query: str,
        *,
        page: int = 1,
    ) -> list[SearchResult]:
        """Recherche via l'API JSON interne.

        Args:
            query: Terme de recherche.
            page: Numéro de page.

        Returns:
            Liste de résultats (vide si l'API échoue).
        """
        url = self._active_base + self.search_api_path
        params = {
            API_QUERY_PARAM: query,
            API_PAGE_PARAM: str(page),
        }

        try:
            response = await self._fetch_json(url, params=params)
        except (SiteUnreachableError, ParserError) as exc:
            logger.bind(site=self.site_id).debug("API search échouée : {}", exc)
            return []

        # Structure attendue : {"data": [...], "meta": {...}}
        # ou {"books": [...]} selon la version de l'API.
        items = (
            response.get("data")
            or response.get("books")
            or response.get("results")
            or []
        )
        if not isinstance(items, list):
            return []

        results: list[SearchResult] = []
        seen_ids: set[str] = set()

        for item in items:
            try:
                book_id = str(item.get("id") or item.get("book_id") or "")
                if not book_id or book_id in seen_ids:
                    continue
                seen_ids.add(book_id)

                title = _clean_text(item.get("title") or item.get("name") or "")
                if not title:
                    continue

                slug = item.get("slug") or _guess_slug(title)
                url_str = (
                    f"{self._active_base}"
                    f"{self.gallery_path_template.format(book_id=book_id, slug=slug)}"
                )

                cover_url: str | None = None
                raw_cover = item.get("cover") or item.get("thumbnail") or item.get("image")
                if raw_cover:
                    cover_url = _normalize_image_url(str(raw_cover), self.cdn_base)

                artist: str | None = None
                raw_artist = item.get("artist") or item.get("author")
                if raw_artist:
                    artist = _clean_text(str(raw_artist)) or None

                results.append(
                    SearchResult(
                        source_id=book_id,
                        title=title,
                        url=url_str,  # type: ignore[arg-type]
                        cover_url=cover_url,  # type: ignore[arg-type]
                        site_id=self.site_id,
                        language=self.language,
                        adult=self.adult,
                        author=artist,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.bind(site=self.site_id).debug(
                    "Erreur parsing item API : {}", exc
                )
                continue

        return results

    async def _search_via_html(
        self,
        query: str,
        *,
        page: int = 1,
    ) -> list[SearchResult]:
        """Recherche via parsing HTML (fallback).

        Args:
            query: Terme de recherche.
            page: Numéro de page.

        Returns:
            Liste de résultats.
        """
        from selectolax.parser import HTMLParser  # noqa: PLC0415

        params: dict[str, str] = {"q": query}
        if page > 1:
            params["page"] = str(page)

        html = await self._fetch_html(
            self._active_base + self.search_path,
            params=params,
        )

        tree = HTMLParser(html)
        results: list[SearchResult] = []
        seen_ids: set[str] = set()

        for item in tree.css(self.selectors["search_item"]):
            try:
                link_node = item.css_first(self.selectors["search_link"])
                if link_node is None:
                    continue

                link = link_node.attributes.get("href") or ""
                if not link:
                    continue

                url = self.normalize_url(link)
                book_id = _extract_book_id(url)
                if not book_id or book_id in seen_ids:
                    continue
                seen_ids.add(book_id)

                title_node = item.css_first(self.selectors["search_title"])
                title = _clean_text(title_node.text()) if title_node else ""
                if not title:
                    continue

                cover_url: str | None = None
                cover_node = item.css_first(self.selectors["search_cover"])
                if cover_node is not None:
                    raw_cover = (
                        cover_node.attributes.get("data-src")
                        or cover_node.attributes.get("src")
                        or ""
                    )
                    if raw_cover:
                        cover_url = _normalize_image_url(raw_cover, self.cdn_base)

                results.append(
                    SearchResult(
                        source_id=book_id,
                        title=title,
                        url=url,  # type: ignore[arg-type]
                        cover_url=cover_url,  # type: ignore[arg-type]
                        site_id=self.site_id,
                        language=self.language,
                        adult=self.adult,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.bind(site=self.site_id).debug(
                    "Erreur parsing item HTML : {}", exc
                )
                continue

        return results

    # ------------------------------------------------------------------------
    #  API publique — manga
    # ------------------------------------------------------------------------

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère les métadonnées complètes d'un livre Pururin.

        Appelle l'API ``/api/book/{id}`` qui retourne à la fois les
        métadonnées (titre, tags, artiste) et la liste complète des
        images. Le parser construit un ``Manga`` avec un chapitre unique
        virtuel contenant toutes les pages.

        Args:
            url_or_id: URL complète, chemin, ou ID numérique.

        Returns:
            Objet Manga peuplé.

        Raises:
            MangaNotFoundError: Si l'API retourne 404.
            SiteUnreachableError: Si le site ne répond pas.
        """
        book_id = self._resolve_book_id(url_or_id)
        logger.bind(site=self.site_id).debug("Récupération livre ID={}", book_id)

        # Tentative API JSON
        data = await self._get_book_via_api(book_id)

        if data is None:
            # Fallback HTML
            logger.bind(site=self.site_id).debug(
                "API book vide, bascule HTML pour ID={}", book_id
            )
            url = self._build_gallery_url(book_id, slug="book")
            html = await self._fetch_html(url)
            if self._is_404_page(html):
                msg = f"Livre introuvable : {book_id}"
                raise MangaNotFoundError(msg)
            manga = self._parse_book_html(html, book_id, url)
        else:
            manga = self._parse_book_api(data, book_id)

        logger.bind(site=self.site_id).info(
            "Livre récupéré : '{}' ({} page(s), {} tag(s))",
            manga.title,
            len(manga.chapters[0].pages) if manga.chapters else 0,
            len(manga.genres),
        )
        return manga

    async def _get_book_via_api(self, book_id: str) -> dict[str, Any] | None:
        """Récupère un livre via l'API JSON.

        Args:
            book_id: ID numérique du livre.

        Returns:
            Données JSON, ou None si l'API échoue.
        """
        # Cache hit
        if book_id in self._book_cache:
            return self._book_cache[book_id]

        url = self._active_base + self.book_api_template.format(book_id=book_id)

        try:
            data = await self._fetch_json(url)
        except (SiteUnreachableError, ParserError) as exc:
            logger.bind(site=self.site_id).debug(
                "API book échouée pour ID={} : {}", book_id, exc
            )
            return None

        if not isinstance(data, dict):
            return None

        # Certains endpoints enveloppent la réponse : {"data": {...}}
        payload = data.get("data") if "data" in data else data
        if not isinstance(payload, dict):
            return None

        self._book_cache[book_id] = payload
        return payload

    def _parse_book_api(self, data: dict[str, Any], book_id: str) -> Manga:
        """Parse les données API d'un livre en objet Manga.

        Args:
            data: Données JSON de l'API.
            book_id: ID du livre.

        Returns:
            Objet Manga peuplé.
        """
        title = _clean_text(data.get("title") or data.get("name") or f"Book {book_id}")
        slug = data.get("slug") or _guess_slug(title)
        url = f"{self._active_base}{self.gallery_path_template.format(book_id=book_id, slug=slug)}"

        # Description (rarement présente)
        description = _clean_text(data.get("description") or "") or None

        # Couverture
        cover_url: str | None = None
        raw_cover = data.get("cover") or data.get("thumbnail") or data.get("image")
        if raw_cover:
            cover_url = _normalize_image_url(str(raw_cover), self.cdn_base)
        elif data.get("images") and isinstance(data["images"], list) and data["images"]:
            cover_url = _normalize_image_url(str(data["images"][0]), self.cdn_base)

        # Artiste
        author: str | None = None
        raw_artist = data.get("artist") or data.get("author")
        if raw_artist:
            author = _clean_text(str(raw_artist)) or None

        # Tags (peuvent être une liste de str, une liste de dict, ou un str)
        genres = self._extract_tags_from_api(data)

        # Images (liste d'URLs ou liste de dicts avec "src")
        images_raw = data.get("images") or data.get("pages") or []
        image_urls: list[str] = []
        for img in images_raw:
            if isinstance(img, str):
                image_urls.append(_normalize_image_url(img, self.cdn_base))
            elif isinstance(img, dict):
                src = img.get("src") or img.get("url") or img.get("image")
                if src:
                    image_urls.append(_normalize_image_url(str(src), self.cdn_base))

        # Pages
        pages = self._build_pages_from_urls(image_urls)

        # Chapitre unique virtuel
        chapter = self._build_virtual_chapter(book_id, title, url, pages)

        # Statut et année (rarement présents)
        status = MangaStatus.COMPLETED
        year: int | None = None
        raw_year = data.get("year")
        if raw_year:
            with suppress(ValueError, TypeError):
                year = int(raw_year)

        return Manga(
            id=f"{self.site_id}:{book_id}",
            source_id=book_id,
            site=self.site_id,
            title=title,
            alternative_titles=[],
            description=description,
            author=author,
            artist=author,
            genres=genres,
            status=status,
            year=year,
            cover_url=cover_url,  # type: ignore[arg-type]
            language=Language.EN,
            content_rating=self.content_rating,
            chapters=[chapter],
            url=url,  # type: ignore[arg-type]
            updated_at=datetime.now(UTC),
        )

    @staticmethod
    def _extract_tags_from_api(data: dict[str, Any]) -> list[str]:
        """Extrait les tags depuis les données API (format variable).

        Args:
            data: Données JSON.

        Returns:
            Liste de tags nettoyés.
        """
        raw_tags = data.get("tags") or data.get("genres") or []
        tags: list[str] = []

        if isinstance(raw_tags, str):
            # Format "tag1, tag2, tag3" ou "tag1 tag2 tag3"
            parts = re.split(r"[,\n]+", raw_tags)
            tags = [_clean_text(p) for p in parts if _clean_text(p)]
        elif isinstance(raw_tags, list):
            for tag in raw_tags:
                if isinstance(tag, str):
                    cleaned = _clean_text(tag)
                    if cleaned:
                        tags.append(cleaned)
                elif isinstance(tag, dict):
                    name = tag.get("name") or tag.get("title") or tag.get("label")
                    if name:
                        cleaned = _clean_text(str(name))
                        if cleaned:
                            tags.append(cleaned)

        # Déduplique en préservant l'ordre
        return list(dict.fromkeys(tags))

    def _build_pages_from_urls(self, urls: list[str]) -> list[Page]:
        """Construit une liste de Page depuis une liste d'URLs.

        Args:
            urls: URLs des images.

        Returns:
            Liste de pages ordonnées.
        """
        pages: list[Page] = []
        for idx, url in enumerate(urls):
            if _is_blob_url(url):
                logger.bind(site=self.site_id).debug(
                    "Image blob: ignorée (index {})", idx
                )
                continue
            if _is_tracking_url(url):
                continue

            ext = Path(urlparse(url).path).suffix.lower() or ".jpg"
            if ext not in ALLOWED_IMAGE_EXTS:
                ext = ".jpg"
            filename = f"page_{idx:04d}{ext}"

            pages.append(
                Page(
                    index=idx,
                    url=url,  # type: ignore[arg-type]
                    filename=filename,
                    checksum=None,
                )
            )
        return pages

    def _build_virtual_chapter(
        self,
        book_id: str,
        title: str,
        url: str,
        pages: list[Page],
    ) -> Chapter:
        """Construit le chapitre unique virtuel d'un livre Pururin.

        Args:
            book_id: ID du livre.
            title: Titre.
            url: URL du livre.
            pages: Pages du chapitre.

        Returns:
            Chapitre unique contenant toutes les pages.
        """
        return Chapter(
            id=f"{self.site_id}:{book_id}:1",
            source_id=f"{book_id}-1",
            title=title,
            number="1",
            volume=None,
            language=Language.EN,
            pages_count=len(pages),
            published_at=None,
            url=url,  # type: ignore[arg-type]
            pages=pages,
        )

    # ------------------------------------------------------------------------
    #  API publique — chapitres
    # ------------------------------------------------------------------------

    async def get_chapters(self, manga: Manga) -> list[Chapter]:
        """Récupère les chapitres d'un manga Pururin.

        Pururin n'a **pas** de chapitres multiples : un livre = un
        chapitre unique. Cette méthode retourne donc la liste contenant
        le chapitre déjà présent dans ``manga.chapters``.

        Args:
            manga: Manga cible.

        Returns:
            Liste avec un seul chapitre virtuel.
        """
        if manga.chapters:
            return list(manga.chapters)

        # Reconstruction : un seul chapitre virtuel vide
        book_id = manga.source_id
        return [
            Chapter(
                id=f"{self.site_id}:{book_id}:1",
                source_id=f"{book_id}-1",
                title=manga.title,
                number="1",
                volume=None,
                language=Language.EN,
                pages_count=0,
                published_at=None,
                url=str(manga.url),  # type: ignore[arg-type]
                pages=[],
            )
        ]

    # ------------------------------------------------------------------------
    #  API publique — pages
    # ------------------------------------------------------------------------

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre Pururin.

        Si le chapitre contient déjà ses pages (cas normal — extraites
        par ``get_manga``), retourne directement la liste. Sinon, tente
        de re-parser le livre via son URL.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste de pages ordonnées.

        Raises:
            ChapterDownloadError: Si aucune page n'est trouvée.
        """
        # Cas normal : pages déjà extraites par get_manga
        if chapter.pages:
            logger.bind(site=self.site_id).debug(
                "{} page(s) déjà présentes pour '{}'",
                len(chapter.pages),
                chapter.title,
            )
            return list(chapter.pages)

        # Fallback : re-parsing depuis l'URL du chapitre
        url = str(chapter.url)
        book_id = _extract_book_id(url)
        if not book_id:
            msg = f"ID de livre introuvable dans l'URL : {url}"
            raise ChapterDownloadError(msg)

        data = await self._get_book_via_api(book_id)
        if data is None:
            html = await self._fetch_html(url)
            return self._parse_pages_html(html, chapter)

        images_raw = data.get("images") or data.get("pages") or []
        image_urls: list[str] = []
        for img in images_raw:
            if isinstance(img, str):
                image_urls.append(_normalize_image_url(img, self.cdn_base))
            elif isinstance(img, dict):
                src = img.get("src") or img.get("url") or img.get("image")
                if src:
                    image_urls.append(_normalize_image_url(str(src), self.cdn_base))

        pages = self._build_pages_from_urls(image_urls)

        if not pages:
            msg = f"Aucune page trouvée pour le livre {book_id}"
            raise ChapterDownloadError(msg)

        logger.bind(site=self.site_id).info(
            "{} page(s) récupérée(s) pour '{}'",
            len(pages),
            chapter.title,
        )
        return pages

    # ------------------------------------------------------------------------
    #  API publique — téléchargement de page
    # ------------------------------------------------------------------------

    async def download_page(
        self,
        page: Page,
        dest: Path,
        *,
        overwrite: bool = False,
        max_retries: int = 3,
    ) -> Path:
        """Télécharge une page avec retry, validation et écriture atomique.

        Les images Pururin sont servies depuis ``cdn.pururin.to`` — pas
        de protection Cloudflare sur le CDN, téléchargement direct.

        Args:
            page: Page à télécharger.
            dest: Dossier ou chemin de destination.
            overwrite: Écraser si existe.
            max_retries: Nombre max de tentatives.

        Returns:
            Chemin du fichier téléchargé.

        Raises:
            ChapterDownloadError: Si toutes les tentatives échouent.
        """
        dest_path = self._resolve_dest_path(page, dest)

        if dest_path.exists() and not overwrite:
            logger.bind(site=self.site_id).trace(
                "Fichier présent, skip : {}", dest_path
            )
            return dest_path

        dest_path.parent.mkdir(parents=True, exist_ok=True)

        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                return await self._download_page_once(page, dest_path)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.bind(site=self.site_id).warning(
                    "Tentative {}/{} échouée pour {} : {}",
                    attempt,
                    max_retries,
                    page.url,
                    exc,
                )
                if attempt < max_retries:
                    backoff = 0.5 * (2 ** (attempt - 1))
                    await asyncio.sleep(backoff)

        msg = f"Échec téléchargement après {max_retries} tentative(s) : {page.url}"
        raise ChapterDownloadError(msg) from last_error

    def _resolve_dest_path(self, page: Page, dest: Path) -> Path:
        """Résout le chemin de destination.

        Args:
            page: Page cible.
            dest: Dossier ou fichier.

        Returns:
            Chemin complet.
        """
        if dest.is_dir() or not dest.suffix:
            filename = page.filename or f"page_{page.index:04d}.jpg"
            return dest / filename
        return dest

    async def _download_page_once(
        self,
        page: Page,
        dest_path: Path,
    ) -> Path:
        """Télécharge une page en une tentative.

        Args:
            page: Page cible.
            dest_path: Destination finale.

        Returns:
            Chemin du fichier téléchargé.

        Raises:
            ChapterDownloadError: Si validation échoue.
        """
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
        try:
            # Referer CDN pour éviter le hotlink protection
            referer = self.cdn_base if self.cdn_base in str(page.url) else self._active_base

            response = await self._session.get(
                str(page.url),
                headers={"Referer": referer},
                timeout=self.page_download_timeout,
            )

            if response.status_code != 200:  # noqa: PLR2004
                msg = f"HTTP {response.status_code} pour {page.url}"
                raise ChapterDownloadError(msg)

            content = response.content
            content_type = (
                response.headers.get("content-type", "").lower().split(";")[0].strip()
            )

            if content_type and content_type not in ALLOWED_MIME_TYPES:
                if content_type not in {
                    "application/octet-stream",
                    "binary/octet-stream",
                    "",
                }:
                    msg = f"MIME invalide '{content_type}' pour {page.url}"
                    raise ChapterDownloadError(msg)

            if len(content) < MIN_IMAGE_BYTES:
                msg = f"Image trop petite ({len(content)} o) : {page.url}"
                raise ChapterDownloadError(msg)
            if len(content) > MAX_IMAGE_BYTES:
                msg = f"Image trop grande ({len(content)} o) : {page.url}"
                raise ChapterDownloadError(msg)

            dest_path = self._fix_extension(dest_path, content_type)
            tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")

            await asyncio.to_thread(tmp_path.write_bytes, content)

            if page.checksum:
                actual = _compute_sha256(content)
                if actual != page.checksum:
                    msg = f"Checksum mismatch pour {page.url}"
                    raise ChapterDownloadError(msg)

            await asyncio.to_thread(tmp_path.replace, dest_path)

            logger.bind(site=self.site_id).trace(
                "Page écrite : {} ({} o)", dest_path.name, len(content)
            )
            return dest_path

        except Exception:
            if tmp_path.exists():
                with suppress(OSError):
                    tmp_path.unlink()
            raise

    @staticmethod
    def _fix_extension(path: Path, content_type: str) -> Path:
        """Corrige l'extension selon le MIME réel.

        Args:
            path: Chemin actuel.
            content_type: MIME type.

        Returns:
            Chemin corrigé.
        """
        if not content_type:
            return path
        guessed = mimetypes.guess_extension(content_type)
        if not guessed:
            return path
        if guessed == ".jpe":
            guessed = ".jpg"
        if path.suffix.lower() == guessed:
            return path
        return path.with_suffix(guessed)

    # ------------------------------------------------------------------------
    #  API publique — health check
    # ------------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que Pururin est accessible.

        Teste l'API ``/api/search`` qui est plus fiable que la page
        d'accueil (qui peut être lourde à charger).

        Returns:
            True si le site répond correctement.
        """
        try:
            if self._playwright_pool is not None:
                html = await self._playwright_pool.fetch_html(
                    self._active_base,
                    timeout=self.playwright_timeout,
                )
                if _is_cloudflare_challenge(html):
                    logger.bind(site=self.site_id).warning(
                        "Pururin : challenge CF non résolu"
                    )
                    return False
                if len(html) > 1000:  # noqa: PLR2004
                    return True
            else:
                # Test API lightweight
                response = await self._session.get(
                    self._active_base + self.search_api_path,
                    params={"q": "test", "page": "1"},
                    timeout=self.http_timeout,
                )
                if response.status_code == 200:  # noqa: PLR2004
                    if not _is_cloudflare_challenge(response.text):
                        return True
        except Exception as exc:  # noqa: BLE001
            logger.bind(site=self.site_id).debug("Health check échoué : {}", exc)

        for mirror in self.mirror_domains[1:]:
            try:
                response = await self._session.get(
                    mirror, follow_redirects=False, timeout=self.http_timeout
                )
                if response.status_code in (301, 302, 307, 308):
                    location = response.headers.get("location", "")
                    logger.bind(site=self.site_id).warning(
                        "Miroir {} redirige vers {} — mettre à jour MIRROR_DOMAINS",
                        mirror,
                        location,
                    )
                elif response.status_code == 200:  # noqa: PLR2004
                    logger.bind(site=self.site_id).warning(
                        "Domaine principal {} down, miroir {} répond",
                        self._active_base,
                        mirror,
                    )
            except Exception:  # noqa: BLE001, S110
                continue

        logger.bind(site=self.site_id).error(
            "Pururin : aucun domaine accessible. Site possiblement saisi, "
            "suspendu, ou migré vers un domaine non référencé."
        )
        return False

    # ------------------------------------------------------------------------
    #  API publique — URL
    # ------------------------------------------------------------------------

    def normalize_url(self, url: str) -> str:
        """Normalise une URL (relative → absolue, réécriture historique).

        Réécrit les domaines historiques (``.io``, ``.com``, ``.net``) vers
        le domaine canonique (``.to``).

        Args:
            url: URL à normaliser.

        Returns:
            URL absolue normalisée.
        """
        url = url.strip()

        for historical in REDIRECT_MARKERS:
            if historical in url:
                url = url.replace(historical, urlparse(self._active_base).netloc)
                logger.bind(site=self.site_id).trace(
                    "URL historique réécrite : {} → {}", historical, self._active_base
                )
                break

        if url.startswith(("http://", "https://")):
            parsed = urlparse(url)
            expected = urlparse(self._active_base)
            known_mirrors = {urlparse(m).netloc for m in self.mirror_domains}
            if parsed.netloc != expected.netloc and parsed.netloc in known_mirrors:
                url = parsed._replace(
                    netloc=expected.netloc, scheme=expected.scheme
                ).geturl()
            return url

        if url.startswith("/"):
            return self._active_base + url
        return urljoin(self._active_base + "/", url)

    def _resolve_book_id(self, url_or_id: str) -> str:
        """Résout une entrée en ID numérique de livre.

        Args:
            url_or_id: URL, chemin, ou ID numérique.

        Returns:
            ID numérique sous forme de string.

        Raises:
            ParserError: Si l'ID ne peut pas être extrait.
        """
        url_or_id = url_or_id.strip()

        # Cas 1 : ID numérique direct
        if url_or_id.isdigit():
            return url_or_id

        # Cas 2 : URL complète ou chemin
        book_id = _extract_book_id(url_or_id)
        if book_id:
            return book_id

        # Cas 3 : extrait un nombre si présent
        numbers = re.findall(r"\d+", url_or_id)
        if numbers:
            return numbers[0]

        msg = f"Impossible d'extraire un ID de livre depuis : {url_or_id}"
        raise ParserError(msg)

    def _build_gallery_url(self, book_id: str, *, slug: str = "book") -> str:
        """Construit l'URL d'une galerie.

        Args:
            book_id: ID du livre.
            slug: Slug (facultatif, utilisé pour la lisibilité).

        Returns:
            URL complète.
        """
        return self._active_base + self.gallery_path_template.format(
            book_id=book_id, slug=slug
        )

    # ------------------------------------------------------------------------
    #  API publique — rotation de miroirs
    # ------------------------------------------------------------------------

    @property
    def current_mirror(self) -> str:
        """Retourne le miroir actuellement utilisé."""
        return self._active_base

    def rotate_mirror(self) -> str:
        """Bascule vers le miroir suivant (rotation circulaire).

        Returns:
            URL du nouveau miroir actif.
        """
        self._mirror_index = (self._mirror_index + 1) % len(self.mirror_domains)
        self._active_base = self.mirror_domains[self._mirror_index]
        logger.bind(site=self.site_id).info(
            "Rotation miroir : {}", self._active_base
        )
        return self._active_base

    @property
    def is_adult(self) -> bool:
        """True — site 18+."""
        return True

    # ------------------------------------------------------------------------
    #  Fetch JSON
    # ------------------------------------------------------------------------

    async def _fetch_json(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Récupère une réponse JSON depuis l'API Pururin.

        Utilise le fallback Playwright si Cloudflare bloque les requêtes
        API (le JSON est alors extrait du DOM ou d'un script inline).

        Args:
            url: URL de l'endpoint API.
            params: Query parameters.

        Returns:
            Réponse JSON parsée.

        Raises:
            ParserError: Si le JSON est invalide.
            SiteUnreachableError: Si l'endpoint est injoignable.
        """
        # Tentative HTTP directe
        try:
            response = await self._session.get(
                url,
                params=params,
                timeout=self.http_timeout,
                headers={
                    "Accept": "application/json",
                    "Referer": self._active_base,
                    "X-Requested-With": "XMLHttpRequest",
                },
            )

            if response.status_code == 200:  # noqa: PLR2004
                text = response.text
                if not _is_cloudflare_challenge(text):
                    try:
                        data = json.loads(text)
                        if isinstance(data, dict):
                            return data
                        if isinstance(data, list):
                            return {"data": data}
                    except json.JSONDecodeError as exc:
                        logger.bind(site=self.site_id).debug(
                            "JSON invalide depuis {} : {}", url, exc
                        )
            elif response.status_code == 404:  # noqa: PLR2004
                msg = f"404 pour {url}"
                raise ParserError(msg)

        except ParserError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.bind(site=self.site_id).debug(
                "Fetch JSON HTTP échoué : {}", exc
            )

        # Fallback Playwright (extrait le JSON du DOM)
        if self._playwright_pool is not None:
            try:
                full_url = url
                if params:
                    query = "&".join(f"{k}={v}" for k, v in params.items())
                    full_url = f"{url}?{query}"

                html = await self._playwright_pool.fetch_html(
                    full_url,
                    timeout=self.playwright_timeout,
                )
                # Cherche un JSON dans le HTML (souvent dans un <pre> ou script)
                json_match = re.search(r"\{.*\}", html, re.DOTALL)
                if json_match:
                    try:
                        data = json.loads(json_match.group(0))
                        if isinstance(data, dict):
                            return data
                    except json.JSONDecodeError:
                        pass
            except Exception as exc:  # noqa: BLE001
                logger.bind(site=self.site_id).debug(
                    "Fetch JSON Playwright échoué : {}", exc
                )

        msg = f"Impossible de récupérer le JSON depuis {url}"
        raise SiteUnreachableError(msg)

    # ------------------------------------------------------------------------
    #  Fetch HTML — gestion Cloudflare
    # ------------------------------------------------------------------------

    async def _fetch_html(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        max_attempts: int = 3,
    ) -> str:
        """Récupère le HTML d'une URL avec gestion Cloudflare.

        Args:
            url: URL cible.
            params: Query parameters.
            max_attempts: Nombre max de tentatives.

        Returns:
            HTML de la page.

        Raises:
            SiteUnreachableError: Si toutes les stratégies échouent.
        """
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                response = await self._session.get(
                    url,
                    params=params,
                    timeout=self.http_timeout,
                    headers={"Referer": self._active_base},
                )

                if response.status_code == 200:  # noqa: PLR2004
                    html = response.text
                    if not _is_cloudflare_challenge(html):
                        return html
                    logger.bind(site=self.site_id).debug(
                        "Challenge CF détecté (HTTP), bascule Playwright"
                    )
                elif response.status_code in (403, 503):
                    logger.bind(site=self.site_id).debug(
                        "HTTP {} — challenge CF probable", response.status_code
                    )

            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.bind(site=self.site_id).debug(
                    "Tentative HTTP {}/{} échouée : {}", attempt, max_attempts, exc
                )

            if self._playwright_pool is not None:
                try:
                    full_url = url
                    if params:
                        query = "&".join(f"{k}={v}" for k, v in params.items())
                        full_url = f"{url}?{query}"

                    html = await self._playwright_pool.fetch_html(
                        full_url,
                        timeout=self.playwright_timeout,
                    )
                    if not _is_cloudflare_challenge(html):
                        return html
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.bind(site=self.site_id).debug(
                        "Playwright tentative {}/{} échouée : {}",
                        attempt,
                        max_attempts,
                        exc,
                    )

            if attempt < max_attempts:
                backoff = 0.5 * (2 ** (attempt - 1))
                await asyncio.sleep(backoff)

        if len(self.mirror_domains) > 1:
            new_mirror = self.rotate_mirror()
            logger.bind(site=self.site_id).warning(
                "Bascule miroir {} après échec de {}", new_mirror, url
            )
            rotated_url = url.replace(self.mirror_domains[0], new_mirror, 1)
            try:
                response = await self._session.get(
                    rotated_url, params=params, timeout=self.http_timeout
                )
                if response.status_code == 200 and not _is_cloudflare_challenge(response.text):  # noqa: PLR2004
                    return response.text
            except Exception as exc:  # noqa: BLE001
                last_error = exc

        msg = f"Impossible de récupérer {url} après {max_attempts} tentative(s)"
        raise SiteUnreachableError(msg) from last_error

    # ------------------------------------------------------------------------
    #  Parsing HTML (fallback)
    # ------------------------------------------------------------------------

    def _parse_book_html(self, html: str, book_id: str, url: str) -> Manga:
        """Parse la page HTML d'un livre (fallback si API indisponible).

        Args:
            html: HTML de la page.
            book_id: ID du livre.
            url: URL du livre.

        Returns:
            Objet Manga peuplé.
        """
        from selectolax.parser import HTMLParser  # noqa: PLC0415

        tree = HTMLParser(html)

        title_node = tree.css_first(self.selectors["book_title"])
        title = _clean_text(title_node.text()) if title_node else f"Book {book_id}"

        cover_url: str | None = None
        cover_node = tree.css_first(self.selectors["book_cover"])
        if cover_node is not None:
            raw_cover = (
                cover_node.attributes.get("data-src")
                or cover_node.attributes.get("src")
            )
            if raw_cover:
                cover_url = _normalize_image_url(raw_cover, self.cdn_base)

        # Tags
        genres: list[str] = []
        for tag_node in tree.css(self.selectors["book_tags"]):
            tag = _clean_text(tag_node.text())
            if tag and tag not in genres:
                genres.append(tag)

        # Artiste
        author: str | None = None
        artist_node = tree.css_first(self.selectors["book_artist"])
        if artist_node is not None:
            author = _clean_text(artist_node.text()) or None

        # Pages
        pages = self._parse_pages_html(tree, book_id)

        chapter = self._build_virtual_chapter(book_id, title, url, pages)

        return Manga(
            id=f"{self.site_id}:{book_id}",
            source_id=book_id,
            site=self.site_id,
            title=title,
            alternative_titles=[],
            description=None,
            author=author,
            artist=author,
            genres=genres,
            status=MangaStatus.COMPLETED,
            year=None,
            cover_url=cover_url,  # type: ignore[arg-type]
            language=Language.EN,
            content_rating=self.content_rating,
            chapters=[chapter],
            url=url,  # type: ignore[arg-type]
            updated_at=datetime.now(UTC),
        )

    def _parse_pages_html(
        self,
        tree_or_html: Any,
        book_id: str,
    ) -> list[Page]:
        """Parse les images d'une page HTML.

        Args:
            tree_or_html: HTMLParser ou HTML brut.
            book_id: ID du livre (non utilisé, pour signature).

        Returns:
            Liste de pages.
        """
        from selectolax.parser import HTMLParser  # noqa: PLC0415

        tree = (
            tree_or_html
            if isinstance(tree_or_html, HTMLParser)
            else HTMLParser(tree_or_html)
        )

        pages: list[Page] = []
        for idx, img in enumerate(tree.css(self.selectors["page_image"])):
            raw_url = (
                img.attributes.get("data-src")
                or img.attributes.get("data-lazy-src")
                or img.attributes.get("src")
                or ""
            )
            if not raw_url:
                continue
            if _is_blob_url(raw_url) or _is_tracking_url(raw_url):
                continue

            url = _normalize_image_url(raw_url, self.cdn_base)
            ext = Path(urlparse(url).path).suffix.lower() or ".jpg"
            if ext not in ALLOWED_IMAGE_EXTS:
                ext = ".jpg"

            pages.append(
                Page(
                    index=idx,
                    url=url,  # type: ignore[arg-type]
                    filename=f"page_{idx:04d}{ext}",
                    checksum=None,
                )
            )
        return pages

    def _is_404_page(self, html: str) -> bool:
        """Détecte une page 404.

        Args:
            html: HTML de la page.

        Returns:
            True si 404.
        """
        markers = ("404", "not found", "page introuvable")
        from selectolax.parser import HTMLParser  # noqa: PLC0415

        tree = HTMLParser(html)
        for selector in ("title", "h1"):
            node = tree.css_first(selector)
            if node:
                text = node.text().lower()
                if any(m in text for m in markers):
                    return True
        return False

    # ------------------------------------------------------------------------
    #  Introspection
    # ------------------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """Retourne une description du parser.

        Returns:
            Dict sérialisable JSON.
        """
        return {
            "site_id": self.site_id,
            "language": self.language,
            "adult": self.adult,
            "base_url": self.base_url,
            "active_mirror": self._active_base,
            "mirrors": list(self.mirror_domains),
            "cdn_base": self.cdn_base,
            "cloudflare_strategy": self.cloudflare_strategy,
            "supports_api": True,
            "selectors_count": len(self.selectors),
        }


# ============================================================================
#  Exports
# ============================================================================

__all__ = [
    "BASE_URL",
    "CDN_BASE",
    "MIRROR_DOMAINS",
    "SELECTORS",
    "PururinParser",
]
