"""Utilitaires pour les opérations filesystem dans NexusDL.

Ce module fournit un ensemble complet de fonctions pour manipuler les fichiers
et répertoires de manière robuste, sécurisée et asynchrone. Il est utilisé
dans tout le projet pour :

    - Lecture/écriture de fichiers (texte, binaire, JSON, YAML)
    - Copie, déplacement, suppression de fichiers et répertoires
    - Création d'arborescences de répertoires
    - Opérations atomiques (write temporaire + rename)
    - Gestion des permissions (Unix)
    - Informations sur les fichiers (taille, dates, checksums)
    - Navigation dans l'arborescence (listing, recherche)
    - Gestion de l'espace disque
    - Fichiers temporaires sécurisés
    - Validation et sanitization de chemins

**Architecture** :
    - Fonctions pures (pas d'état global)
    - Support async via asyncio.to_thread
    - Opérations atomiques pour la sécurité
    - Validation stricte des chemins
    - Gestion robuste des erreurs
    - Thread-safe

**Exemples d'utilisation** :
    >>> from nexusdl.core.utils.filesystem import (
    ...     read_text, write_text, read_json, write_json,
    ...     copy_file, move_file, delete_file,
    ...     ensure_dir, list_files, atomic_write,
    ... )
    >>>
    >>> # Lecture/écriture
    >>> content = read_text(Path("config.yaml"))
    >>> write_text(Path("output.txt"), "Hello World")
    >>>
    >>> # JSON
    >>> data = read_json(Path("data.json"))
    >>> write_json(Path("output.json"), {"key": "value"})
    >>>
    >>> # Opérations atomiques
    >>> atomic_write(Path("config.yaml"), "new content")
    >>>
    >>> # Répertoires
    >>> ensure_dir(Path("/path/to/dir"))
    >>> files = list_files(Path("/path"), pattern="*.jpg")
    >>>
    >>> # Informations
    >>> stats = get_file_stats(Path("image.jpg"))
    >>> print(f"Taille: {stats.size_human}")

Intégration :
    - core/downloader/* : utilise ces utilitaires pour sauvegarder les images
    - core/library/* : utilise ces utilitaires pour scanner la bibliothèque
    - core/packaging/* : utilise ces utilitaires pour créer les archives
    - core/session/* : utilise ces utilitaires pour les cookies/cache
    - interfaces/* : utilise ces utilitaires pour l'import/export
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import stat
import tempfile
import time
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, Final, Iterable, Iterator, TextIO

import yaml
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# CONSTANTES
# ============================================================================


# Taille de buffer pour les opérations I/O (64 KB)
DEFAULT_BUFFER_SIZE: Final[int] = 65536

# Taille de buffer pour les opérations async (1 MB)
ASYNC_BUFFER_SIZE: Final[int] = 1048576

# Permissions par défaut pour les fichiers (Unix)
DEFAULT_FILE_MODE: Final[int] = 0o644

# Permissions par défaut pour les répertoires (Unix)
DEFAULT_DIR_MODE: Final[int] = 0o755

# Permissions sécurisées pour les fichiers sensibles
SECURE_FILE_MODE: Final[int] = 0o600

# Permissions sécurisées pour les répertoires sensibles
SECURE_DIR_MODE: Final[int] = 0o700

# Caractères interdits dans les noms de fichiers (multi-plateforme)
INVALID_FILENAME_CHARS: Final[frozenset[str]] = frozenset({
    "<", ">", ":", '"', "/", "\\", "|", "?", "*",
    "\x00", "\x01", "\x02", "\x03", "\x04", "\x05", "\x06", "\x07",
    "\x08", "\x0b", "\x0c", "\x0e", "\x0f",
    "\x10", "\x11", "\x12", "\x13", "\x14", "\x15", "\x16", "\x17",
    "\x18", "\x19", "\x1a", "\x1b", "\x1c", "\x1d", "\x1e", "\x1f",
})

# Noms de fichiers réservés (Windows)
RESERVED_FILENAMES: Final[frozenset[str]] = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
})

# Extensions de fichiers courants
TEXT_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".txt", ".md", ".rst", ".log", ".csv", ".tsv",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".html", ".htm", ".css", ".js", ".py", ".sh", ".bat",
})

BINARY_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".tiff",
    ".pdf", ".cbz", ".cbr", ".zip", ".rar", ".7z",
    ".mp3", ".mp4", ".avi", ".mkv", ".mov",
})


# ============================================================================
# EXCEPTIONS
# ============================================================================


class FilesystemError(NexusDLError):
    """Exception de base pour les erreurs filesystem."""


class FileNotFoundError(FilesystemError):
    """Exception levée lorsqu'un fichier est introuvable.

    Attributes:
        path: Chemin du fichier introuvable.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(f"Fichier introuvable: {path}")
        self.path = path


class DirectoryNotFoundError(FilesystemError):
    """Exception levée lorsqu'un répertoire est introuvable.

    Attributes:
        path: Chemin du répertoire introuvable.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(f"Répertoire introuvable: {path}")
        self.path = path


class FileExistsError(FilesystemError):
    """Exception levée lorsqu'un fichier existe déjà.

    Attributes:
        path: Chemin du fichier existant.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(f"Le fichier existe déjà: {path}")
        self.path = path


