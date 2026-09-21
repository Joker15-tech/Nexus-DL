"""Mixin API-based pour les parseurs NexusDL.

Ce module expose :class:`ApiBasedMixin`, une brique réutilisable qui fournit
à un parser NexusDL toutes les primitives nécessaires pour dialoguer avec
une **API REST** (ou GraphQL-like) exposée par un site manga :

* Client HTTP JSON typé construit sur la ``HttpSession`` du parser.
* Gestion complète de l'**authentification** : Bearer, API key en header ou
  query, Basic Auth, refresh token OAuth2-like.
* **Rate limiting** par endpoint (token bucket + délai minimal).
* **Retry** avec backoff exponentiel + jitter sur erreurs réseau et codes
  5xx/429.
* **Pagination** générique : offset/limit, page-based, cursor-based, link
  header (RFC 5988).
* **Cache** en mémoire TTL par URL (respect des en-têtes ``Cache-Control``
  et ``ETag`` si activé).
* **Extraction JSONPath-like** légère (dotted paths + index de listes).
* Hooks de normalisation (``api_on_response``, ``api_on_error``).
* Gestion fine des erreurs API (mapping statut → exception NexusDL).

Contrat implicite du parser hôte
--------------------------------

Le parser hôte doit fournir :

* ``self.config: SiteConfig`` (avec ``id`` et éventuellement ``default_headers``).
* ``self.session: HttpSession`` (session httpx async du projet).
* ``self.logger`` (optionnel — sinon :func:`get_logger` est utilisé).

Le mixin **ne dépend jamais** de ``interfaces/`` et respecte strictement la
clean architecture.

Example:
    Utilisation typique :

    >>> class MangaDexParser(ApiBasedMixin, BaseParser):
    ...     site_id = "mangadex"
    ...     api_base_url = "https://api.mangadex.org"
    ...     api_default_headers = {"Accept": "application/json"}
    ...     api_rate_limit_per_second = 5.0
    ...
    ...     async def search(self, query: str, *, page: int = 1):
    ...         data = await self.api_get(
    ...             "/manga",
    ...             params={"title": query, "limit": 20, "offset": (page - 1) * 20},
    ...         )
    ...         return self._parse_search_json(data)
"""

from __future__ import annotations

import asyncio
import contextlib
import json as _json
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Final, Literal, TypeAlias
from urllib.parse import urljoin, urlparse

import httpx

from nexusdl.core.exceptions import (
    ApiAuthenticationError,
    ApiError,
    ApiNotFoundError,
    ApiRateLimitError,
    ApiServerError,
    ApiValidationError,
    ParseError,
)
from nexusdl.core.logger import get_logger

__all__ = [
    "ApiBasedMixin",
    "ApiAuth",
    "ApiCacheEntry",
    "ApiPagination",
    "ApiRetryPolicy",
]


# ---------------------------------------------------------------------------
# Types et constantes
# ---------------------------------------------------------------------------

AuthScheme: TypeAlias = Literal["none", "bearer", "api_key_header", "api_key_query", "basic"]

PaginationMode: TypeAlias = Literal["offset", "page", "cursor", "link_header", "none"]

HttpMethod: TypeAlias = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

_DEFAULT_TIMEOUT: Final[float] = 20.0
_DEFAULT_MAX_RETRIES: Final[int] = 3
_DEFAULT_BACKOFF_BASE: Final[float] = 1.6
_DEFAULT_BACKOFF_MAX: Final[float] = 20.0
_DEFAULT_RATE_LIMIT: Final[float] = 4.0
_DEFAULT_CACHE_TTL: Final[int] = 300
_DEFAULT_MAX_CACHE_ENTRIES: Final[int] = 512

_RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({408, 425, 429, 500, 502, 503, 504, 522, 524})
_AUTH_STATUS: Final[frozenset[int]] = frozenset({401, 403})
_NOT_FOUND_STATUS: Final[frozenset[int]] = frozenset({404, 410})
_VALIDATION_STATUS: Final[frozenset[int]] = frozenset({400, 405, 409, 413, 415, 422})
_RATE_LIMIT_STATUS: Final[int] = 429

_LINK_HEADER_RE: Final[str] = r'<([^>]+)>;\s*rel="([^"]+)"'


# ---------------------------------------------------------------------------
# Dataclasses de configuration
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ApiAuth:
    """Configuration d'authentification pour l'API.

    Attributes:
        scheme: Schéma d'authentification.
        token: Token principal (bearer, api key, ou ``user:pass`` en basic).
        header_name: Nom du header pour ``api_key_header`` (ex. ``X-API-Key``).
        query_param: Nom du paramètre pour ``api_key_query`` (ex. ``api_key``).
        refresh_callback: Callable async retournant un nouveau token.
        refresh_threshold_seconds: Rafraîchit si le token expire dans moins
            de N secondes (nécessite ``expires_at``).
        expires_at: Timestamp UNIX d'expiration du token (optionnel).
    """

    scheme: AuthScheme = "none"
    token: str | None = None
    header_name: str = "Authorization"
    query_param: str = "api_key"
    refresh_callback: Callable[[], Awaitable[str]] | None = None
    refresh_threshold_seconds: int = 60
    expires_at: float | None = None

    @property
    def is_expiring_soon(self) -> bool:
        """Indique si le token doit être rafraîchi."""
        if self.expires_at is None:
            return False
        return (self.expires_at - time.time()) <= self.refresh_threshold_seconds


