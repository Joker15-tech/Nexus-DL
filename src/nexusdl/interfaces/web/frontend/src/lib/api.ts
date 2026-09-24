/**
 * Client API REST pour communiquer avec le backend FastAPI de NexusDL.
 *
 * Ce module fournit un client HTTP robuste et type-safe avec :
 *   - Authentification JWT automatique (avec refresh token)
 *   - Gestion des erreurs typée
 *   - Retry automatique sur erreurs 5xx/réseau
 *   - Request/Response interceptors
 *   - Timeout configurable
 *   - Support des requêtes annulables (AbortController)
 *   - Intégration avec les stores Zustand
 *
 * Architecture :
 *   lib/api.ts
 *   ├── ApiError          : Classe d'erreur typée
 *   ├── ApiClient         : Client HTTP principal
 *   ├── api (instance)    : Instance globale pré-configurée
 *   ├── Auth helpers      : Gestion des tokens JWT
 *   └── Type helpers      : Generics pour les réponses
 *
 * Utilisation :
 *   import { api } from '@/lib/api';
 *
 *   // GET avec typage
 *   const mangas = await api.get<Manga[]>('/mangas');
 *
 *   // POST avec body
 *   const task = await api.post<DownloadTask>('/downloads', {
 *     site_id: 'mangadex',
 *     manga_id: '123',
 *   });
 *
 *   // Requêtes annulables
 *   const controller = new AbortController();
 *   const data = await api.get('/data', { signal: controller.signal });
 *   controller.abort(); // Annule la requête
 *
 * Configuration :
 *   NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
 *   NEXT_PUBLIC_API_TIMEOUT=30000
 *
 * @module lib/api
 */

// ============================================================================
// CONFIGURATION
// ============================================================================

/**
 * Configuration de l'API client.
 */
export interface ApiConfig {
  /** URL de base de l'API (ex: "http://localhost:8000/api/v1"). */
  baseURL: string;
  
  /** Timeout par défaut en millisecondes. */
  timeout: number;
  
  /** Headers par défaut ajoutés à toutes les requêtes. */
  defaultHeaders: Record<string, string>;
  
  /** Nombre maximum de tentatives en cas d'erreur. */
  maxRetries: number;
  
  /** Délai entre les tentatives (ms). */
  retryDelay: number;
  
  /** Activer le logging des requêtes. */
  enableLogging: boolean;
  
  /** Fonction pour récupérer le token d'accès. */
  getAccessToken?: () => string | null;
  
  /** Fonction pour rafraîchir le token. */
  refreshAccessToken?: () => Promise<string>;
  
  /** Fonction appelée quand l'authentification échoue (401). */
  onUnauthorized?: () => void;
}

/**
 * Configuration par défaut.
 */
const DEFAULT_CONFIG: ApiConfig = {
  baseURL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1',
  timeout: parseInt(process.env.NEXT_PUBLIC_API_TIMEOUT || '30000', 10),
  defaultHeaders: {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
  },
  maxRetries: 3,
  retryDelay: 1000,
  enableLogging: process.env.NODE_ENV === 'development',
};

// ============================================================================
// TYPES
// ============================================================================

/**
 * Méthodes HTTP supportées.
 */
export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD' | 'OPTIONS';

/**
 * Options de requête HTTP.
 */
export interface RequestOptions<TBody = unknown> extends Omit<RequestInit, 'method' | 'body'> {
  /** Corps de la requête (sera automatiquement sérialisé en JSON). */
  body?: TBody;
  
  /** Timeout spécifique pour cette requête (ms). */
  timeout?: number;
  
  /** Nombre de tentatives spécifiques pour cette requête. */
  retries?: number;
  
  /** Paramètres de query (seront ajoutés à l'URL). */
  params?: Record<string, string | number | boolean | undefined | null>;
  
  /** Headers spécifiques pour cette requête. */
  headers?: Record<string, string>;
  
  /** Ne pas ajouter le token d'authentification. */
  skipAuth?: boolean;
  
  /** Ne pas retry automatiquement. */
  skipRetry?: boolean;
  
  /** Fonction de progression pour les uploads. */
  onUploadProgress?: (progress: number) => void;
  
