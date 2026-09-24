/**
 * Hooks React spécialisés pour les communications WebSocket dans NexusDL.
 *
 * Ce module fournit des hooks de haut niveau orientés métier qui s'appuient
 * sur le client WebSocket de base (`lib/ws.ts`) pour offrir une API simple
 * et type-safe pour les cas d'usage courants de l'application.
 *
 * Architecture :
 *   hooks/useWebSocket.ts
 *   ├── useDownloadWebSocket      : Événements de téléchargement
 *   ├── useLibraryWebSocket       : Événements de bibliothèque
 *   ├── useNotificationWebSocket  : Notifications temps réel
 *   ├── useLogStream              : Streaming des logs
 *   ├── useSystemStatus           : Statut et métriques système
 *   ├── useSearchWebSocket        : Progression de recherche
 *   ├── useAuthWebSocket          : Événements d'authentification
 *   ├── useConnectionBanner       : Bandeau de statut de connexion
 *   ├── useAutoConnect            : Connexion automatique
 *   └── useChannelWithState       : Canal avec état local
 *
 * Utilisation :
 *   // Écouter les événements de téléchargement
 *   useDownloadWebSocket({
 *     onProgress: (data) => console.log(data.progress),
 *     onCompleted: (data) => toast.success(data.manga_title),
 *   });
 *
 *   // Afficher un bandeau de statut de connexion
 *   const { isVisible, state, reconnect } = useConnectionBanner();
 *
 * @module hooks/useWebSocket
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

import {
  wsClient,
  useWebSocket as useBaseWebSocket,
  useChannel,
  useConnectionState,
  useWebSocketError,
  type ConnectionState,
  type DownloadProgressPayload,
  type DownloadCompletedPayload,
  type DownloadFailedPayload,
  type NotificationPayload,
  type LibraryUpdatePayload,
  type LibraryScanPayload,
  type SearchProgressPayload,
  type SystemStatusPayload,
  type SystemMetricsPayload,
  type AuthEventPayload,
  type LogEntryPayload,
} from '@/lib/ws';

import { useDownloadStore } from '@/store/downloads';

import { formatBytes, formatDuration } from '@/lib/utils';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Options pour le hook useDownloadWebSocket.
 */
export interface UseDownloadWebSocketOptions {
  /** Callback appelé à chaque mise à jour de progression. */
  onProgress?: (data: DownloadProgressPayload) => void;
  /** Callback appelé quand un téléchargement est terminé. */
  onCompleted?: (data: DownloadCompletedPayload) => void;
  /** Callback appelé quand un téléchargement échoue. */
  onFailed?: (data: DownloadFailedPayload) => void;
  /** Callback appelé quand un téléchargement démarre. */
  onStarted?: (data: DownloadProgressPayload) => void;
  /** Callback appelé quand un téléchargement est mis en pause. */
  onPaused?: (data: { task_id: string }) => void;
  /** Activer les notifications toast automatiques. */
  enableToasts?: boolean;
  /** Activer le hook. */
  enabled?: boolean;
}

/**
 * Retour du hook useDownloadWebSocket.
 */
export interface UseDownloadWebSocketReturn {
  /** Indique si le hook est actif. */
  isActive: boolean;
}

/**
 * Options pour le hook useLibraryWebSocket.
 */
export interface UseLibraryWebSocketOptions {
  /** Callback appelé quand un manga est ajouté. */
  onAdded?: (data: LibraryUpdatePayload) => void;
  /** Callback appelé quand un manga est retiré. */
  onRemoved?: (data: LibraryUpdatePayload) => void;
  /** Callback appelé quand un manga est mis à jour. */
  onUpdated?: (data: LibraryUpdatePayload) => void;
  /** Callback appelé pendant un scan de bibliothèque. */
  onScan?: (data: LibraryScanPayload) => void;
  /** Activer les notifications toast automatiques. */
  enableToasts?: boolean;
  /** Activer le hook. */
  enabled?: boolean;
}

/**
 * Options pour le hook useNotificationWebSocket.
 */
export interface UseNotificationWebSocketOptions {
  /** Callback appelé pour chaque notification. */
  onNotification?: (data: NotificationPayload) => void;
  /** Activer l'affichage automatique des toasts. */
  autoShowToasts?: boolean;
  /** Durée d'affichage des toasts (ms). */
  toastDuration?: number;
  /** Activer les sons de notification. */
  enableSounds?: boolean;
  /** Activer le hook. */
  enabled?: boolean;
}

