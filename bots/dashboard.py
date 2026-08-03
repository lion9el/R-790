#!/usr/bin/env python3
"""Dashboard HTML simple d'observabilité des bots (sans dépendance lourde)."""
from __future__ import annotations
import sqlite3, datetime
from pathlib import Path

DB = Path(__file__).resolve().parent / "memory" / "memory.db"
OUT = Path(__file__).resolve().parent / "memory" / "dashboard.html"

def q(sql, args=()):
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def main():
    if not DB.exists():
        print("Pas encore de DB. Lance runtime.py d'abord.")
        return
    events = q("SELECT bot, type, content, importance, ts FROM events ORDER BY id DESC LIMIT 80")
    thoughts = q("SELECT bot, content, mood, next_goal, ts FROM thoughts ORDER BY id DESC LIMIT 30")
    facts = q("SELECT bot, content, importance FROM facts ORDER BY importance DESC LIMIT 40")
    world = q("SELECT id, name, description, created_by, location, ts FROM world_objects ORDER BY id DESC LIMIT 40")
    rels = q("SELECT bot_a, bot_b, trust, summary FROM relationships ORDER BY trust DESC")
    rules = q("SELECT bot, rule, ts FROM learned_rules ORDER BY id DESC LIMIT 30")
    tokens = q("SELECT bot, SUM(total_tokens) as tokens FROM token_usage GROUP BY bot")

    def rows_html(items, cols):
        if not items:
            return "<p><em>vide</em></p>"
        h = "<table><tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>"
        for it in items:
            h += "<tr>" + "".join(f"<td>{str(it.get(c,''))[:200]}</td>" for c in cols) + "</tr>"
        h += "</table>"
        return h

    html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><title>SocialNet Bots Dashboard</title>
<meta http-equiv="refresh" content="30">
<style>
 body {{ font-family: ui-monospace, monospace; background:#0d0900; color:#ffb000; margin:24px; }}
 h1,h2 {{ color:#ffb000; }}
 table {{ border-collapse: collapse; width:100%; margin-bottom:24px; font-size:13px; }}
 th,td {{ border:1px solid #5c3d00; padding:6px 8px; vertical-align:top; }}
 th {{ background:#161005; }}
 .meta {{ color:#a86e00; margin-bottom:20px; }}
 section {{ margin-bottom:32px; }}
</style></head><body>
<h1>SocialNet — dashboard bots</h1>
<p class="meta">Généré {datetime.datetime.now().isoformat(timespec='seconds')} — auto-refresh 30s</p>
<section><h2>Tokens</h2>{rows_html(tokens, ['bot','tokens'])}</section>
<section><h2>Monde</h2>{rows_html(world, ['id','name','location','created_by','description'])}</section>
<section><h2>Relations</h2>{rows_html(rels, ['bot_a','bot_b','trust','summary'])}</section>
<section><h2>Faits</h2>{rows_html(facts, ['bot','importance','content'])}</section>
<section><h2>Règles apprises</h2>{rows_html(rules, ['bot','rule','ts'])}</section>
<section><h2>Pensées récentes</h2>{rows_html(thoughts, ['ts','bot','mood','next_goal','content'])}</section>
<section><h2>Événements</h2>{rows_html(events, ['ts','bot','type','importance','content'])}</section>
</body></html>"""
    OUT.write_text(html, encoding="utf-8")
    print(f"Dashboard écrit → {OUT}")
    print("Ouvre ce fichier dans un navigateur.")

if __name__ == "__main__":
    main()
