/**
 * Composant SiteSelector pour NexusDL.
 *
 * Permet à l'utilisateur de sélectionner les sources de mangas (sites)
 * à utiliser pour la recherche et le téléchargement. Affiche une liste
 * filtrable avec des informations détaillées sur chaque site.
 *
 * Caractéristiques :
 *   - Sélection multiple avec checkboxes
 *   - Filtres par langue, statut (enabled), contenu adulte
 *   - Recherche textuelle dans la liste
 *   - Badges de statut (santé, capacités techniques)
 *   - Actions bulk (tout sélectionner, tout désélectionner)
 *   - Compteur de sites sélectionnés
 *   - Intégration avec le store Zustand (sites)
 *   - Style cyberpunk néon cohérent
 *   - Accessibilité complète (ARIA, clavier, focus)
 *   - Responsive (mobile/desktop)
 *   - Mode compact et mode détaillé
 *   - Tooltip avec détails du site au hover
 *
 * Utilisation :
 *   <SiteSelector />
 *   // Ou avec props personnalisées
 *   <SiteSelector
 *     maxSelection={5}
 *     showHealth={true}
 *     defaultLanguage="en"
 *   />
 *
 * @module components/SiteSelector
 */

'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Check,
  ChevronDown,
  Filter,
  Globe,
  Info,
  Search,
  Shield,
  X,
  Zap,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  HelpCircle,
} from 'lucide-react';

import { cn } from '@/lib/utils';
import { useSiteStore, type Site, type SiteHealth, type SiteLanguage } from '@/store';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Props du composant SiteSelector.
 */
export interface SiteSelectorProps {
  /** IDs des sites initialement sélectionnés. */
  selectedSiteIds?: string[];
  /** Callback appelé quand la sélection change. */
  onSelectionChange?: (siteIds: string[]) => void;
  /** Nombre maximum de sites sélectionnables (0 = illimité). */
  maxSelection?: number;
  /** Afficher les indicateurs de santé des sites. */
  showHealth?: boolean;
  /** Afficher les capacités techniques. */
  showCapabilities?: boolean;
  /** Langue par défaut pour le filtre. */
  defaultLanguage?: SiteLanguage | 'all';
  /** Afficher uniquement les sites activés par défaut. */
  enabledOnly?: boolean;
  /** Inclure les sites adultes par défaut. */
  includeAdult?: boolean;
  /** Variante compacte (sans filtres avancés). */
  compact?: boolean;
  /** Placeholder du champ de recherche. */
  searchPlaceholder?: string;
  /** Classe CSS additionnelle. */
  className?: string;
  /** Désactiver le composant. */
  disabled?: boolean;
  /** Taille du composant. */
  size?: 'sm' | 'md' | 'lg';
}

/**
 * État des filtres.
 */
interface FilterState {
  /** Recherche textuelle. */
  search: string;
  /** Filtre par langue. */
  language: SiteLanguage | 'all';
  /** Afficher uniquement les sites activés. */
  enabledOnly: boolean;
  /** Inclure les sites adultes. */
  includeAdult: boolean;
  /** Filtrer par capacité spécifique. */
  capability: 'all' | 'search' | 'download' | 'cloudflare' | 'auth' | 'js';
}

// ============================================================================
// CONSTANTES
// ============================================================================

/**
 * Langues disponibles avec leurs drapeaux.
 */
const LANGUAGES: Array<{ code: SiteLanguage | 'all'; label: string; flag: string }> = [
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
  { code: 'multi', label: 'Multilingual', flag: '🌐' },
];

/**
 * Capacités disponibles avec leurs icônes.
 */
const CAPABILITIES: Array<{ id: FilterState['capability']; label: string; icon: React.ComponentType<{ size?: number; className?: string }> }> = [
  { id: 'all', label: 'All', icon: Globe },
  { id: 'search', label: 'Search', icon: Search },
  { id: 'download', label: 'Download', icon: Zap },
  { id: 'cloudflare', label: 'Cloudflare', icon: Shield },
  { id: 'auth', label: 'Auth Required', icon: AlertTriangle },
  { id: 'js', label: 'JS Render', icon: Zap },
];

/**
 * Tailles disponibles.
 */
