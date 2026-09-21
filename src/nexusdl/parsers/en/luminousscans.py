"""Parseur Luminous Scans pour NexusDL.

Luminous Scans (``https://luminouscomics.org``) est un groupe de scanlation
anglophone de premier plan, spécialisé dans les manhwa/manhua traduits en
anglais. Le site utilise le thème WordPress **MangaStream** (MangaThemesia),
le même moteur que Asura Scans, Flame Comics, Void Scans et Rizz Comic.

Caractéristiques techniques
---------------------------

* **Moteur** : WordPress MangaStream (thème custom dérivé de MangaThemesia).
* **Langue** : Anglais (``en``).
* **Protection** : Cloudflare (AS13335, IP ``2606:4700:3035::ac43:c0d1``).
  Le site utilise du lazy-loading d'images et un filtrage d'images
  promotionnelles en fin de chapitre.
* **Domaines successifs** :
    - ``luminousscans.com`` (origine, redirige désormais)[reference:0]
    - ``luminousscans.net`` (miroir intermédiaire)[reference:1]
    - ``luminousscans.gg`` (miroir historique)[reference:2]
    - ``luminouscomics.org`` (domaine actuel)[reference:3]
* **Structure des URLs** :
    - Catalogue : ``/series/``
    - Manga : ``/series/{slug}/``[reference:4]
    - Chapitre : ``/{slug}-chapter-{num}/``[reference:5]
* **Junk images** : Luminous Scans insère des images promotionnelles en fin
  de chapitre (promo pour d'autres séries, Discord, Patreon…). Le parseur
  les filtre via une heuristique basée sur les dimensions et les motifs
  d'URL.
* **Sélecteurs MangaStream** : ``.listupd .bs``, ``.uta``,
  ``.eplister ul li``, ``.chbox``, ``.ts_reader`` pour les pages.

Le parseur combine trois mixins :

1. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — rendu
   Playwright complet (Cloudflare + lazy-loading).
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — bypass
   Cloudflare automatique (Playwright / FlareSolverr).
3. Implémentations MangaStream **inline** — sélecteurs et logique adaptés
   au thème MangaStream (pas de mixin dédié dans le projet).

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.en.luminousscans import LuminousScansParser
    >>>
    >>> parser = LuminousScansParser(config, session)
    >>> results = await parser.search("solo leveling")
    >>> manga = await parser.get_manga(results[0].url)
    >>> chapters = await parser.get_chapters(manga)
    >>> pages = await parser.get_pages(chapters[0])
"""

from __future__ import annotations

import json as _json
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

