"""État ops : API, DB, chronique, bots, backups — pour le dashboard unique."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from database import get_conn, DB_PATH
from services import backup as backup_service

ROOT = Path(__file__).resolve().parent.parent.parent
BOTS_DIR = ROOT / "bots"
JOURNAL = BOTS_DIR / "world" / "journal"
MEMORY_DB = BOTS_DIR / "memory" / "memory.db"


def status() -> dict:
    users = posts = messages = 0
    try:
        with get_conn() as conn:
            users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            posts = conn.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"]
            messages = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
    except Exception as e:
        db_error = str(e)
    else:
        db_error = None

    chronicle = None
    if JOURNAL.exists():
        files = sorted(
            [f for f in JOURNAL.glob("*.md") if f.name != "README.md"],
            reverse=True,
        )
        if files:
            chronicle = {
                "file": files[0].name,
                "mtime": datetime.fromtimestamp(
                    files[0].stat().st_mtime, tz=timezone.utc
                ).isoformat(),
                "preview": files[0].read_text(encoding="utf-8")[:500],
            }

    bots = {"memory_db": MEMORY_DB.exists(), "agents": []}
    if MEMORY_DB.exists():
        try:
            import sqlite3

            conn = sqlite3.connect(str(MEMORY_DB))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT bot, SUM(prompt_tokens+completion_tokens) AS tokens "
                    "FROM token_log GROUP BY bot"
                ).fetchall()
                bots["agents"] = [{"bot": r["bot"], "tokens": r["tokens"]} for r in rows]
            except Exception:
                # schéma variable selon versions memory_store
                bots["agents"] = []
            conn.close()
        except Exception:
            pass

    return {
        "ok": db_error is None,
        "time": datetime.now(timezone.utc).isoformat(),
        "database": {
            "engine": "sqlite",
            "path": str(DB_PATH),
            "exists": Path(DB_PATH).exists(),
            "error": db_error,
            "users": users,
            "posts": posts,
            "messages": messages,
        },
        "chronicle": chronicle,
        "bots": bots,
        "backups": backup_service.list_backups()[:5],
        "env": {
            "has_secret": bool(
                os.environ.get("SOCIALNET_SECRET")
                and os.environ.get("SOCIALNET_SECRET") != "change-moi-avant-la-production"
            ),
        },
    }


def status_html() -> str:
    s = status()
    db = s["database"]
    ch = s["chronicle"]
    rows_b = "".join(
        f"<tr><td>{b['id']}</td><td>{'✓' if b['has_db'] else '—'}</td>"
        f"<td>{'✓' if b['has_uploads'] else '—'}</td></tr>"
        for b in s["backups"]
    ) or "<tr><td colspan=3>(aucun)</td></tr>"
    agents = "".join(
        f"<tr><td>@{a['bot']}</td><td>{a['tokens']}</td></tr>" for a in s["bots"]["agents"]
    ) or "<tr><td colspan=2>(pas de memory.db ou vide)</td></tr>"
    ch_html = (
        f"<p><b>{ch['file']}</b> — {ch['mtime']}</p><pre>{_esc(ch['preview'])}</pre>"
        if ch
        else "<p>(pas encore de chronique)</p>"
    )
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<title>SocialNet — ops</title>
<meta http-equiv="refresh" content="15">
<style>
 body {{ font-family: ui-monospace, monospace; background:#0d0900; color:#ffb000; margin:24px; }}
 h1,h2 {{ letter-spacing:1px; }}
 .ok {{ color:#6f6; }} .bad {{ color:#f66; }}
 table {{ border-collapse:collapse; margin:12px 0 24px; }}
 th,td {{ border:1px solid #5c3d00; padding:6px 10px; }}
 th {{ background:#161005; }}
 pre {{ background:#161005; padding:12px; overflow:auto; max-height:240px;
        border:1px solid #5c3d00; white-space:pre-wrap; }}
 a {{ color:#ffcc66; }}
 .meta {{ color:#a86e00; }}
</style></head><body>
<h1>SOCIALNET // ops</h1>
<p class="meta">{s['time']} — refresh 15s —
  <a href="/">terminal</a> · <a href="/api/ops/status">JSON</a></p>
<h2>API / base</h2>
<p class="{'ok' if s['ok'] else 'bad'}">{'● OK' if s['ok'] else '● ERREUR'}</p>
<table>
<tr><th>Engine</th><td>{db['engine']}</td></tr>
<tr><th>Users</th><td>{db['users']}</td></tr>
<tr><th>Posts</th><td>{db['posts']}</td></tr>
<tr><th>Messages</th><td>{db['messages']}</td></tr>
<tr><th>SECRET configuré</th><td>{"oui" if s['env']['has_secret'] else "non (dev)"}</td></tr>
</table>
<h2>Chronique (monde)</h2>
{ch_html}
<h2>Bots (tokens)</h2>
<table><tr><th>Agent</th><th>Tokens</th></tr>{agents}</table>
<h2>Backups récents</h2>
<table><tr><th>ID</th><th>DB</th><th>Uploads</th></tr>{rows_b}</table>
<p class="meta">Snapshots dans <code>backups/</code> — conserver {backup_service.KEEP} max.</p>
</body></html>"""


def _esc(t: str) -> str:
    return (
        t.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
