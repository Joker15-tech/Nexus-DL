"""Interface abstraite et types de base pour le système d'empaquetage.

Ce module définit le contrat fondamental (ABC) que chaque implémentation de
packager doit respecter, ainsi que les types et exceptions communs à tous
les formats d'empaquetage supportés par NexusDL.

Formats supportés :
    - CBZ  : Comic Book ZIP (standard, recommandé)
    - CBR  : Comic Book RAR (compatibilité ancienne)
    - PDF  : Document PDF (lecture sur tablette)
    - ZIP  : Archive ZIP standard
    - FOLDER : Dossier d'images brutes (non empaqueté)

Architecture :
    BasePackager (ABC)
        ├── CbzPackager
        ├── CbrPackager
        ├── PdfPackager
        ├── ZipPackager
        └── FolderPackager

    PackagingFormat (enum)
        ├── CBZ
        ├── CBR
        ├── PDF
        ├── ZIP
        └── FOLDER

Règles d'or :
    1. Tous les packagers doivent hériter de `BasePackager`.
    2. La méthode `package()` doit être async et retourner le chemin final.
    3. La méthode `supports()` doit vérifier la compatibilité avec un format.
    4. Les exceptions doivent hériter de `PackagingError`.
    5. Les métadonnées ComicInfo sont optionnelles mais recommandées.
    6. Le nettoyage des fichiers temporaires est de la responsabilité du packager.

Exemple d'utilisation :
    >>> from nexusdl.core.packaging.base import PackagingFormat, BasePackager
    >>> from nexusdl.core.packaging.cbz_packager import CbzPackager
    >>>
    >>> packager = CbzPackager()
    >>> assert packager.supports(PackagingFormat.CBZ)
    >>>
    >>> output_path = await packager.package(
    ...     pages=[Path("page001.jpg"), Path("page002.jpg")],
    ...     output=Path("/downloads/One_Piece_Ch001.cbz"),
    ...     metadata=comic_info,
    ... )
    >>> print(output_path)
    /downloads/One_Piece_Ch001.cbz
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError

if TYPE_CHECKING:
    from nexusdl.core.packaging.comic_info import ComicInfo


# ============================================================================
# EXCEPTIONS
# ============================================================================


class PackagingError(NexusDLError):
    """Exception de base pour toutes les erreurs liées à l'empaquetage."""


class UnsupportedFormatError(PackagingError):
    """Exception levée lorsqu'un format d'empaquetage n'est pas supporté."""

    def __init__(self, fmt: PackagingFormat) -> None:
        super().__init__(f"Format d'empaquetage non supporté: {fmt.value}")
        self.format = fmt


class PackagingFailedError(PackagingError):
    """Exception levée lorsqu'un empaquetage échoue."""

    def __init__(
        self,
        output_path: Path,
        reason: str = "",
    ) -> None:
        msg = f"Échec de l'empaquetage vers {output_path}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.output_path = output_path
        self.reason = reason


