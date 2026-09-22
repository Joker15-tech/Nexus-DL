-- ============================================================================
-- NEXUSDL — Migration : 002_add_reading_progress.sql
-- ============================================================================
--
-- Rôle :
--   Ajoute le système complet de suivi de lecture utilisateur à la
--   bibliothèque locale. Cette migration enrichit le schéma initial
--   (001_initial.sql) avec :
--
--     1. Table `reading_progress` : Suivi granulaire par manga/chapitre
--        avec statut de lecture, score, notes et compteur de relectures.
--
--     2. Table `reading_list` : Listes de lecture personnalisées par
--        l'utilisateur (ex: "À lire cet été", "Favoris", "Classiques").
--
--     3. Table `reading_list_manga` : Relation Many-to-Many entre
--        listes de lecture et mangas.
--
--     4. Vues d'agrégation : Statistiques de lecture, listes "À continuer",
--        "Récemment lus", "Favoris", etc. pour les interfaces.
--
--     5. Triggers : Maintenance automatique des timestamps et compteurs.
--
-- Dépend de :
--   - Migration 001_initial.sql (tables manga, chapter)
--
-- Règles d'or :
--   1. Toutes les tables utilisent `CREATE TABLE IF NOT EXISTS` pour
--      permettre une réexécution idempotente (sécurité en cas de retry).
--   2. Les timestamps utilisent `datetime('now')` (UTC implicite SQLite).
--   3. Les contraintes CHECK valident les enums au niveau BDD (pas seulement Python).
--   4. Les triggers maintiennent les compteurs dénormalisés pour les performances.
--   5. Les vues ne stockent pas de données — elles sont recalculées à chaque requête.
--
-- Intégration :
--   - Exécutée automatiquement par `core/library/database.py` après 001_initial.sql
--     via `await db.executescript(sql_content)`.
--   - Les nouvelles colonnes de `reading_progress` sont optionnelles (NULL par défaut)
--     pour la rétrocompatibilité avec les données existantes.
--
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. TABLE : reading_progress
-- ----------------------------------------------------------------------------
-- Suivi granulaire de la progression de lecture par manga.
-- Une entrée par manga (le chapitre et la page les plus avancés).
-- Supporte plusieurs statuts de lecture (comme MAL/Anilist).
CREATE TABLE IF NOT EXISTS reading_progress (
    manga_id TEXT PRIMARY KEY,
    chapter_id TEXT NOT NULL,
    page INTEGER NOT NULL DEFAULT 1 CHECK (page >= 0),

    -- Statut de lecture (enum)
    reading_status TEXT NOT NULL DEFAULT 'READING' CHECK (reading_status IN (
        'READING',          -- En cours de lecture
        'COMPLETED',        -- Terminé
        'ON_HOLD',          -- En pause
        'DROPPED',          -- Abandonné
        'PLAN_TO_READ',     -- À lire (wishlist)
        'RE_READING'        -- Relecture en cours
    )),

    -- Métadonnées utilisateur
    score REAL CHECK (score IS NULL OR (score >= 0.0 AND score <= 10.0)),
    notes TEXT,
    reread_count INTEGER NOT NULL DEFAULT 0 CHECK (reread_count >= 0),
    started_at TEXT,
    completed_at TEXT,

    -- Timestamps de maintenance
    last_read_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),

    -- Intégrité référentielle
    FOREIGN KEY (manga_id) REFERENCES manga(id) ON DELETE CASCADE,
    FOREIGN KEY (chapter_id) REFERENCES chapter(id) ON DELETE CASCADE
);

-- Index pour les requêtes de tri courantes
CREATE INDEX IF NOT EXISTS idx_reading_progress_status ON reading_progress(reading_status);
CREATE INDEX IF NOT EXISTS idx_reading_progress_last_read ON reading_progress(last_read_at DESC);
CREATE INDEX IF NOT EXISTS idx_reading_progress_score ON reading_progress(score DESC) WHERE score IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_reading_progress_updated ON reading_progress(updated_at DESC);


