/**
 * Page de recherche pour NexusDL.
 *
 * Interface complète pour rechercher des mangas sur plusieurs sites avec :
 *   - Barre de recherche principale avec autocomplétion
 *   - Sélection multi-sites
 *   - Filtres avancés (langue, statut, contenu adulte)
 *   - Tri et pagination
 *   - Suggestions, historique, et recherches populaires
 *   - Affichage des résultats en grille
 *   - Skeleton loader
 *   - État vide et gestion d'erreurs
 *   - Événements WebSocket pour progression temps réel
 *   - Style cyberpunk néon cohérent
 *   - Accessibilité complète
 *   - Responsive
 *
 * URL : /search?q={query}&sites={site_ids}&lang={language}&status={status}
 *
 * @module app/search/page
 */

'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  AlertCircle,
  BookOpen,
  Check,
  ChevronLeft,
  ChevronRight,
  Clock,
  Filter,
  Globe,
  Info,
  Loader2,
  Search,
  Sparkles,
  TrendingUp,
  X,
  Zap,
} from 'lucide-react';
import { toast } from 'sonner';

import { cn } from '@/lib/utils';
import {
  useSearch,
  useSearchSuggestions,
  useSearchHistory,
  usePopularSearches,
  useSearchWebSocket,
} from '@/hooks/useSearch';

import { SearchBar } from '@/components/SearchBar';
import { SiteSelector } from '@/components/SiteSelector';
import { MangaCard, MangaCardSkeletonGrid, MangaCardEmpty } from '@/components/MangaCard';
import { ProgressBar } from '@/components/ProgressBar';

import type { MangaStatus, LanguageCode, SearchResultItem, SearchFilters } from '@/types/manga';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Options de tri disponibles.
 */
const SORT_OPTIONS: Array<{
  id: string;
  label: string;
}> = [
  { id: 'relevance', label: 'Relevance' },
  { id: 'date', label: 'Release Date' },
  { id: 'title', label: 'Title' },
  { id: 'chapters', label: 'Chapters Count' },
];

/**
 * Statuts de publication disponibles.
 */
const STATUS_OPTIONS: Array<{
  id: MangaStatus | 'all';
  label: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
}> = [
  { id: 'all', label: 'All Status', icon: BookOpen },
  { id: 'ongoing', label: 'Ongoing', icon: Zap },
  { id: 'completed', label: 'Completed', icon: Check },
  { id: 'hiatus', label: 'Hiatus', icon: Clock },
  { id: 'cancelled', label: 'Cancelled', icon: X },
];

/**
 * Langues disponibles.
 */
const LANGUAGE_OPTIONS: Array<{
  code: LanguageCode | 'all';
  label: string;
  flag: string;
}> = [
  { code: 'all', label: 'All Languages', flag: '🌍' },
  { code: 'en', label: 'English', flag: '🇬🇧' },
  { code: 'fr', label: 'Français', flag: '🇫🇷' },
  { code: 'es', label: 'Español', flag: '🇪🇸' },
  { code: 'de', label: 'Deutsch', flag: '🇩🇪' },
  { code: 'it', label: 'Italiano', flag: '🇮🇹' },
  { code: 'pt', label: 'Português', flag: '🇵🇹' },
  { code: 'ru', label: 'Русский', flag: '🇷🇺' },
  { code: 'ja', label: '日本語', flag: '🇯🇵' },
  { code: 'ko', label: '한국어', flag: '🇰🇷' },
  { code: 'zh', label: '中文', flag: '🇨🇳' },
];

// ============================================================================
// COMPOSANTS AUXILIAIRES
// ============================================================================

/**
 * Panneau de progression de recherche (WebSocket).
 */
function SearchProgressBar() {
  const { progress, isSearching, progressPercent } = useSearchWebSocket();

  if (!isSearching || !progress) {
    return null;
  }

  return (
    <div className="mb-4 p-4 rounded-xl border-2 border-cyan-500/30 bg-cyan-500/5">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Loader2 size={16} className="text-cyan-400 animate-spin" />
          <span className="text-sm font-mono font-bold text-cyan-400">
            Searching...
          </span>
        </div>
        <span className="text-xs font-mono text-text-muted">
          {progress.sites_searched} / {progress.total_sites} sites
        </span>
      </div>

      <ProgressBar
        value={progressPercent}
        variant="info"
        size="md"
        shape="pill"
        animated
        aria-label={`Search progress: ${progressPercent}%`}
      />

      <div className="mt-2 flex items-center justify-between text-xs font-mono text-text-dim">
        <span className="flex items-center gap-1">
          <Globe size={10} className="text-secondary" />
          {progress.current_site || 'Searching all sites'}
        </span>
        <span className="flex items-center gap-1">
          <BookOpen size={10} className="text-primary" />
          {progress.results_count} results found
        </span>
      </div>
    </div>
  );
}

