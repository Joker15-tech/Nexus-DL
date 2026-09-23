"""Routeur WebSocket pour l'API REST NexusDL.

Ce module fournit un routeur WebSocket FastAPI complet pour les communications
temps réel entre le serveur et les clients. Il gère l'authentification, les
abonnements à des channels, la diffusion de messages, et l'intégration avec
l'EventBus pour propager les événements système.

**Fonctionnalités** :
    - Connexion WebSocket avec authentification JWT/API key
    - Système de channels/topics (pub/sub)
    - 10 channels prédéfinis (downloads, notifications, logs, etc.)
    - Heartbeat automatique (ping/pong)
    - Rate limiting par client
    - Taille maximale des messages configurable
    - Timeout d'inactivité
    - Diffusion ciblée (broadcast, unicast, par channel)
    - Intégration avec EventBus pour propagation d'événements
    - Logging structuré des connexions et messages
    - Statistiques temps réel (clients connectés, messages échangés)
    - Gestion robuste des erreurs et déconnexions
    - Support JSON structuré pour tous les messages
    - Filtrage par permissions utilisateur

**Architecture** :
    WebSocketRouter (FastAPI APIRouter)
        ├── ConnectionManager (gestion des connexions)
        │   ├── WebSocketClient (client connecté)
        │   ├── Channel (abstraction channel/topic)
        │   └── ChannelRegistry (registre des channels)
        ├── MessageBroker (broker de messages)
        │   ├── WebSocketMessage (message structuré)
        │   └── MessageType (types de messages)
        ├── AuthHandler (authentification WebSocket)
        └── WebSocketStats (statistiques)

**Protocole de message** :
    Tous les messages sont au format JSON avec la structure suivante :
    {
        "id": "uuid-v4",
        "type": "message_type",
        "channel": "channel_name",
        "payload": {...},
        "timestamp": "ISO8601",
        "source": "source_id"
    }

**Channels prédéfinis** :
    - downloads.progress    : Progression des téléchargements
    - downloads.completed   : Téléchargements terminés
    - downloads.failed      : Téléchargements échoués
    - notifications.info    : Notifications info
    - notifications.warning : Notifications warning
    - notifications.error   : Notifications error
    - logs.stream           : Streaming des logs
    - search.results        : Résultats de recherche
    - library.changes       : Changements bibliothèque
    - system.status         : Statut système
    - system.events         : Événements système

**Exemple d'utilisation côté client (JavaScript)** :
    >>> const ws = new WebSocket('ws://localhost:8000/ws?token=xxx');
    >>>
    >>> ws.onopen = () => {
    ...     // S'abonner à un channel
    ...     ws.send(JSON.stringify({
    ...         type: 'subscribe',
    ...         channel: 'downloads.progress'
    ...     }));
    ... };
    >>>
    >>> ws.onmessage = (event) => {
    ...     const message = JSON.parse(event.data);
    ...     console.log('Message reçu:', message);
    ... };

**Exemple d'utilisation côté serveur** :
    >>> from nexusdl.interfaces.web.backend.routers.ws import (
    ...     get_connection_manager, broadcast_to_channel,
    ... )
    >>>
    >>> # Diffuser un message à tous les abonnés d'un channel
    >>> await broadcast_to_channel(
    ...     "downloads.progress",
    ...     {"task_id": "123", "progress": 0.5},
    ... )

Intégration :
    - fastapi                 : Framework web
    - core/events.py          : EventBus pour propagation
    - core/logger.py          : Logs
    - core/i18n.py            : Traductions
    - core/exceptions.py      : Exceptions
    - interfaces/web/backend/middleware/auth.py : Authentification
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, Final

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from fastapi import APIRouter, WebSocket, WebSocketDisconnect
    from starlette.websockets import WebSocketState
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from nexusdl.core.constants import APP_NAME
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t


# ============================================================================
# CONSTANTES
# ============================================================================


# Valeurs par défaut
DEFAULT_HEARTBEAT_INTERVAL_SECONDS: Final[int] = 30
DEFAULT_INACTIVITY_TIMEOUT_SECONDS: Final[int] = 300  # 5 minutes
DEFAULT_MAX_MESSAGE_SIZE_BYTES: Final[int] = 65536  # 64 KB
DEFAULT_MAX_CLIENTS_PER_USER: Final[int] = 5
DEFAULT_RATE_LIMIT_MESSAGES_PER_SECOND: Final[int] = 10

# Codes de fermeture WebSocket standards
WS_CLOSE_NORMAL: Final[int] = 1000
WS_CLOSE_GOING_AWAY: Final[int] = 1001
WS_CLOSE_PROTOCOL_ERROR: Final[int] = 1002
WS_CLOSE_UNSUPPORTED_DATA: Final[int] = 1003
WS_CLOSE_NO_STATUS_RECEIVED: Final[int] = 1005
WS_CLOSE_ABNORMAL: Final[int] = 1006
WS_CLOSE_INVALID_PAYLOAD: Final[int] = 1007
WS_CLOSE_POLICY_VIOLATION: Final[int] = 1008
WS_CLOSE_MESSAGE_TOO_BIG: Final[int] = 1009
WS_CLOSE_MANDATORY_EXTENSION: Final[int] = 1010
WS_CLOSE_INTERNAL_ERROR: Final[int] = 1011
WS_CLOSE_SERVICE_RESTART: Final[int] = 1012
WS_CLOSE_TRY_AGAIN_LATER: Final[int] = 1013

# Codes de fermeture personnalisés NexusDL
WS_CLOSE_AUTH_REQUIRED: Final[int] = 4001
WS_CLOSE_AUTH_FAILED: Final[int] = 4002
WS_CLOSE_RATE_LIMITED: Final[int] = 4003
WS_CLOSE_CHANNEL_NOT_FOUND: Final[int] = 4004
WS_CLOSE_PERMISSION_DENIED: Final[int] = 4005

# Channels prédéfinis
CHANNEL_DOWNLOADS_PROGRESS: Final[str] = "downloads.progress"
CHANNEL_DOWNLOADS_COMPLETED: Final[str] = "downloads.completed"
CHANNEL_DOWNLOADS_FAILED: Final[str] = "downloads.failed"
CHANNEL_NOTIFICATIONS_INFO: Final[str] = "notifications.info"
CHANNEL_NOTIFICATIONS_WARNING: Final[str] = "notifications.warning"
CHANNEL_NOTIFICATIONS_ERROR: Final[str] = "notifications.error"
CHANNEL_LOGS_STREAM: Final[str] = "logs.stream"
CHANNEL_SEARCH_RESULTS: Final[str] = "search.results"
CHANNEL_LIBRARY_CHANGES: Final[str] = "library.changes"
CHANNEL_SYSTEM_STATUS: Final[str] = "system.status"
CHANNEL_SYSTEM_EVENTS: Final[str] = "system.events"

ALL_CHANNELS: Final[frozenset[str]] = frozenset({
    CHANNEL_DOWNLOADS_PROGRESS,
    CHANNEL_DOWNLOADS_COMPLETED,
    CHANNEL_DOWNLOADS_FAILED,
    CHANNEL_NOTIFICATIONS_INFO,
    CHANNEL_NOTIFICATIONS_WARNING,
    CHANNEL_NOTIFICATIONS_ERROR,
    CHANNEL_LOGS_STREAM,
    CHANNEL_SEARCH_RESULTS,
    CHANNEL_LIBRARY_CHANGES,
    CHANNEL_SYSTEM_STATUS,
    CHANNEL_SYSTEM_EVENTS,
})


# ============================================================================
# EXCEPTIONS
# ============================================================================


class WebSocketError(NexusDLError):
    """Exception de base pour les erreurs WebSocket."""


class WebSocketAuthError(WebSocketError):
    """Exception levée lorsqu'une authentification WebSocket échoue.

    Attributes:
        reason: Raison de l'échec.
    """

    def __init__(self, reason: str = "") -> None:
        msg = t("websocket.auth_failed", default="WebSocket authentication failed")
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


class WebSocketChannelError(WebSocketError):
    """Exception levée lorsqu'un channel est invalide.

    Attributes:
        channel: Nom du channel.
        reason: Raison de l'erreur.
    """

    def __init__(self, channel: str, reason: str = "") -> None:
        msg = f"Erreur de channel: {channel}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.channel = channel
        self.reason = reason


class WebSocketMessageError(WebSocketError):
    """Exception levée lorsqu'un message est invalide.

    Attributes:
        reason: Raison de l'erreur.
    """

    def __init__(self, reason: str = "") -> None:
        msg = t("websocket.invalid_message", default="Invalid WebSocket message")
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


class WebSocketRateLimitError(WebSocketError):
    """Exception levée lorsque la limite de messages est dépassée.

    Attributes:
        client_id: ID du client.
        limit: Limite de messages par seconde.
    """

    def __init__(self, client_id: str, limit: int) -> None:
        super().__init__(
            t(
                "websocket.rate_limited",
                default="Rate limit exceeded: {limit} messages/second",
                limit=limit,
            )
        )
        self.client_id = client_id
        self.limit = limit


# ============================================================================
# ENUMS
# ============================================================================


class MessageType(str, Enum):
    """Types de messages WebSocket.

    Attributes:
        AUTH: Message d'authentification.
        SUBSCRIBE: Abonnement à un channel.
        UNSUBSCRIBE: Désabonnement d'un channel.
        PING: Heartbeat ping.
        PONG: Heartbeat pong.
        MESSAGE: Message générique.
        ERROR: Message d'erreur.
        SYSTEM: Message système.
    """

    AUTH = "auth"
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    PING = "ping"
    PONG = "pong"
    MESSAGE = "message"
    ERROR = "error"
    SYSTEM = "system"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            MessageType.AUTH: t("websocket.type.auth", default="Authentication"),
            MessageType.SUBSCRIBE: t("websocket.type.subscribe", default="Subscribe"),
            MessageType.UNSUBSCRIBE: t("websocket.type.unsubscribe", default="Unsubscribe"),
            MessageType.PING: t("websocket.type.ping", default="Ping"),
            MessageType.PONG: t("websocket.type.pong", default="Pong"),
            MessageType.MESSAGE: t("websocket.type.message", default="Message"),
            MessageType.ERROR: t("websocket.type.error", default="Error"),
            MessageType.SYSTEM: t("websocket.type.system", default="System"),
        }[self]


class ClientState(str, Enum):
    """État d'un client WebSocket.

    Attributes:
        CONNECTING: Connexion en cours.
        AUTHENTICATING: Authentification en cours.
        CONNECTED: Connecté et authentifié.
        DISCONNECTING: Déconnexion en cours.
        DISCONNECTED: Déconnecté.
    """

    CONNECTING = "connecting"
    AUTHENTICATING = "authenticating"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    DISCONNECTED = "disconnected"


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class WebSocketMessage(BaseModel):
    """Message WebSocket structuré.

    Attributes:
        id: ID unique du message (UUID v4).
        type: Type de message.
        channel: Channel cible (optionnel).
        payload: Contenu du message.
        timestamp: Timestamp ISO 8601.
        source: Source du message.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="ID unique.")
    type: MessageType = Field(..., description="Type de message.")
    channel: str | None = Field(default=None, description="Channel cible.")
    payload: dict[str, Any] = Field(default_factory=dict, description="Contenu.")
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(), description="Timestamp.")
    source: str = Field(default="server", description="Source.")

    model_config = ConfigDict(extra="forbid")

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, v: str | None) -> str | None:
        """Valide le channel."""
        if v is not None and v not in ALL_CHANNELS:
            logger.debug("Channel inconnu: {}", v)
        return v

    def to_json(self) -> str:
        """Convertit en JSON.

        Returns:
            Chaîne JSON.
        """
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str) -> WebSocketMessage:
        """Parse depuis JSON.

        Args:
            data: Chaîne JSON.

        Returns:
            Instance de WebSocketMessage.
        """
        return cls.model_validate_json(data)

    @classmethod
    def create_error(cls, message: str, code: str = "error") -> WebSocketMessage:
        """Crée un message d'erreur.

        Args:
            message: Message d'erreur.
            code: Code d'erreur.

        Returns:
            Message d'erreur.
        """
        return cls(
            type=MessageType.ERROR,
            payload={"code": code, "message": message},
        )

    @classmethod
    def create_system(cls, event: str, data: dict[str, Any] | None = None) -> WebSocketMessage:
        """Crée un message système.

        Args:
            event: Nom de l'événement.
            data: Données additionnelles.

        Returns:
            Message système.
        """
        return cls(
            type=MessageType.SYSTEM,
            channel=CHANNEL_SYSTEM_EVENTS,
            payload={"event": event, "data": data or {}},
        )


