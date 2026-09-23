"""Module public des utilitaires NexusDL.

Ce module constitue le point d'entrée unique pour tous les utilitaires
généraux du projet. Il agrège et ré-exporte les symboles publics des
6 sous-modules :

    - `url.py`           : Manipulation et validation d'URLs
    - `time.py`          : Gestion des dates, heures et durées
    - `text.py`          : Manipulation et normalisation de texte
    - `hash.py`          : Fonctions de hachage cryptographique
    - `filesystem.py`    : Opérations sur fichiers et répertoires
    - `async_helpers.py` : Utilitaires asyncio et patterns de concurrence

Architecture :
    Utilitaires d'URL
        ├── normalize_url(), resolve_url(), parse_url()
        ├── extract_domain(), extract_path_segments()
        ├── is_valid_url(), match_url_pattern()
        └── UrlPattern, ParsedUrl

    Utilitaires de temps
        ├── now(), parse_datetime(), format_datetime()
        ├── format_duration(), parse_duration()
        ├── time_ago(), time_until()
        └── TimeUnit, DateFormat, Duration

    Utilitaires de texte
        ├── slugify(), sanitize_filename()
        ├── extract_numbers(), extract_chapter_number()
        ├── format_size(), format_number()
        ├── similarity(), fuzzy_match()
        └── TextStats, TextCase

    Utilitaires de hachage
        ├── hash_string(), hash_file(), hash_directory()
        ├── verify_file(), compare_hashes()
        ├── dedup_hash(), file_dedup_hash()
        └── HashAlgorithm, HashResult, IncrementalHasher

    Utilitaires filesystem
        ├── read_text(), write_text(), read_json(), write_json()
        ├── atomic_write(), copy_file(), move_file(), delete_file()
        ├── ensure_dir(), list_files(), get_file_stats()
        └── FileStats, DirectoryStats, DiskUsage

    Utilitaires async
        ├── run_with_timeout(), retry_async()
        ├── gather_with_limit(), gather_with_timeout()
        ├── PriorityAsyncQueue(), RateLimiter(), CircuitBreaker()
        └── TaskState, BackoffStrategy, AsyncTaskTracker

Règles d'or :
    1. Ce fichier n'expose QUE les symboles publics stables.
    2. Il ne dépend d'aucun module de `interfaces/` ni de `parsers/`.
    3. Tous les exports sont typés strictement et documentés.
    4. Les imports sont organisés par sous-module pour la lisibilité.
    5. Les conflits de noms sont résolus par des aliases explicites.
    6. Les fonctions sont pures et thread-safe.
    7. Les opérations I/O ont des versions async (_async suffix).

Exemple d'utilisation — URLs :
    >>> from nexusdl.core.utils import normalize_url, resolve_url, extract_domain
    >>>
    >>> # Normaliser une URL
    >>> normalize_url("HTTPS://Example.COM/path/")
    'https://example.com/path'
    >>>
    >>> # Résoudre une URL relative
    >>> resolve_url("/chapter/123", "https://mangadex.org/title/456")
    'https://mangadex.org/chapter/123'
    >>>
    >>> # Extraire le domaine
    >>> extract_domain("https://cdn.mangadex.org/data/image.jpg")
    'mangadex.org'

Exemple d'utilisation — Temps :
    >>> from nexusdl.core.utils import now, format_datetime, parse_duration
    >>>
    >>> # Heure actuelle
    >>> current = now()
    >>> print(current)
    2026-09-23 14:30:45+00:00
    >>>
    >>> # Formater pour affichage
    >>> format_datetime(current, style="human")
    '23 septembre 2026 à 14:30'
    >>>
    >>> # Parser une durée
    >>> duration = parse_duration("2h 30m")
    >>> print(duration.total_seconds())
    9000.0

Exemple d'utilisation — Texte :
    >>> from nexusdl.core.utils import slugify, extract_chapter_number, similarity
    >>>
    >>> # Slugification
    >>> slugify("One Piece - Chapter 123: Adventure!")
    'one-piece-chapter-123-adventure'
    >>>
    >>> # Extraction de numéro de chapitre
    >>> extract_chapter_number("One Piece - Chapter 123.5")
    123.5
    >>>
    >>> # Similarité de chaînes
    >>> similarity("One Piece", "One Peace")
    0.88

Exemple d'utilisation — Hachage :
    >>> from nexusdl.core.utils import hash_file, verify_file, HashAlgorithm
    >>>
    >>> # Hacher un fichier
    >>> file_hash = hash_file(Path("image.jpg"), algorithm=HashAlgorithm.SHA256)
    >>> print(file_hash)
    'b5bb9d8014a0f9b1d61e21e796d78dccdf1352f23cd32812f4850b878ae4944c'
    >>>
    >>> # Vérifier l'intégrité
    >>> verify_file(Path("image.jpg"), expected_hash=file_hash)
    True

Exemple d'utilisation — Filesystem :
    >>> from nexusdl.core.utils import atomic_write, read_json, list_files
    >>>
    >>> # Écriture atomique
    >>> atomic_write(Path("config.yaml"), "new content")
    >>>
    >>> # Lire du JSON
    >>> data = read_json(Path("data.json"))
    >>>
    >>> # Lister les fichiers
    >>> files = list_files(Path("/path"), pattern="*.jpg", recursive=True)

Exemple d'utilisation — Async :
    >>> from nexusdl.core.utils import retry_async, gather_with_limit, RateLimiter
    >>>
    >>> # Retry avec backoff exponentiel
    >>> result = await retry_async(
    ...     fetch_data,
    ...     "url",
    ...     max_retries=3,
    ...     backoff_strategy=BackoffStrategy.EXPONENTIAL,
    ... )
    >>>
    >>> # Limiter la concurrence
    >>> results = await gather_with_limit(
    ...     [download(url) for url in urls],
    ...     limit=5,
    ... )
    >>>
    >>> # Rate limiting
    >>> limiter = RateLimiter(rate=10.0)  # 10 appels/seconde
    >>> await limiter.acquire()
    >>> await some_api_call()
"""

