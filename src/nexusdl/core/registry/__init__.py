"""Module public du registre des sites NexusDL.

Ce module constitue le point d'entrée de la couche de registre dans
l'architecture hexagonale. Il expose l'API publique stable utilisée par
le `DownloadWorker` et les interfaces pour accéder aux configurations
des sites supportés, charger les fichiers YAML, valider les documents,
et résoudre les parsers de manière lazy.

Pipeline de chargement :
    1. ConfigLoader.load()
        ├── Lit sites.yaml (embarqué)
        ├── Lit sites_overrides.yaml (utilisateur, optionnel)
        ├── Fusionne les deux configs (deep merge)
        └── Retourne le document fusionné

    2. SchemaValidator.validate_document(document)
        ├── Charge sites_schema.json (cache)
        ├── Valide via Draft202012Validator
        ├── Détecte les doublons (IDs, domaines)
        ├── Vérifie les références de parsers
        └── Retourne ValidationResult

    3. SiteRegistry.start()
        ├── Parse le document en SiteConfig[]
        ├── Construit les index (par ID, langue, tag, domaine)
        ├── Précharge les parsers référencés (lazy loading)
        └── Expose l'API publique (get_site, get_parser, etc.)

Architecture :
    ConfigLoader (charge YAML + overrides + fusion)
        │
        ▼ document fusionné
    SchemaValidator (valide contre JSON Schema)
        │
        ▼ ValidationResult
    SiteRegistry (parse, indexe, cache, résout parsers)
        │
        ├── Index multi-critères (O(1) lookup)
        ├── Cache de parsers (lazy loading via importlib)
        └── API publique (get_site, get_parser, reload, etc.)

Règles d'or :
    1. Ce fichier n'expose QUE les symboles publics stables.
    2. Il ne dépend d'aucun module de `interfaces/` ni de `parsers/`.
    3. Tous les exports sont typés strictement et documentés.
    4. Les imports sont organisés par sous-module pour la lisibilité.
    5. Le registre est le SEUL point d'accès aux configurations de sites.
    6. La validation est OBLIGATOIRE au chargement (pas de config invalide).
    7. Les parsers sont chargés à la demande (lazy loading).

Exemple d'utilisation — Workflow complet :
    >>> from pathlib import Path
    >>> from nexusdl.core.registry import (
    ...     ConfigLoader,
    ...     SchemaValidator,
    ...     SiteRegistry,
    ...     ValidationMode,
    ... )
    >>> from nexusdl.core.events import EventBus
    >>>
    >>> # 1. Créer les composants
    >>> loader = ConfigLoader()
    >>> validator = SchemaValidator(mode=ValidationMode.STRICT)
    >>> event_bus = EventBus()
    >>> registry = SiteRegistry(
    ...     loader=loader,
    ...     validator=validator,
    ...     event_bus=event_bus,
    ... )
    >>>
    >>> # 2. Démarrer le registre (charge et valide automatiquement)
    >>> await registry.start()
    >>>
    >>> # 3. Accéder aux sites
    >>> site = registry.get_site("mangadex")
    >>> print(f"{site.name} - {site.language.label}")
    >>>
    >>> # 4. Résoudre un parser (lazy loading)
    >>> parser = await registry.get_parser("mangadex")
    >>> results = await parser.search("one piece")
    >>>
    >>> # 5. Recharger à chaud (si config modifiée)
    >>> if await loader.has_config_changed():
    ...     await registry.reload()
    >>>
    >>> await registry.stop()

Exemple d'utilisation — Validation standalone :
    >>> from nexusdl.core.registry import (
    ...     validate_sites_file_quick,
    ...     format_validation_report,
    ... )
    >>>
    >>> result = await validate_sites_file_quick(
    ...     Path("src/nexusdl/core/registry/sites.yaml")
    >>> )
    >>> if not result.is_valid:
    ...     print(format_validation_report(result))
"""

from __future__ import annotations

# ============================================================================
# EXCEPTIONS — Hiérarchie complète
# ============================================================================

# loader.py
from nexusdl.core.registry.loader import (
    BaseConfigNotFoundError,
    LoaderError,
    LoaderNotStartedError,
    MergeError,
    OverridesFileError,
)
# site_registry.py
from nexusdl.core.registry.site_registry import (
    ConfigurationLoadError,
    ConfigurationValidationError,
    ParserInstantiationError,
    ParserLoadError,
    RegistryError,
    RegistryNotStartedError,
    SiteNotFoundError,
)
# validator.py
from nexusdl.core.registry.validator import (
    InvalidDocumentError,
    RegistryValidationError,
    SchemaLoadError,
    ValidatorNotStartedError,
)

# ============================================================================
# ENUMS — États, modes et classifications
# ============================================================================

from nexusdl.core.registry.loader import LoaderState
from nexusdl.core.registry.site_registry import RegistryState
from nexusdl.core.registry.validator import (
    ValidationCategory,
    ValidationMode,
    ValidationSeverity,
)

# ============================================================================
# MODÈLES PYDANTIC — Configurations et résultats
# ============================================================================

from nexusdl.core.registry.loader import LoaderConfig, LoaderStats
from nexusdl.core.registry.site_registry import ParserInfo, RegistryStats
from nexusdl.core.registry.validator import (
    ValidationIssue,
    ValidationResult,
    ValidationStats,
)

# ============================================================================
# CLASSES PRINCIPALES — Orchestration et accès données
# ============================================================================

from nexusdl.core.registry.loader import ConfigLoader
from nexusdl.core.registry.site_registry import SiteRegistry
from nexusdl.core.registry.validator import SchemaValidator

# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================

from nexusdl.core.registry.loader import (
    get_default_overrides_path,
    get_embedded_base_config_path,
    load_sites_config_quick,
)
from nexusdl.core.registry.validator import (
    format_validation_report,
    validate_site_config_quick,
    validate_sites_file_quick,
)

# ============================================================================
# EXPORTS PUBLICS
# ============================================================================

__all__ = [
    # === Classes principales ===
    "ConfigLoader",
    "SchemaValidator",
    "SiteRegistry",
    # === Enums — États et modes ===
    "LoaderState",
    "RegistryState",
    "ValidationMode",
    "ValidationSeverity",
    "ValidationCategory",
    # === Modèles — Configuration ===
    "LoaderConfig",
    # === Modèles — Résultats ===
    "LoaderStats",
    "RegistryStats",
    "ValidationStats",
    "ValidationResult",
    "ValidationIssue",
    "ParserInfo",
    # === Helpers — Loader ===
    "load_sites_config_quick",
    "get_default_overrides_path",
    "get_embedded_base_config_path",
    # === Helpers — Validator ===
    "validate_site_config_quick",
    "validate_sites_file_quick",
    "format_validation_report",
    # === Exceptions — Loader ===
    "LoaderError",
    "LoaderNotStartedError",
    "BaseConfigNotFoundError",
    "OverridesFileError",
    "MergeError",
    # === Exceptions — Registry ===
    "RegistryError",
    "RegistryNotStartedError",
    "SiteNotFoundError",
    "ParserLoadError",
    "ParserInstantiationError",
    "ConfigurationLoadError",
    "ConfigurationValidationError",
    # === Exceptions — Validator ===
    "RegistryValidationError",
    "SchemaLoadError",
    "ValidatorNotStartedError",
    "InvalidDocumentError",
]

__version__: str = "0.1.0"
