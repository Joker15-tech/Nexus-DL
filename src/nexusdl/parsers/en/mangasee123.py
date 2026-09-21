"""Parseur MangaSee123 pour NexusDL.

MangaSee (``https://mangasee123.com``) est une archive anglophone majeure
de plus de 6 600 mangas, connue pour la qualité élevée de ses scans (souvent
les versions officielles Viz). Le site partage son contenu avec **MangaLife**
(``https://manga4life.com``), qui est une instance clone destinée aux tests.

Caractéristiques techniques
---------------------------

* **Moteur** : **AngularJS** (framework JavaScript historique) — le site
  expose toutes les données via des variables JS globales injectées dans le
  HTML, notamment ``vm.CHAPTERS`` (liste des chapitres) et ``vm.CurPathName``
  (host du CDN d'images).
* **Langue** : Anglais (``en``).
* **Protection** : Cloudflare (AS13335, IP ``104.21.83.37``). Le site utilise
  des règles firewall strictes (erreurs **1020** fréquentes pour les clients
  non-navigateur). Le rendu Playwright est nécessaire.
* **Domaines** : ``mangasee123.com`` (principal), ``manga4life.com``
  (miroir officiel), ``mangalife.us`` (ancien).
* **Structure des URLs** :
    - Recherche : ``/search/?name={query}``.
    - Manga : ``/manga/{slug}``.
    - Chapitre : ``/read-online/{slug}-chapter-{num}-page-1.html``.
* **Images** : hébergées sur des CDN dynamiques (``official-ongoing-2.gamindustri.us``,
  ``scans-hot.leanbox.us``, etc.), avec le format
  ``https://{host}/manga/{manga}/{chapter}-{page}.png``. Les numéros de
  chapitre et de page sont **encodés sur 4 et 3 chiffres** respectivement.
* **Cookie** : ``FullPage=yes`` requis pour obtenir la liste complète des
  chapitres (pagination complète).

Le parseur combine trois mixins :

1. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — rendu
   Playwright obligatoire (site AngularJS + Cloudflare).
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — bypass
   Cloudflare automatique (Playwright / FlareSolverr).
3. :class:`~nexusdl.parsers.mixins.api_based.ApiBasedMixin` — client REST
   pour les endpoints JSON internes si disponibles.

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.en.mangasee123 import MangaSee123Parser
    >>>
    >>> parser = MangaSee123Parser(config, session)
    >>> results = await parser.search("one piece")
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

__all__ = ["MangaSee123Parser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://mangasee123.com"
_FALLBACK_DOMAINS: Final[tuple[str, ...]] = (
    "https://mangasee123.com",
    "https://manga4life.com",
    "https://mangalife.us",
)
_SITE_ID: Final[str] = "mangasee123"
_LANGUAGE: Final[Language] = Language.EN
_ADULT: Final[bool] = False

# Cookie obligatoire pour la liste complète des chapitres.
_FULLPAGE_COOKIE: Final[str] = "FullPage=yes"

# Regex de parsing des chapitres (format MangaSee : "Chapter 1090").
_CHAPTER_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:chapter|ch\.?)\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE
)
_VOLUME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:volume|vol\.?)\s*(\d+)", re.IGNORECASE
)

# Regex pour l'extraction des variables JS AngularJS.
_VM_CHAPTERS_RE: Final[re.Pattern[str]] = re.compile(
    r"vm\.CHAPTERS\s*=\s*(\[.*?\])\s*;", re.DOTALL
)
_VM_CURPATHNAME_RE: Final[re.Pattern[str]] = re.compile(
    r'vm\.CurPathName\s*=\s*"([^"]+)"'
)
_VM_MANGA_RE: Final[re.Pattern[str]] = re.compile(
    r'vm\.MangaName\s*=\s*"([^"]+)"'
)
_VM_INDEXNAME_RE: Final[re.Pattern[str]] = re.compile(
    r'vm\.IndexName\s*=\s*"([^"]+)"'
)

# Statuts → énumération NexusDL.
_STATUS_MAP: Final[dict[str, MangaStatus]] = {
    "ongoing": MangaStatus.ONGOING,
    "complete": MangaStatus.COMPLETED,
    "completed": MangaStatus.COMPLETED,
    "hiatus": MangaStatus.HIATUS,
    "cancelled": MangaStatus.CANCELLED,
    "canceled": MangaStatus.CANCELLED,
    "discontinued": MangaStatus.CANCELLED,
    "suspended": MangaStatus.CANCELLED,
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
# Sélecteurs MangaSee123 (AngularJS rendu)
# ---------------------------------------------------------------------------

_MS_SEARCH_ITEM_SELECTOR: Final[str] = (
    "div.search-results a, .search-results a, "
    "a[href*='/manga/']"
)
_MS_SEARCH_LINK_SELECTOR: Final[str] = "a"
_MS_SEARCH_COVER_SELECTOR: Final[str] = "img"
_MS_SEARCH_TITLE_SELECTOR: Final[str] = ".title, h3, h4, .search-result-title"

_MS_MANGA_TITLE_SELECTOR: Final[str] = "h1, .manga-title, .title"
_MS_MANGA_COVER_SELECTOR: Final[str] = (
    ".cover img, .manga-cover img, img[src*='cover.nep.li']"
)
_MS_MANGA_DESCRIPTION_SELECTOR: Final[str] = (
    ".description, .manga-description, .summary"
)
_MS_MANGA_AUTHOR_SELECTOR: Final[str] = ".author, .manga-author"
_MS_MANGA_ARTIST_SELECTOR: Final[str] = ".artist, .manga-artist"
_MS_MANGA_GENRES_SELECTOR: Final[str] = (
    ".genres a, .manga-genres a, a[href*='/genre/']"
)
_MS_MANGA_STATUS_SELECTOR: Final[str] = ".status, .manga-status"

_MS_CHAPTER_SELECTOR: Final[str] = (
    "div.chapter-list a, "
    ".chapter-list a, "
    "a[href*='/read-online/']"
)
_MS_PAGE_IMG_SELECTOR: Final[str] = (
    ".reading-content img, "
    ".chapter-content img, "
    "#chapter-content img"
)
_MS_PAGES_VAR_NAMES: Final[tuple[str, ...]] = (
    "vm.CHAPTERS",
    "pages",
    "page_urls",
    "pageUrls",
    "images",
)


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


class MangaSee123Parser(
    JsRenderedMixin,
    CloudflareMixin,
    ApiBasedMixin,
    BaseParser,
):
    """Parseur MangaSee123 (AngularJS + Cloudflare).

    Combine le rendu Playwright (obligatoire — le site est une application
    AngularJS et exige un rendu JS complet), le contournement Cloudflare et
    un client API REST pour couvrir l'ensemble des cas d'usage.

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

    # --- Chemins MangaSee123 ---
    ms_search_path: ClassVar[str] = "/search/"
    ms_series_path: ClassVar[str] = "/manga"
    ms_read_path: ClassVar[str] = "/read-online"

    # --- Sélecteurs MangaSee123 ---
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

    ms_chapter_selector: ClassVar[str] = _MS_CHAPTER_SELECTOR
    ms_page_img_selector: ClassVar[str] = _MS_PAGE_IMG_SELECTOR
    ms_pages_var_names: ClassVar[tuple[str, ...]] = _MS_PAGES_VAR_NAMES

    # --- Comportement ---
    ms_requires_js: ClassVar[bool] = True  # Site AngularJS (SPA)
    ms_default_rating: ClassVar[ContentRating] = ContentRating.SAFE

    # --- Configuration API ---
    api_base_url: ClassVar[str] = _BASE_URL
    api_default_headers: ClassVar[dict[str, str]] = {
        "Accept": "application/json",
        "X-Referer": _BASE_URL,
        "Origin": _BASE_URL,
    }
    api_rate_limit_per_second: ClassVar[float] = 3.0
    api_rate_limit_burst: ClassVar[int] = 3
    api_timeout: ClassVar[float] = 20.0
    api_cache_enabled: ClassVar[bool] = True
    api_cache_ttl: ClassVar[int] = 300

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
    default_wait_until: ClassVar[str] = "networkidle"  # AngularJS : attendre le réseau
    default_render_timeout: ClassVar[float] = 45.0
    default_navigation_timeout: ClassVar[float] = 60.0
    block_resources_by_default: ClassVar[bool] = False  # Autoriser les images (SPA)
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
        """Initialise le parseur MangaSee123.

        Args:
            config: Configuration du site (:class:`SiteConfig`).
            session: Session HTTP async du projet (:class:`HttpSession`).
            playwright_pool: Pool Playwright partagé (requis — le site est
                une application AngularJS).
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

        # Injecte le cookie FullPage=yes requis pour la liste complète.
        self._inject_fullpage_cookie()

    def _inject_fullpage_cookie(self) -> None:
        """Injecte le cookie ``FullPage=yes`` requis par MangaSee.

        Ce cookie est nécessaire pour que le site renvoie la liste complète
        des chapitres sans pagination côté serveur.
        """
        target = getattr(self.session, "cookies", None)
        if isinstance(target, dict):
            target.update({"FullPage": "yes"})
            self.logger.debug(
                "Cookie FullPage=yes injecté dans la session HTTP"
            )
            return
        updater = getattr(self.session, "update_cookies", None)
        if callable(updater):
            try:
                updater({"FullPage": "yes"})
                self.logger.debug(
                    "Cookie FullPage=yes injecté via update_cookies"
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Échec injection cookie FullPage: {err}", err=exc
                )

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------

    def _ms_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="mangasee"``.
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
        return MangaSee123Parser._ms_clean(
            node.text(deep=True, separator=" ")
        )

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
        """Convertit un libellé de statut en énumération NexusDL.

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
    def _ms_chapter_number(text: str) -> float | str:
        """Extrait le numéro de chapitre d'un libellé.

        Args:
            text: Libellé (ex. ``"Chapter 1090"``).

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = MangaSee123Parser._ms_clean(text)
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
    # Extraction des variables JS AngularJS
    # ------------------------------------------------------------------

    @staticmethod
    def _ms_extract_vm_chapters(html: str) -> list[dict[str, Any]]:
        """Extrait la variable JS ``vm.CHAPTERS`` du HTML.

        Cette variable contient la liste complète des chapitres avec, pour
        chaque chapitre, son numéro encodé (``Chapter``), le nombre de pages
        (``Page``), sa date de publication (``Date``) et son type
        (``Type`` — ``"Chapter"``, ``"Volume"``, etc.).

        Args:
            html: HTML de la page manga.

        Returns:
            Liste de dictionnaires (vide si introuvable).
        """
        match = _VM_CHAPTERS_RE.search(html)
        if not match:
            return []
        try:
            parsed = _json.loads(match.group(1))
        except ValueError:
            return []
        if not isinstance(parsed, list):
            return []
        return [c for c in parsed if isinstance(c, dict)]

    @staticmethod
    def _ms_extract_cur_path_name(html: str) -> str | None:
        """Extrait la variable JS ``vm.CurPathName`` du HTML.

        Cette variable contient le host du CDN d'images pour le chapitre
        courant (ex. ``official-ongoing-2.gamindustri.us``).

        Args:
            html: HTML de la page chapitre.

        Returns:
            Host du CDN ou ``None``.
        """
        match = _VM_CURPATHNAME_RE.search(html)
        return match.group(1) if match else None

    @staticmethod
    def _ms_extract_index_name(html: str) -> str | None:
        """Extrait la variable JS ``vm.IndexName`` du HTML.

        Args:
            html: HTML de la page manga.

        Returns:
            Index name (slug) du manga ou ``None``.
        """
        match = _VM_INDEXNAME_RE.search(html)
        return match.group(1) if match else None

    @staticmethod
    def _ms_decode_chapter_number(encoded: str) -> str:
        """Décode un numéro de chapitre MangaSee encodé.

        MangaSee encode les numéros de chapitre sur 5 chiffres avec un
        préfixe ``1`` et un suffixe ``0`` (ex. ``100010`` → ``10``,
        ``100020`` → ``20``, ``105005`` → ``50.5``).

        Args:
            encoded: Numéro de chapitre encodé.

        Returns:
            Numéro de chapitre décodé sous forme de chaîne.
        """
        clean = encoded.strip()
        if len(clean) >= 5 and clean.startswith("1"):
            # Enlève le préfixe "1" et le suffixe "0".
            inner = clean[1:-1]
            # Insère un point décimal avant les 3 derniers chiffres.
            if len(inner) > 3:
                whole = inner[:-3]
                dec = inner[-3:].lstrip("0")
                if dec:
                    return f"{whole}.{dec}"
                return whole
            return inner
        return clean

    @staticmethod
    def _ms_build_page_url(
        host: str,
        manga_slug: str,
        chapter_encoded: str,
        page_num: int,
    ) -> str:
        """Construit l'URL d'une page à partir des variables MangaSee.

        Format : ``https://{host}/manga/{manga}/{chapter}-{page}.png``
        où ``chapter`` est sur **4 chiffres** et ``page`` sur **3 chiffres**.

        Args:
            host: Host du CDN (``vm.CurPathName``).
            manga_slug: Slug du manga (``vm.IndexName``).
            chapter_encoded: Numéro de chapitre encodé (``vm.CHAPTERS[i].Chapter``).
            page_num: Numéro de page (1-based).

        Returns:
            URL absolue de la page.
        """
        # Tronque les 5 chiffres à 4 (enlève le préfixe "1").
        if chapter_encoded.startswith("1") and len(chapter_encoded) > 4:
            chapter_part = chapter_encoded[1:]
        else:
            chapter_part = chapter_encoded
        chapter_part = chapter_part.zfill(4)
        page_part = str(page_num).zfill(3)
        return f"https://{host}/manga/{manga_slug}/{chapter_part}-{page_part}.png"

    # ------------------------------------------------------------------
    # Méthodes abstraites de BaseParser
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des mangas sur MangaSee123.

        MangaSee utilise ``/search/?name={query}`` (paramètre ``name``).

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.

        Raises:
            ParseError: Si le HTML de résultats est inexploitable.
        """
        self.logger.debug(
            "MangaSee123 search: {query} (page {page})",
            query=query,
            page=page,
        )

        url = f"{self.base_url}{self.ms_search_path}?name={quote_plus(query)}"

        try:
            rendered = await self.fetch_rendered(
                url,
                wait_until="networkidle",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec recherche MangaSee123 pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        return self._ms_parse_search_html(
            rendered.html, base_url=rendered.final_url or url
        )

    def _ms_parse_search_html(
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
        seen_urls: set[str] = set()

        for node in self._ms_all(tree, self.ms_search_item_selector):
            link_node: Node | None = None
            if node.tag == "a" and "/manga/" in self._ms_attr(node, "href"):
                link_node = node
            else:
                for candidate in node.css("a"):
                    if "/manga/" in self._ms_attr(candidate, "href"):
                        link_node = candidate
                        break

            if link_node is None:
                continue

            href = self._ms_attr(link_node, "href")
            if not href:
                continue
            abs_url = self._ms_abs(href, base_url)
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            title_node = node.css_first(self.ms_search_title_selector)
            title = self._ms_text(title_node) or self._ms_attr(
                link_node, "title"
            )
            if not title:
                title = self._ms_attr(link_node, "href").rstrip("/").rsplit("/", 1)[-1]
                title = title.replace("-", " ").title()
            if not title:
                continue

            cover_node = node.css_first(self.ms_search_cover_selector)
            cover_src = (
                self._ms_attr(cover_node, "data-src")
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
            "MangaSee123 search: {n} résultat(s)", n=len(results)
        )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga MangaSee123.

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
            url = f"{self.base_url}{self.ms_series_path}/{url_or_id.strip('/')}"

        self.logger.debug("MangaSee123 get_manga: {url}", url=url)

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
                "Échec get_manga MangaSee123 pour {url!r}: {err}",
                url=url,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {url!r}: {exc}"
            ) from exc

        html = rendered.html
        tree = HTMLParser(html)

        title_node = self._ms_first(tree, self.ms_manga_title_selector)
        title = self._ms_text(title_node)
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        cover_node = self._ms_first(tree, self.ms_manga_cover_selector)
        cover_src = (
            self._ms_attr(cover_node, "data-src")
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

        # Extraction du slug canonique via vm.IndexName si présent.
        source_id = (
            self._ms_extract_index_name(html)
            or self._ms_extract_series_slug(url)
        )
        rating = self._ms_detect_rating(genres)

        chapters = await self._ms_parse_chapters_from_html(
            html, base_url=url
        )

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
            "MangaSee123 get_manga OK: {title} ({n} chapitres)",
            title=title,
            n=len(chapters),
        )
        return manga

    @staticmethod
    def _ms_extract_series_slug(url: str) -> str:
        """Extrait un identifiant de série depuis une URL MangaSee.

        Format : ``/manga/{slug}`` → retourne ``{slug}``.

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
            "MangaSee123 get_chapters: {title}", title=manga.title
        )

        try:
            rendered = await self.fetch_rendered(
                str(manga.url),
                wait_until="networkidle",
                wait_for_selector="a[href*='/read-online/'], .chapter-list a",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_chapters MangaSee123 pour {title!r}: {err}",
                title=manga.title,
                err=exc,
            )
            raise ParseError(
                f"get_chapters échoué sur {self.site_id!r} "
                f"pour {manga.title!r}: {exc}"
            ) from exc

        chapters = await self._ms_parse_chapters_from_html(
            rendered.html, base_url=rendered.final_url or str(manga.url)
        )
        if not chapters:
            raise ChapterNotFoundError(
                f"Aucun chapitre trouvé pour {manga.url} sur {self.site_id!r}"
            )
        return chapters

    async def _ms_parse_chapters_from_html(
        self,
        html: str,
        *,
        base_url: str,
    ) -> list[Chapter]:
        """Parse la liste des chapitres depuis le HTML d'une fiche manga.

        MangaSee expose la liste complète des chapitres dans la variable JS
        ``vm.CHAPTERS`` (AngularJS). Le parseur privilégie cette source
        structurée, avec un fallback sur les liens HTML classiques.

        Args:
            html: HTML rendu.
            base_url: URL de la page manga.

        Returns:
            Liste de :class:`Chapter` triés.
        """
        language = self.language
        manga_slug = self._ms_extract_index_name(html) or ""

        # Priorité 1 : variable JS vm.CHAPTERS (données structurées).
        vm_chapters = self._ms_extract_vm_chapters(html)
        if vm_chapters:
            chapters: list[Chapter] = []
            for entry in vm_chapters:
                encoded = str(entry.get("Chapter", ""))
                if not encoded:
                    continue
                decoded = self._ms_decode_chapter_number(encoded)
                try:
                    number: float | str = float(decoded)
                except ValueError:
                    number = decoded

                label = f"Chapter {decoded}"
                chapter_url = (
                    f"{self.base_url}{self.ms_read_path}/"
                    f"{manga_slug}-chapter-{decoded}-page-1.html"
                )

                date_str = entry.get("Date")
                published = None
                if isinstance(date_str, str) and date_str:
                    published = self._ms_parse_date(date_str)

                source_id = f"{manga_slug}-chapter-{decoded}"
                chapters.append(
                    Chapter(
                        id=f"{self.config.id}:{source_id}",
                        source_id=source_id,
                        title=label,
                        number=number,
                        volume=None,
                        language=language,
                        pages_count=entry.get("Page") if isinstance(entry.get("Page"), int) else None,
                        published_at=published,
                        url=chapter_url,
                        pages=[],
                    )
                )

            def _sort_key(ch: Chapter) -> tuple[int, float, str]:
                num = ch.number if isinstance(ch.number, (int, float)) else 0.0
                return (int(isinstance(ch.number, str)), float(num), ch.title.lower())

            chapters.sort(key=_sort_key)
            self.logger.debug(
                "MangaSee123 chapitres (vm.CHAPTERS): {n}", n=len(chapters)
            )
            return chapters

        # Priorité 2 : liens HTML classiques.
        tree = HTMLParser(html)
        seen: set[str] = set()
        chapters = []
        for node in self._ms_all(tree, self.ms_chapter_selector):
            href = self._ms_attr(node, "href")
            if not href or "/read-online/" not in href:
                continue
            abs_url = self._ms_abs(href, base_url)
            if abs_url in seen:
                continue
            seen.add(abs_url)

            label = self._ms_text(node) or self._ms_attr(node, "title")
            if not label:
                label = abs_url.rstrip("/").rsplit("/", 1)[-1] or abs_url

            number = self._ms_chapter_number(label)
            source_id = abs_url.rstrip("/").rsplit("/", 1)[-1] or label
            chapters.append(
                Chapter(
                    id=f"{self.config.id}:{source_id}",
                    source_id=source_id,
                    title=label,
                    number=number,
                    volume=None,
                    language=language,
                    pages_count=None,
                    published_at=None,
                    url=abs_url,
                    pages=[],
                )
            )

        def _sort_key_html(ch: Chapter) -> tuple[int, float, str]:
            num = ch.number if isinstance(ch.number, (int, float)) else 0.0
            return (int(isinstance(ch.number, str)), float(num), ch.title.lower())

        chapters.sort(key=_sort_key_html)
        self.logger.debug(
            "MangaSee123 chapitres (HTML): {n}", n=len(chapters)
        )
        return chapters

    @staticmethod
    def _ms_parse_date(raw: str) -> datetime | None:
        """Parse une date MangaSee au format ISO ou texte.

        Args:
            raw: Chaîne de date (ex. ``"2024-01-15 12:30:00"``).

        Returns:
            Datetime UTC correspondant ou ``None``.
        """
        if not raw:
            return None
        # Essaie le format ISO.
        with_pandas = False
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
        # Essaie le format "%Y-%m-%d %H:%M:%S".
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre MangaSee123.

        Le site est une application AngularJS : le rendu Playwright est
        nécessaire. Les URLs des images sont reconstruites à partir des
        variables JS ``vm.CurPathName`` (host CDN) et ``vm.CHAPTERS``
        (nombre de pages).

        Format : ``https://{host}/manga/{manga}/{chapter}-{page}.png``
        où ``chapter`` est sur **4 chiffres** et ``page`` sur **3 chiffres**.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "MangaSee123 get_pages: {url}", url=str(chapter.url)
        )
        chapter_url = str(chapter.url)

        try:
            rendered = await self.fetch_rendered(
                chapter_url,
                wait_until="networkidle",
                wait_for_selector=".reading-content img, .chapter-content img",
                timeout=45.0,
                bypass_cloudflare=True,
                block_resources=False,  # Les images sont nécessaires
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_pages MangaSee123 pour {url!r}: {err}",
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

        # Priorité 1 : reconstruction via variables JS (méthode fiable).
        cur_path_name = self._ms_extract_cur_path_name(html)
        vm_chapters = self._ms_extract_vm_chapters(html)

        if cur_path_name and vm_chapters:
            # Trouve le chapitre correspondant dans vm.CHAPTERS.
            chapter_num = chapter.number
            matched_entry: dict[str, Any] | None = None
            for entry in vm_chapters:
                encoded = str(entry.get("Chapter", ""))
                decoded = self._ms_decode_chapter_number(encoded)
                try:
                    if abs(float(decoded) - float(chapter_num)) < 1e-6:
                        matched_entry = entry
                        break
                except (ValueError, TypeError):
                    continue

            if matched_entry:
                page_count = matched_entry.get("Page", 0)
                if isinstance(page_count, int) and page_count > 0:
                    manga_slug = self._ms_extract_index_name(html) or ""
                    encoded = str(matched_entry.get("Chapter", ""))
                    for page_num in range(1, page_count + 1):
                        urls.append(
                            self._ms_build_page_url(
                                cur_path_name,
                                manga_slug,
                                encoded,
                                page_num,
                            )
                        )

        # Priorité 2 : sélecteur CSS fallback.
        if not urls:
            for node in self._ms_all(tree, self.ms_page_img_selector):
                src = (
                    self._ms_attr(node, "data-src")
                    or self._ms_attr(node, "data-lazy-src")
                    or self._ms_attr(node, "data-original")
                    or self._ms_attr(node, "src")
                )
                if not src:
                    continue
                abs_url = self._ms_abs(src, chapter_url)
                if abs_url not in urls:
                    urls.append(abs_url)

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
            clean = self._ms_abs(clean, chapter_url)
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
            "MangaSee123 get_pages: {n} page(s) extraite(s)", n=len(pages)
        )
        return pages

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que MangaSee123 est accessible.

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
                        "Health check MangaSee123 OK: {base} (status={s})",
                        base=base,
                        s=rendered.status,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Health check MangaSee123 échec sur {base}: {err}",
                    base=base,
                    err=exc,
                )
        self.logger.warning("Health check MangaSee123 KO (tous domaines)")
        return False
