"""Validateur d'intégrité et de cohérence des images.

Ce module fournit un validateur d'images asynchrone robuste conçu pour
détecter les cas pathologiques fréquemment rencontrés lors du scraping
de mangas/webtoons/comics :

    - Images corrompues ou tronquées (téléchargement interrompu)
    - Fichiers HTML déguisés en images (pages d'erreur, CAPTCHA)
    - Placeholders et tracking pixels (1x1, 10x10)
    - Incohérence entre extension et contenu réel
    - Dimensions aberrantes (trop petites ou trop grandes)
    - Formats non supportés ou inattendus
    - Images avec ratio d'aspect invalide

Le validateur est utilisé en amont du convertisseur et de l'optimiseur
pour rejeter les fichiers invalides avant d'engager des opérations
coûteuses (conversion, empaquetage).

Architecture :
    ImageValidator
        ├── ValidationConfig (Pydantic — seuils et règles)
        ├── ValidationLevel (enum — NONE, BASIC, STRICT, AGGRESSIVE)
        ├── ValidationIssue (Pydantic — problème détecté)
        ├── ValidationIssueType (enum — type de problème)
        ├── ValidationResult (Pydantic — verdict + métadonnées)
        └── ValidationStats (Pydantic — statistiques agrégées)

Les opérations Pillow étant CPU-bound, elles sont exécutées via
`asyncio.to_thread()`. Un sémaphore limite la concurrence.

Exemple d'utilisation :
    >>> validator = ImageValidator()
    >>> await validator.start()
    >>>
    >>> # Validation simple
    >>> result = await validator.validate(Path("page001.jpg"))
    >>> if result.is_valid:
    ...     print(f"Image valide: {result.width}x{result.height}")
    ... else:
    ...     print(f"Problèmes: {[i.message for i in result.issues]}")
    >>>
    >>> # Validation stricte avec seuils personnalisés
    >>> config = ValidationConfig(
    ...     level=ValidationLevel.STRICT,
    ...     min_width=500,
    ...     min_height=700,
    ...     reject_html_disguise=True,
    ... )
    >>> result = await validator.validate(Path("suspect.jpg"), config=config)
    >>>
    >>> # Validation par lot
    >>> results = await validator.validate_batch([Path("p1.jpg"), Path("p2.png")])
    >>> valid = [r for r in results if r.is_valid]
    >>>
    >>> await validator.stop()
"""

from __future__ import annotations

import asyncio
import io
import re
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Final, Self

from loguru import logger
from PIL import Image, ImageFile, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.image.converter import (
    ImageFormat,
    detect_format,
    detect_format_from_content,
    detect_format_from_extension,
)


# Autoriser le chargement d'images tronquées pour la détection
ImageFile.LOAD_TRUNCATED_IMAGES = True


# ============================================================================
# EXCEPTIONS
# ============================================================================


class ImageValidationError(NexusDLError):
    """Exception de base pour les erreurs de validation d'images."""


class ValidatorNotStartedError(ImageValidationError):
    """Exception levée lorsqu'on utilise le validateur avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "ImageValidator must be started before use. Call await validator.start()"
        )


class ImageUnreadableError(ImageValidationError):
    """Exception levée lorsqu'une image ne peut être lue."""

    def __init__(self, path: Path, reason: str = "") -> None:
        msg = f"Image illisible: {path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.path = path
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class ValidationLevel(str, Enum):
    """Niveau de rigueur de la validation.

    NONE        : Aucune validation (passe-through).
    BASIC       : Validation minimale (format détectable, non-vide).
    STRICT      : Validation complète (dimensions, format, intégrité).
    AGGRESSIVE  : Validation maximale + détection HTML déguisé + ratio.
    """

    NONE = "none"
    BASIC = "basic"
    STRICT = "strict"
    AGGRESSIVE = "aggressive"


class ValidationIssueType(str, Enum):
    """Types de problèmes détectés par le validateur.

    Chaque type correspond à une règle de validation spécifique.
    """

    EMPTY_FILE = "empty_file"
    TOO_SMALL_FILE = "too_small_file"
    TOO_LARGE_FILE = "too_large_file"
    UNREADABLE = "unreadable"
    CORRUPTED = "corrupted"
    TRUNCATED = "truncated"
    FORMAT_MISMATCH = "format_mismatch"
    UNSUPPORTED_FORMAT = "unsupported_format"
    HTML_DISGUISE = "html_disguise"
    PLACEHOLDER = "placeholder"
    TOO_SMALL_DIMENSIONS = "too_small_dimensions"
    TOO_LARGE_DIMENSIONS = "too_large_dimensions"
    INVALID_ASPECT_RATIO = "invalid_aspect_ratio"
    ZERO_DIMENSIONS = "zero_dimensions"
    SUSPICIOUS_CONTENT = "suspicious_content"
    METADATA_INCONSISTENCY = "metadata_inconsistency"


