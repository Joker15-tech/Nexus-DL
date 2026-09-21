"""API publique exposée aux plugins NexusDL.

Ce module définit le **contrat** entre NexusDL et ses plugins. Il expose :

    - `PluginAPI` : la façade principale. Chaque plugin reçoit une instance
      scopée à ses permissions déclarées.
    - `PluginContext` : métadonnées du plugin (nom, version, chemin, logger).
    - Accesseurs (`Notifier`, `LibraryAccessor`, `HttpAccessor`,
      `FilesystemAccessor`, `ConfigAccessor`) : chaque accès à une ressource
      core est encapsulé et vérifie une permission.

**Invariant** : aucun plugin n'importe `nexusdl.core.*` directement.
Toute opération passe par un accesseur, qui applique les politiques
système (rate limiting, cookies, sandbox de chemins, etc.) de façon
transparente pour le plugin.

**Stabilité** : ce module est couvert par la politique de versionnage
`PLUGIN_API_VERSION`. Ajout de méthode → minor. Changement de signature
→ major. Retrait → major. Un plugin qui déclare `api_version: "1.0"`
fonctionnera sur toute API `1.x`.

Example:
    Un plugin typique reçoit `api` et `context` par injection::

        from nexusdl.plugins.api import PluginAPI, PluginContext

        class MyPlugin:
            def __init__(self, api: PluginAPI, context: PluginContext) -> None:
                self.api = api
                self.ctx = context

            async def on_load(self, payload) -> None:
                self.ctx.logger.info("Chargé !")
                await self.api.notify("Hello", "Plugin démarré", level="info")

            async def pre_search(self, payload):
                # HTTP via l'accesseur (rate-limité, cookies, proxy)
                data = await self.api.http.get_json("https://api.example.com/v1/search")
                # Fichiers confinés dans le sandbox du plugin
                cache = self.api.fs.cache_dir / "last_search.json"
                self.api.fs.write_json(cache, data)
                return payload
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    AsyncIterator,
    Final,
    Literal,
    Protocol,
    runtime_checkable,
)

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError

if TYPE_CHECKING:
    from collections.abc import Callable

    from nexusdl.core.config import Settings
    from nexusdl.plugins.hooks import HookPayload


# ============================================================================
#  Constantes
# ============================================================================

#: Version de l'API plugin — doit rester synchronisé avec
#: `PLUGIN_API_VERSION` dans `validators.py` et `HOOKS_API_VERSION`
#: dans `hooks.py`.
PLUGIN_API_VERSION: Final[str] = "1.0"

#: Taille maximale d'une réponse HTTP lue par `HttpAccessor.get_text/get_json`.
#: Au-delà, la réponse est tronquée avec un warning. Évite qu'un plugin
#: télécharge plusieurs Go de contenu par accident.
MAX_HTTP_RESPONSE_BYTES: Final[int] = 32 * 1024 * 1024  # 32 MiB

#: Timeout par défaut pour les requêtes HTTP des plugins (secondes).
DEFAULT_HTTP_TIMEOUT: Final[float] = 30.0

#: Timeout maximal autorisé pour une requête HTTP de plugin (secondes).
#: Un plugin peut demander moins, jamais plus — protège contre les hangs.
MAX_HTTP_TIMEOUT: Final[float] = 120.0

#: Taille maximale d'un fichier écrit par `FilesystemAccessor.write_bytes`.
#: Protège contre les plugins qui remplissent le disque par accident.
MAX_FILE_WRITE_BYTES: Final[int] = 64 * 1024 * 1024  # 64 MiB

#: Nombre maximal d'entrées retournées par `LibraryAccessor.list_all`.
#: Un plugin qui veut tout parcourir doit paginer explicitement.
MAX_LIBRARY_PAGE_SIZE: Final[int] = 500


# ============================================================================
#  Enums
# ============================================================================


class Permission(str, Enum):
    """Permissions que peut demander un plugin.

    Doit rester synchronisé avec `ALLOWED_PERMISSIONS` dans `validators.py`.
    Une permission déclarée dans le manifest devient un droit d'accès à
    l'accesseur correspondant.

    Attributes:
        READ_LIBRARY: Lire la bibliothèque locale (liste, détails mangas).
        WRITE_LIBRARY: Modifier la bibliothèque (tags, progression, favoris).
        NETWORK_HTTP: Faire des requêtes HTTP via `HttpAccessor`.
        NETWORK_RAW: Sockets brut (non exposé — réservé).
        FILESYSTEM_READ: Lire des fichiers dans le sandbox du plugin.
        FILESYSTEM_WRITE: Écrire des fichiers dans le sandbox du plugin.
        DOWNLOADS: Enregistrer des hooks de téléchargement.
        NOTIFICATIONS: Envoyer des notifications via `Notifier`.
        METADATA_MUTATION: Modifier les métadonnées de mangas.
        COOKIES: Accéder aux cookies chiffrés (non exposé — réservé).
        CONFIG_READ: Lire la configuration NexusDL (whitelist).
    """

    READ_LIBRARY = "read_library"
    WRITE_LIBRARY = "write_library"
    NETWORK_HTTP = "network_http"
    NETWORK_RAW = "network_raw"
    FILESYSTEM_READ = "filesystem_read"
    FILESYSTEM_WRITE = "filesystem_write"
    DOWNLOADS = "downloads"
    NOTIFICATIONS = "notifications"
    METADATA_MUTATION = "metadata_mutation"
    COOKIES = "cookies"
    CONFIG_READ = "config_read"


class NotificationLevel(str, Enum):
    """Niveau de notification.

    Mappé vers les niveaux du système de notification interne (Phase 12).
    """

    DEBUG = "debug"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# ============================================================================
#  Exceptions
# ============================================================================


class PluginAPIError(NexusDLError):
    """Erreur générique de l'API plugin."""


class PermissionDenied(PluginAPIError):
    """Levée quand un plugin tente une opération sans la permission requise.

    Attributes:
        plugin_name: Nom du plugin en faute.
        permission: Permission manquante.
        operation: Opération tentée (ex: ``"http.get"``).
    """

    def __init__(self, plugin_name: str, permission: Permission, operation: str) -> None:
        """Initialise l'exception.

        Args:
            plugin_name: Nom du plugin.
            permission: Permission requise mais absente.
            operation: Nom de l'opération refusée.
        """
        self.plugin_name = plugin_name
        self.permission = permission
        self.operation = operation
        super().__init__(
            f"Plugin '{plugin_name}' : permission '{permission.value}' requise "
            f"pour '{operation}'. Ajoute-la au manifest si légitime.",
        )


