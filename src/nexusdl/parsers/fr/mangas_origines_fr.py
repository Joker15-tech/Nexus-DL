"""Parseur Mangas Origines FR pour NexusDL.

Mangas Origines (``https://mangas-origines.fr``) est l'un des plus anciens
et des plus importants sites de scanlation **francophone**, en activité
depuis 2010. Il utilise le CMS **FoolSlide** — un moteur PHP historique
très répandu dans la communauté scan FR avant l'avènement de WordPress/
Madara.

⚠️ **Note importante** : ce parser est distinct de
:class:`~nexusdl.parsers.fr.mangas_origines.MangasOriginesParser` qui cible
l'instance historique ``mangas-origines.com``. Les deux instances sont
gérées par la **même équipe** mais ont des catalogues partiellement
distincts (l'instance ``.fr`` est la plus récente et la plus active).

Caractéristiques techniques
---------------------------

* **Moteur** : **FoolSlide** (CMS PHP historique, structure DOM stable).
* **Langue** : Français (``fr``).
* **Protection** : Cloudflare (AS13335, IP ``104.21.16.85`` /
  ``172.67.172.115``) avec challenge modéré. Le site impose également un
  **Referer strict** sur les images (hotlink bloqué).
* **Domaines** :
    - ``mangas-origines.fr`` (principal, instance moderne)
    - ``mangasorigines.fr`` (miroir sans tiret)
    - ``mangas-origines.com`` (instance historique, fallback)
* **Structure des URLs** (format FoolSlide) :
    - Catalogue : ``/manga/``.
    - Recherche : ``/search/?q={query}`` (paramètre ``q`` de FoolSlide).
    - Manga : ``/manga/{slug}.html`` (note : extension ``.html``).
    - Chapitre : ``/lecture-en-ligne/{manga-slug}/{chapter-slug}.html``.
    - Lecture AJAX : ``/ajax/lecture.php?id={chapter_id}&page={n}``.
* **Sélecteurs FoolSlide** : ``.manga-list`` pour le catalogue,
  ``.chapter-list a`` pour les chapitres,
  ``.page-image`` pour les pages, ``.reader-content`` pour le reader.
* **Objet JS ``pages``** : les pages sont exposées via une variable JS
  ``pages = [...]`` dans la balise ``<script>`` — méthode principale
  d'extraction.
* **Reader AJAX** : FoolSlide supporte l'endpoint ``/ajax/lecture.php``
  pour charger les pages une par une (utilisé par le reader JS).

Le parseur combine trois mixins :

1. :class:`~nexusdl.parsers.mixins.foolslide.FoolSlideMixin` —
   implémentations par défaut pour le CMS FoolSlide.
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — pour le
   contournement Cloudflare automatique (Playwright / FlareSolverr).
3. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — pour le
   rendu Playwright si nécessaire (fallback pour les pages dynamiques).

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.fr.mangas_origines_fr import MangasOriginesFrParser
    >>>
    >>> parser = MangasOriginesFrParser(config, session)
    >>> results = await parser.search("one piece")
    >>> manga = await parser.get_manga(results[0].url)
    >>> chapters = await parser.get_chapters(manga)
    >>> pages = await parser.get_pages(chapters[0])
"""

from __future__ import annotations

import json as _json
import re
from datetime import datetime, timedelta, timezone
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
from nexusdl.parsers.mixins.cloudflare import CloudflareMixin
from nexusdl.parsers.mixins.foolslide import FoolSlideMixin
from nexusdl.parsers.mixins.js_rendered import JsRenderedMixin

