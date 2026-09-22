"""Optimiseur d'images intelligent multi-techniques.

Ce module fournit un optimiseur d'images asynchrone qui applique un pipeline
configurable de techniques pour réduire la taille des fichiers sans dégradation
visuelle perceptible. Il est conçu pour maximiser l'économie d'espace disque
et de bande passante lors du téléchargement de mangas/webtoons/comics.

Techniques supportées (appliquées dans l'ordre) :
    1. **strip_metadata**     : Suppression EXIF, ICC, XMP, commentaires
    2. **strip_alpha**        : Retrait du canal alpha si inutilisé
    3. **detect_grayscale**   : Conversion RGB→L si image en niveaux de gris
    4. **quantize_png**       : Réduction palette PNG (median cut, 256 couleurs)
    5. **optimize_huffman**   : Optimisation tables Huffman (JPEG/PNG)
    6. **progressive_jpeg**   : Conversion JPEG en mode progressif
    7. **smart_resize**       : Downscale uniquement (preserve aspect ratio)
    8. **convert_format**     : Conversion auto vers WebP/AVIF si gain > seuil
    9. **posterize**          : Réduction couleurs pour images simples (agressif)

Chaque technique peut être activée/désactivée individuellement via
`OptimizationConfig`. Un seuil de gain minimum (`min_gain_percent`) permet
de rejeter les optimisations qui ne rapportent pas assez.

Architecture :
    ImageOptimizer
        ├── OptimizationConfig (Pydantic — techniques activées + seuils)
        ├── OptimizationStrategy (enum — presets: NONE, LOSSLESS, LOSSY, AGGRESSIVE, AUTO)
        ├── OptimizationTechnique (enum — techniques individuelles)
        ├── OptimizationResult (Pydantic — avant/après + techniques appliquées)
        └── _OptimizationPipeline (interne — orchestre les techniques)

Les opérations Pillow étant CPU-bound, elles sont exécutées via
`asyncio.to_thread()`. Un sémaphore limite la concurrence.

Exemple d'utilisation :
    >>> optimizer = ImageOptimizer()
    >>> await optimizer.start()
    >>>
    >>> # Optimisation avec preset LOSSY (recommandé)
    >>> result = await optimizer.optimize(
    ...     source=Path("page001.png"),
    ...     dest=Path("page001_opt.webp"),
    ...     strategy=OptimizationStrategy.LOSSY,
    ... )
    >>> print(f"Gain: {result.gain_percent:.1f}% ({result.bytes_saved} bytes)")
    >>> print(f"Techniques: {[t.value for t in result.techniques_applied]}")
    ['strip_metadata', 'convert_format', 'optimize_huffman']
    >>>
    >>> # Optimisation par lot
    >>> results = await optimizer.optimize_batch(
    ...     sources=[Path("p1.jpg"), Path("p2.png")],
    ...     dest_dir=Path("optimized/"),
    ...     strategy=OptimizationStrategy.AUTO,
    ... )
    >>>
    >>> # Estimation du gain sans exécuter
    >>> estimated = await optimizer.estimate_gain(Path("page001.jpg"))
    >>> print(f"Gain estimé: {estimated.estimated_gain_percent:.1f}%")
    >>>
    >>> await optimizer.stop()
"""

from __future__ import annotations

import asyncio
import io
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Final, Self

from loguru import logger
from PIL import Image, ImageFile, ImageStat
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.image.converter import (
    ConversionConfig,
    ImageConverter,
    ImageFormat,
    detect_format,
    is_avif_supported,
)


# Autoriser le chargement d'images tronquées
ImageFile.LOAD_TRUNCATED_IMAGES = True


# ============================================================================
# EXCEPTIONS
# ============================================================================


class ImageOptimizationError(NexusDLError):
    """Exception de base pour les erreurs d'optimisation d'images."""


class OptimizationFailedError(ImageOptimizationError):
    """Exception levée lorsqu'une optimisation échoue."""

    def __init__(self, source: Path, reason: str = "") -> None:
        msg = f"Échec de l'optimisation de {source}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.source = source
        self.reason = reason


class GainBelowThresholdError(ImageOptimizationError):
    """Exception levée lorsque le gain est inférieur au seuil configuré.

    Cette exception n'est PAS une erreur : elle indique simplement que
    l'optimisation n'a pas été appliquée car le gain était insuffisant.
    Le fichier original est conservé.
    """

    def __init__(
        self,
        source: Path,
        gain_percent: float,
        threshold_percent: float,
    ) -> None:
        super().__init__(
            f"Gain insuffisant pour {source}: {gain_percent:.1f}% "
            f"(seuil: {threshold_percent:.1f}%)"
        )
        self.source = source
        self.gain_percent = gain_percent
        self.threshold_percent = threshold_percent


# ============================================================================
# ENUMS
# ============================================================================


class OptimizationStrategy(str, Enum):
    """Stratégie d'optimisation prédéfinie.

    NONE        : Aucune optimisation (passe-through).
    LOSSLESS    : Optimisations sans perte uniquement (metadata, huffman).
    LOSSY       : Optimisations avec perte légère (format conversion, quantization).
    AGGRESSIVE  : Optimisations agressives (posterize, resize, conversion AVIF).
    AUTO        : Détection automatique selon le format source et le contenu.
    """

    NONE = "none"
    LOSSLESS = "lossless"
    LOSSY = "lossy"
    AGGRESSIVE = "aggressive"
    AUTO = "auto"