/**
 * Options pour le hook useLogStream.
 */
export interface UseLogStreamOptions {
  /** Nombre maximum de logs à conserver en mémoire. */
  maxLogs?: number;
  /** Filtrer par niveau de log. */
  levelFilter?: string[];
  /** Filtrer par module. */
  moduleFilter?: string[];
  /** Activer le hook. */
  enabled?: boolean;
}

/**
 * Retour du hook useLogStream.
 */
export interface UseLogStreamReturn {
  /** Liste des logs reçus. */
  logs: LogEntryPayload[];
  /** Nombre total de logs reçus. */
  totalReceived: number;
  /** Effacer les logs. */
  clearLogs: () => void;
  /** Mettre à jour le filtre de niveau. */
  setLevelFilter: (levels: string[]) => void;
  /** Mettre à jour le filtre de module. */
  setModuleFilter: (modules: string[]) => void;
  /** Logs filtrés. */
  filteredLogs: LogEntryPayload[];
}

/**
 * Options pour le hook useSystemStatus.
 */
export interface UseSystemStatusOptions {
  /** Activer le hook. */
  enabled?: boolean;
}

/**
 * Retour du hook useSystemStatus.
 */
export interface UseSystemStatusReturn {
  /** Statut système actuel. */
  status: SystemStatusPayload | null;
  /** Dernières métriques système. */
  metrics: SystemMetricsPayload | null;
  /** Historique des événements système. */
  events: Array<{ event: string; payload: Record<string, unknown> }>;
  /** Indique si le système est opérationnel. */
  isOperational: boolean;
}

/**
 * Options pour le hook useSearchWebSocket.
 */
export interface UseSearchWebSocketOptions {
  /** Callback appelé à chaque mise à jour de progression. */
  onProgress?: (data: SearchProgressPayload) => void;
  /** Callback appelé quand une recherche est terminée. */
  onCompleted?: (data: { query: string; results_count: number; duration_ms: number }) => void;
  /** Activer le hook. */
  enabled?: boolean;
}

/**
 * Retour du hook useSearchWebSocket.
 */
export interface UseSearchWebSocketReturn {
  /** Progression actuelle de la recherche. */
  progress: SearchProgressPayload | null;
  /** Indique si une recherche est en cours. */
  isSearching: boolean;
  /** Pourcentage de progression (0-100). */
  progressPercent: number;
}

/**
 * Options pour le hook useAuthWebSocket.
 */
export interface UseAuthWebSocketOptions {
  /** Callback appelé quand un utilisateur se connecte. */
  onLogin?: (data: AuthEventPayload) => void;
  /** Callback appelé quand un utilisateur se déconnecte. */
  onLogout?: (data: AuthEventPayload) => void;
  /** Activer le hook. */
  enabled?: boolean;
}

/**
 * Options pour le hook useConnectionBanner.
 */
export interface UseConnectionBannerOptions {
  /** Délai avant d'afficher le bandeau (ms). */
  showDelay?: number;
  /** Activer le hook. */
  enabled?: boolean;
}

/**
 * Retour du hook useConnectionBanner.
 */
export interface UseConnectionBannerReturn {
  /** Indique si le bandeau doit être affiché. */
  isVisible: boolean;
  /** État actuel de la connexion. */
  state: ConnectionState;
  /** Message à afficher. */
  message: string;
  /** Tenter de se reconnecter. */
  reconnect: () => void;
  /** Masquer le bandeau manuellement. */
  dismiss: () => void;
}

/**
 * Options pour le hook useAutoConnect.
 */
export interface UseAutoConnectOptions {
  /** Connecter automatiquement au montage. */
  autoConnect?: boolean;
  /** Déconnecter au démontage. */
  disconnectOnUnmount?: boolean;
  /** Activer le hook. */
  enabled?: boolean;
}

/**
 * Options pour le hook useChannelWithState.
 */
export interface UseChannelWithStateOptions<T> {
  /** État initial. */
  initialState: T;
  /** Fonction de mise à jour de l'état basée sur les messages reçus. */
  reducer: (state: T, message: unknown) => T;
  /** Activer le hook. */
  enabled?: boolean;
}

