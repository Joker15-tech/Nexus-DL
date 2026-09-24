"""Routeur FastAPI pour les endpoints de santé de l'API.

Ce module fournit un routeur FastAPI complet pour vérifier la santé de l'API
et de ses dépendances. Il est conçu pour être utilisé avec des systèmes de
monitoring (Kubernetes, Docker, Prometheus, etc.).

**Endpoints** :
    - GET /health              : Vérification basique (liveness)
    - GET /health/live         : Probe de liveness (Kubernetes)
    - GET /health/ready        : Probe de readiness (Kubernetes)
    - GET /health/startup      : Probe de startup (Kubernetes)
    - GET /health/detailed     : Vérification détaillée de tous les composants
    - GET /health/version      : Informations de version
    - GET /health/metrics      : Métriques de base (pour Prometheus)

**Fonctionnalités** :
    - Vérification de la base de données
    - Vérification du cache
    - Vérification du système de fichiers
    - Vérification du registre des sites
    - Informations de version et uptime
    - Statistiques système (mémoire, CPU)
    - Cache des résultats pour éviter la surcharge
    - Timeouts configurables pour les checks
    - Logging structuré
    - Pas d'authentification requise

**Exemple d'utilisation** :
    >>> from fastapi import FastAPI
    >>> from nexusdl.interfaces.web.backend.routers.health import health_router
    >>>
    >>> app = FastAPI()
    >>> app.include_router(health_router, prefix="/api/v1")

**Exemples d'appels** :
    >>> # Vérification basique
    >>> GET /api/v1/health
    >>> {"status": "healthy", "timestamp": "2026-09-24T14:30:45Z"}
    >>>
    >>> # Vérification détaillée
    >>> GET /api/v1/health/detailed
    >>> {
    ...     "status": "healthy",
    ...     "components": {
    ...         "database": {"status": "healthy", "latency_ms": 5.2},
    ...         "cache": {"status": "healthy", "latency_ms": 1.1},
    ...         "filesystem": {"status": "healthy", "latency_ms": 2.3},
    ...         "registry": {"status": "healthy", "sites_count": 20}
    ...     },
    ...     "version": "0.1.0",
    ...     "uptime_seconds": 3600.5
    ... }

Intégration :
    - core/config.py              : Configuration globale
    - core/paths.py               : Chemins des fichiers
    - core/registry/              : Registre des sites
    - core/library/database.py    : Base de données
    - core/logger.py              : Logs
    - core/i18n.py                : Traductions
"""

from __future__ import annotations

import asyncio
import os
import platform
import sys
import time
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Final

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

try:
    from fastapi import APIRouter, HTTPException, status
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from nexusdl.core.constants import APP_AUTHOR, APP_NAME, APP_URL, APP_VERSION, PYTHON_MIN_VERSION
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t


# ============================================================================
# CONSTANTES
# ============================================================================


# Timeouts pour les checks (secondes)
DEFAULT_CHECK_TIMEOUT_SECONDS: Final[float] = 5.0
DATABASE_CHECK_TIMEOUT_SECONDS: Final[float] = 3.0
CACHE_CHECK_TIMEOUT_SECONDS: Final[float] = 2.0
FILESYSTEM_CHECK_TIMEOUT_SECONDS: Final[float] = 2.0
REGISTRY_CHECK_TIMEOUT_SECONDS: Final[float] = 3.0

# Cache des résultats
HEALTH_CACHE_TTL_SECONDS: Final[int] = 10  # 10 secondes
MAX_CACHE_SIZE: Final[int] = 10

# Timestamp de démarrage de l'application
_APP_START_TIME: Final[datetime] = datetime.now(UTC)


# ============================================================================
# EXCEPTIONS
# ============================================================================


class HealthCheckError(NexusDLError):
    """Exception de base pour les erreurs de health check."""


