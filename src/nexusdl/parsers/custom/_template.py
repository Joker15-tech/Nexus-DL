"""Template de parser custom NexusDL — copier, adapter, déployer.

Ce fichier sert de point de départ pour créer un parser pour un site
non supporté officiellement. Il est **complètement fonctionnel** : une
fois les méthodes marquées ``TODO`` implémentées, le parser fonctionne
avec le reste de NexusDL (DownloadManager, bibliothèque, CLI, web).

Workflow de création
====================

    1. Copier ce fichier dans un dossier utilisateur :

        mkdir -p ~/.config/nexusdl/parsers_custom/
        cp $(python -c "import nexusdl.parsers.custom as m; print(m.__path__[0])")/_template.py \\
           ~/.config/nexusdl/parsers_custom/mon_site.py

    2. Éditer ``mon_site.py`` :

        - Renommer la classe ``TemplateParser`` en ``MonSiteParser``.
        - Remplacer les ``ClassVar`` (``site_id``, ``language``, ``base_url``,
          ``adult``).
        - Implémenter les 4 méthodes abstraites : ``search``, ``get_manga``,
          ``get_chapters``, ``get_pages``.

    3. Enregistrer le site dans ``~/.config/nexusdl/sites_overrides.yaml`` :

        new_sites:
          mon_site:
            parser_class: "parsers_custom.mon_site:MonSiteParser"
            domains: ["https://mon-site.example"]
            language: fr
            adult: false

    4. Ajouter le dossier au PYTHONPATH :

        export PYTHONPATH="$HOME/.config/nexusdl:$PYTHONPATH"

    5. Valider et tester :

        python -m nexusdl sites validate mon_site
        python -m nexusdl search "test" --site mon_site

Architecture du parser
======================

Un parser NexusDL est une classe héritant de ``BaseParser`` qui expose
4 méthodes **obligatoires** et 3 méthodes **optionnelles** (surchargeables
pour personnaliser le comportement par défaut) :

**Obligatoires** :

    - ``search(query, page)``     : rechercher des mangas.
    - ``get_manga(url_or_id)``    : métadonnées complètes d'un manga.
    - ``get_chapters(manga)``     : liste des chapitres.
    - ``get_pages(chapter)``      : URLs des pages d'un chapitre.

**Optionnelles** :

    - ``download_page(page, dest)`` : télécharger une page (par défaut :
      via la ``HttpSession`` du parser, avec retry).
    - ``health_check()``            : vérifier que le site est accessible.
    - ``normalize_url(url)``        : convertir une URL relative en absolue.

**Contexte async** : le parser supporte ``async with`` pour libérer
proprement ses ressources (le ``BaseParser`` gère déjà la session HTTP).

Example:
    Utilisation une fois le template complété::

        from pathlib import Path
        from nexusdl.core.registry.site_registry import SiteRegistry

        registry = SiteRegistry.from_settings(settings)
        registry.load()
        parser = registry.get_parser("mon_site")

        # Recherche
        results = await parser.search("one piece")
        for r in results:
            print(r.title, r.url)

        # Détails
        manga = await parser.get_manga(results[0].url)
        chapters = await parser.get_chapters(manga)
        pages = await parser.get_pages(chapters[0])

Warning:
    Un parser custom a un **accès complet** au core de NexusDL. Il peut
    lire/écrire des fichiers, faire des requêtes réseau arbitraires, et
    accéder à toutes les ressources. Installez uniquement des parsers
    provenant de sources de confiance.

See Also:
    - ``nexusdl.parsers.base`` : ABC commune à tous les parsers.
    - ``nexusdl.parsers.custom.nexus.dl`` : documentation du dossier.
    - ``docs/source/development/adding_parsers.md`` : guide complet.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import re
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final
from urllib.parse import urljoin, urlparse, urlunparse

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
from nexusdl.parsers.base import BaseParser, SearchResult

if TYPE_CHECKING:
    from nexusdl.core.models.site import SiteConfig
    from nexusdl.core.session.http_session import HttpSession
    from nexusdl.core.session.playwright_pool import PlaywrightPool


# ============================================================================
#  CONSTANTES DU SITE — À PERSONNALISER
# ============================================================================
# Ces constantes sont utilisées dans tout le parser. Les regrouper en tête
# de fichier facilite la maintenance (une seule zone à modifier).

#: Domaine canonique du site (avec schéma, sans trailing slash).
#: TODO: remplacer par le vrai domaine.
BASE_URL: Final[str] = "https://mon-site.example"

#: Domaines miroirs, par ordre de préférence (le premier est le canonique).
#: Laisser vide si le site n'a qu'un seul domaine.
#: TODO: ajouter les miroirs si applicable.
MIRROR_DOMAINS: Final[tuple[str, ...]] = (
    BASE_URL,
    # "https://mon-site-mirror.example",
)

#: Marqueurs de domaines historiques à réécrire automatiquement.
#: Utile si le site a migré plusieurs fois de domaine.
REDIRECT_MARKERS: Final[tuple[str, ...]] = (
    # "ancien-domaine.com",
    # "autre-ancien.net",
)

#: Chemins URL (à adapter selon la structure du site).
SEARCH_PATH: Final[str] = "/search"
MANGA_PATH_TEMPLATE: Final[str] = "/manga/{slug}/"
CHAPTER_PATH_TEMPLATE: Final[str] = "/manga/{slug}/{chapter_slug}/"

#: Paramètre de pagination (nom du query param).
SEARCH_PAGE_PARAM: Final[str] = "page"

#: Paramètre de recherche (nom du query param).
SEARCH_QUERY_PARAM: Final[str] = "q"

#: Sélecteurs CSS pour le parsing HTML (via selectolax).
#: Utiliser des clés sémantiques (search_item, manga_title, etc.).
#: Plusieurs sélecteurs séparés par virgule = fallbacks.
SELECTORS: Final[dict[str, str]] = {
    # Recherche
    "search_item": "div.search-result, li.result-item",
    "search_title": "h2.title a, h3 a",
    "search_cover": "img.cover, img.thumbnail",
    "search_link": "h2.title a, h3 a",
    # Manga
    "manga_title": "h1.title, h1.manga-title",
    "manga_description": "div.description, div.summary",
    "manga_cover": "div.cover img, img.manga-cover",
    "manga_author": "span.author a, div.author a",
    "manga_artist": "span.artist a",
    "manga_genres": "div.genres a, span.tag a",
    "manga_status": "div.status, span.status",
    "manga_year": "div.year, span.year",
    # Chapitres
    "chapter_item": "ul.chapters li a, div.chapter-list a",
    # Pages
    "page_image": "div.reader img, div#pages img",
}

#: Timeouts.
HTTP_TIMEOUT: Final[float] = 30.0
PLAYWRIGHT_TIMEOUT: Final[float] = 45.0
PAGE_DOWNLOAD_TIMEOUT: Final[float] = 45.0

#: Validation d'images.
MIN_IMAGE_BYTES: Final[int] = 1024                # 1 KiB
MAX_IMAGE_BYTES: Final[int] = 32 * 1024 * 1024    # 32 MiB

#: Extensions acceptées pour les pages.
ALLOWED_IMAGE_EXTS: Final[frozenset[str]] = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"},
)

#: MIME types acceptés.
ALLOWED_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/avif",
    },
)

#: Marqueurs de challenge Cloudflare (détection de protection).
CLOUDFLARE_MARKERS: Final[tuple[str, ...]] = (
    "Just a moment",
    "cf-chl-",
    "Checking your browser",
    "cf_chl_",
    "__cf_bm",
    "turnstile",
    "challenge-platform",
)

#: Domaines de tracking à exclure des pages.
TRACKING_DOMAINS: Final[tuple[str, ...]] = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "facebook.com/tr",
    "analytics.",
    "pixel.",
)


# ============================================================================
#  REGEX PATTERNS — À PERSONNALISER
# ============================================================================

#: Extraction du slug manga depuis une URL.
MANGA_SLUG_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"/manga/([^/]+)/?",
)

#: Extraction d'un numéro de chapitre depuis un texte.
CHAPTER_NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:chapitre|chap|ch|chapter|ch\.)\s*([0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE,
)

#: Détection des blobs JS (non téléchargeables).
BLOB_URL_PATTERN: Final[re.Pattern[str]] = re.compile(r"^blob:")


# ============================================================================
#  HELPERS INTERNES — RÉUTILISABLES
# ============================================================================
# Ces fonctions sont génériques et peuvent être conservées telles quelles
# dans la plupart des parsers. Elles gèrent les cas standards.


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
    """Détecte une URL ``blob:`` (image JS non téléchargeable).

    Args:
        url: URL à tester.

    Returns:
        True si l'URL est un blob.
    """
    return bool(BLOB_URL_PATTERN.match(url))


def _is_tracking_url(url: str) -> bool:
    """Détecte une URL de tracking / publicité.

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
    """Normalise une URL d'image (relative → absolue, protocole-relatif).

    Args:
        url: URL brute.
        base: URL de base pour la résolution.

    Returns:
        URL absolue.
    """
    url = url.strip()
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith(("http://", "https://")):
        return url
    return urljoin(base, url)


