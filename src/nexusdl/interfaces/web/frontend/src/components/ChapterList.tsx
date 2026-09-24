/**
 * Composant ChapterList pour NexusDL.
 *
 * Affiche la liste des chapitres d'un manga avec toutes les fonctionnalités
 * de tri, filtrage, sélection, et actions. Utilisé dans la page de détail
 * d'un manga et dans la bibliothèque locale.
 *
 * Caractéristiques :
 *   - Liste des chapitres avec affichage riche (numéro, titre, date, scanlator)
 *   - Indicateurs visuels : lu/non-lu, téléchargé, progression
 *   - Sélection multiple pour téléchargement groupé
 *   - Tri par numéro, date, statut
 *   - Filtrage : tous, lus, non-lus, téléchargés
 *   - Recherche dans les chapitres
 *   - Actions : lire, télécharger, marquer lu/non-lu
 *   - 2 variantes : default (détaillé), compact
 *   - Skeleton loader pour l'état de chargement
 *   - État vide pour liste sans chapitres
 *   - Accessibilité complète (ARIA, clavier, focus)
 *   - Style cyberpunk néon cohérent
 *   - Responsive (mobile/desktop)
 *
 * Utilisation :
 *   // Basique
 *   <ChapterList chapters={chapters} mangaId={mangaId} />
 *
 *   // Avec actions
 *   <ChapterList
 *     chapters={chapters}
 *     mangaId={mangaId}
 *     onRead={handleRead}
 *     onDownload={handleDownload}
 *     onMarkRead={handleMarkRead}
 *   />
 *
 *   // Mode compact
 *   <ChapterListCompact chapters={chapters} />
 *
 *   // Skeleton loader
 *   <ChapterListSkeleton count={10} />
 *
 * @module components/ChapterList
 */

'use client';

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
} from 'react';
import {
  BookOpen,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Download,
  Eye,
  EyeOff,
  Filter,
  Loader2,
  Play,
  Search,
  SortAsc,
  SortDesc,
  X,
} from 'lucide-react';

import { cn } from '@/lib/utils';
import { formatChapterNumber } from '@/types/manga';

import { ProgressBar } from '@/components/ProgressBar';

import type { Chapter } from '@/types/manga';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Variantes du composant ChapterList.
 */
export type ChapterListVariant = 'default' | 'compact';

/**
 * Critères de tri des chapitres.
 */
export type ChapterSortBy = 'number' | 'date' | 'status';

/**
 * Filtres de chapitres.
 */
export type ChapterFilter = 'all' | 'unread' | 'read' | 'downloaded';

/**
 * Props du composant ChapterList.
 */
export interface ChapterListProps {
  /** Liste des chapitres à afficher. */
  chapters: Chapter[];
  /** ID du manga parent. */
  mangaId: string;
  /** Variante d'affichage. */
  variant?: ChapterListVariant;
  /** Afficher la barre d'outils (tri, filtre, recherche). */
  showToolbar?: boolean;
  /** Afficher les statistiques (total, lus, téléchargés). */
  showStats?: boolean;
  /** Activer la sélection multiple. */
  selectable?: boolean;
  /** IDs des chapitres sélectionnés. */
  selectedChapterIds?: string[];
  /** Callback quand la sélection change. */
  onSelectionChange?: (chapterIds: string[]) => void;
  /** Callback pour lire un chapitre. */
  onRead?: (chapter: Chapter) => void;
  /** Callback pour télécharger un chapitre. */
  onDownload?: (chapter: Chapter) => void;
  /** Callback pour télécharger les chapitres sélectionnés. */
  onDownloadSelected?: (chapterIds: string[]) => void;
  /** Callback pour marquer un chapitre comme lu/non-lu. */
  onMarkRead?: (chapterId: string, isRead: boolean) => void;
  /** Callback pour marquer les chapitres sélectionnés comme lus. */
  onMarkSelectedRead?: (chapterIds: string[], isRead: boolean) => void;
  /** Tri par défaut. */
  defaultSortBy?: ChapterSortBy;
  /** Ordre de tri par défaut. */
  defaultSortOrder?: 'asc' | 'desc';
  /** Filtre par défaut. */
  defaultFilter?: ChapterFilter;
  /** Nombre maximum de chapitres affichés (pagination). */
  pageSize?: number;
  /** Classe CSS additionnelle. */
  className?: string;
  /** Désactiver les interactions. */
  disabled?: boolean;
}

