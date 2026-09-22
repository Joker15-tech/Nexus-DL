"""Extraction et écriture de métadonnées ComicInfo.xml pour la bibliothèque locale.

Ce module fournit un système complet de gestion des métadonnées embarquées
dans les archives de mangas/webtoons/comics. Il sert de pont bidirectionnel
entre le standard ComicInfo.xml (ComicRack) et les modèles de domaine
Pydantic de NexusDL (`Manga`, `Chapter`).

Fonctionnalités principales :
    - Extraction de ComicInfo.xml depuis CBZ, CBR, PDF, ZIP
    - Écriture de ComicInfo.xml dans les archives (CBZ, PDF)
    - Conversion bidirectionnelle ComicInfo ↔ Manga/Chapter
    - Support des archives multi-chapitres (volumes) et single-chapter
    - Détection automatique du format d'archive
    - Gestion robuste des archives corrompues ou partielles
    - Cache des métadonnées extraites (LRU en mémoire)
    - Validation stricte via Pydantic v2
    - Async-first via asyncio.to_thread pour les opérations I/O

Architecture :
    MetadataExtractor (lecture)
        ├── Extracts ComicInfo.xml from archives
        ├── Parses XML → ComicInfo Pydantic model
        └── Maps ComicInfo → Manga/Chapter domain models

    MetadataWriter (écriture)
        ├── Maps Manga/Chapter → ComicInfo Pydantic model
        ├── Renders ComicInfo.xml via Jinja2 template
        └── Injects XML into CBZ/PDF archives

    MetadataMapper (conversion)
        ├── comic_info_to_manga() : ComicInfo → Manga
        ├── comic_info_to_chapter() : ComicInfo → Chapter
        ├── manga_to_comic_info() : Manga → ComicInfo
        └── chapter_to_comic_info() : Chapter → ComicInfo

Exemple d'utilisation :
    >>> extractor = MetadataExtractor()
    >>> await extractor.start()
    >>>
    >>> # Extraire les métadonnées d'une archive
    >>> result = await extractor.extract(Path("One_Piece_Ch001.cbz"))
    >>> if result.success:
    ...     manga = result.to_manga()
    ...     chapter = result.to_chapter()
    ...     print(f"{manga.title} - Chapitre {chapter.number}")
    >>>
    >>> # Écrire les métadonnées dans une archive
    >>> writer = MetadataWriter()
    >>> await writer.start()
    >>> await writer.write_metadata(
    ...     archive=Path("output.cbz"),
    ...     manga=manga,
    ...     chapter=chapter,
    ... )
    >>>
    >>> await extractor.stop()
    >>> await writer.stop()
"""

from __future__ import annotations

import asyncio
import io
import re
import zipfile
from collections import OrderedDict
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final, Self
from xml.etree import ElementTree as ET

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nexusdl.core.exceptions import NexusDLError

if TYPE_CHECKING:
    from nexusdl.core.models.manga import Chapter, Manga
    from nexusdl.core.packaging.comic_info import ComicInfo


# ============================================================================
# EXCEPTIONS
# ============================================================================


class MetadataError(NexusDLError):
    """Exception de base pour les erreurs de métadonnées."""


class ExtractionError(MetadataError):
    """Exception levée lorsque l'extraction de métadonnées échoue."""

    def __init__(self, path: Path, reason: str = "") -> None:
        msg = f"Échec de l'extraction des métadonnées: {path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.path = path
        self.reason = reason


class WriteError(MetadataError):
    """Exception levée lorsque l'écriture de métadonnées échoue."""

    def __init__(self, path: Path, reason: str = "") -> None:
        msg = f"Échec de l'écriture des métadonnées: {path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.path = path
        self.reason = reason


class UnsupportedArchiveFormatError(MetadataError):
    """Exception levée lorsque le format d'archive n'est pas supporté."""

    def __init__(self, path: Path, fmt: str) -> None:
        super().__init__(
            f"Format d'archive non supporté: {fmt} pour {path}"
        )
        self.path = path
        self.format = fmt


class InvalidComicInfoError(MetadataError):
    """Exception levée lorsque le fichier ComicInfo.xml est invalide."""

    def __init__(self, path: Path, reason: str = "") -> None:
        msg = f"ComicInfo.xml invalide dans {path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.path = path
        self.reason = reason


class MetadataNotStartedError(MetadataError):
    """Exception levée lorsqu'on utilise l'extractor/writer avant start()."""

    def __init__(self, component: str) -> None:
        super().__init__(
            f"{component} must be started before use. Call await {component.lower()}.start()"
        )
        self.component = component


# ============================================================================
# ENUMS
# ============================================================================


class ArchiveFormat(str, Enum):
    """Formats d'archives supportés pour les métadonnées.

    CBZ  : Comic Book ZIP (standard, recommandé)
    CBR  : Comic Book RAR (nécessite `rarfile`)
    PDF  : PDF (nécessite `pikepdf`)
    ZIP  : ZIP standard (même format que CBZ)
    """

    CBZ = "cbz"
    CBR = "cbr"
    PDF = "pdf"
    ZIP = "zip"
    UNKNOWN = "unknown"


class ExtractionMode(str, Enum):
    """Mode d'extraction des métadonnées.

    STRICT    : Lève une exception si ComicInfo.xml est absent ou invalide.
    PERMISSIVE: Retourne None si ComicInfo.xml est absent, lève si invalide.
    BEST_EFFORT: Retourne None si absent ou invalide (ne lève jamais).
    """

    STRICT = "strict"
    PERMISSIVE = "permissive"
    BEST_EFFORT = "best_effort"


