/**
 * Page de gestion des téléchargements pour NexusDL.
 *
 * Interface complète pour gérer les tâches de téléchargement avec :
 *   - Liste paginée des tâches avec filtrage et tri
 *   - Statistiques globales (actives, en attente, terminées, échouées)
 *   - Actions bulk (pause all, resume all, clear completed)
 *   - Actions individuelles (pause, resume, cancel, retry, delete)
 *   - Sélection multiple pour opérations groupées
 *   - Événements WebSocket temps réel
 *   - Polling en fallback
 *   - Bandeau de statut de connexion
 *   - Skeleton loader pendant le chargement
 *   - État vide pour liste sans tâches
 *   - Style cyberpunk néon cohérent
 *   - Accessibilité complète
 *   - Responsive (mobile/desktop)
 *
 * @module app/downloads/page
 */

'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  AlertCircle,
  Ban,
  Check,
  CheckCircle2,
  ChevronDown,
  Clock,
  Download,
  Filter,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  SortAsc,
  SortDesc,
  Trash2,
  X,
  Zap,
} from 'lucide-react';
import { toast } from 'sonner';

import { cn } from '@/lib/utils';
import {
  useDownload,
  useDownloadList,
  useDownloadStats,
  useDownloadActions,
  useDownloadWebSocket,
  useConnectionBanner,
  useDownloadPolling,
} from '@/store';

import { DownloadItem, DownloadItemSkeletonList, DownloadItemEmpty } from '@/components/DownloadItem';
import { ProgressBar } from '@/components/ProgressBar';

import type { DownloadFilter, DownloadSortBy, DownloadTask } from '@/types/download';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Options de tri disponibles.
 */
const SORT_OPTIONS: Array<{
  id: DownloadSortBy;
  label: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
}> = [
  { id: 'date_added', label: 'Date Added', icon: Clock },
  { id: 'progress', label: 'Progress', icon: Zap },
  { id: 'size', label: 'Size', icon: Download },
  { id: 'name', label: 'Name', icon: SortAsc },
  { id: 'priority', label: 'Priority', icon: SortDesc },
  { id: 'status', label: 'Status', icon: CheckCircle2 },
];

/**
 * Options de filtre disponibles.
 */
const FILTER_OPTIONS: Array<{
  id: DownloadFilter;
  label: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
}> = [
  { id: 'all', label: 'All', icon: Download },
  { id: 'active', label: 'Active', icon: Zap },
  { id: 'pending', label: 'Pending', icon: Clock },
  { id: 'completed', label: 'Completed', icon: CheckCircle2 },
  { id: 'failed', label: 'Failed', icon: AlertCircle },
  { id: 'paused', label: 'Paused', icon: Pause },
];

// ============================================================================
// COMPOSANTS AUXILIAIRES
// ============================================================================

/**
 * Panneau de statistiques globales.
 */
