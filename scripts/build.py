#!/usr/bin/env python3
"""Build unifié des artefacts distribuables de NexusDL.

Orchestre la construction des trois cibles principales :

    1. Package Python (wheel + sdist) via hatchling
    2. Frontend Next.js (build statique pour intégration dans le wheel)
    3. Exécutables GUI (PyInstaller) — optionnel, cross-platform

Chaque cible est indépendante et peut être sélectionnée par flag. Le script
nettoie par défaut, produit des artefacts dans dist/, et vérifie leur
intégrité (hash SHA256, contenu attendu, absence de secrets).

Example:
    Build complet::

        python scripts/build.py all

    Uniquement le package Python::

        python scripts/build.py python

    Frontend + package (mode production Docker)::

        python scripts/build.py python frontend --profile release

    GUI uniquement sur macOS::

        python scripts/build.py gui --target macos --gui-onefile
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final, Iterator, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

# --- Résolution des chemins racine -----------------------------------------
_SCRIPT_DIR: Final[Path] = Path(__file__).resolve().parent
_ROOT_DIR: Final[Path] = _SCRIPT_DIR.parent
_SRC_DIR: Final[Path] = _ROOT_DIR / "src"
_FRONTEND_DIR: Final[Path] = _SRC_DIR / "nexusdl" / "interfaces" / "web" / "frontend"
_DIST_DIR: Final[Path] = _ROOT_DIR / "dist"
_BUILD_DIR: Final[Path] = _ROOT_DIR / "build"

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
    name="build",
    help="Build unifié des artefacts NexusDL.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

BUILD_TIMEOUT_PYTHON: Final[float] = 600.0     # 10 min
BUILD_TIMEOUT_FRONTEND: Final[float] = 900.0   # 15 min (npm ci + next build)
BUILD_TIMEOUT_GUI: Final[float] = 1200.0       # 20 min (PyInstaller est lent)

# Fichiers sensibles à exclure de tout artefact
FORBIDDEN_PATTERNS: Final[tuple[str, ...]] = (
    ".env",
    ".env.local",
    "config.yaml",
    "proxy.yaml",
    "sites_overrides.yaml",
    "cookies.enc",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
)


class BuildTarget(str, Enum):
    """Cibles de build disponibles."""

    PYTHON = "python"
    FRONTEND = "frontend"
    GUI = "gui"
    DOCS = "docs"
    ALL = "all"


class BuildProfile(str, Enum):
    """Profil de build (influence les optimisations)."""

    DEV = "dev"        # Debug symbols, source maps, pas de minification
    RELEASE = "release"  # Optimisé, minifié, source maps désactivées


@dataclass(slots=True)
class BuildArtifact:
    """Description d'un artefact produit.

    Attributes:
        name: Nom du fichier (ex: "nexusdl-0.1.0-py3-none-any.whl").
        path: Chemin absolu de l'artefact.
        size_bytes: Taille en octets.
        sha256: Hash SHA256 hexadécimal.
        kind: Type d'artefact (wheel, sdist, gui-exe, web-bundle, docs).
    """

    name: str
    path: Path
    size_bytes: int
    sha256: str
    kind: str


@dataclass(slots=True)
class BuildReport:
    """Rapport de build.

    Attributes:
        started_at: Timestamp epoch.
        finished_at: Timestamp epoch.
        duration_seconds: Durée totale.
        profile: Profil utilisé.
        targets: Cibles demandées.
        artifacts: Liste des artefacts produits.
        errors: Erreurs éventuelles.
        warnings: Avertissements.
    """

    started_at: float
    finished_at: float
    duration_seconds: float
    profile: BuildProfile
    targets: list[BuildTarget]
    artifacts: list[BuildArtifact] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ============================================================================
#  Utilitaires
# ============================================================================


@contextmanager
def step(title: str) -> Iterator[None]:
    """Context manager qui log le début/fin d'une étape avec durée.

    Args:
        title: Titre de l'étape.

    Yields:
        Rien.

    Raises:
        RuntimeError: Reformulée avec le contexte de l'étape en cas d'échec.
    """
    console.rule(f"[bold cyan]{title}[/bold cyan]", style="cyan")
    t0 = time.perf_counter()
    try:
        yield
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        console.print(f"[red]✗ {title} a échoué après {elapsed:.2f}s[/red]")
        raise
    else:
        elapsed = time.perf_counter() - t0
        console.print(f"[green]✓ {title} ({elapsed:.2f}s)[/green]")


def run(
    cmd: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = BUILD_TIMEOUT_PYTHON,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Exécute une commande shell avec logging.

    Args:
        cmd: Commande et arguments (liste, pas de shell=True).
        cwd: Répertoire de travail.
        env: Variables d'environnement additionnelles.
        timeout: Timeout en secondes.
        capture: Si True, capture stdout/stderr au lieu de les streamer.
        check: Si True, lève une exception si le code retour != 0.

    Returns:
        Le `CompletedProcess` résultant.

    Raises:
        subprocess.CalledProcessError: Si `check=True` et code retour != 0.
        subprocess.TimeoutExpired: Si la commande dépasse `timeout`.
        FileNotFoundError: Si le binaire n'existe pas.
    """
    merged_env = {**os.environ, **(env or {})}
    logger.debug("run: {} (cwd={})", " ".join(cmd), cwd or _ROOT_DIR)
    try:
        return subprocess.run(  # noqa: S603 — cmd est contrôlé, pas de shell
            list(cmd),
            cwd=cwd or _ROOT_DIR,
            env=merged_env,
            timeout=timeout,
            check=check,
            capture_output=capture,
            text=True,
        )
    except FileNotFoundError as exc:
        msg = f"Commande introuvable : {cmd[0]} (le binaire est-il installé et dans le PATH ?)"
        raise RuntimeError(msg) from exc