class OptimizationTechnique(str, Enum):
    """Techniques d'optimisation individuelles.

    Chaque technique peut être activée/désactivée via OptimizationConfig.
    """

    STRIP_METADATA = "strip_metadata"
    STRIP_ALPHA = "strip_alpha"
    DETECT_GRAYSCALE = "detect_grayscale"
    QUANTIZE_PNG = "quantize_png"
    OPTIMIZE_HUFFMAN = "optimize_huffman"
    PROGRESSIVE_JPEG = "progressive_jpeg"
    SMART_RESIZE = "smart_resize"
    CONVERT_FORMAT = "convert_format"
    POSTERIZE = "posterize"


class OptimizationLevel(str, Enum):
    """Niveau de qualité pour les optimisations lossy.

    LOW       : Compression maximale, qualité réduite (gain ~60-80%).
    MEDIUM    : Compromis qualité/taille (gain ~40-60%).
    HIGH      : Haute qualité, compression légère (gain ~20-40%).
    ULTRA     : Qualité maximale, compression minimale (gain ~5-20%).
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    ULTRA = "ultra"


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class OptimizationConfig(BaseModel):
    """Configuration complète d'une optimisation d'image.

    Tous les champs sont optionnels avec des valeurs par défaut raisonnables.
    Le modèle est immuable (`frozen=True`) pour garantir la cohérence.
    """

    strategy: OptimizationStrategy = Field(
        default=OptimizationStrategy.AUTO,
        description="Stratégie d'optimisation globale.",
    )
    level: OptimizationLevel = Field(
        default=OptimizationLevel.HIGH,
        description="Niveau de qualité pour les optimisations lossy.",
    )

    # Techniques individuelles (activées/désactivées)
    strip_metadata: bool = Field(
        default=True,
        description="Supprimer EXIF, ICC, XMP, commentaires.",
    )
    strip_alpha: bool = Field(
        default=True,
        description="Retirer le canal alpha si inutilisé.",
    )
    detect_grayscale: bool = Field(
        default=True,
        description="Convertir RGB→L si image en niveaux de gris.",
    )
    quantize_png: bool = Field(
        default=True,
        description="Réduire la palette PNG à 256 couleurs.",
    )
    optimize_huffman: bool = Field(
        default=True,
        description="Optimiser les tables Huffman (JPEG/PNG).",
    )
    progressive_jpeg: bool = Field(
        default=True,
        description="Convertir JPEG en mode progressif.",
    )
    smart_resize: bool = Field(
        default=False,
        description="Redimensionner si l'image dépasse max_width/max_height.",
    )
    convert_format: bool = Field(
        default=True,
        description="Convertir automatiquement vers WebP/AVIF si gain > seuil.",
    )
    posterize: bool = Field(
        default=False,
        description="Réduire les couleurs (agressif, pour images simples).",
    )

    # Paramètres de conversion
    target_format: ImageFormat | None = Field(
        default=None,
        description="Format cible forcé (None = auto).",
    )
    quality: int = Field(
        default=85,
        ge=1,
        le=100,
        description="Qualité pour les formats avec perte.",
    )

    # Paramètres de redimensionnement
    max_width: int = Field(
        default=0,
        ge=0,
        description="Largeur maximale (0 = illimité).",
    )
    max_height: int = Field(
        default=0,
        ge=0,
        description="Hauteur maximale (0 = illimité).",
    )

    # Seuils
    min_gain_percent: float = Field(
        default=5.0,
        ge=0.0,
        le=100.0,
        description="Gain minimum (%) pour accepter l'optimisation.",
    )
    max_gain_format_switch_percent: float = Field(
        default=10.0,
        ge=0.0,
        description="Gain minimum (%) pour changer de format (ex: PNG→WebP).",
    )

    # Posterization
    posterize_bits: int = Field(
        default=4,
        ge=1,
        le=8,
        description="Nombre de bits par canal pour posterize (1=2 couleurs, 8=256).",
    )

    # Quantization
    quantize_colors: int = Field(
        default=256,
        ge=2,
        le=256,
        description="Nombre de couleurs pour la quantization PNG.",
    )

    # Grayscale detection
    grayscale_threshold: float = Field(
        default=0.01,
        ge=0.0,
        le=1.0,
        description="Seuil de variance pour détecter les niveaux de gris (0.01 = strict).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid", validate_assignment=True)

    def with_strategy(self, strategy: OptimizationStrategy) -> OptimizationConfig:
        """Retourne une nouvelle config avec les paramètres du preset donné.

        Args:
            strategy: Stratégie à appliquer.

        Returns:
            Nouvelle config avec les techniques activées/désactivées selon le preset.
        """
        if strategy == OptimizationStrategy.NONE:
            return self.model_copy(
                update={
                    "strip_metadata": False,
                    "strip_alpha": False,
                    "detect_grayscale": False,
                    "quantize_png": False,
                    "optimize_huffman": False,
                    "progressive_jpeg": False,
                    "smart_resize": False,
                    "convert_format": False,
                    "posterize": False,
                }
            )

        if strategy == OptimizationStrategy.LOSSLESS:
            return self.model_copy(
                update={
                    "strip_metadata": True,
                    "strip_alpha": True,
                    "detect_grayscale": True,
                    "quantize_png": False,
                    "optimize_huffman": True,
                    "progressive_jpeg": False,
                    "smart_resize": False,
                    "convert_format": False,
                    "posterize": False,
                }
            )

        if strategy == OptimizationStrategy.LOSSY:
            return self.model_copy(
                update={
                    "strip_metadata": True,
                    "strip_alpha": True,
                    "detect_grayscale": True,
                    "quantize_png": True,
                    "optimize_huffman": True,
                    "progressive_jpeg": True,
                    "smart_resize": False,
                    "convert_format": True,
                    "posterize": False,
                }
            )

        if strategy == OptimizationStrategy.AGGRESSIVE:
            return self.model_copy(
                update={
                    "strip_metadata": True,
                    "strip_alpha": True,
                    "detect_grayscale": True,
                    "quantize_png": True,
                    "optimize_huffman": True,
                    "progressive_jpeg": True,
                    "smart_resize": True,
                    "convert_format": True,
                    "posterize": True,
                }
            )

        # AUTO : comportement par défaut ( LOSSY )
        return self


class OptimizationResult(BaseModel):
    """Résultat immuable d'une optimisation d'image."""

    source_path: Path = Field(..., description="Chemin de l'image source.")
    output_path: Path = Field(..., description="Chemin de l'image optimisée.")
    source_format: ImageFormat = Field(..., description="Format source.")
    output_format: ImageFormat = Field(..., description="Format de sortie.")
    source_size_bytes: int = Field(..., ge=0, description="Taille source en bytes.")
    output_size_bytes: int = Field(..., ge=0, description="Taille optimisée en bytes.")
    width: int = Field(..., ge=0, description="Largeur de sortie.")
    height: int = Field(..., ge=0, description="Hauteur de sortie.")
    techniques_applied: list[OptimizationTechnique] = Field(
        default_factory=list,
        description="Techniques effectivement appliquées.",
    )
    duration_seconds: float = Field(..., ge=0.0, description="Durée d'optimisation.")
    optimized: bool = Field(
        ...,
        description="True si l'image a été modifiée (taille réduite).",
    )
    skipped_reason: str | None = Field(
        default=None,
        description="Raison du skip si non optimisée (gain insuffisant, etc.).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def bytes_saved(self) -> int:
        """Nombre de bytes économisés."""
        return self.source_size_bytes - self.output_size_bytes

    @property
    def gain_percent(self) -> float:
        """Pourcentage de gain (0.0 à 100.0, peut être négatif)."""
        if self.source_size_bytes == 0:
            return 0.0
        return (self.bytes_saved / self.source_size_bytes) * 100.0

    @property
    def compression_ratio(self) -> float:
        """Ratio de compression (0.0 à 1.0)."""
        if self.source_size_bytes == 0:
            return 0.0
        return max(0.0, min(1.0, self.bytes_saved / self.source_size_bytes))


class GainEstimate(BaseModel):
    """Estimation du gain potentiel d'optimisation (sans exécution)."""

    source_path: Path = Field(..., description="Chemin de l'image source.")
    source_format: ImageFormat = Field(..., description="Format source détecté.")
    source_size_bytes: int = Field(..., ge=0, description="Taille source.")
    estimated_gain_percent: float = Field(
        ...,
        ge=0.0,
        description="Gain estimé en pourcentage.",
    )
    estimated_output_size_bytes: int = Field(
        ...,
        ge=0,
        description="Taille estimée après optimisation.",
    )
    recommended_techniques: list[OptimizationTechnique] = Field(
        default_factory=list,
        description="Techniques recommandées pour cette image.",
    )
    recommended_format: ImageFormat | None = Field(
        default=None,
        description="Format cible recommandé (si conversion avantageuse).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class OptimizationStats(BaseModel):
    """Statistiques agrégées de l'optimiseur."""

    total_optimizations: int = Field(default=0, ge=0)
    successful: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0, description="Images non optimisées (gain insuffisant).")
    failed: int = Field(default=0, ge=0)
    total_bytes_input: int = Field(default=0, ge=0)
    total_bytes_output: int = Field(default=0, ge=0)
    total_bytes_saved: int = Field(default=0, ge=0)
    techniques_usage: dict[str, int] = Field(
        default_factory=dict,
        description="Nombre d'applications par technique.",
    )
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def average_gain_percent(self) -> float:
        """Gain moyen en pourcentage."""
        if self.total_bytes_input == 0:
            return 0.0
        return ((self.total_bytes_input - self.total_bytes_output) / self.total_bytes_input) * 100.0


