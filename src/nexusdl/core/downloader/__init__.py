"""Module public du système de téléchargement NexusDL.

Ce module constitue le point d'entrée de la couche d'orchestration des
téléchargements dans l'architecture hexagonale. Il expose l'API publique
stable utilisée par les interfaces (CLI, Web, GUI) pour soumettre, contrôler
et monitorer les tâches de téléchargement.

Architecture :
    DownloadManager (orchestrateur global)
        ├── DownloadQueue (file prioritaire async)
        ├── DownloadWorker[] (workers individuels)
        ├── DeduplicationCache (éviter les re-téléchargements)
        ├── ProgressTracker (suivi temps réel 4 niveaux)
        └── EventBus (notification des interfaces)

Règles d'or :
    1. Ce fichier n'expose QUE les symboles publics stables.
    2. Il ne dépend d'aucun module de `interfaces/` ni de `parsers/`.
    3. Tous les exports sont typés strictement et documentés.
    4. Les imports sont organisés par sous-module pour la lisibilité.

Exemple d'utilisation :
    >>> from nexusdl.core.downloader import (
    ...     DownloadManager,
    ...     DownloadTask,
    ...     PackagingFormat,
    ...     Priority,
    ... )
    >>>
    >>> manager = DownloadManager(
    ...     registry=registry,
    ...     session_factory=session_factory,
    ...     packager_factory=packager_factory,
    ...     event_bus=event_bus,
    ... )
    >>> await manager.start()
    >>> task = DownloadTask(manga=manga, chapters=[ch1], dest=Path("/dl"))
    >>> task_id = manager.submit(task)
    >>> result = await manager.wait_for_task(task_id)
    >>> await manager.stop()
"""

from __future__ import annotations

# ============================================================================
# EXCEPTIONS — Hiérarchie complète
# ============================================================================

from nexusdl.core.downloader.deduplication import (
    DeduplicationCacheError,
    DeduplicationDatabaseError,
    DeduplicationError,
)
from nexusdl.core.downloader.manager import (
    DownloadManagerError,
    ManagerAlreadyStartedError,
    ManagerNotStartedError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
)
from nexusdl.core.downloader.progress import (
    ChapterNotTrackedError,
    ProgressError,
    TaskNotTrackedError,
    TrackerNotStartedError,
)
from nexusdl.core.downloader.queue import (
    QueueError,
    QueueNotStartedError,
    TaskNotInQueueError,
)
from nexusdl.core.downloader.retry import (
    InvalidRetryConfigError,
    MaxRetriesExceededError,
    RetryError,
)
from nexusdl.core.downloader.worker import (
    ChapterDownloadError,
    TaskCancelledError,
    TaskPausedError,
    WorkerError,
)

# ============================================================================
# ENUMS — États et configurations
# ============================================================================

from nexusdl.core.downloader.deduplication import DeduplicationStrategy
from nexusdl.core.downloader.progress import ProgressState
from nexusdl.core.downloader.retry import ErrorClass, RetryStrategy

# ============================================================================
# MODÈLES PYDANTIC — Données immuables
# ============================================================================

from nexusdl.core.downloader.deduplication import (
    ChapterFingerprint,
    DeduplicationStats,
    PageFingerprint,
)
from nexusdl.core.downloader.progress import (
    ChapterProgressSnapshot,
    GlobalProgressSnapshot,
    PageProgressSnapshot,
    TaskProgressSnapshot,
)
from nexusdl.core.downloader.queue import QueueStats
from nexusdl.core.downloader.retry import (
    RetryAttempt,
    RetryConfig,
    RetryPolicy,
    RetryStats,
)
from nexusdl.core.downloader.worker import ChapterResult, WorkerStats

# ============================================================================
# CLASSES PRINCIPALES — Orchestration et suivi
# ============================================================================

