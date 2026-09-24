/**
 * Types et interfaces pour la gestion des mangas, chapitres et de la lecture
 * dans le frontend NexusDL.
 *
 * Ce fichier définit tous les types TypeScript correspondant aux schémas
 * Pydantic du backend FastAPI (schemas/common.py, requests.py, responses.py)
 * et aux modèles du core (core/models/manga.py, core/models/library.py).
 *
 * @module types/manga
 */

// ============================================================================
// ENUMS & TYPES LITTÉRAUX
// ============================================================================

/**
 * Statut de publication d'un manga.
 */
export type MangaStatus = 'ongoing' | 'completed' | 'hiatus' | 'cancelled' | 'unknown';

/**
 * Statut de lecture d'un manga dans la bibliothèque.
 */
export type ReadingStatus = 'reading' | 'completed' | 'plan_to_read' | 'on_hold' | 'dropped';

/**
 * Statut de lecture d'un chapitre individuel.
 */
export type ChapterReadStatus = 'unread' | 'reading' | 'read';

/**
 * Code langue ISO 639-1 (ou 'multi' pour multilingue).
 */
export type LanguageCode = 
  | 'en' | 'fr' | 'es' | 'de' | 'it' | 'pt' | 'ru' 
  | 'ja' | 'ko' | 'zh' | 'ar' | 'pl' | 'tr' | 'nl' 
  | 'vi' | 'th' | 'id' | 'hi' | 'multi' | string;

/**
 * Critères de tri pour les listes de mangas.
 */
export type MangaSortBy = 'title' | 'date_added' | 'last_read' | 'progress' | 'author' | 'status';

/**
 * Ordre de tri.
 */
export type SortOrder = 'asc' | 'desc';

// ============================================================================
// INTERFACES PRINCIPALES (Modèles de domaine)
// ============================================================================

/**
 * Représente la progression de lecture d'un manga.
 * Correspond au modèle `ReadingProgressBase` du backend.
 */
export interface ReadingProgress {
  /** Identifiant du manga. */
  manga_id: string;
  
  /** Statut de lecture global. */
  status: ReadingStatus;
  
  /** Nombre de chapitres marqués comme lus. */
  chapters_read: number;
  
  /** Nombre total de chapitres connus. */
  total_chapters: number;
  
  /** Page actuelle du dernier chapitre lu (pour la reprise). */
  current_page: number;
  
  /** Date et heure de la dernière lecture (ISO 8601). */
  last_read_at: string | null;
}

/**
 * Représente un manga avec ses métadonnées.
 * Correspond aux modèles `MangaBase` et `MangaDetailsResponse` du backend.
 */
export interface Manga {
  /** Identifiant unique du manga (spécifique au site). */
  id: string;
  
  /** Identifiant du site source. */
  site_id: string;
  
  /** Titre du manga. */
  title: string;
  
  /** Auteur / Artiste. */
  author: string;
  
  /** Année de début de publication. */
  year: number | null;
  
  /** Code langue principal. */
  language: LanguageCode;
  
  /** Statut de publication. */
  status: MangaStatus;
  
  /** URL de l'image de couverture. */
  cover_url: string;
  
  /** URL de la page du manga sur le site source. */
  url: string;
  
  /** Synopsis / Description. */
  description: string;
  
  /** Liste des tags / genres. */
  tags: string[];
  
  /** Nombre total de chapitres disponibles. */
  chapters_count: number;
  
  /** Date de dernière mise à jour des métadonnées (ISO 8601). */
  last_updated_at: string | null;
  
  /** Indique si le manga est présent dans la bibliothèque locale. */
  in_library: boolean;
  
  /** Statut de lecture dans la bibliothèque (si in_library est true). */
  reading_status: ReadingStatus | null;
  
  /** Progression de lecture détaillée (optionnelle, selon l'endpoint). */
  progress?: ReadingProgress;
}

/**
 * Représente un chapitre de manga.
 * Correspond au modèle `ChapterDetailResponse` du backend.
 */
export interface Chapter {
  /** Identifiant unique du chapitre. */
  id: string;
  
  /** Numéro du chapitre (peut être un décimal, ex: 123.5). */
  number: number;
  
  /** Titre du chapitre (souvent vide ou "Chapitre 123"). */
  title: string;
  