class DirectoryExistsError(FilesystemError):
    """Exception levée lorsqu'un répertoire existe déjà.

    Attributes:
        path: Chemin du répertoire existant.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(f"Le répertoire existe déjà: {path}")
        self.path = path


class PermissionError(FilesystemError):
    """Exception levée lorsqu'une opération est refusée par les permissions.

    Attributes:
        path: Chemin du fichier/répertoire.
        operation: Opération tentée.
    """

    def __init__(self, path: Path, operation: str) -> None:
        super().__init__(f"Permission refusée pour {operation} sur {path}")
        self.path = path
        self.operation = operation


class InvalidPathError(FilesystemError):
    """Exception levée lorsqu'un chemin est invalide.

    Attributes:
        path: Chemin invalide.
        reason: Raison de l'invalidité.
    """

    def __init__(self, path: Any, reason: str = "") -> None:
        msg = f"Chemin invalide: {path!r}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.path = path
        self.reason = reason


class DiskFullError(FilesystemError):
    """Exception levée lorsqu'il n'y a plus d'espace disque.

    Attributes:
        path: Chemin où l'écriture a échoué.
        required_bytes: Espace requis en bytes.
        available_bytes: Espace disponible en bytes.
    """

    def __init__(
        self,
        path: Path,
        required_bytes: int,
        available_bytes: int,
    ) -> None:
        super().__init__(
            f"Espace disque insuffisant pour {path}: "
            f"requis {required_bytes} bytes, disponible {available_bytes} bytes"
        )
        self.path = path
        self.required_bytes = required_bytes
        self.available_bytes = available_bytes


class AtomicOperationError(FilesystemError):
    """Exception levée lorsqu'une opération atomique échoue.

    Attributes:
        path: Chemin cible.
        reason: Raison de l'échec.
    """

    def __init__(self, path: Path, reason: str = "") -> None:
        msg = f"Opération atomique échouée pour {path}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.path = path
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class FileType(str, Enum):
    """Type de fichier.

    Attributes:
        FILE: Fichier régulier.
        DIRECTORY: Répertoire.
        SYMLINK: Lien symbolique.
        FIFO: FIFO (pipe nommé).
        SOCKET: Socket.
        BLOCK_DEVICE: Périphérique bloc.
        CHAR_DEVICE: Périphérique caractère.
        UNKNOWN: Type inconnu.
    """

    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    FIFO = "fifo"
    SOCKET = "socket"
    BLOCK_DEVICE = "block_device"
    CHAR_DEVICE = "char_device"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            FileType.FILE: "Fichier",
            FileType.DIRECTORY: "Répertoire",
            FileType.SYMLINK: "Lien symbolique",
            FileType.FIFO: "FIFO",
            FileType.SOCKET: "Socket",
            FileType.BLOCK_DEVICE: "Périphérique bloc",
            FileType.CHAR_DEVICE: "Périphérique caractère",
            FileType.UNKNOWN: "Inconnu",
        }[self]


class SortBy(str, Enum):
    """Critère de tri pour le listing de fichiers.

    Attributes:
        NAME: Tri par nom.
        SIZE: Tri par taille.
        MODIFIED: Tri par date de modification.
        CREATED: Tri par date de création.
        EXTENSION: Tri par extension.
    """

    NAME = "name"
    SIZE = "size"
    MODIFIED = "modified"
    CREATED = "created"
    EXTENSION = "extension"


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class FileStats(BaseModel):
    """Statistiques d'un fichier.

    Attributes:
        path: Chemin du fichier.
        name: Nom du fichier.
        extension: Extension du fichier.
        size: Taille en bytes.
        created_at: Date de création.
        modified_at: Date de modification.
        accessed_at: Date d'accès.
        is_file: True si c'est un fichier.
        is_dir: True si c'est un répertoire.
        is_symlink: True si c'est un lien symbolique.
        permissions: Permissions (mode Unix).
        owner_uid: UID du propriétaire (Unix).
        group_gid: GID du groupe (Unix).
    """

    path: Path = Field(..., description="Chemin du fichier.")
    name: str = Field(..., description="Nom du fichier.")
    extension: str = Field(default="", description="Extension du fichier.")
    size: int = Field(..., ge=0, description="Taille en bytes.")
    created_at: datetime = Field(..., description="Date de création.")
    modified_at: datetime = Field(..., description="Date de modification.")
    accessed_at: datetime = Field(..., description="Date d'accès.")
    is_file: bool = Field(default=False, description="True si fichier.")
    is_dir: bool = Field(default=False, description="True si répertoire.")
    is_symlink: bool = Field(default=False, description="True si symlink.")
    permissions: int = Field(default=0, description="Permissions (mode Unix).")
    owner_uid: int | None = Field(default=None, description="UID du propriétaire.")
    group_gid: int | None = Field(default=None, description="GID du groupe.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def size_human(self) -> str:
        """Taille formatée pour l'affichage."""
        from nexusdl.core.utils.text import format_size
        return format_size(self.size)

    @property
    def file_type(self) -> FileType:
        """Type de fichier."""
        if self.is_symlink:
            return FileType.SYMLINK
        if self.is_dir:
            return FileType.DIRECTORY
        if self.is_file:
            return FileType.FILE
        return FileType.UNKNOWN

    @property
    def permissions_human(self) -> str:
        """Permissions au format humain (ex: 'rw-r--r--')."""
        if self.permissions == 0:
            return "----------"

        mode = self.permissions
        perms = ""

        # Owner
        perms += "r" if mode & stat.S_IRUSR else "-"
        perms += "w" if mode & stat.S_IWUSR else "-"
        perms += "x" if mode & stat.S_IXUSR else "-"

        # Group
        perms += "r" if mode & stat.S_IRGRP else "-"
        perms += "w" if mode & stat.S_IWGRP else "-"
        perms += "x" if mode & stat.S_IXGRP else "-"

        # Others
        perms += "r" if mode & stat.S_IROTH else "-"
        perms += "w" if mode & stat.S_IWOTH else "-"
        perms += "x" if mode & stat.S_IXOTH else "-"

        return perms


