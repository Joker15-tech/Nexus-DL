"""Hooks et payloads du système de plugins NexusDL.

Ce module définit l'API plugin côté événements :

    - `HookType` : énumération des hooks supportés.
    - `HOOK_NAMES` : frozenset des noms (importé par `validators.py`).
    - `HookPayload` + sous-classes : données échangées avec les plugins.
    - `HOOK_METADATA` : métadonnées par hook (phase, cancellabilité).
    - `HookDispatcher` : dispatch bas niveau vers un plugin unique.

Les payloads sont **découplés des modèles core** (pas d'import `Manga`,
`Chapter`, `SearchResult`) : ils utilisent des primitives et des dicts,
ce qui les rend légers, sérialisables JSON sans conversion, et
manipulables par un plugin sans import lourd.

Example:
    Dispatch manuel d'un hook::

        from nexusdl.plugins.hooks import (
            HookType, SearchPayload, HookDispatcher,
        )

        dispatcher = HookDispatcher()
        payload = SearchPayload(query="one piece", site_id="mangadex")
        result = await dispatcher.call(plugin, HookType.PRE_SEARCH.value, payload)

    Inspecter les métadonnées d'un hook::

        from nexusdl.plugins.hooks import HOOK_METADATA

        meta = HOOK_METADATA["pre_download"]
        print(meta.phase, meta.cancellable)  # "before" True
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Final,
    Literal,
    Protocol,
    runtime_checkable,
)

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# ============================================================================
#  Constantes
# ============================================================================

#: Timeout par défaut pour un appel de hook (secondes).
#: Dupliqué dans `loader.py` — la constante y est utilisée pour la config
#: par défaut du loader, ici pour le dispatcher standalone.
DEFAULT_HOOK_TIMEOUT: Final[float] = 5.0

#: Marqueur de version pour tracer les changements d'API plugin.
#: Doit rester synchronisé avec `PLUGIN_API_VERSION` dans `validators.py`.
HOOKS_API_VERSION: Final[str] = "1.0"


# ============================================================================
#  Enum des hooks
# ============================================================================


class HookType(str, Enum):
    """Hooks supportés par NexusDL.

    Chaque valeur correspond au nom de la méthode qu'un plugin peut
    implémenter pour recevoir l'événement. Les hooks sont regroupés par
    phase :

    **Cycle de vie** :
        - ``ON_LOAD`` : à l'instanciation du plugin.
        - ``ON_UNLOAD`` : au déchargement du plugin.

    **Before (cancellables)** :
        - ``PRE_SEARCH`` : avant une recherche multi-sites.
        - ``PRE_DOWNLOAD`` : avant le téléchargement d'un manga.
        - ``PRE_PACKAGE`` : avant l'empaquetage d'un chapitre.

    **After (non-cancellables)** :
        - ``POST_SEARCH`` : après une recherche (résultats collectés).
        - ``POST_DOWNLOAD`` : après le téléchargement complet.
        - ``POST_PACKAGE`` : après l'empaquetage.

    **Événements** :
        - ``ON_ERROR`` : lorsqu'une erreur est remontée par un composant.
        - ``ON_NEW_CHAPTER`` : lorsqu'un nouveau chapitre est détecté.
        - ``ON_MANGA_UPDATE`` : lorsqu'un manga est mis à jour dans la bibliothèque.
        - ``ON_LIBRARY_SCAN`` : après un scan de la bibliothèque locale.
    """

    # --- Cycle de vie ---
    ON_LOAD = "on_load"
    ON_UNLOAD = "on_unload"

    # --- Before ---
    PRE_SEARCH = "pre_search"
    PRE_DOWNLOAD = "pre_download"
    PRE_PACKAGE = "pre_package"

    # --- After ---
    POST_SEARCH = "post_search"
    POST_DOWNLOAD = "post_download"
    POST_PACKAGE = "post_package"

    # --- Événements ---
    ON_ERROR = "on_error"
    ON_NEW_CHAPTER = "on_new_chapter"
    ON_MANGA_UPDATE = "on_manga_update"
    ON_LIBRARY_SCAN = "on_library_scan"

    def __str__(self) -> str:
        """Retourne la valeur du hook (utilisable comme nom de méthode).

        Returns:
            La chaîne du hook (ex: ``"pre_search"``).
        """
        return self.value


#: Noms canoniques de tous les hooks supportés. Importé par
#: `validators.py` pour valider les manifests, et par `loader.py`
#: pour vérifier qu'un hook dispatché est valide.
HOOK_NAMES: Final[frozenset[str]] = frozenset(h.value for h in HookType)


# ============================================================================
#  Payloads
# ============================================================================


class HookPayload(BaseModel):
    """Classe de base de tous les payloads de hooks.

    Fournit deux champs communs :

        - ``timestamp`` : horodatage UTC de création du payload.
        - ``metadata`` : dict libre pour données additionnelles. Les plugins
          sont encouragés à y stocker leurs données personnalisées plutôt
          que de sous-classer le payload — cela préserve la compatibilité
          d'une version d'API à l'autre.

    Le modèle est **mutable** pour permettre les modifications en place
    (ex: ``payload.query = payload.query.strip().lower()``). Les plugins
    peuvent soit retourner un nouveau payload, soit muter celui reçu et
    le retourner, soit retourner ``None`` pour ne rien changer.

    La validation à l'assignation est activée : une affectation invalide
    (``payload.page = "abc"``) lève ``ValidationError`` immédiatement, ce
    qui facilite le debug d'un plugin.

    Attributes:
        timestamp: Date/heure UTC de création du payload.
        metadata: Données libres pour les plugins. Sérialisable JSON.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        arbitrary_types_allowed=False,
    )

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    def with_metadata(self, **kwargs: Any) -> HookPayload:
        """Retourne une copie du payload avec les métadonnées fusionnées.

        Helper pratique pour la chaîne de hooks : un plugin peut faire
        ``payload = payload.with_metadata(processed_by="my_plugin")`` sans
        muter le payload original.

        Args:
            **kwargs: Clés/valeurs à ajouter ou remplacer dans ``metadata``.

        Returns:
            Nouveau payload avec métadonnées fusionnées.
        """
        merged = {**self.metadata, **kwargs}
        return self.model_copy(update={"metadata": merged})


