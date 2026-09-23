"""Utilitaires pour la manipulation de texte dans NexusDL.

Ce module fournit un ensemble complet de fonctions pour manipuler, nettoyer,
normaliser et analyser le texte. Il est utilisé dans tout le projet pour :

    - Nettoyage et normalisation de texte (Unicode, espaces, caractères de contrôle)
    - Slugification et sanitization de noms de fichiers
    - Extraction d'informations (nombres, IDs, chapitres, volumes)
    - Formatage de tailles, nombres, pourcentages
    - Comparaison floue de chaînes (similarité, matching)
    - Conversion d'encodage et normalisation de cas
    - Détection de langue et de scripts

**Architecture** :
    - Fonctions pures (pas d'état global)
    - Utilisation de `unicodedata` de la stdlib
    - Regex compilées pour performance
    - Thread-safe et async-compatible
    - Support Unicode complet

**Exemples d'utilisation** :
    >>> from nexusdl.core.utils.text import (
    ...     slugify, sanitize_filename, extract_numbers, format_size,
    ...     normalize_whitespace, truncate, similarity,
    ... )
    >>>
    >>> # Slugification
    >>> slugify("One Piece - Chapter 123: Adventure!")
    'one-piece-chapter-123-adventure'
    >>>
    >>> # Sanitization de nom de fichier
    >>> sanitize_filename("Manga: One Piece <Special>.cbz")
    'Manga_ One Piece _Special_.cbz'
    >>>
    >>> # Extraction de nombres
    >>> extract_numbers("Chapter 123.5 of 500")
    [123.5, 500.0]
    >>>
    >>> # Formatage de taille
    >>> format_size(1234567890)
    '1.2 GB'
    >>>
    >>> # Normalisation d'espaces
    >>> normalize_whitespace("  Hello   World  ")
    'Hello World'
    >>>
    >>> # Similarité de chaînes
    >>> similarity("One Piece", "One Piece")
    1.0
    >>> similarity("One Piece", "One Peace")
    0.88

Intégration :
    - core/parsers/* : utilise ces utilitaires pour nettoyer les titres
    - core/downloader/* : utilise ces utilitaires pour les noms de fichiers
    - core/library/* : utilise ces utilitaires pour la recherche floue
    - interfaces/* : utilise ces utilitaires pour l'affichage
"""

from __future__ import annotations

import re
import unicodedata
from enum import Enum
from typing import Any, Final, Iterable

from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# CONSTANTES
# ============================================================================


# Caractères de contrôle Unicode (C0 et C1)
CONTROL_CHARS: Final[frozenset[str]] = frozenset(
    chr(i) for i in range(32) if i not in (9, 10, 13)  # Tab, LF, CR autorisés
) | frozenset(chr(i) for i in range(127, 160))

# Caractères interdits dans les noms de fichiers (multi-plateforme)
INVALID_FILENAME_CHARS: Final[frozenset[str]] = frozenset({
    "<", ">", ":", '"', "/", "\\", "|", "?", "*",
    "\x00", "\x01", "\x02", "\x03", "\x04", "\x05", "\x06", "\x07",
    "\x08", "\x0b", "\x0c", "\x0e", "\x0f",
    "\x10", "\x11", "\x12", "\x13", "\x14", "\x15", "\x16", "\x17",
    "\x18", "\x19", "\x1a", "\x1b", "\x1c", "\x1d", "\x1e", "\x1f",
})

# Noms de fichiers réservés (Windows)
RESERVED_FILENAMES: Final[frozenset[str]] = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
})

# Caractères de ponctuation
PUNCTUATION: Final[frozenset[str]] = frozenset({
    "!", "\"", "#", "$", "%", "&", "'", "(", ")", "*", "+", ",", "-", ".",
    "/", ":", ";", "<", "=", ">", "?", "@", "[", "\\", "]", "^", "_", "`",
    "{", "|", "}", "~",
})

# Caractères d'espacement
WHITESPACE_CHARS: Final[frozenset[str]] = frozenset({
    " ", "\t", "\n", "\r", "\f", "\v",
    "\u00a0",  # No-break space
    "\u1680",  # Ogham space
    "\u2000", "\u2001", "\u2002", "\u2003", "\u2004", "\u2005", "\u2006",
    "\u2007", "\u2008", "\u2009", "\u200a",  # Various spaces
    "\u2028",  # Line separator
    "\u2029",  # Paragraph separator
    "\u202f",  # Narrow no-break space
    "\u205f",  # Medium mathematical space
    "\u3000",  # Ideographic space
})

# Patterns regex compilés
MULTIPLE_SPACES_PATTERN: Final[re.Pattern[str]] = re.compile(r"\s+")
LEADING_TRAILING_SPACES_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\s+|\s+$")
NON_ALPHANUMERIC_PATTERN: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")
MULTIPLE_DASHES_PATTERN: Final[re.Pattern[str]] = re.compile(r"-+")
NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(r"\d+(?:\.\d+)?")
CHAPTER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:ch(?:apter)?|chapitre|chap\.?)\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
VOLUME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:vol(?:ume)?|tome|t\.)\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

