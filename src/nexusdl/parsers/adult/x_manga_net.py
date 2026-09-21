"""Parser complet pour X-Manga sur le domaine historique (https://x-manga.net).

X-Manga est un site adulte français de scans hentai traduits en français,
basé sur le thème WordPress Madara (MangaBooth). Il est accessible via
deux domaines :

    - ``x-manga.net`` (historique, domaine de ce parser)
    - ``x-manga.org`` (principal actuel)

Les deux domaines servent **le même contenu**. Ce parser est autonome :
il contient toute la logique de parsing, gestion Cloudflare, rotation de
miroirs, et téléchargement de pages — aucune dépendance à un mixin.

Fonctionnalités intégrées
=========================

    - **Recherche** : pagination Madara standard, déduplication URL.
    - **Manga** : parsing complet (titre, description, auteur, artiste,
      genres, statut, année, couverture, chapitres).
    - **Chapitres** : tri décroissant, détection premium / « à venir »,
      filtrage des URLs manquantes.
    - **Pages** : extraction lazy-load, filtrage trackers, gestion blob:.
    - **Cloudflare** : bypass via Playwright, détection de challenge,
      retry avec backoff, rotation automatique de miroir.
    - **Téléchargement** : retry, validation taille/MIME, checksum,
      écriture atomique via ``.part``.
    - **Health check** : distingue domaine actif / redirigé / suspendu.

Historique des domaines
=======================

    - ``scan.hentai.menu``  → redirige vers x-manga.net (avril 2024)
    - ``x-manga.net``       → domaine historique de ce parser
    - ``x-manga.org``       → domaine principal actuel

Example:
    Utilisation directe::

        from nexusdl.parsers.adult.x_manga_net import XMangaNetParser
        from nexusdl.core.session.http_session import HttpSession

        async with HttpSession(site_config) as session:
            parser = XMangaNetParser(site_config, session)
            results = await parser.search("one piece")

Warning:
    Ce parser est marqué ``adult = True``. Il ne sera **pas** chargé si
    ``registry.include_adult`` est à ``False`` (défaut).
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final
from urllib.parse import urljoin, urlparse, urlunparse

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

#: Domaines miroirs connus, par ordre de préférence pour CE parser.
#: Le ``.net`` est le domaine canonique ; le ``.org`` est un fallback.
MIRROR_DOMAINS: Final[tuple[str, ...]] = (
    "https://x-manga.net",
    "https://x-manga.org",
)

#: Domaine canonique du parser.
BASE_URL: Final[str] = MIRROR_DOMAINS[0]

#: Marqueurs de domaines historiques à réécrire automatiquement.
REDIRECT_MARKERS: Final[tuple[str, ...]] = (
    "scan.hentai.menu",
    "x-manga.org",
)

#: Chemins URL (thème Madara).
SEARCH_PATH: Final[str] = "/"
SEARCH_PAGE_PARAM: Final[str] = "page"
MANGA_PATH_TEMPLATE: Final[str] = "/manga/{slug}/"
CHAPTER_PATH_TEMPLATE: Final[str] = "/manga/{slug}/{chapter_slug}/"

#: Sélecteurs CSS (thème Madara + personnalisations X-Manga).
SELECTORS: Final[dict[str, str]] = {
    # Recherche
    "search_item": "div.page-item-detail, div.c-tabs-item__content",
    "search_title": "h3.h5 a, div.post-title h3 a",
    "search_cover": "div.c-image-hover img, div.item-thumb img",
    "search_link": "h3.h5 a, div.post-title h3 a",
    # Manga
    "manga_title": "div.post-title h1, h1.entry-title",
    "manga_description": "div.description-summary, div.summary__content",
    "manga_cover": "div.summary_image img, div.thumb img",
    "manga_author": "div.author-content a, div.author-content",
    "manga_artist": "div.artist-content a",
    "manga_genres": "div.genres-content a, div.genres a",
    "manga_status": "div.summary-content",
    "manga_year": "div.summary-content",
    # Chapitres
    "chapter_list": "ul.main.version-chap, div.listing-chapters_wrap ul",
    "chapter_item": "li.wp-manga-chapter a",
    "chapter_date": "li.wp-manga-chapter span.chapter-release-date",
    # Pages
    "page_image": "div.reading-content img, div#readerarea img",
    "page_container": "div.reading-content, div#readerarea",
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

#: Domaines de tracking à exclure des pages.
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
)

#: User-Agent de secours.
DEFAULT_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ============================================================================
#  Regex patterns
# ============================================================================

#: Détection des chapitres marqués « premium » (accès payant).
PREMIUM_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(premium|vip|payant|locked|🔒|💎|⭐)\b",
    re.IGNORECASE,
)

#: Détection des chapitres « à venir » (annoncés mais non publiés).
FUTURE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(soon|bient[oô]t|[aà] venir|upcoming|TBA)\b",
    re.IGNORECASE,
)

#: Extraction d'un numéro de chapitre.
CHAPTER_NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:chapitre|chap|ch|chapter|ch\.)\s*([0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE,
)

#: Extraction du slug manga depuis une URL.
MANGA_SLUG_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"/manga/([^/]+)/?",
)

#: Détection des blobs JS.
BLOB_URL_PATTERN: Final[re.Pattern[str]] = re.compile(r"^blob:")


# ============================================================================
#  Helpers internes
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


def _parse_chapter_number(text: str) -> float | str:
    """Extrait un numéro de chapitre depuis un texte.

    Args:
        text: Texte à parser.

    Returns:
        Numéro en float, ou texte original si non numérique.
    """
    match = CHAPTER_NUMBER_PATTERN.search(text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if numbers:
        with suppress(ValueError):
            return float(numbers[0])
    return _clean_text(text)


def _is_blob_url(url: str) -> bool:
    """Détecte une URL ``blob:`` (image JS non téléchargeable).

    Args:
        url: URL à tester.

    Returns:
        True si l'URL est un blob.
    """
    return bool(BLOB_URL_PATTERN.match(url))


def _is_tracking_url(url: str) -> bool:
    """Détecte une URL de tracking / publicité.

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


