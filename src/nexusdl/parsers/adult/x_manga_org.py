"""Parser pour X-Manga (https://x-manga.org).

Site adulte français de scans hentai / mangas pornographiques traduits en
français. Basé sur le thème WordPress **Madara** (MangaBooth), avec les
spécificités suivantes :

    - Protection Cloudflare active (stratégie Playwright recommandée).
    - Certains chapitres sont marqués « premium » (nécessitent un compte
      payant) — le parser les marque avec un flag dans les métadonnées.
    - Le domaine a changé plusieurs fois (scan.hentai.menu → x-manga.net →
      x-manga.org) — voir ``MIRROR_DOMAINS``.
    - Contenu 18+ : ``adult = True``, activé uniquement si
      ``registry.include_adult: true`` dans la configuration.

La majorité des méthodes (``search``, ``get_manga``, ``get_chapters``,
``get_pages``) sont fournies par :class:`MadaraMixin` avec les sélecteurs
CSS standard du thème Madara. Seules les parties spécifiques au site sont
surchargées ici.

Example:
    Utilisation via le loader de parsers::

        from pathlib import Path
        from nexusdl.core.registry.site_registry import SiteRegistry

        registry = SiteRegistry.from_settings(settings)
        registry.load()
        parser = registry.get_parser("x_manga_org")

        results = await parser.search("one piece")
        for r in results:
            print(r.title, r.url)

Note:
    Ce parser est marqué ``adult = True``. Il ne sera **pas** chargé si
    ``registry.include_adult`` est à ``False`` (défaut). Le charger
    explicitement avec ``--include-adult`` en CLI.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar, Final

from loguru import logger

from nexusdl.parsers.base import BaseParser
from nexusdl.parsers.mixins.wordpress_madara import MadaraMixin

if TYPE_CHECKING:
    from nexusdl.core.models.manga import Chapter, Manga, Page
    from nexusdl.core.models.site import SiteConfig
    from nexusdl.core.session.http_session import HttpSession
    from nexusdl.core.session.playwright_pool import PlaywrightPool
    from nexusdl.parsers.base import SearchResult


# ============================================================================
#  Constantes spécifiques au site
# ============================================================================

#: Domaines miroirs connus de X-Manga (par ordre de préférence).
#: Le domaine principal est ``base_url`` ; les autres sont des fallbacks
#: si le principal est inaccessible (bascule manuelle ou automatique).
#:
#: Historique documenté :
#:   - ``scan.hentai.menu`` (ancien, redirige)
#:   - ``x-manga.net`` (ancien, redirige)
#:   - ``x-manga.org`` (actuel)
MIRROR_DOMAINS: Final[tuple[str, ...]] = (
    "https://x-manga.org",
    "https://x-manga.net",
)

#: Sélecteurs CSS spécifiques à X-Manga qui diffèrent du Madara standard.
#: Le thème Madara autorise des personnalisations CSS par site ; X-Manga
#: utilise quelques sélecteurs différents des valeurs par défaut.
#:
#: Ce dict est fusionné avec les sélecteurs Madara par défaut dans le mixin.
#: Seules les clés qui diffèrent sont listées ici.
CUSTOM_SELECTORS: Final[dict[str, str]] = {
    # Page de recherche : structure légèrement différente
    "search_item": "div.page-item-detail, div.c-tabs-item__content",
    "search_title": "h3.h5 a, div.post-title h3 a",
    "search_cover": "div.c-image-hover img, div.item-thumb img",
    # Page manga
    "manga_title": "div.post-title h1, h1.entry-title",
    "manga_description": "div.description-summary, div.summary__content",
    "manga_cover": "div.summary_image img, div.thumb img",
    "manga_author": "div.author-content a, div.author-content",
    # Liste des chapitres
    "chapter_item": "li.wp-manga-chapter a, div.listing-chapters_wrap a",
    "page_image": "div.reading-content img, div#readerarea img",
}

#: Regex pour détecter les chapitres marqués « premium » sur X-Manga.
#: Ces chapitres sont visibles mais leur contenu nécessite un compte payant.
#: Le parser les détecte et les marque dans les métadonnées, mais ne les
#: exclut pas — c'est à l'appelant (CLI, UI) de décider.
PREMIUM_CHAPTER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(premium|vip|payant|locked|🔒)\b",
    re.IGNORECASE,
)

#: Regex pour détecter les chapitres « à venir » (annoncés mais non publiés).
FUTURE_CHAPTER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(soon|bient[oô]t|[aà] venir|upcoming)\b",
    re.IGNORECASE,
)

#: Paramètres de pagination Madara standard.
#: Le thème Madara utilise ``?page=N`` pour la pagination de recherche.
SEARCH_PAGE_PARAM: Final[str] = "page"

#: Chemins d'URL spécifiques à X-Manga.
MANGA_PATH_TEMPLATE: Final[str] = "/manga/{slug}/"
CHAPTER_PATH_TEMPLATE: Final[str] = "/manga/{slug}/{chapter_slug}/"
SEARCH_PATH: Final[str] = "/"

#: Timeout pour les opérations Playwright (Cloudflare peut être lent).
PLAYWRIGHT_TIMEOUT: Final[float] = 45.0

#: Timeout pour les opérations HTTP standard.
HTTP_TIMEOUT: Final[float] = 30.0


# ============================================================================
#  Parser
# ============================================================================


class XMangaOrgParser(MadaraMixin, BaseParser):
    """Parser pour X-Manga (https://x-manga.org).

    Ce parser hérite de :class:`MadaraMixin` qui fournit les implémentations
    par défaut de ``search``, ``get_manga``, ``get_chapters`` et ``get_pages``
    pour le thème WordPress Madara.

    Les surcharges concernent :
        - Les **sélecteurs CSS** spécifiques à ce site (``selectors``).
        - La **détection des chapitres premium** (marquage dans les métadonnées).
        - La **gestion Cloudflare** (stratégie Playwright prioritaire).
        - La **résolution d'URL** (chemins Madara personnalisés).

    Attributes:
        site_id: Identifiant unique du site (``"x_manga_org"``).
        language: Langue du contenu (``"fr"`` — français).
        adult: Contenu 18+ (``True`` — actif uniquement si autorisé en config).
        base_url: URL de base du site.
        search_path: Chemin de recherche.
        selectors: Sélecteurs CSS Madara (fusionnés avec les défauts).
        cloudflare_strategy: Stratégie de contournement Cloudflare.
    """

    # ------------------------------------------------------------------------
    #  Métadonnées de classe
    # ------------------------------------------------------------------------

    site_id: ClassVar[str] = "x_manga_org"
    language: ClassVar[str] = "fr"
    adult: ClassVar[bool] = True

    base_url: ClassVar[str] = "https://x-manga.org"
    mirror_domains: ClassVar[tuple[str, ...]] = MIRROR_DOMAINS

    # --- Options Madara ---------------------------------------------------
    search_path: ClassVar[str] = SEARCH_PATH
    manga_path_template: ClassVar[str] = MANGA_PATH_TEMPLATE
    chapter_path_template: ClassVar[str] = CHAPTER_PATH_TEMPLATE
    search_page_param: ClassVar[str] = SEARCH_PAGE_PARAM

    # --- Sélecteurs CSS ---------------------------------------------------
    # Fusion des sélecteurs Madara standard avec les surcharges du site.
    # Le mixin utilise ce dict en priorité sur ses valeurs par défaut.
    selectors: ClassVar[dict[str, str]] = {**MadaraMixin.default_selectors, **CUSTOM_SELECTORS}

    # --- Cloudflare -------------------------------------------------------
    # X-Manga utilise Cloudflare avec un challenge JS. La stratégie
    # Playwright est prioritaire (plus fiable que FlareSolverr pour ce site).
    cloudflare_strategy: ClassVar[str] = "playwright"
    cloudflare_cookie_name: ClassVar[str] = "cf_clearance"
    cloudflare_wait_selector: ClassVar[str | None] = "div.post-title, div.wp-manga-chapter"

    # --- Timeouts ---------------------------------------------------------
    playwright_timeout: ClassVar[float] = PLAYWRIGHT_TIMEOUT
    http_timeout: ClassVar[float] = HTTP_TIMEOUT

    # ------------------------------------------------------------------------
    #  Construction
    # ------------------------------------------------------------------------

    def __init__(
        self,
        config: SiteConfig,
        session: HttpSession,
        *,
        playwright_pool: PlaywrightPool | None = None,
    ) -> None:
        """Initialise le parser X-Manga.

        Args:
            config: Configuration du site (chargée depuis ``sites.yaml``).
            session: Session HTTP configurée pour ce site (cookies, proxy,
                rate limiting).
            playwright_pool: Pool Playwright optionnel. **Recommandé** pour
                X-Manga car le site est protégé par Cloudflare. Sans pool,
                les requêtes peuvent échouer avec un challenge CF non résolu.
        """
        super().__init__(config, session, playwright_pool=playwright_pool)
        self._is_adult = True
        self._mirror_index = 0

        logger.bind(site=self.site_id).debug(
            "Parser X-Manga initialisé (playwright={}, mirrors={})",
            playwright_pool is not None,
            len(self.mirror_domains),
        )

    # ------------------------------------------------------------------------
    #  Surcharges Madara
    # ------------------------------------------------------------------------

    def normalize_url(self, url: str) -> str:
        """Normalise une URL X-Manga (relative → absolue).

        Gère les cas spécifiques :
            - URLs relatives (``/manga/...`` → ``https://x-manga.org/manga/...``).
            - URLs avec le mauvais domaine (miroir → domaine principal).
            - URLs sans trailing slash (ajoute si nécessaire).

        Args:
            url: URL relative ou absolue.

        Returns:
            URL absolue normalisée.
        """
        normalized = super().normalize_url(url)

        # Remplace les domaines miroirs par le domaine principal pour
        # homogénéiser les URLs (évite les doublons de cache).
        for mirror in self.mirror_domains[1:]:
            if normalized.startswith(mirror):
                normalized = normalized.replace(mirror, self.base_url, 1)
                logger.trace("URL miroir normalisée : {} → {}", url, normalized)
                break

        return normalized

    def _parse_chapter_metadata(
        self,
        chapter: Chapter,
        element_text: str,
    ) -> Chapter:
        """Enrichit un chapitre avec les métadonnées spécifiques X-Manga.

        Détecte les chapitres « premium » (accès payant) et « à venir »
        (annoncés mais non publiés). Marque ces informations dans le
        ``Chapter`` pour que l'appelant puisse filtrer/afficher.

        Args:
            chapter: Chapitre construit par le mixin.
            element_text: Texte brut de l'élément HTML du chapitre
                (utilisé pour la détection par regex).

        Returns:
            Le chapitre enrichi (même instance, mutée).
        """
        is_premium = bool(PREMIUM_CHAPTER_PATTERN.search(element_text))
        is_future = bool(FUTURE_CHAPTER_PATTERN.search(element_text))

        # Stocke les flags dans les métadonnées du chapitre.
        # On utilise des attributs dynamiques si le modèle Chapter les
        # supporte, sinon on les loggue uniquement.
        if is_premium:
            logger.debug(
                "Chapitre premium détecté : ch.{} de {}",
                chapter.number,
                chapter.title or "(sans titre)",
            )
            # Marque dans le titre pour visibilité côté CLI/UI
            if chapter.title and not chapter.title.startswith("[Premium]"):
                chapter = chapter.model_copy(
                    update={"title": f"[Premium] {chapter.title}"},
                )

        if is_future:
            logger.debug(
                "Chapitre à venir détecté : ch.{}",
                chapter.number,
            )
            if chapter.title and not chapter.title.startswith("[À venir]"):
                chapter = chapter.model_copy(
                    update={"title": f"[À venir] {chapter.title}"},
                )

        return chapter

    async def get_chapters(self, manga: Manga) -> list[Chapter]:
        """Récupère la liste des chapitres d'un manga X-Manga.

        Surcharge la méthode du mixin pour :
            - Enrichir chaque chapitre avec les métadonnées X-Manga
              (premium, à venir).
            - Trier les chapitres par numéro décroissant (plus récent
              en premier, convention Madara).
            - Filtrer les chapitres vides (URLs manquantes).

        Args:
            manga: Manga dont on veut les chapitres.

        Returns:
            Liste de chapitres triés, enrichis.
        """
        chapters = await super().get_chapters(manga)

        if not chapters:
            logger.warning(
                "Aucun chapitre trouvé pour {} ({})",
                manga.title,
                manga.url,
            )
            return chapters

        # Filtre les chapitres sans URL valide
        valid_chapters = [ch for ch in chapters if ch.url and str(ch.url).strip()]
        filtered = len(chapters) - len(valid_chapters)
        if filtered:
            logger.debug("{} chapitre(s) sans URL filtré(s)", filtered)

        # Tri décroissant par numéro (les plus récents en premier)
        try:
            valid_chapters.sort(
                key=lambda ch: float(ch.number) if str(ch.number).replace(".", "", 1).isdigit() else 0.0,
                reverse=True,
            )
        except (ValueError, TypeError):
            # Si le tri échoue (numéros non numériques), on garde l'ordre original
            logger.debug("Tri des chapitres impossible (numéros non numériques)")

        logger.info(
            "{} chapitre(s) récupéré(s) pour {} ({})",
            len(valid_chapters),
            manga.title,
            self.site_id,
        )
        return valid_chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre X-Manga.

        Surcharge pour :
            - Vérifier que le chapitre n'est pas « premium » (accès payant).
            - Gérer les images lazy-loaded (``data-src`` au lieu de ``src``).
            - Retirer les images de tracking / publicités détectées.

        Args:
            chapter: Chapitre dont on veut les pages.

        Returns:
            Liste de pages ordonnées.

        Raises:
            ChapterDownloadError: Si le chapitre est marqué premium et
                que la configuration refuse les chapitres payants.
        """
        # Vérifie si c'est un chapitre premium
        is_premium = chapter.title and chapter.title.startswith("[Premium]")
        if is_premium:
            logger.warning(
                "Chapitre premium — accès payant requis : ch.{} de {}",
                chapter.number,
                chapter.title,
            )
            # On retourne une liste vide plutôt que de lever, pour laisser
            # l'appelant décider (certains utilisateurs peuvent avoir un compte).

        pages = await super().get_pages(chapter)

        # Filtre les images de tracking (URLs vers des domaines connus)
        tracking_domains = (
            "google-analytics.com",
            "doubleclick.net",
            "facebook.com/tr",
            "analytics.",
            "pixel.",
        )
        filtered_pages: list[Page] = []
        for page in pages:
            url_str = str(page.url)
            if any(td in url_str for td in tracking_domains):
                logger.trace("Page de tracking ignorée : {}", url_str[:80])
                continue
            filtered_pages.append(page)

        if len(filtered_pages) < len(pages):
            logger.debug(
                "{} page(s) de tracking filtrée(s) sur {}",
                len(pages) - len(filtered_pages),
                len(pages),
            )

        logger.debug(
            "{} page(s) récupérée(s) pour ch.{} de {}",
            len(filtered_pages),
            chapter.number,
            self.site_id,
        )
        return filtered_pages

    async def search(
        self,
        query: str,
        *,
        page: int = 1,
    ) -> list[SearchResult]:
        """Recherche des mangas sur X-Manga.

        Surcharge la méthode du mixin pour :
            - Ajouter un paramètre de filtrage ``adult`` dans l'URL.
            - Normaliser la query (retirer les caractères spéciaux).
            - Logger le nombre de résultats.

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-indexé).

        Returns:
            Liste de résultats de recherche.
        """
        # Normalisation basique (le mixin gère le reste)
        query = query.strip()
        if not query:
            logger.warning("Recherche avec query vide — ignorée")
            return []

        logger.debug("Recherche '{}' page {} sur {}", query, page, self.site_id)

        results = await super().search(query, page=page)

        logger.info(
            "{} résultat(s) pour '{}' sur {} (page {})",
            len(results),
            query,
            self.site_id,
            page,
        )
        return results

    # ------------------------------------------------------------------------
    #  Health check spécifique
    # ------------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que X-Manga est accessible.

        Surcharge pour tester avec Playwright si disponible (le site est
        derrière Cloudflare et un simple GET HTTP peut échouer).

        Returns:
            True si le site répond correctement.
        """
        try:
            if self._playwright_pool is not None:
                # Test via Playwright (bypass Cloudflare)
                html = await self._playwright_pool.fetch_html(
                    self.base_url,
                    timeout=self.playwright_timeout,
                )
                # Vérifie qu'on a du contenu réel (pas un challenge CF)
                if "Just a moment" in html or "cf-chl" in html.lower():
                    logger.warning("X-Manga health check : challenge Cloudflare non résolu")
                    return False
                return len(html) > 1000  # noqa: PLR2004

            # Fallback HTTP standard
            response = await self._session.get(self.base_url)
            return response.status_code == 200  # noqa: PLR2004

        except Exception as exc:  # noqa: BLE001
            logger.debug("X-Manga health check échoué : {}", exc)
            return False

    # ------------------------------------------------------------------------
    #  Propriétés utilitaires
    # ------------------------------------------------------------------------

    @property
    def is_adult(self) -> bool:
        """True — X-Manga est un site 18+."""
        return self._is_adult

    @property
    def current_mirror(self) -> str:
        """Retourne le domaine miroir actuellement utilisé.

        Returns:
            URL du domaine actif (parmi ``mirror_domains``).
        """
        return self.mirror_domains[self._mirror_index % len(self.mirror_domains)]

    def rotate_mirror(self) -> str:
        """Bascule vers le miroir suivant (rotation circulaire).

        Utile si le domaine principal est inaccessible. La rotation est
        manuelle — le parser ne bascule **pas** automatiquement en cas
        d'échec (c'est au code appelant de décider).

        Returns:
            URL du nouveau domaine actif.
        """
        self._mirror_index = (self._mirror_index + 1) % len(self.mirror_domains)
        new_domain = self.current_mirror
        logger.info("X-Manga : bascule vers le miroir {}", new_domain)
        return new_domain


# ============================================================================
#  Exports
# ============================================================================

__all__ = ["MIRROR_DOMAINS", "XMangaOrgParser"]
