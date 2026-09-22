"""Chargeur de configuration des sites (YAML + overrides utilisateur).

Ce module orchestre le chargement et la fusion des configurations de sites
depuis deux sources :

    1. **Configuration de base** : `sites.yaml` embarqué dans le package
       (via `importlib.resources` ou chemin direct). Contient la définition
       de tous les sites supportés par défaut.

    2. **Overrides utilisateur** : `sites_overrides.yaml` dans le répertoire
       de configuration utilisateur (`~/.config/nexusdl/`). Permet à
       l'utilisateur de personnaliser les sites (désactiver, modifier le
       rate limit, ajouter des headers, etc.) sans toucher à la config de base.

Fusion :
    - Les overrides priment sur la configuration de base
    - Les sites existants dans l'override sont mis à jour (champs spécifiés)
    - Les nouveaux sites dans l'override sont ajoutés à la liste
    - Les champs non spécifiés dans l'override restent inchangés
    - La fusion est profonde (deep merge) pour les objets imbriqués

Cache :
    Le document chargé est mis en cache avec invalidation automatique si
    les fichiers sources ont été modifiés (détection via mtime).

Architecture :
    ConfigLoader
        ├── LoaderConfig (Pydantic — chemins et options)
        ├── LoaderState (enum — IDLE, LOADING, LOADED, ERROR)
        ├── LoaderStats (Pydantic — statistiques)
        └── _DocumentCache (interne — cache avec mtime)

Exemple d'utilisation :
    >>> loader = ConfigLoader()
    >>> await loader.start()
    >>>
    >>> # Charger la configuration fusionnée
    >>> document = await loader.load()
    >>> print(f"{len(document['sites'])} sites chargés")
    >>>
    >>> # Forcer un rechargement (ignore le cache)
    >>> document = await loader.load(force_reload=True)
    >>>
    >>> # Vérifier si les fichiers ont changé
    >>> if loader.has_config_changed():
    ...     print("Configuration modifiée, rechargement nécessaire")
    >>>
    >>> await loader.stop()
"""

from __future__ import annotations

import asyncio
import importlib.resources
import time
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Final, Self

import yaml
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# EXCEPTIONS
# ============================================================================


class LoaderError(NexusDLError):
    """Exception de base pour les erreurs du chargeur."""


class LoaderNotStartedError(LoaderError):
    """Exception levée lorsqu'on utilise le chargeur avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "ConfigLoader must be started before use. Call await loader.start()"
        )


class BaseConfigNotFoundError(LoaderError):
    """Exception levée lorsque la configuration de base est introuvable."""

    def __init__(self, path: Path | str) -> None:
        super().__init__(f"Configuration de base introuvable: {path}")
        self.path = path


class OverridesFileError(LoaderError):
    """Exception levée lorsque le fichier d'overrides est invalide."""

    def __init__(self, path: Path, reason: str = "") -> None:
        msg = f"Erreur dans le fichier d'overrides: {path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.path = path
        self.reason = reason


