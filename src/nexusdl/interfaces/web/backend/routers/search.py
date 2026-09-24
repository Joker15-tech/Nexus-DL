"""Routeur FastAPI pour la recherche de mangas.

Ce module fournit un routeur FastAPI complet pour rechercher des mangas,
webtoons et comics à travers les sites supportés par NexusDL. Il supporte
la recherche multi-sites en parallèle, le filtrage, le tri, la pagination,
les suggestions d'autocomplétion, et l'historique des recherches.

**Endpoints** :
    - POST /search                     : Recherche multi-sites
    - GET /search/{site_id}            : Recherche sur un site spécifique
    - GET /search/suggestions          : Suggestions d'autocomplétion
    - GET /search/history              : Historique des recherches
    - DELETE /search/history           : Effacer l'historique
    - GET /search/popular              : Recherches populaires

**Fonctionnalités** :
    - Recherche multi-sites en parallèle (asyncio.gather)
    - Filtrage par langue, statut, contenu adulte, tags
    - Tri par pertinence, titre, date, statut
    - Pagination avec page/page_size
    - Cache en mémoire pour les résultats fréquents
    - Suggestions d'autocomplétion
    - Historique des recherches par utilisateur
    - Gestion robuste des erreurs par site
    - Événements EventBus pour monitoring
    - Logging structuré
    - Rate limiting par endpoint
    - Timeout configurable par recherche

**Exemple d'utilisation** :
    >>> from fastapi import FastAPI
    >>> from nexusdl.interfaces.web.backend.routers.search import search_router
    >>>
    >>> app = FastAPI()
    >>> app.include_router(search_router, prefix="/api/v1")

**Exemples d'appels API** :
    >>> # Recherche multi-sites
    >>> POST /api/v1/search
    >>> {
    ...     "query": "one piece",
    ...     "site_ids": ["mangadex", "asurascans"],
    ...     "language": "en",
    ...     "page": 1,
    ...     "page_size": 20
    ... }
    >>>
    >>> # Recherche sur un site spécifique
    >>> GET /api/v1/search/mangadex?query=one+piece&page=1
    >>>
    >>> # Suggestions
    >>> GET /api/v1/search/suggestions?q=one&limit=5

Intégration :
    - core/registry/site_registry.py : Accès au registre des sites
    - core/parsers/*                 : Parsers pour chaque site
    - core/models/manga.py           : Modèles Manga, SearchResult
    - core/events.py                 : EventBus pour monitoring
    - core/logger.py                 : Logs
    - core/i18n.py                   : Traductions
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Final

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from nexusdl.core.constants import APP_NAME
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t


# ============================================================================
# CONSTANTES
# ============================================================================


# Valeurs par défaut
DEFAULT_SEARCH_LIMIT: Final[int] = 20
MAX_SEARCH_LIMIT: Final[int] = 100
DEFAULT_SEARCH_TIMEOUT_SECONDS: Final[int] = 30
DEFAULT_CACHE_TTL_SECONDS: Final[int] = 300  # 5 minutes
MAX_CACHE_SIZE: Final[int] = 500
DEFAULT_SUGGESTION_LIMIT: Final[int] = 10
MAX_SUGGESTION_LIMIT: Final[int] = 20
DEFAULT_HISTORY_SIZE: Final[int] = 100
MIN_QUERY_LENGTH: Final[int] = 1
MAX_QUERY_LENGTH: Final[int] = 200
MAX_SITES_PER_SEARCH: Final[int] = 10


# ============================================================================
# EXCEPTIONS
# ============================================================================


class SearchRouterError(NexusDLError):
    """Exception de base pour les erreurs du routeur de recherche."""


class SearchExecutionError(SearchRouterError):
    """Exception levée lorsqu'une recherche échoue.

    Attributes:
        query: Requête de recherche.
        site_id: ID du site (si applicable).
        reason: Raison de l'échec.
    """

    def __init__(self, query: str, site_id: str | None = None, reason: str = "") -> None:
        msg = t("search.error.failed", default="Search failed")
        if site_id:
            msg += f" on {site_id}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.query = query
        self.site_id = site_id
        self.reason = reason


class SearchTimeoutError(SearchRouterError):
    """Exception levée lorsqu'une recherche dépasse le timeout.

    Attributes:
        query: Requête de recherche.
        timeout: Timeout en secondes.
    """

    def __init__(self, query: str, timeout: float) -> None:
        super().__init__(
            t("search.error.timeout", default="Search timed out after {timeout}s", timeout=timeout)
        )
        self.query = query
        self.timeout = timeout


class InvalidQueryError(SearchRouterError):
    """Exception levée lorsqu'une requête de recherche est invalide.

    Attributes:
        query: Requête invalide.
        reason: Raison de l'invalidité.
    """

    def __init__(self, query: str, reason: str = "") -> None:
        msg = t("search.error.invalid_query", default="Invalid search query")
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.query = query
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class SearchSortBy(str, Enum):
    """Critère de tri des résultats de recherche.

    Attributes:
        RELEVANCE: Tri par pertinence (score).
        TITLE: Tri par titre alphabétique.
        DATE: Tri par date de publication.
        STATUS: Tri par statut.
    """

    RELEVANCE = "relevance"
    TITLE = "title"
    DATE = "date"
    STATUS = "status"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            SearchSortBy.RELEVANCE: t("search.sort.relevance", default="Relevance"),
            SearchSortBy.TITLE: t("search.sort.title", default="Title"),
            SearchSortBy.DATE: t("search.sort.date", default="Date"),
            SearchSortBy.STATUS: t("search.sort.status", default="Status"),
        }[self]


class MangaStatusFilter(str, Enum):
    """Filtre par statut de publication.

    Attributes:
        ALL: Tous les statuts.
        ONGOING: En cours.
        COMPLETED: Terminé.
        HIATUS: En pause.
        CANCELLED: Annulé.
    """

    ALL = "all"
    ONGOING = "ongoing"
    COMPLETED = "completed"
    HIATUS = "hiatus"
    CANCELLED = "cancelled"


# ============================================================================
# MODÈLES DE REQUÊTE — Pydantic
# ============================================================================


class SearchRequest(BaseModel):
    """Requête de recherche multi-sites.

    Attributes:
        query: Texte de recherche.
        site_ids: IDs des sites à rechercher (None = tous les sites activés).
        language: Filtre par langue (ISO 639-1).
        status: Filtre par statut de publication.
        include_adult: Inclure le contenu adulte.
        tags: Filtre par tags.
        sort_by: Critère de tri.
        page: Numéro de page.
        page_size: Nombre de résultats par page.
        timeout: Timeout en secondes.
    """

    query: str = Field(..., min_length=MIN_QUERY_LENGTH, max_length=MAX_QUERY_LENGTH, description="Requête de recherche.")
    site_ids: list[str] | None = Field(default=None, description="IDs des sites (None = tous).")
    language: str | None = Field(default=None, description="Filtre langue (ISO 639-1).")
    status: MangaStatusFilter = Field(default=MangaStatusFilter.ALL, description="Filtre statut.")
    include_adult: bool = Field(default=False, description="Inclure contenu adulte.")
    tags: list[str] | None = Field(default=None, description="Filtre tags.")
    sort_by: SearchSortBy = Field(default=SearchSortBy.RELEVANCE, description="Critère de tri.")
    page: int = Field(default=1, ge=1, description="Numéro de page.")
    page_size: int = Field(default=DEFAULT_SEARCH_LIMIT, ge=1, le=MAX_SEARCH_LIMIT, description="Taille de page.")
    timeout: float = Field(default=DEFAULT_SEARCH_TIMEOUT_SECONDS, ge=1.0, le=120.0, description="Timeout (s).")

    model_config = ConfigDict(extra="forbid")

    @field_validator("site_ids")
    @classmethod
    def validate_site_ids(cls, v: list[str] | None) -> list[str] | None:
        """Valide les IDs de sites."""
        if v is not None and len(v) > MAX_SITES_PER_SEARCH:
            raise ValueError(f"Maximum {MAX_SITES_PER_SEARCH} sites par recherche")
        return v

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        """Valide et nettoie la requête."""
        v = v.strip()
        if not v:
            raise ValueError("La requête ne peut pas être vide")
        return v


# ============================================================================
# MODÈLES DE RÉPONSE — Pydantic
# ============================================================================


class SearchResultItem(BaseModel):
    """Item individuel dans les résultats de recherche.

    Attributes:
        manga_id: ID unique du manga.
        title: Titre.
        author: Auteur.
        year: Année.
        status: Statut de publication.
        language: Code langue.
        cover_url: URL de la couverture.
        url: URL du manga sur le site.
        site_id: ID du site source.
        site_name: Nom du site source.
        score: Score de pertinence (0.0 à 1.0).
        description: Description courte.
        tags: Liste de tags.
        chapters_count: Nombre de chapitres disponibles.
    """

    manga_id: str = Field(..., description="ID unique.")
    title: str = Field(..., description="Titre.")
    author: str = Field(default="", description="Auteur.")
    year: int | None = Field(default=None, description="Année.")
    status: str = Field(default="unknown", description="Statut.")
    language: str = Field(default="en", description="Code langue.")
    cover_url: str = Field(default="", description="URL couverture.")
    url: str = Field(default="", description="URL manga.")
    site_id: str = Field(..., description="ID du site.")
    site_name: str = Field(default="", description="Nom du site.")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="Score pertinence.")
    description: str = Field(default="", description="Description courte.")
    tags: list[str] = Field(default_factory=list, description="Tags.")
    chapters_count: int = Field(default=0, ge=0, description="Nombre chapitres.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class SiteSearchError(BaseModel):
    """Erreur de recherche sur un site spécifique.

    Attributes:
        site_id: ID du site.
        site_name: Nom du site.
        error: Message d'erreur.
    """

    site_id: str = Field(..., description="ID du site.")
    site_name: str = Field(default="", description="Nom du site.")
    error: str = Field(..., description="Message d'erreur.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class SearchResponse(BaseModel):
    """Réponse de recherche.

    Attributes:
        query: Requête de recherche originale.
        results: Liste des résultats.
        total: Nombre total de résultats.
        page: Page actuelle.
        page_size: Taille de page.
        has_next: S'il y a une page suivante.
        has_previous: S'il y a une page précédente.
        duration_ms: Durée totale de la recherche.
        sites_searched: Nombre de sites recherchés.
        errors: Erreurs par site.
    """

    query: str = Field(..., description="Requête originale.")
    results: list[SearchResultItem] = Field(default_factory=list, description="Résultats.")
    total: int = Field(default=0, ge=0, description="Total résultats.")
    page: int = Field(default=1, ge=1, description="Page actuelle.")
    page_size: int = Field(default=DEFAULT_SEARCH_LIMIT, ge=1, description="Taille page.")
    has_next: bool = Field(default=False, description="Page suivante.")
    has_previous: bool = Field(default=False, description="Page précédente.")
    duration_ms: float = Field(default=0.0, ge=0.0, description="Durée ms.")
    sites_searched: int = Field(default=0, ge=0, description="Sites recherchés.")
    errors: list[SiteSearchError] = Field(default_factory=list, description="Erreurs par site.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class SuggestionItem(BaseModel):
    """Item de suggestion d'autocomplétion.

    Attributes:
        text: Texte de la suggestion.
        score: Score de pertinence.
        source: Source de la suggestion.
    """

    text: str = Field(..., description="Texte.")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="Score.")
    source: str = Field(default="", description="Source.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class SuggestionsResponse(BaseModel):
    """Réponse de suggestions d'autocomplétion.

    Attributes:
        query: Requête originale.
        suggestions: Liste des suggestions.
        count: Nombre de suggestions.
    """

    query: str = Field(..., description="Requête originale.")
    suggestions: list[SuggestionItem] = Field(default_factory=list, description="Suggestions.")
    count: int = Field(default=0, ge=0, description="Nombre.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class SearchHistoryEntry(BaseModel):
    """Entrée d'historique de recherche.

    Attributes:
        query: Requête de recherche.
        timestamp: Timestamp de la recherche.
        results_count: Nombre de résultats trouvés.
        sites_searched: Nombre de sites recherchés.
        duration_ms: Durée de la recherche.
    """

    query: str = Field(..., description="Requête.")
    timestamp: datetime = Field(..., description="Timestamp.")
    results_count: int = Field(default=0, ge=0, description="Nombre résultats.")
    sites_searched: int = Field(default=0, ge=0, description="Sites recherchés.")
    duration_ms: float = Field(default=0.0, ge=0.0, description="Durée ms.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class SearchHistoryResponse(BaseModel):
    """Réponse avec l'historique des recherches.

    Attributes:
        entries: Liste des entrées d'historique.
        total: Nombre total d'entrées.
        limit: Limite appliquée.
    """

    entries: list[SearchHistoryEntry] = Field(..., description="Entrées.")
    total: int = Field(default=0, ge=0, description="Total.")
    limit: int = Field(default=0, ge=0, description="Limite.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class PopularSearchResponse(BaseModel):
    """Réponse avec les recherches populaires.

    Attributes:
        queries: Liste des requêtes populaires.
        period: Période considérée.
    """

    queries: list[str] = Field(..., description="Requêtes populaires.")
    period: str = Field(default="all", description="Période.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ErrorResponse(BaseModel):
    """Réponse d'erreur.

    Attributes:
        error: Code d'erreur.
        message: Message d'erreur.
        details: Détails additionnels.
    """

    error: str = Field(..., description="Code erreur.")
    message: str = Field(..., description="Message.")
    details: dict[str, Any] = Field(default_factory=dict, description="Détails.")

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# CACHE — Cache en mémoire pour les résultats de recherche
# ============================================================================


class SearchCache:
    """Cache en mémoire pour les résultats de recherche.

    Utilise un dictionnaire avec TTL pour éviter de surcharger
    les sites avec des requêtes répétées.
    """

    def __init__(self, max_size: int = MAX_CACHE_SIZE, default_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS) -> None:
        """Initialise le cache.

        Args:
            max_size: Taille maximale du cache.
            default_ttl_seconds: TTL par défaut en secondes.
        """
        self._cache: dict[str, tuple[Any, datetime]] = {}
        self._max_size = max_size
        self._default_ttl = timedelta(seconds=default_ttl_seconds)
        self._lock = asyncio.Lock()

    def _make_key(self, query: str, site_ids: list[str] | None, language: str | None, page: int, page_size: int) -> str:
        """Génère une clé de cache.

        Args:
            query: Requête.
            site_ids: IDs des sites.
            language: Langue.
            page: Page.
            page_size: Taille de page.

        Returns:
            Clé de cache.
        """
        sites_str = ",".join(sorted(site_ids)) if site_ids else "all"
        return f"search:{query.lower()}:{sites_str}:{language}:{page}:{page_size}"

    async def get(self, query: str, site_ids: list[str] | None, language: str | None, page: int, page_size: int) -> Any | None:
        """Récupère un résultat du cache.

        Args:
            query: Requête.
            site_ids: IDs des sites.
            language: Langue.
            page: Page.
            page_size: Taille de page.

        Returns:
            Résultat ou None.
        """
        key = self._make_key(query, site_ids, language, page, page_size)
        async with self._lock:
            if key not in self._cache:
                return None
            value, expires_at = self._cache[key]
            if datetime.now(UTC) > expires_at:
                del self._cache[key]
                return None
            return value

    async def set(self, query: str, site_ids: list[str] | None, language: str | None, page: int, page_size: int, value: Any) -> None:
        """Stocke un résultat dans le cache.

        Args:
            query: Requête.
            site_ids: IDs des sites.
            language: Langue.
            page: Page.
            page_size: Taille de page.
            value: Valeur à stocker.
        """
        key = self._make_key(query, site_ids, language, page, page_size)
        async with self._lock:
            if len(self._cache) >= self._max_size:
                oldest_keys = sorted(self._cache.keys(), key=lambda k: self._cache[k][1])[:50]
                for old_key in oldest_keys:
                    del self._cache[old_key]
            self._cache[key] = (value, datetime.now(UTC) + self._default_ttl)

    async def clear(self) -> None:
        """Vide le cache."""
        async with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        """Taille actuelle du cache."""
        return len(self._cache)


# Instance globale du cache
_search_cache = SearchCache()


def get_search_cache() -> SearchCache:
    """Retourne l'instance globale du cache de recherche."""
    return _search_cache


