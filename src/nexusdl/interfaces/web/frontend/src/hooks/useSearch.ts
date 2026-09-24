/**
 * Hooks React pour la recherche de mangas dans NexusDL.
 *
 * Ce module fournit une collection de hooks React personnalisés pour effectuer
 * des recherches de mangas sur un ou plusieurs sites, avec autocomplétion,
 * historique, et gestion des filtres.
 *
 * Architecture :
 *   hooks/useSearch.ts
 *   ├── useSearch              : Hook principal (recherche multi-sites)
 *   ├── useSiteSearch          : Recherche sur un site spécifique
 *   ├── useSearchSuggestions   : Autocomplétion avec debounce
 *   ├── useSearchHistory       : Historique des recherches
 *   ├── usePopularSearches     : Recherches populaires
 *   ├── useSearchFilters       : Gestion des filtres
 *   └── useAdvancedSearch      : Recherche avancée avec tous les paramètres
 *
 * Utilisation :
 *   // Recherche simple
 *   const { results, isLoading, search } = useSearch();
 *   await search('one piece');
 *
 *   // Autocomplétion
 *   const { suggestions, isLoading } = useSearchSuggestions('one');
 *
 *   // Historique
 *   const { history, clearHistory } = useSearchHistory();
 *
 *   // Recherche avancée
 *   const { results, search } = useAdvancedSearch();
 *   await search({
 *     query: 'one piece',
 *     site_ids: ['mangadex', 'asurascans'],
 *     filters: { language: 'en', status: 'ongoing' },
 *     sort_by: 'relevance',
 *     page: 1,
 *     page_size: 20,
 *   });
 *
 * @module hooks/useSearch
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

import { api } from '@/lib/api';
import type { ApiError } from '@/lib/api';

import { debounce } from '@/lib/utils';

import type {
  Manga,
  SearchFilters,
  SearchRequest,
  SearchResultItem,
  MangaStatus,
  LanguageCode,
  PaginationMeta,
} from '@/types/manga';

import type { Site } from '@/types/site';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * État d'une recherche.
 */
export interface SearchState {
  /** Résultats de la recherche. */
  results: SearchResultItem[];
  /** Indique si une recherche est en cours. */
  isLoading: boolean;
  /** Dernier message d'erreur. */
  error: string | null;
  /** Requête de recherche actuelle. */
  currentQuery: string | null;
  /** Métadonnées de pagination. */
  pagination: PaginationMeta | null;
  /** Nombre total de résultats. */
  totalResults: number;
  /** Durée de la dernière recherche (ms). */
  lastSearchDuration: number | null;
  /** Sites recherchés lors de la dernière requête. */
  sitesSearched: number;
  /** Erreurs par site lors de la dernière recherche. */
  siteErrors: Array<{ site_id: string; error: string }>;
}

/**
 * Options pour le hook useSearch.
 */
export interface UseSearchOptions {
  /** Charger automatiquement au montage (avec query initial). */
  autoSearch?: boolean;
  /** Requête initiale. */
  initialQuery?: string;
  /** Sites par défaut à rechercher. */
  defaultSiteIds?: string[];
  /** Filtres par défaut. */
  defaultFilters?: SearchFilters;
  /** Activer le cache des résultats. */
  enableCache?: boolean;
  /** Durée de vie du cache (ms). */
  cacheTTL?: number;
}

/**
 * Retour du hook useSearch.
 */
export interface UseSearchReturn extends SearchState {
  /** Effectuer une recherche. */
  search: (query: string, options?: Partial<SearchRequest>) => Promise<void>;
  /** Annuler la recherche en cours. */
  cancel: () => void;
  /** Réinitialiser l'état. */
  reset: () => void;
  /** Charger la page suivante. */
  nextPage: () => Promise<void>;
  /** Charger la page précédente. */
  previousPage: () => Promise<void>;
  /** Aller à une page spécifique. */
  goToPage: (page: number) => Promise<void>;
  /** Indique s'il y a une page suivante. */
  hasNextPage: boolean;
  /** Indique s'il y a une page précédente. */
  hasPreviousPage: boolean;
}

/**
 * Options pour le hook useSearchSuggestions.
 */
export interface UseSearchSuggestionsOptions {
  /** Délai de debounce (ms). */
  debounceDelay?: number;
  /** Nombre maximum de suggestions. */
  limit?: number;
  /** Activer/désactiver. */
  enabled?: boolean;
}

