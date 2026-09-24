/**
 * Providers globaux pour l'application NexusDL.
 *
 * Ce composant encapsule l'application avec tous les providers nécessaires :
 *   - QueryClientProvider (React Query pour le cache API)
 *   - ThemeProvider (gestion du thème avec persistance)
 *   - LanguageProvider (internationalisation)
 *   - WebSocketProvider (gestion des connexions temps réel)
 *   - ErrorBoundary (capture d'erreurs React)
 *   - DevTools (uniquement en développement)
 *
 * Architecture :
 *   providers.tsx
 *   ├── QueryClientProvider (React Query)
 *   ├── ThemeProvider (thème cyberpunk/dark/light)
 *   ├── LanguageProvider (i18n)
 *   ├── WebSocketProvider (connexions temps réel)
 *   ├── ErrorBoundary (gestion d'erreurs)
 *   └── DevTools (React Query DevTools)
 *
 * Utilisation :
 *   // Dans layout.tsx
 *   <Providers>
 *     {children}
 *   </Providers>
 *
 * @module app/providers
 */

'use client';

import { useEffect, useState, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';
import { toast } from 'sonner';

import {
  useThemeEffect,
  useLanguageEffect,
  useSettingsStore,
} from '@/store';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Props du composant Providers.
 */
interface ProvidersProps {
  /** Contenu enfant à encapsuler. */
  children: ReactNode;
}

/**
 * État de l'ErrorBoundary.
 */
interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

// ============================================================================
// QUERY CLIENT
// ============================================================================

/**
 * Crée une instance de QueryClient avec configuration optimisée.
 */
function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Durée pendant laquelle les données sont considérées comme fraîches
        staleTime: 1000 * 60 * 5, // 5 minutes

        // Durée de conservation dans le cache (garbage collection)
        gcTime: 1000 * 60 * 30, // 30 minutes

        // Nombre de tentatives en cas d'échec
        retry: (failureCount, error) => {
          // Ne pas retry sur les erreurs 4xx
          if (error && typeof error === 'object' && 'status' in error) {
            const status = (error as any).status;
            if (status >= 400 && status < 500) {
              return false;
            }
          }
          // Max 2 tentatives pour les autres erreurs
          return failureCount < 2;
        },

        // Délai entre les tentatives (exponential backoff)
        retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 30000),

        // Ne pas refetch au focus de la fenêtre
        refetchOnWindowFocus: false,

        // Refetch à la reconnexion
        refetchOnReconnect: true,

        // Ne pas refetch au montage si les données sont dans le cache
        refetchOnMount: false,
      },
      mutations: {
        // Une seule tentative pour les mutations
        retry: 1,

        // Délai entre les tentatives
        retryDelay: 1000,
      },
    },
  });
}

/**
 * Instance globale du QueryClient (évite la recréation en SSR).
 */
let browserQueryClient: QueryClient | undefined;

function getQueryClient(): QueryClient {
  if (typeof window === 'undefined') {
    // Server: toujours créer un nouveau QueryClient
    return makeQueryClient();
  }

  // Browser: créer un QueryClient une seule fois
  if (!browserQueryClient) {
    browserQueryClient = makeQueryClient();
  }

  return browserQueryClient;
}

// ============================================================================
// ERROR BOUNDARY
// ============================================================================

/**
 * ErrorBoundary - Capture les erreurs React et affiche un fallback.
 */
class ErrorBoundary extends React.Component<
  { children: ReactNode; fallback?: ReactNode },
  ErrorBoundaryState
> {
  constructor(props: { children: ReactNode; fallback?: ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('[ErrorBoundary] Error caught:', error, errorInfo);

    // Afficher un toast d'erreur
    toast.error('Application Error', {
      description: error.message,
      duration: 10000,
    });
  }

  render() {
    if (this.state.hasError) {
      // Fallback personnalisé ou fallback par défaut
      return (
        this.props.fallback || (
          <div className="min-h-screen flex items-center justify-center bg-background p-4">
            <div className="max-w-md w-full p-6 rounded-xl border-2 border-red-500/30 bg-surface">
              <div className="flex items-center gap-3 mb-4">
                <div className="flex items-center justify-center h-12 w-12 rounded-full bg-red-500/10 border-2 border-red-500/30">
                  <svg
                    className="h-6 w-6 text-red-400"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                    />
                  </svg>
                </div>
                <div>
                  <h2 className="text-lg font-mono font-bold text-text">
                    Something went wrong
                  </h2>
                  <p className="text-xs font-mono text-text-muted">
                    An unexpected error occurred
                  </p>
                </div>
              </div>

              <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 mb-4">
                <p className="text-xs font-mono text-red-400 break-words">
                  {this.state.error?.message || 'Unknown error'}
                </p>
              </div>

              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => window.location.reload()}
                  className="flex-1 px-4 py-2 rounded-lg bg-primary text-background font-mono font-bold text-sm hover:bg-primary-bright transition-colors"
                >
                  Reload Page
                </button>
                <button
                  type="button"
                  onClick={() => this.setState({ hasError: false, error: null })}
                  className="flex-1 px-4 py-2 rounded-lg bg-surface-alt text-text-muted border-2 border-border-dim font-mono text-sm hover:border-secondary hover:text-secondary transition-colors"
                >
                  Try Again
                </button>
              </div>
            </div>
          </div>
        )
      );
    }

    return this.props.children;
  }
}

