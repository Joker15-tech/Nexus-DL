#!/usr/bin/env python3
"""Workflow développeur pour NexusDL.

Lance les composants en mode dev (hot-reload, logs verbeux), fournit un
REPL Python préchargé, et diagnostique l'environnement de développement.

Example:
    Lancer le CLI Textual en dev::

        python scripts/dev.py cli

    Lancer le backend FastAPI (hot-reload) + le frontend Next.js::

        python scripts/dev.py web

    Lancer tous les composants (CLI en foreground, web en arrière-plan)::

        python scripts/dev.py all

    Ouvrir un IPython avec NexusDL préchargé::

        python scripts/dev.py shell

    Vérifier l'environnement (Python, deps, ports, services)::

        python scripts/dev.py doctor

    Arrêter tous les processus d'arrière-plan::

        python scripts/dev.py stop
"""

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Annotated, Final, Iterator, Literal

# --- Résolution des chemins racine -----------------------------------------
_SCRIPT_DIR: Final[Path] = Path(__file__).resolve().parent
_ROOT_DIR: Final[Path] = _SCRIPT_DIR.parent
_SRC_DIR: Final[Path] = _ROOT_DIR / "src"
_FRONTEND_DIR: Final[Path] = _SRC_DIR / "nexusdl" / "interfaces" / "web" / "frontend"
_DEV_STATE_DIR: Final[Path] = _ROOT_DIR / "build" / "dev"
_ENV_FILE: Final[Path] = _ROOT_DIR / ".env"
_CONFIG_FILE: Final[Path] = _ROOT_DIR / "config" / "config.yaml"
_CONFIG_EXAMPLE: Final[Path] = _ROOT_DIR / "config" / "config.example.yaml"

if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import typer  # noqa: E402
from loguru import logger  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402

# ============================================================================
#  Constantes
# ============================================================================

