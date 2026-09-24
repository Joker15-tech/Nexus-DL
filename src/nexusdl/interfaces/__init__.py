"""Module d'agrégation pour toutes les interfaces NexusDL.

Ce module constitue le point d'entrée unifié pour les trois interfaces
de NexusDL : CLI (Textual), GUI (PyQt6), et Web (FastAPI). Il fournit
une API de haut niveau pour détecter, lancer, et gérer les interfaces.

**Interfaces supportées** :
    - CLI  : Interface en ligne de commande interactive (Textual)
    - GUI  : Interface graphique desktop (PyQt6)
    - Web  : API REST + interface web (FastAPI + Next.js)

**Architecture** :
    interfaces/
        ├── __init__.py      : Ce fichier (agrégation + API publique)
        ├── cli/             : Interface CLI (Textual)
        │   ├── app.py       : Application Textual principale
        │   ├── screens/     : Écrans (main, search, download, etc.)
        │   ├── widgets/     : Widgets custom
        │   └── commands.py  : Commandes CLI
        │
        ├── gui/             : Interface GUI (PyQt6)
        │   ├── app.py       : Application PyQt6 principale
        │   ├── views/       : Vues (main, search, download, etc.)
        │   ├── components/  : Composants custom
        │   └── theme.py     : Thème cyberpunk néon
        │
        └── web/             : Interface Web (FastAPI + Next.js)
            ├── backend/     : API REST FastAPI
            │   ├── main.py  : Application FastAPI
            │   ├── routers/ : Routeurs (auth, manga, download, etc.)
            │   ├── middleware/ : Middlewares (auth, cors, rate_limit)
            │   ├── schemas/ : Schémas Pydantic
            │   └── static/  : Assets statiques
            │
            └── frontend/    : Interface Next.js
                ├── app/     : Pages Next.js
                ├── components/ : Composants React
                ├── hooks/   : Hooks React
                ├── store/   : Stores Zustand
                └── lib/     : Utilitaires

**Exemple d'utilisation — Lancement automatique** :
    >>> from nexusdl.interfaces import run
    >>> run()  # Détecte et lance la meilleure interface disponible

**Exemple d'utilisation — Lancement spécifique** :
    >>> from nexusdl.interfaces import run_cli, run_gui, run_web
    >>>
    >>> # Lancer l'interface CLI
    >>> run_cli()
    >>>
    >>> # Lancer l'interface GUI
    >>> run_gui()
    >>>
    >>> # Lancer l'interface Web
    >>> run_web(host="0.0.0.0", port=8000)

**Exemple d'utilisation — Détection des interfaces** :
    >>> from nexusdl.interfaces import detect_available_interfaces
    >>> available = detect_available_interfaces()
    >>> print(available)
    {'cli': True, 'gui': False, 'web': True}

Intégration :
    - interfaces/cli/       : Interface CLI (Textual)
    - interfaces/gui/       : Interface GUI (PyQt6)
    - interfaces/web/       : Interface Web (FastAPI + Next.js)
    - core/config.py        : Configuration globale
    - core/logger.py        : Système de logging
    - core/i18n.py          : Internationalisation
    - core/events.py        : EventBus
    - core/registry/        : Registre des sites
    - core/downloader/      : Gestionnaire de téléchargements
    - core/library/         : Gestionnaire de bibliothèque
"""

from __future__ import annotations

import importlib
import sys
from enum import Enum
from typing import Any, Final

from loguru import logger

from nexusdl.core.constants import APP_NAME, APP_VERSION
from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# CONSTANTES
# ============================================================================


# Noms des interfaces
INTERFACE_CLI: Final[str] = "cli"
INTERFACE_GUI: Final[str] = "gui"
INTERFACE_WEB: Final[str] = "web"

# Priorités des interfaces (ordre de préférence)
INTERFACE_PRIORITY: Final[dict[str, int]] = {
    INTERFACE_GUI: 1,  # Priorité la plus haute
    INTERFACE_CLI: 2,
    INTERFACE_WEB: 3,
}