/**
 * Retour du hook useSearchSuggestions.
 */
export interface UseSearchSuggestionsReturn {
  /** Liste des suggestions. */
  suggestions: Array<{ text: string; score: number; source: string }>;
  /** Indique si les suggestions sont en cours de chargement. */
  isLoading: boolean;
  /** Erreur éventuelle. */
  error: string | null;
}

/**
 * Entrée d'historique de recherche.
 */
export interface SearchHistoryEntry {
  /** Requête de recherche. */
  query: string;
  /** Timestamp de la recherche. */
  timestamp: string;
  /** Nombre de résultats trouvés. */
  results_count: number;
  /** Nombre de sites recherchés. */
  sites_searched: number;
  /** Durée de la recherche (ms). */
  duration_ms: number;
}

/**
 * Retour du hook useSearchHistory.
 */
export interface UseSearchHistoryReturn {
  /** Historique des recherches. */
  history: SearchHistoryEntry[];
  /** Indique si l'historique est en cours de chargement. */
  isLoading: boolean;
  /** Erreur éventuelle. */
  error: string | null;
  /** Effacer l'historique. */
  clearHistory: () => Promise<void>;
  /** Rafraîchir l'historique. */
  refresh: () => Promise<void>;
}

/**
 * Retour du hook usePopularSearches.
 */
export interface UsePopularSearchesReturn {
  /** Liste des recherches populaires. */
  popular: string[];
  /** Indique si les données sont en cours de chargement. */
  isLoading: boolean;
  /** Erreur éventuelle. */
  error: string | null;
  /** Rafraîchir les données. */
  refresh: () => Promise<void>;
}

/**
 * Options pour le hook useSearchFilters.
 */
export interface UseSearchFiltersOptions {
  /** Filtres initiaux. */
  initialFilters?: SearchFilters;
  /** Persister les filtres dans localStorage. */
  persist?: boolean;
  /** Clé de stockage pour la persistance. */
  storageKey?: string;
}

/**
 * Retour du hook useSearchFilters.
 */
export interface UseSearchFiltersReturn {
  /** Filtres actuels. */
  filters: SearchFilters;
  /** Mettre à jour les filtres. */
  setFilters: (filters: Partial<SearchFilters>) => void;
  /** Réinitialiser les filtres. */
  resetFilters: () => void;
  /** Indique si les filtres ont été modifiés. */
  isDirty: boolean;
}

/**
 * Options pour le hook useAdvancedSearch.
 */
export interface UseAdvancedSearchOptions extends UseSearchOptions {
  /** Tri par défaut. */
  defaultSortBy?: string;
  /** Ordre de tri par défaut. */
  defaultSortOrder?: 'asc' | 'desc';
  /** Taille de page par défaut. */
  defaultPageSize?: number;
  /** Timeout par défaut (s). */
  defaultTimeout?: number;
}

// ============================================================================
// CACHE DES RECHERCHES
// ============================================================================

/**
 * Cache simple pour les résultats de recherche.
 */
class SearchCache {
  private cache: Map<string, { data: SearchResultItem[]; timestamp: number }> = new Map();
  private ttl: number;

  constructor(ttl: number = 300000) {
    // 5 minutes par défaut
    this.ttl = ttl;
  }

  get(key: string): SearchResultItem[] | null {
    const entry = this.cache.get(key);
    if (!entry) return null;

    // Vérifier l'expiration
    if (Date.now() - entry.timestamp > this.ttl) {
      this.cache.delete(key);
      return null;
    }

    return entry.data;
  }

  set(key: string, data: SearchResultItem[]): void {
    this.cache.set(key, { data, timestamp: Date.now() });
  }

  clear(): void {
    this.cache.clear();
  }

  /**
   * Génère une clé de cache unique pour une requête.
   */
  static generateKey(request: SearchRequest): string {
    return JSON.stringify({
      q: request.query,
      s: request.site_ids?.sort() || 'all',
      f: request.filters,
      p: request.page,
      ps: request.page_size,
      sb: request.sort_by,
      so: request.sort_order,
    });
  }
}

// Instance globale du cache
const searchCache = new SearchCache();

// ============================================================================
// HOOK PRINCIPAL - useSearch
// ============================================================================

