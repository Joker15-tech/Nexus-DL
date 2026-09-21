#!/usr/bin/env python3
"""Migration de configuration NexusDL entre versions de schéma.

Détecte la version actuelle de chaque fichier de config, détermine les
migrations nécessaires, et applique les transformations en chaîne. Backup
automatique avant toute modification.

Fichiers supportés :
    config/config.yaml                → Settings (core/config.py)
    config/sites_overrides.yaml       → SiteOverride (core/registry/validator.py)
    config/proxy.yaml                 → ProxyConfig (core/session/proxy_manager.py)
    config/logging.yaml               → LoggingConfig (core/logger.py)

Example:
    Détecter les migrations nécessaires ::

        python scripts/migrate_config.py check

    Prévisualiser les migrations (dry-run) ::

        python scripts/migrate_config.py migrate

    Appliquer ::

        python scripts/migrate_config.py migrate --apply

    Réinitialiser un fichier depuis l'exemple ::

        python scripts/migrate_config.py reset config.yaml --apply

    Lister l'historique des migrations ::

        python scripts/migrate_config.py history
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Callable, Final

# --- Résolution des chemins racine -----------------------------------------
_SCRIPT_DIR: Final[Path] = Path(__file__).resolve().parent
_ROOT_DIR: Final[Path] = _SCRIPT_DIR.parent
_CONFIG_DIR: Final[Path] = _ROOT_DIR / "config"
_SRC_DIR: Final[Path] = _ROOT_DIR / "src"

if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import typer  # noqa: E402
import yaml  # noqa: E402
from loguru import logger  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.prompt import Confirm  # noqa: E402
from rich.syntax import Syntax  # noqa: E402
from rich.table import Table  # noqa: E402

# ============================================================================
#  Constantes
# ============================================================================

console: Final[Console] = Console()
app: Final[typer.Typer] = typer.Typer(
    name="migrate-config",
    help="Migration de configuration NexusDL entre versions.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Version actuelle du schéma de configuration (à incrémenter lors d'un
# changement breaking dans core/config.py::Settings).
CURRENT_SCHEMA_VERSION: Final[int] = 2

# Mapping fichier → version minimale supportée
SUPPORTED_FILES: Final[dict[str, dict[str, Any]]] = {
    "config.yaml": {
        "path": _CONFIG_DIR / "config.yaml",
        "example": _CONFIG_DIR / "config.example.yaml",
        "min_version": 1,
        "required": True,
    },
    "sites_overrides.yaml": {
        "path": _CONFIG_DIR / "sites_overrides.yaml",
        "example": _CONFIG_DIR / "sites_overrides.example.yaml",
        "min_version": 1,
        "required": False,
    },
    "proxy.yaml": {
        "path": _CONFIG_DIR / "proxy.yaml",
        "example": _CONFIG_DIR / "proxy.example.yaml",
        "min_version": 1,
        "required": False,
    },
    "logging.yaml": {
        "path": _CONFIG_DIR / "logging.yaml",
        "example": _CONFIG_DIR / "logging.yaml",  # pas d'exemple séparé
        "min_version": 1,
        "required": False,
    },
}


class MigrationStatus(str, Enum):
    """Statut d'un fichier face aux migrations."""

    UP_TO_DATE = "up-to-date"
    NEEDS_MIGRATION = "needs-migration"
    MISSING = "missing"
    INVALID = "invalid"


@dataclass(slots=True)
class MigrationStep:
    """Une étape de migration d'une version N à N+1.

    Attributes:
        from_version: Version source.
        to_version: Version cible.
        description: Description courte (affichée dans le rapport).
        func: Fonction de transformation (config dict → config dict).
        reversible: Si True, une migration inverse existe.
        breaking: Si True, la migration peut perdre des données.
    """

    from_version: int
    to_version: int
    description: str
    func: Callable[[dict[str, Any]], dict[str, Any]]
    reversible: bool = True
    breaking: bool = False


