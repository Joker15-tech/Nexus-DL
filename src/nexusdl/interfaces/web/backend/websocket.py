"""Gestionnaire WebSocket de haut niveau pour l'API REST NexusDL.

Ce module fournit une API de haut niveau pour la gestion des communications
WebSocket temps réel. Il sert de pont entre l'EventBus du core et les clients
WebSocket connectés, permettant de diffuser automatiquement les événements
système vers les interfaces web.

**Responsabilités** :
    - Pont EventBus → WebSocket (propagation automatique des événements)
    - Gestion des canaux de diffusion thématiques
    - API de haut niveau pour diffuser des messages
    - Helpers métier pour les cas d'usage courants
    - Statistiques et monitoring des connexions
    - Intégration avec le routeur ws.py (bas-niveau)

**Architecture** :
    websocket.py (haut niveau)
        │
        ├── WebSocketManager (orchestrateur principal)
        │   ├── EventBusBridge (pont EventBus → WebSocket)
        │   ├── ChannelRegistry (registre des canaux)
        │   └── ConnectionTracker (suivi des connexions)
        │
        ├── Helpers métier
        │   ├── broadcast_download_progress()
        │   ├── broadcast_library_update()
        │   ├── send_notification()
        │   ├── broadcast_log_entry()
        │   └── broadcast_system_event()
        │
        └── Intégration
            ├── routers/ws.py (routeur FastAPI bas-niveau)
            ├── core/events.py (EventBus)
            └── core/downloader/ (événements de téléchargement)

**Canaux de diffusion** :
    - downloads.progress    : Progression des téléchargements
    - downloads.completed   : Téléchargements terminés
    - downloads.failed      : Téléchargements échoués
    - downloads.started     : Téléchargements démarrés
    - downloads.paused      : Téléchargements mis en pause
    - library.added         : Mangas ajoutés à la bibliothèque
    - library.removed       : Mangas retirés de la bibliothèque
    - library.updated       : Mangas mis à jour
    - library.scan          : Progression du scan
    - notifications.info    : Notifications info
    - notifications.warning : Notifications warning
    - notifications.error   : Notifications error
    - notifications.success : Notifications success
    - logs.stream           : Streaming des logs
    - search.progress       : Progression de la recherche
    - search.completed      : Recherche terminée
    - system.status         : Statut système
    - system.events         : Événements système
    - system.metrics        : Métriques système
    - auth.login            : Connexions utilisateur
    - auth.logout           : Déconnexions utilisateur

**Exemple d'utilisation — Depuis un service** :
    >>> from nexusdl.interfaces.web.backend.websocket import (
    ...     broadcast_download_progress,
    ...     send_notification,
    ... )
    >>>
    >>> # Diffuser la progression d'un téléchargement
    >>> await broadcast_download_progress(
    ...     task_id="task_123",
    ...     progress=0.5,
    ...     speed_bytes_per_sec=1024000,
    ... )
    >>>
    >>> # Envoyer une notification à tous les clients
    >>> await send_notification(
    ...     message="Download completed!",
    ...     level="success",
    ... )

**Exemple d'utilisation — Écouter les événements EventBus** :
    >>> from nexusdl.interfaces.web.backend.websocket import (
    ...     get_websocket_manager,
    ... )
    >>>
    >>> manager = get_websocket_manager()
    >>> await manager.start()  # Démarre le pont EventBus → WebSocket

Intégration :
    - interfaces/web/backend/routers/ws.py : routeur bas-niveau
    - core/events.py                       : EventBus
    - core/downloader/manager.py           : événements de téléchargement
    - core/library/scanner.py              : événements de scan
    - core/logger.py                       : logs
    - core/i18n.py                         : traductions
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, Final

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.constants import APP_NAME, APP_VERSION
from nexusdl.core.events import EventBus, EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t


# ============================================================================
# CONSTANTES
# ============================================================================


# Canaux de diffusion
CHANNEL_DOWNLOADS_PROGRESS: Final[str] = "downloads.progress"
CHANNEL_DOWNLOADS_COMPLETED: Final[str] = "downloads.completed"
CHANNEL_DOWNLOADS_FAILED: Final[str] = "downloads.failed"
CHANNEL_DOWNLOADS_STARTED: Final[str] = "downloads.started"
CHANNEL_DOWNLOADS_PAUSED: Final[str] = "downloads.paused"
CHANNEL_LIBRARY_ADDED: Final[str] = "library.added"
CHANNEL_LIBRARY_REMOVED: Final[str] = "library.removed"
CHANNEL_LIBRARY_UPDATED: Final[str] = "library.updated"
CHANNEL_LIBRARY_SCAN: Final[str] = "library.scan"
CHANNEL_NOTIFICATIONS_INFO: Final[str] = "notifications.info"
CHANNEL_NOTIFICATIONS_WARNING: Final[str] = "notifications.warning"
CHANNEL_NOTIFICATIONS_ERROR: Final[str] = "notifications.error"
CHANNEL_NOTIFICATIONS_SUCCESS: Final[str] = "notifications.success"
CHANNEL_LOGS_STREAM: Final[str] = "logs.stream"
CHANNEL_SEARCH_PROGRESS: Final[str] = "search.progress"
CHANNEL_SEARCH_COMPLETED: Final[str] = "search.completed"
CHANNEL_SYSTEM_STATUS: Final[str] = "system.status"
CHANNEL_SYSTEM_EVENTS: Final[str] = "system.events"
CHANNEL_SYSTEM_METRICS: Final[str] = "system.metrics"
CHANNEL_AUTH_LOGIN: Final[str] = "auth.login"
CHANNEL_AUTH_LOGOUT: Final[str] = "auth.logout"

ALL_CHANNELS: Final[frozenset[str]] = frozenset({
    CHANNEL_DOWNLOADS_PROGRESS,
    CHANNEL_DOWNLOADS_COMPLETED,
    CHANNEL_DOWNLOADS_FAILED,
    CHANNEL_DOWNLOADS_STARTED,
    CHANNEL_DOWNLOADS_PAUSED,
    CHANNEL_LIBRARY_ADDED,
    CHANNEL_LIBRARY_REMOVED,
    CHANNEL_LIBRARY_UPDATED,
    CHANNEL_LIBRARY_SCAN,
    CHANNEL_NOTIFICATIONS_INFO,
    CHANNEL_NOTIFICATIONS_WARNING,
    CHANNEL_NOTIFICATIONS_ERROR,
    CHANNEL_NOTIFICATIONS_SUCCESS,
    CHANNEL_LOGS_STREAM,
    CHANNEL_SEARCH_PROGRESS,
    CHANNEL_SEARCH_COMPLETED,
    CHANNEL_SYSTEM_STATUS,
    CHANNEL_SYSTEM_EVENTS,
    CHANNEL_SYSTEM_METRICS,
    CHANNEL_AUTH_LOGIN,
    CHANNEL_AUTH_LOGOUT,
})

# Mapping EventBus → WebSocket channels
EVENT_TYPE_TO_CHANNEL: Final[dict[EventType, str]] = {
    EventType.DOWNLOAD_TASK_PROGRESS: CHANNEL_DOWNLOADS_PROGRESS,
    EventType.DOWNLOAD_TASK_COMPLETED: CHANNEL_DOWNLOADS_COMPLETED,
    EventType.DOWNLOAD_TASK_FAILED: CHANNEL_DOWNLOADS_FAILED,
    EventType.DOWNLOAD_TASK_STARTED: CHANNEL_DOWNLOADS_STARTED,
    EventType.DOWNLOAD_TASK_PAUSED: CHANNEL_DOWNLOADS_PAUSED,
    EventType.LIBRARY_MANGA_ADDED: CHANNEL_LIBRARY_ADDED,
    EventType.LIBRARY_MANGA_REMOVED: CHANNEL_LIBRARY_REMOVED,
    EventType.LIBRARY_MANGA_UPDATED: CHANNEL_LIBRARY_UPDATED,
    EventType.LIBRARY_SCAN_PROGRESS: CHANNEL_LIBRARY_SCAN,
    EventType.SEARCH_STARTED: CHANNEL_SEARCH_PROGRESS,
    EventType.SEARCH_COMPLETED: CHANNEL_SEARCH_COMPLETED,
    EventType.SESSION_STARTED: CHANNEL_SYSTEM_STATUS,
    EventType.SESSION_STOPPED: CHANNEL_SYSTEM_STATUS,
    EventType.CONFIG_CHANGED: CHANNEL_SYSTEM_EVENTS,
}

# Priorités des messages
PRIORITY_LOW: Final[int] = 0
PRIORITY_NORMAL: Final[int] = 1
PRIORITY_HIGH: Final[int] = 2
PRIORITY_URGENT: Final[int] = 3

# Limites
MAX_MESSAGE_SIZE_BYTES: Final[int] = 65536  # 64 KB
MAX_BROADCAST_QUEUE_SIZE: Final[int] = 1000
DEFAULT_BROADCAST_TIMEOUT_SECONDS: Final[float] = 5.0


# ============================================================================
# EXCEPTIONS
# ============================================================================


class WebSocketManagerError(NexusDLError):
    """Exception de base pour les erreurs du WebSocketManager."""


class ChannelNotFoundError(WebSocketManagerError):
    """Exception levée lorsqu'un canal n'existe pas.

    Attributes:
        channel: Nom du canal.
    """

    def __init__(self, channel: str) -> None:
        super().__init__(
            t(
                "websocket.error.channel_not_found",
                default="Channel not found: {channel}",
                channel=channel,
            )
        )
        self.channel = channel


class BroadcastError(WebSocketManagerError):
    """Exception levée lorsqu'une diffusion échoue.

    Attributes:
        channel: Canal cible.
        reason: Raison de l'échec.
    """

    def __init__(self, channel: str, reason: str = "") -> None:
        msg = t("websocket.error.broadcast_failed", default="Broadcast failed")
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.channel = channel
        self.reason = reason


class ManagerNotStartedError(WebSocketManagerError):
    """Exception levée lorsque le manager n'est pas démarré."""

    def __init__(self) -> None:
        super().__init__(
            t(
                "websocket.error.manager_not_started",
                default="WebSocketManager is not started. Call start() first.",
            )
        )