  /** Fonction de progression pour les downloads. */
  onDownloadProgress?: (progress: number) => void;
}

/**
 * Réponse API typée.
 */
export interface ApiResponse<T> {
  /** Données de la réponse. */
  data: T;
  
  /** Code de statut HTTP. */
  status: number;
  
  /** Headers de la réponse. */
  headers: Headers;
  
  /** Temps de traitement (ms). */
  duration: number;
}

/**
 * Structure d'erreur retournée par l'API backend.
 */
export interface ApiErrorPayload {
  error: string;
  message: string;
  details?: Record<string, unknown>;
  meta?: {
    timestamp: string;
    request_id?: string;
    api_version?: string;
  };
}

/**
 * Intercepteur de requête.
 */
export type RequestInterceptor = (
  config: RequestConfig
) => RequestConfig | Promise<RequestConfig>;

/**
 * Intercepteur de réponse.
 */
export type ResponseInterceptor = (
  response: Response,
  config: RequestConfig
) => Response | Promise<Response>;

/**
 * Intercepteur d'erreur.
 */
export type ErrorInterceptor = (
  error: ApiError,
  config: RequestConfig
) => void | Promise<void>;

/**
 * Configuration interne de requête (après résolution des options).
 */
export interface RequestConfig {
  url: string;
  method: HttpMethod;
  headers: Record<string, string>;
  body?: string;
  timeout: number;
  retries: number;
  signal?: AbortSignal;
  skipAuth: boolean;
  skipRetry: boolean;
}

// ============================================================================
// CLASSE D'ERREUR
// ============================================================================

/**
 * Erreur API typée.
 *
 * Cette classe encapsule toutes les erreurs pouvant survenir lors d'un appel API :
 *   - Erreurs HTTP (4xx, 5xx)
 *   - Erreurs réseau (pas de connexion, timeout)
 *   - Erreurs de parsing
 *   - Erreurs d'authentification
 */
export class ApiError extends Error {
  /** Code de statut HTTP (si applicable). */
  public readonly status: number | null;
  
  /** Payload d'erreur retourné par l'API. */
  public readonly payload: ApiErrorPayload | null;
  
  /** Code d'erreur machine-readable. */
  public readonly code: string;
  
  /** Détails additionnels de l'erreur. */
  public readonly details: Record<string, unknown>;
  
  /** URL de la requête ayant échoué. */
  public readonly url: string;
  
  /** Méthode HTTP de la requête. */
  public readonly method: string;
  
  /** Indique si c'est une erreur de timeout. */
  public readonly isTimeout: boolean;
  
  /** Indique si c'est une erreur réseau. */
  public readonly isNetworkError: boolean;
  
  /** Indique si c'est une erreur d'authentification. */
  public readonly isUnauthorized: boolean;
  
  /** Indique si c'est une erreur de permission. */
  public readonly isForbidden: boolean;
  
  /** Indique si c'est une erreur de validation. */
  public readonly isValidationError: boolean;
  
  /** Indique si l'erreur est retryable. */
  public readonly isRetryable: boolean;

  constructor(
    message: string,
    options: {
      status?: number | null;
      payload?: ApiErrorPayload | null;
      code?: string;
      details?: Record<string, unknown>;
      url?: string;
      method?: string;
      isTimeout?: boolean;
      isNetworkError?: boolean;
    } = {}
  ) {
    super(message);
    this.name = 'ApiError';
    
    this.status = options.status ?? null;
    this.payload = options.payload ?? null;
    this.code = options.code ?? options.payload?.error ?? 'unknown_error';
    this.details = options.details ?? options.payload?.details ?? {};
    this.url = options.url ?? '';
    this.method = options.method ?? '';
    this.isTimeout = options.isTimeout ?? false;
    this.isNetworkError = options.isNetworkError ?? false;
    this.isUnauthorized = this.status === 401;
    this.isForbidden = this.status === 403;
    this.isValidationError = this.status === 422;
    this.isRetryable = this.isNetworkError || this.isTimeout || (this.status !== null && this.status >= 500);
    
    // Maintient la stack trace correcte
    if (Error.captureStackTrace) {
      Error.captureStackTrace(this, ApiError);
    }
  }

