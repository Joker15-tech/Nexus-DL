"""Interface de base et utilitaires communs pour tous les parsers de sites.

Ce module définit le contrat abstrait (ABC) que chaque implémentation de parser
doit respecter. Il garantit une cohérence totale dans la manière dont NexusDL
interagit avec les sources externes, tout en fournissant des méthodes concrètes
réutilisables (téléchargement de pages avec retry, normalisation d'URL, etc.).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Self
from urllib.parse import urljoin, urlparse

from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from nexusdl.core.models.manga import Chapter, Manga, Page, SearchResult
from nexusdl.core.models.site import SiteConfig
from nexusdl.core.session.http_session import HttpSession
from nexusdl.core.session.playwright_pool import PlaywrightPool
from nexusdl.parsers.capabilities import ParserCapabilities
from nexusdl.parsers.exceptions import (
    InvalidPageUrlError,
    ParserError,
    ParsingError,
    SiteUnavailableError,
)


class BaseParser(ABC):
    """Interface abstraite pour tous les parsers de sites de manga/comics.

    Cette classe définit le contrat minimal pour l'extraction de données
    depuis une source externe. Elle impose l'implémentation des méthodes
    de recherche, de récupération de métadonnées et de listes de pages,
    tout en fournissant des outils communs pour la résilience réseau.
    """

    # === Méta-données de classe (à surcharger par les sous-classes) ===
    site_id: ClassVar[str]
    """Identifiant unique du site (ex: 'mangadex', 'sushiscan_net')."""

    language: ClassVar[str]
    """Code langue ISO 639-1 du site (ex: 'fr', 'en', 'kr')."""

    adult: ClassVar[bool] = False
    """Indique si le site contient du contenu pour adultes (18+)."""

    capabilities: ClassVar[ParserCapabilities] = ParserCapabilities()
    """Capacités et limitations opérationnelles de ce parser."""

    # === Initialisation ===

    def __init__(
        self,
        config: SiteConfig,
        session: HttpSession,
        *,
        playwright_pool: PlaywrightPool | None = None,
    ) -> None:
        """Initialise le parser avec ses dépendances injectées.

        Args:
            config: Configuration spécifique du site (domaines, headers, etc.).
            session: Session HTTP asynchrone configurée pour ce site.
            playwright_pool: Pool de navigateurs pour le bypass Cloudflare/JS (optionnel).
        """
        self.config = config
        self.session = session
        self.playwright_pool = playwright_pool
        
        self._logger = logger.bind(
            module="parser",
            site_id=self.site_id,
            language=self.language,
        )

    # === Méthodes abstraites (à implémenter par chaque site) ===

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        page: int = 1,
    ) -> list[SearchResult]:
        """Recherche des mangas correspondant à une requête texte.

        Args:
            query: Chaîne de caractères à rechercher.
            page: Numéro de la page de résultats (pour la pagination).

        Returns:
            Liste des résultats de recherche correspondant à la requête.

        Raises:
            SiteUnavailableError: Si le site est inaccessible.
            ParserError: En cas d'erreur d'analyse des résultats.
        """
        pass

    @abstractmethod
    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère les métadonnées complètes d'un manga.

        Args:
            url_or_id: URL de la page du manga ou son identifiant source.

        Returns:
            Objet Manga entièrement peuplé avec ses métadonnées.

        Raises:
            ParserError: Si les métadonnées sont incomplètes ou introuvables.
        """
        pass

    @abstractmethod
    async def get_chapters(self, manga: Manga) -> list[Chapter]:
        """Récupère la liste de tous les chapitres disponibles pour un manga.

        Args:
            manga: Objet Manga pour lequel récupérer les chapitres.

        Returns:
            Liste triée des chapitres disponibles.

        Raises:
            ParserError: Si la liste des chapitres ne peut être analysée.
        """
        pass

    @abstractmethod
    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs de toutes les pages d'un chapitre spécifique.

        Args:
            chapter: Objet Chapter pour lequel récupérer les pages.

        Returns:
            Liste ordonnée des objets Page contenant les URLs et métadonnées.

        Raises:
            ParsingError: Si les sélecteurs d'images échouent.
            InvalidPageUrlError: Si les URLs extraites sont invalides.
        """
        pass

    # === Méthodes concrètes réutilisables ===

    def normalize_url(self, url: str) -> str:
        """Normalise une URL en s'assurant qu'elle est absolue et valide.

        Gère les URLs relatives, les protocoles manquants et les domaines
        alternatifs définis dans la configuration du site.

        Args:
            url: URL brute à normaliser.

        Returns:
            URL absolue, propre et valide.

        Raises:
            ParserError: Si l'URL ne peut être résolue avec les domaines connus.
        """
        if not url:
            raise ParserError("URL vide fournie pour normalisation", site_id=self.site_id)

        url = url.strip()
        parsed = urlparse(url)

        # Si l'URL est déjà absolue et valide, on la retourne après nettoyage
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return url

        # Sinon, on tente de la résoudre par rapport au domaine principal
        primary_domain = str(self.config.domains[0]) if self.config.domains else "https://example.com"
        normalized = urljoin(primary_domain, url)
        
        self._logger.trace("URL normalisée: '{}' -> '{}'", url, normalized)
        return normalized

    async def health_check(self) -> bool:
        """Vérifie que le site est accessible et répond correctement.

        Effectue une requête HEAD ou GET légère sur le domaine principal
        pour valider la disponibilité du site avant de lancer des tâches lourdes.

        Returns:
            True si le site répond avec un code HTTP 2xx ou 3xx, False sinon.
        """
        primary_domain = str(self.config.domains[0]) if self.config.domains else "https://example.com"
        try:
            response = await self.session.get(primary_domain, follow_redirects=True)
            return response.status_code < 400
        except Exception as e:
            self._logger.warning("Échec du health check pour {}: {}", self.site_id, e)
            return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, ParserError)),
        reraise=True,
    )
    async def download_page(
        self,
        page: Page,
        dest: Path,
    ) -> Path:
        """Télécharge une page d'image avec gestion automatique des retries.

        Cette méthode gère le téléchargement asynchrone, la validation du type
        de contenu (pour s'assurer qu'il s'agit bien d'une image) et l'écriture
        sur le disque. Elle utilise le décorateur `@retry` de tenacity pour
        gérer les échecs réseau transitoires.

        Args:
            page: Objet Page contenant l'URL et le nom de fichier attendu.
            dest: Répertoire de destination pour le fichier téléchargé.

        Returns:
            Chemin absolu du fichier téléchargé avec succès.

        Raises:
            InvalidPageUrlError: Si l'URL de la page est invalide ou vide.
            ParserError: Si le téléchargement échoue après toutes les tentatives
                         ou si le contenu reçu n'est pas une image valide.
        """
        if not page.url:
            raise InvalidPageUrlError(
                site_id=self.site_id,
                chapter_id=page.__dict__.get("chapter_id", "unknown"),
                page_index=page.index,
            )

        normalized_url = self.normalize_url(page.url)
        output_path = dest / page.filename

        # Créer le répertoire de destination s'il n'existe pas
        dest.mkdir(parents=True, exist_ok=True)

        self._logger.debug(
            "Téléchargement de la page {} vers {}",
            page.index,
            output_path.name,
        )

        try:
            # On utilise stream_download pour une meilleure gestion mémoire
            # et pour pouvoir valider le Content-Type avant l'écriture complète.
            await self.session.stream_download(
                normalized_url,
                output_path,
                chunk_size=65536,
            )
            
            # Validation basique post-téléchargement (optionnel mais recommandé)
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise ParserError(
                    f"Fichier téléchargé vide ou inexistant: {output_path}",
                    site_id=self.site_id,
                    url=normalized_url,
                )

            self._logger.trace("Page {} téléchargée avec succès", page.index)
            return output_path

        except Exception as e:
            # Nettoyer le fichier partiel en cas d'échec final après retries
            if output_path.exists():
                output_path.unlink(missing_ok=True)
            
            self._logger.error(
                "Échec définitif du téléchargement de la page {} après retries: {}",
                page.index,
                e,
            )
            raise ParserError(
                f"Échec du téléchargement de la page {page.index}: {e}",
                site_id=self.site_id,
                url=normalized_url,
            ) from e

    @staticmethod
    def extract_chapter_number(chapter_title: str, fallback: str | None = None) -> float | str:
        """Extrait le numéro de chapitre d'une chaîne de titre.

        Gère les formats courants : "Chapitre 12", "Ch. 12.5", "Episode 3",
        ou les numéros directs "12", "12.5". Retourne un float si possible,
        sinon une chaîne (pour les "Extra", "Omake", etc.).

        Args:
            chapter_title: Titre brut du chapitre.
            fallback: Valeur de repli si aucune extraction ne réussit.

        Returns:
            Numéro de chapitre sous forme de float ou de str.
        """
        if not chapter_title:
            return fallback or "0"

        # Nettoyer le titre
        cleaned = chapter_title.lower().strip()

        # Patterns courants
        patterns = [
            r"(?:chapitre|ch\.?|episode|ep\.?|capitolo)\s*([0-9]+(?:\.[0-9]+)?)",
            r"([0-9]+(?:\.[0-9]+)?)\s*(?:vostfr|vf|fr|en)?\s*$",
            r"^([0-9]+(?:\.[0-9]+)?)",
        ]

        for pattern in patterns:
            match = re.search(pattern, cleaned)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    return match.group(1)

        # Si c'est un mot-clé spécial
        if any(keyword in cleaned for keyword in ["extra", "omake", "prologue", "épilogue"]):
            return cleaned

        return fallback or cleaned

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(site_id='{self.site_id}', language='{self.language}')>"
