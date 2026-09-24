/**
 * Client WebSocket pour les communications temps réel avec le backend NexusDL.
 *
 * Ce module fournit un client WebSocket robuste et type-safe avec :
 *   - Connexion automatique avec authentification JWT
 *   - Système d'abonnement aux canaux (pub/sub)
 *   - Reconnexion automatique avec backoff exponentiel
 *   - Heartbeat pour détecter les déconnexions silencieuses
 *   - Queue de messages en attente pendant la déconnexion
 *   - Événements typés par canal
 *   - Intégration avec les stores Zustand
 *   - Hooks React pour utilisation dans les composants
 *
 * Architecture :
 *   lib/ws.ts
 *   ├── WebSocketClient     : Client principal
 *   ├── EventBus            : Système d'événements interne
 *   ├── wsClient (instance) : Singleton global
 *   ├── React hooks         : useWebSocket, useChannel, etc.
 *   └── Type helpers        : Types pour chaque canal
 *
 * Canaux supportés (21) :
 *   - downloads.progress, completed, failed, started, paused
 *   - library.added, removed, updated, scan
 *   - notifications.info, warning, error, success
 *   - logs.stream
 *   - search.progress, completed
 *   - system.status, events, metrics
 *   - auth.login, logout
 *
 * Utilisation :
 *   import { wsClient, useChannel } from '@/lib/ws';
 *
 *   // Dans un composant
 *   useChannel('downloads.progress', (data) => {
 *     console.log('Progress:', data.progress);
 *   });
 *
 *   // Ou directement
 *   wsClient.subscribe('downloads.progress', handler);
 *
 * Configuration :
 *   NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
 *
 * @module lib/ws
 */

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * État de la connexion WebSocket.
 */
export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting';

/**
 * Types de messages WebSocket.
 */
export type MessageType =
  | 'auth'
  | 'subscribe'
  | 'unsubscribe'
  | 'ping'
  | 'pong'
  | 'message'
  | 'error'
  | 'system';

/**
 * Structure d'un message WebSocket.
 */
export interface WebSocketMessage {
  /** ID unique du message. */
  id: string;
  /** Type de message. */
  type: MessageType;
  /** Canal cible (optionnel). */
  channel?: string;
  /** Contenu du message. */
  payload: Record<string, unknown>;
  /** Timestamp ISO 8601. */
  timestamp: string;
  /** Source du message. */
  source: string;
}

/**
 * Configuration du client WebSocket.
 */
export interface WebSocketConfig {
  /** URL du serveur WebSocket. */
  url: string;
  /** Protocoles WebSocket (optionnel). */
  protocols?: string | string[];
  /** Intervalle de heartbeat en ms (défaut: 30000). */
  heartbeatInterval: number;
  /** Timeout de connexion en ms (défaut: 10000). */
  connectionTimeout: number;
  /** Nombre max de tentatives de reconnexion (défaut: 10). */
  maxReconnectAttempts: number;
  /** Délai initial de reconnexion en ms (défaut: 1000). */
  reconnectDelay: number;
  /** Délai max de reconnexion en ms (défaut: 30000). */
  maxReconnectDelay: number;
  /** Activer le logging debug. */
  debug: boolean;
  /** Activer la reconnexion automatique. */
  autoReconnect: boolean;
  /** Taille max de la queue de messages (défaut: 100). */
  maxQueueSize: number;
  /** Fonction pour récupérer le token d'authentification. */
  getAuthToken?: () => string | null;
}

/**
 * Handler d'événement WebSocket.
 */
export type WebSocketEventHandler = (data: unknown) => void;

/**
 * Callback pour les événements de connexion.
 */
export type ConnectionEventHandler = (state: ConnectionState) => void;

/**
 * Callback pour les erreurs.
 */
export type ErrorHandler = (error: Error) => void;

// ============================================================================
// TYPES SPÉCIFIQUES PAR CANAL
// ============================================================================

/**
 * Payload de progression de téléchargement.
 */
export interface DownloadProgressPayload {
  task_id: string;
  progress: number;
  manga_title: string;
  pages_completed: number;
  pages_total: number;
  speed_bytes_per_sec: number;
  eta_seconds: number;
}

/**
 * Payload de téléchargement terminé.
 */
export interface DownloadCompletedPayload {
  task_id: string;
  manga_title: string;
  output_path: string;
  size_bytes: number;
  duration_seconds: number;
}

