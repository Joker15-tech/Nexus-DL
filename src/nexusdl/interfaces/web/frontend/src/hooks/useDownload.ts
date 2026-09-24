/**
 * Hooks React pour la gestion des téléchargements dans NexusDL.
 *
 * Ce module fournit une collection de hooks React personnalisés pour interagir
 * avec le store de téléchargements et l'API backend de manière simple et type-safe.
 *
 * Architecture :
 *   hooks/useDownload.ts
 *   ├── useDownload              : Hook principal (état + actions)
 *   ├── useDownloadTask          : Hook pour une tâche spécifique
 *   ├── useDownloadActions       : Hook pour les actions uniquement
 *   ├── useDownloadStats         : Hook pour les statistiques
 *   ├── useDownloadList          : Hook pour la liste avec filtrage/tri
 *   ├── useBulkDownloadActions   : Hook pour les opérations bulk
 *   ├── useDownloadPolling       : Hook pour le polling automatique
 *   └── useDownloadNotifications : Hook pour les notifications toast
 *
 * Utilisation :
 *   // Hook principal
 *   const { tasks, stats, actions } = useDownload();
 *
 *   // Tâche spécifique
 *   const { task, pause, resume } = useDownloadTask('task_123');
 *
 *   // Actions uniquement
 *   const { createTask, pauseAll } = useDownloadActions();
 *
 *   // Liste filtrée
 *   const { tasks, pagination, filters } = useDownloadList({
 *     status: 'active',
 *     sortBy: 'progress',
 *   });
 *
 * @module hooks/useDownload
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

import { api } from '@/lib/api';
import type { ApiError } from '@/lib/api';

import {
  useDownloadStore,
  selectAllTasks,
  selectActiveTasks,
  selectPendingTasks,
  selectCompletedTasks,
  selectFailedTasks,
  selectPausedTasks,
  selectTaskById,
  selectStats,
  selectIsLoading,
  selectError,
  selectTaskCounts,
  initializeWebSocketListeners,
} from '@/store/downloads';

import type {
  DownloadTask,
  DownloadStats,
  CreateDownloadRequest,
  DownloadListParams,
  DownloadStatus,
  DownloadFilter,
  DownloadSortBy,
} from '@/types/download';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Options pour le hook useDownload.
 */
export interface UseDownloadOptions {
  /** Charger automatiquement les tâches au montage. */
  autoLoad?: boolean;
  /** Charger automatiquement les statistiques. */
  autoLoadStats?: boolean;
  /** Initialiser les listeners WebSocket. */
  enableWebSocket?: boolean;
}

/**
 * Retour du hook useDownload.
 */
export interface UseDownloadReturn {
  /** Liste de toutes les tâches. */
  tasks: DownloadTask[];
  /** Statistiques globales. */
  stats: DownloadStats | null;
  /** Indique si une opération est en cours. */
  isLoading: boolean;
  /** Dernier message d'erreur. */
  error: string | null;
  /** Actions disponibles. */
  actions: {
    fetchTasks: (params?: DownloadListParams) => Promise<void>;
    fetchStats: () => Promise<void>;
    createTask: (request: CreateDownloadRequest) => Promise<DownloadTask>;
    pauseTask: (taskId: string) => Promise<void>;
    resumeTask: (taskId: string) => Promise<void>;
    cancelTask: (taskId: string) => Promise<void>;
    retryTask: (taskId: string) => Promise<void>;
    deleteTask: (taskId: string) => Promise<void>;
    pauseAll: () => Promise<void>;
    resumeAll: () => Promise<void>;
    clearCompleted: () => Promise<void>;
    clearError: () => void;
  };
}

/**
 * Options pour le hook useDownloadTask.
 */
export interface UseDownloadTaskOptions {
  /** Rafraîchir automatiquement la tâche. */
  autoRefresh?: boolean;
  /** Intervalle de rafraîchissement en ms. */
  refreshInterval?: number;
}

/**
 * Retour du hook useDownloadTask.
 */
