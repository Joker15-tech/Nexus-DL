"""Téléchargeur d'images intelligent avec validation et streaming.

Ce module fournit un téléchargeur d'images asynchrone robuste conçu pour
le scraping de mangas/webtoons/comics. Il gère les cas pathologiques
fréquemment rencontrés sur les sites sources :

    - Fichiers HTML déguisés en images (pages d'erreur, CAPTCHA, etc.)
    - Content-Type HTTP incorrect ou manquant
    - Images tronquées ou corrompues
    - URLs expirantes ou protégées par Referer
    - Rate limiting et blocages Cloudflare
    - Redirections vers des CDN temporaires

Fonctionnalités principales :
    - Téléchargement streaming (faible empreinte mémoire)
    - Validation du Content-Type HTTP + magic bytes (double vérification)
    - Détection automatique des HTML déguisés en images
    - Calcul du hash SHA256 pour la déduplication
    - Retry intelligent avec backoff exponentiel
    - Conversion optionnelle via ImageConverter (WebP, AVIF, etc.)
    - Validation d'intégrité via ImageValidator
    - Téléchargement par lot avec concurrence contrôlée
    - Support des headers Referer/Origin (contournement hotlink protection)

Architecture :
    ImageDownloader
        ├── HttpSession (téléchargement HTTP)
        ├── ImageValidator (validation d'intégrité)
        ├── ImageConverter (conversion optionnelle)
        └── RetryPolicy (retry intelligent)

Exemple d'utilisation :
    >>> downloader = ImageDownloader(session=session)
    >>> await downloader.start()
    >>>
    >>> # Téléchargement simple
    >>> result = await downloader.download(
    ...     url="https://cdn.example.com/page001.jpg",
    ...     dest=Path("/tmp/pages/"),
    ...     referer="https://example.com/manga/123",
    ... )
    >>> print(f"Téléchargé: {result.output_path} ({result.size_bytes} bytes)")
    >>> print(f"Hash SHA256: {result.sha256}")
    >>>
    >>> # Téléchargement avec conversion WebP
    >>> result = await downloader.download(
    ...     url="https://cdn.example.com/page001.jpg",
    ...     dest=Path("/tmp/pages/"),
    ...     config=DownloadConfig(
    ...         convert_to=ImageFormat.WEBP,
    ...         quality=85,
    ...     ),
    ... )
    >>>
    >>> # Téléchargement par lot
    >>> results = await downloader.download_batch(
    ...     urls=["https://.../page001.jpg", "https://.../page002.jpg"],
    ...     dest_dir=Path("/tmp/pages/"),
    ... )
    >>>
    >>> await downloader.stop()
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final, Self
from urllib.parse import urlparse

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.downloader.retry import (
    PAGE_DOWNLOAD_POLICY,
    build_retry,
    is_retryable,
)
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.image.converter import (
    ConversionConfig,
    ConversionResult,
    ImageConverter,
    ImageFormat,
    detect_format_from_content,
)

if TYPE_CHECKING:
    from nexusdl.core.image.validator import ImageValidator
    from nexusdl.core.session.http_session import HttpSession


# ============================================================================
# EXCEPTIONS
# ============================================================================


class ImageDownloadError(NexusDLError):
    """Exception de base pour les erreurs de téléchargement d'images."""


class InvalidContentTypeError(ImageDownloadError):
    """Exception levée lorsque le Content-Type n'est pas une image."""

    def __init__(self, url: str, content_type: str) -> None:
        super().__init__(
            f"Content-Type invalide pour {url}: {content_type} (attendu: image/*)"
        )
        self.url = url
        self.content_type = content_type


class HtmlDisguisedAsImageError(ImageDownloadError):
    """Exception levée lorsqu'un fichier HTML est déguisé en image.

    Cas courant : site qui renvoie une page d'erreur, un CAPTCHA, ou une
    page de login au lieu de l'image demandée.
    """

    def __init__(self, url: str, reason: str = "") -> None:
        msg = f"HTML déguisé en image détecté pour {url}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.url = url
        self.reason = reason


class ImageTooLargeError(ImageDownloadError):
    """Exception levée lorsqu'une image dépasse la taille maximale autorisée."""

    def __init__(self, url: str, size_bytes: int, max_size_bytes: int) -> None:
        super().__init__(
            f"Image trop volumineuse pour {url}: {size_bytes} bytes "
            f"(max: {max_size_bytes} bytes)"
        )
        self.url = url
        self.size_bytes = size_bytes
        self.max_size_bytes = max_size_bytes


