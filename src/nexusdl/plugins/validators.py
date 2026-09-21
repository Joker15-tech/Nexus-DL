"""Validateurs pour les plugins NexusDL.

Ce module définit la chaîne de validation appliquée à chaque plugin avant
son chargement par `PluginLoader`. Trois niveaux :

    1. Validation structurelle (Pydantic) : le manifest YAML est bien formé.
    2. Validation sémantique : version API compatible, hooks connus,
       permissions whitelistées, dépendances déclarées.
    3. Validation statique (AST) : détection de patterns dangereux dans
       le code source du plugin, sans l'exécuter.

Optionnellement, `allow_import=True` charge réellement le module pour
vérifier que l'entrypoint existe — à utiliser uniquement en dev ou pour
des plugins signés, car cela exécute du code arbitraire.

Example:
    Valider un plugin unique::

        from pathlib import Path
        from nexusdl.plugins.validators import validate_plugin

        result = validate_plugin(Path("plugins/my_plugin"))
        if not result.ok:
            for issue in result.issues:
                print(issue)

    Valider tous les plugins d'un dossier::

        from nexusdl.plugins.validators import validate_all

        aggregate = validate_all(Path("plugins"))
        print(f"{aggregate.ok_count}/{aggregate.total} plugins valides")
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import re
import sys
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, ClassVar, Final, Literal

import yaml
from loguru import logger
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    HttpUrl,
    ValidationError,
    field_validator,
    model_validator,
)

from nexusdl.core.exceptions import NexusDLError

# ============================================================================
#  Constantes
# ============================================================================

#: Version courante de l'API plugin exposée par NexusDL.
#: Format ``<major>.<minor>``. Le major doit matcher exactement, le minor
#: doit être ``<=`` (une API 1.5 peut charger un plugin 1.0, mais pas 2.0
#: ni 1.7).
PLUGIN_API_VERSION: Final[str] = "1.0"

#: Versions d'API acceptées (major courant uniquement).
SUPPORTED_API_MAJOR: Final[int] = 1
SUPPORTED_API_MINOR_MAX: Final[int] = 0

#: Nom du fichier de manifest dans chaque dossier de plugin.
MANIFEST_FILENAME: Final[str] = "nexus.plugin.yaml"

#: Nom du module d'entrée attendu par défaut (si `entrypoint` est omis).
DEFAULT_ENTRYPOINT_MODULE: Final[str] = "plugin"

#: Taille maximale autorisée pour un plugin (10 MiB) — un plugin n'est pas
#: censé embarquer des binaires lourds (utiliser les données du core).
MAX_PLUGIN_SIZE_BYTES: Final[int] = 10 * 1024 * 1024

#: Taille maximale d'un fichier Python individuel (1 MiB).
MAX_PY_FILE_SIZE_BYTES: Final[int] = 1024 * 1024

#: Permissions autorisées. Un plugin ne peut demander une permission
#: hors de cette liste.
ALLOWED_PERMISSIONS: Final[frozenset[str]] = frozenset(
    {
        "read_library",       # Lire la bibliothèque locale
        "write_library",      # Modifier la bibliothèque (tags, progression)
        "network_http",       # Requêtes HTTP via HttpSession (rate-limité)
        "network_raw",        # Socket brut (⚠️ accordé au cas par cas)
        "filesystem_read",    # Lecture de fichiers dans /data
        "filesystem_write",   # Écriture de fichiers dans /data
        "downloads",          # Enregistrer des hooks de téléchargement
        "notifications",      # Envoyer des notifications
        "metadata_mutation",  # Modifier les métadonnées de mangas
        "cookies",            # Accéder aux cookies chiffrés
        "config_read",        # Lire la configuration NexusDL
    },
)

#: Hooks supportés. Doit rester synchronisé avec `nexusdl.plugins.hooks`.
#: Importé dynamiquement si le module est présent, sinon fallback local.
try:
    from nexusdl.plugins.hooks import HOOK_NAMES as _HOOKS_FROM_MODULE
except ImportError:
    _HOOKS_FROM_MODULE = frozenset(
        {
            "on_load",
            "on_unload",
            "pre_search",
            "post_search",
            "pre_download",
            "post_download",
            "pre_package",
            "post_package",
            "on_error",
            "on_new_chapter",
            "on_manga_update",
            "on_library_scan",
        },
    )

ALLOWED_HOOKS: Final[frozenset[str]] = _HOOKS_FROM_MODULE

#: Patterns AST considérés comme dangereux, classés par sévérité.
#: Chaque entrée : (nom_qualifié, sévérité, message).
#: Le nom qualifié est de la forme "module.fonction" ou "fonction".
DANGEROUS_CALLS: Final[tuple[tuple[str, str, str], ...]] = (
    ("eval", "error", "`eval()` permet l'exécution arbitraire de code"),
    ("exec", "error", "`exec()` permet l'exécution arbitraire de code"),
    ("compile", "warning", "`compile()` peut être utilisé pour contourner l'analyse statique"),
    ("os.system", "error", "`os.system()` exécute des commandes shell arbitraires"),
    ("os.popen", "error", "`os.popen()` exécute des commandes shell arbitraires"),
    ("os.exec", "error", "`os.exec*()` remplace le processus courant"),
    ("os.spawn", "error", "`os.spawn*()` lance des processus arbitraires"),
    ("os.fork", "warning", "`os.fork()` — comportement non défini avec asyncio"),
    ("subprocess.run", "warning", "`subprocess.run()` — autorisé uniquement si permission `shell_exec`"),
    ("subprocess.Popen", "warning", "`subprocess.Popen()` — autorisé uniquement si permission `shell_exec`"),
    ("subprocess.call", "warning", "`subprocess.call()` — autorisé uniquement si permission `shell_exec`"),
    ("subprocess.check_output", "warning", "`subprocess.check_output()` — autorisé uniquement si permission `shell_exec`"),
    ("pickle.loads", "error", "`pickle.loads()` peut exécuter du code arbitraire"),
    ("pickle.load", "error", "`pickle.load()` peut exécuter du code arbitraire"),
    ("marshal.loads", "error", "`marshal.loads()` peut exécuter du code arbitraire"),
    ("shelve.open", "warning", "`shelve` utilise pickle en interne"),
    ("__import__", "warning", "`__import__()` dynamique — utiliser `importlib`"),
    ("importlib.import_module", "info", "Import dynamique — vérifier que le module cible est sandboxé"),
    ("shutil.rmtree", "warning", "`shutil.rmtree()` — risque de suppression massive"),
    ("open", "info", "`open()` avec mode 'w'/'a' — vérifier que le chemin est dans /data"),
)

#: Attributs dangereux à accéder (introspection pour sortir du sandbox).
DANGEROUS_ATTRIBUTES: Final[frozenset[str]] = frozenset(
    {
        "__globals__",
        "__builtins__",
        "__subclasses__",
        "__bases__",
        "__mro__",
        "__code__",
        "__closure__",
        "__reduce__",
        "__reduce_ex__",
    },
)

#: Modules stdlib autorisés par défaut dans le sandbox.
#: Un plugin sandboxé ne peut importer que ces modules + l'API NexusDL.
SAFE_STDLIB_MODULES: Final[frozenset[str]] = frozenset(
    {
        "abc",
        "asyncio",
        "base64",
        "collections",
        "contextlib",
        "dataclasses",
        "datetime",
        "enum",
        "functools",
        "hashlib",
        "html",
        "io",
        "itertools",
        "json",
        "logging",
        "math",
        "operator",
        "pathlib",
        "random",
        "re",
        "string",
        "textwrap",
        "time",
        "types",
        "typing",
        "unicodedata",
        "urllib.parse",
        "uuid",
    },
)

#: Préfixes de modules toujours autorisés (API NexusDL).
SAFE_MODULE_PREFIXES: Final[tuple[str, ...]] = (
    "nexusdl.plugins.api",
    "nexusdl.plugins.hooks",
    "nexusdl.core.models",
    "nexusdl.core.exceptions",
    "nexusdl.core.constants",
    "nexusdl.core.utils",
)

#: Regex pour valider les identifiants de plugin (dossier, nom).
PLUGIN_NAME_REGEX: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_-]{2,50}$")

#: Regex pour valider un entrypoint ``module:ClassName`` ou ``module:function``.
ENTRYPOINT_REGEX: Final[re.Pattern[str]] = re.compile(
    r"^[a-zA-Z_][\w.]*:[a-zA-Z_][\w]*$",
)


# ============================================================================
#  Enums
# ============================================================================


class Severity(str, Enum):
    """Sévérité d'un problème de validation."""

    ERROR = "error"      # Bloque le chargement du plugin
    WARNING = "warning"  # Chargement autorisé, avertissement loggé
    INFO = "info"        # Information, pas d'action requise