  /**
   * Crée une ApiError depuis une réponse HTTP.
   */
  static async fromResponse(response: Response, url: string, method: string): Promise<ApiError> {
    let payload: ApiErrorPayload | null = null;
    
    try {
      payload = await response.json();
    } catch {
      // Si le parsing échoue, on crée un payload minimal
      payload = {
        error: 'unknown_error',
        message: response.statusText || 'Unknown error',
      };
    }
    
    return new ApiError(payload?.message || `HTTP ${response.status}`, {
      status: response.status,
      payload,
      url,
      method,
    });
  }

  /**
   * Crée une ApiError pour un timeout.
   */
  static timeout(url: string, method: string, timeout: number): ApiError {
    return new ApiError(`Request timeout after ${timeout}ms`, {
      code: 'timeout',
      url,
      method,
      isTimeout: true,
    });
  }

  /**
   * Crée une ApiError pour une erreur réseau.
   */
  static network(url: string, method: string, cause?: Error): ApiError {
    return new ApiError(cause?.message || 'Network error', {
      code: 'network_error',
      url,
      method,
      isNetworkError: true,
    });
  }

  /**
   * Convertit l'erreur en JSON pour le logging.
   */
  toJSON() {
    return {
      name: this.name,
      message: this.message,
      code: this.code,
      status: this.status,
      url: this.url,
      method: this.method,
      details: this.details,
      isTimeout: this.isTimeout,
      isNetworkError: this.isNetworkError,
      isUnauthorized: this.isUnauthorized,
      isRetryable: this.isRetryable,
    };
  }
}

// ============================================================================
// AUTH HELPERS
// ============================================================================

/**
 * Clés de stockage pour les tokens.
 */
const TOKEN_KEYS = {
  ACCESS: 'nexusdl_access_token',
  REFRESH: 'nexusdl_refresh_token',
  EXPIRES: 'nexusdl_token_expires',
} as const;

/**
 * Définit le token d'accès.
 */
export function setAccessToken(token: string, expiresAt?: number): void {
  if (typeof window === 'undefined') return;
  
  localStorage.setItem(TOKEN_KEYS.ACCESS, token);
  if (expiresAt) {
    localStorage.setItem(TOKEN_KEYS.EXPIRES, expiresAt.toString());
  }
}

/**
 * Récupère le token d'accès.
 */
export function getAccessToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(TOKEN_KEYS.ACCESS);
}

/**
 * Définit le refresh token.
 */
export function setRefreshToken(token: string): void {
  if (typeof window === 'undefined') return;
  localStorage.setItem(TOKEN_KEYS.REFRESH, token);
}

/**
 * Récupère le refresh token.
 */
export function getRefreshToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(TOKEN_KEYS.REFRESH);
}

/**
 * Vérifie si le token est expiré.
 */
export function isTokenExpired(): boolean {
  if (typeof window === 'undefined') return true;
  
  const expires = localStorage.getItem(TOKEN_KEYS.EXPIRES);
  if (!expires) return true;
  
  const expiresAt = parseInt(expires, 10);
  // Considérer le token comme expiré 30 secondes avant l'expiration réelle
  return Date.now() >= (expiresAt - 30000);
}

/**
 * Efface tous les tokens.
 */
export function clearTokens(): void {
  if (typeof window === 'undefined') return;
  
  localStorage.removeItem(TOKEN_KEYS.ACCESS);
  localStorage.removeItem(TOKEN_KEYS.REFRESH);
  localStorage.removeItem(TOKEN_KEYS.EXPIRES);
}

// ============================================================================
// CLIENT API
// ============================================================================

/**
 * Client HTTP pour l'API REST NexusDL.
 */
export class ApiClient {
  private config: ApiConfig;
  private requestInterceptors: RequestInterceptor[] = [];
  private responseInterceptors: ResponseInterceptor[] = [];
  private errorInterceptors: ErrorInterceptor[] = [];
  private pendingRequests: Map<string, Promise<unknown>> = new Map();
  private refreshPromise: Promise<string> | null = null;

  constructor(config: Partial<ApiConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    
    // Configurer les fonctions d'auth par défaut
    if (!this.config.getAccessToken) {
      this.config.getAccessToken = getAccessToken;
    }
  }