// ============================================================================
// HOOK - useDownloadWebSocket
// ============================================================================

/**
 * Hook pour écouter les événements WebSocket liés aux téléchargements.
 *
 * S'intègre automatiquement avec le store de téléchargements pour mettre
 * à jour l'état global de l'application.
 *
 * @param options - Options de configuration
 * @returns Objet avec l'état du hook
 *
 * @example
 * ```tsx
 * function DownloadsPage() {
 *   useDownloadWebSocket({
 *     onProgress: (data) => {
 *       console.log(`Progress: ${data.progress * 100}%`);
 *     },
 *     onCompleted: (data) => {
 *       toast.success(`Downloaded: ${data.manga_title}`);
 *     },
 *     enableToasts: true,
 *   });
 *
 *   return <div>...</div>;
 * }
 * ```
 */
export function useDownloadWebSocket(
  options: UseDownloadWebSocketOptions = {}
): UseDownloadWebSocketReturn {
  const {
    onProgress,
    onCompleted,
    onFailed,
    onStarted,
    onPaused,
    enableToasts = true,
    enabled = true,
  } = options;

  // Refs pour les callbacks (évite les re-renders)
  const onProgressRef = useRef(onProgress);
  const onCompletedRef = useRef(onCompleted);
  const onFailedRef = useRef(onFailed);
  const onStartedRef = useRef(onStarted);
  const onPausedRef = useRef(onPaused);

  useEffect(() => {
    onProgressRef.current = onProgress;
    onCompletedRef.current = onCompleted;
    onFailedRef.current = onFailed;
    onStartedRef.current = onStarted;
    onPausedRef.current = onPaused;
  }, [onProgress, onCompleted, onFailed, onStarted, onPaused]);

  // Accéder au store de téléchargements
  const updateProgress = useDownloadStore((state) => state.actions.updateProgress);
  const onTaskCompleted = useDownloadStore((state) => state.actions.onTaskCompleted);
  const onTaskFailed = useDownloadStore((state) => state.actions.onTaskFailed);

  // Écouter les événements de progression
  useChannel(
    'downloads.progress',
    (data) => {
      updateProgress(data);
      onProgressRef.current?.(data);
    },
    enabled
  );

  // Écouter les événements de complétion
  useChannel(
    'downloads.completed',
    (data) => {
      onTaskCompleted(data);
      onCompletedRef.current?.(data);

      if (enableToasts) {
        toast.success('Download completed', {
          description: data.manga_title,
        });
      }
    },
    enabled
  );

  // Écouter les événements d'échec
  useChannel(
    'downloads.failed',
    (data) => {
      onTaskFailed(data);
      onFailedRef.current?.(data);

      if (enableToasts) {
        toast.error('Download failed', {
          description: `${data.manga_title}: ${data.error}`,
        });
      }
    },
    enabled
  );

  // Écouter les événements de démarrage
  useChannel(
    'downloads.started',
    (data) => {
      onStartedRef.current?.(data as DownloadProgressPayload);

      if (enableToasts) {
        const payload = data as DownloadProgressPayload;
        toast.info('Download started', {
          description: payload.manga_title,
        });
      }
    },
    enabled
  );

  // Écouter les événements de pause
  useChannel(
    'downloads.paused',
    (data) => {
      onPausedRef.current?.(data);
    },
    enabled
  );

  return {
    isActive: enabled,
  };
}

// ============================================================================
// HOOK - useLibraryWebSocket
// ============================================================================

/**
 * Hook pour écouter les événements WebSocket liés à la bibliothèque.
 *
 * @param options - Options de configuration
 *
 * @example
 * ```tsx
 * function LibraryPage() {
 *   useLibraryWebSocket({
 *     onAdded: (data) => {
 *       toast.success(`Added: ${data.manga_title}`);
 *       refreshLibrary();
 *     },
 *     onScan: (data) => {
 *       console.log(`Scan progress: ${data.progress * 100}%`);
 *     },
 *   });
 *
 *   return <div>...</div>;
 * }
 * ```
 */