class SearchPayload(HookPayload):
    """Payload pour ``pre_search`` et ``post_search``.

    En ``pre_search``, le plugin peut modifier la requête, les langues,
    ou les sites ciblés. En ``post_search``, il peut filtrer, trier, ou
    annoter les résultats collectés.

    Les résultats sont stockés sous forme de **dicts** (pas de modèles
    Pydantic). Chaque dict possède au minimum les clés :

        - ``title`` (str) : titre du manga.
        - ``url`` (str) : URL du manga sur le site.
        - ``site_id`` (str) : identifiant du site source.
        - ``cover_url`` (str | None) : URL de la couverture.

    Un plugin peut ajouter d'autres clés (elles seront préservées dans
    les hooks suivants et par l'appelant).

    Attributes:
        query: Terme de recherche.
        site_id: Site ciblé, ou None pour multi-sites.
        languages: Langues cibles (vide = toutes).
        include_adult: Inclure les sites adultes.
        page: Numéro de page (1-indexé).
        results: Résultats collectés (rempli en ``post_search``).
        total_results: Nombre total de résultats retournés par les parsers.
    """

    query: Annotated[str, Field(min_length=1, max_length=500)]
    site_id: str | None = None
    languages: list[str] = Field(default_factory=list)
    include_adult: bool = False
    page: Annotated[int, Field(ge=1)] = 1
    results: list[dict[str, Any]] = Field(default_factory=list)
    total_results: int = 0


