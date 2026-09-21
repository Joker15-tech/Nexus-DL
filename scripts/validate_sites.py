#!/usr/bin/env python3
"""Validation de la configuration de sites et parsers pour NexusDL.

Sept validations indépendantes :

    schema            → JSON Schema de sites.yaml (sites_schema.json)
    sites             → cohérence interne (IDs, domaines, capabilities)
    parsers           → import dynamique de chaque parser_class référencé
    orphans           → fichiers parser non référencés dans sites.yaml
    duplicates        → IDs et domaines dupliqués entre sites
    config-sync       → config.example.yaml ↔ core/config.py::Settings
    logging-profiles  → profils development/testing/staging/production

Example:
    Valider tout ::

        python scripts/validate_sites.py all

    Valider uniquement les parsers importables ::

        python scripts/validate_sites.py parsers

    CI : mode strict (warnings traités comme erreurs) ::

        python scripts/validate_sites.py all --strict

    JSON pour intégration CI ::

        python scripts/validate_sites.py all --format json
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Final, Iterable

# --- Résolution des chemins racine -----------------------------------------
_SCRIPT_DIR: Final[Path] = Path(__file__).resolve().parent
_ROOT_DIR: Final[Path] = _SCRIPT_DIR.parent
_SRC_DIR: Final[Path] = _ROOT_DIR / "src"
_PARSERS_DIR: Final[Path] = _SRC_DIR / "nexusdl" / "parsers"
_REGISTRY_DIR: Final[Path] = _SRC_DIR / "nexusdl" / "core" / "registry"
_CONFIG_DIR: Final[Path] = _ROOT_DIR / "config"
_SITES_YAML: Final[Path] = _REGISTRY_DIR / "sites.yaml"
_SITES_SCHEMA: Final[Path] = _REGISTRY_DIR / "sites_schema.json"
_CONFIG_EXAMPLE: Final[Path] = _CONFIG_DIR / "config.example.yaml"
_LOGGING_YAML: Final[Path] = _CONFIG_DIR / "logging.yaml"
_OVERRIDES_EXAMPLE: Final[Path] = _CONFIG_DIR / "sites_overrides.example.yaml"

if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import typer  # noqa: E402
import yaml  # noqa: E402
from loguru import logger  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402

# ============================================================================
#  Constantes
# ============================================================================

console: Final[Console] = Console()
app: Final[typer.Typer] = typer.Typer(
    name="validate-sites",
    help="Validation de la configuration NexusDL.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

REQUIRED_SITE_FIELDS: Final[frozenset[str]] = frozenset(
    {"id", "name", "domains", "parser_class", "language", "capabilities"},
)

REQUIRED_CAPABILITY_FIELDS: Final[frozenset[str]] = frozenset(
    {"supports_search", "supports_manga_info", "supports_chapters", "supports_pages"},
)

VALID_LANGUAGES: Final[frozenset[str]] = frozenset(
    {"fr", "en", "es", "de", "it", "pt", "ja", "ko", "zh", "adult"},
)

# Profils obligatoires dans logging.yaml (Phase 0)
REQUIRED_LOGGING_PROFILES: Final[frozenset[str]] = frozenset(
    {"development", "testing", "staging", "production"},
)

# Regex pour valider un site_id
SITE_ID_REGEX: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{2,40}$")

# Regex pour valider un parser_class ("module.path:ClassName")
PARSER_CLASS_REGEX: Final[re.Pattern[str]] = re.compile(
    r"^[a-zA-Z_][\w.]*:[A-Z][a-zA-Z0-9_]*$",
)

# Regex pour valider une URL
URL_REGEX: Final[re.Pattern[str]] = re.compile(r"^https?://[\w.-]+(:\d+)?(/.*)?$")


class Severity(str, Enum):
    """Sévérité d'un problème."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ValidationKind(str, Enum):
    """Type de validation."""

    SCHEMA = "schema"
    SITES = "sites"
    PARSERS = "parsers"
    ORPHANS = "orphans"
    DUPLICATES = "duplicates"
    CONFIG_SYNC = "config-sync"
    LOGGING_PROFILES = "logging-profiles"


@dataclass(slots=True)
class Issue:
    """Un problème détecté.

    Attributes:
        severity: Niveau de gravité.
        kind: Type de validation.
        location: Localisation (fichier, ligne, clé).
        message: Description du problème.
        hint: Suggestion de correction optionnelle.
    """

    severity: Severity
    kind: ValidationKind
    location: str
    message: str
    hint: str | None = None


