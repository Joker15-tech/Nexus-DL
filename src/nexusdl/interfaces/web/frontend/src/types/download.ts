/**
 * Types et interfaces pour la gestion des téléchargements dans le frontend NexusDL.
 *
 * Ce fichier définit tous les types TypeScript liés aux tâches de téléchargement,
 * correspondant aux schémas Pydantic du backend FastAPI.
 *
 * @module types/download
 */

// ============================================================================
// ENUMS & TYPES LITTÉRAUX
// ============================================================================

/**
 * Statut d'une tâche de téléchargement.
 */
export type DownloadStatus =
  | 'pending'
  | 'queued'
  | 'running'
  | 'downloading'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled';

/**
 * Format de téléchargement supporté.
 */
export type DownloadFormat = 'cbz' | 'cbr' | 'pdf' | 'zip' | 'folder';

/**
 * Qualité d'image pour le téléchargement.
 */
export type ImageQuality = 'original' | 'high' | 'medium' | 'low';

/**
 * Priorité d'une tâche de téléchargement.
 */
export type DownloadPriority = 'low' | 'normal' | 'high' | 'urgent';

// ============================================================================
// INTERFACES PRINCIPALES
// ============================================================================

/**
 * Représente une tâche de téléchargement complète.
 * Correspond au modèle `DownloadTaskResponse` du backend.
 */
export interface DownloadTask {
  /** Identifiant unique de la tâche. */
  id: string;
  
  /** Identifiant du site source. */
  site_id: string;
  
  /** Identifiant du manga. */
  manga_id: string;
  
  /** Titre du manga (pour affichage). */
  manga_title: string;
  
  /** Statut actuel de la tâche. */
  status: DownloadStatus;
  
  /** Progression globale (0.0 à 1.0). */
  progress: number;
  
  /** Nombre de pages téléchargées. */
  pages_completed: number;
  
  /** Nombre total de pages à télécharger. */
  pages_total: number;
  
  /** Nombre de chapitres dans cette tâche. */
  chapters_count: number;
  
  /** Taille totale estimée en bytes. */
  total_size_bytes: number;
  
  /** Nombre de bytes déjà téléchargés. */
  downloaded_bytes: number;
  
  /** Vitesse de téléchargement actuelle en bytes/seconde. */
  speed_bytes_per_sec: number;
  
  /** Temps restant estimé en secondes. */
  estimated_time_remaining_seconds: number;
  
  /** Format de sortie du téléchargement. */
  format: DownloadFormat;
  
  /** Qualité d'image sélectionnée. */
  quality: ImageQuality;
  
  /** Priorité de la tâche. */
  priority: DownloadPriority;
  
  /** Date et heure de création (ISO 8601). */
  created_at: string;
  
  /** Date et heure de début du téléchargement (ISO 8601), ou null. */
  started_at: string | null;
  
  /** Date et heure de fin du téléchargement (ISO 8601), ou null. */
  completed_at: string | null;
  
  /** Message d'erreur si le statut est 'failed', ou null. */
  error_message: string | null;
  
  /** Chemin de sortie sur le serveur, ou chaîne vide si non encore déterminé. */
  output_path: string;
}

/**
 * Payload pour la création d'une nouvelle tâche de téléchargement.
 * Correspond au modèle `CreateDownloadRequest` du backend.
 */
export interface CreateDownloadRequest {
  /** Identifiant du site source. */
  site_id: string;
  
  /** Identifiant du manga à télécharger. */
  manga_id: string;
  
  /** Liste des IDs de chapitres à télécharger. Si null, tous les chapitres sont téléchargés. */
  chapter_ids: string[] | null;
  
  /** Format de sortie souhaité. */
  format: DownloadFormat;
  
  /** Qualité d'image souhaitée. */
  quality: ImageQuality;
  
  /** Priorité de la tâche. */
  priority: DownloadPriority;
  
  /** Répertoire de sortie personnalisé sur le serveur (optionnel). */
  output_dir: string | null;
}

/**
 * Statistiques globales des téléchargements.
 * Correspond au modèle `DownloadStatsResponse` du backend.
 */
export interface DownloadStats {
  /** Nombre total de tâches de téléchargement. */
  total_tasks: number;
  
  /** Nombre de tâches actuellement actives (running/downloading). */
  active_tasks: number;
  
  /** Nombre de tâches en attente (pending/queued). */
  pending_tasks: number;
  
  /** Nombre de tâches terminées avec succès. */
  completed_tasks: number;
  
  /** Nombre de tâches ayant échoué. */
  failed_tasks: number;
  
  /** Taille totale de tous les téléchargements en bytes. */
  total_size_bytes: number;
  
  /** Nombre total de bytes déjà téléchargés. */
  downloaded_bytes: number;
  
  /** Vitesse moyenne de téléchargement en bytes/seconde. */
  average_speed_bytes_per_sec: number;
  
  /** Temps restant estimé global en secondes. */
  estimated_time_remaining_seconds: number;
}

// ============================================================================
// TYPES POUR LES MISES À JOUR WEBSOCKET
// ============================================================================