/**
 * Props du composant ChapterListSkeleton.
 */
export interface ChapterListSkeletonProps {
  /** Nombre de skeletons à afficher. */
  count?: number;
  /** Variante du skeleton. */
  variant?: ChapterListVariant;
  /** Classe CSS additionnelle. */
  className?: string;
}

// ============================================================================
// CONSTANTES
// ============================================================================

/**
 * Configuration des options de tri.
 */
const SORT_OPTIONS: Array<{
  id: ChapterSortBy;
  label: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
}> = [
  { id: 'number', label: 'Chapter Number', icon: SortAsc },
  { id: 'date', label: 'Release Date', icon: SortDesc },
  { id: 'status', label: 'Read Status', icon: CheckCircle2 },
];

/**
 * Configuration des options de filtre.
 */
const FILTER_OPTIONS: Array<{
  id: ChapterFilter;
  label: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
}> = [
  { id: 'all', label: 'All', icon: BookOpen },
  { id: 'unread', label: 'Unread', icon: Circle },
  { id: 'read', label: 'Read', icon: CheckCircle2 },
  { id: 'downloaded', label: 'Downloaded', icon: Download },
];

// ============================================================================
// HOOKS PERSONNALISÉS
// ============================================================================

/**
 * Hook pour gérer la sélection des chapitres.
 */
export function useChapterSelection(initialSelected: string[] = []) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set(initialSelected));

  const toggleSelection = useCallback((chapterId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(chapterId)) {
        next.delete(chapterId);
      } else {
        next.add(chapterId);
      }
      return next;
    });
  }, []);

  const selectAll = useCallback((chapterIds: string[]) => {
    setSelectedIds(new Set(chapterIds));
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  const isSelected = useCallback((chapterId: string) => {
    return selectedIds.has(chapterId);
  }, [selectedIds]);

  return {
    selectedIds: Array.from(selectedIds),
    toggleSelection,
    selectAll,
    clearSelection,
    isSelected,
    count: selectedIds.size,
  };
}

/**
 * Hook pour gérer les filtres et le tri des chapitres.
 */
export function useChapterFilters(
  chapters: Chapter[],
  options: {
    defaultSortBy?: ChapterSortBy;
    defaultSortOrder?: 'asc' | 'desc';
    defaultFilter?: ChapterFilter;
  } = {}
) {
  const {
    defaultSortBy = 'number',
    defaultSortOrder = 'asc',
    defaultFilter = 'all',
  } = options;

  const [sortBy, setSortBy] = useState<ChapterSortBy>(defaultSortBy);
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>(defaultSortOrder);
  const [filter, setFilter] = useState<ChapterFilter>(defaultFilter);
  const [searchQuery, setSearchQuery] = useState('');

  // Filtrer les chapitres
  const filteredChapters = useMemo(() => {
    let result = [...chapters];

    // Filtre par statut
    if (filter !== 'all') {
      result = result.filter((chapter) => {
        switch (filter) {
          case 'unread':
            return !chapter.is_read;
          case 'read':
            return chapter.is_read;
          case 'downloaded':
            return chapter.is_downloaded;
          default:
            return true;
        }
      });
    }

    // Filtre par recherche
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      result = result.filter(
        (chapter) =>
          chapter.title.toLowerCase().includes(query) ||
          chapter.number.toString().includes(query) ||
          chapter.scanlator.toLowerCase().includes(query)
      );
    }

    return result;
  }, [chapters, filter, searchQuery]);

  // Trier les chapitres
  const sortedChapters = useMemo(() => {
    const sorted = [...filteredChapters];

    sorted.sort((a, b) => {
      let comparison = 0;

      switch (sortBy) {
        case 'number':
          comparison = a.number - b.number;
          break;
        case 'date':
          const dateA = a.published_at ? new Date(a.published_at).getTime() : 0;
          const dateB = b.published_at ? new Date(b.published_at).getTime() : 0;
          comparison = dateA - dateB;
          break;
        case 'status':
          comparison = (a.is_read ? 1 : 0) - (b.is_read ? 1 : 0);
          break;
      }

      return sortOrder === 'asc' ? comparison : -comparison;
    });

    return sorted;
  }, [filteredChapters, sortBy, sortOrder]);

  const toggleSortOrder = useCallback(() => {
    setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'));
  }, []);

  const resetFilters = useCallback(() => {
    setSortBy(defaultSortBy);
    setSortOrder(defaultSortOrder);
    setFilter(defaultFilter);
    setSearchQuery('');
  }, [defaultSortBy, defaultSortOrder, defaultFilter]);

  return {
    chapters: sortedChapters,
    sortBy,
    setSortBy,
    sortOrder,
    setSortOrder,
    toggleSortOrder,
    filter,
    setFilter,
    searchQuery,
    setSearchQuery,
    resetFilters,
    totalCount: chapters.length,
    filteredCount: filteredChapters.length,
  };
}

