"""Système de migrations SQL pour la bibliothèque locale NexusDL.

Ce module fournit un chargeur de migrations ordonné et idempotent pour
la base de données SQLite de la bibliothèque locale. Les fichiers SQL
sont embarqués dans le package Python et chargés via `importlib.resources`.

Migrations disponibles :
    - 001_initial.sql : Schéma de base (manga, chapter, manga_genre,
      reading_progress, download_history, FTS5 manga_search + triggers + vues)
    - 002_add_reading_progress.sql : Enrichissement du suivi de lecture
      (reading_list, reading_list_manga, reading_session, triggers avancés,
      vues d'agrégation, données initiales)

Règles d'or :
    1. Les migrations sont exécutées dans l'ordre lexicographique de leur nom.
    2. Chaque migration est idempotente (CREATE TABLE IF NOT EXISTS, etc.).
    3. Les migrations ne doivent JAMAIS être modifiées après publication.
       Pour corriger une erreur, créer une nouvelle migration (003_xxx.sql).
    4. Les fichiers SQL sont accédés via `importlib.resources` pour la
       compatibilité avec les wheels, zipapps et environnements frozen.

Exemple d'utilisation :
    >>> from nexusdl.core.library.migrations import get_migrations, apply_migrations
    >>> import aiosqlite
    >>>
    >>> # Lister les migrations disponibles
    >>> migrations = get_migrations()
    >>> for m in migrations:
    ...     print(f"{m.name}: {m.description}")
    001_initial: Initial schema (manga, chapter, FTS5, views)
    002_add_reading_progress: Reading progress, lists, sessions
    >>>
    >>> # Appliquer toutes les migrations à une BDD
    >>> async with aiosqlite.connect("library.db") as db:
    ...     applied = await apply_migrations(db)
    ...     print(f"{len(applied)} migrations appliquées")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Final

import aiosqlite
from loguru import logger


# ============================================================================
# CONSTANTES
# ============================================================================

#: Pattern pour extraire le numéro et le nom d'une migration depuis son nom de fichier.
#: Exemple: "001_initial.sql" → group(1)="001", group(2)="initial"
_MIGRATION_FILENAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(\d{3})_([a-z0-9_]+)\.sql$"
)

#: Package contenant les fichiers de migration (pour importlib.resources).
_MIGRATIONS_PACKAGE: Final[str] = "nexusdl.core.library.migrations"


# ============================================================================
# MODÈLES
# ============================================================================


@dataclass(frozen=True, slots=True)
class Migration:
    """Représentation immuable d'une migration SQL.

    Attributes:
        name: Nom du fichier de migration (ex: "001_initial.sql").
        version: Numéro de version (ex: "001").
        slug: Identifiant court (ex: "initial").
        description: Description courte extraite du commentaire d'en-tête SQL.
        content: Contenu SQL complet du fichier.
    """

    name: str
    version: str
    slug: str
    description: str = ""
    content: str = ""


@dataclass(slots=True)
class MigrationResult:
    """Résultat de l'application d'une migration.

    Attributes:
        migration: La migration appliquée.
        success: True si l'application a réussi.
        error: Message d'erreur si l'application a échoué.
    """

    migration: Migration
    success: bool
    error: str | None = None


# ============================================================================
# FONCTIONS PUBLIQUES
# ============================================================================


def get_migrations() -> list[Migration]:
    """Récupère la liste ordonnée de toutes les migrations disponibles.

    Les migrations sont triées par numéro de version (ordre lexicographique).
    Le contenu SQL de chaque fichier est chargé via `importlib.resources`.

    Returns:
        Liste ordonnée des migrations disponibles.

    Raises:
        FileNotFoundError: Si le package de migrations est introuvable.
        ValueError: Si un fichier SQL ne respecte pas la convention de nommage.

    Example:
        >>> migrations = get_migrations()
        >>> len(migrations) >= 2
        True
        >>> migrations[0].version
        '001'
    """
    migrations_dir = files(_MIGRATIONS_PACKAGE)
    migrations: list[Migration] = []

    # Lister tous les fichiers du package
    for resource in migrations_dir.iterdir():
        filename = resource.name

        # Filtrer uniquement les fichiers .sql
        match = _MIGRATION_FILENAME_PATTERN.match(filename)
        if match is None:
            continue

        version = match.group(1)
        slug = match.group(2)

        # Charger le contenu SQL
        content = resource.read_text(encoding="utf-8")

        # Extraire la description depuis le commentaire d'en-tête
        description = _extract_description(content)

        migrations.append(
            Migration(
                name=filename,
                version=version,
                slug=slug,
                description=description,
                content=content,
            )
        )

    # Trier par numéro de version
    migrations.sort(key=lambda m: m.version)

    return migrations


async def apply_migrations(
    db: aiosqlite.Connection,
    *,
    target_version: str | None = None,
) -> list[MigrationResult]:
    """Applique toutes les migrations en attente à la base de données.

    Crée une table `_migrations` interne pour suivre les migrations déjà
    appliquées, puis exécute dans l'ordre toutes les migrations dont la
    version est supérieure à la dernière version appliquée.

    Args:
        db: Connexion aiosqlite ouverte (avec foreign_keys=ON et WAL mode
            déjà configurés par l'appelant).
        target_version: Version cible maximale (inclus). Si None, applique
            toutes les migrations disponibles.

    Returns:
        Liste des résultats d'application (un par migration exécutée).

    Raises:
        RuntimeError: Si une migration échoue (les précédentes restent appliquées).

    Example:
        >>> async with aiosqlite.connect("library.db") as db:
        ...     results = await apply_migrations(db)
        ...     for r in results:
        ...         print(f"{r.migration.name}: {'OK' if r.success else r.error}")
    """
    _logger = logger.bind(module="migrations")

    # 1. Créer la table de suivi des migrations
    await db.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    await db.commit()

    # 2. Récupérer les migrations déjà appliquées
    async with db.execute("SELECT version FROM _migrations ORDER BY version") as cursor:
        rows = await cursor.fetchall()
        applied_versions: set[str] = {row[0] for row in rows}

    # 3. Récupérer toutes les migrations disponibles
    all_migrations = get_migrations()

    # 4. Filtrer les migrations à appliquer
    pending = [
        m for m in all_migrations
        if m.version not in applied_versions
        and (target_version is None or m.version <= target_version)
    ]

    if not pending:
        _logger.debug("Aucune migration en attente")
        return []

    _logger.info("{} migration(s) à appliquer", len(pending))

    # 5. Appliquer chaque migration dans l'ordre
    results: list[MigrationResult] = []

    for migration in pending:
        _logger.info(
            "Application de la migration {} ({})...",
            migration.version,
            migration.slug,
        )

        try:
            # Exécuter le script SQL complet
            await db.executescript(migration.content)

            # Enregistrer dans la table de suivi
            await db.execute(
                "INSERT INTO _migrations (version, name) VALUES (?, ?)",
                (migration.version, migration.name),
            )
            await db.commit()

            _logger.info(
                "Migration {} appliquée avec succès",
                migration.version,
            )
            results.append(MigrationResult(migration=migration, success=True))

        except Exception as e:
            error_msg = str(e)
            _logger.error(
                "Échec de la migration {}: {}",
                migration.version,
                error_msg,
            )

            # Tenter un rollback (executescript fait un commit implicite,
            # donc le rollback peut ne pas annuler tout — d'où l'importance
            # de l'idempotence des migrations)
            try:
                await db.rollback()
            except Exception:
                pass

            results.append(
                MigrationResult(migration=migration, success=False, error=error_msg)
            )

            # Arrêter l'application des migrations suivantes
            raise RuntimeError(
                f"Migration {migration.version} échouée: {error_msg}. "
                f"Corrigez le problème et relancez."
            ) from e

    return results


async def get_applied_migrations(
    db: aiosqlite.Connection,
) -> list[str]:
    """Retourne la liste des versions de migrations déjà appliquées.

    Args:
        db: Connexion aiosqlite ouverte.

    Returns:
        Liste ordonnée des versions appliquées (ex: ["001", "002"]).
    """
    try:
        async with db.execute(
            "SELECT version FROM _migrations ORDER BY version"
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
    except aiosqlite.OperationalError:
        # La table _migrations n'existe pas encore
        return []


async def get_pending_migrations(
    db: aiosqlite.Connection,
) -> list[Migration]:
    """Retourne la liste des migrations en attente d'application.

    Args:
        db: Connexion aiosqlite ouverte.

    Returns:
        Liste ordonnée des migrations non encore appliquées.
    """
    applied = set(await get_applied_migrations(db))
    all_migrations = get_migrations()
    return [m for m in all_migrations if m.version not in applied]


# ============================================================================
# FONCTIONS INTERNES
# ============================================================================


def _extract_description(sql_content: str) -> str:
    """Extrait une description courte depuis le commentaire d'en-tête SQL.

    Cherche la première ligne de commentaire SQL (--) après le bloc
    d'en-tête et l'utilise comme description.

    Args:
        sql_content: Contenu complet du fichier SQL.

    Returns:
        Description extraite, ou chaîne vide si non trouvée.
    """
    lines = sql_content.splitlines()
    for line in lines:
        stripped = line.strip()
        # Ignorer les lignes de séparateur
        if stripped.startswith("-- ===") or stripped.startswith("-- ---"):
            continue
        # Ignorer les lignes vides de commentaire
        if stripped == "--":
            continue
        # Première ligne de commentaire significative
        if stripped.startswith("--"):
            # Retirer le préfixe "--" et les espaces
            desc = stripped.lstrip("-").strip()
            # Ignorer les lignes de metadata (Rôle, Règles, etc.)
            if desc and not desc.startswith(("Rôle", "Règles", "Intégration", "Dépend")):
                return desc
    return ""


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    "Migration",
    "MigrationResult",
    "get_migrations",
    "apply_migrations",
    "get_applied_migrations",
    "get_pending_migrations",
]
