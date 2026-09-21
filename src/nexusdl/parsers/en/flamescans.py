"""Parseur Flame Comics pour NexusDL.

Flame Comics (``https://flamecomics.xyz``) est un groupe de scanlation
anglophone de premier plan, spécialisé dans les manhwa/manhua coréens et
chinois. Il publie des titres majeurs comme *Solo Leveling*, *The Beginning
After The End*, *Tomb Raider King*, etc.

Le site a connu plusieurs changements de domaine et d'infrastructure :

* ``flamecomics.com`` (domaine historique, redirige désormais)
* ``flamecomics.xyz`` (domaine actuel, depuis 2024)
* ``firescans.xyz`` (miroir)

Caractéristiques techniques
---------------------------

* **Moteur** : **Next.js** (React côté client, rendu hydraté côté serveur).
  Le site expose ses données via des endpoints ``_next/data`` et une balise
  ``<script id="__NEXT_DATA__">``, ce qui le rend très différent des sites
  WordPress/Madara.
* **Langue** : Anglais (``en``).
* **Protection** : Cloudflare (AS13335, IP ``104.21.87.8``) avec challenge
  de niveau élevé. Le site utilise également un **système de buildId
  dynamique** Next.js : l'ID change à chaque déploiement et doit être
  re-découvert en cas de 404.
* **Domaines** : ``flamecomics.xyz`` (principal), ``firescans.xyz`` (miroir).
* **Structure des URLs** :
    - Catalogue : ``/series``.
    - Manga : ``/series/{id}/{slug}`` (ex. ``/series/127/afe3b9729745771a``).
    - Chapitre : ``/series/{id}/{slug}/{chapter-slug}``.
* **Endpoints Next.js** :
    - ``/_next/data/{buildId}/en/series/{id}/{slug}.json`` — détails manga.
    - ``/_next/data/{buildId}/en/series/{id}/{slug}/{chapter}.json`` —
      images du chapitre.
* **BuildId** : l'ID est présent dans le HTML de la page d'accueil
  (``__NEXT_DATA__`` ou ``/_next/static/{buildId}/_buildManifest.js``).
  Le parseur le met en cache et le rafraîchit en cas de 404.
* **Images** : servies depuis ``cdn.flamecomics.xyz``, nécessitant le
  header ``Referer`` pointant vers ``https://flamecomics.xyz/``.
* **Contenu composite** : certains chapitres utilisent des « composite
  images » (images longues découpées) — le parseur les gère nativement en
  récupérant les URLs telles quelles.

Le parseur combine trois mixins :

1. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — rendu
   Playwright obligatoire (SPA Next.js + Cloudflare).
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — bypass
   Cloudflare automatique (Playwright / FlareSolverr).
3. :class:`~nexusdl.parsers.mixins.api_based.ApiBasedMixin` — client REST
   pour les endpoints ``_next/data``.

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.en.flamescans import FlameScansParser
    >>>
    >>> parser = FlameScansParser(config, session)
    >>> results = await parser.search("solo leveling")
    >>> manga = await parser.get_manga(results[0].url)
    >>> chapters = await parser.get_chapters(manga)
    >>> pages = await parser.get_pages(chapters[0])
"""

from __future__ import annotations

import json as _json
import re
import time
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

__all__ = ["FlameScansParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://flamecomics.xyz"
_FALLBACK_DOMAINS: Final[tuple[str, ...]] = (
    "https://flamecomics.xyz",
    "https://firescans.xyz",
    "https://flamecomics.com",
)
_SITE_ID: Final[str] = "flamescans"
_LANGUAGE: Final[Language] = Language.EN
_ADULT: Final[bool] = False

# Regex de parsing des chapitres (format Flame : "Chapter 122").
_CHAPTER_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:chapter|ch\.?|episode|ep\.?)\s*(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_VOLUME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:volume|vol\.?)\s*(\d+)", re.IGNORECASE
)

# Regex pour l'extraction du buildId Next.js.
_BUILD_ID_RE: Final[re.Pattern[str]] = re.compile(
    r'"buildId"\s*:\s*"([^"]+)"'
)
_BUILD_MANIFEST_RE: Final[re.Pattern[str]] = re.compile(
    r'/_next/static/([^/]+)/_buildManifest\.js'
)

# Regex pour l'extraction des données Next.js.
_NEXT_DATA_RE: Final[re.Pattern[str]] = re.compile(
    r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>',
    re.DOTALL,
)

