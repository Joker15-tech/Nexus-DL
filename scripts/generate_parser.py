#!/usr/bin/env python3
"""Générateur de squelettes de parsers pour NexusDL.

Produit un fichier parser pré-rempli à partir d'un des templates disponibles,
l'enregistre dans sites.yaml (ou sites_overrides.yaml) et optionnellement un
fichier de test pytest. Réduit drastiquement le boilerplate d'ajout de site.

Templates disponibles :
    madara          → thème WordPress Madara
    mangathemesia   → thème MangaThemesia
    foolslide       → FoolSlide
    api_based       → API REST (MangaDex-like)
    playwright      → site JS / Cloudflare
    minimal         → squelette vide

Example:
    Assistant interactif ::

        python scripts/generate_parser.py new

    Non-interactif (pour CI/scripts) ::

        python scripts/generate_parser.py new \\
            --site-id my_site \\
            --name "My Site" \\
            --template madara \\
            --domain https://my-site.example.com \\
            --language fr \\
            --register-in sites_overrides

    Voir les templates ::

        python scripts/generate_parser.py list-templates

    Valider un parser généré (import + syntaxe) ::

        python scripts/generate_parser.py validate my_site
"""

from __future__ import annotations

import ast
import importlib
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Annotated, Final

# --- Résolution des chemins racine -----------------------------------------
_SCRIPT_DIR: Final[Path] = Path(__file__).resolve().parent
_ROOT_DIR: Final[Path] = _SCRIPT_DIR.parent
_SRC_DIR: Final[Path] = _ROOT_DIR / "src"
_PARSERS_DIR: Final[Path] = _SRC_DIR / "nexusdl" / "parsers"
_SITES_YAML: Final[Path] = _SRC_DIR / "nexusdl" / "core" / "registry" / "sites.yaml"
_TESTS_DIR: Final[Path] = _ROOT_DIR / "tests" / "parsers"

if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import typer  # noqa: E402
from loguru import logger  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.prompt import Confirm, Prompt  # noqa: E402
from rich.syntax import Syntax  # noqa: E402
from rich.table import Table  # noqa: E402

# ============================================================================
#  Constantes
# ============================================================================

console: Final[Console] = Console()
app: Final[typer.Typer] = typer.Typer(
    name="generate-parser",
    help="Générateur de squelettes de parsers NexusDL.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

SITE_ID_REGEX: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{2,40}$")

LANGUAGE_TO_DIR: Final[dict[str, str]] = {
    "fr": "fr",
    "en": "en",
    "es": "es",
    "de": "de",
    "it": "it",
    "pt": "pt",
    "ja": "ja",
    "ko": "kr",
    "zh": "zh",
    "adult": "adult",
}


class Template(str, Enum):
    """Templates de parser disponibles."""

    MADARA = "madara"
    MANGATHEMESIA = "mangathemesia"
    FOOLSLIDE = "foolslide"
    API_BASED = "api_based"
    PLAYWRIGHT = "playwright"
    MINIMAL = "minimal"


class RegisterTarget(str, Enum):
    """Où enregistrer le nouveau site."""

    SITES = "sites"                        # src/nexusdl/core/registry/sites.yaml (PR)
    SITES_OVERRIDES = "sites_overrides"    # config/sites_overrides.yaml (local)
    NONE = "none"                          # Ne pas enregistrer


@dataclass(slots=True)
class ParserSpec:
    """Spécification d'un parser à générer.

    Attributes:
        site_id: Identifiant unique (snake_case).
        name: Nom affiché du site.
        class_name: Nom de la classe Python (PascalCase).
        template: Template à utiliser.
        domain: Domaine principal (https://...).
        extra_domains: Domaines additionnels (miroirs).
        language: Code langue (fr, en, ..., adult).
        adult: Site 18+.
        parser_module_path: Chemin d'import du parser (calculé).
        register_in: Où enregistrer le site.
        generate_test: Générer un fichier de test.
        base_url: URL de base (déduite du domaine si absente).
    """

    site_id: str
    name: str
    class_name: str
    template: Template
    domain: str
    extra_domains: list[str] = field(default_factory=list)
    language: str = "fr"
    adult: bool = False
    register_in: RegisterTarget = RegisterTarget.SITES_OVERRIDES
    generate_test: bool = True
    base_url: str = ""

    @property
    def parser_module(self) -> str:
        """Module Python d'import du parser."""
        subdir = LANGUAGE_TO_DIR.get(self.language, self.language)
        return f"nexusdl.parsers.{subdir}.{self.site_id}"

    @property
    def parser_file(self) -> Path:
        """Chemin absolu du fichier parser à générer."""
        subdir = LANGUAGE_TO_DIR.get(self.language, self.language)
        return _PARSERS_DIR / subdir / f"{self.site_id}.py"

    @property
    def test_file(self) -> Path:
        """Chemin absolu du fichier de test à générer."""
        return _TESTS_DIR / self.language / f"test_{self.site_id}.py"


# ============================================================================
#  Utilitaires de nommage
# ============================================================================


def slugify_site_id(value: str) -> str:
    """Convertit une chaîne arbitraire en site_id valide (snake_case).

    Args:
        value: Chaîne source (nom de site, URL, etc.).

    Returns:
        Identifiant snake_case ASCII minuscule, préfixé si nécessaire.

    Raises:
        ValueError: Si le résultat ne matche pas SITE_ID_REGEX.
    """
    # Normalise les accents → ASCII
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    # Remplace tout caractère non-alphanumérique par un underscore
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_only).strip("_").lower()
    if not cleaned:
        msg = f"Impossible de dériver un site_id depuis : {value!r}"
        raise ValueError(msg)
    if cleaned[0].isdigit():
        cleaned = f"site_{cleaned}"
    if not SITE_ID_REGEX.match(cleaned):
        msg = f"site_id dérivé invalide : {cleaned!r} (regex: {SITE_ID_REGEX.pattern})"
        raise ValueError(msg)
    return cleaned


