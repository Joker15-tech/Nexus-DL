/**
 * Point d'entrée central pour tous les stores Zustand de l'application NexusDL.
 *
 * Ce fichier agrège et ré-exporte tous les stores, types, hooks et utilitaires
 * nécessaires à la gestion de l'état global de l'application frontend.
 *
 * Architecture :
 *   store/
 *   ├── downloads.ts    : Gestion des tâches de téléchargement
 *   ├── settings.ts     : Configuration et préférences UI
 *   └── index.ts        : Ce fichier (agrégation et exports)
 *
 * Utilisation :
 *   // Importer un store spécifique
 *   import { useDownloadStore, useSettingsStore } from '@/store';
 *
 *   // Importer des hooks personnalisés
 *   import { useDownloads, useSettings, useAppReady } from '@/store';
 *
 *   // Importer des types
 *   import type { DownloadTask, FullConfig } from '@/store';
 *
 * @module store
 */

// ============================================================================
// IMPORTS DES STORES
// ============================================================================

import { useDownloadStore } from './downloads';
import { useSettingsStore } from './settings';

// ============================================================================
// RÉ-EXPORTS DES STORES
// ============================================================================

/**
 * Store Zustand pour la gestion des téléchargements.
 * Gère les tâches de téléchargement, les statistiques, et l'intégration WebSocket.
 */
export { useDownloadStore } from './downloads';

/**
 * Store Zustand pour la gestion des paramètres.
 * Gère la configuration backend et les préférences UI locales.
 */
export { useSettingsStore } from './settings';

// ============================================================================
// RÉ-EXPORTS DES TYPES - DOWNLOADS
// ============================================================================

export type {
  DownloadTask,
  DownloadStats,
  DownloadStoreState,
  CreateDownloadRequest,
  DownloadProgressUpdate,
  DownloadCompletedUpdate,
  DownloadFailedUpdate,
  DownloadListParams,
  DownloadStatus,
  DownloadFormat,
  ImageQuality,
  DownloadPriority,
  DownloadFilter,
  DownloadSortBy,
} from './downloads';

// ============================================================================
// RÉ-EXPORTS DES TYPES - SETTINGS
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
} from './settings';

// ============================================================================
// RÉ-EXPORTS DES FONCTIONS UTILITAIRES - DOWNLOADS
// ============================================================================

export {
  formatBytes,
  formatDuration,
  getStatusColor,
  getStatusIcon,
  isTerminalStatus,
  isActiveStatus,
} from './downloads';

// ============================================================================
// RÉ-EXPORTS DES FONCTIONS UTILITAIRES - SETTINGS
// ============================================================================

export {
  // Pas de fonctions utilitaires exportées depuis settings.ts
  // mais on pourrait en ajouter ici si nécessaire
} from './settings';

// ============================================================================
// RÉ-EXPORTS DES HOOKS - DOWNLOADS
// ============================================================================

export {
  useDownloadActions,
  useDownloadTasks,
  useDownloadStats,
  useDownloadTask,
  useDownloadCounts,
  useDownloadPolling,
  initializeWebSocketListeners,
} from './downloads';

// ============================================================================
// RÉ-EXPORTS DES HOOKS - SETTINGS
// ============================================================================

export {
  useSettingsActions,
  useConfig,
  useConfigSection,
  useUIPreferences,
  useThemeEffect,
  useLanguageEffect,
  useConfigReady,
  useUnsavedChangesWarning,
} from './settings';

// ============================================================================
// HOOKS COMBINÉS CROSS-STORES
// ============================================================================

/**
 * Hook pour accéder à l'état global de l'application.
 * Combine les stores downloads et settings pour une utilisation simplifiée.
 *
 * @example
 * ```tsx
 * const { downloads, settings, actions } = useAppStore();
 * console.log(downloads.tasks.length);
 * console.log(settings.ui.theme);
 * ```
 */
export function useAppStore() {
  const downloads = useDownloadStore();
  const settings = useSettingsStore();

  return {
    downloads: {
      tasks: downloads.tasks,
      stats: downloads.stats,
      isLoading: downloads.isLoading,
      error: downloads.error,
    },
    settings: {
      config: settings.config,
      ui: settings.ui,
      isLoaded: settings.isLoaded,
      isLoading: settings.isLoading,
      isDirty: settings.isDirty,
    },
    actions: {
      downloads: downloads.actions,
      settings: settings.actions,
    },
  };
}

/**
 * Hook pour vérifier si l'application est prête à être utilisée.
 * Vérifie que la configuration est chargée et que les téléchargements sont initialisés.
 *
 * @example
 * ```tsx
 * const isReady = useAppReady();
 * if (!isReady) return <LoadingScreen />;
 * return <MainApp />;
 * ```
 */
