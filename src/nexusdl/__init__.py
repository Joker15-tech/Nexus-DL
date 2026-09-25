"""NexusDL - Advanced Manga Downloader and Library Manager.

NexusDL est un gestionnaire de mangas avancé avec trois interfaces :
    - CLI (Textual)  : Interface en ligne de commande interactive
    - GUI (PyQt6)    : Interface graphique desktop
    - Web (FastAPI)  : API REST + interface web Next.js

**Caractéristiques principales** :
    - Téléchargement depuis 20+ sources de mangas
    - Bibliothèque locale avec métadonnées riches
    - Multi-format : CBZ, CBR, PDF, ZIP, dossiers
    - Multi-langue : EN, FR, ES, DE, JA, KO, ZH, etc.
    - Thème cyberpunk néon avec 4 variantes
    - Communications temps réel via WebSocket
    - Système de plugins extensible
    - Configuration YAML flexible

**Exemple d'utilisation — Lancement automatique** :
    >>> import nexusdl
    >>> nexusdl.run()  # Détecte et lance la meilleure interface

**Exemple d'utilisation — Lancement spécifique** :
    >>> import nexusdl
    >>>
    >>> # Interface CLI
    >>> nexusdl.run_cli()
    >>>
    >>> # Interface GUI
    >>> nexusdl.run_gui()
    >>>
    >>> # Interface Web
    >>> nexusdl.run_web(host="0.0.0.0", port=8000)

**Exemple d'utilisation — Informations** :
    >>> import nexusdl
    >>> print(nexusdl.__version__)
    '0.1.0'
    >>> print(nexusdl.get_info())
    {'name': 'NexusDL', 'version': '0.1.0', ...}

**Exemple d'utilisation — Depuis la ligne de commande** :
    $ python -m nexusdl
    $ nexusdl --cli
    $ nexusdl --gui
    $ nexusdl --web --host 0.0.0.0 --port 8000

Architecture :
    nexusdl/
        ├── __init__.py      : Ce fichier (API publique)
        ├── __main__.py      : Point d'entrée CLI
        ├── version.py       : Version du package
        │
        ├── core/            : Logique métier
        │   ├── config.py    : Configuration
        │   ├── logger.py    : Logging
        │   ├── i18n.py      : Internationalisation
        │   ├── events.py    : EventBus
        │   ├── paths.py     : Chemins
        │   ├── models/      : Modèles de données
        │   ├── registry/    : Registre des sites
        │   ├── downloader/  : Gestionnaire de téléchargements
        │   ├── library/     : Gestionnaire de bibliothèque
        │   ├── image/       : Traitement d'images
        │   ├── packaging/   : Empaquetage (CBZ, PDF, etc.)
        │   ├── session/     : Sessions HTTP
        │   └── utils/       : Utilitaires
        │
        ├── interfaces/      : Interfaces utilisateur
        │   ├── cli/         : Interface CLI (Textual)
        │   ├── gui/         : Interface GUI (PyQt6)
        │   └── web/         : Interface Web (FastAPI + Next.js)
        │
        ├── parsers/         : Parseurs de sites
        │   ├── base.py      : Classe de base
        │   ├── en/          : Sites anglais
        │   ├── fr/          : Sites français
        │   ├── kr/          : Sites coréens
        │   ├── adult/       : Sites adultes
        │   └── mixins/      : Mixins réutilisables
        │
        ├── plugins/         : Système de plugins
        │   ├── api.py       : API de plugins
        │   ├── hooks.py     : Hooks
        │   └── loader.py    : Chargeur de plugins
        │
        └── data/            : Données intégrées
            ├── translations/: Traductions (i18n)
            ├── default_config.yaml : Configuration par défaut
            └── user_agents.txt     : User-agents

Intégration :
    - core/*                   : Composants du core
    - interfaces/*             : Interfaces utilisateur
    - parsers/*                : Parseurs de sites
    - plugins/*                : Système de plugins
    - data/*                   : Données intégrées
"""

from __future__ import annotations

import sys
from typing import Any, Final

# ============================================================================
# MÉTADONNÉES DU PACKAGE
# ============================================================================

__version__: Final[str] = "0.1.0"
__author__: Final[str] = "NexusDL Team"
__email__: Final[str] = "contact@nexusdl.dev"
__license__: Final[str] = "GPL-3.0-only"
__copyright__: Final[str] = "Copyright (C) 2024-2026 NexusDL Team"
__url__: Final[str] = "https://github.com/nexusdl/nexusdl"
__description__: Final[str] = "Advanced Manga Downloader and Library Manager"