class WebSocketConfig(BaseModel):
    """Configuration du routeur WebSocket.

    Attributes:
        enabled: Activer le routeur WebSocket.
        heartbeat_interval_seconds: Intervalle de heartbeat.
        inactivity_timeout_seconds: Timeout d'inactivité.
        max_message_size_bytes: Taille max des messages.
        max_clients_per_user: Nombre max de clients par utilisateur.
        rate_limit_messages_per_second: Limite de messages par seconde.
        require_authentication: Exiger l'authentification.
        log_messages: Logger les messages.
        emit_events: Émettre des événements.
        allowed_origins: Origines autorisées (None = toutes).
    """

    enabled: bool = Field(default=True, description="Activer.")
    heartbeat_interval_seconds: int = Field(default=DEFAULT_HEARTBEAT_INTERVAL_SECONDS, ge=5, le=300, description="Intervalle heartbeat.")
    inactivity_timeout_seconds: int = Field(default=DEFAULT_INACTIVITY_TIMEOUT_SECONDS, ge=30, le=3600, description="Timeout inactivité.")
    max_message_size_bytes: int = Field(default=DEFAULT_MAX_MESSAGE_SIZE_BYTES, ge=1024, le=1048576, description="Taille max messages.")
    max_clients_per_user: int = Field(default=DEFAULT_MAX_CLIENTS_PER_USER, ge=1, le=50, description="Max clients/user.")
    rate_limit_messages_per_second: int = Field(default=DEFAULT_RATE_LIMIT_MESSAGES_PER_SECOND, ge=1, le=100, description="Limite messages/s.")
    require_authentication: bool = Field(default=True, description="Exiger auth.")
    log_messages: bool = Field(default=True, description="Logger messages.")
    emit_events: bool = Field(default=False, description="Émettre événements.")
    allowed_origins: list[str] | None = Field(default=None, description="Origines autorisées.")

    model_config = ConfigDict(extra="forbid")


