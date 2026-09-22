"""Moteur de conversion d'images multi-formats.

Ce module fournit un convertisseur d'images asynchrone supportant les formats
couramment rencontrés dans le scraping de mangas/webtoons/comics :

    JPEG, PNG, WebP, AVIF, GIF, BMP, TIFF

Fonctionnalités principales :
    - Conversion entre tous les formats supportés
    - Normalisation automatique des modes de couleur (RGBA→RGB pour JPEG, etc.)
    - Contrôle de qualité configurable (0-100)
    - Préservation optionnelle des profils ICC et métadonnées EXIF
    - Détection automatique du format source
    - Conversion par lot avec concurrence contrôlée
    - Gestion gracieuse des formats optionnels (AVIF via pillow-heif)

Architecture :
    ImageConverter
        ├── ConversionConfig (Pydantic — paramètres de conversion)
        ├── ImageFormat (enum — formats supportés)
        ├── ColorMode (enum — modes de couleur)
        └── ConversionResult (Pydantic — résultat immuable)

Les opérations Pillow étant CPU-bound, elles sont exécutées via
`asyncio.to_thread()` pour ne pas bloquer l'event loop. Un sémaphore
interne limite le nombre de conversions simultanées pour éviter la
surchauffe mémoire sur les gros lots.

Exemple d'utilisation :
    >>> converter = ImageConverter()
    >>> await converter.start()
    >>>
    >>> # Conversion simple
    >>> result = await converter.convert(
    ...     source=Path("image.png"),
    ...     dest=Path("image.webp"),
    ...     config=ConversionConfig(format=ImageFormat.WEBP, quality=85),
    ... )
    >>> print(f"Converti: {result.output_path} ({result.bytes_saved} bytes économisés)")
    >>>
    >>> # Conversion par lot
    >>> results = await converter.convert_batch(
    ...     sources=[Path("img1.jpg"), Path("img2.png")],
    ...     dest_dir=Path("output/"),
    ...     config=ConversionConfig(format=ImageFormat.WEBP, quality=80),
    >>> )
    >>>
    >>> await converter.stop()
"""

from __future__ import annotations

import asyncio
import io
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Final, Self

from loguru import logger
from PIL import Image, ImageFile, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError


# Autoriser le chargement d'images tronquées (courant sur les sites de manga)
ImageFile.LOAD_TRUNCATED_IMAGES = True


# ============================================================================
# EXCEPTIONS
# ============================================================================


class ImageConversionError(NexusDLError):
    """Exception de base pour les erreurs de conversion d'images."""


class UnsupportedFormatError(ImageConversionError):
    """Exception levée lorsqu'un format n'est pas supporté."""

    def __init__(self, fmt: str, *, available: list[str] | None = None) -> None:
        msg = f"Format non supporté: {fmt}"
        if available:
            msg += f" (formats disponibles: {', '.join(available)})"
        super().__init__(msg)
        self.format = fmt
        self.available = available or []


class InvalidImageError(ImageConversionError):
    """Exception levée lorsqu'une image est corrompue ou invalide."""

    def __init__(self, path: Path, reason: str = "") -> None:
        msg = f"Image invalide: {path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.path = path
        self.reason = reason


class ConversionFailedError(ImageConversionError):
    """Exception levée lorsqu'une conversion échoue."""

    def __init__(
        self,
        source: Path,
        target_format: str,
        reason: str = "",
    ) -> None:
        msg = f"Échec de conversion de {source} vers {target_format}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.source = source
        self.target_format = target_format
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class ImageFormat(str, Enum):
    """Formats d'images supportés par le convertisseur.

    Les formats sont classés en trois catégories :
        - LOSSY : avec perte (JPEG, WebP lossy, AVIF)
        - LOSSLESS : sans perte (PNG, WebP lossless, GIF, BMP, TIFF)
        - AUTO : détection automatique depuis l'extension
    """

    # Formats avec perte
    JPEG = "jpeg"
    JPG = "jpg"  # Alias de JPEG
    WEBP_LOSSY = "webp_lossy"
    AVIF = "avif"

    # Formats sans perte
    PNG = "png"
    WEBP_LOSSLESS = "webp_lossless"
    WEBP = "webp"  # Détermine automatiquement lossy/lossless selon la qualité
    GIF = "gif"
    BMP = "bmp"
    TIFF = "tiff"

    # Spécial
    AUTO = "auto"

    @property
    def is_lossy(self) -> bool:
        """Indique si le format est avec perte."""
        return self in (
            ImageFormat.JPEG,
            ImageFormat.JPG,
            ImageFormat.WEBP_LOSSY,
            ImageFormat.AVIF,
        )

    @property
    def extension(self) -> str:
        """Extension de fichier standard pour ce format."""
        mapping: dict[ImageFormat, str] = {
            ImageFormat.JPEG: ".jpg",
            ImageFormat.JPG: ".jpg",
            ImageFormat.PNG: ".png",
            ImageFormat.WEBP: ".webp",
            ImageFormat.WEBP_LOSSY: ".webp",
            ImageFormat.WEBP_LOSSLESS: ".webp",
            ImageFormat.AVIF: ".avif",
            ImageFormat.GIF: ".gif",
            ImageFormat.BMP: ".bmp",
            ImageFormat.TIFF: ".tiff",
            ImageFormat.AUTO: "",
        }
        return mapping[self]

    @property
    def pillow_format(self) -> str:
        """Nom du format pour Pillow (save format)."""
        mapping: dict[ImageFormat, str] = {
            ImageFormat.JPEG: "JPEG",
            ImageFormat.JPG: "JPEG",
            ImageFormat.PNG: "PNG",
            ImageFormat.WEBP: "WEBP",
            ImageFormat.WEBP_LOSSY: "WEBP",
            ImageFormat.WEBP_LOSSLESS: "WEBP",
            ImageFormat.AVIF: "AVIF",
            ImageFormat.GIF: "GIF",
            ImageFormat.BMP: "BMP",
            ImageFormat.TIFF: "TIFF",
        }
        if self == ImageFormat.AUTO:
            raise ValueError("Cannot get pillow_format for AUTO")
        return mapping[self]