class DirectoryStats(BaseModel):
    """Statistiques d'un répertoire.

    Attributes:
        path: Chemin du répertoire.
        name: Nom du répertoire.
        file_count: Nombre de fichiers.
        dir_count: Nombre de sous-répertoires.
        total_size: Taille totale en bytes.
        created_at: Date de création.
        modified_at: Date de modification.
    """

    path: Path = Field(..., description="Chemin du répertoire.")
    name: str = Field(..., description="Nom du répertoire.")
    file_count: int = Field(default=0, ge=0, description="Nombre de fichiers.")
    dir_count: int = Field(default=0, ge=0, description="Nombre de sous-répertoires.")
    total_size: int = Field(default=0, ge=0, description="Taille totale en bytes.")
    created_at: datetime = Field(..., description="Date de création.")
    modified_at: datetime = Field(..., description="Date de modification.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def total_size_human(self) -> str:
        """Taille totale formatée."""
        from nexusdl.core.utils.text import format_size
        return format_size(self.total_size)

    @property
    def item_count(self) -> int:
        """Nombre total d'items (fichiers + répertoires)."""
        return self.file_count + self.dir_count


class DiskUsage(BaseModel):
    """Utilisation de l'espace disque.

    Attributes:
        path: Chemin du point de montage.
        total: Espace total en bytes.
        used: Espace utilisé en bytes.
        free: Espace libre en bytes.
        percent_used: Pourcentage utilisé (0.0 à 100.0).
    """

    path: Path = Field(..., description="Chemin du point de montage.")
    total: int = Field(..., ge=0, description="Espace total en bytes.")
    used: int = Field(..., ge=0, description="Espace utilisé en bytes.")
    free: int = Field(..., ge=0, description="Espace libre en bytes.")
    percent_used: float = Field(..., ge=0.0, le=100.0, description="Pourcentage utilisé.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def total_human(self) -> str:
        """Espace total formaté."""
        from nexusdl.core.utils.text import format_size
        return format_size(self.total)

    @property
    def used_human(self) -> str:
        """Espace utilisé formaté."""
        from nexusdl.core.utils.text import format_size
        return format_size(self.used)

    @property
    def free_human(self) -> str:
        """Espace libre formaté."""
        from nexusdl.core.utils.text import format_size
        return format_size(self.free)


# ============================================================================
# VALIDATION DE CHEMINS
# ============================================================================


def validate_path(
    path: Path | str,
    *,
    must_exist: bool = False,
    must_be_file: bool = False,
    must_be_dir: bool = False,
    must_be_writable: bool = False,
    check_traversal: bool = True,
    base_dir: Path | None = None,
) -> Path:
    """Valide un chemin et retourne une instance Path résolue.

    Args:
        path: Chemin à valider.
        must_exist: Si True, le chemin doit exister.
        must_be_file: Si True, doit être un fichier.
        must_be_dir: Si True, doit être un répertoire.
        must_be_writable: Si True, doit être accessible en écriture.
        check_traversal: Si True, vérifie les path traversal attacks.
        base_dir: Répertoire de base pour la vérification de traversal.

    Returns:
        Chemin résolu et validé.

    Raises:
        InvalidPathError: Si le chemin est invalide.
        FileNotFoundError: Si must_exist=True et le chemin n'existe pas.
        PermissionError: Si must_be_writable=True et non accessible.

    Example:
        >>> validate_path("/path/to/file.txt", must_exist=True)
        PosixPath('/path/to/file.txt')
    """
    if isinstance(path, str):
        path = Path(path)

    # Résoudre le chemin
    try:
        resolved = path.expanduser().resolve()
    except Exception as e:
        raise InvalidPathError(path, f"Impossible de résoudre: {e}") from e

    # Vérifier les path traversal
    if check_traversal and base_dir is not None:
        base_resolved = base_dir.expanduser().resolve()
        try:
            resolved.relative_to(base_resolved)
        except ValueError:
            raise InvalidPathError(
                path,
                f"Path traversal détecté: {resolved} n'est pas dans {base_resolved}",
            )

    # Vérifier l'existence
    if must_exist and not resolved.exists():
        raise FileNotFoundError(resolved)

    # Vérifier le type
    if must_be_file and resolved.exists() and not resolved.is_file():
        raise InvalidPathError(resolved, "N'est pas un fichier")

    if must_be_dir and resolved.exists() and not resolved.is_dir():
        raise InvalidPathError(resolved, "N'est pas un répertoire")

    # Vérifier les permissions
    if must_be_writable and resolved.exists():
        if not os.access(resolved, os.W_OK):
            raise PermissionError(resolved, "écriture")

    return resolved


def is_safe_path(path: Path | str, base_dir: Path | str) -> bool:
    """Vérifie si un chemin est safe (pas de path traversal).

    Args:
        path: Chemin à vérifier.
        base_dir: Répertoire de base autorisé.

    Returns:
        True si le chemin est safe.

    Example:
        >>> is_safe_path("/base/sub/file.txt", "/base")
        True
        >>> is_safe_path("/other/file.txt", "/base")
        False
    """
    try:
        path_resolved = Path(path).expanduser().resolve()
        base_resolved = Path(base_dir).expanduser().resolve()
        path_resolved.relative_to(base_resolved)
        return True
    except (ValueError, OSError):
        return False


def sanitize_filename(name: str, *, replacement: str = "_") -> str:
    """Sanitize un nom de fichier.

    Args:
        name: Nom à sanitiser.
        replacement: Caractère de remplacement.

    Returns:
        Nom sanitizé.

    Example:
        >>> sanitize_filename('file:name<test>.txt')
        'file_name_test_.txt'
    """
    if not name:
        return name

    # Remplacer les caractères interdits
    result: list[str] = []
    for char in name:
        if char in INVALID_FILENAME_CHARS:
            result.append(replacement)
        else:
            result.append(char)

    name = "".join(result)

    # Supprimer les espaces en début/fin
    name = name.strip()

    # Éviter les noms réservés (Windows)
    base_name = name.rsplit(".", 1)[0].upper()
    if base_name in RESERVED_FILENAMES:
        name = "_" + name

    # Éviter les noms vides
    if not name or name in (".", ".."):
        name = "_unnamed_"

    return name


# ============================================================================
# OPÉRATIONS SUR FICHIERS — LECTURE
# ============================================================================


def read_text(
    path: Path | str,
    *,
    encoding: str = "utf-8",
    errors: str = "strict",
) -> str:
    """Lit le contenu d'un fichier texte.

    Args:
        path: Chemin du fichier.
        encoding: Encodage du fichier.
        errors: Gestion des erreurs d'encodage.

    Returns:
        Contenu du fichier.

    Raises:
        FileNotFoundError: Si le fichier n'existe pas.

    Example:
        >>> content = read_text(Path("config.yaml"))
    """
    path = validate_path(path, must_exist=True, must_be_file=True)
    return path.read_text(encoding=encoding, errors=errors)


async def read_text_async(
    path: Path | str,
    *,
    encoding: str = "utf-8",
    errors: str = "strict",
) -> str:
    """Lit le contenu d'un fichier texte de manière asynchrone.

    Args:
        path: Chemin du fichier.
        encoding: Encodage du fichier.
        errors: Gestion des erreurs d'encodage.

    Returns:
        Contenu du fichier.
    """
    path = validate_path(path, must_exist=True, must_be_file=True)
    return await asyncio.to_thread(path.read_text, encoding=encoding, errors=errors)


def read_bytes(path: Path | str) -> bytes:
    """Lit le contenu d'un fichier binaire.

    Args:
        path: Chemin du fichier.

    Returns:
        Contenu du fichier en bytes.

    Raises:
        FileNotFoundError: Si le fichier n'existe pas.
    """
    path = validate_path(path, must_exist=True, must_be_file=True)
    return path.read_bytes()


async def read_bytes_async(path: Path | str) -> bytes:
    """Lit le contenu d'un fichier binaire de manière asynchrone.

    Args:
        path: Chemin du fichier.

    Returns:
        Contenu du fichier en bytes.
    """
    path = validate_path(path, must_exist=True, must_be_file=True)
    return await asyncio.to_thread(path.read_bytes)


def read_json(path: Path | str, *, encoding: str = "utf-8") -> Any:
    """Lit un fichier JSON.

    Args:
        path: Chemin du fichier.
        encoding: Encodage du fichier.

    Returns:
        Données JSON parsées.

    Raises:
        FileNotFoundError: Si le fichier n'existe pas.

    Example:
        >>> data = read_json(Path("config.json"))
    """
    content = read_text(path, encoding=encoding)
    return json.loads(content)


async def read_json_async(path: Path | str, *, encoding: str = "utf-8") -> Any:
    """Lit un fichier JSON de manière asynchrone.

    Args:
        path: Chemin du fichier.
        encoding: Encodage du fichier.

    Returns:
        Données JSON parsées.
    """
    content = await read_text_async(path, encoding=encoding)
    return json.loads(content)


def read_yaml(path: Path | str, *, encoding: str = "utf-8") -> Any:
    """Lit un fichier YAML.

    Args:
        path: Chemin du fichier.
        encoding: Encodage du fichier.

    Returns:
        Données YAML parsées.

    Raises:
        FileNotFoundError: Si le fichier n'existe pas.

    Example:
        >>> data = read_yaml(Path("config.yaml"))
    """
    content = read_text(path, encoding=encoding)
    return yaml.safe_load(content)


async def read_yaml_async(path: Path | str, *, encoding: str = "utf-8") -> Any:
    """Lit un fichier YAML de manière asynchrone.

    Args:
        path: Chemin du fichier.
        encoding: Encodage du fichier.

    Returns:
        Données YAML parsées.
    """
    content = await read_text_async(path, encoding=encoding)
    return yaml.safe_load(content)


def read_lines(
    path: Path | str,
    *,
    encoding: str = "utf-8",
    strip: bool = True,
    skip_empty: bool = False,
) -> list[str]:
    """Lit les lignes d'un fichier texte.

    Args:
        path: Chemin du fichier.
        encoding: Encodage du fichier.
        strip: Si True, supprime les espaces en début/fin.
        skip_empty: Si True, ignore les lignes vides.

    Returns:
        Liste des lignes.

    Example:
        >>> lines = read_lines(Path("data.txt"), skip_empty=True)
    """
    content = read_text(path, encoding=encoding)
    lines = content.splitlines()

    if strip:
        lines = [line.strip() for line in lines]

    if skip_empty:
        lines = [line for line in lines if line]

    return lines


# ============================================================================
# OPÉRATIONS SUR FICHIERS — ÉCRITURE
# ============================================================================


def write_text(
    path: Path | str,
    content: str,
    *,
    encoding: str = "utf-8",
    create_parents: bool = True,
    mode: int | None = None,
) -> None:
    """Écrit du texte dans un fichier.

    Args:
        path: Chemin du fichier.
        content: Contenu à écrire.
        encoding: Encodage du fichier.
        create_parents: Si True, crée les répertoires parents.
        mode: Permissions du fichier (Unix).

    Example:
        >>> write_text(Path("output.txt"), "Hello World")
    """
    path = Path(path).expanduser().resolve()

    if create_parents:
        path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(content, encoding=encoding)

    if mode is not None and os.name != "nt":
        os.chmod(path, mode)


async def write_text_async(
    path: Path | str,
    content: str,
    *,
    encoding: str = "utf-8",
    create_parents: bool = True,
    mode: int | None = None,
) -> None:
    """Écrit du texte dans un fichier de manière asynchrone.

    Args:
        path: Chemin du fichier.
        content: Contenu à écrire.
        encoding: Encodage du fichier.
        create_parents: Si True, crée les répertoires parents.
        mode: Permissions du fichier (Unix).
    """
    path = Path(path).expanduser().resolve()

    if create_parents:
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)

    await asyncio.to_thread(path.write_text, content, encoding=encoding)

    if mode is not None and os.name != "nt":
        await asyncio.to_thread(os.chmod, path, mode)


def write_bytes(
    path: Path | str,
    content: bytes,
    *,
    create_parents: bool = True,
    mode: int | None = None,
) -> None:
    """Écrit des données binaires dans un fichier.

    Args:
        path: Chemin du fichier.
        content: Données à écrire.
        create_parents: Si True, crée les répertoires parents.
        mode: Permissions du fichier (Unix).
    """
    path = Path(path).expanduser().resolve()

    if create_parents:
        path.parent.mkdir(parents=True, exist_ok=True)

    path.write_bytes(content)

    if mode is not None and os.name != "nt":
        os.chmod(path, mode)


async def write_bytes_async(
    path: Path | str,
    content: bytes,
    *,
    create_parents: bool = True,
    mode: int | None = None,
) -> None:
    """Écrit des données binaires dans un fichier de manière asynchrone.

    Args:
        path: Chemin du fichier.
        content: Données à écrire.
        create_parents: Si True, crée les répertoires parents.
        mode: Permissions du fichier (Unix).
    """
    path = Path(path).expanduser().resolve()

    if create_parents:
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)

    await asyncio.to_thread(path.write_bytes, content)

    if mode is not None and os.name != "nt":
        await asyncio.to_thread(os.chmod, path, mode)