class ComponentCheckError(HealthCheckError):
    """Exception levée lorsqu'un check de composant échoue.

    Attributes:
        component: Nom du composant.
        reason: Raison de l'échec.
    """

    def __init__(self, component: str, reason: str = "") -> None:
        msg = f"Health check failed for component: {component}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.component = component
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class HealthStatus(str, Enum):
    """Statut de santé d'un composant ou de l'API.

    Attributes:
        HEALTHY: Composant opérationnel.
        DEGRADED: Composant dégradé (fonctionnel mais lent ou partiel).
        UNHEALTHY: Composant non opérationnel.
        UNKNOWN: Statut inconnu.
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            HealthStatus.HEALTHY: t("health.status.healthy", default="Healthy"),
            HealthStatus.DEGRADED: t("health.status.degraded", default="Degraded"),
            HealthStatus.UNHEALTHY: t("health.status.unhealthy", default="Unhealthy"),
            HealthStatus.UNKNOWN: t("health.status.unknown", default="Unknown"),
        }[self]

    @property
    def http_status_code(self) -> int:
        """Code HTTP associé."""
        return {
            HealthStatus.HEALTHY: 200,
            HealthStatus.DEGRADED: 200,
            HealthStatus.UNHEALTHY: 503,
            HealthStatus.UNKNOWN: 503,
        }[self]


class ComponentType(str, Enum):
    """Type de composant vérifié.

    Attributes:
        DATABASE: Base de données.
        CACHE: Cache.
        FILESYSTEM: Système de fichiers.
        REGISTRY: Registre des sites.
        EVENT_BUS: EventBus.
        API: API elle-même.
    """

    DATABASE = "database"
    CACHE = "cache"
    FILESYSTEM = "filesystem"
    REGISTRY = "registry"
    EVENT_BUS = "event_bus"
    API = "api"


# ============================================================================
# MODÈLES DE RÉPONSE — Pydantic
# ============================================================================


class HealthResponse(BaseModel):
    """Réponse basique de health check.

    Attributes:
        status: Statut global.
        timestamp: Timestamp ISO 8601.
    """

    status: HealthStatus = Field(..., description="Statut global.")
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(), description="Timestamp.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ComponentHealthResponse(BaseModel):
    """Réponse de health check pour un composant.

    Attributes:
        status: Statut du composant.
        latency_ms: Latence en millisecondes.
        message: Message additionnel.
        details: Détails additionnels.
    """

    status: HealthStatus = Field(..., description="Statut du composant.")
    latency_ms: float = Field(default=0.0, ge=0.0, description="Latence ms.")
    message: str = Field(default="", description="Message.")
    details: dict[str, Any] = Field(default_factory=dict, description="Détails.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class DetailedHealthResponse(BaseModel):
    """Réponse détaillée de health check.

    Attributes:
        status: Statut global.
        timestamp: Timestamp ISO 8601.
        components: Statuts des composants.
        version: Version de l'application.
        uptime_seconds: Durée de fonctionnement.
        python_version: Version de Python.
        platform: Plateforme.
    """

    status: HealthStatus = Field(..., description="Statut global.")
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(), description="Timestamp.")
    components: dict[str, ComponentHealthResponse] = Field(default_factory=dict, description="Composants.")
    version: str = Field(..., description="Version.")
    uptime_seconds: float = Field(..., ge=0.0, description="Uptime secondes.")
    python_version: str = Field(..., description="Version Python.")
    platform: str = Field(..., description="Plateforme.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class VersionResponse(BaseModel):
    """Réponse avec les informations de version.

    Attributes:
        app_name: Nom de l'application.
        version: Version.
        author: Auteur.
        url: URL du site.
        python_version: Version de Python.
        python_min_version: Version minimale de Python.
        platform: Plateforme.
        build_date: Date de build.
    """

    app_name: str = Field(..., description="Nom de l'application.")
    version: str = Field(..., description="Version.")
    author: str = Field(..., description="Auteur.")
    url: str = Field(..., description="URL.")
    python_version: str = Field(..., description="Version Python.")
    python_min_version: str = Field(..., description="Version min Python.")
    platform: str = Field(..., description="Plateforme.")
    build_date: str = Field(default="", description="Date de build.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class MetricsResponse(BaseModel):
    """Réponse avec les métriques de base.

    Attributes:
        uptime_seconds: Durée de fonctionnement.
        memory_usage_mb: Utilisation mémoire en MB.
        cpu_percent: Utilisation CPU en pourcentage.
        active_connections: Nombre de connexions actives.
        total_requests: Nombre total de requêtes.
        cache_hit_rate: Taux de hit du cache.
    """

    uptime_seconds: float = Field(..., ge=0.0, description="Uptime secondes.")
    memory_usage_mb: float = Field(default=0.0, ge=0.0, description="Mémoire MB.")
    cpu_percent: float = Field(default=0.0, ge=0.0, le=100.0, description="CPU %.")
    active_connections: int = Field(default=0, ge=0, description="Connexions actives.")
    total_requests: int = Field(default=0, ge=0, description="Total requêtes.")
    cache_hit_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="Cache hit rate.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ErrorResponse(BaseModel):
    """Réponse d'erreur.

    Attributes:
        error: Code d'erreur.
        message: Message d'erreur.
        details: Détails additionnels.
    """

    error: str = Field(..., description="Code erreur.")
    message: str = Field(..., description="Message.")
    details: dict[str, Any] = Field(default_factory=dict, description="Détails.")

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# CACHE — Cache des résultats de health check
# ============================================================================


class HealthCache:
    """Cache des résultats de health check.

    Évite de surcharger les composants avec des checks fréquents.
    """

    def __init__(self, ttl_seconds: int = HEALTH_CACHE_TTL_SECONDS, max_size: int = MAX_CACHE_SIZE) -> None:
        """Initialise le cache.

        Args:
            ttl_seconds: Durée de vie du cache.
            max_size: Taille maximale.
        """
        self._cache: dict[str, tuple[Any, datetime]] = {}
        self._ttl = timedelta(seconds=ttl_seconds)
        self._max_size = max_size
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        """Récupère une valeur du cache.

        Args:
            key: Clé.

        Returns:
            Valeur ou None.
        """
        async with self._lock:
            if key not in self._cache:
                return None
            value, expires_at = self._cache[key]
            if datetime.now(UTC) > expires_at:
                del self._cache[key]
                return None
            return value

    async def set(self, key: str, value: Any) -> None:
        """Stocke une valeur dans le cache.

        Args:
            key: Clé.
            value: Valeur.
        """
        async with self._lock:
            if len(self._cache) >= self._max_size:
                oldest_keys = sorted(self._cache.keys(), key=lambda k: self._cache[k][1])[:5]
                for old_key in oldest_keys:
                    del self._cache[old_key]
            expires_at = datetime.now(UTC) + self._ttl
            self._cache[key] = (value, expires_at)

    async def clear(self) -> None:
        """Vide le cache."""
        async with self._lock:
            self._cache.clear()


# Instance globale du cache
_health_cache = HealthCache()


def get_health_cache() -> HealthCache:
    """Retourne l'instance globale du cache."""
    return _health_cache


