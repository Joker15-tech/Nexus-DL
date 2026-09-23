"""Gestionnaire de rotation des User-Agents pour éviter le fingerprinting.

Ce module fournit un système de rotation intelligent des User-Agents HTTP
pour éviter la détection par les sites protégés (Cloudflare, DataDome, etc.).
Il charge une banque d'User-Agents réels et à jour depuis le fichier embarqué
`data/user_agents.txt` et offre plusieurs stratégies de rotation :

    - **Random** : sélection aléatoire uniforme parmi tous les UA
    - **Category-based** : rotation dans une catégorie spécifique (ex: Desktop Chrome)
    - **Platform-based** : rotation par OS (Windows, macOS, Linux, Android, iOS)
    - **Browser-based** : rotation par navigateur (Chrome, Firefox, Safari, etc.)
    - **Sticky** : conservation du même UA pour une session (via session_id)
    - **Weighted** : rotation pondérée (certains UA plus fréquents que d'autres)

Fonctionnalités principales :
    - Chargement lazy depuis `data/user_agents.txt` (via `importlib.resources`)
    - Parsing par catégories (12 catégories : Desktop Chrome, Firefox, Safari, etc.)
    - Rotation aléatoire avec cache par catégorie
    - Filtrage par plateforme (OS) et navigateur
    - Mode sticky pour sessions persistantes (hash de session_id → UA)
    - Exclusion d'UA blacklistés (via config)
    - Statistiques d'utilisation (compteur par UA)
    - Détection automatique de la plateforme courante
    - Thread-safe (locks asyncio)
    - Support du rechargement à chaud (hot-reload)

Architecture :
    UserAgentsManager
        ├── UserAgentCategory (enum) : 12 catégories de navigateurs
        ├── UserAgentPlatform (enum) : 5 plateformes (OS)
        ├── UserAgentBrowser (enum) : 8 navigateurs
        ├── UserAgentEntry (Pydantic) : UA individuel avec métadonnées
        ├── UserAgentsStats (Pydantic) : statistiques agrégées
        └── _UserAgentsCache (interne) : cache par catégorie

Exemple d'utilisation :
    >>> manager = UserAgentsManager()
    >>> await manager.start()
    >>>
    >>> # Rotation aléatoire
    >>> ua = manager.get_random()
    >>> print(ua)
    Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...
    >>>
    >>> # Rotation par catégorie
    >>> ua = manager.get_random_by_category(UserAgentCategory.DESKTOP_CHROME)
    >>>
    >>> # Rotation par plateforme
    >>> ua = manager.get_random_by_platform(UserAgentPlatform.WINDOWS)
    >>>
    >>> # Mode sticky (même UA pour une session)
    >>> ua = manager.get_sticky(session_id="task_abc123")
    >>> ua2 = manager.get_sticky(session_id="task_abc123")
    >>> assert ua == ua2  # Même UA retourné
    >>>
    >>> # Filtrage combiné
    >>> ua = manager.get_random_filtered(
    ...     platform=UserAgentPlatform.WINDOWS,
    ...     browser=UserAgentBrowser.CHROME,
    ... )
    >>>
    >>> # Statistiques
    >>> stats = manager.get_stats()
    >>> print(f"Total UA: {stats.total_user_agents}")
    >>> print(f"Rotations: {stats.total_rotations}")
    >>>
    >>> await manager.stop()
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.resources
import random
import re
from collections import defaultdict
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Final, Self

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# EXCEPTIONS
# ============================================================================


class UserAgentsError(NexusDLError):
    """Exception de base pour les erreurs du gestionnaire d'UA."""


class UserAgentsNotStartedError(UserAgentsError):
    """Exception levée lorsqu'on utilise le manager avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "UserAgentsManager must be started before use. Call await manager.start()"
        )


class UserAgentsLoadError(UserAgentsError):
    """Exception levée lorsque le fichier d'UA ne peut être chargé."""

    def __init__(self, reason: str = "") -> None:
        msg = "Impossible de charger la banque de User-Agents"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


class NoUserAgentAvailableError(UserAgentsError):
    """Exception levée lorsqu'aucun UA ne correspond aux filtres."""

    def __init__(
        self,
        platform: UserAgentPlatform | None = None,
        browser: UserAgentBrowser | None = None,
        category: UserAgentCategory | None = None,
    ) -> None:
        filters = []
        if platform is not None:
            filters.append(f"platform={platform.value}")
        if browser is not None:
            filters.append(f"browser={browser.value}")
        if category is not None:
            filters.append(f"category={category.value}")
        filter_str = ", ".join(filters) if filters else "aucun"
        super().__init__(f"Aucun User-Agent disponible pour les filtres: {filter_str}")
        self.platform = platform
        self.browser = browser
        self.category = category


# ============================================================================
# ENUMS
# ============================================================================


