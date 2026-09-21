"""Plugin d'exemple officiel NexusDL — référence pour développeurs.

Ce package contient le plugin d'exemple fonctionnel qui démontre
l'utilisation de tous les hooks et de tous les accesseurs de l'API
plugin NexusDL.

Il sert de :
    - Référence pédagogique : copier ce dossier pour créer un plugin.
    - Test d'intégration vivant : le loader le charge au démarrage.
    - Documentation exécutable : les exemples de la doc en sont extraits.

Pour créer votre propre plugin ::

    cp -r src/nexusdl/plugins/_example plugins/my_plugin
    # Éditer nexus.plugin.yaml et plugin.py
    python -m nexusdl plugins validate my_plugin

Ce package est chargé par défaut au démarrage de NexusDL. Pour le
désactiver en production, utiliser son identifiant de manifest
(`nexusdl_example`) ::

    # config/config.yaml
    plugins:
      disabled:
        - nexusdl_example

Voir :
    - src/nexusdl/plugins/_example/nexus.plugin.yaml (manifest)
    - src/nexusdl/plugins/_example/plugin.py (implémentation)
    - src/nexusdl/plugins/_example/nexus.dl (documentation locale)
    - docs/source/development/plugins.md (guide complet)
"""

from __future__ import annotations

from nexusdl.plugins._example.plugin import ExamplePlugin

__all__ = ["ExamplePlugin"]
