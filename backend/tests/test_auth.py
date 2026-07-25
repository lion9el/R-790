"""Tests pour POST /api/register et POST /api/login."""


def test_register_returns_token(client):
    resp = client.post("/api/register", json={"username": "alice", "password": "password123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "alice"
    assert isinstance(body["token"], str) and len(body["token"]) > 0


def test_register_duplicate_username_rejected(client):
    client.post("/api/register", json={"username": "alice", "password": "password123"})
    resp = client.post("/api/register", json={"username": "alice", "password": "autremotdepasse"})
    assert resp.status_code == 400


def test_register_username_too_short_rejected(client):
    # Contrainte du schéma : min_length=3 sur username.
    resp = client.post("/api/register", json={"username": "ab", "password": "password123"})
    assert resp.status_code == 422


def test_register_password_too_short_rejected(client):
    # Contrainte du schéma : min_length=6 sur password.
    resp = client.post("/api/register", json={"username": "alice", "password": "123"})
    assert resp.status_code == 422


def test_register_username_with_invalid_chars_rejected(client):
    # Le pattern n'autorise que [a-zA-Z0-9_].
    resp = client.post("/api/register", json={"username": "alice!", "password": "password123"})
    assert resp.status_code == 422


def test_login_with_correct_credentials(client):
    client.post("/api/register", json={"username": "bob", "password": "password123"})
    resp = client.post("/api/login", json={"username": "bob", "password": "password123"})
    assert resp.status_code == 200
    assert "token" in resp.json()


def test_login_with_wrong_password_rejected(client):
    client.post("/api/register", json={"username": "bob", "password": "password123"})
    resp = client.post("/api/login", json={"username": "bob", "password": "mauvais-mdp"})
    assert resp.status_code == 401


def test_login_with_unknown_username_rejected(client):
    resp = client.post("/api/login", json={"username": "fantome", "password": "password123"})
    assert resp.status_code == 401


def test_login_error_does_not_reveal_which_field_is_wrong(client):
    """Le message d'erreur doit être identique pour 'user inconnu' et 'mauvais
    mot de passe', afin de ne pas aider à deviner les comptes existants."""
    client.post("/api/register", json={"username": "bob", "password": "password123"})
    wrong_password = client.post("/api/login", json={"username": "bob", "password": "faux"})
    unknown_user = client.post("/api/login", json={"username": "personne", "password": "faux"})
    assert wrong_password.json()["detail"] == unknown_user.json()["detail"]


def test_protected_route_without_token_rejected(client):
    resp = client.get("/api/feed")
    assert resp.status_code == 401


def test_protected_route_with_garbage_token_rejected(client):
    resp = client.get("/api/feed", headers={"Authorization": "Bearer un-jeton-invalide"})
    assert resp.status_code == 401
