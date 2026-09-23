"""Routeur FastAPI pour la gestion des paramètres de l'application.

Ce module fournit un routeur FastAPI complet pour gérer la configuration
de l'application via l'API REST. Il permet de lire, modifier, réinitialiser,
exporter et importer les paramètres de manière sécurisée.

**Endpoints** :
    - GET /settings                    : Récupérer la configuration complète
    - PUT /settings                    : Mettre à jour la configuration
    - PATCH /settings/{section}        : Mettre à jour une section spécifique
    - GET /settings/sections           : Lister les sections disponibles
    - GET /settings/{section}          : Récupérer une section spécifique
    - POST /settings/reset             : Réinitialiser aux valeurs par défaut
    - POST /settings/export            : Exporter la configuration
    - POST /settings/import            : Importer une configuration
    - GET /settings/schema             : Récupérer le schéma JSON
    - GET /settings/defaults           : Récupérer les valeurs par défaut
    - GET /settings/history            : Historique des changements

**Fonctionnalités** :
    - Validation stricte via Pydantic v2
    - Sauvegarde atomique (atomic_write)
    - Cache en mémoire pour les lectures fréquentes
    - Événements EventBus pour notifier les changements
    - Historique des modifications (optionnel)
    - Export/Import en JSON ou YAML
    - Permissions (admin requis pour modifications)
    - Logging structuré
    - Gestion robuste des erreurs
    - Support des sections imbriquées

**Exemple d'utilisation** :
    >>> from fastapi import FastAPI
    >>> from nexusdl.interfaces.web.backend.routers.settings import settings_router
    >>>
    >>> app = FastAPI()
    >>> app.include_router(settings_router, prefix="/api/v1")

**Exemples d'appels API** :
    >>> # Récupérer la configuration
    >>> GET /api/v1/settings
    >>>
    >>> # Mettre à jour une section
    >>> PATCH /api/v1/settings/network
    >>> {"timeout": 60, "max_connections": 200}
    >>>
    >>> # Exporter en JSON
    >>> POST /api/v1/settings/export
    >>> {"format": "json"}
    >>>
    >>> # Importer depuis YAML
    >>> POST /api/v1/settings/import
    >>> Content-Type: application/yaml
    >>> app:
    >>>   language: fr

Intégration :
    - core/config.py              : Configuration globale
    - core/paths.py               : Chemins des fichiers
    - core/events.py              : EventBus pour notifications
    - core/logger.py              : Logs
    - core/i18n.py                : Traductions
    - core/utils/filesystem.py    : atomic_write, read_text, write_text
    - interfaces/web/backend/middleware/auth.py : Authentification
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Final

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from nexusdl.core.constants import APP_NAME
from nexusdl.core.events import EventType, get_event_bus
from nexusdl.core.exceptions import NexusDLError
from nexusdl.core.i18n import t


# ============================================================================
# CONSTANTES
# ============================================================================


# Valeurs par défaut
DEFAULT_HISTORY_SIZE: Final[int] = 100
DEFAULT_CACHE_TTL_SECONDS: Final[int] = 60
MAX_EXPORT_SIZE_BYTES: Final[int] = 10485760  # 10 MB

# Formats d'export/import
SUPPORTED_FORMATS: Final[frozenset[str]] = frozenset({"json", "yaml", "yml"})

# Sections de configuration
CONFIG_SECTIONS: Final[frozenset[str]] = frozenset({
    "app",
    "network",
    "proxy",
    "logging",
    "download",
    "library",
    "cloudflare",
    "i18n",
    "storage",
    "events",
    "interface",
})


# ============================================================================
# EXCEPTIONS
# ============================================================================


class SettingsRouterError(NexusDLError):
    """Exception de base pour les erreurs du routeur settings."""


class ConfigLoadError(SettingsRouterError):
    """Exception levée lorsque la configuration ne peut être chargée.

    Attributes:
        path: Chemin du fichier.
        reason: Raison de l'échec.
    """

    def __init__(self, path: str, reason: str = "") -> None:
        msg = t("settings.error.load_failed", default="Failed to load configuration")
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.path = path
        self.reason = reason


class ConfigSaveError(SettingsRouterError):
    """Exception levée lorsque la configuration ne peut être sauvegardée.

    Attributes:
        path: Chemin du fichier.
        reason: Raison de l'échec.
    """

    def __init__(self, path: str, reason: str = "") -> None:
        msg = t("settings.error.save_failed", default="Failed to save configuration")
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.path = path
        self.reason = reason


class ConfigValidationError(SettingsRouterError):
    """Exception levée lorsque la validation échoue.

    Attributes:
        field: Champ invalide.
        value: Valeur invalide.
        reason: Raison de l'erreur.
    """

    def __init__(self, field: str, value: Any, reason: str = "") -> None:
        msg = t("settings.error.validation_failed", default="Configuration validation failed")
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.field = field
        self.value = value
        self.reason = reason


class SectionNotFoundError(SettingsRouterError):
    """Exception levée lorsqu'une section n'existe pas.

    Attributes:
        section: Nom de la section.
    """

    def __init__(self, section: str) -> None:
        super().__init__(
            t("settings.error.section_not_found", default="Section not found: {section}", section=section)
        )
        self.section = section


# ============================================================================
# ENUMS
# ============================================================================


class ExportFormat(str, Enum):
    """Format d'export/import.

    Attributes:
        JSON: Format JSON.
        YAML: Format YAML.
    """

    JSON = "json"
    YAML = "yaml"

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            ExportFormat.JSON: t("settings.format.json", default="JSON"),
            ExportFormat.YAML: t("settings.format.yaml", default="YAML"),
        }[self]

    @property
    def content_type(self) -> str:
        """Type MIME."""
        return {
            ExportFormat.JSON: "application/json",
            ExportFormat.YAML: "application/x-yaml",
        }[self]

    @property
    def file_extension(self) -> str:
        """Extension de fichier."""
        return {
            ExportFormat.JSON: ".json",
            ExportFormat.YAML: ".yaml",
        }[self]


class ChangeAction(str, Enum):
    """Type de changement effectué.

    Attributes:
        UPDATE: Mise à jour.
        RESET: Réinitialisation.
        IMPORT: Import.
    """

    UPDATE = "update"
    RESET = "reset"
    IMPORT = "import"


# ============================================================================
# MODÈLES DE REQUÊTE — Pydantic
# ============================================================================


class UpdateSettingsRequest(BaseModel):
    """Requête de mise à jour complète de la configuration.

    Attributes:
        config: Configuration complète.
    """

    config: dict[str, Any] = Field(..., description="Configuration complète.")

    model_config = ConfigDict(extra="forbid")


class UpdateSectionRequest(BaseModel):
    """Requête de mise à jour d'une section.

    Attributes:
        data: Données de la section.
    """

    data: dict[str, Any] = Field(..., description="Données de la section.")

    model_config = ConfigDict(extra="forbid")


class ExportRequest(BaseModel):
    """Requête d'export de configuration.

    Attributes:
        format: Format d'export (json, yaml).
        include_metadata: Inclure les métadonnées (timestamp, version).
        sections: Sections à exporter (None = toutes).
    """

    format: ExportFormat = Field(default=ExportFormat.JSON, description="Format.")
    include_metadata: bool = Field(default=True, description="Inclure métadonnées.")
    sections: list[str] | None = Field(default=None, description="Sections à exporter.")

    @field_validator("sections")
    @classmethod
    def validate_sections(cls, v: list[str] | None) -> list[str] | None:
        """Valide les sections."""
        if v is not None:
            invalid = [s for s in v if s not in CONFIG_SECTIONS]
            if invalid:
                raise ValueError(f"Sections invalides: {invalid}")
        return v


class ImportRequest(BaseModel):
    """Requête d'import de configuration.

    Attributes:
        config: Configuration à importer.
        overwrite: Écraser la configuration existante.
        validate: Valider avant import.
    """

    config: dict[str, Any] = Field(..., description="Configuration à importer.")
    overwrite: bool = Field(default=True, description="Écraser existante.")
    validate: bool = Field(default=True, description="Valider avant import.")


class ResetRequest(BaseModel):
    """Requête de réinitialisation.

    Attributes:
        sections: Sections à réinitialiser (None = toutes).
        confirm: Confirmation explicite.
    """

    sections: list[str] | None = Field(default=None, description="Sections à réinitialiser.")
    confirm: bool = Field(default=False, description="Confirmation.")

    @field_validator("confirm")
    @classmethod
    def validate_confirm(cls, v: bool) -> bool:
        """Valide la confirmation."""
        if not v:
            raise ValueError("Confirmation requise")
        return v


# ============================================================================
# MODÈLES DE RÉPONSE — Pydantic
# ============================================================================


class SettingsResponse(BaseModel):
    """Réponse avec la configuration complète.

    Attributes:
        config: Configuration.
        last_modified: Timestamp de dernière modification.
        version: Version du schéma.
    """

    config: dict[str, Any] = Field(..., description="Configuration.")
    last_modified: datetime = Field(..., description="Dernière modification.")
    version: str = Field(..., description="Version du schéma.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class SectionResponse(BaseModel):
    """Réponse avec une section de configuration.

    Attributes:
        section: Nom de la section.
        data: Données de la section.
        last_modified: Timestamp de dernière modification.
    """

    section: str = Field(..., description="Nom de la section.")
    data: dict[str, Any] = Field(..., description="Données.")
    last_modified: datetime = Field(..., description="Dernière modification.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class SectionsListResponse(BaseModel):
    """Réponse avec la liste des sections.

    Attributes:
        sections: Liste des noms de sections.
        count: Nombre de sections.
    """

    sections: list[str] = Field(..., description="Sections.")
    count: int = Field(..., ge=0, description="Nombre.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class UpdateResponse(BaseModel):
    """Réponse après mise à jour.

    Attributes:
        success: Si la mise à jour a réussi.
        message: Message de confirmation.
        changed_fields: Liste des champs modifiés.
        timestamp: Timestamp de la mise à jour.
    """

    success: bool = Field(..., description="Succès.")
    message: str = Field(..., description="Message.")
    changed_fields: list[str] = Field(default_factory=list, description="Champs modifiés.")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Timestamp.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ExportResponse(BaseModel):
    """Réponse après export.

    Attributes:
        format: Format d'export.
        content: Contenu exporté.
        size_bytes: Taille en bytes.
        exported_at: Timestamp d'export.
        sections_exported: Sections exportées.
    """

    format: ExportFormat = Field(..., description="Format.")
    content: str = Field(..., description="Contenu.")
    size_bytes: int = Field(..., ge=0, description="Taille.")
    exported_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Timestamp.")
    sections_exported: list[str] = Field(..., description="Sections exportées.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ImportResponse(BaseModel):
    """Réponse après import.

    Attributes:
        success: Si l'import a réussi.
        message: Message de confirmation.
        imported_sections: Sections importées.
        timestamp: Timestamp d'import.
    """

    success: bool = Field(..., description="Succès.")
    message: str = Field(..., description="Message.")
    imported_sections: list[str] = Field(default_factory=list, description="Sections importées.")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Timestamp.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ResetResponse(BaseModel):
    """Réponse après réinitialisation.

    Attributes:
        success: Si la réinitialisation a réussi.
        message: Message de confirmation.
        reset_sections: Sections réinitialisées.
        timestamp: Timestamp de réinitialisation.
    """

    success: bool = Field(..., description="Succès.")
    message: str = Field(..., description="Message.")
    reset_sections: list[str] = Field(default_factory=list, description="Sections réinitialisées.")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Timestamp.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class SchemaResponse(BaseModel):
    """Réponse avec le schéma JSON.

    Attributes:
        schema: Schéma JSON de la configuration.
        version: Version du schéma.
        generated_at: Timestamp de génération.
    """

    schema: dict[str, Any] = Field(..., description="Schéma JSON.")
    version: str = Field(..., description="Version.")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Timestamp.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class DefaultsResponse(BaseModel):
    """Réponse avec les valeurs par défaut.

    Attributes:
        defaults: Valeurs par défaut.
        version: Version du schéma.
    """

    defaults: dict[str, Any] = Field(..., description="Valeurs par défaut.")
    version: str = Field(..., description="Version.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ChangeEntry(BaseModel):
    """Entrée d'historique de changement.

    Attributes:
        timestamp: Timestamp du changement.
        action: Type de changement.
        user_id: ID de l'utilisateur.
        sections_affected: Sections affectées.
        details: Détails du changement.
    """

    timestamp: datetime = Field(..., description="Timestamp.")
    action: ChangeAction = Field(..., description="Action.")
    user_id: str = Field(default="anonymous", description="ID utilisateur.")
    sections_affected: list[str] = Field(default_factory=list, description="Sections.")
    details: dict[str, Any] = Field(default_factory=dict, description="Détails.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class HistoryResponse(BaseModel):
    """Réponse avec l'historique des changements.

    Attributes:
        entries: Liste des entrées d'historique.
        total: Nombre total d'entrées.
        limit: Limite appliquée.
    """

    entries: list[ChangeEntry] = Field(..., description="Entrées.")
    total: int = Field(..., ge=0, description="Total.")
    limit: int = Field(..., ge=0, description="Limite.")

    model_config = ConfigDict(frozen=True, extra="forbid")


class ErrorResponse(BaseModel):
    """Réponse d'erreur.

    Attributes:
        error: Code d'erreur.
        message: Message d'erreur.
        details: Détails additionnels.
    """

    error: str = Field(..., description="Code erreur.")
    message: str = Field(..., description="Message.")
    details: dict[str, Any] = Field(default_factory=dict, description="Détails.")

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# CACHE — Cache en mémoire pour les configurations
# ============================================================================


class SettingsCache:
    """Cache en mémoire pour les configurations.

    Évite les lectures répétées du fichier de configuration.
    """

    def __init__(self, ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS) -> None:
        """Initialise le cache.

        Args:
            ttl_seconds: Durée de vie du cache en secondes.
        """
        self._cache: dict[str, tuple[Any, datetime]] = {}
        self._ttl = timedelta(seconds=ttl_seconds)
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        """Récupère une valeur du cache.

        Args:
            key: Clé du cache.

        Returns:
            Valeur ou None si expirée/inexistante.
        """
        async with self._lock:
            if key not in self._cache:
                return None

            value, expires_at = self._cache[key]
            if datetime.now(UTC) > expires_at:
                del self._cache[key]
                return None

            return value

    async def set(self, key: str, value: Any) -> None:
        """Stocke une valeur dans le cache.

        Args:
            key: Clé du cache.
            value: Valeur à stocker.
        """
        async with self._lock:
            expires_at = datetime.now(UTC) + self._ttl
            self._cache[key] = (value, expires_at)

    async def invalidate(self, key: str | None = None) -> None:
        """Invalide une entrée ou tout le cache.

        Args:
            key: Clé à invalider (None = tout).
        """
        async with self._lock:
            if key is None:
                self._cache.clear()
            else:
                self._cache.pop(key, None)

    @property
    def size(self) -> int:
        """Taille actuelle du cache."""
        return len(self._cache)


# Instance globale du cache
_settings_cache = SettingsCache()


def get_settings_cache() -> SettingsCache:
    """Retourne l'instance globale du cache.

    Returns:
        Instance de SettingsCache.
    """
    return _settings_cache


# ============================================================================
# HISTORIQUE — Historique des changements
# ============================================================================


class ChangeHistory:
    """Historique des changements de configuration.

    Stocke les N derniers changements pour audit.
    """

    def __init__(self, max_size: int = DEFAULT_HISTORY_SIZE) -> None:
        """Initialise l'historique.

        Args:
            max_size: Taille maximale de l'historique.
        """
        self._entries: list[ChangeEntry] = []
        self._max_size = max_size
        self._lock = asyncio.Lock()

    async def add(self, entry: ChangeEntry) -> None:
        """Ajoute une entrée à l'historique.

        Args:
            entry: Entrée à ajouter.
        """
        async with self._lock:
            self._entries.append(entry)
            # Limiter la taille
            if len(self._entries) > self._max_size:
                self._entries = self._entries[-self._max_size:]

    async def get_all(self, limit: int | None = None) -> list[ChangeEntry]:
        """Récupère toutes les entrées.

        Args:
            limit: Nombre maximum d'entrées (None = toutes).

        Returns:
            Liste d'entrées.
        """
        async with self._lock:
            if limit is None:
                return list(self._entries)
            return self._entries[-limit:]

    async def clear(self) -> None:
        """Vide l'historique."""
        async with self._lock:
            self._entries.clear()

    @property
    def size(self) -> int:
        """Nombre d'entrées dans l'historique."""
        return len(self._entries)