/**
 * Hook principal pour effectuer des recherches de mangas.
 *
 * Supporte la recherche multi-sites avec pagination, filtrage, et tri.
 * Gère automatiquement l'annulation des requêtes obsolètes et le cache.
 *
 * @param options - Options de configuration
 * @returns Objet avec l'état et les actions de recherche
 *
 * @example
 * ```tsx
 * function SearchPage() {
 *   const {
 *     results,
 *     isLoading,
 *     error,
 *     search,
 *     cancel,
 *     pagination,
 *     hasNextPage,
 *     nextPage,
 *   } = useSearch({
 *     defaultSiteIds: ['mangadex', 'asurascans'],
 *     enableCache: true,
 *   });
 *
 *   const handleSearch = async (query: string) => {
 *     await search(query);
 *   };
 *
 *   return (
 *     <div>
 *       <SearchBar onSearch={handleSearch} />
 *
 *       {isLoading && <LoadingSpinner />}
 *       {error && <ErrorMessage message={error} />}
 *
 *       <div className="results-grid">
 *         {results.map((result) => (
 *           <MangaCard key={result.manga_id} manga={result} />
 *         ))}
 *       </div>
 *
 *       {hasNextPage && (
 *         <button onClick={nextPage}>Load More</button>
 *       )}
 *     </div>
 *   );
 * }
 * ```
 */
export function useSearch(options: UseSearchOptions = {}): UseSearchReturn {
  const {
    autoSearch = false,
    initialQuery = '',
    defaultSiteIds,
    defaultFilters,
    enableCache = true,
    cacheTTL = 300000,
  } = options;

  // État
  const [state, setState] = useState<SearchState>({
    results: [],
    isLoading: false,
    error: null,
    currentQuery: null,
    pagination: null,
    totalResults: 0,
    lastSearchDuration: null,
    sitesSearched: 0,
    siteErrors: [],
  });

  // Ref pour l'AbortController
  const abortControllerRef = useRef<AbortController | null>(null);

  // Ref pour la requête actuelle (pour la pagination)
  const currentRequestRef = useRef<SearchRequest | null>(null);

  // Initialiser le cache avec le TTL configuré
  useEffect(() => {
    if (enableCache) {
      searchCache['ttl'] = cacheTTL;
    }
  }, [enableCache, cacheTTL]);

  // Recherche automatique au montage
  useEffect(() => {
    if (autoSearch && initialQuery) {
      search(initialQuery);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /**
   * Effectue une recherche.
   */
  const search = useCallback(
    async (query: string, searchOptions: Partial<SearchRequest> = {}) => {
      // Annuler la requête précédente si elle est encore en cours
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }

      // Créer un nouveau AbortController
      const abortController = new AbortController();
      abortControllerRef.current = abortController;

      // Construire la requête
      const request: SearchRequest = {
        query,
        site_ids: searchOptions.site_ids || defaultSiteIds || null,
        filters: searchOptions.filters || defaultFilters || {},
        sort_by: searchOptions.sort_by || 'relevance',
        sort_order: searchOptions.sort_order || 'desc',
        page: searchOptions.page || 1,
        page_size: searchOptions.page_size || 20,
        timeout: searchOptions.timeout || 30,
      };

      currentRequestRef.current = request;

      // Vérifier le cache
      const cacheKey = SearchCache.generateKey(request);
      if (enableCache) {
        const cached = searchCache.get(cacheKey);
        if (cached) {
          setState((prev) => ({
            ...prev,
            results: cached,
            currentQuery: query,
            isLoading: false,
            error: null,
          }));
          return;
        }
      }

      // Démarrer la recherche
      setState((prev) => ({
        ...prev,
        isLoading: true,
        error: null,
        currentQuery: query,
      }));

      const startTime = Date.now();

      try {
        const response = await api.post<{
          query: string;
          results: SearchResultItem[];
          total: number;
          page: number;
          page_size: number;
          has_next: boolean;
          has_previous: boolean;
          duration_ms: number;
          sites_searched: number;
          errors: Array<{ site_id: string; site_name: string; error: string }>;
        }>('/search', request, { signal: abortController.signal });

        // Vérifier si la requête a été annulée
        if (abortController.signal.aborted) {
          return;
        }

        // Mettre à jour le cache
        if (enableCache) {
          searchCache.set(cacheKey, response.results);
        }

        const duration = Date.now() - startTime;

        setState({
          results: response.results,
          isLoading: false,
          error: null,
          currentQuery: query,
          pagination: {
            page: response.page,
            page_size: response.page_size,
            total_items: response.total,
            total_pages: Math.ceil(response.total / response.page_size),
            has_next: response.has_next,
            has_previous: response.has_previous,
            next_page: response.has_next ? response.page + 1 : null,
            previous_page: response.has_previous ? response.page - 1 : null,
          },
          totalResults: response.total,
          lastSearchDuration: duration,
          sitesSearched: response.sites_searched,
          siteErrors: response.errors.map((e) => ({
            site_id: e.site_id,
            error: e.error,
          })),
        });

        // Afficher les erreurs de sites si nécessaire
        if (response.errors.length > 0) {
          toast.warning(
            `${response.errors.length} site(s) encountered errors during search`,
            {
              description: response.errors.map((e) => e.site_name || e.site_id).join(', '),
            }
          );
        }
      } catch (error) {
        // Ignorer les erreurs d'annulation
        if (error instanceof Error && error.name === 'AbortError') {
          return;
        }

        const message = error instanceof ApiError ? error.message : 'Search failed';
        console.error('[useSearch] Search error:', error);

        setState((prev) => ({
          ...prev,
          isLoading: false,
          error: message,
        }));

        toast.error('Search failed', { description: message });
      }
    },
    [defaultSiteIds, defaultFilters, enableCache]
  );

  /**
   * Annule la recherche en cours.
   */
  const cancel = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }

    setState((prev) => ({
      ...prev,
      isLoading: false,
    }));
  }, []);

  /**
   * Réinitialise l'état de recherche.
   */
  const reset = useCallback(() => {
    cancel();
    setState({
      results: [],
      isLoading: false,
      error: null,
      currentQuery: null,
      pagination: null,
      totalResults: 0,
      lastSearchDuration: null,
      sitesSearched: 0,
      siteErrors: [],
    });
    currentRequestRef.current = null;
  }, [cancel]);

  /**
   * Charge la page suivante.
   */
  const nextPage = useCallback(async () => {
    if (!state.pagination?.has_next || !currentRequestRef.current) return;

    const nextPageNum = state.pagination.page + 1;
    await search(currentRequestRef.current.query, {
      ...currentRequestRef.current,
      page: nextPageNum,
    });
  }, [state.pagination, search]);

  /**
   * Charge la page précédente.
   */
  const previousPage = useCallback(async () => {
    if (!state.pagination?.has_previous || !currentRequestRef.current) return;

    const prevPageNum = state.pagination.page - 1;
    await search(currentRequestRef.current.query, {
      ...currentRequestRef.current,
      page: prevPageNum,
    });
  }, [state.pagination, search]);

  /**
   * Va à une page spécifique.
   */
  const goToPage = useCallback(
    async (page: number) => {
      if (!currentRequestRef.current) return;

      await search(currentRequestRef.current.query, {
        ...currentRequestRef.current,
        page,
      });
    },
    [search]
  );

  // Cleanup à la destruction du composant
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, []);

  return {
    ...state,
    search,
    cancel,
    reset,
    nextPage,
    previousPage,
    goToPage,
    hasNextPage: state.pagination?.has_next || false,
    hasPreviousPage: state.pagination?.has_previous || false,
  };
}

