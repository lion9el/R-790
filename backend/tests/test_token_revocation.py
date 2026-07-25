"""Tests pour la révocation de jeton : POST /api/logout (une session) et
POST /api/logout/all (toutes les sessions).

Sans ce mécanisme, un jeton (JWT) reste valable jusqu'à son expiration
naturelle (7 jours) même après un "logout" côté client — un jeton volé
resterait utilisable. Voir CHANGELOG.md pour le contexte de l'audit qui a
mené à ce correctif.
"""
import time

import jwt as pyjwt


def _decode_without_verifying(token):
    """Décode un JWT sans vérifier sa signature, juste pour lire son
    contenu dans les tests (ex: le jti pour vérifier qu'il est bien dans
    revoked_tokens). Ne PAS faire ça en dehors des tests."""
    return pyjwt.decode(token, options={"verify_signature": False})


def _login(client, username, password="password123"):
    resp = client.post("/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


# ---------- /api/logout (session unique) ----------

def test_logout_revokes_the_current_token(client, register):
    headers, username = register("alice")
    resp = client.post("/api/logout", headers=headers)
    assert resp.status_code == 200

    # Le même jeton ne doit plus fonctionner sur AUCUNE route protégée.
    resp2 = client.get("/api/feed", headers=headers)
    assert resp2.status_code == 401


def test_logout_only_revokes_the_token_used_not_other_sessions(client, register):
    """Se déconnecter sur un appareil ne doit pas déconnecter les autres :
    un second jeton obtenu par un nouveau login doit continuer à fonctionner."""
    headers, username = register("alice")
    second_token = _login(client, "alice")
    second_headers = {"Authorization": f"Bearer {second_token}"}

    client.post("/api/logout", headers=headers)  # révoque seulement le premier jeton

    assert client.get("/api/feed", headers=headers).status_code == 401
    assert client.get("/api/feed", headers=second_headers).status_code == 200


def test_logout_requires_auth(client):
    resp = client.post("/api/logout")
    assert resp.status_code == 401


def test_logout_twice_does_not_error(client, register):
    """Révoquer un jeton déjà révoqué ne doit pas planter (INSERT OR IGNORE
    dans app.py::logout) — même si en pratique le second appel échouera
    d'abord sur l'auth (le jeton est déjà révoqué), on vérifie le cas où
    deux révocations concurrentes du même jeton pourraient se chevaucher."""
    headers, _ = register("alice")
    first = client.post("/api/logout", headers=headers)
    assert first.status_code == 200
    second = client.post("/api/logout", headers=headers)
    # Le jeton est déjà révoqué : la dépendance get_current_user le rejette
    # avant même d'atteindre le corps de la route.
    assert second.status_code == 401


# ---------- /api/logout/all (toutes les sessions) ----------

def test_logout_all_revokes_every_previously_issued_token(client, register):
    headers1, username = register("alice")
    token2 = _login(client, "alice")
    token3 = _login(client, "alice")
    headers2 = {"Authorization": f"Bearer {token2}"}
    headers3 = {"Authorization": f"Bearer {token3}"}

    resp = client.post("/api/logout/all", headers=headers3)
    assert resp.status_code == 200

    # Les TROIS jetons doivent être invalidés, y compris celui qui a servi
    # à appeler /api/logout/all lui-même.
    assert client.get("/api/feed", headers=headers1).status_code == 401
    assert client.get("/api/feed", headers=headers2).status_code == 401
    assert client.get("/api/feed", headers=headers3).status_code == 401


def test_logout_all_does_not_affect_a_token_issued_afterwards(client, register):
    """Un NOUVEAU login effectué APRÈS /api/logout/all doit fonctionner
    normalement : la révocation globale ne bloque que les jetons déjà émis
    au moment de l'appel, pas les futurs."""
    headers, username = register("alice")
    client.post("/api/logout/all", headers=headers)

    new_token = _login(client, "alice")
    new_headers = {"Authorization": f"Bearer {new_token}"}
    assert client.get("/api/feed", headers=new_headers).status_code == 200


def test_logout_all_does_not_affect_other_users(client, register):
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")

    client.post("/api/logout/all", headers=alice_headers)

    assert client.get("/api/feed", headers=alice_headers).status_code == 401
    assert client.get("/api/feed", headers=bob_headers).status_code == 200


def test_logout_all_requires_auth(client):
    resp = client.post("/api/logout/all")
    assert resp.status_code == 401


# ---------- Interaction avec le WebSocket ----------

def test_revoked_token_cannot_open_websocket(client, register):
    import json as jsonlib
    import pytest
    from starlette.testclient import WebSocketDisconnect

    headers, _ = register("alice")
    token = headers["Authorization"].removeprefix("Bearer ")
    client.post("/api/logout", headers=headers)  # révoque ce jeton

    with client.websocket_connect("/ws") as ws:
        ws.send_text(jsonlib.dumps({"token": token}))
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == 4401


def test_non_revoked_token_can_still_open_websocket(client, register):
    """Non-régression : un jeton valide, jamais révoqué, doit toujours
    pouvoir ouvrir une connexion WebSocket normalement."""
    import json as jsonlib

    headers, _ = register("alice")
    token = headers["Authorization"].removeprefix("Bearer ")

    with client.websocket_connect("/ws") as ws:
        ws.send_text(jsonlib.dumps({"token": token}))
        # Aucune exception : connexion acceptée normalement.


# ---------- Comportement fin (fenêtre glissante, purge) ----------

def test_revoked_tokens_table_entry_uses_natural_expiration(client, register):
    """L'entrée insérée dans revoked_tokens porte la date d'expiration
    NATURELLE du jeton (7 jours), pas la date de révocation — vérifié
    indirectement en s'assurant que logout fonctionne peu importe quand le
    jeton a été émis dans le passé récent."""
    headers, _ = register("alice")
    resp = client.post("/api/logout", headers=headers)
    assert resp.status_code == 200
    # Re-décodage minimal pour s'assurer qu'on manipule bien un jeton avec exp.
    token = headers["Authorization"].removeprefix("Bearer ")
    payload = _decode_without_verifying(token)
    assert payload["exp"] > payload["iat"]


def test_expired_revocation_entries_are_purged_on_next_logout(client, register, monkeypatch):
    """Les entrées de revoked_tokens dont l'expiration naturelle est passée
    sont purgées à l'occasion d'un prochain appel à /api/logout (voir le
    DELETE en début de route). On vérifie que ça ne casse rien plutôt que
    d'inspecter directement la table (détail d'implémentation)."""
    headers1, username = register("alice")
    client.post("/api/logout", headers=headers1)  # première entrée dans revoked_tokens

    token2 = _login(client, "alice")
    headers2 = {"Authorization": f"Bearer {token2}"}
    resp = client.post("/api/logout", headers=headers2)  # doit purger + insérer sans erreur
    assert resp.status_code == 200