class ColorMode(str, Enum):
    """Modes de couleur des images.

    Correspond aux modes Pillow les plus courants.
    """

    RGB = "RGB"  # Couleur 3 canaux (8 bits chacun)
    RGBA = "RGBA"  # Couleur + transparence
    L = "L"  # Niveaux de gris (8 bits)
    LA = "LA"  # Niveaux de gris + transparence
    P = "P"  # Palette (8 bits, indexé)
    CMYK = "CMYK"  # Quadrichromie (impression)
    I = "I"  # Entier 32 bits (pixels)
    F = "F"  # Float 32 bits (pixels)
    ONE = "1"  # Binaire (1 bit)
    AUTO = "auto"  # Conservation du mode source


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class ConversionConfig(BaseModel):
    """Configuration complète d'une conversion d'image.

    Tous les champs sont optionnels avec des valeurs par défaut raisonnables.
    Le modèle est immuable (`frozen=True`) pour garantir la cohérence lors
    des conversions par lot.
    """

    format: ImageFormat = Field(
        default=ImageFormat.AUTO,
        description="Format de sortie. AUTO = conserve le format source.",
    )
    quality: int = Field(
        default=85,
        ge=1,
        le=100,
        description="Qualité de compression (1-100). Ignoré pour les formats sans perte.",
    )
    target_mode: ColorMode = Field(
        default=ColorMode.AUTO,
        description="Mode de couleur cible. AUTO = conserve le mode source.",
    )
    preserve_icc: bool = Field(
        default=True,
        description="Préserver le profil ICC (gestion de la couleur).",
    )
    preserve_exif: bool = Field(
        default=False,
        description="Préserver les métadonnées EXIF (peut contenir des données sensibles).",
    )
    optimize: bool = Field(
        default=True,
        description="Optimiser la taille du fichier de sortie (plus lent).",
    )
    max_width: int = Field(
        default=0,
        ge=0,
        description="Largeur maximale (0 = illimité). Redimensionne si nécessaire.",
    )
    max_height: int = Field(
        default=0,
        ge=0,
        description="Hauteur maximale (0 = illimité). Redimensionne si nécessaire.",
    )
    resize_filter: str = Field(
        default="lanczos",
        description="Filtre de redimensionnement (nearest, bilinear, bicubic, lanczos).",
    )
    strip_metadata: bool = Field(
        default=True,
        description="Supprimer toutes les métadonnées non essentielles (vie privée).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid", validate_assignment=True)

    def resolve_format(self, source_format: ImageFormat) -> ImageFormat:
        """Résout le format de sortie en tenant compte de AUTO.

        Args:
            source_format: Format détecté de l'image source.

        Returns:
            Format de sortie effectif (jamais AUTO).
        """
        if self.format == ImageFormat.AUTO:
            return source_format
        return self.format


class ConversionResult(BaseModel):
    """Résultat immuable d'une conversion d'image.

    Contient toutes les métadonnées de la conversion pour le reporting
    et la traçabilité.
    """

    source_path: Path = Field(..., description="Chemin de l'image source.")
    output_path: Path = Field(..., description="Chemin de l'image de sortie.")
    source_format: ImageFormat = Field(..., description="Format source détecté.")
    output_format: ImageFormat = Field(..., description="Format de sortie effectif.")
    source_size_bytes: int = Field(..., ge=0, description="Taille source en bytes.")
    output_size_bytes: int = Field(..., ge=0, description="Taille sortie en bytes.")
    width: int = Field(..., ge=0, description="Largeur de l'image de sortie.")
    height: int = Field(..., ge=0, description="Hauteur de l'image de sortie.")
    source_mode: str = Field(..., description="Mode de couleur source.")
    output_mode: str = Field(..., description="Mode de couleur de sortie.")
    duration_seconds: float = Field(..., ge=0.0, description="Durée de conversion.")
    converted: bool = Field(
        ..., description="True si une conversion a réellement eu lieu."
    )
    resized: bool = Field(default=False, description="True si l'image a été redimensionnée.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def bytes_saved(self) -> int:
        """Nombre de bytes économisés (peut être négatif si agrandissement)."""
        return self.source_size_bytes - self.output_size_bytes

    @property
    def compression_ratio(self) -> float:
        """Ratio de compression (0.0 à 1.0). 1.0 = compression maximale."""
        if self.source_size_bytes == 0:
            return 0.0
        return max(0.0, min(1.0, self.bytes_saved / self.source_size_bytes))


class ConversionStats(BaseModel):
    """Statistiques agrégées du convertisseur."""

    total_conversions: int = Field(default=0, ge=0)
    total_bytes_input: int = Field(default=0, ge=0)
    total_bytes_output: int = Field(default=0, ge=0)
    total_bytes_saved: int = Field(default=0, ge=0)
    successful: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0, description="Images non converties (déjà au bon format).")
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def average_compression_ratio(self) -> float:
        """Ratio de compression moyen global."""
        if self.total_bytes_input == 0:
            return 0.0
        return max(
            0.0,
            min(1.0, (self.total_bytes_input - self.total_bytes_output) / self.total_bytes_input),
        )


