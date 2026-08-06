"""Accès à la base de données SQLite du réseau social.

Améliorations expert :
- Mode WAL + busy_timeout pour meilleure concurrence
- Pragmas de performance et intégrité
- Helper transactionnel
- Migrations idempotentes et plus robustes
"""
from __future__ import annotations

import sqlite3
import os
from contextlib import contextmanager
from typing import Iterator, Any

# Fichier de base de données SQLite, un seul fichier local à côté du backend.
# Volontairement hors Git (voir .gitignore) : chaque environnement (dev,
# machine de chaque contributeur, prod) a ses propres données.
DB_PATH = os.path.join(os.path.dirname(__file__), "socialnet.db")

# Schéma SQL (définition des tables), lu et exécuté par init_db() au démarrage.
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def init_db() -> None:
    """Crée les tables si elles n'existent pas encore, puis applique les
    migrations légères (colonnes ajoutées depuis la v1.0).
    Appelé une fois au démarrage du serveur.
    """
    with get_conn() as conn:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()
        _apply_migrations(conn)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Ajoute les colonnes introduites après la création initiale des tables.

    `CREATE TABLE IF NOT EXISTS` ne modifie jamais une table déjà existante.
    Chaque migration vérifie d'abord si la colonne existe déjà.
    """
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(posts)")}
    if "image_path" not in existing_columns:
        conn.execute("ALTER TABLE posts ADD COLUMN image_path TEXT")
        conn.commit()

    users_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "tokens_valid_after" not in users_columns:
        # Horodatage Unix en MILLISECONDES (entier) : comparé au claim JWT "iat_ms".
        conn.execute("ALTER TABLE users ADD COLUMN tokens_valid_after INTEGER")
        conn.commit()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Ouvre une connexion SQLite configurée pour la production légère.

    - row_factory = Row → accès par nom de colonne
    - foreign_keys = ON
    - journal_mode = WAL (meilleure concurrence lecture/écriture)
    - busy_timeout = 5000 ms (évite les "database is locked" brefs)
    - synchronous = NORMAL (bon compromis perf/sécurité avec WAL)
    - temp_store = MEMORY
    """
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA cache_size = -64000")  # ~64 Mo de cache
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Contexte transactionnel explicite.

    Usage :
        with transaction() as conn:
            conn.execute(...)
            conn.execute(...)
            # commit automatique à la sortie normale
            # rollback automatique en cas d'exception
    """
    with get_conn() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def execute(sql: str, params: tuple[Any, ...] | list[Any] = ()) -> list[sqlite3.Row]:
    """Helper pour les lectures simples (SELECT)."""
    with get_conn() as conn:
        return list(conn.execute(sql, params))


def execute_one(sql: str, params: tuple[Any, ...] | list[Any] = ()) -> sqlite3.Row | None:
    """Helper pour un seul résultat."""
    with get_conn() as conn:
        return conn.execute(sql, params).fetchone()