# ============================================================================
# HELPERS — Détection et analyse
# ============================================================================


def _is_grayscale(img: Image.Image, threshold: float = 0.01) -> bool:
    """Détecte si une image est en niveaux de gris.

    Compare les canaux R, G, B pixel par pixel. Si la variance est
    inférieure au seuil, l'image est considérée comme niveaux de gris.

    Args:
        img: Image à analyser (doit être en mode RGB ou RGBA).
        threshold: Seuil de variance (0.0 = parfait, 0.01 = tolérant).

    Returns:
        True si l'image est en niveaux de gris.
    """
    if img.mode not in ("RGB", "RGBA"):
        return img.mode in ("L", "LA")

    # Convertir en RGB si RGBA
    if img.mode == "RGBA":
        img = img.convert("RGB")

    # Séparer les canaux
    r, g, b = img.split()

    # Calculer la différence moyenne entre canaux
    # Utiliser ImageStat pour des stats rapides
    stat_r = ImageStat.Stat(r)
    stat_g = ImageStat.Stat(g)
    stat_b = ImageStat.Stat(b)

    # Si les moyennes sont très proches, c'est du niveau de gris
    avg_diff = (
        abs(stat_r.mean[0] - stat_g.mean[0])
        + abs(stat_g.mean[0] - stat_b.mean[0])
        + abs(stat_r.mean[0] - stat_b.mean[0])
    ) / 3.0

    # Normaliser par 255
    normalized_diff = avg_diff / 255.0

    return normalized_diff < threshold


