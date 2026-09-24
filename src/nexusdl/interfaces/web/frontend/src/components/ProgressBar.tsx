/**
 * Composant ProgressBar pour NexusDL.
 *
 * Barre de progression hautement personnalisable avec un style cyberpunk néon.
 * Supporte les états déterminés (pourcentage connu) et indéterminés (chargement en cours).
 *
 * Caractéristiques :
 *   - 6 variantes de couleurs (default, success, warning, error, info, accent)
 *   - 3 tailles (sm, md, lg)
 *   - 3 formes (rounded, pill, square)
 *   - Animation de rayures (stripes) pour les tâches actives
 *   - État indéterminé avec animation de balayage
 *   - Affichage optionnel du pourcentage ou d'un label personnalisé
 *   - Accessibilité complète (ARIA roles et attributes)
 *   - Style cyberpunk néon avec effets de glow (drop-shadow)
 *   - Composant bonus : ProgressRing (progression circulaire)
 *
 * Utilisation :
 *   // Barre de progression simple
 *   <ProgressBar value={75} />
 *
 *   // Avec label et animation
 *   <ProgressBar 
 *     value={45} 
 *     label="Downloading Chapter 42" 
 *     showValue 
 *     animated 
 *   />
 *
 *   // État indéterminé (chargement)
 *   <ProgressBar indeterminate variant="info" />
 *
 *   // Anneau de progression (pour la lecture)
 *   <ProgressRing value={60} size={64} strokeWidth={6} />
 *
 * @module components/ProgressBar
 */

'use client';

import { useMemo } from 'react';
import { Check, Loader2, AlertCircle } from 'lucide-react';

import { cn } from '@/lib/utils';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Variantes de couleur disponibles.
 */
export type ProgressVariant = 'default' | 'success' | 'warning' | 'error' | 'info' | 'accent';

/**
 * Tailles disponibles.
 */
export type ProgressSize = 'sm' | 'md' | 'lg';

/**
 * Formes disponibles.
 */
export type ProgressShape = 'rounded' | 'pill' | 'square';

/**
 * Props du composant ProgressBar.
 */
export interface ProgressBarProps {
  /** Valeur actuelle de la progression. */
  value?: number;
  /** Valeur maximale (défaut: 100). */
  max?: number;
  /** Valeur minimale (défaut: 0). */
  min?: number;
  /** Label textuel à afficher au-dessus ou à l'intérieur. */
  label?: string;
  /** Afficher le pourcentage calculé. */
  showValue?: boolean;
  /** Variante de couleur. */
  variant?: ProgressVariant;
  /** Taille de la barre. */
  size?: ProgressSize;
  /** Forme des bords. */
  shape?: ProgressShape;
  /** Activer l'animation de rayures (pour les tâches actives). */
  animated?: boolean;
  /** Mode indéterminé (quand la progression est inconnue). */
  indeterminate?: boolean;
  /** Classe CSS additionnelle pour le conteneur. */
  className?: string;
  /** Classe CSS additionnelle pour la barre de progression. */
  barClassName?: string;
  /** Classe CSS additionnelle pour le label. */
  labelClassName?: string;
  /** Désactiver la barre. */
  disabled?: boolean;
}

/**
 * Props du composant ProgressRing.
 */
export interface ProgressRingProps {
  /** Valeur actuelle (0-100). */
  value: number;
  /** Taille totale du composant (px). */
  size?: number;
  /** Épaisseur du trait (px). */
  strokeWidth?: number;
  /** Variante de couleur. */
  variant?: ProgressVariant;
  /** Afficher le pourcentage au centre. */
  showValue?: boolean;
  /** Label optionnel sous l'anneau. */
  label?: string;
  /** Classe CSS additionnelle. */
  className?: string;
}

// ============================================================================
// CONSTANTES & CONFIGURATIONS
// ============================================================================

/**
 * Configuration des variantes (couleurs et effets de glow).
 */
