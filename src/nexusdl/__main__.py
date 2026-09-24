"""Point d'entrée principal de l'application NexusDL.

Ce module constitue le point d'entrée de l'application NexusDL. Il permet
de lancer l'application via :
    - `python -m nexusdl`
    - `nexusdl` (script d'entrée)
    - `nexusdl --gui` (interface graphique)
    - `nexusdl --web` (interface web)
    - `nexusdl --cli` (interface CLI, par défaut)

**Responsabilités** :
    - Parsing des arguments de ligne de commande
    - Détection automatique de l'interface appropriée
    - Initialisation des composants core (config, logger, i18n, events, registry)
    - Lancement de l'interface choisie (CLI, GUI, ou Web)
    - Gestion des signaux (SIGINT, SIGTERM) pour un arrêt propre
    - Gestion des erreurs globales et logging
    - Code de retour approprié

**Interfaces supportées** :
    - CLI (Textual) : Interface en ligne de commande interactive
    - GUI (PyQt6)   : Interface graphique desktop
    - Web (FastAPI)  : API REST + interface web

**Exemples d'utilisation** :
    >>> # Lancer l'interface CLI (par défaut)
    >>> python -m nexusdl
    >>>
    >>> # Lancer l'interface GUI
    >>> python -m nexusdl --gui
    >>>
    >>> # Lancer l'interface Web
    >>> python -m nexusdl --web --host 0.0.0.0 --port 8000
    >>>
    >>> # Avec fichier de configuration personnalisé
    >>> python -m nexusdl --config /path/to/config.yaml
    >>>
    >>> # Mode debug
    >>> python -m nexusdl --debug
    >>>
    >>> # Afficher la version
    >>> python -m nexusdl --version

Intégration :
    - core/config.py        : Configuration globale
    - core/logger.py        : Système de logging
    - core/i18n.py          : Internationalisation
    - core/events.py        : EventBus
    - core/paths.py         : Chemins des fichiers
    - core/registry/        : Registre des sites
    - interfaces/cli/       : Interface CLI (Textual)
    - interfaces/gui/       : Interface GUI (PyQt6)
    - interfaces/web/       : Interface Web (FastAPI)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from loguru import logger

from nexusdl.core.constants import (
    APP_AUTHOR,
    APP_DESCRIPTION,
    APP_NAME,
    APP_URL,
    APP_VERSION,
    PYTHON_MIN_VERSION,
)
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.paths import get_paths, paths


# ============================================================================
# CONSTANTES
# ============================================================================


# Codes de retour
EXIT_SUCCESS: Final[int] = 0
EXIT_ERROR: Final[int] = 1
EXIT_CONFIG_ERROR: Final[int] = 2
EXIT_DEPENDENCY_ERROR: Final[int] = 3
EXIT_INTERRUPTED: Final[int] = 130

# Interfaces disponibles
INTERFACE_CLI: Final[str] = "cli"
INTERFACE_GUI: Final[str] = "gui"
INTERFACE_WEB: Final[str] = "web"

# Ports par défaut
DEFAULT_WEB_PORT: Final[int] = 8000
DEFAULT_WEB_HOST: Final[str] = "127.0.0.1"


# ============================================================================
# GESTION DES SIGNAUX
# ============================================================================


class SignalHandler:
    """Gestionnaire de signaux pour un arrêt propre de l'application.

    Intercepte SIGINT (Ctrl+C) et SIGTERM pour permettre un nettoyage
    approprié avant la fermeture de l'application.
    """

    def __init__(self) -> None:
        """Initialise le gestionnaire de signaux."""
        self._shutdown_requested = False
        self._shutdown_event = asyncio.Event()
        self._original_sigint = None
        self._original_sigterm = None

    def install(self) -> None:
        """Installe les gestionnaires de signaux."""
        if sys.platform != "win32":
            # Unix-like systems
            self._original_sigint = signal.getsignal(signal.SIGINT)
            self._original_sigterm = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGINT, self._handle_signal)
            signal.signal(signal.SIGTERM, self._handle_signal)
        else:
            # Windows
            signal.signal(signal.SIGINT, self._handle_signal)

    def uninstall(self) -> None:
        """Désinstalle les gestionnaires de signaux."""
        if sys.platform != "win32":
            if self._original_sigint:
                signal.signal(signal.SIGINT, self._original_sigint)
            if self._original_sigterm:
                signal.signal(signal.SIGTERM, self._original_sigterm)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        """Gère un signal reçu.

        Args:
            signum: Numéro du signal.
            frame: Frame actuelle (non utilisé).
        """
        if self._shutdown_requested:
            # Deuxième signal, forcer l'arrêt
            logger.warning("Forced shutdown requested")
            sys.exit(EXIT_INTERRUPTED)

        self._shutdown_requested = True
        signal_name = signal.Signals(signum).name
        logger.info("Signal {} received, initiating graceful shutdown...", signal_name)
        self._shutdown_event.set()

    @property
    def shutdown_requested(self) -> bool:
        """Indique si un arrêt a été demandé."""
        return self._shutdown_requested

    @property
    def shutdown_event(self) -> asyncio.Event:
        """Événement asyncio pour attendre l'arrêt."""
        return self._shutdown_event


