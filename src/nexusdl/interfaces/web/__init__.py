"""Point d'entrée unifié pour l'interface web de NexusDL.

Ce module agrège et expose l'API de haut niveau pour l'interface web
de NexusDL, qui comprend deux composants :

    1. **Backend** : API REST basée sur FastAPI
       - 98 endpoints répartis sur 10 routeurs
       - Authentification JWT + API keys
       - WebSocket pour communications temps réel
       - Middlewares de sécurité (CORS, rate limiting, logging)
       - Documentation OpenAPI (Swagger UI / ReDoc)

    2. **Frontend** : Interface utilisateur basée sur Next.js 14
       - Application React avec App Router
       - Thème cyberpunk néon avec 4 variantes
       - State management avec Zustand
       - Cache API avec TanStack Query
       - PWA support (manifest, service worker)

**Architecture** :
    interfaces/web/
        ├── __init__.py      : Ce fichier (agrégation + API publique)
        ├── backend/         : API REST FastAPI
        │   ├── main.py      : Application FastAPI principale
        │   ├── routers/     : Routeurs (health, auth, manga, etc.)
        │   ├── middleware/  : Middlewares (auth, cors, rate_limit, logging)
        │   ├── schemas/     : Schémas Pydantic (requests, responses)
        │   ├── static/      : Assets statiques (style cyberpunk néon)
        │   ├── websocket.py : WebSocketManager de haut niveau
        │   └── dependencies.py : Dépendances FastAPI
        │
        └── frontend/        : Interface Next.js
            ├── app/         : Pages Next.js (App Router)
            ├── components/  : Composants React réutilisables
            ├── hooks/       : Hooks React personnalisés
            ├── store/       : Stores Zustand
            ├── lib/         : Utilitaires (api, ws, utils)
            └── types/       : Types TypeScript

**Exemple d'utilisation — Lancement du backend seul** :
    >>> from nexusdl.interfaces.web import run_backend
    >>> run_backend(host="127.0.0.1", port=8000)

**Exemple d'utilisation — Lancement du frontend en développement** :
    >>> from nexusdl.interfaces.web import run_frontend
    >>> run_frontend(dev=True, port=3000)

**Exemple d'utilisation — Lancement complet (backend + frontend)** :
    >>> from nexusdl.interfaces.web import run_web
    >>> run_web(
    ...     backend_host="127.0.0.1",
    ...     backend_port=8000,
    ...     frontend_port=3000,
    ...     dev=True,
    ... )

**Exemple d'utilisation — Vérification de disponibilité** :
    >>> from nexusdl.interfaces.web import check_web_available, get_web_info
    >>>
    >>> available, info = check_web_available()
    >>> if available:
    ...     print(f"Backend: {info['backend_available']}")
    ...     print(f"Frontend: {info['frontend_available']}")

Intégration :
    - interfaces/web/backend/ : API REST FastAPI
    - interfaces/web/frontend/ : Interface Next.js
    - core/config.py          : Configuration globale
    - core/logger.py          : Système de logging
    - core/events.py          : EventBus
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

from loguru import logger

from nexusdl.core.constants import APP_NAME, APP_VERSION
from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# CONSTANTES
# ============================================================================


# Ports par défaut
DEFAULT_BACKEND_HOST: Final[str] = "127.0.0.1"
DEFAULT_BACKEND_PORT: Final[int] = 8000
DEFAULT_FRONTEND_PORT: Final[int] = 3000

# URLs par défaut
DEFAULT_API_URL: Final[str] = "http://127.0.0.1:8000/api/v1"
DEFAULT_WS_URL: Final[str] = "ws://127.0.0.1:8000/ws"

# Chemins
FRONTEND_DIR: Final[Path] = Path(__file__).parent / "frontend"
FRONTEND_BUILD_DIR: Final[Path] = FRONTEND_DIR / ".next"
FRONTEND_OUT_DIR: Final[Path] = FRONTEND_DIR / "out"

# Timeouts
FRONTEND_STARTUP_TIMEOUT: Final[int] = 60  # secondes
BACKEND_STARTUP_TIMEOUT: Final[int] = 30  # secondes

# Variables d'environnement
ENV_API_URL: Final[str] = "NEXT_PUBLIC_API_URL"
ENV_WS_URL: Final[str] = "NEXT_PUBLIC_WS_URL"


# ============================================================================
# EXCEPTIONS
# ============================================================================


class WebInterfaceError(NexusDLError):
    """Exception de base pour les erreurs de l'interface web."""


