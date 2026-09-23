"""Utilitaires pour la manipulation du temps dans NexusDL.

Ce module fournit un ensemble complet de fonctions pour manipuler les dates,
heures, durées et timestamps. Il est utilisé dans tout le projet pour :

    - Formatage des dates pour l'affichage (humain, relatif, ISO)
    - Parsing de dates depuis des strings (formats courants)
    - Conversions entre timestamps, datetime, et strings
    - Manipulation de durées (ajout, soustraction, formatage)
    - Comparaisons et validations de dates
    - Gestion des timezones et conversions
    - Calcul de débuts/fin de périodes (jour, semaine, mois)

**Architecture** :
    - Fonctions pures (pas d'état global)
    - Utilisation de `datetime` de la stdlib
    - Pydantic pour les modèles de résultat
    - Thread-safe et async-compatible
    - Support des timezones via `zoneinfo` (Python 3.9+)

**Exemples d'utilisation** :
    >>> from nexusdl.core.utils.time import (
    ...     now, format_datetime, parse_datetime, format_duration,
    ...     parse_duration, is_expired, time_ago,
    ... )
    >>>
    >>> # Obtenir l'heure actuelle
    >>> current = now()
    >>> print(current)
    2026-09-23 14:30:45+00:00
    >>>
    >>> # Formater pour affichage
    >>> format_datetime(current, style="human")
    '23 septembre 2026 à 14:30'
    >>> format_datetime(current, style="relative")
    'à l\'instant'
    >>>
    >>> # Parser une date
    >>> dt = parse_datetime("2026-09-23T14:30:45Z")
    >>> print(dt.year)
    2026
    >>>
    >>> # Durées
    >>> duration = parse_duration("2h 30m")
    >>> print(duration.total_seconds())
    9000.0
    >>> format_duration(duration)
    '2h 30m'
    >>>
    >>> # Vérifier expiration
    >>> is_expired("2026-01-01T00:00:00Z")
    True

Intégration :
    - core/models/* : utilise ces utilitaires pour les timestamps
    - core/downloader/* : utilise ces utilitaires pour les timeouts
    - core/library/* : utilise ces utilitaires pour les dates de publication
    - interfaces/* : utilise ces utilitaires pour l'affichage
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from nexusdl.core.exceptions import NexusDLError


# ============================================================================
# CONSTANTES
# ============================================================================


# Unités de temps en secondes
SECOND: Final[int] = 1
MINUTE: Final[int] = 60
HOUR: Final[int] = 3600
DAY: Final[int] = 86400
WEEK: Final[int] = 604800
MONTH: Final[int] = 2592000  # 30 jours
YEAR: Final[int] = 31536000  # 365 jours

# Formats de date courants
ISO_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%S%z"
ISO_FORMAT_NO_TZ: Final[str] = "%Y-%m-%dT%H:%M:%S"
DATE_FORMAT: Final[str] = "%Y-%m-%d"
TIME_FORMAT: Final[str] = "%H:%M:%S"
DATETIME_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"
HUMAN_DATE_FORMAT: Final[str] = "%d %B %Y"
HUMAN_DATETIME_FORMAT: Final[str] = "%d %B %Y à %H:%M"
COMPACT_FORMAT: Final[str] = "%Y%m%d_%H%M%S"

# Timezones courantes
UTC_TZ: Final[timezone] = UTC

# Patterns pour le parsing de durées
DURATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?:(\d+)y)?(?:(\d+)M)?(?:(\d+)w)?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$",
    re.IGNORECASE,
)

DURATION_HUMAN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:(\d+)\s*(?:y|years?|ans?))?\s*"
    r"(?:(\d+)\s*(?:months?|mois?))?\s*"
    r"(?:(\d+)\s*(?:w|weeks?|sem(?:aines?)?))?\s*"
    r"(?:(\d+)\s*(?:d|days?|jours?))?\s*"
    r"(?:(\d+)\s*(?:h|hours?|heures?))?\s*"
    r"(?:(\d+)\s*(?:m|minutes?|min))?\s*"
    r"(?:(\d+)\s*(?:s|seconds?|sec))?",
    re.IGNORECASE,
)

# Patterns pour le parsing de dates
DATE_PATTERNS: Final[list[tuple[str, re.Pattern[str]]]] = [
    ("iso", re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$")),
    ("date", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    ("datetime", re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")),
    ("time", re.compile(r"^\d{2}:\d{2}:\d{2}$")),
    ("timestamp", re.compile(r"^\d+(?:\.\d+)?$")),
]


# ============================================================================
# EXCEPTIONS
# ============================================================================


class TimeError(NexusDLError):
    """Exception de base pour les erreurs liées au temps."""


class InvalidDateTimeError(TimeError):
    """Exception levée lorsqu'une date/heure est invalide.

    Attributes:
        value: Valeur invalide.
        reason: Raison de l'invalidité.
    """

    def __init__(self, value: Any, reason: str = "") -> None:
        msg = f"Date/heure invalide: {value!r}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.value = value
        self.reason = reason


class InvalidDurationError(TimeError):
    """Exception levée lorsqu'une durée est invalide.

    Attributes:
        value: Valeur invalide.
        reason: Raison de l'invalidité.
    """

    def __init__(self, value: Any, reason: str = "") -> None:
        msg = f"Durée invalide: {value!r}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.value = value
        self.reason = reason


class TimezoneError(TimeError):
    """Exception levée lorsqu'il y a une erreur de timezone."""

    def __init__(self, tz: str, reason: str = "") -> None:
        msg = f"Erreur de timezone: {tz!r}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
        self.tz = tz
        self.reason = reason


