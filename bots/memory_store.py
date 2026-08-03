"""Mémoire SQLite complète des bots + monde + relations + règles apprises."""
from __future__ import annotations
import json, sqlite3, datetime
from pathlib import Path
from contextlib import contextmanager
from typing import Optional

DB_PATH = Path(__file__).resolve().parent / "memory" / "memory.db"
WORLD_DIR = Path(__file__).resolve().parent / "world"
WORLD_DIR.mkdir(parents=True, exist_ok=True)
(WORLD_DIR / ".gitkeep").write_text("")

def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
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

def _now():
    return datetime.datetime.utcnow().isoformat() + "Z"

def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot TEXT NOT NULL, type TEXT NOT NULL, content TEXT NOT NULL,
            meta TEXT, importance INTEGER DEFAULT 5, ts TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_bot_ts ON events(bot, ts);

        CREATE TABLE IF NOT EXISTS thoughts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot TEXT NOT NULL, content TEXT NOT NULL,
            mood TEXT, next_goal TEXT, ts TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot TEXT NOT NULL, content TEXT NOT NULL,
            importance INTEGER DEFAULT 5, source TEXT, ts TEXT NOT NULL,
            UNIQUE(bot, content)
        );

        CREATE TABLE IF NOT EXISTS world_objects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, description TEXT NOT NULL,
            created_by TEXT NOT NULL, location TEXT DEFAULT 'void',
            meta TEXT, ts TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS relationships (
            bot_a TEXT NOT NULL, bot_b TEXT NOT NULL,
            trust REAL DEFAULT 0.5, summary TEXT DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (bot_a, bot_b)
        );

        CREATE TABLE IF NOT EXISTS learned_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot TEXT NOT NULL, rule TEXT NOT NULL, ts TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot TEXT, model TEXT, prompt_tokens INTEGER, completion_tokens INTEGER,
            total_tokens INTEGER, ts TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS successful_examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot TEXT NOT NULL, context TEXT, response TEXT, score INTEGER DEFAULT 5,
            ts TEXT NOT NULL
        );
        """)

def log_event(bot, type_, content, *, importance=5, meta=None):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO events (bot, type, content, meta, importance, ts) VALUES (?,?,?,?,?,?)",
            (bot, type_, content, json.dumps(meta or {}), max(0, min(10, importance)), _now()),
        )

def log_thought(bot, content, mood="", next_goal=""):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO thoughts (bot, content, mood, next_goal, ts) VALUES (?,?,?,?,?)",
            (bot, content, mood, next_goal, _now()),
        )

def add_fact(bot, content, importance=5, source="self"):
    content = (content or "").strip()
    if not content:
        return
    with get_db() as conn:
        conn.execute(
            """INSERT INTO facts (bot, content, importance, source, ts) VALUES (?,?,?,?,?)
               ON CONFLICT(bot, content) DO UPDATE SET
                 importance=MAX(importance,excluded.importance), ts=excluded.ts""",
            (bot, content, max(0, min(10, importance)), source, _now()),
        )

def recent_events(bot, limit=12):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT type, content, importance, ts FROM events WHERE bot=? ORDER BY id DESC LIMIT ?",
            (bot, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]

def recent_thoughts(bot, limit=5):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT content, mood, next_goal, ts FROM thoughts WHERE bot=? ORDER BY id DESC LIMIT ?",
            (bot, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]

def recent_actions(bot, limit=5):
    """Dernières actions décidées (type, content) pour détecter la répétition."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT type, content, ts FROM events WHERE bot=? AND type != 'wait' ORDER BY id DESC LIMIT ?",
            (bot, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]

def is_repetitive(bot, text, kind="thought", threshold=0.72, lookback=3):
    """Compare `text` aux contenus PRÉCÉDENTS (hors doublon exact de lui-même) du même type
    (thought/action) via similarité de séquence. Retourne True si trop proche d'un contenu antérieur.
    Important : exclut toute entrée strictement identique à `text` pour éviter de comparer
    un contenu déjà loggé contre lui-même (ce qui donnerait toujours ratio=1.0)."""
    import difflib
    if not text:
        return False
    text_norm = text.strip().lower()
    if kind == "thought":
        candidates = [t["content"] for t in recent_thoughts(bot, lookback + 2)]
    else:
        candidates = [a["content"] for a in recent_actions(bot, lookback + 2)]
    prior = [p for p in candidates if p and p.strip().lower() != text_norm][:lookback]
    for p in prior:
        ratio = difflib.SequenceMatcher(None, text_norm, p.lower()).ratio()
        if ratio >= threshold:
            return True
    return False

def top_facts(bot, limit=15):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT content, importance FROM facts WHERE bot=? ORDER BY importance DESC, id DESC LIMIT ?",
            (bot, limit),
        ).fetchall()
    return [dict(r) for r in rows]

def search_events(bot, query, limit=8):
    q = f"%{query.strip()}%"
    with get_db() as conn:
        rows = conn.execute(
            "SELECT type, content, importance, ts FROM events WHERE bot=? AND content LIKE ? ORDER BY importance DESC, id DESC LIMIT ?",
            (bot, q, limit),
        ).fetchall()
    return [dict(r) for r in rows]

