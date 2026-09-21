"""Parseur Comick pour NexusDL.

Comick (``https://comick.io``) est l'un des agrégateurs de mangas, manhwas,
manhuas et webtoons les plus populaires de la communauté, avec plus de
**500 000 chapitres** dans **40+ langues**. Le site est reconnu pour son
API publique, sa rapidité et la qualité de son catalogue.

⚠️ **Avertissement** : Comick a été **officiellement fermé en 2025** à la
suite de pressions légales. Le site et son API ne sont plus accessibles
publiquement. Ce parseur reste néanmoins fourni à titre d'**archive
technique** — il documente l'architecture d'une API REST moderne et peut
être adapté si le service devait être relancé sous une autre forme.

Caractéristiques techniques
---------------------------

* **API** : ``https://api.comick.io`` (publique, gratuite, sans auth).
* **Protection** : Cloudflare avec **Managed Challenge** — contournement
  nécessaire (Playwright / FlareSolverr).
* **Langue** : multi-langue (``en``, ``fr``, ``ja``, ``ko``, ``zh``, ``es``,
  ``pt``, ``de``, ``it``, ``ar``, ``ru``, ``tr``…).
* **Rate limiting** : modéré — ~3 req/s recommandé pour ne pas être bloqué.
* **User-Agent** : obligatoire, ne doit **jamais** être falsifié.
* **Endpoints principaux** :
    - ``GET /v1.0/search`` — recherche catalogue.
    - ``GET /comic/{slug}`` — détails d'un manga.
    - ``GET /comic/{hid}/chapters`` — liste des chapitres.
    - ``GET /chapter/{hid}`` — détails d'un chapitre.
    - ``GET /chapter/{hid}/images`` — images d'un chapitre.
* **Identifiants** :
    - ``slug`` : identifiant lisible (ex. ``solo-leveling``).
    - ``hid`` : identifiant hexadécimal (ex. ``a1b2c3d4``).
    - ``comic_id`` : identifiant numérique.
* **Structure JSON** : l'API retourne des objets ``{data: ..., total: ...}``
  pour les listes, et ``{data: {...}}`` pour les entités uniques.
* **Images** : servies depuis des CDN dynamiques (``meo.comick.pictures``),
  avec header ``Referer`` requis pour éviter le hotlink-blocking.
* **Lazy-loading** : le site charge les images par lots de 20 via
  ``IntersectionObserver`` — l'API ``/images`` retourne la liste complète.

Le parseur combine deux mixins :

1. :class:`~nexusdl.parsers.mixins.api_based.ApiBasedMixin` — client REST
   complet pour les endpoints publics de l'API Comick.
2. :class:`~nexusdl.parsers.mixins.cloudflare.CloudflareMixin` — bypass
   Cloudflare automatique (Playwright / FlareSolverr).

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.en.comick import ComickParser
    >>>
    >>> parser = ComickParser(config, session)
    >>> results = await parser.search("solo leveling")
    >>> manga = await parser.get_manga(results[0].url)
    >>> chapters = await parser.get_chapters(manga)
    >>> pages = await parser.get_pages(chapters[0])
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, ClassVar, Final
from urllib.parse import quote_plus, urlparse

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

__all__ = ["ComickParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques à l'API Comick
# ---------------------------------------------------------------------------

_API_URL: Final[str] = "https://api.comick.io"
_BASE_URL: Final[str] = "https://comick.io"
_SITE_ID: Final[str] = "comick"
_LANGUAGE: Final[Language] = Language.EN
_ADULT: Final[bool] = False

# User-Agent identifiable (obligatoire selon la politique Comick).
_USER_AGENT: Final[str] = (
    "NexusDL/1.0 (https://github.com/nexusdl/nexusdl)"
)

# Mapping des langues NexusDL → codes Comick (ISO 639-1).
_LANG_MAP: Final[dict[Language, str]] = {
    Language.EN: "en",
    Language.FR: "fr",
    Language.JA: "ja",
    Language.KO: "ko",
    Language.ZH: "zh",
    Language.ES: "es",
    Language.PT: "pt",
    Language.DE: "de",
    Language.IT: "it",
    Language.AR: "ar",
    Language.RU: "ru",
    Language.TR: "tr",
    Language.PL: "pl",
    Language.NL: "nl",
    Language.ID: "id",
    Language.VI: "vi",
    Language.TH: "th",
    Language.HU: "hu",
    Language.CS: "cs",
    Language.UK: "uk",
}

# Mapping inverse : code ISO Comick → Language NexusDL.
_ISO_TO_LANG: Final[dict[str, Language]] = {
    code: lang for lang, code in _LANG_MAP.items()
}

# Statuts Comick → énumération NexusDL.
_STATUS_MAP: Final[dict[int, MangaStatus]] = {
    1: MangaStatus.ONGOING,
    2: MangaStatus.COMPLETED,
    3: MangaStatus.CANCELLED,
    4: MangaStatus.HIATUS,
    5: MangaStatus.CANCELLED,
    6: MangaStatus.ONGOING,  # "Not yet published"
}

# Content ratings Comick → énumération NexusDL.
_RATING_MAP: Final[dict[str, ContentRating]] = {
    "safe": ContentRating.SAFE,
    "suggestive": ContentRating.SUGGESTIVE,
    "erotica": ContentRating.EROTICA,
    "pornographic": ContentRating.PORNOGRAPHIC,
}

# Regex de parsing des numéros de chapitre (Comick stocke en str).
_CHAPTER_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"^(\d+(?:\.\d+)?)", re.IGNORECASE
)

# Pagination : limite maximale par requête.
_API_MAX_LIMIT: Final[int] = 100
_API_DEFAULT_LIMIT: Final[int] = 20

# Comick utilise Cloudflare → le Referer est requis pour les images.
_IMAGES_REFERER: Final[str] = _BASE_URL


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


class ComickParser(
    CloudflareMixin,
    ApiBasedMixin,
    BaseParser,
):
    """Parseur Comick via l'API REST publique.

    ⚠️ **Le service Comick a été fermé en 2025.** Ce parseur reste fourni à
    titre d'archive technique et documente l'usage d'une API REST moderne.

    Combine un client API REST (``ApiBasedMixin``) avec le contournement
    Cloudflare (``CloudflareMixin``) pour couvrir l'ensemble des endpoints
    publics.

    Attributes:
        site_id: Identifiant interne du site.
        language: Langue principale (anglais par défaut, configurable).
        adult: Contenu adulte (``False`` — Comick affiche un avertissement
            mais reste accessible sans flag).
        base_url: URL publique du site.
    """

    # --- Métadonnées du parser ---
    site_id: ClassVar[str] = _SITE_ID
    language: ClassVar[Language] = _LANGUAGE
    adult: ClassVar[bool] = _ADULT

    # --- URLs de base ---
    base_url: ClassVar[str] = _BASE_URL
    api_url: ClassVar[str] = _API_URL

    # --- Configuration API (ApiBasedMixin) ---
    api_base_url: ClassVar[str] = _API_URL
    api_default_headers: ClassVar[dict[str, str]] = {
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
        "Referer": _IMAGES_REFERER,
    }
    api_rate_limit_per_second: ClassVar[float] = 2.0
    api_rate_limit_burst: ClassVar[int] = 3
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

    # --- Comportement spécifique ---
    # Qualité des images : "data" (original) ou "data-saver" (compressé).
    use_data_saver: ClassVar[bool] = False
    # Nombre maximum de chapitres retournés par défaut.
    default_chapter_limit: ClassVar[int] = 500

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
        language: Language | None = None,
        use_data_saver: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialise le parseur Comick.

        Args:
            config: Configuration du site (:class:`SiteConfig`).
            session: Session HTTP async du projet (:class:`HttpSession`).
            playwright_pool: Pool Playwright partagé (requis — Cloudflare).
            cookie_manager: Gestionnaire de cookies chiffrés (optionnel).
            flaresolverr: Client FlareSolverr (optionnel).
            language: Langue cible des chapitres (par défaut : anglais).
            use_data_saver: Si ``True``, utilise les images compressées
                (``data-saver``) au lieu des images originales (``data``).
            **kwargs: Arguments additionnels transmis à :class:`BaseParser`.
        """
        super().__init__(
            config=config,
            session=session,
            playwright_pool=playwright_pool,
        )
        self.cookie_manager = cookie_manager
        self.flaresolverr = flaresolverr
        if language is not None:
            self.language = language
        if use_data_saver is not None:
            self.use_data_saver = use_data_saver
        self.logger = get_logger(f"{self.__class__.__module__}.{self.site_id}")

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------

    def _comick_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="comick"``.
        """
        return self.logger

    def _comick_lang_code(self) -> str:
        """Retourne le code ISO de la langue courante.

        Returns:
            Code ISO 639-1 (``en``, ``fr``, etc.).
        """
        return _LANG_MAP.get(self.language, "en")

    @staticmethod
    def _comick_parse_chapter_number(raw: str | None) -> float | str:
        """Parse un numéro de chapitre Comick.

        Comick stocke les numéros de chapitre sous forme de chaînes
        (ex. ``"1090"``, ``"12.5"``, ``""``).

        Args:
            raw: Numéro brut (str) ou ``None``.

        Returns:
            ``float`` si parsable, sinon la chaîne nettoyée ou ``0.0``.
        """
        if not raw:
            return 0.0
        cleaned = str(raw).strip()
        if not cleaned:
            return 0.0
        match = _CHAPTER_NUMBER_RE.match(cleaned)
        if not match:
            return cleaned
        try:
            return float(match.group(1))
        except ValueError:
            return cleaned

    @staticmethod
    def _comick_parse_iso_date(raw: str | None) -> datetime | None:
        """Parse une date ISO 8601 Comick.

        Args:
            raw: Chaîne de date RFC 3339 (ex. ``"2024-01-15T12:30:00Z"``).

        Returns:
            Datetime UTC ou ``None``.
        """
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None

    @staticmethod
    def _comick_extract_id_from_url(url_or_id: str) -> str:
        """Extrait le slug Comick depuis une URL ou un ID brut.

        Formats acceptés :
            - Slug brut : ``solo-leveling``
            - URL : ``https://comick.io/comic/solo-leveling``
            - URL : ``https://comick.io/comic/solo-leveling/chapter-1``

        Args:
            url_or_id: URL ou slug.

        Returns:
            Slug extrait (ou la chaîne telle quelle si aucun slug trouvé).
        """
        if not url_or_id:
            return url_or_id
        if url_or_id.startswith(("http://", "https://")):
            parsed = urlparse(url_or_id)
            parts = [p for p in parsed.path.split("/") if p]
            # Cherche le segment après "comic".
            if "comic" in parts:
                idx = parts.index("comic")
                remaining = parts[idx + 1:]
                if remaining:
                    return remaining[0]
            # Fallback : dernier segment.
            return parts[-1] if parts else url_or_id
        return url_or_id.strip()

    def _comick_cover_url(self, comic: dict[str, Any]) -> str | None:
        """Construit l'URL absolue d'une couverture Comick.

        Args:
            comic: Objet comic retourné par l'API.

        Returns:
            URL absolue de la couverture ou ``None``.
        """
        cover = comic.get("cover") or comic.get("md_cover")
        if not cover:
            return None
        if cover.startswith(("http://", "https://")):
            return str(cover)
        # Couverture relative (ex. "uploads/manga/solo-leveling/cover.jpg").
        return f"{self.base_url}/{cover.lstrip('/')}"

    # ------------------------------------------------------------------
    # Construction des objets Manga / Chapter / Page
    # ------------------------------------------------------------------

    def _comick_build_manga(self, data: dict[str, Any]) -> Manga:
        """Construit un objet :class:`Manga` depuis une entité API Comick.

        Args:
            data: Entité ``comic`` retournée par l'API.

        Returns:
            Objet :class:`Manga` hydraté.

        Raises:
            ParseError: Si les données sont inexploitables.
        """
        comic_id = data.get("id")
        slug = data.get("slug")
        if not comic_id or not slug:
            raise ParseError("Entité comic sans 'id' ou 'slug' dans l'API")

        title = data.get("title") or "Untitled"

        # Titres alternatifs.
        alt_titles: list[str] = []
        for alt in data.get("md_titles") or []:
            if isinstance(alt, dict):
                alt_title = alt.get("title")
                if alt_title and alt_title not in alt_titles:
                    alt_titles.append(str(alt_title))

        description = data.get("desc") or data.get("description")

        # Auteur / artiste.
        author = data.get("author")
        artist = data.get("artist")

        # Genres.
        genres: list[str] = []
        for genre in data.get("genres") or []:
            if isinstance(genre, dict):
                gname = genre.get("name")
                if gname and gname not in genres:
                    genres.append(str(gname))

        # Statut.
        status_raw = data.get("status")
        status = _STATUS_MAP.get(
            int(status_raw) if isinstance(status_raw, (int, str)) and str(status_raw).isdigit() else 1,
            MangaStatus.ONGOING,
        )

        # Année.
        year = data.get("year")
        if not isinstance(year, int):
            year = None

        # Content rating.
        rating_raw = data.get("content_rating") or data.get("rating")
        content_rating = _RATING_MAP.get(
            str(rating_raw).lower() if rating_raw else "", ContentRating.SAFE
        )

        # Langue originale.
        orig_lang_raw = data.get("country") or data.get("lang")
        manga_language = (
            _ISO_TO_LANG.get(str(orig_lang_raw), self.language)
            if orig_lang_raw
            else self.language
        )

        # Dates.
        updated_at = (
            self._comick_parse_iso_date(data.get("updated_at"))
            or datetime.now(timezone.utc)
        )

        # URL publique.
        url = f"{self.base_url}/comic/{slug}"

        return Manga(
            id=f"{self.config.id}:{slug}",
            source_id=str(slug),
            site=self.config.id,
            title=str(title),
            alternative_titles=alt_titles,
            description=str(description) if description else None,
            author=str(author) if author else None,
            artist=str(artist) if artist else None,
            genres=genres,
            status=status,
            year=year,
            cover_url=self._comick_cover_url(data),
            language=manga_language,
            content_rating=content_rating,
            chapters=[],  # Hydraté séparément via get_chapters.
            url=url,
            updated_at=updated_at,
        )

    def _comick_build_chapter(
        self,
        data: dict[str, Any],
        *,
        manga_slug: str,
    ) -> Chapter:
        """Construit un objet :class:`Chapter` depuis une entité API Comick.

        Args:
            data: Entité ``chapter`` retournée par l'API.
            manga_slug: Slug du manga parent (pour construire l'URL).

        Returns:
            Objet :class:`Chapter` hydraté.
        """
        chapter_hid = str(data.get("hid") or data.get("id") or "")
        chapter_num_raw = data.get("chap")
        chapter_number = self._comick_parse_chapter_number(chapter_num_raw)

        volume_raw = data.get("vol")
        volume: int | None = None
        if volume_raw is not None:
            try:
                volume = int(str(volume_raw))
            except (ValueError, TypeError):
                volume = None

        # Titre : Comick a souvent des titres vides, on génère un label.
        title_raw = data.get("title")
        if title_raw:
            title = str(title_raw)
        else:
            num_str = str(chapter_num_raw) if chapter_num_raw else "?"
            title = f"Chapter {num_str}"

        translated_lang = str(data.get("lang") or "en")
        chapter_language = _ISO_TO_LANG.get(translated_lang, self.language)

        pages_count = data.get("pages")
        if not isinstance(pages_count, int):
            pages_count = None

        published_at = self._comick_parse_iso_date(
            data.get("created_at")
        )

        url = f"{self.base_url}/comic/{manga_slug}/chapter-{chapter_num_raw or '?'}"

        return Chapter(
            id=f"{self.config.id}:{chapter_hid}",
            source_id=chapter_hid,
            title=title,
            number=chapter_number,
            volume=volume,
            language=chapter_language,
            pages_count=pages_count,
            published_at=published_at,
            url=url,
            pages=[],
        )

    # ------------------------------------------------------------------
    # Méthodes abstraites de BaseParser
    # ------------------------------------------------------------------

    async def search(
        self, query: str, *, page: int = 1
    ) -> list[SearchResult]:
        """Recherche des mangas via ``GET /v1.0/search``.

        Args:
            query: Terme de recherche (titre).
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.

        Raises:
            ParseError: Si la réponse API est inexploitable.
        """
        self.logger.debug(
            "Comick search: {query} (page {page})",
            query=query,
            page=page,
        )

        params: dict[str, Any] = {
            "q": query,
            "limit": _API_DEFAULT_LIMIT,
            "page": page,
        }

        try:
            payload = await self.api_get(
                "/v1.0/search",
                params=params,
                timeout=20.0,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec recherche Comick pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        return self._comick_parse_search_payload(payload)

    def _comick_parse_search_payload(
        self, payload: Any
    ) -> list[SearchResult]:
        """Parse la charge utile JSON de recherche.

        Args:
            payload: Réponse JSON de ``GET /v1.0/search``.

        Returns:
            Liste de :class:`SearchResult`.
        """
        if not isinstance(payload, dict):
            return []

        items = payload.get("data")
        if not isinstance(items, list):
            return []

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                manga = self._comick_build_manga(item)
            except ParseError:
                continue

            results.append(
                SearchResult(
                    title=manga.title,
                    url=manga.url,
                    site_id=self.config.id,
                    cover_url=manga.cover_url,
                    author=manga.author,
                )
            )

        self.logger.info(
            "Comick search: {n} résultat(s)", n=len(results)
        )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga via ``GET /comic/{slug}``.

        Args:
            url_or_id: Slug Comick ou URL complète.

        Returns:
            Objet :class:`Manga` hydraté.

        Raises:
            MangaNotFoundError: Si le manga est introuvable.
            ParseError: Si la réponse API est inexploitable.
        """
        slug = self._comick_extract_id_from_url(url_or_id)
        self.logger.debug("Comick get_manga: {slug}", slug=slug)

        try:
            payload = await self.api_get(
                f"/comic/{slug}",
                timeout=20.0,
            )
        except Exception as exc:  # noqa: BLE001
            # Comick renvoie 404 pour manga inexistant.
            if "404" in str(exc):
                raise MangaNotFoundError(
                    f"Manga introuvable : {slug}"
                ) from exc
            self.logger.error(
                "Échec get_manga Comick pour {slug!r}: {err}",
                slug=slug,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {slug!r}: {exc}"
            ) from exc

        if not isinstance(payload, dict) or "data" not in payload:
            raise ParseError(
                f"Réponse API invalide pour {slug!r} : 'data' absent"
            )

        return self._comick_build_manga(payload["data"])

    async def get_chapters(self, manga: Manga) -> list[Chapter]:
        """Récupère la liste des chapitres via ``GET /comic/{hid}/chapters``.

        Args:
            manga: Manga dont on veut les chapitres.

        Returns:
            Liste de :class:`Chapter` triés par numéro croissant.

        Raises:
            ChapterNotFoundError: Si aucun chapitre n'est trouvé.
            ParseError: Si la réponse API est inexploitable.
        """
        manga_slug = manga.source_id
        self.logger.debug(
            "Comick get_chapters: {title} ({slug})",
            title=manga.title,
            slug=manga_slug,
        )

        lang = self._comick_lang_code()
        chapters: list[Chapter] = []
        page = 1

        while True:
            params: dict[str, Any] = {
                "lang": lang,
                "limit": _API_MAX_LIMIT,
                "page": page,
            }

            try:
                payload = await self.api_get(
                    f"/comic/{manga_slug}/chapters",
                    params=params,
                    timeout=25.0,
                )
            except Exception as exc:  # noqa: BLE001
                if "404" in str(exc):
                    raise ChapterNotFoundError(
                        f"Aucun chapitre pour manga {manga_slug!r}"
                    ) from exc
                self.logger.error(
                    "Échec get_chapters Comick pour {slug!r}: {err}",
                    slug=manga_slug,
                    err=exc,
                )
                raise ParseError(
                    f"get_chapters échoué sur {self.site_id!r} "
                    f"pour {manga_slug!r}: {exc}"
                ) from exc

            if not isinstance(payload, dict):
                break

            items = payload.get("data")
            if not isinstance(items, list) or not items:
                break

            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    chapter = self._comick_build_chapter(
                        item, manga_slug=manga_slug
                    )
                except (ParseError, KeyError, ValueError):
                    continue
                chapters.append(chapter)

            # Gestion de la pagination.
            total = payload.get("total")
            if not isinstance(total, int):
                total = None

            if total is not None and len(chapters) >= total:
                break
            if len(items) < _API_MAX_LIMIT:
                break
            page += 1
            # Sécurité anti-boucle infinie.
            if page > 100:
                self.logger.warning(
                    "Comick: limite de pagination atteinte pour {slug}",
                    slug=manga_slug,
                )
                break

        if not chapters:
            raise ChapterNotFoundError(
                f"Aucun chapitre trouvé pour {manga_slug!r} "
                f"sur {self.site_id!r}"
            )

        # Tri par numéro croissant.
        def _sort_key(ch: Chapter) -> tuple[int, float, str]:
            num = ch.number if isinstance(ch.number, (int, float)) else 0.0
            return (int(isinstance(ch.number, str)), float(num), ch.title.lower())

        chapters.sort(key=_sort_key)
        self.logger.info(
            "Comick get_chapters OK: {n} chapitre(s) pour {title}",
            n=len(chapters),
            title=manga.title,
        )
        return chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages via ``GET /chapter/{hid}/images``.

        L'API Comick retourne un tableau d'URLs d'images directes (pas de
        CDN intermédiaire). Les URLs pointent vers ``meo.comick.pictures``
        et nécessitent un header ``Referer`` correct pour être téléchargées.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune page n'est trouvée.
            ParseError: Si la réponse API est inexploitable.
        """
        chapter_hid = chapter.source_id
        self.logger.debug(
            "Comick get_pages: {hid}", hid=chapter_hid
        )

        try:
            payload = await self.api_get(
                f"/chapter/{chapter_hid}/images",
                timeout=15.0,
            )
        except Exception as exc:  # noqa: BLE001
            if "404" in str(exc):
                raise ChapterNotFoundError(
                    f"Chapitre introuvable : {chapter_hid}"
                ) from exc
            self.logger.error(
                "Échec get_pages Comick pour {hid!r}: {err}",
                hid=chapter_hid,
                err=exc,
            )
            raise ParseError(
                f"get_pages échoué sur {self.site_id!r} "
                f"pour {chapter_hid!r}: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise ParseError(
                f"Réponse images invalide pour {chapter_hid!r}"
            )

        # L'API retourne {data: [url1, url2, ...]} ou {data: {images: [...]}}.
        data = payload.get("data")
        urls: list[str] = []

        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, str):
                    urls.append(entry)
                elif isinstance(entry, dict):
                    u = entry.get("url") or entry.get("src")
                    if isinstance(u, str):
                        urls.append(u)
        elif isinstance(data, dict):
            images = data.get("images") or data.get("pages") or []
            if isinstance(images, list):
                for entry in images:
                    if isinstance(entry, str):
                        urls.append(entry)
                    elif isinstance(entry, dict):
                        u = entry.get("url") or entry.get("src")
                        if isinstance(u, str):
                            urls.append(u)

        if not urls:
            raise ChapterNotFoundError(
                f"Aucune page retournée pour {chapter_hid!r}"
            )

        pages: list[Page] = []
        for idx, url in enumerate(urls):
            clean = url.strip()
            if not clean:
                continue
            filename = (
                clean.rsplit("/", 1)[-1].split("?", 1)[0]
                or f"page_{idx:04d}.jpg"
            )
            if "." not in filename:
                filename = f"{filename}.jpg"
            pages.append(
                Page(
                    index=idx + 1,
                    url=clean,
                    filename=filename,
                    checksum=None,
                )
            )

        if not pages:
            raise ChapterNotFoundError(
                f"Aucune page valide pour {chapter_hid!r}"
            )

        self.logger.info(
            "Comick get_pages OK: {n} page(s) pour {hid}",
            n=len(pages),
            hid=chapter_hid,
        )
        return pages

    # ------------------------------------------------------------------
    # Endpoints complémentaires Comick
    # ------------------------------------------------------------------

    async def get_manga_by_hid(self, hid: str) -> Manga:
        """Récupère un manga via son identifiant hexadécimal (``hid``).

        Args:
            hid: Identifiant hexadécimal du manga.

        Returns:
            Objet :class:`Manga` hydraté.

        Raises:
            MangaNotFoundError: Si le manga est introuvable.
            ParseError: Si la réponse API est inexploitable.
        """
        try:
            payload = await self.api_get(f"/comic/{hid}", timeout=20.0)
        except Exception as exc:  # noqa: BLE001
            if "404" in str(exc):
                raise MangaNotFoundError(f"Manga introuvable : {hid}") from exc
            raise ParseError(
                f"get_manga_by_hid échoué pour {hid!r}: {exc}"
            ) from exc

        if not isinstance(payload, dict) or "data" not in payload:
            raise ParseError(f"Réponse invalide pour {hid!r}")
        return self._comick_build_manga(payload["data"])

    async def get_chapter(self, hid: str) -> Chapter:
        """Récupère les détails d'un chapitre via ``GET /chapter/{hid}``.

        Args:
            hid: Identifiant hexadécimal du chapitre.

        Returns:
            Objet :class:`Chapter` hydraté.

        Raises:
            ChapterNotFoundError: Si le chapitre est introuvable.
            ParseError: Si la réponse API est inexploitable.
        """
        try:
            payload = await self.api_get(f"/chapter/{hid}", timeout=15.0)
        except Exception as exc:  # noqa: BLE001
            if "404" in str(exc):
                raise ChapterNotFoundError(
                    f"Chapitre introuvable : {hid}"
                ) from exc
            raise ParseError(
                f"get_chapter échoué pour {hid!r}: {exc}"
            ) from exc

        if not isinstance(payload, dict) or "data" not in payload:
            raise ParseError(f"Réponse invalide pour {hid!r}")
        data = payload["data"]
        if not isinstance(data, dict):
            raise ParseError(f"Données invalides pour {hid!r}")

        # Utilise le slug du manga parent si disponible.
        manga_slug = ""
        manga_data = data.get("manga") or data.get("comic")
        if isinstance(manga_data, dict):
            manga_slug = str(manga_data.get("slug") or "")

        return self._comick_build_chapter(data, manga_slug=manga_slug)

    async def get_latest(self, *, limit: int = 20) -> list[Chapter]:
        """Récupère les derniers chapitres publiés.

        Args:
            limit: Nombre maximum de chapitres à retourner.

        Returns:
            Liste de :class:`Chapter` triés par date de publication
            décroissante.
        """
        try:
            payload = await self.api_get(
                "/chapter",
                params={"limit": min(limit, _API_MAX_LIMIT), "order": "desc"},
                timeout=20.0,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Échec get_latest: {err}", err=exc)
            return []

        if not isinstance(payload, dict):
            return []
        items = payload.get("data") or []
        chapters: list[Chapter] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                manga_slug = ""
                manga_data = item.get("manga") or item.get("comic")
                if isinstance(manga_data, dict):
                    manga_slug = str(manga_data.get("slug") or "")
                chapters.append(
                    self._comick_build_chapter(item, manga_slug=manga_slug)
                )
            except (ParseError, KeyError, ValueError):
                continue
        return chapters

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que l'API Comick est accessible.

        ⚠️ Le service étant fermé, cette méthode retournera probablement
        ``False``.

        Returns:
            ``True`` si l'API répond en < 500.
        """
        try:
            payload = await self.api_get(
                "/v1.0/search",
                params={"q": "test", "limit": 1},
                timeout=10.0,
            )
            ok = isinstance(payload, dict) and "data" in payload
            self.logger.info(
                "Health check Comick: {ok}", ok=ok
            )
            return ok
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                "Health check Comick KO: {err} — le service est "
                "probablement fermé depuis 2025",
                err=exc,
            )
            return False
