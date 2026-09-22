"""Scanner de synchronisation filesystem ↔ bibliothèque locale.

Ce module fournit un scanner asynchrone robuste qui maintient la base de
données SQLite de la bibliothèque synchronisée avec le filesystem. Il
détecte automatiquement les changements (ajouts, suppressions, modifications)
et met à jour les métadonnées des mangas/chapitres en extrayant les données
depuis les fichiers `ComicInfo.xml` embarqués dans les archives CBZ/CBR/PDF.

Fonctionnalités principales :
    - Scan récursif des répertoires configurés
    - Détection des formats supportés (CBZ, CBR, PDF, ZIP, RAR)
    - Scan incrémental basé sur (mtime, size) pour la performance
    - Extraction des métadonnées ComicInfo.xml depuis les archives
    - Détection des ajouts, suppressions et modifications
    - Mise à jour transactionnelle de la BDD SQLite
    - Gestion robuste des erreurs par fichier (ne bloque pas le scan)
    - Émission d'événements via l'EventBus pour les interfaces
    - Support du scan complet (force) et incrémental (delta)
    - Statistiques détaillées (fichiers traités, erreurs, durée)
    - Annulation propre via asyncio.CancelledError
    - Concurrence contrôlée via sémaphore

Architecture :
    LibraryScanner
        ├── ScannerConfig (Pydantic — configuration du scan)
        ├── ScanMode (enum — FULL, INCREMENTAL, QUICK)
        ├── FileChange (Pydantic — changement détecté)
        ├── FileChangeType (enum — ADDED, REMOVED, MODIFIED, UNCHANGED)
        ├── ScanResult (Pydantic — résultat d'un scan)
        ├── ScanStats (Pydantic — statistiques agrégées)
        └── _ScanContext (interne — contexte partagé entre workers)

Les opérations I/O (lecture d'archives, extraction ZIP) sont CPU/IO-bound
et exécutées via `asyncio.to_thread()`. Un sémaphore limite la concurrence
pour éviter la surcharge disque.

Exemple d'utilisation :
    >>> scanner = LibraryScanner(
    ...     database=library_db,
    ...     metadata_extractor=metadata_extractor,
    ...     event_bus=event_bus,
    ... )
    >>> await scanner.start()
    >>>
    >>> # Scan incrémental (rapide, basé sur mtime)
    >>> result = await scanner.scan(
    ...     roots=[Path("~/Mangas").expanduser()],
    ...     mode=ScanMode.INCREMENTAL,
    ... )
    >>> print(f"Ajouts: {result.added}, Suppressions: {result.removed}")
    >>>
    >>> # Scan complet (force la relecture de tous les fichiers)
    >>> result = await scanner.scan(
    ...     roots=[Path("~/Mangas").expanduser()],
    ...     mode=ScanMode.FULL,
    ... )
    >>>
    >>> # Scan rapide (juste détection des ajouts/suppressions, pas de métadonnées)
    >>> result = await scanner.scan(
    ...     roots=[Path("~/Mangas").expanduser()],
    ...     mode=ScanMode.QUICK,
    ... )
    >>>
    >>> await scanner.stop()
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final, Self

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.events import EventBus
from nexusdl.core.exceptions import NexusDLError

if TYPE_CHECKING:
    from nexusdl.core.library.database import LibraryDatabase
    from nexusdl.core.library.metadata import MetadataExtractor


# ============================================================================
# EXCEPTIONS
# ============================================================================


class ScannerError(NexusDLError):
    """Exception de base pour les erreurs du scanner."""


class ScannerNotStartedError(ScannerError):
    """Exception levée lorsqu'on utilise le scanner avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "LibraryScanner must be started before use. Call await scanner.start()"
        )


class ScanCancelledError(ScannerError):
    """Exception levée lorsqu'un scan est annulé."""

    def __init__(self, scanned_files: int) -> None:
        super().__init__(f"Scan annulé après {scanned_files} fichiers")
        self.scanned_files = scanned_files


class RootNotAccessibleError(ScannerError):
    """Exception levée lorsqu'un répertoire racine est inaccessible."""

    def __init__(self, root: Path, reason: str = "") -> None:
        msg = f"Répertoire racine inaccessible: {root}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.root = root
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class ScanMode(str, Enum):
    """Mode de scan de la bibliothèque.

    FULL         : Scan complet — relit toutes les archives, extrait toutes
                   les métadonnées. Lent mais exhaustif. À utiliser après
                   une migration ou pour forcer une synchronisation complète.
    INCREMENTAL  : Scan incrémental — ne traite que les fichiers dont le
                   (mtime, size) a changé depuis le dernier scan. Rapide
                   pour les scans réguliers (défaut).
    QUICK        : Scan rapide — détecte uniquement les ajouts/suppressions
                   sans extraire les métadonnées. Très rapide, utile pour
                   rafraîchir la liste des fichiers sans mise à jour lourde.
    """

    FULL = "full"
    INCREMENTAL = "incremental"
    QUICK = "quick"


class FileChangeType(str, Enum):
    """Type de changement détecté sur un fichier.

    ADDED      : Fichier nouvellement détecté (absent de la BDD).
    REMOVED    : Fichier supprimé du filesystem (présent en BDD mais absent).
    MODIFIED   : Fichier modifié (mtime ou size différent de la BDD).
    UNCHANGED  : Fichier inchangé (pas d'action requise).
    """

    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    UNCHANGED = "unchanged"