class ValidationState(str, Enum):
    """État final de la validation."""

    VALID = "valid"
    INVALID = "invalid"
    WARNING = "warning"  # Valide mais avec avertissements
    UNKNOWN = "unknown"  # Impossible de déterminer


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class ValidationConfig(BaseModel):
    """Configuration complète d'une validation d'image.

    Tous les champs sont optionnels avec des valeurs par défaut raisonnables.
    Le modèle est immuable (`frozen=True`) pour garantir la cohérence.
    """

    level: ValidationLevel = Field(
        default=ValidationLevel.STRICT,
        description="Niveau de rigueur de la validation.",
    )

    # Seuils de taille de fichier
    min_file_size_bytes: int = Field(
        default=512,
        ge=0,
        description="Taille minimale du fichier (512 bytes = ~petite icône).",
    )
    max_file_size_bytes: int = Field(
        default=100 * 1024 * 1024,  # 100 Mo
        ge=0,
        description="Taille maximale du fichier (0 = illimité).",
    )

    # Seuils de dimensions
    min_width: int = Field(
        default=50,
        ge=0,
        description="Largeur minimale en pixels (50 = rejet des placeholders).",
    )
    min_height: int = Field(
        default=50,
        ge=0,
        description="Hauteur minimale en pixels.",
    )
    max_width: int = Field(
        default=10000,
        ge=0,
        description="Largeur maximale en pixels (0 = illimité).",
    )
    max_height: int = Field(
        default=10000,
        ge=0,
        description="Hauteur maximale en pixels (0 = illimité).",
    )

    # Ratio d'aspect
    min_aspect_ratio: float = Field(
        default=0.1,
        gt=0.0,
        description="Ratio largeur/hauteur minimum (0.1 = très vertical).",
    )
    max_aspect_ratio: float = Field(
        default=10.0,
        gt=0.0,
        description="Ratio largeur/hauteur maximum (10.0 = très horizontal).",
    )

    # Détections spécifiques
    reject_html_disguise: bool = Field(
        default=True,
        description="Rejeter les fichiers HTML déguisés en images.",
    )
    reject_placeholders: bool = Field(
        default=True,
        description="Rejeter les placeholders (1x1, tracking pixels, etc.).",
    )
    reject_corrupted: bool = Field(
        default=True,
        description="Rejeter les images corrompues ou illisibles.",
    )
    reject_format_mismatch: bool = Field(
        default=True,
        description="Rejeter si l'extension ne correspond pas au contenu.",
    )
    reject_zero_dimensions: bool = Field(
        default=True,
        description="Rejeter les images avec dimensions nulles.",
    )

    # Formats autorisés
    allowed_formats: set[ImageFormat] = Field(
        default_factory=lambda: {
            ImageFormat.JPEG,
            ImageFormat.JPG,
            ImageFormat.PNG,
            ImageFormat.WEBP,
            ImageFormat.AVIF,
            ImageFormat.GIF,
        },
        description="Formats d'image autorisés.",
    )

    # Tolérance aux images tronquées
    allow_truncated: bool = Field(
        default=True,
        description="Accepter les images partiellement téléchargées (LOAD_TRUNCATED_IMAGES).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid", validate_assignment=True)

    def with_level(self, level: ValidationLevel) -> ValidationConfig:
        """Retourne une nouvelle config avec les paramètres du niveau donné.

        Args:
            level: Niveau de validation à appliquer.

        Returns:
            Nouvelle config avec les règles ajustées.
        """
        if level == ValidationLevel.NONE:
            return self.model_copy(
                update={
                    "min_file_size_bytes": 0,
                    "max_file_size_bytes": 0,
                    "min_width": 0,
                    "min_height": 0,
                    "reject_html_disguise": False,
                    "reject_placeholders": False,
                    "reject_corrupted": False,
                    "reject_format_mismatch": False,
                    "reject_zero_dimensions": False,
                }
            )

        if level == ValidationLevel.BASIC:
            return self.model_copy(
                update={
                    "min_file_size_bytes": 1,
                    "reject_html_disguise": False,
                    "reject_placeholders": False,
                    "reject_corrupted": True,
                    "reject_format_mismatch": False,
                    "reject_zero_dimensions": True,
                }
            )

        if level == ValidationLevel.STRICT:
            return self.model_copy(
                update={
                    "min_file_size_bytes": 512,
                    "min_width": 50,
                    "min_height": 50,
                    "reject_html_disguise": True,
                    "reject_placeholders": True,
                    "reject_corrupted": True,
                    "reject_format_mismatch": True,
                    "reject_zero_dimensions": True,
                }
            )

        if level == ValidationLevel.AGGRESSIVE:
            return self.model_copy(
                update={
                    "min_file_size_bytes": 2048,
                    "min_width": 200,
                    "min_height": 200,
                    "reject_html_disguise": True,
                    "reject_placeholders": True,
                    "reject_corrupted": True,
                    "reject_format_mismatch": True,
                    "reject_zero_dimensions": True,
                    "min_aspect_ratio": 0.2,
                    "max_aspect_ratio": 5.0,
                }
            )

        return self