def to_class_name(site_id: str) -> str:
    """Convertit un site_id en nom de classe PascalCase.

    Args:
        site_id: Identifiant snake_case.

    Returns:
        Nom de classe PascalCase avec suffixe "Parser".
    """
    parts = [p for p in site_id.split("_") if p]
    return "".join(p.capitalize() for p in parts) + "Parser"


def normalize_domain(domain: str) -> str:
    """Normalise un domaine en URL absolue sans trailing slash.

    Args:
        domain: Domaine ou URL.

    Returns:
        URL absolue (https par défaut).

    Raises:
        ValueError: Si le domaine est vide.
    """
    domain = domain.strip()
    if not domain:
        msg = "Domaine vide"
        raise ValueError(msg)
    if not domain.startswith(("http://", "https://")):
        domain = f"https://{domain}"
    return domain.rstrip("/")


# ============================================================================
#  Templates
# ============================================================================


def _render_madara(spec: ParserSpec) -> str:
    """Template parser Madara (WordPress)."""
    return f'''"""Parser pour {spec.name} ({spec.domain}).

Site basé sur le thème WordPress Madara. La majorité des méthodes sont
fournies par `MadaraMixin` ; seules les spécificités (sélecteurs CSS,
pagination, cas particuliers) sont à ajuster ici.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from nexusdl.parsers.base import BaseParser
from nexusdl.parsers.mixins.wordpress_madara import MadaraMixin

if TYPE_CHECKING:
    from nexusdl.core.models.manga import Chapter, Manga
    from nexusdl.core.models.site import SiteConfig
    from nexusdl.core.session.http_session import HttpSession
    from nexusdl.core.session.playwright_pool import PlaywrightPool
    from nexusdl.parsers.base import SearchResult


class {spec.class_name}(MadaraMixin, BaseParser):
    """Parser pour {spec.name}."""

    site_id: ClassVar[str] = "{spec.site_id}"
    language: ClassVar[str] = "{spec.language}"
    adult: ClassVar[bool] = {spec.adult!r}

    base_url: ClassVar[str] = "{spec.domain}"

    # --- Options du mixin Madara -------------------------------------------
    # À ajuster selon le site (les valeurs par défaut couvrent le cas standard)
    search_path: ClassVar[str] = "/?s={{query}}&post_type=wp-manga"
    manga_path_template: ClassVar[str] = "/manga/{{slug}}/"
    chapter_path_template: ClassVar[str] = "/manga/{{slug}}/{{chapter_slug}}/"

    # Sélecteurs CSS — surcharger uniquement si le site diffère du standard
    selectors: ClassVar[dict[str, str]] = {{
        "search_item": "div.c-tabs-item__content",
        "search_title": "div.post-title h3 a",
        "search_cover": "div.c-image-hover img",
        "manga_title": "div.post-title h1",
        "manga_description": "div.description-summary",
        "manga_cover": "div.summary_image img",
        "manga_author": "div.author-content",
        "chapter_item": "li.wp-manga-chapter a",
        "page_image": "div.reading-content img",
    }}

    def __init__(
        self,
        config: SiteConfig,
        session: HttpSession,
        *,
        playwright_pool: PlaywrightPool | None = None,
    ) -> None:
        """Initialise le parser.

        Args:
            config: Configuration du site (chargée depuis sites.yaml).
            session: Session HTTP configurée pour ce site.
            playwright_pool: Pool Playwright optionnel (si Cloudflare).
        """
        super().__init__(config, session, playwright_pool=playwright_pool)

    # --- Surcharges éventuelles --------------------------------------------
    # Décommenter et adapter si le mixin ne suffit pas.

    # async def search(self, query: str, *, page: int = 1) -> list[SearchResult]:
    #     ...

    # async def get_pages(self, chapter: Chapter) -> list[Page]:
    #     ...
'''


