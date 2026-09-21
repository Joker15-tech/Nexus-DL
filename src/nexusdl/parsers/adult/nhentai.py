"""Parser complet pour nhentai (https://nhentai.net).

nhentai est un site adulte international de doujinshi hentai, fonctionnant
sous une stack custom (non WordPress). Il expose une **API JSON non
officielle** qui fournit directement :

    - Les métadonnées d'une galerie (``/api/gallery/{id}``).
    - La liste complète des images (URLs du CDN ``i.nhentai.net``).
    - Les tags, artistes, langues, catégories.

Le parser utilise cette API en priorité et bascule sur le parsing HTML si
elle est indisponible (Cloudflare challenge non résolu, changement d'API).

Particularités du site
======================

    - **IDs numériques** : identifiant unique à 5-6 chiffres (ex: ``421025``).
    - **Galerie = doujinshi complet** : pas de chapitres multiples. Le parser
      crée un **chapitre unique virtuel** contenant toutes les pages.
    - **CDN dédié** : images servies depuis ``i.nhentai.net`` (vignettes :
      ``t.nhentai.net``), non protégées par Cloudflare.
    - **Protection Cloudflare** : ``nhentai.net`` est protégé. Un cookie
      ``cf_clearance`` valide + User-Agent cohérent sont requis pour l'API.
    - **Domaine miroir** : ``nhentai.to`` comme fallback (moins protégé).

Format de l'API JSON
====================

Endpoint : ``GET https://nhentai.net/api/gallery/{id}``

Réponse (structure connue) ::

    {
        "id": 421025,
        "media_id": "987560",
        "title": {
            "english": "...",
            "japanese": "...",
            "pretty": "..."
        },
        "images": {
            "pages": [
                {"t": "j", "w": 1280, "h": 1810},  # type j = jpg
                {"t": "p", "w": 1280, "h": 1810},  # type p = png
                {"t": "w", "w": 1280, "h": 1810}   # type w = webp
            ],
            "cover": {"t": "j", "w": 350, "h": 500},
            "thumbnail": {"t": "j", "w": 250, "h": 350}
        },
        "scanlator": "",
        "upload_date": 1699999999,
        "tags": [
            {"id": 1, "type": "tag", "name": "big breasts", "url": "/tag/..."},
            {"id": 2, "type": "artist", "name": "some artist", ...},
            {"id": 3, "type": "language", "name": "english", ...},
            {"id": 4, "type": "category", "name": "doujinshi", ...},
            {"id": 5, "type": "parody", "name": "original", ...},
            {"id": 6, "type": "group", "name": "some group", ...},
            {"id": 7, "type": "character", "name": "some char", ...}
        ],
        "num_pages": 24,
        "num_favorites": 1234
    }

Construction des URLs d'images
------------------------------

    - Pages   : ``https://i.nhentai.net/galleries/{media_id}/{page_num}.{ext}``
    - Cover   : ``https://t.nhentai.net/galleries/{media_id}/cover.{ext}``
    - Thumb   : ``https://t.nhentai.net/galleries/{media_id}/{page_num}t.{ext}``

    où ``{ext}`` dépend de ``t`` :
        - ``j`` → ``jpg``
        - ``p`` → ``png``
        - ``w`` → ``webp``
        - ``g`` → ``gif``

Statut juridique
================

nhentai fait l'objet de poursuites judiciaires (PCR Distributing, US, août
2024). Le domaine a été bloqué en Ukraine (décembre 2024). Ce parser est
fourni à titre technique ; l'utilisateur reste responsable du respect des
lois de son pays.

Example:
    Utilisation directe::

        from nexusdl.parsers.adult.nhentai import NHentaiParser
        from nexusdl.core.session.http_session import HttpSession

        async with HttpSession(site_config) as session:
            parser = NHentaiParser(site_config, session)
            # Recherche
            results = await parser.search("reitama")
            # Détails d'une galerie (ID numérique)
            manga = await parser.get_manga("421025")
            # Télécharger une page
            await parser.download_page(manga.chapters[0].pages[0], Path("/tmp"))

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
#: Note : ``nhentai.net`` est le domaine officiel (protégé Cloudflare).
#: ``nhentai.to`` est un miroir moins protégé mais parfois obsolète.
MIRROR_DOMAINS: Final[tuple[str, ...]] = (
    "https://nhentai.net",
    "https://nhentai.to",
)

#: Domaine canonique du parser.
BASE_URL: Final[str] = MIRROR_DOMAINS[0]

#: Marqueurs de domaines historiques à réécrire.
REDIRECT_MARKERS: Final[tuple[str, ...]] = (
    "nhentai.com",
    "nhentai.ru",
)

#: Bases CDN pour les images.
CDN_IMAGE_BASE: Final[str] = "https://i.nhentai.net"
CDN_THUMB_BASE: Final[str] = "https://t.nhentai.net"

#: Chemins URL (routes nhentai).
GALLERY_PATH_TEMPLATE: Final[str] = "/g/{book_id}/"
SEARCH_PATH: Final[str] = "/search"
GALLERY_API_TEMPLATE: Final[str] = "/api/gallery/{book_id}"
SEARCH_API_PATH: Final[str] = "/api/v2/galleries/search"
RANDOM_API_PATH: Final[str] = "/api/v2/galleries/random"

#: Paramètres API.
API_PAGE_PARAM: Final[str] = "page"
API_QUERY_PARAM: Final[str] = "q"

#: Sélecteurs CSS (fallback HTML).
SELECTORS: Final[dict[str, str]] = {
    # Recherche HTML
    "search_item": "div.gallery, div.container div.gallery",
    "search_title": "div.caption, a > div.caption",
    "search_cover": "img.lazyload, img.cover",
    "search_link": "a.cover, a",
    # Page galerie HTML
    "gallery_title": "h1.title, h2.title",
    "gallery_cover": "img#cover, div#cover img",
    "gallery_tags": "span.tag a, div.tag-container a",
    "gallery_artist": "a.tag[href*='/artist/'], span.artist a",
    # Fallback images (si l'API ne les fournit pas)
    "page_image": "div#image-container img, div.thumb-container img",
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

#: Mapping des types d'images nhentai → extension.
IMAGE_TYPE_TO_EXT: Final[dict[str, str]] = {
    "j": "jpg",
    "p": "png",
    "w": "webp",
    "g": "gif",
    "jpeg": "jpg",
}

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

#: User-Agent de secours (cohérent avec les cookies cf_clearance).
DEFAULT_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ============================================================================
#  Regex patterns
# ============================================================================

#: Extraction d'un ID de galerie depuis une URL nhentai.
BOOK_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"/g/(\d+)",
)

#: Extraction d'un ID de galerie depuis une URL d'API.
API_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"/gallery/(\d+)",
)

#: Détection des blobs JS.
BLOB_URL_PATTERN: Final[re.Pattern[str]] = re.compile(r"^blob:")

#: Détection des tokens CSRF dans le HTML.
CSRF_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'csrf[-_]?token["\']?\s*[:=]\s*["\']([^"\']+)',
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
    """Extrait l'ID numérique d'une galerie depuis une URL.

    Args:
        url: URL de la galerie.

    Returns:
        ID numérique, ou None.
    """
    match = BOOK_ID_PATTERN.search(url) or API_ID_PATTERN.search(url)
    return match.group(1) if match else None


def _compute_sha256(data: bytes) -> str:
    """Calcule le hash SHA256.

    Args:
        data: Données binaires.

    Returns:
        Hash hexadécimal.
    """
    return hashlib.sha256(data).hexdigest()


def _image_ext_from_type(img_type: str | None) -> str:
    """Convertit un type d'image nhentai en extension.

    Args:
        img_type: Type (``"j"``, ``"p"``, ``"w"``, ``"g"``).

    Returns:
        Extension avec point (``".jpg"``, etc.).
    """
    if not img_type:
        return ".jpg"
    ext = IMAGE_TYPE_TO_EXT.get(img_type.lower(), "jpg")
    return f".{ext}"


def _build_page_url(media_id: str, page_num: int, img_type: str | None) -> str:
    """Construit l'URL CDN d'une page.

    Args:
        media_id: Identifiant média de la galerie.
        page_num: Numéro de page (1-indexé).
        img_type: Type d'image (``"j"``, ``"p"``, ``"w"``, ``"g"``).

    Returns:
        URL absolue de l'image.
    """
    ext = _image_ext_from_type(img_type).lstrip(".")
    return f"{CDN_IMAGE_BASE}/galleries/{media_id}/{page_num}.{ext}"


def _build_cover_url(media_id: str, img_type: str | None = "j") -> str:
    """Construit l'URL CDN de la couverture.

    Args:
        media_id: Identifiant média.
        img_type: Type d'image.

    Returns:
        URL absolue de la couverture.
    """
    ext = _image_ext_from_type(img_type).lstrip(".")
    return f"{CDN_THUMB_BASE}/galleries/{media_id}/cover.{ext}"


def _build_thumbnail_url(media_id: str, page_num: int, img_type: str | None) -> str:
    """Construit l'URL CDN d'une vignette de page.

    Args:
        media_id: Identifiant média.
        page_num: Numéro de page.
        img_type: Type d'image.

    Returns:
        URL absolue de la vignette.
    """
    ext = _image_ext_from_type(img_type).lstrip(".")
    return f"{CDN_THUMB_BASE}/galleries/{media_id}/{page_num}t.{ext}"


def _parse_title_from_json(title_data: Any) -> str:
    """Extrait le titre préféré depuis la structure title de l'API.

    L'API retourne un objet ``{"english": ..., "japanese": ..., "pretty": ...}``.
    Priorité : english > pretty > japanese.

    Args:
        title_data: Structure title (dict ou str).

    Returns:
        Titre nettoyé, ou chaîne vide.
    """
    if isinstance(title_data, str):
        return _clean_text(title_data)
    if not isinstance(title_data, dict):
        return ""
    for key in ("english", "pretty", "japanese"):
        value = title_data.get(key)
        if value:
            return _clean_text(str(value))
    return ""


def _extract_tags_by_type(
    tags: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Regroupe les tags par type.

    nhentai expose 7 types de tags : ``tag``, ``artist``, ``language``,
    ``category``, ``parody``, ``group``, ``character``.

    Args:
        tags: Liste de dicts tag (format API).

    Returns:
        Dict ``{type: [noms]}``.
    """
    grouped: dict[str, list[str]] = {}
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        tag_type = tag.get("type", "tag")
        name = _clean_text(str(tag.get("name", "")))
        if name:
            grouped.setdefault(tag_type, []).append(name)
    # Déduplique en préservant l'ordre
    return {k: list(dict.fromkeys(v)) for k, v in grouped.items()}


