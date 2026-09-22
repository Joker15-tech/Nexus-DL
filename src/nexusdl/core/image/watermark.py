"""Système de filigranes (watermarks) configurable pour les images.

Ce module fournit un applicateur de filigranes asynchrone supportant trois
modes d'application pour protéger les images téléchargées ou les marquer
comme provenant de NexusDL :

    1. **TEXT**     : Filigrane texte (police TTF, couleur, opacité, rotation)
    2. **IMAGE**    : Filigrane image (logo PNG avec transparence)
    3. **TILE**     : Filigrane répété en mosaïque (protection anti-vol)

Fonctionnalités principales :
    - 10 positions prédéfinies (9 coins/centres + mode tile)
    - Opacité ajustable (0.0 à 1.0)
    - Rotation optionnelle du texte (-180° à +180°)
    - Détection automatique des polices système (TTF/OTF)
    - Taille de police adaptative (pourcentage de la largeur de l'image)
    - Couleur personnalisable avec canal alpha (RGBA)
    - Mode tile avec espacement et rotation configurables
    - Application par lot avec concurrence contrôlée
    - Préservation du format source (JPEG→JPEG, PNG→PNG, etc.)

Architecture :
    ImageWatermarker
        ├── WatermarkType (enum — TEXT, IMAGE, TILE)
        ├── WatermarkPosition (enum — 10 positions)
        ├── WatermarkConfig (Pydantic — configuration complète)
        ├── WatermarkResult (Pydantic — résultat immuable)
        └── WatermarkStats (Pydantic — statistiques agrégées)

Les opérations Pillow étant CPU-bound, elles sont exécutées via
`asyncio.to_thread()`. Un sémaphore limite la concurrence.

Exemple d'utilisation :
    >>> watermarker = ImageWatermarker()
    >>> await watermarker.start()
    >>>
    >>> # Filigrane texte simple
    >>> config = WatermarkConfig(
    ...     type=WatermarkType.TEXT,
    ...     text="NexusDL",
    ...     position=WatermarkPosition.BOTTOM_RIGHT,
    ...     opacity=0.5,
    ... )
    >>> result = await watermarker.apply(
    ...     source=Path("page001.jpg"),
    ...     dest=Path("page001_wm.jpg"),
    ...     config=config,
    ... )
    >>> print(f"Filigrane appliqué: {result.output_path}")
    >>>
    >>> # Filigrane image (logo)
    >>> config = WatermarkConfig(
    ...     type=WatermarkType.IMAGE,
    ...     image_path=Path("logo.png"),
    ...     position=WatermarkPosition.CENTER,
    ...     opacity=0.3,
    ...     scale=0.2,  # 20% de la largeur de l'image
    ... )
    >>> result = await watermarker.apply(source, dest, config=config)
    >>>
    >>> # Filigrane tile (mosaïque anti-vol)
    >>> config = WatermarkConfig(
    ...     type=WatermarkType.TILE,
    ...     text="© NexusDL",
    ...     opacity=0.15,
    ...     tile_spacing=200,
    ...     tile_rotation=-30,
    ... )
    >>> result = await watermarker.apply(source, dest, config=config)
    >>>
    >>> await watermarker.stop()
"""

from __future__ import annotations

import asyncio
import platform
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Final, Self

from loguru import logger
from PIL import Image, ImageDraw, ImageFile, ImageFont, ImageOps
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.image.converter import ImageFormat, detect_format


# Autoriser le chargement d'images tronquées
ImageFile.LOAD_TRUNCATED_IMAGES = True


# ============================================================================
# EXCEPTIONS
# ============================================================================


class WatermarkError(NexusDLError):
    """Exception de base pour les erreurs du système de filigranes."""


class WatermarkConfigError(WatermarkError):
    """Exception levée lorsqu'une configuration de filigrane est invalide."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Configuration de filigrane invalide: {reason}")
        self.reason = reason


class WatermarkImageNotFoundError(WatermarkError):
    """Exception levée lorsque l'image de filigrane est introuvable."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"Image de filigrane introuvable: {path}")
        self.path = path


class WatermarkFontNotFoundError(WatermarkError):
    """Exception levée lorsqu'aucune police ne peut être chargée."""

    def __init__(self, requested: str | None = None) -> None:
        msg = "Aucune police TrueType disponible"
        if requested:
            msg += f" (demandée: {requested})"
        super().__init__(msg)
        self.requested = requested