export function useLibraryWebSocket(options: UseLibraryWebSocketOptions = {}): void {
  const {
    onAdded,
    onRemoved,
    onUpdated,
    onScan,
    enableToasts = true,
    enabled = true,
  } = options;

  const onAddedRef = useRef(onAdded);
  const onRemovedRef = useRef(onRemoved);
  const onUpdatedRef = useRef(onUpdated);
  const onScanRef = useRef(onScan);

  useEffect(() => {
    onAddedRef.current = onAdded;
    onRemovedRef.current = onRemoved;
    onUpdatedRef.current = onUpdated;
    onScanRef.current = onScan;
  }, [onAdded, onRemoved, onUpdated, onScan]);

  useChannel(
    'library.added',
    (data) => {
      onAddedRef.current?.(data);

      if (enableToasts) {
        toast.success('Manga added to library', {
          description: data.manga_title,
        });
      }
    },
    enabled
  );

  useChannel(
    'library.removed',
    (data) => {
      onRemovedRef.current?.(data);

      if (enableToasts) {
        toast.info('Manga removed from library', {
          description: data.manga_title,
        });
      }
    },
    enabled
  );

  useChannel(
    'library.updated',
    (data) => {
      onUpdatedRef.current?.(data);
    },
    enabled
  );

  useChannel(
    'library.scan',
    (data) => {
      onScanRef.current?.(data);
    },
    enabled
  );
}

// ============================================================================
// HOOK - useNotificationWebSocket
// ============================================================================

/**
 * Hook pour écouter les notifications WebSocket et les afficher automatiquement.
 *
 * @param options - Options de configuration
 *
 * @example
 * ```tsx
 * function App() {
 *   useNotificationWebSocket({
 *     autoShowToasts: true,
 *     toastDuration: 5000,
 *     enableSounds: true,
 *   });
 *
 *   return <div>...</div>;
 * }
 * ```
 */
export function useNotificationWebSocket(
  options: UseNotificationWebSocketOptions = {}
): void {
  const {
    onNotification,
    autoShowToasts = true,
    toastDuration = 5000,
    enableSounds = false,
    enabled = true,
  } = options;

  const onNotificationRef = useRef(onNotification);

  useEffect(() => {
    onNotificationRef.current = onNotification;
  }, [onNotification]);

  // Fonction pour jouer un son de notification
  const playNotificationSound = useCallback((level: string) => {
    if (!enableSounds) return;

    // Créer un son de notification simple avec Web Audio API
    try {
      const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();
      const oscillator = audioContext.createOscillator();
      const gainNode = audioContext.createGain();

      oscillator.connect(gainNode);
      gainNode.connect(audioContext.destination);

      // Fréquence différente selon le niveau
      const frequencies: Record<string, number> = {
        info: 440,
        success: 523.25,
        warning: 349.23,
        error: 261.63,
      };

      oscillator.frequency.value = frequencies[level] || 440;
      oscillator.type = 'sine';

      gainNode.gain.setValueAtTime(0.3, audioContext.currentTime);
      gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.5);

      oscillator.start(audioContext.currentTime);
      oscillator.stop(audioContext.currentTime + 0.5);
    } catch (error) {
      console.error('[useNotificationWebSocket] Failed to play sound:', error);
    }
  }, [enableSounds]);

  // Écouter tous les canaux de notification
  const channels = ['notifications.info', 'notifications.success', 'notifications.warning', 'notifications.error'] as const;

  channels.forEach((channel) => {
    useChannel(
      channel,
      (data) => {
        onNotificationRef.current?.(data);

        if (autoShowToasts) {
          const level = data.level;

          // Jouer un son si activé
          playNotificationSound(level);

          // Afficher le toast
          const toastOptions = {
            description: data.message,
            duration: toastDuration,
          };

          switch (level) {
            case 'info':
              toast.info(data.title || 'Info', toastOptions);
              break;
            case 'success':
              toast.success(data.title || 'Success', toastOptions);
              break;
            case 'warning':
              toast.warning(data.title || 'Warning', toastOptions);
              break;
            case 'error':
              toast.error(data.title || 'Error', toastOptions);
              break;
          }
        }
      },
      enabled
    );
  });
}

// ============================================================================
// HOOK - useLogStream
// ============================================================================

