"""Système de configuration globale pour NexusDL (Pydantic Settings v2).

Ce module fournit le système de configuration centralisé de NexusDL, chargé
au démarrage de l'application et accessible partout via une instance globale.
Il combine trois sources de configuration par ordre de priorité croissante :

    1. **Valeurs par défaut** : définies dans les modèles Pydantic
    2. **Fichier YAML** : `~/.config/nexusdl/config.yaml` (ou custom)
    3. **Variables d'environnement** : `NEXUSDL_*` (priorité maximale)

**Caractéristiques** :
    - Validation stricte via Pydantic v2 (types, ranges, patterns)
    - Chargement YAML avec support des includes
    - Overrides par variables d'environnement (NEXUSDL_*)
    - Rechargement à chaud (sans redémarrage)
    - Configuration par sections (network, logging, i18n, etc.)
    - Génération automatique du fichier de config par défaut
    - Détection des changements de configuration
    - Thread-safe (locks asyncio)
    - Intégration avec paths.py pour les chemins
    - Intégration avec constants.py pour les valeurs par défaut
    - Événements EventBus lors des changements de configuration

**Structure du fichier config.yaml** :
    ```yaml
    # Configuration NexusDL
    app:
      language: "fr"
      theme: "dark"

    network:
      timeout: 30
      max_connections: 100
      rotate_user_agent: true
      proxy:
        enabled: false
        url: "http://proxy:8080"

    logging:
      level: "INFO"
      format: "text"
      rotation: "10 MB"

    download:
      max_concurrent_tasks: 3
      max_concurrent_pages: 8
      default_format: "cbz"
      output_dir: "~/Downloads/NexusDL"

    library:
      path: "~/.local/share/nexusdl/library"
      auto_scan: true
      scan_interval: 300

    cloudflare:
      bypass_mode: "auto"
      flaresolverr_url: "http://localhost:8191"
      playwright_enabled: true

    i18n:
      language: "fr"
      fallback: "en"

    storage:
      cache_ttl: 3600
      max_cache_size_mb: 500
    ```

**Variables d'environnement** (override le fichier YAML) :
    NEXUSDL_LANGUAGE          → app.language
    NEXUSDL_THEME             → app.theme
    NEXUSDL_LOG_LEVEL         → logging.level
    NEXUSDL_LOG_FORMAT        → logging.format
    NEXUSDL_TIMEOUT           → network.timeout
    NEXUSDL_PROXY_URL         → network.proxy.url
    NEXUSDL_DOWNLOAD_DIR      → download.output_dir
    NEXUSDL_LIBRARY_DIR       → library.path
    NEXUSDL_BYPASS_MODE       → cloudflare.bypass_mode
    NEXUSDL_FLARESOLVERR_URL  → cloudflare.flaresolverr_url

Exemple d'utilisation :
    >>> from nexusdl.core.config import config, get_config, AppConfig
    >>>
    >>> # Accès direct à la configuration
    >>> print(config.app.language)
    'fr'
    >>> print(config.network.timeout)
    30
    >>> print(config.download.default_format)
    'cbz'
    >>>
    >>> # Rechargement à chaud
    >>> await config.reload()
    >>>
    >>> # Validation manuelle
    >>> custom_config = AppConfig.model_validate({
    ...     "app": {"language": "en"},
    ...     "network": {"timeout": 60},
    ... })
    >>>
    >>> # Génération du fichier par défaut
    >>> config.generate_default_config_file()

Intégration :
    - core/logger.py       : lit config.logging.*
    - core/i18n.py         : lit config.i18n.*
    - core/paths.py        : lit config.paths.*
    - core/session/*       : lit config.network.*
    - core/downloader/*    : lit config.download.*
    - core/library/*       : lit config.library.*
    - core/registry/*      : lit config.registry.*
    - interfaces/*         : lit config.app.*
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Final, Self

import yaml
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nexusdl.core.constants import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_CACHE_CLEANUP_INTERVAL_SECONDS,
    DEFAULT_CACHE_MAX_AGE_SECONDS,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_COOKIE_CACHE_SIZE,
    DEFAULT_COOKIE_CACHE_TTL_SECONDS,
    DEFAULT_DEDUP_CACHE_SIZE,
    DEFAULT_EVENT_BUS_WORKERS,
    DEFAULT_EVENT_HANDLER_TIMEOUT_SECONDS,
    DEFAULT_EVENT_QUEUE_SIZE,
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_IMAGE_QUALITY,
    DEFAULT_JPEG_QUALITY,
    DEFAULT_LANGUAGE,
    DEFAULT_LOG_FORMAT,
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOG_RETENTION,
    DEFAULT_LOG_ROTATION,
    DEFAULT_MAX_HTTP_CONNECTIONS,
    DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
    DEFAULT_METADATA_CACHE_SIZE,
    DEFAULT_OPERATION_TIMEOUT_SECONDS,
    DEFAULT_PACKAGING_FORMAT,
    DEFAULT_PAGE_SIZE,
    DEFAULT_PDF_DPI,
    DEFAULT_PROXY_HEALTH_CHECK_INTERVAL_SECONDS,
    DEFAULT_PROXY_ROTATION_STRATEGY,
    DEFAULT_RAR_COMPRESSION_LEVEL,
    DEFAULT_READING_DIRECTION,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_THEME,
    DEFAULT_TIMEZONE,
    DEFAULT_TRANSLATION_CACHE_SIZE,
    DEFAULT_UA_ROTATION_STRATEGY,
    DEFAULT_ZIP_COMPRESSION_LEVEL,
    FALLBACK_LANGUAGE,
    MAX_CONCURRENT_CHAPTERS,
    MAX_CONCURRENT_DOWNLOAD_TASKS,
    MAX_CONCURRENT_PAGES,
    MAX_FILE_SIZE_BYTES,
    MAX_IMAGE_SIZE_BYTES,
    MAX_PAGES_PER_CHAPTER,
    SUPPORTED_PACKAGING_FORMATS,
)
from nexusdl.core.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigurationError,
)


# ============================================================================
# CONSTANTES — Configuration
# ============================================================================


# Nom du fichier de configuration
CONFIG_FILENAME: Final[str] = "config.yaml"

# Préfixe des variables d'environnement
ENV_PREFIX: Final[str] = "NEXUSDL_"

# Version du schéma de configuration
CONFIG_SCHEMA_VERSION: Final[str] = "1.0"


# ============================================================================
# ENUMS — Options de configuration
# ============================================================================


class CloudflareBypassMode(str, Enum):
    """Mode de contournement Cloudflare.

    AUTO       : Détection automatique (Playwright si nécessaire).
    FLARESOLVERR : Utiliser FlareSolverr (service externe).
    PLAYWRIGHT : Utiliser Playwright directement.
    NONE       : Pas de contournement.
    """

    AUTO = "auto"
    FLARESOLVERR = "flaresolverr"
    PLAYWRIGHT = "playwright"
    NONE = "none"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            CloudflareBypassMode.AUTO: "Automatique",
            CloudflareBypassMode.FLARESOLVERR: "FlareSolverr",
            CloudflareBypassMode.PLAYWRIGHT: "Playwright",
            CloudflareBypassMode.NONE: "Aucun",
        }[self]


class ImageQuality(str, Enum):
    """Qualité d'image pour le téléchargement.

    ORIGINAL : Qualité originale (pas de recompression).
    HIGH     : Haute qualité (JPEG 90%).
    MEDIUM   : Qualité moyenne (JPEG 75%).
    LOW      : Basse qualité (JPEG 50%).
    """

    ORIGINAL = "original"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def jpeg_quality(self) -> int | None:
        """Qualité JPEG correspondante (None = original)."""
        return {
            ImageQuality.ORIGINAL: None,
            ImageQuality.HIGH: 90,
            ImageQuality.MEDIUM: 75,
            ImageQuality.LOW: 50,
        }[self]


class LogLevel(str, Enum):
    """Niveaux de log pour la configuration."""

    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogFormat(str, Enum):
    """Formats de log pour la configuration."""

    TEXT = "text"
    RICH = "rich"
    JSON = "json"
    SIMPLE = "simple"


# ============================================================================
# MODÈLES PYDANTIC — Sections de configuration
# ============================================================================


class AppConfig(BaseModel):
    """Configuration générale de l'application.

    Attributes:
        language: Langue de l'interface (ISO 639-1).
        theme: Thème de l'interface (light, dark, system).
        timezone: Fuseau horaire (IANA ou 'auto').
        reading_direction: Sens de lecture par défaut.
        check_updates: Vérifier les mises à jour au démarrage.
        telemetry: Envoyer des données de télémétrie anonymes.
        first_run: Indique si c'est le premier démarrage.
    """

    language: str = Field(
        default=DEFAULT_LANGUAGE,
        min_length=2,
        max_length=5,
        description="Langue de l'interface (ISO 639-1).",
    )
    theme: str = Field(
        default=DEFAULT_THEME,
        description="Thème de l'interface (light, dark, system).",
    )
    timezone: str = Field(
        default=DEFAULT_TIMEZONE,
        description="Fuseau horaire (IANA ou 'auto').",
    )
    reading_direction: str = Field(
        default=DEFAULT_READING_DIRECTION,
        description="Sens de lecture par défaut (rtl, ltr, vertical).",
    )
    check_updates: bool = Field(
        default=True,
        description="Vérifier les mises à jour au démarrage.",
    )
    telemetry: bool = Field(
        default=False,
        description="Envoyer des données de télémétrie anonymes.",
    )
    first_run: bool = Field(
        default=True,
        description="Indique si c'est le premier démarrage.",
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("theme")
    @classmethod
    def _validate_theme(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("light", "dark", "system", "auto"):
            raise ValueError(f"Thème invalide: {v} (attendu: light, dark, system, auto)")
        return v

    @field_validator("reading_direction")
    @classmethod
    def _validate_reading_direction(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("rtl", "ltr", "vertical"):
            raise ValueError(
                f"Sens de lecture invalide: {v} (attendu: rtl, ltr, vertical)"
            )
        return v


class NetworkConfig(BaseModel):
    """Configuration réseau.

    Attributes:
        timeout: Timeout par défaut pour les requêtes HTTP (secondes).
        connect_timeout: Timeout de connexion (secondes).
        read_timeout: Timeout de lecture (secondes).
        max_connections: Nombre maximum de connexions simultanées.
        max_keepalive: Nombre maximum de connexions keep-alive.
        follow_redirects: Suivre les redirections HTTP.
        verify_ssl: Vérifier les certificats SSL.
        http2: Activer HTTP/2.
        rotate_user_agent: Activer la rotation des User-Agents.
        ua_strategy: Stratégie de rotation des UA.
        proxy: Configuration du proxy.
    """

    timeout: float = Field(
        default=DEFAULT_HTTP_TIMEOUT_SECONDS,
        gt=0.0,
        le=600.0,
        description="Timeout par défaut (secondes).",
    )
    connect_timeout: float = Field(
        default=DEFAULT_CONNECT_TIMEOUT_SECONDS,
        gt=0.0,
        le=60.0,
        description="Timeout de connexion (secondes).",
    )
    read_timeout: float = Field(
        default=60.0,
        gt=0.0,
        le=600.0,
        description="Timeout de lecture (secondes).",
    )
    max_connections: int = Field(
        default=DEFAULT_MAX_HTTP_CONNECTIONS,
        ge=1,
        le=1000,
        description="Nombre maximum de connexions simultanées.",
    )
    max_keepalive: int = Field(
        default=DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
        ge=0,
        le=100,
        description="Nombre maximum de connexions keep-alive.",
    )
    follow_redirects: bool = Field(
        default=True,
        description="Suivre les redirections HTTP.",
    )
    verify_ssl: bool = Field(
        default=True,
        description="Vérifier les certificats SSL.",
    )
    http2: bool = Field(
        default=False,
        description="Activer HTTP/2.",
    )
    rotate_user_agent: bool = Field(
        default=True,
        description="Activer la rotation des User-Agents.",
    )
    ua_strategy: str = Field(
        default=DEFAULT_UA_ROTATION_STRATEGY,
        description="Stratégie de rotation des UA.",
    )
    proxy: ProxyConfig = Field(
        default_factory=lambda: ProxyConfig(),
        description="Configuration du proxy.",
    )

    model_config = ConfigDict(extra="forbid")


class ProxyConfig(BaseModel):
    """Configuration du proxy.

    Attributes:
        enabled: Activer le proxy.
        url: URL du proxy (ex: http://proxy:8080).
        username: Nom d'utilisateur (optionnel).
        password: Mot de passe (optionnel).
        rotation_strategy: Stratégie de rotation des proxies.
        health_check_interval: Intervalle de health check (secondes).
        no_proxy: Domaines à exclure du proxy.
    """

    enabled: bool = Field(
        default=False,
        description="Activer le proxy.",
    )
    url: str | None = Field(
        default=None,
        max_length=500,
        description="URL du proxy.",
    )
    username: str | None = Field(
        default=None,
        max_length=100,
        description="Nom d'utilisateur.",
    )
    password: str | None = Field(
        default=None,
        max_length=200,
        description="Mot de passe.",
    )
    rotation_strategy: str = Field(
        default=DEFAULT_PROXY_ROTATION_STRATEGY,
        description="Stratégie de rotation.",
    )
    health_check_interval: float = Field(
        default=DEFAULT_PROXY_HEALTH_CHECK_INTERVAL_SECONDS,
        ge=10.0,
        le=3600.0,
        description="Intervalle de health check (secondes).",
    )
    no_proxy: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "::1"],
        description="Domaines à exclure.",
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not v.startswith(("http://", "https://", "socks5://", "socks5h://")):
            raise ValueError(
                f"URL de proxy invalide: {v} "
                "(doit commencer par http://, https://, socks5://, ou socks5h://)"
            )
        return v


class LoggingConfig(BaseModel):
    """Configuration du logging.

    Attributes:
        level: Niveau de log global.
        format: Format de sortie.
        rotation: Taille max avant rotation.
        retention: Durée de rétention.
        compression: Compression des anciens logs.
        colorize: Activer les couleurs.
        diagnose: Activer le diagnostic.
        backtrace: Activer le backtrace complet.
        log_dir: Répertoire des logs (override).
        quiet: Mode silencieux.
        verbose: Mode verbeux.
        module_levels: Niveaux par module.
    """

    level: str = Field(
        default=DEFAULT_LOG_LEVEL,
        description="Niveau de log global.",
    )
    format: str = Field(
        default=DEFAULT_LOG_FORMAT,
        description="Format de sortie (text, rich, json, simple).",
    )
    rotation: str = Field(
        default=DEFAULT_LOG_ROTATION,
        description="Taille max avant rotation.",
    )
    retention: str = Field(
        default=DEFAULT_LOG_RETENTION,
        description="Durée de rétention.",
    )
    compression: str | None = Field(
        default="gz",
        description="Compression des anciens logs.",
    )
    colorize: bool = Field(
        default=True,
        description="Activer les couleurs.",
    )
    diagnose: bool = Field(
        default=False,
        description="Activer le diagnostic.",
    )
    backtrace: bool = Field(
        default=True,
        description="Activer le backtrace.",
    )
    log_dir: str | None = Field(
        default=None,
        description="Répertoire des logs (override).",
    )
    quiet: bool = Field(
        default=False,
        description="Mode silencieux.",
    )
    verbose: bool = Field(
        default=False,
        description="Mode verbeux.",
    )
    module_levels: dict[str, str] = Field(
        default_factory=dict,
        description="Niveaux par module.",
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("level")
    @classmethod
    def _validate_level(cls, v: str) -> str:
        v = v.strip().upper()
        valid = {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}
        if v not in valid:
            raise ValueError(f"Niveau de log invalide: {v}")
        return v

    @field_validator("format")
    @classmethod
    def _validate_format(cls, v: str) -> str:
        v = v.strip().lower()
        valid = {"text", "rich", "json", "simple"}
        if v not in valid:
            raise ValueError(f"Format de log invalide: {v}")
        return v


class DownloadConfig(BaseModel):
    """Configuration des téléchargements.

    Attributes:
        max_concurrent_tasks: Nombre maximum de tâches simultanées.
        max_concurrent_chapters: Nombre maximum de chapitres simultanés.
        max_concurrent_pages: Nombre maximum de pages simultanées.
        default_format: Format d'empaquetage par défaut.
        output_dir: Répertoire de téléchargement.
        image_quality: Qualité d'image.
        jpeg_quality: Qualité JPEG pour conversions.
        zip_compression: Niveau de compression ZIP.
        rar_compression: Niveau de compression RAR.
        pdf_dpi: DPI pour PDF.
        auto_resume: Reprendre automatiquement les téléchargements interrompus.
        retry_count: Nombre de retries par défaut.
        max_file_size: Taille maximale d'un fichier (bytes).
        max_image_size: Taille maximale d'une image (bytes).
        max_pages_per_chapter: Nombre maximum de pages par chapitre.
        dedup_enabled: Activer la déduplication.
        dedup_cache_size: Taille du cache de déduplication.
    """

    max_concurrent_tasks: int = Field(
        default=MAX_CONCURRENT_DOWNLOAD_TASKS,
        ge=1,
        le=20,
        description="Nombre maximum de tâches simultanées.",
    )
    max_concurrent_chapters: int = Field(
        default=MAX_CONCURRENT_CHAPTERS,
        ge=1,
        le=10,
        description="Nombre maximum de chapitres simultanés.",
    )
    max_concurrent_pages: int = Field(
        default=MAX_CONCURRENT_PAGES,
        ge=1,
        le=32,
        description="Nombre maximum de pages simultanées.",
    )
    default_format: str = Field(
        default=DEFAULT_PACKAGING_FORMAT,
        description="Format d'empaquetage par défaut.",
    )
    output_dir: str = Field(
        default="~/Downloads/NexusDL",
        description="Répertoire de téléchargement.",
    )
    image_quality: str = Field(
        default=DEFAULT_IMAGE_QUALITY,
        description="Qualité d'image (original, high, medium, low).",
    )
    jpeg_quality: int = Field(
        default=DEFAULT_JPEG_QUALITY,
        ge=1,
        le=100,
        description="Qualité JPEG pour conversions.",
    )
    zip_compression: int = Field(
        default=DEFAULT_ZIP_COMPRESSION_LEVEL,
        ge=0,
        le=9,
        description="Niveau de compression ZIP.",
    )
    rar_compression: int = Field(
        default=DEFAULT_RAR_COMPRESSION_LEVEL,
        ge=0,
        le=5,
        description="Niveau de compression RAR.",
    )
    pdf_dpi: int = Field(
        default=DEFAULT_PDF_DPI,
        ge=72,
        le=600,
        description="DPI pour PDF.",
    )
    auto_resume: bool = Field(
        default=True,
        description="Reprendre automatiquement les téléchargements interrompus.",
    )
    retry_count: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Nombre de retries par défaut.",
    )
    max_file_size: int = Field(
        default=MAX_FILE_SIZE_BYTES,
        ge=1024,
        description="Taille maximale d'un fichier (bytes).",
    )
    max_image_size: int = Field(
        default=MAX_IMAGE_SIZE_BYTES,
        ge=1024,
        description="Taille maximale d'une image (bytes).",
    )
    max_pages_per_chapter: int = Field(
        default=MAX_PAGES_PER_CHAPTER,
        ge=1,
        le=10000,
        description="Nombre maximum de pages par chapitre.",
    )
    dedup_enabled: bool = Field(
        default=True,
        description="Activer la déduplication.",
    )
    dedup_cache_size: int = Field(
        default=DEFAULT_DEDUP_CACHE_SIZE,
        ge=100,
        le=1000000,
        description="Taille du cache de déduplication.",
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("default_format")
    @classmethod
    def _validate_format(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in SUPPORTED_PACKAGING_FORMATS:
            raise ValueError(
                f"Format invalide: {v} (attendu: {', '.join(SUPPORTED_PACKAGING_FORMATS)})"
            )
        return v

    @field_validator("image_quality")
    @classmethod
    def _validate_quality(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("original", "high", "medium", "low"):
            raise ValueError(f"Qualité invalide: {v}")
        return v


class LibraryConfig(BaseModel):
    """Configuration de la bibliothèque locale.

    Attributes:
        path: Répertoire de la bibliothèque.
        auto_scan: Scanner automatiquement au démarrage.
        scan_interval: Intervalle de scan automatique (secondes).
        watch_changes: Surveiller les changements en temps réel.
        auto_import: Importer automatiquement les nouveaux fichiers.
        metadata_source: Source de métadonnées (comicinfo, filename, both).
        sort_order: Ordre de tri par défaut.
    """

    path: str = Field(
        default="~/.local/share/nexusdl/library",
        description="Répertoire de la bibliothèque.",
    )
    auto_scan: bool = Field(
        default=True,
        description="Scanner automatiquement au démarrage.",
    )
    scan_interval: float = Field(
        default=DEFAULT_SCAN_INTERVAL_SECONDS,
        ge=30.0,
        le=86400.0,
        description="Intervalle de scan automatique (secondes).",
    )
    watch_changes: bool = Field(
        default=False,
        description="Surveiller les changements en temps réel.",
    )
    auto_import: bool = Field(
        default=True,
        description="Importer automatiquement les nouveaux fichiers.",
    )
    metadata_source: str = Field(
        default="both",
        description="Source de métadonnées (comicinfo, filename, both).",
    )
    sort_order: str = Field(
        default="title_asc",
        description="Ordre de tri par défaut.",
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("metadata_source")
    @classmethod
    def _validate_metadata_source(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("comicinfo", "filename", "both"):
            raise ValueError(f"Source de métadonnées invalide: {v}")
        return v


class CloudflareConfig(BaseModel):
    """Configuration du contournement Cloudflare.

    Attributes:
        bypass_mode: Mode de contournement.
        flaresolverr_url: URL du service FlareSolverr.
        flaresolverr_timeout: Timeout pour FlareSolverr (secondes).
        playwright_enabled: Activer Playwright.
        playwright_browser: Navigateur Playwright (chromium, firefox, webkit).
        playwright_headless: Mode headless pour Playwright.
        playwright_max_contexts: Nombre maximum de contexts Playwright.
    """

    bypass_mode: str = Field(
        default="auto",
        description="Mode de contournement (auto, flaresolverr, playwright, none).",
    )
    flaresolverr_url: str = Field(
        default="http://localhost:8191",
        description="URL du service FlareSolverr.",
    )
    flaresolverr_timeout: float = Field(
        default=60.0,
        gt=0.0,
        le=300.0,
        description="Timeout pour FlareSolverr (secondes).",
    )
    playwright_enabled: bool = Field(
        default=True,
        description="Activer Playwright.",
    )
    playwright_browser: str = Field(
        default="chromium",
        description="Navigateur Playwright.",
    )
    playwright_headless: bool = Field(
        default=True,
        description="Mode headless.",
    )
    playwright_max_contexts: int = Field(
        default=4,
        ge=1,
        le=16,
        description="Nombre maximum de contexts.",
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("bypass_mode")
    @classmethod
    def _validate_bypass_mode(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("auto", "flaresolverr", "playwright", "none"):
            raise ValueError(f"Mode de contournement invalide: {v}")
        return v

    @field_validator("playwright_browser")
    @classmethod
    def _validate_browser(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("chromium", "firefox", "webkit"):
            raise ValueError(f"Navigateur invalide: {v}")
        return v


class I18nConfig(BaseModel):
    """Configuration de l'internationalisation.

    Attributes:
        language: Langue de l'interface.
        fallback: Langue de fallback.
        auto_detect: Détecter automatiquement la langue système.
        translations_dir: Répertoire des traductions (override).
    """

    language: str = Field(
        default=DEFAULT_LANGUAGE,
        min_length=2,
        max_length=5,
        description="Langue de l'interface.",
    )
    fallback: str = Field(
        default=FALLBACK_LANGUAGE,
        min_length=2,
        max_length=5,
        description="Langue de fallback.",
    )
    auto_detect: bool = Field(
        default=True,
        description="Détecter automatiquement la langue système.",
    )
    translations_dir: str | None = Field(
        default=None,
        description="Répertoire des traductions (override).",
    )

    model_config = ConfigDict(extra="forbid")


class StorageConfig(BaseModel):
    """Configuration du stockage et du cache.

    Attributes:
        cache_ttl: Durée de vie du cache (secondes).
        max_cache_size_mb: Taille maximale du cache (Mo).
        cleanup_interval: Intervalle de nettoyage (secondes).
        max_age: Âge maximum des fichiers de cache (secondes).
        temp_dir: Répertoire temporaire (override).
        data_dir: Répertoire de données (override).
    """

    cache_ttl: float = Field(
        default=DEFAULT_CACHE_MAX_AGE_SECONDS,
        ge=60.0,
        le=604800.0,
        description="Durée de vie du cache (secondes).",
    )
    max_cache_size_mb: int = Field(
        default=500,
        ge=10,
        le=10000,
        description="Taille maximale du cache (Mo).",
    )
    cleanup_interval: float = Field(
        default=DEFAULT_CACHE_CLEANUP_INTERVAL_SECONDS,
        ge=60.0,
        le=86400.0,
        description="Intervalle de nettoyage (secondes).",
    )
    max_age: float = Field(
        default=DEFAULT_CACHE_MAX_AGE_SECONDS,
        ge=60.0,
        le=2592000.0,
        description="Âge maximum des fichiers de cache (secondes).",
    )
    temp_dir: str | None = Field(
        default=None,
        description="Répertoire temporaire (override).",
    )
    data_dir: str | None = Field(
        default=None,
        description="Répertoire de données (override).",
    )

    model_config = ConfigDict(extra="forbid")


class EventsConfig(BaseModel):
    """Configuration du système d'événements.

    Attributes:
        queue_size: Taille de la file d'événements.
        worker_count: Nombre de workers.
        handler_timeout: Timeout des handlers (secondes).
        dead_letter_enabled: Activer la dead letter queue.
    """

    queue_size: int = Field(
        default=DEFAULT_EVENT_QUEUE_SIZE,
        ge=100,
        le=1000000,
        description="Taille de la file d'événements.",
    )
    worker_count: int = Field(
        default=DEFAULT_EVENT_BUS_WORKERS,
        ge=1,
        le=32,
        description="Nombre de workers.",
    )
    handler_timeout: float = Field(
        default=DEFAULT_EVENT_HANDLER_TIMEOUT_SECONDS,
        gt=0.0,
        le=600.0,
        description="Timeout des handlers (secondes).",
    )
    dead_letter_enabled: bool = Field(
        default=True,
        description="Activer la dead letter queue.",
    )

    model_config = ConfigDict(extra="forbid")


class InterfaceConfig(BaseModel):
    """Configuration de l'interface utilisateur.

    Attributes:
        web_enabled: Activer l'interface web.
        web_port: Port de l'interface web.
        web_host: Host de l'interface web.
        gui_enabled: Activer l'interface GUI.
        cli_color: Activer les couleurs CLI.
        cli_pager: Activer le pager CLI.
    """

    web_enabled: bool = Field(
        default=False,
        description="Activer l'interface web.",
    )
    web_port: int = Field(
        default=8080,
        ge=1024,
        le=65535,
        description="Port de l'interface web.",
    )
    web_host: str = Field(
        default="127.0.0.1",
        description="Host de l'interface web.",
    )
    gui_enabled: bool = Field(
        default=False,
        description="Activer l'interface GUI.",
    )
    cli_color: bool = Field(
        default=True,
        description="Activer les couleurs CLI.",
    )
    cli_pager: bool = Field(
        default=True,
        description="Activer le pager CLI.",
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# MODÈLE PRINCIPAL — NexusDLConfig
# ============================================================================


class NexusDLConfig(BaseModel):
    """Configuration globale de NexusDL.

    Modèle racine qui regroupe toutes les sections de configuration.
    Chargé depuis config.yaml + variables d'environnement.

    Attributes:
        schema_version: Version du schéma de configuration.
        app: Configuration générale.
        network: Configuration réseau.
        logging: Configuration du logging.
        download: Configuration des téléchargements.
        library: Configuration de la bibliothèque.
        cloudflare: Configuration du contournement Cloudflare.
        i18n: Configuration de l'internationalisation.
        storage: Configuration du stockage.
        events: Configuration du système d'événements.
        interface: Configuration de l'interface.
    """

    schema_version: str = Field(
        default=CONFIG_SCHEMA_VERSION,
        description="Version du schéma de configuration.",
    )
    app: AppConfig = Field(
        default_factory=AppConfig,
        description="Configuration générale.",
    )
    network: NetworkConfig = Field(
        default_factory=NetworkConfig,
        description="Configuration réseau.",
    )
    logging: LoggingConfig = Field(
        default_factory=LoggingConfig,
        description="Configuration du logging.",
    )
    download: DownloadConfig = Field(
        default_factory=DownloadConfig,
        description="Configuration des téléchargements.",
    )
    library: LibraryConfig = Field(
        default_factory=LibraryConfig,
        description="Configuration de la bibliothèque.",
    )
    cloudflare: CloudflareConfig = Field(
        default_factory=CloudflareConfig,
        description="Configuration Cloudflare.",
    )
    i18n: I18nConfig = Field(
        default_factory=I18nConfig,
        description="Configuration i18n.",
    )
    storage: StorageConfig = Field(
        default_factory=StorageConfig,
        description="Configuration du stockage.",
    )
    events: EventsConfig = Field(
        default_factory=EventsConfig,
        description="Configuration des événements.",
    )
    interface: InterfaceConfig = Field(
        default_factory=InterfaceConfig,
        description="Configuration de l'interface.",
    )

    model_config = ConfigDict(extra="forbid")

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @model_validator(mode="after")
    def _sync_language(self) -> Self:
        """Synchronise la langue entre app et i18n."""
        # Si i18n.language est différent de app.language,
        # on utilise app.language comme source de vérité
        if self.i18n.language != self.app.language:
            self.i18n = self.i18n.model_copy(
                update={"language": self.app.language}
            )
        return self

    # --------------------------------------------------------------------
    # Méthodes — Sérialisation
    # --------------------------------------------------------------------

    def to_yaml(self) -> str:
        """Sérialise la configuration en YAML.

        Returns:
            Chaîne YAML.
        """
        data = self.model_dump(mode="json")
        return yaml.dump(
            data,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            indent=2,
        )

    def to_dict(self) -> dict[str, Any]:
        """Sérialise la configuration en dictionnaire.

        Returns:
            Dictionnaire de configuration.
        """
        return self.model_dump(mode="json")

    @classmethod
    def from_yaml(cls, yaml_content: str) -> NexusDLConfig:
        """Charge la configuration depuis une chaîne YAML.

        Args:
            yaml_content: Contenu YAML.

        Returns:
            Instance de NexusDLConfig.

        Raises:
            ConfigParseError: Si le YAML est invalide.
        """
        try:
            data = yaml.safe_load(yaml_content)
            if data is None:
                data = {}
            if not isinstance(data, dict):
                raise ConfigParseError("Le fichier de config doit être un objet YAML")
            return cls.model_validate(data)
        except yaml.YAMLError as e:
            raise ConfigParseError(f"YAML invalide: {e}") from e

    @classmethod
    def from_file(cls, path: Path) -> NexusDLConfig:
        """Charge la configuration depuis un fichier YAML.

        Args:
            path: Chemin du fichier.

        Returns:
            Instance de NexusDLConfig.

        Raises:
            ConfigNotFoundError: Si le fichier n'existe pas.
            ConfigParseError: Si le fichier est invalide.
        """
        if not path.exists():
            raise ConfigNotFoundError(str(path))

        try:
            content = path.read_text(encoding="utf-8")
            return cls.from_yaml(content)
        except ConfigParseError:
            raise
        except Exception as e:
            raise ConfigParseError(f"Erreur de lecture: {e}") from e

    @classmethod
    def from_env(cls, base: NexusDLConfig | None = None) -> NexusDLConfig:
        """Charge la configuration depuis les variables d'environnement.

        Les variables d'environnement priment sur la configuration de base.
        Format : NEXUSDL_SECTION_KEY (ex: NEXUSDL_NETWORK_TIMEOUT).

        Args:
            base: Configuration de base à override.

        Returns:
            Instance de NexusDLConfig avec overrides.
        """
        if base is None:
            base = cls()

        data = base.model_dump(mode="json")

        # Mapping des variables d'environnement vers les clés de config
        env_mappings: dict[str, list[str]] = {
            "LANGUAGE": ["app", "language"],
            "THEME": ["app", "theme"],
            "TIMEZONE": ["app", "timezone"],
            "LOG_LEVEL": ["logging", "level"],
            "LOG_FORMAT": ["logging", "format"],
            "LOG_ROTATION": ["logging", "rotation"],
            "LOG_RETENTION": ["logging", "retention"],
            "LOG_QUIET": ["logging", "quiet"],
            "LOG_VERBOSE": ["logging", "verbose"],
            "TIMEOUT": ["network", "timeout"],
            "CONNECT_TIMEOUT": ["network", "connect_timeout"],
            "MAX_CONNECTIONS": ["network", "max_connections"],
            "VERIFY_SSL": ["network", "verify_ssl"],
            "HTTP2": ["network", "http2"],
            "ROTATE_USER_AGENT": ["network", "rotate_user_agent"],
            "PROXY_URL": ["network", "proxy", "url"],
            "PROXY_ENABLED": ["network", "proxy", "enabled"],
            "DOWNLOAD_DIR": ["download", "output_dir"],
            "DOWNLOAD_FORMAT": ["download", "default_format"],
            "MAX_CONCURRENT_TASKS": ["download", "max_concurrent_tasks"],
            "MAX_CONCURRENT_PAGES": ["download", "max_concurrent_pages"],
            "IMAGE_QUALITY": ["download", "image_quality"],
            "RETRY_COUNT": ["download", "retry_count"],
            "LIBRARY_DIR": ["library", "path"],
            "AUTO_SCAN": ["library", "auto_scan"],
            "SCAN_INTERVAL": ["library", "scan_interval"],
            "BYPASS_MODE": ["cloudflare", "bypass_mode"],
            "FLARESOLVERR_URL": ["cloudflare", "flaresolverr_url"],
            "FLARESOLVERR_TIMEOUT": ["cloudflare", "flaresolverr_timeout"],
            "PLAYWRIGHT_ENABLED": ["cloudflare", "playwright_enabled"],
            "PLAYWRIGHT_BROWSER": ["cloudflare", "playwright_browser"],
            "PLAYWRIGHT_HEADLESS": ["cloudflare", "playwright_headless"],
            "I18N_LANGUAGE": ["i18n", "language"],
            "I18N_FALLBACK": ["i18n", "fallback"],
            "CACHE_TTL": ["storage", "cache_ttl"],
            "MAX_CACHE_SIZE_MB": ["storage", "max_cache_size_mb"],
            "WEB_ENABLED": ["interface", "web_enabled"],
            "WEB_PORT": ["interface", "web_port"],
            "WEB_HOST": ["interface", "web_host"],
        }

        for env_suffix, path_keys in env_mappings.items():
            env_var = f"{ENV_PREFIX}{env_suffix}"
            env_value = os.environ.get(env_var)

            if env_value is not None:
                # Naviguer dans le dictionnaire
                current = data
                for key in path_keys[:-1]:
                    if key not in current:
                        current[key] = {}
                    current = current[key]

                # Convertir le type si nécessaire
                last_key = path_keys[-1]
                current[last_key] = _convert_env_value(env_value, current.get(last_key))

        return cls.model_validate(data)

    def save_to_file(self, path: Path) -> None:
        """Sauvegarde la configuration dans un fichier YAML.

        Args:
            path: Chemin du fichier de destination.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_yaml(), encoding="utf-8")

    def generate_default_config_file(
        self,
        path: Path | None = None,
    ) -> Path:
        """Génère un fichier de configuration par défaut.

        Args:
            path: Chemin du fichier (défaut: ~/.config/nexusdl/config.yaml).

        Returns:
            Chemin du fichier généré.
        """
        if path is None:
            from nexusdl.core.paths import get_paths
            path = get_paths().config_file

        if path.exists():
            logger.warning(
                "Le fichier de configuration existe déjà: {}. "
                "Utilisez --force pour l'écraser.",
                path,
            )
            return path

        # Générer le fichier avec des commentaires
        header = (
            f"# Configuration {APP_NAME} v{APP_VERSION}\n"
            f"# Généré automatiquement le {datetime.now(UTC).isoformat()}\n"
            f"# Documentation: https://docs.nexusdl.dev/configuration\n"
            f"#\n"
            f"# Les variables d'environnement NEXUSDL_* priment sur ce fichier.\n"
            f"# Exemple: NEXUSDL_LANGUAGE=fr override app.language\n"
            f"\n"
        )

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header + self.to_yaml(), encoding="utf-8")

        logger.info("Fichier de configuration généré: {}", path)
        return path


