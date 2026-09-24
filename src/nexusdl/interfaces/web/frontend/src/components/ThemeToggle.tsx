/**
 * Composant ThemeToggle pour NexusDL.
 *
 * Permet à l'utilisateur de basculer entre les différents thèmes de l'application :
 *   - Cyberpunk (néon, thème par défaut)
 *   - Dark (sombre classique)
 *   - Light (clair)
 *   - System (auto, basé sur les préférences OS)
 *
 * Caractéristiques :
 *   - Menu popover avec les 4 options de thème
 *   - Icônes Lucide React pour chaque thème
 *   - Animation de transition fluide
 *   - Accessibilité complète (ARIA, clavier, focus)
 *   - Fermeture au clic extérieur et à la touche Escape
 *   - Intégration avec le store Zustand (settings)
 *   - Style cyberpunk néon cohérent avec l'identité visuelle
 *   - Tooltip au hover
 *   - Responsive (mobile/desktop)
 *
 * Utilisation :
 *   <ThemeToggle />
 *   // Ou avec variante compacte
 *   <ThemeToggle compact />
 *
 * @module components/ThemeToggle
 */

'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Laptop, Moon, Sparkles, Sun } from 'lucide-react';

import { cn } from '@/lib/utils';
import { useUIPreferencesWithActions } from '@/store';
import type { UITheme } from '@/store/settings';

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

/**
 * Définition d'une option de thème.
 */
interface ThemeOption {
  /** Identifiant du thème. */
  id: UITheme;
  /** Libellé affiché. */
  label: string;
  /** Description courte. */
  description: string;
  /** Icône Lucide associée. */
  icon: React.ComponentType<{ className?: string; size?: number }>;
  /** Couleur d'accent pour l'indicateur visuel. */
  accentColor: string;
}

/**
 * Props du composant ThemeToggle.
 */
export interface ThemeToggleProps {
  /** Variante compacte (icône seule sans label). */
  compact?: boolean;
  /** Classe CSS additionnelle. */
  className?: string;
  /** Taille du bouton. */
  size?: 'sm' | 'md' | 'lg';
  /** Afficher le tooltip au hover. */
  showTooltip?: boolean;
  /** Callback appelé quand le thème change. */
  onThemeChange?: (theme: UITheme) => void;
}

// ============================================================================
// CONSTANTES
// ============================================================================

/**
 * Liste des thèmes disponibles avec leurs métadonnées.
 */
const THEME_OPTIONS: ThemeOption[] = [
  {
    id: 'cyberpunk',
    label: 'Cyberpunk',
    description: 'Neon hacker style',
    icon: Sparkles,
    accentColor: '#00ff41',
  },
  {
    id: 'dark',
    label: 'Dark',
    description: 'Classic dark mode',
    icon: Moon,
    accentColor: '#00ffff',
  },
  {
    id: 'light',
    label: 'Light',
    description: 'Clean light mode',
    icon: Sun,
    accentColor: '#ffff00',
  },
  {
    id: 'system',
    label: 'System',
    description: 'Match OS preference',
    icon: Laptop,
    accentColor: '#ff00ff',
  },
];

/**
 * Tailles disponibles pour le bouton.
 */
const SIZE_CLASSES = {
  sm: {
    button: 'h-8 w-8 text-sm',
    icon: 14,
    menu: 'min-w-[180px]',
  },
  md: {
    button: 'h-10 w-10 text-base',
    icon: 18,
    menu: 'min-w-[220px]',
  },
  lg: {
    button: 'h-12 w-12 text-lg',
    icon: 22,
    menu: 'min-w-[260px]',
  },
} as const;

// ============================================================================
// COMPOSANT PRINCIPAL
// ============================================================================

/**
 * ThemeToggle - Composant de sélection de thème.
 *
 * Affiche un bouton qui ouvre un menu popover permettant de choisir
 * parmi les 4 thèmes disponibles. S'intègre avec le store Zustand
 * pour persister la préférence utilisateur.
 *
 * @param props - Props du composant
 * @returns Élément JSX du ThemeToggle
 *
 * @example
 * ```tsx
 * // Utilisation basique
 * <ThemeToggle />
 *
 * // Variante compacte (icône seule)
 * <ThemeToggle compact />
 *
 * // Avec callback
 * <ThemeToggle onThemeChange={(theme) => console.log('New theme:', theme)} />
 *
 * // Grande taille
 * <ThemeToggle size="lg" />
 * ```
 */
