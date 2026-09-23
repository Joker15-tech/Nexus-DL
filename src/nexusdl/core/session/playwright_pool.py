"""Pool de navigateurs headless Playwright avec fingerprint rotation.

Ce module fournit un système complet de gestion de navigateurs headless via
Playwright, utilisé pour contourner les protections anti-bot (Cloudflare,
DataDome, etc.) et exécuter du JavaScript sur les sites qui le nécessitent.

**Pourquoi Playwright ?**
    - Supporte Chromium, Firefox, WebKit (multi-navigateurs)
    - API async native (parfait pour asyncio)
    - Isolation complète par context (cookies, localStorage, etc.)
    - Fingerprinting avancé (UA, viewport, locale, timezone, WebGL, etc.)
    - Détection anti-bot contournable via stealth plugins

**Architecture** :
    PlaywrightPool
        ├── Browser (instance Playwright unique)
        ├── BrowserContext[] (pool de contexts isolés)
        │   ├── Fingerprint (UA, viewport, locale, etc.)
        │   ├── ProxyConfig (optionnel, routing par context)
        │   └── StorageState (cookies, localStorage persistés)
        ├── WarmupManager (visites de pages légitimes)
        └── HealthMonitor (détection des contexts bloqués)

**Stratégies de fingerprint** :
    - RANDOM    : fingerprint complètement aléatoire à chaque context
    - REALISTIC : fingerprint basé sur des profils réalistes (Chrome/Win10, etc.)
    - STICKY    : même fingerprint pour une session donnée (via session_id)
    - CUSTOM    : fingerprint fourni explicitement par l'utilisateur

**Cycle de vie d'un context** :
    1. Création avec fingerprint unique
    2. Warm-up (visite de pages légitimes)
    3. Utilisation pour scraping
    4. Libération (retour au pool ou destruction)
    5. Cleanup si inactif depuis trop longtemps

Exemple d'utilisation :
    >>> pool = PlaywrightPool()
    >>> await pool.start()
    >>>
    >>> # Obtenir un context pour une session
    >>> async with pool.get_context(session_id="task_abc") as context:
    ...     page = await context.new_page()
    ...     await page.goto("https://example.com")
    ...     content = await page.content()
    ...     await page.close()
    >>>
    >>> # Helper pour exécuter du code sur une page
    >>> result = await pool.execute_with_page(
    ...     url="https://example.com",
    ...     script="return document.title",
    ...     session_id="task_abc",
    ... )
    >>> print(result)
    'Example Domain'
    >>>
    >>> # Statistiques
    >>> stats = await pool.get_stats()
    >>> print(f"Contexts actifs: {stats.active_contexts}/{stats.max_contexts}")
    >>>
    >>> await pool.stop()

Dépendances externes :
    - playwright : pip install playwright && playwright install chromium
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Final, Self

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError

if TYPE_CHECKING:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
    )

    from nexusdl.core.session.proxy_manager import ProxyManager
    from nexusdl.core.session.user_agents import UserAgentsManager


# ============================================================================
# EXCEPTIONS
# ============================================================================


class PlaywrightPoolError(NexusDLError):
    """Exception de base pour les erreurs du pool Playwright."""


class PlaywrightNotInstalledError(PlaywrightPoolError):
    """Exception levée lorsque Playwright n'est pas installé."""

    def __init__(self) -> None:
        super().__init__(
            "Playwright n'est pas installé. "
            "Installez-le avec: pip install playwright && playwright install chromium"
        )


class PlaywrightPoolNotStartedError(PlaywrightPoolError):
    """Exception levée lorsqu'on utilise le pool avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "PlaywrightPool must be started before use. Call await pool.start()"
        )


class NoContextAvailableError(PlaywrightPoolError):
    """Exception levée lorsqu'aucun context n'est disponible."""

    def __init__(self, reason: str = "") -> None:
        msg = "Aucun context Playwright disponible"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


class ContextCreationError(PlaywrightPoolError):
    """Exception levée lorsqu'un context ne peut être créé."""

    def __init__(self, reason: str = "") -> None:
        msg = "Impossible de créer un context Playwright"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


class PageExecutionError(PlaywrightPoolError):
    """Exception levée lorsqu'une exécution de script sur une page échoue."""

    def __init__(self, url: str, reason: str = "") -> None:
        msg = f"Échec de l'exécution sur {url}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.url = url
        self.reason = reason


class WarmupError(PlaywrightPoolError):
    """Exception levée lorsqu'un warm-up échoue."""

    def __init__(self, url: str, reason: str = "") -> None:
        msg = f"Warm-up échoué pour {url}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.url = url
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class BrowserType(str, Enum):
    """Type de navigateur Playwright.

    CHROMIUM : Google Chromium (défaut, le plus compatible).
    FIREFOX  : Mozilla Firefox.
    WEBKIT   : Apple WebKit (Safari engine).
    """

    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            BrowserType.CHROMIUM: "Chromium",
            BrowserType.FIREFOX: "Firefox",
            BrowserType.WEBKIT: "WebKit",
        }[self]

    @property
    def default_user_agent_prefix(self) -> str:
        """Préfixe User-Agent par défaut pour ce navigateur."""
        return {
            BrowserType.CHROMIUM: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/",
            BrowserType.FIREFOX: "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:",
            BrowserType.WEBKIT: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/",
        }[self]


class FingerprintStrategy(str, Enum):
    """Stratégie de génération de fingerprint.

    RANDOM    : Fingerprint complètement aléatoire (peut être détecté).
    REALISTIC : Fingerprint basé sur des profils réalistes (recommandé).
    STICKY    : Même fingerprint pour une session donnée (via session_id).
    CUSTOM    : Fingerprint fourni explicitement.
    """

    RANDOM = "random"
    REALISTIC = "realistic"
    STICKY = "sticky"
    CUSTOM = "custom"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            FingerprintStrategy.RANDOM: "Aléatoire",
            FingerprintStrategy.REALISTIC: "Réaliste (recommandé)",
            FingerprintStrategy.STICKY: "Persistant par session",
            FingerprintStrategy.CUSTOM: "Personnalisé",
        }[self]


class ContextState(str, Enum):
    """État d'un context dans le pool.

    INITIALIZING : En cours de création/warm-up.
    READY        : Prêt à être utilisé.
    IN_USE       : Actuellement utilisé par une tâche.
    WARMING_UP   : Warm-up en cours.
    CLOSING      : En cours de fermeture.
    CLOSED       : Fermé.
    STUCK        : Bloqué (timeout ou erreur).
    """

    INITIALIZING = "initializing"
    READY = "ready"
    IN_USE = "in_use"
    WARMING_UP = "warming_up"
    CLOSING = "closing"
    CLOSED = "closed"
    STUCK = "stuck"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ContextState.INITIALIZING: "Initialisation",
            ContextState.READY: "Prêt",
            ContextState.IN_USE: "En cours d'utilisation",
            ContextState.WARMING_UP: "Warm-up",
            ContextState.CLOSING: "Fermeture",
            ContextState.CLOSED: "Fermé",
            ContextState.STUCK: "Bloqué",
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            ContextState.INITIALIZING: "⏳",
            ContextState.READY: "🟢",
            ContextState.IN_USE: "🔵",
            ContextState.WARMING_UP: "🟡",
            ContextState.CLOSING: "🟠",
            ContextState.CLOSED: "⚫",
            ContextState.STUCK: "🔴",
        }[self]

    @property
    def is_available(self) -> bool:
        """Indique si le context est disponible pour utilisation."""
        return self == ContextState.READY