export interface UseDownloadTaskReturn {
  /** La tâche demandée (ou null si non trouvée). */
  task: DownloadTask | null;
  /** Indique si la tâche est en cours de chargement. */
  isLoading: boolean;
  /** Actions spécifiques à la tâche. */
  actions: {
    pause: () => Promise<void>;
    resume: () => Promise<void>;
    cancel: () => Promise<void>;
    retry: () => Promise<void>;
    delete: () => Promise<void>;
    refresh: () => Promise<void>;
  };
}

/**
 * Options pour le hook useDownloadList.
 */
export interface UseDownloadListOptions {
  /** Filtre de statut. */
  status?: DownloadFilter;
  /** Critère de tri. */
  sortBy?: DownloadSortBy;
  /** Ordre de tri. */
  sortOrder?: 'asc' | 'desc';
  /** Numéro de page. */
  page?: number;
  /** Taille de page. */
  pageSize?: number;
  /** Charger automatiquement. */
  autoLoad?: boolean;
}

/**
 * Retour du hook useDownloadList.
 */
export interface UseDownloadListReturn {
  /** Liste des tâches filtrées. */
  tasks: DownloadTask[];
  /** Nombre total de tâches. */
  total: number;
  /** Page actuelle. */
  page: number;
  /** Taille de page. */
  pageSize: number;
  /** Indique s'il y a une page suivante. */
  hasNext: boolean;
  /** Indique s'il y a une page précédente. */
  hasPrevious: boolean;
  /** Indique si les données sont en cours de chargement. */
  isLoading: boolean;
  /** Erreur éventuelle. */
  error: string | null;
  /** Fonction pour changer de page. */
  setPage: (page: number) => void;
  /** Fonction pour changer la taille de page. */
  setPageSize: (size: number) => void;
  /** Fonction pour rafraîchir les données. */
  refresh: () => Promise<void>;
}

/**
 * Options pour le hook useDownloadPolling.
 */
export interface UseDownloadPollingOptions {
  /** Intervalle de polling pour les tâches (ms). */
  tasksInterval?: number;
  /** Intervalle de polling pour les stats (ms). */
  statsInterval?: number;
  /** Activer le polling. */
  enabled?: boolean;
}

// ============================================================================
// HOOK PRINCIPAL - useDownload
// ============================================================================

/**
 * Hook principal pour gérer les téléchargements.
 *
 * Fournit un accès complet à l'état et aux actions du store de téléchargements,
 * avec option d'initialisation automatique des listeners WebSocket.
 *
 * @param options - Options de configuration
 * @returns Objet avec l'état et les actions
 *
 * @example
 * ```tsx
 * function DownloadsPage() {
 *   const { tasks, stats, isLoading, actions } = useDownload({
 *     autoLoad: true,
 *     enableWebSocket: true,
 *   });
 *
 *   return (
 *     <div>
 *       <h1>Downloads ({stats?.active_tasks || 0} active)</h1>
 *       {isLoading ? (
 *         <LoadingSpinner />
 *       ) : (
 *         tasks.map((task) => (
 *           <DownloadItem key={task.id} task={task} />
 *         ))
 *       )}
 *     </div>
 *   );
 * }
 * ```
 */
export function useDownload(options: UseDownloadOptions = {}): UseDownloadReturn {
  const {
    autoLoad = true,
    autoLoadStats = true,
    enableWebSocket = true,
  } = options;

  const tasks = useDownloadStore(selectAllTasks);
  const stats = useDownloadStore(selectStats);
  const isLoading = useDownloadStore(selectIsLoading);
  const error = useDownloadStore(selectError);
  const actions = useDownloadStore((state) => state.actions);

  // Initialiser les listeners WebSocket
  useEffect(() => {
    if (!enableWebSocket) return;

    const cleanup = initializeWebSocketListeners();
    return cleanup;
  }, [enableWebSocket]);

  // Charger automatiquement les tâches
  useEffect(() => {
    if (autoLoad) {
      actions.fetchTasks();
    }
  }, [autoLoad, actions]);

  // Charger automatiquement les statistiques
  useEffect(() => {
    if (autoLoadStats) {
      actions.fetchStats();
    }
  }, [autoLoadStats, actions]);

  return {
    tasks,
    stats,
    isLoading,
    error,
    actions,
  };
}

// ============================================================================
// HOOK - useDownloadTask
// ============================================================================

