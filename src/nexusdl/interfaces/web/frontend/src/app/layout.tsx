/**
 * Layout racine pour l'application NexusDL.
 *
 * Structure globale de l'application avec :
 *   - Metadata SEO (OpenGraph, Twitter Cards, manifest)
 *   - Providers (React Query, Toaster, Theme)
 *   - Layout principal (Sidebar + contenu)
 *   - Bandeau de connexion WebSocket
 *   - Écouteurs WebSocket globaux
 *   - Style cyberpunk néon cohérent
 *   - Accessibilité complète
 *   - Responsive (mobile/desktop)
 *
 * @module app/layout
 */

import type { Metadata, Viewport } from 'next';
import { Inter, JetBrains_Mono } from 'next/font/google';
import { Toaster } from 'sonner';

import './globals.css';

import { Providers } from './providers';
import { Sidebar, SidebarProvider, MobileSidebarDrawer } from '@/components/Sidebar';
import { ThemeToggle } from '@/components/ThemeToggle';
import { ConnectionBanner } from '@/components/ConnectionBanner';
import { useAutoConnect, useDownloadWebSocket, useLibraryWebSocket, useNotificationWebSocket } from '@/hooks/useWebSocket';

import { APP_NAME, APP_DESCRIPTION, APP_URL } from '@/lib/constants';

// ============================================================================
// FONTS
// ============================================================================

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-sans',
  display: 'swap',
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-mono',
  display: 'swap',
});

// ============================================================================
// METADATA
// ============================================================================

export const metadata: Metadata = {
  title: {
    default: APP_NAME,
    template: `%s | ${APP_NAME}`,
  },
  description: APP_DESCRIPTION,
  keywords: [
    'manga',
    'downloader',
    'library',
    'manager',
    'webtoon',
    'comics',
    'reader',
    'cyberpunk',
    'nexusdl',
  ],
  authors: [{ name: 'NexusDL Team' }],
  creator: 'NexusDL',
  publisher: 'NexusDL',
  applicationName: APP_NAME,
  referrer: 'strict-origin-when-cross-origin',
  category: 'entertainment',
  classification: 'manga reader',

  metadataBase: new URL(APP_URL),
  alternates: {
    canonical: '/',
    languages: {
      'en-US': '/en',
      'fr-FR': '/fr',
      'es-ES': '/es',
      'ja-JP': '/ja',
    },
  },

  openGraph: {
    type: 'website',
    locale: 'en_US',
    url: APP_URL,
    siteName: APP_NAME,
    title: APP_NAME,
    description: APP_DESCRIPTION,
    images: [
      {
        url: '/og-image.png',
        width: 1200,
        height: 630,
        alt: `${APP_NAME} - Your manga, your library, your way`,
      },
    ],
  },

  twitter: {
    card: 'summary_large_image',
    title: APP_NAME,
    description: APP_DESCRIPTION,
    images: ['/twitter-card.png'],
    creator: '@nexusdl',
  },

  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      'max-video-preview': -1,
      'max-image-preview': 'large',
      'max-snippet': -1,
    },
  },

  icons: {
    icon: [
      { url: '/favicon.svg', type: 'image/svg+xml' },
      { url: '/icons/favicon-16x16.png', sizes: '16x16', type: 'image/png' },
      { url: '/icons/favicon-32x32.png', sizes: '32x32', type: 'image/png' },
    ],
    apple: [
      { url: '/icons/apple-touch-icon.png', sizes: '180x180', type: 'image/png' },
    ],
    other: [
      {
        rel: 'mask-icon',
        url: '/icons/safari-pinned-tab.svg',
        color: '#00ff41',
      },
    ],
  },

  manifest: '/manifest.json',

  verification: {
    google: 'your-google-verification-code',
    yandex: 'your-yandex-verification-code',
  },

  other: {
    'theme-color': '#000000',
    'msapplication-TileColor': '#000000',
    'msapplication-config': '/browserconfig.xml',
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#ffffff' },
    { media: '(prefers-color-scheme: dark)', color: '#000000' },
  ],
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
  userScalable: true,
  viewportFit: 'cover',
};

// ============================================================================
// COMPOSANT GLOBAL WEBSOCKET LISTENERS
// ============================================================================

/**
 * Composant qui initialise les écouteurs WebSocket globaux.
 */
function GlobalWebSocketListeners() {
  // Connexion automatique
  useAutoConnect({
    autoConnect: true,
    disconnectOnUnmount: false,
  });

  // Écouter les événements de téléchargement
  useDownloadWebSocket({
    enableToasts: true,
  });

  // Écouter les événements de bibliothèque
  useLibraryWebSocket({
    enableToasts: true,
  });

  // Écouter les notifications
  useNotificationWebSocket({
    autoShowToasts: true,
    toastDuration: 5000,
    enableSounds: false,
  });

  return null;
}

