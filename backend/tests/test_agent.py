"""Tests de l'agent IA (backend/services/agent.py + routes /api/agent/*).

Principe : on ne teste JAMAIS un vrai appel LLM ici (pas de clé Groq en CI,
pas de dépendance réseau dans la suite de tests). On teste :
1. Le contrôle d'accès (require_admin).
2. Le workflow "action proposée -> confirmée/rejetée" directement via le
   service, sans passer par le modèle de langage.
3. Que /api/agent/run échoue proprement (503) sans clé Groq configurée,
   plutôt que de planter.
"""
from __future__ import annotations

import database


def _reg(client, user, password="secret12"):
    r = client.post("/api/register", json={"username": user, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _make_admin(username: str) -> None:
    with database.get_conn() as conn:
        conn.execute("UPDATE users SET is_admin = 1 WHERE username = ?", (username,))
        conn.commit()


def test_non_admin_forbidden(client):
    tok = _reg(client, "alice_agent")
    r = client.get("/api/agent/actions", headers=_auth(tok))
    assert r.status_code == 403

    r2 = client.post("/api/agent/run", json={"instruction": "salut"}, headers=_auth(tok))
    assert r2.status_code == 403


def test_agent_run_without_groq_key_returns_503(client):
    tok = _reg(client, "bob_agent")
    _make_admin("bob_agent")
    r = client.post("/api/agent/run", json={"instruction": "liste les signalements"}, headers=_auth(tok))
    # Pas de GROQ_API_KEY dans l'environnement de test → erreur explicite, pas un crash.
    assert r.status_code == 503
    assert "GROQ_API_KEY" in r.json()["detail"]


def test_propose_and_confirm_delete_post(client):
    tok = _reg(client, "carol_agent")
    _make_admin("carol_agent")

    # Carol poste, puis on simule l'agent proposant sa suppression (sans LLM).
    r = client.post("/api/posts", data={"content": "post a supprimer"}, headers=_auth(tok))
    assert r.status_code == 200, r.text
    post_id = r.json()["id"]

    import services.agent as agent_service
    with database.get_conn() as conn:
        admin_id = conn.execute("SELECT id FROM users WHERE username = 'carol_agent'").fetchone()["id"]
    action_id = agent_service._propose_action(
        "delete_post", {"post_id": post_id}, "contenu jugé problématique", admin_id
    )

    # L'action existe et est en attente
    r2 = client.get("/api/agent/actions?status=pending", headers=_auth(tok))
    assert r2.status_code == 200
    assert any(a["id"] == action_id for a in r2.json())

    # Le post existe toujours (rien n'a été exécuté automatiquement)
    r3 = client.get("/api/feed", headers=_auth(tok))
    assert any(p["id"] == post_id for p in r3.json()["posts"])

    # Confirmation -> exécution réelle
    r4 = client.post(f"/api/agent/actions/{action_id}/confirm", headers=_auth(tok))
    assert r4.status_code == 200, r4.text
    assert r4.json()["status"] == "executed"

    r5 = client.get("/api/feed", headers=_auth(tok))
    assert not any(p["id"] == post_id for p in r5.json()["posts"])

    # Ne peut pas être confirmée deux fois
    r6 = client.post(f"/api/agent/actions/{action_id}/confirm", headers=_auth(tok))
    assert r6.status_code == 400


def test_propose_and_reject_action(client):
    tok = _reg(client, "dave_agent")
    _make_admin("dave_agent")

    r = client.post("/api/posts", data={"content": "post a garder"}, headers=_auth(tok))
    post_id = r.json()["id"]

    import services.agent as agent_service
    with database.get_conn() as conn:
        admin_id = conn.execute("SELECT id FROM users WHERE username = 'dave_agent'").fetchone()["id"]
    action_id = agent_service._propose_action("delete_post", {"post_id": post_id}, "faux positif", admin_id)

    r2 = client.post(f"/api/agent/actions/{action_id}/reject", headers=_auth(tok))
    assert r2.status_code == 200
    assert r2.json()["status"] == "rejected"

    # Le post n'a jamais été touché
    r3 = client.get("/api/feed", headers=_auth(tok))
    assert any(p["id"] == post_id for p in r3.json()["posts"])


def test_unknown_action_type_rejected():
    import pytest
    import services.agent as agent_service

    with pytest.raises(ValueError):
        agent_service._propose_action("ban_everyone", {}, "n'importe quoi", requested_by=1)
