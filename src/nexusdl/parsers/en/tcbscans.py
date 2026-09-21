"""Parseur TCB Scans pour NexusDL.

TCB Scans (``https://tcbscans.com``) est un groupe de scanlation anglophone
de premier plan, connu pour ses traductions rapides des grands titres du
Weekly Shōnen Jump (One Piece, Jujutsu Kaisen, My Hero Academia, etc.).

Caractéristiques techniques
---------------------------

* **Moteur** : à l'origine WordPress **Madara**, mais le site a migré vers
  une application **Next.js** côté client (les pages sont rendues en JS).
  Le parsing s'appuie donc sur :class:`JsRenderedMixin` en priorité, avec
  fallback HTML direct quand les pages sont pré-rendues.
* **Langue** : Anglais (``en``).
* **Protection** : Cloudflare (AS13335, IP ``104.21.47.202`` /
  ``172.67.172.115``) avec challenge de niveau élevé. Redirection vers
  ``tcb-backup.bihar-mirchi.com`` en cas de blocage.
* **Domaines** : ``tcbscans.com`` (principal), ``tcbscans.co.uk`` (miroir),
  ``onepiecechapters.com`` (miroir historique).
* **Structure des URLs** :
    - Projets : ``/projects`` (liste complète des séries).
    - Manga : ``/mangas/{id}/{slug}``.
    - Chapitre : ``/chapters/{id}/{slug}``.
* **Junk pages** : TCB Scans insère délibérément des images manquantes
  (HTTP 404) à la fin de certains chapitres pour dissuader le scraping.
  Le parseur détecte et filtre ces pages via le mixin de validation d'image
  et une heuristique de test inversé (les junk sont toujours en fin de
  chapitre).
* **Images** : servies depuis le même domaine, dans des balises
  ``<picture><source><img src="..."></picture>``.

Le parseur combine trois mixins :

1. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — rendu
   Playwright complet (site Next.js SPA).
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — bypass
   Cloudflare automatique (Playwright / FlareSolverr).
3. Implémentations Madara/Next.js **inline** — les sélecteurs et la logique
   de parsing sont adaptés au DOM rendu de TCB Scans (pas de mixin Madara
   strict, le site ayant migré).

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.en.tcbscans import TCBScansParser
    >>>
    >>> parser = TCBScansParser(config, session)
    >>> manga = await parser.get_manga("https://tcbscans.com/mangas/5/one-piece")
    >>> chapters = await parser.get_chapters(manga)
    >>> pages = await parser.get_pages(chapters[-1])
"""

from __future__ import annotations

import json as _json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Final
from urllib.parse import urljoin, urlparse

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