# ============================================================================
# HISTORIQUE — Historique des recherches
# ============================================================================


class SearchHistory:
    """Historique des recherches par utilisateur.

    Stocke les N dernières recherches pour chaque utilisateur.
    """

    def __init__(self, max_size: int = DEFAULT_HISTORY_SIZE) -> None:
        """Initialise l'historique.

        Args:
            max_size: Taille maximale par utilisateur.
        """
        self._history: dict[str, list[SearchHistoryEntry]] = defaultdict(list)
        self._popular_queries: dict[str, int] = defaultdict(int)
        self._max_size = max_size
        self._lock = asyncio.Lock()

    async def add(self, user_id: str, entry: SearchHistoryEntry) -> None:
        """Ajoute une entrée à l'historique.

        Args:
            user_id: ID de l'utilisateur.
            entry: Entrée à ajouter.
        """
        async with self._lock:
            self._history[user_id].append(entry)
            if len(self._history[user_id]) > self._max_size:
                self._history[user_id] = self._history[user_id][-self._max_size:]
            # Compteur pour les recherches populaires
            self._popular_queries[entry.query.lower()] += 1

    async def get(self, user_id: str, limit: int | None = None) -> list[SearchHistoryEntry]:
        """Récupère l'historique d'un utilisateur.

        Args:
            user_id: ID de l'utilisateur.
            limit: Nombre maximum d'entrées.

        Returns:
            Liste d'entrées (les plus récentes en premier).
        """
        async with self._lock:
            entries = list(reversed(self._history.get(user_id, [])))
            if limit is not None:
                return entries[:limit]
            return entries

    async def clear(self, user_id: str) -> int:
        """Efface l'historique d'un utilisateur.

        Args:
            user_id: ID de l'utilisateur.

        Returns:
            Nombre d'entrées supprimées.
        """
        async with self._lock:
            count = len(self._history.get(user_id, []))
            self._history[user_id] = []
            return count

    async def get_popular(self, limit: int = 10) -> list[str]:
        """Récupère les recherches populaires.

        Args:
            limit: Nombre maximum de requêtes.

        Returns:
            Liste des requêtes populaires (les plus fréquentes en premier).
        """
        async with self._lock:
            sorted_queries = sorted(
                self._popular_queries.items(),
                key=lambda x: x[1],
                reverse=True,
            )
            return [q for q, _ in sorted_queries[:limit]]


