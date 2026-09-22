"""Module public de la bibliothèque locale NexusDL.

Ce module constitue le point d'entrée de la couche de gestion de la
bibliothèque locale dans l'architecture hexagonale. Il expose l'API
publique stable utilisée par les interfaces (CLI, Web, GUI) et le
DownloadWorker pour stocker, rechercher, scanner et maintenir à jour
une collection locale de mangas/webtoons/comics synchronisée avec le
filesystem.

Pipeline de synchronisation :
    1. LibraryScanner.scan()        → détecte les changements filesystem
    2. MetadataExtractor.extract()  → extrait ComicInfo.xml des archives
    3. LibraryDatabase.upsert_*()   → met à jour la BDD SQLite
    4. LibrarySearch.search()       → recherche plein texte FTS5
    5. Interfaces (CLI/Web/GUI)     → affichent les résultats

Architecture :
    LibraryDatabase (SQLite async + migrations + FTS5)
        │
        ├── LibraryScanner (filesystem ↔ BDD, 3 modes)
        │       └── MetadataExtractor (ComicInfo.xml → Pydantic)
        │
        ├── LibrarySearch (FTS5 + filtres + tri + pagination)
        │
        └── MetadataWriter (Pydantic → ComicInfo.xml → archives)

Règles d'or :
    1. Ce fichier n'expose QUE les symboles publics stables.
    2. Il ne dépend d'aucun module de `interfaces/` ni de `parsers/`.
    3. Tous les exports sont typés strictement et documentés.
    4. Les imports sont organisés par sous-module pour la lisibilité.

Exemple d'utilisation :
    >>> from pathlib import Path
    >>> from nexusdl.core.library import (
    ...     LibraryDatabase,
    ...     LibraryScanner,
    ...     LibrarySearch,
    ...     MetadataExtractor,
    ...     MetadataWriter,
    ...     MetadataMapper,
    ...     ScanMode,
    ...     SearchQuery,
    ...     SearchFilters,
    ...     SearchSortBy,
    ...     SearchSortOrder,
    ... )
    >>>
    >>> # Initialiser la BDD (migrations automatiques)
    >>> db = LibraryDatabase(path=Path("~/.local/share/nexusdl/library.db"))
    >>> await db.start()
    >>>
    >>> # Scanner la bibliothèque
    >>> scanner = LibraryScanner(database=db, metadata_extractor=MetadataExtractor())
    >>> await scanner.start()
    >>> result = await scanner.scan(
    ...     roots=[Path("~/Mangas").expanduser()],
    ...     mode=ScanMode.INCREMENTAL,
    ... )
    >>> print(f"Ajouts: {result.files_added}, Suppressions: {result.files_removed}")
    >>>
    >>> # Rechercher
    >>> search = LibrarySearch(database=db)
    >>> await search.start()
    >>> page = await search.search(
    ...     "one piece",
    ...     filters=SearchFilters(languages=["fr"], status=["ONGOING"]),
    ...     sort_by=SearchSortBy.RELEVANCE,
    ... )
    >>> for manga in page.items:
    ...     print(f"{manga.title} (score: {manga.score:.2f})")
    >>>
    >>> await db.stop()
"""

from __future__ import annotations

# ============================================================================
# EXCEPTIONS — Hiérarchie complète
# ============================================================================

# database.py
from nexusdl.core.library.database import (
    DatabaseAlreadyStartedError,
    DatabaseError,
    DatabaseNotStartedError,
    EntityNotFoundError,
    IntegrityError,
    MigrationError,
)
# metadata.py
from nexusdl.core.library.metadata import (
    ExtractionError,
    InvalidComicInfoError,
    MetadataError,
    MetadataNotStartedError,
    UnsupportedArchiveFormatError,
    WriteError,
)
# scanner.py
from nexusdl.core.library.scanner import (
    RootNotAccessibleError,
    ScanCancelledError,
    ScannerError,
    ScannerNotStartedError,
)
# search.py
from nexusdl.core.library.search import (
    InvalidQueryError,
    SearchError,
    SearchNotStartedError as SearchEngineNotStartedError,
    SearchTimeoutError,
)

# ============================================================================
# ENUMS — États, modes et configurations
# ============================================================================

