/**
 * Store Zustand pour la gestion des paramètres de l'application.
 *
 * Ce store centralise l'état de tous les paramètres de configuration,
 * gère la persistance locale des préférences UI (thème, langue, layout),
 * et fournit les actions pour synchroniser avec l'API REST backend.
 *
 * Architecture :
 *   - State : sections de configuration, préférences UI locales, état de sync
 *   - Actions : CRUD par section, export/import, reset, sync avec le backend
 *   - Persistance : localStorage pour les préférences UI (via zustand/persist)
 *   - Intégration : API REST (GET/PATCH /settings, POST /settings/export, etc.)
 *
 * Sections de configuration (correspondant au backend) :
 *   - app         : Langue, thème, timezone, sens de lecture
 *   - network     : Timeouts, connexions max, SSL, HTTP/2
 *   - proxy       : Activation, URL, authentification, rotation
 *   - logging     : Niveau, format, rotation, rétention
 *   - download    : Concurrence, format, qualité, compression
 *   - library     : Chemin, scan automatique, intervalle
 *   - cloudflare  : Mode bypass, FlareSolverr, Playwright
 *   - i18n        : Langue, répertoire de traductions
 *   - storage     : Cache, nettoyage, tailles max
 *   - events      : Queue size, workers, timeout
 *   - interface   : Web UI, GUI, CLI
 *
 * @module store/settings
 */

import { create } from 'zustand';
import { devtools, persist, subscribeWithSelector } from 'zustand/middleware';
import { toast } from 'sonner';

import { api } from '@/lib/api';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Sections de configuration disponibles.
 */
export type ConfigSection =
  | 'app'
  | 'network'
  | 'proxy'
  | 'logging'
  | 'download'
  | 'library'
  | 'cloudflare'
  | 'i18n'
  | 'storage'
  | 'events'
  | 'interface';

/**
 * Thème de l'interface utilisateur.
 */
export type UITheme = 'dark' | 'light' | 'system' | 'cyberpunk';

/**
 * Langue de l'interface.
 */
export type UILanguage = 'en' | 'fr' | 'es' | 'de' | 'ja' | 'ko' | 'zh' | 'pt' | 'ru';

/**
 * Sens de lecture des mangas.
 */
export type ReadingDirection = 'ltr' | 'rtl' | 'vertical';

/**
 * Mode d'affichage de la bibliothèque.
 */
export type LibraryViewMode = 'grid' | 'list' | 'compact';

/**
 * Format d'export/import de la configuration.
 */
export type ConfigExportFormat = 'json' | 'yaml';

/**
 * Configuration de la section "app".
 */
export interface AppConfig {
  language: string;
  theme: string;
  timezone: string;
  reading_direction: ReadingDirection;
  check_updates: boolean;
  auto_update: boolean;
}

/**
 * Configuration de la section "network".
 */
export interface NetworkConfig {
  timeout: number;
  connect_timeout: number;
  max_connections: number;
  max_connections_per_host: number;
  verify_ssl: boolean;
  http2: boolean;
  follow_redirects: boolean;
  max_redirects: number;
}

/**
 * Configuration de la section "proxy".
 */
export interface ProxyConfig {
  enabled: boolean;
  url: string;
  username: string;
  password: string;
  rotation: boolean;
  rotation_interval: number;
}

/**
 * Configuration de la section "logging".
 */
export interface LoggingConfig {
  level: 'TRACE' | 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL';
  format: 'text' | 'rich' | 'json' | 'simple';
  file_enabled: boolean;
  file_rotation: string;
  file_retention: string;
  colorize: boolean;
}

/**
 * Configuration de la section "download".
 */
export interface DownloadConfig {
  max_concurrent_tasks: number;
  max_concurrent_pages: number;
  default_format: 'cbz' | 'cbr' | 'pdf' | 'zip' | 'folder';
  default_quality: 'original' | 'high' | 'medium' | 'low';
  output_dir: string;
  temp_dir: string;
  auto_extract: boolean;
  delete_temp: boolean;
  retry_attempts: number;
  retry_delay: number;
}

/**
 * Configuration de la section "library".
 */
export interface LibraryConfig {
  path: string;
  auto_scan: boolean;
  scan_interval_minutes: number;
  organize_by: 'title' | 'author' | 'language' | 'site';
  naming_pattern: string;
}