// ============================================================================
// HOOK - useSiteSearch
// ============================================================================

/**
 * Hook pour effectuer une recherche sur un site spécifique.
 *
 * @param siteId - ID du site sur lequel rechercher
 * @param options - Options de configuration
 * @returns Objet avec l'état et les actions de recherche
 *
 * @example
 * ```tsx
 * function MangaDexSearch() {
 *   const { results, isLoading, search } = useSiteSearch('mangadex');
 *
 *   return (
 *     <div>
 *       <input onChange={(e) => search(e.target.value)} />
 *       {results.map((r) => <MangaCard key={r.manga_id} manga={r} />)}
 *     </div>
 *   );
 * }
 * ```
 */
export function useSiteSearch(
  siteId: string,
  options: { initialQuery?: string; autoSearch?: boolean } = {}
) {
  const { initialQuery = '', autoSearch = false } = options;

  const [results, setResults] = useState<SearchResultItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [hasNext, setHasNext] = useState(false);

  const abortControllerRef = useRef<AbortController | null>(null);

  const search = useCallback(
    async (query: string, pageNum: number = 1) => {
      if (!query.trim()) {
        setResults([]);
        setTotal(0);
        return;
      }

      // Annuler la requête précédente
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }

      const abortController = new AbortController();
      abortControllerRef.current = abortController;

      setIsLoading(true);
      setError(null);

      try {
        const response = await api.get<{
          query: string;
          results: SearchResultItem[];
          total: number;
          page: number;
          page_size: number;
          has_next: boolean;
        }>(`/search/${siteId}`, {
          params: { query, page: pageNum, page_size: 20 },
          signal: abortController.signal,
        });

        if (abortController.signal.aborted) return;

        setResults(response.results);
        setTotal(response.total);
        setPage(response.page);
        setHasNext(response.has_next);
        setIsLoading(false);
      } catch (error) {
        if (error instanceof Error && error.name === 'AbortError') return;

        const message = error instanceof ApiError ? error.message : 'Search failed';
        setError(message);
        setIsLoading(false);
        toast.error('Search failed', { description: message });
      }
    },
    [siteId]
  );

  // Recherche automatique au montage
  useEffect(() => {
    if (autoSearch && initialQuery) {
      search(initialQuery);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Cleanup
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, []);

  return {
    results,
    isLoading,
    error,
    total,
    page,
    hasNext,
    search,
    nextPage: () => search('', page + 1),
    reset: () => {
      setResults([]);
      setTotal(0);
      setPage(1);
      setError(null);
    },
  };
}

// ============================================================================
// HOOK - useSearchSuggestions
// ============================================================================

/**
 * Hook pour l'autocomplétion de recherche.
 *
 * Utilise un debounce pour éviter de surcharger l'API avec des requêtes
 * à chaque frappe.
 *
 * @param query - Requête partielle
 * @param options - Options de configuration
 * @returns Objet avec les suggestions et l'état de chargement
 *
 * @example
 * ```tsx
 * function SearchInput() {
 *   const [query, setQuery] = useState('');
 *   const { suggestions, isLoading } = useSearchSuggestions(query, {
 *     debounceDelay: 300,
 *     limit: 5,
 *   });
 *
 *   return (
 *     <div>
 *       <input
 *         value={query}
 *         onChange={(e) => setQuery(e.target.value)}
 *       />
 *       {isLoading && <Spinner />}
 *       <ul>
 *         {suggestions.map((s) => (
 *           <li key={s.text} onClick={() => setQuery(s.text)}>
 *             {s.text}
 *           </li>
 *         ))}
 *       </ul>
 *     </div>
 *   );
 * }
 * ```
 */
export function useSearchSuggestions(
  query: string,
  options: UseSearchSuggestionsOptions = {}
): UseSearchSuggestionsReturn {
  const { debounceDelay = 300, limit = 10, enabled = true } = options;

  const [suggestions, setSuggestions] = useState<Array<{ text: string; score: number; source: string }>>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);

  // Fonction de fetch avec debounce
  const fetchSuggestions = useMemo(
    () =>
      debounce(async (q: string) => {
        if (!q.trim() || q.length < 2 || !enabled) {
          setSuggestions([]);
          return;
        }

        // Annuler la requête précédente
        if (abortControllerRef.current) {
          abortControllerRef.current.abort();
        }

        const abortController = new AbortController();
        abortControllerRef.current = abortController;

        setIsLoading(true);
        setError(null);

        try {
          const response = await api.get<{
            query: string;
            suggestions: Array<{ text: string; score: number; source: string }>;
            count: number;
          }>('/search/suggestions', {
            params: { q, limit },
            signal: abortController.signal,
          });

          if (abortController.signal.aborted) return;

          setSuggestions(response.suggestions);
          setIsLoading(false);
        } catch (error) {
          if (error instanceof Error && error.name === 'AbortError') return;

          const message = error instanceof ApiError ? error.message : 'Failed to fetch suggestions';
          setError(message);
          setIsLoading(false);
        }
      }, debounceDelay),
    [debounceDelay, limit, enabled]
  );

  // Déclencher la recherche quand la query change
  useEffect(() => {
    fetchSuggestions(query);
  }, [query, fetchSuggestions]);

  // Cleanup
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      fetchSuggestions.cancel?.();
    };
  }, [fetchSuggestions]);

  return {
    suggestions,
    isLoading,
    error,
  };
}

