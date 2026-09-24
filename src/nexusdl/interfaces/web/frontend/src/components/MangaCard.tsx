/**
 * Composant MangaCard pour NexusDL.
 *
 * Carte d'affichage d'un manga avec style cyberpunk néon. Utilisée dans :
 *   - Grille de résultats de recherche
 *   - Bibliothèque locale
 *   - Section "Continue Reading"
 *   - Recommandations et reading lists
 *
 * Caractéristiques :
 *   - 4 variantes : default, compact, detailed, horizontal
 *   - Couverture avec lazy loading et placeholder coloré
 *   - Badges de statut (publication, langue, 18+, nouveau)
 *   - Progression de lecture (barre linéaire ou anneau circulaire)
 *   - Overlay d'actions rapides au hover (read, download, library)
 *   - Skeleton loader pour l'état de chargement
 *   - Accessibilité complète (ARIA, focus, clavier)
 *   - Responsive (mobile, tablette, desktop)
 *   - Style cyberpunk néon cohérent (glow, gradients, animations)
 *
 * Utilisation :
 *   // Variante par défaut (grille)
 *   <MangaCard manga={manga} onClick={() => router.push(`/manga/${manga.id}`)} />
 *
 *   // Avec progression de lecture
 *   <MangaCard manga={manga} showProgress variant="detailed" />
 *
 *   // Compacte (pour listes denses)
 *   <MangaCardCompact manga={manga} />
 *
 *   // Horizontale (pour "Continue Reading")
 *   <MangaCardHorizontal manga={manga} onRead={handleRead} />
 *
 *   // Skeleton loader
 *   <MangaCardSkeleton />
 *
 * @module components/MangaCard
 */

'use client';

import { useCallback, useMemo, useState, type MouseEvent } from 'react';
import Image from 'next/image';
import {
  BookOpen,
  Download,
  Eye,
  EyeOff,
  Globe,
  Heart,
  Info,
  Library,
  LibraryBig,
  Play,
  Plus,
  Star,
  X,
} from 'lucide-react';

import { cn } from '@/lib/utils';
import {
  calculateReadingProgress,
  extractDomainFromUrl,
  getMangaStatusColor,
  getMangaStatusIcon,
  getReadingStatusIcon,
  truncateDescription,
} from '@/types/manga';

import { ProgressBar, ProgressRing } from '@/components/ProgressBar';

import type { Manga, ReadingStatus } from '@/types/manga';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Variantes de la carte manga.
 */
export type MangaCardVariant = 'default' | 'compact' | 'detailed' | 'horizontal';

/**
 * Ratio d'aspect de la couverture.
 */
export type CoverAspectRatio = 'portrait' | 'square' | 'landscape';

/**
 * Position de la progression.
 */
export type ProgressPosition = 'bar' | 'ring' | 'none';

/**
 * Props du composant MangaCard.
 */
export interface MangaCardProps {
  /** Données du manga à afficher. */
  manga: Manga;
  /** Variante d'affichage. */
  variant?: MangaCardVariant;
  /** Ratio d'aspect de la couverture. */
  aspectRatio?: CoverAspectRatio;
  /** Position du indicateur de progression. */
  progressPosition?: ProgressPosition;
  /** Afficher la progression de lecture. */
  showProgress?: boolean;
  /** Afficher les tags. */
  showTags?: boolean;
  /** Nombre maximum de tags affichés. */
  maxTags?: number;
  /** Afficher les actions rapides au hover. */
  showActions?: boolean;
  /** Afficher le badge de statut de publication. */
  showStatus?: boolean;
  /** Afficher le badge de langue. */
  showLanguage?: boolean;
  /** Afficher la description (variante detailed). */
  showDescription?: boolean;
  /** Longueur maximale de la description. */
  descriptionLength?: number;
  /** Charger l'image en priorité (LCP). */
  priority?: boolean;
  /** Callback au clic sur la carte. */
  onClick?: (manga: Manga) => void;
  /** Callback pour lire le manga. */
  onRead?: (manga: Manga) => void;
  /** Callback pour télécharger le manga. */
  onDownload?: (manga: Manga) => void;
  /** Callback pour ajouter à la bibliothèque. */
  onAddToLibrary?: (manga: Manga) => void;
  /** Callback pour retirer de la bibliothèque. */
  onRemoveFromLibrary?: (manga: Manga) => void;
  /** Callback pour basculer le statut favori. */
  onToggleFavorite?: (manga: Manga) => void;
  /** Indique si le manga est marqué comme favori. */
  isFavorite?: boolean;
  /** Classe CSS additionnelle. */
  className?: string;
  /** Désactiver les interactions. */
  disabled?: boolean;
}