export function ThemeToggle({
  compact = false,
  className,
  size = 'md',
  showTooltip = true,
  onThemeChange,
}: ThemeToggleProps) {
  // État local
  const [isOpen, setIsOpen] = useState(false);
  const [isHovered, setIsHovered] = useState(false);

  // Refs
  const containerRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  // Store Zustand
  const { theme, setTheme } = useUIPreferencesWithActions();

  // Thème actuel
  const currentTheme = THEME_OPTIONS.find((t) => t.id === theme) || THEME_OPTIONS[0];
  const CurrentIcon = currentTheme.icon;

  // ==========================================================================
  // HANDLERS
  // ==========================================================================

  /**
   * Ouvre/ferme le menu.
   */
  const handleToggle = useCallback(() => {
    setIsOpen((prev) => !prev);
  }, []);

  /**
   * Sélectionne un thème.
   */
  const handleSelect = useCallback(
    (themeId: UITheme) => {
      if (themeId !== theme) {
        setTheme(themeId);
        onThemeChange?.(themeId);
      }
      setIsOpen(false);
      // Retourner le focus sur le bouton
      buttonRef.current?.focus();
    },
    [theme, setTheme, onThemeChange]
  );

  /**
   * Gère la navigation clavier dans le menu.
   */
  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (!isOpen) {
        if (event.key === 'Enter' || event.key === ' ' || event.key === 'ArrowDown') {
          event.preventDefault();
          setIsOpen(true);
        }
        return;
      }

      switch (event.key) {
        case 'Escape':
          event.preventDefault();
          setIsOpen(false);
          buttonRef.current?.focus();
          break;

        case 'ArrowDown':
          event.preventDefault();
          // Focus sur le premier élément du menu
          const firstItem = menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]');
          firstItem?.focus();
          break;

        case 'ArrowUp':
          event.preventDefault();
          // Focus sur le dernier élément du menu
          const items = menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]');
          if (items && items.length > 0) {
            items[items.length - 1].focus();
          }
          break;

        case 'Tab':
          // Fermer le menu si on tab en dehors
          setIsOpen(false);
          break;
      }
    },
    [isOpen]
  );

  /**
   * Gère la navigation clavier dans les items du menu.
   */
  const handleItemKeyDown = useCallback(
    (event: React.KeyboardEvent, index: number) => {
      const items = menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]');
      if (!items) return;

      switch (event.key) {
        case 'ArrowDown':
          event.preventDefault();
          const nextIndex = (index + 1) % items.length;
          items[nextIndex].focus();
          break;

        case 'ArrowUp':
          event.preventDefault();
          const prevIndex = (index - 1 + items.length) % items.length;
          items[prevIndex].focus();
          break;

        case 'Home':
          event.preventDefault();
          items[0].focus();
          break;

        case 'End':
          event.preventDefault();
          items[items.length - 1].focus();
          break;

        case 'Escape':
          event.preventDefault();
          setIsOpen(false);
          buttonRef.current?.focus();
          break;

        case 'Enter':
        case ' ':
          event.preventDefault();
          const themeId = items[index].dataset.themeId as UITheme;
          if (themeId) {
            handleSelect(themeId);
          }
          break;
      }
    },
    [handleSelect]
  );

  // ==========================================================================
  // EFFECTS
  // ==========================================================================

  /**
   * Ferme le menu au clic extérieur.
   */
  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    };

    // Délai pour éviter la fermeture immédiate
    const timeoutId = setTimeout(() => {
      document.addEventListener('mousedown', handleClickOutside);
    }, 0);

    return () => {
      clearTimeout(timeoutId);
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen]);

  /**
   * Ferme le menu quand on scroll.
   */
  useEffect(() => {
    if (!isOpen) return;

    const handleScroll = () => {
      setIsOpen(false);
    };

    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, [isOpen]);

  /**
   * Auto-focus sur le premier item quand le menu s'ouvre.
   */
  useEffect(() => {
    if (isOpen && menuRef.current) {
      const firstItem = menuRef.current.querySelector<HTMLButtonElement>('[role="menuitem"]');
      // Petit délai pour laisser l'animation se jouer
      const timeoutId = setTimeout(() => {
        firstItem?.focus();
      }, 50);
      return () => clearTimeout(timeoutId);
    }
  }, [isOpen]);

  // ==========================================================================
  // RENDU
  // ==========================================================================

  const sizeConfig = SIZE_CLASSES[size];

  return (
    <div
      ref={containerRef}
      className={cn('relative inline-block', className)}
      onKeyDown={handleKeyDown}
    >
      {/* ================================================================ */}
      {/* BOUTON PRINCIPAL */}
      {/* ================================================================ */}
      <button
        ref={buttonRef}
        type="button"
        onClick={handleToggle}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        aria-label={`Current theme: ${currentTheme.label}. Click to change theme.`}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        aria-controls="theme-menu"
        className={cn(
          // Base
          'group relative inline-flex items-center justify-center',
          'rounded-lg border-2 transition-all duration-200',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2',
          'focus-visible:ring-offset-background',

          // Style cyberpunk néon
          'border-border-dim bg-surface hover:bg-surface-hover',
          'hover:border-secondary',
          'focus-visible:ring-secondary',

          // Taille
          sizeConfig.button,

          // État ouvert
          isOpen && 'border-primary bg-primary-bg',

          // Animation du glow au hover
          'hover:shadow-[0_0_10px_rgba(0,255,255,0.3)]',
          isOpen && 'shadow-[0_0_15px_rgba(0,255,65,0.4)]'
        )}
      >
        {/* Icône du thème actuel */}
        <CurrentIcon
          size={sizeConfig.icon}
          className={cn(
            'transition-all duration-300',
            'text-text-muted group-hover:text-secondary',
            isOpen && 'text-primary',

            // Animation de rotation au hover
            isHovered && !isOpen && 'rotate-12 scale-110'
          )}
          style={
            isOpen
              ? { filter: `drop-shadow(0 0 4px ${currentTheme.accentColor})` }
              : undefined
          }
        />

        {/* Indicateur de thème actif (petit point coloré) */}
        <span
          className={cn(
            'absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full',
            'border border-background',
            'transition-all duration-300',
            isOpen ? 'scale-100 opacity-100' : 'scale-75 opacity-75'
          )}
          style={{ backgroundColor: currentTheme.accentColor }}
          aria-hidden="true"
        />
      </button>

      {/* ================================================================ */}
      {/* TOOLTIP AU HOVER */}
      {/* ================================================================ */}
      {showTooltip && !isOpen && isHovered && (
        <div
          role="tooltip"
          className={cn(
            'pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2',
            'mb-2 whitespace-nowrap rounded-md px-2 py-1',
            'bg-surface-alt border border-border-dim',
            'text-xs text-text-muted font-mono',
            'shadow-lg',
            'animate-in fade-in-0 zoom-in-95 duration-150'
          )}
        >
          Theme: {currentTheme.label}
          {/* Flèche du tooltip */}
          <div
            className="absolute left-1/2 top-full -translate-x-1/2 border-4 border-transparent border-t-surface-alt"
            aria-hidden="true"
          />
        </div>
      )}

      {/* ================================================================ */}
      {/* MENU POPOVER */}
      {/* ================================================================ */}
      {isOpen && (
        <div
          ref={menuRef}
          id="theme-menu"
          role="menu"
          aria-label="Select theme"
          className={cn(
            // Position
            'absolute right-0 top-full mt-2 z-50',

            // Style
            'rounded-lg border-2 border-border-dim',
            'bg-surface shadow-2xl',
            'overflow-hidden',

            // Taille
            sizeConfig.menu,

            // Animation d'entrée
            'animate-in fade-in-0 zoom-in-95 slide-in-from-top-2 duration-200',

            // Glow subtil
            'shadow-[0_0_20px_rgba(0,255,65,0.15)]'
          )}
        >
          {/* En-tête du menu */}
          {!compact && (
            <div
              className="px-3 py-2 border-b border-border-dim bg-surface-alt"
              aria-hidden="true"
            >
              <p className="text-xs font-mono font-bold text-primary tracking-wider uppercase">
                Select Theme
              </p>
            </div>
          )}

          {/* Liste des options */}
          <div className="p-1">
            {THEME_OPTIONS.map((option, index) => {
              const Icon = option.icon;
              const isSelected = option.id === theme;

              return (
                <button
                  key={option.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={isSelected}
                  data-theme-id={option.id}
                  onClick={() => handleSelect(option.id)}
                  onKeyDown={(e) => handleItemKeyDown(e, index)}
                  tabIndex={isOpen ? 0 : -1}
                  className={cn(
                    // Base
                    'group/item relative w-full',
                    'flex items-center gap-3 px-3 py-2.5',
                    'rounded-md text-left',
                    'transition-all duration-150',
                    'focus:outline-none focus-visible:ring-2',
                    'focus-visible:ring-primary focus-visible:ring-offset-0',

                    // États
                    isSelected
                      ? 'bg-primary-bg text-primary border border-primary/30'
                      : 'text-text hover:bg-surface-hover hover:text-secondary border border-transparent',

                    // Effet hover avec accent coloré
                    !isSelected && 'hover:border-border-dim'
                  )}
                >
                  {/* Indicateur de sélection (check) */}
                  <div
                    className={cn(
                      'flex h-5 w-5 items-center justify-center',
                      'rounded-full border-2 transition-all',
                      isSelected
                        ? 'border-primary bg-primary/20'
                        : 'border-border-dim group-hover/item:border-secondary'
                    )}
                    aria-hidden="true"
                  >
                    {isSelected && (
                      <Check
                        size={12}
                        className="text-primary animate-in zoom-in duration-200"
                      />
                    )}
                  </div>

                  {/* Icône du thème */}
                  <Icon
                    size={18}
                    className={cn(
                      'transition-all duration-200',
                      isSelected
                        ? 'text-primary'
                        : 'text-text-muted group-hover/item:text-secondary'
                    )}
                    style={
                      isSelected
                        ? { filter: `drop-shadow(0 0 3px ${option.accentColor})` }
                        : undefined
                    }
                    aria-hidden="true"
                  />

                  {/* Texte */}
                  <div className="flex-1 min-w-0">
                    <div
                      className={cn(
                        'text-sm font-mono font-semibold',
                        isSelected ? 'text-primary' : 'text-text group-hover/item:text-secondary'
                      )}
                    >
                      {option.label}
                    </div>
                    {!compact && (
                      <div className="text-xs text-text-muted truncate">
                        {option.description}
                      </div>
                    )}
                  </div>

                  {/* Raccourci clavier (optionnel, affiché pour le thème actif) */}
                  {isSelected && !compact && (
                    <kbd
                      className={cn(
                        'hidden sm:inline-flex items-center gap-0.5',
                        'px-1.5 py-0.5 rounded text-[10px]',
                        'bg-background border border-border-dim',
                        'text-text-muted font-mono'
                      )}
                      aria-hidden="true"
                    >
                      active
                    </kbd>
                  )}

                  {/* Ligne d'accent colorée à gauche quand sélectionné */}
                  {isSelected && (
                    <span
                      className="absolute left-0 top-1/2 -translate-y-1/2 h-6 w-0.5 rounded-r"
                      style={{ backgroundColor: option.accentColor }}
                      aria-hidden="true"
                    />
                  )}
                </button>
              );
            })}
          </div>

          {/* Pied du menu avec info */}
          {!compact && (
            <div
              className="px-3 py-2 border-t border-border-dim bg-surface-alt"
              aria-hidden="true"
            >
              <p className="text-[10px] text-text-dim font-mono leading-tight">
                Use <kbd className="px-1 py-0.5 rounded bg-background border border-border-dim">↑↓</kbd> to navigate,{' '}
                <kbd className="px-1 py-0.5 rounded bg-background border border-border-dim">Enter</kbd> to select
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// VARIANTE COMPACTE (pour barres d'outils)
// ============================================================================

/**
 * ThemeToggleCompact - Version ultra-compacte du ThemeToggle.
 *
 * Affiche uniquement l'icône du thème actuel. Au clic, cycle directement
 * entre les thèmes (cyberpunk → dark → light → system → cyberpunk).
 *
 * @param props - Props du composant
 *
 * @example
 * ```tsx
 * <ThemeToggleCompact />
 * ```
 */
export function ThemeToggleCompact({ className }: { className?: string }) {
  const { theme, setTheme } = useUIPreferencesWithActions();

  const currentTheme = THEME_OPTIONS.find((t) => t.id === theme) || THEME_OPTIONS[0];
  const CurrentIcon = currentTheme.icon;

  const handleCycle = useCallback(() => {
    const currentIndex = THEME_OPTIONS.findIndex((t) => t.id === theme);
    const nextIndex = (currentIndex + 1) % THEME_OPTIONS.length;
    setTheme(THEME_OPTIONS[nextIndex].id);
  }, [theme, setTheme]);

  return (
    <button
      type="button"
      onClick={handleCycle}
      aria-label={`Current theme: ${currentTheme.label}. Click to cycle to next theme.`}
      title={`${currentTheme.label} (click to change)`}
      className={cn(
        'inline-flex items-center justify-center',
        'h-8 w-8 rounded-md',
        'border border-border-dim bg-surface',
        'text-text-muted hover:text-secondary',
        'hover:bg-surface-hover hover:border-secondary',
        'transition-all duration-200',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
        'hover:shadow-[0_0_8px_rgba(0,255,255,0.2)]',
        className
      )}
    >
      <CurrentIcon
        size={16}
        className="transition-transform duration-300 hover:rotate-12 hover:scale-110"
        style={{ filter: `drop-shadow(0 0 2px ${currentTheme.accentColor})` }}
      />
    </button>
  );
}

// ============================================================================
// VARIANTE INLINE (avec label visible)
// ============================================================================

/**
 * ThemeToggleInline - Version avec label visible à côté de l'icône.
 *
 * @param props - Props du composant
 *
 * @example
 * ```tsx
 * <ThemeToggleInline />
 * ```
 */
export function ThemeToggleInline({ className }: { className?: string }) {
  const { theme, setTheme } = useUIPreferencesWithActions();
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const currentTheme = THEME_OPTIONS.find((t) => t.id === theme) || THEME_OPTIONS[0];
  const CurrentIcon = currentTheme.icon;

  // Fermer au clic extérieur
  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  return (
    <div ref={containerRef} className={cn('relative inline-block', className)}>
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        className={cn(
          'inline-flex items-center gap-2',
          'h-9 px-3 rounded-md',
          'border border-border-dim bg-surface',
          'text-text hover:text-secondary',
          'hover:bg-surface-hover hover:border-secondary',
          'transition-all duration-200',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
          isOpen && 'border-primary bg-primary-bg'
        )}
      >
        <CurrentIcon
          size={16}
          style={{ filter: `drop-shadow(0 0 2px ${currentTheme.accentColor})` }}
        />
        <span className="text-sm font-mono font-medium">{currentTheme.label}</span>
      </button>

      {isOpen && (
        <div
          role="menu"
          className={cn(
            'absolute right-0 top-full mt-2 z-50',
            'min-w-[200px] rounded-lg border-2 border-border-dim',
            'bg-surface shadow-2xl overflow-hidden',
            'animate-in fade-in-0 zoom-in-95 duration-200'
          )}
        >
          <div className="p-1">
            {THEME_OPTIONS.map((option) => {
              const Icon = option.icon;
              const isSelected = option.id === theme;

              return (
                <button
                  key={option.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={isSelected}
                  onClick={() => {
                    setTheme(option.id);
                    setIsOpen(false);
                  }}
                  className={cn(
                    'w-full flex items-center gap-2 px-3 py-2 rounded-md text-left',
                    'transition-all duration-150',
                    'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                    isSelected
                      ? 'bg-primary-bg text-primary'
                      : 'text-text hover:bg-surface-hover hover:text-secondary'
                  )}
                >
                  <Icon size={16} aria-hidden="true" />
                  <span className="text-sm font-mono">{option.label}</span>
                  {isSelected && <Check size={14} className="ml-auto" />}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// EXPORTS
// ============================================================================

export default ThemeToggle;
export { ThemeToggleCompact, ThemeToggleInline, THEME_OPTIONS };
export type { ThemeOption };
