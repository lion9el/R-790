"""Accès base de données — SQLite (défaut) ou PostgreSQL.

DATABASE_URL :
  - absent / sqlite → SQLite local (socialnet.db)
  - postgresql://... → Postgres via psycopg
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

_BACKEND_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(_BACKEND_DIR, "socialnet.db")
SCHEMA_PATH = os.path.join(_BACKEND_DIR, "schema.sql")
SCHEMA_PG_PATH = os.path.join(_BACKEND_DIR, "schema.postgres.sql")

DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()


def _detect_engine() -> str:
    if not DATABASE_URL or DATABASE_URL.startswith("sqlite"):
        return "sqlite"
    if DATABASE_URL.startswith("postgres"):
        return "postgres"
    return "sqlite"


ENGINE = _detect_engine()


# Tables avec colonne SERIAL `id` (RETURNING id sûr sous Postgres)
_SERIAL_ID_TABLES = frozenset({
    "users", "posts", "comments", "messages", "notifications", "reports",
})


def adapt_sql(sql: str, original: str | None = None) -> str:
    if ENGINE != "postgres":
        return sql
    orig = original or sql
    sql = sql.replace("?", "%s")
    # INSERT OR IGNORE
    was_ignore = bool(re.search(r"INSERT\s+OR\s+IGNORE", orig, re.I))
    sql = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, flags=re.I)
    # INSERT OR REPLACE → upsert minimal
    was_replace = bool(re.search(r"INSERT\s+OR\s+REPLACE", orig, re.I))
    sql = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", sql, flags=re.I)
    sql = re.sub(r"\s+COLLATE\s+NOCASE", "", sql, flags=re.I)
    if was_ignore and "ON CONFLICT" not in sql.upper():
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    elif was_replace and "ON CONFLICT" not in sql.upper():
        # Upsert générique : nécessite une contrainte unique — DO UPDATE no-op sur xmin
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        # follow_requests : mieux en DO UPDATE status
        if re.search(r"INTO\s+follow_requests", orig, re.I):
            sql = re.sub(
                r" ON CONFLICT DO NOTHING$",
                " ON CONFLICT (requester_id, target_id) DO UPDATE SET "
                "status = EXCLUDED.status, created_at = EXCLUDED.created_at",
                sql,
            )
    return sql


class _PgRow:
    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._data.values())[key]
        return self._data[key]

    def keys(self):
        return self._data.keys()

    def __contains__(self, key):
        return key in self._data


class _PgCursor:
    def __init__(self, cur):
        self._cur = cur
        self.lastrowid = None

    def execute(self, sql: str, params: tuple | list = ()):
        original = sql
        sql = adapt_sql(sql, original)
        is_insert = bool(re.match(r"\s*INSERT\s+", original, re.I))
        table_m = re.search(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)", original, re.I)
        table = (table_m.group(1).lower() if table_m else "")
        wants_id = (
            is_insert
            and table in _SERIAL_ID_TABLES
            and "ON CONFLICT" not in sql.upper()
            and "RETURNING" not in sql.upper()
        )
        if wants_id:
            sql = sql.rstrip().rstrip(";") + " RETURNING id"
        self._cur.execute(sql, params if params else None)
        if wants_id:
            try:
                row = self._cur.fetchone()
                if row is not None:
                    self.lastrowid = row["id"] if isinstance(row, dict) else row[0]
            except Exception:
                self.lastrowid = None
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        if isinstance(row, dict):
            return _PgRow(row)
        cols = [d[0] for d in self._cur.description]
        return _PgRow(dict(zip(cols, row)))

    def fetchall(self):
        rows = self._cur.fetchall()
        out = []
        for row in rows:
            if isinstance(row, dict):
                out.append(_PgRow(row))
            else:
                cols = [d[0] for d in self._cur.description]
                out.append(_PgRow(dict(zip(cols, row))))
        return out

    def __iter__(self):
        yield from self.fetchall()


class _PgConn:
    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql: str, params: tuple | list = ()):
        cur = self._raw.cursor()
        w = _PgCursor(cur)
        w.execute(sql, params)
        # re-wrap for fetch: keep same cursor
        return w

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()


def _pg_connect():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as e:
        raise RuntimeError(
            "Postgres demandé (DATABASE_URL) mais psycopg absent. "
            "pip install 'psycopg[binary]'"
        ) from e
    return _PgConn(psycopg.connect(DATABASE_URL, row_factory=dict_row))


def init_db() -> None:
    if ENGINE == "postgres":
        path = SCHEMA_PG_PATH if os.path.isfile(SCHEMA_PG_PATH) else SCHEMA_PATH
        with open(path, "r", encoding="utf-8") as f:
            script = f.read()
        with get_conn() as conn:
            conn._raw.execute(script)
            conn.commit()
    else:
        with get_conn() as conn:
            with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
                conn.executescript(f.read())
            conn.commit()
            _apply_migrations_sqlite(conn)


def _apply_migrations_sqlite(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(posts)")}
    if "image_path" not in existing:
        conn.execute("ALTER TABLE posts ADD COLUMN image_path TEXT")
        conn.commit()
    users = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "tokens_valid_after" not in users:
        conn.execute("ALTER TABLE users ADD COLUMN tokens_valid_after INTEGER")
        conn.commit()
    if "display_name" not in users:
        conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
        conn.commit()
    if "avatar_path" not in users:
        conn.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT")
        conn.commit()
    if "is_private" not in users:
        conn.execute("ALTER TABLE users ADD COLUMN is_private INTEGER NOT NULL DEFAULT 0")
        conn.commit()


@contextmanager
def get_conn() -> Iterator[Any]:
    if ENGINE == "postgres":
        conn = _pg_connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA temp_store = MEMORY")
            conn.execute("PRAGMA cache_size = -64000")
            yield conn
        finally:
            conn.close()


@contextmanager
def transaction() -> Iterator[Any]:
    with get_conn() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def execute(sql: str, params: tuple[Any, ...] | list[Any] = ()) -> list:
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        return list(cur)


def execute_one(sql: str, params: tuple[Any, ...] | list[Any] = ()):
    with get_conn() as conn:
        return conn.execute(sql, params).fetchone()