const SIZE_CLASSES = {
  sm: {
    button: 'h-8 px-3 text-xs',
    icon: 14,
    popover: 'w-[320px]',
    item: 'py-1.5 px-2 text-xs',
  },
  md: {
    button: 'h-10 px-4 text-sm',
    icon: 16,
    popover: 'w-[420px]',
    item: 'py-2 px-3 text-sm',
  },
  lg: {
    button: 'h-12 px-5 text-base',
    icon: 18,
    popover: 'w-[520px]',
    item: 'py-2.5 px-4 text-base',
  },
} as const;

// ============================================================================
// COMPOSANTS AUXILIAIRES
// ============================================================================

/**
 * Badge de statut de santé d'un site.
 */
function HealthBadge({ health }: { health?: SiteHealth }) {
  if (!health) {
    return (
      <span
        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono bg-gray-500/10 text-gray-400 border border-gray-500/20"
        title="Health status unknown"
      >
        <HelpCircle size={10} />
        <span>Unknown</span>
      </span>
    );
  }

  const configs = {
    healthy: {
      color: 'bg-green-500/10 text-green-400 border-green-500/20',
      icon: CheckCircle2,
      label: 'Healthy',
    },
    degraded: {
      color: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20',
      icon: AlertTriangle,
      label: 'Degraded',
    },
    down: {
      color: 'bg-red-500/10 text-red-400 border-red-500/20',
      icon: XCircle,
      label: 'Down',
    },
    unknown: {
      color: 'bg-gray-500/10 text-gray-400 border-gray-500/20',
      icon: HelpCircle,
      label: 'Unknown',
    },
  };

  const config = configs[health.status] || configs.unknown;
  const Icon = config.icon;

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono border',
        config.color
      )}
      title={`${config.label} - ${health.latency_ms.toFixed(0)}ms`}
    >
      <Icon size={10} />
      <span>{health.latency_ms.toFixed(0)}ms</span>
    </span>
  );
}

/**
 * Badge de capacité d'un site.
 */
