#!/usr/bin/env python3
"""
Chronique du réseau → mémoire longue (monde).

Lit le fil public SocialNet et écrit un journal quotidien :
  - local  : bots/world/journal/YYYY-MM-DD.md
  - distant: socialnet-world/journal/YYYY-MM-DD.md (si GITHUB_TOKEN)

Peut tourner :
  - une fois  : python bots/chronicle.py
  - en boucle : python bots/chronicle.py --loop
  - depuis runtime (thread) via run_once() / start_background()
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
import threading
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

BOTS_DIR = Path(__file__).resolve().parent
ROOT = BOTS_DIR.parent
sys.path.insert(0, str(BOTS_DIR))
load_dotenv(BOTS_DIR / ".env")
load_dotenv(ROOT / ".env")

SOCIALNET_URL = os.getenv("SOCIALNET_URL", "http://127.0.0.1:8000").rstrip("/")
CHRONICLE_USER = os.getenv("CHRONICLE_USER", "chronicle")
CHRONICLE_PASSWORD = os.getenv("CHRONICLE_PASSWORD", "bot-secret-2026")
CHRONICLE_INTERVAL = int(os.getenv("CHRONICLE_INTERVAL", "300"))  # secondes
LOCAL_WORLD = BOTS_DIR / "world"
LOCAL_JOURNAL = LOCAL_WORLD / "journal"

_stop = threading.Event()


def _today() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")


def _now_stamp() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")


def _login() -> str | None:
    """Retourne un JWT ou None si l'API est indisponible."""
    try:
        with httpx.Client(base_url=SOCIALNET_URL, timeout=15.0) as client:
            r = client.post("/api/login", json={"username": CHRONICLE_USER, "password": CHRONICLE_PASSWORD})
            if r.status_code == 200:
                return r.json()["token"]
            # créer le compte chroniqueur
            r = client.post(
                "/api/register",
                json={"username": CHRONICLE_USER, "password": CHRONICLE_PASSWORD},
            )
            if r.status_code == 200:
                return r.json()["token"]
            # username pris avec un autre mdp
            r = client.post("/api/login", json={"username": CHRONICLE_USER, "password": CHRONICLE_PASSWORD})
            if r.status_code == 200:
                return r.json()["token"]
    except Exception as e:
        print(f"[chronicle] API indisponible ({e})")
    return None


def _fetch_feed(token: str, limit: int = 40) -> list[dict]:
    with httpx.Client(base_url=SOCIALNET_URL, timeout=20.0) as client:
        r = client.get(
            "/api/feed",
            params={"limit": limit},
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code != 200:
            return []
        data = r.json()
        if isinstance(data, list):
            return data
        return data.get("posts") or data.get("items") or []


def _format_entry(posts: list[dict]) -> str:
    lines = [
        f"# Chronique du réseau — {_today()}",
        "",
        f"_Dernière mise à jour : {_now_stamp()}_",
        "",
        f"{len(posts)} post(s) récents du fil :",
        "",
    ]
    if not posts:
        lines.append("(fil vide pour l’instant)")
    else:
        for p in posts:
            author = p.get("author") or p.get("username") or "?"
            content = (p.get("content") or "").replace("\n", " ").strip()
            if len(content) > 220:
                content = content[:217] + "…"
            pid = p.get("id", "?")
            likes = p.get("likes", 0)
            comments = p.get("comments", 0)
            lines.append(f"- **#{pid}** @{author} (♥{likes} 💬{comments}) : {content}")
    lines.append("")
    lines.append("---")
    lines.append("_Généré automatiquement par `bots/chronicle.py` — pont SocialNet → monde._")
    lines.append("")
    return "\n".join(lines)


def _write_local(day: str, body: str) -> Path:
    LOCAL_JOURNAL.mkdir(parents=True, exist_ok=True)
    path = LOCAL_JOURNAL / f"{day}.md"
    path.write_text(body, encoding="utf-8")
    # index simple
    index = LOCAL_JOURNAL / "README.md"
    index.write_text(
        "# Journal du réseau\n\nChroniques quotidiennes générées depuis le fil SocialNet.\n"
        f"Dernière : `{day}.md`\n",
        encoding="utf-8",
    )
    return path


def _write_remote(day: str, body: str) -> str:
    """Écrit dans socialnet-world si token GitHub présent."""
    if not os.getenv("GITHUB_TOKEN", "").strip():
        return "skip (pas de GITHUB_TOKEN)"
    try:
        import git_workspace as ghub

        rel = f"journal/{day}.md"
        ghub.write_file(rel, body, bot="chronicle")
        return ghub.commit_and_push(f"chronicle: journal {day}", bot="chronicle")
    except Exception as e:
        return f"erreur remote: {e}"


def read_latest_chronicle(max_chars: int = 2500) -> str:
    """Texte à injecter dans le prompt des bots (local d'abord)."""
    if not LOCAL_JOURNAL.exists():
        return "(pas encore de chronique)"
    files = sorted(LOCAL_JOURNAL.glob("*.md"), reverse=True)
    files = [f for f in files if f.name != "README.md"]
    if not files:
        return "(pas encore de chronique)"
    text = files[0].read_text(encoding="utf-8")
    header = f"[Chronique réseau — {files[0].stem}]\n"
    return header + text[:max_chars]


def run_once() -> dict:
    token = _login()
    if not token:
        return {"ok": False, "error": "login failed"}
    posts = _fetch_feed(token)
    day = _today()
    body = _format_entry(posts)
    local = _write_local(day, body)
    remote = _write_remote(day, body)
    print(f"[chronicle] {len(posts)} posts → {local} | remote: {remote}")
    return {"ok": True, "posts": len(posts), "local": str(local), "remote": remote}


def start_background(interval: int | None = None) -> threading.Thread:
    """Démarre un thread daemon qui chronique périodiquement."""
    sec = interval or CHRONICLE_INTERVAL

    def _loop():
        # première passe rapide après démarrage API
        time.sleep(8)
        while not _stop.is_set():
            try:
                run_once()
            except Exception as e:
                print(f"[chronicle] erreur: {e}")
            _stop.wait(sec)

    t = threading.Thread(target=_loop, name="chronicle", daemon=True)
    t.start()
    return t


def stop_background() -> None:
    _stop.set()


def main() -> None:
    parser = argparse.ArgumentParser(description="Chronique SocialNet → monde")
    parser.add_argument("--loop", action="store_true", help="Boucle périodique")
    parser.add_argument("--interval", type=int, default=CHRONICLE_INTERVAL)
    args = parser.parse_args()
    if args.loop:
        print(f"[chronicle] boucle toutes les {args.interval}s → {SOCIALNET_URL}")
        while True:
            try:
                run_once()
            except Exception as e:
                print(f"[chronicle] erreur: {e}")
            time.sleep(args.interval)
    else:
        result = run_once()
        sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