class ScanState(str, Enum):
    """État d'un scan en cours.

    IDLE       : Aucun scan en cours.
    SCANNING   : Scan en cours (détection des fichiers).
    PROCESSING : Traitement des fichiers détectés (extraction métadonnées).
    UPDATING   : Mise à jour de la BDD.
    COMPLETED  : Scan terminé avec succès.
    FAILED     : Scan terminé avec erreur.
    CANCELLED  : Scan annulé par l'utilisateur.
    """

    IDLE = "idle"
    SCANNING = "scanning"
    PROCESSING = "processing"
    UPDATING = "updating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ============================================================================
# MODÈLES PYDANTIC — Configuration
# ============================================================================


class ScannerConfig(BaseModel):
    """Configuration complète du scanner de bibliothèque.

    Tous les champs sont optionnels avec des valeurs par défaut raisonnables.
    Le modèle est immuable (`frozen=True`) pour garantir la cohérence.
    """

    # Extensions de fichiers reconnues
    supported_extensions: set[str] = Field(
        default_factory=lambda: {
            ".cbz", ".cbr", ".pdf", ".zip", ".rar", ".cb7",
        },
        description="Extensions de fichiers reconnues comme archives de manga.",
    )

    # Fichiers/répertoires à ignorer
    ignore_patterns: set[str] = Field(
        default_factory=lambda: {
            ".DS_Store", "Thumbs.db", "desktop.ini",
            ".nexusdl", ".trash", "__MACOSX",
        },
        description="Noms de fichiers/répertoires à ignorer.",
    )

    # Profondeur maximale de récursion (0 = illimité)
    max_depth: int = Field(
        default=0,
        ge=0,
        description="Profondeur maximale de récursion (0 = illimité).",
    )

    # Suivre les liens symboliques
    follow_symlinks: bool = Field(
        default=False,
        description="Suivre les liens symboliques (peut causer des boucles).",
    )

    # Concurrence
    max_concurrent_extractions: int = Field(
        default=4,
        ge=1,
        le=32,
        description="Nombre maximum d'extractions de métadonnées simultanées.",
    )

    # Scan incrémental
    incremental_cache_path: Path | None = Field(
        default=None,
        description="Chemin vers le cache de métadonnées pour le scan incrémental. "
                    "Si None, le cache est stocké en BDD.",
    )

    # Comportement
    extract_metadata: bool = Field(
        default=True,
        description="Extraire les métadonnées ComicInfo.xml des archives.",
    )
    update_database: bool = Field(
        default=True,
        description="Mettre à jour la BDD après le scan.",
    )
    remove_orphaned_entries: bool = Field(
        default=True,
        description="Supprimer les entrées BDD orphelines (fichiers supprimés).",
    )
    skip_small_files: bool = Field(
        default=True,
        description="Ignorer les fichiers < 1 KB (probablement corrompus).",
    )
    min_file_size_bytes: int = Field(
        default=1024,
        ge=0,
        description="Taille minimale d'un fichier pour être considéré valide.",
    )

    # Timeout
    extraction_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        le=300.0,
        description="Timeout pour l'extraction de métadonnées d'une archive.",
    )
    scan_timeout_seconds: float = Field(
        default=600.0,
        gt=0.0,
        le=3600.0,
        description="Timeout global pour un scan complet (10 min par défaut).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# MODÈLES PYDANTIC — Résultats
# ============================================================================


class FileChange(BaseModel):
    """Changement détecté sur un fichier individuel.

    Représente un delta entre l'état du filesystem et l'état de la BDD.
    """

    path: Path = Field(..., description="Chemin absolu du fichier.")
    change_type: FileChangeType = Field(..., description="Type de changement.")
    file_size_bytes: int = Field(default=0, ge=0, description="Taille du fichier.")
    mtime: float = Field(default=0.0, description="Timestamp de modification (epoch).")
    previous_size_bytes: int | None = Field(
        default=None,
        description="Taille précédente (pour MODIFIED).",
    )
    previous_mtime: float | None = Field(
        default=None,
        description="Timestamp précédent (pour MODIFIED).",
    )
    error: str | None = Field(
        default=None,
        description="Message d'erreur si le traitement a échoué.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class ScanResult(BaseModel):
    """Résultat complet d'un scan de bibliothèque.

    Contient tous les changements détectés et les statistiques d'exécution.
    """

    mode: ScanMode = Field(..., description="Mode de scan utilisé.")
    roots: list[Path] = Field(..., description="Répertoires racines scannés.")
    state: ScanState = Field(..., description="État final du scan.")
    started_at: datetime = Field(..., description="Timestamp de début.")
    completed_at: datetime = Field(..., description="Timestamp de fin.")
    duration_seconds: float = Field(..., ge=0.0, description="Durée totale.")

    # Fichiers détectés
    files_scanned: int = Field(default=0, ge=0, description="Fichiers inspectés.")
    files_added: int = Field(default=0, ge=0, description="Fichiers ajoutés à la BDD.")
    files_removed: int = Field(default=0, ge=0, description="Fichiers supprimés de la BDD.")
    files_modified: int = Field(default=0, ge=0, description="Fichiers mis à jour.")
    files_unchanged: int = Field(default=0, ge=0, description="Fichiers inchangés.")
    files_skipped: int = Field(default=0, ge=0, description="Fichiers ignorés (patterns, taille).")

    # Erreurs
    errors_count: int = Field(default=0, ge=0, description="Nombre d'erreurs rencontrées.")
    errors: list[FileChange] = Field(
        default_factory=list,
        description="Liste des fichiers en erreur.",
    )

    # Changements détaillés
    changes: list[FileChange] = Field(
        default_factory=list,
        description="Liste de tous les changements détectés.",
    )

    # Métadonnées extraites
    metadata_extracted: int = Field(
        default=0,
        ge=0,
        description="Archives avec métadonnées extraites avec succès.",
    )
    metadata_failed: int = Field(
        default=0,
        ge=0,
        description="Archives dont l'extraction a échoué.",
    )

    # Base de données
    database_updated: bool = Field(
        default=False,
        description="True si la BDD a été mise à jour.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def total_changes(self) -> int:
        """Nombre total de changements (ajouts + suppressions + modifications)."""
        return self.files_added + self.files_removed + self.files_modified

    @property
    def success_rate(self) -> float:
        """Taux de succès (0.0 à 1.0) basé sur les fichiers traités sans erreur."""
        total_processed = self.files_added + self.files_modified + self.files_removed
        if total_processed == 0:
            return 1.0
        return max(0.0, 1.0 - (self.errors_count / total_processed))


class ScanStats(BaseModel):
    """Statistiques agrégées du scanner sur plusieurs scans."""

    total_scans: int = Field(default=0, ge=0)
    full_scans: int = Field(default=0, ge=0)
    incremental_scans: int = Field(default=0, ge=0)
    quick_scans: int = Field(default=0, ge=0)
    total_files_scanned: int = Field(default=0, ge=0)
    total_files_added: int = Field(default=0, ge=0)
    total_files_removed: int = Field(default=0, ge=0)
    total_files_modified: int = Field(default=0, ge=0)
    total_errors: int = Field(default=0, ge=0)
    total_duration_seconds: float = Field(default=0.0, ge=0.0)
    last_scan_at: datetime | None = None
    last_scan_mode: ScanMode | None = None
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def average_duration_seconds(self) -> float:
        """Durée moyenne d'un scan."""
        if self.total_scans == 0:
            return 0.0
        return self.total_duration_seconds / self.total_scans


# ============================================================================
# CONTEXTE INTERNE DE SCAN
# ============================================================================


@dataclass
class _ScanContext:
    """Contexte partagé entre les workers d'un scan.

    Non exposé publiquement — utilisé en interne par LibraryScanner.
    """

    config: ScannerConfig
    mode: ScanMode
    roots: list[Path]
    database: LibraryDatabase
    metadata_extractor: MetadataExtractor | None
    event_bus: EventBus | None
    semaphore: asyncio.Semaphore

    # État mutable (protégé par des locks)
    changes: list[FileChange] = field(default_factory=list)
    errors: list[FileChange] = field(default_factory=list)
    files_scanned: int = 0
    files_added: int = 0
    files_removed: int = 0
    files_modified: int = 0
    files_unchanged: int = 0
    files_skipped: int = 0
    metadata_extracted: int = 0
    metadata_failed: int = 0

    # Cache des fichiers connus en BDD (path → (size, mtime))
    known_files: dict[str, tuple[int, float]] = field(default_factory=dict)

    # Locks
    changes_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    counters_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # Annulation
    cancelled: bool = False

    # Timestamps
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ============================================================================
# CLASSE PRINCIPALE — LibraryScanner
# ============================================================================


class LibraryScanner:
    """Scanner asynchrone de synchronisation filesystem ↔ bibliothèque.

    Maintient la BDD SQLite synchronisée avec le filesystem en détectant
    les ajouts, suppressions et modifications d'archives de mangas.

    Lifecycle :
        >>> scanner = LibraryScanner(database=library_db, ...)
        >>> await scanner.start()
        >>> result = await scanner.scan(roots=[Path("~/Mangas")])
        >>> await scanner.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Un seul scan peut être actif à la fois (verrou interne).
    """

    # Constantes
    _DEFAULT_MAX_CONCURRENT: Final[int] = 4
    _PROGRESS_EMIT_INTERVAL: Final[float] = 0.5  # secondes

    def __init__(
        self,
        database: LibraryDatabase,
        *,
        metadata_extractor: MetadataExtractor | None = None,
        event_bus: EventBus | None = None,
        config: ScannerConfig | None = None,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
    ) -> None:
        """Initialise le scanner de bibliothèque.

        Args:
            database: Instance de LibraryDatabase (doit être initialisée).
            metadata_extractor: Extracteur de métadonnées ComicInfo.xml (optionnel).
            event_bus: Bus d'événements pour notifier les interfaces (optionnel).
            config: Configuration du scanner (défaut: valeurs par défaut).
            max_concurrent: Nombre maximum d'extractions simultanées.
        """
        if max_concurrent <= 0:
            raise ValueError(f"max_concurrent must be positive, got {max_concurrent}")

        self._database = database
        self._metadata_extractor = metadata_extractor
        self._event_bus = event_bus
        self._config = config or ScannerConfig()
        self._max_concurrent = max_concurrent

        self._semaphore: asyncio.Semaphore | None = None
        self._scan_lock: asyncio.Lock = asyncio.Lock()
        self._started: bool = False
        self._start_time: float = 0.0

        # Statistiques cumulées
        self._total_scans: int = 0
        self._full_scans: int = 0
        self._incremental_scans: int = 0
        self._quick_scans: int = 0
        self._total_files_scanned: int = 0
        self._total_files_added: int = 0
        self._total_files_removed: int = 0
        self._total_files_modified: int = 0
        self._total_errors: int = 0
        self._total_duration_seconds: float = 0.0
        self._last_scan_at: datetime | None = None
        self._last_scan_mode: ScanMode | None = None
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="library_scanner")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre le scanner et initialise les ressources."""
        if self._started:
            self._logger.warning("LibraryScanner déjà démarré, ignore")
            return

        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._started = True
        self._start_time = asyncio.get_event_loop().time()

        self._logger.info(
            "LibraryScanner démarré: max_concurrent={}, extensions={}",
            self._max_concurrent,
            sorted(self._config.supported_extensions),
        )

    async def stop(self) -> None:
        """Arrête le scanner et libère les ressources."""
        if not self._started:
            return

        self._semaphore = None
        self._started = False
        self._logger.info("LibraryScanner arrêté")

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
        """Indique si le scanner est démarré."""
        return self._started

    @property
    def is_scanning(self) -> bool:
        """Indique si un scan est en cours."""
        return self._scan_lock.locked()

    # ------------------------------------------------------------------------
    # API publique — Scan
    # ------------------------------------------------------------------------

    async def scan(
        self,
        roots: Sequence[Path | str],
        *,
        mode: ScanMode = ScanMode.INCREMENTAL,
        config: ScannerConfig | None = None,
        on_progress: Any | None = None,
    ) -> ScanResult:
        """Lance un scan de la bibliothèque.

        Args:
            roots: Répertoires racines à scanner (chemins absolus ou relatifs).
            mode: Mode de scan (FULL, INCREMENTAL, QUICK).
            config: Configuration override (défaut: config globale).
            on_progress: Callback appelé périodiquement pendant le scan
                         signature: (scanned: int, total: int | None) -> None.

        Returns:
            Résultat complet du scan avec tous les changements détectés.

        Raises:
            ScannerNotStartedError: Si le scanner n'est pas démarré.
            RootNotAccessibleError: Si un répertoire racine est inaccessible.
            ScannerError: Si un scan est déjà en cours.
        """
        self._ensure_started()

        # Vérifier qu'aucun scan n'est en cours
        if self._scan_lock.locked():
            raise ScannerError("Un scan est déjà en cours")

        async with self._scan_lock:
            return await self._do_scan(roots, mode, config, on_progress)

    async def scan_quick(
        self,
        roots: Sequence[Path | str],
        *,
        config: ScannerConfig | None = None,
    ) -> ScanResult:
        """Lance un scan rapide (détection ajouts/suppressions uniquement).

        Raccourci pour `scan(roots, mode=ScanMode.QUICK)`.
        """
        return await self.scan(roots, mode=ScanMode.QUICK, config=config)

    async def scan_incremental(
        self,
        roots: Sequence[Path | str],
        *,
        config: ScannerConfig | None = None,
    ) -> ScanResult:
        """Lance un scan incrémental (basé sur mtime/size).

        Raccourci pour `scan(roots, mode=ScanMode.INCREMENTAL)`.
        """
        return await self.scan(roots, mode=ScanMode.INCREMENTAL, config=config)

    async def scan_full(
        self,
        roots: Sequence[Path | str],
        *,
        config: ScannerConfig | None = None,
    ) -> ScanResult:
        """Lance un scan complet (relit toutes les archives).

        Raccourci pour `scan(roots, mode=ScanMode.FULL)`.
        """
        return await self.scan(roots, mode=ScanMode.FULL, config=config)

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> ScanStats:
        """Retourne les statistiques agrégées du scanner."""
        async with self._stats_lock:
            uptime = 0.0
            if self._start_time > 0:
                uptime = asyncio.get_event_loop().time() - self._start_time

            return ScanStats(
                total_scans=self._total_scans,
                full_scans=self._full_scans,
                incremental_scans=self._incremental_scans,
                quick_scans=self._quick_scans,
                total_files_scanned=self._total_files_scanned,
                total_files_added=self._total_files_added,
                total_files_removed=self._total_files_removed,
                total_files_modified=self._total_files_modified,
                total_errors=self._total_errors,
                total_duration_seconds=self._total_duration_seconds,
                last_scan_at=self._last_scan_at,
                last_scan_mode=self._last_scan_mode,
                uptime_seconds=uptime,
            )

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        async with self._stats_lock:
            self._total_scans = 0
            self._full_scans = 0
            self._incremental_scans = 0
            self._quick_scans = 0
            self._total_files_scanned = 0
            self._total_files_added = 0
            self._total_files_removed = 0
            self._total_files_modified = 0
            self._total_errors = 0
            self._total_duration_seconds = 0.0
            self._last_scan_at = None
            self._last_scan_mode = None

    # ------------------------------------------------------------------------
    # Méthodes internes — Scan principal
    # ------------------------------------------------------------------------

    async def _do_scan(
        self,
        roots: Sequence[Path | str],
        mode: ScanMode,
        config: ScannerConfig | None,
        on_progress: Any | None,
    ) -> ScanResult:
        """Exécute le scan complet."""
        effective_config = config or self._config
        start_time = time.perf_counter()
        started_at = datetime.now(UTC)

        # Normaliser les racines
        normalized_roots = self._normalize_roots(roots)

        self._logger.info(
            "Démarrage du scan: mode={}, roots={}, extensions={}",
            mode.value,
            [str(r) for r in normalized_roots],
            sorted(effective_config.supported_extensions),
        )

        # Émettre un événement de début
        if self._event_bus is not None:
            await self._event_bus.emit(
                "scan.started",
                {
                    "mode": mode.value,
                    "roots": [str(r) for r in normalized_roots],
                },
            )

        # Créer le contexte de scan
        assert self._semaphore is not None
        ctx = _ScanContext(
            config=effective_config,
            mode=mode,
            roots=normalized_roots,
            database=self._database,
            metadata_extractor=self._metadata_extractor,
            event_bus=self._event_bus,
            semaphore=self._semaphore,
        )

        try:
            # 1. Charger les fichiers connus en BDD (pour scan incrémental)
            if mode in (ScanMode.INCREMENTAL, ScanMode.QUICK):
                await self._load_known_files(ctx)

            # 2. Émettre un événement d'état
            if self._event_bus is not None:
                await self._event_bus.emit("scan.state_changed", {"state": ScanState.SCANNING.value})

            # 3. Scanner le filesystem
            discovered_files = await self._scan_filesystem(ctx, on_progress)

            # 4. Détecter les changements
            changes = await self._detect_changes(ctx, discovered_files)

            # 5. Traiter les changements (extraction métadonnées)
            if mode != ScanMode.QUICK and effective_config.extract_metadata:
                if self._event_bus is not None:
                    await self._event_bus.emit(
                        "scan.state_changed",
                        {"state": ScanState.PROCESSING.value},
                    )
                await self._process_changes(ctx, changes)

            # 6. Mettre à jour la BDD
            if effective_config.update_database:
                if self._event_bus is not None:
                    await self._event_bus.emit(
                        "scan.state_changed",
                        {"state": ScanState.UPDATING.value},
                    )
                await self._update_database(ctx, changes)

            # 7. Construire le résultat
            completed_at = datetime.now(UTC)
            duration = time.perf_counter() - start_time

            result = ScanResult(
                mode=mode,
                roots=normalized_roots,
                state=ScanState.COMPLETED,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
                files_scanned=ctx.files_scanned,
                files_added=ctx.files_added,
                files_removed=ctx.files_removed,
                files_modified=ctx.files_modified,
                files_unchanged=ctx.files_unchanged,
                files_skipped=ctx.files_skipped,
                errors_count=len(ctx.errors),
                errors=ctx.errors,
                changes=ctx.changes,
                metadata_extracted=ctx.metadata_extracted,
                metadata_failed=ctx.metadata_failed,
                database_updated=effective_config.update_database,
            )

            # 8. Mettre à jour les stats cumulées
            await self._record_scan(result, mode)

            self._logger.info(
                "Scan terminé: mode={}, duration={:.2f}s, "
                "added={}, removed={}, modified={}, unchanged={}, errors={}",
                mode.value,
                duration,
                result.files_added,
                result.files_removed,
                result.files_modified,
                result.files_unchanged,
                result.errors_count,
            )

            # 9. Émettre un événement de fin
            if self._event_bus is not None:
                await self._event_bus.emit(
                    "scan.completed",
                    {
                        "mode": mode.value,
                        "duration_seconds": duration,
                        "added": result.files_added,
                        "removed": result.files_removed,
                        "modified": result.files_modified,
                        "errors": result.errors_count,
                    },
                )
                await self._event_bus.emit(
                    "scan.state_changed",
                    {"state": ScanState.COMPLETED.value},
                )

            return result

        except asyncio.CancelledError:
            self._logger.warning("Scan annulé par l'utilisateur")
            duration = time.perf_counter() - start_time

            result = ScanResult(
                mode=mode,
                roots=normalized_roots,
                state=ScanState.CANCELLED,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                duration_seconds=duration,
                files_scanned=ctx.files_scanned,
                files_added=ctx.files_added,
                files_removed=ctx.files_removed,
                files_modified=ctx.files_modified,
                files_unchanged=ctx.files_unchanged,
                files_skipped=ctx.files_skipped,
                errors_count=len(ctx.errors),
                errors=ctx.errors,
                changes=ctx.changes,
                metadata_extracted=ctx.metadata_extracted,
                metadata_failed=ctx.metadata_failed,
                database_updated=False,
            )

            if self._event_bus is not None:
                await self._event_bus.emit(
                    "scan.state_changed",
                    {"state": ScanState.CANCELLED.value},
                )

            raise ScanCancelledError(ctx.files_scanned) from None

        except Exception as e:
            self._logger.error("Scan échoué: {}", e, exc_info=True)
            duration = time.perf_counter() - start_time

            result = ScanResult(
                mode=mode,
                roots=normalized_roots,
                state=ScanState.FAILED,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                duration_seconds=duration,
                files_scanned=ctx.files_scanned,
                errors_count=len(ctx.errors) + 1,
                errors=ctx.errors + [
                    FileChange(
                        path=Path("<scan>"),
                        change_type=FileChangeType.UNCHANGED,
                        error=str(e),
                    )
                ],
                database_updated=False,
            )

            if self._event_bus is not None:
                await self._event_bus.emit(
                    "scan.failed",
                    {"error": str(e), "mode": mode.value},
                )
                await self._event_bus.emit(
                    "scan.state_changed",
                    {"state": ScanState.FAILED.value},
                )

            raise ScannerError(f"Scan échoué: {e}") from e

    # ------------------------------------------------------------------------
    # Méthodes internes — Filesystem
    # ------------------------------------------------------------------------

    def _normalize_roots(self, roots: Sequence[Path | str]) -> list[Path]:
        """Normalise et valide les répertoires racines."""
        normalized: list[Path] = []
        for root in roots:
            path = Path(root).expanduser().resolve()
            if not path.exists():
                raise RootNotAccessibleError(path, "Répertoire inexistant")
            if not path.is_dir():
                raise RootNotAccessibleError(path, "N'est pas un répertoire")
            if not os.access(path, os.R_OK):
                raise RootNotAccessibleError(path, "Permission de lecture refusée")
            normalized.append(path)

        if not normalized:
            raise ScannerError("Aucun répertoire racine fourni")

        return normalized

    async def _scan_filesystem(
        self,
        ctx: _ScanContext,
        on_progress: Any | None,
    ) -> dict[str, tuple[int, float]]:
        """Scanne le filesystem et retourne un dict {path: (size, mtime)}.

        Utilise `asyncio.to_thread` pour l'I/O bloquant (os.walk).
        """
        discovered: dict[str, tuple[int, float]] = {}
        last_progress_emit = time.monotonic()

        for root in ctx.roots:
            # Exécuter os.walk dans un thread
            try:
                entries = await asyncio.to_thread(
                    self._walk_directory,
                    root,
                    ctx.config,
                )
            except Exception as e:
                self._logger.warning(
                    "Erreur lors du parcours de {}: {}", root, e
                )
                continue

            for path_str, size, mtime in entries:
                # Vérifier l'annulation
                if ctx.cancelled:
                    raise asyncio.CancelledError()

                # Filtrer par taille minimale
                if ctx.config.skip_small_files and size < ctx.config.min_file_size_bytes:
                    async with ctx.counters_lock:
                        ctx.files_skipped += 1
                    continue

                discovered[path_str] = (size, mtime)

                async with ctx.counters_lock:
                    ctx.files_scanned += 1
                    scanned = ctx.files_scanned

                # Émettre la progression périodiquement
                now = time.monotonic()
                if on_progress is not None and now - last_progress_emit >= self._PROGRESS_EMIT_INTERVAL:
                    try:
                        on_progress(scanned, None)
                    except Exception:
                        pass
                    last_progress_emit = now

        return discovered

    @staticmethod
    def _walk_directory(
        root: Path,
        config: ScannerConfig,
    ) -> list[tuple[str, int, float]]:
        """Parcourt récursivement un répertoire (synchrone, dans un thread).

        Returns:
            Liste de tuples (path_str, size, mtime) pour les fichiers valides.
        """
        results: list[tuple[str, int, float]] = []

        for dirpath, dirnames, filenames in os.walk(
            root,
            followlinks=config.follow_symlinks,
        ):
            # Filtrer les répertoires à ignorer
            dirnames[:] = [
                d for d in dirnames
                if d not in config.ignore_patterns and not d.startswith(".")
            ]

            # Vérifier la profondeur maximale
            if config.max_depth > 0:
                relative_depth = Path(dirpath).relative_to(root).parts.__len__()
                if relative_depth >= config.max_depth:
                    dirnames.clear()  # Ne pas descendre plus profond

            for filename in filenames:
                # Filtrer par patterns
                if filename in config.ignore_patterns:
                    continue
                if filename.startswith("."):
                    continue

                # Filtrer par extension
                ext = Path(filename).suffix.lower()
                if ext not in config.supported_extensions:
                    continue

                file_path = Path(dirpath) / filename

                try:
                    stat = file_path.stat()
                    results.append((str(file_path), stat.st_size, stat.st_mtime))
                except OSError:
                    # Fichier inaccessible (permissions, supprimé entre-temps)
                    continue

        return results

    # ------------------------------------------------------------------------
    # Méthodes internes — Détection de changements
    # ------------------------------------------------------------------------

    async def _load_known_files(self, ctx: _ScanContext) -> None:
        """Charge les fichiers connus en BDD pour le scan incrémental."""
        try:
            rows = await ctx.database.fetch_all(
                """
                SELECT file_path, file_size_bytes, file_mtime
                FROM scanned_file
                WHERE file_path IS NOT NULL
                """
            )
            for row in rows:
                path = row[0]
                size = row[1]
                mtime = row[2]
                ctx.known_files[path] = (size, mtime)

            ctx.known_files = dict(ctx.known_files)
            ctx._logger = logger.bind(
                module="library_scanner",
                known_files=len(ctx.known_files),
            )

        except Exception as e:
            ctx._logger.warning(
                "Impossible de charger les fichiers connus: {}. "
                "Fallback vers scan complet.",
                e,
            )
            ctx.known_files.clear()

    async def _detect_changes(
        self,
        ctx: _ScanContext,
        discovered: dict[str, tuple[int, float]],
    ) -> list[FileChange]:
        """Détecte les changements entre le filesystem et la BDD."""
        changes: list[FileChange] = []

        discovered_paths = set(discovered.keys())
        known_paths = set(ctx.known_files.keys())

        # Fichiers ajoutés
        added_paths = discovered_paths - known_paths
        for path_str in added_paths:
            size, mtime = discovered[path_str]
            change = FileChange(
                path=Path(path_str),
                change_type=FileChangeType.ADDED,
                file_size_bytes=size,
                mtime=mtime,
            )
            changes.append(change)
            async with ctx.counters_lock:
                ctx.files_added += 1

        # Fichiers supprimés
        removed_paths = known_paths - discovered_paths
        for path_str in removed_paths:
            prev_size, prev_mtime = ctx.known_files[path_str]
            change = FileChange(
                path=Path(path_str),
                change_type=FileChangeType.REMOVED,
                previous_size_bytes=prev_size,
                previous_mtime=prev_mtime,
            )
            changes.append(change)
            async with ctx.counters_lock:
                ctx.files_removed += 1

        # Fichiers potentiellement modifiés
        common_paths = discovered_paths & known_paths
        for path_str in common_paths:
            size, mtime = discovered[path_str]
            prev_size, prev_mtime = ctx.known_files[path_str]

            if ctx.mode == ScanMode.FULL:
                # En mode FULL, on traite tous les fichiers communs
                change = FileChange(
                    path=Path(path_str),
                    change_type=FileChangeType.MODIFIED,
                    file_size_bytes=size,
                    mtime=mtime,
                    previous_size_bytes=prev_size,
                    previous_mtime=prev_mtime,
                )
                changes.append(change)
                async with ctx.counters_lock:
                    ctx.files_modified += 1
            elif size != prev_size or abs(mtime - prev_mtime) > 1.0:
                # Changement détecté (tolérance de 1s sur mtime)
                change = FileChange(
                    path=Path(path_str),
                    change_type=FileChangeType.MODIFIED,
                    file_size_bytes=size,
                    mtime=mtime,
                    previous_size_bytes=prev_size,
                    previous_mtime=prev_mtime,
                )
                changes.append(change)
                async with ctx.counters_lock:
                    ctx.files_modified += 1
            else:
                # Inchangé
                change = FileChange(
                    path=Path(path_str),
                    change_type=FileChangeType.UNCHANGED,
                    file_size_bytes=size,
                    mtime=mtime,
                )
                async with ctx.counters_lock:
                    ctx.files_unchanged += 1

        # Trier : ADDED d'abord, puis MODIFIED, puis REMOVED
        priority = {
            FileChangeType.ADDED: 0,
            FileChangeType.MODIFIED: 1,
            FileChangeType.REMOVED: 2,
            FileChangeType.UNCHANGED: 3,
        }
        changes.sort(key=lambda c: priority[c.change_type])

        async with ctx.changes_lock:
            ctx.changes.extend(changes)

        return changes

    # ------------------------------------------------------------------------
    # Méthodes internes — Traitement des changements
    # ------------------------------------------------------------------------

    async def _process_changes(
        self,
        ctx: _ScanContext,
        changes: list[FileChange],
    ) -> None:
        """Traite les changements en extrayant les métadonnées."""
        if ctx.metadata_extractor is None:
            ctx._logger.debug(
                "Pas d'extracteur de métadonnées, skip de l'extraction"
            )
            return

        # Filtrer les changements nécessitant une extraction
        to_process = [
            c for c in changes
            if c.change_type in (FileChangeType.ADDED, FileChangeType.MODIFIED)
        ]

        if not to_process:
            return

        ctx._logger.info(
            "Extraction des métadonnées pour {} fichiers",
            len(to_process),
        )

        # Traiter en parallèle avec sémaphore
        tasks = [
            asyncio.create_task(
                self._extract_metadata_safe(ctx, change),
                name=f"extract_{change.path.name}",
            )
            for change in to_process
        ]

        # Attendre toutes les extractions
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _extract_metadata_safe(
        self,
        ctx: _ScanContext,
        change: FileChange,
    ) -> None:
        """Extrait les métadonnées d'un fichier avec gestion d'erreur."""
        assert ctx.metadata_extractor is not None
        assert ctx.semaphore is not None

        async with ctx.semaphore:
            try:
                # Vérifier l'annulation
                if ctx.cancelled:
                    raise asyncio.CancelledError()

                # Extraire avec timeout
                metadata = await asyncio.wait_for(
                    ctx.metadata_extractor.extract(change.path),
                    timeout=ctx.config.extraction_timeout_seconds,
                )

                # Stocker les métadonnées dans le change (via un champ caché)
                # On utilise un hack : on ajoute les métadonnées au change
                # via un attribut dynamique (pas idéal, mais évite de modifier
                # le modèle Pydantic frozen)
                object.__setattr__(change, "_metadata", metadata)

                async with ctx.counters_lock:
                    ctx.metadata_extracted += 1

                ctx._logger.trace(
                    "Métadonnées extraites: {} — {}",
                    change.path.name,
                    metadata.series if hasattr(metadata, "series") else "?",
                )

            except asyncio.TimeoutError:
                error_change = change.model_copy(
                    update={"error": "Timeout d'extraction"}
                )
                async with ctx.changes_lock:
                    ctx.errors.append(error_change)
                async with ctx.counters_lock:
                    ctx.metadata_failed += 1
                ctx._logger.warning(
                    "Timeout d'extraction pour {}: {}s",
                    change.path,
                    ctx.config.extraction_timeout_seconds,
                )

            except asyncio.CancelledError:
                raise

            except Exception as e:
                error_change = change.model_copy(
                    update={"error": str(e)}
                )
                async with ctx.changes_lock:
                    ctx.errors.append(error_change)
                async with ctx.counters_lock:
                    ctx.metadata_failed += 1
                ctx._logger.warning(
                    "Échec d'extraction pour {}: {}",
                    change.path,
                    e,
                )

    # ------------------------------------------------------------------------
    # Méthodes internes — Mise à jour BDD
    # ------------------------------------------------------------------------

    async def _update_database(
        self,
        ctx: _ScanContext,
        changes: list[FileChange],
    ) -> None:
        """Met à jour la BDD en fonction des changements détectés."""
        if not ctx.config.update_database:
            return

        # Grouper par type de changement
        added = [c for c in changes if c.change_type == FileChangeType.ADDED]
        modified = [c for c in changes if c.change_type == FileChangeType.MODIFIED]
        removed = [c for c in changes if c.change_type == FileChangeType.REMOVED]

        ctx._logger.info(
            "Mise à jour BDD: {} ajouts, {} modifs, {} suppressions",
            len(added),
            len(modified),
            len(removed),
        )

        try:
            # Utiliser une transaction pour l'atomicité
            async with ctx.database.transaction():
                # 1. Insertions (fichiers ajoutés)
                for change in added:
                    try:
                        metadata = getattr(change, "_metadata", None)
                        await ctx.database.upsert_scanned_file(
                            path=str(change.path),
                            size=change.file_size_bytes,
                            mtime=change.mtime,
                            metadata=metadata,
                        )
                    except Exception as e:
                        ctx._logger.warning(
                            "Échec d'insertion pour {}: {}",
                            change.path,
                            e,
                        )
                        async with ctx.changes_lock:
                            ctx.errors.append(
                                change.model_copy(update={"error": str(e)})
                            )

                # 2. Mises à jour (fichiers modifiés)
                for change in modified:
                    try:
                        metadata = getattr(change, "_metadata", None)
                        await ctx.database.upsert_scanned_file(
                            path=str(change.path),
                            size=change.file_size_bytes,
                            mtime=change.mtime,
                            metadata=metadata,
                        )
                    except Exception as e:
                        ctx._logger.warning(
                            "Échec de mise à jour pour {}: {}",
                            change.path,
                            e,
                        )
                        async with ctx.changes_lock:
                            ctx.errors.append(
                                change.model_copy(update={"error": str(e)})
                            )

                # 3. Suppressions (fichiers retirés)
                if ctx.config.remove_orphaned_entries:
                    for change in removed:
                        try:
                            await ctx.database.delete_scanned_file(
                                path=str(change.path)
                            )
                        except Exception as e:
                            ctx._logger.warning(
                                "Échec de suppression pour {}: {}",
                                change.path,
                                e,
                            )
                            async with ctx.changes_lock:
                                ctx.errors.append(
                                    change.model_copy(update={"error": str(e)})
                                )

            ctx._logger.debug("Mise à jour BDD terminée avec succès")

        except Exception as e:
            ctx._logger.error("Échec de la mise à jour BDD: {}", e, exc_info=True)
            raise ScannerError(f"Échec de la mise à jour BDD: {e}") from e

    # ------------------------------------------------------------------------
    # Méthodes internes — Statistiques
    # ------------------------------------------------------------------------

    async def _record_scan(self, result: ScanResult, mode: ScanMode) -> None:
        """Enregistre un scan dans les statistiques cumulées."""
        async with self._stats_lock:
            self._total_scans += 1
            if mode == ScanMode.FULL:
                self._full_scans += 1
            elif mode == ScanMode.INCREMENTAL:
                self._incremental_scans += 1
            elif mode == ScanMode.QUICK:
                self._quick_scans += 1

            self._total_files_scanned += result.files_scanned
            self._total_files_added += result.files_added
            self._total_files_removed += result.files_removed
            self._total_files_modified += result.files_modified
            self._total_errors += result.errors_count
            self._total_duration_seconds += result.duration_seconds
            self._last_scan_at = result.completed_at
            self._last_scan_mode = mode

    # ------------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # ------------------------------------------------------------------------

    def _ensure_started(self) -> None:
        """Vérifie que le scanner est démarré."""
        if not self._started:
            raise ScannerNotStartedError()

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        scanning = "scanning" if self.is_scanning else "idle"
        return (
            f"<LibraryScanner status={status} state={scanning} "
            f"scans={self._total_scans}>"
        )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "ScannerError",
    "ScannerNotStartedError",
    "ScanCancelledError",
    "RootNotAccessibleError",
    # Enums
    "ScanMode",
    "FileChangeType",
    "ScanState",
    # Modèles
    "ScannerConfig",
    "FileChange",
    "ScanResult",
    "ScanStats",
    # Classe principale
    "LibraryScanner",
]