function CapabilityBadges({ site }: { site: Site }) {
  const badges: Array<{ label: string; icon: React.ComponentType<{ size?: number; className?: string }>; color: string }> = [];

  if (site.capabilities.requires_cloudflare_bypass) {
    badges.push({ label: 'CF', icon: Shield, color: 'text-orange-400 bg-orange-500/10 border-orange-500/20' });
  }
  if (site.capabilities.requires_auth) {
    badges.push({ label: 'Auth', icon: AlertTriangle, color: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/20' });
  }
  if (site.capabilities.requires_javascript_rendering) {
    badges.push({ label: 'JS', icon: Zap, color: 'text-purple-400 bg-purple-500/10 border-purple-500/20' });
  }

  if (badges.length === 0) return null;

  return (
    <div className="flex items-center gap-1">
      {badges.map((badge, idx) => {
        const Icon = badge.icon;
        return (
          <span
            key={idx}
            className={cn(
              'inline-flex items-center gap-0.5 px-1 py-0.5 rounded text-[9px] font-mono border',
              badge.color
            )}
            title={badge.label}
          >
            <Icon size={8} />
            <span>{badge.label}</span>
          </span>
        );
      })}
    </div>
  );
}

// ============================================================================
// COMPOSANT PRINCIPAL
// ============================================================================

/**
 * SiteSelector - Composant de sélection de sites sources.
 *
 * Affiche un bouton qui ouvre un popover avec la liste des sites disponibles,
 * des filtres avancés, et des informations détaillées sur chaque site.
 *
 * @param props - Props du composant
 * @returns Élément JSX du SiteSelector
 *
 * @example
 * ```tsx
 * // Utilisation basique
 * <SiteSelector
 *   selectedSiteIds={['mangadex', 'asurascans']}
 *   onSelectionChange={(ids) => console.log(ids)}
 * />
 *
 * // Avec limites
 * <SiteSelector
 *   maxSelection={3}
 *   showHealth={true}
 *   defaultLanguage="en"
 * />
 *
 * // Mode compact
 * <SiteSelector compact />
 * ```
 */
export function SiteSelector({
  selectedSiteIds: controlledSelectedIds,
  onSelectionChange,
  maxSelection = 0,
  showHealth = true,
  showCapabilities = true,
  defaultLanguage = 'all',
  enabledOnly = true,
  includeAdult = false,
  compact = false,
  searchPlaceholder = 'Search sites...',
  className,
  disabled = false,
  size = 'md',
}: SiteSelectorProps) {
  // État local
  const [isOpen, setIsOpen] = useState(false);
  const [internalSelectedIds, setInternalSelectedIds] = useState<string[]>(controlledSelectedIds || []);
  const [filters, setFilters] = useState<FilterState>({
    search: '',
    language: defaultLanguage,
    enabledOnly,
    includeAdult,
    capability: 'all',
  });
  const [showFilters, setShowFilters] = useState(!compact);

  // Refs
  const containerRef = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Store Zustand
  const sites = useSiteStore((state) => state.sites);
  const healthChecks = useSiteStore((state) => state.healthChecks);
  const isLoading = useSiteStore((state) => state.isLoading);
  const fetchSites = useSiteStore((state) => state.actions.fetchSites);
  const checkAllSitesHealth = useSiteStore((state) => state.actions.checkAllSitesHealth);

  // Synchroniser avec la prop contrôlée
  useEffect(() => {
    if (controlledSelectedIds !== undefined) {
      setInternalSelectedIds(controlledSelectedIds);
    }
  }, [controlledSelectedIds]);

  // Charger les sites au montage
  useEffect(() => {
    if (sites.length === 0) {
      fetchSites();
    }
  }, [sites.length, fetchSites]);

  // Taille configurée
  const sizeConfig = SIZE_CLASSES[size];

  // ==========================================================================
  // FILTRAGE DES SITES
  // ==========================================================================

  const filteredSites = useMemo(() => {
    return sites.filter((site) => {
      // Filtre par recherche textuelle
      if (filters.search) {
        const searchLower = filters.search.toLowerCase();
        const matchesName = site.name.toLowerCase().includes(searchLower);
        const matchesDomain = site.domains.some((d) => d.toLowerCase().includes(searchLower));
        const matchesTags = site.tags.some((t) => t.toLowerCase().includes(searchLower));
        if (!matchesName && !matchesDomain && !matchesTags) return false;
      }

      // Filtre par langue
      if (filters.language !== 'all' && site.language !== filters.language) {
        return false;
      }

      // Filtre par statut enabled
      if (filters.enabledOnly && !site.enabled) {
        return false;
      }

      // Filtre par contenu adulte
      if (!filters.includeAdult && site.adult) {
        return false;
      }

      // Filtre par capacité
      if (filters.capability !== 'all') {
        switch (filters.capability) {
          case 'search':
            if (!site.capabilities.supports_search) return false;
            break;
          case 'download':
            if (!site.capabilities.supports_download) return false;
            break;
          case 'cloudflare':
            if (!site.capabilities.requires_cloudflare_bypass) return false;
            break;
          case 'auth':
            if (!site.capabilities.requires_auth) return false;
            break;
          case 'js':
            if (!site.capabilities.requires_javascript_rendering) return false;
            break;
        }
      }

      return true;
    });
  }, [sites, filters]);

  // ==========================================================================
  // HANDLERS
  // ==========================================================================

  /**
   * Met à jour la sélection et notifie le parent.
   */
  const updateSelection = useCallback(
    (newSelection: string[]) => {
      setInternalSelectedIds(newSelection);
      onSelectionChange?.(newSelection);
    },
    [onSelectionChange]
  );

  /**
   * Toggle la sélection d'un site.
   */
  const handleToggleSite = useCallback(
    (siteId: string) => {
      const isSelected = internalSelectedIds.includes(siteId);

      if (isSelected) {
        updateSelection(internalSelectedIds.filter((id) => id !== siteId));
      } else {
        // Vérifier la limite
        if (maxSelection > 0 && internalSelectedIds.length >= maxSelection) {
          return;
        }
        updateSelection([...internalSelectedIds, siteId]);
      }
    },
    [internalSelectedIds, maxSelection, updateSelection]
  );

  /**
   * Sélectionne tous les sites filtrés.
   */
  const handleSelectAll = useCallback(() => {
    const allFilteredIds = filteredSites.map((s) => s.id);
    if (maxSelection > 0) {
      updateSelection(allFilteredIds.slice(0, maxSelection));
    } else {
      updateSelection(allFilteredIds);
    }
  }, [filteredSites, maxSelection, updateSelection]);

  /**
   * Désélectionne tous les sites.
   */
  const handleClearAll = useCallback(() => {
    updateSelection([]);
  }, [updateSelection]);

  /**
   * Ouvre/ferme le popover.
   */
  const handleTogglePopover = useCallback(() => {
    if (disabled) return;
    setIsOpen((prev) => !prev);
  }, [disabled]);

  /**
   * Gère la navigation clavier.
   */
  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (disabled) return;

      if (!isOpen) {
        if (event.key === 'Enter' || event.key === ' ' || event.key === 'ArrowDown') {
          event.preventDefault();
          setIsOpen(true);
        }
        return;
      }

      switch (event.key) {
        case 'Escape':
          event.preventDefault();
          setIsOpen(false);
          break;
      }
    },
    [isOpen, disabled]
  );

  // ==========================================================================
  // EFFECTS
  // ==========================================================================

  /**
   * Ferme le popover au clic extérieur.
   */
  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    };

    const timeoutId = setTimeout(() => {
      document.addEventListener('mousedown', handleClickOutside);
    }, 0);

    return () => {
      clearTimeout(timeoutId);
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen]);

  /**
   * Focus sur le champ de recherche à l'ouverture.
   */
  useEffect(() => {
    if (isOpen && searchInputRef.current) {
      const timeoutId = setTimeout(() => {
        searchInputRef.current?.focus();
      }, 100);
      return () => clearTimeout(timeoutId);
    }
  }, [isOpen]);

  /**
   * Charger les health checks si activé.
   */
  useEffect(() => {
    if (showHealth && Object.keys(healthChecks).length === 0) {
      checkAllSitesHealth();
    }
  }, [showHealth, healthChecks, checkAllSitesHealth]);

  // ==========================================================================
  // DONNÉES D'AFFICHAGE
  // ==========================================================================

  const selectedSites = useMemo(() => {
    return sites.filter((s) => internalSelectedIds.includes(s.id));
  }, [sites, internalSelectedIds]);

  const selectedCount = internalSelectedIds.length;
  const totalCount = sites.length;
  const filteredCount = filteredSites.length;

  const getLanguageFlag = (lang: SiteLanguage): string => {
    const found = LANGUAGES.find((l) => l.code === lang);
    return found?.flag || '🏳️';
  };

  // ==========================================================================
  // RENDU
  // ==========================================================================

  return (
    <div
      ref={containerRef}
      className={cn('relative inline-block', className)}
      onKeyDown={handleKeyDown}
    >
      {/* ================================================================ */}
      {/* BOUTON PRINCIPAL */}
      {/* ================================================================ */}
      <button
        type="button"
        onClick={handleTogglePopover}
        disabled={disabled}
        aria-label={`Selected ${selectedCount} of ${totalCount} sites. Click to change selection.`}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        aria-controls="site-selector-popover"
        className={cn(
          // Base
          'group inline-flex items-center gap-2',
          'rounded-lg border-2 transition-all duration-200',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2',
          'focus-visible:ring-offset-background',

          // Style cyberpunk néon
          'border-border-dim bg-surface hover:bg-surface-hover',
          'hover:border-secondary',
          'focus-visible:ring-secondary',

          // Taille
          sizeConfig.button,

          // État ouvert
          isOpen && 'border-primary bg-primary-bg',

          // État désactivé
          disabled && 'opacity-50 cursor-not-allowed',

          // Glow au hover
          !disabled && 'hover:shadow-[0_0_10px_rgba(0,255,255,0.3)]',
          isOpen && 'shadow-[0_0_15px_rgba(0,255,65,0.4)]'
        )}
      >
        {/* Icône Globe */}
        <Globe
          size={sizeConfig.icon}
          className={cn(
            'transition-all duration-300',
            'text-text-muted group-hover:text-secondary',
            isOpen && 'text-primary'
          )}
        />

        {/* Label avec compteur */}
        <span className="font-mono font-semibold text-text">
          {selectedCount === 0 ? (
            'All Sites'
          ) : selectedCount === totalCount ? (
            'All Sites'
          ) : (
            <>
              <span className="text-primary">{selectedCount}</span>
              <span className="text-text-muted">/{totalCount}</span>
              <span className="ml-1 hidden sm:inline">Sites</span>
            </>
          )}
        </span>

        {/* Chevron */}
        <ChevronDown
          size={sizeConfig.icon}
          className={cn(
            'transition-transform duration-200',
            'text-text-muted',
            isOpen && 'rotate-180 text-primary'
          )}
        />

        {/* Indicateur de sélection (petit point) */}
        {selectedCount > 0 && selectedCount < totalCount && (
          <span
            className="absolute -top-1 -right-1 h-3 w-3 rounded-full bg-primary border-2 border-background animate-pulse"
            aria-hidden="true"
          />
        )}
      </button>

      {/* ================================================================ */}
      {/* POPOVER */}
      {/* ================================================================ */}
      {isOpen && (
        <div
          id="site-selector-popover"
          ref={popoverRef}
          role="dialog"
          aria-label="Site selection"
          className={cn(
            // Position
            'absolute left-0 top-full mt-2 z-50',

            // Style
            'rounded-lg border-2 border-border-dim',
            'bg-surface shadow-2xl',
            'overflow-hidden',

            // Taille
            sizeConfig.popover,

            // Animation
            'animate-in fade-in-0 zoom-in-95 slide-in-from-top-2 duration-200',

            // Glow
            'shadow-[0_0_20px_rgba(0,255,65,0.15)]'
          )}
        >
          {/* ============================================================ */}
          {/* EN-TÊTE */}
          {/* ============================================================ */}
          <div className="px-3 py-2.5 border-b border-border-dim bg-surface-alt">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-xs font-mono font-bold text-primary tracking-wider uppercase">
                Select Sources
              </h3>
              <button
                type="button"
                onClick={() => setIsOpen(false)}
                aria-label="Close"
                className="p-1 rounded hover:bg-surface-hover text-text-muted hover:text-secondary transition-colors"
              >
                <X size={14} />
              </button>
            </div>

            {/* Champ de recherche */}
            <div className="relative">
              <Search
                size={14}
                className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-dim"
                aria-hidden="true"
              />
              <input
                ref={searchInputRef}
                type="text"
                value={filters.search}
                onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value }))}
                placeholder={searchPlaceholder}
                aria-label="Search sites"
                className={cn(
                  'w-full h-8 pl-8 pr-8 rounded-md',
                  'bg-background border border-border-dim',
                  'text-text placeholder:text-text-dim',
                  'text-xs font-mono',
                  'focus:outline-none focus:border-secondary',
                  'transition-colors'
                )}
              />
              {filters.search && (
                <button
                  type="button"
                  onClick={() => setFilters((f) => ({ ...f, search: '' }))}
                  aria-label="Clear search"
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 rounded hover:bg-surface-hover text-text-dim hover:text-secondary transition-colors"
                >
                  <X size={12} />
                </button>
              )}
            </div>
          </div>

          {/* ============================================================ */}
          {/* FILTRES (si non compact) */}
          {/* ============================================================ */}
          {!compact && (
            <div className="px-3 py-2 border-b border-border-dim bg-surface-alt/50">
              <button
                type="button"
                onClick={() => setShowFilters(!showFilters)}
                aria-expanded={showFilters}
                className="flex items-center gap-1.5 text-xs font-mono text-text-muted hover:text-secondary transition-colors mb-2"
              >
                <Filter size={12} />
                <span>Filters</span>
                <ChevronDown
                  size={10}
                  className={cn('transition-transform', showFilters && 'rotate-180')}
                />
              </button>

              {showFilters && (
                <div className="space-y-2 animate-in fade-in slide-in-from-top-1 duration-150">
                  {/* Filtre par langue */}
                  <div>
                    <label className="text-[10px] font-mono text-text-dim uppercase tracking-wider mb-1 block">
                      Language
                    </label>
                    <select
                      value={filters.language}
                      onChange={(e) =>
                        setFilters((f) => ({ ...f, language: e.target.value as SiteLanguage | 'all' }))
                      }
                      aria-label="Filter by language"
                      className={cn(
                        'w-full h-7 px-2 rounded text-xs font-mono',
                        'bg-background border border-border-dim',
                        'text-text',
                        'focus:outline-none focus:border-secondary'
                      )}
                    >
                      {LANGUAGES.map((lang) => (
                        <option key={lang.code} value={lang.code}>
                          {lang.flag} {lang.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  {/* Filtre par capacité */}
                  <div>
                    <label className="text-[10px] font-mono text-text-dim uppercase tracking-wider mb-1 block">
                      Capability
                    </label>
                    <div className="flex flex-wrap gap-1">
                      {CAPABILITIES.map((cap) => {
                        const Icon = cap.icon;
                        const isActive = filters.capability === cap.id;
                        return (
                          <button
                            key={cap.id}
                            type="button"
                            onClick={() => setFilters((f) => ({ ...f, capability: cap.id }))}
                            aria-pressed={isActive}
                            className={cn(
                              'inline-flex items-center gap-1 px-2 py-1 rounded text-[10px] font-mono',
                              'border transition-colors',
                              isActive
                                ? 'bg-primary-bg text-primary border-primary'
                                : 'bg-background text-text-muted border-border-dim hover:border-secondary hover:text-secondary'
                            )}
                          >
                            <Icon size={10} />
                            <span>{cap.label}</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {/* Toggles */}
                  <div className="flex flex-wrap gap-3 pt-1">
                    <label className="inline-flex items-center gap-1.5 text-xs font-mono text-text-muted cursor-pointer hover:text-text transition-colors">
                      <input
                        type="checkbox"
                        checked={filters.enabledOnly}
                        onChange={(e) => setFilters((f) => ({ ...f, enabledOnly: e.target.checked }))}
                        className="rounded border-border-dim bg-background text-primary focus:ring-primary"
                      />
                      <span>Enabled only</span>
                    </label>

                    <label className="inline-flex items-center gap-1.5 text-xs font-mono text-text-muted cursor-pointer hover:text-text transition-colors">
                      <input
                        type="checkbox"
                        checked={filters.includeAdult}
                        onChange={(e) => setFilters((f) => ({ ...f, includeAdult: e.target.checked }))}
                        className="rounded border-border-dim bg-background text-primary focus:ring-primary"
                      />
                      <span>Include adult</span>
                    </label>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ============================================================ */}
          {/* ACTIONS BULK */}
          {/* ============================================================ */}
          <div className="px-3 py-1.5 border-b border-border-dim bg-surface-alt/30 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleSelectAll}
                disabled={filteredCount === 0 || (maxSelection > 0 && filteredCount > maxSelection && selectedCount === maxSelection)}
                className="text-[10px] font-mono text-text-muted hover:text-primary transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Select all
              </button>
              <span className="text-text-dim">•</span>
              <button
                type="button"
                onClick={handleClearAll}
                disabled={selectedCount === 0}
                className="text-[10px] font-mono text-text-muted hover:text-primary transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Clear
              </button>
            </div>

            <div className="text-[10px] font-mono text-text-dim">
              {filteredCount} / {totalCount} sites
              {maxSelection > 0 && (
                <span className="ml-1 text-text-muted">
                  (max {maxSelection})
                </span>
              )}
            </div>
          </div>

          {/* ============================================================ */}
          {/* LISTE DES SITES */}
          {/* ============================================================ */}
          <div
            className="overflow-y-auto max-h-[400px] scrollbar-thin scrollbar-thumb-border-dim scrollbar-track-transparent"
            role="listbox"
            aria-multiselectable="true"
            aria-label="Available sites"
          >
            {isLoading ? (
              <div className="flex items-center justify-center py-8">
                <div className="flex items-center gap-2 text-text-muted text-xs font-mono">
                  <div className="h-3 w-3 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                  <span>Loading sites...</span>
                </div>
              </div>
            ) : filteredCount === 0 ? (
              <div className="flex flex-col items-center justify-center py-8 text-center">
                <Globe size={24} className="text-text-dim mb-2" />
                <p className="text-xs font-mono text-text-muted">No sites match your filters</p>
                <button
                  type="button"
                  onClick={() =>
                    setFilters({
                      search: '',
                      language: 'all',
                      enabledOnly: false,
                      includeAdult: false,
                      capability: 'all',
                    })
                  }
                  className="mt-2 text-[10px] font-mono text-primary hover:underline"
                >
                  Reset filters
                </button>
              </div>
            ) : (
              <div className="p-1">
                {filteredSites.map((site) => {
                  const isSelected = internalSelectedIds.includes(site.id);
                  const isDisabled = !isSelected && maxSelection > 0 && selectedCount >= maxSelection;
                  const health = showHealth ? healthChecks[site.id] : undefined;
                  const langFlag = getLanguageFlag(site.language);

                  return (
                    <button
                      key={site.id}
                      type="button"
                      role="option"
                      aria-selected={isSelected}
                      aria-disabled={isDisabled}
                      onClick={() => !isDisabled && handleToggleSite(site.id)}
                      disabled={isDisabled}
                      className={cn(
                        // Base
                        'group/item w-full flex items-start gap-2.5',
                        'rounded-md text-left transition-all duration-150',
                        'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',

                        // Padding selon taille
                        sizeConfig.item,

                        // États
                        isSelected
                          ? 'bg-primary-bg border border-primary/30'
                          : 'border border-transparent hover:bg-surface-hover hover:border-border-dim',

                        // Disabled
                        isDisabled && 'opacity-40 cursor-not-allowed'
                      )}
                    >
                      {/* Checkbox */}
                      <div
                        className={cn(
                          'flex-shrink-0 mt-0.5',
                          'h-4 w-4 rounded border-2 flex items-center justify-center',
                          'transition-all',
                          isSelected
                            ? 'bg-primary border-primary'
                            : 'bg-background border-border-dim group-hover/item:border-secondary'
                        )}
                      >
                        {isSelected && (
                          <Check
                            size={10}
                            className="text-background animate-in zoom-in duration-150"
                          />
                        )}
                      </div>

                      {/* Contenu principal */}
                      <div className="flex-1 min-w-0">
                        {/* Ligne 1 : Nom + drapeau */}
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs" aria-hidden="true">
                            {langFlag}
                          </span>
                          <span
                            className={cn(
                              'font-mono font-semibold truncate',
                              isSelected ? 'text-primary' : 'text-text group-hover/item:text-secondary'
                            )}
                          >
                            {site.name}
                          </span>
                          {site.adult && (
                            <span
                              className="text-[9px] px-1 py-0 rounded bg-red-500/10 text-red-400 border border-red-500/20 font-mono"
                              title="Adult content"
                            >
                              18+
                            </span>
                          )}
                          {!site.enabled && (
                            <span
                              className="text-[9px] px-1 py-0 rounded bg-gray-500/10 text-gray-400 border border-gray-500/20 font-mono"
                              title="Site disabled"
                            >
                              OFF
                            </span>
                          )}
                        </div>

                        {/* Ligne 2 : Domaine + badges */}
                        <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
                          <span className="text-[10px] font-mono text-text-dim truncate">
                            {site.domains[0]?.replace(/^https?:\/\//, '').replace(/\/$/, '') || 'Unknown'}
                          </span>

                          {/* Badges de capacité */}
                          {showCapabilities && <CapabilityBadges site={site} />}

                          {/* Badge de santé */}
                          {showHealth && <HealthBadge health={health} />}
                        </div>

                        {/* Ligne 3 : Tags (si disponibles) */}
                        {site.tags.length > 0 && (
                          <div className="flex items-center gap-1 mt-1 flex-wrap">
                            {site.tags.slice(0, 3).map((tag) => (
                              <span
                                key={tag}
                                className="text-[9px] px-1 py-0 rounded bg-surface-alt text-text-muted border border-border-dim font-mono"
                              >
                                {tag}
                              </span>
                            ))}
                            {site.tags.length > 3 && (
                              <span className="text-[9px] text-text-dim font-mono">
                                +{site.tags.length - 3}
                              </span>
                            )}
                          </div>
                        )}
                      </div>

                      {/* Ligne d'accent à gauche si sélectionné */}
                      {isSelected && (
                        <span
                          className="absolute left-0 top-1/2 -translate-y-1/2 h-6 w-0.5 rounded-r bg-primary"
                          aria-hidden="true"
                        />
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* ============================================================ */}
          {/* PIED */}
          {/* ============================================================ */}
          <div className="px-3 py-2 border-t border-border-dim bg-surface-alt">
            <div className="flex items-center justify-between">
              {/* Résumé de la sélection */}
              <div className="flex items-center gap-1.5 min-w-0 flex-1">
                {selectedCount > 0 ? (
                  <div className="flex items-center gap-1 flex-wrap">
                    <span className="text-[10px] font-mono text-text-muted">Selected:</span>
                    {selectedSites.slice(0, 3).map((site) => (
                      <span
                        key={site.id}
                        className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-primary-bg text-primary border border-primary/20"
                      >
                        {site.name}
                      </span>
                    ))}
                    {selectedCount > 3 && (
                      <span className="text-[10px] font-mono text-text-dim">
                        +{selectedCount - 3} more
                      </span>
                    )}
                  </div>
                ) : (
                  <span className="text-[10px] font-mono text-text-dim">
                    No sites selected (using all)
                  </span>
                )}
              </div>

              {/* Bouton de refresh health */}
              {showHealth && (
                <button
                  type="button"
                  onClick={() => checkAllSitesHealth()}
                  aria-label="Refresh health status"
                  title="Check all sites health"
                  className="p-1 rounded hover:bg-surface-hover text-text-dim hover:text-secondary transition-colors"
                >
                  <Info size={12} />
                </button>
              )}
            </div>

            {/* Instructions clavier */}
            <p className="text-[9px] text-text-dim font-mono mt-1.5 leading-tight">
              <kbd className="px-1 py-0.5 rounded bg-background border border-border-dim">↑↓</kbd> navigate
              <span className="mx-1">•</span>
              <kbd className="px-1 py-0.5 rounded bg-background border border-border-dim">Space</kbd> toggle
              <span className="mx-1">•</span>
              <kbd className="px-1 py-0.5 rounded bg-background border border-border-dim">Esc</kbd> close
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// VARIANTE COMPACTE (chips)
// ============================================================================

/**
 * SiteSelectorCompact - Version compacte affichant les sites sélectionnés comme chips.
 *
 * @param props - Props du composant
 *
 * @example
 * ```tsx
 * <SiteSelectorCompact
 *   selectedSiteIds={['mangadex', 'asurascans']}
 *   onSelectionChange={(ids) => console.log(ids)}
 * />
 * ```
 */
export function SiteSelectorCompact({
  selectedSiteIds = [],
  onSelectionChange,
  maxSelection = 0,
  className,
}: {
  selectedSiteIds?: string[];
  onSelectionChange?: (ids: string[]) => void;
  maxSelection?: number;
  className?: string;
}) {
  const sites = useSiteStore((state) => state.sites);
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const selectedSites = useMemo(
    () => sites.filter((s) => selectedSiteIds.includes(s.id)),
    [sites, selectedSiteIds]
  );

  const handleRemove = useCallback(
    (siteId: string) => {
      onSelectionChange?.(selectedSiteIds.filter((id) => id !== siteId));
    },
    [selectedSiteIds, onSelectionChange]
  );

  // Fermer au clic extérieur
  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  return (
    <div ref={containerRef} className={cn('relative', className)}>
      <div className="flex flex-wrap items-center gap-1.5">
        {/* Chips des sites sélectionnés */}
        {selectedSites.map((site) => (
          <span
            key={site.id}
            className={cn(
              'inline-flex items-center gap-1',
              'px-2 py-1 rounded-md',
              'bg-primary-bg text-primary border border-primary/30',
              'text-xs font-mono'
            )}
          >
            <span>{getLanguageFlag(site.language)}</span>
            <span>{site.name}</span>
            <button
              type="button"
              onClick={() => handleRemove(site.id)}
              aria-label={`Remove ${site.name}`}
              className="ml-0.5 p-0.5 rounded hover:bg-primary/20 transition-colors"
            >
              <X size={10} />
            </button>
          </span>
        ))}

        {/* Bouton pour ajouter */}
        <button
          type="button"
          onClick={() => setIsOpen(true)}
          disabled={maxSelection > 0 && selectedSiteIds.length >= maxSelection}
          className={cn(
            'inline-flex items-center gap-1',
            'px-2 py-1 rounded-md',
            'bg-surface text-text-muted border border-border-dim',
            'hover:bg-surface-hover hover:border-secondary hover:text-secondary',
            'text-xs font-mono transition-colors',
            'disabled:opacity-50 disabled:cursor-not-allowed'
          )}
        >
          <Globe size={12} />
          <span>Add site</span>
        </button>
      </div>

      {/* Popover de sélection */}
      {isOpen && (
        <div className="absolute left-0 top-full mt-2 z-50">
          <SiteSelector
            selectedSiteIds={selectedSiteIds}
            onSelectionChange={(ids) => {
              onSelectionChange?.(ids);
              if (maxSelection > 0 && ids.length >= maxSelection) {
                setIsOpen(false);
              }
            }}
            maxSelection={maxSelection}
            compact
          />
        </div>
      )}
    </div>
  );
}

// ============================================================================
// HELPERS
// ============================================================================

function getLanguageFlag(lang: SiteLanguage): string {
  const found = LANGUAGES.find((l) => l.code === lang);
  return found?.flag || '🏳️';
}

// ============================================================================
// EXPORTS
// ============================================================================

export default SiteSelector;
export { SiteSelectorCompact, LANGUAGES, CAPABILITIES };