def _render_mangathemesia(spec: ParserSpec) -> str:
    """Template parser MangaThemesia."""
    return f'''"""Parser pour {spec.name} ({spec.domain}).

Site basé sur le thème MangaThemesia (utilisé par AsuraScans, Flame, etc.).
La majorité des méthodes sont fournies par `MangaThemesiaMixin`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from nexusdl.parsers.base import BaseParser
from nexusdl.parsers.mixins.mangathemesia import MangaThemesiaMixin

if TYPE_CHECKING:
    from nexusdl.core.models.site import SiteConfig
    from nexusdl.core.session.http_session import HttpSession
    from nexusdl.core.session.playwright_pool import PlaywrightPool


class {spec.class_name}(MangaThemesiaMixin, BaseParser):
    """Parser pour {spec.name}."""

    site_id: ClassVar[str] = "{spec.site_id}"
    language: ClassVar[str] = "{spec.language}"
    adult: ClassVar[bool] = {spec.adult!r}

    base_url: ClassVar[str] = "{spec.domain}"

    # --- Options MangaThemesia ---------------------------------------------
    search_path: ClassVar[str] = "/?s={{query}}"
    series_path_template: ClassVar[str] = "/series/{{slug}}/"
    chapter_path_template: ClassVar[str] = "/series/{{slug}}/{{chapter_slug}}/"

    # Le thème expose souvent un endpoint JSON pour la liste des pages
    uses_ajax_pages: ClassVar[bool] = True
    ajax_pages_endpoint: ClassVar[str] = "/wp-admin/admin-ajax.php"

    selectors: ClassVar[dict[str, str]] = {{
        "search_item": "div.listupd div.bs",
        "search_title": "div.bsx > a",
        "search_cover": "div.bsx > a > img",
        "manga_title": "div.bigcontent h1.entry-title",
        "manga_description": "div.entry-content",
        "manga_cover": "div.thumb img",
        "chapter_item": "div.eplister ul li a",
        "page_image": "div#readerarea img",
    }}

    def __init__(
        self,
        config: SiteConfig,
        session: HttpSession,
        *,
        playwright_pool: PlaywrightPool | None = None,
    ) -> None:
        """Initialise le parser.

        Args:
            config: Configuration du site.
            session: Session HTTP configurée.
            playwright_pool: Pool Playwright optionnel.
        """
        super().__init__(config, session, playwright_pool=playwright_pool)
'''


def _render_foolslide(spec: ParserSpec) -> str:
    """Template parser FoolSlide."""
    return f'''"""Parser pour {spec.name} ({spec.domain}).

Site basé sur FoolSlide (Scan-Manga, etc.). Le mixin fournit la logique
commune (endpoints `/directory/`, `/read/`, pagination).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from nexusdl.parsers.base import BaseParser
from nexusdl.parsers.mixins.foolslide import FoolSlideMixin

if TYPE_CHECKING:
    from nexusdl.core.models.site import SiteConfig
    from nexusdl.core.session.http_session import HttpSession
    from nexusdl.core.session.playwright_pool import PlaywrightPool


class {spec.class_name}(FoolSlideMixin, BaseParser):
    """Parser pour {spec.name}."""

    site_id: ClassVar[str] = "{spec.site_id}"
    language: ClassVar[str] = "{spec.language}"
    adult: ClassVar[bool] = {spec.adult!r}

    base_url: ClassVar[str] = "{spec.domain}"

    # --- Options FoolSlide -------------------------------------------------
    directory_path: ClassVar[str] = "/directory/"
    search_path: ClassVar[str] = "/search/"
    read_path_template: ClassVar[str] = "/read/{{series_slug}}/{{lang}}/{{chapter_slug}}/page/{{page}}"

    # Certains sites FoolSlide sont paginés via un paramètre GET
    uses_offset_pagination: ClassVar[bool] = False

    def __init__(
        self,
        config: SiteConfig,
        session: HttpSession,
        *,
        playwright_pool: PlaywrightPool | None = None,
    ) -> None:
        """Initialise le parser.

        Args:
            config: Configuration du site.
            session: Session HTTP configurée.
            playwright_pool: Pool Playwright optionnel.
        """
        super().__init__(config, session, playwright_pool=playwright_pool)
'''


def _render_api_based(spec: ParserSpec) -> str:
    """Template parser API-based."""
    return f'''"""Parser pour {spec.name} ({spec.domain}).

Site exposant une API REST (JSON). Utilise `ApiBasedMixin` pour la logique
générique ; seuls les endpoints et la structure des réponses diffèrent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from nexusdl.parsers.base import BaseParser
from nexusdl.parsers.mixins.api_based import ApiBasedMixin

if TYPE_CHECKING:
    from nexusdl.core.models.manga import Chapter, Manga, Page
    from nexusdl.core.models.site import SiteConfig
    from nexusdl.core.session.http_session import HttpSession
    from nexusdl.core.session.playwright_pool import PlaywrightPool
    from nexusdl.parsers.base import SearchResult


class {spec.class_name}(ApiBasedMixin, BaseParser):
    """Parser pour {spec.name}."""

    site_id: ClassVar[str] = "{spec.site_id}"
    language: ClassVar[str] = "{spec.language}"
    adult: ClassVar[bool] = {spec.adult!r}

    base_url: ClassVar[str] = "{spec.domain}"
    api_base: ClassVar[str] = "{spec.domain}/api"

    # --- Endpoints ---------------------------------------------------------
    # Utiliser {{id}}, {{query}}, {{page}} comme placeholders
    search_endpoint: ClassVar[str] = "/search?q={{query}}&page={{page}}"
    manga_endpoint: ClassVar[str] = "/manga/{{id}}"
    chapters_endpoint: ClassVar[str] = "/manga/{{id}}/chapters"
    pages_endpoint: ClassVar[str] = "/chapter/{{id}}/pages"

    # --- API key (si requise) ----------------------------------------------
    # Nom de la variable d'env contenant la clé — jamais en clair dans le code
    api_key_env: ClassVar[str | None] = None

    def __init__(
        self,
        config: SiteConfig,
        session: HttpSession,
        *,
        playwright_pool: PlaywrightPool | None = None,
    ) -> None:
        """Initialise le parser.

        Args:
            config: Configuration du site.
            session: Session HTTP configurée.
            playwright_pool: Pool Playwright optionnel.
        """
        super().__init__(config, session, playwright_pool=playwright_pool)

    # --- Mapping des réponses JSON ----------------------------------------
    # Surcharger ces méthodes selon la structure de l'API.

    def _parse_search_response(self, payload: dict[str, Any]) -> list[SearchResult]:
        """Convertit la réponse de recherche en SearchResult.

        Args:
            payload: Réponse JSON brute de l'API.

        Returns:
            Liste de résultats de recherche.
        """
        # TODO: adapter selon la structure réelle (voir doc de l'API)
        items = payload.get("data", payload.get("results", []))
        return [
            self._build_search_result(
                source_id=str(item["id"]),
                title=item["title"],
                cover_url=item.get("cover"),
                url=f"{{self.base_url}}/manga/{{item['id']}}",
            )
            for item in items
        ]

    def _parse_manga_response(self, payload: dict[str, Any]) -> Manga:
        """Convertit la réponse manga en objet Manga.

        Args:
            payload: Réponse JSON brute.

        Returns:
            Objet Manga peuplé.
        """
        # TODO: adapter
        raise NotImplementedError

    def _parse_chapters_response(self, payload: dict[str, Any]) -> list[Chapter]:
        """Convertit la réponse chapitres en liste d'objets Chapter.

        Args:
            payload: Réponse JSON brute.

        Returns:
            Liste de chapitres.
        """
        # TODO: adapter
        raise NotImplementedError

    def _parse_pages_response(self, payload: dict[str, Any]) -> list[Page]:
        """Convertit la réponse pages en liste d'objets Page.

        Args:
            payload: Réponse JSON brute.

        Returns:
            Liste de pages.
        """
        # TODO: adapter
        raise NotImplementedError
'''


