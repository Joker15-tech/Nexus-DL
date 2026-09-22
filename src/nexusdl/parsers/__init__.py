"""Module public des parsers de sites pour NexusDL.

Ce module constitue le point d'entrée de la couche d'adaptateurs secondaires
de l'architecture hexagonale. Il expose l'API publique stable utilisée par
le `core/` (notamment le `SiteRegistry`) pour interagir avec les sources
externes sans connaître leurs spécificités d'implémentation.

Règles d'or :
    1. Ce fichier n'expose QUE les symboles publics stables (contrats, exceptions).
    2. Il n'importe AUCUN parser spécifique (mangadex, sushiscan, nhentai...)
       au chargement du module. Le chargement dynamique est géré par le
       `SiteRegistry` via `importlib` pour éviter de payer le coût d'import
       de 60+ parsers à chaque démarrage.
    3. Il ne dépend d'aucun module de `interfaces/` ni de `core/downloader/`.

Exemple d'utilisation :
    >>> from nexusdl.parsers import BaseParser, ParserCapabilities
    >>> from nexusdl.parsers import ParserError, SiteUnavailableError
    >>>
    >>> class MyParser(BaseParser):
    ...     site_id = "example"
    ...     language = "fr"
    ...     capabilities = ParserCapabilities(supports_search=True)
    ...
    ...     async def search(self, query: str, *, page: int = 1): ...
    ...     async def get_manga(self, url_or_id: str): ...
    ...     async def get_chapters(self, manga): ...
    ...     async def get_pages(self, chapter): ...
"""

from __future__ import annotations

from nexusdl.parsers.base import BaseParser
from nexusdl.parsers.capabilities import ParserCapabilities
from nexusdl.parsers.exceptions import (
    AuthenticationRequiredError,
    ChapterNotFoundError,
    CloudflareBypassError,
    InvalidPageUrlError,
    MangaNotFoundError,
    ParserError,
    ParserNotFoundError,
    ParsingError,
    RateLimitExceededError,
    SiteUnavailableError,
)

__all__ = [
    # === Classe de base ===
    "BaseParser",
    # === Capacités ===
    "ParserCapabilities",
    # === Exceptions (hiérarchie complète) ===
    "ParserError",
    "ParserNotFoundError",
    "ParsingError",
    "SiteUnavailableError",
    "CloudflareBypassError",
    "MangaNotFoundError",
    "ChapterNotFoundError",
    "AuthenticationRequiredError",
    "RateLimitExceededError",
    "InvalidPageUrlError",
]

__version__: str = "0.1.0"