# Statuts → énumération NexusDL.
_STATUS_MAP: Final[dict[str, MangaStatus]] = {
    "ongoing": MangaStatus.ONGOING,
    "on going": MangaStatus.ONGOING,
    "releasing": MangaStatus.ONGOING,
    "completed": MangaStatus.COMPLETED,
    "complete": MangaStatus.COMPLETED,
    "finished": MangaStatus.COMPLETED,
    "hiatus": MangaStatus.HIATUS,
    "paused": MangaStatus.HIATUS,
    "cancelled": MangaStatus.CANCELLED,
    "canceled": MangaStatus.CANCELLED,
    "dropped": MangaStatus.CANCELLED,
    "discontinued": MangaStatus.CANCELLED,
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
        "gender bender",
    }
)

# Marqueurs de contenu suggestif.
_SUGGESTIVE_MARKERS: Final[frozenset[str]] = frozenset(
    {"ecchi", "mature", "harem", "suggestive", "romance", "action"}
)

# ---------------------------------------------------------------------------
# Sélecteurs Flame Comics (Next.js rendu + fallback)
# ---------------------------------------------------------------------------

_FLAME_SEARCH_ITEM_SELECTOR: Final[str] = (
    "a[href*='/series/'], "
    ".series-item, .manga-item, "
    "div[class*='card'] a[href*='/series/'], "
    "div.grid a[href*='/series/']"
)
_FLAME_SEARCH_LINK_SELECTOR: Final[str] = "a"
_FLAME_SEARCH_COVER_SELECTOR: Final[str] = "img"
_FLAME_SEARCH_TITLE_SELECTOR: Final[str] = (
    ".title, h3, h4, .series-title, .manga-title, "
    ".text-sm, .font-medium"
)

_FLAME_MANGA_TITLE_SELECTOR: Final[str] = (
    "h1, .series-title, .manga-title, "
    ".entry-title, div.overflow-hidden h1"
)
_FLAME_MANGA_COVER_SELECTOR: Final[str] = (
    ".series-cover img, .manga-cover img, "
    ".cover img, .thumbnail img, "
    "main img, div.overflow-hidden img"
)
_FLAME_MANGA_DESCRIPTION_SELECTOR: Final[str] = (
    ".description, .summary, .series-summary, "
    ".manga-summary, .prose p, .synopsis"
)
_FLAME_MANGA_AUTHOR_SELECTOR: Final[str] = (
    ".author, .series-author, a[href*='/author/'], "
    ".text-sm.font-medium"
)
_FLAME_MANGA_ARTIST_SELECTOR: Final[str] = (
    ".artist, .series-artist, a[href*='/artist/']"
)
_FLAME_MANGA_GENRES_SELECTOR: Final[str] = (
    ".genres a, .series-genres a, "
    "a[href*='/genre/'], a[href*='/tag/'], "
    "div.flex a.text-xs"
)
_FLAME_MANGA_STATUS_SELECTOR: Final[str] = (
    ".status, .series-status, .text-sm"
)

_FLAME_CHAPTER_SELECTOR: Final[str] = (
    "a[href*='/series/'], "
    ".chapter-list a, "
    "ul[role='list'] li a, "
    "div[class*='chapter'] a"
)
_FLAME_PAGE_IMG_SELECTOR: Final[str] = (
    "div#images img, "
    "div[class*='reader'] img, "
    "div[class*='page'] img, "
    "main img"
)
_FLAME_PAGES_VAR_NAMES: Final[tuple[str, ...]] = (
    "pages",
    "page_urls",
    "pageUrls",
    "images",
    "chapterImages",
)


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


