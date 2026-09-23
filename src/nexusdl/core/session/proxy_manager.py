"""Gestionnaire de proxies avec rotation intelligente et health checks.

Ce module fournit un système complet de gestion de proxies HTTP/SOCKS5 pour
contourner les restrictions géographiques, améliorer l'anonymat, et répartir
la charge sur plusieurs points de sortie. Il est essentiel pour :

    - Contourner les blocages géographiques (sites region-locked)
    - Éviter les bans par IP (rotation sur plusieurs proxies)
    - Améliorer l'anonymat (pas d'IP unique identifiable)
    - Répartir la charge (éviter la surcharge d'un seul proxy)
    - Détecter les proxies défaillants (health checks automatiques)

Types de proxies supportés :
    - HTTP/HTTPS : proxies web classiques
    - SOCKS5     : proxies SOCKS5 avec résolution DNS distante (socks5h)
    - SOCKS5H    : SOCKS5 avec résolution DNS côté proxy (recommandé)

Stratégies de rotation :
    - ROUND_ROBIN  : rotation séquentielle (défaut)
    - RANDOM       : sélection aléatoire uniforme
    - LEAST_USED   : proxy le moins utilisé récemment
    - HEALTH_BASED : proxy le plus sain (taux de succès + latence)
    - WEIGHTED     : rotation pondérée par un score de santé

Fonctionnalités principales :
    - Support multi-protocoles (HTTP, HTTPS, SOCKS5, SOCKS5H)
    - Authentification proxy (username/password)
    - Rotation intelligente (5 stratégies)
    - Health checks périodiques (configurables)
    - Exclusion de domaines (no_proxy)
    - Chargement depuis fichier (liste de proxies)
    - Statistiques détaillées par proxy
    - Détection automatique des proxies défaillants
    - Mode fallback (proxy principal + backends)
    - Thread-safe (locks asyncio)
    - Événements EventBus pour monitoring

Architecture :
    ProxyManager
        ├── ProxyType (enum) : HTTP, HTTPS, SOCKS5, SOCKS5H
        ├── RotationStrategy (enum) : ROUND_ROBIN, RANDOM, LEAST_USED, etc.
        ├── ProxyHealth (enum) : HEALTHY, DEGRADED, UNHEALTHY, UNKNOWN
        ├── ProxyConfig (Pydantic) : configuration d'un proxy individuel
        ├── ProxyHealthCheck (Pydantic) : résultat d'un health check
        ├── ProxyStats (Pydantic) : statistiques d'un proxy
        ├── ProxyManagerConfig (Pydantic) : configuration globale
        └── ProxyEntry (interne) : proxy + métadonnées runtime

Exemple d'utilisation :
    >>> manager = ProxyManager()
    >>> await manager.start()
    >>>
    >>> # Ajouter des proxies
    >>> manager.add_proxy(ProxyConfig(
    ...     url="http://proxy1.example.com:8080",
    ...     username="user",
    ...     password="pass",
    ... ))
    >>> manager.add_proxy(ProxyConfig(
    ...     url="socks5h://proxy2.example.com:1080",
    ... ))
    >>>
    >>> # Obtenir un proxy pour une requête
    >>> proxy_url = await manager.get_proxy_for_url("https://mangadex.org")
    >>> print(proxy_url)
    http://user:pass@proxy1.example.com:8080
    >>>
    >>> # Signaler un succès/échec
    >>> await manager.report_success(proxy_url)
    >>> await manager.report_failure(proxy_url, error="timeout")
    >>>
    >>> # Charger depuis un fichier
    >>> await manager.load_from_file(Path("proxies.txt"))
    >>>
    >>> # Statistiques
    >>> stats = await manager.get_stats()
    >>> print(f"Proxies sains: {stats.healthy_count}/{stats.total_count}")
    >>>
    >>> await manager.stop()
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import re
import time
from collections import OrderedDict
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Final, Self
from urllib.parse import urlparse

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# EXCEPTIONS
# ============================================================================


class ProxyError(NexusDLError):
    """Exception de base pour les erreurs de proxy."""


class ProxyManagerNotStartedError(ProxyError):
    """Exception levée lorsqu'on utilise le manager avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "ProxyManager must be started before use. Call await manager.start()"
        )


class NoProxyAvailableError(ProxyError):
    """Exception levée lorsqu'aucun proxy n'est disponible pour une URL."""

    def __init__(self, url: str, reason: str = "") -> None:
        msg = f"Aucun proxy disponible pour {url}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.url = url
        self.reason = reason


class InvalidProxyUrlError(ProxyError):
    """Exception levée lorsqu'une URL de proxy est invalide."""

    def __init__(self, url: str, reason: str = "") -> None:
        msg = f"URL de proxy invalide: {url}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.url = url
        self.reason = reason


class ProxyLoadError(ProxyError):
    """Exception levée lorsque le chargement d'une liste de proxies échoue."""

    def __init__(self, path: Path, reason: str = "") -> None:
        msg = f"Impossible de charger la liste de proxies: {path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.path = path
        self.reason = reason


