"""Système d'EventBus centralisé pour la communication inter-modules.

Ce module fournit un système de publication/abonnement (pub/sub) asynchrone
robuste pour NexusDL, permettant aux différents modules de communiquer de
manière découplée via des événements. Il est utilisé pour :

    - Notification de progression des téléchargements (vers interfaces)
    - Signalement d'état (scan, registry, session)
    - Logging structuré (via EventBusSink)
    - Synchronisation inter-composants
    - Monitoring et télémétrie
    - Déclenchement d'actions réactives

**Caractéristiques** :
    - Publication/abonnement asynchrone (asyncio)
    - Handlers sync et async supportés
    - Pattern matching avec wildcards (ex: "download.*")
    - Priorité des handlers (LOW, NORMAL, HIGH, CRITICAL)
    - Handlers one-shot (auto-unsubscribe après première exécution)
    - Timeout configurable par handler
    - File d'événements avec backpressure (maxsize)
    - Dead letter queue pour événements non traités
    - Statistiques détaillées (émissions, handlers, erreurs)
    - Thread-safe (locks asyncio)
    - Événements avec payload structuré (Pydantic)
    - Instance globale `event_bus` pour accès facile
    - Méthodes utilitaires : wait_for_event(), drain(), clear()

**Architecture** :
    EventBus
        ├── EventType (enum) : types d'événements standards
        ├── EventPriority (enum) : LOW, NORMAL, HIGH, CRITICAL
        ├── Event (Pydantic) : événement avec payload
        ├── Subscription (Pydantic) : abonnement actif
        ├── EventBusConfig (Pydantic) : configuration
        ├── EventBusStats (Pydantic) : statistiques
        └── _HandlerEntry (interne) : handler + métadonnées

**Exemples d'événements émis** :
    - download.task.started       : Tâche de téléchargement démarrée
    - download.task.completed     : Tâche terminée avec succès
    - download.task.failed        : Tâche échouée
    - download.chapter.started    : Chapitre en cours de traitement
    - download.chapter.completed  : Chapitre terminé
    - download.page.completed     : Page téléchargée
    - download.page.failed        : Échec de téléchargement d'une page
    - scan.started                : Scan de bibliothèque démarré
    - scan.completed              : Scan terminé
    - scan.state_changed          : Changement d'état du scan
    - registry.started            : Registre initialisé
    - registry.reloaded           : Registre rechargé
    - registry.stopped            : Registre arrêté
    - progress.updated            : Progression mise à jour
    - retry.attempt               : Tentative de retry
    - retry.completed             : Retry terminé
    - log.message                 : Message de log (via EventBusSink)
    - config.changed              : Configuration modifiée
    - error.occurred              : Erreur globale capturée
    - custom.*                    : Événements personnalisés

Exemple d'utilisation :
    >>> from nexusdl.core.events import event_bus, Event, EventType
    >>>
    >>> # S'abonner à un événement
    >>> @event_bus.on("download.task.completed")
    ... async def on_task_completed(event: Event):
    ...     print(f"Tâche terminée: {event.payload['task_id']}")
    >>>
    >>> # Émettre un événement
    >>> await event_bus.emit(
    ...     "download.task.completed",
    ...     payload={"task_id": "abc123", "pages": 42},
    ... )
    >>>
    >>> # Wildcards : s'abonner à tous les événements de download
    >>> @event_bus.on("download.*")
    ... async def on_any_download(event: Event):
    ...     print(f"Événement download: {event.type}")
    >>>
    >>> # Handler one-shot (auto-unsubscribe)
    >>> await event_bus.once("scan.completed", callback)
    >>>
    >>> # Attendre un événement spécifique
    >>> event = await event_bus.wait_for_event("scan.completed", timeout=60.0)
    >>>
    >>> # Statistiques
    >>> stats = await event_bus.get_stats()
    >>> print(f"Événements émis: {stats.total_emitted}")

Intégration :
    - core/downloader/* : émet des événements de progression
    - core/library/scanner.py : émet des événements de scan
    - core/registry/site_registry.py : émet des événements de registre
    - core/logger.py : EventBusSink émet des logs
    - interfaces/cli/ : s'abonne pour affichage TUI
    - interfaces/web/backend/websocket.py : s'abonne pour WebSocket
    - interfaces/gui/ : s'abonne pour mise à jour GUI
"""

from __future__ import annotations

import asyncio
import fnmatch
import inspect
import time
import traceback
from collections import OrderedDict, defaultdict
from datetime import UTC, datetime
from enum import Enum, IntEnum
from typing import (
    Any,
    Awaitable,
    Callable,
    ClassVar,
    Final,
    Self,
    TypeVar,
    Union,
)
from uuid import UUID, uuid4

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# CONSTANTES
# ============================================================================


# Taille par défaut de la file d'événements
_DEFAULT_QUEUE_SIZE: Final[int] = 10000

# Timeout par défaut pour les handlers (secondes)
_DEFAULT_HANDLER_TIMEOUT: Final[float] = 30.0

# Timeout par défaut pour wait_for_event (secondes)
_DEFAULT_WAIT_TIMEOUT: Final[float] = 60.0

# Nombre maximum de handlers par type d'événement
_MAX_HANDLERS_PER_EVENT: Final[int] = 1000

# Nombre maximum d'événements dans la dead letter queue
_MAX_DEAD_LETTER_SIZE: Final[int] = 1000


# ============================================================================
# EXCEPTIONS
# ============================================================================


class EventError(NexusDLError):
    """Exception de base pour les erreurs du système d'événements."""


class EventBusNotStartedError(EventError):
    """Exception levée lorsqu'on utilise l'EventBus avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "EventBus must be started before use. Call await event_bus.start()"
        )


class EventBusAlreadyStartedError(EventError):
    """Exception levée lorsqu'on appelle start() sur un EventBus déjà démarré."""

    def __init__(self) -> None:
        super().__init__("EventBus is already started")


class InvalidEventTypeError(EventError):
    """Exception levée lorsqu'un type d'événement est invalide."""

    def __init__(self, event_type: str, reason: str = "") -> None:
        msg = f"Type d'événement invalide: '{event_type}'"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.event_type = event_type
        self.reason = reason


class HandlerError(EventError):
    """Exception levée lorsqu'un handler échoue."""

    def __init__(
        self,
        event_type: str,
        handler_name: str,
        reason: str = "",
    ) -> None:
        msg = f"Handler '{handler_name}' a échoué pour l'événement '{event_type}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.event_type = event_type
        self.handler_name = handler_name
        self.reason = reason


class HandlerTimeoutError(EventError):
    """Exception levée lorsqu'un handler dépasse son timeout."""

    def __init__(
        self,
        event_type: str,
        handler_name: str,
        timeout: float,
    ) -> None:
        super().__init__(
            f"Handler '{handler_name}' a dépassé le timeout de {timeout:.1f}s "
            f"pour l'événement '{event_type}'"
        )
        self.event_type = event_type
        self.handler_name = handler_name
        self.timeout = timeout


class SubscriptionNotFoundError(EventError):
    """Exception levée lorsqu'un abonnement est introuvable."""

    def __init__(self, subscription_id: str) -> None:
        super().__init__(f"Abonnement introuvable: {subscription_id}")
        self.subscription_id = subscription_id


class EventQueueFullError(EventError):
    """Exception levée lorsque la file d'événements est pleine."""

    def __init__(self, queue_size: int) -> None:
        super().__init__(
            f"File d'événements pleine ({queue_size} événements en attente)"
        )
        self.queue_size = queue_size


# ============================================================================
# ENUMS
# ============================================================================


