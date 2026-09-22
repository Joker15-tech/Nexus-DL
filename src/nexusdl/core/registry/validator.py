"""Validateur de configuration des sites via JSON Schema.

Ce module fournit un validateur robuste qui vérifie la conformité des fichiers
de configuration des sites (`sites.yaml` et `sites_overrides.yaml`) contre le
schéma JSON Schema officiel (draft 2020-12) défini dans `data/sites_schema.json`.

Fonctionnalités principales :
    - Validation complète d'un document sites.yaml
    - Validation partielle des overrides utilisateur (tolérance aux champs manquants)
    - Messages d'erreur clairs, contextualisés et localisables
    - Cache du schéma pour performances optimales
    - Statistiques de validation (compteur, durée, erreurs)
    - Support du mode strict (erreurs bloquantes) et permissif (warnings)
    - Détection des sites en doublon (même id)
    - Détection des parsers manquants (référence de classe invalide)
    - Validation croisée (références entre sites)

Architecture :
    SchemaValidator
        ├── ValidationMode (enum) : STRICT, PERMISSIVE, LENIENT
        ├── ValidationSeverity (enum) : ERROR, WARNING, INFO
        ├── ValidationIssue (Pydantic) : problème individuel
        ├── ValidationResult (Pydantic) : résultat complet
        ├── ValidationStats (Pydantic) : statistiques agrégées
        └── _SchemaCache (interne) : cache du schéma chargé

Backend :
    - `jsonschema.Draft202012Validator` pour la validation JSON Schema
    - `importlib.resources` pour charger le schéma embarqué
    - `pydantic` pour les modèles de résultat

Exemple d'utilisation :
    >>> validator = SchemaValidator()
    >>> await validator.start()
    >>>
    >>> # Validation complète d'un fichier sites.yaml
    >>> result = await validator.validate_sites_file(
    ...     Path("src/nexusdl/core/registry/sites.yaml")
    ... )
    >>> if result.is_valid:
    ...     print(f"✓ {result.sites_count} sites validés")
    >>> else:
    ...     for issue in result.issues:
    ...         print(f"✗ {issue.path}: {issue.message}")
    >>>
    >>> # Validation partielle d'un override utilisateur
    >>> override_result = await validator.validate_partial(
    ...     {"mangadex": {"enabled": False, "rate_limit_per_second": 5.0}}
    ... )
    >>>
    >>> # Validation d'un site individuel
    >>> site_result = await validator.validate_site_config(site_config_dict)
    >>>
    >>> await validator.stop()
"""

from __future__ import annotations

import asyncio
import importlib.resources
import re
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Final, Self

import orjson
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# EXCEPTIONS
# ============================================================================


class RegistryValidationError(NexusDLError):
    """Exception de base pour les erreurs de validation du registre."""


class SchemaLoadError(RegistryValidationError):
    """Exception levée lorsque le schéma JSON ne peut être chargé."""

    def __init__(self, reason: str = "") -> None:
        msg = "Impossible de charger le schéma JSON"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


class ValidatorNotStartedError(RegistryValidationError):
    """Exception levée lorsqu'on utilise le validateur avant start()."""

    def __init__(self) -> None:
        super().__init__(
            "SchemaValidator must be started before use. Call await validator.start()"
        )


class InvalidDocumentError(RegistryValidationError):
    """Exception levée lorsqu'un document à valider est invalide."""

    def __init__(self, reason: str = "") -> None:
        msg = "Document invalide"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class ValidationMode(str, Enum):
    """Mode de validation.

    STRICT     : Toutes les erreurs sont bloquantes (is_valid=False).
    PERMISSIVE : Les erreurs de format sont bloquantes, les warnings tolérés.
    LENIENT    : Seules les erreurs critiques sont bloquantes.
    """

    STRICT = "strict"
    PERMISSIVE = "permissive"
    LENIENT = "lenient"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ValidationMode.STRICT: "Strict",
            ValidationMode.PERMISSIVE: "Permissif",
            ValidationMode.LENIENT: "Tolérant",
        }[self]


class ValidationSeverity(str, Enum):
    """Sévérité d'un problème de validation.

    ERROR   : Erreur bloquante (le document est invalide).
    WARNING : Avertissement non-bloquant (le document est valide mais suspect).
    INFO    : Information (suggestion d'amélioration).
    """

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def icon(self) -> str:
        """Icône Unicode pour l'affichage."""
        return {
            ValidationSeverity.ERROR: "✗",
            ValidationSeverity.WARNING: "⚠",
            ValidationSeverity.INFO: "ℹ",
        }[self]

    @property
    def color(self) -> str:
        """Couleur ANSI pour l'affichage terminal."""
        return {
            ValidationSeverity.ERROR: "red",
            ValidationSeverity.WARNING: "yellow",
            ValidationSeverity.INFO: "blue",
        }[self]


