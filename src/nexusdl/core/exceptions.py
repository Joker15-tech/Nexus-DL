"""Hiérarchie d'exceptions globale pour NexusDL.

Ce module définit la classe de base `NexusDLError` et une série d'exceptions
génériques utilisées dans toute l'application. Toutes les exceptions spécifiques
des sous-modules (downloader, image, library, etc.) héritent de cette hiérarchie,
garantissant une cohérence dans la gestion des erreurs.

**Caractéristiques** :
    - Classe de base `NexusDLError` avec contexte riche
    - Codes d'erreur machine-readable (enum `ErrorCode`)
    - Sérialisation JSON pour API REST / WebSocket / logs
    - Support de l'internationalisation (clés i18n)
    - Méthodes utilitaires (`to_dict()`, `with_context()`, `format()`)
    - Intégration avec le logger (auto-log à la création)
    - Helpers pour filtrer et formater les erreurs
    - Thread-safe et immutable (frozen après création)

**Hiérarchie** :
    NexusDLError (base)
        ├── ConfigurationError      : Erreurs de configuration
        ├── ValidationError         : Erreurs de validation
        ├── NetworkError            : Erreurs réseau
        │   ├── ConnectionError     : Échec de connexion
        │   ├── TimeoutError        : Timeout dépassé
        │   └── SSLError            : Erreur SSL/TLS
        ├── StorageError            : Erreurs de stockage
        │   ├── FileNotFoundError   : Fichier introuvable
        │   ├── PermissionError     : Permission refusée
        │   └── DiskFullError       : Disque plein
        ├── AuthenticationError     : Erreurs d'authentification
        │   ├── InvalidCredentialsError : Identifiants invalides
        │   └── TokenExpiredError   : Token expiré
        ├── NotFoundError           : Ressource introuvable
        ├── PermissionDeniedError   : Permission refusée
        ├── CancellationError       : Opération annulée
        ├── RateLimitError          : Limite de débit dépassée
        ├── ParserError             : Erreur de parsing
        ├── PackagingError          : Erreur d'empaquetage
        └── InternalError           : Erreur interne (bug)

**Codes d'erreur** :
    Chaque exception a un code unique (enum `ErrorCode`) pour identification
    machine-readable. Les codes sont groupés par domaine :
        - 1xxx : Configuration
        - 2xxx : Validation
        - 3xxx : Réseau
        - 4xxx : Stockage
        - 5xxx : Authentification
        - 6xxx : Ressources
        - 7xxx : Permissions
        - 8xxx : Opérations
        - 9xxx : Internes

Exemple d'utilisation :
    >>> from nexusdl.core.exceptions import (
    ...     NexusDLError,
    ...     ConfigurationError,
    ...     ErrorCode,
    ... )
    >>>
    >>> # Lever une exception avec contexte
    >>> raise ConfigurationError(
    ...     "Fichier de configuration invalide",
    ...     code=ErrorCode.CONFIG_INVALID,
    ...     context={"file": "config.yaml", "line": 42},
    ...     i18n_key="errors.config.invalid",
    ... )
    >>>
    >>> # Sérialiser pour API REST
    >>> try:
    ...     # ... opération ...
    ... except NexusDLError as e:
    ...     error_dict = e.to_dict()
    ...     return JSONResponse(status_code=400, content=error_dict)
    >>>
    >>> # Logger avec contexte enrichi
    >>> try:
    ...     # ... opération ...
    ... except NexusDLError as e:
    ...     logger.error("Erreur: {}", e.format())
    >>>
    >>> # Filtrer les erreurs NexusDL
    >>> if is_nexusdl_error(exception):
    ...     # Gestion spécifique
    ...     pass

Intégration :
    - Tous les sous-modules importent `NexusDLError` comme base
    - Le logger capture automatiquement les exceptions NexusDL
    - L'API REST sérialise les erreurs via `to_dict()`
    - L'i18n traduit les messages via `i18n_key`
    - Les interfaces affichent les erreurs formatées via `format()`
"""

from __future__ import annotations

import traceback
from datetime import UTC, datetime
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any, ClassVar, Final, Self

from loguru import logger


# ============================================================================
# CONSTANTES — Codes d'erreur par domaine
# ============================================================================


# Plage de codes par domaine
_CODE_RANGE_CONFIG: Final[tuple[int, int]] = (1000, 1999)
_CODE_RANGE_VALIDATION: Final[tuple[int, int]] = (2000, 2999)
_CODE_RANGE_NETWORK: Final[tuple[int, int]] = (3000, 3999)
_CODE_RANGE_STORAGE: Final[tuple[int, int]] = (4000, 4999)
_CODE_RANGE_AUTH: Final[tuple[int, int]] = (5000, 5999)
_CODE_RANGE_RESOURCES: Final[tuple[int, int]] = (6000, 6999)
_CODE_RANGE_PERMISSIONS: Final[tuple[int, int]] = (7000, 7999)
_CODE_RANGE_OPERATIONS: Final[tuple[int, int]] = (8000, 8999)
_CODE_RANGE_INTERNAL: Final[tuple[int, int]] = (9000, 9999)


# ============================================================================
# ENUMS — Codes d'erreur
# ============================================================================