def _extract_slug(url: str) -> str | None:
    """Extrait le slug d'un manga depuis une URL.

    Args:
        url: URL du manga.

    Returns:
        Slug, ou None si introuvable.
    """
    match = MANGA_SLUG_PATTERN.search(url)
    return match.group(1) if match else None


def _compute_sha256(data: bytes) -> str:
    """Calcule le hash SHA256 d'un contenu binaire.

    Args:
        data: Données binaires.

    Returns:
        Hash hexadécimal.
    """
    return hashlib.sha256(data).hexdigest()


# ============================================================================
#  Parser
# ============================================================================


class XMangaNetParser(BaseParser):
    """Parser complet pour X-Manga (https://x-manga.net).

    Implémentation autonome — aucune dépendance à un mixin. Toute la
    logique Madara est contenue dans cette classe, ce qui la rend
    testable en isolation complète.

    Attributes:
        site_id: Identifiant du parser (``"x_manga_net"``).
        language: Langue du contenu (``"fr"``).
        adult: Contenu 18+ (``True``).
        base_url: URL canonique du parser.
        mirror_domains: Liste des miroirs.
        cloudflare_strategy: Stratégie de bypass (``"playwright"``).
    """

    # --- Identité ---------------------------------------------------------
    site_id: ClassVar[str] = "x_manga_net"
    language: ClassVar[str] = "fr"
    adult: ClassVar[bool] = True

    # --- URLs -------------------------------------------------------------
    base_url: ClassVar[str] = BASE_URL
    mirror_domains: ClassVar[tuple[str, ...]] = MIRROR_DOMAINS

    # --- Options Madara ---------------------------------------------------
    search_path: ClassVar[str] = SEARCH_PATH
    search_page_param: ClassVar[str] = SEARCH_PAGE_PARAM
    manga_path_template: ClassVar[str] = MANGA_PATH_TEMPLATE
    chapter_path_template: ClassVar[str] = CHAPTER_PATH_TEMPLATE

    # --- Sélecteurs -------------------------------------------------------
    selectors: ClassVar[dict[str, str]] = SELECTORS

    # --- Cloudflare -------------------------------------------------------
    cloudflare_strategy: ClassVar[str] = "playwright"
    cloudflare_cookie_name: ClassVar[str] = "cf_clearance"
    cloudflare_wait_selector: ClassVar[str | None] = "div.post-title, div.wp-manga-chapter"

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
        """Initialise le parser X-Manga (.net).

        Args:
            config: Configuration du site.
            session: Session HTTP configurée (cookies, proxy, rate limit).
            playwright_pool: Pool Playwright. **Recommandé** — sans lui,
                les challenges Cloudflare ne peuvent pas être résolus et
                les images en ``blob:`` ne sont pas accessibles.
        """
        super().__init__(config, session, playwright_pool=playwright_pool)

        self._mirror_index: int = 0
        self._active_base: str = self.base_url
        self._closed: bool = False

        logger.bind(site=self.site_id).debug(
            "Parser X-Manga (.net) initialisé (playwright={}, mirrors={})",
            playwright_pool is not None,
            len(self.mirror_domains),
        )

    # ------------------------------------------------------------------------
    #  Contexte async
    # ------------------------------------------------------------------------

    async def __aenter__(self) -> XMangaNetParser:
        """Entre dans le contexte async.

        Returns:
            Le parser.
        """
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        """Sort du contexte async.

        Args:
            *exc_info: Infos sur l'exception éventuelle.
        """
        await self.close()

    async def close(self) -> None:
        """Libère les ressources du parser (idempotent)."""
        if self._closed:
            return
        self._closed = True
        logger.bind(site=self.site_id).debug("Parser X-Manga (.net) fermé")

    # ------------------------------------------------------------------------
    #  API publique — recherche
    # ------------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        page: int = 1,
    ) -> list[SearchResult]:
        """Recherche des mangas sur X-Manga (.net).

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

        params: dict[str, str] = {
            "s": query,
            "post_type": "wp-manga",
        }
        if page > 1:
            params[self.search_page_param] = str(page)

        html = await self._fetch_html(
            self._active_base + self.search_path,
            params=params,
        )

        results = self._parse_search_html(html)

        logger.bind(site=self.site_id).info(
            "{} résultat(s) pour '{}' (page {})",
            len(results),
            query,
            page,
        )
        return results

    # ------------------------------------------------------------------------
    #  API publique — manga
    # ------------------------------------------------------------------------

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère les métadonnées complètes d'un manga.

        Args:
            url_or_id: URL complète, chemin, ou slug.

        Returns:
            Objet Manga peuplé.

        Raises:
            MangaNotFoundError: Si la page renvoie 404.
            SiteUnreachableError: Si le site ne répond pas.
        """
        url = self._resolve_manga_url(url_or_id)
        logger.bind(site=self.site_id).debug("Récupération manga : {}", url)

        html = await self._fetch_html(url)

        if self._is_404_page(html):
            msg = f"Manga introuvable : {url}"
            raise MangaNotFoundError(msg)

        manga = self._parse_manga_html(html, url)

        logger.bind(site=self.site_id).info(
            "Manga : '{}' ({} chapitre(s))",
            manga.title,
            len(manga.chapters),
        )
        return manga

    # ------------------------------------------------------------------------
    #  API publique — chapitres
    # ------------------------------------------------------------------------

    async def get_chapters(self, manga: Manga) -> list[Chapter]:
        """Récupère la liste des chapitres avec tri décroissant.

        Enrichit chaque chapitre :
            - ``[Premium]`` si accès payant.
            - ``[À venir]`` si annoncé mais non publié.

        Args:
            manga: Manga cible.

        Returns:
            Liste de chapitres triés (plus récent en premier).
        """
        if manga.chapters:
            chapters = list(manga.chapters)
        else:
            html = await self._fetch_html(str(manga.url))
            chapters = self._parse_chapters_html(html, manga)

        valid = [ch for ch in chapters if ch.url and str(ch.url).strip()]
        filtered = len(chapters) - len(valid)
        if filtered:
            logger.bind(site=self.site_id).debug(
                "{} chapitre(s) sans URL filtré(s)", filtered
            )

        valid.sort(key=self._chapter_sort_key, reverse=True)

        logger.bind(site=self.site_id).info(
            "{} chapitre(s) pour '{}' ({} premium, {} à venir)",
            len(valid),
            manga.title,
            sum(1 for ch in valid if ch.title and ch.title.startswith("[Premium]")),
            sum(1 for ch in valid if ch.title and ch.title.startswith("[À venir]")),
        )
        return valid

    @staticmethod
    def _chapter_sort_key(chapter: Chapter) -> float:
        """Clé de tri numérique pour un chapitre.

        Args:
            chapter: Chapitre.

        Returns:
            Numéro en float, ou 0.0 si non numérique.
        """
        try:
            return float(chapter.number)
        except (ValueError, TypeError):
            return 0.0

    # ------------------------------------------------------------------------
    #  API publique — pages
    # ------------------------------------------------------------------------

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste de pages ordonnées.

        Raises:
            ChapterDownloadError: Si aucune page n'est trouvée.
        """
        url = str(chapter.url)
        logger.bind(site=self.site_id).debug(
            "Pages pour ch.{} : {}", chapter.number, url
        )

        if chapter.title and chapter.title.startswith("[Premium]"):
            logger.bind(site=self.site_id).warning(
                "Chapitre premium : ch.{} — accès payant possible",
                chapter.number,
            )

        html = await self._fetch_html(url)
        pages = self._parse_pages_html(html, chapter)

        if not pages:
            msg = f"Aucune page trouvée pour ch.{chapter.number} ({url})"
            raise ChapterDownloadError(msg)

        logger.bind(site=self.site_id).info(
            "{} page(s) pour ch.{} de '{}'",
            len(pages),
            chapter.number,
            chapter.title or "(sans titre)",
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
        """Résout le chemin de destination d'une page.

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
            ChapterDownloadError: Si la validation échoue.
        """
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
        try:
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
        """Vérifie que X-Manga (.net) est accessible.

        Distingue domaine actif / redirigé / suspendu.

        Returns:
            True si le domaine répond correctement.
        """
        try:
            if self._playwright_pool is not None:
                html = await self._playwright_pool.fetch_html(
                    self._active_base,
                    timeout=self.playwright_timeout,
                )
                if _is_cloudflare_challenge(html):
                    logger.bind(site=self.site_id).warning(
                        "X-Manga (.net) : challenge CF non résolu"
                    )
                    return False
                if len(html) > 1000:  # noqa: PLR2004
                    return True
            else:
                response = await self._session.get(
                    self._active_base,
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
            "X-Manga (.net) : aucun domaine accessible. Site possiblement "
            "suspendu ou migré vers un domaine non référencé."
        )
        return False

    # ------------------------------------------------------------------------
    #  API publique — URL
    # ------------------------------------------------------------------------

    def normalize_url(self, url: str) -> str:
        """Normalise une URL (relative → absolue, réécriture historique).

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
                url = urlunparse(
                    parsed._replace(netloc=expected.netloc, scheme=expected.scheme)
                )
            return url

        if url.startswith("/"):
            return self._active_base + url
        return urljoin(self._active_base + "/", url)

    def _resolve_manga_url(self, url_or_id: str) -> str:
        """Résout une entrée en URL de manga absolue.

        Args:
            url_or_id: URL, chemin, ou slug.

        Returns:
            URL absolue.
        """
        url_or_id = url_or_id.strip()
        if url_or_id.startswith(("http://", "https://")):
            return self.normalize_url(url_or_id)
        if url_or_id.startswith("/"):
            return self.normalize_url(url_or_id)
        return self._active_base + self.manga_path_template.format(slug=url_or_id)

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

        Stratégie :
            1. HTTP standard (rapide).
            2. Si challenge ou 403, bascule sur Playwright.
            3. Si Playwright indisponible, retry avec backoff.
            4. Après épuisement, rotation de miroir.

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
                        wait_selector=self.cloudflare_wait_selector,
                        timeout=self.playwright_timeout,
                    )
                    if not _is_cloudflare_challenge(html):
                        return html
                    logger.bind(site=self.site_id).debug(
                        "Playwright : challenge CF persiste"
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
    #  Parsing — recherche
    # ------------------------------------------------------------------------

    def _parse_search_html(self, html: str) -> list[SearchResult]:
        """Parse la page de résultats de recherche.

        Args:
            html: HTML de la page.

        Returns:
            Liste de SearchResult.
        """
        try:
            from selectolax.parser import HTMLParser  # noqa: PLC0415
        except ImportError:
            logger.bind(site=self.site_id).error("selectolax indisponible")
            return []

        tree = HTMLParser(html)
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for item in tree.css(self.selectors["search_item"]):
            try:
                title_node = item.css_first(self.selectors["search_title"])
                if title_node is None:
                    continue

                link = title_node.attributes.get("href") or ""
                if not link:
                    continue

                title = _clean_text(title_node.text())
                if not title:
                    continue

                url = self.normalize_url(link)
                if url in seen_urls:
                    continue
                seen_urls.add(url)

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
                        cover_url = _normalize_image_url(raw_cover, self._active_base)

                author: str | None = None
                author_node = item.css_first(self.selectors["manga_author"])
                if author_node is not None:
                    author = _clean_text(author_node.text()) or None

                slug = _extract_slug(url)

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
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.bind(site=self.site_id).debug(
                    "Erreur parsing item recherche : {}", exc
                )
                continue

        return results

    # ------------------------------------------------------------------------
    #  Parsing — manga
    # ------------------------------------------------------------------------

    def _parse_manga_html(self, html: str, url: str) -> Manga:
        """Parse la page d'un manga.

        Args:
            html: HTML de la page.
            url: URL du manga.

        Returns:
            Objet Manga peuplé.
        """
        from selectolax.parser import HTMLParser  # noqa: PLC0415

        tree = HTMLParser(html)

        title_node = tree.css_first(self.selectors["manga_title"])
        title = _clean_text(title_node.text()) if title_node else ""

        desc_node = tree.css_first(self.selectors["manga_description"])
        description = _clean_text(desc_node.text()) if desc_node else None

        cover_url: str | None = None
        cover_node = tree.css_first(self.selectors["manga_cover"])
        if cover_node is not None:
            raw_cover = (
                cover_node.attributes.get("data-src")
                or cover_node.attributes.get("data-lazy-src")
                or cover_node.attributes.get("src")
            )
            if raw_cover:
                cover_url = _normalize_image_url(raw_cover, self._active_base)

        author: str | None = None
        author_node = tree.css_first(self.selectors["manga_author"])
        if author_node is not None:
            author = _clean_text(author_node.text()) or None

        artist: str | None = None
        artist_node = tree.css_first(self.selectors["manga_artist"])
        if artist_node is not None:
            artist = _clean_text(artist_node.text()) or None

        genres: list[str] = []
        for genre_node in tree.css(self.selectors["manga_genres"]):
            g = _clean_text(genre_node.text())
            if g and g not in genres:
                genres.append(g)

        status = MangaStatus.UNKNOWN
        year: int | None = None
        for node in tree.css(self.selectors["manga_status"]):
            text = _clean_text(node.text()).lower()
            if "en cours" in text or "ongoing" in text:
                status = MangaStatus.ONGOING
            elif "terminé" in text or "completed" in text:
                status = MangaStatus.COMPLETED
            elif "hiatus" in text:
                status = MangaStatus.HIATUS
            elif "annulé" in text or "cancelled" in text:
                status = MangaStatus.CANCELLED
            year_match = re.search(r"\b(19|20)\d{2}\b", text)
            if year_match:
                with suppress(ValueError):
                    year = int(year_match.group(0))

        chapters = self._parse_chapters_html(html, None)  # type: ignore[arg-type]
        slug = _extract_slug(url) or url
        updated_at = datetime.now(UTC)

        return Manga(
            id=f"{self.site_id}:{slug}",
            source_id=slug,
            site=self.site_id,
            title=title,
            alternative_titles=[],
            description=description,
            author=author,
            artist=artist,
            genres=genres,
            status=status,
            year=year,
            cover_url=cover_url,  # type: ignore[arg-type]
            language=Language.FR,
            content_rating=self.content_rating,
            chapters=chapters,
            url=url,  # type: ignore[arg-type]
            updated_at=updated_at,
        )

    # ------------------------------------------------------------------------
    #  Parsing — chapitres
    # ------------------------------------------------------------------------

    def _parse_chapters_html(
        self,
        html: str,
        manga: Manga | None,
    ) -> list[Chapter]:
        """Parse la liste des chapitres.

        Args:
            html: HTML de la page.
            manga: Manga parent (optionnel).

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

                url = self.normalize_url(link)
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                raw_text = _clean_text(item.text())
                number = _parse_chapter_number(raw_text)

                chapter_title = re.sub(
                    r"^\s*(chapitre|chap|ch|chapter)\s*[\d.]+\s*:?\s*",
                    "",
                    raw_text,
                    flags=re.IGNORECASE,
                ).strip() or raw_text

                full_text = f"{raw_text} {chapter_title} {link}"
                is_premium = bool(PREMIUM_PATTERN.search(full_text))
                is_future = bool(FUTURE_PATTERN.search(full_text))

                display_title = chapter_title
                if is_premium:
                    display_title = f"[Premium] {display_title}"
                if is_future:
                    display_title = f"[À venir] {display_title}"

                chapter_slug = url.rstrip("/").split("/")[-1] or str(number)
                chapter_id = f"{self.site_id}:{chapter_slug}"

                chapters.append(
                    Chapter(
                        id=chapter_id,
                        source_id=chapter_slug,
                        title=display_title,
                        number=number,
                        volume=None,
                        language=Language.FR,
                        pages_count=None,
                        published_at=None,
                        url=url,  # type: ignore[arg-type]
                        pages=[],
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.bind(site=self.site_id).debug(
                    "Erreur parsing chapitre : {}", exc
                )
                continue

        return chapters

    # ------------------------------------------------------------------------
    #  Parsing — pages
    # ------------------------------------------------------------------------

    def _parse_pages_html(
        self,
        html: str,
        chapter: Chapter,
    ) -> list[Page]:
        """Parse les URLs des pages d'un chapitre.

        Args:
            html: HTML de la page.
            chapter: Chapitre parent.

        Returns:
            Liste de pages ordonnées.
        """
        from selectolax.parser import HTMLParser  # noqa: PLC0415

        tree = HTMLParser(html)
        pages: list[Page] = []
        blob_count = 0

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
                logger.bind(site=self.site_id).debug(
                    "Image blob: (index {}) — nécessite Playwright", idx
                )
                continue

            if _is_tracking_url(raw_url):
                logger.bind(site=self.site_id).trace(
                    "Image tracking ignorée : {}", raw_url[:80]
                )
                continue

            url = _normalize_image_url(raw_url, self._active_base)

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

        if blob_count > 0:
            logger.bind(site=self.site_id).warning(
                "{} image(s) blob: pour ch.{} — extraction partielle",
                blob_count,
                chapter.number,
            )

        return pages

    # ------------------------------------------------------------------------
    #  Helpers — détection 404
    # ------------------------------------------------------------------------

    def _is_404_page(self, html: str) -> bool:
        """Détecte une page 404 Madara.

        Args:
            html: HTML de la page.

        Returns:
            True si c'est une page 404.
        """
        markers = ("404", "not found", "page introuvable", "introuvable")
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
            "cloudflare_strategy": self.cloudflare_strategy,
            "selectors_count": len(self.selectors),
        }


# ============================================================================
#  Exports
# ============================================================================

__all__ = [
    "BASE_URL",
    "MIRROR_DOMAINS",
    "SELECTORS",
    "XMangaNetParser",
]
