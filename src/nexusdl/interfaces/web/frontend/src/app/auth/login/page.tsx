/**
 * Page de connexion pour NexusDL.
 *
 * Interface d'authentification avec :
 *   - Formulaire username/password
 *   - Validation en temps réel
 *   - Gestion des erreurs API
 *   - Stockage automatique des tokens JWT
 *   - Redirection après connexion
 *   - Lien vers inscription et mot de passe oublié
 *   - Option "Se souvenir de moi"
 *   - Mode alternatif avec API key
 *   - Style cyberpunk néon
 *   - Accessibilité complète
 *   - Responsive
 *
 * @module app/auth/login/page
 */

'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import {
  AlertCircle,
  Eye,
  EyeOff,
  Key,
  Loader2,
  Lock,
  Sparkles,
  User,
  Zap,
} from 'lucide-react';
import { toast } from 'sonner';

import { cn } from '@/lib/utils';
import { api, setAccessToken, setRefreshToken, clearTokens } from '@/lib/api';
import { useSettingsActions } from '@/store';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Mode d'authentification.
 */
type AuthMode = 'credentials' | 'apikey';

/**
 * Données du formulaire de connexion.
 */
interface LoginForm {
  username: string;
  password: string;
  rememberMe: boolean;
}

/**
 * Données du formulaire API key.
 */
interface ApiKeyForm {
  apiKey: string;
}

/**
 * Erreurs de validation.
 */
interface ValidationErrors {
  username?: string;
  password?: string;
  apiKey?: string;
  general?: string;
}

/**
 * Réponse de l'API login.
 */
interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user_id: string;
  username: string;
  roles: string[];
  session_id: string;
}

// ============================================================================
// CONSTANTES
// ============================================================================

/**
 * Durée de stockage du token si "Se souvenir de moi" est coché.
 */
const REMEMBER_ME_DURATION_DAYS = 30;

/**
 * Durée de stockage du token par défaut (session).
 */
const DEFAULT_DURATION_HOURS = 24;

// ============================================================================
// COMPOSANT PRINCIPAL
// ============================================================================

/**
 * LoginPage - Page de connexion avec formulaire et validation.
 *
 * @returns Élément JSX de la page de connexion
 */