# ============================================================================
# ENUMS
# ============================================================================


class TimeUnit(str, Enum):
    """Unités de temps.

    Attributes:
        SECONDS: Secondes.
        MINUTES: Minutes.
        HOURS: Heures.
        DAYS: Jours.
        WEEKS: Semaines.
        MONTHS: Mois (30 jours).
        YEARS: Années (365 jours).
    """

    SECONDS = "seconds"
    MINUTES = "minutes"
    HOURS = "hours"
    DAYS = "days"
    WEEKS = "weeks"
    MONTHS = "months"
    YEARS = "years"

    @property
    def seconds(self) -> int:
        """Nombre de secondes dans cette unité."""
        return {
            TimeUnit.SECONDS: SECOND,
            TimeUnit.MINUTES: MINUTE,
            TimeUnit.HOURS: HOUR,
            TimeUnit.DAYS: DAY,
            TimeUnit.WEEKS: WEEK,
            TimeUnit.MONTHS: MONTH,
            TimeUnit.YEARS: YEAR,
        }[self]

    @property
    def label(self) -> str:
        """Libellé humain."""
        return {
            TimeUnit.SECONDS: "seconde",
            TimeUnit.MINUTES: "minute",
            TimeUnit.HOURS: "heure",
            TimeUnit.DAYS: "jour",
            TimeUnit.WEEKS: "semaine",
            TimeUnit.MONTHS: "mois",
            TimeUnit.YEARS: "année",
        }[self]

    @property
    def label_plural(self) -> str:
        """Libellé humain au pluriel."""
        return {
            TimeUnit.SECONDS: "secondes",
            TimeUnit.MINUTES: "minutes",
            TimeUnit.HOURS: "heures",
            TimeUnit.DAYS: "jours",
            TimeUnit.WEEKS: "semaines",
            TimeUnit.MONTHS: "mois",
            TimeUnit.YEARS: "années",
        }[self]

    @property
    def abbreviation(self) -> str:
        """Abréviation."""
        return {
            TimeUnit.SECONDS: "s",
            TimeUnit.MINUTES: "m",
            TimeUnit.HOURS: "h",
            TimeUnit.DAYS: "j",
            TimeUnit.WEEKS: "sem",
            TimeUnit.MONTHS: "mois",
            TimeUnit.YEARS: "an",
        }[self]


class DateFormat(str, Enum):
    """Formats de date pour l'affichage.

    Attributes:
        ISO: Format ISO 8601 (2026-09-23T14:30:45Z).
        DATE: Format date seule (2026-09-23).
        TIME: Format heure seule (14:30:45).
        DATETIME: Format date et heure (2026-09-23 14:30:45).
        HUMAN: Format humain (23 septembre 2026 à 14:30).
        RELATIVE: Format relatif (il y a 2 heures).
        COMPACT: Format compact (20260923_143045).
    """

    ISO = "iso"
    DATE = "date"
    TIME = "time"
    DATETIME = "datetime"
    HUMAN = "human"
    RELATIVE = "relative"
    COMPACT = "compact"

    @property
    def format_string(self) -> str:
        """Chaîne de format pour strftime."""
        return {
            DateFormat.ISO: ISO_FORMAT,
            DateFormat.DATE: DATE_FORMAT,
            DateFormat.TIME: TIME_FORMAT,
            DateFormat.DATETIME: DATETIME_FORMAT,
            DateFormat.HUMAN: HUMAN_DATETIME_FORMAT,
            DateFormat.RELATIVE: "",  # Géré séparément
            DateFormat.COMPACT: COMPACT_FORMAT,
        }[self]


# ============================================================================
# MODÈLES PYDANTIC
# ============================================================================


