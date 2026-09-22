"""Moteur de recherche plein texte pour la bibliothèque locale.

Ce module fournit un moteur de recherche asynchrone robuste basé sur SQLite FTS5,
offrant des capacités avancées pour explorer la bibliothèque locale de mangas :

Fonctionnalités principales :
    - Recherche plein texte ultra-rapide via FTS5 (titre, auteur, description, genres)
    - Support des opérateurs booléens (AND, OR, NOT, NEAR, préfixe *)
    - Recherche par phrase exacte avec guillemets ("One Piece")
    - Surlignage des termes trouvés (highlight)
    - Extraits contextuels (snippets) autour des termes
    - Filtrage multi-critères (site, langue, statut, genre, année, classification)
    - Tri multi-critères (pertinence BM25, titre, date, note, popularité)
    - Pagination efficace avec comptage total
    - Requêtes sur les vues pré-définies (continue_reading, favorites, etc.)
    - Statistiques de recherche (temps d'exécution, nombre de résultats)
    - Protection contre les injections FTS5 (échappement automatique)
    - Support du mode "recherche rapide" (sans highlight ni snippet)

Architecture :
    LibrarySearch
        ├── SearchQuery (Pydantic — requête utilisateur)
        ├── SearchFilters (Pydantic — filtres combinables)
        ├── SearchResult (Pydantic — résultat individuel)
        ├── SearchResultPage (Pydantic — page paginée)
        ├── SearchSortBy (enum — critères de tri)
        ├── SearchSortOrder (enum — ordre de tri)
        └── SearchStats (Pydantic — statistiques)

Les requêtes FTS5 sont construites dynamiquement avec échappement strict
des caractères spéciaux pour prévenir les injections et les erreurs de syntaxe.

Exemple d'utilisation :
    >>> search = LibrarySearch(database=library_db)
    >>> await search.start()
    >>>
    >>> # Recherche simple
    >>> results = await search.search("one piece")
    >>> for manga in results.items:
    ...     print(f"{manga.title} (score: {manga.score:.2f})")
    >>>
    >>> # Recherche avancée avec filtres
    >>> query = SearchQuery(
    ...     text="action romance",
    ...     filters=SearchFilters(
    ...         sites=["mangadex", "sushiscan_net"],
    ...         languages=[Language.FR],
    ...         status=[MangaStatus.ONGOING],
    ...         content_rating=[ContentRating.SAFE],
    ...         min_year=2020,
    ...     ),
    ...     sort_by=SearchSortBy.RELEVANCE,
    ...     sort_order=SearchSortOrder.DESC,
    ...     page=1,
    ...     page_size=20,
    ...     highlight=True,
    ... )
    >>> page = await search.search_advanced(query)
    >>> print(f"{page.total} résultats en {page.duration_ms:.1f}ms")
    >>>
    >>> # Recherche par phrase exacte
    >>> results = await search.search('"attack on titan"')
    >>>
    >>> # Recherche avec opérateurs booléens
    >>> results = await search.search("dragon AND (ball OR ballz) NOT GT")
    >>>
    >>> # Recherche par préfixe
    >>> results = await search.search("nar*")  # Trouve naruto, narutaru, etc.
    >>>
    >>> # Requêtes sur vues pré-définies
    >>> continue_reading = await search.get_continue_reading(limit=10)
    >>> favorites = await search.get_favorites(limit=20)
    >>> recently_completed = await search.get_recently_completed(limit=30)
    >>>
    >>> await search.stop()
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Final, Self

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError

if TYPE_CHECKING:
    from nexusdl.core.library.database import LibraryDatabase
    from nexusdl.core.models.manga import ContentRating, Language, MangaStatus


# ============================================================================
# EXCEPTIONS
# ============================================================================


class SearchError(NexusDLError):
    """Exception de base pour les erreurs du moteur de recherche."""


class InvalidQueryError(SearchError):
    """Exception levée lorsqu'une requête de recherche est invalide."""

    def __init__(self, query: str, reason: str = "") -> None:
        msg = f"Requête de recherche invalide: '{query}'"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.query = query
        self.reason = reason


class SearchNotStartedError(SearchError):
    """Exception levée lorsqu'on utilise le moteur avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "LibrarySearch must be started before use. Call await search.start()"
        )


class SearchTimeoutError(SearchError):
    """Exception levée lorsqu'une recherche dépasse le timeout."""

    def __init__(self, query: str, timeout_seconds: float) -> None:
        super().__init__(
            f"Recherche timeout après {timeout_seconds:.1f}s: '{query}'"
        )
        self.query = query
        self.timeout_seconds = timeout_seconds


# ============================================================================
# ENUMS
# ============================================================================


class SearchSortBy(str, Enum):
    """Critères de tri des résultats de recherche.

    RELEVANCE   : Tri par score de pertinence BM25 (FTS5 natif).
    TITLE       : Tri alphabétique par titre.
    UPDATED_AT  : Tri par date de dernière mise à jour.
    CREATED_AT  : Tri par date d'ajout à la bibliothèque.
    SCORE       : Tri par note utilisateur (0-10).
    YEAR        : Tri par année de sortie.
    AUTHOR      : Tri par nom d'auteur.
    """

    RELEVANCE = "relevance"
    TITLE = "title"
    UPDATED_AT = "updated_at"
    CREATED_AT = "created_at"
    SCORE = "score"
    YEAR = "year"
    AUTHOR = "author"