/**
 * Hook pour accéder à une tâche de téléchargement spécifique.
 *
 * @param taskId - ID de la tâche
 * @param options - Options de configuration
 * @returns Objet avec la tâche et les actions spécifiques
 *
 * @example
 * ```tsx
 * function DownloadItem({ taskId }: { taskId: string }) {
 *   const { task, isLoading, actions } = useDownloadTask(taskId, {
 *     autoRefresh: true,
 *     refreshInterval: 5000,
 *   });
 *
 *   if (!task) return <div>Task not found</div>;
 *
 *   return (
 *     <div>
 *       <h2>{task.manga_title}</h2>
 *       <progress value={task.progress} max={1} />
 *       <button onClick={actions.pause}>Pause</button>
 *       <button onClick={actions.resume}>Resume</button>
 *     </div>
 *   );
 * }
 * ```
 */
export function useDownloadTask(
  taskId: string,
  options: UseDownloadTaskOptions = {}
): UseDownloadTaskReturn {
  const { autoRefresh = false, refreshInterval = 5000 } = options;

  const task = useDownloadStore(selectTaskById(taskId));
  const isLoading = useDownloadStore(selectIsLoading);
  const actions = useDownloadStore((state) => state.actions);

  // Rafraîchir automatiquement si demandé
  useEffect(() => {
    if (!autoRefresh || !task) return;

    const interval = setInterval(() => {
      actions.fetchTasks();
    }, refreshInterval);

    return () => clearInterval(interval);
  }, [autoRefresh, refreshInterval, task, actions]);

  const pause = useCallback(async () => {
    await actions.pauseTask(taskId);
  }, [actions, taskId]);

  const resume = useCallback(async () => {
    await actions.resumeTask(taskId);
  }, [actions, taskId]);

  const cancel = useCallback(async () => {
    await actions.cancelTask(taskId);
  }, [actions, taskId]);

  const retry = useCallback(async () => {
    await actions.retryTask(taskId);
  }, [actions, taskId]);

  const deleteTask = useCallback(async () => {
    await actions.deleteTask(taskId);
  }, [actions, taskId]);

  const refresh = useCallback(async () => {
    await actions.fetchTasks();
  }, [actions]);

  return {
    task: task || null,
    isLoading,
    actions: {
      pause,
      resume,
      cancel,
      retry,
      delete: deleteTask,
      refresh,
    },
  };
}

// ============================================================================
// HOOK - useDownloadActions
// ============================================================================

/**
 * Hook pour accéder uniquement aux actions de téléchargement.
 *
 * Utile quand on a besoin des actions sans l'état complet.
 *
 * @returns Objet avec toutes les actions
 *
 * @example
 * ```tsx
 * function CreateDownloadButton() {
 *   const { createTask } = useDownloadActions();
 *
 *   const handleClick = async () => {
 *     await createTask({
 *       site_id: 'mangadex',
 *       manga_id: '123',
 *       format: 'cbz',
 *     });
 *   };
 *
 *   return <button onClick={handleClick}>Download</button>;
 * }
 * ```
 */
export function useDownloadActions() {
  const actions = useDownloadStore((state) => state.actions);

  return {
    fetchTasks: actions.fetchTasks,
    fetchStats: actions.fetchStats,
    createTask: actions.createTask,
    pauseTask: actions.pauseTask,
    resumeTask: actions.resumeTask,
    cancelTask: actions.cancelTask,
    retryTask: actions.retryTask,
    deleteTask: actions.deleteTask,
    pauseAll: actions.pauseAll,
    resumeAll: actions.resumeAll,
    clearCompleted: actions.clearCompleted,
    clearError: actions.clearError,
  };
}

// ============================================================================
// HOOK - useDownloadStats
// ============================================================================

/**
 * Hook pour accéder aux statistiques de téléchargement.
 *
 * @returns Statistiques globales
 *
 * @example
 * ```tsx
 * function DownloadStats() {
 *   const stats = useDownloadStats();
 *
 *   if (!stats) return null;
 *
 *   return (
 *     <div>
 *       <p>Active: {stats.active_tasks}</p>
 *       <p>Completed: {stats.completed_tasks}</p>
 *       <p>Speed: {formatBytes(stats.average_speed_bytes_per_sec)}/s</p>
 *     </div>
 *   );
 * }
 * ```
 */