# Unités de taille
SIZE_UNITS: Final[list[tuple[str, int]]] = [
    ("B", 1),
    ("KB", 1024),
    ("MB", 1024 ** 2),
    ("GB", 1024 ** 3),
    ("TB", 1024 ** 4),
    ("PB", 1024 ** 5),
]

# Mapping d'ASCII folding (accents → ASCII)
ASCII_FOLDING_MAP: Final[dict[str, str]] = {
    "à": "a", "á": "a", "â": "a", "ã": "a", "ä": "a", "å": "a",
    "è": "e", "é": "e", "ê": "e", "ë": "e",
    "ì": "i", "í": "i", "î": "i", "ï": "i",
    "ò": "o", "ó": "o", "ô": "o", "õ": "o", "ö": "o", "ø": "o",
    "ù": "u", "ú": "u", "û": "u", "ü": "u",
    "ý": "y", "ÿ": "y",
    "ñ": "n", "ç": "c", "ð": "d", "þ": "th",
    "æ": "ae", "œ": "oe",
    "ß": "ss",
}


# ============================================================================
# EXCEPTIONS
# ============================================================================


class TextError(NexusDLError):
    """Exception de base pour les erreurs liées au texte."""


class InvalidTextError(TextError):
    """Exception levée lorsqu'un texte est invalide.

    Attributes:
        value: Valeur invalide.
        reason: Raison de l'invalidité.
    """

    def __init__(self, value: Any, reason: str = "") -> None:
        msg = f"Texte invalide: {value!r}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.value = value
        self.reason = reason


class EncodingError(TextError):
    """Exception levée lorsqu'il y a une erreur d'encodage."""

    def __init__(self, encoding: str, reason: str = "") -> None:
        msg = f"Erreur d'encodage: {encoding!r}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.encoding = encoding
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class UnicodeNormalization(str, Enum):
    """Formes de normalisation Unicode.

    Attributes:
        NFC: Normalisation C (composition).
        NFD: Normalisation D (décomposition).
        NFKC: Normalisation KC (compatibilité + composition).
        NFKD: Normalisation KD (compatibilité + décomposition).
    """

    NFC = "NFC"
    NFD = "NFD"
    NFKC = "NFKC"
    NFKD = "NFKD"


class TextCase(str, Enum):
    """Casses de texte.

    Attributes:
        LOWER: Minuscules.
        UPPER: Majuscules.
        TITLE: Titre (première lettre de chaque mot en majuscule).
        SENTENCE: Phrase (première lettre en majuscule).
    """

    LOWER = "lower"
    UPPER = "upper"
    TITLE = "title"
    SENTENCE = "sentence"


class TruncationPosition(str, Enum):
    """Position de la troncature.

    Attributes:
        END: Tronquer à la fin.
        MIDDLE: Tronquer au milieu.
        START: Tronquer au début.
    """

    END = "end"
    MIDDLE = "middle"
    START = "start"


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class TextStats(BaseModel):
    """Statistiques d'un texte.

    Attributes:
        length: Nombre de caractères.
        word_count: Nombre de mots.
        line_count: Nombre de lignes.
        char_count: Nombre de caractères (sans espaces).
        unique_words: Nombre de mots uniques.
        avg_word_length: Longueur moyenne des mots.
    """

    length: int = Field(..., ge=0, description="Nombre de caractères.")
    word_count: int = Field(..., ge=0, description="Nombre de mots.")
    line_count: int = Field(..., ge=0, description="Nombre de lignes.")
    char_count: int = Field(..., ge=0, description="Nombre de caractères (sans espaces).")
    unique_words: int = Field(..., ge=0, description="Nombre de mots uniques.")
    avg_word_length: float = Field(..., ge=0.0, description="Longueur moyenne des mots.")

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# NETTOYAGE ET NORMALISATION
# ============================================================================


def normalize_unicode(
    text: str,
    *,
    form: UnicodeNormalization = UnicodeNormalization.NFC,
) -> str:
    """Normalise un texte Unicode.

    Args:
        text: Texte à normaliser.
        form: Forme de normalisation (NFC, NFD, NFKC, NFKD).

    Returns:
        Texte normalisé.

    Example:
        >>> normalize_unicode("café", form=UnicodeNormalization.NFD)
        'café'
    """
    if not text:
        return text
    return unicodedata.normalize(form.value, text)


def normalize_whitespace(text: str, *, collapse: bool = True) -> str:
    """Normalise les espaces dans un texte.

    Remplace tous les caractères d'espacement par des espaces simples
    et optionnellement collapse les espaces multiples.

    Args:
        text: Texte à normaliser.
        collapse: Si True, remplace les espaces multiples par un seul.

    Returns:
        Texte normalisé.

    Example:
        >>> normalize_whitespace("  Hello   World  ")
        'Hello World'
        >>> normalize_whitespace("Hello\\t\\nWorld", collapse=False)
        'Hello World'
    """
    if not text:
        return text

    # Remplacer tous les espaces par des espaces simples
    for char in WHITESPACE_CHARS:
        if char != " ":
            text = text.replace(char, " ")

    if collapse:
        text = MULTIPLE_SPACES_PATTERN.sub(" ", text)

    return text.strip()