class ErrorCode(IntEnum):
    """Codes d'erreur machine-readable pour NexusDL.

    Les codes sont groupés par domaine (1xxx = config, 2xxx = validation, etc.).
    Chaque exception a un code unique pour identification et traitement automatique.
    """

    # Codes génériques
    UNKNOWN = 0
    GENERIC_ERROR = 1

    # Configuration (1xxx)
    CONFIG_NOT_FOUND = 1001
    CONFIG_INVALID = 1002
    CONFIG_PARSE_ERROR = 1003
    CONFIG_MISSING_KEY = 1004
    CONFIG_TYPE_MISMATCH = 1005
    CONFIG_VALIDATION_FAILED = 1006

    # Validation (2xxx)
    VALIDATION_FAILED = 2001
    VALIDATION_TYPE_ERROR = 2002
    VALIDATION_RANGE_ERROR = 2003
    VALIDATION_FORMAT_ERROR = 2004
    VALIDATION_REQUIRED_FIELD = 2005
    VALIDATION_DUPLICATE = 2006

    # Réseau (3xxx)
    NETWORK_ERROR = 3001
    CONNECTION_FAILED = 3002
    CONNECTION_TIMEOUT = 3003
    CONNECTION_REFUSED = 3004
    DNS_ERROR = 3005
    SSL_ERROR = 3006
    HTTP_ERROR = 3007
    RATE_LIMITED = 3008
    PROXY_ERROR = 3009
    CLOUDFLARE_BLOCKED = 3010

    # Stockage (4xxx)
    STORAGE_ERROR = 4001
    FILE_NOT_FOUND = 4002
    FILE_EXISTS = 4003
    FILE_CORRUPTED = 4004
    DISK_FULL = 4005
    DIRECTORY_NOT_EMPTY = 4006
    DATABASE_ERROR = 4007
    DATABASE_LOCKED = 4008

    # Authentification (5xxx)
    AUTH_ERROR = 5001
    AUTH_INVALID_CREDENTIALS = 5002
    AUTH_TOKEN_EXPIRED = 5003
    AUTH_TOKEN_INVALID = 5004
    AUTH_SESSION_EXPIRED = 5005
    AUTH_FORBIDDEN = 5006
    AUTH_ACCOUNT_DISABLED = 5007
    AUTH_EMAIL_NOT_VERIFIED = 5008

    # Ressources (6xxx)
    NOT_FOUND = 6001
    MANGA_NOT_FOUND = 6002
    CHAPTER_NOT_FOUND = 6003
    SITE_NOT_FOUND = 6004
    USER_NOT_FOUND = 6005
    TASK_NOT_FOUND = 6006
    LIST_NOT_FOUND = 6007

    # Permissions (7xxx)
    PERMISSION_DENIED = 7001
    PERMISSION_READ_DENIED = 7002
    PERMISSION_WRITE_DENIED = 7003
    PERMISSION_EXECUTE_DENIED = 7004
    PERMISSION_ADMIN_REQUIRED = 7005

    # Opérations (8xxx)
    OPERATION_CANCELLED = 8001
    OPERATION_TIMEOUT = 8002
    OPERATION_IN_PROGRESS = 8003
    OPERATION_NOT_ALLOWED = 8004
    OPERATION_FAILED = 8005
    DOWNLOAD_FAILED = 8006
    PARSING_FAILED = 8007
    PACKAGING_FAILED = 8008
    CONVERSION_FAILED = 8009

    # Internes (9xxx)
    INTERNAL_ERROR = 9001
    NOT_IMPLEMENTED = 9002
    ASSERTION_FAILED = 9003
    DEPENDENCY_ERROR = 9004
    MIGRATION_ERROR = 9005

    @property
    def label(self) -> str:
        """Libellé humain du code d'erreur."""
        return _ERROR_CODE_LABELS.get(self, f"Erreur {self.value}")

    @property
    def domain(self) -> str:
        """Domaine du code d'erreur."""
        value = self.value
        if 1000 <= value <= 1999:
            return "configuration"
        if 2000 <= value <= 2999:
            return "validation"
        if 3000 <= value <= 3999:
            return "network"
        if 4000 <= value <= 4999:
            return "storage"
        if 5000 <= value <= 5999:
            return "authentication"
        if 6000 <= value <= 6999:
            return "resources"
        if 7000 <= value <= 7999:
            return "permissions"
        if 8000 <= value <= 8999:
            return "operations"
        if 9000 <= value <= 9999:
            return "internal"
        return "unknown"

    @property
    def http_status(self) -> int:
        """Code HTTP suggéré pour cette erreur (pour API REST)."""
        return _ERROR_CODE_HTTP_STATUS.get(self, 500)


# Labels humains pour les codes d'erreur
_ERROR_CODE_LABELS: Final[dict[ErrorCode, str]] = {
    ErrorCode.UNKNOWN: "Erreur inconnue",
    ErrorCode.GENERIC_ERROR: "Erreur générique",
    ErrorCode.CONFIG_NOT_FOUND: "Configuration introuvable",
    ErrorCode.CONFIG_INVALID: "Configuration invalide",
    ErrorCode.CONFIG_PARSE_ERROR: "Erreur de parsing de configuration",
    ErrorCode.CONFIG_MISSING_KEY: "Clé de configuration manquante",
    ErrorCode.CONFIG_TYPE_MISMATCH: "Type de configuration incorrect",
    ErrorCode.CONFIG_VALIDATION_FAILED: "Validation de configuration échouée",
    ErrorCode.VALIDATION_FAILED: "Validation échouée",
    ErrorCode.VALIDATION_TYPE_ERROR: "Erreur de type",
    ErrorCode.VALIDATION_RANGE_ERROR: "Valeur hors limites",
    ErrorCode.VALIDATION_FORMAT_ERROR: "Format invalide",
    ErrorCode.VALIDATION_REQUIRED_FIELD: "Champ requis manquant",
    ErrorCode.VALIDATION_DUPLICATE: "Valeur dupliquée",
    ErrorCode.NETWORK_ERROR: "Erreur réseau",
    ErrorCode.CONNECTION_FAILED: "Échec de connexion",
    ErrorCode.CONNECTION_TIMEOUT: "Timeout de connexion",
    ErrorCode.CONNECTION_REFUSED: "Connexion refusée",
    ErrorCode.DNS_ERROR: "Erreur DNS",
    ErrorCode.SSL_ERROR: "Erreur SSL/TLS",
    ErrorCode.HTTP_ERROR: "Erreur HTTP",
    ErrorCode.RATE_LIMITED: "Limite de débit dépassée",
    ErrorCode.PROXY_ERROR: "Erreur de proxy",
    ErrorCode.CLOUDFLARE_BLOCKED: "Bloqué par Cloudflare",
    ErrorCode.STORAGE_ERROR: "Erreur de stockage",
    ErrorCode.FILE_NOT_FOUND: "Fichier introuvable",
    ErrorCode.FILE_EXISTS: "Le fichier existe déjà",
    ErrorCode.FILE_CORRUPTED: "Fichier corrompu",
    ErrorCode.DISK_FULL: "Disque plein",
    ErrorCode.DIRECTORY_NOT_EMPTY: "Répertoire non vide",
    ErrorCode.DATABASE_ERROR: "Erreur de base de données",
    ErrorCode.DATABASE_LOCKED: "Base de données verrouillée",
    ErrorCode.AUTH_ERROR: "Erreur d'authentification",
    ErrorCode.AUTH_INVALID_CREDENTIALS: "Identifiants invalides",
    ErrorCode.AUTH_TOKEN_EXPIRED: "Token expiré",
    ErrorCode.AUTH_TOKEN_INVALID: "Token invalide",
    ErrorCode.AUTH_SESSION_EXPIRED: "Session expirée",
    ErrorCode.AUTH_FORBIDDEN: "Accès interdit",
    ErrorCode.AUTH_ACCOUNT_DISABLED: "Compte désactivé",
    ErrorCode.AUTH_EMAIL_NOT_VERIFIED: "Email non vérifié",
    ErrorCode.NOT_FOUND: "Ressource introuvable",
    ErrorCode.MANGA_NOT_FOUND: "Manga introuvable",
    ErrorCode.CHAPTER_NOT_FOUND: "Chapitre introuvable",
    ErrorCode.SITE_NOT_FOUND: "Site introuvable",
    ErrorCode.USER_NOT_FOUND: "Utilisateur introuvable",
    ErrorCode.TASK_NOT_FOUND: "Tâche introuvable",
    ErrorCode.LIST_NOT_FOUND: "Liste introuvable",
    ErrorCode.PERMISSION_DENIED: "Permission refusée",
    ErrorCode.PERMISSION_READ_DENIED: "Lecture interdite",
    ErrorCode.PERMISSION_WRITE_DENIED: "Écriture interdite",
    ErrorCode.PERMISSION_EXECUTE_DENIED: "Exécution interdite",
    ErrorCode.PERMISSION_ADMIN_REQUIRED: "Droits admin requis",
    ErrorCode.OPERATION_CANCELLED: "Opération annulée",
    ErrorCode.OPERATION_TIMEOUT: "Timeout d'opération",
    ErrorCode.OPERATION_IN_PROGRESS: "Opération en cours",
    ErrorCode.OPERATION_NOT_ALLOWED: "Opération non autorisée",
    ErrorCode.OPERATION_FAILED: "Échec de l'opération",
    ErrorCode.DOWNLOAD_FAILED: "Échec du téléchargement",
    ErrorCode.PARSING_FAILED: "Échec du parsing",
    ErrorCode.PACKAGING_FAILED: "Échec de l'empaquetage",
    ErrorCode.CONVERSION_FAILED: "Échec de la conversion",
    ErrorCode.INTERNAL_ERROR: "Erreur interne",
    ErrorCode.NOT_IMPLEMENTED: "Fonctionnalité non implémentée",
    ErrorCode.ASSERTION_FAILED: "Assertion échouée",
    ErrorCode.DEPENDENCY_ERROR: "Erreur de dépendance",
    ErrorCode.MIGRATION_ERROR: "Erreur de migration",
}