class BackendNotAvailableError(WebInterfaceError):
    """Exception levée lorsque le backend n'est pas disponible."""

    def __init__(self, missing_dependencies: list[str] | None = None) -> None:
        if missing_dependencies:
            deps = ", ".join(missing_dependencies)
            msg = (
                f"Backend is not available. Missing dependencies: {deps}. "
                f"Install with: pip install fastapi uvicorn"
            )
        else:
            msg = "Backend is not available"
        super().__init__(msg)
        self.missing_dependencies = missing_dependencies or []


class FrontendNotAvailableError(WebInterfaceError):
    """Exception levée lorsque le frontend n'est pas disponible."""

    def __init__(self, reason: str = "") -> None:
        msg = "Frontend is not available"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


class BackendLaunchError(WebInterfaceError):
    """Exception levée lorsque le backend ne peut être lancé."""

    def __init__(self, reason: str = "") -> None:
        msg = "Failed to launch backend"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


class FrontendLaunchError(WebInterfaceError):
    """Exception levée lorsque le frontend ne peut être lancé."""

    def __init__(self, reason: str = "") -> None:
        msg = "Failed to launch frontend"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


# ============================================================================
# DÉTECTION DE DISPONIBILITÉ
# ============================================================================


def check_backend_available() -> tuple[bool, list[str]]:
    """Vérifie si le backend est disponible.

    Vérifie la présence des dépendances requises :
        - fastapi : Framework web
        - uvicorn : Serveur ASGI

    Returns:
        Tuple (is_available, missing_dependencies).

    Example:
        >>> available, missing = check_backend_available()
        >>> print(available, missing)
        True []
    """
    missing: list[str] = []

    try:
        import fastapi  # noqa: F401
    except ImportError:
        missing.append("fastapi")

    try:
        import uvicorn  # noqa: F401
    except ImportError:
        missing.append("uvicorn")

    return len(missing) == 0, missing


def check_frontend_available() -> tuple[bool, str]:
    """Vérifie si le frontend est disponible.

    Vérifie :
        - La présence du dossier frontend
        - La présence de Node.js (pour le mode dev)
        - La présence du build (pour le mode prod)

    Returns:
        Tuple (is_available, reason_if_not).

    Example:
        >>> available, reason = check_frontend_available()
        >>> print(available, reason)
        True ""
    """
    # Vérifier que le dossier frontend existe
    if not FRONTEND_DIR.exists():
        return False, f"Frontend directory not found: {FRONTEND_DIR}"

    # Vérifier que Node.js est disponible
    node_available = shutil.which("node") is not None
    if not node_available:
        return False, "Node.js is not installed or not in PATH"

    # Vérifier que pnpm ou npm est disponible
    pnpm_available = shutil.which("pnpm") is not None
    npm_available = shutil.which("npm") is not None
    if not (pnpm_available or npm_available):
        return False, "Neither pnpm nor npm is available"

    return True, ""


def check_web_available() -> tuple[bool, dict[str, Any]]:
    """Vérifie la disponibilité complète de l'interface web.

    Returns:
        Tuple (is_available, info_dict).

    Example:
        >>> available, info = check_web_available()
        >>> print(info)
        {
            'backend_available': True,
            'frontend_available': True,
            'backend_missing': [],
            'frontend_reason': '',
        }
    """
    backend_ok, backend_missing = check_backend_available()
    frontend_ok, frontend_reason = check_frontend_available()

    info = {
        "backend_available": backend_ok,
        "frontend_available": frontend_ok,
        "backend_missing": backend_missing,
        "frontend_reason": frontend_reason,
    }

    return backend_ok or frontend_ok, info


def is_frontend_built() -> bool:
    """Vérifie si le frontend a été buildé.

    Returns:
        True si le dossier .next ou out existe.

    Example:
        >>> if is_frontend_built():
        ...     print("Frontend is ready for production")
    """
    return FRONTEND_BUILD_DIR.exists() or FRONTEND_OUT_DIR.exists()


# ============================================================================
# FONCTIONS D'INFORMATION
# ============================================================================