# Instance globale du gestionnaire de signaux
_signal_handler = SignalHandler()


# ============================================================================
# PARSING DES ARGUMENTS
# ============================================================================


def create_argument_parser() -> argparse.ArgumentParser:
    """Crée le parser d'arguments de ligne de commande.

    Returns:
        Instance de ArgumentParser configurée.
    """
    parser = argparse.ArgumentParser(
        prog="nexusdl",
        description=f"{APP_NAME} - {APP_DESCRIPTION}",
        epilog=f"For more information, visit {APP_URL}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # =========================================================================
    # Arguments principaux
    # =========================================================================

    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"{APP_NAME} {APP_VERSION}",
        help="Show version information and exit",
    )

    # =========================================================================
    # Interface
    # =========================================================================

    interface_group = parser.add_mutually_exclusive_group()
    interface_group.add_argument(
        "--cli",
        action="store_const",
        const=INTERFACE_CLI,
        dest="interface",
        help="Launch CLI interface (Textual, default)",
    )
    interface_group.add_argument(
        "--gui",
        action="store_const",
        const=INTERFACE_GUI,
        dest="interface",
        help="Launch GUI interface (PyQt6)",
    )
    interface_group.add_argument(
        "--web",
        action="store_const",
        const=INTERFACE_WEB,
        dest="interface",
        help="Launch Web interface (FastAPI)",
    )

    parser.add_argument(
        "--interface",
        choices=[INTERFACE_CLI, INTERFACE_GUI, INTERFACE_WEB],
        default=None,
        help="Interface to launch (auto-detect if not specified)",
    )

    # =========================================================================
    # Configuration
    # =========================================================================

    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        metavar="PATH",
        help="Path to configuration file (YAML)",
    )

    parser.add_argument(
        "--data-dir",
        type=Path,
        metavar="PATH",
        help="Path to data directory",
    )

    parser.add_argument(
        "--cache-dir",
        type=Path,
        metavar="PATH",
        help="Path to cache directory",
    )

    # =========================================================================
    # Web interface options
    # =========================================================================

    web_group = parser.add_argument_group("Web interface options")
    web_group.add_argument(
        "--host",
        type=str,
        default=DEFAULT_WEB_HOST,
        help=f"Web server host (default: {DEFAULT_WEB_HOST})",
    )
    web_group.add_argument(
        "-p",
        "--port",
        type=int,
        default=DEFAULT_WEB_PORT,
        help=f"Web server port (default: {DEFAULT_WEB_PORT})",
    )
    web_group.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development (Web only)",
    )
    web_group.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of worker processes (Web only, default: 1)",
    )

    # =========================================================================
    # Logging & Debug
    # =========================================================================

    debug_group = parser.add_argument_group("Logging & Debug")
    debug_group.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode (verbose logging)",
    )
    debug_group.add_argument(
        "--log-level",
        choices=["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=None,
        help="Set log level (default: INFO, or DEBUG if --debug)",
    )
    debug_group.add_argument(
        "--log-file",
        type=Path,
        metavar="PATH",
        help="Path to log file (default: auto)",
    )
    debug_group.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )

    # =========================================================================
    # Internationalization
    # =========================================================================

    i18n_group = parser.add_argument_group("Internationalization")
    i18n_group.add_argument(
        "-l",
        "--lang",
        "--language",
        type=str,
        metavar="CODE",
        dest="language",
        help="Interface language (e.g., en, fr, es, ja)",
    )

    # =========================================================================
    # Actions rapides
    # =========================================================================

    action_group = parser.add_argument_group("Quick actions")
    action_group.add_argument(
        "--download",
        type=str,
        metavar="URL",
        help="Download manga from URL and exit",
    )
    action_group.add_argument(
        "--search",
        type=str,
        metavar="QUERY",
        help="Search for manga and exit",
    )
    action_group.add_argument(
        "--list-sites",
        action="store_true",
        help="List available sites and exit",
    )

    # =========================================================================
    # Avancé
    # =========================================================================

    advanced_group = parser.add_argument_group("Advanced")
    advanced_group.add_argument(
        "--no-update-check",
        action="store_true",
        help="Disable automatic update check",
    )
    advanced_group.add_argument(
        "--reset-config",
        action="store_true",
        help="Reset configuration to defaults and exit",
    )
    advanced_group.add_argument(
        "--show-paths",
        action="store_true",
        help="Show application paths and exit",
    )
    advanced_group.add_argument(
        "--diagnose",
        action="store_true",
        help="Run diagnostic checks and exit",
    )

    return parser


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    """Parse les arguments de ligne de commande.

    Args:
        args: Arguments à parser (défaut: sys.argv[1:]).

    Returns:
        Arguments parsés.
    """
    parser = create_argument_parser()
    return parser.parse_args(args)


