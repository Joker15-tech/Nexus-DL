#!/usr/bin/env python3
"""Nettoyage des artefacts, caches et données de NexusDL.

Offre plusieurs niveaux de nettoyage, du plus sûr au plus destructeur :

    caches   → __pycache__, .pytest_cache, .ruff_cache, .mypy_cache
    build    → dist/, build/, *.egg-info, .next/, out/, node_modules
    temp     → /tmp/nexusdl-*, fichiers temporaires de download
    user     → cookies, base de données, logs, configs (⚠️ destructeur)

Par défaut, le script fonctionne en **dry-run** : il montre ce qu'il ferait
sans rien supprimer. Utiliser `--apply` pour exécuter réellement.

Example:
    Voir ce qui serait supprimé (dry-run) ::

        python scripts/clean.py build

    Supprimer réellement les caches ::

        python scripts/clean.py caches --apply

    Nettoyage complet (caches + build + temp) ::

        python scripts/clean.py all --apply

    Nettoyage agressif incluant les données utilisateur ::

        python scripts/clean.py all --include-user --apply --yes

    Supprimer uniquement les fichiers plus vieux que 30 jours ::

        python scripts/clean.py temp --older-than 30 --apply
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Annotated, Final, Iterator

# --- Résolution des chemins racine -----------------------------------------
_SCRIPT_DIR: Final[Path] = Path(__file__).resolve().parent
_ROOT_DIR: Final[Path] = _SCRIPT_DIR.parent
_SRC_DIR: Final[Path] = _ROOT_DIR / "src"

if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import typer  # noqa: E402
from loguru import logger  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.prompt import Confirm  # noqa: E402
from rich.table import Table  # noqa: E402

# ============================================================================
#  Constantes
# ============================================================================

console: Final[Console] = Console()
app: Final[typer.Typer] = typer.Typer(
    name="clean",
    help="Nettoyage des artefacts, caches et données NexusDL.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# --- Patterns de fichiers/dossiers par catégorie ---------------------------
# Chaque entrée : (glob pattern, kind) où kind ∈ {"dir", "file", "glob"}
CACHE_PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    ("**/__pycache__", "dir"),
    ("**/*.pyc", "glob"),
    ("**/*.pyo", "glob"),
    ("**/.pytest_cache", "dir"),
    ("**/.mypy_cache", "dir"),
    ("**/.ruff_cache", "dir"),
    ("**/.hypothesis", "dir"),
    ("**/.tox", "dir"),
    ("**/.nox", "dir"),
    ("**/.cache", "dir"),
    ("**/*.egg-info", "dir"),
    ("**/.coverage", "file"),
    ("**/.coverage.*", "glob"),
    ("**/coverage.xml", "file"),
    ("**/htmlcov", "dir"),
    ("**/.benchmarks", "dir"),
    ("**/.pytype", "dir"),
    ("**/.dmypy.json", "file"),
    ("**/Cargo.lock", "file"),  # sécurité : on ne touche jamais à Cargo.lock racine
)

BUILD_PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    ("dist", "dir"),
    ("build", "dir"),
    ("*.egg-info", "dir"),
    ("src/*.egg-info", "dir"),
    (".eggs", "dir"),
    ("wheelhouse", "dir"),
    ("*.whl", "glob"),
    ("*.tar.gz", "glob"),
    ("packaging/pyinstaller/*.spec", "glob"),
    ("packaging/pyinstaller/build", "dir"),
    ("packaging/pyinstaller/dist", "dir"),
    ("src/nexusdl/interfaces/web/frontend/.next", "dir"),
    ("src/nexusdl/interfaces/web/frontend/out", "dir"),
    ("src/nexusdl/interfaces/web/frontend/node_modules", "dir"),
    ("src/nexusdl/interfaces/web/frontend/tsconfig.tsbuildinfo", "file"),
    ("src/nexusdl/interfaces/web/backend/static", "dir"),
    ("docs/build", "dir"),
    ("docs/source/_build", "dir"),
    ("docs/source/api/_build", "dir"),
)

TEMP_PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    ("/tmp/nexusdl-bench", "dir"),  # noqa: S108
    ("/tmp/nexusdl-*", "glob"),  # noqa: S108
    ("**/.nexusdl-tmp", "dir"),
    ("**/*.part", "glob"),  # fichiers de download partiels
    ("**/*.tmp", "glob"),
    ("**/.DS_Store", "file"),
    ("**/Thumbs.db", "file"),
)

# --- Fichiers/dossiers utilisateur (⚠️ destructeur) ------------------------
USER_PATTERNS_RELATIVE: Final[tuple[tuple[str, str], ...]] = (
    # Ces chemins sont relatifs au config_dir et library_dir résolus
    ("cookies.enc", "file"),
    ("cookies/", "dir"),
    ("library.db", "file"),
    ("library.db-wal", "file"),
    ("library.db-shm", "file"),
    ("cache/", "dir"),
    ("logs/", "dir"),
    ("*.log", "glob"),
    ("*.log.gz", "glob"),
    ("*.log.zip", "glob"),
    ("sessions/", "dir"),
    ("playwright_state.json", "file"),
    ("proxy_stats.json", "file"),
)

# --- Chemins à ne JAMAIS toucher (sécurité) --------------------------------
PROTECTED_PATHS: Final[tuple[str, ...]] = (
    ".git",
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
    "pyproject.toml",
    "LICENSE",
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "SECURITY_HALL_OF_FAME.md",
    ".env.example",
    ".python-version",
    ".pre-commit-config.yaml",
    ".gitmodules",
)

# --- Limite de taille pour éviter un glob accidentel -----------------------
MAX_FILES_PER_PATTERN: Final[int] = 10_000


class CleanTarget(str, Enum):
    """Cibles de nettoyage."""

    CACHES = "caches"
    BUILD = "build"
    TEMP = "temp"
    USER = "user"
    ALL = "all"


class CleanMode(str, Enum):
    """Mode de sécurité."""

    DRY_RUN = "dry-run"
    APPLY = "apply"


@dataclass(slots=True)
class CleanItem:
    """Un élément à supprimer.

    Attributes:
        path: Chemin absolu.
        kind: "dir", "file" ou "glob".
        category: Catégorie (caches, build, temp, user).
        size_bytes: Taille (récursive pour les dossiers).
        mtime: Dernière modification (epoch).
        protected: True si dans la liste protégée.
    """

    path: Path
    kind: str
    category: str
    size_bytes: int = 0
    mtime: float = 0.0
    protected: bool = False


@dataclass(slots=True)
class CleanReport:
    """Rapport de nettoyage.

    Attributes:
        mode: Dry-run ou apply.
        started_at: Timestamp epoch.
        finished_at: Timestamp epoch.
        duration_seconds: Durée.
        items: Tous les éléments détectés.
        deleted: Éléments effectivement supprimés.
        skipped: Éléments ignorés (protégés, trop récents).
        errors: Erreurs lors de la suppression.
        total_bytes_freed: Octets libérés.
    """

    mode: CleanMode
    started_at: float
    finished_at: float = 0.0
    duration_seconds: float = 0.0
    items: list[CleanItem] = field(default_factory=list)
    deleted: list[CleanItem] = field(default_factory=list)
    skipped: list[CleanItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_bytes_freed: int = 0


# ============================================================================
#  Utilitaires
# ============================================================================


def dir_size(path: Path) -> int:
    """Calcule la taille récursive d'un dossier (best-effort).

    Args:
        path: Chemin du dossier.

    Returns:
        Taille en octets. Retourne 0 en cas d'erreur d'accès.
    """
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


def is_protected(path: Path) -> bool:
    """Vérifie si un chemin est protégé (ne doit jamais être supprimé).

    Args:
        path: Chemin à vérifier.

    Returns:
        True si le chemin (ou un de ses ancêtres immédiats) est protégé.
    """
    try:
        rel = path.resolve().relative_to(_ROOT_DIR.resolve())
    except ValueError:
        # Hors racine : c'est un chemin système (ex: /tmp) → non protégé par ce check
        return False

    parts = rel.parts
    return any(part in PROTECTED_PATHS for part in parts)


def format_size(size: int) -> str:
    """Formate une taille en unité lisible.

    Args:
        size: Taille en octets.

    Returns:
        Chaîne du type "1.23 MiB".
    """
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0:
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} PiB"


def format_age(mtime: float) -> str:
    """Formate l'âge d'un élément.

    Args:
        mtime: Timestamp epoch de dernière modification.

    Returns:
        Chaîne du type "3d", "5h", "12m", "now".
    """
    age_s = max(0.0, time.time() - mtime)
    if age_s < 60:
        return "now"
    if age_s < 3600:
        return f"{int(age_s / 60)}m"
    if age_s < 86400:
        return f"{int(age_s / 3600)}h"
    if age_s < 86400 * 30:
        return f"{int(age_s / 86400)}d"
    return f"{int(age_s / 86400 / 30)}mo"


def resolve_user_dirs() -> tuple[Path, Path]:
    """Résout les dossiers utilisateur (config et library) via platformdirs.

    Returns:
        Tuple (config_dir, library_dir).
    """
    try:
        from platformdirs import user_config_dir, user_data_dir

        config_dir = Path(user_config_dir("nexusdl", "NexusDL"))
        data_dir = Path(user_data_dir("nexusdl", "NexusDL"))
    except ImportError:
        # Fallback XDG si platformdirs absent
        xdg_config = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        xdg_data = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
        config_dir = Path(xdg_config) / "nexusdl"
        data_dir = Path(xdg_data) / "nexusdl"
    return config_dir, data_dir


# ============================================================================
#  Scan
# ============================================================================


def _scan_patterns(
    root: Path,
    patterns: tuple[tuple[str, str], ...],
    category: str,
    *,
    older_than_days: float | None = None,
) -> Iterator[CleanItem]:
    """Scanne les patterns et produit les items correspondants.

    Args:
        root: Racine de recherche.
        patterns: Liste de (pattern, kind).
        category: Catégorie des items produits.
        older_than_days: Si défini, ne garde que les items plus vieux.

    Yields:
        Les `CleanItem` correspondant aux patterns.
    """
    cutoff = None
    if older_than_days is not None and older_than_days > 0:
        cutoff = time.time() - older_than_days * 86400.0

    seen: set[Path] = set()
    for pattern, kind in patterns:
        if kind == "dir":
            matches = [root / pattern] if (root / pattern).is_dir() else []
            matches.extend(root.glob(pattern))
            # Déduplique + filtre sur les dossiers
            unique = {m.resolve() for m in matches if m.is_dir()}
        elif kind == "file":
            matches_path = root / pattern
            unique = {matches_path.resolve()} if matches_path.is_file() else set()
        elif kind == "glob":
            unique = {m.resolve() for m in root.glob(pattern) if m.is_file()}
        else:
            continue

        count = 0
        for path in sorted(unique):
            if path in seen:
                continue
            seen.add(path)

            if not path.exists():
                continue
            if is_protected(path):
                yield CleanItem(path=path, kind=kind, category=category, protected=True)
                continue

            try:
                stat = path.stat()
            except OSError:
                continue

            if cutoff is not None and stat.st_mtime > cutoff:
                # Trop récent : on skippe
                continue

            size = dir_size(path) if path.is_dir() else stat.st_size
            yield CleanItem(
                path=path,
                kind=kind,
                category=category,
                size_bytes=size,
                mtime=stat.st_mtime,
            )

            count += 1
            if count >= MAX_FILES_PER_PATTERN:
                logger.warning(
                    "Pattern {} a produit plus de {} fichiers — tronqué",
                    pattern,
                    MAX_FILES_PER_PATTERN,
                )
                break


def scan(
    targets: list[CleanTarget],
    *,
    older_than_days: float | None = None,
) -> list[CleanItem]:
    """Scanne le système pour les éléments à nettoyer.

    Args:
        targets: Cibles de nettoyage.
        older_than_days: Si défini, ne garde que les items plus vieux.

    Returns:
        Liste de tous les items détectés (y compris protégés et skippés).
    """
    items: list[CleanItem] = []
    resolved_targets = set(targets)
    if CleanTarget.ALL in resolved_targets:
        resolved_targets = {
            CleanTarget.CACHES,
            CleanTarget.BUILD,
            CleanTarget.TEMP,
        }

    if CleanTarget.CACHES in resolved_targets:
        logger.debug("Scanning caches")
        items.extend(_scan_patterns(_ROOT_DIR, CACHE_PATTERNS, "caches", older_than_days=older_than_days))

    if CleanTarget.BUILD in resolved_targets:
        logger.debug("Scanning build artifacts")
        items.extend(_scan_patterns(_ROOT_DIR, BUILD_PATTERNS, "build", older_than_days=older_than_days))

    if CleanTarget.TEMP in resolved_targets:
        logger.debug("Scanning temp files")
        # Temp : patterns absolus (commencent par /)
        for pattern, kind in TEMP_PATTERNS:
            if pattern.startswith("/"):
                base = Path("/")
                rel = pattern.lstrip("/")
                matches = list(base.glob(rel))
            else:
                matches = list(_ROOT_DIR.glob(pattern))

            for path in matches:
                if not path.exists():
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                size = dir_size(path) if path.is_dir() else stat.st_size
                items.append(
                    CleanItem(
                        path=path,
                        kind=kind,
                        category="temp",
                        size_bytes=size,
                        mtime=stat.st_mtime,
                    ),
                )

    if CleanTarget.USER in resolved_targets:
        logger.debug("Scanning user data")
        config_dir, data_dir = resolve_user_dirs()
        for base_dir in (config_dir, data_dir):
            if not base_dir.exists():
                continue
            for pattern, kind in USER_PATTERNS_RELATIVE:
                matches: list[Path]
                if kind == "dir":
                    matches = [base_dir / pattern.rstrip("/")]
                elif kind == "file":
                    matches = [base_dir / pattern]
                else:  # glob
                    matches = list(base_dir.glob(pattern))

                for path in matches:
                    if not path.exists():
                        continue
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    if older_than_days is not None and older_than_days > 0:
                        cutoff = time.time() - older_than_days * 86400.0
                        if stat.st_mtime > cutoff:
                            continue
                    size = dir_size(path) if path.is_dir() else stat.st_size
                    items.append(
                        CleanItem(
                            path=path,
                            kind=kind,
                            category="user",
                            size_bytes=size,
                            mtime=stat.st_mtime,
                        ),
                    )

    # Déduplication finale (par chemin résolu)
    seen: set[Path] = set()
    unique_items: list[CleanItem] = []
    for item in items:
        try:
            key = item.path.resolve()
        except OSError:
            key = item.path
        if key in seen:
            continue
        seen.add(key)
        unique_items.append(item)

    return unique_items


# ============================================================================
#  Suppression
# ============================================================================


@contextmanager
def _chmod_retry(path: Path) -> Iterator[None]:
    """Context manager qui rend un chemin accessible avant suppression.

    Args:
        path: Chemin à rendre accessible.

    Yields:
        Rien.
    """
    try:
        if path.is_dir():
            os.chmod(path, 0o755)
        yield
    except OSError:
        pass


def _delete(item: CleanItem) -> tuple[bool, str | None]:
    """Supprime un élément (fichier ou dossier).

    Args:
        item: Élément à supprimer.

    Returns:
        Tuple (succès, message d'erreur éventuel).
    """
    path = item.path
    try:
        if not path.exists() and not path.is_symlink():
            return True, None
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            # Rendre accessible avant suppression (dossiers en lecture seule)
            for sub in path.rglob("*"):
                try:
                    if sub.is_dir():
                        os.chmod(sub, 0o755)
                except OSError:
                    continue
            shutil.rmtree(path, ignore_errors=False)
        return True, None
    except PermissionError as exc:
        return False, f"Permission refusée : {exc}"
    except OSError as exc:
        return False, f"Erreur OS : {exc}"


def execute(report: CleanReport) -> None:
    """Exécute la suppression des items d'un rapport.

    Args:
        report: Rapport contenant les items à supprimer.
    """
    for item in report.items:
        if item.protected:
            report.skipped.append(item)
            continue
        ok, err = _delete(item)
        if ok:
            report.deleted.append(item)
            report.total_bytes_freed += item.size_bytes
            logger.debug("Deleted {} ({})", item.path, format_size(item.size_bytes))
        else:
            report.errors.append(f"{item.path}: {err}")
            logger.warning("Failed to delete {}: {}", item.path, err)


# ============================================================================
#  Rendu
# ============================================================================


def render_preview(report: CleanReport, *, verbose: bool = False) -> None:
    """Affiche le rapport avant suppression (dry-run ou preview).

    Args:
        report: Rapport à afficher.
        verbose: Si True, affiche chaque fichier individuellement.
    """
    console.print()
    if not report.items:
        console.print(Panel("[green]Rien à nettoyer.[/green]", border_style="green"))
        return

    # Table par catégorie
    table = Table(title="Éléments détectés", header_style="bold cyan")
    table.add_column("Catégorie", style="magenta")
    table.add_column("Éléments", justify="right")
    table.add_column("Taille", justify="right", style="yellow")
    table.add_column("Protégés", justify="right", style="dim")

    by_cat: dict[str, list[CleanItem]] = {}
    for item in report.items:
        by_cat.setdefault(item.category, []).append(item)

    for cat, items in sorted(by_cat.items()):
        total = sum(i.size_bytes for i in items if not i.protected)
        protected = sum(1 for i in items if i.protected)
        table.add_row(
            cat,
            str(len(items)),
            format_size(total),
            str(protected) if protected else "·",
        )

    console.print(table)

    if verbose:
        console.print()
        detail = Table(title="Détail", header_style="bold cyan")
        detail.add_column("Catégorie", style="magenta")
        detail.add_column("Chemin", style="cyan", overflow="fold")
        detail.add_column("Taille", justify="right", style="yellow")
        detail.add_column("Âge", justify="right", style="dim")
        for item in sorted(report.items, key=lambda i: i.category):
            detail.add_row(
                item.category,
                str(item.path),
                format_size(item.size_bytes),
                format_age(item.mtime),
            )
        console.print(detail)


def render_result(report: CleanReport) -> None:
    """Affiche le résultat final.

    Args:
        report: Rapport final.
    """
    console.print()
    if report.mode == CleanMode.DRY_RUN:
        total = sum(i.size_bytes for i in report.items if not i.protected)
        console.print(
            Panel(
                f"[bold]DRY-RUN[/bold] — rien n'a été supprimé.\n"
                f"À supprimer : {len([i for i in report.items if not i.protected])} élément(s), "
                f"[yellow]{format_size(total)}[/yellow].\n"
                f"Relancer avec [cyan]--apply[/cyan] pour exécuter.",
                title="Aperçu",
                border_style="yellow",
            ),
        )
    else:
        console.print(
            Panel(
                f"[bold green]✓ Nettoyage terminé[/bold green]\n"
                f"Supprimés : {len(report.deleted)} élément(s)\n"
                f"Espace libéré : [yellow]{format_size(report.total_bytes_freed)}[/yellow]\n"
                f"Durée : {report.duration_seconds:.2f}s",
                title="Résultat",
                border_style="green",
            ),
        )

    if report.skipped:
        console.print(f"[dim]{len(report.skipped)} élément(s) protégé(s) ignoré(s).[/dim]")
    if report.errors:
        console.print(Panel("\n".join(f"✗ {e}" for e in report.errors), title="Erreurs", border_style="red"))


# ============================================================================
#  Orchestrateur
# ============================================================================


def run_clean(
    targets: list[CleanTarget],
    *,
    mode: CleanMode,
    older_than_days: float | None,
    confirm_user_data: bool,
    verbose: bool,
    force_yes: bool,
) -> CleanReport:
    """Point d'entrée principal du nettoyage.

    Args:
        targets: Cibles à nettoyer.
        mode: Dry-run ou apply.
        older_than_days: Ne supprime que les fichiers plus vieux.
        confirm_user_data: Nécessite confirmation pour les données user.
        verbose: Affiche le détail.
        force_yes: Skip la confirmation interactive.

    Returns:
        Rapport final.

    Raises:
        typer.Exit: Si l'utilisateur annule.
    """
    started = time.perf_counter()
    report = CleanReport(mode=mode, started_at=time.time())

    with console.status("[cyan]Scan des éléments à nettoyer…[/cyan]"):
        report.items = scan(targets, older_than_days=older_than_days)

    # --- Avertissement + confirmation pour données utilisateur ---
    user_items = [i for i in report.items if i.category == "user" and not i.protected]
    if user_items and mode == CleanMode.APPLY:
        total_user = sum(i.size_bytes for i in user_items)
        console.print()
        console.print(
            Panel(
                f"[bold red]⚠ ATTENTION[/bold red]\n\n"
                f"Cette opération va supprimer [bold]{len(user_items)}[/bold] élément(s) "
                f"de données utilisateur ([yellow]{format_size(total_user)}[/yellow]) :\n"
                f"  • cookies chiffrés (sessions perdues, re-login requis)\n"
                f"  • base de données de bibliothèque (progression de lecture perdue)\n"
                f"  • logs historiques\n"
                f"  • cache applicatif\n\n"
                f"[bold]Cette action est irréversible.[/bold]",
                title="⚠ Confirmation requise",
                border_style="red",
            ),
        )
        if not force_yes and not Confirm.ask("Continuer ?", default=False):
            console.print("[yellow]Annulé par l'utilisateur.[/yellow]")
            raise typer.Exit(code=0)

    # --- Preview ---
    render_preview(report, verbose=verbose)

    # --- Exécution ---
    if mode == CleanMode.APPLY:
        if not report.items or all(i.protected for i in report.items):
            console.print("[dim]Aucun élément à supprimer.[/dim]")
        else:
            with console.status("[cyan]Suppression en cours…[/cyan]"):
                execute(report)

    report.finished_at = time.time()
    report.duration_seconds = time.perf_counter() - started
    render_result(report)
    return report


# ============================================================================
#  CLI
# ============================================================================


def _parse_targets(targets: list[str]) -> list[CleanTarget]:
    """Convertit les strings CLI en `CleanTarget`.

    Args:
        targets: Liste de noms de cibles.

    Returns:
        Liste de `CleanTarget` dédupliquée.

    Raises:
        typer.BadParameter: Si une cible est inconnue.
    """
    parsed: list[CleanTarget] = []
    for t in targets:
        try:
            parsed.append(CleanTarget(t.lower()))
        except ValueError as exc:
            valid = ", ".join(c.value for c in CleanTarget)
            msg = f"Cible inconnue : '{t}'. Valides : {valid}"
            raise typer.BadParameter(msg) from exc
    return list(dict.fromkeys(parsed))  # dédup en gardant l'ordre


@app.command("run")
def cmd_run(
    targets: Annotated[
        list[str],
        typer.Argument(help="Cibles : caches, build, temp, user, all"),
    ],
    apply: Annotated[bool, typer.Option("--apply", help="Exécuter réellement (sinon dry-run)")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip la confirmation interactive")] = False,
    older_than: Annotated[
        float | None,
        typer.Option("--older-than", help="Ne supprime que les fichiers plus vieux (jours)"),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Afficher le détail par fichier")] = False,
    debug: Annotated[bool, typer.Option("--debug", help="Logs DEBUG")] = False,
) -> None:
    """Nettoie les cibles spécifiées.

    Exemples :

        python scripts/clean.py run caches
        python scripts/clean.py run build --apply
        python scripts/clean.py run all --apply -y
        python scripts/clean.py run temp --older-than 7 --apply
    """
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if debug else "WARNING", format="<level>{level: <8}</level> | {message}")

    if not targets:
        console.print("[red]Aucune cible fournie.[/red]")
        raise typer.Exit(code=2)

    parsed = _parse_targets(targets)

    # Sécurité : si l'utilisateur demande "all" seul, on n'inclut PAS user par défaut
    if CleanTarget.USER in parsed and CleanTarget.ALL in parsed:
        console.print("[red]Ne pas combiner 'user' et 'all' — utiliser 'user' explicitement.[/red]")
        raise typer.Exit(code=2)

    report = run_clean(
        parsed,
        mode=CleanMode.APPLY if apply else CleanMode.DRY_RUN,
        older_than_days=older_than,
        confirm_user_data=True,
        verbose=verbose,
        force_yes=yes,
    )

    if report.errors:
        raise typer.Exit(code=1)


@app.command("caches")
def cmd_caches(
    apply: Annotated[bool, typer.Option("--apply")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Nettoie les caches Python (raccourci)."""
    logger.remove()
    logger.add(sys.stderr, level="WARNING", format="<level>{level: <8}</level> | {message}")
    run_clean(
        [CleanTarget.CACHES],
        mode=CleanMode.APPLY if apply else CleanMode.DRY_RUN,
        older_than_days=None,
        confirm_user_data=False,
        verbose=verbose,
        force_yes=True,
    )


@app.command("build")
def cmd_build(
    apply: Annotated[bool, typer.Option("--apply")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Nettoie les artefacts de build (raccourci)."""
    logger.remove()
    logger.add(sys.stderr, level="WARNING", format="<level>{level: <8}</level> | {message}")
    run_clean(
        [CleanTarget.BUILD],
        mode=CleanMode.APPLY if apply else CleanMode.DRY_RUN,
        older_than_days=None,
        confirm_user_data=False,
        verbose=verbose,
        force_yes=True,
    )


@app.command("all")
def cmd_all(
    apply: Annotated[bool, typer.Option("--apply")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    include_user: Annotated[bool, typer.Option("--include-user", help="Inclure les données utilisateur")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Nettoie tout sauf les données utilisateur (par défaut)."""
    logger.remove()
    logger.add(sys.stderr, level="WARNING", format="<level>{level: <8}</level> | {message}")

    targets: list[CleanTarget] = [CleanTarget.CACHES, CleanTarget.BUILD, CleanTarget.TEMP]
    if include_user:
        targets.append(CleanTarget.USER)

    run_clean(
        targets,
        mode=CleanMode.APPLY if apply else CleanMode.DRY_RUN,
        older_than_days=None,
        confirm_user_data=include_user,
        verbose=verbose,
        force_yes=yes,
    )


# ============================================================================
#  Entrée
# ============================================================================


def main() -> None:
    """Point d'entrée CLI."""
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrompu.[/yellow]")
        raise typer.Exit(code=130) from None


if __name__ == "__main__":
    main()