/**
 * Hook pour recevoir les logs en temps réel via WebSocket.
 *
 * Maintient un buffer de logs en mémoire avec filtrage par niveau et module.
 *
 * @param options - Options de configuration
 * @returns Objet avec les logs et les contrôles
 *
 * @example
 * ```tsx
 * function LogViewer() {
 *   const { logs, filteredLogs, clearLogs, setLevelFilter } = useLogStream({
 *     maxLogs: 1000,
 *     levelFilter: ['ERROR', 'WARNING'],
 *   });
 *
 *   return (
 *     <div>
 *       <button onClick={clearLogs}>Clear</button>
 *       <select onChange={(e) => setLevelFilter([e.target.value])}>
 *         <option value="">All Levels</option>
 *         <option value="ERROR">Error</option>
 *         <option value="WARNING">Warning</option>
 *       </select>
 *
 *       <div className="log-container">
 *         {filteredLogs.map((log, idx) => (
 *           <div key={idx} className={`log-entry log-${log.level.toLowerCase()}`}>
 *             [{log.timestamp}] [{log.level}] {log.message}
 *           </div>
 *         ))}
 *       </div>
 *     </div>
 *   );
 * }
 * ```
 */
export function useLogStream(options: UseLogStreamOptions = {}): UseLogStreamReturn {
  const { maxLogs = 1000, levelFilter = [], moduleFilter = [], enabled = true } = options;

  const [logs, setLogs] = useState<LogEntryPayload[]>([]);
  const [totalReceived, setTotalReceived] = useState(0);
  const [currentLevelFilter, setCurrentLevelFilter] = useState<string[]>(levelFilter);
  const [currentModuleFilter, setCurrentModuleFilter] = useState<string[]>(moduleFilter);

  // Écouter les logs
  useChannel(
    'logs.stream',
    (data) => {
      setLogs((prev) => {
        const newLogs = [...prev, data];
        // Limiter la taille du buffer
        if (newLogs.length > maxLogs) {
          return newLogs.slice(-maxLogs);
        }
        return newLogs;
      });
      setTotalReceived((prev) => prev + 1);
    },
    enabled
  );

  // Logs filtrés
  const filteredLogs = useMemo(() => {
    return logs.filter((log) => {
      if (currentLevelFilter.length > 0 && !currentLevelFilter.includes(log.level)) {
        return false;
      }
      if (currentModuleFilter.length > 0 && !currentModuleFilter.includes(log.module)) {
        return false;
      }
      return true;
    });
  }, [logs, currentLevelFilter, currentModuleFilter]);

  const clearLogs = useCallback(() => {
    setLogs([]);
    setTotalReceived(0);
  }, []);

  const setLevelFilterFn = useCallback((levels: string[]) => {
    setCurrentLevelFilter(levels);
  }, []);

  const setModuleFilterFn = useCallback((modules: string[]) => {
    setCurrentModuleFilter(modules);
  }, []);

  return {
    logs,
    totalReceived,
    clearLogs,
    setLevelFilter: setLevelFilterFn,
    setModuleFilter: setModuleFilterFn,
    filteredLogs,
  };
}

// ============================================================================
// HOOK - useSystemStatus
// ============================================================================

/**
 * Hook pour suivre le statut système et les métriques en temps réel.
 *
 * @param options - Options de configuration
 * @returns Objet avec le statut, les métriques et les événements
 *
 * @example
 * ```tsx
 * function SystemStatusPanel() {
 *   const { status, metrics, isOperational } = useSystemStatus();
 *
 *   if (!status) return null;
 *
 *   return (
 *     <div>
 *       <div className={`status-badge ${isOperational ? 'healthy' : 'unhealthy'}`}>
 *         {status.status}
 *       </div>
 *
 *       {metrics && (
 *         <div>
 *           <p>CPU: {metrics.cpu_percent}%</p>
 *           <p>Memory: {formatBytes(metrics.memory_mb * 1024 * 1024)}</p>
 *           <p>Uptime: {formatDuration(metrics.uptime_seconds)}</p>
 *         </div>
 *       )}
 *     </div>
 *   );
 * }
 * ```
 */