def memory_prompt_block(bot):
    events, facts, thoughts = recent_events(bot, 10), top_facts(bot, 12), recent_thoughts(bot, 2)
    rules = learned_rules(bot, 8)
    rels = relationships_for(bot)
    parts = []
    if facts:
        parts.append("Faits :\n" + "\n".join(f"- ({f['importance']}/10) {f['content']}" for f in facts))
    if rules:
        parts.append("Règles apprises :\n" + "\n".join(f"- {r}" for r in rules))
    if events:
        parts.append("Événements récents :\n" + "\n".join(f"- [{e['type']}] {e['content'][:180]}" for e in events))
    if thoughts:
        t = thoughts[-1]
        parts.append(f"Dernière réflexion : {t['content'][:300]}")
        if t.get("mood"):
            parts.append(f"Humeur : {t['mood']}")
        if t.get("next_goal"):
            parts.append(f"Objectif : {t['next_goal']}")
    if rels:
        parts.append("Relations :\n" + "\n".join(f"- vs {r['other']}: trust={r['trust']:.2f} — {r['summary'][:100]}" for r in rels))
    return "\n\n".join(parts) if parts else "(mémoire vide)"

def create_world_object(name, description, created_by, location="void"):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO world_objects (name, description, created_by, location, ts) VALUES (?,?,?,?,?)",
            (name, description, created_by, location, _now()),
        )
        oid = int(cur.lastrowid)
    # miroir fichier sandbox
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40]
    path = WORLD_DIR / f"{oid}_{safe}.md"
    path.write_text(
        f"# {name}\n\n- location: {location}\n- created_by: {created_by}\n- ts: {_now()}\n\n{description}\n",
        encoding="utf-8",
    )
    return oid

def list_world(limit=20):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, description, created_by, location, ts FROM world_objects ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]

def update_relationship(bot_a, bot_b, delta_trust=0.0, note=""):
    with get_db() as conn:
        row = conn.execute(
            "SELECT trust, summary FROM relationships WHERE bot_a=? AND bot_b=?",
            (bot_a, bot_b),
        ).fetchone()
        if row:
            trust = max(0.0, min(1.0, float(row["trust"]) + delta_trust))
            summary = (row["summary"] or "")
            if note:
                summary = (summary + " | " + note)[-500:]
            conn.execute(
                "UPDATE relationships SET trust=?, summary=?, updated_at=? WHERE bot_a=? AND bot_b=?",
                (trust, summary, _now(), bot_a, bot_b),
            )
        else:
            trust = max(0.0, min(1.0, 0.5 + delta_trust))
            conn.execute(
                "INSERT INTO relationships (bot_a, bot_b, trust, summary, updated_at) VALUES (?,?,?,?,?)",
                (bot_a, bot_b, trust, note, _now()),
            )

def relationships_for(bot):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT bot_b as other, trust, summary FROM relationships WHERE bot_a=? ORDER BY trust DESC",
            (bot,),
        ).fetchall()
    return [dict(r) for r in rows]

def add_learned_rule(bot, rule):
    rule = (rule or "").strip()
    if not rule:
        return
    with get_db() as conn:
        conn.execute("INSERT INTO learned_rules (bot, rule, ts) VALUES (?,?,?)", (bot, rule, _now()))

def learned_rules(bot, limit=10):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT rule FROM learned_rules WHERE bot=? ORDER BY id DESC LIMIT ?",
            (bot, limit),
        ).fetchall()
    return [r["rule"] for r in rows]

def log_tokens(bot, model, prompt_tokens, completion_tokens):
    total = (prompt_tokens or 0) + (completion_tokens or 0)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO token_usage (bot, model, prompt_tokens, completion_tokens, total_tokens, ts) VALUES (?,?,?,?,?,?)",
            (bot, model, prompt_tokens or 0, completion_tokens or 0, total, _now()),
        )

def token_total(bot=None, since: str | None = None):
    """Somme des tokens utilisés. Si `since` (timestamp ISO) est fourni, ne compte
    que l'usage à partir de ce moment — permet un budget par jour/session plutôt
    que cumulatif à vie (la table token_usage n'est jamais purgée automatiquement)."""
    where = []
    params = []
    if bot:
        where.append("bot=?")
        params.append(bot)
    if since:
        where.append("ts>=?")
        params.append(since)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with get_db() as conn:
        row = conn.execute(f"SELECT COALESCE(SUM(total_tokens),0) AS s FROM token_usage {clause}", params).fetchone()
    return int(row["s"])

def token_usage_summary(bot=None, since: str | None = None):
    """Répartition par bot pour diagnostic (utilisé par view_memory.py)."""
    where = []
    params = []
    if bot:
        where.append("bot=?")
        params.append(bot)
    if since:
        where.append("ts>=?")
        params.append(since)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT bot, COALESCE(SUM(total_tokens),0) AS total, COUNT(*) AS calls "
            f"FROM token_usage {clause} GROUP BY bot ORDER BY total DESC", params
        ).fetchall()
    return [dict(r) for r in rows]

def add_example(bot, context, response, score=5):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO successful_examples (bot, context, response, score, ts) VALUES (?,?,?,?,?)",
            (bot, context[:500], response[:500], score, _now()),
        )

def few_shot_examples(bot, limit=3):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT context, response FROM successful_examples WHERE bot=? ORDER BY score DESC, id DESC LIMIT ?",
            (bot, limit),
        ).fetchall()
    return [dict(r) for r in rows]

def write_daily_journal(bot, summary):
    day = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    d = Path(__file__).resolve().parent / "memory" / "journal" / bot
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{day}.md"
    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n## {datetime.datetime.utcnow().strftime('%H:%M')} UTC\n\n{summary}\n")
    return path

def summarize_day(bot):
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    with get_db() as conn:
        rows = conn.execute(
            "SELECT type, content FROM events WHERE bot=? AND ts LIKE ? ORDER BY id",
            (bot, f"{today}%"),
        ).fetchall()
    if not rows:
        return f"Aucun événement pour {bot} aujourd'hui."
    lines = [f"- [{r['type']}] {r['content'][:200]}" for r in rows[-40:]]
    return f"Événements du {today} ({len(rows)}) :\n" + "\n".join(lines)

init_db()