# ============================================================================
# CONSTANTES
# ============================================================================

APP_NAME: Final[str] = "NexusDL"
APP_VERSION: Final[str] = __version__
APP_DESCRIPTION: Final[str] = __description__
APP_AUTHOR: Final[str] = __author__
APP_URL: Final[str] = __url__

# Interfaces disponibles
INTERFACE_CLI: Final[str] = "cli"
INTERFACE_GUI: Final[str] = "gui"
INTERFACE_WEB: Final[str] = "web"

# ============================================================================
# IMPORTS CONDITIONNELS
# ============================================================================

# Vérifier la version de Python
PYTHON_MIN_VERSION = (3, 11)
if sys.version_info < PYTHON_MIN_VERSION:
    raise RuntimeError(
        f"{APP_NAME} requires Python {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}+ "
        f"You are using Python {sys.version_info.major}.{sys.version_info.minor}."
    )

# ============================================================================
# IMPORTS LAZY
# ============================================================================


def __getattr__(name: str) -> Any:
    """Import lazy pour éviter de charger tous les modules au démarrage.

    Args:
        name: Nom de l'attribut à importer.

    Returns:
        L'objet importé.

    Raises:
        AttributeError: Si l'attribut n'existe pas.
    """
    # Fonctions de lancement
    if name in ("run", "run_cli", "run_gui", "run_web"):
        from nexusdl.interfaces import run, run_cli, run_gui, run_web

        return {"run": run, "run_cli": run_cli, "run_gui": run_gui, "run_web": run_web}[name]

    # Fonctions d'information
    if name in ("get_version", "get_info", "get_paths"):
        from nexusdl.core.constants import APP_NAME as _APP_NAME
        from nexusdl.core.paths import get_paths as _get_paths

        def get_version() -> str:
            """Retourne la version de NexusDL."""
            return __version__

        def get_info() -> dict[str, Any]:
            """Retourne les informations complètes sur NexusDL."""
            return {
                "name": _APP_NAME,
                "version": __version__,
                "author": __author__,
                "license": __license__,
                "url": __url__,
                "description": __description__,
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "platform": sys.platform,
            }

        return {"get_version": get_version, "get_info": get_info, "get_paths": _get_paths}[name]

    # Modules core
    if name == "config":
        from nexusdl.core import config

        return config

    if name == "logger":
        from nexusdl.core import logger

        return logger

    if name == "i18n":
        from nexusdl.core import i18n

        return i18n

    if name == "events":
        from nexusdl.core import events

        return events

    if name == "paths":
        from nexusdl.core import paths

        return paths

    # Modules interfaces
    if name == "interfaces":
        from nexusdl import interfaces

        return interfaces

    if name == "cli":
        from nexusdl.interfaces import cli

        return cli

    if name == "gui":
        from nexusdl.interfaces import gui

        return gui

    if name == "web":
        from nexusdl.interfaces import web

        return web

    # Modules parsers
    if name == "parsers":
        from nexusdl import parsers

        return parsers

    if name == "registry":
        from nexusdl.core import registry

        return registry

    # Modules plugins
    if name == "plugins":
        from nexusdl import plugins

        return plugins

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ============================================================================
# FONCTIONS PUBLIQUES
# ============================================================================


def get_version() -> str:
    """Retourne la version de NexusDL.

    Returns:
        Version sous forme de string.

    Example:
        >>> import nexusdl
        >>> nexusdl.get_version()
        '0.1.0'
    """
    return __version__


def get_info() -> dict[str, Any]:
    """Retourne les informations complètes sur NexusDL.

    Returns:
        Dictionnaire d'informations.

    Example:
        >>> import nexusdl
        >>> info = nexusdl.get_info()
        >>> print(info['name'])
        'NexusDL'
    """
    from nexusdl.core.constants import APP_NAME as _APP_NAME

    return {
        "name": _APP_NAME,
        "version": __version__,
        "author": __author__,
        "email": __email__,
        "license": __license__,
        "url": __url__,
        "description": __description__,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": sys.platform,
    }


def get_paths() -> Any:
    """Retourne l'instance des chemins de l'application.

    Returns:
        Instance de Paths.

    Example:
        >>> import nexusdl
        >>> paths = nexusdl.get_paths()
        >>> print(paths.config_dir)
    """
    from nexusdl.core.paths import get_paths as _get_paths

    return _get_paths()