class TimeRange(BaseModel):
    """Plage de temps avec début et fin.

    Attributes:
        start: Date/heure de début.
        end: Date/heure de fin.
    """

    start: datetime = Field(..., description="Date/heure de début.")
    end: datetime = Field(..., description="Date/heure de fin.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def duration(self) -> timedelta:
        """Durée de la plage."""
        return self.end - self.start

    @property
    def duration_seconds(self) -> float:
        """Durée en secondes."""
        return self.duration.total_seconds()

    def contains(self, dt: datetime) -> bool:
        """Vérifie si une date est dans la plage.

        Args:
            dt: Date à vérifier.

        Returns:
            True si dt est entre start et end.
        """
        return self.start <= dt <= self.end

    def overlaps(self, other: TimeRange) -> bool:
        """Vérifie si deux plages se chevauchent.

        Args:
            other: Autre plage.

        Returns:
            True si les plages se chevauchent.
        """
        return self.start < other.end and other.start < self.end


class Duration(BaseModel):
    """Durée décomposée en unités.

    Attributes:
        years: Nombre d'années.
        months: Nombre de mois.
        weeks: Nombre de semaines.
        days: Nombre de jours.
        hours: Nombre d'heures.
        minutes: Nombre de minutes.
        seconds: Nombre de secondes.
    """

    years: int = Field(default=0, ge=0, description="Années.")
    months: int = Field(default=0, ge=0, description="Mois.")
    weeks: int = Field(default=0, ge=0, description="Semaines.")
    days: int = Field(default=0, ge=0, description="Jours.")
    hours: int = Field(default=0, ge=0, description="Heures.")
    minutes: int = Field(default=0, ge=0, description="Minutes.")
    seconds: int = Field(default=0, ge=0, description="Secondes.")

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def total_seconds(self) -> float:
        """Durée totale en secondes (approximatif pour mois/années)."""
        return (
            self.years * YEAR
            + self.months * MONTH
            + self.weeks * WEEK
            + self.days * DAY
            + self.hours * HOUR
            + self.minutes * MINUTE
            + self.seconds
        )

    def to_timedelta(self) -> timedelta:
        """Convertit en timedelta (approximatif pour mois/années).

        Returns:
            Instance de timedelta.
        """
        return timedelta(seconds=self.total_seconds)

    @classmethod
    def from_timedelta(cls, td: timedelta) -> Duration:
        """Crée une Duration depuis un timedelta.

        Args:
            td: Timedelta source.

        Returns:
            Instance de Duration.
        """
        total_seconds = int(td.total_seconds())

        days = total_seconds // DAY
        remaining = total_seconds % DAY

        hours = remaining // HOUR
        remaining = remaining % HOUR

        minutes = remaining // MINUTE
        seconds = remaining % MINUTE

        return cls(
            days=days,
            hours=hours,
            minutes=minutes,
            seconds=seconds,
        )


# ============================================================================
# HELPERS — Obtenir l'heure actuelle
# ============================================================================


def now() -> datetime:
    """Retourne l'heure actuelle en UTC.

    Returns:
        Datetime actuelle avec timezone UTC.

    Example:
        >>> current = now()
        >>> print(current.tzinfo)
        UTC
    """
    return datetime.now(UTC)


def utc_now() -> datetime:
    """Alias de now().

    Returns:
        Datetime actuelle en UTC.
    """
    return now()


def today() -> date:
    """Retourne la date actuelle.

    Returns:
        Date du jour.

    Example:
        >>> d = today()
        >>> print(d.year)
        2026
    """
    return date.today()


def timestamp() -> float:
    """Retourne le timestamp Unix actuel (secondes depuis l'epoch).

    Returns:
        Timestamp en secondes (float).

    Example:
        >>> ts = timestamp()
        >>> print(ts > 0)
        True
    """
    return datetime.now(UTC).timestamp()


def timestamp_ms() -> int:
    """Retourne le timestamp Unix actuel en millisecondes.

    Returns:
        Timestamp en millisecondes (int).
    """
    return int(datetime.now(UTC).timestamp() * 1000)


# ============================================================================
# CONVERSIONS
# ============================================================================


def to_datetime(
    value: datetime | date | str | int | float,
    *,
    timezone: timezone | None = None,
) -> datetime:
    """Convertit une valeur en datetime.

    Supporte :
        - datetime : retourné tel quel
        - date : converti en datetime à minuit
        - str : parsé (ISO 8601 ou formats courants)
        - int/float : timestamp Unix

    Args:
        value: Valeur à convertir.
        timezone: Timezone à appliquer (défaut: UTC).

    Returns:
        Instance de datetime.

    Raises:
        InvalidDateTimeError: Si la conversion échoue.

    Example:
        >>> to_datetime("2026-09-23")
        datetime.datetime(2026, 9, 23, 0, 0, tzinfo=datetime.timezone.utc)
        >>> to_datetime(1695481200)
        datetime.datetime(2023, 9, 23, 10, 0, tzinfo=datetime.timezone.utc)
    """
    tz = timezone or UTC

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=tz)
        return value

    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=tz)

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=tz)

    if isinstance(value, str):
        return parse_datetime(value, timezone=tz)

    raise InvalidDateTimeError(value, f"Type non supporté: {type(value).__name__}")


def to_timestamp(dt: datetime | str | int | float) -> float:
    """Convertit une valeur en timestamp Unix (secondes).

    Args:
        dt: Valeur à convertir.

    Returns:
        Timestamp en secondes.

    Raises:
        InvalidDateTimeError: Si la conversion échoue.

    Example:
        >>> to_timestamp(datetime(2026, 9, 23, tzinfo=UTC))
        1790380800.0
    """
    if isinstance(dt, (int, float)):
        return float(dt)

    if isinstance(dt, str):
        dt = parse_datetime(dt)

    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()

    raise InvalidDateTimeError(dt, f"Type non supporté: {type(dt).__name__}")


def to_timestamp_ms(dt: datetime | str | int | float) -> int:
    """Convertit une valeur en timestamp Unix (millisecondes).

    Args:
        dt: Valeur à convertir.

    Returns:
        Timestamp en millisecondes.
    """
    return int(to_timestamp(dt) * 1000)


def to_iso(dt: datetime | str | int | float) -> str:
    """Convertit une valeur en format ISO 8601.

    Args:
        dt: Valeur à convertir.

    Returns:
        Chaîne ISO 8601.

    Example:
        >>> to_iso(datetime(2026, 9, 23, 14, 30, 45, tzinfo=UTC))
        '2026-09-23T14:30:45+00:00'
    """
    if isinstance(dt, str):
        dt = parse_datetime(dt)
    elif isinstance(dt, (int, float)):
        dt = datetime.fromtimestamp(dt, tz=UTC)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    return dt.isoformat()


def to_date(dt: datetime | str | int | float) -> date:
    """Convertit une valeur en date.

    Args:
        dt: Valeur à convertir.

    Returns:
        Instance de date.
    """
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return dt

    dt_obj = to_datetime(dt)
    return dt_obj.date()


# ============================================================================
# PARSING
# ============================================================================