# ============================================================================
# INITIALISATION DES COMPOSANTS CORE
# ============================================================================


def initialize_paths(args: argparse.Namespace) -> None:
    """Initialise les chemins de l'application.

    Args:
        args: Arguments parsés.
    """
    paths_instance = get_paths()

    # Override des chemins si spécifiés
    if args.data_dir:
        paths_instance.set_data_dir(args.data_dir)
    if args.cache_dir:
        paths_instance.set_cache_dir(args.cache_dir)

    paths_instance.initialize()
    logger.debug("Paths initialized: data={}, cache={}, config={}", 
                 paths_instance.data_dir, 
                 paths_instance.cache_dir,
                 paths_instance.config_dir)


def initialize_logging(args: argparse.Namespace) -> None:
    """Initialise le système de logging.

    Args:
        args: Arguments parsés.
    """
    from nexusdl.core.logger import setup_logging

    # Déterminer le niveau de log
    log_level = args.log_level
    if log_level is None:
        log_level = "DEBUG" if args.debug else "INFO"

    # Déterminer le fichier de log
    log_file = args.log_file
    if log_file is None:
        paths_instance = get_paths()
        log_file = paths_instance.logs_dir / "nexusdl.log"

    # Configurer le logging
    setup_logging(
        level=log_level,
        format="text" if not args.no_color else "simple",
        log_file=str(log_file),
        colorize=not args.no_color,
        debug=args.debug,
    )

    logger.debug("Logging initialized: level={}, file={}", log_level, log_file)


def initialize_config(args: argparse.Namespace) -> None:
    """Initialise la configuration.

    Args:
        args: Arguments parsés.

    Raises:
        SystemExit: Si la configuration est invalide.
    """
    from nexusdl.core.config import ConfigManager, set_config_manager

    try:
        config_path = args.config
        manager = ConfigManager(config_path=config_path)
        set_config_manager(manager)

        config = manager.config
        logger.debug("Configuration loaded from {}", config_path or "default location")

        # Override avec les arguments CLI
        if args.language:
            config.i18n.language = args.language

    except Exception as e:
        logger.error("Failed to load configuration: {}", e)
        print(f"Error: Failed to load configuration: {e}", file=sys.stderr)
        sys.exit(EXIT_CONFIG_ERROR)


def initialize_i18n(args: argparse.Namespace) -> None:
    """Initialise l'internationalisation.

    Args:
        args: Arguments parsés.
    """
    from nexusdl.core.i18n import setup_i18n

    # Déterminer la langue
    language = args.language
    if language is None:
        try:
            from nexusdl.core.config import get_config
            language = get_config().i18n.language
        except Exception:
            language = "en"

    # Trouver le répertoire de traductions
    translations_dir = None
    try:
        import nexusdl
        package_dir = Path(nexusdl.__file__).parent
        default_dir = package_dir / "data" / "translations"
        if default_dir.exists():
            translations_dir = default_dir
    except Exception:
        pass

    setup_i18n(language=language, translations_dir=translations_dir)
    logger.debug("I18n initialized: language={}", language)


