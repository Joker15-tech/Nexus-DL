 """Parseurs francophones (French) pour NexusDL.

Ce package regroupe les **25 parseurs francophones** du projet NexusDL,
couvrant les principaux sites de scanlation de langue française (mangas,
manhwas, manhuas, webtoons, raws FR et contenus adultes).

Chaque parseur hérite de :class:`~nexusdl.parsers.base.BaseParser` et
combine un ou plusieurs mixins transverses issus de
:mod:`nexusdl.parsers.mixins` :

* :class:`JsRenderedMixin` — rendu Playwright complet.
* :class:`CloudflareMixin` — contournement Cloudflare.
* :class:`ApiBasedMixin` — client REST générique.
* :class:`MadaraMixin` — thème WordPress Madara.
* :class:`MangaThemesiaMixin` — thème MangaThemesia.
* :class:`FoolSlideMixin` — CMS PHP FoolSlide.

Ordre d'héritage canonique
--------------------------

Pour éviter les conflits de MRO et garantir la bonne priorité des méthodes,
l'ordre canonique est :

1. ``JsRenderedMixin`` (primitives bas-niveau)
2. ``CloudflareMixin`` (bypass CF)
3. ``ApiBasedMixin`` (client REST)
4. Mixin de thème (``MadaraMixin``, ``MangaThemesiaMixin``, ``FoolSlideMixin``)
5. ``BaseParser`` (contrat final)

Spécificités francophones
-------------------------

Les parseurs de ce namespace partagent plusieurs conventions linguistiques
implémentées de manière cohérente :

* **Numéros de chapitre** : support de ``Chapitre N``, ``Chapter N``,
  ``Ch. N``, ``Chap N``, ``Scan N``.
* **Numéros de tome** : support de ``Tome N``, ``Volume N``.
* **Statuts** : mapping des libellés français (``En cours``, ``Terminé``,
  ``Complet``, ``En pause``, ``Annulé``, ``Abandonné``) vers
  :class:`~nexusdl.core.models.manga.MangaStatus`.
* **Dates relatives** : parsing des formats français (``il y a 3 jours``,
  ``3 jours avant``).
* **Contenu adulte** : détection des marqueurs français (``érotique``,
  ``erotique``).
* **Locale / Timezone** : ``fr-FR`` et ``Europe/Paris`` par défaut pour
  un rendu cohérent avec l'audience francophone.

Catalogue des parseurs
----------------------

Catalogue actif (25), répartis par moteur :

**Madara (WordPress)** — 18 parseurs :

* :class:`XanaduScansParser` — Xanadu Scans.
* :class:`UranoScansParser` — Urano Scans.
* :class:`TaiseiScansParser` — Taisei Scans.
* :class:`SushiScanNetParser` — SushiScan.net (instance historique).
* :class:`SushiScanFrParser` — SushiScan.fr (instance sœur).
* :class:`ShinraScansParser` — Shinra Scans.
* :class:`RimuscanParser` — Rimuscan.
* :class:`RaijinScansParser` — Raijin Scans.
* :class:`PoseidonScansParser` — Poseidon Scans.
* :class:`PhenixScansParser` — Phenix Scans.
* :class:`KarmaScansParser` — Karma Scans.
* :class:`FanDubScansParser` — FanDub Scans.
* :class:`EpsilonScanParser` — Epsilon Scan.
* :class:`BlossomScansParser` — Blossom Scans.
* :class:`AnimeScansParser` — Anime Scans.
* :class:`OrtegascansFrParser` — Ortegascans FR.
* :class:`OrtegascansComParser` — Ortegascans COM (instance ES).
* :class:`CrunchyScanFrParser` — CrunchyScan FR.

**MangaThemesia** — 1 parseur :

* :class:`ToonFRParser` — ToonFR.

**FoolSlide (CMS PHP)** — 4 parseurs :

* :class:`ScanMangaParser` — Scan-Manga.
* :class:`MangasOriginesFrParser` — Mangas Origines FR (instance moderne).
* :class:`MangasOriginesParser` — Mangas Origines (instance historique).
* :class:`HentaiOriginesParser` — Hentai Origines (18+/NSFW).

**Genkan (CMS Laravel)** — 1 parseur :

* :class:`GenkanScansParser` — Genkan Scans.

**API nhentai** — 1 parseur :

* :class:`NekohouseParser` — Nekohouse (frontend nhentai, 18+/NSFW).

Import paresseux (PEP 562)
--------------------------

Les parseurs dépendent de bibliothèques lourdes (``playwright``,
``selectolax``, ``httpx``…) et de mixins eux-mêmes lourds. Pour ne pas
pénaliser le temps d'import du package ``nexusdl`` (et éviter les imports
circulaires pendant le bootstrapping), les symboles sont résolus **à la
demande** via :pep:`562` (``__getattr__`` au niveau module).

Cela signifie que l'import suivant est quasi instantané et ne charge **pas**
Playwright tant qu'aucun parseur concret n'est instancié :

    >>> from nexusdl.parsers.fr import SushiScanNetParser  # OK

Example:
    Résolution dynamique d'un parser par ``site_id`` :

    >>> from nexusdl.parsers.fr import get_parser_class
    >>> cls = get_parser_class("sushiscan_net")
    >>> cls.__name__
    'SushiScanNetParser'
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, Final

__all__ = [
    # --- Madara ---
    "AnimeScansParser",
    "BlossomScansParser",
    "CrunchyScanFrParser",
    "EpsilonScanParser",
    "FanDubScansParser",
    "KarmaScansParser",
    "OrtegascansComParser",
    "OrtegascansFrParser",
    "PhenixScansParser",
    "PoseidonScansParser",
    "RaijinScansParser",
    "RimuscanParser",
    "ShinraScansParser",
    "SushiScanFrParser",
    "SushiScanNetParser",
    "TaiseiScansParser",
    "UranoScansParser",
    "XanaduScansParser",
    # --- MangaThemesia ---
    "ToonFRParser",
    # --- FoolSlide ---
    "HentaiOriginesParser",
    "MangasOriginesFrParser",
    "MangasOriginesParser",
    "ScanMangaParser",
    # --- Genkan ---
    "GenkanScansParser",
    # --- nhentai API ---
    "NekohouseParser",
    # --- Utilitaires ---
    "get_parser_class",
    "list_parsers",
    "PARSER_REGISTRY",
]


# ---------------------------------------------------------------------------
# Table de résolution : nom public → (module relatif, attribut réel)
# ---------------------------------------------------------------------------

_RESOLUTION_TABLE: Final[dict[str, tuple[str, str]]] = {
    # --- Madara ---
    "AnimeScansParser": (".anime_scans", "AnimeScansParser"),
    "BlossomScansParser": (".blossom_scans", "BlossomScansParser"),
    "CrunchyScanFrParser": (".crunchyscan_fr", "CrunchyScanFrParser"),
    "EpsilonScanParser": (".epsilonscan", "EpsilonScanParser"),
    "FanDubScansParser": (".fandub_scans", "FanDubScansParser"),
    "KarmaScansParser": (".karma_scans", "KarmaScansParser"),
    "OrtegascansComParser": (".ortegascans_com", "OrtegascansComParser"),
    "OrtegascansFrParser": (".ortegascans_fr", "OrtegascansFrParser"),
    "PhenixScansParser": (".phenix_scans", "PhenixScansParser"),
    "PoseidonScansParser": (".poseidon_scans", "PoseidonScansParser"),
    "RaijinScansParser": (".raijin_scans", "RaijinScansParser"),
    "RimuscanParser": (".rimuscan", "RimuscanParser"),
    "ShinraScansParser": (".shinra_scans", "ShinraScansParser"),
    "SushiScanFrParser": (".sushiscan_fr", "SushiScanFrParser"),
    "SushiScanNetParser": (".sushiscan_net", "SushiScanNetParser"),
    "TaiseiScansParser": (".taisei_scans", "TaiseiScansParser"),
    "UranoScansParser": (".urano_scans", "UranoScansParser"),
    "XanaduScansParser": (".xanadu_scans", "XanaduScansParser"),
    # --- MangaThemesia ---
    "ToonFRParser": (".toonfr", "ToonFRParser"),
    # --- FoolSlide ---
    "HentaiOriginesParser": (".hentai_origines", "HentaiOriginesParser"),
    "MangasOriginesFrParser": (
        ".mangas_origines_fr",
        "MangasOriginesFrParser",
    ),
    "MangasOriginesParser": (".mangas_origines", "MangasOriginesParser"),
    "ScanMangaParser": (".scan_manga", "ScanMangaParser"),
    # --- Genkan ---
    "GenkanScansParser": (".genkan_scans", "GenkanScansParser"),
    # --- nhentai API ---
    "NekohouseParser": (".nekohouse", "NekohouseParser"),
}


# ---------------------------------------------------------------------------
# Registre public : site_id → nom de classe
# ---------------------------------------------------------------------------

PARSER_REGISTRY: Final[dict[str, str]] = {
    # --- Madara ---
    "anime_scans": "AnimeScansParser",
    "blossom_scans": "BlossomScansParser",
    "crunchyscan_fr": "CrunchyScanFrParser",
    "epsilonscan": "EpsilonScanParser",
    "fandub_scans": "FanDubScansParser",
    "karma_scans": "KarmaScansParser",
    "ortegascans_com": "OrtegascansComParser",
    "ortegascans_fr": "OrtegascansFrParser",
    "phenix_scans": "PhenixScansParser",
    "poseidon_scans": "PoseidonScansParser",
    "raijin_scans": "RaijinScansParser",
    "rimuscan": "RimuscanParser",
    "shinra_scans": "ShinraScansParser",
    "sushiscan_fr": "SushiScanFrParser",
    "sushiscan_net": "SushiScanNetParser",
    "taisei_scans": "TaiseiScansParser",
    "urano_scans": "UranoScansParser",
    "xanadu_scans": "XanaduScansParser",
    # --- MangaThemesia ---
    "toonfr": "ToonFRParser",
    # --- FoolSlide ---
    "hentai_origines": "HentaiOriginesParser",
    "mangas_origines_fr": "MangasOriginesFrParser",
    "mangas_origines": "MangasOriginesParser",
    "scan_manga": "ScanMangaParser",
    # --- Genkan ---
    "genkan_scans": "GenkanScansParser",
    # --- nhentai API ---
    "nekohouse": "NekohouseParser",
}


# ---------------------------------------------------------------------------
# Catégorisation par moteur (utile pour les scripts de validation)
# ---------------------------------------------------------------------------

PARSERS_BY_ENGINE: Final[dict[str, tuple[str, ...]]] = {
    "madara": (
        "anime_scans",
        "blossom_scans",
        "crunchyscan_fr",
        "epsilonscan",
        "fandub_scans",
        "karma_scans",
        "ortegascans_com",
        "ortegascans_fr",
        "phenix_scans",
        "poseidon_scans",
        "raijin_scans",
        "rimuscan",
        "shinra_scans",
        "sushiscan_fr",
        "sushiscan_net",
        "taisei_scans",
        "urano_scans",
        "xanadu_scans",
    ),
    "mangathemesia": ("toonfr",),
    "foolslide": (
        "hentai_origines",
        "mangas_origines",
        "mangas_origines_fr",
        "scan_manga",
    ),
    "genkan": ("genkan_scans",),
    "nhentai_api": ("nekohouse",),
}


# ---------------------------------------------------------------------------
# Parseurs 18+/NSFW (utile pour les filtres UI)
# ---------------------------------------------------------------------------

PARSERS_ADULT: Final[frozenset[str]] = frozenset(
    {
        "hentai_origines",
        "nekohouse",
        "crunchyscan_fr",
    }
)


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
        site_id: Identifiant interne du site (ex. ``"sushiscan_net"``).

    Returns:
        La classe de parser correspondante.

    Raises:
        KeyError: Si le ``site_id`` n'est pas enregistré.
        ImportError: Si le module du parser ne peut pas être importé.

    Example:
        >>> cls = get_parser_class("sushiscan_net")
        >>> cls.__name__
        'SushiScanNetParser'
    """
    class_name = PARSER_REGISTRY.get(site_id)
    if class_name is None:
        raise KeyError(
            f"Aucun parser FR enregistré pour site_id={site_id!r}. "
            f"IDs disponibles : {sorted(PARSER_REGISTRY)}"
        )
    return __getattr__(class_name)


