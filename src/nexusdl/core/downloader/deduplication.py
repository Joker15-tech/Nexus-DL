"""Système de déduplication intelligent pour les téléchargements.

Ce module fournit une couche de déduplication à deux niveaux pour éviter
de re-télécharger des contenus déjà présents en local :

1. **Niveau page** : déduplication par hash SHA256 du contenu binaire.
   Permet de détecter qu'une image servie par un site est identique à une
   image déjà téléchargée (même si l'URL diffère).

2. **Niveau chapitre** : déduplication par clé composite (site_id + chapter_id
   + version + scanlator). Permet de détecter qu'un chapitre complet a déjà
   été téléchargé et empaqueté avec succès.

La persistance est assurée par SQLite (via aiosqlite) avec un cache LRU en
mémoire pour les accès fréquents. Le module est thread-safe et conçu pour
être partagé entre plusieurs workers de téléchargement.

Exemple d'utilisation :
    >>> cache = DeduplicationCache(config)
    >>> await cache.start()
    >>>
    >>> # Vérifier si une page existe déjà
    >>> if await cache.check_page_exists(sha256_hash):
    ...     logger.info("Page déjà téléchargée, skip")
    ...
    >>> # Enregistrer une page téléchargée
    >>> await cache.register_page(sha256_hash, size=102400, url="https://...")
    >>>
    >>> # Vérifier si un chapitre complet existe
    >>> key = build_chapter_key("mangadex", "abc-123", "12.5", "fr")
    >>> if await cache.check_chapter_exists(key):
    ...     logger.info("Chapitre déjà complet, skip")
    >>>
    >>> await cache.stop()
"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from collections import OrderedDict
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import ClassVar, Final, Self
from uuid import UUID

import aiosqlite
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# EXCEPTIONS
# ============================================================================


class DeduplicationError(NexusDLError):
    """Exception de base pour les erreurs du système de déduplication."""


class DeduplicationDatabaseError(DeduplicationError):
    """Erreur liée à la base de données de déduplication (corruption, I/O)."""


class DeduplicationCacheError(DeduplicationError):
    """Erreur liée au cache mémoire (éjection, overflow)."""


# ============================================================================
# ENUMS
# ============================================================================


class DeduplicationStrategy(str, Enum):
    """Stratégie de déduplication appliquée aux téléchargements.

    NONE     : aucune déduplication (téléchargements systématiques)
    PAGE     : déduplication par hash SHA256 du contenu des pages
    CHAPTER  : déduplication par clé composite (chapitre complet)
    BOTH     : les deux stratégies combinées (recommandé)
    """

    NONE = "none"
    PAGE = "page"
    CHAPTER = "chapter"
    BOTH = "both"


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class PageFingerprint(BaseModel):
    """Empreinte unique d'une page téléchargée.

    Utilisée pour détecter qu'une image servie par un site est identique
    à une image déjà présente en local, même si l'URL diffère.
    """

    hash: str = Field(
        ...,
        description="Hash SHA256 du contenu binaire de la page (64 caractères hex).",
        pattern=r"^[a-f0-9]{64}$",
    )
    size: int = Field(..., ge=0, description="Taille du fichier en bytes.")
    url: str | None = Field(default=None, description="URL source originale (optionnelle).")
    site_id: str | None = Field(default=None, description="Site d'origine (optionnel).")
    first_seen_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de premier enregistrement (UTC).",
    )
    last_seen_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp du dernier accès (pour LRU).",
    )
    hit_count: int = Field(default=1, ge=1, description="Nombre de fois détectée en doublon.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ChapterFingerprint(BaseModel):
    """Empreinte unique d'un chapitre téléchargé et empaqueté.

    Utilisée pour détecter qu'un chapitre complet a déjà été téléchargé
    avec succès, évitant ainsi un re-téléchargement intégral.
    """

    composite_key: str = Field(
        ...,
        description="Clé composite unique (site_id:chapter_id:version:scanlator:language).",
    )
    content_hash: str | None = Field(
        default=None,
        description="Hash SHA256 du fichier final empaqueté (CBZ/PDF/etc.).",
        pattern=r"^[a-f0-9]{64}$",
    )
    output_path: str | None = Field(
        default=None,
        description="Chemin du fichier de sortie (pour vérification d'existence).",
    )
    pages_count: int = Field(default=0, ge=0, description="Nombre de pages du chapitre.")
    bytes_total: int = Field(default=0, ge=0, description="Taille totale en bytes.")
    packaging_format: str | None = Field(
        default=None,
        description="Format d'empaquetage (cbz, cbr, pdf, etc.).",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de création.",
    )
    task_id: str | None = Field(
        default=None,
        description="UUID de la tâche de téléchargement associée.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class DeduplicationStats(BaseModel):
    """Statistiques agrégées du système de déduplication."""

    total_pages_registered: int = Field(default=0, ge=0)
    total_chapters_registered: int = Field(default=0, ge=0)
    pages_skipped: int = Field(default=0, ge=0, description="Pages évitées grâce à la dédup.")
    chapters_skipped: int = Field(default=0, ge=0, description="Chapitres évités grâce à la dédup.")
    bytes_saved: int = Field(default=0, ge=0, description="Bytes économisés grâce à la dédup.")
    cache_hit_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Taux de hit du cache mémoire (0.0 à 1.0).",
    )
    database_size_bytes: int = Field(default=0, ge=0, description="Taille de la BDD SQLite.")

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# HELPERS
# ============================================================================


def build_chapter_key(
    site_id: str,
    chapter_source_id: str,
    chapter_number: str | float,
    language: str,
    *,
    scanlator: str | None = None,
    version: str | int | None = None,
) -> str:
    """Construit une clé composite unique pour un chapitre.

    La clé est normalisée (minuscule, espaces supprimés) pour garantir
    l'unicité indépendamment des variations de formatage.

    Args:
        site_id: Identifiant du site source (ex: 'mangadex').
        chapter_source_id: ID du chapitre sur le site source.
        chapter_number: Numéro du chapitre (peut être '12.5' ou 'Extra').
        language: Code langue ISO 639-1 (ex: 'fr', 'en').
        scanlator: Groupe de scanlation (optionnel).
        version: Version du chapitre (optionnel, ex: 'v2').

    Returns:
        Clé composite normalisée au format 'site:chapter_id:number:lang[:scanlator][:vN]'.

    Example:
        >>> build_chapter_key("mangadex", "abc-123", 12.5, "fr")
        'mangadex:abc-123:12.5:fr'
        >>> build_chapter_key("mangadex", "abc-123", "Extra", "en", scanlator="GroupX")
        'mangadex:abc-123:extra:en:groupx'
    """
    # Normalisation robuste
    normalized_number = str(chapter_number).strip().lower().replace(" ", "")
    normalized_site = site_id.strip().lower()
    normalized_lang = language.strip().lower()
    normalized_chapter_id = chapter_source_id.strip()

    parts = [normalized_site, normalized_chapter_id, normalized_number, normalized_lang]

    if scanlator:
        parts.append(scanlator.strip().lower())
    if version is not None:
        parts.append(f"v{version}")

    return ":".join(parts)


def compute_content_hash(data: bytes) -> str:
    """Calcule le hash SHA256 d'un contenu binaire.

    Args:
        data: Contenu binaire à hasher.

    Returns:
        Hash SHA256 au format hexadécimal (64 caractères).
    """
    return hashlib.sha256(data).hexdigest()


# ============================================================================
# CACHE LRU
# ============================================================================


class _LRUCache[T]:
    """Cache LRU générique thread-safe avec limite de taille.

    Implémentation légère basée sur OrderedDict. Non persistant.
    """

    __slots__ = ("_capacity", "_data", "_hits", "_misses", "_lock")

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError(f"Capacity must be positive, got {capacity}")
        self._capacity: Final[int] = capacity
        self._data: OrderedDict[str, T] = OrderedDict()
        self._hits: int = 0
        self._misses: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get(self, key: str) -> T | None:
        """Récupère une valeur et la marque comme récemment utilisée."""
        async with self._lock:
            if key in self._data:
                self._hits += 1
                self._data.move_to_end(key)
                return self._data[key]
            self._misses += 1
            return None

    async def put(self, key: str, value: T) -> None:
        """Insère ou met à jour une valeur, en éjectant la plus ancienne si nécessaire."""
        async with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._data[key] = value
            else:
                self._data[key] = value
                if len(self._data) > self._capacity:
                    self._data.popitem(last=False)

    async def contains(self, key: str) -> bool:
        """Vérifie la présence d'une clé sans affecter l'ordre LRU."""
        async with self._lock:
            if key in self._data:
                self._hits += 1
                return True
            self._misses += 1
            return False

    async def remove(self, key: str) -> bool:
        """Supprime une clé. Retourne True si elle existait."""
        async with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

    async def clear(self) -> None:
        """Vide le cache et réinitialise les compteurs."""
        async with self._lock:
            self._data.clear()
            self._hits = 0
            self._misses = 0

    @property
    def size(self) -> int:
        return len(self._data)

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0


# ============================================================================
# CLASSE PRINCIPALE
# ============================================================================


class DeduplicationCache:
    """Cache de déduplication persistant (SQLite) + mémoire (LRU).

    Ce cache est conçu pour être partagé entre plusieurs workers de
    téléchargement. Il est thread-safe et supporte l'accès concurrent
    via un verrou SQLite (WAL mode activé par défaut).

    Lifecycle :
        >>> cache = DeduplicationCache(config)
        >>> await cache.start()   # Ouvre la BDD et initialise les tables
        >>> # ... utilisation ...
        >>> await cache.stop()    # Ferme proprement la BDD
    """

    # Schéma SQL de la base de données
    _SCHEMA_VERSION: ClassVar[int] = 1

    _CREATE_PAGES_TABLE: ClassVar[str] = """
        CREATE TABLE IF NOT EXISTS pages (
            hash TEXT PRIMARY KEY NOT NULL,
            size INTEGER NOT NULL,
            url TEXT,
            site_id TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            hit_count INTEGER NOT NULL DEFAULT 1
        ) WITHOUT ROWID;
    """

    _CREATE_CHAPTERS_TABLE: ClassVar[str] = """
        CREATE TABLE IF NOT EXISTS chapters (
            composite_key TEXT PRIMARY KEY NOT NULL,
            content_hash TEXT,
            output_path TEXT,
            pages_count INTEGER NOT NULL DEFAULT 0,
            bytes_total INTEGER NOT NULL DEFAULT 0,
            packaging_format TEXT,
            created_at TEXT NOT NULL,
            task_id TEXT
        ) WITHOUT ROWID;
    """

    _CREATE_META_TABLE: ClassVar[str] = """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY NOT NULL,
            value TEXT NOT NULL
        );
    """

    _CREATE_INDEXES: ClassVar[str] = """
        CREATE INDEX IF NOT EXISTS idx_pages_site_id ON pages(site_id);
        CREATE INDEX IF NOT EXISTS idx_pages_last_seen ON pages(last_seen_at);
        CREATE INDEX IF NOT EXISTS idx_chapters_created ON chapters(created_at);
        CREATE INDEX IF NOT EXISTS idx_chapters_format ON chapters(packaging_format);
    """

    def __init__(
        self,
        db_path: Path,
        *,
        strategy: DeduplicationStrategy = DeduplicationStrategy.BOTH,
        page_cache_size: int = 10_000,
        chapter_cache_size: int = 5_000,
        wal_mode: bool = True,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        """Initialise le cache de déduplication.

        Args:
            db_path: Chemin vers le fichier SQLite de persistance.
            strategy: Stratégie de déduplication à appliquer.
            page_cache_size: Taille du cache LRU en mémoire pour les pages.
            chapter_cache_size: Taille du cache LRU en mémoire pour les chapitres.
            wal_mode: Active le mode WAL pour de meilleures performances concurrentes.
            busy_timeout_ms: Timeout SQLite en ms en cas de BDD verrouillée.
        """
        if page_cache_size <= 0 or chapter_cache_size <= 0:
            raise ValueError("Cache sizes must be positive")

        self._db_path = db_path.resolve()
        self._strategy = strategy
        self._wal_mode = wal_mode
        self._busy_timeout_ms = busy_timeout_ms

        # Caches mémoire LRU
        self._page_cache: _LRUCache[PageFingerprint] = _LRUCache(page_cache_size)
        self._chapter_cache: _LRUCache[ChapterFingerprint] = _LRUCache(chapter_cache_size)

        # Connection SQLite (initialisée dans start())
        self._db: aiosqlite.Connection | None = None

        # Compteurs de statistiques (atomiques via lock)
        self._pages_skipped: int = 0
        self._chapters_skipped: int = 0
        self._bytes_saved: int = 0
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="deduplication")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Ouvre la base de données et initialise le schéma.

        Raises:
            DeduplicationDatabaseError: Si la BDD ne peut être ouverte ou initialisée.
        """
        if self._db is not None:
            self._logger.warning("DeduplicationCache déjà démarré, ignore")
            return

        try:
            # Créer le répertoire parent si nécessaire
            self._db_path.parent.mkdir(parents=True, exist_ok=True)

            self._db = await aiosqlite.connect(str(self._db_path))
            self._db.row_factory = sqlite3.Row

            # Optimisations performance
            if self._wal_mode:
                await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            await self._db.execute("PRAGMA synchronous=NORMAL")
            await self._db.execute("PRAGMA temp_store=MEMORY")
            await self._db.execute("PRAGMA cache_size=-8000")  # 8 Mo

            # Initialisation du schéma
            await self._db.execute(self._CREATE_PAGES_TABLE)
            await self._db.execute(self._CREATE_CHAPTERS_TABLE)
            await self._db.execute(self._CREATE_META_TABLE)
            await self._db.executescript(self._CREATE_INDEXES)

            # Versioning du schéma
            await self._db.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                ("schema_version", str(self._SCHEMA_VERSION)),
            )
            await self._db.commit()

            self._logger.info(
                "DeduplicationCache démarré: db={}, strategy={}",
                self._db_path,
                self._strategy.value,
            )

        except Exception as e:
            if self._db is not None:
                await self._db.close()
                self._db = None
            raise DeduplicationDatabaseError(
                f"Impossible d'initialiser la BDD de déduplication: {e}"
            ) from e

    async def stop(self) -> None:
        """Ferme proprement la base de données.

        Safe à appeler plusieurs fois. Libère les ressources et vide les caches.
        """
        if self._db is not None:
            try:
                await self._db.commit()
                await self._db.close()
            except Exception as e:
                self._logger.warning("Erreur lors de la fermeture de la BDD: {}", e)
            finally:
                self._db = None

        await self._page_cache.clear()
        await self._chapter_cache.clear()
        self._logger.info("DeduplicationCache arrêté")

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------------

    @property
    def strategy(self) -> DeduplicationStrategy:
        """Stratégie de déduplication active."""
        return self._strategy

    @property
    def is_started(self) -> bool:
        """Indique si le cache est démarré et opérationnel."""
        return self._db is not None

    # ------------------------------------------------------------------------
    # API Pages
    # ------------------------------------------------------------------------

    async def check_page_exists(self, page_hash: str) -> bool:
        """Vérifie si une page avec ce hash existe déjà.

        Consulte d'abord le cache mémoire (LRU), puis la BDD si nécessaire.
        Met à jour automatiquement le `last_seen_at` et `hit_count` en BDD.

        Args:
            page_hash: Hash SHA256 du contenu de la page (64 caractères hex).

        Returns:
            True si la page est déjà enregistrée, False sinon.

        Raises:
            DeduplicationDatabaseError: Si la BDD est inaccessible.
            ValueError: Si le hash est malformé.
        """
        self._ensure_started()
        self._validate_hash(page_hash)

        # 1. Cache mémoire (rapide)
        if await self._page_cache.contains(page_hash):
            return True

        # 2. BDD (lent)
        assert self._db is not None
        try:
            async with self._db.execute(
                "SELECT 1 FROM pages WHERE hash = ?",
                (page_hash,),
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return False

                # Hit en BDD → rafraîchir les métadonnées
                now = datetime.now(UTC).isoformat()
                await self._db.execute(
                    """
                    UPDATE pages
                    SET last_seen_at = ?, hit_count = hit_count + 1
                    WHERE hash = ?
                    """,
                    (now, page_hash),
                )
                await self._db.commit()

                # Préchauffer le cache mémoire
                await self._hydrate_page_cache(page_hash)
                return True

        except Exception as e:
            raise DeduplicationDatabaseError(
                f"Erreur lors de la vérification du hash page: {e}"
            ) from e

    async def register_page(
        self,
        page_hash: str,
        *,
        size: int,
        url: str | None = None,
        site_id: str | None = None,
    ) -> PageFingerprint:
        """Enregistre une page téléchargée dans le cache.

        Si la page existe déjà, incrémente `hit_count` et met à jour `last_seen_at`.
        Sinon, crée une nouvelle entrée.

        Args:
            page_hash: Hash SHA256 du contenu de la page.
            size: Taille du fichier en bytes.
            url: URL source originale (optionnelle).
            site_id: Site d'origine (optionnel).

        Returns:
            L'empreinte de la page (nouvelle ou mise à jour).

        Raises:
            DeduplicationDatabaseError: Si la BDD est inaccessible.
            ValueError: Si le hash ou la taille sont invalides.
        """
        self._ensure_started()
        self._validate_hash(page_hash)
        if size < 0:
            raise ValueError(f"Size must be non-negative, got {size}")

        now = datetime.now(UTC)
        assert self._db is not None

        try:
            # Upsert atomique
            await self._db.execute(
                """
                INSERT INTO pages (hash, size, url, site_id, first_seen_at, last_seen_at, hit_count)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(hash) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at,
                    hit_count = hit_count + 1,
                    url = COALESCE(excluded.url, pages.url),
                    site_id = COALESCE(excluded.site_id, pages.site_id)
                """,
                (page_hash, size, url, site_id, now.isoformat(), now.isoformat()),
            )
            await self._db.commit()

            fingerprint = PageFingerprint(
                hash=page_hash,
                size=size,
                url=url,
                site_id=site_id,
                first_seen_at=now,
                last_seen_at=now,
                hit_count=1,
            )
            await self._page_cache.put(page_hash, fingerprint)

            self._logger.trace(
                "Page enregistrée: hash={} size={} site={}",
                page_hash[:12],
                size,
                site_id,
            )
            return fingerprint

        except Exception as e:
            raise DeduplicationDatabaseError(
                f"Erreur lors de l'enregistrement de la page: {e}"
            ) from e

    async def record_page_skip(self, size: int) -> None:
        """Enregistre qu'une page a été évitée grâce à la déduplication.

        Met à jour les statistiques internes (pages_skipped, bytes_saved).

        Args:
            size: Taille de la page évitée (en bytes).
        """
        async with self._stats_lock:
            self._pages_skipped += 1
            self._bytes_saved += size

    # ------------------------------------------------------------------------
    # API Chapitres
    # ------------------------------------------------------------------------

    async def check_chapter_exists(self, composite_key: str) -> ChapterFingerprint | None:
        """Vérifie si un chapitre complet existe déjà.

        Args:
            composite_key: Clé composite unique du chapitre (voir `build_chapter_key`).

        Returns:
            L'empreinte du chapitre si existant, None sinon.

        Raises:
            DeduplicationDatabaseError: Si la BDD est inaccessible.
        """
        self._ensure_started()
        if not composite_key:
            raise ValueError("composite_key cannot be empty")

        # 1. Cache mémoire
        cached = await self._chapter_cache.get(composite_key)
        if cached is not None:
            return cached

        # 2. BDD
        assert self._db is not None
        try:
            async with self._db.execute(
                """
                SELECT composite_key, content_hash, output_path, pages_count,
                       bytes_total, packaging_format, created_at, task_id
                FROM chapters
                WHERE composite_key = ?
                """,
                (composite_key,),
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None

                fingerprint = ChapterFingerprint(
                    composite_key=row[0],
                    content_hash=row[1],
                    output_path=row[2],
                    pages_count=row[3],
                    bytes_total=row[4],
                    packaging_format=row[5],
                    created_at=datetime.fromisoformat(row[6]),
                    task_id=row[7],
                )
                await self._chapter_cache.put(composite_key, fingerprint)
                return fingerprint

        except Exception as e:
            raise DeduplicationDatabaseError(
                f"Erreur lors de la vérification du chapitre: {e}"
            ) from e

    async def register_chapter(
        self,
        composite_key: str,
        *,
        content_hash: str | None = None,
        output_path: Path | str | None = None,
        pages_count: int = 0,
        bytes_total: int = 0,
        packaging_format: str | None = None,
        task_id: UUID | str | None = None,
    ) -> ChapterFingerprint:
        """Enregistre un chapitre téléchargé et empaqueté avec succès.

        Args:
            composite_key: Clé composite unique du chapitre.
            content_hash: Hash SHA256 du fichier final (optionnel).
            output_path: Chemin du fichier de sortie (optionnel).
            pages_count: Nombre de pages du chapitre.
            bytes_total: Taille totale en bytes.
            packaging_format: Format d'empaquetage (cbz, cbr, pdf, etc.).
            task_id: UUID de la tâche de téléchargement associée.

        Returns:
            L'empreinte du chapitre enregistrée.

        Raises:
            DeduplicationDatabaseError: Si la BDD est inaccessible.
            ValueError: Si la clé composite est vide ou le hash malformé.
        """
        self._ensure_started()
        if not composite_key:
            raise ValueError("composite_key cannot be empty")
        if content_hash is not None:
            self._validate_hash(content_hash)

        now = datetime.now(UTC)
        task_id_str = str(task_id) if task_id is not None else None
        output_path_str = str(output_path) if output_path is not None else None

        assert self._db is not None
        try:
            await self._db.execute(
                """
                INSERT INTO chapters
                    (composite_key, content_hash, output_path, pages_count,
                     bytes_total, packaging_format, created_at, task_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(composite_key) DO UPDATE SET
                    content_hash = COALESCE(excluded.content_hash, chapters.content_hash),
                    output_path = COALESCE(excluded.output_path, chapters.output_path),
                    pages_count = MAX(chapters.pages_count, excluded.pages_count),
                    bytes_total = MAX(chapters.bytes_total, excluded.bytes_total),
                    packaging_format = COALESCE(excluded.packaging_format, chapters.packaging_format),
                    task_id = COALESCE(excluded.task_id, chapters.task_id)
                """,
                (
                    composite_key,
                    content_hash,
                    output_path_str,
                    pages_count,
                    bytes_total,
                    packaging_format,
                    now.isoformat(),
                    task_id_str,
                ),
            )
            await self._db.commit()

            fingerprint = ChapterFingerprint(
                composite_key=composite_key,
                content_hash=content_hash,
                output_path=output_path_str,
                pages_count=pages_count,
                bytes_total=bytes_total,
                packaging_format=packaging_format,
                created_at=now,
                task_id=task_id_str,
            )
            await self._chapter_cache.put(composite_key, fingerprint)

            self._logger.debug(
                "Chapitre enregistré: key={} pages={} size={} format={}",
                composite_key,
                pages_count,
                bytes_total,
                packaging_format,
            )
            return fingerprint

        except Exception as e:
            raise DeduplicationDatabaseError(
                f"Erreur lors de l'enregistrement du chapitre: {e}"
            ) from e

    async def record_chapter_skip(self, bytes_total: int) -> None:
        """Enregistre qu'un chapitre a été évité grâce à la déduplication.

        Args:
            bytes_total: Taille du chapitre évité (en bytes).
        """
        async with self._stats_lock:
            self._chapters_skipped += 1
            self._bytes_saved += bytes_total

    # ------------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------------

    async def clear(self, *, pages: bool = True, chapters: bool = True) -> int:
        """Vide tout ou partie du cache de déduplication.

        Args:
            pages: Si True, vide la table des pages.
            chapters: Si True, vide la table des chapitres.

        Returns:
            Nombre total d'entrées supprimées.
        """
        self._ensure_started()
        assert self._db is not None
        deleted = 0

        try:
            if pages:
                async with self._db.execute("SELECT COUNT(*) FROM pages") as cursor:
                    row = await cursor.fetchone()
                    deleted += row[0] if row else 0
                await self._db.execute("DELETE FROM pages")
                await self._page_cache.clear()

            if chapters:
                async with self._db.execute("SELECT COUNT(*) FROM chapters") as cursor:
                    row = await cursor.fetchone()
                    deleted += row[0] if row else 0
                await self._db.execute("DELETE FROM chapters")
                await self._chapter_cache.clear()

            await self._db.commit()

            async with self._stats_lock:
                self._pages_skipped = 0
                self._chapters_skipped = 0
                self._bytes_saved = 0

            self._logger.info(
                "Cache de déduplication vidé: {} entrées supprimées (pages={}, chapters={})",
                deleted,
                pages,
                chapters,
            )
            return deleted

        except Exception as e:
            raise DeduplicationDatabaseError(
                f"Erreur lors du vidage du cache: {e}"
            ) from e

    async def vacuum(self) -> None:
        """Compacte la base de données SQLite pour récupérer l'espace inutilisé.

        À utiliser périodiquement (ex: une fois par semaine) pour maintenir
        les performances. Opération bloquante — à appeler hors des pics d'usage.
        """
        self._ensure_started()
        assert self._db is not None
        try:
            await self._db.execute("VACUUM")
            self._logger.debug("VACUUM SQLite terminé")
        except Exception as e:
            raise DeduplicationDatabaseError(f"Erreur lors du VACUUM: {e}") from e

    async def prune_old_entries(self, *, max_age_days: int = 90) -> int:
        """Supprime les entrées plus anciennes que `max_age_days`.

        Utile pour éviter une croissance infinie de la BDD sur les installations
        long-terme. Les entrées récentes sont conservées.

        Args:
            max_age_days: Âge maximum en jours (défaut: 90).

        Returns:
            Nombre d'entrées supprimées.
        """
        self._ensure_started()
        if max_age_days <= 0:
            raise ValueError("max_age_days must be positive")

        assert self._db is not None
        cutoff = datetime.now(UTC).timestamp() - (max_age_days * 86_400)
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=UTC).isoformat()
        deleted = 0

        try:
            async with self._db.execute(
                "DELETE FROM pages WHERE last_seen_at < ?",
                (cutoff_iso,),
            ) as cursor:
                deleted += cursor.rowcount

            async with self._db.execute(
                "DELETE FROM chapters WHERE created_at < ?",
                (cutoff_iso,),
            ) as cursor:
                deleted += cursor.rowcount

            await self._db.commit()
            self._logger.info(
                "Prune terminé: {} entrées supprimées (max_age={} jours)",
                deleted,
                max_age_days,
            )
            return deleted

        except Exception as e:
            raise DeduplicationDatabaseError(f"Erreur lors du prune: {e}") from e

    # ------------------------------------------------------------------------
    # Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> DeduplicationStats:
        """Retourne les statistiques agrégées du système de déduplication.

        Returns:
            Objet `DeduplicationStats` avec tous les compteurs à jour.
        """
        self._ensure_started()
        assert self._db is not None

        try:
            async with self._db.execute("SELECT COUNT(*) FROM pages") as cursor:
                row = await cursor.fetchone()
                total_pages = row[0] if row else 0

            async with self._db.execute("SELECT COUNT(*) FROM chapters") as cursor:
                row = await cursor.fetchone()
                total_chapters = row[0] if row else 0

            # Taille de la BDD
            db_size = 0
            if self._db_path.exists():
                db_size = self._db_path.stat().st_size

            async with self._stats_lock:
                pages_skipped = self._pages_skipped
                chapters_skipped = self._chapters_skipped
                bytes_saved = self._bytes_saved

            # Taux de hit global (moyenne des deux caches)
            cache_hit_rate = (
                self._page_cache.hit_rate + self._chapter_cache.hit_rate
            ) / 2.0

            return DeduplicationStats(
                total_pages_registered=total_pages,
                total_chapters_registered=total_chapters,
                pages_skipped=pages_skipped,
                chapters_skipped=chapters_skipped,
                bytes_saved=bytes_saved,
                cache_hit_rate=cache_hit_rate,
                database_size_bytes=db_size,
            )

        except Exception as e:
            raise DeduplicationDatabaseError(
                f"Erreur lors de la récupération des stats: {e}"
            ) from e

    # ------------------------------------------------------------------------
    # Méthodes internes
    # ------------------------------------------------------------------------

    def _ensure_started(self) -> None:
        """Vérifie que le cache est démarré.

        Raises:
            DeduplicationError: Si le cache n'est pas démarré.
        """
        if self._db is None:
            raise DeduplicationError(
                "DeduplicationCache must be started before use. Call await cache.start()"
            )

    @staticmethod
    def _validate_hash(page_hash: str) -> None:
        """Valide le format d'un hash SHA256.

        Raises:
            ValueError: Si le hash est malformé.
        """
        if not page_hash or len(page_hash) != 64:
            raise ValueError(
                f"Invalid SHA256 hash: expected 64 hex chars, got {len(page_hash) if page_hash else 0}"
            )
        try:
            int(page_hash, 16)
        except ValueError as e:
            raise ValueError(f"Invalid SHA256 hash: not hexadecimal: {page_hash}") from e

    async def _hydrate_page_cache(self, page_hash: str) -> None:
        """Précharge une page depuis la BDD vers le cache mémoire."""
        assert self._db is not None
        try:
            async with self._db.execute(
                """
                SELECT hash, size, url, site_id, first_seen_at, last_seen_at, hit_count
                FROM pages WHERE hash = ?
                """,
                (page_hash,),
            ) as cursor:
                row = await cursor.fetchone()
                if row is not None:
                    fingerprint = PageFingerprint(
                        hash=row[0],
                        size=row[1],
                        url=row[2],
                        site_id=row[3],
                        first_seen_at=datetime.fromisoformat(row[4]),
                        last_seen_at=datetime.fromisoformat(row[5]),
                        hit_count=row[6],
                    )
                    await self._page_cache.put(page_hash, fingerprint)
        except Exception as e:
            self._logger.warning(
                "Impossible de précharger la page {} dans le cache: {}",
                page_hash[:12],
                e,
            )

    def __repr__(self) -> str:
        status = "started" if self._db is not None else "stopped"
        return (
            f"<DeduplicationCache db={self._db_path.name} "
            f"strategy={self._strategy.value} status={status}>"
        )
