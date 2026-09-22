"""Définition des capacités supportées par un parser de site.

Ce module définit les modèles de données décrivant les fonctionnalités
et les limitations d'un parser spécifique (ex: support de la recherche,
nécessité de contourner Cloudflare, limites de débit, etc.).
Ces capacités sont utilisées par le Registry et le DownloadManager
pour orchestrer les téléchargements de manière optimale et sûre.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ParserCapabilities(BaseModel):
    """Capacités et limitations opérationnelles d'un parser de site.

    Ce modèle décrit ce qu'un parser peut faire et ses contraintes
    (rate limiting, authentification, bypass Cloudflare, etc.).
    Il est utilisé par le SiteRegistry et le DownloadManager pour
    adapter le comportement du téléchargement à chaque site source.
    """

    supports_search: bool = Field(
        default=True,
        description="Le parser supporte la recherche de mangas par requête texte.",
    )
    supports_manga_info: bool = Field(
        default=True,
        description="Le parser peut récupérer les métadonnées détaillées d'un manga.",
    )
    supports_chapters: bool = Field(
        default=True,
        description="Le parser peut lister les chapitres d'un manga.",
    )
    supports_pages: bool = Field(
        default=True,
        description="Le parser peut récupérer les URLs des pages d'un chapitre.",
    )
    supports_download: bool = Field(
        default=True,
        description="Le parser permet le téléchargement effectif des images.",
    )
    
    requires_auth: bool = Field(
        default=False,
        description="Le site nécessite une authentification (cookies/login) pour accéder au contenu.",
    )
    requires_cloudflare_bypass: bool = Field(
        default=False,
        description="Le site est protégé par Cloudflare ou un système anti-bot similaire.",
    )
    requires_javascript_rendering: bool = Field(
        default=False,
        description="Le site nécessite l'exécution de JavaScript pour afficher le contenu (ex: images chargées dynamiquement).",
    )

    max_concurrent_downloads: int = Field(
        default=4,
        ge=1,
        le=32,
        description="Nombre maximum de téléchargements de pages simultanés autorisés pour ce site.",
    )
    rate_limit_per_second: float = Field(
        default=2.0,
        gt=0.0,
        le=10.0,
        description="Nombre maximum de requêtes HTTP par seconde vers ce site.",
    )
    min_delay_between_requests: float = Field(
        default=0.5,
        ge=0.0,
        description="Délai minimum en secondes entre deux requêtes consécutives.",
    )

    supports_language_filter: bool = Field(
        default=False,
        description="Le parser supporte le filtrage des résultats de recherche par langue.",
    )
    supports_content_rating_filter: bool = Field(
        default=False,
        description="Le parser supporte le filtrage par classification d'âge (Safe, Suggestive, etc.).",
    )

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
    )
