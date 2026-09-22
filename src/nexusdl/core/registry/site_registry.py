"""Registre central des sites supportés par NexusDL.

Ce module constitue le point d'entrée unique pour accéder aux configurations
des sites de manga/webtoon/comics supportés. Il orchestre :

    1. Le chargement de la configuration (via `loader.py`)
    2. La validation contre le schéma JSON Schema (via `validator.py`)
    3. L'indexation multi-critères (par ID, langue, tag, domaine)
    4. La résolution lazy des parsers (via `importlib`)
    5. Le caching des instances de parsers
    6. Le rechargement à chaud (hot-reload) sans redémarrage
    7. L'émission d'événements via l'EventBus

Architecture :
    SiteRegistry
        ├── ConfigLoader (charge sites.yaml + overrides)
        ├── SchemaValidator (valide le document)
        ├── SiteConfig[] (modèles Pydantic parsés)
        ├── Indexes (dicts pour O(1) lookup)
        │   ├── _by_id : str → SiteConfig
        │   ├── _by_language : Language → list[SiteConfig]
        │   ├── _by_tag : str → list[SiteConfig]
        │   ├── _by_domain : str → SiteConfig
        │   └── _by_parser_module : str → type[BaseParser]
        ├── ParserCache (instances de parsers instanciés)
        └── EventBus (notifications de chargement/rechargement)

Thread-safety :
    Toutes les opérations de lecture sont thread-safe (dicts immuables après init).
    Les opérations d'écriture (reload, clear_cache) sont protégées par un lock asyncio.

Exemple d'utilisation :
    >>> registry = SiteRegistry(
    ...     loader=config_loader,
    ...     validator=schema_validator,
    ...     event_bus=event_bus,
    ... )
    >>> await registry.start()
    >>>
    >>> # Accès par ID
    >>> site = registry.get_site("mangadex")
    >>> print(site.name)  # "MangaDex"
    >>>
    >>> # Accès par langue
    >>> fr_sites = registry.get_sites_by_language(Language.FR)
    >>> print(f"{len(fr_sites)} sites francophones")
    >>>
    >>> # Résolution du parser (lazy loading)
    >>> parser = await registry.get_parser("mangadex")
    >>> results = await parser.search("one piece")
    >>>
    >>> # Rechargement à chaud
    >>> await registry.reload()
    >>>
    >>> await registry.stop()
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import time
from collections import defaultdict
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final, Self

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.events import EventBus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.models.manga import Language
from nexusdl.core.models.site import (
    DomainInfo,
    SiteCapabilities,
    SiteConfig,
    SiteHealth,
    SiteOverview,
    SiteStats,
    SiteStatus,
)

if TYPE_CHECKING:
    from nexusdl.core.registry.loader import ConfigLoader
    from nexusdl.core.registry.validator import SchemaValidator, ValidationMode
    from nexusdl.parsers.base import BaseParser


# ============================================================================
# EXCEPTIONS
# ============================================================================


class RegistryError(NexusDLError):
    """Exception de base pour les erreurs du registre."""


class RegistryNotStartedError(RegistryError):
    """Exception levée lorsqu'on utilise le registre avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "SiteRegistry must be started before use. Call await registry.start()"
        )


class SiteNotFoundError(RegistryError):
    """Exception levée lorsqu'un site demandé n'existe pas."""

    def __init__(self, site_id: str) -> None:
        super().__init__(f"Site introuvable: {site_id}")
        self.site_id = site_id


class ParserLoadError(RegistryError):
    """Exception levée lorsqu'un parser ne peut être chargé."""

    def __init__(self, site_id: str, reason: str = "") -> None:
        msg = f"Impossible de charger le parser pour {site_id}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.site_id = site_id
        self.reason = reason


class ParserInstantiationError(RegistryError):
    """Exception levée lorsqu'un parser ne peut être instancié."""

    def __init__(self, site_id: str, parser_class: str, reason: str = "") -> None:
        msg = f"Impossible d'instancier le parser {parser_class} pour {site_id}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.site_id = site_id
        self.parser_class = parser_class
        self.reason = reason


class ConfigurationLoadError(RegistryError):
    """Exception levée lorsque la configuration ne peut être chargée."""

    def __init__(self, reason: str = "") -> None:
        msg = "Impossible de charger la configuration des sites"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