@dataclass(slots=True)
class MigrationReport:
    """Rapport de migration pour un fichier.

    Attributes:
        filename: Nom du fichier.
        path: Chemin absolu.
        status: Statut détecté.
        current_version: Version détectée.
        target_version: Version cible.
        steps: Étapes de migration à appliquer.
        applied_steps: Étapes effectivement appliquées.
        warnings: Avertissements.
        errors: Erreurs.
        backup_path: Chemin du backup créé.
    """

    filename: str
    path: Path
    status: MigrationStatus
    current_version: int
    target_version: int
    steps: list[MigrationStep] = field(default_factory=list)
    applied_steps: list[MigrationStep] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    backup_path: Path | None = None


# ============================================================================
#  Registre des migrations
# ============================================================================
# Chaque entrée décrit une migration de config.yaml de la version N à N+1.
# Les migrations sont appliquées dans l'ordre croissant.
#
# Exemples de transformations courantes :
#   - Renommer une clé (rename_key)
#   - Déplacer une clé dans une sous-section (move_key)
#   - Changer le type (str → int, bool → Literal, etc.)
#   - Diviser une clé en plusieurs
#   - Supprimer une clé obsolète
# ============================================================================


def _migrate_v1_to_v2(config: dict[str, Any]) -> dict[str, Any]:
    """Migration config.yaml de v1 → v2.

    Changements :
        - `download.max_concurrent` → `download.max_concurrent_tasks` (renommage)
        - `download.max_pages` → `download.max_concurrent_pages` (renommage)
        - `network.http2_enabled` → `network.http2` (renommage)
        - `session.cloudflare_bypass` → `session.cloudflare.strategy` (déplacement)
        - `logging.json` → `logging.format: json` (changement de type)

    Args:
        config: Configuration v1.

    Returns:
        Configuration v2.
    """
    result = _deep_copy(config)

    # --- download.max_concurrent → download.max_concurrent_tasks ---
    download = result.get("download")
    if isinstance(download, dict):
        if "max_concurrent" in download:
            download["max_concurrent_tasks"] = download.pop("max_concurrent")
        if "max_pages" in download:
            download["max_concurrent_pages"] = download.pop("max_pages")

    # --- network.http2_enabled → network.http2 ---
    network = result.get("network")
    if isinstance(network, dict) and "http2_enabled" in network:
        network["http2"] = network.pop("http2_enabled")

    # --- session.cloudflare_bypass → session.cloudflare.strategy ---
    session = result.get("session")
    if isinstance(session, dict):
        bypass = session.pop("cloudflare_bypass", None)
        if bypass is not None:
            cloudflare = session.setdefault("cloudflare", {})
            if isinstance(cloudflare, dict):
                cloudflare["strategy"] = "playwright" if bypass else "auto"

    # --- logging.json: true → logging.format: json ---
    logging_section = result.get("logging")
    if isinstance(logging_section, dict):
        json_flag = logging_section.pop("json", None)
        if json_flag is True:
            logging_section["format"] = "json"
        elif json_flag is False:
            logging_section.setdefault("format", "text")

    result["config_version"] = 2
    return result


# Registre des migrations par fichier.
# Les clés sont les noms de fichiers, les valeurs une liste de MigrationStep.
MIGRATIONS: Final[dict[str, list[MigrationStep]]] = {
    "config.yaml": [
        MigrationStep(
            from_version=1,
            to_version=2,
            description=(
                "Renommages : max_concurrent→max_concurrent_tasks, max_pages→max_concurrent_pages, "
                "http2_enabled→http2 ; déplacement cloudflare_bypass→cloudflare.strategy ; "
                "changement logging.json→logging.format"
            ),
            func=_migrate_v1_to_v2,
            reversible=False,
            breaking=False,
        ),
    ],
    "sites_overrides.yaml": [],
    "proxy.yaml": [],
    "logging.yaml": [],
}


# ============================================================================
#  Utilitaires
# ============================================================================