class ValidationCategory(str, Enum):
    """Catégorie d'un problème de validation.

    Permet de regrouper les problèmes par type pour l'affichage et le filtrage.
    """

    SCHEMA = "schema"  # Erreur de schéma JSON Schema
    FORMAT = "format"  # Erreur de format (URL, email, etc.)
    TYPE = "type"  # Erreur de type (string vs int, etc.)
    REQUIRED = "required"  # Champ requis manquant
    ENUM = "enum"  # Valeur hors enum autorisé
    PATTERN = "pattern"  # Regex non matchée
    UNIQUENESS = "uniqueness"  # Doublon détecté
    REFERENCE = "reference"  # Référence invalide (parser_class, etc.)
    LOGIC = "logic"  # Erreur logique (incohérence entre champs)
    CUSTOM = "custom"  # Règle métier custom


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class ValidationIssue(BaseModel):
    """Problème individuel détecté lors de la validation.

    Représente une erreur, un warning ou une info avec son contexte complet
    pour permettre un affichage clair et une correction ciblée.
    """

    severity: ValidationSeverity = Field(
        ..., description="Sévérité du problème (ERROR, WARNING, INFO)."
    )
    category: ValidationCategory = Field(
        ..., description="Catégorie du problème."
    )
    path: str = Field(
        ...,
        description="Chemin JSON vers le problème (ex: 'sites.0.domains.2').",
    )
    message: str = Field(
        ...,
        description="Message d'erreur descriptif et actionnable.",
    )
    expected: Any = Field(
        default=None,
        description="Valeur attendue (pour debug).",
    )
    received: Any = Field(
        default=None,
        description="Valeur reçue (pour debug).",
    )
    schema_path: str = Field(
        default="",
        description="Chemin dans le schéma JSON qui a échoué.",
    )
    validator: str = Field(
        default="",
        description="Nom du validateur JSON Schema qui a échoué.",
    )
    site_id: str | None = Field(
        default=None,
        description="ID du site concerné (si applicable).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def is_blocking(self) -> bool:
        """Indique si le problème est bloquant (erreur)."""
        return self.severity == ValidationSeverity.ERROR

    @property
    def display_path(self) -> str:
        """Chemin formaté pour l'affichage (remplace les indices numériques)."""
        # Remplacer "sites.0" par "sites[0]" pour lisibilité
        path = self.path
        path = re.sub(r"\.(\d+)(?=\.|$)", r"[\1]", path)
        return path or "<root>"

    def format_for_display(self) -> str:
        """Formate le problème pour affichage terminal lisible.

        Returns:
            Chaîne formatée avec icône, chemin et message.
        """
        icon = self.severity.icon
        path = self.display_path
        return f"{icon} [{path}] {self.message}"


class ValidationResult(BaseModel):
    """Résultat complet d'une validation.

    Contient tous les problèmes détectés, le verdict global, et des
    métadonnées sur la validation (durée, nombre de sites, etc.).
    """

    is_valid: bool = Field(
        ...,
        description="True si le document est valide (pas d'erreurs bloquantes).",
    )
    issues: list[ValidationIssue] = Field(
        default_factory=list,
        description="Liste de tous les problèmes détectés.",
    )
    document_path: Path | None = Field(
        default=None,
        description="Chemin du document validé (si applicable).",
    )
    validation_mode: ValidationMode = Field(
        default=ValidationMode.STRICT,
        description="Mode de validation utilisé.",
    )
    duration_ms: float = Field(
        ...,
        ge=0.0,
        description="Durée de la validation en millisecondes.",
    )
    sites_count: int = Field(
        default=0,
        ge=0,
        description="Nombre de sites validés (pour sites.yaml).",
    )
    schema_version: str = Field(
        default="",
        description="Version du schéma utilisé pour la validation.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def errors_count(self) -> int:
        """Nombre d'erreurs bloquantes."""
        return sum(
            1 for i in self.issues if i.severity == ValidationSeverity.ERROR
        )

    @property
    def warnings_count(self) -> int:
        """Nombre de warnings non-bloquants."""
        return sum(
            1 for i in self.issues if i.severity == ValidationSeverity.WARNING
        )

    @property
    def info_count(self) -> int:
        """Nombre d'informations."""
        return sum(
            1 for i in self.issues if i.severity == ValidationSeverity.INFO
        )

    @property
    def errors_by_category(self) -> dict[ValidationCategory, int]:
        """Compteur d'erreurs par catégorie."""
        counts: dict[ValidationCategory, int] = {}
        for issue in self.issues:
            if issue.severity == ValidationSeverity.ERROR:
                counts[issue.category] = counts.get(issue.category, 0) + 1
        return counts

    @property
    def affected_sites(self) -> list[str]:
        """Liste des IDs de sites concernés par des erreurs."""
        sites: set[str] = set()
        for issue in self.issues:
            if issue.site_id is not None and issue.is_blocking:
                sites.add(issue.site_id)
        return sorted(sites)

    def filter_by_severity(
        self, severity: ValidationSeverity
    ) -> list[ValidationIssue]:
        """Filtre les problèmes par sévérité.

        Args:
            severity: Sévérité à filtrer.

        Returns:
            Liste des problèmes correspondants.
        """
        return [i for i in self.issues if i.severity == severity]

    def filter_by_category(
        self, category: ValidationCategory
    ) -> list[ValidationIssue]:
        """Filtre les problèmes par catégorie.

        Args:
            category: Catégorie à filtrer.

        Returns:
            Liste des problèmes correspondants.
        """
        return [i for i in self.issues if i.category == category]

    def format_summary(self) -> str:
        """Formate un résumé lisible du résultat.

        Returns:
            Chaîne de résumé pour affichage terminal.
        """
        if self.is_valid:
            status = "✓ Valide"
        else:
            status = "✗ Invalide"

        parts = [
            f"{status}",
            f"{self.sites_count} sites",
            f"{self.duration_ms:.1f}ms",
        ]

        if self.errors_count > 0:
            parts.append(f"{self.errors_count} erreurs")
        if self.warnings_count > 0:
            parts.append(f"{self.warnings_count} warnings")
        if self.info_count > 0:
            parts.append(f"{self.info_count} infos")

        return " · ".join(parts)


class ValidationStats(BaseModel):
    """Statistiques agrégées du validateur."""

    total_validations: int = Field(default=0, ge=0)
    successful_validations: int = Field(default=0, ge=0)
    failed_validations: int = Field(default=0, ge=0)
    total_issues_found: int = Field(default=0, ge=0)
    total_errors_found: int = Field(default=0, ge=0)
    total_warnings_found: int = Field(default=0, ge=0)
    total_duration_ms: float = Field(default=0.0, ge=0.0)
    issues_by_category: dict[str, int] = Field(
        default_factory=dict,
        description="Compteur d'issues par catégorie.",
    )
    schema_loaded_at: datetime | None = Field(
        default=None,
        description="Timestamp de chargement du schéma.",
    )
    uptime_seconds: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def success_rate(self) -> float:
        """Taux de succès des validations (0.0 à 1.0)."""
        if self.total_validations == 0:
            return 0.0
        return self.successful_validations / self.total_validations

    @property
    def average_duration_ms(self) -> float:
        """Durée moyenne d'une validation en millisecondes."""
        if self.total_validations == 0:
            return 0.0
        return self.total_duration_ms / self.total_validations


# ============================================================================
# HELPERS — Formatage des erreurs JSON Schema
# ============================================================================


# Mapping des validateurs JSON Schema vers des catégories NexusDL
_VALIDATOR_TO_CATEGORY: Final[dict[str, ValidationCategory]] = {
    "type": ValidationCategory.TYPE,
    "enum": ValidationCategory.ENUM,
    "pattern": ValidationCategory.PATTERN,
    "format": ValidationCategory.FORMAT,
    "required": ValidationCategory.REQUIRED,
    "uniqueItems": ValidationCategory.UNIQUENESS,
    "additionalProperties": ValidationCategory.SCHEMA,
    "minItems": ValidationCategory.SCHEMA,
    "maxItems": ValidationCategory.SCHEMA,
    "minimum": ValidationCategory.SCHEMA,
    "maximum": ValidationCategory.SCHEMA,
    "minLength": ValidationCategory.SCHEMA,
    "maxLength": ValidationCategory.SCHEMA,
    "$ref": ValidationCategory.REFERENCE,
}


def _path_to_string(path: Any) -> str:
    """Convertit un chemin JSON Schema en chaîne lisible.

    Args:
        path: Iterable de clés/indices du chemin.

    Returns:
        Chaîne au format 'sites.0.domains.2' ou '<root>' si vide.
    """
    parts: list[str] = []
    for element in path:
        if isinstance(element, int):
            parts.append(str(element))
        else:
            parts.append(str(element))
    return ".".join(parts) if parts else "<root>"


def _extract_site_id_from_path(path: str, document: Any) -> str | None:
    """Extrait l'ID d'un site depuis un chemin JSON.

    Args:
        path: Chemin JSON (ex: 'sites.0.domains.2').
        document: Document complet pour résolution.

    Returns:
        ID du site ou None si non applicable.
    """
    parts = path.split(".")
    if len(parts) >= 2 and parts[0] == "sites":
        try:
            index = int(parts[1])
            if isinstance(document, Mapping) and "sites" in document:
                sites = document["sites"]
                if isinstance(sites, list) and 0 <= index < len(sites):
                    site = sites[index]
                    if isinstance(site, Mapping) and "id" in site:
                        return str(site["id"])
        except (ValueError, TypeError, KeyError):
            pass
    return None


def _format_error_message(error: JsonSchemaValidationError) -> str:
    """Formate un message d'erreur JSON Schema en message lisible.

    Args:
        error: Erreur JSON Schema à formater.

    Returns:
        Message d'erreur formaté et actionnable.
    """
    validator = error.validator
    message = error.message

    # Messages spécifiques par type de validateur
    if validator == "type":
        expected_type = error.validator_value
        return f"Type invalide : attendu {expected_type}, reçu {type(error.instance).__name__}"

    if validator == "enum":
        allowed = error.validator_value
        if isinstance(allowed, list) and len(allowed) <= 10:
            return f"Valeur non autorisée : '{error.instance}'. Valeurs autorisées : {', '.join(repr(v) for v in allowed)}"
        return f"Valeur non autorisée : '{error.instance}'"

    if validator == "pattern":
        pattern = error.validator_value
        return f"La valeur '{error.instance}' ne correspond pas au motif attendu : {pattern}"

    if validator == "format":
        fmt = error.validator_value
        return f"Format invalide pour '{error.instance}' (attendu : {fmt})"

    if validator == "required":
        missing = error.validator_value
        if isinstance(missing, list):
            return f"Champs requis manquants : {', '.join(missing)}"
        return f"Champ requis manquant : {missing}"

    if validator == "uniqueItems":
        return "Doublon détecté : tous les éléments doivent être uniques"

    if validator == "additionalProperties":
        unexpected = error.message
        return f"Propriétés non autorisées détectées : {unexpected}"

    if validator in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        return f"Valeur hors limites : {message}"

    if validator in ("minLength", "maxLength"):
        return f"Longueur de chaîne invalide : {message}"

    if validator in ("minItems", "maxItems"):
        return f"Nombre d'éléments invalide : {message}"

    # Fallback : message original
    return message


def _categorize_error(error: JsonSchemaValidationError) -> ValidationCategory:
    """Catégorise une erreur JSON Schema.

    Args:
        error: Erreur à catégoriser.

    Returns:
        Catégorie déterminée.
    """
    validator = error.validator
    return _VALIDATOR_TO_CATEGORY.get(validator, ValidationCategory.SCHEMA)


def _determine_severity(
    error: JsonSchemaValidationError,
    mode: ValidationMode,
) -> ValidationSeverity:
    """Détermine la sévérité d'une erreur selon le mode de validation.

    Args:
        error: Erreur à évaluer.
        mode: Mode de validation actif.

    Returns:
        Sévérité déterminée.
    """
    # En mode STRICT, toutes les erreurs sont bloquantes
    if mode == ValidationMode.STRICT:
        return ValidationSeverity.ERROR

    # En mode PERMISSIVE, les erreurs de format/pattern sont des warnings
    if mode == ValidationMode.PERMISSIVE:
        if error.validator in ("format", "pattern"):
            return ValidationSeverity.WARNING

    # En mode LENIENT, plus de tolérance
    if mode == ValidationMode.LENIENT:
        if error.validator in ("format", "pattern", "additionalProperties"):
            return ValidationSeverity.WARNING
        if error.validator in ("minLength", "maxLength", "minItems", "maxItems"):
            return ValidationSeverity.INFO

    return ValidationSeverity.ERROR


# ============================================================================
# CLASSE PRINCIPALE — SchemaValidator
# ============================================================================


class SchemaValidator:
    """Validateur de configuration des sites via JSON Schema.

    Charge le schéma JSON officiel au démarrage et l'utilise pour valider
    les documents de configuration (sites.yaml, overrides, sites individuels).

    Lifecycle :
        >>> validator = SchemaValidator()
        >>> await validator.start()
        >>> result = await validator.validate_sites_file(path)
        >>> await validator.stop()

    Thread-safety :
        Cette classe est conçue pour être utilisée dans un seul event loop
        asyncio. Le schéma est chargé une seule fois et mis en cache.
    """

    # Chemin vers le schéma embarqué (relatif au package)
    _SCHEMA_PACKAGE: ClassVar[str] = "nexusdl.data"
    _SCHEMA_FILENAME: ClassVar[str] = "sites_schema.json"

    # Timeout pour le chargement du schéma
    _SCHEMA_LOAD_TIMEOUT: ClassVar[float] = 5.0

    def __init__(
        self,
        *,
        mode: ValidationMode = ValidationMode.STRICT,
        custom_schema_path: Path | None = None,
        fail_fast: bool = False,
    ) -> None:
        """Initialise le validateur.

        Args:
            mode: Mode de validation par défaut (STRICT, PERMISSIVE, LENIENT).
            custom_schema_path: Chemin vers un schéma JSON personnalisé
                                (défaut: schéma embarqué dans le package).
            fail_fast: Si True, arrête la validation à la première erreur.
        """
        self._mode = mode
        self._custom_schema_path = custom_schema_path
        self._fail_fast = fail_fast

        # État interne
        self._schema: dict[str, Any] | None = None
        self._validator: Draft202012Validator | None = None
        self._started: bool = False
        self._schema_loaded_at: datetime | None = None
        self._start_time: float = 0.0

        # Statistiques
        self._total_validations: int = 0
        self._successful_validations: int = 0
        self._failed_validations: int = 0
        self._total_issues_found: int = 0
        self._total_errors_found: int = 0
        self._total_warnings_found: int = 0
        self._total_duration_ms: float = 0.0
        self._issues_by_category: dict[str, int] = {}
        self._stats_lock = asyncio.Lock()

        self._logger = logger.bind(module="schema_validator")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    async def start(self) -> None:
        """Charge le schéma JSON et initialise le validateur.

        Raises:
            SchemaLoadError: Si le schéma ne peut être chargé ou parsé.
        """
        if self._started:
            self._logger.warning("SchemaValidator déjà démarré, ignore")
            return

        try:
            # Charger le schéma (custom ou embarqué)
            schema_data = await asyncio.wait_for(
                asyncio.to_thread(self._load_schema),
                timeout=self._SCHEMA_LOAD_TIMEOUT,
            )

            # Construire le validateur JSON Schema
            self._validator = Draft202012Validator(schema_data)
            self._schema = schema_data
            self._schema_loaded_at = datetime.now(UTC)
            self._started = True
            self._start_time = time.monotonic()

            schema_version = schema_data.get("version", "unknown")
            self._logger.info(
                "SchemaValidator démarré: mode={}, version={}, fail_fast={}",
                self._mode.value,
                schema_version,
                self._fail_fast,
            )

        except asyncio.TimeoutError as e:
            raise SchemaLoadError(
                f"Timeout lors du chargement du schéma ({self._SCHEMA_LOAD_TIMEOUT}s)"
            ) from e
        except Exception as e:
            raise SchemaLoadError(str(e)) from e

    async def stop(self) -> None:
        """Arrête le validateur et libère les ressources."""
        if not self._started:
            return

        self._validator = None
        self._schema = None
        self._started = False
        self._logger.info("SchemaValidator arrêté")

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------------

    @property
    def is_started(self) -> bool:
        """Indique si le validateur est démarré."""
        return self._started

    @property
    def mode(self) -> ValidationMode:
        """Mode de validation actuel."""
        return self._mode

    @property
    def schema_version(self) -> str:
        """Version du schéma chargé."""
        if self._schema is None:
            return ""
        return str(self._schema.get("version", ""))

    @property
    def schema_loaded_at(self) -> datetime | None:
        """Timestamp de chargement du schéma."""
        return self._schema_loaded_at

    # ------------------------------------------------------------------------
    # API publique — Validation complète
    # ------------------------------------------------------------------------

    async def validate_document(
        self,
        document: Any,
        *,
        mode: ValidationMode | None = None,
        document_path: Path | None = None,
    ) -> ValidationResult:
        """Valide un document complet contre le schéma.

        Args:
            document: Document à valider (dict, list, etc.).
            mode: Mode de validation (override du mode par défaut).
            document_path: Chemin du document (pour reporting).

        Returns:
            Résultat de la validation avec tous les problèmes détectés.

        Raises:
            ValidatorNotStartedError: Si le validateur n'est pas démarré.
            InvalidDocumentError: Si le document est None ou non-dict.
        """
        self._ensure_started()
        assert self._validator is not None

        if document is None:
            raise InvalidDocumentError("Le document ne peut pas être None")

        effective_mode = mode or self._mode
        start_time = time.perf_counter()

        # Collecter toutes les erreurs JSON Schema
        issues: list[ValidationIssue] = []
        try:
            errors = list(self._validator.iter_errors(document))
        except Exception as e:
            self._logger.error("Erreur lors de la validation: {}", e)
            raise InvalidDocumentError(f"Erreur de validation: {e}") from e

        # Convertir en ValidationIssue
        for error in errors:
            issue = self._error_to_issue(error, document, effective_mode)
            issues.append(issue)

            # Fail-fast : arrêter à la première erreur
            if self._fail_fast and issue.is_blocking:
                break

        # Validation croisée (règles métier)
        cross_issues = await self._validate_cross_references(document, effective_mode)
        issues.extend(cross_issues)

        # Calculer le verdict
        has_blocking = any(i.is_blocking for i in issues)
        is_valid = not has_blocking

        # Compter les sites
        sites_count = 0
        if isinstance(document, Mapping) and "sites" in document:
            sites = document["sites"]
            if isinstance(sites, list):
                sites_count = len(sites)

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        result = ValidationResult(
            is_valid=is_valid,
            issues=issues,
            document_path=document_path,
            validation_mode=effective_mode,
            duration_ms=duration_ms,
            sites_count=sites_count,
            schema_version=self.schema_version,
        )

        # Enregistrer les stats
        await self._record_validation(result)

        return result

    async def validate_sites_file(
        self,
        path: Path,
        *,
        mode: ValidationMode | None = None,
    ) -> ValidationResult:
        """Valide un fichier sites.yaml complet.

        Charge le fichier YAML, le valide contre le schéma, et retourne
        le résultat avec tous les problèmes détectés.

        Args:
            path: Chemin vers le fichier YAML.
            mode: Mode de validation (override).

        Returns:
            Résultat de la validation.

        Raises:
            ValidatorNotStartedError: Si le validateur n'est pas démarré.
            InvalidDocumentError: Si le fichier ne peut être lu ou parsé.
        """
        self._ensure_started()

        if not path.exists():
            raise InvalidDocumentError(f"Fichier inexistant: {path}")

        if not path.is_file():
            raise InvalidDocumentError(f"N'est pas un fichier: {path}")

        # Charger le YAML (dans un thread pour ne pas bloquer)
        try:
            document = await asyncio.to_thread(self._load_yaml, path)
        except Exception as e:
            raise InvalidDocumentError(f"Impossible de charger {path}: {e}") from e

        return await self.validate_document(
            document,
            mode=mode,
            document_path=path,
        )

    async def validate_site_config(
        self,
        site_config: Mapping[str, Any],
        *,
        mode: ValidationMode | None = None,
    ) -> ValidationResult:
        """Valide la configuration d'un site individuel.

        Utilise le sous-schéma `$defs/SiteConfig` du schéma principal.

        Args:
            site_config: Configuration du site à valider.
            mode: Mode de validation (override).

        Returns:
            Résultat de la validation.
        """
        self._ensure_started()
        assert self._schema is not None

        effective_mode = mode or self._mode
        start_time = time.perf_counter()

        # Extraire le sous-schéma SiteConfig
        site_schema = self._schema.get("$defs", {}).get("SiteConfig")
        if site_schema is None:
            raise SchemaLoadError("Sous-schéma SiteConfig introuvable")

        # Créer un validateur pour ce sous-schéma
        # (on doit résoudre les $ref manuellement)
        validator = Draft202012Validator(
            site_schema,
            resolver=self._validator._resolver if self._validator else None,  # type: ignore[attr-defined]
        )

        issues: list[ValidationIssue] = []
        for error in validator.iter_errors(dict(site_config)):
            issue = self._error_to_issue(error, site_config, effective_mode)
            issues.append(issue)

            if self._fail_fast and issue.is_blocking:
                break

        has_blocking = any(i.is_blocking for i in issues)
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        result = ValidationResult(
            is_valid=not has_blocking,
            issues=issues,
            validation_mode=effective_mode,
            duration_ms=duration_ms,
            sites_count=1,
            schema_version=self.schema_version,
        )

        await self._record_validation(result)
        return result

    # ------------------------------------------------------------------------
    # API publique — Validation partielle (overrides)
    # ------------------------------------------------------------------------

    async def validate_partial(
        self,
        overrides: Mapping[str, Any],
        *,
        mode: ValidationMode | None = None,
    ) -> ValidationResult:
        """Valide des overrides partiels de configuration.

        Les overrides sont des dictionnaires `{site_id: {fields...}}` où
        seuls les champs modifiés sont présents. Cette méthode applique
        une validation plus tolérante :
            - Les champs requis ne sont pas vérifiés
            - Seuls les types et formats des champs présents sont validés
            - Les IDs de sites sont vérifiés contre le schéma SiteId

        Args:
            overrides: Dictionnaire des overrides à valider.
            mode: Mode de validation (override).

        Returns:
            Résultat de la validation.
        """
        self._ensure_started()
        assert self._schema is not None

        effective_mode = mode or self._mode
        start_time = time.perf_counter()

        issues: list[ValidationIssue] = []

        # Vérifier que c'est bien un dict
        if not isinstance(overrides, Mapping):
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    category=ValidationCategory.TYPE,
                    path="<root>",
                    message=f"Overrides doit être un dictionnaire, reçu {type(overrides).__name__}",
                    expected="dict",
                    received=type(overrides).__name__,
                )
            )
        else:
            # Valider chaque site_id
            site_id_pattern = self._schema.get("$defs", {}).get("SiteId", {}).get("pattern")

            for site_id, site_overrides in overrides.items():
                # Valider le site_id
                if site_id_pattern:
                    if not re.match(site_id_pattern, site_id):
                        issues.append(
                            ValidationIssue(
                                severity=ValidationSeverity.ERROR,
                                category=ValidationCategory.PATTERN,
                                path=site_id,
                                message=f"ID de site invalide: '{site_id}' (doit matcher {site_id_pattern})",
                                expected=site_id_pattern,
                                received=site_id,
                                site_id=site_id,
                            )
                        )
                        continue

                # Valider les overrides du site
                if not isinstance(site_overrides, Mapping):
                    issues.append(
                        ValidationIssue(
                            severity=ValidationSeverity.ERROR,
                            category=ValidationCategory.TYPE,
                            path=site_id,
                            message=f"Overrides pour '{site_id}' doit être un dictionnaire",
                            expected="dict",
                            received=type(site_overrides).__name__,
                            site_id=site_id,
                        )
                    )
                    continue

                # Valider les champs individuels (règles de base)
                self._validate_override_fields(
                    site_id, site_overrides, issues, effective_mode
                )

        has_blocking = any(i.is_blocking for i in issues)
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        result = ValidationResult(
            is_valid=not has_blocking,
            issues=issues,
            validation_mode=effective_mode,
            duration_ms=duration_ms,
            sites_count=len(overrides) if isinstance(overrides, Mapping) else 0,
            schema_version=self.schema_version,
        )

        await self._record_validation(result)
        return result

    def _validate_override_fields(
        self,
        site_id: str,
        overrides: Mapping[str, Any],
        issues: list[ValidationIssue],
        mode: ValidationMode,
    ) -> None:
        """Valide les champs d'un override de site.

        Args:
            site_id: ID du site.
            overrides: Champs à valider.
            issues: Liste des issues à enrichir.
            mode: Mode de validation.
        """
        assert self._schema is not None

        # Types attendus pour les champs courants
        field_types: dict[str, tuple[type, ...]] = {
            "enabled": (bool,),
            "priority": (int,),
            "name": (str,),
            "rate_limit_per_second": (int, float),
            "max_concurrent_downloads": (int,),
            "min_delay_between_requests": (int, float),
        }

        for field_name, value in overrides.items():
            expected_types = field_types.get(field_name)
            if expected_types is not None:
                if not isinstance(value, expected_types):
                    issues.append(
                        ValidationIssue(
                            severity=ValidationSeverity.ERROR,
                            category=ValidationCategory.TYPE,
                            path=f"{site_id}.{field_name}",
                            message=f"Type invalide pour '{field_name}': attendu {expected_types}, reçu {type(value).__name__}",
                            expected=str(expected_types),
                            received=type(value).__name__,
                            site_id=site_id,
                        )
                    )

            # Validation des bornes
            if field_name == "rate_limit_per_second" and isinstance(value, (int, float)):
                if value <= 0 or value > 10.0:
                    issues.append(
                        ValidationIssue(
                            severity=ValidationSeverity.ERROR,
                            category=ValidationCategory.SCHEMA,
                            path=f"{site_id}.{field_name}",
                            message=f"rate_limit_per_second doit être dans (0, 10], reçu {value}",
                            expected="(0, 10]",
                            received=value,
                            site_id=site_id,
                        )
                    )

            if field_name == "max_concurrent_downloads" and isinstance(value, int):
                if value < 1 or value > 32:
                    issues.append(
                        ValidationIssue(
                            severity=ValidationSeverity.ERROR,
                            category=ValidationCategory.SCHEMA,
                            path=f"{site_id}.{field_name}",
                            message=f"max_concurrent_downloads doit être dans [1, 32], reçu {value}",
                            expected="[1, 32]",
                            received=value,
                            site_id=site_id,
                        )
                    )

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    async def get_stats(self) -> ValidationStats:
        """Retourne les statistiques agrégées du validateur."""
        async with self._stats_lock:
            uptime = 0.0
            if self._start_time > 0:
                uptime = time.monotonic() - self._start_time

            return ValidationStats(
                total_validations=self._total_validations,
                successful_validations=self._successful_validations,
                failed_validations=self._failed_validations,
                total_issues_found=self._total_issues_found,
                total_errors_found=self._total_errors_found,
                total_warnings_found=self._total_warnings_found,
                total_duration_ms=self._total_duration_ms,
                issues_by_category=dict(self._issues_by_category),
                schema_loaded_at=self._schema_loaded_at,
                uptime_seconds=uptime,
            )

    async def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        async with self._stats_lock:
            self._total_validations = 0
            self._successful_validations = 0
            self._failed_validations = 0
            self._total_issues_found = 0
            self._total_errors_found = 0
            self._total_warnings_found = 0
            self._total_duration_ms = 0.0
            self._issues_by_category.clear()

    # ------------------------------------------------------------------------
    # Méthodes internes — Chargement du schéma
    # ------------------------------------------------------------------------

    def _load_schema(self) -> dict[str, Any]:
        """Charge le schéma JSON (synchrone, dans un thread).

        Cherche d'abord dans le chemin custom, puis dans les ressources
        embarquées du package.

        Returns:
            Schéma JSON parsé.

        Raises:
            SchemaLoadError: Si le schéma ne peut être chargé.
        """
        # 1. Chemin custom
        if self._custom_schema_path is not None:
            if not self._custom_schema_path.exists():
                raise SchemaLoadError(
                    f"Schéma custom introuvable: {self._custom_schema_path}"
                )
            try:
                schema_bytes = self._custom_schema_path.read_bytes()
                return orjson.loads(schema_bytes)
            except Exception as e:
                raise SchemaLoadError(
                    f"Impossible de parser le schéma custom: {e}"
                ) from e

        # 2. Ressources embarquées
        try:
            data_resource = importlib.resources.files(self._SCHEMA_PACKAGE)
            schema_file = data_resource / self._SCHEMA_FILENAME
            schema_bytes = schema_file.read_bytes()
            return orjson.loads(schema_bytes)
        except Exception as e:
            raise SchemaLoadError(
                f"Impossible de charger le schéma embarqué: {e}"
            ) from e

    @staticmethod
    def _load_yaml(path: Path) -> Any:
        """Charge un fichier YAML (synchrone).

        Args:
            path: Chemin du fichier YAML.

        Returns:
            Document parsé.

        Raises:
            InvalidDocumentError: Si le fichier ne peut être parsé.
        """
        try:
            import yaml
            content = path.read_text(encoding="utf-8")
            return yaml.safe_load(content)
        except ImportError as e:
            raise InvalidDocumentError(
                "La librairie PyYAML est requise pour charger les fichiers YAML"
            ) from e
        except Exception as e:
            raise InvalidDocumentError(f"Erreur de parsing YAML: {e}") from e

    # ------------------------------------------------------------------------
    # Méthodes internes — Conversion d'erreurs
    # ------------------------------------------------------------------------

    def _error_to_issue(
        self,
        error: JsonSchemaValidationError,
        document: Any,
        mode: ValidationMode,
    ) -> ValidationIssue:
        """Convertit une erreur JSON Schema en ValidationIssue.

        Args:
            error: Erreur JSON Schema.
            document: Document complet (pour extraction de contexte).
            mode: Mode de validation.

        Returns:
            ValidationIssue formatée.
        """
        path = _path_to_string(error.absolute_path)
        message = _format_error_message(error)
        category = _categorize_error(error)
        severity = _determine_severity(error, mode)
        site_id = _extract_site_id_from_path(path, document)

        return ValidationIssue(
            severity=severity,
            category=category,
            path=path,
            message=message,
            expected=error.validator_value,
            received=error.instance,
            schema_path=_path_to_string(error.absolute_schema_path),
            validator=error.validator,
            site_id=site_id,
        )

    async def _validate_cross_references(
        self,
        document: Any,
        mode: ValidationMode,
    ) -> list[ValidationIssue]:
        """Valide les références croisées entre sites.

        Détecte :
            - IDs de sites en doublon
            - Références de parsers invalides (module inexistant)
            - Domaines en doublon entre sites

        Args:
            document: Document complet.
            mode: Mode de validation.

        Returns:
            Liste des issues détectées.
        """
        issues: list[ValidationIssue] = []

        if not isinstance(document, Mapping) or "sites" not in document:
            return issues

        sites = document["sites"]
        if not isinstance(sites, list):
            return issues

        # 1. Détecter les IDs en doublon
        seen_ids: dict[str, int] = {}
        for index, site in enumerate(sites):
            if not isinstance(site, Mapping):
                continue
            site_id = site.get("id")
            if site_id is None:
                continue
            site_id_str = str(site_id)
            if site_id_str in seen_ids:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        category=ValidationCategory.UNIQUENESS,
                        path=f"sites.{index}.id",
                        message=f"ID de site en doublon: '{site_id_str}' (déjà défini à l'index {seen_ids[site_id_str]})",
                        expected="ID unique",
                        received=site_id_str,
                        site_id=site_id_str,
                    )
                )
            else:
                seen_ids[site_id_str] = index

        # 2. Détecter les domaines en doublon entre sites
        seen_domains: dict[str, str] = {}  # domain → site_id
        for index, site in enumerate(sites):
            if not isinstance(site, Mapping):
                continue
            site_id = str(site.get("id", f"index_{index}"))
            domains = site.get("domains", [])
            if not isinstance(domains, list):
                continue

            for domain_index, domain in enumerate(domains):
                domain_str = str(domain).lower()
                if domain_str in seen_domains:
                    issues.append(
                        ValidationIssue(
                            severity=ValidationSeverity.WARNING,
                            category=ValidationCategory.UNIQUENESS,
                            path=f"sites.{index}.domains.{domain_index}",
                            message=f"Domaine '{domain_str}' déjà utilisé par le site '{seen_domains[domain_str]}'",
                            expected="domaine unique",
                            received=domain_str,
                            site_id=site_id,
                        )
                    )
                else:
                    seen_domains[domain_str] = site_id

        # 3. Vérifier les références de parsers (existence du module)
        for index, site in enumerate(sites):
            if not isinstance(site, Mapping):
                continue
            site_id = str(site.get("id", f"index_{index}"))
            parser_class = site.get("parser_class")
            if parser_class is None:
                continue

            parser_class_str = str(parser_class)
            if ":" not in parser_class_str:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        category=ValidationCategory.REFERENCE,
                        path=f"sites.{index}.parser_class",
                        message=f"Référence de parser invalide: '{parser_class_str}' (format attendu: 'module.path:ClassName')",
                        expected="module.path:ClassName",
                        received=parser_class_str,
                        site_id=site_id,
                    )
                )
                continue

            # Vérifier que le module existe (sans l'importer)
            module_path = parser_class_str.split(":", 1)[0]
            try:
                # Vérification rapide sans import complet
                spec = importlib.util.find_spec(module_path)
                if spec is None:
                    issues.append(
                        ValidationIssue(
                            severity=ValidationSeverity.WARNING,
                            category=ValidationCategory.REFERENCE,
                            path=f"sites.{index}.parser_class",
                            message=f"Module du parser introuvable: '{module_path}' (le site ne pourra pas être chargé)",
                            expected="module Python existant",
                            received=module_path,
                            site_id=site_id,
                        )
                    )
            except (ValueError, ModuleNotFoundError):
                # find_spec peut lever des exceptions pour des chemins invalides
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.WARNING,
                        category=ValidationCategory.REFERENCE,
                        path=f"sites.{index}.parser_class",
                        message=f"Module du parser invalide: '{module_path}'",
                        expected="module Python valide",
                        received=module_path,
                        site_id=site_id,
                    )
                )

        return issues

    # ------------------------------------------------------------------------
    # Méthodes internes — Statistiques
    # ------------------------------------------------------------------------

    async def _record_validation(self, result: ValidationResult) -> None:
        """Enregistre une validation dans les statistiques."""
        async with self._stats_lock:
            self._total_validations += 1
            self._total_duration_ms += result.duration_ms
            self._total_issues_found += len(result.issues)

            errors = result.errors_count
            warnings = result.warnings_count

            self._total_errors_found += errors
            self._total_warnings_found += warnings

            if result.is_valid:
                self._successful_validations += 1
            else:
                self._failed_validations += 1

            # Compter par catégorie
            for issue in result.issues:
                cat_key = issue.category.value
                self._issues_by_category[cat_key] = (
                    self._issues_by_category.get(cat_key, 0) + 1
                )

    def _ensure_started(self) -> None:
        """Vérifie que le validateur est démarré."""
        if not self._started:
            raise ValidatorNotStartedError()

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return (
            f"<SchemaValidator status={status} "
            f"mode={self._mode.value} "
            f"validations={self._total_validations}>"
        )