# Packages requis pour chaque interface
INTERFACE_REQUIREMENTS: Final[dict[str, list[str]]] = {
    INTERFACE_CLI: ["textual"],
    INTERFACE_GUI: ["PyQt6"],
    INTERFACE_WEB: ["fastapi", "uvicorn"],
}


# ============================================================================
# EXCEPTIONS
# ============================================================================


class InterfaceError(NexusDLError):
    """Exception de base pour les erreurs d'interface."""


class InterfaceNotAvailableError(InterfaceError):
    """Exception levée lorsqu'une interface n'est pas disponible.

    Attributes:
        interface: Nom de l'interface.
        missing_dependencies: Liste des dépendances manquantes.
    """

    def __init__(self, interface: str, missing_dependencies: list[str]) -> None:
        deps = ", ".join(missing_dependencies)
        msg = f"Interface '{interface}' is not available. Missing dependencies: {deps}"
        super().__init__(msg)
        self.interface = interface
        self.missing_dependencies = missing_dependencies


class InterfaceLaunchError(InterfaceError):
    """Exception levée lorsqu'une interface ne peut être lancée.

    Attributes:
        interface: Nom de l'interface.
        reason: Raison de l'échec.
    """

    def __init__(self, interface: str, reason: str = "") -> None:
        msg = f"Failed to launch interface '{interface}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.interface = interface
        self.reason = reason


class NoInterfaceAvailableError(InterfaceError):
    """Exception levée lorsqu'aucune interface n'est disponible."""

    def __init__(self) -> None:
        super().__init__(
            "No interface is available. Install one of: "
            "textual (CLI), PyQt6 (GUI), or fastapi+uvicorn (Web)"
        )


# ============================================================================
# ENUMS
# ============================================================================


class InterfaceType(str, Enum):
    """Type d'interface.

    Attributes:
        CLI: Interface en ligne de commande (Textual).
        GUI: Interface graphique (PyQt6).
        WEB: Interface web (FastAPI).
    """

    CLI = INTERFACE_CLI
    GUI = INTERFACE_GUI
    WEB = INTERFACE_WEB

    @property
    def display_name(self) -> str:
        """Nom d'affichage de l'interface."""
        return {
            InterfaceType.CLI: "Command Line Interface",
            InterfaceType.GUI: "Graphical User Interface",
            InterfaceType.WEB: "Web Interface",
        }[self]

    @property
    def description(self) -> str:
        """Description de l'interface."""
        return {
            InterfaceType.CLI: "Interactive terminal interface with TUI",
            InterfaceType.GUI: "Desktop application with modern UI",
            InterfaceType.WEB: "Browser-based interface with REST API",
        }[self]

    @property
    def priority(self) -> int:
        """Priorité de l'interface (1 = plus haute)."""
        return INTERFACE_PRIORITY[self.value]


# ============================================================================
# DÉTECTION DES INTERFACES
# ============================================================================


def check_interface_available(interface: str) -> tuple[bool, list[str]]:
    """Vérifie si une interface est disponible.

    Args:
        interface: Nom de l'interface (cli, gui, web).

    Returns:
        Tuple (is_available, missing_dependencies).

    Example:
        >>> available, missing = check_interface_available("cli")
        >>> print(available, missing)
        True []
    """
    requirements = INTERFACE_REQUIREMENTS.get(interface, [])
    missing = []

    for package in requirements:
        try:
            importlib.import_module(package)
        except ImportError:
            missing.append(package)

    return len(missing) == 0, missing


def detect_available_interfaces() -> dict[str, bool]:
    """Détecte les interfaces disponibles.

    Returns:
        Dictionnaire {interface_name: is_available}.

    Example:
        >>> available = detect_available_interfaces()
        >>> print(available)
        {'cli': True, 'gui': False, 'web': True}
    """
    return {
        INTERFACE_CLI: check_interface_available(INTERFACE_CLI)[0],
        INTERFACE_GUI: check_interface_available(INTERFACE_GUI)[0],
        INTERFACE_WEB: check_interface_available(INTERFACE_WEB)[0],
    }