/**
 * Payload de téléchargement échoué.
 */
export interface DownloadFailedPayload {
  task_id: string;
  error: string;
  manga_title: string;
  retry_count: number;
}

/**
 * Payload de notification.
 */
export interface NotificationPayload {
  message: string;
  level: 'info' | 'success' | 'warning' | 'error';
  title: string;
  icon: string;
  duration_ms: number;
  actions: Array<{ label: string; action: string }>;
}

/**
 * Payload de mise à jour de bibliothèque.
 */
export interface LibraryUpdatePayload {
  event_type: 'added' | 'removed' | 'updated';
  manga_id: string;
  manga_title: string;
  details: Record<string, unknown>;
}

/**
 * Payload de progression de scan.
 */
export interface LibraryScanPayload {
  scan_id: string;
  status: 'running' | 'completed' | 'failed';
  progress: number;
  scanned_files: number;
  added_mangas: number;
  updated_mangas: number;
  removed_mangas: number;
}

/**
 * Payload de progression de recherche.
 */
export interface SearchProgressPayload {
  query: string;
  sites_searched: number;
  total_sites: number;
  results_count: number;
  current_site: string;
  progress: number;
}

/**
 * Payload de statut système.
 */
export interface SystemStatusPayload {
  status: 'starting' | 'running' | 'stopping' | 'error';
  app_name: string;
  version: string;
  timestamp: string;
  details: Record<string, unknown>;
}

/**
 * Payload de métriques système.
 */
export interface SystemMetricsPayload {
  cpu_percent: number;
  memory_mb: number;
  active_connections: number;
  total_requests: number;
  uptime_seconds: number;
  timestamp: string;
}

/**
 * Payload d'événement d'authentification.
 */
export interface AuthEventPayload {
  user_id: string;
  username: string;
  timestamp: string;
}

/**
 * Payload d'entrée de log.
 */
export interface LogEntryPayload {
  level: string;
  message: string;
  module: string;
  timestamp: string;
}

/**
 * Mapping des types de payload par canal.
 */
export interface ChannelPayloadMap {
  'downloads.progress': DownloadProgressPayload;
  'downloads.completed': DownloadCompletedPayload;
  'downloads.failed': DownloadFailedPayload;
  'downloads.started': DownloadProgressPayload;
  'downloads.paused': { task_id: string };
  'library.added': LibraryUpdatePayload;
  'library.removed': LibraryUpdatePayload;
  'library.updated': LibraryUpdatePayload;
  'library.scan': LibraryScanPayload;
  'notifications.info': NotificationPayload;
  'notifications.warning': NotificationPayload;
  'notifications.error': NotificationPayload;
  'notifications.success': NotificationPayload;
  'logs.stream': LogEntryPayload;
  'search.progress': SearchProgressPayload;
  'search.completed': { query: string; results_count: number; duration_ms: number };
  'system.status': SystemStatusPayload;
  'system.events': { event: string; payload: Record<string, unknown> };
  'system.metrics': SystemMetricsPayload;
  'auth.login': AuthEventPayload;
  'auth.logout': AuthEventPayload;
}

/**
 * Type pour tous les canaux disponibles.
 */
export type Channel = keyof ChannelPayloadMap;

/**
 * Handler typé pour un canal spécifique.
 */
export type ChannelHandler<C extends Channel> = (data: ChannelPayloadMap[C]) => void;

// ============================================================================
// CONSTANTES
// ============================================================================

/**
 * Configuration par défaut.
 */
const DEFAULT_CONFIG: WebSocketConfig = {
  url: process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000/ws',
  heartbeatInterval: 30000,
  connectionTimeout: 10000,
  maxReconnectAttempts: 10,
  reconnectDelay: 1000,
  maxReconnectDelay: 30000,
  debug: process.env.NODE_ENV === 'development',
  autoReconnect: true,
  maxQueueSize: 100,
};

/**
 * Liste de tous les canaux disponibles.
 */
export const ALL_CHANNELS: Channel[] = [
  'downloads.progress',
  'downloads.completed',
  'downloads.failed',
  'downloads.started',
  'downloads.paused',
  'library.added',
  'library.removed',
  'library.updated',
  'library.scan',
  'notifications.info',
  'notifications.warning',
  'notifications.error',
  'notifications.success',
  'logs.stream',
  'search.progress',
  'search.completed',
  'system.status',
  'system.events',
  'system.metrics',
  'auth.login',
  'auth.logout',
];