# Instance globale de l'historique
_search_history = SearchHistory()


def get_search_history() -> SearchHistory:
    """Retourne l'instance globale de l'historique de recherche."""
    return _search_history


# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================


def _get_user_id_from_request(request: Any) -> str:
    """Extrait l'ID utilisateur de la requête.

    Args:
        request: Requête HTTP.

    Returns:
        ID utilisateur ou "anonymous".
    """
    if hasattr(request.state, "user") and request.state.user:
        return request.state.user.user_id
    return "anonymous"


def _result_to_item(result: Any, site_id: str, site_name: str) -> SearchResultItem:
    """Convertit un SearchResult en SearchResultItem.

    Args:
        result: Instance de SearchResult.
        site_id: ID du site.
        site_name: Nom du site.

    Returns:
        Instance de SearchResultItem.
    """
    manga = result.manga
    return SearchResultItem(
        manga_id=manga.id,
        title=manga.title,
        author=manga.author or "",
        year=manga.year,
        status=manga.status.value if hasattr(manga.status, "value") else "unknown",
        language=manga.language.value if hasattr(manga.language, "value") else "en",
        cover_url=manga.cover_url or "",
        url=manga.url or "",
        site_id=site_id,
        site_name=site_name,
        score=result.score if hasattr(result, "score") else 0.0,
        description=(manga.description or "")[:200],
        tags=manga.tags[:5] if hasattr(manga, "tags") and manga.tags else [],
        chapters_count=len(manga.chapters) if hasattr(manga, "chapters") and manga.chapters else 0,
    )