def write_json(
    path: Path | str,
    data: Any,
    *,
    encoding: str = "utf-8",
    indent: int = 2,
    ensure_ascii: bool = False,
    create_parents: bool = True,
    mode: int | None = None,
) -> None:
    """Écrit des données dans un fichier JSON.

    Args:
        path: Chemin du fichier.
        data: Données à sérialiser.
        encoding: Encodage du fichier.
        indent: Indentation (None pour compact).
        ensure_ascii: Si True, échappe les caractères non-ASCII.
        create_parents: Si True, crée les répertoires parents.
        mode: Permissions du fichier (Unix).

    Example:
        >>> write_json(Path("data.json"), {"key": "value"})
    """
    content = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
    write_text(path, content, encoding=encoding, create_parents=create_parents, mode=mode)


async def write_json_async(
    path: Path | str,
    data: Any,
    *,
    encoding: str = "utf-8",
    indent: int = 2,
    ensure_ascii: bool = False,
    create_parents: bool = True,
    mode: int | None = None,
) -> None:
    """Écrit des données dans un fichier JSON de manière asynchrone.

    Args:
        path: Chemin du fichier.
        data: Données à sérialiser.
        encoding: Encodage du fichier.
        indent: Indentation.
        ensure_ascii: Si True, échappe les caractères non-ASCII.
        create_parents: Si True, crée les répertoires parents.
        mode: Permissions du fichier (Unix).
    """
    content = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
    await write_text_async(
        path, content, encoding=encoding, create_parents=create_parents, mode=mode
    )


def write_yaml(
    path: Path | str,
    data: Any,
    *,
    encoding: str = "utf-8",
    default_flow_style: bool = False,
    allow_unicode: bool = True,
    create_parents: bool = True,
    mode: int | None = None,
) -> None:
    """Écrit des données dans un fichier YAML.

    Args:
        path: Chemin du fichier.
        data: Données à sérialiser.
        encoding: Encodage du fichier.
        default_flow_style: Style YAML (False = block, True = flow).
        allow_unicode: Si True, autorise les caractères Unicode.
        create_parents: Si True, crée les répertoires parents.
        mode: Permissions du fichier (Unix).

    Example:
        >>> write_yaml(Path("config.yaml"), {"key": "value"})
    """
    content = yaml.dump(
        data,
        default_flow_style=default_flow_style,
        allow_unicode=allow_unicode,
    )
    write_text(path, content, encoding=encoding, create_parents=create_parents, mode=mode)


async def write_yaml_async(
    path: Path | str,
    data: Any,
    *,
    encoding: str = "utf-8",
    default_flow_style: bool = False,
    allow_unicode: bool = True,
    create_parents: bool = True,
    mode: int | None = None,
) -> None:
    """Écrit des données dans un fichier YAML de manière asynchrone.

    Args:
        path: Chemin du fichier.
        data: Données à sérialiser.
        encoding: Encodage du fichier.
        default_flow_style: Style YAML.
        allow_unicode: Si True, autorise les caractères Unicode.
        create_parents: Si True, crée les répertoires parents.
        mode: Permissions du fichier (Unix).
    """
    content = yaml.dump(
        data,
        default_flow_style=default_flow_style,
        allow_unicode=allow_unicode,
    )
    await write_text_async(
        path, content, encoding=encoding, create_parents=create_parents, mode=mode
    )


def append_text(
    path: Path | str,
    content: str,
    *,
    encoding: str = "utf-8",
    create_parents: bool = True,
) -> None:
    """Ajoute du texte à la fin d'un fichier.

    Args:
        path: Chemin du fichier.
        content: Contenu à ajouter.
        encoding: Encodage du fichier.
        create_parents: Si True, crée les répertoires parents.
    """
    path = Path(path).expanduser().resolve()

    if create_parents:
        path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding=encoding) as f:
        f.write(content)


async def append_text_async(
    path: Path | str,
    content: str,
    *,
    encoding: str = "utf-8",
    create_parents: bool = True,
) -> None:
    """Ajoute du texte à la fin d'un fichier de manière asynchrone.

    Args:
        path: Chemin du fichier.
        content: Contenu à ajouter.
        encoding: Encodage du fichier.
        create_parents: Si True, crée les répertoires parents.
    """
    path = Path(path).expanduser().resolve()

    if create_parents:
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)

    def _append() -> None:
        with path.open("a", encoding=encoding) as f:
            f.write(content)

    await asyncio.to_thread(_append)


# ============================================================================
# OPÉRATIONS ATOMIQUES
# ============================================================================


def atomic_write(
    path: Path | str,
    content: str | bytes,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
    create_parents: bool = True,
) -> None:
    """Écrit du contenu dans un fichier de manière atomique.

    Écrit d'abord dans un fichier temporaire, puis renomme vers la cible.
    Garantit que le fichier cible est soit complet, soit inexistant.

    Args:
        path: Chemin du fichier cible.
        content: Contenu à écrire.
        encoding: Encodage (pour texte).
        mode: Permissions du fichier (Unix).
        create_parents: Si True, crée les répertoires parents.

    Raises:
        AtomicOperationError: Si l'opération atomique échoue.

    Example:
        >>> atomic_write(Path("config.yaml"), "new content")
    """
    path = Path(path).expanduser().resolve()

    if create_parents:
        path.parent.mkdir(parents=True, exist_ok=True)

    # Créer un fichier temporaire dans le même répertoire
    temp_fd, temp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )

    try:
        # Écrire le contenu
        if isinstance(content, str):
            with os.fdopen(temp_fd, "w", encoding=encoding) as f:
                f.write(content)
        else:
            with os.fdopen(temp_fd, "wb") as f:
                f.write(content)

        # Définir les permissions
        if mode is not None and os.name != "nt":
            os.chmod(temp_path, mode)

        # Renommer de manière atomique
        os.replace(temp_path, path)

    except Exception as e:
        # Nettoyer le fichier temporaire en cas d'erreur
        with contextlib.suppress(Exception):
            os.unlink(temp_path)
        raise AtomicOperationError(path, str(e)) from e


async def atomic_write_async(
    path: Path | str,
    content: str | bytes,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
    create_parents: bool = True,
) -> None:
    """Écrit du contenu dans un fichier de manière atomique (async).

    Args:
        path: Chemin du fichier cible.
        content: Contenu à écrire.
        encoding: Encodage (pour texte).
        mode: Permissions du fichier (Unix).
        create_parents: Si True, crée les répertoires parents.
    """
    await asyncio.to_thread(
        atomic_write,
        path,
        content,
        encoding=encoding,
        mode=mode,
        create_parents=create_parents,
    )


def atomic_move(
    src: Path | str,
    dst: Path | str,
    *,
    create_parents: bool = True,
) -> None:
    """Déplace un fichier de manière atomique.

    Args:
        src: Chemin source.
        dst: Chemin destination.
        create_parents: Si True, crée les répertoires parents.

    Raises:
        FileNotFoundError: Si le fichier source n'existe pas.
        AtomicOperationError: Si l'opération échoue.
    """
    src = Path(src).expanduser().resolve()
    dst = Path(dst).expanduser().resolve()

    if not src.exists():
        raise FileNotFoundError(src)

    if create_parents:
        dst.parent.mkdir(parents=True, exist_ok=True)

    try:
        os.replace(src, dst)
    except Exception as e:
        raise AtomicOperationError(dst, str(e)) from e


