/**
 * Page d'accueil (Dashboard) pour NexusDL.
 *
 * Landing page principale de l'application avec :
 *   - Hero section avec barre de recherche
 *   - Statistiques globales (mangas, téléchargements, sites)
 *   - Section "Continue Reading" (mangas en cours de lecture)
 *   - Section "Recently Added" (derniers mangas ajoutés)
 *   - Section "Features" (fonctionnalités clés)
 *   - Section "Quick Actions" (actions rapides)
 *   - Style cyberpunk néon cohérent
 *   - Accessibilité complète
 *   - Responsive
 *
 * @module app/page
 */

'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import {
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Clock,
  Download,
  Folder,
  Globe,
  Heart,
  Library,
  Play,
  Search,
  Settings,
  Sparkles,
  Star,
  TrendingUp,
  Zap,
} from 'lucide-react';
import { toast } from 'sonner';

import { cn } from '@/lib/utils';
import {
  useDownloadCounts,
  useDownloadActions,
  useLibrary,
  useSearch,
} from '@/store';

import { SearchBarHero } from '@/components/SearchBar';
import { MangaCard, MangaCardHorizontal, MangaCardSkeletonGrid } from '@/components/MangaCard';
import { ProgressBar } from '@/components/ProgressBar';

import type { Manga } from '@/types/manga';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Statistiques de l'application.
 */
interface AppStats {
  totalMangas: number;
  totalChapters: number;
  totalDownloads: number;
  totalSites: number;
  totalSizeBytes: number;
  readingTimeHours: number;
}

// ============================================================================
// COMPOSANTS AUXILIAIRES
// ============================================================================

/**
 * Carte de statistique.
 */
function StatCard({
  icon: Icon,
  label,
  value,
  color,
  trend,
}: {
  icon: React.ComponentType<{ size?: number; className?: string }>;
  label: string;
  value: string | number;
  color: string;
  trend?: string;
}) {
  return (
    <div className="group relative p-4 rounded-xl border-2 border-border-dim bg-surface hover:border-secondary/50 transition-all duration-300 hover:shadow-[0_0_15px_rgba(0,255,255,0.1)]">
      <div className="flex items-start justify-between mb-2">
        <div
          className={cn(
            'flex items-center justify-center h-10 w-10 rounded-lg',
            'bg-surface-alt border border-border-dim',
            'transition-all duration-300',
            'group-hover:border-secondary group-hover:shadow-[0_0_10px_rgba(0,255,255,0.2)]'
          )}
        >
          <Icon size={20} className={color} />
        </div>
        {trend && (
          <span className="text-[10px] font-mono text-green-400 flex items-center gap-0.5">
            <TrendingUp size={10} />
            {trend}
          </span>
        )}
      </div>
      <div className="text-2xl font-mono font-bold text-text mb-0.5">
        {value}
      </div>
      <div className="text-xs font-mono text-text-dim uppercase tracking-wider">
        {label}
      </div>
    </div>
  );
}

/**
 * Section "Continue Reading".
 */