class DownloadPayload(HookPayload):
    """Payload pour ``pre_download`` et ``post_download``.

    En ``pre_download``, le plugin peut annuler le téléchargement (en
    levant une exception) ou modifier la destination, le format, la
    sélection de chapitres.

    En ``post_download``, le plugin reçoit les statistiques finales
    (chapitres réussis, erreurs, octets totaux). Il ne peut pas annuler
    le téléchargement — seulement observer.

    Attributes:
        task_id: Identifiant UUID de la tâche de téléchargement (str).
        site_id: Site source.
        manga_id: Identifiant du manga sur le site.
        manga_title: Titre du manga (pour lisibilité des logs).
        chapter_ids: Identifiants des chapitres sélectionnés.
        dest: Dossier de destination.
        format: Format d'empaquetage cible (``"cbz"``, ``"pdf"``, etc.).
        chapters_downloaded: Nombre de chapitres téléchargés (post).
        chapters_failed: Nombre de chapitres en échec (post).
        bytes_downloaded: Octets totaux téléchargés (post).
        duration_seconds: Durée du téléchargement (post).
        output_paths: Chemins des fichiers produits (post).
        errors: Messages d'erreur (post).
    """

    task_id: str
    site_id: str
    manga_id: str
    manga_title: str = ""
    chapter_ids: list[str] = Field(default_factory=list)
    dest: str
    format: str = "cbz"
    chapters_downloaded: int = 0
    chapters_failed: int = 0
    bytes_downloaded: int = 0
    duration_seconds: float = 0.0
    output_paths: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PackagingPayload(HookPayload):
    """Payload pour ``pre_package`` et ``post_package``.

    En ``pre_package``, le plugin peut modifier la liste des pages
    (retirer un scan publicitaire, par exemple), changer le format cible,
    ou injecter des métadonnées.

    En ``post_package``, il reçoit le chemin de sortie et la taille
    finale du fichier produit.

    Attributes:
        chapter_id: Identifiant du chapitre empaqueté.
        chapter_number: Numéro du chapitre (str, peut être "12.5" ou "Extra").
        format: Format d'empaquetage.
        pages: Chemins des pages (chemins locaux, str pour sérialisabilité).
        output_path: Chemin du fichier produit (post).
        output_size_bytes: Taille du fichier produit (post).
        comic_info: Métadonnées ComicInfo.xml sous forme de dict (optionnel).
    """

    chapter_id: str
    chapter_number: str = ""
    format: str = "cbz"
    pages: list[str] = Field(default_factory=list)
    output_path: str | None = None
    output_size_bytes: int = 0
    comic_info: dict[str, Any] = Field(default_factory=dict)


class ErrorPayload(HookPayload):
    """Payload pour ``on_error``.

    Un plugin peut l'utiliser pour logger vers un service externe,
    déclencher une notification, ou tenter une récupération (retry,
    fallback sur un autre site).

    Attributes:
        error_type: Nom de la classe d'exception.
        error_message: Message de l'exception.
        traceback: Traceback formaté (si disponible).
        site_id: Site où l'erreur s'est produite (si applicable).
        task_id: ID de tâche associé (si applicable).
        url: URL en cours de traitement (si applicable).
        recoverable: True si l'appelant estime que l'erreur peut être
            retentée.
        context: Données additionnelles libres (parser, méthode, args...).
    """

    error_type: str
    error_message: str
    traceback: str | None = None
    site_id: str | None = None
    task_id: str | None = None
    url: str | None = None
    recoverable: bool = False
    context: dict[str, Any] = Field(default_factory=dict)


class ChapterPayload(HookPayload):
    """Payload pour ``on_new_chapter``.

    Émis lorsqu'un nouveau chapitre est détecté (via une vérification
    périodique ou une action manuelle de l'utilisateur).

    Attributes:
        site_id: Site source.
        manga_id: Identifiant du manga sur le site.
        manga_title: Titre du manga.
        chapter_id: Identifiant du chapitre.
        chapter_number: Numéro du chapitre (str).
        chapter_title: Titre du chapitre (optionnel).
        chapter_url: URL du chapitre.
        language: Langue du chapitre (code ISO 639-1).
        published_at: Date de publication (ISO 8601 str, si connue).
    """

    site_id: str
    manga_id: str
    manga_title: str = ""
    chapter_id: str
    chapter_number: str = ""
    chapter_title: str = ""
    chapter_url: str = ""
    language: str = ""
    published_at: str | None = None