// ============================================================================
// THEME PROVIDER
// ============================================================================

/**
 * ThemeProvider - Gère le thème de l'application.
 *
 * Applique automatiquement le thème au DOM et écoute les changements
 * de préférences système.
 */
function ThemeProvider({ children }: { children: ReactNode }) {
  // Appliquer le thème au DOM
  useThemeEffect();

  // Écouter les changements de préférences système
  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');

    const handleChange = () => {
      // Déclencher un événement personnalisé pour notifier les composants
      window.dispatchEvent(new CustomEvent('theme-change'));
    };

    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  }, []);

  return <>{children}</>;
}

// ============================================================================
// LANGUAGE PROVIDER
// ============================================================================

/**
 * LanguageProvider - Gère la langue de l'application.
 *
 * Applique automatiquement la langue au DOM.
 */
function LanguageProvider({ children }: { children: ReactNode }) {
  // Appliquer la langue au DOM
  useLanguageEffect();

  return <>{children}</>;
}

// ============================================================================
// WEBSOCKET PROVIDER
// ============================================================================

/**
 * WebSocketProvider - Gère les connexions WebSocket globales.
 */
function WebSocketProvider({ children }: { children: ReactNode }) {
  const [isConnected, setIsConnected] = useState(false);

  useEffect(() => {
    // Importer dynamiquement pour éviter les problèmes SSR
    import('@/lib/ws').then(({ wsClient }) => {
      // Connecter automatiquement
      if (!wsClient.isConnected()) {
        wsClient.connect();
      }

      // Écouter les changements de connexion
      const unsubscribe = wsClient.onConnectionChange((state) => {
        setIsConnected(state === 'connected');
      });

      return () => {
        unsubscribe();
        // Ne pas déconnecter au démontage (connexion globale)
      };
    });
  }, []);

  return <>{children}</>;
}

// ============================================================================
// DEVTOOLS
// ============================================================================

/**
 * DevTools - Outils de développement (uniquement en développement).
 */
function DevTools() {
  if (process.env.NODE_ENV !== 'development') {
    return null;
  }

  return (
    <>
      {/* React Query DevTools */}
      <ReactQueryDevtools
        initialIsOpen={false}
        buttonPosition="bottom-left"
      />

      {/* Indicateur de mode développement */}
      <div className="fixed bottom-4 left-4 z-[9999] px-2 py-1 rounded bg-yellow-500/20 border border-yellow-500/30 text-yellow-400 text-[10px] font-mono font-bold">
        DEV
      </div>
    </>
  );
}

// ============================================================================
// COMPOSANT PRINCIPAL
// ============================================================================

/**
 * Providers - Encapsule l'application avec tous les providers nécessaires.
 *
 * @param props - Props du composant
 * @returns Élément JSX des providers
 *
 * @example
 * ```tsx
 * // Dans layout.tsx
 * <Providers>
 *   {children}
 * </Providers>
 * ```
 */
export function Providers({ children }: ProvidersProps) {
  // Instance du QueryClient (stable entre les renders)
  const [queryClient] = useState(() => getQueryClient());

  // État d'hydratation (pour éviter les mismatches SSR/client)
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <LanguageProvider>
            <WebSocketProvider>
              {/* Contenu de l'application */}
              {children}

              {/* DevTools (uniquement en développement) */}
              <DevTools />
            </WebSocketProvider>
          </LanguageProvider>
        </ThemeProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}

// ============================================================================
// EXPORTS
// ============================================================================

export {
  ErrorBoundary,
  ThemeProvider,
  LanguageProvider,
  WebSocketProvider,
  DevTools,
  makeQueryClient,
  getQueryClient,
};

export type { ProvidersProps, ErrorBoundaryState };
