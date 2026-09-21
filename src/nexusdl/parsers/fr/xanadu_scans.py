"""Parseur Xanadu Scans pour NexusDL.

Xanadu Scans (``https://xanadu-scans.com``) est une équipe de scanlation
**francophone** spécialisée dans les mangas, manhwas et manhuas. Le site
utilise le thème WordPress **MangaThemesia** (thème moderne dérivé de
Madara), également utilisé par Asura Scans, Flame Comics, Void Scans,
Luminous Scans et Zenith Scans.

Caractéristiques techniques
---------------------------

* **Moteur** : WordPress MangaThemesia (thème premium pour sites manga).
* **Langue** : Français (``fr``).
* **Protection** : Cloudflare (AS13335, IP ``188.114.97.3`` /
  ``172.67.172.115``) avec challenge modéré.
* **Domaines** :
    - ``xanadu-scans.com`` (principal)
    - ``xanadu-scans.fr`` (miroir francophone)
    - ``xanadu-scans.net`` (miroir secondaire)
* **Structure des URLs** :
    - Catalogue : ``/series/``.
    - Recherche : ``/?s={query}`` (paramètre WordPress standard).
    - Manga : ``/series/{slug}/`` ou ``/manga/{slug}/``.
    - Chapitre : ``/{slug}-chapitre-{num}/`` (format MangaThemesia FR).
* **Sélecteurs MangaThemesia** : ``.listupd .bs``, ``.uta``,
  ``.eplister ul li``, ``.chbox``, ``.ts_reader`` pour les pages.
* **Objet JS ``ts_reader``** : le thème MangaThemesia expose les pages
  dans un objet JavaScript ``ts_reader`` — méthode d'extraction principale
  utilisée par les extensions Tachiyomi/Mihon.
* **Contenu adulte** : certains titres sont classés « mature » ou
  « ecchi » — la classification est détectée automatiquement via les
  genres.

Le parseur combine trois mixins :

1. :class:`~nexusdl.parsers.mixins.mangathemesia.MangaThemesiaMixin` —
   implémentations par défaut pour le thème MangaThemesia.
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — pour le
   contournement Cloudflare automatique (Playwright / FlareSolverr).
3. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — pour le
   rendu Playwright si nécessaire (fallback pour les pages dynamiques).

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.fr.xanadu_scans import XanaduScansParser
    >>>
    >>> parser = XanaduScansParser(config, session)
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
from nexusdl.parsers.mixins.mangathemesia import MangaThemesiaMixin

__all__ = ["XanaduScansParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://xanadu-scans.com"
_FALLBACK_DOMAINS: Final[tuple[str, ...]] = (
    "https://xanadu-scans.com",
    "https://xanadu-scans.fr",
    "https://xanadu-scans.net",
)
_SITE_ID: Final[str] = "xanadu_scans"
_LANGUAGE: Final[Language] = Language.FR
_ADULT: Final[bool] = False

# Regex de parsing des chapitres (format MangaThemesia FR).
# Supporte "Chapitre 47", "Chapter 47", "Ch. 47", "Chap 47".
_CHAPTER_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:chapitre|chapter|chap\.?|ch\.?|episode|ep\.?|ep)\s*"
    r"(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_VOLUME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:tome|volume|vol\.?)\s*(\d+)", re.IGNORECASE
)
_RELATIVE_DATE_RE: Final[re.Pattern[str]] = re.compile(
    r"(\d+)\s*(seconde|minute|heure|jour|semaine|mois|an|"
    r"second|minute|hour|day|week|month|year)s?\s*(ago|avant)?",
    re.IGNORECASE,
)

# Statuts → énumération NexusDL (français + anglais).
_STATUS_MAP: Final[dict[str, MangaStatus]] = {
    "en cours": MangaStatus.ONGOING,
    "en cours de publication": MangaStatus.ONGOING,
    "en cours de parution": MangaStatus.ONGOING,
    "ongoing": MangaStatus.ONGOING,
    "on going": MangaStatus.ONGOING,
    "terminé": MangaStatus.COMPLETED,
    "termine": MangaStatus.COMPLETED,
    "complet": MangaStatus.COMPLETED,
    "completed": MangaStatus.COMPLETED,
    "complete": MangaStatus.COMPLETED,
    "fini": MangaStatus.COMPLETED,
    "en pause": MangaStatus.HIATUS,
    "hiatus": MangaStatus.HIATUS,
    "paused": MangaStatus.HIATUS,
    "annulé": MangaStatus.CANCELLED,
    "annule": MangaStatus.CANCELLED,
    "abandonné": MangaStatus.CANCELLED,
    "abandonne": MangaStatus.CANCELLED,
    "cancelled": MangaStatus.CANCELLED,
    "dropped": MangaStatus.CANCELLED,
}

# Genres adultes (détection de classification).
_ADULT_GENRE_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "hentai",
        "adult",
        "adulte",
        "ecchi",
        "smut",
        "mature",
        "18+",
        "porn",
        "erotic",
        "erotica",
        "érotique",
        "erotique",
        "yaoi",
        "yuri",
        "gender bender",
    }
)

# Marqueurs de contenu suggestif.
_SUGGESTIVE_MARKERS: Final[frozenset[str]] = frozenset(
    {"ecchi", "mature", "harem", "suggestive", "romance", "action"}
)

# ---------------------------------------------------------------------------
# Sélecteurs MangaThemesia spécifiques à Xanadu Scans
# ---------------------------------------------------------------------------

_XANADU_SEARCH_ITEM_SELECTOR: Final[str] = (
    ".listupd .bs, .listupd .bsx, .bs, .bsx, .uta, "
    ".list-item, .manga-item, .manga__item"
)
_XANADU_SEARCH_LINK_SELECTOR: Final[str] = "a"
_XANADU_SEARCH_COVER_SELECTOR: Final[str] = "img"
_XANADU_SEARCH_TITLE_SELECTOR: Final[str] = (
    ".tt, .title, h3, h4, .entry-title, .ntitle"
)

_XANADU_MANGA_TITLE_SELECTOR: Final[str] = (
    "h1.entry-title, h1, .entry-title, .manga-title, .post-title, .ts-breadcrumb li:last-child"
)
_XANADU_MANGA_COVER_SELECTOR: Final[str] = (
    ".thumb img, .manga-cover img, .summary_image img, "
    ".cover img, .thumbook img"
)
_XANADU_MANGA_DESCRIPTION_SELECTOR: Final[str] = (
    ".summary__content, .manga-summary, .description, "
    ".entry-content p, .desc"
)
_XANADU_MANGA_AUTHOR_SELECTOR: Final[str] = (
    ".author, .manga-author, a[href*='/author/'], "
    ".tsinfo .imptdt:contains('Auteur') a"
)
_XANADU_MANGA_ARTIST_SELECTOR: Final[str] = (
    ".artist, .manga-artist, a[href*='/artist/'], "
    ".tsinfo .imptdt:contains('Artiste') a"
)
_XANADU_MANGA_GENRES_SELECTOR: Final[str] = (
    ".genres a, .manga-genres a, a[href*='/genre/'], "
    "a[href*='/tag/'], .mgen a"
)
_XANADU_MANGA_STATUS_SELECTOR: Final[str] = (
    ".status, .manga-status, .post-status, "
    ".tsinfo .imptdt:contains('Statut') i"
)
_XANADU_MANGA_YEAR_SELECTOR: Final[str] = (
    ".year, .manga-year, .post-year, "
    ".tsinfo .imptdt:contains('Année') i"
)

_XANADU_CHAPTER_SELECTOR: Final[str] = (
    ".eplister ul li a, .eplister li a, "
    ".chapter-list a, "
    "a[href*='-chapitre-'], "
    "a[href*='-chapter-'], "
    "a[href*='/chapitre/'], "
    "a[href*='/chapter/']"
)
_XANADU_PAGE_IMG_SELECTOR: Final[str] = (
    ".chapter-page img, .page-image img, "
    ".reading-content img, .reader img, "
    "#readerarea img, .main-reading-area img"
)
_XANADU_PAGES_VAR_NAMES: Final[tuple[str, ...]] = (
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


class XanaduScansParser(
    JsRenderedMixin,
    CloudflareMixin,
    MangaThemesiaMixin,
    BaseParser,
):
    """Parseur Xanadu Scans (MangaThemesia + Cloudflare).

    Combine le rendu Playwright (fallback), le contournement Cloudflare et
    les implémentations MangaThemesia pour fournir une couverture complète
    du site.

    Attributes:
        site_id: Identifiant interne du site.
        language: Langue principale (français).
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

    # --- Configuration MangaThemesia ---
    mt_base_url: ClassVar[str] = _BASE_URL
    mt_search_path: ClassVar[str] = "/"
    mt_search_query_param: ClassVar[str] = "s"
    mt_search_method: ClassVar[str] = "GET"
    mt_series_path: ClassVar[str] = "/series"
    mt_read_path: ClassVar[str] = "/series"

    # --- Sélecteurs MangaThemesia (standards + ajustements Xanadu) ---
    mt_search_item_selector: ClassVar[str] = _XANADU_SEARCH_ITEM_SELECTOR
    mt_search_link_selector: ClassVar[str] = _XANADU_SEARCH_LINK_SELECTOR
    mt_search_cover_selector: ClassVar[str] = _XANADU_SEARCH_COVER_SELECTOR
    mt_search_title_selector: ClassVar[str] = _XANADU_SEARCH_TITLE_SELECTOR

    mt_manga_title_selector: ClassVar[str] = _XANADU_MANGA_TITLE_SELECTOR
    mt_manga_cover_selector: ClassVar[str] = _XANADU_MANGA_COVER_SELECTOR
    mt_manga_description_selector: ClassVar[str] = _XANADU_MANGA_DESCRIPTION_SELECTOR
    mt_manga_author_selector: ClassVar[str] = _XANADU_MANGA_AUTHOR_SELECTOR
    mt_manga_artist_selector: ClassVar[str] = _XANADU_MANGA_ARTIST_SELECTOR
    mt_manga_genres_selector: ClassVar[str] = _XANADU_MANGA_GENRES_SELECTOR
    mt_manga_status_selector: ClassVar[str] = _XANADU_MANGA_STATUS_SELECTOR
    mt_manga_year_selector: ClassVar[str] = _XANADU_MANGA_YEAR_SELECTOR

    mt_chapter_selector: ClassVar[str] = _XANADU_CHAPTER_SELECTOR
    mt_page_img_selector: ClassVar[str] = _XANADU_PAGE_IMG_SELECTOR
    mt_pages_var_names: ClassVar[tuple[str, ...]] = _XANADU_PAGES_VAR_NAMES

    # --- Comportement MangaThemesia ---
    mt_requires_js: ClassVar[bool] = False
    mt_api_pages_endpoint: ClassVar[str | None] = None
    mt_default_rating: ClassVar[ContentRating] = ContentRating.SAFE
    mt_concurrent_chapter_requests: ClassVar[int] = 4
    mt_search_pages_limit: ClassVar[int] = 20

    # --- Configuration Cloudflare ---
    cf_enabled: ClassVar[bool] = True
    cf_requires_js: ClassVar[bool] = False
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
    locale: ClassVar[str] = "fr-FR"
    timezone_id: ClassVar[str] = "Europe/Paris"
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
        """Initialise le parseur Xanadu Scans.

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
    # Helpers internes (MangaThemesia)
    # ------------------------------------------------------------------

    def _mt_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="mangathemesia"``.
        """
        return self.logger

    @staticmethod
    def _mt_clean(value: str | None) -> str:
        """Nettoie une chaîne (strip, espaces multiples, NBSP).

        Args:
            value: Chaîne à nettoyer.

        Returns:
            Chaîne nettoyée (chaîne vide si ``None``).
        """
        if not value:
            return ""
        normalized = (
            value.replace("\xa0", " ")
            .replace("\u200b", "")
            .replace("\u200c", "")
            .replace("\ufeff", "")
        )
        return " ".join(normalized.split()).strip()

    @staticmethod
    def _mt_text(node: Node | None) -> str:
        """Extrait et nettoie le texte d'un nœud.

        Args:
            node: Nœud selectolax ou ``None``.

        Returns:
            Texte nettoyé.
        """
        if node is None:
            return ""
        return XanaduScansParser._mt_clean(
            node.text(deep=True, separator=" ")
        )

    @staticmethod
    def _mt_attr(node: Node | None, name: str) -> str:
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

    def _mt_first(self, tree: HTMLParser, selector: str) -> Node | None:
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

    def _mt_all(self, tree: HTMLParser, selector: str) -> list[Node]:
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

    def _mt_abs(self, url: str, base: str | None = None) -> str:
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

    def _mt_parse_status(self, raw: str | None) -> MangaStatus:
        """Convertit un libellé de statut en énumération NexusDL.

        Args:
            raw: Libellé brut (français ou anglais).

        Returns:
            Statut normalisé (``ONGOING`` par défaut).
        """
        if not raw:
            return MangaStatus.ONGOING
        key = self._mt_clean(raw).lower()
        for needle, status in _STATUS_MAP.items():
            if needle in key:
                return status
        return MangaStatus.ONGOING

    @staticmethod
    def _mt_parse_year(raw: str | None) -> int | None:
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
    def _mt_parse_relative_date(raw: str | None) -> datetime | None:
        """Parse une date relative française ou anglaise.

        Supporte : ``"il y a 3 jours"``, ``"3 jours avant"``,
        ``"3 days ago"``.

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
            # Français
            "seconde": timedelta(seconds=amount),
            "minute": timedelta(minutes=amount),
            "heure": timedelta(hours=amount),
            "jour": timedelta(days=amount),
            "semaine": timedelta(weeks=amount),
            "mois": timedelta(days=amount * 30),
            "an": timedelta(days=amount * 365),
            # Anglais
            "second": timedelta(seconds=amount),
            "hour": timedelta(hours=amount),
            "day": timedelta(days=amount),
            "week": timedelta(weeks=amount),
            "month": timedelta(days=amount * 30),
            "year": timedelta(days=amount * 365),
        }
        return now - table.get(unit, timedelta(0))

    @staticmethod
    def _mt_chapter_number(text: str) -> float | str:
        """Extrait le numéro de chapitre d'un libellé.

        Supporte : ``"Chapitre 47"``, ``"Chapter 47"``, ``"Ch. 47"``.

        Args:
            text: Libellé.

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = XanaduScansParser._mt_clean(text)
        match = _CHAPTER_NUMBER_RE.search(cleaned)
        if not match:
            return cleaned
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return cleaned

    @staticmethod
    def _mt_chapter_volume(text: str) -> int | None:
        """Extrait le numéro de tome d'un libellé.

        Supporte : ``"Tome 3"``, ``"Volume 3"``.

        Args:
            text: Libellé contenant éventuellement un tome.

        Returns:
            Numéro de tome ou ``None``.
        """
        match = _VOLUME_RE.search(text or "")
        return int(match.group(1)) if match else None

    def _mt_detect_rating(self, genres: list[str]) -> ContentRating:
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
            if {"smut", "mature", "erotica", "ecchi", "érotique", "erotique"} & lowered:
                return ContentRating.EROTICA
        if lowered & _SUGGESTIVE_MARKERS:
            return ContentRating.SUGGESTIVE
        return self.mt_default_rating

    # ------------------------------------------------------------------
    # Extraction de l'objet JS ts_reader
    # ------------------------------------------------------------------

    @staticmethod
    def _xanadu_extract_ts_reader(html: str) -> list[str]:
        """Extrait les URLs d'images depuis l'objet JS ``ts_reader``.

        Le thème MangaThemesia expose les pages dans un objet JavaScript
        ``ts_reader`` de la forme
        ``ts_reader.run({"sources":[{"images":["url1","url2",...]}]})``.
        C'est la méthode d'extraction principale utilisée par les
        extensions Tachiyomi/Mihon.

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
                urls = XanaduScansParser._xanadu_walk_ts_reader(parsed)
                if urls:
                    return urls

        # Fallback : extraction par regex de toutes les chaînes.
        urls = re.findall(
            r'"(?:url|image|src)"\s*:\s*"([^"]+)"', html, re.IGNORECASE
        )
        return urls

    @staticmethod
    def _xanadu_walk_ts_reader(payload: Any) -> list[str]:
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

    # ------------------------------------------------------------------
    # Méthodes abstraites de BaseParser
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des mangas sur Xanadu Scans.

        Xanadu Scans utilise ``/?s={query}`` (paramètre WordPress standard).

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.

        Raises:
            ParseError: Si le HTML de résultats est inexploitable.
        """
        self.logger.debug(
            "XanaduScans search: {query} (page {page})",
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
                "Échec recherche XanaduScans pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        return self._mt_parse_search_html(
            rendered.html, base_url=rendered.final_url or url
        )

    def _mt_parse_search_html(
        self,
        html: str,
        *,
        base_url: str,
    ) -> list[SearchResult]:
        """Parse le HTML de résultats de recherche MangaThemesia.

        Args:
            html: HTML rendu.
            base_url: URL de la page (pour résolution relative).

        Returns:
            Liste de :class:`SearchResult`.
        """
        tree = HTMLParser(html)
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for node in self._mt_all(tree, self.mt_search_item_selector):
            link_node: Node | None = None
            if node.tag == "a" and (
                "/series/" in self._mt_attr(node, "href")
                or "/manga/" in self._mt_attr(node, "href")
            ):
                link_node = node
            else:
                for candidate in node.css("a"):
                    href = self._mt_attr(candidate, "href")
                    if "/series/" in href or "/manga/" in href:
                        link_node = candidate
                        break

            if link_node is None:
                continue

            href = self._mt_attr(link_node, "href")
            if not href:
                continue
            abs_url = self._mt_abs(href, base_url)
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            title_node = node.css_first(self.mt_search_title_selector)
            title = self._mt_text(title_node) or self._mt_attr(
                link_node, "title"
            )
            if not title:
                title = (
                    self._mt_attr(link_node, "href")
                    .rstrip("/")
                    .rsplit("/", 1)[-1]
                )
                title = title.replace("-", " ").title()
            if not title:
                continue

            cover_node = node.css_first(self.mt_search_cover_selector)
            cover_src = (
                self._mt_attr(cover_node, "data-src")
                or self._mt_attr(cover_node, "data-lazy-src")
                or self._mt_attr(cover_node, "src")
            )
            cover_url = (
                self._mt_abs(cover_src, base_url) if cover_src else None
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

        self.logger.info(
            "XanaduScans search: {n} résultat(s)", n=len(results)
        )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga Xanadu Scans.

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
            url = f"{self.base_url}{self.mt_series_path}/{url_or_id.strip('/')}/"

        self.logger.debug("XanaduScans get_manga: {url}", url=url)

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
                "Échec get_manga XanaduScans pour {url!r}: {err}",
                url=url,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {url!r}: {exc}"
            ) from exc

        html = rendered.html
        tree = HTMLParser(html)

        title_node = self._mt_first(tree, self.mt_manga_title_selector)
        title = self._mt_text(title_node)
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        cover_node = self._mt_first(tree, self.mt_manga_cover_selector)
        cover_src = (
            self._mt_attr(cover_node, "data-src")
            or self._mt_attr(cover_node, "data-lazy-src")
            or self._mt_attr(cover_node, "src")
        )
        cover_url = self._mt_abs(cover_src, url) if cover_src else None

        description_node = self._mt_first(
            tree, self.mt_manga_description_selector
        )
        description = self._mt_text(description_node) or None

        author_node = self._mt_first(tree, self.mt_manga_author_selector)
        author = self._mt_text(author_node) or None

        artist_node = self._mt_first(tree, self.mt_manga_artist_selector)
        artist = self._mt_text(artist_node) or None

        genre_nodes = self._mt_all(tree, self.mt_manga_genres_selector)
        genres: list[str] = []
        for node in genre_nodes:
            text = self._mt_text(node)
            if text and text not in genres:
                genres.append(text)

        status_node = self._mt_first(tree, self.mt_manga_status_selector)
        status = self._mt_parse_status(self._mt_text(status_node))

        year_node = self._mt_first(tree, self.mt_manga_year_selector)
        year = self._mt_parse_year(self._mt_text(year_node))

        source_id = self._mt_extract_series_slug(url)
        rating = self._mt_detect_rating(genres)

        chapters = self._mt_parse_chapters_from_html(html, base_url=url)

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
            "XanaduScans get_manga OK: {title} ({n} chapitres)",
            title=title,
            n=len(chapters),
        )
        return manga

    @staticmethod
    def _mt_extract_series_slug(url: str) -> str:
        """Extrait un identifiant de série depuis une URL Xanadu Scans.

        Format : ``/series/{slug}`` ou ``/manga/{slug}``.

        Args:
            url: URL de la série.

        Returns:
            Slug nettoyé.
        """
        parts = [p for p in urlparse(url).path.split("/") if p]
        for key in ("series", "manga"):
            if key in parts:
                idx = parts.index(key)
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
            "XanaduScans get_chapters: {title}", title=manga.title
        )

        try:
            rendered = await self.fetch_rendered(
                str(manga.url),
                wait_until="networkidle",
                wait_for_selector=".eplister ul li a, a[href*='-chapitre-'], a[href*='-chapter-']",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_chapters XanaduScans pour {title!r}: {err}",
                title=manga.title,
                err=exc,
            )
            raise ParseError(
                f"get_chapters échoué sur {self.site_id!r} "
                f"pour {manga.title!r}: {exc}"
            ) from exc

        chapters = self._mt_parse_chapters_from_html(
            rendered.html, base_url=rendered.final_url or str(manga.url)
        )
        if not chapters:
            raise ChapterNotFoundError(
                f"Aucun chapitre trouvé pour {manga.url} sur {self.site_id!r}"
            )
        return chapters

    def _mt_parse_chapters_from_html(
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

        for node in self._mt_all(tree, self.mt_chapter_selector):
            href = self._mt_attr(node, "href")
            if not href:
                continue
            # Filtre les liens qui ne sont pas des chapitres.
            if (
                "-chapitre-" not in href
                and "-chapter-" not in href
                and "/chapitre/" not in href
                and "/chapter/" not in href
            ):
                continue
            abs_url = self._mt_abs(href, base_url)
            if abs_url in seen:
                continue
            seen.add(abs_url)

            label = self._mt_text(node) or self._mt_attr(node, "title")
            if not label:
                label = abs_url.rstrip("/").rsplit("/", 1)[-1] or abs_url

            number = self._mt_chapter_number(label)
            volume = self._mt_chapter_volume(label)

            date_text = self._mt_attr(node, "data-date") or None
            published = (
                self._mt_parse_relative_date(date_text)
                if date_text
                else self._mt_parse_relative_date(label)
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
            "XanaduScans chapitres: {n}", n=len(chapters)
        )
        return chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre Xanadu Scans.

        Le site utilise Cloudflare et du lazy-loading : le rendu Playwright
        est nécessaire. Les pages sont extraites en priorité depuis l'objet
        JS ``ts_reader`` (méthode Tachiyomi/Mihon), avec fallback sur les
        sélecteurs CSS MangaThemesia.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "XanaduScans get_pages: {url}", url=str(chapter.url)
        )
        chapter_url = str(chapter.url)

        try:
            rendered = await self.fetch_rendered(
                chapter_url,
                wait_until="networkidle",
                wait_for_selector=".chapter-page img, .reading-content img, #readerarea img",
                timeout=45.0,
                bypass_cloudflare=True,
                block_resources=False,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_pages XanaduScans pour {url!r}: {err}",
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

        # Priorité 1 : objet JS ts_reader (méthode Tachiyomi/Mihon).
        ts_urls = self._xanadu_extract_ts_reader(html)
        if ts_urls:
            for u in ts_urls:
                abs_url = self._mt_abs(u, chapter_url)
                if abs_url not in urls:
                    urls.append(abs_url)

        # Priorité 2 : sélecteur CSS MangaThemesia.
        if not urls:
            for node in self._mt_all(tree, self.mt_page_img_selector):
                src = (
                    self._mt_attr(node, "data-src")
                    or self._mt_attr(node, "data-lazy-src")
                    or self._mt_attr(node, "data-original")
                    or self._mt_attr(node, "src")
                )
                if not src:
                    continue
                abs_url = self._mt_abs(src, chapter_url)
                if abs_url not in urls:
                    urls.append(abs_url)

        # Priorité 3 : variable JS embarquée (fallback).
        if not urls:
            for var_name in self.mt_pages_var_names:
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
            clean = self._mt_abs(clean, chapter_url)
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
            "XanaduScans get_pages: {n} page(s) extraite(s)", n=len(pages)
        )
        return pages

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que Xanadu Scans est accessible.

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
                        "Health check XanaduScans OK: {base} (status={s})",
                        base=base,
                        s=rendered.status,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Health check XanaduScans échec sur {base}: {err}",
                    base=base,
                    err=exc,
                )
        self.logger.warning("Health check XanaduScans KO (tous domaines)")
        return False