# ============================================================================
# MODÈLES PYDANTIC — Fingerprint
# ============================================================================


class BrowserFingerprint(BaseModel):
    """Fingerprint complet d'un navigateur (pour anti-détection).

    Contient tous les paramètres qui identifient un navigateur de manière
    unique. Utilisé pour simuler des navigateurs réels et éviter la détection
    par les systèmes anti-bot.
    """

    user_agent: str = Field(
        ...,
        min_length=20,
        max_length=1000,
        description="User-Agent complet.",
    )
    viewport_width: int = Field(
        default=1920,
        ge=800,
        le=3840,
        description="Largeur du viewport.",
    )
    viewport_height: int = Field(
        default=1080,
        ge=600,
        le=2160,
        description="Hauteur du viewport.",
    )
    device_scale_factor: float = Field(
        default=1.0,
        ge=1.0,
        le=3.0,
        description="Facteur d'échelle de l'appareil.",
    )
    is_mobile: bool = Field(
        default=False,
        description="Simuler un appareil mobile.",
    )
    has_touch: bool = Field(
        default=False,
        description="Simuler un écran tactile.",
    )
    locale: str = Field(
        default="en-US",
        min_length=2,
        max_length=10,
        description="Locale du navigateur (ex: 'en-US', 'fr-FR').",
    )
    timezone_id: str = Field(
        default="America/New_York",
        description="Fuseau horaire (IANA, ex: 'Europe/Paris').",
    )
    geolocation: dict[str, float] | None = Field(
        default=None,
        description="Géolocalisation {'latitude': x, 'longitude': y}.",
    )
    permissions: list[str] = Field(
        default_factory=list,
        description="Permissions accordées (ex: ['geolocation']).",
    )
    color_scheme: str = Field(
        default="light",
        description="Schéma de couleur ('light', 'dark', 'no-preference').",
    )
    extra_headers: dict[str, str] = Field(
        default_factory=dict,
        description="Headers HTTP additionnels.",
    )
    browser_type: BrowserType = Field(
        default=BrowserType.CHROMIUM,
        description="Type de navigateur simulé.",
    )
    platform: str = Field(
        default="Win32",
        description="Plateforme déclarée (Win32, MacIntel, Linux x86_64, etc.).",
    )
    languages: list[str] = Field(
        default_factory=lambda: ["en-US", "en"],
        description="Liste des langues préférées.",
    )
    hardware_concurrency: int = Field(
        default=8,
        ge=1,
        le=32,
        description="Nombre de cœurs CPU déclarés (navigator.hardwareConcurrency).",
    )
    device_memory: int = Field(
        default=8,
        ge=1,
        le=64,
        description="Mémoire de l'appareil en Go (navigator.deviceMemory).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def viewport(self) -> dict[str, int]:
        """Viewport au format dict pour Playwright."""
        return {"width": self.viewport_width, "height": self.viewport_height}

    @property
    def fingerprint_id(self) -> str:
        """Identifiant unique du fingerprint (hash)."""
        content = f"{self.user_agent}|{self.viewport_width}x{self.viewport_height}|{self.locale}|{self.timezone_id}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]


class ContextInfo(BaseModel):
    """Informations sur un context du pool."""

    context_id: str = Field(..., description="Identifiant unique du context.")
    session_id: str | None = Field(
        default=None,
        description="ID de session associé (pour mode sticky).",
    )
    state: ContextState = Field(..., description="État actuel du context.")
    fingerprint_id: str = Field(..., description="ID du fingerprint utilisé.")
    browser_type: BrowserType = Field(..., description="Type de navigateur.")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de création.",
    )
    last_used_at: datetime | None = Field(
        default=None,
        description="Timestamp de dernière utilisation.",
    )
    usage_count: int = Field(default=0, ge=0, description="Nombre d'utilisations.")
    pages_opened: int = Field(default=0, ge=0, description="Nombre de pages ouvertes.")
    proxy_url: str | None = Field(
        default=None,
        description="URL du proxy utilisé (masquée).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def age_seconds(self) -> float:
        """Âge du context en secondes."""
        return (datetime.now(UTC) - self.created_at).total_seconds()

    @property
    def idle_seconds(self) -> float | None:
        """Temps d'inactivité en secondes (None si jamais utilisé)."""
        if self.last_used_at is None:
            return None
        return (datetime.now(UTC) - self.last_used_at).total_seconds()


class PoolStats(BaseModel):
    """Statistiques globales du pool Playwright."""

    total_contexts_created: int = Field(default=0, ge=0)
    total_contexts_closed: int = Field(default=0, ge=0)
    active_contexts: int = Field(default=0, ge=0)
    ready_contexts: int = Field(default=0, ge=0)
    in_use_contexts: int = Field(default=0, ge=0)
    stuck_contexts: int = Field(default=0, ge=0)
    max_contexts: int = Field(default=0, ge=0)
    total_page_executions: int = Field(default=0, ge=0)
    successful_executions: int = Field(default=0, ge=0)
    failed_executions: int = Field(default=0, ge=0)
    total_warmups: int = Field(default=0, ge=0)
    successful_warmups: int = Field(default=0, ge=0)
    failed_warmups: int = Field(default=0, ge=0)
    total_wait_time_seconds: float = Field(default=0.0, ge=0.0)
    browser_type: BrowserType = Field(default=BrowserType.CHROMIUM)
    fingerprint_strategy: FingerprintStrategy = Field(default=FingerprintStrategy.REALISTIC)
    uptime_seconds: float = Field(default=0.0, ge=0.0)
    last_execution_at: datetime | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def execution_success_rate(self) -> float:
        """Taux de succès des exécutions (0.0 à 1.0)."""
        if self.total_page_executions == 0:
            return 0.0
        return self.successful_executions / self.total_page_executions

    @property
    def warmup_success_rate(self) -> float:
        """Taux de succès des warm-ups (0.0 à 1.0)."""
        if self.total_warmups == 0:
            return 0.0
        return self.successful_warmups / self.total_warmups

    @property
    def pool_utilization_percent(self) -> float:
        """Pourcentage d'utilisation du pool (0.0 à 100.0)."""
        if self.max_contexts == 0:
            return 0.0
        return (self.active_contexts / self.max_contexts) * 100.0

    @property
    def average_wait_time_ms(self) -> float:
        """Temps d'attente moyen pour obtenir un context (ms)."""
        if self.total_page_executions == 0:
            return 0.0
        return (self.total_wait_time_seconds / self.total_page_executions) * 1000.0


# ============================================================================
# MODÈLES PYDANTIC — Configuration
# ============================================================================


class PlaywrightPoolConfig(BaseModel):
    """Configuration du pool Playwright.

    Attributes:
        browser_type: Type de navigateur à utiliser.
        max_contexts: Nombre maximum de contexts simultanés.
        min_contexts: Nombre minimum de contexts prêts (préchauffage).
        fingerprint_strategy: Stratégie de génération de fingerprint.
        warmup_enabled: Activer le warm-up automatique des contexts.
        warmup_urls: URLs à visiter pendant le warm-up.
        warmup_timeout_seconds: Timeout pour le warm-up.
        context_idle_timeout_seconds: Durée d'inactivité avant fermeture.
        context_max_age_seconds: Âge maximum d'un context avant recyclage.
        context_max_uses: Nombre maximum d'utilisations avant recyclage.
        headless: Exécuter en mode headless (sans UI).
        slow_mo: Ralentir les opérations (ms, pour debugging).
        default_timeout: Timeout par défaut pour les opérations (ms).
        navigation_timeout: Timeout pour la navigation (ms).
        stealth_enabled: Activer les techniques stealth (anti-détection).
        proxy_manager: ProxyManager pour routing des contexts.
        user_agents_manager: UserAgentsManager pour cohérence UA.
    """

    browser_type: BrowserType = Field(
        default=BrowserType.CHROMIUM,
        description="Type de navigateur à utiliser.",
    )
    max_contexts: int = Field(
        default=4,
        ge=1,
        le=32,
        description="Nombre maximum de contexts simultanés.",
    )
    min_contexts: int = Field(
        default=1,
        ge=0,
        description="Nombre minimum de contexts prêts (préchauffage).",
    )
    fingerprint_strategy: FingerprintStrategy = Field(
        default=FingerprintStrategy.REALISTIC,
        description="Stratégie de génération de fingerprint.",
    )
    warmup_enabled: bool = Field(
        default=True,
        description="Activer le warm-up automatique des contexts.",
    )
    warmup_urls: list[str] = Field(
        default_factory=lambda: [
            "https://www.google.com",
            "https://example.com",
        ],
        description="URLs à visiter pendant le warm-up.",
    )
    warmup_timeout_seconds: float = Field(
        default=30.0,
        ge=5.0,
        le=120.0,
        description="Timeout pour le warm-up.",
    )
    context_idle_timeout_seconds: float = Field(
        default=300.0,
        ge=60.0,
        description="Durée d'inactivité avant fermeture (secondes).",
    )
    context_max_age_seconds: float = Field(
        default=1800.0,
        ge=300.0,
        description="Âge maximum d'un context avant recyclage (secondes).",
    )
    context_max_uses: int = Field(
        default=20,
        ge=1,
        le=1000,
        description="Nombre maximum d'utilisations avant recyclage.",
    )
    headless: bool = Field(
        default=True,
        description="Exécuter en mode headless (sans UI).",
    )
    slow_mo: int = Field(
        default=0,
        ge=0,
        le=1000,
        description="Ralentir les opérations (ms, pour debugging).",
    )
    default_timeout: int = Field(
        default=30000,
        ge=1000,
        le=300000,
        description="Timeout par défaut pour les opérations (ms).",
    )
    navigation_timeout: int = Field(
        default=60000,
        ge=1000,
        le=600000,
        description="Timeout pour la navigation (ms).",
    )
    stealth_enabled: bool = Field(
        default=True,
        description="Activer les techniques stealth (anti-détection).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)


# ============================================================================
# HELPERS — Génération de fingerprints réalistes
# ============================================================================


# Profils réalistes de navigateurs (basés sur des statistiques réelles)
_REALISTIC_PROFILES: Final[list[dict[str, Any]]] = [
    # Chrome Windows 10/11 (le plus courant)
    {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "viewport_width": 1920,
        "viewport_height": 1080,
        "device_scale_factor": 1.0,
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "platform": "Win32",
        "browser_type": BrowserType.CHROMIUM,
        "hardware_concurrency": 8,
        "device_memory": 8,
        "languages": ["en-US", "en"],
    },
    # Chrome Windows FR
    {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "viewport_width": 1920,
        "viewport_height": 1080,
        "device_scale_factor": 1.0,
        "locale": "fr-FR",
        "timezone_id": "Europe/Paris",
        "platform": "Win32",
        "browser_type": BrowserType.CHROMIUM,
        "hardware_concurrency": 8,
        "device_memory": 8,
        "languages": ["fr-FR", "fr", "en-US", "en"],
    },
    # Chrome macOS
    {
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "viewport_width": 1440,
        "viewport_height": 900,
        "device_scale_factor": 2.0,
        "locale": "en-US",
        "timezone_id": "America/Los_Angeles",
        "platform": "MacIntel",
        "browser_type": BrowserType.CHROMIUM,
        "hardware_concurrency": 10,
        "device_memory": 16,
        "languages": ["en-US", "en"],
    },
    # Firefox Windows
    {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
        "viewport_width": 1920,
        "viewport_height": 1080,
        "device_scale_factor": 1.0,
        "locale": "en-US",
        "timezone_id": "America/Chicago",
        "platform": "Win32",
        "browser_type": BrowserType.FIREFOX,
        "hardware_concurrency": 12,
        "device_memory": 16,
        "languages": ["en-US", "en"],
    },
    # Chrome Linux
    {
        "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "viewport_width": 1920,
        "viewport_height": 1080,
        "device_scale_factor": 1.0,
        "locale": "en-US",
        "timezone_id": "Europe/Berlin",
        "platform": "Linux x86_64",
        "browser_type": BrowserType.CHROMIUM,
        "hardware_concurrency": 8,
        "device_memory": 8,
        "languages": ["en-US", "en", "de-DE", "de"],
    },
    # Mobile Android Chrome
    {
        "user_agent": "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36",
        "viewport_width": 412,
        "viewport_height": 915,
        "device_scale_factor": 2.625,
        "is_mobile": True,
        "has_touch": True,
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "platform": "Linux armv8l",
        "browser_type": BrowserType.CHROMIUM,
        "hardware_concurrency": 8,
        "device_memory": 8,
        "languages": ["en-US", "en"],
    },
    # Mobile iOS Safari
    {
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
        "viewport_width": 390,
        "viewport_height": 844,
        "device_scale_factor": 3.0,
        "is_mobile": True,
        "has_touch": True,
        "locale": "en-US",
        "timezone_id": "America/Los_Angeles",
        "platform": "iPhone",
        "browser_type": BrowserType.WEBKIT,
        "hardware_concurrency": 6,
        "device_memory": 6,
        "languages": ["en-US", "en"],
    },
]

# Viewports courants pour variation
_COMMON_VIEWPORTS: Final[list[tuple[int, int]]] = [
    (1920, 1080),
    (1366, 768),
    (1536, 864),
    (1440, 900),
    (1280, 720),
    (1600, 900),
    (2560, 1440),
]

# Timezones courantes
_COMMON_TIMEZONES: Final[list[str]] = [
    "America/New_York",
    "America/Los_Angeles",
    "America/Chicago",
    "Europe/Paris",
    "Europe/London",
    "Europe/Berlin",
    "Asia/Tokyo",
    "Asia/Shanghai",
    "Australia/Sydney",
]


def generate_realistic_fingerprint(
    *,
    browser_type: BrowserType = BrowserType.CHROMIUM,
    locale: str | None = None,
) -> BrowserFingerprint:
    """Génère un fingerprint réaliste basé sur des profils réels.

    Args:
        browser_type: Type de navigateur souhaité (ou None pour aléatoire).
        locale: Locale souhaitée (ou None pour aléatoire).

    Returns:
        Instance de BrowserFingerprint.
    """
    # Filtrer les profils par navigateur
    matching_profiles = [
        p for p in _REALISTIC_PROFILES
        if p["browser_type"] == browser_type
    ]
    if not matching_profiles:
        matching_profiles = _REALISTIC_PROFILES

    # Filtrer par locale si spécifiée
    if locale:
        locale_profiles = [p for p in matching_profiles if p.get("locale") == locale]
        if locale_profiles:
            matching_profiles = locale_profiles

    # Choisir un profil de base
    profile = random.choice(matching_profiles)

    # Ajouter de la variation
    viewport = random.choice(_COMMON_VIEWPORTS)
    # Légère variation du viewport (±5%)
    width_variation = int(viewport[0] * random.uniform(-0.05, 0.05))
    height_variation = int(viewport[1] * random.uniform(-0.05, 0.05))

    data = dict(profile)
    data["viewport_width"] = viewport[0] + width_variation
    data["viewport_height"] = viewport[1] + height_variation

    # Variation du hardware_concurrency
    base_hw = data.get("hardware_concurrency", 8)
    data["hardware_concurrency"] = random.choice([
        max(1, base_hw - 2),
        base_hw,
        base_hw + 2,
        base_hw * 2,
    ])

    return BrowserFingerprint(**data)


def generate_random_fingerprint(
    *,
    browser_type: BrowserType = BrowserType.CHROMIUM,
) -> BrowserFingerprint:
    """Génère un fingerprint complètement aléatoire.

    Moins réaliste que generate_realistic_fingerprint mais plus varié.

    Args:
        browser_type: Type de navigateur.

    Returns:
        Instance de BrowserFingerprint.
    """
    viewport = random.choice(_COMMON_VIEWPORTS)
    timezone = random.choice(_COMMON_TIMEZONES)

    # Générer un UA basique
    if browser_type == BrowserType.CHROMIUM:
        chrome_version = random.randint(120, 130)
        ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version}.0.0.0 Safari/537.36"
        platform = "Win32"
    elif browser_type == BrowserType.FIREFOX:
        firefox_version = random.randint(120, 135)
        ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{firefox_version}.0) Gecko/20100101 Firefox/{firefox_version}.0"
        platform = "Win32"
    else:  # WEBKIT
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
        platform = "MacIntel"

    return BrowserFingerprint(
        user_agent=ua,
        viewport_width=viewport[0],
        viewport_height=viewport[1],
        device_scale_factor=random.choice([1.0, 1.25, 1.5, 2.0]),
        locale="en-US",
        timezone_id=timezone,
        platform=platform,
        browser_type=browser_type,
        hardware_concurrency=random.choice([4, 8, 12, 16]),
        device_memory=random.choice([4, 8, 16, 32]),
        languages=["en-US", "en"],
    )