# ============================================================================
# HELPERS — Détection et validation
# ============================================================================


# Mapping extension → ImageFormat
_EXTENSION_TO_FORMAT: Final[dict[str, ImageFormat]] = {
    ".jpg": ImageFormat.JPEG,
    ".jpeg": ImageFormat.JPEG,
    ".jpe": ImageFormat.JPEG,
    ".png": ImageFormat.PNG,
    ".webp": ImageFormat.WEBP,
    ".avif": ImageFormat.AVIF,
    ".gif": ImageFormat.GIF,
    ".bmp": ImageFormat.BMP,
    ".dib": ImageFormat.BMP,
    ".tiff": ImageFormat.TIFF,
    ".tif": ImageFormat.TIFF,
}

# Mapping format Pillow détecté → ImageFormat
_PILLOW_FORMAT_TO_ENUM: Final[dict[str, ImageFormat]] = {
    "JPEG": ImageFormat.JPEG,
    "MPO": ImageFormat.JPEG,
    "PNG": ImageFormat.PNG,
    "WEBP": ImageFormat.WEBP,
    "AVIF": ImageFormat.AVIF,
    "GIF": ImageFormat.GIF,
    "BMP": ImageFormat.BMP,
    "DIB": ImageFormat.BMP,
    "TIFF": ImageFormat.TIFF,
}

# Filtres de redimensionnement Pillow
_RESIZE_FILTERS: Final[dict[str, int]] = {
    "nearest": Image.Resampling.NEAREST,
    "bilinear": Image.Resampling.BILINEAR,
    "bicubic": Image.Resampling.BICUBIC,
    "lanczos": Image.Resampling.LANCZOS,
    "box": Image.Resampling.BOX,
    "hamming": Image.Resampling.HAMMING,
}


def detect_format_from_extension(path: Path) -> ImageFormat:
    """Détecte le format d'une image depuis son extension.

    Args:
        path: Chemin du fichier.

    Returns:
        Format détecté, ou AUTO si inconnu.
    """
    ext = path.suffix.lower()
    return _EXTENSION_TO_FORMAT.get(ext, ImageFormat.AUTO)


def detect_format_from_content(data: bytes) -> ImageFormat:
    """Détecte le format d'une image depuis son contenu binaire (magic bytes).

    Args:
        data: Contenu binaire de l'image (au moins les 32 premiers bytes).

    Returns:
        Format détecté, ou AUTO si inconnu.

    Raises:
        InvalidImageError: Si les données sont trop courtes pour être analysées.
    """
    if len(data) < 12:
        raise InvalidImageError(Path("<bytes>"), "Données trop courtes pour détection")

    # Magic bytes courants
    if data[:3] == b"\xff\xd8\xff":
        return ImageFormat.JPEG
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ImageFormat.PNG
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ImageFormat.WEBP
    if data[:4] == b"BM":
        return ImageFormat.BMP
    if data[:4] in (b"II\x2a\x00", b"MM\x00\x2a"):
        return ImageFormat.TIFF
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ImageFormat.GIF

    # AVIF : signature plus complexe (ftyp box)
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"avif", b"avis", b"mif1"):
            return ImageFormat.AVIF

    return ImageFormat.AUTO


def detect_format(path: Path) -> ImageFormat:
    """Détecte le format d'une image (extension + contenu).

    Combine la détection par extension (rapide) et par contenu (fiable).
    Le contenu prime sur l'extension en cas de conflit.

    Args:
        path: Chemin du fichier.

    Returns:
        Format détecté.

    Raises:
        InvalidImageError: Si le fichier n'existe pas ou est illisible.
    """
    if not path.exists():
        raise InvalidImageError(path, "Fichier inexistant")

    if not path.is_file():
        raise InvalidImageError(path, "N'est pas un fichier")

    # Détection par extension (rapide)
    ext_format = detect_format_from_extension(path)

    # Détection par contenu (fiable) — lire les 32 premiers bytes
    try:
        with path.open("rb") as f:
            header = f.read(32)
        content_format = detect_format_from_content(header)
    except OSError as e:
        raise InvalidImageError(path, f"Erreur de lecture: {e}") from e

    # Le contenu prime sur l'extension
    if content_format != ImageFormat.AUTO:
        return content_format

    if ext_format != ImageFormat.AUTO:
        return ext_format

    raise InvalidImageError(path, "Format non détectable")


def is_avif_supported() -> bool:
    """Vérifie si le support AVIF est disponible dans Pillow.

    AVIF nécessite soit pillow-avif-plugin, soit pillow-heif.

    Returns:
        True si AVIF est supporté, False sinon.
    """
    return "AVIF" in Image.registered_extensions().values() or "AVIF" in Image.OPEN