// ============================================================================
// SOUS-COMPOSANTS
// ============================================================================

/**
 * Item individuel de chapitre.
 */
function ChapterItem({
  chapter,
  variant,
  isSelected,
  selectable,
  onToggleSelection,
  onRead,
  onDownload,
  onMarkRead,
  disabled,
}: {
  chapter: Chapter;
  variant: ChapterListVariant;
  isSelected?: boolean;
  selectable?: boolean;
  onToggleSelection?: (chapterId: string) => void;
  onRead?: (chapter: Chapter) => void;
  onDownload?: (chapter: Chapter) => void;
  onMarkRead?: (chapterId: string, isRead: boolean) => void;
  disabled?: boolean;
}) {
  const isCompact = variant === 'compact';
  const progressPercent =
    chapter.pages_total > 0
      ? Math.round((chapter.current_page / chapter.pages_total) * 100)
      : 0;

  const handleAction = useCallback(
    (e: MouseEvent, action?: (chapter: Chapter) => void) => {
      e.stopPropagation();
      e.preventDefault();
      action?.(chapter);
    },
    [chapter]
  );

  return (
    <article
      role="article"
      aria-label={`Chapter ${chapter.number}: ${chapter.title || 'Untitled'}`}
      aria-selected={isSelected}
      className={cn(
        'group relative flex items-center gap-3',
        'rounded-lg border-2 bg-surface',
        'transition-all duration-200',
        'focus-within:ring-2 focus-within:ring-primary',

        // Bordure selon l'état
        chapter.is_read
          ? 'border-border-dim opacity-75'
          : 'border-border-dim hover:border-secondary',

        // Sélection
        isSelected && 'border-primary bg-primary-bg/20',

        // Disabled
        disabled && 'opacity-50 cursor-not-allowed',

        // Padding
        isCompact ? 'px-3 py-2' : 'px-4 py-3'
      )}
    >
      {/* Checkbox de sélection */}
      {selectable && (
        <div className="flex items-center flex-shrink-0">
          <input
            type="checkbox"
            checked={isSelected}
            onChange={() => onToggleSelection?.(chapter.id)}
            disabled={disabled}
            className="h-4 w-4 rounded border-border-dim bg-surface-alt text-primary focus:ring-primary focus:ring-offset-0 cursor-pointer"
            aria-label={`Select chapter ${chapter.number}`}
          />
        </div>
      )}

      {/* Indicateur de lecture */}
      <div className="flex items-center flex-shrink-0">
        {chapter.is_read ? (
          <CheckCircle2
            size={isCompact ? 16 : 18}
            className="text-green-400"
            style={{ filter: 'drop-shadow(0 0 3px rgba(34, 197, 94, 0.5))' }}
          />
        ) : chapter.current_page > 0 ? (
          <div className="relative">
            <Circle
              size={isCompact ? 16 : 18}
              className="text-primary"
              style={{ filter: 'drop-shadow(0 0 3px rgba(0, 255, 65, 0.5))' }}
            />
            <div
              className="absolute inset-0 flex items-center justify-center text-[8px] font-mono font-bold text-primary"
              aria-hidden="true"
            >
              {progressPercent}
            </div>
          </div>
        ) : (
          <Circle
            size={isCompact ? 16 : 18}
            className="text-text-dim"
          />
        )}
      </div>

      {/* Contenu principal */}
      <div className="flex-1 min-w-0">
        {/* Numéro et titre */}
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'font-mono font-bold',
              isCompact ? 'text-sm' : 'text-base',
              chapter.is_read ? 'text-text-muted' : 'text-text'
            )}
          >
            {formatChapterNumber(chapter.number)}
          </span>
          {chapter.title && !isCompact && (
            <span className="text-text-muted font-mono text-sm truncate">
              {chapter.title}
            </span>
          )}
        </div>

        {/* Métadonnées */}
        {!isCompact && (
          <div className="flex items-center gap-3 mt-1 text-xs font-mono text-text-dim">
            {chapter.published_at && (
              <span className="flex items-center gap-1">
                <span>{new Date(chapter.published_at).toLocaleDateString()}</span>
              </span>
            )}
            {chapter.scanlator && (
              <>
                <span>•</span>
                <span className="truncate">{chapter.scanlator}</span>
              </>
            )}
            <span>•</span>
            <span>{chapter.pages_count} pages</span>
          </div>
        )}

        {/* Barre de progression (si en cours de lecture) */}
        {!isCompact && chapter.current_page > 0 && !chapter.is_read && (
          <div className="mt-2">
            <ProgressBar
              value={progressPercent}
              size="sm"
              variant="default"
              shape="pill"
              aria-label={`Reading progress: ${progressPercent}%`}
            />
          </div>
        )}
      </div>

      {/* Badges */}
      <div className="flex items-center gap-1.5 flex-shrink-0">
        {/* Badge téléchargé */}
        {chapter.is_downloaded && (
          <span
            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-mono font-bold uppercase bg-cyan-500/10 text-cyan-400 border border-cyan-500/30"
            title="Downloaded"
          >
            <Download size={10} />
            {!isCompact && <span>DL</span>}
          </span>
        )}

        {/* Actions */}
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
          {/* Bouton Lire */}
          {onRead && (
            <button
              type="button"
              onClick={(e) => handleAction(e, onRead)}
              disabled={disabled}
              aria-label="Read chapter"
              title="Read"
              className={cn(
                'p-1.5 rounded-md',
                'bg-primary-bg text-primary border border-primary/30',
                'hover:bg-primary/20 hover:border-primary',
                'transition-all duration-200',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                'disabled:opacity-50 disabled:cursor-not-allowed'
              )}
            >
              <Play size={14} fill="currentColor" />
            </button>
          )}

          {/* Bouton Télécharger */}
          {onDownload && !chapter.is_downloaded && (
            <button
              type="button"
              onClick={(e) => handleAction(e, onDownload)}
              disabled={disabled}
              aria-label="Download chapter"
              title="Download"
              className={cn(
                'p-1.5 rounded-md',
                'bg-surface-alt text-secondary border border-secondary/30',
                'hover:bg-secondary/20 hover:border-secondary',
                'transition-all duration-200',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-secondary',
                'disabled:opacity-50 disabled:cursor-not-allowed'
              )}
            >
              <Download size={14} />
            </button>
          )}

          {/* Bouton Marquer lu/non-lu */}
          {onMarkRead && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                e.preventDefault();
                onMarkRead(chapter.id, !chapter.is_read);
              }}
              disabled={disabled}
              aria-label={chapter.is_read ? 'Mark as unread' : 'Mark as read'}
              title={chapter.is_read ? 'Mark as unread' : 'Mark as read'}
              className={cn(
                'p-1.5 rounded-md',
                'bg-surface-alt border border-border-dim',
                'transition-all duration-200',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                'disabled:opacity-50 disabled:cursor-not-allowed',
                chapter.is_read
                  ? 'text-green-400 hover:bg-green-500/20 hover:border-green-500'
                  : 'text-text-muted hover:bg-surface-hover hover:border-secondary hover:text-secondary'
              )}
            >
              {chapter.is_read ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          )}
        </div>
      </div>
    </article>
  );
}