def get_web_info() -> dict[str, Any]:
    """Retourne les informations complètes sur l'interface web.

    Returns:
        Dictionnaire d'informations.

    Example:
        >>> info = get_web_info()
        >>> print(info['version'])
        '0.1.0'
    """
    backend_ok, backend_missing = check_backend_available()
    frontend_ok, frontend_reason = check_frontend_available()

    return {
        "name": f"{APP_NAME} Web Interface",
        "version": APP_VERSION,
        "backend": {
            "available": backend_ok,
            "missing_dependencies": backend_missing,
            "default_host": DEFAULT_BACKEND_HOST,
            "default_port": DEFAULT_BACKEND_PORT,
            "api_url": DEFAULT_API_URL,
            "ws_url": DEFAULT_WS_URL,
        },
        "frontend": {
            "available": frontend_ok,
            "reason": frontend_reason,
            "directory": str(FRONTEND_DIR),
            "is_built": is_frontend_built(),
            "default_port": DEFAULT_FRONTEND_PORT,
        },
    }


def get_web_urls(
    backend_host: str = DEFAULT_BACKEND_HOST,
    backend_port: int = DEFAULT_BACKEND_PORT,
    frontend_port: int = DEFAULT_FRONTEND_PORT,
) -> dict[str, str]:
    """Retourne les URLs de l'interface web.

    Args:
        backend_host: Hôte du backend.
        backend_port: Port du backend.
        frontend_port: Port du frontend.

    Returns:
        Dictionnaire d'URLs.

    Example:
        >>> urls = get_web_urls()
        >>> print(urls['api'])
        'http://127.0.0.1:8000/api/v1'
    """
    backend_base = f"http://{backend_host}:{backend_port}"
    frontend_base = f"http://{backend_host}:{frontend_port}"

    return {
        "backend": backend_base,
        "frontend": frontend_base,
        "api": f"{backend_base}/api/v1",
        "ws": f"ws://{backend_host}:{backend_port}/ws",
        "docs": f"{backend_base}/docs",
        "redoc": f"{backend_base}/redoc",
        "openapi": f"{backend_base}/openapi.json",
        "health": f"{backend_base}/api/v1/health",
    }


def print_web_status() -> None:
    """Affiche le statut de l'interface web."""
    info = get_web_info()
    urls = get_web_urls()

    print(f"\n{info['name']} v{info['version']}\n")

    # Backend
    print("Backend (FastAPI):")
    if info["backend"]["available"]:
        print(f"  ✓ Available")
        print(f"    Default URL: {info['backend']['api_url']}")
        print(f"    WebSocket:   {info['backend']['ws_url']}")
        print(f"    Docs:        {urls['docs']}")
    else:
        print(f"  ✗ Not available")
        missing = ", ".join(info["backend"]["missing_dependencies"])
        print(f"    Missing: {missing}")
        print(f"    Install: pip install fastapi uvicorn")
    print()

    # Frontend
    print("Frontend (Next.js):")
    if info["frontend"]["available"]:
        print(f"  ✓ Available")
        print(f"    Directory: {info['frontend']['directory']}")
        print(f"    Built: {'Yes' if info['frontend']['is_built'] else 'No'}")
        print(f"    Default URL: http://localhost:{info['frontend']['default_port']}")
    else:
        print(f"  ✗ Not available")
        print(f"    Reason: {info['frontend']['reason']}")
    print()


# ============================================================================
# FONCTIONS DE LANCEMENT — BACKEND
# ============================================================================


def run_backend(
    host: str = DEFAULT_BACKEND_HOST,
    port: int = DEFAULT_BACKEND_PORT,
    reload: bool = False,
    workers: int = 1,
    log_level: str = "info",
    **kwargs: Any,
) -> None:
    """Lance le backend FastAPI.

    Args:
        host: Hôte d'écoute.
        port: Port d'écoute.
        reload: Activer le rechargement automatique (dev).
        workers: Nombre de workers (prod).
        log_level: Niveau de log uvicorn.
        **kwargs: Arguments additionnels passés à uvicorn.

    Raises:
        BackendNotAvailableError: Si les dépendances sont manquantes.
        BackendLaunchError: Si le lancement échoue.

    Example:
        >>> run_backend(host="0.0.0.0", port=8000)
    """
    # Vérifier la disponibilité
    is_available, missing = check_backend_available()
    if not is_available:
        raise BackendNotAvailableError(missing)

    try:
        import uvicorn
    except ImportError as e:
        raise BackendNotAvailableError(["uvicorn"]) from e

    logger.info(
        "Starting backend on {}:{} (reload={}, workers={})",
        host,
        port,
        reload,
        workers,
    )

    try:
        # Configurer les variables d'environnement pour le frontend
        os.environ[ENV_API_URL] = f"http://{host}:{port}/api/v1"
        os.environ[ENV_WS_URL] = f"ws://{host}:{port}/ws"

        # Lancer uvicorn
        uvicorn.run(
            "nexusdl.interfaces.web.backend.main:app",
            host=host,
            port=port,
            reload=reload,
            workers=workers if not reload else 1,
            log_level=log_level,
            **kwargs,
        )

    except KeyboardInterrupt:
        logger.info("Backend stopped by user")
    except Exception as e:
        logger.exception("Backend failed: {}", e)
        raise BackendLaunchError(str(e)) from e


