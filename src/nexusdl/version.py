"""Informations de version et métadonnées du projet NexusDL.

Ce module centralise toutes les informations de version du projet NexusDL.
Il est utilisé par :

    - Le système de mise à jour pour comparer les versions
    - L'interface utilisateur pour afficher la version
    - Les logs pour identifier la version en cours d'exécution
    - Les outils de packaging pour générer les distributions
    - L'API REST pour exposer la version dans les endpoints

**Contenu** :
    - Version actuelle (string, tuple, composants)
    - Métadonnées du projet (nom, auteur, licence, URL)
    - Informations de build (date, commit, branche)
    - Fonctions utilitaires pour la gestion de version
    - Comparaison de versions (pour les mises à jour)

**Exemples d'utilisation** :
    >>> from nexusdl.version import VERSION, VERSION_INFO
    >>> print(VERSION)
    '0.1.0'
    >>> print(VERSION_INFO)
    (0, 1, 0)
    >>>
    >>> from nexusdl.version import is_version_compatible
    >>> is_version_compatible("0.1.0")
    True
    >>>
    >>> from nexusdl.version import get_version_info
    >>> info = get_version_info()
    >>> print(info["version"])
    '0.1.0'

Intégration :
    - core/constants.py : importe APP_VERSION depuis ce module
    - core/config.py : utilise APP_VERSION pour la configuration
    - interfaces/* : affiche la version dans l'UI
    - updater/* : compare les versions pour les mises à jour
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Final, NamedTuple


# ============================================================================
# VERSION — Composants et formats
# ============================================================================


# Version actuelle (format SemVer)
VERSION: Final[str] = "0.1.0"

# Composants de version
VERSION_MAJOR: Final[int] = 0
VERSION_MINOR: Final[int] = 1
VERSION_PATCH: Final[int] = 0

# Version pré-release (None si version stable)
VERSION_PRE_RELEASE: Final[str | None] = None

# Version de build (None si version officielle)
VERSION_BUILD: Final[str | None] = None

# Tuple de version (pour comparaison)
VERSION_INFO: Final[tuple[int, int, int]] = (VERSION_MAJOR, VERSION_MINOR, VERSION_PATCH)

# Version complète avec pré-release et build
VERSION_FULL: Final[str] = (
    f"{VERSION}"
    + (f"-{VERSION_PRE_RELEASE}" if VERSION_PRE_RELEASE else "")
    + (f"+{VERSION_BUILD}" if VERSION_BUILD else "")
)


# ============================================================================
# MÉTADONNÉES DU PROJET
# ============================================================================


# Nom du projet
PROJECT_NAME: Final[str] = "NexusDL"

# Nom court (pour CLI, logs)
PROJECT_SHORT_NAME: Final[str] = "nexusdl"

# Description courte
PROJECT_DESCRIPTION: Final[str] = "Advanced manga, webtoon, and comics downloader"

# Description longue
PROJECT_LONG_DESCRIPTION: Final[str] = (
    "NexusDL is an advanced downloader for manga, webtoons, and comics. "
    "It supports 60+ sites, provides complete library management, "
    "and offers modern interfaces (CLI, Web, GUI)."
)

# Tagline
PROJECT_TAGLINE: Final[str] = "Your manga, your library, your way."

# Auteur principal
PROJECT_AUTHOR: Final[str] = "NexusDL Team"

# Email de contact
PROJECT_AUTHOR_EMAIL: Final[str] = "contact@nexusdl.dev"

# URL du site web
PROJECT_URL: Final[str] = "https://nexusdl.dev"

# URL du dépôt source
PROJECT_REPOSITORY: Final[str] = "https://github.com/nexusdl/nexusdl"

# URL de la documentation
PROJECT_DOCUMENTATION: Final[str] = "https://docs.nexusdl.dev"

# URL du rapport de bugs
PROJECT_BUG_TRACKER: Final[str] = "https://github.com/nexusdl/nexusdl/issues"

# URL de la licence
PROJECT_LICENSE_URL: Final[str] = "https://github.com/nexusdl/nexusdl/blob/main/LICENSE"

# Type de licence
PROJECT_LICENSE: Final[str] = "MIT"

# Copyright
PROJECT_COPYRIGHT: Final[str] = f"Copyright © 2024-{datetime.now(UTC).year} {PROJECT_AUTHOR}"

# Mots-clés
PROJECT_KEYWORDS: Final[list[str]] = [
    "manga",
    "webtoon",
    "comics",
    "downloader",
    "library",
    "reader",
    "cbz",
    "cbr",
    "pdf",
]

# Classificateurs PyPI
PROJECT_CLASSIFIERS: Final[list[str]] = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "Environment :: Web Environment",
    "Intended Audience :: End Users/Desktop",
    "License :: OSI Approved :: MIT License",
    "Natural Language :: English",
    "Natural Language :: French",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Internet :: WWW/HTTP",
    "Topic :: Multimedia :: Graphics",
    "Topic :: Utilities",
    "Typing :: Typed",
]

# Plateformes supportées
PROJECT_PLATFORMS: Final[list[str]] = [
    "Windows",
    "Linux",
    "macOS",
]


# ============================================================================
# INFORMATIONS DE BUILD — Remplies par CI/CD
# ============================================================================


# Date de build (ISO 8601)
BUILD_DATE: Final[str] = "2024-01-01T00:00:00Z"

# Hash du commit Git
BUILD_COMMIT: Final[str] = "unknown"

# Branche Git
BUILD_BRANCH: Final[str] = "unknown"

# Tag Git (si applicable)
BUILD_TAG: Final[str | None] = None

# Numéro de build (pour CI/CD)
BUILD_NUMBER: Final[int | None] = None

# Environnement de build
BUILD_ENVIRONMENT: Final[str] = "development"

# Python utilisé pour le build
BUILD_PYTHON_VERSION: Final[str] = "3.12.0"


# ============================================================================
# COMPATIBILITÉ — Versions minimum requises
# ============================================================================


# Version minimum de Python
PYTHON_MIN_VERSION: Final[tuple[int, int]] = (3, 11)

# Version recommandée de Python
PYTHON_RECOMMENDED_VERSION: Final[tuple[int, int]] = (3, 12)

# Version maximum de Python supportée
PYTHON_MAX_VERSION: Final[tuple[int, int]] = (3, 13)

# Chaîne de version Python pour affichage
PYTHON_VERSION_STRING: Final[str] = f"{PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}+"

# Versions minimum des dépendances principales
DEPENDENCY_VERSIONS: Final[dict[str, str]] = {
    "httpx": ">=0.27.0",
    "pydantic": ">=2.5.0",
    "loguru": ">=0.7.0",
    "pyyaml": ">=6.0.0",
    "orjson": ">=3.9.0",
    "pillow": ">=10.0.0",
    "aiosqlite": ">=0.19.0",
    "jsonschema": ">=4.20.0",
    "tenacity": ">=8.2.0",
    "platformdirs": ">=4.0.0",
    "cryptography": ">=41.0.0",
}

# Dépendances optionnelles
OPTIONAL_DEPENDENCIES: Final[dict[str, str]] = {
    "playwright": ">=1.40.0",
    "img2pdf": ">=0.5.0",
    "rarfile": ">=4.1",
    "pikepdf": ">=8.0.0",
    "rich": ">=13.0.0",
    "textual": ">=0.40.0",
    "fastapi": ">=0.100.0",
    "uvicorn": ">=0.24.0",
    "pyqt6": ">=6.5.0",
}


# ============================================================================
# CLASSES — Structures de données
# ============================================================================


class VersionInfo(NamedTuple):
    """Informations de version structurées.

    Attributes:
        major: Version majeure (breaking changes).
        minor: Version mineure (nouvelles fonctionnalités).
        patch: Version patch (corrections de bugs).
        pre_release: Tag pré-release (alpha, beta, rc).
        build: Métadonnées de build.
    """

    major: int
    minor: int
    patch: int
    pre_release: str | None = None
    build: str | None = None

    @property
    def version_tuple(self) -> tuple[int, int, int]:
        """Tuple de version (major, minor, patch)."""
        return (self.major, self.minor, self.patch)

    @property
    def version_string(self) -> str:
        """Version au format string (SemVer)."""
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.pre_release:
            base += f"-{self.pre_release}"
        if self.build:
            base += f"+{self.build}"
        return base

    @property
    def is_stable(self) -> bool:
        """Indique si c'est une version stable (pas de pré-release)."""
        return self.pre_release is None

    @property
    def is_prerelease(self) -> bool:
        """Indique si c'est une pré-release."""
        return self.pre_release is not None

    def __str__(self) -> str:
        """Représentation en chaîne."""
        return self.version_string

    def __repr__(self) -> str:
        """Représentation détaillée."""
        return f"VersionInfo({self.version_string!r})"


