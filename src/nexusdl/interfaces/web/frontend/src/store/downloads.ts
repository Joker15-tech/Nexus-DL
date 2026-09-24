/**
 * Store Zustand pour la gestion des téléchargements.
 *
 * Ce store centralise l'état des tâches de téléchargement, les statistiques
 * globales, et fournit toutes les actions nécessaires pour interagir avec
 * l'API REST backend et recevoir les mises à jour temps réel via WebSocket.
 *
 * Architecture :
 *   - State : tasks[], stats, isLoading, error
 *   - Actions : CRUD complet + bulk operations + WebSocket updates
 *   - Intégration : API REST (fetchTasks, createTask, etc.)
 *                  WebSocket (updateProgress, onTaskCompleted, etc.)
 *
 * @module store/downloads
 */

import { create } from 'zustand';
import { devtools, persist } from 'zustand/middleware';
import { toast } from 'sonner';

import type {
  DownloadTask,
  DownloadStats,
  DownloadStoreState,
  CreateDownloadRequest,
  DownloadProgressUpdate,
  DownloadCompletedUpdate,
  DownloadFailedUpdate,
  DownloadListParams,
  DownloadStatus,
} from '@/types/download';

import { api } from '@/lib/api';
import { wsClient } from '@/lib/ws';

// ============================================================================
// CONSTANTES
// ============================================================================

/** Intervalle de polling des statistiques (ms) quand le WebSocket n'est pas disponible. */
const STATS_POLL_INTERVAL = 5000;

/** Intervalle de polling de la liste des tâches (ms). */
const TASKS_POLL_INTERVAL = 10000;

// ============================================================================
// STORE PRINCIPAL
// ============================================================================

/**
 * Store Zustand pour les téléchargements.
 *
 * Utilise les middlewares :
 *   - devtools : intégration Redux DevTools pour le debug
 *   - persist : persistance locale de certaines préférences (filtres, tri)
 */
