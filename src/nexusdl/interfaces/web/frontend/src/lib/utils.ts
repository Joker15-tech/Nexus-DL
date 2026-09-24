/**
 * Fonctions utilitaires globales pour le frontend NexusDL.
 *
 * Ce module centralise toutes les fonctions utilitaires réutilisables
 * à travers l'application, organisées par catégorie :
 *
 *   - String utilities     : manipulation de chaînes
 *   - Number utilities     : formatage de nombres
 *   - Date/Time utilities  : formatage de dates et durées
 *   - Object/Array utils   : manipulation de structures de données
 *   - Validation utilities : validation de données
 *   - URL utilities        : manipulation d'URLs
 *   - Storage utilities    : wrappers pour localStorage/sessionStorage
 *   - Async utilities      : debounce, throttle, retry, delay
 *   - UI utilities         : classnames, colors, accessibility
 *   - Domain utilities     : helpers spécifiques au domaine manga
 *
 * Dépendances :
 *   - clsx              : combinaison conditionnelle de classes CSS
 *   - tailwind-merge    : merge intelligent de classes Tailwind
 *
 * @module lib/utils
 */

import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

// ============================================================================
// CONSTANTS
// ============================================================================

/**
 * Palette de couleurs cyberpunk néon du projet.
 */
export const COLORS = {
  primary: '#00ff41',
  primaryDim: '#00cc33',
  primaryBright: '#39ff14',
  primaryBg: '#001a0d',
  secondary: '#00ffff',
  secondaryDim: '#00cccc',
  accent: '#ff00ff',
  accentDim: '#cc00cc',
  success: '#00ff41',
  warning: '#ffff00',
  error: '#ff0040',
  info: '#00ffff',
  background: '#000000',
  surface: '#0d1117',
  surfaceAlt: '#161b22',
  surfaceHover: '#1a1f2e',
  text: '#00ff41',
  textBright: '#ffffff',
  textMuted: '#00aaaa',
  textDim: '#006666',
  border: '#00ff41',
  borderDim: '#006622',
} as const;

/**
 * Unités de taille de fichier.
 */
const SIZE_UNITS = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB'] as const;

/**
 * Patterns de validation courants.
 */
export const PATTERNS = {
  email: /^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$/,
  url: /^https?:\/\/(www\.)?[-a-zA-Z0-9@:%._+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_+.~#?&//=]*)$/,
  username: /^[a-zA-Z0-9_-]{3,50}$/,
  id: /^[a-zA-Z0-9_\-\.]+$/,
  languageCode: /^[a-z]{2}$/,
  hexColor: /^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})$/,
  ipv4: /^(\d{1,3}\.){3}\d{1,3}$/,
  uuid: /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
} as const;

/**
 * Limites communes.
 */
export const LIMITS = {
  maxUsernameLength: 50,
  minUsernameLength: 3,
  maxPasswordLength: 128,
  minPasswordLength: 8,
  maxEmailLength: 254,
  maxQueryLength: 500,
  maxDescriptionLength: 10000,
  maxTagsPerItem: 50,
  maxItemsPerPage: 200,
  defaultItemsPerPage: 20,
} as const;

// ============================================================================
// UI UTILITIES - Classnames & Tailwind
// ============================================================================

/**
 * Combine des classes CSS avec support conditionnel et merge Tailwind intelligent.
 *
 * C'est la fonction standard pour gérer les classes dans les composants React + Tailwind.
 * Elle utilise clsx pour la combinaison conditionnelle et tailwind-merge pour
 * résoudre les conflits de classes Tailwind.
 *
 * @param inputs - Classes CSS, objets conditionnels, tableaux, etc.
 * @returns Chaîne de classes CSS mergée
 *
 * @example
 * ```tsx
 * // Classes conditionnelles
 * cn('px-4 py-2', isActive && 'bg-blue-500', { 'text-white': isPrimary })
 *
 * // Merge intelligent (évite les conflits)
 * cn('px-4', 'px-2') // → 'px-2' (la dernière gagne)
 *
 * // Avec variantes
 * const variants = {
 *   primary: 'bg-blue-500 text-white',
 *   secondary: 'bg-gray-200 text-black',
 * };
 * cn('px-4 py-2', variants[variant], className)
 * ```
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

// ============================================================================
// STRING UTILITIES
// ============================================================================

/**
 * Tronque une chaîne à une longueur maximale avec suffixe.
 *
 * @param str - Chaîne à tronquer
 * @param maxLength - Longueur maximale (incluant le suffixe)
 * @param suffix - Suffixe à ajouter (défaut: '...')
 * @returns Chaîne tronquée
 *
 * @example
 * ```ts
 * truncate('Hello World', 8) // → 'Hello...'
 * truncate('Hi', 10) // → 'Hi'
 * truncate('Long text here', 10, '…') // → 'Long text…'
 * ```
 */
export function truncate(str: string, maxLength: number, suffix: string = '...'): string {
  if (!str) return '';
  if (str.length <= maxLength) return str;
  return str.slice(0, maxLength - suffix.length).trimEnd() + suffix;
}

/**
 * Convertit une chaîne en slug URL-friendly.
 *
 * @param str - Chaîne à slugifier
 * @returns Slug en minuscules avec tirets
 *
 * @example
 * ```ts
 * slugify('Hello World!') // → 'hello-world'
 * slugify('One Piece - Chapter 1') // → 'one-piece-chapter-1'
 * slugify('日本語テスト') // → 'ri-ben-yu-tesuto' (si translitéré)
 * ```
 */
export function slugify(str: string): string {
  return str
    .toString()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '') // Supprime les accents
    .toLowerCase()
    .trim()
    .replace(/\s+/g, '-') // Espaces → tirets
    .replace(/[^\w\-]+/g, '') // Supprime caractères non-alphanum
    .replace(/\-\-+/g, '-') // Supprime tirets doubles
    .replace(/^-+/, '') // Supprime tiret initial
    .replace(/-+$/, ''); // Supprime tiret final
}

/**
 * Capitalise la première lettre d'une chaîne.
 *
 * @param str - Chaîne à capitaliser
 * @returns Chaîne avec première lettre en majuscule
 *
 * @example
 * ```ts
 * capitalize('hello') // → 'Hello'
 * capitalize('WORLD') // → 'WORLD'
 * capitalize('') // → ''
 * ```
 */
export function capitalize(str: string): string {
  if (!str) return '';
  return str.charAt(0).toUpperCase() + str.slice(1);
}

/**
 * Convertit une chaîne en Title Case (chaque mot capitalisé).
 *
 * @param str - Chaîne à convertir
 * @returns Chaîne en Title Case
 *
 * @example
 * ```ts
 * titleCase('hello world') // → 'Hello World'
 * titleCase('one-piece') // → 'One Piece'
 * titleCase('attack_on_titan') // → 'Attack On Titan'
 * ```
 */