async def atomic_move_async(
    src: Path | str,
    dst: Path | str,
    *,
    create_parents: bool = True,
) -> None:
    """Déplace un fichier de manière atomique (async).

    Args:
        src: Chemin source.
        dst: Chemin destination.
        create_parents: Si True, crée les répertoires parents.
    """
    await asyncio.to_thread(atomic_move, src, dst, create_parents=create_parents)


# ============================================================================
# OPÉRATIONS SUR FICHIERS — COPIE, DÉPLACEMENT, SUPPRESSION
# ============================================================================


def copy_file(
    src: Path | str,
    dst: Path | str,
    *,
    overwrite: bool = False,
    preserve_metadata: bool = True,
    create_parents: bool = True,
) -> None:
    """Copie un fichier.

    Args:
        src: Chemin source.
        dst: Chemin destination.
        overwrite: Si True, écrase le fichier destination s'il existe.
        preserve_metadata: Si True, préserve les métadonnées (timestamps, permissions).
        create_parents: Si True, crée les répertoires parents.

    Raises:
        FileNotFoundError: Si le fichier source n'existe pas.
        FileExistsError: Si le fichier destination existe et overwrite=False.

    Example:
        >>> copy_file(Path("source.txt"), Path("dest.txt"))
    """
    src = Path(src).expanduser().resolve()
    dst = Path(dst).expanduser().resolve()

    if not src.exists():
        raise FileNotFoundError(src)

    if dst.exists() and not overwrite:
        raise FileExistsError(dst)

    if create_parents:
        dst.parent.mkdir(parents=True, exist_ok=True)

    if preserve_metadata:
        shutil.copy2(src, dst)
    else:
        shutil.copy(src, dst)


async def copy_file_async(
    src: Path | str,
    dst: Path | str,
    *,
    overwrite: bool = False,
    preserve_metadata: bool = True,
    create_parents: bool = True,
) -> None:
    """Copie un fichier de manière asynchrone.

    Args:
        src: Chemin source.
        dst: Chemin destination.
        overwrite: Si True, écrase le fichier destination.
        preserve_metadata: Si True, préserve les métadonnées.
        create_parents: Si True, crée les répertoires parents.
    """
    await asyncio.to_thread(
        copy_file,
        src,
        dst,
        overwrite=overwrite,
        preserve_metadata=preserve_metadata,
        create_parents=create_parents,
    )


def move_file(
    src: Path | str,
    dst: Path | str,
    *,
    overwrite: bool = False,
    create_parents: bool = True,
) -> None:
    """Déplace un fichier.

    Args:
        src: Chemin source.
        dst: Chemin destination.
        overwrite: Si True, écrase le fichier destination.
        create_parents: Si True, crée les répertoires parents.

    Raises:
        FileNotFoundError: Si le fichier source n'existe pas.
        FileExistsError: Si le fichier destination existe et overwrite=False.
    """
    src = Path(src).expanduser().resolve()
    dst = Path(dst).expanduser().resolve()

    if not src.exists():
        raise FileNotFoundError(src)

    if dst.exists() and not overwrite:
        raise FileExistsError(dst)

    if create_parents:
        dst.parent.mkdir(parents=True, exist_ok=True)

    shutil.move(str(src), str(dst))


async def move_file_async(
    src: Path | str,
    dst: Path | str,
    *,
    overwrite: bool = False,
    create_parents: bool = True,
) -> None:
    """Déplace un fichier de manière asynchrone.

    Args:
        src: Chemin source.
        dst: Chemin destination.
        overwrite: Si True, écrase le fichier destination.
        create_parents: Si True, crée les répertoires parents.
    """
    await asyncio.to_thread(
        move_file,
        src,
        dst,
        overwrite=overwrite,
        create_parents=create_parents,
    )


def delete_file(
    path: Path | str,
    *,
    missing_ok: bool = False,
) -> None:
    """Supprime un fichier.

    Args:
        path: Chemin du fichier.
        missing_ok: Si True, n'erreur pas si le fichier n'existe pas.

    Raises:
        FileNotFoundError: Si le fichier n'existe pas et missing_ok=False.

    Example:
        >>> delete_file(Path("temp.txt"))
    """
    path = Path(path).expanduser().resolve()

    if not path.exists():
        if not missing_ok:
            raise FileNotFoundError(path)
        return

    if path.is_dir():
        raise InvalidPathError(path, "Est un répertoire, utilisez delete_directory()")

    path.unlink()


async def delete_file_async(
    path: Path | str,
    *,
    missing_ok: bool = False,
) -> None:
    """Supprime un fichier de manière asynchrone.

    Args:
        path: Chemin du fichier.
        missing_ok: Si True, n'erreur pas si le fichier n'existe pas.
    """
    await asyncio.to_thread(delete_file, path, missing_ok=missing_ok)


def delete_directory(
    path: Path | str,
    *,
    missing_ok: bool = False,
    recursive: bool = True,
) -> None:
    """Supprime un répertoire.

    Args:
        path: Chemin du répertoire.
        missing_ok: Si True, n'erreur pas si le répertoire n'existe pas.
        recursive: Si True, supprime récursivement le contenu.

    Raises:
        DirectoryNotFoundError: Si le répertoire n'existe pas et missing_ok=False.

    Example:
        >>> delete_directory(Path("temp_dir"), recursive=True)
    """
    path = Path(path).expanduser().resolve()

    if not path.exists():
        if not missing_ok:
            raise DirectoryNotFoundError(path)
        return

    if not path.is_dir():
        raise InvalidPathError(path, "N'est pas un répertoire")

    if recursive:
        shutil.rmtree(path)
    else:
        path.rmdir()


async def delete_directory_async(
    path: Path | str,
    *,
    missing_ok: bool = False,
    recursive: bool = True,
) -> None:
    """Supprime un répertoire de manière asynchrone.

    Args:
        path: Chemin du répertoire.
        missing_ok: Si True, n'erreur pas si le répertoire n'existe pas.
        recursive: Si True, supprime récursivement le contenu.
    """
    await asyncio.to_thread(
        delete_directory,
        path,
        missing_ok=missing_ok,
        recursive=recursive,
    )


# ============================================================================
# OPÉRATIONS SUR RÉPERTOIRES
# ============================================================================


def ensure_dir(
    path: Path | str,
    *,
    mode: int = DEFAULT_DIR_MODE,
    parents: bool = True,
    exist_ok: bool = True,
) -> Path:
    """Crée un répertoire s'il n'existe pas.

    Args:
        path: Chemin du répertoire.
        mode: Permissions du répertoire (Unix).
        parents: Si True, crée les répertoires parents.
        exist_ok: Si True, n'erreur pas si le répertoire existe déjà.

    Returns:
        Chemin du répertoire créé ou existant.

    Example:
        >>> ensure_dir(Path("/path/to/dir"))
        PosixPath('/path/to/dir')
    """
    path = Path(path).expanduser().resolve()
    path.mkdir(parents=parents, exist_ok=exist_ok, mode=mode)
    return path


async def ensure_dir_async(
    path: Path | str,
    *,
    mode: int = DEFAULT_DIR_MODE,
    parents: bool = True,
    exist_ok: bool = True,
) -> Path:
    """Crée un répertoire s'il n'existe pas (async).

    Args:
        path: Chemin du répertoire.
        mode: Permissions du répertoire (Unix).
        parents: Si True, crée les répertoires parents.
        exist_ok: Si True, n'erreur pas si le répertoire existe déjà.

    Returns:
        Chemin du répertoire créé ou existant.
    """
    return await asyncio.to_thread(
        ensure_dir,
        path,
        mode=mode,
        parents=parents,
        exist_ok=exist_ok,
    )


