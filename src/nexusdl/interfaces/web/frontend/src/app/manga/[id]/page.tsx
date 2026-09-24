/**
 * Page de détail d'un manga pour NexusDL.
 *
 * Interface complète pour afficher les informations d'un manga avec :
 *   - Header avec couverture, titre, auteur, statut
 *   - Actions : lire, télécharger, ajouter à la bibliothèque
 *   - Progression de lecture avec statistiques
 *   - Liste des chapitres avec filtrage et tri
 *   - Métadonnées (tags, description, statistiques)
 *   - Skeleton loader pendant le chargement
 *   - Gestion des erreurs
 *   - Événements WebSocket temps réel
 *   - Style cyberpunk néon cohérent
 *   - Accessibilité complète
 *   - Responsive
 *
 * URL : /manga/[id]?site={site_id}
 *   - id : ID du manga
 *   - site : ID du site source (query param)
 *
 * @module app/manga/[id]/page
 */

'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import Image from 'next/image';
import {
  AlertCircle,
  ArrowLeft,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Download,
  Eye,
  EyeOff,
  Globe,
  Heart,
  Info,
  Library,
  LibraryBig,
  Loader2,
  Play,
  Plus,
  RefreshCw,
  Share2,
  Star,
  Trash2,
  X,
  Zap,
} from 'lucide-react';
import { toast } from 'sonner';

import { cn } from '@/lib/utils';
import { api } from '@/lib/api';
import {
  useDownloadActions,
  useLibraryWebSocket,
} from '@/store';

import { ChapterList, ChapterListSkeleton } from '@/components/ChapterList';
import { ProgressBar, ProgressRing } from '@/components/ProgressBar';

import type { Manga, Chapter, ReadingStatus } from '@/types/manga';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Données complètes d'un manga avec ses chapitres.
 */
interface MangaDetail {
  manga: Manga;
  chapters: Chapter[];
  isInLibrary: boolean;
  isFavorite: boolean;
}

/**
 * État de la page.
 */
interface PageState {
  data: MangaDetail | null;
  isLoading: boolean;
  error: string | null;
}

// ============================================================================
// HOOK PERSONNALISÉ - useMangaDetail
// ============================================================================

/**
 * Hook pour charger et gérer les détails d'un manga.
 */