# ============================================================================
# ENUMS
# ============================================================================


class NotificationLevel(str, Enum):
    """Niveau de notification.

    Attributes:
        INFO: Information.
        SUCCESS: Succès.
        WARNING: Avertissement.
        ERROR: Erreur.
    """

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"

    @property
    def channel(self) -> str:
        """Canal associé."""
        return {
            NotificationLevel.INFO: CHANNEL_NOTIFICATIONS_INFO,
            NotificationLevel.SUCCESS: CHANNEL_NOTIFICATIONS_SUCCESS,
            NotificationLevel.WARNING: CHANNEL_NOTIFICATIONS_WARNING,
            NotificationLevel.ERROR: CHANNEL_NOTIFICATIONS_ERROR,
        }[self]

    @property
    def icon(self) -> str:
        """Icône Unicode."""
        return {
            NotificationLevel.INFO: "ℹ️",
            NotificationLevel.SUCCESS: "✅",
            NotificationLevel.WARNING: "⚠️",
            NotificationLevel.ERROR: "❌",
        }[self]


class MessagePriority(int, Enum):
    """Priorité d'un message.

    Attributes:
        LOW: Priorité basse.
        NORMAL: Priorité normale.
        HIGH: Priorité haute.
        URGENT: Priorité urgente.
    """

    LOW = PRIORITY_LOW
    NORMAL = PRIORITY_NORMAL
    HIGH = PRIORITY_HIGH
    URGENT = PRIORITY_URGENT