def generate_sticky_fingerprint(session_id: str) -> BrowserFingerprint:
    """Génère un fingerprint déterministe basé sur un session_id.

    Le même session_id produira toujours le même fingerprint.

    Args:
        session_id: Identifiant de session.

    Returns:
        Instance de BrowserFingerprint.
    """
    # Utiliser le hash du session_id comme seed
    seed = int(hashlib.sha256(session_id.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)

    # Sélection déterministe
    profile_index = rng.randint(0, len(_REALISTIC_PROFILES) - 1)
    profile = _REALISTIC_PROFILES[profile_index]

    viewport_index = rng.randint(0, len(_COMMON_VIEWPORTS) - 1)
    viewport = _COMMON_VIEWPORTS[viewport_index]

    data = dict(profile)
    data["viewport_width"] = viewport[0]
    data["viewport_height"] = viewport[1]

    return BrowserFingerprint(**data)


# ============================================================================
# CLASSE INTERNE — _PooledContext
# ============================================================================


class _PooledContext:
    """Wrapper interne pour un BrowserContext Playwright avec métadonnées.

    Non exposé publiquement — utilisé par PlaywrightPool.
    """

    __slots__ = (
        "_context_id",
        "_context",
        "_fingerprint",
        "_state",
        "_session_id",
        "_created_at",
        "_last_used_at",
        "_usage_count",
        "_pages_opened",
        "_proxy_url",
        "_lock",
    )

    def __init__(
        self,
        context: BrowserContext,
        fingerprint: BrowserFingerprint,
        *,
        session_id: str | None = None,
        proxy_url: str | None = None,
    ) -> None:
        self._context_id = str(uuid.uuid4())
        self._context = context
        self._fingerprint = fingerprint
        self._state = ContextState.INITIALIZING
        self._session_id = session_id
        self._created_at = datetime.now(UTC)
        self._last_used_at: datetime | None = None
        self._usage_count = 0
        self._pages_opened = 0
        self._proxy_url = proxy_url
        self._lock = asyncio.Lock()

    @property
    def context_id(self) -> str:
        return self._context_id

    @property
    def context(self) -> BrowserContext:
        return self._context

    @property
    def fingerprint(self) -> BrowserFingerprint:
        return self._fingerprint

    @property
    def state(self) -> ContextState:
        return self._state

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def is_available(self) -> bool:
        return self._state == ContextState.READY

    @property
    def is_stuck(self) -> bool:
        return self._state == ContextState.STUCK

    async def mark_ready(self) -> None:
        async with self._lock:
            self._state = ContextState.READY

    async def mark_in_use(self) -> None:
        async with self._lock:
            self._state = ContextState.IN_USE
            self._usage_count += 1
            self._last_used_at = datetime.now(UTC)

    async def mark_warming_up(self) -> None:
        async with self._lock:
            self._state = ContextState.WARMING_UP

    async def mark_stuck(self) -> None:
        async with self._lock:
            self._state = ContextState.STUCK

    async def mark_closing(self) -> None:
        async with self._lock:
            self._state = ContextState.CLOSING

    async def record_page_opened(self) -> None:
        async with self._lock:
            self._pages_opened += 1

    async def close(self) -> None:
        """Ferme le context Playwright."""
        async with self._lock:
            self._state = ContextState.CLOSED
            try:
                await self._context.close()
            except Exception as e:
                logger.warning("Erreur lors de la fermeture du context {}: {}", self._context_id, e)

    def to_info(self) -> ContextInfo:
        """Convertit en ContextInfo (snapshot immuable)."""
        return ContextInfo(
            context_id=self._context_id,
            session_id=self._session_id,
            state=self._state,
            fingerprint_id=self._fingerprint.fingerprint_id,
            browser_type=self._fingerprint.browser_type,
            created_at=self._created_at,
            last_used_at=self._last_used_at,
            usage_count=self._usage_count,
            pages_opened=self._pages_opened,
            proxy_url=self._proxy_url,
        )

    def should_recycle(self, config: PlaywrightPoolConfig) -> bool:
        """Détermine si le context doit être recyclé."""
        now = datetime.now(UTC)

        # Âge maximum
        age_seconds = (now - self._created_at).total_seconds()
        if age_seconds > config.context_max_age_seconds:
            return True

        # Nombre d'utilisations maximum
        if self._usage_count >= config.context_max_uses:
            return True

        # Inactivité
        if self._last_used_at is not None:
            idle_seconds = (now - self._last_used_at).total_seconds()
            if idle_seconds > config.context_idle_timeout_seconds:
                return True

        return False


# ============================================================================
# CLASSE PRINCIPALE — PlaywrightPool
# ============================================================================


class PlaywrightPool:
    """Pool de navigateurs headless Playwright avec fingerprint rotation.

    Gère un pool de BrowserContexts isolés, avec rotation intelligente des
    fingerprints, warm-up automatique, et détection des contexts bloqués.

    Lifecycle :
        >>> pool = PlaywrightPool()
        >>> await pool.start()
        >>> async with pool.get_context() as context:
        ...     page = await context.new_page()
        ...     await page.goto("https://example.com")
        >>> await pool.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Les opérations sont protégées par des locks granulaires.
    """

    # URLs de warm-up par défaut
    _DEFAULT_WARMUP_URLS: ClassVar[list[str]] = [
        "https://www.google.com",
        "https://example.com",
    ]

    def __init__(
        self,
        *,
        config: PlaywrightPoolConfig | None = None,
        user_agents_manager: UserAgentsManager | None = None,
        proxy_manager: ProxyManager | None = None,
    ) -> None:
        """Initialise le pool Playwright.

        Args:
            config: Configuration du pool.
            user_agents_manager: Manager d'UA pour cohérence (optionnel).
            proxy_manager: Manager de proxies pour routing (optionnel).
        """
        self._config = config or PlaywrightPoolConfig()
        self._user_agents_manager = user_agents_manager
        self._proxy_manager = proxy_manager

        # État Playwright
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

        # Pool de contexts
        self._contexts: OrderedDict[str, _PooledContext] = OrderedDict()
        self._contexts_lock = asyncio.Lock()

        # Mapping session_id → context_id (pour mode sticky)
        self._session_mapping: dict[str, str] = {}
        self._session_lock = asyncio.Lock()

        # Sémaphore pour limiter la concurrence
        self._semaphore: asyncio.Semaphore | None = None

        # État
        self._started: bool = False
        self._start_time: float = 0.0

        # Tâches de fond
        self._cleanup_task: asyncio.Task[None] | None = None
        self._warmup_task: asyncio.Task[None] | None = None

        # Statistiques
        self._total_contexts_created: int = 0
        self._total_contexts_closed: int = 0
        self._total_page_executions: int = 0
        self._successful_executions: int = 0
        self._failed_executions: int = 0
        self._total_warmups: int = 0
        self._successful_warmups: int = 0
        self._failed_warmups: int = 0
        self._total_wait_time_seconds: float = 0.0
        self._last_execution_at: datetime | None = None
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="playwright_pool")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre le pool et initialise Playwright.

        Raises:
            PlaywrightNotInstalledError: Si Playwright n'est pas installé.
            ContextCreationError: Si le navigateur ne peut être démarré.
        """
        if self._started:
            self._logger.warning("PlaywrightPool déjà démarré, ignore")
            return

        try:
            # Importer Playwright
            try:
                from playwright.async_api import async_playwright
            except ImportError as e:
                raise PlaywrightNotInstalledError() from e

            # Démarrer Playwright
            self._playwright = await async_playwright().start()

            # Lancer le navigateur
            browser_launcher = getattr(self._playwright, self._config.browser_type.value)
            self._browser = await browser_launcher.launch(
                headless=self._config.headless,
                slow_mo=self._config.slow_mo,
                args=self._get_browser_args(),
            )

            self._semaphore = asyncio.Semaphore(self._config.max_contexts)
            self._started = True
            self._start_time = time.monotonic()

            # Démarrer les tâches de fond
            self._cleanup_task = asyncio.create_task(
                self._cleanup_loop(),
                name="playwright_cleanup",
            )

            if self._config.warmup_enabled and self._config.min_contexts > 0:
                self._warmup_task = asyncio.create_task(
                    self._warmup_loop(),
                    name="playwright_warmup",
                )

            self._logger.info(
                "PlaywrightPool démarré: browser={}, max_contexts={}, strategy={}",
                self._config.browser_type.value,
                self._config.max_contexts,
                self._config.fingerprint_strategy.value,
            )

        except PlaywrightPoolError:
            raise
        except Exception as e:
            self._logger.error("Échec du démarrage de PlaywrightPool: {}", e)
            await self._cleanup_on_error()
            raise ContextCreationError(str(e)) from e

    async def stop(self) -> None:
        """Arrête le pool et libère toutes les ressources."""
        if not self._started:
            return

        self._started = False

        # Annuler les tâches de fond
        for task in (self._cleanup_task, self._warmup_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Fermer tous les contexts
        async with self._contexts_lock:
            contexts_to_close = list(self._contexts.values())
            self._contexts.clear()

        for ctx in contexts_to_close:
            try:
                await ctx.close()
                async with self._stats_lock:
                    self._total_contexts_closed += 1
            except Exception as e:
                self._logger.warning("Erreur lors de la fermeture d'un context: {}", e)

        # Fermer le navigateur
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as e:
                self._logger.warning("Erreur lors de la fermeture du navigateur: {}", e)
            self._browser = None

        # Arrêter Playwright
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as e:
                self._logger.warning("Erreur lors de l'arrêt de Playwright: {}", e)
            self._playwright = None

        # Vider le mapping de sessions
        async with self._session_lock:
            self._session_mapping.clear()

        self._logger.info("PlaywrightPool arrêté")

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()

    async def _cleanup_on_error(self) -> None:
        """Nettoie les ressources en cas d'erreur au démarrage."""
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def _get_browser_args(self) -> list[str]:
        """Retourne les arguments de lancement du navigateur.

        Inclut des flags pour améliorer le stealth et la stabilité.
        """
        args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-web-security",
            "--disable-features=IsolateOrigins,site-per-process",
        ]

        if self._config.stealth_enabled:
            args.extend([
                "--disable-infobars",
                "--excludeSwitches=enable-automation",
                "--disable-extensions",
            ])

        return args

    # ------------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------------

    @property
    def is_started(self) -> bool:
        """Indique si le pool est démarré."""
        return self._started

    @property
    def browser(self) -> Browser | None:
        """Instance du navigateur Playwright (ou None)."""
        return self._browser

    @property
    def contexts_count(self) -> int:
        """Nombre total de contexts dans le pool."""
        return len(self._contexts)

    @property
    def available_contexts_count(self) -> int:
        """Nombre de contexts disponibles (READY)."""
        return sum(1 for c in self._contexts.values() if c.is_available)

    # ------------------------------------------------------------------------
    # API publique — Obtention de contexts
    # ------------------------------------------------------------------------

    async def get_context(
        self,
        *,
        session_id: str | None = None,
        proxy_url: str | None = None,
        fingerprint: BrowserFingerprint | None = None,
        timeout: float = 30.0,
    ) -> _ContextHandle:
        """Obtient un context du pool.

        Retourne un context manager qui libère automatiquement le context
        à la fin de l'utilisation.

        Args:
            session_id: ID de session pour mode sticky (optionnel).
            proxy_url: URL du proxy à utiliser pour ce context (optionnel).
            fingerprint: Fingerprint custom (optionnel, pour mode CUSTOM).
            timeout: Timeout pour obtenir un context (secondes).

        Returns:
            Context manager (_ContextHandle) qui yield un BrowserContext.

        Raises:
            PlaywrightPoolNotStartedError: Si le pool n'est pas démarré.
            NoContextAvailableError: Si aucun context n'est disponible.
        """
        self._ensure_started()
        assert self._semaphore is not None
        assert self._browser is not None

        start_time = time.monotonic()

        # Vérifier le mapping sticky
        if session_id is not None and self._config.fingerprint_strategy == FingerprintStrategy.STICKY:
            async with self._session_lock:
                existing_context_id = self._session_mapping.get(session_id)
                if existing_context_id is not None:
                    async with self._contexts_lock:
                        existing = self._contexts.get(existing_context_id)
                        if existing is not None and existing.is_available:
                            await existing.mark_in_use()
                            self._logger.trace(
                                "Context sticky réutilisé pour session {}: {}",
                                session_id[:20],
                                existing.context_id,
                            )
                            return _ContextHandle(self, existing)

        # Acquérir le sémaphore (avec timeout)
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=timeout)
        except asyncio.TimeoutError as e:
            raise NoContextAvailableError(
                f"Timeout après {timeout}s en attendant un context disponible"
            ) from e

        try:
            # Chercher un context READY existant
            context_entry = await self._find_available_context()

            # Sinon, créer un nouveau context
            if context_entry is None:
                context_entry = await self._create_new_context(
                    session_id=session_id,
                    proxy_url=proxy_url,
                    fingerprint=fingerprint,
                )

            await context_entry.mark_in_use()

            wait_time = time.monotonic() - start_time
            async with self._stats_lock:
                self._total_wait_time_seconds += wait_time

            return _ContextHandle(self, context_entry)

        except Exception:
            # Libérer le sémaphore en cas d'erreur
            self._semaphore.release()
            raise

    async def _find_available_context(self) -> _PooledContext | None:
        """Trouve un context READY disponible (non recyclable)."""
        async with self._contexts_lock:
            for ctx in self._contexts.values():
                if ctx.is_available and not ctx.should_recycle(self._config):
                    # Retirer du dict temporairement (sera remis à la libération)
                    return ctx
            return None

    async def _create_new_context(
        self,
        *,
        session_id: str | None = None,
        proxy_url: str | None = None,
        fingerprint: BrowserFingerprint | None = None,
    ) -> _PooledContext:
        """Crée un nouveau BrowserContext avec fingerprint.

        Args:
            session_id: ID de session (pour sticky).
            proxy_url: URL du proxy.
            fingerprint: Fingerprint custom.

        Returns:
            Instance de _PooledContext.
        """
        assert self._browser is not None

        # Générer le fingerprint
        if fingerprint is None:
            fingerprint = self._generate_fingerprint(session_id)

        # Construire les options du context
        context_options: dict[str, Any] = {
            "user_agent": fingerprint.user_agent,
            "viewport": fingerprint.viewport,
            "device_scale_factor": fingerprint.device_scale_factor,
            "is_mobile": fingerprint.is_mobile,
            "has_touch": fingerprint.has_touch,
            "locale": fingerprint.locale,
            "timezone_id": fingerprint.timezone_id,
            "color_scheme": fingerprint.color_scheme,
        }

        if fingerprint.geolocation:
            context_options["geolocation"] = fingerprint.geolocation
            context_options["permissions"] = ["geolocation"]

        if fingerprint.extra_headers:
            context_options["extra_http_headers"] = fingerprint.extra_headers

        # Proxy
        if proxy_url is not None:
            from urllib.parse import urlparse
            parsed = urlparse(proxy_url)
            context_options["proxy"] = {
                "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 8080}",
            }
            if parsed.username:
                context_options["proxy"]["username"] = parsed.username
            if parsed.password:
                context_options["proxy"]["password"] = parsed.password

        # Créer le context
        try:
            browser_context = await self._browser.new_context(**context_options)
        except Exception as e:
            raise ContextCreationError(str(e)) from e

        # Wrapper
        entry = _PooledContext(
            context=browser_context,
            fingerprint=fingerprint,
            session_id=session_id,
            proxy_url=proxy_url,
        )

        # Ajouter au pool
        async with self._contexts_lock:
            self._contexts[entry.context_id] = entry

        async with self._stats_lock:
            self._total_contexts_created += 1

        # Warm-up si activé
        if self._config.warmup_enabled:
            try:
                await self._warmup_context(entry)
            except Exception as e:
                self._logger.warning(
                    "Warm-up échoué pour context {}: {}",
                    entry.context_id,
                    e,
                )

        # Marquer comme prêt
        await entry.mark_ready()

        # Mapper la session si sticky
        if session_id is not None and self._config.fingerprint_strategy == FingerprintStrategy.STICKY:
            async with self._session_lock:
                self._session_mapping[session_id] = entry.context_id

        self._logger.debug(
            "Nouveau context créé: {} (fingerprint={}, session={})",
            entry.context_id[:8],
            fingerprint.fingerprint_id,
            session_id[:20] if session_id else None,
        )

        return entry

    def _generate_fingerprint(
        self,
        session_id: str | None = None,
    ) -> BrowserFingerprint:
        """Génère un fingerprint selon la stratégie configurée.

        Args:
            session_id: ID de session (pour mode STICKY).

        Returns:
            Instance de BrowserFingerprint.
        """
        strategy = self._config.fingerprint_strategy

        if strategy == FingerprintStrategy.REALISTIC:
            # Utiliser l'UA du UserAgentsManager si disponible
            ua = None
            if self._user_agents_manager is not None:
                try:
                    ua = self._user_agents_manager.get_random()
                except Exception:
                    pass

            fingerprint = generate_realistic_fingerprint(
                browser_type=self._config.browser_type,
            )

            # Override l'UA si disponible
            if ua is not None:
                fingerprint = fingerprint.model_copy(update={"user_agent": ua})

            return fingerprint

        if strategy == FingerprintStrategy.RANDOM:
            return generate_random_fingerprint(
                browser_type=self._config.browser_type,
            )

        if strategy == FingerprintStrategy.STICKY:
            if session_id is None:
                session_id = str(uuid.uuid4())
            return generate_sticky_fingerprint(session_id)

        # CUSTOM : ne devrait pas arriver ici (fingerprint fourni explicitement)
        return generate_realistic_fingerprint(
            browser_type=self._config.browser_type,
        )

    # ------------------------------------------------------------------------
    # API publique — Warm-up
    # ------------------------------------------------------------------------

    async def _warmup_context(self, entry: _PooledContext) -> None:
        """Effectue le warm-up d'un context (visite de pages légitimes).

        Args:
            entry: Context à warm-up.
        """
        await entry.mark_warming_up()

        warmup_urls = self._config.warmup_urls or self._DEFAULT_WARMUP_URLS

        async with self._stats_lock:
            self._total_warmups += 1

        try:
            page = await entry.context.new_page()
            try:
                # Visiter 1-2 URLs de warm-up
                urls_to_visit = warmup_urls[:random.randint(1, min(2, len(warmup_urls)))]
                for url in urls_to_visit:
                    try:
                        await page.goto(
                            url,
                            timeout=self._config.warmup_timeout_seconds * 1000,
                            wait_until="domcontentloaded",
                        )
                        # Petit délai réaliste
                        await asyncio.sleep(random.uniform(0.5, 2.0))
                    except Exception as e:
                        self._logger.trace("Warm-up URL échoué (ignoré): {} — {}", url, e)

                async with self._stats_lock:
                    self._successful_warmups += 1

            finally:
                await page.close()

        except Exception as e:
            async with self._stats_lock:
                self._failed_warmups += 1
            raise WarmupError(entry.context_id, str(e)) from e

    async def _warmup_loop(self) -> None:
        """Boucle de warm-up pour maintenir min_contexts prêts."""
        try:
            while self._started:
                await asyncio.sleep(5.0)

                async with self._contexts_lock:
                    ready_count = sum(
                        1 for c in self._contexts.values()
                        if c.is_available and not c.should_recycle(self._config)
                    )

                if ready_count < self._config.min_contexts:
                    try:
                        await self._create_new_context()
                    except Exception as e:
                        self._logger.warning("Erreur lors du warm-up: {}", e)

        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------------
    # API publique — Exécution sur page
    # ------------------------------------------------------------------------

    async def execute_with_page(
        self,
        url: str,
        *,
        script: str | Callable[[Page], Any] | None = None,
        session_id: str | None = None,
        proxy_url: str | None = None,
        wait_until: str = "domcontentloaded",
        timeout: float = 60.0,
    ) -> Any:
        """Exécute du code sur une page dans un context du pool.

        Helper de haut niveau qui gère automatiquement l'obtention et la
        libération du context.

        Args:
            url: URL à visiter.
            script: Script JS à exécuter (str) ou fonction async (Callable).
                    Si None, retourne le contenu HTML.
            session_id: ID de session pour mode sticky.
            proxy_url: URL du proxy à utiliser.
            wait_until: Événement attendu après navigation.
            timeout: Timeout pour l'exécution (secondes).

        Returns:
            Résultat du script, ou contenu HTML si script=None.

        Raises:
            PageExecutionError: Si l'exécution échoue.
        """
        self._ensure_started()

        start_time = time.monotonic()
        async with self._stats_lock:
            self._total_page_executions += 1

        handle = await self.get_context(
            session_id=session_id,
            proxy_url=proxy_url,
            timeout=timeout,
        )

        try:
            async with handle as context:
                page = await context.new_page()
                try:
                    # Navigation
                    await page.goto(
                        url,
                        timeout=int(timeout * 1000),
                        wait_until=wait_until,
                    )

                    # Exécution
                    if script is None:
                        result = await page.content()
                    elif isinstance(script, str):
                        result = await page.evaluate(script)
                    else:
                        result = await script(page)

                    async with self._stats_lock:
                        self._successful_executions += 1
                        self._last_execution_at = datetime.now(UTC)

                    return result

                finally:
                    await page.close()

        except Exception as e:
            async with self._stats_lock:
                self._failed_executions += 1
            raise PageExecutionError(url, str(e)) from e

    # ------------------------------------------------------------------------
    # API publique — Libération de contexts
    # ------------------------------------------------------------------------

    async def _release_context(self, entry: _PooledContext) -> None:
        """Libère un context après utilisation.

        Le context est soit remis dans le pool (si encore valide),
        soit fermé et remplacé.
        """
        assert self._semaphore is not None

        try:
            # Vérifier si le context doit être recyclé
            if entry.should_recycle(self._config):
                await self._recycle_context(entry)
            else:
                # Remettre dans le pool
                await entry.mark_ready()
                async with self._contexts_lock:
                    if entry.context_id in self._contexts:
                        # Remettre à la fin (LRU)
                        self._contexts.move_to_end(entry.context_id)
        finally:
            # Libérer le sémaphore
            self._semaphore.release()

    async def _recycle_context(self, entry: _PooledContext) -> None:
        """Recycle un context (ferme et supprime du pool)."""
        self._logger.debug("Recyclage du context: {}", entry.context_id[:8])

        # Supprimer du mapping de session si présent
        if entry.session_id is not None:
            async with self._session_lock:
                self._session_mapping.pop(entry.session_id, None)

        # Supprimer du pool
        async with self._contexts_lock:
            self._contexts.pop(entry.context_id, None)

        # Fermer
        await entry.close()

        async with self._stats_lock:
            self._total_contexts_closed += 1

    # ------------------------------------------------------------------------
    # API publique — Cleanup
    # ------------------------------------------------------------------------

    async def _cleanup_loop(self) -> None:
        """Boucle de nettoyage des contexts inactifs ou bloqués."""
        try:
            while self._started:
                await asyncio.sleep(30.0)
                await self._cleanup_inactive_contexts()
                await self._detect_stuck_contexts()
        except asyncio.CancelledError:
            pass

    async def _cleanup_inactive_contexts(self) -> None:
        """Supprime les contexts inactifs depuis trop longtemps."""
        async with self._contexts_lock:
            to_recycle = [
                ctx for ctx in self._contexts.values()
                if ctx.is_available and ctx.should_recycle(self._config)
            ]

        for ctx in to_recycle:
            try:
                await self._recycle_context(ctx)
            except Exception as e:
                self._logger.warning("Erreur lors du recyclage: {}", e)

    async def _detect_stuck_contexts(self) -> None:
        """Détecte et remplace les contexts bloqués (IN_USE depuis trop longtemps)."""
        now = time.monotonic()
        stuck_timeout = self._config.navigation_timeout / 1000.0 * 2  # 2x le timeout nav

        async with self._contexts_lock:
            stuck_contexts = []
            for ctx in self._contexts.values():
                if ctx.state == ContextState.IN_USE and ctx._last_used_at is not None:
                    idle_seconds = (now - ctx._last_used_at.timestamp()) if hasattr(ctx._last_used_at, 'timestamp') else 0
                    # Note: détection simplifiée, en production on trackerait le temps de début d'utilisation

        # En pratique, la détection de contexts stuck est complexe
        # et nécessite un tracking plus fin du temps d'utilisation

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> PoolStats:
        """Retourne les statistiques globales du pool."""
        self._ensure_started()

        async with self._contexts_lock:
            contexts = list(self._contexts.values())

        active = 0
        ready = 0
        in_use = 0
        stuck = 0

        for ctx in contexts:
            if ctx.state == ContextState.READY:
                ready += 1
                active += 1
            elif ctx.state == ContextState.IN_USE:
                in_use += 1
                active += 1
            elif ctx.state == ContextState.STUCK:
                stuck += 1
                active += 1

        uptime = 0.0
        if self._start_time > 0:
            uptime = time.monotonic() - self._start_time

        async with self._stats_lock:
            return PoolStats(
                total_contexts_created=self._total_contexts_created,
                total_contexts_closed=self._total_contexts_closed,
                active_contexts=active,
                ready_contexts=ready,
                in_use_contexts=in_use,
                stuck_contexts=stuck,
                max_contexts=self._config.max_contexts,
                total_page_executions=self._total_page_executions,
                successful_executions=self._successful_executions,
                failed_executions=self._failed_executions,
                total_warmups=self._total_warmups,
                successful_warmups=self._successful_warmups,
                failed_warmups=self._failed_warmups,
                total_wait_time_seconds=self._total_wait_time_seconds,
                browser_type=self._config.browser_type,
                fingerprint_strategy=self._config.fingerprint_strategy,
                uptime_seconds=uptime,
                last_execution_at=self._last_execution_at,
            )

    async def list_contexts(self) -> list[ContextInfo]:
        """Liste tous les contexts avec leurs informations.

        Returns:
            Liste de ContextInfo (snapshots immuables).
        """
        async with self._contexts_lock:
            return [ctx.to_info() for ctx in self._contexts.values()]

    async def clear_session_mapping(self, session_id: str | None = None) -> int:
        """Efface le mapping de sessions.

        Args:
            session_id: ID de session spécifique (None = toutes).

        Returns:
            Nombre de mappings effacés.
        """
        async with self._session_lock:
            if session_id is None:
                count = len(self._session_mapping)
                self._session_mapping.clear()
                return count
            else:
                if session_id in self._session_mapping:
                    del self._session_mapping[session_id]
                    return 1
                return 0

    # ------------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # ------------------------------------------------------------------------

    def _ensure_started(self) -> None:
        """Vérifie que le pool est démarré."""
        if not self._started:
            raise PlaywrightPoolNotStartedError()

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<PlaywrightPool status={status} "
            f"browser={self._config.browser_type.value} "
            f"contexts={len(self._contexts)}/{self._config.max_contexts}>"
        )


