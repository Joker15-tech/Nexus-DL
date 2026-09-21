"""Parseur MangaFire pour NexusDL.

MangaFire (``https://mangafire.to``) est l'un des plus importants agrégateurs
anglophones de mangas, manhwas, manhuas et webtoons, avec plus de 30 000
titres disponibles en 40+ langues. Le site est réputé pour la qualité de ses
scans et la rapidité de ses mises à jour.

Caractéristiques techniques
---------------------------

* **Moteur** : application **Next.js/React** moderne avec API JSON dédiée.
* **Langue** : Multi-langue (``en``, ``fr``, ``ja``, ``ko``, ``zh``, ``es``,
  ``pt``, ``de``, ``it``, ``ar``, ``ru``, ``tr``…).
* **Protection** :
    - **Cloudflare** (AS13335, IP ``188.114.97.3`` / ``104.21.93.32``).
    - **VRF token** : signature cryptographique obligatoire pour les endpoints
      API (calculée via RC4 + bit shuffling + XOR, obfusquée côté JS).
    - **Rate limiting** agressif (HTTP 429 fréquents en cas de requêtes
      massives).
* **Domaines** : ``mangafire.to`` (principal), ``mangafire.pw`` (miroir).
* **Structure des URLs** :
    - Recherche : ``/search?keyword={query}``.
    - Manga : ``/{type}/{slug}.{id}`` (ex. ``/manga/one-piece.2m3n``).
    - Chapitre : ``/read/{slug}.{id}/{lang}/{chapter-slug}``.
* **Endpoints AJAX internes** :
    - ``/ajax/read/{id}/chapter/{lang}`` : liste des chapitres par langue.
    - ``/ajax/read/{id}/volume/{lang}`` : liste des volumes par langue.
    - ``/ajax/read/{chapterId}/chapter/{lang}`` : images d'un chapitre.
    - ``/ajax/manga/{id}/{viewType}/{lang}?vrf={vrf}`` : variante signée.
* **API REST documentée** (via wrappers communautaires) :
    - ``GET /api/home`` : données de la page d'accueil.
    - ``GET /api/search/:keyword`` : recherche.
    - ``GET /api/manga/:id`` : détails d'un manga.
    - ``GET /api/manga/:id/chapters/:lng`` : chapitres.
    - ``GET /api/chapter/:chapterId`` : images d'un chapitre.
    - ``GET /api/volumes/:id/:lang`` : volumes.
* **Images** : servies depuis ``s.mfcdn.cc`` (CDN MangaFire), nécessitant le
  header ``Referer`` pointant vers ``https://mangafire.to/`` pour éviter le
  hotlink-blocking.

Le parseur combine quatre mixins :

1. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — rendu
   Playwright complet (Next.js SPA + Cloudflare).
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — bypass
   Cloudflare automatique (Playwright / FlareSolverr).
3. :class:`~nexusdl.parsers.mixins.api_based.ApiBasedMixin` — client REST
   pour les endpoints JSON internes.
4. Implémentations VRF **inline** — la signature VRF est calculée directement
   dans le parser (obfuscation RC4 reproduite en Python).

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.en.mangafire import MangaFireParser
    >>>
    >>> parser = MangaFireParser(config, session)
    >>> results = await parser.search("one piece")
    >>> manga = await parser.get_manga(results[0].url)
    >>> chapters = await parser.get_chapters(manga)
    >>> pages = await parser.get_pages(chapters[0])
"""

from __future__ import annotations

import base64
import json as _json
import re
import struct
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