@dataclass(slots=True)
class ApiRetryPolicy:
    """Politique de retry pour les requêtes API.

    Attributes:
        max_retries: Nombre maximum de tentatives (au-delà de la première).
        backoff_base: Base du backoff exponentiel.
        backoff_max: Plafond du délai entre tentatives.
        jitter: Ajoute un jitter aléatoire (±jitter * delay).
        retry_status: Codes HTTP déclenchant un retry.
        retry_methods: Méthodes HTTP idempotentes éligibles au retry.
        respect_retry_after: Respecte l'en-tête ``Retry-After`` si présent.
    """

    max_retries: int = _DEFAULT_MAX_RETRIES
    backoff_base: float = _DEFAULT_BACKOFF_BASE
    backoff_max: float = _DEFAULT_BACKOFF_MAX
    jitter: float = 0.25
    retry_status: frozenset[int] = field(default_factory=lambda: _RETRYABLE_STATUS)
    retry_methods: frozenset[str] = field(
        default_factory=lambda: frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
    )
    respect_retry_after: bool = True


@dataclass(slots=True)
class ApiPagination:
    """Configuration de pagination pour un endpoint.

    Attributes:
        mode: Stratégie de pagination.
        page_param: Nom du paramètre de page (``page``).
        offset_param: Nom du paramètre d'offset (``offset``).
        limit_param: Nom du paramètre de limite (``limit``).
        cursor_param: Nom du paramètre de curseur (``cursor``).
        cursor_field: Chemin JSON où lire le prochain curseur.
        limit: Nombre d'éléments par page.
        items_field: Chemin JSON où lire la liste d'items.
        next_field: Chemin JSON où lire l'URL/curseur suivant (optionnel).
        has_more_field: Chemin JSON d'un booléen indiquant s'il y a plus.
        max_pages: Nombre maximum de pages à parcourir (sécurité).
    """

    mode: PaginationMode = "offset"
    page_param: str = "page"
    offset_param: str = "offset"
    limit_param: str = "limit"
    cursor_param: str = "cursor"
    cursor_field: str = "next_cursor"
    limit: int = 20
    items_field: str = "data"
    next_field: str | None = None
    has_more_field: str | None = None
    max_pages: int = 100


@dataclass(slots=True)
class ApiCacheEntry:
    """Entrée de cache pour une réponse API.

    Attributes:
        payload: Charge utile JSON mise en cache.
        stored_at: Timestamp monotone d'insertion.
        ttl: Durée de vie en secondes.
        etag: ETag associé (pour revalidation future).
    """

    payload: Any
    stored_at: float
    ttl: int
    etag: str | None = None

    @property
    def is_expired(self) -> bool:
        """Indique si l'entrée a expiré."""
        return (time.monotonic() - self.stored_at) >= self.ttl


# ---------------------------------------------------------------------------
# Mixin principal
# ---------------------------------------------------------------------------