class WatermarkApplicationError(WatermarkError):
    """Exception levée lorsque l'application du filigrane échoue."""

    def __init__(self, source: Path, reason: str = "") -> None:
        msg = f"Échec de l'application du filigrane sur {source}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.source = source
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class WatermarkType(str, Enum):
    """Type de filigrane à appliquer.

    TEXT  : Filigrane texte (police TTF, couleur, opacité).
    IMAGE : Filigrane image (logo PNG avec transparence).
    TILE  : Filigrane répété en mosaïque (protection anti-vol).
    """

    TEXT = "text"
    IMAGE = "image"
    TILE = "tile"


class WatermarkPosition(str, Enum):
    """Position du filigrane sur l'image.

    Les 9 positions standard couvrent les coins, centres et bords.
    La position TILE est spéciale : le filigrane est répété en mosaïque.
    """

    TOP_LEFT = "top_left"
    TOP_CENTER = "top_center"
    TOP_RIGHT = "top_right"
    CENTER_LEFT = "center_left"
    CENTER = "center"
    CENTER_RIGHT = "center_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_CENTER = "bottom_center"
    BOTTOM_RIGHT = "bottom_right"
    TILE = "tile"  # Mode mosaïque (ignoring padding)


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class WatermarkConfig(BaseModel):
    """Configuration complète d'un filigrane.

    Tous les champs sont optionnels avec des valeurs par défaut raisonnables.
    Le modèle est immuable (`frozen=True`) pour garantir la cohérence.
    """

    # Type et contenu
    type: WatermarkType = Field(
        default=WatermarkType.TEXT,
        description="Type de filigrane (TEXT, IMAGE, TILE).",
    )
    text: str = Field(
        default="NexusDL",
        description="Texte du filigrane (pour TEXT et TILE).",
    )
    image_path: Path | None = Field(
        default=None,
        description="Chemin vers l'image de filigrane (pour IMAGE). Doit être un PNG avec transparence.",
    )

    # Position et layout
    position: WatermarkPosition = Field(
        default=WatermarkPosition.BOTTOM_RIGHT,
        description="Position du filigrane sur l'image.",
    )
    padding: int = Field(
        default=20,
        ge=0,
        le=500,
        description="Marge entre le filigrane et le bord de l'image (pixels).",
    )

    # Style texte
    font_path: Path | None = Field(
        default=None,
        description="Chemin vers une police TTF/OTF (None = détection auto).",
    )
    font_size: int = Field(
        default=0,
        ge=0,
        description="Taille de police en pixels (0 = auto, 3% de la largeur de l'image).",
    )
    font_color: str = Field(
        default="#FFFFFF",
        description="Couleur du texte (hex RGB ou RGBA, ex: '#FFFFFF' ou '#FFFFFF80').",
    )
    font_outline: bool = Field(
        default=True,
        description="Ajouter un contour sombre au texte pour la lisibilité.",
    )
    font_outline_color: str = Field(
        default="#000000",
        description="Couleur du contour du texte.",
    )
    font_outline_width: int = Field(
        default=2,
        ge=0,
        le=10,
        description="Épaisseur du contour du texte (pixels).",
    )
    rotation: float = Field(
        default=0.0,
        ge=-180.0,
        le=180.0,
        description="Rotation du filigrane en degrés (-180 à +180).",
    )

    # Opacité
    opacity: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Opacité du filigrane (0.0 = invisible, 1.0 = opaque).",
    )

    # Mode TILE spécifique
    tile_spacing: int = Field(
        default=200,
        ge=50,
        le=1000,
        description="Espacement entre les tuiles en pixels (pour TILE).",
    )
    tile_rotation: float = Field(
        default=-30.0,
        ge=-90.0,
        le=90.0,
        description="Rotation des tuiles en degrés (pour TILE).",
    )

    # Mode IMAGE spécifique
    scale: float = Field(
        default=0.2,
        gt=0.0,
        le=1.0,
        description="Échelle de l'image de filigrane relative à la largeur de l'image (pour IMAGE).",
    )

    # Comportement
    skip_small_images: bool = Field(
        default=True,
        description="Ne pas appliquer le filigrane sur les images < 200x200 pixels.",
    )
    small_image_threshold: int = Field(
        default=200,
        ge=0,
        description="Seuil de dimensions pour skip_small_images (pixels).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid", validate_assignment=True)

    def validate_config(self) -> None:
        """Valide la cohérence de la configuration.

        Raises:
            WatermarkConfigError: Si la configuration est incohérente.
        """
        if self.type == WatermarkType.TEXT and not self.text.strip():
            raise WatermarkConfigError("Le texte du filigrane ne peut pas être vide")

        if self.type == WatermarkType.TILE and not self.text.strip():
            raise WatermarkConfigError("Le texte du filigrane tile ne peut pas être vide")

        if self.type == WatermarkType.IMAGE and self.image_path is None:
            raise WatermarkConfigError(
                "image_path est requis pour le type IMAGE"
            )

        if self.type != WatermarkType.TILE and self.position == WatermarkPosition.TILE:
            raise WatermarkConfigError(
                "La position TILE n'est valide que pour le type TILE"
            )

        if self.type == WatermarkType.TILE and self.position != WatermarkPosition.TILE:
            # Forcer la position TILE pour le type TILE
            pass  # On gère ça dans le code, pas une erreur