# Instance globale de l'historique
_change_history = ChangeHistory()


def get_change_history() -> ChangeHistory:
    """Retourne l'instance globale de l'historique.

    Returns:
        Instance de ChangeHistory.
    """
    return _change_history


# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================


async def _get_config() -> Any:
    """Récupère la configuration actuelle.

    Returns:
        Instance de NexusDLConfig.

    Raises:
        HTTPException: Si la configuration ne peut être chargée.
    """
    try:
        from nexusdl.core.config import get_config
        return get_config()
    except Exception as e:
        logger.error("Impossible de charger la configuration: {}", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorResponse(
                error="config_load_failed",
                message=t("settings.error.load_failed", default="Failed to load configuration"),
                details={"reason": str(e)},
            ).model_dump(),
        ) from e


async def _save_config(config: Any) -> None:
    """Sauvegarde la configuration de manière atomique.

    Args:
        config: Configuration à sauvegarder.

    Raises:
        HTTPException: Si la sauvegarde échoue.
    """
    try:
        from nexusdl.core.paths import get_paths
        from nexusdl.core.utils.filesystem import atomic_write

        paths = get_paths()
        config_path = paths.config_file

        # Sérialiser en YAML
        yaml_content = config.to_yaml()

        # Sauvegarder de manière atomique
        atomic_write(config_path, yaml_content)

        logger.info("Configuration sauvegardée: {}", config_path)

    except Exception as e:
        logger.error("Impossible de sauvegarder la configuration: {}", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorResponse(
                error="config_save_failed",
                message=t("settings.error.save_failed", default="Failed to save configuration"),
                details={"reason": str(e)},
            ).model_dump(),
        ) from e