def _deep_copy(obj: Any) -> Any:
    """Copie profonde via JSON (suffisant pour YAML sérialisable).

    Args:
        obj: Objet à copier.

    Returns:
        Copie indépendante.
    """
    return json.loads(json.dumps(obj, default=str))


def _load_yaml(path: Path) -> dict[str, Any]:
    """Charge un fichier YAML.

    Args:
        path: Chemin du fichier.

    Returns:
        Contenu parsé.

    Raises:
        yaml.YAMLError: Si le fichier est malformé.
        OSError: Si le fichier est illisible.
    """
    content = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)
    return parsed if isinstance(parsed, dict) else {}


def _dump_yaml(data: dict[str, Any], path: Path) -> None:
    """Écrit un dict en YAML en préservant l'ordre des clés.

    Args:
        data: Données à écrire.
        path: Chemin de destination.
    """
    content = yaml.safe_dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        indent=2,
    )
    path.write_text(content, encoding="utf-8")


def _detect_version(config: dict[str, Any], *, default: int = 1) -> int:
    """Détecte la version du schéma d'une config.

    Si `config_version` est absent, utilise la version par défaut (1).

    Args:
        config: Configuration parsée.
        default: Version par défaut si absente.

    Returns:
        Version détectée (entier positif).
    """
    version = config.get("config_version")
    if isinstance(version, int) and version >= 1:
        return version
    return default


def _make_backup(path: Path) -> Path:
    """Crée un backup horodaté d'un fichier.

    Args:
        path: Fichier à sauvegarder.

    Returns:
        Chemin du backup.
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(f"{path.suffix}.bak.{ts}")
    shutil.copy2(path, backup)
    return backup


def _diff_dicts(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Calcule un diff lisible entre deux dicts.

    Args:
        before: Dict avant migration.
        after: Dict après migration.

    Returns:
        Liste de lignes de diff (format simple ``+``/``-``/``~``).
    """
    lines: list[str] = []
    _diff_recursive(before, after, "", lines)
    return lines


def _diff_recursive(
    before: Any,
    after: Any,
    prefix: str,
    out: list[str],
) -> None:
    """Diff récursif interne.

    Args:
        before: Valeur avant.
        after: Valeur après.
        prefix: Préfixe de chemin (pour l'affichage).
        out: Liste accumulée.
    """
    if isinstance(before, dict) and isinstance(after, dict):
        keys_before = set(before.keys())
        keys_after = set(after.keys())
        for key in sorted(keys_before - keys_after):
            out.append(f"[red]−[/red] {prefix}{key}: {before[key]!r}")
        for key in sorted(keys_after - keys_before):
            out.append(f"[green]+[/green] {prefix}{key}: {after[key]!r}")
        for key in sorted(keys_before & keys_after):
            _diff_recursive(before[key], after[key], f"{prefix}{key}.", out)
    elif before != after:
        out.append(f"[yellow]~[/yellow] {prefix.rstrip('.')}: {before!r} → {after!r}")


# ============================================================================
#  Planification des migrations
# ============================================================================


def plan_migration(filename: str, path: Path) -> MigrationReport:
    """Analyse un fichier et détermine les migrations à appliquer.

    Args:
        filename: Nom logique du fichier.
        path: Chemin absolu.

    Returns:
        Rapport de planification (sans application).
    """
    report = MigrationReport(
        filename=filename,
        path=path,
        status=MigrationStatus.UP_TO_DATE,
        current_version=CURRENT_SCHEMA_VERSION,
        target_version=CURRENT_SCHEMA_VERSION,
    )

    if not path.exists():
        report.status = MigrationStatus.MISSING
        return report

    try:
        config = _load_yaml(path)
    except (yaml.YAMLError, OSError) as exc:
        report.status = MigrationStatus.INVALID
        report.errors.append(f"Lecture échouée : {exc}")
        return report

    current_version = _detect_version(config)
    report.current_version = current_version

    if current_version >= CURRENT_SCHEMA_VERSION:
        report.status = MigrationStatus.UP_TO_DATE
        return report

    # Récupère les steps applicables
    steps = MIGRATIONS.get(filename, [])
    applicable = [s for s in steps if s.from_version >= current_version]
    applicable.sort(key=lambda s: s.from_version)

    if not applicable:
        report.warnings.append(
            f"Version {current_version} détectée mais aucune migration connue vers {CURRENT_SCHEMA_VERSION}.",
        )
        return report

    report.steps = applicable
    report.status = MigrationStatus.NEEDS_MIGRATION
    return report


