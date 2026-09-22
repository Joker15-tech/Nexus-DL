"""File de tâches prioritaire async pour le système de téléchargement.

Ce module fournit une file d'attente asynchrone qui ordonnance les tâches
de téléchargement (`DownloadTask`) selon deux critères :

1. **Priorité** (URGENT > HIGH > NORMAL > LOW) : les tâches urgentes
   passent devant les tâches normales.
2. **Timestamp d'insertion** (FIFO) : à priorité égale, la tâche soumise
   en premier est traitée en premier.

La file supporte les opérations suivantes sur les tâches en attente :
    - **Annulation** : retire une tâche de la queue (lazy deletion via set).
    - **Pause** : retire une tâche de la queue et la stocke dans un buffer
      séparé jusqu'à reprise.
    - **Reprise** : réinjecte une tâche pausée dans la queue.
    - **Annulation globale** : vide la queue de toutes les tâches en attente.

Implémentation :
    La file utilise `asyncio.PriorityQueue` en interne. Chaque élément est
    un tuple `(priority_value, monotonic_timestamp, sequence_number, task)`
    pour garantir un tri stable et déterministe. La suppression est "lazy" :
    les tâches annulées sont marquées dans un set et filtrées au moment du
    `get()`, évitant ainsi la reconstruction coûteuse de la heap.

Exemple d'utilisation :
    >>> queue = DownloadQueue()
    >>> await queue.start()
    >>>
    >>> # Soumettre des tâches avec priorités différentes
    >>> queue.put(task_normal)    # Priority.NORMAL
    >>> queue.put(task_urgent)    # Priority.URGENT → passera en premier
    >>>
    >>> # Récupérer la prochaine tâche (bloquant async)
    >>> task = await queue.get()
    >>> print(task.priority)
    Priority.URGENT
    >>>
    >>> # Annuler une tâche en attente
    >>> queue.cancel(task_id)
    >>>
    >>> await queue.stop()
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Final, Self
from uuid import UUID

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.models.download import DownloadStatus, DownloadTask, Priority


# ============================================================================
# EXCEPTIONS
# ============================================================================


class QueueError(NexusDLError):
    """Exception de base pour les erreurs de la file de téléchargement."""


class QueueNotStartedError(QueueError):
    """Exception levée lorsqu'on utilise la queue avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "DownloadQueue must be started before use. Call await queue.start()"
        )


class TaskNotInQueueError(QueueError):
    """Exception levée lorsqu'une opération cible une tâche absente de la queue."""

    def __init__(self, task_id: UUID) -> None:
        super().__init__(f"Tâche {task_id} introuvable dans la queue")
        self.task_id = task_id


# ============================================================================
# ENUMS INTERNES
# ============================================================================


class _PriorityValue(IntEnum):
    """Valeurs numériques internes pour le tri de la PriorityQueue.

    Plus la valeur est BASSE, plus la priorité est HAUTE (min-heap).
    """

    URGENT = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


# Mapping depuis l'enum public `Priority` vers les valeurs internes
_PRIORITY_MAP: Final[dict[Priority, _PriorityValue]] = {
    Priority.URGENT: _PriorityValue.URGENT,
    Priority.HIGH: _PriorityValue.HIGH,
    Priority.NORMAL: _PriorityValue.NORMAL,
    Priority.LOW: _PriorityValue.LOW,
}


# ============================================================================
# MODÈLES INTERNES
# ============================================================================


@dataclass(order=True)
class _QueueEntry:
    """Entrée interne de la file prioritaire.

    L'ordre de tri est déterminé par les champs marqués `compare=True`
    (par défaut tous les champs dans l'ordre de déclaration). Le champ
    `task` est exclu de la comparaison car `DownloadTask` n'est pas
    ordonnable.

    Tri : priority_value ASC → timestamp ASC → sequence ASC
    """

    priority_value: int = field(compare=True)
    timestamp: float = field(compare=True)
    sequence: int = field(compare=True)
    task: DownloadTask = field(compare=False, repr=False)