def list_files(
    path: Path | str,
    *,
    pattern: str = "*",
    recursive: bool = False,
    include_dirs: bool = False,
    include_hidden: bool = False,
    sort_by: SortBy = SortBy.NAME,
    reverse: bool = False,
) -> list[Path]:
    """Liste les fichiers d'un répertoire.

    Args:
        path: Chemin du répertoire.
        pattern: Pattern glob (ex: "*.jpg", "**/*.txt").
        recursive: Si True, liste récursivement.
        include_dirs: Si True, inclut les répertoires.
        include_hidden: Si True, inclut les fichiers cachés.
        sort_by: Critère de tri.
        reverse: Si True, tri inverse.

    Returns:
        Liste des chemins des fichiers.

    Example:
        >>> files = list_files(Path("/path"), pattern="*.jpg", recursive=True)
    """
    path = validate_path(path, must_exist=True, must_be_dir=True)

    if recursive:
        items = list(path.rglob(pattern))
    else:
        items = list(path.glob(pattern))

    # Filtrer
    if not include_dirs:
        items = [item for item in items if item.is_file()]

    if not include_hidden:
        items = [item for item in items if not item.name.startswith(".")]

    # Trier
    if sort_by == SortBy.NAME:
        items.sort(key=lambda p: p.name, reverse=reverse)
    elif sort_by == SortBy.SIZE:
        items.sort(key=lambda p: p.stat().st_size, reverse=reverse)
    elif sort_by == SortBy.MODIFIED:
        items.sort(key=lambda p: p.stat().st_mtime, reverse=reverse)
    elif sort_by == SortBy.CREATED:
        items.sort(key=lambda p: p.stat().st_ctime, reverse=reverse)
    elif sort_by == SortBy.EXTENSION:
        items.sort(key=lambda p: p.suffix.lower(), reverse=reverse)

    return items


async def list_files_async(
    path: Path | str,
    *,
    pattern: str = "*",
    recursive: bool = False,
    include_dirs: bool = False,
    include_hidden: bool = False,
    sort_by: SortBy = SortBy.NAME,
    reverse: bool = False,
) -> list[Path]:
    """Liste les fichiers d'un répertoire de manière asynchrone.

    Args:
        path: Chemin du répertoire.
        pattern: Pattern glob.
        recursive: Si True, liste récursivement.
        include_dirs: Si True, inclut les répertoires.
        include_hidden: Si True, inclut les fichiers cachés.
        sort_by: Critère de tri.
        reverse: Si True, tri inverse.

    Returns:
        Liste des chemins des fichiers.
    """
    return await asyncio.to_thread(
        list_files,
        path,
        pattern=pattern,
        recursive=recursive,
        include_dirs=include_dirs,
        include_hidden=include_hidden,
        sort_by=sort_by,
        reverse=reverse,
    )


def list_directories(
    path: Path | str,
    *,
    recursive: bool = False,
    include_hidden: bool = False,
    sort_by: SortBy = SortBy.NAME,
    reverse: bool = False,
) -> list[Path]:
    """Liste les sous-répertoires d'un répertoire.

    Args:
        path: Chemin du répertoire.
        recursive: Si True, liste récursivement.
        include_hidden: Si True, inclut les répertoires cachés.
        sort_by: Critère de tri.
        reverse: Si True, tri inverse.

    Returns:
        Liste des chemins des répertoires.
    """
    path = validate_path(path, must_exist=True, must_be_dir=True)

    if recursive:
        items = [item for item in path.rglob("*") if item.is_dir()]
    else:
        items = [item for item in path.iterdir() if item.is_dir()]

    if not include_hidden:
        items = [item for item in items if not item.name.startswith(".")]

    # Trier
    if sort_by == SortBy.NAME:
        items.sort(key=lambda p: p.name, reverse=reverse)
    elif sort_by == SortBy.MODIFIED:
        items.sort(key=lambda p: p.stat().st_mtime, reverse=reverse)
    elif sort_by == SortBy.CREATED:
        items.sort(key=lambda p: p.stat().st_ctime, reverse=reverse)

    return items


def get_directory_tree(
    path: Path | str,
    *,
    max_depth: int | None = None,
    include_hidden: bool = False,
    pattern: str = "*",
) -> dict[str, Any]:
    """Construit une arborescence de répertoire.

    Args:
        path: Chemin du répertoire racine.
        max_depth: Profondeur maximale (None = illimité).
        include_hidden: Si True, inclut les fichiers cachés.
        pattern: Pattern glob pour filtrer.

    Returns:
        Dictionnaire représentant l'arborescence.

    Example:
        >>> tree = get_directory_tree(Path("/path"), max_depth=2)
        >>> print(tree)
        {'name': 'path', 'type': 'directory', 'children': [...]}
    """
    path = validate_path(path, must_exist=True, must_be_dir=True)

    def _build_tree(current_path: Path, depth: int) -> dict[str, Any]:
        if max_depth is not None and depth > max_depth:
            return {}

        node: dict[str, Any] = {
            "name": current_path.name,
            "path": str(current_path),
            "type": "directory" if current_path.is_dir() else "file",
        }

        if current_path.is_dir():
            children = []
            for item in current_path.iterdir():
                if not include_hidden and item.name.startswith("."):
                    continue
                if not item.match(pattern):
                    continue
                child = _build_tree(item, depth + 1)
                if child:
                    children.append(child)
            node["children"] = sorted(children, key=lambda x: x["name"])

        return node

    return _build_tree(path, 0)


# ============================================================================
# INFORMATIONS SUR FICHIERS ET RÉPERTOIRES
# ============================================================================


def get_file_stats(path: Path | str) -> FileStats:
    """Récupère les statistiques d'un fichier.

    Args:
        path: Chemin du fichier.

    Returns:
        Instance de FileStats.

    Raises:
        FileNotFoundError: Si le fichier n'existe pas.

    Example:
        >>> stats = get_file_stats(Path("image.jpg"))
        >>> print(f"Taille: {stats.size_human}")
    """
    path = validate_path(path, must_exist=True)

    stat_info = path.stat()

    return FileStats(
        path=path,
        name=path.name,
        extension=path.suffix,
        size=stat_info.st_size,
        created_at=datetime.fromtimestamp(stat_info.st_ctime, tz=UTC),
        modified_at=datetime.fromtimestamp(stat_info.st_mtime, tz=UTC),
        accessed_at=datetime.fromtimestamp(stat_info.st_atime, tz=UTC),
        is_file=path.is_file(),
        is_dir=path.is_dir(),
        is_symlink=path.is_symlink(),
        permissions=stat.S_IMODE(stat_info.st_mode),
        owner_uid=stat_info.st_uid if os.name != "nt" else None,
        group_gid=stat_info.st_gid if os.name != "nt" else None,
    )


async def get_file_stats_async(path: Path | str) -> FileStats:
    """Récupère les statistiques d'un fichier de manière asynchrone.

    Args:
        path: Chemin du fichier.

    Returns:
        Instance de FileStats.
    """
    return await asyncio.to_thread(get_file_stats, path)


def get_directory_stats(
    path: Path | str,
    *,
    recursive: bool = True,
) -> DirectoryStats:
    """Récupère les statistiques d'un répertoire.

    Args:
        path: Chemin du répertoire.
        recursive: Si True, inclut les sous-répertoires.

    Returns:
        Instance de DirectoryStats.

    Raises:
        DirectoryNotFoundError: Si le répertoire n'existe pas.

    Example:
        >>> stats = get_directory_stats(Path("/path/to/dir"))
        >>> print(f"Fichiers: {stats.file_count}, Taille: {stats.total_size_human}")
    """
    path = validate_path(path, must_exist=True, must_be_dir=True)

    stat_info = path.stat()

    file_count = 0
    dir_count = 0
    total_size = 0

    if recursive:
        for item in path.rglob("*"):
            if item.is_file():
                file_count += 1
                total_size += item.stat().st_size
            elif item.is_dir():
                dir_count += 1
    else:
        for item in path.iterdir():
            if item.is_file():
                file_count += 1
                total_size += item.stat().st_size
            elif item.is_dir():
                dir_count += 1

    return DirectoryStats(
        path=path,
        name=path.name,
        file_count=file_count,
        dir_count=dir_count,
        total_size=total_size,
        created_at=datetime.fromtimestamp(stat_info.st_ctime, tz=UTC),
        modified_at=datetime.fromtimestamp(stat_info.st_mtime, tz=UTC),
    )


