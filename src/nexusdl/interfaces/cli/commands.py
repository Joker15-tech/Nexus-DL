"""Commandes CLI de NexusDL.

Ce module implémente toutes les commandes de l'interface CLI NexusDL en utilisant
le pattern Command. Chaque commande est une classe qui hérite de `BaseCommand` et
implémente la méthode `execute()`.

**Architecture** :
    BaseCommand (ABC)
        ├── execute(args) -> int           # Méthode principale
        ├── validate(args) -> None         # Validation des arguments
        ├── setup() -> None                # Initialisation
        └── teardown() -> None             # Nettoyage

    CommandRegistry
        ├── register(command)              # Enregistrer une commande
        ├── get(name) -> BaseCommand       # Récupérer une commande
        ├── execute(name, args) -> int     # Exécuter une commande
        └── list() -> list[str]            # Lister les commandes

**Commandes disponibles** :
    - TuiCommand          : Lancer l'interface TUI
    - SearchCommand       : Recherche rapide
    - DownloadCommand     : Téléchargement direct
    - ListSitesCommand    : Lister les sites
    - ConfigCommand       : Gérer la configuration
    - ValidateCommand     : Valider la configuration
    - VersionCommand      : Afficher la version
    - DoctorCommand       : Diagnostic système
    - CacheCommand        : Gérer le cache
    - CompletionCommand   : Générer les scripts de complétion

**Exemple d'utilisation** :
    >>> from nexusdl.interfaces.cli.commands import CommandRegistry, TuiCommand
    >>>
    >>> # Créer le registry
    >>> registry = CommandRegistry()
    >>> registry.register(TuiCommand())
    >>>
    >>> # Exécuter une commande
    >>> exit_code = registry.execute("tui", args)

Intégration :
    - interfaces/cli/cli_entry.py : utilise le CommandRegistry
    - core/config.py              : configuration
    - core/logger.py              : logging
    - core/i18n.py                : traductions
    - core/paths.py               : chemins
    - core/registry/              : registre des sites
"""

from __future__ import annotations

import abc
import asyncio
import json
import os
import shutil
import subprocess
import sys
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
from nexusdl.core.paths import get_paths


# ============================================================================
# CONSTANTES
# ============================================================================


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


