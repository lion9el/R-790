"""Sauvegardes automatiques : base SQLite + uploads."""
from __future__ import annotations

import os
import shutil
import tarfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
import subprocess

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT = BACKEND_DIR.parent
DB_PATH = BACKEND_DIR / "socialnet.db"
UPLOADS = BACKEND_DIR / "uploads"
BACKUP_ROOT = Path(os.getenv("SOCIALNET_BACKUP_DIR", str(ROOT / "backups")))
KEEP = int(os.getenv("SOCIALNET_BACKUP_KEEP", "14"))  # nombre de snapshots à conserver
INTERVAL = int(os.getenv("SOCIALNET_BACKUP_INTERVAL", "3600"))  # secondes

_stop = threading.Event()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def run_backup() -> dict:
    """Crée un snapshot : socialnet.db + uploads.tar.gz."""
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    name = _stamp()
    dest = BACKUP_ROOT / name
    dest.mkdir(parents=True, exist_ok=True)

    result = {"id": name, "path": str(dest), "db": False, "uploads": False}

    try:
        from database import ENGINE, DATABASE_URL
    except Exception:
        ENGINE, DATABASE_URL = "sqlite", ""

    if ENGINE == "postgres" and DATABASE_URL:
        dump = dest / "socialnet.sql"
        try:
            subprocess.run(
                ["pg_dump", DATABASE_URL, "-f", str(dump)],
                check=True,
                capture_output=True,
                text=True,
            )
            result["db"] = True
        except Exception as e:
            (dest / "db_error.txt").write_text(str(e), encoding="utf-8")
    elif DB_PATH.exists():
        shutil.copy2(DB_PATH, dest / "socialnet.db")
        for suffix in ("-wal", "-shm"):
            side = Path(str(DB_PATH) + suffix)
            if side.exists():
                shutil.copy2(side, dest / side.name)
        result["db"] = True

    if UPLOADS.exists() and any(UPLOADS.iterdir()):
        tar_path = dest / "uploads.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(UPLOADS, arcname="uploads")
        result["uploads"] = True

    meta = dest / "meta.txt"
    meta.write_text(
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
                "has_db": (p / "socialnet.db").exists(),
                "has_uploads": (p / "uploads.tar.gz").exists(),
            }
        )
    return out[:KEEP]


def start_background(interval: int | None = None) -> threading.Thread:
    sec = interval or INTERVAL

    def _loop():
        time.sleep(30)  # laisser l'API démarrer
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