def sha256_file(path: Path, *, chunk_size: int = 65536) -> str:
    """Calcule le SHA256 d'un fichier en streaming.

    Args:
        path: Fichier à hasher.
        chunk_size: Taille des chunks de lecture.

    Returns:
        Hash hexadécimal (64 caractères).
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def make_artifact(path: Path, kind: str) -> BuildArtifact:
    """Construit un `BuildArtifact` à partir d'un chemin.

    Args:
        path: Chemin du fichier produit.
        kind: Type d'artefact.

    Returns:
        L'artefact peuplé.
    """
    stat = path.stat()
    return BuildArtifact(
        name=path.name,
        path=path,
        size_bytes=stat.st_size,
        sha256=sha256_file(path),
        kind=kind,
    )


def clean_dir(path: Path) -> None:
    """Supprime et recrée un dossier, en tolérant les permissions.

    Args:
        path: Dossier à nettoyer.
    """
    if path.exists():
        logger.debug("Cleaning {}", path)
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


def find_tool(name: str) -> str | None:
    """Cherche un binaire dans le PATH.

    Args:
        name: Nom du binaire.

    Returns:
        Le chemin absolu, ou None si absent.
    """
    return shutil.which(name)


def scan_forbidden_files(root: Path) -> list[Path]:
    """Détecte les fichiers sensibles qui ne devraient pas être empaquetés.

    Args:
        root: Racine à scanner.

    Returns:
        Liste des chemins suspects.
    """
    hits: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(pat in path.name for pat in FORBIDDEN_PATTERNS):
            hits.append(path)
    return hits


# ============================================================================
#  Build Python (wheel + sdist)
# ============================================================================


def build_python(*, profile: BuildProfile, clean: bool) -> list[BuildArtifact]:
    """Construit le wheel et le sdist via hatchling.

    Args:
        profile: Profil de build (dev/release).
        clean: Si True, nettoie dist/ et build/ avant.

    Returns:
        Liste des artefacts (wheel + sdist).

    Raises:
        RuntimeError: Si le toolchain est absent ou si le build échoue.
    """
    with step("Build package Python (wheel + sdist)"):
        if clean:
            clean_dir(_DIST_DIR)
            clean_dir(_BUILD_DIR)

        # Vérifie les pré-requis
        if find_tool("uv") is None and find_tool("python") is None:
            msg = "Ni 'uv' ni 'python' ne sont dans le PATH."
            raise RuntimeError(msg)

        # hatchling est le backend configuré dans pyproject.toml
        env = {
            "PYTHONDONTWRITEBYTECODE": "1",
            "NEXUSDL_BUILD_PROFILE": profile.value,
        }

        # Privilégie `uv build` (plus rapide, isolation propre)
        if find_tool("uv"):
            cmd = ["uv", "build", "--out-dir", str(_DIST_DIR)]
            if profile == BuildProfile.DEV:
                cmd.append("--no-build-isolation")
        else:
            cmd = [
                sys.executable,
                "-m",
                "build",
                "--outdir",
                str(_DIST_DIR),
                "--no-isolation" if profile == BuildProfile.DEV else "--sdist",
            ]

        run(cmd, env=env, timeout=BUILD_TIMEOUT_PYTHON)

    artifacts: list[BuildArtifact] = []
    for whl in _DIST_DIR.glob("*.whl"):
        artifacts.append(make_artifact(whl, "wheel"))
    for sdist in _DIST_DIR.glob("*.tar.gz"):
        artifacts.append(make_artifact(sdist, "sdist"))

    if not artifacts:
        msg = "Aucun artefact Python produit — vérifier pyproject.toml et hatchling."
        raise RuntimeError(msg)

    # Vérification : aucun fichier sensible dans le wheel
    _verify_wheel_clean(next(a for a in artifacts if a.kind == "wheel"))
    _verify_sdist_clean(next(a for a in artifacts if a.kind == "sdist"))

    return artifacts


def _verify_wheel_clean(wheel: BuildArtifact) -> None:
    """Vérifie qu'aucun fichier sensible n'est embarqué dans le wheel.

    Args:
        wheel: Artefact wheel.

    Raises:
        RuntimeError: Si un fichier interdit est détecté.
    """
    with zipfile.ZipFile(wheel.path) as zf:
        names = zf.namelist()
    forbidden = [n for n in names if any(pat in Path(n).name for pat in FORBIDDEN_PATTERNS)]
    if forbidden:
        msg = f"Fichiers sensibles détectés dans le wheel : {forbidden}"
        raise RuntimeError(msg)
    logger.debug("Wheel clean — {} fichiers", len(names))


def _verify_sdist_clean(sdist: BuildArtifact) -> None:
    """Vérifie qu'aucun fichier sensible n'est embarqué dans le sdist.

    Args:
        sdist: Artefact sdist.

    Raises:
        RuntimeError: Si un fichier interdit est détecté.
    """
    with tarfile.open(sdist.path, "r:gz") as tf:
        names = tf.getnames()
    forbidden = [n for n in names if any(pat in Path(n).name for pat in FORBIDDEN_PATTERNS)]
    if forbidden:
        msg = f"Fichiers sensibles détectés dans le sdist : {forbidden}"
        raise RuntimeError(msg)
    logger.debug("Sdist clean — {} entrées", len(names))


# ============================================================================
#  Build Frontend (Next.js)
# ============================================================================


def build_frontend(*, profile: BuildProfile, clean: bool) -> list[BuildArtifact]:
    """Construit le frontend Next.js.

    En profil release, le build est copié dans
    `src/nexusdl/interfaces/web/backend/static/` pour être servi par FastAPI.
    En profil dev, le build reste dans le frontend.

    Args:
        profile: Profil de build.
        clean: Si True, nettoie les artefacts précédents.

    Returns:
        Liste des artefacts (un tarball du build statique).

    Raises:
        RuntimeError: Si npm/pnpm est absent ou si le build échoue.
    """
    with step("Build frontend Next.js"):
        if not _FRONTEND_DIR.exists():
            msg = f"Dossier frontend introuvable : {_FRONTEND_DIR}"
            raise RuntimeError(msg)

        package_manager, lockfile = _detect_package_manager(_FRONTEND_DIR)
        logger.info("Package manager détecté : {}", package_manager)

        if clean:
            for d in ("node_modules", ".next", "out"):
                clean_dir(_FRONTEND_DIR / d)

        # Install (ci si lockfile présent, sinon install)
        install_cmd = [package_manager]
        if package_manager == "pnpm":
            install_cmd += ["install", "--frozen-lockfile"] if lockfile else ["install"]
        elif package_manager == "npm":
            install_cmd += ["ci"] if lockfile else ["install"]
        elif package_manager == "yarn":
            install_cmd += ["install", "--frozen-lockfile"] if lockfile else ["install"]

        run(install_cmd, cwd=_FRONTEND_DIR, timeout=BUILD_TIMEOUT_FRONTEND)

        # Build
        build_env = {
            "NODE_ENV": "production" if profile == BuildProfile.RELEASE else "development",
            "NEXT_TELEMETRY_DISABLED": "1",
            "NEXT_PUBLIC_BUILD_PROFILE": profile.value,
        }
        run([package_manager, "run", "build"], cwd=_FRONTEND_DIR, env=build_env, timeout=BUILD_TIMEOUT_FRONTEND)

    # Next.js produit soit `out/` (export statique) soit `.next/` (SSR)
    out_dir = _FRONTEND_DIR / "out"
    next_dir = _FRONTEND_DIR / ".next"
    static_src = out_dir if out_dir.exists() else next_dir
    if not static_src.exists():
        msg = "Build frontend introuvable : ni 'out/' ni '.next/' n'existe."
        raise RuntimeError(msg)

    # Tarball pour traçabilité (même si on intègre dans le wheel en release)
    archive = _DIST_DIR / f"nexusdl-web-{profile.value}.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(static_src, arcname="web")

    artifacts = [make_artifact(archive, "web-bundle")]

    # En release : copie dans le backend pour être embarqué dans le wheel
    if profile == BuildProfile.RELEASE:
        target = _SRC_DIR / "nexusdl" / "interfaces" / "web" / "backend" / "static"
        clean_dir(target)
        shutil.copytree(static_src, target, dirs_exist_ok=True)
        logger.info("Frontend copié dans {}", target)

    return artifacts


def _detect_package_manager(frontend_dir: Path) -> tuple[str, bool]:
    """Détecte le package manager à utiliser et la présence d'un lockfile.

    Args:
        frontend_dir: Dossier du frontend.

    Returns:
        Tuple (nom du binaire, lockfile présent).

    Raises:
        RuntimeError: Si aucun package manager n'est disponible.
    """
    if (frontend_dir / "pnpm-lock.yaml").exists() and find_tool("pnpm"):
        return "pnpm", True
    if (frontend_dir / "yarn.lock").exists() and find_tool("yarn"):
        return "yarn", True
    if (frontend_dir / "package-lock.json").exists() and find_tool("npm"):
        return "npm", True
    # Fallbacks par ordre de préférence
    for pm in ("pnpm", "npm", "yarn"):
        if find_tool(pm):
            return pm, False
    msg = "Aucun package manager Node.js trouvé (pnpm, npm, yarn)."
    raise RuntimeError(msg)


# ============================================================================
#  Build GUI (PyInstaller)
# ============================================================================


def build_gui(
    *,
    profile: BuildProfile,
    target: Literal["auto", "linux", "macos", "windows"],
    onefile: bool,
) -> list[BuildArtifact]:
    """Construit l'exécutable GUI via PyInstaller.

    Args:
        profile: Profil de build.
        target: Plateforme cible (auto = plateforme courante).
        onefile: Si True, produit un seul exécutable (démarrage plus lent).

    Returns:
        Liste des artefacts produits.

    Raises:
        RuntimeError: Si PyInstaller est absent ou si le build échoue.
    """
    with step(f"Build GUI PyInstaller (target={target}, onefile={onefile})"):
        if find_tool("pyinstaller") is None:
            msg = (
                "PyInstaller introuvable. Installe les extras GUI : "
                "`uv pip install -e '.[gui,build]'`"
            )
            raise RuntimeError(msg)

        resolved_target = _resolve_gui_target(target)
        spec_file = _ROOT_DIR / "packaging" / "pyinstaller" / f"nexusdl-{resolved_target}.spec"
        if not spec_file.exists():
            msg = f"Spec PyInstaller introuvable : {spec_file}"
            raise RuntimeError(msg)

        work_dir = _BUILD_DIR / f"pyinstaller-{resolved_target}"
        out_dir = _DIST_DIR / f"gui-{resolved_target}"
        clean_dir(work_dir)
        clean_dir(out_dir)

        env = {
            "NEXUSDL_BUILD_PROFILE": profile.value,
            "PYTHONOPTIMIZE": "2" if profile == BuildProfile.RELEASE else "0",
        }
        cmd = [
            "pyinstaller",
            str(spec_file),
            "--distpath",
            str(out_dir),
            "--workpath",
            str(work_dir),
            "--noconfirm",
        ]
        if onefile:
            cmd.append("--onefile")
        if profile == BuildProfile.RELEASE:
            cmd.append("--clean")

        run(cmd, env=env, timeout=BUILD_TIMEOUT_GUI)

    artifacts: list[BuildArtifact] = []
    for item in out_dir.iterdir():
        if item.is_file():
            # Ignore les .spec générés
            if item.suffix == ".spec":
                continue
            artifacts.append(make_artifact(item, f"gui-{resolved_target}"))
        elif item.is_dir():
            # PyInstaller --onedir : archive le dossier
            archive = _DIST_DIR / f"nexusdl-gui-{resolved_target}.tar.gz"
            with tarfile.open(archive, "w:gz") as tf:
                tf.add(item, arcname=item.name)
            artifacts.append(make_artifact(archive, f"gui-{resolved_target}"))

    if not artifacts:
        msg = "Aucun artefact GUI produit."
        raise RuntimeError(msg)
    return artifacts


def _resolve_gui_target(target: str) -> str:
    """Résout 'auto' vers la plateforme courante.

    Args:
        target: Cible demandée.

    Returns:
        Nom de plateforme normalisé.

    Raises:
        RuntimeError: Si la plateforme est inconnue.
    """
    if target != "auto":
        return target
    sysname = platform.system().lower()
    mapping = {"darwin": "macos", "linux": "linux", "windows": "windows"}
    resolved = mapping.get(sysname)
    if resolved is None:
        msg = f"Plateforme non supportée : {sysname}"
        raise RuntimeError(msg)
    return resolved


# ============================================================================
#  Build Docs (Sphinx)
# ============================================================================


def build_docs(*, profile: BuildProfile) -> list[BuildArtifact]:
    """Construit la documentation Sphinx (HTML + man pages).

    Args:
        profile: Profil de build.

    Returns:
        Liste des artefacts.

    Raises:
        RuntimeError: Si Sphinx est absent ou si le build échoue.
    """
    with step("Build documentation Sphinx"):
        docs_src = _ROOT_DIR / "docs" / "source"
        if not docs_src.exists():
            msg = f"Dossier source Sphinx introuvable : {docs_src}"
            raise RuntimeError(msg)

        out_html = _BUILD_DIR / "docs" / "html"
        out_man = _BUILD_DIR / "docs" / "man"
        clean_dir(out_html)
        clean_dir(out_man)

        try:
            run(
                [
                    sys.executable,
                    "-m",
                    "sphinx",
                    "-b",
                    "html",
                    "-W" if profile == BuildProfile.RELEASE else "-w",
                    str(docs_src),
                    str(out_html),
                ],
                timeout=BUILD_TIMEOUT_PYTHON,
            )
        except subprocess.CalledProcessError as exc:
            msg = "Sphinx introuvable ou build en échec. Installe : `uv pip install -e '.[docs]'`"
            raise RuntimeError(msg) from exc

        # Man pages (optionnel, peut échouer silencieusement)
        try:
            run(
                [sys.executable, "-m", "sphinx", "-b", "man", str(docs_src), str(out_man)],
                timeout=BUILD_TIMEOUT_PYTHON,
            )
        except subprocess.CalledProcessError:
            logger.warning("Man pages : build échoué (non bloquant)")

    archive = _DIST_DIR / "nexusdl-docs.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(out_html, arcname="html")
        if out_man.exists() and any(out_man.iterdir()):
            tf.add(out_man, arcname="man")
    return [make_artifact(archive, "docs")]


# ============================================================================
#  Orchestrateur
# ============================================================================


def _orchestrate(
    targets: list[BuildTarget],
    *,
    profile: BuildProfile,
    clean: bool,
    gui_target: Literal["auto", "linux", "macos", "windows"],
    gui_onefile: bool,
) -> BuildReport:
    """Orchestre le build de toutes les cibles demandées.

    Args:
        targets: Cibles à builder.
        profile: Profil de build.
        clean: Nettoyer avant.
        gui_target: Cible GUI.
        gui_onefile: Mode onefile pour PyInstaller.

    Returns:
        Rapport complet.
    """
    started = time.perf_counter()
    report = BuildReport(
        started_at=time.time(),
        finished_at=0.0,
        duration_seconds=0.0,
        profile=profile,
        targets=targets,
    )

    _DIST_DIR.mkdir(parents=True, exist_ok=True)
    _BUILD_DIR.mkdir(parents=True, exist_ok=True)

    # Détection amont de fichiers sensibles dans le dépôt
    suspects = scan_forbidden_files(_ROOT_DIR)
    if suspects:
        report.warnings.append(
            f"{len(suspects)} fichier(s) sensible(s) présent(s) dans l'arbre source : "
            f"{[str(p.relative_to(_ROOT_DIR)) for p in suspects[:5]]}",
        )

    # Ordre canonique : frontend d'abord (peut être embarqué dans le wheel)
    build_order: list[BuildTarget] = []
    if BuildTarget.FRONTEND in targets or BuildTarget.ALL in targets:
        build_order.append(BuildTarget.FRONTEND)
    if BuildTarget.PYTHON in targets or BuildTarget.ALL in targets:
        build_order.append(BuildTarget.PYTHON)
    if BuildTarget.GUI in targets or BuildTarget.ALL in targets:
        build_order.append(BuildTarget.GUI)
    if BuildTarget.DOCS in targets or BuildTarget.ALL in targets:
        build_order.append(BuildTarget.DOCS)

    for target in build_order:
        try:
            if target is BuildTarget.PYTHON:
                report.artifacts.extend(build_python(profile=profile, clean=clean))
            elif target is BuildTarget.FRONTEND:
                report.artifacts.extend(build_frontend(profile=profile, clean=clean))
            elif target is BuildTarget.GUI:
                report.artifacts.extend(
                    build_gui(profile=profile, target=gui_target, onefile=gui_onefile),
                )
            elif target is BuildTarget.DOCS:
                report.artifacts.extend(build_docs(profile=profile))
        except Exception as exc:  # noqa: BLE001 — on agrège les erreurs pour continuer
            logger.exception("Build target {} failed", target.value)
            report.errors.append(f"{target.value}: {exc}")

    report.finished_at = time.time()
    report.duration_seconds = time.perf_counter() - started
    return report


# ============================================================================
#  Rendu / export
# ============================================================================


def render_report(report: BuildReport) -> None:
    """Affiche le rapport final dans le terminal.

    Args:
        report: Rapport de build.
    """
    console.print()
    if report.artifacts:
        table = Table(title="Artefacts produits", header_style="bold cyan")
        table.add_column("Type", style="magenta")
        table.add_column("Nom", style="cyan")
        table.add_column("Taille", justify="right")
        table.add_column("SHA256", style="dim")
        for a in report.artifacts:
            table.add_row(
                a.kind,
                a.name,
                _format_size(a.size_bytes),
                a.sha256[:16] + "…",
            )
        console.print(table)

    if report.warnings:
        console.print(Panel("\n".join(f"⚠ {w}" for w in report.warnings), title="Avertissements", border_style="yellow"))

    if report.errors:
        console.print(Panel("\n".join(f"✗ {e}" for e in report.errors), title="Erreurs", border_style="red"))

    console.print(
        f"[bold]Durée totale :[/bold] {report.duration_seconds:.2f}s "
        f"— profil [magenta]{report.profile.value}[/magenta] "
        f"— {len(report.artifacts)} artefact(s), "
        f"{len(report.warnings)} warning(s), {len(report.errors)} erreur(s)",
    )


def _format_size(size: int) -> str:
    """Formate une taille en octets en unité lisible.

    Args:
        size: Taille en octets.

    Returns:
        Chaîne du type "1.23 MiB".
    """
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024.0:
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} TiB"


def export_manifest(report: BuildReport, path: Path) -> None:
    """Exporte le rapport en JSON (manifeste pour release).

    Args:
        report: Rapport à exporter.
        path: Chemin destination.
    """
    payload = {
        "profile": report.profile.value,
        "targets": [t.value for t in report.targets],
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "duration_seconds": report.duration_seconds,
        "warnings": report.warnings,
        "errors": report.errors,
        "artifacts": [
            {
                "kind": a.kind,
                "name": a.name,
                "size_bytes": a.size_bytes,
                "sha256": a.sha256,
            }
            for a in report.artifacts
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Manifest exporté : {}", path)


# ============================================================================
#  CLI
# ============================================================================


@app.command("python")
def cmd_python(
    profile: Annotated[BuildProfile, typer.Option("--profile", "-p")] = BuildProfile.RELEASE,
    clean: Annotated[bool, typer.Option("--clean/--no-clean")] = True,
    manifest: Annotated[Path | None, typer.Option("--manifest", help="Export JSON du rapport")] = None,
) -> None:
    """Build le package Python uniquement."""
    report = _orchestrate(
        [BuildTarget.PYTHON],
        profile=profile,
        clean=clean,
        gui_target="auto",
        gui_onefile=False,
    )
    render_report(report)
    if manifest:
        export_manifest(report, manifest)
    if report.errors:
        raise typer.Exit(code=1)


@app.command("frontend")
def cmd_frontend(
    profile: Annotated[BuildProfile, typer.Option("--profile", "-p")] = BuildProfile.RELEASE,
    clean: Annotated[bool, typer.Option("--clean/--no-clean")] = False,
) -> None:
    """Build le frontend Next.js uniquement."""
    report = _orchestrate(
        [BuildTarget.FRONTEND],
        profile=profile,
        clean=clean,
        gui_target="auto",
        gui_onefile=False,
    )
    render_report(report)
    if report.errors:
        raise typer.Exit(code=1)


@app.command("gui")
def cmd_gui(
    profile: Annotated[BuildProfile, typer.Option("--profile", "-p")] = BuildProfile.RELEASE,
    target: Annotated[str, typer.Option("--target", "-t", help="auto|linux|macos|windows")] = "auto",
    onefile: Annotated[bool, typer.Option("--onefile/--onedir")] = False,
    clean: Annotated[bool, typer.Option("--clean/--no-clean")] = True,
) -> None:
    """Build l'exécutable GUI via PyInstaller."""
    if target not in {"auto", "linux", "macos", "windows"}:
        console.print(f"[red]Cible invalide : {target}[/red]")
        raise typer.Exit(code=2)
    report = _orchestrate(
        [BuildTarget.GUI],
        profile=profile,
        clean=clean,
        gui_target=target,  # type: ignore[arg-type]
        gui_onefile=onefile,
    )
    render_report(report)
    if report.errors:
        raise typer.Exit(code=1)


