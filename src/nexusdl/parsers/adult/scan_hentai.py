"""Parser pour Scan-Hentai (https://scan-hentai.net).

Site adulte français de scantrad hentai / mangas pornographiques traduits
en français. Basé sur le thème WordPress **Madara** (MangaBooth), avec
les spécificités suivantes :

    - Protection Cloudflare avec Turnstile (validation assistée nécessaire).
    - Domaine **instable** : scan.hentai.menu → scan-hentai.fr → scan-hentai.net
      Le domaine `.fr` a été suspendu (voir issue keiyoushi #5227). Le `.net`
      est le domaine actif au moment de la rédaction.
    - Contenu 18+ : ``adult = True``, activé uniquement si
      ``registry.include_adult: true`` dans la configuration.
    - Images parfois servies via des blobs JavaScript (nécessite Playwright
      pour l'extraction des pages).

La majorité des méthodes (``search``, ``get_manga``, ``get_chapters``,
``get_pages``) sont fournies par :class:`MadaraMixin` avec les sélecteurs
CSS standard du thème Madara.

Example:
    Utilisation via le loader de parsers::

        from nexusdl.core.registry.site_registry import SiteRegistry

        registry = SiteRegistry.from_settings(settings)
        registry.load()
        parser = registry.get_parser("scan_hentai")

        results = await parser.search("one piece")

Note:
    Ce parser est marqué ``adult = True``. Il ne sera **pas** chargé si
    ``registry.include_adult`` est à ``False`` (défaut). Le charger
    explicitement avec ``--include-adult`` en CLI.

Warning:
    Le domaine de ce site change fréquemment. Si le parser échoue sur
    ``health_check()``, vérifier manuellement le domaine actuel et
    mettre à jour ``MIRROR_DOMAINS`` en conséquence.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar, Final

from loguru import logger

from nexusdl.parsers.adult.x_manga_org import (
    CUSTOM_SELECTORS,
    FUTURE_CHAPTER_PATTERN,
    HTTP_TIMEOUT,
    MANGA_PATH_TEMPLATE,
    PLAYWRIGHT_TIMEOUT,
    PREMIUM_CHAPTER_PATTERN,
    SEARCH_PAGE_PARAM,
    SEARCH_PATH,
    XMangaOrgParser,
)

if TYPE_CHECKING:
    from nexusdl.core.models.manga import Chapter, Manga, Page
    from nexusdl.core.models.site import SiteConfig
    from nexusdl.core.session.http_session import HttpSession
    from nexusdl.core.session.playwright_pool import PlaywrightPool
    from nexusdl.parsers.base import SearchResult


# ============================================================================
#  Constantes spécifiques au site
# ============================================================================

#: Domaines miroirs connus de Scan-Hentai (par ordre de préférence).
#:
#: Historique documenté (d'après les issues keiyoushi et les changelogs) :
#:   - ``scan.hentai.menu`` (ancien, redirige vers x-manga.net depuis 2024)
#:   - ``scan-hentai.fr`` (suspendu en 2025, voir issue #5227)
#:   - ``scan-hentai.net`` (actuel au moment de la rédaction)
#:
#: Note : ``scan.hentai.menu`` a été redirigé vers ``x-manga.net`` en
#: avril 2024[reference:3]. ``scan-hentai.fr`` a été suspendu en septembre
#: 2025[reference:4]. Le ``.net`` est le domaine de repli actuel.
MIRROR_DOMAINS: Final[tuple[str, ...]] = (
    "https://scan-hentai.net",
    "https://scan-hentai.fr",
)

#: Domaine canonique de ce parser.
BASE_URL: Final[str] = "https://scan-hentai.net"

#: Sélecteurs CSS spécifiques à Scan-Hentai qui diffèrent du Madara standard.
#: Le thème Madara autorise des personnalisations CSS par site ; Scan-Hentai
#: utilise quelques sélecteurs différents des valeurs par défaut.
#:
#: Note : le site utilise également un lecteur avec des images `blob:`.
#: Le sélecteur ``page_image`` peut nécessiter Playwright pour fonctionner
#: correctement (voir ``cloudflare_strategy``).
CUSTOM_SELECTORS: Final[dict[str, str]] = {
    # Page de recherche
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

#: Regex pour détecter les chapitres marqués « premium » sur Scan-Hentai.
#: Même pattern que X-Manga (site probablement géré par la même équipe).
SCAN_HENTAI_PREMIUM_PATTERN: Final[re.Pattern[str]] = PREMIUM_CHAPTER_PATTERN

#: Regex pour détecter les chapitres « à venir ».
SCAN_HENTAI_FUTURE_PATTERN: Final[re.Pattern[str]] = FUTURE_CHAPTER_PATTERN

#: Timeout pour les opérations Playwright. Scan-Hentai peut être lent
#: lors du premier challenge Cloudflare (Turnstile).
SCAN_HENTAI_PLAYWRIGHT_TIMEOUT: Final[float] = 60.0

#: Timeout pour les opérations HTTP standard.
SCAN_HENTAI_HTTP_TIMEOUT: Final[float] = 30.0

#: Marqueur de migration — utilisé pour détecter les redirections.
#: Le site redirige souvent ``.fr`` → ``.net`` ou ``.menu`` → ``.net``.
REDIRECT_MARKERS: Final[tuple[str, ...]] = (
    "scan-hentai.fr",
    "scan.hentai.menu",
    "x-manga.net",
)


# ============================================================================
#  Parser
# ============================================================================


class ScanHentaiParser(XMangaOrgParser):
    """Parser pour Scan-Hentai (https://scan-hentai.net).

    Sous-classe de :class:`XMangaOrgParser` qui hérite de **toute** la
    logique Madara (recherche, parsing, extraction des pages, détection
    premium, filtrage trackers).

    Les surcharges concernent :
        - L'identité du parser (``site_id``, ``base_url``).
        - Le **timeout Playwright allongé** (60s) car le site utilise
          Cloudflare Turnstile, plus lent à résoudre que le challenge
          standard.
        - La **détection de redirection** vers d'autres domaines
          (``scan-hentai.fr``, ``x-manga.net``) pour informer l'utilisateur.
        - Un **health check enrichi** qui distingue les échecs temporaires
          (Cloudflare) des échecs permanents (domaine décommissionné).

    Attributes:
        site_id: Identifiant unique (``"scan_hentai"``).
        language: Langue du contenu (``"fr"`` — héritage).
        adult: Contenu 18+ (``True`` — héritage).
        base_url: URL de base (``https://scan-hentai.net``).
        mirror_domains: Domaines miroirs, ``.net`` en premier.
        selectors: Sélecteurs CSS hérités + spécifiques.
        cloudflare_strategy: Stratégie Cloudflare (``"playwright"``).
    """

    # ------------------------------------------------------------------------
    #  Métadonnées de classe
    # ------------------------------------------------------------------------

    #: Identifiant unique du parser.
    site_id: ClassVar[str] = "scan_hentai"

    #: Domaine canonique de ce parser.
    base_url: ClassVar[str] = BASE_URL

    #: Domaines miroirs, ordre de préférence.
    mirror_domains: ClassVar[tuple[str, ...]] = MIRROR_DOMAINS

    # --- Options Madara (héritées, surchargées si nécessaire) -----------
    search_path: ClassVar[str] = SEARCH_PATH
    manga_path_template: ClassVar[str] = MANGA_PATH_TEMPLATE
    chapter_path_template: ClassVar[str] = "/manga/{slug}/{chapter_slug}/"
    search_page_param: ClassVar[str] = SEARCH_PAGE_PARAM

    # --- Sélecteurs CSS ---------------------------------------------------
    selectors: ClassVar[dict[str, str]] = {**XMangaOrgParser.selectors, **CUSTOM_SELECTORS}

    # --- Cloudflare -------------------------------------------------------
    # Scan-Hentai utilise Cloudflare Turnstile, plus lent que le challenge
    # standard. La stratégie Playwright est prioritaire.
    cloudflare_strategy: ClassVar[str] = "playwright"
    cloudflare_cookie_name: ClassVar[str] = "cf_clearance"
    cloudflare_wait_selector: ClassVar[str | None] = "div.post-title, div.wp-manga-chapter"

    # --- Timeouts (allongés pour Turnstile) -------------------------------
    playwright_timeout: ClassVar[float] = SCAN_HENTAI_PLAYWRIGHT_TIMEOUT
    http_timeout: ClassVar[float] = SCAN_HENTAI_HTTP_TIMEOUT

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
        """Initialise le parser Scan-Hentai.

        Args:
            config: Configuration du site (chargée depuis ``sites.yaml``).
            session: Session HTTP configurée pour ce site.
            playwright_pool: Pool Playwright optionnel. **Fortement recommandé**
                pour Scan-Hentai car le site utilise Cloudflare Turnstile et
                sert parfois les images via des blobs JavaScript.
        """
        super().__init__(config, session, playwright_pool=playwright_pool)

        logger.bind(site=self.site_id).debug(
            "Parser Scan-Hentai initialisé (playwright={}, mirrors={})",
            playwright_pool is not None,
            len(self.mirror_domains),
        )

    # ------------------------------------------------------------------------
    #  Surcharges
    # ------------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que Scan-Hentai est accessible.

        Surcharge enrichie qui distingue :
            - **Domaine actif** : retourne True si le site répond.
            - **Domaine redirigé** : retourne False + warning (le site a
              migré vers un autre domaine, il faut mettre à jour le parser).
            - **Domaine suspendu** : retourne False + warning explicite
              (le site a été suspendu, cf. issue keiyoushi #5227).

        Returns:
            True si le domaine principal répond correctement.
        """
        # Test du domaine principal via la méthode parente
        is_healthy = await super().health_check()

        if is_healthy:
            return True

        # Test des domaines alternatifs pour détecter les redirections
        for mirror in self.mirror_domains[1:]:
            try:
                response = await self._session.get(mirror, follow_redirects=False)
                if response.status_code in (301, 302, 307, 308):
                    location = response.headers.get("location", "")
                    logger.warning(
                        "Scan-Hentai : le domaine {} redirige vers {}. "
                        "Mettre à jour MIRROR_DOMAINS avec le nouveau domaine.",
                        mirror,
                        location,
                    )
                    return False
                if response.status_code == 200:  # noqa: PLR2004
                    logger.warning(
                        "Scan-Hentai : le domaine principal {} est down, "
                        "mais le miroir {} répond. Rotation recommandée.",
                        self.base_url,
                        mirror,
                    )
                    return False
            except Exception:  # noqa: BLE001
                continue

        logger.error(
            "Scan-Hentai : aucun domaine accessible. Le site est peut-être "
            "suspendu ou a migré vers un nouveau domaine non référencé. "
            "Vérifier manuellement sur les trackers communautaires (keiyoushi).",
        )
        return False

    def normalize_url(self, url: str) -> str:
        """Normalise une URL Scan-Hentai (relative → absolue).

        Gère les redirections connues :
            - ``scan-hentai.fr`` → ``scan-hentai.net``
            - ``scan.hentai.menu`` → ``scan-hentai.net``
            - ``x-manga.net`` → ``scan-hentai.net`` (si le site a migré)

        Args:
            url: URL relative ou absolue.

        Returns:
            URL absolue normalisée.
        """
        # Remplace les domaines historiques par le domaine canonique
        for historical in REDIRECT_MARKERS:
            if historical in url:
                url = url.replace(historical, self.base_url.replace("https://", ""))
                logger.trace("URL historique réécrite : {} → {}", historical, self.base_url)
                break

        return super().normalize_url(url)

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre Scan-Hentai.

        Surcharge pour gérer les images servies via des blobs JavaScript.
        Le site utilise parfois un lecteur qui injecte les images en
        ``blob:`` URLs — une extraction HTML statique ne suffit pas.
        Playwright est requis pour ces cas.

        Args:
            chapter: Chapitre dont on veut les pages.

        Returns:
            Liste de pages ordonnées.
        """
        pages = await super().get_pages(chapter)

        # Vérifie si les pages sont valides (URLs blob: non exploitables)
        blob_pages = [p for p in pages if str(p.url).startswith("blob:")]
        if blob_pages:
            logger.warning(
                "Scan-Hentai : {} page(s) avec URL blob: détectée(s). "
                "Le lecteur nécessite une extraction Playwright. "
                "Utiliser `playwright_pool` pour résoudre ce cas.",
                len(blob_pages),
            )
            # Filtre les pages blob: (non téléchargeables)
            pages = [p for p in pages if not str(p.url).startswith("blob:")]

        logger.debug(
            "{} page(s) récupérée(s) pour ch.{} de {} ({} blob: filtrée(s))",
            len(pages),
            chapter.number,
            self.site_id,
            len(blob_pages),
        )
        return pages

    async def search(
        self,
        query: str,
        *,
        page: int = 1,
    ) -> list[SearchResult]:
        """Recherche des mangas sur Scan-Hentai.

        Surcharge pour logger le domaine actif et le nombre de résultats.
        Le comportement est identique au parent.

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-indexé).

        Returns:
            Liste de résultats de recherche.
        """
        query = query.strip()
        if not query:
            logger.warning("Recherche avec query vide — ignorée")
            return []

        logger.debug(
            "Recherche '{}' page {} sur {} (domaine: {})",
            query,
            page,
            self.site_id,
            self.current_mirror,
        )

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
    #  Méthodes héritées — documentation explicite
    # ------------------------------------------------------------------------
    #
    # Les méthodes suivantes sont **entièrement héritées** de XMangaOrgParser
    # et ne sont pas surchargées :
    #
    #   - ``get_manga(url_or_id)``          → hérité
    #   - ``get_chapters(manga)``           → hérité, inclut détection premium
    #   - ``_parse_chapter_metadata(...)``  → hérité, mêmes regex
    #   - ``rotate_mirror()``               → hérité
    #   - ``current_mirror``                → hérité
    #   - ``is_adult``                      → hérité (True)


# ============================================================================
#  Exports
# ============================================================================

__all__ = ["BASE_URL", "MIRROR_DOMAINS", "ScanHentaiParser"]