class ServiceUnavailable(PluginAPIError):
    """Levée quand un accesseur est utilisé sans backend disponible.

    Cas typiques : le `LibraryAccessor` sans base de données initialisée,
    le `Notifier` sans backend de notification configuré.
    """


class SandboxViolation(PluginAPIError):
    """Levée quand un plugin tente de sortir de son sandbox filesystem.

    Attributes:
        plugin_name: Nom du plugin.
        attempted: Chemin tenté.
        sandbox_root: Racine autorisée.
    """

    def __init__(self, plugin_name: str, attempted: Path, sandbox_root: Path) -> None:
        """Initialise l'exception.

        Args:
            plugin_name: Nom du plugin.
            attempted: Chemin qui a été tenté.
            sandbox_root: Racine du sandbox.
        """
        self.plugin_name = plugin_name
        self.attempted = attempted
        self.sandbox_root = sandbox_root
        super().__init__(
            f"Plugin '{plugin_name}' : accès refusé à '{attempted}' "
            f"(hors sandbox '{sandbox_root}')",
        )


# ============================================================================
#  Modèles de données exposés
# ============================================================================


class PluginContext(BaseModel):
    """Contexte d'exécution d'un plugin.

    Fournit au plugin ses métadonnées de base : nom, version, chemin,
    et un logger préconfiguré avec le nom du plugin en `extra`.

    Attributes:
        name: Nom unique du plugin.
        version: Version du plugin (depuis le manifest).
        api_version: Version d'API déclarée par le plugin.
        path: Chemin absolu du dossier du plugin (lecture seule).
        logger: Logger Loguru scopé au plugin.

    Note:
        Le champ ``logger`` est un objet `loguru.Logger` — non sérialisable
        Pydantic. On utilise ``arbitrary_types_allowed``. Un plugin qui veut
        sérialiser son contexte doit utiliser ``model_dump(exclude={"logger"})``.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str
    version: str
    api_version: str
    path: Path
    logger: Any  # loguru.Logger


class MangaInfo(BaseModel):
    """Vue allégée d'un manga pour les plugins.

    Contient les champs essentiels, sans exposer le modèle core complet.
    Un plugin qui a besoin de plus de détails utilise ``get_manga_full``.

    Attributes:
        manga_id: Identifiant interne NexusDL.
        site_id: Site source.
        title: Titre principal.
        author: Auteur (optionnel).
        status: Statut (``"ongoing"``, ``"completed"``, etc.).
        genres: Liste de genres.
        chapters_count: Nombre de chapitres.
        updated_at: Timestamp ISO 8601 de dernière mise à jour.
    """

    model_config = ConfigDict(frozen=True)

    manga_id: str
    site_id: str = ""
    title: str = ""
    author: str | None = None
    status: str = "unknown"
    genres: list[str] = Field(default_factory=list)
    chapters_count: int = 0
    updated_at: str = ""


class NotificationResult(BaseModel):
    """Résultat d'un envoi de notification.

    Attributes:
        delivered: True si la notification a été remise à au moins un canal.
        channels: Canaux utilisés (ex: ``["discord", "desktop"]``).
        failed_channels: Canaux qui ont échoué.
        error: Message d'erreur global, si applicable.
    """

    delivered: bool = False
    channels: list[str] = Field(default_factory=list)
    failed_channels: list[str] = Field(default_factory=list)
    error: str | None = None


class HttpResponse(BaseModel):
    """Réponse HTTP exposée aux plugins.

    Attributes:
        status_code: Code HTTP.
        headers: En-têtes (clés lowercase).
        url: URL finale après redirections.
        content_length: Longueur du contenu en octets.
        truncated: True si la réponse a été tronquée à `MAX_HTTP_RESPONSE_BYTES`.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    status_code: int
    headers: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    content_length: int = 0
    truncated: bool = False
    _text: str | None = None
    _json: Any = None

    def text(self) -> str:
        """Retourne le corps de la réponse en texte.

        Returns:
            Corps de la réponse décodé (UTF-8 avec fallback).
        """
        return self._text or ""

    def json(self) -> Any:
        """Retourne le corps parsé en JSON.

        Returns:
            Objet Python issu de ``json.loads``.

        Raises:
            ValueError: Si le corps n'est pas du JSON valide.
        """
        if self._json is None:
            self._json = json.loads(self._text or "")
        return self._json


# ============================================================================
#  Protocols — dépendances injectables
# ============================================================================


@runtime_checkable
class NotificationBackend(Protocol):
    """Backend de notification (injecté dans `Notifier`).

    L'implémentation concrète vit dans `core.notifications` (Phase 12).
    """

    async def send(
        self,
        title: str,
        message: str,
        *,
        level: str = "info",
        tags: list[str] | None = None,
        source: str | None = None,
    ) -> NotificationResult:
        """Envoie une notification.

        Args:
            title: Titre court.
            message: Corps du message.
            level: Niveau de notification.
            tags: Tags libres (filtrage côté canal).
            source: Nom de la source (typiquement le nom du plugin).

        Returns:
            Résultat de l'envoi.
        """
        ...


@runtime_checkable
class LibraryBackend(Protocol):
    """Backend de la bibliothèque locale (injecté dans `LibraryAccessor`).

    L'implémentation concrète vit dans `core.library.database` (Phase 9).
    """

    async def list_mangas(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        site_id: str | None = None,
    ) -> list[MangaInfo]:
        """Liste les mangas de la bibliothèque (paginé).

        Args:
            limit: Nombre maximum de résultats.
            offset: Décalage de pagination.
            site_id: Filtre optionnel par site.

        Returns:
            Liste de mangas.
        """
        ...

    async def get_manga(self, manga_id: str) -> MangaInfo | None:
        """Récupère un manga par son ID.

        Args:
            manga_id: Identifiant interne.

        Returns:
            Manga, ou None s'il n'existe pas.
        """
        ...

    async def set_tags(self, manga_id: str, tags: list[str]) -> bool:
        """Remplace les tags d'un manga.

        Args:
            manga_id: Identifiant interne.
            tags: Nouveaux tags.

        Returns:
            True si la mise à jour a réussi.
        """
        ...

    async def set_favorite(self, manga_id: str, favorite: bool) -> bool:
        """Marque ou démarque un manga comme favori.

        Args:
            manga_id: Identifiant interne.
            favorite: État souhaité.

        Returns:
            True si la mise à jour a réussi.
        """
        ...

    async def count(self) -> int:
        """Compte les mangas dans la bibliothèque.

        Returns:
            Nombre total de mangas.
        """
        ...