# ============================================================================
# HELPERS — Conversion de valeurs
# ============================================================================


def _convert_env_value(value: str, current: Any) -> Any:
    """Convertit une valeur d'environnement en type approprié.

    Args:
        value: Valeur string de l'environnement.
        current: Valeur actuelle (pour inférer le type).

    Returns:
        Valeur convertie.
    """
    if current is None:
        return value

    # Booléen
    if isinstance(current, bool):
        return value.lower() in ("true", "1", "yes", "on")

    # Entier
    if isinstance(current, int):
        try:
            return int(value)
        except ValueError:
            return current

    # Float
    if isinstance(current, float):
        try:
            return float(value)
        except ValueError:
            return current

    # String
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Fusionne profondément deux dictionnaires.

    Args:
        base: Dictionnaire de base.
        override: Dictionnaire d'override.

    Returns:
        Dictionnaire fusionné.
    """
    result = dict(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ============================================================================
# GESTIONNAIRE DE CONFIGURATION
# ============================================================================


class ConfigManager:
    """Gestionnaire de configuration avec chargement et rechargement.

    Orchestre le chargement de la configuration depuis les trois sources
    (défauts, YAML, env vars) et fournit un accès thread-safe.

    Lifecycle :
        >>> manager = ConfigManager()
        >>> manager.load()
        >>> print(manager.config.app.language)
        'fr'
        >>> manager.reload()
    """

    def __init__(
        self,
        *,
        config_path: Path | None = None,
        auto_load: bool = True,
    ) -> None:
        """Initialise le gestionnaire.

        Args:
            config_path: Chemin du fichier de configuration.
            auto_load: Charger automatiquement à l'initialisation.
        """
        self._config_path = config_path
        self._config: NexusDLConfig = NexusDLConfig()
        self._lock = threading.RLock()
        self._loaded_at: datetime | None = None
        self._load_count: int = 0

        if auto_load:
            self.load()

    # ------------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------------

    @property
    def config(self) -> NexusDLConfig:
        """Configuration actuelle (thread-safe)."""
        with self._lock:
            return self._config

    @property
    def config_path(self) -> Path | None:
        """Chemin du fichier de configuration."""
        return self._config_path

    @property
    def loaded_at(self) -> datetime | None:
        """Timestamp du dernier chargement."""
        return self._loaded_at

    @property
    def load_count(self) -> int:
        """Nombre de chargements effectués."""
        return self._load_count

    # ------------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------------

    def load(self, *, config_path: Path | None = None) -> NexusDLConfig:
        """Charge la configuration depuis toutes les sources.

        Ordre de priorité :
            1. Valeurs par défaut (Pydantic)
            2. Fichier YAML (si existant)
            3. Variables d'environnement (NEXUSDL_*)

        Args:
            config_path: Chemin du fichier (override).

        Returns:
            Configuration chargée.
        """
        path = config_path or self._config_path

        with self._lock:
            # 1. Configuration par défaut
            config = NexusDLConfig()

            # 2. Fichier YAML
            if path is not None and path.exists():
                try:
                    file_config = NexusDLConfig.from_file(path)
                    config = file_config
                    logger.debug("Configuration chargée depuis: {}", path)
                except Exception as e:
                    logger.warning(
                        "Erreur lors du chargement de {}: {}. "
                        "Utilisation des valeurs par défaut.",
                        path,
                        e,
                    )

            # 3. Variables d'environnement
            config = NexusDLConfig.from_env(config)

            self._config = config
            self._config_path = path
            self._loaded_at = datetime.now(UTC)
            self._load_count += 1

            logger.info(
                "Configuration chargée ({} sources, {} chargements)",
                "yaml+env" if path and path.exists() else "defaults+env",
                self._load_count,
            )

            return config

    def reload(self) -> NexusDLConfig:
        """Recharge la configuration.

        Returns:
            Configuration rechargée.
        """
        logger.info("Rechargement de la configuration...")
        return self.load()

    def save(self, path: Path | None = None) -> Path:
        """Sauvegarde la configuration actuelle.

        Args:
            path: Chemin de destination (défaut: config_path).

        Returns:
            Chemin du fichier sauvegardé.
        """
        save_path = path or self._config_path
        if save_path is None:
            from nexusdl.core.paths import get_paths
            save_path = get_paths().config_file

        with self._lock:
            self._config.save_to_file(save_path)

        logger.info("Configuration sauvegardée: {}", save_path)
        return save_path

    def update(self, **kwargs: Any) -> NexusDLConfig:
        """Met à jour la configuration avec de nouvelles valeurs.

        Args:
            **kwargs: Paires section.clé=valeur (ex: app__language="en").

        Returns:
            Configuration mise à jour.
        """
        with self._lock:
            data = self._config.model_dump(mode="json")

            for key, value in kwargs.items():
                # Support de la notation section__clé
                parts = key.split("__")
                current = data
                for part in parts[:-1]:
                    if part not in current:
                        current[part] = {}
                    current = current[part]
                current[parts[-1]] = value

            self._config = NexusDLConfig.model_validate(data)
            return self._config

    def get(self, key: str, default: Any = None) -> Any:
        """Accède à une valeur de configuration par clé pointée.

        Args:
            key: Clé pointée (ex: "app.language", "network.timeout").
            default: Valeur par défaut si clé introuvable.

        Returns:
            Valeur de configuration.

        Example:
            >>> manager.get("app.language")
            'fr'
            >>> manager.get("network.timeout")
            30.0
        """
        with self._lock:
            data = self._config.model_dump(mode="json")
            parts = key.split(".")
            current = data

            for part in parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    return default

            return current

    def has_changed(self, path: Path | None = None) -> bool:
        """Vérifie si le fichier de configuration a été modifié.

        Args:
            path: Chemin du fichier (défaut: config_path).

        Returns:
            True si le fichier a été modifié depuis le dernier chargement.
        """
        check_path = path or self._config_path
        if check_path is None or not check_path.exists():
            return False

        if self._loaded_at is None:
            return True

        try:
            file_mtime = datetime.fromtimestamp(
                check_path.stat().st_mtime, tz=UTC
            )
            return file_mtime > self._loaded_at
        except OSError:
            return False

    def validate(self) -> list[str]:
        """Valide la configuration et retourne les erreurs.

        Returns:
            Liste des messages d'erreur (vide si valide).
        """
        errors: list[str] = []

        with self._lock:
            try:
                NexusDLConfig.model_validate(self._config.model_dump())
            except Exception as e:
                errors.append(str(e))

        return errors

    def __repr__(self) -> str:
        return (
            f"<ConfigManager "
            f"path={self._config_path} "
            f"loads={self._load_count} "
            f"language={self._config.app.language}>"
        )


# ============================================================================
# INSTANCE GLOBALE
# ============================================================================


# Instance globale du ConfigManager
_config_manager: ConfigManager | None = None


def get_config_manager() -> ConfigManager:
    """Retourne l'instance globale du ConfigManager.

    Crée l'instance si elle n'existe pas encore.

    Returns:
        Instance globale de ConfigManager.
    """
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


def set_config_manager(manager: ConfigManager) -> None:
    """Remplace l'instance globale du ConfigManager.

    Args:
        manager: Nouvelle instance.
    """
    global _config_manager
    _config_manager = manager


def reset_config_manager() -> None:
    """Réinitialise l'instance globale du ConfigManager."""
    global _config_manager
    _config_manager = None


