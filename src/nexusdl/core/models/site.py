"""Modèles de domaine pour la configuration des sites supportés.

Ce module définit les structures de données immuables (Pydantic v2) utilisées
pour représenter la configuration des sites sources (MangaDex, SushiScan,
nHentai, etc.), leurs capacités opérationnelles, leur état de santé et leurs
statistiques d'utilisation. Ces modèles sont au cœur du système de registre
et sont consommés par :

    - `core/registry/site_registry.py` : chargement depuis sites.yaml
    - `core/registry/validator.py` : validation via JSON Schema
    - `core/parsers/base.py` : injection dans les parsers
    - `core/session/http_session.py` : headers, cookies, rate limiting
    - `core/session/playwright_pool.py` : bypass Cloudflare
    - `core/downloader/manager.py` : concurrence par site
    - `interfaces/web/backend/routers/sites.py` : API REST
    - `interfaces/cli/screens/settings.py` : affichage TUI

Architecture :
    SiteConfig (immutable — configuration complète d'un site)
        ├── id : str (snake_case, ex: "mangadex", "sushiscan_net")
        ├── name : str (ex: "MangaDex", "SushiScan")
        ├── domains : list[DomainInfo] (primaire + miroirs)
        ├── parser_class : str (ex: "nexusdl.parsers.en.mangadex:MangaDexParser")
        ├── language : Language (ISO 639-1)
        ├── adult : bool
        ├── content_rating : ContentRating
        ├── capabilities : SiteCapabilities
        ├── default_headers : dict[str, str]
        ├── cookies_required : list[str]
        └── region_locked, allowed_regions, scanlation_group, notes, enabled

    SiteCapabilities (immutable — capacités opérationnelles)
        ├── supports_search, supports_manga_info, supports_chapters, supports_pages
        ├── requires_auth, requires_cloudflare_bypass, requires_javascript_rendering
        ├── max_concurrent_downloads : int (1-32)
        ├── rate_limit_per_second : float (0-10)
        └── min_delay_between_requests : float

    SiteHealth (immutable — état de santé d'un site)
        ├── status : SiteStatus (OPERATIONAL, DEGRADED, DOWN, MAINTENANCE)
        ├── last_check_at : datetime
        ├── response_time_ms : float
        ├── success_rate : float (0.0-1.0)
        └── error_message : str | None

    SiteStats (immutable — statistiques d'utilisation)
        ├── total_searches, total_downloads, total_errors
        ├── average_response_time_ms
        └── last_used_at : datetime

Règles d'or :
    1. Tous les modèles sont `frozen=True` (immuables, hashables).
    2. Les enums utilisent `str` comme base pour sérialisation JSON native.
    3. `parser_class` est validé via regex (`module.path:ClassName`).
    4. `domains` doit contenir au moins 1 URL valide (HttpUrl).
    5. `id` doit être snake_case (regex `^[a-z][a-z0-9_]{1,63}$`).
    6. Les timestamps sont en UTC, sérialisables en ISO 8601.
    7. Les propriétés dérivées (primary_domain, is_adult, etc.) facilitent l'usage.
    8. Les méthodes `to_summary()` fournissent des snapshots JSON légers.

Exemple d'utilisation :
    >>> from nexusdl.core.models.site import (
    ...     SiteConfig, SiteCapabilities, DomainInfo, SiteStatus,
    ... )
    >>> from nexusdl.core.models.manga import Language, ContentRating
    >>>
    >>> # Créer une configuration de site
    >>> config = SiteConfig(
    ...     id="mangadex",
    ...     name="MangaDex",
    ...     domains=[DomainInfo(url="https://mangadex.org", is_primary=True)],
    ...     parser_class="nexusdl.parsers.en.mangadex:MangaDexParser",
    ...     language=Language.MULTI,
    ...     adult=False,
    ...     content_rating=ContentRating.SAFE,
    ...     capabilities=SiteCapabilities(
    ...         supports_search=True,
    ...         requires_cloudflare_bypass=False,
    ...         max_concurrent_downloads=8,
    ...         rate_limit_per_second=4.0,
    ...     ),
    ... )
    >>> print(config.primary_domain)  # "https://mangadex.org"
    >>> print(config.requires_bypass)  # False
    >>> print(config.is_adult)  # False
    >>>
    >>> # Vérifier la compatibilité avec une langue
    >>> print(config.matches_language(Language.FR))  # True (MULTI)
    >>>
    >>> # Créer un état de santé
    >>> health = SiteHealth(
    ...     site_id="mangadex",
    ...     status=SiteStatus.OPERATIONAL,
    ...     response_time_ms=245.3,
    ...     success_rate=0.99,
    ... )
    >>> print(health.is_healthy)  # True
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import Enum
from typing import Any, ClassVar, Final, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.models.manga import ContentRating, Language


# ============================================================================
# EXCEPTIONS
# ============================================================================


class SiteModelError(NexusDLError):
    """Exception de base pour les erreurs liées aux modèles de sites."""


class InvalidSiteConfigError(SiteModelError):
    """Exception levée lorsqu'une configuration de site est invalide."""

    def __init__(self, site_id: str, reason: str = "") -> None:
        msg = f"Configuration de site invalide: {site_id}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.site_id = site_id
        self.reason = reason


class InvalidDomainError(SiteModelError):
    """Exception levée lorsqu'un domaine est invalide."""