export function useSystemStatus(options: UseSystemStatusOptions = {}): UseSystemStatusReturn {
  const { enabled = true } = options;

  const [status, setStatus] = useState<SystemStatusPayload | null>(null);
  const [metrics, setMetrics] = useState<SystemMetricsPayload | null>(null);
  const [events, setEvents] = useState<Array<{ event: string; payload: Record<string, unknown> }>>([]);

  useChannel(
    'system.status',
    (data) => {
      setStatus(data);
    },
    enabled
  );

  useChannel(
    'system.metrics',
    (data) => {
      setMetrics(data);
    },
    enabled
  );

  useChannel(
    'system.events',
    (data) => {
      setEvents((prev) => {
        const newEvents = [...prev, data];
        // Limiter à 100 événements
        if (newEvents.length > 100) {
          return newEvents.slice(-100);
        }
        return newEvents;
      });
    },
    enabled
  );

  const isOperational = status?.status === 'running';

  return {
    status,
    metrics,
    events,
    isOperational,
  };
}

// ============================================================================
// HOOK - useSearchWebSocket
// ============================================================================

/**
 * Hook pour suivre la progression d'une recherche en temps réel.
 *
 * @param options - Options de configuration
 * @returns Objet avec la progression et l'état
 *
 * @example
 * ```tsx
 * function SearchProgressBar() {
 *   const { progress, isSearching, progressPercent } = useSearchWebSocket({
 *     onCompleted: (data) => {
 *       console.log(`Search completed: ${data.results_count} results`);
 *     },
 *   });
 *
 *   if (!isSearching) return null;
 *
 *   return (
 *     <div>
 *       <progress value={progressPercent} max={100} />
 *       <p>
 *         Searching {progress?.current_site}...
 *         ({progress?.sites_searched}/{progress?.total_sites} sites)
 *       </p>
 *     </div>
 *   );
 * }
 * ```
 */
export function useSearchWebSocket(
  options: UseSearchWebSocketOptions = {}
): UseSearchWebSocketReturn {
  const { onProgress, onCompleted, enabled = true } = options;

  const [progress, setProgress] = useState<SearchProgressPayload | null>(null);
  const [isSearching, setIsSearching] = useState(false);

  const onProgressRef = useRef(onProgress);
  const onCompletedRef = useRef(onCompleted);

  useEffect(() => {
    onProgressRef.current = onProgress;
    onCompletedRef.current = onCompleted;
  }, [onProgress, onCompleted]);

  useChannel(
    'search.progress',
    (data) => {
      setProgress(data);
      setIsSearching(true);
      onProgressRef.current?.(data);
    },
    enabled
  );

  useChannel(
    'search.completed',
    (data) => {
      setIsSearching(false);
      setProgress(null);
      onCompletedRef.current?.(data);
    },
    enabled
  );

  const progressPercent = progress ? Math.round(progress.progress * 100) : 0;

  return {
    progress,
    isSearching,
    progressPercent,
  };
}

// ============================================================================
// HOOK - useAuthWebSocket
// ============================================================================

/**
 * Hook pour écouter les événements d'authentification.
 *
 * Utile pour synchroniser l'état d'authentification entre plusieurs onglets
 * ou pour afficher des notifications de connexion/déconnexion.
 *
 * @param options - Options de configuration
 *
 * @example
 * ```tsx
 * function AuthSync() {
 *   useAuthWebSocket({
 *     onLogin: (data) => {
 *       console.log(`User logged in: ${data.username}`);
 *       refreshUserData();
 *     },
 *     onLogout: (data) => {
 *       console.log(`User logged out: ${data.username}`);
 *       clearUserData();
 *     },
 *   });
 *
 *   return null;
 * }
 * ```
 */
export function useAuthWebSocket(options: UseAuthWebSocketOptions = {}): void {
  const { onLogin, onLogout, enabled = true } = options;

  const onLoginRef = useRef(onLogin);
  const onLogoutRef = useRef(onLogout);

  useEffect(() => {
    onLoginRef.current = onLogin;
    onLogoutRef.current = onLogout;
  }, [onLogin, onLogout]);

  useChannel(
    'auth.login',
    (data) => {
      onLoginRef.current?.(data);
    },
    enabled
  );

  useChannel(
    'auth.logout',
    (data) => {
      onLogoutRef.current?.(data);
    },
    enabled
  );
}

// ============================================================================
// HOOK - useConnectionBanner
// ============================================================================

