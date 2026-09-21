"""Parseur MangaDex pour NexusDL.

MangaDex (``https://mangadex.org``) est **la** référence open-source dans
l'écosystème manga : plateforme de lecture sans publicité, API publique,
gratuite et documentée, servant plus de 92 000 titres dans 40+ langues.

Ce parseur est **particulier** dans NexusDL : c'est le seul à utiliser une
**API REST officielle** entièrement documentée, sans scraping HTML ni
contournement Cloudflare. Il sert de **référence architecturale** pour tous
les autres parseurs et démontre l'usage canonique de
:class:`~nexusdl.parsers.mixins.api_based.ApiBasedMixin`.

Caractéristiques techniques
---------------------------

* **API** : ``https://api.mangadex.org`` (publique, sans authentification
  pour les endpoints de lecture).
* **CDN images** : ``https://uploads.mangadex.org`` (covers) et
  **MangaDex@Home** (chapitres, URLs dynamiques).
* **Protection** : aucune (pas de Cloudflare, pas de captcha).
* **Langue** : multi-langue (par défaut ``en``).
* **Rate limiting** :
    - Global : ~5 req/s par IP.
    - ``/auth/login`` : 30 req / 60 s.
    - ``/auth/refresh`` : 60 req / 60 s.
    - Chapitres : 300 req / 10 min.
    - Random manga : 60 req / min.
* **User-Agent** : obligatoire, ne doit **jamais** être falsifié (rejet HTTP).
* **Endpoints principaux** :
    - ``GET /manga`` — recherche catalogue (filtres avancés par tags).
    - ``GET /manga/{id}`` — détails d'un manga.
    - ``GET /manga/{id}/feed`` — liste des chapitres.
    - ``GET /manga/{id}/aggregate`` — hiérarchie volumes/chapitres.
    - ``GET /chapter`` — recherche de chapitres.
    - ``GET /chapter/{id}`` — détails d'un chapitre.
    - ``GET /at-home/server/{chapterId}`` — métadonnées CDN d'images.
    - ``GET /manga/tag`` — liste des tags/genres officiels.
* **Reference expansion** : ``includes[]=cover_art``, ``includes[]=author``,
  ``includes[]=artist`` permet d'inclure les ressources liées dans une seule
  requête (évite le N+1 problème).
* **Deux qualités d'image** :
    - ``data`` — qualité originale (pixel-perfect).
    - ``data-saver`` — compression (bande passante réduite).

Politique d'usage acceptable MangaDex (RAI)
--------------------------------------------

L'API MangaDex est publique et gratuite **à condition de** :

1. Créditer MangaDex.
2. Créditer les groupes de scanlation (et honorer leurs demandes de retrait).
3. Ne **pas** afficher de publicité ni proposer de service payant exploitant
   l'API.
4. Toujours envoyer un **User-Agent identifiable** (pas de spoof).
5. **Proxyfier** les images côté serveur (interdiction de hotlink).
6. Ne pas déclencher les protections anti-DDoS (persistance après HTTP 429).

NexusDL respecte ces règles par construction : ce parseur est paramétré avec
un User-Agent identifiable, un rate limit conservateur (2 req/s), et le
téléchargement des images passe par la ``HttpSession`` du projet (proxy
naturel).

Example:
    Utilisation directe du parseur :

    >>> from nexusdl.core.models.site import SiteConfig
    >>> from nexusdl.core.session.http_session import HttpSession
    >>> from nexusdl.parsers.en.mangadex import MangaDexParser
    >>>
    >>> parser = MangaDexParser(config, session)
    >>> results = await parser.search("one piece")
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

__all__ = ["MangaDexParser"]


# ---------------------------------------------------------------------------
# Constantes spécifiques à l'API MangaDex
# ---------------------------------------------------------------------------

_API_URL: Final[str] = "https://api.mangadex.org"
_UPLOADS_URL: Final[str] = "https://uploads.mangadex.org"
_SITE_ID: Final[str] = "mangadex"
_LANGUAGE: Final[Language] = Language.EN
_ADULT: Final[bool] = False

# User-Agent identifiable (obligatoire et non-spoofable selon la RAI).
_USER_AGENT: Final[str] = (
    "NexusDL/1.0 (https://github.com/nexusdl/nexusdl)"
)

# Mapping des langues NexusDL → codes MangaDex (ISO 639-1).
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

# Mapping inverse : code ISO MangaDex → Language NexusDL.
_ISO_TO_LANG: Final[dict[str, Language]] = {
    code: lang for lang, code in _LANG_MAP.items()
}

# Statuts MangaDex → énumération NexusDL.
_STATUS_MAP: Final[dict[str, MangaStatus]] = {
    "ongoing": MangaStatus.ONGOING,
    "completed": MangaStatus.COMPLETED,
    "hiatus": MangaStatus.HIATUS,
    "cancelled": MangaStatus.CANCELLED,
}

# Content ratings MangaDex → énumération NexusDL.
_RATING_MAP: Final[dict[str, ContentRating]] = {
    "safe": ContentRating.SAFE,
    "suggestive": ContentRating.SUGGESTIVE,
    "erotica": ContentRating.EROTICA,
    "pornographic": ContentRating.PORNOGRAPHIC,
}

# Regex de parsing des numéros de chapitre (MangaDex stocke en str).
_CHAPTER_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"^(\d+(?:\.\d+)?)", re.IGNORECASE
)

# Pagination : limite maximale par requête.
_API_MAX_LIMIT: Final[int] = 100
_API_DEFAULT_LIMIT: Final[int] = 20


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


class MangaDexParser(ApiBasedMixin, BaseParser):
    """Parseur MangaDex via l'API REST officielle.

    Aucun scraping HTML, aucun contournement Cloudflare : l'intégralité des
    données provient de l'API JSON publique documentée. Ce parseur illustre
    l'usage canonique de :class:`ApiBasedMixin` et sert de référence pour
    les autres intégrations API.

    Attributes:
        site_id: Identifiant interne du site.
        language: Langue principale (anglais par défaut, configurable).
        adult: Contenu adulte (``False`` — MangaDex affiche un avertissement
            mais reste accessible sans flag).
        base_url: URL publique du site.
    """

    # --- Métadonnées du parser ---
    site_id: ClassVar[str] = _SITE_ID
    language: ClassVar[Language] = _LANGUAGE
    adult: ClassVar[bool] = _ADULT

    # --- URLs de base ---
    base_url: ClassVar[str] = "https://mangadex.org"
    api_url: ClassVar[str] = _API_URL
    uploads_url: ClassVar[str] = _UPLOADS_URL

    # --- Configuration API (ApiBasedMixin) ---
    api_base_url: ClassVar[str] = _API_URL
    api_default_headers: ClassVar[dict[str, str]] = {
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }
    api_rate_limit_per_second: ClassVar[float] = 2.0
    api_rate_limit_burst: ClassVar[int] = 3
    api_timeout: ClassVar[float] = 20.0
    api_cache_enabled: ClassVar[bool] = True
    api_cache_ttl: ClassVar[int] = 300
    api_raise_on_error_status: ClassVar[bool] = True

    # --- Configuration retry ---
    # MangaDex renvoie des 5xx transitoires sous charge : 3 retries suffisent.
    # Politique conservatrice pour ne pas aggraver la charge serveur.

    # --- Comportement spécifique ---
    # Utilise la qualité "data" par défaut (qualité originale).
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
        language: Language | None = None,
        use_data_saver: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialise le parseur MangaDex.

        Args:
            config: Configuration du site (:class:`SiteConfig`).
            session: Session HTTP async du projet (:class:`HttpSession`).
            language: Langue cible des chapitres (par défaut : anglais).
            use_data_saver: Si ``True``, utilise les images compressées
                (``data-saver``) au lieu des images originales (``data``).
                Utile pour les connexions lentes ou les profils économes.
            **kwargs: Arguments additionnels transmis à :class:`BaseParser`.
        """
        super().__init__(config=config, session=session, **kwargs)
        if language is not None:
            self.language = language
        if use_data_saver is not None:
            self.use_data_saver = use_data_saver
        self.logger = get_logger(f"{self.__class__.__module__}.{self.site_id}")

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------

    def _md_logger(self) -> Any:
        """Retourne un logger contextualisé.

        Returns:
            Logger loguru bindé avec ``site_id`` et ``mixin="mangadex"``.
        """
        return self.logger

    def _md_lang_code(self) -> str:
        """Retourne le code ISO de la langue courante.

        Returns:
            Code ISO 639-1 (``en``, ``fr``, etc.).
        """
        return _LANG_MAP.get(self.language, "en")

    @staticmethod
    def _md_extract_localized(
        localized: dict[str, str] | None,
        preferred: str,
        *,
        fallback: str | None = None,
    ) -> str | None:
        """Extrait une chaîne localisée d'un dictionnaire MangaDex.

        Les champs ``title``, ``description`` et ``altTitles`` sont des
        dictionnaires ``{code_iso: texte}`` dans l'API MangaDex.

        Args:
            localized: Dictionnaire localisé de l'API.
            preferred: Code ISO préféré (ex. ``"en"``).
            fallback: Code ISO secondaire (ex. ``"ja"``).

        Returns:
            Chaîne extraite ou ``None``.
        """
        if not localized:
            return None
        if preferred in localized:
            return localized[preferred]
        if fallback and fallback in localized:
            return localized[fallback]
        # Retourne la première valeur non vide trouvée.
        for value in localized.values():
            if value:
                return value
        return None

    def _md_cover_url(self, manga_id: str, filename: str) -> str:
        """Construit l'URL absolue d'une couverture MangaDex.

        Format : ``{uploads_url}/covers/{manga_id}/{filename}``.

        Args:
            manga_id: UUID du manga.
            filename: Nom de fichier de la couverture (avec extension).

        Returns:
            URL absolue de la couverture.
        """
        return f"{self.uploads_url}/covers/{manga_id}/{filename}"

    def _md_parse_chapter_number(self, raw: str | None) -> float | str:
        """Parse un numéro de chapitre MangaDex.

        MangaDex stocke les numéros de chapitre sous forme de chaînes
        (ex. ``"1090"``, ``"12.5"``, ``""``).

        Args:
            raw: Numéro brut (str) ou ``None``.

        Returns:
            ``float`` si parsable, sinon la chaîne nettoyée ou ``0.0``.
        """
        if not raw:
            return 0.0
        cleaned = raw.strip()
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
    def _md_parse_iso_date(raw: str | None) -> datetime | None:
        """Parse une date ISO 8601 MangaDex.

        Args:
            raw: Chaîne de date RFC 3339 (ex. ``"2024-01-15T12:30:00+00:00"``).

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

    # ------------------------------------------------------------------
    # Construction des objets Manga / Chapter / Page
    # ------------------------------------------------------------------

    def _md_build_manga(self, data: dict[str, Any]) -> Manga:
        """Construit un objet :class:`Manga` depuis une entité API MangaDex.

        Args:
            data: Entité ``manga`` retournée par l'API (avec ``id``,
                ``attributes`` et ``relationships``).

        Returns:
            Objet :class:`Manga` hydraté.

        Raises:
            ParseError: Si les données sont inexploitables.
        """
        manga_id = data.get("id")
        if not manga_id:
            raise ParseError("Entité manga sans 'id' dans la réponse API")

        attributes = data.get("attributes") or {}
        relationships = data.get("relationships") or []

        # Titre principal (localisé).
        title = self._md_extract_localized(
            attributes.get("title"), self._md_lang_code(), fallback="en"
        ) or "Untitled"

        # Titres alternatifs.
        alt_titles: list[str] = []
        for alt in attributes.get("altTitles") or []:
            if isinstance(alt, dict):
                for value in alt.values():
                    if value and value not in alt_titles:
                        alt_titles.append(str(value))

        # Description.
        description = self._md_extract_localized(
            attributes.get("description"), self._md_lang_code(), fallback="en"
        )

        # Auteur et artiste (via relationships).
        author: str | None = None
        artist: str | None = None
        cover_filename: str | None = None
        for rel in relationships:
            rel_type = rel.get("type")
            rel_attrs = rel.get("attributes") or {}
            if rel_type == "author":
                author = rel_attrs.get("name") or author
            elif rel_type == "artist":
                artist = rel_attrs.get("name") or artist
            elif rel_type == "cover_art":
                cover_filename = rel_attrs.get("fileName") or cover_filename

        # Couverture.
        cover_url = (
            self._md_cover_url(manga_id, cover_filename)
            if cover_filename
            else None
        )

        # Genres (tags).
        genres: list[str] = []
        for tag in attributes.get("tags") or []:
            if not isinstance(tag, dict):
                continue
            tag_attrs = tag.get("attributes") or {}
            tag_name = self._md_extract_localized(
                tag_attrs.get("name"), "en", fallback=self._md_lang_code()
            )
            if tag_name and tag_name not in genres:
                genres.append(tag_name)

        # Statut.
        status_raw = attributes.get("status")
        status = _STATUS_MAP.get(
            str(status_raw).lower() if status_raw else "", MangaStatus.ONGOING
        )

        # Année.
        year = attributes.get("year")
        if not isinstance(year, int):
            year = None

        # Content rating.
        rating_raw = attributes.get("contentRating")
        content_rating = _RATING_MAP.get(
            str(rating_raw).lower() if rating_raw else "", ContentRating.SAFE
        )

        # Langue originale du manga.
        orig_lang_raw = attributes.get("originalLanguage")
        manga_language = (
            _ISO_TO_LANG.get(str(orig_lang_raw), self.language)
            if orig_lang_raw
            else self.language
        )

        # Dates.
        updated_at = (
            self._md_parse_iso_date(attributes.get("updatedAt"))
            or datetime.now(timezone.utc)
        )

        # URL publique.
        url = f"{self.base_url}/title/{manga_id}"

        return Manga(
            id=f"{self.config.id}:{manga_id}",
            source_id=str(manga_id),
            site=self.config.id,
            title=str(title),
            alternative_titles=alt_titles,
            description=str(description) if description else None,
            author=author,
            artist=artist,
            genres=genres,
            status=status,
            year=year,
            cover_url=cover_url,
            language=manga_language,
            content_rating=content_rating,
            chapters=[],  # Hydraté séparément via get_chapters.
            url=url,
            updated_at=updated_at,
        )

    def _md_build_chapter(
        self,
        data: dict[str, Any],
        *,
        manga_id: str,
    ) -> Chapter:
        """Construit un objet :class:`Chapter` depuis une entité API MangaDex.

        Args:
            data: Entité ``chapter`` retournée par l'API.
            manga_id: UUID du manga parent (pour construire l'URL).

        Returns:
            Objet :class:`Chapter` hydraté.
        """
        chapter_id = str(data.get("id") or "")
        attributes = data.get("attributes") or {}

        chapter_num_raw = attributes.get("chapter")
        chapter_number = self._md_parse_chapter_number(chapter_num_raw)

        volume_raw = attributes.get("volume")
        volume: int | None = None
        if volume_raw is not None:
            try:
                volume = int(str(volume_raw))
            except (ValueError, TypeError):
                volume = None

        # Titre : MangaDex a souvent des titres vides, on génère un label.
        title_raw = attributes.get("title")
        if title_raw:
            title = str(title_raw)
        else:
            num_str = str(chapter_num_raw) if chapter_num_raw else "?"
            title = f"Chapter {num_str}"

        translated_lang = str(attributes.get("translatedLanguage") or "en")
        chapter_language = _ISO_TO_LANG.get(translated_lang, self.language)

        pages_count = attributes.get("pages")
        if not isinstance(pages_count, int):
            pages_count = None

        published_at = self._md_parse_iso_date(
            attributes.get("publishAt")
        )

        url = f"{self.base_url}/chapter/{chapter_id}"

        return Chapter(
            id=f"{self.config.id}:{chapter_id}",
            source_id=chapter_id,
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
        """Recherche des mangas via ``GET /manga``.

        Args:
            query: Terme de recherche (titre).
            page: Numéro de page (1-based).

        Returns:
            Liste de :class:`SearchResult`.

        Raises:
            ParseError: Si la réponse API est inexploitable.
        """
        self.logger.debug(
            "MangaDex search: {query} (page {page})",
            query=query,
            page=page,
        )

        offset = max(0, (page - 1) * _API_DEFAULT_LIMIT)
        params: dict[str, Any] = {
            "title": query,
            "limit": _API_DEFAULT_LIMIT,
            "offset": offset,
            "includes[]": ["cover_art", "author", "artist"],
            "order[relevance]": "desc",
        }

        try:
            payload = await self.api_get(
                "/manga",
                params=params,
                timeout=20.0,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec recherche MangaDex pour {query!r}: {err}",
                query=query,
                err=exc,
            )
            raise ParseError(
                f"Recherche échouée sur {self.site_id!r} pour {query!r}: {exc}"
            ) from exc

        return self._md_parse_search_payload(payload)

    def _md_parse_search_payload(
        self, payload: Any
    ) -> list[SearchResult]:
        """Parse la charge utile JSON de recherche.

        Args:
            payload: Réponse JSON de ``GET /manga``.

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
                manga = self._md_build_manga(item)
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
            "MangaDex search: {n} résultat(s)", n=len(results)
        )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère la fiche complète d'un manga via ``GET /manga/{id}``.

        Args:
            url_or_id: UUID MangaDex ou URL complète.

        Returns:
            Objet :class:`Manga` hydraté.

        Raises:
            MangaNotFoundError: Si le manga est introuvable.
            ParseError: Si la réponse API est inexploitable.
        """
        manga_id = self._md_extract_id_from_url(url_or_id)
        self.logger.debug("MangaDex get_manga: {id}", id=manga_id)

        params = {
            "includes[]": ["cover_art", "author", "artist"],
        }

        try:
            payload = await self.api_get(
                f"/manga/{manga_id}",
                params=params,
                timeout=20.0,
            )
        except Exception as exc:  # noqa: BLE001
            # MangaDex renvoie 404 pour manga inexistant.
            if "404" in str(exc):
                raise MangaNotFoundError(
                    f"Manga introuvable : {manga_id}"
                ) from exc
            self.logger.error(
                "Échec get_manga MangaDex pour {id!r}: {err}",
                id=manga_id,
                err=exc,
            )
            raise ParseError(
                f"get_manga échoué sur {self.site_id!r} pour {manga_id!r}: {exc}"
            ) from exc

        if not isinstance(payload, dict) or "data" not in payload:
            raise ParseError(
                f"Réponse API invalide pour {manga_id!r} : 'data' absent"
            )

        return self._md_build_manga(payload["data"])

    @staticmethod
    def _md_extract_id_from_url(url_or_id: str) -> str:
        """Extrait l'UUID MangaDex depuis une URL ou un ID brut.

        Formats acceptés :
            - UUID brut : ``f98660a1-d2e2-461c-960d-7bd13df8b76d``
            - URL : ``https://mangadex.org/title/{uuid}/...``
            - URL : ``https://mangadex.org/chapter/{uuid}/...``

        Args:
            url_or_id: URL ou UUID.

        Returns:
            UUID extrait (ou la chaîne telle quelle si aucun UUID trouvé).
        """
        if not url_or_id:
            return url_or_id
        if url_or_id.startswith(("http://", "https://")):
            parsed = urlparse(url_or_id)
            parts = [p for p in parsed.path.split("/") if p]
            # Cherche le premier segment qui ressemble à un UUID.
            uuid_re = re.compile(
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                re.IGNORECASE,
            )
            for part in parts:
                if uuid_re.match(part):
                    return part
            # Fallback : dernier segment.
            return parts[-1] if parts else url_or_id
        return url_or_id.strip()

    async def get_chapters(self, manga: Manga) -> list[Chapter]:
        """Récupère la liste des chapitres via ``GET /manga/{id}/feed``.

        Args:
            manga: Manga dont on veut les chapitres.

        Returns:
            Liste de :class:`Chapter` triés par numéro croissant.

        Raises:
            ChapterNotFoundError: Si aucun chapitre n'est trouvé.
            ParseError: Si la réponse API est inexploitable.
        """
        manga_id = manga.source_id
        self.logger.debug(
            "MangaDex get_chapters: {title} ({id})",
            title=manga.title,
            id=manga_id,
        )

        lang = self._md_lang_code()
        chapters: list[Chapter] = []
        offset = 0
        limit = _API_MAX_LIMIT
        total = None

        while True:
            params: dict[str, Any] = {
                "translatedLanguage[]": [lang],
                "limit": limit,
                "offset": offset,
                "includes[]": ["scanlation_group"],
                "order[chapter]": "asc",
                "contentRating[]": [
                    "safe",
                    "suggestive",
                    "erotica",
                    "pornographic",
                ],
            }

            try:
                payload = await self.api_get(
                    f"/manga/{manga_id}/feed",
                    params=params,
                    timeout=25.0,
                )
            except Exception as exc:  # noqa: BLE001
                if "404" in str(exc):
                    raise ChapterNotFoundError(
                        f"Aucun chapitre pour manga {manga_id!r}"
                    ) from exc
                self.logger.error(
                    "Échec get_chapters MangaDex pour {id!r}: {err}",
                    id=manga_id,
                    err=exc,
                )
                raise ParseError(
                    f"get_chapters échoué sur {self.site_id!r} "
                    f"pour {manga_id!r}: {exc}"
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
                    chapter = self._md_build_chapter(
                        item, manga_id=manga_id
                    )
                except (ParseError, KeyError, ValueError):
                    continue
                chapters.append(chapter)

            # Gestion de la pagination.
            if total is None:
                total = payload.get("total")
                if not isinstance(total, int):
                    total = None

            offset += len(items)
            if total is not None and offset >= total:
                break
            if len(items) < limit:
                break
            # Sécurité anti-boucle infinie.
            if offset > 10_000:
                self.logger.warning(
                    "MangaDex: limite de pagination atteinte pour {id}",
                    id=manga_id,
                )
                break

        if not chapters:
            raise ChapterNotFoundError(
                f"Aucun chapitre trouvé pour {manga_id!r} sur {self.site_id!r}"
            )

        # Tri par numéro croissant.
        def _sort_key(ch: Chapter) -> tuple[int, float, str]:
            num = ch.number if isinstance(ch.number, (int, float)) else 0.0
            return (int(isinstance(ch.number, str)), float(num), ch.title.lower())

        chapters.sort(key=_sort_key)
        self.logger.info(
            "MangaDex get_chapters OK: {n} chapitre(s) pour {title}",
            n=len(chapters),
            title=manga.title,
        )
        return chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages via ``GET /at-home/server/{id}``.

        L'API MangaDex retourne une URL de base **dynamique** (MangaDex@Home)
        et deux tableaux de noms de fichiers (``data`` et ``data-saver``). Les
        URLs finales sont construites selon le format documenté :
        ``{baseUrl}/{quality}/{chapterHash}/{filename}``.

        Notes:
            - Les URLs ``baseUrl`` sont **géographiquement optimisées** et
              ont une validité de **15 minutes** (garantie minimale).
            - Il est **interdit** d'envoyer des headers d'authentification
              aux serveurs d'images (rejet HTTP).
            - Le hotlinking direct est interdit par la politique CORS
              MangaDex : les images doivent être proxyfiées.

        Args:
            chapter: Chapitre cible.

        Returns:
            Liste ordonnée de :class:`Page`.

        Raises:
            ChapterNotFoundError: Si aucune page n'est trouvée.
            ParseError: Si la réponse API est inexploitable.
        """
        chapter_id = chapter.source_id
        self.logger.debug(
            "MangaDex get_pages: {id}", id=chapter_id
        )

        try:
            payload = await self.api_get(
                f"/at-home/server/{chapter_id}",
                timeout=15.0,
            )
        except Exception as exc:  # noqa: BLE001
            if "404" in str(exc):
                raise ChapterNotFoundError(
                    f"Chapitre introuvable : {chapter_id}"
                ) from exc
            self.logger.error(
                "Échec get_pages MangaDex pour {id!r}: {err}",
                id=chapter_id,
                err=exc,
            )
            raise ParseError(
                f"get_pages échoué sur {self.site_id!r} "
                f"pour {chapter_id!r}: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise ParseError(
                f"Réponse at-home invalide pour {chapter_id!r}"
            )

        base_url = payload.get("baseUrl")
        chapter_meta = payload.get("chapter") or {}
        chapter_hash = chapter_meta.get("hash")

        if not base_url or not chapter_hash:
            raise ParseError(
                f"Réponse at-home incomplète pour {chapter_id!r} "
                f"(baseUrl={base_url!r}, hash={chapter_hash!r})"
            )

        # Choisit la qualité : "data" (original) ou "data-saver".
        if self.use_data_saver:
            quality = "data-saver"
            filenames = chapter_meta.get("dataSaver") or []
        else:
            quality = "data"
            filenames = chapter_meta.get("data") or []

        if not isinstance(filenames, list) or not filenames:
            raise ChapterNotFoundError(
                f"Aucune page retournée par at-home pour {chapter_id!r}"
            )

        # Construit les URLs finales.
        pages: list[Page] = []
        for idx, filename in enumerate(filenames):
            if not isinstance(filename, str) or not filename:
                continue
            page_url = (
                f"{base_url}/{quality}/{chapter_hash}/{filename}"
            )
            pages.append(
                Page(
                    index=idx + 1,
                    url=page_url,
                    filename=filename,
                    checksum=None,
                )
            )

        if not pages:
            raise ChapterNotFoundError(
                f"Aucune page valide pour {chapter_id!r}"
            )

        self.logger.info(
            "MangaDex get_pages OK: {n} page(s) pour {id} (qualité={q})",
            n=len(pages),
            id=chapter_id,
            q=quality,
        )
        return pages

    # ------------------------------------------------------------------
    # Endpoints complémentaires MangaDex
    # ------------------------------------------------------------------

    async def get_manga_by_title(
        self, title: str, *, limit: int = 10
    ) -> list[Manga]:
        """Recherche avancée retournant des objets :class:`Manga` complets.

        Contrairement à :meth:`search` qui retourne des :class:`SearchResult`
        légers, cette méthode hydrate les objets :class:`Manga` complets (utile
        pour l'indexation bibliothèque).

        Args:
            title: Titre recherché.
            limit: Nombre maximum de mangas retournés.

        Returns:
            Liste de :class:`Manga` hydratés.
        """
        params = {
            "title": title,
            "limit": min(limit, _API_MAX_LIMIT),
            "includes[]": ["cover_art", "author", "artist"],
        }
        try:
            payload = await self.api_get("/manga", params=params)
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_manga_by_title: {err}", err=exc
            )
            return []

        if not isinstance(payload, dict):
            return []
        items = payload.get("data") or []
        results: list[Manga] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                results.append(self._md_build_manga(item))
            except ParseError:
                continue
        return results

    async def get_manga_feed(
        self,
        manga_id: str,
        *,
        lang: str | None = None,
        limit: int = _API_MAX_LIMIT,
    ) -> list[Chapter]:
        """Récupère le feed de chapitres d'un manga.

        Variante bas-niveau de :meth:`get_chapters` acceptant un ``lang``
        explicite (utile pour les mangas multi-langues).

        Args:
            manga_id: UUID du manga.
            lang: Code ISO de langue (par défaut : langue du parser).
            limit: Nombre maximum de chapitres.

        Returns:
            Liste de :class:`Chapter`.
        """
        lang = lang or self._md_lang_code()
        params = {
            "translatedLanguage[]": [lang],
            "limit": min(limit, _API_MAX_LIMIT),
            "order[chapter]": "asc",
        }
        try:
            payload = await self.api_get(
                f"/manga/{manga_id}/feed", params=params
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Échec get_manga_feed: {err}", err=exc
            )
            return []
        if not isinstance(payload, dict):
            return []
        items = payload.get("data") or []
        chapters: list[Chapter] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                chapters.append(
                    self._md_build_chapter(item, manga_id=manga_id)
                )
            except (ParseError, KeyError, ValueError):
                continue
        return chapters

    async def get_tags(self) -> list[dict[str, str]]:
        """Récupère la liste des tags/genres officiels MangaDex.

        Returns:
            Liste de dictionnaires ``{"id": uuid, "name": label}``.
        """
        try:
            payload = await self.api_get("/manga/tag", timeout=15.0)
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Échec get_tags: {err}", err=exc)
            return []

        if not isinstance(payload, dict):
            return []
        items = payload.get("data") or []
        tags: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            tag_id = item.get("id")
            attrs = item.get("attributes") or {}
            name = self._md_extract_localized(
                attrs.get("name"), "en"
            )
            if tag_id and name:
                tags.append({"id": str(tag_id), "name": str(name)})
        return tags

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Vérifie que l'API MangaDex est accessible.

        Returns:
            ``True`` si l'API répond en < 500.
        """
        try:
            # Ping léger : recherche d'un manga avec limit=1.
            payload = await self.api_get(
                "/manga",
                params={"limit": 1},
                timeout=10.0,
            )
            ok = isinstance(payload, dict) and "data" in payload
            self.logger.info(
                "Health check MangaDex: {ok}", ok=ok
            )
            return ok
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                "Health check MangaDex KO: {err}", err=exc
            )
            return False
