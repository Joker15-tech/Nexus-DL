"""Système d'internationalisation (i18n) pour NexusDL.

Ce module fournit un système complet de traduction pour l'application NexusDL,
permettant de supporter plusieurs langues avec une API simple et performante.
Il charge les traductions depuis des fichiers JSON structurés et offre des
fonctionnalités avancées : interpolation de variables, support du pluriel,
fallback chaîné, détection automatique de la langue système, et changement
de langue à chaud.

**Architecture des fichiers de traduction** :
    data/translations/
        ├── en.json          (anglais, langue de fallback)
        ├── fr.json          (français)
        ├── de.json          (allemand)
        ├── es.json          (espagnol)
        ├── ja.json          (japonais)
        └── ...

**Format des fichiers JSON** :
    {
        "common": {
            "save": "Save",
            "cancel": "Cancel",
            "confirm": "Confirm"
        },
        "download": {
            "started": "Download started: {title}",
            "completed": "Download completed: {title} ({count} pages)",
            "failed": "Download failed: {error}",
            "items": {
                "one": "{count} item",
                "other": "{count} items"
            }
        }
    }

**Fonctionnalités principales** :
    - Chargement lazy des traductions depuis JSON
    - Interpolation de variables : `t("download.started", title="One Piece")`
    - Support du pluriel : `t("download.items", count=1)` → "1 item"
    - Fallback chaîné : fr → en → clé brute
    - Détection automatique de la langue système
    - Changement de langue à chaud (thread-safe)
    - Cache des traductions chargées
    - Variables d'environnement pour override
    - Support des namespaces imbriqués
    - Validation des clés et des fichiers
    - Statistiques d'utilisation

**Variables d'environnement** (override de la configuration) :
    NEXUSDL_LANGUAGE          : Langue à utiliser (ex: "fr", "en")
    NEXUSDL_TRANSLATIONS_DIR  : Répertoire des fichiers de traduction
    NEXUSDL_FALLBACK_LANGUAGE : Langue de fallback (défaut: "en")

Exemple d'utilisation :
    >>> from nexusdl.core.i18n import setup_i18n, t
    >>>
    >>> # Configuration au démarrage
    >>> setup_i18n(language="fr", translations_dir=Path("data/translations"))
    >>>
    >>> # Traduction simple
    >>> print(t("common.save"))
    'Enregistrer'
    >>>
    >>> # Traduction avec interpolation
    >>> print(t("download.started", title="One Piece"))
    'Téléchargement démarré : One Piece'
    >>>
    >>> # Traduction avec pluriel
    >>> print(t("download.items", count=1))
    '1 élément'
    >>> print(t("download.items", count=5))
    '5 éléments'
    >>>
    >>> # Changement de langue à chaud
    >>> set_language("en")
    >>> print(t("common.save"))
    'Save'
    >>>
    >>> # Détection de la langue système
    >>> lang = detect_system_language()
    >>> print(lang)
    'fr'

Intégration :
    - Toutes les interfaces (CLI/Web/GUI) utilisent `t()` pour l'affichage
    - Configuration chargée depuis `config.yaml` (section `i18n`)
    - Les fichiers de traduction sont dans `data/translations/`
    - Les erreurs de traduction sont loguées mais ne bloquent pas l'application
"""

from __future__ import annotations

import json
import locale
import os
import re
import threading
from collections import OrderedDict
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Final, Self

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# CONSTANTES — Identifiants et valeurs par défaut
# ============================================================================


# Nom du répertoire de traductions par défaut
_DEFAULT_TRANSLATIONS_DIR: Final[str] = "data/translations"

# Fichier de traduction de fallback
_FALLBACK_LANGUAGE: Final[str] = "en"

# Variables d'environnement pour override
_ENV_LANGUAGE: Final[str] = "NEXUSDL_LANGUAGE"
_ENV_TRANSLATIONS_DIR: Final[str] = "NEXUSDL_TRANSLATIONS_DIR"
_ENV_FALLBACK_LANGUAGE: Final[str] = "NEXUSDL_FALLBACK_LANGUAGE"

# Extension des fichiers de traduction
_TRANSLATION_FILE_EXTENSION: Final[str] = ".json"

# Pattern pour l'interpolation de variables
_INTERPOLATION_PATTERN: Final[re.Pattern[str]] = re.compile(r"\{(\w+)\}")

# Pattern pour la clé de pluriel
_PLURAL_KEYS: Final[frozenset[str]] = frozenset({"zero", "one", "two", "few", "many", "other"})

# Taille maximale du cache de traductions compilées
_MAX_CACHE_SIZE: Final[int] = 10000


# ============================================================================
# EXCEPTIONS
# ============================================================================


class I18nError(NexusDLError):
    """Exception de base pour les erreurs d'internationalisation."""


class TranslationNotFoundError(I18nError):
    """Exception levée lorsqu'une traduction est introuvable."""

    def __init__(self, key: str, language: str) -> None:
        super().__init__(
            f"Traduction introuvable: '{key}' pour la langue '{language}'"
        )
        self.key = key
        self.language = language


class TranslationFileError(I18nError):
    """Exception levée lorsqu'un fichier de traduction est invalide."""

    def __init__(self, path: Path, reason: str = "") -> None:
        msg = f"Fichier de traduction invalide: {path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.path = path
        self.reason = reason


class LanguageNotSupportedError(I18nError):
    """Exception levée lorsqu'une langue n'est pas supportée."""

    def __init__(self, language: str, available: list[str] | None = None) -> None:
        msg = f"Langue non supportée: {language}"
        if available:
            msg += f" (langues disponibles: {', '.join(available)})"
        super().__init__(msg)
        self.language = language
        self.available = available or []