/**
 * Barre d'outils (tri, filtre, recherche).
 */
function ChapterListToolbar({
  sortBy,
  setSortBy,
  sortOrder,
  toggleSortOrder,
  filter,
  setFilter,
  searchQuery,
  setSearchQuery,
  totalCount,
  filteredCount,
  selectedCount,
  onClearSelection,
  onDownloadSelected,
  onMarkSelectedRead,
  onResetFilters,
}: {
  sortBy: ChapterSortBy;
  setSortBy: (sortBy: ChapterSortBy) => void;
  sortOrder: 'asc' | 'desc';
  toggleSortOrder: () => void;
  filter: ChapterFilter;
  setFilter: (filter: ChapterFilter) => void;
  searchQuery: string;
  setSearchQuery: (query: string) => void;
  totalCount: number;
  filteredCount: number;
  selectedCount: number;
  onClearSelection?: () => void;
  onDownloadSelected?: (chapterIds: string[]) => void;
  onMarkSelectedRead?: (chapterIds: string[], isRead: boolean) => void;
  onResetFilters: () => void;
}) {
  const [showFilters, setShowFilters] = useState(false);

  return (
    <div className="space-y-3">
      {/* Ligne 1 : Recherche et stats */}
      <div className="flex items-center gap-3">
        {/* Champ de recherche */}
        <div className="relative flex-1 max-w-xs">
          <Search
            size={14}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-text-dim"
            aria-hidden="true"
          />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search chapters..."
            aria-label="Search chapters"
            className={cn(
              'w-full h-9 pl-9 pr-9 rounded-lg',
              'bg-surface-alt border border-border-dim',
              'text-text placeholder:text-text-dim',
              'text-sm font-mono',
              'focus:outline-none focus:border-secondary',
              'transition-colors'
            )}
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery('')}
              aria-label="Clear search"
              className="absolute right-3 top-1/2 -translate-y-1/2 p-0.5 rounded hover:bg-surface-hover text-text-dim hover:text-secondary transition-colors"
            >
              <X size={12} />
            </button>
          )}
        </div>

        {/* Stats */}
        <div className="text-xs font-mono text-text-dim">
          <span className="text-text font-semibold">{filteredCount}</span>
          <span> / {totalCount} chapters</span>
        </div>

        {/* Bouton filtres */}
        <button
          type="button"
          onClick={() => setShowFilters(!showFilters)}
          aria-expanded={showFilters}
          aria-label="Toggle filters"
          className={cn(
            'flex items-center gap-1.5 px-3 py-2 rounded-lg',
            'text-xs font-mono',
            'border transition-all duration-200',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
            showFilters
              ? 'bg-primary-bg text-primary border-primary'
              : 'bg-surface-alt text-text-muted border-border-dim hover:border-secondary hover:text-secondary'
          )}
        >
          <Filter size={14} />
          <span>Filters</span>
          <ChevronDown
            size={12}
            className={cn('transition-transform', showFilters && 'rotate-180')}
          />
        </button>
      </div>

      {/* Ligne 2 : Filtres (si visible) */}
      {showFilters && (
        <div className="flex flex-wrap items-center gap-3 p-3 rounded-lg bg-surface-alt/50 border border-border-dim animate-in fade-in slide-in-from-top-2 duration-200">
          {/* Tri */}
          <div className="flex items-center gap-2">
            <label className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider">
              Sort by
            </label>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as ChapterSortBy)}
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
              {sortOrder === 'asc' ? <SortAsc size={14} /> : <SortDesc size={14} />}
            </button>
          </div>

          {/* Filtre */}
          <div className="flex items-center gap-2">
            <label className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider">
              Filter
            </label>
            <div className="flex gap-1">
              {FILTER_OPTIONS.map((option) => {
                const Icon = option.icon;
                const isActive = filter === option.id;
                return (
                  <button
                    key={option.id}
                    type="button"
                    onClick={() => setFilter(option.id)}
                    aria-pressed={isActive}
                    className={cn(
                      'inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-mono',
                      'border transition-all duration-150',
                      'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                      isActive
                        ? 'bg-primary-bg text-primary border-primary'
                        : 'bg-background text-text-muted border-border-dim hover:border-secondary hover:text-secondary'
                    )}
                  >
                    <Icon size={10} />
                    <span>{option.label}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Reset */}
          <button
            type="button"
            onClick={onResetFilters}
            className="ml-auto text-[10px] font-mono text-text-muted hover:text-primary transition-colors"
          >
            Reset all
          </button>
        </div>
      )}

      {/* Ligne 3 : Actions de sélection (si des chapitres sont sélectionnés) */}
      {selectedCount > 0 && (
        <div className="flex items-center gap-3 p-3 rounded-lg bg-primary-bg/30 border border-primary/30 animate-in fade-in slide-in-from-top-2 duration-200">
          <span className="text-sm font-mono text-primary font-semibold">
            {selectedCount} chapter{selectedCount > 1 ? 's' : ''} selected
          </span>

          <div className="flex items-center gap-2 ml-auto">
            {onDownloadSelected && (
              <button
                type="button"
                onClick={() => {
                  // Récupérer les IDs sélectionnés depuis le parent
                  // Pour l'instant, on utilise un placeholder
                  onDownloadSelected([]);
                }}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-1.5 rounded-md',
                  'bg-primary text-background',
                  'font-mono font-bold text-xs',
                  'hover:bg-primary-bright hover:shadow-[0_0_10px_rgba(0,255,65,0.5)]',
                  'transition-all duration-200'
                )}
              >
                <Download size={12} />
                <span>Download Selected</span>
              </button>
            )}

            {onMarkSelectedRead && (
              <>
                <button
                  type="button"
                  onClick={() => onMarkSelectedRead([], true)}
                  className={cn(
                    'flex items-center gap-1.5 px-3 py-1.5 rounded-md',
                    'bg-surface-alt text-green-400 border border-green-500/30',
                    'font-mono text-xs',
                    'hover:bg-green-500/20 hover:border-green-500',
                    'transition-all duration-200'
                  )}
                >
                  <Check size={12} />
                  <span>Mark Read</span>
                </button>

                <button
                  type="button"
                  onClick={() => onMarkSelectedRead([], false)}
                  className={cn(
                    'flex items-center gap-1.5 px-3 py-1.5 rounded-md',
                    'bg-surface-alt text-text-muted border border-border-dim',
                    'font-mono text-xs',
                    'hover:bg-surface-hover hover:border-secondary',
                    'transition-all duration-200'
                  )}
                >
                  <EyeOff size={12} />
                  <span>Mark Unread</span>
                </button>
              </>
            )}

            {onClearSelection && (
              <button
                type="button"
                onClick={onClearSelection}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-1.5 rounded-md',
                  'bg-surface-alt text-text-muted border border-border-dim',
                  'font-mono text-xs',
                  'hover:bg-surface-hover hover:border-secondary',
                  'transition-all duration-200'
                )}
              >
                <X size={12} />
                <span>Clear</span>
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * En-tête avec statistiques.
 */
function ChapterListHeader({
  chapters,
}: {
  chapters: Chapter[];
}) {
  const stats = useMemo(() => {
    const total = chapters.length;
    const read = chapters.filter((c) => c.is_read).length;
    const downloaded = chapters.filter((c) => c.is_downloaded).length;
    const unread = total - read;

    return { total, read, unread, downloaded };
  }, [chapters]);

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <div className="flex flex-col p-3 rounded-lg bg-surface-alt/50 border border-border-dim">
        <span className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider mb-1">
          Total
        </span>
        <span className="text-2xl font-mono font-bold text-text">
          {stats.total}
        </span>
      </div>

      <div className="flex flex-col p-3 rounded-lg bg-surface-alt/50 border border-border-dim">
        <span className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider mb-1">
          Unread
        </span>
        <span className="text-2xl font-mono font-bold text-primary">
          {stats.unread}
        </span>
      </div>

      <div className="flex flex-col p-3 rounded-lg bg-surface-alt/50 border border-border-dim">
        <span className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider mb-1">
          Read
        </span>
        <span className="text-2xl font-mono font-bold text-green-400">
          {stats.read}
        </span>
      </div>

      <div className="flex flex-col p-3 rounded-lg bg-surface-alt/50 border border-border-dim">
        <span className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider mb-1">
          Downloaded
        </span>
        <span className="text-2xl font-mono font-bold text-cyan-400">
          {stats.downloaded}
        </span>
      </div>
    </div>
  );
}

