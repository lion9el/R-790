CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    bio TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    -- Horodatage Unix en MILLISECONDES (pas en secondes, pour éviter des
    -- collisions entre deux jetons émis pendant la même seconde) : tout
    -- jeton émis avant cette date (comparé à son claim JWT "iat_ms") est
    -- considéré révoqué ("déconnexion de tous les appareils"). NULL = pas
    -- de révocation globale en cours. Ajoutée après la v1.0 pour les bases
    -- déjà créées ; voir database.py::_apply_migrations.
    tokens_valid_after INTEGER
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    -- Nom de fichier de l'image jointe (ex: "a1b2c3.jpg"), NULL si aucune.
    -- Ajoutée après la v1.0 ; voir database.py::_apply_migrations pour les
    -- bases déjà créées avant cet ajout.
    image_path TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS likes (
    post_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (post_id, user_id),
    FOREIGN KEY (post_id) REFERENCES posts(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS follows (
    follower_id INTEGER NOT NULL,
    followed_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (follower_id, followed_id),
    FOREIGN KEY (follower_id) REFERENCES users(id),
    FOREIGN KEY (followed_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER NOT NULL,
    receiver_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    is_read INTEGER DEFAULT 0,
    FOREIGN KEY (sender_id) REFERENCES users(id),
    FOREIGN KEY (receiver_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Destinataire de la notification (celui qui la voit).
    user_id INTEGER NOT NULL,
    -- 'like' | 'follow' | 'message' | 'mention'
    type TEXT NOT NULL,
    -- Auteur de l'action qui a déclenché la notification (celui qui like,
    -- suit, envoie le message, ou mentionne). Stocké par nom d'utilisateur
    -- plutôt que par id pour rester lisible même si on veut inspecter la
    -- table à la main.
    from_username TEXT NOT NULL,
    -- Post concerné : pour 'like' et 'mention' (post où la mention a lieu,
    -- que la mention soit dans le post lui-même ou dans un commentaire dessus).
    post_id INTEGER,
    -- Court extrait du contenu, utilisé pour les notifications de type
    -- 'message' et 'mention' (aperçu du texte contenant la mention).
    content TEXT,
    created_at TEXT NOT NULL,
    is_read INTEGER DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id);

-- Association many-to-many post <-> hashtag. Une ligne par occurrence
-- unique d'un hashtag dans un post (pas de doublon si le même hashtag
-- apparaît plusieurs fois dans le même texte, voir _index_hashtags).
CREATE TABLE IF NOT EXISTS post_hashtags (
    post_id INTEGER NOT NULL,
    -- Toujours en minuscules : #Python et #python comptent comme le même tag.
    hashtag TEXT NOT NULL,
    PRIMARY KEY (post_id, hashtag),
    FOREIGN KEY (post_id) REFERENCES posts(id)
);

CREATE INDEX IF NOT EXISTS idx_post_hashtags_tag ON post_hashtags(hashtag);

-- Jetons révoqués individuellement (déconnexion d'UNE session précise, via
-- POST /api/logout — à distinguer de users.tokens_valid_after, qui révoque
-- TOUTES les sessions d'un coup). Une ligne par jeton révoqué, jusqu'à SA
-- date d'expiration naturelle : au-delà, le jeton serait de toute façon
-- refusé par la vérification normale de signature/expiration, la ligne
-- devient inutile (purgée au fil de l'eau, voir app.py::logout).
CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    -- Date d'expiration NATURELLE du jeton (pas la date de révocation) :
    -- format ISO texte, cohérent avec les autres colonnes *_at de ce schéma
    -- (contrairement à tokens_valid_after ci-dessus, comparée à "iat" et
    -- donc en entier Unix — les deux mécanismes sont indépendants).
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires ON revoked_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_posts_user ON posts(user_id);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(sender_id, receiver_id);
-- Le fil d'actualité lit les abonnements d'un utilisateur (follower_id) et
-- la sérialisation des posts lit les likes d'un utilisateur (user_id) : sans
-- ces index, SQLite parcourt toute la table (la clé primaire composite ne
-- sert que si la première colonne est filtrée).
CREATE INDEX IF NOT EXISTS idx_follows_follower ON follows(follower_id);
CREATE INDEX IF NOT EXISTS idx_likes_user ON likes(user_id);