/**
 * Props du composant MangaCardSkeleton.
 */
export interface MangaCardSkeletonProps {
  /** Variante du skeleton. */
  variant?: MangaCardVariant;
  /** Nombre de cartes à afficher. */
  count?: number;
  /** Classe CSS additionnelle. */
  className?: string;
}

// ============================================================================
// CONSTANTES
// ============================================================================

/**
 * Configuration des ratios d'aspect.
 */
const ASPECT_RATIOS: Record<CoverAspectRatio, string> = {
  portrait: 'aspect-[2/3]',
  square: 'aspect-square',
  landscape: 'aspect-[3/2]',
};

/**
 * Couleurs de placeholder basées sur le titre (hash simple).
 */
const PLACEHOLDER_COLORS = [
  'from-primary/30 to-secondary/30',
  'from-accent/30 to-primary/30',
  'from-secondary/30 to-accent/30',
  'from-primary/40 to-accent/20',
  'from-cyan-500/30 to-primary/30',
  'from-purple-500/30 to-secondary/30',
] as const;

/**
 * Drapeaux de langues courantes.
 */
const LANGUAGE_FLAGS: Record<string, string> = {
  en: '🇬🇧',
  fr: '🇫🇷',
  es: '🇪🇸',
  de: '🇩🇪',
  it: '🇮🇹',
  pt: '🇵🇹',
  ru: '🇷🇺',
  ja: '🇯🇵',
  ko: '🇰🇷',
  zh: '🇨🇳',
  ar: '🇸🇦',
  pl: '🇵🇱',
  tr: '🇹🇷',
  nl: '🇳🇱',
  vi: '🇻🇳',
  th: '🇹🇭',
  id: '🇮🇩',
  hi: '🇮🇳',
  multi: '🌍',
};

// ============================================================================
// HELPERS
// ============================================================================

/**
 * Génère une couleur de placeholder basée sur le titre du manga.
 */
function getPlaceholderColor(title: string): string {
  let hash = 0;
  for (let i = 0; i < title.length; i++) {
    hash = title.charCodeAt(i) + ((hash << 5) - hash);
  }
  const index = Math.abs(hash) % PLACEHOLDER_COLORS.length;
  return PLACEHOLDER_COLORS[index];
}

/**
 * Récupère le drapeau pour un code langue.
 */
function getLanguageFlag(language: string): string {
  return LANGUAGE_FLAGS[language.toLowerCase()] || '🏳️';
}

// ============================================================================
// SOUS-COMPOSANTS
// ============================================================================

/**
 * Couverture du manga avec lazy loading et placeholder.
 */