async def get_directory_stats_async(
    path: Path | str,
    *,
    recursive: bool = True,
) -> DirectoryStats:
    """Récupère les statistiques d'un répertoire de manière asynchrone.

    Args:
        path: Chemin du répertoire.
        recursive: Si True, inclut les sous-répertoires.

    Returns:
        Instance de DirectoryStats.
    """
    return await asyncio.to_thread(get_directory_stats, path, recursive=recursive)


def get_file_size(path: Path | str) -> int:
    """Récupère la taille d'un fichier en bytes.

    Args:
        path: Chemin du fichier.

    Returns:
        Taille en bytes.

    Raises:
        FileNotFoundError: Si le fichier n'existe pas.
    """
    path = validate_path(path, must_exist=True, must_be_file=True)
    return path.stat().st_size


async def get_file_size_async(path: Path | str) -> int:
    """Récupère la taille d'un fichier en bytes (async).

    Args:
        path: Chemin du fichier.

    Returns:
        Taille en bytes.
    """
    return await asyncio.to_thread(get_file_size, path)


# ============================================================================
# GESTION DE L'ESPACE DISQUE
# ============================================================================


def get_disk_usage(path: Path | str) -> DiskUsage:
    """Récupère l'utilisation de l'espace disque.

    Args:
        path: Chemin du point de montage.

    Returns:
        Instance de DiskUsage.

    Example:
        >>> usage = get_disk_usage(Path("/"))
        >>> print(f"Libre: {usage.free_human} ({100 - usage.percent_used:.1f}%)")
    """
    path = Path(path).expanduser().resolve()

    # Remonter jusqu'à trouver un chemin existant
    while not path.exists():
        path = path.parent

    usage = shutil.disk_usage(path)

    return DiskUsage(
        path=path,
        total=usage.total,
        used=usage.used,
        free=usage.free,
        percent_used=(usage.used / usage.total) * 100 if usage.total > 0 else 0.0,
    )


async def get_disk_usage_async(path: Path | str) -> DiskUsage:
    """Récupère l'utilisation de l'espace disque (async).

    Args:
        path: Chemin du point de montage.

    Returns:
        Instance de DiskUsage.
    """
    return await asyncio.to_thread(get_disk_usage, path)


def check_disk_space(
    path: Path | str,
    required_bytes: int,
    *,
    safety_margin: float = 0.1,
) -> bool:
    """Vérifie s'il y a assez d'espace disque.

    Args:
        path: Chemin du point de montage.
        required_bytes: Espace requis en bytes.
        safety_margin: Marge de sécurité (0.1 = 10%).

    Returns:
        True si l'espace est suffisant.

    Example:
        >>> if check_disk_space(Path("/"), 1024 * 1024 * 100):  # 100 MB
        ...     print("Assez d'espace")
    """
    usage = get_disk_usage(path)
    required_with_margin = required_bytes * (1 + safety_margin)
    return usage.free >= required_with_margin


def ensure_disk_space(
    path: Path | str,
    required_bytes: int,
    *,
    safety_margin: float = 0.1,
) -> None:
    """Vérifie s'il y a assez d'espace disque et lève une exception sinon.

    Args:
        path: Chemin du point de montage.
        required_bytes: Espace requis en bytes.
        safety_margin: Marge de sécurité.

    Raises:
        DiskFullError: Si l'espace est insuffisant.
    """
    usage = get_disk_usage(path)
    required_with_margin = required_bytes * (1 + safety_margin)

    if usage.free < required_with_margin:
        raise DiskFullError(path, int(required_with_margin), usage.free)


# ============================================================================
# FICHIERS TEMPORAIRES
# ============================================================================


def create_temp_file(
    *,
    suffix: str = "",
    prefix: str = "nexusdl_",
    dir: Path | str | None = None,
    delete_on_close: bool = True,
) -> Path:
    """Crée un fichier temporaire.

    Args:
        suffix: Suffixe du fichier (ex: ".txt").
        prefix: Préfixe du fichier.
        dir: Répertoire de création (défaut: temp système).
        delete_on_close: Si True, supprime le fichier à la fermeture.

    Returns:
        Chemin du fichier temporaire.

    Example:
        >>> temp_path = create_temp_file(suffix=".txt")
        >>> temp_path.write_text("Hello")
    """
    if dir is not None:
        dir = Path(dir).expanduser().resolve()
        dir.mkdir(parents=True, exist_ok=True)

    fd, path = tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=dir)
    os.close(fd)

    return Path(path)


def create_temp_dir(
    *,
    suffix: str = "",
    prefix: str = "nexusdl_",
    dir: Path | str | None = None,
) -> Path:
    """Crée un répertoire temporaire.

    Args:
        suffix: Suffixe du répertoire.
        prefix: Préfixe du répertoire.
        dir: Répertoire parent (défaut: temp système).

    Returns:
        Chemin du répertoire temporaire.

    Example:
        >>> temp_dir = create_temp_dir()
        >>> print(temp_dir)
    """
    if dir is not None:
        dir = Path(dir).expanduser().resolve()
        dir.mkdir(parents=True, exist_ok=True)

    path = tempfile.mkdtemp(suffix=suffix, prefix=prefix, dir=dir)
    return Path(path)


@contextlib.contextmanager
def temp_file(
    *,
    suffix: str = "",
    prefix: str = "nexusdl_",
    dir: Path | str | None = None,
) -> Iterator[Path]:
    """Context manager pour un fichier temporaire auto-supprimé.

    Args:
        suffix: Suffixe du fichier.
        prefix: Préfixe du fichier.
        dir: Répertoire de création.

    Yields:
        Chemin du fichier temporaire.

    Example:
        >>> with temp_file(suffix=".txt") as temp_path:
        ...     temp_path.write_text("Hello")
        ...     # Le fichier est automatiquement supprimé à la fin
    """
    path = create_temp_file(suffix=suffix, prefix=prefix, dir=dir)
    try:
        yield path
    finally:
        with contextlib.suppress(Exception):
            path.unlink()


@contextlib.contextmanager
def temp_dir(
    *,
    suffix: str = "",
    prefix: str = "nexusdl_",
    dir: Path | str | None = None,
) -> Iterator[Path]:
    """Context manager pour un répertoire temporaire auto-supprimé.

    Args:
        suffix: Suffixe du répertoire.
        prefix: Préfixe du répertoire.
        dir: Répertoire parent.

    Yields:
        Chemin du répertoire temporaire.

    Example:
        >>> with temp_dir() as temp_path:
        ...     # Travailler dans le répertoire
        ...     pass
        ... # Le répertoire est automatiquement supprimé
    """
    path = create_temp_dir(suffix=suffix, prefix=prefix, dir=dir)
    try:
        yield path
    finally:
        with contextlib.suppress(Exception):
            shutil.rmtree(path)


# ============================================================================
# PERMISSIONS (UNIX)
# ============================================================================


def set_permissions(
    path: Path | str,
    mode: int,
    *,
    recursive: bool = False,
) -> None:
    """Définit les permissions d'un fichier ou répertoire (Unix).

    Args:
        path: Chemin du fichier/répertoire.
        mode: Permissions (ex: 0o644, 0o755).
        recursive: Si True, applique récursivement aux répertoires.

    Raises:
        PermissionError: Si les permissions ne peuvent être modifiées.

    Example:
        >>> set_permissions(Path("script.sh"), 0o755)
    """
    if os.name == "nt":
        return  # No-op sur Windows

    path = validate_path(path, must_exist=True)

    if recursive and path.is_dir():
        for item in path.rglob("*"):
            os.chmod(item, mode)
    else:
        os.chmod(path, mode)


async def set_permissions_async(
    path: Path | str,
    mode: int,
    *,
    recursive: bool = False,
) -> None:
    """Définit les permissions d'un fichier ou répertoire (Unix, async).

    Args:
        path: Chemin du fichier/répertoire.
        mode: Permissions.
        recursive: Si True, applique récursivement.
    """
    await asyncio.to_thread(set_permissions, path, mode, recursive=recursive)