def _render_playwright(spec: ParserSpec) -> str:
    """Template parser Playwright (JS/Cloudflare lourd)."""
    return f'''"""Parser pour {spec.name} ({spec.domain}).

Site nécessitant l'exécution JS et/ou un bypass Cloudflare avancé.
Utilise `CloudflareMixin` + `PlaywrightPool` pour récupérer le HTML rendu.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from nexusdl.parsers.base import BaseParser
from nexusdl.parsers.mixins.cloudflare import CloudflareMixin
from nexusdl.parsers.mixins.js_rendered import JsRenderedMixin

if TYPE_CHECKING:
    from nexusdl.core.models.manga import Chapter, Manga, Page
    from nexusdl.core.models.site import SiteConfig
    from nexusdl.core.session.http_session import HttpSession
    from nexusdl.core.session.playwright_pool import PlaywrightPool
    from nexusdl.parsers.base import SearchResult


class {spec.class_name}(CloudflareMixin, JsRenderedMixin, BaseParser):
    """Parser pour {spec.name} (rendu JS + Cloudflare)."""

    site_id: ClassVar[str] = "{spec.site_id}"
    language: ClassVar[str] = "{spec.language}"
    adult: ClassVar[bool] = {spec.adult!r}

    base_url: ClassVar[str] = "{spec.domain}"

    # --- Options Playwright ------------------------------------------------
    wait_selector_search: ClassVar[str] = "div.search-results, ul.manga-list"
    wait_selector_chapter: ClassVar[str] = "div.chapter-content, div.reader"
    wait_selector_pages: ClassVar[str] = "div.reader img, div.chapter-images img"
    wait_timeout: ClassVar[float] = 30.0

    # --- Cloudflare --------------------------------------------------------
    cloudflare_strategy: ClassVar[str] = "playwright"  # ou "flaresolverr"
    cloudflare_cookie_name: ClassVar[str] = "cf_clearance"

    # --- Sélecteurs CSS ----------------------------------------------------
    selectors: ClassVar[dict[str, str]] = {{
        "search_item": "div.search-results div.manga-item",
        "search_title": "h3 a",
        "search_cover": "img.cover",
        "manga_title": "h1.title",
        "chapter_item": "ul.chapter-list li a",
        "page_image": "div.reader img",
    }}

    def __init__(
        self,
        config: SiteConfig,
        session: HttpSession,
        *,
        playwright_pool: PlaywrightPool | None = None,
    ) -> None:
        """Initialise le parser.

        Args:
            config: Configuration du site.
            session: Session HTTP configurée.
            playwright_pool: Pool Playwright (obligatoire pour ce template).

        Raises:
            ValueError: Si `playwright_pool` est absent.
        """
        if playwright_pool is None:
            msg = "Ce parser nécessite un PlaywrightPool (site protégé par Cloudflare)."
            raise ValueError(msg)
        super().__init__(config, session, playwright_pool=playwright_pool)

    async def search(self, query: str, *, page: int = 1) -> list[SearchResult]:
        """Recherche via Playwright (JS requis).

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-indexé).

        Returns:
            Liste de résultats.
        """
        url = f"{{self.base_url}}/?s={{query}}&page={{page}}"
        html = await self.playwright_pool.fetch_html(
            url,
            wait_selector=self.wait_selector_search,
            timeout=self.wait_timeout,
        )
        return self._parse_search_html(html)

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère les métadonnées d'un manga via Playwright.

        Args:
            url_or_id: URL ou identifiant du manga.

        Returns:
            Objet Manga peuplé.
        """
        url = self.normalize_url(url_or_id)
        html = await self.playwright_pool.fetch_html(
            url,
            wait_selector=self.wait_selector_chapter,
            timeout=self.wait_timeout,
        )
        return self._parse_manga_html(html, url)

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs de pages (souvent chargées en JS).

        Args:
            chapter: Chapitre ciblé.

        Returns:
            Liste de pages.
        """
        html = await self.playwright_pool.fetch_html(
            str(chapter.url),
            wait_selector=self.wait_selector_pages,
            timeout=self.wait_timeout,
        )
        return self._parse_pages_html(html, chapter)
'''