export function titleCase(str: string): string {
  if (!str) return '';
  return str
    .replace(/[-_]/g, ' ')
    .split(' ')
    .map((word) => capitalize(word.toLowerCase()))
    .join(' ');
}

/**
 * Convertit du camelCase en snake_case.
 *
 * @param str - Chaîne en camelCase
 * @returns Chaîne en snake_case
 *
 * @example
 * ```ts
 * camelToSnake('mangaTitle') // → 'manga_title'
 * camelToSnake('userID') // → 'user_id'
 * ```
 */
export function camelToSnake(str: string): string {
  return str.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`);
}

/**
 * Convertit du snake_case en camelCase.
 *
 * @param str - Chaîne en snake_case
 * @returns Chaîne en camelCase
 *
 * @example
 * ```ts
 * snakeToCamel('manga_title') // → 'mangaTitle'
 * snakeToCamel('user_id') // → 'userId'
 * ```
 */
export function snakeToCamel(str: string): string {
  return str.replace(/_([a-z])/g, (_, letter) => letter.toUpperCase());
}

/**
 * Convertit une chaîne en kebab-case.
 *
 * @param str - Chaîne à convertir
 * @returns Chaîne en kebab-case
 *
 * @example
 * ```ts
 * toKebabCase('mangaTitle') // → 'manga-title'
 * toKebabCase('Hello World') // → 'hello-world'
 * ```
 */
export function toKebabCase(str: string): string {
  return str
    .replace(/([a-z])([A-Z])/g, '$1-$2')
    .replace(/[\s_]+/g, '-')
    .toLowerCase();
}

/**
 * Échappe les caractères HTML spéciaux pour éviter les injections XSS.
 *
 * @param str - Chaîne à échapper
 * @returns Chaîne échappée
 *
 * @example
 * ```ts
 * escapeHtml('<script>alert("xss")</script>')
 * // → '&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;'
 * ```
 */
export function escapeHtml(str: string): string {
  const htmlEntities: Record<string, string> = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
    '/': '&#x2F;',
    '`': '&#x60;',
    '=': '&#x3D;',
  };
  return str.replace(/[&<>"'`=/]/g, (char) => htmlEntities[char] || char);
}

/**
 * Génère une chaîne aléatoire de longueur donnée.
 *
 * @param length - Longueur de la chaîne (défaut: 16)
 * @param charset - Jeu de caractères à utiliser
 * @returns Chaîne aléatoire
 *
 * @example
 * ```ts
 * randomString(8) // → 'aB3xY9kL'
 * randomString(12, '0123456789') // → '472918364025'
 * ```
 */
export function randomString(
  length: number = 16,
  charset: string = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
): string {
  let result = '';
  const values = new Uint32Array(length);
  
  if (typeof window !== 'undefined' && window.crypto) {
    window.crypto.getRandomValues(values);
  } else {
    // Fallback pour Node.js
    for (let i = 0; i < length; i++) {
      values[i] = Math.floor(Math.random() * charset.length);
    }
  }
  
  for (let i = 0; i < length; i++) {
    result += charset[values[i] % charset.length];
  }
  
  return result;
}

/**
 * Génère un UUID v4.
 *
 * @returns UUID sous forme de string
 *
 * @example
 * ```ts
 * generateUUID() // → '550e8400-e29b-41d4-a716-446655440000'
 * ```
 */
export function generateUUID(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  
  // Fallback pour les environnements sans crypto.randomUUID
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// ============================================================================
// NUMBER UTILITIES
// ============================================================================

/**
 * Formate un nombre avec séparateurs de milliers.
 *
 * @param num - Nombre à formater
 * @param locale - Locale pour le formatage (défaut: 'en-US')
 * @returns Chaîne formatée
 *
 * @example
 * ```ts
 * formatNumber(1234567) // → '1,234,567'
 * formatNumber(1234567, 'fr-FR') // → '1 234 567'
 * formatNumber(1234.56) // → '1,234.56'
 * ```
 */
export function formatNumber(num: number, locale: string = 'en-US'): string {
  return new Intl.NumberFormat(locale).format(num);
}

/**
 * Formate un nombre en version compacte (1.2K, 3.4M, etc.).
 *
 * @param num - Nombre à formater
 * @param decimals - Nombre de décimales (défaut: 1)
 * @returns Chaîne compacte
 *
 * @example
 * ```ts
 * formatCompact(1234) // → '1.2K'
 * formatCompact(1234567) // → '1.2M'
 * formatCompact(1234567890) // → '1.2B'
 * formatCompact(500) // → '500'
 * ```
 */
export function formatCompact(num: number, decimals: number = 1): string {
  const formatter = new Intl.NumberFormat('en-US', {
    notation: 'compact',
    maximumFractionDigits: decimals,
  });
  return formatter.format(num);
}

/**
 * Formate un pourcentage.
 *
 * @param value - Valeur entre 0 et 1 (ou 0 et 100 si asDecimal=false)
 * @param decimals - Nombre de décimales (défaut: 0)
 * @param asDecimal - Si true, value est entre 0 et 1 (défaut: true)
 * @returns Chaîne formatée avec %
 *
 * @example
 * ```ts
 * formatPercent(0.5) // → '50%'
 * formatPercent(0.1234, 2) // → '12.34%'
 * formatPercent(50, 0, false) // → '50%'
 * ```
 */
export function formatPercent(
  value: number,
  decimals: number = 0,
  asDecimal: boolean = true
): string {
  const percent = asDecimal ? value * 100 : value;
  return `${percent.toFixed(decimals)}%`;
}

/**
 * Clamp une valeur entre un minimum et un maximum.
 *
 * @param value - Valeur à clamer
 * @param min - Valeur minimale
 * @param max - Valeur maximale
 * @returns Valeur clamée
 *
 * @example
 * ```ts
 * clamp(5, 0, 10) // → 5
 * clamp(-5, 0, 10) // → 0
 * clamp(15, 0, 10) // → 10
 * ```
 */
export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/**
 * Arrondit un nombre à un nombre spécifique de décimales.
 *
 * @param value - Valeur à arrondir
 * @param decimals - Nombre de décimales (défaut: 2)
 * @returns Valeur arrondie
 *
 * @example
 * ```ts
 * round(3.14159, 2) // → 3.14
 * round(3.14159, 4) // → 3.1416
 * round(1234, -2) // → 1200
 * ```
 */
export function round(value: number, decimals: number = 2): number {
  const multiplier = Math.pow(10, decimals);
  return Math.round(value * multiplier) / multiplier;
}

/**
 * Formate une taille en bytes en unité lisible (KB, MB, GB, etc.).
 *
 * @param bytes - Taille en bytes
 * @param decimals - Nombre de décimales (défaut: 2)
 * @returns Chaîne formatée avec unité
 *
 * @example
 * ```ts
 * formatBytes(0) // → '0 Bytes'
 * formatBytes(1024) // → '1 KB'
 * formatBytes(1234567) // → '1.18 MB'
 * formatBytes(1234567890) // → '1.15 GB'
 * ```
 */
export function formatBytes(bytes: number, decimals: number = 2): string {
  if (bytes === 0) return '0 Bytes';
  if (bytes < 0) return 'Invalid size';

  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const i = Math.floor(Math.log(bytes) / Math.log(k));

  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${SIZE_UNITS[i]}`;
}

/**
 * Parse une taille lisible en bytes.
 *
 * @param str - Chaîne à parser (ex: '1.5 MB', '2GB')
 * @returns Taille en bytes, ou NaN si invalide
 *
 * @example
 * ```ts
 * parseBytes('1 KB') // → 1024
 * parseBytes('1.5 MB') // → 1572864
 * parseBytes('2GB') // → 2147483648
 * parseBytes('invalid') // → NaN
 * ```
 */
export function parseBytes(str: string): number {
  const match = str.trim().match(/^(\d+(?:\.\d+)?)\s*([KMGTPE]?B?)?$/i);
  if (!match) return NaN;

  const value = parseFloat(match[1]);
  const unit = (match[2] || 'B').toUpperCase();

  const multipliers: Record<string, number> = {
    'B': 1,
    'KB': 1024,
    'K': 1024,
    'MB': 1024 ** 2,
    'M': 1024 ** 2,
    'GB': 1024 ** 3,
    'G': 1024 ** 3,
    'TB': 1024 ** 4,
    'T': 1024 ** 4,
    'PB': 1024 ** 5,
    'P': 1024 ** 5,
    'EB': 1024 ** 6,
    'E': 1024 ** 6,
  };

  return value * (multipliers[unit] || 1);
}

// ============================================================================
// DATE/TIME UTILITIES
// ============================================================================

/**
 * Formate une durée en secondes en chaîne lisible.
 *
 * @param seconds - Durée en secondes
 * @param compact - Si true, format compact (défaut: false)
 * @returns Chaîne formatée
 *
 * @example
 * ```ts
 * formatDuration(45) // → '45s'
 * formatDuration(125) // → '2m 5s'
 * formatDuration(3665) // → '1h 1m 5s'
 * formatDuration(90061) // → '1d 1h 1m 1s'
 * formatDuration(125, true) // → '2m'
 * ```
 */
export function formatDuration(seconds: number, compact: boolean = false): string {
  if (seconds <= 0 || !isFinite(seconds)) return '0s';

  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  const parts: string[] = [];

  if (days > 0) parts.push(`${days}d`);
  if (hours > 0) parts.push(`${hours}h`);
  if (minutes > 0) parts.push(`${minutes}m`);
  if (secs > 0 || parts.length === 0) parts.push(`${secs}s`);

  if (compact) {
    return parts.slice(0, 2).join(' ');
  }

  return parts.join(' ');
}

/**
 * Formate une date en chaîne relative ("il y a 5 minutes", "in 2 hours").
 *
 * @param date - Date à formater (ISO string, Date, ou timestamp)
 * @param locale - Locale pour le formatage (défaut: 'en')
 * @returns Chaîne relative
 *
 * @example
 * ```ts
 * formatRelativeTime(new Date(Date.now() - 60000)) // → '1 minute ago'
 * formatRelativeTime('2024-01-01T00:00:00Z') // → 'X days ago'
 * formatRelativeTime(Date.now() + 3600000, 'fr') // → 'dans 1 heure'
 * ```
 */
export function formatRelativeTime(
  date: string | Date | number,
  locale: string = 'en'
): string {
  const dateObj = typeof date === 'string' ? new Date(date) : 
                  typeof date === 'number' ? new Date(date) : date;
  
  const now = new Date();
  const diffInSeconds = Math.floor((now.getTime() - dateObj.getTime()) / 1000);
  const isFuture = diffInSeconds < 0;
  const absDiff = Math.abs(diffInSeconds);

  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });

  if (absDiff < 60) {
    return rtf.format(isFuture ? absDiff : -absDiff, 'second');
  } else if (absDiff < 3600) {
    const minutes = Math.floor(absDiff / 60);
    return rtf.format(isFuture ? minutes : -minutes, 'minute');
  } else if (absDiff < 86400) {
    const hours = Math.floor(absDiff / 3600);
    return rtf.format(isFuture ? hours : -hours, 'hour');
  } else if (absDiff < 2592000) {
    const days = Math.floor(absDiff / 86400);
    return rtf.format(isFuture ? days : -days, 'day');
  } else if (absDiff < 31536000) {
    const months = Math.floor(absDiff / 2592000);
    return rtf.format(isFuture ? months : -months, 'month');
  } else {
    const years = Math.floor(absDiff / 31536000);
    return rtf.format(isFuture ? years : -years, 'year');
  }
}

/**
 * Formate une date en chaîne lisible.
 *
 * @param date - Date à formater
 * @param format - Format ('full', 'date', 'time', 'datetime', 'iso')
 * @param locale - Locale (défaut: 'en-US')
 * @returns Chaîne formatée
 *
 * @example
 * ```ts
 * formatDate(new Date(), 'full') // → 'Monday, September 24, 2026'
 * formatDate(new Date(), 'date') // → '9/24/2026'
 * formatDate(new Date(), 'time') // → '2:30:45 PM'
 * formatDate(new Date(), 'datetime') // → '9/24/2026, 2:30 PM'
 * ```
 */
export function formatDate(
  date: string | Date | number,
  format: 'full' | 'date' | 'time' | 'datetime' | 'iso' = 'datetime',
  locale: string = 'en-US'
): string {
  const dateObj = typeof date === 'string' ? new Date(date) : 
                  typeof date === 'number' ? new Date(date) : date;

  if (format === 'iso') {
    return dateObj.toISOString();
  }

  const options: Intl.DateTimeFormatOptions = {
    full: { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' },
    date: { year: 'numeric', month: 'short', day: 'numeric' },
    time: { hour: '2-digit', minute: '2-digit', second: '2-digit' },
    datetime: { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' },
  }[format];

  return new Intl.DateTimeFormat(locale, options).format(dateObj);
}

/**
 * Vérifie si une date est aujourd'hui.
 *
 * @param date - Date à vérifier
 * @returns True si la date est aujourd'hui
 */
export function isToday(date: string | Date | number): boolean {
  const dateObj = typeof date === 'string' ? new Date(date) : 
                  typeof date === 'number' ? new Date(date) : date;
  const today = new Date();
  
  return dateObj.getFullYear() === today.getFullYear() &&
         dateObj.getMonth() === today.getMonth() &&
         dateObj.getDate() === today.getDate();
}

/**
 * Vérifie si une date est dans le passé.
 *
 * @param date - Date à vérifier
 * @returns True si la date est dans le passé
 */
export function isPast(date: string | Date | number): boolean {
  const dateObj = typeof date === 'string' ? new Date(date) : 
                  typeof date === 'number' ? new Date(date) : date;
  return dateObj.getTime() < Date.now();
}

/**
 * Vérifie si une date est dans le futur.
 *
 * @param date - Date à vérifier
 * @returns True si la date est dans le futur
 */
export function isFuture(date: string | Date | number): boolean {
  const dateObj = typeof date === 'string' ? new Date(date) : 
                  typeof date === 'number' ? new Date(date) : date;
  return dateObj.getTime() > Date.now();
}

// ============================================================================
// OBJECT/ARRAY UTILITIES
// ============================================================================

/**
 * Clone profondément un objet ou un tableau.
 *
 * @param obj - Objet à cloner
 * @returns Clone profond
 *
 * @example
 * ```ts
 * const original = { a: 1, b: { c: 2 } };
 * const clone = deepClone(original);
 * clone.b.c = 3;
 * console.log(original.b.c); // → 2 (non modifié)
 * ```
 */
export function deepClone<T>(obj: T): T {
  if (obj === null || typeof obj !== 'object') {
    return obj;
  }

  if (obj instanceof Date) {
    return new Date(obj.getTime()) as unknown as T;
  }

  if (obj instanceof Array) {
    return obj.map((item) => deepClone(item)) as unknown as T;
  }

  if (obj instanceof Object) {
    const copy: Record<string, unknown> = {};
    Object.keys(obj).forEach((key) => {
      copy[key] = deepClone((obj as Record<string, unknown>)[key]);
    });
    return copy as T;
  }

  return obj;
}

/**
 * Fusionne profondément deux objets.
 *
 * @param target - Objet cible
 * @param source - Objet source
 * @returns Objet fusionné
 *
 * @example
 * ```ts
 * deepMerge({ a: 1, b: { c: 2 } }, { b: { d: 3 } })
 * // → { a: 1, b: { c: 2, d: 3 } }
 * ```
 */
export function deepMerge<T extends Record<string, unknown>>(
  target: T,
  source: Partial<T>
): T {
  const result = { ...target };

  for (const key in source) {
    if (Object.prototype.hasOwnProperty.call(source, key)) {
      const targetValue = target[key];
      const sourceValue = source[key];

      if (
        targetValue &&
        sourceValue &&
        typeof targetValue === 'object' &&
        typeof sourceValue === 'object' &&
        !Array.isArray(targetValue) &&
        !Array.isArray(sourceValue)
      ) {
        result[key] = deepMerge(
          targetValue as Record<string, unknown>,
          sourceValue as Record<string, unknown>
        ) as T[Extract<keyof T, string>];
      } else if (sourceValue !== undefined) {
        result[key] = sourceValue as T[Extract<keyof T, string>];
      }
    }
  }

  return result;
}

/**
 * Groupe les éléments d'un tableau par une clé.
 *
 * @param array - Tableau à grouper
 * @param keyFn - Fonction qui retourne la clé de groupe
 * @returns Objet avec les groupes
 *
 * @example
 * ```ts
 * const items = [
 *   { type: 'fruit', name: 'apple' },
 *   { type: 'vegetable', name: 'carrot' },
 *   { type: 'fruit', name: 'banana' },
 * ];
 * groupBy(items, (item) => item.type)
 * // → {
 * //   fruit: [{ type: 'fruit', name: 'apple' }, { type: 'fruit', name: 'banana' }],
 * //   vegetable: [{ type: 'vegetable', name: 'carrot' }],
 * // }
 * ```
 */
export function groupBy<T, K extends string | number | symbol>(
  array: T[],
  keyFn: (item: T) => K
): Record<K, T[]> {
  return array.reduce(
    (result, item) => {
      const key = keyFn(item);
      if (!result[key]) {
        result[key] = [];
      }
      result[key].push(item);
      return result;
    },
    {} as Record<K, T[]>
  );
}

/**
 * Retourne les éléments uniques d'un tableau selon une clé.
 *
 * @param array - Tableau à filtrer
 * @param keyFn - Fonction qui retourne la clé d'unicité
 * @returns Tableau avec éléments uniques
 *
 * @example
 * ```ts
 * const items = [
 *   { id: 1, name: 'a' },
 *   { id: 2, name: 'b' },
 *   { id: 1, name: 'c' },
 * ];
 * uniqueBy(items, (item) => item.id)
 * // → [{ id: 1, name: 'a' }, { id: 2, name: 'b' }]
 * ```
 */
export function uniqueBy<T, K>(
  array: T[],
  keyFn: (item: T) => K
): T[] {
  const seen = new Set<K>();
  return array.filter((item) => {
    const key = keyFn(item);
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

/**
 * Découpe un tableau en chunks de taille donnée.
 *
 * @param array - Tableau à découper
 * @param size - Taille de chaque chunk
 * @returns Tableau de chunks
 *
 * @example
 * ```ts
 * chunk([1, 2, 3, 4, 5], 2) // → [[1, 2], [3, 4], [5]]
 * chunk(['a', 'b', 'c', 'd'], 3) // → [['a', 'b', 'c'], ['d']]
 * ```
 */
export function chunk<T>(array: T[], size: number): T[][] {
  if (size <= 0) {
    throw new Error('Chunk size must be greater than 0');
  }

  const result: T[][] = [];
  for (let i = 0; i < array.length; i += size) {
    result.push(array.slice(i, i + size));
  }
  return result;
}

/**
 * Mélange aléatoirement un tableau (Fisher-Yates shuffle).
 *
 * @param array - Tableau à mélanger
 * @returns Nouveau tableau mélangé (ne modifie pas l'original)
 *
 * @example
 * ```ts
 * shuffle([1, 2, 3, 4, 5]) // → [3, 1, 5, 2, 4] (ordre aléatoire)
 * ```
 */
export function shuffle<T>(array: T[]): T[] {
  const result = [...array];
  for (let i = result.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [result[i], result[j]] = [result[j], result[i]];
  }
  return result;
}

/**
 * Trie un tableau de manière stable (préserve l'ordre original pour les éléments égaux).
 *
 * @param array - Tableau à trier
 * @param compareFn - Fonction de comparaison
 * @returns Nouveau tableau trié
 */
export function stableSort<T>(
  array: T[],
  compareFn: (a: T, b: T) => number
): T[] {
  const indexed = array.map((item, index) => ({ item, index }));
  indexed.sort((a, b) => {
    const result = compareFn(a.item, b.item);
    return result !== 0 ? result : a.index - b.index;
  });
  return indexed.map(({ item }) => item);
}

/**
 * Pick des propriétés spécifiques d'un objet.
 *
 * @param obj - Objet source
 * @param keys - Clés à conserver
 * @returns Nouvel objet avec seulement les clés spécifiées
 *
 * @example
 * ```ts
 * pick({ a: 1, b: 2, c: 3 }, ['a', 'c']) // → { a: 1, c: 3 }
 * ```
 */
export function pick<T extends object, K extends keyof T>(
  obj: T,
  keys: K[]
): Pick<T, K> {
  const result = {} as Pick<T, K>;
  keys.forEach((key) => {
    if (key in obj) {
      result[key] = obj[key];
    }
  });
  return result;
}

/**
 * Omet des propriétés spécifiques d'un objet.
 *
 * @param obj - Objet source
 * @param keys - Clés à exclure
 * @returns Nouvel objet sans les clés spécifiées
 *
 * @example
 * ```ts
 * omit({ a: 1, b: 2, c: 3 }, ['b']) // → { a: 1, c: 3 }
 * ```
 */
export function omit<T extends object, K extends keyof T>(
  obj: T,
  keys: K[]
): Omit<T, K> {
  const result = { ...obj };
  keys.forEach((key) => {
    delete result[key];
  });
  return result;
}

/**
 * Aplatit un objet imbriqué en objet à un seul niveau avec des clés en notation pointée.
 *
 * @param obj - Objet à aplatir
 * @param prefix - Préfixe pour les clés (interne)
 * @returns Objet aplati
 *
 * @example
 * ```ts
 * flattenObject({ a: 1, b: { c: 2, d: { e: 3 } } })
 * // → { 'a': 1, 'b.c': 2, 'b.d.e': 3 }
 * ```
 */
export function flattenObject(
  obj: Record<string, unknown>,
  prefix: string = ''
): Record<string, unknown> {
  const result: Record<string, unknown> = {};

  for (const key in obj) {
    if (Object.prototype.hasOwnProperty.call(obj, key)) {
      const newKey = prefix ? `${prefix}.${key}` : key;
      const value = obj[key];

      if (value && typeof value === 'object' && !Array.isArray(value)) {
        Object.assign(result, flattenObject(value as Record<string, unknown>, newKey));
      } else {
        result[newKey] = value;
      }
    }
  }

  return result;
}

// ============================================================================
// VALIDATION UTILITIES
// ============================================================================

/**
 * Vérifie si une valeur est vide (null, undefined, chaîne vide, tableau vide, objet vide).
 *
 * @param value - Valeur à vérifier
 * @returns True si vide
 *
 * @example
 * ```ts
 * isEmpty(null) // → true
 * isEmpty('') // → true
 * isEmpty([]) // → true
 * isEmpty({}) // → true
 * isEmpty(0) // → false
 * isEmpty('hello') // → false
 * ```
 */
export function isEmpty(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === 'string') return value.trim() === '';
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === 'object') return Object.keys(value).length === 0;
  return false;
}

/**
 * Vérifie si une chaîne est un email valide.
 *
 * @param email - Email à valider
 * @returns True si valide
 */
export function isValidEmail(email: string): boolean {
  return PATTERNS.email.test(email);
}

/**
 * Vérifie si une chaîne est une URL valide.
 *
 * @param url - URL à valider
 * @returns True si valide
 */
export function isValidUrl(url: string): boolean {
  try {
    new URL(url);
    return PATTERNS.url.test(url);
  } catch {
    return false;
  }
}

/**
 * Vérifie si une chaîne est un username valide.
 *
 * @param username - Username à valider
 * @returns True si valide
 */
export function isValidUsername(username: string): boolean {
  return (
    username.length >= LIMITS.minUsernameLength &&
    username.length <= LIMITS.maxUsernameLength &&
    PATTERNS.username.test(username)
  );
}

/**
 * Vérifie si un mot de passe est suffisamment fort.
 *
 * @param password - Mot de passe à valider
 * @param options - Options de validation
 * @returns Objet avec validité et erreurs
 *
 * @example
 * ```ts
 * validatePassword('Weak')
 * // → { valid: false, errors: ['At least 8 characters', 'Must contain uppercase', ...] }
 *
 * validatePassword('StrongPass123!')
 * // → { valid: true, errors: [] }
 * ```
 */
export function validatePassword(
  password: string,
  options: {
    minLength?: number;
    requireUppercase?: boolean;
    requireLowercase?: boolean;
    requireDigit?: boolean;
    requireSpecial?: boolean;
  } = {}
): { valid: boolean; errors: string[] } {
  const {
    minLength = LIMITS.minPasswordLength,
    requireUppercase = true,
    requireLowercase = true,
    requireDigit = true,
    requireSpecial = false,
  } = options;

  const errors: string[] = [];

  if (password.length < minLength) {
    errors.push(`At least ${minLength} characters`);
  }

  if (requireUppercase && !/[A-Z]/.test(password)) {
    errors.push('At least one uppercase letter');
  }

  if (requireLowercase && !/[a-z]/.test(password)) {
    errors.push('At least one lowercase letter');
  }

  if (requireDigit && !/\d/.test(password)) {
    errors.push('At least one digit');
  }

  if (requireSpecial && !/[!@#$%^&*(),.?":{}|<>]/.test(password)) {
    errors.push('At least one special character');
  }

  return {
    valid: errors.length === 0,
    errors,
  };
}

/**
 * Vérifie si une valeur est un nombre dans une plage donnée.
 *
 * @param value - Valeur à vérifier
 * @param min - Valeur minimale (inclusive)
 * @param max - Valeur maximale (inclusive)
 * @returns True si dans la plage
 */
export function isInRange(value: number, min: number, max: number): boolean {
  return value >= min && value <= max;
}

/**
 * Type guard pour vérifier si une valeur est un objet non-null.
 *
 * @param value - Valeur à vérifier
 * @returns True si c'est un objet
 */
export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Type guard pour vérifier si une valeur est une chaîne non vide.
 *
 * @param value - Valeur à vérifier
 * @returns True si c'est une chaîne non vide
 */
export function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

// ============================================================================
// URL UTILITIES
// ============================================================================

/**
 * Construit une URL avec des paramètres de query.
 *
 * @param baseUrl - URL de base
 * @param params - Paramètres de query
 * @returns URL complète
 *
 * @example
 * ```ts
 * buildUrl('/api/mangas', { page: 1, limit: 20 })
 * // → '/api/mangas?page=1&limit=20'
 *
 * buildUrl('https://example.com', { q: 'hello world' })
 * // → 'https://example.com?q=hello+world'
 * ```
 */
export function buildUrl(
  baseUrl: string,
  params?: Record<string, string | number | boolean | undefined | null>
): string {
  if (!params || Object.keys(params).length === 0) {
    return baseUrl;
  }

  const searchParams = new URLSearchParams();

  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null) {
      searchParams.append(key, String(value));
    }
  });

  const queryString = searchParams.toString();
  return queryString ? `${baseUrl}?${queryString}` : baseUrl;
}

/**
 * Extrait les paramètres de query d'une URL.
 *
 * @param url - URL à parser
 * @returns Objet avec les paramètres
 *
 * @example
 * ```ts
 * parseUrlParams('https://example.com?foo=bar&num=42')
 * // → { foo: 'bar', num: '42' }
 * ```
 */
export function parseUrlParams(url: string): Record<string, string> {
  try {
    const urlObj = new URL(url, 'http://dummy.com');
    const params: Record<string, string> = {};
    urlObj.searchParams.forEach((value, key) => {
      params[key] = value;
    });
    return params;
  } catch {
    return {};
  }
}

/**
 * Extrait le domaine d'une URL.
 *
 * @param url - URL à parser
 * @returns Domaine sans 'www.'
 *
 * @example
 * ```ts
 * getDomain('https://www.mangadex.org/title/123')
 * // → 'mangadex.org'
 *
 * getDomain('http://example.com:8080/path')
 * // → 'example.com'
 * ```
 */
export function getDomain(url: string): string {
  try {
    const urlObj = new URL(url);
    return urlObj.hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

/**
 * Encode une chaîne pour une utilisation dans une URL.
 *
 * @param str - Chaîne à encoder
 * @returns Chaîne encodée
 */
export function encodeUrlComponent(str: string): string {
  return encodeURIComponent(str);
}

/**
 * Décode une chaîne encodée pour URL.
 *
 * @param str - Chaîne à décoder
 * @returns Chaîne décodée
 */
export function decodeUrlComponent(str: string): string {
  try {
    return decodeURIComponent(str);
  } catch {
    return str;
  }
}

// ============================================================================
// STORAGE UTILITIES
// ============================================================================

/**
 * Wrapper sécurisé pour localStorage avec sérialisation JSON.
 */
export const storage = {
  /**
   * Récupère une valeur du localStorage.
   *
   * @param key - Clé
   * @param defaultValue - Valeur par défaut si absente
   * @returns Valeur désérialisée
   */
  get<T>(key: string, defaultValue: T | null = null): T | null {
    if (typeof window === 'undefined') return defaultValue;
    
    try {
      const item = localStorage.getItem(key);
      if (item === null) return defaultValue;
      return JSON.parse(item) as T;
    } catch (error) {
      console.error(`[Storage] Error reading key "${key}":`, error);
      return defaultValue;
    }
  },

  /**
   * Stocke une valeur dans le localStorage.
   *
   * @param key - Clé
   * @param value - Valeur à stocker
   */
  set<T>(key: string, value: T): void {
    if (typeof window === 'undefined') return;
    
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
      console.error(`[Storage] Error writing key "${key}":`, error);
    }
  },

  /**
   * Supprime une valeur du localStorage.
   *
   * @param key - Clé
   */
  remove(key: string): void {
    if (typeof window === 'undefined') return;
    
    try {
      localStorage.removeItem(key);
    } catch (error) {
      console.error(`[Storage] Error removing key "${key}":`, error);
    }
  },

  /**
   * Vérifie si une clé existe dans le localStorage.
   *
   * @param key - Clé
   * @returns True si existe
   */
  has(key: string): boolean {
    if (typeof window === 'undefined') return false;
    return localStorage.getItem(key) !== null;
  },

  /**
   * Vide tout le localStorage.
   */
  clear(): void {
    if (typeof window === 'undefined') return;
    
    try {
      localStorage.clear();
    } catch (error) {
      console.error('[Storage] Error clearing storage:', error);
    }
  },
};

/**
 * Wrapper sécurisé pour sessionStorage.
 */
export const session = {
  get<T>(key: string, defaultValue: T | null = null): T | null {
    if (typeof window === 'undefined') return defaultValue;
    
    try {
      const item = sessionStorage.getItem(key);
      if (item === null) return defaultValue;
      return JSON.parse(item) as T;
    } catch {
      return defaultValue;
    }
  },

  set<T>(key: string, value: T): void {
    if (typeof window === 'undefined') return;
    
    try {
      sessionStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
      console.error(`[Session] Error writing key "${key}":`, error);
    }
  },

  remove(key: string): void {
    if (typeof window === 'undefined') return;
    sessionStorage.removeItem(key);
  },

  clear(): void {
    if (typeof window === 'undefined') return;
    sessionStorage.clear();
  },
};

// ============================================================================
// ASYNC UTILITIES
// ============================================================================

/**
 * Crée une promesse qui se résout après un délai.
 *
 * @param ms - Délai en millisecondes
 * @returns Promise
 *
 * @example
 * ```ts
 * await sleep(1000); // Attend 1 seconde
 * ```
 */
export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Crée une fonction debounce qui retarde l'exécution.
 *
 * @param fn - Fonction à débouncer
 * @param delay - Délai en millisecondes
 * @returns Fonction debouncée
 *
 * @example
 * ```ts
 * const debouncedSearch = debounce((query: string) => {
 *   console.log('Searching:', query);
 * }, 300);
 *
 * debouncedSearch('a');
 * debouncedSearch('ab');
 * debouncedSearch('abc'); // Seule cette exécution aura lieu après 300ms
 * ```
 */
export function debounce<T extends (...args: unknown[]) => unknown>(
  fn: T,
  delay: number
): (...args: Parameters<T>) => void {
  let timeoutId: ReturnType<typeof setTimeout> | null = null;

  return function (this: unknown, ...args: Parameters<T>) {
    if (timeoutId !== null) {
      clearTimeout(timeoutId);
    }

    timeoutId = setTimeout(() => {
      fn.apply(this, args);
      timeoutId = null;
    }, delay);
  };
}

/**
 * Crée une fonction throttle qui limite la fréquence d'exécution.
 *
 * @param fn - Fonction à throttler
 * @param limit - Intervalle minimum en millisecondes
 * @returns Fonction throttled
 *
 * @example
 * ```ts
 * const throttledScroll = throttle(() => {
 *   console.log('Scroll event');
 * }, 100);
 *
 * window.addEventListener('scroll', throttledScroll);
 * // Ne s'exécutera pas plus d'une fois toutes les 100ms
 * ```
 */
export function throttle<T extends (...args: unknown[]) => unknown>(
  fn: T,
  limit: number
): (...args: Parameters<T>) => void {
  let inThrottle = false;
  let lastArgs: Parameters<T> | null = null;
  let lastThis: unknown = null;

  return function (this: unknown, ...args: Parameters<T>) {
    if (!inThrottle) {
      fn.apply(this, args);
      inThrottle = true;
      setTimeout(() => {
        inThrottle = false;
        if (lastArgs) {
          fn.apply(lastThis, lastArgs);
          lastArgs = null;
          lastThis = null;
        }
      }, limit);
    } else {
      lastArgs = args;
      lastThis = this;
    }
  };
}

/**
 * Exécute une fonction avec un timeout.
 *
 * @param promise - Promise à exécuter
 * @param timeoutMs - Timeout en millisecondes
 * @param errorMessage - Message d'erreur personnalisé
 * @returns Résultat de la promise
 * @throws Error si timeout dépassé
 *
 * @example
 * ```ts
 * try {
 *   const data = await withTimeout(fetchData(), 5000);
 * } catch (error) {
 *   console.error('Operation timed out');
 * }
 * ```
 */
export async function withTimeout<T>(
  promise: Promise<T>,
  timeoutMs: number,
  errorMessage: string = 'Operation timed out'
): Promise<T> {
  return new Promise((resolve, reject) => {
    const timeoutId = setTimeout(() => {
      reject(new Error(errorMessage));
    }, timeoutMs);

    promise
      .then((result) => {
        clearTimeout(timeoutId);
        resolve(result);
      })
      .catch((error) => {
        clearTimeout(timeoutId);
        reject(error);
      });
  });
}

/**
 * Retry une fonction asynchrone avec exponential backoff.
 *
 * @param fn - Fonction à retry
 * @param options - Options de retry
 * @returns Résultat de la fonction
 * @throws Dernière erreur si tous les retries échouent
 *
 * @example
 * ```ts
 * const data = await retry(
 *   () => fetchUnstableApi(),
 *   { maxAttempts: 3, initialDelay: 1000 }
 * );
 * ```
 */
export async function retry<T>(
  fn: () => Promise<T>,
  options: {
    maxAttempts?: number;
    initialDelay?: number;
    maxDelay?: number;
    shouldRetry?: (error: unknown) => boolean;
  } = {}
): Promise<T> {
  const {
    maxAttempts = 3,
    initialDelay = 1000,
    maxDelay = 30000,
    shouldRetry = () => true,
  } = options;

  let lastError: unknown;

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (error) {
      lastError = error;

      if (attempt === maxAttempts - 1 || !shouldRetry(error)) {
        throw error;
      }

      const delay = Math.min(initialDelay * Math.pow(2, attempt), maxDelay);
      await sleep(delay);
    }
  }

  throw lastError;
}

/**
 * Exécute des promesses en parallèle avec une limite de concurrence.
 *
 * @param items - Éléments à traiter
 * @param fn - Fonction à appliquer à chaque élément
 * @param concurrency - Nombre max de tâches en parallèle
 * @returns Résultats dans l'ordre original
 *
 * @example
 * ```ts
 * const results = await mapConcurrent(
 *   [1, 2, 3, 4, 5],
 *   async (n) => await fetchItem(n),
 *   2 // Max 2 requêtes en parallèle
 * );
 * ```
 */
export async function mapConcurrent<T, R>(
  items: T[],
  fn: (item: T, index: number) => Promise<R>,
  concurrency: number = 5
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let currentIndex = 0;

  const workers = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (currentIndex < items.length) {
      const index = currentIndex++;
      results[index] = await fn(items[index], index);
    }
  });

  await Promise.all(workers);
  return results;
}

// ============================================================================
// COLOR UTILITIES
// ============================================================================

/**
 * Convertit une couleur hex en RGB.
 *
 * @param hex - Couleur hex (avec ou sans #)
 * @returns Objet { r, g, b }
 *
 * @example
 * ```ts
 * hexToRgb('#00ff41') // → { r: 0, g: 255, b: 65 }
 * hexToRgb('0ff') // → { r: 0, g: 255, b: 255 }
 * ```
 */
export function hexToRgb(hex: string): { r: number; g: number; b: number } {
  let cleanHex = hex.replace('#', '');

  if (cleanHex.length === 3) {
    cleanHex = cleanHex
      .split('')
      .map((c) => c + c)
      .join('');
  }

  const r = parseInt(cleanHex.slice(0, 2), 16);
  const g = parseInt(cleanHex.slice(2, 4), 16);
  const b = parseInt(cleanHex.slice(4, 6), 16);

  return { r, g, b };
}

/**
 * Convertit RGB en hex.
 *
 * @param r - Rouge (0-255)
 * @param g - Vert (0-255)
 * @param b - Bleu (0-255)
 * @returns Couleur hex avec #
 *
 * @example
 * ```ts
 * rgbToHex(0, 255, 65) // → '#00ff41'
 * ```
 */
export function rgbToHex(r: number, g: number, b: number): string {
  const toHex = (n: number) => {
    const hex = Math.max(0, Math.min(255, Math.round(n))).toString(16);
    return hex.length === 1 ? '0' + hex : hex;
  };
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

/**
 * Ajuste la luminosité d'une couleur hex.
 *
 * @param hex - Couleur hex
 * @param percent - Pourcentage d'ajustement (-100 à 100)
 * @returns Couleur hex ajustée
 *
 * @example
 * ```ts
 * adjustBrightness('#00ff41', 20) // → Plus clair
 * adjustBrightness('#00ff41', -20) // → Plus sombre
 * ```
 */
export function adjustBrightness(hex: string, percent: number): string {
  const { r, g, b } = hexToRgb(hex);
  const factor = percent / 100;

  const adjust = (value: number) => {
    if (factor > 0) {
      return value + (255 - value) * factor;
    } else {
      return value * (1 + factor);
    }
  };

  return rgbToHex(adjust(r), adjust(g), adjust(b));
}

/**
 * Convertit une couleur hex en rgba avec opacité.
 *
 * @param hex - Couleur hex
 * @param alpha - Opacité (0 à 1)
 * @returns Couleur rgba
 *
 * @example
 * ```ts
 * hexToRgba('#00ff41', 0.5) // → 'rgba(0, 255, 65, 0.5)'
 * ```
 */
export function hexToRgba(hex: string, alpha: number): string {
  const { r, g, b } = hexToRgb(hex);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// ============================================================================
// DOMAIN-SPECIFIC UTILITIES (Manga)
// ============================================================================

/**
 * Formate un numéro de chapitre pour affichage.
 *
 * @param number - Numéro de chapitre (peut être décimal)
 * @returns Chaîne formatée
 *
 * @example
 * ```ts
 * formatChapterNumber(1) // → 'Chapter 1'
 * formatChapterNumber(123.5) // → 'Chapter 123.5'
 * formatChapterNumber(0) // → 'Chapter 0'
 * ```
 */
export function formatChapterNumber(number: number): string {
  if (Number.isInteger(number)) {
    return `Chapter ${number}`;
  }
  return `Chapter ${number}`;
}

/**
 * Calcule le pourcentage de progression de lecture.
 *
 * @param current - Valeur actuelle (chapitres lus, pages lues, etc.)
 * @param total - Valeur totale
 * @returns Pourcentage entre 0 et 100
 *
 * @example
 * ```ts
 * calculateProgress(50, 100) // → 50
 * calculateProgress(0, 100) // → 0
 * calculateProgress(100, 0) // → 0 (évite division par zéro)
 * ```
 */
export function calculateProgress(current: number, total: number): number {
  if (total === 0) return 0;
  return Math.min(100, Math.round((current / total) * 100));
}

/**
 * Extrait le nom d'un site depuis son URL.
 *
 * @param url - URL du site
 * @returns Nom du site (domaine sans TLD, title-cased)
 *
 * @example
 * ```ts
 * extractSiteName('https://mangadex.org') // → 'Mangadex'
 * extractSiteName('https://www.asurascans.com') // → 'Asurascans'
 * ```
 */
export function extractSiteName(url: string): string {
  const domain = getDomain(url);
  const name = domain.split('.')[0];
  return titleCase(name);
}

/**
 * Détermine l'icône appropriée pour un statut de lecture.
 *
 * @param status - Statut de lecture
 * @returns Emoji correspondant
 */
export function getReadingStatusIcon(
  status: 'reading' | 'completed' | 'plan_to_read' | 'on_hold' | 'dropped'
): string {
  const icons = {
    reading: '📖',
    completed: '✅',
    plan_to_read: '📋',
    on_hold: '⏸️',
    dropped: '❌',
  };
  return icons[status];
}

/**
 * Détermine la couleur appropriée pour un statut de téléchargement.
 *
 * @param status - Statut de téléchargement
 * @returns Couleur hex
 */
export function getDownloadStatusColor(
  status: 'pending' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled'
): string {
  const colors = {
    pending: COLORS.warning,
    running: COLORS.primary,
    paused: COLORS.secondary,
    completed: COLORS.success,
    failed: COLORS.error,
    cancelled: COLORS.textDim,
  };
  return colors[status];
}

// ============================================================================
// BROWSER UTILITIES
// ============================================================================

/**
 * Vérifie si le code s'exécute dans un navigateur.
 *
 * @returns True si dans un navigateur
 */
export function isBrowser(): boolean {
  return typeof window !== 'undefined' && typeof document !== 'undefined';
}

/**
 * Vérifie si le code s'exécute côté serveur (SSR).
 *
 * @returns True si côté serveur
 */
export function isServer(): boolean {
  return !isBrowser();
}

/**
 * Copie du texte dans le presse-papier.
 *
 * @param text - Texte à copier
 * @returns Promise résolue quand la copie est terminée
 *
 * @example
 * ```ts
 * await copyToClipboard('Hello World');
 * ```
 */
export async function copyToClipboard(text: string): Promise<void> {
  if (!isBrowser()) {
    throw new Error('copyToClipboard is only available in browser');
  }

  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
  } else {
    // Fallback pour les navigateurs plus anciens
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.left = '-999999px';
    textArea.style.top = '-999999px';
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    
    try {
      document.execCommand('copy');
    } finally {
      document.body.removeChild(textArea);
    }
  }
}

/**
 * Télécharge un fichier depuis le navigateur.
 *
 * @param data - Données du fichier (Blob, string, etc.)
 * @param filename - Nom du fichier
 * @param mimeType - Type MIME (défaut: 'application/octet-stream')
 *
 * @example
 * ```ts
 * downloadFile('Hello World', 'hello.txt', 'text/plain');
 * downloadFile(jsonData, 'data.json', 'application/json');
 * ```
 */
export function downloadFile(
  data: Blob | string,
  filename: string,
  mimeType: string = 'application/octet-stream'
): void {
  if (!isBrowser()) {
    throw new Error('downloadFile is only available in browser');
  }

  const blob = typeof data === 'string' ? new Blob([data], { type: mimeType }) : data;
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  
  link.href = url;
  link.download = filename;
  link.style.display = 'none';
  
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  
  setTimeout(() => URL.revokeObjectURL(url), 100);
}

/**
 * Détecte le système d'exploitation de l'utilisateur.
 *
 * @returns Nom de l'OS ou 'unknown'
 */
export function detectOS(): 'windows' | 'macos' | 'linux' | 'ios' | 'android' | 'unknown' {
  if (!isBrowser()) return 'unknown';

  const userAgent = navigator.userAgent.toLowerCase();

  if (/iphone|ipad|ipod/.test(userAgent)) return 'ios';
  if (/android/.test(userAgent)) return 'android';
  if (/win/.test(userAgent)) return 'windows';
  if (/mac/.test(userAgent)) return 'macos';
  if (/linux/.test(userAgent)) return 'linux';

  return 'unknown';
}

/**
 * Détecte le navigateur de l'utilisateur.
 *
 * @returns Nom du navigateur ou 'unknown'
 */
export function detectBrowser(): 'chrome' | 'firefox' | 'safari' | 'edge' | 'opera' | 'unknown' {
  if (!isBrowser()) return 'unknown';

  const userAgent = navigator.userAgent.toLowerCase();

  if (/edg/.test(userAgent)) return 'edge';
  if (/opr\//.test(userAgent)) return 'opera';
  if (/chrome/.test(userAgent)) return 'chrome';
  if (/firefox/.test(userAgent)) return 'firefox';
  if (/safari/.test(userAgent)) return 'safari';

  return 'unknown';
}

// ============================================================================
// DEBUG UTILITIES
// ============================================================================

/**
 * Log une valeur dans la console avec un label stylé.
 *
 * @param label - Label à afficher
 * @param value - Valeur à logger
 * @param color - Couleur du label (défaut: vert néon)
 */
export function debugLog(label: string, value: unknown, color: string = COLORS.primary): void {
  if (process.env.NODE_ENV !== 'development') return;

  console.log(
    `%c[${label}]`,
    `color: ${color}; font-weight: bold;`,
    value
  );
}

/**
 * Mesure le temps d'exécution d'une fonction.
 *
 * @param label - Label pour le timer
 * @param fn - Fonction à mesurer
 * @returns Résultat de la fonction
 *
 * @example
 * ```ts
 * const result = await measureTime('fetchData', () => fetchData());
 * // Console: [fetchData] took 123ms
 * ```
 */
export async function measureTime<T>(label: string, fn: () => Promise<T>): Promise<T> {
  if (process.env.NODE_ENV !== 'development') {
    return fn();
  }

  const start = performance.now();
  try {
    const result = await fn();
    const duration = performance.now() - start;
    console.log(`%c[${label}]`, `color: ${COLORS.secondary}; font-weight: bold;`, `took ${duration.toFixed(2)}ms`);
    return result;
  } catch (error) {
    const duration = performance.now() - start;
    console.error(`[${label}] failed after ${duration.toFixed(2)}ms:`, error);
    throw error;
  }
}

// ============================================================================
// EXPORTS
// ============================================================================

export type { ClassValue };