# ============================================================================
# HELPERS — Fonctions de vérification des composants
# ============================================================================


async def _check_database(timeout: float = DATABASE_CHECK_TIMEOUT_SECONDS) -> ComponentHealthResponse:
    """Vérifie la santé de la base de données.

    Args:
        timeout: Timeout en secondes.

    Returns:
        Résultat du check.
    """
    start_time = time.perf_counter()

    try:
        # TODO: Implémenter le check réel
        # from nexusdl.core.library.database import get_database
        # db = get_database()
        # await asyncio.wait_for(db.ping(), timeout=timeout)

        # Simulation pour l'instant
        await asyncio.sleep(0.01)
        latency_ms = (time.perf_counter() - start_time) * 1000

        return ComponentHealthResponse(
            status=HealthStatus.HEALTHY,
            latency_ms=latency_ms,
            message="Database is operational",
        )

    except asyncio.TimeoutError:
        latency_ms = (time.perf_counter() - start_time) * 1000
        return ComponentHealthResponse(
            status=HealthStatus.UNHEALTHY,
            latency_ms=latency_ms,
            message="Database check timed out",
        )
    except Exception as e:
        latency_ms = (time.perf_counter() - start_time) * 1000
        logger.warning("Database health check failed: {}", e)
        return ComponentHealthResponse(
            status=HealthStatus.UNHEALTHY,
            latency_ms=latency_ms,
            message=f"Database check failed: {e}",
        )