class ProjectInfo:
    """Informations complètes sur le projet.

    Regroupe toutes les métadonnées du projet en un seul objet.
    """

    def __init__(self) -> None:
        """Initialise les informations du projet."""
        self.name = PROJECT_NAME
        self.short_name = PROJECT_SHORT_NAME
        self.version = VERSION
        self.version_info = VersionInfo(
            major=VERSION_MAJOR,
            minor=VERSION_MINOR,
            patch=VERSION_PATCH,
            pre_release=VERSION_PRE_RELEASE,
            build=VERSION_BUILD,
        )
        self.description = PROJECT_DESCRIPTION
        self.long_description = PROJECT_LONG_DESCRIPTION
        self.tagline = PROJECT_TAGLINE
        self.author = PROJECT_AUTHOR
        self.author_email = PROJECT_AUTHOR_EMAIL
        self.url = PROJECT_URL
        self.repository = PROJECT_REPOSITORY
        self.documentation = PROJECT_DOCUMENTATION
        self.bug_tracker = PROJECT_BUG_TRACKER
        self.license = PROJECT_LICENSE
        self.license_url = PROJECT_LICENSE_URL
        self.copyright = PROJECT_COPYRIGHT
        self.keywords = PROJECT_KEYWORDS
        self.classifiers = PROJECT_CLASSIFIERS
        self.platforms = PROJECT_PLATFORMS

    def to_dict(self) -> dict[str, Any]:
        """Convertit en dictionnaire.

        Returns:
            Dictionnaire avec toutes les informations.
        """
        return {
            "name": self.name,
            "short_name": self.short_name,
            "version": self.version,
            "version_full": VERSION_FULL,
            "version_info": {
                "major": self.version_info.major,
                "minor": self.version_info.minor,
                "patch": self.version_info.patch,
                "pre_release": self.version_info.pre_release,
                "build": self.version_info.build,
            },
            "description": self.description,
            "long_description": self.long_description,
            "tagline": self.tagline,
            "author": self.author,
            "author_email": self.author_email,
            "url": self.url,
            "repository": self.repository,
            "documentation": self.documentation,
            "bug_tracker": self.bug_tracker,
            "license": self.license,
            "license_url": self.license_url,
            "copyright": self.copyright,
            "keywords": self.keywords,
            "platforms": self.platforms,
        }

    def __repr__(self) -> str:
        """Représentation en chaîne."""
        return f"<ProjectInfo {self.name} v{self.version}>"