// ============================================================================
// HOOK - useSearchHistory
// ============================================================================

/**
 * Hook pour accéder à l'historique des recherches.
 *
 * @param options - Options de configuration
 * @returns Objet avec l'historique et les actions
 *
 * @example
 * ```tsx
 * function SearchHistory() {
 *   const { history, isLoading, clearHistory } = useSearchHistory();
 *
 *   return (
 *     <div>
 *       <h2>Recent Searches</h2>
 *       {history.map((entry, idx) => (
 *         <div key={idx}>
 *           <span>{entry.query}</span>
 *           <span>{entry.results_count} results</span>
 *         </div>
 *       ))}
 *       <button onClick={clearHistory}>Clear History</button>
 *     </div>
 *   );
 * }
 * ```
 */
export function useSearchHistory(options: { limit?: number; autoLoad?: boolean } = {}): UseSearchHistoryReturn {
  const { limit = 50, autoLoad = true } = options;

  const [history, setHistory] = useState<SearchHistoryEntry[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchHistory = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await api.get<{
        entries: SearchHistoryEntry[];
        total: number;
        limit: number;
      }>('/search/history', {
        params: { limit },
      });

      setHistory(response.entries);
      setIsLoading(false);
    } catch (error) {
      const message = error instanceof ApiError ? error.message : 'Failed to fetch history';
      setError(message);
      setIsLoading(false);
    }
  }, [limit]);

  const clearHistory = useCallback(async () => {
    try {
      await api.delete('/search/history');
      setHistory([]);
      toast.success('Search history cleared');
    } catch (error) {
      const message = error instanceof ApiError ? error.message : 'Failed to clear history';
      toast.error('Failed to clear history', { description: message });
    }
  }, []);

  // Chargement automatique
  useEffect(() => {
    if (autoLoad) {
      fetchHistory();
    }
  }, [autoLoad, fetchHistory]);

  return {
    history,
    isLoading,
    error,
    clearHistory,
    refresh: fetchHistory,
  };
}