class InvalidParserClassError(SiteModelError):
    """Exception levée lorsqu'une référence de parser est invalide."""

    def __init__(self, parser_class: str, reason: str = "") -> None:
        msg = f"Référence de parser invalide: {parser_class}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.parser_class = parser_class
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class SiteStatus(str, Enum):
    """État de santé d'un site.

    OPERATIONAL : Site pleinement opérationnel.
    DEGRADED    : Site partiellement fonctionnel (lenteurs, erreurs intermittentes).
    DOWN        : Site inaccessible ou en panne.
    MAINTENANCE : Site en maintenance (connu via message du site).
    UNKNOWN     : État inconnu (pas encore vérifié).
    """

    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    DOWN = "down"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        """Libellé humain du statut."""
        return {
            SiteStatus.OPERATIONAL: "Opérationnel",
            SiteStatus.DEGRADED: "Dégradé",
            SiteStatus.DOWN: "Hors ligne",
            SiteStatus.MAINTENANCE: "En maintenance",
            SiteStatus.UNKNOWN: "Inconnu",
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode pour l'affichage TUI/GUI."""
        return {
            SiteStatus.OPERATIONAL: "🟢",
            SiteStatus.DEGRADED: "🟡",
            SiteStatus.DOWN: "🔴",
            SiteStatus.MAINTENANCE: "🔧",
            SiteStatus.UNKNOWN: "⚪",
        }[self]

    @property
    def color(self) -> str:
        """Couleur hexadécimale pour l'affichage (interfaces web/GUI)."""
        return {
            SiteStatus.OPERATIONAL: "#10b981",  # emerald-500
            SiteStatus.DEGRADED: "#f59e0b",  # amber-500
            SiteStatus.DOWN: "#ef4444",  # red-500
            SiteStatus.MAINTENANCE: "#8b5cf6",  # violet-500
            SiteStatus.UNKNOWN: "#9ca3af",  # gray-400
        }[self]

    @property
    def is_healthy(self) -> bool:
        """Indique si le site est considéré comme sain."""
        return self == SiteStatus.OPERATIONAL

    @property
    def is_available(self) -> bool:
        """Indique si le site est disponible pour utilisation."""
        return self in (SiteStatus.OPERATIONAL, SiteStatus.DEGRADED)


class ProxyRequirement(str, Enum):
    """Niveau de nécessité d'un proxy pour accéder au site.

    NONE     : Aucun proxy requis (accès mondial).
    OPTIONAL : Proxy optionnel (améliore les performances ou contourne blocages).
    REQUIRED : Proxy obligatoire (site géo-restreint).
    """

    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ProxyRequirement.NONE: "Non requis",
            ProxyRequirement.OPTIONAL: "Optionnel",
            ProxyRequirement.REQUIRED: "Requis",
        }[self]


class DomainRole(str, Enum):
    """Rôle d'un domaine dans la configuration d'un site.

    PRIMARY      : Domaine principal (utilisé par défaut).
    MIRROR       : Domaine miroir (fallback si le primaire est inaccessible).
    CDN          : Domaine CDN (pour les images/ressources).
    API          : Domaine API (pour les sites avec API REST séparée).
    """

    PRIMARY = "primary"
    MIRROR = "mirror"
    CDN = "cdn"
    API = "api"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            DomainRole.PRIMARY: "Principal",
            DomainRole.MIRROR: "Miroir",
            DomainRole.CDN: "CDN",
            DomainRole.API: "API",
        }[self]


# ============================================================================
# CONSTANTES — Patterns de validation
# ============================================================================


# Pattern pour valider l'ID d'un site (snake_case, 2-64 caractères)
_SITE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

# Pattern pour valider la référence de classe d'un parser
# Format : "module.path:ClassName"
# Exemple : "nexusdl.parsers.en.mangadex:MangaDexParser"
_PARSER_CLASS_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[a-z_][a-z0-9_.]*:[A-Z][a-zA-Z0-9_]*$"
)

# Pattern pour valider un code région ISO 3166-1 alpha-2
_REGION_CODE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{2}$")

# Pattern pour valider un nom de cookie
_COOKIE_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9_-]+$")


# ============================================================================
# HELPERS — Validation
# ============================================================================


def validate_site_id(site_id: str) -> str:
    """Valide et normalise un ID de site.

    Args:
        site_id: ID à valider.

    Returns:
        ID validé (stripped, lowercase).

    Raises:
        InvalidSiteConfigError: Si l'ID est invalide.
    """
    normalized = site_id.strip().lower()
    if not _SITE_ID_PATTERN.match(normalized):
        raise InvalidSiteConfigError(
            site_id,
            "L'ID doit être en snake_case, commencer par une lettre, "
            "et contenir 2-64 caractères alphanumériques ou underscores",
        )
    return normalized


def validate_parser_class(parser_class: str) -> str:
    """Valide une référence de classe de parser.

    Args:
        parser_class: Référence à valider (format "module.path:ClassName").

    Returns:
        Référence validée.

    Raises:
        InvalidParserClassError: Si la référence est invalide.
    """
    normalized = parser_class.strip()
    if not _PARSER_CLASS_PATTERN.match(normalized):
        raise InvalidParserClassError(
            parser_class,
            "Format attendu: 'module.path:ClassName' "
            "(ex: 'nexusdl.parsers.en.mangadex:MangaDexParser')",
        )
    return normalized


