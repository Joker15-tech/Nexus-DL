"""Point d'entrée de la ligne de commande NexusDL.

Ce module fournit le point d'entrée principal de l'application CLI NexusDL.
Il est utilisé comme cible pour le script console défini dans pyproject.toml :

    [project.scripts]
    nexusdl = "nexusdl.interfaces.cli.cli_entry:main"

**Commandes disponibles** :
    - `nexusdl` (défaut)        : Lancer l'interface TUI
    - `nexusdl search <query>`  : Recherche rapide sans TUI
    - `nexusdl download <url>`  : Téléchargement direct
    - `nexusdl list-sites`      : Lister les sites supportés
    - `nexusdl config`          : Gérer la configuration (show/edit/reset/path)
    - `nexusdl validate`        : Valider la configuration
    - `nexusdl version`         : Afficher la version
    - `nexusdl doctor`          : Diagnostic du système
    - `nexusdl cache`           : Gérer le cache (clear/stats)
    - `nexusdl completion`      : Générer les scripts de complétion shell

**Options globales** :
    --config PATH               : Chemin vers le fichier de configuration
    --language LANG             : Langue de l'interface
    --log-level LEVEL           : Niveau de log (TRACE, DEBUG, INFO, WARNING, ERROR)
    --no-color                  : Désactiver les couleurs
    --verbose, -v               : Mode verbeux (DEBUG)
    --quiet, -q                 : Mode silencieux (ERROR)
    --version, -V               : Afficher la version
    --help, -h                  : Afficher l'aide

**Exemples d'utilisation** :
    >>> # Lancer l'interface TUI
    >>> $ nexusdl
    >>>
    >>> # Recherche rapide
    >>> $ nexusdl search "one piece" --site mangadex --limit 10
    >>>
    >>> # Téléchargement direct
    >>> $ nexusdl download https://mangadex.org/title/12345
    >>>
    >>> # Lister les sites
    >>> $ nexusdl list-sites --language fr
    >>>
    >>> # Afficher la configuration
    >>> $ nexusdl config show
    >>>
    >>> # Diagnostic
    >>> $ nexusdl doctor
    >>>
    >>> # Générer la complétion bash
    >>> $ nexusdl completion bash > ~/.bash_completion.d/nexusdl

Intégration :
    - core/config.py       : Configuration globale
    - core/logger.py       : Système de logging
    - core/i18n.py         : Internationalisation
    - core/paths.py        : Gestion des chemins
    - core/registry/       : Registre des sites
    - interfaces/cli/app.py : Application TUI
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, NoReturn

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
from nexusdl.core.paths import Paths, get_paths


# ============================================================================
# CONSTANTES
# ============================================================================


# Nom de la commande
CLI_COMMAND_NAME: Final[str] = "nexusdl"

# Description de la commande
CLI_DESCRIPTION: Final[str] = (
    f"{APP_NAME} - {APP_DESCRIPTION}\n\n"
    f"Version: {APP_VERSION}\n"
    f"Author: {APP_AUTHOR}\n"
    f"Website: {APP_URL}"
)

# Message d'épilogue
CLI_EPILOG: Final[str] = (
    "Examples:\n"
    "  nexusdl                          Launch the TUI interface\n"
    "  nexusdl search 'one piece'       Search for manga\n"
    "  nexusdl download <url>           Download from URL\n"
    "  nexusdl list-sites               List supported sites\n"
    "  nexusdl config show              Show configuration\n"
    "  nexusdl doctor                   Run diagnostics\n\n"
    f"Report bugs to: {APP_URL}/issues\n"
    f"Documentation: {APP_URL}/docs"
)

# Codes de sortie
EXIT_SUCCESS: Final[int] = 0
EXIT_ERROR: Final[int] = 1
EXIT_USAGE_ERROR: Final[int] = 2
EXIT_CONFIG_ERROR: Final[int] = 3
EXIT_DEPENDENCY_ERROR: Final[int] = 4
EXIT_INTERRUPTED: Final[int] = 130


# ============================================================================
# EXCEPTIONS
# ============================================================================


class CLIError(NexusDLError):
    """Exception de base pour les erreurs de la CLI."""

    def __init__(self, message: str, exit_code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class CommandNotFoundError(CLIError):
    """Exception levée lorsqu'une commande est introuvable."""

    def __init__(self, command: str) -> None:
        super().__init__(
            f"Commande inconnue: {command}\n"
            f"Utilisez '{CLI_COMMAND_NAME} --help' pour voir les commandes disponibles.",
            exit_code=EXIT_USAGE_ERROR,
        )
        self.command = command


