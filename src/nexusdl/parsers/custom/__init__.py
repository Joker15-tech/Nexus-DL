"""Parsers custom NexusDL — point d'entrée du package utilisateur.

Ce package contient le **template officiel** pour créer des parsers
personnalisés, ainsi que la **documentation locale** du dossier. Il ne
contient **aucun parser utilisateur** — ceux-ci doivent vivre dans
``{config_dir}/parsers_custom/`` (chemin XDG, hors du package).

Contenu du package
==================

    - ``_template.py`` : squelette fonctionnel d'un parser custom.
    - ``nexus.dl``     : documentation locale du dossier (YAML).
    - ``__init__.py``  : ce fichier — marqueur + introspection minimale.

Politique de chargement
=======================

Ce package est **inerte** : un simple ``import nexusdl.parsers.custom``
ne déclenche **aucune** action, aucun side-effect, aucune instanciation.
Le module ``_template`` n'est chargé qu'à la **première** accessibilité
de ``TemplateParser`` (import paresseux via PEP 562 ``__getattr__``).

Conséquences :

    - Aucun coût d'import pour les utilisateurs qui n'utilisent pas
      les parsers custom.
    - ``import nexusdl.parsers.custom`` ne charge ni ``selectolax``,
      ni ``playwright``, ni aucun parser utilisateur.
    - Le template n'est chargé que si explicitement demandé.

Usage programmatique
===================

Accès au template pour copie ou introspection ::

    from nexusdl.parsers.custom import TemplateParser, get_template_path

    # Chemin du template (pour copie)
    src = get_template_path()
    print(src)  # .../parsers/custom/_template.py

    # Classe du template (pour inspection)
    print(TemplateParser.__name__)  # "TemplateParser"

Introspection du package ::

    from nexusdl.parsers.custom import describe_package

    meta = describe_package()
    print(meta["version"], meta["template_available"])
    # 0.1.0 True

Workflow utilisateur
====================

    1. Copier le template ::

        cp $(python -c "from nexusdl.parsers.custom import get_template_path; \\
                        print(get_template_path())") \\
           ~/.config/nexusdl/parsers_custom/mon_site.py

    2. Éditer ``mon_site.py`` (site_id, language, base_url, méthodes).

    3. Enregistrer dans ``~/.config/nexusdl/sites_overrides.yaml``.

    4. Ajouter le dossier au PYTHONPATH.

    5. Valider : ``nexusdl sites validate mon_site``.

Sécurité
========

Un parser custom a un **accès complet** au core NexusDL. Il peut :

    - lire/écrire n'importe quel fichier accessible,
    - faire des requêtes réseau arbitraires,
    - accéder aux cookies, à la bibliothèque, à la config,
    - importer n'importe quel module Python.

C'est le même niveau de confiance que le code officiel. Pour un
comportement **sandboxé** avec permissions déclarées, utiliser le
système de plugins (``nexusdl.plugins``). Voir
``src/nexusdl/parsers/custom/nexus.dl`` pour un comparatif.

See Also:
    - ``nexusdl.parsers.base`` : ABC commune à tous les parsers.
    - ``nexusdl.parsers.custom._template`` : template de parser.
    - ``nexusdl.parsers.custom.nexus.dl`` : documentation du dossier.
    - ``docs/source/development/adding_parsers.md`` : guide complet.
    - ``docs/source/development/plugins.md`` : alternative sandboxée.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from nexusdl.parsers.base import BaseParser

# ============================================================================
#  MÉTADONNÉES DU PACKAGE
# ============================================================================

#: Version du package des parsers custom — alignée sur la version NexusDL.
__version__: Final[str] = "0.1.0"

#: Indique que ce package n'est **pas** adulte par nature (neutre).
#: Un parser custom peut définir ``adult = True`` individuellement.
__adult_only__: Final[bool] = False

#: Indique qu'un template est disponible pour copie.
__template_available__: Final[bool] = True

#: Nom du module contenant le template.
_TEMPLATE_MODULE: Final[str] = "_template"

#: Nom du fichier contenant le template (dans ce package).
_TEMPLATE_FILENAME: Final[str] = "_template.py"


# ============================================================================
#  IMPORTS PARESSEUX (PEP 562)
# ============================================================================
# ``_template.py`` n'est PAS importé au chargement du package. Il est
# chargé uniquement si quelqu'un accède à ``TemplateParser`` via
# l'attribut de module. Cela évite de charger les dépendances du template
# (selectolax, loguru, etc.) pour un simple ``import nexusdl.parsers.custom``.
#
# Le mapping ci-dessous liste les attributs disponibles en lazy-load.
# Ajouter une entrée ici pour chaque symbole réexporté depuis un module
# interne (utile si on ajoute d'autres templates à l'avenir).

_LAZY_ATTRS: Final[dict[str, tuple[str, str]]] = {
    # attr_name → (module_relatif, nom_du_symbole)
    "TemplateParser": (_TEMPLATE_MODULE, "TemplateParser"),
}


def __getattr__(name: str) -> Any:
    """Point d'entrée pour l'import paresseux (PEP 562).

    Appelé par Python lorsqu'un attribut du module n'est pas trouvé dans
    les globals. Si l'attribut est dans ``_LAZY_ATTRS``, le module cible
    est importé à la volée et le symbole retourné. Sinon, ``AttributeError``.

    Args:
        name: Nom de l'attribut demandé (ex: ``"TemplateParser"``).

    Returns:
        Le symbole demandé.

    Raises:
        AttributeError: Si ``name`` n'est pas un attribut connu.

    Example:
        ::

            import nexusdl.parsers.custom as pkg
            # À ce stade, _template.py n'est PAS chargé.

            cls = pkg.TemplateParser  # Ici, _template.py est importé.
            print(cls.__name__)  # "TemplateParser"
    """
    if name in _LAZY_ATTRS:
        module_name, symbol_name = _LAZY_ATTRS[name]
        import importlib  # noqa: PLC0415

        module = importlib.import_module(
            f"{__name__}.{module_name}",
        )
        value = getattr(module, symbol_name)
        # Met en cache pour éviter de réimporter au prochain accès
        globals()[name] = value
        return value

    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def __dir__() -> list[str]:
    """Augmente ``dir()`` avec les attributs en import paresseux.

    Sans cette méthode, ``dir(nexusdl.parsers.custom)`` ne listerait pas
    ``TemplateParser`` (car il n'est pas dans les globals tant qu'il n'a
    pas été accédé). Utile pour la complétion IDE et l'introspection.

    Returns:
        Liste triée des attributs publics.
    """
    return sorted(set(globals()) | set(_LAZY_ATTRS))


# ============================================================================
#  HELPERS D'INTROSPECTION
# ============================================================================


def get_template_path() -> Path:
    """Retourne le chemin absolu du fichier ``_template.py``.

    Utile pour la commande ``nexusdl sites scaffold`` et pour un
    utilisateur qui veut copier le template manuellement.

    Returns:
        Chemin absolu du fichier de template.

    Example:
        ::

            from nexusdl.parsers.custom import get_template_path
            from shutil import copyfile

            copyfile(
                get_template_path(),
                Path.home() / ".config/nexusdl/parsers_custom/mon_site.py",
            )
    """
    return Path(__file__).resolve().parent / _TEMPLATE_FILENAME


def is_template_available() -> bool:
    """Vérifie que le fichier de template existe sur le disque.

    Un utilisateur qui aurait supprimé accidentellement ``_template.py``
    verrait cette fonction retourner ``False`` — utile pour diagnostiquer
    une installation cassée.

    Returns:
        True si ``_template.py`` est présent et lisible.
    """
    template_path = get_template_path()
    return template_path.is_file() and template_path.stat().st_size > 0


def get_template_class() -> type[BaseParser]:
    """Retourne la classe ``TemplateParser`` (import paresseux).

    Équivalent à ``from nexusdl.parsers.custom import TemplateParser``,
    mais explicite et typé. Utile pour les tests et l'introspection
    programmatique.

    Returns:
        La classe ``TemplateParser``.

    Raises:
        ImportError: Si le template ne peut pas être importé.

    Example:
        ::

            from nexusdl.parsers.custom import get_template_class

            cls = get_template_class()
            print(cls.site_id)  # "template_site"
            print(cls.language)  # "en"
    """
    from nexusdl.parsers.custom._template import TemplateParser  # noqa: PLC0415

    return TemplateParser


def describe_package() -> dict[str, Any]:
    """Retourne un résumé du package pour l'introspection.

    Utilisé par la CLI (``nexusdl sites info --package custom``) et par
    les tests. Évite de dupliquer la logique de résumé.

    Returns:
        Dict sérialisable JSON avec :
            - ``version`` (str)
            - ``adult_only`` (bool)
            - ``template_available`` (bool)
            - ``template_path`` (str)
            - ``template_module`` (str)
            - ``has_user_parsers`` (bool) — True si des fichiers ``.py``
              non préfixés ``_`` existent dans le dossier (avertissement).

    Example:
        ::

            from nexusdl.parsers.custom import describe_package

            meta = describe_package()
            assert meta["template_available"] is True
            assert meta["adult_only"] is False
    """
    template_path = get_template_path()

    # Détecte des fichiers utilisateur non préfixés ``_`` (anti-pattern)
    user_files: list[str] = []
    package_dir = Path(__file__).resolve().parent
    for py_file in package_dir.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        user_files.append(py_file.name)

    return {
        "version": __version__,
        "adult_only": __adult_only__,
        "template_available": is_template_available(),
        "template_path": str(template_path),
        "template_module": f"{__name__}.{_TEMPLATE_MODULE}",
        "has_user_parsers": bool(user_files),
        "user_files": sorted(user_files),
    }


# ============================================================================
#  EXPORTS PUBLICS
# ============================================================================
# Note : ``TemplateParser`` n'est PAS listé dans les globals tant qu'il
# n'a pas été accédé. Mais il EST dans ``__all__`` (et donc accessible
# via ``from nexusdl.parsers.custom import *``) grâce à ``__getattr__``.

__all__ = [
    # --- Classe template (import paresseux) ---
    "TemplateParser",
    # --- Helpers d'introspection ---
    "describe_package",
    "get_template_class",
    "get_template_path",
    "is_template_available",
    # --- Métadonnées du package ---
    "__adult_only__",
    "__template_available__",
    "__version__",
]
