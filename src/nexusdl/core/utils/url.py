"""Utilitaires pour la manipulation d'URLs dans NexusDL.

Ce module fournit un ensemble complet de fonctions pour manipuler, valider,
normaliser et analyser les URLs dans le contexte du scraping de mangas,
webtoons et comics. Il est utilisé par tous les parsers et le système de
téléchargement pour traiter les URLs de manière robuste.

**Fonctionnalités principales** :
    - Normalisation d'URLs (scheme, host, path, encoding)
    - Résolution d'URLs relatives par rapport à une base
    - Parsing et construction d'URLs en composants
    - Manipulation des query parameters
    - Validation et sanitization
    - Extraction de composants (domaine, segments, IDs)
    - Détection de patterns d'URLs (regex avec groupes nommés)
    - Conversion d'URLs en noms de fichiers safe
    - Comparaison et matching d'URLs
    - Gestion des encodages (IDN, espaces, caractères spéciaux)
    - Support des URLs internationalisées (Unicode)

**Architecture** :
    - Fonctions pures (pas d'état global)
    - Utilisation de `urllib.parse` de la stdlib
    - Regex pour patterns spécifiques
    - Pydantic pour les modèles de résultat
    - Thread-safe et async-compatible

**Exemples d'utilisation** :
    >>> from nexusdl.core.utils.url import (
    ...     normalize_url, resolve_url, parse_url, extract_domain,
    ...     url_to_filename, is_valid_url, match_url_pattern,
    ... )
    >>>
    >>> # Normalisation
    >>> normalize_url("HTTPS://Example.COM/Manga/One-Piece/")
    'https://example.com/Manga/One-Piece'
    >>>
    >>> # Résolution d'URL relative
    >>> resolve_url("/chapter/123", "https://mangadex.org/title/456")
    'https://mangadex.org/chapter/123'
    >>>
    >>> # Extraction de domaine
    >>> extract_domain("https://cdn.mangadex.org/data/image.jpg")
    'cdn.mangadex.org'
    >>>
    >>> # Conversion en nom de fichier safe
    >>> url_to_filename("https://example.com/manga/one-piece/ch-001/page-01.jpg")
    'example.com_manga_one-piece_ch-001_page-01.jpg'
    >>>
    >>> # Pattern matching
    >>> pattern = UrlPattern(
    ...     name="mangadex_chapter",
    ...     regex=r"https://mangadex\\.org/chapter/(?P<chapter_id>\\d+)",
    ... )
    >>> match = match_url_pattern(
    ...     "https://mangadex.org/chapter/123456",
    ...     pattern,
    ... )
    >>> print(match.groups["chapter_id"])
    '123456'

Intégration :
    - core/parsers/* : utilise ces utilitaires pour traiter les URLs scrapées
    - core/downloader/* : utilise ces utilitaires pour valider et normaliser
    - core/session/* : utilise ces utilitaires pour construire les requêtes
    - core/image/* : utilise ces utilitaires pour extraire les URLs d'images
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Final, Iterable, Iterator, Mapping
from urllib.parse import (
    parse_qs,
    quote,
    unquote,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# CONSTANTES
# ============================================================================


# Schemes supportés
SUPPORTED_SCHEMES: Final[frozenset[str]] = frozenset({
    "http",
    "https",
    "ftp",
    "ftps",
})

# Schemes web (pour validation stricte)
WEB_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})

# Ports par défaut par scheme
DEFAULT_PORTS: Final[dict[str, int]] = {
    "http": 80,
    "https": 443,
    "ftp": 21,
    "ftps": 990,
}

# Caractères interdits dans les noms de fichiers (multi-plateforme)
INVALID_FILENAME_CHARS: Final[frozenset[str]] = frozenset({
    "<", ">", ":", '"', "/", "\\", "|", "?", "*",
    "\x00", "\x01", "\x02", "\x03", "\x04", "\x05", "\x06", "\x07",
    "\x08", "\x09", "\x0a", "\x0b", "\x0c", "\x0d", "\x0e", "\x0f",
    "\x10", "\x11", "\x12", "\x13", "\x14", "\x15", "\x16", "\x17",
    "\x18", "\x19", "\x1a", "\x1b", "\x1c", "\x1d", "\x1e", "\x1f",
})

# Caractères réservés dans les URLs (RFC 3986)
RESERVED_CHARS: Final[frozenset[str]] = frozenset({
    "!", "*", "'", "(", ")", ";", ":", "@", "&",
    "=", "+", "$", ",", "/", "?", "#", "[", "]",
})

# Caractères non-safe pour les URLs
UNSAFE_CHARS: Final[frozenset[str]] = frozenset({
    " ", "<", ">", "\"", "{", "}", "|", "\\", "^", "`",
})

# Extensions d'images courantes
IMAGE_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif",
    ".bmp", ".tiff", ".tif", ".svg",
})

# Longueur maximale d'un nom de fichier
MAX_FILENAME_LENGTH: Final[int] = 200

# Longueur maximale d'une URL
MAX_URL_LENGTH: Final[int] = 8000

# Longueur maximale d'un domaine
MAX_DOMAIN_LENGTH: Final[int] = 253

# Pattern pour valider un domaine (simplifié)
DOMAIN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"[a-zA-Z]{2,}$"
)

# Pattern pour détecter les IPs
IPV4_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
    r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
)

# Pattern pour détecter les URLs
URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(https?|ftp)://"
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+"
    r"(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|"
    r"localhost|"
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
    r"(?::\d+)?"
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)

# Pattern pour extraire les segments de chemin
PATH_SEGMENT_PATTERN: Final[re.Pattern[str]] = re.compile(r"/([^/]+)")

# Pattern pour détecter les nombres dans les chaînes
NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(r"\d+(?:\.\d+)?")

# Noms de fichiers réservés (Windows)
RESERVED_FILENAMES: Final[frozenset[str]] = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
})


# ============================================================================
# EXCEPTIONS
# ============================================================================


class UrlError(NexusDLError):
    """Exception de base pour les erreurs liées aux URLs."""


class InvalidUrlError(UrlError):
    """Exception levée lorsqu'une URL est invalide.

    Attributes:
        url: URL invalide.
        reason: Raison de l'invalidité.
    """

    def __init__(self, url: str, reason: str = "") -> None:
        msg = f"URL invalide: {url!r}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.url = url
        self.reason = reason


class UrlParseError(UrlError):
    """Exception levée lorsqu'une URL ne peut être parsée."""

    def __init__(self, url: str, reason: str = "") -> None:
        msg = f"Erreur de parsing de l'URL: {url!r}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.url = url
        self.reason = reason


