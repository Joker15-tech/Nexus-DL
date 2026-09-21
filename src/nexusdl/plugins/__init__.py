"""Système de plugins NexusDL — surface publique.

Ce package implémente l'extension communautaire de NexusDL : un système
de plugins sandboxés, validés, et versionnés qui permet d'ajouter des
comportements personnalisés (hooks, transformations, notificateurs,
intégrations tierces) sans modifier le code core.

Vue d'ensemble
==============

Le système se compose de quatre sous-modules internes, agencés en couches :

    ┌────────────────────────────────────────────────────────────────┐
    │                        __init__.py  (ce fichier)               │
    │                     façade publique + versioning               │
    └────────────────────────────────┬───────────────────────────────┘
                                     │
       ┌─────────────────┬───────────┼─────────────┬─────────────────┐
       │                 │           │             │                 │
       ▼                 ▼           ▼             ▼                 ▼
    loader.py       api.py      hooks.py      validators.py     _example/
    (cycle de    (surface   (payloads +   (schéma du       (plugin de
    vie)         publique)  événements)   manifest)        référence)

**Point d'entrée normal** : `PluginLoader`. Le loader découvre les plugins
dans un dossier, les valide via `validators`, les charge en isolant leur
namespace, et dispatche les hooks via `hooks.HookDispatcher`. Chaque plugin
reçoit une instance de `PluginAPI` scopée à ses permissions.

**Point d'entrée plugin** : `PluginAPI` + `PluginContext`. Un plugin
implémente une classe qui accepte `api` et `context` au constructeur, et
expose des méthodes nommées d'après les hooks (`pre_search`, `on_load`, etc.).

Stabilité et versioning
=======================

Le contrat public de ce package est couvert par `PLUGIN_API_VERSION`
(défini dans `validators.py`, source de vérité). Politique :

    - **Ajout** d'un symbole public  → minor (compatible).
    - **Changement de signature**    → major (breaking).
    - **Retrait** d'un symbole public → major (breaking).

Un plugin qui déclare `api_version: "1.0"` dans son manifest fonctionnera
sur toute API `1.x`. Une API majeure différente est un rejet dur.

Trois constantes doivent rester synchronisées :
    - `validators.PLUGIN_API_VERSION` (source de vérité)
    - `api.PLUGIN_API_VERSION`
    - `hooks.HOOKS_API_VERSION`

Un test de non-régression (`tests/plugins/test_api_stability.py`) vérifie
cette synchronisation.

Exemples
========

**Utilisation programmatique (loader + dispatch)** ::

    from pathlib import Path
    from nexusdl.plugins import PluginLoader, HookType, SearchPayload

    loader = PluginLoader(Path("/config/plugins"))
    summary = await loader.load_all()
    print(f"{summary.loaded}/{summary.discovered} plugins chargés")

    payload = SearchPayload(query="one piece", site_id="mangadex")
    result = await loader.dispatch_hook(HookType.PRE_SEARCH.value, payload)

**Validation d'un plugin (sans chargement)** ::

    from pathlib import Path
    from nexusdl.plugins import validate_plugin

    result = validate_plugin(Path("plugins/my_plugin"), sandbox=True)
    if not result.ok:
        for issue in result.issues:
            print(issue)

**Développement d'un plugin** ::

    from nexusdl.plugins import (
        PluginAPI, PluginContext, SearchPayload,
    )

    class MyPlugin:
        def __init__(self, api: PluginAPI, context: PluginContext) -> None:
            self.api = api
            self.ctx = context

        async def pre_search(self, payload: SearchPayload) -> SearchPayload:
            payload.query = payload.query.strip().lower()
            return payload

    Voir `src/nexusdl/plugins/_example/` pour un exemple complet.

Invariants de sécurité
======================

Le système applique les invariants suivants (vérifiés par `validators.py`) :

    - Aucun plugin n'importe `nexusdl.core.*` directement.
    - Aucun plugin n'utilise `eval`, `exec`, `os.system`, ou l'introspection.
    - Chaque permission demandée est dans la whitelist.
    - Chaque hook déclaré est supporté.
    - Le dossier du plugin ne dépasse pas 10 MiB.
    - Les symlinks sont signalés (peuvent contourner le sandbox).

Voir `src/nexusdl/plugins/nexus.dl` pour la documentation complète.
"""

from __future__ import annotations