function ContinueReadingSection({
  mangas,
  isLoading,
  onRead,
}: {
  mangas: Manga[];
  isLoading: boolean;
  onRead: (manga: Manga) => void;
}) {
  const router = useRouter();

  if (!isLoading && mangas.length === 0) {
    return null;
  }

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-mono font-bold text-text flex items-center gap-2">
          <Play size={20} className="text-primary" />
          <span>Continue Reading</span>
        </h2>
        <Link
          href="/library?status=reading"
          className="text-xs font-mono text-secondary hover:text-primary transition-colors flex items-center gap-1"
        >
          <span>View all</span>
          <ArrowRight size={12} />
        </Link>
      </div>

      {isLoading ? (
        <MangaCardSkeletonGrid variant="horizontal" count={3} />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {mangas.slice(0, 6).map((manga) => (
            <MangaCardHorizontal
              key={manga.id}
              manga={manga}
              showProgress
              progressPosition="ring"
              onRead={() => onRead(manga)}
              onClick={() => router.push(`/manga/${manga.site_id}/${manga.id}`)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

/**
 * Section "Recently Added".
 */
function RecentlyAddedSection({
  mangas,
  isLoading,
  onClick,
}: {
  mangas: Manga[];
  isLoading: boolean;
  onClick: (manga: Manga) => void;
}) {
  const router = useRouter();

  if (!isLoading && mangas.length === 0) {
    return null;
  }

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-mono font-bold text-text flex items-center gap-2">
          <Star size={20} className="text-accent" />
          <span>Recently Added</span>
        </h2>
        <Link
          href="/library?sort=date_added"
          className="text-xs font-mono text-secondary hover:text-primary transition-colors flex items-center gap-1"
        >
          <span>View all</span>
          <ArrowRight size={12} />
        </Link>
      </div>

      {isLoading ? (
        <MangaCardSkeletonGrid variant="default" count={6} />
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-4">
          {mangas.slice(0, 12).map((manga) => (
            <MangaCard
              key={manga.id}
              manga={manga}
              variant="default"
              showStatus
              showLanguage
              showActions={false}
              onClick={() => onClick(manga)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

/**
 * Section "Features".
 */
function FeaturesSection() {
  const features = [
    {
      icon: Search,
      title: 'Multi-Site Search',
      description: 'Search across 20+ manga sources simultaneously',
      color: 'text-primary',
    },
    {
      icon: Download,
      title: 'Smart Downloads',
      description: 'Download in CBZ, CBR, PDF with automatic organization',
      color: 'text-secondary',
    },
    {
      icon: Library,
      title: 'Local Library',
      description: 'Manage your collection with advanced tagging and filtering',
      color: 'text-accent',
    },
    {
      icon: Globe,
      title: 'Multi-Language',
      description: 'Support for English, French, Japanese, Korean, and more',
      color: 'text-cyan-400',
    },
    {
      icon: Zap,
      title: 'Real-Time Updates',
      description: 'WebSocket-powered live progress and notifications',
      color: 'text-yellow-400',
    },
    {
      icon: Settings,
      title: 'Fully Customizable',
      description: 'Tailor every aspect to your preferences',
      color: 'text-purple-400',
    },
  ];

  return (
    <section className="space-y-6">
      <div className="text-center">
        <h2 className="text-2xl font-mono font-bold text-primary mb-2">
          Powerful Features
        </h2>
        <p className="text-sm font-mono text-text-muted">
          Everything you need to manage your manga collection
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {features.map((feature) => {
          const Icon = feature.icon;
          return (
            <div
              key={feature.title}
              className="group p-4 rounded-xl border-2 border-border-dim bg-surface hover:border-secondary/50 transition-all duration-300 hover:shadow-[0_0_15px_rgba(0,255,255,0.1)]"
            >
              <div className="flex items-start gap-3">
                <div
                  className={cn(
                    'flex-shrink-0 flex items-center justify-center h-10 w-10 rounded-lg',
                    'bg-surface-alt border border-border-dim',
                    'transition-all duration-300',
                    'group-hover:border-secondary group-hover:shadow-[0_0_10px_rgba(0,255,255,0.2)]'
                  )}
                >
                  <Icon size={20} className={feature.color} />
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-sm font-mono font-bold text-text mb-1">
                    {feature.title}
                  </h3>
                  <p className="text-xs font-mono text-text-muted leading-relaxed">
                    {feature.description}
                  </p>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

/**
 * Section "Quick Actions".
 */
function QuickActionsSection() {
  const router = useRouter();
  const downloadCounts = useDownloadCounts();

  const actions = [
    {
      icon: Search,
      label: 'Search Manga',
      description: 'Find new manga to read',
      href: '/search',
      color: 'text-primary',
      borderColor: 'border-primary/30',
      bgColor: 'bg-primary-bg',
    },
    {
      icon: Library,
      label: 'My Library',
      description: 'Browse your collection',
      href: '/library',
      color: 'text-secondary',
      borderColor: 'border-secondary/30',
      bgColor: 'bg-secondary/10',
    },
    {
      icon: Download,
      label: 'Downloads',
      description: `${downloadCounts.active} active`,
      href: '/downloads',
      color: 'text-cyan-400',
      borderColor: 'border-cyan-500/30',
      bgColor: 'bg-cyan-500/10',
      badge: downloadCounts.active > 0 ? downloadCounts.active : undefined,
    },
    {
      icon: Settings,
      label: 'Settings',
      description: 'Configure the app',
      href: '/settings',
      color: 'text-accent',
      borderColor: 'border-accent/30',
      bgColor: 'bg-accent/10',
    },
  ];

  return (
    <section className="space-y-4">
      <h2 className="text-xl font-mono font-bold text-text flex items-center gap-2">
        <Zap size={20} className="text-yellow-400" />
        <span>Quick Actions</span>
      </h2>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {actions.map((action) => {
          const Icon = action.icon;
          return (
            <Link
              key={action.label}
              href={action.href}
              className={cn(
                'group relative flex flex-col items-center gap-2 p-4 rounded-xl',
                'border-2 transition-all duration-300',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                action.bgColor,
                action.borderColor,
                'hover:scale-105 hover:shadow-[0_0_20px_rgba(0,255,255,0.2)]'
              )}
            >
              <Icon size={24} className={action.color} />
              <div className="text-center">
                <div className="text-sm font-mono font-bold text-text mb-0.5">
                  {action.label}
                </div>
                <div className="text-[10px] font-mono text-text-muted">
                  {action.description}
                </div>
              </div>
              {action.badge && (
                <span className="absolute -top-1 -right-1 min-w-[20px] h-5 px-1 rounded-full bg-primary text-background text-[10px] font-mono font-bold flex items-center justify-center animate-pulse">
                  {action.badge}
                </span>
              )}
            </Link>
          );
        })}
      </div>
    </section>
  );
}

// ============================================================================
// PAGE PRINCIPALE
// ============================================================================

/**
 * HomePage - Page d'accueil de NexusDL.
 *
 * @returns Élément JSX de la page
 */
export default function HomePage() {
  const router = useRouter();

  // État local
  const [stats, setStats] = useState<AppStats>({
    totalMangas: 0,
    totalChapters: 0,
    totalDownloads: 0,
    totalSites: 0,
    totalSizeBytes: 0,
    readingTimeHours: 0,
  });

  // Hooks
  const downloadCounts = useDownloadCounts();
  const {
    mangas: libraryMangas,
    isLoading: isLibraryLoading,
    fetchLibrary,
  } = useLibrary();

  // Charger les données au montage
  useEffect(() => {
    fetchLibrary();
  }, [fetchLibrary]);

  // Calculer les statistiques
  useEffect(() => {
    const totalMangas = libraryMangas.length;
    const totalChapters = libraryMangas.reduce(
      (sum, m) => sum + (m.progress?.total_chapters || 0),
      0
    );
    const totalSizeBytes = libraryMangas.reduce(
      (sum, m) => sum + (m.total_size_bytes || 0),
      0
    );

    setStats({
      totalMangas,
      totalChapters,
      totalDownloads: downloadCounts.total,
      totalSites: 20, // TODO: Récupérer depuis le backend
      totalSizeBytes,
      readingTimeHours: 0, // TODO: Calculer depuis le backend
    });
  }, [libraryMangas, downloadCounts.total]);

  // Mangas en cours de lecture
  const readingMangas = useMemo(() => {
    return libraryMangas
      .filter((m) => m.reading_status === 'reading')
      .sort((a, b) => {
        const dateA = a.progress?.last_read_at ? new Date(a.progress.last_read_at).getTime() : 0;
        const dateB = b.progress?.last_read_at ? new Date(b.progress.last_read_at).getTime() : 0;
        return dateB - dateA;
      })
      .slice(0, 6);
  }, [libraryMangas]);

  // Mangas récemment ajoutés
  const recentMangas = useMemo(() => {
    return libraryMangas
      .sort((a, b) => {
        const dateA = a.added_at ? new Date(a.added_at).getTime() : 0;
        const dateB = b.added_at ? new Date(b.added_at).getTime() : 0;
        return dateB - dateA;
      })
      .slice(0, 12);
  }, [libraryMangas]);

  // Handlers
  const handleRead = useCallback(
    (manga: Manga) => {
      router.push(`/manga/${manga.site_id}/${manga.id}/read`);
    },
    [router]
  );

  const handleClick = useCallback(
    (manga: Manga) => {
      router.push(`/manga/${manga.site_id}/${manga.id}`);
    },
    [router]
  );

  const formatSize = (bytes: number): string => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
  };

  // ==========================================================================
  // RENDU
  // ==========================================================================

  return (
    <div className="container mx-auto px-4 py-6 max-w-7xl space-y-8">
      {/* ================================================================ */}
      {/* HERO SECTION */}
      {/* ================================================================ */}
      <section className="relative">
        {/* Background effects */}
        <div className="absolute inset-0 overflow-hidden pointer-events-none" aria-hidden="true">
          <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-primary/5 rounded-full blur-3xl animate-pulse" />
          <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-secondary/5 rounded-full blur-3xl animate-pulse delay-1000" />
        </div>

        {/* Contenu */}
        <div className="relative text-center py-12">
          {/* Logo */}
          <div className="inline-flex items-center justify-center h-20 w-20 rounded-2xl bg-primary-bg border-2 border-primary/40 mb-6 shadow-[0_0_30px_rgba(0,255,65,0.3)]">
            <Sparkles
              size={40}
              className="text-primary"
              style={{ filter: 'drop-shadow(0 0 10px rgba(0, 255, 65, 0.8))' }}
            />
          </div>

          {/* Titre */}
          <h1 className="text-4xl md:text-5xl font-mono font-bold text-primary tracking-wider mb-3">
            Welcome to NexusDL
          </h1>
          <p className="text-lg font-mono text-text-muted mb-8 max-w-2xl mx-auto">
            Your manga, your library, your way. Search, download, and organize
            your favorite manga from 20+ sources.
          </p>

          {/* Barre de recherche */}
          <SearchBarHero />
        </div>
      </section>

      {/* ================================================================ */}
      {/* STATISTIQUES */}
      {/* ================================================================ */}
      <section>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          <StatCard
            icon={Library}
            label="Mangas"
            value={stats.totalMangas}
            color="text-primary"
          />
          <StatCard
            icon={BookOpen}
            label="Chapters"
            value={stats.totalChapters}
            color="text-secondary"
          />
          <StatCard
            icon={Download}
            label="Downloads"
            value={stats.totalDownloads}
            color="text-cyan-400"
          />
          <StatCard
            icon={Globe}
            label="Sites"
            value={stats.totalSites}
            color="text-accent"
          />
          <StatCard
            icon={Folder}
            label="Total Size"
            value={formatSize(stats.totalSizeBytes)}
            color="text-yellow-400"
          />
          <StatCard
            icon={Clock}
            label="Reading Time"
            value={`${stats.readingTimeHours}h`}
            color="text-purple-400"
          />
        </div>
      </section>

      {/* ================================================================ */}
      {/* QUICK ACTIONS */}
      {/* ================================================================ */}
      <QuickActionsSection />

      {/* ================================================================ */}
      {/* CONTINUE READING */}
      {/* ================================================================ */}
      <ContinueReadingSection
        mangas={readingMangas}
        isLoading={isLibraryLoading}
        onRead={handleRead}
      />

      {/* ================================================================ */}
      {/* RECENTLY ADDED */}
      {/* ================================================================ */}
      <RecentlyAddedSection
        mangas={recentMangas}
        isLoading={isLibraryLoading}
        onClick={handleClick}
      />

      {/* ================================================================ */}
      {/* FEATURES */}
      {/* ================================================================ */}
      <FeaturesSection />

      {/* ================================================================ */}
      {/* FOOTER */}
      {/* ================================================================ */}
      <footer className="pt-8 border-t border-border-dim">
        <div className="flex flex-col md:flex-row items-center justify-between gap-4 text-xs font-mono text-text-dim">
          <div className="flex items-center gap-4">
            <span>© 2024 NexusDL</span>
            <span className="hidden sm:inline">•</span>
            <a
              href="https://github.com/nexusdl/nexusdl"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-secondary transition-colors"
            >
              GitHub
            </a>
            <span className="hidden sm:inline">•</span>
            <Link href="/docs" className="hover:text-secondary transition-colors">
              API Docs
            </Link>
          </div>

          <div className="flex items-center gap-2">
            <span>v0.1.0</span>
            <span className="text-text-dim">•</span>
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />
              <span>All systems operational</span>
            </span>
          </div>
        </div>
      </footer>
    </div>
  );
}