def _render_minimal(spec: ParserSpec) -> str:
    """Template parser minimal (squelette vide)."""
    return f'''"""Parser pour {spec.name} ({spec.domain}).

Squelette minimal — toutes les méthodes abstraites de `BaseParser` sont
à implémenter selon la logique spécifique du site.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from nexusdl.parsers.base import BaseParser

if TYPE_CHECKING:
    from nexusdl.core.models.manga import Chapter, Manga, Page
    from nexusdl.core.models.site import SiteConfig
    from nexusdl.core.session.http_session import HttpSession
    from nexusdl.core.session.playwright_pool import PlaywrightPool
    from nexusdl.parsers.base import SearchResult


class {spec.class_name}(BaseParser):
    """Parser pour {spec.name}."""

    site_id: ClassVar[str] = "{spec.site_id}"
    language: ClassVar[str] = "{spec.language}"
    adult: ClassVar[bool] = {spec.adult!r}

    base_url: ClassVar[str] = "{spec.domain}"

    def __init__(
        self,
        config: SiteConfig,
        session: HttpSession,
        *,
        playwright_pool: PlaywrightPool | None = None,
    ) -> None:
        """Initialise le parser.

        Args:
            config: Configuration du site.
            session: Session HTTP configurée.
            playwright_pool: Pool Playwright optionnel.
        """
        super().__init__(config, session, playwright_pool=playwright_pool)

    async def search(self, query: str, *, page: int = 1) -> list[SearchResult]:
        """Recherche des mangas correspondant à la requête.

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-indexé).

        Returns:
            Liste de résultats de recherche.
        """
        raise NotImplementedError

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère les métadonnées complètes d'un manga.

        Args:
            url_or_id: URL ou identifiant du manga.

        Returns:
            Objet Manga peuplé.
        """
        raise NotImplementedError

    async def get_chapters(self, manga: Manga) -> list[Chapter]:
        """Récupère la liste des chapitres d'un manga.

        Args:
            manga: Manga cible.

        Returns:
            Liste de chapitres triés.
        """
        raise NotImplementedError

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste de pages ordonnées.
        """
        raise NotImplementedError
'''


TEMPLATES: Final[dict[Template, callable]] = {
    Template.MADARA: _render_madara,
    Template.MANGATHEMESIA: _render_mangathemesia,
    Template.FOOLSLIDE: _render_foolslide,
    Template.API_BASED: _render_api_based,
    Template.PLAYWRIGHT: _render_playwright,
    Template.MINIMAL: _render_minimal,
}

TEMPLATE_DESCRIPTIONS: Final[dict[Template, str]] = {
    Template.MADARA: "Thème WordPress Madara (SushiScan, Anime-Scans, etc.)",
    Template.MANGATHEMESIA: "Thème MangaThemesia (AsuraScans, Flame, etc.)",
    Template.FOOLSLIDE: "FoolSlide (Scan-Manga, etc.)",
    Template.API_BASED: "API REST JSON (MangaDex, Comick, etc.)",
    Template.PLAYWRIGHT: "Site JS/Cloudflare (Playwright + bypass)",
    Template.MINIMAL: "Squelette vide (cas exotiques)",
}


# ============================================================================
#  Génération de tests
# ============================================================================


def render_test(spec: ParserSpec) -> str:
    """Génère un fichier de test pytest pour le parser.

    Args:
        spec: Spécification du parser.

    Returns:
        Contenu du fichier de test.
    """
    return f'''"""Tests pour le parser {spec.name} ({spec.site_id})."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from nexusdl.core.models.site import SiteCapabilities, SiteConfig
from nexusdl.core.session.http_session import HttpSession
from {spec.parser_module} import {spec.class_name}


@pytest.fixture
def site_config() -> SiteConfig:
    """Configuration de test pour le site."""
    return SiteConfig(
        id="{spec.site_id}",
        name="{spec.name}",
        domains=["{spec.domain}"],
        parser_class="{spec.parser_module}:{spec.class_name}",
        language="{spec.language}",
        adult={spec.adult!r},
        capabilities=SiteCapabilities(),
    )


@pytest.fixture
async def parser(site_config: SiteConfig) -> {spec.class_name}:
    """Instance de parser pour les tests."""
    async with HttpSession(site_config) as session:
        yield {spec.class_name}(site_config, session)


@pytest.mark.asyncio
async def test_parser_metadata(parser: {spec.class_name}) -> None:
    """Vérifie les métadonnées de classe du parser."""
    assert parser.site_id == "{spec.site_id}"
    assert parser.language == "{spec.language}"
    assert parser.adult is {spec.adult!r}


@pytest.mark.asyncio
@respx.mock
async def test_search(parser: {spec.class_name}) -> None:
    """Vérifie que `search` parse une réponse simulée."""
    respx.get(url__startswith="{spec.domain}").mock(
        return_value=Response(200, text="<html><body></body></html>"),
    )
    results = await parser.search("test")
    assert isinstance(results, list)


# TODO: ajouter des tests avec fixtures HTML réelles dans tests/fixtures/{spec.site_id}/
'''


# ============================================================================
#  Écriture des fichiers
# ============================================================================


