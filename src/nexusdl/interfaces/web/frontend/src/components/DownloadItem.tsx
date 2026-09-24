/**
 * Composant DownloadItem pour NexusDL.
 *
 * Affiche une tâche de téléchargement individuelle avec toutes les informations
 * pertinentes et les actions disponibles. Utilisé dans la page /downloads.
 *
 * Caractéristiques :
 *   - 3 variantes : default, compact, expanded
 *   - Affichage riche : progression, vitesse, ETA, taille, format, qualité
 *   - Actions contextuelles : pause, resume, cancel, retry, delete
 *   - États visuels distincts pour chaque statut
 *   - Animation de pulse pour les tâches actives
 *   - Menu d'actions avancées (dropdown)
 *   - Sélection multiple (checkbox)
 *   - Skeleton loader pour l'état de chargement
 *   - Accessibilité complète (ARIA, clavier, focus)
 *   - Style cyberpunk néon cohérent
 *   - Responsive (mobile/desktop)
 *
 * Utilisation :
 *   // Liste de téléchargements
 *   <div className="space-y-2">
 *     {tasks.map((task) => (
 *       <DownloadItem
 *         key={task.id}
 *         task={task}
 *         onPause={handlePause}
 *         onResume={handleResume}
 *         onCancel={handleCancel}
 *         onDelete={handleDelete}
 *       />
 *     ))}
 *   </div>
 *
 *   // Mode compact
 *   <DownloadItemCompact task={task} />
 *
 *   // Mode expanded avec détails
 *   <DownloadItemExpanded task={task} showHistory />
 *
 *   // Skeleton loader
 *   <DownloadItemSkeleton />
 *
 * @module components/DownloadItem
 */

'use client';

import { useCallback, useMemo, useState, type MouseEvent } from 'react';
import {
  AlertCircle,
  Archive,
  Ban,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  Download,
  Eye,
  Folder,
  Info,
  MoreVertical,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Trash2,
  X,
  Zap,
} from 'lucide-react';

import { cn } from '@/lib/utils';
import {
  formatBytes,
  formatDuration,
  getStatusColor,
  getStatusIcon,
  isTerminalStatus,
} from '@/types/download';

import { ProgressBar } from '@/components/ProgressBar';

import type { DownloadTask, DownloadStatus } from '@/types/download';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Variantes du composant DownloadItem.
 */
export type DownloadItemVariant = 'default' | 'compact' | 'expanded';

/**
 * Props du composant DownloadItem.
 */
export interface DownloadItemProps {
  /** Tâche de téléchargement à afficher. */
  task: DownloadTask;
  /** Variante d'affichage. */
  variant?: DownloadItemVariant;
  /** Afficher la checkbox de sélection. */
  selectable?: boolean;
  /** État de sélection. */
  isSelected?: boolean;
  /** Callback quand la sélection change. */
  onSelectionChange?: (taskId: string, selected: boolean) => void;
  /** Callback pour mettre en pause. */
  onPause?: (taskId: string) => void;
  /** Callback pour reprendre. */
  onResume?: (taskId: string) => void;
  /** Callback pour annuler. */
  onCancel?: (taskId: string) => void;
  /** Callback pour réessayer. */
  onRetry?: (taskId: string) => void;
  /** Callback pour supprimer. */
  onDelete?: (taskId: string) => void;
  /** Callback pour ouvrir le dossier. */
  onOpenFolder?: (taskId: string) => void;
  /** Callback pour voir les détails. */
  onViewDetails?: (taskId: string) => void;
  /** Afficher l'historique des erreurs (expanded uniquement). */
  showHistory?: boolean;
  /** Afficher les métadonnées complètes. */
  showMetadata?: boolean;
  /** Classe CSS additionnelle. */
  className?: string;
  /** Désactiver les interactions. */
  disabled?: boolean;
}

/**
 * Props du composant DownloadItemSkeleton.
 */
export interface DownloadItemSkeletonProps {
  /** Variante du skeleton. */
  variant?: DownloadItemVariant;
  /** Classe CSS additionnelle. */
  className?: string;
}

// ============================================================================
// CONSTANTES
// ============================================================================

/**
 * Configuration des statuts (couleurs, icônes, labels).
 */
const STATUS_CONFIG: Record<
  DownloadStatus,
  {
    color: string;
    bgColor: string;
    borderColor: string;
    glowColor: string;
    icon: React.ComponentType<{ size?: number; className?: string }>;
    label: string;
    animated: boolean;
  }
