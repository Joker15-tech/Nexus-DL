"""Empaquetage au format PDF via img2pdf (sans perte de qualité).

Ce module implémente le packager pour le format PDF, utilisé pour la lecture
sur tablettes, l'impression, ou l'archivage long-terme. Il s'appuie sur la
bibliothèque `img2pdf` qui offre une conversion **sans perte** des images
JPEG (pas de ré-encodage), préservant ainsi la qualité originale des scans.

**Avantages de img2pdf** :
    - Conversion sans perte pour les images JPEG (pas de ré-encodage)
    - Préservation des profils ICC et métadonnées EXIF
    - Support natif des dimensions originales des images
    - Génération de PDF optimisés (taille minimale)
    - Support des métadonnées PDF/XMP (titre, auteur, etc.)
    - Très rapide (plus rapide que Pillow pour les gros volumes)

**Formats d'images supportés** :
    - JPEG/JPG : natif (sans perte)
    - PNG : via conversion interne
    - WebP : via conversion interne
    - GIF : via conversion interne
    - BMP : via conversion interne
    - TIFF : via conversion interne

**Structure du PDF généré** :
    chapter.pdf
        ├── Page 1 : page_001.jpg (dimensions originales)
        ├── Page 2 : page_002.jpg
        ├── ...
        └── Métadonnées PDF :
            - Title : titre du chapitre
            - Author : auteur/mangaka
            - Subject : synopsis
            - Keywords : genres, tags
            - Creator : "NexusDL"
            - Producer : "img2pdf"

Architecture :
    PdfPackager (hérite de BasePackager)
        ├── FitMode (enum) : A4, LETTER, ORIGINAL, CUSTOM
        ├── Orientation (enum) : PORTRAIT, LANDSCAPE, AUTO
        ├── Valide les pages et convertit les formats non-JPEG
        ├── Génère les métadonnées PDF depuis ComicInfo
        ├── Crée le PDF via img2pdf (sans perte)
        └── Retourne le chemin final

Exemple d'utilisation :
    >>> from nexusdl.core.packaging.pdf_packager import PdfPackager, FitMode
    >>> from nexusdl.core.packaging.base import PackagingFormat
    >>>
    >>> packager = PdfPackager(fit_mode=FitMode.A4)
    >>> assert packager.supports(PackagingFormat.PDF)
    >>>
    >>> output = await packager.package(
    ...     pages=[Path("p1.jpg"), Path("p2.jpg")],
    ...     output=Path("/downloads/chapter.pdf"),
    ...     metadata=comic_info,
    ... )
    >>> print(f"PDF créé: {output}")
    /downloads/chapter.pdf

Dépendances externes :
    - img2pdf : pip install img2pdf
    - Pillow : pour la conversion des formats non-JPEG (déjà utilisé ailleurs)
"""

from __future__ import annotations

import asyncio
import io
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
# EXCEPTIONS
# ============================================================================


class PdfPackagingError(PackagingFailedError):
    """Exception spécifique aux erreurs d'empaquetage PDF."""


class Img2PdfNotAvailableError(PdfPackagingError):
    """Exception levée lorsque img2pdf n'est pas installé."""

    def __init__(self) -> None:
        super().__init__(
            Path("<pdf>"),
            "La bibliothèque 'img2pdf' n'est pas installée. "
            "Installez-la avec: pip install img2pdf",
        )


# ============================================================================
# ENUMS
# ============================================================================


class FitMode(str, Enum):
    """Mode d'ajustement des pages dans le PDF.

    ORIGINAL : Conserve les dimensions originales des images (pas de redimensionnement).
    A4       : Redimensionne les pages pour remplir une page A4 (210x297mm).
    LETTER   : Redimensionne pour remplir une page US Letter (8.5x11in).
    CUSTOM   : Utilise les dimensions personnalisées fournies.
    """

    ORIGINAL = "original"
    A4 = "a4"
    LETTER = "letter"
    CUSTOM = "custom"

    @property
    def label(self) -> str:
        """Libellé humain du mode."""
        return {
            FitMode.ORIGINAL: "Dimensions originales",
            FitMode.A4: "Format A4 (210×297mm)",
            FitMode.LETTER: "US Letter (8.5×11in)",
            FitMode.CUSTOM: "Format personnalisé",
        }[self]

    @property
    def dimensions_mm(self) -> tuple[float, float] | None:
        """Dimensions en millimètres (width, height) ou None pour ORIGINAL."""
        return {
            FitMode.ORIGINAL: None,
            FitMode.A4: (210.0, 297.0),
            FitMode.LETTER: (215.9, 279.4),
            FitMode.CUSTOM: None,  # À fournir via kwargs
        }[self]