def write_parser(spec: ParserSpec, *, dry_run: bool, force: bool) -> Path:
    """Écrit le fichier parser sur disque.

    Args:
        spec: Spécification du parser.
        dry_run: Si True, n'écrit rien.
        force: Si True, écrase un fichier existant.

    Returns:
        Chemin du fichier écrit (ou qui aurait été écrit).

    Raises:
        FileExistsError: Si le fichier existe et `force=False`.
    """
    path = spec.parser_file
    if path.exists() and not force:
        msg = f"Le fichier existe déjà : {path}. Utiliser --force pour écraser."
        raise FileExistsError(msg)

    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        renderer = TEMPLATES[spec.template]
        path.write_text(renderer(spec), encoding="utf-8")

        # Assure la présence d'un __init__.py dans le sous-dossier
        init = path.parent / "__init__.py"
        if not init.exists():
            init.touch()

    return path


def write_test(spec: ParserSpec, *, dry_run: bool, force: bool) -> Path:
    """Écrit le fichier de test sur disque.

    Args:
        spec: Spécification du parser.
        dry_run: Si True, n'écrit rien.
        force: Si True, écrase un fichier existant.

    Returns:
        Chemin du fichier écrit.

    Raises:
        FileExistsError: Si le fichier existe et `force=False`.
    """
    path = spec.test_file
    if path.exists() and not force:
        msg = f"Le fichier de test existe déjà : {path}"
        raise FileExistsError(msg)

    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_test(spec), encoding="utf-8")
        # __init__.py pour que pytest puisse collecter proprement
        init = path.parent / "__init__.py"
        if not init.exists():
            init.touch()

    return path


def register_in_sites_yaml(spec: ParserSpec, *, dry_run: bool) -> None:
    """Ajoute le site dans sites.yaml ou sites_overrides.yaml.

    Implémentation volontairement simple : utilise ruamel.yaml si présent
    pour préserver les commentaires, sinon insère un bloc texte brut.

    Args:
        spec: Spécification du parser.
        dry_run: Si True, n'écrit rien.

    Raises:
        RuntimeError: Si le fichier cible n'existe pas.
    """
    if spec.register_in == RegisterTarget.NONE:
        return

    target = (
        _SITES_YAML
        if spec.register_in == RegisterTarget.SITES
        else _ROOT_DIR / "config" / "sites_overrides.yaml"
    )
    if not target.exists():
        msg = f"Fichier de registre introuvable : {target}"
        raise RuntimeError(msg)

    block = _build_yaml_block(spec)

    if dry_run:
        logger.info("Bloc YAML à insérer dans {} :\n{}", target, block)
        return

    content = target.read_text(encoding="utf-8")
    # Cherche la section "sites:" et insère juste après
    marker = "sites:"
    idx = content.find(marker)
    if idx < 0:
        # Ajoute en fin de fichier
        content += f"\n\n{marker}\n{block}\n"
    else:
        # Insère après "sites:" en préservant l'indentation
        insert_pos = content.find("\n", idx) + 1
        content = content[:insert_pos] + block + "\n" + content[insert_pos:]

    target.write_text(content, encoding="utf-8")


def _build_yaml_block(spec: ParserSpec) -> str:
    """Construit le bloc YAML d'enregistrement d'un site.

    Args:
        spec: Spécification du parser.

    Returns:
        Bloc YAML indenté.
    """
    domains = [spec.domain, *spec.extra_domains]
    domains_yaml = "\n".join(f'      - "{d}"' for d in domains)
    parser_import = f"{spec.parser_module}:{spec.class_name}"

    return f'''  {spec.site_id}:
    name: "{spec.name}"
    domains:
{domains_yaml}
    parser_class: "{parser_import}"
    language: {spec.language}
    adult: {str(spec.adult).lower()}
    capabilities:
      supports_search: true
      supports_manga_info: true
      supports_chapters: true
      supports_pages: true
      supports_download: true
      requires_auth: false
      requires_cloudflare_bypass: {str(spec.template == Template.PLAYWRIGHT).lower()}
      max_concurrent_downloads: 4
      rate_limit_per_second: 2.0
'''


# ============================================================================
#  Validation
# ============================================================================


@dataclass(slots=True)
class ValidationResult:
    """Résultat de validation d'un parser généré.

    Attributes:
        site_id: Site validé.
        syntax_ok: Syntaxe Python valide.
        import_ok: Import du module réussi.
        instantiation_ok: Instanciation réussie.
        errors: Erreurs détaillées.
    """

    site_id: str
    syntax_ok: bool = False
    import_ok: bool = False
    instantiation_ok: bool = False
    errors: list[str] = field(default_factory=list)


def validate_parser(site_id: str, language: str) -> ValidationResult:
    """Valide un parser généré (syntaxe, import, instanciation).

    Args:
        site_id: Identifiant du site.
        language: Code langue (pour localiser le fichier).

    Returns:
        Résultat de validation.
    """
    result = ValidationResult(site_id=site_id)

    subdir = LANGUAGE_TO_DIR.get(language, language)
    path = _PARSERS_DIR / subdir / f"{site_id}.py"
    if not path.exists():
        result.errors.append(f"Fichier introuvable : {path}")
        return result

    # 1. Syntaxe
    try:
        ast.parse(path.read_text(encoding="utf-8"))
        result.syntax_ok = True
    except SyntaxError as exc:
        result.errors.append(f"Erreur de syntaxe : {exc}")
        return result

    # 2. Import
    module_name = f"nexusdl.parsers.{subdir}.{site_id}"
    try:
        importlib.invalidate_caches()
        module = importlib.import_module(module_name)
        result.import_ok = True
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"Import échoué : {type(exc).__name__}: {exc}")
        return result

    # 3. Classe présente
    class_name = to_class_name(site_id)
    if not hasattr(module, class_name):
        result.errors.append(f"Classe {class_name} introuvable dans {module_name}")
        return result

    # 4. Métadonnées de classe présentes
    cls = getattr(module, class_name)
    for attr in ("site_id", "language"):
        if not hasattr(cls, attr):
            result.errors.append(f"Attribut de classe manquant : {attr}")
            return result

    result.instantiation_ok = True
    return result