# ============================================================================
# CLASSE PRINCIPALE — ImageConverter
# ============================================================================


class ImageConverter:
    """Convertisseur d'images asynchrone multi-formats.

    Gère la conversion entre JPEG, PNG, WebP, AVIF, GIF, BMP, TIFF avec :
        - Normalisation automatique des modes de couleur
        - Contrôle de qualité configurable
        - Préservation optionnelle des profils ICC
        - Redimensionnement optionnel
        - Conversion par lot avec concurrence contrôlée

    Lifecycle :
        >>> converter = ImageConverter()
        >>> await converter.start()
        >>> # ... conversions ...
        >>> await converter.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Les opérations Pillow sont exécutées dans des threads via
        `asyncio.to_thread()`. Un sémaphore interne limite la concurrence.
    """

    # Constantes de configuration
    _DEFAULT_MAX_CONCURRENT: Final[int] = 4
    _DEFAULT_QUALITY: Final[int] = 85
    _MIN_FILE_SIZE_FOR_CONVERSION: Final[int] = 1024  # 1 KB

    # Formats nécessitant un mode RGB (pas de transparence)
    _RGB_ONLY_FORMATS: Final[frozenset[ImageFormat]] = frozenset(
        {ImageFormat.JPEG, ImageFormat.JPG}
    )

    def __init__(
        self,
        *,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
        default_quality: int = _DEFAULT_QUALITY,
        temp_dir: Path | None = None,
    ) -> None:
        """Initialise le convertisseur d'images.

        Args:
            max_concurrent: Nombre maximum de conversions simultanées.
            default_quality: Qualité par défaut si non spécifiée dans la config.
            temp_dir: Répertoire temporaire pour les conversions (défaut: système).
        """
        if max_concurrent <= 0:
            raise ValueError(f"max_concurrent must be positive, got {max_concurrent}")
        if not 1 <= default_quality <= 100:
            raise ValueError(
                f"default_quality must be in [1, 100], got {default_quality}"
            )

        self._max_concurrent = max_concurrent
        self._default_quality = default_quality
        self._temp_dir = temp_dir

        self._semaphore: asyncio.Semaphore | None = None
        self._started: bool = False
        self._start_time: float = 0.0

        # Statistiques
        self._total_conversions: int = 0
        self._total_bytes_input: int = 0
        self._total_bytes_output: int = 0
        self._successful: int = 0
        self._failed: int = 0
        self._skipped: int = 0
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="image_converter")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre le convertisseur et initialise les ressources.

        Vérifie la disponibilité des formats optionnels (AVIF).
        """
        if self._started:
            self._logger.warning("ImageConverter déjà démarré, ignore")
            return

        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._started = True
        self._start_time = asyncio.get_event_loop().time()

        # Vérifier le support AVIF
        avif_supported = await asyncio.to_thread(is_avif_supported)
        self._logger.info(
            "ImageConverter démarré: max_concurrent={}, avif_supported={}",
            self._max_concurrent,
            avif_supported,
        )

    async def stop(self) -> None:
        """Arrête le convertisseur et libère les ressources."""
        if not self._started:
            return

        self._semaphore = None
        self._started = False
        self._logger.info("ImageConverter arrêté")

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
        """Indique si le convertisseur est démarré."""
        return self._started

    @property
    def max_concurrent(self) -> int:
        """Nombre maximum de conversions simultanées."""
        return self._max_concurrent

    # ------------------------------------------------------------------------
    # API publique — Conversion simple
    # ------------------------------------------------------------------------

    async def convert(
        self,
        source: Path,
        dest: Path,
        *,
        config: ConversionConfig | None = None,
    ) -> ConversionResult:
        """Convertit une image source vers un format de destination.

        Si le format source et le format cible sont identiques et qu'aucune
        transformation n'est demandée (pas de redimensionnement, pas de
        changement de mode), la conversion est sautée et le fichier source
        est copié vers la destination.

        Args:
            source: Chemin de l'image source.
            dest: Chemin de destination (l'extension détermine le format
                  si config.format = AUTO).
            config: Configuration de conversion (défaut: valeurs par défaut).

        Returns:
            Résultat de la conversion avec métadonnées complètes.

        Raises:
            ImageConversionError: Si la conversion échoue.
            InvalidImageError: Si l'image source est corrompue.
            UnsupportedFormatError: Si le format n'est pas supporté.
        """
        self._ensure_started()
        assert self._semaphore is not None

        effective_config = config or ConversionConfig(quality=self._default_quality)

        async with self._semaphore:
            start_time = asyncio.get_event_loop().time()

            # 1. Valider la source
            source_format = detect_format(source)
            source_size = source.stat().st_size

            # 2. Résoudre le format de sortie
            if effective_config.format == ImageFormat.AUTO:
                # Déterminer depuis l'extension de dest
                output_format = detect_format_from_extension(dest)
                if output_format == ImageFormat.AUTO:
                    # Conserver le format source
                    output_format = source_format
            else:
                output_format = effective_config.format

            # Vérifier le support AVIF
            if output_format == ImageFormat.AVIF and not is_avif_supported():
                raise UnsupportedFormatError(
                    "AVIF",
                    available=["JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF"],
                )

            # 3. Vérifier si la conversion est nécessaire
            needs_conversion = await self._needs_conversion(
                source=source,
                source_format=source_format,
                output_format=output_format,
                config=effective_config,
            )

            if not needs_conversion:
                # Copier le fichier tel quel
                await asyncio.to_thread(shutil.copy2, source, dest)
                duration = asyncio.get_event_loop().time() - start_time

                # Lire les dimensions
                width, height, source_mode = await asyncio.to_thread(
                    self._read_image_metadata, source
                )

                result = ConversionResult(
                    source_path=source,
                    output_path=dest,
                    source_format=source_format,
                    output_format=output_format,
                    source_size_bytes=source_size,
                    output_size_bytes=dest.stat().st_size,
                    width=width,
                    height=height,
                    source_mode=source_mode,
                    output_mode=source_mode,
                    duration_seconds=duration,
                    converted=False,
                    resized=False,
                )

                async with self._stats_lock:
                    self._skipped += 1
                    self._total_bytes_input += source_size
                    self._total_bytes_output += result.output_size_bytes

                self._logger.trace(
                    "Conversion sautée (format identique): {} → {}",
                    source.name,
                    dest.name,
                )
                return result

            # 4. Effectuer la conversion
            try:
                result = await self._do_convert(
                    source=source,
                    dest=dest,
                    source_format=source_format,
                    output_format=output_format,
                    config=effective_config,
                    start_time=start_time,
                )

                async with self._stats_lock:
                    self._successful += 1
                    self._total_conversions += 1
                    self._total_bytes_input += source_size
                    self._total_bytes_output += result.output_size_bytes

                self._logger.debug(
                    "Conversion réussie: {} → {} ({} → {}, {:.1f}% économisé)",
                    source.name,
                    dest.name,
                    source_format.value,
                    output_format.value,
                    result.compression_ratio * 100,
                )
                return result

            except Exception as e:
                async with self._stats_lock:
                    self._failed += 1
                    self._total_conversions += 1

                self._logger.error(
                    "Échec de conversion: {} → {} — {}",
                    source.name,
                    dest.name,
                    e,
                )
                raise

    # ------------------------------------------------------------------------
    # API publique — Conversion par lot
    # ------------------------------------------------------------------------

    async def convert_batch(
        self,
        sources: Sequence[Path],
        dest_dir: Path,
        *,
        config: ConversionConfig | None = None,
        suffix: str = "",
        on_progress: Any | None = None,
    ) -> list[ConversionResult]:
        """Convertit un lot d'images vers un répertoire de destination.

        Les conversions sont exécutées en parallèle avec la concurrence
        contrôlée par le sémaphore interne.

        Args:
            sources: Liste des chemins sources.
            dest_dir: Répertoire de destination (créé si nécessaire).
            config: Configuration de conversion (appliquée à toutes les images).
            suffix: Suffixe à ajouter aux noms de fichiers (ex: "_webp").
            on_progress: Callback optionnel appelé après chaque conversion
                         signature: (result: ConversionResult) -> None.

        Returns:
            Liste des résultats de conversion (un par source).

        Raises:
            ImageConversionError: Si une conversion échoue (les autres continuent).
        """
        self._ensure_started()

        if not sources:
            return []

        # Créer le répertoire de destination
        await asyncio.to_thread(dest_dir.mkdir, parents=True, exist_ok=True)

        effective_config = config or ConversionConfig(quality=self._default_quality)

        # Préparer les tâches
        tasks: list[asyncio.Task[ConversionResult]] = []
        for source in sources:
            # Déterminer le nom de sortie
            output_format = effective_config.format
            if output_format == ImageFormat.AUTO:
                output_format = detect_format(source)

            ext = output_format.extension
            output_name = source.stem + suffix + ext
            dest = dest_dir / output_name

            task = asyncio.create_task(
                self.convert(source, dest, config=effective_config),
                name=f"convert_{source.name}",
            )
            tasks.append(task)

        # Exécuter toutes les conversions
        results: list[ConversionResult] = []
        for task in tasks:
            try:
                result = await task
                results.append(result)
                if on_progress is not None:
                    on_progress(result)
            except Exception as e:
                self._logger.warning("Conversion échouée dans le lot: {}", e)
                # Continuer avec les autres

        self._logger.info(
            "Lot terminé: {}/{} conversions réussies",
            len(results),
            len(sources),
        )
        return results

    # ------------------------------------------------------------------------
    # API publique — Conversion en mémoire
    # ------------------------------------------------------------------------

    async def convert_bytes(
        self,
        data: bytes,
        *,
        source_format: ImageFormat | None = None,
        config: ConversionConfig | None = None,
    ) -> tuple[bytes, ConversionResult]:
        """Convertit une image en mémoire (sans fichier).

        Utile pour les conversions à la volée lors du streaming.

        Args:
            data: Contenu binaire de l'image source.
            source_format: Format source (détection auto si None).
            config: Configuration de conversion.

        Returns:
            Tuple (données converties, résultat de conversion).

        Raises:
            ImageConversionError: Si la conversion échoue.
        """
        self._ensure_started()
        assert self._semaphore is not None

        effective_config = config or ConversionConfig(quality=self._default_quality)

        # Détecter le format source
        if source_format is None:
            source_format = detect_format_from_content(data)
            if source_format == ImageFormat.AUTO:
                raise InvalidImageError(Path("<bytes>"), "Format non détectable")

        # Résoudre le format de sortie
        output_format = effective_config.resolve_format(source_format)

        async with self._semaphore:
            start_time = asyncio.get_event_loop().time()

            # Conversion via Pillow en mémoire
            output_data, width, height, source_mode, output_mode = (
                await asyncio.to_thread(
                    self._convert_in_memory,
                    data,
                    source_format,
                    output_format,
                    effective_config,
                )
            )

            duration = asyncio.get_event_loop().time() - start_time

            result = ConversionResult(
                source_path=Path("<bytes>"),
                output_path=Path("<bytes>"),
                source_format=source_format,
                output_format=output_format,
                source_size_bytes=len(data),
                output_size_bytes=len(output_data),
                width=width,
                height=height,
                source_mode=source_mode,
                output_mode=output_mode,
                duration_seconds=duration,
                converted=True,
                resized=False,
            )

            async with self._stats_lock:
                self._successful += 1
                self._total_conversions += 1
                self._total_bytes_input += len(data)
                self._total_bytes_output += len(output_data)

            return output_data, result

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> ConversionStats:
        """Retourne les statistiques agrégées du convertisseur.

        Returns:
            Objet ConversionStats avec tous les compteurs.
        """
        async with self._stats_lock:
            uptime = 0.0
            if self._start_time > 0:
                uptime = asyncio.get_event_loop().time() - self._start_time

            return ConversionStats(
                total_conversions=self._total_conversions,
                total_bytes_input=self._total_bytes_input,
                total_bytes_output=self._total_bytes_output,
                total_bytes_saved=self._total_bytes_input - self._total_bytes_output,
                successful=self._successful,
                failed=self._failed,
                skipped=self._skipped,
                uptime_seconds=uptime,
            )

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques du convertisseur."""
        async with self._stats_lock:
            self._total_conversions = 0
            self._total_bytes_input = 0
            self._total_bytes_output = 0
            self._successful = 0
            self._failed = 0
            self._skipped = 0
            self._start_time = asyncio.get_event_loop().time()

    # ------------------------------------------------------------------------
    # Méthodes internes — Conversion
    # ------------------------------------------------------------------------

    async def _needs_conversion(
        self,
        source: Path,
        source_format: ImageFormat,
        output_format: ImageFormat,
        config: ConversionConfig,
    ) -> bool:
        """Détermine si une conversion est nécessaire.

        Une conversion est sautée si :
            - Le format source et cible sont identiques
            - Aucun redimensionnement n'est demandé
            - Le mode de couleur est AUTO
            - La taille du fichier est > 1 KB (pour éviter les faux positifs)

        Args:
            source: Chemin source.
            source_format: Format source détecté.
            output_format: Format cible.
            config: Configuration de conversion.

        Returns:
            True si la conversion est nécessaire, False sinon.
        """
        # Formats différents → conversion nécessaire
        if source_format != output_format:
            return True

        # Redimensionnement demandé → conversion nécessaire
        if config.max_width > 0 or config.max_height > 0:
            return True

        # Changement de mode demandé → conversion nécessaire
        if config.target_mode != ColorMode.AUTO:
            return True

        # Fichier trop petit → conversion nécessaire (peut être corrompu)
        if source.stat().st_size < self._MIN_FILE_SIZE_FOR_CONVERSION:
            return True

        return False

    async def _do_convert(
        self,
        source: Path,
        dest: Path,
        source_format: ImageFormat,
        output_format: ImageFormat,
        config: ConversionConfig,
        start_time: float,
    ) -> ConversionResult:
        """Effectue la conversion effective d'une image.

        Args:
            source: Chemin source.
            dest: Chemin de destination.
            source_format: Format source.
            output_format: Format cible.
            config: Configuration de conversion.
            start_time: Timestamp de début (pour calcul de durée).

        Returns:
            Résultat de la conversion.

        Raises:
            ConversionFailedError: Si la conversion échoue.
        """
        try:
            width, height, source_mode, output_mode = await asyncio.to_thread(
                self._convert_file,
                source,
                dest,
                source_format,
                output_format,
                config,
            )

            duration = asyncio.get_event_loop().time() - start_time

            return ConversionResult(
                source_path=source,
                output_path=dest,
                source_format=source_format,
                output_format=output_format,
                source_size_bytes=source.stat().st_size,
                output_size_bytes=dest.stat().st_size,
                width=width,
                height=height,
                source_mode=source_mode,
                output_mode=output_mode,
                duration_seconds=duration,
                converted=True,
                resized=(
                    config.max_width > 0
                    or config.max_height > 0
                ),
            )

        except UnsupportedFormatError:
            raise
        except InvalidImageError:
            raise
        except Exception as e:
            raise ConversionFailedError(
                source,
                output_format.value,
                str(e),
            ) from e

    @staticmethod
    def _convert_file(
        source: Path,
        dest: Path,
        source_format: ImageFormat,
        output_format: ImageFormat,
        config: ConversionConfig,
    ) -> tuple[int, int, str, str]:
        """Effectue la conversion synchrone (exécutée dans un thread).

        Args:
            source: Chemin source.
            dest: Chemin de destination.
            source_format: Format source.
            output_format: Format cible.
            config: Configuration de conversion.

        Returns:
            Tuple (width, height, source_mode, output_mode).

        Raises:
            ConversionFailedError: Si la conversion échoue.
            InvalidImageError: Si l'image est corrompue.
        """
        try:
            with Image.open(source) as img:
                source_mode = img.mode
                original_width, original_height = img.size

                # Extraire les métadonnées à préserver
                icc_profile = img.info.get("icc_profile") if config.preserve_icc else None
                exif_data = img.info.get("exif") if config.preserve_exif else None

                # Normaliser le mode de couleur
                target_mode = ImageConverter._resolve_target_mode(
                    source_mode=source_mode,
                    target_mode=config.target_mode,
                    output_format=output_format,
                )

                if img.mode != target_mode:
                    img = img.convert(target_mode)

                # Redimensionnement si nécessaire
                resized = False
                if config.max_width > 0 or config.max_height > 0:
                    new_width, new_height = ImageConverter._compute_resize(
                        original_width,
                        original_height,
                        config.max_width,
                        config.max_height,
                    )
                    if (new_width, new_height) != (original_width, original_height):
                        filter_name = config.resize_filter
                        resample = _RESIZE_FILTERS.get(filter_name, Image.Resampling.LANCZOS)
                        img = img.resize((new_width, new_height), resample)
                        resized = True

                final_width, final_height = img.size

                # Préparer les paramètres de sauvegarde
                save_kwargs: dict[str, Any] = {
                    "format": output_format.pillow_format,
                }

                # Qualité pour les formats avec perte
                if output_format.is_lossy:
                    save_kwargs["quality"] = config.quality

                # WebP lossless
                if output_format == ImageFormat.WEBP_LOSSLESS:
                    save_kwargs["lossless"] = True
                    save_kwargs.pop("quality", None)

                # WebP auto : lossless si qualité >= 95
                if output_format == ImageFormat.WEBP and config.quality >= 95:
                    save_kwargs["lossless"] = True
                    save_kwargs.pop("quality", None)

                # Optimisation
                if config.optimize:
                    save_kwargs["optimize"] = True

                # Profil ICC
                if icc_profile is not None:
                    save_kwargs["icc_profile"] = icc_profile

                # EXIF
                if exif_data is not None and output_format in (
                    ImageFormat.JPEG,
                    ImageFormat.JPG,
                    ImageFormat.TIFF,
                    ImageFormat.WEBP,
                ):
                    save_kwargs["exif"] = exif_data

                # Suppression des métadonnées si demandé
                if config.strip_metadata and not config.preserve_exif:
                    # Ne pas ajouter d'EXIF
                    save_kwargs.pop("exif", None)

                # Paramètres spécifiques par format
                if output_format in (ImageFormat.JPEG, ImageFormat.JPG):
                    save_kwargs["subsampling"] = "4:2:0"  # Compression chroma
                    if "progressive" not in save_kwargs:
                        save_kwargs["progressive"] = True

                elif output_format == ImageFormat.PNG:
                    save_kwargs["compress_level"] = 6

                elif output_format in (ImageFormat.WEBP, ImageFormat.WEBP_LOSSY, ImageFormat.WEBP_LOSSLESS):
                    save_kwargs["method"] = 4  # Compromis vitesse/size

                # Créer le répertoire parent si nécessaire
                dest.parent.mkdir(parents=True, exist_ok=True)

                # Sauvegarder
                img.save(dest, **save_kwargs)

                return final_width, final_height, source_mode, target_mode

        except UnidentifiedImageError as e:
            raise InvalidImageError(source, f"Image non reconnue: {e}") from e
        except OSError as e:
            raise ConversionFailedError(source, output_format.value, str(e)) from e
        except Exception as e:
            raise ConversionFailedError(source, output_format.value, str(e)) from e

    @staticmethod
    def _convert_in_memory(
        data: bytes,
        source_format: ImageFormat,
        output_format: ImageFormat,
        config: ConversionConfig,
    ) -> tuple[bytes, int, int, str, str]:
        """Effectue la conversion en mémoire (exécutée dans un thread).

        Args:
            data: Données source.
            source_format: Format source.
            output_format: Format cible.
            config: Configuration.

        Returns:
            Tuple (output_data, width, height, source_mode, output_mode).
        """
        try:
            with Image.open(io.BytesIO(data)) as img:
                source_mode = img.mode

                # Extraire les métadonnées
                icc_profile = img.info.get("icc_profile") if config.preserve_icc else None
                exif_data = img.info.get("exif") if config.preserve_exif else None

                # Normaliser le mode
                target_mode = ImageConverter._resolve_target_mode(
                    source_mode=source_mode,
                    target_mode=config.target_mode,
                    output_format=output_format,
                )

                if img.mode != target_mode:
                    img = img.convert(target_mode)

                # Redimensionnement
                if config.max_width > 0 or config.max_height > 0:
                    new_width, new_height = ImageConverter._compute_resize(
                        img.size[0],
                        img.size[1],
                        config.max_width,
                        config.max_height,
                    )
                    if (new_width, new_height) != img.size:
                        filter_name = config.resize_filter
                        resample = _RESIZE_FILTERS.get(filter_name, Image.Resampling.LANCZOS)
                        img = img.resize((new_width, new_height), resample)

                final_width, final_height = img.size

                # Préparer les paramètres
                save_kwargs: dict[str, Any] = {"format": output_format.pillow_format}

                if output_format.is_lossy:
                    save_kwargs["quality"] = config.quality

                if output_format == ImageFormat.WEBP_LOSSLESS:
                    save_kwargs["lossless"] = True
                    save_kwargs.pop("quality", None)

                if config.optimize:
                    save_kwargs["optimize"] = True

                if icc_profile is not None:
                    save_kwargs["icc_profile"] = icc_profile

                if exif_data is not None and output_format in (
                    ImageFormat.JPEG,
                    ImageFormat.JPG,
                    ImageFormat.TIFF,
                    ImageFormat.WEBP,
                ):
                    save_kwargs["exif"] = exif_data

                # Sérialiser en mémoire
                output_buffer = io.BytesIO()
                img.save(output_buffer, **save_kwargs)
                output_data = output_buffer.getvalue()

                return output_data, final_width, final_height, source_mode, target_mode

        except UnidentifiedImageError as e:
            raise InvalidImageError(Path("<bytes>"), f"Image non reconnue: {e}") from e
        except Exception as e:
            raise ConversionFailedError(
                Path("<bytes>"),
                output_format.value,
                str(e),
            ) from e

    # ------------------------------------------------------------------------
    # Méthodes internes — Helpers
    # ------------------------------------------------------------------------

    @staticmethod
    def _resolve_target_mode(
        source_mode: str,
        target_mode: ColorMode,
        output_format: ImageFormat,
    ) -> str:
        """Résout le mode de couleur cible.

        Règles :
            - JPEG ne supporte que RGB (pas de transparence)
            - Si target_mode = AUTO, adapter selon le format de sortie
            - Sinon, utiliser target_mode explicitement

        Args:
            source_mode: Mode source (ex: 'RGBA', 'P', 'L').
            target_mode: Mode cible demandé.
            output_format: Format de sortie.

        Returns:
            Mode de couleur effectif (ex: 'RGB', 'RGBA').
        """
        # JPEG ne supporte que RGB
        if output_format in ImageConverter._RGB_ONLY_FORMATS:
            if source_mode in ("RGBA", "LA", "PA"):
                return "RGB"
            if source_mode == "P":
                return "RGB"
            if source_mode == "L":
                return "L"  # JPEG supporte les niveaux de gris
            return "RGB"

        # Mode explicite demandé
        if target_mode != ColorMode.AUTO:
            return target_mode.value

        # Mode AUTO : conserver le mode source si compatible
        compatible_modes = {
            ImageFormat.PNG: {"RGB", "RGBA", "L", "LA", "P"},
            ImageFormat.WEBP: {"RGB", "RGBA", "L", "LA", "P"},
            ImageFormat.WEBP_LOSSY: {"RGB", "RGBA", "L", "LA"},
            ImageFormat.WEBP_LOSSLESS: {"RGB", "RGBA", "L", "LA", "P"},
            ImageFormat.GIF: {"P", "L", "RGB", "RGBA"},
            ImageFormat.BMP: {"RGB", "L"},
            ImageFormat.TIFF: {"RGB", "RGBA", "L", "LA", "CMYK"},
            ImageFormat.AVIF: {"RGB", "RGBA", "L", "LA"},
        }

        allowed = compatible_modes.get(output_format, {"RGB"})
        if source_mode in allowed:
            return source_mode

        # Fallback : RGB
        return "RGB"

    @staticmethod
    def _compute_resize(
        width: int,
        height: int,
        max_width: int,
        max_height: int,
    ) -> tuple[int, int]:
        """Calcule les nouvelles dimensions en respectant le ratio.

        Args:
            width: Largeur originale.
            height: Hauteur originale.
            max_width: Largeur maximale (0 = illimité).
            max_height: Hauteur maximale (0 = illimité).

        Returns:
            Tuple (new_width, new_height).
        """
        if width <= 0 or height <= 0:
            return width, height

        new_width = width
        new_height = height

        # Limiter par la largeur
        if max_width > 0 and width > max_width:
            ratio = max_width / width
            new_width = max_width
            new_height = int(height * ratio)

        # Limiter par la hauteur
        if max_height > 0 and new_height > max_height:
            ratio = max_height / new_height
            new_height = max_height
            new_width = int(new_width * ratio)

        return new_width, new_height

    @staticmethod
    def _read_image_metadata(path: Path) -> tuple[int, int, str]:
        """Lit les métadonnées d'une image sans la charger complètement.

        Args:
            path: Chemin de l'image.

        Returns:
            Tuple (width, height, mode).
        """
        with Image.open(path) as img:
            return img.size[0], img.size[1], img.mode

    def _ensure_started(self) -> None:
        """Vérifie que le convertisseur est démarré.

        Raises:
            ImageConversionError: Si le convertisseur n'est pas démarré.
        """
        if not self._started:
            raise ImageConversionError(
                "ImageConverter must be started before use. Call await converter.start()"
            )

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<ImageConverter status={status} "
            f"max_concurrent={self._max_concurrent} "
            f"conversions={self._total_conversions}>"
        )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "ImageConversionError",
    "UnsupportedFormatError",
    "InvalidImageError",
    "ConversionFailedError",
    # Enums
    "ImageFormat",
    "ColorMode",
    # Modèles
    "ConversionConfig",
    "ConversionResult",
    "ConversionStats",
    # Helpers
    "detect_format",
    "detect_format_from_extension",
    "detect_format_from_content",
    "is_avif_supported",
    # Classe principale
    "ImageConverter",
]