class UserAgentCategory(str, Enum):
    """Catégorie de User-Agent (correspond aux sections du fichier user_agents.txt).

    Les catégories reflètent la structure du fichier source et permettent
    une rotation ciblée selon le type de navigateur/plateforme souhaité.
    """

    # Desktop
    DESKTOP_CHROME = "desktop_chrome"
    DESKTOP_FIREFOX = "desktop_firefox"
    DESKTOP_SAFARI = "desktop_safari"
    DESKTOP_EDGE = "desktop_edge"
    DESKTOP_OPERA = "desktop_opera"
    DESKTOP_BRAVE = "desktop_brave"

    # Mobile
    MOBILE_ANDROID = "mobile_android"
    MOBILE_IOS = "mobile_ios"

    # Tablettes
    TABLET = "tablet"

    # Crawlers (pour sites qui les whitelistent)
    CRAWLERS = "crawlers"

    @property
    def label(self) -> str:
        """Libellé humain de la catégorie."""
        return {
            UserAgentCategory.DESKTOP_CHROME: "Desktop Chrome",
            UserAgentCategory.DESKTOP_FIREFOX: "Desktop Firefox",
            UserAgentCategory.DESKTOP_SAFARI: "Desktop Safari",
            UserAgentCategory.DESKTOP_EDGE: "Desktop Edge",
            UserAgentCategory.DESKTOP_OPERA: "Desktop Opera",
            UserAgentCategory.DESKTOP_BRAVE: "Desktop Brave",
            UserAgentCategory.MOBILE_ANDROID: "Mobile Android",
            UserAgentCategory.MOBILE_IOS: "Mobile iOS",
            UserAgentCategory.TABLET: "Tablette",
            UserAgentCategory.CRAWLERS: "Crawlers",
        }[self]

    @property
    def platform(self) -> UserAgentPlatform | None:
        """Plateforme associée à cette catégorie (ou None si multiple)."""
        mapping = {
            UserAgentCategory.DESKTOP_CHROME: UserAgentPlatform.DESKTOP,
            UserAgentCategory.DESKTOP_FIREFOX: UserAgentPlatform.DESKTOP,
            UserAgentCategory.DESKTOP_SAFARI: UserAgentPlatform.DESKTOP,
            UserAgentCategory.DESKTOP_EDGE: UserAgentPlatform.DESKTOP,
            UserAgentCategory.DESKTOP_OPERA: UserAgentPlatform.DESKTOP,
            UserAgentCategory.DESKTOP_BRAVE: UserAgentPlatform.DESKTOP,
            UserAgentCategory.MOBILE_ANDROID: UserAgentPlatform.ANDROID,
            UserAgentCategory.MOBILE_IOS: UserAgentPlatform.IOS,
            UserAgentCategory.TABLET: UserAgentPlatform.TABLET,
            UserAgentCategory.CRAWLERS: None,
        }
        return mapping.get(self)

    @property
    def browser(self) -> UserAgentBrowser | None:
        """Navigateur associé à cette catégorie (ou None si multiple)."""
        mapping = {
            UserAgentCategory.DESKTOP_CHROME: UserAgentBrowser.CHROME,
            UserAgentCategory.DESKTOP_FIREFOX: UserAgentBrowser.FIREFOX,
            UserAgentCategory.DESKTOP_SAFARI: UserAgentBrowser.SAFARI,
            UserAgentCategory.DESKTOP_EDGE: UserAgentBrowser.EDGE,
            UserAgentCategory.DESKTOP_OPERA: UserAgentBrowser.OPERA,
            UserAgentCategory.DESKTOP_BRAVE: UserAgentBrowser.BRAVE,
            UserAgentCategory.MOBILE_ANDROID: UserAgentBrowser.CHROME,
            UserAgentCategory.MOBILE_IOS: UserAgentBrowser.SAFARI,
            UserAgentCategory.TABLET: None,
            UserAgentCategory.CRAWLERS: UserAgentBrowser.CRAWLER,
        }
        return mapping.get(self)


class UserAgentPlatform(str, Enum):
    """Plateforme (OS) du User-Agent.

    Utilisé pour filtrer les UA par système d'exploitation.
    """

    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"
    ANDROID = "android"
    IOS = "ios"
    DESKTOP = "desktop"  # Any desktop (Windows, macOS, Linux)
    MOBILE = "mobile"  # Any mobile (Android, iOS)
    TABLET = "tablet"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            UserAgentPlatform.WINDOWS: "Windows",
            UserAgentPlatform.MACOS: "macOS",
            UserAgentPlatform.LINUX: "Linux",
            UserAgentPlatform.ANDROID: "Android",
            UserAgentPlatform.IOS: "iOS",
            UserAgentPlatform.DESKTOP: "Desktop (tout)",
            UserAgentPlatform.MOBILE: "Mobile (tout)",
            UserAgentPlatform.TABLET: "Tablette",
        }[self]

    @property
    def is_desktop(self) -> bool:
        """Indique si c'est une plateforme desktop."""
        return self in (
            UserAgentPlatform.WINDOWS,
            UserAgentPlatform.MACOS,
            UserAgentPlatform.LINUX,
            UserAgentPlatform.DESKTOP,
        )

    @property
    def is_mobile(self) -> bool:
        """Indique si c'est une plateforme mobile."""
        return self in (
            UserAgentPlatform.ANDROID,
            UserAgentPlatform.IOS,
            UserAgentPlatform.MOBILE,
        )