# ============================================================================
# FONCTIONS DE LANCEMENT
# ============================================================================


def run(interface: str | None = None, **kwargs: Any) -> int:
    """Lance NexusDL avec l'interface spécifiée ou la meilleure disponible.

    Args:
        interface: Nom de l'interface (cli, gui, web). Auto-détecté si None.
        **kwargs: Arguments passés à l'interface.

    Returns:
        Code de retour.

    Example:
        >>> import nexusdl
        >>> nexusdl.run()  # Auto-détection
        >>> nexusdl.run("web", host="0.0.0.0", port=8000)
    """
    from nexusdl.interfaces import run as _run

    return _run(interface=interface, **kwargs)


def run_cli(**kwargs: Any) -> int:
    """Lance l'interface CLI (Textual).

    Args:
        **kwargs: Arguments passés à l'interface CLI.

    Returns:
        Code de retour.

    Example:
        >>> import nexusdl
        >>> nexusdl.run_cli()
    """
    from nexusdl.interfaces import run_cli as _run_cli

    return _run_cli(**kwargs)


def run_gui(**kwargs: Any) -> int:
    """Lance l'interface GUI (PyQt6).

    Args:
        **kwargs: Arguments passés à l'interface GUI.

    Returns:
        Code de retour.

    Example:
        >>> import nexusdl
        >>> nexusdl.run_gui()
    """
    from nexusdl.interfaces import run_gui as _run_gui

    return _run_gui(**kwargs)


def run_web(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
    workers: int = 1,
    **kwargs: Any,
) -> int:
    """Lance l'interface Web (FastAPI).

    Args:
        host: Hôte d'écoute.
        port: Port d'écoute.
        reload: Activer le rechargement automatique.
        workers: Nombre de workers.
        **kwargs: Arguments additionnels.

    Returns:
        Code de retour.

    Example:
        >>> import nexusdl
        >>> nexusdl.run_web(host="0.0.0.0", port=8000)
    """
    from nexusdl.interfaces import run_web as _run_web

    return _run_web(host=host, port=port, reload=reload, workers=workers, **kwargs)


# ============================================================================
# FONCTIONS D'INFORMATION
# ============================================================================


def print_version() -> None:
    """Affiche la version de NexusDL."""
    print(f"{APP_NAME} {__version__}")


def print_info() -> None:
    """Affiche les informations complètes sur NexusDL."""
    info = get_info()

    print(f"\n{info['name']} v{info['version']}")
    print(f"{'=' * 50}")
    print(f"Description: {info['description']}")
    print(f"Author:      {info['author']} <{info['email']}>")
    print(f"License:     {info['license']}")
    print(f"URL:         {info['url']}")
    print()
    print(f"Python:      {info['python_version']}")
    print(f"Platform:    {info['platform']}")
    print()


def check_dependencies() -> dict[str, bool]:
    """Vérifie la disponibilité des dépendances optionnelles.

    Returns:
        Dictionnaire {package_name: is_available}.

    Example:
        >>> import nexusdl
        >>> deps = nexusdl.check_dependencies()
        >>> print(deps)
        {'textual': True, 'PyQt6': False, 'fastapi': True, ...}
    """
    dependencies = {
        "textual": False,
        "PyQt6": False,
        "fastapi": False,
        "uvicorn": False,
        "httpx": False,
        "aiohttp": False,
        "Pillow": False,
        "pydantic": False,
        "loguru": False,
        "aiosqlite": False,
        "pyyaml": False,
    }

    for package in dependencies:
        try:
            __import__(package.lower() if package != "PyQt6" else "PyQt6")
            dependencies[package] = True
        except ImportError:
            pass

    return dependencies


def print_dependencies() -> None:
    """Affiche le statut des dépendances optionnelles."""
    deps = check_dependencies()

    print(f"\n{APP_NAME} - Dependencies Status")
    print(f"{'=' * 50}")

    for package, available in sorted(deps.items()):
        status = "✓" if available else "✗"
        print(f"  {status} {package:20}")

    print()


# ============================================================================
# FONCTIONS D'INITIALISATION
# ============================================================================


