"""Module public des modèles de domaine NexusDL.

Ce module constitue le point d'entrée unique pour tous les modèles de domaine
du projet. Il agrège et ré-exporte les symboles publics des 5 sous-modules :

    - `manga.py`    : Manga, Chapter, Page, SearchResult + enums
    - `download.py` : DownloadTask, DownloadResult, DownloadProgress + enums
    - `library.py`  : ReadingProgress, ReadingList, ReadingSession + enums
    - `site.py`     : SiteConfig, SiteCapabilities, SiteHealth + enums
    - `user.py`     : User, UserProfile, AuthToken, Session, ApiKey + enums

Architecture :
    Modèles immuables (frozen=True) :
        Manga, Chapter, Page, SearchResult,
        DownloadResult, DownloadProgress, DownloadStats, QueuePosition, DownloadHistoryEntry,
        ReadingList, ReadingListManga, ReadingSession, LibraryStats, ReadingActivity, ContinueReadingEntry,
        DomainInfo, SiteCapabilities, SiteConfig, SiteHealth, SiteStats, SiteOverview,
        UserProfile, AuthToken, Session, ApiKey

    Modèles mutables (état évolutif) :
        DownloadTask   → status évolue (PENDING → RUNNING → COMPLETED)
        ReadingProgress → reading_status évolue (PLAN_TO_READ → READING → COMPLETED)
        User           → profil évolutif (mot de passe, email, rôle, dernière connexion)

Règles d'or :
    1. Ce fichier n'expose QUE les symboles publics stables.
    2. Il ne dépend d'aucun module de `interfaces/`, `parsers/` ou `core/downloader/`.
    3. Tous les exports sont typés strictement et documentés.
    4. Les imports sont organisés par sous-module puis par catégorie.
    5. Les conflits de noms entre sous-modules sont résolus par des aliases explicites.

Exemple d'utilisation :
    >>> from nexusdl.core.models import (
    ...     # Modèles principaux
    ...     Manga, Chapter, Page, SearchResult,
    ...     DownloadTask, DownloadResult, DownloadProgress,
    ...     ReadingProgress, ReadingList, ReadingSession, LibraryStats,
    ...     SiteConfig, SiteCapabilities, SiteHealth,
    ...     User, UserProfile, AuthToken, Session, ApiKey,
    ...     # Enums
    ...     MangaStatus, Language, ContentRating,
    ...     Priority, DownloadStatus,
    ...     ReadingStatus,
    ...     SiteStatus,
    ...     UserRole, AuthMethod,
    ...     # Helpers
    ...     generate_manga_id, hash_password, verify_password,
    ... )
    >>>
    >>> # Créer un manga
    >>> manga = Manga(
    ...     source_id="one-piece",
    ...     site="mangadex",
    ...     title="One Piece",
    ...     status=MangaStatus.ONGOING,
    ...     language=Language.JA,
    ...     content_rating=ContentRating.SAFE,
    ...     url="https://mangadex.org/title/one-piece",
    ... )
    >>> print(manga.id)  # Auto-généré: 'manga_a1b2c3d4...'
    >>>
    >>> # Créer une tâche de téléchargement
    >>> task = DownloadTask(
    ...     manga=manga,
    ...     chapters=[chapter],
    ...     dest=Path("/downloads"),
    ...     priority=Priority.HIGH,
    ... )
    >>> task.mark_as_running()
    >>> print(task.status)  # DownloadStatus.RUNNING
    >>>
    >>> # Créer un utilisateur
    >>> user = User.create(
    ...     email="user@example.com",
    ...     username="johndoe",
    ...     password="Secure_Pass_123",
    ...     role=UserRole.USER,
    ... )
    >>> assert verify_password("Secure_Pass_123", user.password_hash)
"""

from __future__ import annotations

# ============================================================================
# EXCEPTIONS — Hiérarchie complète
# ============================================================================