# ============================================================================
#  Ordre d'import CRITIQUE — ne pas réordonner
# ============================================================================
# Chaque module importe ses dépendances au niveau module. L'ordre ci-dessous
# respecte le graphe :
#
#     hooks        (feuille — sauf fallback validators)
#     validators   → hooks
#     api          (feuille)
#     loader       → hooks, validators (api en import différé)
#
# Réordonner peut provoquer des ImportError au premier chargement du package.

# --- Couche 1 : hooks (aucune dépendance interne) ---------------------------
from nexusdl.plugins.hooks import (
    DEFAULT_HOOK_TIMEOUT,
    HOOK_METADATA,
    HOOK_NAMES,
    HOOKS_API_VERSION,
    ChapterPayload,
    DownloadPayload,
    ErrorPayload,
    HookDispatcher,
    HookError,
    HookMetadata,
    HookPayload,
    HookTarget,
    HookType,
    LibraryScanPayload,
    LifecyclePayload,
    MangaPayload,
    PackagingPayload,
    SearchPayload,
    is_cancellable,
    make_error_payload,
    make_lifecycle_payload,
    payload_class_for,
)

# --- Couche 2 : validators (dépend de hooks) --------------------------------
from nexusdl.plugins.validators import (
    ALLOWED_HOOKS,
    ALLOWED_PERMISSIONS,
    MANIFEST_FILENAME,
    PLUGIN_API_VERSION,
    AggregateValidationResult,
    Category,
    PluginAuthor,
    PluginDependency,
    PluginManifest,
    PluginManifestError,
    PluginValidationError,
    PluginValidationResult,
    Severity,
    ValidationIssue,
    export_report_json,
    validate_all,
    validate_all_async,
    validate_api_version,
    validate_ast_safety,
    validate_dependencies,
    validate_entrypoint_import,
    validate_entrypoint_symbol,
    validate_files,
    validate_hooks,
    validate_manifest_schema,
    validate_permissions,
    validate_plugin,
    validate_plugin_async,
)

# --- Couche 3 : api (feuille) -----------------------------------------------
from nexusdl.plugins.api import (
    DEFAULT_HTTP_TIMEOUT,
    MAX_FILE_WRITE_BYTES,
    MAX_HTTP_RESPONSE_BYTES,
    MAX_HTTP_TIMEOUT,
    MAX_LIBRARY_PAGE_SIZE,
    ConfigAccessor,
    ConfigBackend,
    FilesystemAccessor,
    HttpAccessor,
    HttpBackend,
    HttpResponse,
    LibraryAccessor,
    LibraryBackend,
    MangaInfo,
    NotificationBackend,
    NotificationLevel,
    NotificationResult,
    Notifier,
    Permission,
    PermissionDenied,
    PluginAPI,
    PluginAPIError,
    PluginContext,
    SandboxViolation,
    ServiceUnavailable,
    make_api_for_plugin,
)

# --- Couche 4 : loader (dépend de hooks + validators) -----------------------
from nexusdl.plugins.loader import (
    DEFAULT_HOOK_TIMEOUT as LOADER_DEFAULT_HOOK_TIMEOUT,
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    LoadSummary,
    LoadedPlugin,
    PluginLoadError,
    PluginLoader,
    PluginRuntimeError,
    PluginState,
)

# ============================================================================
#  Ré-exports du plugin d'exemple
# ============================================================================
# Le plugin d'exemple est importable via `from nexusdl.plugins import ExamplePlugin`
# pour faciliter les tests et les notebooks qui veulent voir un exemple concret.
# L'import reste sans effet de bord (pas d'exécution du cycle de vie).

from nexusdl.plugins._example import ExamplePlugin

# ============================================================================
#  Métadonnées du package
# ============================================================================

#: Version du système de plugins — identique à `PLUGIN_API_VERSION`.
#: Exposée sous les deux noms pour compatibilité avec les conventions
#: (les consommateurs peuvent utiliser soit `PLUGIN_API_VERSION`, soit
#: `__api_version__`).
__api_version__: str = PLUGIN_API_VERSION

#: Version courte du package pour introspection.
__version__: str = "0.1.0"


# ============================================================================
#  Helpers publics
# ============================================================================


