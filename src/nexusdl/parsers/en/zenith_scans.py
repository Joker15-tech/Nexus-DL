"""Parseur Zenith Scans pour NexusDL.

Zenith Scans (``https://zenithscans.com``) est un site de scanlation turc
utilisant le thème WordPress **MangaThemesia**, le même moteur que Asura
Scans, Flame Comics, Rizz Comic, etc. Le site héberge principalement des
manhwa et webtoons traduits en turc.

Caractéristiques techniques
---------------------------

* **Moteur** : MangaThemesia (WordPress + thème custom).
* **Langue** : Turc (``tr``).
* **Protection** : Cloudflare (IP ``172.67.157.201``, AS13335 Cloudflare).
* **Domaines** : ``zenithscans.com`` (domaine principal).
* **API interne** : le thème MangaThemesia expose des endpoints AJAX
  WordPress (``admin-ajax.php``) pour la pagination et la recherche.
* **Images** : servies depuis le même domaine ou un CDN (le mixin
  MangaThemesia gère la résolution ``data-src`` / ``data-lazy-src``).

Le parseur combine trois mixins :

1. :class:`~nexusdl.parsers.mixins.mangathemesia.MangaThemesiaMixin` — pour
   les implémentations ``search`` / ``get_manga`` / ``get_chapters`` /
   ``get_pages`` basées sur les sélecteurs CSS MangaThemesia standards.
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — pour le
   contournement Cloudflare automatique (Playwright / FlareSolverr).
3. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — pour le
   rendu Playwright si nécessaire (fallback pour les pages dynamiques).

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.en.zenith_scans import ZenithScansParser
    >>>
    >>> parser = ZenithScansParser(config, session)
    >>> results = await parser.search("solo leveling")
    >>> manga = await parser.get_manga(results[0].url)
    >>> chapters = await parser.get_chapters(manga)
    >>> pages = await parser.get_pages(chapters[0])
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, ClassVar, Final
from urllib.parse import quote_plus, urljoin, urlparse

from selectolax.parser import HTMLParser

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

__all__ = ["ZenithScansParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://zenithscans.com"
_SITE_ID: Final[str] = "zenith_scans"
_LANGUAGE: Final[Language] = Language.TR
_ADULT: Final[bool] = False

# Regex de parsing des chapitres.
_CHAPTER_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:bölüm|chapter|ch\.?|episode|ep\.?)\s*(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_VOLUME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:cilt|volume|vol\.?)\s*(\d+)", re.IGNORECASE
)
_RELATIVE_DATE_RE: Final[re.Pattern[str]] = re.compile(
    r"(\d+)\s*(saniye|dakika|saat|gün|hafta|ay|yıl|"
    r"second|minute|hour|day|week|month|year)s?\s*(önce|ago)?",
    re.IGNORECASE,
)

# Statuts turcs → énumération NexusDL.
_STATUS_MAP_TR: Final[dict[str, MangaStatus]] = {
    "devam ediyor": MangaStatus.ONGOING,
    "ongoing": MangaStatus.ONGOING,
    "tamamlandı": MangaStatus.COMPLETED,
    "completed": MangaStatus.COMPLETED,
    "tamamlanan": MangaStatus.COMPLETED,
    "bitmiş": MangaStatus.COMPLETED,
    "ara verildi": MangaStatus.HIATUS,
    "hiatus": MangaStatus.HIATUS,
    "iptal edildi": MangaStatus.CANCELLED,
    "cancelled": MangaStatus.CANCELLED,
    "dropped": MangaStatus.CANCELLED,
}

# Genres adultes (pour détection de classification).
_ADULT_GENRE_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "hentai",
        "adult",
        "yetiskin",
        "yetişkin",
        "ecchi",
        "smut",
        "mature",
        "18+",
        "porn",
        "erotik",
        "erotique",
        "pornografi",
    }
)

# Sélecteurs spécifiques à Zenith Scans (surchargeables).
_ZENITH_SEARCH_ITEM_SELECTOR: Final[str] = (
    ".list-item, .group, .manga-item, .bsx, article, .uta"
)
_ZENITH_CHAPTER_SELECTOR: Final[str] = (
    ".chapters a, .chapter-list a, "
    "a[href*='/read/'], a[href*='/bolum/'], "
    "a[href*='/chapter/'], "
    "li:has(.chbox .eph-num):not(:has(.ch-price-side)) a"
)
_ZENITH_PAGE_IMG_SELECTOR: Final[str] = (
    ".chapter-page img, .page-image img, "
    ".reading-content img, .reader img, "
    "#readerarea img, .main-reading-area img"
)


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


class ZenithScansParser(
    JsRenderedMixin,
    CloudflareMixin,
    MangaThemesiaMixin,
    BaseParser,
):
    """Parseur Zenith Scans (MangaThemesia + Cloudflare).

    Combine le rendu Playwright (fallback), le contournement Cloudflare et
    les implémentations MangaThemesia pour fournir une couverture complète
    du site.

    Attributes:
        site_id: Identifiant interne du site.
        language: Langue principale (turc).
        adult: Contenu adulte (``False``).
        base_url: URL racine du site.
    """

    # --- Métadonnées du parser ---
    site_id: ClassVar[str] = _SITE_ID
    language: ClassVar[Language] = _LANGUAGE
    adult: ClassVar[bool] = _ADULT

    # --- URLs de base ---
    base_url: ClassVar[str] = _BASE_URL
    fools_base_url: ClassVar[str | None] = None  # Non utilisé (pas FoolSlide)

    # --- Configuration MangaThemesia ---
    mt_base_url: ClassVar[str] = _BASE_URL
    mt_search_path: ClassVar[str] = "/?s="
    mt_search_query_param: ClassVar[str] = "s"
    mt_search_method: ClassVar[str] = "GET"
    mt_series_path: ClassVar[str] = "/manga"
    mt_read_path: ClassVar[str] = "/read"

    # --- Sélecteurs MangaThemesia (standards + ajustements Zenith) ---
    mt_search_item_selector: ClassVar[str] = _ZENITH_SEARCH_ITEM_SELECTOR
    mt_search_link_selector: ClassVar[str] = "a"
    mt_search_cover_selector: ClassVar[str] = "img"
    mt_search_title_selector: ClassVar[str] = ".title, h3, h4, .tt, .entry-title"
    mt_search_author_selector: ClassVar[str] = ".author, .artist, .mg_author"

    mt_manga_title_selector: ClassVar[str] = (
        "h1, .entry-title, .manga-title, .post-title, .ts-breadcrumb li:last-child"
    )
    mt_manga_cover_selector: ClassVar[str] = (
        ".thumb img, .manga-cover img, .summary_image img, "
        ".cover img, .thumbook img"
    )
    mt_manga_description_selector: ClassVar[str] = (
        ".summary__content, .manga-summary, .description, "
        ".entry-content p, .desc"
    )
    mt_manga_author_selector: ClassVar[str] = (
        ".author, .manga-author, a[href*='/author/'], "
        "a[href*='/yazar/'], .tsinfo .imptdt:contains('Yazar') a"
    )
    mt_manga_artist_selector: ClassVar[str] = (
        ".artist, .manga-artist, a[href*='/artist/'], "
        "a[href*='/cizer/'], .tsinfo .imptdt:contains('Çizer') a"
    )
    mt_manga_genres_selector: ClassVar[str] = (
        ".genres a, .manga-genres a, a[href*='/genre/'], "
        "a[href*='/tur/'], .mgen a"
    )
    mt_manga_status_selector: ClassVar[str] = (
        ".status, .manga-status, .post-status, .tsinfo .imptdt:contains('Durum') i"
    )
    mt_manga_year_selector: ClassVar[str] = (
        ".year, .manga-year, .post-year, .tsinfo .imptdt:contains('Yıl') i"
    )

    mt_chapter_selector: ClassVar[str] = _ZENITH_CHAPTER_SELECTOR
    mt_page_img_selector: ClassVar[str] = _ZENITH_PAGE_IMG_SELECTOR
    mt_pages_var_names: ClassVar[tuple[str, ...]] = (
        "pages",
        "page_urls",
        "pageUrls",
        "images",
        "chapterImages",
        "ts_reader",
    )

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
    cf_challenge_timeout: ClassVar[float] = 30.0
    cf_clearance_ttl: ClassVar[int] = 1800

    # --- Configuration JsRendered ---
    js_rendered: ClassVar[bool] = True
    default_wait_until: ClassVar[str] = "domcontentloaded"
    default_render_timeout: ClassVar[float] = 30.0
    default_navigation_timeout: ClassVar[float] = 45.0
    block_resources_by_default: ClassVar[bool] = True
    locale: ClassVar[str] = "tr-TR"
    timezone_id: ClassVar[str] = "Europe/Istanbul"
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
        """Initialise le parseur Zenith Scans.

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
    # Méthodes abstraites de BaseParser — délégation aux mixins
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des mangas sur Zenith Scans.

        Délègue à :meth:`MangaThemesiaMixin.mt_search` avec bypass
        Cloudflare automatique via :class:`CloudflareMixin`.

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.

        Raises:
            ParseError: Si le HTML de résultats est inexploitable.
        """
        self.logger.debug(
            "ZenithScans search: {query} (page {page})",
            query=query,
            page=page,
        )
        try:
            return await self.mt_search(query, page=page)
        except ParseError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec recherche ZenithScans pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga Zenith Scans.

        Args:
            url_or_id: URL absolue ou slug de la série.

        Returns:
            Objet :class:`Manga` hydraté.

        Raises:
            MangaNotFoundError: Si le manga est inaccessible.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug("ZenithScans get_manga: {url}", url=url_or_id)
        try:
            return await self.mt_get_manga(url_or_id)
        except MangaNotFoundError:
            raise
        except ParseError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_manga ZenithScans pour {url!r}: {err}",
                url=url_or_id,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {url_or_id!r}: {exc}"
            ) from exc

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
            "ZenithScans get_chapters: {title}", title=manga.title
        )
        try:
            return await self.mt_get_chapters(manga)
        except ChapterNotFoundError:
            raise
        except ParseError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_chapters ZenithScans pour {title!r}: {err}",
                title=manga.title,
                err=exc,
            )
            raise ParseError(
                f"get_chapters échoué sur {self.site_id!r} "
                f"pour {manga.title!r}: {exc}"
            ) from exc

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "ZenithScans get_pages: {url}", url=str(chapter.url)
        )
        try:
            return await self.mt_get_pages(chapter)
        except ChapterNotFoundError:
            raise
        except ParseError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_pages ZenithScans pour {url!r}: {err}",
                url=str(chapter.url),
                err=exc,
            )
            raise ParseError(
                f"get_pages échoué sur {self.site_id!r} "
                f"pour {chapter.url!r}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Surcharges spécifiques à Zenith Scans
    # ------------------------------------------------------------------

    def _mt_parse_status(self, raw: str | None) -> MangaStatus:
        """Convertit un statut turc MangaThemesia en énumération NexusDL.

        Zenith Scans affiche les statuts en turc (``Devam Ediyor``,
        ``Tamamlandı``, etc.). Cette surcharge étend la table standard du
        mixin MangaThemesia avec les libellés turcs.

        Args:
            raw: Libellé brut du statut.

        Returns:
            Statut normalisé (``ONGOING`` par défaut).
        """
        if not raw:
            return MangaStatus.ONGOING
        cleaned = self._mt_clean(raw).lower()
        for needle, status in _STATUS_MAP_TR.items():
            if needle in cleaned:
                return status
        # Fallback sur la table du mixin parent.
        return super()._mt_parse_status(raw)

    def _mt_chapter_number(self, text: str) -> float | str:
        """Extrait le numéro de chapitre (support ``Bölüm X`` turc).

        Args:
            text: Libellé du chapitre.

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = self._mt_clean(text)
        match = _CHAPTER_NUMBER_RE.search(cleaned)
        if not match:
            # Fallback sur le pattern générique du mixin parent.
            return super()._mt_chapter_number(text)
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return cleaned

    def _mt_chapter_volume(self, text: str) -> int | None:
        """Extrait le numéro de tome (support ``Cilt X`` turc).

        Args:
            text: Libellé du chapitre.

        Returns:
            Numéro de tome ou ``None``.
        """
        match = _VOLUME_RE.search(text or "")
        if match:
            return int(match.group(1))
        return super()._mt_chapter_volume(text)

    def _mt_detect_rating(self, genres: list[str]) -> ContentRating:
        """Détermine la classification à partir des genres turcs.

        Args:
            genres: Liste des genres du manga.

        Returns:
            Classification détectée.
        """
        lowered = {g.lower() for g in genres}
        if lowered & _ADULT_GENRE_MARKERS:
            if {"hentai", "porn", "18+", "erotik", "pornografi"} & lowered:
                return ContentRating.PORNOGRAPHIC
            return ContentRating.EROTICA
        return self.mt_default_rating

    def _mt_parse_relative_date(self, raw: str | None) -> datetime | None:
        """Parse une date relative turque (``3 gün önce``).

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
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        table: dict[str, timedelta] = {
            "saniye": timedelta(seconds=amount),
            "second": timedelta(seconds=amount),
            "dakika": timedelta(minutes=amount),
            "minute": timedelta(minutes=amount),
            "saat": timedelta(hours=amount),
            "hour": timedelta(hours=amount),
            "gün": timedelta(days=amount),
            "day": timedelta(days=amount),
            "hafta": timedelta(weeks=amount),
            "week": timedelta(weeks=amount),
            "ay": timedelta(days=amount * 30),
            "month": timedelta(days=amount * 30),
            "yıl": timedelta(days=amount * 365),
            "year": timedelta(days=amount * 365),
        }
        return now - table.get(unit, timedelta(0))

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que Zenith Scans est accessible.

        Combine un test HTTP basique avec un test Cloudflare.

        Returns:
            ``True`` si le site répond correctement.
        """
        try:
            response = await self.cf_get(self.base_url, timeout=10.0)
            ok = response.status_code < 500
            self.logger.info(
                "Health check ZenithScans: status={s} ok={ok}",
                s=response.status_code,
                ok=ok,
            )
            return ok
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                "Health check ZenithScans KO: {err}", err=exc
            )
            return False

    # ------------------------------------------------------------------
    # Méthodes utilitaires spécifiques
    # ------------------------------------------------------------------

    def _mt_abs_url(self, url: str, base: str | None = None) -> str:
        """Résout une URL relative en absolue (surcharge utilitaire).

        Args:
            url: URL relative ou absolue.
            base: Base alternative (par défaut : URL de base du site).

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

    def _zenith_build_search_url(self, query: str, page: int = 1) -> str:
        """Construit l'URL de recherche Zenith Scans.

        Args:
            query: Terme de recherche.
            page: Numéro de page.

        Returns:
            URL complète de recherche.
        """
        base = self.base_url.rstrip("/")
        url = f"{base}/?s={quote_plus(query)}"
        if page > 1:
            url = f"{url}&page={page}"
        return url
