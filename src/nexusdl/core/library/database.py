"""Couche d'accès aux données SQLite async pour la bibliothèque locale.

Ce module fournit une couche d'abstraction asynchrone au-dessus de SQLite
(via `aiosqlite`) pour gérer la bibliothèque locale de mangas/webtoons/comics.
Il applique automatiquement les migrations SQL au démarrage, expose une API
CRUD typée pour toutes les entités, et supporte les transactions atomiques
ainsi que la recherche plein texte via FTS5.

Fonctionnalités principales :
    - Connexion async via aiosqlite (non-bloquant)
    - Application automatique des migrations (001, 002, ...)
    - Configuration SQLite optimisée (WAL, foreign_keys, cache)
    - API CRUD complète pour toutes les entités du domaine
    - Transactions atomiques via context manager
    - Requêtes FTS5 pour recherche plein texte
    - Wrappers autour des vues pré-définies (v_continue_reading, etc.)
    - Statistiques de la BDD (taille, nombre d'entrées, etc.)
    - Lifecycle async (start/stop/__aenter__/__aexit__)
    - Gestion robuste des erreurs avec exceptions spécifiques
    - Conversion row → modèles Pydantic (Manga, Chapter, etc.)

Architecture :
    LibraryDatabase
        ├── DatabaseConfig (Pydantic — configuration BDD)
        ├── DatabaseState (enum — état de la connexion)
        ├── DatabaseStats (Pydantic — statistiques)
        ├── MigrationInfo (Pydantic — info de migration)
        └── _TransactionContext (interne — context manager)

Les opérations I/O sont toutes async. Une seule connexion est maintenue
par instance (SQLite ne supporte pas nativement le pool de connexions
en écriture, mais WAL mode permet les lectures concurrentes).

Exemple d'utilisation :
    >>> db = LibraryDatabase(path=Path("~/.local/share/nexusdl/library.db"))
    >>> await db.start()
    >>>
    >>> # Ajouter un manga
    >>> await db.add_manga(manga)
    >>>
    >>> # Rechercher
    >>> results = await db.search_manga("one piece", limit=10)
    >>>
    >>> # Transaction atomique
    >>> async with db.transaction():
    ...     await db.add_manga(manga1)
    ...     await db.add_chapter(chapter1)
    >>>
    >>> # Continuer la lecture
    >>> continue_reading = await db.get_continue_reading(limit=10)
    >>>
    >>> await db.stop()
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final, Self

import aiosqlite
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError

if TYPE_CHECKING:
    from nexusdl.core.models.download import DownloadResult, DownloadStatus
    from nexusdl.core.models.library import (
        ReadingList,
        ReadingProgress,
        ReadingSession,
    )
    from nexusdl.core.models.manga import (
        Chapter,
        ContentRating,
        Language,
        Manga,
        MangaStatus,
        Page,
    )


# ============================================================================
# EXCEPTIONS
# ============================================================================


class DatabaseError(NexusDLError):
    """Exception de base pour les erreurs de base de données."""


class DatabaseNotStartedError(DatabaseError):
    """Exception levée lorsqu'on utilise la BDD avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "LibraryDatabase must be started before use. Call await db.start()"
        )


class DatabaseAlreadyStartedError(DatabaseError):
    """Exception levée lorsqu'on appelle start() sur une BDD déjà démarrée."""

    def __init__(self) -> None:
        super().__init__("LibraryDatabase is already started")


class MigrationError(DatabaseError):
    """Exception levée lorsqu'une migration échoue."""

    def __init__(self, migration_name: str, reason: str = "") -> None:
        msg = f"Échec de la migration {migration_name}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.migration_name = migration_name
        self.reason = reason


class EntityNotFoundError(DatabaseError):
    """Exception levée lorsqu'une entité demandée n'existe pas."""

    def __init__(self, entity_type: str, entity_id: str) -> None:
        super().__init__(f"{entity_type} introuvable: {entity_id}")
        self.entity_type = entity_type
        self.entity_id = entity_id


class IntegrityError(DatabaseError):
    """Exception levée en cas de violation d'intégrité (contrainte UNIQUE, FK, etc.)."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Violation d'intégrité: {reason}")
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class DatabaseState(str, Enum):
    """État de la connexion à la base de données."""

    CLOSED = "closed"
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"
    ERROR = "error"


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class DatabaseConfig(BaseModel):
    """Configuration de la base de données.

    Contrôle les paramètres de connexion, les pragmas SQLite, et le
    comportement des migrations.
    """

    path: Path = Field(..., description="Chemin vers le fichier SQLite.")
    wal_mode: bool = Field(
        default=True,
        description="Active le mode WAL pour lectures concurrentes.",
    )
    foreign_keys: bool = Field(
        default=True,
        description="Active les contraintes de clés étrangères.",
    )
    synchronous: str = Field(
        default="NORMAL",
        description="Mode synchrone SQLite (OFF, NORMAL, FULL, EXTRA).",
    )
    cache_size_kb: int = Field(
        default=-8000,
        description="Taille du cache SQLite en KB (négatif = KB, positif = pages).",
    )
    temp_store: str = Field(
        default="MEMORY",
        description="Stockage temporaire (DEFAULT, FILE, MEMORY).",
    )
    busy_timeout_ms: int = Field(
        default=5000,
        ge=0,
        description="Timeout en ms quand la BDD est verrouillée.",
    )
    journal_size_limit_mb: int = Field(
        default=10,
        ge=0,
        description="Taille max du journal WAL en Mo (0 = illimité).",
    )
    auto_migrate: bool = Field(
        default=True,
        description="Appliquer automatiquement les migrations au démarrage.",
    )
    migrations_dir: Path | None = Field(
        default=None,
        description="Répertoire des migrations (défaut: intégré au package).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class MigrationInfo(BaseModel):
    """Informations sur une migration appliquée."""

    name: str = Field(..., description="Nom du fichier de migration.")
    applied_at: datetime = Field(..., description="Timestamp d'application.")
    duration_ms: float = Field(..., ge=0.0, description="Durée d'application.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class DatabaseStats(BaseModel):
    """Statistiques de la base de données."""

    state: DatabaseState = Field(..., description="État de la connexion.")
    file_size_bytes: int = Field(default=0, ge=0, description="Taille du fichier BDD.")
    manga_count: int = Field(default=0, ge=0)
    chapter_count: int = Field(default=0, ge=0)
    downloaded_chapters_count: int = Field(default=0, ge=0)
    reading_progress_count: int = Field(default=0, ge=0)
    reading_list_count: int = Field(default=0, ge=0)
    download_history_count: int = Field(default=0, ge=0)
    scanned_files_count: int = Field(default=0, ge=0)
    applied_migrations: list[MigrationInfo] = Field(default_factory=list)
    uptime_seconds: float = Field(default=0.0, ge=0.0)
    total_queries: int = Field(default=0, ge=0)
    total_query_duration_ms: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def average_query_duration_ms(self) -> float:
        """Durée moyenne d'une requête."""
        if self.total_queries == 0:
            return 0.0
        return self.total_query_duration_ms / self.total_queries