class UrlPatternError(UrlError):
    """Exception levée lorsqu'un pattern d'URL est invalide."""

    def __init__(self, pattern: str, reason: str = "") -> None:
        msg = f"Pattern d'URL invalide: {pattern!r}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.pattern = pattern
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class UrlScheme(str, Enum):
    """Schemes d'URL supportés."""

    HTTP = "http"
    HTTPS = "https"
    FTP = "ftp"
    FTPS = "ftps"

    @property
    def default_port(self) -> int:
        """Port par défaut pour ce scheme."""
        return DEFAULT_PORTS[self.value]

    @property
    def is_web(self) -> bool:
        """Indique si c'est un scheme web (HTTP/HTTPS)."""
        return self in (UrlScheme.HTTP, UrlScheme.HTTPS)

    @property
    def is_secure(self) -> bool:
        """Indique si le scheme est sécurisé (HTTPS/FTPS)."""
        return self in (UrlScheme.HTTPS, UrlScheme.FTPS)


class UrlMatchType(str, Enum):
    """Type de correspondance d'URL.

    EXACT   : Correspondance exacte.
    PREFIX  : Correspondance par préfixe.
    PATTERN : Correspondance par regex.
    DOMAIN  : Correspondance par domaine.
    """

    EXACT = "exact"
    PREFIX = "prefix"
    PATTERN = "pattern"
    DOMAIN = "domain"


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class ParsedUrl(BaseModel):
    """URL décomposée en ses composants.

    Modèle immuable représentant une URL parsée avec tous ses composants
    accessibles individuellement.

    Attributes:
        scheme: Scheme de l'URL (http, https, etc.).
        username: Nom d'utilisateur (si présent).
        password: Mot de passe (si présent).
        hostname: Nom d'hôte (domaine ou IP).
        port: Numéro de port (None = port par défaut).
        path: Chemin de l'URL.
        params: Paramètres de chemin (rarement utilisé).
        query: Query string (sans le '?').
        fragment: Fragment (sans le '#').
        netloc: Netloc complet (user:pass@host:port).
    """

    scheme: str = Field(default="", description="Scheme de l'URL.")
    username: str | None = Field(default=None, description="Nom d'utilisateur.")
    password: str | None = Field(default=None, description="Mot de passe.")
    hostname: str | None = Field(default=None, description="Nom d'hôte.")
    port: int | None = Field(default=None, description="Numéro de port.")
    path: str = Field(default="", description="Chemin de l'URL.")
    params: str = Field(default="", description="Paramètres de chemin.")
    query: str = Field(default="", description="Query string.")
    fragment: str = Field(default="", description="Fragment.")
    netloc: str = Field(default="", description="Netloc complet.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def base_url(self) -> str:
        """URL de base (scheme + netloc)."""
        if not self.scheme or not self.hostname:
            return ""
        return f"{self.scheme}://{self.netloc}"

    @property
    def full_url(self) -> str:
        """URL complète reconstruite."""
        return urlunparse((
            self.scheme,
            self.netloc,
            self.path,
            self.params,
            self.query,
            self.fragment,
        ))

    @property
    def domain(self) -> str:
        """Domaine de l'URL (sans sous-domaines)."""
        if not self.hostname:
            return ""
        return extract_root_domain(self.hostname)

    @property
    def subdomain(self) -> str | None:
        """Sous-domaine (si présent)."""
        if not self.hostname:
            return None
        parts = self.hostname.split(".")
        if len(parts) > 2:
            return ".".join(parts[:-2])
        return None

    @property
    def path_segments(self) -> list[str]:
        """Segments du chemin (sans les slashes)."""
        return extract_path_segments(self.path)

    @property
    def query_params(self) -> dict[str, list[str]]:
        """Paramètres de query string parsés."""
        if not self.query:
            return {}
        return parse_qs(self.query)

    @property
    def is_absolute(self) -> bool:
        """Indique si l'URL est absolue (a un scheme)."""
        return bool(self.scheme)

    @property
    def is_secure(self) -> bool:
        """Indique si l'URL utilise un scheme sécurisé."""
        return self.scheme in ("https", "ftps")

    @property
    def effective_port(self) -> int:
        """Port effectif (explicite ou par défaut)."""
        if self.port is not None:
            return self.port
        if self.scheme in DEFAULT_PORTS:
            return DEFAULT_PORTS[self.scheme]
        return 0

    def to_tuple(self) -> tuple[str, str, str, str, str, str]:
        """Convertit en tuple pour urlunparse."""
        return (
            self.scheme,
            self.netloc,
            self.path,
            self.params,
            self.query,
            self.fragment,
        )


class UrlPatternMatch(BaseModel):
    """Résultat d'un match entre une URL et un pattern.

    Attributes:
        matched: True si l'URL correspond au pattern.
        pattern_name: Nom du pattern (si fourni).
        groups: Groupes nommés extraits du match.
        full_match: Match complet (groupe 0).
        url: URL testée.
    """

    matched: bool = Field(..., description="True si l'URL match.")
    pattern_name: str = Field(default="", description="Nom du pattern.")
    groups: dict[str, str] = Field(
        default_factory=dict,
        description="Groupes nommés extraits.",
    )
    full_match: str = Field(default="", description="Match complet.")
    url: str = Field(..., description="URL testée.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    def get(self, name: str, default: str = "") -> str:
        """Récupère un groupe nommé avec valeur par défaut.

        Args:
            name: Nom du groupe.
            default: Valeur par défaut si absent.

        Returns:
            Valeur du groupe ou default.
        """
        return self.groups.get(name, default)

    def get_int(self, name: str, default: int = 0) -> int:
        """Récupère un groupe nommé converti en int.

        Args:
            name: Nom du groupe.
            default: Valeur par défaut si absent ou invalide.

        Returns:
            Valeur du groupe en int ou default.
        """
        value = self.groups.get(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def get_float(self, name: str, default: float = 0.0) -> float:
        """Récupère un groupe nommé converti en float.

        Args:
            name: Nom du groupe.
            default: Valeur par défaut si absent ou invalide.

        Returns:
            Valeur du groupe en float ou default.
        """
        value = self.groups.get(name)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            return default


class UrlPattern(BaseModel):
    """Pattern d'URL avec regex et métadonnées.

    Représente un pattern d'URL compilé avec des groupes nommés pour
    extraire des informations (ID de manga, numéro de chapitre, etc.).

    Attributes:
        name: Nom du pattern (pour identification).
        regex: Expression régulière (avec groupes nommés).
        description: Description du pattern.
        site_id: ID du site associé (optionnel).
        priority: Priorité du pattern (plus élevé = testé en premier).
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Nom du pattern.",
    )
    regex: str = Field(
        ...,
        min_length=1,
        description="Expression régulière.",
    )
    description: str = Field(
        default="",
        max_length=500,
        description="Description du pattern.",
    )
    site_id: str | None = Field(
        default=None,
        max_length=100,
        description="ID du site associé.",
    )
    priority: int = Field(
        default=0,
        ge=0,
        le=1000,
        description="Priorité du pattern.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Compiled regex (mis en cache)
    _compiled: re.Pattern[str] | None = None

    def model_post_init(self, __context: Any) -> None:
        """Compile la regex après initialisation."""
        try:
            object.__setattr__(self, "_compiled", re.compile(self.regex))
        except re.error as e:
            raise UrlPatternError(self.regex, f"Regex invalide: {e}") from e

    @property
    def compiled(self) -> re.Pattern[str]:
        """Regex compilée."""
        if self._compiled is None:
            try:
                compiled = re.compile(self.regex)
                object.__setattr__(self, "_compiled", compiled)
                return compiled
            except re.error as e:
                raise UrlPatternError(self.regex, f"Regex invalide: {e}") from e
        return self._compiled

    @property
    def group_names(self) -> list[str]:
        """Noms des groupes capturants."""
        return list(self.compiled.groupindex.keys())

    def match(self, url: str) -> UrlPatternMatch:
        """Teste l'URL contre ce pattern.

        Args:
            url: URL à tester.

        Returns:
            Résultat du match.
        """
        m = self.compiled.search(url)
        if m is None:
            return UrlPatternMatch(
                matched=False,
                pattern_name=self.name,
                url=url,
            )

        return UrlPatternMatch(
            matched=True,
            pattern_name=self.name,
            groups=m.groupdict(),
            full_match=m.group(0),
            url=url,
        )


# ============================================================================
# VALIDATION
# ============================================================================


def is_valid_url(
    url: str,
    *,
    require_scheme: bool = True,
    require_host: bool = True,
    allowed_schemes: Iterable[str] | None = None,
) -> bool:
    """Vérifie si une URL est valide.

    Args:
        url: URL à valider.
        require_scheme: Exiger la présence d'un scheme.
        require_host: Exiger la présence d'un host.
        allowed_schemes: Schemes autorisés (None = tous).

    Returns:
        True si l'URL est valide.

    Example:
        >>> is_valid_url("https://example.com/path")
        True
        >>> is_valid_url("not a url")
        False
        >>> is_valid_url("ftp://example.com", allowed_schemes=["https"])
        False
    """
    if not url or not isinstance(url, str):
        return False

    if len(url) > MAX_URL_LENGTH:
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    # Vérifier le scheme
    if require_scheme:
        if not parsed.scheme:
            return False
        if parsed.scheme.lower() not in SUPPORTED_SCHEMES:
            return False
        if allowed_schemes is not None:
            if parsed.scheme.lower() not in {s.lower() for s in allowed_schemes}:
                return False

    # Vérifier le host
    if require_host:
        if not parsed.hostname:
            return False
        if not is_valid_domain(parsed.hostname):
            # Accepter aussi localhost et les IPs
            if parsed.hostname not in ("localhost",):
                if not IPV4_PATTERN.match(parsed.hostname):
                    return False

    # Vérifier le port
    if parsed.port is not None:
        if not (0 < parsed.port <= 65535):
            return False

    return True


def is_valid_domain(domain: str) -> bool:
    """Vérifie si un domaine est valide.

    Args:
        domain: Domaine à valider.

    Returns:
        True si le domaine est valide.

    Example:
        >>> is_valid_domain("example.com")
        True
        >>> is_valid_domain("sub.example.co.uk")
        True
        >>> is_valid_domain("invalid")
        False
    """
    if not domain or not isinstance(domain, str):
        return False

    if len(domain) > MAX_DOMAIN_LENGTH:
        return False

    # Accepter les IPs
    if IPV4_PATTERN.match(domain):
        return True

    # Valider avec le pattern
    return DOMAIN_PATTERN.match(domain) is not None


def validate_url(url: str) -> str:
    """Valide une URL et la retourne normalisée.

    Args:
        url: URL à valider.

    Returns:
        URL normalisée.

    Raises:
        InvalidUrlError: Si l'URL est invalide.
    """
    if not is_valid_url(url):
        raise InvalidUrlError(url, "URL invalide")
    return normalize_url(url)


# ============================================================================
# NORMALISATION
# ============================================================================


def normalize_url(
    url: str,
    *,
    strip_fragment: bool = True,
    strip_trailing_slash: bool = True,
    lowercase_scheme: bool = True,
    lowercase_host: bool = True,
    sort_query_params: bool = False,
    remove_default_port: bool = True,
    decode_path: bool = False,
) -> str:
    """Normalise une URL en appliquant des transformations standard.

    Cette fonction applique les règles de normalisation recommandées pour
    comparer et dédupliquer les URLs.

    Args:
        url: URL à normaliser.
        strip_fragment: Retirer le fragment (#...).
        strip_trailing_slash: Retirer le slash final du chemin.
        lowercase_scheme: Mettre le scheme en minuscules.
        lowercase_host: Mettre le host en minuscules.
        sort_query_params: Trier les paramètres de query.
        remove_default_port: Retirer le port s'il est par défaut.
        decode_path: Décoder les caractères encodés du chemin.

    Returns:
        URL normalisée.

    Raises:
        InvalidUrlError: Si l'URL est invalide.

    Example:
        >>> normalize_url("HTTPS://Example.COM:443/path/?b=2&a=1#frag")
        'https://example.com/path?a=1&b=2'
    """
    if not url or not isinstance(url, str):
        raise InvalidUrlError(url, "URL vide ou non-string")

    url = url.strip()
    if not url:
        raise InvalidUrlError(url, "URL vide après strip")

    try:
        parsed = urlparse(url)
    except Exception as e:
        raise InvalidUrlError(url, f"Erreur de parsing: {e}") from e

    # Scheme
    scheme = parsed.scheme.lower() if lowercase_scheme else parsed.scheme

    # Netloc (user:pass@host:port)
    netloc = _normalize_netloc(
        parsed,
        lowercase_host=lowercase_host,
        remove_default_port=remove_default_port,
    )

    # Path
    path = parsed.path
    if decode_path:
        path = unquote(path)
        path = quote(path, safe="/:@!$&'()*+,;=-._~")
    if strip_trailing_slash and path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    # Query
    query = parsed.query
    if sort_query_params and query:
        params = parse_qs(query, keep_blank_values=True)
        sorted_params = sorted(params.items())
        query_parts: list[str] = []
        for key, values in sorted_params:
            for value in values:
                query_parts.append(f"{quote(key, safe='')}={quote(value, safe='')}")
        query = "&".join(query_parts)

    # Fragment
    fragment = "" if strip_fragment else parsed.fragment

    # Reconstruire l'URL
    normalized = urlunparse((
        scheme,
        netloc,
        path,
        parsed.params,
        query,
        fragment,
    ))

    return normalized


def _normalize_netloc(
    parsed: Any,
    *,
    lowercase_host: bool = True,
    remove_default_port: bool = True,
) -> str:
    """Normalise le netloc d'une URL parsée.

    Args:
        parsed: Résultat de urlparse.
        lowercase_host: Mettre le host en minuscules.
        remove_default_port: Retirer le port par défaut.

    Returns:
        Netloc normalisé.
    """
    parts: list[str] = []

    # User info
    if parsed.username:
        userinfo = quote(parsed.username, safe="")
        if parsed.password:
            userinfo += ":" + quote(parsed.password, safe="")
        parts.append(userinfo + "@")

    # Host
    host = parsed.hostname or ""
    if lowercase_host:
        host = host.lower()
    parts.append(host)

    # Port
    if parsed.port is not None:
        if not remove_default_port or parsed.port != DEFAULT_PORTS.get(
            parsed.scheme.lower(), 0
        ):
            parts.append(f":{parsed.port}")

    return "".join(parts)


def ensure_scheme(url: str, default_scheme: str = "https") -> str:
    """Ajoute un scheme à une URL s'il est manquant.

    Args:
        url: URL à traiter.
        default_scheme: Scheme par défaut (défaut: "https").

    Returns:
        URL avec scheme.

    Example:
        >>> ensure_scheme("example.com/path")
        'https://example.com/path'
        >>> ensure_scheme("http://example.com")
        'http://example.com'
    """
    url = url.strip()
    if not url:
        return url

    # Déjà un scheme ?
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        return url

    # Protocol-relative URL (//example.com)
    if url.startswith("//"):
        return f"{default_scheme}:{url}"

    return f"{default_scheme}://{url}"


def strip_trailing_slash(url: str) -> str:
    """Retire le slash final d'une URL.

    Args:
        url: URL à traiter.

    Returns:
        URL sans slash final (sauf si c'est juste "/").

    Example:
        >>> strip_trailing_slash("https://example.com/path/")
        'https://example.com/path'
        >>> strip_trailing_slash("https://example.com/")
        'https://example.com/'
    """
    if not url:
        return url

    parsed = urlparse(url)
    if parsed.path == "/":
        return url

    if url.endswith("/"):
        return url[:-1]
    return url


def strip_query_and_fragment(url: str) -> str:
    """Retire la query string et le fragment d'une URL.

    Args:
        url: URL à traiter.

    Returns:
        URL sans query ni fragment.

    Example:
        >>> strip_query_and_fragment("https://example.com/path?a=1#frag")
        'https://example.com/path'
    """
    parsed = urlparse(url)
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        "",
        "",
    ))


# ============================================================================
# RÉSOLUTION
# ============================================================================


def resolve_url(url: str, base: str) -> str:
    """Résout une URL relative par rapport à une URL de base.

    Args:
        url: URL à résoudre (peut être relative).
        base: URL de base.

    Returns:
        URL absolue résolue.

    Raises:
        InvalidUrlError: Si la base est invalide.

    Example:
        >>> resolve_url("/chapter/123", "https://example.com/manga/456")
        'https://example.com/chapter/123'
        >>> resolve_url("page.html", "https://example.com/dir/")
        'https://example.com/dir/page.html'
    """
    if not base:
        raise InvalidUrlError(base, "URL de base vide")

    # Si l'URL est déjà absolue, la retourner
    if is_valid_url(url, require_scheme=True, require_host=True):
        return url

    # Résoudre avec urljoin
    resolved = urljoin(base, url)

    if not resolved:
        raise InvalidUrlError(url, f"Impossible de résoudre par rapport à {base}")

    return resolved


def get_base_url(url: str) -> str:
    """Extrait l'URL de base (scheme + host + port).

    Args:
        url: URL source.

    Returns:
        URL de base.

    Example:
        >>> get_base_url("https://example.com:8080/path/to/page")
        'https://example.com:8080'
    """
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def get_parent_url(url: str) -> str:
    """Retourne l'URL parent (un niveau au-dessus).

    Args:
        url: URL source.

    Returns:
        URL parent.

    Example:
        >>> get_parent_url("https://example.com/a/b/c")
        'https://example.com/a/b'
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if "/" in path:
        parent_path = path.rsplit("/", 1)[0] or "/"
    else:
        parent_path = "/"

    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parent_path,
        "",
        "",
        "",
    ))


# ============================================================================
# EXTRACTION DE COMPOSANTS
# ============================================================================


def parse_url(url: str) -> ParsedUrl:
    """Parse une URL en ses composants.

    Args:
        url: URL à parser.

    Returns:
        Instance de ParsedUrl.

    Example:
        >>> parsed = parse_url("https://user:pass@example.com:8080/path?a=1#frag")
        >>> parsed.scheme
        'https'
        >>> parsed.hostname
        'example.com'
        >>> parsed.port
        8080
    """
    parsed = urlparse(url)
    return ParsedUrl(
        scheme=parsed.scheme,
        username=parsed.username,
        password=parsed.password,
        hostname=parsed.hostname,
        port=parsed.port,
        path=parsed.path,
        params=parsed.params,
        query=parsed.query,
        fragment=parsed.fragment,
        netloc=parsed.netloc,
    )


def extract_domain(url: str, *, include_subdomain: bool = False) -> str:
    """Extrait le domaine d'une URL.

    Args:
        url: URL source.
        include_subdomain: Inclure les sous-domaines.

    Returns:
        Domaine extrait.

    Example:
        >>> extract_domain("https://cdn.example.com/path")
        'example.com'
        >>> extract_domain("https://cdn.example.com/path", include_subdomain=True)
        'cdn.example.com'
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    if not hostname:
        return ""

    if include_subdomain:
        return hostname.lower()

    return extract_root_domain(hostname)


def extract_root_domain(domain: str) -> str:
    """Extrait le domaine racine (sans sous-domaines).

    Note: Cette fonction utilise une heuristique simple. Pour une extraction
    précise des TLDs complexes (ex: co.uk), utiliser une librairie comme
    `tldextract`.

    Args:
        domain: Domaine complet.

    Returns:
        Domaine racine.

    Example:
        >>> extract_root_domain("cdn.example.com")
        'example.com'
        >>> extract_root_domain("example.com")
        'example.com'
    """
    if not domain:
        return ""

    domain = domain.lower().strip()
    parts = domain.split(".")

    if len(parts) <= 2:
        return domain

    # Heuristique : prendre les 2 derniers segments
    # (ne gère pas les TLDs composés comme .co.uk)
    return ".".join(parts[-2:])


def extract_path_segments(path: str) -> list[str]:
    """Extrait les segments du chemin d'une URL.

    Args:
        path: Chemin à traiter.

    Returns:
        Liste des segments (sans les slashes).

    Example:
        >>> extract_path_segments("/manga/one-piece/chapter/123")
        ['manga', 'one-piece', 'chapter', '123']
    """
    if not path:
        return []

    # Retirer les slashes de début et fin
    path = path.strip("/")
    if not path:
        return []

    return [seg for seg in path.split("/") if seg]


def extract_query_params(url: str) -> dict[str, list[str]]:
    """Extrait les paramètres de query string d'une URL.

    Args:
        url: URL source.

    Returns:
        Dictionnaire des paramètres (valeurs en listes).

    Example:
        >>> extract_query_params("https://example.com?a=1&b=2&a=3")
        {'a': ['1', '3'], 'b': ['2']}
    """
    parsed = urlparse(url)
    if not parsed.query:
        return {}
    return parse_qs(parsed.query, keep_blank_values=True)


def extract_id_from_url(
    url: str,
    *,
    patterns: Iterable[str] | None = None,
) -> str | None:
    """Extrait un ID numérique depuis une URL.

    Cherche le premier nombre dans l'URL, en priorisant les patterns fournis.

    Args:
        url: URL source.
        patterns: Patterns regex optionnels (avec groupe nommé 'id').

    Returns:
        ID extrait ou None.

    Example:
        >>> extract_id_from_url("https://mangadex.org/chapter/123456")
        '123456'
        >>> extract_id_from_url("https://example.com/manga/one-piece/ch-42")
        '42'
    """
    if not url:
        return None

    # Essayer les patterns fournis
    if patterns:
        for pattern in patterns:
            try:
                m = re.search(pattern, url)
                if m:
                    groups = m.groupdict()
                    if "id" in groups:
                        return groups["id"]
                    # Retourner le premier groupe
                    if m.groups():
                        return m.group(1)
            except re.error:
                continue

    # Fallback : chercher un nombre dans le dernier segment
    segments = extract_path_segments(urlparse(url).path)
    for segment in reversed(segments):
        # Chercher un nombre pur
        if segment.isdigit():
            return segment
        # Chercher un nombre dans le segment
        m = NUMBER_PATTERN.search(segment)
        if m:
            return m.group(0)

    return None


def extract_extension(url: str) -> str:
    """Extrait l'extension de fichier d'une URL.

    Args:
        url: URL source.

    Returns:
        Extension (avec le point) ou chaîne vide.

    Example:
        >>> extract_extension("https://example.com/image.jpg")
        '.jpg'
        >>> extract_extension("https://example.com/image.jpg?v=123")
        '.jpg'
    """
    parsed = urlparse(url)
    path = parsed.path
    if not path:
        return ""

    # Retirer la query et le fragment
    last_segment = path.rsplit("/", 1)[-1]
    if "." not in last_segment:
        return ""

    ext = last_segment.rsplit(".", 1)[-1].lower()
    return f".{ext}" if ext else ""


def extract_filename_from_url(url: str) -> str:
    """Extrait le nom de fichier d'une URL.

    Args:
        url: URL source.

    Returns:
        Nom de fichier ou chaîne vide.

    Example:
        >>> extract_filename_from_url("https://example.com/path/image.jpg?v=123")
        'image.jpg'
    """
    parsed = urlparse(url)
    path = parsed.path
    if not path:
        return ""

    return path.rsplit("/", 1)[-1]


# ============================================================================
# MANIPULATION DES QUERY PARAMETERS
# ============================================================================


def add_query_params(
    url: str,
    params: Mapping[str, Any],
    *,
    replace: bool = False,
) -> str:
    """Ajoute des paramètres à la query string d'une URL.

    Args:
        url: URL source.
        params: Paramètres à ajouter.
        replace: Si True, remplace les paramètres existants.

    Returns:
        URL avec les nouveaux paramètres.

    Example:
        >>> add_query_params("https://example.com?a=1", {"b": 2})
        'https://example.com?a=1&b=2'
        >>> add_query_params("https://example.com?a=1", {"a": 2}, replace=True)
        'https://example.com?a=2'
    """
    if not params:
        return url

    parsed = urlparse(url)
    existing = parse_qs(parsed.query, keep_blank_values=True) if parsed.query else {}

    if replace:
        existing.clear()

    # Ajouter les nouveaux paramètres
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            existing[key] = [str(v) for v in value]
        else:
            existing[key] = [str(value)]

    # Reconstruire la query string
    new_query = urlencode(existing, doseq=True)

    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment,
    ))


def remove_query_params(url: str, params: Iterable[str]) -> str:
    """Retire des paramètres de la query string d'une URL.

    Args:
        url: URL source.
        params: Noms des paramètres à retirer.

    Returns:
        URL sans les paramètres spécifiés.

    Example:
        >>> remove_query_params("https://example.com?a=1&b=2&c=3", ["a", "c"])
        'https://example.com?b=2'
    """
    params_to_remove = set(params)
    if not params_to_remove:
        return url

    parsed = urlparse(url)
    if not parsed.query:
        return url

    existing = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {k: v for k, v in existing.items() if k not in params_to_remove}
    new_query = urlencode(filtered, doseq=True)

    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment,
    ))


def get_query_param(url: str, name: str, default: str | None = None) -> str | None:
    """Récupère la valeur d'un paramètre de query string.

    Args:
        url: URL source.
        name: Nom du paramètre.
        default: Valeur par défaut si absent.

    Returns:
        Valeur du paramètre ou default.

    Example:
        >>> get_query_param("https://example.com?a=1&b=2", "a")
        '1'
        >>> get_query_param("https://example.com?a=1", "b", "default")
        'default'
    """
    parsed = urlparse(url)
    if not parsed.query:
        return default

    params = parse_qs(parsed.query, keep_blank_values=True)
    values = params.get(name)
    if not values:
        return default
    return values[0]


def set_query_param(url: str, name: str, value: Any) -> str:
    """Définit la valeur d'un paramètre de query string.

    Args:
        url: URL source.
        name: Nom du paramètre.
        value: Valeur à définir.

    Returns:
        URL avec le paramètre défini.
    """
    return add_query_params(url, {name: value}, replace=False)


# ============================================================================
# CONSTRUCTION D'URLS
# ============================================================================


def build_url(
    scheme: str,
    host: str,
    path: str = "",
    *,
    port: int | None = None,
    query: Mapping[str, Any] | None = None,
    fragment: str = "",
    username: str | None = None,
    password: str | None = None,
) -> str:
    """Construit une URL depuis ses composants.

    Args:
        scheme: Scheme (http, https, etc.).
        host: Nom d'hôte.
        path: Chemin.
        port: Numéro de port (optionnel).
        query: Paramètres de query (optionnel).
        fragment: Fragment (optionnel).
        username: Nom d'utilisateur (optionnel).
        password: Mot de passe (optionnel).

    Returns:
        URL construite.

    Example:
        >>> build_url("https", "example.com", "/path", query={"a": 1})
        'https://example.com/path?a=1'
    """
    # Construire le netloc
    netloc_parts: list[str] = []
    if username:
        userinfo = quote(username, safe="")
        if password:
            userinfo += ":" + quote(password, safe="")
        netloc_parts.append(userinfo + "@")
    netloc_parts.append(host)
    if port is not None:
        netloc_parts.append(f":{port}")
    netloc = "".join(netloc_parts)

    # Construire la query string
    query_str = ""
    if query:
        query_str = urlencode(query, doseq=True)

    # Normaliser le path
    if path and not path.startswith("/"):
        path = "/" + path

    return urlunparse((
        scheme,
        netloc,
        path,
        "",
        query_str,
        fragment,
    ))


def join_urls(*parts: str) -> str:
    """Joint plusieurs segments d'URL.

    Args:
        *parts: Segments à joindre.

    Returns:
        URL jointe.

    Example:
        >>> join_urls("https://example.com", "path", "to", "page")
        'https://example.com/path/to/page'
    """
    if not parts:
        return ""

    result = parts[0]
    for part in parts[1:]:
        if not part:
            continue
        # Retirer le slash final de result et le slash initial de part
        result = result.rstrip("/")
        part = part.lstrip("/")
        result = f"{result}/{part}"

    return result


def build_absolute_url(base: str, path: str) -> str:
    """Construit une URL absolue depuis une base et un chemin.

    Args:
        base: URL de base.
        path: Chemin à ajouter.

    Returns:
        URL absolue.

    Example:
        >>> build_absolute_url("https://example.com", "/api/v1/manga")
        'https://example.com/api/v1/manga'
    """
    base = base.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


# ============================================================================
# COMPARAISON ET MATCHING
# ============================================================================


def is_same_domain(url1: str, url2: str) -> bool:
    """Vérifie si deux URLs sont du même domaine.

    Args:
        url1: Première URL.
        url2: Deuxième URL.

    Returns:
        True si les domaines sont identiques.

    Example:
        >>> is_same_domain("https://example.com/a", "http://example.com/b")
        True
        >>> is_same_domain("https://a.example.com", "https://b.example.com")
        True
    """
    domain1 = extract_domain(url1, include_subdomain=False)
    domain2 = extract_domain(url2, include_subdomain=False)
    return domain1 == domain2


def is_same_host(url1: str, url2: str) -> bool:
    """Vérifie si deux URLs ont le même host exact.

    Args:
        url1: Première URL.
        url2: Deuxième URL.

    Returns:
        True si les hosts sont identiques.

    Example:
        >>> is_same_host("https://example.com/a", "https://example.com/b")
        True
        >>> is_same_host("https://a.example.com", "https://b.example.com")
        False
    """
    host1 = (urlparse(url1).hostname or "").lower()
    host2 = (urlparse(url2).hostname or "").lower()
    return host1 == host2


def is_subdomain_of(url: str, domain: str) -> bool:
    """Vérifie si une URL est un sous-domaine d'un domaine donné.

    Args:
        url: URL à vérifier.
        domain: Domaine parent.

    Returns:
        True si l'URL est un sous-domaine.

    Example:
        >>> is_subdomain_of("https://cdn.example.com/path", "example.com")
        True
        >>> is_subdomain_of("https://example.com/path", "example.com")
        True
    """
    host = (urlparse(url).hostname or "").lower()
    domain = domain.lower()

    if host == domain:
        return True
    return host.endswith("." + domain)


def urls_are_equivalent(url1: str, url2: str) -> bool:
    """Vérifie si deux URLs sont équivalentes après normalisation.

    Args:
        url1: Première URL.
        url2: Deuxième URL.

    Returns:
        True si les URLs sont équivalentes.

    Example:
        >>> urls_are_equivalent(
        ...     "HTTPS://Example.COM/path/",
        ...     "https://example.com/path",
        ... )
        True
    """
    try:
        norm1 = normalize_url(url1)
        norm2 = normalize_url(url2)
        return norm1 == norm2
    except InvalidUrlError:
        return False


def match_url_pattern(
    url: str,
    pattern: UrlPattern | str,
    *,
    name: str = "",
) -> UrlPatternMatch:
    """Teste une URL contre un pattern.

    Args:
        url: URL à tester.
        pattern: Pattern (UrlPattern ou regex string).
        name: Nom du pattern (si regex string).

    Returns:
        Résultat du match.

    Example:
        >>> pattern = UrlPattern(
        ...     name="chapter",
        ...     regex=r"/chapter/(?P<id>\\d+)",
        ... )
        >>> match = match_url_pattern("https://example.com/chapter/123", pattern)
        >>> match.matched
        True
        >>> match.groups["id"]
        '123'
    """
    if isinstance(pattern, str):
        pattern = UrlPattern(name=name or "anonymous", regex=pattern)

    return pattern.match(url)


def match_any_pattern(
    url: str,
    patterns: Iterable[UrlPattern],
    *,
    first_match: bool = True,
) -> list[UrlPatternMatch]:
    """Teste une URL contre plusieurs patterns.

    Args:
        url: URL à tester.
        patterns: Patterns à tester.
        first_match: Si True, arrête au premier match.

    Returns:
        Liste des matches (ou liste vide si aucun).
    """
    matches: list[UrlPatternMatch] = []

    # Trier par priorité (décroissante)
    sorted_patterns = sorted(patterns, key=lambda p: p.priority, reverse=True)

    for pattern in sorted_patterns:
        match = pattern.match(url)
        if match.matched:
            matches.append(match)
            if first_match:
                break

    return matches


# ============================================================================
# CONVERSION ET SANITIZATION
# ============================================================================


def url_to_filename(
    url: str,
    *,
    max_length: int = MAX_FILENAME_LENGTH,
    preserve_extension: bool = True,
    hash_suffix: bool = False,
) -> str:
    """Convertit une URL en nom de fichier safe.

    Remplace les caractères problématiques et préserve l'extension si demandée.

    Args:
        url: URL à convertir.
        max_length: Longueur maximale du nom.
        preserve_extension: Préserver l'extension du fichier.
        hash_suffix: Ajouter un hash court pour éviter les collisions.

    Returns:
        Nom de fichier safe.

    Example:
        >>> url_to_filename("https://example.com/manga/one-piece/ch-01/page-01.jpg")
        'example.com_manga_one-piece_ch-01_page-01.jpg'
    """
    if not url:
        return ""

    parsed = urlparse(url)

    # Construire le nom depuis le host + path
    parts: list[str] = []
    if parsed.hostname:
        parts.append(parsed.hostname)
    if parsed.path:
        segments = extract_path_segments(parsed.path)
        parts.extend(segments)

    # Joindre avec des underscores
    name = "_".join(parts)

    # Sanitization
    name = sanitize_filename(name)

    # Gérer l'extension
    if preserve_extension:
        ext = extract_extension(url)
        if ext and not name.lower().endswith(ext.lower()):
            name = name + ext

    # Ajouter un hash si demandé
    if hash_suffix:
        import hashlib
        hash_value = hashlib.md5(url.encode()).hexdigest()[:8]
        # Insérer avant l'extension
        if preserve_extension and "." in name:
            base, ext = name.rsplit(".", 1)
            name = f"{base}_{hash_value}.{ext}"
        else:
            name = f"{name}_{hash_value}"

    # Limiter la longueur
    if len(name) > max_length:
        if preserve_extension and "." in name:
            base, ext = name.rsplit(".", 1)
            name = base[: max_length - len(ext) - 1] + "." + ext
        else:
            name = name[:max_length]

    return name


def sanitize_filename(name: str, *, replacement: str = "_") -> str:
    """Sanitize un nom de fichier en remplaçant les caractères interdits.

    Args:
        name: Nom à sanitiser.
        replacement: Caractère de remplacement.

    Returns:
        Nom sanitizé.

    Example:
        >>> sanitize_filename('file:name<test>.txt')
        'file_name_test_.txt'
    """
    if not name:
        return name

    # Normaliser Unicode (NFC)
    name = unicodedata.normalize("NFC", name)

    # Remplacer les caractères interdits
    result: list[str] = []
    for char in name:
        if char in INVALID_FILENAME_CHARS:
            result.append(replacement)
        else:
            result.append(char)

    name = "".join(result)

    # Retirer les espaces en début/fin
    name = name.strip()

    # Éviter les noms réservés (Windows)
    base_name = name.rsplit(".", 1)[0].upper()
    if base_name in RESERVED_FILENAMES:
        name = "_" + name

    # Éviter les noms vides
    if not name or name in (".", ".."):
        name = "_unnamed_"

    return name


def slugify(text: str, *, max_length: int = 100) -> str:
    """Convertit un texte en slug URL-safe.

    Args:
        text: Texte à convertir.
        max_length: Longueur maximale.

    Returns:
        Slug.

    Example:
        >>> slugify("One Piece - Chapter 123: Adventure!")
        'one-piece-chapter-123-adventure'
    """
    if not text:
        return ""

    # Normaliser Unicode (NFKD) et retirer les accents
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")

    # Minuscules
    text = text.lower()

    # Remplacer les caractères non-alphanumériques par des tirets
    text = re.sub(r"[^a-z0-9]+", "-", text)

    # Retirer les tirets en début/fin
    text = text.strip("-")

    # Retirer les tirets multiples
    text = re.sub(r"-+", "-", text)

    # Limiter la longueur
    if len(text) > max_length:
        text = text[:max_length].rstrip("-")

    return text


# ============================================================================
# ENCODAGE
# ============================================================================


def encode_url(url: str, *, safe: str = "/:@!$&'()*+,;=-._~?#[]") -> str:
    """Encode une URL en remplaçant les caractères non-safe.

    Args:
        url: URL à encoder.
        safe: Caractères à ne pas encoder.

    Returns:
        URL encodée.

    Example:
        >>> encode_url("https://example.com/path with spaces")
        'https://example.com/path%20with%20spaces'
    """
    if not url:
        return url

    # Parser l'URL
    parsed = urlparse(url)

    # Encoder chaque composant séparément
    path = quote(parsed.path, safe=safe)
    query = quote(parsed.query, safe=safe)
    fragment = quote(parsed.fragment, safe=safe)

    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        path,
        parsed.params,
        query,
        fragment,
    ))


def decode_url(url: str) -> str:
    """Décode une URL (inverse de encode_url).

    Args:
        url: URL à décoder.

    Returns:
        URL décodée.

    Example:
        >>> decode_url("https://example.com/path%20with%20spaces")
        'https://example.com/path with spaces'
    """
    if not url:
        return url

    return unquote(url)


def encode_idn_domain(domain: str) -> str:
    """Encode un domaine internationalisé (IDN) en Punycode.

    Args:
        domain: Domaine à encoder.

    Returns:
        Domaine encodé.

    Example:
        >>> encode_idn_domain("例え.jp")
        'xn--r8jz45g.jp'
    """
    if not domain:
        return domain

    try:
        return domain.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        return domain


def decode_idn_domain(domain: str) -> str:
    """Décode un domaine Punycode en Unicode.

    Args:
        domain: Domaine à décoder.

    Returns:
        Domaine décodé.

    Example:
        >>> decode_idn_domain("xn--r8jz45g.jp")
        '例え.jp'
    """
    if not domain:
        return domain

    try:
        parts = domain.split(".")
        decoded_parts = []
        for part in parts:
            if part.startswith("xn--"):
                decoded_parts.append(part.encode("ascii").decode("idna"))
            else:
                decoded_parts.append(part)
        return ".".join(decoded_parts)
    except (UnicodeError, UnicodeDecodeError):
        return domain


# ============================================================================
# DÉTECTION DE TYPE
# ============================================================================


def is_image_url(url: str) -> bool:
    """Vérifie si une URL pointe vers une image.

    Args:
        url: URL à vérifier.

    Returns:
        True si l'URL semble pointer vers une image.

    Example:
        >>> is_image_url("https://example.com/image.jpg")
        True
        >>> is_image_url("https://example.com/page.html")
        False
    """
    ext = extract_extension(url).lower()
    return ext in IMAGE_EXTENSIONS


def is_data_url(url: str) -> bool:
    """Vérifie si une URL est une data URL.

    Args:
        url: URL à vérifier.

    Returns:
        True si c'est une data URL.

    Example:
        >>> is_data_url("data:image/png;base64,abc...")
        True
    """
    return url.startswith("data:")


def is_blob_url(url: str) -> bool:
    """Vérifie si une URL est une blob URL.

    Args:
        url: URL à vérifier.

    Returns:
        True si c'est une blob URL.
    """
    return url.startswith("blob:")


def is_protocol_relative_url(url: str) -> bool:
    """Vérifie si une URL est protocol-relative (//example.com).

    Args:
        url: URL à vérifier.

    Returns:
        True si protocol-relative.
    """
    return url.startswith("//") and not url.startswith("///")


def is_absolute_url(url: str) -> bool:
    """Vérifie si une URL est absolue (a un scheme).

    Args:
        url: URL à vérifier.

    Returns:
        True si absolue.
    """
    parsed = urlparse(url)
    return bool(parsed.scheme and parsed.netloc)


def is_relative_url(url: str) -> bool:
    """Vérifie si une URL est relative.

    Args:
        url: URL à vérifier.

    Returns:
        True si relative.
    """
    return not is_absolute_url(url) and not is_protocol_relative_url(url)


# ============================================================================
# ITERATION
# ============================================================================


def iter_urls(text: str, *, schemes: Iterable[str] | None = None) -> Iterator[str]:
    """Extrait toutes les URLs d'un texte.

    Args:
        text: Texte source.
        schemes: Schemes à rechercher (défaut: http, https).

    Yields:
        URLs trouvées.

    Example:
        >>> list(iter_urls("Visit https://example.com or http://test.org"))
        ['https://example.com', 'http://test.org']
    """
    if schemes is None:
        schemes = ("http", "https")

    schemes_pattern = "|".join(re.escape(s) for s in schemes)
    pattern = re.compile(
        rf"(?:{'|'.join(schemes_pattern)})://[^\s<>\"']+",
        re.IGNORECASE,
    )

    for match in pattern.finditer(text):
        url = match.group(0).rstrip(".,;:!?)]}'\"")
        if is_valid_url(url):
            yield url


def iter_domains(urls: Iterable[str]) -> Iterator[str]:
    """Extrait les domaines uniques d'une liste d'URLs.

    Args:
        urls: URLs à traiter.

    Yields:
        Domaines uniques (dans l'ordre de première apparition).
    """
    seen: set[str] = set()
    for url in urls:
        domain = extract_domain(url, include_subdomain=True)
        if domain and domain not in seen:
            seen.add(domain)
            yield domain


# ============================================================================
# HELPERS AVANCÉS
# ============================================================================


def get_url_depth(url: str) -> int:
    """Retourne la profondeur du chemin d'une URL.

    Args:
        url: URL source.

    Returns:
        Nombre de segments dans le chemin.

    Example:
        >>> get_url_depth("https://example.com/a/b/c")
        3
    """
    parsed = urlparse(url)
    segments = extract_path_segments(parsed.path)
    return len(segments)


def get_common_prefix(urls: Iterable[str]) -> str:
    """Trouve le préfixe commun d'une liste d'URLs.

    Args:
        urls: URLs à analyser.

    Returns:
        Préfixe commun.

    Example:
        >>> get_common_prefix([
        ...     "https://example.com/a/1",
        ...     "https://example.com/a/2",
        ...     "https://example.com/a/3",
        ... ])
        'https://example.com/a/'
    """
    urls_list = list(urls)
    if not urls_list:
        return ""
    if len(urls_list) == 1:
        return urls_list[0]

    # Trouver le préfixe commun caractère par caractère
    prefix = urls_list[0]
    for url in urls_list[1:]:
        while not url.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""

    # S'assurer que le préfixe se termine à une limite de segment
    if prefix and not prefix.endswith("/"):
        prefix = prefix.rsplit("/", 1)[0] + "/"

    return prefix


def fingerprint_url(url: str) -> str:
    """Génère un fingerprint court pour une URL (pour déduplication).

    Args:
        url: URL à fingerprinter.

    Returns:
        Hash court (16 caractères hex).

    Example:
        >>> fp = fingerprint_url("https://example.com/path")
        >>> len(fp)
        16
    """
    import hashlib

    normalized = normalize_url(url)
    hash_bytes = hashlib.sha256(normalized.encode()).digest()
    return hash_bytes[:8].hex()


def simplify_url(url: str) -> str:
    """Simplifie une URL en retirant les parties non essentielles.

    Retire :
        - Query parameters de tracking (utm_*, fbclid, etc.)
        - Fragment
        - Slash final

    Args:
        url: URL à simplifier.

    Returns:
        URL simplifiée.

    Example:
        >>> simplify_url("https://example.com/page?utm_source=twitter#section")
        'https://example.com/page'
    """
    TRACKING_PARAMS: Final[frozenset[str]] = frozenset({
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "msclkid", "twclid", "igshid",
        "ref", "source", "medium", "campaign",
    })

    parsed = urlparse(url)

    # Filtrer les query parameters
    if parsed.query:
        params = parse_qs(parsed.query, keep_blank_values=True)
        filtered = {
            k: v for k, v in params.items()
            if k.lower() not in TRACKING_PARAMS
        }
        new_query = urlencode(filtered, doseq=True)
    else:
        new_query = ""

    # Retirer le slash final
    path = parsed.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        path,
        parsed.params,
        new_query,
        "",  # Retirer le fragment
    ))


def is_cdn_url(url: str) -> bool:
    """Détecte si une URL provient d'un CDN connu.

    Args:
        url: URL à vérifier.

    Returns:
        True si l'URL semble provenir d'un CDN.
    """
    CDN_INDICATORS: Final[frozenset[str]] = frozenset({
        "cdn", "cache", "static", "assets", "media", "img", "images",
        "cloudfront", "cloudflare", "akamai", "fastly",
    })

    domain = extract_domain(url, include_subdomain=True).lower()
    parts = domain.split(".")

    return any(part in CDN_INDICATORS for part in parts)


def guess_url_purpose(url: str) -> str:
    """Devine le but d'une URL en analysant son chemin.

    Args:
        url: URL à analyser.

    Returns:
        But deviné ('manga', 'chapter', 'page', 'image', 'api', 'other').
    """
    parsed = urlparse(url)
    path = parsed.path.lower()
    segments = extract_path_segments(path)

    # Patterns courants
    MANGA_INDICATORS: Final[frozenset[str]] = frozenset({
        "manga", "title", "series", "comic", "webtoon", "manhwa", "manhua",
    })
    CHAPTER_INDICATORS: Final[frozenset[str]] = frozenset({
        "chapter", "ch", "chap", "episode", "ep",
    })
    API_INDICATORS: Final[frozenset[str]] = frozenset({
        "api", "v1", "v2", "v3", "rest", "graphql",
    })

    for segment in segments:
        if segment in MANGA_INDICATORS:
            return "manga"
        if segment in CHAPTER_INDICATORS:
            return "chapter"
        if segment in API_INDICATORS:
            return "api"

    # Vérifier l'extension
    ext = extract_extension(url).lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in (".html", ".htm", ".php", ".asp"):
        return "page"

    return "other"


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "SUPPORTED_SCHEMES",
    "WEB_SCHEMES",
    "DEFAULT_PORTS",
    "INVALID_FILENAME_CHARS",
    "RESERVED_CHARS",
    "UNSAFE_CHARS",
    "IMAGE_EXTENSIONS",
    "MAX_FILENAME_LENGTH",
    "MAX_URL_LENGTH",
    "MAX_DOMAIN_LENGTH",
    "DOMAIN_PATTERN",
    "IPV4_PATTERN",
    "URL_PATTERN",
    "PATH_SEGMENT_PATTERN",
    "NUMBER_PATTERN",
    "RESERVED_FILENAMES",
    # Exceptions
    "UrlError",
    "InvalidUrlError",
    "UrlParseError",
    "UrlPatternError",
    # Enums
    "UrlScheme",
    "UrlMatchType",
    # Modèles
    "ParsedUrl",
    "UrlPatternMatch",
    "UrlPattern",
    # Validation
    "is_valid_url",
    "is_valid_domain",
    "validate_url",
    # Normalisation
    "normalize_url",
    "ensure_scheme",
    "strip_trailing_slash",
    "strip_query_and_fragment",
    # Résolution
    "resolve_url",
    "get_base_url",
    "get_parent_url",
    # Extraction
    "parse_url",
    "extract_domain",
    "extract_root_domain",
    "extract_path_segments",
    "extract_query_params",
    "extract_id_from_url",
    "extract_extension",
    "extract_filename_from_url",
    # Query parameters
    "add_query_params",
    "remove_query_params",
    "get_query_param",
    "set_query_param",
    # Construction
    "build_url",
    "join_urls",
    "build_absolute_url",
    # Comparaison
    "is_same_domain",
    "is_same_host",
    "is_subdomain_of",
    "urls_are_equivalent",
    "match_url_pattern",
    "match_any_pattern",
    # Conversion
    "url_to_filename",
    "sanitize_filename",
    "slugify",
    # Encodage
    "encode_url",
    "decode_url",
    "encode_idn_domain",
    "decode_idn_domain",
    # Détection
    "is_image_url",
    "is_data_url",
    "is_blob_url",
    "is_protocol_relative_url",
    "is_absolute_url",
    "is_relative_url",
    # Itération
    "iter_urls",
    "iter_domains",
    # Helpers avancés
    "get_url_depth",
    "get_common_prefix",
    "fingerprint_url",
    "simplify_url",
    "is_cdn_url",
    "guess_url_purpose",
]
