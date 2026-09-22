"""Module public du système d'empaquetage NexusDL.

Ce module constitue le point d'entrée de la couche d'empaquetage dans
l'architecture hexagonale. Il expose l'API publique stable utilisée par
le `DownloadWorker` pour empaqueter les chapitres téléchargés dans le
format cible (CBZ, CBR, PDF, ZIP, FOLDER), ainsi que le modèle de
métadonnées ComicInfo (standard ComicRack v2.1).

Pipeline d'empaquetage :
    DownloadWorker
        │
        ▼ pages téléchargées + ComicInfo
    PackagerFactory.create(format)
        │
        ▼ BasePackager instance
    packager.package(pages, output, metadata=comic_info)
        │
        ▼ fichier/archive créé(e)
    PackagingResult (immutable)

Formats supportés :
    - CBZ    : Comic Book ZIP (RECOMMANDÉ, stdlib zipfile)
    - CBR    : Comic Book RAR (nécessite outil externe `rar`)
    - PDF    : Document PDF via img2pdf (SANS PERTE pour JPEG)
    - ZIP    : Archive ZIP standard (usage général)
    - FOLDER : Dossier d'images brutes (3 modes : COPY, MOVE, LINK)

Architecture :
    BasePackager (ABC)
        ├── CbzPackager    (format recommandé, stdlib zipfile)
        ├── CbrPackager    (nécessite outil externe `rar`)
        ├── PdfPackager    (via img2pdf, sans perte JPEG)
        ├── ZipPackager    (usage général, stdlib zipfile)
        └── FolderPackager (copie/déplacement, 3 modes)

    PackagerFactory
        └── create(PackagingFormat) → BasePackager instance

    ComicInfo (Pydantic)
        ├── to_xml() → str (sérialisation)
        ├── from_xml() → ComicInfo (parsing)
        └── from_manga_and_chapter() → ComicInfo (conversion)

Règles d'or :
    1. Ce fichier n'expose QUE les symboles publics stables.
    2. Il ne dépend d'aucun module de `interfaces/` ni de `parsers/`.
    3. Tous les exports sont typés strictement et documentés.
    4. Les imports sont organisés par sous-module pour la lisibilité.
    5. CBZ est le format RECOMMANDÉ — pas de dépendance externe.
    6. CBR et PDF nécessitent des outils/librairies externes (vérification préalable).

Exemple d'utilisation — Via la factory (recommandé) :
    >>> from pathlib import Path
    >>> from nexusdl.core.packaging import (
    ...     PackagerFactory,
    ...     PackagingFormat,
    ...     ComicInfo,
    ... )
    >>>
    >>> factory = PackagerFactory()
    >>> packager = factory.create(PackagingFormat.CBZ)
    >>>
    >>> output = await packager.package(
    ...     pages=[Path("p1.jpg"), Path("p2.jpg")],
    ...     output=Path("/downloads/chapter.cbz"),
    ...     metadata=comic_info,
    ... )
    >>> print(f"Archive créée: {output}")

Exemple d'utilisation — Directement :
    >>> from nexusdl.core.packaging import CbzPackager, PdfPackager
    >>>
    >>> # CBZ (recommandé)
    >>> cbz = CbzPackager(compression_level=6)
    >>> await cbz.package(pages, output, metadata=comic_info)
    >>>
    >>> # PDF (sans perte)
    >>> pdf = PdfPackager(fit_mode=FitMode.A4)
    >>> await pdf.package(pages, output, metadata=comic_info)
    >>>
    >>> # FOLDER (debugging)
    >>> from nexusdl.core.packaging import FolderPackager, FolderMode
    >>> folder = FolderPackager(mode=FolderMode.COPY)
    >>> await folder.package(pages, output_dir, metadata=comic_info)

Exemple d'utilisation — Vérification préalable :
    >>> from nexusdl.core.packaging import (
    ...     is_cbr_supported,
    ...     is_img2pdf_available,
    ... )
    >>>
    >>> if not is_cbr_supported():
    ...     print("CBR non disponible, utiliser CBZ à la place")
    >>>
    >>> if not is_img2pdf_available():
    ...     print("PDF non disponible, installer img2pdf")
"""

from __future__ import annotations

# ============================================================================
# EXCEPTIONS — Hiérarchie complète
# ============================================================================

# base.py
from nexusdl.core.packaging.base import (
    InvalidPagesError,
    MetadataError,
    PackagingError,
    PackagingFailedError,
    UnsupportedFormatError,
)
# comic_info.py
from nexusdl.core.packaging.comic_info import (
    ComicInfoError,
    ComicInfoParseError,
    InvalidComicInfoError,
)
# cbr_packager.py
# (pas d'exceptions spécifiques, utilise PackagingFailedError)
# pdf_packager.py
from nexusdl.core.packaging.pdf_packager import (
    Img2PdfNotAvailableError,
    PdfPackagingError,
)
# zip_packager.py
from nexusdl.core.packaging.zip_packager import ZipPackagingError