export const useDownloadStore = create<DownloadStoreState>()(
  devtools(
    (set, get) => ({
      // ====================================================================
      // ÉTAT INITIAL
      // ====================================================================
      tasks: [],
      stats: null,
      isLoading: false,
      error: null,

      // ====================================================================
      // ACTIONS
      // ====================================================================

      actions: {
        // ----------------------------------------------------------------
        // fetchTasks - Récupère la liste des tâches depuis l'API
        // ----------------------------------------------------------------
        fetchTasks: async (params?: DownloadListParams) => {
          set({ isLoading: true, error: null });

          try {
            const response = await api.get<{
              tasks: DownloadTask[];
              total: number;
              page: number;
              page_size: number;
            }>('/downloads', { params });

            set({
              tasks: response.tasks,
              isLoading: false,
            });
          } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to fetch downloads';
            console.error('[DownloadStore] fetchTasks error:', error);
            set({
              isLoading: false,
              error: message,
            });
            toast.error('Failed to load downloads', {
              description: message,
            });
          }
        },

        // ----------------------------------------------------------------
        // fetchStats - Récupère les statistiques globales
        // ----------------------------------------------------------------
        fetchStats: async () => {
          try {
            const stats = await api.get<DownloadStats>('/downloads/stats');
            set({ stats });
          } catch (error) {
            console.error('[DownloadStore] fetchStats error:', error);
            // Les stats ne sont pas critiques, on ne met pas l'état global en erreur
          }
        },

        // ----------------------------------------------------------------
        // createTask - Crée une nouvelle tâche de téléchargement
        // ----------------------------------------------------------------
        createTask: async (request: CreateDownloadRequest) => {
          set({ isLoading: true, error: null });

          try {
            const task = await api.post<DownloadTask>('/downloads', request);

            // Optimistic update : ajout immédiat de la tâche à la liste
            set((state) => ({
              tasks: [task, ...state.tasks],
              isLoading: false,
            }));

            toast.success('Download started', {
              description: task.manga_title || 'Task created successfully',
            });

            // Rafraîchir les stats après création
            get().actions.fetchStats();

            return task;
          } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to create download';
            console.error('[DownloadStore] createTask error:', error);
            set({
              isLoading: false,
              error: message,
            });
            toast.error('Failed to start download', {
              description: message,
            });
            throw error;
          }
        },

        // ----------------------------------------------------------------
        // pauseTask - Met en pause une tâche
        // ----------------------------------------------------------------
        pauseTask: async (taskId: string) => {
          // Optimistic update
          set((state) => ({
            tasks: state.tasks.map((task) =>
              task.id === taskId ? { ...task, status: 'paused' as DownloadStatus } : task
            ),
          }));

          try {
            await api.post(`/downloads/${taskId}/pause`);
            toast.success('Download paused');
            get().actions.fetchStats();
          } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to pause download';
            console.error('[DownloadStore] pauseTask error:', error);
            
            // Rollback en cas d'erreur
            get().actions.fetchTasks();
            
            toast.error('Failed to pause download', {
              description: message,
            });
          }
        },

        // ----------------------------------------------------------------
        // resumeTask - Reprend une tâche en pause
        // ----------------------------------------------------------------
        resumeTask: async (taskId: string) => {
          // Optimistic update
          set((state) => ({
            tasks: state.tasks.map((task) =>
              task.id === taskId ? { ...task, status: 'running' as DownloadStatus } : task
            ),
          }));

          try {
            await api.post(`/downloads/${taskId}/resume`);
            toast.success('Download resumed');
            get().actions.fetchStats();
          } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to resume download';
            console.error('[DownloadStore] resumeTask error:', error);
            
            // Rollback
            get().actions.fetchTasks();
            
            toast.error('Failed to resume download', {
              description: message,
            });
          }
        },

        // ----------------------------------------------------------------
        // cancelTask - Annule une tâche
        // ----------------------------------------------------------------
        cancelTask: async (taskId: string) => {
          // Optimistic update
          set((state) => ({
            tasks: state.tasks.map((task) =>
              task.id === taskId ? { ...task, status: 'cancelled' as DownloadStatus } : task
            ),
          }));

          try {
            await api.post(`/downloads/${taskId}/cancel`);
            toast.success('Download cancelled');
            get().actions.fetchStats();
          } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to cancel download';
            console.error('[DownloadStore] cancelTask error:', error);
            
            // Rollback
            get().actions.fetchTasks();
            
            toast.error('Failed to cancel download', {
              description: message,
            });
          }
        },

        // ----------------------------------------------------------------
        // retryTask - Relance une tâche échouée
        // ----------------------------------------------------------------
        retryTask: async (taskId: string) => {
          // Optimistic update
          set((state) => ({
            tasks: state.tasks.map((task) =>
              task.id === taskId
                ? { ...task, status: 'running' as DownloadStatus, error_message: null }
                : task
            ),
          }));

          try {
            await api.post(`/downloads/${taskId}/retry`);
            toast.success('Download retrying');
            get().actions.fetchStats();
          } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to retry download';
            console.error('[DownloadStore] retryTask error:', error);
            
            // Rollback
            get().actions.fetchTasks();
            
            toast.error('Failed to retry download', {
              description: message,
            });
          }
        },

        // ----------------------------------------------------------------
        // deleteTask - Supprime une tâche de la liste
        // ----------------------------------------------------------------
        deleteTask: async (taskId: string) => {
          // Optimistic update
          set((state) => ({
            tasks: state.tasks.filter((task) => task.id !== taskId),
          }));

          try {
            await api.delete(`/downloads/${taskId}`);
            toast.success('Download removed');
            get().actions.fetchStats();
          } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to delete download';
            console.error('[DownloadStore] deleteTask error:', error);
            
            // Rollback
            get().actions.fetchTasks();
            
            toast.error('Failed to delete download', {
              description: message,
            });
          }
        },

        // ----------------------------------------------------------------
        // pauseAll - Met en pause toutes les tâches actives
        // ----------------------------------------------------------------
        pauseAll: async () => {
          try {
            const response = await api.post<{ affected_count: number }>('/downloads/pause-all');
            
            // Optimistic update
            set((state) => ({
              tasks: state.tasks.map((task) =>
                task.status === 'running' || task.status === 'downloading'
                  ? { ...task, status: 'paused' as DownloadStatus }
                  : task
              ),
            }));

            toast.success('All downloads paused', {
              description: `${response.affected_count} task(s) affected`,
            });
            
            get().actions.fetchStats();
          } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to pause all downloads';
            console.error('[DownloadStore] pauseAll error:', error);
            toast.error('Failed to pause all downloads', {
              description: message,
            });
          }
        },

        // ----------------------------------------------------------------
        // resumeAll - Reprend toutes les tâches en pause
        // ----------------------------------------------------------------
        resumeAll: async () => {
          try {
            const response = await api.post<{ affected_count: number }>('/downloads/resume-all');
            
            // Optimistic update
            set((state) => ({
              tasks: state.tasks.map((task) =>
                task.status === 'paused'
                  ? { ...task, status: 'running' as DownloadStatus }
                  : task
              ),
            }));

            toast.success('All downloads resumed', {
              description: `${response.affected_count} task(s) affected`,
            });
            
            get().actions.fetchStats();
          } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to resume all downloads';
            console.error('[DownloadStore] resumeAll error:', error);
            toast.error('Failed to resume all downloads', {
              description: message,
            });
          }
        },

        // ----------------------------------------------------------------
        // clearCompleted - Supprime toutes les tâches terminées
        // ----------------------------------------------------------------
        clearCompleted: async () => {
          try {
            const response = await api.post<{ affected_count: number }>('/downloads/clear-completed');
            
            // Optimistic update
            set((state) => ({
              tasks: state.tasks.filter(
                (task) => task.status !== 'completed' && task.status !== 'failed' && task.status !== 'cancelled'
              ),
            }));

            toast.success('Completed downloads cleared', {
              description: `${response.affected_count} task(s) removed`,
            });
            
            get().actions.fetchStats();
          } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to clear completed downloads';
            console.error('[DownloadStore] clearCompleted error:', error);
            toast.error('Failed to clear completed downloads', {
              description: message,
            });
          }
        },

        // ----------------------------------------------------------------
        // updateProgress - Mise à jour locale depuis WebSocket
        // ----------------------------------------------------------------
        updateProgress: (update: DownloadProgressUpdate) => {
          set((state) => ({
            tasks: state.tasks.map((task) =>
              task.id === update.task_id
                ? {
                    ...task,
                    progress: update.progress,
                    pages_completed: update.pages_completed,
                    pages_total: update.pages_total,
                    speed_bytes_per_sec: update.speed_bytes_per_sec,
                    estimated_time_remaining_seconds: update.eta_seconds,
                    status: 'downloading' as DownloadStatus,
                  }
                : task
            ),
          }));
        },

        // ----------------------------------------------------------------
        // onTaskCompleted - Callback WebSocket pour tâche terminée
        // ----------------------------------------------------------------
        onTaskCompleted: (update: DownloadCompletedUpdate) => {
          set((state) => ({
            tasks: state.tasks.map((task) =>
              task.id === update.task_id
                ? {
                    ...task,
                    status: 'completed' as DownloadStatus,
                    progress: 1.0,
                    completed_at: new Date().toISOString(),
                    output_path: update.output_path,
                  }
                : task
            ),
          }));

          toast.success('Download completed', {
            description: update.manga_title,
          });

          // Rafraîchir les stats
          get().actions.fetchStats();
        },

        // ----------------------------------------------------------------
        // onTaskFailed - Callback WebSocket pour tâche échouée
        // ----------------------------------------------------------------
        onTaskFailed: (update: DownloadFailedUpdate) => {
          set((state) => ({
            tasks: state.tasks.map((task) =>
              task.id === update.task_id
                ? {
                    ...task,
                    status: 'failed' as DownloadStatus,
                    error_message: update.error,
                  }
                : task
            ),
          }));

          toast.error('Download failed', {
            description: `${update.manga_title}: ${update.error}`,
          });

          // Rafraîchir les stats
          get().actions.fetchStats();
        },

        // ----------------------------------------------------------------
        // clearError - Réinitialise l'état d'erreur
        // ----------------------------------------------------------------
        clearError: () => {
          set({ error: null });
        },
      },
    }),
    {
      name: 'nexusdl-downloads',
    }
  )
);