# Mapping codes d'erreur → status HTTP
_ERROR_CODE_HTTP_STATUS: Final[dict[ErrorCode, int]] = {
    # Configuration
    ErrorCode.CONFIG_NOT_FOUND: 404,
    ErrorCode.CONFIG_INVALID: 400,
    ErrorCode.CONFIG_PARSE_ERROR: 400,
    ErrorCode.CONFIG_MISSING_KEY: 400,
    ErrorCode.CONFIG_TYPE_MISMATCH: 400,
    ErrorCode.CONFIG_VALIDATION_FAILED: 400,
    # Validation
    ErrorCode.VALIDATION_FAILED: 400,
    ErrorCode.VALIDATION_TYPE_ERROR: 400,
    ErrorCode.VALIDATION_RANGE_ERROR: 400,
    ErrorCode.VALIDATION_FORMAT_ERROR: 400,
    ErrorCode.VALIDATION_REQUIRED_FIELD: 400,
    ErrorCode.VALIDATION_DUPLICATE: 409,
    # Réseau
    ErrorCode.NETWORK_ERROR: 502,
    ErrorCode.CONNECTION_FAILED: 502,
    ErrorCode.CONNECTION_TIMEOUT: 504,
    ErrorCode.CONNECTION_REFUSED: 502,
    ErrorCode.DNS_ERROR: 502,
    ErrorCode.SSL_ERROR: 502,
    ErrorCode.HTTP_ERROR: 502,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.PROXY_ERROR: 502,
    ErrorCode.CLOUDFLARE_BLOCKED: 403,
    # Stockage
    ErrorCode.STORAGE_ERROR: 500,
    ErrorCode.FILE_NOT_FOUND: 404,
    ErrorCode.FILE_EXISTS: 409,
    ErrorCode.FILE_CORRUPTED: 500,
    ErrorCode.DISK_FULL: 507,
    ErrorCode.DIRECTORY_NOT_EMPTY: 409,
    ErrorCode.DATABASE_ERROR: 500,
    ErrorCode.DATABASE_LOCKED: 409,
    # Auth
    ErrorCode.AUTH_ERROR: 401,
    ErrorCode.AUTH_INVALID_CREDENTIALS: 401,
    ErrorCode.AUTH_TOKEN_EXPIRED: 401,
    ErrorCode.AUTH_TOKEN_INVALID: 401,
    ErrorCode.AUTH_SESSION_EXPIRED: 401,
    ErrorCode.AUTH_FORBIDDEN: 403,
    ErrorCode.AUTH_ACCOUNT_DISABLED: 403,
    ErrorCode.AUTH_EMAIL_NOT_VERIFIED: 403,
    # Ressources
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.MANGA_NOT_FOUND: 404,
    ErrorCode.CHAPTER_NOT_FOUND: 404,
    ErrorCode.SITE_NOT_FOUND: 404,
    ErrorCode.USER_NOT_FOUND: 404,
    ErrorCode.TASK_NOT_FOUND: 404,
    ErrorCode.LIST_NOT_FOUND: 404,
    # Permissions
    ErrorCode.PERMISSION_DENIED: 403,
    ErrorCode.PERMISSION_READ_DENIED: 403,
    ErrorCode.PERMISSION_WRITE_DENIED: 403,
    ErrorCode.PERMISSION_EXECUTE_DENIED: 403,
    ErrorCode.PERMISSION_ADMIN_REQUIRED: 403,
    # Opérations
    ErrorCode.OPERATION_CANCELLED: 409,
    ErrorCode.OPERATION_TIMEOUT: 504,
    ErrorCode.OPERATION_IN_PROGRESS: 409,
    ErrorCode.OPERATION_NOT_ALLOWED: 403,
    ErrorCode.OPERATION_FAILED: 500,
    ErrorCode.DOWNLOAD_FAILED: 500,
    ErrorCode.PARSING_FAILED: 500,
    ErrorCode.PACKAGING_FAILED: 500,
    ErrorCode.CONVERSION_FAILED: 500,
    # Internes
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.NOT_IMPLEMENTED: 501,
    ErrorCode.ASSERTION_FAILED: 500,
    ErrorCode.DEPENDENCY_ERROR: 500,
    ErrorCode.MIGRATION_ERROR: 500,
}


# ============================================================================
# CLASSE DE BASE — NexusDLError
# ============================================================================


