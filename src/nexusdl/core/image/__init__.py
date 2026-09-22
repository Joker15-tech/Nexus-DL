"""Module public du traitement d'images NexusDL.

Ce module constitue le point d'entrée de la couche de traitement d'images
dans l'architecture hexagonale. Il expose l'API   publique stable utilisée
par le `DownloadWorker` et les interfaces pour télécharger, valider,
convertir, optimiser et filigraner les images scrapées depuis les sites.

Pipeline de traitement recommandé :
    1. ImageDownloader.download()   → récupère l'image avec validation
    2. ImageValidator.validate()    → vérifie l'intégrité (HTML déguisé, etc.)
    3. ImageConverter.convert()     → convertit le format si demandé (WebP, AVIF)
    4. ImageOptimizer.optimize()    → optimise la taille (9 techniques, 5 stratégies)
    5. ImageWatermarker.apply()     → ajoute un filigrane si configuré
    6. Packager.package()           → empaquette dans CBZ/CBR/PDF (core/packaging/)

Architecture :
    ImageDownloader ──► ImageValidator ──► ImageConverter ──► ImageOptimizer ──► ImageWatermarker
         │                   │                   │                   │                  │
         └───────────────────┴───────────────────┴───────────────────┴──────────────────┘
                                         ↓
                                   Pillow (CPU-bound)
                                   via asyncio.to_thread()
                                   avec sémaphore de concurrence

Règles d'or :
    1. Ce fichier n'expose QUE les symboles publics stables.
    2. Il ne dépend d'aucun module de `interfaces/` ni de `parsers/`.
    3. Toutes les opérations Pillow sont CPU-bound → exécutées dans des threads.
    4. Un sémaphore interne limite la concurrence pour éviter la surchauffe mémoire.
    5. Le format AVIF est optionnel (nécessite pillow-heif ou pillow-avif-plugin).
    6. Les HTML déguisés en images sont TOUJOURS détectés (cas pathologique courant).
    7. Le hash SHA256 est calculé pour chaque image (déduplication).

Exemple d'utilisation — Pipeline complet :
    >>> from pathlib import Path
    >>> from nexusdl.core.image import (
    ...     ImageDownloader,
    ...     ImageValidator,
    ...     ImageConverter,
    ...     ImageOptimizer,
    ...     ImageWatermarker,
    ...     DownloadConfig,
    ...     ValidationConfig,
    ...     ConversionConfig,
    ...     OptimizationConfig,
    ...     OptimizationStrategy,
    ...     WatermarkConfig,
    ...     WatermarkType,
    ...     WatermarkPosition,
    ...     ImageFormat,
    ... )
    >>>
    >>> # Initialiser tous les composants
    >>> downloader = ImageDownloader(session=session)
    >>> validator = ImageValidator()
    >>> converter = ImageConverter()
    >>> optimizer = ImageOptimizer(converter=converter)
    >>> watermarker = ImageWatermarker()
    >>>
    >>> await downloader.start()
    >>> await validator.start()
    >>> await converter.start()
    >>> await optimizer.start()
    >>> await watermarker.start()
    >>>
    >>> # 1. Télécharger
    >>> download_result = await downloader.download(
    ...     url="https://cdn.example.com/page001.jpg",
    ...     dest=Path("/tmp/pages/"),
    ...     config=DownloadConfig(compute_hash=True),
    ... )
    >>>
    >>> # 2. Valider
    >>> validation_result = await validator.validate(download_result.output_path)
    >>> if not validation_result.is_valid:
    ...     raise ValueError(f"Image invalide: {validation_result.issues}")
    >>>
    >>> # 3. Convertir en WebP
    >>> conversion_result = await converter.convert(
    ...     source=download_result.output_path,
    ...     dest=Path("/tmp/pages/page001.webp"),
    ...     config=ConversionConfig(format=ImageFormat.WEBP, quality=85),
    ... )
    >>>
    >>> # 4. Optimiser
    >>> optimization_result = await optimizer.optimize(
    ...     source=conversion_result.output_path,
    ...     dest=Path("/tmp/pages/page001_opt.webp"),
    ...     strategy=OptimizationStrategy.LOSSY,
    ... )
    >>>
    >>> # 5. Filigraner
    >>> watermark_result = await watermarker.apply(
    ...     source=optimization_result.output_path,
    ...     dest=Path("/tmp/pages/page001_final.webp"),
    ...     config=WatermarkConfig(
    ...         type=WatermarkType.TEXT,
    ...         text="NexusDL",
    ...         position=WatermarkPosition.BOTTOM_RIGHT,
    ...         opacity=0.3,
    ...     ),
    ... )
    >>>
    >>> await downloader.stop()
    >>> await validator.stop()
    >>> await converter.stop()
    >>> await optimizer.stop()
    >>> await watermarker.stop()
"""

from __future__ import annotations

# ============================================================================
# EXCEPTIONS — Hiérarchie complète
# ============================================================================