# Instance globale
PROJECT_INFO: Final[ProjectInfo] = ProjectInfo()


# ============================================================================
# FONCTIONS — Utilitaires de version
# ============================================================================


def parse_version(version_string: str) -> VersionInfo:
    """Parse une chaîne de version en VersionInfo.

    Supporte le format SemVer : MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]

    Args:
        version_string: Chaîne de version à parser.

    Returns:
        Instance de VersionInfo.

    Raises:
        ValueError: Si le format est invalide.

    Example:
        >>> parse_version("1.2.3")
        VersionInfo(major=1, minor=2, patch=3)
        >>> parse_version("1.2.3-alpha.1")
        VersionInfo(major=1, minor=2, patch=3, pre_release='alpha.1')
        >>> parse_version("1.2.3+build.123")
        VersionInfo(major=1, minor=2, patch=3, build='build.123')
    """
    # Pattern SemVer
    pattern = re.compile(
        r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
        r"(?:-(?P<pre_release>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
        r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
    )

    match = pattern.match(version_string)
    if not match:
        raise ValueError(f"Format de version invalide: {version_string!r}")

    return VersionInfo(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
        pre_release=match.group("pre_release"),
        build=match.group("build"),
    )


def compare_versions(version1: str, version2: str) -> int:
    """Compare deux versions.

    Args:
        version1: Première version.
        version2: Deuxième version.

    Returns:
        -1 si version1 < version2
         0 si version1 == version2
         1 si version1 > version2

    Example:
        >>> compare_versions("1.0.0", "1.0.1")
        -1
        >>> compare_versions("1.0.0", "1.0.0")
        0
        >>> compare_versions("1.0.1", "1.0.0")
        1
    """
    v1 = parse_version(version1)
    v2 = parse_version(version2)

    # Comparer les composants majeurs
    if v1.major != v2.major:
        return -1 if v1.major < v2.major else 1

    # Comparer les composants mineurs
    if v1.minor != v2.minor:
        return -1 if v1.minor < v2.minor else 1

    # Comparer les composants patch
    if v1.patch != v2.patch:
        return -1 if v1.patch < v2.patch else 1

    # Versions stables > pré-releases
    if v1.pre_release is None and v2.pre_release is not None:
        return 1
    if v1.pre_release is not None and v2.pre_release is None:
        return -1

    # Comparer les pré-releases (ordre lexicographique)
    if v1.pre_release and v2.pre_release:
        if v1.pre_release < v2.pre_release:
            return -1
        if v1.pre_release > v2.pre_release:
            return 1

    return 0