# manga.py
from nexusdl.core.models.manga import (
    InvalidChapterError,
    InvalidMangaError,
    InvalidPageError,
    MangaModelError,
)
# download.py
from nexusdl.core.models.download import (
    DownloadModelError,
    InvalidTaskStateError,
    TaskValidationError,
)
# library.py
from nexusdl.core.models.library import (
    InvalidReadingStatusError,
    LibraryModelError,
    ListValidationError,
)
# site.py
from nexusdl.core.models.site import (
    InvalidDomainError,
    InvalidParserClassError,
    InvalidSiteConfigError,
    SiteModelError,
)
# user.py
from nexusdl.core.models.user import (
    AccountDisabledError,
    AuthenticationError,
    InvalidCredentialsError,
    InvalidEmailError,
    InvalidUsernameError,
    SessionExpiredError,
    TokenExpiredError,
    UserModelError,
    WeakPasswordError,
)

# ============================================================================
# ENUMS — États, configurations et classifications
# ============================================================================

# manga.py
from nexusdl.core.models.manga import (
    ContentRating,
    Demographic,
    Language,
    MangaStatus,
    ReadingDirection,
)
# download.py
from nexusdl.core.models.download import (
    DownloadStatus,
    Priority,
)
# library.py
from nexusdl.core.models.library import (
    ListVisibility,
    ReadingStatus,
    SortOrder,
)
# site.py
from nexusdl.core.models.site import (
    DomainRole,
    ProxyRequirement,
    SiteStatus,
)
# user.py
from nexusdl.core.models.user import (
    ApiKeyPermission,
    AuthMethod,
    SessionStatus,
    TokenType,
    UserRole,
)

# ============================================================================
# MODÈLES PYDANTIC — Manga / Chapter / Page
# ============================================================================

from nexusdl.core.models.manga import (
    Chapter,
    Manga,
    Page,
    SearchResult,
)

# ============================================================================
# MODÈLES PYDANTIC — Téléchargement
# ============================================================================

from nexusdl.core.models.download import (
    DownloadHistoryEntry,
    DownloadProgress,
    DownloadResult,
    DownloadStats,
    DownloadTask,
    PageDownloadResult,
    QueuePosition,
)

# ============================================================================
# MODÈLES PYDANTIC — Bibliothèque
# ============================================================================

from nexusdl.core.models.library import (
    ContinueReadingEntry,
    LibraryStats,
    ReadingActivity,
    ReadingList,
    ReadingListManga,
    ReadingProgress,
    ReadingSession,
)

# ============================================================================
# MODÈLES PYDANTIC — Sites
# ============================================================================

from nexusdl.core.models.site import (
    DomainInfo,
    SiteCapabilities,
    SiteConfig,
    SiteHealth,
    SiteOverview,
    SiteStats,
)

# ============================================================================
# MODÈLES PYDANTIC — Utilisateurs et authentification
# ============================================================================

from nexusdl.core.models.user import (
    ApiKey,
    AuthToken,
    Session,
    User,
    UserProfile,
)

# ============================================================================
# HELPERS — Génération d'identifiants
# ============================================================================

from nexusdl.core.models.manga import (
    generate_chapter_id,
    generate_manga_id,
    generate_page_id,
)
from nexusdl.core.models.download import (
    generate_download_id,
    generate_task_id,
)
from nexusdl.core.models.library import (
    generate_reading_list_id,
    generate_session_id as generate_reading_session_id,
)
from nexusdl.core.models.user import (
    generate_api_key,
    generate_session_id as generate_auth_session_id,
    generate_user_id,
)

# ============================================================================
# HELPERS — Hachage et validation (user.py)
# ============================================================================

from nexusdl.core.models.user import (
    hash_password,
    mask_api_key,
    mask_secret,
    validate_email,
    validate_password_strength,
    validate_username,
    verify_password,
)

# ============================================================================
# HELPERS — Validation de sites (site.py)
# ============================================================================