def apply_migration(
    report: MigrationReport,
    *,
    dry_run: bool,
    backup: bool,
) -> None:
    """Applique les migrations planifiées à un fichier.

    Args:
        report: Rapport de planification (modifié en place).
        dry_run: Si True, n'écrit rien.
        backup: Si True, crée un backup avant modification.

    Raises:
        RuntimeError: Si une migration échoue.
    """
    if report.status != MigrationStatus.NEEDS_MIGRATION:
        return

    try:
        config = _load_yaml(report.path)
    except (yaml.YAMLError, OSError) as exc:
        report.errors.append(f"Relecture échouée : {exc}")
        return

    original = _deep_copy(config)

    for step in report.steps:
        try:
            config = step.func(config)
            report.applied_steps.append(step)
            logger.debug("Applied {} → {} for {}", step.from_version, step.to_version, report.filename)
        except Exception as exc:  # noqa: BLE001
            msg = f"Migration {step.from_version}→{step.to_version} échouée : {exc}"
            report.errors.append(msg)
            raise RuntimeError(msg) from exc

    # Diff pour l'affichage
    diff = _diff_dicts(original, config)
    if diff:
        console.print()
        console.print(Panel("[bold]Diff :[/bold]", border_style="cyan"))
        for line in diff:
            console.print(f"  {line}")

    if dry_run:
        return

    # Backup
    if backup:
        report.backup_path = _make_backup(report.path)
        logger.debug("Backup créé : {}", report.backup_path)

    _dump_yaml(config, report.path)
    report.current_version = report.target_version


# ============================================================================
#  Validation post-migration
# ============================================================================


def validate_config(filename: str, path: Path) -> list[str]:
    """Valide un fichier de config après migration.

    Tente de charger la config via les modèles Pydantic correspondants.

    Args:
        filename: Nom logique du fichier.
        path: Chemin absolu.

    Returns:
        Liste d'erreurs (vide si valide).
    """
    errors: list[str] = []

    if not path.exists():
        return errors

    try:
        _load_yaml(path)
    except (yaml.YAMLError, OSError) as exc:
        errors.append(f"YAML invalide : {exc}")
        return errors

    if filename == "config.yaml":
        try:
            from nexusdl.core.config import Settings  # noqa: PLC0415

            # Charge depuis le fichier en injectant l'override
            import os  # noqa: PLC0415

            prev = os.environ.get("NEXUSDL_CONFIG_FILE")
            os.environ["NEXUSDL_CONFIG_FILE"] = str(path)
            try:
                Settings()  # type: ignore[call-arg]
            finally:
                if prev is None:
                    os.environ.pop("NEXUSDL_CONFIG_FILE", None)
                else:
                    os.environ["NEXUSDL_CONFIG_FILE"] = prev
        except ImportError:
            errors.append("Module core.config indisponible — validation sautée")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Validation Pydantic échouée : {type(exc).__name__}: {exc}")

    return errors


# ============================================================================
#  Rendu
# ============================================================================