class ConnectionState(str, Enum):
    """État d'une connexion.

    Attributes:
        CONNECTED: Connectée.
        DISCONNECTED: Déconnectée.
        RECONNECTING: En cours de reconnexion.
    """

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class WebSocketMessage(BaseModel):
    """Message WebSocket structuré.

    Attributes:
        id: ID unique du message.
        type: Type de message.
        channel: Canal cible.
        payload: Contenu du message.
        timestamp: Timestamp ISO 8601.
        priority: Priorité du message.
        source: Source du message.
    """

    id: str = Field(default_factory=lambda: _generate_message_id(), description="ID unique.")
    type: str = Field(..., description="Type de message.")
    channel: str = Field(..., description="Canal cible.")
    payload: dict[str, Any] = Field(default_factory=dict, description="Contenu.")
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(), description="Timestamp.")
    priority: int = Field(default=PRIORITY_NORMAL, ge=0, le=3, description="Priorité.")
    source: str = Field(default="server", description="Source.")

    model_config = ConfigDict(extra="forbid")

    def to_dict(self) -> dict[str, Any]:
        """Convertit en dictionnaire sérialisable."""
        return self.model_dump(mode="json")


class BroadcastStats(BaseModel):
    """Statistiques de diffusion.

    Attributes:
        total_messages: Nombre total de messages diffusés.
        messages_by_channel: Compteur par canal.
        total_clients: Nombre total de clients.
        active_clients: Nombre de clients actifs.
        started_at: Timestamp de début de collecte.
        last_broadcast_at: Timestamp de la dernière diffusion.
    """

    total_messages: int = Field(default=0, ge=0)
    messages_by_channel: dict[str, int] = Field(default_factory=dict)
    total_clients: int = Field(default=0, ge=0)
    active_clients: int = Field(default=0, ge=0)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_broadcast_at: datetime | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")


