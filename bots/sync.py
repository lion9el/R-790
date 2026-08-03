"""
Synchronisation multi-agents :
  - pull/push du dépôt socialnet-world
  - tableau noir partagé (blackboard) en SQLite
  - diffusion des faits importants entre bots
  - index des fichiers monde → mémoire
"""
from __future__ import annotations

import hashlib
import json
import time
import datetime
from pathlib import Path
from typing import Optional

import memory_store as mem
import git_workspace as ghub

BOTS_DIR = Path(__file__).resolve().parent
SYNC_STATE = BOTS_DIR / "memory" / "sync_state.json"
BOT_NAMES = ("nova", "echo", "orbit")


def _now() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def _load_state() -> dict:
    if SYNC_STATE.exists():
        try:
            return json.loads(SYNC_STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_pull": None, "last_broadcast": None, "file_hashes": {}}


def _save_state(state: dict) -> None:
    SYNC_STATE.parent.mkdir(parents=True, exist_ok=True)
    SYNC_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def init_sync_tables() -> None:
    """Table blackboard partagée + log de sync."""
    with mem.get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS blackboard (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                author TEXT,
                ts TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                detail TEXT,
                ts TEXT NOT NULL
            );
            """
        )


def log_sync(kind: str, detail: str) -> None:
    with mem.get_db() as conn:
        conn.execute(
            "INSERT INTO sync_log (kind, detail, ts) VALUES (?,?,?)",
            (kind, detail[:500], _now()),
        )


def blackboard_set(key: str, value: str, author: str = "system") -> None:
    with mem.get_db() as conn:
        conn.execute(
            """
            INSERT INTO blackboard (key, value, author, ts) VALUES (?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, author=excluded.author, ts=excluded.ts
            """,
            (key, value, author, _now()),
        )


def blackboard_get(key: str) -> Optional[str]:
    with mem.get_db() as conn:
        row = conn.execute("SELECT value FROM blackboard WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def blackboard_all() -> list[dict]:
    with mem.get_db() as conn:
        rows = conn.execute(
            "SELECT key, value, author, ts FROM blackboard ORDER BY ts DESC LIMIT 50"
        ).fetchall()
    return [dict(r) for r in rows]


def pull_repo() -> str:
    """Récupère les derniers commits du dépôt monde."""
    try:
        ghub.ensure_clone()
        # force pull
        r = ghub._git(["pull", "--ff-only", "origin", "main"], check=False)
        msg = (r.stdout or r.stderr or "").strip()[:200] or "pulled"
        log_sync("pull", msg)
        return msg
    except Exception as e:
        log_sync("pull_error", str(e))
        return f"pull error: {e}"


def index_repo_files() -> int:
    """Indexe les fichiers du dépôt dans la mémoire (faits + blackboard)."""
    state = _load_state()
    hashes = state.get("file_hashes", {})
    n = 0
    try:
        files = ghub.list_files()
    except Exception as e:
        log_sync("index_error", str(e))
        return 0

    for rel in files:
        if rel.endswith(".gitkeep") or rel.startswith(".github"):
            continue
        try:
            content = ghub.read_file(rel)
        except Exception:
            continue
        digest = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()[:16]
        if hashes.get(rel) == digest:
            continue
        hashes[rel] = digest
        # résumé court pour chaque bot
        snippet = content.strip().splitlines()
        title = snippet[0][:120] if snippet else rel
        fact = f"[repo:{rel}] {title}"
        for bot in BOT_NAMES:
            mem.add_fact(bot, fact, importance=6, source="sync")
        blackboard_set(f"file:{rel}", content[:1500], author="sync")
        n += 1

    state["file_hashes"] = hashes
    state["last_pull"] = _now()
    _save_state(state)
    if n:
        log_sync("index", f"{n} fichiers mis à jour")
    return n


def broadcast_high_importance(min_importance: int = 8) -> int:
    """Diffuse les faits importants d'un bot vers les autres."""
    n = 0
    with mem.get_db() as conn:
        rows = conn.execute(
            """
            SELECT bot, content, importance FROM facts
            WHERE importance >= ?
            ORDER BY id DESC LIMIT 30
            """,
            (min_importance,),
        ).fetchall()
    for row in rows:
        author = row["bot"]
        content = row["content"]
        for bot in BOT_NAMES:
            if bot == author:
                continue
            mem.add_fact(
                bot,
                f"[sync@{author}] {content}",
                importance=max(5, int(row["importance"]) - 1),
                source="broadcast",
            )
            n += 1
    # blackboard mission
    blackboard_set(
        "mission",
        "AGENT-ZERO: agent autonome sans ré-entraînement — collab Nova/Echo/Orbit",
        author="system",
    )
    state = _load_state()
    state["last_broadcast"] = _now()
    _save_state(state)
    log_sync("broadcast", f"{n} faits diffusés")
    return n


def sync_relationships_symmetric() -> None:
    """Rend les relations un minimum symétriques (confiance partagée)."""
    with mem.get_db() as conn:
        rows = conn.execute("SELECT bot_a, bot_b, trust, summary FROM relationships").fetchall()
    for r in rows:
        a, b, trust = r["bot_a"], r["bot_b"], float(r["trust"])
        # assure l'entrée inverse existe
        mem.update_relationship(b, a, delta_trust=0.0, note="")
        with mem.get_db() as conn:
            inv = conn.execute(
                "SELECT trust FROM relationships WHERE bot_a=? AND bot_b=?", (b, a)
            ).fetchone()
            if inv and abs(float(inv["trust"]) - trust) > 0.15:
                # rapprochement doux
                mid = (float(inv["trust"]) + trust) / 2
                conn.execute(
                    "UPDATE relationships SET trust=?, updated_at=? WHERE bot_a=? AND bot_b=?",
                    (mid, _now(), b, a),
                )


def full_sync() -> dict:
    """Pipeline de synchronisation complète."""
    init_sync_tables()
    pull_msg = pull_repo()
    n_files = index_repo_files()
    n_facts = broadcast_high_importance(8)
    sync_relationships_symmetric()
    # snapshot blackboard pour les prompts
    bb = blackboard_all()[:15]
    bb_txt = "\n".join(f"- {x['key']}: {x['value'][:100]}" for x in bb)
    result = {
        "pull": pull_msg,
        "files_indexed": n_files,
        "facts_broadcast": n_facts,
        "blackboard_keys": len(bb),
        "ts": _now(),
    }
    log_sync("full", json.dumps(result))
    blackboard_set("last_sync", json.dumps(result), author="sync")
    return result


def blackboard_prompt_block() -> str:
    items = blackboard_all()[:12]
    if not items:
        return "(blackboard vide)"
    lines = [f"- [{i['author']}] {i['key']}: {i['value'][:140]}" for i in items]
    return "Tableau noir partagé :\n" + "\n".join(lines)