class MergeError(LoaderError):
    """Exception levée lorsque la fusion des configurations échoue."""

    def __init__(self, reason: str = "") -> None:
        msg = "Erreur lors de la fusion des configurations"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class LoaderState(str, Enum):
    """État du chargeur.

    IDLE    : Chargeur créé mais pas encore démarré.
    LOADING : Chargement en cours.
    LOADED  : Configuration chargée et mise en cache.
    ERROR   : Erreur lors du dernier chargement.
    STOPPED : Chargeur arrêté.
    """

    IDLE = "idle"
    LOADING = "loading"
    LOADED = "loaded"
    ERROR = "error"
    STOPPED = "stopped"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            LoaderState.IDLE: "Inactif",
            LoaderState.LOADING: "Chargement",
            LoaderState.LOADED: "Chargé",
            LoaderState.ERROR: "Erreur",
            LoaderState.STOPPED: "Arrêté",
        }[self]

    @property
    def is_operational(self) -> bool:
        """Indique si le chargeur est opérationnel."""
        return self == LoaderState.LOADED


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class LoaderConfig(BaseModel):
    """Configuration du chargeur.

    Définit les chemins vers les fichiers de configuration et les options
    de cache.
    """

    base_config_path: Path | None = Field(
        default=None,
        description="Chemin vers la configuration de base (sites.yaml). "
                    "Si None, utilise la ressource embarquée.",
    )
    overrides_path: Path | None = Field(
        default=None,
        description="Chemin vers les overrides utilisateur (sites_overrides.yaml). "
                    "Si None, utilise ~/.config/nexusdl/sites_overrides.yaml.",
    )
    cache_enabled: bool = Field(
        default=True,
        description="Activer le cache du document chargé.",
    )
    cache_ttl_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Durée de vie du cache en secondes (0 = infini, invalidation par mtime).",
    )
    create_overrides_if_missing: bool = Field(
        default=False,
        description="Créer le fichier d'overrides s'il n'existe pas (avec template).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class LoaderStats(BaseModel):
    """Statistiques agrégées du chargeur."""

    total_loads: int = Field(default=0, ge=0)
    successful_loads: int = Field(default=0, ge=0)
    failed_loads: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    cache_misses: int = Field(default=0, ge=0)
    base_config_loads: int = Field(default=0, ge=0)
    overrides_loads: int = Field(default=0, ge=0)
    overrides_present: bool = Field(default=False)
    total_sites_loaded: int = Field(default=0, ge=0)
    total_sites_overridden: int = Field(default=0, ge=0)
    total_sites_added: int = Field(default=0, ge=0)
    last_load_at: datetime | None = Field(default=None)
    last_load_duration_ms: float = Field(default=0.0, ge=0.0)
    base_config_mtime: float | None = Field(default=None)
    overrides_mtime: float | None = Field(default=None)
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def cache_hit_rate(self) -> float:
        """Taux de hit du cache (0.0 à 1.0)."""
        total = self.cache_hits + self.cache_misses
        if total == 0:
            return 0.0
        return self.cache_hits / total


class _DocumentCache:
    """Cache interne du document chargé avec invalidation par mtime.

    Non exposé publiquement — utilisé en interne par ConfigLoader.
    """

    __slots__ = (
        "_document",
        "_base_mtime",
        "_overrides_mtime",
        "_loaded_at",
        "_ttl_seconds",
    )

    def __init__(self, ttl_seconds: float = 0.0) -> None:
        self._document: dict[str, Any] | None = None
        self._base_mtime: float | None = None
        self._overrides_mtime: float | None = None
        self._loaded_at: float | None = None
        self._ttl_seconds = ttl_seconds

    def get(
        self,
        base_mtime: float | None,
        overrides_mtime: float | None,
    ) -> dict[str, Any] | None:
        """Récupère le document du cache si valide.

        Args:
            base_mtime: mtime actuel du fichier de base.
            overrides_mtime: mtime actuel du fichier d'overrides.

        Returns:
            Document caché ou None si invalide/absent.
        """
        if self._document is None:
            return None

        # Vérifier le TTL
        if self._ttl_seconds > 0 and self._loaded_at is not None:
            if time.monotonic() - self._loaded_at > self._ttl_seconds:
                return None

        # Vérifier les mtimes
        if self._base_mtime != base_mtime:
            return None
        if self._overrides_mtime != overrides_mtime:
            return None

        return self._document

    def put(
        self,
        document: dict[str, Any],
        base_mtime: float | None,
        overrides_mtime: float | None,
    ) -> None:
        """Met à jour le cache.

        Args:
            document: Document à cacher.
            base_mtime: mtime du fichier de base.
            overrides_mtime: mtime du fichier d'overrides.
        """
        self._document = document
        self._base_mtime = base_mtime
        self._overrides_mtime = overrides_mtime
        self._loaded_at = time.monotonic()

    def clear(self) -> None:
        """Vide le cache."""
        self._document = None
        self._base_mtime = None
        self._overrides_mtime = None
        self._loaded_at = None

    @property
    def is_empty(self) -> bool:
        """Indique si le cache est vide."""
        return self._document is None


# ============================================================================
# CLASSE PRINCIPALE — ConfigLoader
# ============================================================================


class ConfigLoader:
    """Chargeur de configuration des sites.

    Gère le chargement, la fusion et le cache des configurations de sites
    depuis les fichiers YAML (base + overrides utilisateur).

    Lifecycle :
        >>> loader = ConfigLoader()
        >>> await loader.start()
        >>> document = await loader.load()
        >>> await loader.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Les opérations de chargement sont protégées par un lock.
    """

    # Nom des fichiers
    _BASE_CONFIG_FILENAME: ClassVar[str] = "sites.yaml"
    _OVERRIDES_FILENAME: ClassVar[str] = "sites_overrides.yaml"

    # Package contenant la config embarquée
    _EMBEDDED_PACKAGE: ClassVar[str] = "nexusdl.core.registry"

    # Template pour le fichier d'overrides
    _OVERRIDES_TEMPLATE: ClassVar[str] = """# ============================================================================
# NEXUSDL — Overrides utilisateur pour la configuration des sites
# ============================================================================
#
# Ce fichier permet de personnaliser la configuration des sites sans
# modifier le fichier de base (sites.yaml). Les overrides priment sur
# la configuration de base.
#
# Structure :
#   overrides:
#     <site_id>:
#       <field>: <value>
#
# Exemples :
#
#   # Désactiver un site
#   overrides:
#     mangadex:
#       enabled: false
#
#   # Modifier le rate limit
#   overrides:
#     sushiscan_net:
#       capabilities:
#         rate_limit_per_second: 1.0
#         max_concurrent_downloads: 2
#
#   # Ajouter des headers personnalisés
#   overrides:
#     asurascans:
#       default_headers:
#         X-Custom-Header: "value"
#
#   # Ajouter un nouveau site (doit respecter le schéma sites_schema.json)
#   new_sites:
#     - id: my_custom_site
#       name: "My Custom Site"
#       domains:
#         - "https://example.com"
#       parser_class: "nexusdl.parsers.custom.my_site:MySiteParser"
#       language: "en"
#       capabilities:
#         supports_search: true
#         supports_manga_info: true
#         supports_chapters: true
#         supports_pages: true
#         supports_download: true
#         requires_auth: false
#         requires_cloudflare_bypass: false
#         requires_javascript_rendering: false
#         max_concurrent_downloads: 4
#         rate_limit_per_second: 2.0
#         min_delay_between_requests: 0.5
#
# ============================================================================

overrides: {}

# new_sites: []
"""

    def __init__(
        self,
        *,
        config: LoaderConfig | None = None,
    ) -> None:
        """Initialise le chargeur.

        Args:
            config: Configuration du chargeur (chemins, cache, etc.).
        """
        self._config = config or LoaderConfig()

        # État
        self._state: LoaderState = LoaderState.IDLE
        self._state_lock = asyncio.Lock()
        self._load_lock = asyncio.Lock()

        # Cache
        self._cache = _DocumentCache(ttl_seconds=self._config.cache_ttl_seconds)

        # Chemins résolus
        self._base_config_path: Path | None = None
        self._overrides_path: Path | None = None

        # Statistiques
        self._total_loads: int = 0
        self._successful_loads: int = 0
        self._failed_loads: int = 0
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._base_config_loads: int = 0
        self._overrides_loads: int = 0
        self._overrides_present: bool = False
        self._total_sites_loaded: int = 0
        self._total_sites_overridden: int = 0
        self._total_sites_added: int = 0
        self._last_load_at: datetime | None = None
        self._last_load_duration_ms: float = 0.0
        self._base_config_mtime: float | None = None
        self._overrides_mtime: float | None = None
        self._start_time: float = 0.0
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="config_loader")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre le chargeur et résout les chemins.

        Raises:
            BaseConfigNotFoundError: Si la configuration de base est introuvable.
        """
        async with self._state_lock:
            if self._state == LoaderState.LOADED:
                self._logger.warning("ConfigLoader déjà démarré, ignore")
                return
            if self._state == LoaderState.LOADING:
                self._logger.warning("ConfigLoader déjà en cours de démarrage")
                return

            self._state = LoaderState.LOADING

        try:
            # Résoudre les chemins
            await self._resolve_paths()

            self._state = LoaderState.IDLE
            self._start_time = time.monotonic()

            self._logger.info(
                "ConfigLoader démarré: base={}, overrides={}",
                self._base_config_path,
                self._overrides_path,
            )

        except Exception as e:
            async with self._state_lock:
                self._state = LoaderState.ERROR
            self._logger.error("Échec du démarrage du ConfigLoader: {}", e)
            raise

    async def stop(self) -> None:
        """Arrête le chargeur et libère les ressources."""
        async with self._state_lock:
            if self._state == LoaderState.STOPPED:
                return
            self._state = LoaderState.STOPPED

        self._cache.clear()
        self._logger.info("ConfigLoader arrêté")

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------------

    @property
    def state(self) -> LoaderState:
        """État actuel du chargeur."""
        return self._state

    @property
    def is_loaded(self) -> bool:
        """Indique si la configuration est chargée et en cache."""
        return self._state == LoaderState.LOADED and not self._cache.is_empty

    @property
    def base_config_path(self) -> Path | None:
        """Chemin vers la configuration de base."""
        return self._base_config_path

    @property
    def overrides_path(self) -> Path | None:
        """Chemin vers les overrides utilisateur."""
        return self._overrides_path

    # ------------------------------------------------------------------------
    # API publique — Chargement
    # ------------------------------------------------------------------------

    async def load(
        self,
        *,
        force_reload: bool = False,
    ) -> dict[str, Any]:
        """Charge et fusionne les configurations.

        Args:
            force_reload: Si True, ignore le cache et recharge depuis les fichiers.

        Returns:
            Document fusionné prêt pour validation.

        Raises:
            LoaderNotStartedError: Si le chargeur n'est pas démarré.
            BaseConfigNotFoundError: Si la configuration de base est introuvable.
            OverridesFileError: Si le fichier d'overrides est invalide.
            MergeError: Si la fusion échoue.
        """
        self._ensure_started()

        async with self._load_lock:
            start_time = time.perf_counter()

            try:
                # 1. Vérifier le cache
                if not force_reload and self._config.cache_enabled:
                    base_mtime = await self._get_mtime(self._base_config_path)
                    overrides_mtime = await self._get_mtime(self._overrides_path)

                    cached = self._cache.get(base_mtime, overrides_mtime)
                    if cached is not None:
                        self._cache_hits += 1
                        self._logger.debug("Configuration chargée depuis le cache")
                        return cached

                    self._cache_misses += 1

                # 2. Charger la configuration de base
                base_config = await self._load_base_config()

                # 3. Charger les overrides (si présents)
                overrides_config = await self._load_overrides()

                # 4. Fusionner
                merged = await self._merge_configs(base_config, overrides_config)

                # 5. Mettre en cache
                if self._config.cache_enabled:
                    base_mtime = await self._get_mtime(self._base_config_path)
                    overrides_mtime = await self._get_mtime(self._overrides_path)
                    self._cache.put(merged, base_mtime, overrides_mtime)

                # 6. Mettre à jour l'état
                async with self._state_lock:
                    self._state = LoaderState.LOADED

                duration_ms = (time.perf_counter() - start_time) * 1000.0

                # 7. Mettre à jour les stats
                async with self._stats_lock:
                    self._total_loads += 1
                    self._successful_loads += 1
                    self._last_load_at = datetime.now(UTC)
                    self._last_load_duration_ms = duration_ms
                    self._total_sites_loaded = len(merged.get("sites", []))

                self._logger.info(
                    "Configuration chargée: {} sites, {:.1f}ms",
                    self._total_sites_loaded,
                    duration_ms,
                )

                return merged

            except Exception as e:
                async with self._stats_lock:
                    self._total_loads += 1
                    self._failed_loads += 1

                async with self._state_lock:
                    self._state = LoaderState.ERROR

                self._logger.error("Échec du chargement de la configuration: {}", e)
                raise

    async def has_config_changed(self) -> bool:
        """Vérifie si les fichiers de configuration ont changé depuis le dernier chargement.

        Returns:
            True si au moins un fichier a été modifié.
        """
        self._ensure_started()

        if self._cache.is_empty:
            return True

        base_mtime = await self._get_mtime(self._base_config_path)
        overrides_mtime = await self._get_mtime(self._overrides_path)

        cached = self._cache.get(base_mtime, overrides_mtime)
        return cached is None

    def clear_cache(self) -> None:
        """Vide le cache du document chargé."""
        self._cache.clear()
        self._logger.debug("Cache du chargeur vidé")

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> LoaderStats:
        """Retourne les statistiques agrégées du chargeur."""
        async with self._stats_lock:
            uptime = 0.0
            if self._start_time > 0:
                uptime = time.monotonic() - self._start_time

            return LoaderStats(
                total_loads=self._total_loads,
                successful_loads=self._successful_loads,
                failed_loads=self._failed_loads,
                cache_hits=self._cache_hits,
                cache_misses=self._cache_misses,
                base_config_loads=self._base_config_loads,
                overrides_loads=self._overrides_loads,
                overrides_present=self._overrides_present,
                total_sites_loaded=self._total_sites_loaded,
                total_sites_overridden=self._total_sites_overridden,
                total_sites_added=self._total_sites_added,
                last_load_at=self._last_load_at,
                last_load_duration_ms=self._last_load_duration_ms,
                base_config_mtime=self._base_config_mtime,
                overrides_mtime=self._overrides_mtime,
                uptime_seconds=uptime,
            )

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        async with self._stats_lock:
            self._total_loads = 0
            self._successful_loads = 0
            self._failed_loads = 0
            self._cache_hits = 0
            self._cache_misses = 0
            self._base_config_loads = 0
            self._overrides_loads = 0
            self._overrides_present = False
            self._total_sites_loaded = 0
            self._total_sites_overridden = 0
            self._total_sites_added = 0
            self._last_load_at = None
            self._last_load_duration_ms = 0.0

    # ------------------------------------------------------------------------
    # Méthodes internes — Résolution des chemins
    # ------------------------------------------------------------------------

    async def _resolve_paths(self) -> None:
        """Résout les chemins vers les fichiers de configuration."""
        # 1. Configuration de base
        if self._config.base_config_path is not None:
            self._base_config_path = self._config.base_config_path.expanduser().resolve()
            if not self._base_config_path.exists():
                raise BaseConfigNotFoundError(self._base_config_path)
        else:
            # Utiliser la ressource embarquée
            try:
                data_resource = importlib.resources.files(self._EMBEDDED_PACKAGE)
                self._base_config_path = Path(str(data_resource / self._BASE_CONFIG_FILENAME))
                if not self._base_config_path.exists():
                    raise BaseConfigNotFoundError(self._base_config_path)
            except Exception as e:
                raise BaseConfigNotFoundError(
                    f"{self._EMBEDDED_PACKAGE}/{self._BASE_CONFIG_FILENAME}"
                ) from e

        # 2. Overrides utilisateur
        if self._config.overrides_path is not None:
            self._overrides_path = self._config.overrides_path.expanduser().resolve()
        else:
            # Chemin par défaut : ~/.config/nexusdl/sites_overrides.yaml
            config_dir = Path.home() / ".config" / "nexusdl"
            self._overrides_path = config_dir / self._OVERRIDES_FILENAME

            # Créer le fichier template si demandé
            if self._config.create_overrides_if_missing and not self._overrides_path.exists():
                await self._create_overrides_template()

        self._logger.debug(
            "Chemins résolus: base={}, overrides={}",
            self._base_config_path,
            self._overrides_path,
        )

    async def _create_overrides_template(self) -> None:
        """Crée le fichier template d'overrides s'il n'existe pas."""
        assert self._overrides_path is not None

        try:
            self._overrides_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(
                self._overrides_path.write_text,
                self._OVERRIDES_TEMPLATE,
                encoding="utf-8",
            )
            self._logger.info(
                "Fichier template d'overrides créé: {}",
                self._overrides_path,
            )
        except Exception as e:
            self._logger.warning(
                "Impossible de créer le fichier template d'overrides: {}",
                e,
            )

    # ------------------------------------------------------------------------
    # Méthodes internes — Chargement des fichiers
    # ------------------------------------------------------------------------

    async def _load_base_config(self) -> dict[str, Any]:
        """Charge la configuration de base (sites.yaml).

        Returns:
            Document YAML parsé.

        Raises:
            BaseConfigNotFoundError: Si le fichier est introuvable.
            LoaderError: Si le parsing échoue.
        """
        assert self._base_config_path is not None

        if not self._base_config_path.exists():
            raise BaseConfigNotFoundError(self._base_config_path)

        try:
            content = await asyncio.to_thread(
                self._base_config_path.read_text,
                encoding="utf-8",
            )
            document = await asyncio.to_thread(yaml.safe_load, content)

            if not isinstance(document, dict):
                raise LoaderError(
                    f"La configuration de base doit être un dictionnaire, "
                    f"reçu {type(document).__name__}"
                )

            async with self._stats_lock:
                self._base_config_loads += 1
                self._base_config_mtime = await self._get_mtime(self._base_config_path)

            self._logger.debug(
                "Configuration de base chargée: {} sites",
                len(document.get("sites", [])),
            )

            return document

        except yaml.YAMLError as e:
            raise LoaderError(f"Erreur de parsing YAML: {e}") from e
        except Exception as e:
            raise LoaderError(f"Erreur lors du chargement: {e}") from e

    async def _load_overrides(self) -> dict[str, Any] | None:
        """Charge les overrides utilisateur (sites_overrides.yaml).

        Returns:
            Document YAML parsé ou None si le fichier n'existe pas.

        Raises:
            OverridesFileError: Si le fichier est invalide.
        """
        assert self._overrides_path is not None

        if not self._overrides_path.exists():
            self._overrides_present = False
            self._logger.debug("Fichier d'overrides absent, ignoré")
            return None

        self._overrides_present = True

        try:
            content = await asyncio.to_thread(
                self._overrides_path.read_text,
                encoding="utf-8",
            )
            document = await asyncio.to_thread(yaml.safe_load, content)

            if document is None:
                # Fichier vide
                return {}

            if not isinstance(document, dict):
                raise OverridesFileError(
                    self._overrides_path,
                    f"Doit être un dictionnaire, reçu {type(document).__name__}",
                )

            async with self._stats_lock:
                self._overrides_loads += 1
                self._overrides_mtime = await self._get_mtime(self._overrides_path)

            self._logger.debug(
                "Overrides chargés: {} sites modifiés, {} sites ajoutés",
                len(document.get("overrides", {})),
                len(document.get("new_sites", [])),
            )

            return document

        except yaml.YAMLError as e:
            raise OverridesFileError(self._overrides_path, f"Erreur de parsing YAML: {e}") from e
        except Exception as e:
            raise OverridesFileError(self._overrides_path, str(e)) from e

    # ------------------------------------------------------------------------
    # Méthodes internes — Fusion
    # ------------------------------------------------------------------------

    async def _merge_configs(
        self,
        base: dict[str, Any],
        overrides: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Fusionne la configuration de base avec les overrides.

        Args:
            base: Configuration de base.
            overrides: Overrides utilisateur (optionnel).

        Returns:
            Document fusionné.

        Raises:
            MergeError: Si la fusion échoue.
        """
        if overrides is None:
            return base

        try:
            # Copier la base (shallow copy pour les champs top-level)
            merged = dict(base)

            # Fusionner les overrides de sites existants
            site_overrides = overrides.get("overrides", {})
            if site_overrides:
                sites = merged.get("sites", [])
                overridden_count = 0

                for site in sites:
                    site_id = site.get("id")
                    if site_id in site_overrides:
                        # Fusion profonde
                        override = site_overrides[site_id]
                        self._deep_merge(site, override)
                        overridden_count += 1

                merged["sites"] = sites

                async with self._stats_lock:
                    self._total_sites_overridden = overridden_count

            # Ajouter les nouveaux sites
            new_sites = overrides.get("new_sites", [])
            if new_sites:
                if not isinstance(new_sites, list):
                    raise MergeError("'new_sites' doit être une liste")

                sites = merged.get("sites", [])
                sites.extend(new_sites)
                merged["sites"] = sites

                async with self._stats_lock:
                    self._total_sites_added = len(new_sites)

            # Fusionner les autres champs top-level (version, last_updated)
            for key, value in overrides.items():
                if key not in ("overrides", "new_sites"):
                    merged[key] = value

            return merged

        except Exception as e:
            raise MergeError(str(e)) from e

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
        """Fusionne profondément deux dictionnaires (in-place).

        Les valeurs de `override` remplacent celles de `base`.
        Les dictionnaires sont fusionnés récursivement.

        Args:
            base: Dictionnaire de base (modifié in-place).
            override: Dictionnaire d'override.
        """
        for key, value in override.items():
            if (
                key in base
                and isinstance(base[key], dict)
                and isinstance(value, dict)
            ):
                # Fusion récursive
                ConfigLoader._deep_merge(base[key], value)
            else:
                # Remplacement
                base[key] = value

    # ------------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # ------------------------------------------------------------------------

    @staticmethod
    async def _get_mtime(path: Path | None) -> float | None:
        """Récupère le mtime d'un fichier (ou None si inexistant).

        Args:
            path: Chemin du fichier.

        Returns:
            Timestamp de modification ou None.
        """
        if path is None or not path.exists():
            return None

        try:
            stat = await asyncio.to_thread(path.stat)
            return stat.st_mtime
        except Exception:
            return None

    def _ensure_started(self) -> None:
        """Vérifie que le chargeur est démarré."""
        if self._state == LoaderState.IDLE:
            raise LoaderNotStartedError()

    def __repr__(self) -> str:
        return (
            f"<ConfigLoader state={self._state.value} "
            f"base={self._base_config_path.name if self._base_config_path else None} "
            f"overrides={'present' if self._overrides_present else 'absent'}>"
        )


# ============================================================================
# HELPERS PUBLICS — Fonctions utilitaires
# ============================================================================


async def load_sites_config_quick(
    *,
    base_path: Path | None = None,
    overrides_path: Path | None = None,
) -> dict[str, Any]:
    """Charge rapidement la configuration (one-shot, sans cache).

    Fonction utilitaire pour des chargements ponctuels sans gestion
    de lifecycle.

    Args:
        base_path: Chemin vers la configuration de base (optionnel).
        overrides_path: Chemin vers les overrides (optionnel).

    Returns:
        Document fusionné.
    """
    config = LoaderConfig(
        base_config_path=base_path,
        overrides_path=overrides_path,
        cache_enabled=False,
    )

    async with ConfigLoader(config=config) as loader:
        return await loader.load()


def get_default_overrides_path() -> Path:
    """Retourne le chemin par défaut du fichier d'overrides.

    Returns:
        Chemin vers ~/.config/nexusdl/sites_overrides.yaml.
    """
    return Path.home() / ".config" / "nexusdl" / "sites_overrides.yaml"


def get_embedded_base_config_path() -> Path | None:
    """Retourne le chemin de la configuration de base embarquée.

    Returns:
        Chemin vers le fichier embarqué ou None si introuvable.
    """
    try:
        data_resource = importlib.resources.files("nexusdl.core.registry")
        return Path(str(data_resource / "sites.yaml"))
    except Exception:
        return None


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "LoaderError",
    "LoaderNotStartedError",
    "BaseConfigNotFoundError",
    "OverridesFileError",
    "MergeError",
    # Enums
    "LoaderState",
    # Modèles
    "LoaderConfig",
    "LoaderStats",
    # Classe principale
    "ConfigLoader",
    # Helpers
    "load_sites_config_quick",
    "get_default_overrides_path",
    "get_embedded_base_config_path",
]