console: Final[Console] = Console()
app: Final[typer.Typer] = typer.Typer(
    name="dev",
    help="Workflow développeur NexusDL.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

DEFAULT_API_HOST: Final[str] = "127.0.0.1"
DEFAULT_API_PORT: Final[int] = 8000
DEFAULT_FRONTEND_PORT: Final[int] = 3000
DEFAULT_FLARESOLVERR_PORT: Final[int] = 8191

GRACEFUL_SHUTDOWN_TIMEOUT: Final[float] = 10.0
PID_FILE_SUFFIX: Final[str] = ".pid"
LOG_FILE_SUFFIX: Final[str] = ".log"


class Component(str, Enum):
    """Composants lançables en dev."""

    CLI = "cli"
    WEB_BACKEND = "web-backend"
    WEB_FRONTEND = "web-frontend"
    GUI = "gui"
    FLARESOLVERR = "flaresolverr"


class RunMode(str, Enum):
    """Mode d'exécution des processus."""

    FOREGROUND = "foreground"
    BACKGROUND = "background"


@dataclass(slots=True)
class ProcessHandle:
    """Handle d'un processus lancé en dev.

    Attributes:
        component: Composant associé.
        popen: Objet subprocess.Popen.
        pid: PID du processus racine.
        log_path: Chemin du fichier de log (background uniquement).
        pid_path: Chemin du fichier PID (background uniquement).
    """

    component: Component
    popen: subprocess.Popen[bytes]
    pid: int
    log_path: Path | None = None
    pid_path: Path | None = None


@dataclass(slots=True)
class DoctorReport:
    """Rapport de diagnostic de l'environnement.

    Attributes:
        checks: Liste de (nom, statut, message) où statut ∈ {"ok", "warn", "fail"}.
    """

    checks: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def failed(self) -> int:
        """Nombre de checks en échec."""
        return sum(1 for _, status, _ in self.checks if status == "fail")

    @property
    def warnings(self) -> int:
        """Nombre de checks en warning."""
        return sum(1 for _, status, _ in self.checks if status == "warn")


# ============================================================================
#  Utilitaires
# ============================================================================


def _ensure_dev_state_dir() -> None:
    """Crée le dossier d'état dev s'il n'existe pas."""
    _DEV_STATE_DIR.mkdir(parents=True, exist_ok=True)


def _pid_path(component: Component) -> Path:
    """Retourne le chemin du fichier PID d'un composant.

    Args:
        component: Composant.

    Returns:
        Chemin absolu du fichier PID.
    """
    return _DEV_STATE_DIR / f"{component.value}{PID_FILE_SUFFIX}"


def _log_path(component: Component) -> Path:
    """Retourne le chemin du fichier de log d'un composant.

    Args:
        component: Composant.

    Returns:
        Chemin absolu du fichier de log.
    """
    return _DEV_STATE_DIR / f"{component.value}{LOG_FILE_SUFFIX}"


def _is_port_free(host: str, port: int) -> bool:
    """Vérifie qu'un port TCP est libre.

    Args:
        host: Adresse d'écoute.
        port: Port à tester.

    Returns:
        True si le port est libre.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _is_process_alive(pid: int) -> bool:
    """Vérifie qu'un processus est vivant.

    Args:
        pid: PID à tester.

    Returns:
        True si le processus existe.
    """
    if platform.system() == "Windows":
        result = subprocess.run(  # noqa: S603
            ["tasklist", "/FI", f"PID eq {pid}"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _read_pid(component: Component) -> int | None:
    """Lit le PID d'un composant depuis son fichier PID.

    Args:
        component: Composant.

    Returns:
        PID si présent et valide, sinon None.
    """
    path = _pid_path(component)
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip())
    except (ValueError, OSError):
        return None
    if not _is_process_alive(pid):
        path.unlink(missing_ok=True)
        return None
    return pid


def _which(name: str) -> str | None:
    """Cherche un binaire dans le PATH.

    Args:
        name: Nom du binaire.

    Returns:
        Chemin absolu, ou None.
    """
    return shutil.which(name)


def _has_module(module: str) -> bool:
    """Vérifie qu'un module Python est importable.

    Args:
        module: Nom du module.

    Returns:
        True si le module est importable.
    """
    return importlib.util.find_spec(module) is not None


def _build_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Construit l'environnement de dev.

    Active les flags de debug, force la config locale, désactive la télémétrie.

    Args:
        extra: Variables additionnelles.

    Returns:
        Dict d'environnement à passer à Popen.
    """
    env = {
        **os.environ,
        "NEXUSDL_ENV": "development",
        "NEXUSDL_DEBUG": "1",
        "NEXUSDL_DEV_MODE": "true",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": f"{_SRC_DIR}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        # Force Rich/Textual à coloriser même sans TTY détecté
        "FORCE_COLOR": "1",
        "RICH_FORCE_TERMINAL": "1",
        "TEXTUAL_ANIMATIONS": "full",
        # Playwright : télécharge les navigateurs dans le cache utilisateur
        "PLAYWRIGHT_BROWSERS_PATH": str(Path.home() / ".cache" / "ms-playwright"),
    }
    if extra:
        env.update(extra)
    return env


# ============================================================================
#  Setup pré-vol
# ============================================================================


def ensure_env_file(*, create: bool = False) -> None:
    """Vérifie l'existence de `.env` et le crée depuis `.env.example` si besoin.

    Args:
        create: Si True, copie `.env.example` vers `.env` automatiquement.
    """
    if _ENV_FILE.exists():
        return
    example = _ROOT_DIR / ".env.example"
    if not example.exists():
        logger.warning("Pas de .env — pas de .env.example non plus. Utilise les défauts.")
        return
    if create:
        shutil.copy(example, _ENV_FILE)
        console.print(f"[green]✓ .env créé depuis .env.example[/green]")
    else:
        console.print(
            f"[yellow]⚠ Pas de .env — utilise `python scripts/dev.py init` pour le créer.[/yellow]",
        )


def ensure_config_file(*, create: bool = False) -> None:
    """Vérifie l'existence de `config/config.yaml`.

    Args:
        create: Si True, copie `config.example.yaml` automatiquement.
    """
    if _CONFIG_FILE.exists():
        return
    if not _CONFIG_EXAMPLE.exists():
        logger.warning("Pas de config.yaml — pas de config.example.yaml non plus.")
        return
    if create:
        shutil.copy(_CONFIG_EXAMPLE, _CONFIG_FILE)
        console.print(f"[green]✓ config/config.yaml créé depuis config.example.yaml[/green]")
    else:
        console.print(
            "[yellow]⚠ Pas de config/config.yaml — l'app utilisera les défauts.[/yellow]",
        )


# ============================================================================
#  Gestion des processus
# ============================================================================


def _spawn(
    cmd: list[str],
    *,
    component: Component,
    cwd: Path,
    env: dict[str, str],
    mode: RunMode,
) -> ProcessHandle:
    """Lance un processus en mode foreground ou background.

    Args:
        cmd: Commande et arguments.
        component: Composant associé (pour les PID/logs).
        cwd: Répertoire de travail.
        env: Environnement.
        mode: Mode d'exécution.

    Returns:
        Handle du processus.

    Raises:
        RuntimeError: Si le binaire est introuvable.
    """
    if _which(cmd[0]) is None and not Path(cmd[0]).exists():
        msg = f"Binaire introuvable : {cmd[0]}"
        raise RuntimeError(msg)

    if mode == RunMode.BACKGROUND:
        _ensure_dev_state_dir()
        log_path = _log_path(component)
        pid_path = _pid_path(component)
        log_fh = log_path.open("ab")
        popen = subprocess.Popen(  # noqa: S603
            cmd,
            cwd=cwd,
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        pid_path.write_text(str(popen.pid))
        log_fh.close()
        return ProcessHandle(
            component=component,
            popen=popen,
            pid=popen.pid,
            log_path=log_path,
            pid_path=pid_path,
        )

    popen = subprocess.Popen(cmd, cwd=cwd, env=env)  # noqa: S603
    return ProcessHandle(component=component, popen=popen, pid=popen.pid)


def _terminate(handle: ProcessHandle, *, timeout: float = GRACEFUL_SHUTDOWN_TIMEOUT) -> None:
    """Termine proprement un processus.

    Envoie SIGTERM, attend `timeout`, puis SIGKILL si nécessaire.

    Args:
        handle: Handle du processus.
        timeout: Délai avant SIGKILL.
    """
    if handle.popen.poll() is not None:
        if handle.pid_path:
            handle.pid_path.unlink(missing_ok=True)
        return

    logger.debug("Terminating {} (pid={})", handle.component.value, handle.pid)
    try:
        if platform.system() == "Windows":
            handle.popen.terminate()
        else:
            handle.popen.send_signal(signal.SIGTERM)
    except ProcessLookupError:
        return

    try:
        handle.popen.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.warning("{} n'a pas répondu à SIGTERM — SIGKILL", handle.component.value)
        handle.popen.kill()
        try:
            handle.popen.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            logger.error("{} résiste même à SIGKILL", handle.component.value)

    if handle.pid_path:
        handle.pid_path.unlink(missing_ok=True)


@contextmanager
def _managed_processes(handles: list[ProcessHandle]) -> Iterator[list[ProcessHandle]]:
    """Context manager qui garantit l'arrêt des processus à la sortie.

    Args:
        handles: Liste initiale de handles.

    Yields:
        La liste des handles.
    """
    try:
        yield handles
    finally:
        console.print()
        console.print("[yellow]Arrêt des processus…[/yellow]")
        for handle in handles:
            _terminate(handle)
        console.print("[green]✓ Tous les processus arrêtés.[/green]")


def _stop_all() -> int:
    """Arrête tous les processus d'arrière-plan enregistrés.

    Returns:
        Nombre de processus arrêtés.
    """
    _ensure_dev_state_dir()
    stopped = 0
    for component in Component:
        pid = _read_pid(component)
        if pid is None:
            continue
        console.print(f"[yellow]Arrêt de {component.value} (pid={pid})…[/yellow]")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            _pid_path(component).unlink(missing_ok=True)
            continue
        # Attend la mort effective
        t0 = time.time()
        while _is_process_alive(pid) and time.time() - t0 < GRACEFUL_SHUTDOWN_TIMEOUT:
            time.sleep(0.1)
        if _is_process_alive(pid):
            logger.warning("{} (pid={}) ne répond pas — SIGKILL", component.value, pid)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        _pid_path(component).unlink(missing_ok=True)
        stopped += 1
    return stopped


# ============================================================================
#  Commandes — Lancement des composants
# ============================================================================


def _run_cli(*, mode: RunMode, extra_args: list[str]) -> ProcessHandle:
    """Lance le CLI Textual.

    Args:
        mode: Mode d'exécution.
        extra_args: Arguments additionnels passés au CLI.

    Returns:
        Handle du processus.
    """
    cmd = [sys.executable, "-m", "nexusdl", "cli", *extra_args]
    return _spawn(cmd, component=Component.CLI, cwd=_ROOT_DIR, env=_build_env(), mode=mode)


def _run_web_backend(
    *,
    mode: RunMode,
    host: str,
    port: int,
    reload: bool,
    extra_args: list[str],
) -> ProcessHandle:
    """Lance le backend FastAPI via uvicorn avec hot-reload.

    Args:
        mode: Mode d'exécution.
        host: Adresse d'écoute.
        port: Port d'écoute.
        reload: Activer le hot-reload.
        extra_args: Arguments additionnels passés à uvicorn.

    Returns:
        Handle du processus.
    """
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "nexusdl.interfaces.web.backend.main:app",
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        "debug",
    ]
    if reload:
        # Watch uniquement le code applicatif, pas les artefacts
        cmd.extend(["--reload", "--reload-dir", str(_SRC_DIR / "nexusdl")])
    cmd.extend(extra_args)
    env = _build_env({"NEXUSDL_WEB__HOST": host, "NEXUSDL_WEB__PORT": str(port)})
    return _spawn(cmd, component=Component.WEB_BACKEND, cwd=_ROOT_DIR, env=env, mode=mode)


def _run_web_frontend(
    *,
    mode: RunMode,
    port: int,
    extra_args: list[str],
) -> ProcessHandle:
    """Lance le frontend Next.js en mode dev.

    Args:
        mode: Mode d'exécution.
        port: Port d'écoute.
        extra_args: Arguments additionnels.

    Returns:
        Handle du processus.

    Raises:
        RuntimeError: Si le package manager ou le dossier frontend est absent.
    """
    if not _FRONTEND_DIR.exists():
        msg = f"Dossier frontend introuvable : {_FRONTEND_DIR}"
        raise RuntimeError(msg)

    pm = _detect_pm(_FRONTEND_DIR)
    if pm is None:
        msg = "Aucun package manager Node.js (pnpm, npm, yarn) dans le PATH."
        raise RuntimeError(msg)

    if not (_FRONTEND_DIR / "node_modules").exists():
        console.print("[yellow]node_modules absent — installation automatique…[/yellow]")
        subprocess.run([pm, "install"], cwd=_FRONTEND_DIR, check=True)  # noqa: S603

    cmd = [pm, "run", "dev", "--", "-p", str(port), *extra_args]
    env = _build_env(
        {
            "NEXT_TELEMETRY_DISABLED": "1",
            "NEXT_PUBLIC_API_URL": f"http://{DEFAULT_API_HOST}:{DEFAULT_API_PORT}",
        },
    )
    return _spawn(cmd, component=Component.WEB_FRONTEND, cwd=_FRONTEND_DIR, env=env, mode=mode)


def _run_gui(*, mode: RunMode, extra_args: list[str]) -> ProcessHandle:
    """Lance le GUI CustomTkinter.

    Args:
        mode: Mode d'exécution.
        extra_args: Arguments additionnels.

    Returns:
        Handle du processus.
    """
    cmd = [sys.executable, "-m", "nexusdl", "gui", *extra_args]
    return _spawn(cmd, component=Component.GUI, cwd=_ROOT_DIR, env=_build_env(), mode=mode)


def _detect_pm(frontend_dir: Path) -> str | None:
    """Détecte le package manager à utiliser.

    Args:
        frontend_dir: Dossier frontend.

    Returns:
        Nom du binaire, ou None.
    """
    if (frontend_dir / "pnpm-lock.yaml").exists() and _which("pnpm"):
        return "pnpm"
    if (frontend_dir / "yarn.lock").exists() and _which("yarn"):
        return "yarn"
    if (frontend_dir / "package-lock.json").exists() and _which("npm"):
        return "npm"
    for pm in ("pnpm", "npm", "yarn"):
        if _which(pm):
            return pm
    return None


# ============================================================================
#  Commandes CLI — cli / web / gui
# ============================================================================


@app.command("cli")
def cmd_cli(
    background: Annotated[bool, typer.Option("--background", "-b", help="Lancer en arrière-plan")] = False,
) -> None:
    """Lance le CLI Textual en mode dev."""
    ensure_env_file()
    ensure_config_file()
    mode = RunMode.BACKGROUND if background else RunMode.FOREGROUND
    handle = _run_cli(mode=mode, extra_args=[])
    if mode == RunMode.BACKGROUND:
        console.print(f"[green]✓ CLI lancé (pid={handle.pid})[/green]")
        console.print(f"Logs : [cyan]{handle.log_path}[/cyan]")
        return
    with _managed_processes([handle]):
        console.print("[green]CLI en cours… Ctrl+C pour quitter.[/green]")
        handle.popen.wait()


@app.command("web")
def cmd_web(
    host: Annotated[str, typer.Option("--host")] = DEFAULT_API_HOST,
    port: Annotated[int, typer.Option("--port", "-p")] = DEFAULT_API_PORT,
    frontend_port: Annotated[int, typer.Option("--frontend-port")] = DEFAULT_FRONTEND_PORT,
    no_frontend: Annotated[bool, typer.Option("--no-frontend", help="Ne pas lancer Next.js")] = False,
    no_reload: Annotated[bool, typer.Option("--no-reload", help="Désactiver le hot-reload backend")] = False,
    background: Annotated[bool, typer.Option("--background", "-b")] = False,
) -> None:
    """Lance le backend FastAPI + frontend Next.js en mode dev."""
    ensure_env_file()
    ensure_config_file()

    # Vérifie les ports
    for h, p, label in ((host, port, "backend"), (host, frontend_port, "frontend")):
        if not _is_port_free(h, p):
            console.print(f"[red]✗ Port {p} ({label}) déjà utilisé.[/red]")
            raise typer.Exit(code=1)

    mode = RunMode.BACKGROUND if background else RunMode.FOREGROUND
    handles: list[ProcessHandle] = []
    handles.append(_run_web_backend(mode=mode, host=host, port=port, reload=not no_reload, extra_args=[]))

    if not no_frontend:
        handles.append(_run_web_frontend(mode=mode, port=frontend_port, extra_args=[]))

    if mode == RunMode.BACKGROUND:
        for h in handles:
            console.print(f"[green]✓ {h.component.value} lancé (pid={h.pid}) — log: {h.log_path}[/green]")
        return

    with _managed_processes(handles):
        console.print()
        console.print(
            Panel(
                f"[bold green]Backend[/bold green]  : http://{host}:{port}\n"
                f"[bold green]API docs[/bold green] : http://{host}:{port}/docs\n"
                + (
                    f"[bold green]Frontend[/bold green] : http://{host}:{frontend_port}\n"
                    if not no_frontend
                    else ""
                )
                + "\n[dim]Ctrl+C pour arrêter.[/dim]",
                title="NexusDL dev server",
                border_style="green",
            ),
        )
        # Attend qu'un processus meure (ou Ctrl+C)
        try:
            while True:
                for h in handles:
                    if h.popen.poll() is not None:
                        console.print(
                            f"[yellow]{h.component.value} s'est arrêté (code={h.popen.returncode}).[/yellow]",
                        )
                        return
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass


@app.command("gui")
def cmd_gui(
    background: Annotated[bool, typer.Option("--background", "-b")] = False,
) -> None:
    """Lance le GUI CustomTkinter en mode dev."""
    ensure_env_file()
    ensure_config_file()
    if not _has_module("customtkinter"):
        console.print("[red]✗ customtkinter absent. Installer : `uv pip install -e '.[gui]'`[/red]")
        raise typer.Exit(code=1)
    mode = RunMode.BACKGROUND if background else RunMode.FOREGROUND
    handle = _run_gui(mode=mode, extra_args=[])
    if mode == RunMode.BACKGROUND:
        console.print(f"[green]✓ GUI lancé (pid={handle.pid}) — log: {handle.log_path}[/green]")
        return
    with _managed_processes([handle]):
        console.print("[green]GUI en cours… Ctrl+C pour quitter.[/green]")
        handle.popen.wait()


@app.command("all")
def cmd_all(
    no_web: Annotated[bool, typer.Option("--no-web", help="Ne pas lancer le web")] = False,
    no_frontend: Annotated[bool, typer.Option("--no-frontend")] = False,
    no_cli: Annotated[bool, typer.Option("--no-cli", help="Ne pas lancer le CLI")] = False,
) -> None:
    """Lance backend + frontend + CLI (web en background, CLI en foreground)."""
    ensure_env_file()
    ensure_config_file()

    if not _is_port_free(DEFAULT_API_HOST, DEFAULT_API_PORT):
        console.print(f"[red]Port {DEFAULT_API_PORT} occupé.[/red]")
        raise typer.Exit(code=1)

    handles: list[ProcessHandle] = []
    try:
        if not no_web:
            handles.append(
                _run_web_backend(
                    mode=RunMode.BACKGROUND,
                    host=DEFAULT_API_HOST,
                    port=DEFAULT_API_PORT,
                    reload=True,
                    extra_args=[],
                ),
            )
            if not no_frontend:
                handles.append(
                    _run_web_frontend(mode=RunMode.BACKGROUND, port=DEFAULT_FRONTEND_PORT, extra_args=[]),
                )

        console.print("[green]Services lancés en arrière-plan :[/green]")
        for h in handles:
            console.print(f"  • {h.component.value} (pid={h.pid}) → {h.log_path}")

        if not no_cli:
            console.print()
            console.print("[cyan]Lancement du CLI (foreground). Ctrl+C pour arrêter le CLI uniquement.[/cyan]")
            cli_handle = _run_cli(mode=RunMode.FOREGROUND, extra_args=[])
            try:
                cli_handle.popen.wait()
            except KeyboardInterrupt:
                _terminate(cli_handle)
    finally:
        # Arrête les services d'arrière-plan
        for h in handles:
            _terminate(h)
        console.print("[green]✓ Tous les services arrêtés.[/green]")


# ============================================================================
#  REPL
# ============================================================================


@app.command("shell")
def cmd_shell(
    ipython: Annotated[bool, typer.Option("--ipython/--plain", help="Forcer IPython ou python natif")] = False,
) -> None:
    """Ouvre un REPL Python avec NexusDL préchargé."""
    ensure_env_file()
    ensure_config_file()

    banner = """
[bold cyan]NexusDL dev shell[/bold cyan]

Objets préchargés :
    [green]settings[/green]      — Settings Pydantic résolus
    [green]registry[/green]      — SiteRegistry chargé
    [green]get_session[/green]   — Factory HttpSession(site_id)
    [green]EventBus[/green]      — Bus d'événements global
    [green]asyncio[/green]       — Module asyncio (pour `await` interactif)

Exemple :
    >>> site = registry.get_site("mangadex")
    >>> async with get_session("mangadex") as s:
    ...     r = await s.get("https://mangadex.org/")
    ...     print(r.status_code)
"""

    console.print(Panel(banner.strip(), border_style="cyan"))

    # Script d'init exécuté par le REPL
    init_code = (
        "import asyncio\n"
        "from nexusdl.core.config import get_settings\n"
        "from nexusdl.core.registry.site_registry import SiteRegistry\n"
        "from nexusdl.core.session.http_session import HttpSession\n"
        "from nexusdl.core.events import EventBus\n"
        "settings = get_settings()\n"
        "registry = SiteRegistry.from_settings(settings)\n"
        "registry.load()\n"
        "event_bus = EventBus.instance()\n"
        "def get_session(site_id, **kw):\n"
        "    site = registry.get_site(site_id)\n"
        "    return HttpSession(site, **kw)\n"
    )

    env = _build_env({"PYTHONSTARTUP": ""})

    use_ipython = ipython or _has_module("IPython")
    if use_ipython and not ipython:
        # Auto-détection : IPython si présent
        cmd = [
            sys.executable,
            "-m",
            "IPython",
            "--no-banner",
            "--no-confirm-exit",
            "-i",
            "-c",
            init_code,
        ]
    elif use_ipython:
        cmd = [sys.executable, "-m", "IPython", "--no-banner", "-i", "-c", init_code]
    else:
        # Plain python -i avec exec du code via un fichier temporaire
        _ensure_dev_state_dir()
        init_file = _DEV_STATE_DIR / "shell_init.py"
        init_file.write_text(init_code, encoding="utf-8")
        env["PYTHONSTARTUP"] = str(init_file)
        cmd = [sys.executable, "-i"]

    try:
        subprocess.run(cmd, cwd=_ROOT_DIR, env=env, check=False)  # noqa: S603
    except KeyboardInterrupt:
        pass


# ============================================================================
#  Doctor
# ============================================================================


def _check_python_version(report: DoctorReport) -> None:
    """Vérifie la version de Python.

    Args:
        report: Rapport à peupler.
    """
    major, minor, micro = sys.version_info[:3]
    if (major, minor) < (3, 12):
        report.checks.append(("Python ≥ 3.12", "fail", f"Actuel : {major}.{minor}.{micro}"))
    else:
        report.checks.append(("Python ≥ 3.12", "ok", f"{major}.{minor}.{micro}"))


def _check_required_modules(report: DoctorReport) -> None:
    """Vérifie la présence des dépendances core.

    Args:
        report: Rapport à peupler.
    """
    required = {
        "pydantic": "core",
        "pydantic_settings": "core",
        "httpx": "core",
        "aiofiles": "core",
        "loguru": "core",
        "tenacity": "core",
        "aiosqlite": "core",
        "platformdirs": "core",
        "orjson": "core",
        "yaml": "core",
        "rich": "core",
        "typer": "core",
        "textual": "core",
        "PIL": "core",
    }
    optional = {
        "playwright": "sites JS / Cloudflare",
        "customtkinter": "GUI",
        "fastapi": "web backend",
        "uvicorn": "web backend",
        "IPython": "REPL amélioré",
    }
    for mod, kind in required.items():
        if _has_module(mod):
            report.checks.append((f"module {mod}", "ok", kind))
        else:
            report.checks.append((f"module {mod}", "fail", f"{kind} — requis"))
    for mod, kind in optional.items():
        if _has_module(mod):
            report.checks.append((f"module {mod}", "ok", f"{kind} (optionnel)"))
        else:
            report.checks.append((f"module {mod}", "warn", f"{kind} — optionnel"))


def _check_nodejs(report: DoctorReport) -> None:
    """Vérifie la présence de Node.js et d'un package manager.

    Args:
        report: Rapport à peupler.
    """
    node = _which("node")
    if node is None:
        report.checks.append(("Node.js", "warn", "absent — frontend non lançable"))
    else:
        try:
            out = subprocess.run(  # noqa: S603
                [node, "--version"], capture_output=True, text=True, check=False, timeout=5.0
            )
            report.checks.append(("Node.js", "ok", out.stdout.strip()))
        except Exception:  # noqa: BLE001
            report.checks.append(("Node.js", "warn", "présent mais version illisible"))

    pm = _detect_pm(_FRONTEND_DIR) if _FRONTEND_DIR.exists() else None
    if pm is None:
        report.checks.append(("package manager", "warn", "aucun (pnpm/npm/yarn)"))
    else:
        report.checks.append(("package manager", "ok", pm))


def _check_ports(report: DoctorReport) -> None:
    """Vérifie la disponibilité des ports par défaut.

    Args:
        report: Rapport à peupler.
    """
    for port, label in (
        (DEFAULT_API_PORT, "backend"),
        (DEFAULT_FRONTEND_PORT, "frontend"),
        (DEFAULT_FLARESOLVERR_PORT, "flaresolverr"),
    ):
        if _is_port_free(DEFAULT_API_HOST, port):
            report.checks.append((f"port {port} ({label})", "ok", "libre"))
        else:
            report.checks.append((f"port {port} ({label})", "warn", "occupé"))


def _check_flaresolverr(report: DoctorReport) -> None:
    """Vérifie la disponibilité de FlareSolverr si configuré.

    Args:
        report: Rapport à peupler.
    """
    try:
        import httpx  # noqa: PLC0415

        url = f"http://{DEFAULT_API_HOST}:{DEFAULT_FLARESOLVERR_PORT}/health"
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(url)
        if resp.status_code == 200:
            report.checks.append(("flaresolverr", "ok", "répond 200"))
        else:
            report.checks.append(("flaresolverr", "warn", f"status {resp.status_code}"))
    except Exception:  # noqa: BLE001
        report.checks.append(("flaresolverr", "warn", "non joignable (optionnel)"))


def _check_config(report: DoctorReport) -> None:
    """Vérifie la présence des fichiers de config.

    Args:
        report: Rapport à peupler.
    """
    if _ENV_FILE.exists():
        report.checks.append((".env", "ok", str(_ENV_FILE)))
    else:
        report.checks.append((".env", "warn", "absent — `dev.py init`"))
    if _CONFIG_FILE.exists():
        report.checks.append(("config.yaml", "ok", str(_CONFIG_FILE)))
    else:
        report.checks.append(("config.yaml", "warn", "absent — utilise les défauts"))


def _check_git(report: DoctorReport) -> None:
    """Vérifie que le dépôt Git est propre.

    Args:
        report: Rapport à peupler.
    """
    if not (_ROOT_DIR / ".git").exists():
        report.checks.append(("git", "warn", "pas un dépôt git"))
        return
    result = subprocess.run(  # noqa: S603
        ["git", "status", "--porcelain"],  # noqa: S607
        cwd=_ROOT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout.strip():
        report.checks.append(("git", "warn", f"{len(result.stdout.splitlines())} fichier(s) modifié(s)"))
    else:
        report.checks.append(("git", "ok", "arbre propre"))


@app.command("doctor")
def cmd_doctor(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Afficher les chemins")] = False,
) -> None:
    """Diagnostique l'environnement de développement."""
    report = DoctorReport()
    _check_python_version(report)
    _check_required_modules(report)
    _check_nodejs(report)
    _check_ports(report)
    _check_flaresolverr(report)
    _check_config(report)
    _check_git(report)

    table = Table(title="Environnement dev", header_style="bold cyan")
    table.add_column("Check", style="cyan", overflow="fold")
    table.add_column("Statut", justify="center")
    table.add_column("Détail", style="dim", overflow="fold")

    icons = {"ok": "[green]✓[/green]", "warn": "[yellow]⚠[/yellow]", "fail": "[red]✗[/red]"}
    for name, status, message in report.checks:
        detail = message if verbose or status != "ok" else ""
        table.add_row(name, icons[status], detail)

    console.print(table)
    console.print()
    console.print(
        f"[bold]Résultat :[/bold] {report.failed} échec(s), {report.warnings} warning(s).",
    )
    if report.failed:
        raise typer.Exit(code=1)


# ============================================================================
#  Init / stop / status
# ============================================================================


@app.command("init")
def cmd_init() -> None:
    """Initialise l'environnement dev (.env, config.yaml)."""
    ensure_env_file(create=True)
    ensure_config_file(create=True)
    _ensure_dev_state_dir()
    console.print("[green]✓ Environnement initialisé.[/green]")
    console.print(f"  .env            : {_ENV_FILE}")
    console.print(f"  config/config.yaml : {_CONFIG_FILE}")


@app.command("stop")
def cmd_stop() -> None:
    """Arrête tous les processus d'arrière-plan."""
    stopped = _stop_all()
    if stopped == 0:
        console.print("[dim]Aucun processus d'arrière-plan actif.[/dim]")
    else:
        console.print(f"[green]✓ {stopped} processus arrêté(s).[/green]")


@app.command("status")
def cmd_status() -> None:
    """Affiche l'état des processus d'arrière-plan."""
    _ensure_dev_state_dir()
    table = Table(title="Processus dev", header_style="bold cyan")
    table.add_column("Composant", style="cyan")
    table.add_column("PID", justify="right")
    table.add_column("État", justify="center")
    table.add_column("Log", style="dim")

    any_alive = False
    for component in Component:
        pid = _read_pid(component)
        if pid is None:
            table.add_row(component.value, "—", "[dim]arrêté[/dim]", "—")
            continue
        any_alive = True
        table.add_row(component.value, str(pid), "[green]vivant[/green]", str(_log_path(component)))

    console.print(table)
    if not any_alive:
        console.print("[dim]Aucun processus actif — lancer `dev.py all --background`.[/dim]")


# ============================================================================
#  Entrée
# ============================================================================


def main() -> None:
    """Point d'entrée CLI."""
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<level>{level: <8}</level> | {message}")

    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrompu.[/yellow]")
        raise typer.Exit(code=130) from None
    except RuntimeError as exc:
        console.print(f"[red]Erreur :[/red] {exc}")
        raise typer.Exit(code=2) from None


if __name__ == "__main__":
    main()