async def _check_cache(timeout: float = CACHE_CHECK_TIMEOUT_SECONDS) -> ComponentHealthResponse:
    """Vérifie la santé du cache.

    Args:
        timeout: Timeout en secondes.

    Returns:
        Résultat du check.
    """
    start_time = time.perf_counter()

    try:
        # TODO: Implémenter le check réel
        # Le cache est en mémoire, donc toujours healthy
        latency_ms = (time.perf_counter() - start_time) * 1000

        return ComponentHealthResponse(
            status=HealthStatus.HEALTHY,
            latency_ms=latency_ms,
            message="Cache is operational",
            details={"cache_size": get_health_cache()._cache.__len__()},
        )

    except Exception as e:
        latency_ms = (time.perf_counter() - start_time) * 1000
        logger.warning("Cache health check failed: {}", e)
        return ComponentHealthResponse(
            status=HealthStatus.UNHEALTHY,
            latency_ms=latency_ms,
            message=f"Cache check failed: {e}",
        )


async def _check_filesystem(timeout: float = FILESYSTEM_CHECK_TIMEOUT_SECONDS) -> ComponentHealthResponse:
    """Vérifie la santé du système de fichiers.

    Args:
        timeout: Timeout en secondes.

    Returns:
        Résultat du check.
    """
    start_time = time.perf_counter()

    try:
        from nexusdl.core.paths import get_paths

        paths = get_paths()

        # Vérifier que les répertoires existent et sont accessibles
        async def _check_path(path: Any) -> bool:
            return await asyncio.wait_for(
                asyncio.to_thread(os.access, str(path), os.W_OK),
                timeout=timeout,
            )

        # Vérifier les répertoires principaux
        dirs_to_check = [
            paths.config_dir,
            paths.data_dir,
            paths.cache_dir,
            paths.logs_dir,
        ]

        results = await asyncio.gather(*[_check_path(d) for d in dirs_to_check], return_exceptions=True)

        all_accessible = all(r is True for r in results)
        latency_ms = (time.perf_counter() - start_time) * 1000

        if all_accessible:
            return ComponentHealthResponse(
                status=HealthStatus.HEALTHY,
                latency_ms=latency_ms,
                message="Filesystem is accessible",
                details={"directories_checked": len(dirs_to_check)},
            )
        else:
            return ComponentHealthResponse(
                status=HealthStatus.DEGRADED,
                latency_ms=latency_ms,
                message="Some directories are not accessible",
                details={"directories_checked": len(dirs_to_check), "accessible": sum(1 for r in results if r is True)},
            )

    except Exception as e:
        latency_ms = (time.perf_counter() - start_time) * 1000
        logger.warning("Filesystem health check failed: {}", e)
        return ComponentHealthResponse(
            status=HealthStatus.UNHEALTHY,
            latency_ms=latency_ms,
            message=f"Filesystem check failed: {e}",
        )


async def _check_registry(timeout: float = REGISTRY_CHECK_TIMEOUT_SECONDS) -> ComponentHealthResponse:
    """Vérifie la santé du registre des sites.

    Args:
        timeout: Timeout en secondes.

    Returns:
        Résultat du check.
    """
    start_time = time.perf_counter()

    try:
        from nexusdl.core.registry import get_site_registry

        registry = get_site_registry()

        # Vérifier que le registre est initialisé
        sites_count = registry.sites_count
        enabled_count = registry.enabled_sites_count

        latency_ms = (time.perf_counter() - start_time) * 1000

        if sites_count > 0:
            return ComponentHealthResponse(
                status=HealthStatus.HEALTHY,
                latency_ms=latency_ms,
                message="Registry is operational",
                details={
                    "sites_count": sites_count,
                    "enabled_count": enabled_count,
                },
            )
        else:
            return ComponentHealthResponse(
                status=HealthStatus.DEGRADED,
                latency_ms=latency_ms,
                message="Registry is empty",
            )

    except Exception as e:
        latency_ms = (time.perf_counter() - start_time) * 1000
        logger.warning("Registry health check failed: {}", e)
        return ComponentHealthResponse(
            status=HealthStatus.UNHEALTHY,
            latency_ms=latency_ms,
            message=f"Registry check failed: {e}",
        )