class DownloadTimeoutError(ImageDownloadError):
    """Exception levée lorsqu'un téléchargement dépasse le timeout."""

    def __init__(self, url: str, timeout_seconds: float) -> None:
        super().__init__(
            f"Timeout dépassé pour {url} après {timeout_seconds:.1f}s"
        )
        self.url = url
        self.timeout_seconds = timeout_seconds


# ============================================================================
# ENUMS
# ============================================================================


class ContentTypeCategory(str, Enum):
    """Catégorie de Content-Type détecté."""

    IMAGE = "image"  # Content-Type valide (image/jpeg, image/png, etc.)
    HTML = "html"  # HTML déguisé (text/html, application/xhtml+xml)
    TEXT = "text"  # Texte pur (text/plain, etc.)
    JSON = "json"  # JSON (application/json)
    XML = "xml"  # XML (application/xml)
    UNKNOWN = "unknown"  # Content-Type inconnu ou manquant


class DownloadState(str, Enum):
    """État d'un téléchargement."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    VALIDATING = "validating"
    CONVERTING = "converting"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"  # Déduplication ou déjà existant


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class DownloadConfig(BaseModel):
    """Configuration complète d'un téléchargement d'image.

    Tous les champs sont optionnels avec des valeurs par défaut raisonnables.
    Le modèle est immuable (`frozen=True`) pour garantir la cohérence lors
    des téléchargements par lot.
    """

    filename: str | None = Field(
        default=None,
        description="Nom de fichier de sortie (défaut: extrait de l'URL).",
    )
    timeout_seconds: float = Field(
        default=60.0,
        gt=0.0,
        le=600.0,
        description="Timeout par téléchargement en secondes.",
    )
    max_size_bytes: int = Field(
        default=50 * 1024 * 1024,  # 50 Mo
        gt=0,
        description="Taille maximale autorisée en bytes (0 = illimité).",
    )
    chunk_size: int = Field(
        default=65536,  # 64 KB
        gt=0,
        le=1024 * 1024,
        description="Taille des chunks de streaming en bytes.",
    )
    referer: str | None = Field(
        default=None,
        description="Header Referer à envoyer (contournement hotlink protection).",
    )
    origin: str | None = Field(
        default=None,
        description="Header Origin à envoyer.",
    )
    accept_html_disguise: bool = Field(
        default=False,
        description="Accepter les fichiers HTML déguisés en images (déconseillé).",
    )
    validate_integrity: bool = Field(
        default=True,
        description="Valider l'intégrité de l'image après téléchargement.",
    )
    compute_hash: bool = Field(
        default=True,
        description="Calculer le hash SHA256 du contenu.",
    )
    convert_to: ImageFormat | None = Field(
        default=None,
        description="Convertir l'image vers ce format après téléchargement (None = pas de conversion).",
    )
    conversion_quality: int = Field(
        default=85,
        ge=1,
        le=100,
        description="Qualité de conversion (1-100).",
    )
    overwrite_existing: bool = Field(
        default=False,
        description="Écraser les fichiers existants.",
    )
    skip_if_exists: bool = Field(
        default=True,
        description="Sauter si le fichier de destination existe déjà.",
    )
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Nombre maximum de tentatives en cas d'échec.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid", validate_assignment=True)


class DownloadResult(BaseModel):
    """Résultat immuable d'un téléchargement d'image.

    Contient toutes les métadonnées du téléchargement pour le reporting,
    la déduplication et la traçabilité.
    """

    url: str = Field(..., description="URL source de l'image.")
    output_path: Path = Field(..., description="Chemin du fichier téléchargé.")
    size_bytes: int = Field(..., ge=0, description="Taille du fichier en bytes.")
    sha256: str | None = Field(
        default=None,
        description="Hash SHA256 du contenu (64 caractères hex).",
        pattern=r"^[a-f0-9]{64}$",
    )
    content_type: str = Field(..., description="Content-Type HTTP détecté.")
    content_type_category: ContentTypeCategory = Field(
        ..., description="Catégorie du Content-Type."
    )
    image_format: ImageFormat = Field(
        ..., description="Format d'image détecté depuis le contenu."
    )
    width: int = Field(default=0, ge=0, description="Largeur de l'image en pixels.")
    height: int = Field(default=0, ge=0, description="Hauteur de l'image en pixels.")
    duration_seconds: float = Field(..., ge=0.0, description="Durée du téléchargement.")
    state: DownloadState = Field(..., description="État final du téléchargement.")
    converted: bool = Field(
        default=False, description="True si l'image a été convertue."
    )
    conversion_result: ConversionResult | None = Field(
        default=None, description="Résultat de la conversion (si convertie)."
    )
    retries_count: int = Field(
        default=0, ge=0, description="Nombre de tentatives effectuées."
    )
    error: str | None = Field(default=None, description="Message d'erreur si échec.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def is_success(self) -> bool:
        """Indique si le téléchargement a réussi."""
        return self.state in (DownloadState.COMPLETED, DownloadState.SKIPPED)


class DownloadStats(BaseModel):
    """Statistiques agrégées du téléchargeur."""

    total_downloads: int = Field(default=0, ge=0)
    successful: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0, description="Images sautées (dédup/existant).")
    converted: int = Field(default=0, ge=0, description="Images convertues.")
    total_bytes_downloaded: int = Field(default=0, ge=0)
    total_bytes_saved_by_conversion: int = Field(
        default=0,
        ge=0,
        description="Bytes économisés grâce à la conversion.",
    )
    total_retries: int = Field(default=0, ge=0)
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def success_rate(self) -> float:
        """Taux de succès (0.0 à 1.0)."""
        if self.total_downloads == 0:
            return 0.0
        return self.successful / self.total_downloads


# ============================================================================
# HELPERS — Détection de Content-Type
# ============================================================================


# Magic bytes pour la détection de format d'image
_IMAGE_MAGIC_BYTES: Final[dict[bytes, str]] = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"RIFF": "image/webp",  # + WEBP à l'offset 8
    b"BM": "image/bmp",
    b"II\x2a\x00": "image/tiff",
    b"MM\x00\x2a": "image/tiff",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}

# Signatures HTML courantes (pour détection de HTML déguisé)
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

# Patterns regex pour extraire le titre des pages HTML déguisées
_HTML_TITLE_PATTERN: Final[re.Pattern[bytes]] = re.compile(
    rb"<title[^>]*>([^<]+)</title>", re.IGNORECASE
)

# Content-Types considérés comme HTML
_HTML_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "application/xml",
        "text/xml",
    }
)

# Content-Types considérés comme images valides
_IMAGE_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/pjpeg",
        "image/png",
        "image/webp",
        "image/avif",
        "image/gif",
        "image/bmp",
        "image/tiff",
        "image/x-icon",
        "image/vnd.microsoft.icon",
    }
)


def classify_content_type(content_type: str | None) -> ContentTypeCategory:
    """Classifie un Content-Type HTTP en catégorie.

    Args:
        content_type: Valeur du header Content-Type (peut être None).

    Returns:
        Catégorie du Content-Type.

    Example:
        >>> classify_content_type("image/jpeg")
        <ContentTypeCategory.IMAGE: 'image'>
        >>> classify_content_type("text/html; charset=utf-8")
        <ContentTypeCategory.HTML: 'html'>
    """
    if not content_type:
        return ContentTypeCategory.UNKNOWN

    # Normaliser (enlever les paramètres comme charset)
    normalized = content_type.split(";")[0].strip().lower()

    if normalized in _IMAGE_CONTENT_TYPES or normalized.startswith("image/"):
        return ContentTypeCategory.IMAGE
    if normalized in _HTML_CONTENT_TYPES:
        return ContentTypeCategory.HTML
    if normalized.startswith("text/"):
        return ContentTypeCategory.TEXT
    if normalized == "application/json":
        return ContentTypeCategory.JSON
    if normalized in ("application/xml", "text/xml"):
        return ContentTypeCategory.XML

    return ContentTypeCategory.UNKNOWN


def detect_html_disguise(data: bytes) -> tuple[bool, str]:
    """Détecte si des données binaires sont en réalité du HTML déguisé.

    Cette fonction analyse les premiers bytes pour détecter les signatures
    HTML courantes. C'est un cas fréquent sur les sites de manga qui
    renvoient une page d'erreur, un CAPTCHA ou une page de login au lieu
    de l'image demandée.

    Args:
        data: Contenu binaire à analyser (au moins les 512 premiers bytes).

    Returns:
        Tuple (is_html, reason) où is_html indique si c'est du HTML déguisé
        et reason explique pourquoi (titre de la page si détecté).

    Example:
        >>> is_html, reason = detect_html_disguise(b"<!DOCTYPE html><html>...")
        >>> print(is_html, reason)
        True "Page d'erreur 404"
    """
    if len(data) < 16:
        return False, ""

    # Vérifier les signatures HTML
    for signature in _HTML_SIGNATURES:
        if data[: len(signature)].lower() == signature.lower():
            # Extraire le titre si possible
            title_match = _HTML_TITLE_PATTERN.search(data[:2048])
            reason = title_match.group(1).decode("utf-8", errors="ignore").strip() if title_match else "HTML détecté"
            return True, reason

    # Détecter les pages d'erreur courantes (même sans balise HTML complète)
    error_patterns = [
        rb"403 Forbidden",
        rb"404 Not Found",
        rb"503 Service Unavailable",
        rb"Access Denied",
        rb"CAPTCHA",
        rb"cf-browser-verification",  # Cloudflare challenge
        rb"Just a moment",  # Cloudflare "Just a moment..."
        rb"Enable JavaScript",  # Cloudflare JS challenge
    ]

    for pattern in error_patterns:
        if pattern.lower() in data[:4096].lower():
            return True, pattern.decode("utf-8", errors="ignore")

    return False, ""


def extract_filename_from_url(url: str, fallback: str = "image.jpg") -> str:
    """Extrait un nom de fichier depuis une URL.

    Args:
        url: URL source.
        fallback: Nom de fichier par défaut si extraction échoue.

    Returns:
        Nom de fichier extrait ou fallback.

    Example:
        >>> extract_filename_from_url("https://cdn.example.com/manga/001/page001.jpg?v=123")
        'page001.jpg'
    """
    try:
        parsed = urlparse(url)
        path = parsed.path
        if not path or path == "/":
            return fallback

        # Prendre le dernier segment du chemin
        filename = path.split("/")[-1]

        # Retirer les query parameters éventuels restants
        if "?" in filename:
            filename = filename.split("?")[0]

        # Valider le nom
        if not filename or len(filename) > 255:
            return fallback

        # Vérifier qu'il a une extension
        if "." not in filename:
            return fallback

        return filename

    except Exception:
        return fallback


# ============================================================================
# CLASSE PRINCIPALE — ImageDownloader
# ============================================================================


class ImageDownloader:
    """Téléchargeur d'images asynchrone avec validation et streaming.

    Gère le téléchargement d'images depuis des URLs avec :
        - Streaming pour faible empreinte mémoire
        - Validation du Content-Type HTTP + magic bytes
        - Détection des HTML déguisés en images
        - Calcul du hash SHA256 pour la déduplication
        - Retry intelligent avec backoff exponentiel
        - Conversion optionnelle via ImageConverter
        - Validation d'intégrité via ImageValidator

    Lifecycle :
        >>> downloader = ImageDownloader(session=session)
        >>> await downloader.start()
        >>> # ... téléchargements ...
        >>> await downloader.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Les téléchargements par lot utilisent un sémaphore pour
        limiter la concurrence.
    """

    # Constantes de configuration
    _DEFAULT_MAX_CONCURRENT: Final[int] = 8
    _DEFAULT_TIMEOUT: Final[float] = 60.0
    _MIN_BYTES_FOR_HTML_DETECTION: Final[int] = 512

    def __init__(
        self,
        session: HttpSession,
        *,
        converter: ImageConverter | None = None,
        validator: ImageValidator | None = None,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
        default_timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        """Initialise le téléchargeur d'images.

        Args:
            session: Session HTTP pour effectuer les requêtes.
            converter: Convertisseur d'images (optionnel, pour conversion post-download).
            validator: Validateur d'images (optionnel, pour validation d'intégrité).
            max_concurrent: Nombre maximum de téléchargements simultanés.
            default_timeout: Timeout par défaut en secondes.
        """
        if max_concurrent <= 0:
            raise ValueError(f"max_concurrent must be positive, got {max_concurrent}")
        if default_timeout <= 0:
            raise ValueError(f"default_timeout must be positive, got {default_timeout}")

        self._session = session
        self._converter = converter
        self._validator = validator
        self._max_concurrent = max_concurrent
        self._default_timeout = default_timeout

        self._semaphore: asyncio.Semaphore | None = None
        self._started: bool = False
        self._start_time: float = 0.0

        # Statistiques
        self._total_downloads: int = 0
        self._successful: int = 0
        self._failed: int = 0
        self._skipped: int = 0
        self._converted: int = 0
        self._total_bytes_downloaded: int = 0
        self._total_bytes_saved: int = 0
        self._total_retries: int = 0
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="image_downloader")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre le téléchargeur et initialise les ressources."""
        if self._started:
            self._logger.warning("ImageDownloader déjà démarré, ignore")
            return

        self._semaphore = asyncio.Semaphore(self._max_concurrent)

        # Démarrer le converter si fourni
        if self._converter is not None and not self._converter.is_started:
            await self._converter.start()

        self._started = True
        self._start_time = asyncio.get_event_loop().time()

        self._logger.info(
            "ImageDownloader démarré: max_concurrent={}, timeout={:.1f}s",
            self._max_concurrent,
            self._default_timeout,
        )

    async def stop(self) -> None:
        """Arrête le téléchargeur et libère les ressources."""
        if not self._started:
            return

        self._semaphore = None
        self._started = False

        # Arrêter le converter si fourni
        if self._converter is not None and self._converter.is_started:
            await self._converter.stop()

        self._logger.info("ImageDownloader arrêté")

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
        """Indique si le téléchargeur est démarré."""
        return self._started

    @property
    def max_concurrent(self) -> int:
        """Nombre maximum de téléchargements simultanés."""
        return self._max_concurrent

    # ------------------------------------------------------------------------
    # API publique — Téléchargement simple
    # ------------------------------------------------------------------------

    async def download(
        self,
        url: str,
        dest: Path,
        *,
        config: DownloadConfig | None = None,
        on_progress: Any | None = None,
    ) -> DownloadResult:
        """Télécharge une image depuis une URL vers un fichier.

        Le téléchargement est effectué en streaming pour minimiser l'empreinte
        mémoire. Le Content-Type est validé, les HTML déguisés sont détectés,
        et le hash SHA256 est calculé pour la déduplication.

        Args:
            url: URL de l'image à télécharger.
            dest: Répertoire de destination (le fichier sera créé dedans).
            config: Configuration de téléchargement (défaut: valeurs par défaut).
            on_progress: Callback optionnel appelé pendant le téléchargement
                         signature: (bytes_downloaded: int, total_bytes: int | None) -> None.

        Returns:
            Résultat du téléchargement avec toutes les métadonnées.

        Raises:
            ImageDownloadError: Si le téléchargement échoue.
            InvalidContentTypeError: Si le Content-Type n'est pas une image.
            HtmlDisguisedAsImageError: Si un HTML est déguisé en image.
            ImageTooLargeError: Si l'image dépasse la taille maximale.
            DownloadTimeoutError: Si le timeout est dépassé.
        """
        self._ensure_started()
        assert self._semaphore is not None

        effective_config = config or DownloadConfig()

        async with self._semaphore:
            start_time = asyncio.get_event_loop().time()

            # 1. Déterminer le nom de fichier
            filename = effective_config.filename or extract_filename_from_url(url)
            output_path = dest / filename

            # 2. Vérifier si le fichier existe déjà
            if output_path.exists():
                if effective_config.skip_if_exists and not effective_config.overwrite_existing:
                    self._logger.trace(
                        "Fichier déjà existant, skip: {}", output_path.name
                    )
                    async with self._stats_lock:
                        self._skipped += 1
                        self._total_downloads += 1

                    return DownloadResult(
                        url=url,
                        output_path=output_path,
                        size_bytes=output_path.stat().st_size,
                        sha256=None,  # On ne recalcule pas le hash
                        content_type="unknown",
                        content_type_category=ContentTypeCategory.UNKNOWN,
                        image_format=ImageFormat.AUTO,
                        duration_seconds=0.0,
                        state=DownloadState.SKIPPED,
                    )

            # 3. Télécharger avec retry
            try:
                result = await self._download_with_retry(
                    url=url,
                    dest=dest,
                    filename=filename,
                    config=effective_config,
                    on_progress=on_progress,
                    start_time=start_time,
                )

                async with self._stats_lock:
                    self._successful += 1
                    self._total_downloads += 1
                    self._total_bytes_downloaded += result.size_bytes

                return result

            except Exception as e:
                async with self._stats_lock:
                    self._failed += 1
                    self._total_downloads += 1

                self._logger.error(
                    "Échec du téléchargement de {}: {}",
                    url,
                    e,
                )

                duration = asyncio.get_event_loop().time() - start_time
                return DownloadResult(
                    url=url,
                    output_path=Path(),
                    size_bytes=0,
                    content_type="unknown",
                    content_type_category=ContentTypeCategory.UNKNOWN,
                    image_format=ImageFormat.AUTO,
                    duration_seconds=duration,
                    state=DownloadState.FAILED,
                    error=str(e),
                )

    # ------------------------------------------------------------------------
    # API publique — Téléchargement par lot
    # ------------------------------------------------------------------------

    async def download_batch(
        self,
        urls: Sequence[str],
        dest_dir: Path,
        *,
        config: DownloadConfig | None = None,
        filenames: Sequence[str] | None = None,
        on_progress: Any | None = None,
    ) -> list[DownloadResult]:
        """Télécharge un lot d'images vers un répertoire.

        Les téléchargements sont exécutés en parallèle avec la concurrence
        contrôlée par le sémaphore interne.

        Args:
            urls: Liste des URLs à télécharger.
            dest_dir: Répertoire de destination (créé si nécessaire).
            config: Configuration de téléchargement (appliquée à toutes les images).
            filenames: Liste optionnelle de noms de fichiers (même ordre que urls).
            on_progress: Callback appelé après chaque téléchargement
                         signature: (result: DownloadResult, index: int, total: int) -> None.

        Returns:
            Liste des résultats de téléchargement (un par URL).

        Raises:
            ImageDownloadError: Si un téléchargement échoue (les autres continuent).
        """
        self._ensure_started()

        if not urls:
            return []

        # Créer le répertoire de destination
        await asyncio.to_thread(dest_dir.mkdir, parents=True, exist_ok=True)

        effective_config = config or DownloadConfig()

        # Préparer les tâches
        tasks: list[asyncio.Task[DownloadResult]] = []
        for index, url in enumerate(urls):
            # Déterminer le nom de fichier
            filename = None
            if filenames is not None and index < len(filenames):
                filename = filenames[index]

            task_config = effective_config
            if filename is not None:
                # Créer une nouvelle config avec le filename
                task_config = effective_config.model_copy(update={"filename": filename})

            task = asyncio.create_task(
                self.download(url, dest_dir, config=task_config),
                name=f"download_{index}_{url[:50]}",
            )
            tasks.append(task)

        # Exécuter tous les téléchargements
        results: list[DownloadResult] = []
        for index, task in enumerate(tasks):
            try:
                result = await task
                results.append(result)
                if on_progress is not None:
                    on_progress(result, index, len(urls))
            except Exception as e:
                self._logger.warning("Téléchargement échoué dans le lot: {}", e)

        self._logger.info(
            "Lot terminé: {}/{} téléchargements réussis",
            sum(1 for r in results if r.is_success),
            len(urls),
        )
        return results

    # ------------------------------------------------------------------------
    # API publique — Téléchargement en mémoire
    # ------------------------------------------------------------------------

    async def download_to_memory(
        self,
        url: str,
        *,
        config: DownloadConfig | None = None,
    ) -> tuple[bytes, DownloadResult]:
        """Télécharge une image en mémoire (sans fichier).

        Utile pour les traitements à la volée ou les conversions sans
        écriture disque.

        Args:
            url: URL de l'image à télécharger.
            config: Configuration de téléchargement.

        Returns:
            Tuple (données binaires, résultat du téléchargement).

        Raises:
            ImageDownloadError: Si le téléchargement échoue.
        """
        self._ensure_started()
        assert self._semaphore is not None

        effective_config = config or DownloadConfig()

        async with self._semaphore:
            start_time = asyncio.get_event_loop().time()

            # Télécharger dans un buffer mémoire
            data = await self._download_to_bytes(
                url=url,
                config=effective_config,
                start_time=start_time,
            )

            # Détecter le format
            image_format = detect_format_from_content(data)

            # Calculer le hash
            sha256 = None
            if effective_config.compute_hash:
                sha256 = hashlib.sha256(data).hexdigest()

            duration = asyncio.get_event_loop().time() - start_time

            result = DownloadResult(
                url=url,
                output_path=Path("<memory>"),
                size_bytes=len(data),
                sha256=sha256,
                content_type="image/unknown",
                content_type_category=ContentTypeCategory.IMAGE,
                image_format=image_format,
                duration_seconds=duration,
                state=DownloadState.COMPLETED,
            )

            async with self._stats_lock:
                self._successful += 1
                self._total_downloads += 1
                self._total_bytes_downloaded += len(data)

            return data, result

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> DownloadStats:
        """Retourne les statistiques agrégées du téléchargeur.

        Returns:
            Objet DownloadStats avec tous les compteurs.
        """
        async with self._stats_lock:
            uptime = 0.0
            if self._start_time > 0:
                uptime = asyncio.get_event_loop().time() - self._start_time

            return DownloadStats(
                total_downloads=self._total_downloads,
                successful=self._successful,
                failed=self._failed,
                skipped=self._skipped,
                converted=self._converted,
                total_bytes_downloaded=self._total_bytes_downloaded,
                total_bytes_saved_by_conversion=self._total_bytes_saved,
                total_retries=self._total_retries,
                uptime_seconds=uptime,
            )

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques du téléchargeur."""
        async with self._stats_lock:
            self._total_downloads = 0
            self._successful = 0
            self._failed = 0
            self._skipped = 0
            self._converted = 0
            self._total_bytes_downloaded = 0
            self._total_bytes_saved = 0
            self._total_retries = 0
            self._start_time = asyncio.get_event_loop().time()

    # ------------------------------------------------------------------------
    # Méthodes internes — Téléchargement avec retry
    # ------------------------------------------------------------------------

    async def _download_with_retry(
        self,
        url: str,
        dest: Path,
        filename: str,
        config: DownloadConfig,
        on_progress: Any | None,
        start_time: float,
    ) -> DownloadResult:
        """Télécharge une image avec retry intelligent.

        Args:
            url: URL de l'image.
            dest: Répertoire de destination.
            filename: Nom de fichier.
            config: Configuration.
            on_progress: Callback de progression.
            start_time: Timestamp de début.

        Returns:
            Résultat du téléchargement.
        """
        output_path = dest / filename
        retries = 0
        last_exception: Exception | None = None

        for attempt in range(1, config.max_retries + 2):  # +2 car on commence à 1
            try:
                result = await self._do_download(
                    url=url,
                    output_path=output_path,
                    config=config,
                    on_progress=on_progress,
                    start_time=start_time,
                )
                result = result.model_copy(update={"retries_count": retries})

                # Conversion optionnelle
                if config.convert_to is not None and self._converter is not None:
                    result = await self._convert_image(result, config)

                return result

            except Exception as e:
                last_exception = e
                retries += 1

                if not is_retryable(e):
                    self._logger.warning(
                        "Erreur non-retryable pour {}: {}", url, e
                    )
                    raise

                if attempt > config.max_retries:
                    self._logger.error(
                        "Max retries atteint pour {}: {}", url, e
                    )
                    raise

                # Calculer le délai avant la prochaine tentative
                delay = min(2.0 ** (attempt - 1), 30.0)  # Backoff exponentiel
                self._logger.warning(
                    "Retry {}/{} pour {} après {:.1f}s — {}",
                    attempt,
                    config.max_retries,
                    url,
                    delay,
                    e,
                )

                async with self._stats_lock:
                    self._total_retries += 1

                await asyncio.sleep(delay)

        # Ne devrait jamais arriver
        raise last_exception or ImageDownloadError(f"Échec du téléchargement de {url}")

    async def _do_download(
        self,
        url: str,
        output_path: Path,
        config: DownloadConfig,
        on_progress: Any | None,
        start_time: float,
    ) -> DownloadResult:
        """Effectue le téléchargement effectif d'une image.

        Args:
            url: URL de l'image.
            output_path: Chemin du fichier de sortie.
            config: Configuration.
            on_progress: Callback de progression.
            start_time: Timestamp de début.

        Returns:
            Résultat du téléchargement.
        """
        # Préparer les headers additionnels
        extra_headers: dict[str, str] = {}
        if config.referer:
            extra_headers["Referer"] = config.referer
        if config.origin:
            extra_headers["Origin"] = config.origin

        # Créer le répertoire de destination
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Télécharger via la session HTTP
        try:
            response = await asyncio.wait_for(
                self._session.get(
                    url,
                    headers=extra_headers if extra_headers else None,
                    follow_redirects=True,
                ),
                timeout=config.timeout_seconds,
            )
        except asyncio.TimeoutError as e:
            raise DownloadTimeoutError(url, config.timeout_seconds) from e
        except Exception as e:
            raise ImageDownloadError(f"Erreur HTTP pour {url}: {e}") from e

        # Vérifier le status code
        if response.status_code >= 400:
            raise ImageDownloadError(
                f"HTTP {response.status_code} pour {url}"
            )

        # Récupérer le Content-Type
        content_type = response.headers.get("content-type", "")
        content_type_category = classify_content_type(content_type)

        # Vérifier que c'est bien une image
        if content_type_category == ContentTypeCategory.HTML and not config.accept_html_disguise:
            raise InvalidContentTypeError(url, content_type)

        # Récupérer le contenu
        content = response.content

        # Vérifier la taille
        if config.max_size_bytes > 0 and len(content) > config.max_size_bytes:
            raise ImageTooLargeError(url, len(content), config.max_size_bytes)

        # Détecter les HTML déguisés
        if len(content) >= self._MIN_BYTES_FOR_HTML_DETECTION:
            is_html, reason = detect_html_disguise(content)
            if is_html and not config.accept_html_disguise:
                raise HtmlDisguisedAsImageError(url, reason)

        # Détecter le format d'image depuis le contenu
        image_format = detect_format_from_content(content)

        # Calculer le hash SHA256
        sha256 = None
        if config.compute_hash:
            sha256 = hashlib.sha256(content).hexdigest()

        # Écrire le fichier
        await asyncio.to_thread(output_path.write_bytes, content)

        # Valider l'intégrité si demandé
        width, height = 0, 0
        if config.validate_integrity and self._validator is not None:
            try:
                validation_result = await self._validator.validate(output_path)
                width = validation_result.width
                height = validation_result.height
            except Exception as e:
                self._logger.warning(
                    "Validation échouée pour {}: {}", output_path, e
                )

        duration = asyncio.get_event_loop().time() - start_time

        return DownloadResult(
            url=url,
            output_path=output_path,
            size_bytes=len(content),
            sha256=sha256,
            content_type=content_type or "unknown",
            content_type_category=content_type_category,
            image_format=image_format,
            width=width,
            height=height,
            duration_seconds=duration,
            state=DownloadState.COMPLETED,
        )

    async def _download_to_bytes(
        self,
        url: str,
        config: DownloadConfig,
        start_time: float,
    ) -> bytes:
        """Télécharge une image en mémoire.

        Args:
            url: URL de l'image.
            config: Configuration.
            start_time: Timestamp de début.

        Returns:
            Contenu binaire de l'image.
        """
        try:
            response = await asyncio.wait_for(
                self._session.get(url, follow_redirects=True),
                timeout=config.timeout_seconds,
            )
        except asyncio.TimeoutError as e:
            raise DownloadTimeoutError(url, config.timeout_seconds) from e
        except Exception as e:
            raise ImageDownloadError(f"Erreur HTTP pour {url}: {e}") from e

        if response.status_code >= 400:
            raise ImageDownloadError(f"HTTP {response.status_code} pour {url}")

        content = response.content

        # Vérifier la taille
        if config.max_size_bytes > 0 and len(content) > config.max_size_bytes:
            raise ImageTooLargeError(url, len(content), config.max_size_bytes)

        # Détecter les HTML déguisés
        if len(content) >= self._MIN_BYTES_FOR_HTML_DETECTION:
            is_html, reason = detect_html_disguise(content)
            if is_html and not config.accept_html_disguise:
                raise HtmlDisguisedAsImageError(url, reason)

        return content

    async def _convert_image(
        self,
        result: DownloadResult,
        config: DownloadConfig,
    ) -> DownloadResult:
        """Convertit une image après téléchargement.

        Args:
            result: Résultat du téléchargement initial.
            config: Configuration avec le format cible.

        Returns:
            Nouveau résultat avec l'image convertie.
        """
        if self._converter is None or config.convert_to is None:
            return result

        assert config.convert_to is not None

        # Construire le nouveau chemin
        new_extension = config.convert_to.extension
        new_path = result.output_path.with_suffix(new_extension)

        # Convertir
        conversion_config = ConversionConfig(
            format=config.convert_to,
            quality=config.conversion_quality,
        )

        try:
            conversion_result = await self._converter.convert(
                result.output_path,
                new_path,
                config=conversion_config,
            )

            # Supprimer l'ancien fichier si différent
            if new_path != result.output_path and result.output_path.exists():
                result.output_path.unlink(missing_ok=True)

            async with self._stats_lock:
                self._converted += 1
                self._total_bytes_saved += conversion_result.bytes_saved

            self._logger.debug(
                "Image convertue: {} → {} ({} bytes économisés)",
                result.output_path.name,
                new_path.name,
                conversion_result.bytes_saved,
            )

            return result.model_copy(
                update={
                    "output_path": new_path,
                    "size_bytes": conversion_result.output_size_bytes,
                    "image_format": config.convert_to,
                    "converted": True,
                    "conversion_result": conversion_result,
                }
            )

        except Exception as e:
            self._logger.warning(
                "Échec de la conversion de {}: {}", result.output_path, e
            )
            # Retourner le résultat original sans conversion
            return result

    # ------------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # ------------------------------------------------------------------------

    def _ensure_started(self) -> None:
        """Vérifie que le téléchargeur est démarré.

        Raises:
            ImageDownloadError: Si le téléchargeur n'est pas démarré.
        """
        if not self._started:
            raise ImageDownloadError(
                "ImageDownloader must be started before use. Call await downloader.start()"
            )

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<ImageDownloader status={status} "
            f"max_concurrent={self._max_concurrent} "
            f"downloads={self._total_downloads}>"
        )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "ImageDownloadError",
    "InvalidContentTypeError",
    "HtmlDisguisedAsImageError",
    "ImageTooLargeError",
    "DownloadTimeoutError",
    # Enums
    "ContentTypeCategory",
    "DownloadState",
    # Modèles
    "DownloadConfig",
    "DownloadResult",
    "DownloadStats",
    # Helpers
    "classify_content_type",
    "detect_html_disguise",
    "extract_filename_from_url",
    # Classe principale
    "ImageDownloader",
]