async def run_backend_async(
    host: str = DEFAULT_BACKEND_HOST,
    port: int = DEFAULT_BACKEND_PORT,
    reload: bool = False,
    workers: int = 1,
    log_level: str = "info",
    **kwargs: Any,
) -> None:
    """Lance le backend FastAPI de manière asynchrone.

    Cette fonction est utile pour intégrer le backend dans une boucle
    asyncio existante.

    Args:
        host: Hôte d'écoute.
        port: Port d'écoute.
        reload: Activer le rechargement automatique.
        workers: Nombre de workers.
        log_level: Niveau de log.
        **kwargs: Arguments additionnels.

    Raises:
        BackendNotAvailableError: Si les dépendances sont manquantes.
        BackendLaunchError: Si le lancement échoue.
    """
    # Vérifier la disponibilité
    is_available, missing = check_backend_available()
    if not is_available:
        raise BackendNotAvailableError(missing)

    try:
        import uvicorn

        from nexusdl.interfaces.web.backend.main import create_app

        logger.info(
            "Starting backend (async) on {}:{} (reload={}, workers={})",
            host,
            port,
            reload,
            workers,
        )

        # Créer l'application
        app = create_app(log_level=log_level.upper(), debug=reload)

        # Configurer uvicorn
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            reload=reload,
            workers=workers if not reload else 1,
            log_level=log_level,
            **kwargs,
        )

        server = uvicorn.Server(config)

        # Lancer le serveur
        await server.serve()

    except KeyboardInterrupt:
        logger.info("Backend stopped by user")
    except Exception as e:
        logger.exception("Backend failed: {}", e)
        raise BackendLaunchError(str(e)) from e


# ============================================================================
# FONCTIONS DE LANCEMENT — FRONTEND
# ============================================================================


def run_frontend(
    dev: bool = True,
    port: int = DEFAULT_FRONTEND_PORT,
    hostname: str = "localhost",
    build: bool = False,
    **kwargs: Any,
) -> int:
    """Lance le frontend Next.js.

    Args:
        dev: Mode développement (True) ou production (False).
        port: Port d'écoute.
        hostname: Nom d'hôte.
        build: Construire avant de lancer (mode prod).
        **kwargs: Arguments additionnels.

    Returns:
        Code de retour du processus.

    Raises:
        FrontendNotAvailableError: Si le frontend n'est pas disponible.
        FrontendLaunchError: Si le lancement échoue.

    Example:
        >>> # Mode développement
        >>> run_frontend(dev=True, port=3000)
        >>>
        >>> # Mode production
        >>> run_frontend(dev=False, build=True)
    """
    # Vérifier la disponibilité
    is_available, reason = check_frontend_available()
    if not is_available:
        raise FrontendNotAvailableError(reason)

    # Vérifier le build en mode production
    if not dev and not is_frontend_built():
        if build:
            logger.info("Building frontend...")
            build_result = _build_frontend()
            if build_result != 0:
                raise FrontendLaunchError("Build failed")
        else:
            raise FrontendNotAvailableError(
                "Frontend is not built. Run with build=True or run 'pnpm build' manually"
            )

    # Déterminer le gestionnaire de paquets
    package_manager = "pnpm" if shutil.which("pnpm") else "npm"

    # Déterminer la commande
    if dev:
        command = [package_manager, "run", "dev", "--", "-p", str(port), "-H", hostname]
    else:
        command = [package_manager, "run", "start", "--", "-p", str(port), "-H", hostname]

    logger.info(
        "Starting frontend ({} mode) on {}:{} with {}",
        "dev" if dev else "prod",
        hostname,
        port,
        package_manager,
    )

    try:
        # Lancer le processus
        process = subprocess.Popen(
            command,
            cwd=str(FRONTEND_DIR),
            env={**os.environ, **kwargs.get("env", {})},
        )

        # Attendre la fin du processus
        return process.wait()

    except KeyboardInterrupt:
        logger.info("Frontend stopped by user")
        process.terminate()
        return 0
    except Exception as e:
        logger.exception("Frontend failed: {}", e)
        raise FrontendLaunchError(str(e)) from e