def remove_control_chars(text: str) -> str:
    """Supprime les caractères de contrôle d'un texte.

    Args:
        text: Texte à nettoyer.

    Returns:
        Texte sans caractères de contrôle.

    Example:
        >>> remove_control_chars("Hello\\x00World")
        'HelloWorld'
    """
    if not text:
        return text
    return "".join(char for char in text if char not in CONTROL_CHARS)


def remove_accents(text: str) -> str:
    """Supprime les accents d'un texte (décomposition Unicode).

    Args:
        text: Texte à traiter.

    Returns:
        Texte sans accents.

    Example:
        >>> remove_accents("café naïve résumé")
        'cafe naive resume'
    """
    if not text:
        return text

    # Décomposer en NFD (Normalisation Form D)
    text = unicodedata.normalize("NFD", text)

    # Supprimer les caractères de catégorie "Mark" (accents)
    text = "".join(
        char for char in text
        if unicodedata.category(char) != "Mn"
    )

    # Recomposer en NFC
    return unicodedata.normalize("NFC", text)


def ascii_fold(text: str) -> str:
    """Convertit un texte en ASCII en remplaçant les caractères non-ASCII.

    Args:
        text: Texte à convertir.

    Returns:
        Texte en ASCII.

    Example:
        >>> ascii_fold("café naïve")
        'cafe naive'
        >>> ascii_fold("日本語")
        'riBenYu'
    """
    if not text:
        return text

    # D'abord supprimer les accents
    text = remove_accents(text)

    # Ensuite appliquer le mapping manuel pour les caractères spéciaux
    result: list[str] = []
    for char in text:
        if char in ASCII_FOLDING_MAP:
            result.append(ASCII_FOLDING_MAP[char])
        elif ord(char) < 128:
            result.append(char)
        else:
            # Caractère non-ASCII non mappé : essayer de le translittérer
            try:
                transliterated = unicodedata.name(char, "").lower()
                if transliterated:
                    result.append(transliterated.replace(" ", "_"))
            except Exception:
                pass  # Ignorer les caractères non translittérables

    return "".join(result)


def strip_html(text: str) -> str:
    """Supprime les balises HTML d'un texte.

    Args:
        text: Texte avec HTML.

    Returns:
        Texte sans balises HTML.

    Example:
        >>> strip_html("<p>Hello <b>World</b></p>")
        'Hello World'
    """
    if not text:
        return text

    # Pattern simple pour les balises HTML
    html_pattern = re.compile(r"<[^>]+>")
    text = html_pattern.sub("", text)

    # Décoder les entités HTML courantes
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    text = text.replace("&nbsp;", " ")

    return normalize_whitespace(text)


# ============================================================================
# SLUGIFICATION ET SANITIZATION
# ============================================================================


def slugify(
    text: str,
    *,
    max_length: int = 100,
    separator: str = "-",
    lowercase: bool = True,
    allow_unicode: bool = False,
) -> str:
    """Convertit un texte en slug URL-safe.

    Args:
        text: Texte à convertir.
        max_length: Longueur maximale du slug.
        separator: Séparateur de mots (défaut: "-").
        lowercase: Si True, convertit en minuscules.
        allow_unicode: Si True, autorise les caractères Unicode.

    Returns:
        Slug URL-safe.

    Example:
        >>> slugify("One Piece - Chapter 123: Adventure!")
        'one-piece-chapter-123-adventure'
        >>> slugify("Café Résumé", allow_unicode=True)
        'cafe-resume'
    """
    if not text:
        return ""

    # Normaliser Unicode
    text = normalize_unicode(text, form=UnicodeNormalization.NFKD)

    if not allow_unicode:
        # Convertir en ASCII
        text = ascii_fold(text)

    # Minuscules
    if lowercase:
        text = text.lower()

    # Remplacer les caractères non-alphanumériques par le séparateur
    if allow_unicode:
        text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
        text = re.sub(r"[-\s]+", separator, text, flags=re.UNICODE)
    else:
        text = NON_ALPHANUMERIC_PATTERN.sub(separator, text)

    # Supprimer les séparateurs multiples
    if separator != "-":
        text = text.replace(separator * 2, separator)
    else:
        text = MULTIPLE_DASHES_PATTERN.sub("-", text)

    # Supprimer les séparateurs en début/fin
    text = text.strip(separator)

    # Limiter la longueur
    if len(text) > max_length:
        text = text[:max_length].rstrip(separator)

    return text


def sanitize_filename(
    name: str,
    *,
    replacement: str = "_",
    max_length: int = 255,
    preserve_extension: bool = True,
) -> str:
    """Sanitize un nom de fichier en remplaçant les caractères interdits.

    Args:
        name: Nom à sanitiser.
        replacement: Caractère de remplacement.
        max_length: Longueur maximale.
        preserve_extension: Si True, préserve l'extension du fichier.

    Returns:
        Nom sanitizé.

    Example:
        >>> sanitize_filename('file:name<test>.txt')
        'file_name_test_.txt'
        >>> sanitize_filename('CON.txt')
        '_CON.txt'
    """
    if not name:
        return name

    # Séparer l'extension si nécessaire
    extension = ""
    if preserve_extension and "." in name:
        base_name, extension = name.rsplit(".", 1)
        extension = f".{extension}"
    else:
        base_name = name

    # Normaliser Unicode
    base_name = normalize_unicode(base_name)

    # Remplacer les caractères interdits
    result: list[str] = []
    for char in base_name:
        if char in INVALID_FILENAME_CHARS:
            result.append(replacement)
        else:
            result.append(char)

    base_name = "".join(result)

    # Supprimer les espaces en début/fin
    base_name = base_name.strip()

    # Éviter les noms réservés (Windows)
    if base_name.upper().split(".")[0] in RESERVED_FILENAMES:
        base_name = "_" + base_name

    # Éviter les noms vides
    if not base_name or base_name in (".", ".."):
        base_name = "_unnamed_"

    # Limiter la longueur
    full_name = base_name + extension
    if len(full_name) > max_length:
        max_base_length = max_length - len(extension)
        base_name = base_name[:max_base_length]

    return base_name + extension