class Orientation(str, Enum):
    """Orientation des pages dans le PDF.

    PORTRAIT  : Hauteur > largeur (défaut pour mangas).
    LANDSCAPE : Largeur > hauteur (pour webtoons).
    AUTO      : Détecte automatiquement selon les dimensions de l'image.
    """

    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    AUTO = "auto"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            Orientation.PORTRAIT: "Portrait",
            Orientation.LANDSCAPE: "Paysage",
            Orientation.AUTO: "Automatique",
        }[self]


# ============================================================================
# CONSTANTES
# ============================================================================


# DPI par défaut pour les pages redimensionnées
_DEFAULT_DPI: Final[int] = 150

# Qualité de conversion pour les images non-JPEG (PNG→JPEG, etc.)
_DEFAULT_JPEG_QUALITY: Final[int] = 95

# Taille max d'un PDF (500 Mo par défaut)
_MAX_PDF_SIZE_BYTES: Final[int] = 500 * 1024 * 1024


# ============================================================================
# HELPERS — Vérification des dépendances
# ============================================================================


def is_img2pdf_available() -> bool:
    """Vérifie si la bibliothèque img2pdf est disponible.

    Returns:
        True si img2pdf est installé et importable.
    """
    try:
        import img2pdf  # noqa: F401
        return True
    except ImportError:
        return False


def get_img2pdf_version() -> str | None:
    """Récupère la version de img2pdf si disponible.

    Returns:
        Chaîne de version ou None si non disponible.
    """
    try:
        import img2pdf
        return getattr(img2pdf, "__version__", "unknown")
    except ImportError:
        return None


# ============================================================================
# CLASSE PRINCIPALE — PdfPackager
# ============================================================================