@app.command("docs")
def cmd_docs(
    profile: Annotated[BuildProfile, typer.Option("--profile", "-p")] = BuildProfile.RELEASE,
) -> None:
    """Build la documentation Sphinx."""
    report = _orchestrate(
        [BuildTarget.DOCS],
        profile=profile,
        clean=False,
        gui_target="auto",
        gui_onefile=False,
    )
    render_report(report)
    if report.errors:
        raise typer.Exit(code=1)


@app.command("all")
def cmd_all(
    profile: Annotated[BuildProfile, typer.Option("--profile", "-p")] = BuildProfile.RELEASE,
    clean: Annotated[bool, typer.Option("--clean/--no-clean")] = True,
    gui_target: Annotated[str, typer.Option("--gui-target")] = "auto",
    gui_onefile: Annotated[bool, typer.Option("--gui-onefile/--gui-onedir")] = False,
    manifest: Annotated[Path | None, typer.Option("--manifest")] = None,
    skip_gui: Annotated[bool, typer.Option("--skip-gui")] = False,
    skip_docs: Annotated[bool, typer.Option("--skip-docs")] = False,
) -> None:
    """Build toutes les cibles."""
    targets: list[BuildTarget] = [BuildTarget.FRONTEND, BuildTarget.PYTHON]
    if not skip_gui:
        targets.append(BuildTarget.GUI)
    if not skip_docs:
        targets.append(BuildTarget.DOCS)

    report = _orchestrate(
        targets,
        profile=profile,
        clean=clean,
        gui_target=gui_target,  # type: ignore[arg-type]
        gui_onefile=gui_onefile,
    )
    render_report(report)
    if manifest:
        export_manifest(report, manifest)
    if report.errors:
        raise typer.Exit(code=1)


# ============================================================================
#  Entrée
# ============================================================================


def main() -> None:
    """Point d'entrée CLI."""
    # Logging minimaliste : le script est déjà très verbeux via Rich
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<level>{level: <8}</level> | {message}")

    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrompu.[/yellow]")
        raise typer.Exit(code=130) from None
    except RuntimeError as exc:
        console.print(f"[red]Erreur de build :[/red] {exc}")
        raise typer.Exit(code=2) from None


if __name__ == "__main__":
    main()