def get_api_version() -> str:
    """Retourne la version courante de l'API plugin.

    Helper trivial mais utile pour les plugins qui veulent afficher ou
    logger la version d'API à leur démarrage.

    Returns:
        Chaîne ``"MAJOR.MINOR"`` (ex: ``"1.0"``).
    """
    return PLUGIN_API_VERSION


def is_hook_supported(hook_name: str) -> bool:
    """Vérifie qu'un nom de hook est supporté par cette version d'API.

    Args:
        hook_name: Nom du hook (ex: ``"pre_search"``).

    Returns:
        True si le hook est dans ``HOOK_NAMES``.
    """
    return hook_name in HOOK_NAMES


def is_permission_allowed(permission: str) -> bool:
    """Vérifie qu'une permission est whitelistée par cette version d'API.

    Args:
        permission: Nom de la permission (ex: ``"network_http"``).

    Returns:
        True si la permission est dans ``ALLOWED_PERMISSIONS``.
    """
    return permission in ALLOWED_PERMISSIONS


def describe_api() -> dict[str, object]:
    """Retourne un dict décrivant la surface publique du système de plugins.

    Utilisé par la CLI (`nexusdl plugins info`) et par l'endpoint
    `/api/plugins/schema` (Phase 14). Évite de dupliquer la liste des
    hooks et permissions autorisés à plusieurs endroits.

    Returns:
        Dict avec :
            - ``api_version`` (str)
            - ``hooks`` (list[str] trié)
            - ``permissions`` (list[str] trié)
            - ``hook_count`` (int)
            - ``permission_count`` (int)
    """
    return {
        "api_version": PLUGIN_API_VERSION,
        "hooks": sorted(HOOK_NAMES),
        "permissions": sorted(ALLOWED_PERMISSIONS),
        "hook_count": len(HOOK_NAMES),
        "permission_count": len(ALLOWED_PERMISSIONS),
    }


# ============================================================================
#  Exports publics
# ============================================================================
# La liste couvre l'intégralité de la surface destinée aux consommateurs
# externes (plugins, CLI, web, tests). Les symboles internes (préfixés `_`
# ou non listés) restent accessibles via leur module d'origine.

__all__ = [
    # --- Constants ---
    "ALLOWED_HOOKS",
    "ALLOWED_PERMISSIONS",
    "DEFAULT_HOOK_TIMEOUT",
    "DEFAULT_HTTP_TIMEOUT",
    "DEFAULT_MAX_CONSECUTIVE_FAILURES",
    "HOOKS_API_VERSION",
    "MAX_FILE_WRITE_BYTES",
    "MAX_HTTP_RESPONSE_BYTES",
    "MAX_HTTP_TIMEOUT",
    "MAX_LIBRARY_PAGE_SIZE",
    "MANIFEST_FILENAME",
    "PLUGIN_API_VERSION",
    # --- Hooks ---
    "ChapterPayload",
    "DownloadPayload",
    "ErrorPayload",
    "HookDispatcher",
    "HookError",
    "HookMetadata",
    "HookPayload",
    "HookTarget",
    "HookType",
    "HOOK_METADATA",
    "HOOK_NAMES",
    "LibraryScanPayload",
    "LifecyclePayload",
    "MangaPayload",
    "PackagingPayload",
    "SearchPayload",
    "is_cancellable",
    "is_hook_supported",
    "make_error_payload",
    "make_lifecycle_payload",
    "payload_class_for",
    # --- Validators ---
    "AggregateValidationResult",
    "Category",
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
    # --- API ---
    "ConfigAccessor",
    "ConfigBackend",
    "FilesystemAccessor",
    "HttpAccessor",
    "HttpBackend",
    "HttpResponse",
    "LibraryAccessor",
    "LibraryBackend",
    "MangaInfo",
    "Notifier",
    "NotificationBackend",
    "NotificationLevel",
    "NotificationResult",
    "Permission",
    "PermissionDenied",
    "PluginAPI",
    "PluginAPIError",
    "PluginContext",
    "SandboxViolation",
    "ServiceUnavailable",
    "is_permission_allowed",
    "make_api_for_plugin",
    # --- Loader ---
    "LoadSummary",
    "LoadedPlugin",
    "PluginLoadError",
    "PluginLoader",
    "PluginRuntimeError",
    "PluginState",
    # --- Example plugin ---
    "ExamplePlugin",
    # --- Package metadata & helpers ---
    "__api_version__",
    "__version__",
    "describe_api",
    "get_api_version",
]