const VARIANT_CONFIG: Record<ProgressVariant, {
  bg: string;
  glow: string;
  text: string;
  stripeColor: string;
}> = {
  default: {
    bg: 'bg-primary',
    glow: 'shadow-[0_0_12px_rgba(0,255,65,0.6)]',
    text: 'text-primary',
    stripeColor: 'rgba(0, 255, 65, 0.3)',
  },
  success: {
    bg: 'bg-green-500',
    glow: 'shadow-[0_0_12px_rgba(34,197,94,0.6)]',
    text: 'text-green-400',
    stripeColor: 'rgba(34, 197, 94, 0.3)',
  },
  warning: {
    bg: 'bg-yellow-500',
    glow: 'shadow-[0_0_12px_rgba(234,179,8,0.6)]',
    text: 'text-yellow-400',
    stripeColor: 'rgba(234, 179, 8, 0.3)',
  },
  error: {
    bg: 'bg-red-500',
    glow: 'shadow-[0_0_12px_rgba(239,68,68,0.6)]',
    text: 'text-red-400',
    stripeColor: 'rgba(239, 68, 68, 0.3)',
  },
  info: {
    bg: 'bg-cyan-500',
    glow: 'shadow-[0_0_12px_rgba(6,182,212,0.6)]',
    text: 'text-cyan-400',
    stripeColor: 'rgba(6, 182, 212, 0.3)',
  },
  accent: {
    bg: 'bg-accent',
    glow: 'shadow-[0_0_12px_rgba(255,0,255,0.6)]',
    text: 'text-accent',
    stripeColor: 'rgba(255, 0, 255, 0.3)',
  },
};

/**
 * Configuration des tailles.
 */
const SIZE_CONFIG: Record<ProgressSize, {
  height: string;
  textSize: string;
  iconSize: number;
}> = {
  sm: { height: 'h-1.5', textSize: 'text-[10px]', iconSize: 12 },
  md: { height: 'h-3', textSize: 'text-xs', iconSize: 14 },
  lg: { height: 'h-5', textSize: 'text-sm', iconSize: 16 },
};

/**
 * Configuration des formes.
 */
const SHAPE_CONFIG: Record<ProgressShape, {
  container: string;
  bar: string;
}> = {
  rounded: { container: 'rounded-md', bar: 'rounded-md' },
  pill: { container: 'rounded-full', bar: 'rounded-full' },
  square: { container: 'rounded-sm', bar: 'rounded-sm' },
};

// ============================================================================
// COMPOSANT PRINCIPAL : ProgressBar
// ============================================================================

/**
 * ProgressBar - Barre de progression linéaire avec style cyberpunk.
 *
 * @param props - Props du composant
 * @returns Élément JSX de la ProgressBar
 */
