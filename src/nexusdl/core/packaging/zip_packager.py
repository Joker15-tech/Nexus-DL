"""Empaquetage au format ZIP standard (usage général).

Ce module implémente le packager pour le format ZIP standard, qui est
techniquement identique au CBZ (même format d'archive ZIP) mais sans
les conventions spécifiques aux comics. Il est utilisé pour :

    - L'archivage général (pas spécifique aux mangas/comics)
    - La compatibilité avec des outils qui ne reconnaissent pas CBZ
    - Le partage de fichiers via des plateformes standard (email, cloud)
    - Le debugging (inspection facile via unzip standard)

**Différences avec CBZ** :
    - Pas de ComicInfo.xml embarqué par défaut (optionnel via paramètre)
    - Conservation des noms de fichiers originaux (pas de renommage séquentiel)
    - Pas de convention de nommage spécifique aux comics
    - Usage général (pas spécifique à la lecture de mangas)

**Structure d'une archive ZIP** :
    chapter.zip/
        ├── image001.jpg           (noms originaux conservés)
        ├── image002.jpg
        ├── image003.png
        ├── ComicInfo.xml          (optionnel, si metadata fourni)
        └── ...

Architecture :
    ZipPackager (hérite de BasePackager)
        ├── Valide les pages
        ├── Crée l'archive ZIP avec compression
        ├── Conserve les noms originaux (ou renommage séquentiel optionnel)
        ├── Ajoute ComicInfo.xml si demandé
        └── Retourne le chemin final

Exemple d'utilisation :
    >>> from nexusdl.core.packaging.zip_packager import ZipPackager
    >>> from nexusdl.core.packaging.base import PackagingFormat
    >>>
    >>> packager = ZipPackager()
    >>> assert packager.supports(PackagingFormat.ZIP)
    >>>
    >>> # Mode par défaut : noms originaux conservés
    >>> output = await packager.package(
    ...     pages=[Path("image001.jpg"), Path("image002.jpg")],
    ...     output=Path("/downloads/chapter.zip"),
    ... )
    >>> print(f"Archive créée: {output}")
    /downloads/chapter.zip
    >>>
    >>> # Mode avec renommage séquentiel
    >>> packager = ZipPackager(sequential_naming=True)
    >>> output = await packager.package(
    ...     pages=[Path("img_a.jpg"), Path("img_b.jpg")],
    ...     output=Path("/downloads/chapter.zip"),
    ... )
    >>> # Les fichiers seront nommés page_001.jpg, page_002.jpg
    >>>
    >>> # Mode avec ComicInfo.xml
    >>> output = await packager.package(
    ...     pages=[Path("p1.jpg"), Path("p2.jpg")],
    ...     output=Path("/downloads/chapter.zip"),
    ...     metadata=comic_info,
    ...     include_comicinfo=True,
    ... )
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
# EXCEPTIONS
# ============================================================================


class ZipPackagingError(PackagingFailedError):
    """Exception spécifique aux erreurs d'empaquetage ZIP."""


# ============================================================================
# CONSTANTES
# ============================================================================


# Niveaux de compression ZIP
# 0 = store (pas de compression)
# 1 = best speed (compression minimale, rapide)
# 6 = default (compromis vitesse/taille, recommandé)
# 9 = best compression (compression maximale, plus lent)
_DEFAULT_COMPRESSION_LEVEL: Final[int] = 6


# ============================================================================
# CLASSE PRINCIPALE — ZipPackager
# ============================================================================