// ============================================================================
// HOOK - usePopularSearches
// ============================================================================

/**
 * Hook pour accéder aux recherches populaires.
 *
 * @param options - Options de configuration
 * @returns Objet avec les recherches populaires
 *
 * @example
 * ```tsx
 * function PopularSearches() {
 *   const { popular, isLoading } = usePopularSearches({ limit: 10 });
 *
 *   if (isLoading) return <Spinner />;
 *
 *   return (
 *     <div>
 *       <h2>Popular Searches</h2>
 *       <ul>
 *         {popular.map((query, idx) => (
 *           <li key={idx}>{query}</li>
 *         ))}
 *       </ul>
 *     </div>
 *   );
 * }
 * ```
 */
export function usePopularSearches(options: { limit?: number; autoLoad?: boolean } = {}): UsePopularSearchesReturn {
  const { limit = 10, autoLoad = true } = options;

  const [popular, setPopular] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchPopular = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await api.get<{
        queries: string[];
        period: string;
      }>('/search/popular', {
        params: { limit },
      });

      setPopular(response.queries);
      setIsLoading(false);
    } catch (error) {
      const message = error instanceof ApiError ? error.message : 'Failed to fetch popular searches';
      setError(message);
      setIsLoading(false);
    }
  }, [limit]);

  // Chargement automatique
  useEffect(() => {
    if (autoLoad) {
      fetchPopular();
    }
  }, [autoLoad, fetchPopular]);

  return {
    popular,
    isLoading,
    error,
    refresh: fetchPopular,
  };
}

// ============================================================================
// HOOK - useSearchFilters
// ============================================================================

/**
 * Hook pour gérer les filtres de recherche.
 *
 * Supporte la persistance dans localStorage pour conserver les préférences
 * de l'utilisateur entre les sessions.
 *
 * @param options - Options de configuration
 * @returns Objet avec les filtres et les actions
 *
 * @example
 * ```tsx
 * function SearchFilters() {
 *   const { filters, setFilters, resetFilters, isDirty } = useSearchFilters({
 *     persist: true,
 *     storageKey: 'search-filters',
 *   });
 *
 *   return (
 *     <div>
 *       <select
 *         value={filters.language || ''}
 *         onChange={(e) => setFilters({ language: e.target.value as LanguageCode })}
 *       >
 *         <option value="">All Languages</option>
 *         <option value="en">English</option>
 *         <option value="fr">Français</option>
 *       </select>
 *
 *       <select
 *         value={filters.status || ''}
 *         onChange={(e) => setFilters({ status: e.target.value as MangaStatus })}
 *       >
 *         <option value="">All Status</option>
 *         <option value="ongoing">Ongoing</option>
 *         <option value="completed">Completed</option>
 *       </select>
 *
 *       {isDirty && <button onClick={resetFilters}>Reset Filters</button>}
 *     </div>
 *   );
 * }
 * ```
 */