-- ----------------------------------------------------------------------------
-- 2. TABLE : reading_list
-- ----------------------------------------------------------------------------
-- Listes de lecture personnalisées par l'utilisateur.
-- Exemples : "À lire cet été", "Favoris", "Classiques du seinen", etc.
CREATE TABLE IF NOT EXISTS reading_list (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    icon TEXT, -- Nom d'icône (ex: "heart", "star", "book")
    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    is_public INTEGER NOT NULL DEFAULT 0 CHECK (is_public IN (0, 1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),

    UNIQUE (name)
);

CREATE INDEX IF NOT EXISTS idx_reading_list_sort_order ON reading_list(sort_order);
CREATE INDEX IF NOT EXISTS idx_reading_list_is_default ON reading_list(is_default);


-- ----------------------------------------------------------------------------
-- 3. TABLE : reading_list_manga
-- ----------------------------------------------------------------------------
-- Relation Many-to-Many entre listes de lecture et mangas.
-- Permet à un manga d'appartenir à plusieurs listes simultanément.
CREATE TABLE IF NOT EXISTS reading_list_manga (
    list_id TEXT NOT NULL,
    manga_id TEXT NOT NULL,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    position INTEGER NOT NULL DEFAULT 0, -- Position dans la liste (pour tri manuel)
    notes TEXT, -- Notes spécifiques à cette liste

    PRIMARY KEY (list_id, manga_id),
    FOREIGN KEY (list_id) REFERENCES reading_list(id) ON DELETE CASCADE,
    FOREIGN KEY (manga_id) REFERENCES manga(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reading_list_manga_list ON reading_list_manga(list_id, position);
CREATE INDEX IF NOT EXISTS idx_reading_list_manga_manga ON reading_list_manga(manga_id);


-- ----------------------------------------------------------------------------
-- 4. TABLE : reading_session
-- ----------------------------------------------------------------------------
-- Journal détaillé des sessions de lecture (pour statistiques avancées).
-- Une entrée par session de lecture (ouverture → fermeture du lecteur).
CREATE TABLE IF NOT EXISTS reading_session (
    id TEXT PRIMARY KEY,
    manga_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    start_page INTEGER NOT NULL,
    end_page INTEGER NOT NULL,
    pages_read INTEGER NOT NULL CHECK (pages_read >= 0),
    duration_seconds REAL NOT NULL CHECK (duration_seconds >= 0.0),
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,

    FOREIGN KEY (manga_id) REFERENCES manga(id) ON DELETE CASCADE,
    FOREIGN KEY (chapter_id) REFERENCES chapter(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reading_session_manga ON reading_session(manga_id);
CREATE INDEX IF NOT EXISTS idx_reading_session_started ON reading_session(started_at DESC);


-- ----------------------------------------------------------------------------
-- 5. TRIGGERS : Maintenance automatique
-- ----------------------------------------------------------------------------

-- Trigger : Met à jour `updated_at` automatiquement à chaque UPDATE
CREATE TRIGGER IF NOT EXISTS trg_reading_progress_update_timestamp
AFTER UPDATE ON reading_progress
FOR EACH ROW
BEGIN
    UPDATE reading_progress
    SET updated_at = datetime('now')
    WHERE manga_id = NEW.manga_id;
END;

-- Trigger : Met à jour `last_read_at` quand la progression change
CREATE TRIGGER IF NOT EXISTS trg_reading_progress_update_last_read
AFTER UPDATE OF chapter_id, page ON reading_progress
FOR EACH ROW
BEGIN
    UPDATE reading_progress
    SET last_read_at = datetime('now')
    WHERE manga_id = NEW.manga_id;
END;

-- Trigger : Incrémente `reread_count` quand on repasse à READING après COMPLETED
CREATE TRIGGER IF NOT EXISTS trg_reading_progress_reread
AFTER UPDATE OF reading_status ON reading_progress
WHEN OLD.reading_status = 'COMPLETED' AND NEW.reading_status = 'READING'
FOR EACH ROW
BEGIN
    UPDATE reading_progress
    SET reread_count = reread_count + 1,
        started_at = datetime('now'),
        completed_at = NULL
    WHERE manga_id = NEW.manga_id;
END;

-- Trigger : Met à jour `completed_at` quand on passe à COMPLETED
CREATE TRIGGER IF NOT EXISTS trg_reading_progress_completed
AFTER UPDATE OF reading_status ON reading_progress
WHEN NEW.reading_status = 'COMPLETED' AND OLD.reading_status != 'COMPLETED'
FOR EACH ROW
BEGIN
    UPDATE reading_progress
    SET completed_at = datetime('now')
    WHERE manga_id = NEW.manga_id;
END;

-- Trigger : Met à jour `started_at` quand on passe à READING pour la première fois
CREATE TRIGGER IF NOT EXISTS trg_reading_progress_started
AFTER UPDATE OF reading_status ON reading_progress
WHEN NEW.reading_status = 'READING' AND OLD.reading_status = 'PLAN_TO_READ' AND NEW.started_at IS NULL
FOR EACH ROW
BEGIN
    UPDATE reading_progress
    SET started_at = datetime('now')
    WHERE manga_id = NEW.manga_id;
END;

-- Trigger : Met à jour `updated_at` de reading_list à chaque modification
CREATE TRIGGER IF NOT EXISTS trg_reading_list_update_timestamp
AFTER UPDATE ON reading_list
FOR EACH ROW
BEGIN
    UPDATE reading_list
    SET updated_at = datetime('now')
    WHERE id = NEW.id;
END;


-- ----------------------------------------------------------------------------
-- 6. VUES : Requêtes courantes optimisées
-- ----------------------------------------------------------------------------

-- Vue : Statistiques globales de lecture
CREATE VIEW IF NOT EXISTS v_reading_stats AS
SELECT
    (SELECT COUNT(*) FROM reading_progress WHERE reading_status = 'READING') AS currently_reading,
    (SELECT COUNT(*) FROM reading_progress WHERE reading_status = 'COMPLETED') AS completed,
    (SELECT COUNT(*) FROM reading_progress WHERE reading_status = 'ON_HOLD') AS on_hold,
    (SELECT COUNT(*) FROM reading_progress WHERE reading_status = 'DROPPED') AS dropped,
    (SELECT COUNT(*) FROM reading_progress WHERE reading_status = 'PLAN_TO_READ') AS plan_to_read,
    (SELECT COUNT(*) FROM reading_progress WHERE reading_status = 'RE_READING') AS re_reading,
    (SELECT COUNT(*) FROM reading_progress) AS total_tracked,
    (SELECT AVG(score) FROM reading_progress WHERE score IS NOT NULL) AS average_score,
    (SELECT SUM(reread_count) FROM reading_progress) AS total_rereads,
    (SELECT COUNT(*) FROM reading_session) AS total_sessions,
    (SELECT SUM(duration_seconds) FROM reading_session) AS total_reading_time_seconds;

-- Vue : "Continuer la lecture" (mangas en cours, triés par dernière lecture)
CREATE VIEW IF NOT EXISTS v_continue_reading AS
SELECT
    m.id AS manga_id,
    m.title,
    m.cover_url,
    m.site,
    rp.chapter_id,
    c.number AS chapter_number,
    c.title AS chapter_title,
    rp.page,
    rp.last_read_at,
    rp.reading_status,
    rp.score
FROM reading_progress rp
JOIN manga m ON rp.manga_id = m.id
JOIN chapter c ON rp.chapter_id = c.id
WHERE rp.reading_status IN ('READING', 'RE_READING')
ORDER BY rp.last_read_at DESC;

-- Vue : "Récemment terminés" (30 derniers mangas complétés)
CREATE VIEW IF NOT EXISTS v_recently_completed AS
SELECT
    m.id AS manga_id,
    m.title,
    m.cover_url,
    m.site,
    rp.completed_at,
    rp.score,
    rp.reread_count
FROM reading_progress rp
JOIN manga m ON rp.manga_id = m.id
WHERE rp.reading_status = 'COMPLETED'
  AND rp.completed_at IS NOT NULL
ORDER BY rp.completed_at DESC
LIMIT 30;

-- Vue : "À lire" (wishlist)
CREATE VIEW IF NOT EXISTS v_plan_to_read AS
SELECT
    m.id AS manga_id,
    m.title,
    m.cover_url,
    m.site,
    m.status AS manga_status,
    m.author,
    rp.updated_at AS added_at
FROM reading_progress rp
JOIN manga m ON rp.manga_id = m.id
WHERE rp.reading_status = 'PLAN_TO_READ'
ORDER BY rp.updated_at DESC;

-- Vue : "Favoris" (score >= 8.0)
CREATE VIEW IF NOT EXISTS v_favorites AS
SELECT
    m.id AS manga_id,
    m.title,
    m.cover_url,
    m.site,
    rp.score,
    rp.reading_status,
    rp.notes
FROM reading_progress rp
JOIN manga m ON rp.manga_id = m.id
WHERE rp.score >= 8.0
ORDER BY rp.score DESC, m.title ASC;

-- Vue : Statistiques par manga (temps de lecture, nombre de sessions)
CREATE VIEW IF NOT EXISTS v_manga_reading_stats AS
SELECT
    m.id AS manga_id,
    m.title,
    COUNT(rs.id) AS session_count,
    SUM(rs.pages_read) AS total_pages_read,
    SUM(rs.duration_seconds) AS total_time_seconds,
    AVG(rs.duration_seconds) AS avg_session_duration,
    MIN(rs.started_at) AS first_read_at,
    MAX(rs.ended_at) AS last_read_at
FROM manga m
LEFT JOIN reading_session rs ON m.id = rs.manga_id
GROUP BY m.id
HAVING session_count > 0;

-- Vue : Listes de lecture avec compteur de mangas
CREATE VIEW IF NOT EXISTS v_reading_lists_with_count AS
SELECT
    rl.id,
    rl.name,
    rl.description,
    rl.icon,
    rl.is_default,
    rl.is_public,
    rl.sort_order,
    rl.created_at,
    rl.updated_at,
    COUNT(rlm.manga_id) AS manga_count
FROM reading_list rl
LEFT JOIN reading_list_manga rlm ON rl.id = rlm.list_id
GROUP BY rl.id
ORDER BY rl.sort_order, rl.name;

-- Vue : Activité de lecture récente (dernières 50 sessions)
CREATE VIEW IF NOT EXISTS v_recent_activity AS
SELECT
    rs.id AS session_id,
    m.id AS manga_id,
    m.title AS manga_title,
    m.cover_url,
    c.number AS chapter_number,
    c.title AS chapter_title,
    rs.pages_read,
    rs.duration_seconds,
    rs.started_at,
    rs.ended_at
FROM reading_session rs
JOIN manga m ON rs.manga_id = m.id
JOIN chapter c ON rs.chapter_id = c.id
ORDER BY rs.started_at DESC
LIMIT 50;


-- ----------------------------------------------------------------------------
-- 7. DONNÉES INITIALES : Listes par défaut
-- ----------------------------------------------------------------------------
-- Crée quelques listes de lecture par défaut pour les nouveaux utilisateurs.

INSERT OR IGNORE INTO reading_list (id, name, description, icon, is_default, sort_order)
VALUES
    ('list_favorites', 'Favoris', 'Mes mangas préférés', 'heart', 1, 1),
    ('list_to_read', 'À lire', 'Liste de lectures à venir', 'bookmark', 1, 2),
    ('list_completed', 'Terminés', 'Mangas que j''ai terminés', 'check-circle', 1, 3),
    ('list_on_hold', 'En pause', 'Mangas mis en pause', 'pause', 1, 4);


-- ============================================================================
-- FIN DE LA MIGRATION 002
-- ============================================================================