class UserAgentBrowser(str, Enum):
    """Navigateur du User-Agent.

    Utilisé pour filtrer les UA par type de navigateur.
    """

    CHROME = "chrome"
    FIREFOX = "firefox"
    SAFARI = "safari"
    EDGE = "edge"
    OPERA = "opera"
    BRAVE = "brave"
    SAMSUNG = "samsung"
    CRAWLER = "crawler"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            UserAgentBrowser.CHROME: "Chrome",
            UserAgentBrowser.FIREFOX: "Firefox",
            UserAgentBrowser.SAFARI: "Safari",
            UserAgentBrowser.EDGE: "Edge",
            UserAgentBrowser.OPERA: "Opera",
            UserAgentBrowser.BRAVE: "Brave",
            UserAgentBrowser.SAMSUNG: "Samsung Internet",
            UserAgentBrowser.CRAWLER: "Crawler",
        }[self]


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class UserAgentEntry(BaseModel):
    """Entrée individuelle de User-Agent avec métadonnées.

    Représente un User-Agent unique avec sa catégorie, sa plateforme,
    son navigateur, et des métadonnées pour le filtrage.
    """

    user_agent: str = Field(
        ...,
        min_length=20,
        max_length=1000,
        description="Chaîne User-Agent complète.",
    )
    category: UserAgentCategory = Field(
        ...,
        description="Catégorie de l'UA.",
    )
    platform: UserAgentPlatform | None = Field(
        default=None,
        description="Plateforme détectée (auto-détectée depuis la chaîne).",
    )
    browser: UserAgentBrowser | None = Field(
        default=None,
        description="Navigateur détecté (auto-détecté depuis la chaîne).",
    )
    weight: float = Field(
        default=1.0,
        gt=0.0,
        le=10.0,
        description="Poids pour la rotation pondérée (1.0 = normal).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def is_desktop(self) -> bool:
        """Indique si c'est un UA desktop."""
        return self.platform is not None and self.platform.is_desktop

    @property
    def is_mobile(self) -> bool:
        """Indique si c'est un UA mobile."""
        return self.platform is not None and self.platform.is_mobile

    def matches_platform(self, platform: UserAgentPlatform) -> bool:
        """Vérifie si cet UA correspond à la plateforme demandée."""
        if self.platform is None:
            return False

        # Cas spéciaux : DESKTOP/MOBILE englobent plusieurs plateformes
        if platform == UserAgentPlatform.DESKTOP:
            return self.platform in (
                UserAgentPlatform.WINDOWS,
                UserAgentPlatform.MACOS,
                UserAgentPlatform.LINUX,
            )
        if platform == UserAgentPlatform.MOBILE:
            return self.platform in (
                UserAgentPlatform.ANDROID,
                UserAgentPlatform.IOS,
            )

        return self.platform == platform

    def matches_browser(self, browser: UserAgentBrowser) -> bool:
        """Vérifie si cet UA correspond au navigateur demandé."""
        return self.browser == browser


class UserAgentsStats(BaseModel):
    """Statistiques agrégées du gestionnaire d'UA."""

    total_user_agents: int = Field(default=0, ge=0)
    total_rotations: int = Field(default=0, ge=0)
    rotations_by_category: dict[str, int] = Field(
        default_factory=dict,
        description="Nombre de rotations par catégorie.",
    )
    rotations_by_platform: dict[str, int] = Field(
        default_factory=dict,
        description="Nombre de rotations par plateforme.",
    )
    sticky_sessions_count: int = Field(
        default=0,
        ge=0,
        description="Nombre de sessions sticky actives.",
    )
    cache_hits: int = Field(default=0, ge=0)
    cache_misses: int = Field(default=0, ge=0)
    last_rotation_at: datetime | None = None
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def average_rotations_per_second(self) -> float:
        """Nombre moyen de rotations par seconde."""
        if self.uptime_seconds <= 0:
            return 0.0
        return self.total_rotations / self.uptime_seconds


# ============================================================================
# HELPERS — Détection de plateforme/navigateur
# ============================================================================


# Patterns regex pour détecter la plateforme depuis la chaîne UA
_PLATFORM_PATTERNS: Final[dict[UserAgentPlatform, re.Pattern[str]]] = {
    UserAgentPlatform.WINDOWS: re.compile(r"Windows NT \d+\.\d+", re.IGNORECASE),
    UserAgentPlatform.MACOS: re.compile(r"Macintosh|Mac OS X", re.IGNORECASE),
    UserAgentPlatform.LINUX: re.compile(r"X11; Linux|Ubuntu|Fedora", re.IGNORECASE),
    UserAgentPlatform.ANDROID: re.compile(r"Android \d+", re.IGNORECASE),
    UserAgentPlatform.IOS: re.compile(r"iPhone|iPad|iPod", re.IGNORECASE),
}

# Patterns regex pour détecter le navigateur
_BROWSER_PATTERNS: Final[dict[UserAgentBrowser, re.Pattern[str]]] = {
    UserAgentBrowser.EDGE: re.compile(r"Edg(?:e)?/[\d.]+", re.IGNORECASE),
    UserAgentBrowser.OPERA: re.compile(r"OPR/[\d.]+|Opera/[\d.]+", re.IGNORECASE),
    UserAgentBrowser.BRAVE: re.compile(r"Brave/[\d.]+", re.IGNORECASE),
    UserAgentBrowser.SAMSUNG: re.compile(r"SamsungBrowser/[\d.]+", re.IGNORECASE),
    UserAgentBrowser.FIREFOX: re.compile(r"Firefox/[\d.]+|FxiOS/[\d.]+", re.IGNORECASE),
    UserAgentBrowser.CHROME: re.compile(r"Chrome/[\d.]+|CriOS/[\d.]+", re.IGNORECASE),
    UserAgentBrowser.SAFARI: re.compile(r"Safari/[\d.]+(?!.*Chrome)", re.IGNORECASE),
    UserAgentBrowser.CRAWLER: re.compile(r"Googlebot|bingbot|YandexBot|DuckDuckBot", re.IGNORECASE),
}


def detect_platform(user_agent: str) -> UserAgentPlatform | None:
    """Détecte la plateforme depuis une chaîne User-Agent.

    Args:
        user_agent: Chaîne User-Agent à analyser.

    Returns:
        Plateforme détectée ou None si non détectable.
    """
    # Ordre important : iOS avant Android (car certains UA Android mentionnent iOS)
    # Edge avant Chrome (car Edge contient "Chrome")
    # Opera/Brave/Samsung avant Chrome (car ils contiennent aussi "Chrome")

    # Tablettes (doit être testé avant mobile)
    if re.search(r"iPad|Tablet|SM-X\d+|Pixel Tablet", user_agent, re.IGNORECASE):
        return UserAgentPlatform.TABLET

    # iOS
    if _PLATFORM_PATTERNS[UserAgentPlatform.IOS].search(user_agent):
        # Distinguer iPhone vs iPad
        if "iPad" in user_agent:
            return UserAgentPlatform.TABLET
        return UserAgentPlatform.IOS

    # Android
    if _PLATFORM_PATTERNS[UserAgentPlatform.ANDROID].search(user_agent):
        # Certains tablettes Android n'ont pas "Mobile" dans l'UA
        if "Mobile" not in user_agent:
            return UserAgentPlatform.TABLET
        return UserAgentPlatform.ANDROID

    # Desktop
    if _PLATFORM_PATTERNS[UserAgentPlatform.WINDOWS].search(user_agent):
        return UserAgentPlatform.WINDOWS
    if _PLATFORM_PATTERNS[UserAgentPlatform.MACOS].search(user_agent):
        return UserAgentPlatform.MACOS
    if _PLATFORM_PATTERNS[UserAgentPlatform.LINUX].search(user_agent):
        return UserAgentPlatform.LINUX

    return None


def detect_browser(user_agent: str) -> UserAgentBrowser | None:
    """Détecte le navigateur depuis une chaîne User-Agent.

    L'ordre de détection est important car certains UA contiennent
    plusieurs signatures (ex: Edge contient "Chrome").

    Args:
        user_agent: Chaîne User-Agent à analyser.

    Returns:
        Navigateur détecté ou None si non détectable.
    """
    # Ordre de priorité : les plus spécifiques d'abord
    for browser, pattern in _BROWSER_PATTERNS.items():
        if pattern.search(user_agent):
            return browser
    return None


def detect_category(user_agent: str) -> UserAgentCategory:
    """Détecte la catégorie depuis une chaîne User-Agent.

    Combine la détection de plateforme et de navigateur pour déterminer
    la catégorie la plus appropriée.

    Args:
        user_agent: Chaîne User-Agent à analyser.

    Returns:
        Catégorie détectée (défaut: DESKTOP_CHROME si non détectable).
    """
    platform = detect_platform(user_agent)
    browser = detect_browser(user_agent)

    # Crawlers
    if browser == UserAgentBrowser.CRAWLER:
        return UserAgentCategory.CRAWLERS

    # Tablettes
    if platform == UserAgentPlatform.TABLET:
        return UserAgentCategory.TABLET

    # Mobile
    if platform == UserAgentPlatform.ANDROID:
        return UserAgentCategory.MOBILE_ANDROID
    if platform == UserAgentPlatform.IOS:
        return UserAgentCategory.MOBILE_IOS

    # Desktop
    if platform is not None and platform.is_desktop:
        browser_to_category = {
            UserAgentBrowser.CHROME: UserAgentCategory.DESKTOP_CHROME,
            UserAgentBrowser.FIREFOX: UserAgentCategory.DESKTOP_FIREFOX,
            UserAgentBrowser.SAFARI: UserAgentCategory.DESKTOP_SAFARI,
            UserAgentBrowser.EDGE: UserAgentCategory.DESKTOP_EDGE,
            UserAgentBrowser.OPERA: UserAgentCategory.DESKTOP_OPERA,
            UserAgentBrowser.BRAVE: UserAgentCategory.DESKTOP_BRAVE,
        }
        if browser in browser_to_category:
            return browser_to_category[browser]
        return UserAgentCategory.DESKTOP_CHROME  # Défaut desktop

    return UserAgentCategory.DESKTOP_CHROME  # Défaut global


# ============================================================================
# CLASSE PRINCIPALE — UserAgentsManager
# ============================================================================


class UserAgentsManager:
    """Gestionnaire de rotation des User-Agents.

    Charge une banque d'User-Agents depuis un fichier texte (embarqué ou custom),
    les catégorise, et fournit plusieurs stratégies de rotation pour éviter
    le fingerprinting par les sites protégés.

    Lifecycle :
        >>> manager = UserAgentsManager()
        >>> await manager.start()
        >>> ua = manager.get_random()
        >>> await manager.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Les opérations de rotation sont thread-safe.
    """

    # Fichier embarqué par défaut
    _EMBEDDED_PACKAGE: ClassVar[str] = "nexusdl.data"
    _EMBEDDED_FILENAME: ClassVar[str] = "user_agents.txt"

    # Patterns pour parser le fichier
    _COMMENT_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^\s*#")
    _CATEGORY_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"^\s*#\s*-{3,}\s*(.+?)\s*-{3,}\s*$"
    )

    # Mapping des noms de catégories du fichier vers les enums
    _CATEGORY_NAME_MAPPING: ClassVar[dict[str, UserAgentCategory]] = {
        "desktop chrome": UserAgentCategory.DESKTOP_CHROME,
        "desktop firefox": UserAgentCategory.DESKTOP_FIREFOX,
        "desktop safari": UserAgentCategory.DESKTOP_SAFARI,
        "desktop edge": UserAgentCategory.DESKTOP_EDGE,
        "desktop opera": UserAgentCategory.DESKTOP_OPERA,
        "desktop brave": UserAgentCategory.DESKTOP_BRAVE,
        "mobile android": UserAgentCategory.MOBILE_ANDROID,
        "mobile ios": UserAgentCategory.MOBILE_IOS,
        "tablet": UserAgentCategory.TABLET,
        "tablets": UserAgentCategory.TABLET,
        "crawlers": UserAgentCategory.CRAWLERS,
    }

    # Longueur minimale/maximale d'un UA valide
    _MIN_UA_LENGTH: Final[int] = 20
    _MAX_UA_LENGTH: Final[int] = 1000

    def __init__(
        self,
        *,
        custom_file_path: Path | None = None,
        blacklist: set[str] | None = None,
        default_category: UserAgentCategory = UserAgentCategory.DESKTOP_CHROME,
    ) -> None:
        """Initialise le gestionnaire d'UA.

        Args:
            custom_file_path: Chemin vers un fichier custom d'UA.
                              Si None, utilise le fichier embarqué.
            blacklist: Ensemble d'UA à exclure de la rotation.
            default_category: Catégorie par défaut pour get_random().
        """
        self._custom_file_path = custom_file_path
        self._blacklist = blacklist or set()
        self._default_category = default_category

        # État
        self._started: bool = False
        self._load_lock = asyncio.Lock()

        # Données chargées
        self._all_entries: list[UserAgentEntry] = []
        self._entries_by_category: dict[UserAgentCategory, list[UserAgentEntry]] = defaultdict(list)
        self._entries_by_platform: dict[UserAgentPlatform, list[UserAgentEntry]] = defaultdict(list)
        self._entries_by_browser: dict[UserAgentBrowser, list[UserAgentEntry]] = defaultdict(list)

        # Cache sticky (session_id → UA)
        self._sticky_cache: dict[str, str] = {}
        self._sticky_lock = asyncio.Lock()

        # Statistiques
        self._total_rotations: int = 0
        self._rotations_by_category: dict[str, int] = defaultdict(int)
        self._rotations_by_platform: dict[str, int] = defaultdict(int)
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._last_rotation_at: datetime | None = None
        self._start_time: float = 0.0
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="user_agents_manager")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Charge la banque d'UA et initialise le gestionnaire.

        Raises:
            UserAgentsLoadError: Si le fichier ne peut être chargé ou parsé.
        """
        if self._started:
            self._logger.warning("UserAgentsManager déjà démarré, ignore")
            return

        async with self._load_lock:
            try:
                await self._load_user_agents()
                self._started = True
                self._start_time = asyncio.get_event_loop().time()

                self._logger.info(
                    "UserAgentsManager démarré: {} UA chargés ({} catégories)",
                    len(self._all_entries),
                    len(self._entries_by_category),
                )

                if not self._all_entries:
                    self._logger.warning(
                        "Aucun User-Agent chargé ! La rotation ne fonctionnera pas."
                    )

            except Exception as e:
                self._logger.error("Échec du chargement des User-Agents: {}", e)
                raise UserAgentsLoadError(str(e)) from e

    async def stop(self) -> None:
        """Arrête le gestionnaire et libère les ressources."""
        if not self._started:
            return

        async with self._sticky_lock:
            self._sticky_cache.clear()

        self._started = False
        self._logger.info("UserAgentsManager arrêté")

    async def reload(self) -> int:
        """Recharge la banque d'UA depuis le fichier.

        Utile si le fichier custom a été modifié.

        Returns:
            Nombre d'UA chargés.
        """
        async with self._load_lock:
            # Reset
            self._all_entries.clear()
            self._entries_by_category.clear()
            self._entries_by_platform.clear()
            self._entries_by_browser.clear()

            await self._load_user_agents()

            self._logger.info(
                "User-Agents rechargés: {} UA",
                len(self._all_entries),
            )
            return len(self._all_entries)

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------------

    @property
    def is_started(self) -> bool:
        """Indique si le gestionnaire est démarré."""
        return self._started

    @property
    def total_user_agents(self) -> int:
        """Nombre total d'UA chargés."""
        return len(self._all_entries)

    @property
    def categories(self) -> list[UserAgentCategory]:
        """Liste des catégories disponibles (non vides)."""
        return [
            cat for cat in UserAgentCategory
            if self._entries_by_category.get(cat)
        ]

    # ------------------------------------------------------------------------
    # API publique — Rotation simple
    # ------------------------------------------------------------------------

    def get_random(self) -> str:
        """Retourne un User-Agent aléatoire (toutes catégories confondues).

        Returns:
            Chaîne User-Agent.

        Raises:
            UserAgentsNotStartedError: Si le gestionnaire n'est pas démarré.
            NoUserAgentAvailableError: Si aucun UA n'est disponible.
        """
        self._ensure_started()
        return self._get_random_from(self._all_entries)

    def get_random_by_category(self, category: UserAgentCategory) -> str:
        """Retourne un UA aléatoire d'une catégorie spécifique.

        Args:
            category: Catégorie souhaitée.

        Returns:
            Chaîne User-Agent.

        Raises:
            NoUserAgentAvailableError: Si aucun UA ne correspond.
        """
        self._ensure_started()
        entries = self._entries_by_category.get(category, [])
        if not entries:
            raise NoUserAgentAvailableError(category=category)
        return self._get_random_from(entries)

    def get_random_by_platform(self, platform: UserAgentPlatform) -> str:
        """Retourne un UA aléatoire d'une plateforme spécifique.

        Args:
            platform: Plateforme souhaitée.

        Returns:
            Chaîne User-Agent.

        Raises:
            NoUserAgentAvailableError: Si aucun UA ne correspond.
        """
        self._ensure_started()
        entries = self._entries_by_platform.get(platform, [])
        if not entries:
            raise NoUserAgentAvailableError(platform=platform)
        return self._get_random_from(entries)

    def get_random_by_browser(self, browser: UserAgentBrowser) -> str:
        """Retourne un UA aléatoire d'un navigateur spécifique.

        Args:
            browser: Navigateur souhaité.

        Returns:
            Chaîne User-Agent.

        Raises:
            NoUserAgentAvailableError: Si aucun UA ne correspond.
        """
        self._ensure_started()
        entries = self._entries_by_browser.get(browser, [])
        if not entries:
            raise NoUserAgentAvailableError(browser=browser)
        return self._get_random_from(entries)

    def get_random_filtered(
        self,
        *,
        platform: UserAgentPlatform | None = None,
        browser: UserAgentBrowser | None = None,
        category: UserAgentCategory | None = None,
        exclude_mobile: bool = False,
        exclude_desktop: bool = False,
    ) -> str:
        """Retourne un UA aléatoire avec filtrage combiné.

        Tous les filtres sont optionnels et combinés avec AND.

        Args:
            platform: Filtrer par plateforme.
            browser: Filtrer par navigateur.
            category: Filtrer par catégorie.
            exclude_mobile: Exclure les UA mobiles.
            exclude_desktop: Exclure les UA desktop.

        Returns:
            Chaîne User-Agent.

        Raises:
            NoUserAgentAvailableError: Si aucun UA ne correspond.
        """
        self._ensure_started()

        # Déterminer le pool de départ
        if category is not None:
            entries = list(self._entries_by_category.get(category, []))
        elif platform is not None:
            entries = list(self._entries_by_platform.get(platform, []))
        elif browser is not None:
            entries = list(self._entries_by_browser.get(browser, []))
        else:
            entries = list(self._all_entries)

        # Appliquer les filtres additionnels
        if platform is not None and category is None:
            entries = [e for e in entries if e.matches_platform(platform)]

        if browser is not None and category is None:
            entries = [e for e in entries if e.matches_browser(browser)]

        if exclude_mobile:
            entries = [e for e in entries if not e.is_mobile]

        if exclude_desktop:
            entries = [e for e in entries if not e.is_desktop]

        if not entries:
            raise NoUserAgentAvailableError(
                platform=platform, browser=browser, category=category
            )

        return self._get_random_from(entries)

    # ------------------------------------------------------------------------
    # API publique — Mode sticky
    # ------------------------------------------------------------------------

    async def get_sticky(self, session_id: str) -> str:
        """Retourne un UA sticky pour une session donnée.

        Le même UA est retourné pour le même session_id, permettant
        de simuler un navigateur cohérent durant toute une session.

        Args:
            session_id: Identifiant unique de la session (ex: task_id).

        Returns:
            Chaîne User-Agent (toujours la même pour ce session_id).

        Raises:
            UserAgentsNotStartedError: Si le gestionnaire n'est pas démarré.
            NoUserAgentAvailableError: Si aucun UA n'est disponible.
        """
        self._ensure_started()

        # Hash du session_id pour éviter les collisions
        cache_key = hashlib.sha256(session_id.encode()).hexdigest()[:16]

        async with self._sticky_lock:
            if cache_key in self._sticky_cache:
                self._cache_hits += 1
                ua = self._sticky_cache[cache_key]
                self._record_rotation(ua)
                return ua

            self._cache_misses += 1

            # Générer un nouvel UA basé sur le hash (déterministe)
            # On utilise le hash pour seed le random, ce qui garantit
            # que le même session_id → même UA
            seed = int(cache_key, 16)
            rng = random.Random(seed)

            if not self._all_entries:
                raise NoUserAgentAvailableError()

            entry = rng.choice(self._all_entries)
            ua = entry.user_agent

            # Mettre en cache
            self._sticky_cache[cache_key] = ua

            self._logger.trace(
                "UA sticky généré pour session {}: {}",
                session_id[:20],
                ua[:50],
            )

            self._record_rotation(ua)
            return ua

    async def release_sticky(self, session_id: str) -> bool:
        """Libère un UA sticky pour une session.

        Args:
            session_id: Identifiant de la session à libérer.

        Returns:
            True si la session était en cache et a été libérée.
        """
        cache_key = hashlib.sha256(session_id.encode()).hexdigest()[:16]

        async with self._sticky_lock:
            if cache_key in self._sticky_cache:
                del self._sticky_cache[cache_key]
                return True
            return False

    def clear_sticky_cache(self) -> int:
        """Vide le cache des UA sticky.

        Returns:
            Nombre de sessions libérées.
        """
        # Note: pas de lock nécessaire car c'est une opération rapide
        count = len(self._sticky_cache)
        self._sticky_cache.clear()
        return count

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> UserAgentsStats:
        """Retourne les statistiques agrégées du gestionnaire."""
        async with self._stats_lock:
            uptime = 0.0
            if self._start_time > 0:
                uptime = asyncio.get_event_loop().time() - self._start_time

            return UserAgentsStats(
                total_user_agents=len(self._all_entries),
                total_rotations=self._total_rotations,
                rotations_by_category=dict(self._rotations_by_category),
                rotations_by_platform=dict(self._rotations_by_platform),
                sticky_sessions_count=len(self._sticky_cache),
                cache_hits=self._cache_hits,
                cache_misses=self._cache_misses,
                last_rotation_at=self._last_rotation_at,
                uptime_seconds=uptime,
            )

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        async with self._stats_lock:
            self._total_rotations = 0
            self._rotations_by_category.clear()
            self._rotations_by_platform.clear()
            self._cache_hits = 0
            self._cache_misses = 0
            self._last_rotation_at = None

    # ------------------------------------------------------------------------
    # API publique — Introspection
    # ------------------------------------------------------------------------

    def list_entries(
        self,
        *,
        category: UserAgentCategory | None = None,
        platform: UserAgentPlatform | None = None,
        browser: UserAgentBrowser | None = None,
    ) -> list[UserAgentEntry]:
        """Liste les entrées d'UA avec filtrage optionnel.

        Args:
            category: Filtrer par catégorie.
            platform: Filtrer par plateforme.
            browser: Filtrer par navigateur.

        Returns:
            Liste des entrées correspondantes.
        """
        self._ensure_started()

        if category is not None:
            entries = list(self._entries_by_category.get(category, []))
        elif platform is not None:
            entries = list(self._entries_by_platform.get(platform, []))
        elif browser is not None:
            entries = list(self._entries_by_browser.get(browser, []))
        else:
            entries = list(self._all_entries)

        # Appliquer les filtres additionnels
        if platform is not None and category is None:
            entries = [e for e in entries if e.matches_platform(platform)]
        if browser is not None and category is None:
            entries = [e for e in entries if e.matches_browser(browser)]

        return entries

    def get_entry_for_ua(self, user_agent: str) -> UserAgentEntry | None:
        """Retourne l'entrée correspondant à un UA spécifique.

        Args:
            user_agent: Chaîne User-Agent à rechercher.

        Returns:
            Entrée correspondante ou None.
        """
        self._ensure_started()
        for entry in self._all_entries:
            if entry.user_agent == user_agent:
                return entry
        return None

    # ------------------------------------------------------------------------
    # Méthodes internes — Chargement
    # ------------------------------------------------------------------------

    async def _load_user_agents(self) -> None:
        """Charge et parse le fichier d'UA."""
        # Déterminer le chemin du fichier
        if self._custom_file_path is not None:
            file_path = self._custom_file_path
            if not file_path.exists():
                raise UserAgentsLoadError(f"Fichier custom introuvable: {file_path}")
            content = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
        else:
            # Charger depuis les ressources embarquées
            try:
                data_resource = importlib.resources.files(self._EMBEDDED_PACKAGE)
                file_path = Path(str(data_resource / self._EMBEDDED_FILENAME))
                content = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
            except Exception as e:
                raise UserAgentsLoadError(
                    f"Impossible de charger le fichier embarqué: {e}"
                ) from e

        # Parser le contenu
        entries = await asyncio.to_thread(self._parse_content, content)

        # Filtrer les UA blacklistés
        if self._blacklist:
            entries = [e for e in entries if e.user_agent not in self._blacklist]

        # Indexer
        self._all_entries = entries
        for entry in entries:
            self._entries_by_category[entry.category].append(entry)
            if entry.platform is not None:
                self._entries_by_platform[entry.platform].append(entry)
            if entry.browser is not None:
                self._entries_by_browser[entry.browser].append(entry)

    def _parse_content(self, content: str) -> list[UserAgentEntry]:
        """Parse le contenu du fichier d'UA.

        Le format est :
            - Lignes vides : ignorées
            - Lignes commençant par # : commentaires ou séparateurs de catégorie
            - Autres lignes : User-Agents

        Args:
            content: Contenu du fichier.

        Returns:
            Liste d'entrées parsées.
        """
        entries: list[UserAgentEntry] = []
        current_category: UserAgentCategory = self._default_category

        for line in content.splitlines():
            line = line.strip()

            # Ligne vide
            if not line:
                continue

            # Commentaire ou séparateur de catégorie
            if line.startswith("#"):
                # Détecter un séparateur de catégorie
                match = self._CATEGORY_PATTERN.match(line)
                if match:
                    category_name = match.group(1).lower().strip()
                    # Normaliser le nom
                    category_name = category_name.replace("—", "-").replace("–", "-")
                    # Chercher dans le mapping
                    for key, category in self._CATEGORY_NAME_MAPPING.items():
                        if key in category_name:
                            current_category = category
                            break
                continue

            # Ligne d'UA
            if len(line) < self._MIN_UA_LENGTH:
                continue
            if len(line) > self._MAX_UA_LENGTH:
                continue

            # Détecter la plateforme et le navigateur
            platform = detect_platform(line)
            browser = detect_browser(line)

            # Créer l'entrée
            try:
                entry = UserAgentEntry(
                    user_agent=line,
                    category=current_category,
                    platform=platform,
                    browser=browser,
                )
                entries.append(entry)
            except Exception as e:
                self._logger.trace("UA ignoré (invalide): {} — {}", line[:50], e)

        return entries

    # ------------------------------------------------------------------------
    # Méthodes internes — Rotation
    # ------------------------------------------------------------------------

    def _get_random_from(self, entries: list[UserAgentEntry]) -> str:
        """Sélectionne un UA aléatoire dans une liste.

        Utilise une rotation pondérée si les poids sont différents.

        Args:
            entries: Liste d'entrées à choisir.

        Returns:
            Chaîne User-Agent.
        """
        if not entries:
            raise NoUserAgentAvailableError()

        # Rotation pondérée si des poids sont différents de 1.0
        weights = [e.weight for e in entries]
        if any(w != 1.0 for w in weights):
            entry = random.choices(entries, weights=weights, k=1)[0]
        else:
            entry = random.choice(entries)

        self._record_rotation(entry.user_agent)
        return entry.user_agent

    def _record_rotation(self, user_agent: str) -> None:
        """Enregistre une rotation dans les statistiques."""
        # Pas de lock nécessaire pour des opérations simples
        self._total_rotations += 1
        self._last_rotation_at = datetime.now(UTC)

        # Trouver l'entrée pour les stats par catégorie/platforme
        entry = self.get_entry_for_ua(user_agent)
        if entry is not None:
            self._rotations_by_category[entry.category.value] += 1
            if entry.platform is not None:
                self._rotations_by_platform[entry.platform.value] += 1

    # ------------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # ------------------------------------------------------------------------

    def _ensure_started(self) -> None:
        """Vérifie que le gestionnaire est démarré."""
        if not self._started:
            raise UserAgentsNotStartedError()

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<UserAgentsManager status={status} "
            f"total_ua={len(self._all_entries)} "
            f"categories={len(self._entries_by_category)}>"
        )

    def __len__(self) -> int:
        """Nombre d'UA chargés."""
        return len(self._all_entries)


