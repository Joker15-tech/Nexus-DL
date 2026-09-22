"""Empaquetage au format CBZ (Comic Book ZIP).

Ce module implémente le packager pour le format CBZ, qui est une archive ZIP
avec l'extension .cbz. C'est le format standard recommandé pour les lecteurs
de comics (ComicRack, CDisplayEx, Kavita, Suwayomi, etc.).

**Avantages du CBZ** :
    - Format ouvert et standardisé
    - Support natif par Python (module zipfile)
    - Compression ZIP efficace (ZIP_DEFLATED)
    - Embarquement de ComicInfo.xml pour les métadonnées
    - Compatible avec tous les lecteurs de comics modernes
    - Pas de dépendance externe requise

**Structure d'une archive CBZ** :
    chapter.cbz/
        ├── ComicInfo.xml          (optionnel, métadonnées)
        ├── page_001.jpg           (pages nommées séquentiellement)
        ├── page_002.jpg
        ├── page_003.png
        └── ...

Architecture :
    CbzPackager (hérite de BasePackager)
        ├── Valide les pages
        ├── Crée l'archive ZIP avec compression
        ├── Ajoute les pages avec nommage séquentiel
        ├── Ajoute ComicInfo.xml si métadonnées fournies
        └── Retourne le chemin final

Exemple d'utilisation :
    >>> from nexusdl.core.packaging.cbz_packager import CbzPackager
    >>> from nexusdl.core.packaging.base import PackagingFormat
    >>>
    >>> packager = CbzPackager()
    >>> assert packager.supports(PackagingFormat.CBZ)
    >>>
    >>> output = await packager.package(
    ...     pages=[Path("p1.jpg"), Path("p2.jpg")],
    ...     output=Path("/downloads/chapter.cbz"),
    ...     metadata=comic_info,
    ...     compression=6,  # 0-9, défaut: 6
    ... )
    >>> print(f"Archive créée: {output}")
    /downloads/chapter.cbz
"""

from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final

from loguru import logger

from nexusdl.core.packaging.base import (
    BasePackager,
    InvalidPagesError,
    MetadataError,
    PackagingFailedError,
    PackagingFormat,
    PackagingResult,
)

if TYPE_CHECKING:
    from nexusdl.core.packaging.comic_info import ComicInfo


# ============================================================================
# CONSTANTES
# ============================================================================


# Niveaux de compression ZIP
# 0 = store (pas de compression)
# 1 = best speed (compression minimale, rapide)
# 6 = default (compromis vitesse/taille, recommandé)
# 9 = best compression (compression maximale, plus lent)
_DEFAULT_COMPRESSION_LEVEL: Final[int] = 6

# Taille des chunks pour la lecture/écriture (64 KB)
_CHUNK_SIZE: Final[int] = 65536


# ============================================================================
# CLASSE PRINCIPALE — CbzPackager
# ============================================================================


