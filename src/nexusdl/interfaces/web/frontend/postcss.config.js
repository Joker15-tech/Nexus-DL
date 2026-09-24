/**
 * PostCSS Configuration for NexusDL Frontend.
 *
 * PostCSS est utilisé par Next.js et Tailwind CSS pour le traitement
 * et la transformation des fichiers CSS. Cette configuration définit
 * la chaîne de plugins appliqués dans l'ordre.
 *
 * Plugins (dans l'ordre d'exécution) :
 *   1. postcss-import        : Résout les @import CSS
 *   2. tailwindcss/nesting   : Support du nesting CSS natif
 *   3. tailwindcss           : Framework CSS utility-first
 *   4. autoprefixer          : Ajoute les préfixes vendor (-webkit, -moz, etc.)
 *   5. postcss-preset-env    : Polyfills pour les fonctionnalités CSS modernes
 *   6. cssnano               : Minification CSS (production uniquement)
 *
 * Chaîne de traitement :
 *   Source CSS
 *     → postcss-import (résolution des imports)
 *     → tailwindcss/nesting (dé-nesting)
 *     → tailwindcss (génération des utilities)
 *     → autoprefixer (préfixes vendor)
 *     → postcss-preset-env (polyfills)
 *     → cssnano (minification, prod only)
 *     → Output CSS
 *
 * Compatibilité :
 *   - Next.js 14+
 *   - Tailwind CSS 3.4+
 *   - PostCSS 8.4+
 *   - Node.js 18.17+
 *
 * @see https://postcss.org/
 * @see https://tailwindcss.com/docs/using-with-preprocessors
 * @see https://nextjs.org/docs/app/building-your-application/configuring/post-css
 *
 * @module postcss.config
 * @license GPL-3.0-only
 */

/**
 * Détermine si l'environnement est en mode production.
 * @type {boolean}
 */
const isProduction = process.env.NODE_ENV === 'production';

/**
 * Détermine si l'analyse du bundle est activée.
 * @type {boolean}
 */
const isAnalyze = process.env.ANALYZE === 'true';

/**
 * Configuration PostCSS.
 *
 * @type {import('postcss-load-config').Config}
 */
