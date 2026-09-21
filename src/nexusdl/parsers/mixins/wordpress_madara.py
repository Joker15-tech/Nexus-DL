"""Mixin complet pour les sites WordPress Madara (MangaBooth).

Le thème Madara est utilisé par des centaines de sites de scantrad à
travers le monde. Il expose une structure HTML **très standardisée** :

    - Recherche      : ``GET /?s=<query>&post_type=wp-manga&page=N``
    - Manga          : ``GET /manga/<slug>/``
    - Chapitre       : ``GET /manga/<slug>/<chapter-slug>/``
    - Lecture        : images dans ``div.reading-content img``
    - Lazy-loading   : ``data-src``, ``data-lazy-src``, ``data-original``
    - Pagination     : ``?page=N`` ou ``/page/N/``
    - Cloudflare     : protection fréquente (challenge JS + Turnstile)

Ce mixin fournit les 4 méthodes abstraites de ``BaseParser`` avec des
implémentations par défaut robustes, que les sous-classes peuvent
**surcharger partiellement** (sélecteurs CSS, chemins URL, regex de
détection) ou **complètement** (remplacer ``_parse_search_html``, etc.).

Architecture
============

    class MonParser(MadaraMixin, BaseParser):
        '''Parser pour mon-site.com — basé sur Madara.'''

        # --- Identité (obligatoire) ---
        site_id = "mon_site"
        language = "fr"
        adult = False
        base_url = "https://mon-site.com"

        # --- Surcharges optionnelles ---
        search_path = "/?s={query}&post_type=wp-manga"
        selectors = {**MadaraMixin.default_selectors, "search_item": "..."}
        premium_patterns = [r"\\bsponsor\\b", r"\\bvip\\b"]

        # --- Surcharges de méthodes (optionnel) ---
        async def _parse_search_html(self, html): ...

Le mixin gère automatiquement :
    - La construction des URLs (recherche, manga, chapitre).
    - Le parsing HTML via ``selectolax``.
    - La normalisation des URLs relatives.
    - La détection des chapitres premium / futurs.
    - Le filtrage des trackers et blobs.
    - Le lazy-loading d'images.
    - La délégation Cloudflare via ``playwright_pool`` (si disponible).
    - La pagination (paramètre ``page``).

Le mixin **ne gère pas** :
    - La validation des images (déléguée à ``BaseParser.download_page``).
    - Le retry réseau (délégué à ``HttpSession``).
    - La rotation de miroirs (chaque parser gère la sienne).

Contrat attendu du parser hôte
==============================

Le parser qui hérite de ce mixin **doit** fournir :

    - ``self._session`` (``HttpSession``) — pour les requêtes HTTP.
    - ``self._playwright_pool`` (``PlaywrightPool | None``) — optionnel.
    - ``self.base_url`` (str) — URL canonique.
    - ``self.site_id`` (str) — identifiant unique.
    - ``self.language`` (str) — code langue.
    - ``self.adult`` (bool) — contenu 18+.

Tous ces attributs sont normalement fournis par ``BaseParser.__init__``.
Si l'un manque, le mixin lève une ``AttributeError`` explicite lors du
premier appel.

Example:
    ::

        from nexusdl.parsers.mixins.wordpress_madara import MadaraMixin
        from nexusdl.parsers.base import BaseParser

        class MyMadaraParser(MadaraMixin, BaseParser):
            site_id = "my_site"
            language = "en"
            adult = False
            base_url = "https://my-site.example"

        # Utilisation
        parser = MyMadaraParser(config, session)
        results = await parser.search("one piece")
        manga = await parser.get_manga(results[0].url)
        chapters = await parser.get_chapters(manga)
        pages = await parser.get_pages(chapters[0])
"""

from __future__ import annotations

import re
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar, Final
from urllib.parse import urljoin, urlparse

from loguru import logger

from nexusdl.core.constants import (
    ContentRating,
    Language,
    MangaStatus,
)
from nexusdl.core.exceptions import (
    ChapterDownloadError,
    MangaNotFoundError,
    ParserError,
    SiteUnreachableError,
)
from nexusdl.core.models.manga import Chapter, Manga, Page
from nexusdl.parsers.base import SearchResult

if TYPE_CHECKING:
    from collections.abc import Iterable

# ============================================================================
#  CONSTANTES PARTAGÉES
# ============================================================================

#: Marqueurs de challenge Cloudflare — utilisés pour détecter une page
#: de challenge non résolu. La liste couvre les challenges standard et
#: Turnstile.
CLOUDFLARE_MARKERS: Final[tuple[str, ...]] = (
    "Just a moment",
    "cf-chl-",
    "Checking your browser",
    "cf_chl_",
    "__cf_bm",
    "turnstile",
    "challenge-platform",
)