def _has_visible_alpha(img: Image.Image) -> bool:
    """Détecte si une image a un canal alpha utilisé (transparence visible).

    Args:
        img: Image à analyser.

    Returns:
        True si le canal alpha contient des valeurs autres que 255 (opaque).
    """
    if img.mode not in ("RGBA", "LA", "PA"):
        return False

    # Extraire le canal alpha
    if img.mode == "RGBA":
        alpha = img.split()[3]
    elif img.mode == "LA":
        alpha = img.split()[1]
    else:  # PA
        alpha = img.convert("RGBA").split()[3]

    # Vérifier si tous les pixels sont à 255 (opaque)
    stat = ImageStat.Stat(alpha)
    # Si le min == max == 255, pas de transparence
    return stat.extrema[0] != 255


def _count_unique_colors(img: Image.Image, sample_size: int = 10000) -> int:
    """Estime le nombre de couleurs uniques dans une image.

    Échantillonne un sous-ensemble de pixels pour une estimation rapide.

    Args:
        img: Image à analyser.
        sample_size: Nombre de pixels à échantillonner.

    Returns:
        Nombre estimé de couleurs uniques.
    """
    # Réduire l'image pour l'échantillonnage
    width, height = img.size
    total_pixels = width * height

    if total_pixels <= sample_size:
        # Image petite, compter toutes les couleurs
        return len(img.getcolors(maxcolors=total_pixels) or [])

    # Redimensionner pour échantillonner
    ratio = (sample_size / total_pixels) ** 0.5
    new_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
    small = img.resize(new_size, Image.Resampling.BILINEAR)

    colors = small.getcolors(maxcolors=sample_size * 2)
    return len(colors) if colors else sample_size


# ============================================================================
# PIPELINE D'OPTIMISATION (interne)
# ============================================================================