def initialize_event_bus() -> None:
    """Initialise l'EventBus."""
    from nexusdl.core.events import EventBus, EventBusConfig, set_event_bus

    try:
        from nexusdl.core.config import get_config
        config = get_config()
        bus_config = EventBusConfig(
            queue_size=config.events.queue_size,
            worker_count=config.events.worker_count,
            handler_timeout=config.events.handler_timeout,
            dead_letter_enabled=config.events.dead_letter_enabled,
        )
    except Exception:
        bus_config = EventBusConfig()

    event_bus = EventBus(config=bus_config)
    set_event_bus(event_bus)
    logger.debug("EventBus initialized")


def initialize_registry() -> None:
    """Initialise le registre des sites."""
    from nexusdl.core.registry import (
        ConfigLoader,
        SchemaValidator,
        SiteRegistry,
        set_site_registry,
    )

    try:
        loader = ConfigLoader()
        validator = SchemaValidator()
        registry = SiteRegistry(loader=loader, validator=validator)

        # Initialisation synchrone minimale
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(loader.start())
            loop.run_until_complete(validator.start())
            loop.run_until_complete(registry.start())
        finally:
            loop.close()

        set_site_registry(registry)
        logger.debug("Site registry initialized: {} sites", registry.sites_count)

    except Exception as e:
        logger.warning("Failed to initialize site registry: {}", e)


# ============================================================================
# ACTIONS RAPIDES
# ============================================================================


def handle_show_paths() -> int:
    """Affiche les chemins de l'application.

    Returns:
        Code de retour.
    """
    paths_instance = get_paths()

    print(f"\n{APP_NAME} v{APP_VERSION} - Application Paths\n")
    print(f"  Config directory:  {paths_instance.config_dir}")
    print(f"  Data directory:    {paths_instance.data_dir}")
    print(f"  Cache directory:   {paths_instance.cache_dir}")
    print(f"  Logs directory:    {paths_instance.logs_dir}")
    print(f"  Downloads directory: {paths_instance.downloads_dir}")
    print(f"  Library directory: {paths_instance.library_dir}")
    print()

    return EXIT_SUCCESS


def handle_list_sites() -> int:
    """Liste les sites disponibles.

    Returns:
        Code de retour.
    """
    try:
        from nexusdl.core.registry import get_site_registry

        registry = get_site_registry()
        sites = registry.list_sites()

        print(f"\n{APP_NAME} v{APP_VERSION} - Available Sites ({len(sites)})\n")

        for site in sorted(sites, key=lambda s: s.name):
            status = "✓" if site.enabled else "✗"
            adult = " [18+]" if site.adult else ""
            print(f"  {status} {site.name:30} ({site.language}){adult}")
            print(f"    {site.domains[0] if site.domains else 'N/A'}")
            print()

        return EXIT_SUCCESS

    except Exception as e:
        logger.error("Failed to list sites: {}", e)
        print(f"Error: Failed to list sites: {e}", file=sys.stderr)
        return EXIT_ERROR