> = {
  pending: {
    color: 'text-yellow-400',
    bgColor: 'bg-yellow-500/10',
    borderColor: 'border-yellow-500/30',
    glowColor: 'shadow-[0_0_8px_rgba(234,179,8,0.2)]',
    icon: Clock,
    label: 'Pending',
    animated: false,
  },
  queued: {
    color: 'text-yellow-400',
    bgColor: 'bg-yellow-500/10',
    borderColor: 'border-yellow-500/30',
    glowColor: 'shadow-[0_0_8px_rgba(234,179,8,0.2)]',
    icon: Clock,
    label: 'Queued',
    animated: false,
  },
  running: {
    color: 'text-cyan-400',
    bgColor: 'bg-cyan-500/10',
    borderColor: 'border-cyan-500/30',
    glowColor: 'shadow-[0_0_12px_rgba(6,182,212,0.3)]',
    icon: Zap,
    label: 'Running',
    animated: true,
  },
  downloading: {
    color: 'text-cyan-400',
    bgColor: 'bg-cyan-500/10',
    borderColor: 'border-cyan-500/30',
    glowColor: 'shadow-[0_0_12px_rgba(6,182,212,0.3)]',
    icon: Download,
    label: 'Downloading',
    animated: true,
  },
  paused: {
    color: 'text-orange-400',
    bgColor: 'bg-orange-500/10',
    borderColor: 'border-orange-500/30',
    glowColor: 'shadow-[0_0_8px_rgba(249,115,22,0.2)]',
    icon: Pause,
    label: 'Paused',
    animated: false,
  },
  completed: {
    color: 'text-green-400',
    bgColor: 'bg-green-500/10',
    borderColor: 'border-green-500/30',
    glowColor: 'shadow-[0_0_8px_rgba(34,197,94,0.2)]',
    icon: Check,
    label: 'Completed',
    animated: false,
  },
  failed: {
    color: 'text-red-400',
    bgColor: 'bg-red-500/10',
    borderColor: 'border-red-500/30',
    glowColor: 'shadow-[0_0_8px_rgba(239,68,68,0.2)]',
    icon: AlertCircle,
    label: 'Failed',
    animated: false,
  },
  cancelled: {
    color: 'text-gray-400',
    bgColor: 'bg-gray-500/10',
    borderColor: 'border-gray-500/30',
    glowColor: '',
    icon: Ban,
    label: 'Cancelled',
    animated: false,
  },
};

/**
 * Labels des formats de téléchargement.
 */
const FORMAT_LABELS: Record<string, string> = {
  cbz: 'CBZ',
  cbr: 'CBR',
  pdf: 'PDF',
  zip: 'ZIP',
  folder: 'Folder',
};

/**
 * Labels des qualités d'image.
 */
const QUALITY_LABELS: Record<string, string> = {
  original: 'Original',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
};

/**
 * Labels des priorités.
 */
const PRIORITY_LABELS: Record<string, string> = {
  low: 'Low',
  normal: 'Normal',
  high: 'High',
  urgent: 'Urgent',
};

// ============================================================================
// SOUS-COMPOSANTS
// ============================================================================

/**
 * Badge de statut avec icône et label.
 */
function DownloadItemStatus({
  status,
  size = 'md',
}: {
  status: DownloadStatus;
  size?: 'sm' | 'md';
}) {
  const config = STATUS_CONFIG[status];
  const Icon = config.icon;
  const isSmall = size === 'sm';

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 px-2 py-0.5 rounded-md',
        'font-mono font-bold uppercase tracking-wider',
        'border backdrop-blur-sm',
        config.color,
        config.bgColor,
        config.borderColor,
        isSmall ? 'text-[9px]' : 'text-[10px]'
      )}
      style={{ textShadow: '0 0 4px currentColor' }}
      title={config.label}
    >
      <Icon size={isSmall ? 10 : 12} className={config.animated ? 'animate-pulse' : ''} />
      {!isSmall && <span>{config.label}</span>}
    </span>
  );
}

/**
 * En-tête de l'item (titre, site, statut).
 */