class WatermarkResult(BaseModel):
    """Résultat immuable de l'application d'un filigrane."""

    source_path: Path = Field(..., description="Chemin de l'image source.")
    output_path: Path = Field(..., description="Chemin de l'image avec filigrane.")
    source_format: ImageFormat = Field(..., description="Format source.")
    output_format: ImageFormat = Field(..., description="Format de sortie.")
    source_size_bytes: int = Field(..., ge=0, description="Taille source en bytes.")
    output_size_bytes: int = Field(..., ge=0, description="Taille sortie en bytes.")
    width: int = Field(..., ge=0, description="Largeur de l'image.")
    height: int = Field(..., ge=0, description="Hauteur de l'image.")
    watermark_type: WatermarkType = Field(..., description="Type de filigrane appliqué.")
    watermark_position: WatermarkPosition = Field(
        ..., description="Position du filigrane."
    )
    applied: bool = Field(
        ..., description="True si le filigrane a été appliqué (False si skip)."
    )
    skipped_reason: str | None = Field(
        default=None,
        description="Raison du skip si non appliqué (image trop petite, etc.).",
    )
    duration_seconds: float = Field(..., ge=0.0, description="Durée d'application.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class WatermarkStats(BaseModel):
    """Statistiques agrégées de l'applicateur de filigranes."""

    total_applications: int = Field(default=0, ge=0)
    successful: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0, description="Images non filigranées (trop petites).")
    failed: int = Field(default=0, ge=0)
    total_bytes_input: int = Field(default=0, ge=0)
    total_bytes_output: int = Field(default=0, ge=0)
    by_type: dict[str, int] = Field(
        default_factory=dict,
        description="Nombre d'applications par type de filigrane.",
    )
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# HELPERS — Détection de polices
# ============================================================================


# Polices courantes à essayer dans l'ordre (par OS)
_SYSTEM_FONTS: Final[dict[str, tuple[str, ...]]] = {
    "Windows": (
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
        "C:/Windows/Fonts/verdana.ttf",
    ),
    "Darwin": (  # macOS
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ),
    "Linux": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ),
}


def find_system_font() -> Path | None:
    """Détecte une police TrueType disponible sur le système.

    Parcourt une liste de chemins courants selon l'OS et retourne le
    premier fichier de police trouvé.

    Returns:
        Chemin vers une police TTF/OTF, ou None si aucune trouvée.
    """
    system = platform.system()
    candidates = _SYSTEM_FONTS.get(system, _SYSTEM_FONTS["Linux"])

    for font_path_str in candidates:
        font_path = Path(font_path_str)
        if font_path.exists() and font_path.is_file():
            return font_path

    # Fallback : chercher dans les répertoires standards
    search_dirs: list[Path] = []
    if system == "Linux":
        search_dirs = [
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            Path.home() / ".local/share/fonts",
        ]
    elif system == "Darwin":
        search_dirs = [
            Path("/System/Library/Fonts"),
            Path("/Library/Fonts"),
            Path.home() / "Library/Fonts",
        ]
    elif system == "Windows":
        search_dirs = [Path("C:/Windows/Fonts")]

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        # Chercher les premières TTF
        for ext in ("*.ttf", "*.TTF", "*.otf", "*.OTF"):
            matches = list(search_dir.rglob(ext))[:5]
            if matches:
                return matches[0]

    return None


