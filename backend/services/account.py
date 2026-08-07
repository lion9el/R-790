"""Gestion de compte : mot de passe, suppression, profil enrichi, export."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from auth import hash_password, verify_password, create_token
from database import get_conn
from services.common import now

try:
    import searchengine
except ImportError:
    searchengine = None


def change_password(user_id: int, current_password: str, new_password: str) -> None:
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Le nouveau mot de passe doit faire au moins 6 caractères.")
    with get_conn() as conn:
        row = conn.execute("SELECT password_hash, salt FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or not verify_password(current_password, row["salt"], row["password_hash"]):
            raise HTTPException(status_code=401, detail="Mot de passe actuel incorrect.")
        pw_hash, salt = hash_password(new_password)
        # Invalide toutes les sessions existantes
        iat_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        conn.execute(
            "UPDATE users SET password_hash = ?, salt = ?, tokens_valid_after = ? WHERE id = ?",
            (pw_hash, salt, iat_ms, user_id),
        )
        conn.commit()


def request_password_reset(username: str) -> str | None:
    """Crée un token de reset. Retourne le token (en prod on l'enverrait par email).

    Pour ne pas révéler si le compte existe, on retourne toujours None côté API
    publique, sauf en mode debug. Ici on renvoie le token pour le terminal demo.
    """
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (username,)).fetchone()
        if not row:
            return None
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None).isoformat()
        conn.execute(
            "INSERT INTO password_reset_tokens (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token, row["id"], expires, now()),
        )
        conn.commit()
        return token


def reset_password(token: str, new_password: str) -> None:
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Le nouveau mot de passe doit faire au moins 6 caractères.")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM password_reset_tokens WHERE token = ?", (token,)
        ).fetchone()
        if not row or row["used_at"] is not None:
            raise HTTPException(status_code=400, detail="Jeton de réinitialisation invalide ou déjà utilisé.")
        if row["expires_at"] < now():
            raise HTTPException(status_code=400, detail="Jeton de réinitialisation expiré.")
        pw_hash, salt = hash_password(new_password)
        iat_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        conn.execute(
            "UPDATE users SET password_hash = ?, salt = ?, tokens_valid_after = ? WHERE id = ?",
            (pw_hash, salt, iat_ms, row["user_id"]),
        )
        conn.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE token = ?",
            (now(), token),
        )
        conn.commit()


def delete_account(user_id: int, password: str) -> None:
    """Suppression RGPD : efface l'utilisateur et cascade les données liées."""
    with get_conn() as conn:
        row = conn.execute("SELECT password_hash, salt, avatar_path FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or not verify_password(password, row["salt"], row["password_hash"]):
            raise HTTPException(status_code=401, detail="Mot de passe incorrect.")

        # Collecter les images à supprimer du disque
        post_images = [
            r["image_path"]
            for r in conn.execute("SELECT image_path FROM posts WHERE user_id = ? AND image_path IS NOT NULL", (user_id,))
        ]
        avatar = row["avatar_path"]

        # Supprimer dans l'ordre (FK)
        conn.execute("DELETE FROM bookmarks WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM blocks WHERE blocker_id = ? OR blocked_id = ?", (user_id, user_id))
        conn.execute("DELETE FROM reports WHERE reporter_id = ?", (user_id,))
        conn.execute("DELETE FROM follow_requests WHERE requester_id = ? OR target_id = ?", (user_id, user_id))
        conn.execute("DELETE FROM password_reset_tokens WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM revoked_tokens WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM notifications WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM messages WHERE sender_id = ? OR receiver_id = ?", (user_id, user_id))
        conn.execute("DELETE FROM follows WHERE follower_id = ? OR followed_id = ?", (user_id, user_id))
        conn.execute("DELETE FROM likes WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM comments WHERE user_id = ?", (user_id,))

        # Posts de l'utilisateur + hashtags + likes/comments dessus
        post_ids = [r["id"] for r in conn.execute("SELECT id FROM posts WHERE user_id = ?", (user_id,))]
        for pid in post_ids:
            conn.execute("DELETE FROM post_hashtags WHERE post_id = ?", (pid,))
            conn.execute("DELETE FROM likes WHERE post_id = ?", (pid,))
            conn.execute("DELETE FROM comments WHERE post_id = ?", (pid,))
            conn.execute("DELETE FROM bookmarks WHERE post_id = ?", (pid,))
            conn.execute("DELETE FROM notifications WHERE post_id = ?", (pid,))
            if searchengine is not None:
                try:
                    searchengine.index_remove(pid)
                except Exception:
                    pass
        conn.execute("DELETE FROM posts WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()

    # Fichiers disque
    upload_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
    for img in post_images:
        try:
            os.remove(os.path.join(upload_dir, img))
        except FileNotFoundError:
            pass
    if avatar:
        try:
            os.remove(os.path.join(upload_dir, avatar))
        except FileNotFoundError:
            pass


def update_profile_fields(
    user_id: int,
    bio: str | None = None,
    display_name: str | None = None,
    is_private: bool | None = None,
    avatar_path: str | None = None,
) -> dict[str, Any]:
    with get_conn() as conn:
        if bio is not None:
            conn.execute("UPDATE users SET bio = ? WHERE id = ?", (bio[:200], user_id))
        if display_name is not None:
            conn.execute("UPDATE users SET display_name = ? WHERE id = ?", (display_name[:50] or None, user_id))
        if is_private is not None:
            conn.execute("UPDATE users SET is_private = ? WHERE id = ?", (1 if is_private else 0, user_id))
        if avatar_path is not None:
            conn.execute("UPDATE users SET avatar_path = ? WHERE id = ?", (avatar_path, user_id))
        conn.commit()
        row = conn.execute(
            "SELECT id, username, display_name, bio, avatar_path, is_private, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return _user_public(row)


def _user_public(row) -> dict[str, Any]:
    avatar = row["avatar_path"] if "avatar_path" in row.keys() else None
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"] if "display_name" in row.keys() else None,
        "bio": row["bio"] or "",
        "avatar_url": f"/uploads/{avatar}" if avatar else None,
        "is_private": bool(row["is_private"]) if "is_private" in row.keys() and row["is_private"] is not None else False,
        "created_at": row["created_at"],
    }


def export_user_data(user_id: int) -> dict[str, Any]:
    """Export RGPD des données de l'utilisateur."""
    with get_conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        posts = [dict(r) for r in conn.execute("SELECT id, content, created_at, image_path FROM posts WHERE user_id = ?", (user_id,))]
        comments = [dict(r) for r in conn.execute("SELECT id, post_id, content, created_at FROM comments WHERE user_id = ?", (user_id,))]
        likes = [dict(r) for r in conn.execute("SELECT post_id, created_at FROM likes WHERE user_id = ?", (user_id,))]
        follows = [dict(r) for r in conn.execute(
            "SELECT u.username, f.created_at FROM follows f JOIN users u ON u.id = f.followed_id WHERE f.follower_id = ?",
            (user_id,),
        )]
        messages_sent = [dict(r) for r in conn.execute(
            "SELECT receiver_id, content, created_at FROM messages WHERE sender_id = ?", (user_id,)
        )]
        bookmarks = [dict(r) for r in conn.execute("SELECT post_id, created_at FROM bookmarks WHERE user_id = ?", (user_id,))]

    return {
        "exported_at": now(),
        "user": {
            "username": user["username"],
            "display_name": user["display_name"] if "display_name" in user.keys() else None,
            "bio": user["bio"],
            "created_at": user["created_at"],
            "is_private": bool(user["is_private"]) if "is_private" in user.keys() and user["is_private"] else False,
        },
        "posts": posts,
        "comments": comments,
        "likes": likes,
        "following": follows,
        "messages_sent": messages_sent,
        "bookmarks": bookmarks,
    }