class EventType(str, Enum):
    """Types d'événements standards de NexusDL.

    Cette enum liste les types d'événements les plus courants.
    Des types personnalisés peuvent être utilisés via des chaînes.
    """

    # Téléchargement
    DOWNLOAD_TASK_STARTED = "download.task.started"
    DOWNLOAD_TASK_COMPLETED = "download.task.completed"
    DOWNLOAD_TASK_FAILED = "download.task.failed"
    DOWNLOAD_TASK_CANCELLED = "download.task.cancelled"
    DOWNLOAD_TASK_PAUSED = "download.task.paused"
    DOWNLOAD_TASK_RESUMED = "download.task.resumed"
    DOWNLOAD_TASK_PROGRESS = "download.task.progress"

    DOWNLOAD_CHAPTER_STARTED = "download.chapter.started"
    DOWNLOAD_CHAPTER_COMPLETED = "download.chapter.completed"
    DOWNLOAD_CHAPTER_FAILED = "download.chapter.failed"

    DOWNLOAD_PAGE_STARTED = "download.page.started"
    DOWNLOAD_PAGE_COMPLETED = "download.page.completed"
    DOWNLOAD_PAGE_FAILED = "download.page.failed"

    # Bibliothèque
    SCAN_STARTED = "scan.started"
    SCAN_COMPLETED = "scan.completed"
    SCAN_FAILED = "scan.failed"
    SCAN_STATE_CHANGED = "scan.state_changed"
    SCAN_PROGRESS = "scan.progress"

    LIBRARY_MANGA_ADDED = "library.manga.added"
    LIBRARY_MANGA_REMOVED = "library.manga.removed"
    LIBRARY_MANGA_UPDATED = "library.manga.updated"
    LIBRARY_CHAPTER_ADDED = "library.chapter.added"
    LIBRARY_CHAPTER_REMOVED = "library.chapter.removed"

    # Registre
    REGISTRY_STARTED = "registry.started"
    REGISTRY_STOPPED = "registry.stopped"
    REGISTRY_RELOADED = "registry.reloaded"
    REGISTRY_SITE_ADDED = "registry.site.added"
    REGISTRY_SITE_REMOVED = "registry.site.removed"

    # Progression
    PROGRESS_UPDATED = "progress.updated"
    PROGRESS_RESET = "progress.reset"

    # Retry
    RETRY_ATTEMPT = "retry.attempt"
    RETRY_COMPLETED = "retry.completed"
    RETRY_FAILED = "retry.failed"

    # Session
    SESSION_STARTED = "session.started"
    SESSION_STOPPED = "session.stopped"
    SESSION_ERROR = "session.error"

    # Logging
    LOG_MESSAGE = "log.message"

    # Configuration
    CONFIG_CHANGED = "config.changed"
    CONFIG_RELOADED = "config.reloaded"

    # Erreurs
    ERROR_OCCURRED = "error.occurred"
    ERROR_RECOVERED = "error.recovered"

    # Personnalisé
    CUSTOM = "custom"

    @property
    def label(self) -> str:
        """Libellé humain du type d'événement."""
        return _EVENT_TYPE_LABELS.get(self, self.value)

    @property
    def domain(self) -> str:
        """Domaine de l'événement (ex: 'download', 'scan')."""
        return self.value.split(".")[0] if "." in self.value else "unknown"


# Labels humains pour les types d'événements
_EVENT_TYPE_LABELS: Final[dict[EventType, str]] = {
    EventType.DOWNLOAD_TASK_STARTED: "Tâche de téléchargement démarrée",
    EventType.DOWNLOAD_TASK_COMPLETED: "Tâche de téléchargement terminée",
    EventType.DOWNLOAD_TASK_FAILED: "Tâche de téléchargement échouée",
    EventType.DOWNLOAD_TASK_CANCELLED: "Tâche de téléchargement annulée",
    EventType.DOWNLOAD_TASK_PAUSED: "Tâche de téléchargement en pause",
    EventType.DOWNLOAD_TASK_RESUMED: "Tâche de téléchargement reprise",
    EventType.DOWNLOAD_TASK_PROGRESS: "Progression de la tâche",
    EventType.DOWNLOAD_CHAPTER_STARTED: "Chapitre démarré",
    EventType.DOWNLOAD_CHAPTER_COMPLETED: "Chapitre terminé",
    EventType.DOWNLOAD_CHAPTER_FAILED: "Chapitre échoué",
    EventType.DOWNLOAD_PAGE_STARTED: "Page démarrée",
    EventType.DOWNLOAD_PAGE_COMPLETED: "Page terminée",
    EventType.DOWNLOAD_PAGE_FAILED: "Page échouée",
    EventType.SCAN_STARTED: "Scan démarré",
    EventType.SCAN_COMPLETED: "Scan terminé",
    EventType.SCAN_FAILED: "Scan échoué",
    EventType.SCAN_STATE_CHANGED: "État du scan modifié",
    EventType.SCAN_PROGRESS: "Progression du scan",
    EventType.LIBRARY_MANGA_ADDED: "Manga ajouté à la bibliothèque",
    EventType.LIBRARY_MANGA_REMOVED: "Manga retiré de la bibliothèque",
    EventType.LIBRARY_MANGA_UPDATED: "Manga mis à jour",
    EventType.LIBRARY_CHAPTER_ADDED: "Chapitre ajouté",
    EventType.LIBRARY_CHAPTER_REMOVED: "Chapitre retiré",
    EventType.REGISTRY_STARTED: "Registre démarré",
    EventType.REGISTRY_STOPPED: "Registre arrêté",
    EventType.REGISTRY_RELOADED: "Registre rechargé",
    EventType.REGISTRY_SITE_ADDED: "Site ajouté au registre",
    EventType.REGISTRY_SITE_REMOVED: "Site retiré du registre",
    EventType.PROGRESS_UPDATED: "Progression mise à jour",
    EventType.PROGRESS_RESET: "Progression réinitialisée",
    EventType.RETRY_ATTEMPT: "Tentative de retry",
    EventType.RETRY_COMPLETED: "Retry terminé",
    EventType.RETRY_FAILED: "Retry échoué",
    EventType.SESSION_STARTED: "Session démarrée",
    EventType.SESSION_STOPPED: "Session arrêtée",
    EventType.SESSION_ERROR: "Erreur de session",
    EventType.LOG_MESSAGE: "Message de log",
    EventType.CONFIG_CHANGED: "Configuration modifiée",
    EventType.CONFIG_RELOADED: "Configuration rechargée",
    EventType.ERROR_OCCURRED: "Erreur survenue",
    EventType.ERROR_RECOVERED: "Erreur récupérée",
    EventType.CUSTOM: "Événement personnalisé",
}


class EventPriority(IntEnum):
    """Priorité d'exécution des handlers.

    Les handlers avec priorité plus haute sont exécutés en premier.
    """

    LOWEST = 0
    LOW = 25
    NORMAL = 50
    HIGH = 75
    HIGHEST = 100
    CRITICAL = 125

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            EventPriority.LOWEST: "Très basse",
            EventPriority.LOW: "Basse",
            EventPriority.NORMAL: "Normale",
            EventPriority.HIGH: "Haute",
            EventPriority.HIGHEST: "Très haute",
            EventPriority.CRITICAL: "Critique",
        }[self]


class EventState(str, Enum):
    """État d'un événement dans le cycle de vie.

    PENDING   : En attente de traitement.
    PROCESSING: En cours de traitement par les handlers.
    COMPLETED : Traité avec succès par tous les handlers.
    FAILED    : Au moins un handler a échoué.
    TIMEOUT   : Timeout dépassé.
    DROPPED   : Événement abandonné (file pleine).
    """

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    DROPPED = "dropped"

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            EventState.PENDING: "⏳",
            EventState.PROCESSING: "⚙️",
            EventState.COMPLETED: "✅",
            EventState.FAILED: "❌",
            EventState.TIMEOUT: "⏱️",
            EventState.DROPPED: "🗑️",
        }[self]


# ============================================================================
# MODÈLES PYDANTIC — Événements
# ============================================================================


