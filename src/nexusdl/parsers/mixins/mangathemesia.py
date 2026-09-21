"""Mixin complet pour les sites WordPress MangaThemesia.

MangaThemesia est un thème WordPress moderne utilisé par de nombreux
sites de scantrad anglophones et internationaux (AsuraScans, FlameScans,
LuminousScans, VoidScans, etc.). Structure HTML standardisée :

    - Recherche    : ``GET /?s=<query>&page=N``
    - Série        : ``GET /series/<slug>/``
    - Chapitre     : ``GET /series/<slug>/<chapter-slug>/``
    - Lecture      : ``GET /series/<slug>/<chapter-slug>/`` (images inline)
    - AJAX pages   : ``POST /wp-admin/admin-ajax.php`` (action=...)
    - Lazy-loading : ``data-src``, ``data-lazy-src``, ``data-original``
    - Pagination   : ``?page=N`` (query param)
    - Cloudflare   : protection fréquente (challenge JS + Turnstile)

Spécificités par rapport à Madara
=================================

    1. **Chemin /series/** au lieu de /manga/ pour les séries.
    2. **AJAX natif** : la liste des pages est souvent chargée via un
       endpoint AJAX (`admin-ajax.php`). Le mixin fournit une méthode
       `_fetch_pages_via_ajax()` utilisable par les sous-classes.
    3. **Titre alternatif** : les sites MangaThemesia exposent souvent
       un titre romanisé (JP) dans un `<span>` ou un `<div>` séparé.
    4. **Sélecteurs modernes** : classes CSS plus modernes (`.bs`,
       `.bsx`, `.eplister`, `#readerarea`).

Contrat attendu du parser hôte
==============================

    - ``self.site_id`` (str) — identifiant unique.
    - ``self.language`` (str) — code langue ISO 639-1.
    - ``self.adult`` (bool) — contenu 18+.
    - ``self.base_url`` (str) — URL canonique.
    - ``self._session`` (HttpSession) — via BaseParser.__init__.
    - ``self._playwright_pool`` (PlaywrightPool | None) — optionnel.

Example:
    ::

        from nexusdl.parsers.mixins.mangathemesia import MangaThemesiaMixin
        from nexusdl.parsers.base import BaseParser

        class AsuraScansParser(MangaThemesiaMixin, BaseParser):
            site_id = "asurascans"
            language = "en"
            adult = False
            base_url = "https://asurascans.com"

            # Optionnel : surcharger les sélecteurs si nécessaire
            selectors = {
                **MangaThemesiaMixin.default_selectors,
                "search_item": "div.listupd div.bs",
            }

        # Utilisation
        parser = AsuraScansParser(config, session)
        results = await parser.search("one piece")
        manga = await parser.get_manga(results[0].url)
        chapters = await parser.get_chapters(manga)
        pages = await parser.get_pages(chapters[0])
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import suppress
from datetime import UTC, datetime
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
from nexusdl.parsers.base import SearchResult

if TYPE_CHECKING:
    from collections.abc import Iterable

# ============================================================================
#  CONSTANTES PARTAGÉES
# ============================================================================

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

#: Domaines de tracking génériques — exclus des pages téléchargées.
TRACKING_DOMAINS: Final[tuple[str, ...]] = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "facebook.com/tr",
    "facebook.net",
    "analytics.",
    "pixel.",
    "stats.",
    "tracker.",
    "exoclick.com",
    "juicyads.com",
    "trafficjunky.net",
)

#: Extensions acceptées pour les images.
ALLOWED_IMAGE_EXTS: Final[frozenset[str]] = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"},
)

#: Regex pour détecter les blobs JS non téléchargeables.
BLOB_URL_PATTERN: Final[re.Pattern[str]] = re.compile(r"^blob:")

#: Regex générique pour le numéro de chapitre.
DEFAULT_CHAPTER_NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:chapter|chap|ch|ch\.)\s*([0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE,
)

#: Regex générique pour extraire le slug série d'une URL.
DEFAULT_SERIES_SLUG_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"/series/([^/]+)/?",
)

#: Regex pour détecter une page 404.
PAGE_404_MARKERS: Final[tuple[str, ...]] = (
    "404",
    "not found",
    "page introuvable",
    "introuvable",
    "doesn't exist",
    "page not found",
)

#: Regex pour extraire un `post_id` ou `series_id` depuis le HTML.
#: Ces IDs sont utilisés pour les appels AJAX.
POST_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"""(?:post[_-]?id|series[_-]?id|data[_-]?id)["']?\s*[:=]\s*["']?(\d+)""",
    re.IGNORECASE,
)

#: Regex pour extraire l'action AJAX depuis un script inline.
AJAX_ACTION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"""action["']?\s*[:=]\s*["']([a-z_]+)["']""",
    re.IGNORECASE,
)


# ============================================================================
#  HELPERS INTERNES
# ============================================================================