  /**
   * Met à jour la configuration.
   */
  setConfig(config: Partial<ApiConfig>): void {
    this.config = { ...this.config, ...config };
  }

  /**
   * Ajoute un intercepteur de requête.
   */
  addRequestInterceptor(interceptor: RequestInterceptor): () => void {
    this.requestInterceptors.push(interceptor);
    return () => {
      this.requestInterceptors = this.requestInterceptors.filter((i) => i !== interceptor);
    };
  }

  /**
   * Ajoute un intercepteur de réponse.
   */
  addResponseInterceptor(interceptor: ResponseInterceptor): () => void {
    this.responseInterceptors.push(interceptor);
    return () => {
      this.responseInterceptors = this.responseInterceptors.filter((i) => i !== interceptor);
    };
  }

  /**
   * Ajoute un intercepteur d'erreur.
   */
  addErrorInterceptor(interceptor: ErrorInterceptor): () => void {
    this.errorInterceptors.push(interceptor);
    return () => {
      this.errorInterceptors = this.errorInterceptors.filter((i) => i !== interceptor);
    };
  }

  /**
   * Construit l'URL complète avec les paramètres de query.
   */
  private buildUrl(path: string, params?: Record<string, string | number | boolean | undefined | null>): string {
    const url = new URL(path, this.config.baseURL);
    
    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          url.searchParams.append(key, String(value));
        }
      });
    }
    
    return url.toString();
  }

  /**
   * Applique les intercepteurs de requête.
   */
  private async applyRequestInterceptors(config: RequestConfig): Promise<RequestConfig> {
    let result = config;
    for (const interceptor of this.requestInterceptors) {
      result = await interceptor(result);
    }
    return result;
  }

  /**
   * Applique les intercepteurs de réponse.
   */
  private async applyResponseInterceptors(response: Response, config: RequestConfig): Promise<Response> {
    let result = response;
    for (const interceptor of this.responseInterceptors) {
      result = await interceptor(result, config);
    }
    return result;
  }

  /**
   * Applique les intercepteurs d'erreur.
   */
  private async applyErrorInterceptors(error: ApiError, config: RequestConfig): Promise<void> {
    for (const interceptor of this.errorInterceptors) {
      await interceptor(error, config);
    }
  }

  /**
   * Rafraîchit le token d'accès.
   */
  private async refreshToken(): Promise<string> {
    // Si un refresh est déjà en cours, attendre
    if (this.refreshPromise) {
      return this.refreshPromise;
    }

    this.refreshPromise = (async () => {
      try {
        const refreshTokenValue = getRefreshToken();
        if (!refreshTokenValue) {
          throw new Error('No refresh token available');
        }

        // Appeler l'endpoint de refresh directement (sans interceptor d'auth)
        const response = await fetch(`${this.config.baseURL}/auth/refresh`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ refresh_token: refreshTokenValue }),
        });

        if (!response.ok) {
          throw new Error('Failed to refresh token');
        }

        const data = await response.json();
        
        // Sauvegarder les nouveaux tokens
        setAccessToken(data.access_token, Date.now() + data.expires_in * 1000);
        setRefreshToken(data.refresh_token);

        return data.access_token;
      } finally {
        this.refreshPromise = null;
      }
    })();

    return this.refreshPromise;
  }

  /**
   * Effectue une requête HTTP avec retry et gestion d'erreurs.
   */
  private async request<T>(
    method: HttpMethod,
    path: string,
    options: RequestOptions = {}
  ): Promise<T> {
    const startTime = Date.now();
    
    // Construire la configuration de requête
    let config: RequestConfig = {
      url: this.buildUrl(path, options.params),
      method,
      headers: {
        ...this.config.defaultHeaders,
        ...options.headers,
      },
      body: options.body ? JSON.stringify(options.body) : undefined,
      timeout: options.timeout ?? this.config.timeout,
      retries: options.retries ?? this.config.maxRetries,
      signal: options.signal,
      skipAuth: options.skipAuth ?? false,
      skipRetry: options.skipRetry ?? false,
    };

    // Ajouter le token d'authentification
    if (!config.skipAuth && this.config.getAccessToken) {
      let token = this.config.getAccessToken();
      
      // Vérifier si le token est expiré et le rafraîchir si nécessaire
      if (token && isTokenExpired() && this.config.refreshAccessToken) {
        try {
          token = await this.refreshToken();
        } catch (error) {
          // Si le refresh échoue, effacer les tokens
          clearTokens();
          if (this.config.onUnauthorized) {
            this.config.onUnauthorized();
          }
          throw new ApiError('Authentication failed', {
            code: 'auth_failed',
            status: 401,
          });
        }
      }
      
      if (token) {
        config.headers['Authorization'] = `Bearer ${token}`;
      }
    }

    // Appliquer les intercepteurs de requête
    config = await this.applyRequestInterceptors(config);

    // Logging
    if (this.config.enableLogging) {
      console.debug(`[API] ${config.method} ${config.url}`, {
        headers: config.headers,
        body: config.body,
      });
    }

    // Créer une clé unique pour la déduplication (sauf pour les méthodes non-idempotentes)
    const dedupeKey = method === 'GET' ? `${config.method}:${config.url}` : null;
    
    // Vérifier si une requête identique est déjà en cours
    if (dedupeKey && this.pendingRequests.has(dedupeKey)) {
      return this.pendingRequests.get(dedupeKey) as Promise<T>;
    }

    // Fonction de requête avec retry
    const executeRequest = async (attempt: number = 0): Promise<T> => {
      try {
        // Créer un AbortController pour le timeout
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), config.timeout);

        // Combiner les signaux (timeout + option.signal)
        const signal = config.signal
          ? AbortSignal.any([controller.signal, config.signal])
          : controller.signal;

        const response = await fetch(config.url, {
          method: config.method,
          headers: config.headers,
          body: config.body,
          signal,
        });

        clearTimeout(timeoutId);

        // Appliquer les intercepteurs de réponse
        const processedResponse = await this.applyResponseInterceptors(response, config);

        // Gérer les erreurs HTTP
        if (!processedResponse.ok) {
          // Si 401 et qu'on a un refresh token, essayer de rafraîchir
          if (processedResponse.status === 401 && !config.skipAuth && getRefreshToken()) {
            try {
              const newToken = await this.refreshToken();
              config.headers['Authorization'] = `Bearer ${newToken}`;
              
              // Retry la requête avec le nouveau token
              return executeRequest(attempt);
            } catch {
              // Si le refresh échoue, effacer les tokens et throw
              clearTokens();
              if (this.config.onUnauthorized) {
                this.config.onUnauthorized();
              }
            }
          }

          const error = await ApiError.fromResponse(processedResponse, config.url, config.method);
          
          // Retry si l'erreur est retryable et qu'on n'a pas dépassé le nombre de tentatives
          if (error.isRetryable && !config.skipRetry && attempt < config.retries) {
            const delay = this.config.retryDelay * Math.pow(2, attempt); // Exponential backoff
            await new Promise((resolve) => setTimeout(resolve, delay));
            return executeRequest(attempt + 1);
          }

          // Appliquer les intercepteurs d'erreur
          await this.applyErrorInterceptors(error, config);
          
          throw error;
        }

        // Parser la réponse
        let data: T;
        const contentType = processedResponse.headers.get('content-type');
        
        if (contentType?.includes('application/json')) {
          data = await processedResponse.json();
        } else if (processedResponse.status === 204) {
          // No content
          data = undefined as T;
        } else {
          // Texte ou autre
          data = (await processedResponse.text()) as unknown as T;
        }

        const duration = Date.now() - startTime;

        // Logging
        if (this.config.enableLogging) {
          console.debug(`[API] ${config.method} ${config.url} → ${processedResponse.status} (${duration}ms)`);
        }

        return data;
      } catch (error) {
        // Gérer les erreurs réseau/timeout
        if (error instanceof ApiError) {
          throw error;
        }

        if (error instanceof Error) {
          if (error.name === 'AbortError') {
            // Vérifier si c'est un timeout ou une annulation manuelle
            if (config.signal?.aborted) {
              throw new ApiError('Request cancelled', {
                code: 'cancelled',
                url: config.url,
                method: config.method,
              });
            }
            throw ApiError.timeout(config.url, config.method, config.timeout);
          }

          // Erreur réseau
          const networkError = ApiError.network(config.url, config.method, error);
          
          // Retry si possible
          if (!config.skipRetry && attempt < config.retries) {
            const delay = this.config.retryDelay * Math.pow(2, attempt);
            await new Promise((resolve) => setTimeout(resolve, delay));
            return executeRequest(attempt + 1);
          }

          throw networkError;
        }

        throw error;
      }
    };

    // Exécuter la requête
    const requestPromise = executeRequest();

    // Stocker pour déduplication
    if (dedupeKey) {
      this.pendingRequests.set(dedupeKey, requestPromise);
      
      // Nettoyer après completion
      requestPromise.finally(() => {
        this.pendingRequests.delete(dedupeKey!);
      });
    }

    return requestPromise;
  }

  // ========================================================================
  // MÉTHODES HTTP
  // ========================================================================

  /**
   * Effectue une requête GET.
   *
   * @example
   * ```ts
   * const mangas = await api.get<Manga[]>('/mangas', {
   *   params: { page: 1, page_size: 20 },
   * });
   * ```
   */
  async get<T>(path: string, options?: RequestOptions): Promise<T> {
    return this.request<T>('GET', path, options);
  }

  /**
   * Effectue une requête POST.
   *
   * @example
   * ```ts
   * const task = await api.post<DownloadTask>('/downloads', {
   *   site_id: 'mangadex',
   *   manga_id: '123',
   * });
   * ```
   */
  async post<T, TBody = unknown>(path: string, body?: TBody, options?: RequestOptions<TBody>): Promise<T> {
    return this.request<T>('POST', path, { ...options, body });
  }

  /**
   * Effectue une requête PUT.
   */
  async put<T, TBody = unknown>(path: string, body?: TBody, options?: RequestOptions<TBody>): Promise<T> {
    return this.request<T>('PUT', path, { ...options, body });
  }

  /**
   * Effectue une requête PATCH.
   */
  async patch<T, TBody = unknown>(path: string, body?: TBody, options?: RequestOptions<TBody>): Promise<T> {
    return this.request<T>('PATCH', path, { ...options, body });
  }

  /**
   * Effectue une requête DELETE.
   */
  async delete<T>(path: string, options?: RequestOptions): Promise<T> {
    return this.request<T>('DELETE', path, options);
  }

  /**
   * Effectue une requête HEAD.
   */
  async head(path: string, options?: RequestOptions): Promise<Headers> {
    const response = await this.request<Response>('HEAD', path, options);
    return response.headers;
  }

  // ========================================================================
  // MÉTHODES SPÉCIALISÉES
  // ========================================================================

  /**
   * Télécharge un fichier et retourne un Blob.
   *
   * @example
   * ```ts
   * const blob = await api.download('/manga/123/cover');
   * const url = URL.createObjectURL(blob);
   * ```
   */
  async download(path: string, options?: RequestOptions): Promise<Blob> {
    const config: RequestConfig = {
      url: this.buildUrl(path, options?.params),
      method: 'GET',
      headers: {
        ...this.config.defaultHeaders,
        ...options?.headers,
        'Accept': '*/*',
      },
      timeout: options?.timeout ?? this.config.timeout * 2, // Plus de temps pour les downloads
      retries: options?.retries ?? this.config.maxRetries,
      signal: options?.signal,
      skipAuth: options?.skipAuth ?? false,
      skipRetry: options?.skipRetry ?? false,
    };

    // Ajouter le token d'auth
    if (!config.skipAuth && this.config.getAccessToken) {
      const token = this.config.getAccessToken();
      if (token) {
        config.headers['Authorization'] = `Bearer ${token}`;
      }
    }

    const response = await fetch(config.url, {
      method: config.method,
      headers: config.headers,
      signal: config.signal,
    });

    if (!response.ok) {
      throw await ApiError.fromResponse(response, config.url, config.method);
    }

    return response.blob();
  }

  /**
   * Upload un fichier avec progression.
   *
   * @example
   * ```ts
   * const formData = new FormData();
   * formData.append('file', file);
   * 
   * await api.upload('/upload', formData, {
   *   onUploadProgress: (progress) => console.log(`${progress}%`),
   * });
   * ```
   */
  async upload<T>(
    path: string,
    formData: FormData,
    options?: RequestOptions
  ): Promise<T> {
    const config: RequestConfig = {
      url: this.buildUrl(path, options?.params),
      method: 'POST',
      headers: {
        ...options?.headers,
        // Ne pas définir Content-Type, le navigateur le fera automatiquement
      },
      body: undefined, // Sera défini ci-dessous
      timeout: options?.timeout ?? this.config.timeout * 2,
      retries: options?.retries ?? 0, // Pas de retry pour les uploads
      signal: options?.signal,
      skipAuth: options?.skipAuth ?? false,
      skipRetry: true,
    };

    // Ajouter le token d'auth
    if (!config.skipAuth && this.config.getAccessToken) {
      const token = this.config.getAccessToken();
      if (token) {
        config.headers['Authorization'] = `Bearer ${token}`;
      }
    }

    const response = await fetch(config.url, {
      method: config.method,
      headers: config.headers,
      body: formData,
      signal: config.signal,
    });

    if (!response.ok) {
      throw await ApiError.fromResponse(response, config.url, config.method);
    }

    return response.json();
  }

  /**
   * Crée un AbortController pour annuler des requêtes.
   *
   * @example
   * ```ts
   * const controller = api.createAbortController();
   * 
   * // Annuler après 5 secondes
   * setTimeout(() => controller.abort(), 5000);
   * 
   * try {
   *   await api.get('/slow-endpoint', { signal: controller.signal });
   * } catch (error) {
   *   if (error.code === 'cancelled') {
   *     console.log('Request cancelled');
   *   }
   * }
   * ```
   */
  createAbortController(): AbortController {
    return new AbortController();
  }

  /**
   * Annule toutes les requêtes GET en cours.
   */
  cancelAllRequests(): void {
    this.pendingRequests.clear();
  }
}