def handle_diagnose() -> int:
    """Exécute des diagnostics.

    Returns:
        Code de retour.
    """
    print(f"\n{APP_NAME} v{APP_VERSION} - Diagnostics\n")

    # Python version
    print(f"  Python version: {sys.version}")
    print(f"  Python executable: {sys.executable}")
    print(f"  Platform: {sys.platform}")
    print()

    # Dependencies
    print("  Dependencies:")
    dependencies = [
        ("textual", "CLI interface"),
        ("PyQt6", "GUI interface"),
        ("fastapi", "Web interface"),
        ("uvicorn", "Web server"),
        ("httpx", "HTTP client"),
        ("aiohttp", "HTTP client"),
        ("Pillow", "Image processing"),
        ("pydantic", "Validation"),
        ("loguru", "Logging"),
        ("aiosqlite", "Database"),
        ("pyyaml", "Configuration"),
    ]

    for package, description in dependencies:
        try:
            __import__(package)
            print(f"    ✓ {package:20} ({description})")
        except ImportError:
            print(f"    ✗ {package:20} ({description}) - NOT INSTALLED")
    print()

    # Paths
    paths_instance = get_paths()
    print("  Paths:")
    print(f"    Config:  {paths_instance.config_dir} ({'✓' if paths_instance.config_dir.exists() else '✗'})")
    print(f"    Data:    {paths_instance.data_dir} ({'✓' if paths_instance.data_dir.exists() else '✗'})")
    print(f"    Cache:   {paths_instance.cache_dir} ({'✓' if paths_instance.cache_dir.exists() else '✗'})")
    print(f"    Logs:    {paths_instance.logs_dir} ({'✓' if paths_instance.logs_dir.exists() else '✗'})")
    print()

    # Configuration
    try:
        from nexusdl.core.config import get_config
        config = get_config()
        print("  Configuration:")
        print(f"    Language: {config.i18n.language}")
        print(f"    Log level: {config.logging.level}")
        print(f"    Max concurrent downloads: {config.download.max_concurrent_tasks}")
        print()
    except Exception as e:
        print(f"  Configuration: ✗ Failed to load ({e})")
        print()

    return EXIT_SUCCESS


def handle_reset_config() -> int:
    """Réinitialise la configuration.

    Returns:
        Code de retour.
    """
    try:
        paths_instance = get_paths()
        config_file = paths_instance.config_dir / "config.yaml"

        if config_file.exists():
            backup = config_file.with_suffix(".yaml.backup")
            config_file.rename(backup)
            print(f"Configuration reset to defaults")
            print(f"Backup saved to: {backup}")
        else:
            print("No configuration file found")

        return EXIT_SUCCESS

    except Exception as e:
        logger.error("Failed to reset configuration: {}", e)
        print(f"Error: Failed to reset configuration: {e}", file=sys.stderr)
        return EXIT_ERROR


async def handle_download(url: str) -> int:
    """Télécharge un manga depuis une URL.

    Args:
        url: URL du manga.

    Returns:
        Code de retour.
    """
    try:
        from nexusdl.core.registry import get_site_registry
        from nexusdl.core.downloader import get_download_manager

        registry = get_site_registry()
        manager = get_download_manager()

        # Trouver le site approprié
        parser = await registry.get_parser_for_url(url)
        if parser is None:
            print(f"Error: No parser found for URL: {url}", file=sys.stderr)
            return EXIT_ERROR

        # Récupérer les informations du manga
        manga = await parser.get_manga(url)
        print(f"Downloading: {manga.title}")
        print(f"Chapters: {len(manga.chapters)}")

        # Créer une tâche de téléchargement
        task = await manager.create_task(
            site_id=parser.site_id,
            manga_id=manga.id,
            chapter_ids=None,  # Tous les chapitres
        )

        print(f"Task created: {task.id}")
        print("Download started...")

        # Attendre la fin du téléchargement
        await task.wait_for_completion()

        if task.status.value == "completed":
            print(f"Download completed: {task.output_path}")
            return EXIT_SUCCESS
        else:
            print(f"Download failed: {task.error_message}", file=sys.stderr)
            return EXIT_ERROR

    except Exception as e:
        logger.error("Download failed: {}", e)
        print(f"Error: Download failed: {e}", file=sys.stderr)
        return EXIT_ERROR


async def handle_search(query: str) -> int:
    """Recherche des mangas.

    Args:
        query: Requête de recherche.

    Returns:
        Code de retour.
    """
    try:
        from nexusdl.core.registry import get_site_registry

        registry = get_site_registry()

        print(f"\nSearching for: {query}\n")

        # Rechercher sur tous les sites
        results = await registry.search_all(query)

        if not results:
            print("No results found")
            return EXIT_SUCCESS

        print(f"Found {len(results)} results:\n")

        for i, result in enumerate(results[:20], 1):
            print(f"{i:3}. {result.title}")
            print(f"     Author: {result.author or 'Unknown'}")
            print(f"     Site: {result.site_id} | Language: {result.language}")
            print(f"     Status: {result.status.value} | Chapters: {result.chapters_count}")
            print()

        if len(results) > 20:
            print(f"... and {len(results) - 20} more results")

        return EXIT_SUCCESS

    except Exception as e:
        logger.error("Search failed: {}", e)
        print(f"Error: Search failed: {e}", file=sys.stderr)
        return EXIT_ERROR