from __future__ import annotations

# ============================================================================
# UTILITAIRES D'URL — url.py
# ============================================================================

# Exceptions
from nexusdl.core.utils.url import (
    InvalidUrlError,
    UrlError,
    UrlParseError,
    UrlPatternError,
)

# Enums
from nexusdl.core.utils.url import UrlMatchType, UrlScheme

# Modèles
from nexusdl.core.utils.url import ParsedUrl, UrlPattern, UrlPatternMatch

# Validation
from nexusdl.core.utils.url import (
    is_valid_domain,
    is_valid_url,
    validate_url,
)

# Normalisation
from nexusdl.core.utils.url import (
    ensure_scheme,
    normalize_url,
    strip_query_and_fragment,
    strip_trailing_slash,
)

# Résolution
from nexusdl.core.utils.url import (
    get_base_url,
    get_parent_url,
    resolve_url,
)

# Extraction
from nexusdl.core.utils.url import (
    extract_domain,
    extract_extension,
    extract_filename_from_url,
    extract_id_from_url,
    extract_path_segments,
    extract_query_params,
    extract_root_domain,
    parse_url,
)

# Query parameters
from nexusdl.core.utils.url import (
    add_query_params,
    get_query_param,
    remove_query_params,
    set_query_param,
)

# Construction
from nexusdl.core.utils.url import (
    build_absolute_url,
    build_url,
    join_urls,
)

# Comparaison
from nexusdl.core.utils.url import (
    is_same_domain,
    is_same_host,
    is_subdomain_of,
    match_any_pattern,
    match_url_pattern,
    urls_are_equivalent,
)

# Conversion
from nexusdl.core.utils.url import (
    sanitize_filename as url_sanitize_filename,  # Alias pour éviter conflit
    slugify as url_slugify,  # Alias pour éviter conflit
    url_to_filename,
)

