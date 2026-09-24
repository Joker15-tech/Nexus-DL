/**
 * Tailwind CSS Configuration for NexusDL Frontend.
 *
 * Configuration complète du framework Tailwind CSS pour l'interface
 * cyberpunk néon de NexusDL. Cette configuration définit :
 *
 *   - Le système de couleurs basé sur les variables CSS (--nx-*)
 *   - Les 4 thèmes (cyberpunk, dark, light, system)
 *   - Les polices (JetBrains Mono + Inter)
 *   - Les animations et keyframes custom
 *   - Les effets de glow néon
 *   - Les plugins officiels (forms, typography, aspect-ratio)
 *   - Les variants et utilities custom
 *
 * Architecture des couleurs :
 *   globals.css (variables CSS)
 *     ↓
 *   tailwind.config.js (mapping vers classes)
 *     ↓
 *   Composants React (utilisation des classes)
 *
 * Exemple d'utilisation :
 *   <div className="bg-primary text-background border-2 border-border-dim">
 *     <span className="text-glow">NexusDL</span>
 *   </div>
 *
 * Thèmes supportés :
 *   - cyberpunk (default) : Vert néon #00ff41
 *   - dark                : Vert classique #22c55e
 *   - light               : Vert sombre #16a34a
 *   - system              : Auto-détection OS
 *
 * Compatibilité :
 *   - Tailwind CSS 3.4+
 *   - Next.js 14+
 *   - PostCSS 8.4+
 *
 * @see https://tailwindcss.com/docs/configuration
 * @see https://tailwindcss.com/docs/customizing-colors
 * @see https://tailwindcss.com/docs/animation
 *
 * @module tailwind.config
 * @license GPL-3.0-only
 */

const plugin = require('tailwindcss/plugin');