/**
 * Panneau de filtres avancés.
 */
function SearchFiltersPanel({
  filters,
  setFilters,
  sortBy,
  setSortBy,
  sortOrder,
  toggleSortOrder,
  selectedSites,
  setSelectedSites,
}: {
  filters: SearchFilters;
  setFilters: (filters: Partial<SearchFilters>) => void;
  sortBy: string;
  setSortBy: (sort: string) => void;
  sortOrder: 'asc' | 'desc';
  toggleSortOrder: () => void;
  selectedSites: string[];
  setSelectedSites: (sites: string[]) => void;
}) {
  const [showAdvanced, setShowAdvanced] = useState(false);

  return (
    <div className="space-y-3">
      {/* Sélecteur de sites */}
      <div className="flex items-center gap-3">
        <label className="text-xs font-mono font-bold text-text-dim uppercase tracking-wider">
          Sources
        </label>
        <SiteSelector
          selectedSiteIds={selectedSites}
          onSelectionChange={setSelectedSites}
          maxSelection={10}
          showHealth
          showCapabilities
          size="sm"
        />
      </div>

      {/* Filtres rapides */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Langue */}
        <div className="flex items-center gap-2">
          <label className="text-xs font-mono font-bold text-text-dim uppercase tracking-wider">
            Language
          </label>
          <select
            value={filters.language || 'all'}
            onChange={(e) => setFilters({ language: e.target.value as LanguageCode | 'all' })}
            aria-label="Filter by language"
            className={cn(
              'h-9 px-3 rounded-lg text-xs font-mono',
              'bg-surface-alt border-2 border-border-dim',
              'text-text',
              'focus:outline-none focus:border-secondary',
              'transition-colors'
            )}
          >
            {LANGUAGE_OPTIONS.map((lang) => (
              <option key={lang.code} value={lang.code}>
                {lang.flag} {lang.label}
              </option>
            ))}
          </select>
        </div>

        {/* Statut */}
        <div className="flex items-center gap-2">
          <label className="text-xs font-mono font-bold text-text-dim uppercase tracking-wider">
            Status
          </label>
          <div className="flex gap-1">
            {STATUS_OPTIONS.map((option) => {
              const Icon = option.icon;
              const isActive = (filters.status || 'all') === option.id;
              return (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => setFilters({ status: option.id === 'all' ? undefined : option.id as MangaStatus })}
                  aria-pressed={isActive}
                  className={cn(
                    'inline-flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs font-mono',
                    'border-2 transition-all duration-150',
                    'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                    isActive
                      ? 'bg-primary-bg text-primary border-primary'
                      : 'bg-surface-alt text-text-muted border-border-dim hover:border-secondary hover:text-secondary'
                  )}
                >
                  <Icon size={12} />
                  <span>{option.label}</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Contenu adulte */}
        <label className="flex items-center gap-2 cursor-pointer group">
          <input
            type="checkbox"
            checked={filters.include_adult || false}
            onChange={(e) => setFilters({ include_adult: e.target.checked })}
            className="h-4 w-4 rounded border-border-dim bg-surface-alt text-primary focus:ring-primary focus:ring-offset-0 cursor-pointer"
          />
          <span className="text-xs font-mono text-text-muted group-hover:text-text transition-colors">
            Include 18+
          </span>
        </label>

        {/* Bouton filtres avancés */}
        <button
          type="button"
          onClick={() => setShowAdvanced(!showAdvanced)}
          aria-expanded={showAdvanced}
          className={cn(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-lg',
            'text-xs font-mono',
            'border-2 transition-all duration-200',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
            showAdvanced
              ? 'bg-primary-bg text-primary border-primary'
              : 'bg-surface-alt text-text-muted border-border-dim hover:border-secondary hover:text-secondary'
          )}
        >
          <Filter size={12} />
          <span>Advanced</span>
        </button>
      </div>

      {/* Filtres avancés */}
      {showAdvanced && (
        <div className="p-3 rounded-lg bg-surface-alt/50 border-2 border-border-dim animate-in fade-in slide-in-from-top-2 duration-200">
          <div className="flex flex-wrap items-center gap-3">
            {/* Tri */}
            <div className="flex items-center gap-2">
              <label className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider">
                Sort by
              </label>
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value)}
                aria-label="Sort by"
                className={cn(
                  'h-8 px-2 rounded text-xs font-mono',
                  'bg-background border border-border-dim',
                  'text-text',
                  'focus:outline-none focus:border-secondary'
                )}
              >
                {SORT_OPTIONS.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>

              <button
                type="button"
                onClick={toggleSortOrder}
                aria-label={`Sort ${sortOrder === 'asc' ? 'ascending' : 'descending'}`}
                className={cn(
                  'p-1.5 rounded',
                  'bg-background border border-border-dim',
                  'text-text-muted hover:text-secondary hover:border-secondary',
                  'transition-colors'
                )}
              >
                {sortOrder === 'asc' ? <ChevronRight size={14} className="rotate-90" /> : <ChevronRight size={14} className="-rotate-90" />}
              </button>
            </div>

            {/* Tags */}
            <div className="flex items-center gap-2">
              <label className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider">
                Tags
              </label>
              <input
                type="text"
                placeholder="action, adventure, romance..."
                className={cn(
                  'h-8 px-3 rounded text-xs font-mono',
                  'bg-background border border-border-dim',
                  'text-text placeholder:text-text-dim',
                  'focus:outline-none focus:border-secondary',
                  'w-64'
                )}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Panneau de suggestions et historique.
 */
function SearchSuggestionsPanel({
  query,
  onSuggestionClick,
}: {
  query: string;
  onSuggestionClick: (suggestion: string) => void;
}) {
  const { suggestions, isLoading: isLoadingSuggestions } = useSearchSuggestions(query, {
    debounceDelay: 300,
    limit: 8,
    enabled: query.length >= 2,
  });

  const { history, isLoading: isLoadingHistory } = useSearchHistory({
    limit: 5,
    autoLoad: true,
  });

  const { popular, isLoading: isLoadingPopular } = usePopularSearches({
    limit: 5,
    autoLoad: true,
  });

  const isLoading = isLoadingSuggestions || isLoadingHistory || isLoadingPopular;

  // Ne rien afficher si pas de données
  if (query.length < 2 && history.length === 0 && popular.length === 0) {
    return null;
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
      {/* Suggestions */}
      {query.length >= 2 && suggestions.length > 0 && (
        <div className="p-4 rounded-xl border-2 border-border-dim bg-surface">
          <h3 className="text-xs font-mono font-bold text-primary uppercase tracking-wider mb-3 flex items-center gap-2">
            <Sparkles size={14} />
            Suggestions
          </h3>
          <div className="space-y-1">
            {suggestions.map((suggestion, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => onSuggestionClick(suggestion.text)}
                className={cn(
                  'w-full flex items-center gap-2 px-3 py-2 rounded-lg text-left',
                  'text-sm font-mono text-text',
                  'hover:bg-surface-hover hover:text-secondary',
                  'transition-all duration-150',
                  'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary'
                )}
              >
                <Search size={12} className="text-text-dim flex-shrink-0" />
                <span className="flex-1 truncate">{suggestion.text}</span>
                {suggestion.source && (
                  <span className="text-[10px] font-mono text-text-dim">
                    {suggestion.source}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Historique */}
      {history.length > 0 && (
        <div className="p-4 rounded-xl border-2 border-border-dim bg-surface">
          <h3 className="text-xs font-mono font-bold text-secondary uppercase tracking-wider mb-3 flex items-center gap-2">
            <Clock size={14} />
            Recent Searches
          </h3>
          <div className="space-y-1">
            {history.map((entry, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => onSuggestionClick(entry.query)}
                className={cn(
                  'w-full flex items-center gap-2 px-3 py-2 rounded-lg text-left',
                  'text-sm font-mono text-text',
                  'hover:bg-surface-hover hover:text-secondary',
                  'transition-all duration-150',
                  'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary'
                )}
              >
                <Clock size={12} className="text-text-dim flex-shrink-0" />
                <span className="flex-1 truncate">{entry.query}</span>
                <span className="text-[10px] font-mono text-text-dim">
                  {entry.results_count} results
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Populaire */}
      {popular.length > 0 && (
        <div className="p-4 rounded-xl border-2 border-border-dim bg-surface">
          <h3 className="text-xs font-mono font-bold text-accent uppercase tracking-wider mb-3 flex items-center gap-2">
            <TrendingUp size={14} />
            Trending
          </h3>
          <div className="space-y-1">
            {popular.map((query, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => onSuggestionClick(query)}
                className={cn(
                  'w-full flex items-center gap-2 px-3 py-2 rounded-lg text-left',
                  'text-sm font-mono text-text',
                  'hover:bg-surface-hover hover:text-secondary',
                  'transition-all duration-150',
                  'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary'
                )}
              >
                <TrendingUp size={12} className="text-accent flex-shrink-0" />
                <span className="flex-1 truncate">{query}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="col-span-full flex items-center justify-center py-8">
          <Loader2 size={20} className="text-primary animate-spin" />
        </div>
      )}
    </div>
  );
}

/**
 * Panneau de résultats avec pagination.
 */
function SearchResults({
  results,
  total,
  page,
  pageSize,
  hasNext,
  hasPrevious,
  onPageChange,
  onMangaClick,
  isLoading,
}: {
  results: SearchResultItem[];
  total: number;
  page: number;
  pageSize: number;
  hasNext: boolean;
  hasPrevious: boolean;
  onPageChange: (page: number) => void;
  onMangaClick: (result: SearchResultItem) => void;
  isLoading: boolean;
}) {
  const totalPages = Math.ceil(total / pageSize);

  return (
    <div>
      {/* Grille de résultats */}
      {isLoading && results.length === 0 ? (
        <MangaCardSkeletonGrid variant="default" count={12} />
      ) : results.length === 0 ? (
        <MangaCardEmpty
          title="No results found"
          description="Try adjusting your search query or filters"
          icon={Search}
        />
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
          {results.map((result) => (
            <MangaCard
              key={result.manga_id}
              manga={{
                id: result.manga_id,
                site_id: result.site_id,
                title: result.title,
                author: result.author,
                year: result.year,
                language: result.language,
                status: result.status,
                cover_url: result.cover_url,
                url: result.url,
                description: result.description,
                tags: result.tags,
                chapters_count: result.chapters_count,
                last_updated_at: null,
                in_library: false,
                reading_status: null,
              }}
              variant="default"
              showStatus
              showLanguage
              showActions={false}
              onClick={() => onMangaClick(result)}
            />
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="mt-8 flex items-center justify-center gap-3">
          <button
            type="button"
            onClick={() => onPageChange(page - 1)}
            disabled={!hasPrevious || isLoading}
            aria-label="Previous page"
            className={cn(
              'flex items-center gap-2 px-4 py-2 rounded-lg',
              'bg-surface-alt text-text-muted border-2 border-border-dim',
              'font-mono text-sm',
              'hover:bg-surface-hover hover:border-secondary hover:text-secondary',
              'transition-all duration-200',
              'disabled:opacity-50 disabled:cursor-not-allowed'
            )}
          >
            <ChevronLeft size={16} />
            <span>Previous</span>
          </button>

          <div className="flex items-center gap-2">
            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
              let pageNum: number;
              if (totalPages <= 5) {
                pageNum = i + 1;
              } else if (page <= 3) {
                pageNum = i + 1;
              } else if (page >= totalPages - 2) {
                pageNum = totalPages - 4 + i;
              } else {
                pageNum = page - 2 + i;
              }

              return (
                <button
                  key={pageNum}
                  type="button"
                  onClick={() => onPageChange(pageNum)}
                  disabled={isLoading}
                  aria-label={`Page ${pageNum}`}
                  aria-current={page === pageNum ? 'page' : undefined}
                  className={cn(
                    'h-9 w-9 rounded-lg font-mono text-sm',
                    'border-2 transition-all duration-200',
                    'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                    page === pageNum
                      ? 'bg-primary-bg text-primary border-primary'
                      : 'bg-surface-alt text-text-muted border-border-dim hover:border-secondary hover:text-secondary',
                    isLoading && 'opacity-50 cursor-not-allowed'
                  )}
                >
                  {pageNum}
                </button>
              );
            })}
          </div>

          <button
            type="button"
            onClick={() => onPageChange(page + 1)}
            disabled={!hasNext || isLoading}
            aria-label="Next page"
            className={cn(
              'flex items-center gap-2 px-4 py-2 rounded-lg',
              'bg-surface-alt text-text-muted border-2 border-border-dim',
              'font-mono text-sm',
              'hover:bg-surface-hover hover:border-secondary hover:text-secondary',
              'transition-all duration-200',
              'disabled:opacity-50 disabled:cursor-not-allowed'
            )}
          >
            <span>Next</span>
            <ChevronRight size={16} />
          </button>
        </div>
      )}

      {/* Info de pagination */}
      {total > 0 && (
        <div className="mt-4 text-center text-xs font-mono text-text-dim">
          Showing {(page - 1) * pageSize + 1} to {Math.min(page * pageSize, total)} of{' '}
          <span className="text-text font-semibold">{total}</span> results
        </div>
      )}
    </div>
  );
}

// ============================================================================
// PAGE PRINCIPALE
// ============================================================================

/**
 * SearchPage - Page de recherche de mangas.
 *
 * @returns Élément JSX de la page
 */
export default function SearchPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // État local
  const [query, setQuery] = useState(searchParams.get('q') || '');
  const [selectedSites, setSelectedSites] = useState<string[]>(
    searchParams.get('sites')?.split(',').filter(Boolean) || []
  );
  const [filters, setFiltersState] = useState<SearchFilters>({
    language: (searchParams.get('lang') as LanguageCode) || undefined,
    status: (searchParams.get('status') as MangaStatus) || undefined,
    include_adult: searchParams.get('adult') === 'true',
  });
  const [sortBy, setSortBy] = useState(searchParams.get('sort') || 'relevance');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>(
    (searchParams.get('order') as 'asc' | 'desc') || 'desc'
  );

  // Hook de recherche
  const {
    results,
    isLoading,
    error,
    search,
    pagination,
    totalResults,
    lastSearchDuration,
    sitesSearched,
    siteErrors,
    hasNextPage,
    hasPreviousPage,
    nextPage,
    previousPage,
    goToPage,
  } = useSearch({
    defaultSiteIds: selectedSites,
    defaultFilters: filters,
    enableCache: true,
  });

  // Écouter les événements WebSocket
  useSearchWebSocket({
    onCompleted: (data) => {
      toast.success('Search completed', {
        description: `${data.results_count} results in ${(data.duration_ms / 1000).toFixed(1)}s`,
      });
    },
  });

  // Synchroniser avec l'URL
  useEffect(() => {
    const params = new URLSearchParams();
    if (query) params.set('q', query);
    if (selectedSites.length > 0) params.set('sites', selectedSites.join(','));
    if (filters.language) params.set('lang', filters.language);
    if (filters.status) params.set('status', filters.status);
    if (filters.include_adult) params.set('adult', 'true');
    if (sortBy !== 'relevance') params.set('sort', sortBy);
    if (sortOrder !== 'desc') params.set('order', sortOrder);

    const newUrl = params.toString() ? `/search?${params.toString()}` : '/search';
    router.replace(newUrl, { scroll: false });
  }, [query, selectedSites, filters, sortBy, sortOrder, router]);

  // Recherche automatique si query dans l'URL
  useEffect(() => {
    const initialQuery = searchParams.get('q');
    if (initialQuery && !query) {
      setQuery(initialQuery);
      handleSearch(initialQuery);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ==========================================================================
  // HANDLERS
  // ==========================================================================

  const handleSearch = useCallback(
    async (searchQuery: string) => {
      if (!searchQuery.trim()) return;

      setQuery(searchQuery);
      await search(searchQuery, {
        site_ids: selectedSites.length > 0 ? selectedSites : null,
        filters,
        sort_by: sortBy,
        sort_order: sortOrder,
        page: 1,
        page_size: 24,
      });
    },
    [selectedSites, filters, sortBy, sortOrder, search]
  );

  const handleFiltersChange = useCallback(
    (newFilters: Partial<SearchFilters>) => {
      setFiltersState((prev) => ({ ...prev, ...newFilters }));
      if (query) {
        handleSearch(query);
      }
    },
    [query, handleSearch]
  );

  const handleToggleSortOrder = useCallback(() => {
    setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'));
    if (query) {
      setTimeout(() => handleSearch(query), 0);
    }
  }, [query, handleSearch]);

  const handlePageChange = useCallback(
    (page: number) => {
      goToPage(page);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },
    [goToPage]
  );

  const handleSuggestionClick = useCallback(
    (suggestion: string) => {
      setQuery(suggestion);
      handleSearch(suggestion);
    },
    [handleSearch]
  );

  const handleMangaClick = useCallback(
    (result: SearchResultItem) => {
      router.push(`/manga/${result.manga_id}?site=${result.site_id}`);
    },
    [router]
  );

  // ==========================================================================
  // RENDU
  // ==========================================================================

  return (
    <div className="container mx-auto px-4 py-6 max-w-7xl">
      {/* Header */}
      <div className="mb-8 text-center">
        <h1 className="text-4xl font-mono font-bold text-primary tracking-wider mb-2">
          Search Manga
        </h1>
        <p className="text-sm font-mono text-text-muted">
          Find your favorite manga across multiple sources
        </p>
      </div>

      {/* Barre de recherche principale */}
      <div className="mb-6">
        <SearchBar
          variant="hero"
          size="lg"
          defaultQuery={query}
          onSearch={handleSearch}
          onQueryChange={setQuery}
          onSiteChange={setSelectedSites}
          onFilterChange={handleFiltersChange}
          defaultSites={selectedSites}
          defaultFilters={filters}
          showSiteSelector={false}
          showFilters={false}
          showHistory={false}
          showPopular={false}
          showSuggestions={false}
          placeholder="Search manga, webtoon, comics..."
          redirectToSearch={false}
        />
      </div>

      {/* Suggestions et historique */}
      {!query && (
        <SearchSuggestionsPanel query={query} onSuggestionClick={handleSuggestionClick} />
      )}

      {/* Filtres */}
      <div className="mb-6 p-4 rounded-xl border-2 border-border-dim bg-surface">
        <SearchFiltersPanel
          filters={filters}
          setFilters={handleFiltersChange}
          sortBy={sortBy}
          setSortBy={setSortBy}
          sortOrder={sortOrder}
          toggleSortOrder={handleToggleSortOrder}
          selectedSites={selectedSites}
          setSelectedSites={setSelectedSites}
        />
      </div>

      {/* Progression de recherche (WebSocket) */}
      <SearchProgressBar />

      {/* Erreur de recherche */}
      {error && (
        <div
          role="alert"
          className="mb-6 flex items-start gap-2 p-4 rounded-xl bg-red-500/10 border-2 border-red-500/30"
        >
          <AlertCircle size={20} className="text-red-400 flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-mono font-semibold text-red-400">Search Error</p>
            <p className="text-xs font-mono text-red-300/80 mt-1">{error}</p>
          </div>
          <button
            type="button"
            onClick={() => handleSearch(query)}
            className="text-xs font-mono text-red-400 hover:text-red-300 transition-colors"
          >
            Retry
          </button>
        </div>
      )}

      {/* Erreurs par site */}
      {siteErrors.length > 0 && (
        <div className="mb-6 p-4 rounded-xl bg-yellow-500/10 border-2 border-yellow-500/30">
          <div className="flex items-start gap-2">
            <Info size={16} className="text-yellow-400 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="text-xs font-mono font-semibold text-yellow-400 mb-2">
                {siteErrors.length} site(s) encountered errors
              </p>
              <div className="space-y-1">
                {siteErrors.map((err, idx) => (
                  <p key={idx} className="text-[10px] font-mono text-yellow-300/80">
                    • {err.site_id}: {err.error}
                  </p>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Résultats */}
      {query && (
        <SearchResults
          results={results}
          total={totalResults}
          page={pagination?.page || 1}
          pageSize={pagination?.page_size || 24}
          hasNext={hasNextPage}
          hasPrevious={hasPreviousPage}
          onPageChange={handlePageChange}
          onMangaClick={handleMangaClick}
          isLoading={isLoading}
        />
      )}

      {/* Footer avec infos */}
      {query && lastSearchDuration !== null && (
        <div className="mt-8 pt-6 border-t border-border-dim">
          <div className="flex items-center justify-between text-xs font-mono text-text-dim">
            <div className="flex items-center gap-4">
              <span>
                <span className="text-text font-semibold">{totalResults}</span> results
              </span>
              <span>
                <span className="text-text font-semibold">{sitesSearched}</span> sites searched
              </span>
              <span>
                in <span className="text-text font-semibold">{(lastSearchDuration / 1000).toFixed(2)}s</span>
              </span>
            </div>

            <div className="flex items-center gap-2">
              <Globe size={12} className="text-secondary" />
              <span>Multi-site search</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
