"""Parser pour X-Manga sur le domaine historique (https://x-manga.net).

X-Manga est un site adulte français de scans hentai traduits, basé sur le
thème WordPress Madara. Il est historiquement accessible via deux domaines :

    - ``x-manga.net`` (ancien, actuel miroir)
    - ``x-manga.org`` (actuel, domaine principal)

Ce parser cible le **premier** (`.net`) mais partage l'intégralité de sa
logique avec :class:`~nexusdl.parsers.adult.x_manga_org.XMangaOrgParser` :

    - Même thème WordPress (Madara).
    - Mêmes sélecteurs CSS.
    - Même protection Cloudflare (stratégie Playwright).
    - Même détection de chapitres « premium » et « à venir ».
    - Même filtrage des trackers d'images.

Seuls diffèrent :
    - ``site_id`` (``"x_manga_net"`` vs ``"x_manga_org"``).
    - ``base_url`` (``https://x-manga.net`` vs ``https://x-manga.org``).
    - L'ordre de préférence des miroirs.

L'héritage évite la duplication de ~400 lignes de logique. Si le domaine
`.net` divergeait significativement à l'avenir (changement de thème,
protection renforcée), les surcharges nécessaires peuvent être ajoutées
ici sans impacter le parser `.org`.

Example:
    Utilisation via le loader de parsers::

        from nexusdl.core.registry.site_registry import SiteRegistry

        registry = SiteRegistry.from_settings(settings)
        registry.load()
        parser = registry.get_parser("x_manga_net")

        results = await parser.search("one piece")

Note:
    Ce parser est marqué ``adult = True`` (héritage). Il n'est chargé que
    si ``registry.include_adult: true`` est explicitement activé dans la
    configuration. Le domaine ``.net`` redirige fréquemment vers ``.org``
    au niveau HTTP — les URLs canoniques peuvent être réécrites par la
    couche session si nécessaire.
"""

from __future__ import annotations

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
    from nexusdl.core.models.site import SiteConfig
    from nexusdl.core.session.http_session import HttpSession
    from nexusdl.core.session.playwright_pool import PlaywrightPool


# ============================================================================
#  Constantes spécifiques au domaine .net
# ============================================================================

#: Domaines miroirs de X-Manga, ordre de préférence pour ce parser :
#: le ``.net`` est ici le domaine **primaire**, le ``.org`` est le fallback.
#:
#: Note : l'ordre est **inversé** par rapport à ``XMangaOrgParser``. Le
#: comportement de réécriture d'URL (``normalize_url``) hérité du parent
#: utilise ``mirror_domains[0]`` comme domaine canonique — d'où l'importance
#: de cet ordre.
MIRROR_DOMAINS: Final[tuple[str, ...]] = (
    "https://x-manga.net",
    "https://x-manga.org",
)

#: Domaine canonique de ce parser.
BASE_URL: Final[str] = "https://x-manga.net"


# ============================================================================
#  Parser
# ============================================================================