class WebSocketClientInfo(BaseModel):
    """Informations sur un client WebSocket connecté.

    Attributes:
        client_id: ID unique du client.
        user_id: ID de l'utilisateur (si authentifié).
        connected_at: Timestamp de connexion.
        last_message_at: Timestamp du dernier message.
        subscribed_channels: Liste des channels abonnés.
        state: État du client.
        remote_address: Adresse distante.
        user_agent: User-Agent.
    """

    client_id: str = Field(..., description="ID client.")
    user_id: str | None = Field(default=None, description="ID utilisateur.")
    connected_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Connexion.")
    last_message_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Dernier message.")
    subscribed_channels: set[str] = Field(default_factory=set, description="Channels abonnés.")
    state: ClientState = Field(default=ClientState.CONNECTING, description="État.")
    remote_address: str = Field(default="", description="Adresse distante.")
    user_agent: str = Field(default="", description="User-Agent.")

    model_config = ConfigDict(extra="forbid")

    @property
    def is_authenticated(self) -> bool:
        """Vérifie si le client est authentifié."""
        return self.user_id is not None and self.state == ClientState.CONNECTED

    @property
    def connection_duration_seconds(self) -> float:
        """Durée de connexion en secondes."""
        return (datetime.now(UTC) - self.connected_at).total_seconds()


class WebSocketStats(BaseModel):
    """Statistiques WebSocket.

    Attributes:
        total_connections: Nombre total de connexions.
        active_connections: Nombre de connexions actives.
        authenticated_connections: Nombre de connexions authentifiées.
        total_messages_sent: Nombre total de messages envoyés.
        total_messages_received: Nombre total de messages reçus.
        total_bytes_sent: Nombre total de bytes envoyés.
        total_bytes_received: Nombre total de bytes reçus.
        messages_by_type: Compteur par type de message.
        messages_by_channel: Compteur par channel.
        started_at: Timestamp de début de collecte.
        last_activity_at: Timestamp de la dernière activité.
    """

    total_connections: int = Field(default=0, ge=0)
    active_connections: int = Field(default=0, ge=0)
    authenticated_connections: int = Field(default=0, ge=0)
    total_messages_sent: int = Field(default=0, ge=0)
    total_messages_received: int = Field(default=0, ge=0)
    total_bytes_sent: int = Field(default=0, ge=0)
    total_bytes_received: int = Field(default=0, ge=0)
    messages_by_type: dict[str, int] = Field(default_factory=dict)
    messages_by_channel: dict[str, int] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_activity_at: datetime | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# CLIENT WEBSOCKET — Représentation d'un client connecté
# ============================================================================


