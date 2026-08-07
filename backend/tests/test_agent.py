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


def test_propose_and_confirm_delete_post_with_likes_and_comments(client):
    """Régression : la première version de _execute("delete_post") ne
    nettoyait pas likes/comments/notifications avant de supprimer le post,
    ce qui plantait avec une erreur FOREIGN KEY constraint dès qu'un post
    avait la moindre interaction. Ce test couvre précisément ce cas."""
    tok = _reg(client, "mona_agent")
    _make_admin("mona_agent")
    tok_other = _reg(client, "nina_other")

    r = client.post("/api/posts", data={"content": "post populaire"}, headers=_auth(tok))
    post_id = r.json()["id"]

    # Une interaction d'un autre utilisateur : like + commentaire
    assert client.post(f"/api/posts/{post_id}/like", headers=_auth(tok_other)).status_code == 200
    assert client.post(
        f"/api/posts/{post_id}/comments", json={"content": "sympa ce post"}, headers=_auth(tok_other)
    ).status_code == 200

    import services.agent as agent_service
    with database.get_conn() as conn:
        admin_id = conn.execute("SELECT id FROM users WHERE username = 'mona_agent'").fetchone()["id"]
    action_id = agent_service._propose_action(
        "delete_post", {"post_id": post_id}, "test régression FK", admin_id
    )

    r_confirm = client.post(f"/api/agent/actions/{action_id}/confirm", headers=_auth(tok))
    assert r_confirm.status_code == 200, r_confirm.text
    assert r_confirm.json()["status"] == "executed"

    r_feed = client.get("/api/feed", headers=_auth(tok))
    assert not any(p["id"] == post_id for p in r_feed.json()["posts"])


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


def test_ban_user_blocks_login_and_existing_session(client):
    tok_admin = _reg(client, "erin_agent")
    _make_admin("erin_agent")
    tok_target = _reg(client, "frank_target")

    # La session de frank fonctionne avant le ban
    r0 = client.get("/api/notifications", headers=_auth(tok_target))
    assert r0.status_code == 200

    import services.agent as agent_service
    with database.get_conn() as conn:
        admin_id = conn.execute("SELECT id FROM users WHERE username = 'erin_agent'").fetchone()["id"]
        target_id = conn.execute("SELECT id FROM users WHERE username = 'frank_target'").fetchone()["id"]
    action_id = agent_service._propose_action(
        "ban_user", {"user_id": target_id, "username": "frank_target"}, "spam répété", admin_id
    )

    r1 = client.post(f"/api/agent/actions/{action_id}/confirm", headers=_auth(tok_admin))
    assert r1.status_code == 200
    assert r1.json()["status"] == "executed"

    # Session déjà ouverte : coupée immédiatement (pas seulement au prochain login)
    r2 = client.get("/api/notifications", headers=_auth(tok_target))
    assert r2.status_code == 403

    # Nouveau login : refusé aussi
    r3 = client.post("/api/login", json={"username": "frank_target", "password": "secret12"})
    assert r3.status_code == 403

    # Débannissement : lève le blocage
    action_id2 = agent_service._propose_action(
        "unban_user", {"user_id": target_id, "username": "frank_target"}, "recours accepté", admin_id
    )
    r4 = client.post(f"/api/agent/actions/{action_id2}/confirm", headers=_auth(tok_admin))
    assert r4.status_code == 200
    r5 = client.post("/api/login", json={"username": "frank_target", "password": "secret12"})
    assert r5.status_code == 200


def test_cannot_ban_admin(client):
    tok_admin = _reg(client, "gina_agent")
    _make_admin("gina_agent")
    _reg(client, "henry_agent")
    _make_admin("henry_agent")

    import services.agent as agent_service
    with database.get_conn() as conn:
        admin_id = conn.execute("SELECT id FROM users WHERE username = 'gina_agent'").fetchone()["id"]
        target_id = conn.execute("SELECT id FROM users WHERE username = 'henry_agent'").fetchone()["id"]
    action_id = agent_service._propose_action(
        "ban_user", {"user_id": target_id, "username": "henry_agent"}, "test", admin_id
    )
    r = client.post(f"/api/agent/actions/{action_id}/confirm", headers=_auth(tok_admin))
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    assert "administrateur" in r.json()["result"]["error"]


def test_force_logout_does_not_ban(client):
    tok_admin = _reg(client, "ivy_agent")
    _make_admin("ivy_agent")
    tok_target = _reg(client, "jack_target")

    import services.agent as agent_service
    with database.get_conn() as conn:
        admin_id = conn.execute("SELECT id FROM users WHERE username = 'ivy_agent'").fetchone()["id"]
        target_id = conn.execute("SELECT id FROM users WHERE username = 'jack_target'").fetchone()["id"]
    action_id = agent_service._propose_action(
        "force_logout", {"user_id": target_id, "username": "jack_target"}, "compte probablement compromis", admin_id
    )
    r1 = client.post(f"/api/agent/actions/{action_id}/confirm", headers=_auth(tok_admin))
    assert r1.status_code == 200

    # Ancienne session coupée...
    r2 = client.get("/api/notifications", headers=_auth(tok_target))
    assert r2.status_code == 401

    # ...mais un nouveau login fonctionne (pas banni)
    r3 = client.post("/api/login", json={"username": "jack_target", "password": "secret12"})
    assert r3.status_code == 200


