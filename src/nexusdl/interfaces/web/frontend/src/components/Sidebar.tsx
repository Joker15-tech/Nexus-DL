/**
 * Composant Sidebar pour NexusDL.
 *
 * Barre de navigation latérale principale de l'application avec :
 *   - Navigation vers les pages principales
 *   - Compteurs d'activité (téléchargements actifs, erreurs)
 *   - Indicateur de statut de connexion WebSocket
 *   - Mode collapsed (icônes seules) / expanded (icônes + labels)
 *   - Version mobile en drawer avec overlay
 *   - Persistance de l'état collapsed dans localStorage
 *   - Badges animés pour les notifications
 *   - Tooltips au hover en mode collapsed
 *   - Accessibilité complète (ARIA, clavier, focus)
 *   - Style cyberpunk néon cohérent avec l'identité visuelle
 *
 * Architecture :
 *   Sidebar (composant principal)
 *   ├── SidebarHeader (logo + toggle)
 *   ├── SidebarNav (liens de navigation)
 *   │   └── SidebarItem (lien individuel)
 *   ├── SidebarActivity (compteurs d'activité)
 *   ├── SidebarStatus (statut connexion)
 *   ├── SidebarFooter (utilisateur + actions)
 *   └── MobileSidebarDrawer (version mobile)
 *
 * Utilisation :
 *   // Dans un layout
 *   <div className="flex">
 *     <Sidebar />
 *     <main className="flex-1">{children}</main>
 *   </div>
 *
 *   // Hook pour contrôler l'état
 *   const { isCollapsed, toggle, expand, collapse } = useSidebar();
 *
 * @module components/Sidebar
 */

'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { usePathname, useRouter } from 'next/navigation';
import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Download,
  Globe,
  Home,
  LayoutDashboard,
  Library,
  LogOut,
  Menu,
  Search,
  Settings,
  Sparkles,
  User,
  X,
  Wifi,
  WifiOff,
  AlertCircle,
  CheckCircle2,
  Clock,
  Zap,
} from 'lucide-react';

import { cn, storage } from '@/lib/utils';
import {
  useDownloadCounts,
  useUIPreferencesWithActions,
} from '@/store';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Élément de navigation dans la sidebar.
 */
interface NavItem {
  /** Identifiant unique. */
  id: string;
  /** Libellé affiché. */
  label: string;
  /** URL de destination. */
  href: string;
  /** Icône Lucide. */
  icon: React.ComponentType<{ size?: number; className?: string }>;
  /** Badge optionnel (compteur, statut). */
  badge?: {
    value: number | string;
    variant?: 'default' | 'success' | 'warning' | 'error' | 'info';
    pulse?: boolean;
  };
  /** Raccourci clavier (optionnel). */
  shortcut?: string;
  /** Section à laquelle appartient l'item. */
  section?: 'main' | 'library' | 'system';
  /** Masquer le label en mode collapsed (par défaut: true). */
  hideLabelOnCollapse?: boolean;
  /** Indicateur "bientôt disponible". */
  comingSoon?: boolean;
}

/**
 * Props du composant Sidebar.
 */
export interface SidebarProps {
  /** État collapsed initial (défaut: false). */
  defaultCollapsed?: boolean;
  /** Classe CSS additionnelle. */
  className?: string;
}

/**
 * Contexte de la sidebar pour partager l'état collapsed.
 */
interface SidebarContextValue {
  /** Indique si la sidebar est collapsed. */
  isCollapsed: boolean;
  /** Toggle l'état collapsed. */
  toggle: () => void;
  /** Collapse la sidebar. */
  collapse: () => void;
  /** Expand la sidebar. */
  expand: () => void;
  /** Indique si on est en mode mobile. */
  isMobile: boolean;
  /** État du drawer mobile. */
  isMobileOpen: boolean;
  /** Ouvre le drawer mobile. */
  openMobile: () => void;
  /** Ferme le drawer mobile. */
  closeMobile: () => void;
}

// ============================================================================
// CONSTANTES
// ============================================================================

/**
 * Clé de stockage pour l'état collapsed.
 */
const SIDEBAR_COLLAPSED_KEY = 'nexusdl-sidebar-collapsed';