class WebSocketClient:
    """Représentation d'un client WebSocket connecté.

    Gère l'état du client, les abonnements, et la communication.
    """

    def __init__(
        self,
        websocket: Any,
        client_id: str | None = None,
    ) -> None:
        """Initialise le client.

        Args:
            websocket: Instance WebSocket FastAPI.
            client_id: ID unique du client (généré si None).
        """
        self._websocket = websocket
        self._client_id = client_id or str(uuid.uuid4())
        self._info = WebSocketClientInfo(
            client_id=self._client_id,
            remote_address=str(websocket.client.host) if websocket.client else "",
            user_agent=websocket.headers.get("User-Agent", ""),
        )
        self._send_lock = asyncio.Lock()
        self._rate_limit_tokens = 0.0
        self._rate_limit_last_refill = time.monotonic()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def client_id(self) -> str:
        """ID du client."""
        return self._client_id

    @property
    def info(self) -> WebSocketClientInfo:
        """Informations du client."""
        return self._info

    @property
    def user_id(self) -> str | None:
        """ID de l'utilisateur."""
        return self._info.user_id

    @property
    def is_authenticated(self) -> bool:
        """Vérifie si authentifié."""
        return self._info.is_authenticated

    @property
    def subscribed_channels(self) -> set[str]:
        """Channels abonnés."""
        return self._info.subscribed_channels

    @property
    def websocket(self) -> Any:
        """Instance WebSocket."""
        return self._websocket

    def set_authenticated(self, user_id: str) -> None:
        """Marque le client comme authentifié.

        Args:
            user_id: ID de l'utilisateur.
        """
        self._info.user_id = user_id
        self._info.state = ClientState.CONNECTED

    def subscribe(self, channel: str) -> bool:
        """Abonne le client à un channel.

        Args:
            channel: Nom du channel.

        Returns:
            True si l'abonnement a réussi.
        """
        if channel not in ALL_CHANNELS:
            return False
        self._info.subscribed_channels.add(channel)
        return True

    def unsubscribe(self, channel: str) -> bool:
        """Désabonne le client d'un channel.

        Args:
            channel: Nom du channel.

        Returns:
            True si le désabonnement a réussi.
        """
        if channel in self._info.subscribed_channels:
            self._info.subscribed_channels.discard(channel)
            return True
        return False

    def is_subscribed_to(self, channel: str) -> bool:
        """Vérifie si le client est abonné à un channel.

        Args:
            channel: Nom du channel.

        Returns:
            True si abonné.
        """
        return channel in self._info.subscribed_channels

    def check_rate_limit(self, max_per_second: int) -> bool:
        """Vérifie la limite de débit.

        Args:
            max_per_second: Limite de messages par seconde.

        Returns:
            True si autorisé.
        """
        now = time.monotonic()
        elapsed = now - self._rate_limit_last_refill

        # Recharger les jetons
        self._rate_limit_tokens = min(
            float(max_per_second),
            self._rate_limit_tokens + elapsed * max_per_second,
        )
        self._rate_limit_last_refill = now

        # Vérifier si on peut consommer un jeton
        if self._rate_limit_tokens >= 1.0:
            self._rate_limit_tokens -= 1.0
            return True
        return False

    async def send_message(self, message: WebSocketMessage) -> bool:
        """Envoie un message au client.

        Args:
            message: Message à envoyer.

        Returns:
            True si envoyé avec succès.
        """
        if self._closed:
            return False

        try:
            async with self._send_lock:
                if self._websocket.client_state != WebSocketState.CONNECTED:
                    return False

                await self._websocket.send_text(message.to_json())
                self._info.last_message_at = datetime.now(UTC)
                return True

        except Exception as e:
            logger.warning("Erreur lors de l'envoi au client {}: {}", self._client_id, e)
            return False

    async def send_json(self, data: dict[str, Any]) -> bool:
        """Envoie des données JSON au client.

        Args:
            data: Données à envoyer.

        Returns:
            True si envoyé avec succès.
        """
        message = WebSocketMessage(
            type=MessageType.MESSAGE,
            payload=data,
        )
        return await self.send_message(message)

    async def send_error(self, message: str, code: str = "error") -> bool:
        """Envoie un message d'erreur au client.

        Args:
            message: Message d'erreur.
            code: Code d'erreur.

        Returns:
            True si envoyé avec succès.
        """
        error_msg = WebSocketMessage.create_error(message, code)
        return await self.send_message(error_msg)

    async def send_pong(self) -> bool:
        """Envoie un pong en réponse à un ping.

        Returns:
            True si envoyé avec succès.
        """
        pong_msg = WebSocketMessage(
            type=MessageType.PONG,
            payload={"timestamp": datetime.now(UTC).isoformat()},
        )
        return await self.send_message(pong_msg)

    async def close(self, code: int = WS_CLOSE_NORMAL, reason: str = "") -> None:
        """Ferme la connexion WebSocket.

        Args:
            code: Code de fermeture.
            reason: Raison de la fermeture.
        """
        if self._closed:
            return

        self._closed = True
        self._info.state = ClientState.DISCONNECTING

        # Annuler la tâche de heartbeat
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        # Fermer la connexion WebSocket
        try:
            if self._websocket.client_state == WebSocketState.CONNECTED:
                await self._websocket.close(code=code, reason=reason)
        except Exception as e:
            logger.debug("Erreur lors de la fermeture de la connexion: {}", e)

        self._info.state = ClientState.DISCONNECTED

    def start_heartbeat(self, interval_seconds: int) -> None:
        """Démarre la tâche de heartbeat.

        Args:
            interval_seconds: Intervalle en secondes.
        """
        if self._heartbeat_task is not None:
            return

        async def _heartbeat_loop() -> None:
            try:
                while not self._closed:
                    await asyncio.sleep(interval_seconds)
                    if self._closed:
                        break
                    await self.send_pong()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug("Erreur heartbeat pour client {}: {}", self._client_id, e)

        self._heartbeat_task = asyncio.create_task(
            _heartbeat_loop(),
            name=f"ws_heartbeat_{self._client_id}",
        )


# ============================================================================
# CONNECTION MANAGER — Gestion des connexions
# ============================================================================