def get_best_available_interface() -> str | None:
    """Retourne la meilleure interface disponible selon la priorité.

    Returns:
        Nom de l'interface ou None si aucune n'est disponible.

    Example:
        >>> interface = get_best_available_interface()
        >>> print(interface)
        'gui'
    """
    available = detect_available_interfaces()

    # Trier par priorité
    sorted_interfaces = sorted(
        [iface for iface, is_avail in available.items() if is_avail],
        key=lambda x: INTERFACE_PRIORITY[x],
    )

    return sorted_interfaces[0] if sorted_interfaces else None


# ============================================================================
# FONCTIONS DE LANCEMENT
# ============================================================================


async def run_cli(**kwargs: Any) -> int:
    """Lance l'interface CLI (Textual).

    Args:
        **kwargs: Arguments passés à l'interface CLI.

    Returns:
        Code de retour.

    Raises:
        InterfaceNotAvailableError: Si l'interface n'est pas disponible.
        InterfaceLaunchError: Si le lancement échoue.

    Example:
        >>> exit_code = await run_cli()
    """
    is_available, missing = check_interface_available(INTERFACE_CLI)
    if not is_available:
        raise InterfaceNotAvailableError(INTERFACE_CLI, missing)

    try:
        from nexusdl.interfaces.cli import run_cli as _run_cli

        logger.info("Launching CLI interface")
        return await _run_cli(**kwargs)

    except ImportError as e:
        logger.error("Failed to import CLI interface: {}", e)
        raise InterfaceLaunchError(INTERFACE_CLI, str(e)) from e

    except Exception as e:
        logger.exception("CLI interface failed: {}", e)
        raise InterfaceLaunchError(INTERFACE_CLI, str(e)) from e


async def run_gui(**kwargs: Any) -> int:
    """Lance l'interface GUI (PyQt6).

    Args:
        **kwargs: Arguments passés à l'interface GUI.

    Returns:
        Code de retour.

    Raises:
        InterfaceNotAvailableError: Si l'interface n'est pas disponible.
        InterfaceLaunchError: Si le lancement échoue.

    Example:
        >>> exit_code = await run_gui()
    """
    is_available, missing = check_interface_available(INTERFACE_GUI)
    if not is_available:
        raise InterfaceNotAvailableError(INTERFACE_GUI, missing)

    try:
        from nexusdl.interfaces.gui import run_gui as _run_gui

        logger.info("Launching GUI interface")
        return await _run_gui(**kwargs)

    except ImportError as e:
        logger.error("Failed to import GUI interface: {}", e)
        raise InterfaceLaunchError(INTERFACE_GUI, str(e)) from e

    except Exception as e:
        logger.exception("GUI interface failed: {}", e)
        raise InterfaceLaunchError(INTERFACE_GUI, str(e)) from e