# ============================================================================
# HELPERS PUBLICS — Fonctions utilitaires
# ============================================================================


async def validate_site_config_quick(
    site_config: Mapping[str, Any],
    *,
    mode: ValidationMode = ValidationMode.STRICT,
) -> bool:
    """Validation rapide d'un site individuel (one-shot).

    Crée un validateur temporaire, valide, et le détruit. Utile pour
    des validations ponctuelles sans gestion de lifecycle.

    Args:
        site_config: Configuration du site à valider.
        mode: Mode de validation.

    Returns:
        True si la configuration est valide.
    """
    async with SchemaValidator(mode=mode) as validator:
        result = await validator.validate_site_config(site_config, mode=mode)
        return result.is_valid


async def validate_sites_file_quick(
    path: Path,
    *,
    mode: ValidationMode = ValidationMode.STRICT,
) -> ValidationResult:
    """Validation rapide d'un fichier sites.yaml (one-shot).

    Args:
        path: Chemin du fichier à valider.
        mode: Mode de validation.

    Returns:
        Résultat complet de la validation.
    """
    async with SchemaValidator(mode=mode) as validator:
        return await validator.validate_sites_file(path, mode=mode)


def format_validation_report(result: ValidationResult) -> str:
    """Formate un rapport de validation lisible.

    Args:
        result: Résultat de validation à formater.

    Returns:
        Rapport textuel multi-lignes.
    """
    lines: list[str] = []

    # En-tête
    lines.append("=" * 70)
    lines.append("RAPPORT DE VALIDATION")
    lines.append("=" * 70)
    lines.append("")

    # Résumé
    lines.append(result.format_summary())
    if result.document_path:
        lines.append(f"Fichier: {result.document_path}")
    lines.append(f"Mode: {result.validation_mode.label}")
    lines.append(f"Schéma: v{result.schema_version}")
    lines.append("")

    # Problèmes
    if result.issues:
        lines.append("-" * 70)
        lines.append("PROBLÈMES DÉTECTÉS")
        lines.append("-" * 70)
        lines.append("")

        # Grouper par sévérité
        for severity in (
            ValidationSeverity.ERROR,
            ValidationSeverity.WARNING,
            ValidationSeverity.INFO,
        ):
            severity_issues = [i for i in result.issues if i.severity == severity]
            if not severity_issues:
                continue

            lines.append(f"{severity.icon} {severity.value.upper()} ({len(severity_issues)})")
            lines.append("")
            for issue in severity_issues:
                lines.append(f"  {issue.format_for_display()}")
            lines.append("")
    else:
        lines.append("✓ Aucun problème détecté")
        lines.append("")

    # Sites affectés
    affected = result.affected_sites
    if affected:
        lines.append("-" * 70)
        lines.append(f"SITES AFFECTÉS ({len(affected)})")
        lines.append("-" * 70)
        for site_id in affected:
            lines.append(f"  • {site_id}")
        lines.append("")

    # Pied
    lines.append("=" * 70)

    return "\n".join(lines)


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Exceptions
    "RegistryValidationError",
    "SchemaLoadError",
    "ValidatorNotStartedError",
    "InvalidDocumentError",
    # Enums
    "ValidationMode",
    "ValidationSeverity",
    "ValidationCategory",
    # Modèles
    "ValidationIssue",
    "ValidationResult",
    "ValidationStats",
    # Classe principale
    "SchemaValidator",
    # Helpers publics
    "validate_site_config_quick",
    "validate_sites_file_quick",
    "format_validation_report",
]