// ============================================================================
// SÉLECTEURS TYPÉS (pour éviter les re-renders inutiles)
// ============================================================================

/**
 * Sélecteur : Récupère toutes les tâches.
 */
export const selectAllTasks = (state: DownloadStoreState) => state.tasks;

/**
 * Sélecteur : Récupère les tâches actives (en cours de téléchargement).
 */
export const selectActiveTasks = (state: DownloadStoreState) =>
  state.tasks.filter((task) => task.status === 'running' || task.status === 'downloading');

/**
 * Sélecteur : Récupère les tâches en attente.
 */
export const selectPendingTasks = (state: DownloadStoreState) =>
  state.tasks.filter((task) => task.status === 'pending' || task.status === 'queued');

/**
 * Sélecteur : Récupère les tâches terminées.
 */
export const selectCompletedTasks = (state: DownloadStoreState) =>
  state.tasks.filter((task) => task.status === 'completed');

/**
 * Sélecteur : Récupère les tâches échouées.
 */
export const selectFailedTasks = (state: DownloadStoreState) =>
  state.tasks.filter((task) => task.status === 'failed');

/**
 * Sélecteur : Récupère les tâches en pause.
 */
export const selectPausedTasks = (state: DownloadStoreState) =>
  state.tasks.filter((task) => task.status === 'paused');