export function useDownloadStats(): DownloadStats | null {
  return useDownloadStore(selectStats);
}

// ============================================================================
// HOOK - useDownloadList
// ============================================================================

/**
 * Hook pour afficher une liste de téléchargements avec filtrage et pagination.
 *
 * @param options - Options de filtrage et pagination
 * @returns Objet avec la liste, la pagination et les contrôles
 *
 * @example
 * ```tsx
 * function DownloadsList() {
 *   const {
 *     tasks,
 *     total,
 *     page,
 *     hasNext,
 *     hasPrevious,
 *     setPage,
 *     isLoading,
 *   } = useDownloadList({
 *     status: 'active',
 *     sortBy: 'progress',
 *     sortOrder: 'desc',
 *     pageSize: 20,
 *   });
 *
 *   return (
 *     <div>
 *       {tasks.map((task) => (
 *         <DownloadItem key={task.id} task={task} />
 *       ))}
 *
 *       <Pagination
 *         page={page}
 *         total={total}
 *         hasNext={hasNext}
 *         hasPrevious={hasPrevious}
 *         onPageChange={setPage}
 *       />
 *     </div>
 *   );
 * }
 * ```
 */
export function useDownloadList(options: UseDownloadListOptions = {}): UseDownloadListReturn {
  const {
    status,
    sortBy = 'date_added',
    sortOrder = 'desc',
    page: initialPage = 1,
    pageSize: initialPageSize = 20,
    autoLoad = true,
  } = options;

  const [page, setPageState] = useState(initialPage);
  const [pageSize, setPageSizeState] = useState(initialPageSize);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const allTasks = useDownloadStore(selectAllTasks);
  const actions = useDownloadStore((state) => state.actions);

  // Filtrer les tâches selon le statut
  const filteredTasks = useMemo(() => {
    if (!status || status === 'all') return allTasks;

    switch (status) {
      case 'active':
        return selectActiveTasks({ tasks: allTasks } as any);
      case 'pending':
        return selectPendingTasks({ tasks: allTasks } as any);
      case 'completed':
        return selectCompletedTasks({ tasks: allTasks } as any);
      case 'failed':
        return selectFailedTasks({ tasks: allTasks } as any);
      case 'paused':
        return selectPausedTasks({ tasks: allTasks } as any);
      default:
        return allTasks;
    }
  }, [allTasks, status]);

  // Trier les tâches
  const sortedTasks = useMemo(() => {
    const sorted = [...filteredTasks];

    sorted.sort((a, b) => {
      let aValue: any;
      let bValue: any;

      switch (sortBy) {
        case 'date_added':
          aValue = new Date(a.created_at).getTime();
          bValue = new Date(b.created_at).getTime();
          break;
        case 'progress':
          aValue = a.progress;
          bValue = b.progress;
          break;
        case 'size':
          aValue = a.total_size_bytes;
          bValue = b.total_size_bytes;
          break;
        case 'name':
          aValue = a.manga_title.toLowerCase();
          bValue = b.manga_title.toLowerCase();
          break;
        case 'priority':
          const priorityOrder = { low: 0, normal: 1, high: 2, urgent: 3 };
          aValue = priorityOrder[a.priority as keyof typeof priorityOrder];
          bValue = priorityOrder[b.priority as keyof typeof priorityOrder];
          break;
        case 'status':
          aValue = a.status;
          bValue = b.status;
          break;
        default:
          return 0;
      }

      if (sortOrder === 'asc') {
        return aValue < bValue ? -1 : aValue > bValue ? 1 : 0;
      } else {
        return aValue > bValue ? -1 : aValue < bValue ? 1 : 0;
      }
    });

    return sorted;
  }, [filteredTasks, sortBy, sortOrder]);

  // Paginer les tâches
  const paginatedTasks = useMemo(() => {
    const start = (page - 1) * pageSize;
    const end = start + pageSize;
    return sortedTasks.slice(start, end);
  }, [sortedTasks, page, pageSize]);

  // Calculer la pagination
  const total = sortedTasks.length;
  const totalPages = Math.ceil(total / pageSize);
  const hasNext = page < totalPages;
  const hasPrevious = page > 1;

  // Charger automatiquement
  useEffect(() => {
    if (autoLoad) {
      actions.fetchTasks();
    }
  }, [autoLoad, actions]);

  const setPage = useCallback((newPage: number) => {
    setPageState(Math.max(1, Math.min(newPage, totalPages)));
  }, [totalPages]);

  const setPageSize = useCallback((newSize: number) => {
    setPageSizeState(newSize);
    setPageState(1); // Reset to first page
  }, []);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      await actions.fetchTasks();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to refresh');
    } finally {
      setIsLoading(false);
    }
  }, [actions]);

  return {
    tasks: paginatedTasks,
    total,
    page,
    pageSize,
    hasNext,
    hasPrevious,
    isLoading,
    error,
    setPage,
    setPageSize,
    refresh,
  };
}