from nexusdl.core.downloader.deduplication import DeduplicationCache
from nexusdl.core.downloader.manager import DownloadManager, ManagerState, ManagerStats
from nexusdl.core.downloader.progress import (
    ChapterProgress,
    PageProgress,
    ProgressTracker,
    TaskProgress,
)
from nexusdl.core.downloader.queue import DownloadQueue
from nexusdl.core.downloader.worker import DownloadWorker

# ============================================================================
# RETRY — Policies prédéfinies et constructeurs
# ============================================================================

from nexusdl.core.downloader.retry import (
    CLOUDFLARE_BYPASS_POLICY,
    CHAPTER_DOWNLOAD_POLICY,
    CRITICAL_POLICY,
    DISK_IO_POLICY,
    NETWORK_POLICY,
    PAGE_DOWNLOAD_POLICY,
    PARSER_POLICY,
    RATE_LIMIT_POLICY,
    RETRY_POLICIES,
    build_retry,
    build_retry_callback,
    classify_error,
    compute_delay,
    download_retry,
    is_retryable,
    network_retry,
    retry_context,
    retry_with_respect_to_rate_limit,
    RetryContext,
)

# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================

from nexusdl.core.downloader.deduplication import (
    build_chapter_key,
    compute_content_hash,
)
from nexusdl.core.downloader.progress import (
    format_bytes,
    format_duration,
    format_eta,
    format_speed,
)

# ============================================================================
# EXPORTS PUBLICS
# ============================================================================

__all__ = [
    # === Classes principales ===
    "DownloadManager",
    "DownloadQueue",
    "DownloadWorker",
    "DeduplicationCache",
    "ProgressTracker",
    "TaskProgress",
    "ChapterProgress",
    "PageProgress",
    "RetryContext",
    # === États et enums ===
    "ManagerState",
    "DeduplicationStrategy",
    "ProgressState",
    "RetryStrategy",
    "ErrorClass",
    # === Modèles Pydantic ===
    "ManagerStats",
    "QueueStats",
    "WorkerStats",
    "DeduplicationStats",
    "RetryStats",
    "RetryConfig",
    "RetryPolicy",
    "RetryAttempt",
    "TaskProgressSnapshot",
    "ChapterProgressSnapshot",
    "PageProgressSnapshot",
    "GlobalProgressSnapshot",
    "PageFingerprint",
    "ChapterFingerprint",
    "ChapterResult",
    # === Retry policies ===
    "NETWORK_POLICY",
    "PAGE_DOWNLOAD_POLICY",
    "CHAPTER_DOWNLOAD_POLICY",
    "PARSER_POLICY",
    "CRITICAL_POLICY",
    "DISK_IO_POLICY",
    "CLOUDFLARE_BYPASS_POLICY",
    "RATE_LIMIT_POLICY",
    "RETRY_POLICIES",
    # === Retry functions ===
    "build_retry",
    "build_retry_callback",
    "network_retry",
    "download_retry",
    "retry_context",
    "retry_with_respect_to_rate_limit",
    "classify_error",
    "is_retryable",
    "compute_delay",
    # === Helpers ===
    "build_chapter_key",
    "compute_content_hash",
    "format_bytes",
    "format_speed",
    "format_eta",
    "format_duration",
    # === Exceptions ===
    "DownloadManagerError",
    "ManagerNotStartedError",
    "ManagerAlreadyStartedError",
    "TaskNotFoundError",
    "TaskAlreadyExistsError",
    "QueueError",
    "QueueNotStartedError",
    "TaskNotInQueueError",
    "WorkerError",
    "ChapterDownloadError",
    "TaskCancelledError",
    "TaskPausedError",
    "ProgressError",
    "TrackerNotStartedError",
    "TaskNotTrackedError",
    "ChapterNotTrackedError",
    "DeduplicationError",
    "DeduplicationDatabaseError",
    "DeduplicationCacheError",
    "RetryError",
    "MaxRetriesExceededError",
    "InvalidRetryConfigError",
]

__version__: str = "0.1.0"