/**
 * Configuration de la section "cloudflare".
 */
export interface CloudflareConfig {
  bypass_mode: 'none' | 'flaresolverr' | 'playwright' | 'auto';
  flaresolverr_url: string;
  playwright_headless: boolean;
  playwright_timeout: number;
}

/**
 * Configuration de la section "i18n".
 */
export interface I18nConfig {
  language: string;
  fallback_language: string;
  translations_dir: string;
}

/**
 * Configuration de la section "storage".
 */
export interface StorageConfig {
  cache_enabled: boolean;
  cache_dir: string;
  cache_max_size_mb: number;
  cache_ttl_hours: number;
  auto_cleanup: boolean;
  cleanup_interval_hours: number;
}

/**
 * Configuration de la section "events".
 */
export interface EventsConfig {
  queue_size: number;
  worker_count: number;
  handler_timeout: number;
  dead_letter_enabled: boolean;
}

/**
 * Configuration de la section "interface".
 */
export interface InterfaceConfig {
  web_enabled: boolean;
  web_host: string;
  web_port: number;
  gui_enabled: boolean;
  cli_enabled: boolean;
}

/**
 * Configuration complète de l'application (toutes les sections).
 */
export interface FullConfig {
  app: AppConfig;
  network: NetworkConfig;
  proxy: ProxyConfig;
  logging: LoggingConfig;
  download: DownloadConfig;
  library: LibraryConfig;
  cloudflare: CloudflareConfig;
  i18n: I18nConfig;
  storage: StorageConfig;
  events: EventsConfig;
  interface: InterfaceConfig;
}

/**
 * Préférences UI locales (persistées dans le navigateur).
 * Ces préférences ne sont PAS synchronisées avec le backend.
 */
export interface LocalUIPreferences {
  /** Thème de l'interface. */
  theme: UITheme;
  /** Langue de l'interface. */
  language: UILanguage;
  /** Sens de lecture. */
  readingDirection: ReadingDirection;
  /** Mode d'affichage de la bibliothèque. */
  libraryViewMode: LibraryViewMode;
  /** Taille des cartes de manga dans la bibliothèque. */
  mangaCardSize: 'small' | 'medium' | 'large';
  /** Afficher les mangas adultes dans les résultats. */
  showAdultContent: boolean;
  /** Sidebar repliée. */
  sidebarCollapsed: boolean;
  /** Notifications activées. */
  notificationsEnabled: boolean;
  /** Sons de notification activés. */
  notificationSounds: boolean;
  /** Densité de l'interface. */
  density: 'compact' | 'comfortable' | 'spacious';
}

/**
 * État complet du store de paramètres.
 */
export interface SettingsStoreState {
  // ----------------------------------------------------------------
  // Configuration backend (synchronisée avec l'API)
  // ----------------------------------------------------------------
  /** Configuration complète chargée depuis le backend. */
  config: FullConfig | null;
  /** Indique si la configuration a été chargée. */
  isLoaded: boolean;
  /** Indique si une opération est en cours. */
  isLoading: boolean;
  /** Indique si des modifications non sauvegardées existent. */
  isDirty: boolean;
  /** Dernier message d'erreur. */
  error: string | null;
  /** Timestamp de la dernière synchronisation. */
  lastSyncedAt: string | null;

  // ----------------------------------------------------------------
  // Préférences UI locales (persistées dans le navigateur)
  // ----------------------------------------------------------------
  /** Préférences UI locales. */
  ui: LocalUIPreferences;

  // ----------------------------------------------------------------
  // Actions
  // ----------------------------------------------------------------
  actions: {
    // Configuration backend
    fetchConfig: () => Promise<void>;
    updateSection: <K extends ConfigSection>(section: K, data: Partial<FullConfig[K]>) => Promise<void>;
    updateFullConfig: (config: Partial<FullConfig>) => Promise<void>;
    resetConfig: (sections?: ConfigSection[]) => Promise<void>;
    exportConfig: (format: ConfigExportFormat, sections?: ConfigSection[]) => Promise<string>;
    importConfig: (config: Record<string, unknown>, overwrite?: boolean) => Promise<void>;
    syncConfig: () => Promise<void>;

    // Préférences UI locales
    setUITheme: (theme: UITheme) => void;
    setUILanguage: (language: UILanguage) => void;
    setReadingDirection: (direction: ReadingDirection) => void;
    setLibraryViewMode: (mode: LibraryViewMode) => void;
    setMangaCardSize: (size: 'small' | 'medium' | 'large') => void;
    toggleAdultContent: () => void;
    toggleSidebar: () => void;
    toggleNotifications: () => void;
    toggleNotificationSounds: () => void;
    setDensity: (density: 'compact' | 'comfortable' | 'spacious') => void;
    resetUIPreferences: () => void;

    // Utilitaires
    clearError: () => void;
    markDirty: () => void;
    markClean: () => void;
  };
}