@dataclass(slots=True)
class ValidationReport:
    """Rapport de validation global.

    Attributes:
        issues: Liste des problèmes détectés.
        checked_sites: Nombre de sites vérifiés.
        checked_parsers: Nombre de parsers importés.
        checked_files: Nombre de fichiers validés.
        duration_seconds: Durée d'exécution.
    """

    issues: list[Issue] = field(default_factory=list)
    checked_sites: int = 0
    checked_parsers: int = 0
    checked_files: int = 0
    duration_seconds: float = 0.0

    @property
    def errors(self) -> int:
        """Nombre d'erreurs."""
        return sum(1 for i in self.issues if i.severity == Severity.ERROR)

    @property
    def warnings(self) -> int:
        """Nombre d'avertissements."""
        return sum(1 for i in self.issues if i.severity == Severity.WARNING)


# ============================================================================
#  Utilitaires
# ============================================================================


def _add(
    report: ValidationReport,
    severity: Severity,
    kind: ValidationKind,
    location: str,
    message: str,
    hint: str | None = None,
) -> None:
    """Ajoute un problème au rapport.

    Args:
        report: Rapport cible.
        severity: Sévérité.
        kind: Type de validation.
        location: Localisation.
        message: Message.
        hint: Suggestion optionnelle.
    """
    report.issues.append(
        Issue(severity=severity, kind=kind, location=location, message=message, hint=hint),
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    """Charge un YAML en dict.

    Args:
        path: Chemin du fichier.

    Returns:
        Contenu parsé.

    Raises:
        FileNotFoundError: Si le fichier n'existe pas.
        yaml.YAMLError: Si le YAML est invalide.
    """
    if not path.exists():
        msg = f"Fichier introuvable : {path}"
        raise FileNotFoundError(msg)
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _iter_sites(sites_yaml: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    """Itère sur les sites d'un sites.yaml.

    Args:
        sites_yaml: Contenu parsé.

    Yields:
        Tuple (site_id, config).
    """
    sites = sites_yaml.get("sites", {})
    if isinstance(sites, dict):
        for site_id, config in sites.items():
            if isinstance(config, dict):
                yield site_id, config


def _resolve_parser_module(parser_class: str) -> str:
    """Extrait le chemin du module depuis un ``parser_class``.

    Args:
        parser_class: Chaîne au format "module.path:ClassName".

    Returns:
        Le chemin du module (avant les deux-points).
    """
    return parser_class.split(":", 1)[0]


# ============================================================================
#  Validation — Schema
# ============================================================================


def validate_schema(report: ValidationReport) -> None:
    """Valide sites.yaml contre sites_schema.json.

    Args:
        report: Rapport à peupler.
    """
    report.checked_files += 1
    kind = ValidationKind.SCHEMA

    if not _SITES_YAML.exists():
        _add(report, Severity.ERROR, kind, str(_SITES_YAML), "Fichier sites.yaml introuvable")
        return

    if not _SITES_SCHEMA.exists():
        _add(
            report,
            Severity.WARNING,
            kind,
            str(_SITES_SCHEMA),
            "sites_schema.json absent — validation JSON Schema sautée",
            hint="Créer le schéma ou désactiver cette validation.",
        )
        return

    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:
        _add(
            report,
            Severity.WARNING,
            kind,
            "jsonschema",
            "Module jsonschema absent — validation sautée",
            hint="Installer : `uv pip install -e '.[dev]'`",
        )
        return

    try:
        data = _load_yaml(_SITES_YAML)
        schema = json.loads(_SITES_SCHEMA.read_text(encoding="utf-8"))
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        _add(report, Severity.ERROR, kind, str(_SITES_YAML), f"Parsing échoué : {exc}")
        return

    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        path = ".".join(str(p) for p in exc.absolute_path) or "<racine>"
        _add(
            report,
            Severity.ERROR,
            kind,
            f"{_SITES_YAML.name}:{path}",
            f"Schéma invalide : {exc.message}",
        )


# ============================================================================
#  Validation — Sites (cohérence interne)
# ============================================================================


def validate_sites(report: ValidationReport) -> None:
    """Valide la cohérence interne de chaque site.

    Vérifie :
        - Champs requis présents
        - site_id valide (regex)
        - domaines valides (URL)
        - language valide
        - capabilities complètes
        - parser_class au bon format

    Args:
        report: Rapport à peupler.
    """
    report.checked_files += 1
    kind = ValidationKind.SITES

    if not _SITES_YAML.exists():
        _add(report, Severity.ERROR, kind, str(_SITES_YAML), "sites.yaml introuvable")
        return

    try:
        data = _load_yaml(_SITES_YAML)
    except yaml.YAMLError as exc:
        _add(report, Severity.ERROR, kind, str(_SITES_YAML), f"YAML invalide : {exc}")
        return

    for site_id, config in _iter_sites(data):
        report.checked_sites += 1

        # --- Champs requis ---
        missing = REQUIRED_SITE_FIELDS - set(config.keys())
        if missing:
            _add(
                report,
                Severity.ERROR,
                kind,
                f"sites.{site_id}",
                f"Champs manquants : {sorted(missing)}",
            )
            continue

        # --- site_id valide ---
        if not SITE_ID_REGEX.match(site_id):
            _add(
                report,
                Severity.ERROR,
                kind,
                f"sites.{site_id}",
                f"site_id invalide (regex : {SITE_ID_REGEX.pattern})",
            )

        # --- ID interne cohérent ---
        if config.get("id") != site_id:
            _add(
                report,
                Severity.WARNING,
                kind,
                f"sites.{site_id}.id",
                f"id='{config.get('id')}' diffère de la clé '{site_id}'",
                hint="Aligner les deux valeurs.",
            )

        # --- Domaines ---
        domains = config.get("domains", [])
        if not isinstance(domains, list) or not domains:
            _add(report, Severity.ERROR, kind, f"sites.{site_id}.domains", "Liste de domaines vide ou invalide")
        else:
            for d in domains:
                if not isinstance(d, str) or not URL_REGEX.match(d):
                    _add(
                        report,
                        Severity.ERROR,
                        kind,
                        f"sites.{site_id}.domains",
                        f"URL invalide : {d!r}",
                        hint="Format attendu : https://example.com",
                    )

        # --- Langue ---
        lang = config.get("language")
        if lang not in VALID_LANGUAGES:
            _add(
                report,
                Severity.ERROR,
                kind,
                f"sites.{site_id}.language",
                f"Langue invalide : {lang!r}. Valides : {sorted(VALID_LANGUAGES)}",
            )

        # --- parser_class ---
        parser_class = config.get("parser_class", "")
        if not isinstance(parser_class, str) or not PARSER_CLASS_REGEX.match(parser_class):
            _add(
                report,
                Severity.ERROR,
                kind,
                f"sites.{site_id}.parser_class",
                f"parser_class invalide : {parser_class!r}",
                hint="Format attendu : 'module.path:ClassName'",
            )

        # --- capabilities ---
        caps = config.get("capabilities", {})
        if not isinstance(caps, dict):
            _add(report, Severity.ERROR, kind, f"sites.{site_id}.capabilities", "Doit être un dict")
        else:
            missing_caps = REQUIRED_CAPABILITY_FIELDS - set(caps.keys())
            if missing_caps:
                _add(
                    report,
                    Severity.WARNING,
                    kind,
                    f"sites.{site_id}.capabilities",
                    f"Capabilities manquantes : {sorted(missing_caps)} (défauts appliqués)",
                )


# ============================================================================
#  Validation — Parsers (import dynamique)
# ============================================================================


def validate_parsers(report: ValidationReport) -> None:
    """Importe dynamiquement chaque parser référencé.

    Vérifie :
        - Le module est importable
        - La classe existe dans le module
        - La classe hérite de ``BaseParser``
        - Les ClassVars ``site_id`` et ``language`` existent

    Args:
        report: Rapport à peupler.
    """
    report.checked_files += 1
    kind = ValidationKind.PARSERS

    if not _SITES_YAML.exists():
        _add(report, Severity.ERROR, kind, str(_SITES_YAML), "sites.yaml introuvable")
        return

    try:
        data = _load_yaml(_SITES_YAML)
    except yaml.YAMLError as exc:
        _add(report, Severity.ERROR, kind, str(_SITES_YAML), f"YAML invalide : {exc}")
        return

    # Import de BaseParser — s'il échoue, tout le reste est foutu
    try:
        from nexusdl.parsers.base import BaseParser  # noqa: PLC0415
    except ImportError as exc:
        _add(
            report,
            Severity.ERROR,
            kind,
            "nexusdl.parsers.base",
            f"BaseParser introuvable : {exc}",
            hint="Vérifier que src/nexusdl/parsers/base.py existe et est importable.",
        )
        return

    importlib.invalidate_caches()

    for site_id, config in _iter_sites(data):
        parser_class = config.get("parser_class")
        if not isinstance(parser_class, str) or ":" not in parser_class:
            continue  # déjà signalé par validate_sites

        module_path, class_name = parser_class.split(":", 1)
        report.checked_parsers += 1

        # --- Import module ---
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            _add(
                report,
                Severity.ERROR,
                kind,
                f"sites.{site_id}.parser_class",
                f"Import échoué : {module_path} ({exc})",
            )
            continue
        except Exception as exc:  # noqa: BLE001
            _add(
                report,
                Severity.ERROR,
                kind,
                f"sites.{site_id}.parser_class",
                f"Erreur à l'import de {module_path} : {type(exc).__name__}: {exc}",
            )
            continue

        # --- Classe présente ---
        if not hasattr(module, class_name):
            _add(
                report,
                Severity.ERROR,
                kind,
                f"sites.{site_id}.parser_class",
                f"Classe {class_name} absente de {module_path}",
            )
            continue

        cls = getattr(module, class_name)

        # --- Héritage de BaseParser ---
        if not (isinstance(cls, type) and issubclass(cls, BaseParser)):
            _add(
                report,
                Severity.ERROR,
                kind,
                f"sites.{site_id}.parser_class",
                f"{class_name} n'hérite pas de BaseParser",
            )
            continue

        # --- ClassVars ---
        if not hasattr(cls, "site_id"):
            _add(report, Severity.ERROR, kind, f"{class_name}.site_id", "ClassVar site_id manquant")
        elif getattr(cls, "site_id") != site_id:
            _add(
                report,
                Severity.WARNING,
                kind,
                f"{class_name}.site_id",
                f"site_id='{getattr(cls, 'site_id')}' diffère de la clé '{site_id}'",
            )

        if not hasattr(cls, "language"):
            _add(report, Severity.ERROR, kind, f"{class_name}.language", "ClassVar language manquant")


# ============================================================================
#  Validation — Orphelins (parsers non référencés)
# ============================================================================


def validate_orphans(report: ValidationReport) -> None:
    """Détecte les fichiers parser non référencés dans sites.yaml.

    Args:
        report: Rapport à peupler.
    """
    report.checked_files += 1
    kind = ValidationKind.ORPHANS

    if not _PARSERS_DIR.exists():
        return

    try:
        data = _load_yaml(_SITES_YAML)
    except (FileNotFoundError, yaml.YAMLError):
        return  # déjà signalé

    # Liste des modules référencés
    referenced: set[str] = set()
    for _, config in _iter_sites(data):
        pc = config.get("parser_class", "")
        if isinstance(pc, str) and ":" in pc:
            referenced.add(pc.split(":", 1)[0])

    # Parcours des fichiers parser
    for subdir in _PARSERS_DIR.iterdir():
        if not subdir.is_dir() or subdir.name.startswith("_") or subdir.name == "__pycache__":
            continue
        if subdir.name in {"mixins", "custom"}:
            continue  # pas des parsers directs

        for py_file in subdir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            module = f"nexusdl.parsers.{subdir.name}.{py_file.stem}"
            if module not in referenced:
                _add(
                    report,
                    Severity.WARNING,
                    kind,
                    str(py_file.relative_to(_ROOT_DIR)),
                    f"Parser '{module}' non référencé dans sites.yaml",
                    hint="Soit l'ajouter à sites.yaml, soit supprimer le fichier.",
                )


# ============================================================================
#  Validation — Doublons
# ============================================================================


def validate_duplicates(report: ValidationReport) -> None:
    """Détecte les doublons d'IDs et de domaines entre sites.

    Args:
        report: Rapport à peupler.
    """
    report.checked_files += 1
    kind = ValidationKind.DUPLICATES

    try:
        data = _load_yaml(_SITES_YAML)
    except (FileNotFoundError, yaml.YAMLError):
        return

    # --- IDs dupliqués (ne devrait pas arriver — YAML dict) ---
    # YAML écrase silencieusement les clés dupliquées, donc on parse en
    # mode "raw" pour détecter. Approche simple : re-parse avec un hook.

    def _detect_dup_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        for k, v in pairs:
            if k in seen:
                _add(
                    report,
                    Severity.ERROR,
                    kind,
                    "sites.yaml",
                    f"Clé dupliquée : '{k}' (l'une sera silencieusement écrasée)",
                )
            seen[k] = v
        return seen

    try:
        _ = yaml.load(_SITES_YAML.read_text(encoding="utf-8"), Loader=yaml.SafeLoader)  # noqa: S506
    except yaml.YAMLError:
        pass

    # --- Domaines dupliqués ---
    domain_to_sites: dict[str, list[str]] = {}
    for site_id, config in _iter_sites(data):
        for domain in config.get("domains", []):
            if not isinstance(domain, str):
                continue
            # Normalise : retire scheme et trailing slash pour la comparaison
            normalized = re.sub(r"^https?://", "", domain).rstrip("/").lower()
            domain_to_sites.setdefault(normalized, []).append(site_id)

    for domain, sites in domain_to_sites.items():
        if len(sites) > 1:
            _add(
                report,
                Severity.WARNING,
                kind,
                "sites.yaml",
                f"Domaine '{domain}' partagé par : {sorted(sites)}",
                hint="Vérifier que ce n'est pas une erreur de copier-coller.",
            )

    # --- parser_class dupliqué (plusieurs sites avec le même parser, OK si `_extends` non utilisé) ---
    parser_to_sites: dict[str, list[str]] = {}
    for site_id, config in _iter_sites(data):
        pc = config.get("parser_class", "")
        if isinstance(pc, str):
            parser_to_sites.setdefault(pc, []).append(site_id)

    for pc, sites in parser_to_sites.items():
        if len(sites) > 1:
            _add(
                report,
                Severity.INFO,
                kind,
                "sites.yaml",
                f"parser_class '{pc}' partagé par : {sorted(sites)}",
                hint="Intentionnel ? Sinon, spécialiser.",
            )


# ============================================================================
#  Validation — Config sync
# ============================================================================


def validate_config_sync(report: ValidationReport) -> None:
    """Vérifie que config.example.yaml est synchronisé avec Settings.

    Compare les clés de premier niveau du YAML aux champs du modèle Pydantic
    `Settings`. Signale les clés YAML orphelines et les champs Pydantic
    absents de l'exemple.

    Args:
        report: Rapport à peupler.
    """
    report.checked_files += 1
    kind = ValidationKind.CONFIG_SYNC

    if not _CONFIG_EXAMPLE.exists():
        _add(report, Severity.ERROR, kind, str(_CONFIG_EXAMPLE), "config.example.yaml introuvable")
        return

    try:
        from nexusdl.core.config import Settings  # noqa: PLC0415
    except ImportError as exc:
        _add(
            report,
            Severity.WARNING,
            kind,
            "nexusdl.core.config",
            f"Settings introuvable : {exc} — validation sautée",
        )
        return

    try:
        example = _load_yaml(_CONFIG_EXAMPLE)
    except yaml.YAMLError as exc:
        _add(report, Severity.ERROR, kind, str(_CONFIG_EXAMPLE), f"YAML invalide : {exc}")
        return

    # Champs du modèle Pydantic
    model_fields = set(Settings.model_fields.keys())

    # Clés YAML de premier niveau (à l'exception des métadonnées)
    yaml_keys = set(example.keys()) - {"config_version"}

    # --- Clés YAML absentes du modèle ---
    for key in sorted(yaml_keys - model_fields):
        _add(
            report,
            Severity.ERROR,
            kind,
            f"config.example.yaml.{key}",
            f"Clé '{key}' absente de Settings (core/config.py)",
            hint="Ajouter le champ dans Settings ou supprimer la clé.",
        )

    # --- Champs Pydantic absents du YAML ---
    # Tolérance : certains champs sont purement runtime (ex: profiles).
    runtime_only: set[str] = set()  # à compléter si besoin
    for key in sorted(model_fields - yaml_keys - runtime_only):
        _add(
            report,
            Severity.WARNING,
            kind,
            "core/config.py::Settings",
            f"Champ '{key}' non documenté dans config.example.yaml",
            hint="Ajouter une entrée commentée dans l'exemple.",
        )


# ============================================================================
#  Validation — Logging profiles
# ============================================================================


def validate_logging_profiles(report: ValidationReport) -> None:
    """Vérifie que logging.yaml définit tous les profils requis.

    Args:
        report: Rapport à peupler.
    """
    report.checked_files += 1
    kind = ValidationKind.LOGGING_PROFILES

    if not _LOGGING_YAML.exists():
        _add(
            report,
            Severity.WARNING,
            kind,
            str(_LOGGING_YAML),
            "logging.yaml absent — les défauts du code seront utilisés",
        )
        return

    try:
        data = _load_yaml(_LOGGING_YAML)
    except yaml.YAMLError as exc:
        _add(report, Severity.ERROR, kind, str(_LOGGING_YAML), f"YAML invalide : {exc}")
        return

    profiles = set(data.get("profiles", {}).keys())
    missing = REQUIRED_LOGGING_PROFILES - profiles
    if missing:
        _add(
            report,
            Severity.WARNING,
            kind,
            "logging.yaml::profiles",
            f"Profils manquants : {sorted(missing)}",
            hint="Ajouter les profils manquants pour cohérence multi-env.",
        )

    # Vérifie la présence des handlers essentiels
    loguru_section = data.get("loguru", {})
    if isinstance(loguru_section, dict):
        for handler in ("console", "app_file", "error_file"):
            if handler not in loguru_section:
                _add(
                    report,
                    Severity.WARNING,
                    kind,
                    f"logging.yaml::loguru.{handler}",
                    f"Handler '{handler}' absent",
                )


# ============================================================================
#  Orchestration
# ============================================================================


VALIDATORS: Final[dict[ValidationKind, Any]] = {
    ValidationKind.SCHEMA: validate_schema,
    ValidationKind.SITES: validate_sites,
    ValidationKind.PARSERS: validate_parsers,
    ValidationKind.ORPHANS: validate_orphans,
    ValidationKind.DUPLICATES: validate_duplicates,
    ValidationKind.CONFIG_SYNC: validate_config_sync,
    ValidationKind.LOGGING_PROFILES: validate_logging_profiles,
}


def run_validations(
    kinds: list[ValidationKind],
    *,
    strict: bool,
) -> ValidationReport:
    """Lance une séquence de validations.

    Args:
        kinds: Types de validation à exécuter.
        strict: Si True, les warnings sont convertis en erreurs.

    Returns:
        Rapport agrégé.
    """
    import time

    report = ValidationReport()
    t0 = time.perf_counter()

    for kind in kinds:
        validator = VALIDATORS.get(kind)
        if validator is None:
            logger.warning("Validateur inconnu : {}", kind)
            continue
        try:
            validator(report)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Validateur {} a crashé", kind.value)
            _add(
                report,
                Severity.ERROR,
                kind,
                "<internal>",
                f"Crash du validateur : {type(exc).__name__}: {exc}",
            )

    if strict:
        for issue in report.issues:
            if issue.severity == Severity.WARNING:
                issue.severity = Severity.ERROR

    report.duration_seconds = time.perf_counter() - t0
    return report


# ============================================================================
#  Rendu
# ============================================================================


def render_report(report: ValidationReport, *, verbose: bool = False) -> None:
    """Affiche le rapport de validation.

    Args:
        report: Rapport à afficher.
        verbose: Affiche aussi les info/warnings réussis.
    """
    if not report.issues:
        console.print(
            Panel(
                f"[bold green]✓ Toutes les validations ont réussi.[/bold green]\n"
                f"{report.checked_sites} site(s), {report.checked_parsers} parser(s), "
                f"{report.checked_files} fichier(s) vérifiés en {report.duration_seconds:.2f}s.",
                border_style="green",
            ),
        )
        return

    table = Table(title="Problèmes détectés", header_style="bold cyan", show_lines=False)
    table.add_column("Sévérité", style="bold", no_wrap=True)
    table.add_column("Type", style="magenta", no_wrap=True)
    table.add_column("Localisation", style="cyan", overflow="fold")
    table.add_column("Message", overflow="fold")
    if verbose:
        table.add_column("Suggestion", style="dim", overflow="fold")

    icons = {
        Severity.ERROR: "[red]✗ ERROR[/red]",
        Severity.WARNING: "[yellow]⚠ WARN [/yellow]",
        Severity.INFO: "[blue]ℹ INFO [/blue]",
    }

    # Trie : erreurs d'abord, puis warnings, puis infos
    order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    sorted_issues = sorted(report.issues, key=lambda i: (order[i.severity], i.kind.value, i.location))

    for issue in sorted_issues:
        row = [
            icons[issue.severity],
            issue.kind.value,
            issue.location,
            issue.message,
        ]
        if verbose:
            row.append(issue.hint or "·")
        table.add_row(*row)

    console.print(table)
    console.print()
    console.print(
        f"[bold]Résumé :[/bold] {report.errors} erreur(s), {report.warnings} warning(s), "
        f"{report.checked_sites} site(s), {report.checked_parsers} parser(s) — "
        f"{report.duration_seconds:.2f}s",
    )


def render_json(report: ValidationReport) -> None:
    """Sortie JSON (pour CI).

    Args:
        report: Rapport à sérialiser.
    """
    payload = {
        "summary": {
            "errors": report.errors,
            "warnings": report.warnings,
            "checked_sites": report.checked_sites,
            "checked_parsers": report.checked_parsers,
            "checked_files": report.checked_files,
            "duration_seconds": report.duration_seconds,
        },
        "issues": [
            {
                **asdict(i),
                "severity": i.severity.value,
                "kind": i.kind.value,
            }
            for i in report.issues
        ],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))  # noqa: T201


# ============================================================================
#  CLI
# ============================================================================


def _parse_kinds(values: list[str]) -> list[ValidationKind]:
    """Convertit une liste de strings en ValidationKind.

    Args:
        values: Noms des validations.

    Returns:
        Liste de ValidationKind.

    Raises:
        typer.BadParameter: Si un nom est inconnu.
    """
    if "all" in values:
        return list(ValidationKind)
    parsed: list[ValidationKind] = []
    for v in values:
        try:
            parsed.append(ValidationKind(v))
        except ValueError as exc:
            valid = ", ".join(k.value for k in ValidationKind) + ", all"
            msg = f"Validation inconnue : '{v}'. Valides : {valid}"
            raise typer.BadParameter(msg) from exc
    return list(dict.fromkeys(parsed))  # dédup


@app.command("run")
def cmd_run(
    kinds: Annotated[
        list[str],
        typer.Argument(help="Validations : schema, sites, parsers, orphans, duplicates, config-sync, logging-profiles, all"),
    ],
    strict: Annotated[bool, typer.Option("--strict", help="Traiter les warnings comme erreurs")] = False,
    output_format: Annotated[str, typer.Option("--format", "-f", help="text | json")] = "text",
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Afficher les suggestions")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Silence si tout est OK")] = False,
) -> None:
    """Lance les validations demandées.

    Exemples :

        python scripts/validate_sites.py run all
        python scripts/validate_sites.py run parsers config-sync
        python scripts/validate_sites.py run all --strict --format json
    """
    logger.remove()
    logger.add(sys.stderr, level="WARNING", format="<level>{level: <8}</level> | {message}")

    if not kinds:
        kinds = ["all"]

    parsed = _parse_kinds(kinds)
    report = run_validations(parsed, strict=strict)

    if output_format == "json":
        render_json(report)
    else:
        if quiet and not report.issues:
            return
        render_report(report, verbose=verbose)

    if report.errors:
        raise typer.Exit(code=1)