# Encodage
from nexusdl.core.utils.url import (
    decode_url,
    encode_idn_domain,
    encode_url,
)

# Détection
from nexusdl.core.utils.url import (
    is_absolute_url,
    is_blob_url,
    is_cdn_url,
    is_data_url,
    is_image_url,
    is_protocol_relative_url,
    is_relative_url,
)

# Itération
from nexusdl.core.utils.url import (
    iter_domains,
    iter_urls,
)

# Helpers avancés
from nexusdl.core.utils.url import (
    fingerprint_url,
    get_common_prefix,
    get_url_depth,
    guess_url_purpose,
    simplify_url,
)

# ============================================================================
# UTILITAIRES DE TEMPS — time.py
# ============================================================================

# Exceptions
from nexusdl.core.utils.time import (
    InvalidDateTimeError,
    InvalidDurationError,
    TimeError,
    TimezoneError,
)

# Enums
from nexusdl.core.utils.time import DateFormat, TimeUnit

# Modèles
from nexusdl.core.utils.time import Duration, TimeRange

# Heure actuelle
from nexusdl.core.utils.time import (
    now,
    timestamp,
    timestamp_ms,
    today,
    utc_now,
)

# Conversions
from nexusdl.core.utils.time import (
    to_date,
    to_datetime,
    to_iso,
    to_timestamp,
    to_timestamp_ms,
)

# Parsing
from nexusdl.core.utils.time import (
    parse_date,
    parse_datetime,
    parse_duration,
)

# Formatage
from nexusdl.core.utils.time import (
    format_datetime,
    format_duration,
    format_relative,
    format_timestamp,
)

# Manipulation
from nexusdl.core.utils.time import (
    add_duration,
    end_of_day,
    end_of_month,
    end_of_week,
    end_of_year,
    start_of_day,
    start_of_month,
    start_of_week,
    start_of_year,
    subtract_duration,
)

# Comparaisons
from nexusdl.core.utils.time import (
    is_after,
    is_before,
    is_between,
    is_expired,
    is_same_day,
    is_today,
    is_tomorrow,
    is_yesterday,
)

# Différences
from nexusdl.core.utils.time import (
    time_ago,
    time_diff,
    time_diff_seconds,
    time_until,
)

# Timezones
from nexusdl.core.utils.time import (
    get_timezone_offset,
    to_timezone,
    to_utc,
)

# Validation
from nexusdl.core.utils.time import (
    is_valid_datetime,
    is_valid_duration,
    is_valid_timezone,
)

# ============================================================================
# UTILITAIRES DE TEXTE — text.py
# ============================================================================

# Exceptions
from nexusdl.core.utils.text import (
    EncodingError,
    InvalidTextError,
    TextError,
)

# Enums
from nexusdl.core.utils.text import TextCase, TruncationPosition, UnicodeNormalization

# Modèles
from nexusdl.core.utils.text import TextStats

# Nettoyage et normalisation
from nexusdl.core.utils.text import (
    ascii_fold,
    normalize_unicode,
    normalize_whitespace,
    remove_accents,
    remove_control_chars,
    strip_html,
)

# Slugification et sanitization
from nexusdl.core.utils.text import (
    sanitize_filename as text_sanitize_filename,  # Alias pour éviter conflit
    sanitize_for_display,
    slugify as text_slugify,  # Alias pour éviter conflit
)

# Extraction
from nexusdl.core.utils.text import (
    extract_chapter_number,
    extract_emails,
    extract_hashtags,
    extract_integers,
    extract_mentions,
    extract_numbers as text_extract_numbers,  # Alias pour éviter conflit
    extract_urls,
    extract_volume_number,
    extract_words,
)

# Formatage
from nexusdl.core.utils.text import (
    format_number as text_format_number,  # Alias pour éviter conflit
    format_percentage,
    format_size,
    pad,
    truncate,
    wrap_text,
)

# Comparaison et matching
from nexusdl.core.utils.text import (
    contains_all,
    contains_any,
    fuzzy_match,
    similarity,
)