class _OptimizationPipeline:
    """Pipeline interne orchestrant les techniques d'optimisation.

    Non exposé publiquement — utilisé par ImageOptimizer.
    """

    def __init__(self, config: OptimizationConfig) -> None:
        self._config = config
        self._applied: list[OptimizationTechnique] = []

    @property
    def applied_techniques(self) -> list[OptimizationTechnique]:
        """Techniques effectivement appliquées."""
        return list(self._applied)

    def run(self, img: Image.Image, source_format: ImageFormat) -> Image.Image:
        """Exécute le pipeline complet sur une image.

        Args:
            img: Image à optimiser.
            source_format: Format source de l'image.

        Returns:
            Image optimisée (peut être le même objet si aucune technique appliquée).
        """
        current = img

        # 1. Strip metadata
        if self._config.strip_metadata:
            current = self._strip_metadata(current)

        # 2. Strip alpha si inutilisé
        if self._config.strip_alpha and current.mode in ("RGBA", "LA", "PA"):
            if not _has_visible_alpha(current):
                current = self._strip_alpha(current)

        # 3. Detect grayscale
        if self._config.detect_grayscale and current.mode in ("RGB", "RGBA"):
            if _is_grayscale(current, self._config.grayscale_threshold):
                current = self._convert_to_grayscale(current)

        # 4. Smart resize
        if self._config.smart_resize:
            if self._config.max_width > 0 or self._config.max_height > 0:
                current = self._smart_resize(current)

        # 5. Posterize (agressif)
        if self._config.posterize and current.mode in ("RGB", "L"):
            current = self._posterize(current)

        # 6. Quantize PNG
        if self._config.quantize_png and source_format == ImageFormat.PNG:
            if current.mode in ("RGB", "RGBA"):
                current = self._quantize_png(current)

        # 7. Optimize Huffman (appliqué à la sauvegarde via save_kwargs)
        # Pas de transformation d'image, juste un flag

        # 8. Progressive JPEG (appliqué à la sauvegarde)
        # Pas de transformation d'image, juste un flag

        return current

    def get_save_kwargs(self, output_format: ImageFormat) -> dict[str, Any]:
        """Construit les paramètres de sauvegarde Pillow.

        Args:
            output_format: Format de sortie.

        Returns:
            Dictionnaire de paramètres pour img.save().
        """
        kwargs: dict[str, Any] = {"format": output_format.pillow_format}

        # Qualité pour formats lossy
        if output_format.is_lossy:
            kwargs["quality"] = self._config.quality

        # Huffman optimization
        if self._config.optimize_huffman:
            kwargs["optimize"] = True

        # Progressive JPEG
        if (
            self._config.progressive_jpeg
            and output_format in (ImageFormat.JPEG, ImageFormat.JPG)
        ):
            kwargs["progressive"] = True
            kwargs["subsampling"] = "4:2:0"

        # PNG specific
        if output_format == ImageFormat.PNG:
            kwargs["compress_level"] = 9 if self._config.optimize_huffman else 6

        # WebP specific
        if output_format in (ImageFormat.WEBP, ImageFormat.WEBP_LOSSY):
            kwargs["method"] = 6 if self._config.optimize_huffman else 4

        return kwargs

    # --- Techniques individuelles ---

    def _strip_metadata(self, img: Image.Image) -> Image.Image:
        """Supprime toutes les métadonnées (EXIF, ICC, XMP, commentaires)."""
        # Créer une nouvelle image sans métadonnées
        data = list(img.getdata())
        clean = Image.new(img.mode, img.size)
        clean.putdata(data)
        self._applied.append(OptimizationTechnique.STRIP_METADATA)
        return clean

    def _strip_alpha(self, img: Image.Image) -> Image.Image:
        """Retire le canal alpha (convertit RGBA→RGB, LA→L, PA→P)."""
        if img.mode == "RGBA":
            result = img.convert("RGB")
        elif img.mode == "LA":
            result = img.convert("L")
        elif img.mode == "PA":
            result = img.convert("P")
        else:
            result = img
        self._applied.append(OptimizationTechnique.STRIP_ALPHA)
        return result

    def _convert_to_grayscale(self, img: Image.Image) -> Image.Image:
        """Convertit une image RGB/RGBA en niveaux de gris."""
        if img.mode == "RGBA":
            # Préserver l'alpha si présent
            alpha = img.split()[3]
            gray = img.convert("L")
            result = Image.merge("LA", (gray, alpha))
        else:
            result = img.convert("L")
        self._applied.append(OptimizationTechnique.DETECT_GRAYSCALE)
        return result

    def _smart_resize(self, img: Image.Image) -> Image.Image:
        """Redimensionne l'image si elle dépasse les limites (downscale only)."""
        width, height = img.size
        new_width, new_height = width, height

        # Limiter par la largeur
        if self._config.max_width > 0 and width > self._config.max_width:
            ratio = self._config.max_width / width
            new_width = self._config.max_width
            new_height = int(height * ratio)

        # Limiter par la hauteur
        if self._config.max_height > 0 and new_height > self._config.max_height:
            ratio = self._config.max_height / new_height
            new_height = self._config.max_height
            new_width = int(new_width * ratio)

        if (new_width, new_height) != (width, height):
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            self._applied.append(OptimizationTechnique.SMART_RESIZE)

        return img

    def _posterize(self, img: Image.Image) -> Image.Image:
        """Réduit le nombre de couleurs par canal (posterize)."""
        try:
            result = img.posterize(self._config.posterize_bits)
            self._applied.append(OptimizationTechnique.POSTERIZE)
            return result
        except Exception:
            return img

    def _quantize_png(self, img: Image.Image) -> Image.Image:
        """Réduit la palette PNG à N couleurs (median cut)."""
        # Ne quantizer que si l'image a beaucoup de couleurs
        unique_colors = _count_unique_colors(img)
        if unique_colors <= self._config.quantize_colors:
            return img

        try:
            if img.mode == "RGBA":
                # Quantize avec alpha
                result = img.quantize(
                    colors=self._config.quantize_colors,
                    method=Image.Quantize.MEDIANCUT,
                )
            else:
                result = img.quantize(
                    colors=self._config.quantize_colors,
                    method=Image.Quantize.MEDIANCUT,
                )
            self._applied.append(OptimizationTechnique.QUANTIZE_PNG)
            return result
        except Exception:
            return img


# ============================================================================
# CLASSE PRINCIPALE — ImageOptimizer
# ============================================================================