class ConfigurationValidationError(RegistryError):
    """Exception levée lorsque la configuration est invalide."""

    def __init__(self, errors_count: int, warnings_count: int = 0) -> None:
        super().__init__(
            f"Configuration invalide: {errors_count} erreurs, {warnings_count} warnings"
        )
        self.errors_count = errors_count
        self.warnings_count = warnings_count


# ============================================================================
# ENUMS
# ============================================================================


class RegistryState(str, Enum):
    """État du registre.

    UNINITIALIZED : Registre créé mais pas encore démarré.
    LOADING       : Configuration en cours de chargement.
    READY         : Registre opérationnel.
    RELOADING     : Rechargement en cours.
    ERROR         : Erreur lors du chargement.
    STOPPED       : Registre arrêté.
    """

    UNINITIALIZED = "uninitialized"
    LOADING = "loading"
    READY = "ready"
    RELOADING = "reloading"
    ERROR = "error"
    STOPPED = "stopped"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            RegistryState.UNINITIALIZED: "Non initialisé",
            RegistryState.LOADING: "Chargement",
            RegistryState.READY: "Prêt",
            RegistryState.RELOADING: "Rechargement",
            RegistryState.ERROR: "Erreur",
            RegistryState.STOPPED: "Arrêté",
        }[self]

    @property
    def is_operational(self) -> bool:
        """Indique si le registre est opérationnel."""
        return self in (RegistryState.READY, RegistryState.RELOADING)


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class RegistryStats(BaseModel):
    """Statistiques agrégées du registre."""

    state: RegistryState = Field(..., description="État actuel du registre.")
    sites_count: int = Field(default=0, ge=0, description="Nombre total de sites chargés.")
    enabled_sites_count: int = Field(default=0, ge=0, description="Nombre de sites activés.")
    disabled_sites_count: int = Field(default=0, ge=0, description="Nombre de sites désactivés.")
    adult_sites_count: int = Field(default=0, ge=0, description="Nombre de sites adultes (18+).")
    languages_count: int = Field(default=0, ge=0, description="Nombre de langues distinctes.")
    parsers_loaded: int = Field(default=0, ge=0, description="Nombre de parsers instanciés.")
    parsers_cache_hits: int = Field(default=0, ge=0, description="Nombre de hits du cache parsers.")
    parsers_cache_misses: int = Field(default=0, ge=0, description="Nombre de misses du cache parsers.")
    parser_load_errors: int = Field(default=0, ge=0, description="Nombre d'erreurs de chargement de parsers.")
    total_access_count: int = Field(default=0, ge=0, description="Nombre total d'accès au registre.")
    last_reload_at: datetime | None = Field(
        default=None,
        description="Timestamp du dernier rechargement.",
    )
    config_version: str = Field(default="", description="Version de la configuration chargée.")
    config_last_updated: str = Field(default="", description="Date de dernière mise à jour de la config.")
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def parser_cache_hit_rate(self) -> float:
        """Taux de hit du cache parsers (0.0 à 1.0)."""
        total = self.parsers_cache_hits + self.parsers_cache_misses
        if total == 0:
            return 0.0
        return self.parsers_cache_hits / total


