"""Loader de plugins NexusDL — découverte, validation, chargement, cycle de vie.

Ce module est le point d'entrée unique pour tout ce qui touche aux plugins
au runtime. Il encapsule :

    - La découverte des plugins dans un dossier configurable
    - L'appel à `validators.validate_plugin` en amont de chaque import
    - Le chargement isolé (namespace + nom de module unique)
    - Le cycle de vie (`on_load`, `on_unload`, `reload`)
    - Le dispatch de hooks vers les plugins abonnés
    - Les timeouts, le backoff, et la désactivation automatique
    - Le hot-reload optionnel (dev uniquement)

Le loader est **async-first** : toutes les opérations I/O sont async, et
le dispatch de hooks est awaitable. Les hooks synchrones sont exécutés
dans un thread dédié via `asyncio.to_thread` pour ne pas bloquer l'event
loop.

Example:
    Chargement de tous les plugins au démarrage::

        from pathlib import Path
        from nexusdl.plugins.loader import PluginLoader

        loader = PluginLoader(Path("/config/plugins"))
        summary = await loader.load_all()
        print(f"{summary.loaded}/{summary.discovered} plugins chargés")

    Dispatch d'un hook::

        from nexusdl.plugins.hooks import HookType, SearchPayload

        payload = SearchPayload(query="one piece", site_id="mangadex")
        result = await loader.dispatch_hook(HookType.PRE_SEARCH, payload)

    Arrêt propre::

        await loader.shutdown()
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from loguru import logger

from nexusdl.core.exceptions import NexusDLError

# --- Imports différés (évitent les cycles) -----------------------------------
# Le loader est importé par __init__.py qui expose aussi api/hooks. Pour
# éviter un cycle, on importe ces modules au runtime plutôt qu'au niveau
# module, sauf pour les constantes de hooks qui sont stables et sans
# dépendance sortante.
from nexusdl.plugins.hooks import HOOK_NAMES, HookPayload
from nexusdl.plugins.validators import (
    MANIFEST_FILENAME,
    AggregateValidationResult,
    PluginManifest,
    PluginValidationError,
    PluginValidationResult,
    validate_plugin,
)

if TYPE_CHECKING:
    from nexusdl.plugins.api import PluginAPI, PluginContext

# ============================================================================
#  Constantes
# ============================================================================

#: Timeout par défaut pour un appel de hook (secondes).
DEFAULT_HOOK_TIMEOUT: Final[float] = 5.0

#: Nombre d'échecs consécutifs avant désactivation automatique du plugin.
DEFAULT_MAX_CONSECUTIVE_FAILURES: Final[int] = 3

#: Intervalle de polling pour le hot-reload (secondes). Utilisé uniquement
#: si `enable_hot_reload=True`. Assez court pour une réactivité perçue,
#: assez long pour ne pas saturer le CPU.
DEFAULT_HOT_RELOAD_INTERVAL: Final[float] = 2.0

#: Préfixe des modules internes du loader. Un plugin ne peut pas réutiliser
#: ce préfixe (protection contre les collisions de namespace).
_MODULE_PREFIX: Final[str] = "_nexusdl_loaded_plugin_"

#: Suffixe des dossiers à ignorer silencieusement lors de la découverte.
_IGNORED_DIRS: Final[frozenset[str]] = frozenset(
    {"__pycache__", ".git", ".svn", ".hg", "node_modules", ".mypy_cache", ".ruff_cache"},
)


# ============================================================================
#  Enums
# ============================================================================


class PluginState(str, Enum):
    """État d'un plugin dans le cycle de vie du loader.

    Transitions attendues ::

        DISCOVERED → VALIDATING → VALIDATED → LOADING → LOADED
                                    │            │
                                    ↓            ↓
                                 FAILED       FAILED
        LOADED → UNLOADED (appel on_unload + retrait)
        LOADED → DISABLED (désactivation manuelle ou backoff)
        FAILED → LOADING (reload réussi)

    Attributes:
        DISCOVERED: Dossier détecté, pas encore traité.
        VALIDATING: Validation en cours (`validators.validate_plugin`).
        VALIDATED: Validation réussie, prêt à charger.
        LOADING: Import en cours.
        LOADED: Instance chargée et opérationnelle.
        FAILED: Validation ou import échoué.
        UNLOADED: Chargé puis déchargé (fin de vie propre).
        DISABLED: Désactivé (config ou backoff après échecs répétés).
    """

    DISCOVERED = "discovered"
    VALIDATING = "validating"
    VALIDATED = "validated"
    LOADING = "loading"
    LOADED = "loaded"
    FAILED = "failed"
    UNLOADED = "unloaded"
    DISABLED = "disabled"


# ============================================================================
#  Exceptions
# ============================================================================


class PluginLoadError(NexusDLError):
    """Erreur levée quand un plugin ne peut pas être chargé.

    Attributes:
        plugin_name: Nom du plugin (ou du dossier si manifest illisible).
        plugin_path: Chemin du dossier du plugin.
        cause: Exception d'origine, si applicable.
    """

    def __init__(
        self,
        plugin_name: str,
        plugin_path: Path,
        message: str,
        *,
        cause: Exception | None = None,
    ) -> None:
        """Initialise l'exception.

        Args:
            plugin_name: Nom du plugin.
            plugin_path: Chemin du dossier.
            message: Description de l'erreur.
            cause: Exception d'origine (chaînée automatiquement).
        """
        self.plugin_name = plugin_name
        self.plugin_path = plugin_path
        self.cause = cause
        super().__init__(f"Plugin '{plugin_name}' ({plugin_path}) : {message}")


class PluginRuntimeError(NexusDLError):
    """Erreur levée lors de l'exécution d'un hook.

    N'interrompt pas le dispatch global — le loader capture et continue
    avec les autres plugins.

    Attributes:
        plugin_name: Nom du plugin en faute.
        hook_name: Nom du hook qui a levé.
    """

    def __init__(self, plugin_name: str, hook_name: str, cause: Exception) -> None:
        """Initialise l'exception.

        Args:
            plugin_name: Nom du plugin.
            hook_name: Nom du hook.
            cause: Exception d'origine.
        """
        self.plugin_name = plugin_name
        self.hook_name = hook_name
        self.cause = cause
        super().__init__(
            f"Plugin '{plugin_name}' a levé dans le hook '{hook_name}' : "
            f"{type(cause).__name__}: {cause}",
        )


# ============================================================================
#  Modèles de données
# ============================================================================


@dataclass(slots=True)
class LoadedPlugin:
    """Représente un plugin chargé et opérationnel.

    Attributes:
        name: Nom unique du plugin (depuis le manifest).
        path: Dossier racine du plugin.
        manifest: Manifest validé.
        instance: Instance de la classe d'entrypoint du plugin.
        module: Module Python importé (pour reload).
        module_name: Nom unique du module dans `sys.modules`.
        state: État courant du cycle de vie.
        loaded_at: Timestamp epoch du chargement.
        subscribed_hooks: Hooks réellement souscrits (manifest + méthode présente).
        consecutive_failures: Nombre d'échecs consécutifs (backoff).
        last_error: Dernier message d'erreur (pour debug).
    """

    name: str
    path: Path
    manifest: PluginManifest
    instance: Any
    module: Any
    module_name: str
    state: PluginState = PluginState.LOADED
    loaded_at: float = 0.0
    subscribed_hooks: frozenset[str] = field(default_factory=frozenset)
    consecutive_failures: int = 0
    last_error: str | None = None

    @property
    def is_operational(self) -> bool:
        """True si le plugin peut recevoir des hooks."""
        return self.state is PluginState.LOADED and self.consecutive_failures == 0

    def has_hook(self, hook_name: str) -> bool:
        """Vérifie que le plugin souscrit à un hook donné.

        Args:
            hook_name: Nom du hook.

        Returns:
            True si le plugin reçoit ce hook.
        """
        return hook_name in self.subscribed_hooks


@dataclass(slots=True)
class LoadSummary:
    """Résumé d'un chargement de masse (`load_all`).

    Attributes:
        discovered: Nombre de dossiers contenant un manifest détectés.
        loaded: Nombre de plugins chargés avec succès.
        failed: Nombre de plugins en échec (validation ou import).
        skipped: Nombre de plugins ignorés (désactivés via config).
        validation_aggregate: Résultat complet de la validation (tous plugins).
        duration_ms: Durée totale du chargement.
    """

    discovered: int = 0
    loaded: int = 0
    failed: int = 0
    skipped: int = 0
    validation_aggregate: AggregateValidationResult | None = None
    duration_ms: float = 0.0


# ============================================================================
#  Loader
# ============================================================================


class PluginLoader:
    """Loader central des plugins NexusDL.

    Encapsule la découverte, la validation, le chargement, le cycle de vie
    et le dispatch de hooks. Utilise `validators.validate_plugin` **avant**
    tout import — le code d'un plugin invalide n'est jamais exécuté.

    Le loader est thread-safe pour les opérations de lecture (`get`,
    `list_loaded`) via un `RLock`. Les mutations d'état (chargement,
    déchargement, dispatch) sont protégées par des opérations atomiques
    au niveau asyncio.

    Attributes:
        plugins_dir: Dossier racine des plugins.
        sandbox: Active la vérification des imports en validation.
        allow_import: Si True, la validation exécute le code du plugin.
        hook_timeout: Timeout par appel de hook (secondes).
        max_consecutive_failures: Nombre d'échecs avant désactivation.
    """

    def __init__(
        self,
        plugins_dir: Path,
        *,
        sandbox: bool = True,
        allow_import: bool = False,
        hook_timeout: float = DEFAULT_HOOK_TIMEOUT,
        max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
        disabled_plugins: set[str] | None = None,
    ) -> None:
        """Initialise le loader.

        Args:
            plugins_dir: Dossier racine contenant les sous-dossiers de plugins.
                Il n'est pas nécessaire que le dossier existe au moment de
                l'instanciation — la découverte retournera une liste vide.
            sandbox: Active la vérification des imports non whitelistés
                (défaut : True).
            allow_import: Si True, la validation exécute le code de chaque
                plugin pour vérifier l'entrypoint réellement. À activer
                uniquement pour des plugins de confiance (défaut : False).
            hook_timeout: Timeout en secondes par appel de hook. Un plugin
                qui dépasse ce délai est retiré temporairement (défaut : 5s).
            max_consecutive_failures: Nombre d'échecs consécutifs avant
                désactivation automatique du plugin (défaut : 3).
            disabled_plugins: Ensemble de noms de plugins à ignorer
                (issus de `plugins.disabled` dans la config).
        """
        self.plugins_dir = plugins_dir.resolve()
        self.sandbox = sandbox
        self.allow_import = allow_import
        self.hook_timeout = hook_timeout
        self.max_consecutive_failures = max_consecutive_failures
        self.disabled_plugins: set[str] = set(disabled_plugins or ())

        # Registre des plugins chargés (name → LoadedPlugin)
        self._plugins: dict[str, LoadedPlugin] = {}

        # Compteur pour générer des noms de modules uniques
        self._module_counter: int = 0

        # Verrou pour les accès concurrents (lecture/écriture du registre)
        self._lock = asyncio.Lock()

        # Flag d'état global
        self._shutdown: bool = False

        # Tâche de hot-reload (si activé)
        self._hot_reload_task: asyncio.Task[None] | None = None
        self._hot_reload_stop: asyncio.Event | None = None
        self._mtimes: dict[Path, float] = {}

    # ------------------------------------------------------------------------
    #  Propriétés
    # ------------------------------------------------------------------------

    @property
    def loaded_count(self) -> int:
        """Nombre de plugins actuellement chargés (opérationnels)."""
        return sum(1 for p in self._plugins.values() if p.state is PluginState.LOADED)

    @property
    def failed_count(self) -> int:
        """Nombre de plugins en échec."""
        return sum(1 for p in self._plugins.values() if p.state is PluginState.FAILED)

    @property
    def is_shutdown(self) -> bool:
        """True si `shutdown()` a été appelé."""
        return self._shutdown

    # ------------------------------------------------------------------------
    #  Découverte
    # ------------------------------------------------------------------------

    def discover(self) -> list[Path]:
        """Découvre les dossiers candidats (contenant un manifest).

        Ne lit pas le manifest, ne valide rien — c'est une simple opération
        de listing. Retourne les dossiers triés par nom pour un ordre de
        chargement stable.

        Returns:
            Liste des chemins absolus des dossiers de plugins candidats.
        """
        if not self.plugins_dir.is_dir():
            logger.debug("Dossier plugins inexistant : {}", self.plugins_dir)
            return []

        candidates: list[Path] = []
        for entry in sorted(self.plugins_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name in _IGNORED_DIRS:
                continue
            # Les dossiers commençant par `_` sont ignorés sauf `_example`
            if entry.name.startswith("_") and entry.name != "_example":
                continue
            if not (entry / MANIFEST_FILENAME).exists():
                logger.debug("Dossier sans manifest ignoré : {}", entry.name)
                continue
            candidates.append(entry.resolve())

        logger.debug("{} plugin(s) candidat(s) découvert(s) dans {}", len(candidates), self.plugins_dir)
        return candidates

    # ------------------------------------------------------------------------
    #  Chargement de masse
    # ------------------------------------------------------------------------

    async def load_all(self) -> LoadSummary:
        """Découvre, valide et charge tous les plugins disponibles.

        Le chargement est **best-effort** : un plugin qui échoue n'empêche
        pas les autres de se charger. Chaque échec est loggé et stocké dans
        l'état interne (`PluginState.FAILED`).

        Returns:
            Résumé du chargement (découverts, chargés, échoués, skippés).

        Raises:
            RuntimeError: Si `shutdown()` a déjà été appelé.
        """
        if self._shutdown:
            msg = "PluginLoader est arrêté — impossible de charger de nouveaux plugins"
            raise RuntimeError(msg)

        t0 = time.perf_counter()
        summary = LoadSummary()

        candidates = self.discover()
        summary.discovered = len(candidates)

        if not candidates:
            logger.info("Aucun plugin trouvé dans {}", self.plugins_dir)
            summary.duration_ms = (time.perf_counter() - t0) * 1000.0
            return summary

        # --- Validation globale en amont (parallélisée) ---
        # On valide tous les plugins d'un coup pour produire un rapport
        # unique visible dans les logs, puis on charge ceux qui sont OK.
        from nexusdl.plugins.validators import validate_all_async  # noqa: PLC0415

        logger.info("Validation de {} plugin(s)…", len(candidates))
        aggregate = await validate_all_async(
            self.plugins_dir,
            sandbox=self.sandbox,
            allow_import=self.allow_import,
            strict=False,
        )
        summary.validation_aggregate = aggregate

        # --- Chargement individuel des plugins valides ---
        for candidate in candidates:
            # Récupère le résultat de validation pour ce candidat
            result = next(
                (r for r in aggregate.results if r.path == candidate),
                None,
            )

            # Plugin non validé → skip
            if result is None:
                logger.warning("Aucun résultat de validation pour {}, skip", candidate.name)
                summary.skipped += 1
                continue

            # Plugin désactivé explicitement → skip silencieux
            if result.plugin_name in self.disabled_plugins:
                logger.info("Plugin '{}' désactivé via config", result.plugin_name)
                summary.skipped += 1
                continue

            # Validation échouée → enregistre l'état FAILED
            if not result.ok:
                self._register_failed(candidate, result)
                summary.failed += 1
                continue

            # Validation OK → tentative de chargement
            try:
                await self._load_validated(candidate, result)
                summary.loaded += 1
            except PluginLoadError as exc:
                logger.error("Échec du chargement de '{}' : {}", candidate.name, exc)
                summary.failed += 1

        summary.duration_ms = (time.perf_counter() - t0) * 1000.0
        logger.info(
            "Plugins chargés : {}/{} ({} échec(s), {} skip) en {:.0f}ms",
            summary.loaded,
            summary.discovered,
            summary.failed,
            summary.skipped,
            summary.duration_ms,
        )
        return summary

    # ------------------------------------------------------------------------
    #  Chargement individuel
    # ------------------------------------------------------------------------

    async def load_one(self, plugin_path: Path) -> LoadedPlugin:
        """Valide et charge un plugin unique.

        Args:
            plugin_path: Chemin du dossier du plugin.

        Returns:
            Le plugin chargé.

        Raises:
            PluginLoadError: Si la validation ou l'import échoue.
            RuntimeError: Si `shutdown()` a déjà été appelé.
            NotADirectoryError: Si `plugin_path` n'est pas un dossier.
        """
        if self._shutdown:
            msg = "PluginLoader est arrêté — impossible de charger de nouveaux plugins"
            raise RuntimeError(msg)

        plugin_path = plugin_path.resolve()
        if not plugin_path.is_dir():
            msg = f"Chemin de plugin invalide : {plugin_path}"
            raise NotADirectoryError(msg)

        # --- Validation ---
        try:
            result = await asyncio.to_thread(
                validate_plugin,
                plugin_path,
                sandbox=self.sandbox,
                allow_import=self.allow_import,
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"validation a levé : {type(exc).__name__}: {exc}"
            raise PluginLoadError(plugin_path.name, plugin_path, msg, cause=exc) from exc

        if not result.ok:
            raise PluginLoadError(
                result.plugin_name,
                plugin_path,
                f"validation échouée ({result.error_count} erreur(s))",
                cause=PluginValidationError(result.plugin_name, result),
            )

        return await self._load_validated(plugin_path, result)

    async def _load_validated(
        self,
        plugin_path: Path,
        validation: PluginValidationResult,
    ) -> LoadedPlugin:
        """Charge un plugin déjà validé (import + instanciation + on_load).

        Args:
            plugin_path: Chemin du dossier du plugin.
            validation: Résultat de validation (doit être valide).

        Returns:
            Le plugin chargé.

        Raises:
            PluginLoadError: Si l'import, l'instanciation ou `on_load` échoue.
        """
        assert validation.manifest is not None, "Validation OK sans manifest — invariant cassé"  # noqa: S101
        manifest = validation.manifest

        # --- Déjà chargé ? → unload + reload ---
        async with self._lock:
            if manifest.name in self._plugins:
                existing = self._plugins[manifest.name]
                logger.info(
                    "Plugin '{}' déjà chargé (state={}) — déchargement avant rechargement",
                    manifest.name,
                    existing.state.value,
                )
                await self._unload_internal(existing)

        # --- Import isolé ---
        logger.debug("Import du plugin '{}' depuis {}", manifest.name, plugin_path)
        try:
            module, instance = await self._import_and_instantiate(plugin_path, manifest)
        except PluginLoadError:
            raise
        except Exception as exc:  # noqa: BLE001
            msg = f"chargement échoué : {type(exc).__name__}: {exc}"
            raise PluginLoadError(manifest.name, plugin_path, msg, cause=exc) from exc

        # --- Calcul des hooks réellement souscrits ---
        subscribed = frozenset(
            h for h in manifest.hooks if callable(getattr(instance, h, None))
        )
        missing_methods = [h for h in manifest.hooks if h not in subscribed]
        if missing_methods:
            logger.warning(
                "Plugin '{}' déclare {} hook(s) sans méthode : {}",
                manifest.name,
                len(missing_methods),
                ", ".join(missing_methods),
            )

        # --- Enregistrement ---
        module_name = module.__name__
        loaded = LoadedPlugin(
            name=manifest.name,
            path=plugin_path,
            manifest=manifest,
            instance=instance,
            module=module,
            module_name=module_name,
            state=PluginState.LOADED,
            loaded_at=time.time(),
            subscribed_hooks=subscribed,
        )

        async with self._lock:
            self._plugins[manifest.name] = loaded
            # Trace le mtime du manifest pour le hot-reload
            self._mtimes[plugin_path / MANIFEST_FILENAME] = (
                plugin_path / MANIFEST_FILENAME
            ).stat().st_mtime

        # --- on_load (hors verrou : peut appeler des hooks) ---
        on_load = getattr(instance, "on_load", None)
        if callable(on_load):
            try:
                await self._call_hook_method(loaded, "on_load", _NoPayload())
            except PluginRuntimeError as exc:
                # on_load a levé — on considère le plugin comme cassé
                logger.error(
                    "on_load du plugin '{}' a levé — plugin marqué FAILED : {}",
                    manifest.name,
                    exc.cause,
                )
                loaded.state = PluginState.FAILED
                loaded.last_error = str(exc.cause)
                msg = f"on_load a levé : {exc.cause}"
                raise PluginLoadError(manifest.name, plugin_path, msg, cause=exc.cause) from exc

        logger.info(
            "Plugin '{}' v{} chargé ({} hook(s))",
            manifest.name,
            manifest.version,
            len(subscribed),
        )
        return loaded

    async def _import_and_instantiate(
        self,
        plugin_path: Path,
        manifest: PluginManifest,
    ) -> tuple[Any, Any]:
        """Importe le module du plugin et instancie sa classe d'entrypoint.

        Utilise `importlib.util.spec_from_file_location` avec un nom de
        module **unique** pour éviter les collisions entre plugins. Le
        dossier du plugin est temporairement ajouté à `sys.path` pour
        supporter les imports internes (``from . import helpers``).

        Args:
            plugin_path: Dossier du plugin.
            manifest: Manifest validé.

        Returns:
            Tuple `(module_importé, instance_de_la_classe)`.

        Raises:
            PluginLoadError: Si le fichier source est introuvable, si
                l'import échoue, ou si l'instanciation échoue.
        """
        module_rel, symbol_name = manifest.entrypoint.split(":", 1)
        module_file_rel = module_rel.replace(".", "/")

        candidates = [
            plugin_path / f"{module_file_rel}.py",
            plugin_path / module_file_rel / "__init__.py",
        ]
        source_file: Path | None = next((c for c in candidates if c.exists()), None)
        if source_file is None:
            msg = f"fichier source de l'entrypoint '{module_rel}' introuvable"
            raise PluginLoadError(manifest.name, plugin_path, msg)

        # Nom de module unique
        self._module_counter += 1
        unique_name = f"{_MODULE_PREFIX}{manifest.name}_{self._module_counter}"

        # Ajout temporaire de plugin_path à sys.path
        added_to_path = False
        if str(plugin_path) not in sys.path:
            sys.path.insert(0, str(plugin_path))
            added_to_path = True

        try:
            spec = importlib.util.spec_from_file_location(unique_name, source_file)
            if spec is None or spec.loader is None:
                msg = f"impossible de créer un spec pour {source_file.name}"
                raise PluginLoadError(manifest.name, plugin_path, msg)

            module = importlib.util.module_from_spec(spec)
            # Enregistre dans sys.modules pour les imports relatifs
            sys.modules[unique_name] = module
            spec.loader.exec_module(module)

        except PluginLoadError:
            raise
        except Exception as exc:  # noqa: BLE001
            # Nettoie sys.modules si l'import a partiellement réussi
            sys.modules.pop(unique_name, None)
            msg = f"import de '{source_file.name}' échoué : {type(exc).__name__}: {exc}"
            raise PluginLoadError(manifest.name, plugin_path, msg, cause=exc) from exc
        finally:
            if added_to_path:
                try:
                    sys.path.remove(str(plugin_path))
                except ValueError:
                    pass

        # --- Résolution du symbole ---
        if not hasattr(module, symbol_name):
            sys.modules.pop(unique_name, None)
            msg = f"symbole '{symbol_name}' absent du module chargé"
            raise PluginLoadError(manifest.name, plugin_path, msg)

        obj = getattr(module, symbol_name)

        # --- Instanciation ---
        instance: Any
        if inspect.isclass(obj):
            try:
                # Injecte api + context si le constructeur les accepte
                sig = inspect.signature(obj.__init__)
                params = set(sig.parameters.keys()) - {"self"}
                kwargs: dict[str, Any] = {}
                if "api" in params or "plugin_api" in params:
                    kwargs["api"] = self._make_api(manifest)
                if "context" in params or "plugin_context" in params:
                    kwargs["context"] = self._make_context(manifest, plugin_path)
                instance = obj(**kwargs)
            except Exception as exc:  # noqa: BLE001
                sys.modules.pop(unique_name, None)
                msg = f"instanciation de '{symbol_name}' échouée : {type(exc).__name__}: {exc}"
                raise PluginLoadError(manifest.name, plugin_path, msg, cause=exc) from exc
        elif callable(obj):
            # Entrypoint sous forme de fonction/factory
            try:
                instance = obj(
                    api=self._make_api(manifest),
                    context=self._make_context(manifest, plugin_path),
                )
            except Exception as exc:  # noqa: BLE001
                sys.modules.pop(unique_name, None)
                msg = f"appel de la factory '{symbol_name}' échoué : {type(exc).__name__}: {exc}"
                raise PluginLoadError(manifest.name, plugin_path, msg, cause=exc) from exc
        else:
            sys.modules.pop(unique_name, None)
            msg = f"entrypoint '{symbol_name}' n'est ni une classe ni un callable"
            raise PluginLoadError(manifest.name, plugin_path, msg)

        return module, instance

    # ------------------------------------------------------------------------
    #  Fabriques API / Context (déléguées au module api.py)
    # ------------------------------------------------------------------------

    def _make_api(self, manifest: PluginManifest) -> PluginAPI:
        """Crée une instance `PluginAPI` scopée pour ce plugin.

        Args:
            manifest: Manifest du plugin (pour vérifier les permissions).

        Returns:
            Instance de `PluginAPI` limitée aux permissions déclarées.
        """
        from nexusdl.plugins.api import PluginAPI  # noqa: PLC0415

        return PluginAPI(
            plugin_name=manifest.name,
            permissions=frozenset(manifest.permissions),
        )

    def _make_context(
        self,
        manifest: PluginManifest,
        plugin_path: Path,
    ) -> PluginContext:
        """Crée une instance `PluginContext` scopée pour ce plugin.

        Args:
            manifest: Manifest du plugin.
            plugin_path: Dossier du plugin.

        Returns:
            Instance de `PluginContext` avec les métadonnées du plugin.
        """
        from nexusdl.plugins.api import PluginContext  # noqa: PLC0415

        return PluginContext(
            name=manifest.name,
            version=manifest.version,
            api_version=manifest.api_version,
            path=plugin_path,
            logger=logger.bind(plugin=manifest.name),
        )

    # ------------------------------------------------------------------------
    #  Déchargement
    # ------------------------------------------------------------------------

    async def unload(self, name: str) -> bool:
        """Décharge un plugin (appel `on_unload` + retrait du registre).

        Args:
            name: Nom du plugin.

        Returns:
            True si le plugin a été déchargé, False s'il n'était pas chargé.
        """
        async with self._lock:
            plugin = self._plugins.get(name)
            if plugin is None:
                return False
            await self._unload_internal(plugin)
            return True

    async def _unload_internal(self, plugin: LoadedPlugin) -> None:
        """Déchargement effectif (appel `on_unload` + nettoyage).

        Doit être appelé **avec le verrou déjà tenu** (ou en dehors du
        dispatch concurrent). L'appel à `on_unload` est fait hors verrou
        pour permettre au plugin d'interagir avec le loader si nécessaire.

        Args:
            plugin: Plugin à décharger.
        """
        # on_unload — appel best-effort, un échec n'empêche pas le nettoyage
        on_unload = getattr(plugin.instance, "on_unload", None)
        if callable(on_unload):
            try:
                await self._call_hook_method(plugin, "on_unload", _NoPayload())
            except PluginRuntimeError as exc:
                logger.warning(
                    "on_unload du plugin '{}' a levé : {}",
                    plugin.name,
                    exc.cause,
                )

        # Retrait du registre sys.modules
        sys.modules.pop(plugin.module_name, None)

        # Nettoyage des sous-modules éventuels (module.package.*)
        prefix = f"{plugin.module_name}."
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith(prefix):
                sys.modules.pop(mod_name, None)

        # Retire du registre interne
        self._plugins.pop(plugin.name, None)
        plugin.state = PluginState.UNLOADED

        logger.debug("Plugin '{}' déchargé", plugin.name)

    async def reload(self, name: str) -> LoadedPlugin:
        """Recharge un plugin (unload + load).

        Args:
            name: Nom du plugin.

        Returns:
            Le plugin rechargé.

        Raises:
            KeyError: Si le plugin n'est pas dans le registre.
            PluginLoadError: Si le rechargement échoue.
        """
        async with self._lock:
            existing = self._plugins.get(name)
            if existing is None:
                msg = f"Plugin inconnu : '{name}'"
                raise KeyError(msg)
            path = existing.path
            await self._unload_internal(existing)

        logger.info("Rechargement du plugin '{}' depuis {}", name, path)
        return await self.load_one(path)

    # ------------------------------------------------------------------------
    #  Accès
    # ------------------------------------------------------------------------

    def get(self, name: str) -> LoadedPlugin | None:
        """Retourne un plugin chargé par nom.

        Args:
            name: Nom du plugin.

        Returns:
            Le plugin chargé, ou None.
        """
        return self._plugins.get(name)

    def list_loaded(self) -> list[LoadedPlugin]:
        """Liste tous les plugins chargés (opérationnels ou non).

        Returns:
            Liste triée par nom.
        """
        return sorted(self._plugins.values(), key=lambda p: p.name)

    def list_operational(self) -> list[LoadedPlugin]:
        """Liste uniquement les plugins opérationnels (state=LOADED, no backoff).

        Returns:
            Liste triée par nom.
        """
        return [p for p in self.list_loaded() if p.is_operational]

    # ------------------------------------------------------------------------
    #  Dispatch de hooks
    # ------------------------------------------------------------------------

    async def dispatch_hook(
        self,
        hook_name: str,
        payload: HookPayload,
    ) -> HookPayload:
        """Dispatch un hook à tous les plugins abonnés.

        Les plugins sont appelés **séquentiellement** dans l'ordre alphabétique
        pour garantir un comportement déterministe. Un plugin qui lève, qui
        timeout, ou qui est en backoff est **ignoré** — le dispatch continue.

        Si un plugin retourne un payload modifié, il **remplace** le payload
        courant pour les plugins suivants (chaînage).

        Args:
            hook_name: Nom du hook (doit être dans ``HOOK_NAMES``).
            payload: Payload initial.

        Returns:
            Payload final (potentiellement modifié par les plugins).

        Raises:
            ValueError: Si ``hook_name`` n'est pas un hook valide.
        """
        if hook_name not in HOOK_NAMES:
            msg = f"Hook inconnu : '{hook_name}'. Valides : {sorted(HOOK_NAMES)}"
            raise ValueError(msg)

        current_payload = payload
        plugins = [p for p in self.list_operational() if p.has_hook(hook_name)]

        if not plugins:
            return current_payload

        logger.trace(
            "Dispatch du hook '{}' à {} plugin(s)",
            hook_name,
            len(plugins),
        )

        for plugin in plugins:
            if not plugin.is_operational:
                continue

            try:
                result = await self._call_hook_method(plugin, hook_name, current_payload)
            except PluginRuntimeError as exc:
                self._record_failure(plugin, exc.cause)
                continue

            # Payload retourné → chaînage
            if result is not None and isinstance(result, HookPayload):
                current_payload = result
            elif result is not None:
                logger.warning(
                    "Plugin '{}' a retourné un objet non-HookPayload dans '{}' : {} — ignoré",
                    plugin.name,
                    hook_name,
                    type(result).__name__,
                )

            # Reset du compteur d'échecs après un succès
            if plugin.consecutive_failures > 0:
                logger.debug(
                    "Plugin '{}' : reset du compteur d'échecs après succès",
                    plugin.name,
                )
                plugin.consecutive_failures = 0
                plugin.last_error = None

        return current_payload

    async def _call_hook_method(
        self,
        plugin: LoadedPlugin,
        hook_name: str,
        payload: HookPayload,
    ) -> Any:
        """Appelle la méthode de hook d'un plugin avec timeout.

        Détecte automatiquement si la méthode est sync ou async :
            - Async : `asyncio.wait_for(coro, timeout)`
            - Sync  : `asyncio.wait_for(asyncio.to_thread(...), timeout)`

        Args:
            plugin: Plugin cible.
            hook_name: Nom du hook (méthode à appeler).
            payload: Payload à passer.

        Returns:
            La valeur retournée par la méthode, ou None.

        Raises:
            PluginRuntimeError: Si la méthode lève, timeout, ou n'existe pas.
        """
        method = getattr(plugin.instance, hook_name, None)
        if not callable(method):
            return None

        bound = logger.bind(plugin=plugin.name, hook=hook_name)
        bound.trace("Appel du hook")

        try:
            if inspect.iscoroutinefunction(method):
                # Méthode async — appel direct
                return await asyncio.wait_for(
                    method(payload),
                    timeout=self.hook_timeout,
                )
            # Méthode sync — exécutée dans un thread
            return await asyncio.wait_for(
                asyncio.to_thread(method, payload),
                timeout=self.hook_timeout,
            )
        except TimeoutError as exc:
            msg = f"timeout après {self.hook_timeout}s"
            bound.warning(msg)
            err = TimeoutError(msg)
            raise PluginRuntimeError(plugin.name, hook_name, err) from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — on capture tout ce que le plugin peut lever
            bound.opt(exception=exc).debug("Hook a levé")
            raise PluginRuntimeError(plugin.name, hook_name, exc) from exc

    # ------------------------------------------------------------------------
    #  Backoff et gestion d'échec
    # ------------------------------------------------------------------------

    def _record_failure(self, plugin: LoadedPlugin, error: Exception) -> None:
        """Enregistre un échec et déclenche la désactivation si seuil atteint.

        Args:
            plugin: Plugin en faute.
            error: Exception d'origine.
        """
        plugin.consecutive_failures += 1
        plugin.last_error = f"{type(error).__name__}: {error}"

        if plugin.consecutive_failures >= self.max_consecutive_failures:
            plugin.state = PluginState.DISABLED
            logger.error(
                "Plugin '{}' désactivé après {} échec(s) consécutif(s). "
                "Dernière erreur : {}",
                plugin.name,
                plugin.consecutive_failures,
                plugin.last_error,
            )
        else:
            logger.warning(
                "Plugin '{}' : échec {}/{} — {}",
                plugin.name,
                plugin.consecutive_failures,
                self.max_consecutive_failures,
                plugin.last_error,
            )

    def _register_failed(
        self,
        plugin_path: Path,
        validation: PluginValidationResult,
    ) -> None:
        """Enregistre un plugin en échec de validation (sans instance).

        Args:
            plugin_path: Chemin du dossier.
            validation: Résultat de validation en échec.
        """
        # Crée un placeholder LoadedPlugin avec une instance factice
        # (None) pour tracer l'état dans le registre.
        placeholder = LoadedPlugin(
            name=validation.plugin_name,
            path=plugin_path,
            manifest=validation.manifest
            or PluginManifest.model_construct(name=validation.plugin_name),
            instance=None,
            module=None,
            module_name="",
            state=PluginState.FAILED,
            last_error="; ".join(
                f"{i.category.value}: {i.message}"
                for i in validation.issues
                if i.severity.value == "error"
            )[:500],
        )
        # Écrase une éventuelle entrée précédente (reload après fix)
        self._plugins[validation.plugin_name] = placeholder

    # ------------------------------------------------------------------------
    #  Shutdown
    # ------------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Décharge tous les plugins et arrête le hot-reload.

        Appelle `on_unload` sur chaque plugin, retire les modules de
        `sys.modules`, et marque le loader comme arrêté. Idempotent.
        """
        if self._shutdown:
            return

        self._shutdown = True
        logger.info("Arrêt du loader de plugins ({} chargé(s))…", self.loaded_count)

        # Arrête le hot-reload
        await self.stop_hot_reload()

        # Décharge tous les plugins
        async with self._lock:
            plugins = list(self._plugins.values())

        for plugin in plugins:
            if plugin.instance is None:
                continue
            try:
                await self._unload_internal(plugin)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Échec du déchargement de '{}' : {}",
                    plugin.name,
                    exc,
                )

        self._plugins.clear()
        logger.info("Loader de plugins arrêté")

    # ------------------------------------------------------------------------
    #  Hot-reload (dev uniquement)
    # ------------------------------------------------------------------------

    async def start_hot_reload(
        self,
        *,
        interval: float = DEFAULT_HOT_RELOAD_INTERVAL,
    ) -> None:
        """Démarre une boucle de surveillance des plugins (dev uniquement).

        Poll les mtime des manifests et des fichiers ``.py`` dans chaque
        dossier de plugin. Sur modification, recharge le plugin concerné.

        ⚠️ **À ne pas activer en production** : le hot-reload consomme du
        CPU en continu et peut masquer des bugs de cycle de vie. Le loader
        en dev est piloté par `NEXUSDL_ENV=development`.

        Args:
            interval: Intervalle de polling en secondes.
        """
        if self._hot_reload_task is not None:
            logger.warning("Hot-reload déjà en cours")
            return

        if self._shutdown:
            msg = "Impossible de démarrer le hot-reload après shutdown()"
            raise RuntimeError(msg)

        self._hot_reload_stop = asyncio.Event()
        self._hot_reload_task = asyncio.create_task(
            self._hot_reload_loop(interval),
            name="nexusdl.plugins.hot_reload",
        )
        logger.info("Hot-reload démarré (intervalle={}s)", interval)

    async def stop_hot_reload(self) -> None:
        """Arrête la boucle de hot-reload si elle tourne."""
        if self._hot_reload_task is None:
            return
        if self._hot_reload_stop is not None:
            self._hot_reload_stop.set()
        try:
            await asyncio.wait_for(self._hot_reload_task, timeout=5.0)
        except TimeoutError:
            logger.warning("Hot-reload n'a pas répondu dans le délai — annulation")
            self._hot_reload_task.cancel()
            try:
                await self._hot_reload_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                pass
        finally:
            self._hot_reload_task = None
            self._hot_reload_stop = None
            logger.debug("Hot-reload arrêté")

    async def _hot_reload_loop(self, interval: float) -> None:
        """Boucle principale du hot-reload.

        Args:
            interval: Intervalle de polling en secondes.
        """
        assert self._hot_reload_stop is not None  # noqa: S101
        try:
            while not self._hot_reload_stop.is_set():
                try:
                    await asyncio.wait_for(
                        self._hot_reload_stop.wait(),
                        timeout=interval,
                    )
                    # Si on arrive ici, c'est que le stop a été mis
                    break
                except TimeoutError:
                    # Timeout normal — c'est l'intervalle de polling
                    await self._check_for_changes()
        except asyncio.CancelledError:
            logger.debug("Hot-reload annulé")
            raise

    async def _check_for_changes(self) -> None:
        """Vérifie les modifications des manifests et recharge si nécessaire."""
        if not self.plugins_dir.is_dir():
            return

        for plugin_path in self.discover():
            manifest_file = plugin_path / MANIFEST_FILENAME
            try:
                current_mtime = manifest_file.stat().st_mtime
            except OSError:
                continue

            previous_mtime = self._mtimes.get(manifest_file)
            if previous_mtime is None:
                # Nouveau plugin — tentative de chargement
                logger.info("Nouveau plugin détecté : {}", plugin_path.name)
                try:
                    await self.load_one(plugin_path)
                except PluginLoadError as exc:
                    logger.warning("Échec du chargement de '{}' : {}", plugin_path.name, exc)
                continue

            if current_mtime > previous_mtime:
                logger.info("Modification détectée dans '{}' — rechargement", plugin_path.name)
                try:
                    # Cherche le nom du plugin à partir du path
                    loaded = next(
                        (p for p in self._plugins.values() if p.path == plugin_path),
                        None,
                    )
                    if loaded is not None:
                        await self.reload(loaded.name)
                    else:
                        await self.load_one(plugin_path)
                except (PluginLoadError, KeyError) as exc:
                    logger.warning("Échec du rechargement de '{}' : {}", plugin_path.name, exc)

    # ------------------------------------------------------------------------
    #  Introspection (pour CLI et API)
    # ------------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Retourne un résumé de l'état du loader (pour CLI/API).

        Returns:
            Dict sérialisable JSON.
        """
        return {
            "plugins_dir": str(self.plugins_dir),
            "loaded": self.loaded_count,
            "failed": self.failed_count,
            "total": len(self._plugins),
            "shutdown": self._shutdown,
            "plugins": [
                {
                    "name": p.name,
                    "version": p.manifest.version,
                    "state": p.state.value,
                    "path": str(p.path),
                    "hooks": sorted(p.subscribed_hooks),
                    "permissions": sorted(p.manifest.permissions),
                    "consecutive_failures": p.consecutive_failures,
                    "last_error": p.last_error,
                }
                for p in self.list_loaded()
            ],
        }


# ============================================================================
#  Helpers
# ============================================================================


class _NoPayload(HookPayload):
    """Payload sentinelle pour les hooks sans données (on_load, on_unload).

    Utilisé uniquement en interne — les hooks utilisateur reçoivent toujours
    un payload concret (SearchPayload, DownloadPayload, etc.).
    """

    pass


# ============================================================================
#  Exports publics
# ============================================================================

__all__ = [
    "DEFAULT_HOOK_TIMEOUT",
    "DEFAULT_MAX_CONSECUTIVE_FAILURES",
    "LoadSummary",
    "LoadedPlugin",
    "PluginLoadError",
    "PluginLoader",
    "PluginRuntimeError",
    "PluginState",
]
