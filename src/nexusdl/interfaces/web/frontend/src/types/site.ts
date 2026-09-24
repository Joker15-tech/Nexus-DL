/**
 * Types et interfaces pour la gestion des sites (sources de mangas)
 * dans le frontend NexusDL.
 *
 * Ce fichier définit tous les types TypeScript correspondant aux schémas
 * Pydantic du backend (schemas/common.py, requests.py, responses.py)
 * et aux modèles du core (core/models/site.py).
 *
 * @module types/site
 */

// ============================================================================
// ENUMS & TYPES LITTÉRAUX
// ============================================================================

/**
 * Code langue ISO 639-1 supporté par les sites (ou 'multi').
 */
export type SiteLanguage = 
  | 'en' | 'fr' | 'es' | 'de' | 'it' | 'pt' | 'ru' 
  | 'ja' | 'ko' | 'zh' | 'ar' | 'pl' | 'tr' | 'nl' 
  | 'vi' | 'th' | 'id' | 'hi' | 'multi' | string;

/**
 * Statut de santé d'un site (utilisé par l'endpoint de health check).
 */
export type SiteHealthStatus = 'healthy' | 'degraded' | 'down' | 'unknown';

/**
 * Critères de tri pour la liste des sites.
 */
export type SiteSortBy = 'name' | 'language' | 'priority' | 'status';

// ============================================================================
// INTERFACES PRINCIPALES (Modèles de domaine)
// ============================================================================

/**
 * Représente les capacités techniques et les limitations d'un site.
 * Correspond au modèle `SiteCapabilities` du backend.
 */
export interface SiteCapabilities {
  /** Le site supporte-t-il la recherche de mangas ? */
  supports_search: boolean;
  
  /** Le site supporte-t-il le téléchargement direct ? */
  supports_download: boolean;
  
  /** Le site nécessite-t-il un contournement de Cloudflare (ex: FlareSolverr) ? */
  requires_cloudflare_bypass: boolean;
  
  /** Le site nécessite-t-il une authentification (compte utilisateur) ? */
  requires_auth: boolean;
  
  /** Le site nécessite-t-il un rendu JavaScript (ex: Playwright) ? */
  requires_javascript_rendering: boolean;
  
  /** Nombre maximal de requêtes simultanées autorisées par ce site. */
  max_concurrent_requests: number;
  
  /** Limite de débit imposée par le site (requêtes par minute). */
  rate_limit_requests_per_minute: number;
}

/**
 * Représente une source de manga (site web / parser).
 * Correspond aux modèles `SiteBase` et `SiteResponse` du backend.
 */
export interface Site {
  /** Identifiant unique du site (ex: "mangadex", "asurascans"). */
  id: string;
  
  /** Nom affiché du site (ex: "MangaDex"). */
  name: string;
  
  /** Langue principale du contenu du site. */
  language: SiteLanguage;
  
  /** Liste des domaines/miroirs officiels du site. */
  domains: string[];
  
  /** Indique si le site est activé pour la recherche et le téléchargement. */
  enabled: boolean;
  
  /** Indique si le site contient du contenu pour adultes (NSFW). */
  adult: boolean;
  
  /** Capacités techniques et limitations du site. */
  capabilities: SiteCapabilities;
  
  /** Priorité du site dans les résultats de recherche multi-sites (plus haut = prioritaire). */
  priority: number;
  
  /** Tags descriptifs (ex: ["official", "fast", "high-quality", "aggregator"]). */
  tags: string[];
  
  /** Date de dernière mise à jour des métadonnées du site (ISO 8601). */
  last_updated_at?: string | null;
}

/**
 * Représente le résultat d'un test de santé (ping) d'un site.
 * Correspond au modèle `SiteHealthResponse` du backend.
 */
export interface SiteHealth {
  /** Identifiant du site testé. */
  site_id: string;
  
  /** Statut de santé actuel. */
  status: SiteHealthStatus;
  
  /** Temps de réponse en millisecondes. */
  latency_ms: number;
  
  /** Message descriptif (ex: "Site is operational", "Cloudflare challenge detected"). */
  message: string;
  
  /** Détails techniques additionnels (ex: nombre de sites actifs dans le registre). */
  details?: Record<string, unknown>;
}

// ============================================================================
// INTERFACES DE REQUÊTE ET RÉPONSE API
// ============================================================================

/**
 * Paramètres de filtrage pour la liste des sites.
 * Correspond au modèle `SiteFilterRequest` du backend.
 */
export interface SiteFilterParams {
  /** Filtrer par langue spécifique. */
  language?: SiteLanguage | null;
  
  /** Ne retourner que les sites activés. */
  enabled_only?: boolean;
  
  /** Inclure les sites pour adultes dans les résultats. */
  include_adult?: boolean;
  
  /** Filtrer par tags spécifiques. */
  tags?: string[] | null;
}

/**
 * Paramètres de requête pour la liste des sites (incluant pagination et tri).
 */
export interface SiteListParams extends SiteFilterParams {
  /** Champ de tri. */
  sort_by?: SiteSortBy;
  
  /** Ordre de tri. */
  sort_order?: 'asc' | 'desc';
  
  /** Numéro de page (si paginé). */
  page?: number;
  
