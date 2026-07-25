"""Tests pour follow/unfollow, profils utilisateur, messagerie privée."""


# ---------- Follow ----------

def test_follow_user(client, register):
    alice_headers, _ = register("alice")
    register("bob")
    resp = client.post("/api/follow/bob", headers=alice_headers)
    assert resp.status_code == 200
    profile = client.get("/api/users/bob", headers=alice_headers).json()
    assert profile["followers"] == 1


def test_follow_yourself_rejected(client, register):
    headers, _ = register("alice")
    resp = client.post("/api/follow/alice", headers=headers)
    assert resp.status_code == 400


def test_follow_nonexistent_user_returns_404(client, register):
    headers, _ = register("alice")
    resp = client.post("/api/follow/fantome", headers=headers)
    assert resp.status_code == 404


def test_follow_twice_is_idempotent(client, register):
    alice_headers, _ = register("alice")
    register("bob")
    client.post("/api/follow/bob", headers=alice_headers)
    resp = client.post("/api/follow/bob", headers=alice_headers)
    assert resp.status_code == 200  # ne plante pas (contrainte PK)
    profile = client.get("/api/users/bob", headers=alice_headers).json()
    assert profile["followers"] == 1  # toujours 1, pas 2


def test_unfollow_user(client, register):
    alice_headers, _ = register("alice")
    register("bob")
    client.post("/api/follow/bob", headers=alice_headers)
    resp = client.delete("/api/follow/bob", headers=alice_headers)
    assert resp.status_code == 200
    profile = client.get("/api/users/bob", headers=alice_headers).json()
    assert profile["followers"] == 0


# ---------- Profils ----------

def test_profile_counts(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    client.post("/api/posts", headers=alice_headers, data={"content": "un post"})
    client.post("/api/follow/alice", headers=bob_headers)

    profile = client.get("/api/users/alice", headers=bob_headers).json()
    assert profile["posts"] == 1
    assert profile["followers"] == 1
    assert profile["following"] == 0


def test_profile_of_nonexistent_user_returns_404(client, register):
    headers, _ = register("alice")
    resp = client.get("/api/users/fantome", headers=headers)
    assert resp.status_code == 404


def test_profile_never_exposes_password(client, register):
    headers, _ = register("alice")
    profile = client.get("/api/users/alice", headers=headers).json()
    assert "password" not in profile
    assert "password_hash" not in profile
    assert "salt" not in profile


# ---------- Messagerie privée ----------

def test_send_and_read_message(client, register):
    alice_headers, _ = register("alice")
    register("bob")
    resp = client.post("/api/messages", headers=alice_headers, json={"to": "bob", "content": "Salut !"})
    assert resp.status_code == 200

    bob_headers, _ = register("bob2")  # juste pour avoir un second jeu de headers si besoin plus tard
    conv = client.get("/api/messages/bob", headers=alice_headers).json()
    assert len(conv) == 1
    assert conv[0]["content"] == "Salut !"
    assert conv[0]["from"] == "alice"


def test_message_to_nonexistent_user_returns_404(client, register):
    headers, _ = register("alice")
    resp = client.post("/api/messages", headers=headers, json={"to": "fantome", "content": "?"})
    assert resp.status_code == 404


def test_conversation_is_bidirectional(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    client.post("/api/messages", headers=alice_headers, json={"to": "bob", "content": "de alice"})
    client.post("/api/messages", headers=bob_headers, json={"to": "alice", "content": "de bob"})

    conv_alice_side = client.get("/api/messages/bob", headers=alice_headers).json()
    conv_bob_side = client.get("/api/messages/alice", headers=bob_headers).json()
    assert len(conv_alice_side) == 2
    assert len(conv_bob_side) == 2
    # Ordre chronologique croissant.
    assert conv_alice_side[0]["content"] == "de alice"
    assert conv_alice_side[1]["content"] == "de bob"


def test_opening_conversation_marks_messages_as_read(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    client.post("/api/messages", headers=alice_headers, json={"to": "bob", "content": "salut"})

    inbox_before = client.get("/api/inbox", headers=bob_headers).json()
    assert inbox_before[0]["unread"] == 1

    client.get("/api/messages/alice", headers=bob_headers)  # ouvre la conversation

    inbox_after = client.get("/api/inbox", headers=bob_headers).json()
    assert inbox_after[0]["unread"] == 0


def test_inbox_lists_conversations_most_recent_first(client, register):
    alice_headers, _ = register("alice")
    register("bob")
    register("carol")
    client.post("/api/messages", headers=alice_headers, json={"to": "bob", "content": "premier"})
    client.post("/api/messages", headers=alice_headers, json={"to": "carol", "content": "second"})

    inbox = client.get("/api/inbox", headers=alice_headers).json()
    assert [c["with"] for c in inbox] == ["carol", "bob"]


def test_inbox_empty_when_no_messages(client, register):
    headers, _ = register("alice")
    resp = client.get("/api/inbox", headers=headers)
    assert resp.json() == []