def get_config() -> NexusDLConfig:
    """Retourne la configuration globale.

    Raccourci pour get_config_manager().config.

    Returns:
        Configuration globale.
    """
    return get_config_manager().config


def reload_config() -> NexusDLConfig:
    """Recharge la configuration globale.

    Returns:
        Configuration rechargée.
    """
    return get_config_manager().reload()


# Alias pratique : accès direct à la configuration
# Usage : from nexusdl.core.config import config
class _ConfigProxy:
    """Proxy pour l'instance globale de configuration.

    Permet d'accéder à la configuration comme si c'était un objet direct.
    """

    def __getattr__(self, name: str) -> Any:
        return getattr(get_config(), name)

    def __repr__(self) -> str:
        return repr(get_config())


config: NexusDLConfig = _ConfigProxy()  # type: ignore[assignment]


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "CONFIG_FILENAME",
    "ENV_PREFIX",
    "CONFIG_SCHEMA_VERSION",
    # Enums
    "CloudflareBypassMode",
    "ImageQuality",
    "LogLevel",
    "LogFormat",
    # Modèles — Sections
    "AppConfig",
    "NetworkConfig",
    "ProxyConfig",
    "LoggingConfig",
    "DownloadConfig",
    "LibraryConfig",
    "CloudflareConfig",
    "I18nConfig",
    "StorageConfig",
    "EventsConfig",
    "InterfaceConfig",
    # Modèle principal
    "NexusDLConfig",
    # Gestionnaire
    "ConfigManager",
    # Instance globale
    "config",
    "get_config",
    "get_config_manager",
    "set_config_manager",
    "reset_config_manager",
    "reload_config",
    # Helpers
    "deep_merge",
]

# Alias pour _deep_merge (export public)
deep_merge = _deep_merge
