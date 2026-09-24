/**
 * Page de bibliothèque pour NexusDL.
 *
 * Interface complète pour gérer la bibliothèque locale de mangas avec :
 *   - Affichage en grille ou liste
 *   - Filtrage par statut de lecture, langue, tags
 *   - Tri par titre, date, progression, auteur
 *   - Recherche textuelle
 *   - Modes d'affichage : grid, list, compact
 *   - Statistiques globales
 *   - Actions : lire, télécharger, marquer lu/non-lu, retirer
 *   - Reading lists (listes de lecture)
 *   - Scan de la bibliothèque
 *   - Pagination
 *   - Événements WebSocket temps réel
 *   - Skeleton loader
 *   - État vide
 *   - Style cyberpunk néon
 *   - Accessibilité complète
 *   - Responsive
 *
 * @module app/library/page
 */

'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  AlertCircle,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronDown,
  Clock,
  Download,
  Eye,
  EyeOff,
  Filter,
  Folder,
  Grid3x3,
  Heart,
  LayoutGrid,
  LayoutList,
  Library,
  List,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  Search,
  SortAsc,
  SortDesc,
  Star,
  Trash2,
  X,
  Zap,
} from 'lucide-react';
import { toast } from 'sonner';

import { cn } from '@/lib/utils';
import {
  useLibrary,
  useLibraryWebSocket,
  useUIPreferencesWithActions,
} from '@/store';

import {
  MangaCard,
  MangaCardCompact,
  MangaCardHorizontal,
  MangaCardSkeletonGrid,
  MangaCardEmpty,
} from '@/components/MangaCard';
import { ProgressBar } from '@/components/ProgressBar';
import { SearchBar } from '@/components/SearchBar';

import type {
  Manga,
  ReadingStatus,
  MangaSortBy,
  LanguageCode,
} from '@/types/manga';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Mode d'affichage de la bibliothèque.
 */
type LibraryViewMode = 'grid' | 'list' | 'compact';

/**
 * Options de tri disponibles.
 */
const SORT_OPTIONS: Array<{
  id: MangaSortBy;
  label: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
}> = [
  { id: 'title', label: 'Title', icon: SortAsc },
  { id: 'date_added', label: 'Date Added', icon: Clock },
  { id: 'last_read', label: 'Last Read', icon: Eye },
  { id: 'progress', label: 'Progress', icon: Zap },
  { id: 'author', label: 'Author', icon: SortDesc },
  { id: 'status', label: 'Status', icon: CheckCircle2 },
];

/**
 * Options de filtre par statut de lecture.
 */
const READING_STATUS_OPTIONS: Array<{
  id: ReadingStatus | 'all';
  label: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  color: string;
}> = [
  { id: 'all', label: 'All', icon: Library, color: 'text-text' },
  { id: 'reading', label: 'Reading', icon: BookOpen, color: 'text-blue-400' },
  { id: 'completed', label: 'Completed', icon: CheckCircle2, color: 'text-green-400' },
  { id: 'plan_to_read', label: 'Plan to Read', icon: Star, color: 'text-purple-400' },
  { id: 'on_hold', label: 'On Hold', icon: Pause, color: 'text-yellow-400' },
  { id: 'dropped', label: 'Dropped', icon: X, color: 'text-red-400' },
];

/**
 * Options de langues rapides.
 */
const LANGUAGE_OPTIONS: Array<{ code: LanguageCode | 'all'; label: string; flag: string }> = [
  { code: 'all', label: 'All', flag: '🌍' },
  { code: 'en', label: 'EN', flag: '🇬🇧' },
  { code: 'fr', label: 'FR', flag: '🇫🇷' },
  { code: 'es', label: 'ES', flag: '🇪🇸' },
  { code: 'ja', label: 'JP', flag: '🇯🇵' },
  { code: 'ko', label: 'KR', flag: '🇰🇷' },
  { code: 'zh', label: 'CN', flag: '🇨🇳' },
];

// ============================================================================
// COMPOSANTS AUXILIAIRES
// ============================================================================

/**
 * Panneau de statistiques de la bibliothèque.
 */