class PdfPackager(BasePackager):
    """Packager pour le format PDF via img2pdf (sans perte).

    Crée des fichiers PDF optimisés à partir d'une liste d'images, en
    préservant la qualité originale des JPEG et en supportant les métadonnées
    PDF complètes (titre, auteur, synopsis, etc.).

    Attributes:
        format: Format d'empaquetage supporté (PDF).
    """

    format: ClassVar[PackagingFormat] = PackagingFormat.PDF

    def __init__(
        self,
        *,
        fit_mode: FitMode = FitMode.ORIGINAL,
        orientation: Orientation = Orientation.AUTO,
        dpi: int = _DEFAULT_DPI,
        jpeg_quality: int = _DEFAULT_JPEG_QUALITY,
        title: str | None = None,
        author: str | None = None,
        subject: str | None = None,
        keywords: list[str] | None = None,
        creator: str = "NexusDL",
        producer: str = "img2pdf",
        custom_width_mm: float | None = None,
        custom_height_mm: float | None = None,
    ) -> None:
        """Initialise le packager PDF.

        Args:
            fit_mode: Mode d'ajustement des pages (ORIGINAL, A4, LETTER, CUSTOM).
            orientation: Orientation des pages (PORTRAIT, LANDSCAPE, AUTO).
            dpi: Résolution en DPI pour les pages redimensionnées (défaut: 150).
            jpeg_quality: Qualité JPEG pour la conversion des images non-JPEG (1-100).
            title: Titre du PDF (override des métadonnées ComicInfo).
            author: Auteur du PDF (override des métadonnées ComicInfo).
            subject: Sujet du PDF (override des métadonnées ComicInfo).
            keywords: Mots-clés du PDF (override des métadonnées ComicInfo).
            creator: Créateur du PDF (défaut: "NexusDL").
            producer: Producteur du PDF (défaut: "img2pdf").
            custom_width_mm: Largeur personnalisée en mm (si fit_mode=CUSTOM).
            custom_height_mm: Hauteur personnalisée en mm (si fit_mode=CUSTOM).

        Raises:
            ValueError: Si les paramètres sont invalides.
            Img2PdfNotAvailableError: Si img2pdf n'est pas installé.
        """
        if not is_img2pdf_available():
            raise Img2PdfNotAvailableError()

        if not 1 <= dpi <= 1200:
            raise ValueError(f"dpi must be in [1, 1200], got {dpi}")
        if not 1 <= jpeg_quality <= 100:
            raise ValueError(f"jpeg_quality must be in [1, 100], got {jpeg_quality}")
        if fit_mode == FitMode.CUSTOM:
            if custom_width_mm is None or custom_height_mm is None:
                raise ValueError(
                    "custom_width_mm and custom_height_mm are required when fit_mode=CUSTOM"
                )
            if custom_width_mm <= 0 or custom_height_mm <= 0:
                raise ValueError("Custom dimensions must be positive")

        self._fit_mode = fit_mode
        self._orientation = orientation
        self._dpi = dpi
        self._jpeg_quality = jpeg_quality
        self._title = title
        self._author = author
        self._subject = subject
        self._keywords = keywords
        self._creator = creator
        self._producer = producer
        self._custom_width_mm = custom_width_mm
        self._custom_height_mm = custom_height_mm

        # Logger avec contexte
        self._logger = logger.bind(
            module="pdf_packager",
            fit_mode=fit_mode.value,
            orientation=orientation.value,
        )

    # --------------------------------------------------------------------
    # Méthodes abstraites implémentées
    # --------------------------------------------------------------------

    def supports(self, fmt: PackagingFormat) -> bool:
        """Vérifie si ce packager supporte le format PDF.

        Args:
            fmt: Format à vérifier.

        Returns:
            True si fmt est PackagingFormat.PDF.
        """
        return fmt == PackagingFormat.PDF

    async def package(
        self,
        pages: list[Path],
        output: Path,
        *,
        metadata: ComicInfo | None = None,
        **kwargs: Any,
    ) -> Path:
        """Empaquette les pages dans un fichier PDF.

        Args:
            pages: Liste des chemins vers les images à empaqueter.
                   Les pages doivent être triées dans l'ordre de lecture.
            output: Chemin du fichier PDF de destination.
                    Le répertoire parent sera créé si nécessaire.
            metadata: Métadonnées ComicInfo à embarquer (optionnel).
                      Si fourni, les métadonnées PDF seront extraites
                      (titre, auteur, synopsis, genres, etc.).
            **kwargs: Arguments spécifiques (fit_mode, orientation, dpi, etc.).

        Returns:
            Chemin absolu du fichier PDF créé.

        Raises:
            InvalidPagesError: Si la liste de pages est vide ou invalide.
            PackagingFailedError: Si l'empaquetage échoue.
            MetadataError: Si les métadonnées sont invalides.
            Img2PdfNotAvailableError: Si img2pdf n'est pas installé.
            OSError: Si une erreur I/O survient.

        Example:
            >>> packager = PdfPackager(fit_mode=FitMode.A4)
            >>> output = await packager.package(
            ...     pages=[Path("p1.jpg"), Path("p2.jpg")],
            ...     output=Path("/downloads/chapter.pdf"),
            ...     metadata=comic_info,
            ... )
        """
        # Vérifier la disponibilité de img2pdf
        if not is_img2pdf_available():
            raise Img2PdfNotAvailableError()

        # Valider les pages
        self.validate_pages(pages)

        # Créer le répertoire parent
        self.ensure_parent_dir(output)

        # Options depuis kwargs si fournis
        fit_mode = kwargs.get("fit_mode", self._fit_mode)
        orientation = kwargs.get("orientation", self._orientation)
        dpi = kwargs.get("dpi", self._dpi)

        self._logger.info(
            "Début de l'empaquetage PDF: {} pages → {} (fit={}, orient={})",
            len(pages),
            output.name,
            fit_mode.value,
            orientation.value,
        )

        try:
            # 1. Construire les métadonnées PDF
            pdf_metadata = self._build_pdf_metadata(metadata)

            # 2. Préparer les images (conversion des formats non-JPEG)
            prepared_images = await self._prepare_images(pages, dpi)

            # 3. Créer le PDF via img2pdf (dans un thread)
            await asyncio.to_thread(
                self._create_pdf,
                prepared_images,
                output,
                pdf_metadata,
                fit_mode,
                orientation,
                dpi,
            )

            # 4. Vérifier que le PDF a été créé
            if not output.exists():
                raise PackagingFailedError(
                    output,
                    "Le fichier PDF n'a pas été créé (erreur inconnue)",
                )

            # 5. Vérifier la taille
            pdf_size = output.stat().st_size
            if pdf_size > _MAX_PDF_SIZE_BYTES:
                self._logger.warning(
                    "PDF très volumineux: {:.2f} MB (limite recommandée: {} MB)",
                    pdf_size / (1024 * 1024),
                    _MAX_PDF_SIZE_BYTES / (1024 * 1024),
                )

            self._logger.info(
                "Empaquetage PDF terminé: {} pages, {:.2f} MB",
                len(pages),
                pdf_size / (1024 * 1024),
            )

            return output

        except PackagingFailedError:
            raise
        except InvalidPagesError:
            raise
        except MetadataError:
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
    # Méthodes internes — Préparation des images
    # --------------------------------------------------------------------

    async def _prepare_images(
        self,
        pages: list[Path],
        dpi: int,
    ) -> list[bytes]:
        """Prépare les images pour img2pdf.

        Les images JPEG sont utilisées telles quelles (sans perte).
        Les autres formats (PNG, WebP, GIF, BMP, TIFF) sont convertis
        en JPEG via Pillow pour garantir la compatibilité avec img2pdf.

        Args:
            pages: Liste des chemins des pages.
            dpi: Résolution cible pour les conversions.

        Returns:
            Liste des contenus binaires des images (JPEG ou convertis).

        Raises:
            InvalidPagesError: Si une image ne peut pas être préparée.
        """
        prepared: list[bytes] = []
        semaphore = asyncio.Semaphore(4)  # Max 4 conversions simultanées

        async def prepare_one(page_path: Path) -> bytes:
            async with semaphore:
                return await self._prepare_single_image(page_path, dpi)

        # Préparer toutes les images en parallèle
        tasks = [asyncio.create_task(prepare_one(p)) for p in pages]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                raise InvalidPagesError(
                    f"Impossible de préparer la page {i + 1} ({pages[i]}): {result}"
                ) from result
            prepared.append(result)

        return prepared

    async def _prepare_single_image(
        self,
        page_path: Path,
        dpi: int,
    ) -> bytes:
        """Prépare une image individuelle pour img2pdf.

        Si l'image est déjà en JPEG, elle est lue telle quelle (sans perte).
        Sinon, elle est convertie en JPEG via Pillow.

        Args:
            page_path: Chemin de l'image.
            dpi: Résolution cible pour les conversions.

        Returns:
            Contenu binaire de l'image (JPEG).

        Raises:
            InvalidPagesError: Si l'image ne peut pas être préparée.
        """
        try:
            # Lire le contenu brut
            raw_data = await asyncio.to_thread(page_path.read_bytes)

            # Détecter le format via magic bytes
            format_detected = self._detect_image_format(raw_data)

            # Si JPEG natif, retourner tel quel (sans perte)
            if format_detected == "jpeg":
                self._logger.trace(
                    "Page {} conservée en JPEG natif (sans perte)",
                    page_path.name,
                )
                return raw_data

            # Sinon, convertir en JPEG via Pillow
            self._logger.trace(
                "Conversion de {} ({}) → JPEG",
                page_path.name,
                format_detected,
            )

            converted = await asyncio.to_thread(
                self._convert_to_jpeg,
                raw_data,
                self._jpeg_quality,
                dpi,
            )
            return converted

        except Exception as e:
            raise InvalidPagesError(
                f"Impossible de préparer l'image {page_path}: {e}"
            ) from e

    @staticmethod
    def _detect_image_format(data: bytes) -> str:
        """Détecte le format d'une image depuis ses magic bytes.

        Args:
            data: Contenu binaire de l'image (au moins 12 bytes).

        Returns:
            Nom du format détecté ('jpeg', 'png', 'webp', 'gif', 'bmp', 'tiff', 'unknown').
        """
        if len(data) < 12:
            return "unknown"

        # JPEG
        if data[:3] == b"\xff\xd8\xff":
            return "jpeg"
        # PNG
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "png"
        # WebP
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "webp"
        # GIF
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return "gif"
        # BMP
        if data[:2] == b"BM":
            return "bmp"
        # TIFF
        if data[:4] in (b"II\x2a\x00", b"MM\x00\x2a"):
            return "tiff"

        return "unknown"

    @staticmethod
    def _convert_to_jpeg(
        image_data: bytes,
        quality: int,
        dpi: int,
    ) -> bytes:
        """Convertit une image en JPEG via Pillow.

        Args:
            image_data: Contenu binaire de l'image source.
            quality: Qualité JPEG (1-100).
            dpi: Résolution cible.

        Returns:
            Contenu binaire de l'image JPEG convertie.
        """
        from PIL import Image

        with Image.open(io.BytesIO(image_data)) as img:
            # Convertir en RGB si nécessaire (RGBA, P, LA → RGB)
            if img.mode in ("RGBA", "LA", "PA"):
                # Créer un fond blanc et coller l'image dessus
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "RGBA":
                    background.paste(img, mask=img.split()[3])
                else:
                    background.paste(img)
                img = background
            elif img.mode == "P":
                img = img.convert("RGB")
            elif img.mode == "L":
                pass  # Niveaux de gris OK pour JPEG
            elif img.mode != "RGB":
                img = img.convert("RGB")

            # Définir la résolution
            img.info["dpi"] = (dpi, dpi)

            # Sauvegarder en JPEG
            output_buffer = io.BytesIO()
            img.save(
                output_buffer,
                format="JPEG",
                quality=quality,
                optimize=True,
                dpi=(dpi, dpi),
            )
            return output_buffer.getvalue()

    # --------------------------------------------------------------------
    # Méthodes internes — Métadonnées PDF
    # --------------------------------------------------------------------

    def _build_pdf_metadata(
        self,
        metadata: ComicInfo | None,
    ) -> dict[str, str]:
        """Construit les métadonnées PDF à partir de ComicInfo.

        Les métadonnées explicites (title, author, etc.) passées au
        constructeur ont priorité sur celles de ComicInfo.

        Args:
            metadata: Métadonnées ComicInfo (optionnel).

        Returns:
            Dictionnaire des métadonnées PDF.
        """
        pdf_meta: dict[str, str] = {}

        # Métadonnées explicites (priorité haute)
        if self._title:
            pdf_meta["Title"] = self._title
        if self._author:
            pdf_meta["Author"] = self._author
        if self._subject:
            pdf_meta["Subject"] = self._subject
        if self._keywords:
            pdf_meta["Keywords"] = ", ".join(self._keywords)

        pdf_meta["Creator"] = self._creator
        pdf_meta["Producer"] = self._producer

        # Compléter avec ComicInfo si fourni
        if metadata is not None:
            # Titre : explicite > ComicInfo.title > ComicInfo.full_title
            if "Title" not in pdf_meta:
                if metadata.title:
                    pdf_meta["Title"] = metadata.title
                else:
                    pdf_meta["Title"] = metadata.full_title

            # Auteur : explicite > ComicInfo.writer
            if "Author" not in pdf_meta and metadata.writer:
                pdf_meta["Author"] = metadata.writer

            # Sujet : explicite > ComicInfo.summary
            if "Subject" not in pdf_meta and metadata.summary:
                pdf_meta["Subject"] = metadata.summary[:500]  # Limite PDF

            # Mots-clés : explicite > ComicInfo.genres + tags
            if "Keywords" not in pdf_meta:
                all_keywords = list(metadata.genres) + list(metadata.tags)
                if all_keywords:
                    pdf_meta["Keywords"] = ", ".join(all_keywords)

        return pdf_meta

    # --------------------------------------------------------------------
    # Méthodes internes — Création du PDF
    # --------------------------------------------------------------------

    def _create_pdf(
        self,
        images: list[bytes],
        output: Path,
        metadata: dict[str, str],
        fit_mode: FitMode,
        orientation: Orientation,
        dpi: int,
    ) -> None:
        """Crée le PDF via img2pdf (synchrone, exécuté dans un thread).

        Args:
            images: Liste des contenus binaires des images (JPEG).
            output: Chemin du fichier PDF de destination.
            metadata: Métadonnées PDF.
            fit_mode: Mode d'ajustement des pages.
            orientation: Orientation des pages.
            dpi: Résolution cible.

        Raises:
            PackagingFailedError: Si la création échoue.
        """
        try:
            import img2pdf

            # Construire les options de layout
            layout_fun = self._build_layout_function(fit_mode, orientation, dpi)

            # Créer le PDF
            pdf_bytes = img2pdf.convert(
                images,
                layout_fun=layout_fun,
                title=metadata.get("Title"),
                author=metadata.get("Author"),
                subject=metadata.get("Subject"),
                keywords=metadata.get("Keywords"),
                creator=metadata.get("Creator"),
                producer=metadata.get("Producer"),
                creationdate=None,  # Utiliser la date courante
                moddate=None,
                trailer_convertible=True,
            )

            # Écrire le fichier
            output.write_bytes(pdf_bytes)

            self._logger.debug(
                "PDF créé: {} pages, {:.2f} MB",
                len(images),
                len(pdf_bytes) / (1024 * 1024),
            )

        except ImportError as e:
            raise Img2PdfNotAvailableError() from e
        except Exception as e:
            raise PackagingFailedError(
                output,
                f"Erreur lors de la création du PDF: {e}",
            ) from e

    def _build_layout_function(
        self,
        fit_mode: FitMode,
        orientation: Orientation,
        dpi: int,
    ) -> Any:
        """Construit la fonction de layout pour img2pdf.

        Args:
            fit_mode: Mode d'ajustement.
            orientation: Orientation des pages.
            dpi: Résolution cible.

        Returns:
            Fonction de layout img2pdf.
        """
        import img2pdf

        if fit_mode == FitMode.ORIGINAL:
            # Conserver les dimensions originales
            return img2pdf.get_layout_fun(
                pagesize=None,
                imgsize=None,
                fit=img2pdf.Fit.into,
                auto_orient=(orientation == Orientation.AUTO),
            )

        # Déterminer les dimensions de la page
        if fit_mode == FitMode.A4:
            pagesize = img2pdf.mm_to_pt(210), img2pdf.mm_to_pt(297)
        elif fit_mode == FitMode.LETTER:
            pagesize = img2pdf.in_to_pt(8.5), img2pdf.in_to_pt(11)
        elif fit_mode == FitMode.CUSTOM:
            assert self._custom_width_mm is not None
            assert self._custom_height_mm is not None
            pagesize = (
                img2pdf.mm_to_pt(self._custom_width_mm),
                img2pdf.mm_to_pt(self._custom_height_mm),
            )
        else:
            pagesize = None

        return img2pdf.get_layout_fun(
            pagesize=pagesize,
            imgsize=None,
            fit=img2pdf.Fit.into,
            auto_orient=(orientation == Orientation.AUTO),
        )

    # --------------------------------------------------------------------
    # Méthodes publiques — Utilitaires
    # --------------------------------------------------------------------

    @staticmethod
    def list_pdf_pages(pdf_path: Path) -> int:
        """Compte le nombre de pages dans un PDF.

        Args:
            pdf_path: Chemin du fichier PDF.

        Returns:
            Nombre de pages.

        Raises:
            PackagingFailedError: Si le PDF ne peut pas être lu.
        """
        try:
            import pypdf

            with pypdf.PdfReader(pdf_path) as reader:
                return len(reader.pages)
        except ImportError:
            # Fallback : utiliser img2pdf pour compter
            try:
                import img2pdf

                with pdf_path.open("rb") as f:
                    pdf_data = f.read()
                # img2pdf ne permet pas de compter facilement,
                # on utilise une heuristique basée sur /Type /Page
                return pdf_data.count(b"/Type /Page") - pdf_data.count(b"/Type /Pages")
            except Exception as e:
                raise PackagingFailedError(
                    pdf_path,
                    f"Impossible de compter les pages: {e}",
                ) from e
        except Exception as e:
            raise PackagingFailedError(
                pdf_path,
                f"Impossible de lire le PDF: {e}",
            ) from e

    @staticmethod
    def extract_pdf_metadata(pdf_path: Path) -> dict[str, str]:
        """Extrait les métadonnées d'un PDF.

        Args:
            pdf_path: Chemin du fichier PDF.

        Returns:
            Dictionnaire des métadonnées.

        Raises:
            PackagingFailedError: Si le PDF ne peut pas être lu.
        """
        try:
            import pypdf

            with pypdf.PdfReader(pdf_path) as reader:
                info = reader.metadata
                if info is None:
                    return {}
                return {
                    k: str(v)
                    for k, v in info.items()
                    if v is not None
                }
        except ImportError:
            return {}
        except Exception as e:
            raise PackagingFailedError(
                pdf_path,
                f"Impossible d'extraire les métadonnées: {e}",
            ) from e

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def fit_mode(self) -> FitMode:
        """Mode d'ajustement actuel."""
        return self._fit_mode

    @property
    def orientation(self) -> Orientation:
        """Orientation actuelle."""
        return self._orientation

    @property
    def dpi(self) -> int:
        """Résolution actuelle en DPI."""
        return self._dpi

    # --------------------------------------------------------------------
    # Représentation
    # --------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<PdfPackager format={self.format.value} "
            f"fit={self._fit_mode.value} "
            f"orient={self._orientation.value} "
            f"dpi={self._dpi}>"
        )


# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================


def is_valid_pdf(pdf_path: Path) -> bool:
    """Vérifie si un fichier est un PDF valide.

    Args:
        pdf_path: Chemin du fichier à vérifier.

    Returns:
        True si c'est un PDF valide.
    """
    try:
        # Vérifier les magic bytes
        with pdf_path.open("rb") as f:
            header = f.read(5)
        if header != b"%PDF-":
            return False

        # Vérifier la fin du fichier (%%EOF)
        with pdf_path.open("rb") as f:
            f.seek(-32, 2)  # 32 bytes avant la fin
            tail = f.read()
        return b"%%EOF" in tail

    except Exception:
        return False


def get_pdf_page_count(pdf_path: Path) -> int:
    """Compte le nombre de pages dans un PDF.

    Wrapper autour de PdfPackager.list_pdf_pages pour usage externe.

    Args:
        pdf_path: Chemin du PDF.

    Returns:
        Nombre de pages, ou 0 si erreur.
    """
    try:
        return PdfPackager.list_pdf_pages(pdf_path)
    except Exception:
        return 0


def get_pdf_installation_instructions() -> str:
    """Retourne les instructions d'installation pour le support PDF.

    Returns:
        Chaîne de texte avec les instructions.
    """
    return """
Pour utiliser le format PDF, vous devez installer les dépendances suivantes :

**Dépendance principale (obligatoire)** :
    pip install img2pdf

**Dépendance optionnelle (pour lecture de PDF)** :
    pip install pypdf

**Installation complète** :
    pip install img2pdf pypdf

**Vérification** :
    python -c "import img2pdf; print(img2pdf.__version__)"

Après installation, redémarrez NexusDL pour détecter les bibliothèques.
""".strip()


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "PdfPackagingError",
    "Img2PdfNotAvailableError",
    # Enums
    "FitMode",
    "Orientation",
    # Classe principale
    "PdfPackager",
    # Helpers
    "is_img2pdf_available",
    "get_img2pdf_version",
    "is_valid_pdf",
    "get_pdf_page_count",
    "get_pdf_installation_instructions",
]