export function ProgressBar({
  value = 0,
  max = 100,
  min = 0,
  label,
  showValue = false,
  variant = 'default',
  size = 'md',
  shape = 'rounded',
  animated = false,
  indeterminate = false,
  className,
  barClassName,
  labelClassName,
  disabled = false,
}: ProgressBarProps) {
  // Calculer le pourcentage
  const percentage = useMemo(() => {
    if (indeterminate) return 0;
    const pct = ((value - min) / (max - min)) * 100;
    return Math.min(100, Math.max(0, pct));
  }, [value, min, max, indeterminate]);

  const config = VARIANT_CONFIG[variant];
  const sizeConfig = SIZE_CONFIG[size];
  const shapeConfig = SHAPE_CONFIG[shape];

  // Style d'animation des rayures
  const stripeStyle = animated && !indeterminate ? {
    backgroundImage: `linear-gradient(
      45deg,
      ${config.stripeColor} 25%,
      transparent 25%,
      transparent 50%,
      ${config.stripeColor} 50%,
      ${config.stripeColor} 75%,
      transparent 75%,
      transparent
    )`,
    backgroundSize: '1rem 1rem',
    animation: 'progress-stripes 1s linear infinite',
  } : {};

  return (
    <div className={cn('w-full', className)}>
      {/* Label et Pourcentage */}
      {(label || showValue) && (
        <div
          className={cn(
            'flex items-center justify-between mb-1.5',
            sizeConfig.textSize,
            'font-mono',
            labelClassName
          )}
        >
          {label && (
            <span className={cn('font-semibold truncate', config.text)}>
              {label}
            </span>
          )}
          {showValue && !indeterminate && (
            <span className="text-text-muted">
              {Math.round(percentage)}%
            </span>
          )}
          {indeterminate && (
            <span className={cn('flex items-center gap-1', config.text)}>
              <Loader2 size={sizeConfig.iconSize} className="animate-spin" />
              <span>Processing...</span>
            </span>
          )}
        </div>
      )}

      {/* Conteneur de la barre */}
      <div
        role="progressbar"
        aria-valuenow={indeterminate ? undefined : Math.round(percentage)}
        aria-valuemin={min}
        aria-valuemax={max}
        aria-label={label || 'Progress'}
        aria-busy={indeterminate}
        className={cn(
          'relative w-full overflow-hidden',
          'bg-surface-alt border border-border-dim',
          'transition-all duration-300',
          shapeConfig.container,
          sizeConfig.height,
          disabled && 'opacity-50 cursor-not-allowed'
        )}
      >
        {/* Barre de progression */}
        <div
          className={cn(
            'h-full transition-all duration-500 ease-out',
            config.bg,
            shapeConfig.bar,
            !indeterminate && config.glow,
            barClassName
          )}
          style={{
            width: indeterminate ? '100%' : `${percentage}%`,
            ...stripeStyle,
          }}
        >
          {/* Animation indéterminée */}
          {indeterminate && (
            <div
              className="absolute inset-0 overflow-hidden"
              style={{
                backgroundImage: `linear-gradient(
                  90deg,
                  transparent,
                  rgba(255, 255, 255, 0.3),
                  transparent
                )`,
                backgroundSize: '200% 100%',
                animation: 'progress-shimmer 1.5s ease-in-out infinite',
              }}
            />
          )}
        </div>

        {/* Icône de complétion à 100% */}
        {!indeterminate && percentage >= 100 && (
          <div className="absolute right-1 top-1/2 -translate-y-1/2">
            <Check
              size={sizeConfig.iconSize}
              className={cn('text-background drop-shadow-md', config.text)}
            />
          </div>
        )}
      </div>

      {/* Style global pour les animations (injecté une seule fois) */}
      <style jsx global>{`
        @keyframes progress-stripes {
          0% { background-position: 1rem 0; }
          100% { background-position: 0 0; }
        }
        @keyframes progress-shimmer {
          0% { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }
      `}</style>
    </div>
  );
}

// ============================================================================
// COMPOSANT BONUS : ProgressRing
// ============================================================================

/**
 * ProgressRing - Anneau de progression circulaire.
 *
 * Idéal pour afficher la progression de lecture d'un chapitre ou d'un manga
 * dans un espace compact (ex: carte de manga).
 *
 * @param props - Props du composant
 * @returns Élément JSX du ProgressRing
 */
export function ProgressRing({
  value,
  size = 64,
  strokeWidth = 6,
  variant = 'default',
  showValue = false,
  label,
  className,
}: ProgressRingProps) {
  const config = VARIANT_CONFIG[variant];
  const radius = (size - strokeWidth) / 2;
  const circumference = radius * 2 * Math.PI;
  const offset = circumference - (value / 100) * circumference;

  return (
    <div className={cn('relative flex flex-col items-center', className)}>
      <div className="relative" style={{ width: size, height: size }}>
        {/* SVG Anneau */}
        <svg
          width={size}
          height={size}
          viewBox={`0 0 ${size} ${size}`}
          className="transform -rotate-90"
          role="progressbar"
          aria-valuenow={value}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={label || 'Circular progress'}
        >
          {/* Anneau de fond */}
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="currentColor"
            strokeWidth={strokeWidth}
            className="text-surface-alt"
          />
          {/* Anneau de progression */}
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="currentColor"
            strokeWidth={strokeWidth}
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            strokeLinecap="round"
            className={cn(
              config.text,
              'transition-all duration-700 ease-out',
              value >= 100 ? config.glow : ''
            )}
            style={{
              filter: value >= 100 ? undefined : 'none',
            }}
          />
        </svg>

        {/* Contenu central */}
        <div className="absolute inset-0 flex items-center justify-center">
          {value >= 100 ? (
            <Check size={size * 0.4} className={cn(config.text, 'drop-shadow-md')} />
          ) : showValue ? (
            <span className={cn('font-mono font-bold', config.text)} style={{ fontSize: size * 0.25 }}>
              {Math.round(value)}%
            </span>
          ) : null}
        </div>
      </div>

      {/* Label sous l'anneau */}
      {label && (
        <span className="mt-1.5 text-[10px] font-mono text-text-muted text-center truncate max-w-full">
          {label}
        </span>
      )}
    </div>
  );
}