class I18nNotInitializedError(I18nError):
    """Exception levée lorsqu'on utilise i18n avant initialisation."""

    def __init__(self) -> None:
        super().__init__(
            "I18n must be initialized before use. Call setup_i18n() first."
        )


class InvalidTranslationKeyError(I18nError):
    """Exception levée lorsqu'une clé de traduction est invalide."""

    def __init__(self, key: str, reason: str = "") -> None:
        msg = f"Clé de traduction invalide: '{key}'"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.key = key
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class SupportedLanguage(str, Enum):
    """Langues supportées par NexusDL.

    Les codes correspondent aux fichiers JSON dans data/translations/.
    """

    EN = "en"  # Anglais (fallback)
    FR = "fr"  # Français
    DE = "de"  # Allemand
    ES = "es"  # Espagnol
    IT = "it"  # Italien
    PT = "pt"  # Portugais
    RU = "ru"  # Russe
    JA = "ja"  # Japonais
    KO = "ko"  # Coréen
    ZH = "zh"  # Chinois
    AR = "ar"  # Arabe
    PL = "pl"  # Polonais
    TR = "tr"  # Turc
    NL = "nl"  # Néerlandais

    @property
    def label(self) -> str:
        """Libellé humain de la langue."""
        return {
            SupportedLanguage.EN: "English",
            SupportedLanguage.FR: "Français",
            SupportedLanguage.DE: "Deutsch",
            SupportedLanguage.ES: "Español",
            SupportedLanguage.IT: "Italiano",
            SupportedLanguage.PT: "Português",
            SupportedLanguage.RU: "Русский",
            SupportedLanguage.JA: "日本語",
            SupportedLanguage.KO: "한국어",
            SupportedLanguage.ZH: "中文",
            SupportedLanguage.AR: "العربية",
            SupportedLanguage.PL: "Polski",
            SupportedLanguage.TR: "Türkçe",
            SupportedLanguage.NL: "Nederlands",
        }[self]

    @property
    def flag(self) -> str:
        """Drapeau emoji de la langue."""
        return {
            SupportedLanguage.EN: "🇬🇧",
            SupportedLanguage.FR: "🇫🇷",
            SupportedLanguage.DE: "🇩🇪",
            SupportedLanguage.ES: "🇪🇸",
            SupportedLanguage.IT: "🇮🇹",
            SupportedLanguage.PT: "🇵🇹",
            SupportedLanguage.RU: "🇷🇺",
            SupportedLanguage.JA: "🇯🇵",
            SupportedLanguage.KO: "🇰🇷",
            SupportedLanguage.ZH: "🇨🇳",
            SupportedLanguage.AR: "🇸🇦",
            SupportedLanguage.PL: "🇵🇱",
            SupportedLanguage.TR: "🇹🇷",
            SupportedLanguage.NL: "🇳🇱",
        }[self]

    @property
    def is_rtl(self) -> bool:
        """Indique si la langue s'écrit de droite à gauche."""
        return self == SupportedLanguage.AR


# ============================================================================
# MODÈLES PYDANTIC — Configuration
# ============================================================================