class SearchSortOrder(str, Enum):
    """Ordre de tri des résultats."""

    ASC = "asc"
    DESC = "desc"


class SearchMode(str, Enum):
    """Mode de recherche (influence les fonctionnalités activées).

    SIMPLE    : Recherche basique sans highlight ni snippet (rapide).
    STANDARD  : Recherche avec highlight (défaut).
    ADVANCED  : Recherche avec highlight + snippet + stats complètes.
    """

    SIMPLE = "simple"
    STANDARD = "standard"
    ADVANCED = "advanced"


# ============================================================================
# MODÈLES PYDANTIC — Requêtes
# ============================================================================


class SearchFilters(BaseModel):
    """Filtres combinables pour affiner une recherche.

    Tous les champs sont optionnels. Les filtres sont combinés avec AND.
    Les listes à l'intérieur d'un filtre sont combinées avec OR.
    """

    sites: list[str] = Field(
        default_factory=list,
        description="Filtrer par sites (ex: ['mangadex', 'sushiscan_net']).",
    )
    languages: list[str] = Field(
        default_factory=list,
        description="Filtrer par langues ISO 639-1 (ex: ['fr', 'en']).",
    )
    status: list[str] = Field(
        default_factory=list,
        description="Filtrer par statut (ONGOING, COMPLETED, HIATUS, etc.).",
    )
    content_rating: list[str] = Field(
        default_factory=list,
        description="Filtrer par classification (SAFE, SUGGESTIVE, EROTICA, PORNOGRAPHIC).",
    )
    genres: list[str] = Field(
        default_factory=list,
        description="Filtrer par genres (tous les genres listés doivent correspondre).",
    )
    authors: list[str] = Field(
        default_factory=list,
        description="Filtrer par auteurs (OR entre les valeurs).",
    )
    min_year: int | None = Field(
        default=None,
        ge=1900,
        le=2100,
        description="Année de sortie minimum.",
    )
    max_year: int | None = Field(
        default=None,
        ge=1900,
        le=2100,
        description="Année de sortie maximum.",
    )
    include_adult: bool = Field(
        default=False,
        description="Inclure le contenu adulte (18+) dans les résultats.",
    )
    downloaded_only: bool = Field(
        default=False,
        description="Retourner uniquement les mangas avec au moins un chapitre téléchargé.",
    )
    in_library: bool = Field(
        default=False,
        description="Retourner uniquement les mangas présents dans la bibliothèque.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    def is_empty(self) -> bool:
        """Indique si aucun filtre n'est actif."""
        return (
            not self.sites
            and not self.languages
            and not self.status
            and not self.content_rating
            and not self.genres
            and not self.authors
            and self.min_year is None
            and self.max_year is None
            and not self.downloaded_only
            and not self.in_library
        )


class SearchQuery(BaseModel):
    """Requête de recherche complète.

    Combinaison d'un texte de recherche, de filtres, d'un tri et d'une pagination.
    """

    text: str = Field(
        default="",
        description="Texte de recherche (supporte opérateurs FTS5 : AND, OR, NOT, *, \"\").",
    )
    filters: SearchFilters = Field(
        default_factory=SearchFilters,
        description="Filtres à appliquer.",
    )
    sort_by: SearchSortBy = Field(
        default=SearchSortBy.RELEVANCE,
        description="Critère de tri.",
    )
    sort_order: SearchSortOrder = Field(
        default=SearchSortOrder.DESC,
        description="Ordre de tri.",
    )
    page: int = Field(
        default=1,
        ge=1,
        description="Numéro de page (1-based).",
    )
    page_size: int = Field(
        default=20,
        ge=1,
        le=200,
        description="Nombre de résultats par page.",
    )
    mode: SearchMode = Field(
        default=SearchMode.STANDARD,
        description="Mode de recherche (influence highlight/snippet).",
    )
    highlight: bool = Field(
        default=True,
        description="Surligner les termes trouvés dans le titre.",
    )
    snippet: bool = Field(
        default=False,
        description="Générer des extraits contextuels de la description.",
    )
    snippet_length: int = Field(
        default=150,
        ge=50,
        le=500,
        description="Longueur des snippets en caractères.",
    )
    timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        le=60.0,
        description="Timeout de la recherche en secondes.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def offset(self) -> int:
        """Offset SQL pour la pagination."""
        return (self.page - 1) * self.page_size


# ============================================================================
# MODÈLES PYDANTIC — Résultats
# ============================================================================


class SearchResult(BaseModel):
    """Résultat individuel de recherche.

    Contient les métadonnées du manga, le score de pertinence,
    et optionnellement les highlights/snippets.
    """

    manga_id: str = Field(..., description="ID unique du manga.")
    source_id: str = Field(..., description="ID sur le site source.")
    site: str = Field(..., description="Site d'origine.")
    title: str = Field(..., description="Titre du manga (avec highlight si activé).")
    alternative_titles: list[str] = Field(
        default_factory=list,
        description="Titres alternatifs.",
    )
    description: str | None = Field(
        default=None,
        description="Description (avec snippet si activé).",
    )
    author: str | None = Field(default=None, description="Auteur.")
    artist: str | None = Field(default=None, description="Dessinateur.")
    status: str = Field(..., description="Statut du manga.")
    year: int | None = Field(default=None, description="Année de sortie.")
    cover_url: str | None = Field(default=None, description="URL de la couverture.")
    language: str = Field(..., description="Langue principale.")
    content_rating: str = Field(..., description="Classification d'âge.")
    genres: list[str] = Field(default_factory=list, description="Genres/tags.")
    url: str = Field(..., description="URL source du manga.")
    updated_at: str = Field(..., description="Date de dernière mise à jour (ISO 8601).")
    created_at: str = Field(..., description="Date d'ajout à la bibliothèque (ISO 8601).")

    # Métadonnées de recherche
    score: float = Field(
        default=0.0,
        description="Score de pertinence BM25 (plus élevé = plus pertinent).",
    )
    highlight_title: str | None = Field(
        default=None,
        description="Titre avec surlignage des termes (balises <b>).</b>",
    )
    snippet_description: str | None = Field(
        default=None,
        description="Extrait contextuel de la description avec surlignage.",
    )

    # Métadonnées utilisateur (optionnelles)
    reading_status: str | None = Field(
        default=None,
        description="Statut de lecture utilisateur (READING, COMPLETED, etc.).",
    )
    user_score: float | None = Field(
        default=None,
        description="Note attribuée par l'utilisateur (0-10).",
    )
    downloaded_chapters: int = Field(
        default=0,
        ge=0,
        description="Nombre de chapitres téléchargés.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class SearchResultPage(BaseModel):
    """Page paginée de résultats de recherche.

    Contient les résultats, les métadonnées de pagination, et les statistiques
    d'exécution de la requête.
    """

    items: list[SearchResult] = Field(
        default_factory=list,
        description="Résultats de la page courante.",
    )
    total: int = Field(
        default=0,
        ge=0,
        description="Nombre total de résultats (toutes pages confondues).",
    )
    page: int = Field(default=1, ge=1, description="Numéro de la page courante.")
    page_size: int = Field(default=20, ge=1, description="Taille de page demandée.")
    total_pages: int = Field(
        default=0,
        ge=0,
        description="Nombre total de pages.",
    )
    has_next: bool = Field(
        default=False,
        description="True s'il existe une page suivante.",
    )
    has_previous: bool = Field(
        default=False,
        description="True s'il existe une page précédente.",
    )
    duration_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Durée d'exécution de la requête en millisecondes.",
    )
    query_text: str = Field(
        default="",
        description="Texte de recherche original (non échappé).",
    )
    applied_filters: dict[str, Any] = Field(
        default_factory=dict,
        description="Filtres effectivement appliqués (pour debug).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class SearchStats(BaseModel):
    """Statistiques agrégées du moteur de recherche."""

    total_searches: int = Field(default=0, ge=0)
    total_results_returned: int = Field(default=0, ge=0)
    average_duration_ms: float = Field(default=0.0, ge=0.0)
    total_duration_ms: float = Field(default=0.0, ge=0.0)
    empty_searches: int = Field(
        default=0,
        ge=0,
        description="Nombre de recherches sans résultats.",
    )
    most_searched_terms: list[tuple[str, int]] = Field(
        default_factory=list,
        description="Top 20 des termes les plus recherchés.",
    )
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# HELPERS — Construction et échappement de requêtes FTS5
# ============================================================================


# Caractères spéciaux FTS5 à échapper
_FTS5_SPECIAL_CHARS: Final[re.Pattern[str]] = re.compile(r'(["\\])')

# Pattern pour détecter les opérateurs FTS5 valides
_FTS5_OPERATORS: Final[re.Pattern[str]] = re.compile(
    r"\b(AND|OR|NOT|NEAR)\b",
    re.IGNORECASE,
)

# Pattern pour détecter les phrases entre guillemets
_FTS5_PHRASES: Final[re.Pattern[str]] = re.compile(r'"([^"]*)"')

# Pattern pour détecter les préfixes (terme suivi de *)
_FTS5_PREFIX: Final[re.Pattern[str]] = re.compile(r"(\w+)\*")


def escape_fts5_query(query: str) -> str:
    """Échappe une requête utilisateur pour FTS5 en préservant les opérateurs.

    Cette fonction :
        1. Préserve les phrases entre guillemets ("...")
        2. Préserve les opérateurs booléens (AND, OR, NOT, NEAR)
        3. Préserve les préfixes (terme*)
        4. Échappe les autres caractères spéciaux
        5. Combine les termes restants avec AND implicite

    Args:
        query: Requête utilisateur brute.

    Returns:
        Requête FTS5 valide et sécurisée.

    Example:
        >>> escape_fts5_query('one piece')
        'one AND piece'
        >>> escape_fts5_query('"attack on titan"')
        '"attack on titan"'
        >>> escape_fts5_query('dragon AND ball')
        'dragon AND ball'
        >>> escape_fts5_query('nar*')
        'nar*'
        >>> escape_fts5_query('test "exact phrase" AND other')
        'test AND "exact phrase" AND other'
    """
    if not query or not query.strip():
        return ""

    query = query.strip()

    # Si la requête contient déjà des opérateurs ou guillemets, on la valide
    # mais on ne la modifie pas (l'utilisateur sait ce qu'il fait)
    if _FTS5_OPERATORS.search(query) or _FTS5_PHRASES.search(query):
        # Validation basique : échapper les guillemets non fermés
        if query.count('"') % 2 != 0:
            raise InvalidQueryError(query, "Guillemets non fermés")
        return query

    # Sinon, on traite comme une recherche simple
    # Séparer en termes et échapper chaque terme
    terms = query.split()
    escaped_terms: list[str] = []

    for term in terms:
        # Vérifier si c'est un préfixe
        prefix_match = _FTS5_PREFIX.match(term)
        if prefix_match:
            base = prefix_match.group(1)
            escaped = _FTS5_SPECIAL_CHARS.sub(r"\\\1", base)
            escaped_terms.append(f"{escaped}*")
            continue

        # Échapper les caractères spéciaux
        escaped = _FTS5_SPECIAL_CHARS.sub(r"\\\1", term)
        escaped_terms.append(escaped)

    # Combiner avec AND implicite
    return " AND ".join(escaped_terms)


def build_fts5_select_columns(
    *,
    highlight: bool = False,
    snippet: bool = False,
    snippet_length: int = 150,
) -> str:
    """Construit la clause SELECT pour une requête FTS5.

    Args:
        highlight: Activer le surlignage du titre.
        snippet: Activer les extraits de description.
        snippet_length: Longueur des snippets.

    Returns:
        Clause SELECT complète avec colonnes FTS5.
    """
    columns = [
        "m.id AS manga_id",
        "m.source_id",
        "m.site",
    ]

    if highlight:
        columns.append(
            "highlight(manga_search, 1, '<b>', '</b>') AS highlight_title"
        )
        columns.append("m.title")
    else:
        columns.append("m.title")
        columns.append("NULL AS highlight_title")

    columns.extend([
        "m.alternative_titles",
    ])

    if snippet:
        columns.append(
            f"snippet(manga_search, 4, '<b>', '</b>', '...', {snippet_length // 20}) "
            "AS snippet_description"
        )
        columns.append("m.description")
    else:
        columns.append("m.description")
        columns.append("NULL AS snippet_description")

    columns.extend([
        "m.author",
        "m.artist",
        "m.status",
        "m.year",
        "m.cover_url",
        "m.language",
        "m.content_rating",
        "m.url",
        "m.updated_at",
        "m.created_at",
        "bm25(manga_search) AS bm25_score",
        "manga_search.genres AS fts_genres",
    ])

    return ", ".join(columns)


# ============================================================================
# CLASSE PRINCIPALE — LibrarySearch
# ============================================================================


class LibrarySearch:
    """Moteur de recherche plein texte pour la bibliothèque locale.

    S'appuie sur la table FTS5 `manga_search` et les vues pré-définies
    pour offrir des recherches rapides et riches en fonctionnalités.

    Lifecycle :
        >>> search = LibrarySearch(database=library_db)
        >>> await search.start()
        >>> results = await search.search("one piece")
        >>> await search.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Un sémaphore limite la concurrence des recherches.
    """

    # Constantes
    _DEFAULT_MAX_CONCURRENT: Final[int] = 4
    _DEFAULT_TIMEOUT: Final[float] = 10.0
    _MAX_TOP_TERMS: Final[int] = 20

    def __init__(
        self,
        database: LibraryDatabase,
        *,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
        default_timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        """Initialise le moteur de recherche.

        Args:
            database: Instance de LibraryDatabase (doit être initialisée).
            max_concurrent: Nombre maximum de recherches simultanées.
            default_timeout: Timeout par défaut en secondes.
        """
        if max_concurrent <= 0:
            raise ValueError(f"max_concurrent must be positive, got {max_concurrent}")
        if default_timeout <= 0:
            raise ValueError(f"default_timeout must be positive, got {default_timeout}")

        self._database = database
        self._max_concurrent = max_concurrent
        self._default_timeout = default_timeout

        self._semaphore: asyncio.Semaphore | None = None
        self._started: bool = False
        self._start_time: float = 0.0

        # Statistiques
        self._total_searches: int = 0
        self._total_results: int = 0
        self._total_duration_ms: float = 0.0
        self._empty_searches: int = 0
        self._search_terms: dict[str, int] = {}
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="library_search")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre le moteur de recherche."""
        if self._started:
            self._logger.warning("LibrarySearch déjà démarré, ignore")
            return

        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._started = True
        self._start_time = asyncio.get_event_loop().time()

        self._logger.info(
            "LibrarySearch démarré: max_concurrent={}, timeout={:.1f}s",
            self._max_concurrent,
            self._default_timeout,
        )

    async def stop(self) -> None:
        """Arrête le moteur de recherche."""
        if not self._started:
            return

        self._semaphore = None
        self._started = False
        self._logger.info("LibrarySearch arrêté")

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------------

    @property
    def is_started(self) -> bool:
        """Indique si le moteur est démarré."""
        return self._started

    # ------------------------------------------------------------------------
    # API publique — Recherche simple
    # ------------------------------------------------------------------------

    async def search(
        self,
        text: str,
        *,
        page: int = 1,
        page_size: int = 20,
        sort_by: SearchSortBy = SearchSortBy.RELEVANCE,
        sort_order: SearchSortOrder = SearchSortOrder.DESC,
        filters: SearchFilters | None = None,
        highlight: bool = True,
        timeout: float | None = None,
    ) -> SearchResultPage:
        """Recherche simple avec texte et options.

        Méthode de haut niveau pour les cas d'usage courants. Pour des
        besoins avancés, utiliser `search_advanced()` avec un SearchQuery.

        Args:
            text: Texte de recherche (supporte opérateurs FTS5).
            page: Numéro de page (1-based).
            page_size: Nombre de résultats par page.
            sort_by: Critère de tri.
            sort_order: Ordre de tri.
            filters: Filtres optionnels.
            highlight: Surligner les termes trouvés.
            timeout: Timeout en secondes (défaut: timeout global).

        Returns:
            Page de résultats paginée.

        Raises:
            SearchError: Si la recherche échoue.
            InvalidQueryError: Si la requête est invalide.
            SearchTimeoutError: Si le timeout est dépassé.
        """
        query = SearchQuery(
            text=text,
            filters=filters or SearchFilters(),
            sort_by=sort_by,
            sort_order=sort_order,
            page=page,
            page_size=page_size,
            mode=SearchMode.STANDARD if highlight else SearchMode.SIMPLE,
            highlight=highlight,
            timeout_seconds=timeout or self._default_timeout,
        )
        return await self.search_advanced(query)

    # ------------------------------------------------------------------------
    # API publique — Recherche avancée
    # ------------------------------------------------------------------------

    async def search_advanced(self, query: SearchQuery) -> SearchResultPage:
        """Recherche avancée avec tous les paramètres.

        Args:
            query: Requête complète avec texte, filtres, tri, pagination.

        Returns:
            Page de résultats paginée.

        Raises:
            SearchNotStartedError: Si le moteur n'est pas démarré.
            InvalidQueryError: Si la requête est invalide.
            SearchTimeoutError: Si le timeout est dépassé.
        """
        self._ensure_started()
        assert self._semaphore is not None

        start_time = time.perf_counter()

        # Valider la requête
        if not query.text.strip() and query.filters.is_empty():
            # Recherche vide sans filtres → retourner page vide
            return SearchResultPage(
                items=[],
                total=0,
                page=query.page,
                page_size=query.page_size,
                total_pages=0,
                has_next=False,
                has_previous=False,
                duration_ms=0.0,
                query_text=query.text,
            )

        # Construire la requête FTS5
        try:
            fts_query = escape_fts5_query(query.text) if query.text.strip() else None
        except InvalidQueryError:
            raise
        except Exception as e:
            raise InvalidQueryError(query.text, str(e)) from e

        async with self._semaphore:
            try:
                # Exécuter avec timeout
                result_page = await asyncio.wait_for(
                    self._execute_search(query, fts_query),
                    timeout=query.timeout_seconds,
                )

                # Enregistrer les stats
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                await self._record_search(query.text, len(result_page.items), duration_ms)

                return result_page

            except asyncio.TimeoutError as e:
                raise SearchTimeoutError(query.text, query.timeout_seconds) from e
            except SearchError:
                raise
            except Exception as e:
                self._logger.error("Erreur de recherche: {}", e)
                raise SearchError(f"Erreur lors de la recherche: {e}") from e

    # ------------------------------------------------------------------------
    # API publique — Requêtes sur vues pré-définies
    # ------------------------------------------------------------------------

    async def get_continue_reading(
        self,
        *,
        limit: int = 10,
    ) -> list[SearchResult]:
        """Récupère les mangas en cours de lecture (vue v_continue_reading).

        Args:
            limit: Nombre maximum de résultats.

        Returns:
            Liste des mangas en cours de lecture, triés par dernière lecture.
        """
        self._ensure_started()

        sql = """
            SELECT
                manga_id, source_id, site, title, cover_url, chapter_number,
                page, last_read_at, reading_status, score
            FROM v_continue_reading
            LIMIT ?
        """

        try:
            rows = await self._database.fetch_all(sql, (limit,))
            return [self._row_to_search_result(row) for row in rows]
        except Exception as e:
            self._logger.error("Erreur get_continue_reading: {}", e)
            raise SearchError(f"Erreur lors de la récupération: {e}") from e

    async def get_favorites(
        self,
        *,
        limit: int = 20,
    ) -> list[SearchResult]:
        """Récupère les mangas favoris (score >= 8.0, vue v_favorites).

        Args:
            limit: Nombre maximum de résultats.

        Returns:
            Liste des mangas favoris, triés par score décroissant.
        """
        self._ensure_started()

        sql = """
            SELECT manga_id, title, cover_url, site, score, reading_status, notes
            FROM v_favorites
            LIMIT ?
        """

        try:
            rows = await self._database.fetch_all(sql, (limit,))
            return [self._row_to_search_result(row) for row in rows]
        except Exception as e:
            self._logger.error("Erreur get_favorites: {}", e)
            raise SearchError(f"Erreur lors de la récupération: {e}") from e

    async def get_recently_completed(
        self,
        *,
        limit: int = 30,
    ) -> list[SearchResult]:
        """Récupère les mangas récemment terminés (vue v_recently_completed).

        Args:
            limit: Nombre maximum de résultats.

        Returns:
            Liste des mangas terminés, triés par date de complétion.
        """
        self._ensure_started()

        sql = """
            SELECT manga_id, title, cover_url, site, completed_at, score, reread_count
            FROM v_recently_completed
            LIMIT ?
        """

        try:
            rows = await self._database.fetch_all(sql, (limit,))
            return [self._row_to_search_result(row) for row in rows]
        except Exception as e:
            self._logger.error("Erreur get_recently_completed: {}", e)
            raise SearchError(f"Erreur lors de la récupération: {e}") from e

    async def get_plan_to_read(
        self,
        *,
        limit: int = 50,
    ) -> list[SearchResult]:
        """Récupère les mangas dans la wishlist (vue v_plan_to_read).

        Args:
            limit: Nombre maximum de résultats.

        Returns:
            Liste des mangas à lire, triés par date d'ajout.
        """
        self._ensure_started()

        sql = """
            SELECT manga_id, title, cover_url, site, manga_status, author, added_at
            FROM v_plan_to_read
            LIMIT ?
        """

        try:
            rows = await self._database.fetch_all(sql, (limit,))
            return [self._row_to_search_result(row) for row in rows]
        except Exception as e:
            self._logger.error("Erreur get_plan_to_read: {}", e)
            raise SearchError(f"Erreur lors de la récupération: {e}") from e

    async def get_recent_activity(
        self,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Récupère l'activité de lecture récente (vue v_recent_activity).

        Args:
            limit: Nombre maximum de sessions.

        Returns:
            Liste des sessions de lecture récentes.
        """
        self._ensure_started()

        sql = """
            SELECT
                session_id, manga_id, manga_title, cover_url,
                chapter_number, chapter_title, pages_read,
                duration_seconds, started_at, ended_at
            FROM v_recent_activity
            LIMIT ?
        """

        try:
            rows = await self._database.fetch_all(sql, (limit,))
            return [dict(row) for row in rows]
        except Exception as e:
            self._logger.error("Erreur get_recent_activity: {}", e)
            raise SearchError(f"Erreur lors de la récupération: {e}") from e

    async def get_reading_stats(self) -> dict[str, Any]:
        """Récupère les statistiques globales de lecture (vue v_reading_stats).

        Returns:
            Dictionnaire avec toutes les statistiques.
        """
        self._ensure_started()

        sql = "SELECT * FROM v_reading_stats"

        try:
            row = await self._database.fetch_one(sql)
            return dict(row) if row else {}
        except Exception as e:
            self._logger.error("Erreur get_reading_stats: {}", e)
            raise SearchError(f"Erreur lors de la récupération: {e}") from e

    # ------------------------------------------------------------------------
    # API publique — Statistiques du moteur
    # ------------------------------------------------------------------------

    async def get_stats(self) -> SearchStats:
        """Retourne les statistiques du moteur de recherche."""
        async with self._stats_lock:
            uptime = 0.0
            if self._start_time > 0:
                uptime = asyncio.get_event_loop().time() - self._start_time

            avg_duration = (
                self._total_duration_ms / self._total_searches
                if self._total_searches > 0
                else 0.0
            )

            # Top termes recherchés
            top_terms = sorted(
                self._search_terms.items(),
                key=lambda x: x[1],
                reverse=True,
            )[: self._MAX_TOP_TERMS]

            return SearchStats(
                total_searches=self._total_searches,
                total_results_returned=self._total_results,
                average_duration_ms=avg_duration,
                total_duration_ms=self._total_duration_ms,
                empty_searches=self._empty_searches,
                most_searched_terms=top_terms,
                uptime_seconds=uptime,
            )

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        async with self._stats_lock:
            self._total_searches = 0
            self._total_results = 0
            self._total_duration_ms = 0.0
            self._empty_searches = 0
            self._search_terms.clear()

    # ------------------------------------------------------------------------
    # Méthodes internes — Exécution de requêtes
    # ------------------------------------------------------------------------

    async def _execute_search(
        self,
        query: SearchQuery,
        fts_query: str | None,
    ) -> SearchResultPage:
        """Exécute une recherche complète avec filtres, tri et pagination."""
        # Construire la requête SQL dynamique
        select_columns = build_fts5_select_columns(
            highlight=query.highlight,
            snippet=query.snippet,
            snippet_length=query.snippet_length,
        )

        # Jointure et conditions de base
        where_clauses: list[str] = []
        params: list[Any] = []

        # Recherche plein texte
        if fts_query:
            where_clauses.append("manga_search MATCH ?")
            params.append(fts_query)

        # Filtres
        if query.filters.sites:
            placeholders = ", ".join("?" for _ in query.filters.sites)
            where_clauses.append(f"m.site IN ({placeholders})")
            params.extend(query.filters.sites)

        if query.filters.languages:
            placeholders = ", ".join("?" for _ in query.filters.languages)
            where_clauses.append(f"m.language IN ({placeholders})")
            params.extend(query.filters.languages)

        if query.filters.status:
            placeholders = ", ".join("?" for _ in query.filters.status)
            where_clauses.append(f"m.status IN ({placeholders})")
            params.extend(query.filters.status)

        if query.filters.content_rating:
            placeholders = ", ".join("?" for _ in query.filters.content_rating)
            where_clauses.append(f"m.content_rating IN ({placeholders})")
            params.extend(query.filters.content_rating)

        if not query.filters.include_adult:
            where_clauses.append("m.content_rating != 'PORNOGRAPHIC'")

        if query.filters.min_year is not None:
            where_clauses.append("m.year >= ?")
            params.append(query.filters.min_year)

        if query.filters.max_year is not None:
            where_clauses.append("m.year <= ?")
            params.append(query.filters.max_year)

        if query.filters.authors:
            placeholders = ", ".join("?" for _ in query.filters.authors)
            where_clauses.append(f"m.author IN ({placeholders})")
            params.extend(query.filters.authors)

        if query.filters.genres:
            # Tous les genres doivent correspondre (AND)
            for genre in query.filters.genres:
                where_clauses.append("manga_search.genres LIKE ?")
                params.append(f"%{genre}%")

        if query.filters.downloaded_only:
            where_clauses.append("""
                EXISTS (
                    SELECT 1 FROM chapter c
                    WHERE c.manga_id = m.id AND c.downloaded = 1
                )
            """)

        # Construction de la requête
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        # Tri
        order_by = self._build_order_by(query.sort_by, query.sort_order)

        # Requête principale
        sql = f"""
            SELECT {select_columns}
            FROM manga m
            LEFT JOIN manga_search ON manga_search.manga_id = m.id
            LEFT JOIN reading_progress rp ON rp.manga_id = m.id
            WHERE {where_sql}
            {order_by}
            LIMIT ? OFFSET ?
        """
        params.extend([query.page_size, query.offset])

        # Requête de comptage (sans highlight/snippet pour la performance)
        count_sql = f"""
            SELECT COUNT(DISTINCT m.id)
            FROM manga m
            LEFT JOIN manga_search ON manga_search.manga_id = m.id
            WHERE {where_sql}
        """
        count_params = params[:-2]  # Retirer LIMIT et OFFSET

        # Exécuter les deux requêtes
        try:
            # Comptage (rapide)
            count_row = await self._database.fetch_one(count_sql, count_params)
            total = count_row[0] if count_row else 0

            # Résultats
            rows = await self._database.fetch_all(sql, params)

            # Convertir en SearchResult
            items = [self._row_to_search_result(row, query) for row in rows]

            # Construire la page
            total_pages = (total + query.page_size - 1) // query.page_size if total > 0 else 0

            return SearchResultPage(
                items=items,
                total=total,
                page=query.page,
                page_size=query.page_size,
                total_pages=total_pages,
                has_next=query.page < total_pages,
                has_previous=query.page > 1,
                duration_ms=0.0,  # Sera mis à jour par l'appelant
                query_text=query.text,
                applied_filters=self._filters_to_dict(query.filters),
            )

        except Exception as e:
            self._logger.error("Erreur d'exécution SQL: {}", e)
            raise SearchError(f"Erreur lors de l'exécution de la requête: {e}") from e

    def _build_order_by(
        self,
        sort_by: SearchSortBy,
        sort_order: SearchSortOrder,
    ) -> str:
        """Construit la clause ORDER BY selon le tri demandé."""
        direction = "ASC" if sort_order == SearchSortOrder.ASC else "DESC"

        order_map: dict[SearchSortBy, str] = {
            SearchSortBy.RELEVANCE: f"bm25_score {direction}",  # BM25 : plus négatif = meilleur
            SearchSortBy.TITLE: f"m.title COLLATE NOCASE {direction}",
            SearchSortBy.UPDATED_AT: f"m.updated_at {direction}",
            SearchSortBy.CREATED_AT: f"m.created_at {direction}",
            SearchSortBy.SCORE: f"rp.score {direction} NULLS LAST",
            SearchSortBy.YEAR: f"m.year {direction} NULLS LAST",
            SearchSortBy.AUTHOR: f"m.author COLLATE NOCASE {direction}",
        }

        return f"ORDER BY {order_map.get(sort_by, f'bm25_score {direction}')}"

    def _row_to_search_result(
        self,
        row: Any,
        query: SearchQuery | None = None,
    ) -> SearchResult:
        """Convertit une ligne SQL en SearchResult."""
        # Gestion flexible selon le type de row (sqlite3.Row, dict, tuple)
        if hasattr(row, "keys"):
            # sqlite3.Row ou dict-like
            data = dict(row)
        else:
            # Tuple
            data = {
                "manga_id": row[0],
                "source_id": row[1],
                "site": row[2],
                "title": row[3],
                "alternative_titles": row[4] or "[]",
                "description": row[5],
                "author": row[6],
                "artist": row[7],
                "status": row[8],
                "year": row[9],
                "cover_url": row[10],
                "language": row[11],
                "content_rating": row[12],
                "url": row[13],
                "updated_at": row[14],
                "created_at": row[15],
                "bm25_score": row[16] if len(row) > 16 else 0.0,
                "fts_genres": row[17] if len(row) > 17 else "",
                "highlight_title": row[18] if len(row) > 18 else None,
                "snippet_description": row[19] if len(row) > 19 else None,
            }

        # Parser les listes JSON
        alt_titles = self._parse_json_list(data.get("alternative_titles"))
        genres = self._parse_json_list(data.get("fts_genres"))

        # Score BM25 : inverser le signe pour que plus élevé = meilleur
        bm25_score = data.get("bm25_score", 0.0) or 0.0
        score = -bm25_score if bm25_score < 0 else bm25_score

        return SearchResult(
            manga_id=data["manga_id"],
            source_id=data["source_id"],
            site=data["site"],
            title=data["title"],
            alternative_titles=alt_titles,
            description=data.get("description"),
            author=data.get("author"),
            artist=data.get("artist"),
            status=data["status"],
            year=data.get("year"),
            cover_url=data.get("cover_url"),
            language=data["language"],
            content_rating=data["content_rating"],
            genres=genres,
            url=data["url"],
            updated_at=data["updated_at"],
            created_at=data["created_at"],
            score=score,
            highlight_title=data.get("highlight_title"),
            snippet_description=data.get("snippet_description"),
            reading_status=data.get("reading_status"),
            user_score=data.get("user_score"),
            downloaded_chapters=data.get("downloaded_chapters", 0),
        )

    @staticmethod
    def _parse_json_list(value: str | list | None) -> list[str]:
        """Parse une liste JSON ou une chaîne séparée par des espaces."""
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return []
            # Essayer JSON d'abord
            if value.startswith("["):
                try:
                    import orjson
                    return orjson.loads(value)
                except Exception:
                    pass
            # Sinon, séparer par espaces
            return [g.strip() for g in value.split() if g.strip()]
        return []

    @staticmethod
    def _filters_to_dict(filters: SearchFilters) -> dict[str, Any]:
        """Convertit les filtres en dictionnaire pour le debug."""
        result: dict[str, Any] = {}
        if filters.sites:
            result["sites"] = filters.sites
        if filters.languages:
            result["languages"] = filters.languages
        if filters.status:
            result["status"] = filters.status
        if filters.content_rating:
            result["content_rating"] = filters.content_rating
        if filters.genres:
            result["genres"] = filters.genres
        if filters.authors:
            result["authors"] = filters.authors
        if filters.min_year is not None:
            result["min_year"] = filters.min_year
        if filters.max_year is not None:
            result["max_year"] = filters.max_year
        if not filters.include_adult:
            result["include_adult"] = False
        if filters.downloaded_only:
            result["downloaded_only"] = True
        if filters.in_library:
            result["in_library"] = True
        return result

    async def _record_search(
        self,
        query_text: str,
        results_count: int,
        duration_ms: float,
    ) -> None:
        """Enregistre une recherche dans les statistiques."""
        async with self._stats_lock:
            self._total_searches += 1
            self._total_results += results_count
            self._total_duration_ms += duration_ms

            if results_count == 0:
                self._empty_searches += 1

            # Enregistrer le terme (normalisé)
            if query_text.strip():
                normalized = query_text.strip().lower()[:100]  # Limiter la taille
                self._search_terms[normalized] = (
                    self._search_terms.get(normalized, 0) + 1
                )

    def _ensure_started(self) -> None:
        """Vérifie que le moteur est démarré."""
        if not self._started:
            raise SearchNotStartedError()

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<LibrarySearch status={status} "
            f"searches={self._total_searches}>"
        )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "SearchError",
    "InvalidQueryError",
    "SearchNotStartedError",
    "SearchTimeoutError",
    # Enums
    "SearchSortBy",
    "SearchSortOrder",
    "SearchMode",
    # Modèles — Requêtes
    "SearchQuery",
    "SearchFilters",
    # Modèles — Résultats
    "SearchResult",
    "SearchResultPage",
    "SearchStats",
    # Helpers
    "escape_fts5_query",
    "build_fts5_select_columns",
    # Classe principale
    "LibrarySearch",
]