class HealthCheckError(ProxyError):
    """Exception levée lorsqu'un health check échoue."""

    def __init__(self, proxy_url: str, reason: str = "") -> None:
        msg = f"Health check échoué pour {proxy_url}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.proxy_url = proxy_url
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class ProxyType(str, Enum):
    """Type de protocole proxy.

    HTTP    : Proxy HTTP classique (non sécurisé).
    HTTPS   : Proxy HTTP avec tunnel TLS (CONNECT).
    SOCKS5  : Proxy SOCKS5 avec résolution DNS locale.
    SOCKS5H : Proxy SOCKS5 avec résolution DNS distante (recommandé).
    """

    HTTP = "http"
    HTTPS = "https"
    SOCKS5 = "socks5"
    SOCKS5H = "socks5h"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ProxyType.HTTP: "HTTP",
            ProxyType.HTTPS: "HTTPS",
            ProxyType.SOCKS5: "SOCKS5",
            ProxyType.SOCKS5H: "SOCKS5H (DNS distant)",
        }[self]

    @property
    def default_port(self) -> int:
        """Port par défaut pour ce type."""
        return {
            ProxyType.HTTP: 8080,
            ProxyType.HTTPS: 8443,
            ProxyType.SOCKS5: 1080,
            ProxyType.SOCKS5H: 1080,
        }[self]

    @property
    def supports_auth(self) -> bool:
        """Indique si le type supporte l'authentification."""
        return self in (ProxyType.HTTP, ProxyType.HTTPS, ProxyType.SOCKS5, ProxyType.SOCKS5H)

    @property
    def is_socks(self) -> bool:
        """Indique si c'est un proxy SOCKS."""
        return self in (ProxyType.SOCKS5, ProxyType.SOCKS5H)


class RotationStrategy(str, Enum):
    """Stratégie de rotation des proxies.

    ROUND_ROBIN  : Rotation séquentielle (défaut, équitable).
    RANDOM       : Sélection aléatoire uniforme.
    LEAST_USED   : Proxy le moins utilisé récemment.
    HEALTH_BASED : Proxy avec le meilleur score de santé.
    WEIGHTED     : Rotation pondérée par un score de santé.
    """

    ROUND_ROBIN = "round_robin"
    RANDOM = "random"
    LEAST_USED = "least_used"
    HEALTH_BASED = "health_based"
    WEIGHTED = "weighted"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            RotationStrategy.ROUND_ROBIN: "Round-robin (séquentiel)",
            RotationStrategy.RANDOM: "Aléatoire",
            RotationStrategy.LEAST_USED: "Moins utilisé",
            RotationStrategy.HEALTH_BASED: "Meilleure santé",
            RotationStrategy.WEIGHTED: "Pondéré par santé",
        }[self]


