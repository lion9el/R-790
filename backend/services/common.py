"""Utilitaires métier partagés (temps, normalisation recherche, notifications)."""
from __future__ import annotations

import datetime
import re
import unicodedata
from typing import Any

HASHTAG_RE = re.compile(r"#(\w{1,50})", re.UNICODE)
MENTION_RE = re.compile(r"@(\w{1,30})", re.UNICODE)


def now() -> str:
    """Horodatage UTC ISO 8601 (sans timezone explicite pour compatibilité)."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()


def normalize_for_search(text: str) -> str:
    """NFD + suppression diacritiques + lower → matching accent-insensible."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFD", text)
    without = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return without.lower()


def extract_hashtags(text: str) -> set[str]:
    return {tag.lower() for tag in HASHTAG_RE.findall(text)}


def extract_mentions(text: str) -> set[str]:
    return set(MENTION_RE.findall(text))


def index_hashtags(conn, post_id: int, content: str) -> None:
    for tag in extract_hashtags(content):
        conn.execute(
            "INSERT OR IGNORE INTO post_hashtags (post_id, hashtag) VALUES (?, ?)",
            (post_id, tag),
        )


def deindex_hashtags(conn, post_id: int) -> None:
    conn.execute("DELETE FROM post_hashtags WHERE post_id = ?", (post_id,))


def create_notification(
    conn,
    user_id: int,
    ntype: str,
    from_username: str,
    post_id: int | None = None,
    content: str | None = None,
    *,
    do_commit: bool = True,
) -> dict[str, Any]:
    created_at = now()
    cur = conn.execute(
        "INSERT INTO notifications (user_id, type, from_username, post_id, content, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, ntype, from_username, post_id, content, created_at),
    )
    if do_commit:
        conn.commit()
    return {
        "id": cur.lastrowid,
        "type": ntype,
        "from": from_username,
        "post_id": post_id,
        "content": content,
        "created_at": created_at,
        "is_read": False,
    }


def create_mention_notifications(
    conn,
    content: str,
    post_id: int,
    actor_id: int,
    actor_username: str,
    only_usernames: set[str] | None = None,
) -> list[tuple[int, dict]]:
    snippet = content[:80] + ("…" if len(content) > 80 else "")
    usernames = only_usernames if only_usernames is not None else extract_mentions(content)
    to_push: list[tuple[int, dict]] = []
    for uname in usernames:
        target = conn.execute(
            "SELECT id, username FROM users WHERE LOWER(username) = LOWER(?)", (uname,)
        ).fetchone()
        if not target or target["id"] == actor_id:
            continue
        notification = create_notification(
            conn, target["id"], "mention", actor_username, post_id=post_id, content=snippet
        )
        to_push.append((target["id"], notification))
    return to_push


def serialize_posts(conn, rows, current_user_id: int) -> list[dict[str, Any]]:
    """Sérialise des posts en évitant le N+1 (requêtes groupées)."""
    rows = list(rows)
    if not rows:
        return []

    post_ids = [row["id"] for row in rows]
    placeholders = ",".join("?" * len(post_ids))

    like_counts = {
        r["post_id"]: r["c"]
        for r in conn.execute(
            f"SELECT post_id, COUNT(*) c FROM likes WHERE post_id IN ({placeholders}) GROUP BY post_id",
            post_ids,
        )
    }
    comment_counts = {
        r["post_id"]: r["c"]
        for r in conn.execute(
            f"SELECT post_id, COUNT(*) c FROM comments WHERE post_id IN ({placeholders}) GROUP BY post_id",
            post_ids,
        )
    }
    liked_by_me = {
        r["post_id"]
        for r in conn.execute(
            f"SELECT post_id FROM likes WHERE user_id = ? AND post_id IN ({placeholders})",
            [current_user_id, *post_ids],
        )
    }
    author_ids = list({row["user_id"] for row in rows})
    authors = {
        r["id"]: r["username"]
        for r in conn.execute(
            f"SELECT id, username FROM users WHERE id IN ({','.join('?' * len(author_ids))})",
            author_ids,
        )
    }

    serialized = []
    for row in rows:
        image_path = row["image_path"] if "image_path" in row.keys() else None
        serialized.append(
            {
                "id": row["id"],
                "author": authors[row["user_id"]],
                "content": row["content"],
                "created_at": row["created_at"],
                "likes": like_counts.get(row["id"], 0),
                "liked_by_me": row["id"] in liked_by_me,
                "comments": comment_counts.get(row["id"], 0),
                "image_url": f"/uploads/{image_path}" if image_path else None,
            }
        )
    return serialized


def serialize_post(conn, row, current_user_id: int) -> dict[str, Any]:
    return serialize_posts(conn, [row], current_user_id)[0]