// ============================================================================
// COMPOSANT PRINCIPAL : ChapterList
// ============================================================================

/**
 * ChapterList - Liste des chapitres d'un manga.
 *
 * @param props - Props du composant
 * @returns Élément JSX du ChapterList
 *
 * @example
 * ```tsx
 * // Basique
 * <ChapterList chapters={chapters} mangaId={mangaId} />
 *
 * // Avec actions
 * <ChapterList
 *   chapters={chapters}
 *   mangaId={mangaId}
 *   onRead={(chapter) => router.push(`/read/${chapter.id}`)}
 *   onDownload={(chapter) => downloadChapter(chapter.id)}
 *   onMarkRead={(chapterId, isRead) => markAsRead(chapterId, isRead)}
 * />
 *
 * // Avec sélection multiple
 * <ChapterList
 *   chapters={chapters}
 *   mangaId={mangaId}
 *   selectable
 *   onDownloadSelected={(ids) => downloadChapters(ids)}
 * />
 * ```
 */
export function ChapterList({
  chapters,
  mangaId,
  variant = 'default',
  showToolbar = true,
  showStats = true,
  selectable = false,
  selectedChapterIds: controlledSelectedIds,
  onSelectionChange,
  onRead,
  onDownload,
  onDownloadSelected,
  onMarkRead,
  onMarkSelectedRead,
  defaultSortBy = 'number',
  defaultSortOrder = 'asc',
  defaultFilter = 'all',
  pageSize = 50,
  className,
  disabled = false,
}: ChapterListProps) {
  // Gestion de la sélection
  const {
    selectedIds,
    toggleSelection,
    selectAll,
    clearSelection,
    isSelected,
    count: selectedCount,
  } = useChapterSelection(controlledSelectedIds || []);

  // Synchroniser avec la prop contrôlée
  useEffect(() => {
    if (controlledSelectedIds !== undefined && onSelectionChange) {
      onSelectionChange(selectedIds);
    }
  }, [selectedIds, controlledSelectedIds, onSelectionChange]);

  // Gestion des filtres et du tri
  const {
    chapters: filteredChapters,
    sortBy,
    setSortBy,
    sortOrder,
    toggleSortOrder,
    filter,
    setFilter,
    searchQuery,
    setSearchQuery,
    resetFilters,
    totalCount,
    filteredCount,
  } = useChapterFilters(chapters, {
    defaultSortBy,
    defaultSortOrder,
    defaultFilter,
  });

  // Pagination
  const [currentPage, setCurrentPage] = useState(1);
  const totalPages = Math.ceil(filteredChapters.length / pageSize);
  const paginatedChapters = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    return filteredChapters.slice(start, end);
  }, [filteredChapters, currentPage, pageSize]);

  // Reset la page quand les filtres changent
  useEffect(() => {
    setCurrentPage(1);
  }, [filter, searchQuery, sortBy, sortOrder]);

  // Handlers
  const handleToggleSelection = useCallback(
    (chapterId: string) => {
      toggleSelection(chapterId);
    },
    [toggleSelection]
  );

  const handleSelectAll = useCallback(() => {
    selectAll(filteredChapters.map((c) => c.id));
  }, [selectAll, filteredChapters]);

  const handleClearSelection = useCallback(() => {
    clearSelection();
  }, [clearSelection]);

  return (
    <div className={cn('space-y-4', className)}>
      {/* Statistiques */}
      {showStats && <ChapterListHeader chapters={chapters} />}

      {/* Barre d'outils */}
      {showToolbar && (
        <ChapterListToolbar
          sortBy={sortBy}
          setSortBy={setSortBy}
          sortOrder={sortOrder}
          toggleSortOrder={toggleSortOrder}
          filter={filter}
          setFilter={setFilter}
          searchQuery={searchQuery}
          setSearchQuery={setSearchQuery}
          totalCount={totalCount}
          filteredCount={filteredCount}
          selectedCount={selectedCount}
          onClearSelection={handleClearSelection}
          onDownloadSelected={onDownloadSelected ? () => onDownloadSelected(selectedIds) : undefined}
          onMarkSelectedRead={onMarkSelectedRead ? (ids, isRead) => onMarkSelectedRead(selectedIds, isRead) : undefined}
          onResetFilters={resetFilters}
        />
      )}

      {/* Liste des chapitres */}
      <div className="space-y-2" role="list" aria-label="Chapter list">
        {paginatedChapters.length === 0 ? (
          <ChapterListEmpty
            title="No chapters found"
            description="Try adjusting your filters or search query"
          />
        ) : (
          <>
            {paginatedChapters.map((chapter) => (
              <ChapterItem
                key={chapter.id}
                chapter={chapter}
                variant={variant}
                isSelected={isSelected(chapter.id)}
                selectable={selectable}
                onToggleSelection={handleToggleSelection}
                onRead={onRead}
                onDownload={onDownload}
                onMarkRead={onMarkRead}
                disabled={disabled}
              />
            ))}

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="flex items-center justify-center gap-2 pt-4">
                <button
                  type="button"
                  onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                  disabled={currentPage === 1}
                  aria-label="Previous page"
                  className={cn(
                    'px-3 py-1.5 rounded-md',
                    'bg-surface-alt text-text-muted border border-border-dim',
                    'font-mono text-xs',
                    'hover:bg-surface-hover hover:border-secondary hover:text-secondary',
                    'transition-all duration-200',
                    'disabled:opacity-50 disabled:cursor-not-allowed'
                  )}
                >
                  Previous
                </button>

                <span className="text-xs font-mono text-text-muted">
                  Page {currentPage} of {totalPages}
                </span>

                <button
                  type="button"
                  onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                  disabled={currentPage === totalPages}
                  aria-label="Next page"
                  className={cn(
                    'px-3 py-1.5 rounded-md',
                    'bg-surface-alt text-text-muted border border-border-dim',
                    'font-mono text-xs',
                    'hover:bg-surface-hover hover:border-secondary hover:text-secondary',
                    'transition-all duration-200',
                    'disabled:opacity-50 disabled:cursor-not-allowed'
                  )}
                >
                  Next
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ============================================================================
// VARIANTE COMPACTE
// ============================================================================

/**
 * ChapterListCompact - Version compacte de la liste des chapitres.
 *
 * @param props - Props du composant
 */
export function ChapterListCompact(props: Omit<ChapterListProps, 'variant'>) {
  return <ChapterList {...props} variant="compact" showStats={false} />;
}

// ============================================================================
// SKELETON LOADER
// ============================================================================

/**
 * ChapterListSkeleton - Skeleton loader pour l'état de chargement.
 *
 * @param props - Props du composant
 *
 * @example
 * ```tsx
 * {isLoading ? (
 *   <ChapterListSkeleton count={10} />
 * ) : (
 *   <ChapterList chapters={chapters} mangaId={mangaId} />
 * )}
 * ```
 */
export function ChapterListSkeleton({
  count = 10,
  variant = 'default',
  className,
}: ChapterListSkeletonProps) {
  const isCompact = variant === 'compact';

  return (
    <div className={cn('space-y-2', className)}>
      {/* Stats skeleton */}
      {!isCompact && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className="h-20 rounded-lg bg-surface-alt border border-border-dim animate-pulse"
            />
          ))}
        </div>
      )}

      {/* Toolbar skeleton */}
      <div className="flex items-center gap-3">
        <div className="h-9 flex-1 max-w-xs rounded-lg bg-surface-alt animate-pulse" />
        <div className="h-9 w-24 rounded-lg bg-surface-alt animate-pulse" />
      </div>

      {/* Items skeleton */}
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className={cn(
            'relative flex items-center gap-3 rounded-lg border-2 border-border-dim bg-surface animate-pulse',
            isCompact ? 'px-3 py-2' : 'px-4 py-3'
          )}
        >
          {/* Checkbox */}
          <div className="h-4 w-4 rounded bg-surface-alt" />

          {/* Indicateur */}
          <div className={cn('rounded-full bg-surface-alt', isCompact ? 'h-4 w-4' : 'h-5 w-5')} />

          {/* Contenu */}
          <div className="flex-1 space-y-2">
            <div className={cn('h-4 bg-surface-alt rounded', isCompact ? 'w-1/3' : 'w-1/2')} />
            {!isCompact && (
              <>
                <div className="h-3 bg-surface-alt rounded w-2/3" />
                <div className="h-2 bg-surface-alt rounded-full w-full" />
              </>
            )}
          </div>

          {/* Actions */}
          <div className="flex gap-1">
            <div className="h-7 w-7 rounded bg-surface-alt" />
            <div className="h-7 w-7 rounded bg-surface-alt" />
          </div>
        </div>
      ))}
    </div>
  );
}