async def run_web(
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

    Raises:
        InterfaceNotAvailableError: Si l'interface n'est pas disponible.
        InterfaceLaunchError: Si le lancement échoue.

    Example:
        >>> exit_code = await run_web(host="0.0.0.0", port=8000)
    """
    is_available, missing = check_interface_available(INTERFACE_WEB)
    if not is_available:
        raise InterfaceNotAvailableError(INTERFACE_WEB, missing)

    try:
        from nexusdl.interfaces.web.backend.main import run_api

        logger.info("Launching Web interface on {}:{}", host, port)
        run_api(host=host, port=port, reload=reload, workers=workers, **kwargs)
        return 0

    except ImportError as e:
        logger.error("Failed to import Web interface: {}", e)
        raise InterfaceLaunchError(INTERFACE_WEB, str(e)) from e

    except Exception as e:
        logger.exception("Web interface failed: {}", e)
        raise InterfaceLaunchError(INTERFACE_WEB, str(e)) from e


async def run(interface: str | None = None, **kwargs: Any) -> int:
    """Lance l'interface spécifiée ou la meilleure disponible.

    Args:
        interface: Nom de l'interface (cli, gui, web). Auto-détecté si None.
        **kwargs: Arguments passés à l'interface.

    Returns:
        Code de retour.

    Raises:
        NoInterfaceAvailableError: Si aucune interface n'est disponible.
        InterfaceNotAvailableError: Si l'interface spécifiée n'est pas disponible.
        InterfaceLaunchError: Si le lancement échoue.

    Example:
        >>> # Lancer la meilleure interface disponible
        >>> exit_code = await run()
        >>>
        >>> # Lancer une interface spécifique
        >>> exit_code = await run("web", host="0.0.0.0", port=8000)
    """
    # Déterminer l'interface à lancer
    if interface is None:
        interface = get_best_available_interface()
        if interface is None:
            raise NoInterfaceAvailableError()
        logger.info("Auto-detected interface: {}", interface)

    # Lancer l'interface
    if interface == INTERFACE_CLI:
        return await run_cli(**kwargs)
    elif interface == INTERFACE_GUI:
        return await run_gui(**kwargs)
    elif interface == INTERFACE_WEB:
        return await run_web(**kwargs)
    else:
        raise InterfaceError(f"Unknown interface: {interface}")


# ============================================================================
# FONCTIONS HELPERS
# ============================================================================


def get_interface_info(interface: str) -> dict[str, Any]:
    """Retourne les informations d'une interface.

    Args:
        interface: Nom de l'interface.

    Returns:
        Dictionnaire d'informations.

    Example:
        >>> info = get_interface_info("cli")
        >>> print(info["display_name"])
        'Command Line Interface'
    """
    is_available, missing = check_interface_available(interface)

    return {
        "name": interface,
        "display_name": InterfaceType(interface).display_name,
        "description": InterfaceType(interface).description,
        "priority": InterfaceType(interface).priority,
        "available": is_available,
        "missing_dependencies": missing,
        "requirements": INTERFACE_REQUIREMENTS.get(interface, []),
    }


def list_interfaces() -> list[dict[str, Any]]:
    """Liste toutes les interfaces avec leurs informations.

    Returns:
        Liste de dictionnaires d'informations.

    Example:
        >>> interfaces = list_interfaces()
        >>> for iface in interfaces:
        ...     print(f"{iface['name']}: {iface['available']}")
    """
    return [
        get_interface_info(INTERFACE_CLI),
        get_interface_info(INTERFACE_GUI),
        get_interface_info(INTERFACE_WEB),
    ]


def print_interfaces_status() -> None:
    """Affiche le statut de toutes les interfaces."""
    print(f"\n{APP_NAME} v{APP_VERSION} - Interface Status\n")

    interfaces = list_interfaces()
    for iface in interfaces:
        status = "✓" if iface["available"] else "✗"
        print(f"  {status} {iface['display_name']:30} ({iface['name']})")

        if not iface["available"]:
            missing = ", ".join(iface["missing_dependencies"])
            print(f"    Missing: {missing}")

        print(f"    {iface['description']}")
        print()


def get_installation_instructions(interface: str) -> str:
    """Retourne les instructions d'installation pour une interface.

    Args:
        interface: Nom de l'interface.

    Returns:
        Instructions d'installation.

    Example:
        >>> print(get_installation_instructions("cli"))
        pip install textual
    """
    requirements = INTERFACE_REQUIREMENTS.get(interface, [])

    if interface == INTERFACE_CLI:
        return "pip install textual"
    elif interface == INTERFACE_GUI:
        return "pip install PyQt6"
    elif interface == INTERFACE_WEB:
        return "pip install fastapi uvicorn"
    else:
        return f"pip install {' '.join(requirements)}"


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "INTERFACE_CLI",
    "INTERFACE_GUI",
    "INTERFACE_WEB",
    "INTERFACE_PRIORITY",
    "INTERFACE_REQUIREMENTS",
    # Exceptions
    "InterfaceError",
    "InterfaceNotAvailableError",
    "InterfaceLaunchError",
    "NoInterfaceAvailableError",
    # Enums
    "InterfaceType",
    # Fonctions de détection
    "check_interface_available",
    "detect_available_interfaces",
    "get_best_available_interface",
    # Fonctions de lancement
    "run_cli",
    "run_gui",
    "run_web",
    "run",
    # Fonctions helpers
    "get_interface_info",
    "list_interfaces",
    "print_interfaces_status",
    "get_installation_instructions",
]