def _build_frontend() -> int:
    """Construit le frontend Next.js.

    Returns:
        Code de retour du processus.
    """
    package_manager = "pnpm" if shutil.which("pnpm") else "npm"
    command = [package_manager, "run", "build"]

    logger.info("Building frontend with {}...", package_manager)

    try:
        result = subprocess.run(
            command,
            cwd=str(FRONTEND_DIR),
            check=False,
        )
        return result.returncode
    except Exception as e:
        logger.error("Build failed: {}", e)
        return 1


def _install_frontend_dependencies() -> int:
    """Installe les dépendances du frontend.

    Returns:
        Code de retour du processus.
    """
    package_manager = "pnpm" if shutil.which("pnpm") else "npm"
    command = [package_manager, "install"]

    logger.info("Installing frontend dependencies with {}...", package_manager)

    try:
        result = subprocess.run(
            command,
            cwd=str(FRONTEND_DIR),
            check=False,
        )
        return result.returncode
    except Exception as e:
        logger.error("Installation failed: {}", e)
        return 1


# ============================================================================
# FONCTIONS DE LANCEMENT — COMBINÉ
# ============================================================================


def run_web(
    backend_host: str = DEFAULT_BACKEND_HOST,
    backend_port: int = DEFAULT_BACKEND_PORT,
    frontend_port: int = DEFAULT_FRONTEND_PORT,
    dev: bool = True,
    reload: bool = False,
    workers: int = 1,
    log_level: str = "info",
    frontend_only: bool = False,
    backend_only: bool = False,
    **kwargs: Any,
) -> int:
    """Lance l'interface web complète (backend + frontend).

    Args:
        backend_host: Hôte du backend.
        backend_port: Port du backend.
        frontend_port: Port du frontend.
        dev: Mode développement.
        reload: Rechargement automatique du backend.
        workers: Nombre de workers du backend.
        log_level: Niveau de log.
        frontend_only: Lancer uniquement le frontend.
        backend_only: Lancer uniquement le backend.
        **kwargs: Arguments additionnels.

    Returns:
        Code de retour.

    Raises:
        WebInterfaceError: Si le lancement échoue.

    Example:
        >>> # Lancer les deux en mode dev
        >>> run_web(dev=True)
        >>>
        >>> # Lancer uniquement le backend
        >>> run_web(backend_only=True, host="0.0.0.0")
        >>>
        >>> # Lancer uniquement le frontend
        >>> run_web(frontend_only=True, dev=False, build=True)
    """
    # Mode backend uniquement
    if backend_only:
        run_backend(
            host=backend_host,
            port=backend_port,
            reload=reload or dev,
            workers=workers,
            log_level=log_level,
            **kwargs,
        )
        return 0

    # Mode frontend uniquement
    if frontend_only:
        return run_frontend(
            dev=dev,
            port=frontend_port,
            **kwargs,
        )

    # Mode complet : backend + frontend
    logger.info("Starting complete web interface")
    logger.info("  Backend:  http://{}:{}", backend_host, backend_port)
    logger.info("  Frontend: http://{}:{}", backend_host, frontend_port)

    # Configurer les URLs pour le frontend
    os.environ[ENV_API_URL] = f"http://{backend_host}:{backend_port}/api/v1"
    os.environ[ENV_WS_URL] = f"ws://{backend_host}:{backend_port}/ws"

    try:
        # Lancer le backend dans un thread séparé
        import threading

        backend_thread = threading.Thread(
            target=run_backend,
            kwargs={
                "host": backend_host,
                "port": backend_port,
                "reload": reload or dev,
                "workers": workers,
                "log_level": log_level,
            },
            daemon=True,
        )
        backend_thread.start()

        # Attendre un peu que le backend démarre
        import time
        time.sleep(2)

        # Lancer le frontend (bloquant)
        return run_frontend(
            dev=dev,
            port=frontend_port,
            **kwargs,
        )

    except KeyboardInterrupt:
        logger.info("Web interface stopped by user")
        return 0
    except Exception as e:
        logger.exception("Web interface failed: {}", e)
        raise WebInterfaceError(str(e)) from e