# ============================================================================
# ENUMS — Formats, modes et classifications
# ============================================================================

# base.py
from nexusdl.core.packaging.base import PackagingFormat
# comic_info.py
from nexusdl.core.packaging.comic_info import (
    AgeRating,
    ComicPageType,
    FormatType,
    MangaType,
)
# folder_packager.py
from nexusdl.core.packaging.folder_packager import FolderMode
# pdf_packager.py
from nexusdl.core.packaging.pdf_packager import FitMode, Orientation

# ============================================================================
# MODÈLES PYDANTIC — Métadonnées et résultats
# ============================================================================

# base.py
from nexusdl.core.packaging.base import PackagingResult, PackagingStats
# comic_info.py
from nexusdl.core.packaging.comic_info import ComicInfo, ComicPage

# ============================================================================
# CLASSES PRINCIPALES — Packagers
# ============================================================================

# base.py
from nexusdl.core.packaging.base import BasePackager, PackagerFactory
# cbz_packager.py
from nexusdl.core.packaging.cbz_packager import CbzPackager
# cbr_packager.py
from nexusdl.core.packaging.cbr_packager import CbrPackager
# folder_packager.py
from nexusdl.core.packaging.folder_packager import FolderPackager
# pdf_packager.py
from nexusdl.core.packaging.pdf_packager import PdfPackager
# zip_packager.py
from nexusdl.core.packaging.zip_packager import ZipPackager

# ============================================================================
# HELPERS — Détection de disponibilité
# ============================================================================

# cbr_packager.py
from nexusdl.core.packaging.cbr_packager import (
    get_cbr_installation_instructions,
    is_cbr_supported,
)
# pdf_packager.py
from nexusdl.core.packaging.pdf_packager import (
    get_img2pdf_version,
    get_pdf_installation_instructions,
    is_img2pdf_available,
)

# ============================================================================
# HELPERS — Validation d'archives
# ============================================================================

# cbz_packager.py
from nexusdl.core.packaging.cbz_packager import (
    get_cbz_page_count,
    is_valid_cbz,
)
# folder_packager.py
from nexusdl.core.packaging.folder_packager import (
    count_folder_pages,
    is_folder_packaged,
)
# pdf_packager.py
from nexusdl.core.packaging.pdf_packager import (
    get_pdf_page_count,
    is_valid_pdf,
)
# zip_packager.py
from nexusdl.core.packaging.zip_packager import (
    get_zip_file_count,
    get_zip_total_size,
    is_valid_zip,
)

# ============================================================================
# HELPERS — ComicInfo
# ============================================================================

# comic_info.py
from nexusdl.core.packaging.comic_info import (
    merge_comic_info,
    parse_comic_info_from_archive,
)

# ============================================================================
# EXPORTS PUBLICS
# ============================================================================

__all__ = [
    # === Classes principales — Packagers ===
    "BasePackager",
    "PackagerFactory",
    "CbzPackager",
    "CbrPackager",
    "PdfPackager",
    "ZipPackager",
    "FolderPackager",
    # === Enums — Formats et modes ===
    "PackagingFormat",
    "AgeRating",
    "MangaType",
    "ComicPageType",
    "FormatType",
    "FolderMode",
    "FitMode",
    "Orientation",
    # === Modèles — Métadonnées ===
    "ComicInfo",
    "ComicPage",
    # === Modèles — Résultats ===
    "PackagingResult",
    "PackagingStats",
    # === Helpers — Disponibilité ===
    "is_cbr_supported",
    "get_cbr_installation_instructions",
    "is_img2pdf_available",
    "get_img2pdf_version",
    "get_pdf_installation_instructions",
    # === Helpers — Validation d'archives ===
    "is_valid_cbz",
    "get_cbz_page_count",
    "is_valid_zip",
    "get_zip_file_count",
    "get_zip_total_size",
    "is_valid_pdf",
    "get_pdf_page_count",
    "is_folder_packaged",
    "count_folder_pages",
    # === Helpers — ComicInfo ===
    "parse_comic_info_from_archive",
    "merge_comic_info",
    # === Exceptions — Base ===
    "PackagingError",
    "UnsupportedFormatError",
    "PackagingFailedError",
    "InvalidPagesError",
    "MetadataError",
    # === Exceptions — ComicInfo ===
    "ComicInfoError",
    "InvalidComicInfoError",
    "ComicInfoParseError",
    # === Exceptions — PDF ===
    "PdfPackagingError",
    "Img2PdfNotAvailableError",
    # === Exceptions — ZIP ===
    "ZipPackagingError",
]

__version__: str = "0.1.0"
