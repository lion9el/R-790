"""Accès à la base de données SQLite du réseau social."""
import sqlite3
import os
from contextlib import contextmanager

# Fichier de base de données SQLite, un seul fichier local à côté du backend.
# Volontairement hors Git (voir .gitignore) : chaque environnement (dev,
# machine de chaque contributeur, prod) a ses propres données.
DB_PATH = os.path.join(os.path.dirname(__file__), "socialnet.db")

# Schéma SQL (définition des tables), lu et exécuté par init_db() au démarrage.
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def init_db():
    """Crée les tables si elles n'existent pas encore, puis applique les
    migrations légères (colonnes ajoutées depuis la v1.0, voir
    _apply_migrations). Appelé une fois au démarrage du serveur.
    """
    with get_conn() as conn:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()
        _apply_migrations(conn)


def _apply_migrations(conn):
    """Ajoute les colonnes introduites après la création initiale des tables.

    `CREATE TABLE IF NOT EXISTS` (dans schema.sql) ne modifie jamais une
    table déjà existante : si quelqu'un a déjà une base créée avec une
    ancienne version du schéma, il faut ajouter les nouvelles colonnes à la
    main pour ne pas perdre ses données existantes. Chaque migration vérifie
    d'abord si la colonne existe déjà avant de l'ajouter, pour rester
    exécutable sans risque à chaque démarrage.
    """
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(posts)")}
    if "image_path" not in existing_columns:
        conn.execute("ALTER TABLE posts ADD COLUMN image_path TEXT")
        conn.commit()

    users_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "tokens_valid_after" not in users_columns:
        # Horodatage Unix en MILLISECONDES (entier, PAS le format ISO texte
        # utilisé par les autres colonnes *_at de ce schéma) : comparé au
        # claim JWT "iat_ms" (résolution milliseconde, pas "iat" qui est en
        # secondes — évite les collisions entre deux jetons émis pendant la
        # même seconde). NULL = aucune révocation globale en cours pour cet
        # utilisateur (comportement normal). Voir app.py::logout_all et
        # get_current_user pour son utilisation.
        conn.execute("ALTER TABLE users ADD COLUMN tokens_valid_after INTEGER")
        conn.commit()


@contextmanager
def get_conn():
    """Ouvre une connexion SQLite à utiliser avec `with get_conn() as conn:`.

    - `conn.row_factory = sqlite3.Row` permet d'accéder aux colonnes par nom
      (ex: `row["username"]`) au lieu d'index numériques.
    - Les clés étrangères (`FOREIGN KEY`) sont désactivées par défaut dans
      SQLite ; on les active explicitement pour que les contraintes du
      schéma (schema.sql) soient bien respectées.
    - La connexion est toujours fermée à la sortie du bloc `with`, même en
      cas d'exception, grâce au `try/finally`.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()