def parse_datetime(
    value: str,
    *,
    timezone: timezone | None = None,
    formats: list[str] | None = None,
) -> datetime:
    """Parse une chaîne en datetime.

    Supporte de nombreux formats courants :
        - ISO 8601 : 2026-09-23T14:30:45Z, 2026-09-23T14:30:45+02:00
        - Date seule : 2026-09-23
        - Datetime : 2026-09-23 14:30:45
        - Timestamp : 1695481200

    Args:
        value: Chaîne à parser.
        timezone: Timezone par défaut si absente de la chaîne.
        formats: Formats personnalisés à essayer (strftime).

    Returns:
        Instance de datetime.

    Raises:
        InvalidDateTimeError: Si le parsing échoue.

    Example:
        >>> parse_datetime("2026-09-23T14:30:45Z")
        datetime.datetime(2026, 9, 23, 14, 30, 45, tzinfo=datetime.timezone.utc)
        >>> parse_datetime("2026-09-23")
        datetime.datetime(2026, 9, 23, 0, 0, tzinfo=datetime.timezone.utc)
    """
    if not value or not isinstance(value, str):
        raise InvalidDateTimeError(value, "Valeur vide ou non-string")

    value = value.strip()
    tz = timezone or UTC

    # Essayer les formats personnalisés en premier
    if formats:
        for fmt in formats:
            try:
                dt = datetime.strptime(value, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=tz)
                return dt
            except ValueError:
                continue

    # Timestamp Unix
    if value.replace(".", "", 1).isdigit():
        try:
            ts = float(value)
            # Heuristique : si > 1e12, c'est probablement en millisecondes
            if ts > 1e12:
                ts = ts / 1000
            return datetime.fromtimestamp(ts, tz=tz)
        except (ValueError, OSError):
            pass

    # ISO 8601 avec timezone
    if "T" in value and ("Z" in value or "+" in value[10:] or value.count("-") > 2):
        try:
            # Remplacer Z par +00:00
            value_normalized = value.replace("Z", "+00:00")
            return datetime.fromisoformat(value_normalized)
        except ValueError:
            pass

    # ISO 8601 sans timezone
    if "T" in value:
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            return dt
        except ValueError:
            pass

    # Date seule (YYYY-MM-DD)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        try:
            dt = datetime.strptime(value, DATE_FORMAT)
            return dt.replace(tzinfo=tz)
        except ValueError:
            pass

    # Datetime sans timezone (YYYY-MM-DD HH:MM:SS)
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", value):
        try:
            dt = datetime.strptime(value, DATETIME_FORMAT)
            return dt.replace(tzinfo=tz)
        except ValueError:
            pass

    # Format humain (DD/MM/YYYY)
    if re.match(r"^\d{2}/\d{2}/\d{4}$", value):
        try:
            dt = datetime.strptime(value, "%d/%m/%Y")
            return dt.replace(tzinfo=tz)
        except ValueError:
            pass

    # Format humain avec heure (DD/MM/YYYY HH:MM)
    if re.match(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$", value):
        try:
            dt = datetime.strptime(value, "%d/%m/%Y %H:%M")
            return dt.replace(tzinfo=tz)
        except ValueError:
            pass

    raise InvalidDateTimeError(value, "Format non reconnu")


def parse_date(value: str) -> date:
    """Parse une chaîne en date.

    Args:
        value: Chaîne à parser.

    Returns:
        Instance de date.

    Raises:
        InvalidDateTimeError: Si le parsing échoue.
    """
    dt = parse_datetime(value)
    return dt.date()


def parse_duration(value: str) -> timedelta:
    """Parse une chaîne de durée en timedelta.

    Supporte les formats :
        - "2h 30m" : 2 heures 30 minutes
        - "1d 12h" : 1 jour 12 heures
        - "90s" : 90 secondes
        - "1y 2M 3w 4d 5h 6m 7s" : format complet

    Args:
        value: Chaîne à parser.

    Returns:
        Instance de timedelta.

    Raises:
        InvalidDurationError: Si le parsing échoue.

    Example:
        >>> parse_duration("2h 30m")
        datetime.timedelta(seconds=9000)
        >>> parse_duration("1d 12h")
        datetime.timedelta(seconds=129600)
    """
    if not value or not isinstance(value, str):
        raise InvalidDurationError(value, "Valeur vide ou non-string")

    value = value.strip().lower()

    # Essayer le format compact (1y2M3w4d5h6m7s)
    match = DURATION_PATTERN.match(value.replace(" ", ""))
    if match and any(match.groups()):
        years, months, weeks, days, hours, minutes, seconds = [
            int(g) if g else 0 for g in match.groups()
        ]
        total_seconds = (
            years * YEAR
            + months * MONTH
            + weeks * WEEK
            + days * DAY
            + hours * HOUR
            + minutes * MINUTE
            + seconds
        )
        return timedelta(seconds=total_seconds)

    # Essayer le format humain (2 hours 30 minutes)
    match = DURATION_HUMAN_PATTERN.match(value)
    if match and any(match.groups()):
        years, months, weeks, days, hours, minutes, seconds = [
            int(g) if g else 0 for g in match.groups()
        ]
        total_seconds = (
            years * YEAR
            + months * MONTH
            + weeks * WEEK
            + days * DAY
            + hours * HOUR
            + minutes * MINUTE
            + seconds
        )
        return timedelta(seconds=total_seconds)

    # Essayer un nombre seul (secondes)
    try:
        seconds = float(value)
        return timedelta(seconds=seconds)
    except ValueError:
        pass

    raise InvalidDurationError(value, "Format non reconnu")


# ============================================================================
# FORMATAGE
# ============================================================================


def format_datetime(
    dt: datetime | str | int | float,
    *,
    style: str | DateFormat = DateFormat.DATETIME,
    timezone: timezone | None = None,
) -> str:
    """Formate une date/heure pour l'affichage.

    Args:
        dt: Date/heure à formater.
        style: Style de formatage (iso, date, time, datetime, human, relative, compact).
        timezone: Timezone pour l'affichage (défaut: UTC).

    Returns:
        Chaîne formatée.

    Raises:
        InvalidDateTimeError: Si la date est invalide.

    Example:
        >>> dt = datetime(2026, 9, 23, 14, 30, 45, tzinfo=UTC)
        >>> format_datetime(dt, style="human")
        '23 septembre 2026 à 14:30'
        >>> format_datetime(dt, style="relative")
        'dans 2 ans'
        >>> format_datetime(dt, style="compact")
        '20260923_143045'
    """
    if isinstance(style, str):
        try:
            style = DateFormat(style)
        except ValueError:
            raise InvalidDateTimeError(style, f"Style inconnu: {style}")

    dt_obj = to_datetime(dt, timezone=timezone)

    if style == DateFormat.RELATIVE:
        return format_relative(dt_obj)

    if style == DateFormat.HUMAN:
        # Format humain en français
        months_fr = [
            "janvier", "février", "mars", "avril", "mai", "juin",
            "juillet", "août", "septembre", "octobre", "novembre", "décembre",
        ]
        month_name = months_fr[dt_obj.month - 1]
        return f"{dt_obj.day} {month_name} {dt_obj.year} à {dt_obj.hour:02d}:{dt_obj.minute:02d}"

    return dt_obj.strftime(style.format_string)


def format_relative(dt: datetime | str | int | float, *, reference: datetime | None = None) -> str:
    """Formate une date en format relatif (il y a X, dans X).

    Args:
        dt: Date à formater.
        reference: Date de référence (défaut: maintenant).

    Returns:
        Chaîne relative.

    Example:
        >>> past = now() - timedelta(hours=2)
        >>> format_relative(past)
        'il y a 2 heures'
        >>> future = now() + timedelta(days=3)
        >>> format_relative(future)
        'dans 3 jours'
    """
    dt_obj = to_datetime(dt)
    ref = reference or now()

    # Assurer que les deux ont une timezone
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=UTC)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=UTC)

    delta = ref - dt_obj
    seconds = int(delta.total_seconds())
    is_past = seconds > 0
    abs_seconds = abs(seconds)

    if abs_seconds < 10:
        return "à l'instant"

    # Déterminer l'unité appropriée
    if abs_seconds < MINUTE:
        value = abs_seconds
        unit = "seconde" if value == 1 else "secondes"
    elif abs_seconds < HOUR:
        value = abs_seconds // MINUTE
        unit = "minute" if value == 1 else "minutes"
    elif abs_seconds < DAY:
        value = abs_seconds // HOUR
        unit = "heure" if value == 1 else "heures"
    elif abs_seconds < WEEK:
        value = abs_seconds // DAY
        unit = "jour" if value == 1 else "jours"
    elif abs_seconds < MONTH:
        value = abs_seconds // WEEK
        unit = "semaine" if value == 1 else "semaines"
    elif abs_seconds < YEAR:
        value = abs_seconds // MONTH
        unit = "mois"
    else:
        value = abs_seconds // YEAR
        unit = "an" if value == 1 else "ans"

    if is_past:
        return f"il y a {value} {unit}"
    else:
        return f"dans {value} {unit}"


