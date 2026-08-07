"""Edge cases découverts en parcours utilisateur."""
from __future__ import annotations


def test_login_case_insensitive(client, register):
    register("CaseUser", "secret12")
    r = client.post("/api/login", json={"username": "caseuser", "password": "secret12"})
    assert r.status_code == 200
    r = client.post("/api/login", json={"username": "CASEUSER", "password": "secret12"})
    assert r.status_code == 200


def test_register_duplicate_case_insensitive(client, register):
    register("DupName", "secret12")
    r = client.post("/api/register", json={"username": "dupname", "password": "secret12"})
    assert r.status_code == 400


def test_delete_post_with_bookmark(client, register):
    h1, u1 = register("author1")
    h2, u2 = register("reader1")
    pid = client.post("/api/posts", data={"content": "bookmarked then deleted"}, headers=h1).json()["id"]
    assert client.post(f"/api/posts/{pid}/bookmark", headers=h2).status_code == 200
    assert client.delete(f"/api/posts/{pid}", headers=h1).status_code == 200
    assert client.get("/api/bookmarks", headers=h2).json() == []


def test_message_case_insensitive_username(client, register):
    h1, _ = register("MsgAlice")
    h2, _ = register("MsgBob")
    r = client.post("/api/messages", json={"to": "msgalice", "content": "hello"}, headers=h2)
    assert r.status_code == 200


def test_search_excludes_blocked(client, register):
    h1, _ = register("Searcher")
    h2, _ = register("BlockedAuthor")
    pid = client.post(
        "/api/posts", data={"content": "uniqueblocktoken999"}, headers=h2
    ).json()["id"]
    assert client.post("/api/block/BlockedAuthor", headers=h1).status_code == 200
    r = client.get("/api/search", params={"q": "uniqueblocktoken999"}, headers=h1)
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_privacy_form_string_true(client, register):
    h, _ = register("PrivUser")
    r = client.patch("/api/me/profile", data={"is_private": "true"}, headers=h)
    assert r.status_code == 200
    assert r.json()["is_private"] is True
    r = client.patch("/api/me/profile", data={"is_private": "false"}, headers=h)
    assert r.json()["is_private"] is False


def test_backup_works(client, register):
    h, _ = register("BackupUser")
    r = client.post("/api/ops/backup", headers=h)
    assert r.status_code == 200
    assert r.json().get("db") is True
