"""
Mémoire structurée des bots — SQLite local (gratuit, consultable).

Tables :
  events   — mémoire épisodique (chaque échange / action)
  thoughts — réflexion interne (non publique)
  facts    — faits long terme (importance 0-10)
"""

from __future__ import annotations

import json
import sqlite3
import datetime
from pathlib import Path
from contextlib import contextmanager
from typing import Optional

DB_PATH = Path(__file__).resolve().parent / "memory" / "memory.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def get_db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot TEXT NOT NULL,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                meta TEXT,
                importance INTEGER DEFAULT 5,
                ts TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_bot_ts ON events(bot, ts);

            CREATE TABLE IF NOT EXISTS thoughts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot TEXT NOT NULL,
                content TEXT NOT NULL,
                mood TEXT,
                next_goal TEXT,
                ts TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_thoughts_bot_ts ON thoughts(bot, ts);

            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot TEXT NOT NULL,
                content TEXT NOT NULL,
                importance INTEGER DEFAULT 5,
                source TEXT,
                ts TEXT NOT NULL,
                UNIQUE(bot, content)
            );
            CREATE INDEX IF NOT EXISTS idx_facts_bot ON facts(bot, importance);

            CREATE TABLE IF NOT EXISTS world_objects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                created_by TEXT NOT NULL,
                location TEXT DEFAULT 'void',
                meta TEXT,
                ts TEXT NOT NULL
            );
            """
        )


def _now() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def log_event(
    bot: str,
    type_: str,
    content: str,
    *,
    importance: int = 5,
    meta: Optional[dict] = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO events (bot, type, content, meta, importance, ts) VALUES (?,?,?,?,?,?)",
            (bot, type_, content, json.dumps(meta or {}), max(0, min(10, importance)), _now()),
        )


def log_thought(bot: str, content: str, mood: str = "", next_goal: str = "") -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO thoughts (bot, content, mood, next_goal, ts) VALUES (?,?,?,?,?)",
            (bot, content, mood, next_goal, _now()),
        )


def add_fact(bot: str, content: str, importance: int = 5, source: str = "self") -> None:
    content = content.strip()
    if not content:
        return
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO facts (bot, content, importance, source, ts)
            VALUES (?,?,?,?,?)
            ON CONFLICT(bot, content) DO UPDATE SET
                importance = MAX(importance, excluded.importance),
                ts = excluded.ts
            """,
            (bot, content, max(0, min(10, importance)), source, _now()),
        )


def recent_events(bot: str, limit: int = 12) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT type, content, importance, ts FROM events WHERE bot = ? ORDER BY id DESC LIMIT ?",
            (bot, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def recent_thoughts(bot: str, limit: int = 5) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT content, mood, next_goal, ts FROM thoughts WHERE bot = ? ORDER BY id DESC LIMIT ?",
            (bot, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def top_facts(bot: str, limit: int = 15) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT content, importance FROM facts WHERE bot = ? ORDER BY importance DESC, id DESC LIMIT ?",
            (bot, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def search_events(bot: str, query: str, limit: int = 8) -> list[dict]:
    """Recherche texte simple (pas de vecteurs — étape suivante possible avec Chroma)."""
    q = f"%{query.strip()}%"
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT type, content, importance, ts FROM events
            WHERE bot = ? AND content LIKE ?
            ORDER BY importance DESC, id DESC LIMIT ?
            """,
            (bot, q, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def memory_prompt_block(bot: str) -> str:
    """Bloc texte à injecter dans le prompt (événements récents + faits + dernière pensée)."""
    events = recent_events(bot, 10)
    facts = top_facts(bot, 12)
    thoughts = recent_thoughts(bot, 2)

    parts = []
    if facts:
        parts.append("Faits importants :\n" + "\n".join(f"- ({f['importance']}/10) {f['content']}" for f in facts))
    if events:
        parts.append(
            "Événements récents :\n"
            + "\n".join(f"- [{e['type']}] {e['content'][:180]}" for e in events)
        )
    if thoughts:
        t = thoughts[-1]
        parts.append(f"Dernière réflexion interne : {t['content'][:300]}")
        if t.get("mood"):
            parts.append(f"Humeur : {t['mood']}")
        if t.get("next_goal"):
            parts.append(f"Prochain objectif déclaré : {t['next_goal']}")
    return "\n\n".join(parts) if parts else "(mémoire vide)"


def create_world_object(name: str, description: str, created_by: str, location: str = "void") -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO world_objects (name, description, created_by, location, ts) VALUES (?,?,?,?,?)",
            (name, description, created_by, location, _now()),
        )
        return int(cur.lastrowid)


def list_world(limit: int = 20) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, description, created_by, location, ts FROM world_objects ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def summarize_day(bot: str) -> str:
    """Résumé textuel des événements du jour (pour journal)."""
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    with get_db() as conn:
        rows = conn.execute(
            "SELECT type, content FROM events WHERE bot = ? AND ts LIKE ? ORDER BY id",
            (bot, f"{today}%"),
        ).fetchall()
    if not rows:
        return f"Aucun événement enregistré pour {bot} aujourd'hui."
    lines = [f"- [{r['type']}] {r['content'][:200]}" for r in rows[-30:]]
    return f"Événements du {today} ({len(rows)} total) :\n" + "\n".join(lines)


# init à l'import
init_db()