class ValidationIssue(BaseModel):
    """Problème détecté lors de la validation d'une image.

    Chaque issue a un type, un message descriptif, et un niveau de sévérité.
    """

    issue_type: ValidationIssueType = Field(
        ..., description="Type du problème détecté."
    )
    message: str = Field(..., description="Message descriptif du problème.")
    severity: str = Field(
        default="error",
        description="Sévérité: 'error' (bloquant) ou 'warning' (non-bloquant).",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Détails additionnels (dimensions, format, etc.).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class ValidationResult(BaseModel):
    """Résultat immuable de la validation d'une image.

    Contient le verdict (valide/invalide), les problèmes détectés,
    et les métadonnées de l'image (dimensions, format, taille).
    """

    path: Path = Field(..., description="Chemin de l'image validée.")
    state: ValidationState = Field(..., description="État final de la validation.")
    is_valid: bool = Field(..., description="True si l'image est valide (pas d'erreurs).")
    issues: list[ValidationIssue] = Field(
        default_factory=list,
        description="Liste des problèmes détectés (vide si valide).",
    )
    width: int = Field(default=0, ge=0, description="Largeur en pixels (0 si non lue).")
    height: int = Field(default=0, ge=0, description="Hauteur en pixels (0 si non lue).")
    file_size_bytes: int = Field(default=0, ge=0, description="Taille du fichier en bytes.")
    detected_format: ImageFormat = Field(
        ..., description="Format d'image détecté depuis le contenu."
    )
    declared_format: ImageFormat = Field(
        ..., description="Format déclaré par l'extension du fichier."
    )
    mode: str = Field(default="", description="Mode de couleur (RGB, RGBA, L, etc.).")
    duration_seconds: float = Field(..., ge=0.0, description="Durée de la validation.")
    format_consistent: bool = Field(
        ..., description="True si format déclaré == format détecté."
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def has_warnings(self) -> bool:
        """Indique si la validation a des avertissements non-bloquants."""
        return any(i.severity == "warning" for i in self.issues)

    @property
    def has_errors(self) -> bool:
        """Indique si la validation a des erreurs bloquantes."""
        return any(i.severity == "error" for i in self.issues)

    @property
    def aspect_ratio(self) -> float:
        """Ratio d'aspect (largeur / hauteur). 0.0 si dimensions invalides."""
        if self.height <= 0 or self.width <= 0:
            return 0.0
        return self.width / self.height

    @property
    def total_pixels(self) -> int:
        """Nombre total de pixels (width * height)."""
        return self.width * self.height


class ValidationStats(BaseModel):
    """Statistiques agrégées du validateur."""

    total_validations: int = Field(default=0, ge=0)
    valid: int = Field(default=0, ge=0)
    invalid: int = Field(default=0, ge=0)
    warnings: int = Field(default=0, ge=0, description="Images valides avec avertissements.")
    unreadable: int = Field(default=0, ge=0)
    total_bytes_processed: int = Field(default=0, ge=0)
    issues_by_type: dict[str, int] = Field(
        default_factory=dict,
        description="Nombre d'issues par type.",
    )
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def validation_success_rate(self) -> float:
        """Taux de succès (0.0 à 1.0)."""
        if self.total_validations == 0:
            return 0.0
        return self.valid / self.total_validations


# ============================================================================
# HELPERS — Détection de HTML déguisé
# ============================================================================


# Signatures HTML courantes
_HTML_SIGNATURES: Final[tuple[bytes, ...]] = (
    b"<!DOCTYPE html",
    b"<!doctype html",
    b"<html",
    b"<HTML",
    b"<head",
    b"<HEAD",
    b"<body",
    b"<BODY",
    b"<?xml",
)

# Patterns d'erreur courants dans les pages HTML
_ERROR_PATTERNS: Final[tuple[bytes, ...]] = (
    b"403 Forbidden",
    b"404 Not Found",
    b"503 Service Unavailable",
    b"Access Denied",
    b"CAPTCHA",
    b"cf-browser-verification",
    b"Just a moment",
    b"Enable JavaScript",
    b"Attention Required",
    b"Cloudflare",
)

# Pattern pour extraire le titre HTML
_HTML_TITLE_PATTERN: Final[re.Pattern[bytes]] = re.compile(
    rb"<title[^>]*>([^<]+)</title>", re.IGNORECASE
)

# Dimensions typiques des placeholders à rejeter
_PLACEHOLDER_DIMENSIONS: Final[frozenset[tuple[int, int]]] = frozenset(
    {
        (1, 1),
        (2, 2),
        (3, 3),
        (4, 4),
        (10, 10),
        (1, 10),
        (10, 1),
        (0, 0),
    }
)


def detect_html_disguise(data: bytes) -> tuple[bool, str]:
    """Détecte si des données binaires sont du HTML déguisé en image.

    Args:
        data: Contenu binaire à analyser (au moins 512 bytes recommandés).

    Returns:
        Tuple (is_html, reason) où reason contient le titre de la page
        ou le pattern d'erreur détecté.
    """
    if len(data) < 16:
        return False, ""

    # Vérifier les signatures HTML
    for signature in _HTML_SIGNATURES:
        if data[: len(signature)].lower() == signature.lower():
            title_match = _HTML_TITLE_PATTERN.search(data[:2048])
            reason = (
                title_match.group(1).decode("utf-8", errors="ignore").strip()
                if title_match
                else "HTML détecté"
            )
            return True, reason

    # Détecter les pages d'erreur courantes
    data_lower = data[:4096].lower()
    for pattern in _ERROR_PATTERNS:
        if pattern.lower() in data_lower:
            return True, pattern.decode("utf-8", errors="ignore")

    return False, ""


def is_placeholder(width: int, height: int) -> bool:
    """Détecte si les dimensions correspondent à un placeholder.

    Args:
        width: Largeur en pixels.
        height: Hauteur en pixels.

    Returns:
        True si les dimensions sont typiques d'un placeholder.
    """
    return (width, height) in _PLACEHOLDER_DIMENSIONS or width * height < 100


# ============================================================================
# CLASSE PRINCIPALE — ImageValidator
# ============================================================================


class ImageValidator:
    """Validateur d'intégrité d'images asynchrone.

    Détecte les cas pathologiques courants lors du scraping :
        - Images corrompues ou illisibles
        - HTML déguisé en images
        - Placeholders et tracking pixels
        - Incohérences de format
        - Dimensions aberrantes

    Lifecycle :
        >>> validator = ImageValidator()
        >>> await validator.start()
        >>> result = await validator.validate(Path("image.jpg"))
        >>> await validator.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Les opérations Pillow sont exécutées via `asyncio.to_thread()`.
        Un sémaphore limite la concurrence.
    """

    # Constantes
    _DEFAULT_MAX_CONCURRENT: Final[int] = 8
    _HTML_DETECTION_MIN_BYTES: Final[int] = 512

    def __init__(
        self,
        *,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
        default_config: ValidationConfig | None = None,
    ) -> None:
        """Initialise le validateur d'images.

        Args:
            max_concurrent: Nombre maximum de validations simultanées.
            default_config: Configuration par défaut (défaut: STRICT).
        """
        if max_concurrent <= 0:
            raise ValueError(f"max_concurrent must be positive, got {max_concurrent}")

        self._max_concurrent = max_concurrent
        self._default_config = default_config or ValidationConfig()

        self._semaphore: asyncio.Semaphore | None = None
        self._started: bool = False
        self._start_time: float = 0.0

        # Statistiques
        self._total_validations: int = 0
        self._valid: int = 0
        self._invalid: int = 0
        self._warnings: int = 0
        self._unreadable: int = 0
        self._total_bytes_processed: int = 0
        self._issues_by_type: dict[str, int] = {}
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="image_validator")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre le validateur et initialise les ressources."""
        if self._started:
            self._logger.warning("ImageValidator déjà démarré, ignore")
            return

        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._started = True
        self._start_time = asyncio.get_event_loop().time()

        self._logger.info(
            "ImageValidator démarré: max_concurrent={}, level={}",
            self._max_concurrent,
            self._default_config.level.value,
        )

    async def stop(self) -> None:
        """Arrête le validateur et libère les ressources."""
        if not self._started:
            return

        self._semaphore = None
        self._started = False
        self._logger.info("ImageValidator arrêté")

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
        """Indique si le validateur est démarré."""
        return self._started

    @property
    def max_concurrent(self) -> int:
        """Nombre maximum de validations simultanées."""
        return self._max_concurrent

    # ------------------------------------------------------------------------
    # API publique — Validation simple
    # ------------------------------------------------------------------------

    async def validate(
        self,
        path: Path,
        *,
        config: ValidationConfig | None = None,
    ) -> ValidationResult:
        """Valide l'intégrité d'une image.

        Args:
            path: Chemin de l'image à valider.
            config: Configuration de validation (défaut: config par défaut).

        Returns:
            Résultat de la validation avec verdict et problèmes détectés.

        Raises:
            ValidatorNotStartedError: Si le validateur n'est pas démarré.
            ImageUnreadableError: Si le fichier ne peut être lu.
        """
        self._ensure_started()
        assert self._semaphore is not None

        effective_config = config or self._default_config

        # Niveau NONE : passe-through
        if effective_config.level == ValidationLevel.NONE:
            return await self._passthrough(path)

        async with self._semaphore:
            start_time = asyncio.get_event_loop().time()

            # 1. Vérifier l'existence du fichier
            if not path.exists():
                return self._build_result(
                    path=path,
                    state=ValidationState.INVALID,
                    issues=[
                        ValidationIssue(
                            issue_type=ValidationIssueType.UNREADABLE,
                            message=f"Fichier inexistant: {path}",
                            severity="error",
                        )
                    ],
                    detected_format=ImageFormat.AUTO,
                    declared_format=ImageFormat.AUTO,
                    duration=asyncio.get_event_loop().time() - start_time,
                )

            # 2. Récupérer la taille du fichier
            try:
                file_size = path.stat().st_size
            except OSError as e:
                return self._build_result(
                    path=path,
                    state=ValidationState.INVALID,
                    issues=[
                        ValidationIssue(
                            issue_type=ValidationIssueType.UNREADABLE,
                            message=f"Impossible de lire le fichier: {e}",
                            severity="error",
                        )
                    ],
                    detected_format=ImageFormat.AUTO,
                    declared_format=ImageFormat.AUTO,
                    duration=asyncio.get_event_loop().time() - start_time,
                )

            # 3. Format déclaré (extension)
            declared_format = detect_format_from_extension(path)

            # 4. Validations pré-Pillow (rapides)
            issues: list[ValidationIssue] = []

            # Fichier vide
            if file_size == 0:
                issues.append(
                    ValidationIssue(
                        issue_type=ValidationIssueType.EMPTY_FILE,
                        message="Fichier vide (0 bytes)",
                        severity="error",
                    )
                )
                return self._finalize_result(
                    path=path,
                    issues=issues,
                    file_size=file_size,
                    declared_format=declared_format,
                    detected_format=ImageFormat.AUTO,
                    duration=asyncio.get_event_loop().time() - start_time,
                )

            # Fichier trop petit
            if (
                effective_config.min_file_size_bytes > 0
                and file_size < effective_config.min_file_size_bytes
            ):
                issues.append(
                    ValidationIssue(
                        issue_type=ValidationIssueType.TOO_SMALL_FILE,
                        message=(
                            f"Fichier trop petit: {file_size} bytes "
                            f"(min: {effective_config.min_file_size_bytes})"
                        ),
                        severity="error",
                        details={
                            "file_size": file_size,
                            "min_size": effective_config.min_file_size_bytes,
                        },
                    )
                )

            # Fichier trop grand
            if (
                effective_config.max_file_size_bytes > 0
                and file_size > effective_config.max_file_size_bytes
            ):
                issues.append(
                    ValidationIssue(
                        issue_type=ValidationIssueType.TOO_LARGE_FILE,
                        message=(
                            f"Fichier trop volumineux: {file_size} bytes "
                            f"(max: {effective_config.max_file_size_bytes})"
                        ),
                        severity="error",
                        details={
                            "file_size": file_size,
                            "max_size": effective_config.max_file_size_bytes,
                        },
                    )
                )

            # 5. Détection HTML déguisé (rapide, avant Pillow)
            if effective_config.reject_html_disguise and file_size >= self._HTML_DETECTION_MIN_BYTES:
                try:
                    header = await asyncio.to_thread(self._read_header, path, 4096)
                    is_html, reason = detect_html_disguise(header)
                    if is_html:
                        issues.append(
                            ValidationIssue(
                                issue_type=ValidationIssueType.HTML_DISGUISE,
                                message=f"HTML déguisé en image détecté: {reason}",
                                severity="error",
                                details={"reason": reason},
                            )
                        )
                except Exception as e:
                    self._logger.warning(
                        "Erreur lors de la détection HTML pour {}: {}", path, e
                    )

            # 6. Détection du format depuis le contenu
            detected_format = ImageFormat.AUTO
            try:
                header = await asyncio.to_thread(self._read_header, path, 32)
                detected_format = detect_format_from_content(header)
            except Exception as e:
                self._logger.warning(
                    "Erreur lors de la détection de format pour {}: {}", path, e
                )

            # Format non détecté
            if detected_format == ImageFormat.AUTO:
                issues.append(
                    ValidationIssue(
                        issue_type=ValidationIssueType.UNSUPPORTED_FORMAT,
                        message="Format d'image non détectable depuis le contenu",
                        severity="error",
                    )
                )

            # Format non autorisé
            elif (
                effective_config.allowed_formats
                and detected_format not in effective_config.allowed_formats
            ):
                issues.append(
                    ValidationIssue(
                        issue_type=ValidationIssueType.UNSUPPORTED_FORMAT,
                        message=f"Format non autorisé: {detected_format.value}",
                        severity="error",
                        details={
                            "detected": detected_format.value,
                            "allowed": [f.value for f in effective_config.allowed_formats],
                        },
                    )
                )

            # Incohérence format déclaré vs détecté
            if (
                effective_config.reject_format_mismatch
                and declared_format != ImageFormat.AUTO
                and detected_format != ImageFormat.AUTO
                and declared_format != detected_format
                # Normaliser JPEG/JPG
                and not (
                    declared_format in (ImageFormat.JPEG, ImageFormat.JPG)
                    and detected_format in (ImageFormat.JPEG, ImageFormat.JPG)
                )
            ):
                issues.append(
                    ValidationIssue(
                        issue_type=ValidationIssueType.FORMAT_MISMATCH,
                        message=(
                            f"Incohérence de format: extension={declared_format.value}, "
                            f"contenu={detected_format.value}"
                        ),
                        severity="error" if effective_config.level == ValidationLevel.STRICT else "warning",
                        details={
                            "declared": declared_format.value,
                            "detected": detected_format.value,
                        },
                    )
                )

            # 7. Si des erreurs critiques sont déjà détectées, on s'arrête là
            if any(i.severity == "error" for i in issues):
                return self._finalize_result(
                    path=path,
                    issues=issues,
                    file_size=file_size,
                    declared_format=declared_format,
                    detected_format=detected_format,
                    duration=asyncio.get_event_loop().time() - start_time,
                )

            # 8. Validation Pillow (dimensions, intégrité, mode)
            try:
                metadata = await asyncio.to_thread(self._read_image_metadata, path)
                width, height, mode, is_truncated = metadata

                # Dimensions nulles
                if effective_config.reject_zero_dimensions and (width == 0 or height == 0):
                    issues.append(
                        ValidationIssue(
                            issue_type=ValidationIssueType.ZERO_DIMENSIONS,
                            message=f"Dimensions nulles: {width}x{height}",
                            severity="error",
                            details={"width": width, "height": height},
                        )
                    )

                # Dimensions trop petites
                if (
                    effective_config.min_width > 0
                    and width < effective_config.min_width
                ) or (
                    effective_config.min_height > 0
                    and height < effective_config.min_height
                ):
                    issues.append(
                        ValidationIssue(
                            issue_type=ValidationIssueType.TOO_SMALL_DIMENSIONS,
                            message=(
                                f"Dimensions trop petites: {width}x{height} "
                                f"(min: {effective_config.min_width}x{effective_config.min_height})"
                            ),
                            severity="error",
                            details={
                                "width": width,
                                "height": height,
                                "min_width": effective_config.min_width,
                                "min_height": effective_config.min_height,
                            },
                        )
                    )

                # Dimensions trop grandes
                if (
                    effective_config.max_width > 0
                    and width > effective_config.max_width
                ) or (
                    effective_config.max_height > 0
                    and height > effective_config.max_height
                ):
                    issues.append(
                        ValidationIssue(
                            issue_type=ValidationIssueType.TOO_LARGE_DIMENSIONS,
                            message=(
                                f"Dimensions trop grandes: {width}x{height} "
                                f"(max: {effective_config.max_width}x{effective_config.max_height})"
                            ),
                            severity="warning",
                            details={
                                "width": width,
                                "height": height,
                                "max_width": effective_config.max_width,
                                "max_height": effective_config.max_height,
                            },
                        )
                    )

                # Placeholder
                if effective_config.reject_placeholders and is_placeholder(width, height):
                    issues.append(
                        ValidationIssue(
                            issue_type=ValidationIssueType.PLACEHOLDER,
                            message=f"Placeholder détecté: {width}x{height}",
                            severity="error",
                            details={"width": width, "height": height},
                        )
                    )

                # Ratio d'aspect invalide
                if width > 0 and height > 0:
                    ratio = width / height
                    if (
                        ratio < effective_config.min_aspect_ratio
                        or ratio > effective_config.max_aspect_ratio
                    ):
                        issues.append(
                            ValidationIssue(
                                issue_type=ValidationIssueType.INVALID_ASPECT_RATIO,
                                message=(
                                    f"Ratio d'aspect invalide: {ratio:.2f} "
                                    f"(autorisé: {effective_config.min_aspect_ratio}-{effective_config.max_aspect_ratio})"
                                ),
                                severity="warning",
                                details={
                                    "ratio": ratio,
                                    "min_ratio": effective_config.min_aspect_ratio,
                                    "max_ratio": effective_config.max_aspect_ratio,
                                },
                            )
                        )

                # Image tronquée
                if is_truncated and not effective_config.allow_truncated:
                    issues.append(
                        ValidationIssue(
                            issue_type=ValidationIssueType.TRUNCATED,
                            message="Image tronquée (téléchargement incomplet)",
                            severity="error",
                        )
                    )
                elif is_truncated:
                    issues.append(
                        ValidationIssue(
                            issue_type=ValidationIssueType.TRUNCATED,
                            message="Image partiellement tronquée (acceptée)",
                            severity="warning",
                        )
                    )

            except UnidentifiedImageError:
                issues.append(
                    ValidationIssue(
                        issue_type=ValidationIssueType.UNREADABLE,
                        message="Image non reconnue par Pillow",
                        severity="error",
                    )
                )
                width, height, mode = 0, 0, ""

            except Exception as e:
                issues.append(
                    ValidationIssue(
                        issue_type=ValidationIssueType.CORRUPTED,
                        message=f"Image corrompue ou illisible: {e}",
                        severity="error",
                        details={"error": str(e)},
                    )
                )
                width, height, mode = 0, 0, ""

            # 9. Finaliser le résultat
            return self._finalize_result(
                path=path,
                issues=issues,
                file_size=file_size,
                declared_format=declared_format,
                detected_format=detected_format,
                width=width,
                height=height,
                mode=mode,
                duration=asyncio.get_event_loop().time() - start_time,
            )

    # ------------------------------------------------------------------------
    # API publique — Validation par lot
    # ------------------------------------------------------------------------

    async def validate_batch(
        self,
        paths: Sequence[Path],
        *,
        config: ValidationConfig | None = None,
        on_progress: Any | None = None,
    ) -> list[ValidationResult]:
        """Valide un lot d'images en parallèle.

        Args:
            paths: Liste des chemins à valider.
            config: Configuration de validation.
            on_progress: Callback appelé après chaque validation
                         signature: (result: ValidationResult, index: int, total: int) -> None.

        Returns:
            Liste des résultats de validation.
        """
        self._ensure_started()

        if not paths:
            return []

        effective_config = config or self._default_config

        # Créer les tâches
        tasks: list[asyncio.Task[ValidationResult]] = []
        for path in paths:
            task = asyncio.create_task(
                self.validate(path, config=effective_config),
                name=f"validate_{path.name}",
            )
            tasks.append(task)

        # Exécuter
        results: list[ValidationResult] = []
        for index, task in enumerate(tasks):
            try:
                result = await task
                results.append(result)
                if on_progress is not None:
                    on_progress(result, index, len(paths))
            except Exception as e:
                self._logger.warning("Validation échouée dans le lot: {}", e)

        self._logger.info(
            "Lot terminé: {}/{} images valides",
            sum(1 for r in results if r.is_valid),
            len(paths),
        )
        return results

    # ------------------------------------------------------------------------
    # API publique — Validation en mémoire
    # ------------------------------------------------------------------------

    async def validate_bytes(
        self,
        data: bytes,
        *,
        filename: str = "<bytes>",
        config: ValidationConfig | None = None,
    ) -> ValidationResult:
        """Valide une image en mémoire (sans fichier).

        Args:
            data: Contenu binaire de l'image.
            filename: Nom de fichier fictif pour le résultat.
            config: Configuration de validation.

        Returns:
            Résultat de la validation.
        """
        self._ensure_started()
        assert self._semaphore is not None

        effective_config = config or self._default_config
        path = Path(filename)

        if effective_config.level == ValidationLevel.NONE:
            detected = detect_format_from_content(data)
            return self._build_result(
                path=path,
                state=ValidationState.VALID,
                issues=[],
                file_size=len(data),
                detected_format=detected,
                declared_format=ImageFormat.AUTO,
                format_consistent=True,
                duration=0.0,
            )

        async with self._semaphore:
            start_time = asyncio.get_event_loop().time()
            issues: list[ValidationIssue] = []

            # Détection format
            detected_format = detect_format_from_content(data)

            # Taille
            if len(data) == 0:
                issues.append(
                    ValidationIssue(
                        issue_type=ValidationIssueType.EMPTY_FILE,
                        message="Données vides",
                        severity="error",
                    )
                )

            # HTML déguisé
            if effective_config.reject_html_disguise and len(data) >= self._HTML_DETECTION_MIN_BYTES:
                is_html, reason = detect_html_disguise(data)
                if is_html:
                    issues.append(
                        ValidationIssue(
                            issue_type=ValidationIssueType.HTML_DISGUISE,
                            message=f"HTML déguisé: {reason}",
                            severity="error",
                        )
                    )

            # Validation Pillow
            width, height, mode = 0, 0, ""
            if not any(i.severity == "error" for i in issues):
                try:
                    width, height, mode = await asyncio.to_thread(
                        self._read_bytes_metadata, data
                    )

                    if effective_config.reject_placeholders and is_placeholder(width, height):
                        issues.append(
                            ValidationIssue(
                                issue_type=ValidationIssueType.PLACEHOLDER,
                                message=f"Placeholder: {width}x{height}",
                                severity="error",
                            )
                        )

                    if (
                        effective_config.min_width > 0
                        and width < effective_config.min_width
                    ) or (
                        effective_config.min_height > 0
                        and height < effective_config.min_height
                    ):
                        issues.append(
                            ValidationIssue(
                                issue_type=ValidationIssueType.TOO_SMALL_DIMENSIONS,
                                message=f"Dimensions trop petites: {width}x{height}",
                                severity="error",
                            )
                        )

                except Exception as e:
                    issues.append(
                        ValidationIssue(
                            issue_type=ValidationIssueType.CORRUPTED,
                            message=f"Image corrompue: {e}",
                            severity="error",
                        )
                    )

            return self._finalize_result(
                path=path,
                issues=issues,
                file_size=len(data),
                declared_format=ImageFormat.AUTO,
                detected_format=detected_format,
                width=width,
                height=height,
                mode=mode,
                duration=asyncio.get_event_loop().time() - start_time,
            )

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> ValidationStats:
        """Retourne les statistiques agrégées du validateur."""
        async with self._stats_lock:
            uptime = 0.0
            if self._start_time > 0:
                uptime = asyncio.get_event_loop().time() - self._start_time

            return ValidationStats(
                total_validations=self._total_validations,
                valid=self._valid,
                invalid=self._invalid,
                warnings=self._warnings,
                unreadable=self._unreadable,
                total_bytes_processed=self._total_bytes_processed,
                issues_by_type=dict(self._issues_by_type),
                uptime_seconds=uptime,
            )

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        async with self._stats_lock:
            self._total_validations = 0
            self._valid = 0
            self._invalid = 0
            self._warnings = 0
            self._unreadable = 0
            self._total_bytes_processed = 0
            self._issues_by_type.clear()
            self._start_time = asyncio.get_event_loop().time()

    # ------------------------------------------------------------------------
    # Méthodes internes — Helpers
    # ------------------------------------------------------------------------

    async def _passthrough(self, path: Path) -> ValidationResult:
        """Validation passe-through (niveau NONE)."""
        start_time = asyncio.get_event_loop().time()

        try:
            file_size = path.stat().st_size
        except OSError:
            file_size = 0

        detected = ImageFormat.AUTO
        try:
            header = await asyncio.to_thread(self._read_header, path, 32)
            detected = detect_format_from_content(header)
        except Exception:
            pass

        return self._build_result(
            path=path,
            state=ValidationState.VALID,
            issues=[],
            file_size=file_size,
            detected_format=detected,
            declared_format=detect_format_from_extension(path),
            format_consistent=True,
            duration=asyncio.get_event_loop().time() - start_time,
        )

    def _finalize_result(
        self,
        path: Path,
        issues: list[ValidationIssue],
        file_size: int,
        declared_format: ImageFormat,
        detected_format: ImageFormat,
        width: int = 0,
        height: int = 0,
        mode: str = "",
        duration: float = 0.0,
    ) -> ValidationResult:
        """Construit et enregistre le résultat final."""
        has_errors = any(i.severity == "error" for i in issues)
        has_warnings = any(i.severity == "warning" for i in issues)

        if has_errors:
            state = ValidationState.INVALID
            is_valid = False
        elif has_warnings:
            state = ValidationState.WARNING
            is_valid = True
        else:
            state = ValidationState.VALID
            is_valid = True

        format_consistent = (
            declared_format == ImageFormat.AUTO
            or detected_format == ImageFormat.AUTO
            or declared_format == detected_format
            or (
                declared_format in (ImageFormat.JPEG, ImageFormat.JPG)
                and detected_format in (ImageFormat.JPEG, ImageFormat.JPG)
            )
        )

        result = ValidationResult(
            path=path,
            state=state,
            is_valid=is_valid,
            issues=issues,
            width=width,
            height=height,
            file_size_bytes=file_size,
            detected_format=detected_format,
            declared_format=declared_format,
            mode=mode,
            duration_seconds=duration,
            format_consistent=format_consistent,
        )

        # Mettre à jour les stats (fire-and-forget)
        asyncio.create_task(self._record_result(result))

        return result

    def _build_result(
        self,
        path: Path,
        state: ValidationState,
        issues: list[ValidationIssue],
        detected_format: ImageFormat,
        declared_format: ImageFormat,
        file_size: int = 0,
        width: int = 0,
        height: int = 0,
        mode: str = "",
        format_consistent: bool = True,
        duration: float = 0.0,
    ) -> ValidationResult:
        """Construit un résultat sans enregistrement de stats."""
        return ValidationResult(
            path=path,
            state=state,
            is_valid=state in (ValidationState.VALID, ValidationState.WARNING),
            issues=issues,
            width=width,
            height=height,
            file_size_bytes=file_size,
            detected_format=detected_format,
            declared_format=declared_format,
            mode=mode,
            duration_seconds=duration,
            format_consistent=format_consistent,
        )

    async def _record_result(self, result: ValidationResult) -> None:
        """Enregistre un résultat dans les statistiques."""
        async with self._stats_lock:
            self._total_validations += 1
            self._total_bytes_processed += result.file_size_bytes

            if result.state == ValidationState.VALID:
                self._valid += 1
            elif result.state == ValidationState.WARNING:
                self._warnings += 1
                self._valid += 1  # Compte comme valide
            elif result.state == ValidationState.INVALID:
                self._invalid += 1
                if any(
                    i.issue_type == ValidationIssueType.UNREADABLE for i in result.issues
                ):
                    self._unreadable += 1

            for issue in result.issues:
                key = issue.issue_type.value
                self._issues_by_type[key] = self._issues_by_type.get(key, 0) + 1

    @staticmethod
    def _read_header(path: Path, size: int) -> bytes:
        """Lit les premiers bytes d'un fichier (synchrone)."""
        with path.open("rb") as f:
            return f.read(size)

    @staticmethod
    def _read_image_metadata(path: Path) -> tuple[int, int, str, bool]:
        """Lit les métadonnées d'une image (synchrone, dans un thread).

        Returns:
            Tuple (width, height, mode, is_truncated).
        """
        with Image.open(path) as img:
            width, height = img.size
            mode = img.mode

            # Détecter si l'image est tronquée
            is_truncated = False
            try:
                img.load()  # Force le chargement complet
            except (OSError, SyntaxError, Image.DecompressionBombError):
                is_truncated = True
            except Exception:
                is_truncated = True

            return width, height, mode, is_truncated

    @staticmethod
    def _read_bytes_metadata(data: bytes) -> tuple[int, int, str]:
        """Lit les métadonnées depuis des bytes (synchrone)."""
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            return img.size[0], img.size[1], img.mode

    def _ensure_started(self) -> None:
        """Vérifie que le validateur est démarré."""
        if not self._started:
            raise ValidatorNotStartedError()

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<ImageValidator status={status} "
            f"max_concurrent={self._max_concurrent} "
            f"validations={self._total_validations}>"
        )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "ImageValidationError",
    "ValidatorNotStartedError",
    "ImageUnreadableError",
    # Enums
    "ValidationLevel",
    "ValidationIssueType",
    "ValidationState",
    # Modèles
    "ValidationConfig",
    "ValidationIssue",
    "ValidationResult",
    "ValidationStats",
    # Helpers
    "detect_html_disguise",
    "is_placeholder",
    # Classe principale
    "ImageValidator",
]