async def run_web_async(
    backend_host: str = DEFAULT_BACKEND_HOST,
    backend_port: int = DEFAULT_BACKEND_PORT,
    frontend_port: int = DEFAULT_FRONTEND_PORT,
    dev: bool = True,
    reload: bool = False,
    workers: int = 1,
    log_level: str = "info",
    **kwargs: Any,
) -> int:
    """Lance l'interface web de manière asynchrone.

    Cette fonction lance le backend en mode async et le frontend
    dans un processus séparé.

    Args:
        backend_host: Hôte du backend.
        backend_port: Port du backend.
        frontend_port: Port du frontend.
        dev: Mode développement.
        reload: Rechargement automatique.
        workers: Nombre de workers.
        log_level: Niveau de log.
        **kwargs: Arguments additionnels.

    Returns:
        Code de retour.
    """
    logger.info("Starting web interface (async mode)")

    # Configurer les URLs pour le frontend
    os.environ[ENV_API_URL] = f"http://{backend_host}:{backend_port}/api/v1"
    os.environ[ENV_WS_URL] = f"ws://{backend_host}:{backend_port}/ws"

    try:
        # Lancer le backend et le frontend en parallèle
        backend_task = asyncio.create_task(
            run_backend_async(
                host=backend_host,
                port=backend_port,
                reload=reload or dev,
                workers=workers,
                log_level=log_level,
            )
        )

        # Lancer le frontend dans un thread
        import threading

        frontend_thread = threading.Thread(
            target=run_frontend,
            kwargs={
                "dev": dev,
                "port": frontend_port,
            },
            daemon=True,
        )
        frontend_thread.start()

        # Attendre la fin du backend
        await backend_task

        return 0

    except KeyboardInterrupt:
        logger.info("Web interface stopped by user")
        return 0
    except Exception as e:
        logger.exception("Web interface failed: {}", e)
        raise WebInterfaceError(str(e)) from e


# ============================================================================
# HELPERS D'INTÉGRATION
# ============================================================================


def setup_frontend_env(
    api_url: str = DEFAULT_API_URL,
    ws_url: str = DEFAULT_WS_URL,
) -> None:
    """Configure les variables d'environnement pour le frontend.

    Args:
        api_url: URL de l'API REST.
        ws_url: URL du WebSocket.

    Example:
        >>> setup_frontend_env(
        ...     api_url="https://api.nexusdl.dev/v1",
        ...     ws_url="wss://api.nexusdl.dev/ws",
        ... )
    """
    os.environ[ENV_API_URL] = api_url
    os.environ[ENV_WS_URL] = ws_url
    logger.debug("Frontend environment configured: api={}, ws={}", api_url, ws_url)


def get_frontend_env() -> dict[str, str]:
    """Retourne les variables d'environnement du frontend.

    Returns:
        Dictionnaire des variables d'environnement.
    """
    return {
        ENV_API_URL: os.environ.get(ENV_API_URL, DEFAULT_API_URL),
        ENV_WS_URL: os.environ.get(ENV_WS_URL, DEFAULT_WS_URL),
    }


def ensure_frontend_dependencies() -> bool:
    """S'assure que les dépendances du frontend sont installées.

    Si node_modules n'existe pas, lance l'installation.

    Returns:
        True si les dépendances sont prêtes.

    Example:
        >>> if ensure_frontend_dependencies():
        ...     run_frontend(dev=True)
    """
    node_modules = FRONTEND_DIR / "node_modules"

    if node_modules.exists():
        logger.debug("Frontend dependencies already installed")
        return True

    logger.info("Frontend dependencies not found, installing...")
    result = _install_frontend_dependencies()
    return result == 0


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_BACKEND_HOST",
    "DEFAULT_BACKEND_PORT",
    "DEFAULT_FRONTEND_PORT",
    "DEFAULT_API_URL",
    "DEFAULT_WS_URL",
    "FRONTEND_DIR",
    "FRONTEND_BUILD_DIR",
    "FRONTEND_OUT_DIR",
    "ENV_API_URL",
    "ENV_WS_URL",
    # Exceptions
    "WebInterfaceError",
    "BackendNotAvailableError",
    "FrontendNotAvailableError",
    "BackendLaunchError",
    "FrontendLaunchError",
    # Détection
    "check_backend_available",
    "check_frontend_available",
    "check_web_available",
    "is_frontend_built",
    # Informations
    "get_web_info",
    "get_web_urls",
    "print_web_status",
    # Lancement backend
    "run_backend",
    "run_backend_async",
    # Lancement frontend
    "run_frontend",
    # Lancement combiné
    "run_web",
    "run_web_async",
    # Helpers d'intégration
    "setup_frontend_env",
    "get_frontend_env",
    "ensure_frontend_dependencies",
]