function DownloadItemHeader({
  task,
  variant,
  selectable,
  isSelected,
  onSelectionChange,
}: {
  task: DownloadTask;
  variant: DownloadItemVariant;
  selectable?: boolean;
  isSelected?: boolean;
  onSelectionChange?: (taskId: string, selected: boolean) => void;
}) {
  const isCompact = variant === 'compact';

  return (
    <div className="flex items-start gap-3">
      {/* Checkbox de sélection */}
      {selectable && (
        <div className="flex items-center pt-1">
          <input
            type="checkbox"
            checked={isSelected}
            onChange={(e) => onSelectionChange?.(task.id, e.target.checked)}
            className="h-4 w-4 rounded border-border-dim bg-surface-alt text-primary focus:ring-primary focus:ring-offset-0 cursor-pointer"
            aria-label={`Select ${task.manga_title}`}
          />
        </div>
      )}

      {/* Contenu principal */}
      <div className="flex-1 min-w-0">
        {/* Titre et statut */}
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 min-w-0">
            <h3
              className={cn(
                'font-mono font-bold text-text truncate',
                isCompact ? 'text-sm' : 'text-base'
              )}
              title={task.manga_title}
            >
              {task.manga_title || 'Unknown Manga'}
            </h3>

            {/* Site et format */}
            <div className="flex items-center gap-2 mt-0.5 text-xs font-mono text-text-muted">
              <span className="flex items-center gap-1">
                <Zap size={10} className="text-secondary" />
                {task.site_id}
              </span>
              <span className="text-text-dim">•</span>
              <span className="flex items-center gap-1">
                <Archive size={10} />
                {FORMAT_LABELS[task.format] || task.format}
              </span>
              {!isCompact && (
                <>
                  <span className="text-text-dim">•</span>
                  <span>{QUALITY_LABELS[task.quality] || task.quality}</span>
                </>
              )}
            </div>
          </div>

          {/* Badge de statut */}
          <DownloadItemStatus status={task.status} size={isCompact ? 'sm' : 'md'} />
        </div>
      </div>
    </div>
  );
}

/**
 * Section de progression (barre + métadonnées).
 */
