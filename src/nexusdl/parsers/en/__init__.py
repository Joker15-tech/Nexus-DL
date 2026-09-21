"""Parseurs anglophones (English) pour NexusDL.

Ce package regroupe les **21 parseurs** anglophones du projet NexusDL,
couvrant les principaux sites de scanlation de langue anglaise (mangas,
manhwas, manhuas, webtoons et doujinshis).

Chaque parseur hérite de :class:`~nexusdl.parsers.base.BaseParser` et
combine un ou plusieurs mixins transverses issus de
:mod:`nexusdl.parsers.mixins` :

* :class:`JsRenderedMixin` — rendu Playwright complet.
* :class:`CloudflareMixin` — contournement Cloudflare.
* :class:`ApiBasedMixin` — client REST générique.
* :class:`MadaraMixin` — thème WordPress Madara.
* :class:`MangaThemesiaMixin` — thème MangaThemesia.

Ordre d'héritage canonique
--------------------------

Pour éviter les conflits de MRO et garantir la bonne priorité des méthodes,
l'ordre canonique est :

1. ``JsRenderedMixin`` (primitives bas-niveau)
2. ``CloudflareMixin`` (bypass CF)
3. ``ApiBasedMixin`` (client REST)
4. Mixin de thème (``MadaraMixin``, ``MangaThemesiaMixin``, …)
5. ``BaseParser`` (contrat final)

Parseurs disponibles
--------------------

Catalogue actif (15) :

* :class:`AsuraScansParser` — Asura Scans (manhwa/manhua).
* :class:`BatotoParser` — Batoto (mangas multi-langues).
* :class:`CrunchyScanParser` — CrunchyScan (scanlation FR).
* :class:`FlameScansParser` — Flame Comics (manhwa/manhua).
* :class:`GalaxyScansParser` — GD Scans (manhwa/manhua).
* :class:`KireiCakeParser` — Kirei Cake (yuri/shoujo-ai).
* :class:`LuminousScansParser` — Luminous Comics (manhwa).
* :class:`MangaDexParser` — MangaDex (API officielle).
* :class:`MangaFireParser` — MangaFire (agrégateur multi-langue).
* :class:`MangakakalotParser` — Mangakakalot (agrégateur).
* :class:`MangaSee123Parser` — MangaSee123 (archive de scans officiels).
* :class:`ReaperScansParser` — Reaper Scans (manhwa/manhua).
* :class:`TCBScansParser` — TCB Scans (Shōnen Jump).
* :class:`ToonGodParser` — ToonGod (manhwa adulte).
* :class:`ToonilyParser` — Toonily (manhwa/manhua).
* :class:`VoidScansParser` — Void Scans (manhwa/manhua).
* :class:`ZenithScansParser` — Zenith Scans (manhwa/manhua turc).

Catalogue obsolète (4) :

* :class:`DisasterScansParser` — site inactif depuis 2024.
* :class:`LeviatanScansParser` — site fermé en 2024.
* :class:`ScyllaScansParser` — site fermé en 2025.
* :class:`ComickParser` — service fermé en 2025.

Import paresseux (PEP 562)
--------------------------

Les parseurs dépendent de bibliothèques lourdes (``playwright``,
``selectolax``, ``httpx``…) et de mixins eux-mêmes lourds. Pour ne pas
pénaliser le temps d'import du package ``nexusdl`` (et éviter les imports
circulaires pendant le bootstrapping), les symboles sont résolus **à la
demande** via :pep:`562` (``__getattr__`` au niveau module).

Cela signifie que l'import suivant est quasi instantané et ne charge **pas**
Playwright tant qu'aucun parseur concret n'est instancié :

    >>> from nexusdl.parsers.en import MangaDexParser  # OK

Example:
    Résolution dynamique d'un parser par ``site_id`` :

    >>> from nexusdl.parsers.en import get_parser_class
    >>> cls = get_parser_class("mangadex")
    >>> cls.__name__
    'MangaDexParser'
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, Final

__all__ = [
    # Parseurs actifs
    "AsuraScansParser",
    "BatotoParser",
    "CrunchyScanParser",
    "FlameScansParser",
    "GalaxyScansParser",
    "KireiCakeParser",
    "LuminousScansParser",
    "MangaDexParser",
    "MangaFireParser",
    "MangakakalotParser",
    "MangaSee123Parser",
    "ReaperScansParser",
    "TCBScansParser",
    "ToonGodParser",
    "ToonilyParser",
    "VoidScansParser",
    "ZenithScansParser",
    # Parseurs obsolètes / archivés
    "ComickParser",
    "DisasterScansParser",
    "LeviatanScansParser",
    "ScyllaScansParser",
    # Utilitaires
    "get_parser_class",
    "list_parsers",
    "PARSER_REGISTRY",
]


# ---------------------------------------------------------------------------
# Table de résolution : nom public → (module relatif, attribut réel)
# ---------------------------------------------------------------------------

_RESOLUTION_TABLE: Final[dict[str, tuple[str, str]]] = {
    # --- Parseurs actifs ---
    "AsuraScansParser": (".asurascans", "AsuraScansParser"),
    "BatotoParser": (".batoto", "BatotoParser"),
    "CrunchyScanParser": (".crunchyscan_org", "CrunchyScanParser"),
    "FlameScansParser": (".flamescans", "FlameScansParser"),
    "GalaxyScansParser": (".galaxy_scans", "GalaxyScansParser"),
    "KireiCakeParser": (".kireicake", "KireiCakeParser"),
    "LuminousScansParser": (".luminousscans", "LuminousScansParser"),
    "MangaDexParser": (".mangadex", "MangaDexParser"),
    "MangaFireParser": (".mangafire", "MangaFireParser"),
    "MangakakalotParser": (".mangakakalot", "MangakakalotParser"),
    "MangaSee123Parser": (".mangasee123", "MangaSee123Parser"),
    "ReaperScansParser": (".reaperscans", "ReaperScansParser"),
    "TCBScansParser": (".tcbscans", "TCBScansParser"),
    "ToonGodParser": (".toongod", "ToonGodParser"),
    "ToonilyParser": (".toonily", "ToonilyParser"),
    "VoidScansParser": (".void_scans", "VoidScansParser"),
    "ZenithScansParser": (".zenith_scans", "ZenithScansParser"),
    # --- Parseurs obsolètes / archivés ---
    "ComickParser": (".comick", "ComickParser"),
    "DisasterScansParser": (".disasterscans", "DisasterScansParser"),
    "LeviatanScansParser": (".leviathanscans", "LeviatanScansParser"),
    "ScyllaScansParser": (".scyllascans", "ScyllaScansParser"),
}


# ---------------------------------------------------------------------------
# Registre public : site_id → nom de classe
# ---------------------------------------------------------------------------

PARSER_REGISTRY: Final[dict[str, str]] = {
    # --- Actifs ---
    "asurascans": "AsuraScansParser",
    "batoto": "BatotoParser",
    "crunchyscan_org": "CrunchyScanParser",
    "flamescans": "FlameScansParser",
    "galaxy_scans": "GalaxyScansParser",
    "kireicake": "KireiCakeParser",
    "luminousscans": "LuminousScansParser",
    "mangadex": "MangaDexParser",
    "mangafire": "MangaFireParser",
    "mangakakalot": "MangakakalotParser",
    "mangasee123": "MangaSee123Parser",
    "reaperscans": "ReaperScansParser",
    "tcbscans": "TCBScansParser",
    "toongod": "ToonGodParser",
    "toonily": "ToonilyParser",
    "void_scans": "VoidScansParser",
    "zenith_scans": "ZenithScansParser",
    # --- Obsolètes / archivés ---
    "comick": "ComickParser",
    "disasterscans": "DisasterScansParser",
    "leviathanscans": "LeviatanScansParser",
    "scyllascans": "ScyllaScansParser",
}


# ---------------------------------------------------------------------------
# Résolution paresseuse (PEP 562)
# ---------------------------------------------------------------------------


def __getattr__(name: str) -> Any:
    """Résout dynamiquement un parser à la demande (PEP 562).

    Args:
        name: Nom du parser demandé (classe, utilitaire ou registre).

    Returns:
        La classe de parser, l'utilitaire ou le registre correspondant.

    Raises:
        AttributeError: Si le nom n'est pas exporté par ce package.
    """
    # Cas spéciaux : attributs utilitaires déjà présents globalement.
    if name == "PARSER_REGISTRY":
        return PARSER_REGISTRY
    if name in ("get_parser_class", "list_parsers"):
        # Ces fonctions sont définies plus bas dans le module : on les
        # résout via le module courant (accès direct).
        return globals()[name]

    entry = _RESOLUTION_TABLE.get(name)
    if entry is None:
        raise AttributeError(
            f"module {__name__!r} n'a pas d'attribut {name!r}"
        )
    module_path, attribute = entry
    module = import_module(module_path, package=__name__)
    value = getattr(module, attribute)
    # Cache l'attribut résolu pour les accès suivants (perf + cohérence).
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Retourne la liste des symboles exposés (autocomplétion IDE).

    Returns:
        Liste triée des parseurs et utilitaires disponibles.
    """
    return sorted(__all__)