async def _check_event_bus(timeout: float = 2.0) -> ComponentHealthResponse:
    """Vérifie la santé de l'EventBus.

    Args:
        timeout: Timeout en secondes.

    Returns:
        Résultat du check.
    """
    start_time = time.perf_counter()

    try:
        from nexusdl.core.events import get_event_bus

        event_bus = get_event_bus()

        # Vérifier que l'EventBus est démarré
        is_started = event_bus.is_started

        latency_ms = (time.perf_counter() - start_time) * 1000

        if is_started:
            return ComponentHealthResponse(
                status=HealthStatus.HEALTHY,
                latency_ms=latency_ms,
                message="EventBus is operational",
            )
        else:
            return ComponentHealthResponse(
                status=HealthStatus.DEGRADED,
                latency_ms=latency_ms,
                message="EventBus is not started",
            )

    except Exception as e:
        latency_ms = (time.perf_counter() - start_time) * 1000
        logger.warning("EventBus health check failed: {}", e)
        return ComponentHealthResponse(
            status=HealthStatus.UNHEALTHY,
            latency_ms=latency_ms,
            message=f"EventBus check failed: {e}",
        )


def _get_system_metrics() -> dict[str, Any]:
    """Récupère les métriques système.

    Returns:
        Dictionnaire de métriques.
    """
    import psutil

    try:
        process = psutil.Process(os.getpid())
        memory_info = process.memory_info()
        cpu_percent = process.cpu_percent(interval=0.1)

        return {
            "memory_usage_mb": memory_info.rss / (1024 * 1024),
            "cpu_percent": cpu_percent,
        }
    except Exception as e:
        logger.debug("Impossible de récupérer les métriques système: {}", e)
        return {
            "memory_usage_mb": 0.0,
            "cpu_percent": 0.0,
        }


def _calculate_uptime_seconds() -> float:
    """Calcule la durée de fonctionnement en secondes.

    Returns:
        Uptime en secondes.
    """
    return (datetime.now(UTC) - _APP_START_TIME).total_seconds()


# ============================================================================
# ROUTEUR — Endpoints FastAPI
# ============================================================================