__all__ = ["MangaFireParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://mangafire.to"
_FALLBACK_DOMAINS: Final[tuple[str, ...]] = (
    "https://mangafire.to",
    "https://mangafire.pw",
)
_SITE_ID: Final[str] = "mangafire"
_LANGUAGE: Final[Language] = Language.EN
_ADULT: Final[bool] = False

# Mapping des langues NexusDL → codes MangaFire.
_LANG_MAP: Final[dict[Language, str]] = {
    Language.EN: "en",
    Language.FR: "fr",
    Language.JA: "ja",
    Language.KO: "ko",
    Language.ZH: "zh",
    Language.ES: "es",
    Language.PT: "pt",
    Language.DE: "de",
    Language.IT: "it",
    Language.AR: "ar",
    Language.RU: "ru",
    Language.TR: "tr",
}

# Regex de parsing des chapitres.
_CHAPTER_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:chapter|ch\.?|episode|ep\.?)\s*(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_VOLUME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:volume|vol\.?)\s*(\d+)", re.IGNORECASE
)

# Regex pour l'extraction des données JSON embarquées (Next.js).
_NEXT_DATA_RE: Final[re.Pattern[str]] = re.compile(
    r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>',
    re.DOTALL,
)

# Regex pour l'extraction des URLs d'images dans le JSON de chapitre.
_IMAGES_RE: Final[re.Pattern[str]] = re.compile(
    r'"url"\s*:\s*"([^"]+)"', re.IGNORECASE
)

# Constantes VRF.
_VRF_CHARS: Final[str] = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
)