def is_version_compatible(
    required_version: str,
    current_version: str | None = None,
) -> bool:
    """Vérifie si la version actuelle est compatible avec une version requise.

    La compatibilité suit les règles SemVer :
        - Même version majeure
        - Version mineure >= version requise
        - Ou version patch >= version requise si même mineure

    Args:
        required_version: Version requise.
        current_version: Version actuelle (défaut: VERSION).

    Returns:
        True si compatible.

    Example:
        >>> is_version_compatible("0.1.0", "0.1.5")
        True
        >>> is_version_compatible("0.2.0", "0.1.5")
        False
        >>> is_version_compatible("1.0.0", "1.2.3")
        True
    """
    if current_version is None:
        current_version = VERSION

    required = parse_version(required_version)
    current = parse_version(current_version)

    # Versions majeures différentes = incompatible
    if required.major != current.major:
        return False

    # Version mineure actuelle >= version mineure requise
    if current.minor > required.minor:
        return True

    # Même version mineure, vérifier le patch
    if current.minor == required.minor:
        return current.patch >= required.patch

    return False


def get_version_info() -> dict[str, Any]:
    """Retourne toutes les informations de version.

    Returns:
        Dictionnaire avec version, build, métadonnées, etc.

    Example:
        >>> info = get_version_info()
        >>> print(info["version"])
        '0.1.0'
    """
    return {
        "version": VERSION,
        "version_full": VERSION_FULL,
        "version_info": {
            "major": VERSION_MAJOR,
            "minor": VERSION_MINOR,
            "patch": VERSION_PATCH,
            "pre_release": VERSION_PRE_RELEASE,
            "build": VERSION_BUILD,
        },
        "project": PROJECT_INFO.to_dict(),
        "build": {
            "date": BUILD_DATE,
            "commit": BUILD_COMMIT,
            "branch": BUILD_BRANCH,
            "tag": BUILD_TAG,
            "number": BUILD_NUMBER,
            "environment": BUILD_ENVIRONMENT,
            "python_version": BUILD_PYTHON_VERSION,
        },
        "compatibility": {
            "python_min": PYTHON_VERSION_STRING,
            "python_recommended": f"{PYTHON_RECOMMENDED_VERSION[0]}.{PYTHON_RECOMMENDED_VERSION[1]}",
        },
    }


def get_user_agent() -> str:
    """Retourne le User-Agent HTTP pour les requêtes.

    Returns:
        Chaîne User-Agent.

    Example:
        >>> get_user_agent()
        'NexusDL/0.1.0 (+https://nexusdl.dev)'
    """
    return f"{PROJECT_NAME}/{VERSION} (+{PROJECT_URL})"