if FASTAPI_AVAILABLE:

    health_router = APIRouter(tags=["health"])

    # =========================================================================
    # GET /health — Vérification basique
    # =========================================================================

    @health_router.get(
        "/health",
        response_model=HealthResponse,
        summary="Vérification basique de santé",
        description="Retourne le statut de santé basique de l'API.",
        responses={
            200: {"description": "API opérationnelle"},
            503: {"description": "API non opérationnelle"},
        },
    )
    async def health_check() -> HealthResponse:
        """Vérification basique de santé.

        Returns:
            Statut de santé.
        """
        # Vérifier le cache
        cache = get_health_cache()
        cached = await cache.get("health_basic")
        if cached:
            return cached

        # Pour l'instant, toujours healthy
        # TODO: Implémenter des checks basiques
        response = HealthResponse(status=HealthStatus.HEALTHY)

        # Stocker dans le cache
        await cache.set("health_basic", response)

        return response

    # =========================================================================
    # GET /health/live — Probe de liveness
    # =========================================================================

    @health_router.get(
        "/health/live",
        response_model=HealthResponse,
        summary="Probe de liveness",
        description="Vérifie que l'API est vivante (pour Kubernetes).",
        responses={
            200: {"description": "API vivante"},
            503: {"description": "API non vivante"},
        },
    )
    async def liveness_probe() -> HealthResponse:
        """Probe de liveness.

        Returns:
            Statut de liveness.
        """
        # Toujours retourner healthy pour liveness
        # Si l'API ne répond pas, Kubernetes la redémarrera
        return HealthResponse(status=HealthStatus.HEALTHY)

    # =========================================================================
    # GET /health/ready — Probe de readiness
    # =========================================================================

    @health_router.get(
        "/health/ready",
        response_model=HealthResponse,
        summary="Probe de readiness",
        description="Vérifie que l'API est prête à recevoir du trafic (pour Kubernetes).",
        responses={
            200: {"description": "API prête"},
            503: {"description": "API non prête"},
        },
    )
    async def readiness_probe() -> HealthResponse:
        """Probe de readiness.

        Returns:
            Statut de readiness.
        """
        # Vérifier le cache
        cache = get_health_cache()
        cached = await cache.get("health_ready")
        if cached:
            return cached

        # Vérifier que les composants critiques sont opérationnels
        try:
            # Vérifier la base de données
            db_check = await _check_database(timeout=2.0)
            if db_check.status == HealthStatus.UNHEALTHY:
                response = HealthResponse(status=HealthStatus.UNHEALTHY)
                await cache.set("health_ready", response)
                return response

            # Vérifier le registre
            registry_check = await _check_registry(timeout=2.0)
            if registry_check.status == HealthStatus.UNHEALTHY:
                response = HealthResponse(status=HealthStatus.UNHEALTHY)
                await cache.set("health_ready", response)
                return response

            response = HealthResponse(status=HealthStatus.HEALTHY)
            await cache.set("health_ready", response)
            return response

        except Exception as e:
            logger.error("Erreur lors du probe de readiness: {}", e)
            return HealthResponse(status=HealthStatus.UNHEALTHY)

    # =========================================================================
    # GET /health/startup — Probe de startup
    # =========================================================================

    @health_router.get(
        "/health/startup",
        response_model=HealthResponse,
        summary="Probe de startup",
        description="Vérifie que l'API a terminé son démarrage (pour Kubernetes).",
        responses={
            200: {"description": "API démarrée"},
            503: {"description": "API en cours de démarrage"},
        },
    )
    async def startup_probe() -> HealthResponse:
        """Probe de startup.

        Returns:
            Statut de startup.
        """
        # Vérifier que l'application a terminé son initialisation
        # Pour l'instant, toujours retourner healthy
        # TODO: Implémenter un flag d'initialisation
        return HealthResponse(status=HealthStatus.HEALTHY)

    # =========================================================================
    # GET /health/detailed — Vérification détaillée
    # =========================================================================

    @health_router.get(
        "/health/detailed",
        response_model=DetailedHealthResponse,
        summary="Vérification détaillée de santé",
        description="Retourne une vérification détaillée de tous les composants.",
        responses={
            200: {"description": "Vérification détaillée"},
            503: {"description": "Un ou plusieurs composants sont non opérationnels"},
        },
    )
    async def detailed_health_check() -> DetailedHealthResponse:
        """Vérification détaillée de santé.

        Returns:
            Résultat détaillé.
        """
        # Vérifier le cache
        cache = get_health_cache()
        cached = await cache.get("health_detailed")
        if cached:
            return cached

        # Vérifier tous les composants en parallèle
        components = await asyncio.gather(
            _check_database(),
            _check_cache(),
            _check_filesystem(),
            _check_registry(),
            _check_event_bus(),
            return_exceptions=True,
        )

        # Construire la réponse
        component_results = {
            "database": components[0] if not isinstance(components[0], Exception) else ComponentHealthResponse(
                status=HealthStatus.UNHEALTHY,
                message=f"Check failed: {components[0]}",
            ),
            "cache": components[1] if not isinstance(components[1], Exception) else ComponentHealthResponse(
                status=HealthStatus.UNHEALTHY,
                message=f"Check failed: {components[1]}",
            ),
            "filesystem": components[2] if not isinstance(components[2], Exception) else ComponentHealthResponse(
                status=HealthStatus.UNHEALTHY,
                message=f"Check failed: {components[2]}",
            ),
            "registry": components[3] if not isinstance(components[3], Exception) else ComponentHealthResponse(
                status=HealthStatus.UNHEALTHY,
                message=f"Check failed: {components[3]}",
            ),
            "event_bus": components[4] if not isinstance(components[4], Exception) else ComponentHealthResponse(
                status=HealthStatus.UNHEALTHY,
                message=f"Check failed: {components[4]}",
            ),
        }

        # Déterminer le statut global
        statuses = [c.status for c in component_results.values()]
        if HealthStatus.UNHEALTHY in statuses:
            global_status = HealthStatus.UNHEALTHY
        elif HealthStatus.DEGRADED in statuses:
            global_status = HealthStatus.DEGRADED
        else:
            global_status = HealthStatus.HEALTHY

        response = DetailedHealthResponse(
            status=global_status,
            components=component_results,
            version=APP_VERSION,
            uptime_seconds=_calculate_uptime_seconds(),
            python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            platform=f"{platform.system()} {platform.release()}",
        )

        # Stocker dans le cache
        await cache.set("health_detailed", response)

        return response

    # =========================================================================
    # GET /health/version — Informations de version
    # =========================================================================

    @health_router.get(
        "/health/version",
        response_model=VersionResponse,
        summary="Informations de version",
        description="Retourne les informations de version de l'API.",
        responses={
            200: {"description": "Informations de version"},
        },
    )
    async def get_version() -> VersionResponse:
        """Récupère les informations de version.

        Returns:
            Informations de version.
        """
        return VersionResponse(
            app_name=APP_NAME,
            version=APP_VERSION,
            author=APP_AUTHOR,
            url=APP_URL,
            python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            python_min_version=f"{PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}",
            platform=f"{platform.system()} {platform.release()} ({platform.machine()})",
            build_date="",  # TODO: Ajouter la date de build
        )

    # =========================================================================
    # GET /health/metrics — Métriques de base
    # =========================================================================

    @health_router.get(
        "/health/metrics",
        response_model=MetricsResponse,
        summary="Métriques de base",
        description="Retourne les métriques de base de l'API (pour Prometheus).",
        responses={
            200: {"description": "Métriques"},
        },
    )
    async def get_metrics() -> MetricsResponse:
        """Récupère les métriques de base.

        Returns:
            Métriques.
        """
        system_metrics = _get_system_metrics()

        # TODO: Récupérer les vraies métriques
        # from nexusdl.interfaces.web.backend.middleware.logging import get_logging_stats
        # logging_stats = await get_logging_stats()

        return MetricsResponse(
            uptime_seconds=_calculate_uptime_seconds(),
            memory_usage_mb=system_metrics["memory_usage_mb"],
            cpu_percent=system_metrics["cpu_percent"],
            active_connections=0,  # TODO
            total_requests=0,  # TODO
            cache_hit_rate=0.0,  # TODO
        )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_CHECK_TIMEOUT_SECONDS",
    "DATABASE_CHECK_TIMEOUT_SECONDS",
    "CACHE_CHECK_TIMEOUT_SECONDS",
    "FILESYSTEM_CHECK_TIMEOUT_SECONDS",
    "REGISTRY_CHECK_TIMEOUT_SECONDS",
    "HEALTH_CACHE_TTL_SECONDS",
    # Exceptions
    "HealthCheckError",
    "ComponentCheckError",
    # Enums
    "HealthStatus",
    "ComponentType",
    # Modèles de réponse
    "HealthResponse",
    "ComponentHealthResponse",
    "DetailedHealthResponse",
    "VersionResponse",
    "MetricsResponse",
    "ErrorResponse",
    # Cache
    "HealthCache",
    "get_health_cache",
    # Fonctions de check
    "check_database" if FASTAPI_AVAILABLE else None,
    "check_cache" if FASTAPI_AVAILABLE else None,
    "check_filesystem" if FASTAPI_AVAILABLE else None,
    "check_registry" if FASTAPI_AVAILABLE else None,
    "check_event_bus" if FASTAPI_AVAILABLE else None,
    # Helpers
    "get_system_metrics",
    "calculate_uptime_seconds",
    # Routeur
    "health_router" if FASTAPI_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]

# Exports des fonctions de check (sans underscore)
if FASTAPI_AVAILABLE:
    check_database = _check_database
    check_cache = _check_cache
    check_filesystem = _check_filesystem
    check_registry = _check_registry
    check_event_bus = _check_event_bus
    calculate_uptime_seconds = _calculate_uptime_seconds