# ============================================================================
# HELPERS PUBLICS — Fonctions utilitaires
# ============================================================================


async def load_user_agents_from_file(file_path: Path) -> list[UserAgentEntry]:
    """Charge une liste d'UA depuis un fichier (one-shot, sans manager).

    Fonction utilitaire pour usage externe.

    Args:
        file_path: Chemin vers le fichier d'UA.

    Returns:
        Liste d'entrées parsées.
    """
    manager = UserAgentsManager(custom_file_path=file_path)
    await manager.start()
    entries = manager.list_entries()
    await manager.stop()
    return entries


def detect_user_agent_info(user_agent: str) -> dict[str, Any]:
    """Détecte les informations d'un User-Agent.

    Fonction utilitaire pour analyser un UA arbitraire.

    Args:
        user_agent: Chaîne User-Agent à analyser.

    Returns:
        Dictionnaire avec platform, browser, category.
    """
    platform = detect_platform(user_agent)
    browser = detect_browser(user_agent)
    category = detect_category(user_agent)

    return {
        "platform": platform.value if platform else None,
        "browser": browser.value if browser else None,
        "category": category.value,
    }


def is_likely_bot(user_agent: str) -> bool:
    """Détecte si un User-Agent semble être un bot/crawler.

    Args:
        user_agent: Chaîne User-Agent à analyser.

    Returns:
        True si l'UA semble être un bot.
    """
    bot_patterns = [
        r"bot",
        r"crawler",
        r"spider",
        r"scraper",
        r"python-requests",
        r"python-urllib",
        r"go-http-client",
        r"curl/",
        r"wget/",
        r"headlesschrome",
        r"phantomjs",
        r"playwright",
        r"selenium",
    ]
    ua_lower = user_agent.lower()
    return any(re.search(pattern, ua_lower) for pattern in bot_patterns)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "UserAgentsError",
    "UserAgentsNotStartedError",
    "UserAgentsLoadError",
    "NoUserAgentAvailableError",
    # Enums
    "UserAgentCategory",
    "UserAgentPlatform",
    "UserAgentBrowser",
    # Modèles
    "UserAgentEntry",
    "UserAgentsStats",
    # Classe principale
    "UserAgentsManager",
    # Helpers — Détection
    "detect_platform",
    "detect_browser",
    "detect_category",
    "detect_user_agent_info",
    "is_likely_bot",
    # Helpers — Chargement
    "load_user_agents_from_file",
]