class ZipPackager(BasePackager):
    """Packager pour le format ZIP standard (usage général).

    Crée des archives ZIP avec conservation des noms de fichiers originaux
    par défaut, ou renommage séquentiel optionnel. Format identique au CBZ
    mais sans les conventions spécifiques aux comics.

    Attributes:
        format: Format d'empaquetage supporté (ZIP).
    """

    format: ClassVar[PackagingFormat] = PackagingFormat.ZIP

    def __init__(
        self,
        *,
        compression_level: int = _DEFAULT_COMPRESSION_LEVEL,
        sequential_naming: bool = False,
        include_comicinfo: bool = False,
        allow_duplicates: bool = False,
    ) -> None:
        """Initialise le packager ZIP.

        Args:
            compression_level: Niveau de compression ZIP (0-9, défaut: 6).
                               0 = pas de compression, 9 = compression maximale.
            sequential_naming: Si True, renomme les fichiers en page_001.jpg, etc.
                               Si False (défaut), conserve les noms originaux.
            include_comicinfo: Si True, ajoute ComicInfo.xml si metadata fourni.
                               Si False (défaut), n'ajoute pas ComicInfo.xml.
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
        self._sequential_naming = sequential_naming
        self._include_comicinfo = include_comicinfo
        self._allow_duplicates = allow_duplicates

        # Logger avec contexte
        self._logger = logger.bind(
            module="zip_packager",
            compression=compression_level,
            sequential=sequential_naming,
        )

    # --------------------------------------------------------------------
    # Méthodes abstraites implémentées
    # --------------------------------------------------------------------

    def supports(self, fmt: PackagingFormat) -> bool:
        """Vérifie si ce packager supporte le format ZIP.

        Args:
            fmt: Format à vérifier.

        Returns:
            True si fmt est PackagingFormat.ZIP.
        """
        return fmt == PackagingFormat.ZIP

    async def package(
        self,
        pages: list[Path],
        output: Path,
        *,
        metadata: ComicInfo | None = None,
        **kwargs: Any,
    ) -> Path:
        """Empaquette les pages dans une archive ZIP standard.

        Args:
            pages: Liste des chemins vers les images à empaqueter.
                   Les pages doivent être triées dans l'ordre de lecture.
            output: Chemin du fichier ZIP de destination.
                    Le répertoire parent sera créé si nécessaire.
            metadata: Métadonnées ComicInfo à embarquer (optionnel).
                      Si fourni et include_comicinfo=True, un fichier
                      ComicInfo.xml sera ajouté à l'archive.
            **kwargs: Arguments spécifiques (compression_level, sequential_naming,
                      include_comicinfo).

        Returns:
            Chemin absolu du fichier ZIP créé.

        Raises:
            InvalidPagesError: Si la liste de pages est vide ou invalide.
            PackagingFailedError: Si l'empaquetage échoue.
            MetadataError: Si les métadonnées sont invalides.
            OSError: Si une erreur I/O survient.

        Example:
            >>> packager = ZipPackager()
            >>> output = await packager.package(
            ...     pages=[Path("p1.jpg"), Path("p2.jpg")],
            ...     output=Path("/downloads/chapter.zip"),
            ... )
        """
        # Valider les pages
        self.validate_pages(pages)

        # Créer le répertoire parent
        self.ensure_parent_dir(output)

        # Options depuis kwargs si fournis
        compression = kwargs.get("compression_level", self._compression_level)
        sequential = kwargs.get("sequential_naming", self._sequential_naming)
        include_comicinfo = kwargs.get("include_comicinfo", self._include_comicinfo)

        self._logger.info(
            "Début de l'empaquetage ZIP: {} pages → {} (compression={}, sequential={})",
            len(pages),
            output.name,
            compression,
            sequential,
        )

        try:
            # Calculer le nombre de chiffres pour le padding (si renommage séquentiel)
            num_digits = max(3, len(str(len(pages)))) if sequential else 0

            # Créer l'archive ZIP
            await asyncio.to_thread(
                self._create_zip_archive,
                pages,
                output,
                metadata if include_comicinfo else None,
                compression,
                sequential,
                num_digits,
            )

            # Vérifier que l'archive a été créée
            if not output.exists():
                raise PackagingFailedError(
                    output,
                    "L'archive ZIP n'a pas été créée (erreur inconnue)",
                )

            # Calculer les statistiques
            total_size = output.stat().st_size
            self._logger.info(
                "Empaquetage ZIP terminé: {} pages, {:.2f} MB",
                len(pages),
                total_size / (1024 * 1024),
            )

            return output

        except PackagingFailedError:
            raise
        except InvalidPagesError:
            raise
        except MetadataError:
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
        sequential: bool,
        num_digits: int,
    ) -> None:
        """Crée l'archive ZIP (synchrone, exécuté dans un thread).

        Args:
            pages: Liste des chemins des pages.
            output: Chemin de l'archive de destination.
            metadata: Métadonnées ComicInfo (optionnel).
            compression: Niveau de compression (0-9).
            sequential: Si True, renommage séquentiel.
            num_digits: Nombre de chiffres pour le padding.

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
                # 1. Ajouter ComicInfo.xml si demandé et metadata fourni
                if metadata is not None:
                    self._add_comicinfo_to_zip(zf, metadata, added_files)

                # 2. Ajouter les pages
                for index, page_path in enumerate(pages, start=1):
                    self._add_file_to_zip(
                        zf,
                        page_path,
                        index,
                        num_digits,
                        sequential,
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

    def _add_file_to_zip(
        self,
        zf: zipfile.ZipFile,
        page_path: Path,
        index: int,
        num_digits: int,
        sequential: bool,
        added_files: set[str],
    ) -> None:
        """Ajoute un fichier à l'archive ZIP.

        Args:
            zf: Instance ZipFile ouverte.
            page_path: Chemin du fichier à ajouter.
            index: Index du fichier (1-based).
            num_digits: Nombre de chiffres pour le padding (si sequential).
            sequential: Si True, renommage séquentiel.
            added_files: Set des noms de fichiers déjà ajoutés.

        Raises:
            InvalidPagesError: Si le fichier ne peut pas être ajouté.
        """
        # Déterminer le nom de fichier dans l'archive
        if sequential:
            # Renommage séquentiel : page_001.jpg, page_002.jpg, etc.
            extension = page_path.suffix.lower()
            filename = f"page_{index:0{num_digits}d}{extension}"
        else:
            # Conserver le nom original
            filename = page_path.name

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
            # Lire le contenu du fichier
            with page_path.open("rb") as f:
                data = f.read()

            # Ajouter à l'archive
            zf.writestr(filename, data)
            added_files.add(filename)

            self._logger.trace("Fichier {} ajouté: {}", index, filename)

        except Exception as e:
            raise InvalidPagesError(
                f"Impossible d'ajouter le fichier {index} ({page_path}): {e}"
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

            self._logger.debug("ComicInfo.xml ajouté à l'archive ZIP")

        except Exception as e:
            raise MetadataError(f"Impossible d'écrire ComicInfo.xml: {e}") from e

    # --------------------------------------------------------------------
    # Méthodes publiques — Utilitaires
    # --------------------------------------------------------------------

    @staticmethod
    def list_archive_contents(archive_path: Path) -> list[str]:
        """Liste le contenu d'une archive ZIP.

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
    def extract_file(archive_path: Path, filename: str, dest: Path) -> Path:
        """Extrait un fichier spécifique d'une archive ZIP.

        Args:
            archive_path: Chemin de l'archive.
            filename: Nom du fichier à extraire.
            dest: Répertoire de destination.

        Returns:
            Chemin du fichier extrait.

        Raises:
            PackagingFailedError: Si l'extraction échoue.
        """
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extract(filename, dest)
                return dest / filename
        except Exception as e:
            raise PackagingFailedError(
                archive_path,
                f"Impossible d'extraire {filename}: {e}",
            ) from e

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def compression_level(self) -> int:
        """Niveau de compression actuel."""
        return self._compression_level

    @property
    def sequential_naming(self) -> bool:
        """Indique si le renommage séquentiel est activé."""
        return self._sequential_naming

    @property
    def include_comicinfo(self) -> bool:
        """Indique si ComicInfo.xml est inclus par défaut."""
        return self._include_comicinfo

    # --------------------------------------------------------------------
    # Représentation
    # --------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<ZipPackager format={self.format.value} "
            f"compression={self._compression_level} "
            f"sequential={self._sequential_naming} "
            f"comicinfo={self._include_comicinfo}>"
        )


# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================


def is_valid_zip(archive_path: Path) -> bool:
    """Vérifie si un fichier est une archive ZIP valide.

    Args:
        archive_path: Chemin du fichier à vérifier.

    Returns:
        True si c'est une archive ZIP valide.
    """
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            # Vérifier que c'est bien une archive ZIP
            return zf.testzip() is None
    except Exception:
        return False


def get_zip_file_count(archive_path: Path) -> int:
    """Compte le nombre de fichiers dans une archive ZIP.

    Args:
        archive_path: Chemin de l'archive.

    Returns:
        Nombre de fichiers dans l'archive.
    """
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            return len(zf.namelist())
    except Exception:
        return 0


def get_zip_total_size(archive_path: Path) -> int:
    """Calcule la taille totale des fichiers dans une archive ZIP (décompressée).

    Args:
        archive_path: Chemin de l'archive.

    Returns:
        Taille totale décompressée en bytes.
    """
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            return sum(info.file_size for info in zf.infolist())
    except Exception:
        return 0


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "ZipPackagingError",
    # Classe principale
    "ZipPackager",
    # Helpers
    "is_valid_zip",
    "get_zip_file_count",
    "get_zip_total_size",
]