# Conversion de cas
from nexusdl.core.utils.text import (
    to_camel_case,
    to_case,
    to_kebab_case,
    to_pascal_case,
    to_snake_case,
)

# Analyse
from nexusdl.core.utils.text import (
    count_lines,
    count_words,
    get_most_common_words,
    get_text_stats,
)

# Détection
from nexusdl.core.utils.text import (
    detect_script,
    has_emoji,
    is_ascii,
)

# ============================================================================
# UTILITAIRES DE HACHAGE — hash.py
# ============================================================================

# Exceptions
from nexusdl.core.utils.hash import (
    HashError,
    HashMismatchError,
    InvalidAlgorithmError,
    InvalidHashError,
)

# Enums
from nexusdl.core.utils.hash import HashAlgorithm

# Modèles
from nexusdl.core.utils.hash import DirectoryHashResult, HashResult

# Classe
from nexusdl.core.utils.hash import IncrementalHasher

# Hachage de strings/bytes
from nexusdl.core.utils.hash import (
    hash_bytes,
    hash_string,
    hash_with_salt,
)

# Hachage de fichiers
from nexusdl.core.utils.hash import (
    hash_file,
    hash_file_async,
    hash_file_with_result,
    hash_file_with_result_async,
    hash_stream,
)

# Hachage de répertoires
from nexusdl.core.utils.hash import (
    hash_directory,
    hash_directory_async,
)

# Vérification
from nexusdl.core.utils.hash import (
    verify_file,
    verify_file_async,
    verify_file_strict,
    verify_hash,
)

# Comparaison et validation
from nexusdl.core.utils.hash import (
    compare_hashes,
    is_valid_hash,
    validate_hash,
)

# Déduplication
from nexusdl.core.utils.hash import (
    dedup_hash,
    file_dedup_hash,
    file_dedup_hash_async,
)

# Checksums
from nexusdl.core.utils.hash import (
    checksum_directory,
    checksum_file,
)

# Helpers
from nexusdl.core.utils.hash import (
    generate_random_salt,
    get_algorithm_info,
    list_algorithms,
)

# ============================================================================
# UTILITAIRES FILESYSTEM — filesystem.py
# ============================================================================

# Exceptions
from nexusdl.core.utils.filesystem import (
    AtomicOperationError,
    DirectoryExistsError,
    DirectoryNotFoundError,
    DiskFullError,
    FileExistsError,
    FileNotFoundError as FsFileNotFoundError,  # Alias pour éviter conflit avec builtin
    FilesystemError,
    InvalidPathError,
    PermissionError as FsPermissionError,  # Alias pour éviter conflit avec builtin
)

# Enums
from nexusdl.core.utils.filesystem import FileType, SortBy

# Modèles
from nexusdl.core.utils.filesystem import DirectoryStats, DiskUsage, FileStats

# Validation
from nexusdl.core.utils.filesystem import (
    is_safe_path,
    sanitize_filename as fs_sanitize_filename,  # Alias pour éviter conflit
    validate_path,
)

# Lecture
from nexusdl.core.utils.filesystem import (
    read_bytes,
    read_bytes_async,
    read_json,
    read_json_async,
    read_lines,
    read_text,
    read_text_async,
    read_yaml,
    read_yaml_async,
)

# Écriture
from nexusdl.core.utils.filesystem import (
    append_text,
    append_text_async,
    write_bytes,
    write_bytes_async,
    write_json,
    write_json_async,
    write_text,
    write_text_async,
    write_yaml,
    write_yaml_async,
)

# Opérations atomiques
from nexusdl.core.utils.filesystem import (
    atomic_move,
    atomic_move_async,
    atomic_write,
    atomic_write_async,
)

# Copie, déplacement, suppression
from nexusdl.core.utils.filesystem import (
    copy_directory,
    copy_directory_async,
    copy_file,
    copy_file_async,
    delete_directory,
    delete_directory_async,
    delete_file,
    delete_file_async,
    move_directory,
    move_directory_async,
    move_file,
    move_file_async,
)