class InvalidPagesError(PackagingError):
    """Exception levée lorsque la liste de pages est invalide."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Liste de pages invalide: {reason}")
        self.reason = reason


class MetadataError(PackagingError):
    """Exception levée lorsqu'il y a une erreur avec les métadonnées."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Erreur de métadonnées: {reason}")
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class PackagingFormat(str, Enum):
    """Formats d'empaquetage supportés par NexusDL.

    CBZ    : Comic Book ZIP (standard, recommandé pour les lecteurs de comics).
    CBR    : Comic Book RAR (compatibilité avec les anciens lecteurs).
    PDF    : Document PDF (lecture sur tablette, impression).
    ZIP    : Archive ZIP standard (usage général).
    FOLDER : Dossier d'images brutes (pas d'empaquetage).
    """

    CBZ = "cbz"
    CBR = "cbr"
    PDF = "pdf"
    ZIP = "zip"
    FOLDER = "folder"

    @property
    def label(self) -> str:
        """Libellé humain du format."""
        return {
            PackagingFormat.CBZ: "CBZ (Comic Book ZIP)",
            PackagingFormat.CBR: "CBR (Comic Book RAR)",
            PackagingFormat.PDF: "PDF (Document)",
            PackagingFormat.ZIP: "ZIP (Archive)",
            PackagingFormat.FOLDER: "Dossier (Images brutes)",
        }[self]

    @property
    def extension(self) -> str:
        """Extension de fichier standard pour ce format."""
        return {
            PackagingFormat.CBZ: ".cbz",
            PackagingFormat.CBR: ".cbr",
            PackagingFormat.PDF: ".pdf",
            PackagingFormat.ZIP: ".zip",
            PackagingFormat.FOLDER: "",
        }[self]

    @property
    def mime_type(self) -> str:
        """Type MIME associé au format."""
        return {
            PackagingFormat.CBZ: "application/vnd.comicbook+zip",
            PackagingFormat.CBR: "application/vnd.comicbook-rar",
            PackagingFormat.PDF: "application/pdf",
            PackagingFormat.ZIP: "application/zip",
            PackagingFormat.FOLDER: "inode/directory",
        }[self]

    @property
    def is_archive(self) -> bool:
        """Indique si le format est une archive (vs dossier)."""
        return self != PackagingFormat.FOLDER

    @property
    def is_comic_format(self) -> bool:
        """Indique si le format est spécifique aux comics (CBZ/CBR)."""
        return self in (PackagingFormat.CBZ, PackagingFormat.CBR)

    @property
    def supports_comicinfo(self) -> bool:
        """Indique si le format supporte l'embarquement de ComicInfo.xml."""
        return self in (PackagingFormat.CBZ, PackagingFormat.CBR, PackagingFormat.ZIP)


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class PackagingResult(BaseModel):
    """Résultat immuable d'une opération d'empaquetage.

    Contient toutes les métadonnées de l'empaquetage pour le reporting
    et la traçabilité.
    """

    output_path: Path = Field(..., description="Chemin du fichier/dossier final.")
    format: PackagingFormat = Field(..., description="Format d'empaquetage utilisé.")
    pages_count: int = Field(..., ge=0, description="Nombre de pages empaquetées.")
    total_size_bytes: int = Field(..., ge=0, description="Taille totale en bytes.")
    duration_seconds: float = Field(..., ge=0.0, description="Durée de l'empaquetage.")
    metadata_included: bool = Field(
        default=False,
        description="True si les métadonnées ComicInfo ont été incluses.",
    )
    compression_ratio: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Ratio de compression (0.0 = pas de compression, 1.0 = max).",
    )
    success: bool = Field(..., description="True si l'empaquetage a réussi.")
    error: str | None = Field(
        default=None,
        description="Message d'erreur si l'empaquetage a échoué.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def total_size_human(self) -> str:
        """Taille totale formatée (ex: '2.3 MB')."""
        size = self.total_size_bytes
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024.0:
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"

    @property
    def average_page_size_bytes(self) -> int:
        """Taille moyenne d'une page en bytes."""
        if self.pages_count == 0:
            return 0
        return self.total_size_bytes // self.pages_count


class PackagingStats(BaseModel):
    """Statistiques agrégées du système d'empaquetage."""

    total_packaged: int = Field(default=0, ge=0)
    by_format: dict[str, int] = Field(
        default_factory=dict,
        description="Nombre d'empaquetages par format.",
    )
    total_bytes_output: int = Field(default=0, ge=0)
    total_pages_packaged: int = Field(default=0, ge=0)
    total_duration_seconds: float = Field(default=0.0, ge=0.0)
    successful: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def average_size_bytes(self) -> int:
        """Taille moyenne d'un empaquetage en bytes."""
        if self.total_packaged == 0:
            return 0
        return self.total_bytes_output // self.total_packaged

    @property
    def success_rate(self) -> float:
        """Taux de succès (0.0 à 1.0)."""
        if self.total_packaged == 0:
            return 0.0
        return self.successful / self.total_packaged


# ============================================================================
# CLASSE ABSTRAITE — BasePackager
# ============================================================================


class BasePackager(ABC):
    """Interface abstraite pour tous les packagers de NexusDL.

    Chaque format d'empaquetage (CBZ, CBR, PDF, ZIP, FOLDER) doit implémenter
    cette interface pour garantir une cohérence dans l'API et le comportement.

    Attributes:
        format: Format d'empaquetage supporté par ce packager (ClassVar).
    """

    format: ClassVar[PackagingFormat]

    @abstractmethod
    async def package(
        self,
        pages: list[Path],
        output: Path,
        *,
        metadata: ComicInfo | None = None,
        **kwargs: Any,
    ) -> Path:
        """Empaquette les pages dans le format cible.

        Args:
            pages: Liste des chemins vers les images à empaqueter.
                   Les pages doivent être triées dans l'ordre de lecture.
            output: Chemin du fichier/dossier de destination.
                    Le répertoire parent sera créé si nécessaire.
            metadata: Métadonnées ComicInfo à embarquer (optionnel).
                      Si fourni et que le format le supporte, un fichier
                      ComicInfo.xml sera ajouté à l'archive.
            **kwargs: Arguments spécifiques au format (ex: compression_level
                      pour ZIP/CBZ, quality pour PDF, etc.).

        Returns:
            Chemin absolu du fichier/dossier créé.

        Raises:
            InvalidPagesError: Si la liste de pages est vide ou invalide.
            PackagingFailedError: Si l'empaquetage échoue.
            MetadataError: Si les métadonnées sont invalides.
            OSError: Si une erreur I/O survient.

        Example:
            >>> packager = CbzPackager()
            >>> output = await packager.package(
            ...     pages=[Path("p1.jpg"), Path("p2.jpg")],
            ...     output=Path("/downloads/chapter.cbz"),
            ...     metadata=comic_info,
            ... )
        """
        pass

    @abstractmethod
    def supports(self, fmt: PackagingFormat) -> bool:
        """Vérifie si ce packager supporte un format donné.

        Args:
            fmt: Format à vérifier.

        Returns:
            True si ce packager peut empaqueter dans le format demandé.

        Example:
            >>> packager = CbzPackager()
            >>> packager.supports(PackagingFormat.CBZ)
            True
            >>> packager.supports(PackagingFormat.PDF)
            False
        """
        pass

    # --------------------------------------------------------------------
    # Méthodes concrètes réutilisables
    # --------------------------------------------------------------------

    def validate_pages(self, pages: list[Path]) -> None:
        """Valide la liste de pages avant empaquetage.

        Vérifie que :
            - La liste n'est pas vide
            - Tous les fichiers existent
            - Tous les fichiers sont des images valides

        Args:
            pages: Liste des chemins à valider.

        Raises:
            InvalidPagesError: Si la validation échoue.
        """
        if not pages:
            raise InvalidPagesError("La liste de pages est vide")

        for i, page in enumerate(pages):
            if not page.exists():
                raise InvalidPagesError(f"Page {i} introuvable: {page}")
            if not page.is_file():
                raise InvalidPagesError(f"Page {i} n'est pas un fichier: {page}")
            if page.stat().st_size == 0:
                raise InvalidPagesError(f"Page {i} est vide: {page}")

    def ensure_parent_dir(self, output: Path) -> None:
        """Crée le répertoire parent si nécessaire.

        Args:
            output: Chemin du fichier de sortie.
        """
        output.parent.mkdir(parents=True, exist_ok=True)

    def get_comicinfo_filename(self) -> str:
        """Retourne le nom de fichier standard pour ComicInfo.xml.

        Returns:
            Nom de fichier (toujours "ComicInfo.xml").
        """
        return "ComicInfo.xml"

    def render_comicinfo_xml(self, metadata: ComicInfo) -> str:
        """Convertit un objet ComicInfo en XML.

        Args:
            metadata: Métadonnées à convertir.

        Returns:
            Contenu XML sous forme de chaîne.
        """
        return metadata.to_xml()

    async def cleanup_temp_files(self, temp_files: list[Path]) -> None:
        """Nettoie les fichiers temporaires après empaquetage.

        Args:
            temp_files: Liste des fichiers temporaires à supprimer.
        """
        for temp_file in temp_files:
            try:
                if temp_file.exists():
                    temp_file.unlink()
            except OSError:
                pass  # Ignorer les erreurs de nettoyage

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} format={self.format.value}>"