# ============================================================================
# DÉTECTION DE L'INTERFACE
# ============================================================================


def detect_interface() -> str:
    """Détecte automatiquement l'interface appropriée.

    Returns:
        Nom de l'interface à utiliser.
    """
    # Vérifier si on a un display (pour GUI)
    has_display = False
    if sys.platform != "win32":
        has_display = "DISPLAY" in os.environ or "WAYLAND_DISPLAY" in os.environ
    else:
        has_display = True  # Windows a toujours un display

    # Vérifier si PyQt6 est disponible
    has_pyqt6 = False
    try:
        import PyQt6
        has_pyqt6 = True
    except ImportError:
        pass

    # Vérifier si Textual est disponible
    has_textual = False
    try:
        import textual
        has_textual = True
    except ImportError:
        pass

    # Vérifier si FastAPI est disponible
    has_fastapi = False
    try:
        import fastapi
        has_fastapi = True
    except ImportError:
        pass

    # Priorité : GUI si display + PyQt6, sinon CLI si Textual, sinon Web
    if has_display and has_pyqt6:
        logger.debug("Auto-detected GUI interface")
        return INTERFACE_GUI
    elif has_textual:
        logger.debug("Auto-detected CLI interface")
        return INTERFACE_CLI
    elif has_fastapi:
        logger.debug("Auto-detected Web interface")
        return INTERFACE_WEB
    else:
        logger.error("No interface available! Install textual, PyQt6, or fastapi")
        print("Error: No interface available!", file=sys.stderr)
        print("Install one of: textual (CLI), PyQt6 (GUI), or fastapi (Web)", file=sys.stderr)
        sys.exit(EXIT_DEPENDENCY_ERROR)


# ============================================================================
# LANCEMENT DES INTERFACES
# ============================================================================


async def launch_cli(args: argparse.Namespace) -> int:
    """Lance l'interface CLI (Textual).

    Args:
        args: Arguments parsés.

    Returns:
        Code de retour.
    """
    try:
        from nexusdl.interfaces.cli import run_cli

        logger.info("Launching CLI interface")
        return await run_cli()

    except ImportError as e:
        logger.error("CLI interface not available: {}", e)
        print("Error: CLI interface not available. Install textual:", file=sys.stderr)
        print("  pip install textual", file=sys.stderr)
        return EXIT_DEPENDENCY_ERROR

    except Exception as e:
        logger.exception("CLI interface failed: {}", e)
        print(f"Error: CLI interface failed: {e}", file=sys.stderr)
        return EXIT_ERROR


async def launch_gui(args: argparse.Namespace) -> int:
    """Lance l'interface GUI (PyQt6).

    Args:
        args: Arguments parsés.

    Returns:
        Code de retour.
    """
    try:
        from nexusdl.interfaces.gui import run_gui

        logger.info("Launching GUI interface")
        return await run_gui()

    except ImportError as e:
        logger.error("GUI interface not available: {}", e)
        print("Error: GUI interface not available. Install PyQt6:", file=sys.stderr)
        print("  pip install PyQt6", file=sys.stderr)
        return EXIT_DEPENDENCY_ERROR

    except Exception as e:
        logger.exception("GUI interface failed: {}", e)
        print(f"Error: GUI interface failed: {e}", file=sys.stderr)
        return EXIT_ERROR