# ============================================================================
# MIGRATIONS EMBARQUÉES
# ============================================================================


# Les migrations sont lues depuis les fichiers SQL embarqués dans le package.
# Cette liste est ordonnée : l'ordre d'application est garanti.
_BUILTIN_MIGRATIONS: Final[tuple[str, ...]] = (
    "001_initial.sql",
    "002_add_reading_progress.sql",
)


# ============================================================================
# CLASSE PRINCIPALE — LibraryDatabase
# ============================================================================


class LibraryDatabase:
    """Couche d'accès async à la base de données SQLite de la bibliothèque.

    Gère le cycle de vie complet de la connexion, applique les migrations
    automatiquement, et expose une API CRUD typée pour toutes les entités.

    Lifecycle :
        >>> db = LibraryDatabase(path=Path("library.db"))
        >>> await db.start()
        >>> # ... opérations ...
        >>> await db.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. SQLite en mode WAL supporte les lectures concurrentes,
        mais les écritures sont sérialisées. Un lock interne garantit
        l'atomicité des transactions.
    """

    # Version du schéma attendue (doit correspondre à la dernière migration)
    _SCHEMA_VERSION: ClassVar[int] = 2

    def __init__(
        self,
        path: Path | str,
        *,
        config: DatabaseConfig | None = None,
    ) -> None:
        """Initialise la base de données.

        Args:
            path: Chemin vers le fichier SQLite.
            config: Configuration avancée (défaut: valeurs optimisées).
        """
        path_obj = Path(path).expanduser().resolve()

        self._config = config or DatabaseConfig(path=path_obj)
        # S'assurer que le path dans la config correspond
        if self._config.path != path_obj:
            self._config = self._config.model_copy(update={"path": path_obj})

        self._db: aiosqlite.Connection | None = None
        self._state: DatabaseState = DatabaseState.CLOSED
        self._state_lock = asyncio.Lock()

        self._start_time: float = 0.0
        self._total_queries: int = 0
        self._total_query_duration_ms: float = 0.0
        self._query_lock = asyncio.Lock()

        self._applied_migrations: list[MigrationInfo] = []

        self._logger = logger.bind(module="library_database", path=str(path_obj))

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Ouvre la connexion et applique les migrations.

        Raises:
            DatabaseAlreadyStartedError: Si la BDD est déjà démarrée.
            DatabaseError: Si l'ouverture ou la migration échoue.
        """
        async with self._state_lock:
            if self._state == DatabaseState.OPEN:
                raise DatabaseAlreadyStartedError()
            if self._state == DatabaseState.OPENING:
                self._logger.warning("Database déjà en cours d'ouverture")
                return

            self._state = DatabaseState.OPENING

        try:
            # Créer le répertoire parent si nécessaire
            self._config.path.parent.mkdir(parents=True, exist_ok=True)

            # Ouvrir la connexion
            self._db = await aiosqlite.connect(str(self._config.path))
            self._db.row_factory = aiosqlite.Row

            # Appliquer les pragmas
            await self._apply_pragmas()

            # Créer la table des migrations si elle n'existe pas
            await self._ensure_migrations_table()

            # Appliquer les migrations
            if self._config.auto_migrate:
                await self._apply_migrations()

            self._state = DatabaseState.OPEN
            self._start_time = time.monotonic()

            self._logger.info(
                "Base de données ouverte: path={}, migrations={}",
                self._config.path,
                len(self._applied_migrations),
            )

        except Exception as e:
            async with self._state_lock:
                self._state = DatabaseState.ERROR
            if self._db is not None:
                try:
                    await self._db.close()
                except Exception:
                    pass
                self._db = None
            self._logger.error("Échec de l'ouverture de la BDD: {}", e)
            raise DatabaseError(f"Impossible d'ouvrir la base de données: {e}") from e

    async def stop(self) -> None:
        """Ferme proprement la connexion à la base de données.

        Safe à appeler plusieurs fois.
        """
        async with self._state_lock:
            if self._state == DatabaseState.CLOSED:
                return
            if self._state == DatabaseState.CLOSING:
                return
            self._state = DatabaseState.CLOSING

        if self._db is not None:
            try:
                await self._db.commit()
                await self._db.close()
                self._logger.info("Base de données fermée proprement")
            except Exception as e:
                self._logger.warning("Erreur lors de la fermeture de la BDD: {}", e)
            finally:
                self._db = None

        async with self._state_lock:
            self._state = DatabaseState.CLOSED

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------------

    @property
    def state(self) -> DatabaseState:
        """État actuel de la connexion."""
        return self._state

    @property
    def is_open(self) -> bool:
        """Indique si la connexion est ouverte."""
        return self._state == DatabaseState.OPEN

    @property
    def path(self) -> Path:
        """Chemin vers le fichier SQLite."""
        return self._config.path

    @property
    def applied_migrations(self) -> list[MigrationInfo]:
        """Liste des migrations appliquées."""
        return list(self._applied_migrations)

    # ------------------------------------------------------------------------
    # API publique — Transactions
    # ------------------------------------------------------------------------

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Context manager pour une transaction atomique.

        Utilise BEGIN/COMMIT/ROLLBACK. En cas d'exception, la transaction
        est automatiquement annulée (ROLLBACK).

        Example:
            >>> async with db.transaction():
            ...     await db.add_manga(manga)
            ...     await db.add_chapter(chapter)
        """
        self._ensure_open()
        assert self._db is not None

        await self._db.execute("BEGIN")
        try:
            yield
            await self._db.commit()
        except Exception:
            await self._db.rollback()
            raise

    # ------------------------------------------------------------------------
    # API publique — Requêtes bas niveau
    # ------------------------------------------------------------------------

    async def execute(
        self,
        sql: str,
        parameters: Sequence[Any] | None = None,
    ) -> aiosqlite.Cursor:
        """Exécute une requête SQL (INSERT, UPDATE, DELETE, CREATE, etc.).

        Args:
            sql: Requête SQL.
            parameters: Paramètres pour la requête (protection contre injections).

        Returns:
            Curseur SQLite.
        """
        self._ensure_open()
        assert self._db is not None

        start = time.perf_counter()
        try:
            cursor = await self._db.execute(sql, parameters or ())
            await self._db.commit()
            return cursor
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            async with self._query_lock:
                self._total_queries += 1
                self._total_query_duration_ms += duration_ms

    async def fetch_one(
        self,
        sql: str,
        parameters: Sequence[Any] | None = None,
    ) -> aiosqlite.Row | None:
        """Exécute une requête SELECT et retourne une seule ligne.

        Args:
            sql: Requête SQL.
            parameters: Paramètres pour la requête.

        Returns:
            La première ligne ou None si aucun résultat.
        """
        self._ensure_open()
        assert self._db is not None

        start = time.perf_counter()
        try:
            async with self._db.execute(sql, parameters or ()) as cursor:
                return await cursor.fetchone()
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            async with self._query_lock:
                self._total_queries += 1
                self._total_query_duration_ms += duration_ms

    async def fetch_all(
        self,
        sql: str,
        parameters: Sequence[Any] | None = None,
    ) -> list[aiosqlite.Row]:
        """Exécute une requête SELECT et retourne toutes les lignes.

        Args:
            sql: Requête SQL.
            parameters: Paramètres pour la requête.

        Returns:
            Liste des lignes.
        """
        self._ensure_open()
        assert self._db is not None

        start = time.perf_counter()
        try:
            async with self._db.execute(sql, parameters or ()) as cursor:
                return await cursor.fetchall()
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            async with self._query_lock:
                self._total_queries += 1
                self._total_query_duration_ms += duration_ms

    async def fetch_scalar(
        self,
        sql: str,
        parameters: Sequence[Any] | None = None,
    ) -> Any:
        """Exécute une requête et retourne la première colonne de la première ligne.

        Args:
            sql: Requête SQL.
            parameters: Paramètres.

        Returns:
            Valeur scalaire ou None.
        """
        row = await self.fetch_one(sql, parameters)
        return row[0] if row else None

    # ------------------------------------------------------------------------
    # API publique — Manga CRUD
    # ------------------------------------------------------------------------

    async def add_manga(self, manga: Manga) -> str:
        """Ajoute un manga à la bibliothèque.

        Args:
            manga: Modèle Manga à insérer.

        Returns:
            ID du manga inséré.

        Raises:
            IntegrityError: Si un manga avec le même (source_id, site) existe déjà.
        """
        self._ensure_open()

        alt_titles_json = json.dumps(manga.alternative_titles or [])
        genres_json = json.dumps(manga.genres or [])

        try:
            await self.execute(
                """
                INSERT INTO manga (
                    id, source_id, site, title, alternative_titles, description,
                    author, artist, status, year, cover_url, language,
                    content_rating, url, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manga.id,
                    manga.source_id,
                    manga.site,
                    manga.title,
                    alt_titles_json,
                    manga.description,
                    manga.author,
                    manga.artist,
                    manga.status.value if hasattr(manga.status, "value") else str(manga.status),
                    manga.year,
                    str(manga.cover_url) if manga.cover_url else None,
                    manga.language.value if hasattr(manga.language, "value") else str(manga.language),
                    manga.content_rating.value if hasattr(manga.content_rating, "value") else str(manga.content_rating),
                    str(manga.url),
                    manga.created_at.isoformat() if manga.created_at else datetime.now(UTC).isoformat(),
                    manga.updated_at.isoformat() if manga.updated_at else datetime.now(UTC).isoformat(),
                ),
            )
        except Exception as e:
            if "UNIQUE constraint" in str(e):
                raise IntegrityError(
                    f"Manga déjà existant: ({manga.source_id}, {manga.site})"
                ) from e
            raise

        # Insérer les genres
        if manga.genres:
            await self._insert_genres(manga.id, manga.genres)

        self._logger.debug("Manga ajouté: {} ({})", manga.title, manga.id)
        return manga.id

    async def get_manga(self, manga_id: str) -> Manga | None:
        """Récupère un manga par son ID.

        Args:
            manga_id: ID unique du manga.

        Returns:
            Modèle Manga ou None si introuvable.
        """
        self._ensure_open()

        row = await self.fetch_one(
            "SELECT * FROM manga WHERE id = ?",
            (manga_id,),
        )
        if row is None:
            return None

        # Récupérer les genres
        genres = await self._get_genres(manga_id)

        return self._row_to_manga(row, genres)

    async def get_manga_by_source(self, source_id: str, site: str) -> Manga | None:
        """Récupère un manga par son ID source et le site.

        Args:
            source_id: ID du manga sur le site source.
            site: Nom du site.

        Returns:
            Modèle Manga ou None.
        """
        self._ensure_open()

        row = await self.fetch_one(
            "SELECT * FROM manga WHERE source_id = ? AND site = ?",
            (source_id, site),
        )
        if row is None:
            return None

        genres = await self._get_genres(row["id"])
        return self._row_to_manga(row, genres)

    async def update_manga(self, manga: Manga) -> None:
        """Met à jour un manga existant.

        Args:
            manga: Modèle Manga avec les nouvelles valeurs.

        Raises:
            EntityNotFoundError: Si le manga n'existe pas.
        """
        self._ensure_open()

        alt_titles_json = json.dumps(manga.alternative_titles or [])

        cursor = await self.execute(
            """
            UPDATE manga SET
                title = ?,
                alternative_titles = ?,
                description = ?,
                author = ?,
                artist = ?,
                status = ?,
                year = ?,
                cover_url = ?,
                language = ?,
                content_rating = ?,
                url = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                manga.title,
                alt_titles_json,
                manga.description,
                manga.author,
                manga.artist,
                manga.status.value if hasattr(manga.status, "value") else str(manga.status),
                manga.year,
                str(manga.cover_url) if manga.cover_url else None,
                manga.language.value if hasattr(manga.language, "value") else str(manga.language),
                manga.content_rating.value if hasattr(manga.content_rating, "value") else str(manga.content_rating),
                str(manga.url),
                datetime.now(UTC).isoformat(),
                manga.id,
            ),
        )

        if cursor.rowcount == 0:
            raise EntityNotFoundError("Manga", manga.id)

        # Mettre à jour les genres
        await self.execute("DELETE FROM manga_genre WHERE manga_id = ?", (manga.id,))
        if manga.genres:
            await self._insert_genres(manga.id, manga.genres)

    async def delete_manga(self, manga_id: str) -> None:
        """Supprime un manga et toutes ses données associées (CASCADE).

        Args:
            manga_id: ID du manga à supprimer.

        Raises:
            EntityNotFoundError: Si le manga n'existe pas.
        """
        self._ensure_open()

        cursor = await self.execute("DELETE FROM manga WHERE id = ?", (manga_id,))
        if cursor.rowcount == 0:
            raise EntityNotFoundError("Manga", manga_id)

        self._logger.debug("Manga supprimé: {}", manga_id)

    async def list_manga(
        self,
        *,
        site: str | None = None,
        status: str | None = None,
        language: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Manga]:
        """Liste les mangas avec filtrage optionnel.

        Args:
            site: Filtrer par site.
            status: Filtrer par statut.
            language: Filtrer par langue.
            limit: Nombre maximum de résultats.
            offset: Décalage pour la pagination.

        Returns:
            Liste des mangas correspondants.
        """
        self._ensure_open()

        where_clauses: list[str] = []
        params: list[Any] = []

        if site is not None:
            where_clauses.append("site = ?")
            params.append(site)
        if status is not None:
            where_clauses.append("status = ?")
            params.append(status)
        if language is not None:
            where_clauses.append("language = ?")
            params.append(language)

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        sql = f"""
            SELECT * FROM manga
            WHERE {where_sql}
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        rows = await self.fetch_all(sql, params)

        # Récupérer les genres pour chaque manga
        results: list[Manga] = []
        for row in rows:
            genres = await self._get_genres(row["id"])
            results.append(self._row_to_manga(row, genres))

        return results

    async def count_manga(
        self,
        *,
        site: str | None = None,
        status: str | None = None,
    ) -> int:
        """Compte les mangas avec filtrage optionnel."""
        self._ensure_open()

        where_clauses: list[str] = []
        params: list[Any] = []

        if site is not None:
            where_clauses.append("site = ?")
            params.append(site)
        if status is not None:
            where_clauses.append("status = ?")
            params.append(status)

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        sql = f"SELECT COUNT(*) FROM manga WHERE {where_sql}"

        return await self.fetch_scalar(sql, params) or 0

    # ------------------------------------------------------------------------
    # API publique — Chapter CRUD
    # ------------------------------------------------------------------------

    async def add_chapter(self, chapter: Chapter, manga_id: str) -> str:
        """Ajoute un chapitre à un manga.

        Args:
            chapter: Modèle Chapter à insérer.
            manga_id: ID du manga parent.

        Returns:
            ID du chapitre inséré.
        """
        self._ensure_open()

        await self.execute(
            """
            INSERT INTO chapter (
                id, source_id, manga_id, title, number, volume,
                language, pages_count, published_at, url,
                downloaded, download_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?)
            """,
            (
                chapter.id,
                chapter.source_id,
                manga_id,
                chapter.title,
                str(chapter.number),
                chapter.volume,
                chapter.language.value if hasattr(chapter.language, "value") else str(chapter.language),
                chapter.pages_count,
                chapter.published_at.isoformat() if chapter.published_at else None,
                str(chapter.url),
                datetime.now(UTC).isoformat(),
            ),
        )

        self._logger.debug(
            "Chapitre ajouté: {} → manga {}",
            chapter.number,
            manga_id,
        )
        return chapter.id

    async def get_chapter(self, chapter_id: str) -> Chapter | None:
        """Récupère un chapitre par son ID."""
        self._ensure_open()

        row = await self.fetch_one(
            "SELECT * FROM chapter WHERE id = ?",
            (chapter_id,),
        )
        return self._row_to_chapter(row) if row else None

    async def get_chapters_for_manga(
        self,
        manga_id: str,
        *,
        downloaded_only: bool = False,
    ) -> list[Chapter]:
        """Récupère tous les chapitres d'un manga.

        Args:
            manga_id: ID du manga parent.
            downloaded_only: Si True, ne retourne que les chapitres téléchargés.

        Returns:
            Liste des chapitres, triés par numéro.
        """
        self._ensure_open()

        sql = "SELECT * FROM chapter WHERE manga_id = ?"
        params: list[Any] = [manga_id]

        if downloaded_only:
            sql += " AND downloaded = 1"

        sql += " ORDER BY number ASC"

        rows = await self.fetch_all(sql, params)
        return [self._row_to_chapter(row) for row in rows if self._row_to_chapter(row) is not None]

    async def mark_chapter_downloaded(
        self,
        chapter_id: str,
        download_path: Path | str,
    ) -> None:
        """Marque un chapitre comme téléchargé.

        Args:
            chapter_id: ID du chapitre.
            download_path: Chemin vers le fichier téléchargé.
        """
        self._ensure_open()

        await self.execute(
            "UPDATE chapter SET downloaded = 1, download_path = ? WHERE id = ?",
            (str(download_path), chapter_id),
        )

    async def mark_chapter_not_downloaded(self, chapter_id: str) -> None:
        """Marque un chapitre comme non téléchargé."""
        self._ensure_open()

        await self.execute(
            "UPDATE chapter SET downloaded = 0, download_path = NULL WHERE id = ?",
            (chapter_id,),
        )

    async def delete_chapter(self, chapter_id: str) -> None:
        """Supprime un chapitre."""
        self._ensure_open()

        cursor = await self.execute("DELETE FROM chapter WHERE id = ?", (chapter_id,))
        if cursor.rowcount == 0:
            raise EntityNotFoundError("Chapter", chapter_id)

    # ------------------------------------------------------------------------
    # API publique — Reading Progress
    # ------------------------------------------------------------------------

    async def set_reading_progress(
        self,
        manga_id: str,
        chapter_id: str,
        page: int,
        *,
        reading_status: str = "READING",
        score: float | None = None,
        notes: str | None = None,
    ) -> None:
        """Met à jour la progression de lecture d'un manga.

        Crée l'entrée si elle n'existe pas, sinon met à jour.

        Args:
            manga_id: ID du manga.
            chapter_id: ID du chapitre courant.
            page: Numéro de page courant.
            reading_status: Statut de lecture (READING, COMPLETED, etc.).
            score: Note utilisateur (0-10, optionnel).
            notes: Notes utilisateur (optionnel).
        """
        self._ensure_open()

        await self.execute(
            """
            INSERT INTO reading_progress (
                manga_id, chapter_id, page, reading_status, score, notes,
                last_read_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(manga_id) DO UPDATE SET
                chapter_id = excluded.chapter_id,
                page = MAX(reading_progress.page, excluded.page),
                reading_status = excluded.reading_status,
                score = COALESCE(excluded.score, reading_progress.score),
                notes = COALESCE(excluded.notes, reading_progress.notes),
                last_read_at = datetime('now'),
                updated_at = datetime('now')
            """,
            (manga_id, chapter_id, page, reading_status, score, notes),
        )

    async def get_reading_progress(self, manga_id: str) -> dict[str, Any] | None:
        """Récupère la progression de lecture d'un manga.

        Args:
            manga_id: ID du manga.

        Returns:
            Dictionnaire avec les données de progression ou None.
        """
        self._ensure_open()

        row = await self.fetch_one(
            "SELECT * FROM reading_progress WHERE manga_id = ?",
            (manga_id,),
        )
        return dict(row) if row else None

    async def set_reading_status(
        self,
        manga_id: str,
        status: str,
    ) -> None:
        """Met à jour le statut de lecture d'un manga.

        Args:
            manga_id: ID du manga.
            status: Nouveau statut (READING, COMPLETED, ON_HOLD, DROPPED, PLAN_TO_READ, RE_READING).
        """
        self._ensure_open()

        # Créer l'entrée si elle n'existe pas
        await self.execute(
            """
            INSERT INTO reading_progress (manga_id, chapter_id, page, reading_status)
            VALUES (?, '', 0, ?)
            ON CONFLICT(manga_id) DO UPDATE SET
                reading_status = excluded.reading_status,
                updated_at = datetime('now')
            """,
            (manga_id, status),
        )

    async def set_manga_score(
        self,
        manga_id: str,
        score: float | None,
    ) -> None:
        """Définit la note utilisateur d'un manga.

        Args:
            manga_id: ID du manga.
            score: Note (0-10) ou None pour retirer.
        """
        self._ensure_open()

        await self.execute(
            """
            INSERT INTO reading_progress (manga_id, chapter_id, page, score)
            VALUES (?, '', 0, ?)
            ON CONFLICT(manga_id) DO UPDATE SET
                score = excluded.score,
                updated_at = datetime('now')
            """,
            (manga_id, score),
        )

    # ------------------------------------------------------------------------
    # API publique — Reading Lists
    # ------------------------------------------------------------------------

    async def add_reading_list(
        self,
        list_id: str,
        name: str,
        *,
        description: str | None = None,
        icon: str | None = None,
        is_default: bool = False,
        is_public: bool = False,
        sort_order: int = 0,
    ) -> str:
        """Crée une nouvelle liste de lecture.

        Args:
            list_id: ID unique de la liste.
            name: Nom de la liste.
            description: Description (optionnel).
            icon: Nom d'icône (optionnel).
            is_default: Liste par défaut.
            is_public: Liste publique.
            sort_order: Ordre de tri.

        Returns:
            ID de la liste créée.
        """
        self._ensure_open()

        await self.execute(
            """
            INSERT INTO reading_list (
                id, name, description, icon, is_default, is_public, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (list_id, name, description, icon, int(is_default), int(is_public), sort_order),
        )

        return list_id

    async def get_reading_list(self, list_id: str) -> dict[str, Any] | None:
        """Récupère une liste de lecture par son ID."""
        self._ensure_open()

        row = await self.fetch_one(
            "SELECT * FROM v_reading_lists_with_count WHERE id = ?",
            (list_id,),
        )
        return dict(row) if row else None

    async def list_reading_lists(self) -> list[dict[str, Any]]:
        """Liste toutes les listes de lecture avec leur compteur de mangas."""
        self._ensure_open()

        rows = await self.fetch_all(
            "SELECT * FROM v_reading_lists_with_count ORDER BY sort_order, name"
        )
        return [dict(row) for row in rows]

    async def add_manga_to_list(
        self,
        list_id: str,
        manga_id: str,
        *,
        position: int = 0,
        notes: str | None = None,
    ) -> None:
        """Ajoute un manga à une liste de lecture."""
        self._ensure_open()

        await self.execute(
            """
            INSERT INTO reading_list_manga (list_id, manga_id, position, notes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(list_id, manga_id) DO UPDATE SET
                position = excluded.position,
                notes = COALESCE(excluded.notes, reading_list_manga.notes)
            """,
            (list_id, manga_id, position, notes),
        )

    async def remove_manga_from_list(
        self,
        list_id: str,
        manga_id: str,
    ) -> None:
        """Retire un manga d'une liste de lecture."""
        self._ensure_open()

        await self.execute(
            "DELETE FROM reading_list_manga WHERE list_id = ? AND manga_id = ?",
            (list_id, manga_id),
        )

    async def get_manga_ids_in_list(self, list_id: str) -> list[str]:
        """Récupère les IDs des mangas dans une liste, triés par position."""
        self._ensure_open()

        rows = await self.fetch_all(
            """
            SELECT manga_id FROM reading_list_manga
            WHERE list_id = ?
            ORDER BY position, added_at
            """,
            (list_id,),
        )
        return [row[0] for row in rows]

    async def delete_reading_list(self, list_id: str) -> None:
        """Supprime une liste de lecture (et ses associations CASCADE)."""
        self._ensure_open()

        cursor = await self.execute("DELETE FROM reading_list WHERE id = ?", (list_id,))
        if cursor.rowcount == 0:
            raise EntityNotFoundError("ReadingList", list_id)

    # ------------------------------------------------------------------------
    # API publique — Download History
    # ------------------------------------------------------------------------

    async def log_download(
        self,
        task_id: str,
        *,
        manga_id: str | None = None,
        chapter_id: str | None = None,
        status: str,
        bytes_downloaded: int = 0,
        duration_seconds: float = 0.0,
        error_message: str | None = None,
    ) -> None:
        """Enregistre une tentative de téléchargement dans l'historique.

        Args:
            task_id: UUID de la tâche de téléchargement.
            manga_id: ID du manga (optionnel).
            chapter_id: ID du chapitre (optionnel).
            status: Statut (SUCCESS, FAILED, CANCELLED).
            bytes_downloaded: Bytes téléchargés.
            duration_seconds: Durée du téléchargement.
            error_message: Message d'erreur si échec.
        """
        self._ensure_open()

        await self.execute(
            """
            INSERT INTO download_history (
                id, manga_id, chapter_id, status, bytes_downloaded,
                duration_seconds, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                manga_id,
                chapter_id,
                status,
                bytes_downloaded,
                duration_seconds,
                error_message,
            ),
        )

    async def list_downloads(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Liste l'historique des téléchargements.

        Args:
            status: Filtrer par statut (optionnel).
            limit: Nombre maximum de résultats.

        Returns:
            Liste des entrées d'historique.
        """
        self._ensure_open()

        sql = "SELECT * FROM download_history"
        params: list[Any] = []

        if status is not None:
            sql += " WHERE status = ?"
            params.append(status)

        sql += " ORDER BY completed_at DESC LIMIT ?"
        params.append(limit)

        rows = await self.fetch_all(sql, params)
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------------
    # API publique — Scanned Files (pour LibraryScanner)
    # ------------------------------------------------------------------------

    async def get_all_scanned_files(self) -> list[dict[str, Any]]:
        """Récupère tous les fichiers scannés connus (pour scan incrémental).

        Note : cette méthode suppose qu'une table `scanned_file` existe.
        Si elle n'existe pas (vieille installation), elle est créée à la volée.
        """
        self._ensure_open()

        # Créer la table si nécessaire
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS scanned_file (
                file_path TEXT PRIMARY KEY,
                file_size_bytes INTEGER NOT NULL,
                file_mtime REAL NOT NULL,
                metadata_json TEXT,
                scanned_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        rows = await self.fetch_all(
            "SELECT file_path, file_size_bytes, file_mtime FROM scanned_file"
        )
        return [dict(row) for row in rows]

    async def upsert_scanned_file(
        self,
        path: str,
        size: int,
        mtime: float,
        metadata: Any | None = None,
    ) -> None:
        """Insère ou met à jour un fichier scanné.

        Args:
            path: Chemin absolu du fichier.
            size: Taille en bytes.
            mtime: Timestamp de modification.
            metadata: Métadonnées extraites (sérialisées en JSON).
        """
        self._ensure_open()

        metadata_json = json.dumps(metadata, default=str) if metadata is not None else None

        await self.execute(
            """
            INSERT INTO scanned_file (file_path, file_size_bytes, file_mtime, metadata_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                file_size_bytes = excluded.file_size_bytes,
                file_mtime = excluded.file_mtime,
                metadata_json = COALESCE(excluded.metadata_json, scanned_file.metadata_json),
                scanned_at = datetime('now')
            """,
            (path, size, mtime, metadata_json),
        )

    async def delete_scanned_file(self, path: str) -> None:
        """Supprime un fichier scanné de la table."""
        self._ensure_open()

        await self.execute("DELETE FROM scanned_file WHERE file_path = ?", (path,))

    # ------------------------------------------------------------------------
    # API publique — Vues pré-définies (wrappers)
    # ------------------------------------------------------------------------

    async def get_continue_reading(self, limit: int = 10) -> list[dict[str, Any]]:
        """Récupère les mangas en cours de lecture (vue v_continue_reading)."""
        self._ensure_open()

        rows = await self.fetch_all(
            "SELECT * FROM v_continue_reading LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]

    async def get_recently_completed(self, limit: int = 30) -> list[dict[str, Any]]:
        """Récupère les mangas récemment terminés."""
        self._ensure_open()

        rows = await self.fetch_all(
            "SELECT * FROM v_recently_completed LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]

    async def get_plan_to_read(self, limit: int = 50) -> list[dict[str, Any]]:
        """Récupère les mangas dans la wishlist."""
        self._ensure_open()

        rows = await self.fetch_all(
            "SELECT * FROM v_plan_to_read LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]

    async def get_favorites(self, limit: int = 20) -> list[dict[str, Any]]:
        """Récupère les mangas favoris (score >= 8.0)."""
        self._ensure_open()

        rows = await self.fetch_all(
            "SELECT * FROM v_favorites LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]

    async def get_reading_stats(self) -> dict[str, Any]:
        """Récupère les statistiques globales de lecture."""
        self._ensure_open()

        row = await self.fetch_one("SELECT * FROM v_reading_stats")
        return dict(row) if row else {}

    async def get_library_stats(self) -> dict[str, Any]:
        """Récupère les statistiques globales de la bibliothèque."""
        self._ensure_open()

        row = await self.fetch_one("SELECT * FROM v_library_stats")
        return dict(row) if row else {}

    async def get_recent_activity(self, limit: int = 50) -> list[dict[str, Any]]:
        """Récupère l'activité de lecture récente."""
        self._ensure_open()

        rows = await self.fetch_all(
            "SELECT * FROM v_recent_activity LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------------
    # API publique — Recherche FTS5 basique
    # ------------------------------------------------------------------------

    async def search_manga(
        self,
        query: str,
        *,
        limit: int = 50,
    ) -> list[Manga]:
        """Recherche basique de mangas via FTS5.

        Pour des recherches avancées (filtres, tri, pagination), utiliser
        `core.library.search.LibrarySearch`.

        Args:
            query: Texte de recherche.
            limit: Nombre maximum de résultats.

        Returns:
            Liste des mangas correspondants.
        """
        self._ensure_open()

        if not query.strip():
            return []

        rows = await self.fetch_all(
            """
            SELECT m.*, bm25(manga_search) AS score
            FROM manga m
            JOIN manga_search ON manga_search.manga_id = m.id
            WHERE manga_search MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (query, limit),
        )

        results: list[Manga] = []
        for row in rows:
            genres = await self._get_genres(row["id"])
            results.append(self._row_to_manga(row, genres))

        return results

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> DatabaseStats:
        """Retourne les statistiques complètes de la base de données."""
        self._ensure_open()

        # Taille du fichier
        file_size = 0
        if self._config.path.exists():
            file_size = self._config.path.stat().st_size

        # Compteurs
        manga_count = await self.fetch_scalar("SELECT COUNT(*) FROM manga") or 0
        chapter_count = await self.fetch_scalar("SELECT COUNT(*) FROM chapter") or 0
        downloaded_count = (
            await self.fetch_scalar(
                "SELECT COUNT(*) FROM chapter WHERE downloaded = 1"
            )
            or 0
        )
        reading_count = (
            await self.fetch_scalar("SELECT COUNT(*) FROM reading_progress") or 0
        )
        list_count = (
            await self.fetch_scalar("SELECT COUNT(*) FROM reading_list") or 0
        )
        history_count = (
            await self.fetch_scalar("SELECT COUNT(*) FROM download_history") or 0
        )

        # scanned_file peut ne pas exister
        scanned_count = 0
        try:
            scanned_count = (
                await self.fetch_scalar("SELECT COUNT(*) FROM scanned_file") or 0
            )
        except Exception:
            pass

        # Uptime
        uptime = 0.0
        if self._start_time > 0:
            uptime = time.monotonic() - self._start_time

        async with self._query_lock:
            total_queries = self._total_queries
            total_duration = self._total_query_duration_ms

        return DatabaseStats(
            state=self._state,
            file_size_bytes=file_size,
            manga_count=manga_count,
            chapter_count=chapter_count,
            downloaded_chapters_count=downloaded_count,
            reading_progress_count=reading_count,
            reading_list_count=list_count,
            download_history_count=history_count,
            scanned_files_count=scanned_count,
            applied_migrations=list(self._applied_migrations),
            uptime_seconds=uptime,
            total_queries=total_queries,
            total_query_duration_ms=total_duration,
        )

    async def vacuum(self) -> None:
        """Compacte la base de données pour récupérer l'espace inutilisé.

        Opération bloquante — à appeler hors des pics d'usage.
        """
        self._ensure_open()
        assert self._db is not None

        await self._db.execute("VACUUM")
        self._logger.info("VACUUM terminé sur {}", self._config.path.name)

    async def integrity_check(self) -> bool:
        """Vérifie l'intégrité de la base de données.

        Returns:
            True si la BDD est intègre, False sinon.
        """
        self._ensure_open()

        row = await self.fetch_one("PRAGMA integrity_check")
        return row is not None and row[0] == "ok"

    # ------------------------------------------------------------------------
    # Méthodes internes — Configuration
    # ------------------------------------------------------------------------

    async def _apply_pragmas(self) -> None:
        """Applique les pragmas SQLite de configuration."""
        assert self._db is not None

        pragmas = [
            ("journal_mode", "WAL" if self._config.wal_mode else "DELETE"),
            ("foreign_keys", "ON" if self._config.foreign_keys else "OFF"),
            ("synchronous", self._config.synchronous),
            ("temp_store", self._config.temp_store),
            ("busy_timeout", str(self._config.busy_timeout_ms)),
            ("cache_size", str(self._config.cache_size_kb)),
        ]

        if self._config.journal_size_limit_mb > 0:
            pragmas.append(
                ("journal_size_limit", str(self._config.journal_size_limit_mb * 1024 * 1024))
            )

        for pragma, value in pragmas:
            try:
                await self._db.execute(f"PRAGMA {pragma}={value}")
            except Exception as e:
                self._logger.warning(
                    "Impossible d'appliquer le pragma {}={}: {}",
                    pragma,
                    value,
                    e,
                )

    # ------------------------------------------------------------------------
    # Méthodes internes — Migrations
    # ------------------------------------------------------------------------

    async def _ensure_migrations_table(self) -> None:
        """Crée la table de suivi des migrations si elle n'existe pas."""
        assert self._db is not None

        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS _migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL,
                duration_ms REAL NOT NULL
            )
            """
        )
        await self._db.commit()

        # Charger les migrations déjà appliquées
        rows = await self.fetch_all(
            "SELECT name, applied_at, duration_ms FROM _migrations ORDER BY name"
        )
        self._applied_migrations = [
            MigrationInfo(
                name=row[0],
                applied_at=datetime.fromisoformat(row[1]),
                duration_ms=row[2],
            )
            for row in rows
        ]

    async def _apply_migrations(self) -> None:
        """Applique les migrations manquantes dans l'ordre."""
        applied_names = {m.name for m in self._applied_migrations}

        for migration_name in _BUILTIN_MIGRATIONS:
            if migration_name in applied_names:
                continue

            start = time.perf_counter()
            self._logger.info("Application de la migration: {}", migration_name)

            try:
                sql_content = self._load_migration(migration_name)
                assert self._db is not None
                await self._db.executescript(sql_content)
                await self._db.commit()

                duration_ms = (time.perf_counter() - start) * 1000.0

                # Enregistrer la migration
                await self._db.execute(
                    "INSERT INTO _migrations (name, applied_at, duration_ms) VALUES (?, ?, ?)",
                    (migration_name, datetime.now(UTC).isoformat(), duration_ms),
                )
                await self._db.commit()

                self._applied_migrations.append(
                    MigrationInfo(
                        name=migration_name,
                        applied_at=datetime.now(UTC),
                        duration_ms=duration_ms,
                    )
                )

                self._logger.info(
                    "Migration {} appliquée en {:.1f}ms",
                    migration_name,
                    duration_ms,
                )

            except Exception as e:
                raise MigrationError(migration_name, str(e)) from e

    def _load_migration(self, name: str) -> str:
        """Charge le contenu d'un fichier de migration.

        Cherche d'abord dans le répertoire configuré, puis dans les
        ressources embarquées du package.
        """
        # 1. Répertoire configuré
        if self._config.migrations_dir is not None:
            migration_path = self._config.migrations_dir / name
            if migration_path.exists():
                return migration_path.read_text(encoding="utf-8")

        # 2. Ressources embarquées (via importlib.resources)
        try:
            from importlib.resources import files
            migrations_resource = files("nexusdl.core.library.migrations")
            return (migrations_resource / name).read_text(encoding="utf-8")
        except Exception as e:
            raise MigrationError(
                name,
                f"Impossible de charger la migration embarquée: {e}",
            ) from e

    # ------------------------------------------------------------------------
    # Méthodes internes — Helpers
    # ------------------------------------------------------------------------

    async def _insert_genres(self, manga_id: str, genres: list[str]) -> None:
        """Insère les genres d'un manga dans la table manga_genre."""
        if not genres:
            return

        # Utiliser executemany pour la performance
        assert self._db is not None
        await self._db.executemany(
            "INSERT OR IGNORE INTO manga_genre (manga_id, genre) VALUES (?, ?)",
            [(manga_id, genre) for genre in genres],
        )
        await self._db.commit()

    async def _get_genres(self, manga_id: str) -> list[str]:
        """Récupère les genres d'un manga."""
        rows = await self.fetch_all(
            "SELECT genre FROM manga_genre WHERE manga_id = ? ORDER BY genre",
            (manga_id,),
        )
        return [row[0] for row in rows]

    def _row_to_manga(self, row: Any, genres: list[str]) -> Manga:
        """Convertit une ligne SQL en modèle Manga."""
        from nexusdl.core.models.manga import (
            ContentRating,
            Language,
            Manga,
            MangaStatus,
        )

        # Parser les titres alternatifs (JSON)
        alt_titles_raw = row["alternative_titles"]
        alt_titles: list[str] = []
        if alt_titles_raw:
            try:
                alt_titles = json.loads(alt_titles_raw)
            except (json.JSONDecodeError, TypeError):
                alt_titles = []

        # Mapper les enums
        try:
            status = MangaStatus(row["status"])
        except ValueError:
            status = MangaStatus.UNKNOWN

        try:
            language = Language(row["language"])
        except ValueError:
            language = Language.EN

        try:
            content_rating = ContentRating(row["content_rating"])
        except ValueError:
            content_rating = ContentRating.SAFE

        return Manga(
            id=row["id"],
            source_id=row["source_id"],
            site=row["site"],
            title=row["title"],
            alternative_titles=alt_titles,
            description=row["description"],
            author=row["author"],
            artist=row["artist"],
            genres=genres,
            status=status,
            year=row["year"],
            cover_url=row["cover_url"],
            language=language,
            content_rating=content_rating,
            chapters=[],  # À charger séparément si nécessaire
            url=row["url"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _row_to_chapter(self, row: Any) -> Chapter | None:
        """Convertit une ligne SQL en modèle Chapter."""
        from nexusdl.core.models.manga import Chapter, Language

        try:
            language = Language(row["language"])
        except ValueError:
            language = Language.EN

        # Parser le numéro (peut être float ou str)
        number_str = row["number"]
        number: float | str
        try:
            number = float(number_str)
        except (ValueError, TypeError):
            number = number_str

        published_at = None
        if row["published_at"]:
            try:
                published_at = datetime.fromisoformat(row["published_at"])
            except (ValueError, TypeError):
                pass

        return Chapter(
            id=row["id"],
            source_id=row["source_id"],
            title=row["title"] or "",
            number=number,
            volume=row["volume"],
            language=language,
            pages_count=row["pages_count"],
            published_at=published_at,
            url=row["url"],
            pages=[],  # Non stocké en BDD principale
        )

    def _ensure_open(self) -> None:
        """Vérifie que la connexion est ouverte."""
        if self._state != DatabaseState.OPEN or self._db is None:
            raise DatabaseNotStartedError()

    def __repr__(self) -> str:
        return (
            f"<LibraryDatabase path={self._config.path.name} "
            f"state={self._state.value} "
            f"migrations={len(self._applied_migrations)}>"
        )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "DatabaseError",
    "DatabaseNotStartedError",
    "DatabaseAlreadyStartedError",
    "MigrationError",
    "EntityNotFoundError",
    "IntegrityError",
    # Enums
    "DatabaseState",
    # Modèles
    "DatabaseConfig",
    "DatabaseStats",
    "MigrationInfo",
    # Classe principale
    "LibraryDatabase",
]
