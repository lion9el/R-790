"""Tests des hashtags (#tag) et mentions (@pseudo).

Comme pour test_notifications.py, le push WebSocket des notifications de
mention n'est pas testé ici (demanderait un client WebSocket) : ces tests
couvrent la partie persistée (table post_hashtags, table notifications).
"""


def _create_post(client, headers, content="mon post"):
    return client.post("/api/posts", data={"content": content}, headers=headers).json()["id"]


# ---------- Hashtags ----------

def test_hashtag_indexed_on_post_creation(client, register):
    headers, _ = register("alice")
    _create_post(client, headers, "J'adore le #python et le #C")

    resp = client.get("/api/hashtags/python", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()["posts"]) == 1

    resp = client.get("/api/hashtags/c", headers=headers)
    assert len(resp.json()["posts"]) == 1


def test_hashtag_lookup_is_case_insensitive(client, register):
    headers, _ = register("alice")
    _create_post(client, headers, "Vive #Python")
    assert len(client.get("/api/hashtags/python", headers=headers).json()["posts"]) == 1
    assert len(client.get("/api/hashtags/PYTHON", headers=headers).json()["posts"]) == 1


def test_hashtag_leading_dash_is_optional(client, register):
    headers, _ = register("alice")
    _create_post(client, headers, "#test")
    assert len(client.get("/api/hashtags/test", headers=headers).json()["posts"]) == 1
    assert len(client.get("/api/hashtags/%23test", headers=headers).json()["posts"]) == 1


def test_hashtag_deduplicated_within_same_post(client, register):
    """#test répété plusieurs fois dans un post ne doit compter qu'une fois
    dans le décompte trending (contrainte PRIMARY KEY (post_id, hashtag))."""
    headers, _ = register("alice")
    _create_post(client, headers, "#test et encore #test et #TEST")
    trending = client.get("/api/hashtags/trending", headers=headers).json()
    assert trending == [{"hashtag": "test", "count": 1}]


def test_post_without_hashtag_not_returned(client, register):
    headers, _ = register("alice")
    _create_post(client, headers, "aucun tag ici")
    assert client.get("/api/hashtags/python", headers=headers).json()["posts"] == []


def test_hashtag_pagination_has_more_and_cursor(client, register):
    headers, _ = register("alice")
    for i in range(3):
        _create_post(client, headers, f"post numero {i} #tag")

    page1 = client.get("/api/hashtags/tag?limit=2", headers=headers).json()
    assert len(page1["posts"]) == 2
    assert page1["has_more"] is True
    assert page1["next_cursor"] is not None

    page2 = client.get(f"/api/hashtags/tag?limit=2&before={page1['next_cursor']}", headers=headers).json()
    assert len(page2["posts"]) == 1
    assert page2["has_more"] is False


def test_trending_sorted_by_count_desc(client, register):
    headers, _ = register("alice")
    _create_post(client, headers, "#popular")
    _create_post(client, headers, "#popular #rare")
    _create_post(client, headers, "#popular")

    trending = client.get("/api/hashtags/trending", headers=headers).json()
    assert trending[0] == {"hashtag": "popular", "count": 3}
    assert {"hashtag": "rare", "count": 1} in trending


def test_editing_post_reindexes_hashtags(client, register):
    headers, _ = register("alice")
    post_id = _create_post(client, headers, "#ancien")
    client.patch(f"/api/posts/{post_id}", json={"content": "#nouveau"}, headers=headers)

    assert client.get("/api/hashtags/ancien", headers=headers).json()["posts"] == []
    assert len(client.get("/api/hashtags/nouveau", headers=headers).json()["posts"]) == 1


def test_deleting_post_removes_its_hashtags(client, register):
    headers, _ = register("alice")
    post_id = _create_post(client, headers, "#ephemere")
    client.delete(f"/api/posts/{post_id}", headers=headers)
    assert client.get("/api/hashtags/ephemere", headers=headers).json()["posts"] == []


def test_hashtag_requires_authentication(client):
    assert client.get("/api/hashtags/python").status_code == 401
    assert client.get("/api/hashtags/trending").status_code == 401


# ---------- Mentions ----------

def test_mention_in_post_creates_notification(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    post_id = _create_post(client, bob_headers, "Salut @alice, comment vas-tu ?")

    notifs = client.get("/api/notifications", headers=alice_headers).json()
    assert len(notifs) == 1
    assert notifs[0]["type"] == "mention"
    assert notifs[0]["from"] == "bob"
    assert notifs[0]["post_id"] == post_id


def test_mention_is_case_insensitive(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    _create_post(client, bob_headers, "Coucou @ALICE")
    assert len(client.get("/api/notifications", headers=alice_headers).json()) == 1


def test_self_mention_creates_no_notification(client, register):
    headers, _ = register("alice")
    _create_post(client, headers, "je me parle à moi-même @alice")
    assert client.get("/api/notifications", headers=headers).json() == []


def test_mention_of_unknown_user_creates_no_notification(client, register):
    headers, _ = register("alice")
    _create_post(client, headers, "salut @personne_qui_nexiste_pas")
    assert client.get("/api/notifications", headers=headers).json() == []


def test_mention_in_comment_creates_notification(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    post_id = _create_post(client, alice_headers)
    client.post(f"/api/posts/{post_id}/comments", json={"content": "regarde ça @alice"}, headers=bob_headers)

    notifs = client.get("/api/notifications", headers=alice_headers).json()
    assert len(notifs) == 1
    assert notifs[0]["type"] == "mention"
    assert notifs[0]["post_id"] == post_id


def test_multiple_mentions_in_one_post_notify_each_once(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    charlie_headers, _ = register("charlie")
    _create_post(client, charlie_headers, "@alice @bob venez voir ça, @alice !")

    assert len(client.get("/api/notifications", headers=alice_headers).json()) == 1
    assert len(client.get("/api/notifications", headers=bob_headers).json()) == 1


def test_editing_post_only_notifies_new_mentions(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    charlie_headers, _ = register("charlie")
    post_id = _create_post(client, charlie_headers, "salut @alice")
    # alice a déjà été notifiée une fois à la création (et le fetch ci-dessous la marque comme lue)
    first_alice_notifs = client.get("/api/notifications", headers=alice_headers).json()
    assert len(first_alice_notifs) == 1

    client.patch(f"/api/posts/{post_id}", json={"content": "salut @alice et @bob"}, headers=charlie_headers)

    # alice ne doit PAS recevoir de DEUXIÈME notification (déjà mentionnée avant
    # l'édition) : toujours une seule notification au total, déjà lue.
    alice_notifs = client.get("/api/notifications", headers=alice_headers).json()
    assert len(alice_notifs) == 1
    assert alice_notifs[0]["is_read"] is True
    # bob, nouvellement mentionné par l'édition, doit lui être notifié
    bob_notifs = client.get("/api/notifications", headers=bob_headers).json()
    assert len(bob_notifs) == 1
    assert bob_notifs[0]["content"] == "salut @alice et @bob"