/**
 * Payload de mise à jour de progression reçu via WebSocket.
 * Correspond au canal `downloads.progress`.
 */
export interface DownloadProgressUpdate {
  /** Identifiant de la tâche mise à jour. */
  task_id: string;
  
  /** Nouvelle progression (0.0 à 1.0). */
  progress: number;
  
  /** Titre du manga (pour affichage dans les notifications). */
  manga_title: string;
  
  /** Nombre de pages téléchargées. */
  pages_completed: number;
  
  /** Nombre total de pages. */
  pages_total: number;
  
  /** Vitesse actuelle en bytes/seconde. */
  speed_bytes_per_sec: number;
  
  /** Temps restant estimé en secondes. */
  eta_seconds: number;
}

/**
 * Payload de notification de téléchargement terminé.
 * Correspond au canal `downloads.completed`.
 */
export interface DownloadCompletedUpdate {
  task_id: string;
  manga_title: string;
  output_path: string;
  size_bytes: number;
  duration_seconds: number;
}

/**
 * Payload de notification d'échec de téléchargement.
 * Correspond au canal `downloads.failed`.
 */
export interface DownloadFailedUpdate {
  task_id: string;
  error: string;
  manga_title: string;
  retry_count: number;
}

// ============================================================================
// TYPES UTILITAIRES & STORE
// ============================================================================

/**
 * État local du store de téléchargements (ex: pour Zustand ou Redux).
 */
export interface DownloadStoreState {
  /** Liste de toutes les tâches de téléchargement. */
  tasks: DownloadTask[];
  
  /** Statistiques globales. */
  stats: DownloadStats | null;
  
  /** Indique si les données sont en cours de chargement. */
  isLoading: boolean;
  
  /** Dernier message d'erreur global, ou null. */
  error: string | null;
  
  /** Actions du store. */
  actions: {
    fetchTasks: () => Promise<void>;
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
    updateProgress: (update: DownloadProgressUpdate) => void;
    clearError: () => void;
  };
}

/**
 * Filtres disponibles pour la liste des téléchargements.
 */
export type DownloadFilter = 
  | 'all' 
  | 'active' 
  | 'pending' 
  | 'completed' 
  | 'failed' 
  | 'cancelled' 
  | 'paused';

/**
 * Critères de tri pour la liste des téléchargements.
 */
export type DownloadSortBy = 
  | 'date_added' 
  | 'progress' 
  | 'size' 
  | 'name' 
  | 'priority' 
  | 'status';

/**
 * Paramètres de requête pour la liste des téléchargements.
 */
export interface DownloadListParams {
  page?: number;
  page_size?: number;
  status_filter?: DownloadFilter;
  sort_by?: DownloadSortBy;
  sort_order?: 'asc' | 'desc';
}

// ============================================================================
// FONCTIONS UTILITAIRES DE FORMATAGE
// ============================================================================

/**
 * Formate une taille en bytes en une chaîne lisible (ex: "1.5 MB").
 */
export function formatBytes(bytes: number, decimals: number = 2): string {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

/**
 * Formate une durée en secondes en une chaîne lisible (ex: "1h 23m").
 */
export function formatDuration(seconds: number): string {
  if (seconds <= 0 || !isFinite(seconds)) return '--:--';
  
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  
  if (h > 0) {
    return `${h}h ${m}m ${s}s`;
  } else if (m > 0) {
    return `${m}m ${s}s`;
  } else {
    return `${s}s`;
  }
}

/**
 * Retourne une couleur Tailwind CSS correspondant au statut de téléchargement.
 */
export function getStatusColor(status: DownloadStatus): string {
  switch (status) {
    case 'pending':
    case 'queued':
      return 'text-yellow-500';
    case 'running':
    case 'downloading':
      return 'text-blue-500';
    case 'paused':
      return 'text-orange-500';
    case 'completed':
      return 'text-green-500';
    case 'failed':
      return 'text-red-500';
    case 'cancelled':
      return 'text-gray-500';
    default:
      return 'text-gray-500';
  }
}

/**
 * Retourne une icône (emoji ou nom d'icône Lucide) correspondant au statut.
 */
export function getStatusIcon(status: DownloadStatus): string {
  switch (status) {
    case 'pending':
    case 'queued':
      return '⏳'; // ou 'Clock'
    case 'running':
    case 'downloading':
      return '⚡'; // ou 'Download'
    case 'paused':
      return '⏸️'; // ou 'Pause'
    case 'completed':
      return '✅'; // ou 'CheckCircle'
    case 'failed':
      return '❌'; // ou 'XCircle'
    case 'cancelled':
      return '🚫'; // ou 'Ban'
    default:
      return '❓';
  }
}

/**
 * Vérifie si une tâche est dans un état terminal (ne peut plus changer).
 */
export function isTerminalStatus(status: DownloadStatus): boolean {
  return status === 'completed' || status === 'failed' || status === 'cancelled';
}

/**
 * Vérifie si une tâche est actuellement en cours de téléchargement actif.
 */
export function isActiveStatus(status: DownloadStatus): boolean {
  return status === 'running' || status === 'downloading';
}