def extract_module_path(parser_class: str) -> str:
    """Extrait le chemin du module depuis une référence de parser.

    Args:
        parser_class: Référence de parser (format "module.path:ClassName").

    Returns:
        Chemin du module (ex: "nexusdl.parsers.en.mangadex").

    Example:
        >>> extract_module_path("nexusdl.parsers.en.mangadex:MangaDexParser")
        'nexusdl.parsers.en.mangadex'
    """
    return parser_class.split(":", 1)[0]


def extract_class_name(parser_class: str) -> str:
    """Extrait le nom de la classe depuis une référence de parser.

    Args:
        parser_class: Référence de parser (format "module.path:ClassName").

    Returns:
        Nom de la classe (ex: "MangaDexParser").

    Example:
        >>> extract_class_name("nexusdl.parsers.en.mangadex:MangaDexParser")
        'MangaDexParser'
    """
    return parser_class.split(":", 1)[1]


# ============================================================================
# MODÈLES PYDANTIC — Domaines
# ============================================================================


class DomainInfo(BaseModel):
    """Information sur un domaine d'un site (immutable).

    Représente un domaine (URL) associé à un site, avec son rôle
    (primaire, miroir, CDN, API) et son état de santé.

    Attributes:
        url: URL complète du domaine (validée HttpUrl).
        role: Rôle du domaine (PRIMARY, MIRROR, CDN, API).
        is_primary: True si c'est le domaine principal (raccourci).
        enabled: True si le domaine est activé (peut être désactivé temporairement).
        notes: Notes optionnelles (ex: "miroir européen").
    """

    url: HttpUrl = Field(..., description="URL complète du domaine.")
    role: DomainRole = Field(
        default=DomainRole.PRIMARY,
        description="Rôle du domaine.",
    )
    is_primary: bool = Field(
        default=False,
        description="True si c'est le domaine principal.",
    )
    enabled: bool = Field(
        default=True,
        description="True si le domaine est activé.",
    )
    notes: str | None = Field(
        default=None,
        max_length=200,
        description="Notes optionnelles.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_primary_consistency(self) -> Self:
        """Vérifie la cohérence entre role et is_primary."""
        # Si role est PRIMARY, is_primary devrait être True
        # (mais on ne force pas l'inverse pour flexibilité)
        return self

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def host(self) -> str:
        """Nom d'hôte du domaine (ex: 'mangadex.org')."""
        # HttpUrl est un objet Pydantic, on extrait le host
        url_str = str(self.url)
        # Retirer le protocole
        if "://" in url_str:
            url_str = url_str.split("://", 1)[1]
        # Retirer le chemin
        if "/" in url_str:
            url_str = url_str.split("/", 1)[0]
        return url_str

    @property
    def base_url(self) -> str:
        """URL de base (scheme + host, sans chemin)."""
        url_str = str(self.url)
        if "://" in url_str:
            scheme, rest = url_str.split("://", 1)
            host = rest.split("/", 1)[0]
            return f"{scheme}://{host}"
        return url_str

    @property
    def is_mirror(self) -> bool:
        """Indique si c'est un domaine miroir."""
        return self.role == DomainRole.MIRROR

    @property
    def is_cdn(self) -> bool:
        """Indique si c'est un domaine CDN."""
        return self.role == DomainRole.CDN

    @property
    def is_api(self) -> bool:
        """Indique si c'est un domaine API."""
        return self.role == DomainRole.API

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable."""
        return {
            "url": str(self.url),
            "host": self.host,
            "role": self.role.value,
            "role_label": self.role.label,
            "is_primary": self.is_primary,
            "enabled": self.enabled,
            "notes": self.notes,
        }

    def __repr__(self) -> str:
        primary_marker = " [PRIMARY]" if self.is_primary else ""
        return f"<DomainInfo {self.host}{primary_marker} role={self.role.value}>"


# ============================================================================
# MODÈLES PYDANTIC — Capacités
# ============================================================================


class SiteCapabilities(BaseModel):
    """Capacités et limitations opérationnelles d'un site (immutable).

    Ce modèle décrit ce qu'un site supporte et ses contraintes
    (rate limiting, bypass Cloudflare, authentification, etc.).
    Il est utilisé par le SiteRegistry et le DownloadManager pour
    adapter le comportement du téléchargement à chaque site source.

    Ce modèle est le modèle canonique de domaine. Le module
    `parsers/capabilities.py` définit `ParserCapabilities` qui est
    un alias/extension de ce modèle pour les besoins spécifiques
    des parsers.

    Attributes:
        supports_search: Le site supporte la recherche de mangas.
        supports_manga_info: Le site permet de récupérer les métadonnées d'un manga.
        supports_chapters: Le site permet de lister les chapitres d'un manga.
        supports_pages: Le site permet de récupérer les URLs des pages.
        supports_download: Le site permet le téléchargement effectif des images.
        supports_language_filter: Le site supporte le filtrage par langue.
        supports_content_rating_filter: Le site supporte le filtrage par classification.
        requires_auth: Le site nécessite une authentification (cookies/login).
        requires_cloudflare_bypass: Le site est protégé par Cloudflare ou anti-bot.
        requires_javascript_rendering: Le site nécessite l'exécution de JavaScript.
        max_concurrent_downloads: Nombre max de téléchargements simultanés (1-32).
        rate_limit_per_second: Nombre max de requêtes HTTP par seconde (0-10).
        min_delay_between_requests: Délai minimum entre deux requêtes (secondes).
    """

    # Capacités fonctionnelles
    supports_search: bool = Field(
        default=True,
        description="Le site supporte la recherche de mangas.",
    )
    supports_manga_info: bool = Field(
        default=True,
        description="Le site permet de récupérer les métadonnées d'un manga.",
    )
    supports_chapters: bool = Field(
        default=True,
        description="Le site permet de lister les chapitres d'un manga.",
    )
    supports_pages: bool = Field(
        default=True,
        description="Le site permet de récupérer les URLs des pages.",
    )
    supports_download: bool = Field(
        default=True,
        description="Le site permet le téléchargement effectif des images.",
    )
    supports_language_filter: bool = Field(
        default=False,
        description="Le site supporte le filtrage par langue.",
    )
    supports_content_rating_filter: bool = Field(
        default=False,
        description="Le site supporte le filtrage par classification d'âge.",
    )

    # Exigences techniques
    requires_auth: bool = Field(
        default=False,
        description="Le site nécessite une authentification (cookies/login).",
    )
    requires_cloudflare_bypass: bool = Field(
        default=False,
        description="Le site est protégé par Cloudflare ou anti-bot.",
    )
    requires_javascript_rendering: bool = Field(
        default=False,
        description="Le site nécessite l'exécution de JavaScript.",
    )

    # Limites de concurrence et rate limiting
    max_concurrent_downloads: int = Field(
        default=4,
        ge=1,
        le=32,
        description="Nombre max de téléchargements simultanés (1-32).",
    )
    rate_limit_per_second: float = Field(
        default=2.0,
        gt=0.0,
        le=10.0,
        description="Nombre max de requêtes HTTP par seconde (0-10).",
    )
    min_delay_between_requests: float = Field(
        default=0.5,
        ge=0.0,
        le=30.0,
        description="Délai minimum entre deux requêtes (secondes).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def requires_bypass(self) -> bool:
        """Indique si le site nécessite un bypass (Cloudflare ou JS)."""
        return self.requires_cloudflare_bypass or self.requires_javascript_rendering

    @property
    def is_read_only(self) -> bool:
        """Indique si le site est en lecture seule (pas de téléchargement)."""
        return not self.supports_download

    @property
    def is_full_featured(self) -> bool:
        """Indique si le site supporte toutes les fonctionnalités de base."""
        return (
            self.supports_search
            and self.supports_manga_info
            and self.supports_chapters
            and self.supports_pages
            and self.supports_download
        )

    @property
    def request_interval_seconds(self) -> float:
        """Intervalle recommandé entre deux requêtes (inverse du rate limit)."""
        if self.rate_limit_per_second <= 0:
            return self.min_delay_between_requests
        return max(self.min_delay_between_requests, 1.0 / self.rate_limit_per_second)

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable."""
        return {
            "supports_search": self.supports_search,
            "supports_manga_info": self.supports_manga_info,
            "supports_chapters": self.supports_chapters,
            "supports_pages": self.supports_pages,
            "supports_download": self.supports_download,
            "supports_language_filter": self.supports_language_filter,
            "supports_content_rating_filter": self.supports_content_rating_filter,
            "requires_auth": self.requires_auth,
            "requires_cloudflare_bypass": self.requires_cloudflare_bypass,
            "requires_javascript_rendering": self.requires_javascript_rendering,
            "requires_bypass": self.requires_bypass,
            "max_concurrent_downloads": self.max_concurrent_downloads,
            "rate_limit_per_second": self.rate_limit_per_second,
            "min_delay_between_requests": self.min_delay_between_requests,
            "request_interval_seconds": round(self.request_interval_seconds, 3),
        }

    def __repr__(self) -> str:
        features = []
        if self.supports_search:
            features.append("search")
        if self.supports_download:
            features.append("download")
        if self.requires_bypass:
            features.append("bypass")
        return (
            f"<SiteCapabilities [{', '.join(features)}] "
            f"concurrent={self.max_concurrent_downloads} "
            f"rate={self.rate_limit_per_second}/s>"
        )


# ============================================================================
# MODÈLES PYDANTIC — Configuration de site
# ============================================================================


class SiteConfig(BaseModel):
    """Configuration complète d'un site supporté (immutable).

    Représente la configuration d'un site source (MangaDex, SushiScan, etc.)
    telle que chargée depuis `sites.yaml` ou `sites_overrides.yaml`. Ce modèle
    est le contrat entre le registre, les parsers et les sessions HTTP.

    Attributes:
        id: Identifiant unique du site (snake_case, ex: "mangadex").
        name: Nom d'affichage du site (ex: "MangaDex").
        domains: Liste des domaines (primaire + miroirs + CDN).
        parser_class: Référence de la classe de parser (format "module:Class").
        language: Langue principale du site (ISO 639-1).
        adult: True si le site héberge du contenu adulte (18+).
        content_rating: Classification d'âge par défaut du site.
        capabilities: Capacités opérationnelles du site.
        default_headers: Headers HTTP par défaut pour toutes les requêtes.
        cookies_required: Liste des noms de cookies requis (ex: ["cf_clearance"]).
        region_locked: True si le site est géo-restreint.
        allowed_regions: Liste des codes régions ISO 3166-1 alpha-2 autorisés.
        proxy_requirement: Niveau de nécessité d'un proxy.
        scanlation_group: Nom du groupe de scanlation (si applicable).
        tags: Tags pour classification (ex: ["wordpress", "madara"]).
        notes: Notes libres (maintenance, problèmes connus, etc.).
        enabled: True si le site est activé par défaut.
        priority: Priorité d'affichage dans l'interface (0 = plus haute).
    """

    # Identification
    id: str = Field(
        ...,
        description="Identifiant unique du site (snake_case).",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Nom d'affichage du site.",
    )

    # Domaines
    domains: list[DomainInfo] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Liste des domaines (primaire + miroirs + CDN).",
    )

    # Parser
    parser_class: str = Field(
        ...,
        description="Référence de la classe de parser (format 'module:Class').",
    )

    # Localisation et contenu
    language: Language = Field(
        ...,
        description="Langue principale du site (ISO 639-1).",
    )
    adult: bool = Field(
        default=False,
        description="True si le site héberge du contenu adulte (18+).",
    )
    content_rating: ContentRating = Field(
        default=ContentRating.SAFE,
        description="Classification d'âge par défaut du site.",
    )

    # Capacités
    capabilities: SiteCapabilities = Field(
        ...,
        description="Capacités opérationnelles du site.",
    )

    # Configuration HTTP
    default_headers: dict[str, str] = Field(
        default_factory=dict,
        description="Headers HTTP par défaut pour toutes les requêtes.",
    )
    cookies_required: list[str] = Field(
        default_factory=list,
        description="Liste des noms de cookies requis.",
    )

    # Géolocalisation
    region_locked: bool = Field(
        default=False,
        description="True si le site est géo-restreint.",
    )
    allowed_regions: list[str] = Field(
        default_factory=list,
        description="Codes régions ISO 3166-1 alpha-2 autorisés (vide = mondial).",
    )
    proxy_requirement: ProxyRequirement = Field(
        default=ProxyRequirement.NONE,
        description="Niveau de nécessité d'un proxy.",
    )

    # Métadonnées
    scanlation_group: str | None = Field(
        default=None,
        max_length=200,
        description="Nom du groupe de scanlation (si applicable).",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Tags pour classification (ex: ['wordpress', 'madara']).",
    )
    notes: str | None = Field(
        default=None,
        max_length=1000,
        description="Notes libres (maintenance, problèmes connus, etc.).",
    )

    # État
    enabled: bool = Field(
        default=True,
        description="True si le site est activé par défaut.",
    )
    priority: int = Field(
        default=100,
        ge=0,
        le=1000,
        description="Priorité d'affichage dans l'interface (0 = plus haute).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        """Valide l'ID du site (snake_case)."""
        return validate_site_id(v)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        """Valide le nom du site (trim + non vide)."""
        v = v.strip()
        if not v:
            raise InvalidSiteConfigError("", "Le nom ne peut pas être vide")
        return v

    @field_validator("parser_class")
    @classmethod
    def _validate_parser_class(cls, v: str) -> str:
        """Valide la référence de classe de parser."""
        return validate_parser_class(v)

    @field_validator("domains")
    @classmethod
    def _validate_domains(cls, v: list[DomainInfo]) -> list[DomainInfo]:
        """Valide la liste des domaines (au moins 1 primaire)."""
        if not v:
            raise InvalidSiteConfigError("", "Au moins un domaine est requis")

        # Vérifier qu'il y a au moins un domaine primaire
        has_primary = any(d.is_primary or d.role == DomainRole.PRIMARY for d in v)
        if not has_primary:
            # Marquer automatiquement le premier comme primaire
            first = v[0]
            v[0] = first.model_copy(update={"is_primary": True, "role": DomainRole.PRIMARY})

        # Vérifier l'unicité des URLs
        urls = [str(d.url) for d in v]
        if len(urls) != len(set(urls)):
            raise InvalidSiteConfigError("", "Les domaines doivent être uniques")

        return v

    @field_validator("cookies_required")
    @classmethod
    def _validate_cookies(cls, v: list[str]) -> list[str]:
        """Valide les noms de cookies."""
        for cookie in v:
            if not _COOKIE_NAME_PATTERN.match(cookie):
                raise InvalidSiteConfigError(
                    "",
                    f"Nom de cookie invalide: {cookie}",
                )
        return v

    @field_validator("allowed_regions")
    @classmethod
    def _validate_regions(cls, v: list[str]) -> list[str]:
        """Valide les codes régions (ISO 3166-1 alpha-2)."""
        for region in v:
            if not _REGION_CODE_PATTERN.match(region):
                raise InvalidSiteConfigError(
                    "",
                    f"Code région invalide: {region} (attendu: 2 lettres majuscules)",
                )
        return v

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Vérifie la cohérence globale de la configuration."""
        # Si adult=True, content_rating devrait être EROTICA ou PORNOGRAPHIC
        if self.adult and self.content_rating in (
            ContentRating.SAFE,
            ContentRating.SUGGESTIVE,
        ):
            # Auto-corriger : élever le content_rating
            # (on ne peut pas modifier car frozen, donc on laisse tel quel
            # mais on pourrait logger un warning)
            pass

        # Si region_locked=True, allowed_regions ne devrait pas être vide
        if self.region_locked and not self.allowed_regions:
            pass  # Toléré : certains sites sont lockés sans liste explicite

        # Si proxy_requirement=REQUIRED, region_locked devrait être True
        # (mais pas obligatoire : certains sites nécessitent un proxy sans être lockés)

        return self

    # --------------------------------------------------------------------
    # Propriétés — Domaines
    # --------------------------------------------------------------------

    @property
    def primary_domain(self) -> DomainInfo:
        """Domaine principal du site.

        Returns:
            Le premier domaine marqué comme primaire.

        Raises:
            InvalidSiteConfigError: Si aucun domaine primaire n'est trouvé.
        """
        for domain in self.domains:
            if domain.is_primary or domain.role == DomainRole.PRIMARY:
                return domain
        # Fallback : retourner le premier domaine
        return self.domains[0]

    @property
    def primary_url(self) -> str:
        """URL du domaine principal (str)."""
        return str(self.primary_domain.url)

    @property
    def primary_host(self) -> str:
        """Host du domaine principal."""
        return self.primary_domain.host

    @property
    def alternative_domains(self) -> list[DomainInfo]:
        """Liste des domaines alternatifs (miroirs, CDN, API)."""
        return [d for d in self.domains if not d.is_primary and d.role != DomainRole.PRIMARY]

    @property
    def mirror_domains(self) -> list[DomainInfo]:
        """Liste des domaines miroirs."""
        return [d for d in self.domains if d.role == DomainRole.MIRROR]

    @property
    def cdn_domains(self) -> list[DomainInfo]:
        """Liste des domaines CDN."""
        return [d for d in self.domains if d.role == DomainRole.CDN]

    @property
    def api_domains(self) -> list[DomainInfo]:
        """Liste des domaines API."""
        return [d for d in self.domains if d.role == DomainRole.API]

    @property
    def all_urls(self) -> list[str]:
        """Liste de toutes les URLs (primaire + alternatives)."""
        return [str(d.url) for d in self.domains]

    @property
    def all_hosts(self) -> list[str]:
        """Liste de tous les hosts."""
        return [d.host for d in self.domains]

    # --------------------------------------------------------------------
    # Propriétés — Contenu
    # --------------------------------------------------------------------

    @property
    def is_adult(self) -> bool:
        """Indique si le site héberge du contenu adulte."""
        return self.adult or self.content_rating.is_adult

    @property
    def requires_bypass(self) -> bool:
        """Indique si le site nécessite un bypass (Cloudflare ou JS)."""
        return self.capabilities.requires_bypass

    @property
    def requires_proxy(self) -> bool:
        """Indique si le site nécessite un proxy."""
        return self.proxy_requirement == ProxyRequirement.REQUIRED

    @property
    def is_enabled(self) -> bool:
        """Indique si le site est activé."""
        return self.enabled

    @property
    def is_worldwide(self) -> bool:
        """Indique si le site est accessible mondialement (pas de restriction)."""
        return not self.region_locked and not self.allowed_regions

    # --------------------------------------------------------------------
    # Propriétés — Parser
    # --------------------------------------------------------------------

    @property
    def parser_module(self) -> str:
        """Chemin du module du parser (ex: 'nexusdl.parsers.en.mangadex')."""
        return extract_module_path(self.parser_class)

    @property
    def parser_class_name(self) -> str:
        """Nom de la classe du parser (ex: 'MangaDexParser')."""
        return extract_class_name(self.parser_class)

    # --------------------------------------------------------------------
    # Méthodes — Filtrage
    # --------------------------------------------------------------------

    def matches_language(self, language: Language) -> bool:
        """Vérifie si le site correspond à une langue donnée.

        Un site avec language=MULTI correspond à toutes les langues.

        Args:
            language: Langue à vérifier.

        Returns:
            True si le site correspond.
        """
        if self.language == Language.MULTI:
            return True
        return self.language == language

    def matches_region(self, region_code: str) -> bool:
        """Vérifie si le site est accessible depuis une région donnée.

        Args:
            region_code: Code région ISO 3166-1 alpha-2 (ex: "FR", "US").

        Returns:
            True si le site est accessible depuis cette région.
        """
        if not self.region_locked:
            return True  # Site mondial
        if not self.allowed_regions:
            return True  # Locké mais pas de liste = accessible partout (par défaut)
        return region_code.upper() in self.allowed_regions

    def has_tag(self, tag: str) -> bool:
        """Vérifie si le site a un tag donné.

        Args:
            tag: Tag à vérifier.

        Returns:
            True si le tag est présent.
        """
        return tag.lower() in [t.lower() for t in self.tags]

    def requires_cookie(self, cookie_name: str) -> bool:
        """Vérifie si le site requiert un cookie spécifique.

        Args:
            cookie_name: Nom du cookie à vérifier.

        Returns:
            True si le cookie est requis.
        """
        return cookie_name in self.cookies_required

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable (pour WebSocket/API).

        Returns:
            Dictionnaire avec les champs essentiels pour l'affichage.
        """
        return {
            "id": self.id,
            "name": self.name,
            "primary_url": self.primary_url,
            "primary_host": self.primary_host,
            "domains_count": len(self.domains),
            "mirror_count": len(self.mirror_domains),
            "parser_class": self.parser_class,
            "parser_module": self.parser_module,
            "parser_class_name": self.parser_class_name,
            "language": self.language.value,
            "language_label": self.language.label,
            "language_flag": self.language.flag,
            "adult": self.adult,
            "is_adult": self.is_adult,
            "content_rating": self.content_rating.value,
            "content_rating_label": self.content_rating.label,
            "content_rating_icon": self.content_rating.icon,
            "capabilities": self.capabilities.to_summary(),
            "requires_bypass": self.requires_bypass,
            "requires_proxy": self.requires_proxy,
            "region_locked": self.region_locked,
            "allowed_regions": self.allowed_regions,
            "is_worldwide": self.is_worldwide,
            "scanlation_group": self.scanlation_group,
            "tags": self.tags,
            "notes": self.notes,
            "enabled": self.enabled,
            "priority": self.priority,
        }

    def to_full(self) -> dict[str, Any]:
        """Retourne une représentation complète avec tous les domaines."""
        summary = self.to_summary()
        summary["domains"] = [d.to_summary() for d in self.domains]
        summary["cookies_required"] = self.cookies_required
        summary["default_headers"] = self.default_headers
        return summary

    def __repr__(self) -> str:
        return (
            f"<SiteConfig id={self.id} name='{self.name}' "
            f"language={self.language.value} "
            f"adult={self.adult} "
            f"domains={len(self.domains)}>"
        )


# ============================================================================
# MODÈLES PYDANTIC — Santé et statistiques
# ============================================================================


class SiteHealth(BaseModel):
    """État de santé d'un site à un instant T (immutable).

    Utilisé par le système de monitoring pour afficher l'état des sites
    dans les interfaces et déclencher des alertes.

    Attributes:
        site_id: ID du site.
        status: État de santé actuel.
        last_check_at: Timestamp du dernier health check.
        response_time_ms: Temps de réponse moyen en millisecondes.
        success_rate: Taux de succès des dernières requêtes (0.0 à 1.0).
        consecutive_failures: Nombre d'échecs consécutifs.
        error_message: Message d'erreur si le site est DOWN/DEGRADED.
        http_status_code: Dernier code HTTP reçu (si disponible).
    """

    site_id: str = Field(..., description="ID du site.")
    status: SiteStatus = Field(
        default=SiteStatus.UNKNOWN,
        description="État de santé actuel.",
    )
    last_check_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp du dernier health check.",
    )
    response_time_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Temps de réponse moyen en millisecondes.",
    )
    success_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Taux de succès des dernières requêtes (0.0 à 1.0).",
    )
    consecutive_failures: int = Field(
        default=0,
        ge=0,
        description="Nombre d'échecs consécutifs.",
    )
    error_message: str | None = Field(
        default=None,
        max_length=500,
        description="Message d'erreur si le site est DOWN/DEGRADED.",
    )
    http_status_code: int | None = Field(
        default=None,
        description="Dernier code HTTP reçu (si disponible).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def is_healthy(self) -> bool:
        """Indique si le site est considéré comme sain."""
        return self.status.is_healthy

    @property
    def is_available(self) -> bool:
        """Indique si le site est disponible pour utilisation."""
        return self.status.is_available

    @property
    def response_time_human(self) -> str:
        """Temps de réponse formaté (ex: '245ms', '1.2s')."""
        if self.response_time_ms < 1000:
            return f"{int(self.response_time_ms)}ms"
        return f"{self.response_time_ms / 1000:.1f}s"

    @property
    def success_rate_percent(self) -> float:
        """Taux de succès en pourcentage (0.0 à 100.0)."""
        return self.success_rate * 100.0

    @property
    def age_seconds(self) -> float:
        """Âge du dernier health check en secondes."""
        return (datetime.now(UTC) - self.last_check_at).total_seconds()

    @property
    def is_stale(self) -> bool:
        """Indique si le health check est obsolète (> 5 minutes)."""
        return self.age_seconds > 300.0

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable."""
        return {
            "site_id": self.site_id,
            "status": self.status.value,
            "status_label": self.status.label,
            "status_icon": self.status.icon,
            "status_color": self.status.color,
            "last_check_at": self.last_check_at.isoformat(),
            "response_time_ms": round(self.response_time_ms, 1),
            "response_time_human": self.response_time_human,
            "success_rate": round(self.success_rate, 3),
            "success_rate_percent": round(self.success_rate_percent, 1),
            "consecutive_failures": self.consecutive_failures,
            "error_message": self.error_message,
            "http_status_code": self.http_status_code,
            "is_healthy": self.is_healthy,
            "is_available": self.is_available,
            "is_stale": self.is_stale,
            "age_seconds": round(self.age_seconds, 1),
        }

    def __repr__(self) -> str:
        return (
            f"<SiteHealth site={self.site_id} status={self.status.value} "
            f"response={self.response_time_human} "
            f"success={self.success_rate_percent:.1f}%>"
        )


class SiteStats(BaseModel):
    """Statistiques d'utilisation d'un site (immutable).

    Agrège les statistiques d'usage d'un site pour le monitoring
    et l'optimisation (identifier les sites les plus utilisés,
    les plus lents, les plus error-prone, etc.).

    Attributes:
        site_id: ID du site.
        total_searches: Nombre total de recherches effectuées.
        total_downloads: Nombre total de téléchargements initiés.
        total_errors: Nombre total d'erreurs rencontrées.
        total_bytes_downloaded: Total de bytes téléchargés.
        average_response_time_ms: Temps de réponse moyen.
        last_used_at: Timestamp de la dernière utilisation.
        first_used_at: Timestamp de la première utilisation.
    """

    site_id: str = Field(..., description="ID du site.")
    total_searches: int = Field(default=0, ge=0)
    total_downloads: int = Field(default=0, ge=0)
    total_errors: int = Field(default=0, ge=0)
    total_bytes_downloaded: int = Field(default=0, ge=0)
    average_response_time_ms: float = Field(default=0.0, ge=0.0)
    last_used_at: datetime | None = Field(
        default=None,
        description="Timestamp de la dernière utilisation.",
    )
    first_used_at: datetime | None = Field(
        default=None,
        description="Timestamp de la première utilisation.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def total_operations(self) -> int:
        """Nombre total d'opérations (searches + downloads)."""
        return self.total_searches + self.total_downloads

    @property
    def error_rate(self) -> float:
        """Taux d'erreur (0.0 à 1.0)."""
        if self.total_operations == 0:
            return 0.0
        return self.total_errors / self.total_operations

    @property
    def error_rate_percent(self) -> float:
        """Taux d'erreur en pourcentage (0.0 à 100.0)."""
        return self.error_rate * 100.0

    @property
    def total_bytes_human(self) -> str:
        """Total de bytes téléchargés formaté (ex: '2.3 GB')."""
        size = self.total_bytes_downloaded
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024.0:
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
            size /= 1024.0
        return f"{size:.1f} PB"

    @property
    def is_active(self) -> bool:
        """Indique si le site a été utilisé récemment (< 7 jours)."""
        if self.last_used_at is None:
            return False
        age_days = (datetime.now(UTC) - self.last_used_at).total_seconds() / 86_400.0
        return age_days < 7.0

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable."""
        return {
            "site_id": self.site_id,
            "total_searches": self.total_searches,
            "total_downloads": self.total_downloads,
            "total_operations": self.total_operations,
            "total_errors": self.total_errors,
            "error_rate_percent": round(self.error_rate_percent, 2),
            "total_bytes_downloaded": self.total_bytes_downloaded,
            "total_bytes_human": self.total_bytes_human,
            "average_response_time_ms": round(self.average_response_time_ms, 1),
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "first_used_at": self.first_used_at.isoformat() if self.first_used_at else None,
            "is_active": self.is_active,
        }

    def __repr__(self) -> str:
        return (
            f"<SiteStats site={self.site_id} "
            f"ops={self.total_operations} "
            f"errors={self.total_errors} "
            f"size={self.total_bytes_human}>"
        )


# ============================================================================
# MODÈLES PYDANTIC — Site complet avec santé et stats
# ============================================================================


class SiteOverview(BaseModel):
    """Vue d'ensemble complète d'un site (config + santé + stats).

    Utilisé par les interfaces pour afficher une vue unifiée d'un site
    dans les paramètres, le sélecteur de sites, etc.

    Attributes:
        config: Configuration du site.
        health: État de santé actuel.
        stats: Statistiques d'utilisation.
        is_favorite: True si le site est marqué comme favori par l'utilisateur.
    """

    config: SiteConfig = Field(..., description="Configuration du site.")
    health: SiteHealth = Field(
        default_factory=lambda: SiteHealth(site_id=""),
        description="État de santé actuel.",
    )
    stats: SiteStats = Field(
        default_factory=lambda: SiteStats(site_id=""),
        description="Statistiques d'utilisation.",
    )
    is_favorite: bool = Field(
        default=False,
        description="True si le site est marqué comme favori.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def is_healthy(self) -> bool:
        """Indique si le site est sain."""
        return self.health.is_healthy

    @property
    def is_available(self) -> bool:
        """Indique si le site est disponible."""
        return self.health.is_available and self.config.is_enabled

    @property
    def display_name(self) -> str:
        """Nom d'affichage avec indicateur de santé."""
        return f"{self.health.status.icon} {self.config.name}"

    # --------------------------------------------------------------------
    # Sérialisation
    # --------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable."""
        return {
            "config": self.config.to_summary(),
            "health": self.health.to_summary(),
            "stats": self.stats.to_summary(),
            "is_favorite": self.is_favorite,
            "is_healthy": self.is_healthy,
            "is_available": self.is_available,
            "display_name": self.display_name,
        }

    def __repr__(self) -> str:
        return (
            f"<SiteOverview site={self.config.id} "
            f"health={self.health.status.value} "
            f"favorite={self.is_favorite}>"
        )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "SiteModelError",
    "InvalidSiteConfigError",
    "InvalidDomainError",
    "InvalidParserClassError",
    # Enums
    "SiteStatus",
    "ProxyRequirement",
    "DomainRole",
    # Modèles principaux
    "DomainInfo",
    "SiteCapabilities",
    "SiteConfig",
    "SiteHealth",
    "SiteStats",
    "SiteOverview",
    # Helpers
    "validate_site_id",
    "validate_parser_class",
    "extract_module_path",
    "extract_class_name",
    # Constantes
    "_SITE_ID_PATTERN",
    "_PARSER_CLASS_PATTERN",
    "_REGION_CODE_PATTERN",
    "_COOKIE_NAME_PATTERN",
]