class Event(BaseModel):
    """Événement émis dans le système.

    Représente un événement avec son type, son payload, et ses métadonnées.
    Les événements sont immuables après création.

    Attributes:
        id: Identifiant unique de l'événement (UUID).
        type: Type d'événement (chaîne ou EventType).
        payload: Données associées à l'événement.
        source: Module/component source de l'événement.
        timestamp: Timestamp de création.
        correlation_id: ID de corrélation pour traçabilité.
        metadata: Métadonnées additionnelles.
    """

    id: UUID = Field(
        default_factory=uuid4,
        description="Identifiant unique de l'événement.",
    )
    type: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Type d'événement (ex: 'download.task.completed').",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Données associées à l'événement.",
    )
    source: str = Field(
        default="",
        max_length=100,
        description="Module/component source.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de création.",
    )
    correlation_id: UUID | None = Field(
        default=None,
        description="ID de corrélation pour traçabilité.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Métadonnées additionnelles.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        """Valide le type d'événement."""
        v = v.strip()
        if not v:
            raise InvalidEventTypeError(v, "Le type ne peut pas être vide")

        # Vérifier le format (domaine.sous-domaine.action)
        parts = v.split(".")
        if len(parts) < 2:
            raise InvalidEventTypeError(
                v,
                "Le type doit être au format 'domaine.action' (ex: 'download.task.completed')",
            )

        # Vérifier les caractères autorisés
        for part in parts:
            if not part.replace("_", "").replace("*", "").isalnum():
                raise InvalidEventTypeError(
                    v,
                    f"Caractères invalides dans la partie '{part}'",
                )

        return v

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def domain(self) -> str:
        """Domaine de l'événement (première partie du type)."""
        return self.type.split(".")[0] if "." in self.type else self.type

    @property
    def action(self) -> str:
        """Action de l'événement (dernière partie du type)."""
        parts = self.type.split(".")
        return parts[-1] if parts else self.type

    @property
    def event_type_enum(self) -> EventType | None:
        """Retourne l'EventType enum si le type est standard, None sinon."""
        try:
            return EventType(self.type)
        except ValueError:
            return None

    @property
    def is_standard(self) -> bool:
        """Indique si c'est un type d'événement standard (enum)."""
        return self.event_type_enum is not None

    @property
    def age_seconds(self) -> float:
        """Âge de l'événement en secondes."""
        return (datetime.now(UTC) - self.timestamp).total_seconds()

    # --------------------------------------------------------------------
    # Méthodes
    # --------------------------------------------------------------------

    def with_payload(self, **kwargs: Any) -> Event:
        """Retourne un nouvel événement avec payload enrichi.

        Args:
            **kwargs: Paires clé-valeur à ajouter au payload.

        Returns:
            Nouvel événement avec payload mis à jour.
        """
        new_payload = dict(self.payload)
        new_payload.update(kwargs)
        return self.model_copy(update={"payload": new_payload})

    def with_metadata(self, **kwargs: Any) -> Event:
        """Retourne un nouvel événement avec métadonnées enrichies.

        Args:
            **kwargs: Paires clé-valeur à ajouter aux métadonnées.

        Returns:
            Nouvel événement avec métadonnées mises à jour.
        """
        new_metadata = dict(self.metadata)
        new_metadata.update(kwargs)
        return self.model_copy(update={"metadata": new_metadata})

    def matches(self, pattern: str) -> bool:
        """Vérifie si l'événement correspond à un pattern (avec wildcards).

        Args:
            pattern: Pattern à matcher (ex: "download.*", "*.completed").

        Returns:
            True si l'événement correspond au pattern.

        Example:
            >>> event = Event(type="download.task.completed")
            >>> event.matches("download.*")
            True
            >>> event.matches("*.completed")
            True
            >>> event.matches("scan.*")
            False
        """
        return fnmatch.fnmatch(self.type, pattern)

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable (pour WebSocket/API)."""
        return {
            "id": str(self.id),
            "type": self.type,
            "domain": self.domain,
            "action": self.action,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "correlation_id": str(self.correlation_id) if self.correlation_id else None,
            "payload_keys": list(self.payload.keys()),
            "metadata_keys": list(self.metadata.keys()),
            "age_seconds": round(self.age_seconds, 3),
            "is_standard": self.is_standard,
        }

    def __repr__(self) -> str:
        return (
            f"<Event id={str(self.id)[:8]} "
            f"type={self.type!r} "
            f"source={self.source!r} "
            f"payload_keys={list(self.payload.keys())}>"
        )


class Subscription(BaseModel):
    """Abonnement à un type d'événement.

    Représente un handler enregistré pour un type d'événement (ou pattern).

    Attributes:
        id: Identifiant unique de l'abonnement.
        event_pattern: Pattern d'événement (ex: "download.*").
        handler_name: Nom du handler (pour debugging).
        priority: Priorité d'exécution.
        one_shot: Si True, auto-unsubscribe après première exécution.
        timeout: Timeout d'exécution du handler (secondes).
        created_at: Timestamp de création.
        call_count: Nombre d'appels effectués.
        last_called_at: Timestamp du dernier appel.
        error_count: Nombre d'erreurs du handler.
        is_active: Si True, l'abonnement est actif.
    """

    id: UUID = Field(
        default_factory=uuid4,
        description="Identifiant unique de l'abonnement.",
    )
    event_pattern: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Pattern d'événement (ex: 'download.*').",
    )
    handler_name: str = Field(
        default="",
        max_length=200,
        description="Nom du handler.",
    )
    priority: EventPriority = Field(
        default=EventPriority.NORMAL,
        description="Priorité d'exécution.",
    )
    one_shot: bool = Field(
        default=False,
        description="Auto-unsubscribe après première exécution.",
    )
    timeout: float = Field(
        default=_DEFAULT_HANDLER_TIMEOUT,
        gt=0.0,
        le=600.0,
        description="Timeout d'exécution (secondes).",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de création.",
    )
    call_count: int = Field(
        default=0,
        ge=0,
        description="Nombre d'appels effectués.",
    )
    last_called_at: datetime | None = Field(
        default=None,
        description="Timestamp du dernier appel.",
    )
    error_count: int = Field(
        default=0,
        ge=0,
        description="Nombre d'erreurs du handler.",
    )
    is_active: bool = Field(
        default=True,
        description="Si True, l'abonnement est actif.",
    )

    model_config = ConfigDict(frozen=False, extra="forbid")

    @property
    def is_wildcard(self) -> bool:
        """Indique si le pattern contient des wildcards."""
        return "*" in self.event_pattern or "?" in self.event_pattern

    @property
    def success_rate(self) -> float:
        """Taux de succès du handler (0.0 à 1.0)."""
        if self.call_count == 0:
            return 1.0
        return (self.call_count - self.error_count) / self.call_count

    @property
    def age_seconds(self) -> float:
        """Âge de l'abonnement en secondes."""
        return (datetime.now(UTC) - self.created_at).total_seconds()

    @property
    def idle_seconds(self) -> float | None:
        """Temps d'inactivité en secondes (None si jamais appelé)."""
        if self.last_called_at is None:
            return None
        return (datetime.now(UTC) - self.last_called_at).total_seconds()

    def record_call(self, success: bool = True) -> None:
        """Enregistre un appel au handler."""
        self.call_count += 1
        self.last_called_at = datetime.now(UTC)
        if not success:
            self.error_count += 1

    def deactivate(self) -> None:
        """Désactive l'abonnement."""
        self.is_active = False

    def to_summary(self) -> dict[str, Any]:
        """Retourne un résumé sérialisable."""
        return {
            "id": str(self.id),
            "event_pattern": self.event_pattern,
            "handler_name": self.handler_name,
            "priority": self.priority.value,
            "priority_label": self.priority.label,
            "one_shot": self.one_shot,
            "timeout": self.timeout,
            "created_at": self.created_at.isoformat(),
            "call_count": self.call_count,
            "last_called_at": (
                self.last_called_at.isoformat() if self.last_called_at else None
            ),
            "error_count": self.error_count,
            "success_rate": round(self.success_rate, 3),
            "is_active": self.is_active,
            "is_wildcard": self.is_wildcard,
        }

    def __repr__(self) -> str:
        return (
            f"<Subscription id={str(self.id)[:8]} "
            f"pattern={self.event_pattern!r} "
            f"handler={self.handler_name!r} "
            f"priority={self.priority.value}>"
        )


# ============================================================================
# MODÈLES PYDANTIC — Configuration et statistiques
# ============================================================================


class EventBusConfig(BaseModel):
    """Configuration de l'EventBus.

    Attributes:
        queue_size: Taille maximale de la file d'événements.
        handler_timeout: Timeout par défaut pour les handlers (secondes).
        max_handlers_per_event: Nombre maximum de handlers par type.
        dead_letter_enabled: Activer la dead letter queue.
        dead_letter_size: Taille maximale de la dead letter queue.
        log_emissions: Logger les émissions d'événements.
        log_handler_errors: Logger les erreurs des handlers.
        propagate_exceptions: Propager les exceptions des handlers.
        enable_metrics: Activer les métriques détaillées.
        worker_count: Nombre de workers pour traiter les événements.
    """

    queue_size: int = Field(
        default=_DEFAULT_QUEUE_SIZE,
        ge=100,
        le=1000000,
        description="Taille maximale de la file d'événements.",
    )
    handler_timeout: float = Field(
        default=_DEFAULT_HANDLER_TIMEOUT,
        gt=0.0,
        le=600.0,
        description="Timeout par défaut pour les handlers.",
    )
    max_handlers_per_event: int = Field(
        default=_MAX_HANDLERS_PER_EVENT,
        ge=1,
        le=10000,
        description="Nombre maximum de handlers par type.",
    )
    dead_letter_enabled: bool = Field(
        default=True,
        description="Activer la dead letter queue.",
    )
    dead_letter_size: int = Field(
        default=_MAX_DEAD_LETTER_SIZE,
        ge=10,
        le=100000,
        description="Taille maximale de la dead letter queue.",
    )
    log_emissions: bool = Field(
        default=False,
        description="Logger les émissions d'événements.",
    )
    log_handler_errors: bool = Field(
        default=True,
        description="Logger les erreurs des handlers.",
    )
    propagate_exceptions: bool = Field(
        default=False,
        description="Propager les exceptions des handlers.",
    )
    enable_metrics: bool = Field(
        default=True,
        description="Activer les métriques détaillées.",
    )
    worker_count: int = Field(
        default=1,
        ge=1,
        le=32,
        description="Nombre de workers pour traiter les événements.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class EventBusStats(BaseModel):
    """Statistiques globales de l'EventBus.

    Attributes:
        total_emitted: Nombre total d'événements émis.
        total_processed: Nombre total d'événements traités.
        total_failed: Nombre total d'événements ayant échoué.
        total_dropped: Nombre total d'événements abandonnés.
        total_handler_calls: Nombre total d'appels de handlers.
        total_handler_errors: Nombre total d'erreurs de handlers.
        total_timeouts: Nombre total de timeouts.
        active_subscriptions: Nombre d'abonnements actifs.
        total_subscriptions: Nombre total d'abonnements (historique).
        queue_size: Taille actuelle de la file.
        dead_letter_size: Taille actuelle de la dead letter queue.
        events_by_type: Nombre d'événements par type.
        events_by_domain: Nombre d'événements par domaine.
        handlers_by_priority: Nombre de handlers par priorité.
        average_processing_time_ms: Temps moyen de traitement (ms).
        last_event_at: Timestamp du dernier événement.
        uptime_seconds: Durée de fonctionnement.
    """

    total_emitted: int = Field(default=0, ge=0)
    total_processed: int = Field(default=0, ge=0)
    total_failed: int = Field(default=0, ge=0)
    total_dropped: int = Field(default=0, ge=0)
    total_handler_calls: int = Field(default=0, ge=0)
    total_handler_errors: int = Field(default=0, ge=0)
    total_timeouts: int = Field(default=0, ge=0)
    active_subscriptions: int = Field(default=0, ge=0)
    total_subscriptions: int = Field(default=0, ge=0)
    queue_size: int = Field(default=0, ge=0)
    dead_letter_size: int = Field(default=0, ge=0)
    events_by_type: dict[str, int] = Field(default_factory=dict)
    events_by_domain: dict[str, int] = Field(default_factory=dict)
    handlers_by_priority: dict[str, int] = Field(default_factory=dict)
    average_processing_time_ms: float = Field(default=0.0, ge=0.0)
    last_event_at: datetime | None = None
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def processing_success_rate(self) -> float:
        """Taux de succès du traitement (0.0 à 1.0)."""
        if self.total_emitted == 0:
            return 1.0
        return self.total_processed / self.total_emitted

    @property
    def handler_success_rate(self) -> float:
        """Taux de succès des handlers (0.0 à 1.0)."""
        if self.total_handler_calls == 0:
            return 1.0
        return (
            (self.total_handler_calls - self.total_handler_errors)
            / self.total_handler_calls
        )

    @property
    def drop_rate(self) -> float:
        """Taux d'événements abandonnés (0.0 à 1.0)."""
        if self.total_emitted == 0:
            return 0.0
        return self.total_dropped / self.total_emitted


# ============================================================================
# CLASSE INTERNE — _HandlerEntry
# ============================================================================


# Type pour les handlers (sync ou async)
EventHandler = Union[
    Callable[[Event], Awaitable[None]],
    Callable[[Event], None],
]


class _HandlerEntry:
    """Entrée interne représentant un handler enregistré.

    Non exposé publiquement — utilisé par EventBus.
    """

    __slots__ = (
        "_subscription",
        "_handler",
        "_is_async",
    )

    def __init__(
        self,
        subscription: Subscription,
        handler: EventHandler,
    ) -> None:
        self._subscription = subscription
        self._handler = handler
        self._is_async = inspect.iscoroutinefunction(handler)

    @property
    def subscription(self) -> Subscription:
        return self._subscription

    @property
    def handler(self) -> EventHandler:
        return self._handler

    @property
    def is_async(self) -> bool:
        return self._is_async

    @property
    def priority(self) -> EventPriority:
        return self._subscription.priority

    @property
    def is_active(self) -> bool:
        return self._subscription.is_active

    async def invoke(self, event: Event) -> None:
        """Invoque le handler avec l'événement.

        Args:
            event: Événement à traiter.

        Raises:
            HandlerTimeoutError: Si le handler dépasse son timeout.
            HandlerError: Si le handler échoue.
        """
        try:
            if self._is_async:
                await asyncio.wait_for(
                    self._handler(event),
                    timeout=self._subscription.timeout,
                )
            else:
                # Handler synchrone : exécuter dans un thread
                await asyncio.wait_for(
                    asyncio.to_thread(self._handler, event),
                    timeout=self._subscription.timeout,
                )
        except asyncio.TimeoutError as e:
            raise HandlerTimeoutError(
                event.type,
                self._subscription.handler_name,
                self._subscription.timeout,
            ) from e
        except Exception as e:
            raise HandlerError(
                event.type,
                self._subscription.handler_name,
                str(e),
            ) from e


# ============================================================================
# CLASSE PRINCIPALE — EventBus
# ============================================================================


class EventBus:
    """Système d'EventBus centralisé pour la communication inter-modules.

    Fournit un système de publication/abonnement asynchrone robuste avec
    support des wildcards, priorités, handlers sync/async, et statistiques.

    Lifecycle :
        >>> event_bus = EventBus()
        >>> await event_bus.start()
        >>> subscription = event_bus.on("download.*", handler)
        >>> await event_bus.emit("download.task.completed", payload={...})
        >>> event_bus.off(subscription.id)
        >>> await event_bus.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Les opérations sont protégées par des locks.
    """

    # Instance globale par défaut
    _default_instance: ClassVar[EventBus | None] = None

    def __init__(
        self,
        *,
        config: EventBusConfig | None = None,
    ) -> None:
        """Initialise l'EventBus.

        Args:
            config: Configuration de l'EventBus.
        """
        self._config = config or EventBusConfig()

        # File d'événements
        self._queue: asyncio.Queue[Event] | None = None

        # Abonnements : pattern → liste de handlers triés par priorité
        self._subscriptions: dict[str, list[_HandlerEntry]] = defaultdict(list)
        self._subscriptions_by_id: dict[UUID, _HandlerEntry] = {}
        self._subscriptions_lock = asyncio.Lock()

        # Dead letter queue
        self._dead_letter: OrderedDict[UUID, tuple[Event, str]] = OrderedDict()
        self._dead_letter_lock = asyncio.Lock()

        # Waiters pour wait_for_event
        self._waiters: dict[str, list[asyncio.Future[Event]]] = defaultdict(list)
        self._waiters_lock = asyncio.Lock()

        # Tâches de traitement
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._processing_task: asyncio.Task[None] | None = None

        # État
        self._started: bool = False
        self._start_time: float = 0.0

        # Statistiques
        self._total_emitted: int = 0
        self._total_processed: int = 0
        self._total_failed: int = 0
        self._total_dropped: int = 0
        self._total_handler_calls: int = 0
        self._total_handler_errors: int = 0
        self._total_timeouts: int = 0
        self._total_subscriptions: int = 0
        self._total_processing_time_ms: float = 0.0
        self._events_by_type: dict[str, int] = defaultdict(int)
        self._events_by_domain: dict[str, int] = defaultdict(int)
        self._handlers_by_priority: dict[str, int] = defaultdict(int)
        self._last_event_at: datetime | None = None
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="event_bus")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre l'EventBus et les workers de traitement.

        Raises:
            EventBusAlreadyStartedError: Si l'EventBus est déjà démarré.
        """
        if self._started:
            raise EventBusAlreadyStartedError()

        # Créer la file d'événements
        self._queue = asyncio.Queue(maxsize=self._config.queue_size)

        # Démarrer les workers
        for i in range(self._config.worker_count):
            task = asyncio.create_task(
                self._worker_loop(i),
                name=f"event_bus_worker_{i}",
            )
            self._worker_tasks.append(task)

        self._started = True
        self._start_time = time.monotonic()

        self._logger.info(
            "EventBus démarré: queue_size={}, workers={}",
            self._config.queue_size,
            self._config.worker_count,
        )

    async def stop(self) -> None:
        """Arrête l'EventBus et libère les ressources.

        Attend que tous les événements en file soient traités avant d'arrêter.
        """
        if not self._started:
            return

        self._started = False

        # Attendre que la file soit vide
        if self._queue is not None:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=5.0)
            except asyncio.TimeoutError:
                self._logger.warning(
                    "Timeout en attendant le vidage de la file ({} événements restants)",
                    self._queue.qsize(),
                )

        # Annuler les workers
        for task in self._worker_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._worker_tasks.clear()

        # Annuler tous les waiters
        async with self._waiters_lock:
            for futures in self._waiters.values():
                for future in futures:
                    if not future.done():
                        future.cancel()
            self._waiters.clear()

        self._queue = None

        self._logger.info("EventBus arrêté")

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
        """Indique si l'EventBus est démarré."""
        return self._started

    @property
    def config(self) -> EventBusConfig:
        """Configuration de l'EventBus."""
        return self._config

    @property
    def queue_size(self) -> int:
        """Taille actuelle de la file d'événements."""
        return self._queue.qsize() if self._queue else 0

    @property
    def subscriptions_count(self) -> int:
        """Nombre d'abonnements actifs."""
        return len(self._subscriptions_by_id)

    @property
    def dead_letter_size(self) -> int:
        """Taille de la dead letter queue."""
        return len(self._dead_letter)

    # ------------------------------------------------------------------------
    # API publique — Publication
    # ------------------------------------------------------------------------

    async def emit(
        self,
        event_type: str | EventType,
        *,
        payload: dict[str, Any] | None = None,
        source: str = "",
        correlation_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        blocking: bool = False,
    ) -> Event:
        """Émet un événement dans le bus.

        Args:
            event_type: Type d'événement (chaîne ou EventType).
            payload: Données associées à l'événement.
            source: Module/component source.
            correlation_id: ID de corrélation pour traçabilité.
            metadata: Métadonnées additionnelles.
            blocking: Si True, attend que l'événement soit traité.

        Returns:
            Instance de Event créée.

        Raises:
            EventBusNotStartedError: Si l'EventBus n'est pas démarré.
            EventQueueFullError: Si la file est pleine et blocking=False.
            InvalidEventTypeError: Si le type est invalide.
        """
        self._ensure_started()
        assert self._queue is not None

        # Convertir EventType en string
        if isinstance(event_type, EventType):
            event_type_str = event_type.value
        else:
            event_type_str = event_type

        # Créer l'événement
        event = Event(
            type=event_type_str,
            payload=payload or {},
            source=source,
            correlation_id=correlation_id,
            metadata=metadata or {},
        )

        # Logger si configuré
        if self._config.log_emissions:
            self._logger.debug(
                "Émission: {} (source={}, payload_keys={})",
                event.type,
                event.source,
                list(event.payload.keys()),
            )

        # Mettre en file
        if blocking:
            await self._queue.put(event)
        else:
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                async with self._stats_lock:
                    self._total_dropped += 1

                if self._config.dead_letter_enabled:
                    await self._add_to_dead_letter(
                        event,
                        "File d'événements pleine",
                    )

                raise EventQueueFullError(self._config.queue_size)

        # Mettre à jour les stats
        async with self._stats_lock:
            self._total_emitted += 1
            self._events_by_type[event.type] += 1
            domain = event.domain
            self._events_by_domain[domain] += 1
            self._last_event_at = datetime.now(UTC)

        # Notifier les waiters
        await self._notify_waiters(event)

        return event

    async def emit_event(self, event: Event, *, blocking: bool = False) -> None:
        """Émet un événement pré-construit.

        Args:
            event: Instance de Event à émettre.
            blocking: Si True, attend que l'événement soit traité.
        """
        self._ensure_started()
        assert self._queue is not None

        if blocking:
            await self._queue.put(event)
        else:
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                async with self._stats_lock:
                    self._total_dropped += 1

                if self._config.dead_letter_enabled:
                    await self._add_to_dead_letter(
                        event,
                        "File d'événements pleine",
                    )

                raise EventQueueFullError(self._config.queue_size)

        async with self._stats_lock:
            self._total_emitted += 1
            self._events_by_type[event.type] += 1
            self._events_by_domain[event.domain] += 1
            self._last_event_at = datetime.now(UTC)

        await self._notify_waiters(event)

    # ------------------------------------------------------------------------
    # API publique — Abonnement
    # ------------------------------------------------------------------------

    def on(
        self,
        event_pattern: str | EventType,
        handler: EventHandler | None = None,
        *,
        priority: EventPriority = EventPriority.NORMAL,
        timeout: float | None = None,
        name: str | None = None,
    ) -> Subscription | Callable[[EventHandler], Subscription]:
        """S'abonne à un type d'événement.

        Peut être utilisé comme décorateur ou comme méthode directe.

        Args:
            event_pattern: Pattern d'événement (ex: "download.*").
            handler: Fonction handler (optionnel si utilisé comme décorateur).
            priority: Priorité d'exécution.
            timeout: Timeout d'exécution (secondes).
            name: Nom du handler (pour debugging).

        Returns:
            Subscription si handler fourni, sinon décorateur.

        Example:
            >>> # Comme méthode
            >>> sub = event_bus.on("download.*", my_handler)
            >>>
            >>> # Comme décorateur
            >>> @event_bus.on("download.task.completed")
            ... async def on_task_completed(event: Event):
            ...     print(event.payload)
        """
        # Convertir EventType en string
        if isinstance(event_pattern, EventType):
            pattern_str = event_pattern.value
        else:
            pattern_str = event_pattern

        handler_timeout = timeout if timeout is not None else self._config.handler_timeout

        def _register_handler(h: EventHandler) -> Subscription:
            handler_name = name or getattr(h, "__name__", str(h))

            subscription = Subscription(
                event_pattern=pattern_str,
                handler_name=handler_name,
                priority=priority,
                timeout=handler_timeout,
            )

            entry = _HandlerEntry(subscription, h)

            # Planifier l'ajout (thread-safe)
            asyncio.create_task(
                self._add_subscription(entry),
                name=f"add_subscription_{subscription.id}",
            )

            return subscription

        if handler is not None:
            return _register_handler(handler)

        return _register_handler

    def once(
        self,
        event_pattern: str | EventType,
        handler: EventHandler | None = None,
        *,
        priority: EventPriority = EventPriority.NORMAL,
        timeout: float | None = None,
        name: str | None = None,
    ) -> Subscription | Callable[[EventHandler], Subscription]:
        """S'abonne à un événement pour une seule exécution.

        L'abonnement est automatiquement supprimé après la première exécution.

        Args:
            event_pattern: Pattern d'événement.
            handler: Fonction handler.
            priority: Priorité d'exécution.
            timeout: Timeout d'exécution.
            name: Nom du handler.

        Returns:
            Subscription si handler fourni, sinon décorateur.
        """
        # Convertir EventType en string
        if isinstance(event_pattern, EventType):
            pattern_str = event_pattern.value
        else:
            pattern_str = event_pattern

        handler_timeout = timeout if timeout is not None else self._config.handler_timeout

        def _register_handler(h: EventHandler) -> Subscription:
            handler_name = name or getattr(h, "__name__", str(h))

            subscription = Subscription(
                event_pattern=pattern_str,
                handler_name=handler_name,
                priority=priority,
                timeout=handler_timeout,
                one_shot=True,
            )

            entry = _HandlerEntry(subscription, h)

            asyncio.create_task(
                self._add_subscription(entry),
                name=f"add_subscription_{subscription.id}",
            )

            return subscription

        if handler is not None:
            return _register_handler(handler)

        return _register_handler

    async def off(
        self,
        subscription: Subscription | UUID | str,
    ) -> bool:
        """Désabonne un handler.

        Args:
            subscription: Subscription, UUID, ou string ID.

        Returns:
            True si l'abonnement a été supprimé.
        """
        # Extraire l'ID
        if isinstance(subscription, Subscription):
            sub_id = subscription.id
        elif isinstance(subscription, UUID):
            sub_id = subscription
        else:
            try:
                sub_id = UUID(subscription)
            except ValueError:
                return False

        async with self._subscriptions_lock:
            entry = self._subscriptions_by_id.get(sub_id)
            if entry is None:
                return False

            # Retirer de la liste par pattern
            pattern = entry.subscription.event_pattern
            if pattern in self._subscriptions:
                self._subscriptions[pattern] = [
                    e for e in self._subscriptions[pattern]
                    if e.subscription.id != sub_id
                ]
                if not self._subscriptions[pattern]:
                    del self._subscriptions[pattern]

            # Retirer du mapping par ID
            del self._subscriptions_by_id[sub_id]

            # Marquer comme inactif
            entry.subscription.deactivate()

            # Mettre à jour les stats
            async with self._stats_lock:
                priority_key = entry.priority.name
                self._handlers_by_priority[priority_key] = max(
                    0, self._handlers_by_priority.get(priority_key, 1) - 1
                )

        self._logger.debug(
            "Abonnement supprimé: {} (pattern={})",
            sub_id,
            entry.subscription.event_pattern,
        )

        return True

    async def off_all(self, event_pattern: str | None = None) -> int:
        """Désabonne tous les handlers pour un pattern (ou tous).

        Args:
            event_pattern: Pattern spécifique (None = tous).

        Returns:
            Nombre d'abonnements supprimés.
        """
        async with self._subscriptions_lock:
            if event_pattern is None:
                count = len(self._subscriptions_by_id)
                self._subscriptions.clear()
                self._subscriptions_by_id.clear()
                async with self._stats_lock:
                    self._handlers_by_priority.clear()
                return count

            # Supprimer les handlers pour ce pattern
            entries = self._subscriptions.pop(event_pattern, [])
            count = 0
            for entry in entries:
                sub_id = entry.subscription.id
                if sub_id in self._subscriptions_by_id:
                    del self._subscriptions_by_id[sub_id]
                    entry.subscription.deactivate()
                    count += 1

                    async with self._stats_lock:
                        priority_key = entry.priority.name
                        self._handlers_by_priority[priority_key] = max(
                            0, self._handlers_by_priority.get(priority_key, 1) - 1
                        )

            return count

    # ------------------------------------------------------------------------
    # API publique — Attente d'événements
    # ------------------------------------------------------------------------

    async def wait_for_event(
        self,
        event_pattern: str | EventType,
        *,
        timeout: float = _DEFAULT_WAIT_TIMEOUT,
        predicate: Callable[[Event], bool] | None = None,
    ) -> Event:
        """Attend qu'un événement correspondant soit émis.

        Args:
            event_pattern: Pattern d'événement à attendre.
            timeout: Timeout en secondes.
            predicate: Fonction de filtrage additionnelle (optionnel).

        Returns:
            Événement correspondant.

        Raises:
            asyncio.TimeoutError: Si le timeout est dépassé.
            EventBusNotStartedError: Si l'EventBus n'est pas démarré.
        """
        self._ensure_started()

        # Convertir EventType en string
        if isinstance(event_pattern, EventType):
            pattern_str = event_pattern.value
        else:
            pattern_str = event_pattern

        # Créer une future
        loop = asyncio.get_event_loop()
        future: asyncio.Future[Event] = loop.create_future()

        # Enregistrer le waiter
        async with self._waiters_lock:
            self._waiters[pattern_str].append(future)

        try:
            # Attendre avec timeout
            event = await asyncio.wait_for(future, timeout=timeout)

            # Vérifier le prédicat si fourni
            if predicate is not None and not predicate(event):
                # Réenregistrer et réattendre
                async with self._waiters_lock:
                    self._waiters[pattern_str].append(future)
                return await self.wait_for_event(
                    event_pattern,
                    timeout=timeout,
                    predicate=predicate,
                )

            return event

        except asyncio.TimeoutError:
            # Retirer le waiter
            async with self._waiters_lock:
                if pattern_str in self._waiters:
                    try:
                        self._waiters[pattern_str].remove(future)
                    except ValueError:
                        pass
                    if not self._waiters[pattern_str]:
                        del self._waiters[pattern_str]
            raise

    # ------------------------------------------------------------------------
    # API publique — Dead letter queue
    # ------------------------------------------------------------------------

    async def get_dead_letter(self) -> list[tuple[Event, str]]:
        """Retourne le contenu de la dead letter queue.

        Returns:
            Liste de tuples (event, reason).
        """
        async with self._dead_letter_lock:
            return list(self._dead_letter.items())

    async def clear_dead_letter(self) -> int:
        """Vide la dead letter queue.

        Returns:
            Nombre d'événements supprimés.
        """
        async with self._dead_letter_lock:
            count = len(self._dead_letter)
            self._dead_letter.clear()
            return count

    async def retry_dead_letter(self) -> int:
        """Réémet les événements de la dead letter queue.

        Returns:
            Nombre d'événements réémis.
        """
        async with self._dead_letter_lock:
            events = list(self._dead_letter.values())
            self._dead_letter.clear()

        count = 0
        for event, _reason in events:
            try:
                await self.emit_event(event)
                count += 1
            except Exception as e:
                self._logger.warning(
                    "Échec de réémission de l'événement {}: {}",
                    event.id,
                    e,
                )

        return count

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> EventBusStats:
        """Retourne les statistiques globales de l'EventBus."""
        async with self._stats_lock:
            uptime = 0.0
            if self._start_time > 0:
                uptime = time.monotonic() - self._start_time

            avg_processing_time = 0.0
            if self._total_processed > 0:
                avg_processing_time = (
                    self._total_processing_time_ms / self._total_processed
                )

            async with self._dead_letter_lock:
                dead_letter_size = len(self._dead_letter)

            return EventBusStats(
                total_emitted=self._total_emitted,
                total_processed=self._total_processed,
                total_failed=self._total_failed,
                total_dropped=self._total_dropped,
                total_handler_calls=self._total_handler_calls,
                total_handler_errors=self._total_handler_errors,
                total_timeouts=self._total_timeouts,
                active_subscriptions=len(self._subscriptions_by_id),
                total_subscriptions=self._total_subscriptions,
                queue_size=self.queue_size,
                dead_letter_size=dead_letter_size,
                events_by_type=dict(self._events_by_type),
                events_by_domain=dict(self._events_by_domain),
                handlers_by_priority=dict(self._handlers_by_priority),
                average_processing_time_ms=avg_processing_time,
                last_event_at=self._last_event_at,
                uptime_seconds=uptime,
            )

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        async with self._stats_lock:
            self._total_emitted = 0
            self._total_processed = 0
            self._total_failed = 0
            self._total_dropped = 0
            self._total_handler_calls = 0
            self._total_handler_errors = 0
            self._total_timeouts = 0
            self._total_processing_time_ms = 0.0
            self._events_by_type.clear()
            self._events_by_domain.clear()
            self._handlers_by_priority.clear()
            self._last_event_at = None

    async def list_subscriptions(self) -> list[Subscription]:
        """Liste tous les abonnements actifs.

        Returns:
            Liste des Subscriptions.
        """
        async with self._subscriptions_lock:
            return [
                entry.subscription
                for entry in self._subscriptions_by_id.values()
            ]

    # ------------------------------------------------------------------------
    # API publique — Utilitaires
    # ------------------------------------------------------------------------

    async def drain(self, *, timeout: float = 5.0) -> int:
        """Attend que la file soit vide.

        Args:
            timeout: Timeout maximum (secondes).

        Returns:
            Nombre d'événements traités pendant le drain.

        Raises:
            asyncio.TimeoutError: Si le timeout est dépassé.
        """
        if self._queue is None:
            return 0

        initial_size = self._queue.qsize()
        start_count = self._total_processed

        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            remaining = self._queue.qsize()
            self._logger.warning(
                "Drain timeout: {} événements restants",
                remaining,
            )
            raise

        return self._total_processed - start_count

    async def clear(self) -> None:
        """Vide la file d'événements et tous les abonnements."""
        if self._queue is not None:
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except asyncio.QueueEmpty:
                    break

        async with self._subscriptions_lock:
            self._subscriptions.clear()
            self._subscriptions_by_id.clear()

        async with self._dead_letter_lock:
            self._dead_letter.clear()

        async with self._waiters_lock:
            for futures in self._waiters.values():
                for future in futures:
                    if not future.done():
                        future.cancel()
            self._waiters.clear()

        self._logger.info("EventBus vidé")

    # ------------------------------------------------------------------------
    # Méthodes internes — Traitement des événements
    # ------------------------------------------------------------------------

    async def _worker_loop(self, worker_id: int) -> None:
        """Boucle de traitement des événements pour un worker.

        Args:
            worker_id: ID du worker.
        """
        assert self._queue is not None

        self._logger.trace("Worker {} démarré", worker_id)

        try:
            while self._started:
                try:
                    # Récupérer un événement avec timeout
                    event = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break

                try:
                    await self._process_event(event)
                except Exception as e:
                    self._logger.error(
                        "Erreur non gérée dans le worker {}: {}",
                        worker_id,
                        e,
                        exc_info=True,
                    )
                finally:
                    self._queue.task_done()

        except asyncio.CancelledError:
            pass
        finally:
            self._logger.trace("Worker {} arrêté", worker_id)

    async def _process_event(self, event: Event) -> None:
        """Traite un événement en invoquant tous les handlers correspondants.

        Args:
            event: Événement à traiter.
        """
        start_time = time.perf_counter()

        # Trouver les handlers correspondants
        handlers = await self._find_matching_handlers(event)

        if not handlers:
            # Aucun handler : événement non traité
            async with self._stats_lock:
                self._total_processed += 1
                self._total_processing_time_ms += (
                    (time.perf_counter() - start_time) * 1000
                )
            return

        # Trier par priorité (décroissante)
        handlers.sort(key=lambda h: h.priority.value, reverse=True)

        # Invoquer les handlers
        failed = False
        for entry in handlers:
            try:
                await entry.invoke(event)
                entry.subscription.record_call(success=True)

                async with self._stats_lock:
                    self._total_handler_calls += 1

                # Auto-unsubscribe si one-shot
                if entry.subscription.one_shot:
                    await self.off(entry.subscription.id)

            except HandlerTimeoutError as e:
                entry.subscription.record_call(success=False)
                async with self._stats_lock:
                    self._total_handler_errors += 1
                    self._total_timeouts += 1

                if self._config.log_handler_errors:
                    self._logger.warning("{}", e)

                if self._config.propagate_exceptions:
                    raise

                failed = True

            except HandlerError as e:
                entry.subscription.record_call(success=False)
                async with self._stats_lock:
                    self._total_handler_errors += 1

                if self._config.log_handler_errors:
                    self._logger.warning("{}", e)

                if self._config.propagate_exceptions:
                    raise

                failed = True

            except Exception as e:
                entry.subscription.record_call(success=False)
                async with self._stats_lock:
                    self._total_handler_errors += 1

                if self._config.log_handler_errors:
                    self._logger.error(
                        "Erreur inattendue dans le handler {}: {}",
                        entry.subscription.handler_name,
                        e,
                        exc_info=True,
                    )

                if self._config.propagate_exceptions:
                    raise

                failed = True

        # Mettre à jour les stats
        async with self._stats_lock:
            self._total_processed += 1
            if failed:
                self._total_failed += 1
            self._total_processing_time_ms += (
                (time.perf_counter() - start_time) * 1000
            )

    async def _find_matching_handlers(self, event: Event) -> list[_HandlerEntry]:
        """Trouve tous les handlers correspondant à un événement.

        Supporte les patterns avec wildcards (ex: "download.*").

        Args:
            event: Événement à matcher.

        Returns:
            Liste des handlers correspondants.
        """
        matching: list[_HandlerEntry] = []

        async with self._subscriptions_lock:
            for pattern, entries in self._subscriptions.items():
                # Vérifier si le pattern correspond
                if fnmatch.fnmatch(event.type, pattern):
                    # Ajouter les handlers actifs
                    for entry in entries:
                        if entry.is_active:
                            matching.append(entry)

        return matching

    # ------------------------------------------------------------------------
    # Méthodes internes — Gestion des abonnements
    # ------------------------------------------------------------------------

    async def _add_subscription(self, entry: _HandlerEntry) -> None:
        """Ajoute un abonnement.

        Args:
            entry: Handler entry à ajouter.
        """
        async with self._subscriptions_lock:
            pattern = entry.subscription.event_pattern

            # Vérifier la limite
            if len(self._subscriptions[pattern]) >= self._config.max_handlers_per_event:
                self._logger.warning(
                    "Limite de handlers atteinte pour le pattern '{}' ({} handlers)",
                    pattern,
                    self._config.max_handlers_per_event,
                )
                return

            # Ajouter
            self._subscriptions[pattern].append(entry)
            self._subscriptions_by_id[entry.subscription.id] = entry

            async with self._stats_lock:
                self._total_subscriptions += 1
                priority_key = entry.priority.name
                self._handlers_by_priority[priority_key] = (
                    self._handlers_by_priority.get(priority_key, 0) + 1
                )

        self._logger.debug(
            "Abonnement ajouté: {} (pattern={}, handler={}, priority={})",
            entry.subscription.id,
            pattern,
            entry.subscription.handler_name,
            entry.priority.value,
        )

    # ------------------------------------------------------------------------
    # Méthodes internes — Waiters
    # ------------------------------------------------------------------------

    async def _notify_waiters(self, event: Event) -> None:
        """Notifie les waiters correspondant à un événement.

        Args:
            event: Événement émis.
        """
        async with self._waiters_lock:
            # Chercher les waiters pour ce type exact
            if event.type in self._waiters:
                for future in self._waiters[event.type]:
                    if not future.done():
                        future.set_result(event)
                del self._waiters[event.type]

            # Chercher les waiters avec wildcards
            for pattern, futures in list(self._waiters.items()):
                if "*" in pattern or "?" in pattern:
                    if fnmatch.fnmatch(event.type, pattern):
                        for future in futures:
                            if not future.done():
                                future.set_result(event)
                        del self._waiters[pattern]

    # ------------------------------------------------------------------------
    # Méthodes internes — Dead letter queue
    # ------------------------------------------------------------------------

    async def _add_to_dead_letter(self, event: Event, reason: str) -> None:
        """Ajoute un événement à la dead letter queue.

        Args:
            event: Événement à ajouter.
            reason: Raison de l'abandon.
        """
        async with self._dead_letter_lock:
            self._dead_letter[event.id] = (event, reason)

            # Limiter la taille
            while len(self._dead_letter) > self._config.dead_letter_size:
                self._dead_letter.popitem(last=False)

        self._logger.debug(
            "Événement ajouté à la dead letter queue: {} ({})",
            event.id,
            reason,
        )

    # ------------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # ------------------------------------------------------------------------

    def _ensure_started(self) -> None:
        """Vérifie que l'EventBus est démarré."""
        if not self._started:
            raise EventBusNotStartedError()

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<EventBus status={status} "
            f"subscriptions={len(self._subscriptions_by_id)} "
            f"queue={self.queue_size}>"
        )