# ---------------------------------------------------------------------------
# Utilitaires publics
# ---------------------------------------------------------------------------


def get_parser_class(site_id: str) -> type[Any]:
    """Retourne la classe de parser pour un ``site_id`` donné.

    Args:
        site_id: Identifiant interne du site (ex. ``"mangadex"``).

    Returns:
        La classe de parser correspondante.

    Raises:
        KeyError: Si le ``site_id`` n'est pas enregistré.
        ImportError: Si le module du parser ne peut pas être importé.

    Example:
        >>> cls = get_parser_class("mangadex")
        >>> cls.__name__
        'MangaDexParser'
    """
    class_name = PARSER_REGISTRY.get(site_id)
    if class_name is None:
        raise KeyError(
            f"Aucun parser enregistré pour site_id={site_id!r}. "
            f"IDs disponibles : {sorted(PARSER_REGISTRY)}"
        )
    return __getattr__(class_name)


def list_parsers(*, active_only: bool = False) -> list[str]:
    """Retourne la liste des ``site_id`` disponibles.

    Args:
        active_only: Si ``True``, exclut les parsers obsolètes/archivés
            (Disaster Scans, Leviatan Scans, Scylla Scans, Comick).

    Returns:
        Liste triée des ``site_id``.

    Example:
        >>> ids = list_parsers(active_only=True)
        >>> "mangadex" in ids
        True
        >>> "comick" in ids
        False
    """
    deprecated = {"comick", "disasterscans", "leviathanscans", "scyllascans"}
    if active_only:
        return sorted(sid for sid in PARSER_REGISTRY if sid not in deprecated)
    return sorted(PARSER_REGISTRY)