function DownloadsStats() {
  const stats = useDownloadStats();

  if (!stats) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <div
            key={i}
            className="h-20 rounded-lg bg-surface-alt border border-border-dim animate-pulse"
          />
        ))}
      </div>
    );
  }

  const statCards = [
    {
      label: 'Total',
      value: stats.total_tasks,
      color: 'text-text',
      icon: Download,
    },
    {
      label: 'Active',
      value: stats.active_tasks,
      color: 'text-cyan-400',
      icon: Zap,
    },
    {
      label: 'Pending',
      value: stats.pending_tasks,
      color: 'text-yellow-400',
      icon: Clock,
    },
    {
      label: 'Completed',
      value: stats.completed_tasks,
      color: 'text-green-400',
      icon: CheckCircle2,
    },
    {
      label: 'Failed',
      value: stats.failed_tasks,
      color: 'text-red-400',
      icon: AlertCircle,
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
 * Barre d'outils avec filtres, tri, et actions bulk.
 */
function DownloadsToolbar({
  filter,
  setFilter,
  sortBy,
  setSortBy,
  sortOrder,
  toggleSortOrder,
  searchQuery,
  setSearchQuery,
  selectedCount,
  onClearSelection,
  onPauseAll,
  onResumeAll,
  onClearCompleted,
  isLoading,
}: {
  filter: DownloadFilter;
  setFilter: (filter: DownloadFilter) => void;
  sortBy: DownloadSortBy;
  setSortBy: (sortBy: DownloadSortBy) => void;
  sortOrder: 'asc' | 'desc';
  toggleSortOrder: () => void;
  searchQuery: string;
  setSearchQuery: (query: string) => void;
  selectedCount: number;
  onClearSelection: () => void;
  onPauseAll: () => void;
  onResumeAll: () => void;
  onClearCompleted: () => void;
  isLoading: boolean;
}) {
  const [showFilters, setShowFilters] = useState(false);

  return (
    <div className="space-y-3">
      {/* Ligne 1 : Recherche et actions globales */}
      <div className="flex items-center gap-3 flex-wrap">
        {/* Champ de recherche */}
        <div className="relative flex-1 min-w-[200px] max-w-xs">
          <Search
            size={14}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-text-dim"
            aria-hidden="true"
          />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search downloads..."
            aria-label="Search downloads"
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

        {/* Actions globales */}
        <div className="flex items-center gap-2 ml-auto">
          <button
            type="button"
            onClick={onPauseAll}
            disabled={isLoading}
            aria-label="Pause all downloads"
            title="Pause all"
            className={cn(
              'flex items-center gap-1.5 px-3 py-2 rounded-lg',
              'bg-surface-alt text-orange-400 border border-orange-500/30',
              'font-mono text-xs',
              'hover:bg-orange-500/20 hover:border-orange-500',
              'transition-all duration-200',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-orange-500',
              'disabled:opacity-50 disabled:cursor-not-allowed'
            )}
          >
            <Pause size={14} />
            <span className="hidden sm:inline">Pause All</span>
          </button>

          <button
            type="button"
            onClick={onResumeAll}
            disabled={isLoading}
            aria-label="Resume all downloads"
            title="Resume all"
            className={cn(
              'flex items-center gap-1.5 px-3 py-2 rounded-lg',
              'bg-primary-bg text-primary border border-primary/30',
              'font-mono text-xs',
              'hover:bg-primary/20 hover:border-primary',
              'transition-all duration-200',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
              'disabled:opacity-50 disabled:cursor-not-allowed'
            )}
          >
            <Play size={14} fill="currentColor" />
            <span className="hidden sm:inline">Resume All</span>
          </button>

          <button
            type="button"
            onClick={onClearCompleted}
            disabled={isLoading}
            aria-label="Clear completed downloads"
            title="Clear completed"
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
            <Trash2 size={14} />
            <span className="hidden sm:inline">Clear Completed</span>
          </button>
        </div>
      </div>

      {/* Ligne 2 : Filtres et tri (si visible) */}
      {showFilters && (
        <div className="flex flex-wrap items-center gap-3 p-3 rounded-lg bg-surface-alt/50 border border-border-dim animate-in fade-in slide-in-from-top-2 duration-200">
          {/* Filtre par statut */}
          <div className="flex items-center gap-2">
            <label className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider">
              Status
            </label>
            <div className="flex gap-1 flex-wrap">
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

          {/* Tri */}
          <div className="flex items-center gap-2">
            <label className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider">
              Sort by
            </label>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as DownloadSortBy)}
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
        </div>
      )}

      {/* Ligne 3 : Actions de sélection (si des tâches sont sélectionnées) */}
      {selectedCount > 0 && (
        <div className="flex items-center gap-3 p-3 rounded-lg bg-primary-bg/30 border border-primary/30 animate-in fade-in slide-in-from-top-2 duration-200">
          <span className="text-sm font-mono text-primary font-semibold">
            {selectedCount} download{selectedCount > 1 ? 's' : ''} selected
          </span>

          <div className="flex items-center gap-2 ml-auto">
            <button
              type="button"
              onClick={onPauseAll}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-md',
                'bg-surface-alt text-orange-400 border border-orange-500/30',
                'font-mono text-xs',
                'hover:bg-orange-500/20 hover:border-orange-500',
                'transition-all duration-200'
              )}
            >
              <Pause size={12} />
              <span>Pause Selected</span>
            </button>

            <button
              type="button"
              onClick={onResumeAll}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-md',
                'bg-primary-bg text-primary border border-primary/30',
                'font-mono text-xs',
                'hover:bg-primary/20 hover:border-primary',
                'transition-all duration-200'
              )}
            >
              <Play size={12} fill="currentColor" />
              <span>Resume Selected</span>
            </button>

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
              <span>Clear Selection</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// PAGE PRINCIPALE
// ============================================================================

/**
 * DownloadsPage - Page de gestion des téléchargements.
 *
 * @returns Élément JSX de la page
 */
