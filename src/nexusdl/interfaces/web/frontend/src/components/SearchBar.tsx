/**
 * Composant SearchBar pour NexusDL.
 *
 * Barre de recherche principale de l'application avec :
 *   - Champ de recherche avec autocomplétion temps réel
 *   - Intégration avec le hook useSearchSuggestions (debounce 300ms)
 *   - Intégration avec le hook useSearchHistory (recherches récentes)
 *   - Intégration avec le hook usePopularSearches (trending)
 *   - Sélecteur de sites intégré (SiteSelector)
 *   - Raccourci clavier global Ctrl+K / ⌘K
 *   - Dropdown avec sections (suggestions, historique, populaire)
 *   - Navigation clavier complète dans le dropdown
 *   - État de chargement avec spinner animé
 *   - Bouton de réinitialisation
 *   - Filtres rapides (langue, statut)
 *   - Style cyberpunk néon cohérent
 *   - Accessibilité complète (ARIA, clavier, focus)
 *   - Responsive (mobile/desktop)
 *
 * Architecture :
 *   SearchBar (composant principal)
 *   ├── SearchInput (champ de saisie)
 *   ├── SearchDropdown (dropdown de résultats)
 *   │   ├── SuggestionsSection (autocomplétion)
 *   │   ├── HistorySection (recherches récentes)
 *   │   └── PopularSection (recherches populaires)
 *   ├── SearchFilters (filtres rapides)
 *   └── SiteSelector (sélecteur de sites)
 *
 * Utilisation :
 *   // Basique
 *   <SearchBar onSearch={(query) => console.log(query)} />
 *
 *   // Avec toutes les options
 *   <SearchBar
 *     onSearch={handleSearch}
 *     onSiteChange={handleSiteChange}
 *     defaultSites={['mangadex', 'asurascans']}
 *     placeholder="Search manga, webtoon, comics..."
 *     showFilters={true}
 *     showHistory={true}
 *     showPopular={true}
 *     autoFocus={false}
 *   />
 *
 * @module components/SearchBar
 */

'use client';

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';
import { useRouter } from 'next/navigation';
import {
  Clock,
  Filter,
  Loader2,
  Search,
  Sparkles,
  TrendingUp,
  X,
  ArrowRight,
  Globe,
  ChevronDown,
  BookOpen,
} from 'lucide-react';

import { cn } from '@/lib/utils';
import {
  useSearchSuggestions,
  useSearchHistory,
  usePopularSearches,
} from '@/hooks/useSearch';
import { SiteSelector } from '@/components/SiteSelector';
import type { SiteLanguage, MangaStatus } from '@/types/manga';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Props du composant SearchBar.
 */
export interface SearchBarProps {
  /** Callback appelé quand une recherche est soumise. */
  onSearch?: (query: string, options?: SearchBarOptions) => void;
  /** Callback appelé quand la query change (à chaque frappe). */
  onQueryChange?: (query: string) => void;
  /** Callback appelé quand les sites sélectionnés changent. */
  onSiteChange?: (siteIds: string[]) => void;
  /** Callback appelé quand les filtres changent. */
  onFilterChange?: (filters: SearchBarFilters) => void;
  /** Query initiale. */
  defaultQuery?: string;
  /** Sites sélectionnés par défaut. */
  defaultSites?: string[];
  /** Filtres par défaut. */
  defaultFilters?: SearchBarFilters;
  /** Placeholder du champ de recherche. */
  placeholder?: string;
  /** Afficher le sélecteur de sites. */
  showSiteSelector?: boolean;
  /** Afficher les filtres rapides. */
  showFilters?: boolean;
  /** Afficher l'historique des recherches. */
  showHistory?: boolean;
  /** Afficher les recherches populaires. */
  showPopular?: boolean;
  /** Afficher les suggestions d'autocomplétion. */
  showSuggestions?: boolean;
  /** Auto-focus au montage. */
  autoFocus?: boolean;
  /** Désactiver le composant. */
  disabled?: boolean;
  /** Taille du composant. */
  size?: 'sm' | 'md' | 'lg';
  /** Variante visuelle. */
  variant?: 'default' | 'hero' | 'inline';
  /** Classe CSS additionnelle. */
  className?: string;
  /** Rediriger vers /search?q=... au lieu d'appeler onSearch. */
  redirectToSearch?: boolean;
  /** Nombre maximum de suggestions à afficher. */
  maxSuggestions?: number;
  /** Nombre maximum d'éléments d'historique. */
  maxHistory?: number;
  /** Délai de debounce pour les suggestions (ms). */
  debounceDelay?: number;
}