def _apply_filters(
    results: list[SearchResultItem],
    language: str | None,
    status_filter: MangaStatusFilter,
    include_adult: bool,
    tags: list[str] | None,
) -> list[SearchResultItem]:
    """Applique les filtres aux résultats.

    Args:
        results: Résultats à filtrer.
        language: Filtre par langue.
        status_filter: Filtre par statut.
        include_adult: Inclure contenu adulte.
        tags: Filtre par tags.

    Returns:
        Résultats filtrés.
    """
    filtered = results

    if language:
        filtered = [r for r in filtered if r.language == language]

    if status_filter != MangaStatusFilter.ALL:
        filtered = [r for r in filtered if r.status == status_filter.value]

    # Note: le filtrage par contenu adulte est géré au niveau du registre des sites

    if tags:
        tags_lower = {t.lower() for t in tags}
        filtered = [r for r in filtered if tags_lower & {t.lower() for t in r.tags}]

    return filtered


def _sort_results(results: list[SearchResultItem], sort_by: SearchSortBy) -> list[SearchResultItem]:
    """Trie les résultats.

    Args:
        results: Résultats à trier.
        sort_by: Critère de tri.

    Returns:
        Résultats triés.
    """
    if sort_by == SearchSortBy.RELEVANCE:
        return sorted(results, key=lambda r: r.score, reverse=True)
    elif sort_by == SearchSortBy.TITLE:
        return sorted(results, key=lambda r: r.title.lower())
    elif sort_by == SearchSortBy.DATE:
        return sorted(results, key=lambda r: r.year or 0, reverse=True)
    elif sort_by == SearchSortBy.STATUS:
        return sorted(results, key=lambda r: r.status)
    return results