  /** Taille de la page (si paginé). */
  page_size?: number;
}

/**
 * Réponse de l'API pour la liste des sites.
 */
export interface SiteListResponse {
  sites: Site[];
  total: number;
  filtered_count: number;
}

// ============================================================================
// TYPES POUR LE STATE MANAGEMENT (Zustand / Redux)
// ============================================================================

/**
 * État local du store des sites.
 */
export interface SiteStoreState {
  /** Liste complète des sites disponibles. */
  sites: Site[];
  
  /** Cache des résultats de health check par site_id. */
  healthChecks: Record<string, SiteHealth>;
  
  /** Indique si la liste des sites est en cours de chargement. */
  isLoading: boolean;
  
  /** Indique si un health check est en cours d'exécution. */
  isCheckingHealth: boolean;
  
  /** Dernier message d'erreur global, ou null. */
  error: string | null;
  
  /** Actions du store. */
  actions: {
    fetchSites: (params?: SiteListParams) => Promise<void>;
    checkSiteHealth: (siteId: string) => Promise<SiteHealth>;
    checkAllSitesHealth: () => Promise<void>;
    toggleSiteEnabled: (siteId: string, enabled: boolean) => Promise<void>;
    clearHealthCache: () => void;
    clearError: () => void;
  };
}

// ============================================================================
// FONCTIONS UTILITAIRES DE FORMATAGE ET D'AFFICHAGE
// ============================================================================

/**
 * Retourne le drapeau emoji correspondant à un code langue.
 */
export function getLanguageFlag(language: SiteLanguage): string {
  const flags: Record<string, string> = {
    en: '🇬🇧',
    fr: '🇫🇷',
    es: '🇪🇸',
    de: '🇩🇪',
    it: '🇮🇹',
    pt: '🇵🇹',
    ru: '🇷🇺',
    ja: '🇯🇵',
    ko: '🇰🇷',
    zh: '🇨🇳',
    ar: '🇸🇦',
    pl: '🇵🇱',
    tr: '🇹🇷',
    nl: '🇳🇱',
    vi: '🇻🇳',
    th: '🇹🇭',
    id: '🇮🇩',
    hi: '🇮🇳',
    multi: '🌍',
  };
  return flags[language.toLowerCase()] || '🏳️';
}

/**
 * Retourne le nom lisible de la langue.
 */
export function getLanguageName(language: SiteLanguage): string {
  const names: Record<string, string> = {
    en: 'English',
    fr: 'Français',
    es: 'Español',
    de: 'Deutsch',
    it: 'Italiano',
    pt: 'Português',
    ru: 'Русский',
    ja: '日本語',
    ko: '한국어',
    zh: '中文',
    ar: 'العربية',
    pl: 'Polski',
    tr: 'Türkçe',
    nl: 'Nederlands',
    vi: 'Tiếng Việt',
    th: 'ไทย',
    id: 'Bahasa Indonesia',
    hi: 'हिन्दी',
    multi: 'Multilingual',
  };
  return names[language.toLowerCase()] || language.toUpperCase();
}

/**
 * Retourne les classes Tailwind CSS pour un badge de statut de santé.
 */
export function getHealthStatusColor(status: SiteHealthStatus): string {
  switch (status) {
    case 'healthy':
      return 'text-green-400 bg-green-400/10 border-green-400/20';
    case 'degraded':
      return 'text-yellow-400 bg-yellow-400/10 border-yellow-400/20';
    case 'down':
      return 'text-red-400 bg-red-400/10 border-red-400/20';
    case 'unknown':
    default:
      return 'text-gray-400 bg-gray-400/10 border-gray-400/20';
  }
}

/**
 * Retourne une icône (emoji) pour le statut de santé.
 */
export function getHealthStatusIcon(status: SiteHealthStatus): string {
  switch (status) {
    case 'healthy': return '✅';
    case 'degraded': return '⚠️';
    case 'down': return '❌';
    case 'unknown': return '❓';
  }
}

/**
 * Formate la limite de débit pour l'affichage (ex: "60 req/min").
 */
export function formatRateLimit(rpm: number): string {
  if (rpm <= 0) return 'Unlimited';
  return `${rpm} req/min`;
}

/**
 * Génère une liste de badges de fonctionnalités pour un site.
 */
export function getSiteFeatureBadges(capabilities: SiteCapabilities): string[] {
  const badges: string[] = [];
  if (capabilities.requires_cloudflare_bypass) badges.push('🛡️ Cloudflare');
  if (capabilities.requires_auth) badges.push('🔐 Auth Required');
  if (capabilities.requires_javascript_rendering) badges.push('⚡ JS Render');
  if (capabilities.supports_download) badges.push('⬇️ Downloadable');
  return badges;
}

/**
 * Extrait le domaine principal d'une liste de domaines pour l'affichage.
 */
export function getPrimaryDomain(domains: string[]): string {
  if (domains.length === 0) return 'Unknown';
  // Prend le premier domaine et enlève le protocole s'il est présent
  let domain = domains[0];
  if (domain.startsWith('http://') || domain.startsWith('https://')) {
    try {
      domain = new URL(domain).hostname;
    } catch {
      // Fallback si l'URL est malformée
    }
  }
  return domain.replace('www.', '');
}