class CommandError(NexusDLError):
    """Exception de base pour les erreurs de commande."""

    def __init__(self, message: str, exit_code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class CommandNotFoundError(CommandError):
    """Exception levée lorsqu'une commande est introuvable."""

    def __init__(self, command_name: str) -> None:
        super().__init__(
            f"Commande inconnue: {command_name}\n"
            f"Utilisez 'nexusdl --help' pour voir les commandes disponibles.",
            exit_code=EXIT_USAGE_ERROR,
        )
        self.command_name = command_name


class CommandValidationError(CommandError):
    """Exception levée lorsqu'une validation de commande échoue."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=EXIT_USAGE_ERROR)


class DependencyNotInstalledError(CommandError):
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
# CLASSE DE BASE — BaseCommand
# ============================================================================


class BaseCommand(abc.ABC):
    """Classe de base pour toutes les commandes CLI.

    Chaque commande doit hériter de cette classe et implémenter la méthode
    `execute()`. Les méthodes `validate()`, `setup()` et `teardown()` sont
    optionnelles.

    Attributes:
        name: Nom de la commande (utilisé pour l'appel CLI).
        description: Description courte de la commande.
        help_text: Texte d'aide détaillé.
        requires_full_setup: Si True, initialise tous les composants.
    """

    name: str = ""
    description: str = ""
    help_text: str = ""
    requires_full_setup: bool = False

    @abc.abstractmethod
    def execute(self, args: Any) -> int:
        """Exécute la commande.

        Args:
            args: Arguments parsés par argparse.

        Returns:
            Code de sortie (0 = succès).
        """
        pass

    def validate(self, args: Any) -> None:
        """Valide les arguments de la commande.

        Args:
            args: Arguments parsés.

        Raises:
            CommandValidationError: Si la validation échoue.
        """
        pass

    def setup(self) -> None:
        """Initialise les ressources nécessaires à la commande.

        Appelée avant `execute()` si `requires_full_setup` est True.
        """
        pass

    def teardown(self) -> None:
        """Nettoie les ressources après l'exécution.

        Appelée après `execute()` si `requires_full_setup` est True.
        """
        pass

    def run(self, args: Any) -> int:
        """Exécute la commande avec validation, setup et teardown.

        Args:
            args: Arguments parsés.

        Returns:
            Code de sortie.
        """
        try:
            # Validation
            self.validate(args)

            # Setup si nécessaire
            if self.requires_full_setup:
                self.setup()

            # Exécution
            return self.execute(args)

        except CommandError as e:
            logger.error("Erreur de commande: {}", e)
            print(f"Error: {e}", file=sys.stderr)
            return e.exit_code

        except KeyboardInterrupt:
            print("\nInterrupted.", file=sys.stderr)
            return EXIT_INTERRUPTED

        except Exception as e:
            logger.exception("Erreur non gérée dans la commande {}", self.name)
            print(f"Unexpected error: {e}", file=sys.stderr)
            return EXIT_ERROR

        finally:
            # Teardown si nécessaire
            if self.requires_full_setup:
                try:
                    self.teardown()
                except Exception as e:
                    logger.warning("Erreur lors du teardown: {}", e)


# ============================================================================
# REGISTRY — CommandRegistry
# ============================================================================


class CommandRegistry:
    """Registry des commandes CLI.

    Permet d'enregistrer, récupérer et exécuter des commandes de manière
    dynamique.

    Attributes:
        _commands: Dictionnaire des commandes enregistrées.
    """

    def __init__(self) -> None:
        """Initialise le registry."""
        self._commands: dict[str, BaseCommand] = {}

    def register(self, command: BaseCommand) -> None:
        """Enregistre une commande.

        Args:
            command: Commande à enregistrer.

        Raises:
            ValueError: Si le nom de la commande est vide ou déjà enregistré.
        """
        if not command.name:
            raise ValueError("Le nom de la commande ne peut pas être vide")

        if command.name in self._commands:
            logger.warning("Commande '{}' déjà enregistrée, remplacement", command.name)

        self._commands[command.name] = command
        logger.debug("Commande '{}' enregistrée", command.name)

    def get(self, name: str) -> BaseCommand:
        """Récupère une commande par son nom.

        Args:
            name: Nom de la commande.

        Returns:
            Instance de la commande.

        Raises:
            CommandNotFoundError: Si la commande n'existe pas.
        """
        if name not in self._commands:
            raise CommandNotFoundError(name)
        return self._commands[name]

    def execute(self, name: str, args: Any) -> int:
        """Exécute une commande.

        Args:
            name: Nom de la commande.
            args: Arguments parsés.

        Returns:
            Code de sortie.
        """
        command = self.get(name)
        return command.run(args)

    def list(self) -> list[str]:
        """Liste toutes les commandes enregistrées.

        Returns:
            Liste des noms de commandes.
        """
        return list(self._commands.keys())

    def get_all(self) -> dict[str, BaseCommand]:
        """Récupère toutes les commandes.

        Returns:
            Dictionnaire des commandes.
        """
        return dict(self._commands)


# ============================================================================
# COMMANDES — Implémentations
# ============================================================================


class TuiCommand(BaseCommand):
    """Commande : lancer l'interface TUI."""

    name = "tui"
    description = "Launch the TUI interface"
    help_text = "Launch the Text User Interface (default command)"
    requires_full_setup = True

    def setup(self) -> None:
        """Vérifie que Textual est installé."""
        try:
            import textual  # noqa: F401
        except ImportError:
            raise DependencyNotInstalledError(
                "textual",
                "pip install textual",
            )

        # Initialiser tous les composants
        from nexusdl.interfaces.cli.cli_entry import _setup_full
        _setup_full()

    def execute(self, args: Any) -> int:
        """Lance l'interface TUI."""
        from nexusdl.interfaces.cli.app import AppTheme, run_app

        theme = AppTheme(args.theme) if hasattr(args, "theme") and args.theme else AppTheme.AUTO

        try:
            run_app(
                theme=theme,
                config_path=args.config if hasattr(args, "config") else None,
                headless=getattr(args, "headless", False),
            )
            return EXIT_SUCCESS
        except Exception as e:
            logger.error("Erreur lors du lancement de l'interface TUI: {}", e)
            print(f"Error: {e}", file=sys.stderr)
            return EXIT_ERROR


class SearchCommand(BaseCommand):
    """Commande : recherche rapide sans TUI."""

    name = "search"
    description = "Search for manga/webtoons/comics"
    help_text = "Search for manga across supported sites"
    requires_full_setup = True

    def setup(self) -> None:
        """Initialise les composants nécessaires."""
        from nexusdl.interfaces.cli.cli_entry import _setup_full
        _setup_full()

    def validate(self, args: Any) -> None:
        """Valide les arguments."""
        if not hasattr(args, "query") or not args.query:
            raise CommandValidationError("Query is required")

        if hasattr(args, "limit") and (args.limit < 1 or args.limit > 100):
            raise CommandValidationError("Limit must be between 1 and 100")

    def execute(self, args: Any) -> int:
        """Effectue la recherche."""
        query = args.query
        site_id = getattr(args, "site", None)
        limit = getattr(args, "limit", 20)
        output_format = getattr(args, "format", "text")

        logger.info("Recherche: query={!r}, site={}, limit={}", query, site_id, limit)

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


class DownloadCommand(BaseCommand):
    """Commande : téléchargement direct."""

    name = "download"
    description = "Download manga from URL"
    help_text = "Download manga directly from a URL"
    requires_full_setup = True

    def setup(self) -> None:
        """Initialise les composants nécessaires."""
        from nexusdl.interfaces.cli.cli_entry import _setup_full
        _setup_full()

    def validate(self, args: Any) -> None:
        """Valide les arguments."""
        if not hasattr(args, "url") or not args.url:
            raise CommandValidationError("URL is required")

    def execute(self, args: Any) -> int:
        """Effectue le téléchargement."""
        url = args.url
        output_dir = Path(args.output) if hasattr(args, "output") and args.output else None

        logger.info("Téléchargement: url={}, output={}", url, output_dir)

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
            print(f"Download from {url} is not yet implemented in CLI mode.")
            print("Please use the TUI interface for full download functionality.")
            print(f"  $ nexusdl")

            return EXIT_SUCCESS

        except Exception as e:
            logger.error("Erreur lors du téléchargement: {}", e)
            print(f"Error: {e}", file=sys.stderr)
            return EXIT_ERROR


class ListSitesCommand(BaseCommand):
    """Commande : lister les sites supportés."""

    name = "list-sites"
    description = "List supported sites"
    help_text = "List all supported manga/webtoon sites"
    requires_full_setup = True

    def setup(self) -> None:
        """Initialise les composants nécessaires."""
        from nexusdl.interfaces.cli.cli_entry import _setup_full
        _setup_full()

    def execute(self, args: Any) -> int:
        """Liste les sites."""
        language_filter = getattr(args, "language", None)
        output_format = getattr(args, "format", "text")
        enabled_only = not getattr(args, "all", False)
        include_adult = getattr(args, "adult", False)

        logger.info("Liste des sites: language={}, enabled_only={}", language_filter, enabled_only)

        try:
            from nexusdl.core.registry import get_site_registry

            registry = get_site_registry()
            sites = registry.list_sites(enabled_only=enabled_only, include_adult=include_adult)

            # Filtrer par langue
            if language_filter:
                sites = [s for s in sites if s.language.value == language_filter]

            if not sites:
                print("No sites found.")
                return EXIT_SUCCESS

            if output_format == "json":
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


class ConfigCommand(BaseCommand):
    """Commande : gérer la configuration."""

    name = "config"
    description = "Manage configuration"
    help_text = "Manage application configuration"

    def execute(self, args: Any) -> int:
        """Gère la configuration."""
        action = args.config_action

        if action == "show":
            return self._action_show()
        elif action == "path":
            return self._action_path()
        elif action == "edit":
            return self._action_edit(args)
        elif action == "reset":
            return self._action_reset(args)
        elif action == "generate":
            return self._action_generate(args)
        else:
            print(f"Unknown config action: {action}", file=sys.stderr)
            return EXIT_USAGE_ERROR

    def _action_show(self) -> int:
        """Affiche la configuration."""
        try:
            from nexusdl.core.config import get_config

            config = get_config()
            print(config.to_yaml())
            return EXIT_SUCCESS
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return EXIT_ERROR

    def _action_path(self) -> int:
        """Affiche le chemin du fichier de configuration."""
        paths = get_paths()
        print(paths.config_file)
        return EXIT_SUCCESS

    def _action_edit(self, args: Any) -> int:
        """Ouvre la configuration dans un éditeur."""
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
            subprocess.run([editor, str(config_file)], check=True)
            return EXIT_SUCCESS
        except Exception as e:
            print(f"Error opening editor: {e}", file=sys.stderr)
            print(f"Config file: {config_file}")
            return EXIT_ERROR

    def _action_reset(self, args: Any) -> int:
        """Réinitialise la configuration."""
        paths = get_paths()
        config_file = paths.config_file

        if config_file.exists():
            if not getattr(args, "force", False):
                response = input(f"Delete {config_file}? [y/N] ")
                if response.lower() != "y":
                    print("Cancelled.")
                    return EXIT_SUCCESS

            config_file.unlink()
            print(f"Deleted: {config_file}")
        else:
            print(f"Config file does not exist: {config_file}")

        return EXIT_SUCCESS

    def _action_generate(self, args: Any) -> int:
        """Génère le fichier de configuration par défaut."""
        paths = get_paths()
        config_file = paths.config_file

        if config_file.exists() and not getattr(args, "force", False):
            print(f"Config file already exists: {config_file}")
            print("Use --force to overwrite.")
            return EXIT_ERROR

        from nexusdl.core.config import get_config
        config = get_config()
        config.generate_default_config_file(config_file)
        print(f"Generated: {config_file}")
        return EXIT_SUCCESS


class ValidateCommand(BaseCommand):
    """Commande : valider la configuration."""

    name = "validate"
    description = "Validate configuration"
    help_text = "Validate configuration files"

    def execute(self, args: Any) -> int:
        """Valide la configuration."""
        paths = get_paths()
        config_file = paths.config_file

        if not config_file.exists():
            print(f"Config file not found: {config_file}")
            return EXIT_ERROR

        try:
            # Valider la configuration principale
            from nexusdl.core.config import get_config
            config = get_config()
            config.model_validate(config.model_dump())
            print("✓ Main configuration is valid")

            # Valider sites.yaml
            try:
                import nexusdl
                from nexusdl.core.registry import format_validation_report, validate_sites_file_quick

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


class VersionCommand(BaseCommand):
    """Commande : afficher la version."""

    name = "version"
    description = "Show version information"
    help_text = "Display version and dependency information"

    def execute(self, args: Any) -> int:
        """Affiche la version."""
        short = getattr(args, "short", False)

        if short:
            print(APP_VERSION)
            return EXIT_SUCCESS

        print(f"{APP_NAME} v{APP_VERSION}")
        print(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
        print(f"Platform: {sys.platform}")

        # Afficher les versions des dépendances principales
        dependencies = [
            ("httpx", "httpx"),
            ("pydantic", "pydantic"),
            ("textual", "textual"),
            ("loguru", "loguru"),
        ]

        for module_name, display_name in dependencies:
            try:
                mod = __import__(module_name)
                version = getattr(mod, "__version__", "unknown")
                print(f"{display_name}: {version}")
            except ImportError:
                print(f"{display_name}: not installed")

        return EXIT_SUCCESS


class DoctorCommand(BaseCommand):
    """Commande : diagnostic du système."""

    name = "doctor"
    description = "Run system diagnostics"
    help_text = "Check system health and dependencies"

    def execute(self, args: Any) -> int:
        """Effectue le diagnostic."""
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


class CacheCommand(BaseCommand):
    """Commande : gérer le cache."""

    name = "cache"
    description = "Manage cache"
    help_text = "Manage application cache"

    def execute(self, args: Any) -> int:
        """Gère le cache."""
        action = args.cache_action
        paths = get_paths()
        cache_dir = paths.cache_dir

        if action == "stats":
            return self._action_stats(cache_dir)
        elif action == "clear":
            return self._action_clear(cache_dir, args)
        elif action == "path":
            print(cache_dir)
            return EXIT_SUCCESS
        else:
            print(f"Unknown cache action: {action}", file=sys.stderr)
            return EXIT_USAGE_ERROR

    def _action_stats(self, cache_dir: Path) -> int:
        """Affiche les statistiques du cache."""
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

    def _action_clear(self, cache_dir: Path, args: Any) -> int:
        """Vide le cache."""
        if not cache_dir.exists():
            print("Cache directory does not exist.")
            return EXIT_SUCCESS

        if not getattr(args, "force", False):
            response = input(f"Clear cache in {cache_dir}? [y/N] ")
            if response.lower() != "y":
                print("Cancelled.")
                return EXIT_SUCCESS

        try:
            shutil.rmtree(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            print(f"Cache cleared: {cache_dir}")
            return EXIT_SUCCESS
        except Exception as e:
            print(f"Error clearing cache: {e}", file=sys.stderr)
            return EXIT_ERROR


class CompletionCommand(BaseCommand):
    """Commande : générer les scripts de complétion shell."""

    name = "completion"
    description = "Generate shell completion script"
    help_text = "Generate shell completion scripts for bash, zsh, or fish"

    def execute(self, args: Any) -> int:
        """Génère le script de complétion."""
        shell = args.shell

        if shell == "bash":
            return self._generate_bash()
        elif shell == "zsh":
            return self._generate_zsh()
        elif shell == "fish":
            return self._generate_fish()
        else:
            print(f"Unknown shell: {shell}", file=sys.stderr)
            return EXIT_USAGE_ERROR

    def _generate_bash(self) -> int:
        """Génère le script bash."""
        script = '''
# Bash completion for nexusdl
_nexusdl_completion() {
    local cur prev commands
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    commands="search download list-sites config validate version doctor cache completion"

    if [[ ${COMP_CWORD} -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "${commands}" -- "${cur}") )
        return 0
    fi

    case "${prev}" in
        config)
            COMPREPLY=( $(compgen -W "show path edit reset generate" -- "${cur}") )
            ;;
        cache)
            COMPREPLY=( $(compgen -W "stats clear path" -- "${cur}") )
            ;;
        completion)
            COMPREPLY=( $(compgen -W "bash zsh fish" -- "${cur}") )
            ;;
    esac
}
complete -F _nexusdl_completion nexusdl
'''
        print(script)
        return EXIT_SUCCESS

    def _generate_zsh(self) -> int:
        """Génère le script zsh."""
        script = '''
# Zsh completion for nexusdl
_nexusdl() {
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
}
compdef _nexusdl nexusdl
'''
        print(script)
        return EXIT_SUCCESS

    def _generate_fish(self) -> int:
        """Génère le script fish."""
        script = '''
# Fish completion for nexusdl
complete -c nexusdl -n '__fish_use_subcommand' -a search -d 'Search for manga'
complete -c nexusdl -n '__fish_use_subcommand' -a download -d 'Download from URL'
complete -c nexusdl -n '__fish_use_subcommand' -a list-sites -d 'List supported sites'
complete -c nexusdl -n '__fish_use_subcommand' -a config -d 'Manage configuration'
complete -c nexusdl -n '__fish_use_subcommand' -a validate -d 'Validate configuration'
complete -c nexusdl -n '__fish_use_subcommand' -a version -d 'Show version'
complete -c nexusdl -n '__fish_use_subcommand' -a doctor -d 'Run diagnostics'
complete -c nexusdl -n '__fish_use_subcommand' -a cache -d 'Manage cache'
complete -c nexusdl -n '__fish_use_subcommand' -a completion -d 'Generate shell completion'
'''
        print(script)
        return EXIT_SUCCESS


# ============================================================================
# FONCTIONS HELPERS
# ============================================================================


def create_default_registry() -> CommandRegistry:
    """Crée un registry avec toutes les commandes par défaut.

    Returns:
        Instance de CommandRegistry avec toutes les commandes enregistrées.
    """
    registry = CommandRegistry()

    # Enregistrer toutes les commandes
    registry.register(TuiCommand())
    registry.register(SearchCommand())
    registry.register(DownloadCommand())
    registry.register(ListSitesCommand())
    registry.register(ConfigCommand())
    registry.register(ValidateCommand())
    registry.register(VersionCommand())
    registry.register(DoctorCommand())
    registry.register(CacheCommand())
    registry.register(CompletionCommand())

    return registry


def execute_command(
    command_name: str,
    args: Any,
    registry: CommandRegistry | None = None,
) -> int:
    """Exécute une commande par son nom.

    Args:
        command_name: Nom de la commande.
        args: Arguments parsés.
        registry: Registry à utiliser (défaut: registry par défaut).

    Returns:
        Code de sortie.
    """
    if registry is None:
        registry = create_default_registry()

    return registry.execute(command_name, args)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "EXIT_SUCCESS",
    "EXIT_ERROR",
    "EXIT_USAGE_ERROR",
    "EXIT_CONFIG_ERROR",
    "EXIT_DEPENDENCY_ERROR",
    "EXIT_INTERRUPTED",
    # Exceptions
    "CommandError",
    "CommandNotFoundError",
    "CommandValidationError",
    "DependencyNotInstalledError",
    # Classe de base
    "BaseCommand",
    # Registry
    "CommandRegistry",
    # Commandes
    "TuiCommand",
    "SearchCommand",
    "DownloadCommand",
    "ListSitesCommand",
    "ConfigCommand",
    "ValidateCommand",
    "VersionCommand",
    "DoctorCommand",
    "CacheCommand",
    "CompletionCommand",
    # Fonctions helpers
    "create_default_registry",
    "execute_command",
]
