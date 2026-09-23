"""Utilitaires pour le hachage cryptographique dans NexusDL.

Ce module fournit un ensemble complet de fonctions pour le hachage de données,
fichiers, et répertoires. Il est utilisé dans tout le projet pour :

    - Calcul de checksums pour la déduplication d'images
    - Vérification d'intégrité des fichiers téléchargés
    - Génération d'identifiants uniques basés sur le contenu
    - Hachage incrémental pour les gros fichiers
    - Support multi-algorithmes (MD5, SHA1, SHA256, SHA512, SHA3, BLAKE2)

**Architecture** :
    - Fonctions pures (pas d'état global)
    - Utilisation de `hashlib` de la stdlib
    - Support async pour les opérations I/O
    - Classe IncrementalHasher pour le streaming
    - Thread-safe et async-compatible

**Exemples d'utilisation** :
    >>> from nexusdl.core.utils.hash import (
    ...     hash_string, hash_file, verify_file, HashAlgorithm,
    ... )
    >>>
    >>> # Hachage de string
    >>> hash_string("Hello World")
    'a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e'
    >>>
    >>> # Hachage de fichier
    >>> hash_file(Path("image.jpg"), algorithm=HashAlgorithm.SHA256)
    'b5bb9d8014a0f9b1d61e21e796d78dccdf1352f23cd32812f4850b878ae4944c'
    >>>
    >>> # Vérification d'intégrité
    >>> verify_file(Path("image.jpg"), expected_hash="b5bb9d...")
    True
    >>>
    >>> # Hachage incrémental
    >>> hasher = IncrementalHasher()
    >>> hasher.update(b"chunk1")
    >>> hasher.update(b"chunk2")
    >>> hasher.hexdigest()
    '...'

Intégration :
    - core/downloader/deduplication.py : utilise hash_file() pour la déduplication
    - core/image/converter.py : utilise hash_bytes() pour les checksums
    - core/library/scanner.py : utilise hash_directory() pour le scan
    - core/packaging/* : utilise hash_file() pour la vérification
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, Final, Iterable

from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# CONSTANTES
# ============================================================================


# Taille de buffer pour la lecture de fichiers (64 KB)
DEFAULT_BUFFER_SIZE: Final[int] = 65536

# Taille de buffer pour le hachage async (1 MB)
ASYNC_BUFFER_SIZE: Final[int] = 1048576

# Algorithmes de hachage supportés
SUPPORTED_ALGORITHMS: Final[frozenset[str]] = frozenset({
    "md5",
    "sha1",
    "sha224",
    "sha256",
    "sha384",
    "sha512",
    "sha3_256",
    "sha3_512",
    "blake2b",
    "blake2s",
})

# Tailles de hash en bytes par algorithme
HASH_SIZES: Final[dict[str, int]] = {
    "md5": 16,
    "sha1": 20,
    "sha224": 28,
    "sha256": 32,
    "sha384": 48,
    "sha512": 64,
    "sha3_256": 32,
    "sha3_512": 64,
    "blake2b": 64,
    "blake2s": 32,
}

# Longueurs de hash en caractères hexadécimaux
HASH_LENGTHS: Final[dict[str, int]] = {
    algo: size * 2 for algo, size in HASH_SIZES.items()
}


# ============================================================================
# EXCEPTIONS
# ============================================================================


class HashError(NexusDLError):
    """Exception de base pour les erreurs de hachage."""


class InvalidAlgorithmError(HashError):
    """Exception levée lorsqu'un algorithme de hachage est invalide.

    Attributes:
        algorithm: Nom de l'algorithme invalide.
        supported: Liste des algorithmes supportés.
    """

    def __init__(self, algorithm: str, supported: Iterable[str] | None = None) -> None:
        supported_list = list(supported or SUPPORTED_ALGORITHMS)
        msg = f"Algorithme de hachage invalide: {algorithm!r}"
        msg += f". Algorithmes supportés: {', '.join(sorted(supported_list))}"
        super().__init__(msg)
        self.algorithm = algorithm
        self.supported = supported_list


class FileNotFoundError(HashError):
    """Exception levée lorsqu'un fichier est introuvable.

    Attributes:
        path: Chemin du fichier introuvable.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(f"Fichier introuvable: {path}")
        self.path = path