// ============================================================================
// ÉTAT VIDE
// ============================================================================

/**
 * ChapterListEmpty - État vide pour la liste des chapitres.
 *
 * @param props - Props du composant
 */
export function ChapterListEmpty({
  title = 'No chapters found',
  description = 'Try adjusting your filters or search query',
  action,
  className,
}: {
  title?: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center py-12 px-4 text-center',
        'rounded-lg border-2 border-dashed border-border-dim bg-surface/50',
        className
      )}
    >
      <div
        className="flex items-center justify-center h-14 w-14 rounded-full bg-surface-alt border-2 border-border-dim mb-3"
        aria-hidden="true"
      >
        <BookOpen size={24} className="text-text-dim" />
      </div>

      <h3 className="text-base font-mono font-bold text-text mb-1">{title}</h3>
      <p className="text-sm font-mono text-text-muted max-w-md">{description}</p>

      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

// ============================================================================
// EXPORTS
// ============================================================================

export default ChapterList;
export {
  ChapterListCompact,
  ChapterListSkeleton,
  ChapterListEmpty,
  ChapterItem,
  ChapterListToolbar,
  ChapterListHeader,
  useChapterSelection,
  useChapterFilters,
  SORT_OPTIONS,
  FILTER_OPTIONS,
};
export type { ChapterListVariant, ChapterSortBy, ChapterFilter };