/**
 * Largeur de la sidebar en mode expanded.
 */
const SIDEBAR_WIDTH_EXPANDED = 260;

/**
 * Largeur de la sidebar en mode collapsed.
 */
const SIDEBAR_WIDTH_COLLAPSED = 72;

/**
 * Breakpoint pour le mode mobile.
 */
const MOBILE_BREAKPOINT = 1024;

/**
 * Items de navigation principaux.
 */
const NAV_ITEMS: NavItem[] = [
  {
    id: 'dashboard',
    label: 'Dashboard',
    href: '/',
    icon: LayoutDashboard,
    section: 'main',
    shortcut: '⌘1',
  },
  {
    id: 'search',
    label: 'Search',
    href: '/search',
    icon: Search,
    section: 'main',
    shortcut: '⌘K',
  },
  {
    id: 'library',
    label: 'Library',
    href: '/library',
    icon: Library,
    section: 'library',
    shortcut: '⌘2',
  },
  {
    id: 'downloads',
    label: 'Downloads',
    href: '/downloads',
    icon: Download,
    section: 'library',
    shortcut: '⌘3',
  },
  {
    id: 'sites',
    label: 'Sites',
    href: '/sites',
    icon: Globe,
    section: 'system',
    shortcut: '⌘4',
  },
  {
    id: 'settings',
    label: 'Settings',
    href: '/settings',
    icon: Settings,
    section: 'system',
    shortcut: '⌘,',
  },
];

// ============================================================================
// CONTEXT - Sidebar State
// ============================================================================

const SidebarContext = createContext<SidebarContextValue | null>(null);

/**
 * Hook pour accéder au contexte de la sidebar.
 */
export function useSidebar(): SidebarContextValue {
  const context = useContext(SidebarContext);
  if (!context) {
    throw new Error('useSidebar must be used within a SidebarProvider');
  }
  return context;
}

/**
 * Provider pour le contexte de la sidebar.
 */