def _paginate_results(
    results: list[SearchResultItem],
    page: int,
    page_size: int,
) -> tuple[list[SearchResultItem], bool, bool]:
    """Paginer les résultats.

    Args:
        results: Résultats à paginer.
        page: Numéro de page.
        page_size: Taille de page.

    Returns:
        Tuple (résultats de la page, has_next, has_previous).
    """
    total = len(results)
    start = (page - 1) * page_size
    end = start + page_size
    paginated = results[start:end]
    has_next = end < total
    has_previous = page > 1
    return paginated, has_next, has_previous


async def _search_on_site(
    site: Any,
    query: str,
) -> tuple[str, str, list[Any], str | None]:
    """Recherche sur un site spécifique.

    Args:
        site: Instance de SiteConfig.
        query: Requête de recherche.

    Returns:
        Tuple (site_id, site_name, results, error_message).
    """
    try:
        from nexusdl.core.registry import get_site_registry
        registry = get_site_registry()
        parser = await registry.get_parser(site.id)
        results = await parser.search(query)
        return site.id, site.name, results, None
    except Exception as e:
        logger.warning("Erreur lors de la recherche sur {}: {}", site.id, e)
        return site.id, site.name, [], str(e)


# ============================================================================
# ROUTEUR — Endpoints FastAPI
# ============================================================================