// ============================================================================
// HOOK - useBulkDownloadActions
// ============================================================================

/**
 * Hook pour les opérations bulk sur les téléchargements.
 *
 * @returns Objet avec les actions bulk
 *
 * @example
 * ```tsx
 * function BulkActions({ selectedTaskIds }: { selectedTaskIds: string[] }) {
 *   const { pauseSelected, resumeSelected, cancelSelected, deleteSelected } = useBulkDownloadActions();
 *
 *   return (
 *     <div>
 *       <button onClick={() => pauseSelected(selectedTaskIds)}>Pause Selected</button>
 *       <button onClick={() => resumeSelected(selectedTaskIds)}>Resume Selected</button>
 *       <button onClick={() => cancelSelected(selectedTaskIds)}>Cancel Selected</button>
 *       <button onClick={() => deleteSelected(selectedTaskIds)}>Delete Selected</button>
 *     </div>
 *   );
 * }
 * ```
 */
export function useBulkDownloadActions() {
  const actions = useDownloadStore((state) => state.actions);

  const pauseSelected = useCallback(
    async (taskIds: string[]) => {
      const promises = taskIds.map((id) => actions.pauseTask(id));
      await Promise.allSettled(promises);
      toast.success(`Paused ${taskIds.length} download(s)`);
    },
    [actions]
  );

  const resumeSelected = useCallback(
    async (taskIds: string[]) => {
      const promises = taskIds.map((id) => actions.resumeTask(id));
      await Promise.allSettled(promises);
      toast.success(`Resumed ${taskIds.length} download(s)`);
    },
    [actions]
  );

  const cancelSelected = useCallback(
    async (taskIds: string[]) => {
      const promises = taskIds.map((id) => actions.cancelTask(id));
      await Promise.allSettled(promises);
      toast.success(`Cancelled ${taskIds.length} download(s)`);
    },
    [actions]
  );

  const deleteSelected = useCallback(
    async (taskIds: string[]) => {
      const promises = taskIds.map((id) => actions.deleteTask(id));
      await Promise.allSettled(promises);
      toast.success(`Deleted ${taskIds.length} download(s)`);
    },
    [actions]
  );

  return {
    pauseSelected,
    resumeSelected,
    cancelSelected,
    deleteSelected,
    pauseAll: actions.pauseAll,
    resumeAll: actions.resumeAll,
    clearCompleted: actions.clearCompleted,
  };
}

// ============================================================================
// HOOK - useDownloadPolling
// ============================================================================

/**
 * Hook pour le polling automatique des téléchargements.
 *
 * Utile quand WebSocket n'est pas disponible ou en fallback.
 *
 * @param options - Options de polling
 *
 * @example
 * ```tsx
 * function DownloadsPage() {
 *   useDownloadPolling({
 *     tasksInterval: 10000,
 *     statsInterval: 5000,
 *     enabled: true,
 *   });
 *
 *   return <div>...</div>;
 * }
 * ```
 */