export function useAppReady(): boolean {
  const { settings } = useAppStore();
  const { useConfigReady } = require('./settings');
  
  const configReady = useConfigReady();
  
  return configReady && settings.isLoaded;
}

/**
 * Hook pour obtenir un résumé rapide de l'état des téléchargements.
 * Utile pour afficher des badges ou notifications dans l'UI.
 *
 * @example
 * ```tsx
 * const { activeCount, completedCount, hasErrors } = useDownloadSummary();
 * ```
 */
export function useDownloadSummary() {
  const tasks = useDownloadStore((state) => state.tasks);
  const stats = useDownloadStore((state) => state.stats);

  const activeCount = tasks.filter((t) => t.status === 'running' || t.status === 'downloading').length;
  const pendingCount = tasks.filter((t) => t.status === 'pending' || t.status === 'queued').length;
  const completedCount = tasks.filter((t) => t.status === 'completed').length;
  const failedCount = tasks.filter((t) => t.status === 'failed').length;
  const hasErrors = failedCount > 0;

  return {
    activeCount,
    pendingCount,
    completedCount,
    failedCount,
    hasErrors,
    totalTasks: tasks.length,
    stats,
  };
}

/**
 * Hook pour obtenir les préférences UI courantes avec leurs setters.
 * Combine l'accès aux préférences et aux actions de mise à jour.
 *
 * @example
 * ```tsx
 * const { theme, setTheme, language, setLanguage } = useUIPreferencesWithActions();
 * ```
 */
export function useUIPreferencesWithActions() {
  const ui = useSettingsStore((state) => state.ui);
  const actions = useSettingsStore((state) => state.actions);

  return {
    // Préférences
    theme: ui.theme,
    language: ui.language,
    readingDirection: ui.readingDirection,
    libraryViewMode: ui.libraryViewMode,
    mangaCardSize: ui.mangaCardSize,
    showAdultContent: ui.showAdultContent,
    sidebarCollapsed: ui.sidebarCollapsed,
    notificationsEnabled: ui.notificationsEnabled,
    notificationSounds: ui.notificationSounds,
    density: ui.density,

    // Setters
    setTheme: actions.setUITheme,
    setLanguage: actions.setUILanguage,
    setReadingDirection: actions.setReadingDirection,
    setLibraryViewMode: actions.setLibraryViewMode,
    setMangaCardSize: actions.setMangaCardSize,
    toggleAdultContent: actions.toggleAdultContent,
    toggleSidebar: actions.toggleSidebar,
    toggleNotifications: actions.toggleNotifications,
    toggleNotificationSounds: actions.toggleNotificationSounds,
    setDensity: actions.setDensity,
    resetUIPreferences: actions.resetUIPreferences,
  };
}

/**
 * Hook pour obtenir la configuration d'une section spécifique avec son setter.
 * Simplifie l'accès et la mise à jour d'une section de configuration.
 *
 * @example
 * ```tsx
 * const { config: downloadConfig, update } = useConfigSectionWithUpdater('download');
 * update({ max_concurrent_tasks: 10 });
 * ```
 */
export function useConfigSectionWithUpdater<K extends keyof FullConfig>(section: K) {
  const config = useSettingsStore((state) => state.config?.[section] ?? null);
  const updateSection = useSettingsStore((state) => state.actions.updateSection);

  const update = async (data: Partial<FullConfig[K]>) => {
    await updateSection(section, data);
  };

  return {
    config,
    update,
  };
}

// ============================================================================
// UTILITAIRES CROSS-STORES
// ============================================================================

/**
 * Réinitialise tous les stores à leur état initial.
 * Utile pour la déconnexion utilisateur ou le logout.
 *
 * @example
 * ```tsx
 * const handleLogout = async () => {
 *   await api.post('/auth/logout');
 *   resetAllStores();
 *   router.push('/login');
 * };
 * ```
 */
export function resetAllStores() {
  // Réinitialiser le store des téléchargements
  useDownloadStore.setState({
    tasks: [],
    stats: null,
    isLoading: false,
    error: null,
  });

  // Réinitialiser le store des paramètres (garder les préférences UI)
  useSettingsStore.setState({
    config: null,
    isLoaded: false,
    isLoading: false,
    isDirty: false,
    error: null,
    lastSyncedAt: null,
  });

  console.info('[Store] Tous les stores ont été réinitialisés');
}

/**
 * Force la synchronisation de tous les stores avec le backend.
 * Utile après une reconnexion ou un changement de contexte.
 *
 * @example
 * ```tsx
 * const handleReconnect = async () => {
 *   await syncAllStores();
 * };
 * ```
 */