class HashMismatchError(HashError):
    """Exception levée lorsqu'un hash ne correspond pas à l'attendu.

    Attributes:
        expected: Hash attendu.
        actual: Hash calculé.
        path: Chemin du fichier (si applicable).
    """

    def __init__(
        self,
        expected: str,
        actual: str,
        path: Path | None = None,
    ) -> None:
        msg = f"Hash mismatch: attendu {expected!r}, obtenu {actual!r}"
        if path:
            msg += f" pour {path}"
        super().__init__(msg)
        self.expected = expected
        self.actual = actual
        self.path = path


class InvalidHashError(HashError):
    """Exception levée lorsqu'un hash est invalide.

    Attributes:
        hash_value: Hash invalide.
        algorithm: Algorithme attendu.
    """

    def __init__(self, hash_value: str, algorithm: str) -> None:
        expected_length = HASH_LENGTHS.get(algorithm, 0)
        msg = f"Hash invalide: {hash_value!r}"
        if expected_length:
            msg += f" (longueur attendue: {expected_length} caractères hex)"
        super().__init__(msg)
        self.hash_value = hash_value
        self.algorithm = algorithm


# ============================================================================
# ENUMS
# ============================================================================


class HashAlgorithm(str, Enum):
    """Algorithmes de hachage supportés.

    Attributes:
        MD5: MD5 (128 bits, rapide mais peu sécurisé).
        SHA1: SHA-1 (160 bits, déprécié pour la sécurité).
        SHA224: SHA-224 (224 bits).
        SHA256: SHA-256 (256 bits, recommandé).
        SHA384: SHA-384 (384 bits).
        SHA512: SHA-512 (512 bits, très sécurisé).
        SHA3_256: SHA-3 256 bits.
        SHA3_512: SHA-3 512 bits.
        BLAKE2B: BLAKE2b (optimisé pour 64-bit).
        BLAKE2S: BLAKE2s (optimisé pour 8-32 bit).
    """

    MD5 = "md5"
    SHA1 = "sha1"
    SHA224 = "sha224"
    SHA256 = "sha256"
    SHA384 = "sha384"
    SHA512 = "sha512"
    SHA3_256 = "sha3_256"
    SHA3_512 = "sha3_512"
    BLAKE2B = "blake2b"
    BLAKE2S = "blake2s"

    @property
    def hash_size(self) -> int:
        """Taille du hash en bytes."""
        return HASH_SIZES[self.value]

    @property
    def hex_length(self) -> int:
        """Longueur du hash en caractères hexadécimaux."""
        return HASH_LENGTHS[self.value]

    @property
    def is_secure(self) -> bool:
        """Indique si l'algorithme est considéré comme sécurisé."""
        return self not in (HashAlgorithm.MD5, HashAlgorithm.SHA1)

    @property
    def is_fast(self) -> bool:
        """Indique si l'algorithme est rapide (pour la déduplication)."""
        return self in (HashAlgorithm.MD5, HashAlgorithm.SHA1, HashAlgorithm.BLAKE2S)

    def create_hasher(self) -> hashlib._Hash:
        """Crée une instance de l'algorithme de hachage.

        Returns:
            Instance de hashlib hash object.

        Raises:
            InvalidAlgorithmError: Si l'algorithme n'est pas supporté.
        """
        try:
            return hashlib.new(self.value)
        except ValueError as e:
            raise InvalidAlgorithmError(self.value) from e


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class HashResult(BaseModel):
    """Résultat d'une opération de hachage.

    Attributes:
        hash: Hash en hexadécimal.
        algorithm: Algorithme utilisé.
        size: Taille des données hachées en bytes.
        path: Chemin du fichier (si applicable).
        duration_ms: Durée du hachage en millisecondes.
    """

    hash: str = Field(..., description="Hash en hexadécimal.")
    algorithm: str = Field(..., description="Algorithme utilisé.")
    size: int = Field(..., ge=0, description="Taille en bytes.")
    path: Path | None = Field(default=None, description="Chemin du fichier.")
    duration_ms: float = Field(..., ge=0.0, description="Durée en ms.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def hash_bytes(self) -> bytes:
        """Hash en bytes."""
        return bytes.fromhex(self.hash)

    @property
    def size_human(self) -> str:
        """Taille formatée pour l'affichage."""
        from nexusdl.core.utils.text import format_size
        return format_size(self.size)

    @property
    def speed_mb_per_sec(self) -> float:
        """Vitesse de hachage en MB/s."""
        if self.duration_ms == 0:
            return 0.0
        return (self.size / (1024 * 1024)) / (self.duration_ms / 1000)


class DirectoryHashResult(BaseModel):
    """Résultat du hachage d'un répertoire.

    Attributes:
        root: Chemin du répertoire racine.
        hash: Hash combiné de tous les fichiers.
        algorithm: Algorithme utilisé.
        file_count: Nombre de fichiers hachés.
        total_size: Taille totale en bytes.
        file_hashes: Dictionnaire {chemin_rel: hash}.
        duration_ms: Durée totale en millisecondes.
    """

    root: Path = Field(..., description="Chemin du répertoire racine.")
    hash: str = Field(..., description="Hash combiné.")
    algorithm: str = Field(..., description="Algorithme utilisé.")
    file_count: int = Field(..., ge=0, description="Nombre de fichiers.")
    total_size: int = Field(..., ge=0, description="Taille totale en bytes.")
    file_hashes: dict[str, str] = Field(
        default_factory=dict,
        description="Hashes individuels.",
    )
    duration_ms: float = Field(..., ge=0.0, description="Durée en ms.")

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# CLASSE — IncrementalHasher
# ============================================================================


class IncrementalHasher:
    """Hacheur incrémental pour le streaming de données.

    Permet de hacher des données par chunks sans tout charger en mémoire.

    Example:
        >>> hasher = IncrementalHasher(algorithm=HashAlgorithm.SHA256)
        >>> with open("large_file.bin", "rb") as f:
        ...     while chunk := f.read(65536):
        ...         hasher.update(chunk)
        >>> print(hasher.hexdigest())
    """

    def __init__(
        self,
        algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    ) -> None:
        """Initialise le hacheur incrémental.

        Args:
            algorithm: Algorithme de hachage à utiliser.
        """
        self._algorithm = algorithm
        self._hasher = algorithm.create_hasher()
        self._size = 0

    @property
    def algorithm(self) -> HashAlgorithm:
        """Algorithme utilisé."""
        return self._algorithm

    @property
    def size(self) -> int:
        """Taille totale des données hachées en bytes."""
        return self._size

    def update(self, data: bytes) -> None:
        """Ajoute des données au hacheur.

        Args:
            data: Données à ajouter.
        """
        self._hasher.update(data)
        self._size += len(data)

    def digest(self) -> bytes:
        """Retourne le hash en bytes.

        Returns:
            Hash en bytes.
        """
        return self._hasher.digest()

    def hexdigest(self) -> str:
        """Retourne le hash en hexadécimal.

        Returns:
            Hash en hexadécimal.
        """
        return self._hasher.hexdigest()

    def copy(self) -> IncrementalHasher:
        """Crée une copie du hacheur.

        Returns:
            Nouvelle instance avec le même état.
        """
        new_hasher = IncrementalHasher(self._algorithm)
        new_hasher._hasher = self._hasher.copy()
        new_hasher._size = self._size
        return new_hasher

    def reset(self) -> None:
        """Réinitialise le hacheur."""
        self._hasher = self._algorithm.create_hasher()
        self._size = 0


# ============================================================================
# HACHAGE DE STRINGS ET BYTES
# ============================================================================


def hash_string(
    text: str,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    encoding: str = "utf-8",
) -> str:
    """Hache une chaîne de caractères.

    Args:
        text: Texte à hacher.
        algorithm: Algorithme de hachage.
        encoding: Encodage du texte.

    Returns:
        Hash en hexadécimal.

    Example:
        >>> hash_string("Hello World")
        'a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e'
    """
    data = text.encode(encoding)
    return hash_bytes(data, algorithm=algorithm)


def hash_bytes(
    data: bytes,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
) -> str:
    """Hache des données binaires.

    Args:
        data: Données à hacher.
        algorithm: Algorithme de hachage.

    Returns:
        Hash en hexadécimal.

    Example:
        >>> hash_bytes(b"Hello World")
        'a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e'
    """
    hasher = algorithm.create_hasher()
    hasher.update(data)
    return hasher.hexdigest()


def hash_with_salt(
    data: bytes | str,
    salt: bytes | str,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
) -> str:
    """Hache des données avec un salt.

    Args:
        data: Données à hacher.
        salt: Salt à ajouter.
        algorithm: Algorithme de hachage.

    Returns:
        Hash en hexadécimal.

    Example:
        >>> hash_with_salt("password", "random_salt")
        '...'
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    if isinstance(salt, str):
        salt = salt.encode("utf-8")

    return hash_bytes(salt + data, algorithm=algorithm)


# ============================================================================
# HACHAGE DE FICHIERS
# ============================================================================


def hash_file(
    path: Path | str,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    buffer_size: int = DEFAULT_BUFFER_SIZE,
) -> str:
    """Hache un fichier de manière synchrone.

    Args:
        path: Chemin du fichier à hacher.
        algorithm: Algorithme de hachage.
        buffer_size: Taille du buffer de lecture.

    Returns:
        Hash en hexadécimal.

    Raises:
        FileNotFoundError: Si le fichier n'existe pas.

    Example:
        >>> hash_file(Path("image.jpg"))
        'b5bb9d8014a0f9b1d61e21e796d78dccdf1352f23cd32812f4850b878ae4944c'
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    hasher = algorithm.create_hasher()

    with path.open("rb") as f:
        while chunk := f.read(buffer_size):
            hasher.update(chunk)

    return hasher.hexdigest()


async def hash_file_async(
    path: Path | str,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    buffer_size: int = ASYNC_BUFFER_SIZE,
) -> str:
    """Hache un fichier de manière asynchrone.

    Args:
        path: Chemin du fichier à hacher.
        algorithm: Algorithme de hachage.
        buffer_size: Taille du buffer de lecture.

    Returns:
        Hash en hexadécimal.

    Raises:
        FileNotFoundError: Si le fichier n'existe pas.

    Example:
        >>> await hash_file_async(Path("large_file.bin"))
        '...'
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    def _hash_sync() -> str:
        hasher = algorithm.create_hasher()
        with path.open("rb") as f:
            while chunk := f.read(buffer_size):
                hasher.update(chunk)
        return hasher.hexdigest()

    return await asyncio.to_thread(_hash_sync)


def hash_file_with_result(
    path: Path | str,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    buffer_size: int = DEFAULT_BUFFER_SIZE,
) -> HashResult:
    """Hache un fichier et retourne un résultat détaillé.

    Args:
        path: Chemin du fichier à hacher.
        algorithm: Algorithme de hachage.
        buffer_size: Taille du buffer de lecture.

    Returns:
        Instance de HashResult.

    Example:
        >>> result = hash_file_with_result(Path("image.jpg"))
        >>> print(result.hash)
        'b5bb9d...'
        >>> print(result.size_human)
        '2.3 MB'
    """
    import time

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    start_time = time.perf_counter()
    file_hash = hash_file(path, algorithm=algorithm, buffer_size=buffer_size)
    duration_ms = (time.perf_counter() - start_time) * 1000

    size = path.stat().st_size

    return HashResult(
        hash=file_hash,
        algorithm=algorithm.value,
        size=size,
        path=path,
        duration_ms=duration_ms,
    )


async def hash_file_with_result_async(
    path: Path | str,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    buffer_size: int = ASYNC_BUFFER_SIZE,
) -> HashResult:
    """Hache un fichier de manière asynchrone et retourne un résultat détaillé.

    Args:
        path: Chemin du fichier à hacher.
        algorithm: Algorithme de hachage.
        buffer_size: Taille du buffer de lecture.

    Returns:
        Instance de HashResult.
    """
    import time

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    start_time = time.perf_counter()
    file_hash = await hash_file_async(path, algorithm=algorithm, buffer_size=buffer_size)
    duration_ms = (time.perf_counter() - start_time) * 1000

    size = path.stat().st_size

    return HashResult(
        hash=file_hash,
        algorithm=algorithm.value,
        size=size,
        path=path,
        duration_ms=duration_ms,
    )


def hash_stream(
    stream: BinaryIO,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    buffer_size: int = DEFAULT_BUFFER_SIZE,
) -> str:
    """Hache un flux binaire.

    Args:
        stream: Flux binaire à hacher.
        algorithm: Algorithme de hachage.
        buffer_size: Taille du buffer de lecture.

    Returns:
        Hash en hexadécimal.

    Example:
        >>> with open("file.bin", "rb") as f:
        ...     hash_stream(f)
        '...'
    """
    hasher = algorithm.create_hasher()

    while chunk := stream.read(buffer_size):
        hasher.update(chunk)

    return hasher.hexdigest()


# ============================================================================
# HACHAGE DE RÉPERTOIRES
# ============================================================================


def hash_directory(
    path: Path | str,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    pattern: str = "*",
    recursive: bool = True,
    include_empty: bool = False,
) -> DirectoryHashResult:
    """Hache tous les fichiers d'un répertoire.

    Args:
        path: Chemin du répertoire à hacher.
        algorithm: Algorithme de hachage.
        pattern: Pattern de fichiers à inclure (glob).
        recursive: Si True, hache récursivement.
        include_empty: Si True, inclut les fichiers vides.

    Returns:
        Instance de DirectoryHashResult.

    Example:
        >>> result = hash_directory(Path("./manga_chapter"))
        >>> print(result.file_count)
        42
        >>> print(result.hash)
        '...'
    """
    import time

    path = Path(path)
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(path)

    start_time = time.perf_counter()

    # Collecter tous les fichiers
    if recursive:
        files = sorted(path.rglob(pattern))
    else:
        files = sorted(path.glob(pattern))

    # Filtrer les fichiers
    files = [f for f in files if f.is_file()]
    if not include_empty:
        files = [f for f in files if f.stat().st_size > 0]

    # Hacher chaque fichier
    file_hashes: dict[str, str] = {}
    total_size = 0
    combined_hasher = algorithm.create_hasher()

    for file_path in files:
        file_hash = hash_file(file_path, algorithm=algorithm)
        rel_path = str(file_path.relative_to(path))
        file_hashes[rel_path] = file_hash
        total_size += file_path.stat().st_size

        # Ajouter au hash combiné
        combined_hasher.update(file_hash.encode("utf-8"))

    duration_ms = (time.perf_counter() - start_time) * 1000

    return DirectoryHashResult(
        root=path,
        hash=combined_hasher.hexdigest(),
        algorithm=algorithm.value,
        file_count=len(files),
        total_size=total_size,
        file_hashes=file_hashes,
        duration_ms=duration_ms,
    )


async def hash_directory_async(
    path: Path | str,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    pattern: str = "*",
    recursive: bool = True,
    include_empty: bool = False,
    max_concurrent: int = 4,
) -> DirectoryHashResult:
    """Hache tous les fichiers d'un répertoire de manière asynchrone.

    Args:
        path: Chemin du répertoire à hacher.
        algorithm: Algorithme de hachage.
        pattern: Pattern de fichiers à inclure.
        recursive: Si True, hache récursivement.
        include_empty: Si True, inclut les fichiers vides.
        max_concurrent: Nombre maximum de hachages simultanés.

    Returns:
        Instance de DirectoryHashResult.
    """
    import time

    path = Path(path)
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(path)

    start_time = time.perf_counter()

    # Collecter tous les fichiers
    if recursive:
        files = sorted(path.rglob(pattern))
    else:
        files = sorted(path.glob(pattern))

    # Filtrer les fichiers
    files = [f for f in files if f.is_file()]
    if not include_empty:
        files = [f for f in files if f.stat().st_size > 0]

    # Hacher chaque fichier en parallèle
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _hash_one(file_path: Path) -> tuple[str, str]:
        async with semaphore:
            file_hash = await hash_file_async(file_path, algorithm=algorithm)
            rel_path = str(file_path.relative_to(path))
            return rel_path, file_hash

    tasks = [_hash_one(f) for f in files]
    results = await asyncio.gather(*tasks)

    file_hashes = dict(results)
    total_size = sum(f.stat().st_size for f in files)

    # Calculer le hash combiné
    combined_hasher = algorithm.create_hasher()
    for rel_path in sorted(file_hashes.keys()):
        combined_hasher.update(file_hashes[rel_path].encode("utf-8"))

    duration_ms = (time.perf_counter() - start_time) * 1000

    return DirectoryHashResult(
        root=path,
        hash=combined_hasher.hexdigest(),
        algorithm=algorithm.value,
        file_count=len(files),
        total_size=total_size,
        file_hashes=file_hashes,
        duration_ms=duration_ms,
    )


# ============================================================================
# VÉRIFICATION D'INTÉGRITÉ
# ============================================================================


def verify_hash(
    data: bytes | str,
    expected_hash: str,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
) -> bool:
    """Vérifie si le hash de données correspond à un hash attendu.

    Args:
        data: Données à vérifier.
        expected_hash: Hash attendu.
        algorithm: Algorithme de hachage.

    Returns:
        True si les hashes correspondent.

    Example:
        >>> verify_hash(b"Hello World", "a591a6d4...")
        True
    """
    if isinstance(data, str):
        data = data.encode("utf-8")

    actual_hash = hash_bytes(data, algorithm=algorithm)
    return compare_hashes(actual_hash, expected_hash)


def verify_file(
    path: Path | str,
    expected_hash: str,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    buffer_size: int = DEFAULT_BUFFER_SIZE,
) -> bool:
    """Vérifie l'intégrité d'un fichier.

    Args:
        path: Chemin du fichier à vérifier.
        expected_hash: Hash attendu.
        algorithm: Algorithme de hachage.
        buffer_size: Taille du buffer de lecture.

    Returns:
        True si le hash du fichier correspond.

    Raises:
        FileNotFoundError: Si le fichier n'existe pas.

    Example:
        >>> verify_file(Path("image.jpg"), "b5bb9d...")
        True
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    actual_hash = hash_file(path, algorithm=algorithm, buffer_size=buffer_size)
    return compare_hashes(actual_hash, expected_hash)


async def verify_file_async(
    path: Path | str,
    expected_hash: str,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    buffer_size: int = ASYNC_BUFFER_SIZE,
) -> bool:
    """Vérifie l'intégrité d'un fichier de manière asynchrone.

    Args:
        path: Chemin du fichier à vérifier.
        expected_hash: Hash attendu.
        algorithm: Algorithme de hachage.
        buffer_size: Taille du buffer de lecture.

    Returns:
        True si le hash du fichier correspond.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    actual_hash = await hash_file_async(path, algorithm=algorithm, buffer_size=buffer_size)
    return compare_hashes(actual_hash, expected_hash)


def verify_file_strict(
    path: Path | str,
    expected_hash: str,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    buffer_size: int = DEFAULT_BUFFER_SIZE,
) -> None:
    """Vérifie l'intégrité d'un fichier et lève une exception si échec.

    Args:
        path: Chemin du fichier à vérifier.
        expected_hash: Hash attendu.
        algorithm: Algorithme de hachage.
        buffer_size: Taille du buffer de lecture.

    Raises:
        FileNotFoundError: Si le fichier n'existe pas.
        HashMismatchError: Si le hash ne correspond pas.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    actual_hash = hash_file(path, algorithm=algorithm, buffer_size=buffer_size)

    if not compare_hashes(actual_hash, expected_hash):
        raise HashMismatchError(expected_hash, actual_hash, path)


# ============================================================================
# COMPARAISON ET VALIDATION
# ============================================================================


def compare_hashes(hash1: str, hash2: str, *, case_sensitive: bool = False) -> bool:
    """Compare deux hashes de manière sécurisée (timing-safe).

    Args:
        hash1: Premier hash.
        hash2: Deuxième hash.
        case_sensitive: Si False, ignore la casse.

    Returns:
        True si les hashes sont identiques.

    Example:
        >>> compare_hashes("abc123", "ABC123")
        True
    """
    if not case_sensitive:
        hash1 = hash1.lower()
        hash2 = hash2.lower()

    # Comparaison timing-safe
    return hashlib.compare_digest(hash1, hash2)


def is_valid_hash(hash_value: str, algorithm: str | HashAlgorithm) -> bool:
    """Vérifie si un hash est valide pour un algorithme donné.

    Args:
        hash_value: Hash à vérifier.
        algorithm: Algorithme de hachage.

    Returns:
        True si le hash est valide.

    Example:
        >>> is_valid_hash("a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e", "sha256")
        True
    """
    if isinstance(algorithm, HashAlgorithm):
        algorithm = algorithm.value

    expected_length = HASH_LENGTHS.get(algorithm)
    if expected_length is None:
        return False

    if len(hash_value) != expected_length:
        return False

    # Vérifier que c'est bien hexadécimal
    try:
        int(hash_value, 16)
        return True
    except ValueError:
        return False


def validate_hash(hash_value: str, algorithm: str | HashAlgorithm) -> str:
    """Valide un hash et le retourne normalisé.

    Args:
        hash_value: Hash à valider.
        algorithm: Algorithme de hachage.

    Returns:
        Hash normalisé (minuscules).

    Raises:
        InvalidHashError: Si le hash est invalide.
    """
    if isinstance(algorithm, HashAlgorithm):
        algorithm = algorithm.value

    if not is_valid_hash(hash_value, algorithm):
        raise InvalidHashError(hash_value, algorithm)

    return hash_value.lower()


# ============================================================================
# HACHAGE POUR DÉDUPLICATION
# ============================================================================


def dedup_hash(
    data: bytes | str,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    truncate: int = 16,
) -> str:
    """Génère un hash court pour la déduplication.

    Args:
        data: Données à hacher.
        algorithm: Algorithme de hachage.
        truncate: Nombre de caractères à garder (0 = complet).

    Returns:
        Hash (tronqué si demandé).

    Example:
        >>> dedup_hash(b"image_data", truncate=16)
        'a591a6d40bf42040'
    """
    if isinstance(data, str):
        data = data.encode("utf-8")

    full_hash = hash_bytes(data, algorithm=algorithm)

    if truncate > 0:
        return full_hash[:truncate]

    return full_hash


def file_dedup_hash(
    path: Path | str,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    truncate: int = 16,
    buffer_size: int = DEFAULT_BUFFER_SIZE,
) -> str:
    """Génère un hash court pour la déduplication de fichiers.

    Args:
        path: Chemin du fichier.
        algorithm: Algorithme de hachage.
        truncate: Nombre de caractères à garder.
        buffer_size: Taille du buffer de lecture.

    Returns:
        Hash (tronqué si demandé).
    """
    full_hash = hash_file(path, algorithm=algorithm, buffer_size=buffer_size)

    if truncate > 0:
        return full_hash[:truncate]

    return full_hash


async def file_dedup_hash_async(
    path: Path | str,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    truncate: int = 16,
    buffer_size: int = ASYNC_BUFFER_SIZE,
) -> str:
    """Génère un hash court pour la déduplication de fichiers (async).

    Args:
        path: Chemin du fichier.
        algorithm: Algorithme de hachage.
        truncate: Nombre de caractères à garder.
        buffer_size: Taille du buffer de lecture.

    Returns:
        Hash (tronqué si demandé).
    """
    full_hash = await hash_file_async(path, algorithm=algorithm, buffer_size=buffer_size)

    if truncate > 0:
        return full_hash[:truncate]

    return full_hash


# ============================================================================
# CHECKSUMS
# ============================================================================


def checksum_file(
    path: Path | str,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    buffer_size: int = DEFAULT_BUFFER_SIZE,
) -> str:
    """Calcule le checksum d'un fichier.

    Alias de hash_file() pour compatibilité.

    Args:
        path: Chemin du fichier.
        algorithm: Algorithme de hachage.
        buffer_size: Taille du buffer de lecture.

    Returns:
        Checksum en hexadécimal.
    """
    return hash_file(path, algorithm=algorithm, buffer_size=buffer_size)


def checksum_directory(
    path: Path | str,
    *,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    pattern: str = "*",
    recursive: bool = True,
) -> str:
    """Calcule le checksum d'un répertoire.

    Alias de hash_directory().hash pour compatibilité.

    Args:
        path: Chemin du répertoire.
        algorithm: Algorithme de hachage.
        pattern: Pattern de fichiers à inclure.
        recursive: Si True, hache récursivement.

    Returns:
        Checksum en hexadécimal.
    """
    result = hash_directory(
        path,
        algorithm=algorithm,
        pattern=pattern,
        recursive=recursive,
    )
    return result.hash


# ============================================================================
# HELPERS
# ============================================================================


def get_algorithm_info(algorithm: str | HashAlgorithm) -> dict[str, Any]:
    """Retourne des informations sur un algorithme de hachage.

    Args:
        algorithm: Nom de l'algorithme ou enum.

    Returns:
        Dictionnaire avec les informations.
    """
    if isinstance(algorithm, str):
        try:
            algorithm = HashAlgorithm(algorithm)
        except ValueError:
            raise InvalidAlgorithmError(algorithm)

    return {
        "name": algorithm.value,
        "hash_size_bytes": algorithm.hash_size,
        "hash_length_hex": algorithm.hex_length,
        "is_secure": algorithm.is_secure,
        "is_fast": algorithm.is_fast,
    }


def list_algorithms() -> list[dict[str, Any]]:
    """Liste tous les algorithmes de hachage supportés.

    Returns:
        Liste de dictionnaires avec les informations.
    """
    return [get_algorithm_info(algo) for algo in HashAlgorithm]


def generate_random_salt(length: int = 16) -> bytes:
    """Génère un salt aléatoire.

    Args:
        length: Longueur du salt en bytes.

    Returns:
        Salt aléatoire.
    """
    return os.urandom(length)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_BUFFER_SIZE",
    "ASYNC_BUFFER_SIZE",
    "SUPPORTED_ALGORITHMS",
    "HASH_SIZES",
    "HASH_LENGTHS",
    # Exceptions
    "HashError",
    "InvalidAlgorithmError",
    "FileNotFoundError",
    "HashMismatchError",
    "InvalidHashError",
    # Enums
    "HashAlgorithm",
    # Modèles
    "HashResult",
    "DirectoryHashResult",
    # Classe
    "IncrementalHasher",
    # Hachage de strings/bytes
    "hash_string",
    "hash_bytes",
    "hash_with_salt",
    # Hachage de fichiers
    "hash_file",
    "hash_file_async",
    "hash_file_with_result",
    "hash_file_with_result_async",
    "hash_stream",
    # Hachage de répertoires
    "hash_directory",
    "hash_directory_async",
    # Vérification
    "verify_hash",
    "verify_file",
    "verify_file_async",
    "verify_file_strict",
    # Comparaison et validation
    "compare_hashes",
    "is_valid_hash",
    "validate_hash",
    # Déduplication
    "dedup_hash",
    "file_dedup_hash",
    "file_dedup_hash_async",
    # Checksums
    "checksum_file",
    "checksum_directory",
    # Helpers
    "get_algorithm_info",
    "list_algorithms",
    "generate_random_salt",
]