# ============================================================================
# INSTANCE GLOBALE
# ============================================================================


# Instance globale par défaut
_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Retourne l'instance globale de l'EventBus.

    Crée l'instance si elle n'existe pas encore.

    Returns:
        Instance globale de EventBus.
    """
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def set_event_bus(event_bus: EventBus) -> None:
    """Remplace l'instance globale de l'EventBus.

    Utile pour les tests ou pour utiliser une configuration custom.

    Args:
        event_bus: Nouvelle instance de EventBus.
    """
    global _event_bus
    _event_bus = event_bus


def reset_event_bus() -> None:
    """Réinitialise l'instance globale de l'EventBus."""
    global _event_bus
    _event_bus = None


# Alias pratique : l'instance globale accessible directement
# Usage : from nexusdl.core.events import event_bus
class _EventBusProxy:
    """Proxy pour l'instance globale de l'EventBus.

    Permet d'accéder à l'EventBus global comme si c'était une instance directe.
    """

    def __getattr__(self, name: str) -> Any:
        return getattr(get_event_bus(), name)

    def __repr__(self) -> str:
        return repr(get_event_bus())


event_bus: EventBus = _EventBusProxy()  # type: ignore[assignment]


# ============================================================================
# HELPERS PUBLICS — Fonctions utilitaires
# ============================================================================


async def emit_event(
    event_type: str | EventType,
    *,
    payload: dict[str, Any] | None = None,
    source: str = "",
    **kwargs: Any,
) -> Event:
    """Émet un événement via l'EventBus global (raccourci).

    Args:
        event_type: Type d'événement.
        payload: Données associées.
        source: Module/component source.
        **kwargs: Arguments additionnels pour emit().

    Returns:
        Instance de Event créée.
    """
    return await get_event_bus().emit(
        event_type,
        payload=payload,
        source=source,
        **kwargs,
    )


def subscribe(
    event_pattern: str | EventType,
    handler: EventHandler | None = None,
    **kwargs: Any,
) -> Subscription | Callable[[EventHandler], Subscription]:
    """S'abonne à un événement via l'EventBus global (raccourci).

    Args:
        event_pattern: Pattern d'événement.
        handler: Fonction handler.
        **kwargs: Arguments additionnels pour on().

    Returns:
        Subscription si handler fourni, sinon décorateur.
    """
    return get_event_bus().on(event_pattern, handler, **kwargs)


def subscribe_once(
    event_pattern: str | EventType,
    handler: EventHandler | None = None,
    **kwargs: Any,
) -> Subscription | Callable[[EventHandler], Subscription]:
    """S'abonne à un événement pour une seule exécution (raccourci).

    Args:
        event_pattern: Pattern d'événement.
        handler: Fonction handler.
        **kwargs: Arguments additionnels pour once().

    Returns:
        Subscription si handler fourni, sinon décorateur.
    """
    return get_event_bus().once(event_pattern, handler, **kwargs)


async def unsubscribe(subscription: Subscription | UUID | str) -> bool:
    """Désabonne un handler via l'EventBus global (raccourci).

    Args:
        subscription: Subscription, UUID, ou string ID.

    Returns:
        True si l'abonnement a été supprimé.
    """
    return await get_event_bus().off(subscription)


async def wait_for(
    event_pattern: str | EventType,
    *,
    timeout: float = _DEFAULT_WAIT_TIMEOUT,
    predicate: Callable[[Event], bool] | None = None,
) -> Event:
    """Attend un événement via l'EventBus global (raccourci).

    Args:
        event_pattern: Pattern d'événement.
        timeout: Timeout en secondes.
        predicate: Fonction de filtrage.

    Returns:
        Événement correspondant.
    """
    return await get_event_bus().wait_for_event(
        event_pattern,
        timeout=timeout,
        predicate=predicate,
    )


# ============================================================================
# DÉCORATEURS — Helpers pour handlers
# ============================================================================


def event_handler(
    event_pattern: str | EventType,
    *,
    priority: EventPriority = EventPriority.NORMAL,
    timeout: float | None = None,
    name: str | None = None,
) -> Callable[[EventHandler], EventHandler]:
    """Décorateur pour marquer une fonction comme handler d'événement.

    N'enregistre pas automatiquement le handler — utilise event_bus.on() pour ça.
    Ce décorateur ajoute des métadonnées à la fonction pour documentation.

    Args:
        event_pattern: Pattern d'événement.
        priority: Priorité d'exécution.
        timeout: Timeout d'exécution.
        name: Nom du handler.

    Returns:
        Décorateur.

    Example:
        >>> @event_handler("download.task.completed", priority=EventPriority.HIGH)
        ... async def on_task_completed(event: Event):
        ...     print(event.payload)
        >>>
        >>> # Enregistrer le handler
        >>> event_bus.on("download.task.completed", on_task_completed)
    """
    import functools

    # Convertir EventType en string
    if isinstance(event_pattern, EventType):
        pattern_str = event_pattern.value
    else:
        pattern_str = event_pattern

    def _decorator(func: EventHandler) -> EventHandler:
        @functools.wraps(func)
        def _wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        # Ajouter des métadonnées
        _wrapper._event_pattern = pattern_str  # type: ignore[attr-defined]
        _wrapper._event_priority = priority  # type: ignore[attr-defined]
        _wrapper._event_timeout = timeout  # type: ignore[attr-defined]
        _wrapper._event_handler_name = name or func.__name__  # type: ignore[attr-defined]

        return _wrapper  # type: ignore[return-value]

    return _decorator


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "_DEFAULT_QUEUE_SIZE",
    "_DEFAULT_HANDLER_TIMEOUT",
    "_DEFAULT_WAIT_TIMEOUT",
    # Exceptions
    "EventError",
    "EventBusNotStartedError",
    "EventBusAlreadyStartedError",
    "InvalidEventTypeError",
    "HandlerError",
    "HandlerTimeoutError",
    "SubscriptionNotFoundError",
    "EventQueueFullError",
    # Enums
    "EventType",
    "EventPriority",
    "EventState",
    # Modèles — Événements
    "Event",
    "Subscription",
    # Modèles — Configuration et stats
    "EventBusConfig",
    "EventBusStats",
    # Classe principale
    "EventBus",
    # Instance globale
    "event_bus",
    "get_event_bus",
    "set_event_bus",
    "reset_event_bus",
    # Helpers — Raccourcis
    "emit_event",
    "subscribe",
    "subscribe_once",
    "unsubscribe",
    "wait_for",
    # Décorateurs
    "event_handler",
]
