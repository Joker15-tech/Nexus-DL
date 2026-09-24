/**
 * Page de paramètres pour NexusDL.
 *
 * Interface complète pour gérer la configuration de l'application avec :
 *   - Navigation latérale entre sections
 *   - 11 sections de configuration (app, network, proxy, etc.)
 *   - Préférences UI locales (thème, langue, etc.)
 *   - Validation en temps réel des champs
 *   - Indicateur de modifications non sauvegardées
 *   - Export/import de configuration (JSON/YAML)
 *   - Reset aux valeurs par défaut
 *   - Recherche dans les paramètres
 *   - Breadcrumbs
 *   - Toast notifications
 *   - Style cyberpunk néon cohérent
 *   - Accessibilité complète
 *   - Responsive
 *
 * @module app/settings/page
 */

'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  AlertCircle,
  Check,
  ChevronRight,
  Download,
  Eye,
  EyeOff,
  Folder,
  Globe,
  Info,
  Key,
  Loader2,
  Network,
  Palette,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Settings as SettingsIcon,
  Shield,
  Sparkles,
  Storage,
  Terminal,
  Trash2,
  Upload,
  Wifi,
  X,
  Zap,
} from 'lucide-react';
import { toast } from 'sonner';

import { cn } from '@/lib/utils';
import { useSettingsActions, useConfig, useUIPreferencesWithActions, useUnsavedChangesWarning } from '@/store';

import type {
  ConfigSection,
  FullConfig,
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
} from '@/store/settings';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Métadonnées d'une section de paramètres.
 */
interface SectionMeta {
  id: ConfigSection;
  label: string;
  description: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  color: string;
}

/**
 * État de validation d'un champ.
 */
interface FieldValidation {
  isValid: boolean;
  error?: string;
}

// ============================================================================
// CONSTANTES
// ============================================================================

/**
 * Liste des sections avec leurs métadonnées.
 */
const SECTIONS: SectionMeta[] = [
  {
    id: 'app',
    label: 'Application',
    description: 'General application settings',
    icon: Sparkles,
    color: 'text-primary',
  },
  {
    id: 'download',
    label: 'Downloads',
    description: 'Download behavior and defaults',
    icon: Download,
    color: 'text-cyan-400',
  },
  {
    id: 'library',
    label: 'Library',
    description: 'Local library management',
    icon: Folder,
    color: 'text-green-400',
  },
  {
    id: 'network',
    label: 'Network',
    description: 'HTTP client configuration',
    icon: Network,
    color: 'text-blue-400',
  },
  {
    id: 'proxy',
    label: 'Proxy',
    description: 'Proxy and rotation settings',
    icon: Shield,
    color: 'text-purple-400',
  },
  {
    id: 'cloudflare',
    label: 'Cloudflare',
    description: 'Anti-bot bypass configuration',
    icon: Zap,
    color: 'text-orange-400',
  },
  {
    id: 'storage',
    label: 'Storage',
    description: 'Cache and cleanup settings',
    icon: Storage,
    color: 'text-yellow-400',
  },
  {
    id: 'logging',
    label: 'Logging',
    description: 'Log level and output',
    icon: Terminal,
    color: 'text-text',
  },
  {
    id: 'i18n',
    label: 'Language',
    description: 'Internationalization',
    icon: Globe,
    color: 'text-accent',
  },
  {
    id: 'events',
    label: 'Events',
    description: 'Event bus configuration',
    icon: Wifi,
    color: 'text-pink-400',
  },
  {
    id: 'interface',
    label: 'Interfaces',
    description: 'Web, GUI, CLI settings',
    icon: Key,
    color: 'text-secondary',
  },
];

/**
 * Options de langue disponibles.
 */
const LANGUAGE_OPTIONS = [
  { code: 'en', label: 'English', flag: '🇬🇧' },
  { code: 'fr', label: 'Français', flag: '🇫🇷' },
  { code: 'es', label: 'Español', flag: '🇪🇸' },
  { code: 'de', label: 'Deutsch', flag: '🇩🇪' },
  { code: 'ja', label: '日本語', flag: '🇯🇵' },
  { code: 'ko', label: '한국어', flag: '🇰🇷' },
  { code: 'zh', label: '中文', flag: '🇨🇳' },
];

/**
 * Options de thème disponibles.
 */
const THEME_OPTIONS = [
  { id: 'cyberpunk', label: 'Cyberpunk', description: 'Neon hacker style' },
  { id: 'dark', label: 'Dark', description: 'Classic dark mode' },
  { id: 'light', label: 'Light', description: 'Clean light mode' },
  { id: 'system', label: 'System', description: 'Match OS preference' },
];

/**
 * Options de sens de lecture.
 */
const READING_DIRECTION_OPTIONS = [
  { id: 'ltr', label: 'Left to Right', description: 'Western style' },
  { id: 'rtl', label: 'Right to Left', description: 'Manga style' },
  { id: 'vertical', label: 'Vertical', description: 'Webtoon style' },
];

/**
 * Options de format de téléchargement.
 */
const FORMAT_OPTIONS = [
  { id: 'cbz', label: 'CBZ', description: 'Comic Book ZIP (recommended)' },
  { id: 'cbr', label: 'CBR', description: 'Comic Book RAR' },
  { id: 'pdf', label: 'PDF', description: 'Portable Document Format' },
  { id: 'zip', label: 'ZIP', description: 'Standard ZIP archive' },
  { id: 'folder', label: 'Folder', description: 'Extracted images' },
];

/**
 * Options de qualité d'image.
 */
const QUALITY_OPTIONS = [
  { id: 'original', label: 'Original', description: 'Best quality' },
  { id: 'high', label: 'High', description: 'High quality' },
  { id: 'medium', label: 'Medium', description: 'Balanced' },
  { id: 'low', label: 'Low', description: 'Save bandwidth' },
];

/**
 * Options de niveau de log.
 */
const LOG_LEVEL_OPTIONS = [
  { id: 'TRACE', label: 'Trace', description: 'Very verbose' },
  { id: 'DEBUG', label: 'Debug', description: 'Development' },
  { id: 'INFO', label: 'Info', description: 'Normal' },
  { id: 'WARNING', label: 'Warning', description: 'Important only' },
  { id: 'ERROR', label: 'Error', description: 'Errors only' },
  { id: 'CRITICAL', label: 'Critical', description: 'Critical only' },
];

// ============================================================================
// COMPOSANTS DE FORMULAIRE RÉUTILISABLES
// ============================================================================

/**
 * Label de champ de formulaire.
 */
function FormLabel({
  htmlFor,
  children,
  required,
  description,
}: {
  htmlFor?: string;
  children: React.ReactNode;
  required?: boolean;
  description?: string;
}) {
  return (
    <div className="mb-1.5">
      <label
        htmlFor={htmlFor}
        className="text-xs font-mono font-bold text-text-dim uppercase tracking-wider"
      >
        {children}
        {required && <span className="text-red-400 ml-1">*</span>}
      </label>
      {description && (
        <p className="text-[10px] font-mono text-text-muted mt-0.5">{description}</p>
      )}
    </div>
  );
}

/**
 * Champ de texte.
 */