async def launch_web(args: argparse.Namespace) -> int:
    """Lance l'interface Web (FastAPI).

    Args:
        args: Arguments parsés.

    Returns:
        Code de retour.
    """
    try:
        from nexusdl.interfaces.web.backend.main import create_app

        logger.info("Launching Web interface on {}:{}", args.host, args.port)

        # Créer l'application FastAPI
        app = create_app(
            log_level="DEBUG" if args.debug else "INFO",
            debug=args.debug,
        )

        # Lancer avec uvicorn
        import uvicorn

        config = uvicorn.Config(
            app,
            host=args.host,
            port=args.port,
            reload=args.reload,
            workers=args.workers if not args.reload else 1,
            log_level="debug" if args.debug else "info",
        )

        server = uvicorn.Server(config)

        # Attendre le signal d'arrêt
        shutdown_task = asyncio.create_task(_signal_handler.shutdown_event.wait())
        server_task = asyncio.create_task(server.serve())

        done, pending = await asyncio.wait(
            [shutdown_task, server_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Annuler les tâches en attente
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Arrêter le serveur proprement
        server.should_exit = True

        logger.info("Web interface stopped")
        return EXIT_SUCCESS

    except ImportError as e:
        logger.error("Web interface not available: {}", e)
        print("Error: Web interface not available. Install fastapi and uvicorn:", file=sys.stderr)
        print("  pip install fastapi uvicorn", file=sys.stderr)
        return EXIT_DEPENDENCY_ERROR

    except Exception as e:
        logger.exception("Web interface failed: {}", e)
        print(f"Error: Web interface failed: {e}", file=sys.stderr)
        return EXIT_ERROR


# ============================================================================
# FONCTION PRINCIPALE
# ============================================================================


async def async_main(args: argparse.Namespace) -> int:
    """Fonction principale asynchrone.

    Args:
        args: Arguments parsés.

    Returns:
        Code de retour.
    """
    # Initialiser les composants core
    initialize_paths(args)
    initialize_logging(args)
    initialize_config(args)
    initialize_i18n(args)
    initialize_event_bus()
    initialize_registry()

    # Installer le gestionnaire de signaux
    _signal_handler.install()

    logger.info("Starting {} v{}", APP_NAME, APP_VERSION)

    try:
        # Démarrer l'EventBus
        from nexusdl.core.events import get_event_bus
        event_bus = get_event_bus()
        await event_bus.start()

        # =========================================================================
        # Actions rapides
        # =========================================================================

        if args.show_paths:
            return handle_show_paths()

        if args.list_sites:
            return handle_list_sites()

        if args.diagnose:
            return handle_diagnose()

        if args.reset_config:
            return handle_reset_config()

        if args.download:
            return await handle_download(args.download)

        if args.search:
            return await handle_search(args.search)

        # =========================================================================
        # Lancement de l'interface
        # =========================================================================

        # Déterminer l'interface
        interface = args.interface or detect_interface()

        logger.info("Launching {} interface", interface)

        if interface == INTERFACE_CLI:
            return await launch_cli(args)
        elif interface == INTERFACE_GUI:
            return await launch_gui(args)
        elif interface == INTERFACE_WEB:
            return await launch_web(args)
        else:
            logger.error("Unknown interface: {}", interface)
            return EXIT_ERROR

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return EXIT_INTERRUPTED

    except NexusDLError as e:
        logger.error("Application error: {}", e)
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_ERROR

    except Exception as e:
        logger.exception("Unexpected error: {}", e)
        print(f"Unexpected error: {e}", file=sys.stderr)
        traceback.print_exc()
        return EXIT_ERROR

    finally:
        # Arrêter l'EventBus
        try:
            from nexusdl.core.events import get_event_bus
            event_bus = get_event_bus()
            await event_bus.stop()
        except Exception as e:
            logger.warning("Error stopping EventBus: {}", e)

        # Désinstaller le gestionnaire de signaux
        _signal_handler.uninstall()

        logger.info("Application stopped")


def main(args: list[str] | None = None) -> int:
    """Point d'entrée principal synchrone.

    Args:
        args: Arguments de ligne de commande (défaut: sys.argv[1:]).

    Returns:
        Code de retour.
    """
    # Vérifier la version de Python
    if sys.version_info < PYTHON_MIN_VERSION:
        print(
            f"Error: Python {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}+ is required.",
            file=sys.stderr,
        )
        print(
            f"You are using Python {sys.version_info.major}.{sys.version_info.minor}.",
            file=sys.stderr,
        )
        return EXIT_DEPENDENCY_ERROR

    # Parser les arguments
    parsed_args = parse_arguments(args)

    # Exécuter la fonction principale asynchrone
    try:
        return asyncio.run(async_main(parsed_args))
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        traceback.print_exc()
        return EXIT_ERROR


# ============================================================================
# POINT D'ENTRÉE
# ============================================================================


if __name__ == "__main__":
    sys.exit(main())