// ============================================================================
// VALEURS PAR DÉFAUT
// ============================================================================

/**
 * Préférences UI par défaut.
 */
const DEFAULT_UI_PREFERENCES: LocalUIPreferences = {
  theme: 'cyberpunk',
  language: 'en',
  readingDirection: 'ltr',
  libraryViewMode: 'grid',
  mangaCardSize: 'medium',
  showAdultContent: false,
  sidebarCollapsed: false,
  notificationsEnabled: true,
  notificationSounds: true,
  density: 'comfortable',
};

// ============================================================================
// STORE PRINCIPAL
// ============================================================================

/**
 * Store Zustand pour les paramètres de l'application.
 *
 * Utilise les middlewares :
 *   - devtools : intégration Redux DevTools
 *   - persist : persistance des préférences UI dans localStorage
 *   - subscribeWithSelector : subscriptions sélectives pour les effets de bord
 */
export const useSettingsStore = create<SettingsStoreState>()(
  devtools(
    persist(
      subscribeWithSelector((set, get) => ({
        // ==============================================================
        // ÉTAT INITIAL
        // ==============================================================
        config: null,
        isLoaded: false,
        isLoading: false,
        isDirty: false,
        error: null,
        lastSyncedAt: null,

        ui: { ...DEFAULT_UI_PREFERENCES },

        // ==============================================================
        // ACTIONS
        // ==============================================================

        actions: {
          // ----------------------------------------------------------
          // fetchConfig - Charge la configuration depuis le backend
          // ----------------------------------------------------------
          fetchConfig: async () => {
            set({ isLoading: true, error: null });

            try {
              const response = await api.get<{
                config: FullConfig;
                last_modified: string;
                version: string;
              }>('/settings');

              set({
                config: response.config,
                isLoaded: true,
                isLoading: false,
                isDirty: false,
                lastSyncedAt: new Date().toISOString(),
              });

              console.debug('[SettingsStore] Configuration chargée depuis le backend');
            } catch (error) {
              const message = error instanceof Error ? error.message : 'Failed to fetch settings';
              console.error('[SettingsStore] fetchConfig error:', error);
              set({
                isLoading: false,
                error: message,
              });
              toast.error('Failed to load settings', {
                description: message,
              });
            }
          },

          // ----------------------------------------------------------
          // updateSection - Met à jour une section spécifique
          // ----------------------------------------------------------
          updateSection: async <K extends ConfigSection>(
            section: K,
            data: Partial<FullConfig[K]>
          ) => {
            set({ isLoading: true, error: null });

            try {
              await api.patch(`/settings/${section}`, { data });

              // Mettre à jour le state local
              set((state) => {
                if (!state.config) return state;
                return {
                  config: {
                    ...state.config,
                    [section]: {
                      ...state.config[section],
                      ...data,
                    },
                  },
                  isLoading: false,
                  isDirty: true,
                  lastSyncedAt: new Date().toISOString(),
                };
              });

              toast.success('Settings updated', {
                description: `Section "${section}" updated successfully`,
              });
            } catch (error) {
              const message = error instanceof Error ? error.message : 'Failed to update settings';
              console.error(`[SettingsStore] updateSection(${section}) error:`, error);
              set({
                isLoading: false,
                error: message,
              });
              toast.error('Failed to update settings', {
                description: message,
              });
            }
          },

          // ----------------------------------------------------------
          // updateFullConfig - Met à jour la configuration complète
          // ----------------------------------------------------------
          updateFullConfig: async (config: Partial<FullConfig>) => {
            set({ isLoading: true, error: null });

            try {
              await api.put('/settings', { config });

              set((state) => ({
                config: state.config
                  ? { ...state.config, ...config }
                  : (config as FullConfig),
                isLoading: false,
                isDirty: false,
                lastSyncedAt: new Date().toISOString(),
              }));

              toast.success('Settings saved', {
                description: 'All settings updated successfully',
              });
            } catch (error) {
              const message = error instanceof Error ? error.message : 'Failed to save settings';
              console.error('[SettingsStore] updateFullConfig error:', error);
              set({
                isLoading: false,
                error: message,
              });
              toast.error('Failed to save settings', {
                description: message,
              });
            }
          },

          // ----------------------------------------------------------
          // resetConfig - Réinitialise la configuration
          // ----------------------------------------------------------
          resetConfig: async (sections?: ConfigSection[]) => {
            set({ isLoading: true, error: null });

            try {
              await api.post('/settings/reset', {
                sections: sections || null,
                confirm: true,
              });

              // Recharger la configuration depuis le backend
              await get().actions.fetchConfig();

              toast.success('Settings reset', {
                description: sections
                  ? `Sections ${sections.join(', ')} reset to defaults`
                  : 'All settings reset to defaults',
              });
            } catch (error) {
              const message = error instanceof Error ? error.message : 'Failed to reset settings';
              console.error('[SettingsStore] resetConfig error:', error);
              set({
                isLoading: false,
                error: message,
              });
              toast.error('Failed to reset settings', {
                description: message,
              });
            }
          },

          // ----------------------------------------------------------
          // exportConfig - Exporte la configuration
          // ----------------------------------------------------------
          exportConfig: async (
            format: ConfigExportFormat,
            sections?: ConfigSection[]
          ): Promise<string> => {
            set({ isLoading: true, error: null });

            try {
              const response = await api.post<{
                format: string;
                content: string;
                size_bytes: number;
                sections_exported: string[];
              }>('/settings/export', {
                format,
                include_metadata: true,
                sections: sections || null,
              });

              set({ isLoading: false });

              toast.success('Configuration exported', {
                description: `${response.sections_exported.length} section(s) exported as ${format.toUpperCase()}`,
              });

              return response.content;
            } catch (error) {
              const message = error instanceof Error ? error.message : 'Failed to export settings';
              console.error('[SettingsStore] exportConfig error:', error);
              set({
                isLoading: false,
                error: message,
              });
              toast.error('Failed to export settings', {
                description: message,
              });
              throw error;
            }
          },

          // ----------------------------------------------------------
          // importConfig - Importe une configuration
          // ----------------------------------------------------------
          importConfig: async (
            config: Record<string, unknown>,
            overwrite: boolean = true
          ) => {
            set({ isLoading: true, error: null });

            try {
              await api.post('/settings/import', {
                config,
                overwrite,
                validate: true,
              });

              // Recharger la configuration
              await get().actions.fetchConfig();

              toast.success('Configuration imported', {
                description: 'Settings imported and applied successfully',
              });
            } catch (error) {
              const message = error instanceof Error ? error.message : 'Failed to import settings';
              console.error('[SettingsStore] importConfig error:', error);
              set({
                isLoading: false,
                error: message,
              });
              toast.error('Failed to import settings', {
                description: message,
              });
            }
          },

          // ----------------------------------------------------------
          // syncConfig - Synchronise avec le backend
          // ----------------------------------------------------------
          syncConfig: async () => {
            await get().actions.fetchConfig();
          },

          // ----------------------------------------------------------
          // Préférences UI locales
          // ----------------------------------------------------------

          setUITheme: (theme: UITheme) => {
            set((state) => ({
              ui: { ...state.ui, theme },
            }));
          },

          setUILanguage: (language: UILanguage) => {
            set((state) => ({
              ui: { ...state.ui, language },
            }));
          },

          setReadingDirection: (direction: ReadingDirection) => {
            set((state) => ({
              ui: { ...state.ui, readingDirection: direction },
            }));
          },

          setLibraryViewMode: (mode: LibraryViewMode) => {
            set((state) => ({
              ui: { ...state.ui, libraryViewMode: mode },
            }));
          },

          setMangaCardSize: (size: 'small' | 'medium' | 'large') => {
            set((state) => ({
              ui: { ...state.ui, mangaCardSize: size },
            }));
          },

          toggleAdultContent: () => {
            set((state) => ({
              ui: { ...state.ui, showAdultContent: !state.ui.showAdultContent },
            }));
          },

          toggleSidebar: () => {
            set((state) => ({
              ui: { ...state.ui, sidebarCollapsed: !state.ui.sidebarCollapsed },
            }));
          },

          toggleNotifications: () => {
            set((state) => ({
              ui: { ...state.ui, notificationsEnabled: !state.ui.notificationsEnabled },
            }));
          },

          toggleNotificationSounds: () => {
            set((state) => ({
              ui: { ...state.ui, notificationSounds: !state.ui.notificationSounds },
            }));
          },

          setDensity: (density: 'compact' | 'comfortable' | 'spacious') => {
            set((state) => ({
              ui: { ...state.ui, density },
            }));
          },

          resetUIPreferences: () => {
            set({
              ui: { ...DEFAULT_UI_PREFERENCES },
            });
            toast.success('UI preferences reset to defaults');
          },

          // ----------------------------------------------------------
          // Utilitaires
          // ----------------------------------------------------------

          clearError: () => {
            set({ error: null });
          },

          markDirty: () => {
            set({ isDirty: true });
          },

          markClean: () => {
            set({ isDirty: false });
          },
        },
      })),
      {
        name: 'nexusdl-settings',
        partialize: (state) => ({
          // Ne persister que les préférences UI locales
          ui: state.ui,
        }),
      }
    ),
    {
      name: 'nexusdl-settings-devtools',
    }
  )
);