export function SidebarProvider({
  children,
  defaultCollapsed = false,
}: {
  children: ReactNode;
  defaultCollapsed?: boolean;
}) {
  const [isCollapsed, setIsCollapsed] = useState<boolean>(() => {
    if (typeof window === 'undefined') return defaultCollapsed;
    const stored = storage.get<boolean | null>(SIDEBAR_COLLAPSED_KEY, null);
    return stored ?? defaultCollapsed;
  });

  const [isMobileOpen, setIsMobileOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);

  // Détecter le mode mobile
  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < MOBILE_BREAKPOINT);
    };

    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  // Persister l'état collapsed
  useEffect(() => {
    storage.set(SIDEBAR_COLLAPSED_KEY, isCollapsed);
  }, [isCollapsed]);

  // Fermer le drawer mobile au resize vers desktop
  useEffect(() => {
    if (!isMobile && isMobileOpen) {
      setIsMobileOpen(false);
    }
  }, [isMobile, isMobileOpen]);

  // Lock scroll quand drawer mobile ouvert
  useEffect(() => {
    if (isMobileOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [isMobileOpen]);

  const toggle = useCallback(() => setIsCollapsed((prev) => !prev), []);
  const collapse = useCallback(() => setIsCollapsed(true), []);
  const expand = useCallback(() => setIsCollapsed(false), []);
  const openMobile = useCallback(() => setIsMobileOpen(true), []);
  const closeMobile = useCallback(() => setIsMobileOpen(false), []);

  const value = useMemo<SidebarContextValue>(
    () => ({
      isCollapsed,
      toggle,
      collapse,
      expand,
      isMobile,
      isMobileOpen,
      openMobile,
      closeMobile,
    }),
    [isCollapsed, isMobileOpen, isMobile, toggle, collapse, expand, openMobile, closeMobile]
  );

  return <SidebarContext.Provider value={value}>{children}</SidebarContext.Provider>;
}

// ============================================================================
// SOUS-COMPOSANTS
// ============================================================================

/**
 * Badge de compteur animé.
 */
function SidebarBadge({
  value,
  variant = 'default',
  pulse = false,
  collapsed = false,
}: {
  value: number | string;
  variant?: 'default' | 'success' | 'warning' | 'error' | 'info';
  pulse?: boolean;
  collapsed?: boolean;
}) {
  const variantClasses = {
    default: 'bg-primary/20 text-primary border-primary/30',
    success: 'bg-green-500/20 text-green-400 border-green-500/30',
    warning: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
    error: 'bg-red-500/20 text-red-400 border-red-500/30',
    info: 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30',
  };

  if (collapsed) {
    return (
      <span
        className={cn(
          'absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1',
          'rounded-full border text-[10px] font-mono font-bold',
          'flex items-center justify-center',
          variantClasses[variant],
          pulse && 'animate-pulse'
        )}
        aria-label={`${value} notifications`}
      >
        {value}
      </span>
    );
  }

  return (
    <span
      className={cn(
        'ml-auto min-w-[24px] h-5 px-1.5',
        'rounded-md border text-[10px] font-mono font-bold',
        'flex items-center justify-center',
        variantClasses[variant],
        pulse && 'animate-pulse'
      )}
    >
      {value}
    </span>
  );
}

/**
 * Item de navigation individuel.
 */
function SidebarItem({
  item,
  isActive,
  isCollapsed,
  onClick,
}: {
  item: NavItem;
  isActive: boolean;
  isCollapsed: boolean;
  onClick?: () => void;
}) {
  const Icon = item.icon;
  const [showTooltip, setShowTooltip] = useState(false);
  const tooltipTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleMouseEnter = () => {
    if (!isCollapsed) return;
    tooltipTimeoutRef.current = setTimeout(() => {
      setShowTooltip(true);
    }, 300);
  };

  const handleMouseLeave = () => {
    if (tooltipTimeoutRef.current) {
      clearTimeout(tooltipTimeoutRef.current);
    }
    setShowTooltip(false);
  };

  useEffect(() => {
    return () => {
      if (tooltipTimeoutRef.current) {
        clearTimeout(tooltipTimeoutRef.current);
      }
    };
  }, []);

  return (
    <li className="relative" onMouseEnter={handleMouseEnter} onMouseLeave={handleMouseLeave}>
      <a
        href={item.href}
        onClick={(e) => {
          if (item.comingSoon) {
            e.preventDefault();
            return;
          }
          onClick?.();
        }}
        aria-label={
          isCollapsed
            ? `${item.label}${item.badge ? ` (${item.badge.value})` : ''}${item.comingSoon ? ' - Coming soon' : ''}`
            : undefined
        }
        aria-current={isActive ? 'page' : undefined}
        aria-disabled={item.comingSoon}
        className={cn(
          'group relative flex items-center gap-3',
          'rounded-lg px-3 py-2.5',
          'transition-all duration-200',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',

          // Layout adapté au mode collapsed
          isCollapsed ? 'justify-center' : 'justify-start',

          // États visuels
          item.comingSoon
            ? 'opacity-50 cursor-not-allowed'
            : isActive
              ? 'bg-primary-bg border border-primary/40 text-primary shadow-[0_0_10px_rgba(0,255,65,0.2)]'
              : 'border border-transparent text-text-muted hover:bg-surface-hover hover:text-secondary hover:border-border-dim',

          // Glow au hover pour items actifs
          isActive && 'hover:shadow-[0_0_15px_rgba(0,255,65,0.3)]'
        )}
      >
        {/* Indicateur de page active (barre verticale à gauche) */}
        {isActive && !isCollapsed && (
          <span
            className="absolute left-0 top-1/2 -translate-y-1/2 h-6 w-0.5 rounded-r bg-primary shadow-[0_0_8px_rgba(0,255,65,0.8)]"
            aria-hidden="true"
          />
        )}

        {/* Icône */}
        <div className="relative flex-shrink-0">
          <Icon
            size={20}
            className={cn(
              'transition-all duration-200',
              isActive
                ? 'text-primary'
                : 'text-text-muted group-hover:text-secondary',
              !item.comingSoon && 'group-hover:scale-110'
            )}
            style={
              isActive
                ? { filter: 'drop-shadow(0 0 4px rgba(0, 255, 65, 0.6))' }
                : undefined
            }
          />

          {/* Badge en mode collapsed */}
          {isCollapsed && item.badge && (
            <SidebarBadge
              value={item.badge.value}
              variant={item.badge.variant}
              pulse={item.badge.pulse}
              collapsed
            />
          )}
        </div>

        {/* Label (masqué en mode collapsed) */}
        {!isCollapsed && (
          <>
            <span className="flex-1 font-mono text-sm font-medium truncate">
              {item.label}
            </span>

            {/* Badge en mode expanded */}
            {item.badge && (
              <SidebarBadge
                value={item.badge.value}
                variant={item.badge.variant}
                pulse={item.badge.pulse}
              />
            )}

            {/* Raccourci clavier */}
            {item.shortcut && (
              <kbd
                className="hidden xl:inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono bg-background/50 border border-border-dim text-text-dim"
                aria-hidden="true"
              >
                {item.shortcut}
              </kbd>
            )}

            {/* Label "Coming soon" */}
            {item.comingSoon && (
              <span className="text-[9px] font-mono text-text-dim italic">
                soon
              </span>
            )}
          </>
        )}
      </a>

      {/* Tooltip en mode collapsed */}
      {isCollapsed && showTooltip && (
        <div
          role="tooltip"
          className={cn(
            'pointer-events-none absolute left-full top-1/2 -translate-y-1/2',
            'ml-3 z-50',
            'whitespace-nowrap rounded-md px-2.5 py-1.5',
            'bg-surface-alt border border-border-dim',
            'text-xs font-mono text-text shadow-lg',
            'animate-in fade-in-0 zoom-in-95 duration-150'
          )}
        >
          <span className="text-primary font-semibold">{item.label}</span>
          {item.badge && (
            <span className="ml-2 text-text-muted">
              ({item.badge.value})
            </span>
          )}
          {item.shortcut && (
            <span className="ml-2 text-text-dim">[{item.shortcut}]</span>
          )}
          {/* Flèche du tooltip */}
          <div
            className="absolute right-full top-1/2 -translate-y-1/2 border-4 border-transparent border-r-surface-alt"
            aria-hidden="true"
          />
        </div>
      )}
    </li>
  );
}

/**
 * Séparateur de section avec label.
 */
function SidebarSection({
  label,
  isCollapsed,
}: {
  label: string;
  isCollapsed: boolean;
}) {
  if (isCollapsed) {
    return (
      <div className="my-2 mx-auto h-px w-6 bg-border-dim" aria-hidden="true" />
    );
  }

  return (
    <div className="px-3 py-2 mt-2">
      <h3 className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider">
        {label}
      </h3>
    </div>
  );
}

/**
 * Indicateur de statut de connexion WebSocket.
 */
function ConnectionStatus({ isCollapsed }: { isCollapsed: boolean }) {
  // Simule un statut de connexion - à remplacer par le vrai hook
  const [isConnected, setIsConnected] = useState(true);

  useEffect(() => {
    // Ici on utiliserait useWebSocket() du store
    // Pour l'instant, on simule
  }, []);

  const statusConfig = isConnected
    ? {
        icon: Wifi,
        label: 'Connected',
        color: 'text-green-400',
        bgColor: 'bg-green-500/10',
        borderColor: 'border-green-500/30',
        glow: 'shadow-[0_0_8px_rgba(0,255,65,0.3)]',
      }
    : {
        icon: WifiOff,
        label: 'Disconnected',
        color: 'text-red-400',
        bgColor: 'bg-red-500/10',
        borderColor: 'border-red-500/30',
        glow: '',
      };

  const Icon = statusConfig.icon;

  if (isCollapsed) {
    return (
      <div
        className={cn(
          'flex items-center justify-center',
          'h-8 w-8 rounded-full',
          statusConfig.bgColor,
          'border',
          statusConfig.borderColor,
          statusConfig.glow
        )}
        title={statusConfig.label}
        aria-label={`WebSocket: ${statusConfig.label}`}
      >
        <Icon
          size={14}
          className={cn(statusConfig.color, isConnected && 'animate-pulse')}
        />
      </div>
    );
  }

  return (
    <div
      className={cn(
        'flex items-center gap-2 px-3 py-2 rounded-lg',
        statusConfig.bgColor,
        'border',
        statusConfig.borderColor,
        statusConfig.glow
      )}
      aria-label={`WebSocket: ${statusConfig.label}`}
    >
      <Icon
        size={14}
        className={cn(statusConfig.color, isConnected && 'animate-pulse')}
      />
      <span className={cn('text-xs font-mono font-medium', statusConfig.color)}>
        {statusConfig.label}
      </span>
      {isConnected && (
        <span className="ml-auto flex h-2 w-2">
          <span className="animate-ping absolute inline-flex h-2 w-2 rounded-full bg-green-400 opacity-75" />
          <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
        </span>
      )}
    </div>
  );
}

/**
 * Panneau d'activité (compteurs de téléchargements).
 */
function ActivityPanel({ isCollapsed }: { isCollapsed: boolean }) {
  const counts = useDownloadCounts();

  const hasActivity = counts.active > 0 || counts.pending > 0 || counts.failed > 0;

  if (!hasActivity && isCollapsed) {
    return null;
  }

  if (isCollapsed) {
    return (
      <div
        className="relative"
        title={`${counts.active} active, ${counts.pending} pending`}
      >
        <div className="flex items-center justify-center h-8 w-8 rounded-full bg-cyan-500/10 border border-cyan-500/30">
          <Zap
            size={14}
            className={cn(
              'text-cyan-400',
              counts.active > 0 && 'animate-pulse'
            )}
          />
        </div>
        {counts.active > 0 && (
          <span className="absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1 rounded-full bg-cyan-500/20 border border-cyan-500/30 text-[10px] font-mono font-bold text-cyan-400 flex items-center justify-center">
            {counts.active}
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="px-3 py-2 rounded-lg bg-surface-alt/50 border border-border-dim">
      <div className="flex items-center gap-2 mb-2">
        <Zap size={12} className="text-cyan-400" />
        <h4 className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider">
          Activity
        </h4>
      </div>

      <div className="grid grid-cols-3 gap-1.5">
        <div className="flex flex-col items-center p-1.5 rounded bg-background/50 border border-border-dim">
          <span className="text-xs font-mono font-bold text-primary">
            {counts.active}
          </span>
          <span className="text-[9px] font-mono text-text-dim">Active</span>
        </div>
        <div className="flex flex-col items-center p-1.5 rounded bg-background/50 border border-border-dim">
          <span className="text-xs font-mono font-bold text-yellow-400">
            {counts.pending}
          </span>
          <span className="text-[9px] font-mono text-text-dim">Queue</span>
        </div>
        <div className="flex flex-col items-center p-1.5 rounded bg-background/50 border border-border-dim">
          <span className="text-xs font-mono font-bold text-red-400">
            {counts.failed}
          </span>
          <span className="text-[9px] font-mono text-text-dim">Failed</span>
        </div>
      </div>

      {counts.completed > 0 && (
        <div className="mt-2 flex items-center justify-between text-[10px] font-mono">
          <span className="text-text-dim flex items-center gap-1">
            <CheckCircle2 size={10} className="text-green-400" />
            Completed
          </span>
          <span className="text-green-400 font-bold">{counts.completed}</span>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// COMPOSANT PRINCIPAL - SIDEBAR
// ============================================================================

/**
 * Sidebar - Barre de navigation latérale principale.
 *
 * @param props - Props du composant
 * @returns Élément JSX de la Sidebar
 *
 * @example
 * ```tsx
 * // Dans un layout
 * <SidebarProvider>
 *   <div className="flex min-h-screen">
 *     <Sidebar />
 *     <main className="flex-1">{children}</main>
 *   </div>
 * </SidebarProvider>
 * ```
 */
export function Sidebar({ defaultCollapsed = false, className }: SidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { isCollapsed, isMobile } = useSidebar();

  // En mode mobile, ne pas afficher la sidebar desktop
  if (isMobile) {
    return <MobileSidebarTrigger />;
  }

  // Déterminer l'item actif
  const isActive = (href: string) => {
    if (href === '/') return pathname === '/';
    return pathname.startsWith(href);
  };

  // Construire les badges dynamiques
  const counts = useDownloadCounts();

  const navItemsWithBadges = useMemo<NavItem[]>(() => {
    return NAV_ITEMS.map((item) => {
      if (item.id === 'downloads') {
        const total = counts.active + counts.pending;
        if (total === 0) return item;

        return {
          ...item,
          badge: {
            value: total,
            variant: counts.failed > 0 ? 'warning' : 'info',
            pulse: counts.active > 0,
          },
        };
      }
      return item;
    });
  }, [counts]);

  // Grouper les items par section
  const sections = useMemo(() => {
    const grouped: Record<string, NavItem[]> = {};
    navItemsWithBadges.forEach((item) => {
      const section = item.section || 'main';
      if (!grouped[section]) grouped[section] = [];
      grouped[section].push(item);
    });
    return grouped;
  }, [navItemsWithBadges]);

  const sectionLabels: Record<string, string> = {
    main: 'Navigation',
    library: 'Library',
    system: 'System',
  };

  const handleNavClick = () => {
    // Sur desktop, pas besoin de fermer quoi que ce soit
  };

  return (
    <aside
      aria-label="Main navigation"
      className={cn(
        'relative flex flex-col h-screen',
        'bg-surface border-r-2 border-border-dim',
        'transition-all duration-300 ease-in-out',
        'flex-shrink-0',
        className
      )}
      style={{
        width: isCollapsed ? SIDEBAR_WIDTH_COLLAPSED : SIDEBAR_WIDTH_EXPANDED,
      }}
    >
      {/* ================================================================ */}
      {/* HEADER - Logo + Toggle */}
      {/* ================================================================ */}
      <div
        className={cn(
          'flex items-center border-b-2 border-border-dim',
          'bg-surface-alt/50',
          isCollapsed ? 'justify-center px-2 h-16' : 'justify-between px-4 h-16'
        )}
      >
        {/* Logo */}
        {!isCollapsed && (
          <button
            type="button"
            onClick={() => router.push('/')}
            className="flex items-center gap-2 group focus:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded"
            aria-label="Go to homepage"
          >
            <div
              className="flex items-center justify-center h-8 w-8 rounded-md bg-primary-bg border border-primary/40 group-hover:border-primary transition-colors"
              style={{ boxShadow: '0 0 10px rgba(0, 255, 65, 0.3)' }}
            >
              <Sparkles
                size={16}
                className="text-primary"
                style={{ filter: 'drop-shadow(0 0 3px rgba(0, 255, 65, 0.8))' }}
              />
            </div>
            <div className="flex flex-col items-start">
              <span className="font-mono font-bold text-sm text-primary tracking-wider">
                NexusDL
              </span>
              <span className="text-[9px] font-mono text-text-dim -mt-0.5">
                v0.1.0
              </span>
            </div>
          </button>
        )}

        {/* Logo compacté */}
        {isCollapsed && (
          <button
            type="button"
            onClick={() => router.push('/')}
            className="flex items-center justify-center h-10 w-10 rounded-md bg-primary-bg border border-primary/40 hover:border-primary transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            aria-label="Go to homepage"
            style={{ boxShadow: '0 0 10px rgba(0, 255, 65, 0.3)' }}
          >
            <Sparkles
              size={18}
              className="text-primary"
              style={{ filter: 'drop-shadow(0 0 3px rgba(0, 255, 65, 0.8))' }}
            />
          </button>
        )}
      </div>

      {/* ================================================================ */}
      {/* NAVIGATION */}
      {/* ================================================================ */}
      <nav
        aria-label="Main navigation"
        className="flex-1 overflow-y-auto overflow-x-hidden py-3 scrollbar-thin scrollbar-thumb-border-dim scrollbar-track-transparent"
      >
        <ul className="space-y-0.5 px-2" role="list">
          {Object.entries(sections).map(([sectionKey, items]) => (
            <li key={sectionKey}>
              <SidebarSection label={sectionLabels[sectionKey] || sectionKey} isCollapsed={isCollapsed} />
              <ul className="space-y-0.5" role="list">
                {items.map((item) => (
                  <SidebarItem
                    key={item.id}
                    item={item}
                    isActive={isActive(item.href)}
                    isCollapsed={isCollapsed}
                    onClick={handleNavClick}
                  />
                ))}
              </ul>
            </li>
          ))}
        </ul>
      </nav>

      {/* ================================================================ */}
      {/* ACTIVITY PANEL */}
      {/* ================================================================ */}
      <div className="px-2 py-2 border-t border-border-dim">
        <ActivityPanel isCollapsed={isCollapsed} />
      </div>

      {/* ================================================================ */}
      {/* CONNECTION STATUS */}
      {/* ================================================================ */}
      <div className="px-2 py-2 border-t border-border-dim">
        <ConnectionStatus isCollapsed={isCollapsed} />
      </div>

      {/* ================================================================ */}
      {/* FOOTER - User + Toggle */}
      {/* ================================================================ */}
      <div className="border-t-2 border-border-dim bg-surface-alt/50 p-2">
        {!isCollapsed ? (
          <div className="flex items-center gap-2">
            {/* User avatar */}
            <button
              type="button"
              onClick={() => router.push('/settings')}
              className="flex items-center gap-2 flex-1 min-w-0 p-1.5 rounded-lg hover:bg-surface-hover transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
              aria-label="User settings"
            >
              <div className="flex-shrink-0 h-8 w-8 rounded-full bg-gradient-to-br from-primary/30 to-secondary/30 border border-primary/40 flex items-center justify-center">
                <User size={14} className="text-primary" />
              </div>
              <div className="flex-1 min-w-0 text-left">
                <div className="text-xs font-mono font-semibold text-text truncate">
                  Guest User
                </div>
                <div className="text-[10px] font-mono text-text-dim truncate">
                  guest@nexusdl.local
                </div>
              </div>
            </button>

            {/* Toggle collapse */}
            <button
              type="button"
              onClick={() => useSidebar().collapse()}
              aria-label="Collapse sidebar"
              className="flex-shrink-0 p-1.5 rounded-md hover:bg-surface-hover text-text-muted hover:text-secondary transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              <ChevronLeft size={16} />
            </button>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2">
            {/* Avatar compact */}
            <button
              type="button"
              onClick={() => router.push('/settings')}
              className="h-9 w-9 rounded-full bg-gradient-to-br from-primary/30 to-secondary/30 border border-primary/40 flex items-center justify-center hover:border-primary transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
              aria-label="User settings"
            >
              <User size={14} className="text-primary" />
            </button>

            {/* Toggle expand */}
            <button
              type="button"
              onClick={() => useSidebar().expand()}
              aria-label="Expand sidebar"
              className="p-1.5 rounded-md hover:bg-surface-hover text-text-muted hover:text-secondary transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              <ChevronRight size={16} />
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}

// ============================================================================
// MOBILE SIDEBAR
// ============================================================================

/**
 * Bouton trigger pour ouvrir la sidebar mobile.
 */
function MobileSidebarTrigger() {
  const { openMobile } = useSidebar();

  return (
    <button
      type="button"
      onClick={openMobile}
      aria-label="Open navigation menu"
      className={cn(
        'fixed top-4 left-4 z-40',
        'h-10 w-10 rounded-lg',
        'bg-surface border-2 border-border-dim',
        'flex items-center justify-center',
        'text-text-muted hover:text-secondary',
        'hover:border-secondary hover:bg-surface-hover',
        'transition-all duration-200',
        'shadow-lg',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary'
      )}
    >
      <Menu size={20} />
    </button>
  );
}

/**
 * Drawer mobile pour la sidebar.
 */
export function MobileSidebarDrawer() {
  const pathname = usePathname();
  const router = useRouter();
  const { isMobile, isMobileOpen, closeMobile } = useSidebar();
  const drawerRef = useRef<HTMLDivElement>(null);

  // Fermer au changement de route
  useEffect(() => {
    closeMobile();
  }, [pathname, closeMobile]);

  // Fermer à la touche Escape
  useEffect(() => {
    if (!isMobileOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        closeMobile();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isMobileOpen, closeMobile]);

  if (!isMobile || !isMobileOpen) return null;

  const isActive = (href: string) => {
    if (href === '/') return pathname === '/';
    return pathname.startsWith(href);
  };

  return (
    <>
      {/* Overlay */}
      <div
        className="fixed inset-0 z-40 bg-background/80 backdrop-blur-sm animate-in fade-in duration-200"
        onClick={closeMobile}
        aria-hidden="true"
      />

      {/* Drawer */}
      <div
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-label="Navigation menu"
        className={cn(
          'fixed inset-y-0 left-0 z-50',
          'w-[280px] max-w-[85vw]',
          'bg-surface border-r-2 border-border-dim',
          'flex flex-col',
          'shadow-2xl',
          'animate-in slide-in-from-left duration-300'
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 h-16 border-b-2 border-border-dim bg-surface-alt/50">
          <button
            type="button"
            onClick={() => {
              router.push('/');
              closeMobile();
            }}
            className="flex items-center gap-2 group focus:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded"
          >
            <div
              className="flex items-center justify-center h-8 w-8 rounded-md bg-primary-bg border border-primary/40"
              style={{ boxShadow: '0 0 10px rgba(0, 255, 65, 0.3)' }}
            >
              <Sparkles size={16} className="text-primary" />
            </div>
            <div className="flex flex-col items-start">
              <span className="font-mono font-bold text-sm text-primary tracking-wider">
                NexusDL
              </span>
              <span className="text-[9px] font-mono text-text-dim -mt-0.5">
                v0.1.0
              </span>
            </div>
          </button>

          <button
            type="button"
            onClick={closeMobile}
            aria-label="Close navigation menu"
            className="p-2 rounded-md hover:bg-surface-hover text-text-muted hover:text-secondary transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            <X size={20} />
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto py-3 px-2">
          <ul className="space-y-0.5">
            {NAV_ITEMS.map((item) => (
              <SidebarItem
                key={item.id}
                item={item}
                isActive={isActive(item.href)}
                isCollapsed={false}
                onClick={closeMobile}
              />
            ))}
          </ul>
        </nav>

        {/* Activity */}
        <div className="px-2 py-2 border-t border-border-dim">
          <ActivityPanel isCollapsed={false} />
        </div>

        {/* Connection Status */}
        <div className="px-2 py-2 border-t border-border-dim">
          <ConnectionStatus isCollapsed={false} />
        </div>

        {/* Footer */}
        <div className="border-t-2 border-border-dim bg-surface-alt/50 p-3">
          <div className="flex items-center gap-2">
            <div className="flex-shrink-0 h-9 w-9 rounded-full bg-gradient-to-br from-primary/30 to-secondary/30 border border-primary/40 flex items-center justify-center">
              <User size={16} className="text-primary" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-mono font-semibold text-text truncate">
                Guest User
              </div>
              <div className="text-[10px] font-mono text-text-dim truncate">
                guest@nexusdl.local
              </div>
            </div>
            <button
              type="button"
              aria-label="Logout"
              className="p-2 rounded-md hover:bg-surface-hover text-text-muted hover:text-red-400 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

// ============================================================================
// HOOK PERSONNALISÉ - useSidebarState
// ============================================================================

/**
 * Hook simplifié pour accéder à l'état de la sidebar.
 *
 * @example
 * ```tsx
 * const { isCollapsed, toggle } = useSidebarState();
 * ```
 */
export function useSidebarState() {
  const context = useSidebar();
  return {
    isCollapsed: context.isCollapsed,
    toggle: context.toggle,
    collapse: context.collapse,
    expand: context.expand,
  };
}

// ============================================================================
// EXPORTS
// ============================================================================

export default Sidebar;
export {
  MobileSidebarDrawer,
  MobileSidebarTrigger,
  SidebarProvider,
  SidebarSection,
  SidebarItem,
  SidebarBadge,
  ConnectionStatus,
  ActivityPanel,
  NAV_ITEMS,
  SIDEBAR_WIDTH_EXPANDED,
  SIDEBAR_WIDTH_COLLAPSED,
  MOBILE_BREAKPOINT,
};
export type { NavItem };