function LibraryStats({ mangas }: { mangas: Manga[] }) {
  const stats = useMemo(() => {
    const total = mangas.length;
    const reading = mangas.filter((m) => m.reading_status === 'reading').length;
    const completed = mangas.filter((m) => m.reading_status === 'completed').length;
    const planToRead = mangas.filter((m) => m.reading_status === 'plan_to_read').length;
    const totalSize = mangas.reduce((sum, m) => sum + (m.total_size_bytes || 0), 0);

    return { total, reading, completed, planToRead, totalSize };
  }, [mangas]);

  const formatSize = (bytes: number): string => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
  };

  const statCards = [
    {
      label: 'Total',
      value: stats.total,
      color: 'text-text',
      icon: Library,
    },
    {
      label: 'Reading',
      value: stats.reading,
      color: 'text-blue-400',
      icon: BookOpen,
    },
    {
      label: 'Completed',
      value: stats.completed,
      color: 'text-green-400',
      icon: CheckCircle2,
    },
    {
      label: 'Plan to Read',
      value: stats.planToRead,
      color: 'text-purple-400',
      icon: Star,
    },
    {
      label: 'Total Size',
      value: formatSize(stats.totalSize),
      color: 'text-cyan-400',
      icon: Folder,
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
      {statCards.map((stat) => {
        const Icon = stat.icon;
        return (
          <div
            key={stat.label}
            className="flex flex-col p-3 rounded-lg bg-surface-alt/50 border border-border-dim hover:border-secondary/50 transition-all duration-200"
          >
            <div className="flex items-center gap-2 mb-1">
              <Icon size={14} className={stat.color} />
              <span className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider">
                {stat.label}
              </span>
            </div>
            <span className={cn('text-2xl font-mono font-bold', stat.color)}>
              {stat.value}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Barre d'outils avec filtres, tri, recherche, et contrôles d'affichage.
 */
function LibraryToolbar({
  searchQuery,
  setSearchQuery,
  readingStatus,
  setReadingStatus,
  language,
  setLanguage,
  sortBy,
  setSortBy,
  sortOrder,
  toggleSortOrder,
  viewMode,
  setViewMode,
  showFilters,
  setShowFilters,
  totalCount,
  filteredCount,
  onScan,
  isScanning,
}: {
  searchQuery: string;
  setSearchQuery: (query: string) => void;
  readingStatus: ReadingStatus | 'all';
  setReadingStatus: (status: ReadingStatus | 'all') => void;
  language: LanguageCode | 'all';
  setLanguage: (lang: LanguageCode | 'all') => void;
  sortBy: MangaSortBy;
  setSortBy: (sort: MangaSortBy) => void;
  sortOrder: 'asc' | 'desc';
  toggleSortOrder: () => void;
  viewMode: LibraryViewMode;
  setViewMode: (mode: LibraryViewMode) => void;
  showFilters: boolean;
  setShowFilters: (show: boolean) => void;
  totalCount: number;
  filteredCount: number;
  onScan: () => void;
  isScanning: boolean;
}) {
  return (
    <div className="space-y-3">
      {/* Ligne 1 : Recherche et contrôles */}
      <div className="flex items-center gap-3 flex-wrap">
        {/* Barre de recherche */}
        <div className="flex-1 min-w-[200px] max-w-md">
          <SearchBar
            variant="inline"
            size="sm"
            value={searchQuery}
            onChange={setSearchQuery}
            placeholder="Search your library..."
            showSiteSelector={false}
            showFilters={false}
            showHistory={false}
            showPopular={false}
            showSuggestions={false}
          />
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

        {/* Contrôles d'affichage */}
        <div className="flex items-center gap-1 p-1 rounded-lg bg-surface-alt border border-border-dim">
          <button
            type="button"
            onClick={() => setViewMode('grid')}
            aria-label="Grid view"
            aria-pressed={viewMode === 'grid'}
            className={cn(
              'p-1.5 rounded',
              'transition-all duration-200',
              viewMode === 'grid'
                ? 'bg-primary-bg text-primary'
                : 'text-text-muted hover:text-secondary'
            )}
          >
            <LayoutGrid size={14} />
          </button>
          <button
            type="button"
            onClick={() => setViewMode('list')}
            aria-label="List view"
            aria-pressed={viewMode === 'list'}
            className={cn(
              'p-1.5 rounded',
              'transition-all duration-200',
              viewMode === 'list'
                ? 'bg-primary-bg text-primary'
                : 'text-text-muted hover:text-secondary'
            )}
          >
            <List size={14} />
          </button>
          <button
            type="button"
            onClick={() => setViewMode('compact')}
            aria-label="Compact view"
            aria-pressed={viewMode === 'compact'}
            className={cn(
              'p-1.5 rounded',
              'transition-all duration-200',
              viewMode === 'compact'
                ? 'bg-primary-bg text-primary'
                : 'text-text-muted hover:text-secondary'
            )}
          >
            <Grid3x3 size={14} />
          </button>
        </div>

        {/* Bouton scan */}
        <button
          type="button"
          onClick={onScan}
          disabled={isScanning}
          aria-label="Scan library"
          title="Scan library for new manga"
          className={cn(
            'flex items-center gap-1.5 px-3 py-2 rounded-lg',
            'bg-surface-alt text-text-muted border border-border-dim',
            'font-mono text-xs',
            'hover:bg-surface-hover hover:border-secondary hover:text-secondary',
            'transition-all duration-200',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
            'disabled:opacity-50 disabled:cursor-not-allowed'
          )}
        >
          {isScanning ? (
            <>
              <Loader2 size={14} className="animate-spin" />
              <span>Scanning...</span>
            </>
          ) : (
            <>
              <RefreshCw size={14} />
              <span>Scan</span>
            </>
          )}
        </button>
      </div>

      {/* Ligne 2 : Filtres (si visible) */}
      {showFilters && (
        <div className="flex flex-wrap items-center gap-3 p-3 rounded-lg bg-surface-alt/50 border border-border-dim animate-in fade-in slide-in-from-top-2 duration-200">
          {/* Filtre par statut de lecture */}
          <div className="flex items-center gap-2">
            <label className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider">
              Status
            </label>
            <div className="flex gap-1 flex-wrap">
              {READING_STATUS_OPTIONS.map((option) => {
                const Icon = option.icon;
                const isActive = readingStatus === option.id;
                return (
                  <button
                    key={option.id}
                    type="button"
                    onClick={() => setReadingStatus(option.id)}
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
                    <Icon size={10} className={isActive ? option.color : ''} />
                    <span>{option.label}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Filtre par langue */}
          <div className="flex items-center gap-2">
            <label className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider">
              Language
            </label>
            <div className="flex gap-1">
              {LANGUAGE_OPTIONS.map((lang) => {
                const isActive = language === lang.code;
                return (
                  <button
                    key={lang.code}
                    type="button"
                    onClick={() => setLanguage(lang.code)}
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
                    <span>{lang.flag}</span>
                    <span>{lang.label}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Tri */}
          <div className="flex items-center gap-2">
            <label className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider">
              Sort by
            </label>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as MangaSortBy)}
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

          {/* Compteur */}
          <div className="ml-auto text-xs font-mono text-text-dim">
            <span className="text-text font-semibold">{filteredCount}</span>
            <span> / {totalCount} manga</span>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Grille de mangas.
 */
function LibraryGrid({
  mangas,
  viewMode,
  onRead,
  onDownload,
  onToggleFavorite,
  onRemoveFromLibrary,
  onClick,
}: {
  mangas: Manga[];
  viewMode: LibraryViewMode;
  onRead: (manga: Manga) => void;
  onDownload: (manga: Manga) => void;
  onToggleFavorite: (manga: Manga) => void;
  onRemoveFromLibrary: (manga: Manga) => void;
  onClick: (manga: Manga) => void;
}) {
  if (viewMode === 'grid') {
    return (
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
        {mangas.map((manga) => (
          <MangaCard
            key={manga.id}
            manga={manga}
            variant="default"
            showProgress
            showStatus
            showLanguage
            showActions
            onClick={() => onClick(manga)}
            onRead={() => onRead(manga)}
            onDownload={() => onDownload(manga)}
            onToggleFavorite={() => onToggleFavorite(manga)}
            onRemoveFromLibrary={() => onRemoveFromLibrary(manga)}
          />
        ))}
      </div>
    );
  }

  if (viewMode === 'list') {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {mangas.map((manga) => (
          <MangaCard
            key={manga.id}
            manga={manga}
            variant="detailed"
            showProgress
            showStatus
            showLanguage
            showActions
            showDescription
            onClick={() => onClick(manga)}
            onRead={() => onRead(manga)}
            onDownload={() => onDownload(manga)}
            onToggleFavorite={() => onToggleFavorite(manga)}
            onRemoveFromLibrary={() => onRemoveFromLibrary(manga)}
          />
        ))}
      </div>
    );
  }

  // Compact
  return (
    <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 xl:grid-cols-10 gap-2">
      {mangas.map((manga) => (
        <MangaCard
          key={manga.id}
          manga={manga}
          variant="compact"
          showProgress
          progressPosition="ring"
          onClick={() => onClick(manga)}
          onRead={() => onRead(manga)}
        />
      ))}
    </div>
  );
}

// ============================================================================
// PAGE PRINCIPALE
// ============================================================================

/**
 * LibraryPage - Page de gestion de la bibliothèque locale.
 *
 * @returns Élément JSX de la page
 */
export default function LibraryPage() {
  const router = useRouter();

  // État local
  const [searchQuery, setSearchQuery] = useState('');
  const [readingStatus, setReadingStatus] = useState<ReadingStatus | 'all'>('all');
  const [language, setLanguage] = useState<LanguageCode | 'all'>('all');
  const [sortBy, setSortBy] = useState<MangaSortBy>('date_added');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const [showFilters, setShowFilters] = useState(false);
  const [isScanning, setIsScanning] = useState(false);

  // Préférences UI
  const { libraryViewMode, setLibraryViewMode } = useUIPreferencesWithActions();

  // Hooks de bibliothèque
  const {
    mangas,
    isLoading,
    error,
    fetchLibrary,
    scanLibrary,
    updateReadingStatus,
    removeFromLibrary,
    markChapterRead,
  } = useLibrary();

  // Écouter les événements WebSocket
  useLibraryWebSocket({
    onAdded: (data) => {
      toast.success('Manga added to library', {
        description: data.manga_title,
      });
      fetchLibrary();
    },
    onRemoved: (data) => {
      toast.info('Manga removed from library', {
        description: data.manga_title,
      });
      fetchLibrary();
    },
    onUpdated: () => {
      fetchLibrary();
    },
    onScan: (data) => {
      if (data.status === 'completed') {
        setIsScanning(false);
        toast.success('Library scan completed', {
          description: `Added ${data.added_mangas} manga(s)`,
        });
        fetchLibrary();
      } else if (data.status === 'failed') {
        setIsScanning(false);
        toast.error('Library scan failed');
      }
    },
  });

  // Charger la bibliothèque au montage
  useEffect(() => {
    fetchLibrary();
  }, [fetchLibrary]);

  // ==========================================================================
  // FILTRAGE ET TRI
  // ==========================================================================

  const filteredMangas = useMemo(() => {
    let result = [...mangas];

    // Filtre par recherche
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      result = result.filter(
        (manga) =>
          manga.title.toLowerCase().includes(query) ||
          manga.author.toLowerCase().includes(query) ||
          manga.tags.some((tag) => tag.toLowerCase().includes(query))
      );
    }

    // Filtre par statut de lecture
    if (readingStatus !== 'all') {
      result = result.filter((manga) => manga.reading_status === readingStatus);
    }

    // Filtre par langue
    if (language !== 'all') {
      result = result.filter((manga) => manga.language === language);
    }

    // Tri
    result.sort((a, b) => {
      let comparison = 0;

      switch (sortBy) {
        case 'title':
          comparison = a.title.localeCompare(b.title);
          break;
        case 'date_added':
          const dateA = a.added_at ? new Date(a.added_at).getTime() : 0;
          const dateB = b.added_at ? new Date(b.added_at).getTime() : 0;
          comparison = dateA - dateB;
          break;
        case 'last_read':
          const lastReadA = a.progress?.last_read_at
            ? new Date(a.progress.last_read_at).getTime()
            : 0;
          const lastReadB = b.progress?.last_read_at
            ? new Date(b.progress.last_read_at).getTime()
            : 0;
          comparison = lastReadA - lastReadB;
          break;
        case 'progress':
          const progressA = a.progress?.progress_percentage || 0;
          const progressB = b.progress?.progress_percentage || 0;
          comparison = progressA - progressB;
          break;
        case 'author':
          comparison = a.author.localeCompare(b.author);
          break;
        case 'status':
          comparison = (a.reading_status || '').localeCompare(b.reading_status || '');
          break;
      }

      return sortOrder === 'asc' ? comparison : -comparison;
    });

    return result;
  }, [mangas, searchQuery, readingStatus, language, sortBy, sortOrder]);

  // ==========================================================================
  // HANDLERS
  // ==========================================================================

  const handleToggleSortOrder = useCallback(() => {
    setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'));
  }, []);

  const handleScan = useCallback(async () => {
    setIsScanning(true);
    try {
      await scanLibrary();
    } catch (error) {
      toast.error('Failed to scan library');
      setIsScanning(false);
    }
  }, [scanLibrary]);

  const handleRead = useCallback(
    (manga: Manga) => {
      router.push(`/manga/${manga.site_id}/${manga.id}/read`);
    },
    [router]
  );

  const handleDownload = useCallback(
    (manga: Manga) => {
      router.push(`/manga/${manga.site_id}/${manga.id}/download`);
    },
    [router]
  );

  const handleToggleFavorite = useCallback(
    (manga: Manga) => {
      // TODO: Implémenter la gestion des favoris
      toast.info('Favorites feature coming soon');
    },
    []
  );

  const handleRemoveFromLibrary = useCallback(
    async (manga: Manga) => {
      const confirmed = window.confirm(
        `Remove "${manga.title}" from your library?`
      );
      if (confirmed) {
        await removeFromLibrary(manga.id);
      }
    },
    [removeFromLibrary]
  );

  const handleClick = useCallback(
    (manga: Manga) => {
      router.push(`/manga/${manga.site_id}/${manga.id}`);
    },
    [router]
  );

  // ==========================================================================
  // RENDU
  // ==========================================================================

  return (
    <div className="container mx-auto px-4 py-6 max-w-7xl">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-3xl font-mono font-bold text-primary tracking-wider mb-1">
              Library
            </h1>
            <p className="text-sm font-mono text-text-muted">
              Your personal manga collection
            </p>
          </div>
        </div>

        {/* Statistiques */}
        <LibraryStats mangas={mangas} />
      </div>

      {/* Barre d'outils */}
      <div className="mb-6">
        <LibraryToolbar
          searchQuery={searchQuery}
          setSearchQuery={setSearchQuery}
          readingStatus={readingStatus}
          setReadingStatus={setReadingStatus}
          language={language}
          setLanguage={setLanguage}
          sortBy={sortBy}
          setSortBy={setSortBy}
          sortOrder={sortOrder}
          toggleSortOrder={handleToggleSortOrder}
          viewMode={libraryViewMode as LibraryViewMode}
          setViewMode={(mode) => setLibraryViewMode(mode as any)}
          showFilters={showFilters}
          setShowFilters={setShowFilters}
          totalCount={mangas.length}
          filteredCount={filteredMangas.length}
          onScan={handleScan}
          isScanning={isScanning}
        />
      </div>

      {/* Erreur de chargement */}
      {error && (
        <div
          role="alert"
          className="mb-4 flex items-start gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/30"
        >
          <AlertCircle size={16} className="text-red-400 flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-xs font-mono font-semibold text-red-400">Error</p>
            <p className="text-xs font-mono text-red-300/80 mt-0.5">{error}</p>
          </div>
          <button
            type="button"
            onClick={() => fetchLibrary()}
            className="text-xs font-mono text-red-400 hover:text-red-300 transition-colors"
          >
            Retry
          </button>
        </div>
      )}

      {/* Contenu principal */}
      {isLoading && mangas.length === 0 ? (
        <MangaCardSkeletonGrid variant={libraryViewMode as any} count={12} />
      ) : filteredMangas.length === 0 ? (
        <MangaCardEmpty
          title={searchQuery ? 'No manga found' : 'Your library is empty'}
          description={
            searchQuery
              ? 'Try adjusting your search or filters'
              : 'Start by searching and adding manga to your library'
          }
          action={
            !searchQuery && (
              <button
                type="button"
                onClick={() => router.push('/search')}
                className={cn(
                  'flex items-center gap-2 px-4 py-2 rounded-lg',
                  'bg-primary text-background',
                  'font-mono font-bold text-sm',
                  'hover:bg-primary-bright hover:shadow-[0_0_15px_rgba(0,255,65,0.5)]',
                  'transition-all duration-200'
                )}
              >
                <Search size={16} />
                <span>Search Manga</span>
              </button>
            )
          }
        />
      ) : (
        <LibraryGrid
          mangas={filteredMangas}
          viewMode={libraryViewMode as LibraryViewMode}
          onRead={handleRead}
          onDownload={handleDownload}
          onToggleFavorite={handleToggleFavorite}
          onRemoveFromLibrary={handleRemoveFromLibrary}
          onClick={handleClick}
        />
      )}

      {/* Footer */}
      <div className="mt-8 pt-6 border-t border-border-dim">
        <div className="flex items-center justify-between text-xs font-mono text-text-dim">
          <div className="flex items-center gap-4">
            <span>
              <span className="text-text font-semibold">{filteredMangas.length}</span> manga shown
            </span>
            {readingStatus !== 'all' && (
              <span>
                Filtered by:{' '}
                <span className="text-primary capitalize">{readingStatus.replace(/_/g, ' ')}</span>
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1">
              <Library size={12} className="text-primary" />
              <span>Local Library</span>
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