def sanitize_for_display(text: str, *, max_length: int = 100) -> str:
    """Sanitize un texte pour l'affichage (supprime les caractères problématiques).

    Args:
        text: Texte à sanitiser.
        max_length: Longueur maximale.

    Returns:
        Texte sanitizé.
    """
    if not text:
        return text

    # Supprimer les caractères de contrôle
    text = remove_control_chars(text)

    # Normaliser les espaces
    text = normalize_whitespace(text)

    # Limiter la longueur
    if len(text) > max_length:
        text = text[:max_length]

    return text


# ============================================================================
# EXTRACTION
# ============================================================================


def extract_numbers(text: str) -> list[float]:
    """Extrait tous les nombres d'un texte.

    Args:
        text: Texte source.

    Returns:
        Liste des nombres extraits (float).

    Example:
        >>> extract_numbers("Chapter 123.5 of 500")
        [123.5, 500.0]
        >>> extract_numbers("No numbers here")
        []
    """
    if not text:
        return []

    matches = NUMBER_PATTERN.findall(text)
    return [float(m) for m in matches]


def extract_integers(text: str) -> list[int]:
    """Extrait tous les entiers d'un texte.

    Args:
        text: Texte source.

    Returns:
        Liste des entiers extraits.

    Example:
        >>> extract_integers("Page 10 of 100")
        [10, 100]
    """
    if not text:
        return []

    matches = re.findall(r"\d+", text)
    return [int(m) for m in matches]