def _clean_text(text: str | None) -> str:
    """Nettoie un texte HTML (entités, whitespace).

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
    """Détecte une URL ``blob:`` (non téléchargeable).

    Args:
        url: URL à tester.

    Returns:
        True si l'URL est un blob.
    """
    return bool(BLOB_URL_PATTERN.match(url))


def _is_tracking_url(url: str) -> bool:
    """Détecte une URL de tracking.

    Args:
        url: URL à tester.

    Returns:
        True si l'URL pointe vers un domaine de tracking connu.
    """
    url_lower = url.lower()
    return any(td in url_lower for td in TRACKING_DOMAINS)


def _is_cloudflare_challenge(html: str) -> bool:
    """Détecte une page de challenge Cloudflare.

    Args:
        html: Contenu HTML.

    Returns:
        True si le HTML contient des marqueurs CF.
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


def _safe_float(value: str) -> float | None:
    """Convertit une chaîne en float, ou retourne None.

    Args:
        value: Chaîne à convertir.

    Returns:
        Float, ou None si non convertible.
    """
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _chapter_sort_key(chapter: Chapter) -> float:
    """Clé de tri numérique pour un chapitre.

    Args:
        chapter: Chapitre à trier.

    Returns:
        Numéro en float, ou 0.0 si non numérique.
    """
    try:
        return float(chapter.number)
    except (ValueError, TypeError):
        return 0.0


# ============================================================================
#  MIXIN
# ============================================================================