class ApiBasedMixin:
    """Mixin fournissant un client API REST complet pour NexusDL.

    Toutes les requêtes passent par la ``HttpSession`` du parser (donc
    bénéficient des cookies, proxies, retry réseau déjà configurés) mais
    ajoutent une couche sémantique dédiée aux API JSON : auth, rate limiting,
    pagination, cache, retry applicatif, décodage et mapping d'erreurs.

    Class Attributes:
        api_base_url: URL racine de l'API (ex. ``https://api.mangadex.org``).
        api_default_headers: Headers additionnels pour toutes les requêtes.
        api_default_params: Query params additionnels pour toutes les requêtes.
        api_auth: Configuration :class:`ApiAuth`.
        api_retry: Configuration :class:`ApiRetryPolicy`.
        api_pagination: Configuration :class:`ApiPagination` par défaut.
        api_rate_limit_per_second: Nombre max de requêtes par seconde.
        api_rate_limit_burst: Taille du burst autorisé (token bucket).
        api_timeout: Timeout par défaut en secondes.
        api_cache_enabled: Active le cache en mémoire.
        api_cache_ttl: TTL par défaut du cache.
        api_cache_max_entries: Nombre max d'entrées de cache.
        api_cache_respect_headers: Respecte ``Cache-Control``/``ETag``.
        api_raise_on_error_status: Lève des exceptions typées sur erreurs.
        api_json_response_field: Si non ``None``, extrait ce champ racine de
            chaque réponse JSON avant de la retourner.
    """

    # --- Endpoint ---
    api_base_url: ClassVar[str | None] = None
    api_default_headers: ClassVar[dict[str, str]] = {}
    api_default_params: ClassVar[dict[str, Any]] = {}

    # --- Auth / retry / pagination ---
    api_auth: ClassVar[ApiAuth] = ApiAuth()
    api_retry: ClassVar[ApiRetryPolicy] = ApiRetryPolicy()
    api_pagination: ClassVar[ApiPagination] = ApiPagination()

    # --- Rate limiting ---
    api_rate_limit_per_second: ClassVar[float] = _DEFAULT_RATE_LIMIT
    api_rate_limit_burst: ClassVar[int] = 4

    # --- Divers ---
    api_timeout: ClassVar[float] = _DEFAULT_TIMEOUT
    api_cache_enabled: ClassVar[bool] = True
    api_cache_ttl: ClassVar[int] = _DEFAULT_CACHE_TTL
    api_cache_max_entries: ClassVar[int] = _DEFAULT_MAX_CACHE_ENTRIES
    api_cache_respect_headers: ClassVar[bool] = True
    api_raise_on_error_status: ClassVar[bool] = True
    api_json_response_field: ClassVar[str | None] = None

    # --- Contrat attendu du parser hôte ---
    config: Any
    session: Any

    # --- État interne par instance (lazy) ---
    _api_cache: ClassVar[dict[str, ApiCacheEntry]] = {}
    _api_token_bucket: ClassVar[dict[str, float]] = {}
    _api_lock: ClassVar[asyncio.Lock | None] = None
    _api_token: ClassVar[str | None] = None

    # ------------------------------------------------------------------
    # Setup / helpers internes
    # ------------------------------------------------------------------

    def _api_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="api_based"``.
        """
        existing = getattr(self, "logger", None)
        if existing is not None:
            return existing
        logger = get_logger(self.__class__.__module__)
        site_id = getattr(self.config, "id", "unknown")
        return logger.bind(site_id=site_id, mixin="api_based")

    def _api_get_lock(self) -> asyncio.Lock:
        """Retourne le verrou global de rate limiting.

        Returns:
            Verrou asyncio partagé entre tous les appels API.
        """
        lock = ApiBasedMixin._api_lock
        if lock is None:
            lock = asyncio.Lock()
            ApiBasedMixin._api_lock = lock
        return lock

    def _api_base(self) -> str:
        """Retourne l'URL de base de l'API.

        Returns:
            URL racine sans slash final.

        Raises:
            ApiError: Si aucune URL de base n'est disponible.
        """
        base = self.api_base_url
        if base:
            return base.rstrip("/")
        domains = getattr(self.config, "domains", None) or []
        if not domains:
            raise ApiError(
                f"Aucune URL d'API configurée pour {self.config.id!r}"
            )
        first = domains[0]
        return (str(first) if not isinstance(first, str) else first).rstrip("/")

    def _api_url(self, path: str) -> str:
        """Résout un chemin relatif en URL absolue.

        Args:
            path: Chemin absolu (``/manga``) ou URL complète.

        Returns:
            URL absolue.
        """
        if path.startswith(("http://", "https://")):
            return path
        return urljoin(self._api_base() + "/", path.lstrip("/"))

    def _api_default_headers(self) -> dict[str, str]:
        """Assemble les headers par défaut (config + parser + UA + Accept).

        Returns:
            Dictionnaire de headers fusionnés.
        """
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": self._api_user_agent(),
        }
        config_headers = getattr(self.config, "default_headers", None) or {}
        headers.update({str(k): str(v) for k, v in config_headers.items()})
        headers.update(
            {str(k): str(v) for k, v in self.api_default_headers.items()}
        )
        return headers

    def _api_user_agent(self) -> str:
        """Récupère l'User-Agent courant.

        Returns:
            User-Agent effectif.
        """
        ua = getattr(self.session, "user_agent", None)
        if ua:
            return str(ua)
        headers = getattr(self.config, "default_headers", None) or {}
        return str(
            headers.get("User-Agent")
            or headers.get("user-agent")
            or "NexusDL/1.0 (+https://github.com/nexusdl)"
        )

    # ------------------------------------------------------------------
    # Authentification
    # ------------------------------------------------------------------

    async def api_set_token(self, token: str, *, expires_at: float | None = None) -> None:
        """Définit le token d'authentification courant.

        Args:
            token: Token (bearer ou API key).
            expires_at: Timestamp UNIX d'expiration (optionnel).
        """
        ApiBasedMixin._api_token = token
        self.api_auth.token = token
        if expires_at is not None:
            self.api_auth.expires_at = expires_at

    async def api_refresh_token(self) -> str:
        """Rafraîchit le token via le callback configuré.

        Returns:
            Nouveau token.

        Raises:
            ApiAuthenticationError: Si aucun callback n'est défini ou échoue.
        """
        callback = self.api_auth.refresh_callback
        if callback is None:
            raise ApiAuthenticationError(
                f"Aucun refresh_callback pour {self.config.id!r}"
            )
        try:
            new_token = await callback()
        except Exception as exc:  # noqa: BLE001
            raise ApiAuthenticationError(
                f"Échec du rafraîchissement de token : {exc}"
            ) from exc
        await self.api_set_token(new_token)
        self._api_logger().info("Token API rafraîchi pour {site}", site=self.config.id)
        return new_token

    async def _api_ensure_token(self) -> str | None:
        """Vérifie/rafraîchit le token avant une requête.

        Returns:
            Token courant ou ``None`` si pas d'auth.

        Raises:
            ApiAuthenticationError: Si le refresh échoue.
        """
        if self.api_auth.scheme == "none":
            return None
        token = ApiBasedMixin._api_token or self.api_auth.token
        if token and not self.api_auth.is_expiring_soon:
            return token
        if self.api_auth.refresh_callback is not None:
            return await self.api_refresh_token()
        if token:
            return token
        raise ApiAuthenticationError(
            f"Aucun token API pour {self.config.id!r} "
            f"(schéma {self.api_auth.scheme!r})"
        )

    async def _api_apply_auth(
        self,
        headers: dict[str, str],
        params: dict[str, Any],
    ) -> None:
        """Applique l'authentification aux headers/params.

        Args:
            headers: Headers à enrichir.
            params: Query params à enrichir.

        Raises:
            ApiAuthenticationError: Si le token ne peut être obtenu.
        """
        scheme = self.api_auth.scheme
        if scheme == "none":
            return
        token = await self._api_ensure_token()
        if not token:
            return

        if scheme == "bearer":
            headers["Authorization"] = f"Bearer {token}"
        elif scheme == "api_key_header":
            headers[self.api_auth.header_name] = token
        elif scheme == "api_key_query":
            params[self.api_auth.query_param] = token
        elif scheme == "basic":
            import base64

            encoded = base64.b64encode(token.encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"

    # ------------------------------------------------------------------
    # Rate limiting (token bucket)
    # ------------------------------------------------------------------

    async def _api_throttle(self, key: str = "global") -> None:
        """Applique le rate limiting token-bucket pour une clé.

        Args:
            key: Identifiant du bucket (par défaut ``global``).
        """
        rate = self.api_rate_limit_per_second
        burst = max(1, self.api_rate_limit_burst)
        if rate <= 0:
            return

        async with self._api_get_lock():
            now = time.monotonic()
            tokens_key = f"{key}:tokens"
            last_key = f"{key}:last"
            tokens = ApiBasedMixin._api_token_bucket.get(tokens_key, float(burst))
            last = ApiBasedMixin._api_token_bucket.get(last_key, now)
            elapsed = now - last
            tokens = min(float(burst), tokens + elapsed * rate)

            if tokens < 1.0:
                wait_time = (1.0 - tokens) / rate
                ApiBasedMixin._api_token_bucket[tokens_key] = tokens
                ApiBasedMixin._api_token_bucket[last_key] = now
            else:
                ApiBasedMixin._api_token_bucket[tokens_key] = tokens - 1.0
                ApiBasedMixin._api_token_bucket[last_key] = now
                return

        if wait_time > 0:
            await asyncio.sleep(wait_time)
            # Recharge après attente.
            async with self._api_get_lock():
                ApiBasedMixin._api_token_bucket[tokens_key] = float(burst - 1)
                ApiBasedMixin._api_token_bucket[last_key] = time.monotonic()

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    @staticmethod
    def _api_cache_key(method: str, url: str, params: Mapping[str, Any] | None) -> str:
        """Calcule une clé de cache déterministe.

        Args:
            method: Méthode HTTP.
            url: URL absolue.
            params: Query params.

        Returns:
            Clé de cache sous forme ``METHOD url?sorted_params``.
        """
        if not params:
            return f"{method.upper()} {url}"
        items = "&".join(f"{k}={params[k]}" for k in sorted(params))
        return f"{method.upper()} {url}?{items}"

    def _api_cache_get(self, key: str) -> ApiCacheEntry | None:
        """Récupère une entrée de cache valide.

        Args:
            key: Clé de cache.

        Returns:
            Entrée non expirée ou ``None``.
        """
        entry = ApiBasedMixin._api_cache.get(key)
        if entry is None:
            return None
        if entry.is_expired:
            ApiBasedMixin._api_cache.pop(key, None)
            return None
        return entry

    def _api_cache_put(self, key: str, payload: Any, *, ttl: int, etag: str | None = None) -> None:
        """Insère une entrée en cache avec éviction LRU simple.

        Args:
            key: Clé de cache.
            payload: Charge utile JSON.
            ttl: Durée de vie en secondes.
            etag: ETag associé.
        """
        cache = ApiBasedMixin._api_cache
        cache[key] = ApiCacheEntry(
            payload=payload,
            stored_at=time.monotonic(),
            ttl=ttl,
            etag=etag,
        )
        if len(cache) > self.api_cache_max_entries:
            oldest = min(cache.items(), key=lambda kv: kv[1].stored_at)
            cache.pop(oldest[0], None)

    def api_clear_cache(self, key: str | None = None) -> None:
        """Invalide tout ou partie du cache.

        Args:
            key: Clé précise ou ``None`` pour vider intégralement.
        """
        if key is None:
            ApiBasedMixin._api_cache.clear()
            return
        ApiBasedMixin._api_cache.pop(key, None)

    @staticmethod
    def _api_parse_cache_control(response: httpx.Response) -> int | None:
        """Extrait un TTL depuis ``Cache-Control``.

        Args:
            response: Réponse httpx.

        Returns:
            TTL en secondes, ou ``None`` si non déterminable.
        """
        header = response.headers.get("cache-control", "")
        if not header:
            return None
        for chunk in header.split(","):
            chunk = chunk.strip().lower()
            if chunk.startswith("max-age="):
                with contextlib.suppress(ValueError):
                    return int(chunk.split("=", 1)[1])
        return None

    # ------------------------------------------------------------------
    # Cœur : _api_request
    # ------------------------------------------------------------------

    async def _api_request(
        self,
        method: HttpMethod,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        data: Any | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        use_cache: bool | None = None,
        cache_ttl: int | None = None,
        raise_on_error: bool | None = None,
        skip_auth: bool = False,
        skip_throttle: bool = False,
        retry_policy: ApiRetryPolicy | None = None,
    ) -> httpx.Response:
        """Exécute une requête API complète (auth, throttle, retry, cache).

        Args:
            method: Méthode HTTP.
            path: Chemin relatif ou URL complète.
            params: Query params additionnels.
            json_body: Corps JSON.
            data: Corps form-encoded.
            headers: Headers additionnels.
            timeout: Timeout par requête.
            use_cache: Force/désactive le cache pour cet appel.
            cache_ttl: TTL spécifique pour cet appel.
            raise_on_error: Force/annule la levée d'exceptions typées.
            skip_auth: Saute l'injection d'auth.
            skip_throttle: Saute le rate limiting.
            retry_policy: Politique de retry spécifique.

        Returns:
            Réponse httpx finale.

        Raises:
            ApiError: En cas d'erreur HTTP non récupérable ou d'échec réseau.
        """
        url = self._api_url(path)
        method_upper = method.upper()
        policy = retry_policy or self.api_retry
        timeout = timeout or self.api_timeout
        raise_on_error = (
            raise_on_error if raise_on_error is not None else self.api_raise_on_error_status
        )
        use_cache = (
            use_cache if use_cache is not None else self.api_cache_enabled
        ) and method_upper == "GET"
        cache_ttl = cache_ttl or self.api_cache_ttl

        merged_params: dict[str, Any] = {}
        merged_params.update(self.api_default_params)
        if params:
            merged_params.update(params)

        merged_headers: dict[str, str] = self._api_default_headers()
        if headers:
            merged_headers.update({str(k): str(v) for k, v in headers.items()})

        if not skip_auth:
            await self._api_apply_auth(merged_headers, merged_params)

        cache_key = self._api_cache_key(method_upper, url, merged_params)
        if use_cache:
            cached = self._api_cache_get(cache_key)
            if cached is not None:
                self._api_logger().debug("Cache API HIT: {url}", url=url)
                return self._api_build_synthetic_response(url, cached.payload)

        log = self._api_logger()
        last_exception: Exception | None = None

        for attempt in range(0, policy.max_retries + 1):
            if not skip_throttle:
                await self._api_throttle(key=self._api_domain_key(url))

            log.debug(
                "API {m} {url} (tentative {n}/{t})",
                m=method_upper,
                url=url,
                n=attempt + 1,
                t=policy.max_retries + 1,
            )

            try:
                response = await self._api_execute(
                    method_upper,
                    url,
                    params=merged_params or None,
                    json_body=json_body,
                    data=data,
                    headers=merged_headers,
                    timeout=timeout,
                )
            except (httpx.HTTPError, OSError) as exc:
                last_exception = exc
                log.warning(
                    "Erreur réseau API {url}: {err} (tentative {n})",
                    url=url,
                    err=exc,
                    n=attempt + 1,
                )
                if attempt < policy.max_retries:
                    await self._api_backoff(attempt, policy)
                    continue
                raise ApiError(
                    f"Erreur réseau persistante sur {url!r}: {exc}"
                ) from exc

            # Retry sur statuts retryables (uniquement si méthode idempotente).
            if (
                response.status_code in policy.retry_status
                and method_upper in policy.retry_methods
                and attempt < policy.max_retries
            ):
                delay = self._api_retry_delay(response, attempt, policy)
                log.warning(
                    "Statut {s} sur {url} → retry dans {d:.2f}s",
                    s=response.status_code,
                    url=url,
                    d=delay,
                )
                await asyncio.sleep(delay)
                continue

            # Cache si activé et 2xx.
            if use_cache and 200 <= response.status_code < 300:
                etag = response.headers.get("etag")
                ttl = cache_ttl
                if self.api_cache_respect_headers:
                    header_ttl = self._api_parse_cache_control(response)
                    if header_ttl is not None:
                        ttl = header_ttl
                try:
                    payload = response.json()
                    self._api_cache_put(cache_key, payload, ttl=ttl, etag=etag)
                except ValueError:
                    pass

            if raise_on_error and response.status_code >= 400:
                raise self._api_error_from_response(response, url)

            return response

        # Ne devrait pas arriver, mais sécurité.
        raise ApiError(
            f"Échec inattendu de la requête API {url!r} "
            f"(dernière erreur : {last_exception})"
        )

    async def _api_execute(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        json_body: Any | None,
        data: Any | None,
        headers: Mapping[str, str],
        timeout: float,
    ) -> httpx.Response:
        """Exécute la requête HTTP sous-jacente via la session du parser.

        Args:
            method: Méthode HTTP.
            url: URL absolue.
            params: Query params.
            json_body: Corps JSON.
            data: Corps form.
            headers: Headers.
            timeout: Timeout en secondes.

        Returns:
            Réponse httpx.
        """
        request = getattr(self.session, "request", None)
        if callable(request):
            result = request(
                method,
                url,
                params=params,
                json=json_body,
                data=data,
                headers=dict(headers),
                timeout=timeout,
            )
            if asyncio.iscoroutine(result):
                return await result
            return result  # type: ignore[return-value]

        method_lower = method.lower()
        fn = getattr(self.session, method_lower, None)
        if not callable(fn):
            raise ApiError(
                f"Méthode HTTP {method!r} non supportée par la session "
                f"{type(self.session).__name__}"
            )
        kwargs: dict[str, Any] = {
            "headers": dict(headers),
            "timeout": timeout,
        }
        if params:
            kwargs["params"] = dict(params)
        if json_body is not None:
            kwargs["json"] = json_body
        if data is not None:
            kwargs["data"] = data
        result = fn(url, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result  # type: ignore[return-value]

    def _api_domain_key(self, url: str) -> str:
        """Retourne la clé de bucket pour une URL.

        Args:
            url: URL absolue.

        Returns:
            Domaine (netloc) utilisé comme clé de rate limiting.
        """
        return urlparse(url).netloc.lower() or "global"

    async def _api_backoff(self, attempt: int, policy: ApiRetryPolicy) -> None:
        """Effectue un sleep de backoff exponentiel avec jitter.

        Args:
            attempt: Index de tentative (0-based).
            policy: Politique de retry.
        """
        delay = min(policy.backoff_max, policy.backoff_base ** attempt)
        if policy.jitter > 0:
            delay *= 1.0 + random.uniform(-policy.jitter, policy.jitter)
        await asyncio.sleep(max(0.0, delay))

    @staticmethod
    def _api_retry_delay(
        response: httpx.Response,
        attempt: int,
        policy: ApiRetryPolicy,
    ) -> float:
        """Calcule le délai avant retry (respect ``Retry-After`` si activé).

        Args:
            response: Réponse ayant échoué.
            attempt: Index de tentative.
            policy: Politique de retry.

        Returns:
            Délai en secondes.
        """
        if policy.respect_retry_after:
            retry_after = response.headers.get("retry-after")
            if retry_after:
                with contextlib.suppress(ValueError):
                    return float(retry_after)
        delay = min(policy.backoff_max, policy.backoff_base ** attempt)
        if policy.jitter > 0:
            delay *= 1.0 + random.uniform(-policy.jitter, policy.jitter)
        return max(0.0, delay)

    def _api_error_from_response(
        self, response: httpx.Response, url: str
    ) -> ApiError:
        """Mappe une réponse HTTP en exception typée.

        Args:
            response: Réponse HTTP en erreur.
            url: URL ciblée.

        Returns:
            Exception correspondante.
        """
        status = response.status_code
        snippet = ""
        with contextlib.suppress(Exception):
            snippet = response.text[:400]

        if status in _AUTH_STATUS:
            return ApiAuthenticationError(
                f"HTTP {status} sur {url!r} — authentification requise "
                f"({snippet!r})"
            )
        if status in _NOT_FOUND_STATUS:
            return ApiNotFoundError(f"HTTP {status} sur {url!r} — ressource absente")
        if status == _RATE_LIMIT_STATUS:
            retry_after = response.headers.get("retry-after", "?")
            return ApiRateLimitError(
                f"HTTP 429 sur {url!r} — rate limit (Retry-After={retry_after})"
            )
        if status in _VALIDATION_STATUS:
            return ApiValidationError(
                f"HTTP {status} sur {url!r} — requête invalide ({snippet!r})"
            )
        if 500 <= status < 600:
            return ApiServerError(
                f"HTTP {status} sur {url!r} — erreur serveur ({snippet!r})"
            )
        return ApiError(f"HTTP {status} sur {url!r} ({snippet!r})")

    @staticmethod
    def _api_build_synthetic_response(url: str, payload: Any) -> httpx.Response:
        """Reconstruit une réponse httpx synthétique depuis le cache.

        Args:
            url: URL cible.
            payload: Charge utile JSON mise en cache.

        Returns:
            Réponse httpx factice.
        """
        body = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return httpx.Response(
            status_code=200,
            content=body,
            headers={"content-type": "application/json; charset=utf-8"},
            request=httpx.Request("GET", url),
        )

    # ------------------------------------------------------------------
    # API publique haut-niveau
    # ------------------------------------------------------------------

    async def api_get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        use_cache: bool | None = None,
        cache_ttl: int | None = None,
        raise_on_error: bool | None = None,
        skip_auth: bool = False,
        skip_throttle: bool = False,
    ) -> Any:
        """GET JSON retournant la charge utile décodée.

        Args:
            path: Chemin relatif ou URL.
            params: Query params.
            headers: Headers additionnels.
            timeout: Timeout.
            use_cache: Force/désactive le cache.
            cache_ttl: TTL spécifique.
            raise_on_error: Force/annule la levée typée.
            skip_auth: Saute l'auth.
            skip_throttle: Saute le throttling.

        Returns:
            Charge utile JSON décodée (éventuellement extraite via
            ``api_json_response_field``).

        Raises:
            ApiError: Sur erreur HTTP ou décodage.
        """
        response = await self._api_request(
            "GET",
            path,
            params=params,
            headers=headers,
            timeout=timeout,
            use_cache=use_cache,
            cache_ttl=cache_ttl,
            raise_on_error=raise_on_error,
            skip_auth=skip_auth,
            skip_throttle=skip_throttle,
        )
        return await self._api_decode(response)

    async def api_post(
        self,
        path: str,
        *,
        json_body: Any | None = None,
        data: Any | None = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        raise_on_error: bool | None = None,
        skip_auth: bool = False,
    ) -> Any:
        """POST JSON retournant la charge utile décodée.

        Args:
            path: Chemin relatif ou URL.
            json_body: Corps JSON.
            data: Corps form-encoded.
            params: Query params.
            headers: Headers additionnels.
            timeout: Timeout.
            raise_on_error: Force/annule la levée typée.
            skip_auth: Saute l'auth.

        Returns:
            Charge utile JSON décodée.
        """
        response = await self._api_request(
            "POST",
            path,
            params=params,
            json_body=json_body,
            data=data,
            headers=headers,
            timeout=timeout,
            use_cache=False,
            raise_on_error=raise_on_error,
            skip_auth=skip_auth,
        )
        return await self._api_decode(response)

    async def api_put(
        self,
        path: str,
        *,
        json_body: Any | None = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """PUT JSON retournant la charge utile décodée.

        Args:
            path: Chemin relatif ou URL.
            json_body: Corps JSON.
            params: Query params.
            headers: Headers additionnels.
            timeout: Timeout.

        Returns:
            Charge utile JSON décodée.
        """
        response = await self._api_request(
            "PUT",
            path,
            params=params,
            json_body=json_body,
            headers=headers,
            timeout=timeout,
            use_cache=False,
        )
        return await self._api_decode(response)

    async def api_patch(
        self,
        path: str,
        *,
        json_body: Any | None = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """PATCH JSON retournant la charge utile décodée.

        Args:
            path: Chemin relatif ou URL.
            json_body: Corps JSON.
            params: Query params.
            headers: Headers additionnels.
            timeout: Timeout.

        Returns:
            Charge utile JSON décodée.
        """
        response = await self._api_request(
            "PATCH",
            path,
            params=params,
            json_body=json_body,
            headers=headers,
            timeout=timeout,
            use_cache=False,
        )
        return await self._api_decode(response)

    async def api_delete(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """DELETE retournant la charge utile décodée (si présente).

        Args:
            path: Chemin relatif ou URL.
            params: Query params.
            headers: Headers additionnels.
            timeout: Timeout.

        Returns:
            Charge utile JSON décodée (ou ``None`` si corps vide).
        """
        response = await self._api_request(
            "DELETE",
            path,
            params=params,
            headers=headers,
            timeout=timeout,
            use_cache=False,
        )
        if not response.content:
            return None
        return await self._api_decode(response)

    async def _api_decode(self, response: httpx.Response) -> Any:
        """Décode une réponse HTTP en JSON en respectant les hooks.

        Args:
            response: Réponse httpx.

        Returns:
            Charge utile décodée.

        Raises:
            ParseError: Si le corps n'est pas un JSON valide.
        """
        if not response.content:
            return None
        try:
            payload = response.json()
        except ValueError as exc:
            snippet = ""
            with contextlib.suppress(Exception):
                snippet = response.text[:200]
            raise ParseError(
                f"Réponse non-JSON depuis {response.request.url} "
                f"(status={response.status_code}, extrait={snippet!r})"
            ) from exc

        payload = await self.api_on_response(response, payload)

        field = self.api_json_response_field
        if field:
            extracted = self.api_extract(payload, field)
            if extracted is None:
                raise ParseError(
                    f"Champ racine {field!r} absent de la réponse "
                    f"({response.request.url})"
                )
            return extracted
        return payload

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    async def api_paginate(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        pagination: ApiPagination | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> AsyncIterator[Any]:
        """Itère sur les pages d'un endpoint paginé.

        Args:
            path: Chemin relatif ou URL.
            params: Query params de base.
            pagination: Configuration de pagination.
            headers: Headers additionnels.
            timeout: Timeout par page.

        Yields:
            Chaque item individuel extrait de ``pagination.items_field``.
        """
        cfg = pagination or self.api_pagination
        base_params: dict[str, Any] = dict(params or {})

        if cfg.mode == "link_header":
            async for item in self._api_paginate_link_header(
                path, base_params, headers, timeout, cfg
            ):
                yield item
            return

        if cfg.mode == "cursor":
            async for item in self._api_paginate_cursor(
                path, base_params, headers, timeout, cfg
            ):
                yield item
            return

        if cfg.mode == "none":
            payload = await self.api_get(
                path, params=base_params, headers=headers, timeout=timeout
            )
            for item in self._api_extract_items(payload, cfg.items_field):
                yield item
            return

        # offset / page
        for page in range(1, cfg.max_pages + 1):
            page_params = dict(base_params)
            if cfg.mode == "page":
                page_params[cfg.page_param] = page
            else:
                page_params[cfg.offset_param] = (page - 1) * cfg.limit
            page_params[cfg.limit_param] = cfg.limit

            payload = await self.api_get(
                path, params=page_params, headers=headers, timeout=timeout
            )
            items = self._api_extract_items(payload, cfg.items_field)
            if not items:
                return
            for item in items:
                yield item

            if not self._api_has_more(payload, cfg, len(items)):
                return

    async def _api_paginate_cursor(
        self,
        path: str,
        base_params: dict[str, Any],
        headers: Mapping[str, str] | None,
        timeout: float | None,
        cfg: ApiPagination,
    ) -> AsyncIterator[Any]:
        """Pagination par curseur.

        Args:
            path: Chemin relatif.
            base_params: Params de base.
            headers: Headers additionnels.
            timeout: Timeout par page.
            cfg: Configuration de pagination.

        Yields:
            Items des pages successives.
        """
        cursor: str | None = None
        for _ in range(cfg.max_pages):
            page_params = dict(base_params)
            page_params[cfg.limit_param] = cfg.limit
            if cursor is not None:
                page_params[cfg.cursor_param] = cursor

            payload = await self.api_get(
                path, params=page_params, headers=headers, timeout=timeout
            )
            items = self._api_extract_items(payload, cfg.items_field)
            if not items:
                return
            for item in items:
                yield item

            next_cursor = self.api_extract(payload, cfg.cursor_field)
            if not next_cursor or next_cursor == cursor:
                return
            cursor = str(next_cursor)

    async def _api_paginate_link_header(
        self,
        path: str,
        base_params: dict[str, Any],
        headers: Mapping[str, str] | None,
        timeout: float | None,
        cfg: ApiPagination,
    ) -> AsyncIterator[Any]:
        """Pagination via en-tête HTTP ``Link`` (RFC 5988).

        Args:
            path: Chemin relatif.
            base_params: Params de base.
            headers: Headers additionnels.
            timeout: Timeout par page.
            cfg: Configuration de pagination.

        Yields:
            Items des pages successives.
        """
        import re as _re

        next_url: str | None = self._api_url(path)
        current_params: dict[str, Any] | None = dict(base_params)
        pages = 0
        while next_url and pages < cfg.max_pages:
            response = await self._api_request(
                "GET",
                next_url,
                params=current_params,
                headers=headers,
                timeout=timeout,
                use_cache=False,
            )
            payload = await self._api_decode(response)
            items = self._api_extract_items(payload, cfg.items_field)
            for item in items:
                yield item

            link_header = response.headers.get("link", "")
            next_url = None
            current_params = None
            for match in _re.finditer(_LINK_HEADER_RE, link_header):
                link_url, rel = match.group(1), match.group(2)
                if rel == "next":
                    next_url = link_url
                    break
            pages += 1

    # ------------------------------------------------------------------
    # Extraction / navigation JSON
    # ------------------------------------------------------------------

    @staticmethod
    def _api_extract_items(payload: Any, items_field: str) -> list[Any]:
        """Extrait une liste d'items depuis un payload JSON.

        Args:
            payload: Charge utile.
            items_field: Chemin JSON de la liste.

        Returns:
            Liste (vide si introuvable).
        """
        extracted = ApiBasedMixin.api_extract(payload, items_field)
        if isinstance(extracted, list):
            return extracted
        if isinstance(payload, list):
            return payload
        return []

    def _api_has_more(
        self, payload: Any, cfg: ApiPagination, items_count: int
    ) -> bool:
        """Détermine s'il reste des pages à parcourir.

        Args:
            payload: Charge utile JSON.
            cfg: Configuration de pagination.
            items_count: Nombre d'items dans la page courante.

        Returns:
            ``True`` s'il faut continuer.
        """
        if cfg.has_more_field:
            value = self.api_extract(payload, cfg.has_more_field)
            if isinstance(value, bool):
                return value
        if cfg.next_field:
            value = self.api_extract(payload, cfg.next_field)
            return bool(value)
        # Sinon : on continue tant que la page est pleine.
        return items_count >= cfg.limit

    @staticmethod
    def api_extract(payload: Any, path: str | None) -> Any:
        """Extrait une valeur via un chemin JSON (dotted path).

        Le chemin supporte :

        * clés imbriquées : ``"data.attributes.title"``
        * index de liste : ``"relationships.0.id"``

        Args:
            payload: Objet Python (dict/list).
            path: Chemin JSON ou ``None`` pour retourner le payload tel quel.

        Returns:
            Valeur extraite, ou ``None`` si le chemin est introuvable.
        """
        if path is None:
            return payload
        current: Any = payload
        for part in path.split("."):
            if current is None:
                return None
            if isinstance(current, list):
                if part.isdigit():
                    idx = int(part)
                    if 0 <= idx < len(current):
                        current = current[idx]
                    else:
                        return None
                else:
                    collected = []
                    for item in current:
                        sub = ApiBasedMixin.api_extract(item, part)
                        if sub is not None:
                            collected.append(sub)
                    current = collected or None
            elif isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    # ------------------------------------------------------------------
    # Hooks surchargeables
    # ------------------------------------------------------------------

    async def api_on_response(self, response: httpx.Response, payload: Any) -> Any:
        """Hook post-décodage (surchargeable).

        Args:
            response: Réponse httpx d'origine.
            payload: Charge utile JSON décodée.

        Returns:
            Charge utile éventuellement transformée.
        """
        return payload

    async def api_on_error(self, response: httpx.Response, error: Exception) -> None:
        """Hook appelé avant lever d'une exception API (surchargeable).

        Args:
            response: Réponse en erreur.
            error: Exception sur le point d'être levée.
        """

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def api_debug_info(self) -> dict[str, Any]:
        """Retourne un dictionnaire de diagnostic du mixin.

        Returns:
            Informations sur la configuration et l'état interne.
        """
        return {
            "base_url": self.api_base_url,
            "auth_scheme": self.api_auth.scheme,
            "has_token": bool(self.api_auth.token),
            "rate_limit": self.api_rate_limit_per_second,
            "cache_enabled": self.api_cache_enabled,
            "cache_size": len(ApiBasedMixin._api_cache),
            "cache_max_entries": self.api_cache_max_entries,
            "timeout": self.api_timeout,
            "retry_max": self.api_retry.max_retries,
            "response_field": self.api_json_response_field,
        }

    async def api_health_check(self, path: str = "/") -> bool:
        """Teste la disponibilité de l'API.

        Args:
            path: Chemin à interroger.

        Returns:
            ``True`` si l'API répond en 2xx/3xx/4xx (hors 5xx/network).
        """
        try:
            response = await self._api_request(
                "GET",
                path,
                timeout=self.api_timeout,
                use_cache=False,
                raise_on_error=False,
            )
        except ApiError as exc:
            self._api_logger().warning("Health check API KO : {err}", err=exc)
            return False
        return response.status_code < 500