def format_version_long() -> str:
    """Formate la version pour affichage détaillé.

    Returns:
        Chaîne multi-lignes avec toutes les informations.

    Example:
        >>> print(format_version_long())
        NexusDL v0.1.0
        Build: 2024-01-01T00:00:00Z (unknown)
        Python: 3.11+
        License: MIT
    """
    lines = [
        f"{PROJECT_NAME} v{VERSION}",
    ]

    if VERSION_FULL != VERSION:
        lines.append(f"Full version: {VERSION_FULL}")

    lines.extend([
        f"Build: {BUILD_DATE} ({BUILD_COMMIT[:8] if BUILD_COMMIT != 'unknown' else 'unknown'})",
        f"Branch: {BUILD_BRANCH}",
        f"Python: {PYTHON_VERSION_STRING}",
        f"License: {PROJECT_LICENSE}",
        f"Copyright: {PROJECT_COPYRIGHT}",
    ])

    return "\n".join(lines)


def format_version_short() -> str:
    """Formate la version pour affichage court.

    Returns:
        Chaîne courte.

    Example:
        >>> format_version_short()
        'NexusDL v0.1.0'
    """
    return f"{PROJECT_NAME} v{VERSION}"


def check_python_version() -> bool:
    """Vérifie si la version de Python est compatible.

    Returns:
        True si la version est >= PYTHON_MIN_VERSION.

    Example:
        >>> check_python_version()
        True
    """
    import sys

    current = sys.version_info[:2]
    return current >= PYTHON_MIN_VERSION


def get_python_version_warning() -> str | None:
    """Retourne un message d'avertissement si Python n'est pas compatible.

    Returns:
        Message d'avertissement ou None si compatible.
    """
    if not check_python_version():
        import sys

        current = f"{sys.version_info.major}.{sys.version_info.minor}"
        required = PYTHON_VERSION_STRING
        return (
            f"Python {required} or higher is required. "
            f"You are using Python {current}. "
            f"Please upgrade Python to use {PROJECT_NAME}."
        )
    return None


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Version
    "VERSION",
    "VERSION_MAJOR",
    "VERSION_MINOR",
    "VERSION_PATCH",
    "VERSION_PRE_RELEASE",
    "VERSION_BUILD",
    "VERSION_INFO",
    "VERSION_FULL",
    # Métadonnées
    "PROJECT_NAME",
    "PROJECT_SHORT_NAME",
    "PROJECT_DESCRIPTION",
    "PROJECT_LONG_DESCRIPTION",
    "PROJECT_TAGLINE",
    "PROJECT_AUTHOR",
    "PROJECT_AUTHOR_EMAIL",
    "PROJECT_URL",
    "PROJECT_REPOSITORY",
    "PROJECT_DOCUMENTATION",
    "PROJECT_BUG_TRACKER",
    "PROJECT_LICENSE_URL",
    "PROJECT_LICENSE",
    "PROJECT_COPYRIGHT",
    "PROJECT_KEYWORDS",
    "PROJECT_CLASSIFIERS",
    "PROJECT_PLATFORMS",
    # Build
    "BUILD_DATE",
    "BUILD_COMMIT",
    "BUILD_BRANCH",
    "BUILD_TAG",
    "BUILD_NUMBER",
    "BUILD_ENVIRONMENT",
    "BUILD_PYTHON_VERSION",
    # Compatibilité
    "PYTHON_MIN_VERSION",
    "PYTHON_RECOMMENDED_VERSION",
    "PYTHON_MAX_VERSION",
    "PYTHON_VERSION_STRING",
    "DEPENDENCY_VERSIONS",
    "OPTIONAL_DEPENDENCIES",
    # Classes
    "VersionInfo",
    "ProjectInfo",
    "PROJECT_INFO",
    # Fonctions
    "parse_version",
    "compare_versions",
    "is_version_compatible",
    "get_version_info",
    "get_user_agent",
    "format_version_long",
    "format_version_short",
    "check_python_version",
    "get_python_version_warning",
]
