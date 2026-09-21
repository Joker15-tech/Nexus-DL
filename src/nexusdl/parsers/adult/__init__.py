"""Parsers adultes NexusDL — contenu 18+ (hentai, doujinshi, ecchi).

Ce package regroupe les parsers pour sites adultes. Chaque parser est
**autonome** — hérite directement de ``BaseParser``, aucune dépendance à
un mixin partagé. Toute la logique de scraping est contenue dans chaque
fichier, ce qui les rend indépendants et testables en isolation.

Parsers disponibles
===================

**API JSON (non officielle)** :

    - ``NHentaiParser``   — nhentai.net (doujinshi hentai international)
    - ``PururinParser``   — pururin.to (doujinshi hentai)

**WordPress Madara (français)** :

    - ``HentaiZoneParser``   — hentaizone.com (hentai + ecchi traduits)
    - ``ScanHentaiParser``   — scan-hentai.net (scantrad hentai FR)
    - ``XMangaNetParser``    — x-manga.net (domaine historique)
    - ``XMangaOrgParser``    — x-manga.org (domaine principal actuel)

Activation opt-in
=================

Ce package n'est **jamais** importé par défaut. Le ``SiteRegistry`` ne
l'inclut que si l'utilisateur a explicitement activé le mode adulte :

    .. code-block:: yaml

        # config/config.yaml
        registry:
          include_adult: true
          adult_confirmation_shown: true

Ou via la CLI ::

    nexusdl search "query" --include-adult

Sans cette activation :

    - Ce package n'est pas importé (aucune empreinte mémoire).
    - Les parsers adultes ne sont pas enregistrés dans le registry.
    - ``nexusdl --site nhentai`` retourne ``KeyError: site not found``.

Invariants de sécurité
======================

Tous les parsers de ce package respectent les invariants suivants :

    - **Pas de contournement de paywall** : les chapitres sponsor/membre/
      premium sont marqués dans les titres (``[Sponsor]``, ``[Membre]``,
      ``[Premium]``) mais **jamais** débloqués.
    - **Pas de stockage de contenu protégé** : le parser récupère des
      URLs et des métadonnées uniquement.
    - **Pas de credentials en dur** : cookies et tokens passent par
      ``CookieManager`` (chiffrés AES-GCM).
    - **Écriture atomique** : fichiers temporaires ``.part`` + rename.
    - **Validation d'images** : taille min/max + MIME vérifiés.

Usage programmatique
====================

    Import direct d'un parser ::

        from nexusdl.parsers.adult import NHentaiParser

    Import via le registry (recommandé) ::

        from nexusdl.core.registry.site_registry import SiteRegistry

        registry = SiteRegistry.from_settings(settings)
        registry.load()
        parser = registry.get_parser("nhentai")

    Introspection de tous les parsers adultes ::

        from nexusdl.parsers.adult import list_adult_parsers

        for meta in list_adult_parsers():
            print(f"{meta['site_id']:15} {meta['base_url']}")

Voir :
    - src/nexusdl/parsers/adult/nexus.dl  (documentation détaillée)
    - src/nexusdl/parsers/base.py         (ABC commune)
    - config/config.example.yaml          (section registry.include_adult)
"""

from __future__ import annotations

from typing import Any, Final

# ============================================================================
#  Imports des parsers — ordre alphabétique par site_id
# ============================================================================
# Chaque parser est importé inconditionnellement au chargement du package.
# Le coût est faible (chaque module n'importe que BaseParser + stdlib), et
# le package lui-même n'est chargé que si l'utilisateur a activé le mode
# adulte — donc ce coût n'est payé que par les utilisateurs concernés.

from nexusdl.parsers.adult.hentaizone import HentaiZoneParser
from nexusdl.parsers.adult.nhentai import NHentaiParser
from nexusdl.parsers.adult.pururin import PururinParser
from nexusdl.parsers.adult.scan_hentai import ScanHentaiParser
from nexusdl.parsers.adult.x_manga_net import XMangaNetParser
from nexusdl.parsers.adult.x_manga_org import XMangaOrgParser

# ============================================================================
#  Métadonnées du package
# ============================================================================

#: Version du package des parsers adultes.
#: Alignée sur la version globale de NexusDL (voir ``nexusdl.version``).
#: Exposée ici pour introspection rapide (CLI, tests, docs).
__version__: Final[str] = "0.1.0"

#: Indique que ce package ne contient QUE des parsers adultes (18+).
#: Utilisé par les outils d'introspection (CLI, docs) pour filtrer.
__adult_only__: Final[bool] = True

#: Nombre de parsers exposés — vérifiable en CI.
__parser_count__: Final[int] = 6


# ============================================================================
#  Helpers d'introspection
# ============================================================================