  /** Date de publication sur le site source (ISO 8601). */
  published_at: string | null;
  
  /** Nom du groupe de scanlation. */
  scanlator: string;
  
  /** Nombre total de pages dans ce chapitre. */
  pages_count: number;
  
  /** URL du chapitre sur le site source. */
  url: string;
  
  /** Indique si le chapitre est téléchargé localement. */
  is_downloaded: boolean;
  
  /** Indique si le chapitre est marqué comme lu. */
  is_read: boolean;
  
  /** Dernière page lue dans ce chapitre (pour la reprise). */
  current_page: number;
  
  /** Statut de lecture spécifique à ce chapitre. */
  read_status: ChapterReadStatus;
  
  /** Date et heure de la dernière lecture de ce chapitre (ISO 8601). */
  last_read_at: string | null;
}

/**
 * Représente une page d'un chapitre.
 * Correspond au modèle `PageResponse` du backend.
 */
export interface Page {
  /** Numéro de la page (1-indexed). */
  number: number;
  
  /** URL source de l'image (sur le site de scan). */
  url: string;
  
  /** Largeur de l'image en pixels (si connue). */
  width: number;
  
  /** Hauteur de l'image en pixels (si connue). */
  height: number;
  
  /** Taille du fichier en bytes (si connue). */
  size_bytes: number;
  
  /** Type MIME de l'image (ex: "image/jpeg", "image/webp"). */
  content_type: string;
  
  /** URL de l'endpoint API pour streamer l'image (avec gestion du cache/rate limit). */
  image_url: string;
}

// ============================================================================
// INTERFACES DE REQUÊTE ET RÉPONSE API
// ============================================================================

/**
 * Filtres pour la recherche de mangas.
 * Correspond au modèle `SearchFilters` du backend.
 */
export interface SearchFilters {
  /** Filtrer par langue. */
  language?: LanguageCode | null;
  
  /** Filtrer par statut de publication. */
  status?: MangaStatus | null;
  
  /** Inclure les contenus pour adultes. */
  include_adult?: boolean;
  
  /** Filtrer par tags (doit contenir tous les tags spécifiés). */
  tags?: string[] | null;
}

/**
 * Payload pour une requête de recherche multi-sites.
 * Correspond au modèle `SearchRequest` du backend.
 */
export interface SearchRequest {
  /** Terme de recherche. */
  query: string;
  
  /** Liste des IDs de sites à interroger. Si null, tous les sites sont utilisés. */
  site_ids?: string[] | null;
  
  /** Filtres de recherche. */
  filters?: SearchFilters;
  
  /** Champ de tri (ex: "relevance", "date", "title"). */
  sort_by?: string;
  
  /** Ordre de tri. */
  sort_order?: SortOrder;
  
  /** Numéro de page (1-indexed). */
  page: number;
  
  /** Nombre de résultats par page. */
  page_size: number;
  
  /** Timeout de la recherche en secondes. */
  timeout?: number;
}

/**
 * Élément de résultat de recherche.
 * Correspond au modèle `SearchResultItem` du backend.
 */
export interface SearchResultItem {
  manga_id: string;
  title: string;
  author: string;
  year: number | null;
  status: MangaStatus;
  language: LanguageCode;
  cover_url: string;
  url: string;
  site_id: string;
  site_name: string;
  /** Score de pertinence de la recherche (0.0 à 1.0). */
  score: number;
  description: string;
  tags: string[];
  chapters_count: number;
}

/**
 * Métadonnées de pagination retournées par l'API.
 */
export interface PaginationMeta {
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
  has_next: boolean;
  has_previous: boolean;
  next_page: number | null;
  previous_page: number | null;
}

/**
 * Réponse paginée standard de l'API.
 */
export interface PaginatedResponse<T> {
  status: 'success' | 'error' | 'partial';
  data: T[];
  pagination: PaginationMeta;
  message: string;
  meta?: {
    timestamp: string;
    request_id?: string;
    api_version: string;
    processing_time_ms?: number;
  };
}

// ============================================================================
// TYPES POUR LE STATE MANAGEMENT (Zustand / Redux)
// ============================================================================

/**
 * État local du store de mangas / bibliothèque.
 */
export interface MangaStoreState {
  /** Liste des mangas dans la bibliothèque (avec pagination). */
  library: Manga[];
  