def initialize(
    config_path: str | None = None,
    log_level: str = "INFO",
    language: str | None = None,
) -> None:
    """Initialise les composants core de NexusDL.

    Cette fonction est appelée automatiquement lors du lancement d'une
    interface, mais peut être appelée manuellement pour une utilisation
    en bibliothèque.

    Args:
        config_path: Chemin vers le fichier de configuration.
        log_level: Niveau de log (TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL).
        language: Langue de l'interface.

    Example:
        >>> import nexusdl
        >>> nexusdl.initialize(log_level="DEBUG", language="fr")
    """
    from nexusdl.core.config import ConfigManager, set_config_manager
    from nexusdl.core.events import EventBus, EventBusConfig, set_event_bus
    from nexusdl.core.i18n import setup_i18n
    from nexusdl.core.logger import setup_logging
    from nexusdl.core.paths import get_paths
    from nexusdl.core.registry import (
        ConfigLoader,
        SchemaValidator,
        SiteRegistry,
        set_site_registry,
    )

    # 1. Initialiser les chemins
    paths = get_paths()
    if not paths.is_initialized:
        paths.initialize()

    # 2. Charger la configuration
    try:
        manager = ConfigManager(config_path=config_path)
        set_config_manager(manager)
    except Exception as e:
        print(f"Warning: Failed to load configuration: {e}", file=sys.stderr)

    # 3. Configurer le logging
    try:
        setup_logging(
            level=log_level,
            format="text",
            log_file=str(paths.logs_dir / "nexusdl.log"),
            colorize=True,
        )
    except Exception as e:
        print(f"Warning: Failed to setup logging: {e}", file=sys.stderr)

    # 4. Configurer l'i18n
    try:
        effective_language = language or "en"
        try:
            from nexusdl.core.config import get_config

            effective_language = get_config().i18n.language
        except Exception:
            pass

        setup_i18n(language=effective_language)
    except Exception as e:
        print(f"Warning: Failed to setup i18n: {e}", file=sys.stderr)

    # 5. Initialiser l'EventBus
    try:
        bus_config = EventBusConfig()
        event_bus = EventBus(config=bus_config)
        set_event_bus(event_bus)
    except Exception as e:
        print(f"Warning: Failed to initialize EventBus: {e}", file=sys.stderr)

    # 6. Initialiser le registre des sites
    try:
        import asyncio

        loader = ConfigLoader()
        validator = SchemaValidator()
        registry = SiteRegistry(loader=loader, validator=validator)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(loader.start())
            loop.run_until_complete(validator.start())
            loop.run_until_complete(registry.start())
        finally:
            loop.close()

        set_site_registry(registry)
    except Exception as e:
        print(f"Warning: Failed to initialize site registry: {e}", file=sys.stderr)


def shutdown() -> None:
    """Arrête proprement les composants core de NexusDL.

    Example:
        >>> import nexusdl
        >>> nexusdl.initialize()
        >>> # ... utiliser NexusDL ...
        >>> nexusdl.shutdown()
    """
    try:
        from nexusdl.core.events import get_event_bus

        event_bus = get_event_bus()
        if event_bus and event_bus.is_started:
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(event_bus.stop())
            finally:
                loop.close()
    except Exception as e:
        print(f"Warning: Failed to shutdown EventBus: {e}", file=sys.stderr)


# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    # Métadonnées
    "__version__",
    "__author__",
    "__email__",
    "__license__",
    "__copyright__",
    "__url__",
    "__description__",
    # Constantes
    "APP_NAME",
    "APP_VERSION",
    "APP_DESCRIPTION",
    "APP_AUTHOR",
    "APP_URL",
    "INTERFACE_CLI",
    "INTERFACE_GUI",
    "INTERFACE_WEB",
    # Fonctions d'information
    "get_version",
    "get_info",
    "get_paths",
    "print_version",
    "print_info",
    "check_dependencies",
    "print_dependencies",
    # Fonctions de lancement
    "run",
    "run_cli",
    "run_gui",
    "run_web",
    # Fonctions d'initialisation
    "initialize",
    "shutdown",
]


# ============================================================================
# MESSAGE DE BIENVENUE
# ============================================================================


def _print_welcome() -> None:
    """Affiche un message de bienvenue si exécuté directement."""
    if __name__ == "__main__":
        print(f"\n{'=' * 60}")
        print(f"  {APP_NAME} v{__version__}")
        print(f"  {__description__}")
        print(f"{'=' * 60}")
        print()
        print("Usage:")
        print("  python -m nexusdl              # Launch default interface")
        print("  python -m nexusdl --cli        # Launch CLI interface")
        print("  python -m nexusdl --gui        # Launch GUI interface")
        print("  python -m nexusdl --web        # Launch Web interface")
        print()
        print("For more information:")
        print(f"  {__url__}")
        print()


# Afficher le message de bienvenue si exécuté directement
_print_welcome()