def format_duration(
    duration: timedelta | Duration | float | int,
    *,
    style: str = "human",
    precision: int = 2,
) -> str:
    """Formate une durée pour l'affichage.

    Args:
        duration: Durée à formater.
        style: Style de formatage ("human", "compact", "iso").
        precision: Nombre d'unités à afficher (défaut: 2).

    Returns:
        Chaîne formatée.

    Example:
        >>> format_duration(timedelta(hours=2, minutes=30))
        '2h 30m'
        >>> format_duration(timedelta(days=1, hours=12), style="human")
        '1 jour 12 heures'
        >>> format_duration(timedelta(seconds=90), style="compact")
        '1m 30s'
    """
    if isinstance(duration, Duration):
        total_seconds = duration.total_seconds
    elif isinstance(duration, timedelta):
        total_seconds = duration.total_seconds()
    else:
        total_seconds = float(duration)

    if total_seconds < 0:
        total_seconds = abs(total_seconds)
        prefix = "-"
    else:
        prefix = ""

    if style == "iso":
        # Format ISO 8601 (PT2H30M)
        hours = int(total_seconds // HOUR)
        minutes = int((total_seconds % HOUR) // MINUTE)
        seconds = int(total_seconds % MINUTE)
        parts = []
        if hours > 0:
            parts.append(f"{hours}H")
        if minutes > 0:
            parts.append(f"{minutes}M")
        if seconds > 0 or not parts:
            parts.append(f"{seconds}S")
        return f"{prefix}PT{''.join(parts)}"

    # Décomposer en unités
    units: list[tuple[int, str, str]] = []

    years = int(total_seconds // YEAR)
    total_seconds %= YEAR
    if years > 0:
        units.append((years, "an", "ans"))

    months = int(total_seconds // MONTH)
    total_seconds %= MONTH
    if months > 0:
        units.append((months, "mois", "mois"))

    weeks = int(total_seconds // WEEK)
    total_seconds %= WEEK
    if weeks > 0:
        units.append((weeks, "semaine", "semaines"))

    days = int(total_seconds // DAY)
    total_seconds %= DAY
    if days > 0:
        units.append((days, "jour", "jours"))

    hours = int(total_seconds // HOUR)
    total_seconds %= HOUR
    if hours > 0:
        units.append((hours, "heure", "heures"))

    minutes = int(total_seconds // MINUTE)
    total_seconds %= MINUTE
    if minutes > 0:
        units.append((minutes, "minute", "minutes"))

    seconds = int(total_seconds)
    if seconds > 0 or not units:
        units.append((seconds, "seconde", "secondes"))

    # Limiter à precision unités
    units = units[:precision]

    if style == "compact":
        # Format compact (2h 30m)
        abbreviations = {
            "an": "an", "ans": "an",
            "mois": "mois",
            "semaine": "sem", "semaines": "sem",
            "jour": "j", "jours": "j",
            "heure": "h", "heures": "h",
            "minute": "m", "minutes": "m",
            "seconde": "s", "secondes": "s",
        }
        parts = [f"{value}{abbreviations[unit]}" for value, unit, _ in units]
        return f"{prefix}{' '.join(parts)}"

    # Format human (2 heures 30 minutes)
    parts = []
    for value, singular, plural in units:
        unit = singular if value == 1 else plural
        parts.append(f"{value} {unit}")

    return f"{prefix}{' '.join(parts)}"


def format_timestamp(ts: float | int, *, style: str = "datetime") -> str:
    """Formate un timestamp Unix pour l'affichage.

    Args:
        ts: Timestamp en secondes.
        style: Style de formatage.

    Returns:
        Chaîne formatée.
    """
    dt = datetime.fromtimestamp(ts, tz=UTC)
    return format_datetime(dt, style=style)


# ============================================================================
# MANIPULATION
# ============================================================================


def add_duration(
    dt: datetime | str | int | float,
    duration: timedelta | Duration | str,
) -> datetime:
    """Ajoute une durée à une date.

    Args:
        dt: Date de base.
        duration: Durée à ajouter (timedelta, Duration, ou string).

    Returns:
        Nouvelle date.

    Example:
        >>> base = datetime(2026, 9, 23, tzinfo=UTC)
        >>> add_duration(base, timedelta(days=7))
        datetime.datetime(2026, 9, 30, 0, 0, tzinfo=datetime.timezone.utc)
        >>> add_duration(base, "2h 30m")
        datetime.datetime(2026, 9, 23, 2, 30, tzinfo=datetime.timezone.utc)
    """
    dt_obj = to_datetime(dt)

    if isinstance(duration, str):
        duration = parse_duration(duration)
    elif isinstance(duration, Duration):
        duration = duration.to_timedelta()

    return dt_obj + duration


def subtract_duration(
    dt: datetime | str | int | float,
    duration: timedelta | Duration | str,
) -> datetime:
    """Soustrait une durée d'une date.

    Args:
        dt: Date de base.
        duration: Durée à soustraire.

    Returns:
        Nouvelle date.
    """
    dt_obj = to_datetime(dt)

    if isinstance(duration, str):
        duration = parse_duration(duration)
    elif isinstance(duration, Duration):
        duration = duration.to_timedelta()

    return dt_obj - duration


def start_of_day(dt: datetime | str | int | float) -> datetime:
    """Retourne le début du jour (00:00:00).

    Args:
        dt: Date source.

    Returns:
        Début du jour.

    Example:
        >>> dt = datetime(2026, 9, 23, 14, 30, 45, tzinfo=UTC)
        >>> start_of_day(dt)
        datetime.datetime(2026, 9, 23, 0, 0, tzinfo=datetime.timezone.utc)
    """
    dt_obj = to_datetime(dt)
    return dt_obj.replace(hour=0, minute=0, second=0, microsecond=0)


def end_of_day(dt: datetime | str | int | float) -> datetime:
    """Retourne la fin du jour (23:59:59.999999).

    Args:
        dt: Date source.

    Returns:
        Fin du jour.
    """
    dt_obj = to_datetime(dt)
    return dt_obj.replace(hour=23, minute=59, second=59, microsecond=999999)


def start_of_week(dt: datetime | str | int | float) -> datetime:
    """Retourne le début de la semaine (lundi 00:00:00).

    Args:
        dt: Date source.

    Returns:
        Début de la semaine.
    """
    dt_obj = to_datetime(dt)
    # weekday() : 0=lundi, 6=dimanche
    days_since_monday = dt_obj.weekday()
    start = dt_obj - timedelta(days=days_since_monday)
    return start_of_day(start)


def end_of_week(dt: datetime | str | int | float) -> datetime:
    """Retourne la fin de la semaine (dimanche 23:59:59).

    Args:
        dt: Date source.

    Returns:
        Fin de la semaine.
    """
    dt_obj = to_datetime(dt)
    days_until_sunday = 6 - dt_obj.weekday()
    end = dt_obj + timedelta(days=days_until_sunday)
    return end_of_day(end)


def start_of_month(dt: datetime | str | int | float) -> datetime:
    """Retourne le début du mois (jour 1, 00:00:00).

    Args:
        dt: Date source.

    Returns:
        Début du mois.
    """
    dt_obj = to_datetime(dt)
    start = dt_obj.replace(day=1)
    return start_of_day(start)


def end_of_month(dt: datetime | str | int | float) -> datetime:
    """Retourne la fin du mois (dernier jour, 23:59:59).

    Args:
        dt: Date source.

    Returns:
        Fin du mois.
    """
    dt_obj = to_datetime(dt)
    # Dernier jour du mois
    if dt_obj.month == 12:
        next_month = dt_obj.replace(year=dt_obj.year + 1, month=1, day=1)
    else:
        next_month = dt_obj.replace(month=dt_obj.month + 1, day=1)
    last_day = next_month - timedelta(days=1)
    return end_of_day(last_day)


def start_of_year(dt: datetime | str | int | float) -> datetime:
    """Retourne le début de l'année (1er janvier, 00:00:00).

    Args:
        dt: Date source.

    Returns:
        Début de l'année.
    """
    dt_obj = to_datetime(dt)
    start = dt_obj.replace(month=1, day=1)
    return start_of_day(start)


def end_of_year(dt: datetime | str | int | float) -> datetime:
    """Retourne la fin de l'année (31 décembre, 23:59:59).

    Args:
        dt: Date source.

    Returns:
        Fin de l'année.
    """
    dt_obj = to_datetime(dt)
    end = dt_obj.replace(month=12, day=31)
    return end_of_day(end)


# ============================================================================
# COMPARAISONS
# ============================================================================


def is_before(
    dt1: datetime | str | int | float,
    dt2: datetime | str | int | float,
) -> bool:
    """Vérifie si dt1 est avant dt2.

    Args:
        dt1: Première date.
        dt2: Deuxième date.

    Returns:
        True si dt1 < dt2.
    """
    dt1_obj = to_datetime(dt1)
    dt2_obj = to_datetime(dt2)
    return dt1_obj < dt2_obj


def is_after(
    dt1: datetime | str | int | float,
    dt2: datetime | str | int | float,
) -> bool:
    """Vérifie si dt1 est après dt2.

    Args:
        dt1: Première date.
        dt2: Deuxième date.

    Returns:
        True si dt1 > dt2.
    """
    dt1_obj = to_datetime(dt1)
    dt2_obj = to_datetime(dt2)
    return dt1_obj > dt2_obj


def is_between(
    dt: datetime | str | int | float,
    start: datetime | str | int | float,
    end: datetime | str | int | float,
) -> bool:
    """Vérifie si dt est entre start et end (inclusif).

    Args:
        dt: Date à vérifier.
        start: Début de la plage.
        end: Fin de la plage.

    Returns:
        True si start <= dt <= end.
    """
    dt_obj = to_datetime(dt)
    start_obj = to_datetime(start)
    end_obj = to_datetime(end)
    return start_obj <= dt_obj <= end_obj


def is_expired(
    dt: datetime | str | int | float,
    *,
    reference: datetime | None = None,
) -> bool:
    """Vérifie si une date est expirée (dans le passé).

    Args:
        dt: Date à vérifier.
        reference: Date de référence (défaut: maintenant).

    Returns:
        True si dt < reference.
    """
    dt_obj = to_datetime(dt)
    ref = reference or now()

    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=UTC)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=UTC)

    return dt_obj < ref


def is_today(dt: datetime | str | int | float) -> bool:
    """Vérifie si une date est aujourd'hui.

    Args:
        dt: Date à vérifier.

    Returns:
        True si c'est aujourd'hui.
    """
    dt_obj = to_datetime(dt)
    return dt_obj.date() == today()


def is_yesterday(dt: datetime | str | int | float) -> bool:
    """Vérifie si une date est hier.

    Args:
        dt: Date à vérifier.

    Returns:
        True si c'est hier.
    """
    dt_obj = to_datetime(dt)
    yesterday = today() - timedelta(days=1)
    return dt_obj.date() == yesterday


def is_tomorrow(dt: datetime | str | int | float) -> bool:
    """Vérifie si une date est demain.

    Args:
        dt: Date à vérifier.

    Returns:
        True si c'est demain.
    """
    dt_obj = to_datetime(dt)
    tomorrow = today() + timedelta(days=1)
    return dt_obj.date() == tomorrow


def is_same_day(
    dt1: datetime | str | int | float,
    dt2: datetime | str | int | float,
) -> bool:
    """Vérifie si deux dates sont le même jour.

    Args:
        dt1: Première date.
        dt2: Deuxième date.

    Returns:
        True si même jour.
    """
    dt1_obj = to_datetime(dt1)
    dt2_obj = to_datetime(dt2)
    return dt1_obj.date() == dt2_obj.date()


# ============================================================================
# DIFFÉRENCES
# ============================================================================


def time_diff(
    dt1: datetime | str | int | float,
    dt2: datetime | str | int | float,
) -> timedelta:
    """Calcule la différence entre deux dates.

    Args:
        dt1: Première date.
        dt2: Deuxième date.

    Returns:
        Différence (dt1 - dt2).
    """
    dt1_obj = to_datetime(dt1)
    dt2_obj = to_datetime(dt2)
    return dt1_obj - dt2_obj


def time_diff_seconds(
    dt1: datetime | str | int | float,
    dt2: datetime | str | int | float,
) -> float:
    """Calcule la différence en secondes.

    Args:
        dt1: Première date.
        dt2: Deuxième date.

    Returns:
        Différence en secondes.
    """
    return time_diff(dt1, dt2).total_seconds()


def time_ago(dt: datetime | str | int | float) -> str:
    """Retourne une chaîne "il y a X" pour une date passée.

    Args:
        dt: Date passée.

    Returns:
        Chaîne relative.

    Example:
        >>> past = now() - timedelta(hours=2)
        >>> time_ago(past)
        'il y a 2 heures'
    """
    return format_relative(dt)


def time_until(dt: datetime | str | int | float) -> str:
    """Retourne une chaîne "dans X" pour une date future.

    Args:
        dt: Date future.

    Returns:
        Chaîne relative.

    Example:
        >>> future = now() + timedelta(days=3)
        >>> time_until(future)
        'dans 3 jours'
    """
    return format_relative(dt)


# ============================================================================
# TIMEZONES
# ============================================================================


def to_timezone(
    dt: datetime | str | int | float,
    tz: timezone | str,
) -> datetime:
    """Convertit une date vers une timezone.

    Args:
        dt: Date à convertir.
        tz: Timezone cible (objet ou nom).

    Returns:
        Date dans la nouvelle timezone.

    Example:
        >>> utc_dt = datetime(2026, 9, 23, 14, 0, tzinfo=UTC)
        >>> paris_tz = timezone(timedelta(hours=2))
        >>> to_timezone(utc_dt, paris_tz)
        datetime.datetime(2026, 9, 23, 16, 0, tzinfo=datetime.timezone(datetime.timedelta(seconds=7200)))
    """
    dt_obj = to_datetime(dt)

    if isinstance(tz, str):
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(tz)
        except ImportError:
            raise TimezoneError(tz, "zoneinfo non disponible")
        except Exception as e:
            raise TimezoneError(tz, str(e)) from e

    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=UTC)

    return dt_obj.astimezone(tz)


def to_utc(dt: datetime | str | int | float) -> datetime:
    """Convertit une date vers UTC.

    Args:
        dt: Date à convertir.

    Returns:
        Date en UTC.
    """
    return to_timezone(dt, UTC)


def get_timezone_offset(dt: datetime | str | int | float) -> str:
    """Retourne l'offset de timezone d'une date (ex: "+02:00").

    Args:
        dt: Date source.

    Returns:
        Offset au format string.
    """
    dt_obj = to_datetime(dt)
    if dt_obj.tzinfo is None:
        return "+00:00"

    offset = dt_obj.utcoffset()
    if offset is None:
        return "+00:00"

    total_seconds = int(offset.total_seconds())
    hours, remainder = divmod(abs(total_seconds), 3600)
    minutes = remainder // 60
    sign = "+" if total_seconds >= 0 else "-"

    return f"{sign}{hours:02d}:{minutes:02d}"


# ============================================================================
# VALIDATION
# ============================================================================


def is_valid_datetime(value: Any) -> bool:
    """Vérifie si une valeur peut être convertie en datetime.

    Args:
        value: Valeur à vérifier.

    Returns:
        True si convertible.
    """
    try:
        to_datetime(value)
        return True
    except (InvalidDateTimeError, ValueError, TypeError):
        return False


def is_valid_duration(value: Any) -> bool:
    """Vérifie si une valeur peut être convertie en durée.

    Args:
        value: Valeur à vérifier.

    Returns:
        True si convertible.
    """
    try:
        if isinstance(value, (timedelta, Duration)):
            return True
        if isinstance(value, (int, float)):
            return value >= 0
        if isinstance(value, str):
            parse_duration(value)
            return True
        return False
    except (InvalidDurationError, ValueError):
        return False


def is_valid_timezone(tz: str) -> bool:
    """Vérifie si un nom de timezone est valide.

    Args:
        tz: Nom de timezone (ex: "Europe/Paris").

    Returns:
        True si valide.
    """
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
        return True
    except Exception:
        return False


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "SECOND",
    "MINUTE",
    "HOUR",
    "DAY",
    "WEEK",
    "MONTH",
    "YEAR",
    "ISO_FORMAT",
    "DATE_FORMAT",
    "TIME_FORMAT",
    "DATETIME_FORMAT",
    "HUMAN_DATE_FORMAT",
    "HUMAN_DATETIME_FORMAT",
    "COMPACT_FORMAT",
    "UTC_TZ",
    # Exceptions
    "TimeError",
    "InvalidDateTimeError",
    "InvalidDurationError",
    "TimezoneError",
    # Enums
    "TimeUnit",
    "DateFormat",
    # Modèles
    "TimeRange",
    "Duration",
    # Helpers — Heure actuelle
    "now",
    "utc_now",
    "today",
    "timestamp",
    "timestamp_ms",
    # Conversions
    "to_datetime",
    "to_timestamp",
    "to_timestamp_ms",
    "to_iso",
    "to_date",
    # Parsing
    "parse_datetime",
    "parse_date",
    "parse_duration",
    # Formatage
    "format_datetime",
    "format_relative",
    "format_duration",
    "format_timestamp",
    # Manipulation
    "add_duration",
    "subtract_duration",
    "start_of_day",
    "end_of_day",
    "start_of_week",
    "end_of_week",
    "start_of_month",
    "end_of_month",
    "start_of_year",
    "end_of_year",
    # Comparaisons
    "is_before",
    "is_after",
    "is_between",
    "is_expired",
    "is_today",
    "is_yesterday",
    "is_tomorrow",
    "is_same_day",
    # Différences
    "time_diff",
    "time_diff_seconds",
    "time_ago",
    "time_until",
    # Timezones
    "to_timezone",
    "to_utc",
    "get_timezone_offset",
    # Validation
    "is_valid_datetime",
    "is_valid_duration",
    "is_valid_timezone",
]