// ============================================================================
// LAYOUT PRINCIPAL
// ============================================================================

/**
 * RootLayout - Layout racine de l'application.
 *
 * @param props - Props du layout
 * @returns Élément JSX du layout
 */
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${jetbrainsMono.variable}`}
      suppressHydrationWarning
    >
      <head>
        {/* Preconnect pour les ressources externes */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        
        {/* DNS prefetch */}
        <link rel="dns-prefetch" href="//api.nexusdl.dev" />
        <link rel="dns-prefetch" href="//ws.nexusdl.dev" />
        
        {/* Fallback pour les navigateurs sans support SVG */}
        <link rel="icon" href="/favicon.ico" sizes="any" />
      </head>
      <body className="min-h-screen bg-background text-text font-sans antialiased transition-theme">
        <Providers>
          <SidebarProvider defaultCollapsed={false}>
            {/* Écouteurs WebSocket globaux */}
            <GlobalWebSocketListeners />

            {/* Bandeau de statut de connexion */}
            <ConnectionBanner />

            {/* Layout principal */}
            <div className="flex min-h-screen">
              {/* Sidebar desktop */}
              <div className="hidden lg:block">
                <Sidebar />
              </div>

              {/* Sidebar mobile (drawer) */}
              <MobileSidebarDrawer />

              {/* Contenu principal */}
              <main className="flex-1 flex flex-col min-w-0">
                {/* Header mobile avec toggle menu et theme */}
                <header className="lg:hidden sticky top-0 z-30 flex items-center justify-between gap-3 px-4 py-3 bg-surface/95 backdrop-blur-md border-b-2 border-border-dim">
                  <div className="flex items-center gap-2">
                    <div className="h-8 w-8 rounded-md bg-primary-bg border border-primary/40 flex items-center justify-center">
                      <span className="text-primary font-mono font-bold text-sm">N</span>
                    </div>
                    <span className="font-mono font-bold text-primary text-sm">
                      NexusDL
                    </span>
                  </div>

                  <div className="flex items-center gap-2">
                    <ThemeToggle compact size="sm" />
                  </div>
                </header>

                {/* Header desktop avec theme toggle */}
                <header className="hidden lg:flex items-center justify-end gap-3 px-6 py-3 bg-surface/50 border-b border-border-dim">
                  <ThemeToggle />
                </header>

                {/* Contenu de la page */}
                <div className="flex-1 overflow-auto">
                  {children}
                </div>

                {/* Footer */}
                <footer className="border-t border-border-dim bg-surface/50 px-6 py-4">
                  <div className="flex items-center justify-between text-xs font-mono text-text-dim">
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
                      <a
                        href="/docs"
                        className="hover:text-secondary transition-colors"
                      >
                        API Docs
                      </a>
                    </div>

                    <div className="flex items-center gap-2">
                      <span>v0.1.0</span>
                      <span className="text-text-dim">•</span>
                      <span className="flex items-center gap-1">
                        <span className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />
                        <span>Online</span>
                      </span>
                    </div>
                  </div>
                </footer>
              </main>
            </div>

            {/* Toast notifications */}
            <Toaster
              position="bottom-right"
              toastOptions={{
                style: {
                  background: 'var(--nx-surface)',
                  border: '2px solid var(--nx-border-dim)',
                  color: 'var(--nx-text)',
                  fontFamily: 'var(--nx-font-mono)',
                  fontSize: '0.875rem',
                  borderRadius: '0.75rem',
                  boxShadow: '0 10px 15px rgba(0, 0, 0, 0.3)',
                },
                className: 'nx-toast',
                success: {
                  style: {
                    borderColor: 'rgba(34, 197, 94, 0.3)',
                  },
                  iconTheme: {
                    primary: '#22c55e',
                    secondary: 'var(--nx-surface)',
                  },
                },
                error: {
                  style: {
                    borderColor: 'rgba(239, 68, 68, 0.3)',
                  },
                  iconTheme: {
                    primary: '#ef4444',
                    secondary: 'var(--nx-surface)',
                  },
                },
                warning: {
                  style: {
                    borderColor: 'rgba(234, 179, 8, 0.3)',
                  },
                  iconTheme: {
                    primary: '#eab308',
                    secondary: 'var(--nx-surface)',
                  },
                },
                info: {
                  style: {
                    borderColor: 'rgba(6, 182, 212, 0.3)',
                  },
                  iconTheme: {
                    primary: '#06b6d4',
                    secondary: 'var(--nx-surface)',
                  },
                },
              }}
              closeButton
              richColors
              expand
              duration={5000}
            />
          </SidebarProvider>
        </Providers>
      </body>
    </html>
  );
}