// ============================================================================
// INSTANCE GLOBALE
// ============================================================================

/**
 * Instance globale du client API pré-configurée.
 *
 * @example
 * ```ts
 * import { api } from '@/lib/api';
 *
 * const data = await api.get('/endpoint');
 * ```
 */
export const api = new ApiClient();

/**
 * Configure l'instance globale de l'API.
 *
 * @example
 * ```ts
 * configureApi({
 *   baseURL: 'https://api.nexusdl.dev/v1',
 *   timeout: 60000,
 *   onUnauthorized: () => {
 *     router.push('/login');
 *   },
 * });
 * ```
 */
export function configureApi(config: Partial<ApiConfig>): void {
  api.setConfig(config);
}

// ============================================================================
// HELPERS
// ============================================================================

/**
 * Vérifie si une erreur est une ApiError.
 */
export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/**
 * Extrait le message d'erreur d'une ApiError ou d'une Error standard.
 */
export function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.payload?.message || error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

/**
 * Extrait le code d'erreur d'une ApiError.
 */
export function getErrorCode(error: unknown): string {
  if (error instanceof ApiError) {
    return error.code;
  }
  return 'unknown_error';
}

/**
 * Attend un délai avant de retry (exponential backoff).
 */
export function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Crée une fonction de retry avec exponential backoff.
 *
 * @example
 * ```ts
 * const fetchWithRetry = withRetry(() => api.get('/unstable-endpoint'), {
 *   maxRetries: 3,
 *   initialDelay: 1000,
 * });
 * 
 * const data = await fetchWithRetry();
 * ```
 */
export function withRetry<T>(
  fn: () => Promise<T>,
  options: {
    maxRetries?: number;
    initialDelay?: number;
    shouldRetry?: (error: unknown) => boolean;
  } = {}
): () => Promise<T> {
  const {
    maxRetries = 3,
    initialDelay = 1000,
    shouldRetry = (error) => error instanceof ApiError && error.isRetryable,
  } = options;

  return async () => {
    let lastError: unknown;
    
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        return await fn();
      } catch (error) {
        lastError = error;
        
        if (attempt === maxRetries || !shouldRetry(error)) {
          throw error;
        }
        
        const delayMs = initialDelay * Math.pow(2, attempt);
        await delay(delayMs);
      }
    }
    
    throw lastError;
  };
}

// ============================================================================
// EXPORTS
// ============================================================================

export { ApiClient };
export type { ApiConfig, HttpMethod, RequestOptions, ApiResponse, RequestConfig };