class MetadataSource(str, Enum):
    """Source des métadonnées extraites.

    COMIC_INFO : Extraites depuis ComicInfo.xml dans l'archive.
    PDF_INFO   : Extraites depuis les métadonnées PDF (Title, Author, etc.).
    FILENAME   : Inférées depuis le nom du fichier (fallback).
    NONE       : Aucune métadonnée disponible.
    """

    COMIC_INFO = "comic_info"
    PDF_INFO = "pdf_info"
    FILENAME = "filename"
    NONE = "none"


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class ExtractionResult(BaseModel):
    """Résultat immuable d'une extraction de métadonnées.

    Contient les métadonnées extraites (si disponibles), le format détecté,
    et des métadonnées sur l'extraction elle-même.
    """

    path: Path = Field(..., description="Chemin de l'archive analysée.")
    archive_format: ArchiveFormat = Field(..., description="Format d'archive détecté.")
    source: MetadataSource = Field(..., description="Source des métadonnées.")
    success: bool = Field(..., description="True si l'extraction a réussi.")
    comic_info: dict[str, Any] | None = Field(
        default=None,
        description="Métadonnées ComicInfo.xml en dictionnaire (None si absent).",
    )
    error: str | None = Field(
        default=None,
        description="Message d'erreur si l'extraction a échoué.",
    )
    extraction_duration_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Durée de l'extraction en millisecondes.",
    )
    archive_size_bytes: int = Field(
        default=0,
        ge=0,
        description="Taille de l'archive en bytes.",
    )
    pages_count: int = Field(
        default=0,
        ge=0,
        description="Nombre de pages détectées dans l'archive.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    def to_comic_info(self) -> ComicInfo | None:
        """Convertit le dictionnaire en modèle ComicInfo Pydantic.

        Returns:
            Instance de ComicInfo si les données sont valides, None sinon.

        Raises:
            InvalidComicInfoError: Si la conversion échoue.
        """
        if self.comic_info is None:
            return None

        try:
            from nexusdl.core.packaging.comic_info import ComicInfo
            return ComicInfo.model_validate(self.comic_info)
        except ValidationError as e:
            raise InvalidComicInfoError(self.path, str(e)) from e

    def to_manga(self) -> Manga | None:
        """Convertit les métadonnées en modèle Manga.

        Returns:
            Instance de Manga si les données sont suffisantes, None sinon.
        """
        if self.comic_info is None:
            return None

        from nexusdl.core.library.metadata import MetadataMapper
        try:
            comic_info = self.to_comic_info()
            if comic_info is None:
                return None
            return MetadataMapper.comic_info_to_manga(comic_info)
        except Exception:
            return None

    def to_chapter(self) -> Chapter | None:
        """Convertit les métadonnées en modèle Chapter.

        Returns:
            Instance de Chapter si les données sont suffisantes, None sinon.
        """
        if self.comic_info is None:
            return None

        from nexusdl.core.library.metadata import MetadataMapper
        try:
            comic_info = self.to_comic_info()
            if comic_info is None:
                return None
            return MetadataMapper.comic_info_to_chapter(comic_info)
        except Exception:
            return None


class WriteResult(BaseModel):
    """Résultat immuable d'une écriture de métadonnées."""

    path: Path = Field(..., description="Chemin de l'archive modifiée.")
    archive_format: ArchiveFormat = Field(..., description="Format d'archive.")
    success: bool = Field(..., description="True si l'écriture a réussi.")
    bytes_written: int = Field(
        default=0,
        ge=0,
        description="Taille du XML écrit en bytes.",
    )
    write_duration_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Durée de l'écriture en millisecondes.",
    )
    error: str | None = Field(
        default=None,
        description="Message d'erreur si l'écriture a échoué.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class ExtractionStats(BaseModel):
    """Statistiques agrégées de l'extracteur de métadonnées."""

    total_extractions: int = Field(default=0, ge=0)
    successful: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    no_metadata: int = Field(
        default=0,
        ge=0,
        description="Archives sans ComicInfo.xml.",
    )
    by_format: dict[str, int] = Field(
        default_factory=dict,
        description="Nombre d'extractions par format d'archive.",
    )
    by_source: dict[str, int] = Field(
        default_factory=dict,
        description="Nombre d'extractions par source de métadonnées.",
    )
    cache_hits: int = Field(default=0, ge=0)
    cache_misses: int = Field(default=0, ge=0)
    total_duration_ms: float = Field(default=0.0, ge=0.0)
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def success_rate(self) -> float:
        """Taux de succès (0.0 à 1.0)."""
        if self.total_extractions == 0:
            return 0.0
        return self.successful / self.total_extractions

    @property
    def average_duration_ms(self) -> float:
        """Durée moyenne d'extraction en millisecondes."""
        if self.total_extractions == 0:
            return 0.0
        return self.total_duration_ms / self.total_extractions


# ============================================================================
# HELPERS — Détection de format
# ============================================================================


# Mapping extension → format
_EXTENSION_TO_FORMAT: Final[dict[str, ArchiveFormat]] = {
    ".cbz": ArchiveFormat.CBZ,
    ".cbr": ArchiveFormat.CBR,
    ".pdf": ArchiveFormat.PDF,
    ".zip": ArchiveFormat.ZIP,
}

# Magic bytes pour détection par contenu
_MAGIC_BYTES: Final[dict[bytes, ArchiveFormat]] = {
    b"PK\x03\x04": ArchiveFormat.CBZ,  # ZIP (CBZ utilise le même format)
    b"Rar!": ArchiveFormat.CBR,
    b"%PDF": ArchiveFormat.PDF,
}


def detect_archive_format(path: Path) -> ArchiveFormat:
    """Détecte le format d'une archive par extension et contenu.

    Le contenu prime sur l'extension en cas de conflit.

    Args:
        path: Chemin de l'archive.

    Returns:
        Format détecté, ou UNKNOWN si non détectable.
    """
    # Détection par extension (rapide)
    ext = path.suffix.lower()
    ext_format = _EXTENSION_TO_FORMAT.get(ext, ArchiveFormat.UNKNOWN)

    # Détection par contenu (fiable)
    try:
        with path.open("rb") as f:
            header = f.read(8)

        for magic, fmt in _MAGIC_BYTES.items():
            if header.startswith(magic):
                return fmt
    except OSError:
        pass

    return ext_format


def is_rarfile_available() -> bool:
    """Vérifie si la librairie rarfile est disponible."""
    try:
        import rarfile  # noqa: F401
        return True
    except ImportError:
        return False


def is_pikepdf_available() -> bool:
    """Vérifie si la librairie pikepdf est disponible."""
    try:
        import pikepdf  # noqa: F401
        return True
    except ImportError:
        return False


# ============================================================================
# HELPERS — Parsing XML
# ============================================================================


# Namespace ComicInfo (optionnel, souvent absent)
_COMICINFO_NS = "http://www.comicinfo.org/ComicInfo"


def parse_comic_info_xml(xml_content: str | bytes) -> dict[str, Any]:
    """Parse un fichier ComicInfo.xml en dictionnaire Python.

    Gère les deux cas :
        1. XML avec namespace ComicInfo
        2. XML sans namespace (cas le plus courant)

    Args:
        xml_content: Contenu XML (str ou bytes).

    Returns:
        Dictionnaire avec toutes les métadonnées.

    Raises:
        InvalidComicInfoError: Si le XML est malformé.
    """
    try:
        if isinstance(xml_content, str):
            xml_content = xml_content.encode("utf-8")

        root = ET.fromstring(xml_content)

        # Détecter le namespace
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"

        # Extraire tous les éléments enfants
        result: dict[str, Any] = {}

        for child in root:
            tag = child.tag.replace(ns, "")
            text = child.text

            if text is None:
                continue

            text = text.strip()
            if not text:
                continue

            # Conversion de types
            if tag in ("Volume", "Year", "Month", "Day", "PageCount", "Count", "NumberAsText"):
                try:
                    result[tag] = int(text)
                except ValueError:
                    result[tag] = text
            elif tag in ("CommunityRating", "UserRating"):
                try:
                    result[tag] = float(text)
                except ValueError:
                    result[tag] = text
            elif tag == "Pages":
                # Parser les pages
                pages = []
                for page_elem in child:
                    page_data = dict(page_elem.attrib)
                    # Convertir les attributs numériques
                    for key in ("Image", "ImageSize", "ImageWidth", "ImageHeight"):
                        if key in page_data:
                            try:
                                page_data[key] = int(page_data[key])
                            except ValueError:
                                pass
                    pages.append(page_data)
                result[tag] = pages
            else:
                result[tag] = text

        return result

    except ET.ParseError as e:
        raise InvalidComicInfoError(Path("<xml>"), f"XML malformé: {e}") from e
    except Exception as e:
        raise InvalidComicInfoError(Path("<xml>"), str(e)) from e


def render_comic_info_xml(data: dict[str, Any], template_path: Path | None = None) -> str:
    """Rend un dictionnaire de métadonnées en XML ComicInfo.

    Utilise Jinja2 si un template est fourni, sinon génère un XML basique.

    Args:
        data: Dictionnaire de métadonnées.
        template_path: Chemin vers un template Jinja2 (optionnel).

    Returns:
        Contenu XML sous forme de chaîne.
    """
    if template_path is not None and template_path.exists():
        try:
            from jinja2 import Template
            template = Template(template_path.read_text(encoding="utf-8"))
            return template.render(**data)
        except Exception:
            pass

    # Fallback : génération manuelle
    return _generate_basic_comic_info_xml(data)


def _generate_basic_comic_info_xml(data: dict[str, Any]) -> str:
    """Génère un XML ComicInfo basique sans template Jinja2."""
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<ComicInfo>"]

    # Ordre des champs (du plus important au moins important)
    field_order = [
        "Title", "Series", "Number", "Volume", "Summary",
        "Year", "Month", "Day",
        "Writer", "Penciller", "Inker", "Colorist", "Letterer",
        "CoverArtist", "Editor", "Translator",
        "Genre", "Tags",
        "PageCount", "LanguageISO", "AgeRating", "Manga",
        "Web", "Publisher",
        "CommunityRating",
        "Characters", "Teams", "StoryArc",
        "Format", "Notes",
    ]

    for field in field_order:
        if field in data and data[field] is not None:
            value = data[field]
            if field == "Pages" and isinstance(value, list):
                lines.append("  <Pages>")
                for page in value:
                    attrs = " ".join(f'{k}="{v}"' for k, v in page.items())
                    lines.append(f"    <Page {attrs} />")
                lines.append("  </Pages>")
            else:
                # Échapper les caractères XML
                escaped = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                lines.append(f"  <{field}>{escaped}</{field}>")

    # Ajouter les champs non listés
    for field, value in data.items():
        if field not in field_order and value is not None:
            escaped = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            lines.append(f"  <{field}>{escaped}</{field}>")

    lines.append("</ComicInfo>")
    return "\n".join(lines)


# ============================================================================
# CLASSE PRINCIPALE — MetadataExtractor
# ============================================================================


class MetadataExtractor:
    """Extracteur asynchrone de métadonnées depuis les archives.

    Supporte CBZ, CBR, PDF et ZIP. Utilise un cache LRU en mémoire
    pour éviter de ré-extraire les métadonnées des archives déjà vues.

    Lifecycle :
        >>> extractor = MetadataExtractor()
        >>> await extractor.start()
        >>> result = await extractor.extract(Path("manga.cbz"))
        >>> await extractor.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Un sémaphore limite la concurrence des extractions.
    """

    # Constantes
    _DEFAULT_MAX_CONCURRENT: Final[int] = 4
    _DEFAULT_CACHE_SIZE: Final[int] = 1000
    _COMIC_INFO_FILENAME: Final[str] = "ComicInfo.xml"

    def __init__(
        self,
        *,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
        cache_size: int = _DEFAULT_CACHE_SIZE,
        mode: ExtractionMode = ExtractionMode.PERMISSIVE,
        template_path: Path | None = None,
    ) -> None:
        """Initialise l'extracteur de métadonnées.

        Args:
            max_concurrent: Nombre maximum d'extractions simultanées.
            cache_size: Taille du cache LRU en mémoire.
            mode: Mode d'extraction (STRICT, PERMISSIVE, BEST_EFFORT).
            template_path: Chemin vers un template Jinja2 pour le parsing.
        """
        if max_concurrent <= 0:
            raise ValueError(f"max_concurrent must be positive, got {max_concurrent}")
        if cache_size <= 0:
            raise ValueError(f"cache_size must be positive, got {cache_size}")

        self._max_concurrent = max_concurrent
        self._cache_size = cache_size
        self._mode = mode
        self._template_path = template_path

        self._semaphore: asyncio.Semaphore | None = None
        self._started: bool = False
        self._start_time: float = 0.0

        # Cache LRU (path_str → ExtractionResult)
        self._cache: OrderedDict[str, ExtractionResult] = OrderedDict()
        self._cache_lock = asyncio.Lock()

        # Statistiques
        self._total_extractions: int = 0
        self._successful: int = 0
        self._failed: int = 0
        self._no_metadata: int = 0
        self._by_format: dict[str, int] = {}
        self._by_source: dict[str, int] = {}
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._total_duration_ms: float = 0.0
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="metadata_extractor")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre l'extracteur et initialise les ressources."""
        if self._started:
            self._logger.warning("MetadataExtractor déjà démarré, ignore")
            return

        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._started = True
        self._start_time = asyncio.get_event_loop().time()

        self._logger.info(
            "MetadataExtractor démarré: max_concurrent={}, cache_size={}, mode={}",
            self._max_concurrent,
            self._cache_size,
            self._mode.value,
        )

    async def stop(self) -> None:
        """Arrête l'extracteur et libère les ressources."""
        if not self._started:
            return

        self._semaphore = None
        async with self._cache_lock:
            self._cache.clear()
        self._started = False
        self._logger.info("MetadataExtractor arrêté")

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
        """Indique si l'extracteur est démarré."""
        return self._started

    @property
    def cache_size(self) -> int:
        """Taille actuelle du cache."""
        return len(self._cache)

    # ------------------------------------------------------------------------
    # API publique — Extraction
    # ------------------------------------------------------------------------

    async def extract(
        self,
        path: Path,
        *,
        mode: ExtractionMode | None = None,
        use_cache: bool = True,
    ) -> ExtractionResult:
        """Extrait les métadonnées d'une archive.

        Args:
            path: Chemin de l'archive à analyser.
            mode: Mode d'extraction (override du mode par défaut).
            use_cache: Utiliser le cache si disponible.

        Returns:
            Résultat de l'extraction avec métadonnées.

        Raises:
            MetadataNotStartedError: Si l'extracteur n'est pas démarré.
            ExtractionError: Si l'extraction échoue en mode STRICT.
            UnsupportedArchiveFormatError: Si le format n'est pas supporté.
        """
        self._ensure_started()
        assert self._semaphore is not None

        effective_mode = mode or self._mode
        start_time = asyncio.get_event_loop().time()
        path_str = str(path.resolve())

        # Vérifier le cache
        if use_cache:
            async with self._cache_lock:
                if path_str in self._cache:
                    self._cache_hits += 1
                    self._cache.move_to_end(path_str)
                    return self._cache[path_str]
                self._cache_misses += 1

        async with self._semaphore:
            try:
                result = await self._do_extract(path, effective_mode, start_time)

                # Mettre en cache
                if use_cache:
                    async with self._cache_lock:
                        self._cache[path_str] = result
                        self._cache.move_to_end(path_str)
                        # Évincer les entrées les plus anciennes si cache plein
                        while len(self._cache) > self._cache_size:
                            self._cache.popitem(last=False)

                # Enregistrer les stats
                await self._record_extraction(result)

                return result

            except Exception as e:
                self._logger.error("Extraction échouée pour {}: {}", path, e)
                raise

    async def extract_batch(
        self,
        paths: list[Path],
        *,
        mode: ExtractionMode | None = None,
        on_progress: Any | None = None,
    ) -> list[ExtractionResult]:
        """Extrait les métadonnées d'un lot d'archives.

        Args:
            paths: Liste des chemins d'archives.
            mode: Mode d'extraction.
            on_progress: Callback appelé après chaque extraction
                         signature: (result: ExtractionResult, index: int, total: int) -> None.

        Returns:
            Liste des résultats d'extraction.
        """
        self._ensure_started()

        if not paths:
            return []

        effective_mode = mode or self._mode

        # Créer les tâches
        tasks = [
            asyncio.create_task(
                self.extract(path, mode=effective_mode),
                name=f"extract_{path.name}",
            )
            for path in paths
        ]

        # Exécuter
        results: list[ExtractionResult] = []
        for index, task in enumerate(tasks):
            try:
                result = await task
                results.append(result)
                if on_progress is not None:
                    on_progress(result, index, len(paths))
            except Exception as e:
                self._logger.warning("Extraction échouée dans le lot: {}", e)

        return results

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> ExtractionStats:
        """Retourne les statistiques agrégées de l'extracteur."""
        async with self._stats_lock:
            uptime = 0.0
            if self._start_time > 0:
                uptime = asyncio.get_event_loop().time() - self._start_time

            return ExtractionStats(
                total_extractions=self._total_extractions,
                successful=self._successful,
                failed=self._failed,
                no_metadata=self._no_metadata,
                by_format=dict(self._by_format),
                by_source=dict(self._by_source),
                cache_hits=self._cache_hits,
                cache_misses=self._cache_misses,
                total_duration_ms=self._total_duration_ms,
                uptime_seconds=uptime,
            )

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        async with self._stats_lock:
            self._total_extractions = 0
            self._successful = 0
            self._failed = 0
            self._no_metadata = 0
            self._by_format.clear()
            self._by_source.clear()
            self._cache_hits = 0
            self._cache_misses = 0
            self._total_duration_ms = 0.0

    async def clear_cache(self) -> None:
        """Vide le cache de métadonnées."""
        async with self._cache_lock:
            self._cache.clear()
        self._logger.debug("Cache de métadonnées vidé")

    # ------------------------------------------------------------------------
    # Méthodes internes — Extraction
    # ------------------------------------------------------------------------

    async def _do_extract(
        self,
        path: Path,
        mode: ExtractionMode,
        start_time: float,
    ) -> ExtractionResult:
        """Effectue l'extraction effective."""
        # Vérifier l'existence
        if not path.exists():
            raise ExtractionError(path, "Fichier inexistant")

        if not path.is_file():
            raise ExtractionError(path, "N'est pas un fichier")

        # Détecter le format
        archive_format = detect_archive_format(path)
        if archive_format == ArchiveFormat.UNKNOWN:
            raise UnsupportedArchiveFormatError(path, "unknown")

        # Taille de l'archive
        archive_size = path.stat().st_size

        # Extraire selon le format
        try:
            if archive_format in (ArchiveFormat.CBZ, ArchiveFormat.ZIP):
                comic_info, pages_count = await self._extract_from_zip(path)
                source = MetadataSource.COMIC_INFO if comic_info else MetadataSource.NONE
            elif archive_format == ArchiveFormat.CBR:
                comic_info, pages_count = await self._extract_from_cbr(path)
                source = MetadataSource.COMIC_INFO if comic_info else MetadataSource.NONE
            elif archive_format == ArchiveFormat.PDF:
                comic_info, pages_count = await self._extract_from_pdf(path)
                source = MetadataSource.PDF_INFO if comic_info else MetadataSource.NONE
            else:
                raise UnsupportedArchiveFormatError(path, archive_format.value)

            duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000.0

            # Gérer le cas sans métadonnées
            if comic_info is None:
                if mode == ExtractionMode.STRICT:
                    raise ExtractionError(path, "ComicInfo.xml absent")

                return ExtractionResult(
                    path=path,
                    archive_format=archive_format,
                    source=MetadataSource.NONE,
                    success=True,
                    comic_info=None,
                    extraction_duration_ms=duration_ms,
                    archive_size_bytes=archive_size,
                    pages_count=pages_count,
                )

            return ExtractionResult(
                path=path,
                archive_format=archive_format,
                source=source,
                success=True,
                comic_info=comic_info,
                extraction_duration_ms=duration_ms,
                archive_size_bytes=archive_size,
                pages_count=pages_count,
            )

        except ExtractionError:
            raise
        except UnsupportedArchiveFormatError:
            raise
        except Exception as e:
            if mode == ExtractionMode.BEST_EFFORT:
                duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000.0
                return ExtractionResult(
                    path=path,
                    archive_format=archive_format,
                    source=MetadataSource.NONE,
                    success=False,
                    error=str(e),
                    extraction_duration_ms=duration_ms,
                    archive_size_bytes=archive_size,
                )
            raise ExtractionError(path, str(e)) from e

    async def _extract_from_zip(
        self, path: Path
    ) -> tuple[dict[str, Any] | None, int]:
        """Extrait les métadonnées d'une archive ZIP/CBZ.

        Returns:
            Tuple (comic_info_dict, pages_count).
        """

        def _sync_extract() -> tuple[dict[str, Any] | None, int]:
            comic_info: dict[str, Any] | None = None
            pages_count = 0

            with zipfile.ZipFile(path, "r") as zf:
                # Chercher ComicInfo.xml
                for name in zf.namelist():
                    if name.lower() == self._COMIC_INFO_FILENAME.lower():
                        xml_content = zf.read(name)
                        comic_info = parse_comic_info_xml(xml_content)
                        break

                # Compter les pages (images)
                image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp"}
                for name in zf.namelist():
                    if Path(name).suffix.lower() in image_extensions:
                        pages_count += 1

            return comic_info, pages_count

        return await asyncio.to_thread(_sync_extract)

    async def _extract_from_cbr(
        self, path: Path
    ) -> tuple[dict[str, Any] | None, int]:
        """Extrait les métadonnées d'une archive CBR (RAR).

        Nécessite la librairie `rarfile`.
        """
        if not is_rarfile_available():
            raise ExtractionError(
                path,
                "La librairie 'rarfile' est requise pour les archives CBR. "
                "Installez-la avec: pip install rarfile",
            )

        def _sync_extract() -> tuple[dict[str, Any] | None, int]:
            import rarfile

            comic_info: dict[str, Any] | None = None
            pages_count = 0

            with rarfile.RarFile(path, "r") as rf:
                # Chercher ComicInfo.xml
                for name in rf.namelist():
                    if name.lower() == self._COMIC_INFO_FILENAME.lower():
                        xml_content = rf.read(name)
                        comic_info = parse_comic_info_xml(xml_content)
                        break

                # Compter les pages
                image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp"}
                for name in rf.namelist():
                    if Path(name).suffix.lower() in image_extensions:
                        pages_count += 1

            return comic_info, pages_count

        return await asyncio.to_thread(_sync_extract)

    async def _extract_from_pdf(
        self, path: Path
    ) -> tuple[dict[str, Any] | None, int]:
        """Extrait les métadonnées d'un PDF.

        Utilise pikepdf si disponible, sinon fallback sur les métadonnées basiques.
        """
        if not is_pikepdf_available():
            # Fallback : extraire juste le nombre de pages via zipfile (ne marche pas pour PDF)
            # On retourne None pour les métadonnées
            return None, 0

        def _sync_extract() -> tuple[dict[str, Any] | None, int]:
            import pikepdf

            comic_info: dict[str, Any] | None = None
            pages_count = 0

            try:
                with pikepdf.open(path) as pdf:
                    pages_count = len(pdf.pages)

                    # Extraire les métadonnées PDF
                    with pdf.open_metadata() as meta:
                        title = meta.get("dc:title")
                        author = meta.get("dc:creator")
                        subject = meta.get("dc:subject")

                        if title or author:
                            comic_info = {}
                            if title:
                                comic_info["Title"] = title
                            if author:
                                comic_info["Writer"] = author
                            if subject:
                                comic_info["Summary"] = subject
                            comic_info["PageCount"] = pages_count

            except Exception:
                pass

            return comic_info, pages_count

        return await asyncio.to_thread(_sync_extract)

    # ------------------------------------------------------------------------
    # Méthodes internes — Statistiques
    # ------------------------------------------------------------------------

    async def _record_extraction(self, result: ExtractionResult) -> None:
        """Enregistre une extraction dans les statistiques."""
        async with self._stats_lock:
            self._total_extractions += 1
            self._total_duration_ms += result.extraction_duration_ms

            if result.success:
                self._successful += 1
                if result.comic_info is None:
                    self._no_metadata += 1
            else:
                self._failed += 1

            # Par format
            fmt_key = result.archive_format.value
            self._by_format[fmt_key] = self._by_format.get(fmt_key, 0) + 1

            # Par source
            source_key = result.source.value
            self._by_source[source_key] = self._by_source.get(source_key, 0) + 1

    def _ensure_started(self) -> None:
        """Vérifie que l'extracteur est démarré."""
        if not self._started:
            raise MetadataNotStartedError("MetadataExtractor")

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<MetadataExtractor status={status} "
            f"cache_size={len(self._cache)} "
            f"extractions={self._total_extractions}>"
        )


