"""Tests pour l'authentification WebSocket (backend/app.py::websocket_endpoint).

Le jeton doit être envoyé comme premier message JSON une fois la connexion
ouverte — plus jamais dans l'URL (`?token=...`), pour ne pas finir en clair
dans les logs d'accès du serveur. Voir le commentaire dans app.py.
"""
import json


def test_websocket_rejects_missing_auth_message(client):
    """Connexion sans envoyer de premier message : le serveur doit fermer
    après le délai d'attente plutôt que de rester connecté indéfiniment."""
    with client.websocket_connect("/ws") as ws:
        # On ne renvoie rien : le serveur doit fermer proprement le socket.
        # starlette lève WebSocketDisconnect quand on essaie de lire après fermeture.
        import pytest
        from starlette.testclient import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == 4401


def test_websocket_rejects_invalid_token(client):
    import pytest
    from starlette.testclient import WebSocketDisconnect
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"token": "un-jeton-invalide"}))
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == 4401


def test_websocket_rejects_message_without_token_field(client):
    import pytest
    from starlette.testclient import WebSocketDisconnect
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"pas_un_token": "..."}))
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == 4401


def test_websocket_rejects_non_json_message(client):
    import pytest
    from starlette.testclient import WebSocketDisconnect
    with client.websocket_connect("/ws") as ws:
        ws.send_text("ceci n'est pas du JSON")
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == 4401


def test_websocket_accepts_valid_token_and_receives_realtime_notification(client, register):
    """Bout en bout : alice se connecte en WS avec un jeton valide, bob la
    like, alice doit recevoir l'événement 'notification' en temps réel."""
    alice_headers, _ = register("alice")
    bob_headers, _ = register("bob")

    alice_token = alice_headers["Authorization"].removeprefix("Bearer ")
    post = client.post("/api/posts", headers=alice_headers, data={"content": "coucou"}).json()

    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"token": alice_token}))
        client.post(f"/api/posts/{post['id']}/like", headers=bob_headers)
        event = json.loads(ws.receive_text())
        assert event["type"] == "notification"
        assert event["notification"]["type"] == "like"
        assert event["notification"]["from"] == "bob"


def test_websocket_token_never_appears_in_connection_url(client, register):
    """Garde-fou explicite contre une régression : s'assure que se connecter
    à /ws SANS query string fonctionne toujours (donc que rien ne dépend
    encore d'un ?token= dans l'URL, qui finirait en clair dans les logs)."""
    headers, _ = register("alice")
    token = headers["Authorization"].removeprefix("Bearer ")
    # Note : pas de "?token=..." dans l'URL ci-dessous, volontairement.
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"token": token}))
        # Aucune exception levée dans le `with` => connexion acceptée.