export function useSearchFilters(options: UseSearchFiltersOptions = {}): UseSearchFiltersReturn {
  const {
    initialFilters = {},
    persist = false,
    storageKey = 'nexusdl-search-filters',
  } = options;

  // Charger les filtres depuis localStorage si persist est activé
  const loadFilters = useCallback((): SearchFilters => {
    if (!persist || typeof window === 'undefined') {
      return initialFilters;
    }

    try {
      const stored = localStorage.getItem(storageKey);
      if (stored) {
        return { ...initialFilters, ...JSON.parse(stored) };
      }
    } catch (error) {
      console.error('[useSearchFilters] Failed to load filters from storage:', error);
    }

    return initialFilters;
  }, [initialFilters, persist, storageKey]);

  const [filters, setFiltersState] = useState<SearchFilters>(loadFilters);
  const [isDirty, setIsDirty] = useState(false);

  // Sauvegarder dans localStorage quand les filtres changent
  useEffect(() => {
    if (persist && typeof window !== 'undefined') {
      try {
        localStorage.setItem(storageKey, JSON.stringify(filters));
      } catch (error) {
        console.error('[useSearchFilters] Failed to save filters to storage:', error);
      }
    }
  }, [filters, persist, storageKey]);

  const setFilters = useCallback((newFilters: Partial<SearchFilters>) => {
    setFiltersState((prev) => ({ ...prev, ...newFilters }));
    setIsDirty(true);
  }, []);

  const resetFilters = useCallback(() => {
    setFiltersState(initialFilters);
    setIsDirty(false);

    if (persist && typeof window !== 'undefined') {
      try {
        localStorage.removeItem(storageKey);
      } catch (error) {
        console.error('[useSearchFilters] Failed to clear filters from storage:', error);
      }
    }
  }, [initialFilters, persist, storageKey]);

  return {
    filters,
    setFilters,
    resetFilters,
    isDirty,
  };
}

// ============================================================================
// HOOK - useAdvancedSearch
// ============================================================================

/**
 * Hook pour une recherche avancée avec tous les paramètres configurables.
 *
 * Combine useSearch, useSearchFilters, et ajoute des fonctionnalités
 * supplémentaires comme le tri et la taille de page configurables.
 *
 * @param options - Options de configuration
 * @returns Objet avec l'état complet et les actions
 *
 * @example
 * ```tsx
 * function AdvancedSearchPage() {
 *   const {
 *     results,
 *     isLoading,
 *     filters,
 *     setFilters,
 *     search,
 *     pagination,
 *     sortBy,
 *     setSortBy,
 *   } = useAdvancedSearch({
 *     defaultPageSize: 30,
 *     defaultSortBy: 'relevance',
 *     enableCache: true,
 *   });
 *
 *   return (
 *     <div>
 *       <SearchBar onSearch={search} />
 *       <SearchFilters filters={filters} onChange={setFilters} />
 *       <SortSelect value={sortBy} onChange={setSortBy} />
 *
 *       {results.map((r) => <MangaCard key={r.manga_id} manga={r} />)}
 *
 *       <Pagination
 *         page={pagination?.page || 1}
 *         totalPages={pagination?.total_pages || 1}
 *       />
 *     </div>
 *   );
 * }
 * ```
 */