# ---------------------------------------------------------------------------
# Support du typage statique (mypy strict) et des IDE
# ---------------------------------------------------------------------------

if TYPE_CHECKING:  # pragma: no cover — uniquement pour les outils d'analyse
    from nexusdl.parsers.en.asurascans import (
        AsuraScansParser as AsuraScansParser,
    )
    from nexusdl.parsers.en.batoto import BatotoParser as BatotoParser
    from nexusdl.parsers.en.comick import ComickParser as ComickParser
    from nexusdl.parsers.en.crunchyscan_org import (
        CrunchyScanParser as CrunchyScanParser,
    )
    from nexusdl.parsers.en.disasterscans import (
        DisasterScansParser as DisasterScansParser,
    )
    from nexusdl.parsers.en.flamescans import (
        FlameScansParser as FlameScansParser,
    )
    from nexusdl.parsers.en.galaxy_scans import (
        GalaxyScansParser as GalaxyScansParser,
    )
    from nexusdl.parsers.en.kireicake import KireiCakeParser as KireiCakeParser
    from nexusdl.parsers.en.leviathanscans import (
        LeviatanScansParser as LeviatanScansParser,
    )
    from nexusdl.parsers.en.luminousscans import (
        LuminousScansParser as LuminousScansParser,
    )
    from nexusdl.parsers.en.mangadex import MangaDexParser as MangaDexParser
    from nexusdl.parsers.en.mangafire import MangaFireParser as MangaFireParser
    from nexusdl.parsers.en.mangakakalot import (
        MangakakalotParser as MangakakalotParser,
    )
    from nexusdl.parsers.en.mangasee123 import (
        MangaSee123Parser as MangaSee123Parser,
    )
    from nexusdl.parsers.en.reaperscans import (
        ReaperScansParser as ReaperScansParser,
    )
    from nexusdl.parsers.en.scyllascans import (
        ScyllaScansParser as ScyllaScansParser,
    )
    from nexusdl.parsers.en.tcbscans import TCBScansParser as TCBScansParser
    from nexusdl.parsers.en.toongod import ToonGodParser as ToonGodParser
    from nexusdl.parsers.en.toonily import ToonilyParser as ToonilyParser
    from nexusdl.parsers.en.void_scans import VoidScansParser as VoidScansParser
    from nexusdl.parsers.en.zenith_scans import (
        ZenithScansParser as ZenithScansParser,
    )