def list_parsers(
    *,
    active_only: bool = False,
    include_adult: bool = True,
) -> list[str]:
    """Retourne la liste des ``site_id`` disponibles dans ce namespace.

    Args:
        active_only: Si ``True``, exclut les parsers obsolètes/archivés.
            Note : tous les parsers FR sont actuellement actifs, mais ce
            paramètre est conservé pour l'uniformité avec les autres
            namespaces et pour la compatibilité future.
        include_adult: Si ``False``, exclut les parsers 18+/NSFW
            (``hentai_origines``, ``nekohouse``, ``crunchyscan_fr``).

    Returns:
        Liste triée des ``site_id``.

    Example:
        >>> ids = list_parsers()
        >>> "sushiscan_net" in ids
        True
        >>> ids_no_adult = list_parsers(include_adult=False)
        >>> "hentai_origines" in ids_no_adult
        False
    """
    # Aucun parser FR n'est obsolète actuellement, mais la convention est
    # maintenue pour rester cohérent avec `nexusdl.parsers.en`.
    deprecated: set[str] = set()
    ids = set(PARSER_REGISTRY)

    if active_only:
        ids -= deprecated
    if not include_adult:
        ids -= PARSERS_ADULT

    return sorted(ids)


def list_parsers_by_engine(engine: str) -> list[str]:
    """Retourne les ``site_id`` d'un moteur donné.

    Args:
        engine: Nom du moteur (``"madara"``, ``"mangathemesia"``,
            ``"foolslide"``, ``"genkan"``, ``"nhentai_api"``).

    Returns:
        Liste triée des ``site_id`` associés à ce moteur.

    Raises:
        KeyError: Si le nom du moteur est inconnu.

    Example:
        >>> list_parsers_by_engine("foolslide")
        ['hentai_origines', 'mangas_origines', 'mangas_origines_fr', 'scan_manga']
    """
    if engine not in PARSERS_BY_ENGINE:
        raise KeyError(
            f"Moteur inconnu : {engine!r}. "
            f"Moteurs disponibles : {sorted(PARSERS_BY_ENGINE)}"
        )
    return sorted(PARSERS_BY_ENGINE[engine])