// ============================================================================
// EVENT BUS - Système d'événements interne
// ============================================================================

/**
 * Bus d'événements interne pour le client WebSocket.
 */
class EventBus {
  private listeners: Map<string, Set<WebSocketEventHandler>> = new Map();

  /**
   * Ajoute un listener pour un événement.
   */
  on(event: string, handler: WebSocketEventHandler): () => void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event)!.add(handler);

    // Retourne une fonction de cleanup
    return () => this.off(event, handler);
  }

  /**
   * Supprime un listener.
   */
  off(event: string, handler: WebSocketEventHandler): void {
    const handlers = this.listeners.get(event);
    if (handlers) {
      handlers.delete(handler);
      if (handlers.size === 0) {
        this.listeners.delete(event);
      }
    }
  }

  /**
   * Émet un événement à tous les listeners.
   */
  emit(event: string, data: unknown): void {
    const handlers = this.listeners.get(event);
    if (handlers) {
      handlers.forEach((handler) => {
        try {
          handler(data);
        } catch (error) {
          console.error(`[WebSocket] Error in event handler for "${event}":`, error);
        }
      });
    }
  }

  /**
   * Supprime tous les listeners.
   */
  clear(): void {
    this.listeners.clear();
  }

  /**
   * Retourne le nombre de listeners pour un événement.
   */
  listenerCount(event: string): number {
    return this.listeners.get(event)?.size || 0;
  }
}

// ============================================================================
// WEBSOCKET CLIENT
// ============================================================================

/**
 * Client WebSocket pour NexusDL.
 *
 * Gère la connexion, l'authentification, les abonnements aux canaux,
 * la reconnexion automatique, et la diffusion des événements.
 */
export class WebSocketClient {
  private config: WebSocketConfig;
  private ws: WebSocket | null = null;
  private eventBus: EventBus = new EventBus();
  private state: ConnectionState = 'disconnected';
  private reconnectAttempts: number = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private connectionTimeoutTimer: ReturnType<typeof setTimeout> | null = null;
  private messageQueue: WebSocketMessage[] = [];
  private subscribedChannels: Set<Channel> = new Set();
  private isManualDisconnect: boolean = false;