export function useAdvancedSearch(options: UseAdvancedSearchOptions = {}) {
  const {
    defaultSortBy = 'relevance',
    defaultSortOrder = 'desc',
    defaultPageSize = 20,
    defaultTimeout = 30,
    ...searchOptions
  } = options;

  // Hooks internes
  const searchHook = useSearch(searchOptions);
  const filtersHook = useSearchFilters({
    initialFilters: searchOptions.defaultFilters,
    persist: true,
  });

  // État local pour le tri et la taille de page
  const [sortBy, setSortBy] = useState(defaultSortBy);
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>(defaultSortOrder);
  const [pageSize, setPageSize] = useState(defaultPageSize);

  // Fonction de recherche étendue
  const search = useCallback(
    async (query: string, customOptions: Partial<SearchRequest> = {}) => {
      await searchHook.search(query, {
        site_ids: customOptions.site_ids || searchOptions.defaultSiteIds,
        filters: customOptions.filters || filtersHook.filters,
        sort_by: customOptions.sort_by || sortBy,
        sort_order: customOptions.sort_order || sortOrder,
        page_size: customOptions.page_size || pageSize,
        timeout: customOptions.timeout || defaultTimeout,
        ...customOptions,
      });
    },
    [searchHook, filtersHook.filters, sortBy, sortOrder, pageSize, defaultTimeout, searchOptions.defaultSiteIds]
  );

  return {
    // État de recherche
    results: searchHook.results,
    isLoading: searchHook.isLoading,
    error: searchHook.error,
    currentQuery: searchHook.currentQuery,
    pagination: searchHook.pagination,
    totalResults: searchHook.totalResults,
    lastSearchDuration: searchHook.lastSearchDuration,
    sitesSearched: searchHook.sitesSearched,
    siteErrors: searchHook.siteErrors,

    // Actions de recherche
    search,
    cancel: searchHook.cancel,
    reset: searchHook.reset,
    nextPage: searchHook.nextPage,
    previousPage: searchHook.previousPage,
    goToPage: searchHook.goToPage,
    hasNextPage: searchHook.hasNextPage,
    hasPreviousPage: searchHook.hasPreviousPage,

    // Filtres
    filters: filtersHook.filters,
    setFilters: filtersHook.setFilters,
    resetFilters: filtersHook.resetFilters,
    isFiltersDirty: filtersHook.isDirty,

    // Tri
    sortBy,
    setSortBy,
    sortOrder,
    setSortOrder,

    // Pagination
    pageSize,
    setPageSize,
  };
}

// ============================================================================
// HOOK - useSearchWithContext
// ============================================================================

/**
 * Hook pour une recherche avec contexte (sites sélectionnés, filtres actifs, etc.).
 *
 * Utile pour maintenir un état de recherche cohérent entre plusieurs composants.
 *
 * @param context - Contexte de recherche initial
 * @returns Objet avec l'état et les actions
 *
 * @example
 * ```tsx
 * function SearchContext() {
 *   const { query, setQuery, selectedSites, toggleSite, search } = useSearchWithContext({
 *     initialQuery: '',
 *     initialSites: ['mangadex'],
 *   });
 *
 *   return (
 *     <div>
 *       <input value={query} onChange={(e) => setQuery(e.target.value)} />
 *       <SiteSelector selected={selectedSites} onToggle={toggleSite} />
 *       <button onClick={() => search()}>Search</button>
 *     </div>
 *   );
 * }
 * ```
 */
export function useSearchWithContext(context: {
  initialQuery?: string;
  initialSites?: string[];
  initialFilters?: SearchFilters;
} = {}) {
  const { initialQuery = '', initialSites = [], initialFilters = {} } = context;

  const [query, setQuery] = useState(initialQuery);
  const [selectedSites, setSelectedSites] = useState<string[]>(initialSites);
  const [filters, setFilters] = useState<SearchFilters>(initialFilters);

  const searchHook = useSearch({
    defaultSiteIds: selectedSites,
    defaultFilters: filters,
  });

  const toggleSite = useCallback((siteId: string) => {
    setSelectedSites((prev) =>
      prev.includes(siteId) ? prev.filter((id) => id !== siteId) : [...prev, siteId]
    );
  }, []);

  const search = useCallback(async () => {
    if (!query.trim()) return;
    await searchHook.search(query, {
      site_ids: selectedSites.length > 0 ? selectedSites : null,
      filters,
    });
  }, [query, selectedSites, filters, searchHook]);

  return {
    query,
    setQuery,
    selectedSites,
    setSelectedSites,
    toggleSite,
    filters,
    setFilters,
    search,
    // État de recherche
    results: searchHook.results,
    isLoading: searchHook.isLoading,
    error: searchHook.error,
    pagination: searchHook.pagination,
    totalResults: searchHook.totalResults,
  };
}

// ============================================================================
// EXPORTS
// ============================================================================

export type {
  SearchState,
  UseSearchOptions,
  UseSearchReturn,
  UseSearchSuggestionsOptions,
  UseSearchSuggestionsReturn,
  SearchHistoryEntry,
  UseSearchHistoryReturn,
  UsePopularSearchesReturn,
  UseSearchFiltersOptions,
  UseSearchFiltersReturn,
  UseAdvancedSearchOptions,
};
