"""Tests des notifications (likes, follows, messages).

Le push WebSocket n'est pas testé ici (il demanderait un client WebSocket) :
ces tests couvrent la partie persistée, c'est-à-dire ce qu'un utilisateur
retrouve via GET /api/notifications même s'il était hors ligne au moment de
l'événement.
"""


def _create_post(client, headers, content="mon post"):
    return client.post("/api/posts", data={"content": content}, headers=headers).json()["id"]


def test_like_creates_notification_for_post_author(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    post_id = _create_post(client, alice_headers)
    client.post(f"/api/posts/{post_id}/like", headers=bob_headers)

    notifs = client.get("/api/notifications", headers=alice_headers).json()
    assert len(notifs) == 1
    assert notifs[0]["type"] == "like"
    assert notifs[0]["from"] == "bob"
    assert notifs[0]["post_id"] == post_id


def test_self_like_creates_no_notification(client, register):
    headers, _ = register("alice")
    post_id = _create_post(client, headers)
    client.post(f"/api/posts/{post_id}/like", headers=headers)
    assert client.get("/api/notifications", headers=headers).json() == []


def test_liking_twice_creates_only_one_notification(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    post_id = _create_post(client, alice_headers)
    client.post(f"/api/posts/{post_id}/like", headers=bob_headers)
    client.post(f"/api/posts/{post_id}/like", headers=bob_headers)
    assert len(client.get("/api/notifications", headers=alice_headers).json()) == 1


def test_follow_creates_notification(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    client.post("/api/follow/alice", headers=bob_headers)

    notifs = client.get("/api/notifications", headers=alice_headers).json()
    assert len(notifs) == 1
    assert notifs[0]["type"] == "follow"
    assert notifs[0]["from"] == "bob"


def test_following_twice_creates_only_one_notification(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    client.post("/api/follow/alice", headers=bob_headers)
    client.post("/api/follow/alice", headers=bob_headers)
    assert len(client.get("/api/notifications", headers=alice_headers).json()) == 1


def test_message_creates_notification_with_snippet(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    client.post("/api/messages", json={"to": "alice", "content": "salut !"}, headers=bob_headers)

    notifs = client.get("/api/notifications", headers=alice_headers).json()
    assert len(notifs) == 1
    assert notifs[0]["type"] == "message"
    assert notifs[0]["content"] == "salut !"


def test_long_message_notification_is_truncated(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    client.post("/api/messages", json={"to": "alice", "content": "x" * 200}, headers=bob_headers)

    content = client.get("/api/notifications", headers=alice_headers).json()[0]["content"]
    assert content == "x" * 80 + "…"


def test_notifications_are_marked_read_after_reading(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    client.post("/api/follow/alice", headers=bob_headers)

    first_read = client.get("/api/notifications", headers=alice_headers).json()
    assert first_read[0]["is_read"] is False
    second_read = client.get("/api/notifications", headers=alice_headers).json()
    assert second_read[0]["is_read"] is True


def test_notifications_are_private_to_their_recipient(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    client.post("/api/follow/alice", headers=bob_headers)
    assert client.get("/api/notifications", headers=bob_headers).json() == []


def test_notifications_sorted_most_recent_first(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    post_id = _create_post(client, alice_headers)
    client.post("/api/follow/alice", headers=bob_headers)
    client.post(f"/api/posts/{post_id}/like", headers=bob_headers)

    types = [n["type"] for n in client.get("/api/notifications", headers=alice_headers).json()]
    assert types == ["like", "follow"]


def test_deleting_post_removes_its_notifications(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    post_id = _create_post(client, alice_headers)
    client.post(f"/api/posts/{post_id}/like", headers=bob_headers)
    client.delete(f"/api/posts/{post_id}", headers=alice_headers)
    assert client.get("/api/notifications", headers=alice_headers).json() == []


def test_notifications_require_authentication(client):
    assert client.get("/api/notifications").status_code == 401


def test_comment_creates_notification_for_post_author(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    post_id = _create_post(client, alice_headers)
    client.post(
        f"/api/posts/{post_id}/comments",
        json={"content": "super post !"},
        headers=bob_headers,
    )

    notifs = client.get("/api/notifications", headers=alice_headers).json()
    assert len(notifs) == 1
    assert notifs[0]["type"] == "comment"
    assert notifs[0]["from"] == "bob"
    assert notifs[0]["post_id"] == post_id
    assert "super post" in notifs[0]["content"]


def test_self_comment_creates_no_notification(client, register):
    headers, _ = register("alice")
    post_id = _create_post(client, headers)
    client.post(
        f"/api/posts/{post_id}/comments",
        json={"content": "commentaire sur mon propre post"},
        headers=headers,
    )
    assert client.get("/api/notifications", headers=headers).json() == []


def test_comment_notification_preview_is_truncated(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    post_id = _create_post(client, alice_headers)
    long_text = "y" * 200
    client.post(
        f"/api/posts/{post_id}/comments",
        json={"content": long_text},
        headers=bob_headers,
    )
    content = client.get("/api/notifications", headers=alice_headers).json()[0]["content"]
    assert content == "y" * 120 + "…"