class ConnectionManager:
    """Gestionnaire des connexions WebSocket.

    Gère tous les clients connectés, les abonnements aux channels,
    et la diffusion des messages.
    """

    def __init__(self, config: WebSocketConfig) -> None:
        """Initialise le gestionnaire.

        Args:
            config: Configuration WebSocket.
        """
        self._config = config
        self._clients: dict[str, WebSocketClient] = {}  # client_id -> client
        self._clients_by_user: dict[str, set[str]] = defaultdict(set)  # user_id -> client_ids
        self._channel_subscribers: dict[str, set[str]] = defaultdict(set)  # channel -> client_ids
        self._stats = WebSocketStats()
        self._lock = asyncio.Lock()
        self._event_bus_subscription: Any = None

    async def start(self) -> None:
        """Démarre le gestionnaire."""
        # S'abonner aux événements EventBus
        if self._config.emit_events:
            await self._subscribe_to_events()
        logger.info("ConnectionManager démarré")

    async def stop(self) -> None:
        """Arrête le gestionnaire."""
        # Fermer toutes les connexions
        async with self._lock:
            for client in list(self._clients.values()):
                await client.close(WS_CLOSE_GOING_AWAY, "Server shutting down")
            self._clients.clear()
            self._clients_by_user.clear()
            self._channel_subscribers.clear()

        # Se désabonner des événements
        await self._unsubscribe_from_events()
        logger.info("ConnectionManager arrêté")

    async def _subscribe_to_events(self) -> None:
        """S'abonne aux événements EventBus."""
        try:
            event_bus = get_event_bus()

            # Événements de téléchargement
            event_bus.on(EventType.DOWNLOAD_TASK_PROGRESS, self._on_download_progress)
            event_bus.on(EventType.DOWNLOAD_TASK_COMPLETED, self._on_download_completed)
            event_bus.on(EventType.DOWNLOAD_TASK_FAILED, self._on_download_failed)

            # Événements de bibliothèque
            event_bus.on(EventType.LIBRARY_MANGA_ADDED, self._on_library_changed)
            event_bus.on(EventType.LIBRARY_MANGA_REMOVED, self._on_library_changed)

            # Événements système
            event_bus.on(EventType.SESSION_STARTED, self._on_system_event)
            event_bus.on(EventType.SESSION_STOPPED, self._on_system_event)

            logger.debug("Souscrit aux événements EventBus")
        except Exception as e:
            logger.warning("Impossible de s'abonner aux événements: {}", e)

    async def _unsubscribe_from_events(self) -> None:
        """Se désabonne des événements EventBus."""
        # TODO: Implémenter la désinscription propre
        pass

    async def _on_download_progress(self, event: Any) -> None:
        """Gère l'événement de progression de téléchargement."""
        await self.broadcast_to_channel(
            CHANNEL_DOWNLOADS_PROGRESS,
            event.payload,
        )

    async def _on_download_completed(self, event: Any) -> None:
        """Gère l'événement de téléchargement terminé."""
        await self.broadcast_to_channel(
            CHANNEL_DOWNLOADS_COMPLETED,
            event.payload,
        )

    async def _on_download_failed(self, event: Any) -> None:
        """Gère l'événement de téléchargement échoué."""
        await self.broadcast_to_channel(
            CHANNEL_DOWNLOADS_FAILED,
            event.payload,
        )

    async def _on_library_changed(self, event: Any) -> None:
        """Gère l'événement de changement de bibliothèque."""
        await self.broadcast_to_channel(
            CHANNEL_LIBRARY_CHANGES,
            event.payload,
        )

    async def _on_system_event(self, event: Any) -> None:
        """Gère un événement système."""
        await self.broadcast_to_channel(
            CHANNEL_SYSTEM_EVENTS,
            {"event": event.type.value, "payload": event.payload},
        )

    async def connect(self, websocket: Any) -> WebSocketClient:
        """Accepte une nouvelle connexion WebSocket.

        Args:
            websocket: Instance WebSocket FastAPI.

        Returns:
            Instance de WebSocketClient.
        """
        await websocket.accept()

        client = WebSocketClient(websocket)

        async with self._lock:
            self._clients[client.client_id] = client
            self._stats = self._stats.model_copy(update={
                "total_connections": self._stats.total_connections + 1,
                "active_connections": self._stats.active_connections + 1,
                "last_activity_at": datetime.now(UTC),
            })

        logger.info(
            "Client WebSocket connecté: id={}, remote={}",
            client.client_id,
            client.info.remote_address,
        )

        # Envoyer un message de bienvenue
        welcome_msg = WebSocketMessage(
            type=MessageType.SYSTEM,
            payload={
                "event": "connected",
                "data": {
                    "client_id": client.client_id,
                    "server": APP_NAME,
                    "available_channels": list(ALL_CHANNELS),
                },
            },
        )
        await client.send_message(welcome_msg)

        return client

    async def disconnect(self, client: WebSocketClient) -> None:
        """Déconnecte un client.

        Args:
            client: Client à déconnecter.
        """
        async with self._lock:
            # Retirer des channels
            for channel in list(client.subscribed_channels):
                self._channel_subscribers[channel].discard(client.client_id)
                if not self._channel_subscribers[channel]:
                    del self._channel_subscribers[channel]

            # Retirer des clients par utilisateur
            if client.user_id:
                self._clients_by_user[client.user_id].discard(client.client_id)
                if not self._clients_by_user[client.user_id]:
                    del self._clients_by_user[client.user_id]

            # Retirer de la liste des clients
            self._clients.pop(client.client_id, None)

            # Mettre à jour les stats
            new_stats = self._stats.model_dump()
            new_stats["active_connections"] = max(0, self._stats.active_connections - 1)
            if client.is_authenticated:
                new_stats["authenticated_connections"] = max(0, self._stats.authenticated_connections - 1)
            new_stats["last_activity_at"] = datetime.now(UTC)
            self._stats = WebSocketStats(**new_stats)

        # Fermer la connexion
        await client.close()

        logger.info(
            "Client WebSocket déconnecté: id={}, user={}",
            client.client_id,
            client.user_id or "anonymous",
        )

    async def authenticate(
        self,
        client: WebSocketClient,
        user_id: str,
    ) -> bool:
        """Authentifie un client.

        Args:
            client: Client à authentifier.
            user_id: ID de l'utilisateur.

        Returns:
            True si authentifié avec succès.
        """
        async with self._lock:
            # Vérifier la limite de clients par utilisateur
            if len(self._clients_by_user[user_id]) >= self._config.max_clients_per_user:
                logger.warning(
                    "Limite de clients atteinte pour user {}: {}/{}",
                    user_id,
                    len(self._clients_by_user[user_id]),
                    self._config.max_clients_per_user,
                )
                return False

            # Marquer comme authentifié
            client.set_authenticated(user_id)
            self._clients_by_user[user_id].add(client.client_id)

            # Mettre à jour les stats
            new_stats = self._stats.model_dump()
            new_stats["authenticated_connections"] = self._stats.authenticated_connections + 1
            self._stats = WebSocketStats(**new_stats)

        logger.info(
            "Client WebSocket authentifié: id={}, user={}",
            client.client_id,
            user_id,
        )

        # Envoyer un message de confirmation
        auth_msg = WebSocketMessage(
            type=MessageType.SYSTEM,
            payload={
                "event": "authenticated",
                "data": {"user_id": user_id},
            },
        )
        await client.send_message(auth_msg)

        return True

    async def subscribe_client(self, client: WebSocketClient, channel: str) -> bool:
        """Abonne un client à un channel.

        Args:
            client: Client à abonner.
            channel: Channel cible.

        Returns:
            True si l'abonnement a réussi.
        """
        if not client.subscribe(channel):
            return False

        async with self._lock:
            self._channel_subscribers[channel].add(client.client_id)

        logger.debug(
            "Client {} abonné au channel {}",
            client.client_id,
            channel,
        )

        # Envoyer une confirmation
        confirm_msg = WebSocketMessage(
            type=MessageType.SYSTEM,
            payload={
                "event": "subscribed",
                "data": {"channel": channel},
            },
        )
        await client.send_message(confirm_msg)

        return True

    async def unsubscribe_client(self, client: WebSocketClient, channel: str) -> bool:
        """Désabonne un client d'un channel.

        Args:
            client: Client à désabonner.
            channel: Channel cible.

        Returns:
            True si le désabonnement a réussi.
        """
        if not client.unsubscribe(channel):
            return False

        async with self._lock:
            self._channel_subscribers[channel].discard(client.client_id)
            if not self._channel_subscribers[channel]:
                del self._channel_subscribers[channel]

        logger.debug(
            "Client {} désabonné du channel {}",
            client.client_id,
            channel,
        )

        # Envoyer une confirmation
        confirm_msg = WebSocketMessage(
            type=MessageType.SYSTEM,
            payload={
                "event": "unsubscribed",
                "data": {"channel": channel},
            },
        )
        await client.send_message(confirm_msg)

        return True

    async def broadcast_to_channel(
        self,
        channel: str,
        payload: dict[str, Any],
        *,
        exclude_client_id: str | None = None,
        require_auth: bool = False,
    ) -> int:
        """Diffuse un message à tous les abonnés d'un channel.

        Args:
            channel: Channel cible.
            payload: Contenu du message.
            exclude_client_id: ID du client à exclure.
            require_auth: Exiger l'authentification.

        Returns:
            Nombre de clients à qui le message a été envoyé.
        """
        if channel not in ALL_CHANNELS:
            logger.warning("Tentative de diffusion sur channel inconnu: {}", channel)
            return 0

        message = WebSocketMessage(
            type=MessageType.MESSAGE,
            channel=channel,
            payload=payload,
        )

        async with self._lock:
            subscriber_ids = self._channel_subscribers.get(channel, set()).copy()

        sent_count = 0
        for client_id in subscriber_ids:
            if client_id == exclude_client_id:
                continue

            client = self._clients.get(client_id)
            if client is None:
                continue

            if require_auth and not client.is_authenticated:
                continue

            if await client.send_message(message):
                sent_count += 1

        # Mettre à jour les stats
        async with self._lock:
            new_stats = self._stats.model_dump()
            new_stats["total_messages_sent"] += sent_count
            new_stats["messages_by_channel"][channel] = (
                new_stats["messages_by_channel"].get(channel, 0) + sent_count
            )
            new_stats["last_activity_at"] = datetime.now(UTC)
            self._stats = WebSocketStats(**new_stats)

        return sent_count

    async def send_to_user(
        self,
        user_id: str,
        payload: dict[str, Any],
        *,
        channel: str | None = None,
    ) -> int:
        """Envoie un message à tous les clients d'un utilisateur.

        Args:
            user_id: ID de l'utilisateur.
            payload: Contenu du message.
            channel: Channel cible (optionnel).

        Returns:
            Nombre de clients à qui le message a été envoyé.
        """
        message = WebSocketMessage(
            type=MessageType.MESSAGE,
            channel=channel,
            payload=payload,
        )

        async with self._lock:
            client_ids = self._clients_by_user.get(user_id, set()).copy()

        sent_count = 0
        for client_id in client_ids:
            client = self._clients.get(client_id)
            if client is None:
                continue

            if await client.send_message(message):
                sent_count += 1

        return sent_count

    async def send_to_client(
        self,
        client_id: str,
        payload: dict[str, Any],
        *,
        channel: str | None = None,
    ) -> bool:
        """Envoie un message à un client spécifique.

        Args:
            client_id: ID du client.
            payload: Contenu du message.
            channel: Channel cible (optionnel).

        Returns:
            True si envoyé avec succès.
        """
        client = self._clients.get(client_id)
        if client is None:
            return False

        message = WebSocketMessage(
            type=MessageType.MESSAGE,
            channel=channel,
            payload=payload,
        )

        return await client.send_message(message)

    async def broadcast_all(
        self,
        payload: dict[str, Any],
        *,
        channel: str | None = None,
        require_auth: bool = False,
    ) -> int:
        """Diffuse un message à tous les clients connectés.

        Args:
            payload: Contenu du message.
            channel: Channel cible (optionnel).
            require_auth: Exiger l'authentification.

        Returns:
            Nombre de clients à qui le message a été envoyé.
        """
        message = WebSocketMessage(
            type=MessageType.MESSAGE,
            channel=channel,
            payload=payload,
        )

        async with self._lock:
            clients = list(self._clients.values())

        sent_count = 0
        for client in clients:
            if require_auth and not client.is_authenticated:
                continue

            if await client.send_message(message):
                sent_count += 1

        return sent_count

    def get_client(self, client_id: str) -> WebSocketClient | None:
        """Récupère un client par son ID.

        Args:
            client_id: ID du client.

        Returns:
            Instance de WebSocketClient ou None.
        """
        return self._clients.get(client_id)

    def get_clients_by_user(self, user_id: str) -> list[WebSocketClient]:
        """Récupère tous les clients d'un utilisateur.

        Args:
            user_id: ID de l'utilisateur.

        Returns:
            Liste de WebSocketClient.
        """
        client_ids = self._clients_by_user.get(user_id, set())
        return [self._clients[cid] for cid in client_ids if cid in self._clients]

    def get_subscribers(self, channel: str) -> list[WebSocketClient]:
        """Récupère tous les abonnés d'un channel.

        Args:
            channel: Nom du channel.

        Returns:
            Liste de WebSocketClient.
        """
        client_ids = self._channel_subscribers.get(channel, set())
        return [self._clients[cid] for cid in client_ids if cid in self._clients]

    async def get_stats(self) -> WebSocketStats:
        """Récupère les statistiques.

        Returns:
            Statistiques actuelles.
        """
        async with self._lock:
            return self._stats

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        async with self._lock:
            self._stats = WebSocketStats(
                active_connections=self._stats.active_connections,
                authenticated_connections=self._stats.authenticated_connections,
            )

    @property
    def active_connections(self) -> int:
        """Nombre de connexions actives."""
        return len(self._clients)

    @property
    def authenticated_connections(self) -> int:
        """Nombre de connexions authentifiées."""
        return sum(1 for c in self._clients.values() if c.is_authenticated)


