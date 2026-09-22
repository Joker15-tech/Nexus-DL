"""Empaquetage au format CBR (Comic Book RAR).

Ce module implémente le packager pour le format CBR, qui est une archive RAR
renommée avec l'extension .cbr. Ce format est supporté par de nombreux lecteurs
de comics (ComicRack, CDisplayEx, etc.) mais nécessite l'outil externe `rar`
(WinRAR) pour la création d'archives.

**Contraintes techniques** :
    - La librairie Python `rarfile` peut LIRE les archives RAR mais ne peut
      PAS les CRÉER (licence propriétaire de l'algorithme RAR).
    - Pour CRÉER des archives CBR, il faut l'outil externe `rar` (WinRAR)
      installé sur le système.
    - Si `rar` n'est pas disponible, le packager lève une erreur claire
      avec des instructions d'installation.

**Alternative** :
    Si le format CBR n'est pas disponible, utiliser CBZ (ZIP) qui est
    supporté nativement par Python et ne nécessite aucun outil externe.

Architecture :
    CbrPackager (hérite de BasePackager)
        ├── Vérifie la disponibilité de l'outil `rar`
        ├── Valide les pages
        ├── Crée l'archive RAR via subprocess
        ├── Ajoute ComicInfo.xml si métadonnées fournies
        └── Nettoie les fichiers temporaires

Exemple d'utilisation :
    >>> from nexusdl.core.packaging.cbr_packager import CbrPackager
    >>> from nexusdl.core.packaging.base import PackagingFormat
    >>>
    >>> packager = CbrPackager()
    >>> if packager.is_rar_available():
    ...     output = await packager.package(
    ...         pages=[Path("p1.jpg"), Path("p2.jpg")],
    ...         output=Path("/downloads/chapter.cbr"),
    ...         metadata=comic_info,
    ...     )
    ...     print(f"Archive créée: {output}")
    >>> else:
    ...     print("RAR non disponible, utiliser CBZ à la place")

Dépendances externes :
    - Outil `rar` (WinRAR) doit être installé et accessible dans le PATH
    - Linux : `sudo apt-get install rar` ou `sudo pacman -S rar`
    - macOS : `brew install rar` (nécessite Homebrew)
    - Windows : Installer WinRAR depuis https://www.winrar.fr/
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
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


# Commandes possibles pour l'outil RAR
_RAR_COMMANDS: Final[tuple[str, ...]] = (
    "rar",  # Standard (WinRAR sur Windows, rar sur Linux/macOS)
    "winrar",  # Alternative Windows
    "/usr/bin/rar",  # Chemin absolu Linux
    "/usr/local/bin/rar",  # Chemin absolu macOS
)

# Options de compression RAR
# 0 = store (pas de compression)
# 1 = fastest (compression minimale)
# 2 = fast
# 3 = normal (défaut)
# 4 = good
# 5 = best (compression maximale, plus lent)
_DEFAULT_COMPRESSION_LEVEL: Final[int] = 3

# Timeout pour la création de l'archive (secondes)
_RAR_TIMEOUT: Final[float] = 300.0  # 5 minutes


# ============================================================================
# CLASSE PRINCIPALE — CbrPackager
# ============================================================================


class CbrPackager(BasePackager):
    """Packager pour le format CBR (Comic Book RAR).

    Crée des archives RAR avec l'extension .cbr, supportées par les lecteurs
    de comics. Nécessite l'outil externe `rar` (WinRAR) installé sur le système.

    Attributes:
        format: Format d'empaquetage supporté (CBR).
    """

    format: ClassVar[PackagingFormat] = PackagingFormat.CBR

    def __init__(
        self,
        *,
        compression_level: int = _DEFAULT_COMPRESSION_LEVEL,
        rar_command: str | None = None,
        timeout: float = _RAR_TIMEOUT,
    ) -> None:
        """Initialise le packager CBR.

        Args:
            compression_level: Niveau de compression RAR (0-5, défaut: 3).
                               0 = pas de compression, 5 = compression maximale.
            rar_command: Chemin vers l'outil `rar` (défaut: détection automatique).
            timeout: Timeout pour la création de l'archive (secondes, défaut: 300).

        Raises:
            ValueError: Si compression_level est hors limites.
        """
        if not 0 <= compression_level <= 5:
            raise ValueError(
                f"compression_level must be in [0, 5], got {compression_level}"
            )

        self._compression_level = compression_level
        self._rar_command = rar_command
        self._timeout = timeout
        self._rar_path: str | None = None

        # Logger avec contexte
        self._logger = logger.bind(module="cbr_packager")

    # --------------------------------------------------------------------
    # Méthodes abstraites implémentées
    # --------------------------------------------------------------------

    def supports(self, fmt: PackagingFormat) -> bool:
        """Vérifie si ce packager supporte le format CBR.

        Args:
            fmt: Format à vérifier.

        Returns:
            True si fmt est PackagingFormat.CBR.
        """
        return fmt == PackagingFormat.CBR

    async def package(
        self,
        pages: list[Path],
        output: Path,
        *,
        metadata: ComicInfo | None = None,
        **kwargs: Any,
    ) -> Path:
        """Empaquette les pages dans une archive CBR (RAR).

        Args:
            pages: Liste des chemins vers les images à empaqueter.
                   Les pages doivent être triées dans l'ordre de lecture.
            output: Chemin du fichier CBR de destination.
                    Le répertoire parent sera créé si nécessaire.
            metadata: Métadonnées ComicInfo à embarquer (optionnel).
                      Si fourni, un fichier ComicInfo.xml sera ajouté à l'archive.
            **kwargs: Arguments spécifiques (compression_level, timeout).

        Returns:
            Chemin absolu du fichier CBR créé.

        Raises:
            InvalidPagesError: Si la liste de pages est vide ou invalide.
            PackagingFailedError: Si l'empaquetage échoue (RAR non disponible, etc.).
            MetadataError: Si les métadonnées sont invalides.
            OSError: Si une erreur I/O survient.

        Example:
            >>> packager = CbrPackager()
            >>> output = await packager.package(
            ...     pages=[Path("p1.jpg"), Path("p2.jpg")],
            ...     output=Path("/downloads/chapter.cbr"),
            ...     metadata=comic_info,
            ... )
        """
        # Vérifier la disponibilité de RAR
        if not self.is_rar_available():
            raise PackagingFailedError(
                output,
                "L'outil `rar` (WinRAR) n'est pas installé sur ce système. "
                "Installez-le ou utilisez le format CBZ (ZIP) à la place. "
                "Voir la documentation pour les instructions d'installation.",
            )

        # Valider les pages
        self.validate_pages(pages)

        # Créer le répertoire parent
        self.ensure_parent_dir(output)

        # Compression level depuis kwargs si fourni
        compression = kwargs.get("compression_level", self._compression_level)
        timeout = kwargs.get("timeout", self._timeout)

        self._logger.info(
            "Début de l'empaquetage CBR: {} pages → {}",
            len(pages),
            output.name,
        )

        # Créer un répertoire temporaire pour préparer l'archive
        with tempfile.TemporaryDirectory(prefix="nexusdl_cbr_") as temp_dir:
            temp_path = Path(temp_dir)

            try:
                # 1. Copier les pages dans le répertoire temporaire avec nommage séquentiel
                await self._prepare_pages(pages, temp_path)

                # 2. Ajouter ComicInfo.xml si métadonnées fournies
                if metadata is not None:
                    await self._add_comicinfo(metadata, temp_path)

                # 3. Créer l'archive RAR
                await self._create_rar_archive(temp_path, output, compression, timeout)

                # 4. Vérifier que l'archive a été créée
                if not output.exists():
                    raise PackagingFailedError(
                        output,
                        "L'archive RAR n'a pas été créée (erreur inconnue)",
                    )

                # 5. Calculer les statistiques
                total_size = output.stat().st_size
                self._logger.info(
                    "Empaquetage CBR terminé: {} pages, {:.2f} MB",
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
    # Méthodes publiques — Vérification de disponibilité
    # --------------------------------------------------------------------

    def is_rar_available(self) -> bool:
        """Vérifie si l'outil `rar` est disponible sur le système.

        Retourne True si l'outil `rar` est trouvé et exécutable.

        Returns:
            True si RAR est disponible, False sinon.
        """
        if self._rar_path is not None:
            return True

        # Si une commande spécifique est fournie, la tester
        if self._rar_command is not None:
            if self._test_rar_command(self._rar_command):
                self._rar_path = self._rar_command
                return True
            return False

        # Sinon, tester toutes les commandes possibles
        for cmd in _RAR_COMMANDS:
            if self._test_rar_command(cmd):
                self._rar_path = cmd
                self._logger.debug("Outil RAR trouvé: {}", cmd)
                return True

        self._logger.warning(
            "Outil RAR non trouvé. Le format CBR ne sera pas disponible. "
            "Installez WinRAR ou l'outil `rar` pour utiliser ce format."
        )
        return False

    def get_rar_version(self) -> str | None:
        """Récupère la version de l'outil `rar` si disponible.

        Returns:
            Chaîne de version (ex: "6.24") ou None si non disponible.
        """
        if not self.is_rar_available():
            return None

        try:
            result = subprocess.run(
                [self._rar_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            # Parser la sortie pour extraire la version
            # Format typique: "RAR 6.24 x64   Copyright (c) 1993-2023 Alexander Roshal"
            for line in result.stdout.split("\n"):
                if "RAR" in line and any(c.isdigit() for c in line):
                    parts = line.split()
                    for part in parts:
                        if part[0].isdigit() and "." in part:
                            return part
            return None
        except Exception as e:
            self._logger.debug("Impossible de récupérer la version RAR: {}", e)
            return None

    # --------------------------------------------------------------------
    # Méthodes internes — Préparation des fichiers
    # --------------------------------------------------------------------

    async def _prepare_pages(
        self,
        pages: list[Path],
        temp_dir: Path,
    ) -> None:
        """Copie les pages dans le répertoire temporaire avec nommage séquentiel.

        Args:
            pages: Liste des chemins des pages originales.
            temp_dir: Répertoire temporaire de destination.

        Raises:
            InvalidPagesError: Si une page ne peut pas être copiée.
        """
        # Déterminer le nombre de chiffres pour le padding
        num_digits = max(3, len(str(len(pages))))

        for index, page_path in enumerate(pages, start=1):
            # Générer le nom de fichier séquentiel
            extension = page_path.suffix.lower()
            filename = f"page_{index:0{num_digits}d}{extension}"
            dest_path = temp_dir / filename

            try:
                # Copier le fichier (async via to_thread)
                await asyncio.to_thread(shutil.copy2, page_path, dest_path)
                self._logger.trace("Page {} copiée: {}", index, filename)
            except Exception as e:
                raise InvalidPagesError(
                    f"Impossible de copier la page {index} ({page_path}): {e}"
                ) from e

    async def _add_comicinfo(
        self,
        metadata: ComicInfo,
        temp_dir: Path,
    ) -> None:
        """Ajoute le fichier ComicInfo.xml au répertoire temporaire.

        Args:
            metadata: Métadonnées ComicInfo à écrire.
            temp_dir: Répertoire temporaire.

        Raises:
            MetadataError: Si les métadonnées ne peuvent pas être écrites.
        """
        try:
            # Générer le XML
            xml_content = metadata.to_xml()

            # Écrire le fichier
            comicinfo_path = temp_dir / self.get_comicinfo_filename()
            await asyncio.to_thread(
                comicinfo_path.write_text,
                xml_content,
                encoding="utf-8",
            )

            self._logger.debug("ComicInfo.xml ajouté aux métadonnées")

        except Exception as e:
            raise MetadataError(f"Impossible d'écrire ComicInfo.xml: {e}") from e

    async def _create_rar_archive(
        self,
        source_dir: Path,
        output: Path,
        compression: int,
        timeout: float,
    ) -> None:
        """Crée l'archive RAR via subprocess.

        Args:
            source_dir: Répertoire contenant les fichiers à archiver.
            output: Chemin de l'archive CBR de destination.
            compression: Niveau de compression (0-5).
            timeout: Timeout en secondes.

        Raises:
            PackagingFailedError: Si la création de l'archive échoue.
        """
        assert self._rar_path is not None

        # Construire la commande RAR
        # rar a -ep1 -m<compression> -y <archive> <source_dir>
        # -a : ajouter des fichiers
        # -ep1 : exclure le chemin du répertoire de base (stocke les fichiers à la racine)
        # -m<n> : niveau de compression (0-5)
        # -y : répondre oui à toutes les questions
        # -o+ : écraser les fichiers existants
        cmd = [
            self._rar_path,
            "a",  # add
            "-ep1",  # exclude paths (store files at root)
            f"-m{compression}",  # compression level
            "-y",  # assume yes on all queries
            "-o+",  # overwrite existing files
            str(output),  # archive name
            str(source_dir / "*"),  # files to add
        ]

        self._logger.debug("Commande RAR: {}", " ".join(cmd))

        try:
            # Exécuter la commande RAR
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Attendre la fin avec timeout
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise PackagingFailedError(
                    output,
                    f"Timeout dépassé ({timeout}s) lors de la création de l'archive",
                )

            # Vérifier le code de retour
            if process.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="ignore").strip()
                raise PackagingFailedError(
                    output,
                    f"L'outil RAR a échoué (code {process.returncode}): {error_msg}",
                )

            # Logger la sortie standard (debug)
            if stdout:
                output_text = stdout.decode("utf-8", errors="ignore")
                self._logger.trace("Sortie RAR: {}", output_text[:500])

        except FileNotFoundError:
            raise PackagingFailedError(
                output,
                f"L'outil RAR n'a pas été trouvé à l'emplacement: {self._rar_path}",
            )
        except PermissionError:
            raise PackagingFailedError(
                output,
                f"Permission refusée pour exécuter l'outil RAR: {self._rar_path}",
            )
        except Exception as e:
            raise PackagingFailedError(output, f"Erreur lors de la création RAR: {e}") from e

    # --------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # --------------------------------------------------------------------

    @staticmethod
    def _test_rar_command(command: str) -> bool:
        """Teste si une commande RAR est disponible et exécutable.

        Args:
            command: Nom ou chemin de la commande à tester.

        Returns:
            True si la commande est disponible, False sinon.
        """
        try:
            result = subprocess.run(
                [command, "--version"],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            # Vérifier que c'est bien RAR (pas unrar qui est différent)
            return "RAR" in result.stdout.upper() and result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False
        except Exception:
            return False

    def __repr__(self) -> str:
        rar_status = "available" if self.is_rar_available() else "unavailable"
        return (
            f"<CbrPackager format={self.format.value} "
            f"rar={rar_status} compression={self._compression_level}>"
        )


# ============================================================================
# HELPER — Vérification rapide
# ============================================================================


def is_cbr_supported() -> bool:
    """Vérifie rapidement si le format CBR est supporté sur ce système.

    Fonction utilitaire pour vérifier la disponibilité de l'outil RAR
    sans instancier le packager.

    Returns:
        True si CBR est supporté, False sinon.

    Example:
        >>> if is_cbr_supported():
        ...     print("CBR disponible")
        ... else:
        ...     print("Utiliser CBZ à la place")
    """
    packager = CbrPackager()
    return packager.is_rar_available()


def get_cbr_installation_instructions() -> str:
    """Retourne les instructions d'installation de l'outil RAR.

    Returns:
        Chaîne de texte avec les instructions pour Linux, macOS et Windows.
    """
    return """
Pour utiliser le format CBR, vous devez installer l'outil `rar` (WinRAR) :

**Linux (Debian/Ubuntu)** :
    sudo apt-get install rar

**Linux (Arch)** :
    sudo pacman -S rar

**macOS (Homebrew)** :
    brew install rar

**Windows** :
    Télécharger et installer WinRAR depuis https://www.winrar.fr/
    Assurez-vous que rar.exe est dans le PATH système.

**Vérification** :
    rar --version

Après installation, redémarrez NexusDL pour détecter l'outil.
""".strip()


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    "CbrPackager",
    "is_cbr_supported",
    "get_cbr_installation_instructions",
]