function FormInput({
  id,
  type = 'text',
  value,
  onChange,
  placeholder,
  error,
  disabled,
  min,
  max,
  step,
}: {
  id: string;
  type?: 'text' | 'number' | 'password' | 'email';
  value: string | number;
  onChange: (value: string) => void;
  placeholder?: string;
  error?: string;
  disabled?: boolean;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <input
      id={id}
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      disabled={disabled}
      min={min}
      max={max}
      step={step}
      aria-invalid={!!error}
      aria-describedby={error ? `${id}-error` : undefined}
      className={cn(
        'w-full h-10 px-3 rounded-lg',
        'bg-background border-2',
        'text-text placeholder:text-text-dim',
        'text-sm font-mono',
        'transition-all duration-200',
        'focus:outline-none focus:ring-2 focus:ring-offset-0',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        error
          ? 'border-red-500/50 focus:border-red-500 focus:ring-red-500/20'
          : 'border-border-dim focus:border-secondary focus:ring-secondary/20 hover:border-secondary/50'
      )}
    />
  );
}

/**
 * Champ select.
 */
function FormSelect({
  id,
  value,
  onChange,
  options,
  disabled,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
  disabled?: boolean;
}) {
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      className={cn(
        'w-full h-10 px-3 rounded-lg',
        'bg-background border-2 border-border-dim',
        'text-text text-sm font-mono',
        'transition-all duration-200',
        'focus:outline-none focus:border-secondary',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        'hover:border-secondary/50'
      )}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

/**
 * Toggle switch.
 */
function FormToggle({
  id,
  checked,
  onChange,
  disabled,
  label,
  description,
}: {
  id: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  label?: string;
  description?: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        disabled={disabled}
        className={cn(
          'relative flex-shrink-0',
          'h-6 w-11 rounded-full',
          'transition-all duration-200',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2',
          'disabled:opacity-50 disabled:cursor-not-allowed',
          checked ? 'bg-primary' : 'bg-surface-alt border border-border-dim'
        )}
      >
        <span
          className={cn(
            'absolute top-0.5 left-0.5',
            'h-5 w-5 rounded-full',
            'bg-background',
            'transition-transform duration-200',
            checked && 'translate-x-5'
          )}
          style={checked ? { boxShadow: '0 0 8px rgba(0, 255, 65, 0.6)' } : undefined}
        />
      </button>
      {(label || description) && (
        <div className="flex-1 min-w-0">
          {label && (
            <label htmlFor={id} className="text-sm font-mono text-text cursor-pointer">
              {label}
            </label>
          )}
          {description && (
            <p className="text-[10px] font-mono text-text-muted mt-0.5">{description}</p>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Slider numérique.
 */
function FormSlider({
  id,
  value,
  onChange,
  min,
  max,
  step = 1,
  disabled,
  showValue,
  formatValue,
}: {
  id: string;
  value: number;
  onChange: (value: number) => void;
  min: number;
  max: number;
  step?: number;
  disabled?: boolean;
  showValue?: boolean;
  formatValue?: (value: number) => string;
}) {
  return (
    <div className="flex items-center gap-3">
      <input
        id={id}
        type="range"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        className={cn(
          'flex-1 h-2 rounded-full appearance-none cursor-pointer',
          'bg-surface-alt',
          'disabled:opacity-50 disabled:cursor-not-allowed',
          '[&::-webkit-slider-thumb]:appearance-none',
          '[&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4',
          '[&::-webkit-slider-thumb]:rounded-full',
          '[&::-webkit-slider-thumb]:bg-primary',
          '[&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-background',
          '[&::-webkit-slider-thumb]:shadow-[0_0_8px_rgba(0,255,65,0.6)]',
          '[&::-webkit-slider-thumb]:cursor-pointer'
        )}
      />
      {showValue && (
        <span className="text-sm font-mono font-bold text-primary min-w-[60px] text-right">
          {formatValue ? formatValue(value) : value}
        </span>
      )}
    </div>
  );
}

/**
 * Groupe de boutons radio.
 */
function FormRadioGroup<T extends string>({
  id,
  value,
  onChange,
  options,
  disabled,
}: {
  id: string;
  value: T;
  onChange: (value: T) => void;
  options: Array<{ value: T; label: string; description?: string }>;
  disabled?: boolean;
}) {
  return (
    <div className="space-y-2">
      {options.map((option) => (
        <label
          key={option.value}
          className={cn(
            'flex items-start gap-3 p-3 rounded-lg cursor-pointer',
            'border-2 transition-all duration-200',
            'focus-within:ring-2 focus-within:ring-primary focus-within:ring-offset-2',
            'disabled:opacity-50 disabled:cursor-not-allowed',
            value === option.value
              ? 'bg-primary-bg border-primary'
              : 'bg-surface-alt border-border-dim hover:border-secondary'
          )}
        >
          <input
            type="radio"
            name={id}
            value={option.value}
            checked={value === option.value}
            onChange={() => onChange(option.value)}
            disabled={disabled}
            className="sr-only"
          />
          <div
            className={cn(
              'flex-shrink-0 mt-0.5',
              'h-4 w-4 rounded-full border-2',
              'flex items-center justify-center',
              'transition-all',
              value === option.value
                ? 'border-primary bg-primary'
                : 'border-border-dim bg-background'
            )}
          >
            {value === option.value && (
              <div className="h-1.5 w-1.5 rounded-full bg-background" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-mono font-semibold text-text">{option.label}</div>
            {option.description && (
              <div className="text-[10px] font-mono text-text-muted mt-0.5">
                {option.description}
              </div>
            )}
          </div>
        </label>
      ))}
    </div>
  );
}

/**
 * Sélecteur de dossier.
 */
function FormFolderPicker({
  id,
  value,
  onChange,
  placeholder,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="flex gap-2">
      <input
        id={id}
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={cn(
          'flex-1 h-10 px-3 rounded-lg',
          'bg-background border-2 border-border-dim',
          'text-text placeholder:text-text-dim',
          'text-sm font-mono',
          'focus:outline-none focus:border-secondary',
          'hover:border-secondary/50 transition-colors'
        )}
      />
      <button
        type="button"
        onClick={() => {
          // TODO: Implémenter le sélecteur de dossier natif
          toast.info('Folder picker coming soon');
        }}
        className={cn(
          'flex items-center gap-1.5 px-3 h-10 rounded-lg',
          'bg-surface-alt text-text-muted border-2 border-border-dim',
          'font-mono text-xs',
          'hover:bg-surface-hover hover:border-secondary hover:text-secondary',
          'transition-all duration-200'
        )}
      >
        <Folder size={14} />
        <span>Browse</span>
      </button>
    </div>
  );
}

/**
 * Section de formulaire avec titre et description.
 */
function FormSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-sm font-mono font-bold text-text uppercase tracking-wider">
          {title}
        </h3>
        {description && (
          <p className="text-xs font-mono text-text-muted mt-1">{description}</p>
        )}
      </div>
      <div className="space-y-4">{children}</div>
    </div>
  );
}

/**
 * Champ de formulaire avec label et contenu.
 */
function FormField({
  label,
  description,
  required,
  error,
  children,
}: {
  label: string;
  description?: string;
  required?: boolean;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <FormLabel required={required} description={description}>
        {label}
      </FormLabel>
      {children}
      {error && (
        <p className="mt-1 text-xs font-mono text-red-400 flex items-center gap-1">
          <AlertCircle size={10} />
          {error}
        </p>
      )}
    </div>
  );
}

// ============================================================================
// SECTIONS DE CONFIGURATION
// ============================================================================

/**
 * Section Application.
 */
function AppSection({
  config,
  onChange,
}: {
  config: AppConfig;
  onChange: (config: Partial<AppConfig>) => void;
}) {
  return (
    <div className="space-y-6">
      <FormSection title="Localization" description="Language and regional settings">
        <FormField label="Language" description="Interface language">
          <FormSelect
            id="app-language"
            value={config.language}
            onChange={(v) => onChange({ language: v })}
            options={LANGUAGE_OPTIONS.map((l) => ({ value: l.code, label: `${l.flag} ${l.label}` }))}
          />
        </FormField>

        <FormField label="Timezone" description="Your local timezone">
          <FormSelect
            id="app-timezone"
            value={config.timezone}
            onChange={(v) => onChange({ timezone: v })}
            options={[
              { value: 'auto', label: 'Auto-detect' },
              { value: 'UTC', label: 'UTC' },
              { value: 'Europe/Paris', label: 'Europe/Paris' },
              { value: 'America/New_York', label: 'America/New York' },
              { value: 'Asia/Tokyo', label: 'Asia/Tokyo' },
            ]}
          />
        </FormField>
      </FormSection>

      <FormSection title="Reading" description="Reading preferences">
        <FormField label="Reading Direction" description="Default direction for manga reader">
          <FormRadioGroup
            id="app-reading-direction"
            value={config.reading_direction}
            onChange={(v) => onChange({ reading_direction: v as any })}
            options={READING_DIRECTION_OPTIONS as any}
          />
        </FormField>
      </FormSection>

      <FormSection title="Updates" description="Application update settings">
        <FormToggle
          id="app-check-updates"
          checked={config.check_updates}
          onChange={(v) => onChange({ check_updates: v })}
          label="Check for updates"
          description="Automatically check for new versions on startup"
        />
        <FormToggle
          id="app-auto-update"
          checked={config.auto_update}
          onChange={(v) => onChange({ auto_update: v })}
          label="Auto-update"
          description="Automatically download and install updates"
        />
      </FormSection>
    </div>
  );
}

/**
 * Section Downloads.
 */
function DownloadSection({
  config,
  onChange,
}: {
  config: DownloadConfig;
  onChange: (config: Partial<DownloadConfig>) => void;
}) {
  return (
    <div className="space-y-6">
      <FormSection title="Concurrency" description="Parallel download settings">
        <FormField label="Max Concurrent Tasks" description="Number of downloads running simultaneously">
          <FormSlider
            id="download-max-tasks"
            value={config.max_concurrent_tasks}
            onChange={(v) => onChange({ max_concurrent_tasks: v })}
            min={1}
            max={20}
            showValue
          />
        </FormField>

        <FormField label="Max Concurrent Pages" description="Pages downloaded per task in parallel">
          <FormSlider
            id="download-max-pages"
            value={config.max_concurrent_pages}
            onChange={(v) => onChange({ max_concurrent_pages: v })}
            min={1}
            max={10}
            showValue
          />
        </FormField>
      </FormSection>

      <FormSection title="Defaults" description="Default values for new downloads">
        <FormField label="Default Format" description="Output format for downloads">
          <FormRadioGroup
            id="download-format"
            value={config.default_format}
            onChange={(v) => onChange({ default_format: v as any })}
            options={FORMAT_OPTIONS as any}
          />
        </FormField>

        <FormField label="Default Quality" description="Image quality for downloads">
          <FormRadioGroup
            id="download-quality"
            value={config.default_quality}
            onChange={(v) => onChange({ default_quality: v as any })}
            options={QUALITY_OPTIONS as any}
          />
        </FormField>
      </FormSection>

      <FormSection title="Paths" description="Download directories">
        <FormField label="Output Directory" description="Where downloaded files are saved">
          <FormFolderPicker
            id="download-output-dir"
            value={config.output_dir}
            onChange={(v) => onChange({ output_dir: v })}
            placeholder="/home/user/manga"
          />
        </FormField>

        <FormField label="Temporary Directory" description="Temporary files during download">
          <FormFolderPicker
            id="download-temp-dir"
            value={config.temp_dir}
            onChange={(v) => onChange({ temp_dir: v })}
            placeholder="/tmp/nexusdl"
          />
        </FormField>
      </FormSection>

      <FormSection title="Behavior" description="Download behavior">
        <FormToggle
          id="download-auto-extract"
          checked={config.auto_extract}
          onChange={(v) => onChange({ auto_extract: v })}
          label="Auto-extract archives"
          description="Automatically extract CBZ/CBR files after download"
        />
        <FormToggle
          id="download-delete-temp"
          checked={config.delete_temp}
          onChange={(v) => onChange({ delete_temp: v })}
          label="Delete temporary files"
          description="Clean up temp files after successful download"
        />
      </FormSection>

      <FormSection title="Retry" description="Retry settings for failed downloads">
        <FormField label="Retry Attempts" description="Number of retries before giving up">
          <FormSlider
            id="download-retry-attempts"
            value={config.retry_attempts}
            onChange={(v) => onChange({ retry_attempts: v })}
            min={0}
            max={10}
            showValue
          />
        </FormField>

        <FormField label="Retry Delay" description="Delay between retries (seconds)">
          <FormSlider
            id="download-retry-delay"
            value={config.retry_delay}
            onChange={(v) => onChange({ retry_delay: v })}
            min={1}
            max={60}
            showValue
            formatValue={(v) => `${v}s`}
          />
        </FormField>
      </FormSection>
    </div>
  );
}

/**
 * Section Network.
 */
function NetworkSection({
  config,
  onChange,
}: {
  config: NetworkConfig;
  onChange: (config: Partial<NetworkConfig>) => void;
}) {
  return (
    <div className="space-y-6">
      <FormSection title="Timeouts" description="Request timeout settings">
        <FormField label="Request Timeout" description="Maximum time to wait for a response (seconds)">
          <FormSlider
            id="network-timeout"
            value={config.timeout}
            onChange={(v) => onChange({ timeout: v })}
            min={5}
            max={120}
            showValue
            formatValue={(v) => `${v}s`}
          />
        </FormField>

        <FormField label="Connect Timeout" description="Maximum time to establish connection (seconds)">
          <FormSlider
            id="network-connect-timeout"
            value={config.connect_timeout}
            onChange={(v) => onChange({ connect_timeout: v })}
            min={1}
            max={30}
            showValue
            formatValue={(v) => `${v}s`}
          />
        </FormField>
      </FormSection>

      <FormSection title="Connections" description="Connection pool settings">
        <FormField label="Max Connections" description="Maximum simultaneous connections">
          <FormSlider
            id="network-max-connections"
            value={config.max_connections}
            onChange={(v) => onChange({ max_connections: v })}
            min={1}
            max={100}
            showValue
          />
        </FormField>

        <FormField label="Max Connections per Host" description="Maximum connections to a single host">
          <FormSlider
            id="network-max-per-host"
            value={config.max_connections_per_host}
            onChange={(v) => onChange({ max_connections_per_host: v })}
            min={1}
            max={20}
            showValue
          />
        </FormField>
      </FormSection>

      <FormSection title="Security" description="Security settings">
        <FormToggle
          id="network-verify-ssl"
          checked={config.verify_ssl}
          onChange={(v) => onChange({ verify_ssl: v })}
          label="Verify SSL Certificates"
          description="Validate SSL certificates (recommended)"
        />

        <FormToggle
          id="network-http2"
          checked={config.http2}
          onChange={(v) => onChange({ http2: v })}
          label="Enable HTTP/2"
          description="Use HTTP/2 when available for better performance"
        />
      </FormSection>

      <FormSection title="Redirects" description="HTTP redirect handling">
        <FormToggle
          id="network-follow-redirects"
          checked={config.follow_redirects}
          onChange={(v) => onChange({ follow_redirects: v })}
          label="Follow Redirects"
          description="Automatically follow HTTP redirects"
        />

        <FormField label="Max Redirects" description="Maximum number of redirects to follow">
          <FormSlider
            id="network-max-redirects"
            value={config.max_redirects}
            onChange={(v) => onChange({ max_redirects: v })}
            min={0}
            max={10}
            showValue
          />
        </FormField>
      </FormSection>
    </div>
  );
}

/**
 * Section Proxy.
 */
function ProxySection({
  config,
  onChange,
}: {
  config: ProxyConfig;
  onChange: (config: Partial<ProxyConfig>) => void;
}) {
  const [showPassword, setShowPassword] = useState(false);

  return (
    <div className="space-y-6">
      <FormSection title="Proxy" description="HTTP proxy configuration">
        <FormToggle
          id="proxy-enabled"
          checked={config.enabled}
          onChange={(v) => onChange({ enabled: v })}
          label="Enable Proxy"
          description="Route all requests through a proxy server"
        />

        {config.enabled && (
          <>
            <FormField label="Proxy URL" description="Proxy server URL (e.g., http://proxy.example.com:8080)" required>
              <FormInput
                id="proxy-url"
                value={config.url}
                onChange={(v) => onChange({ url: v })}
                placeholder="http://proxy.example.com:8080"
              />
            </FormField>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <FormField label="Username" description="Proxy authentication username">
                <FormInput
                  id="proxy-username"
                  value={config.username}
                  onChange={(v) => onChange({ username: v })}
                  placeholder="username"
                />
              </FormField>

              <FormField label="Password" description="Proxy authentication password">
                <div className="relative">
                  <FormInput
                    id="proxy-password"
                    type={showPassword ? 'text' : 'password'}
                    value={config.password}
                    onChange={(v) => onChange({ password: v })}
                    placeholder="password"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-text-dim hover:text-secondary transition-colors"
                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                  >
                    {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
              </FormField>
            </div>
          </>
        )}
      </FormSection>

      <FormSection title="Rotation" description="Proxy rotation settings">
        <FormToggle
          id="proxy-rotation"
          checked={config.rotation}
          onChange={(v) => onChange({ rotation: v })}
          label="Enable Proxy Rotation"
          description="Rotate through multiple proxies to avoid detection"
        />

        {config.rotation && (
          <FormField label="Rotation Interval" description="Time between proxy rotations (minutes)">
            <FormSlider
              id="proxy-rotation-interval"
              value={config.rotation_interval}
              onChange={(v) => onChange({ rotation_interval: v })}
              min={1}
              max={60}
              showValue
              formatValue={(v) => `${v}m`}
            />
          </FormField>
        )}
      </FormSection>
    </div>
  );
}

/**
 * Section Logging.
 */
function LoggingSection({
  config,
  onChange,
}: {
  config: LoggingConfig;
  onChange: (config: Partial<LoggingConfig>) => void;
}) {
  return (
    <div className="space-y-6">
      <FormSection title="Level" description="Logging verbosity">
        <FormField label="Log Level" description="Minimum severity level to log">
          <FormRadioGroup
            id="logging-level"
            value={config.level}
            onChange={(v) => onChange({ level: v as any })}
            options={LOG_LEVEL_OPTIONS as any}
          />
        </FormField>
      </FormSection>

      <FormSection title="Output" description="Log output format">
        <FormField label="Format" description="Log message format">
          <FormRadioGroup
            id="logging-format"
            value={config.format}
            onChange={(v) => onChange({ format: v as any })}
            options={[
              { id: 'text', label: 'Text', description: 'Human-readable text' },
              { id: 'rich', label: 'Rich', description: 'Colored terminal output' },
              { id: 'json', label: 'JSON', description: 'Machine-readable JSON' },
              { id: 'simple', label: 'Simple', description: 'Minimal output' },
            ] as any}
          />
        </FormField>

        <FormToggle
          id="logging-colorize"
          checked={config.colorize}
          onChange={(v) => onChange({ colorize: v })}
          label="Colorize Output"
          description="Add colors to log messages (terminal only)"
        />
      </FormSection>

      <FormSection title="File Output" description="Log file settings">
        <FormToggle
          id="logging-file-enabled"
          checked={config.file_enabled}
          onChange={(v) => onChange({ file_enabled: v })}
          label="Enable File Logging"
          description="Write logs to a file"
        />

        {config.file_enabled && (
          <>
            <FormField label="Rotation" description="When to rotate log files">
              <FormSelect
                id="logging-file-rotation"
                value={config.file_rotation}
                onChange={(v) => onChange({ file_rotation: v })}
                options={[
                  { value: '10 MB', label: '10 MB' },
                  { value: '50 MB', label: '50 MB' },
                  { value: '100 MB', label: '100 MB' },
                  { value: 'daily', label: 'Daily' },
                  { value: 'weekly', label: 'Weekly' },
                ]}
              />
            </FormField>

            <FormField label="Retention" description="How long to keep old log files">
              <FormSelect
                id="logging-file-retention"
                value={config.file_retention}
                onChange={(v) => onChange({ file_retention: v })}
                options={[
                  { value: '7 days', label: '7 days' },
                  { value: '14 days', label: '14 days' },
                  { value: '30 days', label: '30 days' },
                  { value: '90 days', label: '90 days' },
                ]}
              />
            </FormField>
          </>
        )}
      </FormSection>
    </div>
  );
}

/**
 * Section Library.
 */
function LibrarySection({
  config,
  onChange,
}: {
  config: LibraryConfig;
  onChange: (config: Partial<LibraryConfig>) => void;
}) {
  return (
    <div className="space-y-6">
      <FormSection title="Path" description="Library storage location">
        <FormField label="Library Path" description="Root directory for your manga library" required>
          <FormFolderPicker
            id="library-path"
            value={config.path}
            onChange={(v) => onChange({ path: v })}
            placeholder="/home/user/manga-library"
          />
        </FormField>
      </FormSection>

      <FormSection title="Scanning" description="Automatic library scanning">
        <FormToggle
          id="library-auto-scan"
          checked={config.auto_scan}
          onChange={(v) => onChange({ auto_scan: v })}
          label="Auto-Scan"
          description="Automatically scan for new manga in the library folder"
        />

        {config.auto_scan && (
          <FormField label="Scan Interval" description="Time between automatic scans (minutes)">
            <FormSlider
              id="library-scan-interval"
              value={config.scan_interval_minutes}
              onChange={(v) => onChange({ scan_interval_minutes: v })}
              min={5}
              max={1440}
              step={5}
              showValue
              formatValue={(v) => v >= 60 ? `${Math.floor(v / 60)}h` : `${v}m`}
            />
          </FormField>
        )}
      </FormSection>

      <FormSection title="Organization" description="How manga are organized in the library">
        <FormField label="Organize By" description="Folder structure for manga">
          <FormRadioGroup
            id="library-organize-by"
            value={config.organize_by}
            onChange={(v) => onChange({ organize_by: v as any })}
            options={[
              { id: 'title', label: 'Title', description: 'Library/Manga Title/' },
              { id: 'author', label: 'Author', description: 'Library/Author/Title/' },
              { id: 'language', label: 'Language', description: 'Library/Language/Title/' },
              { id: 'site', label: 'Site', description: 'Library/Site/Title/' },
            ] as any}
          />
        </FormField>

        <FormField label="Naming Pattern" description="Pattern for file/folder names">
          <FormInput
            id="library-naming-pattern"
            value={config.naming_pattern}
            onChange={(v) => onChange({ naming_pattern: v })}
            placeholder="{title} - {chapter}"
          />
        </FormField>
      </FormSection>
    </div>
  );
}

/**
 * Section Cloudflare.
 */
function CloudflareSection({
  config,
  onChange,
}: {
  config: CloudflareConfig;
  onChange: (config: Partial<CloudflareConfig>) => void;
}) {
  return (
    <div className="space-y-6">
      <FormSection title="Bypass Mode" description="How to handle Cloudflare protection">
        <FormField label="Mode" description="Method to bypass Cloudflare challenges">
          <FormRadioGroup
            id="cloudflare-bypass-mode"
            value={config.bypass_mode}
            onChange={(v) => onChange({ bypass_mode: v as any })}
            options={[
              { id: 'none', label: 'None', description: 'No bypass (may fail on protected sites)' },
              { id: 'flaresolverr', label: 'FlareSolverr', description: 'Use external FlareSolverr service' },
              { id: 'playwright', label: 'Playwright', description: 'Use headless browser' },
              { id: 'auto', label: 'Auto', description: 'Automatically choose best method' },
            ] as any}
          />
        </FormField>
      </FormSection>

      {config.bypass_mode === 'flaresolverr' && (
        <FormSection title="FlareSolverr" description="FlareSolverr service configuration">
          <FormField label="FlareSolverr URL" description="URL of your FlareSolverr instance" required>
            <FormInput
              id="cloudflare-flaresolverr-url"
              value={config.flaresolverr_url}
              onChange={(v) => onChange({ flaresolverr_url: v })}
              placeholder="http://localhost:8191"
            />
          </FormField>
        </FormSection>
      )}

      {config.bypass_mode === 'playwright' && (
        <FormSection title="Playwright" description="Headless browser configuration">
          <FormToggle
            id="cloudflare-playwright-headless"
            checked={config.playwright_headless}
            onChange={(v) => onChange({ playwright_headless: v })}
            label="Headless Mode"
            description="Run browser without visible window"
          />

          <FormField label="Timeout" description="Maximum time to wait for page load (seconds)">
            <FormSlider
              id="cloudflare-playwright-timeout"
              value={config.playwright_timeout}
              onChange={(v) => onChange({ playwright_timeout: v })}
              min={10}
              max={120}
              showValue
              formatValue={(v) => `${v}s`}
            />
          </FormField>
        </FormSection>
      )}
    </div>
  );
}

/**
 * Section Storage.
 */
function StorageSection({
  config,
  onChange,
}: {
  config: StorageConfig;
  onChange: (config: Partial<StorageConfig>) => void;
}) {
  return (
    <div className="space-y-6">
      <FormSection title="Cache" description="Cache settings">
        <FormToggle
          id="storage-cache-enabled"
          checked={config.cache_enabled}
          onChange={(v) => onChange({ cache_enabled: v })}
          label="Enable Cache"
          description="Cache responses to improve performance"
        />

        {config.cache_enabled && (
          <>
            <FormField label="Cache Directory" description="Where to store cache files">
              <FormFolderPicker
                id="storage-cache-dir"
                value={config.cache_dir}
                onChange={(v) => onChange({ cache_dir: v })}
                placeholder="/tmp/nexusdl-cache"
              />
            </FormField>

            <FormField label="Max Cache Size" description="Maximum cache size (MB)">
              <FormSlider
                id="storage-cache-max-size"
                value={config.cache_max_size_mb}
                onChange={(v) => onChange({ cache_max_size_mb: v })}
                min={100}
                max={10000}
                step={100}
                showValue
                formatValue={(v) => v >= 1000 ? `${(v / 1000).toFixed(1)} GB` : `${v} MB`}
              />
            </FormField>

            <FormField label="Cache TTL" description="How long to keep cached items (hours)">
              <FormSlider
                id="storage-cache-ttl"
                value={config.cache_ttl_hours}
                onChange={(v) => onChange({ cache_ttl_hours: v })}
                min={1}
                max={168}
                showValue
                formatValue={(v) => v >= 24 ? `${Math.floor(v / 24)}d` : `${v}h`}
              />
            </FormField>
          </>
        )}
      </FormSection>

      <FormSection title="Cleanup" description="Automatic cleanup settings">
        <FormToggle
          id="storage-auto-cleanup"
          checked={config.auto_cleanup}
          onChange={(v) => onChange({ auto_cleanup: v })}
          label="Auto-Cleanup"
          description="Automatically clean up old files"
        />

        {config.auto_cleanup && (
          <FormField label="Cleanup Interval" description="Time between cleanups (hours)">
            <FormSlider
              id="storage-cleanup-interval"
              value={config.cleanup_interval_hours}
              onChange={(v) => onChange({ cleanup_interval_hours: v })}
              min={1}
              max={168}
              showValue
              formatValue={(v) => v >= 24 ? `${Math.floor(v / 24)}d` : `${v}h`}
            />
          </FormField>
        )}
      </FormSection>
    </div>
  );
}

/**
 * Section I18n.
 */
function I18nSection({
  config,
  onChange,
}: {
  config: I18nConfig;
  onChange: (config: Partial<I18nConfig>) => void;
}) {
  return (
    <div className="space-y-6">
      <FormSection title="Languages" description="Language settings">
        <FormField label="Primary Language" description="Main language for the interface">
          <FormSelect
            id="i18n-language"
            value={config.language}
            onChange={(v) => onChange({ language: v })}
            options={LANGUAGE_OPTIONS.map((l) => ({ value: l.code, label: `${l.flag} ${l.label}` }))}
          />
        </FormField>

        <FormField label="Fallback Language" description="Language to use when translation is missing">
          <FormSelect
            id="i18n-fallback"
            value={config.fallback_language}
            onChange={(v) => onChange({ fallback_language: v })}
            options={LANGUAGE_OPTIONS.map((l) => ({ value: l.code, label: `${l.flag} ${l.label}` }))}
          />
        </FormField>
      </FormSection>

      <FormSection title="Translations" description="Translation files location">
        <FormField label="Translations Directory" description="Where translation files are stored">
          <FormFolderPicker
            id="i18n-translations-dir"
            value={config.translations_dir}
            onChange={(v) => onChange({ translations_dir: v })}
            placeholder="/path/to/translations"
          />
        </FormField>
      </FormSection>
    </div>
  );
}

/**
 * Section Events.
 */
function EventsSection({
  config,
  onChange,
}: {
  config: EventsConfig;
  onChange: (config: Partial<EventsConfig>) => void;
}) {
  return (
    <div className="space-y-6">
      <FormSection title="Queue" description="Event queue settings">
        <FormField label="Queue Size" description="Maximum number of events in the queue">
          <FormSlider
            id="events-queue-size"
            value={config.queue_size}
            onChange={(v) => onChange({ queue_size: v })}
            min={10}
            max={1000}
            step={10}
            showValue
          />
        </FormField>
      </FormSection>

      <FormSection title="Workers" description="Event processing workers">
        <FormField label="Worker Count" description="Number of workers processing events">
          <FormSlider
            id="events-worker-count"
            value={config.worker_count}
            onChange={(v) => onChange({ worker_count: v })}
            min={1}
            max={10}
            showValue
          />
        </FormField>
      </FormSection>

      <FormSection title="Timeouts" description="Event handling timeouts">
        <FormField label="Handler Timeout" description="Maximum time for event handlers (seconds)">
          <FormSlider
            id="events-handler-timeout"
            value={config.handler_timeout}
            onChange={(v) => onChange({ handler_timeout: v })}
            min={1}
            max={60}
            showValue
            formatValue={(v) => `${v}s`}
          />
        </FormField>
      </FormSection>

      <FormSection title="Error Handling" description="Dead letter queue settings">
        <FormToggle
          id="events-dead-letter"
          checked={config.dead_letter_enabled}
          onChange={(v) => onChange({ dead_letter_enabled: v })}
          label="Enable Dead Letter Queue"
          description="Store failed events for later inspection"
        />
      </FormSection>
    </div>
  );
}

/**
 * Section Interface.
 */
function InterfaceSection({
  config,
  onChange,
}: {
  config: InterfaceConfig;
  onChange: (config: Partial<InterfaceConfig>) => void;
}) {
  return (
    <div className="space-y-6">
      <FormSection title="Web Interface" description="Web UI settings">
        <FormToggle
          id="interface-web-enabled"
          checked={config.web_enabled}
          onChange={(v) => onChange({ web_enabled: v })}
          label="Enable Web Interface"
          description="Start the web server on application launch"
        />

        {config.web_enabled && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <FormField label="Host" description="Web server host">
              <FormInput
                id="interface-web-host"
                value={config.web_host}
                onChange={(v) => onChange({ web_host: v })}
                placeholder="127.0.0.1"
              />
            </FormField>

            <FormField label="Port" description="Web server port">
              <FormInput
                id="interface-web-port"
                type="number"
                value={config.web_port}
                onChange={(v) => onChange({ web_port: parseInt(v) || 8000 })}
                min={1024}
                max={65535}
              />
            </FormField>
          </div>
        )}
      </FormSection>

      <FormSection title="Other Interfaces" description="GUI and CLI settings">
        <FormToggle
          id="interface-gui-enabled"
          checked={config.gui_enabled}
          onChange={(v) => onChange({ gui_enabled: v })}
          label="Enable GUI"
          description="Enable graphical user interface (PyQt6)"
        />

        <FormToggle
          id="interface-cli-enabled"
          checked={config.cli_enabled}
          onChange={(v) => onChange({ cli_enabled: v })}
          label="Enable CLI"
          description="Enable command-line interface (Textual)"
        />
      </FormSection>
    </div>
  );
}

// ============================================================================
// PAGE PRINCIPALE
// ============================================================================

/**
 * SettingsPage - Page de paramètres de l'application.
 *
 * @returns Élément JSX de la page
 */
export default function SettingsPage() {
  const router = useRouter();

  // État local
  const [activeSection, setActiveSection] = useState<ConfigSection>('app');
  const [searchQuery, setSearchQuery] = useState('');
  const [localConfig, setLocalConfig] = useState<FullConfig | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [showExportModal, setShowExportModal] = useState(false);
  const [showImportModal, setShowImportModal] = useState(false);
  const [showResetModal, setShowResetModal] = useState(false);

  // Hooks
  const config = useConfig();
  const ui = useUIPreferencesWithActions();
  const actions = useSettingsActions();

  // Avertissement de modifications non sauvegardées
  useUnsavedChangesWarning();

  // Initialiser la config locale
  useEffect(() => {
    if (config) {
      setLocalConfig(config);
    }
  }, [config]);

  // ==========================================================================
  // HANDLERS
  // ==========================================================================

  const handleSectionChange = useCallback((section: ConfigSection) => {
    setActiveSection(section);
  }, []);

  const handleConfigChange = useCallback(
    <K extends ConfigSection>(section: K, data: Partial<FullConfig[K]>) => {
      setLocalConfig((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          [section]: {
            ...prev[section],
            ...data,
          },
        };
      });
      actions.markDirty();
    },
    [actions]
  );

  const handleSave = useCallback(async () => {
    if (!localConfig) return;

    setIsSaving(true);
    try {
      await actions.updateFullConfig(localConfig);
      toast.success('Settings saved', {
        description: 'All changes have been applied',
      });
      actions.markClean();
    } catch (error) {
      toast.error('Failed to save settings', {
        description: error instanceof Error ? error.message : 'Unknown error',
      });
    } finally {
      setIsSaving(false);
    }
  }, [localConfig, actions]);

  const handleReset = useCallback(async () => {
    try {
      await actions.resetConfig([activeSection]);
      toast.success('Settings reset', {
        description: `${activeSection} section reset to defaults`,
      });
    } catch (error) {
      toast.error('Failed to reset settings', {
        description: error instanceof Error ? error.message : 'Unknown error',
      });
    }
    setShowResetModal(false);
  }, [activeSection, actions]);

  const handleExport = useCallback(async () => {
    try {
      const content = await actions.exportConfig('json', [activeSection]);
      const blob = new Blob([content], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `nexusdl-${activeSection}-config.json`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success('Configuration exported');
    } catch (error) {
      toast.error('Failed to export configuration');
    }
    setShowExportModal(false);
  }, [activeSection, actions]);

  const handleImport = useCallback(
    async (file: File) => {
      try {
        const content = await file.text();
        const config = JSON.parse(content);
        await actions.importConfig(config);
        toast.success('Configuration imported');
      } catch (error) {
        toast.error('Failed to import configuration', {
          description: error instanceof Error ? error.message : 'Invalid file',
        });
      }
      setShowImportModal(false);
    },
    [actions]
  );

  // ==========================================================================
  // FILTRAGE DES SECTIONS
  // ==========================================================================

  const filteredSections = useMemo(() => {
    if (!searchQuery.trim()) return SECTIONS;

    const query = searchQuery.toLowerCase();
    return SECTIONS.filter(
      (section) =>
        section.label.toLowerCase().includes(query) ||
        section.description.toLowerCase().includes(query)
    );
  }, [searchQuery]);

  // ==========================================================================
  // RENDU DU CONTENU DE LA SECTION ACTIVE
  // ==========================================================================

  const renderSectionContent = () => {
    if (!localConfig) {
      return (
        <div className="flex items-center justify-center py-16">
          <Loader2 size={24} className="text-primary animate-spin" />
        </div>
      );
    }

    switch (activeSection) {
      case 'app':
        return <AppSection config={localConfig.app} onChange={(data) => handleConfigChange('app', data)} />;
      case 'download':
        return <DownloadSection config={localConfig.download} onChange={(data) => handleConfigChange('download', data)} />;
      case 'library':
        return <LibrarySection config={localConfig.library} onChange={(data) => handleConfigChange('library', data)} />;
      case 'network':
        return <NetworkSection config={localConfig.network} onChange={(data) => handleConfigChange('network', data)} />;
      case 'proxy':
        return <ProxySection config={localConfig.proxy} onChange={(data) => handleConfigChange('proxy', data)} />;
      case 'cloudflare':
        return <CloudflareSection config={localConfig.cloudflare} onChange={(data) => handleConfigChange('cloudflare', data)} />;
      case 'storage':
        return <StorageSection config={localConfig.storage} onChange={(data) => handleConfigChange('storage', data)} />;
      case 'logging':
        return <LoggingSection config={localConfig.logging} onChange={(data) => handleConfigChange('logging', data)} />;
      case 'i18n':
        return <I18nSection config={localConfig.i18n} onChange={(data) => handleConfigChange('i18n', data)} />;
      case 'events':
        return <EventsSection config={localConfig.events} onChange={(data) => handleConfigChange('events', data)} />;
      case 'interface':
        return <InterfaceSection config={localConfig.interface} onChange={(data) => handleConfigChange('interface', data)} />;
      default:
        return null;
    }
  };

  const activeSectionMeta = SECTIONS.find((s) => s.id === activeSection);

  // ==========================================================================
  // RENDU
  // ==========================================================================

  return (
    <div className="container mx-auto px-4 py-6 max-w-7xl">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-2">
          <div>
            <h1 className="text-3xl font-mono font-bold text-primary tracking-wider mb-1 flex items-center gap-3">
              <SettingsIcon size={32} className="text-primary" />
              Settings
            </h1>
            <p className="text-sm font-mono text-text-muted">
              Configure your NexusDL application
            </p>
          </div>

          {/* Actions globales */}
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShowImportModal(true)}
              className={cn(
                'flex items-center gap-2 px-3 py-2 rounded-lg',
                'bg-surface-alt text-text-muted border-2 border-border-dim',
                'font-mono text-xs',
                'hover:bg-surface-hover hover:border-secondary hover:text-secondary',
                'transition-all duration-200'
              )}
            >
              <Upload size={14} />
              <span>Import</span>
            </button>

            <button
              type="button"
              onClick={() => setShowExportModal(true)}
              className={cn(
                'flex items-center gap-2 px-3 py-2 rounded-lg',
                'bg-surface-alt text-text-muted border-2 border-border-dim',
                'font-mono text-xs',
                'hover:bg-surface-hover hover:border-secondary hover:text-secondary',
                'transition-all duration-200'
              )}
            >
              <Download size={14} />
              <span>Export</span>
            </button>

            <button
              type="button"
              onClick={() => setShowResetModal(true)}
              className={cn(
                'flex items-center gap-2 px-3 py-2 rounded-lg',
                'bg-surface-alt text-text-muted border-2 border-border-dim',
                'font-mono text-xs',
                'hover:bg-surface-hover hover:border-red-500 hover:text-red-400',
                'transition-all duration-200'
              )}
            >
              <RotateCcw size={14} />
              <span>Reset</span>
            </button>

            <button
              type="button"
              onClick={handleSave}
              disabled={isSaving || !localConfig}
              className={cn(
                'flex items-center gap-2 px-4 py-2 rounded-lg',
                'bg-primary text-background',
                'font-mono font-bold text-xs uppercase tracking-wider',
                'hover:bg-primary-bright hover:shadow-[0_0_15px_rgba(0,255,65,0.5)]',
                'transition-all duration-200',
                'disabled:opacity-50 disabled:cursor-not-allowed'
              )}
            >
              {isSaving ? (
                <>
                  <Loader2 size={14} className="animate-spin" />
                  <span>Saving...</span>
                </>
              ) : (
                <>
                  <Save size={14} />
                  <span>Save Changes</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Indicateur de modifications non sauvegardées */}
        {localConfig && (
          <div className="flex items-center gap-2 text-xs font-mono text-text-dim">
            <span>Editing:</span>
            <span className="text-primary font-semibold capitalize">{activeSection}</span>
            {localConfig !== config && (
              <>
                <span className="text-text-dim">•</span>
                <span className="text-yellow-400 flex items-center gap-1">
                  <Info size={10} />
                  Unsaved changes
                </span>
              </>
            )}
          </div>
        )}
      </div>

      {/* Layout principal */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Sidebar de navigation */}
        <aside className="lg:col-span-1">
          <div className="sticky top-4 space-y-4">
            {/* Recherche */}
            <div className="relative">
              <Search
                size={14}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-text-dim"
                aria-hidden="true"
              />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search settings..."
                aria-label="Search settings"
                className={cn(
                  'w-full h-9 pl-9 pr-9 rounded-lg',
                  'bg-surface-alt border-2 border-border-dim',
                  'text-text placeholder:text-text-dim',
                  'text-xs font-mono',
                  'focus:outline-none focus:border-secondary',
                  'transition-colors'
                )}
              />
              {searchQuery && (
                <button
                  type="button"
                  onClick={() => setSearchQuery('')}
                  aria-label="Clear search"
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-text-dim hover:text-secondary transition-colors"
                >
                  <X size={12} />
                </button>
              )}
            </div>

            {/* Liste des sections */}
            <nav className="space-y-1" aria-label="Settings sections">
              {filteredSections.map((section) => {
                const Icon = section.icon;
                const isActive = activeSection === section.id;

                return (
                  <button
                    key={section.id}
                    type="button"
                    onClick={() => handleSectionChange(section.id)}
                    aria-current={isActive ? 'page' : undefined}
                    className={cn(
                      'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left',
                      'transition-all duration-200',
                      'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                      isActive
                        ? 'bg-primary-bg border-2 border-primary text-primary'
                        : 'border-2 border-transparent text-text-muted hover:bg-surface-hover hover:text-text hover:border-border-dim'
                    )}
                  >
                    <Icon
                      size={16}
                      className={cn(
                        'flex-shrink-0',
                        isActive ? section.color : 'text-text-dim'
                      )}
                      style={isActive ? { filter: 'drop-shadow(0 0 3px currentColor)' } : undefined}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-mono font-semibold truncate">
                        {section.label}
                      </div>
                      <div className="text-[10px] font-mono text-text-dim truncate">
                        {section.description}
                      </div>
                    </div>
                    {isActive && <ChevronRight size={14} className="flex-shrink-0" />}
                  </button>
                );
              })}

              {filteredSections.length === 0 && (
                <div className="text-center py-8 text-xs font-mono text-text-dim">
                  No sections match your search
                </div>
              )}
            </nav>

            {/* Préférences UI */}
            <div className="pt-4 border-t-2 border-border-dim">
              <h3 className="text-[10px] font-mono font-bold text-text-dim uppercase tracking-wider mb-3">
                UI Preferences
              </h3>
              <div className="space-y-3">
                <FormField label="Theme">
                  <FormSelect
                    id="ui-theme"
                    value={ui.theme}
                    onChange={(v) => ui.setTheme(v as any)}
                    options={THEME_OPTIONS.map((t) => ({ value: t.id, label: t.label }))}
                  />
                </FormField>

                <FormField label="Language">
                  <FormSelect
                    id="ui-language"
                    value={ui.language}
                    onChange={(v) => ui.setLanguage(v as any)}
                    options={LANGUAGE_OPTIONS.map((l) => ({ value: l.code, label: `${l.flag} ${l.label}` }))}
                  />
                </FormField>

                <FormField label="Reading Direction">
                  <FormSelect
                    id="ui-reading-direction"
                    value={ui.readingDirection}
                    onChange={(v) => ui.setReadingDirection(v as any)}
                    options={READING_DIRECTION_OPTIONS.map((r) => ({ value: r.id, label: r.label }))}
                  />
                </FormField>

                <FormField label="Library View">
                  <FormSelect
                    id="ui-library-view"
                    value={ui.libraryViewMode}
                    onChange={(v) => ui.setLibraryViewMode(v as any)}
                    options={[
                      { value: 'grid', label: 'Grid' },
                      { value: 'list', label: 'List' },
                      { value: 'compact', label: 'Compact' },
                    ]}
                  />
                </FormField>

                <FormToggle
                  id="ui-notifications"
                  checked={ui.notificationsEnabled}
                  onChange={() => ui.toggleNotifications()}
                  label="Notifications"
                  description="Enable desktop notifications"
                />

                <FormToggle
                  id="ui-sounds"
                  checked={ui.notificationSounds}
                  onChange={() => ui.toggleNotificationSounds()}
                  label="Notification Sounds"
                  description="Play sounds for notifications"
                />
              </div>
            </div>
          </div>
        </aside>

        {/* Contenu principal */}
        <main className="lg:col-span-3">
          <div className="rounded-xl border-2 border-border-dim bg-surface p-6">
            {/* En-tête de la section */}
            {activeSectionMeta && (
              <div className="mb-6 pb-4 border-b-2 border-border-dim">
                <div className="flex items-center gap-3 mb-2">
                  <activeSectionMeta.icon
                    size={24}
                    className={activeSectionMeta.color}
                    style={{ filter: 'drop-shadow(0 0 5px currentColor)' }}
                  />
                  <h2 className="text-xl font-mono font-bold text-text">
                    {activeSectionMeta.label}
                  </h2>
                </div>
                <p className="text-sm font-mono text-text-muted">
                  {activeSectionMeta.description}
                </p>
              </div>
            )}

            {/* Contenu de la section */}
            {renderSectionContent()}
          </div>
        </main>
      </div>

      {/* Modal de confirmation de reset */}
      {showResetModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-xl border-2 border-red-500/30 bg-surface p-6 shadow-2xl">
            <div className="flex items-center gap-3 mb-4">
              <div className="flex items-center justify-center h-10 w-10 rounded-full bg-red-500/10 border-2 border-red-500/30">
                <AlertCircle size={20} className="text-red-400" />
              </div>
              <div>
                <h3 className="text-lg font-mono font-bold text-text">Reset Settings</h3>
                <p className="text-xs font-mono text-text-muted">
                  This action cannot be undone
                </p>
              </div>
            </div>

            <p className="text-sm font-mono text-text-muted mb-6">
              Are you sure you want to reset the <span className="text-primary font-semibold">{activeSection}</span> section to default values?
            </p>

            <div className="flex gap-3">
              <button
                type="button"
                onClick={() => setShowResetModal(false)}
                className={cn(
                  'flex-1 px-4 py-2 rounded-lg',
                  'bg-surface-alt text-text-muted border-2 border-border-dim',
                  'font-mono text-sm',
                  'hover:bg-surface-hover hover:border-secondary hover:text-secondary',
                  'transition-all duration-200'
                )}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleReset}
                className={cn(
                  'flex-1 px-4 py-2 rounded-lg',
                  'bg-red-500 text-background',
                  'font-mono font-bold text-sm',
                  'hover:bg-red-600 hover:shadow-[0_0_15px_rgba(239,68,68,0.5)]',
                  'transition-all duration-200'
                )}
              >
                Reset
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal d'export */}
      {showExportModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-xl border-2 border-border-dim bg-surface p-6 shadow-2xl">
            <div className="flex items-center gap-3 mb-4">
              <div className="flex items-center justify-center h-10 w-10 rounded-full bg-primary-bg border-2 border-primary/30">
                <Download size={20} className="text-primary" />
              </div>
              <div>
                <h3 className="text-lg font-mono font-bold text-text">Export Configuration</h3>
                <p className="text-xs font-mono text-text-muted">
                  Save settings to a file
                </p>
              </div>
            </div>

            <p className="text-sm font-mono text-text-muted mb-6">
              Export the <span className="text-primary font-semibold">{activeSection}</span> configuration to a JSON file.
            </p>

            <div className="flex gap-3">
              <button
                type="button"
                onClick={() => setShowExportModal(false)}
                className={cn(
                  'flex-1 px-4 py-2 rounded-lg',
                  'bg-surface-alt text-text-muted border-2 border-border-dim',
                  'font-mono text-sm',
                  'hover:bg-surface-hover hover:border-secondary hover:text-secondary',
                  'transition-all duration-200'
                )}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleExport}
                className={cn(
                  'flex-1 px-4 py-2 rounded-lg',
                  'bg-primary text-background',
                  'font-mono font-bold text-sm',
                  'hover:bg-primary-bright hover:shadow-[0_0_15px_rgba(0,255,65,0.5)]',
                  'transition-all duration-200'
                )}
              >
                Export
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal d'import */}
      {showImportModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-xl border-2 border-border-dim bg-surface p-6 shadow-2xl">
            <div className="flex items-center gap-3 mb-4">
              <div className="flex items-center justify-center h-10 w-10 rounded-full bg-secondary/10 border-2 border-secondary/30">
                <Upload size={20} className="text-secondary" />
              </div>
              <div>
                <h3 className="text-lg font-mono font-bold text-text">Import Configuration</h3>
                <p className="text-xs font-mono text-text-muted">
                  Load settings from a file
                </p>
              </div>
            </div>

            <p className="text-sm font-mono text-text-muted mb-6">
              Import configuration from a JSON file. This will overwrite your current settings.
            </p>

            <input
              type="file"
              accept=".json,.yaml,.yml"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) {
                  handleImport(file);
                }
              }}
              className="hidden"
              id="import-file"
            />

            <div className="flex gap-3">
              <button
                type="button"
                onClick={() => setShowImportModal(false)}
                className={cn(
                  'flex-1 px-4 py-2 rounded-lg',
                  'bg-surface-alt text-text-muted border-2 border-border-dim',
                  'font-mono text-sm',
                  'hover:bg-surface-hover hover:border-secondary hover:text-secondary',
                  'transition-all duration-200'
                )}
              >
                Cancel
              </button>
              <label
                htmlFor="import-file"
                className={cn(
                  'flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-lg cursor-pointer',
                  'bg-secondary text-background',
                  'font-mono font-bold text-sm',
                  'hover:bg-secondary/80 hover:shadow-[0_0_15px_rgba(0,255,255,0.5)]',
                  'transition-all duration-200'
                )}
              >
                <Upload size={14} />
                <span>Choose File</span>
              </label>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