@runtime_checkable
class HttpBackend(Protocol):
    """Backend HTTP (injecté dans `HttpAccessor`).

    L'implémentation concrète vit dans `core.session.http_session`
    (Phase 5). Le backend applique automatiquement rate limiting, cookies,
    proxy, retry — le plugin n'a rien à gérer.
    """

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        data: Any = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
        follow_redirects: bool = True,
        site_id: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Exécute une requête HTTP.

        Args:
            method: Méthode HTTP.
            url: URL cible.
            params: Query parameters.
            headers: En-têtes additionnels.
            json_body: Corps JSON (sérialisé automatiquement).
            data: Corps brut (bytes, form, etc.).
            timeout: Timeout en secondes.
            follow_redirects: Suivre les redirections.
            site_id: Site associé (pour la politique rate limit).

        Returns:
            Tuple ``(status_code, headers, body)``.
        """
        ...


@runtime_checkable
class ConfigBackend(Protocol):
    """Backend de configuration (injecté dans `ConfigAccessor`).

    Expose une **whitelist** de clés en lecture seule. Un plugin ne peut
    pas modifier la config NexusDL.
    """

    def get(self, key: str, default: Any = None) -> Any:
        """Lit une clé de config par chemin pointé (``"download.max_concurrent_tasks"``).

        Args:
            key: Chemin pointé vers la clé.
            default: Valeur par défaut si la clé n'existe pas.

        Returns:
            La valeur, ou ``default``.
        """
        ...

    def keys(self) -> list[str]:
        """Liste les clés autorisées en lecture.

        Returns:
            Liste de chemins pointés (ex: ``["app.language", "library.enabled"]``).
        """
        ...


# ============================================================================
#  Accesseurs
# ============================================================================


class _BaseAccessor:
    """Base commune aux accesseurs — fournit la logique de permission.

    Attributes:
        plugin_name: Nom du plugin propriétaire.
        permissions: Permissions déclarées (frozenset).
    """

    __slots__ = ("plugin_name", "permissions")

    def __init__(self, plugin_name: str, permissions: frozenset[str]) -> None:
        """Initialise l'accesseur.

        Args:
            plugin_name: Nom du plugin.
            permissions: Permissions déclarées par le plugin.
        """
        self.plugin_name = plugin_name
        self.permissions = permissions

    def _require(self, permission: Permission, operation: str) -> None:
        """Vérifie qu'une permission est accordée, sinon lève.

        Args:
            permission: Permission requise.
            operation: Nom de l'opération tentée.

        Raises:
            PermissionDenied: Si la permission n'est pas accordée.
        """
        if permission.value not in self.permissions:
            raise PermissionDenied(self.plugin_name, permission, operation)

    def _has(self, permission: Permission) -> bool:
        """Vérifie une permission sans lever.

        Args:
            permission: Permission à tester.

        Returns:
            True si accordée.
        """
        return permission.value in self.permissions


class Notifier(_BaseAccessor):
    """Accesseur pour l'envoi de notifications.

    Requiert la permission ``notifications``.

    Example:
        ::

            await api.notify("Chapitre trouvé", "One Piece ch. 1100", level="info")
    """

    __slots__ = ("_backend",)

    def __init__(
        self,
        plugin_name: str,
        permissions: frozenset[str],
        backend: NotificationBackend | None,
    ) -> None:
        """Initialise le notifier.

        Args:
            plugin_name: Nom du plugin.
            permissions: Permissions déclarées.
            backend: Backend de notification, ou None si non disponible.
        """
        super().__init__(plugin_name, permissions)
        self._backend = backend

    async def send(
        self,
        title: str,
        message: str,
        *,
        level: NotificationLevel | str = NotificationLevel.INFO,
        tags: list[str] | None = None,
    ) -> NotificationResult:
        """Envoie une notification.

        Args:
            title: Titre court (max 200 caractères).
            message: Corps du message (max 4000 caractères).
            level: Niveau de notification.
            tags: Tags libres (filtrage côté canal).

        Returns:
            Résultat de l'envoi.

        Raises:
            PermissionDenied: Si ``notifications`` non accordée.
            ServiceUnavailable: Si aucun backend n'est configuré.
            ValueError: Si ``title`` ou ``message`` dépasse les limites.
        """
        self._require(Permission.NOTIFICATIONS, "notify")
        if self._backend is None:
            msg = "Aucun backend de notification configuré"
            raise ServiceUnavailable(msg)

        if len(title) > 200:  # noqa: PLR2004
            msg = f"title trop long : {len(title)} > 200"
            raise ValueError(msg)
        if len(message) > 4000:  # noqa: PLR2004
            msg = f"message trop long : {len(message)} > 4000"
            raise ValueError(msg)

        level_str = level.value if isinstance(level, NotificationLevel) else level
        return await self._backend.send(
            title=title,
            message=message,
            level=level_str,
            tags=tags,
            source=self.plugin_name,
        )

    async def debug(self, title: str, message: str) -> NotificationResult:
        """Raccourci pour ``send(level=DEBUG)``.

        Args:
            title: Titre.
            message: Corps.

        Returns:
            Résultat.
        """
        return await self.send(title, message, level=NotificationLevel.DEBUG)

    async def info(self, title: str, message: str) -> NotificationResult:
        """Raccourci pour ``send(level=INFO)``.

        Args:
            title: Titre.
            message: Corps.

        Returns:
            Résultat.
        """
        return await self.send(title, message, level=NotificationLevel.INFO)

    async def success(self, title: str, message: str) -> NotificationResult:
        """Raccourci pour ``send(level=SUCCESS)``.

        Args:
            title: Titre.
            message: Corps.

        Returns:
            Résultat.
        """
        return await self.send(title, message, level=NotificationLevel.SUCCESS)

    async def warning(self, title: str, message: str) -> NotificationResult:
        """Raccourci pour ``send(level=WARNING)``.

        Args:
            title: Titre.
            message: Corps.

        Returns:
            Résultat.
        """
        return await self.send(title, message, level=NotificationLevel.WARNING)

    async def error(self, title: str, message: str) -> NotificationResult:
        """Raccourci pour ``send(level=ERROR)``.

        Args:
            title: Titre.
            message: Corps.

        Returns:
            Résultat.
        """
        return await self.send(title, message, level=NotificationLevel.ERROR)

    async def critical(self, title: str, message: str) -> NotificationResult:
        """Raccourci pour ``send(level=CRITICAL)``.

        Args:
            title: Titre.
            message: Corps.

        Returns:
            Résultat.
        """
        return await self.send(title, message, level=NotificationLevel.CRITICAL)


class LibraryAccessor(_BaseAccessor):
    """Accesseur pour la bibliothèque locale.

    Requiert ``read_library`` pour les lectures, ``write_library`` pour les
    écritures (tags, favoris). Les permissions sont vérifiées **par méthode**,
    pas globalement — un plugin peut demander uniquement la lecture.

    Example:
        ::

            mangas = await api.library.list_all(limit=50)
            if api.library.can_write:
                await api.library.set_favorite(mangas[0].manga_id, True)
    """

    __slots__ = ("_backend",)

    def __init__(
        self,
        plugin_name: str,
        permissions: frozenset[str],
        backend: LibraryBackend | None,
    ) -> None:
        """Initialise l'accesseur.

        Args:
            plugin_name: Nom du plugin.
            permissions: Permissions déclarées.
            backend: Backend de bibliothèque, ou None.
        """
        super().__init__(plugin_name, permissions)
        self._backend = backend

    @property
    def can_read(self) -> bool:
        """True si ``read_library`` est accordée."""
        return self._has(Permission.READ_LIBRARY)

    @property
    def can_write(self) -> bool:
        """True si ``write_library`` est accordée."""
        return self._has(Permission.WRITE_LIBRARY)

    async def list_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        site_id: str | None = None,
    ) -> list[MangaInfo]:
        """Liste les mangas de la bibliothèque (paginé).

        Args:
            limit: Nombre maximum de résultats (1 à 500).
            offset: Décalage.
            site_id: Filtre optionnel par site.

        Returns:
            Liste de mangas.

        Raises:
            PermissionDenied: Si ``read_library`` non accordée.
            ServiceUnavailable: Si aucun backend n'est disponible.
            ValueError: Si ``limit`` est hors bornes.
        """
        self._require(Permission.READ_LIBRARY, "library.list_all")
        if self._backend is None:
            msg = "Aucun backend de bibliothèque configuré"
            raise ServiceUnavailable(msg)
        if not 1 <= limit <= MAX_LIBRARY_PAGE_SIZE:
            msg = f"limit hors bornes : 1 <= {limit} <= {MAX_LIBRARY_PAGE_SIZE}"
            raise ValueError(msg)
        if offset < 0:
            msg = f"offset négatif : {offset}"
            raise ValueError(msg)

        return await self._backend.list_mangas(limit=limit, offset=offset, site_id=site_id)

    async def get(self, manga_id: str) -> MangaInfo | None:
        """Récupère un manga par son ID.

        Args:
            manga_id: Identifiant interne.

        Returns:
            Manga, ou None.

        Raises:
            PermissionDenied: Si ``read_library`` non accordée.
        """
        self._require(Permission.READ_LIBRARY, "library.get")
        if self._backend is None:
            msg = "Aucun backend de bibliothèque configuré"
            raise ServiceUnavailable(msg)
        return await self._backend.get_manga(manga_id)

    async def count(self) -> int:
        """Compte les mangas dans la bibliothèque.

        Returns:
            Nombre total de mangas.

        Raises:
            PermissionDenied: Si ``read_library`` non accordée.
        """
        self._require(Permission.READ_LIBRARY, "library.count")
        if self._backend is None:
            msg = "Aucun backend de bibliothèque configuré"
            raise ServiceUnavailable(msg)
        return await self._backend.count()

    async def set_tags(self, manga_id: str, tags: list[str]) -> bool:
        """Remplace les tags d'un manga.

        Args:
            manga_id: Identifiant interne.
            tags: Nouveaux tags (remplace les existants).

        Returns:
            True si la mise à jour a réussi.

        Raises:
            PermissionDenied: Si ``write_library`` non accordée.
            ValueError: Si un tag dépasse 50 caractères ou si plus de 100 tags.
        """
        self._require(Permission.WRITE_LIBRARY, "library.set_tags")
        if self._backend is None:
            msg = "Aucun backend de bibliothèque configuré"
            raise ServiceUnavailable(msg)
        if len(tags) > 100:  # noqa: PLR2004
            msg = f"trop de tags : {len(tags)} > 100"
            raise ValueError(msg)
        for tag in tags:
            if len(tag) > 50:  # noqa: PLR2004
                msg = f"tag trop long : {tag!r} ({len(tag)} > 50)"
                raise ValueError(msg)
        return await self._backend.set_tags(manga_id, tags)

    async def set_favorite(self, manga_id: str, favorite: bool = True) -> bool:
        """Marque ou démarque un manga comme favori.

        Args:
            manga_id: Identifiant interne.
            favorite: État souhaité.

        Returns:
            True si la mise à jour a réussi.

        Raises:
            PermissionDenied: Si ``write_library`` non accordée.
        """
        self._require(Permission.WRITE_LIBRARY, "library.set_favorite")
        if self._backend is None:
            msg = "Aucun backend de bibliothèque configuré"
            raise ServiceUnavailable(msg)
        return await self._backend.set_favorite(manga_id, favorite)


class HttpAccessor(_BaseAccessor):
    """Accesseur HTTP scopé.

    Requiert ``network_http``. Le backend applique automatiquement :
    rate limiting par site, gestion des cookies, proxy configuré, retry
    avec backoff, User-Agent rotation.

    **Limites imposées** :
        - Réponses tronquées à ``MAX_HTTP_RESPONSE_BYTES`` (32 MiB).
        - Timeout borné à ``MAX_HTTP_TIMEOUT`` (120s).
        - Toutes les opérations sont async.

    Example:
        ::

            resp = await api.http.get("https://api.example.com/v1/data")
            if resp.status_code == 200:
                data = resp.json()
    """

    __slots__ = ("_backend",)

    def __init__(
        self,
        plugin_name: str,
        permissions: frozenset[str],
        backend: HttpBackend | None,
    ) -> None:
        """Initialise l'accesseur.

        Args:
            plugin_name: Nom du plugin.
            permissions: Permissions déclarées.
            backend: Backend HTTP, ou None.
        """
        super().__init__(plugin_name, permissions)
        self._backend = backend

    def _check_backend(self) -> HttpBackend:
        """Vérifie que le backend est disponible.

        Returns:
            Le backend.

        Raises:
            ServiceUnavailable: Si aucun backend HTTP n'est configuré.
        """
        if self._backend is None:
            msg = "Aucun backend HTTP configuré"
            raise ServiceUnavailable(msg)
        return self._backend

    @staticmethod
    def _validate_timeout(timeout: float) -> float:
        """Valide et borne un timeout.

        Args:
            timeout: Timeout demandé.

        Returns:
            Timeout effectif (borné à MAX_HTTP_TIMEOUT).

        Raises:
            ValueError: Si timeout <= 0.
        """
        if timeout <= 0:
            msg = f"timeout doit être > 0, reçu {timeout}"
            raise ValueError(msg)
        return min(timeout, MAX_HTTP_TIMEOUT)

    def _build_response(
        self,
        status: int,
        headers: dict[str, str],
        body: bytes,
        url: str,
    ) -> HttpResponse:
        """Construit un `HttpResponse` en appliquant la troncature.

        Args:
            status: Code HTTP.
            headers: En-têtes.
            body: Corps brut.
            url: URL finale.

        Returns:
            Réponse exposable au plugin.
        """
        truncated = len(body) > MAX_HTTP_RESPONSE_BYTES
        if truncated:
            logger.warning(
                "Plugin '{}' : réponse HTTP tronquée ({} > {} octets)",
                self.plugin_name,
                len(body),
                MAX_HTTP_RESPONSE_BYTES,
            )
            body = body[:MAX_HTTP_RESPONSE_BYTES]

        text: str
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            text = body.decode("utf-8", errors="replace")

        resp = HttpResponse(
            status_code=status,
            headers={k.lower(): v for k, v in headers.items()},
            url=url,
            content_length=len(body),
            truncated=truncated,
        )
        resp._text = text  # noqa: SLF001 — attribut privé, injection
        return resp

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        data: Any = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
        follow_redirects: bool = True,
        site_id: str | None = None,
    ) -> HttpResponse:
        """Exécute une requête HTTP.

        Args:
            method: Méthode (GET, POST, ...). Sensible à la casse.
            url: URL cible (http:// ou https://).
            params: Query parameters.
            headers: En-têtes additionnels.
            json_body: Corps JSON (sérialisé automatiquement).
            data: Corps brut (bytes, form, etc.).
            timeout: Timeout en secondes (max 120).
            follow_redirects: Suivre les redirections.
            site_id: Site associé (pour la politique rate limit).

        Returns:
            Réponse.

        Raises:
            PermissionDenied: Si ``network_http`` non accordée.
            ServiceUnavailable: Si aucun backend HTTP.
            ValueError: Si l'URL est invalide ou le timeout hors bornes.
        """
        self._require(Permission.NETWORK_HTTP, "http.request")
        backend = self._check_backend()

        if not url.startswith(("http://", "https://")):
            msg = f"URL invalide (schéma http/https requis) : {url!r}"
            raise ValueError(msg)
        method_upper = method.upper()
        if method_upper not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            msg = f"méthode HTTP non autorisée : {method!r}"
            raise ValueError(msg)

        effective_timeout = self._validate_timeout(timeout)

        status, resp_headers, body = await backend.request(
            method_upper,
            url,
            params=params,
            headers=headers,
            json_body=json_body,
            data=data,
            timeout=effective_timeout,
            follow_redirects=follow_redirects,
            site_id=site_id,
        )
        return self._build_response(status, resp_headers, body, url)

    async def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
        site_id: str | None = None,
    ) -> HttpResponse:
        """Raccourci GET.

        Args:
            url: URL cible.
            params: Query parameters.
            headers: En-têtes.
            timeout: Timeout.
            site_id: Site associé.

        Returns:
            Réponse.
        """
        return await self.request(
            "GET", url,
            params=params, headers=headers, timeout=timeout, site_id=site_id,
        )

    async def post(
        self,
        url: str,
        *,
        json_body: Any = None,
        data: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
        site_id: str | None = None,
    ) -> HttpResponse:
        """Raccourci POST.

        Args:
            url: URL cible.
            json_body: Corps JSON.
            data: Corps brut.
            headers: En-têtes.
            timeout: Timeout.
            site_id: Site associé.

        Returns:
            Réponse.
        """
        return await self.request(
            "POST", url,
            json_body=json_body, data=data, headers=headers,
            timeout=timeout, site_id=site_id,
        )

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
        site_id: str | None = None,
    ) -> Any:
        """GET et parse la réponse en JSON.

        Args:
            url: URL cible.
            params: Query parameters.
            headers: En-têtes.
            timeout: Timeout.
            site_id: Site associé.

        Returns:
            Objet Python parsé.

        Raises:
            ValueError: Si la réponse n'est pas du JSON valide.
        """
        resp = await self.get(
            url, params=params, headers=headers, timeout=timeout, site_id=site_id,
        )
        return resp.json()

    async def get_text(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
        site_id: str | None = None,
    ) -> str:
        """GET et retourne le corps en texte.

        Args:
            url: URL cible.
            params: Query parameters.
            headers: En-têtes.
            timeout: Timeout.
            site_id: Site associé.

        Returns:
            Corps de la réponse en texte.
        """
        resp = await self.get(
            url, params=params, headers=headers, timeout=timeout, site_id=site_id,
        )
        return resp.text()


class FilesystemAccessor(_BaseAccessor):
    """Accesseur filesystem confiné dans un sandbox par plugin.

    Chaque plugin dispose d'un dossier racine dédié :
    ``{base_dir}/{plugin_name}/``. Tout accès en dehors est rejeté avec
    ``SandboxViolation``.

    Structure du sandbox (créée à la demande) :
        ::

            {base_dir}/{plugin_name}/
            ├── cache/       # fichiers éphémères (purgés régulièrement)
            ├── data/        # données persistantes du plugin
            └── tmp/         # fichiers temporaires (purgés à chaque on_load)

    Permissions :
        - ``filesystem_read`` : ``read_text``, ``read_bytes``, ``read_json``,
          ``list_dir``, ``exists``.
        - ``filesystem_write`` : ``write_text``, ``write_bytes``,
          ``write_json``, ``mkdir``, ``delete``.

    Example:
        ::

            # Écriture d'un cache (permission write)
            await api.fs.write_json(api.fs.cache_dir / "search.json", results)

            # Lecture (permission read)
            data = await api.fs.read_json(api.fs.cache_dir / "search.json")
    """

    __slots__ = ("_root", "_cache_dir", "_data_dir", "_tmp_dir")

    def __init__(
        self,
        plugin_name: str,
        permissions: frozenset[str],
        base_dir: Path,
    ) -> None:
        """Initialise l'accesseur.

        Args:
            plugin_name: Nom du plugin.
            permissions: Permissions déclarées.
            base_dir: Racine des sandbox (généralement
                ``{config_dir}/plugins_data/``). Le sous-dossier
                ``{base_dir}/{plugin_name}/`` est créé à la demande.
        """
        super().__init__(plugin_name, permissions)
        self._root = (base_dir / plugin_name).resolve()
        self._cache_dir = self._root / "cache"
        self._data_dir = self._root / "data"
        self._tmp_dir = self._root / "tmp"

    # --- Propriétés --------------------------------------------------------

    @property
    def root(self) -> Path:
        """Racine du sandbox du plugin (lecture seule pour l'appelant)."""
        return self._root

    @property
    def cache_dir(self) -> Path:
        """Dossier de cache (éphémère)."""
        return self._cache_dir

    @property
    def data_dir(self) -> Path:
        """Dossier de données persistantes."""
        return self._data_dir

    @property
    def tmp_dir(self) -> Path:
        """Dossier temporaire (purgé à chaque ``on_load``)."""
        return self._tmp_dir

    # --- Sandbox -----------------------------------------------------------

    def _ensure_within_sandbox(self, path: Path) -> Path:
        """Vérifie qu'un chemin est bien dans le sandbox du plugin.

        Résout les symlinks et les ``..`` pour empêcher les contournements.

        Args:
            path: Chemin à vérifier.

        Returns:
            Le chemin résolu.

        Raises:
            SandboxViolation: Si le chemin sort du sandbox.
        """
        resolved = path.resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise SandboxViolation(self.plugin_name, resolved, self._root) from exc
        return resolved

    def _prepare_dir(self, directory: Path) -> None:
        """Crée un dossier (et ses parents) s'il n'existe pas.

        Args:
            directory: Dossier à créer (doit être dans le sandbox).
        """
        self._ensure_within_sandbox(directory)
        directory.mkdir(parents=True, exist_ok=True)

    # --- Lecture -----------------------------------------------------------

    async def exists(self, path: Path) -> bool:
        """Vérifie qu'un chemin existe dans le sandbox.

        Args:
            path: Chemin relatif ou absolu (doit être dans le sandbox).

        Returns:
            True si le chemin existe.

        Raises:
            PermissionDenied: Si ``filesystem_read`` non accordée.
            SandboxViolation: Si le chemin sort du sandbox.
        """
        self._require(Permission.FILESYSTEM_READ, "fs.exists")
        resolved = self._ensure_within_sandbox(path)
        return await asyncio.to_thread(resolved.exists)

    async def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        """Lit un fichier texte.

        Args:
            path: Chemin dans le sandbox.
            encoding: Encodage (défaut UTF-8).

        Returns:
            Contenu du fichier.

        Raises:
            PermissionDenied: Si ``filesystem_read`` non accordée.
            SandboxViolation: Si le chemin sort du sandbox.
            FileNotFoundError: Si le fichier n'existe pas.
        """
        self._require(Permission.FILESYSTEM_READ, "fs.read_text")
        resolved = self._ensure_within_sandbox(path)
        return await asyncio.to_thread(resolved.read_text, encoding=encoding)

    async def read_bytes(self, path: Path) -> bytes:
        """Lit un fichier binaire.

        Args:
            path: Chemin dans le sandbox.

        Returns:
            Contenu brut.

        Raises:
            PermissionDenied: Si ``filesystem_read`` non accordée.
            SandboxViolation: Si le chemin sort du sandbox.
        """
        self._require(Permission.FILESYSTEM_READ, "fs.read_bytes")
        resolved = self._ensure_within_sandbox(path)
        return await asyncio.to_thread(resolved.read_bytes)

    async def read_json(self, path: Path) -> Any:
        """Lit un fichier JSON.

        Args:
            path: Chemin dans le sandbox.

        Returns:
            Objet Python parsé.

        Raises:
            PermissionDenied: Si ``filesystem_read`` non accordée.
            json.JSONDecodeError: Si le fichier n'est pas du JSON valide.
        """
        self._require(Permission.FILESYSTEM_READ, "fs.read_json")
        text = await self.read_text(path)
        return json.loads(text)

    async def list_dir(self, path: Path) -> list[Path]:
        """Liste le contenu d'un dossier.

        Args:
            path: Dossier dans le sandbox.

        Returns:
            Liste triée de chemins absolus.

        Raises:
            PermissionDenied: Si ``filesystem_read`` non accordée.
            SandboxViolation: Si le chemin sort du sandbox.
        """
        self._require(Permission.FILESYSTEM_READ, "fs.list_dir")
        resolved = self._ensure_within_sandbox(path)
        if not await asyncio.to_thread(resolved.is_dir):
            msg = f"Pas un dossier : {resolved}"
            raise NotADirectoryError(msg)
        entries = await asyncio.to_thread(lambda: sorted(resolved.iterdir()))
        return entries

    # --- Écriture ----------------------------------------------------------

    async def write_text(
        self,
        path: Path,
        content: str,
        *,
        encoding: str = "utf-8",
        create_parents: bool = True,
    ) -> Path:
        """Écrit un fichier texte.

        Args:
            path: Chemin dans le sandbox.
            content: Contenu à écrire.
            encoding: Encodage.
            create_parents: Créer les dossiers parents si absents.

        Returns:
            Chemin absolu du fichier écrit.

        Raises:
            PermissionDenied: Si ``filesystem_write`` non accordée.
            SandboxViolation: Si le chemin sort du sandbox.
            ValueError: Si ``content`` dépasse ``MAX_FILE_WRITE_BYTES``.
        """
        self._require(Permission.FILESYSTEM_WRITE, "fs.write_text")
        encoded = content.encode(encoding)
        if len(encoded) > MAX_FILE_WRITE_BYTES:
            msg = f"contenu trop volumineux : {len(encoded)} > {MAX_FILE_WRITE_BYTES}"
            raise ValueError(msg)

        resolved = self._ensure_within_sandbox(path)
        if create_parents:
            await asyncio.to_thread(self._prepare_dir, resolved.parent)
        await asyncio.to_thread(resolved.write_text, content, encoding=encoding)
        return resolved

    async def write_bytes(
        self,
        path: Path,
        content: bytes,
        *,
        create_parents: bool = True,
    ) -> Path:
        """Écrit un fichier binaire.

        Args:
            path: Chemin dans le sandbox.
            content: Données à écrire.
            create_parents: Créer les dossiers parents si absents.

        Returns:
            Chemin absolu du fichier écrit.

        Raises:
            PermissionDenied: Si ``filesystem_write`` non accordée.
            SandboxViolation: Si le chemin sort du sandbox.
            ValueError: Si ``content`` dépasse ``MAX_FILE_WRITE_BYTES``.
        """
        self._require(Permission.FILESYSTEM_WRITE, "fs.write_bytes")
        if len(content) > MAX_FILE_WRITE_BYTES:
            msg = f"contenu trop volumineux : {len(content)} > {MAX_FILE_WRITE_BYTES}"
            raise ValueError(msg)

        resolved = self._ensure_within_sandbox(path)
        if create_parents:
            await asyncio.to_thread(self._prepare_dir, resolved.parent)
        await asyncio.to_thread(resolved.write_bytes, content)
        return resolved

    async def write_json(
        self,
        path: Path,
        data: Any,
        *,
        indent: int = 2,
        create_parents: bool = True,
    ) -> Path:
        """Écrit un objet en JSON.

        Args:
            path: Chemin dans le sandbox.
            data: Objet sérialisable JSON.
            indent: Indentation (0 pour compact).
            create_parents: Créer les dossiers parents.

        Returns:
            Chemin absolu du fichier écrit.

        Raises:
            PermissionDenied: Si ``filesystem_write`` non accordée.
            TypeError: Si ``data`` n'est pas sérialisable.
        """
        payload = json.dumps(data, indent=indent or None, ensure_ascii=False, default=str)
        return await self.write_text(path, payload, create_parents=create_parents)

    async def mkdir(self, path: Path, *, parents: bool = True) -> Path:
        """Crée un dossier.

        Args:
            path: Dossier à créer dans le sandbox.
            parents: Créer les parents manquants.

        Returns:
            Chemin absolu du dossier créé.

        Raises:
            PermissionDenied: Si ``filesystem_write`` non accordée.
            SandboxViolation: Si le chemin sort du sandbox.
        """
        self._require(Permission.FILESYSTEM_WRITE, "fs.mkdir")
        resolved = self._ensure_within_sandbox(path)
        await asyncio.to_thread(resolved.mkdir, parents=parents, exist_ok=True)
        return resolved

    async def delete(self, path: Path) -> bool:
        """Supprime un fichier ou un dossier (récursivement).

        Args:
            path: Chemin à supprimer dans le sandbox.

        Returns:
            True si un élément a été supprimé, False s'il n'existait pas.

        Raises:
            PermissionDenied: Si ``filesystem_write`` non accordée.
            SandboxViolation: Si le chemin sort du sandbox.
        """
        self._require(Permission.FILESYSTEM_WRITE, "fs.delete")
        resolved = self._ensure_within_sandbox(path)
        # Refus de supprimer la racine du sandbox
        if resolved == self._root:
            msg = "Refus de supprimer la racine du sandbox"
            raise SandboxViolation(self.plugin_name, resolved, self._root)

        def _do_delete() -> bool:
            if not resolved.exists() and not resolved.is_symlink():
                return False
            if resolved.is_dir():
                import shutil  # noqa: PLC0415
                shutil.rmtree(resolved)
            else:
                resolved.unlink()
            return True

        return await asyncio.to_thread(_do_delete)

    # --- Utilitaires -------------------------------------------------------

    def ensure_sandbox_dirs(self) -> None:
        """Crée ``cache/``, ``data/`` et ``tmp/`` (idempotent).

        Appelé par le loader avant ``on_load`` pour garantir que les
        dossiers standard existent. N'exige pas de permission — c'est une
        initialisation, pas une opération utilisateur.
        """
        self._root.mkdir(parents=True, exist_ok=True)
        self._cache_dir.mkdir(exist_ok=True)
        self._data_dir.mkdir(exist_ok=True)
        self._tmp_dir.mkdir(exist_ok=True)

    def clear_tmp(self) -> int:
        """Vide ``tmp/`` (appelé à ``on_load``). Retourne le nombre d'éléments supprimés."""
        import shutil  # noqa: PLC0415

        if not self._tmp_dir.exists():
            return 0
        count = 0
        for entry in self._tmp_dir.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            count += 1
        return count

    @asynccontextmanager
    async def temp_file(self, *, suffix: str = "") -> AsyncIterator[Path]:
        """Context manager pour un fichier temporaire auto-nettoyé.

        Args:
            suffix: Suffixe du fichier temporaire (ex: ``".json"``).

        Yields:
            Chemin du fichier temporaire (dans ``tmp/``).

        Raises:
            PermissionDenied: Si ``filesystem_write`` non accordée.
        """
        self._require(Permission.FILESYSTEM_WRITE, "fs.temp_file")
        self._tmp_dir.mkdir(parents=True, exist_ok=True)
        import uuid  # noqa: PLC0415

        tmp_path = self._tmp_dir / f"{uuid.uuid4().hex}{suffix}"
        try:
            yield tmp_path
        finally:
            if tmp_path.exists():
                try:
                    if tmp_path.is_dir():
                        import shutil  # noqa: PLC0415
                        shutil.rmtree(tmp_path)
                    else:
                        tmp_path.unlink()
                except OSError as exc:
                    logger.warning(
                        "Plugin '{}' : échec du nettoyage de {} : {}",
                        self.plugin_name,
                        tmp_path,
                        exc,
                    )


class ConfigAccessor(_BaseAccessor):
    """Accesseur de configuration NexusDL en lecture seule.

    Requiert ``config_read``. Seules les clés whitelistées sont accessibles.
    Un plugin ne peut **pas** modifier la configuration.

    Example:
        ::

            language = api.config.get("app.language", default="en")
            max_tasks = api.config.get("download.max_concurrent_tasks", default=3)
    """

    __slots__ = ("_backend",)

    def __init__(
        self,
        plugin_name: str,
        permissions: frozenset[str],
        backend: ConfigBackend | None,
    ) -> None:
        """Initialise l'accesseur.

        Args:
            plugin_name: Nom du plugin.
            permissions: Permissions déclarées.
            backend: Backend de config, ou None.
        """
        super().__init__(plugin_name, permissions)
        self._backend = backend

    def get(self, key: str, default: Any = None) -> Any:
        """Lit une clé de config par chemin pointé.

        Args:
            key: Chemin pointé (ex: ``"download.max_concurrent_tasks"``).
            default: Valeur par défaut si la clé n'existe pas ou n'est pas
                autorisée.

        Returns:
            La valeur, ou ``default``.

        Raises:
            PermissionDenied: Si ``config_read`` non accordée.
        """
        self._require(Permission.CONFIG_READ, "config.get")
        if self._backend is None:
            return default
        return self._backend.get(key, default)

    def keys(self) -> list[str]:
        """Liste les clés autorisées en lecture.

        Returns:
            Liste de chemins pointés.

        Raises:
            PermissionDenied: Si ``config_read`` non accordée.
        """
        self._require(Permission.CONFIG_READ, "config.keys")
        if self._backend is None:
            return []
        return self._backend.keys()


# ============================================================================
#  PluginAPI — façade principale
# ============================================================================


class PluginAPI:
    """Façade principale exposée à chaque plugin.

    Regroupe tous les accesseurs. Chaque accesseur vérifie lui-même ses
    permissions — la façade ne fait que router.

    Une instance est créée par le loader pour chaque plugin, avec :
        - les permissions déclarées dans le manifest,
        - les backends disponibles (None si un service est absent).

    Attributes:
        plugin_name: Nom du plugin propriétaire.
        permissions: Permissions déclarées (frozenset).
        notify: Accesseur de notification.
        library: Accesseur de bibliothèque.
        http: Accesseur HTTP.
        fs: Accesseur filesystem (sandbox par plugin).
        config: Accesseur de configuration.

    Example:
        ::

            class MyPlugin:
                def __init__(self, api: PluginAPI, context: PluginContext) -> None:
                    self.api = api

                async def on_new_chapter(self, payload: ChapterPayload) -> None:
                    await self.api.notify.success(
                        "Nouveau chapitre",
                        f"{payload.manga_title} — ch. {payload.chapter_number}",
                    )
    """

    __slots__ = (
        "plugin_name",
        "permissions",
        "notify",
        "library",
        "http",
        "fs",
        "config",
    )

    def __init__(
        self,
        plugin_name: str,
        permissions: frozenset[str],
        *,
        notification_backend: NotificationBackend | None = None,
        library_backend: LibraryBackend | None = None,
        http_backend: HttpBackend | None = None,
        filesystem_base_dir: Path | None = None,
        config_backend: ConfigBackend | None = None,
    ) -> None:
        """Initialise l'API plugin.

        Args:
            plugin_name: Nom du plugin propriétaire.
            permissions: Permissions déclarées (frozenset de valeurs de
                ``Permission``). Une permission absente bloque l'accès.
            notification_backend: Backend de notification (optionnel).
            library_backend: Backend de bibliothèque (optionnel).
            http_backend: Backend HTTP (optionnel).
            filesystem_base_dir: Racine des sandbox (obligatoire si le
                plugin a des permissions filesystem).
            config_backend: Backend de config (optionnel).
        """
        self.plugin_name = plugin_name
        self.permissions = permissions

        self.notify = Notifier(plugin_name, permissions, notification_backend)
        self.library = LibraryAccessor(plugin_name, permissions, library_backend)
        self.http = HttpAccessor(plugin_name, permissions, http_backend)
        self.config = ConfigAccessor(plugin_name, permissions, config_backend)

        # FilesystemAccessor nécessite un base_dir — si absent, on utilise
        # un dossier temporaire (utile en test) mais on log un warning.
        if filesystem_base_dir is None and (
            "filesystem_read" in permissions or "filesystem_write" in permissions
        ):
            logger.warning(
                "Plugin '{}' : permissions filesystem demandées mais aucun base_dir — "
                "les opérations fs lèveront ServiceUnavailable",
                plugin_name,
            )
            filesystem_base_dir = Path("/tmp/nexusdl_plugin_sandbox_fallback")  # noqa: S108

        self.fs = FilesystemAccessor(
            plugin_name,
            permissions,
            filesystem_base_dir or Path("/tmp/nexusdl_plugin_sandbox_fallback"),  # noqa: S108
        )

    # --- Helpers -----------------------------------------------------------

    def has_permission(self, permission: Permission | str) -> bool:
        """Vérifie si une permission est accordée.

        Args:
            permission: Permission à tester (``Permission`` ou valeur str).

        Returns:
            True si accordée.
        """
        value = permission.value if isinstance(permission, Permission) else permission
        return value in self.permissions

    def require(self, permission: Permission, operation: str = "operation") -> None:
        """Vérifie une permission ou lève.

        Args:
            permission: Permission requise.
            operation: Nom de l'opération (pour le message d'erreur).

        Raises:
            PermissionDenied: Si la permission n'est pas accordée.
        """
        if not self.has_permission(permission):
            raise PermissionDenied(self.plugin_name, permission, operation)

    def __repr__(self) -> str:
        """Représentation textuelle pour le debug.

        Returns:
            Chaîne du type ``PluginAPI(name='x', permissions=5)``.
        """
        return (
            f"PluginAPI(name={self.plugin_name!r}, "
            f"permissions={len(self.permissions)})"
        )


# ============================================================================
#  Helpers de construction
# ============================================================================


def make_api_for_plugin(
    plugin_name: str,
    permissions: list[str] | frozenset[str],
    *,
    settings: Settings | None = None,
    notification_backend: NotificationBackend | None = None,
    library_backend: LibraryBackend | None = None,
    http_backend: HttpBackend | None = None,
    config_backend: ConfigBackend | None = None,
    filesystem_base_dir: Path | None = None,
) -> PluginAPI:
    """Construit une `PluginAPI` pour un plugin (utilisé par le loader).

    Wrapper qui normalise les permissions en frozenset et dérive le
    ``filesystem_base_dir`` depuis les settings si non fourni.

    Args:
        plugin_name: Nom du plugin.
        permissions: Permissions déclarées (liste ou frozenset).
        settings: Settings NexusDL (utilisé pour dériver le base_dir fs).
        notification_backend: Backend de notification.
        library_backend: Backend de bibliothèque.
        http_backend: Backend HTTP.
        config_backend: Backend de config.
        filesystem_base_dir: Racine des sandbox. Si None et ``settings``
            fourni, dérivé de ``settings.paths.config_dir / "plugins_data"``.

    Returns:
        Instance de `PluginAPI` prête à être injectée dans un plugin.
    """
    perms = frozenset(permissions)

    if filesystem_base_dir is None and settings is not None:
        try:
            # settings.paths.config_dir peut être None (résolu par platformdirs)
            from nexusdl.core.paths import get_config_dir  # noqa: PLC0415

            filesystem_base_dir = get_config_dir() / "plugins_data"
        except ImportError:
            filesystem_base_dir = None

    return PluginAPI(
        plugin_name=plugin_name,
        permissions=perms,
        notification_backend=notification_backend,
        library_backend=library_backend,
        http_backend=http_backend,
        filesystem_base_dir=filesystem_base_dir,
        config_backend=config_backend,
    )


# ============================================================================
#  Exports publics
# ============================================================================

__all__ = [
    "DEFAULT_HTTP_TIMEOUT",
    "MAX_FILE_WRITE_BYTES",
    "MAX_HTTP_RESPONSE_BYTES",
    "MAX_HTTP_TIMEOUT",
    "MAX_LIBRARY_PAGE_SIZE",
    "PLUGIN_API_VERSION",
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
    "make_api_for_plugin",
]