/**
 * Options de recherche transmises au callback.
 */
export interface SearchBarOptions {
  siteIds: string[];
  filters: SearchBarFilters;
}

/**
 * Filtres rapides de la barre de recherche.
 */
export interface SearchBarFilters {
  language: SiteLanguage | 'all';
  status: MangaStatus | 'all';
  includeAdult: boolean;
}

/**
 * Type d'élément dans le dropdown.
 */
type DropdownItemType = 'suggestion' | 'history' | 'popular' | 'action';

/**
 * Élément du dropdown.
 */
interface DropdownItem {
  id: string;
  type: DropdownItemType;
  text: string;
  score?: number;
  source?: string;
  icon?: React.ComponentType<{ size?: number; className?: string }>;
  meta?: string;
}

// ============================================================================
// CONSTANTES
// ============================================================================

/**
 * Tailles disponibles.
 */
const SIZE_CONFIG = {
  sm: {
    input: 'h-9 text-sm px-3',
    icon: 14,
    button: 'h-9 w-9',
    dropdown: 'max-h-[300px]',
    text: 'text-xs',
  },
  md: {
    input: 'h-12 text-base px-4',
    icon: 18,
    button: 'h-12 w-12',
    dropdown: 'max-h-[400px]',
    text: 'text-sm',
  },
  lg: {
    input: 'h-16 text-lg px-6',
    icon: 22,
    button: 'h-16 w-16',
    dropdown: 'max-h-[500px]',
    text: 'text-base',
  },
} as const;

/**
 * Langues rapides pour le filtre.
 */
const QUICK_LANGUAGES: Array<{ code: SiteLanguage | 'all'; label: string; flag: string }> = [
  { code: 'all', label: 'All', flag: '🌍' },
  { code: 'en', label: 'EN', flag: '🇬🇧' },
  { code: 'fr', label: 'FR', flag: '🇫🇷' },
  { code: 'ja', label: 'JP', flag: '🇯🇵' },
  { code: 'ko', label: 'KR', flag: '🇰🇷' },
  { code: 'zh', label: 'CN', flag: '🇨🇳' },
  { code: 'es', label: 'ES', flag: '🇪🇸' },
];

/**
 * Statuts rapides pour le filtre.
 */
const QUICK_STATUSES: Array<{ code: MangaStatus | 'all'; label: string }> = [
  { code: 'all', label: 'All Status' },
  { code: 'ongoing', label: 'Ongoing' },
  { code: 'completed', label: 'Completed' },
  { code: 'hiatus', label: 'Hiatus' },
];

// ============================================================================
// COMPOSANT PRINCIPAL
// ============================================================================

/**
 * SearchBar - Barre de recherche principale avec autocomplétion.
 *
 * @param props - Props du composant
 * @returns Élément JSX du SearchBar
 *
 * @example
 * ```tsx
 * // Basique
 * <SearchBar onSearch={(query) => console.log(query)} />
 *
 * // Hero variant (page d'accueil)
 * <SearchBar
 *   variant="hero"
 *   size="lg"
 *   placeholder="Search manga, webtoon, comics..."
 *   showSiteSelector
 *   showFilters
 *   showHistory
 *   showPopular
 *   redirectToSearch
 * />
 *
 * // Inline variant (header)
 * <SearchBar
 *   variant="inline"
 *   size="sm"
 *   showSiteSelector={false}
 *   showFilters={false}
 * />
 * ```
 */