// ============================================================================
// SÉLECTEURS TYPÉS
// ============================================================================

/** Sélecteur : Configuration complète. */
export const selectConfig = (state: SettingsStoreState) => state.config;

/** Sélecteur : Indique si la configuration est chargée. */
export const selectIsLoaded = (state: SettingsStoreState) => state.isLoaded;

/** Sélecteur : Indique si une opération est en cours. */
export const selectIsLoading = (state: SettingsStoreState) => state.isLoading;

/** Sélecteur : Indique s'il y a des modifications non sauvegardées. */
export const selectIsDirty = (state: SettingsStoreState) => state.isDirty;

/** Sélecteur : Dernier message d'erreur. */
export const selectError = (state: SettingsStoreState) => state.error;

/** Sélecteur : Préférences UI locales. */
export const selectUIPreferences = (state: SettingsStoreState) => state.ui;

/** Sélecteur : Thème UI. */
export const selectUITheme = (state: SettingsStoreState) => state.ui.theme;

/** Sélecteur : Langue UI. */
export const selectUILanguage = (state: SettingsStoreState) => state.ui.language;

/** Sélecteur : Configuration d'une section spécifique. */
export const selectSection = <K extends ConfigSection>(section: K) => (state: SettingsStoreState) =>
  state.config?.[section] ?? null;