# ============================================================================
# AUTH HANDLER — Authentification WebSocket
# ============================================================================


class WebSocketAuthHandler:
    """Gestionnaire d'authentification WebSocket.

    Supporte l'authentification via :
        - Token JWT en query parameter
        - API key en query parameter
        - Premier message d'authentification
    """

    def __init__(self, config: WebSocketConfig) -> None:
        """Initialise le gestionnaire.

        Args:
            config: Configuration WebSocket.
        """
        self._config = config

    async def authenticate_from_query(self, websocket: Any) -> tuple[bool, str | None]:
        """Authentifie depuis les query parameters.

        Args:
            websocket: Instance WebSocket.

        Returns:
            Tuple (success, user_id).
        """
        # Vérifier le token JWT
        token = websocket.query_params.get("token")
        if token:
            try:
                from nexusdl.interfaces.web.backend.middleware.auth import (
                    TokenManager,
                    TokenType,
                )
                from nexusdl.core.config import get_config

                config = get_config()
                token_manager = TokenManager(config.auth)
                payload = token_manager.validate_token(token, TokenType.ACCESS)
                return True, payload.sub

            except Exception as e:
                logger.debug("Auth JWT échouée: {}", e)

        # Vérifier l'API key
        api_key = websocket.query_params.get("api_key")
        if api_key:
            try:
                # TODO: Valider l'API key
                # Pour l'instant, on rejette
                return False, None

            except Exception as e:
                logger.debug("Auth API key échouée: {}", e)

        return False, None

    async def authenticate_from_message(self, message: WebSocketMessage) -> tuple[bool, str | None]:
        """Authentifie depuis un message.

        Args:
            message: Message d'authentification.

        Returns:
            Tuple (success, user_id).
        """
        if message.type != MessageType.AUTH:
            return False, None

        # Vérifier le token JWT
        token = message.payload.get("token")
        if token:
            try:
                from nexusdl.interfaces.web.backend.middleware.auth import (
                    TokenManager,
                    TokenType,
                )
                from nexusdl.core.config import get_config

                config = get_config()
                token_manager = TokenManager(config.auth)
                payload = token_manager.validate_token(token, TokenType.ACCESS)
                return True, payload.sub

            except Exception as e:
                logger.debug("Auth JWT échouée: {}", e)
                return False, None

        # Vérifier l'API key
        api_key = message.payload.get("api_key")
        if api_key:
            try:
                # TODO: Valider l'API key
                return False, None

            except Exception as e:
                logger.debug("Auth API key échouée: {}", e)
                return False, None

        return False, None