def extract_chapter_number(text: str) -> float | None:
    """Extrait le numéro de chapitre d'un texte.

    Args:
        text: Texte source.

    Returns:
        Numéro de chapitre ou None.

    Example:
        >>> extract_chapter_number("One Piece - Chapter 123.5")
        123.5
        >>> extract_chapter_number("Chapitre 42")
        42.0
    """
    if not text:
        return None

    match = CHAPTER_PATTERN.search(text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    # Fallback : chercher un nombre après "ch" ou "chap"
    match = re.search(r"(?:ch|chap)[\.\s]*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    return None


def extract_volume_number(text: str) -> float | None:
    """Extrait le numéro de volume d'un texte.

    Args:
        text: Texte source.

    Returns:
        Numéro de volume ou None.

    Example:
        >>> extract_volume_number("One Piece Vol. 10")
        10.0
        >>> extract_volume_number("Tome 5")
        5.0
    """
    if not text:
        return None

    match = VOLUME_PATTERN.search(text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    return None


def extract_words(text: str, *, lowercase: bool = False) -> list[str]:
    """Extrait les mots d'un texte.

    Args:
        text: Texte source.
        lowercase: Si True, convertit en minuscules.

    Returns:
        Liste des mots.

    Example:
        >>> extract_words("Hello, World!")
        ['Hello', 'World']
    """
    if not text:
        return []

    # Extraire les séquences alphanumériques
    words = re.findall(r"\b\w+\b", text, flags=re.UNICODE)

    if lowercase:
        words = [w.lower() for w in words]

    return words


def extract_hashtags(text: str) -> list[str]:
    """Extrait les hashtags d'un texte.

    Args:
        text: Texte source.

    Returns:
        Liste des hashtags (sans le #).

    Example:
        >>> extract_hashtags("Check out #manga and #anime!")
        ['manga', 'anime']
    """
    if not text:
        return []

    matches = re.findall(r"#(\w+)", text)
    return matches


def extract_mentions(text: str) -> list[str]:
    """Extrait les mentions (@username) d'un texte.

    Args:
        text: Texte source.

    Returns:
        Liste des mentions (sans le @).

    Example:
        >>> extract_mentions("Hello @john and @jane!")
        ['john', 'jane']
    """
    if not text:
        return []

    matches = re.findall(r"@(\w+)", text)
    return matches


def extract_urls(text: str) -> list[str]:
    """Extrait les URLs d'un texte.

    Args:
        text: Texte source.

    Returns:
        Liste des URLs.

    Example:
        >>> extract_urls("Visit https://example.com or http://test.org")
        ['https://example.com', 'http://test.org']
    """
    if not text:
        return []

    url_pattern = re.compile(
        r"https?://[^\s<>\"']+|www\.[^\s<>\"']+",
        re.IGNORECASE,
    )
    return url_pattern.findall(text)


def extract_emails(text: str) -> list[str]:
    """Extrait les adresses email d'un texte.

    Args:
        text: Texte source.

    Returns:
        Liste des emails.

    Example:
        >>> extract_emails("Contact: john@example.com or jane@test.org")
        ['john@example.com', 'jane@test.org']
    """
    if not text:
        return []

    email_pattern = re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    )
    return email_pattern.findall(text)


# ============================================================================
# FORMATAGE
# ============================================================================


def format_size(
    size: int | float,
    *,
    binary: bool = True,
    precision: int = 1,
) -> str:
    """Formate une taille en bytes pour l'affichage.

    Args:
        size: Taille en bytes.
        binary: Si True, utilise 1024 (KiB, MiB), sinon 1000 (KB, MB).
        precision: Nombre de décimales.

    Returns:
        Taille formatée.

    Example:
        >>> format_size(1234567890)
        '1.2 GB'
        >>> format_size(1024, binary=False)
        '1.0 KB'
    """
    if size < 0:
        raise ValueError("Size cannot be negative")

    divisor = 1024 if binary else 1000

    for unit, threshold in SIZE_UNITS:
        if size < threshold:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size / (threshold / divisor):.{precision}f} {unit}"

    # Si on dépasse PB
    return f"{size / (1024 ** 5):.{precision}f} PB"


def format_number(
    number: int | float,
    *,
    thousands_sep: str = ",",
    decimal_sep: str = ".",
    precision: int | None = None,
) -> str:
    """Formate un nombre avec séparateurs de milliers.

    Args:
        number: Nombre à formater.
        thousands_sep: Séparateur de milliers.
        decimal_sep: Séparateur décimal.
        precision: Nombre de décimales (None = auto).

    Returns:
        Nombre formaté.

    Example:
        >>> format_number(1234567.89)
        '1,234,567.89'
        >>> format_number(1234567, thousands_sep=" ")
        '1 234 567'
    """
    if precision is not None:
        number = round(number, precision)

    # Séparer partie entière et décimale
    if isinstance(number, float):
        int_part = int(number)
        dec_part = abs(number - int_part)
        if precision is not None and precision > 0:
            dec_str = f"{dec_part:.{precision}f}"[2:]  # Retirer "0."
        else:
            dec_str = f"{dec_part:.10f}".rstrip("0").lstrip("0")
            if dec_str:
                dec_str = dec_str[1:]  # Retirer le "."
    else:
        int_part = number
        dec_str = ""

    # Formater la partie entière
    int_str = str(abs(int_part))
    groups: list[str] = []
    while int_str:
        groups.append(int_str[-3:])
        int_str = int_str[:-3]

    formatted_int = thousands_sep.join(reversed(groups))

    # Ajouter le signe négatif si nécessaire
    if number < 0:
        formatted_int = "-" + formatted_int

    # Ajouter la partie décimale
    if dec_str:
        return f"{formatted_int}{decimal_sep}{dec_str}"
    return formatted_int


def format_percentage(
    value: float,
    *,
    precision: int = 1,
    multiply: bool = True,
) -> str:
    """Formate un nombre en pourcentage.

    Args:
        value: Valeur (0.0-1.0 ou 0-100).
        precision: Nombre de décimales.
        multiply: Si True, multiplie par 100.

    Returns:
        Pourcentage formaté.

    Example:
        >>> format_percentage(0.856)
        '85.6%'
        >>> format_percentage(85.6, multiply=False)
        '85.6%'
    """
    if multiply:
        value = value * 100
    return f"{value:.{precision}f}%"


def truncate(
    text: str,
    max_length: int,
    *,
    suffix: str = "...",
    position: TruncationPosition = TruncationPosition.END,
    word_boundary: bool = True,
) -> str:
    """Tronque un texte à une longueur maximale.

    Args:
        text: Texte à tronquer.
        max_length: Longueur maximale (incluant le suffixe).
        suffix: Suffixe à ajouter (défaut: "...").
        position: Position de la troncature (end, middle, start).
        word_boundary: Si True, tronque à la limite d'un mot.

    Returns:
        Texte tronqué.

    Example:
        >>> truncate("Hello World", 8)
        'Hello...'
        >>> truncate("Hello World", 8, position=TruncationPosition.MIDDLE)
        'Hel...ld'
    """
    if not text or len(text) <= max_length:
        return text

    if max_length <= len(suffix):
        return suffix[:max_length]

    available_length = max_length - len(suffix)

    if position == TruncationPosition.END:
        truncated = text[:available_length]
        if word_boundary:
            # Trouver la dernière espace
            last_space = truncated.rfind(" ")
            if last_space > available_length // 2:
                truncated = truncated[:last_space]
        return truncated + suffix

    elif position == TruncationPosition.START:
        truncated = text[-available_length:]
        if word_boundary:
            # Trouver la première espace
            first_space = truncated.find(" ")
            if first_space != -1 and first_space < len(truncated) // 2:
                truncated = truncated[first_space + 1:]
        return suffix + truncated

    elif position == TruncationPosition.MIDDLE:
        half_length = available_length // 2
        start = text[:half_length]
        end = text[-half_length:]
        if word_boundary:
            # Ajuster aux limites de mots
            last_space_start = start.rfind(" ")
            if last_space_start > half_length // 2:
                start = start[:last_space_start]
            first_space_end = end.find(" ")
            if first_space_end != -1 and first_space_end < half_length // 2:
                end = end[first_space_end + 1:]
        return start + suffix + end

    return text


def pad(
    text: str,
    width: int,
    *,
    fill_char: str = " ",
    align: str = "left",
) -> str:
    """Remplit un texte à une largeur donnée.

    Args:
        text: Texte à remplir.
        width: Largeur cible.
        fill_char: Caractère de remplissage.
        align: Alignement ("left", "right", "center").

    Returns:
        Texte rempli.

    Example:
        >>> pad("Hello", 10, align="center")
        '  Hello   '
    """
    if align == "left":
        return text.ljust(width, fill_char)
    elif align == "right":
        return text.rjust(width, fill_char)
    elif align == "center":
        return text.center(width, fill_char)
    else:
        raise ValueError(f"Invalid alignment: {align}")


def wrap_text(text: str, width: int, *, indent: str = "") -> str:
    """Wrap un texte à une largeur donnée.

    Args:
        text: Texte à wrapper.
        width: Largeur maximale par ligne.
        indent: Indentation pour chaque ligne.

    Returns:
        Texte wrappé.

    Example:
        >>> wrap_text("Hello World", 5)
        'Hello\\nWorld'
    """
    if not text:
        return text

    words = text.split()
    lines: list[str] = []
    current_line: list[str] = []
    current_length = 0

    for word in words:
        word_length = len(word)
        if current_length + word_length + len(current_line) > width:
            if current_line:
                lines.append(indent + " ".join(current_line))
            current_line = [word]
            current_length = word_length
        else:
            current_line.append(word)
            current_length += word_length

    if current_line:
        lines.append(indent + " ".join(current_line))

    return "\n".join(lines)


# ============================================================================
# COMPARAISON ET MATCHING
# ============================================================================


def similarity(
    text1: str,
    text2: str,
    *,
    method: str = "jaro_winkler",
    case_sensitive: bool = False,
) -> float:
    """Calcule la similarité entre deux chaînes.

    Args:
        text1: Première chaîne.
        text2: Deuxième chaîne.
        method: Méthode de calcul ("jaro_winkler", "levenshtein", "ratio").
        case_sensitive: Si False, ignore la casse.

    Returns:
        Score de similarité (0.0 à 1.0).

    Example:
        >>> similarity("One Piece", "One Piece")
        1.0
        >>> similarity("One Piece", "One Peace")
        0.88
    """
    if not case_sensitive:
        text1 = text1.lower()
        text2 = text2.lower()

    if text1 == text2:
        return 1.0

    if not text1 or not text2:
        return 0.0

    if method == "jaro_winkler":
        return _jaro_winkler_similarity(text1, text2)
    elif method == "levenshtein":
        return _levenshtein_similarity(text1, text2)
    elif method == "ratio":
        return _ratio_similarity(text1, text2)
    else:
        raise ValueError(f"Unknown method: {method}")


def _jaro_winkler_similarity(s1: str, s2: str) -> float:
    """Calcule la similarité Jaro-Winkler entre deux chaînes."""
    # Calculer la distance de Jaro
    if s1 == s2:
        return 1.0

    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0

    match_distance = max(len1, len2) // 2 - 1
    if match_distance < 0:
        match_distance = 0

    s1_matches = [False] * len1
    s2_matches = [False] * len2

    matches = 0
    transpositions = 0

    for i in range(len1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len2)

        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    jaro = (
        matches / len1
        + matches / len2
        + (matches - transpositions / 2) / matches
    ) / 3

    # Bonus de Winkler pour les préfixes communs
    prefix = 0
    for i in range(min(len1, len2, 4)):
        if s1[i] == s2[i]:
            prefix += 1
        else:
            break

    return jaro + prefix * 0.1 * (1 - jaro)


def _levenshtein_similarity(s1: str, s2: str) -> float:
    """Calcule la similarité basée sur la distance de Levenshtein."""
    distance = _levenshtein_distance(s1, s2)
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    return 1.0 - distance / max_len


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Calcule la distance de Levenshtein entre deux chaînes."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def _ratio_similarity(s1: str, s2: str) -> float:
    """Calcule la similarité basée sur le ratio de séquences communes."""
    if not s1 or not s2:
        return 0.0

    # Trouver les séquences communes
    matches = 0
    total = len(s1) + len(s2)

    # Algorithme simple : compter les caractères communs
    s1_chars = set(s1)
    s2_chars = set(s2)
    common = s1_chars & s2_chars

    for char in common:
        matches += min(s1.count(char), s2.count(char))

    if total == 0:
        return 0.0

    return 2.0 * matches / total


def fuzzy_match(
    query: str,
    candidates: Iterable[str],
    *,
    threshold: float = 0.6,
    max_results: int = 10,
) -> list[tuple[str, float]]:
    """Trouve les meilleures correspondances floues pour une requête.

    Args:
        query: Requête à matcher.
        candidates: Candidats à tester.
        threshold: Seuil de similarité minimum (0.0 à 1.0).
        max_results: Nombre maximum de résultats.

    Returns:
        Liste de tuples (candidat, score) triés par score décroissant.

    Example:
        >>> candidates = ["One Piece", "Naruto", "Bleach", "One Peace"]
        >>> fuzzy_match("One Peice", candidates, threshold=0.7)
        [('One Piece', 0.93), ('One Peace', 0.88)]
    """
    results: list[tuple[str, float]] = []

    for candidate in candidates:
        score = similarity(query, candidate)
        if score >= threshold:
            results.append((candidate, score))

    # Trier par score décroissant
    results.sort(key=lambda x: x[1], reverse=True)

    return results[:max_results]


def contains_any(text: str, substrings: Iterable[str], *, case_sensitive: bool = False) -> bool:
    """Vérifie si un texte contient l'un des sous-chaînes.

    Args:
        text: Texte à vérifier.
        substrings: Sous-chaînes à chercher.
        case_sensitive: Si False, ignore la casse.

    Returns:
        True si au moins une sous-chaîne est trouvée.

    Example:
        >>> contains_any("Hello World", ["world", "universe"])
        True
    """
    if not case_sensitive:
        text = text.lower()
        substrings = [s.lower() for s in substrings]

    return any(sub in text for sub in substrings)


def contains_all(text: str, substrings: Iterable[str], *, case_sensitive: bool = False) -> bool:
    """Vérifie si un texte contient toutes les sous-chaînes.

    Args:
        text: Texte à vérifier.
        substrings: Sous-chaînes à chercher.
        case_sensitive: Si False, ignore la casse.

    Returns:
        True si toutes les sous-chaînes sont trouvées.

    Example:
        >>> contains_all("Hello World", ["hello", "world"])
        True
    """
    if not case_sensitive:
        text = text.lower()
        substrings = [s.lower() for s in substrings]

    return all(sub in text for sub in substrings)


# ============================================================================
# CONVERSION ET NORMALISATION DE CAS
# ============================================================================


def to_case(text: str, case: TextCase) -> str:
    """Convertit un texte dans la casse spécifiée.

    Args:
        text: Texte à convertir.
        case: Casse cible.

    Returns:
        Texte converti.

    Example:
        >>> to_case("hello world", TextCase.TITLE)
        'Hello World'
        >>> to_case("HELLO WORLD", TextCase.LOWER)
        'hello world'
    """
    if case == TextCase.LOWER:
        return text.lower()
    elif case == TextCase.UPPER:
        return text.upper()
    elif case == TextCase.TITLE:
        return text.title()
    elif case == TextCase.SENTENCE:
        if not text:
            return text
        return text[0].upper() + text[1:].lower()
    else:
        raise ValueError(f"Unknown case: {case}")


def to_camel_case(text: str) -> str:
    """Convertit un texte en camelCase.

    Args:
        text: Texte à convertir.

    Returns:
        Texte en camelCase.

    Example:
        >>> to_camel_case("hello_world")
        'helloWorld'
        >>> to_camel_case("Hello World")
        'helloWorld'
    """
    words = re.split(r"[\s_\-]+", text)
    if not words:
        return ""
    return words[0].lower() + "".join(word.capitalize() for word in words[1:])


def to_pascal_case(text: str) -> str:
    """Convertit un texte en PascalCase.

    Args:
        text: Texte à convertir.

    Returns:
        Texte en PascalCase.

    Example:
        >>> to_pascal_case("hello_world")
        'HelloWorld'
    """
    words = re.split(r"[\s_\-]+", text)
    return "".join(word.capitalize() for word in words)


def to_snake_case(text: str) -> str:
    """Convertit un texte en snake_case.

    Args:
        text: Texte à convertir.

    Returns:
        Texte en snake_case.

    Example:
        >>> to_snake_case("HelloWorld")
        'hello_world'
        >>> to_snake_case("helloWorld")
        'hello_world'
    """
    # Insérer un underscore avant les majuscules
    text = re.sub(r"([A-Z])", r"_\1", text)
    # Remplacer les espaces et tirets par des underscores
    text = re.sub(r"[\s\-]+", "_", text)
    # Supprimer les underscores multiples
    text = re.sub(r"_+", "_", text)
    # Supprimer les underscores en début/fin
    text = text.strip("_")
    return text.lower()


def to_kebab_case(text: str) -> str:
    """Convertit un texte en kebab-case.

    Args:
        text: Texte à convertir.

    Returns:
        Texte en kebab-case.

    Example:
        >>> to_kebab_case("HelloWorld")
        'hello-world'
    """
    snake = to_snake_case(text)
    return snake.replace("_", "-")


# ============================================================================
# ANALYSE
# ============================================================================


def get_text_stats(text: str) -> TextStats:
    """Calcule les statistiques d'un texte.

    Args:
        text: Texte à analyser.

    Returns:
        Instance de TextStats.

    Example:
        >>> stats = get_text_stats("Hello World\\nHello Universe")
        >>> stats.word_count
        4
        >>> stats.unique_words
        3
    """
    if not text:
        return TextStats(
            length=0,
            word_count=0,
            line_count=0,
            char_count=0,
            unique_words=0,
            avg_word_length=0.0,
        )

    words = extract_words(text)
    unique_words = set(w.lower() for w in words)

    return TextStats(
        length=len(text),
        word_count=len(words),
        line_count=text.count("\n") + 1,
        char_count=len(text.replace(" ", "")),
        unique_words=len(unique_words),
        avg_word_length=sum(len(w) for w in words) / len(words) if words else 0.0,
    )


def count_words(text: str) -> int:
    """Compte le nombre de mots dans un texte.

    Args:
        text: Texte à analyser.

    Returns:
        Nombre de mots.
    """
    return len(extract_words(text))


def count_lines(text: str) -> int:
    """Compte le nombre de lignes dans un texte.

    Args:
        text: Texte à analyser.

    Returns:
        Nombre de lignes.
    """
    if not text:
        return 0
    return text.count("\n") + 1


def get_most_common_words(text: str, *, n: int = 10, lowercase: bool = True) -> list[tuple[str, int]]:
    """Retourne les mots les plus fréquents dans un texte.

    Args:
        text: Texte à analyser.
        n: Nombre de mots à retourner.
        lowercase: Si True, ignore la casse.

    Returns:
        Liste de tuples (mot, count) triés par fréquence décroissante.

    Example:
        >>> get_most_common_words("hello world hello", n=2)
        [('hello', 2), ('world', 1)]
    """
    words = extract_words(text, lowercase=lowercase)
    word_counts: dict[str, int] = {}

    for word in words:
        word_counts[word] = word_counts.get(word, 0) + 1

    sorted_words = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)
    return sorted_words[:n]