class MangaPayload(HookPayload):
    """Payload pour ``on_manga_update``.

    Émis lorsqu'un manga est mis à jour dans la bibliothèque locale :
    nouveau chapitre, changement de statut, mise à jour de métadonnées.

    Attributes:
        manga_id: Identifiant interne NexusDL du manga.
        site_id: Site source.
        title: Titre actuel.
        changes: Dict des changements appliqués (clé → {"old": ..., "new": ...}).
        source: Source du changement (``"manual"``, ``"scan"``, ``"import"``).
    """

    manga_id: str
    site_id: str = ""
    title: str = ""
    changes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    source: Literal["manual", "scan", "import", "sync", "unknown"] = "unknown"


class LibraryScanPayload(HookPayload):
    """Payload pour ``on_library_scan``.

    Émis après chaque scan de la bibliothèque locale (manuel ou
    périodique). Permet à un plugin de synchroniser avec un service
    externe (calibre, Kavita, etc.).

    Attributes:
        library_path: Dossier scanné.
        total_mangas: Nombre total de mangas dans la bibliothèque.
        new_mangas: Mangas ajoutés lors de ce scan.
        removed_mangas: Mangas disparus (fichiers supprimés).
        updated_mangas: Mangas dont les métadonnées ont changé.
        duration_seconds: Durée du scan.
        errors: Erreurs rencontrées pendant le scan.
    """

    library_path: str
    total_mangas: int = 0
    new_mangas: int = 0
    removed_mangas: int = 0
    updated_mangas: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = Field(default_factory=list)


class LifecyclePayload(HookPayload):
    """Payload minimal pour ``on_load`` et ``on_unload``.

    Ces hooks n'ont pas de données métier — juste l'occasion pour un
    plugin d'initialiser ou de nettoyer ses ressources. Le payload
    fournit le nom du plugin concerné et son chemin.

    Attributes:
        plugin_name: Nom du plugin (rappel — utile pour les logs).
        plugin_path: Chemin du dossier du plugin.
    """

    plugin_name: str
    plugin_path: str = ""


# ============================================================================
#  Métadonnées des hooks
# ============================================================================


@dataclass(frozen=True, slots=True)
class HookMetadata:
    """Métadonnées descriptives d'un hook.

    Attributes:
        hook: Le type de hook.
        payload_class: Classe de payload attendue par ce hook.
        phase: Phase du cycle de vie — ``"lifecycle"``, ``"before"``,
            ``"after"``, ``"event"``.
        cancellable: Si True, un plugin qui lève annule l'opération en
            cours (le loader propage l'exception à l'appelant).
        description: Description courte (affichée dans la CLI et la doc).
    """

    hook: HookType
    payload_class: type[HookPayload]
    phase: Literal["lifecycle", "before", "after", "event"]
    cancellable: bool
    description: str