# converter.py
from nexusdl.core.image.converter import (
    ConversionFailedError,
    ImageConversionError,
    InvalidImageError,
    UnsupportedFormatError,
)
# downloader.py
from nexusdl.core.image.downloader import (
    DownloadTimeoutError,
    HtmlDisguisedAsImageError,
    ImageDownloadError,
    ImageTooLargeError,
    InvalidContentTypeError,
)
# optimizer.py
from nexusdl.core.image.optimizer import (
    GainBelowThresholdError,
    ImageOptimizationError,
    OptimizationFailedError,
)
# validator.py
from nexusdl.core.image.validator import (
    ImageUnreadableError,
    ImageValidationError,
    ValidatorNotStartedError,
)
# watermark.py
from nexusdl.core.image.watermark import (
    WatermarkApplicationError,
    WatermarkConfigError,
    WatermarkError,
    WatermarkFontNotFoundError,
    WatermarkImageNotFoundError,
)

# ============================================================================
# ENUMS — Formats, états et configurations
# ============================================================================

from nexusdl.core.image.converter import ColorMode, ImageFormat
from nexusdl.core.image.downloader import ContentTypeCategory, DownloadState
from nexusdl.core.image.optimizer import (
    OptimizationLevel,
    OptimizationStrategy,
    OptimizationTechnique,
)
from nexusdl.core.image.validator import (
    ValidationIssueType,
    ValidationLevel,
    ValidationState,
)
from nexusdl.core.image.watermark import WatermarkPosition, WatermarkType

# ============================================================================
# MODÈLES PYDANTIC — Configurations et résultats immuables
# ============================================================================

from nexusdl.core.image.converter import (
    ConversionConfig,
    ConversionResult,
    ConversionStats,
)
from nexusdl.core.image.downloader import (
    DownloadConfig,
    DownloadResult,
    DownloadStats,
)
from nexusdl.core.image.optimizer import (
    GainEstimate,
    OptimizationConfig,
    OptimizationResult,
    OptimizationStats,
)
from nexusdl.core.image.validator import (
    ValidationConfig,
    ValidationIssue,
    ValidationResult,
    ValidationStats,
)
from nexusdl.core.image.watermark import (
    WatermarkConfig,
    WatermarkResult,
    WatermarkStats,
)

# ============================================================================
# CLASSES PRINCIPALES — Traitement d'images
# ============================================================================

from nexusdl.core.image.converter import ImageConverter
from nexusdl.core.image.downloader import ImageDownloader
from nexusdl.core.image.optimizer import ImageOptimizer
from nexusdl.core.image.validator import ImageValidator
from nexusdl.core.image.watermark import ImageWatermarker

# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================

from nexusdl.core.image.converter import (
    detect_format,
    detect_format_from_content,
    detect_format_from_extension,
    is_avif_supported,
)
from nexusdl.core.image.downloader import (
    classify_content_type,
    detect_html_disguise as detect_html_disguise_from_bytes,
    extract_filename_from_url,
)
from nexusdl.core.image.validator import (
    detect_html_disguise,
    is_placeholder,
)
from nexusdl.core.image.watermark import (
    compute_text_position,
    find_system_font,
    load_font,
    parse_color,
)

# ============================================================================
# EXPORTS PUBLICS
# ============================================================================

__all__ = [
    # === Classes principales ===
    "ImageConverter",
    "ImageDownloader",
    "ImageOptimizer",
    "ImageValidator",
    "ImageWatermarker",
    # === Enums — Formats et états ===
    "ImageFormat",
    "ColorMode",
    "ContentTypeCategory",
    "DownloadState",
    "OptimizationStrategy",
    "OptimizationTechnique",
    "OptimizationLevel",
    "ValidationLevel",
    "ValidationIssueType",
    "ValidationState",
    "WatermarkType",
    "WatermarkPosition",
    # === Modèles — Configuration ===
    "ConversionConfig",
    "DownloadConfig",
    "OptimizationConfig",
    "ValidationConfig",
    "WatermarkConfig",
    # === Modèles — Résultats ===
    "ConversionResult",
    "DownloadResult",
    "OptimizationResult",
    "ValidationResult",
    "ValidationIssue",
    "WatermarkResult",
    "GainEstimate",
    # === Modèles — Statistiques ===
    "ConversionStats",
    "DownloadStats",
    "OptimizationStats",
    "ValidationStats",
    "WatermarkStats",
    # === Helpers — Détection de format ===
    "detect_format",
    "detect_format_from_content",
    "detect_format_from_extension",
    "is_avif_supported",
    # === Helpers — Téléchargement ===
    "classify_content_type",
    "detect_html_disguise_from_bytes",
    "extract_filename_from_url",
    # === Helpers — Validation ===
    "detect_html_disguise",
    "is_placeholder",
    # === Helpers — Watermark ===
    "find_system_font",
    "load_font",
    "parse_color",
    "compute_text_position",
    # === Exceptions — Converter ===
    "ImageConversionError",
    "UnsupportedFormatError",
    "InvalidImageError",
    "ConversionFailedError",
    # === Exceptions — Downloader ===
    "ImageDownloadError",
    "InvalidContentTypeError",
    "HtmlDisguisedAsImageError",
    "ImageTooLargeError",
    "DownloadTimeoutError",
    # === Exceptions — Optimizer ===
    "ImageOptimizationError",
    "OptimizationFailedError",
    "GainBelowThresholdError",
    # === Exceptions — Validator ===
    "ImageValidationError",
    "ValidatorNotStartedError",
    "ImageUnreadableError",
    # === Exceptions — Watermark ===
    "WatermarkError",
    "WatermarkConfigError",
    "WatermarkImageNotFoundError",
    "WatermarkFontNotFoundError",
    "WatermarkApplicationError",
]

__version__: str = "0.1.0"