# ============================================================================
# ROUTEUR WEBSOCKET — Endpoints FastAPI
# ============================================================================


if FASTAPI_AVAILABLE:

    # Instance globale du gestionnaire de connexions
    _connection_manager: ConnectionManager | None = None
    _auth_handler: WebSocketAuthHandler | None = None

    def get_connection_manager() -> ConnectionManager | None:
        """Retourne l'instance globale du ConnectionManager."""
        return _connection_manager

    def set_connection_manager(manager: ConnectionManager) -> None:
        """Définit l'instance globale du ConnectionManager."""
        global _connection_manager
        _connection_manager = manager

    def get_auth_handler() -> WebSocketAuthHandler | None:
        """Retourne l'instance globale du WebSocketAuthHandler."""
        return _auth_handler

    def set_auth_handler(handler: WebSocketAuthHandler) -> None:
        """Définit l'instance globale du WebSocketAuthHandler."""
        global _auth_handler
        _auth_handler = handler

    # Créer le routeur
    ws_router = APIRouter(prefix="/ws", tags=["websocket"])

    @ws_router.websocket("/")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        """Endpoint WebSocket principal.

        Gère la connexion, l'authentification, et la communication
        avec un client WebSocket.

        Args:
            websocket: Instance WebSocket FastAPI.
        """
        manager = get_connection_manager()
        auth_handler = get_auth_handler()

        if manager is None or auth_handler is None:
            await websocket.close(code=WS_CLOSE_INTERNAL_ERROR, reason="Server not initialized")
            return

        # Accepter la connexion
        client = await manager.connect(websocket)

        try:
            # Essayer l'authentification via query parameters
            if manager._config.require_authentication:
                success, user_id = await auth_handler.authenticate_from_query(websocket)
                if success and user_id:
                    await manager.authenticate(client, user_id)
                else:
                    # Attendre un message d'authentification
                    client._info.state = ClientState.AUTHENTICATING
                    auth_timeout = asyncio.create_task(asyncio.sleep(30))
                    auth_message_task = asyncio.create_task(websocket.receive_text())

                    done, pending = await asyncio.wait(
                        [auth_timeout, auth_message_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    for task in pending:
                        task.cancel()

                    if auth_timeout in done:
                        await client.close(WS_CLOSE_AUTH_REQUIRED, "Authentication timeout")
                        return

                    # Traiter le message d'authentification
                    try:
                        message_text = auth_message_task.result()
                        message = WebSocketMessage.from_json(message_text)
                        success, user_id = await auth_handler.authenticate_from_message(message)

                        if not success or not user_id:
                            await client.send_error("Authentication failed", "auth_failed")
                            await client.close(WS_CLOSE_AUTH_FAILED, "Authentication failed")
                            return

                        await manager.authenticate(client, user_id)

                    except Exception as e:
                        await client.send_error(f"Invalid auth message: {e}", "invalid_auth")
                        await client.close(WS_CLOSE_INVALID_PAYLOAD, "Invalid auth message")
                        return

            # Démarrer le heartbeat
            client.start_heartbeat(manager._config.heartbeat_interval_seconds)

            # Boucle principale de réception des messages
            while True:
                try:
                    message_text = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=manager._config.inactivity_timeout_seconds,
                    )

                    # Vérifier la taille du message
                    if len(message_text) > manager._config.max_message_size_bytes:
                        await client.send_error("Message too big", "message_too_big")
                        await client.close(WS_CLOSE_MESSAGE_TOO_BIG, "Message too big")
                        break

                    # Vérifier le rate limit
                    if not client.check_rate_limit(manager._config.rate_limit_messages_per_second):
                        await client.send_error("Rate limit exceeded", "rate_limited")
                        await client.close(WS_CLOSE_RATE_LIMITED, "Rate limit exceeded")
                        break

                    # Parser le message
                    try:
                        message = WebSocketMessage.from_json(message_text)
                    except Exception as e:
                        await client.send_error(f"Invalid message format: {e}", "invalid_format")
                        continue

                    # Mettre à jour les stats
                    async with manager._lock:
                        new_stats = manager._stats.model_dump()
                        new_stats["total_messages_received"] += 1
                        new_stats["total_bytes_received"] += len(message_text)
                        new_stats["messages_by_type"][message.type.value] = (
                            new_stats["messages_by_type"].get(message.type.value, 0) + 1
                        )
                        new_stats["last_activity_at"] = datetime.now(UTC)
                        manager._stats = WebSocketStats(**new_stats)

                    # Logger le message
                    if manager._config.log_messages:
                        logger.debug(
                            "Message reçu: client={}, type={}, channel={}",
                            client.client_id,
                            message.type.value,
                            message.channel,
                        )

                    # Traiter le message selon son type
                    await _handle_message(client, message, manager)

                except asyncio.TimeoutError:
                    # Timeout d'inactivité
                    await client.send_error("Inactivity timeout", "timeout")
                    await client.close(WS_CLOSE_GOING_AWAY, "Inactivity timeout")
                    break

                except WebSocketDisconnect:
                    # Déconnexion normale
                    break

                except Exception as e:
                    logger.error("Erreur lors du traitement du message: {}", e)
                    await client.send_error(f"Internal error: {e}", "internal_error")
                    break

        except Exception as e:
            logger.error("Erreur dans le endpoint WebSocket: {}", e)

        finally:
            # Déconnecter le client
            await manager.disconnect(client)

    async def _handle_message(
        client: WebSocketClient,
        message: WebSocketMessage,
        manager: ConnectionManager,
    ) -> None:
        """Traite un message WebSocket.

        Args:
            client: Client émetteur.
            message: Message reçu.
            manager: Gestionnaire de connexions.
        """
        if message.type == MessageType.PING:
            # Répondre au ping
            await client.send_pong()

        elif message.type == MessageType.SUBSCRIBE:
            # Abonnement à un channel
            channel = message.payload.get("channel")
            if not channel:
                await client.send_error("Missing channel", "missing_channel")
                return

            if channel not in ALL_CHANNELS:
                await client.send_error(f"Unknown channel: {channel}", "unknown_channel")
                return

            success = await manager.subscribe_client(client, channel)
            if not success:
                await client.send_error("Subscription failed", "subscription_failed")

        elif message.type == MessageType.UNSUBSCRIBE:
            # Désabonnement d'un channel
            channel = message.payload.get("channel")
            if not channel:
                await client.send_error("Missing channel", "missing_channel")
                return

            success = await manager.unsubscribe_client(client, channel)
            if not success:
                await client.send_error("Unsubscription failed", "unsubscription_failed")

        elif message.type == MessageType.MESSAGE:
            # Message générique (pour l'instant, on ne fait rien)
            # TODO: Implémenter le routing des messages entre clients
            pass

        elif message.type == MessageType.AUTH:
            # Message d'authentification (déjà traité lors de la connexion)
            await client.send_error("Already authenticated", "already_authenticated")

        else:
            # Type de message non supporté
            await client.send_error(f"Unsupported message type: {message.type}", "unsupported_type")

    # ========================================================================
    # FONCTIONS HELPERS PUBLIQUES
    # ========================================================================

    async def broadcast_to_channel(
        channel: str,
        payload: dict[str, Any],
        *,
        exclude_client_id: str | None = None,
    ) -> int:
        """Diffuse un message à tous les abonnés d'un channel.

        Fonction helper pour usage externe.

        Args:
            channel: Channel cible.
            payload: Contenu du message.
            exclude_client_id: ID du client à exclure.

        Returns:
            Nombre de clients à qui le message a été envoyé.
        """
        manager = get_connection_manager()
        if manager is None:
            return 0
        return await manager.broadcast_to_channel(
            channel,
            payload,
            exclude_client_id=exclude_client_id,
        )

    async def send_to_user(
        user_id: str,
        payload: dict[str, Any],
        *,
        channel: str | None = None,
    ) -> int:
        """Envoie un message à tous les clients d'un utilisateur.

        Args:
            user_id: ID de l'utilisateur.
            payload: Contenu du message.
            channel: Channel cible (optionnel).

        Returns:
            Nombre de clients à qui le message a été envoyé.
        """
        manager = get_connection_manager()
        if manager is None:
            return 0
        return await manager.send_to_user(user_id, payload, channel=channel)

    async def send_notification(
        message: str,
        *,
        level: str = "info",
        user_id: str | None = None,
    ) -> int:
        """Envoie une notification aux clients.

        Args:
            message: Message de notification.
            level: Niveau (info, warning, error).
            user_id: ID de l'utilisateur (None = tous).

        Returns:
            Nombre de clients à qui la notification a été envoyée.
        """
        channel_map = {
            "info": CHANNEL_NOTIFICATIONS_INFO,
            "warning": CHANNEL_NOTIFICATIONS_WARNING,
            "error": CHANNEL_NOTIFICATIONS_ERROR,
        }
        channel = channel_map.get(level, CHANNEL_NOTIFICATIONS_INFO)

        payload = {
            "message": message,
            "level": level,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        manager = get_connection_manager()
        if manager is None:
            return 0

        if user_id:
            return await manager.send_to_user(user_id, payload, channel=channel)
        else:
            return await manager.broadcast_to_channel(channel, payload)

    async def get_ws_stats() -> WebSocketStats:
        """Récupère les statistiques WebSocket.

        Returns:
            Statistiques actuelles.
        """
        manager = get_connection_manager()
        if manager is None:
            return WebSocketStats()
        return await manager.get_stats()

    def init_websocket_router(config: WebSocketConfig | None = None) -> APIRouter:
        """Initialise le routeur WebSocket.

        Args:
            config: Configuration WebSocket.

        Returns:
            Instance du routeur.
        """
        config = config or WebSocketConfig()

        # Créer le gestionnaire de connexions
        manager = ConnectionManager(config)
        set_connection_manager(manager)

        # Créer le gestionnaire d'authentification
        auth_handler = WebSocketAuthHandler(config)
        set_auth_handler(auth_handler)

        logger.info("Routeur WebSocket initialisé")

        return ws_router


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_HEARTBEAT_INTERVAL_SECONDS",
    "DEFAULT_INACTIVITY_TIMEOUT_SECONDS",
    "DEFAULT_MAX_MESSAGE_SIZE_BYTES",
    "DEFAULT_MAX_CLIENTS_PER_USER",
    "DEFAULT_RATE_LIMIT_MESSAGES_PER_SECOND",
    "ALL_CHANNELS",
    "CHANNEL_DOWNLOADS_PROGRESS",
    "CHANNEL_DOWNLOADS_COMPLETED",
    "CHANNEL_DOWNLOADS_FAILED",
    "CHANNEL_NOTIFICATIONS_INFO",
    "CHANNEL_NOTIFICATIONS_WARNING",
    "CHANNEL_NOTIFICATIONS_ERROR",
    "CHANNEL_LOGS_STREAM",
    "CHANNEL_SEARCH_RESULTS",
    "CHANNEL_LIBRARY_CHANGES",
    "CHANNEL_SYSTEM_STATUS",
    "CHANNEL_SYSTEM_EVENTS",
    # Codes de fermeture WebSocket
    "WS_CLOSE_NORMAL",
    "WS_CLOSE_GOING_AWAY",
    "WS_CLOSE_AUTH_REQUIRED",
    "WS_CLOSE_AUTH_FAILED",
    "WS_CLOSE_RATE_LIMITED",
    "WS_CLOSE_CHANNEL_NOT_FOUND",
    "WS_CLOSE_PERMISSION_DENIED",
    # Exceptions
    "WebSocketError",
    "WebSocketAuthError",
    "WebSocketChannelError",
    "WebSocketMessageError",
    "WebSocketRateLimitError",
    # Enums
    "MessageType",
    "ClientState",
    # Modèles
    "WebSocketMessage",
    "WebSocketConfig",
    "WebSocketClientInfo",
    "WebSocketStats",
    # Classes
    "WebSocketClient",
    "ConnectionManager",
    "WebSocketAuthHandler",
    # Routeur
    "ws_router" if FASTAPI_AVAILABLE else None,
    # Instance globale
    "get_connection_manager",
    "set_connection_manager",
    "get_auth_handler",
    "set_auth_handler",
    # Fonctions helpers
    "init_websocket_router" if FASTAPI_AVAILABLE else None,
    "broadcast_to_channel",
    "send_to_user",
    "send_notification",
    "get_ws_stats",
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