export function useDownloadPolling(options: UseDownloadPollingOptions = {}) {
  const {
    tasksInterval = 10000,
    statsInterval = 5000,
    enabled = true,
  } = options;

  const actions = useDownloadStore((state) => state.actions);

  useEffect(() => {
    if (!enabled) return;

    // Polling pour les tâches
    const tasksTimer = setInterval(() => {
      actions.fetchTasks();
    }, tasksInterval);

    // Polling pour les stats
    const statsTimer = setInterval(() => {
      actions.fetchStats();
    }, statsInterval);

    return () => {
      clearInterval(tasksTimer);
      clearInterval(statsTimer);
    };
  }, [enabled, tasksInterval, statsInterval, actions]);
}

// ============================================================================
// HOOK - useDownloadNotifications
// ============================================================================

/**
 * Hook pour afficher des notifications toast basées sur les événements de téléchargement.
 *
 * @param options - Options de configuration
 *
 * @example
 * ```tsx
 * function App() {
 *   useDownloadNotifications({
 *     showProgress: true,
 *     showCompletion: true,
 *     showErrors: true,
 *   });
 *
 *   return <div>...</div>;
 * }
 * ```
 */
export function useDownloadNotifications(options: {
  showProgress?: boolean;
  showCompletion?: boolean;
  showErrors?: boolean;
} = {}) {
  const {
    showProgress = false,
    showCompletion = true,
    showErrors = true,
  } = options;

  const tasks = useDownloadStore(selectAllTasks);
  const previousTasksRef = useRef<DownloadTask[]>([]);

  useEffect(() => {
    const previousTasks = previousTasksRef.current;

    tasks.forEach((task) => {
      const previousTask = previousTasks.find((t) => t.id === task.id);

      // Notification de complétion
      if (
        showCompletion &&
        previousTask &&
        previousTask.status !== 'completed' &&
        task.status === 'completed'
      ) {
        toast.success('Download completed', {
          description: task.manga_title,
        });
      }

      // Notification d'erreur
      if (
        showErrors &&
        previousTask &&
        previousTask.status !== 'failed' &&
        task.status === 'failed'
      ) {
        toast.error('Download failed', {
          description: `${task.manga_title}: ${task.error_message || 'Unknown error'}`,
        });
      }

      // Notification de progression (optionnel, peut être verbeux)
      if (showProgress && task.status === 'downloading') {
        const progressPercent = Math.round(task.progress * 100);
        if (progressPercent % 25 === 0) {
          // Notifier tous les 25%
          toast.info(`Downloading: ${progressPercent}%`, {
            description: task.manga_title,
            duration: 2000,
          });
        }
      }
    });

    previousTasksRef.current = tasks;
  }, [tasks, showProgress, showCompletion, showErrors]);
}

// ============================================================================
// HOOK - useDownloadCounts
// ============================================================================

/**
 * Hook pour obtenir les compteurs de tâches par statut.
 *
 * @returns Objet avec les compteurs
 *
 * @example
 * ```tsx
 * function DownloadBadge() {
 *   const counts = useDownloadCounts();
 *
 *   return (
 *     <span>
 *       {counts.active} active, {counts.pending} pending
 *     </span>
 *   );
 * }
 * ```
 */
export function useDownloadCounts() {
  return useDownloadStore(selectTaskCounts);
}

// ============================================================================
// HOOK - useDownloadFiltered
// ============================================================================

/**
 * Hook pour obtenir les tâches filtrées par statut.
 *
 * @param filter - Filtre de statut
 * @returns Liste des tâches filtrées
 *
 * @example
 * ```tsx
 * function ActiveDownloads() {
 *   const tasks = useDownloadFiltered('active');
 *
 *   return (
 *     <div>
 *       {tasks.map((task) => (
 *         <DownloadItem key={task.id} task={task} />
 *       ))}
 *     </div>
 *   );
 * }
 * ```
 */
export function useDownloadFiltered(filter: DownloadFilter): DownloadTask[] {
  const tasks = useDownloadStore(selectAllTasks);

  return useMemo(() => {
    if (filter === 'all') return tasks;

    switch (filter) {
      case 'active':
        return tasks.filter((t) => t.status === 'running' || t.status === 'downloading');
      case 'pending':
        return tasks.filter((t) => t.status === 'pending' || t.status === 'queued');
      case 'completed':
        return tasks.filter((t) => t.status === 'completed');
      case 'failed':
        return tasks.filter((t) => t.status === 'failed');
      case 'cancelled':
        return tasks.filter((t) => t.status === 'cancelled');
      case 'paused':
        return tasks.filter((t) => t.status === 'paused');
      default:
        return tasks;
    }
  }, [tasks, filter]);
}