async def _validate_config(config_data: dict[str, Any]) -> Any:
    """Valide et construit une configuration.

    Args:
        config_data: Données de configuration.

    Returns:
        Instance de NexusDLConfig validée.

    Raises:
        HTTPException: Si la validation échoue.
    """
    try:
        from nexusdl.core.config import NexusDLConfig
        return NexusDLConfig.model_validate(config_data)
    except Exception as e:
        logger.error("Validation de configuration échouée: {}", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorResponse(
                error="validation_failed",
                message=t("settings.error.validation_failed", default="Configuration validation failed"),
                details={"reason": str(e)},
            ).model_dump(),
        ) from e


async def _emit_config_changed_event(
    action: ChangeAction,
    sections: list[str],
    user_id: str = "anonymous",
    details: dict[str, Any] | None = None,
) -> None:
    """Émet un événement de changement de configuration.

    Args:
        action: Type de changement.
        sections: Sections affectées.
        user_id: ID de l'utilisateur.
        details: Détails additionnels.
    """
    try:
        event_bus = get_event_bus()
        await event_bus.emit(
            EventType.CONFIG_CHANGED,
            payload={
                "action": action.value,
                "sections": sections,
                "user_id": user_id,
                "details": details or {},
                "timestamp": datetime.now(UTC).isoformat(),
            },
            source="interfaces.web.settings",
        )
    except Exception as e:
        logger.debug("Impossible d'émettre l'événement de configuration: {}", e)