/** @type {import('tailwindcss').Config} */
module.exports = {
  // ===========================================================================
  // DARK MODE
  // ===========================================================================
  // Utilise la classe CSS plutôt que la media query pour supporter
  // les 4 thèmes (cyberpunk, dark, light, system).
  // Le thème est appliqué via <html class="theme-xxx">.
  darkMode: 'class',

  // ===========================================================================
  // CONTENT PATHS
  // ===========================================================================
  // Fichiers à scanner pour détecter les classes Tailwind utilisées.
  // Le tree-shaking ne garde que les classes effectivement utilisées.
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
    './src/hooks/**/*.{js,ts,jsx,tsx,mdx}',
    './src/lib/**/*.{js,ts,jsx,tsx,mdx}',
    // Fichiers de documentation (Storybook)
    './.storybook/**/*.{js,ts,jsx,tsx,mdx}',
  ],

  // ===========================================================================
  // SAFELIST
  // ===========================================================================
  // Classes générées dynamiquement qui doivent être conservées
  // même si elles ne sont pas détectées dans le code source.
  safelist: [
    // Couleurs dynamiques pour les badges de statut
    {
      pattern: /(bg|text|border)-(primary|secondary|accent|success|warning|error|info)(-dim|-bright)?/,
      variants: ['hover', 'focus', 'dark'],
    },
    // Classes de glow
    {
      pattern: /shadow-(sm|md|lg|glow)/,
    },
    // Classes d'animation
    {
      pattern: /animate-(fade-in|slide-in|zoom-in|glow-pulse|neon-flicker|shimmer|float|blink|gradient)/,
    },
    // Thèmes
    {
      pattern: /theme-(cyberpunk|dark|light|system)/,
    },
  ],

  // ===========================================================================
  // THEME
  // ===========================================================================
  theme: {
    // -------------------------------------------------------------------------
    // CONTAINER
    // -------------------------------------------------------------------------
    container: {
      center: true,
      padding: {
        DEFAULT: '1rem',
        sm: '1.5rem',
        lg: '2rem',
        xl: '3rem',
        '2xl': '4rem',
      },
      screens: {
        sm: '640px',
        md: '768px',
        lg: '1024px',
        xl: '1280px',
        '2xl': '1536px',
      },
    },

    // -------------------------------------------------------------------------
    // EXTEND
    // -------------------------------------------------------------------------
    // Extensions du thème par défaut de Tailwind.
    extend: {
      // =====================================================================
      // COLORS
      // =====================================================================
      // Mapping des couleurs vers les variables CSS définies dans globals.css.
      // Cela permet de changer de thème en modifiant simplement les variables.
      colors: {
        // -----------------------------------------------------------------
        // Primary (Vert néon)
        // -----------------------------------------------------------------
        primary: {
          DEFAULT: 'var(--nx-primary)',
          dim: 'var(--nx-primary-dim)',
          bright: 'var(--nx-primary-bright)',
          bg: 'var(--nx-primary-bg)',
          glow: 'var(--nx-primary-glow)',
        },

        // -----------------------------------------------------------------
        // Secondary (Cyan)
        // -----------------------------------------------------------------
        secondary: {
          DEFAULT: 'var(--nx-secondary)',
          dim: 'var(--nx-secondary-dim)',
          bg: 'var(--nx-secondary-bg)',
          glow: 'var(--nx-secondary-glow)',
        },

        // -----------------------------------------------------------------
        // Accent (Magenta)
        // -----------------------------------------------------------------
        accent: {
          DEFAULT: 'var(--nx-accent)',
          dim: 'var(--nx-accent-dim)',
          bg: 'var(--nx-accent-bg)',
          glow: 'var(--nx-accent-glow)',
        },

        // -----------------------------------------------------------------
        // Semantic colors (statuts)
        // -----------------------------------------------------------------
        success: 'var(--nx-success)',
        warning: 'var(--nx-warning)',
        error: 'var(--nx-error)',
        info: 'var(--nx-info)',

        // -----------------------------------------------------------------
        // Backgrounds
        // -----------------------------------------------------------------
        background: 'var(--nx-background)',
        surface: {
          DEFAULT: 'var(--nx-surface)',
          alt: 'var(--nx-surface-alt)',
          hover: 'var(--nx-surface-hover)',
        },

        // -----------------------------------------------------------------
        // Text
        // -----------------------------------------------------------------
        text: {
          DEFAULT: 'var(--nx-text)',
          bright: 'var(--nx-text-bright)',
          muted: 'var(--nx-text-muted)',
          dim: 'var(--nx-text-dim)',
        },

        // -----------------------------------------------------------------
        // Borders
        // -----------------------------------------------------------------
        border: {
          DEFAULT: 'var(--nx-border)',
          dim: 'var(--nx-border-dim)',
        },

        // -----------------------------------------------------------------
        // Override des couleurs Tailwind par défaut
        // -----------------------------------------------------------------
        // On redéfinit quelques couleurs Tailwind pour qu'elles
        // correspondent au thème cyberpunk.
        inherit: 'inherit',
        current: 'currentColor',
        transparent: 'transparent',
        black: '#000000',
        white: '#ffffff',

        // Gris custom pour le thème cyberpunk
        gray: {
          50: '#f9fafb',
          100: '#f3f4f6',
          200: '#e5e7eb',
          300: '#d1d5db',
          400: '#9ca3af',
          500: '#6b7280',
          600: '#4b5563',
          700: '#374151',
          800: '#1f2937',
          900: '#111827',
          950: '#030712',
        },
      },

      // =====================================================================
      // FONT FAMILY
      // =====================================================================
      // Polices définies dans layout.tsx via next/font/google.
      // Les variables CSS sont injectées dans <html>.
      fontFamily: {
        // Police sans-serif (texte général)
        sans: [
          'var(--font-sans)',
          'system-ui',
          '-apple-system',
          'BlinkMacSystemFont',
          '"Segoe UI"',
          'Roboto',
          '"Helvetica Neue"',
          'Arial',
          '"Noto Sans"',
          'sans-serif',
          '"Apple Color Emoji"',
          '"Segoe UI Emoji"',
          '"Segoe UI Symbol"',
          '"Noto Color Emoji"',
        ],
        // Police monospace (code, données, UI cyberpunk)
        mono: [
          'var(--font-mono)',
          '"JetBrains Mono"',
          '"Fira Code"',
          '"SF Mono"',
          'Monaco',
          'Inconsolata',
          '"Roboto Mono"',
          '"Source Code Pro"',
          'Menlo',
          'Consolas',
          'monospace',
        ],
      },

      // =====================================================================
      // FONT SIZE
      // =====================================================================
      // Tailles de police custom pour le thème cyberpunk.
      fontSize: {
        '2xs': ['0.625rem', { lineHeight: '0.875rem' }], // 10px
        '3xs': ['0.5rem', { lineHeight: '0.75rem' }], // 8px
      },

      // =====================================================================
      // BORDER RADIUS
      // =====================================================================
      // Rayons de bordure basés sur les variables CSS.
      borderRadius: {
        sm: 'var(--nx-radius-sm)',
        md: 'var(--nx-radius-md)',
        lg: 'var(--nx-radius-lg)',
        xl: 'var(--nx-radius-xl)',
        '2xl': '1.5rem',
        '3xl': '2rem',
      },

      // =====================================================================
      // BOX SHADOW
      // =====================================================================
      // Ombres custom avec effets de glow néon.
      boxShadow: {
        sm: 'var(--nx-shadow-sm)',
        md: 'var(--nx-shadow-md)',
        lg: 'var(--nx-shadow-lg)',
        glow: 'var(--nx-shadow-glow)',
        // Glow colors spécifiques
        'glow-primary': '0 0 20px var(--nx-primary-glow)',
        'glow-secondary': '0 0 20px var(--nx-secondary-glow)',
        'glow-accent': '0 0 20px var(--nx-accent-glow)',
        // Glow intense (hover)
        'glow-primary-lg': '0 0 30px var(--nx-primary-glow), 0 0 60px var(--nx-primary-glow)',
        'glow-secondary-lg': '0 0 30px var(--nx-secondary-glow), 0 0 60px var(--nx-secondary-glow)',
        'glow-accent-lg': '0 0 30px var(--nx-accent-glow), 0 0 60px var(--nx-accent-glow)',
        // Inner glow
        'inner-glow': 'inset 0 0 20px var(--nx-primary-glow)',
        'inner-glow-secondary': 'inset 0 0 20px var(--nx-secondary-glow)',
      },

      // =====================================================================
      // ANIMATIONS & KEYFRAMES
      // =====================================================================
      // Animations custom pour le thème cyberpunk néon.
      animation: {
        // Apparitions
        'fade-in': 'nx-fade-in 0.2s ease-out',
        'fade-in-slow': 'nx-fade-in 0.5s ease-out',
        'fade-out': 'nx-fade-out 0.2s ease-out',

        // Slides
        'slide-in-top': 'nx-slide-in-top 0.2s ease-out',
        'slide-in-bottom': 'nx-slide-in-bottom 0.2s ease-out',
        'slide-in-left': 'nx-slide-in-left 0.2s ease-out',
        'slide-in-right': 'nx-slide-in-right 0.2s ease-out',

        // Zoom
        'zoom-in': 'nx-zoom-in 0.2s ease-out',
        'zoom-out': 'nx-zoom-out 0.2s ease-out',

        // Effets néon
        'glow-pulse': 'nx-glow-pulse 2s ease-in-out infinite',
        'neon-flicker': 'nx-neon-flicker 4s linear infinite',
        'scanline': 'nx-scanline 8s linear infinite',

        // Shimmer (skeleton loader)
        shimmer: 'nx-shimmer 1.5s ease-in-out infinite',
        'shimmer-slow': 'nx-shimmer 2.5s ease-in-out infinite',

        // Progress
        'progress-stripes': 'nx-progress-stripes 1s linear infinite',
        'progress-shimmer': 'nx-progress-shimmer 1.5s ease-in-out infinite',

        // Spin
        spin: 'nx-spin 1s linear infinite',
        'spin-slow': 'nx-spin 3s linear infinite',

        // Bounce
        bounce: 'nx-bounce 1s infinite',
        'bounce-slow': 'nx-bounce 2s infinite',

        // Ping (notification)
        ping: 'nx-ping 1s cubic-bezier(0, 0, 0.2, 1) infinite',

        // Float
        float: 'nx-float 3s ease-in-out infinite',
        'float-slow': 'nx-float 5s ease-in-out infinite',

        // Blink (cursor)
        blink: 'nx-blink 1s step-end infinite',

        // Gradient
        gradient: 'nx-gradient-shift 3s ease infinite',
        'gradient-slow': 'nx-gradient-shift 6s ease infinite',
      },

      keyframes: {
        // =================================================================
        // Fade
        // =================================================================
        'nx-fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'nx-fade-out': {
          from: { opacity: '1' },
          to: { opacity: '0' },
        },

        // =================================================================
        // Slide
        // =================================================================
        'nx-slide-in-top': {
          from: {
            opacity: '0',
            transform: 'translateY(-8px)',
          },
          to: {
            opacity: '1',
            transform: 'translateY(0)',
          },
        },
        'nx-slide-in-bottom': {
          from: {
            opacity: '0',
            transform: 'translateY(8px)',
          },
          to: {
            opacity: '1',
            transform: 'translateY(0)',
          },
        },
        'nx-slide-in-left': {
          from: {
            opacity: '0',
            transform: 'translateX(-8px)',
          },
          to: {
            opacity: '1',
            transform: 'translateX(0)',
          },
        },
        'nx-slide-in-right': {
          from: {
            opacity: '0',
            transform: 'translateX(8px)',
          },
          to: {
            opacity: '1',
            transform: 'translateX(0)',
          },
        },

        // =================================================================
        // Zoom
        // =================================================================
        'nx-zoom-in': {
          from: {
            opacity: '0',
            transform: 'scale(0.95)',
          },
          to: {
            opacity: '1',
            transform: 'scale(1)',
          },
        },
        'nx-zoom-out': {
          from: {
            opacity: '1',
            transform: 'scale(1)',
          },
          to: {
            opacity: '0',
            transform: 'scale(0.95)',
          },
        },

        // =================================================================
        // Glow Pulse (néon)
        // =================================================================
        'nx-glow-pulse': {
          '0%, 100%': {
            boxShadow: '0 0 5px var(--nx-primary-glow)',
          },
          '50%': {
            boxShadow:
              '0 0 20px var(--nx-primary-glow), 0 0 40px var(--nx-primary-glow)',
          },
        },

        // =================================================================
        // Neon Flicker (CRT effect)
        // =================================================================
        'nx-neon-flicker': {
          '0%, 19%, 21%, 23%, 25%, 54%, 56%, 100%': {
            textShadow:
              '0 0 4px var(--nx-primary), 0 0 11px var(--nx-primary), 0 0 19px var(--nx-primary), 0 0 40px var(--nx-primary)',
            opacity: '1',
          },
          '20%, 24%, 55%': {
            textShadow: 'none',
            opacity: '0.8',
          },
        },

        // =================================================================
        // Scanline (CRT effect)
        // =================================================================
        'nx-scanline': {
          '0%': { transform: 'translateY(-100%)' },
          '100%': { transform: 'translateY(100vh)' },
        },

        // =================================================================
        // Shimmer (skeleton loader)
        // =================================================================
        'nx-shimmer': {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },

        // =================================================================
        // Progress
        // =================================================================
        'nx-progress-stripes': {
          '0%': { backgroundPosition: '1rem 0' },
          '100%': { backgroundPosition: '0 0' },
        },
        'nx-progress-shimmer': {
          '0%': { backgroundPosition: '200% 0' },
          '100%': { backgroundPosition: '-200% 0' },
        },

        // =================================================================
        // Spin
        // =================================================================
        'nx-spin': {
          from: { transform: 'rotate(0deg)' },
          to: { transform: 'rotate(360deg)' },
        },

        // =================================================================
        // Bounce
        // =================================================================
        'nx-bounce': {
          '0%, 100%': {
            transform: 'translateY(0)',
            animationTimingFunction: 'cubic-bezier(0.8, 0, 1, 1)',
          },
          '50%': {
            transform: 'translateY(-4px)',
            animationTimingFunction: 'cubic-bezier(0, 0, 0.2, 1)',
          },
        },

        // =================================================================
        // Ping (notification dot)
        // =================================================================
        'nx-ping': {
          '75%, 100%': {
            transform: 'scale(2)',
            opacity: '0',
          },
        },

        // =================================================================
        // Float
        // =================================================================
        'nx-float': {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-6px)' },
        },

        // =================================================================
        // Blink (cursor)
        // =================================================================
        'nx-blink': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0' },
        },

        // =================================================================
        // Gradient shift
        // =================================================================
        'nx-gradient-shift': {
          '0%': { backgroundPosition: '0% 50%' },
          '50%': { backgroundPosition: '100% 50%' },
          '100%': { backgroundPosition: '0% 50%' },
        },
      },

      // =====================================================================
      // TRANSITION
      // =====================================================================
      // Durées de transition basées sur les variables CSS.
      transitionDuration: {
        fast: 'var(--nx-transition-fast)',
        normal: 'var(--nx-transition-normal)',
        slow: 'var(--nx-transition-slow)',
        '2000': '2000ms',
        '3000': '3000ms',
      },

      transitionTimingFunction: {
        'bounce-in': 'cubic-bezier(0.68, -0.55, 0.265, 1.55)',
        'bounce-out': 'cubic-bezier(0.4, 1.4, 0.6, 1)',
        neon: 'cubic-bezier(0.4, 0, 0.2, 1)',
      },

      // =====================================================================
      // Z-INDEX
      // =====================================================================
      // Niveaux z-index basés sur les variables CSS.
      zIndex: {
        dropdown: 'var(--nx-z-dropdown)',
        sticky: '30',
        modal: 'var(--nx-z-modal)',
        popover: '40',
        tooltip: 'var(--nx-z-tooltip)',
        toast: 'var(--nx-z-toast)',
        overlay: '45',
        max: '9999',
      },

      // =====================================================================
      // SPACING
      // =====================================================================
      // Espacements custom pour le layout.
      spacing: {
        '18': '4.5rem',
        '88': '22rem',
        '100': '25rem',
        '112': '28rem',
        '128': '32rem',
        '144': '36rem',
      },

      // =====================================================================
      // WIDTH / HEIGHT
      // =====================================================================
      // Tailles custom pour les composants.
      width: {
        'sidebar-expanded': '260px',
        'sidebar-collapsed': '72px',
        'card-sm': '200px',
        'card-md': '280px',
        'card-lg': '360px',
      },

      height: {
        'screen-90': '90vh',
        'screen-80': '80vh',
        'screen-70': '70vh',
        'header': '64px',
        'footer': '80px',
      },

      // =====================================================================
      // MAX WIDTH
      // =====================================================================
      maxWidth: {
        '8xl': '88rem',
        '9xl': '96rem',
        content: 'max-content',
        fit: 'fit-content',
      },

      // =====================================================================
      // ASPECT RATIOS
      // =====================================================================
      aspectRatio: {
        manga: '2 / 3',
        'manga-wide': '3 / 2',
        'manga-square': '1 / 1',
        banner: '16 / 9',
        cover: '2 / 3',
      },

      // =====================================================================
      // BACKDROP BLUR
      // =====================================================================
      backdropBlur: {
        xs: '2px',
      },

      // =====================================================================
      // BACKGROUND IMAGE
      // =====================================================================
      backgroundImage: {
        'gradient-radial': 'radial-gradient(var(--tw-gradient-stops))',
        'gradient-conic': 'conic-gradient(from 180deg at 50% 50%, var(--tw-gradient-stops))',
        'grid-pattern': `
          linear-gradient(var(--nx-border-dim) 1px, transparent 1px),
          linear-gradient(90deg, var(--nx-border-dim) 1px, transparent 1px)
        `,
        'dots-pattern': 'radial-gradient(var(--nx-border-dim) 1px, transparent 1px)',
      },

      // =====================================================================
      // BACKGROUND SIZE
      // =====================================================================
      backgroundSize: {
        grid: '40px 40px',
        dots: '20px 20px',
      },

      // =====================================================================
      // TEXT SHADOW
      // =====================================================================
      textShadow: {
        sm: '0 1px 2px var(--tw-shadow-color)',
        DEFAULT: '0 2px 4px var(--tw-shadow-color)',
        lg: '0 8px 16px var(--tw-shadow-color)',
        // Text glow effects
        glow: '0 0 10px currentColor',
        'glow-sm': '0 0 5px currentColor',
        'glow-lg': '0 0 20px currentColor, 0 0 30px currentColor',
        'neon-primary': '0 0 7px var(--nx-primary), 0 0 10px var(--nx-primary), 0 0 21px var(--nx-primary)',
        'neon-secondary': '0 0 7px var(--nx-secondary), 0 0 10px var(--nx-secondary), 0 0 21px var(--nx-secondary)',
        'neon-accent': '0 0 7px var(--nx-accent), 0 0 10px var(--nx-accent), 0 0 21px var(--nx-accent)',
      },

      // =====================================================================
      // SKEW
      // =====================================================================
      skew: {
        '3': '3deg',
        '6': '6deg',
        '12': '12deg',
      },

      // =====================================================================
      // SCALE
      // =====================================================================
      scale: {
        '102': '1.02',
        '103': '1.03',
        '105': '1.05',
        '110': '1.1',
        '115': '1.15',
        '120': '1.2',
      },

      // =====================================================================
      // LINE HEIGHT
      // =====================================================================
      lineHeight: {
        '11': '2.75rem',
        '12': '3rem',
        '13': '3.25rem',
        '14': '3.5rem',
      },

      // =====================================================================
      // LETTER SPACING
      // =====================================================================
      letterSpacing: {
        wider: '0.1em',
        widest: '0.2em',
        cyber: '0.15em',
      },

      // =====================================================================
      // CURSOR
      // =====================================================================
      cursor: {
        fancy: 'pointer',
        cyber: 'crosshair',
      },

      // =====================================================================
      // SCREENS (Breakpoints)
      // =====================================================================
      // Breakpoints custom pour le responsive.
      screens: {
        xs: '475px',
        '3xl': '1920px',
        '4xl': '2560px',
      },
    },
  },

  // ===========================================================================
  // PLUGINS
  // ===========================================================================
  plugins: [
    // -------------------------------------------------------------------------
    // @tailwindcss/forms
    // -------------------------------------------------------------------------
    // Styles de base pour les éléments de formulaire (inputs, selects, etc.).
    // Fournit un reset cohérent et des styles par défaut.
    //
    // @see https://github.com/tailwindlabs/tailwindcss-forms
    require('@tailwindcss/forms')({
      strategy: 'class', // Applique les styles via la classe .form-input, etc.
    }),

    // -------------------------------------------------------------------------
    // @tailwindcss/typography
    // -------------------------------------------------------------------------
    // Plugin pour le contenu riche (markdown, articles, etc.).
    // Fournit la classe .prose avec des styles typographiques.
    //
    // @see https://github.com/tailwindlabs/tailwindcss-typography
    require('@tailwindcss/typography'),

    // -------------------------------------------------------------------------
    // @tailwindcss/aspect-ratio
    // -------------------------------------------------------------------------
    // Plugin pour gérer les ratios d'aspect (videos, images, etc.).
    // Fournit la classe .aspect-w-16 .aspect-h-9.
    //
    // @see https://github.com/tailwindlabs/tailwindcss-aspect-ratio
    require('@tailwindcss/aspect-ratio'),

    // -------------------------------------------------------------------------
    // tailwindcss-animate
    // -------------------------------------------------------------------------
    // Plugin shadcn-like pour les animations.
    // Fournit des utilities d'animation supplémentaires.
    //
    // @see https://github.com/jamiebuilds/tailwindcss-animate
    require('tailwindcss-animate'),

    // -------------------------------------------------------------------------
    // Plugin custom : Text Shadow
    // -------------------------------------------------------------------------
    // Ajoute les utilities text-shadow-* qui ne sont pas natives à Tailwind.
    plugin(function ({ addUtilities, theme }) {
      const textShadow = theme('textShadow');
      const utilities = Object.entries(textShadow || {}).reduce(
        (acc, [key, value]) => {
          acc[`.text-shadow-${key}`] = { textShadow: value };
          return acc;
        },
        {}
      );
      addUtilities(utilities);
    }),

    // -------------------------------------------------------------------------
    // Plugin custom : Glow Effects
    // -------------------------------------------------------------------------
    // Ajoute des utilities pour les effets de glow néon.
    plugin(function ({ addUtilities, theme }) {
      addUtilities({
        // Text glow utilities
        '.text-glow': {
          textShadow: '0 0 10px currentColor',
        },
        '.text-glow-strong': {
          textShadow:
            '0 0 5px currentColor, 0 0 10px currentColor, 0 0 20px currentColor',
        },
        '.text-glow-primary': {
          textShadow: 'var(--nx-primary-glow)',
          color: 'var(--nx-primary)',
        },
        '.text-glow-secondary': {
          textShadow: 'var(--nx-secondary-glow)',
          color: 'var(--nx-secondary)',
        },
        '.text-glow-accent': {
          textShadow: 'var(--nx-accent-glow)',
          color: 'var(--nx-accent)',
        },

        // Border glow utilities
        '.border-glow': {
          boxShadow: '0 0 10px var(--nx-primary-glow)',
          borderColor: 'var(--nx-primary)',
        },
        '.border-glow-secondary': {
          boxShadow: '0 0 10px var(--nx-secondary-glow)',
          borderColor: 'var(--nx-secondary)',
        },
        '.border-glow-accent': {
          boxShadow: '0 0 10px var(--nx-accent-glow)',
          borderColor: 'var(--nx-accent)',
        },

        // Neon text effect
        '.neon-text': {
          color: 'var(--nx-primary)',
          textShadow:
            '0 0 7px var(--nx-primary), 0 0 10px var(--nx-primary), 0 0 21px var(--nx-primary), 0 0 42px var(--nx-primary)',
        },
        '.neon-text-secondary': {
          color: 'var(--nx-secondary)',
          textShadow:
            '0 0 7px var(--nx-secondary), 0 0 10px var(--nx-secondary), 0 0 21px var(--nx-secondary), 0 0 42px var(--nx-secondary)',
        },
        '.neon-text-accent': {
          color: 'var(--nx-accent)',
          textShadow:
            '0 0 7px var(--nx-accent), 0 0 10px var(--nx-accent), 0 0 21px var(--nx-accent), 0 0 42px var(--nx-accent)',
        },
      });
    }),

    // -------------------------------------------------------------------------
    // Plugin custom : Background Patterns
    // -------------------------------------------------------------------------
    // Ajoute des utilities pour les patterns de fond (grille, points, etc.).
    plugin(function ({ addUtilities }) {
      addUtilities({
        '.bg-grid': {
          backgroundImage: `
            linear-gradient(var(--nx-border-dim) 1px, transparent 1px),
            linear-gradient(90deg, var(--nx-border-dim) 1px, transparent 1px)
          `,
          backgroundSize: '40px 40px',
          opacity: '0.15',
        },
        '.bg-dots': {
          backgroundImage:
            'radial-gradient(var(--nx-border-dim) 1px, transparent 1px)',
          backgroundSize: '20px 20px',
          opacity: '0.2',
        },
        '.bg-grid-primary': {
          backgroundImage: `
            linear-gradient(var(--nx-primary-bg) 1px, transparent 1px),
            linear-gradient(90deg, var(--nx-primary-bg) 1px, transparent 1px)
          `,
          backgroundSize: '40px 40px',
        },
        '.bg-scanlines': {
          backgroundImage: `repeating-linear-gradient(
            0deg,
            transparent,
            transparent 2px,
            rgba(0, 255, 65, 0.03) 2px,
            rgba(0, 255, 65, 0.03) 4px
          )`,
        },
        '.bg-noise': {
          backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.65' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)'/%3E%3C/svg%3E")`,
          opacity: '0.05',
        },
      });
    }),

    // -------------------------------------------------------------------------
    // Plugin custom : Glass Morphism
    // -------------------------------------------------------------------------
    // Ajoute des utilities pour l'effet glass morphism.
    plugin(function ({ addUtilities, theme }) {
      addUtilities({
        '.glass': {
          background: 'rgba(13, 17, 23, 0.8)',
          backdropFilter: 'blur(12px)',
          WebkitBackdropFilter: 'blur(12px)',
          border: '1px solid var(--nx-border-dim)',
        },
        '.glass-light': {
          background: 'rgba(255, 255, 255, 0.8)',
          backdropFilter: 'blur(12px)',
          WebkitBackdropFilter: 'blur(12px)',
          border: '1px solid var(--nx-border-dim)',
        },
        '.glass-primary': {
          background: 'rgba(0, 255, 65, 0.05)',
          backdropFilter: 'blur(12px)',
          WebkitBackdropFilter: 'blur(12px)',
          border: '1px solid var(--nx-primary-bg)',
        },
      });
    }),

    // -------------------------------------------------------------------------
    // Plugin custom : Scrollbar
    // -------------------------------------------------------------------------
    // Ajoute des utilities pour personnaliser les scrollbars.
    plugin(function ({ addUtilities }) {
      addUtilities({
        '.scrollbar-thin': {
          scrollbarWidth: 'thin',
          '&::-webkit-scrollbar': {
            width: '4px',
            height: '4px',
          },
          '&::-webkit-scrollbar-thumb': {
            background: 'var(--nx-border-dim)',
            borderRadius: '2px',
          },
          '&::-webkit-scrollbar-track': {
            background: 'transparent',
          },
        },
        '.scrollbar-default': {
          scrollbarWidth: 'auto',
          '&::-webkit-scrollbar': {
            width: '8px',
            height: '8px',
          },
          '&::-webkit-scrollbar-thumb': {
            background: 'var(--nx-border-dim)',
            borderRadius: '4px',
            border: '2px solid var(--nx-surface)',
          },
          '&::-webkit-scrollbar-thumb:hover': {
            background: 'var(--nx-primary)',
          },
          '&::-webkit-scrollbar-track': {
            background: 'var(--nx-surface)',
          },
        },
        '.scrollbar-none': {
          scrollbarWidth: 'none',
          '&::-webkit-scrollbar': {
            display: 'none',
          },
        },
      });
    }),

    // -------------------------------------------------------------------------
    // Plugin custom : Cyberpunk Effects
    // -------------------------------------------------------------------------
    // Ajoute des utilities pour les effets cyberpunk spécifiques.
    plugin(function ({ addUtilities }) {
      addUtilities({
        // CRT effect (scanlines)
        '.crt-effect': {
          position: 'relative',
          '&::before': {
            content: '""',
            position: 'absolute',
            inset: '0',
            background: `repeating-linear-gradient(
              0deg,
              rgba(0, 0, 0, 0.15),
              rgba(0, 0, 0, 0.15) 1px,
              transparent 1px,
              transparent 2px
            )`,
            pointerEvents: 'none',
            zIndex: '1',
          },
        },

        // Glitch effect
        '.glitch': {
          position: 'relative',
          '&::before, &::after': {
            content: 'attr(data-text)',
            position: 'absolute',
            top: '0',
            left: '0',
            width: '100%',
            height: '100%',
          },
          '&::before': {
            left: '2px',
            textShadow: '-2px 0 var(--nx-error)',
            clip: 'rect(44px, 450px, 56px, 0)',
            animation: 'glitch-anim-1 5s infinite linear alternate-reverse',
          },
          '&::after': {
            left: '-2px',
            textShadow: '-2px 0 var(--nx-secondary)',
            clip: 'rect(44px, 450px, 56px, 0)',
            animation: 'glitch-anim-2 5s infinite linear alternate-reverse',
          },
        },

        // Holographic effect
        '.holographic': {
          background: `linear-gradient(
            135deg,
            var(--nx-primary) 0%,
            var(--nx-secondary) 25%,
            var(--nx-accent) 50%,
            var(--nx-secondary) 75%,
            var(--nx-primary) 100%
          )`,
          backgroundSize: '200% 200%',
          animation: 'nx-gradient-shift 3s ease infinite',
        },

        // Matrix rain effect (background)
        '.matrix-bg': {
          backgroundImage: `
            radial-gradient(circle at 20% 50%, var(--nx-primary-bg) 0%, transparent 50%),
            radial-gradient(circle at 80% 80%, var(--nx-secondary-bg) 0%, transparent 50%),
            radial-gradient(circle at 40% 20%, var(--nx-accent-bg) 0%, transparent 50%)
          `,
        },
      });
    }),
  ],
};