#: Tableau de bord des hooks. Source de vérité pour :
#:   - la doc générée (docs/source/development/plugins.md)
#:   - la CLI (`nexusdl plugins hooks`)
#:   - le loader (pour choisir le bon comportement en cas d'erreur)
HOOK_METADATA: Final[dict[str, HookMetadata]] = {
    HookType.ON_LOAD.value: HookMetadata(
        hook=HookType.ON_LOAD,
        payload_class=LifecyclePayload,
        phase="lifecycle",
        cancellable=False,
        description="Appelé au chargement du plugin. Utilisé pour initialiser l'état.",
    ),
    HookType.ON_UNLOAD.value: HookMetadata(
        hook=HookType.ON_UNLOAD,
        payload_class=LifecyclePayload,
        phase="lifecycle",
        cancellable=False,
        description="Appelé au déchargement du plugin. Utilisé pour libérer les ressources.",
    ),
    HookType.PRE_SEARCH.value: HookMetadata(
        hook=HookType.PRE_SEARCH,
        payload_class=SearchPayload,
        phase="before",
        cancellable=True,
        description="Avant une recherche. Modifier query/sites/langues ou annuler.",
    ),
    HookType.POST_SEARCH.value: HookMetadata(
        hook=HookType.POST_SEARCH,
        payload_class=SearchPayload,
        phase="after",
        cancellable=False,
        description="Après une recherche. Filtrer/trier/annoter les résultats.",
    ),
    HookType.PRE_DOWNLOAD.value: HookMetadata(
        hook=HookType.PRE_DOWNLOAD,
        payload_class=DownloadPayload,
        phase="before",
        cancellable=True,
        description="Avant un téléchargement. Modifier la sélection ou annuler.",
    ),
    HookType.POST_DOWNLOAD.value: HookMetadata(
        hook=HookType.POST_DOWNLOAD,
        payload_class=DownloadPayload,
        phase="after",
        cancellable=False,
        description="Après un téléchargement. Observer les statistiques finales.",
    ),
    HookType.PRE_PACKAGE.value: HookMetadata(
        hook=HookType.PRE_PACKAGE,
        payload_class=PackagingPayload,
        phase="before",
        cancellable=True,
        description="Avant un empaquetage. Modifier les pages ou le format cible.",
    ),
    HookType.POST_PACKAGE.value: HookMetadata(
        hook=HookType.POST_PACKAGE,
        payload_class=PackagingPayload,
        phase="after",
        cancellable=False,
        description="Après un empaquetage. Observer le fichier produit.",
    ),
    HookType.ON_ERROR.value: HookMetadata(
        hook=HookType.ON_ERROR,
        payload_class=ErrorPayload,
        phase="event",
        cancellable=False,
        description="Erreur remontée par un composant. Notification/récupération.",
    ),
    HookType.ON_NEW_CHAPTER.value: HookMetadata(
        hook=HookType.ON_NEW_CHAPTER,
        payload_class=ChapterPayload,
        phase="event",
        cancellable=False,
        description="Nouveau chapitre détecté sur un manga suivi.",
    ),
    HookType.ON_MANGA_UPDATE.value: HookMetadata(
        hook=HookType.ON_MANGA_UPDATE,
        payload_class=MangaPayload,
        phase="event",
        cancellable=False,
        description="Manga modifié dans la bibliothèque locale.",
    ),
    HookType.ON_LIBRARY_SCAN.value: HookMetadata(
        hook=HookType.ON_LIBRARY_SCAN,
        payload_class=LibraryScanPayload,
        phase="event",
        cancellable=False,
        description="Scan de la bibliothèque terminé (manuel ou périodique).",
    ),
}


def payload_class_for(hook_name: str) -> type[HookPayload]:
    """Retourne la classe de payload attendue pour un hook.

    Args:
        hook_name: Nom du hook (ex: ``"pre_search"``).

    Returns:
        Classe de payload associée.

    Raises:
        ValueError: Si le hook est inconnu.
    """
    meta = HOOK_METADATA.get(hook_name)
    if meta is None:
        msg = f"Hook inconnu : '{hook_name}'. Valides : {sorted(HOOK_NAMES)}"
        raise ValueError(msg)
    return meta.payload_class


def is_cancellable(hook_name: str) -> bool:
    """Indique si un hook peut annuler l'opération en cours.

    Args:
        hook_name: Nom du hook.

    Returns:
        True si le hook est cancellable.

    Raises:
        ValueError: Si le hook est inconnu.
    """
    meta = HOOK_METADATA.get(hook_name)
    if meta is None:
        msg = f"Hook inconnu : '{hook_name}'"
        raise ValueError(msg)
    return meta.cancellable


# ============================================================================
#  Exceptions
# ============================================================================


class HookError(NexusDLError):
    """Erreur levée lors de l'appel d'un hook sur un plugin.

    Le loader capture cette exception et peut décider de marquer le plugin
    en backoff, mais ne la propage pas à l'appelant du hook (sauf si le
    hook est cancellable et que la politique de dispatch le demande).

    Attributes:
        target_name: Nom du plugin en faute.
        hook_name: Nom du hook.
        cause: Exception d'origine levée par le plugin.
        is_timeout: True si l'erreur est un timeout.
    """

    def __init__(
        self,
        target_name: str,
        hook_name: str,
        cause: Exception,
        *,
        is_timeout: bool = False,
    ) -> None:
        """Initialise l'exception.

        Args:
            target_name: Nom du plugin.
            hook_name: Nom du hook.
            cause: Exception d'origine.
            is_timeout: True si l'exception est un timeout.
        """
        self.target_name = target_name
        self.hook_name = hook_name
        self.cause = cause
        self.is_timeout = is_timeout
        prefix = "Timeout" if is_timeout else "Échec"
        super().__init__(
            f"{prefix} du hook '{hook_name}' sur le plugin '{target_name}' : "
            f"{type(cause).__name__}: {cause}",
        )