def list_adult_parsers() -> list[dict[str, Any]]:
    """Retourne les métadonnées des parsers adultes disponibles.

    Chaque entrée est un dict sérialisable JSON contenant les attributs
    de classe essentiels. Aucune instanciation n'est faite — uniquement
    de l'introspection de ``ClassVar``.

    Utilisé par la CLI (``nexusdl sites list --include-adult``) et
    l'endpoint REST ``/api/sites?include_adult=true`` (Phase 14).

    Returns:
        Liste triée par ``site_id``, chaque dict contenant :
            - ``site_id`` (str)
            - ``language`` (str)
            - ``adult`` (bool — toujours True ici)
            - ``base_url`` (str)
            - ``mirror_domains`` (list[str])
            - ``class_name`` (str)
            - ``cloudflare_strategy`` (str)
            - ``content_rating`` (str)

    Example:
        ::

            from nexusdl.parsers.adult import list_adult_parsers

            for meta in list_adult_parsers():
                print(f"{meta['site_id']:15} {meta['base_url']}")
            # hentaizone      https://hentaizone.com
            # nhentai         https://nhentai.net
            # pururin         https://pururin.to
            # scan_hentai     https://scan-hentai.net
            # x_manga_net     https://x-manga.net
            # x_manga_org     https://x-manga.org
    """
    parsers = _ADULT_PARSER_REGISTRY
    return sorted(
        (_describe_parser_class(cls) for cls in parsers.values()),
        key=lambda meta: meta["site_id"],
    )


def describe_adult_parsers() -> dict[str, Any]:
    """Retourne un résumé global du package (pour CLI et docs).

    Returns:
        Dict sérialisable JSON avec :
            - ``count`` (int) : nombre de parsers.
            - ``version`` (str) : version du package.
            - ``adult_only`` (bool) : toujours True.
            - ``by_language`` (dict[str, int]) : répartition par langue.
            - ``by_cloudflare_strategy`` (dict[str, int]) : répartition par stratégie.
            - ``parsers`` (list[dict]) : liste des parsers (voir ``list_adult_parsers``).
    """
    parsers = list_adult_parsers()

    by_language: dict[str, int] = {}
    by_cloudflare: dict[str, int] = {}
    for meta in parsers:
        by_language[meta["language"]] = by_language.get(meta["language"], 0) + 1
        by_cloudflare[meta["cloudflare_strategy"]] = (
            by_cloudflare.get(meta["cloudflare_strategy"], 0) + 1
        )

    return {
        "count": len(parsers),
        "version": __version__,
        "adult_only": __adult_only__,
        "by_language": by_language,
        "by_cloudflare_strategy": by_cloudflare,
        "parsers": parsers,
    }


def get_adult_parser_class(site_id: str) -> type[Any] | None:
    """Retourne la classe d'un parser adulte par son ``site_id``.

    Ne lève pas si le site est inconnu — retourne ``None``. Utile pour
    les appelants qui veulent tester la présence d'un parser sans
    capturer d'exception.

    Args:
        site_id: Identifiant du parser (ex: ``"nhentai"``).

    Returns:
        La classe du parser, ou ``None`` si non trouvé.

    Example:
        ::

            from nexusdl.parsers.adult import get_adult_parser_class

            cls = get_adult_parser_class("nhentai")
            if cls is not None:
                print(cls.base_url)  # https://nhentai.net
    """
    return _ADULT_PARSER_REGISTRY.get(site_id)


# ============================================================================
#  Registry interne des parsers
# ============================================================================

#: Mapping ``site_id → classe`` pour l'introspection et la résolution.
#: Ce dict est utilisé par les helpers ci-dessus. Il est **distinct** du
#: ``SiteRegistry`` du core (qui inclut également les parsers tout public).
#:
#: Important : les clés doivent correspondre exactement aux ``site_id``
#: déclarés dans chaque parser (ClassVar). Un test de non-régression
#: vérifie la cohérence.
_ADULT_PARSER_REGISTRY: Final[dict[str, type[Any]]] = {
    HentaiZoneParser.site_id: HentaiZoneParser,
    NHentaiParser.site_id: NHentaiParser,
    PururinParser.site_id: PururinParser,
    ScanHentaiParser.site_id: ScanHentaiParser,
    XMangaNetParser.site_id: XMangaNetParser,
    XMangaOrgParser.site_id: XMangaOrgParser,
}


def _describe_parser_class(cls: type[Any]) -> dict[str, Any]:
    """Extrait les métadonnées d'une classe de parser (pour introspection).

    Args:
        cls: Classe de parser (sous-classe de ``BaseParser``).

    Returns:
        Dict sérialisable JSON.
    """
    return {
        "site_id": getattr(cls, "site_id", ""),
        "language": getattr(cls, "language", ""),
        "adult": bool(getattr(cls, "adult", True)),
        "base_url": getattr(cls, "base_url", ""),
        "mirror_domains": list(getattr(cls, "mirror_domains", [])),
        "class_name": cls.__name__,
        "cloudflare_strategy": getattr(cls, "cloudflare_strategy", "unknown"),
        "content_rating": (
            getattr(cls, "content_rating").value
            if hasattr(cls, "content_rating") and hasattr(getattr(cls, "content_rating"), "value")
            else str(getattr(cls, "content_rating", "unknown"))
        ),
    }


# ============================================================================
#  Exports publics
# ============================================================================

__all__ = [
    # --- Parsers ---
    "HentaiZoneParser",
    "NHentaiParser",
    "PururinParser",
    "ScanHentaiParser",
    "XMangaNetParser",
    "XMangaOrgParser",
    # --- Helpers d'introspection ---
    "describe_adult_parsers",
    "get_adult_parser_class",
    "list_adult_parsers",
    # --- Métadonnées du package ---
    "__adult_only__",
    "__parser_count__",
    "__version__",
]