from nexusdl.core.models.site import (
    extract_class_name,
    extract_module_path,
    validate_parser_class,
    validate_site_id,
)

# ============================================================================
# EXPORTS PUBLICS
# ============================================================================

__all__ = [
    # ========================================================================
    # Classes principales — Modèles de domaine
    # ========================================================================
    # Manga / Chapter / Page
    "Manga",
    "Chapter",
    "Page",
    "SearchResult",
    # Téléchargement
    "DownloadTask",
    "DownloadResult",
    "PageDownloadResult",
    "DownloadProgress",
    "DownloadStats",
    "QueuePosition",
    "DownloadHistoryEntry",
    # Bibliothèque
    "ReadingProgress",
    "ReadingList",
    "ReadingListManga",
    "ReadingSession",
    "LibraryStats",
    "ReadingActivity",
    "ContinueReadingEntry",
    # Sites
    "SiteConfig",
    "SiteCapabilities",
    "DomainInfo",
    "SiteHealth",
    "SiteStats",
    "SiteOverview",
    # Utilisateurs et authentification
    "User",
    "UserProfile",
    "AuthToken",
    "Session",
    "ApiKey",
    # ========================================================================
    # Enums — Manga
    # ========================================================================
    "MangaStatus",
    "Language",
    "ContentRating",
    "Demographic",
    "ReadingDirection",
    # ========================================================================
    # Enums — Téléchargement
    # ========================================================================
    "Priority",
    "DownloadStatus",
    # ========================================================================
    # Enums — Bibliothèque
    # ========================================================================
    "ReadingStatus",
    "ListVisibility",
    "SortOrder",
    # ========================================================================
    # Enums — Sites
    # ========================================================================
    "SiteStatus",
    "ProxyRequirement",
    "DomainRole",
    # ========================================================================
    # Enums — Utilisateurs
    # ========================================================================
    "UserRole",
    "AuthMethod",
    "SessionStatus",
    "TokenType",
    "ApiKeyPermission",
    # ========================================================================
    # Helpers — Génération d'identifiants
    # ========================================================================
    "generate_manga_id",
    "generate_chapter_id",
    "generate_page_id",
    "generate_task_id",
    "generate_download_id",
    "generate_reading_list_id",
    "generate_reading_session_id",
    "generate_auth_session_id",
    "generate_user_id",
    "generate_api_key",
    # ========================================================================
    # Helpers — Hachage et validation (user)
    # ========================================================================
    "hash_password",
    "verify_password",
    "validate_password_strength",
    "validate_email",
    "validate_username",
    "mask_api_key",
    "mask_secret",
    # ========================================================================
    # Helpers — Validation de sites
    # ========================================================================
    "validate_site_id",
    "validate_parser_class",
    "extract_module_path",
    "extract_class_name",
    # ========================================================================
    # Exceptions — Manga
    # ========================================================================
    "MangaModelError",
    "InvalidMangaError",
    "InvalidChapterError",
    "InvalidPageError",
    # ========================================================================
    # Exceptions — Téléchargement
    # ========================================================================
    "DownloadModelError",
    "InvalidTaskStateError",
    "TaskValidationError",
    # ========================================================================
    # Exceptions — Bibliothèque
    # ========================================================================
    "LibraryModelError",
    "InvalidReadingStatusError",
    "ListValidationError",
    # ========================================================================
    # Exceptions — Sites
    # ========================================================================
    "SiteModelError",
    "InvalidSiteConfigError",
    "InvalidDomainError",
    "InvalidParserClassError",
    # ========================================================================
    # Exceptions — Utilisateurs
    # ========================================================================
    "UserModelError",
    "AuthenticationError",
    "InvalidCredentialsError",
    "AccountDisabledError",
    "TokenExpiredError",
    "SessionExpiredError",
    "InvalidEmailError",
    "InvalidUsernameError",
    "WeakPasswordError",
]

__version__: str = "0.1.0"