__all__ = ["MangasOriginesFrParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://mangas-origines.fr"
_FALLBACK_DOMAINS: Final[tuple[str, ...]] = (
    "https://mangas-origines.fr",
    "https://mangasorigines.fr",
    "https://mangas-origines.com",
)
_SITE_ID: Final[str] = "mangas_origines_fr"
_LANGUAGE: Final[Language] = Language.FR
_ADULT: Final[bool] = False

# Regex de parsing des chapitres (format FoolSlide FR).
# Supporte "Chapitre 47", "Chapter 47", "Ch. 47", "Scan 47".
_CHAPTER_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:chapitre|chapter|chap\.?|ch\.?|scan|episode|ep\.?|ep)\s*"
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

# Extraction de l'ID de chapitre (utilisé pour l'endpoint AJAX).
_CHAPTER_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"/(\d+)/[^/]*\.html?$"
)
_CHAPTER_ID_QUERY_RE: Final[re.Pattern[str]] = re.compile(
    r"id=(\d+)"
)

# Extraction des variables JS FoolSlide.
_PAGES_JS_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:var|let|const)\s+pages\s*=\s*(\[.*?\])\s*;", re.DOTALL
)
_PAGE_URL_JS_RE: Final[re.Pattern[str]] = re.compile(
    r'"(?:url|image|src|page)"\s*:\s*"([^"]+)"', re.IGNORECASE
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
# Sélecteurs FoolSlide spécifiques à Mangas Origines FR
# ---------------------------------------------------------------------------

_MO_SEARCH_ITEM_SELECTOR: Final[str] = (
    "div.manga-list div.manga-item, "
    "div.manga-list a, "
    ".list-manga a, "
    "div.manga-item, "
    "div.group"
)
_MO_SEARCH_LINK_SELECTOR: Final[str] = "a"
_MO_SEARCH_COVER_SELECTOR: Final[str] = "img"
_MO_SEARCH_TITLE_SELECTOR: Final[str] = (
    ".manga-title, .title, h3, h4, .manga-name"
)

_MO_MANGA_TITLE_SELECTOR: Final[str] = (
    "h1.manga-title, h1.title, h1, .manga-title, .title"
)
_MO_MANGA_COVER_SELECTOR: Final[str] = (
    ".manga-cover img, .cover img, .thumbnail img, "
    "img.manga-cover, img.cover"
)
_MO_MANGA_DESCRIPTION_SELECTOR: Final[str] = (
    ".manga-summary, .summary, .description, "
    ".synopsis, .manga-description"
)
_MO_MANGA_AUTHOR_SELECTOR: Final[str] = (
    ".manga-author, .author, a[href*='/author/'], "
    "a[href*='/auteur/']"
)
_MO_MANGA_ARTIST_SELECTOR: Final[str] = (
    ".manga-artist, .artist, a[href*='/artist/'], "
    "a[href*='/artiste/']"
)
_MO_MANGA_GENRES_SELECTOR: Final[str] = (
    ".manga-genres a, .genres a, "
    "a[href*='/genre/'], a[href*='/tag/']"
)
_MO_MANGA_STATUS_SELECTOR: Final[str] = (
    ".manga-status, .status"
)

_MO_CHAPTER_SELECTOR: Final[str] = (
    ".chapter-list a, "
    "ul.chapter-list li a, "
    "div.chapter-list a, "
    "a[href*='/lecture-en-ligne/'], "
    "a[href*='/manga/']"
)
_MO_PAGE_IMG_SELECTOR: Final[str] = (
    "img.page-image, "
    ".reader-content img, "
    ".page-content img, "
    "div.page-image img, "
    "img[src*='/manga/']"
)
_MO_PAGES_VAR_NAMES: Final[tuple[str, ...]] = (
    "pages",
    "page_urls",
    "pageUrls",
    "images",
)


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


class MangasOriginesFrParser(
    JsRenderedMixin,
    CloudflareMixin,
    FoolSlideMixin,
    BaseParser,
):
    """Parseur Mangas Origines FR (FoolSlide + Cloudflare).

    Combine le rendu Playwright (fallback), le contournement Cloudflare et
    les implémentations FoolSlide pour fournir une couverture complète du
    site.

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

    # --- Configuration FoolSlide ---
    fools_base_url: ClassVar[str | None] = _BASE_URL
    fools_search_path: ClassVar[str] = "/search/"
    fools_search_query_param: ClassVar[str] = "q"
    fools_search_method: ClassVar[str] = "GET"
    fools_series_path: ClassVar[str] = "/manga"
    fools_read_path: ClassVar[str] = "/lecture-en-ligne"

    # --- Sélecteurs FoolSlide (standards + ajustements Mangas Origines) ---
    fools_search_item_selector: ClassVar[str] = _MO_SEARCH_ITEM_SELECTOR
    fools_search_link_selector: ClassVar[str] = _MO_SEARCH_LINK_SELECTOR
    fools_search_cover_selector: ClassVar[str] = _MO_SEARCH_COVER_SELECTOR
    fools_search_title_selector: ClassVar[str] = _MO_SEARCH_TITLE_SELECTOR

    fools_manga_title_selector: ClassVar[str] = _MO_MANGA_TITLE_SELECTOR
    fools_manga_cover_selector: ClassVar[str] = _MO_MANGA_COVER_SELECTOR
    fools_manga_description_selector: ClassVar[str] = _MO_MANGA_DESCRIPTION_SELECTOR
    fools_manga_author_selector: ClassVar[str] = _MO_MANGA_AUTHOR_SELECTOR
    fools_manga_artist_selector: ClassVar[str] = _MO_MANGA_ARTIST_SELECTOR
    fools_manga_genres_selector: ClassVar[str] = _MO_MANGA_GENRES_SELECTOR
    fools_manga_status_selector: ClassVar[str] = _MO_MANGA_STATUS_SELECTOR

    fools_chapter_selector: ClassVar[str] = _MO_CHAPTER_SELECTOR
    fools_page_img_selector: ClassVar[str] = _MO_PAGE_IMG_SELECTOR
    fools_pages_var_names: ClassVar[tuple[str, ...]] = _MO_PAGES_VAR_NAMES

    # --- Comportement FoolSlide ---
    fools_requires_js: ClassVar[bool] = False
    fools_api_pages_endpoint: ClassVar[str | None] = None
    fools_default_rating: ClassVar[ContentRating] = ContentRating.SAFE
    fools_concurrent_chapter_requests: ClassVar[int] = 4
    fools_search_pages_limit: ClassVar[int] = 20

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

    # --- Endpoint AJAX FoolSlide ---
    mo_ajax_endpoint: ClassVar[str] = "/ajax/lecture.php"

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
        """Initialise le parseur Mangas Origines FR.

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
    # Helpers internes (FoolSlide)
    # ------------------------------------------------------------------

    def _fools_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="foolslide"``.
        """
        return self.logger

    @staticmethod
    def _fools_clean(value: str | None) -> str:
        """Nettoie une chaîne (strip, espaces multiples, NBSP, zero-width).

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
    def _fools_text(node: Node | None) -> str:
        """Extrait et nettoie le texte d'un nœud.

        Args:
            node: Nœud selectolax ou ``None``.

        Returns:
            Texte nettoyé.
        """
        if node is None:
            return ""
        return MangasOriginesFrParser._fools_clean(
            node.text(deep=True, separator=" ")
        )

    @staticmethod
    def _fools_attr(node: Node | None, name: str) -> str:
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

    def _fools_first(self, tree: HTMLParser, selector: str) -> Node | None:
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

    def _fools_all(self, tree: HTMLParser, selector: str) -> list[Node]:
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

    def _fools_abs(self, url: str, base: str | None = None) -> str:
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

    def _fools_parse_status(self, raw: str | None) -> MangaStatus:
        """Convertit un libellé de statut en énumération NexusDL.

        Args:
            raw: Libellé brut (français ou anglais).

        Returns:
            Statut normalisé (``ONGOING`` par défaut).
        """
        if not raw:
            return MangaStatus.ONGOING
        key = self._fools_clean(raw).lower()
        for needle, status in _STATUS_MAP.items():
            if needle in key:
                return status
        return MangaStatus.ONGOING

    @staticmethod
    def _fools_parse_year(raw: str | None) -> int | None:
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
    def _fools_parse_relative_date(raw: str | None) -> datetime | None:
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
    def _fools_chapter_number(text: str) -> float | str:
        """Extrait le numéro de chapitre d'un libellé.

        Supporte : ``"Chapitre 47"``, ``"Scan 47"``, ``"Ch. 47"``.

        Args:
            text: Libellé.

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = MangasOriginesFrParser._fools_clean(text)
        match = _CHAPTER_NUMBER_RE.search(cleaned)
        if not match:
            return cleaned
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return cleaned

    @staticmethod
    def _fools_chapter_volume(text: str) -> int | None:
        """Extrait le numéro de tome d'un libellé.

        Supporte : ``"Tome 3"``, ``"Volume 3"``.

        Args:
            text: Libellé contenant éventuellement un tome.

        Returns:
            Numéro de tome ou ``None``.
        """
        match = _VOLUME_RE.search(text or "")
        return int(match.group(1)) if match else None

    def _fools_detect_rating(self, genres: list[str]) -> ContentRating:
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
        return self.fools_default_rating

    # ------------------------------------------------------------------
    # Helpers Mangas Origines spécifiques
    # ------------------------------------------------------------------

    @staticmethod
    def _mo_extract_chapter_id(url: str) -> str | None:
        """Extrait l'ID de chapitre depuis une URL Mangas Origines.

        Deux formats possibles :
            - ``/lecture-en-ligne/{manga}/{id}/{slug}.html``
            - ``/ajax/lecture.php?id={id}&page={n}``

        Args:
            url: URL du chapitre.

        Returns:
            ID de chapitre ou ``None``.
        """
        # Format query string (?id=NNN).
        match = _CHAPTER_ID_QUERY_RE.search(url)
        if match:
            return match.group(1)

        # Format path avec extension .html.
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        # Pattern : /lecture-en-ligne/{slug}/{id}/{slug}.html
        for i, part in enumerate(parts):
            if part.isdigit() and i > 0:
                return part

        # Fallback : regex directe.
        match = _CHAPTER_ID_RE.search(parsed.path)
        if match:
            return match.group(1)

        return None

    @staticmethod
    def _mo_extract_pages_from_js(html: str) -> list[str]:
        """Extrait les URLs des pages depuis la variable JS FoolSlide.

        FoolSlide expose les pages dans une variable JS
        ``pages = [...]`` dans la balise ``<script>``.

        Args:
            html: HTML de la page chapitre.

        Returns:
            Liste ordonnée d'URLs d'images (vide si introuvable).
        """
        match = _PAGES_JS_RE.search(html)
        if not match:
            return []

        raw = match.group(1)
        urls: list[str] = []

        # Tentative 1 : décodage JSON direct.
        try:
            parsed = _json.loads(raw)
        except ValueError:
            parsed = None

        if isinstance(parsed, list):
            for entry in parsed:
                if isinstance(entry, str):
                    urls.append(entry)
                elif isinstance(entry, dict):
                    for key in ("url", "image", "src", "page"):
                        value = entry.get(key)
                        if isinstance(value, str):
                            urls.append(value)
                            break
            return urls

        # Tentative 2 : extraction par regex de toutes les chaînes.
        return _PAGE_URL_JS_RE.findall(raw)

    # ------------------------------------------------------------------
    # Méthodes abstraites de BaseParser
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des mangas sur Mangas Origines FR.

        Mangas Origines utilise ``/search/?q={query}`` (paramètre FoolSlide).

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.

        Raises:
            ParseError: Si le HTML de résultats est inexploitable.
        """
        self.logger.debug(
            "MangasOriginesFr search: {query} (page {page})",
            query=query,
            page=page,
        )

        base = self.base_url.rstrip("/")
        url = (
            f"{base}{self.fools_search_path}"
            f"?{self.fools_search_query_param}={quote_plus(query)}"
        )
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
                "Échec recherche MangasOriginesFr pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        return self._fools_parse_search_html(
            rendered.html, base_url=rendered.final_url or url
        )

    def _fools_parse_search_html(
        self,
        html: str,
        *,
        base_url: str,
    ) -> list[SearchResult]:
        """Parse le HTML de résultats de recherche FoolSlide.

        Args:
            html: HTML rendu.
            base_url: URL de la page (pour résolution relative).

        Returns:
            Liste de :class:`SearchResult`.
        """
        tree = HTMLParser(html)
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for node in self._fools_all(tree, self.fools_search_item_selector):
            link_node: Node | None = None
            if node.tag == "a" and "/manga/" in self._fools_attr(node, "href"):
                link_node = node
            else:
                for candidate in node.css("a"):
                    if "/manga/" in self._fools_attr(candidate, "href"):
                        link_node = candidate
                        break

            if link_node is None:
                continue

            href = self._fools_attr(link_node, "href")
            if not href:
                continue
            abs_url = self._fools_abs(href, base_url)
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            title_node = node.css_first(self.fools_search_title_selector)
            title = self._fools_text(title_node) or self._fools_attr(
                link_node, "title"
            )
            if not title:
                title = (
                    self._fools_attr(link_node, "href")
                    .rstrip("/")
                    .rsplit("/", 1)[-1]
                    .replace(".html", "")
                )
                title = title.replace("-", " ").title()
            if not title:
                continue

            cover_node = node.css_first(self.fools_search_cover_selector)
            cover_src = (
                self._fools_attr(cover_node, "data-src")
                or self._fools_attr(cover_node, "data-lazy-src")
                or self._fools_attr(cover_node, "src")
            )
            cover_url = (
                self._fools_abs(cover_src, base_url) if cover_src else None
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
            "MangasOriginesFr search: {n} résultat(s)", n=len(results)
        )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga Mangas Origines FR.

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
            # FoolSlide utilise l'extension .html pour les mangas.
            slug = url_or_id.strip("/")
            if not slug.endswith(".html"):
                slug = f"{slug}.html"
            url = f"{self.base_url}{self.fools_series_path}/{slug}"

        self.logger.debug("MangasOriginesFr get_manga: {url}", url=url)

        try:
            rendered = await self.fetch_rendered(
                url,
                wait_until="networkidle",
                wait_for_selector="h1, .manga-title, .title",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_manga MangasOriginesFr pour {url!r}: {err}",
                url=url,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {url!r}: {exc}"
            ) from exc

        html = rendered.html
        tree = HTMLParser(html)

        title_node = self._fools_first(tree, self.fools_manga_title_selector)
        title = self._fools_text(title_node)
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        cover_node = self._fools_first(tree, self.fools_manga_cover_selector)
        cover_src = (
            self._fools_attr(cover_node, "data-src")
            or self._fools_attr(cover_node, "data-lazy-src")
            or self._fools_attr(cover_node, "src")
        )
        cover_url = self._fools_abs(cover_src, url) if cover_src else None

        description_node = self._fools_first(
            tree, self.fools_manga_description_selector
        )
        description = self._fools_text(description_node) or None

        author_node = self._fools_first(
            tree, self.fools_manga_author_selector
        )
        author = self._fools_text(author_node) or None

        artist_node = self._fools_first(
            tree, self.fools_manga_artist_selector
        )
        artist = self._fools_text(artist_node) or None

        genre_nodes = self._fools_all(tree, self.fools_manga_genres_selector)
        genres: list[str] = []
        for node in genre_nodes:
            text = self._fools_text(node)
            if text and text not in genres:
                genres.append(text)

        status_node = self._fools_first(
            tree, self.fools_manga_status_selector
        )
        status = self._fools_parse_status(self._fools_text(status_node))

        source_id = self._fools_extract_series_slug(url)
        rating = self._fools_detect_rating(genres)

        chapters = self._fools_parse_chapters_from_html(html, base_url=url)

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
            "MangasOriginesFr get_manga OK: {title} ({n} chapitres)",
            title=title,
            n=len(chapters),
        )
        return manga

    @staticmethod
    def _fools_extract_series_slug(url: str) -> str:
        """Extrait un identifiant de série depuis une URL Mangas Origines.

        Format : ``/manga/{slug}.html`` → retourne ``{slug}``.

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
                slug = remaining[0]
                if slug.endswith(".html"):
                    slug = slug[:-5]
                return slug
        if parts:
            slug = parts[-1]
            if slug.endswith(".html"):
                slug = slug[:-5]
            return slug
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
            "MangasOriginesFr get_chapters: {title}", title=manga.title
        )

        try:
            rendered = await self.fetch_rendered(
                str(manga.url),
                wait_until="networkidle",
                wait_for_selector=".chapter-list a, a[href*='/lecture-en-ligne/']",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_chapters MangasOriginesFr pour {title!r}: {err}",
                title=manga.title,
                err=exc,
            )
            raise ParseError(
                f"get_chapters échoué sur {self.site_id!r} "
                f"pour {manga.title!r}: {exc}"
            ) from exc

        chapters = self._fools_parse_chapters_from_html(
            rendered.html, base_url=rendered.final_url or str(manga.url)
        )
        if not chapters:
            raise ChapterNotFoundError(
                f"Aucun chapitre trouvé pour {manga.url} sur {self.site_id!r}"
            )
        return chapters

    def _fools_parse_chapters_from_html(
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

        for node in self._fools_all(tree, self.fools_chapter_selector):
            href = self._fools_attr(node, "href")
            if not href or "/lecture-en-ligne/" not in href:
                continue
            abs_url = self._fools_abs(href, base_url)
            if abs_url in seen:
                continue
            seen.add(abs_url)

            label = self._fools_text(node) or self._fools_attr(node, "title")
            if not label:
                label = abs_url.rstrip("/").rsplit("/", 1)[-1] or abs_url
                if label.endswith(".html"):
                    label = label[:-5]

            number = self._fools_chapter_number(label)
            volume = self._fools_chapter_volume(label)

            date_text = self._fools_attr(node, "data-date") or None
            published = (
                self._fools_parse_relative_date(date_text)
                if date_text
                else self._fools_parse_relative_date(label)
            )

            # Extrait l'ID de chapitre pour usage ultérieur (AJAX reader).
            chapter_id = self._mo_extract_chapter_id(abs_url)
            source_id = chapter_id or (
                abs_url.rstrip("/").rsplit("/", 1)[-1].replace(".html", "")
                or label
            )

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
            "MangasOriginesFr chapitres: {n}", n=len(chapters)
        )
        return chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre Mangas Origines FR.

        Mangas Origines (FoolSlide) expose les pages dans une variable JS
        ``pages = [...]``. Le parseur utilise trois stratégies :

        1. Extraction depuis la variable JS ``pages``.
        2. Rendu Playwright + extraction DOM (fallback).
        3. Endpoint AJAX ``/ajax/lecture.php?id={id}&page={n}`` (dernier
           recours).

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "MangasOriginesFr get_pages: {url}", url=str(chapter.url)
        )
        chapter_url = str(chapter.url)

        try:
            rendered = await self.fetch_rendered(
                chapter_url,
                wait_until="networkidle",
                wait_for_selector="img.page-image, .reader-content img, .page-content img",
                timeout=45.0,
                bypass_cloudflare=True,
                block_resources=False,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_pages MangasOriginesFr pour {url!r}: {err}",
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

        # Priorité 1 : extraction depuis la variable JS `pages`.
        js_urls = self._mo_extract_pages_from_js(html)
        if js_urls:
            for u in js_urls:
                abs_url = self._fools_abs(u, chapter_url)
                if abs_url not in urls:
                    urls.append(abs_url)

        # Priorité 2 : sélecteur CSS.
        if not urls:
            for node in self._fools_all(tree, self.fools_page_img_selector):
                src = (
                    self._fools_attr(node, "data-src")
                    or self._fools_attr(node, "data-lazy-src")
                    or self._fools_attr(node, "data-original")
                    or self._fools_attr(node, "src")
                )
                if not src:
                    continue
                # Ignore les images non-chapitre (logos, bannières).
                if any(
                    marker in src.lower()
                    for marker in ("logo", "banner", "avatar", "icon", "sprite")
                ):
                    continue
                abs_url = self._fools_abs(src, chapter_url)
                if abs_url not in urls:
                    urls.append(abs_url)

        # Priorité 3 : endpoint AJAX FoolSlide.
        if not urls:
            chapter_id = (
                self._mo_extract_chapter_id(chapter_url) or chapter.source_id
            )
            if chapter_id and chapter_id.isdigit():
                urls = await self._mo_fetch_pages_via_ajax(chapter_id)

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
            clean = self._fools_abs(clean, chapter_url)
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
            "MangasOriginesFr get_pages: {n} page(s) extraite(s)",
            n=len(pages),
        )
        return pages

    async def _mo_fetch_pages_via_ajax(
        self, chapter_id: str
    ) -> list[str]:
        """Récupère les pages via l'endpoint AJAX FoolSlide.

        L'endpoint ``/ajax/lecture.php?id={chapter_id}&page={n}`` retourne
        le HTML d'une page individuelle. Le parseur incrémente ``n`` jusqu'à
        obtenir une réponse vide ou une erreur.

        Args:
            chapter_id: ID du chapitre (numérique).

        Returns:
            Liste ordonnée d'URLs d'images.
        """
        urls: list[str] = []
        page_num = 1
        max_pages = 500  # sécurité

        while page_num <= max_pages:
            endpoint = (
                f"{self.base_url}{self.mo_ajax_endpoint}"
                f"?id={chapter_id}&page={page_num}"
            )

            try:
                response = await self.cf_get(
                    endpoint,
                    headers={
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": f"{self.base_url}/",
                    },
                    timeout=15.0,
                )
            except Exception:  # noqa: BLE001
                break

            if response.status_code >= 400:
                break

            body = response.text.strip()
            if not body:
                break

            # L'endpoint peut retourner du JSON ou du HTML.
            img_url: str | None = None

            # Tentative JSON.
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    img_url = (
                        payload.get("url")
                        or payload.get("image")
                        or payload.get("src")
                    )
                elif isinstance(payload, list) and payload:
                    first = payload[0]
                    if isinstance(first, str):
                        img_url = first
                    elif isinstance(first, dict):
                        img_url = (
                            first.get("url")
                            or first.get("image")
                            or first.get("src")
                        )
            except ValueError:
                # Fallback HTML.
                img_match = re.search(
                    r'<img[^>]+(?:data-)?src="([^"]+)"', body, re.IGNORECASE
                )
                if img_match:
                    img_url = img_match.group(1)

            if not img_url or img_url in urls:
                break

            urls.append(img_url)
            page_num += 1

        return urls

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que Mangas Origines FR est accessible.

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
                        "Health check MangasOriginesFr OK: {base} (status={s})",
                        base=base,
                        s=rendered.status,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Health check MangasOriginesFr échec sur {base}: {err}",
                    base=base,
                    err=exc,
                )
        self.logger.warning(
            "Health check MangasOriginesFr KO (tous domaines)"
        )
        return False