# Répertoires
from nexusdl.core.utils.filesystem import (
    ensure_dir,
    ensure_dir_async,
    get_directory_tree,
    list_directories,
    list_files,
    list_files_async,
)

# Informations
from nexusdl.core.utils.filesystem import (
    get_directory_stats,
    get_directory_stats_async,
    get_file_size,
    get_file_size_async,
    get_file_stats,
    get_file_stats_async,
)

# Espace disque
from nexusdl.core.utils.filesystem import (
    check_disk_space,
    ensure_disk_space,
    get_disk_usage,
    get_disk_usage_async,
)

# Fichiers temporaires
from nexusdl.core.utils.filesystem import (
    create_temp_dir,
    create_temp_file,
    temp_dir,
    temp_file,
)

# Permissions
from nexusdl.core.utils.filesystem import (
    make_executable,
    make_readonly,
    set_permissions,
    set_permissions_async,
)

# Helpers
from nexusdl.core.utils.filesystem import (
    dir_exists,
    file_exists,
    get_unique_filename,
    is_empty_dir,
)

# ============================================================================
# UTILITAIRES ASYNC — async_helpers.py
# ============================================================================

# Exceptions
from nexusdl.core.utils.async_helpers import (
    AsyncError,
    CircuitBreakerOpenError,
    QueueFullError,
    RetryExhaustedError,
    TimeoutError as AsyncTimeoutError,  # Alias pour éviter conflit avec builtin
)

# Enums
from nexusdl.core.utils.async_helpers import BackoffStrategy, TaskState

# Modèles
from nexusdl.core.utils.async_helpers import AsyncStats, TaskInfo

# Gestion des tâches
from nexusdl.core.utils.async_helpers import (
    gather_with_limit,
    gather_with_timeout,
    retry_async,
    run_with_timeout,
)

# Synchronisation
from nexusdl.core.utils.async_helpers import AsyncLockWithTimeout, AsyncSemaphore

# Files d'attente
from nexusdl.core.utils.async_helpers import BoundedAsyncQueue, PriorityAsyncQueue

# Patterns de concurrence
from nexusdl.core.utils.async_helpers import CircuitBreaker, RateLimiter

# Helpers divers
from nexusdl.core.utils.async_helpers import (
    debounce,
    run_sync,
    sleep_until,
    throttle,
    to_thread,
)

# Monitoring
from nexusdl.core.utils.async_helpers import AsyncTaskTracker

# Context managers
from nexusdl.core.utils.async_helpers import semaphore_context, timeout_context

# ============================================================================
# FONCTIONS PRINCIPALES — Résolution des conflits de noms
# ============================================================================

# slugify : utiliser celui de text.py (plus généraliste)
slugify = text_slugify

# sanitize_filename : utiliser celui de filesystem.py (plus complet)
sanitize_filename = fs_sanitize_filename

# extract_numbers : utiliser celui de text.py (plus généraliste)
extract_numbers = text_extract_numbers

# format_number : utiliser celui de text.py (plus flexible)
format_number = text_format_number

# ============================================================================
# EXPORTS PUBLICS
# ============================================================================