# ============================================================================
#  Protocole de cible
# ============================================================================


@runtime_checkable
class HookTarget(Protocol):
    """Interface minimale attendue par `HookDispatcher` pour une cible.

    Le loader expose une classe `LoadedPlugin` qui satisfait ce protocole
    (attributs ``name`` et ``instance``). Ce découplage évite un import
    circulaire `loader → hooks → loader`.
    """

    name: str
    instance: Any


# ============================================================================
#  Dispatcher
# ============================================================================


class HookDispatcher:
    """Dispatcher bas niveau pour l'appel d'un hook sur un plugin unique.

    Ce composant ne gère **pas** la liste des plugins ni la sélection des
    abonnés — c'est le rôle du `PluginLoader`. Il encapsule uniquement :

        - La détection auto sync vs async (`inspect.iscoroutinefunction`).
        - L'exécution avec timeout (`asyncio.wait_for`).
        - La gestion d'erreur uniforme (`HookError`).
        - Le logging contextualisé (bind plugin + hook).

    Une instance est réutilisable entre appels. Elle est conçue pour être
    instanciée **une fois** par loader et partagée. Elle est thread-safe
    pour l'appel concurrent (aucun état mutable interne).

    Attributes:
        default_timeout: Timeout en secondes par défaut.

    Example:
        Utilisation directe::

            dispatcher = HookDispatcher(default_timeout=3.0)
            result = await dispatcher.call(
                plugin,
                "pre_search",
                SearchPayload(query="one piece"),
            )
    """

    __slots__ = ("default_timeout",)

    def __init__(self, *, default_timeout: float = DEFAULT_HOOK_TIMEOUT) -> None:
        """Initialise le dispatcher.

        Args:
            default_timeout: Timeout en secondes par défaut. Utilisé quand
                l'appelant ne passe pas de ``timeout`` explicite.
        """
        if default_timeout <= 0:
            msg = f"default_timeout doit être > 0, reçu {default_timeout}"
            raise ValueError(msg)
        self.default_timeout = default_timeout

    async def call(
        self,
        target: HookTarget,
        hook_name: str,
        payload: HookPayload,
        *,
        timeout: float | None = None,
    ) -> Any:
        """Appelle le hook ``hook_name`` sur ``target``.

        Détecte automatiquement si la méthode est sync ou async :
            - Async : attente directe avec timeout.
            - Sync : exécution dans un thread (`asyncio.to_thread`).

        Retourne la valeur retournée par la méthode, ou ``None`` si la
        méthode n'existe pas sur le plugin.

        Args:
            target: Plugin cible (objet avec ``.name`` et ``.instance``).
            hook_name: Nom du hook à appeler (doit être dans ``HOOK_NAMES``).
            payload: Payload à passer à la méthode.
            timeout: Timeout en secondes. ``None`` = ``default_timeout``.

        Returns:
            La valeur retournée par la méthode, ou ``None``.

        Raises:
            ValueError: Si ``hook_name`` n'est pas un hook connu.
            HookError: Si la méthode lève, timeout, ou retourne un type
                inattendu.
        """
        if hook_name not in HOOK_NAMES:
            msg = f"Hook inconnu : '{hook_name}'. Valides : {sorted(HOOK_NAMES)}"
            raise ValueError(msg)

        method = getattr(target.instance, hook_name, None)
        if not callable(method):
            return None

        effective_timeout = timeout if timeout is not None else self.default_timeout
        bound = logger.bind(plugin=target.name, hook=hook_name)
        start = time.perf_counter()

        try:
            if inspect.iscoroutinefunction(method):
                result = await asyncio.wait_for(method(payload), timeout=effective_timeout)
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(method, payload),
                    timeout=effective_timeout,
                )
        except TimeoutError as exc:
            elapsed = time.perf_counter() - start
            bound.warning(
                "Timeout après {:.2f}s (limite : {:.2f}s)",
                elapsed,
                effective_timeout,
            )
            timeout_exc = TimeoutError(
                f"hook '{hook_name}' a dépassé {effective_timeout}s",
            )
            raise HookError(target.name, hook_name, timeout_exc, is_timeout=True) from exc
        except asyncio.CancelledError:
            bound.debug("Appel annulé")
            raise
        except Exception as exc:  # noqa: BLE001 — on capture tout ce que le plugin peut lever
            elapsed = time.perf_counter() - start
            bound.opt(exception=exc).debug(
                "Hook a levé après {:.3f}s : {}: {}",
                elapsed,
                type(exc).__name__,
                exc,
            )
            raise HookError(target.name, hook_name, exc) from exc

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        bound.trace("Hook exécuté en {:.1f}ms", elapsed_ms)
        return result

    def call_sync_safe(
        self,
        target: HookTarget,
        hook_name: str,
        payload: HookPayload,
        *,
        timeout: float | None = None,
    ) -> Awaitable[Any]:
        """Retourne une coroutine — utile pour `asyncio.gather`.

        Wrapper trivial autour de :meth:`call` pour permettre de
        construire une liste de coroutines sans les exécuter :

            coros = [dispatcher.call_sync_safe(p, h, pl) for p in plugins]
            results = await asyncio.gather(*coros, return_exceptions=True)

        Args:
            target: Plugin cible.
            hook_name: Nom du hook.
            payload: Payload à passer.
            timeout: Timeout en secondes.

        Returns:
            Coroutine prête à être exécutée.
        """
        return self.call(target, hook_name, payload, timeout=timeout)