class DependencyNotInstalledError(CLIError):
    """Exception levée lorsqu'une dépendance optionnelle n'est pas installée."""

    def __init__(self, dependency: str, install_command: str) -> None:
        super().__init__(
            f"Dépendance manquante: {dependency}\n"
            f"Installez-la avec: {install_command}",
            exit_code=EXIT_DEPENDENCY_ERROR,
        )
        self.dependency = dependency
        self.install_command = install_command


# ============================================================================
# INITIALISATION MINIMALE
# ============================================================================


def _check_python_version() -> None:
    """Vérifie que la version de Python est compatible.

    Raises:
        SystemExit: Si la version est trop ancienne.
    """
    if sys.version_info < PYTHON_MIN_VERSION:
        print(
            f"Error: Python {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}+ is required.\n"
            f"You are using Python {sys.version_info.major}.{sys.version_info.minor}.\n"
            f"Please upgrade Python to use {APP_NAME}.",
            file=sys.stderr,
        )
        sys.exit(EXIT_ERROR)


def _setup_minimal(
    *,
    config_path: Path | None = None,
    language: str | None = None,
    log_level: str = "INFO",
    no_color: bool = False,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Initialise les composants minimaux nécessaires à la CLI.

    Cette fonction initialise uniquement les composants légers :
        - Paths (chemins)
        - Config (configuration)
        - Logger (logging)
        - I18n (internationalisation)

    Les composants lourds (EventBus, Registry, Session) sont initialisés
    uniquement si nécessaire (par exemple, pour la commande TUI).

    Args:
        config_path: Chemin vers le fichier de configuration.
        language: Langue de l'interface.
        log_level: Niveau de log.
        no_color: Désactiver les couleurs.
        verbose: Mode verbeux.
        quiet: Mode silencieux.
    """
    # Déterminer le niveau de log effectif
    if verbose:
        effective_level = "DEBUG"
    elif quiet:
        effective_level = "ERROR"
    else:
        effective_level = log_level

    # 1. Initialiser les chemins
    paths_instance = get_paths()
    if not paths_instance.is_initialized:
        paths_instance.initialize()

    # 2. Charger la configuration
    try:
        from nexusdl.core.config import ConfigManager, set_config_manager

        manager = ConfigManager(config_path=config_path)
        set_config_manager(manager)
    except Exception as e:
        # La configuration n'est pas critique, on continue avec les défauts
        logger.debug("Impossible de charger la configuration: {}", e)

    # 3. Configurer le logging
    try:
        from nexusdl.core.logger import setup_logging

        setup_logging(
            level=effective_level,
            format="text" if no_color else "rich",
            log_dir=paths_instance.logs_dir,
            colorize=not no_color,
        )
    except Exception as e:
        # Fallback sur un logging basique
        logger.remove()
        logger.add(sys.stderr, level=effective_level, colorize=not no_color)
        logger.debug("Logging configuré avec fallback: {}", e)

    # 4. Configurer l'i18n
    try:
        from nexusdl.core.i18n import setup_i18n

        effective_language = language
        if effective_language is None:
            try:
                from nexusdl.core.config import get_config
                effective_language = get_config().i18n.language
            except Exception:
                effective_language = "en"

        # Chercher le répertoire de traductions
        translations_dir = None
        try:
            import nexusdl
            package_dir = Path(nexusdl.__file__).parent
            default_dir = package_dir.parent / "data" / "translations"
            if default_dir.exists():
                translations_dir = default_dir
        except Exception:
            pass

        setup_i18n(
            language=effective_language,
            translations_dir=translations_dir,
        )
    except Exception as e:
        logger.debug("Impossible de configurer l'i18n: {}", e)


def _setup_full() -> None:
    """Initialise tous les composants (lourd).

    Utilisé uniquement pour les commandes qui nécessitent tous les composants
    (TUI, search, download, etc.).
    """
    try:
        from nexusdl.core.events import EventBus, set_event_bus

        event_bus = EventBus()
        set_event_bus(event_bus)
        # Ne pas démarrer l'EventBus ici, il sera démarré par l'app TUI
    except Exception as e:
        logger.debug("Impossible d'initialiser l'EventBus: {}", e)

    try:
        from nexusdl.core.registry import (
            ConfigLoader,
            SchemaValidator,
            SiteRegistry,
            set_site_registry,
        )

        loader = ConfigLoader()
        validator = SchemaValidator()
        registry = SiteRegistry(loader=loader, validator=validator)

        # Initialiser de manière synchrone pour la CLI
        asyncio.run(loader.start())
        asyncio.run(validator.start())
        asyncio.run(registry.start())

        set_site_registry(registry)
    except Exception as e:
        logger.warning("Impossible d'initialiser le registre: {}", e)


# ============================================================================
# COMMANDES — Implémentations
# ============================================================================


def _cmd_version(args: argparse.Namespace) -> int:
    """Commande : afficher la version.

    Args:
        args: Arguments parsés.

    Returns:
        Code de sortie.
    """
    from nexusdl.core.utils.time import format_datetime

    if args.short:
        print(APP_VERSION)
        return EXIT_SUCCESS

    print(f"{APP_NAME} v{APP_VERSION}")
    print(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    print(f"Platform: {sys.platform}")

    # Afficher les versions des dépendances principales
    try:
        import httpx
        print(f"httpx: {httpx.__version__}")
    except ImportError:
        print("httpx: not installed")

    try:
        import pydantic
        print(f"pydantic: {pydantic.__version__}")
    except ImportError:
        print("pydantic: not installed")

    try:
        import textual
        print(f"textual: {textual.__version__}")
    except ImportError:
        print("textual: not installed")

    try:
        import loguru
        print(f"loguru: {loguru.__version__}")
    except ImportError:
        print("loguru: not installed")

    return EXIT_SUCCESS


def _cmd_tui(args: argparse.Namespace) -> int:
    """Commande : lancer l'interface TUI.

    Args:
        args: Arguments parsés.

    Returns:
        Code de sortie.
    """
    # Vérifier que Textual est installé
    try:
        import textual  # noqa: F401
    except ImportError:
        raise DependencyNotInstalledError(
            "textual",
            "pip install textual",
        )

    # Initialiser tous les composants
    _setup_full()

    # Lancer l'application TUI
    from nexusdl.interfaces.cli.app import AppTheme, run_app

    theme = AppTheme(args.theme) if hasattr(args, "theme") and args.theme else AppTheme.AUTO

    try:
        run_app(
            theme=theme,
            config_path=args.config if hasattr(args, "config") else None,
            headless=getattr(args, "headless", False),
        )
        return EXIT_SUCCESS
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except Exception as e:
        logger.error("Erreur lors du lancement de l'interface TUI: {}", e)
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_ERROR


def _cmd_search(args: argparse.Namespace) -> int:
    """Commande : recherche rapide sans TUI.

    Args:
        args: Arguments parsés.

    Returns:
        Code de sortie.
    """
    query = args.query
    site_id = args.site
    limit = args.limit
    output_format = args.format

    logger.info("Recherche: query={!r}, site={}, limit={}", query, site_id, limit)

    # Initialiser les composants nécessaires
    _setup_full()

    try:
        from nexusdl.core.registry import get_site_registry

        registry = get_site_registry()

        # Déterminer les sites à rechercher
        if site_id:
            try:
                sites = [registry.get_site(site_id)]
            except Exception as e:
                print(f"Error: Site '{site_id}' not found: {e}", file=sys.stderr)
                return EXIT_ERROR
        else:
            sites = registry.list_sites(enabled_only=True, include_adult=False)[:5]

        # Rechercher sur tous les sites
        all_results = []

        async def _search_all() -> None:
            for site in sites:
                try:
                    parser = await registry.get_parser(site.id)
                    results = await parser.search(query)
                    for result in results[:limit]:
                        result._site_name = site.name  # type: ignore[attr-defined]
                        all_results.append(result)
                except Exception as e:
                    logger.warning("Erreur lors de la recherche sur {}: {}", site.id, e)

        asyncio.run(_search_all())

        # Limiter les résultats
        all_results = all_results[:limit]

        # Afficher les résultats
        if not all_results:
            print("No results found.")
            return EXIT_SUCCESS

        if output_format == "json":
            import json
            results_data = [
                {
                    "title": r.manga.title,
                    "url": r.url,
                    "site": getattr(r, "_site_name", r.manga.site),
                    "author": r.manga.author,
                    "year": r.manga.year,
                    "status": r.manga.status.value,
                    "language": r.manga.language.value,
                }
                for r in all_results
            ]
            print(json.dumps(results_data, indent=2, ensure_ascii=False))
        else:
            # Format texte
            print(f"\nFound {len(all_results)} result(s) for '{query}':\n")
            for i, result in enumerate(all_results, 1):
                site_name = getattr(result, "_site_name", result.manga.site)
                author = f" by {result.manga.author}" if result.manga.author else ""
                year = f" ({result.manga.year})" if result.manga.year else ""
                print(f"{i:3d}. {result.manga.title}{author}{year}")
                print(f"     Site: {site_name}")
                print(f"     URL:  {result.url}")
                print()

        return EXIT_SUCCESS

    except Exception as e:
        logger.error("Erreur lors de la recherche: {}", e)
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_ERROR


def _cmd_download(args: argparse.Namespace) -> int:
    """Commande : téléchargement direct.

    Args:
        args: Arguments parsés.

    Returns:
        Code de sortie.
    """
    url = args.url
    output_dir = Path(args.output) if args.output else None

    logger.info("Téléchargement: url={}, output={}", url, output_dir)

    # Initialiser les composants nécessaires
    _setup_full()

    try:
        from nexusdl.core.registry import get_site_registry

        registry = get_site_registry()

        # Résoudre le site depuis l'URL
        site = registry.get_site_by_domain(url)
        if site is None:
            print(f"Error: No site found for URL: {url}", file=sys.stderr)
            return EXIT_ERROR

        print(f"Site detected: {site.name}")

        # TODO: Implémenter le téléchargement complet
        # Pour l'instant, on affiche juste un message
        print(f"Download from {url} is not yet implemented in CLI mode.")
        print("Please use the TUI interface for full download functionality.")
        print(f"  $ {CLI_COMMAND_NAME}")

        return EXIT_SUCCESS

    except Exception as e:
        logger.error("Erreur lors du téléchargement: {}", e)
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_ERROR


def _cmd_list_sites(args: argparse.Namespace) -> int:
    """Commande : lister les sites supportés.

    Args:
        args: Arguments parsés.

    Returns:
        Code de sortie.
    """
    language_filter = args.language
    output_format = args.format
    enabled_only = not args.all

    logger.info("Liste des sites: language={}, enabled_only={}", language_filter, enabled_only)

    # Initialiser les composants nécessaires
    _setup_full()

    try:
        from nexusdl.core.registry import get_site_registry

        registry = get_site_registry()
        sites = registry.list_sites(enabled_only=enabled_only, include_adult=args.adult)

        # Filtrer par langue
        if language_filter:
            sites = [s for s in sites if s.language.value == language_filter]

        if not sites:
            print("No sites found.")
            return EXIT_SUCCESS

        if output_format == "json":
            import json
            sites_data = [
                {
                    "id": s.id,
                    "name": s.name,
                    "language": s.language.value,
                    "domains": [d.url for d in s.domains],
                    "enabled": s.enabled,
                    "adult": s.adult,
                    "capabilities": {
                        "search": s.capabilities.supports_search,
                        "download": s.capabilities.supports_download,
                        "cloudflare": s.capabilities.requires_cloudflare_bypass,
                    },
                }
                for s in sites
            ]
            print(json.dumps(sites_data, indent=2, ensure_ascii=False))
        else:
            # Format texte
            print(f"\nSupported sites ({len(sites)}):\n")
            print(f"{'ID':<25} {'Name':<30} {'Lang':<6} {'Status':<10}")
            print("-" * 75)

            for site in sites:
                status = "✓ enabled" if site.enabled else "✗ disabled"
                if site.capabilities.requires_cloudflare_bypass:
                    status += " 🛡️"
                if site.adult:
                    status += " 🔞"
                print(f"{site.id:<25} {site.name:<30} {site.language.value:<6} {status}")

            print()

        return EXIT_SUCCESS

    except Exception as e:
        logger.error("Erreur lors du listing des sites: {}", e)
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_ERROR


def _cmd_config(args: argparse.Namespace) -> int:
    """Commande : gérer la configuration.

    Args:
        args: Arguments parsés.

    Returns:
        Code de sortie.
    """
    action = args.config_action

    if action == "show":
        try:
            from nexusdl.core.config import get_config

            config = get_config()
            print(config.to_yaml())
            return EXIT_SUCCESS
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return EXIT_ERROR

    elif action == "path":
        paths = get_paths()
        print(paths.config_file)
        return EXIT_SUCCESS

    elif action == "edit":
        paths = get_paths()
        config_file = paths.config_file

        if not config_file.exists():
            # Générer le fichier par défaut
            from nexusdl.core.config import get_config
            config = get_config()
            config.generate_default_config_file(config_file)
            print(f"Generated default config: {config_file}")

        # Ouvrir avec l'éditeur par défaut
        editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "nano"))
        try:
            import subprocess
            subprocess.run([editor, str(config_file)], check=True)
            return EXIT_SUCCESS
        except Exception as e:
            print(f"Error opening editor: {e}", file=sys.stderr)
            print(f"Config file: {config_file}")
            return EXIT_ERROR

    elif action == "reset":
        paths = get_paths()
        config_file = paths.config_file

        if config_file.exists():
            if not args.force:
                response = input(f"Delete {config_file}? [y/N] ")
                if response.lower() != "y":
                    print("Cancelled.")
                    return EXIT_SUCCESS

            config_file.unlink()
            print(f"Deleted: {config_file}")
        else:
            print(f"Config file does not exist: {config_file}")

        return EXIT_SUCCESS

    elif action == "generate":
        paths = get_paths()
        config_file = paths.config_file

        if config_file.exists() and not args.force:
            print(f"Config file already exists: {config_file}")
            print("Use --force to overwrite.")
            return EXIT_ERROR

        from nexusdl.core.config import get_config
        config = get_config()
        config.generate_default_config_file(config_file)
        print(f"Generated: {config_file}")
        return EXIT_SUCCESS

    else:
        print(f"Unknown config action: {action}", file=sys.stderr)
        return EXIT_USAGE_ERROR


def _cmd_validate(args: argparse.Namespace) -> int:
    """Commande : valider la configuration.

    Args:
        args: Arguments parsés.

    Returns:
        Code de sortie.
    """
    paths = get_paths()
    config_file = paths.config_file

    if not config_file.exists():
        print(f"Config file not found: {config_file}")
        return EXIT_ERROR

    try:
        from nexusdl.core.registry import SchemaValidator, validate_sites_file_quick, format_validation_report

        # Valider la configuration principale
        from nexusdl.core.config import get_config
        config = get_config()
        errors = config.model_validate(config.model_dump())
        print("✓ Main configuration is valid")

        # Valider sites.yaml
        try:
            import nexusdl
            package_dir = Path(nexusdl.__file__).parent
            sites_file = package_dir / "core" / "registry" / "sites.yaml"

            if sites_file.exists():
                result = asyncio.run(validate_sites_file_quick(sites_file))
                if result.is_valid:
                    print(f"✓ sites.yaml is valid ({result.sites_count} sites)")
                else:
                    print(f"✗ sites.yaml has errors:")
                    print(format_validation_report(result))
                    return EXIT_CONFIG_ERROR
        except Exception as e:
            print(f"⚠ Could not validate sites.yaml: {e}")

        print("\n✓ All validations passed")
        return EXIT_SUCCESS

    except Exception as e:
        print(f"✗ Validation failed: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Commande : diagnostic du système.

    Args:
        args: Arguments parsés.

    Returns:
        Code de sortie.
    """
    print(f"{APP_NAME} Doctor - System Diagnostics\n")
    print("=" * 60)

    issues: list[str] = []
    warnings: list[str] = []

    # 1. Version de Python
    print(f"\n[1/8] Python version")
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= PYTHON_MIN_VERSION:
        print(f"  ✓ Python {py_version}")
    else:
        print(f"  ✗ Python {py_version} (requires {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}+)")
        issues.append(f"Python version too old: {py_version}")

    # 2. Dépendances obligatoires
    print(f"\n[2/8] Required dependencies")
    required_deps = ["httpx", "pydantic", "loguru", "pyyaml", "platformdirs"]
    for dep in required_deps:
        try:
            mod = __import__(dep)
            version = getattr(mod, "__version__", "unknown")
            print(f"  ✓ {dep} {version}")
        except ImportError:
            print(f"  ✗ {dep} NOT INSTALLED")
            issues.append(f"Missing required dependency: {dep}")

    # 3. Dépendances optionnelles
    print(f"\n[3/8] Optional dependencies")
    optional_deps = {
        "textual": "pip install textual",
        "pillow": "pip install pillow",
        "playwright": "pip install playwright && playwright install",
        "img2pdf": "pip install img2pdf",
        "cryptography": "pip install cryptography",
        "rich": "pip install rich",
    }
    for dep, install_cmd in optional_deps.items():
        try:
            mod = __import__(dep)
            version = getattr(mod, "__version__", "unknown")
            print(f"  ✓ {dep} {version}")
        except ImportError:
            print(f"  ○ {dep} not installed (optional)")
            warnings.append(f"Optional dependency not installed: {dep} ({install_cmd})")

    # 4. Chemins
    print(f"\n[4/8] Paths")
    paths = get_paths()
    for name, path in [
        ("config", paths.config_dir),
        ("data", paths.data_dir),
        ("cache", paths.cache_dir),
        ("logs", paths.logs_dir),
    ]:
        if path.exists():
            writable = os.access(path, os.W_OK)
            status = "✓" if writable else "✗ (not writable)"
            print(f"  {status} {name}: {path}")
            if not writable:
                issues.append(f"Path not writable: {path}")
        else:
            print(f"  ○ {name}: {path} (not created yet)")

    # 5. Configuration
    print(f"\n[5/8] Configuration")
    try:
        from nexusdl.core.config import get_config
        config = get_config()
        print(f"  ✓ Configuration loaded")
        print(f"    Language: {config.app.language}")
        print(f"    Theme: {config.app.theme}")
        print(f"    Log level: {config.logging.level}")
    except Exception as e:
        print(f"  ✗ Configuration error: {e}")
        issues.append(f"Configuration error: {e}")

    # 6. Traductions
    print(f"\n[6/8] Translations")
    try:
        from nexusdl.core.i18n import get_i18n_manager
        manager = get_i18n_manager()
        if manager:
            stats = manager.get_stats()
            print(f"  ✓ I18n initialized")
            print(f"    Current language: {stats.current_language}")
            print(f"    Loaded files: {stats.loaded_files}")
        else:
            print(f"  ○ I18n not initialized")
    except Exception as e:
        print(f"  ✗ I18n error: {e}")
        warnings.append(f"I18n error: {e}")

    # 7. Registre des sites
    print(f"\n[7/8] Site registry")
    try:
        from nexusdl.core.registry import get_site_registry
        registry = get_site_registry()
        print(f"  ✓ Registry initialized")
        print(f"    Sites: {registry.sites_count}")
        print(f"    Enabled: {registry.enabled_sites_count}")
    except Exception as e:
        print(f"  ○ Registry not initialized: {e}")
        warnings.append(f"Registry not initialized: {e}")

    # 8. Espace disque
    print(f"\n[8/8] Disk space")
    try:
        import shutil
        data_dir = paths.data_dir
        usage = shutil.disk_usage(data_dir if data_dir.exists() else Path.home())
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        percent_used = (usage.used / usage.total) * 100

        print(f"  ✓ Free: {free_gb:.1f} GB / {total_gb:.1f} GB ({percent_used:.1f}% used)")

        if free_gb < 1.0:
            issues.append(f"Low disk space: {free_gb:.1f} GB free")
            print(f"  ✗ WARNING: Low disk space!")
        elif free_gb < 5.0:
            warnings.append(f"Disk space getting low: {free_gb:.1f} GB free")
    except Exception as e:
        print(f"  ○ Could not check disk space: {e}")

    # Résumé
    print("\n" + "=" * 60)
    if issues:
        print(f"\n✗ {len(issues)} issue(s) found:")
        for issue in issues:
            print(f"  - {issue}")
    if warnings:
        print(f"\n○ {len(warnings)} warning(s):")
        for warning in warnings:
            print(f"  - {warning}")
    if not issues and not warnings:
        print("\n✓ All checks passed!")

    return EXIT_ERROR if issues else EXIT_SUCCESS


def _cmd_cache(args: argparse.Namespace) -> int:
    """Commande : gérer le cache.

    Args:
        args: Arguments parsés.

    Returns:
        Code de sortie.
    """
    action = args.cache_action
    paths = get_paths()
    cache_dir = paths.cache_dir

    if action == "stats":
        if not cache_dir.exists():
            print("Cache directory does not exist yet.")
            return EXIT_SUCCESS

        # Calculer la taille
        total_size = 0
        file_count = 0
        for item in cache_dir.rglob("*"):
            if item.is_file():
                total_size += item.stat().st_size
                file_count += 1

        from nexusdl.core.utils.text import format_size
        print(f"Cache directory: {cache_dir}")
        print(f"Files: {file_count}")
        print(f"Total size: {format_size(total_size)}")
        return EXIT_SUCCESS

    elif action == "clear":
        if not cache_dir.exists():
            print("Cache directory does not exist.")
            return EXIT_SUCCESS

        if not args.force:
            response = input(f"Clear cache in {cache_dir}? [y/N] ")
            if response.lower() != "y":
                print("Cancelled.")
                return EXIT_SUCCESS

        import shutil
        try:
            shutil.rmtree(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            print(f"Cache cleared: {cache_dir}")
            return EXIT_SUCCESS
        except Exception as e:
            print(f"Error clearing cache: {e}", file=sys.stderr)
            return EXIT_ERROR

    elif action == "path":
        print(cache_dir)
        return EXIT_SUCCESS

    else:
        print(f"Unknown cache action: {action}", file=sys.stderr)
        return EXIT_USAGE_ERROR


def _cmd_completion(args: argparse.Namespace) -> int:
    """Commande : générer les scripts de complétion shell.

    Args:
        args: Arguments parsés.

    Returns:
        Code de sortie.
    """
    shell = args.shell

    if shell == "bash":
        script = f'''
# Bash completion for {CLI_COMMAND_NAME}
_{CLI_COMMAND_NAME}_completion() {{
    local cur prev commands
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"
    commands="search download list-sites config validate version doctor cache completion"

    if [[ ${{COMP_CWORD}} -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "${{commands}}" -- "${{cur}}") )
        return 0
    fi

    case "${{prev}}" in
        config)
            COMPREPLY=( $(compgen -W "show path edit reset generate" -- "${{cur}}") )
            ;;
        cache)
            COMPREPLY=( $(compgen -W "stats clear path" -- "${{cur}}") )
            ;;
        completion)
            COMPREPLY=( $(compgen -W "bash zsh fish" -- "${{cur}}") )
            ;;
    esac
}}
complete -F _{CLI_COMMAND_NAME}_completion {CLI_COMMAND_NAME}
'''
        print(script)
        return EXIT_SUCCESS

    elif shell == "zsh":
        script = f'''
# Zsh completion for {CLI_COMMAND_NAME}
_{CLI_COMMAND_NAME}() {{
    local -a commands
    commands=(
        'search:Search for manga'
        'download:Download from URL'
        'list-sites:List supported sites'
        'config:Manage configuration'
        'validate:Validate configuration'
        'version:Show version'
        'doctor:Run diagnostics'
        'cache:Manage cache'
        'completion:Generate shell completion'
    )
    _describe 'command' commands
}}
compdef _{CLI_COMMAND_NAME} {CLI_COMMAND_NAME}
'''
        print(script)
        return EXIT_SUCCESS

    elif shell == "fish":
        script = f'''
# Fish completion for {CLI_COMMAND_NAME}
complete -c {CLI_COMMAND_NAME} -n '__fish_use_subcommand' -a search -d 'Search for manga'
complete -c {CLI_COMMAND_NAME} -n '__fish_use_subcommand' -a download -d 'Download from URL'
complete -c {CLI_COMMAND_NAME} -n '__fish_use_subcommand' -a list-sites -d 'List supported sites'
complete -c {CLI_COMMAND_NAME} -n '__fish_use_subcommand' -a config -d 'Manage configuration'
complete -c {CLI_COMMAND_NAME} -n '__fish_use_subcommand' -a validate -d 'Validate configuration'
complete -c {CLI_COMMAND_NAME} -n '__fish_use_subcommand' -a version -d 'Show version'
complete -c {CLI_COMMAND_NAME} -n '__fish_use_subcommand' -a doctor -d 'Run diagnostics'
complete -c {CLI_COMMAND_NAME} -n '__fish_use_subcommand' -a cache -d 'Manage cache'
complete -c {CLI_COMMAND_NAME} -n '__fish_use_subcommand' -a completion -d 'Generate shell completion'
'''
        print(script)
        return EXIT_SUCCESS

    else:
        print(f"Unknown shell: {shell}", file=sys.stderr)
        return EXIT_USAGE_ERROR


# ============================================================================
# PARSING DES ARGUMENTS
# ============================================================================


def _build_parser() -> argparse.ArgumentParser:
    """Construit le parseur d'arguments.

    Returns:
        Instance de ArgumentParser.
    """
    parser = argparse.ArgumentParser(
        prog=CLI_COMMAND_NAME,
        description=CLI_DESCRIPTION,
        epilog=CLI_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Arguments globaux
    parser.add_argument(
        "--config",
        type=Path,
        metavar="PATH",
        help="Path to configuration file",
    )
    parser.add_argument(
        "--language",
        type=str,
        metavar="LANG",
        help="Interface language (ISO 639-1 code, e.g., 'en', 'fr')",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        metavar="LEVEL",
        default="INFO",
        choices=["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log level (default: INFO)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose mode (DEBUG level)",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Enable quiet mode (ERROR level only)",
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"{APP_NAME} {APP_VERSION}",
    )

    # Sous-commandes
    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        description="Available commands",
    )

    # Commande : TUI (défaut)
    tui_parser = subparsers.add_parser(
        "tui",
        help="Launch the TUI interface (default)",
        description="Launch the Text User Interface",
    )
    tui_parser.add_argument(
        "--theme",
        type=str,
        choices=["light", "dark", "system", "auto"],
        default="auto",
        help="UI theme (default: auto)",
    )
    tui_parser.add_argument(
        "--headless",
        action="store_true",
        help=argparse.SUPPRESS,  # Option cachée pour les tests
    )

    # Commande : search
    search_parser = subparsers.add_parser(
        "search",
        help="Search for manga/webtoons/comics",
        description="Search for manga across supported sites",
    )
    search_parser.add_argument(
        "query",
        type=str,
        help="Search query",
    )
    search_parser.add_argument(
        "--site",
        type=str,
        metavar="SITE_ID",
        help="Search only on a specific site",
    )
    search_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        metavar="N",
        help="Maximum number of results (default: 20)",
    )
    search_parser.add_argument(
        "--format",
        type=str,
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    # Commande : download
    download_parser = subparsers.add_parser(
        "download",
        help="Download manga from URL",
        description="Download manga directly from a URL",
    )
    download_parser.add_argument(
        "url",
        type=str,
        help="URL to download",
    )
    download_parser.add_argument(
        "-o", "--output",
        type=str,
        metavar="DIR",
        help="Output directory",
    )

    # Commande : list-sites
    list_sites_parser = subparsers.add_parser(
        "list-sites",
        help="List supported sites",
        description="List all supported manga/webtoon sites",
    )
    list_sites_parser.add_argument(
        "--language",
        type=str,
        metavar="LANG",
        help="Filter by language (ISO 639-1 code)",
    )
    list_sites_parser.add_argument(
        "--all",
        action="store_true",
        help="Include disabled sites",
    )
    list_sites_parser.add_argument(
        "--adult",
        action="store_true",
        help="Include adult sites",
    )
    list_sites_parser.add_argument(
        "--format",
        type=str,
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    # Commande : config
    config_parser = subparsers.add_parser(
        "config",
        help="Manage configuration",
        description="Manage application configuration",
    )
    config_parser.add_argument(
        "config_action",
        type=str,
        choices=["show", "path", "edit", "reset", "generate"],
        help="Configuration action",
    )
    config_parser.add_argument(
        "--force",
        action="store_true",
        help="Force action without confirmation",
    )

    # Commande : validate
    subparsers.add_parser(
        "validate",
        help="Validate configuration",
        description="Validate configuration files",
    )

    # Commande : doctor
    subparsers.add_parser(
        "doctor",
        help="Run system diagnostics",
        description="Check system health and dependencies",
    )

    # Commande : cache
    cache_parser = subparsers.add_parser(
        "cache",
        help="Manage cache",
        description="Manage application cache",
    )
    cache_parser.add_argument(
        "cache_action",
        type=str,
        choices=["stats", "clear", "path"],
        help="Cache action",
    )
    cache_parser.add_argument(
        "--force",
        action="store_true",
        help="Force action without confirmation",
    )

    # Commande : completion
    completion_parser = subparsers.add_parser(
        "completion",
        help="Generate shell completion script",
        description="Generate shell completion scripts",
    )
    completion_parser.add_argument(
        "shell",
        type=str,
        choices=["bash", "zsh", "fish"],
        help="Shell type",
    )

    return parser


# ============================================================================
# POINT D'ENTRÉE PRINCIPAL
# ============================================================================


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée principal de la CLI.

    Args:
        argv: Arguments de la ligne de commande (défaut: sys.argv[1:]).

    Returns:
        Code de sortie.
    """
    # Vérifier la version de Python
    _check_python_version()

    # Parser les arguments
    parser = _build_parser()

    # Si aucun argument, lancer la TUI par défaut
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        argv = ["tui"]

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else EXIT_ERROR

    # Initialisation minimale
    try:
        _setup_minimal(
            config_path=args.config,
            language=args.language,
            log_level=args.log_level,
            no_color=args.no_color,
            verbose=args.verbose,
            quiet=args.quiet,
        )
    except Exception as e:
        print(f"Error during initialization: {e}", file=sys.stderr)
        return EXIT_ERROR

    # Dispatch vers la commande appropriée
    command = args.command

    try:
        if command == "tui" or command is None:
            return _cmd_tui(args)
        elif command == "search":
            return _cmd_search(args)
        elif command == "download":
            return _cmd_download(args)
        elif command == "list-sites":
            return _cmd_list_sites(args)
        elif command == "config":
            return _cmd_config(args)
        elif command == "validate":
            return _cmd_validate(args)
        elif command == "version":
            # La version est déjà gérée par --version, mais on la supporte aussi comme commande
            args.short = False
            return _cmd_version(args)
        elif command == "doctor":
            return _cmd_doctor(args)
        elif command == "cache":
            return _cmd_cache(args)
        elif command == "completion":
            return _cmd_completion(args)
        else:
            raise CommandNotFoundError(command)

    except DependencyNotInstalledError as e:
        print(f"Error: {e}", file=sys.stderr)
        return e.exit_code
    except CommandNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return e.exit_code
    except CLIError as e:
        print(f"Error: {e}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as e:
        logger.exception("Erreur non gérée dans la CLI")
        print(f"Unexpected error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return EXIT_ERROR


# ============================================================================
# POINT D'ENTRÉE DU SCRIPT
# ============================================================================


if __name__ == "__main__":
    sys.exit(main())


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "CLI_COMMAND_NAME",
    "CLI_DESCRIPTION",
    "CLI_EPILOG",
    "EXIT_SUCCESS",
    "EXIT_ERROR",
    "EXIT_USAGE_ERROR",
    "EXIT_CONFIG_ERROR",
    "EXIT_DEPENDENCY_ERROR",
    "EXIT_INTERRUPTED",
    # Exceptions
    "CLIError",
    "CommandNotFoundError",
    "DependencyNotInstalledError",
    # Fonctions principales
    "main",
    # Helpers
    "check_python_version",
    "setup_minimal",
    "setup_full",
]

# Exports avec underscore retiré pour usage public
check_python_version = _check_python_version
setup_minimal = _setup_minimal
setup_full = _setup_full