# ============================================================================
# DÉTECTION
# ============================================================================


def detect_script(text: str) -> str | None:
    """Détecte le script d'écriture dominant dans un texte.

    Args:
        text: Texte à analyser.

    Returns:
        Nom du script ("Latin", "CJK", "Cyrillic", etc.) ou None.

    Example:
        >>> detect_script("Hello World")
        'Latin'
        >>> detect_script("日本語")
        'Han'
    """
    if not text:
        return None

    script_counts: dict[str, int] = {}

    for char in text:
        if char.isspace() or char in PUNCTUATION:
            continue

        try:
            name = unicodedata.name(char, "")
            if "LATIN" in name:
                script_counts["Latin"] = script_counts.get("Latin", 0) + 1
            elif "CJK" in name or "HIRAGANA" in name or "KATAKANA" in name:
                script_counts["CJK"] = script_counts.get("CJK", 0) + 1
            elif "CYRILLIC" in name:
                script_counts["Cyrillic"] = script_counts.get("Cyrillic", 0) + 1
            elif "ARABIC" in name:
                script_counts["Arabic"] = script_counts.get("Arabic", 0) + 1
            elif "GREEK" in name:
                script_counts["Greek"] = script_counts.get("Greek", 0) + 1
            # Ajouter d'autres scripts selon les besoins
        except Exception:
            pass

    if not script_counts:
        return None

    return max(script_counts, key=script_counts.get)