def render_validation(result: ValidationResult) -> None:
    """Affiche le résultat de validation.

    Args:
        result: Résultat de validation.
    """
    table = Table(title=f"Validation — {result.site_id}", header_style="bold cyan")
    table.add_column("Étape", style="cyan")
    table.add_column("Statut", justify="center")

    def _icon(ok: bool) -> str:
        return "[green]✓[/green]" if ok else "[red]✗[/red]"

    table.add_row("Syntaxe", _icon(result.syntax_ok))
    table.add_row("Import", _icon(result.import_ok))
    table.add_row("Instanciation", _icon(result.instantiation_ok))
    console.print(table)

    if result.errors:
        console.print(Panel("\n".join(f"✗ {e}" for e in result.errors), title="Erreurs", border_style="red"))


# ============================================================================
#  Assistant interactif
# ============================================================================


def _prompt_spec() -> ParserSpec:
    """Assistant interactif de création d'un ParserSpec.

    Returns:
        Spécification complétée par l'utilisateur.

    Raises:
        typer.Exit: Si l'utilisateur annule.
    """
    console.print(Panel("[bold cyan]Assistant de création de parser[/bold cyan]", border_style="cyan"))

    name = Prompt.ask("[cyan]Nom du site[/cyan] (ex: My Manga Site)")
    site_id_default = slugify_site_id(name)
    site_id = Prompt.ask("[cyan]Identifiant unique (snake_case)[/cyan]", default=site_id_default)
    if not SITE_ID_REGEX.match(site_id):
        console.print(f"[red]site_id invalide : {site_id!r} (regex: {SITE_ID_REGEX.pattern})[/red]")
        raise typer.Exit(code=2)

    domain = Prompt.ask("[cyan]Domaine principal[/cyan] (ex: https://example.com)")

    # Choix du template
    console.print()
    console.print("[bold]Templates disponibles :[/bold]")
    templates_list = list(Template)
    for i, tpl in enumerate(templates_list, 1):
        console.print(f"  [cyan]{i}.[/cyan] [bold]{tpl.value}[/bold] — {TEMPLATE_DESCRIPTIONS[tpl]}")
    tpl_choice = Prompt.ask(
        "[cyan]Template[/cyan]",
        choices=[t.value for t in templates_list],
        default=Template.MADARA.value,
    )
    template = Template(tpl_choice)

    language = Prompt.ask(
        "[cyan]Langue[/cyan]",
        choices=["fr", "en", "es", "de", "it", "pt", "ja", "ko", "zh", "adult"],
        default="fr",
    )
    adult = language == "adult" or Confirm.ask("[cyan]Site 18+ ?[/cyan]", default=False)

    register_choice = Prompt.ask(
        "[cyan]Enregistrer dans[/cyan]",
        choices=[t.value for t in RegisterTarget],
        default=RegisterTarget.SITES_OVERRIDES.value,
    )
    register_in = RegisterTarget(register_choice)

    generate_test = Confirm.ask("[cyan]Générer un fichier de test ?[/cyan]", default=True)

    return ParserSpec(
        site_id=site_id,
        name=name,
        class_name=to_class_name(site_id),
        template=template,
        domain=normalize_domain(domain),
        language=language,
        adult=adult,
        register_in=register_in,
        generate_test=generate_test,
    )


# ============================================================================
#  Rendu / preview
# ============================================================================


def preview_spec(spec: ParserSpec) -> None:
    """Affiche un récapitulatif de la spec avant génération.

    Args:
        spec: Spécification à afficher.
    """
    table = Table(title="Récapitulatif", header_style="bold cyan")
    table.add_column("Champ", style="magenta")
    table.add_column("Valeur", style="cyan")
    table.add_row("Site ID", spec.site_id)
    table.add_row("Nom", spec.name)
    table.add_row("Classe", spec.class_name)
    table.add_row("Template", spec.template.value)
    table.add_row("Domaine", spec.domain)
    table.add_row("Langue", spec.language)
    table.add_row("18+", "oui" if spec.adult else "non")
    table.add_row("Enregistrer dans", spec.register_in.value)
    table.add_row("Fichier parser", str(spec.parser_file.relative_to(_ROOT_DIR)))
    if spec.generate_test:
        table.add_row("Fichier test", str(spec.test_file.relative_to(_ROOT_DIR)))
    console.print(table)


def preview_parser_code(spec: ParserSpec) -> None:
    """Affiche un aperçu du code généré.

    Args:
        spec: Spécification.
    """
    renderer = TEMPLATES[spec.template]
    code = renderer(spec)
    console.print()
    console.print(Panel("[bold]Aperçu du parser généré[/bold]", border_style="cyan"))
    console.print(Syntax(code, "python", theme="monokai", line_numbers=True))


# ============================================================================
#  CLI
# ============================================================================