def make_executable(path: Path | str) -> None:
    """Rend un fichier exécutable (Unix).

    Args:
        path: Chemin du fichier.

    Example:
        >>> make_executable(Path("script.sh"))
    """
    if os.name == "nt":
        return

    path = validate_path(path, must_exist=True, must_be_file=True)
    current_mode = path.stat().st_mode
    new_mode = current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    os.chmod(path, new_mode)


def make_readonly(path: Path | str, *, recursive: bool = False) -> None:
    """Rend un fichier/répertoire en lecture seule.

    Args:
        path: Chemin du fichier/répertoire.
        recursive: Si True, applique récursivement.
    """
    if os.name == "nt":
        # Sur Windows, utiliser l'attribut readonly
        path = validate_path(path, must_exist=True)
        if recursive and path.is_dir():
            for item in path.rglob("*"):
                item.chmod(stat.S_IREAD)
        else:
            path.chmod(stat.S_IREAD)
        return

    set_permissions(path, 0o444 if path.is_file() else 0o555, recursive=recursive)


# ============================================================================
# HELPERS DIVERS
# ============================================================================


def file_exists(path: Path | str) -> bool:
    """Vérifie si un fichier existe.

    Args:
        path: Chemin du fichier.

    Returns:
        True si le fichier existe.
    """
    try:
        path = Path(path).expanduser().resolve()
        return path.exists() and path.is_file()
    except Exception:
        return False


def dir_exists(path: Path | str) -> bool:
    """Vérifie si un répertoire existe.

    Args:
        path: Chemin du répertoire.

    Returns:
        True si le répertoire existe.
    """
    try:
        path = Path(path).expanduser().resolve()
        return path.exists() and path.is_dir()
    except Exception:
        return False


def is_empty_dir(path: Path | str) -> bool:
    """Vérifie si un répertoire est vide.

    Args:
        path: Chemin du répertoire.

    Returns:
        True si le répertoire est vide.
    """
    path = validate_path(path, must_exist=True, must_be_dir=True)
    return not any(path.iterdir())


def get_unique_filename(path: Path | str) -> Path:
    """Génère un nom de fichier unique en ajoutant un suffixe numérique.

    Args:
        path: Chemin du fichier.

    Returns:
        Chemin avec nom unique.

    Example:
        >>> get_unique_filename(Path("file.txt"))  # Si file.txt existe
        PosixPath('file_1.txt')
    """
    path = Path(path).expanduser().resolve()

    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent

    counter = 1
    while True:
        new_path = parent / f"{stem}_{counter}{suffix}"
        if not new_path.exists():
            return new_path
        counter += 1


def copy_directory(
    src: Path | str,
    dst: Path | str,
    *,
    overwrite: bool = False,
    ignore: Iterable[str] | None = None,
) -> None:
    """Copie un répertoire récursivement.

    Args:
        src: Chemin source.
        dst: Chemin destination.
        overwrite: Si True, écrase les fichiers existants.
        ignore: Patterns à ignorer (ex: ["*.pyc", "__pycache__"]).

    Raises:
        DirectoryNotFoundError: Si le répertoire source n'existe pas.
        DirectoryExistsError: Si le répertoire destination existe et overwrite=False.
    """
    src = Path(src).expanduser().resolve()
    dst = Path(dst).expanduser().resolve()

    if not src.exists() or not src.is_dir():
        raise DirectoryNotFoundError(src)

    if dst.exists() and not overwrite:
        raise DirectoryExistsError(dst)

    ignore_func = None
    if ignore:
        ignore_patterns = set(ignore)
        ignore_func = shutil.ignore_patterns(*ignore_patterns)

    shutil.copytree(src, dst, dirs_exist_ok=overwrite, ignore=ignore_func)


async def copy_directory_async(
    src: Path | str,
    dst: Path | str,
    *,
    overwrite: bool = False,
    ignore: Iterable[str] | None = None,
) -> None:
    """Copie un répertoire récursivement (async).

    Args:
        src: Chemin source.
        dst: Chemin destination.
        overwrite: Si True, écrase les fichiers existants.
        ignore: Patterns à ignorer.
    """
    await asyncio.to_thread(
        copy_directory,
        src,
        dst,
        overwrite=overwrite,
        ignore=ignore,
    )


def move_directory(
    src: Path | str,
    dst: Path | str,
    *,
    overwrite: bool = False,
) -> None:
    """Déplace un répertoire.

    Args:
        src: Chemin source.
        dst: Chemin destination.
        overwrite: Si True, écrase le répertoire destination.

    Raises:
        DirectoryNotFoundError: Si le répertoire source n'existe pas.
        DirectoryExistsError: Si le répertoire destination existe et overwrite=False.
    """
    src = Path(src).expanduser().resolve()
    dst = Path(dst).expanduser().resolve()

    if not src.exists() or not src.is_dir():
        raise DirectoryNotFoundError(src)

    if dst.exists() and not overwrite:
        raise DirectoryExistsError(dst)

    shutil.move(str(src), str(dst))


async def move_directory_async(
    src: Path | str,
    dst: Path | str,
    *,
    overwrite: bool = False,
) -> None:
    """Déplace un répertoire (async).

    Args:
        src: Chemin source.
        dst: Chemin destination.
        overwrite: Si True, écrase le répertoire destination.
    """
    await asyncio.to_thread(move_directory, src, dst, overwrite=overwrite)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_BUFFER_SIZE",
    "ASYNC_BUFFER_SIZE",
    "DEFAULT_FILE_MODE",
    "DEFAULT_DIR_MODE",
    "SECURE_FILE_MODE",
    "SECURE_DIR_MODE",
    "INVALID_FILENAME_CHARS",
    "RESERVED_FILENAMES",
    "TEXT_EXTENSIONS",
    "BINARY_EXTENSIONS",
    # Exceptions
    "FilesystemError",
    "FileNotFoundError",
    "DirectoryNotFoundError",
    "FileExistsError",
    "DirectoryExistsError",
    "PermissionError",
    "InvalidPathError",
    "DiskFullError",
    "AtomicOperationError",
    # Enums
    "FileType",
    "SortBy",
    # Modèles
    "FileStats",
    "DirectoryStats",
    "DiskUsage",
    # Validation
    "validate_path",
    "is_safe_path",
    "sanitize_filename",
    # Lecture
    "read_text",
    "read_text_async",
    "read_bytes",
    "read_bytes_async",
    "read_json",
    "read_json_async",
    "read_yaml",
    "read_yaml_async",
    "read_lines",
    # Écriture
    "write_text",
    "write_text_async",
    "write_bytes",
    "write_bytes_async",
    "write_json",
    "write_json_async",
    "write_yaml",
    "write_yaml_async",
    "append_text",
    "append_text_async",
    # Opérations atomiques
    "atomic_write",
    "atomic_write_async",
    "atomic_move",
    "atomic_move_async",
    # Copie, déplacement, suppression
    "copy_file",
    "copy_file_async",
    "move_file",
    "move_file_async",
    "delete_file",
    "delete_file_async",
    "delete_directory",
    "delete_directory_async",
    "copy_directory",
    "copy_directory_async",
    "move_directory",
    "move_directory_async",
    # Répertoires
    "ensure_dir",
    "ensure_dir_async",
    "list_files",
    "list_files_async",
    "list_directories",
    "get_directory_tree",
    # Informations
    "get_file_stats",
    "get_file_stats_async",
    "get_directory_stats",
    "get_directory_stats_async",
    "get_file_size",
    "get_file_size_async",
    # Espace disque
    "get_disk_usage",
    "get_disk_usage_async",
    "check_disk_space",
    "ensure_disk_space",
    # Fichiers temporaires
    "create_temp_file",
    "create_temp_dir",
    "temp_file",
    "temp_dir",
    # Permissions
    "set_permissions",
    "set_permissions_async",
    "make_executable",
    "make_readonly",
    # Helpers
    "file_exists",
    "dir_exists",
    "is_empty_dir",
    "get_unique_filename",
]