export function SearchBar({
  onSearch,
  onQueryChange,
  onSiteChange,
  onFilterChange,
  defaultQuery = '',
  defaultSites = [],
  defaultFilters = { language: 'all', status: 'all', includeAdult: false },
  placeholder = 'Search manga...',
  showSiteSelector = true,
  showFilters = false,
  showHistory = true,
  showPopular = true,
  showSuggestions = true,
  autoFocus = false,
  disabled = false,
  size = 'md',
  variant = 'default',
  className,
  redirectToSearch = false,
  maxSuggestions = 8,
  maxHistory = 5,
  debounceDelay = 300,
}: SearchBarProps) {
  const router = useRouter();

  // ==========================================================================
  // ÉTAT LOCAL
  // ==========================================================================

  const [query, setQuery] = useState(defaultQuery);
  const [isFocused, setIsFocused] = useState(false);
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [selectedSites, setSelectedSites] = useState<string[]>(defaultSites);
  const [filters, setFilters] = useState<SearchBarFilters>(defaultFilters);
  const [showFilterPanel, setShowFilterPanel] = useState(false);
  const [isSearching, setIsSearching] = useState(false);

  // ==========================================================================
  // REFS
  // ==========================================================================

  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // ==========================================================================
  // HOOKS DE DONNÉES
  // ==========================================================================

  // Suggestions d'autocomplétion
  const { suggestions, isLoading: isLoadingSuggestions } = useSearchSuggestions(
    query,
    {
      debounceDelay,
      limit: maxSuggestions,
      enabled: showSuggestions && isFocused && query.length >= 2,
    }
  );

  // Historique des recherches
  const { history, isLoading: isLoadingHistory } = useSearchHistory({
    limit: maxHistory,
    autoLoad: showHistory,
  });

  // Recherches populaires
  const { popular, isLoading: isLoadingPopular } = usePopularSearches({
    limit: 5,
    autoLoad: showPopular,
  });

  // ==========================================================================
  // TAILLE CONFIGURÉE
  // ==========================================================================

  const sizeConfig = SIZE_CONFIG[size];

  // ==========================================================================
  // CONSTRUCTION DU DROPDOWN
  // ==========================================================================

  const dropdownItems = useMemo<DropdownItem[]>(() => {
    const items: DropdownItem[] = [];

    // 1. Suggestions d'autocomplétion
    if (showSuggestions && query.length >= 2 && suggestions.length > 0) {
      suggestions.forEach((s, idx) => {
        items.push({
          id: `suggestion-${idx}`,
          type: 'suggestion',
          text: s.text,
          score: s.score,
          source: s.source,
          icon: Search,
        });
      });
    }

    // 2. Historique des recherches (si pas de query ou query courte)
    if (showHistory && query.length < 2 && history.length > 0) {
      history.slice(0, maxHistory).forEach((h, idx) => {
        items.push({
          id: `history-${idx}`,
          type: 'history',
          text: h.query,
          icon: Clock,
          meta: `${h.results_count} results`,
        });
      });
    }

    // 3. Recherches populaires (si pas de query)
    if (showPopular && query.length === 0 && popular.length > 0) {
      popular.slice(0, 5).forEach((p, idx) => {
        items.push({
          id: `popular-${idx}`,
          type: 'popular',
          text: p,
          icon: TrendingUp,
        });
      });
    }

    return items;
  }, [showSuggestions, showHistory, showPopular, query, suggestions, history, popular, maxHistory]);

  // ==========================================================================
  // HANDLERS
  // ==========================================================================

  /**
   * Met à jour la query et notifie le parent.
   */
  const handleQueryChange = useCallback(
    (value: string) => {
      setQuery(value);
      onQueryChange?.(value);
      setActiveIndex(-1);

      // Ouvrir le dropdown si on a des items
      if (value.length >= 2 || (value.length === 0 && (showHistory || showPopular))) {
        setIsDropdownOpen(true);
      } else {
        setIsDropdownOpen(false);
      }
    },
    [onQueryChange, showHistory, showPopular]
  );

  /**
   * Soumet la recherche.
   */
  const handleSubmit = useCallback(
    (searchQuery?: string) => {
      const q = (searchQuery || query).trim();
      if (!q) return;

      setIsSearching(true);
      setIsDropdownOpen(false);

      const options: SearchBarOptions = {
        siteIds: selectedSites,
        filters,
      };

      if (redirectToSearch) {
        const params = new URLSearchParams({ q });
        if (selectedSites.length > 0) {
          params.set('sites', selectedSites.join(','));
        }
        if (filters.language !== 'all') {
          params.set('lang', filters.language);
        }
        if (filters.status !== 'all') {
          params.set('status', filters.status);
        }
        router.push(`/search?${params.toString()}`);
      } else {
        onSearch?.(q, options);
      }

      // Reset l'état de recherche après un délai
      setTimeout(() => setIsSearching(false), 500);
    },
    [query, selectedSites, filters, redirectToSearch, router, onSearch]
  );

  /**
   * Gère la soumission du formulaire.
   */
  const handleFormSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      handleSubmit();
    },
    [handleSubmit]
  );

  /**
   * Gère la navigation clavier dans le dropdown.
   */
  const handleKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLInputElement>) => {
      if (!isDropdownOpen || dropdownItems.length === 0) {
        // Ctrl+K pour focus
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
          e.preventDefault();
          inputRef.current?.focus();
          inputRef.current?.select();
        }
        return;
      }

      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault();
          setActiveIndex((prev) =>
            prev < dropdownItems.length - 1 ? prev + 1 : 0
          );
          break;

        case 'ArrowUp':
          e.preventDefault();
          setActiveIndex((prev) =>
            prev > 0 ? prev - 1 : dropdownItems.length - 1
          );
          break;

        case 'Enter':
          e.preventDefault();
          if (activeIndex >= 0 && activeIndex < dropdownItems.length) {
            const item = dropdownItems[activeIndex];
            handleQueryChange(item.text);
            handleSubmit(item.text);
          } else {
            handleSubmit();
          }
          break;

        case 'Escape':
          e.preventDefault();
          setIsDropdownOpen(false);
          setActiveIndex(-1);
          inputRef.current?.blur();
          break;

        case 'Tab':
          setIsDropdownOpen(false);
          setActiveIndex(-1);
          break;
      }
    },
    [isDropdownOpen, dropdownItems, activeIndex, handleQueryChange, handleSubmit]
  );

  /**
   * Gère le focus du champ.
   */
  const handleFocus = useCallback(() => {
    setIsFocused(true);
    if (dropdownItems.length > 0 || query.length === 0) {
      setIsDropdownOpen(true);
    }
  }, [dropdownItems.length, query.length]);

  /**
   * Gère le blur du champ.
   */
  const handleBlur = useCallback(() => {
    // Délai pour permettre le clic sur un item du dropdown
    setTimeout(() => {
      if (
        containerRef.current &&
        !containerRef.current.contains(document.activeElement)
      ) {
        setIsFocused(false);
        setIsDropdownOpen(false);
        setActiveIndex(-1);
      }
    }, 200);
  }, []);

  /**
   * Gère la sélection d'un site.
   */
  const handleSiteSelectionChange = useCallback(
    (siteIds: string[]) => {
      setSelectedSites(siteIds);
      onSiteChange?.(siteIds);
    },
    [onSiteChange]
  );

  /**
   * Gère le changement d'un filtre.
   */
  const handleFilterChange = useCallback(
    (newFilters: Partial<SearchBarFilters>) => {
      const updated = { ...filters, ...newFilters };
      setFilters(updated);
      onFilterChange?.(updated);
    },
    [filters, onFilterChange]
  );

  /**
   * Réinitialise la recherche.
   */
  const handleClear = useCallback(() => {
    setQuery('');
    onQueryChange?.('');
    setIsDropdownOpen(false);
    setActiveIndex(-1);
    inputRef.current?.focus();
  }, [onQueryChange]);

  // ==========================================================================
  // RACCOURCI CLAVIER GLOBAL (Ctrl+K)
  // ==========================================================================

  useEffect(() => {
    const handleGlobalKeyDown = (e: globalThis.KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    };

    document.addEventListener('keydown', handleGlobalKeyDown);
    return () => document.removeEventListener('keydown', handleGlobalKeyDown);
  }, []);

  // ==========================================================================
  // SCROLL AUTO DANS LE DROPDOWN
  // ==========================================================================

  useEffect(() => {
    if (activeIndex >= 0 && dropdownRef.current) {
      const activeElement = dropdownRef.current.children[activeIndex] as HTMLElement;
      if (activeElement) {
        activeElement.scrollIntoView({ block: 'nearest' });
      }
    }
  }, [activeIndex]);

  // ==========================================================================
  // DÉTECTION DU CLIC EXTÉRIEUR
  // ==========================================================================

  useEffect(() => {
    if (!isDropdownOpen) return;

    const handleClickOutside = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setIsDropdownOpen(false);
        setActiveIndex(-1);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isDropdownOpen]);

  // ==========================================================================
  // RENDU
  // ==========================================================================

  const isHero = variant === 'hero';
  const isInline = variant === 'inline';
  const isLoading = isLoadingSuggestions || isSearching;
  const hasActiveFilters = filters.language !== 'all' || filters.status !== 'all' || filters.includeAdult;

  return (
    <div
      ref={containerRef}
      className={cn(
        'relative w-full',
        isHero && 'max-w-3xl mx-auto',
        className
      )}
    >
      {/* ================================================================ */}
      {/* FORMULAIRE DE RECHERCHE */}
      {/* ================================================================ */}
      <form
        onSubmit={handleFormSubmit}
        role="search"
        aria-label="Search manga"
        className={cn(
          'relative flex items-center',
          'rounded-xl border-2 transition-all duration-300',
          'bg-surface',

          // Bordure et glow selon l'état
          isFocused
            ? 'border-primary shadow-[0_0_20px_rgba(0,255,65,0.3)]'
            : 'border-border-dim hover:border-secondary hover:shadow-[0_0_10px_rgba(0,255,255,0.15)]',

          // Variant hero
          isHero && 'rounded-2xl border-[3px]',
          isHero && isFocused && 'shadow-[0_0_30px_rgba(0,255,65,0.4)]',

          // Variant inline
          isInline && 'rounded-lg',

          // Disabled
          disabled && 'opacity-50 cursor-not-allowed'
        )}
      >
        {/* Icône de recherche */}
        <div
          className={cn(
            'flex items-center justify-center flex-shrink-0',
            'pl-4',
            isHero && 'pl-6'
          )}
          aria-hidden="true"
        >
          {isLoading ? (
            <Loader2
              size={sizeConfig.icon}
              className="text-primary animate-spin"
              style={{ filter: 'drop-shadow(0 0 4px rgba(0, 255, 65, 0.6))' }}
            />
          ) : (
            <Search
              size={sizeConfig.icon}
              className={cn(
                'transition-colors duration-200',
                isFocused ? 'text-primary' : 'text-text-muted'
              )}
              style={
                isFocused
                  ? { filter: 'drop-shadow(0 0 3px rgba(0, 255, 65, 0.5))' }
                  : undefined
              }
            />
          )}
        </div>

        {/* Champ de saisie */}
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => handleQueryChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={handleFocus}
          onBlur={handleBlur}
          placeholder={placeholder}
          disabled={disabled}
          autoFocus={autoFocus}
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
          aria-label="Search query"
          aria-autocomplete="list"
          aria-controls="search-dropdown"
          aria-expanded={isDropdownOpen}
          aria-activedescendant={
            activeIndex >= 0 ? dropdownItems[activeIndex]?.id : undefined
          }
          className={cn(
            'flex-1 min-w-0',
            'bg-transparent border-none outline-none',
            'text-text placeholder:text-text-dim',
            'font-mono',
            sizeConfig.input,
            isHero && 'text-xl font-semibold'
          )}
        />

        {/* Actions à droite du champ */}
        <div className="flex items-center gap-1 pr-2 flex-shrink-0">
          {/* Indicateur de filtres actifs */}
          {hasActiveFilters && showFilters && (
            <span
              className="hidden sm:flex items-center gap-1 px-2 py-1 rounded-md bg-accent/10 border border-accent/30 text-accent text-[10px] font-mono font-bold"
              title="Active filters"
            >
              <Filter size={10} />
              <span>Filters</span>
            </span>
          )}

          {/* Bouton de réinitialisation */}
          {query && (
            <button
              type="button"
              onClick={handleClear}
              aria-label="Clear search"
              className={cn(
                'flex items-center justify-center rounded-md',
                'text-text-dim hover:text-secondary',
                'hover:bg-surface-hover',
                'transition-all duration-150',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                sizeConfig.button.replace('w-', 'w-').replace('h-', 'h-'),
                'h-8 w-8'
              )}
            >
              <X size={sizeConfig.icon - 2} />
            </button>
          )}

          {/* Sélecteur de sites (inline) */}
          {showSiteSelector && !isInline && (
            <div className="hidden md:block">
              <SiteSelector
                selectedSiteIds={selectedSites}
                onSelectionChange={handleSiteSelectionChange}
                compact
                size="sm"
              />
            </div>
          )}

          {/* Bouton de filtres */}
          {showFilters && (
            <button
              type="button"
              onClick={() => setShowFilterPanel(!showFilterPanel)}
              aria-label="Toggle filters"
              aria-expanded={showFilterPanel}
              className={cn(
                'hidden sm:flex items-center justify-center rounded-md',
                'transition-all duration-150',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                'h-8 w-8',
                showFilterPanel
                  ? 'bg-primary-bg text-primary border border-primary/30'
                  : 'text-text-muted hover:text-secondary hover:bg-surface-hover'
              )}
            >
              <Filter size={sizeConfig.icon - 2} />
            </button>
          )}

          {/* Bouton de recherche */}
          <button
            type="submit"
            disabled={disabled || !query.trim()}
            aria-label="Submit search"
            className={cn(
              'flex items-center justify-center rounded-lg',
              'font-mono font-bold',
              'transition-all duration-200',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
              'disabled:opacity-30 disabled:cursor-not-allowed',

              // Style cyberpunk
              'bg-primary text-background',
              'hover:bg-primary-bright hover:shadow-[0_0_15px_rgba(0,255,65,0.5)]',
              'active:scale-95',

              // Taille
              isHero ? 'h-12 px-6 text-base' : 'h-8 px-3 text-xs',
              isInline && 'h-7 px-2'
            )}
          >
            {isSearching ? (
              <Loader2 size={sizeConfig.icon - 2} className="animate-spin" />
            ) : (
              <>
                <span className="hidden sm:inline">Search</span>
                <ArrowRight size={sizeConfig.icon - 2} className="sm:ml-1" />
              </>
            )}
          </button>
        </div>
      </form>

      {/* ================================================================ */}
      {/* RACCOURCI CLAVIER (affiché quand pas focus) */}
      {/* ================================================================ */}
      {!isFocused && !query && !isHero && (
        <div
          className="absolute right-24 top-1/2 -translate-y-1/2 pointer-events-none hidden lg:flex items-center gap-1"
          aria-hidden="true"
        >
          <kbd className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-surface-alt border border-border-dim text-text-dim">
            ⌘
          </kbd>
          <kbd className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-surface-alt border border-border-dim text-text-dim">
            K
          </kbd>
        </div>
      )}

      {/* ================================================================ */}
      {/* PANNEAU DE FILTRES RAPIDES */}
      {/* ================================================================ */}
      {showFilters && showFilterPanel && (
        <div
          className={cn(
            'absolute left-0 right-0 top-full mt-2 z-40',
            'rounded-xl border-2 border-border-dim',
            'bg-surface shadow-2xl',
            'p-4',
            'animate-in fade-in-0 slide-in-from-top-2 duration-200',
            'shadow-[0_0_20px_rgba(0,255,65,0.1)]'
          )}
        >
          <div className="flex flex-wrap gap-4">
            {/* Filtre par langue */}
            <div>
              <label className="block text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider mb-2">
                Language
              </label>
              <div className="flex flex-wrap gap-1">
                {QUICK_LANGUAGES.map((lang) => (
                  <button
                    key={lang.code}
                    type="button"
                    onClick={() => handleFilterChange({ language: lang.code })}
                    aria-pressed={filters.language === lang.code}
                    className={cn(
                      'inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs font-mono',
                      'border transition-all duration-150',
                      'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                      filters.language === lang.code
                        ? 'bg-primary-bg text-primary border-primary'
                        : 'bg-surface-alt text-text-muted border-border-dim hover:border-secondary hover:text-secondary'
                    )}
                  >
                    <span>{lang.flag}</span>
                    <span>{lang.label}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Filtre par statut */}
            <div>
              <label className="block text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider mb-2">
                Status
              </label>
              <div className="flex flex-wrap gap-1">
                {QUICK_STATUSES.map((status) => (
                  <button
                    key={status.code}
                    type="button"
                    onClick={() => handleFilterChange({ status: status.code })}
                    aria-pressed={filters.status === status.code}
                    className={cn(
                      'inline-flex items-center px-2 py-1 rounded-md text-xs font-mono',
                      'border transition-all duration-150',
                      'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                      filters.status === status.code
                        ? 'bg-primary-bg text-primary border-primary'
                        : 'bg-surface-alt text-text-muted border-border-dim hover:border-secondary hover:text-secondary'
                    )}
                  >
                    {status.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Toggle contenu adulte */}
            <div>
              <label className="block text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider mb-2">
                Content
              </label>
              <label
                className={cn(
                  'inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-mono',
                  'border cursor-pointer transition-all duration-150',
                  filters.includeAdult
                    ? 'bg-red-500/10 text-red-400 border-red-500/30'
                    : 'bg-surface-alt text-text-muted border-border-dim hover:border-secondary'
                )}
              >
                <input
                  type="checkbox"
                  checked={filters.includeAdult}
                  onChange={(e) => handleFilterChange({ includeAdult: e.target.checked })}
                  className="sr-only"
                />
                <span>Include 18+</span>
              </label>
            </div>
          </div>

          {/* Bouton reset filtres */}
          {hasActiveFilters && (
            <div className="mt-3 pt-3 border-t border-border-dim">
              <button
                type="button"
                onClick={() =>
                  handleFilterChange({
                    language: 'all',
                    status: 'all',
                    includeAdult: false,
                  })
                }
                className="text-[10px] font-mono text-text-muted hover:text-primary transition-colors"
              >
                Reset all filters
              </button>
            </div>
          )}
        </div>
      )}

      {/* ================================================================ */}
      {/* DROPDOWN DE SUGGESTIONS / HISTORIQUE / POPULAIRE */}
      {/* ================================================================ */}
      {isDropdownOpen && dropdownItems.length > 0 && (
        <div
          id="search-dropdown"
          ref={dropdownRef}
          role="listbox"
          aria-label="Search suggestions"
          className={cn(
            'absolute left-0 right-0 top-full mt-2 z-50',
            'rounded-xl border-2 border-border-dim',
            'bg-surface shadow-2xl',
            'overflow-hidden',
            sizeConfig.dropdown,
            'overflow-y-auto scrollbar-thin scrollbar-thumb-border-dim scrollbar-track-transparent',
            'animate-in fade-in-0 zoom-in-95 slide-in-from-top-2 duration-200',
            'shadow-[0_0_20px_rgba(0,255,65,0.15)]'
          )}
        >
          {/* Suggestions */}
          {showSuggestions && query.length >= 2 && suggestions.length > 0 && (
            <DropdownSection label="Suggestions" icon={Sparkles}>
              {suggestions.map((s, idx) => {
                const itemIndex = dropdownItems.findIndex(
                  (item) => item.id === `suggestion-${idx}`
                );
                return (
                  <DropdownItemRow
                    key={`suggestion-${idx}`}
                    id={`suggestion-${idx}`}
                    icon={Search}
                    text={s.text}
                    meta={s.source}
                    isActive={activeIndex === itemIndex}
                    onClick={() => {
                      handleQueryChange(s.text);
                      handleSubmit(s.text);
                    }}
                    onMouseEnter={() => setActiveIndex(itemIndex)}
                  />
                );
              })}
            </DropdownSection>
          )}

          {/* Historique */}
          {showHistory && query.length < 2 && history.length > 0 && (
            <DropdownSection label="Recent Searches" icon={Clock}>
              {history.slice(0, maxHistory).map((h, idx) => {
                const itemIndex = dropdownItems.findIndex(
                  (item) => item.id === `history-${idx}`
                );
                return (
                  <DropdownItemRow
                    key={`history-${idx}`}
                    id={`history-${idx}`}
                    icon={Clock}
                    text={h.query}
                    meta={`${h.results_count} results • ${h.sites_searched} sites`}
                    isActive={activeIndex === itemIndex}
                    onClick={() => {
                      handleQueryChange(h.query);
                      handleSubmit(h.query);
                    }}
                    onMouseEnter={() => setActiveIndex(itemIndex)}
                    iconColor="text-text-dim"
                  />
                );
              })}
            </DropdownSection>
          )}

          {/* Populaire */}
          {showPopular && query.length === 0 && popular.length > 0 && (
            <DropdownSection label="Trending" icon={TrendingUp}>
              {popular.slice(0, 5).map((p, idx) => {
                const itemIndex = dropdownItems.findIndex(
                  (item) => item.id === `popular-${idx}`
                );
                return (
                  <DropdownItemRow
                    key={`popular-${idx}`}
                    id={`popular-${idx}`}
                    icon={TrendingUp}
                    text={p}
                    isActive={activeIndex === itemIndex}
                    onClick={() => {
                      handleQueryChange(p);
                      handleSubmit(p);
                    }}
                    onMouseEnter={() => setActiveIndex(itemIndex)}
                    iconColor="text-accent"
                  />
                );
              })}
            </DropdownSection>
          )}

          {/* Loading state */}
          {isLoadingSuggestions && query.length >= 2 && (
            <div className="flex items-center justify-center py-4 gap-2">
              <Loader2 size={14} className="text-primary animate-spin" />
              <span className="text-xs font-mono text-text-muted">
                Searching...
              </span>
            </div>
          )}

          {/* Footer du dropdown */}
          <div className="px-3 py-2 border-t border-border-dim bg-surface-alt/50">
            <div className="flex items-center justify-between text-[10px] font-mono text-text-dim">
              <div className="flex items-center gap-2">
                <kbd className="px-1 py-0.5 rounded bg-background border border-border-dim">
                  ↑↓
                </kbd>
                <span>navigate</span>
                <span className="mx-1">•</span>
                <kbd className="px-1 py-0.5 rounded bg-background border border-border-dim">
                  ↵
                </kbd>
                <span>search</span>
                <span className="mx-1">•</span>
                <kbd className="px-1 py-0.5 rounded bg-background border border-border-dim">
                  esc
                </kbd>
                <span>close</span>
              </div>
              {selectedSites.length > 0 && (
                <div className="flex items-center gap-1">
                  <Globe size={10} className="text-secondary" />
                  <span>{selectedSites.length} site(s)</span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// SOUS-COMPOSANTS
// ============================================================================

/**
 * Section du dropdown avec label et icône.
 */
function DropdownSection({
  label,
  icon: Icon,
  children,
}: {
  label: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="flex items-center gap-1.5 px-3 py-2 bg-surface-alt/50 border-b border-border-dim">
        <Icon size={12} className="text-primary" />
        <h3 className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider">
          {label}
        </h3>
      </div>
      <div className="py-1">{children}</div>
    </div>
  );
}

/**
 * Item individuel du dropdown.
 */
function DropdownItemRow({
  id,
  icon: Icon,
  text,
  meta,
  isActive,
  onClick,
  onMouseEnter,
  iconColor = 'text-text-muted',
}: {
  id: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  text: string;
  meta?: string;
  isActive: boolean;
  onClick: () => void;
  onMouseEnter: () => void;
  iconColor?: string;
}) {
  return (
    <button
      id={id}
      type="button"
      role="option"
      aria-selected={isActive}
      onClick={onClick}
      onMouseEnter={onMouseEnter}
      className={cn(
        'w-full flex items-center gap-3 px-3 py-2 text-left',
        'transition-all duration-100',
        'focus:outline-none',
        isActive
          ? 'bg-primary-bg border-l-2 border-l-primary'
          : 'border-l-2 border-l-transparent hover:bg-surface-hover'
      )}
    >
      <Icon
        size={14}
        className={cn(
          'flex-shrink-0 transition-colors',
          isActive ? 'text-primary' : iconColor
        )}
        style={
          isActive
            ? { filter: 'drop-shadow(0 0 3px rgba(0, 255, 65, 0.5))' }
            : undefined
        }
      />

      <span
        className={cn(
          'flex-1 min-w-0 truncate font-mono text-sm',
          isActive ? 'text-primary font-semibold' : 'text-text'
        )}
      >
        {text}
      </span>

      {meta && (
        <span className="flex-shrink-0 text-[10px] font-mono text-text-dim truncate max-w-[120px]">
          {meta}
        </span>
      )}

      {isActive && (
        <ArrowRight
          size={12}
          className="flex-shrink-0 text-primary animate-in slide-in-from-left-1 duration-150"
        />
      )}
    </button>
  );
}

// ============================================================================
// VARIANTE HERO (pour la page d'accueil)
// ============================================================================

/**
 * SearchBarHero - Version hero pour la page d'accueil.
 *
 * Affiche une barre de recherche grande et centrée avec toutes les options.
 *
 * @param props - Props du composant
 *
 * @example
 * ```tsx
 * <SearchBarHero onSearch={(query) => router.push(`/search?q=${query}`)} />
 * ```
 */
export function SearchBarHero({
  onSearch,
  className,
}: {
  onSearch?: (query: string, options?: SearchBarOptions) => void;
  className?: string;
}) {
  return (
    <div className={cn('w-full', className)}>
      <SearchBar
        variant="hero"
        size="lg"
        placeholder="Search manga, webtoon, comics..."
        showSiteSelector
        showFilters
        showHistory
        showPopular
        showSuggestions
        redirectToSearch={!onSearch}
        onSearch={onSearch}
      />

      {/* Texte d'aide */}
      <p className="mt-3 text-center text-xs font-mono text-text-dim">
        Search across <span className="text-secondary">20+ sources</span> •{' '}
        <span className="text-primary">Multi-language</span> •{' '}
        <span className="text-accent">Real-time results</span>
      </p>
    </div>
  );
}

// ============================================================================
// VARIANTE INLINE (pour le header)
// ============================================================================

/**
 * SearchBarInline - Version compacte pour le header.
 *
 * @param props - Props du composant
 *
 * @example
 * ```tsx
 * <header>
 *   <SearchBarInline onSearch={handleSearch} />
 * </header>
 * ```
 */
export function SearchBarInline({
  onSearch,
  className,
}: {
  onSearch?: (query: string) => void;
  className?: string;
}) {
  return (
    <SearchBar
      variant="inline"
      size="sm"
      placeholder="Search..."
      showSiteSelector={false}
      showFilters={false}
      showHistory={false}
      showPopular={false}
      showSuggestions
      redirectToSearch={!onSearch}
      onSearch={onSearch ? (q) => onSearch(q) : undefined}
      className={cn('max-w-xs', className)}
    />
  );
}

// ============================================================================
// EXPORTS
// ============================================================================

export default SearchBar;
export { SearchBarHero, SearchBarInline };
export type { SearchBarOptions, SearchBarFilters };
