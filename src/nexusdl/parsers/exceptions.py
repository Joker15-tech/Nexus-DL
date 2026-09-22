"""Exceptions spécifiques aux parsers de sites.

Ce module définit la hiérarchie des exceptions levées par les parsers
lors du scraping, de l'analyse HTML/JSON ou de l'interaction avec les sites sources.
Toutes les exceptions héritent de `ParserError` pour permettre une interception
centralisée au niveau du `DownloadManager` ou des interfaces, facilitant le logging
structuré et les retries intelligents.
"""

from __future__ import annotations

from typing import Any


class ParserError(Exception):
    """Exception de base pour toutes les erreurs liées aux parsers.

    Cette classe sert de point d'ancrage pour intercepter toute erreur
    provenant d'un parser, sans attraper les exceptions système génériques.
    """

    def __init__(
        self,
        message: str,
        site_id: str | None = None,
        url: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialise l'exception de parser.

        Args:
            message: Message d'erreur descriptif et concis.
            site_id: Identifiant du site concerné (ex: 'mangadex', 'sushiscan').
            url: URL ayant provoqué l'erreur (pour le logging et le debugging).
            details: Dictionnaire de détails supplémentaires pour le débogage.
        """
        self.site_id = site_id
        self.url = url
        self.details = details or {}
        
        full_message = message
        if site_id:
            full_message = f"[{site_id}] {full_message}"
        if url:
            full_message = f"{full_message} (URL: {url})"
            
        super().__init__(full_message)


class ParserNotFoundError(ParserError):
    """Exception levée lorsqu'aucun parser n'est trouvé pour un site donné."""

    def __init__(self, site_id: str) -> None:
        """Initialise l'exception.

        Args:
            site_id: Identifiant du site introuvable dans le registre.
        """
        super().__init__(
            f"Aucun parser enregistré pour le site '{site_id}'",
            site_id=site_id,
        )


class ParsingError(ParserError):
    """Exception levée lors d'une erreur d'analyse HTML, JSON ou XML."""

    def __init__(
        self,
        message: str,
        site_id: str | None = None,
        url: str | None = None,
        selector: str | None = None,
    ) -> None:
        """Initialise l'exception.

        Args:
            message: Message d'erreur descriptif.
            site_id: Identifiant du site concerné.
            url: URL ayant provoqué l'erreur.
            selector: Sélecteur CSS ou chemin JSON spécifique qui a échoué.
        """
        details = {"selector": selector} if selector else None
        super().__init__(
            message,
            site_id=site_id,
            url=url,
            details=details,
        )


class SiteUnavailableError(ParserError):
    """Exception levée lorsque le site cible est inaccessible ou en maintenance."""

    def __init__(
        self,
        site_id: str,
        url: str,
        status_code: int | None = None,
    ) -> None:
        """Initialise l'exception.

        Args:
            site_id: Identifiant du site concerné.
            url: URL ayant provoqué l'erreur.
            status_code: Code HTTP de la réponse (si disponible).
        """
        message = "Le site est inaccessible ou en maintenance"
        if status_code:
            message = f"Le site est inaccessible (HTTP {status_code})"
        
        super().__init__(
            message,
            site_id=site_id,
            url=url,
            details={"status_code": status_code},
        )


class CloudflareBypassError(ParserError):
    """Exception levée lorsque le contournement de Cloudflare/anti-bot échoue."""

    def __init__(
        self,
        site_id: str,
        url: str,
        provider: str | None = None,
    ) -> None:
        """Initialise l'exception.

        Args:
            site_id: Identifiant du site concerné.
            url: URL ayant provoqué l'erreur.
            provider: Méthode de contournement utilisée (ex: 'flaresolverr', 'playwright').
        """
        message = "Échec du contournement de la protection anti-bot (Cloudflare)"
        if provider:
            message = f"Échec du contournement de la protection anti-bot via {provider}"
            
        super().__init__(
            message,
            site_id=site_id,
            url=url,
            details={"provider": provider},
        )


class MangaNotFoundError(ParserError):
    """Exception levée lorsqu'un manga spécifique est introuvable."""

    def __init__(self, site_id: str, identifier: str) -> None:
        """Initialise l'exception.

        Args:
            site_id: Identifiant du site concerné.
            identifier: ID, slug ou titre du manga introuvable.
        """
        super().__init__(
            f"Le manga avec l'identifiant '{identifier}' est introuvable",
            site_id=site_id,
            details={"identifier": identifier},
        )


class ChapterNotFoundError(ParserError):
    """Exception levée lorsqu'un chapitre spécifique est introuvable."""

    def __init__(
        self,
        site_id: str,
        manga_id: str,
        chapter_id: str,
    ) -> None:
        """Initialise l'exception.

        Args:
            site_id: Identifiant du site concerné.
            manga_id: Identifiant du manga parent.
            chapter_id: Identifiant du chapitre introuvable.
        """
        super().__init__(
            f"Le chapitre '{chapter_id}' est introuvable pour le manga '{manga_id}'",
            site_id=site_id,
            details={"manga_id": manga_id, "chapter_id": chapter_id},
        )


class AuthenticationRequiredError(ParserError):
    """Exception levée lorsque le site requiert une authentification non fournie."""

    def __init__(self, site_id: str, url: str) -> None:
        """Initialise l'exception.

        Args:
            site_id: Identifiant du site concerné.
            url: URL ayant provoqué l'erreur (généralement une page de login).
        """
        super().__init__(
            "Authentification requise pour accéder à cette ressource",
            site_id=site_id,
            url=url,
        )


class RateLimitExceededError(ParserError):
    """Exception levée lorsque la limite de requêtes du site est dépassée."""

    def __init__(
        self,
        site_id: str,
        url: str,
        retry_after: int | float | None = None,
    ) -> None:
        """Initialise l'exception.

        Args:
            site_id: Identifiant du site concerné.
            url: URL ayant provoqué l'erreur.
            retry_after: Délai conseillé avant de réessayer (en secondes).
        """
        message = "Limite de requêtes dépassée (Rate Limit)"
        if retry_after:
            message = f"Limite de requêtes dépassée. Réessayez dans {retry_after} secondes"
            
        super().__init__(
            message,
            site_id=site_id,
            url=url,
            details={"retry_after": retry_after},
        )


class InvalidPageUrlError(ParserError):
    """Exception levée lorsqu'une URL de page d'image est invalide ou manquante."""

    def __init__(
        self,
        site_id: str,
        chapter_id: str,
        page_index: int,
    ) -> None:
        """Initialise l'exception.

        Args:
            site_id: Identifiant du site concerné.
            chapter_id: Identifiant du chapitre concerné.
            page_index: Index de la page problématique (base 0 ou 1 selon le parser).
        """
        super().__init__(
            f"URL de page invalide ou manquante à l'index {page_index}",
            site_id=site_id,
            details={"chapter_id": chapter_id, "page_index": page_index},
        )