class ProxyHealth(str, Enum):
    """État de santé d'un proxy.

    HEALTHY   : Proxy fonctionnel, latence acceptable.
    DEGRADED  : Proxy fonctionnel mais lent ou avec erreurs.
    UNHEALTHY : Proxy défaillant (ne pas utiliser).
    UNKNOWN   : État inconnu (pas encore testé).
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ProxyHealth.HEALTHY: "Sain",
            ProxyHealth.DEGRADED: "Dégradé",
            ProxyHealth.UNHEALTHY: "Défaillant",
            ProxyHealth.UNKNOWN: "Inconnu",
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            ProxyHealth.HEALTHY: "🟢",
            ProxyHealth.DEGRADED: "🟡",
            ProxyHealth.UNHEALTHY: "🔴",
            ProxyHealth.UNKNOWN: "⚪",
        }[self]

    @property
    def is_usable(self) -> bool:
        """Indique si le proxy peut être utilisé."""
        return self in (ProxyHealth.HEALTHY, ProxyHealth.DEGRADED, ProxyHealth.UNKNOWN)


# ============================================================================
# MODÈLES PYDANTIC — Configuration
# ============================================================================


class ProxyConfig(BaseModel):
    """Configuration d'un proxy individuel.

    Attributes:
        url: URL complète du proxy (ex: http://host:port, socks5h://host:port).
        username: Nom d'utilisateur pour l'authentification (optionnel).
        password: Mot de passe pour l'authentification (optionnel).
        name: Nom descriptif du proxy (optionnel, pour affichage).
        region: Code région ISO 3166-1 alpha-2 (optionnel, pour géo-routing).
        weight: Poids pour la rotation pondérée (défaut: 1.0).
        enabled: True si le proxy est activé.
        tags: Tags pour classification (ex: ["fast", "europe"]).
    """

    url: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="URL complète du proxy (ex: http://host:port).",
    )
    username: str | None = Field(
        default=None,
        max_length=100,
        description="Nom d'utilisateur pour l'authentification.",
    )
    password: str | None = Field(
        default=None,
        max_length=200,
        description="Mot de passe pour l'authentification.",
    )
    name: str | None = Field(
        default=None,
        max_length=100,
        description="Nom descriptif du proxy.",
    )
    region: str | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description="Code région ISO 3166-1 alpha-2 (ex: 'FR', 'US').",
    )
    weight: float = Field(
        default=1.0,
        gt=0.0,
        le=10.0,
        description="Poids pour la rotation pondérée.",
    )
    enabled: bool = Field(
        default=True,
        description="True si le proxy est activé.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Tags pour classification.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        """Valide l'URL du proxy."""
        v = v.strip()
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https", "socks5", "socks5h"):
            raise InvalidProxyUrlError(
                v,
                f"Schéma non supporté: {parsed.scheme} (attendu: http, https, socks5, socks5h)",
            )
        if not parsed.hostname:
            raise InvalidProxyUrlError(v, "Host manquant")
        return v

    @field_validator("region")
    @classmethod
    def _validate_region(cls, v: str | None) -> str | None:
        """Valide le code région."""
        if v is None:
            return None
        v = v.strip().upper()
        if len(v) != 2 or not v.isalpha():
            raise ValueError(f"Code région invalide: {v} (attendu: 2 lettres)")
        return v

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def proxy_type(self) -> ProxyType:
        """Type de proxy détecté depuis l'URL."""
        parsed = urlparse(self.url)
        scheme = parsed.scheme.lower()
        return {
            "http": ProxyType.HTTP,
            "https": ProxyType.HTTPS,
            "socks5": ProxyType.SOCKS5,
            "socks5h": ProxyType.SOCKS5H,
        }.get(scheme, ProxyType.HTTP)

    @property
    def host(self) -> str:
        """Host du proxy."""
        return urlparse(self.url).hostname or ""

    @property
    def port(self) -> int:
        """Port du proxy (défaut selon le type)."""
        parsed = urlparse(self.url)
        if parsed.port:
            return parsed.port
        return self.proxy_type.default_port

    @property
    def display_name(self) -> str:
        """Nom d'affichage (name ou host:port)."""
        if self.name:
            return self.name
        return f"{self.host}:{self.port}"

    @property
    def masked_url(self) -> str:
        """URL avec credentials masqués (pour logs)."""
        parsed = urlparse(self.url)
        if self.username:
            # Remplacer les credentials par ***
            netloc = f"{self.username}:***@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            return f"{parsed.scheme}://{netloc}{parsed.path}"
        return self.url

    @property
    def authenticated_url(self) -> str:
        """URL avec credentials intégrés (pour httpx)."""
        if not self.username:
            return self.url
        parsed = urlparse(self.url)
        password_part = f":{self.password}" if self.password else ""
        netloc = f"{self.username}{password_part}@{parsed.hostname}"
        if parsed.port:
            netloc += f":{parsed.port}"
        return f"{parsed.scheme}://{netloc}{parsed.path}"

    @property
    def unique_id(self) -> str:
        """Identifiant unique du proxy (hash de l'URL)."""
        return hashlib.sha256(self.url.encode()).hexdigest()[:16]


class ProxyManagerConfig(BaseModel):
    """Configuration globale du gestionnaire de proxies.

    Attributes:
        rotation_strategy: Stratégie de rotation par défaut.
        health_check_enabled: Activer les health checks périodiques.
        health_check_interval_seconds: Intervalle entre deux health checks.
        health_check_timeout_seconds: Timeout pour un health check.
        health_check_url: URL de test pour les health checks.
        unhealthy_threshold: Nombre d'échecs consécutifs pour marquer UNHEALTHY.
        recovery_threshold: Nombre de succès pour repasser HEALTHY.
        max_proxies: Nombre maximum de proxies en mémoire.
        no_proxy_domains: Liste de domaines à exclure du proxy.
        fallback_to_direct: Si True, utiliser une connexion directe si aucun proxy sain.
    """

    rotation_strategy: RotationStrategy = Field(
        default=RotationStrategy.ROUND_ROBIN,
        description="Stratégie de rotation par défaut.",
    )
    health_check_enabled: bool = Field(
        default=True,
        description="Activer les health checks périodiques.",
    )
    health_check_interval_seconds: float = Field(
        default=60.0,
        ge=10.0,
        le=3600.0,
        description="Intervalle entre deux health checks.",
    )
    health_check_timeout_seconds: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
        description="Timeout pour un health check.",
    )
    health_check_url: str = Field(
        default="https://httpbin.org/ip",
        description="URL de test pour les health checks.",
    )
    unhealthy_threshold: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Nombre d'échecs consécutifs pour marquer UNHEALTHY.",
    )
    recovery_threshold: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Nombre de succès pour repasser HEALTHY.",
    )
    max_proxies: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Nombre maximum de proxies en mémoire.",
    )
    no_proxy_domains: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "::1"],
        description="Domaines à exclure du proxy.",
    )
    fallback_to_direct: bool = Field(
        default=False,
        description="Utiliser une connexion directe si aucun proxy sain.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# MODÈLES PYDANTIC — Health check et statistiques
# ============================================================================


class ProxyHealthCheck(BaseModel):
    """Résultat d'un health check sur un proxy."""

    proxy_url: str = Field(..., description="URL du proxy testé.")
    healthy: bool = Field(..., description="True si le proxy est sain.")
    latency_ms: float = Field(..., ge=0.0, description="Latence en millisecondes.")
    status_code: int | None = Field(
        default=None,
        description="Code HTTP de la réponse (si applicable).",
    )
    error: str | None = Field(
        default=None,
        description="Message d'erreur si le check a échoué.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp du health check.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class ProxyStats(BaseModel):
    """Statistiques d'utilisation d'un proxy."""

    proxy_url: str = Field(..., description="URL du proxy.")
    total_requests: int = Field(default=0, ge=0)
    successful_requests: int = Field(default=0, ge=0)
    failed_requests: int = Field(default=0, ge=0)
    total_latency_ms: float = Field(default=0.0, ge=0.0)
    last_used_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    consecutive_successes: int = Field(default=0, ge=0)
    health: ProxyHealth = Field(default=ProxyHealth.UNKNOWN)
    last_health_check: ProxyHealthCheck | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def success_rate(self) -> float:
        """Taux de succès (0.0 à 1.0)."""
        if self.total_requests == 0:
            return 0.0
        return self.successful_requests / self.total_requests

    @property
    def average_latency_ms(self) -> float:
        """Latence moyenne en millisecondes."""
        if self.successful_requests == 0:
            return 0.0
        return self.total_latency_ms / self.successful_requests

    @property
    def health_score(self) -> float:
        """Score de santé (0.0 à 1.0, plus élevé = meilleur).

        Combine le taux de succès, la latence, et l'état de santé.
        """
        if self.total_requests == 0:
            return 0.5  # Neutre si jamais utilisé

        # Composante succès (50% du score)
        success_component = self.success_rate * 0.5

        # Composante latence (30% du score)
        # Latence idéale: < 200ms, mauvaise: > 2000ms
        avg_latency = self.average_latency_ms
        if avg_latency <= 0:
            latency_component = 0.3
        elif avg_latency < 200:
            latency_component = 0.3
        elif avg_latency > 2000:
            latency_component = 0.0
        else:
            latency_component = 0.3 * (1 - (avg_latency - 200) / 1800)

        # Composante santé (20% du score)
        health_map = {
            ProxyHealth.HEALTHY: 0.2,
            ProxyHealth.DEGRADED: 0.1,
            ProxyHealth.UNHEALTHY: 0.0,
            ProxyHealth.UNKNOWN: 0.1,
        }
        health_component = health_map.get(self.health, 0.1)

        return success_component + latency_component + health_component


class ProxyManagerStats(BaseModel):
    """Statistiques globales du gestionnaire de proxies."""

    total_proxies: int = Field(default=0, ge=0)
    enabled_proxies: int = Field(default=0, ge=0)
    healthy_count: int = Field(default=0, ge=0)
    degraded_count: int = Field(default=0, ge=0)
    unhealthy_count: int = Field(default=0, ge=0)
    unknown_count: int = Field(default=0, ge=0)
    total_requests: int = Field(default=0, ge=0)
    total_successes: int = Field(default=0, ge=0)
    total_failures: int = Field(default=0, ge=0)
    total_health_checks: int = Field(default=0, ge=0)
    rotation_strategy: RotationStrategy = Field(default=RotationStrategy.ROUND_ROBIN)
    last_rotation_at: datetime | None = None
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def overall_success_rate(self) -> float:
        """Taux de succès global."""
        if self.total_requests == 0:
            return 0.0
        return self.total_successes / self.total_requests


# ============================================================================
# CLASSE INTERNE — ProxyEntry
# ============================================================================


class _ProxyEntry:
    """Entrée interne représentant un proxy avec ses métadonnées runtime.

    Non exposé publiquement — utilisé par ProxyManager.
    """

    __slots__ = (
        "_config",
        "_stats",
        "_lock",
    )

    def __init__(self, config: ProxyConfig) -> None:
        self._config = config
        self._stats = ProxyStats(proxy_url=config.url)
        self._lock = asyncio.Lock()

    @property
    def config(self) -> ProxyConfig:
        return self._config

    @property
    def stats(self) -> ProxyStats:
        return self._stats

    @property
    def is_enabled(self) -> bool:
        return self._config.enabled

    @property
    def health(self) -> ProxyHealth:
        return self._stats.health

    @property
    def is_usable(self) -> bool:
        return self.is_enabled and self._stats.health.is_usable

    async def record_success(self, latency_ms: float) -> None:
        """Enregistre un succès."""
        async with self._lock:
            now = datetime.now(UTC)
            self._stats = self._stats.model_copy(
                update={
                    "total_requests": self._stats.total_requests + 1,
                    "successful_requests": self._stats.successful_requests + 1,
                    "total_latency_ms": self._stats.total_latency_ms + latency_ms,
                    "last_used_at": now,
                    "last_success_at": now,
                    "consecutive_failures": 0,
                    "consecutive_successes": self._stats.consecutive_successes + 1,
                }
            )

    async def record_failure(self, error: str | None = None) -> None:
        """Enregistre un échec."""
        async with self._lock:
            now = datetime.now(UTC)
            new_consecutive_failures = self._stats.consecutive_failures + 1
            self._stats = self._stats.model_copy(
                update={
                    "total_requests": self._stats.total_requests + 1,
                    "failed_requests": self._stats.failed_requests + 1,
                    "last_used_at": now,
                    "last_failure_at": now,
                    "consecutive_failures": new_consecutive_failures,
                    "consecutive_successes": 0,
                }
            )

    async def update_health(
        self,
        unhealthy_threshold: int,
        recovery_threshold: int,
    ) -> None:
        """Met à jour l'état de santé selon les seuils."""
        async with self._lock:
            current_health = self._stats.health

            # Passage à UNHEALTHY
            if self._stats.consecutive_failures >= unhealthy_threshold:
                new_health = ProxyHealth.UNHEALTHY
            # Passage à HEALTHY
            elif self._stats.consecutive_successes >= recovery_threshold:
                new_health = ProxyHealth.HEALTHY
            # Dégradé si quelques échecs
            elif self._stats.consecutive_failures > 0:
                new_health = ProxyHealth.DEGRADED
            # Inconnu par défaut
            elif current_health == ProxyHealth.UNKNOWN:
                new_health = ProxyHealth.UNKNOWN
            else:
                new_health = current_health

            if new_health != current_health:
                self._stats = self._stats.model_copy(update={"health": new_health})

    async def record_health_check(self, check: ProxyHealthCheck) -> None:
        """Enregistre un health check."""
        async with self._lock:
            self._stats = self._stats.model_copy(
                update={
                    "last_health_check": check,
                    "health": ProxyHealth.HEALTHY if check.healthy else ProxyHealth.UNHEALTHY,
                }
            )


# ============================================================================
# CLASSE PRINCIPALE — ProxyManager
# ============================================================================


class ProxyManager:
    """Gestionnaire de proxies avec rotation intelligente et health checks.

    Gère une liste de proxies, applique une stratégie de rotation, effectue
    des health checks périodiques, et expose des statistiques détaillées.

    Lifecycle :
        >>> manager = ProxyManager()
        >>> await manager.start()
        >>> proxy_url = await manager.get_proxy_for_url("https://example.com")
        >>> await manager.report_success(proxy_url)
        >>> await manager.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Les opérations sont protégées par des locks granulaires.
    """

    # Pattern pour parser une ligne de fichier de proxies
    _PROXY_LINE_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"^(?P<type>http|https|socks5|socks5h)?(?:://)?(?:(?P<user>[^:@]+)(?::(?P<pass>[^@]+))?@)?(?P<host>[^:/]+)(?::(?P<port>\d+))?$",
        re.IGNORECASE,
    )

    def __init__(
        self,
        *,
        config: ProxyManagerConfig | None = None,
    ) -> None:
        """Initialise le gestionnaire de proxies.

        Args:
            config: Configuration globale (défaut: valeurs raisonnables).
        """
        self._config = config or ProxyManagerConfig()

        # État
        self._started: bool = False
        self._start_time: float = 0.0

        # Proxies (OrderedDict pour round-robin)
        self._proxies: OrderedDict[str, _ProxyEntry] = OrderedDict()
        self._proxies_lock = asyncio.Lock()

        # Round-robin index
        self._rr_index: int = 0
        self._rr_lock = asyncio.Lock()

        # Tâche de health check
        self._health_check_task: asyncio.Task[None] | None = None

        # Statistiques globales
        self._total_health_checks: int = 0
        self._last_rotation_at: datetime | None = None
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="proxy_manager")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre le gestionnaire et la tâche de health checks."""
        if self._started:
            self._logger.warning("ProxyManager déjà démarré, ignore")
            return

        self._started = True
        self._start_time = time.monotonic()

        # Démarrer les health checks si activés
        if self._config.health_check_enabled:
            self._health_check_task = asyncio.create_task(
                self._health_check_loop(),
                name="proxy_health_checks",
            )

        self._logger.info(
            "ProxyManager démarré: strategy={}, health_check={}s",
            self._config.rotation_strategy.value,
            self._config.health_check_interval_seconds,
        )

    async def stop(self) -> None:
        """Arrête le gestionnaire et libère les ressources."""
        if not self._started:
            return

        self._started = False

        # Annuler la tâche de health checks
        if self._health_check_task is not None:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
            self._health_check_task = None

        async with self._proxies_lock:
            self._proxies.clear()

        self._logger.info("ProxyManager arrêté")

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
    def proxies_count(self) -> int:
        """Nombre total de proxies."""
        return len(self._proxies)

    @property
    def enabled_proxies_count(self) -> int:
        """Nombre de proxies activés."""
        return sum(1 for p in self._proxies.values() if p.is_enabled)

    @property
    def healthy_proxies_count(self) -> int:
        """Nombre de proxies sains."""
        return sum(1 for p in self._proxies.values() if p.health == ProxyHealth.HEALTHY)

    # ------------------------------------------------------------------------
    # API publique — Gestion des proxies
    # ------------------------------------------------------------------------

    async def add_proxy(self, config: ProxyConfig) -> str:
        """Ajoute un proxy au gestionnaire.

        Args:
            config: Configuration du proxy.

        Returns:
            Identifiant unique du proxy ajouté.

        Raises:
            ProxyManagerNotStartedError: Si le gestionnaire n'est pas démarré.
            ProxyError: Si la limite de proxies est atteinte.
        """
        self._ensure_started()

        async with self._proxies_lock:
            if len(self._proxies) >= self._config.max_proxies:
                raise ProxyError(
                    f"Limite de proxies atteinte ({self._config.max_proxies})"
                )

            proxy_id = config.unique_id
            if proxy_id in self._proxies:
                self._logger.debug("Proxy déjà présent: {}", config.masked_url)
                return proxy_id

            entry = _ProxyEntry(config)
            self._proxies[proxy_id] = entry

            self._logger.info(
                "Proxy ajouté: {} ({})",
                config.display_name,
                config.proxy_type.label,
            )
            return proxy_id

    async def remove_proxy(self, proxy_url: str) -> bool:
        """Supprime un proxy du gestionnaire.

        Args:
            proxy_url: URL du proxy à supprimer.

        Returns:
            True si le proxy a été supprimé.
        """
        proxy_id = hashlib.sha256(proxy_url.encode()).hexdigest()[:16]

        async with self._proxies_lock:
            if proxy_id in self._proxies:
                del self._proxies[proxy_id]
                self._logger.info("Proxy supprimé: {}", proxy_url)
                return True
            return False

    async def enable_proxy(self, proxy_url: str) -> bool:
        """Active un proxy."""
        proxy_id = hashlib.sha256(proxy_url.encode()).hexdigest()[:16]

        async with self._proxies_lock:
            entry = self._proxies.get(proxy_id)
            if entry is None:
                return False
            # Recréer l'entrée avec enabled=True
            new_config = entry.config.model_copy(update={"enabled": True})
            self._proxies[proxy_id] = _ProxyEntry(new_config)
            return True

    async def disable_proxy(self, proxy_url: str) -> bool:
        """Désactive un proxy."""
        proxy_id = hashlib.sha256(proxy_url.encode()).hexdigest()[:16]

        async with self._proxies_lock:
            entry = self._proxies.get(proxy_id)
            if entry is None:
                return False
            new_config = entry.config.model_copy(update={"enabled": False})
            self._proxies[proxy_id] = _ProxyEntry(new_config)
            return True

    async def clear_proxies(self) -> int:
        """Supprime tous les proxies.

        Returns:
            Nombre de proxies supprimés.
        """
        async with self._proxies_lock:
            count = len(self._proxies)
            self._proxies.clear()
            return count

    # ------------------------------------------------------------------------
    # API publique — Chargement depuis fichier
    # ------------------------------------------------------------------------

    async def load_from_file(
        self,
        path: Path,
        *,
        default_type: ProxyType = ProxyType.HTTP,
        default_region: str | None = None,
    ) -> int:
        """Charge une liste de proxies depuis un fichier texte.

        Format du fichier (une ligne par proxy) :
            - http://host:port
            - http://user:pass@host:port
            - socks5h://host:port
            - host:port (type par défaut utilisé)
            - # commentaires ignorés
            - lignes vides ignorées

        Args:
            path: Chemin vers le fichier.
            default_type: Type par défaut si non spécifié dans l'URL.
            default_region: Région par défaut pour tous les proxies.

        Returns:
            Nombre de proxies chargés.

        Raises:
            ProxyLoadError: Si le fichier ne peut être chargé.
        """
        self._ensure_started()

        if not path.exists():
            raise ProxyLoadError(path, "Fichier inexistant")

        try:
            content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except Exception as e:
            raise ProxyLoadError(path, str(e)) from e

        loaded = 0
        for line_num, line in enumerate(content.splitlines(), start=1):
            line = line.strip()

            # Ignorer les commentaires et lignes vides
            if not line or line.startswith("#"):
                continue

            try:
                config = self._parse_proxy_line(line, default_type, default_region)
                await self.add_proxy(config)
                loaded += 1
            except Exception as e:
                self._logger.warning(
                    "Ligne {} ignorée: {} — {}",
                    line_num,
                    line[:50],
                    e,
                )

        self._logger.info(
            "Fichier de proxies chargé: {} proxies depuis {}",
            loaded,
            path.name,
        )
        return loaded

    def _parse_proxy_line(
        self,
        line: str,
        default_type: ProxyType,
        default_region: str | None,
    ) -> ProxyConfig:
        """Parse une ligne de fichier en ProxyConfig."""
        match = self._PROXY_LINE_PATTERN.match(line)
        if not match:
            raise InvalidProxyUrlError(line, "Format invalide")

        groups = match.groupdict()
        scheme = (groups.get("type") or default_type.value).lower()
        host = groups["host"]
        port = groups.get("port")
        username = groups.get("user")
        password = groups.get("pass")

        # Construire l'URL
        if port:
            url = f"{scheme}://{host}:{port}"
        else:
            url = f"{scheme}://{host}"

        return ProxyConfig(
            url=url,
            username=username,
            password=password,
            region=default_region,
        )

    # ------------------------------------------------------------------------
    # API publique — Sélection de proxy
    # ------------------------------------------------------------------------

    async def get_proxy_for_url(
        self,
        url: str,
        *,
        strategy: RotationStrategy | None = None,
        required_region: str | None = None,
        required_tags: list[str] | None = None,
    ) -> str | None:
        """Sélectionne un proxy pour une URL donnée.

        Args:
            url: URL cible de la requête.
            strategy: Stratégie de rotation (override du défaut).
            required_region: Code région requis (ex: "FR", "US").
            required_tags: Tags requis (tous doivent correspondre).

        Returns:
            URL du proxy avec credentials (pour httpx), ou None si aucun
            proxy ne correspond (ou si l'URL est dans no_proxy).
        """
        self._ensure_started()

        # Vérifier no_proxy
        if self._is_no_proxy(url):
            self._logger.trace("URL dans no_proxy: {}", url)
            return None

        # Filtrer les proxies éligibles
        candidates = await self._get_eligible_proxies(
            required_region=required_region,
            required_tags=required_tags,
        )

        if not candidates:
            if self._config.fallback_to_direct:
                self._logger.debug("Aucun proxy éligible, fallback direct")
                return None
            raise NoProxyAvailableError(url, "Aucun proxy sain disponible")

        # Appliquer la stratégie de rotation
        effective_strategy = strategy or self._config.rotation_strategy
        selected = await self._select_proxy(candidates, effective_strategy)

        if selected is None:
            if self._config.fallback_to_direct:
                return None
            raise NoProxyAvailableError(url, "Échec de la sélection")

        async with self._stats_lock:
            self._last_rotation_at = datetime.now(UTC)

        self._logger.trace(
            "Proxy sélectionné pour {}: {} (strategy={})",
            url,
            selected.config.display_name,
            effective_strategy.value,
        )

        return selected.config.authenticated_url

    def _is_no_proxy(self, url: str) -> bool:
        """Vérifie si une URL doit bypass le proxy."""
        try:
            parsed = urlparse(url)
            host = parsed.hostname or ""
            host_lower = host.lower()

            for domain in self._config.no_proxy_domains:
                domain_lower = domain.lower()
                if host_lower == domain_lower:
                    return True
                if host_lower.endswith("." + domain_lower):
                    return True
            return False
        except Exception:
            return False

    async def _get_eligible_proxies(
        self,
        *,
        required_region: str | None = None,
        required_tags: list[str] | None = None,
    ) -> list[_ProxyEntry]:
        """Récupère les proxies éligibles selon les filtres."""
        async with self._proxies_lock:
            proxies = list(self._proxies.values())

        # Filtrer par état
        eligible = [p for p in proxies if p.is_usable]

        # Filtrer par région
        if required_region is not None:
            required_region_upper = required_region.upper()
            eligible = [
                p for p in eligible
                if p.config.region is not None
                and p.config.region.upper() == required_region_upper
            ]

        # Filtrer par tags
        if required_tags:
            required_set = set(t.lower() for t in required_tags)
            eligible = [
                p for p in eligible
                if required_set.issubset(set(t.lower() for t in p.config.tags))
            ]

        return eligible

    async def _select_proxy(
        self,
        candidates: list[_ProxyEntry],
        strategy: RotationStrategy,
    ) -> _ProxyEntry | None:
        """Sélectionne un proxy selon la stratégie."""
        if not candidates:
            return None

        if len(candidates) == 1:
            return candidates[0]

        if strategy == RotationStrategy.ROUND_ROBIN:
            return await self._select_round_robin(candidates)
        if strategy == RotationStrategy.RANDOM:
            return random.choice(candidates)
        if strategy == RotationStrategy.LEAST_USED:
            return self._select_least_used(candidates)
        if strategy == RotationStrategy.HEALTH_BASED:
            return self._select_health_based(candidates)
        if strategy == RotationStrategy.WEIGHTED:
            return self._select_weighted(candidates)

        return candidates[0]

    async def _select_round_robin(
        self,
        candidates: list[_ProxyEntry],
    ) -> _ProxyEntry:
        """Sélection round-robin."""
        async with self._rr_lock:
            index = self._rr_index % len(candidates)
            self._rr_index += 1
            return candidates[index]

    @staticmethod
    def _select_least_used(candidates: list[_ProxyEntry]) -> _ProxyEntry:
        """Sélection du proxy le moins utilisé."""
        return min(
            candidates,
            key=lambda p: p.stats.total_requests,
        )

    @staticmethod
    def _select_health_based(candidates: list[_ProxyEntry]) -> _ProxyEntry:
        """Sélection du proxy avec le meilleur score de santé."""
        return max(
            candidates,
            key=lambda p: p.stats.health_score,
        )

    @staticmethod
    def _select_weighted(candidates: list[_ProxyEntry]) -> _ProxyEntry:
        """Sélection pondérée par le score de santé et le weight config."""
        weights = [
            p.stats.health_score * p.config.weight
            for p in candidates
        ]
        # Éviter les poids nuls
        if all(w <= 0 for w in weights):
            return random.choice(candidates)
        return random.choices(candidates, weights=weights, k=1)[0]

    # ------------------------------------------------------------------------
    # API publique — Reporting
    # ------------------------------------------------------------------------

    async def report_success(
        self,
        proxy_url: str,
        *,
        latency_ms: float = 0.0,
    ) -> None:
        """Signale un succès d'utilisation du proxy.

        Args:
            proxy_url: URL du proxy utilisé.
            latency_ms: Latence de la requête en millisecondes.
        """
        entry = await self._get_entry_by_url(proxy_url)
        if entry is None:
            return

        await entry.record_success(latency_ms)
        await entry.update_health(
            self._config.unhealthy_threshold,
            self._config.recovery_threshold,
        )

    async def report_failure(
        self,
        proxy_url: str,
        *,
        error: str | None = None,
    ) -> None:
        """Signale un échec d'utilisation du proxy.

        Args:
            proxy_url: URL du proxy utilisé.
            error: Message d'erreur (optionnel).
        """
        entry = await self._get_entry_by_url(proxy_url)
        if entry is None:
            return

        await entry.record_failure(error)
        await entry.update_health(
            self._config.unhealthy_threshold,
            self._config.recovery_threshold,
        )

        # Log si le proxy devient UNHEALTHY
        if entry.health == ProxyHealth.UNHEALTHY:
            self._logger.warning(
                "Proxy marqué UNHEALTHY: {} (échecs consécutifs: {})",
                entry.config.display_name,
                entry.stats.consecutive_failures,
            )

    async def _get_entry_by_url(self, proxy_url: str) -> _ProxyEntry | None:
        """Récupère une entrée par URL (avec ou sans credentials)."""
        # Normaliser l'URL pour la comparaison
        parsed = urlparse(proxy_url)
        # Retirer les credentials pour la comparaison
        normalized = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port:
            normalized += f":{parsed.port}"

        async with self._proxies_lock:
            for entry in self._proxies.values():
                if entry.config.url == normalized or entry.config.url == proxy_url:
                    return entry
        return None

    # ------------------------------------------------------------------------
    # API publique — Health checks
    # ------------------------------------------------------------------------

    async def check_proxy_health(self, proxy_url: str) -> ProxyHealthCheck:
        """Effectue un health check sur un proxy spécifique.

        Args:
            proxy_url: URL du proxy à tester.

        Returns:
            Résultat du health check.
        """
        self._ensure_started()

        entry = await self._get_entry_by_url(proxy_url)
        if entry is None:
            raise ProxyError(f"Proxy inconnu: {proxy_url}")

        check = await self._perform_health_check(entry)

        async with self._proxies_lock:
            await entry.record_health_check(check)

        async with self._stats_lock:
            self._total_health_checks += 1

        return check

    async def check_all_proxies_health(self) -> list[ProxyHealthCheck]:
        """Effectue des health checks sur tous les proxies.

        Returns:
            Liste des résultats.
        """
        self._ensure_started()

        async with self._proxies_lock:
            entries = list(self._proxies.values())

        results: list[ProxyHealthCheck] = []
        for entry in entries:
            try:
                check = await self._perform_health_check(entry)
                await entry.record_health_check(check)
                results.append(check)
            except Exception as e:
                self._logger.warning(
                    "Health check échoué pour {}: {}",
                    entry.config.display_name,
                    e,
                )

        async with self._stats_lock:
            self._total_health_checks += len(results)

        return results

    async def _perform_health_check(self, entry: _ProxyEntry) -> ProxyHealthCheck:
        """Effectue un health check sur un proxy.

        Utilise une requête HTTP simple via httpx pour tester le proxy.
        """
        start_time = time.monotonic()
        proxy_url = entry.config.authenticated_url

        try:
            # Import httpx ici pour éviter les dépendances circulaires
            import httpx

            async with httpx.AsyncClient(
                proxies=proxy_url,
                timeout=self._config.health_check_timeout_seconds,
            ) as client:
                response = await client.get(self._config.health_check_url)
                latency_ms = (time.monotonic() - start_time) * 1000.0

                return ProxyHealthCheck(
                    proxy_url=entry.config.url,
                    healthy=response.status_code < 400,
                    latency_ms=latency_ms,
                    status_code=response.status_code,
                )

        except Exception as e:
            latency_ms = (time.monotonic() - start_time) * 1000.0
            return ProxyHealthCheck(
                proxy_url=entry.config.url,
                healthy=False,
                latency_ms=latency_ms,
                error=str(e),
            )

    async def _health_check_loop(self) -> None:
        """Boucle de health checks périodiques."""
        try:
            while self._started:
                await asyncio.sleep(self._config.health_check_interval_seconds)

                if not self._proxies:
                    continue

                self._logger.debug("Health check périodique en cours...")
                try:
                    await self.check_all_proxies_health()
                except Exception as e:
                    self._logger.error("Erreur dans le health check loop: {}", e)

        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> ProxyManagerStats:
        """Retourne les statistiques globales du gestionnaire."""
        self._ensure_started()

        async with self._proxies_lock:
            entries = list(self._proxies.values())

        total_requests = 0
        total_successes = 0
        total_failures = 0
        healthy = 0
        degraded = 0
        unhealthy = 0
        unknown = 0
        enabled = 0

        for entry in entries:
            stats = entry.stats
            total_requests += stats.total_requests
            total_successes += stats.successful_requests
            total_failures += stats.failed_requests

            if entry.health == ProxyHealth.HEALTHY:
                healthy += 1
            elif entry.health == ProxyHealth.DEGRADED:
                degraded += 1
            elif entry.health == ProxyHealth.UNHEALTHY:
                unhealthy += 1
            else:
                unknown += 1

            if entry.is_enabled:
                enabled += 1

        uptime = 0.0
        if self._start_time > 0:
            uptime = time.monotonic() - self._start_time

        async with self._stats_lock:
            total_health_checks = self._total_health_checks
            last_rotation = self._last_rotation_at

        return ProxyManagerStats(
            total_proxies=len(entries),
            enabled_proxies=enabled,
            healthy_count=healthy,
            degraded_count=degraded,
            unhealthy_count=unhealthy,
            unknown_count=unknown,
            total_requests=total_requests,
            total_successes=total_successes,
            total_failures=total_failures,
            total_health_checks=total_health_checks,
            rotation_strategy=self._config.rotation_strategy,
            last_rotation_at=last_rotation,
            uptime_seconds=uptime,
        )

    async def get_proxy_stats(self, proxy_url: str) -> ProxyStats | None:
        """Retourne les statistiques d'un proxy spécifique.

        Args:
            proxy_url: URL du proxy.

        Returns:
            Statistiques du proxy ou None si inconnu.
        """
        entry = await self._get_entry_by_url(proxy_url)
        if entry is None:
            return None
        return entry.stats

    async def list_proxies(
        self,
        *,
        enabled_only: bool = False,
        healthy_only: bool = False,
    ) -> list[ProxyConfig]:
        """Liste les proxies avec filtrage optionnel.

        Args:
            enabled_only: Si True, ne retourne que les proxies activés.
            healthy_only: Si True, ne retourne que les proxies sains.

        Returns:
            Liste des configurations de proxies.
        """
        async with self._proxies_lock:
            entries = list(self._proxies.values())

        result: list[ProxyConfig] = []
        for entry in entries:
            if enabled_only and not entry.is_enabled:
                continue
            if healthy_only and not entry.is_usable:
                continue
            result.append(entry.config)

        return result

    # ------------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # ------------------------------------------------------------------------

    def _ensure_started(self) -> None:
        """Vérifie que le gestionnaire est démarré."""
        if not self._started:
            raise ProxyManagerNotStartedError()

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<ProxyManager status={status} "
            f"proxies={len(self._proxies)} "
            f"healthy={self.healthy_proxies_count}>"
        )

    def __len__(self) -> int:
        """Nombre de proxies."""
        return len(self._proxies)


# ============================================================================
# HELPERS PUBLICS — Fonctions utilitaires
# ============================================================================


def parse_proxy_url(url: str) -> ProxyConfig:
    """Parse une URL de proxy en ProxyConfig.

    Args:
        url: URL du proxy (ex: http://user:pass@host:port).

    Returns:
        Instance de ProxyConfig.

    Example:
        >>> config = parse_proxy_url("http://user:pass@proxy.example.com:8080")
        >>> print(config.host)
        'proxy.example.com'
    """
    parsed = urlparse(url)
    return ProxyConfig(
        url=f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 8080}",
        username=parsed.username,
        password=parsed.password,
    )


def build_proxies_dict(proxy_url: str) -> dict[str, str]:
    """Construit un dictionnaire de proxies pour httpx.

    Args:
        proxy_url: URL du proxy (avec credentials si nécessaire).

    Returns:
        Dictionnaire au format attendu par httpx.

    Example:
        >>> proxies = build_proxies_dict("http://user:pass@proxy:8080")
        >>> print(proxies)
        {'http://': 'http://user:pass@proxy:8080', 'https://': 'http://user:pass@proxy:8080'}
    """
    return {
        "http://": proxy_url,
        "https://": proxy_url,
    }


async def load_proxies_from_file(
    path: Path,
    *,
    manager: ProxyManager | None = None,
) -> list[ProxyConfig]:
    """Charge une liste de proxies depuis un fichier (one-shot).

    Fonction utilitaire pour usage externe sans gestion de lifecycle.

    Args:
        path: Chemin vers le fichier.
        manager: ProxyManager existant (optionnel, créé si None).

    Returns:
        Liste des configurations chargées.
    """
    if manager is None:
        async with ProxyManager() as m:
            await m.load_from_file(path)
            return await m.list_proxies()
    else:
        await manager.load_from_file(path)
        return await manager.list_proxies()


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "ProxyError",
    "ProxyManagerNotStartedError",
    "NoProxyAvailableError",
    "InvalidProxyUrlError",
    "ProxyLoadError",
    "HealthCheckError",
    # Enums
    "ProxyType",
    "RotationStrategy",
    "ProxyHealth",
    # Modèles — Configuration
    "ProxyConfig",
    "ProxyManagerConfig",
    # Modèles — Health check et stats
    "ProxyHealthCheck",
    "ProxyStats",
    "ProxyManagerStats",
    # Classe principale
    "ProxyManager",
    # Helpers
    "parse_proxy_url",
    "build_proxies_dict",
    "load_proxies_from_file",
]