class QueueStats(BaseModel):
    """Statistiques instantanées de la file de téléchargement."""

    pending: int = Field(default=0, ge=0, description="Tâches en attente dans la queue.")
    paused: int = Field(default=0, ge=0, description="Tâches en pause (buffer séparé).")
    cancelled: int = Field(default=0, ge=0, description="Tâches annulées (lazy deletion).")
    total_enqueued: int = Field(
        default=0, ge=0, description="Total de tâches ayant transité par la queue."
    )
    total_dequeued: int = Field(
        default=0, ge=0, description="Total de tâches récupérées via get()."
    )
    total_cancelled: int = Field(
        default=0, ge=0, description="Total de tâches annulées depuis le démarrage."
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# CLASSE PRINCIPALE
# ============================================================================


class DownloadQueue:
    """File de tâches prioritaire async pour le DownloadManager.

    Ordonnance les `DownloadTask` par priorité (URGENT > HIGH > NORMAL > LOW)
    puis par ordre d'arrivée (FIFO). Supporte l'annulation, la pause et la
    reprise de tâches individuelles.

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Elle n'est PAS thread-safe pour un usage multi-thread.
        Tous les accès concurrents doivent passer par des coroutines.

    Lifecycle :
        >>> queue = DownloadQueue()
        >>> await queue.start()
        >>> # ... put / get / cancel / pause / resume ...
        >>> await queue.stop()
    """

    def __init__(self, *, max_size: int = 0) -> None:
        """Initialise la file de téléchargement.

        Args:
            max_size: Taille maximum de la queue (0 = illimité).
                      Si atteint, `put()` bloquera jusqu'à ce qu'une place
                      se libère.
        """
        if max_size < 0:
            raise ValueError(f"max_size must be non-negative, got {max_size}")

        self._max_size = max_size

        # Queue interne asyncio
        self._queue: asyncio.PriorityQueue[_QueueEntry] | None = None

        # Compteur de séquence pour le tri stable (FIFO à priorité égale)
        self._sequence: int = 0

        # Set des IDs de tâches annulées (lazy deletion)
        self._cancelled_ids: set[UUID] = set()

        # Buffer des tâches en pause (task_id → QueueEntry)
        self._paused: dict[UUID, _QueueEntry] = {}

        # Set des IDs de tâches actuellement dans la queue (pour lookup O(1))
        self._enqueued_ids: set[UUID] = set()

        # Statistiques
        self._total_enqueued: int = 0
        self._total_dequeued: int = 0
        self._total_cancelled: int = 0

        # État
        self._started: bool = False
        self._lock = asyncio.Lock()

        self._logger = logger.bind(module="download_queue")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Initialise la file et la rend opérationnelle.

        Raises:
            QueueError: Si la queue est déjà démarrée.
        """
        if self._started:
            self._logger.warning("DownloadQueue déjà démarrée, ignore")
            return

        self._queue = asyncio.PriorityQueue(maxsize=self._max_size)
        self._started = True
        self._logger.info("DownloadQueue démarrée (max_size={})", self._max_size or "∞")

    async def stop(self) -> None:
        """Vide et ferme la file.

        Les tâches en attente sont marquées comme annulées. Les tâches
        en pause sont conservées dans le buffer (peuvent être reprises
        après redémarrage si la persistance est implémentée).
        """
        if not self._started:
            return

        # Marquer toutes les tâches en attente comme annulées
        assert self._queue is not None
        while not self._queue.empty():
            try:
                entry = self._queue.get_nowait()
                self._cancelled_ids.add(entry.task.id)
                self._enqueued_ids.discard(entry.task.id)
            except asyncio.QueueEmpty:
                break

        self._started = False
        self._queue = None
        self._logger.info("DownloadQueue arrêtée")

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
        """Indique si la queue est démarrée et opérationnelle."""
        return self._started

    @property
    def size(self) -> int:
        """Nombre de tâches actuellement en attente dans la queue.

        Note : ce nombre inclut les tâches annulées non encore filtrées
        (lazy deletion). Le nombre effectif peut être inférieur.
        """
        if self._queue is None:
            return 0
        return self._queue.qsize()

    @property
    def effective_size(self) -> int:
        """Nombre de tâches réellement en attente (excluant les annulées).

        Plus coûteux que `size` car nécessite un filtrage, mais précis.
        """
        return len(self._enqueued_ids - self._cancelled_ids)

    @property
    def paused_count(self) -> int:
        """Nombre de tâches en pause."""
        return len(self._paused)

    @property
    def is_empty(self) -> bool:
        """Indique si la queue est vide (sans compter les annulées)."""
        return self.effective_size == 0

    # ------------------------------------------------------------------------
    # API publique — Insertion
    # ------------------------------------------------------------------------

    def put(self, task: DownloadTask) -> None:
        """Ajoute une tâche à la file selon sa priorité.

        La tâche est insérée dans la min-heap avec un tuple de tri
        `(priority_value, timestamp, sequence)` garantissant :
            1. Les tâches URGENT passent en premier
            2. À priorité égale, FIFO (premier arrivé, premier servi)

        Args:
            task: Tâche à ajouter. Son champ `priority` détermine l'ordre.

        Raises:
            QueueNotStartedError: Si la queue n'est pas démarrée.
            QueueError: Si la tâche est déjà dans la queue.
        """
        self._ensure_started()
        assert self._queue is not None

        if task.id in self._enqueued_ids and task.id not in self._cancelled_ids:
            raise QueueError(f"Tâche {task.id} déjà dans la queue")

        # Si la tâche était annulée, la retirer du set (réinsertion)
        self._cancelled_ids.discard(task.id)

        # Calculer la valeur de priorité
        priority_value = _PRIORITY_MAP.get(task.priority, _PriorityValue.NORMAL)

        # Créer l'entrée
        entry = _QueueEntry(
            priority_value=int(priority_value),
            timestamp=time.monotonic(),
            sequence=self._sequence,
            task=task,
        )
        self._sequence += 1

        # Insérer dans la heap
        self._queue.put_nowait(entry)
        self._enqueued_ids.add(task.id)
        self._total_enqueued += 1

        self._logger.debug(
            "Tâche ajoutée à la queue: id={} priority={} size={}",
            task.id,
            task.priority.value,
            self._queue.qsize(),
        )

    async def put_async(self, task: DownloadTask) -> None:
        """Version async de `put()` qui bloque si la queue est pleine.

        Utile lorsque `max_size > 0` et que la queue est saturée.

        Args:
            task: Tâche à ajouter.

        Raises:
            QueueNotStartedError: Si la queue n'est pas démarrée.
        """
        self._ensure_started()
        assert self._queue is not None

        priority_value = _PRIORITY_MAP.get(task.priority, _PriorityValue.NORMAL)
        entry = _QueueEntry(
            priority_value=int(priority_value),
            timestamp=time.monotonic(),
            sequence=self._sequence,
            task=task,
        )
        self._sequence += 1

        # put() async bloque si maxsize atteint
        await self._queue.put(entry)
        self._enqueued_ids.add(task.id)
        self._total_enqueued += 1

        self._logger.debug(
            "Tâche ajoutée (async) à la queue: id={} priority={}",
            task.id,
            task.priority.value,
        )

    # ------------------------------------------------------------------------
    # API publique — Extraction
    # ------------------------------------------------------------------------

    async def get(self, *, timeout: float | None = None) -> DownloadTask:
        """Récupère et retire la prochaine tâche de la file.

        Bloque de manière asynchrone jusqu'à ce qu'une tâche valide
        (non annulée) soit disponible. Les tâches annulées sont filtrées
        automatiquement (lazy deletion).

        Args:
            timeout: Timeout en secondes (None = infini). Si dépassé,
                     lève `asyncio.TimeoutError`.

        Returns:
            La prochaine `DownloadTask` à traiter.

        Raises:
            QueueNotStartedError: Si la queue n'est pas démarrée.
            asyncio.TimeoutError: Si le timeout est dépassé.
        """
        self._ensure_started()
        assert self._queue is not None

        while True:
            # Récupérer la prochaine entrée (bloquant)
            if timeout is not None:
                entry = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            else:
                entry = await self._queue.get()

            task_id = entry.task.id

            # Filtrer les tâches annulées (lazy deletion)
            if task_id in self._cancelled_ids:
                self._cancelled_ids.discard(task_id)
                self._enqueued_ids.discard(task_id)
                self._queue.task_done()
                self._logger.trace(
                    "Tâche annulée filtrée de la queue: id={}", task_id
                )
                continue

            # Tâche valide
            self._enqueued_ids.discard(task_id)
            self._total_dequeued += 1
            self._queue.task_done()

            self._logger.debug(
                "Tâche extraite de la queue: id={} priority={} remaining={}",
                task_id,
                entry.task.priority.value,
                self._queue.qsize(),
            )
            return entry.task

    def get_nowait(self) -> DownloadTask | None:
        """Récupère la prochaine tâche sans bloquer.

        Retourne None si la queue est vide ou si toutes les tâches
        restantes sont annulées.

        Returns:
            La prochaine `DownloadTask`, ou None si aucune disponible.

        Raises:
            QueueNotStartedError: Si la queue n'est pas démarrée.
        """
        self._ensure_started()
        assert self._queue is not None

        while not self._queue.empty():
            try:
                entry = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return None

            task_id = entry.task.id

            if task_id in self._cancelled_ids:
                self._cancelled_ids.discard(task_id)
                self._enqueued_ids.discard(task_id)
                self._queue.task_done()
                continue

            self._enqueued_ids.discard(task_id)
            self._total_dequeued += 1
            self._queue.task_done()
            return entry.task

        return None

    def peek(self) -> DownloadTask | None:
        """Regarde la prochaine tâche sans la retirer de la queue.

        Note : cette opération est O(n) dans le pire cas car elle doit
        filtrer les tâches annulées sans les retirer.

        Returns:
            La prochaine `DownloadTask` qui serait retournée par `get()`,
            ou None si la queue est vide.
        """
        if not self._started or self._queue is None:
            return None

        # Parcourir la heap interne pour trouver la première tâche non annulée
        # Note : PriorityQueue expose _queue (list) en interne
        internal_queue = self._queue._queue  # type: ignore[attr-defined]
        for entry in sorted(internal_queue):
            if entry.task.id not in self._cancelled_ids:
                return entry.task
        return None

    # ------------------------------------------------------------------------
    # API publique — Contrôle des tâches
    # ------------------------------------------------------------------------

    def cancel(self, task_id: UUID) -> bool:
        """Annule une tâche en attente dans la queue.

        La suppression est "lazy" : la tâche reste dans la heap mais sera
        filtrée au prochain `get()`. Cela évite la reconstruction O(n)
        de la heap.

        Args:
            task_id: UUID de la tâche à annuler.

        Returns:
            True si la tâche était dans la queue et a été marquée annulée,
            False si elle n'était pas présente.

        Raises:
            QueueNotStartedError: Si la queue n'est pas démarrée.
        """
        self._ensure_started()

        if task_id not in self._enqueued_ids:
            self._logger.debug("Tâche {} pas dans la queue, cancel ignoré", task_id)
            return False

        self._cancelled_ids.add(task_id)
        self._total_cancelled += 1
        self._logger.info("Tâche annulée dans la queue: id={}", task_id)
        return True

    async def pause(self, task_id: UUID) -> bool:
        """Met en pause une tâche en attente.

        La tâche est retirée de la queue et stockée dans un buffer séparé.
        Elle ne sera pas traitée jusqu'à ce que `resume()` soit appelé.

        Note : cette opération est O(n) car elle nécessite de reconstruire
        la heap sans la tâche ciblée. À utiliser avec parcimonie.

        Args:
            task_id: UUID de la tâche à mettre en pause.

        Returns:
            True si la tâche a été mise en pause, False si absente.

        Raises:
            QueueNotStartedError: Si la queue n'est pas démarrée.
        """
        self._ensure_started()
        assert self._queue is not None

        if task_id not in self._enqueued_ids:
            self._logger.debug("Tâche {} pas dans la queue, pause ignoré", task_id)
            return False

        # Extraire toutes les entrées, filtrer la cible, réinsérer le reste
        entries: list[_QueueEntry] = []
        target_entry: _QueueEntry | None = None

        while not self._queue.empty():
            try:
                entry = self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

            if entry.task.id == task_id:
                target_entry = entry
            else:
                entries.append(entry)

        # Réinsérer les autres tâches
        for entry in entries:
            self._queue.put_nowait(entry)

        if target_entry is None:
            return False

        # Stocker dans le buffer de pause
        self._paused[task_id] = target_entry
        self._enqueued_ids.discard(task_id)
        self._logger.info("Tâche mise en pause: id={}", task_id)
        return True

    async def resume(self, task_id: UUID) -> bool:
        """Reprend une tâche en pause et la réinjecte dans la queue.

        Args:
            task_id: UUID de la tâche à reprendre.

        Returns:
            True si la tâche a été reprise, False si elle n'était pas en pause.

        Raises:
            QueueNotStartedError: Si la queue n'est pas démarrée.
        """
        self._ensure_started()
        assert self._queue is not None

        entry = self._paused.pop(task_id, None)
        if entry is None:
            self._logger.debug("Tâche {} pas en pause, resume ignoré", task_id)
            return False

        # Réinsérer avec un nouveau timestamp pour qu'elle passe après
        # les tâches de même priorité déjà en attente
        entry.timestamp = time.monotonic()
        entry.sequence = self._sequence
        self._sequence += 1

        self._queue.put_nowait(entry)
        self._enqueued_ids.add(task_id)
        self._total_enqueued += 1

        self._logger.info("Tâche reprise: id={}", task_id)
        return True

    def cancel_all(self) -> int:
        """Annule toutes les tâches en attente dans la queue.

        Returns:
            Nombre de tâches annulées.

        Raises:
            QueueNotStartedError: Si la queue n'est pas démarrée.
        """
        self._ensure_started()

        # Marquer toutes les tâches en attente comme annulées
        cancelled_count = len(self._enqueued_ids - self._cancelled_ids)
        self._cancelled_ids.update(self._enqueued_ids)
        self._total_cancelled += cancelled_count

        self._logger.info(
            "Toutes les tâches annulées: {} tâches", cancelled_count
        )
        return cancelled_count

    def clear(self) -> int:
        """Vide complètement la queue et les buffers.

        Returns:
            Nombre total d'entrées supprimées (queue + pause + cancelled).
        """
        if not self._started or self._queue is None:
            return 0

        count = self._queue.qsize() + len(self._paused) + len(self._cancelled_ids)

        # Vider la heap
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

        self._enqueued_ids.clear()
        self._cancelled_ids.clear()
        self._paused.clear()

        self._logger.info("Queue vidée: {} entrées supprimées", count)
        return count

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    def get_stats(self) -> QueueStats:
        """Retourne les statistiques instantanées de la queue.

        Returns:
            Objet QueueStats avec tous les compteurs.
        """
        return QueueStats(
            pending=self.effective_size,
            paused=len(self._paused),
            cancelled=len(self._cancelled_ids),
            total_enqueued=self._total_enqueued,
            total_dequeued=self._total_dequeued,
            total_cancelled=self._total_cancelled,
        )

    # ------------------------------------------------------------------------
    # API publique — Query
    # ------------------------------------------------------------------------

    def contains(self, task_id: UUID) -> bool:
        """Vérifie si une tâche est en attente dans la queue (non annulée).

        Args:
            task_id: UUID de la tâche.

        Returns:
            True si la tâche est dans la queue et non annulée.
        """
        return task_id in self._enqueued_ids and task_id not in self._cancelled_ids

    def is_paused(self, task_id: UUID) -> bool:
        """Vérifie si une tâche est en pause.

        Args:
            task_id: UUID de la tâche.

        Returns:
            True si la tâche est dans le buffer de pause.
        """
        return task_id in self._paused

    def list_pending_ids(self) -> list[UUID]:
        """Liste les IDs des tâches en attente (non annulées).

        Returns:
            Liste des UUIDs en attente, sans ordre garanti.
        """
        return list(self._enqueued_ids - self._cancelled_ids)

    def list_paused_ids(self) -> list[UUID]:
        """Liste les IDs des tâches en pause.

        Returns:
            Liste des UUIDs en pause.
        """
        return list(self._paused.keys())

    # ------------------------------------------------------------------------
    # Méthodes internes
    # ------------------------------------------------------------------------

    def _ensure_started(self) -> None:
        """Vérifie que la queue est démarrée.

        Raises:
            QueueNotStartedError: Si la queue n'est pas démarrée.
        """
        if not self._started:
            raise QueueNotStartedError()

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<DownloadQueue status={status} "
            f"pending={self.effective_size} "
            f"paused={len(self._paused)} "
            f"cancelled={len(self._cancelled_ids)}>"
        )

    def __len__(self) -> int:
        """Nombre de tâches en attente (alias de `effective_size`)."""
        return self.effective_size