__all__ = ["TCBScansParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://tcbscans.com"
_FALLBACK_DOMAINS: Final[tuple[str, ...]] = (
    "https://tcbscans.com",
    "https://tcbscans.co.uk",
    "https://onepiecechapters.com",
)
_SITE_ID: Final[str] = "tcbscans"
_LANGUAGE: Final[Language] = Language.EN
_ADULT: Final[bool] = False

# Regex de parsing des chapitres (format TCB Scans : "Chapter 1089").
_CHAPTER_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:chapter|ch\.?|episode|ep\.?)\s*(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
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
# Sélecteurs TCB Scans (Next.js rendu + fallback Madara)
# ---------------------------------------------------------------------------

_TCB_PROJECTS_ITEM_SELECTOR: Final[str] = (
    "div.projects div.project, "
    "div.project, "
    ".page-item-detail.manga, .bs, .bsx, "
    "a[href*='/mangas/']"
)
_TCB_PROJECTS_LINK_SELECTOR: Final[str] = "a"
_TCB_PROJECTS_COVER_SELECTOR: Final[str] = "img"
_TCB_PROJECTS_TITLE_SELECTOR: Final[str] = (
    "div.project-title, .post-title, h3, h4, .title"
)

_TCB_MANGA_TITLE_SELECTOR: Final[str] = (
    "h1, .entry-title, .post-title, .manga-title, "
    "div.project-title h1"
)
_TCB_MANGA_COVER_SELECTOR: Final[str] = (
    ".summary_image img, .thumb img, .manga-cover img, "
    ".cover img, div.project-cover img"
)
_TCB_MANGA_DESCRIPTION_SELECTOR: Final[str] = (
    ".summary__content, .description, .manga-summary, "
    ".entry-content p, .c-content, div.project-description"
)
_TCB_MANGA_AUTHOR_SELECTOR: Final[str] = (
    ".author, .manga-author, a[href*='/author/'], "
    ".post-content_item .author-content a"
)
_TCB_MANGA_ARTIST_SELECTOR: Final[str] = (
    ".artist, .manga-artist, a[href*='/artist/'], "
    ".post-content_item .artist-content a"
)
_TCB_MANGA_GENRES_SELECTOR: Final[str] = (
    ".genres a, .manga-genres a, a[href*='/genre/'], "
    "a[href*='/tag/'], .wp-manga-tags-list a"
)
_TCB_MANGA_STATUS_SELECTOR: Final[str] = (
    ".status, .manga-status, .post-status, "
    ".post-content_item .summary-content"
)

_TCB_CHAPTER_SELECTOR: Final[str] = (
    "div.chapters a, "
    "a[href*='/chapters/'], "
    ".listing-chapters_wrap li a, "
    ".eplister li:not(.ch-price-side) a"
)
_TCB_PAGE_IMG_SELECTOR: Final[str] = (
    "picture img, "
    ".reading-content img, "
    ".page-break img, "
    "#readerarea img, "
    "div.reading-content div.page-break.no-gaps img"
)
_TCB_PAGES_VAR_NAMES: Final[tuple[str, ...]] = (
    "pages",
    "page_urls",
    "pageUrls",
    "images",
    "chapterImages",
)


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


class TCBScansParser(
    JsRenderedMixin,
    CloudflareMixin,
    BaseParser,
):
    """Parseur TCB Scans (Next.js + Cloudflare).

    Combine le rendu Playwright (obligatoire — le site est une SPA Next.js),
    le contournement Cloudflare et des implémentations de parsing inline
    adaptées au DOM de TCB Scans.

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

    # --- Chemins TCB Scans ---
    tcb_projects_path: ClassVar[str] = "/projects"
    tcb_series_path: ClassVar[str] = "/mangas"
    tcb_read_path: ClassVar[str] = "/chapters"

    # --- Sélecteurs TCB Scans ---
    tcb_projects_item_selector: ClassVar[str] = _TCB_PROJECTS_ITEM_SELECTOR
    tcb_projects_link_selector: ClassVar[str] = _TCB_PROJECTS_LINK_SELECTOR
    tcb_projects_cover_selector: ClassVar[str] = _TCB_PROJECTS_COVER_SELECTOR
    tcb_projects_title_selector: ClassVar[str] = _TCB_PROJECTS_TITLE_SELECTOR

    tcb_manga_title_selector: ClassVar[str] = _TCB_MANGA_TITLE_SELECTOR
    tcb_manga_cover_selector: ClassVar[str] = _TCB_MANGA_COVER_SELECTOR
    tcb_manga_description_selector: ClassVar[str] = _TCB_MANGA_DESCRIPTION_SELECTOR
    tcb_manga_author_selector: ClassVar[str] = _TCB_MANGA_AUTHOR_SELECTOR
    tcb_manga_artist_selector: ClassVar[str] = _TCB_MANGA_ARTIST_SELECTOR
    tcb_manga_genres_selector: ClassVar[str] = _TCB_MANGA_GENRES_SELECTOR
    tcb_manga_status_selector: ClassVar[str] = _TCB_MANGA_STATUS_SELECTOR

    tcb_chapter_selector: ClassVar[str] = _TCB_CHAPTER_SELECTOR
    tcb_page_img_selector: ClassVar[str] = _TCB_PAGE_IMG_SELECTOR
    tcb_pages_var_names: ClassVar[tuple[str, ...]] = _TCB_PAGES_VAR_NAMES

    # --- Comportement ---
    tcb_requires_js: ClassVar[bool] = True  # Site Next.js (SPA)
    tcb_default_rating: ClassVar[ContentRating] = ContentRating.SAFE
    tcb_junk_page_filter: ClassVar[bool] = True  # Filtre les images junk
    tcb_junk_probe_depth: ClassVar[int] = 5  # Nombre de pages testées en fin

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
    default_wait_until: ClassVar[str] = "networkidle"  # Next.js : attendre le réseau
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
        """Initialise le parseur TCB Scans.

        Args:
            config: Configuration du site (:class:`SiteConfig`).
            session: Session HTTP async du projet (:class:`HttpSession`).
            playwright_pool: Pool Playwright partagé (requis — le site est
                une SPA Next.js).
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

    def _tcb_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="tcbscans"``.
        """
        return self.logger

    @staticmethod
    def _tcb_clean(value: str | None) -> str:
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
    def _tcb_text(node: Node | None) -> str:
        """Extrait et nettoie le texte d'un nœud.

        Args:
            node: Nœud selectolax ou ``None``.

        Returns:
            Texte nettoyé.
        """
        if node is None:
            return ""
        return TCBScansParser._tcb_clean(node.text(deep=True, separator=" "))

    @staticmethod
    def _tcb_attr(node: Node | None, name: str) -> str:
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

    def _tcb_first(self, tree: HTMLParser, selector: str) -> Node | None:
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

    def _tcb_all(self, tree: HTMLParser, selector: str) -> list[Node]:
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

    def _tcb_abs(self, url: str, base: str | None = None) -> str:
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

    def _tcb_parse_status(self, raw: str | None) -> MangaStatus:
        """Convertit un libellé de statut en énumération NexusDL.

        Args:
            raw: Libellé brut.

        Returns:
            Statut normalisé (``ONGOING`` par défaut).
        """
        if not raw:
            return MangaStatus.ONGOING
        key = self._tcb_clean(raw).lower()
        for needle, status in _STATUS_MAP.items():
            if needle in key:
                return status
        return MangaStatus.ONGOING

    @staticmethod
    def _tcb_parse_year(raw: str | None) -> int | None:
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
    def _tcb_parse_relative_date(raw: str | None) -> datetime | None:
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
    def _tcb_chapter_number(text: str) -> float | str:
        """Extrait le numéro de chapitre d'un libellé.

        Args:
            text: Libellé (ex. ``"Chapter 1089"``).

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = TCBScansParser._tcb_clean(text)
        match = _CHAPTER_NUMBER_RE.search(cleaned)
        if not match:
            return cleaned
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return cleaned

    def _tcb_detect_rating(self, genres: list[str]) -> ContentRating:
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
        return self.tcb_default_rating

    # ------------------------------------------------------------------
    # Filtrage des junk pages
    # ------------------------------------------------------------------

    async def _tcb_filter_junk_pages(
        self, pages: list[Page]
    ) -> list[Page]:
        """Filtre les images junk (404) insérées par TCB Scans.

        TCB Scans place délibérément des images manquantes (HTTP 404) en fin
        de chapitre pour dissuader le scraping. L'algorithme teste les pages
        depuis la fin et s'arrête à la première page valide.

        Args:
            pages: Liste de pages candidates.

        Returns:
            Liste de pages sans les junk en fin de chapitre.
        """
        if not self.tcb_junk_page_filter or not pages:
            return pages

        # Teste les N dernières pages en partant de la fin.
        probe_count = min(self.tcb_junk_probe_depth, len(pages))
        first_junk_index: int | None = None

        for offset in range(1, probe_count + 1):
            idx = len(pages) - offset
            page = pages[idx]
            if await self._tcb_probe_page(page):
                # Page valide → on arrête.
                first_junk_index = idx + 1
                break

        if first_junk_index is not None and first_junk_index < len(pages):
            filtered = pages[:first_junk_index]
            removed = len(pages) - len(filtered)
            self.logger.info(
                "TCBScans: {n} junk page(s) filtrée(s) en fin de chapitre",
                n=removed,
            )
            return filtered
        return pages

    async def _tcb_probe_page(self, page: Page) -> bool:
        """Vérifie qu'une page est accessible (HTTP 200).

        Args:
            page: Page à tester.

        Returns:
            ``True`` si la page renvoie 200.
        """
        try:
            response = await self.session.head(str(page.url), timeout=8.0)
            return response.status_code == 200
        except Exception:  # noqa: BLE001 — best-effort
            return False

    # ------------------------------------------------------------------
    # Méthodes abstraites de BaseParser
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des mangas sur TCB Scans.

        TCB Scans n'a pas d'endpoint de recherche dédié : le parseur charge
        ``/projects`` et filtre côté client les titres correspondant à la
        requête.

        Args:
            query: Terme de recherche.
            page: Numéro de page (ignoré — TCB Scans n'a qu'une page).

        Returns:
            Liste de :class:`SearchResult` filtrés.

        Raises:
            ParseError: Si la page projets est inexploitable.
        """
        self.logger.debug("TCBScans search: {query}", query=query)
        url = f"{self.base_url}{self.tcb_projects_path}"

        try:
            rendered = await self.fetch_rendered(
                url,
                wait_until="networkidle",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec search TCBScans pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        all_results = self._tcb_parse_search_html(
            rendered.html, base_url=rendered.final_url or url
        )

        # Filtre côté client sur la requête.
        query_lower = query.lower().strip()
        filtered = [
            r for r in all_results
            if query_lower in r.title.lower()
        ]

        self.logger.info(
            "TCBScans search: {n} résultat(s) pour {query!r}",
            n=len(filtered),
            query=query,
        )
        return filtered

    def _tcb_parse_search_html(
        self,
        html: str,
        *,
        base_url: str,
    ) -> list[SearchResult]:
        """Parse le HTML de la page projets.

        Args:
            html: HTML rendu.
            base_url: URL de la page (pour résolution relative).

        Returns:
            Liste de :class:`SearchResult`.
        """
        tree = HTMLParser(html)
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for node in self._tcb_all(tree, self.tcb_projects_item_selector):
            # Cherche un lien vers /mangas/.
            link_node: Node | None = None
            if node.tag == "a" and "/mangas/" in self._tcb_attr(node, "href"):
                link_node = node
            else:
                for candidate in node.css("a"):
                    if "/mangas/" in self._tcb_attr(candidate, "href"):
                        link_node = candidate
                        break

            if link_node is None:
                continue

            href = self._tcb_attr(link_node, "href")
            if not href:
                continue
            abs_url = self._tcb_abs(href, base_url)
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            title_node = node.css_first(self.tcb_projects_title_selector)
            title = self._tcb_text(title_node) or self._tcb_attr(
                link_node, "title"
            )
            if not title:
                title = self._tcb_attr(link_node, "href").rstrip("/").rsplit("/", 1)[-1]
                title = title.replace("-", " ").title()
            if not title:
                continue

            cover_node = node.css_first(self.tcb_projects_cover_selector)
            cover_src = (
                self._tcb_attr(cover_node, "data-src")
                or self._tcb_attr(cover_node, "data-lazy-src")
                or self._tcb_attr(cover_node, "src")
            )
            cover_url = self._tcb_abs(cover_src, base_url) if cover_src else None

            results.append(
                SearchResult(
                    title=title,
                    url=abs_url,
                    site_id=self.config.id,
                    cover_url=cover_url,
                    author=None,
                )
            )

        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga TCB Scans.

        Args:
            url_or_id: URL absolue ou chemin de la série.

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
            url = f"{self.base_url}{self.tcb_series_path}/{url_or_id.strip('/')}"

        self.logger.debug("TCBScans get_manga: {url}", url=url)

        try:
            rendered = await self.fetch_rendered(
                url,
                wait_until="networkidle",
                wait_for_selector="h1, .project-title, .entry-title",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_manga TCBScans pour {url!r}: {err}",
                url=url,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {url!r}: {exc}"
            ) from exc

        html = rendered.html
        tree = HTMLParser(html)

        title_node = self._tcb_first(tree, self.tcb_manga_title_selector)
        title = self._tcb_text(title_node)
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        cover_node = self._tcb_first(tree, self.tcb_manga_cover_selector)
        cover_src = (
            self._tcb_attr(cover_node, "data-src")
            or self._tcb_attr(cover_node, "data-lazy-src")
            or self._tcb_attr(cover_node, "src")
        )
        cover_url = self._tcb_abs(cover_src, url) if cover_src else None

        description_node = self._tcb_first(
            tree, self.tcb_manga_description_selector
        )
        description = self._tcb_text(description_node) or None

        author_node = self._tcb_first(tree, self.tcb_manga_author_selector)
        author = self._tcb_text(author_node) or None

        artist_node = self._tcb_first(tree, self.tcb_manga_artist_selector)
        artist = self._tcb_text(artist_node) or None

        genre_nodes = self._tcb_all(tree, self.tcb_manga_genres_selector)
        genres: list[str] = []
        for node in genre_nodes:
            text = self._tcb_text(node)
            if text and text not in genres:
                genres.append(text)

        status_node = self._tcb_first(tree, self.tcb_manga_status_selector)
        status = self._tcb_parse_status(self._tcb_text(status_node))

        source_id = self._tcb_extract_series_slug(url)
        rating = self._tcb_detect_rating(genres)

        chapters = self._tcb_parse_chapters_from_html(html, base_url=url)

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
            "TCBScans get_manga OK: {title} ({n} chapitres)",
            title=title,
            n=len(chapters),
        )
        return manga

    @staticmethod
    def _tcb_extract_series_slug(url: str) -> str:
        """Extrait un identifiant de série depuis une URL TCB Scans.

        Format : ``/mangas/{id}/{slug}`` → retourne ``{id}/{slug}``.

        Args:
            url: URL de la série.

        Returns:
            Identifiant composite ``{id}/{slug}`` ou slug seul.
        """
        parts = [p for p in urlparse(url).path.split("/") if p]
        if "mangas" in parts:
            idx = parts.index("mangas")
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
            "TCBScans get_chapters: {title}", title=manga.title
        )

        try:
            rendered = await self.fetch_rendered(
                str(manga.url),
                wait_until="networkidle",
                wait_for_selector="a[href*='/chapters/'], .chapters a",
                timeout=45.0,
                bypass_cloudflare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_chapters TCBScans pour {title!r}: {err}",
                title=manga.title,
                err=exc,
            )
            raise ParseError(
                f"get_chapters échoué sur {self.site_id!r} "
                f"pour {manga.title!r}: {exc}"
            ) from exc

        chapters = self._tcb_parse_chapters_from_html(
            rendered.html, base_url=rendered.final_url or str(manga.url)
        )
        if not chapters:
            raise ChapterNotFoundError(
                f"Aucun chapitre trouvé pour {manga.url} sur {self.site_id!r}"
            )
        return chapters

    def _tcb_parse_chapters_from_html(
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

        for node in self._tcb_all(tree, self.tcb_chapter_selector):
            href = self._tcb_attr(node, "href")
            if not href or "/chapters/" not in href:
                continue
            abs_url = self._tcb_abs(href, base_url)
            if abs_url in seen:
                continue
            seen.add(abs_url)

            label = self._tcb_text(node) or self._tcb_attr(node, "title")
            if not label:
                label = abs_url.rstrip("/").rsplit("/", 1)[-1] or abs_url

            number = self._tcb_chapter_number(label)

            date_text = self._tcb_attr(node, "data-date") or None
            published = (
                self._tcb_parse_relative_date(date_text)
                if date_text
                else self._tcb_parse_relative_date(label)
            )

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
                    published_at=published,
                    url=abs_url,
                    pages=[],
                )
            )

        def _sort_key(ch: Chapter) -> tuple[int, float, str]:
            num = ch.number if isinstance(ch.number, (int, float)) else 0.0
            return (
                int(isinstance(ch.number, str)),
                float(num),
                ch.title.lower(),
            )

        chapters.sort(key=_sort_key)
        self.logger.debug(
            "TCBScans chapitres: {n}", n=len(chapters)
        )
        return chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre TCB Scans.

        Le site est une SPA Next.js : le rendu Playwright est nécessaire.
        Les images sont dans des balises ``<picture><source><img src="...">``.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page` (junk pages filtrées).

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si le HTML est inexploitable.
        """
        self.logger.debug(
            "TCBScans get_pages: {url}", url=str(chapter.url)
        )
        chapter_url = str(chapter.url)

        try:
            rendered = await self.fetch_rendered(
                chapter_url,
                wait_until="networkidle",
                wait_for_selector="picture img, .reading-content img, .page-break img",
                timeout=45.0,
                bypass_cloudflare=True,
                block_resources=False,  # Les images sont nécessaires
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_pages TCBScans pour {url!r}: {err}",
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

        # Priorité 1 : balises <picture> avec <img src>.
        for picture in tree.css("picture"):
            img = picture.css_first("img")
            if img is None:
                continue
            src = self._tcb_attr(img, "src")
            if src and src not in urls:
                urls.append(self._tcb_abs(src, chapter_url))

        # Priorité 2 : sélecteur CSS fallback.
        if not urls:
            for node in self._tcb_all(tree, self.tcb_page_img_selector):
                src = (
                    self._tcb_attr(node, "data-src")
                    or self._tcb_attr(node, "data-lazy-src")
                    or self._tcb_attr(node, "data-original")
                    or self._tcb_attr(node, "src")
                )
                if not src:
                    continue
                abs_url = self._tcb_abs(src, chapter_url)
                if abs_url not in urls:
                    urls.append(abs_url)

        # Priorité 3 : variable JS embarquée.
        if not urls:
            for var_name in self.tcb_pages_var_names:
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
                f"Aucune page trouvée pour {chapter_url} sur {self.site_id!r}"
            )

        pages: list[Page] = []
        for idx, url in enumerate(urls):
            clean = url.strip()
            if not clean:
                continue
            clean = self._tcb_abs(clean, chapter_url)
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

        # Filtre les junk pages (404) en fin de chapitre.
        pages = await self._tcb_filter_junk_pages(pages)
        return pages

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que TCB Scans est accessible.

        Teste le domaine principal puis les domaines de fallback.

        Returns:
            ``True`` si un domaine répond correctement.
        """
        for base in self.fallback_domains:
            try:
                rendered = await self.fetch_rendered(
                    f"{base}/projects",
                    wait_until="networkidle",
                    timeout=30.0,
                    bypass_cloudflare=True,
                    screenshot=False,
                )
                if rendered.ok:
                    self.logger.info(
                        "Health check TCBScans OK: {base} (status={s})",
                        base=base,
                        s=rendered.status,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "Health check TCBScans échec sur {base}: {err}",
                    base=base,
                    err=exc,
                )
        self.logger.warning("Health check TCBScans KO (tous domaines)")
        return False