class XMangaNetParser(XMangaOrgParser):
    """Parser pour X-Manga sur le domaine historique ``x-manga.net``.

    Sous-classe de :class:`XMangaOrgParser` qui hérite de **toute** la
    logique métier (recherche, parsing, extraction des pages) et ne
    surcharge que les métadonnées de site.

    Le comportement est **strictement identique** au parser `.org` à
    l'exception des URLs manipulées. L'utilisateur peut utiliser l'un
    ou l'autre indifféremment — le contenu servi est le même.

    Attributes:
        site_id: Identifiant unique (``"x_manga_net"``).
        language: Langue du contenu (``"fr"`` — héritage).
        adult: Contenu 18+ (``True`` — héritage).
        base_url: URL de base pour ce parser (``https://x-manga.net``).
        mirror_domains: Domaines miroirs, ``.net`` en premier.
        selectors: Sélecteurs CSS hérités (``CUSTOM_SELECTORS`` fusionnés).
    """

    # ------------------------------------------------------------------------
    #  Métadonnées de classe — seules différences avec le parent
    # ------------------------------------------------------------------------

    #: Identifiant unique du parser. Distinct de ``x_manga_org`` pour que
    #: le registre puisse indexer les deux entrées séparément.
    site_id: ClassVar[str] = "x_manga_net"

    #: Domaine canonique de ce parser. Toutes les URLs manipulées par
    #: ce parser utilisent ce domaine comme référence.
    base_url: ClassVar[str] = BASE_URL

    #: Domaines miroirs, ordre de préférence : ``.net`` en premier,
    #: ``.org`` en fallback. Hérité par ``normalize_url`` du parent.
    mirror_domains: ClassVar[tuple[str, ...]] = MIRROR_DOMAINS

    # --- Toutes les autres ClassVars sont héritées telles quelles --------
    # language, adult, search_path, manga_path_template, chapter_path_template,
    # search_page_param, selectors, cloudflare_strategy, cloudflare_cookie_name,
    # cloudflare_wait_selector, playwright_timeout, http_timeout.

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
        """Initialise le parser X-Manga (domaine ``.net``).

        Args:
            config: Configuration du site (chargée depuis ``sites.yaml``).
            session: Session HTTP configurée pour ce site.
            playwright_pool: Pool Playwright optionnel — **recommandé**
                car X-Manga (comme son domaine ``.org``) est protégé par
                Cloudflare.
        """
        super().__init__(config, session, playwright_pool=playwright_pool)

        logger.bind(site=self.site_id).debug(
            "Parser X-Manga (.net) initialisé — domaine canonique: {}",
            self.base_url,
        )

    # ------------------------------------------------------------------------
    #  Surcharges mineures (comportement identique, logs distincts)
    # ------------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que le domaine ``.net`` est accessible.

        Surcharge pour ajouter une vérification spécifique : si le domaine
        ``.net`` redirige systématiquement vers ``.org``, ce health check
        peut retourner ``True`` malgré la redirection (le contenu est
        servi, ce qui est l'essentiel). Si le domaine est **cassé**
        (DNS, décommissionné), le check échoue.

        Returns:
            True si le domaine répond correctement (avec ou sans redirection
            vers le domaine principal).
        """
        # Appel au health check hérité — il teste via Playwright ou HTTP
        # selon la disponibilité du pool. Le comportement est correct pour
        # les deux domaines.
        is_healthy = await super().health_check()

        if not is_healthy:
            logger.warning(
                "X-Manga (.net) health check échoué — le domaine pourrait "
                "être décommissionné. Bascule recommandée vers {}.",
                self.mirror_domains[1],  # le .org
            )

        return is_healthy

    # ------------------------------------------------------------------------
    #  Méthodes héritées — documentation explicite
    # ------------------------------------------------------------------------
    #
    # Les méthodes suivantes sont **entièrement héritées** du parent et ne
    # sont pas surchargées. Elles sont listées ici pour documenter leur
    # provenance et faciliter la maintenance :
    #
    #   - ``search(query, page)``          → hérité, même logique
    #   - ``get_manga(url_or_id)``         → hérité, même logique
    #   - ``get_chapters(manga)``          → hérité, inclut détection premium
    #   - ``get_pages(chapter)``           → hérité, inclut filtrage trackers
    #   - ``normalize_url(url)``           → hérité, réécrit .org → .net
    #                                         (grâce à l'ordre inversé de
    #                                         mirror_domains)
    #   - ``_parse_chapter_metadata(...)`` → hérité, mêmes regex
    #   - ``rotate_mirror()``              → hérité, cycle .net → .org → .net
    #   - ``current_mirror``               → hérité
    #   - ``is_adult``                     → hérité (True)


# ============================================================================
#  Exports
# ============================================================================

__all__ = ["BASE_URL", "MIRROR_DOMAINS", "XMangaNetParser"]