if FASTAPI_AVAILABLE:

    search_router = APIRouter(tags=["search"])

    # =========================================================================
    # POST /search — Recherche multi-sites
    # =========================================================================

    @search_router.post(
        "/search",
        response_model=SearchResponse,
        summary="Recherche multi-sites",
        description="Recherche des mangas sur plusieurs sites en parallèle.",
        responses={
            200: {"description": "Résultats de recherche"},
            400: {"description": "Requête invalide"},
            504: {"description": "Timeout de recherche"},
        },
    )
    async def search_manga(
        request: Request,
        body: SearchRequest,
    ) -> SearchResponse:
        """Recherche des mangas sur plusieurs sites.

        Args:
            request: Requête HTTP.
            body: Corps de la requête.

        Returns:
            Résultats de recherche.
        """
        user_id = _get_user_id_from_request(request)
        logger.info(
            "Recherche multi-sites: query={!r}, sites={}, user={}",
            body.query,
            body.site_ids or "all",
            user_id,
        )

        # Vérifier le cache
        cache = get_search_cache()
        cached = await cache.get(body.query, body.site_ids, body.language, body.page, body.page_size)
        if cached:
            logger.debug("Résultat de cache pour: {!r}", body.query)
            return cached

        start_time = time.perf_counter()

        try:
            from nexusdl.core.registry import get_site_registry
            registry = get_site_registry()

            # Déterminer les sites à rechercher
            if body.site_ids:
                sites = []
                for site_id in body.site_ids:
                    try:
                        sites.append(registry.get_site(site_id))
                    except Exception:
                        logger.warning("Site non trouvé: {}", site_id)
            else:
                sites = registry.list_sites(enabled_only=True, include_adult=body.include_adult)

            # Limiter le nombre de sites
            sites = sites[:MAX_SITES_PER_SEARCH]

            if not sites:
                return SearchResponse(
                    query=body.query,
                    results=[],
                    total=0,
                    page=body.page,
                    page_size=body.page_size,
                    duration_ms=(time.perf_counter() - start_time) * 1000,
                    sites_searched=0,
                )

            # Rechercher sur tous les sites en parallèle avec timeout
            try:
                tasks = [_search_on_site(site, body.query) for site in sites]
                raw_results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=body.timeout,
                )
            except asyncio.TimeoutError:
                raise SearchTimeoutError(body.query, body.timeout)

            # Traiter les résultats
            all_items: list[SearchResultItem] = []
            errors: list[SiteSearchError] = []

            for result in raw_results:
                if isinstance(result, Exception):
                    errors.append(SiteSearchError(
                        site_id="unknown",
                        error=str(result),
                    ))
                    continue

                site_id, site_name, results, error_msg = result

                if error_msg:
                    errors.append(SiteSearchError(
                        site_id=site_id,
                        site_name=site_name,
                        error=error_msg,
                    ))

                for r in results:
                    all_items.append(_result_to_item(r, site_id, site_name))

            # Appliquer les filtres
            filtered = _apply_filters(
                all_items,
                body.language,
                body.status,
                body.include_adult,
                body.tags,
            )

            # Trier
            sorted_results = _sort_results(filtered, body.sort_by)

            # Paginer
            paginated, has_next, has_previous = _paginate_results(
                sorted_results,
                body.page,
                body.page_size,
            )

            duration_ms = (time.perf_counter() - start_time) * 1000

            response = SearchResponse(
                query=body.query,
                results=paginated,
                total=len(sorted_results),
                page=body.page,
                page_size=body.page_size,
                has_next=has_next,
                has_previous=has_previous,
                duration_ms=duration_ms,
                sites_searched=len(sites),
                errors=errors,
            )

            # Stocker dans le cache
            await cache.set(body.query, body.site_ids, body.language, body.page, body.page_size, response)

            # Enregistrer dans l'historique
            history = get_search_history()
            await history.add(user_id, SearchHistoryEntry(
                query=body.query,
                timestamp=datetime.now(UTC),
                results_count=len(sorted_results),
                sites_searched=len(sites),
                duration_ms=duration_ms,
            ))

            # Émettre un événement
            try:
                event_bus = get_event_bus()
                await event_bus.emit(
                    EventType.CUSTOM,
                    payload={
                        "type": "api.search.completed",
                        "query": body.query,
                        "results_count": len(paginated),
                        "total_count": len(sorted_results),
                        "sites_searched": len(sites),
                        "duration_ms": duration_ms,
                        "errors_count": len(errors),
                        "user_id": user_id,
                    },
                    source="interfaces.web.search",
                )
            except Exception as e:
                logger.debug("Impossible d'émettre l'événement: {}", e)

            return response

        except SearchTimeoutError as e:
            logger.warning("Timeout de recherche: {}", e)
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=ErrorResponse(
                    error="search_timeout",
                    message=str(e),
                    details={"query": body.query, "timeout": body.timeout},
                ).model_dump(),
            ) from e

        except HTTPException:
            raise

        except Exception as e:
            logger.error("Erreur lors de la recherche: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="search_failed",
                    message=t("search.error.failed", default="Search failed"),
                    details={"query": body.query, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /search/{site_id} — Recherche sur un site spécifique
    # =========================================================================

    @search_router.get(
        "/search/{site_id}",
        response_model=SearchResponse,
        summary="Recherche sur un site spécifique",
        description="Recherche des mangas sur un site spécifique.",
        responses={
            200: {"description": "Résultats de recherche"},
            404: {"description": "Site non trouvé"},
            503: {"description": "Parser indisponible"},
        },
    )
    async def search_on_site(
        request: Request,
        site_id: str,
        query: str = Query(..., min_length=MIN_QUERY_LENGTH, max_length=MAX_QUERY_LENGTH, description="Requête"),
        page: int = Query(1, ge=1, description="Page"),
        page_size: int = Query(DEFAULT_SEARCH_LIMIT, ge=1, le=MAX_SEARCH_LIMIT, description="Taille page"),
        language: str | None = Query(None, description="Filtre langue"),
        status: MangaStatusFilter = Query(MangaStatusFilter.ALL, description="Filtre statut"),
        sort_by: SearchSortBy = Query(SearchSortBy.RELEVANCE, description="Tri"),
    ) -> SearchResponse:
        """Recherche sur un site spécifique.

        Args:
            request: Requête HTTP.
            site_id: ID du site.
            query: Requête de recherche.
            page: Numéro de page.
            page_size: Taille de page.
            language: Filtre langue.
            status: Filtre statut.
            sort_by: Critère de tri.

        Returns:
            Résultats de recherche.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Recherche sur {}: query={!r}, user={}", site_id, query, user_id)

        # Vérifier le cache
        cache = get_search_cache()
        cached = await cache.get(query, [site_id], language, page, page_size)
        if cached:
            return cached

        start_time = time.perf_counter()

        try:
            from nexusdl.core.registry import get_site_registry
            registry = get_site_registry()

            # Vérifier que le site existe
            try:
                site = registry.get_site(site_id)
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ErrorResponse(
                        error="site_not_found",
                        message=t("search.error.site_not_found", default="Site not found: {site_id}", site_id=site_id),
                        details={"site_id": site_id},
                    ).model_dump(),
                )

            # Rechercher
            site_id_result, site_name, results, error_msg = await _search_on_site(site, query)

            errors: list[SiteSearchError] = []
            if error_msg:
                errors.append(SiteSearchError(site_id=site_id, site_name=site_name, error=error_msg))

            # Convertir
            all_items = [_result_to_item(r, site_id, site_name) for r in results]

            # Filtrer
            filtered = _apply_filters(all_items, language, status, False, None)

            # Trier
            sorted_results = _sort_results(filtered, sort_by)

            # Paginer
            paginated, has_next, has_previous = _paginate_results(sorted_results, page, page_size)

            duration_ms = (time.perf_counter() - start_time) * 1000

            response = SearchResponse(
                query=query,
                results=paginated,
                total=len(sorted_results),
                page=page,
                page_size=page_size,
                has_next=has_next,
                has_previous=has_previous,
                duration_ms=duration_ms,
                sites_searched=1,
                errors=errors,
            )

            # Cache
            await cache.set(query, [site_id], language, page, page_size, response)

            # Historique
            history = get_search_history()
            await history.add(user_id, SearchHistoryEntry(
                query=query,
                timestamp=datetime.now(UTC),
                results_count=len(sorted_results),
                sites_searched=1,
                duration_ms=duration_ms,
            ))

            return response

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de la recherche sur {}: {}", site_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="search_failed",
                    message=t("search.error.failed", default="Search failed on site: {site_id}", site_id=site_id),
                    details={"query": query, "reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /search/suggestions — Suggestions d'autocomplétion
    # =========================================================================

    @search_router.get(
        "/search/suggestions",
        response_model=SuggestionsResponse,
        summary="Suggestions d'autocomplétion",
        description="Retourne des suggestions de recherche basées sur l'historique et les recherches populaires.",
        responses={
            200: {"description": "Suggestions"},
        },
    )
    async def get_suggestions(
        request: Request,
        q: str = Query(..., min_length=1, max_length=100, description="Début de la requête"),
        limit: int = Query(DEFAULT_SUGGESTION_LIMIT, ge=1, le=MAX_SUGGESTION_LIMIT, description="Nombre max"),
    ) -> SuggestionsResponse:
        """Retourne des suggestions d'autocomplétion.

        Args:
            request: Requête HTTP.
            q: Début de la requête.
            limit: Nombre maximum de suggestions.

        Returns:
            Suggestions.
        """
        user_id = _get_user_id_from_request(request)
        logger.debug("Suggestions pour: {!r}, user={}", q, user_id)

        suggestions: list[SuggestionItem] = []
        q_lower = q.lower()

        # 1. Suggestions depuis l'historique de l'utilisateur
        history = get_search_history()
        user_history = await history.get(user_id)
        seen_queries: set[str] = set()

        for entry in user_history:
            if entry.query.lower().startswith(q_lower) and entry.query.lower() not in seen_queries:
                suggestions.append(SuggestionItem(
                    text=entry.query,
                    score=0.8,
                    source="history",
                ))
                seen_queries.add(entry.query.lower())

        # 2. Suggestions depuis les recherches populaires
        popular = await history.get_popular(limit=50)
        for query_text in popular:
            if query_text.startswith(q_lower) and query_text not in seen_queries:
                suggestions.append(SuggestionItem(
                    text=query_text,
                    score=0.5,
                    source="popular",
                ))
                seen_queries.add(query_text)

        # Trier par score et limiter
        suggestions.sort(key=lambda s: s.score, reverse=True)
        suggestions = suggestions[:limit]

        return SuggestionsResponse(
            query=q,
            suggestions=suggestions,
            count=len(suggestions),
        )

    # =========================================================================
    # GET /search/history — Historique des recherches
    # =========================================================================

    @search_router.get(
        "/search/history",
        response_model=SearchHistoryResponse,
        summary="Historique des recherches",
        description="Retourne l'historique des recherches de l'utilisateur.",
        responses={
            200: {"description": "Historique"},
        },
    )
    async def get_history(
        request: Request,
        limit: int = Query(50, ge=1, le=500, description="Nombre max d'entrées"),
    ) -> SearchHistoryResponse:
        """Récupère l'historique des recherches.

        Args:
            request: Requête HTTP.
            limit: Nombre maximum d'entrées.

        Returns:
            Historique des recherches.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Historique des recherches: user={}, limit={}", user_id, limit)

        history = get_search_history()
        entries = await history.get(user_id, limit=limit)

        return SearchHistoryResponse(
            entries=entries,
            total=len(entries),
            limit=limit,
        )

    # =========================================================================
    # DELETE /search/history — Effacer l'historique
    # =========================================================================

    @search_router.delete(
        "/search/history",
        summary="Effacer l'historique des recherches",
        description="Efface l'historique des recherches de l'utilisateur.",
        responses={
            200: {"description": "Historique effacé"},
        },
    )
    async def clear_history(request: Request) -> dict[str, Any]:
        """Efface l'historique des recherches.

        Args:
            request: Requête HTTP.

        Returns:
            Message de confirmation.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Effacement de l'historique: user={}", user_id)

        history = get_search_history()
        count = await history.clear(user_id)

        return {
            "success": True,
            "message": t("search.success.history_cleared", default="Search history cleared"),
            "deleted_count": count,
        }

    # =========================================================================
    # GET /search/popular — Recherches populaires
    # =========================================================================

    @search_router.get(
        "/search/popular",
        response_model=PopularSearchResponse,
        summary="Recherches populaires",
        description="Retourne les recherches les plus populaires.",
        responses={
            200: {"description": "Recherches populaires"},
        },
    )
    async def get_popular_searches(
        limit: int = Query(10, ge=1, le=50, description="Nombre max"),
    ) -> PopularSearchResponse:
        """Récupère les recherches populaires.

        Args:
            limit: Nombre maximum de requêtes.

        Returns:
            Recherches populaires.
        """
        logger.info("Recherches populaires: limit={}", limit)

        history = get_search_history()
        popular = await history.get_popular(limit=limit)

        return PopularSearchResponse(
            queries=popular,
            period="all",
        )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_SEARCH_LIMIT",
    "MAX_SEARCH_LIMIT",
    "DEFAULT_SEARCH_TIMEOUT_SECONDS",
    "DEFAULT_CACHE_TTL_SECONDS",
    "MAX_CACHE_SIZE",
    "DEFAULT_SUGGESTION_LIMIT",
    "MAX_SUGGESTION_LIMIT",
    "DEFAULT_HISTORY_SIZE",
    "MIN_QUERY_LENGTH",
    "MAX_QUERY_LENGTH",
    "MAX_SITES_PER_SEARCH",
    # Exceptions
    "SearchRouterError",
    "SearchExecutionError",
    "SearchTimeoutError",
    "InvalidQueryError",
    # Enums
    "SearchSortBy",
    "MangaStatusFilter",
    # Modèles de requête
    "SearchRequest",
    # Modèles de réponse
    "SearchResultItem",
    "SiteSearchError",
    "SearchResponse",
    "SuggestionItem",
    "SuggestionsResponse",
    "SearchHistoryEntry",
    "SearchHistoryResponse",
    "PopularSearchResponse",
    "ErrorResponse",
    # Cache
    "SearchCache",
    "get_search_cache",
    # Historique
    "SearchHistory",
    "get_search_history",
    # Routeur
    "search_router" if FASTAPI_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