@app.command("new")
def cmd_new(
    site_id: Annotated[str | None, typer.Option("--site-id", help="Identifiant (snake_case)")] = None,
    name: Annotated[str | None, typer.Option("--name", "-n", help="Nom affiché du site")] = None,
    template: Annotated[str | None, typer.Option("--template", "-t", help="Template (voir list-templates)")] = None,
    domain: Annotated[str | None, typer.Option("--domain", "-d", help="Domaine principal")] = None,
    extra_domain: Annotated[list[str] | None, typer.Option("--extra-domain", help="Domaine secondaire (répétable)")] = None,
    language: Annotated[str, typer.Option("--language", "-l", help="Code langue")] = "fr",
    adult: Annotated[bool, typer.Option("--adult/--no-adult")] = False,
    register_in: Annotated[str, typer.Option("--register-in", help="sites | sites_overrides | none")] = "sites_overrides",
    with_test: Annotated[bool, typer.Option("--with-test/--no-test")] = True,
    force: Annotated[bool, typer.Option("--force", "-f", help="Écraser si existant")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Afficher sans écrire")] = False,
    show_code: Annotated[bool, typer.Option("--show-code", help="Afficher le code généré")] = False,
    interactive: Annotated[bool, typer.Option("--interactive", "-i", help="Forcer l'assistant interactif")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Crée un nouveau parser à partir d'un template."""
    import logging as _logging

    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO", format="<level>{level: <8}</level> | {message}")

    # Détermine si on doit lancer l'assistant interactif
    use_interactive = interactive or (site_id is None and name is None and domain is None)

    try:
        if use_interactive:
            spec = _prompt_spec()
        else:
            if site_id is None or name is None or domain is None or template is None:
                console.print(
                    "[red]Mode non-interactif : --site-id, --name, --domain et --template sont requis.[/red]",
                )
                raise typer.Exit(code=2)

            if not SITE_ID_REGEX.match(site_id):
                console.print(f"[red]site_id invalide : {site_id!r}[/red]")
                raise typer.Exit(code=2)

            if template not in {t.value for t in Template}:
                console.print(f"[red]Template inconnu : {template}. Voir `list-templates`.[/red]")
                raise typer.Exit(code=2)

            if register_in not in {r.value for r in RegisterTarget}:
                console.print(f"[red]register-in invalide : {register_in}[/red]")
                raise typer.Exit(code=2)

            spec = ParserSpec(
                site_id=site_id,
                name=name,
                class_name=to_class_name(site_id),
                template=Template(template),
                domain=normalize_domain(domain),
                extra_domains=[normalize_domain(d) for d in (extra_domain or [])],
                language=language,
                adult=adult,
                register_in=RegisterTarget(register_in),
                generate_test=with_test,
            )

        preview_spec(spec)
        if show_code:
            preview_parser_code(spec)

        if dry_run:
            console.print()
            console.print("[yellow]DRY-RUN — aucun fichier écrit.[/yellow]")
            return

        # Écriture du parser
        parser_path = write_parser(spec, dry_run=False, force=force)
        console.print(f"[green]✓ Parser écrit :[/green] {parser_path}")

        # Écriture du test
        if spec.generate_test:
            test_path = write_test(spec, dry_run=False, force=force)
            console.print(f"[green]✓ Test écrit :[/green] {test_path}")

        # Enregistrement dans le registre
        register_in_sites_yaml(spec, dry_run=False)
        if spec.register_in != RegisterTarget.NONE:
            console.print(f"[green]✓ Site enregistré dans[/green] {spec.register_in.value}")

        console.print()
        console.print(
            Panel(
                f"[bold]Prochaines étapes :[/bold]\n"
                f"  1. Compléter les TODO dans [cyan]{parser_path.relative_to(_ROOT_DIR)}[/cyan]\n"
                f"  2. Valider : [cyan]python scripts/generate_parser.py validate {spec.site_id} -l {spec.language}[/cyan]\n"
                f"  3. Tester : [cyan]pytest {spec.test_file.relative_to(_ROOT_DIR) if spec.generate_test else ''}[/cyan]\n"
                f"  4. Voir le guide : [cyan]docs/source/development/adding_parsers.md[/cyan]",
                border_style="green",
            ),
        )

    except FileExistsError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(code=1) from exc
    except (ValueError, RuntimeError) as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(code=2) from exc


@app.command("list-templates")
def cmd_list_templates() -> None:
    """Liste les templates disponibles."""
    table = Table(title="Templates disponibles", header_style="bold cyan")
    table.add_column("Nom", style="magenta")
    table.add_column("Description", style="cyan")
    for tpl, desc in TEMPLATE_DESCRIPTIONS.items():
        table.add_row(tpl.value, desc)
    console.print(table)


@app.command("validate")
def cmd_validate(
    site_id: Annotated[str, typer.Argument(help="ID du site à valider")],
    language: Annotated[str, typer.Option("--language", "-l")] = "fr",
) -> None:
    """Valide la syntaxe, l'import et l'instanciation d'un parser."""
    logger.remove()
    logger.add(sys.stderr, level="WARNING", format="<level>{level: <8}</level> | {message}")

    result = validate_parser(site_id, language)
    render_validation(result)
    if result.errors:
        raise typer.Exit(code=1)


# ============================================================================
#  Entrée
# ============================================================================


def main() -> None:
    """Point d'entrée CLI."""
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrompu.[/yellow]")
        raise typer.Exit(code=130) from None


if __name__ == "__main__":
    main()
