"""Sauvegardes automatiques : base SQLite (API online) ou Postgres + uploads."""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tarfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT = BACKEND_DIR.parent
UPLOADS = BACKEND_DIR / "uploads"
BACKUP_ROOT = Path(os.getenv("SOCIALNET_BACKUP_DIR", str(ROOT / "backups")))
KEEP = int(os.getenv("SOCIALNET_BACKUP_KEEP", "14"))
INTERVAL = int(os.getenv("SOCIALNET_BACKUP_INTERVAL", "3600"))

_stop = threading.Event()
_lock = threading.Lock()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _db_path() -> Path:
    try:
        from database import DB_PATH as live
        return Path(live)
    except Exception:
        return BACKEND_DIR / "socialnet.db"


def _sqlite_online_backup(dest_db: Path) -> None:
    """Copie cohérente via l'API backup SQLite (évite disk I/O / corruption)."""
    dest_db.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(_db_path()), timeout=30.0)
    try:
        dst = sqlite3.connect(str(dest_db), timeout=30.0)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


def run_backup() -> dict:
    """Crée un snapshot : socialnet.db + uploads.tar.gz."""
    with _lock:
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        name = _stamp()
        dest = BACKUP_ROOT / name
        dest.mkdir(parents=True, exist_ok=True)
        result = {"id": name, "path": str(dest), "db": False, "uploads": False}

        try:
            from database import ENGINE, DATABASE_URL
        except Exception:
            ENGINE, DATABASE_URL = "sqlite", ""

        try:
            if ENGINE == "postgres" and DATABASE_URL:
                dump = dest / "socialnet.sql"
                subprocess.run(
                    ["pg_dump", DATABASE_URL, "-f", str(dump)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                result["db"] = True
            elif _db_path().exists():
                _sqlite_online_backup(dest / "socialnet.db")
                result["db"] = True
        except Exception as e:
            (dest / "db_error.txt").write_text(str(e), encoding="utf-8")

        try:
            if UPLOADS.exists() and any(UPLOADS.iterdir()):
                tar_path = dest / "uploads.tar.gz"
                with tarfile.open(tar_path, "w:gz") as tar:
                    tar.add(UPLOADS, arcname="uploads")
                result["uploads"] = True
        except Exception as e:
            (dest / "uploads_error.txt").write_text(str(e), encoding="utf-8")

        (dest / "meta.txt").write_text(
            f"created_at={datetime.now(timezone.utc).isoformat()}\n"
            f"db={result['db']}\nuploads={result['uploads']}\n",
            encoding="utf-8",
        )
        _prune()
        return result


def _prune() -> None:
    if not BACKUP_ROOT.exists():
        return
    dirs = sorted(
        [p for p in BACKUP_ROOT.iterdir() if p.is_dir()],
        key=lambda p: p.name,
        reverse=True,
    )
    for old in dirs[KEEP:]:
        shutil.rmtree(old, ignore_errors=True)


def list_backups() -> list[dict]:
    if not BACKUP_ROOT.exists():
        return []
    out = []
    for p in sorted(BACKUP_ROOT.iterdir(), reverse=True):
        if not p.is_dir():
            continue
        out.append(
            {
                "id": p.name,
                "path": str(p),
                "has_db": (p / "socialnet.db").exists() or (p / "socialnet.sql").exists(),
                "has_uploads": (p / "uploads.tar.gz").exists(),
            }
        )
    return out[:KEEP]


def start_background(interval: int | None = None) -> threading.Thread:
    sec = interval or INTERVAL

    def _loop():
        # Laisser l'API stabiliser la DB avant le 1er snapshot
        time.sleep(60)
        while not _stop.is_set():
            try:
                r = run_backup()
                print(f"[backup] snapshot {r['id']} db={r['db']} uploads={r['uploads']}")
            except Exception as e:
                print(f"[backup] erreur: {e}")
            _stop.wait(sec)

    t = threading.Thread(target=_loop, name="backup", daemon=True)
    t.start()
    return t


def stop_background() -> None:
    _stop.set()
