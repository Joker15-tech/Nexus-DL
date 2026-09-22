"""Empaquetage au format FOLDER (dossier d'images brutes).

Ce module implémente le packager le plus simple : il ne crée pas d'archive
mais copie (ou déplace) les images dans un dossier avec un nommage séquentiel.
Ce format est utile pour :

    - Le debugging (inspection visuelle rapide des pages)
    - L'usage brut (traitement externe des images)
    - La compatibilité avec des outils qui ne supportent pas les archives
    - La préservation des images originales sans compression
    - Les webtoons en lecture verticale (scroll)

**Caractéristiques** :
    - Pas de compression (copie bit-à-bit des fichiers)
    - Nommage séquentiel : page_001.jpg, page_002.jpg, etc.
    - Option `move` pour déplacer au lieu de copier (économie d'espace)
    - Écriture optionnelle d'un fichier `ComicInfo.txt` avec les métadonnées
    - Préservation des timestamps et permissions (via shutil.copy2)
    - Création automatique du dossier de destination

**Structure du dossier créé** :
    chapter_folder/
        ├── ComicInfo.txt        (optionnel, métadonnées lisibles)
        ├── page_001.jpg         (pages nommées séquentiellement)
        ├── page_002.jpg
        ├── page_003.png
        └── ...

Architecture :
    FolderPackager (hérite de BasePackager)
        ├── Valide les pages
        ├── Crée le dossier de destination
        ├── Copie/déplace les pages avec nommage séquentiel
        ├── Écrit ComicInfo.txt si métadonnées fournies
        └── Retourne le chemin du dossier

Exemple d'utilisation :
    >>> from nexusdl.core.packaging.folder_packager import FolderPackager
    >>> from nexusdl.core.packaging.base import PackagingFormat
    >>>
    >>> packager = FolderPackager()
    >>> assert packager.supports(PackagingFormat.FOLDER)
    >>>
    >>> # Mode copie (défaut)
    >>> output = await packager.package(
    ...     pages=[Path("p1.jpg"), Path("p2.jpg")],
    ...     output=Path("/downloads/chapter_folder"),
    ...     metadata=comic_info,
    ... )
    >>> print(f"Dossier créé: {output}")
    /downloads/chapter_folder
    >>>
    >>> # Mode déplacement (économie d'espace)
    >>> packager = FolderPackager(mode="move")
    >>> output = await packager.package(
    ...     pages=[Path("/tmp/p1.jpg"), Path("/tmp/p2.jpg")],
    ...     output=Path("/downloads/chapter_folder"),
    ... )
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from enum import Enum
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
# ENUMS
# ============================================================================


class FolderMode(str, Enum):
    """Mode d'opération du FolderPackager.

    COPY  : Copie les fichiers (préserve les sources, plus lent, plus d'espace).
    MOVE  : Déplace les fichiers (supprime les sources, rapide, économie d'espace).
    LINK  : Crée des liens symboliques (rapide, peu d'espace, mais fragile).
    """

    COPY = "copy"
    MOVE = "move"
    LINK = "link"

    @property
    def label(self) -> str:
        """Libellé humain du mode."""
        return {
            FolderMode.COPY: "Copie",
            FolderMode.MOVE: "Déplacement",
            FolderMode.LINK: "Lien symbolique",
        }[self]


# ============================================================================
# CONSTANTES
# ============================================================================


# Nom du fichier de métadonnées texte
_METADATA_FILENAME: Final[str] = "ComicInfo.txt"

# Séparateur pour le fichier de métadonnées
_METADATA_SEPARATOR: Final[str] = "=" * 60


# ============================================================================
# CLASSE PRINCIPALE — FolderPackager
# ============================================================================


class FolderPackager(BasePackager):
    """Packager pour le format FOLDER (dossier d'images brutes).

    Copie ou déplace les images dans un dossier avec nommage séquentiel.
    Format le plus simple, sans compression ni empaquetage.

    Attributes:
        format: Format d'empaquetage supporté (FOLDER).
    """

    format: ClassVar[PackagingFormat] = PackagingFormat.FOLDER

    def __init__(
        self,
        *,
        mode: FolderMode = FolderMode.COPY,
        write_metadata: bool = True,
        preserve_timestamps: bool = True,
    ) -> None:
        """Initialise le packager FOLDER.

        Args:
            mode: Mode d'opération (COPY, MOVE, LINK). Défaut: COPY.
            write_metadata: Écrire un fichier ComicInfo.txt avec les métadonnées.
            preserve_timestamps: Préserver les timestamps des fichiers sources
                                 (uniquement en mode COPY).

        Raises:
            ValueError: Si le mode est invalide.
        """
        if not isinstance(mode, FolderMode):
            try:
                mode = FolderMode(mode)
            except ValueError as e:
                raise ValueError(
                    f"Mode invalide: {mode}. "
                    f"Valeurs acceptées: {[m.value for m in FolderMode]}"
                ) from e

        self._mode = mode
        self._write_metadata = write_metadata
        self._preserve_timestamps = preserve_timestamps

        # Logger avec contexte
        self._logger = logger.bind(
            module="folder_packager",
            mode=mode.value,
        )

    # --------------------------------------------------------------------
    # Méthodes abstraites implémentées
    # --------------------------------------------------------------------

    def supports(self, fmt: PackagingFormat) -> bool:
        """Vérifie si ce packager supporte le format FOLDER.

        Args:
            fmt: Format à vérifier.

        Returns:
            True si fmt est PackagingFormat.FOLDER.
        """
        return fmt == PackagingFormat.FOLDER

    async def package(
        self,
        pages: list[Path],
        output: Path,
        *,
        metadata: ComicInfo | None = None,
        **kwargs: Any,
    ) -> Path:
        """Copie/déplace les pages dans un dossier.

        Args:
            pages: Liste des chemins vers les images à traiter.
                   Les pages doivent être triées dans l'ordre de lecture.
            output: Chemin du dossier de destination.
                    Le dossier sera créé si nécessaire.
                    Si le dossier existe déjà, les fichiers seront ajoutés.
            metadata: Métadonnées ComicInfo à écrire (optionnel).
                      Si fourni et write_metadata=True, un fichier ComicInfo.txt
                      sera créé dans le dossier.
            **kwargs: Arguments spécifiques (mode, write_metadata).

        Returns:
            Chemin absolu du dossier créé.

        Raises:
            InvalidPagesError: Si la liste de pages est vide ou invalide.
            PackagingFailedError: Si l'opération échoue.
            MetadataError: Si les métadonnées ne peuvent pas être écrites.
            OSError: Si une erreur I/O survient.

        Example:
            >>> packager = FolderPackager()
            >>> output = await packager.package(
            ...     pages=[Path("p1.jpg"), Path("p2.jpg")],
            ...     output=Path("/downloads/chapter_folder"),
            ...     metadata=comic_info,
            ... )
        """
        # Valider les pages
        self.validate_pages(pages)

        # Options depuis kwargs si fournis
        mode = kwargs.get("mode", self._mode)
        if isinstance(mode, str):
            mode = FolderMode(mode)
        write_metadata = kwargs.get("write_metadata", self._write_metadata)

        self._logger.info(
            "Début de l'empaquetage FOLDER: {} pages → {} (mode={})",
            len(pages),
            output.name,
            mode.value,
        )

        try:
            # 1. Créer le dossier de destination
            await asyncio.to_thread(output.mkdir, parents=True, exist_ok=True)

            # 2. Copier/déplacer les pages
            await self._process_pages(pages, output, mode)

            # 3. Écrire les métadonnées si demandé
            if write_metadata and metadata is not None:
                await self._write_metadata_file(metadata, output)

            # 4. Vérifier que le dossier a été créé
            if not output.exists() or not output.is_dir():
                raise PackagingFailedError(
                    output,
                    "Le dossier n'a pas été créé (erreur inconnue)",
                )

            # 5. Calculer les statistiques
            total_size = await asyncio.to_thread(self._calculate_folder_size, output)
            self._logger.info(
                "Empaquetage FOLDER terminé: {} pages, {:.2f} MB",
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
            # En mode MOVE, tenter de restaurer les fichiers si possible
            if mode == FolderMode.MOVE:
                self._logger.warning(
                    "Échec en mode MOVE, certains fichiers ont peut-être été déplacés"
                )
            raise PackagingFailedError(output, str(e)) from e

    # --------------------------------------------------------------------
    # Méthodes internes — Traitement des pages
    # --------------------------------------------------------------------

    async def _process_pages(
        self,
        pages: list[Path],
        output_dir: Path,
        mode: FolderMode,
    ) -> None:
        """Copie/déplace/lie les pages dans le dossier de destination.

        Args:
            pages: Liste des chemins des pages.
            output_dir: Dossier de destination.
            mode: Mode d'opération (COPY, MOVE, LINK).

        Raises:
            InvalidPagesError: Si une page ne peut pas être traitée.
        """
        # Déterminer le nombre de chiffres pour le padding
        num_digits = max(3, len(str(len(pages))))

        # Traiter les pages en parallèle (avec limite de concurrence)
        semaphore = asyncio.Semaphore(8)  # Max 8 opérations I/O simultanées

        async def process_one(index: int, page_path: Path) -> None:
            async with semaphore:
                await self._process_single_page(
                    page_path, output_dir, index, num_digits, mode
                )

        # Créer les tâches
        tasks = [
            asyncio.create_task(process_one(i, page))
            for i, page in enumerate(pages, start=1)
        ]

        # Attendre toutes les tâches
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Vérifier les erreurs
        errors: list[tuple[int, Exception]] = []
        for i, result in enumerate(results, start=1):
            if isinstance(result, Exception):
                errors.append((i, result))

        if errors:
            error_msgs = [f"Page {i}: {e}" for i, e in errors]
            raise InvalidPagesError(
                f"Échec du traitement de {len(errors)} page(s): "
                + "; ".join(error_msgs[:3])  # Limiter la taille du message
            )

    async def _process_single_page(
        self,
        page_path: Path,
        output_dir: Path,
        index: int,
        num_digits: int,
        mode: FolderMode,
    ) -> None:
        """Traite une page individuelle (copie, déplacement ou lien).

        Args:
            page_path: Chemin de la page source.
            output_dir: Dossier de destination.
            index: Index de la page (1-based).
            num_digits: Nombre de chiffres pour le padding.
            mode: Mode d'opération.

        Raises:
            InvalidPagesError: Si la page ne peut pas être traitée.
        """
        # Générer le nom de fichier séquentiel
        extension = page_path.suffix.lower()
        filename = f"page_{index:0{num_digits}d}{extension}"
        dest_path = output_dir / filename

        try:
            if mode == FolderMode.COPY:
                # Copier avec préservation des métadonnées
                await asyncio.to_thread(
                    shutil.copy2 if self._preserve_timestamps else shutil.copy,
                    page_path,
                    dest_path,
                )

            elif mode == FolderMode.MOVE:
                # Déplacer (plus rapide, supprime la source)
                await asyncio.to_thread(shutil.move, str(page_path), str(dest_path))

            elif mode == FolderMode.LINK:
                # Créer un lien symbolique
                await asyncio.to_thread(dest_path.symlink_to, page_path.resolve())

            self._logger.trace(
                "Page {} traitée: {} → {} (mode={})",
                index,
                page_path.name,
                filename,
                mode.value,
            )

        except Exception as e:
            raise InvalidPagesError(
                f"Impossible de traiter la page {index} ({page_path}): {e}"
            ) from e

    # --------------------------------------------------------------------
    # Méthodes internes — Métadonnées
    # --------------------------------------------------------------------

    async def _write_metadata_file(
        self,
        metadata: ComicInfo,
        output_dir: Path,
    ) -> None:
        """Écrit un fichier ComicInfo.txt lisible dans le dossier.

        Le format est un texte simple clé-valeur, plus lisible que l'XML
        pour un usage humain.

        Args:
            metadata: Métadonnées ComicInfo à écrire.
            output_dir: Dossier de destination.

        Raises:
            MetadataError: Si l'écriture échoue.
        """
        metadata_path = output_dir / _METADATA_FILENAME

        try:
            # Générer le contenu texte
            content = self._format_metadata_as_text(metadata)

            # Écrire le fichier
            await asyncio.to_thread(
                metadata_path.write_text,
                content,
                encoding="utf-8",
            )

            self._logger.debug("ComicInfo.txt écrit dans le dossier")

        except Exception as e:
            raise MetadataError(f"Impossible d'écrire ComicInfo.txt: {e}") from e

    @staticmethod
    def _format_metadata_as_text(metadata: ComicInfo) -> str:
        """Formate les métadonnées ComicInfo en texte lisible.

        Args:
            metadata: Métadonnées à formater.

        Returns:
            Contenu texte formaté.
        """
        lines: list[str] = []

        # En-tête
        lines.append("ComicInfo - Métadonnées du chapitre")
        lines.append(_METADATA_SEPARATOR)
        lines.append("")

        # Identification
        lines.append("IDENTIFICATION")
        lines.append("-" * 40)
        lines.append(f"Titre: {metadata.title}")
        lines.append(f"Série: {metadata.series}")
        lines.append(f"Numéro: {metadata.number}")
        if metadata.volume is not None:
            lines.append(f"Volume: {metadata.volume}")
        lines.append("")

        # Contenu
        if metadata.summary:
            lines.append("RÉSUMÉ")
            lines.append("-" * 40)
            lines.append(metadata.summary)
            lines.append("")

        # Date
        if metadata.year is not None:
            lines.append("DATE DE PUBLICATION")
            lines.append("-" * 40)
            date_parts = [str(metadata.year)]
            if metadata.month is not None:
                date_parts.append(f"{metadata.month:02d}")
            if metadata.day is not None:
                date_parts.append(f"{metadata.day:02d}")
            lines.append("-".join(date_parts))
            lines.append("")

        # Équipe créative
        creative_team: list[tuple[str, str | None]] = [
            ("Auteur", metadata.writer),
            ("Dessinateur", metadata.penciller),
            ("Encreur", metadata.inker),
            ("Coloriste", metadata.colorist),
            ("Lettriste", metadata.letterer),
            ("Couverture", metadata.cover_artist),
            ("Éditeur", metadata.editor),
            ("Traducteur", metadata.translator),
        ]
        creative_lines = [(label, val) for label, val in creative_team if val]
        if creative_lines:
            lines.append("ÉQUIPE CRÉATIVE")
            lines.append("-" * 40)
            for label, value in creative_lines:
                lines.append(f"{label}: {value}")
            lines.append("")

        # Genres et tags
        if metadata.genres:
            lines.append("GENRES")
            lines.append("-" * 40)
            lines.append(", ".join(metadata.genres))
            lines.append("")

        if metadata.tags:
            lines.append("TAGS")
            lines.append("-" * 40)
            lines.append(", ".join(metadata.tags))
            lines.append("")

        # Personnages et équipes
        if metadata.characters:
            lines.append("PERSONNAGES")
            lines.append("-" * 40)
            lines.append(", ".join(metadata.characters))
            lines.append("")

        if metadata.teams:
            lines.append("ÉQUIPES")
            lines.append("-" * 40)
            lines.append(", ".join(metadata.teams))
            lines.append("")

        # Métadonnées techniques
        lines.append("INFORMATIONS TECHNIQUES")
        lines.append("-" * 40)
        lines.append(f"Nombre de pages: {metadata.page_count}")
        lines.append(f"Langue: {metadata.language_iso}")
        lines.append(f"Classification: {metadata.age_rating.value}")
        lines.append(f"Type: {metadata.manga.value}")
        if metadata.format_type.value != "Unknown":
            lines.append(f"Format: {metadata.format_type.value}")
        lines.append("")

        # Publication
        if metadata.publisher or metadata.web:
            lines.append("PUBLICATION")
            lines.append("-" * 40)
            if metadata.publisher:
                lines.append(f"Éditeur: {metadata.publisher}")
            if metadata.web:
                lines.append(f"Source: {metadata.web}")
            lines.append("")

        # Évaluation
        if metadata.community_rating is not None:
            lines.append("ÉVALUATION")
            lines.append("-" * 40)
            lines.append(f"Note communautaire: {metadata.community_rating:.1f}/5.0")
            lines.append("")

        # Arc narratif
        if metadata.story_arc:
            lines.append("ARC NARRATIF")
            lines.append("-" * 40)
            lines.append(metadata.story_arc)
            if metadata.story_arc_number:
                lines.append(f"Numéro: {metadata.story_arc_number}")
            lines.append("")

        # Notes
        if metadata.notes:
            lines.append("NOTES")
            lines.append("-" * 40)
            lines.append(metadata.notes)
            lines.append("")

        # Pied de page
        lines.append(_METADATA_SEPARATOR)
        lines.append(
            f"Généré par NexusDL le {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )

        return "\n".join(lines)

    # --------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # --------------------------------------------------------------------

    @staticmethod
    def _calculate_folder_size(folder: Path) -> int:
        """Calcule la taille totale d'un dossier (synchrone).

        Args:
            folder: Chemin du dossier.

        Returns:
            Taille totale en bytes.
        """
        total = 0
        for path in folder.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
        return total

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def mode(self) -> FolderMode:
        """Mode d'opération actuel."""
        return self._mode

    @property
    def write_metadata(self) -> bool:
        """Indique si les métadonnées sont écrites."""
        return self._write_metadata

    # --------------------------------------------------------------------
    # Représentation
    # --------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<FolderPackager format={self.format.value} "
            f"mode={self._mode.value} "
            f"metadata={self._write_metadata}>"
        )


# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================


def is_folder_packaged(path: Path) -> bool:
    """Vérifie si un chemin est un dossier empaqueté par FolderPackager.

    Détecte la présence du fichier ComicInfo.txt et d'au moins une image.

    Args:
        path: Chemin à vérifier.

    Returns:
        True si c'est un dossier empaqueté.
    """
    if not path.is_dir():
        return False

    # Vérifier la présence de ComicInfo.txt
    if not (path / _METADATA_FILENAME).exists():
        return False

    # Vérifier la présence d'au moins une image
    image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp"}
    for file in path.iterdir():
        if file.is_file() and file.suffix.lower() in image_extensions:
            return True

    return False


def count_folder_pages(folder: Path) -> int:
    """Compte le nombre de pages (images) dans un dossier empaqueté.

    Args:
        folder: Chemin du dossier.

    Returns:
        Nombre de fichiers image dans le dossier.
    """
    if not folder.is_dir():
        return 0

    image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp"}
    return sum(
        1
        for file in folder.iterdir()
        if file.is_file() and file.suffix.lower() in image_extensions
    )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    "FolderPackager",
    "FolderMode",
    "is_folder_packaged",
    "count_folder_pages",
]
