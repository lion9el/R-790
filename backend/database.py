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
    """Crée les tables si elles n'existent pas encore."""
    with get_conn() as conn:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
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