# ============================================================================
# CLASSE — MetadataWriter
# ============================================================================


class MetadataWriter:
    """Écrivain asynchrone de métadonnées dans les archives.

    Supporte l'écriture de ComicInfo.xml dans CBZ et PDF.

    Lifecycle :
        >>> writer = MetadataWriter()
        >>> await writer.start()
        >>> await writer.write_metadata(archive, manga, chapter)
        >>> await writer.stop()
    """

    _DEFAULT_MAX_CONCURRENT: Final[int] = 4

    def __init__(
        self,
        *,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
        template_path: Path | None = None,
    ) -> None:
        """Initialise l'écrivain de métadonnées.

        Args:
            max_concurrent: Nombre maximum d'écritures simultanées.
            template_path: Chemin vers un template Jinja2 pour le rendu XML.
        """
        if max_concurrent <= 0:
            raise ValueError(f"max_concurrent must be positive, got {max_concurrent}")

        self._max_concurrent = max_concurrent
        self._template_path = template_path

        self._semaphore: asyncio.Semaphore | None = None
        self._started: bool = False
        self._start_time: float = 0.0

        # Statistiques
        self._total_writes: int = 0
        self._successful: int = 0
        self._failed: int = 0
        self._total_bytes_written: int = 0
        self._total_duration_ms: float = 0.0
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="metadata_writer")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre l'écrivain."""
        if self._started:
            self._logger.warning("MetadataWriter déjà démarré, ignore")
            return

        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._started = True
        self._start_time = asyncio.get_event_loop().time()

        self._logger.info("MetadataWriter démarré: max_concurrent={}", self._max_concurrent)

    async def stop(self) -> None:
        """Arrête l'écrivain."""
        if not self._started:
            return

        self._semaphore = None
        self._started = False
        self._logger.info("MetadataWriter arrêté")

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------------

    async def write_metadata(
        self,
        archive: Path,
        *,
        manga: Manga | None = None,
        chapter: Chapter | None = None,
        comic_info: ComicInfo | None = None,
    ) -> WriteResult:
        """Écrit les métadonnées dans une archive.

        Args:
            archive: Chemin de l'archive à modifier.
            manga: Modèle Manga (optionnel si comic_info fourni).
            chapter: Modèle Chapter (optionnel si comic_info fourni).
            comic_info: Modèle ComicInfo pré-construit (prioritaire).

        Returns:
            Résultat de l'écriture.

        Raises:
            MetadataNotStartedError: Si l'écrivain n'est pas démarré.
            WriteError: Si l'écriture échoue.
        """
        self._ensure_started()
        assert self._semaphore is not None

        start_time = asyncio.get_event_loop().time()

        # Construire le ComicInfo si non fourni
        if comic_info is None:
            if manga is None and chapter is None:
                raise WriteError(archive, "Aucune métadonnée fournie")

            from nexusdl.core.library.metadata import MetadataMapper
            comic_info = MetadataMapper.build_comic_info(manga=manga, chapter=chapter)

        # Détecter le format
        archive_format = detect_archive_format(archive)

        async with self._semaphore:
            try:
                if archive_format in (ArchiveFormat.CBZ, ArchiveFormat.ZIP):
                    bytes_written = await self._write_to_zip(archive, comic_info)
                elif archive_format == ArchiveFormat.PDF:
                    bytes_written = await self._write_to_pdf(archive, comic_info)
                else:
                    raise UnsupportedArchiveFormatError(archive, archive_format.value)

                duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000.0

                result = WriteResult(
                    path=archive,
                    archive_format=archive_format,
                    success=True,
                    bytes_written=bytes_written,
                    write_duration_ms=duration_ms,
                )

                # Stats
                async with self._stats_lock:
                    self._total_writes += 1
                    self._successful += 1
                    self._total_bytes_written += bytes_written
                    self._total_duration_ms += duration_ms

                return result

            except Exception as e:
                async with self._stats_lock:
                    self._total_writes += 1
                    self._failed += 1

                self._logger.error("Écriture échouée pour {}: {}", archive, e)
                raise WriteError(archive, str(e)) from e

    # ------------------------------------------------------------------------
    # Méthodes internes
    # ------------------------------------------------------------------------

    async def _write_to_zip(self, archive: Path, comic_info: ComicInfo) -> int:
        """Écrit ComicInfo.xml dans une archive ZIP/CBZ."""

        def _sync_write() -> int:
            # Convertir en dictionnaire
            data = comic_info.model_dump(exclude_none=True)

            # Rendre le XML
            xml_content = render_comic_info_xml(data, self._template_path)
            xml_bytes = xml_content.encode("utf-8")

            # Créer un nouveau ZIP avec ComicInfo.xml ajouté
            temp_path = archive.with_suffix(".cbz.tmp")

            with zipfile.ZipFile(archive, "r") as zf_in:
                with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as zf_out:
                    # Copier tous les fichiers existants
                    for item in zf_in.infolist():
                        if item.filename.lower() != self._COMIC_INFO_FILENAME.lower():
                            zf_out.writestr(item, zf_in.read(item.filename))

                    # Ajouter ComicInfo.xml
                    zf_out.writestr(self._COMIC_INFO_FILENAME, xml_bytes)

            # Remplacer l'original
            temp_path.replace(archive)

            return len(xml_bytes)

        return await asyncio.to_thread(_sync_write)

    async def _write_to_pdf(self, archive: Path, comic_info: ComicInfo) -> int:
        """Écrit les métadonnées dans un PDF."""
        if not is_pikepdf_available():
            raise WriteError(
                archive,
                "La librairie 'pikepdf' est requise pour écrire dans les PDF. "
                "Installez-la avec: pip install pikepdf",
            )

        def _sync_write() -> int:
            import pikepdf

            # Convertir en dictionnaire
            data = comic_info.model_dump(exclude_none=True)

            # Rendre le XML (pour l'embarquer en annexe)
            xml_content = render_comic_info_xml(data, self._template_path)
            xml_bytes = xml_content.encode("utf-8")

            with pikepdf.open(archive) as pdf:
                # Mettre à jour les métadonnées XMP
                with pdf.open_metadata() as meta:
                    if data.get("Title"):
                        meta["dc:title"] = data["Title"]
                    if data.get("Writer"):
                        meta["dc:creator"] = data["Writer"]
                    if data.get("Summary"):
                        meta["dc:subject"] = data["Summary"]

                # Sauvegarder
                temp_path = archive.with_suffix(".pdf.tmp")
                pdf.save(temp_path)

            temp_path.replace(archive)

            return len(xml_bytes)

        return await asyncio.to_thread(_sync_write)

    def _ensure_started(self) -> None:
        """Vérifie que l'écrivain est démarré."""
        if not self._started:
            raise MetadataNotStartedError("MetadataWriter")

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<MetadataWriter status={status} "
            f"writes={self._total_writes}>"
        )