# ============================================================================
#  Helpers de construction
# ============================================================================


def make_lifecycle_payload(plugin_name: str, plugin_path: str = "") -> LifecyclePayload:
    """Construit un payload pour ``on_load`` / ``on_unload``.

    Args:
        plugin_name: Nom du plugin.
        plugin_path: Chemin du dossier du plugin.

    Returns:
        Payload prêt à passer au dispatcher.
    """
    return LifecyclePayload(plugin_name=plugin_name, plugin_path=plugin_path)


def make_error_payload(
    error: BaseException,
    *,
    site_id: str | None = None,
    task_id: str | None = None,
    url: str | None = None,
    recoverable: bool = False,
    **context: Any,
) -> ErrorPayload:
    """Construit un payload d'erreur depuis une exception.

    Args:
        error: Exception d'origine.
        site_id: Site concerné.
        task_id: Tâche concernée.
        url: URL en cours de traitement.
        recoverable: True si l'erreur peut être retentée.
        **context: Données additionnelles pour ``ErrorPayload.context``.

    Returns:
        Payload prêt à dispatcher.
    """
    import traceback as tb  # noqa: PLC0415 — import local pour éviter un import sys au top

    tb_str: str | None = None
    try:
        tb_str = "".join(
            tb.format_exception(type(error), error, error.__traceback__),
        )
    except Exception:  # noqa: BLE001 — best-effort, traceback peut échouer
        tb_str = None

    return ErrorPayload(
        error_type=type(error).__name__,
        error_message=str(error),
        traceback=tb_str,
        site_id=site_id,
        task_id=task_id,
        url=url,
        recoverable=recoverable,
        context=dict(context),
    )


# ============================================================================
#  Exports publics
# ============================================================================

__all__ = [
    "DEFAULT_HOOK_TIMEOUT",
    "HOOK_METADATA",
    "HOOK_NAMES",
    "HOOKS_API_VERSION",
    "ChapterPayload",
    "DownloadPayload",
    "ErrorPayload",
    "HookDispatcher",
    "HookError",
    "HookMetadata",
    "HookPayload",
    "HookTarget",
    "HookType",
    "LibraryScanPayload",
    "LifecyclePayload",
    "MangaPayload",
    "PackagingPayload",
    "SearchPayload",
    "is_cancellable",
    "make_error_payload",
    "make_lifecycle_payload",
    "payload_class_for",
]