@app.command("all")
def cmd_all(
    strict: Annotated[bool, typer.Option("--strict")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Raccourci pour valider tout."""
    logger.remove()
    logger.add(sys.stderr, level="WARNING", format="<level>{level: <8}</level> | {message}")

    report = run_validations(list(ValidationKind), strict=strict)
    render_report(report, verbose=verbose)
    if report.errors:
        raise typer.Exit(code=1)


@app.command("list")
def cmd_list() -> None:
    """Liste les validations disponibles."""
    table = Table(title="Validations disponibles", header_style="bold cyan")
    table.add_column("Nom", style="magenta")
    table.add_column("Description", style="cyan")
    descriptions = {
        ValidationKind.SCHEMA: "Validation JSON Schema de sites.yaml",
        ValidationKind.SITES: "Cohérence interne (champs requis, IDs, URL, langue)",
        ValidationKind.PARSERS: "Import dynamique + héritage BaseParser + ClassVars",
        ValidationKind.ORPHANS: "Fichiers parser non référencés dans sites.yaml",
        ValidationKind.DUPLICATES: "IDs et domaines dupliqués",
        ValidationKind.CONFIG_SYNC: "config.example.yaml ↔ core/config.py::Settings",
        ValidationKind.LOGGING_PROFILES: "Profils requis dans logging.yaml",
    }
    for kind, desc in descriptions.items():
        table.add_row(kind.value, desc)
    console.print(table)


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
