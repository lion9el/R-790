"""Tests pour les posts, likes, commentaires et le fil d'actualité paginé."""


def create_post(client, headers, text):
    resp = client.post("/api/posts", headers=headers, data={"content": text})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------- Création de posts ----------

def test_create_post(client, register):
    headers, _ = register("alice")
    post = create_post(client, headers, "Bonjour tout le monde")
    assert post["content"] == "Bonjour tout le monde"
    assert post["author"] == "alice"
    assert post["likes"] == 0
    assert post["comments"] == 0
    assert post["liked_by_me"] is False
    assert post["image_url"] is None


def test_create_post_empty_content_rejected(client, register):
    headers, _ = register("alice")
    resp = client.post("/api/posts", headers=headers, data={"content": ""})
    assert resp.status_code == 422


def test_create_post_requires_auth(client):
    resp = client.post("/api/posts", data={"content": "sans être connecté"})
    assert resp.status_code == 401


# ---------- Fil d'actualité ----------

def test_feed_shows_own_posts(client, register):
    headers, _ = register("alice")
    create_post(client, headers, "Mon premier post")
    resp = client.get("/api/feed", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["posts"]) == 1
    assert body["posts"][0]["content"] == "Mon premier post"


def test_feed_does_not_show_unrelated_users_posts(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    create_post(client, bob_headers, "Post de bob")
    resp = client.get("/api/feed", headers=alice_headers)
    assert resp.json()["posts"] == []


def test_feed_shows_followed_users_posts(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")
    create_post(client, bob_headers, "Post de bob")
    client.post("/api/follow/bob", headers=alice_headers)
    resp = client.get("/api/feed", headers=alice_headers)
    posts = resp.json()["posts"]
    assert len(posts) == 1
    assert posts[0]["author"] == "bob"


def test_feed_empty_when_no_posts(client, register):
    headers, _ = register("alice")
    resp = client.get("/api/feed", headers=headers)
    body = resp.json()
    assert body == {"posts": [], "has_more": False, "next_cursor": None}


def test_feed_pagination_covers_all_posts_without_duplicates(client, register):
    """12 posts, pages de 5 : doit couvrir exactement les 12 ids, sans
    doublon ni trou, peu importe où on coupe."""
    headers, _ = register("alice")
    created_ids = [create_post(client, headers, f"post {i}")["id"] for i in range(12)]

    seen_ids = []
    cursor = None
    for _ in range(10):  # garde-fou anti-boucle-infinie si le has_more mentait
        url = "/api/feed?limit=5" + (f"&before={cursor}" if cursor is not None else "")
        body = client.get(url, headers=headers).json()
        seen_ids.extend(p["id"] for p in body["posts"])
        if not body["has_more"]:
            assert body["next_cursor"] is None
            break
        cursor = body["next_cursor"]
    else:
        raise AssertionError("pagination du fil : trop de pages, has_more ne se termine jamais")

    assert sorted(seen_ids) == sorted(created_ids)
    assert len(seen_ids) == len(set(seen_ids)), "un post a été renvoyé deux fois"
    # Ordre du plus récent au plus ancien.
    assert seen_ids == sorted(created_ids, reverse=True)


def test_feed_pagination_page_sizes_respect_limit(client, register):
    headers, _ = register("alice")
    for i in range(7):
        create_post(client, headers, f"post {i}")
    page1 = client.get("/api/feed?limit=3", headers=headers).json()
    assert len(page1["posts"]) == 3
    assert page1["has_more"] is True

    page2 = client.get(f"/api/feed?limit=3&before={page1['next_cursor']}", headers=headers).json()
    assert len(page2["posts"]) == 3
    assert page2["has_more"] is True

    page3 = client.get(f"/api/feed?limit=3&before={page2['next_cursor']}", headers=headers).json()
    assert len(page3["posts"]) == 1
    assert page3["has_more"] is False


def test_feed_limit_is_clamped_to_100(client, register):
    headers, _ = register("alice")
    create_post(client, headers, "un seul post")
    resp = client.get("/api/feed?limit=99999", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()["posts"]) == 1  # ne plante pas, juste clampé


# ---------- Likes ----------

def test_like_post(client, register):
    headers, _ = register("alice")
    post = create_post(client, headers, "à liker")
    resp = client.post(f"/api/posts/{post['id']}/like", headers=headers)
    assert resp.status_code == 200
    feed = client.get("/api/feed", headers=headers).json()["posts"][0]
    assert feed["likes"] == 1
    assert feed["liked_by_me"] is True


def test_like_post_twice_is_idempotent(client, register):
    headers, _ = register("alice")
    post = create_post(client, headers, "à liker deux fois")
    client.post(f"/api/posts/{post['id']}/like", headers=headers)
    resp = client.post(f"/api/posts/{post['id']}/like", headers=headers)
    assert resp.status_code == 200  # ne doit pas planter (contrainte PK)
    feed = client.get("/api/feed", headers=headers).json()["posts"][0]
    assert feed["likes"] == 1  # toujours 1, pas 2


def test_unlike_post(client, register):
    headers, _ = register("alice")
    post = create_post(client, headers, "à unliker")
    client.post(f"/api/posts/{post['id']}/like", headers=headers)
    resp = client.delete(f"/api/posts/{post['id']}/like", headers=headers)
    assert resp.status_code == 200
    feed = client.get("/api/feed", headers=headers).json()["posts"][0]
    assert feed["likes"] == 0
    assert feed["liked_by_me"] is False


def test_unlike_post_never_liked_does_not_error(client, register):
    headers, _ = register("alice")
    post = create_post(client, headers, "jamais liké")
    resp = client.delete(f"/api/posts/{post['id']}/like", headers=headers)
    assert resp.status_code == 200


def test_like_nonexistent_post_returns_404(client, register):
    headers, _ = register("alice")
    resp = client.post("/api/posts/999999/like", headers=headers)
    assert resp.status_code == 404


# ---------- Commentaires ----------

def test_add_and_list_comments(client, register):
    headers, _ = register("alice")
    post = create_post(client, headers, "avec des commentaires")
    resp = client.post(f"/api/posts/{post['id']}/comments", headers=headers, json={"content": "Super post !"})
    assert resp.status_code == 200

    comments = client.get(f"/api/posts/{post['id']}/comments", headers=headers).json()
    assert len(comments) == 1
    assert comments[0]["content"] == "Super post !"
    assert comments[0]["author"] == "alice"


def test_comment_count_reflected_in_feed(client, register):
    headers, _ = register("alice")
    post = create_post(client, headers, "compteur de commentaires")
    client.post(f"/api/posts/{post['id']}/comments", headers=headers, json={"content": "un"})
    client.post(f"/api/posts/{post['id']}/comments", headers=headers, json={"content": "deux"})
    feed_post = client.get("/api/feed", headers=headers).json()["posts"][0]
    assert feed_post["comments"] == 2


def test_comment_on_nonexistent_post_returns_404(client, register):
    headers, _ = register("alice")
    resp = client.post("/api/posts/999999/comments", headers=headers, json={"content": "..."})
    assert resp.status_code == 404


def test_comment_empty_content_rejected(client, register):
    headers, _ = register("alice")
    post = create_post(client, headers, "post normal")
    resp = client.post(f"/api/posts/{post['id']}/comments", headers=headers, json={"content": ""})
    assert resp.status_code == 422