export async function syncAllStores() {
  const downloadActions = useDownloadStore.getState().actions;
  const settingsActions = useSettingsStore.getState().actions;

  await Promise.all([
    downloadActions.fetchTasks(),
    downloadActions.fetchStats(),
    settingsActions.fetchConfig(),
  ]);

  console.info('[Store] Tous les stores ont été synchronisés');
}

/**
 * Vérifie s'il y a des modifications non sauvegardées dans n'importe quel store.
 * Utile pour afficher un avertissement avant de quitter une page.
 *
 * @example
 * ```tsx
 * const hasUnsavedChanges = checkForUnsavedChanges();
 * if (hasUnsavedChanges) {
 *   showConfirmDialog('You have unsaved changes. Are you sure you want to leave?');
 * }
 * ```
 */
export function checkForUnsavedChanges(): boolean {
  const settingsState = useSettingsStore.getState();
  return settingsState.isDirty;
}

/**
 * Exporte l'état complet de tous les stores pour le debug ou la sauvegarde.
 *
 * @example
 * ```tsx
 * const state = exportAllStoresState();
 * localStorage.setItem('debug-state', JSON.stringify(state));
 * ```
 */
export function exportAllStoresState() {
  return {
    downloads: useDownloadStore.getState(),
    settings: useSettingsStore.getState(),
    timestamp: new Date().toISOString(),
  };
}

/**
 * Restaure l'état des stores depuis un export précédent.
 * Attention : cette fonction peut causer des incohérences si mal utilisée.
 *
 * @example
 * ```tsx
 * const savedState = JSON.parse(localStorage.getItem('debug-state') || '{}');
 * importAllStoresState(savedState);
 * ```
 */
export function importAllStoresState(state: ReturnType<typeof exportAllStoresState>) {
  if (state.downloads) {
    useDownloadStore.setState(state.downloads);
  }

  if (state.settings) {
    useSettingsStore.setState(state.settings);
  }

  console.info('[Store] État des stores restauré depuis un export');
}

// ============================================================================
// CONSTANTES GLOBALES
// ============================================================================

/**
 * Version du schéma de state.
 * Incrémenter lors de changements breaking dans la structure des stores.
 */
export const STORE_SCHEMA_VERSION = '1.0.0';

/**
 * Clé de stockage pour la persistance des stores.
 */
export const STORE_PERSISTENCE_KEY = 'nexusdl-store';

// ============================================================================
// TYPES GLOBAUX
// ============================================================================

/**
 * État global de l'application (combinaison de tous les stores).
 */
export interface AppStoreState {
  downloads: ReturnType<typeof useDownloadStore.getState>;
  settings: ReturnType<typeof useSettingsStore.getState>;
}

/**
 * Actions globales de l'application.
 */
export interface AppStoreActions {
  resetAll: () => void;
  syncAll: () => Promise<void>;
  hasUnsavedChanges: () => boolean;
  exportState: () => ReturnType<typeof exportAllStoresState>;
  importState: (state: ReturnType<typeof exportAllStoresState>) => void;
}

// ============================================================================
// EXPORTS FINAUX
// ============================================================================

/**
 * Objet global contenant toutes les actions cross-stores.
 * Utile pour une utilisation outside des composants React.
 *
 * @example
 * ```ts
 * // Dans un service ou un utilitaire
 * import { appStoreActions } from '@/store';
 * await appStoreActions.syncAll();
 * ```
 */
export const appStoreActions: AppStoreActions = {
  resetAll: resetAllStores,
  syncAll: syncAllStores,
  hasUnsavedChanges: checkForUnsavedChanges,
  exportState: exportAllStoresState,
  importState: importAllStoresState,
};

// ============================================================================
// RÉ-EXPORTS DES SÉLECTEURS
// ============================================================================

/**
 * Sélecteurs du store downloads.
 */
export {
  selectAllTasks,
  selectActiveTasks,
  selectPendingTasks,
  selectCompletedTasks,
  selectFailedTasks,
  selectPausedTasks,
  selectTaskById,
  selectStats,
  selectIsLoading,
  selectError,
  selectTaskCounts,
} from './downloads';

/**
 * Sélecteurs du store settings.
 */
export {
  selectConfig,
  selectIsLoaded,
  selectIsDirty,
  selectUIPreferences,
  selectUITheme,
  selectUILanguage,
  selectSection,
  selectAppConfig,
  selectDownloadConfig,
  selectLibraryConfig,
  selectNetworkConfig,
  selectInterfaceConfig,
} from './settings';

// ============================================================================
// FIN DU MODULE
// ============================================================================
