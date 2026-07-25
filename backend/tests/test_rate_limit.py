"""Tests pour le rate limiting (backend/ratelimit.py) appliqué à
/api/register et /api/login."""
import time


def test_register_allows_up_to_the_limit(client):
    # Limite : 5 inscriptions par minute par IP (voir app.py). TestClient
    # utilise toujours la même IP simulée ("testclient"), donc ces 5 appels
    # partagent bien le même compteur.
    for i in range(5):
        resp = client.post("/api/register", json={"username": f"user{i}", "password": "password123"})
        assert resp.status_code == 200, f"tentative {i} rejetée alors qu'elle est sous la limite"


def test_register_blocks_beyond_the_limit(client):
    for i in range(5):
        client.post("/api/register", json={"username": f"user{i}", "password": "password123"})
    resp = client.post("/api/register", json={"username": "user_de_trop", "password": "password123"})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_register_rate_limit_message_does_not_leak_internals(client):
    for i in range(5):
        client.post("/api/register", json={"username": f"user{i}", "password": "password123"})
    resp = client.post("/api/register", json={"username": "encore", "password": "password123"})
    assert "Trop de tentatives" in resp.json()["detail"]


def test_login_allows_up_to_the_limit(client, register):
    register("alice")  # ne compte pas dans la limite de /api/login (route différente)
    for i in range(10):
        resp = client.post("/api/login", json={"username": "alice", "password": "mauvais-mdp"})
        assert resp.status_code == 401, f"tentative {i} bloquée alors qu'elle est sous la limite"


def test_login_blocks_beyond_the_limit_even_with_correct_password(client, register):
    """Point clé : une fois la limite atteinte, même les BONS identifiants
    sont bloqués — le rate limiting s'applique avant toute vérification du
    mot de passe, sinon un attaquant pourrait distinguer 'bloqué' de 'mauvais
    mot de passe' et continuer à deviner sans jamais être ralenti sur les
    tentatives qui échouent."""
    headers, _ = register("alice")
    for i in range(10):
        client.post("/api/login", json={"username": "alice", "password": "mauvais-mdp"})
    resp = client.post("/api/login", json={"username": "alice", "password": "password123"})
    assert resp.status_code == 429


def test_register_and_login_have_independent_limits(client, register):
    """Les 5 inscriptions faites par `register(...)` d'un autre test (ou
    plus haut dans ce test) ne doivent pas entamer le quota de /api/login,
    qui a sa propre clé de rate limiting (voir ratelimit.py : la clé inclut
    request.url.path)."""
    for i in range(5):
        client.post("/api/register", json={"username": f"user{i}", "password": "password123"})
    # /api/register est maintenant à sa limite, mais /api/login doit encore
    # fonctionner normalement.
    resp = client.post("/api/login", json={"username": "user0", "password": "password123"})
    assert resp.status_code == 200


def test_rate_limit_window_expires_after_60_seconds(client, monkeypatch):
    """Vérifie que la fenêtre glissante se vide bien avec le temps, sans
    attendre 60 vraies secondes : on avance artificiellement l'horloge
    interne du rate limiter (time.monotonic) plutôt que d'utiliser
    time.sleep(60), ce qui rendrait ce test inutilement lent."""
    import ratelimit

    fake_time = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: fake_time[0])

    for i in range(5):
        client.post("/api/register", json={"username": f"user{i}", "password": "password123"})
    blocked = client.post("/api/register", json={"username": "bloque", "password": "password123"})
    assert blocked.status_code == 429

    fake_time[0] += 61  # avance le temps simulé au-delà de la fenêtre de 60s
    allowed_again = client.post("/api/register", json={"username": "apres_fenetre", "password": "password123"})
    assert allowed_again.status_code == 200
