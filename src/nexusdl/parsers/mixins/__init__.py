"""Mixins réutilisables pour les parseurs NexusDL.

Ce package regroupe l'ensemble des **mixins** que les parseurs peuvent
composer pour hériter de comportements transverses (rendu JS, Cloudflare,
FoolSlide, API REST, thèmes WordPress/Madara, MangaThemesia…) sans
dupliquer de code.

Chaque mixin est conçu pour être combiné à
:class:`~nexusdl.parsers.base.BaseParser` par héritage multiple, dans un
ordre précis (les mixins AVANT la classe de base) :

    >>> class SushiScanParser(FoolSlideMixin, CloudflareMixin, BaseParser):
    ...     site_id = "sushiscan_net"
    ...     language = Language.FR

Ordre d'héritage recommandé
---------------------------

Pour éviter tout conflit de MRO et garantir la bonne priorité des méthodes
surchargées par les mixins, l'ordre canonique est :

1. :class:`JsRenderedMixin` — primitives bas-niveau (rendu navigateur).
2. :class:`CloudflareMixin` — bypass CF (dépend du rendu ou de FS).
3. :class:`ApiBasedMixin` — client REST (indépendant).
4. Mixin de thème (:class:`FoolSlideMixin`, :class:`MadaraMixin`,
   :class:`MangaThemesiaMixin`) — implémentations `search`/`get_*`.
5. :class:`~nexusdl.parsers.base.BaseParser` — contrat final.

Mixins disponibles
------------------

* :class:`ApiBasedMixin` — client REST complet (auth, rate-limit, retry,
  cache TTL, pagination offset/page/cursor/link-header, mapping
  d'erreurs typées ``Api*Error``).
* :class:`CloudflareMixin` — détection fine des challenges (IUAM, JS,
  Managed, Turnstile, Block 1020/1006/1015) + bypass chaînable
  Playwright/FlareSolverr, cache et persistance du ``cf_clearance``.
* :class:`FoolSlideMixin` — implémentations par défaut pour les sites
  basés sur le CMS FoolSlide (Scan-Manga, SushiScan, JapScan…).
* :class:`JsRenderedMixin` — rendu Playwright complet, interception
  XHR/fetch, synchronisation bidirectionnelle des cookies httpx ⇄
  navigateur, screenshots de debug.
* :class:`MadaraMixin` — thème WordPress Madara (search / manga /
  chapitres / pages par sélecteurs CSS standards + pagination AJAX).
* :class:`MangaThemesiaMixin` — thème MangaThemesia (Asura, Flame,
  Luminous, etc.) exploitant les endpoints ``/api/...`` internes.

Import paresseux (PEP 562)
--------------------------

Les mixins dépendent de bibliothèques lourdes (``playwright``,
``selectolax``, ``httpx``, ``lxml``…). Pour ne pas pénaliser le temps
d'import du package ``nexusdl`` (et éviter les imports circulaires
pendant le bootstrapping), les symboles sont résolus **à la demande**
via :pep:`562` (``__getattr__`` au niveau module).

Conséquence : l'import suivant est quasi instantané et ne charge **pas**
Playwright ni selectolax tant qu'aucun mixin concret n'est instancié :

    >>> from nexusdl.parsers.mixins import CloudflareMixin  # OK

La dépendance lourde n'est tirée qu'au premier accès effectif à l'attribut.

Example:
    Combinaison typique multi-mixins :

    >>> class HentaiZoneParser(  # doctest: +SKIP
    ...     JsRenderedMixin,
    ...     CloudflareMixin,
    ...     ApiBasedMixin,
    ...     BaseParser,
    ... ):
    ...     site_id = "hentaizone"
    ...     language = Language.FR
    ...     api_base_url = "https://hentaizone.xyz/api"
    ...     cf_requires_js = True
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, Final

__all__ = [
    "ApiBasedMixin",
    "CloudflareMixin",
    "FoolSlideMixin",
    "JsRenderedMixin",
    "MadaraMixin",
    "MangaThemesiaMixin",
]


# ---------------------------------------------------------------------------
# Table de résolution : nom public → (module relatif, attribut réel)
# ---------------------------------------------------------------------------

_RESOLUTION_TABLE: Final[dict[str, tuple[str, str]]] = {
    "ApiBasedMixin": (".api_based", "ApiBasedMixin"),
    "CloudflareMixin": (".cloudflare", "CloudflareMixin"),
    "FoolSlideMixin": (".foolslide", "FoolSlideMixin"),
    "JsRenderedMixin": (".js_rendered", "JsRenderedMixin"),
    "MadaraMixin": (".wordpress_madara", "MadaraMixin"),
    "MangaThemesiaMixin": (".mangathemesia", "MangaThemesiaMixin"),
}


# ---------------------------------------------------------------------------
# Résolution paresseuse (PEP 562)
# ---------------------------------------------------------------------------


def __getattr__(name: str) -> Any:
    """Résout dynamiquement un mixin à la demande (PEP 562).

    Args:
        name: Nom du mixin demandé.

    Returns:
        La classe de mixin correspondante.

    Raises:
        AttributeError: Si le nom n'est pas un mixin connu du package.
    """
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
        Liste triée des mixins disponibles.
    """
    return sorted(__all__)


# ---------------------------------------------------------------------------
# Support du typage statique (mypy strict) et des IDE
# ---------------------------------------------------------------------------

if TYPE_CHECKING:  # pragma: no cover — uniquement pour les outils d'analyse
    from nexusdl.parsers.mixins.api_based import (
        ApiBasedMixin as ApiBasedMixin,
    )
    from nexusdl.parsers.mixins.cloudflare import (
        CloudflareMixin as CloudflareMixin,
    )
    from nexusdl.parsers.mixins.foolslide import (
        FoolSlideMixin as FoolSlideMixin,
    )
    from nexusdl.parsers.mixins.js_rendered import (
        JsRenderedMixin as JsRenderedMixin,
    )
    from nexusdl.parsers.mixins.mangathemesia import (
        MangaThemesiaMixin as MangaThemesiaMixin,
    )
    from nexusdl.parsers.mixins.wordpress_madara import (
        MadaraMixin as MadaraMixin,
    )