// ============================================================================
// HOOK - useDownloadQuickActions
// ============================================================================

/**
 * Hook pour les actions rapides sur une tâche.
 *
 * Combine plusieurs actions en une seule interface simplifiée.
 *
 * @param taskId - ID de la tâche
 * @returns Objet avec les actions rapides
 *
 * @example
 * ```tsx
 * function DownloadControls({ taskId }: { taskId: string }) {
 *   const { canPause, canResume, canCancel, canRetry, togglePause, cancel, retry } =
 *     useDownloadQuickActions(taskId);
 *
 *   return (
 *     <div>
 *       {(canPause || canResume) && (
 *         <button onClick={togglePause}>
 *           {canPause ? 'Pause' : 'Resume'}
 *         </button>
 *       )}
 *       {canCancel && <button onClick={cancel}>Cancel</button>}
 *       {canRetry && <button onClick={retry}>Retry</button>}
 *     </div>
 *   );
 * }
 * ```
 */
export function useDownloadQuickActions(taskId: string) {
  const task = useDownloadStore(selectTaskById(taskId));
  const actions = useDownloadStore((state) => state.actions);

  const canPause = task?.status === 'running' || task?.status === 'downloading';
  const canResume = task?.status === 'paused';
  const canCancel = task && !['completed', 'cancelled'].includes(task.status);
  const canRetry = task?.status === 'failed';

  const togglePause = useCallback(async () => {
    if (canPause) {
      await actions.pauseTask(taskId);
    } else if (canResume) {
      await actions.resumeTask(taskId);
    }
  }, [canPause, canResume, taskId, actions]);

  const cancel = useCallback(async () => {
    if (canCancel) {
      await actions.cancelTask(taskId);
    }
  }, [canCancel, taskId, actions]);

  const retry = useCallback(async () => {
    if (canRetry) {
      await actions.retryTask(taskId);
    }
  }, [canRetry, taskId, actions]);

  return {
    canPause: !!canPause,
    canResume: !!canResume,
    canCancel: !!canCancel,
    canRetry: !!canRetry,
    togglePause,
    cancel,
    retry,
  };
}

// ============================================================================
// HOOK - useDownloadProgress
// ============================================================================

/**
 * Hook pour suivre la progression d'une tâche.
 *
 * @param taskId - ID de la tâche
 * @returns Objet avec les informations de progression
 *
 * @example
 * ```tsx
 * function ProgressBar({ taskId }: { taskId: string }) {
 *   const { progress, pagesCompleted, pagesTotal, speed, eta } =
 *     useDownloadProgress(taskId);
 *
 *   return (
 *     <div>
 *       <progress value={progress} max={1} />
 *       <p>{pagesCompleted} / {pagesTotal} pages</p>
 *       <p>Speed: {formatBytes(speed)}/s</p>
 *       <p>ETA: {formatDuration(eta)}</p>
 *     </div>
 *   );
 * }
 * ```
 */
export function useDownloadProgress(taskId: string) {
  const task = useDownloadStore(selectTaskById(taskId));

  return useMemo(() => {
    if (!task) {
      return {
        progress: 0,
        pagesCompleted: 0,
        pagesTotal: 0,
        speed: 0,
        eta: 0,
        isDownloading: false,
      };
    }

    return {
      progress: task.progress,
      pagesCompleted: task.pages_completed,
      pagesTotal: task.pages_total,
      speed: task.speed_bytes_per_sec,
      eta: task.estimated_time_remaining_seconds,
      isDownloading: task.status === 'running' || task.status === 'downloading',
    };
  }, [task]);
}

// ============================================================================
// EXPORTS
// ============================================================================

export type {
  UseDownloadOptions,
  UseDownloadReturn,
  UseDownloadTaskOptions,
  UseDownloadTaskReturn,
  UseDownloadListOptions,
  UseDownloadListReturn,
  UseDownloadPollingOptions,
};