def test_warn_user_creates_notification(client):
    tok_admin = _reg(client, "kate_agent")
    _make_admin("kate_agent")
    tok_target = _reg(client, "leo_target")

    import services.agent as agent_service
    with database.get_conn() as conn:
        admin_id = conn.execute("SELECT id FROM users WHERE username = 'kate_agent'").fetchone()["id"]
        target_id = conn.execute("SELECT id FROM users WHERE username = 'leo_target'").fetchone()["id"]
    action_id = agent_service._propose_action(
        "warn_user",
        {"user_id": target_id, "username": "leo_target", "message": "merci de respecter les règles"},
        "langage limite",
        admin_id,
    )
    r1 = client.post(f"/api/agent/actions/{action_id}/confirm", headers=_auth(tok_admin))
    assert r1.status_code == 200

    r2 = client.get("/api/notifications", headers=_auth(tok_target))
    assert r2.status_code == 200
    notifs = r2.json()
    assert any(n["type"] == "admin_warning" and "respecter" in n["content"] for n in notifs)


def test_concurrent_confirm_executes_exactly_once(client):
    """Preuve concrète (pas juste théorique) que la parade anti-course tient.

    On appelle `agent_service.confirm_action` directement depuis de VRAIS
    threads OS (pas via TestClient/HTTP) : la route /confirm est `async def`
    (nécessaire pour le push websocket) et appelle du code synchrone
    bloquant SANS le déporter dans un threadpool — ce qui sérialise de facto
    les requêtes concurrentes sur la boucle d'événements et masquerait une
    éventuelle course au niveau HTTP, sans rien prouver sur la vraie garantie
    qui nous intéresse : l'atomicité de l'UPDATE SQL lui-même, indépendante
    de la couche ASGI. Tester directement le service élimine ce facteur de
    confusion.

    On utilise propose_warn_user précisément parce que chaque exécution
    laisse une trace observable (une notification supplémentaire) : si la
    course n'était pas fermée, on verrait plusieurs notifications au lieu
    d'une seule.
    """
    import concurrent.futures
    import services.agent as agent_service

    tok_admin = _reg(client, "oscar_agent")
    _make_admin("oscar_agent")
    tok_target = _reg(client, "paul_target")

    with database.get_conn() as conn:
        admin_id = conn.execute("SELECT id FROM users WHERE username = 'oscar_agent'").fetchone()["id"]
        target_id = conn.execute("SELECT id FROM users WHERE username = 'paul_target'").fetchone()["id"]
    action_id = agent_service._propose_action(
        "warn_user",
        {"user_id": target_id, "username": "paul_target", "message": "dernier avertissement"},
        "récidive",
        admin_id,
    )

    def _confirm():
        try:
            return agent_service.confirm_action(action_id, admin_id)
        except Exception as e:  # HTTPException notamment (déjà traité/en cours)
            return e

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _confirm(), range(8)))

    executed = [r for r in results if isinstance(r, dict) and r.get("status") == "executed"]
    refused = [r for r in results if not isinstance(r, dict)]
    assert len(executed) == 1, f"attendu exactement 1 exécution, obtenu {len(executed)} : {results}"
    assert len(refused) == 7

    # Preuve indépendante côté effet de bord : une seule notification créée,
    # pas huit.
    r_notifs = client.get("/api/notifications", headers=_auth(tok_target))
    admin_warnings = [n for n in r_notifs.json() if n["type"] == "admin_warning"]
    assert len(admin_warnings) == 1


def test_concurrent_confirm_and_reject_only_one_wins(client):
    """Même parade, testée directement au niveau service (voir docstring du
    test précédent pour pourquoi pas via HTTP) : confirm et reject en vraie
    concurrence sur la même action, un seul des deux doit l'emporter."""
    import concurrent.futures
    import services.agent as agent_service

    tok_admin = _reg(client, "quinn_agent")
    _make_admin("quinn_agent")

    r = client.post("/api/posts", data={"content": "post en litige"}, headers=_auth(tok_admin))
    post_id = r.json()["id"]

    with database.get_conn() as conn:
        admin_id = conn.execute("SELECT id FROM users WHERE username = 'quinn_agent'").fetchone()["id"]
    action_id = agent_service._propose_action("delete_post", {"post_id": post_id}, "test course", admin_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f_confirm = pool.submit(agent_service.confirm_action, action_id, admin_id)
        f_reject = pool.submit(agent_service.reject_action, action_id, admin_id)

        def _outcome(fut):
            try:
                return fut.result()
            except Exception as e:
                return e

        out_confirm, out_reject = _outcome(f_confirm), _outcome(f_reject)

    ok_outcomes = [o for o in (out_confirm, out_reject) if isinstance(o, dict)]
    assert len(ok_outcomes) == 1, f"un seul des deux (confirm XOR reject) doit réussir : {(out_confirm, out_reject)}"

    with database.get_conn() as conn:
        final_status = conn.execute(
            "SELECT status FROM agent_actions WHERE id = ?", (action_id,)
        ).fetchone()["status"]
    assert final_status in ("executed", "rejected")