/**
 * Sélecteur : Récupère une tâche spécifique par ID.
 */
export const selectTaskById = (taskId: string) => (state: DownloadStoreState) =>
  state.tasks.find((task) => task.id === taskId);

/**
 * Sélecteur : Récupère les statistiques globales.
 */
export const selectStats = (state: DownloadStoreState) => state.stats;

/**
 * Sélecteur : Indique si le store est en cours de chargement.
 */
export const selectIsLoading = (state: DownloadStoreState) => state.isLoading;

/**
 * Sélecteur : Récupère le dernier message d'erreur.
 */
export const selectError = (state: DownloadStoreState) => state.error;

/**
 * Sélecteur : Compte le nombre de tâches par statut.
 */
export const selectTaskCounts = (state: DownloadStoreState) => {
  const counts = {
    total: state.tasks.length,
    active: 0,
    pending: 0,
    paused: 0,
    completed: 0,
    failed: 0,
    cancelled: 0,
  };

  state.tasks.forEach((task) => {
    switch (task.status) {
      case 'running':
      case 'downloading':
        counts.active++;
        break;
      case 'pending':
      case 'queued':
        counts.pending++;
        break;
      case 'paused':
        counts.paused++;
        break;
      case 'completed':
        counts.completed++;
        break;
      case 'failed':
        counts.failed++;
        break;
      case 'cancelled':
        counts.cancelled++;
        break;
    }
  });

  return counts;
};

// ============================================================================
// INTÉGRATION WEBSOCKET
// ============================================================================

/**
 * Initialise l'écoute des événements WebSocket pour les téléchargements.
 *
 * Cette fonction doit être appelée une fois au démarrage de l'application
 * (généralement dans un composant racine ou un layout).
 *
 * @example
 * ```tsx
 * // Dans app/layout.tsx ou un provider
 * useEffect(() => {
 *   const cleanup = initializeWebSocketListeners();
 *   return cleanup;
 * }, []);
 * ```
 */
