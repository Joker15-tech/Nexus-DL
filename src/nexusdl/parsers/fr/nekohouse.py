"""Parseur Nekohouse pour NexusDL.

Nekohouse (``https://nekohouse.su``) est un **frontend alternatif** pour
nhentai, offrant une interface moderne (React/Next.js) pour explorer les
doujinshis et mangas adultes du catalogue nhentai. Il s'appuie en interne
sur l'**API publique nhentai** (``https://nhentai.net/api``) et sert de
couche de présentation enrichie (pools, séries, recommandations).

Ce parser est classé dans le namespace ``fr/`` car Nekohouse propose une
**interface localisée en français** (traductions de l'UI, filtres, tags
francophones) et est particulièrement utilisé par la communauté
francophone pour accéder aux titres traduits ou aux métadonnées
multilingues (titre ``french`` disponible dans l'API nhentai).

Caractéristiques techniques
---------------------------

* **Moteur** : Next.js (frontend) + **API nhentai** (backend JSON).
* **Langue** : Multi-langue — français prioritaire pour l'UI, titres
  disponibles en ``french``, ``english``, ``japanese``, ``chinese``.
* **Protection** : Cloudflare (AS13335, IP ``104.21.16.85``) avec
  challenge modéré. Rate limiting nhentai (~1 req/s recommandé).
* **Domaines** :
    - ``nekohouse.su`` (frontend principal)
    - ``nhentai.net`` (API backend, fallback)
    - ``nhentai.xxx`` (miroir nhentai)
* **Structure des URLs** :
    - Catalogue : ``/`` (accueil avec recommandations).
    - Recherche : ``/search?q={query}``.
    - Galerie (doujinshi) : ``/g/{gallery_id}`` (ex. ``/g/123456``).
    - Pool/série : ``/p/{pool_id}``.
* **API nhentai** (utilisée en interne) :
    - ``GET /api/gallery/{id}`` — métadonnées d'une galerie.
    - ``GET /api/galleries/search?query={q}`` — recherche.
    - ``GET /api/galleries/tagged?tag_id={id}`` — par tag.
    - ``GET /api/galleries/popular`` — populaires.
* **Images** : servies depuis les CDN nhentai (``i.nhentai.net``,
  ``i1.nhentai.net`` à ``i4.nhentai.net``, ``t.nhentai.net``).
* **Format des images** : ``{base_url}/galleries/{media_id}/{page}.{ext}``
  où ``ext`` est déterminé par le champ ``t`` :
    - ``j`` → ``jpg``
    - ``p`` → ``png``
    - ``g`` → ``gif``
    - ``w`` → ``webp``
* **Contenu adulte** : site 18+/NSFW (hentai, doujinshi, mangas adultes).

⚠️ **Note importante** : Nekohouse n'est pas un site de scanlation mais un
**agrégateur/browser** qui exploite l'API nhentai. Le parser utilise
directement l'API nhentai pour les métadonnées et les images — Nekohouse
sert essentiellement de frontend alternatif et son API propre n'est pas
documentée publiquement.

Le parseur combine trois mixins :

1. :class:`~nexusdl.parsers.mixins.api_based.ApiBasedMixin` — client REST
   pour l'API nhentai.
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — pour le
   contournement Cloudflare automatique (Playwright / FlareSolverr).
3. :class:`~nexusdl.parsers.mixins.js_rendered.JsRenderedMixin` — pour le
   rendu Playwright (fallback si l'API est bloquée).

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.fr.nekohouse import NekohouseParser
    >>>
    >>> parser = NekohouseParser(config, session)
    >>> results = await parser.search("metamorphosis")
    >>> manga = await parser.get_manga(results[0].url)
    >>> chapters = await parser.get_chapters(manga)
    >>> pages = await parser.get_pages(chapters[0])
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, ClassVar, Final
from urllib.parse import quote_plus, urljoin, urlparse

from nexusdl.core.exceptions import (
    ChapterNotFoundError,
    MangaNotFoundError,
    ParseError,
)
from nexusdl.core.logger import get_logger
from nexusdl.core.models.manga import (
    Chapter,
    ContentRating,
    Language,
    Manga,
    MangaStatus,
    Page,
    SearchResult,
)
from nexusdl.parsers.base import BaseParser
from nexusdl.parsers.mixins.api_based import ApiBasedMixin
from nexusdl.parsers.mixins.cloudflare import CloudflareMixin
from nexusdl.parsers.mixins.js_rendered import JsRenderedMixin

__all__ = ["NekohouseParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques au site
# ---------------------------------------------------------------------------

_BASE_URL: Final[str] = "https://nekohouse.su"
_FALLBACK_DOMAINS: Final[tuple[str, ...]] = (
    "https://nekohouse.su",
    "https://nhentai.net",
    "https://nhentai.xxx",
)
_API_URL: Final[str] = "https://nhentai.net/api"
_SITE_ID: Final[str] = "nekohouse"
_LANGUAGE: Final[Language] = Language.FR
_ADULT: Final[bool] = True  # Site 18+/NSFW (hentai, doujinshi)

# User-Agent identifiable (nhentai bloque les UA par défaut).
_USER_AGENT: Final[str] = (
    "NexusDL/1.0 (https://github.com/nexusdl/nexusdl)"
)

# CDN d'images nhentai (round-robin entre i1 à i4).
_IMAGE_CDN_HOSTS: Final[tuple[str, ...]] = (
    "https://i1.nhentai.net",
    "https://i2.nhentai.net",
    "https://i3.nhentai.net",
    "https://i4.nhentai.net",
)
_THUMBNAIL_CDN: Final[str] = "https://t.nhentai.net"

# Mapping du champ "t" nhentai → extension de fichier.
_IMAGE_EXT_MAP: Final[dict[str, str]] = {
    "j": "jpg",
    "p": "png",
    "g": "gif",
    "w": "webp",
}

# Regex de parsing des IDs de galerie.
_GALLERY_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"/(?:g|gallery)/(\d+)", re.IGNORECASE
)
_POOL_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"/p/(\d+)", re.IGNORECASE
)

# Regex de parsing des titres multilingues.
_CHAPTER_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:chapter|chapitre|ch\.?|episode|ep\.?|ep|part|caps?)\s*"
    r"(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)

# Statuts → énumération NexusDL (nhentai n'expose pas de statut, mais
# on peut avoir des indications sur les séries en cours/terminées via tags).
_STATUS_MAP: Final[dict[str, MangaStatus]] = {
    "ongoing": MangaStatus.ONGOING,
    "completed": MangaStatus.COMPLETED,
    "hiatus": MangaStatus.HIATUS,
    "cancelled": MangaStatus.CANCELLED,
}

# Classification de contenu (nhentai est toujours adulte).
_RATING_MAP: Final[dict[str, ContentRating]] = {
    "hentai": ContentRating.PORNOGRAPHIC,
    "adult": ContentRating.PORNOGRAPHIC,
    "ecchi": ContentRating.EROTICA,
    "smut": ContentRating.EROTICA,
    "mature": ContentRating.EROTICA,
}

# Tags adultes par défaut.
_ADULT_GENRE_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "hentai",
        "adult",
        "ecchi",
        "smut",
        "mature",
        "18+",
        "porn",
        "erotic",
        "erotica",
        "érotique",
        "erotique",
        "yaoi",
        "yuri",
        "gender bender",
        "doujinshi",
        "manga",
        "anthology",
    }
)

# Pagination.
_API_MAX_LIMIT: Final[int] = 25
_API_DEFAULT_LIMIT: Final[int] = 25


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


class NekohouseParser(
    JsRenderedMixin,
    CloudflareMixin,
    ApiBasedMixin,
    BaseParser,
):
    """Parseur Nekohouse (frontend nhentai + API nhentai).

    Combine le rendu Playwright (fallback), le contournement Cloudflare et
    un client REST pour l'API nhentai publique.

    Attributes:
        site_id: Identifiant interne du site.
        language: Langue principale (français).
        adult: Contenu adulte (``True`` — site 18+/NSFW).
        base_url: URL racine du site.
    """

    # --- Métadonnées du parser ---
    site_id: ClassVar[str] = _SITE_ID
    language: ClassVar[Language] = _LANGUAGE
    adult: ClassVar[bool] = _ADULT

    # --- URLs de base ---
    base_url: ClassVar[str] = _BASE_URL
    fallback_domains: ClassVar[tuple[str, ...]] = _FALLBACK_DOMAINS
    api_url: ClassVar[str] = _API_URL
    fools_base_url: ClassVar[str | None] = None

    # --- Configuration API (nhentai) ---
    api_base_url: ClassVar[str] = _API_URL
    api_default_headers: ClassVar[dict[str, str]] = {
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
        "Referer": "https://nhentai.net/",
    }
    api_rate_limit_per_second: ClassVar[float] = 1.0  # nhentai : 1 req/s recommandé
    api_rate_limit_burst: ClassVar[int] = 2
    api_timeout: ClassVar[float] = 20.0
    api_cache_enabled: ClassVar[bool] = True
    api_cache_ttl: ClassVar[int] = 300
    api_raise_on_error_status: ClassVar[bool] = True

    # --- Configuration Cloudflare ---
    cf_enabled: ClassVar[bool] = True
    cf_requires_js: ClassVar[bool] = False
    cf_preferred_backend: ClassVar[str] = "playwright"
    cf_use_playwright_fallback: ClassVar[bool] = True
    cf_use_flaresolverr_fallback: ClassVar[bool] = True
    cf_max_attempts: ClassVar[int] = 3
    cf_challenge_timeout: ClassVar[float] = 30.0
    cf_clearance_ttl: ClassVar[int] = 1800

    # --- Configuration JsRendered ---
    js_rendered: ClassVar[bool] = True
    default_wait_until: ClassVar[str] = "networkidle"
    default_render_timeout: ClassVar[float] = 30.0
    default_navigation_timeout: ClassVar[float] = 45.0
    block_resources_by_default: ClassVar[bool] = False
    locale: ClassVar[str] = "fr-FR"
    timezone_id: ClassVar[str] = "Europe/Paris"
    viewport_width: ClassVar[int] = 1366
    viewport_height: ClassVar[int] = 900

    # --- Comportement spécifique ---
    # Qualité d'image : False = original, True = thumbnail (inutilisé ici).
    use_thumbnail: ClassVar[bool] = False

    # ------------------------------------------------------------------
    # Constructeur
    # ------------------------------------------------------------------

    def __init__(
        self,
        config: Any,
        session: Any,
        *,
        playwright_pool: Any | None = None,
        cookie_manager: Any | None = None,
        flaresolverr: Any | None = None,
    ) -> None:
        """Initialise le parseur Nekohouse.

        Args:
            config: Configuration du site (:class:`SiteConfig`).
            session: Session HTTP async du projet (:class:`HttpSession`).
            playwright_pool: Pool Playwright partagé (requis — Cloudflare).
            cookie_manager: Gestionnaire de cookies chiffrés (optionnel).
            flaresolverr: Client FlareSolverr (optionnel).
        """
        super().__init__(
            config=config,
            session=session,
            playwright_pool=playwright_pool,
        )
        self.cookie_manager = cookie_manager
        self.flaresolverr = flaresolverr
        self.logger = get_logger(f"{self.__class__.__module__}.{self.site_id}")

        # Injecte le header Referer requis pour le CDN d'images.
        self._inject_cdn_referer()

    def _inject_cdn_referer(self) -> None:
        """Injecte le header ``Referer`` requis pour le CDN d'images."""
        target = getattr(self.session, "headers", None)
        if isinstance(target, dict):
            target.setdefault("Referer", "https://nhentai.net/")
            self.logger.debug("Header Referer CDN injecté dans la session")
            return
        config_headers = getattr(self.config, "default_headers", None)
        if isinstance(config_headers, dict):
            config_headers.setdefault("Referer", "https://nhentai.net/")

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------

    def _neko_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="nekohouse"``.
        """
        return self.logger

    @staticmethod
    def _neko_clean(value: str | None) -> str:
        """Nettoie une chaîne (strip, espaces multiples, NBSP).

        Args:
            value: Chaîne à nettoyer.

        Returns:
            Chaîne nettoyée (chaîne vide si ``None``).
        """
        if not value:
            return ""
        normalized = (
            value.replace("\xa0", " ")
            .replace("\u200b", "")
            .replace("\u200c", "")
            .replace("\ufeff", "")
        )
        return " ".join(normalized.split()).strip()

    def _neko_image_url(
        self,
        media_id: str,
        page_num: int,
        ext: str,
    ) -> str:
        """Construit l'URL d'une image de page nhentai.

        Format : ``{cdn}/galleries/{media_id}/{page_num}.{ext}``.

        Args:
            media_id: Identifiant média de la galerie.
            page_num: Numéro de page (1-based).
            ext: Extension de fichier (``jpg``, ``png``, ``gif``, ``webp``).

        Returns:
            URL absolue de l'image.
        """
        # Distribution round-robin sur les 4 CDN nhentai pour éviter le
        # rate limiting par host.
        cdn = _IMAGE_CDN_HOSTS[(page_num - 1) % len(_IMAGE_CDN_HOSTS)]
        return f"{cdn}/galleries/{media_id}/{page_num}.{ext}"

    @staticmethod
    def _neko_ext_from_type(t: str) -> str:
        """Convertit le code de type nhentai en extension.

        Args:
            t: Code ``t`` de l'API nhentai (``j``, ``p``, ``g``, ``w``).

        Returns:
            Extension de fichier (``jpg`` par défaut).
        """
        return _IMAGE_EXT_MAP.get(t.lower(), "jpg")

    def _neko_cover_url(self, media_id: str, t: str) -> str:
        """Construit l'URL de la couverture d'une galerie nhentai.

        Args:
            media_id: Identifiant média de la galerie.
            t: Code type de la couverture.

        Returns:
            URL absolue de la couverture.
        """
        ext = self._neko_ext_from_type(t)
        return f"{_THUMBNAIL_CDN}/galleries/{media_id}/cover.{ext}"

    def _neko_extract_id_from_url(self, url_or_id: str) -> str:
        """Extrait l'ID de galerie depuis une URL ou un ID brut.

        Formats acceptés :
            - ID brut : ``123456``
            - Nekohouse : ``https://nekohouse.su/g/123456``
            - nhentai : ``https://nhentai.net/g/123456/``

        Args:
            url_or_id: URL ou ID.

        Returns:
            ID de galerie ou chaîne brute si introuvable.
        """
        if not url_or_id:
            return url_or_id
        if url_or_id.isdigit():
            return url_or_id
        if url_or_id.startswith(("http://", "https://")):
            match = _GALLERY_ID_RE.search(url_or_id)
            if match:
                return match.group(1)
            parsed = urlparse(url_or_id)
            parts = [p for p in parsed.path.split("/") if p]
            if parts and parts[-1].isdigit():
                return parts[-1]
        return url_or_id.strip()

    def _neko_extract_title(self, title_field: Any) -> str:
        """Extrait le titre depuis le champ ``title`` de l'API nhentai.

        Priorité : ``french`` > ``pretty`` > ``english`` > ``japanese``.

        Args:
            title_field: Champ ``title`` (dict) de l'API nhentai.

        Returns:
            Titre extrait.
        """
        if isinstance(title_field, str):
            return title_field
        if not isinstance(title_field, dict):
            return "Untitled"
        for key in ("french", "pretty", "english", "japanese"):
            value = title_field.get(key)
            if value:
                return str(value)
        # Fallback : première valeur non vide.
        for value in title_field.values():
            if value:
                return str(value)
        return "Untitled"

    def _neko_parse_iso_timestamp(self, raw: int | float | None) -> datetime | None:
        """Convertit un timestamp UNIX nhentai en datetime UTC.

        Args:
            raw: Timestamp UNIX (secondes).

        Returns:
            Datetime UTC ou ``None``.
        """
        if raw is None:
            return None
        try:
            return datetime.fromtimestamp(int(raw), tz=timezone.utc)
        except (ValueError, OSError, TypeError):
            return None

    def _neko_build_manga(self, data: dict[str, Any]) -> Manga:
        """Construit un objet :class:`Manga` depuis une entité nhentai.

        Args:
            data: Entité gallery retournée par l'API nhentai.

        Returns:
            Objet :class:`Manga` hydraté.

        Raises:
            ParseError: Si les données sont inexploitables.
        """
        gallery_id = data.get("id")
        if not gallery_id:
            raise ParseError("Entité gallery sans 'id'")

        media_id = str(data.get("media_id") or "")
        title = self._neko_extract_title(data.get("title"))

        # Titres alternatifs depuis les autres langues.
        alt_titles: list[str] = []
        title_field = data.get("title")
        if isinstance(title_field, dict):
            for key, value in title_field.items():
                if value and str(value) not in alt_titles and str(value) != title:
                    alt_titles.append(str(value))

        # Genres depuis les tags (type=tag).
        genres: list[str] = []
        tags = data.get("tags") or []
        for tag in tags:
            if not isinstance(tag, dict):
                continue
            if tag.get("type") == "tag":
                name = tag.get("name")
                if name and name not in genres:
                    genres.append(str(name))

        # Auteur/artiste depuis les tags (type=artist).
        author: str | None = None
        for tag in tags:
            if not isinstance(tag, dict):
                continue
            if tag.get("type") == "artist":
                name = tag.get("name")
                if name:
                    author = str(name)
                    break

        # Classification : nhentai est toujours adulte.
        content_rating = ContentRating.PORNOGRAPHIC
        for marker in _ADULT_GENRE_MARKERS:
            if any(marker in g.lower() for g in genres):
                content_rating = _RATING_MAP.get(marker, ContentRating.PORNOGRAPHIC)
                break

        # Couverture.
        cover_url: str | None = None
        images_field = data.get("images") or {}
        cover = images_field.get("cover") if isinstance(images_field, dict) else None
        if isinstance(cover, dict):
            cover_t = cover.get("t") or "j"
            if media_id:
                cover_url = self._neko_cover_url(media_id, cover_t)

        # Nombre de pages.
        num_pages = data.get("num_pages")
        if not isinstance(num_pages, int):
            num_pages = None

        # Date de mise en ligne.
        updated_at = (
            self._neko_parse_iso_timestamp(data.get("upload_date"))
            or datetime.now(timezone.utc)
        )

        # URL publique.
        url = f"{self.base_url}/g/{gallery_id}"

        return Manga(
            id=f"{self.config.id}:{gallery_id}",
            source_id=str(gallery_id),
            site=self.config.id,
            title=title,
            alternative_titles=alt_titles,
            description=None,  # nhentai n'expose pas de description.
            author=author,
            artist=author,
            genres=genres,
            status=MangaStatus.COMPLETED,  # nhentai : doujinshi auto-contenus.
            year=None,
            cover_url=cover_url,
            language=self.language,
            content_rating=content_rating,
            chapters=[],
            url=url,
            updated_at=updated_at,
        )

    # ------------------------------------------------------------------
    # Méthodes abstraites de BaseParser
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des doujinshis/galeries via l'API nhentai.

        Args:
            query: Terme de recherche.
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.

        Raises:
            ParseError: Si la réponse API est inexploitable.
        """
        self.logger.debug(
            "Nekohouse search: {query} (page {page})",
            query=query,
            page=page,
        )

        params: dict[str, Any] = {
            "query": query,
            "page": page,
        }

        try:
            payload = await self.api_get(
                "/galleries/search",
                params=params,
                timeout=20.0,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec recherche Nekohouse pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        return self._neko_parse_search_payload(payload)

    def _neko_parse_search_payload(
        self, payload: Any
    ) -> list[SearchResult]:
        """Parse la charge utile JSON de recherche nhentai.

        Args:
            payload: Réponse JSON de ``/galleries/search``.

        Returns:
            Liste de :class:`SearchResult`.
        """
        if not isinstance(payload, dict):
            return []

        # nhentai retourne {result: [...], num_pages: N, per_page: M}.
        items = payload.get("result")
        if not isinstance(items, list):
            # Fallback : parfois nhentai retourne directement une liste.
            items = payload.get("data") if isinstance(payload.get("data"), list) else []

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            gallery_id = item.get("id")
            if not gallery_id:
                continue

            title = self._neko_extract_title(item.get("title"))

            media_id = str(item.get("media_id") or "")
            cover_url: str | None = None
            images_field = item.get("images") or {}
            cover = (
                images_field.get("cover")
                if isinstance(images_field, dict)
                else None
            )
            if isinstance(cover, dict) and media_id:
                cover_t = cover.get("t") or "j"
                cover_url = self._neko_cover_url(media_id, cover_t)

            # Auteur.
            author: str | None = None
            for tag in item.get("tags") or []:
                if isinstance(tag, dict) and tag.get("type") == "artist":
                    name = tag.get("name")
                    if name:
                        author = str(name)
                        break

            results.append(
                SearchResult(
                    title=title,
                    url=f"{self.base_url}/g/{gallery_id}",
                    site_id=self.config.id,
                    cover_url=cover_url,
                    author=author,
                )
            )

        self.logger.info(
            "Nekohouse search: {n} résultat(s)", n=len(results)
        )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'une galerie via ``/api/gallery/{id}``.

        Args:
            url_or_id: URL absolue ou ID de galerie.

        Returns:
            Objet :class:`Manga` hydraté.

        Raises:
            MangaNotFoundError: Si la galerie est introuvable.
            ParseError: Si la réponse API est inexploitable.
        """
        gallery_id = self._neko_extract_id_from_url(url_or_id)
        self.logger.debug("Nekohouse get_manga: {id}", id=gallery_id)

        try:
            payload = await self.api_get(
                f"/gallery/{gallery_id}",
                timeout=20.0,
            )
        except Exception as exc:  # noqa: BLE001
            if "404" in str(exc):
                raise MangaNotFoundError(
                    f"Galerie introuvable : {gallery_id}"
                ) from exc
            self.logger.error(
                "Échec get_manga Nekohouse pour {id!r}: {err}",
                id=gallery_id,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {gallery_id!r}: {exc}"
            ) from exc

        if not isinstance(payload, dict) or not payload.get("id"):
            raise ParseError(
                f"Réponse API invalide pour {gallery_id!r}"
            )

        return self._neko_build_manga(payload)

    async def get_chapters(self, manga: Manga) -> list[Chapter]:
        """Récupère le "chapitre" unique d'une galerie nhentai.

        Les galeries nhentai sont des **œuvres auto-contenues** (un
        doujinshi = une œuvre). Le parser synthétise donc un chapitre
        unique contenant toutes les pages de la galerie.

        Args:
            manga: Manga dont on veut les chapitres.

        Returns:
            Liste de :class:`Chapter` (toujours 1 élément).

        Raises:
            ChapterNotFoundError: Si aucun chapitre n'est trouvé.
        """
        self.logger.debug(
            "Nekohouse get_chapters: {title}", title=manga.title
        )

        gallery_id = manga.source_id
        if not gallery_id:
            raise ChapterNotFoundError(
                f"ID de galerie introuvable dans {manga.url!r}"
            )

        # Recharge les métadonnées pour obtenir le nombre de pages.
        try:
            payload = await self.api_get(
                f"/gallery/{gallery_id}",
                timeout=20.0,
            )
        except Exception as exc:  # noqa: BLE001
            if "404" in str(exc):
                raise ChapterNotFoundError(
                    f"Galerie introuvable : {gallery_id}"
                ) from exc
            raise ParseError(
                f"get_chapters échoué sur {self.site_id!r} "
                f"pour {gallery_id!r}: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise ParseError(f"Réponse invalide pour {gallery_id!r}")

        num_pages = payload.get("num_pages")
        if not isinstance(num_pages, int) or num_pages <= 0:
            # Fallback : compte les pages depuis le champ images.
            images = payload.get("images") or {}
            pages_list = images.get("pages") if isinstance(images, dict) else None
            if isinstance(pages_list, list):
                num_pages = len(pages_list)
            else:
                num_pages = 0

        # Un seul chapitre synthétique contenant toutes les pages.
        chapter = Chapter(
            id=f"{self.config.id}:{gallery_id}:ch1",
            source_id=gallery_id,
            title=manga.title,
            number=1.0,
            volume=None,
            language=self.language,
            pages_count=num_pages or None,
            published_at=manga.updated_at,
            url=manga.url,
            pages=[],
        )

        self.logger.info(
            "Nekohouse get_chapters OK: 1 chapitre ({n} pages)",
            n=num_pages,
        )
        return [chapter]

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'une galerie nhentai.

        L'API nhentai retourne les métadonnées des pages (dimensions et
        type) ; les URLs finales sont construites selon le format
        ``{cdn}/galleries/{media_id}/{page}.{ext}``.

        Args:
            chapter: Chapitre (galerie) cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune page n'est trouvée.
            ParseError: Si la réponse API est inexploitable.
        """
        gallery_id = chapter.source_id
        self.logger.debug(
            "Nekohouse get_pages: {id}", id=gallery_id
        )

        try:
            payload = await self.api_get(
                f"/gallery/{gallery_id}",
                timeout=20.0,
            )
        except Exception as exc:  # noqa: BLE001
            if "404" in str(exc):
                raise ChapterNotFoundError(
                    f"Galerie introuvable : {gallery_id}"
                ) from exc
            raise ParseError(
                f"get_pages échoué sur {self.site_id!r} "
                f"pour {gallery_id!r}: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise ParseError(f"Réponse invalide pour {gallery_id!r}")

        media_id = str(payload.get("media_id") or "")
        if not media_id:
            raise ParseError(
                f"media_id manquant pour la galerie {gallery_id!r}"
            )

        images_field = payload.get("images") or {}
        pages_meta = (
            images_field.get("pages")
            if isinstance(images_field, dict)
            else None
        )
        if not isinstance(pages_meta, list) or not pages_meta:
            raise ChapterNotFoundError(
                f"Aucune page retournée pour {gallery_id!r}"
            )

        pages: list[Page] = []
        for idx, meta in enumerate(pages_meta):
            if not isinstance(meta, dict):
                continue
            t = str(meta.get("t") or "j")
            ext = self._neko_ext_from_type(t)
            page_num = idx + 1
            url = self._neko_image_url(media_id, page_num, ext)
            filename = f"{page_num:04d}.{ext}"
            pages.append(
                Page(
                    index=page_num,
                    url=url,
                    filename=filename,
                    checksum=None,
                )
            )

        if not pages:
            raise ChapterNotFoundError(
                f"Aucune page valide pour {gallery_id!r}"
            )

        self.logger.info(
            "Nekohouse get_pages OK: {n} page(s) pour {id}",
            n=len(pages),
            id=gallery_id,
        )
        return pages

    # ------------------------------------------------------------------
    # Endpoints complémentaires nhentai
    # ------------------------------------------------------------------

    async def get_popular(self, *, page: int = 1) -> list[SearchResult]:
        """Récupère les galeries populaires via ``/galleries/popular``.

        Args:
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.
        """
        try:
            payload = await self.api_get(
                "/galleries/popular",
                params={"page": page},
                timeout=20.0,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Échec get_popular: {err}", err=exc)
            return []

        return self._neko_parse_search_payload(payload)

    async def get_by_tag(
        self, tag_id: int, *, page: int = 1
    ) -> list[SearchResult]:
        """Récupère les galeries associées à un tag.

        Args:
            tag_id: ID numérique du tag nhentai.
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.
        """
        try:
            payload = await self.api_get(
                "/galleries/tagged",
                params={"tag_id": tag_id, "page": page},
                timeout=20.0,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Échec get_by_tag: {err}", err=exc)
            return []

        return self._neko_parse_search_payload(payload)

    async def get_related(self, gallery_id: str) -> list[SearchResult]:
        """Récupère les galeries liées (recommandations nhentai).

        Args:
            gallery_id: ID de la galerie source.

        Returns:
            Liste de :class:`SearchResult`.
        """
        try:
            payload = await self.api_get(
                f"/gallery/{gallery_id}/related",
                timeout=20.0,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("Échec get_related: {err}", err=exc)
            return []

        if not isinstance(payload, dict):
            return []
        items = payload.get("result") or payload.get("data") or []
        if not isinstance(items, list):
            return []
        # Réutilise le parseur de payload avec un faux conteneur.
        return self._neko_parse_search_payload({"result": items})

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que l'API nhentai (backend Nekohouse) est accessible.

        Returns:
            ``True`` si l'API répond correctement.
        """
        try:
            # Ping léger : recherche avec un terme neutre.
            payload = await self.api_get(
                "/galleries/search",
                params={"query": "test", "page": 1},
                timeout=10.0,
            )
            ok = isinstance(payload, dict)
            self.logger.info(
                "Health check Nekohouse (nhentai API): {ok}", ok=ok
            )
            return ok
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                "Health check Nekohouse KO: {err}", err=exc
            )
            return False