#: Domaines de tracking génériques — exclus des pages téléchargées.
TRACKING_DOMAINS: Final[tuple[str, ...]] = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "facebook.com/tr",
    "facebook.net",
    "analytics.",
    "pixel.",
    "stats.",
    "tracker.",
    "exoclick.com",
    "juicyads.com",
    "trafficjunky.net",
)

#: Extensions acceptées pour les images.
ALLOWED_IMAGE_EXTS: Final[frozenset[str]] = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"},
)

#: Regex pour détecter les blobs JS non téléchargeables.
BLOB_URL_PATTERN: Final[re.Pattern[str]] = re.compile(r"^blob:")

#: Regex génériques pour le numéro de chapitre.
DEFAULT_CHAPTER_NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:chapitre|chap|ch|chapter|ch\.)\s*([0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE,
)

#: Regex générique pour extraire le slug manga d'une URL.
DEFAULT_MANGA_SLUG_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"/manga/([^/]+)/?",
)

#: Regex pour détecter une page 404.
PAGE_404_MARKERS: Final[tuple[str, ...]] = (
    "404",
    "not found",
    "page introuvable",
    "introuvable",
    "doesn't exist",
)


# ============================================================================
#  HELPERS INTERNES (non exportés)
# ============================================================================


def _clean_text(text: str | None) -> str:
    """Nettoie un texte HTML (entités, whitespace).

    Args:
        text: Texte brut ou None.

    Returns:
        Texte nettoyé, ou chaîne vide.
    """
    if not text:
        return ""
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_blob_url(url: str) -> bool:
    """Détecte une URL ``blob:`` (non téléchargeable).

    Args:
        url: URL à tester.

    Returns:
        True si l'URL est un blob.
    """
    return bool(BLOB_URL_PATTERN.match(url))


def _is_tracking_url(url: str) -> bool:
    """Détecte une URL de tracking.

    Args:
        url: URL à tester.

    Returns:
        True si l'URL pointe vers un domaine de tracking connu.
    """
    url_lower = url.lower()
    return any(td in url_lower for td in TRACKING_DOMAINS)


def _is_cloudflare_challenge(html: str) -> bool:
    """Détecte une page de challenge Cloudflare.

    Args:
        html: Contenu HTML.

    Returns:
        True si le HTML contient des marqueurs CF.
    """
    html_lower = html.lower()
    return any(marker.lower() in html_lower for marker in CLOUDFLARE_MARKERS)


def _normalize_image_url(url: str, base: str) -> str:
    """Normalise une URL d'image.

    Args:
        url: URL brute.
        base: URL de base pour résolution.

    Returns:
        URL absolue.
    """
    url = url.strip()
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith(("http://", "https://")):
        return url
    return urljoin(base, url)


def _safe_float(value: str) -> float | None:
    """Convertit une chaîne en float, ou retourne None.

    Args:
        value: Chaîne à convertir.

    Returns:
        Float, ou None si non convertible.
    """
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# ============================================================================
#  MIXIN
# ============================================================================