class ParserInfo(BaseModel):
    """Informations sur un parser chargé."""

    site_id: str = Field(..., description="ID du site.")
    parser_class: str = Field(..., description="Référence de la classe de parser.")
    module_path: str = Field(..., description="Chemin du module Python.")
    class_name: str = Field(..., description="Nom de la classe.")
    loaded_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de chargement.",
    )
    is_cached: bool = Field(
        default=False,
        description="True si l'instance est en cache.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# CLASSE PRINCIPALE — SiteRegistry
# ============================================================================


class SiteRegistry:
    """Registre central des sites supportés.

    Gère le cycle de vie complet des configurations de sites :
        - Chargement depuis YAML (via ConfigLoader)
        - Validation (via SchemaValidator)
        - Parsing en modèles Pydantic (SiteConfig)
        - Indexation multi-critères
        - Résolution lazy des parsers
        - Caching des instances de parsers
        - Rechargement à chaud

    Lifecycle :
        >>> registry = SiteRegistry(loader=loader, validator=validator)
        >>> await registry.start()
        >>> # ... utilisation ...
        >>> await registry.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Les opérations de lecture sont thread-safe (dicts immuables).
        Les opérations d'écriture sont protégées par un lock asyncio.
    """

    # Constantes
    _DEFAULT_PARSER_CACHE_SIZE: Final[int] = 100

    def __init__(
        self,
        loader: ConfigLoader,
        validator: SchemaValidator,
        *,
        event_bus: EventBus | None = None,
        parser_cache_size: int = _DEFAULT_PARSER_CACHE_SIZE,
        auto_reload_on_change: bool = False,
    ) -> None:
        """Initialise le registre.

        Args:
            loader: Chargeur de configuration (YAML + overrides).
            validator: Validateur JSON Schema.
            event_bus: Bus d'événements pour notifications (optionnel).
            parser_cache_size: Taille maximale du cache de parsers.
            auto_reload_on_change: Recharger automatiquement si les fichiers
                                   de config changent (non implémenté pour l'instant).
        """
        if parser_cache_size <= 0:
            raise ValueError(f"parser_cache_size must be positive, got {parser_cache_size}")

        self._loader = loader
        self._validator = validator
        self._event_bus = event_bus
        self._parser_cache_size = parser_cache_size
        self._auto_reload_on_change = auto_reload_on_change

        # État
        self._state: RegistryState = RegistryState.UNINITIALIZED
        self._state_lock = asyncio.Lock()

        # Données chargées
        self._sites: list[SiteConfig] = []
        self._sites_by_id: dict[str, SiteConfig] = {}
        self._sites_by_language: dict[str, list[SiteConfig]] = defaultdict(list)
        self._sites_by_tag: dict[str, list[SiteConfig]] = defaultdict(list)
        self._sites_by_domain: dict[str, SiteConfig] = {}

        # Cache des parsers
        self._parser_classes: dict[str, type[BaseParser]] = {}
        self._parser_instances: dict[str, BaseParser] = {}
        self._parser_lock = asyncio.Lock()

        # Statistiques
        self._parsers_cache_hits: int = 0
        self._parsers_cache_misses: int = 0
        self._parser_load_errors: int = 0
        self._total_access_count: int = 0
        self._start_time: float = 0.0
        self._last_reload_at: datetime | None = None
        self._config_version: str = ""
        self._config_last_updated: str = ""
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="site_registry")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre le registre et charge la configuration.

        Raises:
            ConfigurationLoadError: Si la configuration ne peut être chargée.
            ConfigurationValidationError: Si la configuration est invalide.
        """
        async with self._state_lock:
            if self._state == RegistryState.READY:
                self._logger.warning("SiteRegistry déjà démarré, ignore")
                return
            if self._state == RegistryState.LOADING:
                self._logger.warning("SiteRegistry déjà en cours de chargement")
                return

            self._state = RegistryState.LOADING

        try:
            await self._load_and_validate()
            self._state = RegistryState.READY
            self._start_time = time.monotonic()

            self._logger.info(
                "SiteRegistry démarré: {} sites ({} activés, {} langues)",
                len(self._sites),
                sum(1 for s in self._sites if s.enabled),
                len(self._sites_by_language),
            )

            # Émettre un événement de démarrage
            if self._event_bus is not None:
                await self._event_bus.emit(
                    "registry.started",
                    {
                        "sites_count": len(self._sites),
                        "enabled_count": sum(1 for s in self._sites if s.enabled),
                    },
                )

        except Exception as e:
            async with self._state_lock:
                self._state = RegistryState.ERROR
            self._logger.error("Échec du démarrage du SiteRegistry: {}", e)
            raise

    async def stop(self) -> None:
        """Arrête le registre et libère les ressources."""
        async with self._state_lock:
            if self._state == RegistryState.STOPPED:
                return
            self._state = RegistryState.STOPPED

        # Vider les caches
        async with self._parser_lock:
            self._parser_instances.clear()
            self._parser_classes.clear()

        self._sites.clear()
        self._sites_by_id.clear()
        self._sites_by_language.clear()
        self._sites_by_tag.clear()
        self._sites_by_domain.clear()

        if self._event_bus is not None:
            await self._event_bus.emit("registry.stopped", {})

        self._logger.info("SiteRegistry arrêté")

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------------

    @property
    def state(self) -> RegistryState:
        """État actuel du registre."""
        return self._state

    @property
    def is_ready(self) -> bool:
        """Indique si le registre est opérationnel."""
        return self._state == RegistryState.READY

    @property
    def sites_count(self) -> int:
        """Nombre total de sites chargés."""
        return len(self._sites)

    @property
    def enabled_sites_count(self) -> int:
        """Nombre de sites activés."""
        return sum(1 for s in self._sites if s.enabled)

    # ------------------------------------------------------------------------
    # API publique — Accès aux sites
    # ------------------------------------------------------------------------

    def get_site(self, site_id: str) -> SiteConfig:
        """Récupère la configuration d'un site par son ID.

        Args:
            site_id: Identifiant unique du site.

        Returns:
            Configuration du site.

        Raises:
            RegistryNotStartedError: Si le registre n'est pas démarré.
            SiteNotFoundError: Si le site n'existe pas.
        """
        self._ensure_ready()
        self._increment_access_count()

        site = self._sites_by_id.get(site_id)
        if site is None:
            raise SiteNotFoundError(site_id)
        return site

    def get_site_or_none(self, site_id: str) -> SiteConfig | None:
        """Récupère la configuration d'un site par son ID, ou None si inexistant.

        Args:
            site_id: Identifiant unique du site.

        Returns:
            Configuration du site ou None.
        """
        self._ensure_ready()
        self._increment_access_count()
        return self._sites_by_id.get(site_id)

    def get_sites_by_language(
        self,
        language: Language | str,
        *,
        enabled_only: bool = True,
    ) -> list[SiteConfig]:
        """Récupère les sites pour une langue donnée.

        Args:
            language: Langue (enum ou code ISO 639-1).
            enabled_only: Si True, ne retourne que les sites activés.

        Returns:
            Liste des sites correspondants, triés par priorité.
        """
        self._ensure_ready()
        self._increment_access_count()

        lang_code = language.value if isinstance(language, Language) else language
        sites = self._sites_by_language.get(lang_code, [])

        # Inclure aussi les sites multilingues
        if lang_code != Language.MULTI.value:
            multi_sites = self._sites_by_language.get(Language.MULTI.value, [])
            sites = sites + multi_sites

        if enabled_only:
            sites = [s for s in sites if s.enabled]

        return sorted(sites, key=lambda s: s.priority)

    def get_sites_by_tag(
        self,
        tag: str,
        *,
        enabled_only: bool = True,
    ) -> list[SiteConfig]:
        """Récupère les sites pour un tag donné.

        Args:
            tag: Tag à rechercher (case-insensitive).
            enabled_only: Si True, ne retourne que les sites activés.

        Returns:
            Liste des sites correspondants.
        """
        self._ensure_ready()
        self._increment_access_count()

        tag_lower = tag.lower()
        sites = self._sites_by_tag.get(tag_lower, [])

        if enabled_only:
            sites = [s for s in sites if s.enabled]

        return sorted(sites, key=lambda s: s.priority)

    def get_site_by_domain(self, domain: str) -> SiteConfig | None:
        """Résout un site à partir d'un domaine.

        Args:
            domain: Nom de domaine (ex: "mangadex.org").

        Returns:
            Configuration du site ou None si non trouvé.
        """
        self._ensure_ready()
        self._increment_access_count()

        # Normaliser le domaine
        domain_lower = domain.lower()
        if domain_lower.startswith(("http://", "https://")):
            # Extraire le host depuis l'URL
            from urllib.parse import urlparse
            parsed = urlparse(domain_lower)
            domain_lower = parsed.netloc

        return self._sites_by_domain.get(domain_lower)

    def list_sites(
        self,
        *,
        enabled_only: bool = False,
        include_adult: bool = True,
    ) -> list[SiteConfig]:
        """Liste tous les sites chargés.

        Args:
            enabled_only: Si True, ne retourne que les sites activés.
            include_adult: Si False, exclut les sites adultes (18+).

        Returns:
            Liste des sites, triés par priorité puis par nom.
        """
        self._ensure_ready()
        self._increment_access_count()

        sites = list(self._sites)

        if enabled_only:
            sites = [s for s in sites if s.enabled]

        if not include_adult:
            sites = [s for s in sites if not s.is_adult]

        return sorted(sites, key=lambda s: (s.priority, s.name))

    def list_languages(self) -> list[Language]:
        """Liste toutes les langues disponibles.

        Returns:
            Liste des langues avec au moins un site.
        """
        self._ensure_ready()
        self._increment_access_count()

        languages: set[Language] = set()
        for lang_code in self._sites_by_language.keys():
            try:
                languages.add(Language(lang_code))
            except ValueError:
                pass

        return sorted(languages, key=lambda l: l.value)

    def list_tags(self) -> list[str]:
        """Liste tous les tags disponibles.

        Returns:
            Liste triée des tags.
        """
        self._ensure_ready()
        self._increment_access_count()
        return sorted(self._sites_by_tag.keys())

    # ------------------------------------------------------------------------
    # API publique — Résolution des parsers
    # ------------------------------------------------------------------------

    async def get_parser(self, site_id: str) -> BaseParser:
        """Résout et instancie le parser pour un site donné.

        Le parser est chargé à la demande (lazy loading) et mis en cache
        pour les appels suivants.

        Args:
            site_id: Identifiant du site.

        Returns:
            Instance du parser, prête à l'emploi.

        Raises:
            RegistryNotStartedError: Si le registre n'est pas démarré.
            SiteNotFoundError: Si le site n'existe pas.
            ParserLoadError: Si le parser ne peut être chargé.
            ParserInstantiationError: Si le parser ne peut être instancié.
        """
        self._ensure_ready()
        self._increment_access_count()

        site = self.get_site(site_id)

        # Vérifier le cache d'instances
        async with self._parser_lock:
            if site_id in self._parser_instances:
                self._parsers_cache_hits += 1
                return self._parser_instances[site_id]

            self._parsers_cache_misses += 1

        # Charger la classe du parser
        parser_class = await self._load_parser_class(site)

        # Instancier le parser
        try:
            parser = parser_class(site)
        except Exception as e:
            self._parser_load_errors += 1
            raise ParserInstantiationError(
                site_id,
                site.parser_class,
                str(e),
            ) from e

        # Mettre en cache
        async with self._parser_lock:
            # Vérifier à nouveau (peut avoir été ajouté entre-temps)
            if site_id not in self._parser_instances:
                self._parser_instances[site_id] = parser
                self._parser_classes[site_id] = parser_class

                # Limiter la taille du cache
                if len(self._parser_instances) > self._parser_cache_size:
                    await self._evict_parser_cache()

        self._logger.debug(
            "Parser chargé pour {}: {}",
            site_id,
            site.parser_class,
        )

        return parser

    async def get_parser_class(self, site_id: str) -> type[BaseParser]:
        """Résout la classe du parser pour un site donné (sans instanciation).

        Args:
            site_id: Identifiant du site.

        Returns:
            Classe du parser.

        Raises:
            ParserLoadError: Si la classe ne peut être chargée.
        """
        self._ensure_ready()
        site = self.get_site(site_id)
        return await self._load_parser_class(site)

    async def preload_parsers(self, site_ids: list[str] | None = None) -> int:
        """Précharge les parsers pour une liste de sites.

        Utile pour éviter les latences au premier accès.

        Args:
            site_ids: Liste des IDs de sites à précharger.
                      Si None, précharge tous les sites activés.

        Returns:
            Nombre de parsers préchargés avec succès.
        """
        self._ensure_ready()

        if site_ids is None:
            site_ids = [s.id for s in self._sites if s.enabled]

        loaded = 0
        for site_id in site_ids:
            try:
                await self.get_parser(site_id)
                loaded += 1
            except Exception as e:
                self._logger.warning(
                    "Échec du préchargement du parser pour {}: {}",
                    site_id,
                    e,
                )

        self._logger.info("Préchargement terminé: {}/{} parsers", loaded, len(site_ids))
        return loaded

    def clear_parser_cache(self) -> int:
        """Vide le cache des instances de parsers.

        Les classes de parsers restent en cache.

        Returns:
            Nombre d'instances supprimées.
        """
        # Note: cette méthode est synchrone car le cache est protégé
        # par un lock asyncio, mais l'opération est rapide
        count = len(self._parser_instances)
        self._parser_instances.clear()
        self._logger.info("Cache des parsers vidé: {} instances supprimées", count)
        return count

    # ------------------------------------------------------------------------
    # API publique — Rechargement
    # ------------------------------------------------------------------------

    async def reload(self) -> int:
        """Recharge la configuration des sites.

        Préserve le cache des parsers déjà instanciés si les configurations
        n'ont pas changé.

        Returns:
            Nombre de sites chargés.

        Raises:
            ConfigurationLoadError: Si la configuration ne peut être chargée.
            ConfigurationValidationError: Si la configuration est invalide.
        """
        async with self._state_lock:
            if self._state not in (RegistryState.READY, RegistryState.ERROR):
                raise RegistryError(
                    f"Impossible de recharger depuis l'état {self._state.value}"
                )
            self._state = RegistryState.RELOADING

        try:
            # Sauvegarder les anciens parsers
            old_parser_instances = dict(self._parser_instances)

            # Recharger
            await self._load_and_validate()

            # Restaurer les parsers compatibles
            async with self._parser_lock:
                self._parser_instances.clear()
                for site_id, parser in old_parser_instances.items():
                    if site_id in self._sites_by_id:
                        new_site = self._sites_by_id[site_id]
                        # Vérifier que la classe n'a pas changé
                        if new_site.parser_class == parser.__class__.__module__ + ":" + parser.__class__.__name__:
                            self._parser_instances[site_id] = parser

            self._last_reload_at = datetime.now(UTC)
            self._state = RegistryState.READY

            self._logger.info(
                "Rechargement terminé: {} sites, {} parsers restaurés",
                len(self._sites),
                len(self._parser_instances),
            )

            if self._event_bus is not None:
                await self._event_bus.emit(
                    "registry.reloaded",
                    {
                        "sites_count": len(self._sites),
                        "parsers_restored": len(self._parser_instances),
                    },
                )

            return len(self._sites)

        except Exception as e:
            async with self._state_lock:
                self._state = RegistryState.ERROR
            self._logger.error("Échec du rechargement: {}", e)
            raise

    # ------------------------------------------------------------------------
    # API publique — Statistiques et overview
    # ------------------------------------------------------------------------

    async def get_stats(self) -> RegistryStats:
        """Retourne les statistiques agrégées du registre."""
        async with self._stats_lock:
            uptime = 0.0
            if self._start_time > 0:
                uptime = time.monotonic() - self._start_time

            return RegistryStats(
                state=self._state,
                sites_count=len(self._sites),
                enabled_sites_count=sum(1 for s in self._sites if s.enabled),
                disabled_sites_count=sum(1 for s in self._sites if not s.enabled),
                adult_sites_count=sum(1 for s in self._sites if s.is_adult),
                languages_count=len(self._sites_by_language),
                parsers_loaded=len(self._parser_instances),
                parsers_cache_hits=self._parsers_cache_hits,
                parsers_cache_misses=self._parsers_cache_misses,
                parser_load_errors=self._parser_load_errors,
                total_access_count=self._total_access_count,
                last_reload_at=self._last_reload_at,
                config_version=self._config_version,
                config_last_updated=self._config_last_updated,
                uptime_seconds=uptime,
            )

    def get_site_overview(self, site_id: str) -> SiteOverview:
        """Retourne une vue d'ensemble complète d'un site.

        Combine la configuration, la santé et les statistiques.

        Args:
            site_id: Identifiant du site.

        Returns:
            Vue d'ensemble du site.
        """
        site = self.get_site(site_id)

        # Santé par défaut (inconnue)
        health = SiteHealth(
            site_id=site_id,
            status=SiteStatus.UNKNOWN,
        )

        # Stats par défaut (vides)
        stats = SiteStats(site_id=site_id)

        return SiteOverview(
            config=site,
            health=health,
            stats=stats,
            is_favorite=False,
        )

    def get_parser_info(self, site_id: str) -> ParserInfo | None:
        """Retourne les informations sur le parser d'un site.

        Args:
            site_id: Identifiant du site.

        Returns:
            Informations sur le parser ou None si non chargé.
        """
        self._ensure_ready()

        if site_id not in self._sites_by_id:
            raise SiteNotFoundError(site_id)

        site = self._sites_by_id[site_id]
        module_path = site.parser_class.split(":", 1)[0]
        class_name = site.parser_class.split(":", 1)[1]

        return ParserInfo(
            site_id=site_id,
            parser_class=site.parser_class,
            module_path=module_path,
            class_name=class_name,
            is_cached=site_id in self._parser_instances,
        )

    # ------------------------------------------------------------------------
    # Méthodes internes — Chargement et validation
    # ------------------------------------------------------------------------

    async def _load_and_validate(self) -> None:
        """Charge et valide la configuration des sites."""
        # 1. Charger le document YAML
        try:
            document = await self._loader.load()
        except Exception as e:
            raise ConfigurationLoadError(str(e)) from e

        # 2. Valider le document
        try:
            validation_result = await self._validator.validate_document(document)
        except Exception as e:
            raise ConfigurationLoadError(f"Erreur de validation: {e}") from e

        if not validation_result.is_valid:
            errors = validation_result.errors_count
            warnings = validation_result.warnings_count
            self._logger.error(
                "Configuration invalide: {} erreurs, {} warnings",
                errors,
                warnings,
            )
            for issue in validation_result.issues:
                if issue.is_blocking:
                    self._logger.error("  {} {}", issue.severity.icon, issue.format_for_display())
            raise ConfigurationValidationError(errors, warnings)

        if validation_result.warnings_count > 0:
            self._logger.warning(
                "Configuration valide avec {} warnings",
                validation_result.warnings_count,
            )

        # 3. Parser en modèles SiteConfig
        try:
            sites = self._parse_sites(document)
        except Exception as e:
            raise ConfigurationLoadError(f"Erreur de parsing: {e}") from e

        # 4. Indexer
        self._index_sites(sites)

        # 5. Mettre à jour les métadonnées de config
        self._config_version = str(document.get("version", ""))
        self._config_last_updated = str(document.get("last_updated", ""))

        self._logger.debug(
            "Configuration chargée: {} sites, version {}",
            len(sites),
            self._config_version,
        )

    def _parse_sites(self, document: dict[str, Any]) -> list[SiteConfig]:
        """Parse le document YAML en liste de SiteConfig.

        Args:
            document: Document YAML validé.

        Returns:
            Liste de SiteConfig.

        Raises:
            ConfigurationLoadError: Si le parsing échoue.
        """
        sites_data = document.get("sites", [])
        if not isinstance(sites_data, list):
            raise ConfigurationLoadError("Le champ 'sites' doit être une liste")

        sites: list[SiteConfig] = []
        for index, site_data in enumerate(sites_data):
            try:
                site = self._parse_site_config(site_data)
                sites.append(site)
            except Exception as e:
                site_id = site_data.get("id", f"index_{index}")
                raise ConfigurationLoadError(
                    f"Erreur de parsing du site {site_id}: {e}"
                ) from e

        return sites

    def _parse_site_config(self, data: dict[str, Any]) -> SiteConfig:
        """Parse un site individuel en SiteConfig.

        Args:
            data: Données du site.

        Returns:
            Instance de SiteConfig.
        """
        # Parser les domaines
        domains_data = data.get("domains", [])
        domains: list[DomainInfo] = []
        for i, domain_url in enumerate(domains_data):
            role = "primary" if i == 0 else "mirror"
            domains.append(
                DomainInfo(
                    url=domain_url,
                    role=role,
                    is_primary=(i == 0),
                )
            )

        # Parser les capacités
        capabilities_data = data.get("capabilities", {})
        capabilities = SiteCapabilities(**capabilities_data)

        # Mapper le language
        language_str = data.get("language", "en")
        try:
            language = Language(language_str)
        except ValueError:
            language = Language.EN

        # Mapper le content_rating
        from nexusdl.core.models.manga import ContentRating
        content_rating_str = data.get("content_rating", "safe")
        try:
            content_rating = ContentRating(content_rating_str)
        except ValueError:
            content_rating = ContentRating.SAFE

        return SiteConfig(
            id=data["id"],
            name=data["name"],
            domains=domains,
            parser_class=data["parser_class"],
            language=language,
            adult=data.get("adult", False),
            content_rating=content_rating,
            capabilities=capabilities,
            default_headers=data.get("default_headers", {}),
            cookies_required=data.get("cookies_required", []),
            region_locked=data.get("region_locked", False),
            allowed_regions=data.get("allowed_regions", []),
            scanlation_group=data.get("scanlation_group"),
            tags=data.get("tags", []),
            notes=data.get("notes"),
            enabled=data.get("enabled", True),
            priority=data.get("priority", 100),
        )

    # ------------------------------------------------------------------------
    # Méthodes internes — Indexation
    # ------------------------------------------------------------------------

    def _index_sites(self, sites: list[SiteConfig]) -> None:
        """Construit les index multi-critères.

        Args:
            sites: Liste des sites à indexer.
        """
        self._sites = sites
        self._sites_by_id.clear()
        self._sites_by_language.clear()
        self._sites_by_tag.clear()
        self._sites_by_domain.clear()

        for site in sites:
            # Index par ID
            self._sites_by_id[site.id] = site

            # Index par langue
            self._sites_by_language[site.language.value].append(site)

            # Index par tags
            for tag in site.tags:
                self._sites_by_tag[tag.lower()].append(site)

            # Index par domaines
            for domain in site.domains:
                domain_host = domain.host.lower()
                self._sites_by_domain[domain_host] = site

        # Rendre les dicts de liste immuables (vue lecture)
        # Note: on ne les freeze pas vraiment, mais on les considère comme read-only

    # ------------------------------------------------------------------------
    # Méthodes internes — Résolution de parsers
    # ------------------------------------------------------------------------

    async def _load_parser_class(self, site: SiteConfig) -> type[BaseParser]:
        """Charge la classe du parser pour un site.

        Utilise importlib pour charger dynamiquement le module Python
        et extraire la classe référencée.

        Args:
            site: Configuration du site.

        Returns:
            Classe du parser.

        Raises:
            ParserLoadError: Si le chargement échoue.
        """
        # Vérifier le cache de classes
        if site.id in self._parser_classes:
            return self._parser_classes[site.id]

        # Parser la référence
        try:
            module_path, class_name = site.parser_class.split(":", 1)
        except ValueError as e:
            raise ParserLoadError(
                site.id,
                f"Format invalide: {site.parser_class} (attendu: 'module:Class')",
            ) from e

        # Charger le module
        try:
            module = await asyncio.to_thread(importlib.import_module, module_path)
        except ImportError as e:
            self._parser_load_errors += 1
            raise ParserLoadError(
                site.id,
                f"Module introuvable: {module_path} ({e})",
            ) from e
        except Exception as e:
            self._parser_load_errors += 1
            raise ParserLoadError(
                site.id,
                f"Erreur lors du chargement du module {module_path}: {e}",
            ) from e

        # Extraire la classe
        try:
            parser_class = getattr(module, class_name)
        except AttributeError as e:
            self._parser_load_errors += 1
            raise ParserLoadError(
                site.id,
                f"Classe introuvable: {class_name} dans {module_path}",
            ) from e

        # Vérifier que c'est bien une classe
        if not isinstance(parser_class, type):
            self._parser_load_errors += 1
            raise ParserLoadError(
                site.id,
                f"{class_name} n'est pas une classe",
            )

        # Mettre en cache
        async with self._parser_lock:
            self._parser_classes[site.id] = parser_class

        return parser_class

    async def _evict_parser_cache(self) -> None:
        """Évince les parsers les moins récemment utilisés du cache.

        Stratégie simple : supprimer la moitié du cache quand il est plein.
        """
        if len(self._parser_instances) <= self._parser_cache_size // 2:
            return

        # Supprimer la moitié la plus ancienne
        # (en pratique, on pourrait utiliser un LRU, mais c'est suffisant)
        to_remove = len(self._parser_instances) - (self._parser_cache_size // 2)
        keys_to_remove = list(self._parser_instances.keys())[:to_remove]

        for key in keys_to_remove:
            del self._parser_instances[key]

        self._logger.debug(
            "Cache parsers évicté: {} instances supprimées",
            to_remove,
        )

    # ------------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # ------------------------------------------------------------------------

    def _ensure_ready(self) -> None:
        """Vérifie que le registre est opérationnel."""
        if self._state != RegistryState.READY:
            raise RegistryNotStartedError()

    def _increment_access_count(self) -> None:
        """Incrémente le compteur d'accès."""
        # Pas de lock nécessaire pour un simple incrément
        self._total_access_count += 1

    def __repr__(self) -> str:
        return (
            f"<SiteRegistry state={self._state.value} "
            f"sites={len(self._sites)} "
            f"parsers_cached={len(self._parser_instances)}>"
        )

    def __len__(self) -> int:
        """Nombre de sites chargés."""
        return len(self._sites)

    def __contains__(self, site_id: str) -> bool:
        """Vérifie si un site est chargé."""
        return site_id in self._sites_by_id

    def __iter__(self):
        """Itère sur les sites chargés."""
        return iter(self._sites)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "RegistryError",
    "RegistryNotStartedError",
    "SiteNotFoundError",
    "ParserLoadError",
    "ParserInstantiationError",
    "ConfigurationLoadError",
    "ConfigurationValidationError",
    # Enums
    "RegistryState",
    # Modèles
    "RegistryStats",
    "ParserInfo",
    # Classe principale
    "SiteRegistry",
]