  constructor(config: Partial<WebSocketConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  // ==========================================================================
  // PUBLIC API - Connexion
  // ==========================================================================

  /**
   * Établit la connexion WebSocket.
   *
   * @example
   * ```ts
   * wsClient.connect();
   * ```
   */
  connect(): void {
    if (this.state === 'connected' || this.state === 'connecting') {
      this.log('Already connected or connecting');
      return;
    }

    this.isManualDisconnect = false;
    this.setState('connecting');

    try {
      // Construire l'URL avec le token d'authentification
      let url = this.config.url;
      const token = this.config.getAuthToken?.();
      if (token) {
        const separator = url.includes('?') ? '&' : '?';
        url = `${url}${separator}token=${encodeURIComponent(token)}`;
      }

      this.log(`Connecting to ${url}`);

      // Créer la connexion WebSocket
      this.ws = new WebSocket(url, this.config.protocols);

      // Configurer les event handlers
      this.ws.onopen = this.handleOpen.bind(this);
      this.ws.onmessage = this.handleMessage.bind(this);
      this.ws.onerror = this.handleError.bind(this);
      this.ws.onclose = this.handleClose.bind(this);

      // Timeout de connexion
      this.connectionTimeoutTimer = setTimeout(() => {
        if (this.state === 'connecting') {
          this.log('Connection timeout');
          this.ws?.close();
          this.handleReconnect();
        }
      }, this.config.connectionTimeout);

    } catch (error) {
      this.log('Connection error:', error);
      this.eventBus.emit('error', error);
      this.handleReconnect();
    }
  }

  /**
   * Ferme la connexion WebSocket.
   *
   * @param code - Code de fermeture (défaut: 1000 = normal)
   * @param reason - Raison de la fermeture
   */
  disconnect(code: number = 1000, reason: string = 'Client disconnect'): void {
    this.isManualDisconnect = true;
    this.clearTimers();

    if (this.ws) {
      this.log(`Disconnecting (code: ${code}, reason: ${reason})`);
      this.ws.close(code, reason);
      this.ws = null;
    }

    this.setState('disconnected');
  }

  /**
   * Retourne l'état actuel de la connexion.
   */
  getState(): ConnectionState {
    return this.state;
  }

  /**
   * Vérifie si le client est connecté.
   */
  isConnected(): boolean {
    return this.state === 'connected';
  }

  // ==========================================================================
  // PUBLIC API - Abonnements aux canaux
  // ==========================================================================

  /**
   * S'abonne à un canal.
   *
   * @param channel - Canal auquel s'abonner
   * @param handler - Handler pour les messages du canal
   * @returns Fonction de cleanup pour se désabonner
   *
   * @example
   * ```ts
   * const unsubscribe = wsClient.subscribe('downloads.progress', (data) => {
   *   console.log('Progress:', data.progress);
   * });
   *
   * // Plus tard
   * unsubscribe();
   * ```
   */
  subscribe<C extends Channel>(channel: C, handler: ChannelHandler<C>): () => void {
    // Ajouter le handler au event bus
    const unsubscribe = this.eventBus.on(channel, handler as WebSocketEventHandler);

    // Si c'est le premier handler pour ce canal, s'abonner au serveur
    if (!this.subscribedChannels.has(channel)) {
      this.subscribedChannels.add(channel);

      // Envoyer le message d'abonnement si connecté
      if (this.state === 'connected') {
        this.sendMessage({
          type: 'subscribe',
          channel,
          payload: {},
        });
      }
    }

    return unsubscribe;
  }

  /**
   * Se désabonne d'un canal.
   *
   * @param channel - Canal auquel se désabonner
   */
  unsubscribe(channel: Channel): void {
    if (this.subscribedChannels.has(channel)) {
      this.subscribedChannels.delete(channel);

      // Supprimer tous les handlers pour ce canal
      this.eventBus.emit(`unsubscribe:${channel}`, null);

      // Envoyer le message de désabonnement si connecté
      if (this.state === 'connected') {
        this.sendMessage({
          type: 'unsubscribe',
          channel,
          payload: {},
        });
      }
    }
  }

  /**
   * Retourne la liste des canaux auxquels le client est abonné.
   */
  getSubscribedChannels(): Channel[] {
    return Array.from(this.subscribedChannels);
  }

  // ==========================================================================
  // PUBLIC API - Événements de connexion
  // ==========================================================================

  /**
   * Ajoute un listener pour les changements d'état de connexion.
   *
   * @param handler - Handler appelé à chaque changement d'état
   * @returns Fonction de cleanup
   *
   * @example
   * ```ts
   * const unsubscribe = wsClient.onConnectionChange((state) => {
   *   console.log('Connection state:', state);
   * });
   * ```
   */
  onConnectionChange(handler: ConnectionEventHandler): () => void {
    return this.eventBus.on('connection:change', handler as WebSocketEventHandler);
  }

  /**
   * Ajoute un listener pour les erreurs.
   *
   * @param handler - Handler appelé en cas d'erreur
   * @returns Fonction de cleanup
   */
  onError(handler: ErrorHandler): () => void {
    return this.eventBus.on('error', handler as WebSocketEventHandler);
  }

  /**
   * Ajoute un listener pour un événement spécifique.
   *
   * @param event - Nom de l'événement
   * @param handler - Handler
   * @returns Fonction de cleanup
   */
  on(event: string, handler: WebSocketEventHandler): () => void {
    return this.eventBus.on(event, handler);
  }

  /**
   * Supprime un listener.
   */
  off(event: string, handler: WebSocketEventHandler): void {
    this.eventBus.off(event, handler);
  }

  // ==========================================================================
  // PUBLIC API - Envoi de messages
  // ==========================================================================

  /**
   * Envoie un message au serveur.
   *
   * @param message - Message à envoyer (sans id et timestamp, ajoutés automatiquement)
   */
  private sendMessage(message: Omit<WebSocketMessage, 'id' | 'timestamp' | 'source'>): void {
    const fullMessage: WebSocketMessage = {
      id: this.generateMessageId(),
      timestamp: new Date().toISOString(),
      source: 'client',
      ...message,
    };

    if (this.state === 'connected' && this.ws?.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify(fullMessage));
        this.log('Sent message:', fullMessage);
      } catch (error) {
        this.log('Error sending message:', error);
        this.queueMessage(fullMessage);
      }
    } else {
      // Mettre en queue si pas connecté
      this.queueMessage(fullMessage);
    }
  }

  /**
   * Ajoute un message à la queue.
   */
  private queueMessage(message: WebSocketMessage): void {
    if (this.messageQueue.length >= this.config.maxQueueSize) {
      this.log('Message queue full, dropping oldest message');
      this.messageQueue.shift();
    }
    this.messageQueue.push(message);
    this.log(`Message queued (${this.messageQueue.length} in queue)`);
  }

  /**
   * Envoie tous les messages en queue.
   */
  private flushMessageQueue(): void {
    if (this.messageQueue.length === 0) return;

    this.log(`Flushing ${this.messageQueue.length} queued messages`);

    const queue = [...this.messageQueue];
    this.messageQueue = [];

    queue.forEach((message) => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        try {
          this.ws.send(JSON.stringify(message));
        } catch (error) {
          this.log('Error flushing message:', error);
          this.messageQueue.push(message);
        }
      }
    });
  }

  // ==========================================================================
  // PRIVATE - Handlers WebSocket
  // ==========================================================================

  /**
   * Handler pour l'événement open.
   */
  private handleOpen(): void {
    this.log('Connection opened');
    this.clearTimers();
    this.reconnectAttempts = 0;
    this.setState('connected');

    // Démarrer le heartbeat
    this.startHeartbeat();

    // Re-s'abonner aux canaux
    this.resubscribeChannels();

    // Envoyer les messages en queue
    this.flushMessageQueue();

    this.eventBus.emit('connected', null);
  }

  /**
   * Handler pour l'événement message.
   */
  private handleMessage(event: MessageEvent): void {
    try {
      const message: WebSocketMessage = JSON.parse(event.data);
      this.log('Received message:', message);

      // Traiter selon le type
      switch (message.type) {
        case 'pong':
          // Réponse au heartbeat, rien à faire
          break;

        case 'message':
          // Message normal, émettre sur le canal
          if (message.channel) {
            this.eventBus.emit(message.channel, message.payload);
          }
          break;

        case 'system':
          // Message système
          if (message.channel) {
            this.eventBus.emit(message.channel, message.payload);
          }
          this.eventBus.emit('system', message);
          break;

        case 'error':
          // Message d'erreur
          this.eventBus.emit('error', new Error(message.payload.message as string));
          break;

        default:
          this.log('Unknown message type:', message.type);
      }

      this.eventBus.emit('message', message);
    } catch (error) {
      this.log('Error parsing message:', error);
      this.eventBus.emit('error', error);
    }
  }

  /**
   * Handler pour l'événement error.
   */
  private handleError(event: Event): void {
    this.log('WebSocket error:', event);
    this.eventBus.emit('error', new Error('WebSocket error'));
  }

  /**
   * Handler pour l'événement close.
   */
  private handleClose(event: CloseEvent): void {
    this.log(`Connection closed (code: ${event.code}, reason: ${event.reason})`);
    this.clearTimers();
    this.ws = null;
    this.setState('disconnected');

    this.eventBus.emit('disconnected', { code: event.code, reason: event.reason });

    // Reconnexion automatique si pas déconnexion manuelle
    if (!this.isManualDisconnect && this.config.autoReconnect) {
      this.handleReconnect();
    }
  }

  // ==========================================================================
  // PRIVATE - Reconnexion
  // ==========================================================================

  /**
   * Gère la reconnexion automatique.
   */
  private handleReconnect(): void {
    if (this.reconnectAttempts >= this.config.maxReconnectAttempts) {
      this.log('Max reconnect attempts reached');
      this.eventBus.emit('error', new Error('Max reconnect attempts reached'));
      this.setState('disconnected');
      return;
    }

    this.setState('reconnecting');
    this.reconnectAttempts++;

    // Calculer le délai avec backoff exponentiel et jitter
    const delay = Math.min(
      this.config.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1),
      this.config.maxReconnectDelay
    );
    const jitter = Math.random() * 1000; // Jitter de 0 à 1s
    const totalDelay = delay + jitter;

    this.log(`Reconnecting in ${totalDelay}ms (attempt ${this.reconnectAttempts}/${this.config.maxReconnectAttempts})`);

    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, totalDelay);
  }

  // ==========================================================================
  // PRIVATE - Heartbeat
  // ==========================================================================

  /**
   * Démarre le heartbeat.
   */
  private startHeartbeat(): void {
    this.stopHeartbeat();

    this.heartbeatTimer = setInterval(() => {
      if (this.state === 'connected' && this.ws?.readyState === WebSocket.OPEN) {
        this.sendMessage({
          type: 'ping',
          payload: { timestamp: Date.now() },
        });
      }
    }, this.config.heartbeatInterval);

    this.log('Heartbeat started');
  }

  /**
   * Arrête le heartbeat.
   */
  private stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  // ==========================================================================
  // PRIVATE - Utilitaires
  // ==========================================================================

  /**
   * Change l'état de connexion et émet l'événement.
   */
  private setState(state: ConnectionState): void {
    if (this.state !== state) {
      this.state = state;
      this.eventBus.emit('connection:change', state);
    }
  }

  /**
   * Ré-abonne tous les canaux après reconnexion.
   */
  private resubscribeChannels(): void {
    if (this.subscribedChannels.size === 0) return;

    this.log(`Resubscribing to ${this.subscribedChannels.size} channels`);

    this.subscribedChannels.forEach((channel) => {
      this.sendMessage({
        type: 'subscribe',
        channel,
        payload: {},
      });
    });
  }

  /**
   * Efface tous les timers.
   */
  private clearTimers(): void {
    this.stopHeartbeat();

    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    if (this.connectionTimeoutTimer) {
      clearTimeout(this.connectionTimeoutTimer);
      this.connectionTimeoutTimer = null;
    }
  }

  /**
   * Génère un ID unique pour un message.
   */
  private generateMessageId(): string {
    return `msg_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  }

  /**
   * Log un message si le debug est activé.
   */
  private log(...args: unknown[]): void {
    if (this.config.debug) {
      console.log('[WebSocket]', ...args);
    }
  }

  // ==========================================================================
  // PUBLIC API - Cleanup
  // ==========================================================================

  /**
   * Détruit le client et libère toutes les ressources.
   */
  destroy(): void {
    this.disconnect();
    this.eventBus.clear();
    this.subscribedChannels.clear();
    this.messageQueue = [];
    this.log('Client destroyed');
  }
}

// ============================================================================
// INSTANCE GLOBALE
// ============================================================================

/**
 * Instance globale du client WebSocket.
 *
 * @example
 * ```ts
 * import { wsClient } from '@/lib/ws';
 *
 * wsClient.connect();
 * wsClient.subscribe('downloads.progress', (data) => {
 *   console.log(data);
 * });
 * ```
 */
export const wsClient = new WebSocketClient();

/**
 * Configure l'instance globale du client WebSocket.
 *
 * @example
 * ```ts
 * configureWebSocket({
 *   url: 'wss://api.nexusdl.dev/ws',
 *   debug: true,
 * });
 * ```
 */
export function configureWebSocket(config: Partial<WebSocketConfig>): void {
  Object.assign(wsClient, { config: { ...wsClient['config'], ...config } });
}

// ============================================================================
// REACT HOOKS
// ============================================================================

/**
 * Hook pour utiliser le client WebSocket dans un composant React.
 *
 * Gère automatiquement la connexion/déconnexion et le cleanup.
 *
 * @example
 * ```tsx
 * function MyComponent() {
 *   const { isConnected, state } = useWebSocket();
 *
 *   return (
 *     <div>
 *       Status: {state}
 *       {isConnected ? '✅' : '❌'}
 *     </div>
 *   );
 * }
 * ```
 */
export function useWebSocket() {
  const { useState, useEffect } = require('react');

  const [state, setState] = useState<ConnectionState>(wsClient.getState());
  const [isConnected, setIsConnected] = useState(wsClient.isConnected());

  useEffect(() => {
    const unsubscribe = wsClient.onConnectionChange((newState) => {
      setState(newState);
      setIsConnected(newState === 'connected');
    });

    return unsubscribe;
  }, []);

  return {
    state,
    isConnected,
    connect: () => wsClient.connect(),
    disconnect: () => wsClient.disconnect(),
  };
}

/**
 * Hook pour s'abonner à un canal WebSocket.
 *
 * Gère automatiquement l'abonnement/désabonnement et le cleanup.
 *
 * @param channel - Canal auquel s'abonner
 * @param handler - Handler pour les messages
 * @param enabled - Activer/désactiver l'abonnement (défaut: true)
 *
 * @example
 * ```tsx
 * function DownloadsPanel() {
 *   useChannel('downloads.progress', (data) => {
 *     console.log('Progress:', data.progress);
 *   });
 *
 *   return <div>...</div>;
 * }
 * ```
 */
export function useChannel<C extends Channel>(
  channel: C,
  handler: ChannelHandler<C>,
  enabled: boolean = true
): void {
  const { useEffect, useRef } = require('react');

  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    if (!enabled) return;

    const stableHandler = (data: unknown) => {
      handlerRef.current(data as ChannelPayloadMap[C]);
    };

    const unsubscribe = wsClient.subscribe(channel, stableHandler);

    return () => {
      unsubscribe();
    };
  }, [channel, enabled]);
}

/**
 * Hook pour s'abonner à plusieurs canaux.
 *
 * @param channels - Liste des canaux
 * @param handler - Handler commun pour tous les canaux
 *
 * @example
 * ```tsx
 * useChannels(['downloads.progress', 'downloads.completed'], (data, channel) => {
 *   console.log(`Event on ${channel}:`, data);
 * });
 * ```
 */
export function useChannels<C extends Channel>(
  channels: C[],
  handler: (data: ChannelPayloadMap[C], channel: C) => void
): void {
  const { useEffect, useRef } = require('react');

  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    const unsubscribes = channels.map((channel) => {
      const stableHandler = (data: unknown) => {
        handlerRef.current(data as ChannelPayloadMap[C], channel);
      };
      return wsClient.subscribe(channel, stableHandler);
    });

    return () => {
      unsubscribes.forEach((unsubscribe) => unsubscribe());
    };
  }, [channels.join(',')]);
}

/**
 * Hook pour écouter les changements d'état de connexion.
 *
 * @param handler - Handler appelé à chaque changement
 *
 * @example
 * ```tsx
 * useConnectionState((state) => {
 *   if (state === 'disconnected') {
 *     toast.error('Connection lost');
 *   }
 * });
 * ```
 */
export function useConnectionState(handler: ConnectionEventHandler): void {
  const { useEffect, useRef } = require('react');

  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    const stableHandler = (state: unknown) => {
      handlerRef.current(state as ConnectionState);
    };

    const unsubscribe = wsClient.onConnectionChange(stableHandler);
    return unsubscribe;
  }, []);
}

/**
 * Hook pour écouter les erreurs WebSocket.
 *
 * @param handler - Handler appelé en cas d'erreur
 *
 * @example
 * ```tsx
 * useWebSocketError((error) => {
 *   console.error('WebSocket error:', error);
 * });
 * ```
 */
export function useWebSocketError(handler: ErrorHandler): void {
  const { useEffect, useRef } = require('react');

  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    const stableHandler = (error: unknown) => {
      handlerRef.current(error as Error);
    };

    const unsubscribe = wsClient.onError(stableHandler);
    return unsubscribe;
  }, []);
}

// ============================================================================
// HELPERS
// ============================================================================

/**
 * Vérifie si un canal est valide.
 *
 * @param channel - Canal à vérifier
 * @returns True si le canal est valide
 */
export function isValidChannel(channel: string): channel is Channel {
  return ALL_CHANNELS.includes(channel as Channel);
}

/**
 * Retourne les canaux liés aux téléchargements.
 */
export function getDownloadChannels(): Channel[] {
  return ALL_CHANNELS.filter((c) => c.startsWith('downloads.'));
}

/**
 * Retourne les canaux liés à la bibliothèque.
 */
export function getLibraryChannels(): Channel[] {
  return ALL_CHANNELS.filter((c) => c.startsWith('library.'));
}

/**
 * Retourne les canaux liés aux notifications.
 */
export function getNotificationChannels(): Channel[] {
  return ALL_CHANNELS.filter((c) => c.startsWith('notifications.'));
}

/**
 * Retourne les canaux liés au système.
 */
export function getSystemChannels(): Channel[] {
  return ALL_CHANNELS.filter((c) => c.startsWith('system.'));
}

// ============================================================================
// EXPORTS
// ============================================================================

export type {
  WebSocketConfig,
  WebSocketMessage,
  ConnectionState,
  MessageType,
  WebSocketEventHandler,
  ConnectionEventHandler,
  ErrorHandler,
  DownloadProgressPayload,
  DownloadCompletedPayload,
  DownloadFailedPayload,
  NotificationPayload,
  LibraryUpdatePayload,
  LibraryScanPayload,
  SearchProgressPayload,
  SystemStatusPayload,
  SystemMetricsPayload,
  AuthEventPayload,
  LogEntryPayload,
  ChannelPayloadMap,
  Channel,
  ChannelHandler,
};