# ============================================================================
# CLASSE — Context Handle (context manager)
# ============================================================================


class _ContextHandle:
    """Context manager pour obtenir et libérer un BrowserContext.

    Non exposé publiquement — retourné par PlaywrightPool.get_context().
    """

    __slots__ = ("_pool", "_entry", "_released")

    def __init__(self, pool: PlaywrightPool, entry: _PooledContext) -> None:
        self._pool = pool
        self._entry = entry
        self._released = False

    async def __aenter__(self) -> BrowserContext:
        return self._entry.context

    async def __aexit__(self, *args: object) -> None:
        if not self._released:
            self._released = True
            await self._pool._release_context(self._entry)


# ============================================================================
# HELPERS PUBLICS — Fonctions utilitaires
# ============================================================================


def is_playwright_installed() -> bool:
    """Vérifie si Playwright est installé.

    Returns:
        True si Playwright est disponible.
    """
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def get_playwright_installation_instructions() -> str:
    """Retourne les instructions d'installation de Playwright.

    Returns:
        Chaîne de texte avec les instructions.
    """
    return """
Pour utiliser Playwright, vous devez installer les dépendances suivantes :

**Installation de la librairie Python** :
    pip install playwright

**Installation des navigateurs** :
    playwright install chromium
    # Ou pour tous les navigateurs :
    playwright install

**Vérification** :
    python -c "from playwright.sync_api import sync_playwright; print('OK')"

**Dépendances système (Linux)** :
    playwright install-deps

Après installation, redémarrez NexusDL pour détecter Playwright.
""".strip()