class I18nConfig(BaseModel):
    """Configuration du système d'internationalisation.

    Attributes:
        language: Langue courante (code ISO 639-1).
        fallback_language: Langue de fallback si traduction introuvable.
        translations_dir: Répertoire des fichiers de traduction.
        auto_detect: Détecter automatiquement la langue du système.
        cache_enabled: Activer le cache des traductions.
        cache_size: Taille maximale du cache.
        strict_mode: Lever une exception si traduction introuvable.
        log_missing: Logger les traductions manquantes.
        reload_on_change: Recharger les traductions si fichiers modifiés.
    """

    language: str = Field(
        default=_FALLBACK_LANGUAGE,
        min_length=2,
        max_length=5,
        description="Langue courante (code ISO 639-1).",
    )
    fallback_language: str = Field(
        default=_FALLBACK_LANGUAGE,
        min_length=2,
        max_length=5,
        description="Langue de fallback.",
    )
    translations_dir: Path | None = Field(
        default=None,
        description="Répertoire des fichiers de traduction.",
    )
    auto_detect: bool = Field(
        default=True,
        description="Détecter automatiquement la langue du système.",
    )
    cache_enabled: bool = Field(
        default=True,
        description="Activer le cache des traductions.",
    )
    cache_size: int = Field(
        default=_MAX_CACHE_SIZE,
        ge=100,
        le=100000,
        description="Taille maximale du cache.",
    )
    strict_mode: bool = Field(
        default=False,
        description="Lever une exception si traduction introuvable.",
    )
    log_missing: bool = Field(
        default=True,
        description="Logger les traductions manquantes.",
    )
    reload_on_change: bool = Field(
        default=False,
        description="Recharger les traductions si fichiers modifiés.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    # --------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------

    @field_validator("translations_dir", mode="before")
    @classmethod
    def _validate_translations_dir(cls, v: Any) -> Path | None:
        """Valide et normalise le répertoire de traductions."""
        if v is None:
            return None
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return None
            v = Path(v)
        if not isinstance(v, Path):
            raise TranslationFileError(v, "Doit être un chemin (str ou Path)")
        return v.expanduser().resolve()

    # --------------------------------------------------------------------
    # Méthodes
    # --------------------------------------------------------------------

    def with_env_overrides(self) -> I18nConfig:
        """Retourne une nouvelle config avec les overrides d'environnement.

        Returns:
            Nouvelle instance de I18nConfig avec overrides appliqués.
        """
        updates: dict[str, Any] = {}

        # NEXUSDL_LANGUAGE
        env_lang = os.environ.get(_ENV_LANGUAGE)
        if env_lang:
            updates["language"] = env_lang.lower()

        # NEXUSDL_TRANSLATIONS_DIR
        env_dir = os.environ.get(_ENV_TRANSLATIONS_DIR)
        if env_dir:
            updates["translations_dir"] = Path(env_dir).expanduser().resolve()

        # NEXUSDL_FALLBACK_LANGUAGE
        env_fallback = os.environ.get(_ENV_FALLBACK_LANGUAGE)
        if env_fallback:
            updates["fallback_language"] = env_fallback.lower()

        if not updates:
            return self

        return self.model_copy(update=updates)


class I18nStats(BaseModel):
    """Statistiques du système d'internationalisation.

    Attributes:
        total_translations: Nombre total de traductions chargées.
        translations_by_language: Nombre de traductions par langue.
        missing_translations: Nombre de traductions manquantes.
        cache_hits: Nombre de hits du cache.
        cache_misses: Nombre de misses du cache.
        current_language: Langue courante.
        fallback_language: Langue de fallback.
        loaded_files: Nombre de fichiers chargés.
        last_reload_at: Timestamp du dernier rechargement.
    """

    total_translations: int = Field(default=0, ge=0)
    translations_by_language: dict[str, int] = Field(default_factory=dict)
    missing_translations: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    cache_misses: int = Field(default=0, ge=0)
    current_language: str = Field(default=_FALLBACK_LANGUAGE)
    fallback_language: str = Field(default=_FALLBACK_LANGUAGE)
    loaded_files: int = Field(default=0, ge=0)
    last_reload_at: datetime | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")


# ============================================================================
# HELPERS — Détection de langue
# ============================================================================


def detect_system_language() -> str:
    """Détecte la langue du système d'exploitation.

    Utilise le module `locale` pour déterminer la langue préférée
    de l'utilisateur. Fallback sur "en" si détection impossible.

    Returns:
        Code ISO 639-1 de la langue détectée (ex: "fr", "en").
    """
    try:
        # Essayer plusieurs méthodes de détection
        # 1. Variable d'environnement LANG
        lang_env = os.environ.get("LANG") or os.environ.get("LANGUAGE")
        if lang_env:
            # Extraire le code de langue (ex: "fr_FR.UTF-8" → "fr")
            lang_code = lang_env.split("_")[0].split(".")[0].lower()
            if len(lang_code) == 2:
                return lang_code

        # 2. Module locale
        try:
            locale.setlocale(locale.LC_ALL, "")
            lang_locale = locale.getlocale(locale.LC_MESSAGES)[0]
            if lang_locale:
                lang_code = lang_locale.split("_")[0].lower()
                if len(lang_code) == 2:
                    return lang_code
        except (locale.Error, ValueError):
            pass

        # 3. Windows
        if os.name == "nt":
            try:
                import ctypes
                windll = ctypes.windll.kernel32
                lang_id = windll.GetUserDefaultUILanguage()
                # Convertir l'ID de langue en code ISO
                # (simplifié, en production utiliser une table de conversion)
                lang_map = {
                    1033: "en",  # English
                    1036: "fr",  # French
                    1031: "de",  # German
                    1034: "es",  # Spanish
                    1040: "it",  # Italian
                    1046: "pt",  # Portuguese
                    1049: "ru",  # Russian
                    1041: "ja",  # Japanese
                    1042: "ko",  # Korean
                    2052: "zh",  # Chinese
                }
                if lang_id in lang_map:
                    return lang_map[lang_id]
            except Exception:
                pass

    except Exception as e:
        logger.debug("Erreur lors de la détection de la langue: {}", e)

    # Fallback
    return _FALLBACK_LANGUAGE


def normalize_language_code(code: str) -> str:
    """Normalise un code de langue (lowercase, 2 lettres).

    Args:
        code: Code de langue à normaliser.

    Returns:
        Code normalisé (ex: "FR" → "fr", "en_US" → "en").
    """
    if not code:
        return _FALLBACK_LANGUAGE

    code = code.strip().lower()

    # Extraire les 2 premières lettres
    if "_" in code:
        code = code.split("_")[0]
    if "-" in code:
        code = code.split("-")[0]
    if "." in code:
        code = code.split(".")[0]

    # Valider la longueur
    if len(code) != 2:
        return _FALLBACK_LANGUAGE

    return code


# ============================================================================
# CLASSE INTERNE — TranslationStore
# ============================================================================


class _TranslationStore:
    """Stockage interne des traductions chargées.

    Non exposé publiquement — utilisé par I18nManager.
    """

    __slots__ = (
        "_translations",
        "_file_mtimes",
        "_lock",
    )

    def __init__(self) -> None:
        # Structure : {language: {key: value}}
        self._translations: dict[str, dict[str, Any]] = {}
        self._file_mtimes: dict[str, float] = {}
        self._lock = threading.RLock()

    def load_file(self, path: Path, language: str) -> int:
        """Charge un fichier de traduction.

        Args:
            path: Chemin du fichier JSON.
            language: Code de langue.

        Returns:
            Nombre de clés chargées.

        Raises:
            TranslationFileError: Si le fichier est invalide.
        """
        try:
            content = path.read_text(encoding="utf-8")
            data = json.loads(content)

            if not isinstance(data, dict):
                raise TranslationFileError(path, "Le fichier doit contenir un objet JSON")

            # Aplatir la structure hiérarchique
            flattened = self._flatten_dict(data)

            with self._lock:
                self._translations[language] = flattened
                self._file_mtimes[language] = path.stat().st_mtime

            return len(flattened)

        except json.JSONDecodeError as e:
            raise TranslationFileError(path, f"JSON invalide: {e}") from e
        except OSError as e:
            raise TranslationFileError(path, f"Erreur de lecture: {e}") from e

    def get(self, language: str, key: str) -> Any:
        """Récupère une traduction.

        Args:
            language: Code de langue.
            key: Clé de traduction.

        Returns:
            Valeur de la traduction ou None si introuvable.
        """
        with self._lock:
            lang_translations = self._translations.get(language, {})
            return lang_translations.get(key)

    def has_language(self, language: str) -> bool:
        """Vérifie si une langue est chargée.

        Args:
            language: Code de langue.

        Returns:
            True si la langue est chargée.
        """
        with self._lock:
            return language in self._translations

    def has_file_changed(self, language: str, path: Path) -> bool:
        """Vérifie si un fichier a été modifié depuis le dernier chargement.

        Args:
            language: Code de langue.
            path: Chemin du fichier.

        Returns:
            True si le fichier a été modifié.
        """
        with self._lock:
            last_mtime = self._file_mtimes.get(language)
            if last_mtime is None:
                return True

            try:
                current_mtime = path.stat().st_mtime
                return current_mtime > last_mtime
            except OSError:
                return False

    def list_languages(self) -> list[str]:
        """Liste toutes les langues chargées.

        Returns:
            Liste des codes de langue.
        """
        with self._lock:
            return list(self._translations.keys())

    def count_translations(self, language: str) -> int:
        """Compte le nombre de traductions pour une langue.

        Args:
            language: Code de langue.

        Returns:
            Nombre de traductions.
        """
        with self._lock:
            return len(self._translations.get(language, {}))

    def clear(self) -> None:
        """Vide le stockage."""
        with self._lock:
            self._translations.clear()
            self._file_mtimes.clear()

    @staticmethod
    def _flatten_dict(
        data: dict[str, Any],
        prefix: str = "",
    ) -> dict[str, Any]:
        """Aplatit un dictionnaire hiérarchique en clés pointées.

        Args:
            data: Dictionnaire à aplatir.
            prefix: Préfixe à ajouter aux clés.

        Returns:
            Dictionnaire aplati.

        Example:
            >>> _flatten_dict({"a": {"b": "value"}})
            {"a.b": "value"}
        """
        result: dict[str, Any] = {}

        for key, value in data.items():
            full_key = f"{prefix}.{key}" if prefix else key

            if isinstance(value, dict):
                # Récursion pour les sous-dictionnaires
                result.update(_TranslationStore._flatten_dict(value, full_key))
            else:
                result[full_key] = value

        return result


# ============================================================================
# CLASSE PRINCIPALE — I18nManager
# ============================================================================


class I18nManager:
    """Gestionnaire centralisé de l'internationalisation.

    Charge les traductions depuis des fichiers JSON, fournit une API simple
    pour traduire des clés avec interpolation et pluriel, et supporte le
    changement de langue à chaud.

    Lifecycle :
        >>> manager = I18nManager(config=I18nConfig(language="fr"))
        >>> manager.initialize()
        >>> print(manager.t("common.save"))
        'Enregistrer'
        >>> manager.shutdown()

    Thread-safety :
        Cette classe est thread-safe. Les opérations de traduction peuvent
        être appelées depuis plusieurs threads simultanément.
    """

    def __init__(
        self,
        *,
        config: I18nConfig | None = None,
    ) -> None:
        """Initialise le gestionnaire i18n.

        Args:
            config: Configuration du système i18n.
        """
        self._config = (config or I18nConfig()).with_env_overrides()
        self._store = _TranslationStore()
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._cache_lock = threading.Lock()

        # État
        self._initialized: bool = False
        self._current_language: str = self._config.language
        self._fallback_language: str = self._config.fallback_language

        # Statistiques
        self._missing_translations: int = 0
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._loaded_files: int = 0
        self._last_reload_at: datetime | None = None
        self._stats_lock = threading.Lock()

        # Lock pour les opérations de chargement
        self._load_lock = threading.Lock()

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    def initialize(self) -> None:
        """Initialise le système i18n et charge les traductions.

        Raises:
            TranslationFileError: Si un fichier de traduction est invalide.
            LanguageNotSupportedError: Si la langue configurée n'est pas supportée.
        """
        if self._initialized:
            return

        # Déterminer le répertoire de traductions
        translations_dir = self._resolve_translations_dir()

        # Détecter la langue si auto_detect activé
        if self._config.auto_detect:
            detected = detect_system_language()
            # Vérifier si la langue détectée est disponible
            lang_file = translations_dir / f"{detected}{_TRANSLATION_FILE_EXTENSION}"
            if lang_file.exists():
                self._current_language = detected
            else:
                logger.debug(
                    "Langue détectée '{}' non disponible, fallback sur '{}'",
                    detected,
                    self._config.language,
                )

        # Charger toutes les traductions disponibles
        self._load_all_translations(translations_dir)

        # Vérifier que la langue courante est disponible
        if not self._store.has_language(self._current_language):
            if self._store.has_language(self._fallback_language):
                logger.warning(
                    "Langue '{}' non disponible, fallback sur '{}'",
                    self._current_language,
                    self._fallback_language,
                )
                self._current_language = self._fallback_language
            else:
                raise LanguageNotSupportedError(
                    self._current_language,
                    self._store.list_languages(),
                )

        self._initialized = True
        self._last_reload_at = datetime.now(UTC)

        logger.info(
            "I18n initialisé: language={}, fallback={}, files={}",
            self._current_language,
            self._fallback_language,
            self._loaded_files,
        )

    def shutdown(self) -> None:
        """Arrête le système i18n et libère les ressources."""
        if not self._initialized:
            return

        self._store.clear()

        with self._cache_lock:
            self._cache.clear()

        self._initialized = False

    def reconfigure(self, config: I18nConfig) -> None:
        """Reconfigure le système i18n avec une nouvelle configuration.

        Args:
            config: Nouvelle configuration.
        """
        self._config = config.with_env_overrides()
        self.shutdown()
        self.initialize()

    def __enter__(self) -> Self:
        self.initialize()
        return self

    def __exit__(self, *args: object) -> None:
        self.shutdown()

    # ------------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------------

    @property
    def is_initialized(self) -> bool:
        """Indique si le système i18n est initialisé."""
        return self._initialized

    @property
    def current_language(self) -> str:
        """Langue courante."""
        return self._current_language

    @property
    def fallback_language(self) -> str:
        """Langue de fallback."""
        return self._fallback_language

    @property
    def available_languages(self) -> list[str]:
        """Liste des langues disponibles."""
        return self._store.list_languages()

    @property
    def config(self) -> I18nConfig:
        """Configuration actuelle."""
        return self._config

    # ------------------------------------------------------------------------
    # API publique — Traduction
    # ------------------------------------------------------------------------

    def t(
        self,
        key: str,
        *,
        default: str | None = None,
        language: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Traduit une clé avec interpolation de variables.

        Args:
            key: Clé de traduction (ex: "common.save").
            default: Valeur par défaut si traduction introuvable.
            language: Langue spécifique (défaut: langue courante).
            **kwargs: Variables pour l'interpolation.

        Returns:
            Traduction interpolée, ou la clé brute si introuvable.

        Example:
            >>> t("download.started", title="One Piece")
            'Téléchargement démarré : One Piece'
        """
        self._ensure_initialized()

        target_language = language or self._current_language

        # Vérifier le cache
        cache_key = f"{target_language}:{key}:{self._make_cache_key(kwargs)}"
        if self._config.cache_enabled:
            with self._cache_lock:
                if cache_key in self._cache:
                    self._cache_hits += 1
                    self._cache.move_to_end(cache_key)
                    return self._cache[cache_key]
                self._cache_misses += 1

        # Chercher la traduction
        translation = self._get_translation(key, target_language)

        # Fallback si introuvable
        if translation is None:
            # Essayer la langue de fallback
            if target_language != self._fallback_language:
                translation = self._get_translation(key, self._fallback_language)

            # Si toujours introuvable
            if translation is None:
                with self._stats_lock:
                    self._missing_translations += 1

                if self._config.log_missing:
                    logger.warning(
                        "Traduction manquante: '{}' pour langue '{}'",
                        key,
                        target_language,
                    )

                if self._config.strict_mode:
                    raise TranslationNotFoundError(key, target_language)

                # Retourner la valeur par défaut ou la clé brute
                result = default if default is not None else key
                return result

        # Gérer le pluriel
        if isinstance(translation, dict) and "count" in kwargs:
            translation = self._handle_plural(translation, kwargs["count"])

        # Interpoler les variables
        if isinstance(translation, str):
            result = self._interpolate(translation, kwargs)
        else:
            result = str(translation)

        # Mettre en cache
        if self._config.cache_enabled:
            with self._cache_lock:
                self._cache[cache_key] = result
                self._cache.move_to_end(cache_key)
                # Limiter la taille du cache
                while len(self._cache) > self._config.cache_size:
                    self._cache.popitem(last=False)

        return result

    def translate(
        self,
        key: str,
        **kwargs: Any,
    ) -> str:
        """Alias de t() pour compatibilité.

        Args:
            key: Clé de traduction.
            **kwargs: Variables pour l'interpolation.

        Returns:
            Traduction interpolée.
        """
        return self.t(key, **kwargs)

    def has_translation(
        self,
        key: str,
        *,
        language: str | None = None,
    ) -> bool:
        """Vérifie si une traduction existe.

        Args:
            key: Clé de traduction.
            language: Langue spécifique (défaut: langue courante).

        Returns:
            True si la traduction existe.
        """
        self._ensure_initialized()

        target_language = language or self._current_language
        translation = self._store.get(target_language, key)

        if translation is None and target_language != self._fallback_language:
            translation = self._store.get(self._fallback_language, key)

        return translation is not None

    # ------------------------------------------------------------------------
    # API publique — Gestion des langues
    # ------------------------------------------------------------------------

    def set_language(self, language: str) -> None:
        """Change la langue courante.

        Args:
            language: Code de langue (ex: "fr", "en").

        Raises:
            LanguageNotSupportedError: Si la langue n'est pas disponible.
        """
        self._ensure_initialized()

        normalized = normalize_language_code(language)

        if not self._store.has_language(normalized):
            raise LanguageNotSupportedError(
                normalized,
                self._store.list_languages(),
            )

        self._current_language = normalized

        # Vider le cache car les traductions changent
        with self._cache_lock:
            self._cache.clear()

        logger.info("Langue changée: {}", normalized)

    def get_current_language(self) -> str:
        """Retourne la langue courante.

        Returns:
            Code ISO 639-1 de la langue courante.
        """
        return self._current_language

    def list_available_languages(self) -> list[dict[str, str]]:
        """Liste toutes les langues disponibles avec métadonnées.

        Returns:
            Liste de dictionnaires avec code, label, flag, etc.
        """
        self._ensure_initialized()

        result: list[dict[str, str]] = []

        for lang_code in self._store.list_languages():
            try:
                lang_enum = SupportedLanguage(lang_code)
                result.append({
                    "code": lang_code,
                    "label": lang_enum.label,
                    "flag": lang_enum.flag,
                    "is_rtl": str(lang_enum.is_rtl).lower(),
                    "is_current": str(lang_code == self._current_language).lower(),
                    "translations_count": str(self._store.count_translations(lang_code)),
                })
            except ValueError:
                # Langue non reconnue dans l'enum
                result.append({
                    "code": lang_code,
                    "label": lang_code.upper(),
                    "flag": "🏳️",
                    "is_rtl": "false",
                    "is_current": str(lang_code == self._current_language).lower(),
                    "translations_count": str(self._store.count_translations(lang_code)),
                })

        return result

    # ------------------------------------------------------------------------
    # API publique — Rechargement
    # ------------------------------------------------------------------------

    def reload(self) -> int:
        """Recharge toutes les traductions depuis les fichiers.

        Returns:
            Nombre de fichiers rechargés.
        """
        self._ensure_initialized()

        with self._load_lock:
            translations_dir = self._resolve_translations_dir()
            self._store.clear()

            with self._cache_lock:
                self._cache.clear()

            self._loaded_files = 0
            self._load_all_translations(translations_dir)

            self._last_reload_at = datetime.now(UTC)

            logger.info(
                "Traductions rechargées: {} fichiers",
                self._loaded_files,
            )

            return self._loaded_files

    def reload_if_changed(self) -> bool:
        """Recharge les traductions si les fichiers ont été modifiés.

        Returns:
            True si un rechargement a été effectué.
        """
        if not self._config.reload_on_change:
            return False

        self._ensure_initialized()

        translations_dir = self._resolve_translations_dir()
        changed = False

        for lang_code in self._store.list_languages():
            lang_file = translations_dir / f"{lang_code}{_TRANSLATION_FILE_EXTENSION}"
            if lang_file.exists() and self._store.has_file_changed(lang_code, lang_file):
                changed = True
                break

        if changed:
            self.reload()
            return True

        return False

    # ------------------------------------------------------------------------
    # API publique — Statistiques
    # ------------------------------------------------------------------------

    def get_stats(self) -> I18nStats:
        """Retourne les statistiques du système i18n.

        Returns:
            Instance de I18nStats.
        """
        self._ensure_initialized()

        translations_by_language = {
            lang: self._store.count_translations(lang)
            for lang in self._store.list_languages()
        }

        with self._stats_lock:
            return I18nStats(
                total_translations=sum(translations_by_language.values()),
                translations_by_language=translations_by_language,
                missing_translations=self._missing_translations,
                cache_hits=self._cache_hits,
                cache_misses=self._cache_misses,
                current_language=self._current_language,
                fallback_language=self._fallback_language,
                loaded_files=self._loaded_files,
                last_reload_at=self._last_reload_at,
            )

    def reset_stats(self) -> None:
        """Réinitialise les statistiques."""
        with self._stats_lock:
            self._missing_translations = 0
            self._cache_hits = 0
            self._cache_misses = 0

    # ------------------------------------------------------------------------
    # Méthodes internes — Chargement
    # ------------------------------------------------------------------------

    def _resolve_translations_dir(self) -> Path:
        """Résout le répertoire de traductions.

        Returns:
            Chemin absolu vers le répertoire.
        """
        if self._config.translations_dir is not None:
            return self._config.translations_dir

        # Fallback : répertoire par défaut relatif au package
        try:
            # Essayer de trouver le répertoire data/translations
            import nexusdl
            package_dir = Path(nexusdl.__file__).parent
            default_dir = package_dir.parent / "data" / "translations"
            if default_dir.exists():
                return default_dir
        except Exception:
            pass

        # Fallback ultime : répertoire courant
        return Path.cwd() / _DEFAULT_TRANSLATIONS_DIR

    def _load_all_translations(self, translations_dir: Path) -> None:
        """Charge tous les fichiers de traduction d'un répertoire.

        Args:
            translations_dir: Répertoire contenant les fichiers JSON.
        """
        if not translations_dir.exists():
            logger.warning(
                "Répertoire de traductions introuvable: {}",
                translations_dir,
            )
            return

        if not translations_dir.is_dir():
            raise TranslationFileError(
                translations_dir,
                "N'est pas un répertoire",
            )

        # Chercher tous les fichiers JSON
        for file_path in translations_dir.glob(f"*{_TRANSLATION_FILE_EXTENSION}"):
            language = file_path.stem  # Nom du fichier sans extension

            try:
                count = self._store.load_file(file_path, language)
                self._loaded_files += 1
                logger.debug(
                    "Traductions chargées: {} ({} clés)",
                    language,
                    count,
                )
            except TranslationFileError as e:
                logger.error("Erreur lors du chargement de {}: {}", file_path, e)

    # ------------------------------------------------------------------------
    # Méthodes internes — Traduction
    # ------------------------------------------------------------------------

    def _get_translation(
        self,
        key: str,
        language: str,
    ) -> Any:
        """Récupère une traduction pour une langue donnée.

        Args:
            key: Clé de traduction.
            language: Code de langue.

        Returns:
            Valeur de la traduction ou None.
        """
        return self._store.get(language, key)

    def _handle_plural(
        self,
        plural_dict: dict[str, Any],
        count: int | float,
    ) -> str:
        """Gère le pluriel selon le nombre.

        Supporte les clés : zero, one, two, few, many, other.
        Pour simplifier, on utilise uniquement "one" et "other".

        Args:
            plural_dict: Dictionnaire avec les formes plurielles.
            count: Nombre pour déterminer la forme.

        Returns:
            Forme plurielle appropriée.
        """
        # Règles simplifiées (pour les langues européennes)
        if count == 0 and "zero" in plural_dict:
            return str(plural_dict["zero"])
        if count == 1 and "one" in plural_dict:
            return str(plural_dict["one"])
        if "other" in plural_dict:
            return str(plural_dict["other"])

        # Fallback : première valeur disponible
        for key in ("one", "other", "zero", "two", "few", "many"):
            if key in plural_dict:
                return str(plural_dict[key])

        return str(count)

    def _interpolate(
        self,
        template: str,
        variables: dict[str, Any],
    ) -> str:
        """Interpole les variables dans un template.

        Args:
            template: Template avec placeholders {var}.
            variables: Dictionnaire de variables.

        Returns:
            Chaîne interpolée.

        Example:
            >>> _interpolate("Hello {name}", {"name": "World"})
            'Hello World'
        """
        if not variables:
            return template

        def _replace(match: re.Match[str]) -> str:
            var_name = match.group(1)
            if var_name in variables:
                return str(variables[var_name])
            # Si variable manquante, garder le placeholder
            return match.group(0)

        return _INTERPOLATION_PATTERN.sub(_replace, template)

    @staticmethod
    def _make_cache_key(variables: dict[str, Any]) -> str:
        """Construit une clé de cache à partir des variables.

        Args:
            variables: Dictionnaire de variables.

        Returns:
            Clé de cache sous forme de chaîne.
        """
        if not variables:
            return ""

        # Trier les clés pour cohérence
        sorted_items = sorted(variables.items())
        return "|".join(f"{k}={v}" for k, v in sorted_items)

    # ------------------------------------------------------------------------
    # Méthodes internes — Utilitaires
    # ------------------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        """Vérifie que le système i18n est initialisé."""
        if not self._initialized:
            raise I18nNotInitializedError()

    def __repr__(self) -> str:
        status = "initialized" if self._initialized else "not initialized"
        return (
            f"<I18nManager status={status} "
            f"language={self._current_language} "
            f"fallback={self._fallback_language} "
            f"files={self._loaded_files}>"
        )


# ============================================================================
# INSTANCE GLOBALE ET FONCTIONS PRATIQUES
# ============================================================================


# Instance globale du I18nManager
_i18n_manager: I18nManager | None = None


def setup_i18n(
    *,
    language: str | None = None,
    fallback_language: str = _FALLBACK_LANGUAGE,
    translations_dir: Path | str | None = None,
    auto_detect: bool = True,
    cache_enabled: bool = True,
    cache_size: int = _MAX_CACHE_SIZE,
    strict_mode: bool = False,
    log_missing: bool = True,
    reload_on_change: bool = False,
) -> I18nManager:
    """Configure le système i18n de NexusDL.

    Fonction de haut niveau pour initialiser rapidement l'i18n.
    Crée une instance globale de I18nManager et l'initialise.

    Args:
        language: Langue à utiliser (défaut: auto-détection ou fallback).
        fallback_language: Langue de fallback (défaut: "en").
        translations_dir: Répertoire des fichiers de traduction.
        auto_detect: Détecter automatiquement la langue du système.
        cache_enabled: Activer le cache des traductions.
        cache_size: Taille maximale du cache.
        strict_mode: Lever une exception si traduction introuvable.
        log_missing: Logger les traductions manquantes.
        reload_on_change: Recharger si fichiers modifiés.

    Returns:
        Instance de I18nManager initialisée.

    Example:
        >>> from nexusdl.core.i18n import setup_i18n
        >>> setup_i18n(language="fr", translations_dir=Path("data/translations"))
    """
    global _i18n_manager

    # Convertir les paramètres
    if isinstance(translations_dir, str):
        translations_dir_path = Path(translations_dir).expanduser().resolve()
    else:
        translations_dir_path = translations_dir

    # Déterminer la langue
    if language is None:
        if auto_detect:
            language = detect_system_language()
        else:
            language = fallback_language

    # Construire la configuration
    config = I18nConfig(
        language=language,
        fallback_language=fallback_language,
        translations_dir=translations_dir_path,
        auto_detect=auto_detect,
        cache_enabled=cache_enabled,
        cache_size=cache_size,
        strict_mode=strict_mode,
        log_missing=log_missing,
        reload_on_change=reload_on_change,
    )

    # Créer et initialiser le manager
    _i18n_manager = I18nManager(config=config)
    _i18n_manager.initialize()

    return _i18n_manager


def t(
    key: str,
    *,
    default: str | None = None,
    language: str | None = None,
    **kwargs: Any,
) -> str:
    """Traduit une clé (raccourci vers l'instance globale).

    Args:
        key: Clé de traduction.
        default: Valeur par défaut si introuvable.
        language: Langue spécifique (optionnel).
        **kwargs: Variables pour l'interpolation.

    Returns:
        Traduction interpolée.

    Raises:
        I18nNotInitializedError: Si i18n n'est pas initialisé.

    Example:
        >>> t("common.save")
        'Enregistrer'
        >>> t("download.started", title="One Piece")
        'Téléchargement démarré : One Piece'
    """
    if _i18n_manager is None:
        raise I18nNotInitializedError()
    return _i18n_manager.t(key, default=default, language=language, **kwargs)


def translate(key: str, **kwargs: Any) -> str:
    """Alias de t() pour compatibilité.

    Args:
        key: Clé de traduction.
        **kwargs: Variables pour l'interpolation.

    Returns:
        Traduction interpolée.
    """
    return t(key, **kwargs)


def get_i18n_manager() -> I18nManager | None:
    """Retourne l'instance globale du I18nManager.

    Returns:
        Instance de I18nManager ou None si non initialisée.
    """
    return _i18n_manager


def get_i18n_stats() -> I18nStats:
    """Retourne les statistiques du système i18n.

    Returns:
        Instance de I18nStats.

    Raises:
        I18nNotInitializedError: Si i18n n'est pas initialisé.
    """
    if _i18n_manager is None:
        raise I18nNotInitializedError()
    return _i18n_manager.get_stats()


def set_language(language: str) -> None:
    """Change la langue courante.

    Args:
        language: Code de langue (ex: "fr", "en").

    Raises:
        I18nNotInitializedError: Si i18n n'est pas initialisé.
        LanguageNotSupportedError: Si la langue n'est pas disponible.
    """
    if _i18n_manager is None:
        raise I18nNotInitializedError()
    _i18n_manager.set_language(language)


def get_current_language() -> str:
    """Retourne la langue courante.

    Returns:
        Code ISO 639-1 de la langue courante.

    Raises:
        I18nNotInitializedError: Si i18n n'est pas initialisé.
    """
    if _i18n_manager is None:
        raise I18nNotInitializedError()
    return _i18n_manager.get_current_language()


def list_available_languages() -> list[dict[str, str]]:
    """Liste toutes les langues disponibles.

    Returns:
        Liste de dictionnaires avec métadonnées des langues.

    Raises:
        I18nNotInitializedError: Si i18n n'est pas initialisé.
    """
    if _i18n_manager is None:
        raise I18nNotInitializedError()
    return _i18n_manager.list_available_languages()


def reset_i18n() -> None:
    """Réinitialise le système i18n.

    Arrête le manager global et le remet à zéro.
    """
    global _i18n_manager
    if _i18n_manager is not None:
        _i18n_manager.shutdown()
        _i18n_manager = None


def has_translation(
    key: str,
    *,
    language: str | None = None,
) -> bool:
    """Vérifie si une traduction existe.

    Args:
        key: Clé de traduction.
        language: Langue spécifique (optionnel).

    Returns:
        True si la traduction existe.

    Raises:
        I18nNotInitializedError: Si i18n n'est pas initialisé.
    """
    if _i18n_manager is None:
        raise I18nNotInitializedError()
    return _i18n_manager.has_translation(key, language=language)


# Alias pratique : la fonction de traduction
# Usage : from nexusdl.core.i18n import _
_ = t


# ============================================================================
# HELPERS — Fonctions utilitaires
# ============================================================================


def format_number(
    number: int | float,
    *,
    language: str | None = None,
    locale_style: str = "decimal",
) -> str:
    """Formate un nombre selon la langue courante.

    Args:
        number: Nombre à formater.
        language: Langue spécifique (optionnel).
        locale_style: Style de formatage ("decimal", "percent", "currency").

    Returns:
        Nombre formaté.

    Example:
        >>> format_number(1234567.89, language="fr")
        '1 234 567,89'
        >>> format_number(1234567.89, language="en")
        '1,234,567.89'
    """
    target_lang = language or get_current_language() if _i18n_manager else "en"

    # Mapping simplifié des séparateurs
    separators = {
        "fr": (" ", ","),
        "de": (".", ","),
        "es": (".", ","),
        "it": (".", ","),
        "pt": (".", ","),
        "en": (",", "."),
        "ja": (",", "."),
        "ko": (",", "."),
        "zh": (",", "."),
    }

    thousands_sep, decimal_sep = separators.get(target_lang, (",", "."))

    if isinstance(number, float):
        # Formater avec décimales
        integer_part = int(number)
        decimal_part = abs(number - integer_part)
        decimal_str = f"{decimal_part:.2f}"[2:]  # Retirer "0."

        integer_str = _format_integer(integer_part, thousands_sep)
        return f"{integer_str}{decimal_sep}{decimal_str}"
    else:
        return _format_integer(number, thousands_sep)


def _format_integer(number: int, thousands_sep: str) -> str:
    """Formate un entier avec séparateurs de milliers.

    Args:
        number: Entier à formater.
        thousands_sep: Séparateur de milliers.

    Returns:
        Entier formaté.
    """
    if number == 0:
        return "0"

    negative = number < 0
    number = abs(number)

    # Formater par groupes de 3 chiffres
    groups: list[str] = []
    while number > 0:
        groups.append(f"{number % 1000:03d}")
        number //= 1000

    # Inverser et joindre
    result = thousands_sep.join(reversed(groups))

    # Retirer les zéros en trop au début
    result = result.lstrip("0") or "0"

    if negative:
        result = f"-{result}"

    return result


def format_date(
    date: datetime,
    *,
    language: str | None = None,
    style: str = "medium",
) -> str:
    """Formate une date selon la langue courante.

    Args:
        date: Date à formater.
        language: Langue spécifique (optionnel).
        style: Style de formatage ("short", "medium", "long", "full").

    Returns:
        Date formatée.

    Example:
        >>> format_date(datetime(2024, 1, 15), language="fr", style="long")
        '15 janvier 2024'
    """
    target_lang = language or get_current_language() if _i18n_manager else "en"

    # Mapping simplifié des formats de date
    # En production, utiliser babel ou une librairie i18n complète
    formats = {
        "short": {
            "fr": "%d/%m/%Y",
            "en": "%m/%d/%Y",
            "de": "%d.%m.%Y",
            "ja": "%Y/%m/%d",
        },
        "medium": {
            "fr": "%d %b %Y",
            "en": "%b %d, %Y",
            "de": "%d.%m.%Y",
            "ja": "%Y年%m月%d日",
        },
        "long": {
            "fr": "%d %B %Y",
            "en": "%B %d, %Y",
            "de": "%d. %B %Y",
            "ja": "%Y年%m月%d日",
        },
    }

    fmt = formats.get(style, formats["medium"]).get(target_lang, "%Y-%m-%d")

    try:
        return date.strftime(fmt)
    except Exception:
        return date.isoformat()


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "_FALLBACK_LANGUAGE",
    # Exceptions
    "I18nError",
    "TranslationNotFoundError",
    "TranslationFileError",
    "LanguageNotSupportedError",
    "I18nNotInitializedError",
    "InvalidTranslationKeyError",
    # Enums
    "SupportedLanguage",
    # Modèles
    "I18nConfig",
    "I18nStats",
    # Classe principale
    "I18nManager",
    # Fonctions principales
    "setup_i18n",
    "t",
    "translate",
    "_",  # Alias de t()
    "get_i18n_manager",
    "get_i18n_stats",
    "set_language",
    "get_current_language",
    "list_available_languages",
    "reset_i18n",
    "has_translation",
    # Helpers — Détection
    "detect_system_language",
    "normalize_language_code",
    # Helpers — Formatage
    "format_number",
    "format_date",
]
