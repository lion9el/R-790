"""Tests des fonctionnalités indispensables : compte, blocage, bookmarks, privé."""
from __future__ import annotations


def _reg(client, user, password="secret12"):
    r = client.post("/api/register", json={"username": user, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_change_password(client):
    tok = _reg(client, "alice_pwd")
    r = client.post(
        "/api/account/change-password",
        json={"current_password": "secret12", "new_password": "nouveau99"},
        headers=_auth(tok),
    )
    assert r.status_code == 200
    # ancien token invalidé
    r2 = client.get("/api/notifications", headers=_auth(tok))
    assert r2.status_code == 401
    # login avec nouveau mdp
    r3 = client.post("/api/login", json={"username": "alice_pwd", "password": "nouveau99"})
    assert r3.status_code == 200


def test_password_reset_flow(client):
    _reg(client, "bob_reset")
    r = client.post("/api/account/reset-request", json={"username": "bob_reset"})
    assert r.status_code == 200
    token = r.json().get("token")
    assert token
    r2 = client.post(
        "/api/account/reset-confirm",
        json={"token": token, "new_password": "resetok1"},
    )
    assert r2.status_code == 200
    r3 = client.post("/api/login", json={"username": "bob_reset", "password": "resetok1"})
    assert r3.status_code == 200


def test_block_prevents_message(client):
    t1 = _reg(client, "blocker1")
    t2 = _reg(client, "blocked1")
    r = client.post("/api/block/blocked1", headers=_auth(t1))
    assert r.status_code == 200
    r2 = client.post(
        "/api/messages",
        json={"to": "blocked1", "content": "hello"},
        headers=_auth(t1),
    )
    assert r2.status_code == 403


def test_bookmark_lifecycle(client):
    tok = _reg(client, "booker1")
    r = client.post(
        "/api/posts",
        data={"content": "post a sauver"},
        headers=_auth(tok),
    )
    assert r.status_code == 200
    post_id = r.json()["id"]
    assert client.post(f"/api/posts/{post_id}/bookmark", headers=_auth(tok)).status_code == 200
    bookmarks = client.get("/api/bookmarks", headers=_auth(tok)).json()
    assert any(p["id"] == post_id for p in bookmarks)
    assert client.delete(f"/api/posts/{post_id}/bookmark", headers=_auth(tok)).status_code == 200


def test_private_account_follow_request(client):
    owner = _reg(client, "private_owner")
    fan = _reg(client, "fan_user")
    # activer privé
    import io
    r = client.patch(
        "/api/me/profile",
        data={"is_private": "true"},
        headers=_auth(owner),
    )
    assert r.status_code == 200
    assert r.json()["is_private"] is True
    # follow devient demande
    r2 = client.post("/api/follow/private_owner", headers=_auth(fan))
    assert r2.status_code == 200
    assert r2.json()["status"] == "requested"
    # owner voit la demande
    reqs = client.get("/api/follow-requests", headers=_auth(owner)).json()
    assert any(x["username"] == "fan_user" for x in reqs)
    # accept
    assert client.post("/api/follow-requests/fan_user/accept", headers=_auth(owner)).status_code == 200


def test_report_post(client):
    tok = _reg(client, "reporter1")
    r = client.post("/api/posts", data={"content": "spam content"}, headers=_auth(tok))
    post_id = r.json()["id"]
    r2 = client.post(
        "/api/report",
        json={"target_type": "post", "target_id": post_id, "reason": "spam publicitaire"},
        headers=_auth(tok),
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "open"


def test_delete_account(client):
    tok = _reg(client, "to_delete")
    r = client.post(
        "/api/account/delete",
        json={"password": "secret12"},
        headers=_auth(tok),
    )
    assert r.status_code == 200
    r2 = client.post("/api/login", json={"username": "to_delete", "password": "secret12"})
    assert r2.status_code == 401


def test_export_data(client):
    tok = _reg(client, "exporter1")
    client.post("/api/posts", data={"content": "mon post"}, headers=_auth(tok))
    r = client.get("/api/account/export", headers=_auth(tok))
    assert r.status_code == 200
    data = r.json()
    assert data["user"]["username"] == "exporter1"
    assert len(data["posts"]) >= 1