/** Sélecteur : Configuration de la section "app". */
export const selectAppConfig = (state: SettingsStoreState) => state.config?.app ?? null;

/** Sélecteur : Configuration de la section "download". */
export const selectDownloadConfig = (state: SettingsStoreState) => state.config?.download ?? null;

/** Sélecteur : Configuration de la section "library". */
export const selectLibraryConfig = (state: SettingsStoreState) => state.config?.library ?? null;

/** Sélecteur : Configuration de la section "network". */
export const selectNetworkConfig = (state: SettingsStoreState) => state.config?.network ?? null;

/** Sélecteur : Configuration de la section "interface". */
export const selectInterfaceConfig = (state: SettingsStoreState) => state.config?.interface ?? null;

// ============================================================================
// HOOKS PERSONNALISÉS
// ============================================================================

/**
 * Hook pour accéder aux actions du store de paramètres.
 *
 * @example
 * ```tsx
 * const { fetchConfig, updateSection, setUITheme } = useSettingsActions();
 * ```
 */
export function useSettingsActions() {
  return useSettingsStore((state) => state.actions);
}

/**
 * Hook pour accéder à la configuration complète.
 */
export function useConfig() {
  return useSettingsStore(selectConfig);
}

/**
 * Hook pour accéder à une section spécifique de la configuration.
 *
 * @example
 * ```tsx
 * const downloadConfig = useConfigSection('download');
 * console.log(downloadConfig?.max_concurrent_tasks); // 5
 * ```
 */