__all__ = [
    # ========================================================================
    # UTILITAIRES D'URL
    # ========================================================================
    # Exceptions
    "UrlError",
    "InvalidUrlError",
    "UrlParseError",
    "UrlPatternError",
    # Enums
    "UrlScheme",
    "UrlMatchType",
    # Modèles
    "ParsedUrl",
    "UrlPattern",
    "UrlPatternMatch",
    # Validation
    "is_valid_url",
    "is_valid_domain",
    "validate_url",
    # Normalisation
    "normalize_url",
    "ensure_scheme",
    "strip_trailing_slash",
    "strip_query_and_fragment",
    # Résolution
    "resolve_url",
    "get_base_url",
    "get_parent_url",
    # Extraction
    "parse_url",
    "extract_domain",
    "extract_root_domain",
    "extract_path_segments",
    "extract_query_params",
    "extract_id_from_url",
    "extract_extension",
    "extract_filename_from_url",
    # Query parameters
    "add_query_params",
    "remove_query_params",
    "get_query_param",
    "set_query_param",
    # Construction
    "build_url",
    "join_urls",
    "build_absolute_url",
    # Comparaison
    "is_same_domain",
    "is_same_host",
    "is_subdomain_of",
    "urls_are_equivalent",
    "match_url_pattern",
    "match_any_pattern",
    # Conversion
    "url_to_filename",
    "url_slugify",
    "url_sanitize_filename",
    # Encodage
    "encode_url",
    "decode_url",
    "encode_idn_domain",
    # Détection
    "is_image_url",
    "is_data_url",
    "is_blob_url",
    "is_protocol_relative_url",
    "is_absolute_url",
    "is_relative_url",
    "is_cdn_url",
    # Itération
    "iter_urls",
    "iter_domains",
    # Helpers avancés
    "get_url_depth",
    "get_common_prefix",
    "fingerprint_url",
    "simplify_url",
    "guess_url_purpose",
    # ========================================================================
    # UTILITAIRES DE TEMPS
    # ========================================================================
    # Exceptions
    "TimeError",
    "InvalidDateTimeError",
    "InvalidDurationError",
    "TimezoneError",
    # Enums
    "TimeUnit",
    "DateFormat",
    # Modèles
    "TimeRange",
    "Duration",
    # Heure actuelle
    "now",
    "utc_now",
    "today",
    "timestamp",
    "timestamp_ms",
    # Conversions
    "to_datetime",
    "to_timestamp",
    "to_timestamp_ms",
    "to_iso",
    "to_date",
    # Parsing
    "parse_datetime",
    "parse_date",
    "parse_duration",
    # Formatage
    "format_datetime",
    "format_relative",
    "format_duration",
    "format_timestamp",
    # Manipulation
    "add_duration",
    "subtract_duration",
    "start_of_day",
    "end_of_day",
    "start_of_week",
    "end_of_week",
    "start_of_month",
    "end_of_month",
    "start_of_year",
    "end_of_year",
    # Comparaisons
    "is_before",
    "is_after",
    "is_between",
    "is_expired",
    "is_today",
    "is_yesterday",
    "is_tomorrow",
    "is_same_day",
    # Différences
    "time_diff",
    "time_diff_seconds",
    "time_ago",
    "time_until",
    # Timezones
    "to_timezone",
    "to_utc",
    "get_timezone_offset",
    # Validation
    "is_valid_datetime",
    "is_valid_duration",
    "is_valid_timezone",
    # ========================================================================
    # UTILITAIRES DE TEXTE
    # ========================================================================
    # Exceptions
    "TextError",
    "InvalidTextError",
    "EncodingError",
    # Enums
    "UnicodeNormalization",
    "TextCase",
    "TruncationPosition",
    # Modèles
    "TextStats",
    # Nettoyage et normalisation
    "normalize_unicode",
    "normalize_whitespace",
    "remove_control_chars",
    "remove_accents",
    "ascii_fold",
    "strip_html",
    # Slugification et sanitization
    "slugify",
    "text_slugify",
    "sanitize_filename",
    "fs_sanitize_filename",
    "text_sanitize_filename",
    "sanitize_for_display",
    # Extraction
    "extract_numbers",
    "text_extract_numbers",
    "extract_integers",
    "extract_chapter_number",
    "extract_volume_number",
    "extract_words",
    "extract_hashtags",
    "extract_mentions",
    "extract_urls",
    "extract_emails",
    # Formatage
    "format_size",
    "format_number",
    "text_format_number",
    "format_percentage",
    "truncate",
    "pad",
    "wrap_text",
    # Comparaison et matching
    "similarity",
    "fuzzy_match",
    "contains_any",
    "contains_all",
    # Conversion de cas
    "to_case",
    "to_camel_case",
    "to_pascal_case",
    "to_snake_case",
    "to_kebab_case",
    # Analyse
    "get_text_stats",
    "count_words",
    "count_lines",
    "get_most_common_words",
    # Détection
    "detect_script",
    "is_ascii",
    "has_emoji",
    # ========================================================================
    # UTILITAIRES DE HACHAGE
    # ========================================================================
    # Exceptions
    "HashError",
    "InvalidAlgorithmError",
    "FsFileNotFoundError",
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
    # ========================================================================
    # UTILITAIRES FILESYSTEM
    # ========================================================================
    # Exceptions
    "FilesystemError",
    "FsFileNotFoundError",
    "DirectoryNotFoundError",
    "FileExistsError",
    "DirectoryExistsError",
    "FsPermissionError",
    "InvalidPathError",
    "DiskFullError",
    "AtomicOperationError",
    # Enums
    "FileType",
    "SortBy",
    # Modèles
    "FileStats",
    "DirectoryStats",
    "DiskUsage",
    # Validation
    "validate_path",
    "is_safe_path",
    "fs_sanitize_filename",
    # Lecture
    "read_text",
    "read_text_async",
    "read_bytes",
    "read_bytes_async",
    "read_json",
    "read_json_async",
    "read_yaml",
    "read_yaml_async",
    "read_lines",
    # Écriture
    "write_text",
    "write_text_async",
    "write_bytes",
    "write_bytes_async",
    "write_json",
    "write_json_async",
    "write_yaml",
    "write_yaml_async",
    "append_text",
    "append_text_async",
    # Opérations atomiques
    "atomic_write",
    "atomic_write_async",
    "atomic_move",
    "atomic_move_async",
    # Copie, déplacement, suppression
    "copy_file",
    "copy_file_async",
    "move_file",
    "move_file_async",
    "delete_file",
    "delete_file_async",
    "delete_directory",
    "delete_directory_async",
    "copy_directory",
    "copy_directory_async",
    "move_directory",
    "move_directory_async",
    # Répertoires
    "ensure_dir",
    "ensure_dir_async",
    "list_files",
    "list_files_async",
    "list_directories",
    "get_directory_tree",
    # Informations
    "get_file_stats",
    "get_file_stats_async",
    "get_directory_stats",
    "get_directory_stats_async",
    "get_file_size",
    "get_file_size_async",
    # Espace disque
    "get_disk_usage",
    "get_disk_usage_async",
    "check_disk_space",
    "ensure_disk_space",
    # Fichiers temporaires
    "create_temp_file",
    "create_temp_dir",
    "temp_file",
    "temp_dir",
    # Permissions
    "set_permissions",
    "set_permissions_async",
    "make_executable",
    "make_readonly",
    # Helpers
    "file_exists",
    "dir_exists",
    "is_empty_dir",
    "get_unique_filename",
    # ========================================================================
    # UTILITAIRES ASYNC
    # ========================================================================
    # Exceptions
    "AsyncError",
    "AsyncTimeoutError",
    "RetryExhaustedError",
    "QueueFullError",
    "CircuitBreakerOpenError",
    # Enums
    "TaskState",
    "BackoffStrategy",
    # Modèles
    "TaskInfo",
    "AsyncStats",
    # Gestion des tâches
    "run_with_timeout",
    "retry_async",
    "gather_with_limit",
    "gather_with_timeout",
    # Synchronisation
    "AsyncSemaphore",
    "AsyncLockWithTimeout",
    # Files d'attente
    "PriorityAsyncQueue",
    "BoundedAsyncQueue",
    # Patterns de concurrence
    "RateLimiter",
    "CircuitBreaker",
    # Helpers divers
    "sleep_until",
    "debounce",
    "throttle",
    "run_sync",
    "to_thread",
    # Monitoring
    "AsyncTaskTracker",
    # Context managers
    "timeout_context",
    "semaphore_context",
]

__version__: str = "0.1.0"