  /** Manga actuellement consulté en détail. */
  currentManga: Manga | null;
  
  /** Liste des chapitres du manga actuellement consulté. */
  currentChapters: Chapter[];
  
  /** Résultats de la dernière recherche. */
  searchResults: SearchResultItem[];
  
  /** Indique si une opération est en cours. */
  isLoading: boolean;
  
  /** Dernier message d'erreur global, ou null. */
  error: string | null;
  
  /** Actions du store. */
  actions: {
    fetchLibrary: (params?: { page?: number; status?: ReadingStatus }) => Promise<void>;
    fetchMangaDetails: (mangaId: string, siteId: string) => Promise<void>;
    fetchChapters: (mangaId: string, siteId: string) => Promise<void>;
    search: (request: SearchRequest) => Promise<void>;
    updateReadingProgress: (mangaId: string, progress: Partial<ReadingProgress>) => Promise<void>;
    markChapterRead: (chapterId: string, isRead: boolean, currentPage?: number) => Promise<void>;
    addToLibrary: (mangaId: string, siteId: string, status: ReadingStatus) => Promise<void>;
    removeFromLibrary: (mangaId: string) => Promise<void>;
    clearCurrentManga: () => void;
    clearSearchResults: () => void;
    clearError: () => void;
  };
}

// ============================================================================
// FONCTIONS UTILITAIRES DE FORMATAGE ET D'AFFICHAGE
// ============================================================================

/**
 * Retourne une couleur Tailwind CSS correspondant au statut de publication.
 */
export function getMangaStatusColor(status: MangaStatus): string {
  switch (status) {
    case 'ongoing':
      return 'text-blue-500 bg-blue-500/10 border-blue-500/20';
    case 'completed':
      return 'text-green-500 bg-green-500/10 border-green-500/20';
    case 'hiatus':
      return 'text-yellow-500 bg-yellow-500/10 border-yellow-500/20';
    case 'cancelled':
      return 'text-red-500 bg-red-500/10 border-red-500/20';
    case 'unknown':
    default:
      return 'text-gray-500 bg-gray-500/10 border-gray-500/20';
  }
}

/**
 * Retourne une icône (emoji) correspondant au statut de publication.
 */
export function getMangaStatusIcon(status: MangaStatus): string {
  switch (status) {
    case 'ongoing': return '🔄';
    case 'completed': return '✅';
    case 'hiatus': return '⏸️';
    case 'cancelled': return '❌';
    case 'unknown': return '❓';
  }
}

/**
 * Retourne une couleur Tailwind CSS correspondant au statut de lecture.
 */
export function getReadingStatusColor(status: ReadingStatus): string {
  switch (status) {
    case 'reading':
      return 'text-blue-400 bg-blue-400/10';
    case 'completed':
      return 'text-green-400 bg-green-400/10';
    case 'plan_to_read':
      return 'text-purple-400 bg-purple-400/10';
    case 'on_hold':
      return 'text-yellow-400 bg-yellow-400/10';
    case 'dropped':
      return 'text-red-400 bg-red-400/10';
  }
}

/**
 * Formate un numéro de chapitre pour l'affichage (ex: 12.5 -> "Ch. 12.5").
 */
export function formatChapterNumber(num: number): string {
  // Si c'est un entier, on l'affiche sans décimale
  if (Number.isInteger(num)) {
    return `Ch. ${num}`;
  }
  return `Ch. ${num}`;
}

/**
 * Calcule le pourcentage de progression de lecture d'un manga.
 */
export function calculateReadingProgress(chaptersRead: number, totalChapters: number): number {
  if (totalChapters === 0) return 0;
  return Math.min(100, Math.round((chaptersRead / totalChapters) * 100));
}

/**
 * Tronque une description trop longue pour l'affichage dans les cartes.
 */
export function truncateDescription(description: string, maxLength: number = 120): string {
  if (description.length <= maxLength) return description;
  return description.slice(0, maxLength).trim() + '...';
}

/**
 * Extrait le nom du domaine à partir d'une URL de site source pour l'affichage.
 */
export function extractDomainFromUrl(url: string): string {
  try {
    const domain = new URL(url).hostname;
    return domain.replace('www.', '');
  } catch {
    return url;
  }
}
