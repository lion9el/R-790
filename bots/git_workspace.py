"""
Accès GitHub des bots au dépôt socialnet-world.
Clone local + écriture de fichiers + commit/push.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
import datetime
from pathlib import Path

BOTS_DIR = Path(__file__).resolve().parent
DEFAULT_REPO = "https://github.com/lion9el/socialnet-world.git"
# Clone local du dépôt monde (dans bots/ pour rester portable)
WORK_DIR = Path(os.getenv("BOTS_WORLD_REPO_DIR", str(BOTS_DIR / "socialnet-world-clone")))

TOKEN = os.getenv("GITHUB_TOKEN") or os.getenv("GROQ_API_KEY") and os.getenv("GITHUB_TOKEN")
# Prefer explicit GITHUB_TOKEN; fallback to known env from setup
TOKEN = os.getenv("GITHUB_TOKEN", "").strip()


def _token() -> str:
    t = os.getenv("GITHUB_TOKEN", "").strip()
    return t


def _auth_url(repo_url: str = DEFAULT_REPO) -> str:
    t = _token()
    if t and repo_url.startswith("https://"):
        return repo_url.replace("https://", f"https://{t}@")
    return repo_url


def ensure_clone(repo_url: str = DEFAULT_REPO) -> Path:
    WORK_DIR.parent.mkdir(parents=True, exist_ok=True)
    if (WORK_DIR / ".git").exists():
        _git(["pull", "--ff-only", "origin", "main"], check=False)
        return WORK_DIR
    url = _auth_url(repo_url)
    subprocess.run(
        ["git", "clone", "--depth", "1", url, str(WORK_DIR)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(["config", "user.email", "bots@socialnet.local"])
    _git(["config", "user.name", "SocialNet Bots"])
    return WORK_DIR


def _git(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(WORK_DIR),
        capture_output=True,
        text=True,
        check=check,
    )


def _safe_relpath(path: str) -> Path:
    """Empêche path traversal hors du dépôt."""
    rel = Path(path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"chemin interdit: {path}")
    # limiter aux dossiers autorisés
    allowed_roots = {"notes", "places", "objects", "journal", "README.md", "AGENTS.md"}
    if rel.parts and rel.parts[0] not in allowed_roots and rel.name not in allowed_roots:
        # autoriser sous-dossiers connus seulement
        raise ValueError(f"dossier non autorisé: {path} (notes|places|objects|journal)")
    return rel


def write_file(relpath: str, content: str, bot: str) -> str:
    ensure_clone()
    rel = _safe_relpath(relpath)
    dest = WORK_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    body = content
    if not body.endswith("\n"):
        body += "\n"
    if "signé" not in body.lower() and bot:
        body += f"\n\n---\n*{bot} — {stamp}*\n"
    dest.write_text(body, encoding="utf-8")
    return str(rel)


def commit_and_push(message: str, bot: str = "bot") -> str:
    ensure_clone()
    _git(["add", "-A"], check=False)
    status = _git(["status", "--porcelain"], check=False)
    if not (status.stdout or "").strip():
        return "rien à committer"
    msg = f"{message} (by @{bot})"
    _git(["commit", "-m", msg], check=False)
    # push with token url, avec un retry sur coupure réseau/DNS transitoire
    t = _token()
    push_args = ["push", _auth_url(), "main"] if t else ["push", "origin", "main"]
    for attempt in range(2):
        r = subprocess.run(
            ["git", *push_args],
            cwd=str(WORK_DIR),
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            return f"push ok: {msg}"
        transient = "Could not resolve host" in r.stderr or "Connection" in r.stderr or "timed out" in r.stderr
        if transient and attempt == 0:
            time.sleep(5)
            continue
        return f"push erreur: {r.stderr[:200]}"
    return f"push erreur: échec après retry"


def list_files(subdir: str = "") -> list[str]:
    ensure_clone()
    root = WORK_DIR / subdir if subdir else WORK_DIR
    if not root.exists():
        return []
    out = []
    for p in root.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            out.append(str(p.relative_to(WORK_DIR)))
    return sorted(out)[:100]


def read_file(relpath: str) -> str:
    ensure_clone()
    rel = _safe_relpath(relpath)
    path = WORK_DIR / rel
    if not path.exists():
        return f"(fichier absent: {relpath})"
    return path.read_text(encoding="utf-8")[:4000]