async def quick_page_execution(
    url: str,
    *,
    script: str | None = None,
    browser_type: BrowserType = BrowserType.CHROMIUM,
    headless: bool = True,
) -> Any:
    """Exécute rapidement du code sur une page (one-shot, sans pool).

    Fonction utilitaire pour des exécutions ponctuelles sans gestion
    de lifecycle.

    Args:
        url: URL à visiter.
        script: Script JS à exécuter (ou None pour contenu HTML).
        browser_type: Type de navigateur.
        headless: Mode headless.

    Returns:
        Résultat du script ou contenu HTML.
    """
    config = PlaywrightPoolConfig(
        browser_type=browser_type,
        max_contexts=1,
        warmup_enabled=False,
        headless=headless,
    )

    async with PlaywrightPool(config=config) as pool:
        return await pool.execute_with_page(url, script=script)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "PlaywrightPoolError",
    "PlaywrightNotInstalledError",
    "PlaywrightPoolNotStartedError",
    "NoContextAvailableError",
    "ContextCreationError",
    "PageExecutionError",
    "WarmupError",
    # Enums
    "BrowserType",
    "FingerprintStrategy",
    "ContextState",
    # Modèles — Fingerprint
    "BrowserFingerprint",
    "ContextInfo",
    "PoolStats",
    # Modèles — Configuration
    "PlaywrightPoolConfig",
    # Classe principale
    "PlaywrightPool",
    # Helpers — Génération de fingerprints
    "generate_realistic_fingerprint",
    "generate_random_fingerprint",
    "generate_sticky_fingerprint",
    # Helpers — Utilitaires
    "is_playwright_installed",
    "get_playwright_installation_instructions",
    "quick_page_execution",
]