class MangaThemesiaMixin:
    """Mixin fournissant les implémentations par défaut pour sites MangaThemesia.

    Cette classe **ne peut pas être instanciée seule** — elle est conçue
    pour être composée avec ``BaseParser`` via héritage multiple :

        class FooParser(MangaThemesiaMixin, BaseParser):
            ...

    L'ordre est critique : ``MangaThemesiaMixin`` **en premier** pour que
    ses méthodes priment sur celles de ``BaseParser`` (MRO).

    Attributes:
        default_selectors: Sélecteurs CSS par défaut du thème MangaThemesia.
        search_path: Chemin de recherche (query param ``s``).
        series_path_template: Template d'URL pour une série (``{slug}``).
        chapter_path_template: Template d'URL pour un chapitre.
        ajax_endpoint: Endpoint AJAX pour la liste des pages.
        uses_ajax_pages: Si True, tente d'utiliser AJAX pour les pages.
        cloudflare_strategy: Stratégie de bypass CF.
    """

    # ------------------------------------------------------------------------
    #  SÉLECTEURS CSS PAR DÉFAUT — THÈME MANGATHEMESIA
    # ------------------------------------------------------------------------

    default_selectors: ClassVar[dict[str, str]] = {
        # --- Recherche ---
        "search_item": "div.listupd div.bs, div.listupd div.bsx, div.bs",
        "search_title": "div.bsx > a, div.bigors > a, a[title]",
        "search_cover": "div.bsx > a > img, img.ts-post-image, img",
        "search_link": "div.bsx > a, a.tip, a[href*='/series/']",
        # --- Page série ---
        "manga_title": "div.bigcontent h1.entry-title, h1.entry-title, div.seriestuhead h1",
        "manga_alt_title": "div.seriestualt, div.bigcontent span.alternative, div.alt-title",
        "manga_description": "div.entry-content-single, div.entry-content, div.synp",
        "manga_cover": "div.thumb img, div.seriestucon img, div.thumbcontent img",
        "manga_author": "div.seriestucon span:contains('Author'), div.author-content a",
        "manga_artist": "div.artist-content a, span.artist a",
        "manga_genres": "div.seriestugenre a, div.genxed a, div.mgen a",
        "manga_status": "div.seriestucont div.seriestucon, div.post-status, div.status",
        "manga_year": "div.seriestucon span:contains('Release'), div.year",
        # --- Chapitres ---
        "chapter_list": "div.eplister ul, ul#chapterlist, div#chapterlist",
        "chapter_item": "div.eplister ul li a, ul#chapterlist li a, div.eplister a",
        "chapter_date": "span.chapterdate, span.epl-date",
        # --- Pages ---
        "page_image": "div#readerarea img, div.readerarea img, div#reader img",
        "page_container": "div#readerarea, div.readerarea, div#reader",
    }

    #: Sélecteurs effectifs (fusionnés par la sous-classe si surchargés).
    selectors: ClassVar[dict[str, str]] = default_selectors

    # ------------------------------------------------------------------------
    #  TEMPLATES D'URL
    # ------------------------------------------------------------------------

    #: Chemin de recherche (avec query param ``s``).
    search_path: ClassVar[str] = "/"

    #: Paramètre de recherche (défaut WordPress : ``s``).
    search_query_param: ClassVar[str] = "s"

    #: Paramètre de pagination (défaut MangaThemesia : ``page``).
    search_page_param: ClassVar[str] = "page"

    #: Template d'URL pour une série.
    series_path_template: ClassVar[str] = "/series/{slug}/"

    #: Template d'URL pour un chapitre.
    chapter_path_template: ClassVar[str] = "/series/{slug}/{chapter_slug}/"

    #: Alias rétrocompatible (certains parsers attendent ``manga_path_template``).
    manga_path_template: ClassVar[str] = "/series/{slug}/"

    #: Regex pour extraire le slug série depuis une URL.
    series_slug_pattern: ClassVar[re.Pattern[str]] = DEFAULT_SERIES_SLUG_PATTERN

    #: Regex pour extraire un numéro de chapitre.
    chapter_number_pattern: ClassVar[re.Pattern[str]] = DEFAULT_CHAPTER_NUMBER_PATTERN

    # ------------------------------------------------------------------------
    #  AJAX (spécificité MangaThemesia)
    # ------------------------------------------------------------------------

    #: Endpoint AJAX WordPress par défaut.
    ajax_endpoint: ClassVar[str] = "/wp-admin/admin-ajax.php"

    #: Si True, le mixin tente de récupérer les pages via AJAX avant de
    #: parser le HTML statique. Certains sites MangaThemesia chargent
    #: les pages d'un chapitre via un POST AJAX (action=...).
    uses_ajax_pages: ClassVar[bool] = False

    #: Nom de l'action AJAX (à surcharger si nécessaire, ex: ``"chapter_content"``).
    ajax_action: ClassVar[str | None] = None

    # ------------------------------------------------------------------------
    #  CLOUDFLARE
    # ------------------------------------------------------------------------

    cloudflare_strategy: ClassVar[str] = "playwright"
    cloudflare_wait_selector: ClassVar[str | None] = (
        "div.listupd, div.bigcontent, div.eplister, div#readerarea"
    )

    # ------------------------------------------------------------------------
    #  DÉTECTION PREMIUM / FUTUR
    # ------------------------------------------------------------------------

    premium_patterns: ClassVar[list[str]] = [
        r"\b(premium|vip|payant|locked|🔒|💎|⭐)\b",
    ]

    future_patterns: ClassVar[list[str]] = [
        r"\b(soon|bient[oô]t|[aà] venir|upcoming|TBA)\b",
    ]

    premium_prefix: ClassVar[str] = "[Premium]"
    future_prefix: ClassVar[str] = "[À venir]"

    # ------------------------------------------------------------------------
    #  LAZY INIT
    # ------------------------------------------------------------------------

    def _init_mangathemesia(self) -> None:
        """Initialise les attributs internes du mixin (idempotent)."""
        if getattr(self, "_mangathemesia_initialized", False):
            return

        self._premium_regex: tuple[re.Pattern[str], ...] = tuple(
            re.compile(p, re.IGNORECASE) for p in self.premium_patterns
        )
        self._future_regex: tuple[re.Pattern[str], ...] = tuple(
            re.compile(p, re.IGNORECASE) for p in self.future_patterns
        )

        if "selectors" not in type(self).__dict__:
            self.selectors = dict(self.default_selectors)

        self._mangathemesia_initialized = True
        logger.bind(site=getattr(self, "site_id", "?")).debug(
            "MangaThemesiaMixin initialisé ({} sélecteurs, {} premium, {} futurs)",
            len(self.selectors),
            len(self._premium_regex),
            len(self._future_regex),
        )

    def _ensure_initialized(self) -> None:
        """Lazy init du mixin."""
        if not getattr(self, "_mangathemesia_initialized", False):
            self._init_mangathemesia()

    # ------------------------------------------------------------------------
    #  MÉTHODES PUBLIQUES
    # ------------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        page: int = 1,
    ) -> list[SearchResult]:
        """Recherche des séries sur un site MangaThemesia.

        Args:
            query: Terme de recherche (non vide).
            page: Numéro de page (1-indexé).

        Returns:
            Liste de résultats déduplicés.

        Raises:
            ParserError: Si la query est vide ou la page invalide.
            SiteUnreachableError: Si le site ne répond pas.
        """
        self._ensure_initialized()

        query = query.strip()
        if not query:
            msg = "query vide après normalisation"
            raise ParserError(msg)
        if page < 1:
            msg = f"page doit être >= 1, reçu {page}"
            raise ParserError(msg)

        logger.bind(site=self.site_id).debug(
            "Recherche '{}' page {}",
            query,
            page,
        )

        params: dict[str, str] = {self.search_query_param: query}
        if page > 1:
            params[self.search_page_param] = str(page)

        html = await self._fetch_html(self.base_url + self.search_path, params=params)
        results = self._parse_search_html(html)

        logger.bind(site=self.site_id).info(
            "{} résultat(s) pour '{}' (page {})",
            len(results),
            query,
            page,
        )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère les métadonnées complètes d'une série MangaThemesia.

        Args:
            url_or_id: URL absolue, chemin relatif, ou slug.

        Returns:
            Objet Manga peuplé (avec chapitres si la page les liste).

        Raises:
            MangaNotFoundError: Si la page renvoie 404.
            SiteUnreachableError: Si le site ne répond pas.
        """
        self._ensure_initialized()

        url = self._resolve_manga_url(url_or_id)
        logger.bind(site=self.site_id).debug("Récupération série : {}", url)

        html = await self._fetch_html(url)

        if self._is_404_page(html):
            msg = f"Série introuvable : {url}"
            raise MangaNotFoundError(msg)

        manga = self._parse_manga_html(html, url)

        logger.bind(site=self.site_id).info(
            "Série : '{}' ({} chapitre(s), {} titre(s) alternatif(s))",
            manga.title,
            len(manga.chapters),
            len(manga.alternative_titles),
        )
        return manga

    async def get_chapters(self, manga: Manga) -> list[Chapter]:
        """Récupère les chapitres d'une série MangaThemesia.

        Args:
            manga: Manga cible.

        Returns:
            Liste de chapitres triés (plus récent en premier).
        """
        self._ensure_initialized()

        if manga.chapters:
            chapters = list(manga.chapters)
        else:
            html = await self._fetch_html(str(manga.url))
            chapters = self._parse_chapters_html(html)

        valid = [ch for ch in chapters if ch.url and str(ch.url).strip()]
        filtered = len(chapters) - len(valid)
        if filtered:
            logger.bind(site=self.site_id).debug(
                "{} chapitre(s) sans URL filtré(s)",
                filtered,
            )

        valid.sort(key=_chapter_sort_key, reverse=True)

        logger.bind(site=self.site_id).info(
            "{} chapitre(s) pour '{}'",
            len(valid),
            manga.title,
        )
        return valid

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre MangaThemesia.

        Si ``uses_ajax_pages`` est True et que ``ajax_action`` est défini,
        tente d'abord une requête AJAX. Sinon, parse le HTML standard.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste de pages ordonnées.

        Raises:
            ChapterDownloadError: Si aucune page n'est trouvée.
        """
        self._ensure_initialized()

        url = str(chapter.url)
        logger.bind(site=self.site_id).debug(
            "Pages pour ch.{} : {}",
            chapter.number,
            url,
        )

        # Tentative AJAX si configurée
        if self.uses_ajax_pages and self.ajax_action:
            try:
                pages = await self._fetch_pages_via_ajax(chapter)
                if pages:
                    logger.bind(site=self.site_id).debug(
                        "{} page(s) récupérée(s) via AJAX",
                        len(pages),
                    )
                    return pages
            except Exception as exc:  # noqa: BLE001
                logger.bind(site=self.site_id).debug(
                    "Fetch AJAX échoué, fallback HTML : {}",
                    exc,
                )

        # Fallback HTML standard
        html = await self._fetch_html(url)
        pages = self._parse_pages_html(html)

        if not pages:
            msg = f"Aucune page trouvée pour ch.{chapter.number} ({url})"
            raise ChapterDownloadError(msg)

        logger.bind(site=self.site_id).info(
            "{} page(s) pour ch.{}",
            len(pages),
            chapter.number,
        )
        return pages

    # ------------------------------------------------------------------------
    #  FETCH AJAX (spécifique MangaThemesia)
    # ------------------------------------------------------------------------

    async def _fetch_pages_via_ajax(self, chapter: Chapter) -> list[Page]:
        """Récupère les pages via l'endpoint AJAX WordPress.

        Effectue un POST vers ``ajax_endpoint`` avec ``action=ajax_action``
        et ``post_id=<id>`` (extrait de l'URL du chapitre ou de la page HTML).

        La réponse est attendue au format JSON avec une clé ``data`` ou
        ``images`` contenant la liste des URLs (ou du HTML à parser).

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste de pages, ou liste vide si la réponse est invalide.

        Raises:
            SiteUnreachableError: Si l'endpoint AJAX est injoignable.
        """
        session = getattr(self, "_session", None)
        if session is None:
            return []

        # Extrait le post_id depuis l'URL du chapitre (format /series/slug/1234/)
        chapter_url = str(chapter.url)
        post_id = self._extract_post_id(chapter_url)
        if not post_id:
            # Tente d'extraire le post_id depuis la page HTML
            try:
                html = await self._fetch_html(chapter_url)
                match = POST_ID_PATTERN.search(html)
                if match:
                    post_id = match.group(1)
            except Exception:  # noqa: BLE001
                return []

        if not post_id:
            logger.bind(site=self.site_id).debug(
                "post_id introuvable pour {} — skip AJAX",
                chapter_url,
            )
            return []

        ajax_url = self.base_url + self.ajax_endpoint
        data = {
            "action": self.ajax_action,
            "post_id": post_id,
            "chapter": chapter.source_id,
        }

        response = await session.post(
            ajax_url,
            data=data,
            timeout=getattr(self, "http_timeout", 30.0),
            headers={
                "Referer": chapter_url,
                "X-Requested-With": "XMLHttpRequest",
            },
        )

        if response.status_code != 200:  # noqa: PLR2004
            return []

        # Tentative JSON
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError:
            # Réponse HTML — parse directement
            return self._parse_pages_html(response.text)

        return self._parse_ajax_pages_response(payload)

    def _extract_post_id(self, url: str) -> str | None:
        """Extrait le ``post_id`` depuis une URL de chapitre.

        MangaThemesia utilise souvent des URLs de la forme
        ``/series/<slug>/<chapter-slug>-<chapter-id>/`` ou
        ``/series/<slug>/<chapter-id>/``.

        Args:
            url: URL du chapitre.

        Returns:
            post_id, ou None si introuvable.
        """
        # Dernier segment de l'URL
        segments = [s for s in url.rstrip("/").split("/") if s]
        if not segments:
            return None

        # Cherche un nombre pur dans les segments
        for seg in reversed(segments):
            if seg.isdigit():
                return seg
            # Format "chapter-123" ou "chapter-12-5"
            parts = re.split(r"[-_]", seg)
            for part in reversed(parts):
                if part.isdigit() and len(part) >= 2:  # noqa: PLR2004
                    return part

        return None

    def _parse_ajax_pages_response(self, payload: Any) -> list[Page]:
        """Parse une réponse AJAX JSON en liste de Pages.

        Le format varie selon les sites. Les clés cherchées :
            - ``data`` : peut être une liste d'URLs, un HTML à parser,
              ou un dict avec ``images``.
            - ``images`` : liste d'URLs ou de dicts avec ``src``/``url``.
            - ``pages`` : liste d'URLs.

        Args:
            payload: Charge utile JSON parsée.

        Returns:
            Liste de pages.
        """
        urls: list[str] = []

        # Cherche les URLs dans différents emplacements
        candidates: list[Any] = []
        if isinstance(payload, dict):
            for key in ("images", "pages", "data"):
                if key in payload:
                    candidates.append(payload[key])
        elif isinstance(payload, list):
            candidates.append(payload)

        for candidate in candidates:
            if isinstance(candidate, str):
                # HTML à parser
                return self._parse_pages_html(candidate)
            if isinstance(candidate, list):
                for item in candidate:
                    if isinstance(item, str):
                        urls.append(item)
                    elif isinstance(item, dict):
                        src = item.get("src") or item.get("url") or item.get("image")
                        if isinstance(src, str):
                            urls.append(src)
                if urls:
                    break
            if isinstance(candidate, dict):
                # Récursif
                sub = self._parse_ajax_pages_response(candidate)
                if sub:
                    return sub

        return self._build_pages_from_urls(urls)

    def _build_pages_from_urls(self, urls: list[str]) -> list[Page]:
        """Construit une liste de Pages depuis une liste d'URLs.

        Args:
            urls: URLs des images.

        Returns:
            Liste de pages ordonnées, trackers/blobs filtrés.
        """
        pages: list[Page] = []
        for idx, raw_url in enumerate(urls):
            if not raw_url:
                continue
            if _is_blob_url(raw_url) or _is_tracking_url(raw_url):
                continue

            url = _normalize_image_url(raw_url, self.base_url)
            ext = self._extract_image_ext(url)
            pages.append(
                Page(
                    index=idx,
                    url=url,  # type: ignore[arg-type]
                    filename=f"page_{idx:04d}{ext}",
                    checksum=None,
                ),
            )
        return pages

    # ------------------------------------------------------------------------
    #  PARSING HTML
    # ------------------------------------------------------------------------

    def _parse_search_html(self, html: str) -> list[SearchResult]:
        """Parse la page de résultats de recherche MangaThemesia.

        Args:
            html: HTML de la page.

        Returns:
            Liste de résultats déduplicés.
        """
        from selectolax.parser import HTMLParser  # noqa: PLC0415

        tree = HTMLParser(html)
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        items = tree.css(self.selectors["search_item"])
        if not items:
            logger.bind(site=self.site_id).debug(
                "Aucun résultat (sélecteur '{}' vide)",
                self.selectors["search_item"],
            )
            return []

        for item in items:
            try:
                title_node = item.css_first(self.selectors["search_title"])
                if title_node is None:
                    continue

                # Le lien peut être sur le titre ou sur un parent <a>
                link = title_node.attributes.get("href") or ""
                if not link:
                    link_node = item.css_first("a")
                    if link_node is not None:
                        link = link_node.attributes.get("href") or ""
                if not link:
                    continue

                title = _clean_text(
                    title_node.attributes.get("title") or title_node.text(),
                )
                if not title:
                    continue

                url = self._normalize_url(link)
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # Couverture
                cover_url: str | None = None
                cover_node = item.css_first(self.selectors["search_cover"])
                if cover_node is not None:
                    raw_cover = (
                        cover_node.attributes.get("data-src")
                        or cover_node.attributes.get("data-lazy-src")
                        or cover_node.attributes.get("src")
                        or ""
                    )
                    if raw_cover:
                        cover_url = _normalize_image_url(raw_cover, self.base_url)

                # Auteur (optionnel)
                author: str | None = None
                author_node = item.css_first(self.selectors["manga_author"])
                if author_node is not None:
                    author = _clean_text(author_node.text()) or None

                slug = self._extract_slug(url)

                results.append(
                    SearchResult(
                        source_id=slug or url,
                        title=title,
                        url=url,  # type: ignore[arg-type]
                        cover_url=cover_url,  # type: ignore[arg-type]
                        site_id=self.site_id,
                        language=self.language,
                        adult=self.adult,
                        author=author,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.bind(site=self.site_id).debug(
                    "Erreur parsing item recherche : {}",
                    exc,
                )
                continue

        return results

    def _parse_manga_html(self, html: str, url: str) -> Manga:
        """Parse la page d'une série MangaThemesia.

        Args:
            html: HTML de la page.
            url: URL absolue de la série.

        Returns:
            Objet Manga peuplé.
        """
        from selectolax.parser import HTMLParser  # noqa: PLC0415

        tree = HTMLParser(html)

        # --- Titre principal ---
        title_node = tree.css_first(self.selectors["manga_title"])
        title = _clean_text(title_node.text()) if title_node else ""

        # --- Titre alternatif (spécifique MangaThemesia) ---
        alt_titles: list[str] = []
        alt_node = tree.css_first(self.selectors["manga_alt_title"])
        if alt_node is not None:
            raw_alt = _clean_text(alt_node.text())
            # Nettoyage : "Alternative: <title>" → "<title>"
            raw_alt = re.sub(
                r"^\s*(alternative|alt(ernative)?|jp|romanized)\s*[:\-]\s*",
                "",
                raw_alt,
                flags=re.IGNORECASE,
            ).strip()
            if raw_alt and raw_alt != title:
                alt_titles.append(raw_alt)

        # --- Description ---
        desc_node = tree.css_first(self.selectors["manga_description"])
        description = _clean_text(desc_node.text()) if desc_node else None

        # --- Couverture ---
        cover_url: str | None = None
        cover_node = tree.css_first(self.selectors["manga_cover"])
        if cover_node is not None:
            raw_cover = (
                cover_node.attributes.get("data-src")
                or cover_node.attributes.get("data-lazy-src")
                or cover_node.attributes.get("src")
            )
            if raw_cover:
                cover_url = _normalize_image_url(raw_cover, self.base_url)

        # --- Auteur / Artiste ---
        author: str | None = None
        author_node = tree.css_first(self.selectors["manga_author"])
        if author_node is not None:
            author = _clean_text(author_node.text()) or None

        artist: str | None = None
        artist_node = tree.css_first(self.selectors["manga_artist"])
        if artist_node is not None:
            artist = _clean_text(artist_node.text()) or None

        # --- Genres ---
        genres: list[str] = []
        for genre_node in tree.css(self.selectors["manga_genres"]):
            g = _clean_text(genre_node.text())
            if g and g not in genres:
                genres.append(g)

        # --- Statut et année ---
        status, year = self._parse_status_and_year(tree)

        # --- Chapitres ---
        chapters = self._parse_chapters_html(html)

        # --- Slug / ID ---
        slug = self._extract_slug(url) or "unknown"

        # --- Content rating et language ---
        content_rating = getattr(self, "content_rating", ContentRating.SAFE)
        language = self._resolve_language_enum()

        return Manga(
            id=f"{self.site_id}:{slug}",
            source_id=slug,
            site=self.site_id,
            title=title,
            alternative_titles=alt_titles,
            description=description,
            author=author,
            artist=artist,
            genres=genres,
            status=status,
            year=year,
            cover_url=cover_url,  # type: ignore[arg-type]
            language=language,
            content_rating=content_rating,
            chapters=chapters,
            url=url,  # type: ignore[arg-type]
            updated_at=datetime.now(UTC),
        )

    def _parse_chapters_html(self, html: str) -> list[Chapter]:
        """Parse la liste des chapitres.

        Args:
            html: HTML de la page.

        Returns:
            Liste de chapitres.
        """
        from selectolax.parser import HTMLParser  # noqa: PLC0415

        tree = HTMLParser(html)
        chapters: list[Chapter] = []
        seen_urls: set[str] = set()

        for item in tree.css(self.selectors["chapter_item"]):
            try:
                link = item.attributes.get("href") or ""
                if not link:
                    continue

                url = self._normalize_url(link)
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                raw_text = _clean_text(item.text())
                number = self._extract_chapter_number(raw_text)

                chapter_title = re.sub(
                    r"^\s*(chapter|chap|ch)\s*[\d.]+\s*:?\s*",
                    "",
                    raw_text,
                    flags=re.IGNORECASE,
                ).strip() or raw_text

                is_premium, is_future = self._detect_chapter_flags(
                    raw_text,
                    chapter_title,
                    link,
                )

                display_title = chapter_title
                if is_premium:
                    display_title = f"{self.premium_prefix} {display_title}"
                if is_future:
                    display_title = f"{self.future_prefix} {display_title}"

                chapter_slug = url.rstrip("/").split("/")[-1] or str(number)
                chapter_id = f"{self.site_id}:{chapter_slug}"

                chapters.append(
                    Chapter(
                        id=chapter_id,
                        source_id=chapter_slug,
                        title=display_title,
                        number=number,
                        volume=None,
                        language=self._resolve_language_enum(),
                        pages_count=None,
                        published_at=None,
                        url=url,  # type: ignore[arg-type]
                        pages=[],
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.bind(site=self.site_id).debug(
                    "Erreur parsing chapitre : {}",
                    exc,
                )
                continue

        return chapters

    def _parse_pages_html(self, html: str) -> list[Page]:
        """Parse les URLs des pages d'un chapitre.

        Args:
            html: HTML de la page du chapitre.

        Returns:
            Liste de pages ordonnées.
        """
        from selectolax.parser import HTMLParser  # noqa: PLC0415

        tree = HTMLParser(html)
        pages: list[Page] = []
        blob_count = 0
        tracking_count = 0

        for idx, img in enumerate(tree.css(self.selectors["page_image"])):
            raw_url = (
                img.attributes.get("data-src")
                or img.attributes.get("data-lazy-src")
                or img.attributes.get("data-original")
                or img.attributes.get("src")
                or ""
            )

            if not raw_url:
                continue

            if _is_blob_url(raw_url):
                blob_count += 1
                continue

            if _is_tracking_url(raw_url):
                tracking_count += 1
                continue

            url = _normalize_image_url(raw_url, self.base_url)
            ext = self._extract_image_ext(url)
            filename = f"page_{idx:04d}{ext}"

            pages.append(
                Page(
                    index=idx,
                    url=url,  # type: ignore[arg-type]
                    filename=filename,
                    checksum=None,
                ),
            )

        if blob_count:
            logger.bind(site=self.site_id).warning(
                "{} image(s) blob: ignorée(s) — extraction partielle",
                blob_count,
            )
        if tracking_count:
            logger.bind(site=self.site_id).debug(
                "{} image(s) de tracking ignorée(s)",
                tracking_count,
            )

        return pages

    # ------------------------------------------------------------------------
    #  HELPERS D'EXTRACTION
    # ------------------------------------------------------------------------

    def _parse_status_and_year(
        self,
        tree: Any,
    ) -> tuple[MangaStatus, int | None]:
        """Extrait le statut et l'année depuis un arbre HTML.

        Args:
            tree: Arbre ``selectolax.HTMLParser``.

        Returns:
            Tuple ``(status, year)``.
        """
        status = MangaStatus.UNKNOWN
        year: int | None = None

        for node in tree.css(self.selectors["manga_status"]):
            text = _clean_text(node.text()).lower()
            if not text:
                continue

            if "ongoing" in text or "en cours" in text:
                status = MangaStatus.ONGOING
            elif "completed" in text or "complete" in text or "terminé" in text:
                status = MangaStatus.COMPLETED
            elif "hiatus" in text or "on hold" in text:
                status = MangaStatus.HIATUS
            elif "canceled" in text or "cancelled" in text or "annulé" in text:
                status = MangaStatus.CANCELLED

            year_match = re.search(r"\b(19|20)\d{2}\b", text)
            if year_match and year is None:
                with suppress(ValueError):
                    year = int(year_match.group(0))

        return status, year

    def _extract_chapter_number(self, text: str) -> float | str:
        """Extrait un numéro de chapitre depuis un texte.

        Args:
            text: Texte à parser.

        Returns:
            Numéro en float, ou texte original si non numérique.
        """
        match = self.chapter_number_pattern.search(text)
        if match:
            value = _safe_float(match.group(1))
            if value is not None:
                return value

        numbers = re.findall(r"\d+(?:\.\d+)?", text)
        if numbers:
            value = _safe_float(numbers[0])
            if value is not None:
                return value

        return _clean_text(text)

    def _detect_chapter_flags(
        self,
        raw_text: str,
        chapter_title: str,
        link: str,
    ) -> tuple[bool, bool]:
        """Détecte les flags premium / futur.

        Args:
            raw_text: Texte brut.
            chapter_title: Titre nettoyé.
            link: URL du chapitre.

        Returns:
            Tuple ``(is_premium, is_future)``.
        """
        haystack = f"{raw_text} {chapter_title} {link}"
        is_premium = any(rx.search(haystack) for rx in self._premium_regex)
        is_future = any(rx.search(haystack) for rx in self._future_regex)
        return is_premium, is_future

    def _extract_image_ext(self, url: str) -> str:
        """Extrait l'extension d'une URL d'image (validée).

        Args:
            url: URL de l'image.

        Returns:
            Extension avec point (``".jpg"`` par défaut).
        """
        ext = ""
        try:
            ext = urlparse(url).path.rsplit(".", 1)[-1].lower()
        except Exception:  # noqa: BLE001
            return ".jpg"
        candidate = f".{ext}" if ext else ""
        if candidate and candidate in ALLOWED_IMAGE_EXTS:
            return candidate
        return ".jpg"

    def _extract_slug(self, url: str) -> str | None:
        """Extrait le slug série depuis une URL.

        Args:
            url: URL de la série.

        Returns:
            Slug, ou None.
        """
        match = self.series_slug_pattern.search(url)
        return match.group(1) if match else None

    def _resolve_language_enum(self) -> Language:
        """Résout ``self.language`` en enum ``Language``.

        Returns:
            Membre de ``Language``, ou ``Language.EN`` si inconnu.
        """
        lang_str = getattr(self, "language", "en")
        try:
            return Language(lang_str)
        except (ValueError, KeyError):
            logger.bind(site=self.site_id).debug(
                "Langue '{}' non reconnue, fallback sur EN",
                lang_str,
            )
            return Language.EN

    # ------------------------------------------------------------------------
    #  HELPERS D'URL
    # ------------------------------------------------------------------------

    def _normalize_url(self, url: str) -> str:
        """Normalise une URL (relative → absolue).

        Args:
            url: URL relative ou absolue.

        Returns:
            URL absolue.
        """
        url = url.strip()
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith(("http://", "https://")):
            return url
        if url.startswith("/"):
            return self.base_url + url
        return urljoin(self.base_url + "/", url)

    def _resolve_manga_url(self, url_or_id: str) -> str:
        """Résout une entrée en URL absolue.

        Args:
            url_or_id: URL, chemin, ou slug.

        Returns:
            URL absolue.
        """
        url_or_id = url_or_id.strip()

        if url_or_id.startswith(("http://", "https://")):
            return self._normalize_url(url_or_id)
        if url_or_id.startswith("/"):
            return self._normalize_url(url_or_id)

        return self.base_url + self.series_path_template.format(slug=url_or_id)

    # ------------------------------------------------------------------------
    #  HELPERS DE LECTURE
    # ------------------------------------------------------------------------

    def _is_404_page(self, html: str) -> bool:
        """Détecte une page 404.

        Args:
            html: HTML de la page.

        Returns:
            True si 404.
        """
        try:
            from selectolax.parser import HTMLParser  # noqa: PLC0415

            tree = HTMLParser(html)
            for selector in ("title", "h1"):
                node = tree.css_first(selector)
                if node:
                    text = node.text().lower()
                    if any(m in text for m in PAGE_404_MARKERS):
                        return True
        except ImportError:
            html_lower = html.lower()
            return any(m in html_lower for m in PAGE_404_MARKERS)
        return False

    # ------------------------------------------------------------------------
    #  FETCH HTML
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
            max_attempts: Nombre maximal de tentatives.

        Returns:
            HTML de la page.

        Raises:
            SiteUnreachableError: Si toutes les stratégies échouent.
        """
        session = getattr(self, "_session", None)
        if session is None:
            msg = (
                f"{type(self).__name__} n'expose pas self._session — "
                "héritage BaseParser requis pour utiliser MangaThemesiaMixin"
            )
            raise AttributeError(msg)

        playwright_pool = getattr(self, "_playwright_pool", None)
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                response = await session.get(
                    url,
                    params=params,
                    timeout=getattr(self, "http_timeout", 30.0),
                    headers={"Referer": self.base_url},
                )

                if response.status_code == 200:  # noqa: PLR2004
                    html = response.text
                    if not _is_cloudflare_challenge(html):
                        return html
                    logger.bind(site=self.site_id).debug(
                        "Challenge CF détecté (HTTP), bascule Playwright",
                    )
                elif response.status_code in (403, 503):  # noqa: PLR2004
                    logger.bind(site=self.site_id).debug(
                        "HTTP {} — challenge CF probable",
                        response.status_code,
                    )

            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.bind(site=self.site_id).debug(
                    "Tentative HTTP {}/{} échouée : {}",
                    attempt,
                    max_attempts,
                    exc,
                )

            if playwright_pool is not None and self.cloudflare_strategy == "playwright":
                try:
                    full_url = url
                    if params:
                        query = "&".join(f"{k}={v}" for k, v in params.items())
                        full_url = f"{url}?{query}"

                    html = await playwright_pool.fetch_html(
                        full_url,
                        wait_selector=self.cloudflare_wait_selector,
                        timeout=getattr(self, "playwright_timeout", 45.0),
                    )
                    if not _is_cloudflare_challenge(html):
                        return html
                    logger.bind(site=self.site_id).debug(
                        "Playwright : challenge CF persiste",
                    )
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

        msg = f"Impossible de récupérer {url} après {max_attempts} tentative(s)"
        raise SiteUnreachableError(msg) from last_error

    # ------------------------------------------------------------------------
    #  INTROSPECTION
    # ------------------------------------------------------------------------

    def describe_mangathemesia_config(self) -> dict[str, Any]:
        """Retourne la configuration effective du mixin.

        Returns:
            Dict sérialisable JSON.
        """
        self._ensure_initialized()
        return {
            "site_id": getattr(self, "site_id", ""),
            "base_url": getattr(self, "base_url", ""),
            "search_path": self.search_path,
            "search_query_param": self.search_query_param,
            "search_page_param": self.search_page_param,
            "series_path_template": self.series_path_template,
            "chapter_path_template": self.chapter_path_template,
            "cloudflare_strategy": self.cloudflare_strategy,
            "uses_ajax_pages": self.uses_ajax_pages,
            "ajax_endpoint": self.ajax_endpoint,
            "ajax_action": self.ajax_action,
            "selectors_count": len(self.selectors),
            "selectors": dict(self.selectors),
            "premium_patterns": list(self.premium_patterns),
            "future_patterns": list(self.future_patterns),
            "premium_prefix": self.premium_prefix,
            "future_prefix": self.future_prefix,
        }


# ============================================================================
#  EXPORTS
# ============================================================================

__all__ = [
    "ALLOWED_IMAGE_EXTS",
    "CLOUDFLARE_MARKERS",
    "MangaThemesiaMixin",
    "TRACKING_DOMAINS",
]
