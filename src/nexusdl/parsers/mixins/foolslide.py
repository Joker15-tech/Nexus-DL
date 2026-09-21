"""Mixin FoolSlide pour les parseurs NexusDL.

Ce module expose :class:`FoolSlideMixin`, une brique réutilisable qui fournit
des implémentations par défaut de ``search``, ``get_manga``, ``get_chapters``
et ``get_pages`` pour les sites utilisant le CMS **FoolSlide** (Scan-Manga,
SushiScan, JapScan, Manga-Scan, Scan-VF, etc.) et ses variantes dérivées.

FoolSlide est un CMS PHP historique, encore massivement utilisé par la
communauté scan FR/EN. Il existe trois grandes familles de thèmes :

1. **FoolSlide classique** : pages extraites via ``var pages = [...]`` dans une
   balise ``<script>``, images servies depuis ``/leech/...``.
2. **FoolSlide moderne (variante WordPress-like)** : sélecteur CSS stable
   ``.chapter-page img`` ou ``.page-image img``.
3. **FoolSlide API** : endpoint ``/api/...`` retournant du JSON (rare mais
   présent sur les forks récents).

Le mixin gère les trois stratégies et expose des ``ClassVar`` surchargeables
pour les sélecteurs, les motifs d'URL et les hooks optionnels.

Contrat implicite du parser hôte
--------------------------------

Le parser hôte doit fournir :

* ``self.config: SiteConfig``
* ``self.session: HttpSession``
* ``self.logger`` (optionnel, sinon :func:`get_logger` est utilisé)
* ``self.playwright_pool`` (optionnel, requis uniquement si
  ``foolslide_requires_js = True``)

Example:
    >>> class SushiScanNetParser(FoolSlideMixin, BaseParser):
    ...     site_id = "sushiscan_net"
    ...     language = Language.FR
    ...     fools_base_url = "https://sushiscan.net"
    ...     fools_search_path = "/catalogue/"
    ...     fools_search_query_param = "search"
    ...
    ...     async def search(self, query: str, *, page: int = 1):
    ...         return await self.foolslide_search(query, page=page)
"""

from __future__ import annotations

import json as _json
import re
from collections.abc import Iterable
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

__all__ = ["FoolSlideMixin"]


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_CHAPTER_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:chapitre|chapter|ch|chap|episode|ep|tome|vol|volume)?\s*"
    r"(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)

_VOLUME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:tome|vol|volume)\s*(\d+)", re.IGNORECASE
)

_RELATIVE_DATE_RE: Final[re.Pattern[str]] = re.compile(
    r"(\d+)\s*(seconde|minute|heure|jour|semaine|mois|an|year|month|week|day|hour|minute|second)s?",
    re.IGNORECASE,
)

_STATUS_MAP: Final[dict[str, MangaStatus]] = {
    "ongoing": MangaStatus.ONGOING,
    "en cours": MangaStatus.ONGOING,
    "en cours de publication": MangaStatus.ONGOING,
    "en cours de parution": MangaStatus.ONGOING,
    "completed": MangaStatus.COMPLETED,
    "complete": MangaStatus.COMPLETED,
    "terminé": MangaStatus.COMPLETED,
    "termine": MangaStatus.COMPLETED,
    "fini": MangaStatus.COMPLETED,
    "hiatus": MangaStatus.HIATUS,
    "en pause": MangaStatus.HIATUS,
    "paused": MangaStatus.HIATUS,
    "cancelled": MangaStatus.CANCELLED,
    "canceled": MangaStatus.CANCELLED,
    "annulé": MangaStatus.CANCELLED,
    "abandonné": MangaStatus.CANCELLED,
    "dropped": MangaStatus.CANCELLED,
}

_ADULT_GENRE_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "hentai",
        "adult",
        "adulte",
        "ecchi",
        "smut",
        "mature",
        "porn",
        "18+",
        "érotique",
        "erotique",
        "yaoi",
        "yuri",
        "lolicon",
        "shotacon",
    }
)


# ---------------------------------------------------------------------------
# Mixin principal
# ---------------------------------------------------------------------------