# ============================================================================
# FACTORY — PackagerFactory
# ============================================================================


class PackagerFactory:
    """Factory pour créer des packagers selon le format demandé.

    Utilise le pattern Factory pour instancier le bon packager selon
    le format d'empaquetage souhaité.

    Example:
        >>> factory = PackagerFactory()
        >>> packager = factory.create(PackagingFormat.CBZ)
        >>> assert isinstance(packager, CbzPackager)
    """

    def __init__(self) -> None:
        """Initialise la factory avec les packagers disponibles."""
        # Importation différée pour éviter les dépendances circulaires
        from nexusdl.core.packaging.cbz_packager import CbzPackager
        from nexusdl.core.packaging.cbr_packager import CbrPackager
        from nexusdl.core.packaging.pdf_packager import PdfPackager
        from nexusdl.core.packaging.zip_packager import ZipPackager
        from nexusdl.core.packaging.folder_packager import FolderPackager

        self._packagers: dict[PackagingFormat, type[BasePackager]] = {
            PackagingFormat.CBZ: CbzPackager,
            PackagingFormat.CBR: CbrPackager,
            PackagingFormat.PDF: PdfPackager,
            PackagingFormat.ZIP: ZipPackager,
            PackagingFormat.FOLDER: FolderPackager,
        }

    def create(self, fmt: PackagingFormat) -> BasePackager:
        """Crée un packager pour le format demandé.

        Args:
            fmt: Format d'empaquetage souhaité.

        Returns:
            Instance du packager approprié.

        Raises:
            UnsupportedFormatError: Si le format n'est pas supporté.
        """
        packager_class = self._packagers.get(fmt)
        if packager_class is None:
            raise UnsupportedFormatError(fmt)
        return packager_class()

    def supports(self, fmt: PackagingFormat) -> bool:
        """Vérifie si un format est supporté par la factory.

        Args:
            fmt: Format à vérifier.

        Returns:
            True si le format est supporté.
        """
        return fmt in self._packagers

    def list_supported_formats(self) -> list[PackagingFormat]:
        """Liste tous les formats supportés.

        Returns:
            Liste des formats supportés.
        """
        return list(self._packagers.keys())


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "PackagingError",
    "UnsupportedFormatError",
    "PackagingFailedError",
    "InvalidPagesError",
    "MetadataError",
    # Enums
    "PackagingFormat",
    # Modèles
    "PackagingResult",
    "PackagingStats",
    # Classes
    "BasePackager",
    "PackagerFactory",
]