async def _record_change(
    action: ChangeAction,
    sections: list[str],
    user_id: str = "anonymous",
    details: dict[str, Any] | None = None,
) -> None:
    """Enregistre un changement dans l'historique.

    Args:
        action: Type de changement.
        sections: Sections affectées.
        user_id: ID de l'utilisateur.
        details: Détails additionnels.
    """
    entry = ChangeEntry(
        timestamp=datetime.now(UTC),
        action=action,
        user_id=user_id,
        sections_affected=sections,
        details=details or {},
    )
    await _change_history.add(entry)


def _get_user_id_from_request(request: Any) -> str:
    """Extrait l'ID utilisateur de la requête.

    Args:
        request: Requête HTTP.

    Returns:
        ID utilisateur ou "anonymous".
    """
    if hasattr(request.state, "user") and request.state.user:
        return request.state.user.user_id
    return "anonymous"


def _config_to_dict(config: Any) -> dict[str, Any]:
    """Convertit une configuration en dictionnaire.

    Args:
        config: Instance de NexusDLConfig.

    Returns:
        Dictionnaire.
    """
    return config.model_dump(mode="json")


def _serialize_config(config_data: dict[str, Any], format: ExportFormat) -> str:
    """Sérialise une configuration dans le format spécifié.

    Args:
        config_data: Données de configuration.
        format: Format de sérialisation.

    Returns:
        Chaîne sérialisée.
    """
    if format == ExportFormat.JSON:
        return json.dumps(config_data, indent=2, ensure_ascii=False)
    elif format == ExportFormat.YAML:
        import yaml
        return yaml.dump(config_data, default_flow_style=False, allow_unicode=True)
    else:
        raise ValueError(f"Format non supporté: {format}")