class FlameScansParser(
    JsRenderedMixin,
    CloudflareMixin,
    ApiBasedMixin,
    BaseParser,
):
    """Parseur Flame Comics (Next.js + Cloudflare + buildId).

    Combine le rendu Playwright (obligatoire — SPA Next.js protégée),
    le contournement Cloudflare, la gestion dynamique du buildId Next.js
    et un client REST pour les endpoints ``_next/data``.

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

    # --- Chemins Flame Comics ---
    flame_series_path: ClassVar[str] = "/series"
    flame_next_data_path: ClassVar[str] = "/_next/data"

    # --- Sélecteurs Flame Comics ---
    flame_search_item_selector: ClassVar[str] = _FLAME_SEARCH_ITEM_SELECTOR
    flame_search_link_selector: ClassVar[str] = _FLAME_SEARCH_LINK_SELECTOR
    flame_search_cover_selector: ClassVar[str] = _FLAME_SEARCH_COVER_SELECTOR
    flame_search_title_selector: ClassVar[str] = _FLAME_SEARCH_TITLE_SELECTOR

    flame_manga_title_selector: ClassVar[str] = _FLAME_MANGA_TITLE_SELECTOR
    flame_manga_cover_selector: ClassVar[str] = _FLAME_MANGA_COVER_SELECTOR
    flame_manga_description_selector: ClassVar[str] = _FLAME_MANGA_DESCRIPTION_SELECTOR
    flame_manga_author_selector: ClassVar[str] = _FLAME_MANGA_AUTHOR_SELECTOR
    flame_manga_artist_selector: ClassVar[str] = _FLAME_MANGA_ARTIST_SELECTOR
    flame_manga_genres_selector: ClassVar[str] = _FLAME_MANGA_GENRES_SELECTOR
    flame_manga_status_selector: ClassVar[str] = _FLAME_MANGA_STATUS_SELECTOR

    flame_chapter_selector: ClassVar[str] = _FLAME_CHAPTER_SELECTOR
    flame_page_img_selector: ClassVar[str] = _FLAME_PAGE_IMG_SELECTOR
    flame_pages_var_names: ClassVar[tuple[str, ...]] = _FLAME_PAGES_VAR_NAMES

    # --- Comportement ---
    flame_requires_js: ClassVar[bool] = True
    flame_default_rating: ClassVar[ContentRating] = ContentRating.SAFE
    flame_filter_promo_images: ClassVar[bool] = True

    # --- BuildId cache ---
    _build_id: ClassVar[str | None] = None
    _build_id_fetched_at: ClassVar[float] = 0.0
    _build_id_ttl: ClassVar[int] = 3600  # 1 heure

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
        """Initialise le parseur Flame Comics.

        Args:
            config: Configuration du site (:class:`SiteConfig`).
            session: Session HTTP async du projet (:class:`HttpSession`).
            playwright_pool: Pool Playwright partagé (requis — SPA Next.js).
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
    # Helpers internes
    # ------------------------------------------------------------------

    def _flame_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="flamescans"``.
        """
        return self.logger

    @staticmethod
    def _flame_clean(value: str | None) -> str:
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
    def _flame_text(node: Node | None) -> str:
        """Extrait et nettoie le texte d'un nœud.

        Args:
            node: Nœud selectolax ou ``None``.

        Returns:
            Texte nettoyé.
        """
        if node is None:
            return ""
        return FlameScansParser._flame_clean(
            node.text(deep=True, separator=" ")
        )

    @staticmethod
    def _flame_attr(node: Node | None, name: str) -> str:
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

    def _flame_first(self, tree: HTMLParser, selector: str) -> Node | None:
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

    def _flame_all(self, tree: HTMLParser, selector: str) -> list[Node]:
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

    def _flame_abs(self, url: str, base: str | None = None) -> str:
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
    # BuildId Next.js
    # ------------------------------------------------------------------

    async def _flame_get_build_id(self, *, force: bool = False) -> str | None:
        """Récupère le buildId Next.js courant.

        Le buildId est nécessaire pour construire les URLs des endpoints
        ``_next/data``. Il change à chaque déploiement du site.

        Args:
            force: Force le rafraîchissement même si un buildId est en cache.

        Returns:
            Le buildId ou ``None`` si introuvable.
        """
        now = time.monotonic()
        if (
            not force
            and self._build_id
            and (now - self._build_id_fetched_at) < self._build_id_ttl
        ):
            return self._build_id

        try:
            rendered = await self.fetch_rendered(
                self.base_url,
                wait_until="domcontentloaded",
                timeout=20.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(
                "Échec récupération buildId : {err}", err=exc
            )
            return self._build_id

        html = rendered.html

        # Priorité 1 : script __NEXT_DATA__.
        next_data = self._flame_extract_next_data(html)
        if next_data:
            bid = next_data.get("buildId")
            if isinstance(bid, str) and bid:
                self._build_id = bid
                self._build_id_fetched_at = now
                self.logger.debug("BuildId récupéré : {bid}", bid=bid)
                return bid

        # Priorité 2 : regex directe.
        match = _BUILD_ID_RE.search(html)
        if match:
            bid = match.group(1)
            self._build_id = bid
            self._build_id_fetched_at = now
            self.logger.debug("BuildId récupéré (regex) : {bid}", bid=bid)
            return bid

        # Priorité 3 : _buildManifest.js.
        match = _BUILD_MANIFEST_RE.search(html)
        if match:
            bid = match.group(1)
            self._build_id = bid
            self._build_id_fetched_at = now
            self.logger.debug(
                "BuildId récupéré (_buildManifest) : {bid}", bid=bid
            )
            return bid

        self.logger.warning("BuildId Next.js introuvable")
        return None

    @staticmethod
    def _flame_extract_next_data(html: str) -> dict[str, Any]:
        """Extrait les données JSON de la balise ``__NEXT_DATA__``.

        Args:
            html: HTML de la page.

        Returns:
            Dictionnaire des props Next.js (vide si introuvable).
        """
        match = _NEXT_DATA_RE.search(html)
        if not match:
            return {}
        try:
            data = _json.loads(match.group(1))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _flame_navigate_json(
        data: dict[str, Any], *keys: str
    ) -> Any:
        """Navigue dans un dictionnaire imbriqué.

        Args:
            data: Dictionnaire racine.
            *keys: Chemin de clés à suivre.

        Returns:
            Valeur trouvée ou ``None``.
        """
        current: Any = data
        for key in keys:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
            if current is None:
                return None
        return current

    async def _flame_fetch_next_data(
        self, path: str, *, force_build_id: bool = False
    ) -> dict[str, Any]:
        """Récupère un endpoint ``_next/data``.

        Args:
            path: Chemin relatif après ``/en`` (ex. ``/series/127/slug.json``).
            force_build_id: Force le rafraîchissement du buildId.

        Returns:
            Charge utile JSON (vide si l'endpoint échoue).
        """
        build_id = await self._flame_get_build_id(force=force_build_id)
        if not build_id:
            return {}

        url = f"{self.base_url}{self.flame_next_data_path}/{build_id}/en{path}"

        try:
            response = await self.cf_get(url, timeout=20.0)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(
                "Échec fetch _next/data : {err}", err=exc
            )
            return {}

        if response.status_code == 404:
            # BuildId obsolète → rafraîchit et réessaie une fois.
            self.logger.debug(
                "BuildId obsolète, rafraîchissement et retry"
            )
            new_build_id = await self._flame_get_build_id(force=True)
            if new_build_id and new_build_id != build_id:
                url = (
                    f"{self.base_url}{self.flame_next_data_path}/"
                    f"{new_build_id}/en{path}"
                )
                try:
                    response = await self.cf_get(url, timeout=20.0)
                except Exception:  # noqa: BLE001
                    return {}
            else:
                return {}

        if response.status_code >= 400:
            return {}

        try:
            return response.json()
        except ValueError:
            return {}

    # ------------------------------------------------------------------
    # Parsing statut / date / numéro de chapitre
    # ------------------------------------------------------------------

    def _flame_parse_status(self, raw: str | None) -> MangaStatus:
        """Convertit un libellé de statut en énumération NexusDL.

        Args:
            raw: Libellé brut.

        Returns:
            Statut normalisé (``ONGOING`` par défaut).
        """
        if not raw:
            return MangaStatus.ONGOING
        key = self._flame_clean(raw).lower()
        for needle, status in _STATUS_MAP.items():
            if needle in key:
                return status
        return MangaStatus.ONGOING

    @staticmethod
    def _flame_parse_year(raw: str | None) -> int | None:
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
    def _flame_chapter_number(text: str) -> float | str:
        """Extrait le numéro de chapitre d'un libellé.

        Args:
            text: Libellé (ex. ``"Chapter 122"``).

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = FlameScansParser._flame_clean(text)
        match = _CHAPTER_NUMBER_RE.search(cleaned)
        if not match:
            return cleaned
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return cleaned

    @staticmethod
    def _flame_chapter_volume(text: str) -> int | None:
        """Extrait le numéro de tome d'un libellé.

        Args:
            text: Libellé contenant éventuellement ``Volume X``.

        Returns:
            Numéro de tome ou ``None``.
        """
        match = _VOLUME_RE.search(text or "")
        return int(match.group(1)) if match else None

    def _flame_detect_rating(self, genres: list[str]) -> ContentRating:
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
            if {"smut", "mature", "erotica", "ecchi"} & lowered:
                return ContentRating.EROTICA
        if lowered & _SUGGESTIVE_MARKERS:
            return ContentRating.SUGGESTIVE
        return self.flame_default_rating

    # ------------------------------------------------------------------
    # Méthodes abstraites de BaseParser
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des mangas sur Flame Comics.

        Flame Comics n'a pas d'endpoint de recherche dédié : le parseur
        charge ``/series`` (catalogue complet) et filtre côté client sur
        le titre.

        Args:
            query: Terme de recherche.
            page: Numéro de page (ignoré — catalogue unique).

        Returns:
            Liste de :class:`SearchResult` filtrés.

        Raises:
            ParseError: Si le HTML de résultats est inexploitable.
        """
        self.logger.debug(
            "FlameScans search: {query}", query=query
        )

        url = f"{self.base_url}{self.flame_series_path}"

        try:
            rendered = await self.fetch_rendered(
                url,
                wait_until="networkidle",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec recherche FlameScans pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        all_results = self._flame_parse_search_html(
            rendered.html, base_url=rendered.final_url or url
        )

        # Filtre côté client.
        query_lower = query.lower().strip()
        filtered = [
            r for r in all_results
            if query_lower in r.title.lower()
        ]

        self.logger.info(
            "FlameScans search: {n} résultat(s) pour {query!r}",
            n=len(filtered),
            query=query,
        )
        return filtered

    def _flame_parse_search_html(
        self,
        html: str,
        *,
        base_url: str,
    ) -> list[SearchResult]:
        """Parse le HTML du catalogue.

        Args:
            html: HTML rendu.
            base_url: URL de la page (pour résolution relative).

        Returns:
            Liste de :class:`SearchResult`.
        """
        # Priorité 1 : données Next.js.
        next_data = self._flame_extract_next_data(html)
        results: list[SearchResult] = []

        if next_data:
            items = self._flame_navigate_json(
                next_data, "props", "pageProps", "series"
            ) or self._flame_navigate_json(
                next_data, "props", "pageProps", "items"
            )
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    title = item.get("title") or item.get("name")
                    series_id = item.get("id") or item.get("series_id")
                    slug = item.get("slug") or item.get("url")
                    if not title or not series_id:
                        continue
                    slug_part = slug or str(series_id)
                    url = (
                        f"{self.base_url}{self.flame_series_path}/"
                        f"{series_id}/{slug_part}"
                    )
                    cover = item.get("image") or item.get("cover") or item.get("poster")
                    results.append(
                        SearchResult(
                            title=str(title),
                            url=url,
                            site_id=self.config.id,
                            cover_url=str(cover) if cover else None,
                            author=item.get("author"),
                        )
                    )
                if results:
                    self.logger.info(
                        "FlameScans search (Next.js): {n} résultat(s)",
                        n=len(results),
                    )
                    return results

        # Priorité 2 : fallback DOM.
        tree = HTMLParser(html)
        seen_urls: set[str] = set()

        for node in self._flame_all(tree, self.flame_search_item_selector):
            link_node: Node | None = None
            if node.tag == "a" and "/series/" in self._flame_attr(node, "href"):
                link_node = node
            else:
                for candidate in node.css("a"):
                    if "/series/" in self._flame_attr(candidate, "href"):
                        link_node = candidate
                        break

            if link_node is None:
                continue

            href = self._flame_attr(link_node, "href")
            if not href:
                continue
            abs_url = self._flame_abs(href, base_url)
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            title_node = node.css_first(self.flame_search_title_selector)
            title = self._flame_text(title_node) or self._flame_attr(
                link_node, "title"
            )
            if not title:
                title = (
                    self._flame_attr(link_node, "href")
                    .rstrip("/")
                    .rsplit("/", 1)[-1]
                )
                title = title.replace("-", " ").title()
            if not title:
                continue

            cover_node = node.css_first(self.flame_search_cover_selector)
            cover_src = (
                self._flame_attr(cover_node, "data-src")
                or self._flame_attr(cover_node, "data-lazy-src")
                or self._flame_attr(cover_node, "src")
            )
            cover_url = (
                self._flame_abs(cover_src, base_url) if cover_src else None
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
            "FlameScans search (DOM): {n} résultat(s)", n=len(results)
        )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga Flame Comics.

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
            url = f"{self.base_url}{self.flame_series_path}/{url_or_id}"

        self.logger.debug("FlameScans get_manga: {url}", url=url)

        # Tentative 1 : endpoint _next/data (rapide et fiable).
        series_path = self._flame_extract_series_path(url)
        if series_path:
            payload = await self._flame_fetch_next_data(
                f"{series_path}.json"
            )
            if payload:
                page_props = self._flame_navigate_json(
                    payload, "pageProps"
                )
                if isinstance(page_props, dict):
                    series_data = (
                        page_props.get("series")
                        or page_props.get("manga")
                        or page_props.get("data")
                    )
                    if isinstance(series_data, dict):
                        return self._flame_build_manga_from_next_data(
                            series_data, url=url
                        )

        # Tentative 2 : rendu Playwright.
        try:
            rendered = await self.fetch_rendered(
                url,
                wait_until="networkidle",
                wait_for_selector="h1, .series-title, .manga-title",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_manga FlameScans pour {url!r}: {err}",
                url=url,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {url!r}: {exc}"
            ) from exc

        html = rendered.html
        next_data = self._flame_extract_next_data(html)
        tree = HTMLParser(html)

        # Priorité 3 : données Next.js depuis le rendu.
        manga_data = (
            self._flame_navigate_json(
                next_data, "props", "pageProps", "series"
            )
            if next_data
            else None
        )
        if isinstance(manga_data, dict):
            return self._flame_build_manga_from_next_data(
                manga_data, url=url
            )

        # Priorité 4 : fallback DOM.
        return self._flame_build_manga_from_dom(tree, url=url, html=html)

    def _flame_build_manga_from_next_data(
        self,
        data: dict[str, Any],
        *,
        url: str,
    ) -> Manga:
        """Construit un objet :class:`Manga` depuis les données Next.js.

        Args:
            data: Données Next.js du manga.
            url: URL du manga.

        Returns:
            Objet :class:`Manga` hydraté.

        Raises:
            ParseError: Si le titre est absent.
        """
        title = data.get("title") or data.get("name")
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        cover = data.get("image") or data.get("cover") or data.get("poster")
        description = data.get("description") or data.get("synopsis")
        author = data.get("author")
        artist = data.get("artist")
        status_raw = data.get("status")
        status = self._flame_parse_status(str(status_raw) if status_raw else None)
        year = data.get("year") if isinstance(data.get("year"), int) else None

        genres_raw = data.get("genres") or data.get("tags") or []
        genres: list[str] = []
        if isinstance(genres_raw, list):
            for g in genres_raw:
                if isinstance(g, str):
                    genres.append(g)
                elif isinstance(g, dict):
                    name = g.get("name") or g.get("title")
                    if isinstance(name, str):
                        genres.append(name)

        source_id = (
            str(data.get("id"))
            or self._flame_extract_series_slug(url)
        )
        rating = self._flame_detect_rating(genres)

        return Manga(
            id=f"{self.config.id}:{source_id}",
            source_id=source_id,
            site=self.config.id,
            title=str(title),
            alternative_titles=[],
            description=str(description) if description else None,
            author=str(author) if author else None,
            artist=str(artist) if artist else None,
            genres=genres,
            status=status,
            year=year,
            cover_url=str(cover) if cover else None,
            language=self.language,
            content_rating=rating,
            chapters=[],
            url=url,
            updated_at=datetime.now(timezone.utc),
        )

    def _flame_build_manga_from_dom(
        self,
        tree: HTMLParser,
        *,
        url: str,
        html: str,
    ) -> Manga:
        """Construit un objet :class:`Manga` depuis le DOM.

        Args:
            tree: Arbre HTML parsé.
            url: URL du manga.
            html: HTML source.

        Returns:
            Objet :class:`Manga` hydraté.

        Raises:
            ParseError: Si le titre est absent.
        """
        title_node = self._flame_first(tree, self.flame_manga_title_selector)
        title = self._flame_text(title_node)
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        cover_node = self._flame_first(tree, self.flame_manga_cover_selector)
        cover_src = (
            self._flame_attr(cover_node, "data-src")
            or self._flame_attr(cover_node, "src")
        )
        cover_url = self._flame_abs(cover_src, url) if cover_src else None

        description_node = self._flame_first(
            tree, self.flame_manga_description_selector
        )
        description = self._flame_text(description_node) or None

        author_node = self._flame_first(
            tree, self.flame_manga_author_selector
        )
        author = self._flame_text(author_node) or None

        artist_node = self._flame_first(
            tree, self.flame_manga_artist_selector
        )
        artist = self._flame_text(artist_node) or None

        genre_nodes = self._flame_all(
            tree, self.flame_manga_genres_selector
        )
        genres: list[str] = []
        for node in genre_nodes:
            text = self._flame_text(node)
            if text and text not in genres:
                genres.append(text)

        status_node = self._flame_first(
            tree, self.flame_manga_status_selector
        )
        status = self._flame_parse_status(
            self._flame_text(status_node)
        )

        source_id = self._flame_extract_series_slug(url)
        rating = self._flame_detect_rating(genres)

        chapters = self._flame_parse_chapters_from_html(
            html, base_url=url
        )

        return Manga(
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

    def _flame_extract_series_path(self, url: str) -> str | None:
        """Extrait le chemin de série depuis une URL Flame Comics.

        Format : ``/series/{id}/{slug}`` → retourne ``/series/{id}/{slug}``.

        Args:
            url: URL de la série.

        Returns:
            Chemin de série ou ``None``.
        """
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        if "series" in parts:
            idx = parts.index("series")
            remaining = parts[idx:]
            if len(remaining) >= 3:
                return "/" + "/".join(remaining[:3])
            return "/" + "/".join(remaining)
        return None

    @staticmethod
    def _flame_extract_series_slug(url: str) -> str:
        """Extrait un identifiant de série depuis une URL Flame Comics.

        Args:
            url: URL de la série.

        Returns:
            Identifiant composite ``{id}/{slug}`` ou slug seul.
        """
        parts = [p for p in urlparse(url).path.split("/") if p]
        if "series" in parts:
            idx = parts.index("series")
            remaining = parts[idx + 1:]
            if len(remaining) >= 2:
                return f"{remaining[0]}/{remaining[1]}"
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
            "FlameScans get_chapters: {title}", title=manga.title
        )

        # Tentative 1 : endpoint _next/data.
        series_path = self._flame_extract_series_path(str(manga.url))
        if series_path:
            payload = await self._flame_fetch_next_data(
                f"{series_path}.json"
            )
            if payload:
                page_props = self._flame_navigate_json(
                    payload, "pageProps"
                )
                if isinstance(page_props, dict):
                    chapters_data = (
                        page_props.get("chapters")
                        or page_props.get("chapterList")
                    )
                    if isinstance(chapters_data, list):
                        chapters = self._flame_parse_chapters_from_json(
                            chapters_data, base_url=str(manga.url)
                        )
                        if chapters:
                            self.logger.info(
                                "FlameScans get_chapters (Next.js): {n}",
                                n=len(chapters),
                            )
                            return chapters

        # Tentative 2 : rendu Playwright.
        try:
            rendered = await self.fetch_rendered(
                str(manga.url),
                wait_until="networkidle",
                wait_for_selector="a[href*='/series/'], .chapter-list a",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_chapters FlameScans pour {title!r}: {err}",
                title=manga.title,
                err=exc,
            )
            raise ParseError(
                f"get_chapters échoué sur {self.site_id!r} "
                f"pour {manga.title!r}: {exc}"
            ) from exc

        chapters = self._flame_parse_chapters_from_html(
            rendered.html, base_url=rendered.final_url or str(manga.url)
        )
        if not chapters:
            raise ChapterNotFoundError(
                f"Aucun chapitre trouvé pour {manga.url} sur {self.site_id!r}"
            )
        return chapters

    def _flame_parse_chapters_from_json(
        self,
        chapters_data: list[Any],
        *,
        base_url: str,
    ) -> list[Chapter]:
        """Parse la liste des chapitres depuis les données JSON Next.js.

        Args:
            chapters_data: Liste d'objets chapitre.
            base_url: URL de la page manga.

        Returns:
            Liste de :class:`Chapter` triés.
        """
        chapters: list[Chapter] = []
        seen: set[str] = set()
        language = self.language
        series_path = self._flame_extract_series_path(base_url) or ""

        for item in chapters_data:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("name") or ""
            slug = item.get("slug") or item.get("chapter") or item.get("id")
            if not slug:
                continue

            chapter_url = f"{self.base_url}{series_path}/{slug}"
            if chapter_url in seen:
                continue
            seen.add(chapter_url)

            label = title or f"Chapter {slug}"
            number = self._flame_chapter_number(label)
            volume = self._flame_chapter_volume(label)

            source_id = str(slug)
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
                    url=chapter_url,
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
        return chapters

    def _flame_parse_chapters_from_html(
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

        for node in self._flame_all(tree, self.flame_chapter_selector):
            href = self._flame_attr(node, "href")
            if not href or "/series/" not in href:
                continue
            abs_url = self._flame_abs(href, base_url)
            if abs_url in seen:
                continue
            seen.add(abs_url)

            label = self._flame_text(node) or self._flame_attr(node, "title")
            if not label:
                label = abs_url.rstrip("/").rsplit("/", 1)[-1] or abs_url

            number = self._flame_chapter_number(label)
            volume = self._flame_chapter_volume(label)

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
            "FlameScans chapitres: {n}", n=len(chapters)
        )
        return chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre Flame Comics.

        Le site est une SPA Next.js : le rendu Playwright est nécessaire.
        Les images sont extraites en priorité depuis les données Next.js,
        avec fallback sur les sélecteurs CSS.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "FlameScans get_pages: {url}", url=str(chapter.url)
        )
        chapter_url = str(chapter.url)

        # Tentative 1 : endpoint _next/data.
        series_path = self._flame_extract_series_path(chapter_url)
        chapter_slug = chapter_url.rstrip("/").rsplit("/", 1)[-1]
        if series_path:
            payload = await self._flame_fetch_next_data(
                f"{series_path}/{chapter_slug}.json"
            )
            if payload:
                page_props = self._flame_navigate_json(
                    payload, "pageProps"
                )
                if isinstance(page_props, dict):
                    images = (
                        page_props.get("images")
                        or page_props.get("pages")
                        or page_props.get("chapter", {}).get("images")
                    )
                    if isinstance(images, list):
                        urls = self._flame_extract_urls_from_json(images)
                        if urls:
                            return self._flame_build_pages(
                                urls, chapter_url
                            )

        # Tentative 2 : rendu Playwright.
        try:
            rendered = await self.fetch_rendered(
                chapter_url,
                wait_until="networkidle",
                wait_for_selector="div#images img, .reader img, main img",
                timeout=45.0,
                bypass_cloudflare=True,
                block_resources=False,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_pages FlameScans pour {url!r}: {err}",
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

        # Priorité 1 : données Next.js du rendu.
        next_data = self._flame_extract_next_data(html)
        if next_data:
            images = (
                self._flame_navigate_json(
                    next_data, "props", "pageProps", "images"
                )
                or self._flame_navigate_json(
                    next_data, "props", "pageProps", "chapter", "images"
                )
            )
            if isinstance(images, list):
                urls = self._flame_extract_urls_from_json(images)

        # Priorité 2 : sélecteur CSS.
        if not urls:
            for node in self._flame_all(tree, self.flame_page_img_selector):
                src = (
                    self._flame_attr(node, "data-src")
                    or self._flame_attr(node, "data-lazy-src")
                    or self._flame_attr(node, "data-original")
                    or self._flame_attr(node, "src")
                )
                if not src:
                    continue
                abs_url = self._flame_abs(src, chapter_url)
                if abs_url not in urls:
                    urls.append(abs_url)

        if not urls:
            raise ChapterNotFoundError(
                f"Aucune page trouvée pour {chapter_url} sur "
                f"{self.site_id!r}"
            )

        return self._flame_build_pages(urls, chapter_url)

    @staticmethod
    def _flame_extract_urls_from_json(images: list[Any]) -> list[str]:
        """Extrait les URLs d'images depuis une liste JSON.

        Args:
            images: Liste d'entrées (str ou dict).

        Returns:
            Liste d'URLs.
        """
        urls: list[str] = []
        for entry in images:
            if isinstance(entry, str):
                if entry.startswith(("http://", "https://", "/")):
                    urls.append(entry)
            elif isinstance(entry, dict):
                for key in ("url", "src", "image", "link"):
                    val = entry.get(key)
                    if isinstance(val, str) and val.startswith(
                        ("http://", "https://", "/")
                    ):
                        urls.append(val)
                        break
        return urls

    def _flame_build_pages(
        self, urls: list[str], chapter_url: str
    ) -> list[Page]:
        """Construit les objets :class:`Page` depuis une liste d'URLs.

        Args:
            urls: Liste d'URLs d'images.
            chapter_url: URL du chapitre (pour résolution relative).

        Returns:
            Liste de :class:`Page`.
        """
        pages: list[Page] = []
        for idx, url in enumerate(urls):
            clean = url.strip()
            if not clean:
                continue
            clean = self._flame_abs(clean, chapter_url)
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
        return pages

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que Flame Comics est accessible.

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
                        "Health check FlameScans OK: {base} (status={s})",
                        base=base,
                        s=rendered.status,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Health check FlameScans échec sur {base}: {err}",
                    base=base,
                    err=exc,
                )
        self.logger.warning("Health check FlameScans KO (tous domaines)")
        return False