/**
 * Hook pour afficher un bandeau de statut de connexion.
 *
 * Affiche automatiquement un bandeau quand la connexion est perdue
 * ou en cours de rétablissement.
 *
 * @param options - Options de configuration
 * @returns Objet avec l'état du bandeau et les contrôles
 *
 * @example
 * ```tsx
 * function ConnectionBanner() {
 *   const { isVisible, state, message, reconnect, dismiss } = useConnectionBanner({
 *     showDelay: 2000,
 *   });
 *
 *   if (!isVisible) return null;
 *
 *   return (
 *     <div className={`banner banner-${state}`}>
 *       <span>{message}</span>
 *       {state === 'disconnected' && (
 *         <button onClick={reconnect}>Reconnect</button>
 *       )}
 *       <button onClick={dismiss}>×</button>
 *     </div>
 *   );
 * }
 * ```
 */
export function useConnectionBanner(
  options: UseConnectionBannerOptions = {}
): UseConnectionBannerReturn {
  const { showDelay = 2000, enabled = true } = options;

  const { state } = useBaseWebSocket();
  const [isVisible, setIsVisible] = useState(false);
  const [isDismissed, setIsDismissed] = useState(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Afficher le bandeau après un délai si déconnecté
  useEffect(() => {
    if (!enabled) return;

    if (state === 'disconnected' || state === 'reconnecting') {
      if (!isDismissed) {
        timeoutRef.current = setTimeout(() => {
          setIsVisible(true);
        }, showDelay);
      }
    } else {
      // Masquer immédiatement quand reconnecté
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      setIsVisible(false);
      setIsDismissed(false);
    }

    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, [state, showDelay, enabled, isDismissed]);

  const message = useMemo(() => {
    switch (state) {
      case 'disconnected':
        return 'Connection lost. Some features may not work.';
      case 'reconnecting':
        return 'Reconnecting...';
      case 'connecting':
        return 'Connecting...';
      case 'connected':
        return 'Connected';
      default:
        return '';
    }
  }, [state]);

  const reconnect = useCallback(() => {
    wsClient.connect();
  }, []);

  const dismiss = useCallback(() => {
    setIsVisible(false);
    setIsDismissed(true);
  }, []);

  return {
    isVisible,
    state,
    message,
    reconnect,
    dismiss,
  };
}

// ============================================================================
// HOOK - useAutoConnect
// ============================================================================

/**
 * Hook pour gérer la connexion WebSocket automatique.
 *
 * Connecte automatiquement au montage du composant et déconnecte au démontage.
 *
 * @param options - Options de configuration
 *
 * @example
 * ```tsx
 * function App() {
 *   useAutoConnect({
 *     autoConnect: true,
 *     disconnectOnUnmount: false, // Garder la connexion active
 *   });
 *
 *   return <div>...</div>;
 * }
 * ```
 */
export function useAutoConnect(options: UseAutoConnectOptions = {}): void {
  const { autoConnect = true, disconnectOnUnmount = false, enabled = true } = options;

  useEffect(() => {
    if (!enabled) return;

    if (autoConnect && !wsClient.isConnected()) {
      wsClient.connect();
    }

    return () => {
      if (disconnectOnUnmount) {
        wsClient.disconnect();
      }
    };
  }, [autoConnect, disconnectOnUnmount, enabled]);
}

// ============================================================================
// HOOK - useChannelWithState
// ============================================================================

/**
 * Hook générique pour maintenir un état local basé sur les messages d'un canal.
 *
 * Utile pour construire des états complexes à partir de flux de messages.
 *
 * @param channel - Canal à écouter
 * @param options - Options de configuration
 * @returns État actuel
 *
 * @example
 * ```tsx
 * interface DownloadProgress {
 *   [taskId: string]: {
 *     progress: number;
 *     speed: number;
 *   };
 * }
 *
 * function DownloadProgressTracker() {
 *   const progress = useChannelWithState<DownloadProgress>(
 *     'downloads.progress',
 *     {
 *       initialState: {},
 *       reducer: (state, message) => {
 *         const data = message as DownloadProgressPayload;
 *         return {
 *           ...state,
 *           [data.task_id]: {
 *             progress: data.progress,
 *             speed: data.speed_bytes_per_sec,
 *           },
 *         };
 *       },
 *     }
 *   );
 *
 *   return (
 *     <div>
 *       {Object.entries(progress).map(([taskId, data]) => (
 *         <div key={taskId}>
 *           Task {taskId}: {data.progress * 100}% at {formatBytes(data.speed)}/s
 *         </div>
 *       ))}
 *     </div>
 *   );
 * }
 * ```
 */
export function useChannelWithState<T>(
  channel: string,
  options: UseChannelWithStateOptions<T>
): T {
  const { initialState, reducer, enabled = true } = options;

  const [state, setState] = useState<T>(initialState);

  useChannel(
    channel as any,
    (message) => {
      setState((prev) => reducer(prev, message));
    },
    enabled
  );

  return state;
}

// ============================================================================
// HOOK - useWebSocketHealth
// ============================================================================

/**
 * Hook pour monitorer la santé de la connexion WebSocket.
 *
 * Fournit des métriques sur la qualité de la connexion (latence, pertes, etc.).
 *
 * @returns Objet avec les métriques de santé
 *
 * @example
 * ```tsx
 * function ConnectionHealth() {
 *   const { latency, isHealthy, lastPong } = useWebSocketHealth();
 *
 *   return (
 *     <div>
 *       <span>Latency: {latency}ms</span>
 *       <span>Status: {isHealthy ? '✅' : '❌'}</span>
 *     </div>
 *   );
 * }
 * ```
 */
export function useWebSocketHealth() {
  const [latency, setLatency] = useState<number | null>(null);
  const [lastPong, setLastPong] = useState<Date | null>(null);
  const [isHealthy, setIsHealthy] = useState(true);

  const { state } = useBaseWebSocket();

  // Envoyer un ping périodiquement et mesurer la latence
  useEffect(() => {
    if (state !== 'connected') {
      setIsHealthy(false);
      return;
    }

    const interval = setInterval(() => {
      const startTime = Date.now();

      // Envoyer un ping
      wsClient['sendMessage']({
        type: 'ping',
        payload: { timestamp: startTime },
      });

      // Écouter le pong
      const unsubscribe = wsClient.on('pong', () => {
        const latency = Date.now() - startTime;
        setLatency(latency);
        setLastPong(new Date());
        setIsHealthy(latency < 1000); // Considérer comme unhealthy si > 1s
        unsubscribe();
      });

      // Timeout après 5s
      setTimeout(() => {
        setIsHealthy(false);
        unsubscribe();
      }, 5000);
    }, 10000); // Toutes les 10s

    return () => clearInterval(interval);
  }, [state]);

  return {
    latency,
    lastPong,
    isHealthy,
    connectionState: state,
  };
}

// ============================================================================
// HOOK - useWebSocketDebug
// ============================================================================

/**
 * Hook pour le debug WebSocket (développement uniquement).
 *
 * Log tous les messages WebSocket dans la console.
 *
 * @example
 * ```tsx
 * function App() {
 *   if (process.env.NODE_ENV === 'development') {
 *     useWebSocketDebug();
 *   }
 *
 *   return <div>...</div>;
 * }
 * ```
 */
export function useWebSocketDebug(): void {
  useEffect(() => {
    if (process.env.NODE_ENV !== 'development') return;

    const unsubscribeMessage = wsClient.on('message', (message) => {
      console.log('[WebSocket Debug] Message received:', message);
    });

    const unsubscribeError = wsClient.on('error', (error) => {
      console.error('[WebSocket Debug] Error:', error);
    });

    const unsubscribeConnection = wsClient.on('connection:change', (state) => {
      console.log('[WebSocket Debug] Connection state changed:', state);
    });

    return () => {
      unsubscribeMessage();
      unsubscribeError();
      unsubscribeConnection();
    };
  }, []);
}

// ============================================================================
// EXPORTS
// ============================================================================

export type {
  UseDownloadWebSocketOptions,
  UseDownloadWebSocketReturn,
  UseLibraryWebSocketOptions,
  UseNotificationWebSocketOptions,
  UseLogStreamOptions,
  UseLogStreamReturn,
  UseSystemStatusOptions,
  UseSystemStatusReturn,
  UseSearchWebSocketOptions,
  UseSearchWebSocketReturn,
  UseAuthWebSocketOptions,
  UseConnectionBannerOptions,
  UseConnectionBannerReturn,
  UseAutoConnectOptions,
  UseChannelWithStateOptions,
};