export function useConfigSection<K extends ConfigSection>(section: K) {
  return useSettingsStore(selectSection(section));
}

/**
 * Hook pour accéder aux préférences UI locales.
 *
 * @example
 * ```tsx
 * const ui = useUIPreferences();
 * console.log(ui.theme); // 'cyberpunk'
 * ```
 */
export function useUIPreferences() {
  return useSettingsStore(selectUIPreferences);
}

/**
 * Hook pour le thème UI avec application automatique au DOM.
 *
 * Applique automatiquement la classe CSS correspondante sur <html>
 * quand le thème change.
 *
 * @example
 * ```tsx
 * // Dans un layout ou provider
 * useThemeEffect();
 * ```
 */
export function useThemeEffect() {
  const theme = useSettingsStore(selectUITheme);

  // Effet de bord : appliquer le thème au DOM
  useSettingsStore.subscribe(
    (state) => state.ui.theme,
    (newTheme) => {
      if (typeof document !== 'undefined') {
        const root = document.documentElement;
        
        // Retirer les anciennes classes de thème
        root.classList.remove('theme-dark', 'theme-light', 'theme-cyberpunk', 'theme-system');
        
        // Appliquer la nouvelle classe
        if (newTheme === 'system') {
          const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
          root.classList.add(prefersDark ? 'theme-dark' : 'theme-light');
        } else {
          root.classList.add(`theme-${newTheme}`);
        }
        
        // Mettre à jour la meta theme-color
        const metaThemeColor = document.querySelector('meta[name="theme-color"]');
        if (metaThemeColor) {
          const colors: Record<string, string> = {
            dark: '#0d1117',
            light: '#ffffff',
            cyberpunk: '#000000',
            system: '#0d1117',
          };
          metaThemeColor.setAttribute('content', colors[newTheme] || '#000000');
        }
      }
    },
    { fireImmediately: true }
  );
}

/**
 * Hook pour la langue UI avec application automatique.
 *
 * @example
 * ```tsx
 * useLanguageEffect();
 * ```
 */
export function useLanguageEffect() {
  useSettingsStore.subscribe(
    (state) => state.ui.language,
    (newLanguage) => {
      if (typeof document !== 'undefined') {
        document.documentElement.setAttribute('lang', newLanguage);
      }
    },
    { fireImmediately: true }
  );
}

/**
 * Hook pour vérifier si la configuration est prête.
 * Charge automatiquement la configuration si elle n'est pas encore chargée.
 *
 * @example
 * ```tsx
 * const isReady = useConfigReady();
 * if (!isReady) return <LoadingSpinner />;
 * ```
 */
export function useConfigReady(): boolean {
  const isLoaded = useSettingsStore(selectIsLoaded);
  const isLoading = useSettingsStore(selectIsLoading);
  const fetchConfig = useSettingsStore((state) => state.actions.fetchConfig);

  // Import dynamique de useEffect pour éviter les warnings SSR
  const { useEffect } = require('react');

  useEffect(() => {
    if (!isLoaded && !isLoading) {
      fetchConfig();
    }
  }, [isLoaded, isLoading, fetchConfig]);

  return isLoaded;
}

/**
 * Hook pour détecter les modifications non sauvegardées.
 * Affiche un avertissement avant de quitter la page si isDirty est true.
 *
 * @example
 * ```tsx
 * useUnsavedChangesWarning();
 * ```
 */
export function useUnsavedChangesWarning() {
  const isDirty = useSettingsStore(selectIsDirty);

  const { useEffect } = require('react');

  useEffect(() => {
    if (!isDirty) return;

    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [isDirty]);
}

// ============================================================================
// EXPORTS
// ============================================================================

export type {
  ConfigSection,
  UITheme,
  UILanguage,
  ReadingDirection,
  LibraryViewMode,
  ConfigExportFormat,
  AppConfig,
  NetworkConfig,
  ProxyConfig,
  LoggingConfig,
  DownloadConfig,
  LibraryConfig,
  CloudflareConfig,
  I18nConfig,
  StorageConfig,
  EventsConfig,
  InterfaceConfig,
  FullConfig,
  LocalUIPreferences,
  SettingsStoreState,
};