function useMangaDetail(siteId: string, mangaId: string) {
  const [state, setState] = useState<PageState>({
    data: null,
    isLoading: true,
    error: null,
  });

  const fetchData = useCallback(async () => {
    setState((prev) => ({ ...prev, isLoading: true, error: null }));

    try {
      // Charger les détails du manga et les chapitres en parallèle
      const [manga, chapters] = await Promise.all([
        api.get<Manga>(`/manga/${siteId}/${mangaId}`),
        api.get<{ chapters: Chapter[] }>(`/manga/${siteId}/${mangaId}/chapters`),
      ]);

      setState({
        data: {
          manga,
          chapters: chapters.chapters,
          isInLibrary: manga.in_library,
          isFavorite: false, // TODO: Récupérer depuis le backend
        },
        isLoading: false,
        error: null,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to load manga';
      setState({
        data: null,
        isLoading: false,
        error: message,
      });
    }
  }, [siteId, mangaId]);

  const addToLibrary = useCallback(
    async (status: ReadingStatus = 'plan_to_read') => {
      try {
        await api.post(`/manga/${siteId}/${mangaId}/library`, {
          reading_status: status,
        });

        setState((prev) => {
          if (!prev.data) return prev;
          return {
            ...prev,
            data: {
              ...prev.data,
              isInLibrary: true,
              manga: {
                ...prev.data.manga,
                in_library: true,
                reading_status: status,
              },
            },
          };
        });

        toast.success('Added to library', {
          description: state.data?.manga.title,
        });
      } catch (error) {
        toast.error('Failed to add to library');
      }
    },
    [siteId, mangaId, state.data?.manga.title]
  );

  const removeFromLibrary = useCallback(async () => {
    try {
      await api.delete(`/manga/${siteId}/${mangaId}/library`);

      setState((prev) => {
        if (!prev.data) return prev;
        return {
          ...prev,
          data: {
            ...prev.data,
            isInLibrary: false,
            manga: {
              ...prev.data.manga,
              in_library: false,
              reading_status: null,
            },
          },
        };
      });

      toast.success('Removed from library');
    } catch (error) {
      toast.error('Failed to remove from library');
    }
  }, [siteId, mangaId]);

  const toggleFavorite = useCallback(() => {
    setState((prev) => {
      if (!prev.data) return prev;
      return {
        ...prev,
        data: {
          ...prev.data,
          isFavorite: !prev.data.isFavorite,
        },
      };
    });
  }, []);

  const markChapterRead = useCallback(
    async (chapterId: string, isRead: boolean) => {
      try {
        await api.patch(`/manga/${siteId}/${mangaId}/chapters/${chapterId}/read`, {
          is_read: isRead,
        });

        setState((prev) => {
          if (!prev.data) return prev;
          return {
            ...prev,
            data: {
              ...prev.data,
              chapters: prev.data.chapters.map((ch) =>
                ch.id === chapterId ? { ...ch, is_read: isRead } : ch
              ),
            },
          };
        });
      } catch (error) {
        toast.error('Failed to update chapter status');
      }
    },
    [siteId, mangaId]
  );

  return {
    ...state,
    fetchData,
    addToLibrary,
    removeFromLibrary,
    toggleFavorite,
    markChapterRead,
  };
}

// ============================================================================
// SOUS-COMPOSANTS
// ============================================================================

/**
 * Header du manga avec couverture et informations principales.
 */
function MangaDetailHeader({
  manga,
  isInLibrary,
  isFavorite,
  onToggleFavorite,
}: {
  manga: Manga;
  isInLibrary: boolean;
  isFavorite: boolean;
  onToggleFavorite: () => void;
}) {
  const [imageError, setImageError] = useState(false);
  const showCover = manga.cover_url && !imageError;

  return (
    <div className="relative flex flex-col md:flex-row gap-6 p-6 rounded-xl border-2 border-border-dim bg-surface">
      {/* Couverture */}
      <div className="relative flex-shrink-0 w-full md:w-64 aspect-[2/3] rounded-lg overflow-hidden bg-surface-alt">
        {showCover ? (
          <Image
            src={manga.cover_url}
            alt={manga.title}
            fill
            sizes="(max-width: 768px) 100vw, 256px"
            priority
            onError={() => setImageError(true)}
            className="object-cover"
          />
        ) : (
          <div className="absolute inset-0 bg-gradient-to-br from-primary/20 to-secondary/20 flex items-center justify-center">
            <BookOpen size={64} className="text-text-dim" />
          </div>
        )}

        {/* Overlay gradient */}
        <div className="absolute inset-0 bg-gradient-to-t from-background/80 via-transparent to-transparent pointer-events-none" />

        {/* Badge de statut */}
        {manga.status !== 'unknown' && (
          <div className="absolute top-3 left-3">
            <span
              className={cn(
                'inline-flex items-center gap-1 px-2 py-1 rounded-md',
                'text-xs font-mono font-bold uppercase tracking-wider',
                'border backdrop-blur-sm',
                manga.status === 'ongoing'
                  ? 'bg-blue-500/20 text-blue-400 border-blue-500/30'
                  : manga.status === 'completed'
                    ? 'bg-green-500/20 text-green-400 border-green-500/30'
                    : 'bg-gray-500/20 text-gray-400 border-gray-500/30'
              )}
            >
              {manga.status}
            </span>
          </div>
        )}

        {/* Badge de langue */}
        <div className="absolute top-3 right-3">
          <span className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-background/80 backdrop-blur-sm border border-border-dim text-xs font-mono text-text-muted">
            <Globe size={12} />
            {manga.language.toUpperCase()}
          </span>
        </div>
      </div>

      {/* Informations */}
      <div className="flex-1 min-w-0">
        {/* Titre */}
        <h1 className="text-3xl font-mono font-bold text-text mb-2 break-words">
          {manga.title}
        </h1>

        {/* Auteur et année */}
        <div className="flex items-center gap-3 text-sm font-mono text-text-muted mb-4">
          {manga.author && (
            <span className="flex items-center gap-1">
              <Star size={14} className="text-primary" />
              {manga.author}
            </span>
          )}
          {manga.year && (
            <>
              <span className="text-text-dim">•</span>
              <span>{manga.year}</span>
            </>
          )}
          <span className="text-text-dim">•</span>
          <span className="flex items-center gap-1">
            <BookOpen size={14} />
            {manga.chapters_count} chapters
          </span>
        </div>

        {/* Description */}
        {manga.description && (
          <div className="mb-4">
            <p className="text-sm font-mono text-text-muted leading-relaxed line-clamp-4">
              {manga.description}
            </p>
            {manga.description.length > 200 && (
              <button className="mt-2 text-xs font-mono text-secondary hover:text-primary transition-colors">
                Read more
              </button>
            )}
          </div>
        )}

        {/* Tags */}
        {manga.tags && manga.tags.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-4">
            {manga.tags.slice(0, 10).map((tag) => (
              <span
                key={tag}
                className="inline-flex px-2 py-1 rounded-md text-xs font-mono bg-surface-alt text-text-muted border border-border-dim hover:border-secondary hover:text-secondary transition-colors cursor-pointer"
              >
                {tag}
              </span>
            ))}
            {manga.tags.length > 10 && (
              <span className="inline-flex px-2 py-1 rounded-md text-xs font-mono text-text-dim">
                +{manga.tags.length - 10} more
              </span>
            )}
          </div>
        )}

        {/* Actions rapides */}
        <div className="flex items-center gap-2">
          {/* Bouton favori */}
          <button
            type="button"
            onClick={onToggleFavorite}
            aria-label={isFavorite ? 'Remove from favorites' : 'Add to favorites'}
            className={cn(
              'p-2 rounded-lg border-2 transition-all duration-200',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
              isFavorite
                ? 'bg-red-500/20 text-red-400 border-red-500/30 hover:bg-red-500/30'
                : 'bg-surface-alt text-text-muted border-border-dim hover:border-red-500 hover:text-red-400'
            )}
          >
            <Heart size={18} className={isFavorite ? 'fill-current' : ''} />
          </button>

          {/* Bouton partager */}
          <button
            type="button"
            onClick={() => {
              navigator.clipboard.writeText(window.location.href);
              toast.success('Link copied to clipboard');
            }}
            aria-label="Share manga"
            className={cn(
              'p-2 rounded-lg border-2',
              'bg-surface-alt text-text-muted border-border-dim',
              'hover:border-secondary hover:text-secondary',
              'transition-all duration-200',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary'
            )}
          >
            <Share2 size={18} />
          </button>

          {/* Bouton site source */}
          {manga.url && (
            <a
              href={manga.url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="View on source site"
              className={cn(
                'p-2 rounded-lg border-2',
                'bg-surface-alt text-text-muted border-border-dim',
                'hover:border-secondary hover:text-secondary',
                'transition-all duration-200',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary'
              )}
            >
              <Globe size={18} />
            </a>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Panneau d'actions principales (lire, télécharger, bibliothèque).
 */
function MangaDetailActions({
  manga,
  isInLibrary,
  onRead,
  onDownload,
  onAddToLibrary,
  onRemoveFromLibrary,
}: {
  manga: Manga;
  isInLibrary: boolean;
  onRead: () => void;
  onDownload: () => void;
  onAddToLibrary: () => void;
  onRemoveFromLibrary: () => void;
}) {
  return (
    <div className="flex flex-col sm:flex-row gap-3">
      {/* Bouton Lire */}
      <button
        type="button"
        onClick={onRead}
        className={cn(
          'flex-1 flex items-center justify-center gap-2',
          'px-6 py-3 rounded-lg',
          'bg-primary text-background',
          'font-mono font-bold text-sm uppercase tracking-wider',
          'hover:bg-primary-bright hover:shadow-[0_0_20px_rgba(0,255,65,0.5)]',
          'transition-all duration-200',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2',
          'active:scale-[0.98]'
        )}
      >
        <Play size={18} fill="currentColor" />
        <span>Start Reading</span>
      </button>

      {/* Bouton Télécharger */}
      <button
        type="button"
        onClick={onDownload}
        className={cn(
          'flex-1 flex items-center justify-center gap-2',
          'px-6 py-3 rounded-lg',
          'bg-surface-alt text-secondary border-2 border-secondary/30',
          'font-mono font-bold text-sm uppercase tracking-wider',
          'hover:bg-secondary/20 hover:border-secondary hover:shadow-[0_0_15px_rgba(0,255,255,0.3)]',
          'transition-all duration-200',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-secondary focus-visible:ring-offset-2',
          'active:scale-[0.98]'
        )}
      >
        <Download size={18} />
        <span>Download</span>
      </button>

      {/* Bouton Bibliothèque */}
      {isInLibrary ? (
        <button
          type="button"
          onClick={onRemoveFromLibrary}
          className={cn(
            'flex items-center justify-center gap-2',
            'px-6 py-3 rounded-lg',
            'bg-accent/20 text-accent border-2 border-accent/30',
            'font-mono font-bold text-sm uppercase tracking-wider',
            'hover:bg-accent/30 hover:border-accent',
            'transition-all duration-200',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2',
            'active:scale-[0.98]'
          )}
        >
          <LibraryBig size={18} />
          <span>In Library</span>
        </button>
      ) : (
        <button
          type="button"
          onClick={onAddToLibrary}
          className={cn(
            'flex items-center justify-center gap-2',
            'px-6 py-3 rounded-lg',
            'bg-surface-alt text-text-muted border-2 border-border-dim',
            'font-mono font-bold text-sm uppercase tracking-wider',
            'hover:border-primary hover:text-primary hover:bg-primary-bg',
            'transition-all duration-200',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2',
            'active:scale-[0.98]'
          )}
        >
          <Plus size={18} />
          <span>Add to Library</span>
        </button>
      )}
    </div>
  );
}

/**
 * Panneau de progression de lecture.
 */
function MangaDetailProgress({ manga }: { manga: Manga }) {
  if (!manga.in_library || !manga.progress) {
    return null;
  }

  const progress = manga.progress;
  const progressPercent = Math.round(
    (progress.chapters_read / progress.total_chapters) * 100
  );

  return (
    <div className="p-4 rounded-xl border-2 border-border-dim bg-surface">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-mono font-bold text-text-dim uppercase tracking-wider">
          Reading Progress
        </h3>
        <span className="text-2xl font-mono font-bold text-primary">
          {progressPercent}%
        </span>
      </div>

      {/* Barre de progression */}
      <ProgressBar
        value={progressPercent}
        variant="default"
        size="md"
        shape="pill"
        className="mb-3"
      />

      {/* Statistiques */}
      <div className="grid grid-cols-3 gap-3">
        <div className="flex flex-col items-center p-2 rounded-lg bg-surface-alt/50 border border-border-dim">
          <span className="text-xs font-mono font-bold text-primary">
            {progress.chapters_read}
          </span>
          <span className="text-[10px] font-mono text-text-dim uppercase">
            Read
          </span>
        </div>
        <div className="flex flex-col items-center p-2 rounded-lg bg-surface-alt/50 border border-border-dim">
          <span className="text-xs font-mono font-bold text-text-muted">
            {progress.total_chapters - progress.chapters_read}
          </span>
          <span className="text-[10px] font-mono text-text-dim uppercase">
            Remaining
          </span>
        </div>
        <div className="flex flex-col items-center p-2 rounded-lg bg-surface-alt/50 border border-border-dim">
          <span className="text-xs font-mono font-bold text-secondary">
            {progress.total_chapters}
          </span>
          <span className="text-[10px] font-mono text-text-dim uppercase">
            Total
          </span>
        </div>
      </div>

      {/* Dernière lecture */}
      {progress.last_read_at && (
        <div className="mt-3 flex items-center gap-2 text-xs font-mono text-text-dim">
          <Clock size={12} />
          <span>
            Last read:{' '}
            {new Date(progress.last_read_at).toLocaleDateString(undefined, {
              month: 'short',
              day: 'numeric',
              year: 'numeric',
            })}
          </span>
        </div>
      )}
    </div>
  );
}

/**
 * Panneau de métadonnées (statistiques, dates, etc.).
 */
function MangaDetailMetadata({ manga }: { manga: Manga }) {
  return (
    <div className="p-4 rounded-xl border-2 border-border-dim bg-surface">
      <h3 className="text-sm font-mono font-bold text-text-dim uppercase tracking-wider mb-3">
        Information
      </h3>

      <div className="space-y-2">
        {/* Site source */}
        <div className="flex items-center justify-between text-xs font-mono">
          <span className="text-text-dim">Source</span>
          <span className="text-text flex items-center gap-1">
            <Globe size={12} className="text-secondary" />
            {manga.site_id}
          </span>
        </div>

        {/* Statut de lecture */}
        {manga.in_library && manga.reading_status && (
          <div className="flex items-center justify-between text-xs font-mono">
            <span className="text-text-dim">Status</span>
            <span className="text-primary capitalize flex items-center gap-1">
              <BookOpen size={12} />
              {manga.reading_status.replace(/_/g, ' ')}
            </span>
          </div>
        )}

        {/* Date d'ajout */}
        {manga.in_library && (
          <div className="flex items-center justify-between text-xs font-mono">
            <span className="text-text-dim">Added</span>
            <span className="text-text">
              {new Date().toLocaleDateString(undefined, {
                month: 'short',
                day: 'numeric',
                year: 'numeric',
              })}
            </span>
          </div>
        )}

        {/* Dernière mise à jour */}
        {manga.last_updated_at && (
          <div className="flex items-center justify-between text-xs font-mono">
            <span className="text-text-dim">Updated</span>
            <span className="text-text">
              {new Date(manga.last_updated_at).toLocaleDateString(undefined, {
                month: 'short',
                day: 'numeric',
                year: 'numeric',
              })}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Skeleton loader pour la page de détail.
 */
function MangaDetailSkeleton() {
  return (
    <div className="space-y-6 animate-pulse">
      {/* Header skeleton */}
      <div className="flex flex-col md:flex-row gap-6 p-6 rounded-xl border-2 border-border-dim bg-surface">
        <div className="w-full md:w-64 aspect-[2/3] rounded-lg bg-surface-alt" />
        <div className="flex-1 space-y-4">
          <div className="h-8 bg-surface-alt rounded w-3/4" />
          <div className="h-4 bg-surface-alt rounded w-1/2" />
          <div className="space-y-2">
            <div className="h-3 bg-surface-alt rounded w-full" />
            <div className="h-3 bg-surface-alt rounded w-full" />
            <div className="h-3 bg-surface-alt rounded w-2/3" />
          </div>
          <div className="flex gap-2">
            <div className="h-8 w-20 bg-surface-alt rounded" />
            <div className="h-8 w-20 bg-surface-alt rounded" />
            <div className="h-8 w-20 bg-surface-alt rounded" />
          </div>
        </div>
      </div>

      {/* Actions skeleton */}
      <div className="flex gap-3">
        <div className="flex-1 h-12 bg-surface-alt rounded-lg" />
        <div className="flex-1 h-12 bg-surface-alt rounded-lg" />
        <div className="flex-1 h-12 bg-surface-alt rounded-lg" />
      </div>

      {/* Progress skeleton */}
      <div className="h-40 bg-surface-alt rounded-xl border-2 border-border-dim" />

      {/* Chapters skeleton */}
      <ChapterListSkeleton count={10} />
    </div>
  );
}

// ============================================================================
// PAGE PRINCIPALE
// ============================================================================

/**
 * MangaDetailPage - Page de détail d'un manga.
 *
 * @returns Élément JSX de la page
 */
export default function MangaDetailPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();

  const mangaId = params.id as string;
  const siteId = searchParams.get('site') || 'mangadex';

  // Hooks
  const {
    data,
    isLoading,
    error,
    fetchData,
    addToLibrary,
    removeFromLibrary,
    toggleFavorite,
    markChapterRead,
  } = useMangaDetail(siteId, mangaId);

  const downloadActions = useDownloadActions();

  // Écouter les événements WebSocket
  useLibraryWebSocket({
    onUpdated: (update) => {
      if (update.manga_id === mangaId) {
        fetchData();
      }
    },
  });

  // Charger les données au montage
  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // ==========================================================================
  // HANDLERS
  // ==========================================================================

  const handleRead = useCallback(() => {
    if (!data) return;

    // Trouver le premier chapitre non lu
    const firstUnread = data.chapters.find((ch) => !ch.is_read);
    const chapterToRead = firstUnread || data.chapters[0];

    if (chapterToRead) {
      router.push(`/read/${chapterToRead.id}`);
    }
  }, [data, router]);

  const handleDownload = useCallback(() => {
    if (!data) return;

    downloadActions.createTask({
      site_id: siteId,
      manga_id: mangaId,
      format: 'cbz',
      quality: 'original',
      priority: 'normal',
    });

    toast.success('Download started', {
      description: data.manga.title,
    });
  }, [data, siteId, mangaId, downloadActions]);

  const handleAddToLibrary = useCallback(() => {
    addToLibrary('plan_to_read');
  }, [addToLibrary]);

  const handleRemoveFromLibrary = useCallback(() => {
    const confirmed = window.confirm(
      `Remove "${data?.manga.title}" from your library?`
    );
    if (confirmed) {
      removeFromLibrary();
    }
  }, [data?.manga.title, removeFromLibrary]);

  const handleReadChapter = useCallback(
    (chapter: Chapter) => {
      router.push(`/read/${chapter.id}`);
    },
    [router]
  );

  const handleDownloadChapter = useCallback(
    (chapter: Chapter) => {
      downloadActions.createTask({
        site_id: siteId,
        manga_id: mangaId,
        chapter_ids: [chapter.id],
        format: 'cbz',
        quality: 'original',
        priority: 'normal',
      });

      toast.success('Chapter download started', {
        description: `Chapter ${chapter.number}`,
      });
    },
    [siteId, mangaId, downloadActions]
  );

  const handleMarkChapterRead = useCallback(
    (chapterId: string, isRead: boolean) => {
      markChapterRead(chapterId, isRead);
    },
    [markChapterRead]
  );

  // ==========================================================================
  // RENDU
  // ==========================================================================

  // État de chargement
  if (isLoading && !data) {
    return (
      <div className="container mx-auto px-4 py-6 max-w-7xl">
        <MangaDetailSkeleton />
      </div>
    );
  }

  // État d'erreur
  if (error) {
    return (
      <div className="container mx-auto px-4 py-6 max-w-7xl">
        <div className="flex flex-col items-center justify-center py-16">
          <div className="flex items-center justify-center h-16 w-16 rounded-full bg-red-500/10 border-2 border-red-500/30 mb-4">
            <AlertCircle size={32} className="text-red-400" />
          </div>
          <h2 className="text-xl font-mono font-bold text-text mb-2">
            Failed to load manga
          </h2>
          <p className="text-sm font-mono text-text-muted mb-4">{error}</p>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => router.back()}
              className={cn(
                'flex items-center gap-2 px-4 py-2 rounded-lg',
                'bg-surface-alt text-text-muted border border-border-dim',
                'font-mono text-sm',
                'hover:bg-surface-hover hover:border-secondary hover:text-secondary',
                'transition-all duration-200'
              )}
            >
              <ArrowLeft size={16} />
              <span>Go Back</span>
            </button>
            <button
              type="button"
              onClick={fetchData}
              className={cn(
                'flex items-center gap-2 px-4 py-2 rounded-lg',
                'bg-primary text-background',
                'font-mono font-bold text-sm',
                'hover:bg-primary-bright hover:shadow-[0_0_15px_rgba(0,255,65,0.5)]',
                'transition-all duration-200'
              )}
            >
              <RefreshCw size={16} />
              <span>Retry</span>
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Pas de données
  if (!data) {
    return null;
  }

  const { manga, chapters, isInLibrary, isFavorite } = data;

  return (
    <div className="container mx-auto px-4 py-6 max-w-7xl">
      {/* Bouton retour */}
      <div className="mb-4">
        <button
          type="button"
          onClick={() => router.back()}
          className={cn(
            'flex items-center gap-2 px-3 py-2 rounded-lg',
            'text-text-muted hover:text-secondary',
            'font-mono text-sm',
            'transition-colors duration-200',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary'
          )}
        >
          <ArrowLeft size={16} />
          <span>Back</span>
        </button>
      </div>

      {/* Header */}
      <div className="mb-6">
        <MangaDetailHeader
          manga={manga}
          isInLibrary={isInLibrary}
          isFavorite={isFavorite}
          onToggleFavorite={toggleFavorite}
        />
      </div>

      {/* Actions principales */}
      <div className="mb-6">
        <MangaDetailActions
          manga={manga}
          isInLibrary={isInLibrary}
          onRead={handleRead}
          onDownload={handleDownload}
          onAddToLibrary={handleAddToLibrary}
          onRemoveFromLibrary={handleRemoveFromLibrary}
        />
      </div>

      {/* Grille principale */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Colonne gauche : Progression + Métadonnées */}
        <div className="lg:col-span-1 space-y-6">
          {/* Progression */}
          <MangaDetailProgress manga={manga} />

          {/* Métadonnées */}
          <MangaDetailMetadata manga={manga} />
        </div>

        {/* Colonne droite : Liste des chapitres */}
        <div className="lg:col-span-2">
          <div className="rounded-xl border-2 border-border-dim bg-surface p-4">
            <h2 className="text-lg font-mono font-bold text-text mb-4 flex items-center gap-2">
              <BookOpen size={20} className="text-primary" />
              <span>Chapters</span>
              <span className="text-sm font-mono text-text-muted ml-auto">
                {chapters.length} total
              </span>
            </h2>

            <ChapterList
              chapters={chapters}
              mangaId={mangaId}
              onRead={handleReadChapter}
              onDownload={handleDownloadChapter}
              onMarkRead={handleMarkChapterRead}
              showToolbar
              showStats
              selectable
              defaultSortBy="number"
              defaultSortOrder="asc"
            />
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="mt-8 pt-6 border-t border-border-dim">
        <div className="flex items-center justify-between text-xs font-mono text-text-dim">
          <div className="flex items-center gap-4">
            <span>
              <span className="text-text font-semibold">{chapters.length}</span> chapters
            </span>
            {isInLibrary && (
              <span className="flex items-center gap-1">
                <Library size={12} className="text-primary" />
                <span>In Library</span>
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            <Globe size={12} />
            <span>{manga.site_id}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