def is_ascii(text: str) -> bool:
    """Vérifie si un texte contient uniquement des caractères ASCII.

    Args:
        text: Texte à vérifier.

    Returns:
        True si tous les caractères sont ASCII.
    """
    try:
        text.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def has_emoji(text: str) -> bool:
    """Vérifie si un texte contient des emojis.

    Args:
        text: Texte à vérifier.

    Returns:
        True si des emojis sont présents.
    """
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # Emoticons
        "\U0001F300-\U0001F5FF"  # Symbols & Pictographs
        "\U0001F680-\U0001F6FF"  # Transport & Map
        "\U0001F1E0-\U0001F1FF"  # Flags
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "]+",
        flags=re.UNICODE,
    )
    return bool(emoji_pattern.search(text))


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "CONTROL_CHARS",
    "INVALID_FILENAME_CHARS",
    "RESERVED_FILENAMES",
    "PUNCTUATION",
    "WHITESPACE_CHARS",
    "SIZE_UNITS",
    # Exceptions
    "TextError",
    "InvalidTextError",
    "EncodingError",
    # Enums
    "UnicodeNormalization",
    "TextCase",
    "TruncationPosition",
    # Modèles
    "TextStats",
    # Nettoyage et normalisation
    "normalize_unicode",
    "normalize_whitespace",
    "remove_control_chars",
    "remove_accents",
    "ascii_fold",
    "strip_html",
    # Slugification et sanitization
    "slugify",
    "sanitize_filename",
    "sanitize_for_display",
    # Extraction
    "extract_numbers",
    "extract_integers",
    "extract_chapter_number",
    "extract_volume_number",
    "extract_words",
    "extract_hashtags",
    "extract_mentions",
    "extract_urls",
    "extract_emails",
    # Formatage
    "format_size",
    "format_number",
    "format_percentage",
    "truncate",
    "pad",
    "wrap_text",
    # Comparaison et matching
    "similarity",
    "fuzzy_match",
    "contains_any",
    "contains_all",
    # Conversion de cas
    "to_case",
    "to_camel_case",
    "to_pascal_case",
    "to_snake_case",
    "to_kebab_case",
    # Analyse
    "get_text_stats",
    "count_words",
    "count_lines",
    "get_most_common_words",
    # Détection
    "detect_script",
    "is_ascii",
    "has_emoji",
]