class Category(str, Enum):
    """Catégorie du problème détecté."""

    MANIFEST = "manifest"        # Structure du manifest YAML
    API_VERSION = "api_version"  # Compatibilité API
    HOOKS = "hooks"              # Hooks déclarés
    PERMISSIONS = "permissions"  # Permissions demandées
    DEPENDENCIES = "dependencies"  # Dépendances Python
    ENTRYPOINT = "entrypoint"    # Module/classe d'entrée
    SECURITY = "security"        # Patterns dangereux (AST)
    SANDBOX = "sandbox"          # Imports non autorisés en sandbox
    FILES = "files"              # Structure du dossier (symlinks, taille)
    INTEGRITY = "integrity"      # Checksums, signatures (futur)


# ============================================================================
#  Exceptions
# ============================================================================


class PluginValidationError(NexusDLError):
    """Erreur levée quand un plugin échoue la validation en mode strict.

    Attributes:
        plugin_name: Nom du plugin concerné.
        result: Résultat complet de la validation.
    """

    def __init__(self, plugin_name: str, result: PluginValidationResult) -> None:
        """Initialise l'exception.

        Args:
            plugin_name: Nom du plugin.
            result: Résultat de validation associé.
        """
        self.plugin_name = plugin_name
        self.result = result
        errors = [i for i in result.issues if i.severity is Severity.ERROR]
        summary = "; ".join(f"{i.category.value}: {i.message}" for i in errors[:3])
        super().__init__(f"Plugin '{plugin_name}' invalide : {summary}")


class PluginManifestError(PluginValidationError):
    """Erreur spécifique à un manifest invalide (YAML malformé, schéma)."""


# ============================================================================
#  Modèles Pydantic
# ============================================================================


class PluginAuthor(BaseModel):
    """Informations sur l'auteur d'un plugin.

    Attributes:
        name: Nom affiché.
        email: Email de contact (optionnel).
        url: Site ou profil (optionnel).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: Annotated[str, Field(min_length=1, max_length=100)]
    email: EmailStr | None = None
    url: HttpUrl | None = None


class PluginDependency(BaseModel):
    """Dépendance Python déclarée par un plugin.

    Attributes:
        name: Nom du package PyPI.
        version_spec: Spécification de version PEP 440 (ex: ">=2.28,<3").
        optional: True si la dépendance est optionnelle.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z][\w.-]*$")]
    version_spec: str | None = None
    optional: bool = False