const config = {
  /**
   * Plugins PostCSS appliqués dans l'ordre.
   *
   * L'ordre est important :
   *   - postcss-import doit être en premier pour résoudre les @import
   *     avant que Tailwind ne traite le CSS.
   *   - tailwindcss/nesting doit être avant tailwindcss pour dé-nester
   *     les règles CSS avant la génération des utilities.
   *   - autoprefixer doit être après tailwindcss pour préfixer les
   *     utilities générées.
   *   - cssnano doit être en dernier pour minifier le résultat final.
   */
  plugins: {
    // -----------------------------------------------------------------------
    // 1. postcss-import
    // -----------------------------------------------------------------------
    // Résout les directives @import dans les fichiers CSS.
    // Permet d'organiser le CSS en plusieurs fichiers et de les importer
    // dans globals.css ou d'autres fichiers.
    //
    // Exemple :
    //   @import './components/buttons.css';
    //   @import './utilities/animations.css';
    //
    // @see https://github.com/postcss/postcss-import
    'postcss-import': {},

    // -----------------------------------------------------------------------
    // 2. tailwindcss/nesting
    // -----------------------------------------------------------------------
    // Support du nesting CSS natif (CSS Nesting Module Level 1).
    // Permet d'écrire du CSS imbriqué qui sera dé-nesté avant le
    // traitement par Tailwind.
    //
    // Exemple :
    //   .card {
    //     @apply bg-surface border-2 border-border-dim;
    //     &:hover {
    //       @apply border-secondary;
    //     }
    //     & .title {
    //       @apply text-primary font-bold;
    //     }
    //   }
    //
    // @see https://tailwindcss.com/docs/using-with-preprocessors#nesting
    'tailwindcss/nesting': {},

    // -----------------------------------------------------------------------
    // 3. tailwindcss
    // -----------------------------------------------------------------------
    // Plugin principal de Tailwind CSS.
    // Génère les classes utilitaires à partir de la configuration
    // définie dans tailwind.config.js.
    //
    // Scanne les fichiers définis dans le `content` de tailwind.config.js
    // et génère uniquement les classes utilisées (tree-shaking).
    //
    // @see https://tailwindcss.com/docs/installation/using-postcss
    tailwindcss: {},

    // -----------------------------------------------------------------------
    // 4. autoprefixer
    // -----------------------------------------------------------------------
    // Ajoute automatiquement les préfixes vendor (-webkit-, -moz-, -ms-)
    // en fonction des navigateurs cibles définis dans browserslist
    // (package.json ou .browserslistrc).
    //
    // Cibles NexusDL (définies dans package.json) :
    //   - Chrome >= 90
    //   - Firefox >= 88
    //   - Safari >= 14
    //   - Edge >= 90
    //
    // Exemple de transformation :
    //   Input:  user-select: none;
    //   Output: -webkit-user-select: none;
    //           user-select: none;
    //
    // @see https://github.com/postcss/autoprefixer
    autoprefixer: {},

    // -----------------------------------------------------------------------
    // 5. postcss-preset-env (optionnel, activé en production)
    // -----------------------------------------------------------------------
    // Polyfills pour les fonctionnalités CSS modernes non supportées
    // par les navigateurs cibles. Convertit le CSS moderne en CSS
    // compatible avec les anciens navigateurs.
    //
    // Fonctionnalités polyfillées :
    //   - CSS Custom Properties (var())
    //   - CSS Nesting
    //   - :is() et :where()
    //   - color-mix()
    //   - Container Queries
    //   - Cascade Layers (@layer)
    //
    // Stage 2 = fonctionnalités relativement stables
    // @see https://preset-env.cssdb.org/
    ...(isProduction
      ? {
          'postcss-preset-env': {
            stage: 2,
            features: {
              // Activer les custom properties avec fallback
              'custom-properties': {
                preserve: true,
              },
              // Activer le nesting natif
              'nesting-rules': true,
              // Activer :is() et :where()
              'is-pseudo-class': true,
              'where-pseudo-class': true,
              // Activer color-mix()
              'color-mix': true,
              // Activer les container queries
              'container-queries': true,
            },
            browsers: [
              'chrome >= 90',
              'firefox >= 88',
              'safari >= 14',
              'edge >= 90',
            ],
          },
        }
      : {}),

    // -----------------------------------------------------------------------
    // 6. cssnano (production uniquement)
    // -----------------------------------------------------------------------
    // Minifie le CSS pour la production :
    //   - Supprime les espaces et commentaires
    //   - Optimise les sélecteurs
    //   - Fusionne les règles dupliquées
    //   - Réduit les valeurs (ex: #ffffff → #fff)
    //   - Supprime les propriétés inutilisées
    //
    // Réduction typique : 40-60% de la taille du CSS
    //
    // @see https://cssnano.co/
    ...(isProduction && !isAnalyze
      ? {
          cssnano: {
            preset: [
              'default',
              {
                // Conserver les commentaires de licence
                discardComments: {
                  removeAll: false,
                  remove: (comment) => !comment.includes('@license'),
                },
                // Ne pas réordonner les z-index (peut casser les overlays)
                zindex: false,
                // Ne pas convertir les couleurs en noms (peut casser le thème)
                colormin: true,
                // Optimiser les calc()
                calc: true,
                // Réduire les valeurs initiales
                reduceInitial: true,
                // Fusionner les règles longues
                mergeLonghand: true,
                // Ne pas réduire les identifiants (peut casser les animations)
                reduceIdents: false,
              },
            ],
          },
        }
      : {}),
  },
};

module.exports = config;