class ImageOptimizer:
    """Optimiseur d'images asynchrone multi-techniques.

    Applique un pipeline configurable de techniques pour réduire la taille
    des fichiers sans dégradation visuelle perceptible. Supporte la conversion
    automatique vers des formats plus efficaces (WebP, AVIF) si le gain
    dépasse un seuil configurable.

    Lifecycle :
        >>> optimizer = ImageOptimizer()
        >>> await optimizer.start()
        >>> # ... optimisations ...
        >>> await optimizer.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Les opérations Pillow sont exécutées via `asyncio.to_thread()`.
        Un sémaphore limite la concurrence.
    """

    # Constantes
    _DEFAULT_MAX_CONCURRENT: Final[int] = 4
    _MIN_FILE_SIZE_FOR_OPTIMIZATION: Final[int] = 1024  # 1 KB

    def __init__(
        self,
        *,
        converter: ImageConverter | None = None,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
    ) -> None:
        """Initialise l'optimiseur d'images.

        Args:
            converter: Convertisseur d'images pour les changements de format.
                       Si None, la conversion de format est désactivée.
            max_concurrent: Nombre maximum d'optimisations simultanées.
        """
        if max_concurrent <= 0:
            raise ValueError(f"max_concurrent must be positive, got {max_concurrent}")

        self._converter = converter
        self._max_concurrent = max_concurrent

        self._semaphore: asyncio.Semaphore | None = None
        self._started: bool = False
        self._start_time: float = 0.0

        # Statistiques
        self._total_optimizations: int = 0
        self._successful: int = 0
        self._skipped: int = 0
        self._failed: int = 0
        self._total_bytes_input: int = 0
        self._total_bytes_output: int = 0
        self._techniques_usage: dict[str, int] = {}
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="image_optimizer")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre l'optimiseur et initialise les ressources."""
        if self._started:
            self._logger.warning("ImageOptimizer déjà démarré, ignore")
            return

        self._semaphore = asyncio.Semaphore(self._max_concurrent)

        # Démarrer le converter si fourni
        if self._converter is not None and not self._converter.is_started:
            await self._converter.start()

        self._started = True
        self._start_time = asyncio.get_event_loop().time()

        self._logger.info(
            "ImageOptimizer démarré: max_concurrent={}, converter={}",
            self._max_concurrent,
            self._converter is not None,
        )

    async def stop(self) -> None:
        """Arrête l'optimiseur et libère les ressources."""
        if not self._started:
            return

        self._semaphore = None
        self._started = False

        if self._converter is not None and self._converter.is_started:
            await self._converter.stop()

        self._logger.info("ImageOptimizer arrêté")

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
        """Indique si l'optimiseur est démarré."""
        return self._started

    @property
    def max_concurrent(self) -> int:
        """Nombre maximum d'optimisations simultanées."""
        return self._max_concurrent

    # ------------------------------------------------------------------------
    # API publique — Optimisation simple
    # ------------------------------------------------------------------------

    async def optimize(
        self,
        source: Path,
        dest: Path,
        *,
        config: OptimizationConfig | None = None,
        strategy: OptimizationStrategy | None = None,
    ) -> OptimizationResult:
        """Optimise une image source vers un fichier de destination.

        Le pipeline d'optimisation est appliqué selon la stratégie choisie.
        Si le gain est inférieur au seuil configuré, le fichier source est
        copié tel quel vers la destination.

        Args:
            source: Chemin de l'image source.
            dest: Chemin de destination.
            config: Configuration d'optimisation (défaut: LOSSY).
            strategy: Stratégie à appliquer (override config.strategy).

        Returns:
            Résultat de l'optimisation avec techniques appliquées.

        Raises:
            ImageOptimizationError: Si l'optimisation échoue.
            OptimizationFailedError: Si une technique échoue.
        """
        self._ensure_started()
        assert self._semaphore is not None

        effective_config = config or OptimizationConfig()
        if strategy is not None:
            effective_config = effective_config.with_strategy(strategy)

        # Résoudre la stratégie
        if effective_config.strategy == OptimizationStrategy.AUTO:
            effective_config = effective_config.with_strategy(OptimizationStrategy.LOSSY)

        async with self._semaphore:
            start_time = asyncio.get_event_loop().time()

            # 1. Détecter le format source
            source_format = detect_format(source)
            source_size = source.stat().st_size

            # 2. Vérifier la taille minimale
            if source_size < self._MIN_FILE_SIZE_FOR_OPTIMIZATION:
                # Trop petit pour optimiser, copier tel quel
                await asyncio.to_thread(self._copy_file, source, dest)
                duration = asyncio.get_event_loop().time() - start_time

                result = OptimizationResult(
                    source_path=source,
                    output_path=dest,
                    source_format=source_format,
                    output_format=source_format,
                    source_size_bytes=source_size,
                    output_size_bytes=dest.stat().st_size,
                    width=0,
                    height=0,
                    techniques_applied=[],
                    duration_seconds=duration,
                    optimized=False,
                    skipped_reason="Fichier trop petit",
                )

                async with self._stats_lock:
                    self._skipped += 1
                    self._total_optimizations += 1
                    self._total_bytes_input += source_size
                    self._total_bytes_output += result.output_size_bytes

                return result

            # 3. Optimiser
            try:
                result = await self._do_optimize(
                    source=source,
                    dest=dest,
                    source_format=source_format,
                    config=effective_config,
                    start_time=start_time,
                )

                async with self._stats_lock:
                    self._total_optimizations += 1
                    self._total_bytes_input += source_size
                    self._total_bytes_output += result.output_size_bytes

                    if result.optimized:
                        self._successful += 1
                        # Compter les techniques
                        for tech in result.techniques_applied:
                            self._techniques_usage[tech.value] = (
                                self._techniques_usage.get(tech.value, 0) + 1
                            )
                    else:
                        self._skipped += 1

                return result

            except GainBelowThresholdError as e:
                # Gain insuffisant, copier le fichier original
                await asyncio.to_thread(self._copy_file, source, dest)
                duration = asyncio.get_event_loop().time() - start_time

                result = OptimizationResult(
                    source_path=source,
                    output_path=dest,
                    source_format=source_format,
                    output_format=source_format,
                    source_size_bytes=source_size,
                    output_size_bytes=dest.stat().st_size,
                    width=0,
                    height=0,
                    techniques_applied=[],
                    duration_seconds=duration,
                    optimized=False,
                    skipped_reason=f"Gain insuffisant: {e.gain_percent:.1f}%",
                )

                async with self._stats_lock:
                    self._skipped += 1
                    self._total_optimizations += 1
                    self._total_bytes_input += source_size
                    self._total_bytes_output += result.output_size_bytes

                return result

            except Exception as e:
                async with self._stats_lock:
                    self._failed += 1
                    self._total_optimizations += 1

                self._logger.error("Échec de l'optimisation de {}: {}", source, e)
                raise OptimizationFailedError(source, str(e)) from e

    # ------------------------------------------------------------------------
    # API publique — Optimisation par lot
    # ------------------------------------------------------------------------

    async def optimize_batch(
        self,
        sources: Sequence[Path],
        dest_dir: Path,
        *,
        config: OptimizationConfig | None = None,
        strategy: OptimizationStrategy | None = None,
        on_progress: Any | None = None,
    ) -> list[OptimizationResult]:
        """Optimise un lot d'images vers un répertoire.

        Args:
            sources: Liste des chemins sources.
            dest_dir: Répertoire de destination.
            config: Configuration d'optimisation.
            strategy: Stratégie à appliquer.
            on_progress: Callback appelé après chaque optimisation.

        Returns:
            Liste des résultats d'optimisation.
        """
        self._ensure_started()

        if not sources:
            return []

        await asyncio.to_thread(dest_dir.mkdir, parents=True, exist_ok=True)

        effective_config = config or OptimizationConfig()
        if strategy is not None:
            effective_config = effective_config.with_strategy(strategy)

        # Préparer les tâches
        tasks: list[asyncio.Task[OptimizationResult]] = []
        for source in sources:
            dest = dest_dir / source.name
            task = asyncio.create_task(
                self.optimize(source, dest, config=effective_config),
                name=f"optimize_{source.name}",
            )
            tasks.append(task)

        # Exécuter
        results: list[OptimizationResult] = []
        for task in tasks:
            try:
                result = await task
                results.append(result)
                if on_progress is not None:
                    on_progress(result)
            except Exception as e:
                self._logger.warning("Optimisation échouée dans le lot: {}", e)

        self._logger.info(
            "Lot terminé: {}/{} optimisations réussies",
            sum(1 for r in results if r.optimized),
            len(sources),
        )
        return results

    # ------------------------------------------------------------------------
    # API publique — Estimation de gain
    # ------------------------------------------------------------------------

    async def estimate_gain(
        self,
        source: Path,
        *,
        config: OptimizationConfig | None = None,
    ) -> GainEstimate:
        """Estime le gain potentiel d'optimisation sans exécuter.

        Utile pour décider si une optimisation vaut le coup avant de la lancer.

        Args:
            source: Chemin de l'image source.
            config: Configuration d'optimisation.

        Returns:
            Estimation du gain avec techniques recommandées.
        """
        self._ensure_started()

        effective_config = config or OptimizationConfig()

        # Détecter le format
        source_format = detect_format(source)
        source_size = source.stat().st_size

        # Analyser l'image
        analysis = await asyncio.to_thread(self._analyze_image, source)

        # Déterminer les techniques recommandées
        recommended: list[OptimizationTechnique] = []

        if analysis["has_metadata"]:
            recommended.append(OptimizationTechnique.STRIP_METADATA)

        if analysis["has_unused_alpha"]:
            recommended.append(OptimizationTechnique.STRIP_ALPHA)

        if analysis["is_grayscale"]:
            recommended.append(OptimizationTechnique.DETECT_GRAYSCALE)

        if source_format == ImageFormat.PNG and analysis["unique_colors"] > 256:
            recommended.append(OptimizationTechnique.QUANTIZE_PNG)

        # Estimer le format cible
        recommended_format: ImageFormat | None = None
        if effective_config.convert_format:
            if source_format in (ImageFormat.PNG, ImageFormat.JPEG):
                if is_avif_supported():
                    recommended_format = ImageFormat.AVIF
                else:
                    recommended_format = ImageFormat.WEBP
                recommended.append(OptimizationTechnique.CONVERT_FORMAT)

        # Estimer le gain (heuristique basée sur l'expérience)
        estimated_gain = 0.0
        if OptimizationTechnique.STRIP_METADATA in recommended:
            estimated_gain += 5.0
        if OptimizationTechnique.STRIP_ALPHA in recommended:
            estimated_gain += 10.0
        if OptimizationTechnique.DETECT_GRAYSCALE in recommended:
            estimated_gain += 30.0
        if OptimizationTechnique.QUANTIZE_PNG in recommended:
            estimated_gain += 20.0
        if recommended_format is not None:
            if recommended_format == ImageFormat.WEBP:
                estimated_gain += 25.0
            elif recommended_format == ImageFormat.AVIF:
                estimated_gain += 40.0

        estimated_gain = min(estimated_gain, 90.0)  # Plafond
        estimated_output_size = int(source_size * (1.0 - estimated_gain / 100.0))

        return GainEstimate(
            source_path=source,
            source_format=source_format,
            source_size_bytes=source_size,
            estimated_gain_percent=estimated_gain,
            estimated_output_size_bytes=estimated_output_size,
            recommended_techniques=recommended,
            recommended_format=recommended_format,
        )

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> OptimizationStats:
        """Retourne les statistiques agrégées de l'optimiseur."""
        async with self._stats_lock:
            uptime = 0.0
            if self._start_time > 0:
                uptime = asyncio.get_event_loop().time() - self._start_time

            return OptimizationStats(
                total_optimizations=self._total_optimizations,
                successful=self._successful,
                skipped=self._skipped,
                failed=self._failed,
                total_bytes_input=self._total_bytes_input,
                total_bytes_output=self._total_bytes_output,
                total_bytes_saved=self._total_bytes_input - self._total_bytes_output,
                techniques_usage=dict(self._techniques_usage),
                uptime_seconds=uptime,
            )

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        async with self._stats_lock:
            self._total_optimizations = 0
            self._successful = 0
            self._skipped = 0
            self._failed = 0
            self._total_bytes_input = 0
            self._total_bytes_output = 0
            self._techniques_usage.clear()
            self._start_time = asyncio.get_event_loop().time()

    # ------------------------------------------------------------------------
    # Méthodes internes — Optimisation
    # ------------------------------------------------------------------------

    async def _do_optimize(
        self,
        source: Path,
        dest: Path,
        source_format: ImageFormat,
        config: OptimizationConfig,
        start_time: float,
    ) -> OptimizationResult:
        """Effectue l'optimisation effective d'une image."""
        # Exécuter le pipeline dans un thread
        optimized_img, pipeline, width, height = await asyncio.to_thread(
            self._run_pipeline, source, config
        )

        # Déterminer le format de sortie
        output_format = source_format
        if config.convert_format and self._converter is not None:
            # Essayer WebP ou AVIF
            if is_avif_supported():
                output_format = ImageFormat.AVIF
            else:
                output_format = ImageFormat.WEBP

            # Changer l'extension
            dest = dest.with_suffix(output_format.extension)

        # Sauvegarder dans un buffer pour estimer la taille
        save_kwargs = pipeline.get_save_kwargs(output_format)

        output_data = await asyncio.to_thread(
            self._save_to_bytes, optimized_img, output_format, save_kwargs
        )
        output_size = len(output_data)

        # Vérifier le gain
        source_size = source.stat().st_size
        gain_percent = ((source_size - output_size) / source_size) * 100.0

        if gain_percent < config.min_gain_percent:
            raise GainBelowThresholdError(source, gain_percent, config.min_gain_percent)

        # Écrire le fichier
        await asyncio.to_thread(dest.write_bytes, output_data)

        duration = asyncio.get_event_loop().time() - start_time

        return OptimizationResult(
            source_path=source,
            output_path=dest,
            source_format=source_format,
            output_format=output_format,
            source_size_bytes=source_size,
            output_size_bytes=output_size,
            width=width,
            height=height,
            techniques_applied=pipeline.applied_techniques,
            duration_seconds=duration,
            optimized=True,
        )

    @staticmethod
    def _run_pipeline(
        source: Path,
        config: OptimizationConfig,
    ) -> tuple[Image.Image, _OptimizationPipeline, int, int]:
        """Exécute le pipeline d'optimisation (synchrone, dans un thread)."""
        with Image.open(source) as img:
            width, height = img.size
            source_format = detect_format(source)

            pipeline = _OptimizationPipeline(config)
            optimized = pipeline.run(img, source_format)

            return optimized, pipeline, width, height

    @staticmethod
    def _save_to_bytes(
        img: Image.Image,
        output_format: ImageFormat,
        save_kwargs: dict[str, Any],
    ) -> bytes:
        """Sauvegarde une image en mémoire."""
        buffer = io.BytesIO()
        img.save(buffer, **save_kwargs)
        return buffer.getvalue()

    @staticmethod
    def _copy_file(source: Path, dest: Path) -> None:
        """Copie un fichier."""
        import shutil
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)

    @staticmethod
    def _analyze_image(path: Path) -> dict[str, Any]:
        """Analyse une image pour l'estimation de gain."""
        with Image.open(path) as img:
            has_metadata = bool(img.info.get("exif") or img.info.get("icc_profile"))
            has_unused_alpha = img.mode in ("RGBA", "LA", "PA") and not _has_visible_alpha(img)
            is_grayscale = img.mode in ("RGB", "RGBA") and _is_grayscale(img)
            unique_colors = _count_unique_colors(img)

            return {
                "has_metadata": has_metadata,
                "has_unused_alpha": has_unused_alpha,
                "is_grayscale": is_grayscale,
                "unique_colors": unique_colors,
                "mode": img.mode,
                "size": img.size,
            }

    # ------------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # ------------------------------------------------------------------------

    def _ensure_started(self) -> None:
        """Vérifie que l'optimiseur est démarré."""
        if not self._started:
            raise ImageOptimizationError(
                "ImageOptimizer must be started before use. Call await optimizer.start()"
            )

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<ImageOptimizer status={status} "
            f"max_concurrent={self._max_concurrent} "
            f"optimizations={self._total_optimizations}>"
        )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "ImageOptimizationError",
    "OptimizationFailedError",
    "GainBelowThresholdError",
    # Enums
    "OptimizationStrategy",
    "OptimizationTechnique",
    "OptimizationLevel",
    # Modèles
    "OptimizationConfig",
    "OptimizationResult",
    "GainEstimate",
    "OptimizationStats",
    # Classe principale
    "ImageOptimizer",
]
