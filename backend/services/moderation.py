"""Blocage, signalements, bookmarks, comptes privés."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from database import get_conn
from services.common import now, create_notification


def is_blocked(conn, user_a: int, user_b: int) -> bool:
    """True si A a bloqué B ou B a bloqué A."""
    row = conn.execute(
        "SELECT 1 FROM blocks WHERE (blocker_id = ? AND blocked_id = ?) OR (blocker_id = ? AND blocked_id = ?)",
        (user_a, user_b, user_b, user_a),
    ).fetchone()
    return row is not None


def block_user(blocker_id: int, username: str) -> None:
    with get_conn() as conn:
        target = conn.execute("SELECT id FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
        if target["id"] == blocker_id:
            raise HTTPException(status_code=400, detail="Tu ne peux pas te bloquer toi-même.")
        conn.execute(
            "INSERT OR IGNORE INTO blocks (blocker_id, blocked_id, created_at) VALUES (?, ?, ?)",
            (blocker_id, target["id"], now()),
        )
        # Couper le follow dans les deux sens
        conn.execute(
            "DELETE FROM follows WHERE (follower_id = ? AND followed_id = ?) OR (follower_id = ? AND followed_id = ?)",
            (blocker_id, target["id"], target["id"], blocker_id),
        )
        conn.execute(
            "DELETE FROM follow_requests WHERE (requester_id = ? AND target_id = ?) OR (requester_id = ? AND target_id = ?)",
            (blocker_id, target["id"], target["id"], blocker_id),
        )
        conn.commit()


def unblock_user(blocker_id: int, username: str) -> None:
    with get_conn() as conn:
        target = conn.execute("SELECT id FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
        conn.execute(
            "DELETE FROM blocks WHERE blocker_id = ? AND blocked_id = ?",
            (blocker_id, target["id"]),
        )
        conn.commit()


def list_blocked(user_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT u.username, b.created_at FROM blocks b JOIN users u ON u.id = b.blocked_id "
            "WHERE b.blocker_id = ? ORDER BY b.created_at DESC",
            (user_id,),
        ).fetchall()
        return [{"username": r["username"], "blocked_at": r["created_at"]} for r in rows]


def report_content(reporter_id: int, target_type: str, target_id: int, reason: str) -> dict[str, Any]:
    if target_type not in ("user", "post", "comment"):
        raise HTTPException(status_code=400, detail="Type de signalement invalide (user|post|comment).")
    reason = (reason or "").strip()
    if len(reason) < 3 or len(reason) > 500:
        raise HTTPException(status_code=400, detail="La raison doit faire entre 3 et 500 caractères.")
    with get_conn() as conn:
        # Vérifier que la cible existe
        if target_type == "user":
            exists = conn.execute("SELECT 1 FROM users WHERE id = ?", (target_id,)).fetchone()
        elif target_type == "post":
            exists = conn.execute("SELECT 1 FROM posts WHERE id = ?", (target_id,)).fetchone()
        else:
            exists = conn.execute("SELECT 1 FROM comments WHERE id = ?", (target_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Cible introuvable.")
        cur = conn.execute(
            "INSERT INTO reports (reporter_id, target_type, target_id, reason, created_at, status) "
            "VALUES (?, ?, ?, ?, ?, 'open')",
            (reporter_id, target_type, target_id, reason, now()),
        )
        conn.commit()
        return {"id": cur.lastrowid, "status": "open"}


def bookmark_post(user_id: int, post_id: int) -> None:
    with get_conn() as conn:
        post = conn.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone()
        if not post:
            raise HTTPException(status_code=404, detail="Post introuvable.")
        conn.execute(
            "INSERT OR IGNORE INTO bookmarks (user_id, post_id, created_at) VALUES (?, ?, ?)",
            (user_id, post_id, now()),
        )
        conn.commit()


def unbookmark_post(user_id: int, post_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM bookmarks WHERE user_id = ? AND post_id = ?", (user_id, post_id))
        conn.commit()


def list_bookmarks(user_id: int, limit: int = 30) -> list[int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT post_id FROM bookmarks WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [r["post_id"] for r in rows]


def request_follow(requester_id: int, target_username: str) -> dict[str, Any]:
    """Follow classique ou demande si compte privé."""
    with get_conn() as conn:
        target = conn.execute(
            "SELECT id, username, is_private FROM users WHERE username = ? COLLATE NOCASE",
            (target_username,),
        ).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
        if target["id"] == requester_id:
            raise HTTPException(status_code=400, detail="Tu ne peux pas te suivre toi-même.")
        if is_blocked(conn, requester_id, target["id"]):
            raise HTTPException(status_code=403, detail="Action impossible (blocage).")

        already = conn.execute(
            "SELECT 1 FROM follows WHERE follower_id = ? AND followed_id = ?",
            (requester_id, target["id"]),
        ).fetchone()
        if already:
            return {"status": "already_following"}

        is_private = bool(target["is_private"]) if target["is_private"] is not None else False
        if is_private:
            conn.execute(
                "INSERT OR REPLACE INTO follow_requests (requester_id, target_id, created_at, status) "
                "VALUES (?, ?, ?, 'pending')",
                (requester_id, target["id"], now()),
            )
            conn.commit()
            # Notification
            create_notification(conn, target["id"], "follow_request", _username(conn, requester_id))
            return {"status": "requested"}

        conn.execute(
            "INSERT INTO follows (follower_id, followed_id, created_at) VALUES (?, ?, ?)",
            (requester_id, target["id"], now()),
        )
        conn.commit()
        create_notification(conn, target["id"], "follow", _username(conn, requester_id))
        return {"status": "following"}


def respond_follow_request(target_id: int, requester_username: str, accept: bool) -> None:
    with get_conn() as conn:
        requester = conn.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE", (requester_username,)
        ).fetchone()
        if not requester:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
        req = conn.execute(
            "SELECT * FROM follow_requests WHERE requester_id = ? AND target_id = ? AND status = 'pending'",
            (requester["id"], target_id),
        ).fetchone()
        if not req:
            raise HTTPException(status_code=404, detail="Aucune demande en attente.")
        if accept:
            conn.execute(
                "INSERT OR IGNORE INTO follows (follower_id, followed_id, created_at) VALUES (?, ?, ?)",
                (requester["id"], target_id, now()),
            )
            conn.execute(
                "UPDATE follow_requests SET status = 'accepted' WHERE requester_id = ? AND target_id = ?",
                (requester["id"], target_id),
            )
            create_notification(conn, requester["id"], "follow_accepted", _username(conn, target_id))
        else:
            conn.execute(
                "UPDATE follow_requests SET status = 'rejected' WHERE requester_id = ? AND target_id = ?",
                (requester["id"], target_id),
            )
        conn.commit()


def list_follow_requests(user_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT u.username, fr.created_at FROM follow_requests fr "
            "JOIN users u ON u.id = fr.requester_id "
            "WHERE fr.target_id = ? AND fr.status = 'pending' ORDER BY fr.created_at DESC",
            (user_id,),
        ).fetchall()
        return [{"username": r["username"], "requested_at": r["created_at"]} for r in rows]


def _username(conn, user_id: int) -> str:
    row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["username"] if row else "?"