class NexusDLError(Exception):
    """Classe de base pour toutes les exceptions de NexusDL.

    Toutes les exceptions spécifiques du projet doivent hériter de cette classe
    pour garantir une gestion cohérente des erreurs dans toute l'application.

    **Caractéristiques** :
        - Code d'erreur machine-readable (ErrorCode)
        - Message localisable (i18n_key)
        - Contexte additionnel (dict)
        - Sérialisation JSON (to_dict)
        - Formatage humain (format)
        - Chaîne de causes (cause)

    Attributes:
        message: Message d'erreur principal.
        code: Code d'erreur (ErrorCode).
        context: Contexte additionnel (dict).
        i18n_key: Clé i18n pour traduction.
        cause: Exception originale (si wrapping).
        timestamp: Timestamp de création.
        details: Détails supplémentaires (dict).

    Example:
        >>> raise NexusDLError(
        ...     "Configuration invalide",
        ...     code=ErrorCode.CONFIG_INVALID,
        ...     context={"file": "config.yaml"},
        ...     i18n_key="errors.config.invalid",
        ... )
    """

    # Code d'erreur par défaut (peut être override par sous-classe)
    default_code: ClassVar[ErrorCode] = ErrorCode.GENERIC_ERROR

    # Clé i18n par défaut (peut être override par sous-classe)
    default_i18n_key: ClassVar[str | None] = None

    def __init__(
        self,
        message: str = "",
        *,
        code: ErrorCode | None = None,
        context: dict[str, Any] | None = None,
        i18n_key: str | None = None,
        cause: BaseException | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialise l'exception.

        Args:
            message: Message d'erreur principal.
            code: Code d'erreur (défaut: default_code de la classe).
            context: Contexte additionnel (fichier, ligne, etc.).
            i18n_key: Clé i18n pour traduction.
            cause: Exception originale (pour chaining).
            details: Détails supplémentaires (stack trace, etc.).
        """
        super().__init__(message)

        self._message = message
        self._code = code if code is not None else self.default_code
        self._context = dict(context) if context else {}
        self._i18n_key = i18n_key or self.default_i18n_key
        self._cause = cause
        self._details = dict(details) if details else {}
        self._timestamp = datetime.now(UTC)

        # Préserver la chaîne de causes
        if cause is not None:
            self.__cause__ = cause

    # --------------------------------------------------------------------
    # Propriétés
    # --------------------------------------------------------------------

    @property
    def message(self) -> str:
        """Message d'erreur principal."""
        return self._message

    @property
    def code(self) -> ErrorCode:
        """Code d'erreur."""
        return self._code

    @property
    def context(self) -> dict[str, Any]:
        """Contexte additionnel (copie)."""
        return dict(self._context)

    @property
    def i18n_key(self) -> str | None:
        """Clé i18n pour traduction."""
        return self._i18n_key

    @property
    def cause(self) -> BaseException | None:
        """Exception originale (cause)."""
        return self._cause

    @property
    def details(self) -> dict[str, Any]:
        """Détails supplémentaires (copie)."""
        return dict(self._details)

    @property
    def timestamp(self) -> datetime:
        """Timestamp de création."""
        return self._timestamp

    @property
    def error_code(self) -> int:
        """Code d'erreur numérique."""
        return self._code.value

    @property
    def error_label(self) -> str:
        """Libellé humain du code d'erreur."""
        return self._code.label

    @property
    def error_domain(self) -> str:
        """Domaine du code d'erreur."""
        return self._code.domain

    @property
    def http_status(self) -> int:
        """Code HTTP suggéré pour cette erreur."""
        return self._code.http_status

    @property
    def is_recoverable(self) -> bool:
        """Indique si l'erreur est potentiellement récupérable.

        Les erreurs réseau, timeout, rate limit sont généralement récupérables.
        Les erreurs de config, validation, permissions ne le sont pas.
        """
        return self._code in {
            ErrorCode.NETWORK_ERROR,
            ErrorCode.CONNECTION_FAILED,
            ErrorCode.CONNECTION_TIMEOUT,
            ErrorCode.RATE_LIMITED,
            ErrorCode.OPERATION_TIMEOUT,
            ErrorCode.DATABASE_LOCKED,
            ErrorCode.CLOUDFLARE_BLOCKED,
        }

    @property
    def is_user_error(self) -> bool:
        """Indique si l'erreur est causée par l'utilisateur.

        Les erreurs de validation, config, auth sont des erreurs utilisateur.
        Les erreurs internes, réseau, stockage ne le sont pas.
        """
        return self._code.domain in ("configuration", "validation", "authentication")

    # --------------------------------------------------------------------
    # Méthodes — Contexte
    # --------------------------------------------------------------------

    def with_context(self, **kwargs: Any) -> Self:
        """Retourne une nouvelle exception avec contexte additionnel.

        Args:
            **kwargs: Paires clé-valeur à ajouter au contexte.

        Returns:
            Nouvelle instance avec contexte enrichi.

        Example:
            >>> error = NexusDLError("Erreur").with_context(file="test.txt")
        """
        new_context = dict(self._context)
        new_context.update(kwargs)
        return self.__class__(
            message=self._message,
            code=self._code,
            context=new_context,
            i18n_key=self._i18n_key,
            cause=self._cause,
            details=self._details,
        )

    def with_details(self, **kwargs: Any) -> Self:
        """Retourne une nouvelle exception avec détails additionnels.

        Args:
            **kwargs: Paires clé-valeur à ajouter aux détails.

        Returns:
            Nouvelle instance avec détails enrichis.
        """
        new_details = dict(self._details)
        new_details.update(kwargs)
        return self.__class__(
            message=self._message,
            code=self._code,
            context=self._context,
            i18n_key=self._i18n_key,
            cause=self._cause,
            details=new_details,
        )

    # --------------------------------------------------------------------
    # Méthodes — Sérialisation
    # --------------------------------------------------------------------

    def to_dict(
        self,
        *,
        include_traceback: bool = False,
        include_context: bool = True,
        include_details: bool = True,
    ) -> dict[str, Any]:
        """Sérialise l'exception en dictionnaire (pour JSON/API).

        Args:
            include_traceback: Inclure la stack trace.
            include_context: Inclure le contexte.
            include_details: Inclure les détails.

        Returns:
            Dictionnaire sérialisable.

        Example:
            >>> error_dict = error.to_dict()
            >>> print(error_dict["code"])
            1002
        """
        result: dict[str, Any] = {
            "error": {
                "type": self.__class__.__name__,
                "code": self._code.value,
                "code_name": self._code.name,
                "label": self._code.label,
                "domain": self._code.domain,
                "message": self._message,
                "http_status": self._code.http_status,
                "is_recoverable": self.is_recoverable,
                "is_user_error": self.is_user_error,
                "timestamp": self._timestamp.isoformat(),
            }
        }

        if self._i18n_key:
            result["error"]["i18n_key"] = self._i18n_key

        if include_context and self._context:
            result["error"]["context"] = self._context

        if include_details and self._details:
            result["error"]["details"] = self._details

        if self._cause is not None:
            result["error"]["cause"] = {
                "type": self._cause.__class__.__name__,
                "message": str(self._cause),
            }

        if include_traceback:
            result["error"]["traceback"] = "".join(
                traceback.format_exception(
                    type(self),
                    self,
                    self.__traceback__,
                )
            )

        return result

    def to_json(
        self,
        *,
        include_traceback: bool = False,
        pretty: bool = False,
    ) -> str:
        """Sérialise l'exception en JSON.

        Args:
            include_traceback: Inclure la stack trace.
            pretty: Formater avec indentation.

        Returns:
            Chaîne JSON.
        """
        import json

        data = self.to_dict(include_traceback=include_traceback)
        if pretty:
            return json.dumps(data, indent=2, ensure_ascii=False)
        return json.dumps(data, ensure_ascii=False)

    # --------------------------------------------------------------------
    # Méthodes — Formatage
    # --------------------------------------------------------------------

    def format(
        self,
        *,
        include_code: bool = True,
        include_context: bool = True,
        include_cause: bool = True,
        verbose: bool = False,
    ) -> str:
        """Formate l'exception pour affichage humain.

        Args:
            include_code: Inclure le code d'erreur.
            include_context: Inclure le contexte.
            include_cause: Inclure la cause.
            verbose: Mode verbeux (avec stack trace).

        Returns:
            Chaîne formatée.

        Example:
            >>> print(error.format())
            [1002] Configuration invalide (file=config.yaml)
        """
        parts: list[str] = []

        # Code d'erreur
        if include_code:
            parts.append(f"[{self._code.value}]")

        # Message
        parts.append(self._message)

        # Contexte
        if include_context and self._context:
            context_str = ", ".join(
                f"{k}={v!r}" for k, v in self._context.items()
            )
            parts.append(f"({context_str})")

        # Cause
        if include_cause and self._cause is not None:
            parts.append(f"caused by: {self._cause.__class__.__name__}: {self._cause}")

        result = " ".join(parts)

        # Verbose : ajouter stack trace
        if verbose and self.__traceback__:
            result += "\n" + "".join(
                traceback.format_exception(
                    type(self),
                    self,
                    self.__traceback__,
                )
            )

        return result

    def format_short(self) -> str:
        """Formate l'exception en version courte.

        Returns:
            Chaîne courte (code + message).
        """
        return f"[{self._code.value}] {self._message}"

    def format_for_log(self) -> str:
        """Formate l'exception pour logging.

        Returns:
            Chaîne formatée pour les logs.
        """
        parts = [
            f"type={self.__class__.__name__}",
            f"code={self._code.value}",
            f"domain={self._code.domain}",
            f"message={self._message!r}",
        ]
        if self._context:
            parts.append(f"context={self._context}")
        if self._cause is not None:
            parts.append(f"cause={self._cause.__class__.__name__}")
        return " | ".join(parts)

    # --------------------------------------------------------------------
    # Méthodes — Logging
    # --------------------------------------------------------------------

    def log(
        self,
        *,
        level: str = "ERROR",
        include_traceback: bool = True,
        module: str | None = None,
    ) -> None:
        """Log l'exception via Loguru.

        Args:
            level: Niveau de log (DEBUG, INFO, WARNING, ERROR, CRITICAL).
            include_traceback: Inclure la stack trace.
            module: Module source (pour contexte).
        """
        log = logger.bind(module=module) if module else logger

        log_method = getattr(log, level.lower(), log.error)

        if include_traceback and self.__traceback__:
            log_method.opt(exception=self).error(
                "{}: {}",
                self.__class__.__name__,
                self._message,
            )
        else:
            log_method.error(
                "{} [{}]: {}",
                self.__class__.__name__,
                self._code.value,
                self._message,
            )

    # --------------------------------------------------------------------
    # Méthodes — Représentation
    # --------------------------------------------------------------------

    def __str__(self) -> str:
        """Représentation en chaîne (message simple)."""
        if self._context:
            context_str = ", ".join(
                f"{k}={v!r}" for k, v in self._context.items()
            )
            return f"{self._message} ({context_str})"
        return self._message

    def __repr__(self) -> str:
        """Représentation détaillée."""
        return (
            f"<{self.__class__.__name__} "
            f"code={self._code.value} "
            f"message={self._message!r}>"
        )

    # --------------------------------------------------------------------
    # Méthodes — Comparaison
    # --------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        """Comparaison d'égalité (basée sur type, code, message)."""
        if not isinstance(other, NexusDLError):
            return False
        return (
            self.__class__ == other.__class__
            and self._code == other._code
            and self._message == other._message
        )

    def __hash__(self) -> int:
        """Hash (basé sur type, code, message)."""
        return hash((self.__class__.__name__, self._code, self._message))


# ============================================================================
# EXCEPTIONS GÉNÉRIQUES — Configuration
# ============================================================================


class ConfigurationError(NexusDLError):
    """Erreur liée à la configuration de l'application.

    Levée lorsque la configuration est invalide, manquante, ou incohérente.
    """

    default_code: ClassVar[ErrorCode] = ErrorCode.CONFIG_INVALID
    default_i18n_key: ClassVar[str | None] = "errors.config.invalid"


class ConfigNotFoundError(ConfigurationError):
    """Fichier de configuration introuvable."""

    default_code: ClassVar[ErrorCode] = ErrorCode.CONFIG_NOT_FOUND
    default_i18n_key: ClassVar[str | None] = "errors.config.not_found"


class ConfigParseError(ConfigurationError):
    """Erreur de parsing du fichier de configuration."""

    default_code: ClassVar[ErrorCode] = ErrorCode.CONFIG_PARSE_ERROR
    default_i18n_key: ClassVar[str | None] = "errors.config.parse_error"


# ============================================================================
# EXCEPTIONS GÉNÉRIQUES — Validation
# ============================================================================


class ValidationError(NexusDLError):
    """Erreur de validation d'entrée utilisateur ou de données.

    Levée lorsque des données ne respectent pas les contraintes attendues.
    """

    default_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_FAILED
    default_i18n_key: ClassVar[str | None] = "errors.validation.failed"


class InvalidInputError(ValidationError):
    """Entrée utilisateur invalide."""

    default_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_FORMAT_ERROR
    default_i18n_key: ClassVar[str | None] = "errors.validation.format_error"


class MissingFieldError(ValidationError):
    """Champ requis manquant."""

    default_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_REQUIRED_FIELD
    default_i18n_key: ClassVar[str | None] = "errors.validation.required_field"


class DuplicateError(ValidationError):
    """Valeur dupliquée (contrainte d'unicité)."""

    default_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_DUPLICATE
    default_i18n_key: ClassVar[str | None] = "errors.validation.duplicate"


# ============================================================================
# EXCEPTIONS GÉNÉRIQUES — Réseau
# ============================================================================


class NetworkError(NexusDLError):
    """Erreur liée au réseau (HTTP, connexion, DNS, etc.).

    Levée lorsqu'une opération réseau échoue.
    """

    default_code: ClassVar[ErrorCode] = ErrorCode.NETWORK_ERROR
    default_i18n_key: ClassVar[str | None] = "errors.network.error"


class ConnectionError(NetworkError):
    """Échec de connexion à un serveur."""

    default_code: ClassVar[ErrorCode] = ErrorCode.CONNECTION_FAILED
    default_i18n_key: ClassVar[str | None] = "errors.network.connection_failed"


class NetworkTimeoutError(NetworkError):
    """Timeout lors d'une opération réseau."""

    default_code: ClassVar[ErrorCode] = ErrorCode.CONNECTION_TIMEOUT
    default_i18n_key: ClassVar[str | None] = "errors.network.timeout"


class ConnectionRefusedError(NetworkError):
    """Connexion refusée par le serveur."""

    default_code: ClassVar[ErrorCode] = ErrorCode.CONNECTION_REFUSED
    default_i18n_key: ClassVar[str | None] = "errors.network.connection_refused"


class DnsError(NetworkError):
    """Erreur de résolution DNS."""

    default_code: ClassVar[ErrorCode] = ErrorCode.DNS_ERROR
    default_i18n_key: ClassVar[str | None] = "errors.network.dns_error"


class SSLError(NetworkError):
    """Erreur SSL/TLS."""

    default_code: ClassVar[ErrorCode] = ErrorCode.SSL_ERROR
    default_i18n_key: ClassVar[str | None] = "errors.network.ssl_error"


class HttpError(NetworkError):
    """Erreur HTTP (status code >= 400)."""

    default_code: ClassVar[ErrorCode] = ErrorCode.HTTP_ERROR
    default_i18n_key: ClassVar[str | None] = "errors.network.http_error"

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialise l'erreur HTTP.

        Args:
            message: Message d'erreur.
            status_code: Code HTTP de la réponse.
            **kwargs: Arguments supplémentaires.
        """
        super().__init__(message, **kwargs)
        if status_code is not None:
            self._context["status_code"] = status_code

    @property
    def status_code(self) -> int | None:
        """Code HTTP de la réponse."""
        return self._context.get("status_code")


class RateLimitError(NetworkError):
    """Limite de débit dépassée (HTTP 429)."""

    default_code: ClassVar[ErrorCode] = ErrorCode.RATE_LIMITED
    default_i18n_key: ClassVar[str | None] = "errors.network.rate_limited"

    def __init__(
        self,
        message: str = "",
        *,
        retry_after: float | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialise l'erreur de rate limit.

        Args:
            message: Message d'erreur.
            retry_after: Temps d'attente recommandé (secondes).
            **kwargs: Arguments supplémentaires.
        """
        super().__init__(message, **kwargs)
        if retry_after is not None:
            self._context["retry_after"] = retry_after

    @property
    def retry_after(self) -> float | None:
        """Temps d'attente recommandé (secondes)."""
        return self._context.get("retry_after")


class ProxyError(NetworkError):
    """Erreur liée à un proxy."""

    default_code: ClassVar[ErrorCode] = ErrorCode.PROXY_ERROR
    default_i18n_key: ClassVar[str | None] = "errors.network.proxy_error"


class CloudflareBlockedError(NetworkError):
    """Requête bloquée par Cloudflare."""

    default_code: ClassVar[ErrorCode] = ErrorCode.CLOUDFLARE_BLOCKED
    default_i18n_key: ClassVar[str | None] = "errors.network.cloudflare_blocked"


# ============================================================================
# EXCEPTIONS GÉNÉRIQUES — Stockage
# ============================================================================


class StorageError(NexusDLError):
    """Erreur liée au stockage (fichiers, BDD, disque)."""

    default_code: ClassVar[ErrorCode] = ErrorCode.STORAGE_ERROR
    default_i18n_key: ClassVar[str | None] = "errors.storage.error"


class FileNotFoundError(StorageError):
    """Fichier introuvable."""

    default_code: ClassVar[ErrorCode] = ErrorCode.FILE_NOT_FOUND
    default_i18n_key: ClassVar[str | None] = "errors.storage.file_not_found"


class FileExistsError(StorageError):
    """Le fichier existe déjà."""

    default_code: ClassVar[ErrorCode] = ErrorCode.FILE_EXISTS
    default_i18n_key: ClassVar[str | None] = "errors.storage.file_exists"


class FileCorruptedError(StorageError):
    """Fichier corrompu ou illisible."""

    default_code: ClassVar[ErrorCode] = ErrorCode.FILE_CORRUPTED
    default_i18n_key: ClassVar[str | None] = "errors.storage.file_corrupted"


class DiskFullError(StorageError):
    """Disque plein."""

    default_code: ClassVar[ErrorCode] = ErrorCode.DISK_FULL
    default_i18n_key: ClassVar[str | None] = "errors.storage.disk_full"


class DatabaseError(StorageError):
    """Erreur de base de données."""

    default_code: ClassVar[ErrorCode] = ErrorCode.DATABASE_ERROR
    default_i18n_key: ClassVar[str | None] = "errors.storage.database_error"


class DatabaseLockedError(DatabaseError):
    """Base de données verrouillée."""

    default_code: ClassVar[ErrorCode] = ErrorCode.DATABASE_LOCKED
    default_i18n_key: ClassVar[str | None] = "errors.storage.database_locked"


# ============================================================================
# EXCEPTIONS GÉNÉRIQUES — Authentification
# ============================================================================


class AuthenticationError(NexusDLError):
    """Erreur d'authentification."""

    default_code: ClassVar[ErrorCode] = ErrorCode.AUTH_ERROR
    default_i18n_key: ClassVar[str | None] = "errors.auth.error"


class InvalidCredentialsError(AuthenticationError):
    """Identifiants invalides."""

    default_code: ClassVar[ErrorCode] = ErrorCode.AUTH_INVALID_CREDENTIALS
    default_i18n_key: ClassVar[str | None] = "errors.auth.invalid_credentials"


class TokenExpiredError(AuthenticationError):
    """Token expiré."""

    default_code: ClassVar[ErrorCode] = ErrorCode.AUTH_TOKEN_EXPIRED
    default_i18n_key: ClassVar[str | None] = "errors.auth.token_expired"


class TokenInvalidError(AuthenticationError):
    """Token invalide."""

    default_code: ClassVar[ErrorCode] = ErrorCode.AUTH_TOKEN_INVALID
    default_i18n_key: ClassVar[str | None] = "errors.auth.token_invalid"


class SessionExpiredError(AuthenticationError):
    """Session expirée."""

    default_code: ClassVar[ErrorCode] = ErrorCode.AUTH_SESSION_EXPIRED
    default_i18n_key: ClassVar[str | None] = "errors.auth.session_expired"


class ForbiddenError(AuthenticationError):
    """Accès interdit (403)."""

    default_code: ClassVar[ErrorCode] = ErrorCode.AUTH_FORBIDDEN
    default_i18n_key: ClassVar[str | None] = "errors.auth.forbidden"


class AccountDisabledError(AuthenticationError):
    """Compte désactivé."""

    default_code: ClassVar[ErrorCode] = ErrorCode.AUTH_ACCOUNT_DISABLED
    default_i18n_key: ClassVar[str | None] = "errors.auth.account_disabled"


# ============================================================================
# EXCEPTIONS GÉNÉRIQUES — Ressources
# ============================================================================


class NotFoundError(NexusDLError):
    """Ressource introuvable (404)."""

    default_code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND
    default_i18n_key: ClassVar[str | None] = "errors.resources.not_found"

    def __init__(
        self,
        message: str = "",
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialise l'erreur de ressource introuvable.

        Args:
            message: Message d'erreur.
            resource_type: Type de ressource (manga, chapter, etc.).
            resource_id: ID de la ressource.
            **kwargs: Arguments supplémentaires.
        """
        super().__init__(message, **kwargs)
        if resource_type:
            self._context["resource_type"] = resource_type
        if resource_id:
            self._context["resource_id"] = resource_id


class MangaNotFoundError(NotFoundError):
    """Manga introuvable."""

    default_code: ClassVar[ErrorCode] = ErrorCode.MANGA_NOT_FOUND
    default_i18n_key: ClassVar[str | None] = "errors.resources.manga_not_found"


class ChapterNotFoundError(NotFoundError):
    """Chapitre introuvable."""

    default_code: ClassVar[ErrorCode] = ErrorCode.CHAPTER_NOT_FOUND
    default_i18n_key: ClassVar[str | None] = "errors.resources.chapter_not_found"


class SiteNotFoundError(NotFoundError):
    """Site introuvable."""

    default_code: ClassVar[ErrorCode] = ErrorCode.SITE_NOT_FOUND
    default_i18n_key: ClassVar[str | None] = "errors.resources.site_not_found"


class UserNotFoundError(NotFoundError):
    """Utilisateur introuvable."""

    default_code: ClassVar[ErrorCode] = ErrorCode.USER_NOT_FOUND
    default_i18n_key: ClassVar[str | None] = "errors.resources.user_not_found"


class TaskNotFoundError(NotFoundError):
    """Tâche introuvable."""

    default_code: ClassVar[ErrorCode] = ErrorCode.TASK_NOT_FOUND
    default_i18n_key: ClassVar[str | None] = "errors.resources.task_not_found"


# ============================================================================
# EXCEPTIONS GÉNÉRIQUES — Permissions
# ============================================================================


class PermissionDeniedError(NexusDLError):
    """Permission refusée (403)."""

    default_code: ClassVar[ErrorCode] = ErrorCode.PERMISSION_DENIED
    default_i18n_key: ClassVar[str | None] = "errors.permissions.denied"


class AdminRequiredError(PermissionDeniedError):
    """Droits administrateur requis."""

    default_code: ClassVar[ErrorCode] = ErrorCode.PERMISSION_ADMIN_REQUIRED
    default_i18n_key: ClassVar[str | None] = "errors.permissions.admin_required"


# ============================================================================
# EXCEPTIONS GÉNÉRIQUES — Opérations
# ============================================================================


class OperationCancelledError(NexusDLError):
    """Opération annulée par l'utilisateur."""

    default_code: ClassVar[ErrorCode] = ErrorCode.OPERATION_CANCELLED
    default_i18n_key: ClassVar[str | None] = "errors.operations.cancelled"


class OperationTimeoutError(NexusDLError):
    """Timeout d'opération."""

    default_code: ClassVar[ErrorCode] = ErrorCode.OPERATION_TIMEOUT
    default_i18n_key: ClassVar[str | None] = "errors.operations.timeout"


class OperationFailedError(NexusDLError):
    """Échec d'opération."""

    default_code: ClassVar[ErrorCode] = ErrorCode.OPERATION_FAILED
    default_i18n_key: ClassVar[str | None] = "errors.operations.failed"


class DownloadFailedError(OperationFailedError):
    """Échec de téléchargement."""

    default_code: ClassVar[ErrorCode] = ErrorCode.DOWNLOAD_FAILED
    default_i18n_key: ClassVar[str | None] = "errors.operations.download_failed"


class ParsingFailedError(OperationFailedError):
    """Échec de parsing."""

    default_code: ClassVar[ErrorCode] = ErrorCode.PARSING_FAILED
    default_i18n_key: ClassVar[str | None] = "errors.operations.parsing_failed"


class PackagingFailedError(OperationFailedError):
    """Échec d'empaquetage."""

    default_code: ClassVar[ErrorCode] = ErrorCode.PACKAGING_FAILED
    default_i18n_key: ClassVar[str | None] = "errors.operations.packaging_failed"


class ConversionFailedError(OperationFailedError):
    """Échec de conversion."""

    default_code: ClassVar[ErrorCode] = ErrorCode.CONVERSION_FAILED
    default_i18n_key: ClassVar[str | None] = "errors.operations.conversion_failed"


# ============================================================================
# EXCEPTIONS GÉNÉRIQUES — Internes
# ============================================================================


class InternalError(NexusDLError):
    """Erreur interne (bug de l'application)."""

    default_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    default_i18n_key: ClassVar[str | None] = "errors.internal.error"


class NotImplementedError(InternalError):
    """Fonctionnalité non implémentée."""

    default_code: ClassVar[ErrorCode] = ErrorCode.NOT_IMPLEMENTED
    default_i18n_key: ClassVar[str | None] = "errors.internal.not_implemented"


class DependencyError(InternalError):
    """Erreur de dépendance (librairie manquante, etc.)."""

    default_code: ClassVar[ErrorCode] = ErrorCode.DEPENDENCY_ERROR
    default_i18n_key: ClassVar[str | None] = "errors.internal.dependency_error"


class MigrationError(InternalError):
    """Erreur de migration (BDD, config, etc.)."""

    default_code: ClassVar[ErrorCode] = ErrorCode.MIGRATION_ERROR
    default_i18n_key: ClassVar[str | None] = "errors.internal.migration_error"


# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================


def is_nexusdl_error(exception: BaseException) -> bool:
    """Vérifie si une exception est une NexusDLError.

    Args:
        exception: Exception à vérifier.

    Returns:
        True si c'est une NexusDLError.

    Example:
        >>> try:
        ...     # ...
        ... except Exception as e:
        ...     if is_nexusdl_error(e):
        ...         # Gestion spécifique
        ...         pass
    """
    return isinstance(exception, NexusDLError)


def get_error_code(exception: BaseException) -> ErrorCode | None:
    """Extrait le code d'erreur d'une exception.

    Args:
        exception: Exception à analyser.

    Returns:
        ErrorCode si c'est une NexusDLError, None sinon.
    """
    if isinstance(exception, NexusDLError):
        return exception.code
    return None


def format_error(
    exception: BaseException,
    *,
    verbose: bool = False,
) -> str:
    """Formate une exception pour affichage.

    Si c'est une NexusDLError, utilise son format().
    Sinon, utilise le formatage standard.

    Args:
        exception: Exception à formater.
        verbose: Mode verbeux (avec stack trace).

    Returns:
        Chaîne formatée.
    """
    if isinstance(exception, NexusDLError):
        return exception.format(verbose=verbose)

    # Exception standard
    result = f"{exception.__class__.__name__}: {exception}"
    if verbose and exception.__traceback__:
        result += "\n" + "".join(
            traceback.format_exception(
                type(exception),
                exception,
                exception.__traceback__,
            )
        )
    return result


def error_to_dict(
    exception: BaseException,
    *,
    include_traceback: bool = False,
) -> dict[str, Any]:
    """Convertit une exception en dictionnaire.

    Si c'est une NexusDLError, utilise son to_dict().
    Sinon, construit un dict basique.

    Args:
        exception: Exception à convertir.
        include_traceback: Inclure la stack trace.

    Returns:
        Dictionnaire sérialisable.
    """
    if isinstance(exception, NexusDLError):
        return exception.to_dict(include_traceback=include_traceback)

    # Exception standard
    result: dict[str, Any] = {
        "error": {
            "type": exception.__class__.__name__,
            "message": str(exception),
            "timestamp": datetime.now(UTC).isoformat(),
        }
    }

    if include_traceback and exception.__traceback__:
        result["error"]["traceback"] = "".join(
            traceback.format_exception(
                type(exception),
                exception,
                exception.__traceback__,
            )
        )

    return result


def wrap_exception(
    exception: BaseException,
    *,
    message: str | None = None,
    code: ErrorCode | None = None,
    context: dict[str, Any] | None = None,
) -> NexusDLError:
    """Enveloppe une exception standard en NexusDLError.

    Utile pour convertir des exceptions tierces (httpx, sqlite3, etc.)
    en exceptions NexusDL avec contexte riche.

    Args:
        exception: Exception à envelopper.
        message: Message personnalisé (défaut: message original).
        code: Code d'erreur (défaut: détection automatique).
        context: Contexte additionnel.

    Returns:
        Instance de NexusDLError enveloppant l'exception.

    Example:
        >>> try:
        ...     response = await httpx.get(url)
        ...     response.raise_for_status()
        ... except httpx.HTTPError as e:
        ...     raise wrap_exception(e, code=ErrorCode.HTTP_ERROR)
    """
    # Détection automatique du code si non fourni
    if code is None:
        code = _detect_error_code(exception)

    # Message par défaut
    if message is None:
        message = str(exception) or exception.__class__.__name__

    # Contexte
    ctx = dict(context) if context else {}
    ctx["original_type"] = exception.__class__.__name__

    return NexusDLError(
        message=message,
        code=code,
        context=ctx,
        cause=exception,
    )


def _detect_error_code(exception: BaseException) -> ErrorCode:
    """Détecte automatiquement le code d'erreur approprié.

    Args:
        exception: Exception à analyser.

    Returns:
        ErrorCode détecté.
    """
    # Exceptions Python standards
    if isinstance(exception, TimeoutError):
        return ErrorCode.OPERATION_TIMEOUT
    if isinstance(exception, PermissionError):
        return ErrorCode.PERMISSION_DENIED
    if isinstance(exception, FileNotFoundError):
        return ErrorCode.FILE_NOT_FOUND
    if isinstance(exception, FileExistsError):
        return ErrorCode.FILE_EXISTS

    # Exceptions courantes
    class_name = exception.__class__.__name__.lower()

    if "timeout" in class_name:
        return ErrorCode.OPERATION_TIMEOUT
    if "connection" in class_name:
        return ErrorCode.CONNECTION_FAILED
    if "network" in class_name:
        return ErrorCode.NETWORK_ERROR
    if "auth" in class_name or "credential" in class_name:
        return ErrorCode.AUTH_ERROR
    if "permission" in class_name or "forbidden" in class_name:
        return ErrorCode.PERMISSION_DENIED
    if "notfound" in class_name or "not_found" in class_name:
        return ErrorCode.NOT_FOUND
    if "validation" in class_name or "invalid" in class_name:
        return ErrorCode.VALIDATION_FAILED
    if "config" in class_name:
        return ErrorCode.CONFIG_INVALID
    if "database" in class_name or "sqlite" in class_name:
        return ErrorCode.DATABASE_ERROR
    if "disk" in class_name or "space" in class_name:
        return ErrorCode.DISK_FULL
    if "ssl" in class_name or "tls" in class_name:
        return ErrorCode.SSL_ERROR
    if "dns" in class_name:
        return ErrorCode.DNS_ERROR
    if "rate" in class_name or "limit" in class_name:
        return ErrorCode.RATE_LIMITED
    if "proxy" in class_name:
        return ErrorCode.PROXY_ERROR
    if "cloudflare" in class_name:
        return ErrorCode.CLOUDFLARE_BLOCKED

    return ErrorCode.GENERIC_ERROR


def translate_error(
    exception: NexusDLError,
    *,
    language: str | None = None,
) -> str:
    """Traduit le message d'erreur via i18n.

    Si l'exception a une clé i18n, utilise le système de traduction.
    Sinon, retourne le message original.

    Args:
        exception: Exception à traduire.
        language: Langue cible (défaut: langue courante).

    Returns:
        Message traduit.
    """
    if exception.i18n_key is None:
        return exception.message

    try:
        from nexusdl.core.i18n import t
        return t(exception.i18n_key, language=language, **exception.context)
    except Exception:
        # Fallback si i18n non disponible
        return exception.message


def log_exception(
    exception: BaseException,
    *,
    level: str = "ERROR",
    include_traceback: bool = True,
    module: str | None = None,
) -> None:
    """Log une exception de manière structurée.

    Args:
        exception: Exception à logger.
        level: Niveau de log.
        include_traceback: Inclure la stack trace.
        module: Module source.
    """
    if isinstance(exception, NexusDLError):
        exception.log(level=level, include_traceback=include_traceback, module=module)
    else:
        log = logger.bind(module=module) if module else logger
        log_method = getattr(log, level.lower(), log.error)

        if include_traceback and exception.__traceback__:
            log_method.opt(exception=exception).error(
                "{}: {}",
                exception.__class__.__name__,
                exception,
            )
        else:
            log_method.error(
                "{}: {}",
                exception.__class__.__name__,
                exception,
            )


# ============================================================================
# DÉCORATEURS — Gestion automatique des erreurs
# ============================================================================


def catch_and_log(
    *,
    level: str = "ERROR",
    reraise: bool = True,
    wrap_as: type[NexusDLError] | None = None,
    default_code: ErrorCode | None = None,
) -> Any:
    """Décorateur pour attraper et logger automatiquement les exceptions.

    Args:
        level: Niveau de log.
        reraise: Si True, relance l'exception après logging.
        wrap_as: Classe NexusDLError pour envelopper les exceptions standard.
        default_code: Code d'erreur par défaut si wrap_as utilisé.

    Returns:
        Décorateur.

    Example:
        >>> @catch_and_log(level="ERROR", wrap_as=NetworkError)
        ... async def fetch_data():
        ...     return await httpx.get(url)
    """
    import functools

    def _decorator(func: Any) -> Any:
        import asyncio

        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await func(*args, **kwargs)
                except NexusDLError as e:
                    log_exception(e, level=level)
                    if reraise:
                        raise
                    return None
                except Exception as e:
                    if wrap_as is not None:
                        wrapped = wrap_exception(
                            e,
                            code=default_code or wrap_as.default_code,
                        )
                        log_exception(wrapped, level=level)
                        if reraise:
                            raise wrapped from e
                        return None
                    else:
                        log_exception(e, level=level)
                        if reraise:
                            raise
                        return None

            return _async_wrapper

        else:

            @functools.wraps(func)
            def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return func(*args, **kwargs)
                except NexusDLError as e:
                    log_exception(e, level=level)
                    if reraise:
                        raise
                    return None
                except Exception as e:
                    if wrap_as is not None:
                        wrapped = wrap_exception(
                            e,
                            code=default_code or wrap_as.default_code,
                        )
                        log_exception(wrapped, level=level)
                        if reraise:
                            raise wrapped from e
                        return None
                    else:
                        log_exception(e, level=level)
                        if reraise:
                            raise
                        return None

            return _sync_wrapper

    return _decorator


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Enums
    "ErrorCode",
    # Classe de base
    "NexusDLError",
    # Exceptions — Configuration
    "ConfigurationError",
    "ConfigNotFoundError",
    "ConfigParseError",
    # Exceptions — Validation
    "ValidationError",
    "InvalidInputError",
    "MissingFieldError",
    "DuplicateError",
    # Exceptions — Réseau
    "NetworkError",
    "ConnectionError",
    "NetworkTimeoutError",
    "ConnectionRefusedError",
    "DnsError",
    "SSLError",
    "HttpError",
    "RateLimitError",
    "ProxyError",
    "CloudflareBlockedError",
    # Exceptions — Stockage
    "StorageError",
    "FileNotFoundError",
    "FileExistsError",
    "FileCorruptedError",
    "DiskFullError",
    "DatabaseError",
    "DatabaseLockedError",
    # Exceptions — Authentification
    "AuthenticationError",
    "InvalidCredentialsError",
    "TokenExpiredError",
    "TokenInvalidError",
    "SessionExpiredError",
    "ForbiddenError",
    "AccountDisabledError",
    # Exceptions — Ressources
    "NotFoundError",
    "MangaNotFoundError",
    "ChapterNotFoundError",
    "SiteNotFoundError",
    "UserNotFoundError",
    "TaskNotFoundError",
    # Exceptions — Permissions
    "PermissionDeniedError",
    "AdminRequiredError",
    # Exceptions — Opérations
    "OperationCancelledError",
    "OperationTimeoutError",
    "OperationFailedError",
    "DownloadFailedError",
    "ParsingFailedError",
    "PackagingFailedError",
    "ConversionFailedError",
    # Exceptions — Internes
    "InternalError",
    "NotImplementedError",
    "DependencyError",
    "MigrationError",
    # Helpers
    "is_nexusdl_error",
    "get_error_code",
    "format_error",
    "error_to_dict",
    "wrap_exception",
    "translate_error",
    "log_exception",
    # Décorateurs
    "catch_and_log",
]