class FoolSlideMixin:
    """Mixin fournissant les implémentations FoolSlide standard.

    Class Attributes:
        fools_base_url: URL racine du site (écrase ``self.config.domains[0]``
            si défini).
        fools_search_path: Chemin de recherche (ex. ``/search``).
        fools_search_query_param: Nom du paramètre de requête (``q``, ``s``,
            ``search``).
        fools_search_method: ``"GET"`` ou ``"POST"``.
        fools_series_path: Préfixe des URLs de série (``/series``, ``/catalogue``).
        fools_read_path: Préfixe des URLs de lecture (``/read``, ``/lecture``).
        fools_search_item_selector: Sélecteur CSS des items de résultats.
        fools_search_link_selector: Sélecteur CSS du lien titre.
        fools_search_cover_selector: Sélecteur CSS de la couverture.
        fools_search_title_selector: Sélecteur CSS du titre.
        fools_search_author_selector: Sélecteur CSS de l'auteur.
        fools_manga_title_selector: Sélecteur CSS du titre sur la fiche manga.
        fools_manga_cover_selector: Sélecteur CSS de la couverture.
        fools_manga_description_selector: Sélecteur CSS du synopsis.
        fools_manga_author_selector: Sélecteur CSS de l'auteur.
        fools_manga_artist_selector: Sélecteur CSS de l'artiste.
        fools_manga_genres_selector: Sélecteur CSS des genres.
        fools_manga_status_selector: Sélecteur CSS du statut.
        fools_manga_year_selector: Sélecteur CSS de l'année.
        fools_chapter_selector: Sélecteur CSS des liens de chapitres.
        fools_page_img_selector: Sélecteur CSS des images de pages.
        fools_pages_var_names: Noms des variables JS contenant les pages
            (``("pages", "page_urls", "pageUrls", "images")``).
        fools_requires_js: Si ``True``, la fiche manga et les chapitres sont
            chargés via Playwright (utile pour les SPAs FoolSlide-like).
        fools_api_pages_endpoint: Motif d'endpoint API pour les pages, avec
            ``{series}`` et ``{chapter}`` comme placeholders. ``None`` pour
            désactiver.
        fools_default_rating: Classification par défaut si non déterminée.
        fools_concurrent_chapter_requests: Limite de requêtes simultanées pour
            la récupération des chapitres d'un manga.
    """

    # --- URLs de base ---
    fools_base_url: ClassVar[str | None] = None
    fools_search_path: ClassVar[str] = "/search"
    fools_search_query_param: ClassVar[str] = "q"
    fools_search_method: ClassVar[str] = "GET"
    fools_series_path: ClassVar[str] = "/series"
    fools_read_path: ClassVar[str] = "/read"

    # --- Sélecteurs : recherche ---
    fools_search_item_selector: ClassVar[str] = ".list-item, .group, .manga-item"
    fools_search_link_selector: ClassVar[str] = "a"
    fools_search_cover_selector: ClassVar[str] = "img"
    fools_search_title_selector: ClassVar[str] = ".title, h3, h4"
    fools_search_author_selector: ClassVar[str] = ".author, .artist, .chapitre"

    # --- Sélecteurs : fiche manga ---
    fools_manga_title_selector: ClassVar[str] = "h1, .manga-title, .title"
    fools_manga_cover_selector: ClassVar[str] = ".manga-cover img, .thumbnail img, .cover img"
    fools_manga_description_selector: ClassVar[str] = (
        ".manga-summary, .summary, .description, .synopsis"
    )
    fools_manga_author_selector: ClassVar[str] = (
        ".manga-author, .author, a[href*='/author/'], a[href*='/auteur/']"
    )
    fools_manga_artist_selector: ClassVar[str] = (
        ".manga-artist, .artist, a[href*='/artist/'], a[href*='/artiste/']"
    )
    fools_manga_genres_selector: ClassVar[str] = (
        ".manga-genres a, .genres a, a[href*='/genre/'], a[href*='/tag/']"
    )
    fools_manga_status_selector: ClassVar[str] = ".manga-status, .status"
    fools_manga_year_selector: ClassVar[str] = ".manga-year, .year"

    # --- Sélecteurs : chapitres et pages ---
    fools_chapter_selector: ClassVar[str] = (
        ".chapters a, .chapter-list a, "
        "a[href*='/read/'], a[href*='/lecture/'], "
        "a[href*='/chapter/'], a[href*='/chapitre/']"
    )
    fools_page_img_selector: ClassVar[str] = (
        ".chapter-page img, .page-image img, "
        ".reading-content img, .reader img, #page img"
    )
    fools_pages_var_names: ClassVar[tuple[str, ...]] = (
        "pages",
        "page_urls",
        "pageUrls",
        "images",
        "chapterImages",
    )

    # --- Comportement ---
    fools_requires_js: ClassVar[bool] = False
    fools_api_pages_endpoint: ClassVar[str | None] = None
    fools_default_rating: ClassVar[ContentRating] = ContentRating.SAFE
    fools_concurrent_chapter_requests: ClassVar[int] = 4
    fools_search_pages_limit: ClassVar[int] = 20

    # --- Contrat attendu du parser hôte ---
    config: Any  # SiteConfig
    session: Any  # HttpSession
    playwright_pool: Any | None = None

    # ------------------------------------------------------------------
    # Setup / helpers internes
    # ------------------------------------------------------------------

    def _fools_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="foolslide"``.
        """
        existing = getattr(self, "logger", None)
        if existing is not None:
            return existing
        logger = get_logger(self.__class__.__module__)
        site_id = getattr(self.config, "id", "unknown")
        return logger.bind(site_id=site_id, mixin="foolslide")

    def _fools_base(self) -> str:
        """Retourne l'URL de base du site.

        Returns:
            URL racine sans slash final.

        Raises:
            ParseError: Si aucune URL de base n'est déterminable.
        """
        explicit = self.fools_base_url
        if explicit:
            return explicit.rstrip("/")
        domains = getattr(self.config, "domains", None) or []
        if not domains:
            raise ParseError(
                f"Aucun domaine configuré pour le site {self.config.id!r}"
            )
        first = domains[0]
        url = str(first) if not isinstance(first, str) else first
        return url.rstrip("/")

    def _fools_abs(self, url: str, base: str | None = None) -> str:
        """Convertit une URL relative en URL absolue.

        Args:
            url: URL relative ou absolue.
            base: Base alternative (par défaut : URL de la page courante).

        Returns:
            URL absolue normalisée.
        """
        if not url:
            return ""
        if url.startswith(("http://", "https://")):
            return url
        if url.startswith("//"):
            scheme = urlparse(base or self._fools_base()).scheme or "https"
            return f"{scheme}:{url}"
        root = base or self._fools_base()
        return urljoin(root + "/", url.lstrip("/"))

    @staticmethod
    def _fools_clean(value: str | None) -> str:
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
    def _fools_text(node: Node | None) -> str:
        """Extrait et nettoie le texte d'un nœud.

        Args:
            node: Nœud selectolax ou ``None``.

        Returns:
            Texte nettoyé.
        """
        if node is None:
            return ""
        return FoolSlideMixin._fools_clean(node.text(deep=True, separator=" "))

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
            selector: Sélecteur CSS (peut contenir plusieurs sélecteurs séparés
                par virgule).

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

    # ------------------------------------------------------------------
    # Parsing du statut / date / numéro de chapitre
    # ------------------------------------------------------------------

    def _fools_parse_status(self, raw: str | None) -> MangaStatus:
        """Convertit un libellé de statut FoolSlide en énumération.

        Args:
            raw: Libellé brut (ex. ``"En cours"``).

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

    def _fools_parse_year(self, raw: str | None) -> int | None:
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
        """Parse une date relative type ``"il y a 3 jours"``.

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
            "seconde": timedelta(seconds=amount),
            "second": timedelta(seconds=amount),
            "minute": timedelta(minutes=amount),
            "heure": timedelta(hours=amount),
            "hour": timedelta(hours=amount),
            "jour": timedelta(days=amount),
            "day": timedelta(days=amount),
            "semaine": timedelta(weeks=amount),
            "week": timedelta(weeks=amount),
            "mois": timedelta(days=amount * 30),
            "month": timedelta(days=amount * 30),
            "an": timedelta(days=amount * 365),
            "year": timedelta(days=amount * 365),
        }
        return now - table.get(unit, timedelta(0))

    def _fools_chapter_number(self, text: str) -> float | str:
        """Extrait le numéro de chapitre d'un libellé.

        Args:
            text: Libellé (ex. ``"Chapitre 12.5 VF"``).

        Returns:
            ``float`` si un numéro est trouvé, sinon la chaîne nettoyée.
        """
        cleaned = self._fools_clean(text)
        match = _CHAPTER_NUMBER_RE.search(cleaned)
        if not match:
            return cleaned
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return cleaned

    def _fools_chapter_volume(self, text: str) -> int | None:
        """Extrait le numéro de tome d'un libellé.

        Args:
            text: Libellé contenant éventuellement ``Tome X``.

        Returns:
            Numéro de tome ou ``None``.
        """
        match = _VOLUME_RE.search(text or "")
        return int(match.group(1)) if match else None

    def _fools_detect_rating(self, genres: Iterable[str]) -> ContentRating:
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
        return self.fools_default_rating

    # ------------------------------------------------------------------
    # URLs FoolSlide
    # ------------------------------------------------------------------

    def _fools_search_url(self, query: str, *, page: int = 1) -> str:
        """Construit l'URL de recherche FoolSlide.

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-based).

        Returns:
            URL complète.
        """
        base = self._fools_base()
        path = self.fools_search_path
        if not path.startswith("/"):
            path = "/" + path
        url = f"{base}{path}"
        joiner = "&" if "?" in url else "?"
        if self.fools_search_method.upper() == "GET":
            url = f"{url}{joiner}{self.fools_search_query_param}={quote_plus(query)}"
            if page > 1:
                url = f"{url}&page={page}"
        return url

    def _fools_series_url(self, slug: str) -> str:
        """Construit l'URL d'une série FoolSlide.

        Args:
            slug: Identifiant ou chemin de la série.

        Returns:
            URL absolue.
        """
        if slug.startswith(("http://", "https://")):
            return slug
        base = self._fools_base()
        path = self.fools_series_path.strip("/")
        clean_slug = slug.strip("/")
        return f"{base}/{path}/{clean_slug}/"

    # ------------------------------------------------------------------
    # Recherche
    # ------------------------------------------------------------------

    async def foolslide_search(
        self,
        query: str,
        *,
        page: int = 1,
    ) -> list[SearchResult]:
        """Recherche FoolSlide standard.

        Args:
            query: Terme de recherche.
            page: Numéro de page.

        Returns:
            Liste de :class:`SearchResult`.
        """
        url = self._fools_search_url(query, page=page)
        logger = self._fools_logger()
        logger.debug("FoolSlide search: {url}", url=url)

        if self.fools_search_method.upper() == "POST":
            response = await self.session.post(
                self._fools_base() + self.fools_search_path,
                data={self.fools_search_query_param: query, "page": page},
                headers={"Referer": self._fools_base() + "/"},
            )
            html = response.text
        else:
            html = await self.session.get_html(url)

        return self._fools_parse_search_html(html, base_url=url)

    def _fools_parse_search_html(
        self,
        html: str,
        *,
        base_url: str,
    ) -> list[SearchResult]:
        """Parse le HTML de résultats de recherche.

        Args:
            html: HTML brut.
            base_url: URL de la page (pour résolution relative).

        Returns:
            Liste de :class:`SearchResult`.
        """
        tree = HTMLParser(html)
        results: list[SearchResult] = []
        for node in self._fools_all(tree, self.fools_search_item_selector):
            link_node = node.css_first(self.fools_search_link_selector)
            href = self._fools_attr(link_node, "href")
            if not href:
                continue
            abs_url = self._fools_abs(href, base_url)

            title_node = node.css_first(self.fools_search_title_selector)
            title = self._fools_text(title_node) or self._fools_attr(link_node, "title")
            if not title:
                continue

            cover_node = node.css_first(self.fools_search_cover_selector)
            cover_src = (
                self._fools_attr(cover_node, "data-src")
                or self._fools_attr(cover_node, "data-lazy-src")
                or self._fools_attr(cover_node, "src")
            )
            cover_url = self._fools_abs(cover_src, base_url) if cover_src else None

            author_node = node.css_first(self.fools_search_author_selector)
            author = self._fools_text(author_node) or None

            results.append(
                SearchResult(
                    title=title,
                    url=abs_url,
                    site_id=self.config.id,
                    cover_url=cover_url,
                    author=author,
                )
            )

        self._fools_logger().info(
            "FoolSlide search: {n} résultat(s)", n=len(results)
        )
        return results

    # ------------------------------------------------------------------
    # Fiche manga
    # ------------------------------------------------------------------

    async def foolslide_get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga FoolSlide.

        Args:
            url_or_id: URL absolue ou slug de la série.

        Returns:
            Objet :class:`Manga` hydraté.

        Raises:
            MangaNotFoundError: Si la page est inaccessible.
            ParseError: Si le HTML est inexploitable.
        """
        url = (
            url_or_id
            if url_or_id.startswith(("http://", "https://"))
            else self._fools_series_url(url_or_id)
        )
        logger = self._fools_logger()
        logger.debug("FoolSlide get_manga: {url}", url=url)

        response = await self.session.get(url, follow_redirects=True)
        if response.status_code == 404:
            raise MangaNotFoundError(f"Manga introuvable : {url}")
        if response.status_code >= 400:
            raise ParseError(
                f"HTTP {response.status_code} sur {url} "
                f"({self.config.id!r})"
            )

        html = response.text
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

        author_node = self._fools_first(tree, self.fools_manga_author_selector)
        author = self._fools_text(author_node) or None

        artist_node = self._fools_first(tree, self.fools_manga_artist_selector)
        artist = self._fools_text(artist_node) or None

        genre_nodes = self._fools_all(tree, self.fools_manga_genres_selector)
        genres: list[str] = []
        for node in genre_nodes:
            text = self._fools_text(node)
            if text and text not in genres:
                genres.append(text)

        status_node = self._fools_first(tree, self.fools_manga_status_selector)
        status = self._fools_parse_status(self._fools_text(status_node))

        year_node = self._fools_first(tree, self.fools_manga_year_selector)
        year = self._fools_parse_year(self._fools_text(year_node))

        source_id = self._fools_extract_series_slug(url)
        language = getattr(self, "language", Language.FR)
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
            year=year,
            cover_url=cover_url,
            language=language,
            content_rating=rating,
            chapters=chapters,
            url=url,
            updated_at=datetime.now(timezone.utc),
        )
        logger.info(
            "FoolSlide get_manga OK: {title} ({n} chapitres)",
            title=title,
            n=len(chapters),
        )
        return manga

    @staticmethod
    def _fools_extract_series_slug(url: str) -> str:
        """Extrait un identifiant de série depuis une URL FoolSlide.

        Args:
            url: URL de la série.

        Returns:
            Slug nettoyé.
        """
        parts = [p for p in urlparse(url).path.split("/") if p]
        if not parts:
            return url
        # Le slug est généralement le dernier segment significatif.
        for candidate in reversed(parts):
            if candidate and not candidate.isdigit():
                return candidate
        return parts[-1]

    # ------------------------------------------------------------------
    # Chapitres
    # ------------------------------------------------------------------

    async def foolslide_get_chapters(self, manga: Manga) -> list[Chapter]:
        """Récupère la liste des chapitres d'un manga.

        Args:
            manga: Manga dont on veut les chapitres.

        Returns:
            Liste de :class:`Chapter` triés par numéro croissant.
        """
        html = await self.session.get_html(str(manga.url))
        return self._fools_parse_chapters_from_html(html, base_url=str(manga.url))

    def _fools_parse_chapters_from_html(
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
        language = getattr(self, "language", Language.FR)

        for node in self._fools_all(tree, self.fools_chapter_selector):
            href = self._fools_attr(node, "href")
            if not href:
                continue
            abs_url = self._fools_abs(href, base_url)
            if abs_url in seen:
                continue
            seen.add(abs_url)

            label = self._fools_text(node) or self._fools_attr(node, "title")
            if not label:
                label = abs_url.rsplit("/", 2)[-2] if "/" in abs_url else abs_url

            number = self._fools_chapter_number(label)
            volume = self._fools_chapter_volume(label)

            date_text = self._fools_attr(node, "data-date") or None
            published = (
                self._fools_parse_relative_date(date_text)
                if date_text
                else self._fools_parse_relative_date(label)
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
        self._fools_logger().debug(
            "FoolSlide chapitres: {n}", n=len(chapters)
        )
        return chapters

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    async def foolslide_get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre FoolSlide.

        Stratégie (par ordre de priorité) :

        1. Endpoint API JSON si ``fools_api_pages_endpoint`` est défini.
        2. Variable JS ``var pages = [...]`` embarquée dans le HTML.
        3. Sélecteur CSS d'images (fallback HTML).

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune image n'est trouvée.
            ParseError: Si le HTML est inexploitable.
        """
        logger = self._fools_logger()
        chapter_url = str(chapter.url)
        logger.debug("FoolSlide get_pages: {url}", url=chapter_url)

        if self.fools_api_pages_endpoint:
            pages = await self._fools_try_api_pages(chapter)
            if pages:
                return pages

        html = await self.session.get_html(chapter_url)

        # Tentative 1 : extraction via variable JS.
        pages = self._fools_extract_pages_from_js(html, base_url=chapter_url)
        if pages:
            return pages

        # Tentative 2 : extraction via sélecteur CSS d'images.
        pages = self._fools_extract_pages_from_html(html, base_url=chapter_url)
        if pages:
            return pages

        raise ChapterNotFoundError(
            f"Aucune page trouvée pour {chapter_url} sur {self.config.id!r}"
        )

    async def _fools_try_api_pages(self, chapter: Chapter) -> list[Page]:
        """Tente de récupérer les pages via l'endpoint API configuré.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste de pages (vide si l'endpoint échoue).
        """
        endpoint = self.fools_api_pages_endpoint
        if not endpoint:
            return []
        try:
            url = endpoint.format(
                series=chapter.source_id,
                chapter=chapter.source_id,
            )
        except KeyError:
            url = endpoint
        url = self._fools_abs(url)
        try:
            response = await self.session.get(url)
        except Exception as exc:  # noqa: BLE001 — fallback best-effort
            self._fools_logger().debug("API pages KO : {err}", err=exc)
            return []
        if response.status_code >= 400:
            return []
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            return []
        urls = self._fools_flatten_page_urls(payload)
        return self._fools_build_pages(urls)

    def _fools_extract_pages_from_js(
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
        for var_name in self.fools_pages_var_names:
            pattern = re.compile(
                rf"(?:var|let|const)\s+{re.escape(var_name)}\s*=\s*(\[.*?\])\s*;",
                re.DOTALL,
            )
            for match in pattern.finditer(html):
                raw = match.group(1)
                urls = self._fools_parse_js_array(raw)
                if urls:
                    resolved = [self._fools_abs(u, base_url) for u in urls]
                    return self._fools_build_pages(resolved)
        return []

    @staticmethod
    def _fools_parse_js_array(raw: str) -> list[str]:
        """Parse un tableau JS ``[ ... ]`` en liste d'URLs.

        Args:
            raw: Contenu brut du tableau JS.

        Returns:
            Liste d'URLs (chaînes).
        """
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

    def _fools_extract_pages_from_html(
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
        for node in self._fools_all(tree, self.fools_page_img_selector):
            src = (
                self._fools_attr(node, "data-src")
                or self._fools_attr(node, "data-lazy-src")
                or self._fools_attr(node, "data-original")
                or self._fools_attr(node, "src")
            )
            if not src:
                continue
            abs_url = self._fools_abs(src, base_url)
            if abs_url not in urls:
                urls.append(abs_url)
        return self._fools_build_pages(urls)

    @staticmethod
    def _fools_flatten_page_urls(payload: Any) -> list[str]:
        """Aplatit une charge utile JSON en liste d'URLs.

        Args:
            payload: Charge JSON quelconque.

        Returns:
            Liste d'URLs trouvées.
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
                for key in ("url", "src", "image", "page", "link"):
                    if key in obj:
                        _walk(obj[key])
                for value in obj.values():
                    if isinstance(value, (list, dict)):
                        _walk(value)

        _walk(payload)
        return urls

    def _fools_build_pages(self, urls: Iterable[str]) -> list[Page]:
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
