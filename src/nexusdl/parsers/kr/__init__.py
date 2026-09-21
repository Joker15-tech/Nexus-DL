"""Parseurs coréens (Korean) pour NexusDL.

Ce package regroupe les parseurs **coréens** du projet NexusDL, couvrant
les sites de manhwa/manhua coréens et les variantes coréennes de sites
internationaux.

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

Spécificités coréennes
----------------------

Les parseurs de ce namespace partagent plusieurs conventions linguistiques
implémentées de manière cohérente :

* **Numéros de chapitre** : support de ``Chapter N``, ``챕터 N``, ``N화``
  et ``N장``.
* **Numéros de tome** : support de ``Volume N`` et ``N권``.
* **Statuts** : mapping des libellés coréens (``연재중``, ``완결``,
  ``휴재``, ``중단``) vers :class:`~nexusdl.core.models.manga.MangaStatus`.
* **Dates relatives** : parsing des formats coréens (``3일 전``,
  ``2시간 전``) en plus des formats anglais.
* **Contenu adulte** : détection des marqueurs coréens (``성인``,
  ``야설``, ``19금``).
* **Locale / Timezone** : ``ko-KR`` et ``Asia/Seoul`` par défaut pour
  un rendu cohérent avec l'audience coréenne.

Parseurs disponibles
--------------------

Catalogue actif (3) :

* :class:`AsuraScansKrParser` — variante coréenne d'Asura Scans
  (manhwas coréens + support anglais).
* :class:`ManhwaClubParser` — ManhwaClub (manhwa/mangas anglophones, classé
  ici pour cohérence thématique manhwa).
* :class:`ManhwaRawParser` — ManhwaRaw (raws coréens non traduits).

Import paresseux (PEP 562)
--------------------------

Les parseurs dépendent de bibliothèques lourdes (``playwright``,
``selectolax``, ``httpx``…) et de mixins eux-mêmes lourds. Pour ne pas
pénaliser le temps d'import du package ``nexusdl`` (et éviter les imports
circulaires pendant le bootstrapping), les symboles sont résolus **à la
demande** via :pep:`562` (``__getattr__`` au niveau module).

Cela signifie que l'import suivant est quasi instantané et ne charge **pas**
Playwright tant qu'aucun parseur concret n'est instancié :

    >>> from nexusdl.parsers.kr import ManhwaRawParser  # OK

Example:
    Résolution dynamique d'un parser par ``site_id`` :

    >>> from nexusdl.parsers.kr import get_parser_class
    >>> cls = get_parser_class("manhwa_raw")
    >>> cls.__name__
    'ManhwaRawParser'
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, Final

__all__ = [
    # Parseurs coréens
    "AsuraScansKrParser",
    "ManhwaClubParser",
    "ManhwaRawParser",
    # Utilitaires
    "get_parser_class",
    "list_parsers",
    "PARSER_REGISTRY",
]


# ---------------------------------------------------------------------------
# Table de résolution : nom public → (module relatif, attribut réel)
# ---------------------------------------------------------------------------

_RESOLUTION_TABLE: Final[dict[str, tuple[str, str]]] = {
    "AsuraScansKrParser": (".asura_scans_kr", "AsuraScansKrParser"),
    "ManhwaClubParser": (".manhwaclub", "ManhwaClubParser"),
    "ManhwaRawParser": (".manhwa_raw", "ManhwaRawParser"),
}


# ---------------------------------------------------------------------------
# Registre public : site_id → nom de classe
# ---------------------------------------------------------------------------

PARSER_REGISTRY: Final[dict[str, str]] = {
    "asura_scans_kr": "AsuraScansKrParser",
    "manhwaclub": "ManhwaClubParser",
    "manhwa_raw": "ManhwaRawParser",
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
        site_id: Identifiant interne du site (ex. ``"manhwa_raw"``).

    Returns:
        La classe de parser correspondante.

    Raises:
        KeyError: Si le ``site_id`` n'est pas enregistré.
        ImportError: Si le module du parser ne peut pas être importé.

    Example:
        >>> cls = get_parser_class("manhwa_raw")
        >>> cls.__name__
        'ManhwaRawParser'
    """
    class_name = PARSER_REGISTRY.get(site_id)
    if class_name is None:
        raise KeyError(
            f"Aucun parser KR enregistré pour site_id={site_id!r}. "
            f"IDs disponibles : {sorted(PARSER_REGISTRY)}"
        )
    return __getattr__(class_name)


def list_parsers(*, active_only: bool = False) -> list[str]:
    """Retourne la liste des ``site_id`` disponibles dans ce namespace.

    Args:
        active_only: Si ``True``, exclut les parsers obsolètes/archivés.
            Note : tous les parsers KR sont actuellement actifs, mais ce
            paramètre est conservé pour l'uniformité avec les autres
            namespaces et pour la compatibilité future.

    Returns:
        Liste triée des ``site_id``.

    Example:
        >>> ids = list_parsers()
        >>> "manhwa_raw" in ids
        True
        >>> "asura_scans_kr" in ids
        True
    """
    # Aucun parser KR n'est obsolète actuellement, mais la convention est
    # maintenue pour rester cohérent avec `nexusdl.parsers.en`.
    deprecated: set[str] = set()
    if active_only:
        return sorted(
            sid for sid in PARSER_REGISTRY if sid not in deprecated
        )
    return sorted(PARSER_REGISTRY)


# ---------------------------------------------------------------------------
# Support du typage statique (mypy strict) et des IDE
# ---------------------------------------------------------------------------

if TYPE_CHECKING:  # pragma: no cover — uniquement pour les outils d'analyse
    from nexusdl.parsers.kr.asura_scans_kr import (
        AsuraScansKrParser as AsuraScansKrParser,
    )
    from nexusdl.parsers.kr.manhwa_raw import (
        ManhwaRawParser as ManhwaRawParser,
    )
    from nexusdl.parsers.kr.manhwaclub import (
        ManhwaClubParser as ManhwaClubParser,
    )