class ChannelInfo(BaseModel):
    """Informations sur un canal.

    Attributes:
        name: Nom du canal.
        description: Description.
        subscriber_count: Nombre d'abonnés.
        message_count: Nombre de messages diffusés.
        last_message_at: Timestamp du dernier message.
    """

    name: str = Field(..., description="Nom.")
    description: str = Field(default="", description="Description.")
    subscriber_count: int = Field(default=0, ge=0, description="Abonnés.")
    message_count: int = Field(default=0, ge=0, description="Messages.")
    last_message_at: datetime | None = Field(default=None, description="Dernier message.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ConnectionInfo(BaseModel):
    """Informations sur une connexion.

    Attributes:
        client_id: ID du client.
        user_id: ID de l'utilisateur (si authentifié).
        connected_at: Timestamp de connexion.
        state: État de la connexion.
        subscribed_channels: Canaux abonnés.
        ip_address: Adresse IP.
        user_agent: User-Agent.
    """

    client_id: str = Field(..., description="ID client.")
    user_id: str | None = Field(default=None, description="ID utilisateur.")
    connected_at: datetime = Field(..., description="Connexion.")
    state: ConnectionState = Field(default=ConnectionState.CONNECTED, description="État.")
    subscribed_channels: list[str] = Field(default_factory=list, description="Canaux abonnés.")
    ip_address: str = Field(default="", description="IP.")
    user_agent: str = Field(default="", description="User-Agent.")

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# HELPERS PRIVÉS
# ============================================================================


def _generate_message_id() -> str:
    """Génère un ID unique pour un message.

    Returns:
        ID unique (format: msg_<timestamp>_<random>).
    """
    import secrets
    timestamp = int(datetime.now(UTC).timestamp() * 1000)
    random_part = secrets.token_hex(4)
    return f"msg_{timestamp}_{random_part}"


# ============================================================================
# EVENT BUS BRIDGE — Pont EventBus → WebSocket
# ============================================================================


class EventBusBridge:
    """Pont entre l'EventBus du core et le WebSocketManager.

    Écoute les événements EventBus et les propage automatiquement
    vers les canaux WebSocket appropriés.
    """

    def __init__(
        self,
        manager: WebSocketManager,
        *,
        event_bus: EventBus | None = None,
        event_mapping: dict[EventType, str] | None = None,
    ) -> None:
        """Initialise le pont.

        Args:
            manager: Instance du WebSocketManager.
            event_bus: Instance de l'EventBus (défaut: globale).
            event_mapping: Mapping personnalisé EventType → channel.
        """
        self._manager = manager
        self._event_bus = event_bus
        self._event_mapping = event_mapping or EVENT_TYPE_TO_CHANNEL
        self._subscriptions: list[Any] = []
        self._started = False

    async def start(self) -> None:
        """Démarre le pont et s'abonne aux événements."""
        if self._started:
            return

        if self._event_bus is None:
            try:
                self._event_bus = get_event_bus()
            except Exception as e:
                logger.warning("EventBus indisponible, pont WebSocket désactivé: {}", e)
                return

        # S'abonner à tous les événements mappés
        for event_type, channel in self._event_mapping.items():
            try:
                subscription = self._event_bus.on(
                    event_type,
                    self._create_handler(channel),
                )
                self._subscriptions.append(subscription)
            except Exception as e:
                logger.warning(
                    "Impossible de s'abonner à l'événement {}: {}",
                    event_type.value,
                    e,
                )

        self._started = True
        logger.info(
            "EventBusBridge démarré: {} abonnements",
            len(self._subscriptions),
        )

    async def stop(self) -> None:
        """Arrête le pont et se désabonne des événements."""
        if not self._started:
            return

        # Se désabonner de tous les événements
        for subscription in self._subscriptions:
            try:
                if hasattr(subscription, "unsubscribe"):
                    await subscription.unsubscribe()
            except Exception as e:
                logger.debug("Erreur lors du désabonnement: {}", e)

        self._subscriptions.clear()
        self._started = False
        logger.info("EventBusBridge arrêté")

    def _create_handler(self, channel: str) -> Callable:
        """Crée un handler pour un canal spécifique.

        Args:
            channel: Canal cible.

        Returns:
            Fonction handler.
        """
        async def handler(event: Any) -> None:
            try:
                await self._manager.broadcast(
                    channel=channel,
                    payload=event.payload if hasattr(event, "payload") else {},
                    message_type="event",
                    priority=PRIORITY_NORMAL,
                )
            except Exception as e:
                logger.warning(
                    "Erreur lors de la diffusion de l'événement vers {}: {}",
                    channel,
                    e,
                )

        return handler

    @property
    def is_started(self) -> bool:
        """Indique si le pont est démarré."""
        return self._started

    @property
    def subscription_count(self) -> int:
        """Nombre d'abonnements actifs."""
        return len(self._subscriptions)


# ============================================================================
# WEBSOCKET MANAGER — Orchestrateur principal
# ============================================================================


class WebSocketManager:
    """Gestionnaire WebSocket de haut niveau.

    Orchestre la diffusion des messages vers les clients WebSocket,
    gère les canaux, et fait le pont avec l'EventBus.
    """

    def __init__(
        self,
        *,
        enable_event_bus_bridge: bool = True,
        max_queue_size: int = MAX_BROADCAST_QUEUE_SIZE,
        broadcast_timeout: float = DEFAULT_BROADCAST_TIMEOUT_SECONDS,
    ) -> None:
        """Initialise le manager.

        Args:
            enable_event_bus_bridge: Activer le pont EventBus.
            max_queue_size: Taille max de la file de diffusion.
            broadcast_timeout: Timeout de diffusion (secondes).
        """
        self._enable_bridge = enable_event_bus_bridge
        self._max_queue_size = max_queue_size
        self._broadcast_timeout = broadcast_timeout

        self._started = False
        self._stats = BroadcastStats()
        self._stats_lock = asyncio.Lock()

        self._bridge: EventBusBridge | None = None
        self._connection_manager: Any = None  # Sera défini par routers/ws.py

        # File de diffusion
        self._broadcast_queue: asyncio.Queue[WebSocketMessage] = asyncio.Queue(
            maxsize=max_queue_size,
        )
        self._broadcast_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Démarre le manager et le pont EventBus."""
        if self._started:
            return

        # Démarrer le pont EventBus si activé
        if self._enable_bridge:
            self._bridge = EventBusBridge(self)
            await self._bridge.start()

        # Démarrer la tâche de diffusion
        self._broadcast_task = asyncio.create_task(
            self._broadcast_loop(),
            name="websocket_broadcast_loop",
        )

        self._started = True
        logger.info("WebSocketManager démarré")

    async def stop(self) -> None:
        """Arrête le manager et le pont EventBus."""
        if not self._started:
            return

        # Arrêter la tâche de diffusion
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass
            self._broadcast_task = None

        # Arrêter le pont EventBus
        if self._bridge:
            await self._bridge.stop()
            self._bridge = None

        self._started = False
        logger.info("WebSocketManager arrêté")

    async def _broadcast_loop(self) -> None:
        """Boucle de diffusion asynchrone."""
        try:
            while True:
                message = await self._broadcast_queue.get()
                try:
                    await self._do_broadcast(message)
                except Exception as e:
                    logger.warning("Erreur lors de la diffusion: {}", e)
                finally:
                    self._broadcast_queue.task_done()
        except asyncio.CancelledError:
            pass

    async def _do_broadcast(self, message: WebSocketMessage) -> None:
        """Effectue la diffusion effective d'un message.

        Args:
            message: Message à diffuser.
        """
        if self._connection_manager is None:
            # Pas de connection manager, on skip
            return

        try:
            # Utiliser le connection manager du routeur ws.py
            await self._connection_manager.broadcast_to_channel(
                message.channel,
                message.to_dict(),
            )

            # Mettre à jour les statistiques
            async with self._stats_lock:
                new_stats = self._stats.model_dump()
                new_stats["total_messages"] += 1
                new_stats["messages_by_channel"][message.channel] = (
                    new_stats["messages_by_channel"].get(message.channel, 0) + 1
                )
                new_stats["last_broadcast_at"] = datetime.now(UTC)
                self._stats = BroadcastStats(**new_stats)

        except Exception as e:
            logger.warning(
                "Erreur lors de la diffusion vers {}: {}",
                message.channel,
                e,
            )

    # =====================================================================
    # API PUBLIQUE — Diffusion de messages
    # =====================================================================

    async def broadcast(
        self,
        channel: str,
        payload: dict[str, Any],
        *,
        message_type: str = "message",
        priority: int = PRIORITY_NORMAL,
        source: str = "server",
    ) -> str:
        """Diffuse un message vers un canal.

        Args:
            channel: Canal cible.
            payload: Contenu du message.
            message_type: Type de message.
            priority: Priorité (0-3).
            source: Source du message.

        Returns:
            ID du message diffusé.

        Raises:
            ChannelNotFoundError: Si le canal n'existe pas.
            ManagerNotStartedError: Si le manager n'est pas démarré.
        """
        if not self._started:
            raise ManagerNotStartedError()

        if channel not in ALL_CHANNELS:
            raise ChannelNotFoundError(channel)

        message = WebSocketMessage(
            type=message_type,
            channel=channel,
            payload=payload,
            priority=priority,
            source=source,
        )

        # Ajouter à la file de diffusion
        try:
            self._broadcast_queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.warning(
                "File de diffusion pleine, message ignoré: channel={}",
                channel,
            )

        return message.id

    async def broadcast_to_user(
        self,
        user_id: str,
        channel: str,
        payload: dict[str, Any],
        *,
        message_type: str = "message",
    ) -> int:
        """Diffuse un message à un utilisateur spécifique.

        Args:
            user_id: ID de l'utilisateur.
            channel: Canal cible.
            payload: Contenu du message.
            message_type: Type de message.

        Returns:
            Nombre de clients ayant reçu le message.
        """
        if self._connection_manager is None:
            return 0

        message = WebSocketMessage(
            type=message_type,
            channel=channel,
            payload=payload,
        )

        return await self._connection_manager.send_to_user(
            user_id,
            message.to_dict(),
            channel=channel,
        )

    async def broadcast_global(
        self,
        payload: dict[str, Any],
        *,
        channel: str | None = None,
        message_type: str = "message",
    ) -> int:
        """Diffuse un message à tous les clients connectés.

        Args:
            payload: Contenu du message.
            channel: Canal cible (optionnel).
            message_type: Type de message.

        Returns:
            Nombre de clients ayant reçu le message.
        """
        if self._connection_manager is None:
            return 0

        message = WebSocketMessage(
            type=message_type,
            channel=channel,
            payload=payload,
        )

        return await self._connection_manager.broadcast_all(
            message.to_dict(),
            channel=channel,
        )

    # =====================================================================
    # API PUBLIQUE — Accès aux informations
    # =====================================================================

    async def get_stats(self) -> BroadcastStats:
        """Récupère les statistiques de diffusion.

        Returns:
            Statistiques actuelles.
        """
        async with self._stats_lock:
            return self._stats

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        async with self._stats_lock:
            self._stats = BroadcastStats()

    async def get_channels_info(self) -> list[ChannelInfo]:
        """Récupère les informations sur tous les canaux.

        Returns:
            Liste de ChannelInfo.
        """
        channels_info: list[ChannelInfo] = []

        for channel_name in sorted(ALL_CHANNELS):
            subscriber_count = 0
            message_count = 0
            last_message_at = None

            # Récupérer les infos depuis le connection manager
            if self._connection_manager:
                subscribers = self._connection_manager.get_subscribers(channel_name)
                subscriber_count = len(subscribers)

            # Récupérer le compteur de messages
            async with self._stats_lock:
                message_count = self._stats.messages_by_channel.get(channel_name, 0)
                last_message_at = self._stats.last_broadcast_at

            channels_info.append(
                ChannelInfo(
                    name=channel_name,
                    description=self._get_channel_description(channel_name),
                    subscriber_count=subscriber_count,
                    message_count=message_count,
                    last_message_at=last_message_at,
                )
            )

        return channels_info

    async def get_connections_info(self) -> list[ConnectionInfo]:
        """Récupère les informations sur toutes les connexions.

        Returns:
            Liste de ConnectionInfo.
        """
        if self._connection_manager is None:
            return []

        connections: list[ConnectionInfo] = []

        # Récupérer les clients depuis le connection manager
        for client_id, client in self._connection_manager._clients.items():
            connections.append(
                ConnectionInfo(
                    client_id=client.client_id,
                    user_id=client.user_id,
                    connected_at=client.info.connected_at,
                    state=ConnectionState.CONNECTED,
                    subscribed_channels=list(client.subscribed_channels),
                    ip_address=client.info.remote_address,
                    user_agent=client.info.user_agent,
                )
            )

        return connections

    def _get_channel_description(self, channel: str) -> str:
        """Retourne la description d'un canal.

        Args:
            channel: Nom du canal.

        Returns:
            Description.
        """
        descriptions = {
            CHANNEL_DOWNLOADS_PROGRESS: "Download progress updates",
            CHANNEL_DOWNLOADS_COMPLETED: "Completed downloads",
            CHANNEL_DOWNLOADS_FAILED: "Failed downloads",
            CHANNEL_DOWNLOADS_STARTED: "Started downloads",
            CHANNEL_DOWNLOADS_PAUSED: "Paused downloads",
            CHANNEL_LIBRARY_ADDED: "Mangas added to library",
            CHANNEL_LIBRARY_REMOVED: "Mangas removed from library",
            CHANNEL_LIBRARY_UPDATED: "Mangas updated in library",
            CHANNEL_LIBRARY_SCAN: "Library scan progress",
            CHANNEL_NOTIFICATIONS_INFO: "Information notifications",
            CHANNEL_NOTIFICATIONS_WARNING: "Warning notifications",
            CHANNEL_NOTIFICATIONS_ERROR: "Error notifications",
            CHANNEL_NOTIFICATIONS_SUCCESS: "Success notifications",
            CHANNEL_LOGS_STREAM: "Live log streaming",
            CHANNEL_SEARCH_PROGRESS: "Search progress",
            CHANNEL_SEARCH_COMPLETED: "Completed searches",
            CHANNEL_SYSTEM_STATUS: "System status updates",
            CHANNEL_SYSTEM_EVENTS: "System events",
            CHANNEL_SYSTEM_METRICS: "System metrics",
            CHANNEL_AUTH_LOGIN: "User login events",
            CHANNEL_AUTH_LOGOUT: "User logout events",
        }
        return descriptions.get(channel, "")

    # =====================================================================
    # API PUBLIQUE — Configuration
    # =====================================================================

    def set_connection_manager(self, manager: Any) -> None:
        """Définit le connection manager (depuis routers/ws.py).

        Args:
            manager: Instance de ConnectionManager.
        """
        self._connection_manager = manager
        logger.debug("ConnectionManager défini dans WebSocketManager")

    @property
    def is_started(self) -> bool:
        """Indique si le manager est démarré."""
        return self._started

    @property
    def bridge(self) -> EventBusBridge | None:
        """Pont EventBus (si activé)."""
        return self._bridge


# ============================================================================
# INSTANCE GLOBALE
# ============================================================================


_websocket_manager: WebSocketManager | None = None


def get_websocket_manager() -> WebSocketManager:
    """Retourne l'instance globale du WebSocketManager.

    Crée une instance par défaut si aucune n'existe.

    Returns:
        Instance de WebSocketManager.
    """
    global _websocket_manager
    if _websocket_manager is None:
        _websocket_manager = WebSocketManager()
    return _websocket_manager


def set_websocket_manager(manager: WebSocketManager) -> None:
    """Définit l'instance globale du WebSocketManager.

    Args:
        manager: Instance de WebSocketManager.
    """
    global _websocket_manager
    _websocket_manager = manager


def reset_websocket_manager() -> None:
    """Réinitialise l'instance globale."""
    global _websocket_manager
    if _websocket_manager is not None:
        # Arrêter proprement si démarré
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(_websocket_manager.stop())
        except Exception:
            pass
    _websocket_manager = None


# ============================================================================
# HELPERS DE HAUT NIVEAU — Helpers métier
# ============================================================================


async def broadcast_download_progress(
    task_id: str,
    progress: float,
    *,
    manga_title: str = "",
    pages_completed: int = 0,
    pages_total: int = 0,
    speed_bytes_per_sec: float = 0.0,
    eta_seconds: float = 0.0,
) -> str:
    """Diffuse la progression d'un téléchargement.

    Args:
        task_id: ID de la tâche.
        progress: Progression (0.0 à 1.0).
        manga_title: Titre du manga.
        pages_completed: Pages téléchargées.
        pages_total: Pages totales.
        speed_bytes_per_sec: Vitesse en bytes/seconde.
        eta_seconds: Temps restant estimé.

    Returns:
        ID du message diffusé.

    Example:
        >>> await broadcast_download_progress(
        ...     task_id="task_123",
        ...     progress=0.5,
        ...     manga_title="One Piece",
        ...     pages_completed=50,
        ...     pages_total=100,
        ... )
    """
    manager = get_websocket_manager()
    return await manager.broadcast(
        channel=CHANNEL_DOWNLOADS_PROGRESS,
        payload={
            "task_id": task_id,
            "progress": progress,
            "manga_title": manga_title,
            "pages_completed": pages_completed,
            "pages_total": pages_total,
            "speed_bytes_per_sec": speed_bytes_per_sec,
            "eta_seconds": eta_seconds,
        },
        message_type="download_progress",
        priority=PRIORITY_NORMAL,
    )


async def broadcast_download_completed(
    task_id: str,
    *,
    manga_title: str = "",
    output_path: str = "",
    size_bytes: int = 0,
    duration_seconds: float = 0.0,
) -> str:
    """Diffuse la notification de téléchargement terminé.

    Args:
        task_id: ID de la tâche.
        manga_title: Titre du manga.
        output_path: Chemin de sortie.
        size_bytes: Taille du fichier.
        duration_seconds: Durée du téléchargement.

    Returns:
        ID du message diffusé.
    """
    manager = get_websocket_manager()
    return await manager.broadcast(
        channel=CHANNEL_DOWNLOADS_COMPLETED,
        payload={
            "task_id": task_id,
            "manga_title": manga_title,
            "output_path": output_path,
            "size_bytes": size_bytes,
            "duration_seconds": duration_seconds,
        },
        message_type="download_completed",
        priority=PRIORITY_HIGH,
    )


async def broadcast_download_failed(
    task_id: str,
    error: str,
    *,
    manga_title: str = "",
    retry_count: int = 0,
) -> str:
    """Diffuse la notification d'échec de téléchargement.

    Args:
        task_id: ID de la tâche.
        error: Message d'erreur.
        manga_title: Titre du manga.
        retry_count: Nombre de tentatives.

    Returns:
        ID du message diffusé.
    """
    manager = get_websocket_manager()
    return await manager.broadcast(
        channel=CHANNEL_DOWNLOADS_FAILED,
        payload={
            "task_id": task_id,
            "error": error,
            "manga_title": manga_title,
            "retry_count": retry_count,
        },
        message_type="download_failed",
        priority=PRIORITY_URGENT,
    )


async def broadcast_library_update(
    event_type: str,
    manga_id: str,
    *,
    manga_title: str = "",
    details: dict[str, Any] | None = None,
) -> str:
    """Diffuse une mise à jour de la bibliothèque.

    Args:
        event_type: Type d'événement (added, removed, updated).
        manga_id: ID du manga.
        manga_title: Titre du manga.
        details: Détails additionnels.

    Returns:
        ID du message diffusé.
    """
    channel_map = {
        "added": CHANNEL_LIBRARY_ADDED,
        "removed": CHANNEL_LIBRARY_REMOVED,
        "updated": CHANNEL_LIBRARY_UPDATED,
    }
    channel = channel_map.get(event_type, CHANNEL_LIBRARY_UPDATED)

    manager = get_websocket_manager()
    return await manager.broadcast(
        channel=channel,
        payload={
            "event_type": event_type,
            "manga_id": manga_id,
            "manga_title": manga_title,
            "details": details or {},
        },
        message_type="library_update",
        priority=PRIORITY_NORMAL,
    )


async def send_notification(
    message: str,
    *,
    level: NotificationLevel | str = NotificationLevel.INFO,
    title: str = "",
    user_id: str | None = None,
    duration_ms: int = 5000,
    actions: list[dict[str, Any]] | None = None,
) -> str | int:
    """Envoie une notification aux clients.

    Args:
        message: Message de la notification.
        level: Niveau (info, success, warning, error).
        title: Titre de la notification.
        user_id: ID utilisateur (None = tous).
        duration_ms: Durée d'affichage.
        actions: Actions disponibles (boutons).

    Returns:
        ID du message ou nombre de clients.

    Example:
        >>> await send_notification(
        ...     message="Download completed!",
        ...     level="success",
        ...     title="One Piece Ch. 123",
        ... )
    """
    if isinstance(level, str):
        level = NotificationLevel(level)

    payload = {
        "message": message,
        "level": level.value,
        "title": title or APP_NAME,
        "icon": level.icon,
        "duration_ms": duration_ms,
        "actions": actions or [],
    }

    manager = get_websocket_manager()

    if user_id:
        return await manager.broadcast_to_user(
            user_id=user_id,
            channel=level.channel,
            payload=payload,
            message_type="notification",
        )
    else:
        return await manager.broadcast(
            channel=level.channel,
            payload=payload,
            message_type="notification",
            priority=PRIORITY_HIGH if level in (NotificationLevel.ERROR, NotificationLevel.WARNING) else PRIORITY_NORMAL,
        )


async def broadcast_log_entry(
    level: str,
    message: str,
    *,
    module: str = "",
    timestamp: str | None = None,
) -> str:
    """Diffuse une entrée de log vers les clients abonnés.

    Args:
        level: Niveau de log (DEBUG, INFO, WARNING, ERROR).
        message: Message du log.
        module: Module source.
        timestamp: Timestamp (défaut: maintenant).

    Returns:
        ID du message diffusé.
    """
    manager = get_websocket_manager()
    return await manager.broadcast(
        channel=CHANNEL_LOGS_STREAM,
        payload={
            "level": level,
            "message": message,
            "module": module,
            "timestamp": timestamp or datetime.now(UTC).isoformat(),
        },
        message_type="log_entry",
        priority=PRIORITY_LOW,
    )


async def broadcast_search_progress(
    query: str,
    *,
    sites_searched: int = 0,
    total_sites: int = 0,
    results_count: int = 0,
    current_site: str = "",
) -> str:
    """Diffuse la progression d'une recherche.

    Args:
        query: Requête de recherche.
        sites_searched: Nombre de sites recherchés.
        total_sites: Nombre total de sites.
        results_count: Nombre de résultats trouvés.
        current_site: Site en cours de recherche.

    Returns:
        ID du message diffusé.
    """
    manager = get_websocket_manager()
    return await manager.broadcast(
        channel=CHANNEL_SEARCH_PROGRESS,
        payload={
            "query": query,
            "sites_searched": sites_searched,
            "total_sites": total_sites,
            "results_count": results_count,
            "current_site": current_site,
            "progress": sites_searched / total_sites if total_sites > 0 else 0.0,
        },
        message_type="search_progress",
        priority=PRIORITY_NORMAL,
    )


async def broadcast_system_status(
    status: str,
    *,
    details: dict[str, Any] | None = None,
) -> str:
    """Diffuse un changement de statut système.

    Args:
        status: Statut (starting, running, stopping, error).
        details: Détails additionnels.

    Returns:
        ID du message diffusé.
    """
    manager = get_websocket_manager()
    return await manager.broadcast(
        channel=CHANNEL_SYSTEM_STATUS,
        payload={
            "status": status,
            "app_name": APP_NAME,
            "version": APP_VERSION,
            "timestamp": datetime.now(UTC).isoformat(),
            "details": details or {},
        },
        message_type="system_status",
        priority=PRIORITY_HIGH,
    )


async def broadcast_system_metrics(
    *,
    cpu_percent: float = 0.0,
    memory_mb: float = 0.0,
    active_connections: int = 0,
    total_requests: int = 0,
    uptime_seconds: float = 0.0,
) -> str:
    """Diffuse les métriques système.

    Args:
        cpu_percent: Utilisation CPU (%).
        memory_mb: Utilisation mémoire (MB).
        active_connections: Connexions actives.
        total_requests: Total de requêtes.
        uptime_seconds: Durée de fonctionnement.

    Returns:
        ID du message diffusé.
    """
    manager = get_websocket_manager()
    return await manager.broadcast(
        channel=CHANNEL_SYSTEM_METRICS,
        payload={
            "cpu_percent": cpu_percent,
            "memory_mb": memory_mb,
            "active_connections": active_connections,
            "total_requests": total_requests,
            "uptime_seconds": uptime_seconds,
            "timestamp": datetime.now(UTC).isoformat(),
        },
        message_type="system_metrics",
        priority=PRIORITY_LOW,
    )


# ============================================================================
# HELPERS D'INTÉGRATION
# ============================================================================


async def start_websocket_manager() -> WebSocketManager:
    """Démarre le WebSocketManager global.

    Fonction utilitaire pour démarrer le manager au lancement de l'application.

    Returns:
        Instance du WebSocketManager démarrée.

    Example:
        >>> # Dans le lifespan de FastAPI
        >>> @app.on_event("startup")
        >>> async def startup():
        ...     await start_websocket_manager()
    """
    manager = get_websocket_manager()
    await manager.start()
    return manager


async def stop_websocket_manager() -> None:
    """Arrête le WebSocketManager global.

    Fonction utilitaire pour arrêter le manager à la fermeture de l'application.

    Example:
        >>> # Dans le lifespan de FastAPI
        >>> @app.on_event("shutdown")
        >>> async def shutdown():
        ...     await stop_websocket_manager()
    """
    manager = get_websocket_manager()
    await manager.stop()


def integrate_with_connection_manager(connection_manager: Any) -> None:
    """Intègre le WebSocketManager avec le ConnectionManager du routeur ws.py.

    Args:
        connection_manager: Instance de ConnectionManager.

    Example:
        >>> from nexusdl.interfaces.web.backend.routers.ws import get_connection_manager
        >>> integrate_with_connection_manager(get_connection_manager())
    """
    manager = get_websocket_manager()
    manager.set_connection_manager(connection_manager)
    logger.info("WebSocketManager intégré avec ConnectionManager")


# ============================================================================
# DÉCORATEURS — Helpers pour les services
# ============================================================================


def notify_on_download_progress(func: Callable) -> Callable:
    """Décorateur pour notifier la progression d'un téléchargement.

    Utilise la fonction décorée pour obtenir la progression et la diffuse
    automatiquement vers le canal downloads.progress.

    Args:
        func: Fonction à décorer.

    Returns:
        Fonction décorée.

    Example:
        >>> @notify_on_download_progress
        >>> async def download_chapter(chapter_id: str):
        ...     for i in range(100):
        ...         await download_page(i)
        ...         yield {"progress": i / 100, "task_id": chapter_id}
    """
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = func(*args, **kwargs)

        # Si c'est un générateur asynchrone, diffuser la progression
        if hasattr(result, "__aiter__"):
            async for update in result:
                if isinstance(update, dict) and "progress" in update:
                    await broadcast_download_progress(
                        task_id=update.get("task_id", ""),
                        progress=update["progress"],
                        manga_title=update.get("manga_title", ""),
                        pages_completed=update.get("pages_completed", 0),
                        pages_total=update.get("pages_total", 0),
                    )
                yield update
        else:
            return result

    return wrapper


def notify_on_event(event_type: EventType, channel: str) -> Callable:
    """Décorateur pour diffuser automatiquement un événement vers un canal.

    Args:
        event_type: Type d'événement à écouter.
        channel: Canal de diffusion.

    Returns:
        Décorateur.

    Example:
        >>> @notify_on_event(EventType.DOWNLOAD_TASK_COMPLETED, CHANNEL_DOWNLOADS_COMPLETED)
        >>> async def on_download_completed(event):
        ...     pass
    """
    def decorator(func: Callable) -> Callable:
        async def wrapper(event: Any) -> Any:
            # Appeler la fonction originale
            result = await func(event)

            # Diffuser l'événement
            manager = get_websocket_manager()
            if manager.is_started:
                await manager.broadcast(
                    channel=channel,
                    payload=event.payload if hasattr(event, "payload") else {},
                    message_type=event_type.value,
                )

            return result

        return wrapper

    return decorator


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes de canaux
    "CHANNEL_DOWNLOADS_PROGRESS",
    "CHANNEL_DOWNLOADS_COMPLETED",
    "CHANNEL_DOWNLOADS_FAILED",
    "CHANNEL_DOWNLOADS_STARTED",
    "CHANNEL_DOWNLOADS_PAUSED",
    "CHANNEL_LIBRARY_ADDED",
    "CHANNEL_LIBRARY_REMOVED",
    "CHANNEL_LIBRARY_UPDATED",
    "CHANNEL_LIBRARY_SCAN",
    "CHANNEL_NOTIFICATIONS_INFO",
    "CHANNEL_NOTIFICATIONS_WARNING",
    "CHANNEL_NOTIFICATIONS_ERROR",
    "CHANNEL_NOTIFICATIONS_SUCCESS",
    "CHANNEL_LOGS_STREAM",
    "CHANNEL_SEARCH_PROGRESS",
    "CHANNEL_SEARCH_COMPLETED",
    "CHANNEL_SYSTEM_STATUS",
    "CHANNEL_SYSTEM_EVENTS",
    "CHANNEL_SYSTEM_METRICS",
    "CHANNEL_AUTH_LOGIN",
    "CHANNEL_AUTH_LOGOUT",
    "ALL_CHANNELS",
    "EVENT_TYPE_TO_CHANNEL",
    # Constantes de priorité
    "PRIORITY_LOW",
    "PRIORITY_NORMAL",
    "PRIORITY_HIGH",
    "PRIORITY_URGENT",
    # Constantes de limites
    "MAX_MESSAGE_SIZE_BYTES",
    "MAX_BROADCAST_QUEUE_SIZE",
    "DEFAULT_BROADCAST_TIMEOUT_SECONDS",
    # Exceptions
    "WebSocketManagerError",
    "ChannelNotFoundError",
    "BroadcastError",
    "ManagerNotStartedError",
    # Enums
    "NotificationLevel",
    "MessagePriority",
    "ConnectionState",
    # Modèles
    "WebSocketMessage",
    "BroadcastStats",
    "ChannelInfo",
    "ConnectionInfo",
    # Classes
    "EventBusBridge",
    "WebSocketManager",
    # Instance globale
    "get_websocket_manager",
    "set_websocket_manager",
    "reset_websocket_manager",
    # Helpers de haut niveau
    "broadcast_download_progress",
    "broadcast_download_completed",
    "broadcast_download_failed",
    "broadcast_library_update",
    "send_notification",
    "broadcast_log_entry",
    "broadcast_search_progress",
    "broadcast_system_status",
    "broadcast_system_metrics",
    # Helpers d'intégration
    "start_websocket_manager",
    "stop_websocket_manager",
    "integrate_with_connection_manager",
    # Décorateurs
    "notify_on_download_progress",
    "notify_on_event",
]