export default function LoginPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // État du formulaire
  const [mode, setMode] = useState<AuthMode>('credentials');
  const [credentials, setCredentials] = useState<LoginForm>({
    username: '',
    password: '',
    rememberMe: false,
  });
  const [apiKeyForm, setApiKeyForm] = useState<ApiKeyForm>({
    apiKey: '',
  });

  // État UI
  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [errors, setErrors] = useState<ValidationErrors>({});

  // Récupérer l'URL de redirection
  const redirectTo = searchParams.get('redirect') || '/';

  // ==========================================================================
  // VALIDATION
  // ==========================================================================

  /**
   * Valide le formulaire de connexion.
   */
  const validateCredentials = useCallback((): boolean => {
    const newErrors: ValidationErrors = {};

    // Username
    if (!credentials.username.trim()) {
      newErrors.username = 'Username is required';
    } else if (credentials.username.length < 3) {
      newErrors.username = 'Username must be at least 3 characters';
    } else if (credentials.username.length > 50) {
      newErrors.username = 'Username must be less than 50 characters';
    }

    // Password
    if (!credentials.password) {
      newErrors.password = 'Password is required';
    } else if (credentials.password.length < 8) {
      newErrors.password = 'Password must be at least 8 characters';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }, [credentials]);

  /**
   * Valide le formulaire API key.
   */
  const validateApiKey = useCallback((): boolean => {
    const newErrors: ValidationErrors = {};

    if (!apiKeyForm.apiKey.trim()) {
      newErrors.apiKey = 'API key is required';
    } else if (apiKeyForm.apiKey.length < 16) {
      newErrors.apiKey = 'API key is too short';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }, [apiKeyForm]);

  // ==========================================================================
  // HANDLERS
  // ==========================================================================

  /**
   * Gère la soumission du formulaire de connexion.
   */
  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();

      // Valider selon le mode
      const isValid = mode === 'credentials' ? validateCredentials() : validateApiKey();
      if (!isValid) return;

      setIsLoading(true);
      setErrors({});

      try {
        if (mode === 'credentials') {
          // Connexion avec username/password
          const response = await api.post<LoginResponse>('/auth/login', {
            username: credentials.username,
            password: credentials.password,
          });

          // Stocker les tokens
          const expiresAt = credentials.rememberMe
            ? Date.now() + REMEMBER_ME_DURATION_DAYS * 24 * 60 * 60 * 1000
            : Date.now() + DEFAULT_DURATION_HOURS * 60 * 60 * 1000;

          setAccessToken(response.access_token, expiresAt);
          setRefreshToken(response.refresh_token);

          // Stocker le session_id dans localStorage
          if (credentials.rememberMe) {
            localStorage.setItem('nexusdl_session_id', response.session_id);
          }

          toast.success('Welcome back!', {
            description: `Logged in as ${response.username}`,
          });

          // Rediriger
          router.push(redirectTo);
        } else {
          // Connexion avec API key
          // TODO: Implémenter la validation de l'API key côté backend
          // Pour l'instant, on stocke simplement l'API key
          localStorage.setItem('nexusdl_api_key', apiKeyForm.apiKey);

          toast.success('API key configured', {
            description: 'You can now use the API',
          });

          // Rediriger
          router.push(redirectTo);
        }
      } catch (error) {
        console.error('[LoginPage] Login error:', error);

        // Gérer les erreurs
        if (error instanceof Error) {
          if ('status' in error && (error as any).status === 401) {
            setErrors({
              general: 'Invalid username or password',
            });
            toast.error('Login failed', {
              description: 'Invalid username or password',
            });
          } else if ('status' in error && (error as any).status === 423) {
            setErrors({
              general: 'Account is locked. Please try again later.',
            });
            toast.error('Account locked', {
              description: 'Too many failed attempts',
            });
          } else {
            setErrors({
              general: error.message || 'An error occurred during login',
            });
            toast.error('Login failed', {
              description: error.message,
            });
          }
        } else {
          setErrors({
            general: 'An unexpected error occurred',
          });
          toast.error('Login failed', {
            description: 'Please try again',
          });
        }
      } finally {
        setIsLoading(false);
      }
    },
    [mode, credentials, apiKeyForm, validateCredentials, validateApiKey, router, redirectTo]
  );

  /**
   * Change le mode d'authentification.
   */
  const handleModeChange = useCallback((newMode: AuthMode) => {
    setMode(newMode);
    setErrors({});
  }, []);

  /**
   * Met à jour les champs du formulaire.
   */
  const handleFieldChange = useCallback(
    (field: keyof LoginForm, value: string | boolean) => {
      setCredentials((prev) => ({ ...prev, [field]: value }));
      // Effacer l'erreur du champ quand l'utilisateur tape
      if (errors[field as keyof ValidationErrors]) {
        setErrors((prev) => {
          const next = { ...prev };
          delete next[field as keyof ValidationErrors];
          return next;
        });
      }
    },
    [errors]
  );

  // ==========================================================================
  // EFFECTS
  // ==========================================================================

  /**
   * Vérifier si l'utilisateur est déjà connecté.
   */
  useEffect(() => {
    const token = localStorage.getItem('nexusdl_access_token');
    if (token) {
      // Déjà connecté, rediriger
      router.replace(redirectTo);
    }
  }, [router, redirectTo]);

  // ==========================================================================
  // RENDU
  // ==========================================================================

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      {/* Background effects */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none" aria-hidden="true">
        <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-primary/5 rounded-full blur-3xl animate-pulse" />
        <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-secondary/5 rounded-full blur-3xl animate-pulse delay-1000" />
      </div>

      {/* Container principal */}
      <div className="relative w-full max-w-md">
        {/* Logo et titre */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center h-16 w-16 rounded-2xl bg-primary-bg border-2 border-primary/40 mb-4 shadow-[0_0_20px_rgba(0,255,65,0.3)]">
            <Sparkles
              size={32}
              className="text-primary"
              style={{ filter: 'drop-shadow(0 0 8px rgba(0, 255, 65, 0.8))' }}
            />
          </div>
          <h1 className="text-3xl font-mono font-bold text-primary tracking-wider mb-2">
            NexusDL
          </h1>
          <p className="text-sm font-mono text-text-muted">
            Your manga, your library, your way
          </p>
        </div>

        {/* Carte de connexion */}
        <div className="rounded-xl border-2 border-border-dim bg-surface p-6 shadow-2xl shadow-primary/10">
          {/* Tabs pour changer de mode */}
          <div className="flex gap-2 mb-6 p-1 rounded-lg bg-surface-alt border border-border-dim">
            <button
              type="button"
              onClick={() => handleModeChange('credentials')}
              className={cn(
                'flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-md',
                'font-mono text-sm font-semibold transition-all duration-200',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                mode === 'credentials'
                  ? 'bg-primary-bg text-primary border border-primary/30 shadow-[0_0_10px_rgba(0,255,65,0.2)]'
                  : 'text-text-muted hover:text-text hover:bg-surface-hover'
              )}
            >
              <User size={16} />
              <span>Credentials</span>
            </button>
            <button
              type="button"
              onClick={() => handleModeChange('apikey')}
              className={cn(
                'flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-md',
                'font-mono text-sm font-semibold transition-all duration-200',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                mode === 'apikey'
                  ? 'bg-primary-bg text-primary border border-primary/30 shadow-[0_0_10px_rgba(0,255,65,0.2)]'
                  : 'text-text-muted hover:text-text hover:bg-surface-hover'
              )}
            >
              <Key size={16} />
              <span>API Key</span>
            </button>
          </div>

          {/* Formulaire */}
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Erreur générale */}
            {errors.general && (
              <div
                role="alert"
                className="flex items-start gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/30 animate-in fade-in slide-in-from-top-2 duration-200"
              >
                <AlertCircle size={16} className="text-red-400 flex-shrink-0 mt-0.5" />
                <p className="text-xs font-mono text-red-300">{errors.general}</p>
              </div>
            )}

            {/* Mode Credentials */}
            {mode === 'credentials' && (
              <>
                {/* Username */}
                <div>
                  <label
                    htmlFor="username"
                    className="block text-xs font-mono font-bold text-text-dim uppercase tracking-wider mb-2"
                  >
                    Username
                  </label>
                  <div className="relative">
                    <User
                      size={16}
                      className="absolute left-3 top-1/2 -translate-y-1/2 text-text-dim"
                      aria-hidden="true"
                    />
                    <input
                      id="username"
                      type="text"
                      value={credentials.username}
                      onChange={(e) => handleFieldChange('username', e.target.value)}
                      disabled={isLoading}
                      autoComplete="username"
                      autoFocus
                      aria-invalid={!!errors.username}
                      aria-describedby={errors.username ? 'username-error' : undefined}
                      placeholder="Enter your username"
                      className={cn(
                        'w-full h-11 pl-10 pr-3 rounded-lg',
                        'bg-background border-2',
                        'text-text placeholder:text-text-dim',
                        'text-sm font-mono',
                        'transition-all duration-200',
                        'focus:outline-none focus:ring-2 focus:ring-offset-0',
                        'disabled:opacity-50 disabled:cursor-not-allowed',
                        errors.username
                          ? 'border-red-500/50 focus:border-red-500 focus:ring-red-500/20'
                          : 'border-border-dim focus:border-secondary focus:ring-secondary/20 hover:border-secondary/50'
                      )}
                    />
                  </div>
                  {errors.username && (
                    <p id="username-error" role="alert" className="mt-1.5 text-xs font-mono text-red-400">
                      {errors.username}
                    </p>
                  )}
                </div>

                {/* Password */}
                <div>
                  <label
                    htmlFor="password"
                    className="block text-xs font-mono font-bold text-text-dim uppercase tracking-wider mb-2"
                  >
                    Password
                  </label>
                  <div className="relative">
                    <Lock
                      size={16}
                      className="absolute left-3 top-1/2 -translate-y-1/2 text-text-dim"
                      aria-hidden="true"
                    />
                    <input
                      id="password"
                      type={showPassword ? 'text' : 'password'}
                      value={credentials.password}
                      onChange={(e) => handleFieldChange('password', e.target.value)}
                      disabled={isLoading}
                      autoComplete="current-password"
                      aria-invalid={!!errors.password}
                      aria-describedby={errors.password ? 'password-error' : undefined}
                      placeholder="Enter your password"
                      className={cn(
                        'w-full h-11 pl-10 pr-10 rounded-lg',
                        'bg-background border-2',
                        'text-text placeholder:text-text-dim',
                        'text-sm font-mono',
                        'transition-all duration-200',
                        'focus:outline-none focus:ring-2 focus:ring-offset-0',
                        'disabled:opacity-50 disabled:cursor-not-allowed',
                        errors.password
                          ? 'border-red-500/50 focus:border-red-500 focus:ring-red-500/20'
                          : 'border-border-dim focus:border-secondary focus:ring-secondary/20 hover:border-secondary/50'
                      )}
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      disabled={isLoading}
                      aria-label={showPassword ? 'Hide password' : 'Show password'}
                      className="absolute right-3 top-1/2 -translate-y-1/2 p-1 rounded text-text-dim hover:text-secondary transition-colors disabled:opacity-50"
                    >
                      {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                  {errors.password && (
                    <p id="password-error" role="alert" className="mt-1.5 text-xs font-mono text-red-400">
                      {errors.password}
                    </p>
                  )}
                </div>

                {/* Remember me + Forgot password */}
                <div className="flex items-center justify-between">
                  <label className="flex items-center gap-2 cursor-pointer group">
                    <input
                      type="checkbox"
                      checked={credentials.rememberMe}
                      onChange={(e) => handleFieldChange('rememberMe', e.target.checked)}
                      disabled={isLoading}
                      className="h-4 w-4 rounded border-border-dim bg-background text-primary focus:ring-primary focus:ring-offset-0 cursor-pointer"
                    />
                    <span className="text-xs font-mono text-text-muted group-hover:text-text transition-colors">
                      Remember me
                    </span>
                  </label>

                  <Link
                    href="/auth/forgot-password"
                    className="text-xs font-mono text-secondary hover:text-primary transition-colors"
                  >
                    Forgot password?
                  </Link>
                </div>
              </>
            )}

            {/* Mode API Key */}
            {mode === 'apikey' && (
              <div>
                <label
                  htmlFor="apiKey"
                  className="block text-xs font-mono font-bold text-text-dim uppercase tracking-wider mb-2"
                >
                  API Key
                </label>
                <div className="relative">
                  <Key
                    size={16}
                    className="absolute left-3 top-1/2 -translate-y-1/2 text-text-dim"
                    aria-hidden="true"
                  />
                  <input
                    id="apiKey"
                    type="password"
                    value={apiKeyForm.apiKey}
                    onChange={(e) => {
                      setApiKeyForm({ apiKey: e.target.value });
                      if (errors.apiKey) {
                        setErrors((prev) => {
                          const next = { ...prev };
                          delete next.apiKey;
                          return next;
                        });
                      }
                    }}
                    disabled={isLoading}
                    autoComplete="off"
                    autoFocus
                    aria-invalid={!!errors.apiKey}
                    aria-describedby={errors.apiKey ? 'apikey-error' : undefined}
                    placeholder="Enter your API key"
                    className={cn(
                      'w-full h-11 pl-10 pr-3 rounded-lg',
                      'bg-background border-2',
                      'text-text placeholder:text-text-dim',
                      'text-sm font-mono',
                      'transition-all duration-200',
                      'focus:outline-none focus:ring-2 focus:ring-offset-0',
                      'disabled:opacity-50 disabled:cursor-not-allowed',
                      errors.apiKey
                        ? 'border-red-500/50 focus:border-red-500 focus:ring-red-500/20'
                        : 'border-border-dim focus:border-secondary focus:ring-secondary/20 hover:border-secondary/50'
                    )}
                  />
                </div>
                {errors.apiKey && (
                  <p id="apikey-error" role="alert" className="mt-1.5 text-xs font-mono text-red-400">
                    {errors.apiKey}
                  </p>
                )}
                <p className="mt-2 text-[10px] font-mono text-text-dim">
                  Generate an API key from your account settings
                </p>
              </div>
            )}

            {/* Bouton de soumission */}
            <button
              type="submit"
              disabled={isLoading}
              className={cn(
                'w-full h-11 flex items-center justify-center gap-2 rounded-lg',
                'font-mono font-bold text-sm uppercase tracking-wider',
                'transition-all duration-200',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2',
                'disabled:opacity-50 disabled:cursor-not-allowed',
                // Style cyberpunk
                'bg-primary text-background',
                'hover:bg-primary-bright hover:shadow-[0_0_20px_rgba(0,255,65,0.5)]',
                'active:scale-[0.98]'
              )}
            >
              {isLoading ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  <span>Authenticating...</span>
                </>
              ) : (
                <>
                  <Zap size={16} />
                  <span>{mode === 'credentials' ? 'Sign In' : 'Connect'}</span>
                </>
              )}
            </button>
          </form>

          {/* Séparateur */}
          <div className="relative my-6">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-border-dim" />
            </div>
            <div className="relative flex justify-center text-xs uppercase">
              <span className="px-2 bg-surface text-text-dim font-mono">
                New to NexusDL?
              </span>
            </div>
          </div>

          {/* Lien vers inscription */}
          <Link
            href="/auth/register"
            className={cn(
              'w-full h-11 flex items-center justify-center gap-2 rounded-lg',
              'font-mono font-semibold text-sm',
              'border-2 border-border-dim bg-surface-alt text-text',
              'hover:border-secondary hover:text-secondary hover:bg-surface-hover',
              'transition-all duration-200',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary'
            )}
          >
            <Sparkles size={16} />
            <span>Create Account</span>
          </Link>
        </div>

        {/* Footer */}
        <div className="mt-6 text-center">
          <p className="text-[10px] font-mono text-text-dim">
            By signing in, you agree to our{' '}
            <Link href="/terms" className="text-secondary hover:text-primary transition-colors">
              Terms of Service
            </Link>{' '}
            and{' '}
            <Link href="/privacy" className="text-secondary hover:text-primary transition-colors">
              Privacy Policy
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