function MangaCardCover({
  manga,
  aspectRatio,
  priority,
  variant,
}: {
  manga: Manga;
  aspectRatio: CoverAspectRatio;
  priority?: boolean;
  variant: MangaCardVariant;
}) {
  const [imageError, setImageError] = useState(false);
  const placeholderColor = useMemo(() => getPlaceholderColor(manga.title), [manga.title]);

  const showCover = manga.cover_url && !imageError;

  return (
    <div
      className={cn(
        'relative w-full overflow-hidden bg-surface-alt',
        ASPECT_RATIOS[aspectRatio],
        variant === 'horizontal' && 'w-24 flex-shrink-0 aspect-[2/3]'
      )}
    >
      {/* Placeholder gradient */}
      {!showCover && (
        <div
          className={cn(
            'absolute inset-0 bg-gradient-to-br',
            placeholderColor,
            'flex items-center justify-center'
          )}
        >
          <BookOpen
            size={variant === 'compact' ? 20 : 32}
            className="text-text-dim opacity-50"
          />
        </div>
      )}

      {/* Image de couverture */}
      {showCover && (
        <Image
          src={manga.cover_url}
          alt={manga.title}
          fill
          sizes={
            variant === 'horizontal'
              ? '96px'
              : variant === 'compact'
                ? '120px'
                : '(max-width: 640px) 50vw, (max-width: 1024px) 33vw, 200px'
          }'
          }
          priority={priority}
          loading={priority ? 'eager' : 'lazy'}
          onError={() => setImageError(true)}
          className="object-cover transition-transform duration-500 group-hover:scale-105"
        />
      )}

      {/* Overlay gradient en bas pour lisibilité du texte */}
      <div
        className="absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t from-background/90 via-background/40 to-transparent pointer-events-none"
        aria-hidden="true"
      />
    </div>
  );
}

/**
 * Badges de statut (publication, langue, 18+, nouveau).
 */