// ============================================================================
// COMPOSANT : ProgressCard (pour les tâches de téléchargement)
// ============================================================================

/**
 * ProgressCard - Carte de progression pour les tâches de téléchargement.
 *
 * Combine une ProgressBar avec des métadonnées (vitesse, ETA, taille).
 *
 * @param props - Props du composant
 * @returns Élément JSX de la ProgressCard
 */
export interface ProgressCardProps extends Omit<ProgressBarProps, 'label' | 'showValue'> {
  /** Titre de la tâche (ex: nom du manga). */
  title: string;
  /** Sous-titre (ex: nom du chapitre). */
  subtitle?: string;
  /** Vitesse actuelle (ex: "1.2 MB/s"). */
  speed?: string;
  /** Temps restant estimé (ex: "2m 30s"). */
  eta?: string;
  /** Taille téléchargée / totale (ex: "15.4 / 45.2 MB"). */
  size?: string;
  /** Statut de la tâche. */
  status?: 'downloading' | 'paused' | 'completed' | 'failed';
  /** Action secondaire (ex: bouton pause/annuler). */
  action?: React.ReactNode;
}

export function ProgressCard({
  title,
  subtitle,
  speed,
  eta,
  size,
  status = 'downloading',
  action,
  value = 0,
  variant = 'default',
  size: barSize = 'md',
  className,
  ...progressProps
}: ProgressCardProps) {
  const statusConfig = {
    downloading: { icon: null, variant: 'info' as ProgressVariant, label: 'Downloading' },
    paused: { icon: null, variant: 'warning' as ProgressVariant, label: 'Paused' },
    completed: { icon: Check, variant: 'success' as ProgressVariant, label: 'Completed' },
    failed: { icon: AlertCircle, variant: 'error' as ProgressVariant, label: 'Failed' },
  };

  const currentStatus = statusConfig[status];

  return (
    <div
      className={cn(
        'group relative p-4 rounded-xl border',
        'bg-surface border-border-dim',
        'hover:border-secondary/50 transition-all duration-300',
        'hover:shadow-[0_0_15px_rgba(0,255,255,0.1)]',
        className
      )}
    >
      {/* En-tête */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex-1 min-w-0 mr-4">
          <h4 className="font-mono font-semibold text-text truncate">
            {title}
          </h4>
          {subtitle && (
            <p className="text-xs font-mono text-text-muted truncate mt-0.5">
              {subtitle}
            </p>
          )}
        </div>

        {/* Badge de statut */}
        <div className="flex items-center gap-2 flex-shrink-0">
          <span
            className={cn(
              'inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase',
              'bg-surface-alt border border-border-dim',
              VARIANT_CONFIG[currentStatus.variant].text
            )}
          >
            {currentStatus.icon && <currentStatus.icon size={10} />}
            {currentStatus.label}
          </span>
          {action}
        </div>
      </div>

      {/* Barre de progression */}
      <ProgressBar
        value={value}
        variant={currentStatus.variant}
        size={barSize}
        showValue
        animated={status === 'downloading'}
        indeterminate={status === 'downloading' && value === 0}
        className="mb-3"
        {...progressProps}
      />

      {/* Métadonnées */}
      {(speed || eta || size) && (
        <div className="flex items-center justify-between text-[10px] font-mono text-text-dim">
          <div className="flex items-center gap-3">
            {speed && (
              <span className="flex items-center gap-1">
                <span className="text-secondary">⚡</span> {speed}
              </span>
            )}
            {eta && (
              <span className="flex items-center gap-1">
                <span className="text-accent">⏱</span> {eta}
              </span>
            )}
          </div>
          {size && <span>{size}</span>}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// EXPORTS
// ============================================================================

export default ProgressBar;
export { ProgressRing, ProgressCard };
export type { ProgressVariant, ProgressSize, ProgressShape, ProgressRingProps, ProgressCardProps };