class PluginManifest(BaseModel):
    """Schéma du manifest ``nexus.plugin.yaml``.

    Un manifest valide est la **condition nécessaire** pour qu'un plugin
    soit considéré. Le loader ne va pas plus loin si le manifest échoue.

    Attributes:
        name: Identifiant unique du plugin (snake_case ou kebab-case).
        version: Version du plugin (PEP 440 simplifiée).
        api_version: Version d'API NexusDL requise (ex: "1.0").
        author: Informations sur l'auteur.
        description: Description courte (1-3 phrases).
        homepage: URL de la page du projet (optionnel).
        license: Identifiant SPDX (ex: "MIT", "GPL-3.0-or-later").
        entrypoint: Chemin d'entrée ``module:Classe``.
        hooks: Liste des hooks que le plugin veut recevoir.
        permissions: Liste des permissions demandées.
        dependencies: Dépendances Python.
        python_requires: Contrainte sur la version Python (optionnel).
        min_nexusdl_version: Version minimale de NexusDL (optionnel).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: Annotated[str, Field(min_length=3, max_length=50)]
    version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+(?:[-+].+)?$")]
    api_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    author: PluginAuthor
    description: Annotated[str, Field(min_length=10, max_length=500)]
    homepage: HttpUrl | None = None
    license: str | None = None
    entrypoint: str = f"{DEFAULT_ENTRYPOINT_MODULE}:Plugin"
    hooks: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    dependencies: list[PluginDependency] = Field(default_factory=list)
    python_requires: str | None = None
    min_nexusdl_version: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        """Vérifie que le nom respecte la regex des identifiants de plugin.

        Args:
            value: Nom à valider.

        Returns:
            Le nom inchangé si valide.

        Raises:
            ValueError: Si le nom ne matche pas ``PLUGIN_NAME_REGEX``.
        """
        if not PLUGIN_NAME_REGEX.match(value):
            msg = f"nom invalide '{value}' (regex : {PLUGIN_NAME_REGEX.pattern})"
            raise ValueError(msg)
        return value

    @field_validator("entrypoint")
    @classmethod
    def _validate_entrypoint(cls, value: str) -> str:
        """Vérifie le format de l'entrypoint.

        Args:
            value: Chaîne ``module:ClassName``.

        Returns:
            La chaîne inchangée si valide.

        Raises:
            ValueError: Si le format est incorrect.
        """
        if not ENTRYPOINT_REGEX.match(value):
            msg = f"entrypoint invalide '{value}' (attendu : 'module:Classe')"
            raise ValueError(msg)
        return value

    @field_validator("hooks")
    @classmethod
    def _validate_hooks_no_duplicates(cls, value: list[str]) -> list[str]:
        """Vérifie l'absence de doublons dans les hooks.

        Args:
            value: Liste de hooks.

        Returns:
            La liste dédupliquée dans le même ordre.

        Raises:
            ValueError: Si un doublon est détecté (intentionnellement strict).
        """
        seen: set[str] = set()
        for h in value:
            if h in seen:
                msg = f"hook dupliqué : '{h}'"
                raise ValueError(msg)
            seen.add(h)
        return value

    @field_validator("permissions")
    @classmethod
    def _validate_permissions_no_duplicates(cls, value: list[str]) -> list[str]:
        """Vérifie l'absence de doublons dans les permissions.

        Args:
            value: Liste de permissions.

        Returns:
            La liste dédupliquée dans le même ordre.

        Raises:
            ValueError: Si un doublon est détecté.
        """
        seen: set[str] = set()
        for p in value:
            if p in seen:
                msg = f"permission dupliquée : '{p}'"
                raise ValueError(msg)
            seen.add(p)
        return value

    @model_validator(mode="after")
    def _validate_consistency(self) -> PluginManifest:
        """Vérifie la cohérence globale du manifest.

        Returns:
            L'instance validée.

        Raises:
            ValueError: Si des incohérences sont détectées.
        """
        # Un plugin qui veut recevoir un hook "on_error" doit avoir accès à
        # des notifications pour signaler l'erreur.
        if "on_error" in self.hooks and "notifications" not in self.permissions:
            msg = "hook 'on_error' déclaré sans permission 'notifications'"
            raise ValueError(msg)
        # Un plugin qui déclare 'downloads' doit vouloir recevoir au moins
        # un hook de téléchargement.
        download_hooks = {"pre_download", "post_download"}
        if "downloads" in self.permissions and not (download_hooks & set(self.hooks)):
            msg = "permission 'downloads' déclarée sans hook de téléchargement"
            raise ValueError(msg)
        return self


class ValidationIssue(BaseModel):
    """Un problème détecté lors de la validation.

    Attributes:
        severity: Niveau de gravité.
        category: Catégorie du problème.
        message: Description courte et actionnable.
        location: Fichier:ligne ou clé du manifest concernée.
        hint: Suggestion de correction (optionnel).
    """

    model_config = ConfigDict(frozen=True)

    severity: Severity
    category: Category
    message: str
    location: str | None = None
    hint: str | None = None

    def __str__(self) -> str:
        """Rend le problème sous forme lisible.

        Returns:
            Chaîne formatée ``[SEVERITY] category: message (location)``.
        """
        loc = f" ({self.location})" if self.location else ""
        return f"[{self.severity.value.upper()}] {self.category.value}: {self.message}{loc}"


class PluginValidationResult(BaseModel):
    """Résultat de la validation d'un plugin unique.

    Attributes:
        plugin_name: Nom du plugin (ou nom du dossier si manifest illisible).
        path: Chemin du dossier du plugin.
        manifest: Manifest parsé et validé, si la validation structurelle a réussi.
        issues: Liste des problèmes détectés.
        duration_ms: Durée de la validation en millisecondes.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    plugin_name: str
    path: Path
    manifest: PluginManifest | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def ok(self) -> bool:
        """True si le plugin est valide (aucune erreur)."""
        return not any(i.severity is Severity.ERROR for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        """True si au moins un avertissement est présent."""
        return any(i.severity is Severity.WARNING for i in self.issues)

    @property
    def error_count(self) -> int:
        """Nombre d'erreurs."""
        return sum(1 for i in self.issues if i.severity is Severity.ERROR)

    @property
    def warning_count(self) -> int:
        """Nombre d'avertissements."""
        return sum(1 for i in self.issues if i.severity is Severity.WARNING)

    def by_category(self, category: Category) -> list[ValidationIssue]:
        """Filtre les problèmes par catégorie.

        Args:
            category: Catégorie cible.

        Returns:
            Liste des problèmes de cette catégorie.
        """
        return [i for i in self.issues if i.category is category]

    def format_report(self, *, color: bool = False) -> str:
        """Génère un rapport textuel lisible.

        Args:
            color: Insère des codes ANSI (vert/jaune/rouge) si True.

        Returns:
            Rapport multi-lignes.
        """
        status = "OK" if self.ok else "FAIL"
        if color:
            code = "\033[32m" if self.ok else "\033[31m"
            status = f"{code}{status}\033[0m"

        lines = [
            f"Plugin: {self.plugin_name}",
            f"Path:   {self.path}",
            f"Status: {status} ({self.error_count} erreurs, {self.warning_count} warnings)",
            f"Time:   {self.duration_ms:.1f} ms",
        ]
        if self.manifest:
            lines.append(f"Version: {self.manifest.version} (API {self.manifest.api_version})")
        if self.issues:
            lines.append("")
            lines.append("Problèmes:")
            for issue in self.issues:
                lines.append(f"  {issue}")
                if issue.hint:
                    lines.append(f"    → {issue.hint}")
        return "\n".join(lines)


class AggregateValidationResult(BaseModel):
    """Résultat de la validation d'un ensemble de plugins.

    Attributes:
        results: Résultats individuels par plugin.
        duration_ms: Durée totale.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    results: list[PluginValidationResult] = Field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def total(self) -> int:
        """Nombre total de plugins validés."""
        return len(self.results)

    @property
    def ok_count(self) -> int:
        """Nombre de plugins valides."""
        return sum(1 for r in self.results if r.ok)

    @property
    def failed_count(self) -> int:
        """Nombre de plugins invalides."""
        return self.total - self.ok_count

    @property
    def with_warnings_count(self) -> int:
        """Nombre de plugins avec warnings (mais valides)."""
        return sum(1 for r in self.results if r.ok and r.has_warnings)

    def failed(self) -> list[PluginValidationResult]:
        """Retourne les résultats des plugins invalides.

        Returns:
            Liste des résultats en échec.
        """
        return [r for r in self.results if not r.ok]


# ============================================================================
#  Visiteurs AST
# ============================================================================


class _DangerousPatternVisitor(ast.NodeVisitor):
    """Visiteur AST qui détecte les patterns dangereux.

    Parcourt l'arbre syntaxique sans l'exécuter. Collecte les appels à des
    fonctions interdites, les accès à des attributs d'introspection, et les
    imports non autorisés en mode sandbox.

    Attributes:
        issues: Liste des problèmes détectés.
        sandbox: Si True, vérifie les imports contre la whitelist.
        allowed_modules: Modules supplémentaires autorisés (dépendances déclarées).
    """

    def __init__(
        self,
        *,
        sandbox: bool,
        allowed_modules: frozenset[str] | None = None,
    ) -> None:
        """Initialise le visiteur.

        Args:
            sandbox: Active la vérification des imports.
            allowed_modules: Modules additionnels autorisés (dépendances du manifest).
        """
        self.issues: list[ValidationIssue] = []
        self.sandbox = sandbox
        self.allowed_modules = allowed_modules or frozenset()

    def _record(
        self,
        severity: Severity,
        category: Category,
        message: str,
        node: ast.AST,
        hint: str | None = None,
    ) -> None:
        """Enregistre un problème avec sa localisation.

        Args:
            severity: Sévérité du problème.
            category: Catégorie.
            message: Description.
            node: Nœud AST source (pour la ligne).
            hint: Suggestion optionnelle.
        """
        lineno = getattr(node, "lineno", 0)
        self.issues.append(
            ValidationIssue(
                severity=severity,
                category=category,
                message=message,
                location=f"L{lineno}",
                hint=hint,
            ),
        )

    # --- Visite des appels -------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 — convention ast.NodeVisitor
        """Visite un appel de fonction/méthode.

        Args:
            node: Nœud d'appel AST.
        """
        qualname = self._call_qualname(node)
        if qualname:
            for pattern, severity_str, message in DANGEROUS_CALLS:
                if qualname == pattern or qualname.endswith(f".{pattern}"):
                    severity = Severity(severity_str)
                    self._record(
                        severity,
                        Category.SECURITY,
                        message,
                        node,
                        hint=f"Appel détecté : {qualname}",
                    )
                    break
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        """Visite un accès à un attribut.

        Args:
            node: Nœud d'attribut AST.
        """
        if node.attr in DANGEROUS_ATTRIBUTES:
            self._record(
                Severity.ERROR,
                Category.SECURITY,
                f"accès à l'attribut d'introspection '{node.attr}'",
                node,
                hint="L'introspection est interdite pour maintenir l'isolation du plugin.",
            )
        self.generic_visit(node)

    # --- Visite des imports ------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        """Visite un import ``import X``.

        Args:
            node: Nœud d'import AST.
        """
        if self.sandbox:
            for alias in node.names:
                self._check_module(alias.name, node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        """Visite un import ``from X import Y``.

        Args:
            node: Nœud d'import-from AST.
        """
        if self.sandbox and node.module:
            self._check_module(node.module, node)
        self.generic_visit(node)

    # --- Helpers -----------------------------------------------------------

    def _check_module(self, module: str, node: ast.AST) -> None:
        """Vérifie qu'un module est autorisé en mode sandbox.

        Args:
            module: Nom complet du module importé.
            node: Nœud AST (pour la ligne).
        """
        root = module.split(".", 1)[0]
        # Autorisé si dans la stdlib safe OU préfixe NexusDL OU dépendance déclarée
        if root in SAFE_STDLIB_MODULES or module in SAFE_STDLIB_MODULES:
            return
        if any(module.startswith(prefix) for prefix in SAFE_MODULE_PREFIXES):
            return
        if root in self.allowed_modules:
            return
        self._record(
            Severity.WARNING,
            Category.SANDBOX,
            f"import non whitelisté en mode sandbox : '{module}'",
            node,
            hint="Déclarer la dépendance dans le manifest ou désactiver le sandbox.",
        )

    @staticmethod
    def _call_qualname(node: ast.Call) -> str | None:
        """Extrait le nom qualifié d'un appel (ex: ``os.system``).

        Args:
            node: Nœud d'appel AST.

        Returns:
            Nom qualifié, ou None si non dérivable statiquement.
        """
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            parts: list[str] = [func.attr]
            current: ast.AST = func.value
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
                return ".".join(reversed(parts))
        return None


class _EntrypointVisitor(ast.NodeVisitor):
    """Visiteur AST qui vérifie l'existence d'un symbole (classe ou fonction).

    Utilisé pour valider l'entrypoint sans importer le module.

    Attributes:
        target_name: Nom du symbole recherché.
        found_classes: Noms des classes trouvées.
        found_functions: Noms des fonctions trouvées.
    """

    def __init__(self, target_name: str) -> None:
        """Initialise le visiteur.

        Args:
            target_name: Nom du symbole (classe ou fonction) à chercher.
        """
        self.target_name = target_name
        self.found_classes: list[str] = []
        self.found_functions: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        """Visite une définition de classe.

        Args:
            node: Nœud de classe AST.
        """
        self.found_classes.append(node.name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        """Visite une définition de fonction.

        Args:
            node: Nœud de fonction AST.
        """
        self.found_functions.append(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        """Visite une définition de fonction async.

        Args:
            node: Nœud de fonction async AST.
        """
        self.found_functions.append(node.name)
        self.generic_visit(node)

    @property
    def exists(self) -> bool:
        """True si le symbole cible a été trouvé."""
        return self.target_name in self.found_classes or self.target_name in self.found_functions


# ============================================================================
#  Validateurs unitaires
# ============================================================================


def _load_manifest_yaml(plugin_path: Path) -> tuple[dict[str, Any] | None, list[ValidationIssue]]:
    """Charge et parse le manifest YAML d'un plugin.

    Args:
        plugin_path: Chemin du dossier du plugin.

    Returns:
        Tuple (dict parsé ou None, liste de problèmes).
    """
    issues: list[ValidationIssue] = []
    manifest_path = plugin_path / MANIFEST_FILENAME

    if not manifest_path.exists():
        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category=Category.MANIFEST,
                message=f"manifest '{MANIFEST_FILENAME}' introuvable",
                location=str(plugin_path),
                hint=f"Créer un fichier {MANIFEST_FILENAME} à la racine du plugin.",
            ),
        )
        return None, issues

    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category=Category.MANIFEST,
                message=f"lecture impossible : {exc}",
                location=str(manifest_path),
            ),
        )
        return None, issues

    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category=Category.MANIFEST,
                message=f"YAML invalide : {exc}",
                location=str(manifest_path),
                hint="Vérifier l'indentation (2 espaces) et les caractères spéciaux.",
            ),
        )
        return None, issues

    if not isinstance(parsed, dict):
        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category=Category.MANIFEST,
                message=f"structure attendue : mapping YAML, obtenu {type(parsed).__name__}",
                location=str(manifest_path),
            ),
        )
        return None, issues

    return parsed, issues


def validate_manifest_schema(
    plugin_path: Path,
) -> tuple[PluginManifest | None, list[ValidationIssue]]:
    """Valide la structure du manifest contre le schéma Pydantic.

    Args:
        plugin_path: Chemin du dossier du plugin.

    Returns:
        Tuple (manifest validé ou None, liste de problèmes).

    Raises:
        Aucune — les erreurs sont converties en ``ValidationIssue``.
    """
    data, issues = _load_manifest_yaml(plugin_path)
    if data is None:
        return None, issues

    try:
        manifest = PluginManifest.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"])
            issues.append(
                ValidationIssue(
                    severity=Severity.ERROR,
                    category=Category.MANIFEST,
                    message=f"{loc}: {err['msg']}",
                    location=f"{MANIFEST_FILENAME}:{loc}",
                    hint=err.get("type", ""),
                ),
            )
        return None, issues

    return manifest, issues


def validate_api_version(
    manifest: PluginManifest,
) -> list[ValidationIssue]:
    """Vérifie la compatibilité de la version d'API.

    Règle : le major doit correspondre exactement ; le minor du plugin doit
    être ``<=`` au minor supporté.

    Args:
        manifest: Manifest validé.

    Returns:
        Liste de problèmes (vide si compatible).
    """
    issues: list[ValidationIssue] = []

    parts = manifest.api_version.split(".")
    if len(parts) != 2:  # noqa: PLR2004
        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category=Category.API_VERSION,
                message=f"format invalide : '{manifest.api_version}' (attendu X.Y)",
                location="api_version",
            ),
        )
        return issues

    try:
        major, minor = int(parts[0]), int(parts[1])
    except ValueError:
        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category=Category.API_VERSION,
                message=f"composants non numériques : '{manifest.api_version}'",
                location="api_version",
            ),
        )
        return issues

    if major != SUPPORTED_API_MAJOR:
        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category=Category.API_VERSION,
                message=(
                    f"API majeure incompatible : plugin demande {major}.x, "
                    f"NexusDL expose {PLUGIN_API_VERSION}"
                ),
                location="api_version",
                hint="Mettre à jour le plugin ou downgrader NexusDL.",
            ),
        )
        return issues

    if minor > SUPPORTED_API_MINOR_MAX:
        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category=Category.API_VERSION,
                message=(
                    f"API mineure trop récente : plugin demande {major}.{minor}, "
                    f"maximum supporté {major}.{SUPPORTED_API_MINOR_MAX}"
                ),
                location="api_version",
                hint="Mettre à jour NexusDL vers une version plus récente.",
            ),
        )

    return issues


def validate_hooks(manifest: PluginManifest) -> list[ValidationIssue]:
    """Vérifie que tous les hooks déclarés sont supportés.

    Args:
        manifest: Manifest validé.

    Returns:
        Liste de problèmes.
    """
    issues: list[ValidationIssue] = []
    for hook in manifest.hooks:
        if hook not in ALLOWED_HOOKS:
            issues.append(
                ValidationIssue(
                    severity=Severity.ERROR,
                    category=Category.HOOKS,
                    message=f"hook inconnu : '{hook}'",
                    location="hooks",
                    hint=f"Hooks supportés : {', '.join(sorted(ALLOWED_HOOKS))}",
                ),
            )
    return issues


def validate_permissions(manifest: PluginManifest) -> list[ValidationIssue]:
    """Vérifie que toutes les permissions demandées sont whitelistées.

    Args:
        manifest: Manifest validé.

    Returns:
        Liste de problèmes.
    """
    issues: list[ValidationIssue] = []
    for perm in manifest.permissions:
        if perm not in ALLOWED_PERMISSIONS:
            issues.append(
                ValidationIssue(
                    severity=Severity.ERROR,
                    category=Category.PERMISSIONS,
                    message=f"permission inconnue : '{perm}'",
                    location="permissions",
                    hint=f"Permissions autorisées : {', '.join(sorted(ALLOWED_PERMISSIONS))}",
                ),
            )

    # Avertissement sur les permissions sensibles
    sensitive = {"network_raw", "filesystem_write", "cookies", "write_library"}
    for perm in manifest.permissions:
        if perm in sensitive:
            issues.append(
                ValidationIssue(
                    severity=Severity.WARNING,
                    category=Category.PERMISSIONS,
                    message=f"permission sensible demandée : '{perm}'",
                    location="permissions",
                    hint="Vérifier que le plugin est de confiance.",
                ),
            )

    return issues


def validate_dependencies(manifest: PluginManifest) -> list[ValidationIssue]:
    """Vérifie la présence des dépendances Python (sauf optionnelles).

    Args:
        manifest: Manifest validé.

    Returns:
        Liste de problèmes.
    """
    issues: list[ValidationIssue] = []
    for dep in manifest.dependencies:
        if dep.optional:
            continue
        # Normalise le nom PyPI (les tirets et underscores sont équivalents)
        normalized = dep.name.replace("-", "_").lower()
        if importlib.util.find_spec(normalized) is None and importlib.util.find_spec(dep.name) is None:
            issues.append(
                ValidationIssue(
                    severity=Severity.WARNING,
                    category=Category.DEPENDENCIES,
                    message=f"dépendance '{dep.name}' absente",
                    location="dependencies",
                    hint=f"Installer : pip install '{dep.name}{dep.version_spec or ''}'",
                ),
            )
    return issues


def validate_files(
    plugin_path: Path,
) -> list[ValidationIssue]:
    """Vérifie la structure du dossier du plugin.

    Détecte : symlinks, fichiers trop gros, fichiers exécutables suspects.

    Args:
        plugin_path: Chemin du dossier du plugin.

    Returns:
        Liste de problèmes.
    """
    issues: list[ValidationIssue] = []
    total_size = 0

    for entry in plugin_path.rglob("*"):
        # Symlinks : peuvent pointer en dehors du dossier
        if entry.is_symlink():
            issues.append(
                ValidationIssue(
                    severity=Severity.WARNING,
                    category=Category.FILES,
                    message=f"symlink détecté : {entry.relative_to(plugin_path)}",
                    location=str(entry),
                    hint="Les symlinks peuvent contourner le sandbox.",
                ),
            )
            continue

        if not entry.is_file():
            continue

        try:
            size = entry.stat().st_size
        except OSError:
            continue

        total_size += size

        # Fichiers Python trop gros (souvent du code obfusqué)
        if entry.suffix == ".py" and size > MAX_PY_FILE_SIZE_BYTES:
            issues.append(
                ValidationIssue(
                    severity=Severity.WARNING,
                    category=Category.FILES,
                    message=f"fichier Python volumineux : {entry.name} ({size} octets)",
                    location=str(entry.relative_to(plugin_path)),
                    hint="Code possiblement obfusqué — vérifier manuellement.",
                ),
            )

        # Fichiers exécutables (binaire embarqué)
        if entry.suffix in {".so", ".dll", ".dylib", ".exe", ".bin"}:
            issues.append(
                ValidationIssue(
                    severity=Severity.WARNING,
                    category=Category.FILES,
                    message=f"binaire embarqué : {entry.name}",
                    location=str(entry.relative_to(plugin_path)),
                    hint="Un plugin Python ne devrait pas embarquer de binaire.",
                ),
            )

    if total_size > MAX_PLUGIN_SIZE_BYTES:
        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category=Category.FILES,
                message=f"plugin trop volumineux : {total_size} octets > {MAX_PLUGIN_SIZE_BYTES}",
                location=str(plugin_path),
                hint="Utiliser un dépôt externe pour les ressources lourdes.",
            ),
        )

    return issues


def validate_ast_safety(
    plugin_path: Path,
    *,
    sandbox: bool,
    allowed_modules: frozenset[str] | None = None,
) -> list[ValidationIssue]:
    """Analyse statiquement le code Python du plugin (sans l'exécuter).

    Détecte les patterns dangereux (``eval``, ``exec``, ``os.system``,
    introspection, etc.) et — en mode sandbox — les imports non whitelistés.

    Args:
        plugin_path: Chemin du dossier du plugin.
        sandbox: Si True, vérifie les imports contre la whitelist.
        allowed_modules: Modules supplémentaires autorisés (deps déclarées).

    Returns:
        Liste de problèmes consolidés.
    """
    issues: list[ValidationIssue] = []

    py_files = sorted(plugin_path.rglob("*.py"))
    if not py_files:
        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category=Category.ENTRYPOINT,
                message="aucun fichier Python trouvé dans le plugin",
                location=str(plugin_path),
            ),
        )
        return issues

    for py_file in py_files:
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError as exc:
            issues.append(
                ValidationIssue(
                    severity=Severity.ERROR,
                    category=Category.SECURITY,
                    message=f"erreur de syntaxe : {exc.msg}",
                    location=f"{py_file.relative_to(plugin_path)}:{exc.lineno}",
                ),
            )
            continue
        except OSError as exc:
            issues.append(
                ValidationIssue(
                    severity=Severity.ERROR,
                    category=Category.SECURITY,
                    message=f"lecture impossible : {exc}",
                    location=str(py_file),
                ),
            )
            continue

        visitor = _DangerousPatternVisitor(sandbox=sandbox, allowed_modules=allowed_modules)
        visitor.visit(tree)

        # Préfixe chaque problème avec le chemin relatif du fichier
        rel = py_file.relative_to(plugin_path)
        for issue in visitor.issues:
            issues.append(
                issue.model_copy(
                    update={"location": f"{rel}:{issue.location}" if issue.location else str(rel)},
                ),
            )

    return issues


def validate_entrypoint_symbol(
    plugin_path: Path,
    manifest: PluginManifest,
) -> list[ValidationIssue]:
    """Vérifie statiquement (AST) que l'entrypoint existe.

    Contrairement à ``validate_entrypoint_import``, **n'exécute pas** le code
    du plugin. Cherche une ``ClassDef`` ou ``FunctionDef`` portant le nom
    du symbole cible dans le fichier spécifié par l'entrypoint.

    Args:
        plugin_path: Chemin du dossier du plugin.
        manifest: Manifest validé.

    Returns:
        Liste de problèmes.
    """
    issues: list[ValidationIssue] = []

    module_name, symbol_name = manifest.entrypoint.split(":", 1)
    # "plugin" → "plugin.py" ; "sub.plugin" → "sub/plugin.py"
    module_rel = module_name.replace(".", "/")
    candidates = [
        plugin_path / f"{module_rel}.py",
        plugin_path / module_rel / "__init__.py",
    ]

    target_file: Path | None = None
    for candidate in candidates:
        if candidate.exists():
            target_file = candidate
            break

    if target_file is None:
        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category=Category.ENTRYPOINT,
                message=f"module '{module_name}' introuvable",
                location=manifest.entrypoint,
                hint=f"Créer '{module_rel}.py' ou '{module_rel}/__init__.py'.",
            ),
        )
        return issues

    try:
        tree = ast.parse(target_file.read_text(encoding="utf-8"), filename=str(target_file))
    except (SyntaxError, OSError) as exc:
        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category=Category.ENTRYPOINT,
                message=f"parsing échoué : {exc}",
                location=str(target_file.relative_to(plugin_path)),
            ),
        )
        return issues

    visitor = _EntrypointVisitor(symbol_name)
    visitor.visit(tree)

    if not visitor.exists:
        available = sorted(set(visitor.found_classes + visitor.found_functions))
        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category=Category.ENTRYPOINT,
                message=f"symbole '{symbol_name}' absent de {target_file.name}",
                location=manifest.entrypoint,
                hint=f"Symboles disponibles : {', '.join(available[:10])}",
            ),
        )

    return issues


def validate_entrypoint_import(
    plugin_path: Path,
    manifest: PluginManifest,
) -> list[ValidationIssue]:
    """Charge le module du plugin et vérifie que l'entrypoint est utilisable.

    ⚠️ **Exécute le code du plugin** au moment de l'import. À n'utiliser que
    pour des plugins de confiance (dev, plugins signés, CI). Le chargement
    est effectué dans un namespace isolé via ``importlib.util``.

    Args:
        plugin_path: Chemin du dossier du plugin.
        manifest: Manifest validé.

    Returns:
        Liste de problèmes.
    """
    issues: list[ValidationIssue] = []

    module_name, symbol_name = manifest.entrypoint.split(":", 1)
    module_rel = module_name.replace(".", "/")
    candidates = [
        plugin_path / f"{module_rel}.py",
        plugin_path / module_rel / "__init__.py",
    ]

    source_file: Path | None = None
    for candidate in candidates:
        if candidate.exists():
            source_file = candidate
            break

    if source_file is None:
        # Déjà signalé par validate_entrypoint_symbol — pas de doublon
        return issues

    # Nom de module unique pour éviter les collisions entre plugins
    import_name = f"_nexusdl_plugin_{manifest.name}_{module_name.replace('.', '_')}"

    # Ajoute temporairement le dossier du plugin à sys.path pour les imports
    # internes au plugin (from . import helpers, etc.)
    added_to_path = False
    if str(plugin_path) not in sys.path:
        sys.path.insert(0, str(plugin_path))
        added_to_path = True

    try:
        spec = importlib.util.spec_from_file_location(import_name, source_file)
        if spec is None or spec.loader is None:
            issues.append(
                ValidationIssue(
                    severity=Severity.ERROR,
                    category=Category.ENTRYPOINT,
                    message=f"impossible de créer un spec pour {source_file.name}",
                    location=manifest.entrypoint,
                ),
            )
            return issues

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not hasattr(module, symbol_name):
            issues.append(
                ValidationIssue(
                    severity=Severity.ERROR,
                    category=Category.ENTRYPOINT,
                    message=f"'{symbol_name}' absent du module chargé",
                    location=manifest.entrypoint,
                ),
            )
            return issues

        obj = getattr(module, symbol_name)

        # Si c'est une classe, vérifier qu'elle expose au moins `on_load`.
        if isinstance(obj, type):
            for method in ("on_load", "on_unload"):
                if not hasattr(obj, method):
                    issues.append(
                        ValidationIssue(
                            severity=Severity.WARNING,
                            category=Category.ENTRYPOINT,
                            message=f"classe '{symbol_name}' sans méthode '{method}'",
                            location=manifest.entrypoint,
                            hint="Les méthodes on_load/on_unload sont recommandées.",
                        ),
                    )
        elif not callable(obj):
            issues.append(
                ValidationIssue(
                    severity=Severity.ERROR,
                    category=Category.ENTRYPOINT,
                    message=f"'{symbol_name}' n'est ni une classe ni un callable",
                    location=manifest.entrypoint,
                ),
            )

    except Exception as exc:  # noqa: BLE001 — on capture tout ce que le plugin peut lever
        logger.opt(exception=exc).debug("Échec d'import du plugin {}", manifest.name)
        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category=Category.ENTRYPOINT,
                message=f"import échoué : {type(exc).__name__}: {exc}",
                location=manifest.entrypoint,
                hint="Vérifier les dépendances et les imports relatifs.",
            ),
        )
    finally:
        # Nettoyage : retire le module du cache et du sys.path
        sys.modules.pop(import_name, None)
        if added_to_path:
            try:
                sys.path.remove(str(plugin_path))
            except ValueError:
                pass

    return issues


# ============================================================================
#  Orchestrateur
# ============================================================================


def validate_plugin(
    plugin_path: Path,
    *,
    sandbox: bool = True,
    allow_import: bool = False,
    strict: bool = False,
) -> PluginValidationResult:
    """Valide un plugin complet (manifest + code + structure).

    Enchaîne les validateurs dans l'ordre logique :

        1. Manifest (structure + schéma)
        2. Version d'API
        3. Hooks
        4. Permissions
        5. Dépendances
        6. Structure de fichiers
        7. Analyse AST (patterns dangereux + imports sandbox)
        8. Entrypoint (AST symbol check)
        9. Import réel (optionnel, ``allow_import=True``)

    Chaque étape peut court-circuiter les suivantes si un pré-requis échoue
    (ex: pas de manifest → pas de validation d'API version).

    Args:
        plugin_path: Chemin du dossier du plugin.
        sandbox: Active la vérification des imports (défaut: True).
        allow_import: Si True, exécute le code du plugin pour valider
            l'entrypoint réellement (défaut: False — plus sûr).
        strict: Si True, lève ``PluginValidationError`` si le plugin est
            invalide au lieu de retourner un résultat (défaut: False).

    Returns:
        Résultat de validation complet.

    Raises:
        PluginValidationError: Si ``strict=True`` et le plugin est invalide.
        NotADirectoryError: Si ``plugin_path`` n'est pas un dossier.
    """
    import time

    t0 = time.perf_counter()
    plugin_path = plugin_path.resolve()

    if not plugin_path.is_dir():
        msg = f"Plugin path doit être un dossier : {plugin_path}"
        raise NotADirectoryError(msg)

    result = PluginValidationResult(
        plugin_name=plugin_path.name,
        path=plugin_path,
    )

    # --- Étape 1 : manifest ---
    manifest, manifest_issues = validate_manifest_schema(plugin_path)
    result.issues.extend(manifest_issues)

    if manifest is None:
        # Manifest invalide → on ne peut pas continuer
        result.duration_ms = (time.perf_counter() - t0) * 1000.0
        _maybe_raise(result, strict=strict)
        return result

    result.plugin_name = manifest.name
    result.manifest = manifest

    # --- Étape 2 : version API ---
    result.issues.extend(validate_api_version(manifest))

    # --- Étape 3 : hooks ---
    result.issues.extend(validate_hooks(manifest))

    # --- Étape 4 : permissions ---
    result.issues.extend(validate_permissions(manifest))

    # --- Étape 5 : dépendances ---
    result.issues.extend(validate_dependencies(manifest))

    # --- Étape 6 : structure du dossier ---
    result.issues.extend(validate_files(plugin_path))

    # --- Étape 7 : analyse AST ---
    # En mode sandbox, les dépendances déclarées deviennent des modules autorisés.
    allowed_modules: frozenset[str] = frozenset(
        d.name.replace("-", "_").lower() for d in manifest.dependencies
    )
    result.issues.extend(
        validate_ast_safety(plugin_path, sandbox=sandbox, allowed_modules=allowed_modules),
    )

    # --- Étape 8 : entrypoint (statique) ---
    result.issues.extend(validate_entrypoint_symbol(plugin_path, manifest))

    # --- Étape 9 : import réel (optionnel) ---
    if allow_import and result.ok:
        result.issues.extend(validate_entrypoint_import(plugin_path, manifest))

    result.duration_ms = (time.perf_counter() - t0) * 1000.0
    _maybe_raise(result, strict=strict)
    return result


def _maybe_raise(result: PluginValidationResult, *, strict: bool) -> None:
    """Lève une exception si strict et le résultat est invalide.

    Args:
        result: Résultat de validation.
        strict: Active le mode strict.

    Raises:
        PluginValidationError: Si ``strict=True`` et ``result.ok=False``.
    """
    if strict and not result.ok:
        raise PluginValidationError(result.plugin_name, result)


def validate_all(
    plugins_dir: Path,
    *,
    sandbox: bool = True,
    allow_import: bool = False,
    strict: bool = False,
    stop_on_error: bool = False,
) -> AggregateValidationResult:
    """Valide tous les plugins d'un dossier.

    Chaque sous-dossier de ``plugins_dir`` contenant un ``nexus.plugin.yaml``
    est validé. Les dossiers sans manifest sont ignorés silencieusement
    (peut être un dossier `__pycache__`, `.git`, ou un plugin en construction).

    Args:
        plugins_dir: Dossier parent des plugins.
        sandbox: Active la vérification des imports.
        allow_import: Si True, exécute le code de chaque plugin.
        strict: Si True, lève à la première erreur (combiné à ``stop_on_error``).
        stop_on_error: Si True, arrête la validation au premier plugin invalide.

    Returns:
        Résultat agrégé.

    Raises:
        PluginValidationError: Si ``strict=True`` et un plugin échoue.
        NotADirectoryError: Si ``plugins_dir`` n'existe pas.
    """
    import time

    t0 = time.perf_counter()

    if not plugins_dir.is_dir():
        msg = f"Dossier plugins introuvable : {plugins_dir}"
        raise NotADirectoryError(msg)

    aggregate = AggregateValidationResult()

    for entry in sorted(plugins_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("_") and entry.name != "_example":
            # Ignore __pycache__, _private, .git, etc. sauf _example
            continue
        if not (entry / MANIFEST_FILENAME).exists():
            logger.debug("Dossier sans manifest ignoré : {}", entry.name)
            continue

        result = validate_plugin(
            entry,
            sandbox=sandbox,
            allow_import=allow_import,
            strict=False,  # on gère le strict au niveau agrégé
        )
        aggregate.results.append(result)

        if not result.ok:
            logger.warning(
                "Plugin invalide : {} ({} erreurs)",
                result.plugin_name,
                result.error_count,
            )
            if stop_on_error:
                break

    aggregate.duration_ms = (time.perf_counter() - t0) * 1000.0

    if strict and aggregate.failed_count > 0:
        first = aggregate.failed()[0]
        raise PluginValidationError(first.plugin_name, first)

    return aggregate


async def validate_plugin_async(
    plugin_path: Path,
    *,
    sandbox: bool = True,
    allow_import: bool = False,
    strict: bool = False,
) -> PluginValidationResult:
    """Version async de :func:`validate_plugin`.

    Exécute la validation dans un thread séparé via
    ``asyncio.to_thread`` pour ne pas bloquer la boucle d'événements.
    Utile pour un appelant async (loader, endpoint FastAPI).

    Args:
        plugin_path: Chemin du dossier du plugin.
        sandbox: Active la vérification des imports.
        allow_import: Si True, exécute le code du plugin.
        strict: Si True, lève à la première erreur.

    Returns:
        Résultat de validation.
    """
    return await asyncio.to_thread(
        validate_plugin,
        plugin_path,
        sandbox=sandbox,
        allow_import=allow_import,
        strict=strict,
    )


async def validate_all_async(
    plugins_dir: Path,
    *,
    sandbox: bool = True,
    allow_import: bool = False,
    strict: bool = False,
    stop_on_error: bool = False,
    max_concurrent: int = 4,
) -> AggregateValidationResult:
    """Version async de :func:`validate_all` avec parallélisation.

    Valide les plugins en parallèle dans un pool de threads limité à
    ``max_concurrent``. Utile quand on a beaucoup de plugins et que le
    coût de validation devient perceptible.

    Args:
        plugins_dir: Dossier parent des plugins.
        sandbox: Active la vérification des imports.
        allow_import: Si True, exécute le code de chaque plugin.
        strict: Si True, lève si au moins un plugin échoue.
        stop_on_error: Si True, arrête au premier échec (séquentiel).
        max_concurrent: Nombre de validations parallèles.

    Returns:
        Résultat agrégé.

    Raises:
        PluginValidationError: Si ``strict=True`` et au moins un échec.
        NotADirectoryError: Si ``plugins_dir`` n'existe pas.
    """
    import time

    t0 = time.perf_counter()

    if not plugins_dir.is_dir():
        msg = f"Dossier plugins introuvable : {plugins_dir}"
        raise NotADirectoryError(msg)

    candidates: list[Path] = []
    for entry in sorted(plugins_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("_") and entry.name != "_example":
            continue
        if not (entry / MANIFEST_FILENAME).exists():
            continue
        candidates.append(entry)

    aggregate = AggregateValidationResult()

    if stop_on_error:
        # Mode séquentiel : s'arrête au premier échec
        for path in candidates:
            result = await validate_plugin_async(
                path,
                sandbox=sandbox,
                allow_import=allow_import,
            )
            aggregate.results.append(result)
            if not result.ok:
                break
    else:
        # Mode parallèle : sémaphore pour limiter la concurrence
        sem = asyncio.Semaphore(max_concurrent)

        async def _validate_with_sem(path: Path) -> PluginValidationResult:
            async with sem:
                return await validate_plugin_async(
                    path,
                    sandbox=sandbox,
                    allow_import=allow_import,
                )

        results = await asyncio.gather(
            *(_validate_with_sem(p) for p in candidates),
        )
        aggregate.results.extend(results)

    aggregate.duration_ms = (time.perf_counter() - t0) * 1000.0

    if strict and aggregate.failed_count > 0:
        first = aggregate.failed()[0]
        raise PluginValidationError(first.plugin_name, first)

    return aggregate


def export_report_json(result: PluginValidationResult | AggregateValidationResult) -> str:
    """Exporte un résultat de validation au format JSON.

    Args:
        result: Résultat unique ou agrégé.

    Returns:
        Chaîne JSON indentée (UTF-8 safe).
    """
    if isinstance(result, PluginValidationResult):
        payload: dict[str, Any] = {
            "plugin_name": result.plugin_name,
            "path": str(result.path),
            "ok": result.ok,
            "error_count": result.error_count,
            "warning_count": result.warning_count,
            "duration_ms": result.duration_ms,
            "manifest": result.manifest.model_dump(mode="json") if result.manifest else None,
            "issues": [i.model_dump(mode="json") for i in result.issues],
        }
    else:
        payload = {
            "total": result.total,
            "ok_count": result.ok_count,
            "failed_count": result.failed_count,
            "duration_ms": result.duration_ms,
            "plugins": [
                {
                    "plugin_name": r.plugin_name,
                    "path": str(r.path),
                    "ok": r.ok,
                    "error_count": r.error_count,
                    "warning_count": r.warning_count,
                    "issues": [i.model_dump(mode="json") for i in r.issues],
                }
                for r in result.results
            ],
        }
    return json.dumps(payload, indent=2, ensure_ascii=False)


# ============================================================================
#  Exports publics
# ============================================================================

__all__ = [
    "ALLOWED_HOOKS",
    "ALLOWED_PERMISSIONS",
    "AggregateValidationResult",
    "Category",
    "MANIFEST_FILENAME",
    "PLUGIN_API_VERSION",
    "PluginAuthor",
    "PluginDependency",
    "PluginManifest",
    "PluginManifestError",
    "PluginValidationError",
    "PluginValidationResult",
    "Severity",
    "ValidationIssue",
    "export_report_json",
    "validate_all",
    "validate_all_async",
    "validate_api_version",
    "validate_ast_safety",
    "validate_dependencies",
    "validate_entrypoint_import",
    "validate_entrypoint_symbol",
    "validate_files",
    "validate_hooks",
    "validate_manifest_schema",
    "validate_permissions",
    "validate_plugin",
    "validate_plugin_async",
]
