-- ============================================================================
-- NEXUSDL — Migration : 001_initial.sql
-- ============================================================================
--
-- Rôle :
--   Initialisation du schéma de la base de données SQLite de la bibliothèque
--   locale. Ce script crée les tables pour les mangas, chapitres, progrès
--   de lecture, historique de téléchargement, et la table virtuelle FTS5
--   pour la recherche plein texte ultra-rapide.
--
-- Règles d'or :
--   1. Les clés primaires sont des TEXT (UUID ou hash SHA256) pour garantir
--      l'unicité globale et éviter les collisions lors de synchronisations.
--   2. Les relations utilisent ON DELETE CASCADE ou SET NULL de manière
--      explicite pour maintenir l'intégrité référentielle.
--   3. Le numéro de chapitre est un TEXT pour supporter les formats
--      non-entiers ("12.5", "Extra", "v2", "Omake").
--   4. La table `page` n'est PAS stockée en BDD pour éviter le bloat
--      (ex: 1000 chapitres * 20 pages = 20 000 lignes/manga). La déduplication
--      au niveau page est gérée par `core/downloader/deduplication.py` (SQLite
--      séparé avec hash SHA256), tandis que la BDD principale suit l'état
--      de téléchargement au niveau du chapitre.
--   5. FTS5 est maintenu synchronisé via des triggers (INSERT/UPDATE/DELETE)
--      car la clé primaire de `manga` est un TEXT (FTS5 requiert un INTEGER
--      pour l'intégration automatique via content_rowid).
--
-- Intégration :
--   - Exécuté automatiquement par `core/library/database.py` via `aiosqlite`
--     au premier démarrage ou lors de la détection d'une nouvelle installation.
--   - Les pragmas (foreign_keys=ON, journal_mode=WAL) sont définis dans
--     le code Python (`database.py`), pas ici, pour garantir leur application
--     à chaque connexion.
--
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. TABLE : manga
-- ----------------------------------------------------------------------------
-- Stocke les métadonnées principales des œuvres suivies ou téléchargées.
CREATE TABLE IF NOT EXISTS manga (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    site TEXT NOT NULL,
    title TEXT NOT NULL,
    alternative_titles TEXT, -- Stocké comme JSON array: '["Titre 2", "Titre 3"]'
    description TEXT,
    author TEXT,
    artist TEXT,
    status TEXT NOT NULL CHECK (status IN (
        'ONGOING', 'COMPLETED', 'HIATUS', 'CANCELLED',
        'LICENSED', 'DISCONTINUED', 'UPCOMING', 'UNKNOWN'
    )),
    year INTEGER,
    cover_url TEXT,
    language TEXT NOT NULL,
    content_rating TEXT NOT NULL CHECK (content_rating IN (
        'SAFE', 'SUGGESTIVE', 'EROTICA', 'PORNOGRAPHIC'
    )),
    url TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    
    -- Un manga est unique par son ID source et le site d'origine
    UNIQUE (source_id, site)
);

-- Index pour les requêtes de filtrage courantes
CREATE INDEX IF NOT EXISTS idx_manga_site ON manga(site);
CREATE INDEX IF NOT EXISTS idx_manga_status ON manga(status);
CREATE INDEX IF NOT EXISTS idx_manga_language ON manga(language);
CREATE INDEX IF NOT EXISTS idx_manga_updated_at ON manga(updated_at DESC);


-- ----------------------------------------------------------------------------
-- 2. TABLE : manga_genre
-- ----------------------------------------------------------------------------
-- Relation Many-to-Many pour les genres/tags, permettant des requêtes
-- SQL efficaces (ex: "tous les mangas 'Action' ET 'Romance'").
CREATE TABLE IF NOT EXISTS manga_genre (
    manga_id TEXT NOT NULL,
    genre TEXT NOT NULL,
    PRIMARY KEY (manga_id, genre),
    FOREIGN KEY (manga_id) REFERENCES manga(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_manga_genre_genre ON manga_genre(genre);


-- ----------------------------------------------------------------------------
-- 3. TABLE : chapter
-- ----------------------------------------------------------------------------
-- Stocke les métadonnées des chapitres et leur état de téléchargement local.
CREATE TABLE IF NOT EXISTS chapter (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    manga_id TEXT NOT NULL,
    title TEXT,
    number TEXT NOT NULL, -- TEXT pour supporter "12.5", "Extra", "v2"
    volume INTEGER,
    language TEXT NOT NULL,
    pages_count INTEGER,
    published_at TEXT,
    url TEXT NOT NULL,
    
    -- État local
    downloaded INTEGER NOT NULL DEFAULT 0 CHECK (downloaded IN (0, 1)),
    download_path TEXT, -- Chemin absolu ou relatif vers le fichier CBZ/PDF/etc.
    
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    
    FOREIGN KEY (manga_id) REFERENCES manga(id) ON DELETE CASCADE,
    UNIQUE (manga_id, source_id)
);

-- Index pour récupérer rapidement les chapitres d'un manga
CREATE INDEX IF NOT EXISTS idx_chapter_manga_id ON chapter(manga_id);
-- Index pour trouver les chapitres non téléchargés d'un manga
CREATE INDEX IF NOT EXISTS idx_chapter_manga_downloaded ON chapter(manga_id, downloaded);
-- Index pour trier les chapitres par numéro (attention: tri lexicographique, 
-- le tri logique doit être fait en Python ou via une colonne 'sort_number' si nécessaire)
CREATE INDEX IF NOT EXISTS idx_chapter_number ON chapter(number);


-- ----------------------------------------------------------------------------
-- 4. TABLE : reading_progress
-- ----------------------------------------------------------------------------
-- Suit la progression de lecture de l'utilisateur.
-- Une entrée par manga (le chapitre et la page les plus avancés).
CREATE TABLE IF NOT EXISTS reading_progress (
    manga_id TEXT PRIMARY KEY,
    chapter_id TEXT NOT NULL,
    page INTEGER NOT NULL DEFAULT 1 CHECK (page >= 0),
    completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)),
    last_read_at TEXT NOT NULL DEFAULT (datetime('now')),
    
    FOREIGN KEY (manga_id) REFERENCES manga(id) ON DELETE CASCADE,
    FOREIGN KEY (chapter_id) REFERENCES chapter(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reading_progress_last_read ON reading_progress(last_read_at DESC);


-- ----------------------------------------------------------------------------
-- 5. TABLE : download_history
-- ----------------------------------------------------------------------------
-- Journal d'audit des tentatives de téléchargement (succès ou échec).
-- Utile pour le débogage, les statistiques et la reprise sur erreur.
CREATE TABLE IF NOT EXISTS download_history (
    id TEXT PRIMARY KEY, -- UUID de la DownloadTask
    manga_id TEXT,
    chapter_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('SUCCESS', 'FAILED', 'CANCELLED')),
    bytes_downloaded INTEGER NOT NULL DEFAULT 0,
    duration_seconds REAL NOT NULL DEFAULT 0.0,
    error_message TEXT,
    completed_at TEXT NOT NULL DEFAULT (datetime('now')),
    
    FOREIGN KEY (manga_id) REFERENCES manga(id) ON DELETE SET NULL,
    FOREIGN KEY (chapter_id) REFERENCES chapter(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_download_history_completed_at ON download_history(completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_download_history_status ON download_history(status);


-- ----------------------------------------------------------------------------
-- 6. TABLE VIRTUELLE : manga_search (FTS5)
-- ----------------------------------------------------------------------------
-- Table de recherche plein texte pour des requêtes ultra-rapides sur le titre,
-- l'auteur, la description et les genres.
-- Note: FTS5 ne supporte pas les clés TEXT comme rowid pour l'intégration
-- automatique (content_rowid), nous gérons donc la synchronisation via des triggers.
CREATE VIRTUAL TABLE IF NOT EXISTS manga_search USING fts5(
    manga_id UNINDEXED,
    title,
    alternative_titles,
    author,
    artist,
    description,
    genres
);


-- ----------------------------------------------------------------------------
-- 7. TRIGGERS : Synchronisation FTS5
-- ----------------------------------------------------------------------------

-- Trigger INSERT : Ajoute une entrée dans FTS5 quand un manga est ajouté
CREATE TRIGGER IF NOT EXISTS trg_manga_ai AFTER INSERT ON manga
BEGIN
    INSERT INTO manga_search (
        manga_id, title, alternative_titles, author, artist, description, genres
    )
    VALUES (
        new.id,
        new.title,
        new.alternative_titles,
        new.author,
        new.artist,
        new.description,
        (SELECT GROUP_CONCAT(genre, ' ') FROM manga_genre WHERE manga_id = new.id)
    );
END;

-- Trigger UPDATE : Met à jour l'entrée FTS5 quand un manga est modifié
CREATE TRIGGER IF NOT EXISTS trg_manga_au AFTER UPDATE ON manga
BEGIN
    UPDATE manga_search SET
        title = new.title,
        alternative_titles = new.alternative_titles,
        author = new.author,
        artist = new.artist,
        description = new.description,
        genres = (SELECT GROUP_CONCAT(genre, ' ') FROM manga_genre WHERE manga_id = new.id)
    WHERE manga_id = old.id;
END;

-- Trigger DELETE : Supprime l'entrée FTS5 quand un manga est supprimé
CREATE TRIGGER IF NOT EXISTS trg_manga_ad AFTER DELETE ON manga
BEGIN
    DELETE FROM manga_search WHERE manga_id = old.id;
END;

-- Trigger pour mettre à jour les genres dans FTS5 quand un genre est ajouté/supprimé
CREATE TRIGGER IF NOT EXISTS trg_genre_ai AFTER INSERT ON manga_genre
BEGIN
    UPDATE manga_search SET
        genres = (SELECT GROUP_CONCAT(genre, ' ') FROM manga_genre WHERE manga_id = new.manga_id)
    WHERE manga_id = new.manga_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_genre_ad AFTER DELETE ON manga_genre
BEGIN
    UPDATE manga_search SET
        genres = (SELECT GROUP_CONCAT(genre, ' ') FROM manga_genre WHERE manga_id = old.manga_id)
    WHERE manga_id = old.manga_id;
END;


-- ----------------------------------------------------------------------------
-- 8. VUES (VIEWS) : Requêtes courantes optimisées
-- ----------------------------------------------------------------------------

-- Vue pour obtenir les statistiques rapides de la bibliothèque
CREATE VIEW IF NOT EXISTS v_library_stats AS
SELECT
    (SELECT COUNT(*) FROM manga) AS total_manga,
    (SELECT COUNT(*) FROM chapter WHERE downloaded = 1) AS total_downloaded_chapters,
    (SELECT COUNT(*) FROM reading_progress WHERE completed = 0) AS currently_reading,
    (SELECT COUNT(*) FROM reading_progress WHERE completed = 1) AS completed_reading;

-- Vue pour la liste "Continuer la lecture" (triée par dernière lecture)
CREATE VIEW IF NOT EXISTS v_continue_reading AS
SELECT
    m.id AS manga_id,
    m.title,
    m.cover_url,
    rp.chapter_id,
    c.number AS chapter_number,
    rp.page,
    rp.last_read_at
FROM reading_progress rp
JOIN manga m ON rp.manga_id = m.id
JOIN chapter c ON rp.chapter_id = c.id
WHERE rp.completed = 0
ORDER BY rp.last_read_at DESC;

-- ============================================================================
-- FIN DE LA MIGRATION 001
-- ============================================================================