def load_font(
    font_path: Path | None,
    size: int,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Charge une police TrueType avec fallback sur la police par défaut.

    Args:
        font_path: Chemin vers une police TTF/OTF (None = détection auto).
        size: Taille de la police en pixels.

    Returns:
        Police chargée (FreeTypeFont si TTF disponible, sinon ImageFont par défaut).

    Raises:
        WatermarkFontNotFoundError: Si aucune police ne peut être chargée.
    """
    # 1. Police explicite fournie
    if font_path is not None:
        if not font_path.exists():
            raise WatermarkFontNotFoundError(str(font_path))
        try:
            return ImageFont.truetype(str(font_path), size)
        except (OSError, IOError) as e:
            raise WatermarkFontNotFoundError(str(font_path)) from e

    # 2. Détection automatique
    system_font = find_system_font()
    if system_font is not None:
        try:
            return ImageFont.truetype(str(system_font), size)
        except (OSError, IOError):
            pass

    # 3. Fallback sur la police par défaut de Pillow
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        # Anciennes versions de Pillow sans paramètre size
        return ImageFont.load_default()


def parse_color(color_str: str) -> tuple[int, int, int, int]:
    """Parse une couleur hexadécimale en tuple RGBA.

    Supporte les formats :
        - '#RRGGBB' → (R, G, B, 255)
        - '#RRGGBBAA' → (R, G, B, A)
        - 'RRGGBB' → (R, G, B, 255)
        - 'RRGGBBAA' → (R, G, B, A)

    Args:
        color_str: Couleur au format hexadécimal.

    Returns:
        Tuple (R, G, B, A) avec valeurs 0-255.

    Raises:
        WatermarkConfigError: Si le format est invalide.
    """
    color = color_str.strip().lstrip("#")

    if len(color) == 6:
        try:
            r = int(color[0:2], 16)
            g = int(color[2:4], 16)
            b = int(color[4:6], 16)
            return (r, g, b, 255)
        except ValueError as e:
            raise WatermarkConfigError(f"Couleur invalide: {color_str}") from e

    if len(color) == 8:
        try:
            r = int(color[0:2], 16)
            g = int(color[2:4], 16)
            b = int(color[4:6], 16)
            a = int(color[6:8], 16)
            return (r, g, b, a)
        except ValueError as e:
            raise WatermarkConfigError(f"Couleur invalide: {color_str}") from e

    raise WatermarkConfigError(
        f"Format de couleur non supporté: {color_str} (attendu: #RRGGBB ou #RRGGBBAA)"
    )


def compute_text_position(
    image_size: tuple[int, int],
    text_size: tuple[int, int],
    position: WatermarkPosition,
    padding: int,
) -> tuple[int, int]:
    """Calcule la position (x, y) du texte selon la position demandée.

    Args:
        image_size: Dimensions de l'image (width, height).
        text_size: Dimensions du texte (width, height).
        position: Position demandée.
        padding: Marge depuis les bords.

    Returns:
        Tuple (x, y) en pixels.
    """
    img_w, img_h = image_size
    txt_w, txt_h = text_size

    if position == WatermarkPosition.TOP_LEFT:
        return (padding, padding)

    if position == WatermarkPosition.TOP_CENTER:
        return ((img_w - txt_w) // 2, padding)

    if position == WatermarkPosition.TOP_RIGHT:
        return (img_w - txt_w - padding, padding)

    if position == WatermarkPosition.CENTER_LEFT:
        return (padding, (img_h - txt_h) // 2)

    if position == WatermarkPosition.CENTER:
        return ((img_w - txt_w) // 2, (img_h - txt_h) // 2)

    if position == WatermarkPosition.CENTER_RIGHT:
        return (img_w - txt_w - padding, (img_h - txt_h) // 2)

    if position == WatermarkPosition.BOTTOM_LEFT:
        return (padding, img_h - txt_h - padding)

    if position == WatermarkPosition.BOTTOM_CENTER:
        return ((img_w - txt_w) // 2, img_h - txt_h - padding)

    if position == WatermarkPosition.BOTTOM_RIGHT:
        return (img_w - txt_w - padding, img_h - txt_h - padding)

    # TILE : pas de position unique
    return (padding, padding)


# ============================================================================
# CLASSE PRINCIPALE — ImageWatermarker
# ============================================================================


class ImageWatermarker:
    """Applicateur de filigranes asynchrone multi-modes.

    Supporte trois types de filigranes :
        - TEXT : texte avec police, couleur, opacité, rotation
        - IMAGE : logo PNG avec transparence
        - TILE : mosaïque répétée pour protection anti-vol

    Lifecycle :
        >>> watermarker = ImageWatermarker()
        >>> await watermarker.start()
        >>> # ... applications ...
        >>> await watermarker.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Les opérations Pillow sont exécutées via `asyncio.to_thread()`.
        Un sémaphore limite la concurrence.
    """

    # Constantes
    _DEFAULT_MAX_CONCURRENT: Final[int] = 4
    _AUTO_FONT_SIZE_RATIO: Final[float] = 0.03  # 3% de la largeur
    _MIN_FONT_SIZE: Final[int] = 12
    _MAX_FONT_SIZE: Final[int] = 200

    def __init__(
        self,
        *,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
    ) -> None:
        """Initialise l'applicateur de filigranes.

        Args:
            max_concurrent: Nombre maximum d'applications simultanées.
        """
        if max_concurrent <= 0:
            raise ValueError(f"max_concurrent must be positive, got {max_concurrent}")

        self._max_concurrent = max_concurrent

        self._semaphore: asyncio.Semaphore | None = None
        self._started: bool = False
        self._start_time: float = 0.0

        # Cache des polices chargées (par taille)
        self._font_cache: dict[tuple[str | None, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

        # Statistiques
        self._total_applications: int = 0
        self._successful: int = 0
        self._skipped: int = 0
        self._failed: int = 0
        self._total_bytes_input: int = 0
        self._total_bytes_output: int = 0
        self._by_type: dict[str, int] = {}
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="image_watermarker")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre l'applicateur et initialise les ressources."""
        if self._started:
            self._logger.warning("ImageWatermarker déjà démarré, ignore")
            return

        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._started = True
        self._start_time = asyncio.get_event_loop().time()

        # Précharger une police par défaut
        try:
            await asyncio.to_thread(self._get_font, None, 24)
        except Exception as e:
            self._logger.warning("Impossible de précharger une police: {}", e)

        self._logger.info(
            "ImageWatermarker démarré: max_concurrent={}",
            self._max_concurrent,
        )

    async def stop(self) -> None:
        """Arrête l'applicateur et libère les ressources."""
        if not self._started:
            return

        self._semaphore = None
        self._font_cache.clear()
        self._started = False
        self._logger.info("ImageWatermarker arrêté")

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
        """Indique si l'applicateur est démarré."""
        return self._started

    @property
    def max_concurrent(self) -> int:
        """Nombre maximum d'applications simultanées."""
        return self._max_concurrent

    # ------------------------------------------------------------------------
    # API publique — Application simple
    # ------------------------------------------------------------------------

    async def apply(
        self,
        source: Path,
        dest: Path,
        *,
        config: WatermarkConfig | None = None,
    ) -> WatermarkResult:
        """Applique un filigrane sur une image source.

        Args:
            source: Chemin de l'image source.
            dest: Chemin de destination (même format que source par défaut).
            config: Configuration du filigrane (défaut: texte "NexusDL" en bas à droite).

        Returns:
            Résultat de l'application avec métadonnées complètes.

        Raises:
            WatermarkError: Si l'application échoue.
            WatermarkConfigError: Si la configuration est invalide.
            WatermarkImageNotFoundError: Si l'image de filigrane est introuvable.
        """
        self._ensure_started()
        assert self._semaphore is not None

        effective_config = config or WatermarkConfig()
        effective_config.validate_config()

        async with self._semaphore:
            start_time = asyncio.get_event_loop().time()

            # 1. Détecter le format source
            source_format = detect_format(source)
            source_size = source.stat().st_size

            # 2. Appliquer le filigrane
            try:
                result = await self._do_apply(
                    source=source,
                    dest=dest,
                    source_format=source_format,
                    config=effective_config,
                    start_time=start_time,
                )

                async with self._stats_lock:
                    self._successful += 1
                    self._total_applications += 1
                    self._total_bytes_input += source_size
                    self._total_bytes_output += result.output_size_bytes
                    self._by_type[effective_config.type.value] = (
                        self._by_type.get(effective_config.type.value, 0) + 1
                    )

                return result

            except WatermarkError:
                async with self._stats_lock:
                    self._failed += 1
                    self._total_applications += 1
                raise

            except Exception as e:
                async with self._stats_lock:
                    self._failed += 1
                    self._total_applications += 1
                self._logger.error("Échec de l'application du filigrane sur {}: {}", source, e)
                raise WatermarkApplicationError(source, str(e)) from e

    # ------------------------------------------------------------------------
    # API publique — Application par lot
    # ------------------------------------------------------------------------

    async def apply_batch(
        self,
        sources: Sequence[Path],
        dest_dir: Path,
        *,
        config: WatermarkConfig | None = None,
        suffix: str = "_wm",
        on_progress: Any | None = None,
    ) -> list[WatermarkResult]:
        """Applique un filigrane sur un lot d'images.

        Args:
            sources: Liste des chemins sources.
            dest_dir: Répertoire de destination.
            config: Configuration du filigrane.
            suffix: Suffixe à ajouter aux noms de fichiers.
            on_progress: Callback appelé après chaque application.

        Returns:
            Liste des résultats d'application.
        """
        self._ensure_started()

        if not sources:
            return []

        await asyncio.to_thread(dest_dir.mkdir, parents=True, exist_ok=True)

        effective_config = config or WatermarkConfig()

        # Préparer les tâches
        tasks: list[asyncio.Task[WatermarkResult]] = []
        for source in sources:
            dest = dest_dir / f"{source.stem}{suffix}{source.suffix}"
            task = asyncio.create_task(
                self.apply(source, dest, config=effective_config),
                name=f"watermark_{source.name}",
            )
            tasks.append(task)

        # Exécuter
        results: list[WatermarkResult] = []
        for task in tasks:
            try:
                result = await task
                results.append(result)
                if on_progress is not None:
                    on_progress(result)
            except Exception as e:
                self._logger.warning("Application de filigrane échouée dans le lot: {}", e)

        self._logger.info(
            "Lot terminé: {}/{} filigranes appliqués",
            sum(1 for r in results if r.applied),
            len(sources),
        )
        return results

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> WatermarkStats:
        """Retourne les statistiques agrégées de l'applicateur."""
        async with self._stats_lock:
            uptime = 0.0
            if self._start_time > 0:
                uptime = asyncio.get_event_loop().time() - self._start_time

            return WatermarkStats(
                total_applications=self._total_applications,
                successful=self._successful,
                skipped=self._skipped,
                failed=self._failed,
                total_bytes_input=self._total_bytes_input,
                total_bytes_output=self._total_bytes_output,
                by_type=dict(self._by_type),
                uptime_seconds=uptime,
            )

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        async with self._stats_lock:
            self._total_applications = 0
            self._successful = 0
            self._skipped = 0
            self._failed = 0
            self._total_bytes_input = 0
            self._total_bytes_output = 0
            self._by_type.clear()
            self._start_time = asyncio.get_event_loop().time()

    # ------------------------------------------------------------------------
    # Méthodes internes — Application
    # ------------------------------------------------------------------------

    async def _do_apply(
        self,
        source: Path,
        dest: Path,
        source_format: ImageFormat,
        config: WatermarkConfig,
        start_time: float,
    ) -> WatermarkResult:
        """Effectue l'application effective du filigrane."""
        # Exécuter dans un thread
        width, height, output_size, applied, skipped_reason = await asyncio.to_thread(
            self._apply_watermark_sync,
            source,
            dest,
            source_format,
            config,
        )

        duration = asyncio.get_event_loop().time() - start_time

        if not applied and skipped_reason:
            async with self._stats_lock:
                self._skipped += 1

        return WatermarkResult(
            source_path=source,
            output_path=dest,
            source_format=source_format,
            output_format=source_format,
            source_size_bytes=source.stat().st_size,
            output_size_bytes=output_size,
            width=width,
            height=height,
            watermark_type=config.type,
            watermark_position=config.position,
            applied=applied,
            skipped_reason=skipped_reason,
            duration_seconds=duration,
        )

    def _apply_watermark_sync(
        self,
        source: Path,
        dest: Path,
        source_format: ImageFormat,
        config: WatermarkConfig,
    ) -> tuple[int, int, int, bool, str | None]:
        """Applique le filigrane de manière synchrone (dans un thread).

        Returns:
            Tuple (width, height, output_size, applied, skipped_reason).
        """
        with Image.open(source) as img:
            width, height = img.size

            # Vérifier si l'image est trop petite
            if config.skip_small_images:
                if (
                    width < config.small_image_threshold
                    or height < config.small_image_threshold
                ):
                    # Copier le fichier tel quel
                    import shutil
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)
                    return width, height, dest.stat().st_size, False, (
                        f"Image trop petite: {width}x{height}"
                    )

            # Convertir en RGBA pour supporter la transparence
            if img.mode != "RGBA":
                img = img.convert("RGBA")

            # Créer un calque transparent pour le filigrane
            watermark_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))

            # Appliquer selon le type
            if config.type == WatermarkType.TEXT:
                self._apply_text_watermark(watermark_layer, config)
            elif config.type == WatermarkType.IMAGE:
                self._apply_image_watermark(watermark_layer, img.size, config)
            elif config.type == WatermarkType.TILE:
                self._apply_tile_watermark(watermark_layer, config)

            # Composer le filigrane sur l'image
            img = Image.alpha_composite(img, watermark_layer)

            # Convertir selon le format de sortie
            output_img = self._convert_for_format(img, source_format)

            # Sauvegarder
            dest.parent.mkdir(parents=True, exist_ok=True)
            save_kwargs = self._get_save_kwargs(source_format)
            output_img.save(dest, **save_kwargs)

            return width, height, dest.stat().st_size, True, None

    def _apply_text_watermark(
        self,
        layer: Image.Image,
        config: WatermarkConfig,
    ) -> None:
        """Applique un filigrane texte sur le calque."""
        draw = ImageDraw.Draw(layer)

        # Calculer la taille de police
        img_width = layer.size[0]
        font_size = config.font_size
        if font_size <= 0:
            font_size = max(
                self._MIN_FONT_SIZE,
                min(self._MAX_FONT_SIZE, int(img_width * self._AUTO_FONT_SIZE_RATIO)),
            )

        # Charger la police
        font = self._get_font(config.font_path, font_size)

        # Calculer la taille du texte
        bbox = draw.textbbox((0, 0), config.text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Rotation du texte si demandée
        if config.rotation != 0:
            # Créer une image temporaire pour le texte
            text_img = Image.new("RGBA", (text_width + 20, text_height + 20), (0, 0, 0, 0))
            text_draw = ImageDraw.Draw(text_img)

            # Dessiner le texte avec contour
            color = parse_color(config.font_color)
            if config.font_outline:
                outline_color = parse_color(config.font_outline_color)
                outline_width = config.font_outline_width
                for dx in range(-outline_width, outline_width + 1):
                    for dy in range(-outline_width, outline_width + 1):
                        if dx != 0 or dy != 0:
                            text_draw.text(
                                (10 + dx, 10 + dy),
                                config.text,
                                font=font,
                                fill=outline_color,
                            )
            text_draw.text((10, 10), config.text, font=font, fill=color)

            # Rotation
            text_img = text_img.rotate(
                config.rotation,
                expand=True,
                resample=Image.Resampling.BICUBIC,
            )

            # Positionner le texte tourné
            pos = compute_text_position(
                layer.size,
                text_img.size,
                config.position,
                config.padding,
            )

            # Appliquer l'opacité
            if config.opacity < 1.0:
                text_img = self._apply_opacity(text_img, config.opacity)

            layer.paste(text_img, pos, text_img)
        else:
            # Pas de rotation : dessiner directement
            pos = compute_text_position(
                layer.size,
                (text_width, text_height),
                config.position,
                config.padding,
            )

            # Couleur avec opacité
            color = parse_color(config.font_color)
            if config.opacity < 1.0:
                alpha = int(color[3] * config.opacity)
                color = (color[0], color[1], color[2], alpha)

            # Dessiner avec contour
            if config.font_outline:
                outline_color = parse_color(config.font_outline_color)
                if config.opacity < 1.0:
                    alpha = int(outline_color[3] * config.opacity)
                    outline_color = (outline_color[0], outline_color[1], outline_color[2], alpha)
                outline_width = config.font_outline_width
                for dx in range(-outline_width, outline_width + 1):
                    for dy in range(-outline_width, outline_width + 1):
                        if dx != 0 or dy != 0:
                            draw.text(
                                (pos[0] + dx, pos[1] + dy),
                                config.text,
                                font=font,
                                fill=outline_color,
                            )

            draw.text(pos, config.text, font=font, fill=color)

    def _apply_image_watermark(
        self,
        layer: Image.Image,
        image_size: tuple[int, int],
        config: WatermarkConfig,
    ) -> None:
        """Applique un filigrane image sur le calque."""
        if config.image_path is None:
            raise WatermarkConfigError("image_path est requis pour le type IMAGE")

        if not config.image_path.exists():
            raise WatermarkImageNotFoundError(config.image_path)

        with Image.open(config.image_path) as wm_img:
            # Redimensionner selon l'échelle
            target_width = int(image_size[0] * config.scale)
            ratio = target_width / wm_img.width
            target_height = int(wm_img.height * ratio)
            wm_img = wm_img.resize(
                (target_width, target_height),
                Image.Resampling.LANCZOS,
            )

            # Convertir en RGBA si nécessaire
            if wm_img.mode != "RGBA":
                wm_img = wm_img.convert("RGBA")

            # Rotation si demandée
            if config.rotation != 0:
                wm_img = wm_img.rotate(
                    config.rotation,
                    expand=True,
                    resample=Image.Resampling.BICUBIC,
                )

            # Appliquer l'opacité
            if config.opacity < 1.0:
                wm_img = self._apply_opacity(wm_img, config.opacity)

            # Positionner
            pos = compute_text_position(
                layer.size,
                wm_img.size,
                config.position,
                config.padding,
            )

            layer.paste(wm_img, pos, wm_img)

    def _apply_tile_watermark(
        self,
        layer: Image.Image,
        config: WatermarkConfig,
    ) -> None:
        """Applique un filigrane en mosaïque sur le calque."""
        # Créer une image temporaire pour une tuile
        img_width = layer.size[0]
        font_size = config.font_size
        if font_size <= 0:
            font_size = max(
                self._MIN_FONT_SIZE,
                min(self._MAX_FONT_SIZE, int(img_width * self._AUTO_FONT_SIZE_RATIO * 0.7)),
            )

        font = self._get_font(config.font_path, font_size)

        # Mesurer le texte
        temp_draw = ImageDraw.Draw(layer)
        bbox = temp_draw.textbbox((0, 0), config.text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Créer une tuile avec le texte
        tile_width = text_width + config.tile_spacing
        tile_height = text_height + config.tile_spacing
        tile = Image.new("RGBA", (tile_width, tile_height), (0, 0, 0, 0))
        tile_draw = ImageDraw.Draw(tile)

        # Couleur avec opacité
        color = parse_color(config.font_color)
        if config.opacity < 1.0:
            alpha = int(color[3] * config.opacity)
            color = (color[0], color[1], color[2], alpha)

        # Dessiner le texte au centre de la tuile
        text_x = (tile_width - text_width) // 2
        text_y = (tile_height - text_height) // 2
        tile_draw.text((text_x, text_y), config.text, font=font, fill=color)

        # Rotation de la tuile si demandée
        if config.tile_rotation != 0:
            tile = tile.rotate(
                config.tile_rotation,
                expand=True,
                resample=Image.Resampling.BICUBIC,
            )

        # Répéter la tuile sur toute l'image
        layer_width, layer_height = layer.size
        tile_w, tile_h = tile.size

        for y in range(-tile_h, layer_height + tile_h, tile_h):
            for x in range(-tile_w, layer_width + tile_w, tile_w):
                layer.paste(tile, (x, y), tile)

    def _apply_opacity(self, img: Image.Image, opacity: float) -> Image.Image:
        """Applique une opacité globale à une image RGBA.

        Args:
            img: Image RGBA.
            opacity: Opacité (0.0 à 1.0).

        Returns:
            Image avec opacité ajustée.
        """
        if img.mode != "RGBA":
            img = img.convert("RGBA")

        # Extraire le canal alpha et le multiplier
        r, g, b, a = img.split()
        # Multiplier l'alpha par l'opacité
        a = a.point(lambda x: int(x * opacity))
        return Image.merge("RGBA", (r, g, b, a))

    def _convert_for_format(
        self,
        img: Image.Image,
        output_format: ImageFormat,
    ) -> Image.Image:
        """Convertit l'image selon le format de sortie.

        JPEG ne supporte pas RGBA, donc on convertit en RGB.

        Args:
            img: Image RGBA.
            output_format: Format de sortie.

        Returns:
            Image convertue si nécessaire.
        """
        if output_format in (ImageFormat.JPEG, ImageFormat.JPG):
            if img.mode == "RGBA":
                # Créer un fond blanc et coller l'image dessus
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[3])
                return background
            if img.mode != "RGB":
                return img.convert("RGB")
        return img

    def _get_save_kwargs(self, output_format: ImageFormat) -> dict[str, Any]:
        """Construit les paramètres de sauvegarde Pillow."""
        kwargs: dict[str, Any] = {"format": output_format.pillow_format}

        if output_format in (ImageFormat.JPEG, ImageFormat.JPG):
            kwargs["quality"] = 95
            kwargs["optimize"] = True
            kwargs["progressive"] = True
        elif output_format == ImageFormat.PNG:
            kwargs["optimize"] = True
        elif output_format in (ImageFormat.WEBP, ImageFormat.WEBP_LOSSY):
            kwargs["quality"] = 90
            kwargs["method"] = 4

        return kwargs

    def _get_font(
        self,
        font_path: Path | None,
        size: int,
    ) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Récupère une police avec cache.

        Args:
            font_path: Chemin vers la police (None = auto).
            size: Taille en pixels.

        Returns:
            Police chargée.
        """
        cache_key = (str(font_path) if font_path else None, size)
        if cache_key in self._font_cache:
            return self._font_cache[cache_key]

        font = load_font(font_path, size)
        self._font_cache[cache_key] = font
        return font

    def _ensure_started(self) -> None:
        """Vérifie que l'applicateur est démarré."""
        if not self._started:
            raise WatermarkError(
                "ImageWatermarker must be started before use. Call await watermarker.start()"
            )

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<ImageWatermarker status={status} "
            f"max_concurrent={self._max_concurrent} "
            f"applications={self._total_applications}>"
        )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "WatermarkError",
    "WatermarkConfigError",
    "WatermarkImageNotFoundError",
    "WatermarkFontNotFoundError",
    "WatermarkApplicationError",
    # Enums
    "WatermarkType",
    "WatermarkPosition",
    # Modèles
    "WatermarkConfig",
    "WatermarkResult",
    "WatermarkStats",
    # Helpers
    "find_system_font",
    "load_font",
    "parse_color",
    "compute_text_position",
    # Classe principale
    "ImageWatermarker",
]