__all__ = ["LuminousScansParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://luminouscomics.org"
_FALLBACK_DOMAINS: Final[tuple[str, ...]] = (
    "https://luminouscomics.org",
    "https://luminousscans.gg",
    "https://luminousscans.net",
    "https://luminousscans.com",
)
_SITE_ID: Final[str] = "luminousscans"
_LANGUAGE: Final[Language] = Language.EN
_ADULT: Final[bool] = False

# Regex de parsing des chapitres (format MangaStream : "Chapter 176").
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

# Motifs d'URL à filtrer (images promotionnelles / junk).
_JUNK_URL_MARKERS: Final[tuple[str, ...]] = (
    "discord",
    "patreon",
    "ko-fi",
    "buymeacoffee",
    "promo",
    "banner",
    "advert",
    "sponsor",
    "logo",
    "recruit",
    "donate",
    "social",
    "twitter",
    "instagram",
    "facebook",
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
# Sélecteurs MangaStream (Luminous Scans)
# ---------------------------------------------------------------------------

_LS_SEARCH_ITEM_SELECTOR: Final[str] = (
    ".listupd .bs, .listupd .bsx, .bs, .bsx, .uta, "
    ".list-item, .manga-item"
)
_LS_SEARCH_LINK_SELECTOR: Final[str] = "a"
_LS_SEARCH_COVER_SELECTOR: Final[str] = "img"
_LS_SEARCH_TITLE_SELECTOR: Final[str] = (
    ".tt, .title, h3, h4, .entry-title, .ntitle"
)

_LS_MANGA_TITLE_SELECTOR: Final[str] = (
    "h1.entry-title, h1, .entry-title, .manga-title, .post-title"
)
_LS_MANGA_COVER_SELECTOR: Final[str] = (
    ".thumb img, .manga-cover img, .summary_image img, "
    ".cover img, .thumbook img"
)
_LS_MANGA_DESCRIPTION_SELECTOR: Final[str] = (
    ".summary__content, .manga-summary, .description, "
    ".entry-content p, .desc"
)
_LS_MANGA_AUTHOR_SELECTOR: Final[str] = (
    ".author, .manga-author, a[href*='/author/'], "
    ".tsinfo .imptdt:contains('Author') a"
)
_LS_MANGA_ARTIST_SELECTOR: Final[str] = (
    ".artist, .manga-artist, a[href*='/artist/'], "
    ".tsinfo .imptdt:contains('Artist') a"
)
_LS_MANGA_GENRES_SELECTOR: Final[str] = (
    ".genres a, .manga-genres a, a[href*='/genre/'], "
    "a[href*='/tag/'], .mgen a"
)
_LS_MANGA_STATUS_SELECTOR: Final[str] = (
    ".status, .manga-status, .post-status, "
    ".tsinfo .imptdt:contains('Status') i"
)
_LS_MANGA_YEAR_SELECTOR: Final[str] = (
    ".year, .manga-year, .post-year, "
    ".tsinfo .imptdt:contains('Year') i"
)

_LS_CHAPTER_SELECTOR: Final[str] = (
    ".eplister ul li a, .eplister li a, "
    ".chapter-list a, "
    "a[href*='-chapter-'], "
    "a[href*='/chapter/']"
)
_LS_PAGE_IMG_SELECTOR: Final[str] = (
    ".chapter-page img, .page-image img, "
    ".reading-content img, .reader img, "
    "#readerarea img, .main-reading-area img"
)
_LS_PAGES_VAR_NAMES: Final[tuple[str, ...]] = (
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


class LuminousScansParser(
    JsRenderedMixin,
    CloudflareMixin,
    BaseParser,
):
    """Parseur Luminous Scans (MangaStream + Cloudflare).

    Combine le rendu Playwright (obligatoire — Cloudflare + lazy-loading),
    le contournement Cloudflare et des implémentations MangaStream inline
    pour fournir une couverture complète du site.

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

    # --- Chemins Luminous Scans ---
    ls_search_path: ClassVar[str] = "/"
    ls_series_path: ClassVar[str] = "/series"

    # --- Sélecteurs Luminous Scans ---
    ls_search_item_selector: ClassVar[str] = _LS_SEARCH_ITEM_SELECTOR
    ls_search_link_selector: ClassVar[str] = _LS_SEARCH_LINK_SELECTOR
    ls_search_cover_selector: ClassVar[str] = _LS_SEARCH_COVER_SELECTOR
    ls_search_title_selector: ClassVar[str] = _LS_SEARCH_TITLE_SELECTOR

    ls_manga_title_selector: ClassVar[str] = _LS_MANGA_TITLE_SELECTOR
    ls_manga_cover_selector: ClassVar[str] = _LS_MANGA_COVER_SELECTOR
    ls_manga_description_selector: ClassVar[str] = _LS_MANGA_DESCRIPTION_SELECTOR
    ls_manga_author_selector: ClassVar[str] = _LS_MANGA_AUTHOR_SELECTOR
    ls_manga_artist_selector: ClassVar[str] = _LS_MANGA_ARTIST_SELECTOR
    ls_manga_genres_selector: ClassVar[str] = _LS_MANGA_GENRES_SELECTOR
    ls_manga_status_selector: ClassVar[str] = _LS_MANGA_STATUS_SELECTOR
    ls_manga_year_selector: ClassVar[str] = _LS_MANGA_YEAR_SELECTOR

    ls_chapter_selector: ClassVar[str] = _LS_CHAPTER_SELECTOR
    ls_page_img_selector: ClassVar[str] = _LS_PAGE_IMG_SELECTOR
    ls_pages_var_names: ClassVar[tuple[str, ...]] = _LS_PAGES_VAR_NAMES

    # --- Comportement ---
    ls_requires_js: ClassVar[bool] = True
    ls_default_rating: ClassVar[ContentRating] = ContentRating.SAFE
    ls_filter_promo_images: ClassVar[bool] = True
    ls_junk_probe_depth: ClassVar[int] = 3

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
        """Initialise le parseur Luminous Scans.

        Args:
            config: Configuration du site (:class:`SiteConfig`).
            session: Session HTTP async du projet (:class:`HttpSession`).
            playwright_pool: Pool Playwright partagé (requis — le site est
                protégé par Cloudflare).
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
        """Injecte le header ``Referer`` requis par le CDN d'images."""
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

    def _ls_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="luminousscans"``.
        """
        return self.logger

    @staticmethod
    def _ls_clean(value: str | None) -> str:
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
    def _ls_text(node: Node | None) -> str:
        """Extrait et nettoie le texte d'un nœud.

        Args:
            node: Nœud selectolax ou ``None``.

        Returns:
            Texte nettoyé.
        """
        if node is None:
            return ""
        return LuminousScansParser._ls_clean(
            node.text(deep=True, separator=" ")
        )

    @staticmethod
    def _ls_attr(node: Node | None, name: str) -> str:
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

    def _ls_first(self, tree: HTMLParser, selector: str) -> Node | None:
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

    def _ls_all(self, tree: HTMLParser, selector: str) -> list[Node]:
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

    def _ls_abs(self, url: str, base: str | None = None) -> str:
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

    def _ls_parse_status(self, raw: str | None) -> MangaStatus:
        """Convertit un libellé de statut MangaStream en énumération.

        Args:
            raw: Libellé brut.

        Returns:
            Statut normalisé (``ONGOING`` par défaut).
        """
        if not raw:
            return MangaStatus.ONGOING
        key = self._ls_clean(raw).lower()
        for needle, status in _STATUS_MAP.items():
            if needle in key:
                return status
        return MangaStatus.ONGOING

    @staticmethod
    def _ls_parse_year(raw: str | None) -> int | None:
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
    def _ls_parse_relative_date(raw: str | None) -> datetime | None:
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
    def _ls_chapter_number(text: str) -> float | str:
        """Extrait le numéro de chapitre d'un libellé.

        Args:
            text: Libellé (ex. ``"Chapter 176"``).

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = LuminousScansParser._ls_clean(text)
        match = _CHAPTER_NUMBER_RE.search(cleaned)
        if not match:
            return cleaned
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return cleaned

    @staticmethod
    def _ls_chapter_volume(text: str) -> int | None:
        """Extrait le numéro de tome d'un libellé.

        Args:
            text: Libellé contenant éventuellement ``Volume X``.

        Returns:
            Numéro de tome ou ``None``.
        """
        match = _VOLUME_RE.search(text or "")
        return int(match.group(1)) if match else None

    def _ls_detect_rating(self, genres: list[str]) -> ContentRating:
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
        return self.ls_default_rating

    # ------------------------------------------------------------------
    # Filtrage des images promotionnelles
    # ------------------------------------------------------------------

    def _ls_is_junk_url(self, url: str) -> bool:
        """Détecte une image promotionnelle via des motifs d'URL.

        Luminous Scans insère des images promotionnelles (Discord, Patreon,
        réseaux sociaux…) en fin de chapitre. Cette méthode filtre les URLs
        contenant des mots-clés caractéristiques.

        Args:
            url: URL de l'image.

        Returns:
            ``True`` si l'URL semble être une image junk.
        """
        lowered = url.lower()
        return any(marker in lowered for marker in _JUNK_URL_MARKERS)

    def _ls_filter_junk_urls(self, urls: list[str]) -> list[str]:
        """Filtre les URLs promotionnelles d'une liste.

        Args:
            urls: Liste d'URLs candidates.

        Returns:
            Liste filtrée.
        """
        if not self.ls_filter_promo_images:
            return urls
        filtered = [u for u in urls if not self._ls_is_junk_url(u)]
        removed = len(urls) - len(filtered)
        if removed > 0:
            self.logger.debug(
                "LuminousScans: {n} image(s) promo filtrée(s)", n=removed
            )
        return filtered

    # ------------------------------------------------------------------
    # Méthodes abstraites de BaseParser
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des mangas sur Luminous Scans.

        Luminous Scans utilise ``/?s={query}`` (paramètre WordPress standard).

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.

        Raises:
            ParseError: Si le HTML de résultats est inexploitable.
        """
        self.logger.debug(
            "LuminousScans search: {query} (page {page})",
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
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec recherche LuminousScans pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        return self._ls_parse_search_html(
            rendered.html, base_url=rendered.final_url or url
        )

    def _ls_parse_search_html(
        self,
        html: str,
        *,
        base_url: str,
    ) -> list[SearchResult]:
        """Parse le HTML de résultats de recherche MangaStream.

        Args:
            html: HTML rendu.
            base_url: URL de la page (pour résolution relative).

        Returns:
            Liste de :class:`SearchResult`.
        """
        tree = HTMLParser(html)
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for node in self._ls_all(tree, self.ls_search_item_selector):
            link_node: Node | None = None
            if node.tag == "a" and "/series/" in self._ls_attr(node, "href"):
                link_node = node
            else:
                for candidate in node.css("a"):
                    if "/series/" in self._ls_attr(candidate, "href"):
                        link_node = candidate
                        break

            if link_node is None:
                continue

            href = self._ls_attr(link_node, "href")
            if not href:
                continue
            abs_url = self._ls_abs(href, base_url)
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            title_node = node.css_first(self.ls_search_title_selector)
            title = self._ls_text(title_node) or self._ls_attr(
                link_node, "title"
            )
            if not title:
                title = self._ls_attr(link_node, "href").rstrip("/").rsplit("/", 1)[-1]
                title = title.replace("-", " ").title()
            if not title:
                continue

            cover_node = node.css_first(self.ls_search_cover_selector)
            cover_src = (
                self._ls_attr(cover_node, "data-src")
                or self._ls_attr(cover_node, "data-lazy-src")
                or self._ls_attr(cover_node, "src")
            )
            cover_url = self._ls_abs(cover_src, base_url) if cover_src else None

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
            "LuminousScans search: {n} résultat(s)", n=len(results)
        )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga Luminous Scans.

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
            url = f"{self.base_url}{self.ls_series_path}/{url_or_id.strip('/')}/"

        self.logger.debug("LuminousScans get_manga: {url}", url=url)

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
                "Échec get_manga LuminousScans pour {url!r}: {err}",
                url=url,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {url!r}: {exc}"
            ) from exc

        html = rendered.html
        tree = HTMLParser(html)

        title_node = self._ls_first(tree, self.ls_manga_title_selector)
        title = self._ls_text(title_node)
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        cover_node = self._ls_first(tree, self.ls_manga_cover_selector)
        cover_src = (
            self._ls_attr(cover_node, "data-src")
            or self._ls_attr(cover_node, "data-lazy-src")
            or self._ls_attr(cover_node, "src")
        )
        cover_url = self._ls_abs(cover_src, url) if cover_src else None

        description_node = self._ls_first(
            tree, self.ls_manga_description_selector
        )
        description = self._ls_text(description_node) or None

        author_node = self._ls_first(tree, self.ls_manga_author_selector)
        author = self._ls_text(author_node) or None

        artist_node = self._ls_first(tree, self.ls_manga_artist_selector)
        artist = self._ls_text(artist_node) or None

        genre_nodes = self._ls_all(tree, self.ls_manga_genres_selector)
        genres: list[str] = []
        for node in genre_nodes:
            text = self._ls_text(node)
            if text and text not in genres:
                genres.append(text)

        status_node = self._ls_first(tree, self.ls_manga_status_selector)
        status = self._ls_parse_status(self._ls_text(status_node))

        year_node = self._ls_first(tree, self.ls_manga_year_selector)
        year = self._ls_parse_year(self._ls_text(year_node))

        source_id = self._ls_extract_series_slug(url)
        rating = self._ls_detect_rating(genres)

        chapters = self._ls_parse_chapters_from_html(html, base_url=url)

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
            "LuminousScans get_manga OK: {title} ({n} chapitres)",
            title=title,
            n=len(chapters),
        )
        return manga

    @staticmethod
    def _ls_extract_series_slug(url: str) -> str:
        """Extrait un identifiant de série depuis une URL Luminous Scans.

        Format : ``/series/{slug}/`` → retourne ``{slug}``.

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

        Args:
            manga: Manga dont on veut les chapitres.

        Returns:
            Liste de :class:`Chapter` triés par numéro croissant.

        Raises:
            ChapterNotFoundError: Si aucun chapitre n'est trouvé.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "LuminousScans get_chapters: {title}", title=manga.title
        )

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
                "Échec get_chapters LuminousScans pour {title!r}: {err}",
                title=manga.title,
                err=exc,
            )
            raise ParseError(
                f"get_chapters échoué sur {self.site_id!r} "
                f"pour {manga.title!r}: {exc}"
            ) from exc

        chapters = self._ls_parse_chapters_from_html(
            rendered.html, base_url=rendered.final_url or str(manga.url)
        )
        if not chapters:
            raise ChapterNotFoundError(
                f"Aucun chapitre trouvé pour {manga.url} sur {self.site_id!r}"
            )
        return chapters

    def _ls_parse_chapters_from_html(
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

        for node in self._ls_all(tree, self.ls_chapter_selector):
            href = self._ls_attr(node, "href")
            if not href:
                continue
            # Filtre les liens qui ne sont pas des chapitres.
            if "-chapter-" not in href and "/chapter/" not in href:
                continue
            abs_url = self._ls_abs(href, base_url)
            if abs_url in seen:
                continue
            seen.add(abs_url)

            label = self._ls_text(node) or self._ls_attr(node, "title")
            if not label:
                label = abs_url.rstrip("/").rsplit("/", 1)[-1] or abs_url

            number = self._ls_chapter_number(label)
            volume = self._ls_chapter_volume(label)

            date_text = self._ls_attr(node, "data-date") or None
            published = (
                self._ls_parse_relative_date(date_text)
                if date_text
                else self._ls_parse_relative_date(label)
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
            "LuminousScans chapitres: {n}", n=len(chapters)
        )
        return chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre Luminous Scans.

        Le site est protégé par Cloudflare et utilise du lazy-loading :
        le rendu Playwright est nécessaire. Les images promotionnelles
        (Discord, Patreon…) sont filtrées automatiquement.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page` (promos filtrées).

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "LuminousScans get_pages: {url}", url=str(chapter.url)
        )
        chapter_url = str(chapter.url)

        try:
            rendered = await self.fetch_rendered(
                chapter_url,
                wait_until="networkidle",
                wait_for_selector=".chapter-page img, .reading-content img",
                timeout=45.0,
                bypass_cloudflare=True,
                block_resources=False,  # Les images sont nécessaires
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_pages LuminousScans pour {url!r}: {err}",
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

        # Priorité 1 : sélecteur CSS MangaStream.
        for node in self._ls_all(tree, self.ls_page_img_selector):
            src = (
                self._ls_attr(node, "data-src")
                or self._ls_attr(node, "data-lazy-src")
                or self._ls_attr(node, "data-original")
                or self._ls_attr(node, "src")
            )
            if not src:
                continue
            abs_url = self._ls_abs(src, chapter_url)
            if abs_url not in urls:
                urls.append(abs_url)

        # Priorité 2 : variable JS embarquée (ts_reader / pages).
        if not urls:
            for var_name in self.ls_pages_var_names:
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

        # Filtre les images promotionnelles.
        urls = self._ls_filter_junk_urls(urls)

        pages: list[Page] = []
        for idx, url in enumerate(urls):
            clean = url.strip()
            if not clean:
                continue
            clean = self._ls_abs(clean, chapter_url)
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
            "LuminousScans get_pages: {n} page(s) extraite(s)", n=len(pages)
        )
        return pages

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que Luminous Scans est accessible.

        Teste le domaine principal puis les domaines de fallback (le site a
        changé plusieurs fois de domaine).

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
                        "Health check LuminousScans OK: {base} (status={s})",
                        base=base,
                        s=rendered.status,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Health check LuminousScans échec sur {base}: {err}",
                    base=base,
                    err=exc,
                )
        self.logger.warning("Health check LuminousScans KO (tous domaines)")
        return False