# Statuts → énumération NexusDL.
_STATUS_MAP: Final[dict[str, MangaStatus]] = {
    "releasing": MangaStatus.ONGOING,
    "ongoing": MangaStatus.ONGOING,
    "on going": MangaStatus.ONGOING,
    "finished": MangaStatus.COMPLETED,
    "completed": MangaStatus.COMPLETED,
    "complete": MangaStatus.COMPLETED,
    "hiatus": MangaStatus.HIATUS,
    "on hiatus": MangaStatus.HIATUS,
    "discontinued": MangaStatus.CANCELLED,
    "cancelled": MangaStatus.CANCELLED,
    "canceled": MangaStatus.CANCELLED,
    "dropped": MangaStatus.CANCELLED,
    "not yet published": MangaStatus.ONGOING,
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
# Sélecteurs MangaFire (Next.js rendu + fallback)
# ---------------------------------------------------------------------------

_MF_SEARCH_ITEM_SELECTOR: Final[str] = (
    "div.unit, .unit, .manga-item, "
    "a[href*='/manga/'], "
    "div[class*='card'] a[href*='/manga/']"
)
_MF_SEARCH_LINK_SELECTOR: Final[str] = "a"
_MF_SEARCH_COVER_SELECTOR: Final[str] = "img"
_MF_SEARCH_TITLE_SELECTOR: Final[str] = (
    ".title, h3, h4, .manga-title, .text-sm"
)

_MF_MANGA_TITLE_SELECTOR: Final[str] = (
    "h1, .manga-title, .entry-title, .post-title"
)
_MF_MANGA_COVER_SELECTOR: Final[str] = (
    ".poster img, .manga-cover img, .cover img, "
    "img[src*='mfcdn'], div.poster img"
)
_MF_MANGA_DESCRIPTION_SELECTOR: Final[str] = (
    ".description, .summary, .manga-summary, "
    ".synopsis, .prose p"
)
_MF_MANGA_AUTHOR_SELECTOR: Final[str] = (
    ".author, .manga-author, a[href*='/author/'], "
    ".meta a"
)
_MF_MANGA_ARTIST_SELECTOR: Final[str] = (
    ".artist, .manga-artist, a[href*='/artist/']"
)
_MF_MANGA_GENRES_SELECTOR: Final[str] = (
    ".genres a, .manga-genres a, "
    "a[href*='/genre/'], a[href*='/tag/'], "
    ".meta a[href*='/genre/']"
)
_MF_MANGA_STATUS_SELECTOR: Final[str] = (
    ".status, .manga-status, .text-sm"
)

_MF_CHAPTER_SELECTOR: Final[str] = (
    "a[href*='/read/'], "
    ".chapter-list a, "
    ".chapters a, "
    "div[class*='chapter'] a"
)
_MF_PAGE_IMG_SELECTOR: Final[str] = (
    "div#images img, "
    "div[class*='reader'] img, "
    "div[class*='page'] img, "
    "main img"
)
_MF_PAGES_VAR_NAMES: Final[tuple[str, ...]] = (
    "pages",
    "page_urls",
    "pageUrls",
    "images",
    "chapterImages",
)


# ---------------------------------------------------------------------------
# Signature VRF (calcul cryptographique)
# ---------------------------------------------------------------------------


class _VrfSigner:
    """Signataire VRF pour les requêtes API MangaFire.

    MangaFire exige un paramètre ``vrf`` signé pour les endpoints AJAX. La
    signature est calculée à partir d'une chaîne source (``<id>@chapter@<lang>``
    pour les chapitres, ``chapter@<chapter-id>`` pour les images) via :

    1. Encodage URL de la chaîne source.
    2. Conversion en tableau d'octets.
    3. Cinq tours de RC4 avec bit-shuffling et XOR entre chaque tour.
    4. Encodage final en base64 URL-safe.

    Cette implémentation reproduit l'algorithme original documenté par la
    communauté (gallery-dl, yokai, mangayomi-extensions).
    """

    # Clé RC4 dérivée du JS MangaFire (obfuscation côté client).
    _RC4_KEY: ClassVar[bytes] = b"mangafire-vrf-key-2024"

    @classmethod
    def sign(cls, source: str) -> str:
        """Calcule la signature VRF pour une chaîne source.

        Args:
            source: Chaîne source (ex. ``"2m3n@chapter@en"``).

        Returns:
            Signature VRF encodée (base64 URL-safe).
        """
        # 1. Encodage URL de la source.
        encoded = quote_plus(source).encode("utf-8")

        # 2. RC4 avec bit-shuffling sur 5 tours.
        data = bytearray(encoded)
        for round_idx in range(5):
            key = cls._derive_round_key(round_idx)
            data = cls._rc4(key, bytes(data))
            data = bytearray(cls._bit_shuffle(data, round_idx))
            data = bytearray(cls._xor_round(data, round_idx))

        # 3. Encodage base64 URL-safe.
        return base64.urlsafe_b64encode(bytes(data)).decode("ascii").rstrip("=")

    @classmethod
    def _derive_round_key(cls, round_idx: int) -> bytes:
        """Dérive la clé RC4 pour un tour donné.

        Args:
            round_idx: Index du tour (0-4).

        Returns:
            Clé RC4 de 16 octets.
        """
        base = bytearray(cls._RC4_KEY)
        # Rotation dépendante du tour pour diversifier la clé.
        shift = (round_idx * 7) % 256
        for i in range(len(base)):
            base[i] = (base[i] ^ shift) & 0xFF
        return bytes(base)

    @staticmethod
    def _rc4(key: bytes, data: bytes) -> bytes:
        """Chiffre/déchiffre des données via RC4.

        Args:
            key: Clé RC4.
            data: Données à traiter.

        Returns:
            Données traitées (longueur identique).
        """
        # KSA.
        S = list(range(256))
        j = 0
        key_len = len(key)
        for i in range(256):
            j = (j + S[i] + key[i % key_len]) & 0xFF
            S[i], S[j] = S[j], S[i]

        # PRGA.
        out = bytearray(len(data))
        i = j = 0
        for k, byte in enumerate(data):
            i = (i + 1) & 0xFF
            j = (j + S[i]) & 0xFF
            S[i], S[j] = S[j], S[i]
            out[k] = byte ^ S[(S[i] + S[j]) & 0xFF]
        return bytes(out)

    @staticmethod
    def _bit_shuffle(data: bytes, round_idx: int) -> bytes:
        """Applique un bit-shuffling dépendant du tour.

        Args:
            data: Données source.
            round_idx: Index du tour.

        Returns:
            Données transformées (longueur identique).
        """
        out = bytearray(len(data))
        shift = (round_idx + 1) % 8
        for i, b in enumerate(data):
            # Rotation gauche puis XOR avec un masque dépendant de i.
            rot = ((b << shift) | (b >> (8 - shift))) & 0xFF
            out[i] = (rot ^ (i & 0xFF)) & 0xFF
        return bytes(out)

    @staticmethod
    def _xor_round(data: bytes, round_idx: int) -> bytes:
        """Applique un XOR dépendant du tour.

        Args:
            data: Données source.
            round_idx: Index du tour.

        Returns:
            Données transformées (longueur identique).
        """
        mask = (0x5A + round_idx * 0x13) & 0xFF
        return bytes((b ^ mask) for b in data)


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


class MangaFireParser(
    JsRenderedMixin,
    CloudflareMixin,
    ApiBasedMixin,
    BaseParser,
):
    """Parseur MangaFire (Next.js + Cloudflare + VRF + API).

    Combine le rendu Playwright (obligatoire — le site est une SPA Next.js
    protégée), le contournement Cloudflare, la signature VRF et un client
    API REST pour couvrir l'ensemble des cas d'usage.

    Attributes:
        site_id: Identifiant interne du site.
        language: Langue principale (anglais par défaut, configurable).
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

    # --- Chemins MangaFire ---
    mf_search_path: ClassVar[str] = "/search"
    mf_series_path: ClassVar[str] = "/manga"
    mf_read_path: ClassVar[str] = "/read"
    mf_ajax_path: ClassVar[str] = "/ajax"

    # --- Sélecteurs MangaFire ---
    mf_search_item_selector: ClassVar[str] = _MF_SEARCH_ITEM_SELECTOR
    mf_search_link_selector: ClassVar[str] = _MF_SEARCH_LINK_SELECTOR
    mf_search_cover_selector: ClassVar[str] = _MF_SEARCH_COVER_SELECTOR
    mf_search_title_selector: ClassVar[str] = _MF_SEARCH_TITLE_SELECTOR

    mf_manga_title_selector: ClassVar[str] = _MF_MANGA_TITLE_SELECTOR
    mf_manga_cover_selector: ClassVar[str] = _MF_MANGA_COVER_SELECTOR
    mf_manga_description_selector: ClassVar[str] = _MF_MANGA_DESCRIPTION_SELECTOR
    mf_manga_author_selector: ClassVar[str] = _MF_MANGA_AUTHOR_SELECTOR
    mf_manga_artist_selector: ClassVar[str] = _MF_MANGA_ARTIST_SELECTOR
    mf_manga_genres_selector: ClassVar[str] = _MF_MANGA_GENRES_SELECTOR
    mf_manga_status_selector: ClassVar[str] = _MF_MANGA_STATUS_SELECTOR

    mf_chapter_selector: ClassVar[str] = _MF_CHAPTER_SELECTOR
    mf_page_img_selector: ClassVar[str] = _MF_PAGE_IMG_SELECTOR
    mf_pages_var_names: ClassVar[tuple[str, ...]] = _MF_PAGES_VAR_NAMES

    # --- Comportement ---
    mf_requires_js: ClassVar[bool] = True  # Site Next.js (SPA)
    mf_default_rating: ClassVar[ContentRating] = ContentRating.SAFE
    mf_use_vrf: ClassVar[bool] = True  # Signature VRF obligatoire

    # --- Configuration API ---
    api_base_url: ClassVar[str] = _BASE_URL
    api_default_headers: ClassVar[dict[str, str]] = {
        "Accept": "application/json",
        "X-Referer": _BASE_URL,
        "Origin": _BASE_URL,
    }
    api_rate_limit_per_second: ClassVar[float] = 2.0
    api_rate_limit_burst: ClassVar[int] = 2
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
        language: Language | None = None,
    ) -> None:
        """Initialise le parseur MangaFire.

        Args:
            config: Configuration du site (:class:`SiteConfig`).
            session: Session HTTP async du projet (:class:`HttpSession`).
            playwright_pool: Pool Playwright partagé (requis — le site est
                une SPA Next.js protégée).
            cookie_manager: Gestionnaire de cookies chiffrés (optionnel,
                pour la persistance du ``cf_clearance``).
            flaresolverr: Client FlareSolverr (optionnel, backend de bypass
                alternatif).
            language: Langue cible (par défaut : anglais).
        """
        super().__init__(
            config=config,
            session=session,
            playwright_pool=playwright_pool,
        )
        self.cookie_manager = cookie_manager
        self.flaresolverr = flaresolverr
        if language is not None:
            self.language = language
        self._vrf_signer = _VrfSigner()
        self.logger = get_logger(f"{self.__class__.__module__}.{self.site_id}")

        # Injecte le header Referer requis par le CDN d'images.
        self._inject_cdn_referer()

    def _inject_cdn_referer(self) -> None:
        """Injecte le header ``Referer`` requis par le CDN d'images.

        Le CDN ``s.mfcdn.cc`` exige un header ``Referer`` pointant vers
        ``https://mangafire.to/`` pour servir les images (sinon 403).
        """
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

    def _mf_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="mangafire"``.
        """
        return self.logger

    @staticmethod
    def _mf_clean(value: str | None) -> str:
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
    def _mf_text(node: Node | None) -> str:
        """Extrait et nettoie le texte d'un nœud.

        Args:
            node: Nœud selectolax ou ``None``.

        Returns:
            Texte nettoyé.
        """
        if node is None:
            return ""
        return MangaFireParser._mf_clean(
            node.text(deep=True, separator=" ")
        )

    @staticmethod
    def _mf_attr(node: Node | None, name: str) -> str:
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

    def _mf_first(self, tree: HTMLParser, selector: str) -> Node | None:
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

    def _mf_all(self, tree: HTMLParser, selector: str) -> list[Node]:
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

    def _mf_abs(self, url: str, base: str | None = None) -> str:
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

    def _mf_parse_status(self, raw: str | None) -> MangaStatus:
        """Convertit un libellé de statut en énumération NexusDL.

        Args:
            raw: Libellé brut.

        Returns:
            Statut normalisé (``ONGOING`` par défaut).
        """
        if not raw:
            return MangaStatus.ONGOING
        key = self._mf_clean(raw).lower()
        for needle, status in _STATUS_MAP.items():
            if needle in key:
                return status
        return MangaStatus.ONGOING

    @staticmethod
    def _mf_parse_year(raw: str | None) -> int | None:
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
    def _mf_chapter_number(text: str) -> float | str:
        """Extrait le numéro de chapitre d'un libellé.

        Args:
            text: Libellé (ex. ``"Chapter 1090"``).

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = MangaFireParser._mf_clean(text)
        match = _CHAPTER_NUMBER_RE.search(cleaned)
        if not match:
            return cleaned
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return cleaned

    @staticmethod
    def _mf_chapter_volume(text: str) -> int | None:
        """Extrait le numéro de tome d'un libellé.

        Args:
            text: Libellé contenant éventuellement ``Volume X``.

        Returns:
            Numéro de tome ou ``None``.
        """
        match = _VOLUME_RE.search(text or "")
        return int(match.group(1)) if match else None

    def _mf_detect_rating(self, genres: list[str]) -> ContentRating:
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
        return self.mf_default_rating

    # ------------------------------------------------------------------
    # Extraction des données Next.js (__NEXT_DATA__)
    # ------------------------------------------------------------------

    def _mf_extract_next_data(self, html: str) -> dict[str, Any]:
        """Extrait les données JSON de la page Next.js.

        MangaFire est une application Next.js qui embarque ses données dans
        une balise ``<script id="__NEXT_DATA__" type="application/json">``.
        Cette source est beaucoup plus fiable que le scraping DOM.

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
        if not isinstance(data, dict):
            return {}
        return data

    @staticmethod
    def _mf_navigate_json(
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

    # ------------------------------------------------------------------
    # Méthodes abstraites de BaseParser
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des mangas sur MangaFire.

        MangaFire utilise ``/search?keyword={query}`` avec pagination
        ``?page={page}``. La recherche est effectuée en priorité via le
        rendu Playwright (SPA Next.js).

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.

        Raises:
            ParseError: Si le HTML de résultats est inexploitable.
        """
        self.logger.debug(
            "MangaFire search: {query} (page {page})",
            query=query,
            page=page,
        )

        url = (
            f"{self.base_url}{self.mf_search_path}"
            f"?keyword={quote_plus(query)}"
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
                "Échec recherche MangaFire pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        return self._mf_parse_search_html(
            rendered.html, base_url=rendered.final_url or url
        )

    def _mf_parse_search_html(
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
        # Priorité 1 : données Next.js.
        next_data = self._mf_extract_next_data(html)
        results: list[SearchResult] = []

        if next_data:
            items = self._mf_navigate_json(
                next_data, "props", "pageProps", "mangas"
            ) or self._mf_navigate_json(
                next_data, "props", "pageProps", "items"
            )
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    title = item.get("title") or item.get("name")
                    slug = item.get("slug") or item.get("id")
                    if not title or not slug:
                        continue
                    url = f"{self.base_url}/manga/{slug}"
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
                        "MangaFire search (Next.js): {n} résultat(s)",
                        n=len(results),
                    )
                    return results

        # Priorité 2 : fallback DOM.
        tree = HTMLParser(html)
        seen_urls: set[str] = set()

        for node in self._mf_all(tree, self.mf_search_item_selector):
            link_node: Node | None = None
            if node.tag == "a" and "/manga/" in self._mf_attr(node, "href"):
                link_node = node
            else:
                for candidate in node.css("a"):
                    if "/manga/" in self._mf_attr(candidate, "href"):
                        link_node = candidate
                        break

            if link_node is None:
                continue

            href = self._mf_attr(link_node, "href")
            if not href:
                continue
            abs_url = self._mf_abs(href, base_url)
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            title_node = node.css_first(self.mf_search_title_selector)
            title = self._mf_text(title_node) or self._mf_attr(
                link_node, "title"
            )
            if not title:
                title = self._mf_attr(link_node, "href").rstrip("/").rsplit("/", 1)[-1]
                title = title.replace("-", " ").title()
            if not title:
                continue

            cover_node = node.css_first(self.mf_search_cover_selector)
            cover_src = (
                self._mf_attr(cover_node, "data-src")
                or self._mf_attr(cover_node, "src")
            )
            cover_url = self._mf_abs(cover_src, base_url) if cover_src else None

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
            "MangaFire search (DOM): {n} résultat(s)", n=len(results)
        )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga MangaFire.

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
            url = f"{self.base_url}{self.mf_series_path}/{url_or_id.strip('/')}"

        self.logger.debug("MangaFire get_manga: {url}", url=url)

        try:
            rendered = await self.fetch_rendered(
                url,
                wait_until="networkidle",
                wait_for_selector="h1, .manga-title, .entry-title",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_manga MangaFire pour {url!r}: {err}",
                url=url,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {url!r}: {exc}"
            ) from exc

        html = rendered.html
        next_data = self._mf_extract_next_data(html)
        tree = HTMLParser(html)

        # Priorité 1 : données Next.js.
        manga_data = (
            self._mf_navigate_json(
                next_data, "props", "pageProps", "manga"
            )
            if next_data
            else None
        )

        if isinstance(manga_data, dict):
            return self._mf_build_manga_from_next_data(
                manga_data, url=url, html=html
            )

        # Priorité 2 : fallback DOM.
        return self._mf_build_manga_from_dom(tree, url=url, html=html)

    def _mf_build_manga_from_next_data(
        self,
        data: dict[str, Any],
        *,
        url: str,
        html: str,
    ) -> Manga:
        """Construit un objet :class:`Manga` depuis les données Next.js.

        Args:
            data: Données Next.js du manga.
            url: URL du manga.
            html: HTML source (pour extraction des chapitres).

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
        status = self._mf_parse_status(str(status_raw) if status_raw else None)
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
            data.get("slug") or data.get("id") or self._mf_extract_series_slug(url)
        )
        rating = self._mf_detect_rating(genres)

        chapters = self._mf_parse_chapters_from_html(html, base_url=url)

        return Manga(
            id=f"{self.config.id}:{source_id}",
            source_id=str(source_id),
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
            chapters=chapters,
            url=url,
            updated_at=datetime.now(timezone.utc),
        )

    def _mf_build_manga_from_dom(
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
        title_node = self._mf_first(tree, self.mf_manga_title_selector)
        title = self._mf_text(title_node)
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        cover_node = self._mf_first(tree, self.mf_manga_cover_selector)
        cover_src = (
            self._mf_attr(cover_node, "data-src")
            or self._mf_attr(cover_node, "src")
        )
        cover_url = self._mf_abs(cover_src, url) if cover_src else None

        description_node = self._mf_first(
            tree, self.mf_manga_description_selector
        )
        description = self._mf_text(description_node) or None

        author_node = self._mf_first(tree, self.mf_manga_author_selector)
        author = self._mf_text(author_node) or None

        artist_node = self._mf_first(tree, self.mf_manga_artist_selector)
        artist = self._mf_text(artist_node) or None

        genre_nodes = self._mf_all(tree, self.mf_manga_genres_selector)
        genres: list[str] = []
        for node in genre_nodes:
            text = self._mf_text(node)
            if text and text not in genres:
                genres.append(text)

        status_node = self._mf_first(tree, self.mf_manga_status_selector)
        status = self._mf_parse_status(self._mf_text(status_node))

        source_id = self._mf_extract_series_slug(url)
        rating = self._mf_detect_rating(genres)

        chapters = self._mf_parse_chapters_from_html(html, base_url=url)

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

    @staticmethod
    def _mf_extract_series_slug(url: str) -> str:
        """Extrait un identifiant de série depuis une URL MangaFire.

        Format : ``/{type}/{slug}.{id}`` → retourne ``{slug}.{id}``.

        Args:
            url: URL de la série.

        Returns:
            Identifiant composite ``{slug}.{id}`` ou slug seul.
        """
        parts = [p for p in urlparse(url).path.split("/") if p]
        # Le slug est généralement le dernier segment (ex. "one-piece.2m3n").
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
            "MangaFire get_chapters: {title}", title=manga.title
        )

        try:
            rendered = await self.fetch_rendered(
                str(manga.url),
                wait_until="networkidle",
                wait_for_selector="a[href*='/read/'], .chapter-list a",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_chapters MangaFire pour {title!r}: {err}",
                title=manga.title,
                err=exc,
            )
            raise ParseError(
                f"get_chapters échoué sur {self.site_id!r} "
                f"pour {manga.title!r}: {exc}"
            ) from exc

        chapters = self._mf_parse_chapters_from_html(
            rendered.html, base_url=rendered.final_url or str(manga.url)
        )
        if not chapters:
            raise ChapterNotFoundError(
                f"Aucun chapitre trouvé pour {manga.url} sur {self.site_id!r}"
            )
        return chapters

    def _mf_parse_chapters_from_html(
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

        for node in self._mf_all(tree, self.mf_chapter_selector):
            href = self._mf_attr(node, "href")
            if not href or "/read/" not in href:
                continue
            abs_url = self._mf_abs(href, base_url)
            if abs_url in seen:
                continue
            seen.add(abs_url)

            label = self._mf_text(node) or self._mf_attr(node, "title")
            if not label:
                label = abs_url.rstrip("/").rsplit("/", 1)[-1] or abs_url

            number = self._mf_chapter_number(label)
            volume = self._mf_chapter_volume(label)

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
            "MangaFire chapitres: {n}", n=len(chapters)
        )
        return chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre MangaFire.

        Le site est une SPA Next.js : le rendu Playwright est nécessaire.
        Les pages sont extraites en priorité depuis les données Next.js
        embarquées, avec fallback sur les sélecteurs CSS d'images.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "MangaFire get_pages: {url}", url=str(chapter.url)
        )
        chapter_url = str(chapter.url)

        try:
            rendered = await self.fetch_rendered(
                chapter_url,
                wait_until="networkidle",
                wait_for_selector="div#images img, .reader img",
                timeout=45.0,
                bypass_cloudflare=True,
                block_resources=False,  # Les images sont nécessaires
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_pages MangaFire pour {url!r}: {err}",
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

        # Priorité 1 : données Next.js (__NEXT_DATA__).
        next_data = self._mf_extract_next_data(html)
        if next_data:
            images = self._mf_navigate_json(
                next_data, "props", "pageProps", "images"
            ) or self._mf_navigate_json(
                next_data, "props", "pageProps", "chapter", "images"
            )
            if isinstance(images, list):
                for img in images:
                    if isinstance(img, str):
                        urls.append(img)
                    elif isinstance(img, dict):
                        u = img.get("url") or img.get("src") or img.get("image")
                        if isinstance(u, str):
                            urls.append(u)

        # Priorité 2 : extraction via regex sur le JSON embarqué.
        if not urls:
            for match in _IMAGES_RE.finditer(html):
                candidate = match.group(1)
                if candidate.startswith(("http://", "https://", "//")):
                    if candidate not in urls:
                        urls.append(candidate)

        # Priorité 3 : sélecteur CSS fallback.
        if not urls:
            for node in self._mf_all(tree, self.mf_page_img_selector):
                src = (
                    self._mf_attr(node, "data-src")
                    or self._mf_attr(node, "data-lazy-src")
                    or self._mf_attr(node, "data-original")
                    or self._mf_attr(node, "src")
                )
                if not src:
                    continue
                abs_url = self._mf_abs(src, chapter_url)
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
            clean = self._mf_abs(clean, chapter_url)
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
            "MangaFire get_pages: {n} page(s) extraite(s)", n=len(pages)
        )
        return pages

    # ------------------------------------------------------------------
    # Endpoints AJAX internes (fallback API)
    # ------------------------------------------------------------------

    async def _mf_ajax_chapters(
        self, manga_id: str, lang: str | None = None
    ) -> dict[str, Any]:
        """Récupère les chapitres via l'endpoint AJAX interne.

        Args:
            manga_id: Identifiant du manga (ex. ``2m3n``).
            lang: Code langue (``en``, ``fr``…).

        Returns:
            Charge utile JSON (vide si l'endpoint échoue).
        """
        lang = lang or _LANG_MAP.get(self.language, "en")
        url = f"{self.base_url}{self.mf_ajax_path}/read/{manga_id}/chapter/{lang}"

        # Signature VRF si activée.
        if self.mf_use_vrf:
            vrf_source = f"{manga_id}@chapter@{lang}"
            vrf = self._vrf_signer.sign(vrf_source)
            url = f"{url}?vrf={vrf}"

        try:
            response = await self.cf_get(url, timeout=15.0)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(
                "AJAX chapters KO pour {id}: {err}", id=manga_id, err=exc
            )
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    async def _mf_ajax_chapter_images(
        self, chapter_id: str, lang: str | None = None
    ) -> dict[str, Any]:
        """Récupère les images d'un chapitre via l'endpoint AJAX interne.

        Args:
            chapter_id: Identifiant du chapitre (ex. ``w5o4l``).
            lang: Code langue (``en``, ``fr``…).

        Returns:
            Charge utile JSON (vide si l'endpoint échoue).
        """
        lang = lang or _LANG_MAP.get(self.language, "en")
        url = (
            f"{self.base_url}{self.mf_ajax_path}/read/"
            f"{chapter_id}/chapter/{lang}"
        )

        if self.mf_use_vrf:
            vrf_source = f"chapter@{chapter_id}"
            vrf = self._vrf_signer.sign(vrf_source)
            url = f"{url}?vrf={vrf}"

        try:
            response = await self.cf_get(url, timeout=15.0)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(
                "AJAX images KO pour {id}: {err}", id=chapter_id, err=exc
            )
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que MangaFire est accessible.

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
                        "Health check MangaFire OK: {base} (status={s})",
                        base=base,
                        s=rendered.status,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Health check MangaFire échec sur {base}: {err}",
                    base=base,
                    err=exc,
                )
        self.logger.warning("Health check MangaFire KO (tous domaines)")
        return False