export function initializeWebSocketListeners(): () => void {
  // Écouter les mises à jour de progression
  const unsubProgress = wsClient.on('downloads.progress', (data: DownloadProgressUpdate) => {
    useDownloadStore.getState().actions.updateProgress(data);
  });

  // Écouter les tâches terminées
  const unsubCompleted = wsClient.on('downloads.completed', (data: DownloadCompletedUpdate) => {
    useDownloadStore.getState().actions.onTaskCompleted(data);
  });

  // Écouter les tâches échouées
  const unsubFailed = wsClient.on('downloads.failed', (data: DownloadFailedUpdate) => {
    useDownloadStore.getState().actions.onTaskFailed(data);
  });

  // Écouter les tâches démarrées (pour ajout automatique à la liste)
  const unsubStarted = wsClient.on('downloads.started', (task: DownloadTask) => {
    const state = useDownloadStore.getState();
    const exists = state.tasks.some((t) => t.id === task.id);
    if (!exists) {
      useDownloadStore.setState((s) => ({
        tasks: [task, ...s.tasks],
      }));
    }
  });

  // Écouter les tâches mises en pause
  const unsubPaused = wsClient.on('downloads.paused', (data: { task_id: string }) => {
    useDownloadStore.setState((state) => ({
      tasks: state.tasks.map((task) =>
        task.id === data.task_id ? { ...task, status: 'paused' as DownloadStatus } : task
      ),
    }));
  });

  // Fonction de cleanup
  return () => {
    unsubProgress();
    unsubCompleted();
    unsubFailed();
    unsubStarted();
    unsubPaused();
  };
}

// ============================================================================
// HOOKS PERSONNALISÉS
// ============================================================================

/**
 * Hook pour accéder aux actions du store de téléchargements.
 *
 * @example
 * ```tsx
 * const { fetchTasks, createTask, pauseTask } = useDownloadActions();
 * ```
 */
export function useDownloadActions() {
  return useDownloadStore((state) => state.actions);
}

/**
 * Hook pour accéder à la liste des tâches avec filtrage.
 *
 * @param filter - Filtre optionnel ('active', 'pending', 'completed', etc.)
 */
export function useDownloadTasks(filter?: 'active' | 'pending' | 'completed' | 'failed' | 'paused') {
  return useDownloadStore((state) => {
    switch (filter) {
      case 'active':
        return selectActiveTasks(state);
      case 'pending':
        return selectPendingTasks(state);
      case 'completed':
        return selectCompletedTasks(state);
      case 'failed':
        return selectFailedTasks(state);
      case 'paused':
        return selectPausedTasks(state);
      default:
        return state.tasks;
    }
  });
}

/**
 * Hook pour accéder aux statistiques de téléchargement.
 */
export function useDownloadStats() {
  return useDownloadStore(selectStats);
}

/**
 * Hook pour accéder à une tâche spécifique par ID.
 */
export function useDownloadTask(taskId: string) {
  return useDownloadStore(selectTaskById(taskId));
}

/**
 * Hook pour obtenir les compteurs de tâches par statut.
 */
export function useDownloadCounts() {
  return useDownloadStore(selectTaskCounts);
}

/**
 * Hook pour initialiser le polling des tâches et stats.
 *
 * Utile quand le WebSocket n'est pas disponible ou en fallback.
 *
 * @example
 * ```tsx
 * useEffect(() => {
 *   const cleanup = useDownloadPolling();
 *   return cleanup;
 * }, []);
 * ```
 */
export function useDownloadPolling(): () => void {
  const fetchTasks = useDownloadStore((state) => state.actions.fetchTasks);
  const fetchStats = useDownloadStore((state) => state.actions.fetchStats);

  useEffect(() => {
    // Fetch initial
    fetchTasks();
    fetchStats();

    // Polling pour les tâches
    const tasksInterval = setInterval(() => {
      fetchTasks();
    }, TASKS_POLL_INTERVAL);

    // Polling pour les stats (plus fréquent)
    const statsInterval = setInterval(() => {
      fetchStats();
    }, STATS_POLL_INTERVAL);

    return () => {
      clearInterval(tasksInterval);
      clearInterval(statsInterval);
    };
  }, [fetchTasks, fetchStats]);

  return () => {
    // Cleanup function (vide car géré par useEffect)
  };
}

// Import de useEffect pour le hook de polling
import { useEffect } from 'react';