# ============================================================================
#  Parser
# ============================================================================


class NHentaiParser(BaseParser):
    """Parser complet pour nhentai (https://nhentai.net).

    Contrairement aux parsers Madara, nhentai expose une API JSON non
    officielle qui fournit directement les métadonnées et la liste des
    images. Le parser tente l'API en priorité et bascule sur le parsing
    HTML si nécessaire.

    Structure des données
    ---------------------

    Une "galerie" nhentai correspond à un doujinshi complet. Le parser
    crée un **chapitre unique virtuel** qui contient toutes les pages :

        - ``Manga.title`` = titre (priorité english)
        - ``Manga.chapters`` = [Chapter unique avec toutes les pages]
        - ``Chapter.number`` = ``"1"`` (virtuel)
        - ``Chapter.pages`` = liste des pages du CDN

    Attributes:
        site_id: Identifiant du parser (``"nhentai"``).
        language: Langue (``"en"`` — site international).
        adult: Contenu 18+ (``True``).
        base_url: URL canonique.
        mirror_domains: Miroirs.
        cdn_image_base: CDN des images.
        cdn_thumb_base: CDN des vignettes.
        cloudflare_strategy: Stratégie de bypass.
    """

    # --- Identité ---------------------------------------------------------
    site_id: ClassVar[str] = "nhentai"
    language: ClassVar[str] = "en"
    adult: ClassVar[bool] = True

    # --- URLs -------------------------------------------------------------
    base_url: ClassVar[str] = BASE_URL
    mirror_domains: ClassVar[tuple[str, ...]] = MIRROR_DOMAINS
    cdn_image_base: ClassVar[str] = CDN_IMAGE_BASE
    cdn_thumb_base: ClassVar[str] = CDN_THUMB_BASE

    # --- Options ----------------------------------------------------------
    search_path: ClassVar[str] = SEARCH_PATH
    gallery_path_template: ClassVar[str] = GALLERY_PATH_TEMPLATE
    gallery_api_template: ClassVar[str] = GALLERY_API_TEMPLATE
    search_api_path: ClassVar[str] = SEARCH_API_PATH
    random_api_path: ClassVar[str] = RANDOM_API_PATH

    # --- Sélecteurs (fallback HTML) --------------------------------------
    selectors: ClassVar[dict[str, str]] = SELECTORS

    # --- Cloudflare -------------------------------------------------------
    # nhentai utilise Cloudflare avec un challenge JS. Playwright est la
    # stratégie prioritaire ; un cookie cf_clearance est nécessaire pour
    # les endpoints API.
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
        """Initialise le parser nhentai.

        Args:
            config: Configuration du site.
            session: Session HTTP configurée (cookies, proxy, rate limit).
            playwright_pool: Pool Playwright. **Fortement recommandé** —
                sans lui, les challenges Cloudflare ne peuvent pas être
                résolus et les endpoints API restent bloqués (403).
        """
        super().__init__(config, session, playwright_pool=playwright_pool)

        self._mirror_index: int = 0
        self._active_base: str = self.base_url
        self._closed: bool = False
        # Cache des galeries (id → données API)
        self._gallery_cache: dict[str, dict[str, Any]] = {}

        logger.bind(site=self.site_id).debug(
            "Parser nhentai initialisé (playwright={}, cdn={})",
            playwright_pool is not None,
            self.cdn_image_base,
        )

    # ------------------------------------------------------------------------
    #  Contexte async
    # ------------------------------------------------------------------------

    async def __aenter__(self) -> NHentaiParser:
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
        self._gallery_cache.clear()
        logger.bind(site=self.site_id).debug("Parser nhentai fermé")

    # ------------------------------------------------------------------------
    #  API publique — recherche
    # ------------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        page: int = 1,
    ) -> list[SearchResult]:
        """Recherche des galeries sur nhentai.

        Tente d'abord l'API JSON (``/api/v2/galleries/search``). Si elle
        échoue ou retourne vide, bascule sur le parsing HTML.

        Args:
            query: Terme de recherche (titre, tag, artiste...).
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

        Endpoint : ``/api/v2/galleries/search?q=...&page=...``

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

        # Structure attendue : {"result": [...], "num_pages": N, ...}
        items = (
            response.get("result")
            or response.get("data")
            or response.get("galleries")
            or []
        )
        if not isinstance(items, list):
            return []

        results: list[SearchResult] = []
        seen_ids: set[str] = set()

        for item in items:
            try:
                book_id = str(item.get("id") or "")
                if not book_id or book_id in seen_ids:
                    continue
                seen_ids.add(book_id)

                title = _parse_title_from_json(item.get("title"))
                if not title:
                    continue

                url_str = f"{self._active_base}{self.gallery_path_template.format(book_id=book_id)}"

                # Couverture
                cover_url: str | None = None
                media_id = str(item.get("media_id") or "")
                images = item.get("images") or {}
                if media_id and isinstance(images, dict):
                    cover_data = images.get("cover") or {}
                    img_type = cover_data.get("t") if isinstance(cover_data, dict) else "j"
                    cover_url = _build_cover_url(media_id, img_type)

                # Tags (extrait artistes, langues, catégories)
                tags_raw = item.get("tags") or []
                tags_grouped = _extract_tags_by_type(tags_raw) if isinstance(tags_raw, list) else {}

                artist: str | None = None
                if tags_grouped.get("artist"):
                    artist = ", ".join(tags_grouped["artist"])

                # Langue (première langue trouvée)
                language = Language.EN
                if tags_grouped.get("language"):
                    lang_name = tags_grouped["language"][0].lower()
                    lang_map = {
                        "english": Language.EN,
                        "japanese": Language.JA,
                        "chinese": Language.ZH,
                        "korean": Language.KO,
                        "spanish": Language.ES,
                        "french": Language.FR,
                        "german": Language.DE,
                        "italian": Language.IT,
                        "portuguese": Language.PT,
                    }
                    language = lang_map.get(lang_name, Language.EN)

                results.append(
                    SearchResult(
                        source_id=book_id,
                        title=title,
                        url=url_str,  # type: ignore[arg-type]
                        cover_url=cover_url,  # type: ignore[arg-type]
                        site_id=self.site_id,
                        language=language.value,
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
                        cover_url = _normalize_image_url(raw_cover, self.cdn_thumb_base)

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
        """Récupère les métadonnées complètes d'une galerie nhentai.

        Appelle l'API ``/api/gallery/{id}`` qui retourne à la fois les
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
        logger.bind(site=self.site_id).debug("Récupération galerie ID={}", book_id)

        # Tentative API JSON
        data = await self._get_gallery_via_api(book_id)

        if data is None:
            # Fallback HTML
            logger.bind(site=self.site_id).debug(
                "API gallery vide, bascule HTML pour ID={}", book_id
            )
            url = self._build_gallery_url(book_id)
            html = await self._fetch_html(url)
            if self._is_404_page(html):
                msg = f"Galerie introuvable : {book_id}"
                raise MangaNotFoundError(msg)
            manga = self._parse_gallery_html(html, book_id, url)
        else:
            manga = self._parse_gallery_api(data, book_id)

        logger.bind(site=self.site_id).info(
            "Galerie récupérée : '{}' ({} page(s), {} tag(s))",
            manga.title,
            len(manga.chapters[0].pages) if manga.chapters else 0,
            len(manga.genres),
        )
        return manga

    async def _get_gallery_via_api(self, book_id: str) -> dict[str, Any] | None:
        """Récupère une galerie via l'API JSON.

        Args:
            book_id: ID numérique de la galerie.

        Returns:
            Données JSON, ou None si l'API échoue.
        """
        # Cache hit
        if book_id in self._gallery_cache:
            return self._gallery_cache[book_id]

        url = self._active_base + self.gallery_api_template.format(book_id=book_id)

        try:
            data = await self._fetch_json(url)
        except (SiteUnreachableError, ParserError) as exc:
            logger.bind(site=self.site_id).debug(
                "API gallery échouée pour ID={} : {}", book_id, exc
            )
            return None

        if not isinstance(data, dict):
            return None

        # L'API nhentai retourne directement l'objet sans enveloppe.
        self._gallery_cache[book_id] = data
        return data

    def _parse_gallery_api(self, data: dict[str, Any], book_id: str) -> Manga:
        """Parse les données API d'une galerie en objet Manga.

        Args:
            data: Données JSON de l'API.
            book_id: ID de la galerie.

        Returns:
            Objet Manga peuplé.
        """
        # Titre (priorité english > pretty > japanese)
        title_data = data.get("title", {})
        title = _parse_title_from_json(title_data) or f"Gallery {book_id}"

        # Titre alternatif (japonais si dispo)
        alt_titles: list[str] = []
        if isinstance(title_data, dict):
            jp_title = title_data.get("japanese")
            if jp_title and jp_title != title:
                alt_titles.append(_clean_text(str(jp_title)))

        url = f"{self._active_base}{self.gallery_path_template.format(book_id=book_id)}"

        # Media ID et images
        media_id = str(data.get("media_id") or "")
        images_data = data.get("images", {})
        pages_data = images_data.get("pages", []) if isinstance(images_data, dict) else []

        # Couverture
        cover_url: str | None = None
        if media_id:
            cover_data = images_data.get("cover", {}) if isinstance(images_data, dict) else {}
            cover_type = cover_data.get("t") if isinstance(cover_data, dict) else "j"
            cover_url = _build_cover_url(media_id, cover_type)

        # Pages
        pages: list[Page] = []
        if media_id and isinstance(pages_data, list):
            for idx, page_data in enumerate(pages_data):
                if not isinstance(page_data, dict):
                    continue
                img_type = page_data.get("t")
                page_num = idx + 1  # nhentai est 1-indexé pour les pages
                page_url = _build_page_url(media_id, page_num, img_type)
                ext = _image_ext_from_type(img_type)
                pages.append(
                    Page(
                        index=idx,
                        url=page_url,  # type: ignore[arg-type]
                        filename=f"{page_num:04d}{ext}",
                        checksum=None,
                    )
                )

        # Tags par type
        tags_raw = data.get("tags", [])
        tags_grouped = _extract_tags_by_type(tags_raw) if isinstance(tags_raw, list) else {}

        # Auteur (artists)
        artist: str | None = None
        if tags_grouped.get("artist"):
            artist = ", ".join(tags_grouped["artist"])

        # Genres (tags + category + parody + character)
        genres: list[str] = []
        for tag_type in ("tag", "category", "parody", "character", "group"):
            genres.extend(tags_grouped.get(tag_type, []))
        genres = list(dict.fromkeys(genres))

        # Langue
        language = Language.EN
        if tags_grouped.get("language"):
            lang_name = tags_grouped["language"][0].lower()
            lang_map = {
                "english": Language.EN,
                "japanese": Language.JA,
                "chinese": Language.ZH,
                "korean": Language.KO,
                "spanish": Language.ES,
                "french": Language.FR,
                "german": Language.DE,
                "italian": Language.IT,
                "portuguese": Language.PT,
            }
            language = lang_map.get(lang_name, Language.EN)

        # Date de publication (upload_date = epoch timestamp)
        published_at: datetime | None = None
        raw_upload = data.get("upload_date")
        if isinstance(raw_upload, (int, float)):
            with suppress(ValueError, OSError, OverflowError):
                published_at = datetime.fromtimestamp(raw_upload, tz=UTC)

        # Scanlator (optionnel)
        scanlator = _clean_text(data.get("scanlator") or "") or None

        # Num pages
        num_pages = data.get("num_pages")
        if isinstance(num_pages, int) and not pages:
            # Cas rare : API retourne num_pages mais pas la liste d'images
            # On construit des URLs vides (fallback HTML nécessaire)
            logger.bind(site=self.site_id).warning(
                "API gallery {} : num_pages={} mais pages vides", book_id, num_pages
            )

        # Chapitre unique virtuel
        chapter = self._build_virtual_chapter(book_id, title, url, pages, published_at)

        return Manga(
            id=f"{self.site_id}:{book_id}",
            source_id=book_id,
            site=self.site_id,
            title=title,
            alternative_titles=alt_titles,
            description=scanlator,
            author=artist,
            artist=artist,
            genres=genres,
            status=MangaStatus.COMPLETED,
            year=published_at.year if published_at else None,
            cover_url=cover_url,  # type: ignore[arg-type]
            language=language,
            content_rating=self.content_rating,
            chapters=[chapter],
            url=url,  # type: ignore[arg-type]
            updated_at=published_at or datetime.now(UTC),
        )

    def _build_virtual_chapter(
        self,
        book_id: str,
        title: str,
        url: str,
        pages: list[Page],
        published_at: datetime | None = None,
    ) -> Chapter:
        """Construit le chapitre unique virtuel d'une galerie nhentai.

        Args:
            book_id: ID de la galerie.
            title: Titre.
            url: URL de la galerie.
            pages: Pages du chapitre.
            published_at: Date de publication.

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
            published_at=published_at,
            url=url,  # type: ignore[arg-type]
            pages=pages,
        )

    # ------------------------------------------------------------------------
    #  API publique — chapitres
    # ------------------------------------------------------------------------

    async def get_chapters(self, manga: Manga) -> list[Chapter]:
        """Récupère les chapitres d'un manga nhentai.

        nhentai n'a **pas** de chapitres multiples : une galerie = un
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
        """Récupère les URLs des pages d'un chapitre nhentai.

        Si le chapitre contient déjà ses pages (cas normal — extraites
        par ``get_manga``), retourne directement la liste. Sinon, tente
        de re-parser la galerie via son URL.

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
            msg = f"ID de galerie introuvable dans l'URL : {url}"
            raise ChapterDownloadError(msg)

        data = await self._get_gallery_via_api(book_id)
        if data is None:
            html = await self._fetch_html(url)
            return self._parse_pages_html(html, chapter)

        media_id = str(data.get("media_id") or "")
        images_data = data.get("images", {})
        pages_data = images_data.get("pages", []) if isinstance(images_data, dict) else []

        pages: list[Page] = []
        if media_id and isinstance(pages_data, list):
            for idx, page_data in enumerate(pages_data):
                if not isinstance(page_data, dict):
                    continue
                img_type = page_data.get("t")
                page_num = idx + 1
                page_url = _build_page_url(media_id, page_num, img_type)
                ext = _image_ext_from_type(img_type)
                pages.append(
                    Page(
                        index=idx,
                        url=page_url,  # type: ignore[arg-type]
                        filename=f"{page_num:04d}{ext}",
                        checksum=None,
                    )
                )

        if not pages:
            msg = f"Aucune page trouvée pour la galerie {book_id}"
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

        Les images nhentai sont servies depuis ``i.nhentai.net`` — pas
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
            # Le CDN i.nhentai.net ne nécessite pas de Referer strict,
            # mais on l'envoie par cohérence avec les autres parsers.
            response = await self._session.get(
                str(page.url),
                headers={"Referer": self._active_base},
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
        """Vérifie que nhentai est accessible.

        Teste l'API ``/api/gallery/1`` (ID connu) qui est plus fiable que
        la page d'accueil. Un challenge Cloudflare non résolu retourne
        False, même si le site est techniquement up.

        Returns:
            True si l'API répond correctement.
        """
        try:
            if self._playwright_pool is not None:
                html = await self._playwright_pool.fetch_html(
                    self._active_base,
                    timeout=self.playwright_timeout,
                )
                if _is_cloudflare_challenge(html):
                    logger.bind(site=self.site_id).warning(
                        "nhentai : challenge CF non résolu"
                    )
                    return False
                if len(html) > 1000:  # noqa: PLR2004
                    return True
            else:
                # Test API lightweight sur un ID connu
                response = await self._session.get(
                    self._active_base + self.gallery_api_template.format(book_id="1"),
                    timeout=self.http_timeout,
                    headers={
                        "Accept": "application/json",
                        "Referer": self._active_base,
                    },
                )
                if response.status_code == 200:  # noqa: PLR2004
                    if not _is_cloudflare_challenge(response.text):
                        return True
                elif response.status_code == 403:  # noqa: PLR2004
                    logger.bind(site=self.site_id).debug(
                        "nhentai : 403 — challenge Cloudflare actif"
                    )
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
            "nhentai : aucun domaine accessible. Site possiblement bloqué, "
            "sous saisie, ou migré vers un domaine non référencé."
        )
        return False

    # ------------------------------------------------------------------------
    #  API publique — URL
    # ------------------------------------------------------------------------

    def normalize_url(self, url: str) -> str:
        """Normalise une URL (relative → absolue, réécriture historique).

        Réécrit les domaines historiques (``.com``, ``.ru``) vers le
        domaine canonique (``.net``).

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
        """Résout une entrée en ID numérique de galerie.

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

        msg = f"Impossible d'extraire un ID de galerie depuis : {url_or_id}"
        raise ParserError(msg)

    def _build_gallery_url(self, book_id: str) -> str:
        """Construit l'URL d'une galerie.

        Args:
            book_id: ID de la galerie.

        Returns:
            URL complète.
        """
        return self._active_base + self.gallery_path_template.format(book_id=book_id)

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
        """Récupère une réponse JSON depuis l'API nhentai.

        Utilise le fallback Playwright si Cloudflare bloque les requêtes
        API (le JSON est alors extrait du DOM ou d'un script inline).

        Args:
            url: URL de l'endpoint API.
            params: Query parameters.

        Returns:
            Réponse JSON parsée.

        Raises:
            ParserError: Si le JSON est invalide ou l'ID est 404.
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
            elif response.status_code in (403, 429):  # noqa: PLR2004
                logger.bind(site=self.site_id).warning(
                    "HTTP {} sur {} — Cloudflare ou rate limit",
                    response.status_code,
                    url,
                )

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
                elif response.status_code in (403, 503):  # noqa: PLR2004
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

    def _parse_gallery_html(self, html: str, book_id: str, url: str) -> Manga:
        """Parse la page HTML d'une galerie (fallback si API indisponible).

        Args:
            html: HTML de la page.
            book_id: ID de la galerie.
            url: URL de la galerie.

        Returns:
            Objet Manga peuplé.
        """
        from selectolax.parser import HTMLParser  # noqa: PLC0415

        tree = HTMLParser(html)

        title_node = tree.css_first(self.selectors["gallery_title"])
        title = _clean_text(title_node.text()) if title_node else f"Gallery {book_id}"

        cover_url: str | None = None
        cover_node = tree.css_first(self.selectors["gallery_cover"])
        if cover_node is not None:
            raw_cover = (
                cover_node.attributes.get("data-src")
                or cover_node.attributes.get("src")
            )
            if raw_cover:
                cover_url = _normalize_image_url(raw_cover, self.cdn_thumb_base)

        # Tags (extrait artistes, langues, catégories)
        genres: list[str] = []
        for tag_node in tree.css(self.selectors["gallery_tags"]):
            tag = _clean_text(tag_node.text())
            if tag and tag not in genres:
                genres.append(tag)

        # Artiste
        author: str | None = None
        artist_node = tree.css_first(self.selectors["gallery_artist"])
        if artist_node is not None:
            author = _clean_text(artist_node.text()) or None

        # Pages (fallback : parse le HTML, souvent des miniatures)
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
        """Parse les images d'une page HTML (fallback).

        Args:
            tree_or_html: HTMLParser ou HTML brut.
            book_id: ID de la galerie (non utilisé, pour signature).

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

            url = _normalize_image_url(raw_url, self.cdn_image_base)
            ext = Path(urlparse(url).path).suffix.lower() or ".jpg"
            if ext not in ALLOWED_IMAGE_EXTS:
                ext = ".jpg"

            pages.append(
                Page(
                    index=idx,
                    url=url,  # type: ignore[arg-type]
                    filename=f"{idx + 1:04d}{ext}",
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
            "cdn_image_base": self.cdn_image_base,
            "cdn_thumb_base": self.cdn_thumb_base,
            "cloudflare_strategy": self.cloudflare_strategy,
            "supports_api": True,
            "api_version": "v2",
            "selectors_count": len(self.selectors),
        }


# ============================================================================
#  Exports
# ============================================================================

__all__ = [
    "BASE_URL",
    "CDN_IMAGE_BASE",
    "CDN_THUMB_BASE",
    "MIRROR_DOMAINS",
    "SELECTORS",
    "NHentaiParser",
]