def render_plan_table(reports: list[MigrationReport], *, verbose: bool = False) -> None:
    """Affiche le tableau de planification.

    Args:
        reports: Liste des rapports de planification.
        verbose: Affiche le détail des steps.
    """
    table = Table(title="Plan de migration", header_style="bold cyan")
    table.add_column("Fichier", style="cyan")
    table.add_column("Version", justify="center")
    table.add_column("Statut", justify="center")
    table.add_column("Steps", justify="right")

    status_display = {
        MigrationStatus.UP_TO_DATE: "[green]✓ à jour[/green]",
        MigrationStatus.NEEDS_MIGRATION: "[yellow]⚠ à migrer[/yellow]",
        MigrationStatus.MISSING: "[dim]· absent[/dim]",
        MigrationStatus.INVALID: "[red]✗ invalide[/red]",
    }

    for r in reports:
        version_str = (
            f"{r.current_version} → {r.target_version}"
            if r.current_version != r.target_version
            else str(r.current_version)
        )
        table.add_row(
            r.filename,
            version_str,
            status_display[r.status],
            str(len(r.steps)),
        )

    console.print(table)

    if verbose:
        for r in reports:
            if r.steps:
                console.print(f"\n[bold cyan]{r.filename}[/bold cyan] :")
                for step in r.steps:
                    marker = "[red]![/red]" if step.breaking else "[green]✓[/green]"
                    console.print(f"  {marker} v{step.from_version}→v{step.to_version} : {step.description}")


def render_summary(reports: list[MigrationReport], *, dry_run: bool) -> None:
    """Affiche le résumé final.

    Args:
        reports: Rapports de migration.
        dry_run: Indique si on est en dry-run.
    """
    console.print()
    to_migrate = [r for r in reports if r.status == MigrationStatus.NEEDS_MIGRATION]
    invalid = [r for r in reports if r.status == MigrationStatus.INVALID]
    migrated = [r for r in reports if r.applied_steps]

    if not to_migrate and not invalid:
        console.print(Panel("[bold green]✓ Toutes les configs sont à jour.[/bold green]", border_style="green"))
        return

    if invalid:
        console.print(
            Panel(
                "\n".join(f"✗ {r.filename} : {'; '.join(r.errors)}" for r in invalid),
                title="Configs invalides",
                border_style="red",
            ),
        )

    if dry_run and to_migrate:
        console.print(
            Panel(
                f"[bold yellow]DRY-RUN[/bold yellow] — {len(to_migrate)} fichier(s) à migrer.\n"
                f"Relancer avec [cyan]--apply[/cyan] pour exécuter.",
                border_style="yellow",
            ),
        )
    elif migrated:
        backups = [r for r in migrated if r.backup_path]
        console.print(
            Panel(
                f"[bold green]✓ {len(migrated)} fichier(s) migré(s).[/bold green]\n"
                f"Backups : {len(backups)} créé(s).",
                border_style="green",
            ),
        )


# ============================================================================
#  CLI
# ============================================================================


