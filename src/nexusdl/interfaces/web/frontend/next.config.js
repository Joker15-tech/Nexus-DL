/**
 * Configuration Next.js pour NexusDL Frontend.
 *
 * Ce fichier configure l'application Next.js avec :
 *   - Optimisations de performance (images, fonts, compression)
 *   - Sécurité (CSP, headers, CORS)
 *   - Internationalisation (i18n)
 *   - Proxy API vers le backend FastAPI
 *   - WebSocket proxy
 *   - PWA support
 *   - Configuration Docker (standalone output)
 *   - Bundle analyzer (dev only)
 *
 * @module next.config
 */

const path = require('path');
const withBundleAnalyzer = require('@next/bundle-analyzer')({
  enabled: process.env.ANALYZE === 'true',
});

/** @type {import('next').NextConfig} */
const nextConfig = {
  // ============================================================================
  // CONFIGURATION DE BASE
  // ============================================================================

  /**
   * Active React Strict Mode pour détecter les problèmes potentiels.
   */
  reactStrictMode: true,

  /**
   * Active SWC minification (plus rapide que Terser).
   */
  swcMinify: true,

  /**
   * Mode de sortie pour Docker.
   * 'standalone' crée un build autonome avec toutes les dépendances.
   */
  output: process.env.DOCKER === 'true' ? 'standalone' : undefined,

  /**
   * Désactive le header X-Powered-By pour la sécurité.
   */
  poweredByHeader: false,

  /**
   * Génère des ETags pour le cache.
   */
  generateEtags: true,

  /**
   * Active le trailing slash pour les URLs.
   */
  trailingSlash: false,

  /**
   * Désactive le header Server.
   */
  compress: true,

  // ============================================================================
  // IMAGES
  // ============================================================================

  /**
   * Configuration de l'optimisation des images.
   */
  images: {
    /**
     * Formats d'image supportés.
     */
    formats: ['image/avif', 'image/webp'],

    /**
     * Domaines autorisés pour les images externes.
     */
    domains: [
      'localhost',
      'mangadex.org',
      'api.mangadex.org',
      'uploads.mangadex.org',
      'asuracomic.net',
      'flamecomics.com',
      'reaperscans.com',
      'manganato.com',
      'mangakakalot.com',
      'cdn.statically.io',
      // Ajouter d'autres domaines de sources manga ici
    ],

    /**
     * Tailles d'image optimisées.
     */
    deviceSizes: [640, 750, 828, 1080, 1200, 1920, 2048, 3840],
    imageSizes: [16, 32, 48, 64, 96, 128, 256, 384],

    /**
     * Qualité des images optimisées.
     */
    quality: 85,

    /**
     * Taille minimum pour l'optimisation.
     */
    minimumCacheTTL: 60 * 60 * 24 * 30, // 30 jours

    /**
     * Active le lazy loading par défaut.
     */
    loader: 'default',
  },

  // ============================================================================
  // HEADERS DE SÉCURITÉ
  // ============================================================================

  /**
   * Headers HTTP personnalisés.
   */
  async headers() {
    return [
      {
        /**
         * Appliquer à toutes les routes.
         */
        source: '/(.*)',
        headers: [
          // Sécurité
          {
            key: 'X-Frame-Options',
            value: 'DENY',
          },
          {
            key: 'X-Content-Type-Options',
            value: 'nosniff',
          },
          {
            key: 'X-XSS-Protection',
            value: '1; mode=block',
          },
          {
            key: 'Referrer-Policy',
            value: 'strict-origin-when-cross-origin',
          },
          {
            key: 'Permissions-Policy',
            value: 'camera=(), microphone=(), geolocation=()',
          },
          {
            key: 'Strict-Transport-Security',
            value: 'max-age=31536000; includeSubDomains',
          },

          // Cache
          {
            key: 'Cache-Control',
            value: 'public, max-age=3600, s-maxage=86400',
          },

          // CSP (Content Security Policy)
          {
            key: 'Content-Security-Policy',
            value: `
              default-src 'self';
              script-src 'self' 'unsafe-eval' 'unsafe-inline' https://fonts.googleapis.com;
              style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;
              img-src 'self' data: blob: https:;
              font-src 'self' https://fonts.gstatic.com;
              connect-src 'self' ws: wss: http://localhost:8000 https://api.nexusdl.dev;
              media-src 'self';
              object-src 'none';
              frame-src 'none';
              worker-src 'self' blob:;
              manifest-src 'self';
            `.replace(/\s+/g, ' ').trim(),
          },
        ],
      },
      {
        /**
         * Headers spécifiques pour les assets statiques.
         */
        source: '/static/(.*)',
        headers: [
          {
            key: 'Cache-Control',
            value: 'public, max-age=31536000, immutable',
          },
        ],
      },
      {
        /**
         * Headers pour les API routes.
         */
        source: '/api/(.*)',
        headers: [
          {
            key: 'Cache-Control',
            value: 'no-store, must-revalidate',
          },
        ],
      },
    ];
  },

  // ============================================================================
  // REDIRECTS
  // ============================================================================

  /**
   * Redirections HTTP.
   */
  async redirects() {
    return [
      {
        /**
         * Rediriger /home vers /.
         */
        source: '/home',
        destination: '/',
        permanent: true,
      },
      {
        /**
         * Rediriger /dashboard vers /.
         */
        source: '/dashboard',
        destination: '/',
        permanent: true,
      },
      {
        /**
         * Rediriger les anciennes URLs de manga.
         */
        source: '/manga/:id',
        has: [
          {
            type: 'query',
            key: 'site',
          },
        ],
        destination: '/manga/:id?site=:site',
        permanent: false,
      },
    ];
  },

  // ============================================================================
  // REWRITES (PROXY API)
  // ============================================================================

  /**
   * Réécritures d'URL (proxy vers le backend).
   */
  async rewrites() {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

    return [
      {
        /**
         * Proxy pour l'API REST.
         */
        source: '/api/:path*',
        destination: `${apiUrl}/api/v1/:path*`,
      },
      {
        /**
         * Proxy pour les endpoints de santé.
         */
        source: '/health/:path*',
        destination: `${apiUrl}/health/:path*`,
      },
      {
        /**
         * Proxy pour WebSocket.
         */
        source: '/ws',
        destination: `${apiUrl.replace('http', 'ws')}/ws`,
      },
      {
        /**
         * Proxy pour les fichiers statiques du backend.
         */
        source: '/backend-static/:path*',
        destination: `${apiUrl}/static/:path*`,
      },
    ];
  },

  // ============================================================================
  // INTERNATIONALISATION (i18n)
  // ============================================================================

  /**
   * Configuration i18n.
   */
  i18n: {
    /**
     * Langues supportées.
     */
    locales: ['en', 'fr', 'es', 'de', 'ja', 'ko', 'zh'],

    /**
     * Langue par défaut.
     */
    defaultLocale: 'en',

    /**
     * Détection automatique de la langue.
     */
    localeDetection: true,
  },

  // ============================================================================
  // WEBPACK CUSTOMIZATION
  // ============================================================================

  /**
   * Configuration Webpack personnalisée.
   */
  webpack: (config, { dev, isServer }) => {
    // Alias pour les imports absolus
    config.resolve.alias = {
      ...config.resolve.alias,
      '@': path.resolve(__dirname, 'src'),
      '@components': path.resolve(__dirname, 'src/components'),
      '@hooks': path.resolve(__dirname, 'src/hooks'),
      '@store': path.resolve(__dirname, 'src/store'),
      '@lib': path.resolve(__dirname, 'src/lib'),
      '@types': path.resolve(__dirname, 'src/types'),
    };

    // Optimisations pour le développement
    if (dev) {
      config.devtool = 'source-map';
    }

    // Optimisations pour la production
    if (!dev && !isServer) {
      // Split chunks pour optimiser le bundle
      config.optimization.splitChunks = {
        chunks: 'all',
        minSize: 20000,
        maxSize: 244000,
        minChunks: 1,
        maxAsyncRequests: 30,
        maxInitialRequests: 30,
        enforceSizeThreshold: 50000,
        cacheGroups: {
          defaultVendors: {
            test: /[\\/]node_modules[\\/]/,
            priority: -10,
            reuseExistingChunk: true,
          },
          default: {
            minChunks: 2,
            priority: -20,
            reuseExistingChunk: true,
          },
          // Séparer les gros packages
          react: {
            test: /[\\/]node_modules[\\/](react|react-dom)[\\/]/,
            name: 'react',
            chunks: 'all',
            priority: 10,
          },
          lucide: {
            test: /[\\/]node_modules[\\/]lucide-react[\\/]/,
            name: 'lucide',
            chunks: 'all',
            priority: 10,
          },
          zustand: {
            test: /[\\/]node_modules[\\/]zustand[\\/]/,
            name: 'zustand',
            chunks: 'all',
            priority: 10,
          },
        },
      };
    }

    return config;
  },

  // ============================================================================
  // EXPERIMENTAL FEATURES
  // ============================================================================

  /**
   * Fonctionnalités expérimentales.
   */
  experimental: {
    /**
     * Optimisation des fonts Google.
     */
    optimizePackageImports: ['lucide-react', '@heroicons/react'],

    /**
     * Server Actions (si utilisé).
     */
    serverActions: {
      enabled: false,
    },

    /**
     * Turbo pack (Next.js 13+).
     */
    turbo: {
      rules: {
        // Règles personnalisées pour Turbo
      },
    },
  },

  // ============================================================================
  // ESLINT
  // ============================================================================

  /**
   * Configuration ESLint.
   */
  eslint: {
    /**
     * Ignorer les erreurs ESLint pendant le build.
     */
    ignoreDuringBuilds: process.env.NODE_ENV === 'production',
  },

  // ============================================================================
  // TYPESCRIPT
  // ============================================================================

  /**
   * Configuration TypeScript.
   */
  typescript: {
    /**
     * Ignorer les erreurs TypeScript pendant le build.
     */
    ignoreBuildErrors: false,
  },

  // ============================================================================
  // ENVIRONMENT VARIABLES
  // ============================================================================

  /**
   * Variables d'environnement exposées au client.
   */
  env: {
    NEXT_PUBLIC_APP_NAME: process.env.NEXT_PUBLIC_APP_NAME || 'NexusDL',
    NEXT_PUBLIC_APP_VERSION: process.env.NEXT_PUBLIC_APP_VERSION || '0.1.0',
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000',
    NEXT_PUBLIC_WS_URL: process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000/ws',
  },
};

// ============================================================================
// EXPORT
// ============================================================================

/**
 * Export avec Bundle Analyzer (si activé).
 */
module.exports = withBundleAnalyzer(nextConfig);
