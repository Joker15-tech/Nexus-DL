"""Parseur Void Scans pour NexusDL.

Void Scans (``https://voidscans.net``) est un site de scanlation anglophone
utilisant le thème WordPress **MangaStream**, le même moteur que Asura Scans,
Flame Comics, Luminous Scans, Rizz Comic, etc. Le site héberge principalement
des manhwa et webtoons traduits en anglais.

Caractéristiques techniques
---------------------------

* **Moteur** : MangaStream WordPress Plugin.
* **Langue** : Anglais (``en``).
* **Protection** : Cloudflare (IP ``162.159.135.233``, AS13335 Cloudflare).
* **Domaines** : ``voidscans.net`` (domaine principal actuel) ; auparavant
  ``void-scans.com`` puis ``hivescans.com``. Le domaine peut changer — un
  mécanisme de fallback est prévu.
* **Structure** : pages de séries sous ``/manga/{slug}/``, chapitres sous
  ``/manga/{slug}/{chapter-slug}/``, images servies depuis le même domaine
  ou un CDN.
* **Paywall temporel** : certains chapitres récents sont verrouillés
  (``.ch-price-side``) — le parseur les filtre automatiquement.

Le parseur combine trois mixins :

1. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — pour le
   rendu Playwright si nécessaire (fallback pour les pages dynamiques).
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — pour le
   contournement Cloudflare automatique (Playwright / FlareSolverr).
3. Implémentations MangaStream **inline** — les sélecteurs et la logique de
   parsing sont adaptés au thème MangaStream (pas de mixin dédié disponible
   dans le projet, la logique est donc intégrée directement).

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.en.void_scans import VoidScansParser
    >>>
    >>> parser = VoidScansParser(config, session)
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

__all__ = ["VoidScansParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://voidscans.net"
_FALLBACK_DOMAINS: Final[tuple[str, ...]] = (
    "https://voidscans.net",
    "https://void-scans.com",
    "https://hivescans.com",
)
_SITE_ID: Final[str] = "void_scans"
_LANGUAGE: Final[Language] = Language.EN
_ADULT: Final[bool] = False

# Regex de parsing des chapitres (format MangaStream : "Chapter 12.5").
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
# Sélecteurs MangaStream (thème WordPress)
# ---------------------------------------------------------------------------

_MS_SEARCH_ITEM_SELECTOR: Final[str] = (
    ".listupd .bs, .listupd .bsx, .bs, .bsx, .manga-item, .uta"
)
_MS_SEARCH_LINK_SELECTOR: Final[str] = "a"
_MS_SEARCH_COVER_SELECTOR: Final[str] = "img"
_MS_SEARCH_TITLE_SELECTOR: Final[str] = ".tt, .title, h3, h4, .entry-title"

_MS_MANGA_TITLE_SELECTOR: Final[str] = (
    "h1.entry-title, h1, .entry-title, .manga-title, .post-title"
)
_MS_MANGA_COVER_SELECTOR: Final[str] = (
    ".thumb img, .manga-cover img, .summary_image img, .cover img, .thumbook img"
)
_MS_MANGA_DESCRIPTION_SELECTOR: Final[str] = (
    ".summary__content, .manga-summary, .description, "
    ".entry-content p, .desc, .wd-full p"
)
_MS_MANGA_AUTHOR_SELECTOR: Final[str] = (
    ".author, .manga-author, a[href*='/author/'], "
    ".tsinfo .imptdt:contains('Author') a"
)
_MS_MANGA_ARTIST_SELECTOR: Final[str] = (
    ".artist, .manga-artist, a[href*='/artist/'], "
    ".tsinfo .imptdt:contains('Artist') a"
)
_MS_MANGA_GENRES_SELECTOR: Final[str] = (
    ".genres a, .manga-genres a, a[href*='/genre/'], "
    "a[href*='/tag/'], .mgen a"
)
_MS_MANGA_STATUS_SELECTOR: Final[str] = (
    ".status, .manga-status, .post-status, "
    ".tsinfo .imptdt:contains('Status') i"
)
_MS_MANGA_YEAR_SELECTOR: Final[str] = (
    ".year, .manga-year, .post-year, "
    ".tsinfo .imptdt:contains('Year') i"
)

_MS_CHAPTER_SELECTOR: Final[str] = (
    ".eplister a, .chapter-list a, "
    "a[href*='/manga/'][href*='/chapter-'], "
    "li:has(.chbox .eph-num):not(:has(.ch-price-side)) a"
)
_MS_PAGE_IMG_SELECTOR: Final[str] = (
    ".chapter-page img, .page-image img, "
    ".reading-content img, .reader img, "
    "#readerarea img, .main-reading-area img"
)
_MS_PAGES_VAR_NAMES: Final[tuple[str, ...]] = (
    "pages",
    "page_urls",
    "pageUrls",
    "images",
    "chapterImages",
    "ts_reader",
)


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


class VoidScansParser(
    JsRenderedMixin,
    CloudflareMixin,
    BaseParser,
):
    """Parseur Void Scans (MangaStream + Cloudflare).

    Combine le rendu Playwright (fallback), le contournement Cloudflare et
    les implémentations MangaStream inline pour fournir une couverture
    complète du site.

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
    fools_base_url: ClassVar[str | None] = None  # Non utilisé (pas FoolSlide)

    # --- Configuration MangaStream ---
    ms_search_path: ClassVar[str] = "/?s="
    ms_search_query_param: ClassVar[str] = "s"
    ms_series_path: ClassVar[str] = "/manga"
    ms_read_path: ClassVar[str] = "/manga"

    # --- Sélecteurs MangaStream ---
    ms_search_item_selector: ClassVar[str] = _MS_SEARCH_ITEM_SELECTOR
    ms_search_link_selector: ClassVar[str] = _MS_SEARCH_LINK_SELECTOR
    ms_search_cover_selector: ClassVar[str] = _MS_SEARCH_COVER_SELECTOR
    ms_search_title_selector: ClassVar[str] = _MS_SEARCH_TITLE_SELECTOR

    ms_manga_title_selector: ClassVar[str] = _MS_MANGA_TITLE_SELECTOR
    ms_manga_cover_selector: ClassVar[str] = _MS_MANGA_COVER_SELECTOR
    ms_manga_description_selector: ClassVar[str] = _MS_MANGA_DESCRIPTION_SELECTOR
    ms_manga_author_selector: ClassVar[str] = _MS_MANGA_AUTHOR_SELECTOR
    ms_manga_artist_selector: ClassVar[str] = _MS_MANGA_ARTIST_SELECTOR
    ms_manga_genres_selector: ClassVar[str] = _MS_MANGA_GENRES_SELECTOR
    ms_manga_status_selector: ClassVar[str] = _MS_MANGA_STATUS_SELECTOR
    ms_manga_year_selector: ClassVar[str] = _MS_MANGA_YEAR_SELECTOR

    ms_chapter_selector: ClassVar[str] = _MS_CHAPTER_SELECTOR
    ms_page_img_selector: ClassVar[str] = _MS_PAGE_IMG_SELECTOR
    ms_pages_var_names: ClassVar[tuple[str, ...]] = _MS_PAGES_VAR_NAMES

    # --- Comportement ---
    ms_requires_js: ClassVar[bool] = False
    ms_default_rating: ClassVar[ContentRating] = ContentRating.SAFE
    ms_concurrent_chapter_requests: ClassVar[int] = 4
    ms_search_pages_limit: ClassVar[int] = 20

    # --- Configuration Cloudflare ---
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
    default_wait_until: ClassVar[str] = "domcontentloaded"
    default_render_timeout: ClassVar[float] = 30.0
    default_navigation_timeout: ClassVar[float] = 45.0
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
        """Initialise le parseur Void Scans.

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

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------

    def _ms_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="mangastream"``.
        """
        return self.logger

    @staticmethod
    def _ms_clean(value: str | None) -> str:
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
    def _ms_text(node: Node | None) -> str:
        """Extrait et nettoie le texte d'un nœud.

        Args:
            node: Nœud selectolax ou ``None``.

        Returns:
            Texte nettoyé.
        """
        if node is None:
            return ""
        return VoidScansParser._ms_clean(node.text(deep=True, separator=" "))

    @staticmethod
    def _ms_attr(node: Node | None, name: str) -> str:
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

    def _ms_first(self, tree: HTMLParser, selector: str) -> Node | None:
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

    def _ms_all(self, tree: HTMLParser, selector: str) -> list[Node]:
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

    def _ms_abs(self, url: str, base: str | None = None) -> str:
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

    def _ms_parse_status(self, raw: str | None) -> MangaStatus:
        """Convertit un libellé de statut MangaStream en énumération.

        Args:
            raw: Libellé brut.

        Returns:
            Statut normalisé (``ONGOING`` par défaut).
        """
        if not raw:
            return MangaStatus.ONGOING
        key = self._ms_clean(raw).lower()
        for needle, status in _STATUS_MAP.items():
            if needle in key:
                return status
        return MangaStatus.ONGOING

    @staticmethod
    def _ms_parse_year(raw: str | None) -> int | None:
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
    def _ms_parse_relative_date(raw: str | None) -> datetime | None:
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
    def _ms_chapter_number(text: str) -> float | str:
        """Extrait le numéro de chapitre d'un libellé.

        Args:
            text: Libellé (ex. ``"Chapter 12.5"``).

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = VoidScansParser._ms_clean(text)
        match = _CHAPTER_NUMBER_RE.search(cleaned)
        if not match:
            return cleaned
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return cleaned

    @staticmethod
    def _ms_chapter_volume(text: str) -> int | None:
        """Extrait le numéro de tome d'un libellé.

        Args:
            text: Libellé contenant éventuellement ``Volume X``.

        Returns:
            Numéro de tome ou ``None``.
        """
        match = _VOLUME_RE.search(text or "")
        return int(match.group(1)) if match else None

    def _ms_detect_rating(self, genres: list[str]) -> ContentRating:
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
        return self.ms_default_rating

    # ------------------------------------------------------------------
    # Méthodes abstraites de BaseParser
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des mangas sur Void Scans.

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.

        Raises:
            ParseError: Si le HTML de résultats est inexploitable.
        """
        self.logger.debug(
            "VoidScans search: {query} (page {page})",
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
                "Échec recherche VoidScans pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        return self._ms_parse_search_html(html, base_url=url)

    def _ms_parse_search_html(
        self,
        html: str,
        *,
        base_url: str,
    ) -> list[SearchResult]:
        """Parse le HTML de résultats de recherche MangaStream.

        Args:
            html: HTML brut.
            base_url: URL de la page (pour résolution relative).

        Returns:
            Liste de :class:`SearchResult`.
        """
        tree = HTMLParser(html)
        results: list[SearchResult] = []
        for node in self._ms_all(tree, self.ms_search_item_selector):
            link_node = node.css_first(self.ms_search_link_selector)
            href = self._ms_attr(link_node, "href")
            if not href:
                continue
            abs_url = self._ms_abs(href, base_url)

            title_node = node.css_first(self.ms_search_title_selector)
            title = self._ms_text(title_node) or self._ms_attr(link_node, "title")
            if not title:
                continue

            cover_node = node.css_first(self.ms_search_cover_selector)
            cover_src = (
                self._ms_attr(cover_node, "data-src")
                or self._ms_attr(cover_node, "data-lazy-src")
                or self._ms_attr(cover_node, "src")
            )
            cover_url = self._ms_abs(cover_src, base_url) if cover_src else None

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
            "VoidScans search: {n} résultat(s)", n=len(results)
        )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga Void Scans.

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
            url = f"{base}{self.ms_series_path}/{url_or_id.strip('/')}/"

        self.logger.debug("VoidScans get_manga: {url}", url=url)

        try:
            response = await self.cf_get(url)
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_manga VoidScans pour {url!r}: {err}",
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

        title_node = self._ms_first(tree, self.ms_manga_title_selector)
        title = self._ms_text(title_node)
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        cover_node = self._ms_first(tree, self.ms_manga_cover_selector)
        cover_src = (
            self._ms_attr(cover_node, "data-src")
            or self._ms_attr(cover_node, "data-lazy-src")
            or self._ms_attr(cover_node, "src")
        )
        cover_url = self._ms_abs(cover_src, url) if cover_src else None

        description_node = self._ms_first(
            tree, self.ms_manga_description_selector
        )
        description = self._ms_text(description_node) or None

        author_node = self._ms_first(tree, self.ms_manga_author_selector)
        author = self._ms_text(author_node) or None

        artist_node = self._ms_first(tree, self.ms_manga_artist_selector)
        artist = self._ms_text(artist_node) or None

        genre_nodes = self._ms_all(tree, self.ms_manga_genres_selector)
        genres: list[str] = []
        for node in genre_nodes:
            text = self._ms_text(node)
            if text and text not in genres:
                genres.append(text)

        status_node = self._ms_first(tree, self.ms_manga_status_selector)
        status = self._ms_parse_status(self._ms_text(status_node))

        year_node = self._ms_first(tree, self.ms_manga_year_selector)
        year = self._ms_parse_year(self._ms_text(year_node))

        source_id = self._ms_extract_series_slug(url)
        rating = self._ms_detect_rating(genres)

        chapters = self._ms_parse_chapters_from_html(html, base_url=url)

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
            "VoidScans get_manga OK: {title} ({n} chapitres)",
            title=title,
            n=len(chapters),
        )
        return manga

    @staticmethod
    def _ms_extract_series_slug(url: str) -> str:
        """Extrait un identifiant de série depuis une URL MangaStream.

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
            "VoidScans get_chapters: {title}", title=manga.title
        )
        try:
            html = await self.cf_get_html(str(manga.url))
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_chapters VoidScans pour {title!r}: {err}",
                title=manga.title,
                err=exc,
            )
            raise ParseError(
                f"get_chapters échoué sur {self.site_id!r} "
                f"pour {manga.title!r}: {exc}"
            ) from exc

        chapters = self._ms_parse_chapters_from_html(
            html, base_url=str(manga.url)
        )
        if not chapters:
            raise ChapterNotFoundError(
                f"Aucun chapitre trouvé pour {manga.url} sur {self.site_id!r}"
            )
        return chapters

    def _ms_parse_chapters_from_html(
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

        for node in self._ms_all(tree, self.ms_chapter_selector):
            href = self._ms_attr(node, "href")
            if not href:
                continue
            abs_url = self._ms_abs(href, base_url)
            if abs_url in seen:
                continue
            seen.add(abs_url)

            label = self._ms_text(node) or self._ms_attr(node, "title")
            if not label:
                label = abs_url.rstrip("/").rsplit("/", 1)[-1] or abs_url

            number = self._ms_chapter_number(label)
            volume = self._ms_chapter_volume(label)

            date_text = self._ms_attr(node, "data-date") or None
            published = (
                self._ms_parse_relative_date(date_text)
                if date_text
                else self._ms_parse_relative_date(label)
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
            num = ch.number if isinstance(ch.number, (int, float)) else 0.0
            return (int(isinstance(ch.number, str)), float(num), ch.title.lower())

        chapters.sort(key=_sort_key)
        self.logger.debug(
            "VoidScans chapitres: {n}", n=len(chapters)
        )
        return chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre Void Scans.

        Stratégie (par ordre de priorité) :

        1. Variable JS ``var pages = [...]`` embarquée dans le HTML.
        2. Sélecteur CSS d'images (fallback HTML).

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "VoidScans get_pages: {url}", url=str(chapter.url)
        )
        chapter_url = str(chapter.url)

        try:
            html = await self.cf_get_html(chapter_url)
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_pages VoidScans pour {url!r}: {err}",
                url=chapter_url,
                err=exc,
            )
            raise ParseError(
                f"get_pages échoué sur {self.site_id!r} "
                f"pour {chapter_url!r}: {exc}"
            ) from exc

        # Tentative 1 : extraction via variable JS.
        pages = self._ms_extract_pages_from_js(html, base_url=chapter_url)
        if pages:
            return pages

        # Tentative 2 : extraction via sélecteur CSS d'images.
        pages = self._ms_extract_pages_from_html(html, base_url=chapter_url)
        if pages:
            return pages

        raise ChapterNotFoundError(
            f"Aucune page trouvée pour {chapter_url} sur {self.site_id!r}"
        )

    def _ms_extract_pages_from_js(
        self,
        html: str,
        *,
        base_url: str,
    ) -> list[Page]:
        """Extrait les URLs de pages depuis une variable JS.

        Args:
            html: HTML du chapitre.
            base_url: URL de la page pour résolution relative.

        Returns:
            Liste de :class:`Page` (vide si introuvable).
        """
        for var_name in self.ms_pages_var_names:
            pattern = re.compile(
                rf"(?:var|let|const)\s+{re.escape(var_name)}\s*=\s*(\[.*?\])\s*;",
                re.DOTALL,
            )
            for match in pattern.finditer(html):
                raw = match.group(1)
                urls = self._ms_parse_js_array(raw)
                if urls:
                    resolved = [self._ms_abs(u, base_url) for u in urls]
                    return self._ms_build_pages(resolved)
        return []

    @staticmethod
    def _ms_parse_js_array(raw: str) -> list[str]:
        """Parse un tableau JS ``[ ... ]`` en liste d'URLs.

        Args:
            raw: Contenu brut du tableau JS.

        Returns:
            Liste d'URLs (chaînes).
        """
        import json as _json

        try:
            parsed = _json.loads(raw)
        except ValueError:
            parsed = None

        urls: list[str] = []
        if isinstance(parsed, list):
            for entry in parsed:
                if isinstance(entry, str):
                    urls.append(entry)
                elif isinstance(entry, dict):
                    for key in ("url", "src", "image", "page"):
                        value = entry.get(key)
                        if isinstance(value, str):
                            urls.append(value)
                            break
            return urls

        # Fallback : extraction par regex de toutes les chaînes.
        urls = re.findall(r"['\"](https?://[^'\"]+|/[^'\"]+)['\"]", raw)
        return urls

    def _ms_extract_pages_from_html(
        self,
        html: str,
        *,
        base_url: str,
    ) -> list[Page]:
        """Extrait les URLs de pages depuis les balises ``<img>``.

        Args:
            html: HTML du chapitre.
            base_url: URL de la page.

        Returns:
            Liste de :class:`Page`.
        """
        tree = HTMLParser(html)
        urls: list[str] = []
        for node in self._ms_all(tree, self.ms_page_img_selector):
            src = (
                self._ms_attr(node, "data-src")
                or self._ms_attr(node, "data-lazy-src")
                or self._ms_attr(node, "data-original")
                or self._ms_attr(node, "src")
            )
            if not src:
                continue
            abs_url = self._ms_abs(src, base_url)
            if abs_url not in urls:
                urls.append(abs_url)
        return self._ms_build_pages(urls)

    def _ms_build_pages(self, urls: list[str]) -> list[Page]:
        """Construit des :class:`Page` depuis une liste d'URLs.

        Args:
            urls: URLs d'images ordonnées.

        Returns:
            Liste de :class:`Page` indexée.
        """
        pages: list[Page] = []
        for idx, url in enumerate(urls):
            clean = url.strip()
            if not clean:
                continue
            filename = clean.rsplit("/", 1)[-1].split("?", 1)[0] or f"page_{idx:04d}.jpg"
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
        """Vérifie que Void Scans est accessible.

        Teste le domaine principal puis les domaines de fallback.

        Returns:
            ``True`` si un domaine répond correctement.
        """
        for base in self.fallback_domains:
            try:
                response = await self.cf_get(base, timeout=10.0)
                if response.status_code < 500:
                    self.logger.info(
                        "Health check VoidScans OK: {base} (status={s})",
                        base=base,
                        s=response.status_code,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Health check VoidScans échec sur {base}: {err}",
                    base=base,
                    err=exc,
                )
        self.logger.warning("Health check VoidScans KO (tous domaines)")
        return False