class CbzPackager(BasePackager):
    """Packager pour le format CBZ (Comic Book ZIP).

    Crée des archives ZIP avec l'extension .cbz, supportées par tous les
    lecteurs de comics modernes. Format recommandé pour NexusDL.

    Attributes:
        format: Format d'empaquetage supporté (CBZ).
    """

    format: ClassVar[PackagingFormat] = PackagingFormat.CBZ

    def __init__(
        self,
        *,
        compression_level: int = _DEFAULT_COMPRESSION_LEVEL,
        allow_duplicates: bool = False,
    ) -> None:
        """Initialise le packager CBZ.

        Args:
            compression_level: Niveau de compression ZIP (0-9, défaut: 6).
                               0 = pas de compression, 9 = compression maximale.
            allow_duplicates: Autoriser les noms de fichiers dupliqués dans l'archive
                              (défaut: False, lève une erreur si doublon).

        Raises:
            ValueError: Si compression_level est hors limites.
        """
        if not 0 <= compression_level <= 9:
            raise ValueError(
                f"compression_level must be in [0, 9], got {compression_level}"
            )

        self._compression_level = compression_level
        self._allow_duplicates = allow_duplicates

        # Logger avec contexte
        self._logger = logger.bind(module="cbz_packager")

    # --------------------------------------------------------------------
    # Méthodes abstraites implémentées
    # --------------------------------------------------------------------

    def supports(self, fmt: PackagingFormat) -> bool:
        """Vérifie si ce packager supporte le format CBZ.

        Args:
            fmt: Format à vérifier.

        Returns:
            True si fmt est PackagingFormat.CBZ.
        """
        return fmt == PackagingFormat.CBZ

    async def package(
        self,
        pages: list[Path],
        output: Path,
        *,
        metadata: ComicInfo | None = None,
        **kwargs: Any,
    ) -> Path:
        """Empaquette les pages dans une archive CBZ (ZIP).

        Args:
            pages: Liste des chemins vers les images à empaqueter.
                   Les pages doivent être triées dans l'ordre de lecture.
            output: Chemin du fichier CBZ de destination.
                    Le répertoire parent sera créé si nécessaire.
            metadata: Métadonnées ComicInfo à embarquer (optionnel).
                      Si fourni, un fichier ComicInfo.xml sera ajouté à l'archive.
            **kwargs: Arguments spécifiques (compression_level).

        Returns:
            Chemin absolu du fichier CBZ créé.

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
            ...     compression=6,
            ... )
        """
        # Valider les pages
        self.validate_pages(pages)

        # Créer le répertoire parent
        self.ensure_parent_dir(output)

        # Compression level depuis kwargs si fourni
        compression = kwargs.get("compression_level", self._compression_level)

        self._logger.info(
            "Début de l'empaquetage CBZ: {} pages → {}",
            len(pages),
            output.name,
        )

        try:
            # Calculer le nombre de chiffres pour le padding
            num_digits = max(3, len(str(len(pages))))

            # Créer l'archive ZIP
            await asyncio.to_thread(
                self._create_zip_archive,
                pages,
                output,
                metadata,
                compression,
                num_digits,
            )

            # Vérifier que l'archive a été créée
            if not output.exists():
                raise PackagingFailedError(
                    output,
                    "L'archive CBZ n'a pas été créée (erreur inconnue)",
                )

            # Calculer les statistiques
            total_size = output.stat().st_size
            self._logger.info(
                "Empaquetage CBZ terminé: {} pages, {:.2f} MB",
                len(pages),
                total_size / (1024 * 1024),
            )

            return output

        except PackagingFailedError:
            raise
        except Exception as e:
            # Nettoyer le fichier partiel si existe
            if output.exists():
                try:
                    output.unlink()
                except OSError:
                    pass
            raise PackagingFailedError(output, str(e)) from e

    # --------------------------------------------------------------------
    # Méthodes internes — Création de l'archive
    # --------------------------------------------------------------------

    def _create_zip_archive(
        self,
        pages: list[Path],
        output: Path,
        metadata: ComicInfo | None,
        compression: int,
        num_digits: int,
    ) -> None:
        """Crée l'archive ZIP/CBZ (synchrone, exécuté dans un thread).

        Args:
            pages: Liste des chemins des pages.
            output: Chemin de l'archive de destination.
            metadata: Métadonnées ComicInfo (optionnel).
            compression: Niveau de compression (0-9).
            num_digits: Nombre de chiffres pour le padding des noms de fichiers.

        Raises:
            PackagingFailedError: Si la création échoue.
            MetadataError: Si les métadonnées sont invalides.
        """
        # Déterminer la méthode de compression
        if compression == 0:
            zip_compression = zipfile.ZIP_STORED
        else:
            zip_compression = zipfile.ZIP_DEFLATED

        # Track les noms de fichiers pour détecter les doublons
        added_files: set[str] = set()

        try:
            with zipfile.ZipFile(
                output,
                "w",
                compression=zip_compression,
                compresslevel=compression if compression > 0 else None,
            ) as zf:
                # 1. Ajouter ComicInfo.xml si métadonnées fournies
                if metadata is not None:
                    self._add_comicinfo_to_zip(zf, metadata, added_files)

                # 2. Ajouter les pages
                for index, page_path in enumerate(pages, start=1):
                    self._add_page_to_zip(
                        zf,
                        page_path,
                        index,
                        num_digits,
                        added_files,
                    )

        except zipfile.BadZipFile as e:
            raise PackagingFailedError(output, f"Archive ZIP corrompue: {e}") from e
        except PermissionError as e:
            raise PackagingFailedError(
                output,
                f"Permission refusée pour écrire l'archive: {e}",
            ) from e
        except OSError as e:
            raise PackagingFailedError(
                output,
                f"Erreur I/O lors de la création de l'archive: {e}",
            ) from e

    def _add_page_to_zip(
        self,
        zf: zipfile.ZipFile,
        page_path: Path,
        index: int,
        num_digits: int,
        added_files: set[str],
    ) -> None:
        """Ajoute une page à l'archive ZIP avec nommage séquentiel.

        Args:
            zf: Instance ZipFile ouverte.
            page_path: Chemin de la page à ajouter.
            index: Index de la page (1-based).
            num_digits: Nombre de chiffres pour le padding.
            added_files: Set des noms de fichiers déjà ajoutés.

        Raises:
            InvalidPagesError: Si la page ne peut pas être ajoutée.
        """
        # Générer le nom de fichier séquentiel
        extension = page_path.suffix.lower()
        filename = f"page_{index:0{num_digits}d}{extension}"

        # Vérifier les doublons
        if filename in added_files:
            if not self._allow_duplicates:
                raise InvalidPagesError(
                    f"Nom de fichier dupliqué dans l'archive: {filename}"
                )
            # Si doublons autorisés, ajouter un suffixe
            base = filename.rsplit(".", 1)[0]
            ext = filename.rsplit(".", 1)[1] if "." in filename else ""
            filename = f"{base}_dup{index}.{ext}"

        try:
            # Lire le contenu de la page
            with page_path.open("rb") as f:
                data = f.read()

            # Ajouter à l'archive
            zf.writestr(filename, data)
            added_files.add(filename)

            self._logger.trace("Page {} ajoutée: {}", index, filename)

        except Exception as e:
            raise InvalidPagesError(
                f"Impossible d'ajouter la page {index} ({page_path}): {e}"
            ) from e

    def _add_comicinfo_to_zip(
        self,
        zf: zipfile.ZipFile,
        metadata: ComicInfo,
        added_files: set[str],
    ) -> None:
        """Ajoute le fichier ComicInfo.xml à l'archive ZIP.

        Args:
            zf: Instance ZipFile ouverte.
            metadata: Métadonnées ComicInfo à écrire.
            added_files: Set des noms de fichiers déjà ajoutés.

        Raises:
            MetadataError: Si les métadonnées ne peuvent pas être écrites.
        """
        filename = self.get_comicinfo_filename()

        # Vérifier les doublons
        if filename in added_files and not self._allow_duplicates:
            raise MetadataError(
                f"Le fichier {filename} existe déjà dans l'archive"
            )

        try:
            # Générer le XML
            xml_content = metadata.to_xml()

            # Ajouter à l'archive
            zf.writestr(filename, xml_content.encode("utf-8"))
            added_files.add(filename)

            self._logger.debug("ComicInfo.xml ajouté à l'archive")

        except Exception as e:
            raise MetadataError(f"Impossible d'écrire ComicInfo.xml: {e}") from e

    # --------------------------------------------------------------------
    # Méthodes publiques — Utilitaires
    # --------------------------------------------------------------------

    @staticmethod
    def list_archive_contents(archive_path: Path) -> list[str]:
        """Liste le contenu d'une archive CBZ/CBR.

        Args:
            archive_path: Chemin de l'archive.

        Returns:
            Liste des noms de fichiers dans l'archive.

        Raises:
            PackagingFailedError: Si l'archive ne peut pas être lue.
        """
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                return zf.namelist()
        except Exception as e:
            raise PackagingFailedError(
                archive_path,
                f"Impossible de lire l'archive: {e}",
            ) from e

    @staticmethod
    def extract_comicinfo(archive_path: Path) -> str | None:
        """Extrait le contenu de ComicInfo.xml d'une archive CBZ.

        Args:
            archive_path: Chemin de l'archive.

        Returns:
            Contenu XML de ComicInfo.xml ou None si absent.

        Raises:
            PackagingFailedError: Si l'archive ne peut pas être lue.
        """
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                # Chercher ComicInfo.xml (case-insensitive)
                for name in zf.namelist():
                    if name.lower() == "comicinfo.xml":
                        return zf.read(name).decode("utf-8")
                return None
        except Exception as e:
            raise PackagingFailedError(
                archive_path,
                f"Impossible d'extraire ComicInfo.xml: {e}",
            ) from e

    def __repr__(self) -> str:
        return (
            f"<CbzPackager format={self.format.value} "
            f"compression={self._compression_level}>"
        )


# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================


def is_valid_cbz(archive_path: Path) -> bool:
    """Vérifie si un fichier est une archive CBZ valide.

    Args:
        archive_path: Chemin du fichier à vérifier.

    Returns:
        True si c'est une archive ZIP/CBZ valide.
    """
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            # Vérifier que c'est bien une archive ZIP
            return zf.testzip() is None
    except Exception:
        return False


def get_cbz_page_count(archive_path: Path) -> int:
    """Compte le nombre de pages (images) dans une archive CBZ.

    Args:
        archive_path: Chemin de l'archive.

    Returns:
        Nombre de pages (fichiers image) dans l'archive.
    """
    image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp"}

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            return sum(
                1
                for name in zf.namelist()
                if Path(name).suffix.lower() in image_extensions
            )
    except Exception:
        return 0


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    "CbzPackager",
    "is_valid_cbz",
    "get_cbz_page_count",
]