@app.command("check")
def cmd_check(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Détecte les migrations nécessaires sans rien modifier."""
    reports = [
        plan_migration(name, info["path"])
        for name, info in SUPPORTED_FILES.items()
    ]
    render_plan_table(reports, verbose=verbose)

    needs = sum(1 for r in reports if r.status == MigrationStatus.NEEDS_MIGRATION)
    if needs:
        raise typer.Exit(code=1)  # CI : migration requise


@app.command("migrate")
def cmd_migrate(
    apply: Annotated[bool, typer.Option("--apply", help="Écrire les changements (sinon dry-run)")] = False,
    backup: Annotated[bool, typer.Option("--backup/--no-backup", help="Créer un backup")] = True,
    file: Annotated[str | None, typer.Option("--file", "-f", help="Migrer un seul fichier")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Applique les migrations de configuration."""
    if file and file not in SUPPORTED_FILES:
        console.print(f"[red]Fichier inconnu : {file}[/red]")
        console.print(f"[dim]Valides : {', '.join(SUPPORTED_FILES)}[/dim]")
        raise typer.Exit(code=2)

    targets = {file: SUPPORTED_FILES[file]} if file else SUPPORTED_FILES

    reports = [
        plan_migration(name, info["path"])
        for name, info in targets.items()
    ]
    render_plan_table(reports, verbose=verbose)

    # Avertissement si migration breaking
    breaking = [
        step
        for r in reports
        for step in r.steps
        if step.breaking
    ]
    if breaking and apply and not Confirm.ask(
        f"[yellow]⚠ {len(breaking)} migration(s) peuvent perdre des données. Continuer ?[/yellow]",
        default=False,
    ):
        console.print("[yellow]Annulé.[/yellow]")
        raise typer.Exit(code=0)

    # Application
    for r in reports:
        if r.status != MigrationStatus.NEEDS_MIGRATION:
            continue
        try:
            apply_migration(r, dry_run=not apply, backup=backup)
        except RuntimeError:
            continue  # Erreur déjà dans r.errors

    # Validation post-migration
    if apply:
        for r in reports:
            if not r.applied_steps:
                continue
            errors = validate_config(r.filename, r.path)
            if errors:
                r.errors.extend(errors)
                # Restaure le backup si la validation échoue
                if r.backup_path and r.backup_path.exists():
                    shutil.copy2(r.backup_path, r.path)
                    console.print(f"[red]Validation échouée — restauration depuis {r.backup_path.name}[/red]")

    render_summary(reports, dry_run=not apply)

    if any(r.errors for r in reports):
        raise typer.Exit(code=1)


@app.command("reset")
def cmd_reset(
    filename: Annotated[str, typer.Argument(help="Fichier à réinitialiser (ex: config.yaml)")],
    apply: Annotated[bool, typer.Option("--apply")] = False,
    keep_backup: Annotated[bool, typer.Option("--keep-backup/--no-keep-backup")] = True,
) -> None:
    """Réinitialise un fichier de config depuis l'exemple."""
    if filename not in SUPPORTED_FILES:
        console.print(f"[red]Fichier inconnu : {filename}[/red]")
        raise typer.Exit(code=2)

    info = SUPPORTED_FILES[filename]
    target: Path = info["path"]
    example: Path = info["example"]

    if not example.exists():
        console.print(f"[red]Fichier d'exemple introuvable : {example}[/red]")
        raise typer.Exit(code=2)

    if not target.exists():
        console.print(f"[yellow]{target} n'existe pas — copie directe.[/yellow]")
        if not apply:
            console.print("[dim]DRY-RUN — utiliser --apply.[/dim]")
            return
        shutil.copy2(example, target)
        console.print(f"[green]✓ {filename} créé depuis l'exemple.[/green]")
        return

    if not apply:
        console.print(
            Panel(
                f"[bold yellow]DRY-RUN[/bold yellow] — {filename} serait écrasé par l'exemple.\n"
                f"Backup : {'oui' if keep_backup else 'non'}\n"
                f"Utiliser [cyan]--apply[/cyan] pour exécuter.",
                border_style="yellow",
            ),
        )
        return

    if not Confirm.ask(
        f"[red]⚠ Écraser {filename} par l'exemple ? Les personnalisations seront perdues.[/red]",
        default=False,
    ):
        console.print("[yellow]Annulé.[/yellow]")
        return

    if keep_backup:
        backup = _make_backup(target)
        console.print(f"[dim]Backup : {backup}[/dim]")

    shutil.copy2(example, target)
    console.print(f"[green]✓ {filename} réinitialisé depuis l'exemple.[/green]")


@app.command("history")
def cmd_history() -> None:
    """Liste l'historique des migrations disponibles."""
    table = Table(title="Historique des migrations", header_style="bold cyan")
    table.add_column("Fichier", style="cyan")
    table.add_column("Version", justify="center")
    table.add_column("Description", style="dim", overflow="fold")
    table.add_column("Breaking", justify="center")

    for filename, steps in MIGRATIONS.items():
        if not steps:
            table.add_row(filename, "—", "[dim]aucune migration[/dim]", "·")
            continue
        for step in steps:
            table.add_row(
                filename,
                f"v{step.from_version} → v{step.to_version}",
                step.description,
                "[red]⚠[/red]" if step.breaking else "·",
            )

    console.print(table)
    console.print(f"\n[dim]Version de schéma actuelle : [bold]{CURRENT_SCHEMA_VERSION}[/bold][/dim]")


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


if __name__ == "__main__":
    main()