# ---------------------------------------------------------------------------
# Support du typage statique (mypy strict) et des IDE
# ---------------------------------------------------------------------------

if TYPE_CHECKING:  # pragma: no cover — uniquement pour les outils d'analyse
    # --- Madara ---
    from nexusdl.parsers.fr.anime_scans import (
        AnimeScansParser as AnimeScansParser,
    )
    from nexusdl.parsers.fr.blossom_scans import (
        BlossomScansParser as BlossomScansParser,
    )
    from nexusdl.parsers.fr.crunchyscan_fr import (
        CrunchyScanFrParser as CrunchyScanFrParser,
    )
    from nexusdl.parsers.fr.epsilonscan import (
        EpsilonScanParser as EpsilonScanParser,
    )
    from nexusdl.parsers.fr.fandub_scans import (
        FanDubScansParser as FanDubScansParser,
    )
    from nexusdl.parsers.fr.karma_scans import (
        KarmaScansParser as KarmaScansParser,
    )
    from nexusdl.parsers.fr.ortegascans_com import (
        OrtegascansComParser as OrtegascansComParser,
    )
    from nexusdl.parsers.fr.ortegascans_fr import (
        OrtegascansFrParser as OrtegascansFrParser,
    )
    from nexusdl.parsers.fr.phenix_scans import (
        PhenixScansParser as PhenixScansParser,
    )
    from nexusdl.parsers.fr.poseidon_scans import (
        PoseidonScansParser as PoseidonScansParser,
    )
    from nexusdl.parsers.fr.raijin_scans import (
        RaijinScansParser as RaijinScansParser,
    )
    from nexusdl.parsers.fr.rimuscan import (
        RimuscanParser as RimuscanParser,
    )
    from nexusdl.parsers.fr.shinra_scans import (
        ShinraScansParser as ShinraScansParser,
    )
    from nexusdl.parsers.fr.sushiscan_fr import (
        SushiScanFrParser as SushiScanFrParser,
    )
    from nexusdl.parsers.fr.sushiscan_net import (
        SushiScanNetParser as SushiScanNetParser,
    )
    from nexusdl.parsers.fr.taisei_scans import (
        TaiseiScansParser as TaiseiScansParser,
    )
    from nexusdl.parsers.fr.urano_scans import (
        UranoScansParser as UranoScansParser,
    )
    from nexusdl.parsers.fr.xanadu_scans import (
        XanaduScansParser as XanaduScansParser,
    )

    # --- MangaThemesia ---
    from nexusdl.parsers.fr.toonfr import ToonFRParser as ToonFRParser

    # --- FoolSlide ---
    from nexusdl.parsers.fr.hentai_origines import (
        HentaiOriginesParser as HentaiOriginesParser,
    )
    from nexusdl.parsers.fr.mangas_origines import (
        MangasOriginesParser as MangasOriginesParser,
    )
    from nexusdl.parsers.fr.mangas_origines_fr import (
        MangasOriginesFrParser as MangasOriginesFrParser,
    )
    from nexusdl.parsers.fr.scan_manga import (
        ScanMangaParser as ScanMangaParser,
    )

    # --- Genkan ---
    from nexusdl.parsers.fr.genkan_scans import (
        GenkanScansParser as GenkanScansParser,
    )

    # --- nhentai API ---
    from nexusdl.parsers.fr.nekohouse import (
        NekohouseParser as NekohouseParser,
    )