class MadaraMixin:
    """Mixin fournissant les implémentations par défaut pour sites Madara.

    Cette classe **ne peut pas être instanciée seule** — elle est conçue
    pour être composée avec ``BaseParser`` via héritage multiple :

        class FooParser(MadaraMixin, BaseParser):
            ...

    L'ordre est critique : ``MadaraMixin`` **en premier** pour que ses
    méthodes priment sur celles de ``BaseParser`` (MRO).

    Attributes:
        default_selectors: Sélecteurs CSS par défaut du thème Madara.
        search_path: Template d'URL pour la recherche (avec ``{query}``).
        manga_path_template: Template d'URL pour un manga (``{slug}``).
        chapter_path_template: Template d'URL pour un chapitre.
        search_page_param: Nom du query param de pagination (défaut : ``page``).
        search_query_param: Nom du query param de recherche (défaut : ``s``).
        cloudflare_strategy: Stratégie de bypass (``"playwright"`` par défaut).
        premium_patterns: Liste de regex détectant les chapitres premium.
        future_patterns: Liste de regex détectant les chapitres à venir.
    """

    # ------------------------------------------------------------------------
    #  SÉLECTEURS CSS PAR DÉFAUT DU THÈME MADARA
    # ------------------------------------------------------------------------
    # Ces sélecteurs couvrent 95% des sites Madara. Les sous-classes
    # peuvent les surcharger en fusionnant avec ceux-ci :
    #
    #     selectors = {**MadaraMixin.default_selectors, "search_item": "..."}

    default_selectors: ClassVar[dict[str, str]] = {
        # --- Recherche ---
        "search_item": "div.page-item-detail, div.c-tabs-item__content",
        "search_title": "h3.h5 a, div.post-title h3 a",
        "search_cover": "div.c-image-hover img, div.item-thumb img",
        "search_link": "h3.h5 a, div.post-title h3 a",
        # --- Page manga ---
        "manga_title": "div.post-title h1, h1.entry-title",
        "manga_description": "div.description-summary, div.summary__content",
        "manga_cover": "div.summary_image img, div.thumb img",
        "manga_author": "div.author-content a, div.author-content",
        "manga_artist": "div.artist-content a",
        "manga_genres": "div.genres-content a, div.genres a",
        "manga_status": "div.summary-content",
        "manga_year": "div.summary-content",
        # --- Chapitres ---
        "chapter_list": "ul.main.version-chap, div.listing-chapters_wrap ul",
        "chapter_item": "li.wp-manga-chapter a",
        "chapter_date": "li.wp-manga-chapter span.chapter-release-date",
        # --- Pages ---
        "page_image": "div.reading-content img, div#readerarea img",
        "page_container": "div.reading-content, div#readerarea",
    }

    #: Sélecteurs effectifs du parser (fusionnés par la sous-classe).
    #: Si non fourni par la sous-classe, prend ``default_selectors``.
    selectors: ClassVar[dict[str, str]] = default_selectors

    # ------------------------------------------------------------------------
    #  TEMPLATES D'URL
    # ------------------------------------------------------------------------

    #: Chemin de recherche avec placeholders ``{query}`` et ``{page}``.
    #: Pour les sites avec pagination par path, utiliser
    #: ``/?s={query}&post_type=wp-manga`` + ``_page_url()`` custom.
    search_path: ClassVar[str] = "/"

    #: Paramètre de recherche (nom du query param, défaut Madara : ``s``).
    search_query_param: ClassVar[str] = "s"

    #: Paramètre de pagination (défaut Madara : ``page``).
    search_page_param: ClassVar[str] = "page"

    #: Template d'URL pour un manga (``{slug}`` remplacé).
    manga_path_template: ClassVar[str] = "/manga/{slug}/"

    #: Template d'URL pour un chapitre.
    chapter_path_template: ClassVar[str] = "/manga/{slug}/{chapter_slug}/"

    #: Regex pour extraire le slug manga depuis une URL.
    manga_slug_pattern: ClassVar[re.Pattern[str]] = DEFAULT_MANGA_SLUG_PATTERN

    #: Regex pour extraire un numéro de chapitre depuis un texte.
    chapter_number_pattern: ClassVar[re.Pattern[str]] = DEFAULT_CHAPTER_NUMBER_PATTERN

    # ------------------------------------------------------------------------
    #  CLOUDFLARE
    # ------------------------------------------------------------------------

    #: Stratégie de bypass CF : ``"playwright"``, ``"flaresolverr"`` ou ``"none"``.
    cloudflare_strategy: ClassVar[str] = "playwright"

    #: Sélecteur d'attente après navigation Playwright (pour attendre la
    #: fin du challenge CF et le chargement du contenu Madara).
    cloudflare_wait_selector: ClassVar[str | None] = (
        "div.post-title, div.wp-manga-chapter, div.reading-content"
    )

    # ------------------------------------------------------------------------
    #  DÉTECTION PREMIUM / FUTUR
    # ------------------------------------------------------------------------

    #: Patterns regex détectant les chapitres premium.
    #: Les sous-classes peuvent étendre cette liste :
    #:     premium_patterns = [*MadaraMixin.premium_patterns, r"\\bsponsor\\b"]
    premium_patterns: ClassVar[list[str]] = [
        r"\b(premium|vip|payant|locked|🔒|💎|⭐)\b",
    ]

    #: Patterns regex détectant les chapitres à venir.
    future_patterns: ClassVar[list[str]] = [
        r"\b(soon|bient[oô]t|[aà] venir|upcoming|TBA)\b",
    ]

    #: Préfixes ajoutés aux titres (personnalisables).
    premium_prefix: ClassVar[str] = "[Premium]"
    future_prefix: ClassVar[str] = "[À venir]"

    # ------------------------------------------------------------------------
    #  INITIALISATION (peut être appelée par le parser hôte si besoin)
    # ------------------------------------------------------------------------

    def _init_madara(self) -> None:
        """Initialise les attributs internes du mixin.

        Appelée automatiquement au premier usage. Les sous-classes qui
        ont besoin de hooks dans ``__init__`` peuvent l'appeler après
        ``super().__init__()``.

        Idempotente — peut être appelée plusieurs fois sans effet.
        """
        if getattr(self, "_madara_initialized", False):
            return

        # Compile les patterns regex (plus rapide que re.search à chaque appel).
        self._premium_regex: tuple[re.Pattern[str], ...] = tuple(
            re.compile(p, re.IGNORECASE) for p in self.premium_patterns
        )
        self._future_regex: tuple[re.Pattern[str], ...] = tuple(
            re.compile(p, re.IGNORECASE) for p in self.future_patterns
        )

        # Fusionne les sélecteurs : si la sous-classe n'a pas surchargé,
        # on utilise les défauts du mixin.
        if "selectors" not in type(self).__dict__:
            self.selectors = dict(self.default_selectors)

        self._madara_initialized = True
        logger.bind(site=getattr(self, "site_id", "?")).debug(
            "MadaraMixin initialisé ({} sélecteurs, {} patterns premium, {} futurs)",
            len(self.selectors),
            len(self._premium_regex),
            len(self._future_regex),
        )

    def _ensure_initialized(self) -> None:
        """Vérifie que le mixin est initialisé (lazy init).

        Appelée en tête de chaque méthode publique. Évite d'imposer un
        ``__init__`` spécifique aux sous-classes.
        """
        if not getattr(self, "_madara_initialized", False):
            self._init_madara()

    # ------------------------------------------------------------------------
    #  MÉTHODES PUBLIQUES (implémentent l'API BaseParser)
    # ------------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        page: int = 1,
    ) -> list[SearchResult]:
        """Recherche des mangas sur un site Madara.

        Construit l'URL de recherche ``/?s=<query>&post_type=wp-manga``
        (avec pagination), récupère le HTML, et parse les items.

        Args:
            query: Terme de recherche (non vide).
            page: Numéro de page (1-indexé).

        Returns:
            Liste de résultats déduplicés.

        Raises:
            ParserError: Si la query est vide ou la page invalide.
            SiteUnreachableError: Si le site ne répond pas.
        """
        self._ensure_initialized()

        query = query.strip()
        if not query:
            msg = "query vide après normalisation"
            raise ParserError(msg)
        if page < 1:
            msg = f"page doit être >= 1, reçu {page}"
            raise ParserError(msg)

        logger.bind(site=self.site_id).debug(
            "Recherche '{}' page {}",
            query,
            page,
        )

        # Construit les paramètres
        params: dict[str, str] = {
            self.search_query_param: query,
            "post_type": "wp-manga",
        }
        if page > 1:
            params[self.search_page_param] = str(page)

        html = await self._fetch_html(self.base_url + self.search_path, params=params)
        results = self._parse_search_html(html)

        logger.bind(site=self.site_id).info(
            "{} résultat(s) pour '{}' (page {})",
            len(results),
            query,
            page,
        )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère les métadonnées complètes d'un manga Madara.

        Args:
            url_or_id: URL absolue, chemin relatif, ou slug.

        Returns:
            Objet Manga peuplé (avec chapitres si la page les liste).

        Raises:
            MangaNotFoundError: Si la page renvoie 404.
            SiteUnreachableError: Si le site ne répond pas.
        """
        self._ensure_initialized()

        url = self._resolve_manga_url(url_or_id)
        logger.bind(site=self.site_id).debug("Récupération manga : {}", url)

        html = await self._fetch_html(url)

        if self._is_404_page(html):
            msg = f"Manga introuvable : {url}"
            raise MangaNotFoundError(msg)

        manga = self._parse_manga_html(html, url)

        logger.bind(site=self.site_id).info(
            "Manga : '{}' ({} chapitre(s))",
            manga.title,
            len(manga.chapters),
        )
        return manga

    async def get_chapters(self, manga: Manga) -> list[Chapter]:
        """Récupère la liste des chapitres d'un manga Madara.

        Si les chapitres sont déjà présents dans ``manga.chapters``
        (extraits par ``get_manga``), les retourne directement.
        Sinon, refait une requête pour parser la page manga.

        Tri : par numéro décroissant (plus récent en premier).

        Args:
            manga: Manga cible.

        Returns:
            Liste de chapitres triés.
        """
        self._ensure_initialized()

        if manga.chapters:
            chapters = list(manga.chapters)
        else:
            html = await self._fetch_html(str(manga.url))
            chapters = self._parse_chapters_html(html)

        # Filtre les chapitres sans URL
        valid = [ch for ch in chapters if ch.url and str(ch.url).strip()]
        filtered = len(chapters) - len(valid)
        if filtered:
            logger.bind(site=self.site_id).debug(
                "{} chapitre(s) sans URL filtré(s)",
                filtered,
            )

        # Tri décroissant par numéro (les non-numériques à la fin)
        valid.sort(key=_chapter_sort_key, reverse=True)

        logger.bind(site=self.site_id).info(
            "{} chapitre(s) pour '{}'",
            len(valid),
            manga.title,
        )
        return valid

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre Madara.

        Gère le lazy-loading, le filtrage des trackers et des blobs,
        et la validation de l'extension.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste de pages ordonnées.

        Raises:
            ChapterDownloadError: Si aucune page n'est trouvée.
        """
        self._ensure_initialized()

        url = str(chapter.url)
        logger.bind(site=self.site_id).debug(
            "Pages pour ch.{} : {}",
            chapter.number,
            url,
        )

        html = await self._fetch_html(url)
        pages = self._parse_pages_html(html)

        if not pages:
            msg = f"Aucune page trouvée pour ch.{chapter.number} ({url})"
            raise ChapterDownloadError(msg)

        logger.bind(site=self.site_id).info(
            "{} page(s) pour ch.{}",
            len(pages),
            chapter.number,
        )
        return pages

    # ------------------------------------------------------------------------
    #  PARSING HTML — méthode par défaut (overridable)
    # ------------------------------------------------------------------------

    def _parse_search_html(self, html: str) -> list[SearchResult]:
        """Parse la page de résultats de recherche Madara.

        Itère sur ``selectors["search_item"]``, extrait le titre, le
        lien, la couverture et l'auteur (si présent). Déduplique par URL.

        Args:
            html: HTML de la page de recherche.

        Returns:
            Liste de résultats triés par ordre d'apparition.
        """
        from selectolax.parser import HTMLParser  # noqa: PLC0415

        tree = HTMLParser(html)
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        items = tree.css(self.selectors["search_item"])
        if not items:
            logger.bind(site=self.site_id).debug(
                "Aucun résultat (sélecteur '{}' vide)",
                self.selectors["search_item"],
            )
            return []

        for item in items:
            try:
                title_node = item.css_first(self.selectors["search_title"])
                if title_node is None:
                    continue

                link = title_node.attributes.get("href") or ""
                if not link:
                    continue

                title = _clean_text(title_node.text())
                if not title:
                    continue

                url = self._normalize_url(link)
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # Couverture (lazy-load aware)
                cover_url: str | None = None
                cover_node = item.css_first(self.selectors["search_cover"])
                if cover_node is not None:
                    raw_cover = (
                        cover_node.attributes.get("data-src")
                        or cover_node.attributes.get("data-lazy-src")
                        or cover_node.attributes.get("src")
                        or ""
                    )
                    if raw_cover:
                        cover_url = _normalize_image_url(raw_cover, self.base_url)

                # Auteur (optionnel — peut être absent sur certains sites)
                author: str | None = None
                author_node = item.css_first(self.selectors["manga_author"])
                if author_node is not None:
                    author = _clean_text(author_node.text()) or None

                slug = self._extract_slug(url)

                results.append(
                    SearchResult(
                        source_id=slug or url,
                        title=title,
                        url=url,  # type: ignore[arg-type]
                        cover_url=cover_url,  # type: ignore[arg-type]
                        site_id=self.site_id,
                        language=self.language,
                        adult=self.adult,
                        author=author,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.bind(site=self.site_id).debug(
                    "Erreur parsing item recherche : {}",
                    exc,
                )
                continue

        return results

    def _parse_manga_html(self, html: str, url: str) -> Manga:
        """Parse la page manga Madara.

        Extrait titre, description, couverture, auteur, artiste, genres,
        statut, année, et chapitres (si présents sur la page).

        Args:
            html: HTML de la page manga.
            url: URL absolue du manga.

        Returns:
            Objet Manga peuplé.
        """
        from selectolax.parser import HTMLParser  # noqa: PLC0415

        tree = HTMLParser(html)

        # --- Titre ---
        title_node = tree.css_first(self.selectors["manga_title"])
        title = _clean_text(title_node.text()) if title_node else ""

        # --- Description ---
        desc_node = tree.css_first(self.selectors["manga_description"])
        description = _clean_text(desc_node.text()) if desc_node else None

        # --- Couverture ---
        cover_url: str | None = None
        cover_node = tree.css_first(self.selectors["manga_cover"])
        if cover_node is not None:
            raw_cover = (
                cover_node.attributes.get("data-src")
                or cover_node.attributes.get("data-lazy-src")
                or cover_node.attributes.get("src")
            )
            if raw_cover:
                cover_url = _normalize_image_url(raw_cover, self.base_url)

        # --- Auteur / Artiste ---
        author: str | None = None
        author_node = tree.css_first(self.selectors["manga_author"])
        if author_node is not None:
            author = _clean_text(author_node.text()) or None

        artist: str | None = None
        artist_node = tree.css_first(self.selectors["manga_artist"])
        if artist_node is not None:
            artist = _clean_text(artist_node.text()) or None

        # --- Genres ---
        genres: list[str] = []
        for genre_node in tree.css(self.selectors["manga_genres"]):
            g = _clean_text(genre_node.text())
            if g and g not in genres:
                genres.append(g)

        # --- Statut et année ---
        status, year = self._parse_status_and_year(tree)

        # --- Chapitres (souvent sur la même page) ---
        chapters = self._parse_chapters_html(html)

        # --- Slug et ID ---
        slug = self._extract_slug(url) or "unknown"

        # --- Mise à jour ---
        updated_at = datetime.now(UTC)

        # --- Content rating (hérité du parser) ---
        content_rating = getattr(self, "content_rating", ContentRating.SAFE)

        # --- Language (héritée du parser) ---
        language = self._resolve_language_enum()

        return Manga(
            id=f"{self.site_id}:{slug}",
            source_id=slug,
            site=self.site_id,
            title=title,
            alternative_titles=[],
            description=description,
            author=author,
            artist=artist,
            genres=genres,
            status=status,
            year=year,
            cover_url=cover_url,  # type: ignore[arg-type]
            language=language,
            content_rating=content_rating,
            chapters=chapters,
            url=url,  # type: ignore[arg-type]
            updated_at=updated_at,
        )

    def _parse_chapters_html(self, html: str) -> list[Chapter]:
        """Parse la liste des chapitres depuis une page manga.

        Détecte les chapitres premium et à venir (via les regex
        ``premium_patterns`` et ``future_patterns``). Ajoute les préfixes
        ``[Premium]`` et ``[À venir]`` aux titres.

        Args:
            html: HTML de la page manga.

        Returns:
            Liste de chapitres triés par ordre d'apparition (le tri
            final est appliqué par ``get_chapters``).
        """
        from selectolax.parser import HTMLParser  # noqa: PLC0415

        tree = HTMLParser(html)
        chapters: list[Chapter] = []
        seen_urls: set[str] = set()

        for item in tree.css(self.selectors["chapter_item"]):
            try:
                link = item.attributes.get("href") or ""
                if not link:
                    continue

                url = self._normalize_url(link)
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                raw_text = _clean_text(item.text())
                number = self._extract_chapter_number(raw_text)

                chapter_title = re.sub(
                    r"^\s*(chapitre|chap|ch|chapter)\s*[\d.]+\s*:?\s*",
                    "",
                    raw_text,
                    flags=re.IGNORECASE,
                ).strip() or raw_text

                # Détection premium / futur
                is_premium, is_future = self._detect_chapter_flags(
                    raw_text,
                    chapter_title,
                    link,
                )

                display_title = chapter_title
                if is_premium:
                    display_title = f"{self.premium_prefix} {display_title}"
                if is_future:
                    display_title = f"{self.future_prefix} {display_title}"

                chapter_slug = url.rstrip("/").split("/")[-1] or str(number)
                chapter_id = f"{self.site_id}:{chapter_slug}"

                chapters.append(
                    Chapter(
                        id=chapter_id,
                        source_id=chapter_slug,
                        title=display_title,
                        number=number,
                        volume=None,
                        language=self._resolve_language_enum(),
                        pages_count=None,
                        published_at=None,
                        url=url,  # type: ignore[arg-type]
                        pages=[],
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.bind(site=self.site_id).debug(
                    "Erreur parsing chapitre : {}",
                    exc,
                )
                continue

        return chapters

    def _parse_pages_html(self, html: str) -> list[Page]:
        """Parse les URLs des pages d'un chapitre Madara.

        Gère le lazy-loading, filtre les trackers et blobs, valide les
        extensions. Aucune exception n'est levée — les items invalides
        sont silencieusement ignorés (avec log DEBUG).

        Args:
            html: HTML de la page du chapitre.

        Returns:
            Liste de pages ordonnées.
        """
        from selectolax.parser import HTMLParser  # noqa: PLC0415

        tree = HTMLParser(html)
        pages: list[Page] = []
        blob_count = 0
        tracking_count = 0

        for idx, img in enumerate(tree.css(self.selectors["page_image"])):
            raw_url = (
                img.attributes.get("data-src")
                or img.attributes.get("data-lazy-src")
                or img.attributes.get("data-original")
                or img.attributes.get("src")
                or ""
            )

            if not raw_url:
                continue

            if _is_blob_url(raw_url):
                blob_count += 1
                continue

            if _is_tracking_url(raw_url):
                tracking_count += 1
                continue

            url = _normalize_image_url(raw_url, self.base_url)

            # Extrait et valide l'extension
            ext = self._extract_image_ext(url)
            filename = f"page_{idx:04d}{ext}"

            pages.append(
                Page(
                    index=idx,
                    url=url,  # type: ignore[arg-type]
                    filename=filename,
                    checksum=None,
                ),
            )

        if blob_count:
            logger.bind(site=self.site_id).warning(
                "{} image(s) blob: ignorée(s) — extraction partielle",
                blob_count,
            )
        if tracking_count:
            logger.bind(site=self.site_id).debug(
                "{} image(s) de tracking ignorée(s)",
                tracking_count,
            )

        return pages

    # ------------------------------------------------------------------------
    #  HELPERS D'EXTRACTION — overridables
    # ------------------------------------------------------------------------

    def _parse_status_and_year(
        self,
        tree: Any,
    ) -> tuple[MangaStatus, int | None]:
        """Extrait le statut et l'année depuis un arbre HTML.

        Args:
            tree: Arbre ``selectolax.HTMLParser``.

        Returns:
            Tuple ``(status, year)``. ``year`` peut être None.
        """
        status = MangaStatus.UNKNOWN
        year: int | None = None

        for node in tree.css(self.selectors["manga_status"]):
            text = _clean_text(node.text()).lower()
            if not text:
                continue

            if "en cours" in text or "ongoing" in text:
                status = MangaStatus.ONGOING
            elif "terminé" in text or "completed" in text or "complete" in text:
                status = MangaStatus.COMPLETED
            elif "hiatus" in text or "on hold" in text:
                status = MangaStatus.HIATUS
            elif "annulé" in text or "cancelled" in text or "canceled" in text:
                status = MangaStatus.CANCELLED

            # Cherche une année à 4 chiffres entre 1900 et 2099
            year_match = re.search(r"\b(19|20)\d{2}\b", text)
            if year_match and year is None:
                with suppress(ValueError):
                    year = int(year_match.group(0))

        return status, year

    def _extract_chapter_number(self, text: str) -> float | str:
        """Extrait un numéro de chapitre depuis un texte.

        Args:
            text: Texte à parser.

        Returns:
            Numéro en float, ou texte original si non numérique.
        """
        match = self.chapter_number_pattern.search(text)
        if match:
            value = _safe_float(match.group(1))
            if value is not None:
                return value

        # Fallback : premier nombre isolé
        numbers = re.findall(r"\d+(?:\.\d+)?", text)
        if numbers:
            value = _safe_float(numbers[0])
            if value is not None:
                return value

        return _clean_text(text)

    def _detect_chapter_flags(
        self,
        raw_text: str,
        chapter_title: str,
        link: str,
    ) -> tuple[bool, bool]:
        """Détecte les flags premium et futur d'un chapitre.

        Cherche dans le texte brut, le titre nettoyé, et l'URL (certains
        sites marquent les chapitres premium dans le path).

        Args:
            raw_text: Texte brut de l'élément.
            chapter_title: Titre nettoyé.
            link: URL du chapitre.

        Returns:
            Tuple ``(is_premium, is_future)``.
        """
        haystack = f"{raw_text} {chapter_title} {link}"

        is_premium = any(rx.search(haystack) for rx in self._premium_regex)
        is_future = any(rx.search(haystack) for rx in self._future_regex)

        return is_premium, is_future

    def _extract_image_ext(self, url: str) -> str:
        """Extrait l'extension d'une URL d'image (validée).

        Args:
            url: URL de l'image.

        Returns:
            Extension avec point (ex: ``".jpg"``). Retourne ``".jpg"``
            si l'extension est absente ou non whitelistée.
        """
        from pathlib import Path  # noqa: PLC0415

        ext = Path(urlparse(url).path).suffix.lower()
        if ext and ext in ALLOWED_IMAGE_EXTS:
            return ext
        return ".jpg"

    def _extract_slug(self, url: str) -> str | None:
        """Extrait le slug manga depuis une URL.

        Args:
            url: URL du manga.

        Returns:
            Slug, ou None si introuvable.
        """
        match = self.manga_slug_pattern.search(url)
        return match.group(1) if match else None

    # ------------------------------------------------------------------------
    #  HELPERS D'URL
    # ------------------------------------------------------------------------

    def _normalize_url(self, url: str) -> str:
        """Normalise une URL (relative → absolue).

        Args:
            url: URL relative ou absolue.

        Returns:
            URL absolue.
        """
        url = url.strip()
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith(("http://", "https://")):
            return url
        if url.startswith("/"):
            return self.base_url + url
        return urljoin(self.base_url + "/", url)

    def _resolve_manga_url(self, url_or_id: str) -> str:
        """Résout une entrée utilisateur en URL absolue.

        Args:
            url_or_id: URL, chemin relatif, ou slug.

        Returns:
            URL absolue.
        """
        url_or_id = url_or_id.strip()

        if url_or_id.startswith(("http://", "https://")):
            return self._normalize_url(url_or_id)
        if url_or_id.startswith("/"):
            return self._normalize_url(url_or_id)

        # Slug brut → construire via le template
        return self.base_url + self.manga_path_template.format(slug=url_or_id)

    # ------------------------------------------------------------------------
    #  HELPERS DE LECTURE — statut HTTP, 404
    # ------------------------------------------------------------------------

    def _is_404_page(self, html: str) -> bool:
        """Détecte une page 404 Madara.

        Args:
            html: HTML de la page.

        Returns:
            True si c'est une page 404.
        """
        try:
            from selectolax.parser import HTMLParser  # noqa: PLC0415

            tree = HTMLParser(html)
            for selector in ("title", "h1"):
                node = tree.css_first(selector)
                if node:
                    text = node.text().lower()
                    if any(m in text for m in PAGE_404_MARKERS):
                        return True
        except ImportError:
            # Fallback basique si selectolax absent
            html_lower = html.lower()
            return any(m in html_lower for m in PAGE_404_MARKERS)
        return False

    # ------------------------------------------------------------------------
    #  HELPERS DE FETCH — HTTP + Cloudflare
    # ------------------------------------------------------------------------

    async def _fetch_html(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        max_attempts: int = 3,
    ) -> str:
        """Récupère le HTML d'une URL avec gestion Cloudflare.

        Stratégie :
            1. Requête HTTP standard via ``self._session``.
            2. Si challenge CF détecté et ``cloudflare_strategy == "playwright"``,
               bascule sur ``self._playwright_pool``.
            3. Retry avec backoff exponentiel.

        Args:
            url: URL cible.
            params: Query parameters.
            max_attempts: Nombre maximal de tentatives.

        Returns:
            HTML de la page.

        Raises:
            SiteUnreachableError: Si toutes les stratégies échouent.
        """
        session = getattr(self, "_session", None)
        if session is None:
            msg = (
                f"{type(self).__name__} n'expose pas self._session — "
                "héritage BaseParser requis pour utiliser MadaraMixin"
            )
            raise AttributeError(msg)

        playwright_pool = getattr(self, "_playwright_pool", None)
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                response = await session.get(
                    url,
                    params=params,
                    timeout=getattr(self, "http_timeout", 30.0),
                    headers={"Referer": self.base_url},
                )

                if response.status_code == 200:  # noqa: PLR2004
                    html = response.text
                    if not _is_cloudflare_challenge(html):
                        return html
                    logger.bind(site=self.site_id).debug(
                        "Challenge CF détecté (HTTP), bascule Playwright",
                    )
                elif response.status_code in (403, 503):  # noqa: PLR2004
                    logger.bind(site=self.site_id).debug(
                        "HTTP {} — challenge CF probable",
                        response.status_code,
                    )

            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.bind(site=self.site_id).debug(
                    "Tentative HTTP {}/{} échouée : {}",
                    attempt,
                    max_attempts,
                    exc,
                )

            # Fallback Playwright
            if playwright_pool is not None and self.cloudflare_strategy == "playwright":
                try:
                    full_url = url
                    if params:
                        query = "&".join(f"{k}={v}" for k, v in params.items())
                        full_url = f"{url}?{query}"

                    html = await playwright_pool.fetch_html(
                        full_url,
                        wait_selector=self.cloudflare_wait_selector,
                        timeout=getattr(self, "playwright_timeout", 45.0),
                    )
                    if not _is_cloudflare_challenge(html):
                        return html
                    logger.bind(site=self.site_id).debug(
                        "Playwright : challenge CF persiste",
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.bind(site=self.site_id).debug(
                        "Playwright tentative {}/{} échouée : {}",
                        attempt,
                        max_attempts,
                        exc,
                    )

            if attempt < max_attempts:
                import asyncio  # noqa: PLC0415

                backoff = 0.5 * (2 ** (attempt - 1))
                await asyncio.sleep(backoff)

        msg = f"Impossible de récupérer {url} après {max_attempts} tentative(s)"
        raise SiteUnreachableError(msg) from last_error

    # ------------------------------------------------------------------------
    #  UTILITAIRES
    # ------------------------------------------------------------------------

    def _resolve_language_enum(self) -> Language:
        """Résout ``self.language`` (str) en ``Language`` (Enum).

        Retourne ``Language.EN`` par défaut si le code langue n'est pas
        reconnu.

        Returns:
            Membre de l'enum ``Language``.
        """
        lang_str = getattr(self, "language", "en")
        try:
            return Language(lang_str)
        except (ValueError, KeyError):
            logger.bind(site=self.site_id).debug(
                "Langue '{}' non reconnue, fallback sur EN",
                lang_str,
            )
            return Language.EN

    # ------------------------------------------------------------------------
    #  INTROSPECTION
    # ------------------------------------------------------------------------

    def describe_madara_config(self) -> dict[str, Any]:
        """Retourne la configuration effective du mixin.

        Utile pour la CLI (``nexusdl sites info <site>``) et pour les
        tests qui veulent vérifier les surcharges appliquées.

        Returns:
            Dict sérialisable JSON.
        """
        self._ensure_initialized()
        return {
            "site_id": getattr(self, "site_id", ""),
            "base_url": getattr(self, "base_url", ""),
            "search_path": self.search_path,
            "search_query_param": self.search_query_param,
            "search_page_param": self.search_page_param,
            "manga_path_template": self.manga_path_template,
            "chapter_path_template": self.chapter_path_template,
            "cloudflare_strategy": self.cloudflare_strategy,
            "selectors_count": len(self.selectors),
            "selectors": dict(self.selectors),
            "premium_patterns": list(self.premium_patterns),
            "future_patterns": list(self.future_patterns),
            "premium_prefix": self.premium_prefix,
            "future_prefix": self.future_prefix,
        }


# ============================================================================
#  HELPERS DE TRI (module-level pour test isolation)
# ============================================================================


def _chapter_sort_key(chapter: Chapter) -> float:
    """Clé de tri numérique pour un chapitre.

    Args:
        chapter: Chapitre à trier.

    Returns:
        Numéro en float, ou 0.0 si non numérique.
    """
    try:
        return float(chapter.number)
    except (ValueError, TypeError):
        return 0.0


# ============================================================================
#  EXPORTS
# ============================================================================

__all__ = [
    "ALLOWED_IMAGE_EXTS",
    "CLOUDFLARE_MARKERS",
    "MadaraMixin",
    "TRACKING_DOMAINS",
]