def _parse_chapter_number(text: str) -> float | str:
    """Extrait un numéro de chapitre depuis un texte.

    Gère les cas numériques (« 12 », « 12.5 ») et retourne le texte brut
    si le numéro n'est pas numérique (« Extra », « Omake »).

    Args:
        text: Texte à parser.

    Returns:
        Numéro en float, ou texte original si non numérique.
    """
    match = CHAPTER_NUMBER_PATTERN.search(text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    # Fallback : premier nombre isolé dans le texte
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if numbers:
        with suppress(ValueError):
            return float(numbers[0])
    return _clean_text(text)


def _compute_sha256(data: bytes) -> str:
    """Calcule le hash SHA256 d'un contenu binaire.

    Args:
        data: Données binaires.

    Returns:
        Hash hexadécimal (64 caractères).
    """
    return hashlib.sha256(data).hexdigest()


def _extract_slug(url: str) -> str | None:
    """Extrait le slug d'un manga depuis une URL.

    Args:
        url: URL du manga.

    Returns:
        Slug, ou None si introuvable.
    """
    match = MANGA_SLUG_PATTERN.search(url)
    return match.group(1) if match else None


# ============================================================================
#  PARSER
# ============================================================================


class TemplateParser(BaseParser):
    """Parser pour [NOM DU SITE] — template à personnaliser.

    Ce parser hérite de ``BaseParser`` et implémente les 4 méthodes
    abstraites. Il est **fonctionnel** dès que les ``NotImplementedError``
    sont remplacés par la logique métier.

    Étapes de personnalisation :

        1. Renommer la classe en ``<SiteName>Parser``.
        2. Changer les ``ClassVar`` (``site_id``, ``language``,
           ``base_url``, ``adult``).
        3. Adapter les constantes du module (``SEARCH_PATH``,
           ``MANGA_PATH_TEMPLATE``, ``SELECTORS``).
        4. Implémenter les 4 méthodes ``search``, ``get_manga``,
           ``get_chapters``, ``get_pages``.
        5. Tester avec ``nexusdl sites validate <site_id>``.

    Attributes:
        site_id: Identifiant unique du parser (à personnaliser).
        language: Code langue ISO 639-1 (``"fr"``, ``"en"``, etc.).
        adult: True si le site contient du contenu 18+.
        base_url: URL canonique du site.
        mirror_domains: Domaines miroirs (fallbacks).
        cloudflare_strategy: Stratégie de bypass CF (``"playwright"`` ou
            ``"flaresolverr"``).
    """

    # ------------------------------------------------------------------------
    #  MÉTADONNÉES DE CLASSE — À PERSONNALISER
    # ------------------------------------------------------------------------

    #: TODO: identifiant unique en snake_case (ex: ``"mon_site"``).
    #: Utilisé partout : SiteRegistry, logs, CLI, API REST.
    site_id: ClassVar[str] = "template_site"

    #: TODO: code langue ISO 639-1 (``"fr"``, ``"en"``, ``"ja"``, ``"ko"``...).
    language: ClassVar[str] = "en"

    #: TODO: True si le site contient du contenu 18+.
    #: Un parser ``adult=True`` n'est chargé que si
    #: ``registry.include_adult: true`` (opt-in explicite).
    adult: ClassVar[bool] = False

    #: TODO: URL canonique du site (sans trailing slash).
    base_url: ClassVar[str] = BASE_URL

    #: TODO: domaines miroirs (fallbacks). Le premier doit être ``base_url``.
    mirror_domains: ClassVar[tuple[str, ...]] = MIRROR_DOMAINS

    #: Chemins URL.
    search_path: ClassVar[str] = SEARCH_PATH
    search_page_param: ClassVar[str] = SEARCH_PAGE_PARAM
    manga_path_template: ClassVar[str] = MANGA_PATH_TEMPLATE
    chapter_path_template: ClassVar[str] = CHAPTER_PATH_TEMPLATE

    #: Sélecteurs CSS.
    selectors: ClassVar[dict[str, str]] = SELECTORS

    #: TODO: stratégie de bypass Cloudflare.
    #: - ``"playwright"`` : utilise le PlaywrightPool (recommandé).
    #: - ``"flaresolverr"`` : utilise un service FlareSolverr externe.
    #: - ``"none"`` : pas de protection CF (défaut).
    cloudflare_strategy: ClassVar[str] = "none"

    #: Timeouts (peuvent être ajustés pour des sites lents).
    http_timeout: ClassVar[float] = HTTP_TIMEOUT
    playwright_timeout: ClassVar[float] = PLAYWRIGHT_TIMEOUT
    page_download_timeout: ClassVar[float] = PAGE_DOWNLOAD_TIMEOUT

    #: Classification du contenu (SAFE, SUGGESTIVE, EROTICA, PORNOGRAPHIC).
    content_rating: ClassVar[ContentRating] = ContentRating.SAFE

    # ------------------------------------------------------------------------
    #  CONSTRUCTION
    # ------------------------------------------------------------------------

    def __init__(
        self,
        config: SiteConfig,
        session: HttpSession,
        *,
        playwright_pool: PlaywrightPool | None = None,
    ) -> None:
        """Initialise le parser.

        Args:
            config: Configuration du site (chargée depuis ``sites.yaml``
                ou ``sites_overrides.yaml``).
            session: Session HTTP configurée (cookies, proxy, rate limit,
                retry). Ne pas instancier ``HttpSession`` soi-même — elle
                est fournie par le ``SiteRegistry``.
            playwright_pool: Pool Playwright optionnel. Nécessaire si le
                site utilise Cloudflare ou un rendu JS. Peut être ``None``
                si le parser n'en a pas besoin.
        """
        super().__init__(config, session, playwright_pool=playwright_pool)

        #: Index du miroir actuellement utilisé (pour la rotation).
        self._mirror_index: int = 0

        #: URL canonique active (peut changer via ``rotate_mirror()``).
        self._active_base: str = self.base_url

        #: Flag de fermeture (évite double ``close()``).
        self._closed: bool = False

        #: Cache local (ex: résultats de recherche, métadonnées).
        #: TODO: adapter ou supprimer selon les besoins.
        self._cache: dict[str, Any] = {}

        logger.bind(site=self.site_id).debug(
            "Parser {} initialisé (playwright={})",
            self.site_id,
            playwright_pool is not None,
        )

    # ------------------------------------------------------------------------
    #  CONTEXTE ASYNC — gestion propre des ressources
    # ------------------------------------------------------------------------

    async def __aenter__(self) -> TemplateParser:
        """Entre dans le contexte async.

        Returns:
            Le parser lui-même.
        """
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        """Sort du contexte async et libère les ressources.

        Args:
            *exc_info: Informations sur l'exception éventuelle.
        """
        await self.close()

    async def close(self) -> None:
        """Libère les ressources du parser (idempotent).

        N'appelle PAS ``session.close()`` — la session appartient à
        l'appelant (``SiteRegistry``). Seul le cache interne est vidé.
        """
        if self._closed:
            return
        self._closed = True
        self._cache.clear()
        logger.bind(site=self.site_id).debug("Parser {} fermé", self.site_id)

    # ------------------------------------------------------------------------
    #  MÉTHODE OBLIGATOIRE 1/4 — search
    # ------------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        page: int = 1,
    ) -> list[SearchResult]:
        """Recherche des mangas correspondant à la requête.

        Args:
            query: Terme de recherche (non vide).
            page: Numéro de page (1-indexé, >= 1).

        Returns:
            Liste de résultats de recherche. Peut être vide si aucun
            résultat, mais ne doit pas lever dans ce cas.

        Raises:
            ParserError: Si la query est vide ou la page invalide.
            SiteUnreachableError: Si le site ne répond pas.

        Example:
            ::

                results = await parser.search("one piece")
                for r in results:
                    print(r.title, r.url)
        """
        # --- Validation d'entrée ---
        query = query.strip()
        if not query:
            msg = "query vide après normalisation"
            raise ParserError(msg)
        if page < 1:
            msg = f"page doit être >= 1, reçu {page}"
            raise ParserError(msg)

        logger.bind(site=self.site_id).debug(
            "Recherche '{}' page {} (miroir: {})",
            query,
            page,
            self._active_base,
        )

        # -------------------------------------------------------------------
        # TODO: implémenter la logique de recherche.
        #
        # Approche typique :
        #
        #   1. Construire les paramètres de requête.
        #   2. Appeler ``self._fetch_html()`` ou ``self._fetch_json()``.
        #   3. Parser la réponse avec ``self._parse_search_html()`` ou
        #      ``self._parse_search_json()``.
        #   4. Retourner la liste de ``SearchResult``.
        #
        # Exemple concret (site HTML simple) :
        #
        #   params = {"q": query}
        #   if page > 1:
        #       params["page"] = str(page)
        #
        #   html = await self._fetch_html(
        #       self._active_base + self.search_path,
        #       params=params,
        #   )
        #   results = self._parse_search_html(html)
        #
        # Exemple concret (site avec API JSON) :
        #
        #   data = await self._fetch_json(
        #       self._active_base + "/api/search",
        #       params={"q": query, "page": str(page)},
        #   )
        #   results = self._parse_search_json(data)
        #
        # -------------------------------------------------------------------

        msg = (
            f"search() non implémentée pour {self.site_id} — "
            "éditer _template.py et remplacer ce NotImplementedError"
        )
        raise NotImplementedError(msg)

    # ------------------------------------------------------------------------
    #  MÉTHODE OBLIGATOIRE 2/4 — get_manga
    # ------------------------------------------------------------------------

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère les métadonnées complètes d'un manga.

        Args:
            url_or_id: URL absolue, chemin relatif, ou identifiant du manga.
                L'appelant peut fournir l'un des trois — le parser doit
                normaliser en URL absolue avant de faire la requête.

        Returns:
            Objet ``Manga`` peuplé avec au minimum :
                - ``id`` : identifiant interne (``f"{site_id}:{slug}"``).
                - ``source_id`` : identifiant sur le site source.
                - ``title`` : titre principal.
                - ``url`` : URL absolue du manga.
                - ``chapters`` : liste des chapitres (peut être vide si
                  l'extraction nécessite une seconde requête).

        Raises:
            MangaNotFoundError: Si la page est 404.
            SiteUnreachableError: Si le site ne répond pas.

        Example:
            ::

                manga = await parser.get_manga("one-piece")
                print(manga.title, len(manga.chapters))
        """
        url = self._resolve_manga_url(url_or_id)
        logger.bind(site=self.site_id).debug("Récupération manga : {}", url)

        # -------------------------------------------------------------------
        # TODO: implémenter la logique de récupération.
        #
        # Approche typique :
        #
        #   1. Résoudre l'URL via ``self._resolve_manga_url()`` (fait).
        #   2. Récupérer le HTML ou le JSON.
        #   3. Vérifier si c'est une page 404 (via ``self._is_404_page()``).
        #   4. Parser la réponse et construire un objet ``Manga``.
        #
        # Exemple concret :
        #
        #   html = await self._fetch_html(url)
        #   if self._is_404_page(html):
        #       raise MangaNotFoundError(f"Manga introuvable : {url}")
        #   manga = self._parse_manga_html(html, url)
        #
        # -------------------------------------------------------------------

        msg = (
            f"get_manga() non implémentée pour {self.site_id} — "
            "éditer _template.py et remplacer ce NotImplementedError"
        )
        raise NotImplementedError(msg)

    # ------------------------------------------------------------------------
    #  MÉTHODE OBLIGATOIRE 3/4 — get_chapters
    # ------------------------------------------------------------------------

    async def get_chapters(self, manga: Manga) -> list[Chapter]:
        """Récupère la liste des chapitres d'un manga.

        Args:
            manga: Manga dont on veut les chapitres. Peut déjà contenir
                des chapitres (si extraits par ``get_manga``) — dans ce
                cas, les retourner directement sans nouvelle requête.

        Returns:
            Liste de chapitres triés (plus récent en premier).

        Example:
            ::

                chapters = await parser.get_chapters(manga)
                for ch in chapters:
                    print(ch.number, ch.title)
        """
        # Cas optimisé : les chapitres sont déjà dans le Manga
        if manga.chapters:
            logger.bind(site=self.site_id).debug(
                "{} chapitre(s) déjà présents dans le Manga",
                len(manga.chapters),
            )
            return list(manga.chapters)

        # -------------------------------------------------------------------
        # TODO: implémenter la logique de récupération.
        #
        # Approche typique :
        #
        #   html = await self._fetch_html(str(manga.url))
        #   chapters = self._parse_chapters_html(html)
        #   chapters.sort(
        #       key=lambda ch: float(ch.number)
        #       if str(ch.number).replace(".", "", 1).isdigit()
        #       else 0.0,
        #       reverse=True,
        #   )
        #   return chapters
        #
        # -------------------------------------------------------------------

        msg = (
            f"get_chapters() non implémentée pour {self.site_id} — "
            "éditer _template.py et remplacer ce NotImplementedError"
        )
        raise NotImplementedError(msg)

    # ------------------------------------------------------------------------
    #  MÉTHODE OBLIGATOIRE 4/4 — get_pages
    # ------------------------------------------------------------------------

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre.

        Args:
            chapter: Chapitre dont on veut les pages.

        Returns:
            Liste de ``Page`` ordonnées par index (0-indexé).

        Raises:
            ChapterDownloadError: Si aucune page n'est trouvée.

        Example:
            ::

                pages = await parser.get_pages(chapters[0])
                for p in pages:
                    print(p.index, p.url)
        """
        url = str(chapter.url)
        logger.bind(site=self.site_id).debug(
            "Récupération des pages pour ch.{} : {}",
            chapter.number,
            url,
        )

        # -------------------------------------------------------------------
        # TODO: implémenter la logique de récupération.
        #
        # Approche typique :
        #
        #   html = await self._fetch_html(url)
        #   pages = self._parse_pages_html(html)
        #   if not pages:
        #       raise ChapterDownloadError(
        #           f"Aucune page pour ch.{chapter.number}"
        #       )
        #   return pages
        #
        # Pour les sites avec images lazy-loaded, chercher dans cet ordre :
        #   - ``data-src``
        #   - ``data-lazy-src``
        #   - ``data-original``
        #   - ``src``
        #
        # -------------------------------------------------------------------

        msg = (
            f"get_pages() non implémentée pour {self.site_id} — "
            "éditer _template.py et remplacer ce NotImplementedError"
        )
        raise NotImplementedError(msg)

    # ------------------------------------------------------------------------
    #  MÉTHODE OPTIONNELLE — download_page
    # ------------------------------------------------------------------------

    async def download_page(
        self,
        page: Page,
        dest: Path,
        *,
        overwrite: bool = False,
        max_retries: int = 3,
    ) -> Path:
        """Télécharge une page avec retry, validation et écriture atomique.

        Cette implémentation par défaut couvre la majorité des cas.
        Surcharger seulement si le site a des exigences particulières
        (Referer strict, cookies custom, signature d'URL, etc.).

        Args:
            page: Page à télécharger.
            dest: Dossier ou chemin de destination. Si ``dest`` est un
                dossier (ou n'a pas d'extension), le nom de fichier est
                pris depuis ``page.filename``. Sinon, ``dest`` est utilisé
                comme chemin de fichier complet.
            overwrite: Écraser un fichier existant.
            max_retries: Nombre maximal de tentatives.

        Returns:
            Chemin absolu du fichier téléchargé.

        Raises:
            ChapterDownloadError: Si toutes les tentatives échouent.
        """
        dest_path = self._resolve_dest_path(page, dest)

        # Fichier déjà présent ?
        if dest_path.exists() and not overwrite:
            logger.bind(site=self.site_id).trace(
                "Fichier déjà présent, skip : {}",
                dest_path,
            )
            return dest_path

        dest_path.parent.mkdir(parents=True, exist_ok=True)

        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                return await self._download_page_once(page, dest_path)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.bind(site=self.site_id).warning(
                    "Tentative {}/{} échouée pour {} : {}",
                    attempt,
                    max_retries,
                    page.url,
                    exc,
                )
                if attempt < max_retries:
                    backoff = 0.5 * (2 ** (attempt - 1))
                    await asyncio.sleep(backoff)

        msg = (
            f"Échec téléchargement après {max_retries} tentative(s) : "
            f"{page.url}"
        )
        raise ChapterDownloadError(msg) from last_error

    def _resolve_dest_path(self, page: Page, dest: Path) -> Path:
        """Résout le chemin de destination d'une page.

        Args:
            page: Page cible.
            dest: Dossier ou fichier.

        Returns:
            Chemin complet du fichier cible.
        """
        if dest.is_dir() or not dest.suffix:
            filename = page.filename or f"page_{page.index:04d}.jpg"
            return dest / filename
        return dest

    async def _download_page_once(
        self,
        page: Page,
        dest_path: Path,
    ) -> Path:
        """Télécharge une page en une tentative.

        Args:
            page: Page à télécharger.
            dest_path: Chemin de destination final.

        Returns:
            Chemin du fichier téléchargé.

        Raises:
            ChapterDownloadError: Si la validation échoue.
        """
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
        try:
            # -----------------------------------------------------------------
            # TODO: adapter les headers si le site exige un Referer spécifique.
            # Par défaut : Referer = base active.
            # -----------------------------------------------------------------
            headers = {
                "Referer": self._active_base,
            }

            response = await self._session.get(
                str(page.url),
                headers=headers,
                timeout=self.page_download_timeout,
            )

            if response.status_code != 200:  # noqa: PLR2004
                msg = f"HTTP {response.status_code} pour {page.url}"
                raise ChapterDownloadError(msg)

            content = response.content
            content_type = (
                response.headers.get("content-type", "")
                .lower()
                .split(";")[0]
                .strip()
            )

            # Validation MIME
            if content_type and content_type not in ALLOWED_MIME_TYPES:
                if content_type not in {
                    "application/octet-stream",
                    "binary/octet-stream",
                    "",
                }:
                    msg = f"MIME invalide '{content_type}' pour {page.url}"
                    raise ChapterDownloadError(msg)

            # Validation taille
            if len(content) < MIN_IMAGE_BYTES:
                msg = f"Image trop petite ({len(content)} o) : {page.url}"
                raise ChapterDownloadError(msg)
            if len(content) > MAX_IMAGE_BYTES:
                msg = f"Image trop grande ({len(content)} o) : {page.url}"
                raise ChapterDownloadError(msg)

            # Corrige l'extension selon le MIME réel
            dest_path = self._fix_extension(dest_path, content_type)
            tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")

            # Écriture atomique
            await asyncio.to_thread(tmp_path.write_bytes, content)

            # Vérification du checksum si fourni
            if page.checksum:
                actual = _compute_sha256(content)
                if actual != page.checksum:
                    msg = f"Checksum mismatch pour {page.url}"
                    raise ChapterDownloadError(msg)

            # Renomme atomiquement
            await asyncio.to_thread(tmp_path.replace, dest_path)

            logger.bind(site=self.site_id).trace(
                "Page écrite : {} ({} o)",
                dest_path.name,
                len(content),
            )
            return dest_path

        except Exception:
            # Nettoyage du fichier partiel en cas d'échec
            if tmp_path.exists():
                with suppress(OSError):
                    tmp_path.unlink()
            raise

    @staticmethod
    def _fix_extension(path: Path, content_type: str) -> Path:
        """Corrige l'extension d'un fichier selon le MIME réel.

        Args:
            path: Chemin actuel.
            content_type: MIME type.

        Returns:
            Chemin avec l'extension corrigée si nécessaire.
        """
        if not content_type:
            return path
        guessed = mimetypes.guess_extension(content_type)
        if not guessed:
            return path
        if guessed == ".jpe":
            guessed = ".jpg"
        if path.suffix.lower() == guessed:
            return path
        return path.with_suffix(guessed)

    # ------------------------------------------------------------------------
    #  MÉTHODE OPTIONNELLE — health_check
    # ------------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que le site est accessible.

        Implémentation par défaut : requête HTTP vers ``base_url`` et
        vérification du status 200 + absence de challenge Cloudflare.

        Surcharger si le site nécessite une URL spécifique pour le check
        (ex: ``/api/health``) ou une validation plus fine.

        Returns:
            True si le site répond correctement, False sinon.
        """
        try:
            response = await self._session.get(
                self._active_base,
                timeout=self.http_timeout,
            )
            if response.status_code == 200:  # noqa: PLR2004
                if not _is_cloudflare_challenge(response.text):
                    return True
                logger.bind(site=self.site_id).warning(
                    "Health check : challenge CF sur {}",
                    self._active_base,
                )
                return False
            logger.bind(site=self.site_id).debug(
                "Health check : HTTP {} sur {}",
                response.status_code,
                self._active_base,
            )
            return False
        except Exception as exc:  # noqa: BLE001
            logger.bind(site=self.site_id).debug(
                "Health check échoué : {}",
                exc,
            )
            return False

    # ------------------------------------------------------------------------
    #  MÉTHODE OPTIONNELLE — normalize_url
    # ------------------------------------------------------------------------

    def normalize_url(self, url: str) -> str:
        """Normalise une URL (relative → absolue).

        Implémentation par défaut : réécrit les marqueurs historiques
        vers le domaine canonique, puis résout les URLs relatives.

        Surcharger si le site a des règles particulières (réécriture de
        paths, sous-domaines, etc.).

        Args:
            url: URL relative ou absolue.

        Returns:
            URL absolue normalisée.
        """
        url = url.strip()

        # Réécriture des marqueurs historiques
        for historical in REDIRECT_MARKERS:
            if historical in url:
                url = url.replace(
                    historical,
                    urlparse(self._active_base).netloc,
                )
                logger.bind(site=self.site_id).trace(
                    "URL historique réécrite : {} → {}",
                    historical,
                    self._active_base,
                )
                break

        # URL absolue
        if url.startswith(("http://", "https://")):
            parsed = urlparse(url)
            expected = urlparse(self._active_base)
            known_mirrors = {urlparse(m).netloc for m in self.mirror_domains}
            if parsed.netloc != expected.netloc and parsed.netloc in known_mirrors:
                url = urlunparse(
                    parsed._replace(
                        netloc=expected.netloc,
                        scheme=expected.scheme,
                    ),
                )
            return url

        # Chemin relatif
        if url.startswith("/"):
            return self._active_base + url

        # URL relative sans slash initial
        return urljoin(self._active_base + "/", url)

    def _resolve_manga_url(self, url_or_id: str) -> str:
        """Résout une entrée utilisateur en URL de manga absolue.

        Args:
            url_or_id: URL complète, chemin relatif, ou slug.

        Returns:
            URL absolue du manga.
        """
        url_or_id = url_or_id.strip()

        if url_or_id.startswith(("http://", "https://")):
            return self.normalize_url(url_or_id)
        if url_or_id.startswith("/"):
            return self.normalize_url(url_or_id)

        # Slug brut — construire l'URL via le template
        return self._active_base + self.manga_path_template.format(
            slug=url_or_id,
        )

    # ------------------------------------------------------------------------
    #  HELPERS DE FETCH — HTTP et JSON
    # ------------------------------------------------------------------------

    async def _fetch_html(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        max_attempts: int = 3,
    ) -> str:
        """Récupère le HTML d'une URL avec gestion Cloudflare.

        Si le parser a un ``cloudflare_strategy`` actif et qu'un challenge
        est détecté, bascule sur le ``playwright_pool``.

        Args:
            url: URL à récupérer.
            params: Query parameters.
            max_attempts: Nombre maximal de tentatives.

        Returns:
            HTML de la page.

        Raises:
            SiteUnreachableError: Si toutes les stratégies échouent.
        """
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                response = await self._session.get(
                    url,
                    params=params,
                    timeout=self.http_timeout,
                    headers={"Referer": self._active_base},
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

            # Fallback Playwright (si configuré)
            if (
                self._playwright_pool is not None
                and self.cloudflare_strategy == "playwright"
            ):
                try:
                    full_url = url
                    if params:
                        query = "&".join(f"{k}={v}" for k, v in params.items())
                        full_url = f"{url}?{query}"

                    html = await self._playwright_pool.fetch_html(
                        full_url,
                        timeout=self.playwright_timeout,
                    )
                    if not _is_cloudflare_challenge(html):
                        return html
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.bind(site=self.site_id).debug(
                        "Playwright tentative {}/{} échouée : {}",
                        attempt,
                        max_attempts,
                        exc,
                    )

            if attempt < max_attempts:
                backoff = 0.5 * (2 ** (attempt - 1))
                await asyncio.sleep(backoff)

        msg = f"Impossible de récupérer {url} après {max_attempts} tentative(s)"
        raise SiteUnreachableError(msg) from last_error

    async def _fetch_json(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        max_attempts: int = 3,
    ) -> Any:
        """Récupère et parse une réponse JSON depuis une URL.

        Args:
            url: URL de l'endpoint JSON.
            params: Query parameters.
            max_attempts: Nombre maximal de tentatives.

        Returns:
            Objet Python parsé depuis le JSON.

        Raises:
            ParserError: Si le JSON est invalide ou l'URL renvoie 404.
            SiteUnreachableError: Si l'endpoint est injoignable.
        """
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                response = await self._session.get(
                    url,
                    params=params,
                    timeout=self.http_timeout,
                    headers={
                        "Accept": "application/json",
                        "Referer": self._active_base,
                        "X-Requested-With": "XMLHttpRequest",
                    },
                )

                if response.status_code == 200:  # noqa: PLR2004
                    text = response.text
                    if not _is_cloudflare_challenge(text):
                        try:
                            return json.loads(text)
                        except json.JSONDecodeError as exc:
                            logger.bind(site=self.site_id).debug(
                                "JSON invalide depuis {} : {}",
                                url,
                                exc,
                            )
                elif response.status_code == 404:  # noqa: PLR2004
                    msg = f"404 pour {url}"
                    raise ParserError(msg)

            except ParserError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.bind(site=self.site_id).debug(
                    "Tentative JSON {}/{} échouée : {}",
                    attempt,
                    max_attempts,
                    exc,
                )

            if attempt < max_attempts:
                backoff = 0.5 * (2 ** (attempt - 1))
                await asyncio.sleep(backoff)

        msg = f"Impossible de récupérer le JSON depuis {url}"
        raise SiteUnreachableError(msg) from last_error

    # ------------------------------------------------------------------------
    #  HELPERS DE PARSING — HTML (avec selectolax)
    # ------------------------------------------------------------------------

    def _parse_search_html(self, html: str) -> list[SearchResult]:
        """Parse la page de résultats de recherche.

        Args:
            html: HTML de la page.

        Returns:
            Liste de ``SearchResult``.
        """
        from selectolax.parser import HTMLParser  # noqa: PLC0415

        tree = HTMLParser(html)
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for item in tree.css(self.selectors["search_item"]):
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

                url = self.normalize_url(link)
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # Couverture (optionnelle)
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
                        cover_url = _normalize_image_url(
                            raw_cover,
                            self._active_base,
                        )

                # Auteur (optionnel)
                author: str | None = None
                author_node = item.css_first(self.selectors["manga_author"])
                if author_node is not None:
                    author = _clean_text(author_node.text()) or None

                slug = _extract_slug(url)

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

    def _is_404_page(self, html: str) -> bool:
        """Détecte une page 404.

        Args:
            html: HTML de la page.

        Returns:
            True si c'est une page 404.
        """
        markers = ("404", "not found", "page introuvable", "introuvable")
        from selectolax.parser import HTMLParser  # noqa: PLC0415

        tree = HTMLParser(html)
        for selector in ("title", "h1"):
            node = tree.css_first(selector)
            if node:
                text = node.text().lower()
                if any(m in text for m in markers):
                    return True
        return False

    # ------------------------------------------------------------------------
    #  INTROSPECTION
    # ------------------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """Retourne une description du parser (pour la CLI et l'API REST).

        Returns:
            Dict sérialisable JSON.
        """
        return {
            "site_id": self.site_id,
            "language": self.language,
            "adult": self.adult,
            "base_url": self.base_url,
            "active_mirror": self._active_base,
            "mirrors": list(self.mirror_domains),
            "cloudflare_strategy": self.cloudflare_strategy,
            "content_rating": self.content_rating.value,
            "selectors_count": len(self.selectors),
        }

    # ------------------------------------------------------------------------
    #  MÉTHODE UTILITAIRE — rotation de miroirs
    # ------------------------------------------------------------------------

    @property
    def current_mirror(self) -> str:
        """Retourne le miroir actuellement utilisé."""
        return self._active_base

    def rotate_mirror(self) -> str:
        """Bascule vers le miroir suivant (rotation circulaire).

        Cette méthode n'est **pas** appelée automatiquement — c'est à
        l'appelant (worker de téléchargement) de décider quand basculer.

        Returns:
            URL du nouveau miroir actif.
        """
        if len(self.mirror_domains) <= 1:
            return self._active_base

        self._mirror_index = (self._mirror_index + 1) % len(self.mirror_domains)
        self._active_base = self.mirror_domains[self._mirror_index]
        logger.bind(site=self.site_id).info(
            "Rotation miroir : nouveau domaine {}",
            self._active_base,
        )
        return self._active_base


# ============================================================================
#  EXEMPLES D'IMPLÉMENTATION — À DÉCOMMENTER ET ADAPTER
# ============================================================================
# Ces exemples ne sont PAS exécutés — ils servent de référence pour
# comprendre comment implémenter chaque méthode. Copier-coller dans le
# corps de la méthode correspondante, puis adapter au site cible.

# --- Exemple 1 : parser HTML simple (recherche) -----------------------------
#
# async def search(self, query: str, *, page: int = 1) -> list[SearchResult]:
#     query = query.strip()
#     if not query:
#         raise ParserError("query vide")
#
#     params = {"q": query}
#     if page > 1:
#         params["page"] = str(page)
#
#     html = await self._fetch_html(
#         self._active_base + self.search_path,
#         params=params,
#     )
#     return self._parse_search_html(html)


# --- Exemple 2 : parser avec API JSON ---------------------------------------
#
# async def search(self, query: str, *, page: int = 1) -> list[SearchResult]:
#     data = await self._fetch_json(
#         self._active_base + "/api/search",
#         params={"q": query, "page": str(page)},
#     )
#     items = data.get("results", [])
#     return [
#         SearchResult(
#             source_id=str(item["id"]),
#             title=item["title"],
#             url=f"{self._active_base}/manga/{item['id']}",
#             site_id=self.site_id,
#             language=self.language,
#             adult=self.adult,
#         )
#         for item in items
#     ]


# --- Exemple 3 : construction d'un Manga ------------------------------------
#
# def _parse_manga_html(self, html: str, url: str) -> Manga:
#     from selectolax.parser import HTMLParser
#
#     tree = HTMLParser(html)
#     title_node = tree.css_first(self.selectors["manga_title"])
#     title = _clean_text(title_node.text()) if title_node else ""
#
#     desc_node = tree.css_first(self.selectors["manga_description"])
#     description = _clean_text(desc_node.text()) if desc_node else None
#
#     cover_url = None
#     cover_node = tree.css_first(self.selectors["manga_cover"])
#     if cover_node is not None:
#         raw = cover_node.attributes.get("src") or ""
#         if raw:
#             cover_url = _normalize_image_url(raw, self._active_base)
#
#     slug = _extract_slug(url) or "unknown"
#
#     return Manga(
#         id=f"{self.site_id}:{slug}",
#         source_id=slug,
#         site=self.site_id,
#         title=title,
#         alternative_titles=[],
#         description=description,
#         author=None,
#         artist=None,
#         genres=[],
#         status=MangaStatus.UNKNOWN,
#         year=None,
#         cover_url=cover_url,
#         language=Language.FR,
#         content_rating=self.content_rating,
#         chapters=[],
#         url=url,
#         updated_at=datetime.now(UTC),
#     )


# --- Exemple 4 : extraction des pages avec lazy-load ------------------------
#
# def _parse_pages_html(self, html: str, chapter: Chapter) -> list[Page]:
#     from selectolax.parser import HTMLParser
#
#     tree = HTMLParser(html)
#     pages: list[Page] = []
#
#     for idx, img in enumerate(tree.css(self.selectors["page_image"])):
#         raw_url = (
#             img.attributes.get("data-src")
#             or img.attributes.get("data-lazy-src")
#             or img.attributes.get("data-original")
#             or img.attributes.get("src")
#             or ""
#         )
#         if not raw_url or _is_blob_url(raw_url) or _is_tracking_url(raw_url):
#             continue
#
#         url = _normalize_image_url(raw_url, self._active_base)
#         ext = Path(urlparse(url).path).suffix.lower() or ".jpg"
#         if ext not in ALLOWED_IMAGE_EXTS:
#             ext = ".jpg"
#
#         pages.append(
#             Page(
#                 index=idx,
#                 url=url,
#                 filename=f"page_{idx:04d}{ext}",
#                 checksum=None,
#             ),
#         )
#
#     return pages


# ============================================================================
#  EXPORTS
# ============================================================================

__all__ = ["BASE_URL", "MIRROR_DOMAINS", "SELECTORS", "TemplateParser"]