from nexusdl.core.library.database import DatabaseState
from nexusdl.core.library.metadata import (
    ArchiveFormat,
    ExtractionMode,
    MetadataSource,
)
from nexusdl.core.library.scanner import (
    FileChangeType,
    ScanMode,
    ScanState,
)
from nexusdl.core.library.search import (
    SearchMode as SearchEngineMode,
    SearchSortBy,
    SearchSortOrder,
)

# ============================================================================
# MODÈLES PYDANTIC — Configurations
# ============================================================================

from nexusdl.core.library.database import DatabaseConfig
from nexusdl.core.library.metadata import ExtractionStats
from nexusdl.core.library.scanner import ScannerConfig
from nexusdl.core.library.search import SearchFilters, SearchQuery

# ============================================================================
# MODÈLES PYDANTIC — Résultats
# ============================================================================

from nexusdl.core.library.metadata import (
    ExtractionResult,
    WriteResult,
)
from nexusdl.core.library.scanner import (
    FileChange,
    ScanResult,
    ScanStats,
)
from nexusdl.core.library.search import (
    SearchResult,
    SearchResultPage,
    SearchStats,
)

# ============================================================================
# MODÈLES PYDANTIC — Statistiques et info
# ============================================================================

from nexusdl.core.library.database import (
    DatabaseStats,
    MigrationInfo,
)

# ============================================================================
# CLASSES PRINCIPALES — Orchestration et accès données
# ============================================================================

from nexusdl.core.library.database import LibraryDatabase
from nexusdl.core.library.metadata import (
    MetadataExtractor,
    MetadataMapper,
    MetadataWriter,
)
from nexusdl.core.library.scanner import LibraryScanner
from nexusdl.core.library.search import LibrarySearch

# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================

from nexusdl.core.library.metadata import (
    detect_archive_format,
    is_pikepdf_available,
    is_rarfile_available,
    parse_comic_info_xml,
    render_comic_info_xml,
)
from nexusdl.core.library.search import (
    build_fts5_select_columns,
    escape_fts5_query,
)

# ============================================================================
# EXPORTS PUBLICS
# ============================================================================

__all__ = [
    # === Classes principales ===
    "LibraryDatabase",
    "LibraryScanner",
    "LibrarySearch",
    "MetadataExtractor",
    "MetadataWriter",
    "MetadataMapper",
    # === Enums — États et modes ===
    "DatabaseState",
    "ArchiveFormat",
    "ExtractionMode",
    "MetadataSource",
    "ScanMode",
    "FileChangeType",
    "ScanState",
    "SearchSortBy",
    "SearchSortOrder",
    "SearchEngineMode",  # Alias pour éviter conflit avec ScanMode
    # === Modèles — Configuration ===
    "DatabaseConfig",
    "ScannerConfig",
    "SearchQuery",
    "SearchFilters",
    # === Modèles — Résultats ===
    "ExtractionResult",
    "WriteResult",
    "ScanResult",
    "FileChange",
    "SearchResult",
    "SearchResultPage",
    # === Modèles — Statistiques ===
    "DatabaseStats",
    "MigrationInfo",
    "ExtractionStats",
    "ScanStats",
    "SearchStats",
    # === Helpers — Métadonnées ===
    "detect_archive_format",
    "is_rarfile_available",
    "is_pikepdf_available",
    "parse_comic_info_xml",
    "render_comic_info_xml",
    # === Helpers — Recherche ===
    "escape_fts5_query",
    "build_fts5_select_columns",
    # === Exceptions — Database ===
    "DatabaseError",
    "DatabaseNotStartedError",
    "DatabaseAlreadyStartedError",
    "MigrationError",
    "EntityNotFoundError",
    "IntegrityError",
    # === Exceptions — Metadata ===
    "MetadataError",
    "ExtractionError",
    "WriteError",
    "UnsupportedArchiveFormatError",
    "InvalidComicInfoError",
    "MetadataNotStartedError",
    # === Exceptions — Scanner ===
    "ScannerError",
    "ScannerNotStartedError",
    "ScanCancelledError",
    "RootNotAccessibleError",
    # === Exceptions — Search ===
    "SearchError",
    "SearchEngineNotStartedError",  # Alias pour éviter conflit
    "InvalidQueryError",
    "SearchTimeoutError",
]

__version__: str = "0.1.0"