export default function DownloadsPage() {
  const router = useRouter();

  // État local
  const [filter, setFilter] = useState<DownloadFilter>('all');
  const [sortBy, setSortBy] = useState<DownloadSortBy>('date_added');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  // Hooks
  const { isLoading: isListLoading, error: listError } = useDownload();
  const { tasks, total, page, pageSize, hasNext, hasPrevious, setPage, setPageSize, refresh } =
    useDownloadList({
      status: filter,
      sortBy,
      sortOrder,
      pageSize: 20,
    });
  const stats = useDownloadStats();
  const actions = useDownloadActions();

  // Écouter les événements WebSocket
  useDownloadWebSocket({
    enableToasts: true,
  });

  // Polling en fallback
  useDownloadPolling({
    tasksInterval: 10000,
    statsInterval: 5000,
  });

  // Bandeau de statut de connexion
  const { isVisible: isBannerVisible, state: connectionState, message: bannerMessage, reconnect } =
    useConnectionBanner({
      showDelay: 2000,
    });

  // ==========================================================================
  // FILTRAGE LOCAL (recherche)
  // ==========================================================================

  const filteredTasks = useMemo(() => {
    if (!searchQuery.trim()) return tasks;

    const query = searchQuery.toLowerCase();
    return tasks.filter(
      (task) =>
        task.manga_title.toLowerCase().includes(query) ||
        task.site_id.toLowerCase().includes(query) ||
        task.id.toLowerCase().includes(query)
    );
  }, [tasks, searchQuery]);

  // ==========================================================================
  // HANDLERS
  // ==========================================================================

  const handleToggleSortOrder = useCallback(() => {
    setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'));
  }, []);

  const handlePauseTask = useCallback(
    async (taskId: string) => {
      await actions.pauseTask(taskId);
    },
    [actions]
  );

  const handleResumeTask = useCallback(
    async (taskId: string) => {
      await actions.resumeTask(taskId);
    },
    [actions]
  );

  const handleCancelTask = useCallback(
    async (taskId: string) => {
      await actions.cancelTask(taskId);
    },
    [actions]
  );

  const handleRetryTask = useCallback(
    async (taskId: string) => {
      await actions.retryTask(taskId);
    },
    [actions]
  );

  const handleDeleteTask = useCallback(
    async (taskId: string) => {
      await actions.deleteTask(taskId);
      // Retirer de la sélection si présent
      setSelectedIds((prev) => {
        const next = new Set(prev);
        next.delete(taskId);
        return next;
      });
    },
    [actions]
  );

  const handleOpenFolder = useCallback(
    (taskId: string) => {
      const task = tasks.find((t) => t.id === taskId);
      if (task?.output_path) {
        // TODO: Implémenter l'ouverture du dossier côté serveur
        toast.info('Opening folder', {
          description: task.output_path,
        });
      }
    },
    [tasks]
  );

  const handleViewDetails = useCallback(
    (taskId: string) => {
      router.push(`/downloads/${taskId}`);
    },
    [router]
  );

  const handlePauseAll = useCallback(async () => {
    await actions.pauseAll();
  }, [actions]);

  const handleResumeAll = useCallback(async () => {
    await actions.resumeAll();
  }, [actions]);

  const handleClearCompleted = useCallback(async () => {
    await actions.clearCompleted();
    setSelectedIds(new Set());
  }, [actions]);

  const handleToggleSelection = useCallback((taskId: string, selected: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (selected) {
        next.add(taskId);
      } else {
        next.delete(taskId);
      }
      return next;
    });
  }, []);

  const handleClearSelection = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  // ==========================================================================
  // RENDU
  // ==========================================================================

  return (
    <div className="container mx-auto px-4 py-6 max-w-7xl">
      {/* Bandeau de statut de connexion */}
      {isBannerVisible && (
        <div
          className={cn(
            'mb-4 flex items-center justify-between gap-3 p-3 rounded-lg border-2',
            connectionState === 'disconnected'
              ? 'bg-red-500/10 border-red-500/30 text-red-400'
              : 'bg-yellow-500/10 border-yellow-500/30 text-yellow-400'
          )}
          role="alert"
        >
          <div className="flex items-center gap-2">
            {connectionState === 'disconnected' ? (
              <AlertCircle size={16} />
            ) : (
              <Loader2 size={16} className="animate-spin" />
            )}
            <span className="text-sm font-mono">{bannerMessage}</span>
          </div>
          {connectionState === 'disconnected' && (
            <button
              type="button"
              onClick={reconnect}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-md',
                'bg-red-500/20 border border-red-500/50',
                'font-mono text-xs',
                'hover:bg-red-500/30',
                'transition-all duration-200'
              )}
            >
              <RefreshCw size={12} />
              <span>Reconnect</span>
            </button>
          )}
        </div>
      )}

      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-3xl font-mono font-bold text-primary tracking-wider mb-1">
              Downloads
            </h1>
            <p className="text-sm font-mono text-text-muted">
              Manage your download tasks
            </p>
          </div>

          {/* Bouton refresh */}
          <button
            type="button"
            onClick={refresh}
            disabled={isListLoading}
            aria-label="Refresh downloads"
            className={cn(
              'flex items-center gap-2 px-4 py-2 rounded-lg',
              'bg-surface-alt text-text-muted border border-border-dim',
              'font-mono text-sm',
              'hover:bg-surface-hover hover:border-secondary hover:text-secondary',
              'transition-all duration-200',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
              'disabled:opacity-50 disabled:cursor-not-allowed'
            )}
          >
            <RefreshCw size={16} className={isListLoading ? 'animate-spin' : ''} />
            <span>Refresh</span>
          </button>
        </div>

        {/* Statistiques */}
        <DownloadsStats />
      </div>

      {/* Barre d'outils */}
      <div className="mb-4">
        <DownloadsToolbar
          filter={filter}
          setFilter={setFilter}
          sortBy={sortBy}
          setSortBy={setSortBy}
          sortOrder={sortOrder}
          toggleSortOrder={handleToggleSortOrder}
          searchQuery={searchQuery}
          setSearchQuery={setSearchQuery}
          selectedCount={selectedIds.size}
          onClearSelection={handleClearSelection}
          onPauseAll={handlePauseAll}
          onResumeAll={handleResumeAll}
          onClearCompleted={handleClearCompleted}
          isLoading={isListLoading}
        />
      </div>

      {/* Erreur de chargement */}
      {listError && (
        <div
          role="alert"
          className="mb-4 flex items-start gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/30"
        >
          <AlertCircle size={16} className="text-red-400 flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-xs font-mono font-semibold text-red-400">Error</p>
            <p className="text-xs font-mono text-red-300/80 mt-0.5">{listError}</p>
          </div>
          <button
            type="button"
            onClick={refresh}
            className="text-xs font-mono text-red-400 hover:text-red-300 transition-colors"
          >
            Retry
          </button>
        </div>
      )}

      {/* Liste des tâches */}
      <div className="space-y-2">
        {isListLoading && tasks.length === 0 ? (
          <DownloadItemSkeletonList count={5} />
        ) : filteredTasks.length === 0 ? (
          <DownloadItemEmpty
            title="No downloads"
            description={
              searchQuery
                ? 'No downloads match your search'
                : filter !== 'all'
                  ? `No ${filter} downloads`
                  : 'Start downloading manga to see them here'
            }
            action={
              !searchQuery && filter === 'all' && (
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
          <>
            {filteredTasks.map((task) => (
              <DownloadItem
                key={task.id}
                task={task}
                selectable
                isSelected={selectedIds.has(task.id)}
                onSelectionChange={handleToggleSelection}
                onPause={handlePauseTask}
                onResume={handleResumeTask}
                onCancel={handleCancelTask}
                onRetry={handleRetryTask}
                onDelete={handleDeleteTask}
                onOpenFolder={handleOpenFolder}
                onViewDetails={handleViewDetails}
                disabled={isListLoading}
              />
            ))}

            {/* Pagination */}
            {total > pageSize && (
              <div className="flex items-center justify-center gap-3 pt-6">
                <button
                  type="button"
                  onClick={() => setPage(page - 1)}
                  disabled={!hasPrevious}
                  aria-label="Previous page"
                  className={cn(
                    'flex items-center gap-2 px-4 py-2 rounded-lg',
                    'bg-surface-alt text-text-muted border border-border-dim',
                    'font-mono text-sm',
                    'hover:bg-surface-hover hover:border-secondary hover:text-secondary',
                    'transition-all duration-200',
                    'disabled:opacity-50 disabled:cursor-not-allowed'
                  )}
                >
                  <span>Previous</span>
                </button>

                <span className="text-sm font-mono text-text-muted">
                  Page {page} of {Math.ceil(total / pageSize)}
                </span>

                <button
                  type="button"
                  onClick={() => setPage(page + 1)}
                  disabled={!hasNext}
                  aria-label="Next page"
                  className={cn(
                    'flex items-center gap-2 px-4 py-2 rounded-lg',
                    'bg-surface-alt text-text-muted border border-border-dim',
                    'font-mono text-sm',
                    'hover:bg-surface-hover hover:border-secondary hover:text-secondary',
                    'transition-all duration-200',
                    'disabled:opacity-50 disabled:cursor-not-allowed'
                  )}
                >
                  <span>Next</span>
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {/* Footer avec infos */}
      <div className="mt-8 pt-6 border-t border-border-dim">
        <div className="flex items-center justify-between text-xs font-mono text-text-dim">
          <div className="flex items-center gap-4">
            <span>
              <span className="text-text font-semibold">{filteredTasks.length}</span> tasks shown
            </span>
            {selectedIds.size > 0 && (
              <span>
                <span className="text-primary font-semibold">{selectedIds.size}</span> selected
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1">
              <span
                className={cn(
                  'h-2 w-2 rounded-full',
                  connectionState === 'connected' ? 'bg-green-500' : 'bg-red-500'
                )}
              />
              <span>{connectionState === 'connected' ? 'Live' : 'Offline'}</span>
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