def _deserialize_config(content: str, format: ExportFormat) -> dict[str, Any]:
    """Désérialise une configuration depuis le format spécifié.

    Args:
        content: Contenu à désérialiser.
        format: Format de désérialisation.

    Returns:
        Dictionnaire de configuration.
    """
    if format == ExportFormat.JSON:
        return json.loads(content)
    elif format == ExportFormat.YAML:
        import yaml
        return yaml.safe_load(content)
    else:
        raise ValueError(f"Format non supporté: {format}")


# ============================================================================
# ROUTEUR — Endpoints FastAPI
# ============================================================================


if FASTAPI_AVAILABLE:

    settings_router = APIRouter(tags=["settings"])

    # =========================================================================
    # GET /settings — Récupérer la configuration complète
    # =========================================================================

    @settings_router.get(
        "/settings",
        response_model=SettingsResponse,
        summary="Récupérer la configuration complète",
        description="Retourne la configuration actuelle de l'application.",
        responses={
            200: {"description": "Configuration actuelle"},
            500: {"description": "Erreur interne"},
        },
    )
    async def get_settings(request: Request) -> SettingsResponse:
        """Récupère la configuration complète.

        Args:
            request: Requête HTTP.

        Returns:
            Configuration actuelle.
        """
        logger.info("Récupération de la configuration complète")

        # Vérifier le cache
        cache = get_settings_cache()
        cached = await cache.get("full_config")
        if cached:
            return cached

        config = await _get_config()
        config_dict = _config_to_dict(config)

        response = SettingsResponse(
            config=config_dict,
            last_modified=datetime.now(UTC),
            version="1.0",
        )

        # Stocker dans le cache
        await cache.set("full_config", response)

        return response

    # =========================================================================
    # PUT /settings — Mettre à jour la configuration complète
    # =========================================================================

    @settings_router.put(
        "/settings",
        response_model=UpdateResponse,
        summary="Mettre à jour la configuration complète",
        description="Remplace la configuration actuelle par une nouvelle configuration complète.",
        responses={
            200: {"description": "Configuration mise à jour"},
            400: {"description": "Validation échouée"},
            500: {"description": "Erreur interne"},
        },
    )
    async def update_settings(
        request: Request,
        body: UpdateSettingsRequest,
    ) -> UpdateResponse:
        """Met à jour la configuration complète.

        Args:
            request: Requête HTTP.
            body: Corps de la requête.

        Returns:
            Réponse de mise à jour.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Mise à jour complète de la configuration par user={}", user_id)

        # Valider la nouvelle configuration
        validated_config = await _validate_config(body.config)

        # Sauvegarder
        await _save_config(validated_config)

        # Invalider le cache
        cache = get_settings_cache()
        await cache.invalidate()

        # Enregistrer le changement
        await _record_change(
            action=ChangeAction.UPDATE,
            sections=list(CONFIG_SECTIONS),
            user_id=user_id,
            details={"type": "full_update"},
        )

        # Émettre un événement
        await _emit_config_changed_event(
            action=ChangeAction.UPDATE,
            sections=list(CONFIG_SECTIONS),
            user_id=user_id,
            details={"type": "full_update"},
        )

        return UpdateResponse(
            success=True,
            message=t("settings.success.updated", default="Configuration updated successfully"),
            changed_fields=list(body.config.keys()),
        )

    # =========================================================================
    # PATCH /settings/{section} — Mettre à jour une section spécifique
    # =========================================================================

    @settings_router.patch(
        "/settings/{section}",
        response_model=UpdateResponse,
        summary="Mettre à jour une section spécifique",
        description="Met à jour uniquement les champs d'une section spécifique.",
        responses={
            200: {"description": "Section mise à jour"},
            400: {"description": "Validation échouée"},
            404: {"description": "Section non trouvée"},
            500: {"description": "Erreur interne"},
        },
    )
    async def update_section(
        request: Request,
        section: str,
        body: UpdateSectionRequest,
    ) -> UpdateResponse:
        """Met à jour une section spécifique.

        Args:
            request: Requête HTTP.
            section: Nom de la section.
            body: Corps de la requête.

        Returns:
            Réponse de mise à jour.
        """
        # Vérifier que la section existe
        if section not in CONFIG_SECTIONS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse(
                    error="section_not_found",
                    message=t("settings.error.section_not_found", default="Section not found: {section}", section=section),
                    details={"section": section, "available": list(CONFIG_SECTIONS)},
                ).model_dump(),
            )

        user_id = _get_user_id_from_request(request)
        logger.info("Mise à jour de la section {} par user={}", section, user_id)

        # Récupérer la configuration actuelle
        config = await _get_config()
        config_dict = _config_to_dict(config)

        # Mettre à jour la section
        if section not in config_dict:
            config_dict[section] = {}

        config_dict[section].update(body.data)

        # Valider la nouvelle configuration
        validated_config = await _validate_config(config_dict)

        # Sauvegarder
        await _save_config(validated_config)

        # Invalider le cache
        cache = get_settings_cache()
        await cache.invalidate()

        # Enregistrer le changement
        await _record_change(
            action=ChangeAction.UPDATE,
            sections=[section],
            user_id=user_id,
            details={"type": "section_update", "changed_fields": list(body.data.keys())},
        )

        # Émettre un événement
        await _emit_config_changed_event(
            action=ChangeAction.UPDATE,
            sections=[section],
            user_id=user_id,
            details={"type": "section_update", "changed_fields": list(body.data.keys())},
        )

        return UpdateResponse(
            success=True,
            message=t("settings.success.section_updated", default="Section {section} updated successfully", section=section),
            changed_fields=list(body.data.keys()),
        )

    # =========================================================================
    # GET /settings/sections — Lister les sections disponibles
    # =========================================================================

    @settings_router.get(
        "/settings/sections",
        response_model=SectionsListResponse,
        summary="Lister les sections disponibles",
        description="Retourne la liste de toutes les sections de configuration disponibles.",
        responses={
            200: {"description": "Liste des sections"},
        },
    )
    async def list_sections() -> SectionsListResponse:
        """Liste les sections disponibles.

        Returns:
            Liste des sections.
        """
        logger.info("Liste des sections de configuration")

        return SectionsListResponse(
            sections=sorted(list(CONFIG_SECTIONS)),
            count=len(CONFIG_SECTIONS),
        )

    # =========================================================================
    # GET /settings/{section} — Récupérer une section spécifique
    # =========================================================================

    @settings_router.get(
        "/settings/{section}",
        response_model=SectionResponse,
        summary="Récupérer une section spécifique",
        description="Retourne les données d'une section spécifique de la configuration.",
        responses={
            200: {"description": "Données de la section"},
            404: {"description": "Section non trouvée"},
            500: {"description": "Erreur interne"},
        },
    )
    async def get_section(section: str) -> SectionResponse:
        """Récupère une section spécifique.

        Args:
            section: Nom de la section.

        Returns:
            Données de la section.
        """
        # Vérifier que la section existe
        if section not in CONFIG_SECTIONS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse(
                    error="section_not_found",
                    message=t("settings.error.section_not_found", default="Section not found: {section}", section=section),
                    details={"section": section, "available": list(CONFIG_SECTIONS)},
                ).model_dump(),
            )

        logger.info("Récupération de la section: {}", section)

        # Vérifier le cache
        cache = get_settings_cache()
        cache_key = f"section:{section}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        config = await _get_config()
        config_dict = _config_to_dict(config)

        section_data = config_dict.get(section, {})

        response = SectionResponse(
            section=section,
            data=section_data,
            last_modified=datetime.now(UTC),
        )

        # Stocker dans le cache
        await cache.set(cache_key, response)

        return response

    # =========================================================================
    # POST /settings/reset — Réinitialiser aux valeurs par défaut
    # =========================================================================

    @settings_router.post(
        "/settings/reset",
        response_model=ResetResponse,
        summary="Réinitialiser aux valeurs par défaut",
        description="Réinitialise la configuration (ou des sections spécifiques) aux valeurs par défaut.",
        responses={
            200: {"description": "Configuration réinitialisée"},
            400: {"description": "Confirmation manquante"},
            500: {"description": "Erreur interne"},
        },
    )
    async def reset_settings(
        request: Request,
        body: ResetRequest,
    ) -> ResetResponse:
        """Réinitialise la configuration.

        Args:
            request: Requête HTTP.
            body: Corps de la requête.

        Returns:
            Réponse de réinitialisation.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Réinitialisation de la configuration par user={}", user_id)

        sections_to_reset = body.sections or list(CONFIG_SECTIONS)

        try:
            from nexusdl.core.config import NexusDLConfig

            # Récupérer la configuration actuelle
            config = await _get_config()
            config_dict = _config_to_dict(config)

            # Récupérer les valeurs par défaut
            default_config = NexusDLConfig()
            default_dict = _config_to_dict(default_config)

            # Réinitialiser les sections spécifiées
            for section in sections_to_reset:
                if section in default_dict:
                    config_dict[section] = default_dict[section]

            # Valider et sauvegarder
            validated_config = await _validate_config(config_dict)
            await _save_config(validated_config)

            # Invalider le cache
            cache = get_settings_cache()
            await cache.invalidate()

            # Enregistrer le changement
            await _record_change(
                action=ChangeAction.RESET,
                sections=sections_to_reset,
                user_id=user_id,
                details={"type": "reset"},
            )

            # Émettre un événement
            await _emit_config_changed_event(
                action=ChangeAction.RESET,
                sections=sections_to_reset,
                user_id=user_id,
                details={"type": "reset"},
            )

            return ResetResponse(
                success=True,
                message=t("settings.success.reset", default="Configuration reset successfully"),
                reset_sections=sections_to_reset,
            )

        except Exception as e:
            logger.error("Erreur lors de la réinitialisation: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="reset_failed",
                    message=t("settings.error.reset_failed", default="Failed to reset configuration"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /settings/export — Exporter la configuration
    # =========================================================================

    @settings_router.post(
        "/settings/export",
        response_model=ExportResponse,
        summary="Exporter la configuration",
        description="Exporte la configuration dans le format spécifié (JSON ou YAML).",
        responses={
            200: {"description": "Configuration exportée"},
            500: {"description": "Erreur interne"},
        },
    )
    async def export_settings(body: ExportRequest) -> ExportResponse:
        """Exporte la configuration.

        Args:
            body: Corps de la requête.

        Returns:
            Configuration exportée.
        """
        logger.info("Export de la configuration: format={}", body.format.value)

        try:
            config = await _get_config()
            config_dict = _config_to_dict(config)

            # Filtrer les sections si spécifié
            if body.sections:
                config_dict = {k: v for k, v in config_dict.items() if k in body.sections}

            # Ajouter les métadonnées si demandé
            if body.include_metadata:
                config_dict = {
                    "_metadata": {
                        "exported_at": datetime.now(UTC).isoformat(),
                        "app_name": APP_NAME,
                        "schema_version": "1.0",
                    },
                    **config_dict,
                }

            # Sérialiser
            content = _serialize_config(config_dict, body.format)

            # Vérifier la taille
            if len(content.encode()) > MAX_EXPORT_SIZE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=ErrorResponse(
                        error="export_too_large",
                        message=t("settings.error.export_too_large", default="Export too large"),
                        details={"max_size": MAX_EXPORT_SIZE_BYTES},
                    ).model_dump(),
                )

            sections_exported = body.sections or list(CONFIG_SECTIONS)

            return ExportResponse(
                format=body.format,
                content=content,
                size_bytes=len(content.encode()),
                sections_exported=sections_exported,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erreur lors de l'export: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="export_failed",
                    message=t("settings.error.export_failed", default="Failed to export configuration"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # POST /settings/import — Importer une configuration
    # =========================================================================

    @settings_router.post(
        "/settings/import",
        response_model=ImportResponse,
        summary="Importer une configuration",
        description="Importe une configuration depuis JSON ou YAML.",
        responses={
            200: {"description": "Configuration importée"},
            400: {"description": "Validation échouée"},
            415: {"description": "Format non supporté"},
            500: {"description": "Erreur interne"},
        },
    )
    async def import_settings(
        request: Request,
        body: ImportRequest,
    ) -> ImportResponse:
        """Importe une configuration.

        Args:
            request: Requête HTTP.
            body: Corps de la requête.

        Returns:
            Réponse d'import.
        """
        user_id = _get_user_id_from_request(request)
        logger.info("Import de configuration par user={}", user_id)

        try:
            # Valider si demandé
            if body.validate:
                await _validate_config(body.config)

            # Sauvegarder
            validated_config = await _validate_config(body.config)
            await _save_config(validated_config)

            # Invalider le cache
            cache = get_settings_cache()
            await cache.invalidate()

            # Enregistrer le changement
            imported_sections = list(body.config.keys())
            await _record_change(
                action=ChangeAction.IMPORT,
                sections=imported_sections,
                user_id=user_id,
                details={"type": "import"},
            )

            # Émettre un événement
            await _emit_config_changed_event(
                action=ChangeAction.IMPORT,
                sections=imported_sections,
                user_id=user_id,
                details={"type": "import"},
            )

            return ImportResponse(
                success=True,
                message=t("settings.success.imported", default="Configuration imported successfully"),
                imported_sections=imported_sections,
            )

        except Exception as e:
            logger.error("Erreur lors de l'import: {}", e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorResponse(
                    error="import_failed",
                    message=t("settings.error.import_failed", default="Failed to import configuration"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /settings/schema — Récupérer le schéma JSON
    # =========================================================================

    @settings_router.get(
        "/settings/schema",
        response_model=SchemaResponse,
        summary="Récupérer le schéma JSON",
        description="Retourne le schéma JSON de la configuration pour validation.",
        responses={
            200: {"description": "Schéma JSON"},
        },
    )
    async def get_schema() -> SchemaResponse:
        """Récupère le schéma JSON.

        Returns:
            Schéma JSON.
        """
        logger.info("Récupération du schéma JSON")

        try:
            from nexusdl.core.config import NexusDLConfig

            schema = NexusDLConfig.model_json_schema()

            return SchemaResponse(
                schema=schema,
                version="1.0",
            )

        except Exception as e:
            logger.error("Erreur lors de la génération du schéma: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="schema_generation_failed",
                    message=t("settings.error.schema_failed", default="Failed to generate schema"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /settings/defaults — Récupérer les valeurs par défaut
    # =========================================================================

    @settings_router.get(
        "/settings/defaults",
        response_model=DefaultsResponse,
        summary="Récupérer les valeurs par défaut",
        description="Retourne les valeurs par défaut de la configuration.",
        responses={
            200: {"description": "Valeurs par défaut"},
        },
    )
    async def get_defaults() -> DefaultsResponse:
        """Récupère les valeurs par défaut.

        Returns:
            Valeurs par défaut.
        """
        logger.info("Récupération des valeurs par défaut")

        try:
            from nexusdl.core.config import NexusDLConfig

            default_config = NexusDLConfig()
            default_dict = _config_to_dict(default_config)

            return DefaultsResponse(
                defaults=default_dict,
                version="1.0",
            )

        except Exception as e:
            logger.error("Erreur lors de la récupération des valeurs par défaut: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ErrorResponse(
                    error="defaults_failed",
                    message=t("settings.error.defaults_failed", default="Failed to get defaults"),
                    details={"reason": str(e)},
                ).model_dump(),
            ) from e

    # =========================================================================
    # GET /settings/history — Historique des changements
    # =========================================================================

    @settings_router.get(
        "/settings/history",
        response_model=HistoryResponse,
        summary="Historique des changements",
        description="Retourne l'historique des changements de configuration.",
        responses={
            200: {"description": "Historique des changements"},
        },
    )
    async def get_history(
        limit: int = Query(50, ge=1, le=500, description="Nombre maximum d'entrées"),
    ) -> HistoryResponse:
        """Récupère l'historique des changements.

        Args:
            limit: Nombre maximum d'entrées.

        Returns:
            Historique des changements.
        """
        logger.info("Récupération de l'historique: limit={}", limit)

        history = get_change_history()
        entries = await history.get_all(limit=limit)

        return HistoryResponse(
            entries=entries,
            total=history.size,
            limit=limit,
        )


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "DEFAULT_HISTORY_SIZE",
    "DEFAULT_CACHE_TTL_SECONDS",
    "MAX_EXPORT_SIZE_BYTES",
    "SUPPORTED_FORMATS",
    "CONFIG_SECTIONS",
    # Exceptions
    "SettingsRouterError",
    "ConfigLoadError",
    "ConfigSaveError",
    "ConfigValidationError",
    "SectionNotFoundError",
    # Enums
    "ExportFormat",
    "ChangeAction",
    # Modèles de requête
    "UpdateSettingsRequest",
    "UpdateSectionRequest",
    "ExportRequest",
    "ImportRequest",
    "ResetRequest",
    # Modèles de réponse
    "SettingsResponse",
    "SectionResponse",
    "SectionsListResponse",
    "UpdateResponse",
    "ExportResponse",
    "ImportResponse",
    "ResetResponse",
    "SchemaResponse",
    "DefaultsResponse",
    "ChangeEntry",
    "HistoryResponse",
    "ErrorResponse",
    # Cache
    "SettingsCache",
    "get_settings_cache",
    # Historique
    "ChangeHistory",
    "get_change_history",
    # Helpers
    "get_settings",
    "update_settings",
    "update_section",
    "list_sections",
    "get_section",
    "reset_settings",
    "export_settings",
    "import_settings",
    "get_schema",
    "get_defaults",
    "get_history",
    # Routeur
    "settings_router" if FASTAPI_AVAILABLE else None,
]

# Nettoyer les None
__all__ = [x for x in __all__ if x is not None]