function MangaCardBadges({
  manga,
  showStatus,
  showLanguage,
  variant,
}: {
  manga: Manga;
  showStatus: boolean;
  showLanguage: boolean;
  variant: MangaCardVariant;
}) {
  const isCompact = variant === 'compact';

  return (
    <div
      className={cn(
        'absolute top-2 left-2 right-2 flex items-start justify-between gap-1 z-10',
        'pointer-events-none'
      )}
    >
      {/* Badge de statut (gauche) */}
      <div className="flex flex-col gap-1">
        {showStatus && manga.status !== 'unknown' && (
          <span
            className={cn(
              'inline-flex items-center gap-1 px-1.5 py-0.5 rounded',
              'text-[9px] font-mono font-bold uppercase tracking-wider',
              'border backdrop-blur-sm',
              getMangaStatusColor(manga.status)
            )}
            style={{ textShadow: '0 0 4px currentColor' }}
            title={`Status: ${manga.status}`}
          >
            <span>{getMangaStatusIcon(manga.status)}</span>
            {!isCompact && <span>{manga.status}</span>}
          </span>
        )}
      </div>

      {/* Badges droite (langue, 18+) */}
      <div className="flex flex-col items-end gap-1">
        {showLanguage && manga.language && (
          <span
            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-background/80 backdrop-blur-sm border border-border-dim text-[9px] font-mono text-text-muted"
            title={`Language: ${manga.language}`}
          >
            <span>{getLanguageFlag(manga.language)}</span>
            {!isCompact && <span className="uppercase">{manga.language}</span>}
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * Indicateur de progression de lecture.
 */
function MangaCardProgress({
  manga,
  position,
  variant,
}: {
  manga: Manga;
  position: ProgressPosition;
  variant: MangaCardVariant;
}) {
  if (!manga.in_library || !manga.progress || position === 'none') {
    return null;
  }

  const progressPercent = calculateReadingProgress(
    manga.progress.chapters_read,
    manga.progress.total_chapters
  );

  // Anneau circulaire (overlay sur la couverture)
  if (position === 'ring') {
    return (
      <div className="absolute bottom-2 right-2 z-10">
        <ProgressRing
          value={progressPercent}
          size={variant === 'compact' ? 32 : 40}
          strokeWidth={variant === 'compact' ? 3 : 4}
          variant="default"
          showValue
        />
      </div>
    );
  }

  // Barre linéaire (sous la couverture)
  if (position === 'bar') {
    return (
      <div className="px-2 pb-2">
        <ProgressBar
          value={progressPercent}
          size="sm"
          variant="default"
          shape="pill"
          aria-label={`Reading progress: ${progressPercent}%`}
        />
        <div className="flex items-center justify-between mt-1 text-[9px] font-mono text-text-dim">
          <span>
            {manga.progress.chapters_read} / {manga.progress.total_chapters} ch.
          </span>
          <span className="text-primary">{progressPercent}%</span>
        </div>
      </div>
    );
  }

  return null;
}

/**
 * Overlay d'actions rapides au hover.
 */
function MangaCardActions({
  manga,
  onRead,
  onDownload,
  onAddToLibrary,
  onRemoveFromLibrary,
  onToggleFavorite,
  isFavorite,
  variant,
}: {
  manga: Manga;
  onRead?: (manga: Manga) => void;
  onDownload?: (manga: Manga) => void;
  onAddToLibrary?: (manga: Manga) => void;
  onRemoveFromLibrary?: (manga: Manga) => void;
  onToggleFavorite?: (manga: Manga) => void;
  isFavorite?: boolean;
  variant: MangaCardVariant;
}) {
  const handleAction = useCallback(
    (e: MouseEvent, action?: (manga: Manga) => void) => {
      e.stopPropagation();
      e.preventDefault();
      action?.(manga);
    },
    [manga]
  );

  const isCompact = variant === 'compact';

  return (
    <div
      className={cn(
        'absolute inset-0 z-20',
        'flex flex-col justify-end',
        'bg-gradient-to-t from-background/95 via-background/60 to-transparent',
        'opacity-0 group-hover:opacity-100',
        'transition-opacity duration-300',
        'p-2'
      )}
    >
      {/* Bouton favori (en haut à droite) */}
      {onToggleFavorite && (
        <button
          type="button"
          onClick={(e) => handleAction(e, onToggleFavorite)}
          aria-label={isFavorite ? 'Remove from favorites' : 'Add to favorites'}
          className={cn(
            'absolute top-2 right-2',
            'p-1.5 rounded-full',
            'bg-background/80 backdrop-blur-sm border',
            'transition-all duration-200',
            'hover:scale-110',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
            isFavorite
              ? 'border-red-500/50 text-red-400 hover:bg-red-500/20'
              : 'border-border-dim text-text-muted hover:border-secondary hover:text-secondary'
          )}
        >
          <Heart
            size={isCompact ? 12 : 14}
            className={isFavorite ? 'fill-current' : ''}
          />
        </button>
      )}

      {/* Actions principales */}
      <div className="flex items-center gap-1.5 mt-auto">
        {/* Bouton Lire */}
        {onRead && (
          <button
            type="button"
            onClick={(e) => handleAction(e, onRead)}
            aria-label="Read manga"
            className={cn(
              'flex-1 flex items-center justify-center gap-1',
              'px-2 py-1.5 rounded-md',
              'bg-primary text-background',
              'font-mono font-bold text-xs',
              'hover:bg-primary-bright hover:shadow-[0_0_10px_rgba(0,255,65,0.5)]',
              'transition-all duration-200',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary'
            )}
          >
            <Play size={isCompact ? 10 : 12} fill="currentColor" />
            {!isCompact && <span>Read</span>}
          </button>
        )}

        {/* Bouton Télécharger */}
        {onDownload && (
          <button
            type="button"
            onClick={(e) => handleAction(e, onDownload)}
            aria-label="Download manga"
            className={cn(
              'flex items-center justify-center',
              'p-1.5 rounded-md',
              'bg-surface-alt text-secondary border border-secondary/30',
              'hover:bg-secondary/20 hover:border-secondary',
              'transition-all duration-200',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-secondary'
            )}
          >
            <Download size={isCompact ? 10 : 12} />
          </button>
        )}

        {/* Bouton Bibliothèque */}
        {(onAddToLibrary || onRemoveFromLibrary) && (
          <button
            type="button"
            onClick={(e) =>
              handleAction(
                e,
                manga.in_library ? onRemoveFromLibrary : onAddToLibrary
              )
            }
            aria-label={manga.in_library ? 'Remove from library' : 'Add to library'}
            className={cn(
              'flex items-center justify-center',
              'p-1.5 rounded-md',
              'border transition-all duration-200',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-accent',
              manga.in_library
                ? 'bg-accent/20 text-accent border-accent/30 hover:bg-accent/30'
                : 'bg-surface-alt text-text-muted border-border-dim hover:border-accent hover:text-accent'
            )}
          >
            {manga.in_library ? (
              <LibraryBig size={isCompact ? 10 : 12} />
            ) : (
              <Plus size={isCompact ? 10 : 12} />
            )}
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Informations du manga (titre, auteur, année).
 */
function MangaCardInfo({
  manga,
  variant,
  showDescription,
  descriptionLength,
}: {
  manga: Manga;
  variant: MangaCardVariant;
  showDescription?: boolean;
  descriptionLength?: number;
}) {
  const isCompact = variant === 'compact';
  const isDetailed = variant === 'detailed';
  const isHorizontal = variant === 'horizontal';

  return (
    <div className={cn('flex-1 min-w-0 p-2', isHorizontal && 'p-3')}>
      {/* Titre */}
      <h3
        className={cn(
          'font-mono font-bold text-text truncate',
          isCompact ? 'text-xs' : 'text-sm',
          isDetailed && 'text-base line-clamp-2',
          isHorizontal && 'text-base'
        )}
        title={manga.title}
      >
        {manga.title}
      </h3>

      {/* Auteur et année */}
      <div
        className={cn(
          'flex items-center gap-1.5 mt-0.5',
          'text-text-muted font-mono',
          isCompact ? 'text-[10px]' : 'text-xs'
        )}
      >
        {manga.author && (
          <span className="truncate" title={manga.author}>
            {manga.author}
          </span>
        )}
        {manga.author && manga.year && (
          <span className="text-text-dim flex-shrink-0">•</span>
        )}
        {manga.year && <span className="flex-shrink-0">{manga.year}</span>}
      </div>

      {/* Description (variante detailed uniquement) */}
      {isDetailed && showDescription && manga.description && (
        <p className="mt-2 text-xs font-mono text-text-muted line-clamp-3 leading-relaxed">
          {truncateDescription(manga.description, descriptionLength || 150)}
        </p>
      )}

      {/* Tags (variante detailed uniquement) */}
      {isDetailed && manga.tags && manga.tags.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {manga.tags.slice(0, 5).map((tag) => (
            <span
              key={tag}
              className="inline-flex px-1.5 py-0.5 rounded text-[9px] font-mono bg-surface-alt text-text-muted border border-border-dim"
            >
              {tag}
            </span>
          ))}
          {manga.tags.length > 5 && (
            <span className="inline-flex px-1.5 py-0.5 rounded text-[9px] font-mono text-text-dim">
              +{manga.tags.length - 5}
            </span>
          )}
        </div>
      )}

      {/* Statut de lecture (si dans la bibliothèque) */}
      {manga.in_library && manga.reading_status && !isCompact && (
        <div className="mt-1.5 flex items-center gap-1.5">
          <span className="text-xs" aria-hidden="true">
            {getReadingStatusIcon(manga.reading_status as ReadingStatus)}
          </span>
          <span className="text-[10px] font-mono text-text-muted capitalize">
            {manga.reading_status.replace(/_/g, ' ')}
          </span>
        </div>
      )}

      {/* Nombre de chapitres */}
      {(isDetailed || isHorizontal) && manga.chapters_count > 0 && (
        <div className="mt-1.5 flex items-center gap-1 text-[10px] font-mono text-text-dim">
          <BookOpen size={10} />
          <span>{manga.chapters_count} chapters</span>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// COMPOSANT PRINCIPAL : MangaCard
// ============================================================================

/**
 * MangaCard - Carte d'affichage d'un manga avec style cyberpunk néon.
 *
 * @param props - Props du composant
 * @returns Élément JSX de la MangaCard
 *
 * @example
 * ```tsx
 * // Grille de recherche
 * <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
 *   {mangas.map((manga) => (
 *     <MangaCard
 *       key={manga.id}
 *       manga={manga}
 *       onClick={() => router.push(`/manga/${manga.id}`)}
 *       onRead={handleRead}
 *       onDownload={handleDownload}
 *     />
 *   ))}
 * </div>
 *
 * // Liste horizontale (Continue Reading)
 * <div className="space-y-2">
 *   {readingMangas.map((manga) => (
 *     <MangaCard
 *       key={manga.id}
 *       manga={manga}
 *       variant="horizontal"
 *       showProgress
 *       progressPosition="ring"
 *       onRead={handleRead}
 *     />
 *   ))}
 * </div>
 * ```
 */
export function MangaCard({
  manga,
  variant = 'default',
  aspectRatio = 'portrait',
  progressPosition = 'bar',
  showProgress = false,
  showTags = false,
  maxTags = 5,
  showActions = true,
  showStatus = true,
  showLanguage = true,
  showDescription = false,
  descriptionLength = 150,
  priority = false,
  onClick,
  onRead,
  onDownload,
  onAddToLibrary,
  onRemoveFromLibrary,
  onToggleFavorite,
  isFavorite = false,
  className,
  disabled = false,
}: MangaCardProps) {
  const isCompact = variant === 'compact';
  const isDetailed = variant === 'detailed';
  const isHorizontal = variant === 'horizontal';

  const handleClick = useCallback(() => {
    if (!disabled && onClick) {
      onClick(manga);
    }
  }, [disabled, onClick, manga]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if ((e.key === 'Enter' || e.key === ' ') && onClick && !disabled) {
        e.preventDefault();
        onClick(manga);
      }
    },
    [onClick, manga, disabled]
  );

  // Layout horizontal
  if (isHorizontal) {
    return (
      <article
        role="article"
        aria-label={`Manga: ${manga.title}`}
        onClick={handleClick}
        onKeyDown={handleKeyDown}
        tabIndex={onClick ? 0 : undefined}
        className={cn(
          'group relative flex gap-3',
          'rounded-lg border-2 border-border-dim bg-surface',
          'overflow-hidden cursor-pointer',
          'transition-all duration-300',
          'hover:border-secondary hover:shadow-[0_0_15px_rgba(0,255,255,0.2)]',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
          disabled && 'opacity-50 cursor-not-allowed',
          className
        )}
      >
        {/* Couverture */}
        <div className="relative flex-shrink-0">
          <MangaCardCover
            manga={manga}
            aspectRatio="portrait"
            priority={priority}
            variant={variant}
          />
          <MangaCardBadges
            manga={manga}
            showStatus={showStatus}
            showLanguage={showLanguage}
            variant={variant}
          />
          {showProgress && (
            <MangaCardProgress
              manga={manga}
              position="ring"
              variant={variant}
            />
          )}
        </div>

        {/* Contenu */}
        <MangaCardInfo
          manga={manga}
          variant={variant}
          showDescription={showDescription}
          descriptionLength={descriptionLength}
        />

        {/* Actions (toujours visibles en horizontal) */}
        {showActions && (onRead || onDownload || onAddToLibrary) && (
          <div className="flex flex-col items-center justify-center gap-1.5 p-2 border-l border-border-dim">
            {onRead && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onRead(manga);
                }}
                aria-label="Read"
                className={cn(
                  'p-2 rounded-md',
                  'bg-primary text-background',
                  'hover:bg-primary-bright hover:shadow-[0_0_10px_rgba(0,255,65,0.5)]',
                  'transition-all duration-200',
                  'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary'
                )}
              >
                <Play size={14} fill="currentColor" />
              </button>
            )}
            {onDownload && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onDownload(manga);
                }}
                aria-label="Download"
                className={cn(
                  'p-2 rounded-md',
                  'bg-surface-alt text-secondary border border-secondary/30',
                  'hover:bg-secondary/20 hover:border-secondary',
                  'transition-all duration-200',
                  'focus:outline-none focus-visible:ring-2 focus-visible:ring-secondary'
                )}
              >
                <Download size={14} />
              </button>
            )}
          </div>
        )}
      </article>
    );
  }

  // Layout vertical (default, compact, detailed)
  return (
    <article
      role="article"
      aria-label={`Manga: ${manga.title}`}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      tabIndex={onClick ? 0 : undefined}
      className={cn(
        'group relative flex flex-col',
        'rounded-lg border-2 border-border-dim bg-surface',
        'overflow-hidden cursor-pointer',
        'transition-all duration-300',
        'hover:border-secondary hover:shadow-[0_0_15px_rgba(0,255,255,0.2)]',
        'hover:-translate-y-0.5',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
        disabled && 'opacity-50 cursor-not-allowed',
        className
      )}
    >
      {/* Couverture avec badges et overlay */}
      <div className="relative">
        <MangaCardCover
          manga={manga}
          aspectRatio={aspectRatio}
          priority={priority}
          variant={variant}
        />

        <MangaCardBadges
          manga={manga}
          showStatus={showStatus}
          showLanguage={showLanguage}
          variant={variant}
        />

        {showProgress && progressPosition === 'ring' && (
          <MangaCardProgress
            manga={manga}
            position="ring"
            variant={variant}
          />
        )}

        {showActions && (
          <MangaCardActions
            manga={manga}
            onRead={onRead}
            onDownload={onDownload}
            onAddToLibrary={onAddToLibrary}
            onRemoveFromLibrary={onRemoveFromLibrary}
            onToggleFavorite={onToggleFavorite}
            isFavorite={isFavorite}
            variant={variant}
          />
        )}
      </div>

      {/* Progression (barre sous la couverture) */}
      {showProgress && progressPosition === 'bar' && (
        <MangaCardProgress
          manga={manga}
          position="bar"
          variant={variant}
        />
      )}

      {/* Informations */}
      <MangaCardInfo
        manga={manga}
        variant={variant}
        showDescription={showDescription}
        descriptionLength={descriptionLength}
      />

      {/* Indicateur "in library" */}
      {manga.in_library && !isCompact && (
        <div className="absolute top-2 right-2 z-10">
          <div
            className="p-1 rounded-full bg-accent/20 border border-accent/40 backdrop-blur-sm"
            title="In your library"
          >
            <Library size={10} className="text-accent" />
          </div>
        </div>
      )}
    </article>
  );
}

// ============================================================================
// VARIANTE COMPACTE
// ============================================================================

/**
 * MangaCardCompact - Version compacte de la carte manga.
 *
 * Optimisée pour les grilles denses (ex: résultats de recherche).
 *
 * @param props - Props du composant
 */
export function MangaCardCompact(props: Omit<MangaCardProps, 'variant'>) {
  return <MangaCard {...props} variant="compact" aspectRatio="portrait" />;
}

// ============================================================================
// VARIANTE DÉTAILLÉE
// ============================================================================

/**
 * MangaCardDetailed - Version détaillée de la carte manga.
 *
 * Affiche plus d'informations (description, tags, chapitres).
 *
 * @param props - Props du composant
 */
export function MangaCardDetailed(props: Omit<MangaCardProps, 'variant'>) {
  return (
    <MangaCard
      {...props}
      variant="detailed"
      aspectRatio="portrait"
      showDescription
    />
  );
}

// ============================================================================
// VARIANTE HORIZONTALE
// ============================================================================

/**
 * MangaCardHorizontal - Version horizontale de la carte manga.
 *
 * Idéale pour les listes "Continue Reading" ou "Recently Added".
 *
 * @param props - Props du composant
 */
export function MangaCardHorizontal(props: Omit<MangaCardProps, 'variant'>) {
  return <MangaCard {...props} variant="horizontal" />;
}

// ============================================================================
// SKELETON LOADER
// ============================================================================

/**
 * MangaCardSkeleton - Skeleton loader pour l'état de chargement.
 *
 * @param props - Props du composant
 *
 * @example
 * ```tsx
 * // Grille de chargement
 * <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
 *   {Array.from({ length: 12 }).map((_, i) => (
 *     <MangaCardSkeleton key={i} />
 *   ))}
 * </div>
 * ```
 */
export function MangaCardSkeleton({
  variant = 'default',
  className,
}: Omit<MangaCardSkeletonProps, 'count'>) {
  const isHorizontal = variant === 'horizontal';
  const isCompact = variant === 'compact';

  return (
    <div
      className={cn(
        'relative overflow-hidden rounded-lg border-2 border-border-dim bg-surface',
        isHorizontal ? 'flex gap-3' : 'flex flex-col',
        className
      )}
      aria-hidden="true"
    >
      {/* Skeleton couverture */}
      <div
        className={cn(
          'relative bg-surface-alt',
          isHorizontal ? 'w-24 flex-shrink-0 aspect-[2/3]' : 'w-full aspect-[2/3]',
          'animate-pulse'
        )}
      >
        <div className="absolute inset-0 bg-gradient-to-r from-transparent via-background/20 to-transparent animate-shimmer" />
      </div>

      {/* Skeleton contenu */}
      <div className={cn('flex-1 p-2 space-y-2', isHorizontal && 'p-3')}>
        {/* Titre */}
        <div
          className={cn(
            'h-4 bg-surface-alt rounded animate-pulse',
            isCompact ? 'w-3/4' : 'w-full'
          )}
        />

        {/* Auteur */}
        <div className="h-3 bg-surface-alt rounded animate-pulse w-1/2" />

        {/* Lignes supplémentaires pour detailed */}
        {variant === 'detailed' && (
          <>
            <div className="h-3 bg-surface-alt rounded animate-pulse w-full" />
            <div className="h-3 bg-surface-alt rounded animate-pulse w-4/5" />
            <div className="flex gap-1 mt-2">
              <div className="h-4 w-12 bg-surface-alt rounded animate-pulse" />
              <div className="h-4 w-16 bg-surface-alt rounded animate-pulse" />
              <div className="h-4 w-10 bg-surface-alt rounded animate-pulse" />
            </div>
          </>
        )}
      </div>

      {/* Style pour l'animation shimmer */}
      <style jsx>{`
        @keyframes shimmer {
          0% {
            transform: translateX(-100%);
          }
          100% {
            transform: translateX(100%);
          }
        }
        .animate-shimmer {
          animation: shimmer 1.5s infinite;
        }
      `}</style>
    </div>
  );
}

/**
 * MangaCardSkeletonGrid - Grille de skeletons pour le chargement.
 *
 * @param props - Props du composant
 */
export function MangaCardSkeletonGrid({
  variant = 'default',
  count = 12,
  className,
}: MangaCardSkeletonProps) {
  return (
    <div
      className={cn(
        'grid gap-4',
        variant === 'horizontal'
          ? 'grid-cols-1'
          : 'grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6',
        className
      )}
    >
      {Array.from({ length: count }).map((_, i) => (
        <MangaCardSkeleton key={i} variant={variant} />
      ))}
    </div>
  );
}

// ============================================================================
// COMPOSANT UTILITAIRE : EmptyState
// ============================================================================

/**
 * MangaCardEmpty - État vide pour une liste de mangas.
 *
 * @param props - Props du composant
 */
export function MangaCardEmpty({
  title = 'No manga found',
  description = 'Try adjusting your search or filters',
  icon: Icon = BookOpen,
  action,
  className,
}: {
  title?: string;
  description?: string;
  icon?: React.ComponentType<{ size?: number; className?: string }>;
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
        <Icon size={28} className="text-text-dim" />
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

export default MangaCard;
export {
  MangaCardCompact,
  MangaCardDetailed,
  MangaCardHorizontal,
  MangaCardSkeleton,
  MangaCardSkeletonGrid,
  MangaCardEmpty,
  MangaCardCover,
  MangaCardBadges,
  MangaCardProgress,
  MangaCardActions,
  MangaCardInfo,
  getPlaceholderColor,
  getLanguageFlag,
};
export type {
  MangaCardVariant,
  CoverAspectRatio,
  ProgressPosition,
};