function DownloadItemProgress({ task }: { task: DownloadTask }) {
  const progressPercent = Math.round(task.progress * 100);
  const isActive = task.status === 'running' || task.status === 'downloading';
  const isPaused = task.status === 'paused';

  return (
    <div className="space-y-2">
      {/* Barre de progression */}
      <ProgressBar
        value={progressPercent}
        variant={
          task.status === 'completed'
            ? 'success'
            : task.status === 'failed'
              ? 'error'
              : isPaused
                ? 'warning'
                : 'info'
        }
        size="md"
        shape="pill"
        animated={isActive}
        indeterminate={isActive && progressPercent === 0}
        aria-label={`Download progress: ${progressPercent}%`}
      />

      {/* Métadonnées de progression */}
      <div className="flex items-center justify-between text-xs font-mono">
        {/* Gauche : pages et pourcentage */}
        <div className="flex items-center gap-3">
          <span className="text-text-muted">
            <span className="text-text font-semibold">{task.pages_completed}</span>
            <span className="text-text-dim"> / </span>
            <span>{task.pages_total}</span>
            <span className="text-text-dim ml-1">pages</span>
          </span>
          <span className={cn('font-bold', getStatusColor(task.status))}>
            {progressPercent}%
          </span>
        </div>

        {/* Droite : vitesse et ETA */}
        {(isActive || isPaused) && (
          <div className="flex items-center gap-3 text-text-dim">
            {task.speed_bytes_per_sec > 0 && (
              <span className="flex items-center gap-1">
                <Zap size={10} className="text-secondary" />
                {formatBytes(task.speed_bytes_per_sec)}/s
              </span>
            )}
            {task.estimated_time_remaining_seconds > 0 && (
              <span className="flex items-center gap-1">
                <Clock size={10} className="text-accent" />
                {formatDuration(task.estimated_time_remaining_seconds)}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Métadonnées supplémentaires (taille, dates, chapitres).
 */
function DownloadItemMeta({ task }: { task: DownloadTask }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs font-mono">
      {/* Taille */}
      <div className="flex flex-col">
        <span className="text-text-dim text-[10px] uppercase tracking-wider mb-0.5">
          Size
        </span>
        <span className="text-text">
          {task.downloaded_bytes > 0 ? (
            <>
              <span className="text-secondary font-semibold">
                {formatBytes(task.downloaded_bytes)}
              </span>
              <span className="text-text-dim"> / </span>
              <span>{formatBytes(task.total_size_bytes)}</span>
            </>
          ) : (
            <span className="text-text-dim">—</span>
          )}
        </span>
      </div>

      {/* Chapitres */}
      <div className="flex flex-col">
        <span className="text-text-dim text-[10px] uppercase tracking-wider mb-0.5">
          Chapters
        </span>
        <span className="text-text">
          {task.chapters_count > 0 ? (
            <span className="font-semibold">{task.chapters_count}</span>
          ) : (
            <span className="text-text-dim">—</span>
          )}
        </span>
      </div>

      {/* Priorité */}
      <div className="flex flex-col">
        <span className="text-text-dim text-[10px] uppercase tracking-wider mb-0.5">
          Priority
        </span>
        <span
          className={cn(
            'font-semibold',
            task.priority === 'urgent'
              ? 'text-red-400'
              : task.priority === 'high'
                ? 'text-orange-400'
                : task.priority === 'low'
                  ? 'text-text-dim'
                  : 'text-text'
          )}
        >
          {PRIORITY_LABELS[task.priority] || task.priority}
        </span>
      </div>

      {/* Dates */}
      <div className="flex flex-col">
        <span className="text-text-dim text-[10px] uppercase tracking-wider mb-0.5">
          {task.completed_at ? 'Completed' : task.started_at ? 'Started' : 'Created'}
        </span>
        <span className="text-text">
          {new Date(
            task.completed_at || task.started_at || task.created_at
          ).toLocaleString(undefined, {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
          })}
        </span>
      </div>
    </div>
  );
}

/**
 * Message d'erreur (si la tâche a échoué).
 */
function DownloadItemError({ task }: { task: DownloadTask }) {
  if (task.status !== 'failed' || !task.error_message) {
    return null;
  }

  return (
    <div
      className="flex items-start gap-2 p-2 rounded-md bg-red-500/10 border border-red-500/30"
      role="alert"
    >
      <AlertCircle size={14} className="text-red-400 flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <p className="text-xs font-mono font-semibold text-red-400">Error</p>
        <p className="text-xs font-mono text-red-300/80 mt-0.5 break-words">
          {task.error_message}
        </p>
      </div>
    </div>
  );
}

/**
 * Actions principales (boutons rapides).
 */
function DownloadItemActions({
  task,
  onPause,
  onResume,
  onCancel,
  onRetry,
  onDelete,
  onOpenFolder,
  disabled,
}: {
  task: DownloadTask;
  onPause?: (taskId: string) => void;
  onResume?: (taskId: string) => void;
  onCancel?: (taskId: string) => void;
  onRetry?: (taskId: string) => void;
  onDelete?: (taskId: string) => void;
  onOpenFolder?: (taskId: string) => void;
  disabled?: boolean;
}) {
  const isActive = task.status === 'running' || task.status === 'downloading';
  const isPaused = task.status === 'paused';
  const isCompleted = task.status === 'completed';
  const isFailed = task.status === 'failed';
  const isTerminal = isTerminalStatus(task.status);

  const handleAction = useCallback(
    (e: MouseEvent, action?: (taskId: string) => void) => {
      e.stopPropagation();
      e.preventDefault();
      action?.(task.id);
    },
    [task.id]
  );

  return (
    <div className="flex items-center gap-1.5">
      {/* Pause / Resume */}
      {(isActive || isPaused) && (
        <>
          {isActive && onPause && (
            <button
              type="button"
              onClick={(e) => handleAction(e, onPause)}
              disabled={disabled}
              aria-label="Pause download"
              title="Pause"
              className={cn(
                'p-1.5 rounded-md',
                'bg-surface-alt text-orange-400 border border-orange-500/30',
                'hover:bg-orange-500/20 hover:border-orange-500',
                'transition-all duration-200',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-orange-500',
                'disabled:opacity-50 disabled:cursor-not-allowed'
              )}
            >
              <Pause size={14} />
            </button>
          )}

          {isPaused && onResume && (
            <button
              type="button"
              onClick={(e) => handleAction(e, onResume)}
              disabled={disabled}
              aria-label="Resume download"
              title="Resume"
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
        </>
      )}

      {/* Retry (si failed) */}
      {isFailed && onRetry && (
        <button
          type="button"
          onClick={(e) => handleAction(e, onRetry)}
          disabled={disabled}
          aria-label="Retry download"
          title="Retry"
          className={cn(
            'p-1.5 rounded-md',
            'bg-surface-alt text-secondary border border-secondary/30',
            'hover:bg-secondary/20 hover:border-secondary',
            'transition-all duration-200',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-secondary',
            'disabled:opacity-50 disabled:cursor-not-allowed'
          )}
        >
          <RotateCcw size={14} />
        </button>
      )}

      {/* Cancel (si pas terminal) */}
      {!isTerminal && onCancel && (
        <button
          type="button"
          onClick={(e) => handleAction(e, onCancel)}
          disabled={disabled}
          aria-label="Cancel download"
          title="Cancel"
          className={cn(
            'p-1.5 rounded-md',
            'bg-surface-alt text-red-400 border border-red-500/30',
            'hover:bg-red-500/20 hover:border-red-500',
            'transition-all duration-200',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500',
            'disabled:opacity-50 disabled:cursor-not-allowed'
          )}
        >
          <X size={14} />
        </button>
      )}

      {/* Open folder (si completed) */}
      {isCompleted && onOpenFolder && task.output_path && (
        <button
          type="button"
          onClick={(e) => handleAction(e, onOpenFolder)}
          disabled={disabled}
          aria-label="Open folder"
          title="Open folder"
          className={cn(
            'p-1.5 rounded-md',
            'bg-surface-alt text-green-400 border border-green-500/30',
            'hover:bg-green-500/20 hover:border-green-500',
            'transition-all duration-200',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-green-500',
            'disabled:opacity-50 disabled:cursor-not-allowed'
          )}
        >
          <Folder size={14} />
        </button>
      )}

      {/* Delete (toujours disponible) */}
      {onDelete && (
        <button
          type="button"
          onClick={(e) => handleAction(e, onDelete)}
          disabled={disabled}
          aria-label="Delete download"
          title="Delete"
          className={cn(
            'p-1.5 rounded-md',
            'bg-surface-alt text-text-muted border border-border-dim',
            'hover:bg-red-500/20 hover:border-red-500 hover:text-red-400',
            'transition-all duration-200',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500',
            'disabled:opacity-50 disabled:cursor-not-allowed'
          )}
        >
          <Trash2 size={14} />
        </button>
      )}
    </div>
  );
}

/**
 * Menu d'actions avancées (dropdown).
 */
function DownloadItemMenu({
  task,
  onViewDetails,
  onOpenFolder,
  onDelete,
  disabled,
}: {
  task: DownloadTask;
  onViewDetails?: (taskId: string) => void;
  onOpenFolder?: (taskId: string) => void;
  onDelete?: (taskId: string) => void;
  disabled?: boolean;
}) {
  const [isOpen, setIsOpen] = useState(false);

  const handleAction = useCallback(
    (action?: (taskId: string) => void) => {
      action?.(task.id);
      setIsOpen(false);
    },
    [task.id]
  );

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        disabled={disabled}
        aria-label="More actions"
        aria-expanded={isOpen}
        aria-haspopup="menu"
        className={cn(
          'p-1.5 rounded-md',
          'bg-surface-alt text-text-muted border border-border-dim',
          'hover:bg-surface-hover hover:border-secondary hover:text-secondary',
          'transition-all duration-200',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
          'disabled:opacity-50 disabled:cursor-not-allowed'
        )}
      >
        <MoreVertical size={14} />
      </button>

      {/* Dropdown menu */}
      {isOpen && (
        <>
          {/* Overlay pour fermer */}
          <div
            className="fixed inset-0 z-40"
            onClick={() => setIsOpen(false)}
            aria-hidden="true"
          />

          {/* Menu */}
          <div
            role="menu"
            className={cn(
              'absolute right-0 top-full mt-1 z-50',
              'min-w-[180px] rounded-lg border-2 border-border-dim',
              'bg-surface shadow-2xl overflow-hidden',
              'animate-in fade-in-0 zoom-in-95 slide-in-from-top-2 duration-200'
            )}
          >
            {/* View details */}
            {onViewDetails && (
              <button
                type="button"
                role="menuitem"
                onClick={() => handleAction(onViewDetails)}
                className={cn(
                  'w-full flex items-center gap-2 px-3 py-2 text-left',
                  'text-sm font-mono text-text',
                  'hover:bg-surface-hover hover:text-secondary',
                  'transition-colors duration-150',
                  'focus:outline-none focus-visible:bg-surface-hover'
                )}
              >
                <Info size={14} />
                <span>View Details</span>
              </button>
            )}

            {/* Open folder */}
            {onOpenFolder && task.output_path && task.status === 'completed' && (
              <button
                type="button"
                role="menuitem"
                onClick={() => handleAction(onOpenFolder)}
                className={cn(
                  'w-full flex items-center gap-2 px-3 py-2 text-left',
                  'text-sm font-mono text-text',
                  'hover:bg-surface-hover hover:text-secondary',
                  'transition-colors duration-150',
                  'focus:outline-none focus-visible:bg-surface-hover'
                )}
              >
                <Folder size={14} />
                <span>Open Folder</span>
              </button>
            )}

            {/* Separator */}
            {(onViewDetails || onOpenFolder) && onDelete && (
              <div className="border-t border-border-dim" />
            )}

            {/* Delete */}
            {onDelete && (
              <button
                type="button"
                role="menuitem"
                onClick={() => handleAction(onDelete)}
                className={cn(
                  'w-full flex items-center gap-2 px-3 py-2 text-left',
                  'text-sm font-mono text-red-400',
                  'hover:bg-red-500/10 hover:text-red-300',
                  'transition-colors duration-150',
                  'focus:outline-none focus-visible:bg-red-500/10'
                )}
              >
                <Trash2 size={14} />
                <span>Delete</span>
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ============================================================================
// COMPOSANT PRINCIPAL : DownloadItem
// ============================================================================

/**
 * DownloadItem - Affiche une tâche de téléchargement individuelle.
 *
 * @param props - Props du composant
 * @returns Élément JSX du DownloadItem
 *
 * @example
 * ```tsx
 * // Liste de téléchargements
 * <div className="space-y-2">
 *   {tasks.map((task) => (
 *     <DownloadItem
 *       key={task.id}
 *       task={task}
 *       onPause={handlePause}
 *       onResume={handleResume}
 *       onCancel={handleCancel}
 *       onDelete={handleDelete}
 *     />
 *   ))}
 * </div>
 *
 * // Avec sélection multiple
 * <DownloadItem
 *   task={task}
 *   selectable
 *   isSelected={selectedIds.includes(task.id)}
 *   onSelectionChange={(id, selected) => toggleSelection(id, selected)}
 * />
 * ```
 */
export function DownloadItem({
  task,
  variant = 'default',
  selectable = false,
  isSelected = false,
  onSelectionChange,
  onPause,
  onResume,
  onCancel,
  onRetry,
  onDelete,
  onOpenFolder,
  onViewDetails,
  showHistory = false,
  showMetadata = true,
  className,
  disabled = false,
}: DownloadItemProps) {
  const [isExpanded, setIsExpanded] = useState(variant === 'expanded');

  const statusConfig = STATUS_CONFIG[task.status];
  const isCompact = variant === 'compact';
  const isExpanded2 = variant === 'expanded' || isExpanded;

  const toggleExpand = useCallback(() => {
    setIsExpanded((prev) => !prev);
  }, []);

  return (
    <article
      role="article"
      aria-label={`Download: ${task.manga_title}`}
      className={cn(
        'group relative rounded-lg border-2 bg-surface overflow-hidden',
        'transition-all duration-300',
        statusConfig.borderColor,
        statusConfig.glowColor,
        'hover:border-secondary/50',
        isSelected && 'border-primary bg-primary-bg/30',
        disabled && 'opacity-50 cursor-not-allowed',
        className
      )}
    >
      {/* Indicateur de statut (barre latérale) */}
      <div
        className={cn(
          'absolute left-0 top-0 bottom-0 w-1',
          statusConfig.bgColor,
          statusConfig.animated && 'animate-pulse'
        )}
        aria-hidden="true"
      />

      {/* Contenu principal */}
      <div className="pl-3 pr-4 py-3">
        {/* En-tête */}
        <DownloadItemHeader
          task={task}
          variant={variant}
          selectable={selectable}
          isSelected={isSelected}
          onSelectionChange={onSelectionChange}
        />

        {/* Progression */}
        {!isCompact && (
          <div className="mt-3">
            <DownloadItemProgress task={task} />
          </div>
        )}

        {/* Métadonnées (si expanded ou default) */}
        {!isCompact && showMetadata && (
          <div className="mt-3">
            <DownloadItemMeta task={task} />
          </div>
        )}

        {/* Message d'erreur */}
        {task.status === 'failed' && (
          <div className="mt-3">
            <DownloadItemError task={task} />
          </div>
        )}

        {/* Actions */}
        <div className="mt-3 flex items-center justify-between gap-2">
          {/* Gauche : Bouton expand (si default) */}
          <div className="flex items-center gap-2">
            {variant === 'default' && (
              <button
                type="button"
                onClick={toggleExpand}
                aria-label={isExpanded ? 'Collapse details' : 'Expand details'}
                aria-expanded={isExpanded}
                className={cn(
                  'flex items-center gap-1 px-2 py-1 rounded-md',
                  'text-xs font-mono text-text-muted',
                  'hover:bg-surface-hover hover:text-secondary',
                  'transition-all duration-200',
                  'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary'
                )}
              >
                {isExpanded ? (
                  <>
                    <ChevronDown size={12} />
                    <span>Less</span>
                  </>
                ) : (
                  <>
                    <ChevronRight size={12} />
                    <span>More</span>
                  </>
                )}
              </button>
            )}

            {/* Chemin de sortie (si completed) */}
            {task.status === 'completed' && task.output_path && (
              <span
                className="text-[10px] font-mono text-text-dim truncate max-w-[200px]"
                title={task.output_path}
              >
                <Folder size={10} className="inline mr-1" />
                {task.output_path}
              </span>
            )}
          </div>

          {/* Droite : Actions */}
          <div className="flex items-center gap-2">
            <DownloadItemActions
              task={task}
              onPause={onPause}
              onResume={onResume}
              onCancel={onCancel}
              onRetry={onRetry}
              onDelete={onDelete}
              onOpenFolder={onOpenFolder}
              disabled={disabled}
            />

            <DownloadItemMenu
              task={task}
              onViewDetails={onViewDetails}
              onOpenFolder={onOpenFolder}
              onDelete={onDelete}
              disabled={disabled}
            />
          </div>
        </div>

        {/* Contenu expanded (si expanded ou isExpanded) */}
        {isExpanded2 && variant !== 'compact' && (
          <div className="mt-3 pt-3 border-t border-border-dim space-y-2 animate-in fade-in slide-in-from-top-2 duration-200">
            {/* Détails techniques */}
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs font-mono">
              <div>
                <span className="text-text-dim text-[10px] uppercase tracking-wider">
                  Task ID
                </span>
                <p className="text-text-muted truncate" title={task.id}>
                  {task.id}
                </p>
              </div>
              <div>
                <span className="text-text-dim text-[10px] uppercase tracking-wider">
                  Manga ID
                </span>
                <p className="text-text-muted truncate" title={task.manga_id}>
                  {task.manga_id}
                </p>
              </div>
              <div>
                <span className="text-text-dim text-[10px] uppercase tracking-wider">
                  Created
                </span>
                <p className="text-text-muted">
                  {new Date(task.created_at).toLocaleString()}
                </p>
              </div>
              {task.started_at && (
                <div>
                  <span className="text-text-dim text-[10px] uppercase tracking-wider">
                    Started
                  </span>
                  <p className="text-text-muted">
                    {new Date(task.started_at).toLocaleString()}
                  </p>
                </div>
              )}
              {task.completed_at && (
                <div>
                  <span className="text-text-dim text-[10px] uppercase tracking-wider">
                    Completed
                  </span>
                  <p className="text-text-muted">
                    {new Date(task.completed_at).toLocaleString()}
                  </p>
                </div>
              )}
            </div>

            {/* Historique d'erreurs (placeholder) */}
            {showHistory && task.status === 'failed' && (
              <div className="p-2 rounded-md bg-surface-alt/50 border border-border-dim">
                <p className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider mb-1">
                  Error History
                </p>
                <p className="text-xs font-mono text-text-muted">
                  No detailed history available
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </article>
  );
}

// ============================================================================
// VARIANTE COMPACTE
// ============================================================================

/**
 * DownloadItemCompact - Version compacte de l'item de téléchargement.
 *
 * Optimisée pour les listes denses avec moins d'informations.
 *
 * @param props - Props du composant
 */
export function DownloadItemCompact(props: Omit<DownloadItemProps, 'variant'>) {
  return <DownloadItem {...props} variant="compact" showMetadata={false} />;
}

// ============================================================================
// VARIANTE EXPANDED
// ============================================================================

/**
 * DownloadItemExpanded - Version expanded de l'item de téléchargement.
 *
 * Affiche toutes les informations disponibles avec détails techniques.
 *
 * @param props - Props du composant
 */
export function DownloadItemExpanded(props: Omit<DownloadItemProps, 'variant'>) {
  return <DownloadItem {...props} variant="expanded" showMetadata showHistory />;
}

// ============================================================================
// SKELETON LOADER
// ============================================================================

/**
 * DownloadItemSkeleton - Skeleton loader pour l'état de chargement.
 *
 * @param props - Props du composant
 *
 * @example
 * ```tsx
 * // Liste de chargement
 * <div className="space-y-2">
 *   {Array.from({ length: 5 }).map((_, i) => (
 *     <DownloadItemSkeleton key={i} />
 *   ))}
 * </div>
 * ```
 */
export function DownloadItemSkeleton({
  variant = 'default',
  className,
}: DownloadItemSkeletonProps) {
  const isCompact = variant === 'compact';

  return (
    <div
      className={cn(
        'relative rounded-lg border-2 border-border-dim bg-surface overflow-hidden',
        'animate-pulse',
        className
      )}
      aria-hidden="true"
    >
      {/* Barre latérale */}
      <div className="absolute left-0 top-0 bottom-0 w-1 bg-surface-alt" />

      {/* Contenu */}
      <div className="pl-3 pr-4 py-3 space-y-3">
        {/* En-tête */}
        <div className="flex items-start gap-3">
          <div className="flex-1 space-y-2">
            <div className="h-5 bg-surface-alt rounded w-3/4" />
            <div className="h-3 bg-surface-alt rounded w-1/2" />
          </div>
          <div className="h-5 w-20 bg-surface-alt rounded" />
        </div>

        {/* Progression */}
        {!isCompact && (
          <>
            <div className="h-3 bg-surface-alt rounded-full" />
            <div className="flex justify-between">
              <div className="h-3 bg-surface-alt rounded w-24" />
              <div className="h-3 bg-surface-alt rounded w-16" />
            </div>
          </>
        )}

        {/* Métadonnées */}
        {!isCompact && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="space-y-1">
                <div className="h-2 bg-surface-alt rounded w-12" />
                <div className="h-3 bg-surface-alt rounded w-16" />
              </div>
            ))}
          </div>
        )}

        {/* Actions */}
        <div className="flex justify-between">
          <div className="h-6 w-16 bg-surface-alt rounded" />
          <div className="flex gap-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-7 w-7 bg-surface-alt rounded" />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * DownloadItemSkeletonList - Liste de skeletons pour le chargement.
 *
 * @param props - Props du composant
 */
export function DownloadItemSkeletonList({
  variant = 'default',
  count = 5,
  className,
}: DownloadItemSkeletonProps & { count?: number }) {
  return (
    <div className={cn('space-y-2', className)}>
      {Array.from({ length: count }).map((_, i) => (
        <DownloadItemSkeleton key={i} variant={variant} />
      ))}
    </div>
  );
}

// ============================================================================
// COMPOSANT UTILITAIRE : EmptyState
// ============================================================================

/**
 * DownloadItemEmpty - État vide pour la liste des téléchargements.
 *
 * @param props - Props du composant
 */
export function DownloadItemEmpty({
  title = 'No downloads',
  description = 'Start downloading manga to see them here',
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
        'flex flex-col items-center justify-center py-16 px-4 text-center',
        'rounded-lg border-2 border-dashed border-border-dim bg-surface/50',
        className
      )}
    >
      <div
        className="flex items-center justify-center h-16 w-16 rounded-full bg-surface-alt border-2 border-border-dim mb-4"
        aria-hidden="true"
      >
        <Download size={28} className="text-text-dim" />
      </div>

      <h3 className="text-lg font-mono font-bold text-text mb-1">{title}</h3>
      <p className="text-sm font-mono text-text-muted max-w-md">{description}</p>

      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

// ============================================================================
// EXPORTS
// ============================================================================

export default DownloadItem;
export {
  DownloadItemCompact,
  DownloadItemExpanded,
  DownloadItemSkeleton,
  DownloadItemSkeletonList,
  DownloadItemEmpty,
  DownloadItemStatus,
  DownloadItemHeader,
  DownloadItemProgress,
  DownloadItemMeta,
  DownloadItemError,
  DownloadItemActions,
  DownloadItemMenu,
  STATUS_CONFIG,
  FORMAT_LABELS,
  QUALITY_LABELS,
  PRIORITY_LABELS,
};
export type { DownloadItemVariant };