# ============================================================================
# CLASSE — MetadataMapper
# ============================================================================


class MetadataMapper:
    """Convertisseur bidirectionnel entre ComicInfo et modèles de domaine.

    Fournit des méthodes statiques pour convertir :
        - ComicInfo → Manga
        - ComicInfo → Chapter
        - Manga + Chapter → ComicInfo
    """

    # Mapping des statuts
    _STATUS_MAP: ClassVar[dict[str, str]] = {
        "Ongoing": "ONGOING",
        "Continuing": "ONGOING",
        "Completed": "COMPLETED",
        "Ended": "COMPLETED",
        "Hiatus": "HIATUS",
        "Cancelled": "CANCELLED",
        "Discontinued": "DISCONTINUED",
        "Licensed": "LICENSED",
    }

    # Mapping des classifications
    _RATING_MAP: ClassVar[dict[str, str]] = {
        "Everyone": "SAFE",
        "G": "SAFE",
        "Teen": "SUGGESTIVE",
        "Teen+": "SUGGESTIVE",
        "Mature": "EROTICA",
        "R18+": "PORNOGRAPHIC",
        "Explicit": "PORNOGRAPHIC",
    }

    @staticmethod
    def comic_info_to_manga(comic_info: ComicInfo) -> Manga:
        """Convertit un ComicInfo en modèle Manga.

        Args:
            comic_info: Instance de ComicInfo.

        Returns:
            Instance de Manga avec les métadonnées mappées.
        """
        from nexusdl.core.models.manga import (
            ContentRating,
            Language,
            Manga,
            MangaStatus,
        )

        # Mapper le statut
        status_str = comic_info.series_status or "Unknown"
        status = MangaStatus[MetadataMapper._STATUS_MAP.get(status_str, "UNKNOWN")]

        # Mapper la classification
        rating_str = comic_info.age_rating or "Everyone"
        content_rating = ContentRating[MetadataMapper._RATING_MAP.get(rating_str, "SAFE")]

        # Mapper la langue
        lang_code = comic_info.language_iso or "en"
        try:
            language = Language(lang_code.lower())
        except ValueError:
            language = Language.EN

        # Mapper les genres
        genres: list[str] = []
        if comic_info.genre:
            genres = [g.strip() for g in comic_info.genre.split(",") if g.strip()]
        if comic_info.tags:
            genres.extend([t.strip() for t in comic_info.tags.split(",") if t.strip()])

        # Construire le Manga
        return Manga(
            id=f"comicinfo:{comic_info.series}:{comic_info.number}",
            source_id=comic_info.series,
            site="local",
            title=comic_info.series or comic_info.title or "Unknown",
            alternative_titles=[],
            description=comic_info.summary,
            author=comic_info.writer,
            artist=comic_info.penciller,
            genres=genres,
            status=status,
            year=comic_info.year,
            cover_url=None,
            language=language,
            content_rating=content_rating,
            chapters=[],
            url="",
            updated_at=datetime.now(UTC),
        )

    @staticmethod
    def comic_info_to_chapter(comic_info: ComicInfo) -> Chapter:
        """Convertit un ComicInfo en modèle Chapter.

        Args:
            comic_info: Instance de ComicInfo.

        Returns:
            Instance de Chapter avec les métadonnées mappées.
        """
        from nexusdl.core.models.manga import Chapter, Language

        # Mapper la langue
        lang_code = comic_info.language_iso or "en"
        try:
            language = Language(lang_code.lower())
        except ValueError:
            language = Language.EN

        # Numéro de chapitre
        number: float | str
        if comic_info.number:
            try:
                number = float(comic_info.number)
            except ValueError:
                number = comic_info.number
        else:
            number = "0"

        return Chapter(
            id=f"comicinfo:{comic_info.series}:{comic_info.number}",
            source_id=comic_info.number or "0",
            title=comic_info.title or "",
            number=number,
            volume=comic_info.volume,
            language=language,
            pages_count=comic_info.page_count,
            published_at=None,
            url="",
            pages=[],
        )

    @staticmethod
    def build_comic_info(
        *,
        manga: Manga | None = None,
        chapter: Chapter | None = None,
    ) -> ComicInfo:
        """Construit un ComicInfo à partir de modèles Manga/Chapter.

        Args:
            manga: Modèle Manga (optionnel).
            chapter: Modèle Chapter (optionnel).

        Returns:
            Instance de ComicInfo avec les métadonnées mappées.
        """
        from nexusdl.core.packaging.comic_info import ComicInfo

        data: dict[str, Any] = {}

        if manga is not None:
            data["Series"] = manga.title
            data["Summary"] = manga.description
            data["Writer"] = manga.author
            data["Penciller"] = manga.artist
            data["Genre"] = ", ".join(manga.genres) if manga.genres else None
            data["Year"] = manga.year
            data["LanguageISO"] = manga.language.value
            data["AgeRating"] = MetadataMapper._reverse_rating_map(manga.content_rating.value)
            data["Manga"] = "Yes"

        if chapter is not None:
            data["Title"] = chapter.title
            data["Number"] = str(chapter.number)
            data["Volume"] = chapter.volume
            data["PageCount"] = chapter.pages_count
            if chapter.language != (manga.language if manga else None):
                data["LanguageISO"] = chapter.language.value

        # Filtrer les None
        data = {k: v for k, v in data.items() if v is not None}

        return ComicInfo.model_validate(data)

    @staticmethod
    def _reverse_rating_map(rating: str) -> str:
        """Mappe une classification interne vers ComicInfo."""
        reverse_map = {
            "SAFE": "Everyone",
            "SUGGESTIVE": "Teen",
            "EROTICA": "Mature",
            "PORNOGRAPHIC": "R18+",
        }
        return reverse_map.get(rating, "Everyone")


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "MetadataError",
    "ExtractionError",
    "WriteError",
    "UnsupportedArchiveFormatError",
    "InvalidComicInfoError",
    "MetadataNotStartedError",
    # Enums
    "ArchiveFormat",
    "ExtractionMode",
    "MetadataSource",
    # Modèles
    "ExtractionResult",
    "WriteResult",
    "ExtractionStats",
    # Helpers
    "detect_archive_format",
    "is_rarfile_available",
    "is_pikepdf_available",
    "parse_comic_info_xml",
    "render_comic_info_xml",
    # Classes principales
    "MetadataExtractor",
    "MetadataWriter",
    "MetadataMapper",
]
