"""
Gestion des connexions WebSocket pour les mises à jour en temps réel.

Une connexion WebSocket est ouverte par onglet de navigateur connecté (le
frontend s'y connecte juste après login/register, voir frontend/index.html).
On garde une table `user_id -> ensemble de connexions actives` (un même
utilisateur peut avoir plusieurs onglets ouverts) afin de pouvoir lui pousser
des événements — nouveau message privé, nouveau post d'un compte suivi —
sans qu'il ait besoin de retaper une commande pour rafraîchir.

Ce module ne connaît rien de la base de données ni de la logique métier :
il sait seulement "envoyer ce JSON à cet utilisateur, s'il est connecté".
C'est app.py qui décide quand l'appeler (après un INSERT en base).
"""
from fastapi import WebSocket


class ConnectionManager:
    """Registre en mémoire des connexions WebSocket actives, par utilisateur.

    Vit uniquement en RAM : si le serveur redémarre, tout le monde doit se
    reconnecter (le frontend le fait automatiquement après un login).
    """

    def __init__(self):
        self.active: dict[int, set[WebSocket]] = {}

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        """Accepte la connexion WebSocket et l'enregistre pour cet utilisateur."""
        await websocket.accept()
        self.active.setdefault(user_id, set()).add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        """Retire une connexion fermée (onglet fermé, perte réseau, etc.)."""
        conns = self.active.get(user_id)
        if not conns:
            return
        conns.discard(websocket)
        if not conns:
            del self.active[user_id]

    async def send_to_user(self, user_id: int, message: dict) -> None:
        """Pousse un événement JSON à toutes les connexions actives d'un
        utilisateur. Ne fait rien si l'utilisateur n'est pas connecté
        (le message reste consultable plus tard via l'API classique,
        ex: GET /api/messages/{username})."""
        conns = self.active.get(user_id)
        if not conns:
            return
        dead = []
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                # Connexion morte (fermée sans prévenir) : nettoyage différé
                # pour ne pas modifier le set pendant qu'on l'itère.
                dead.append(ws)
        for ws in dead:
            conns.discard(ws)

    async def broadcast_to_users(self, user_ids, message: dict) -> None:
        """Pousse le même événement à plusieurs utilisateurs (ex: tous les
        followers quand quelqu'un publie un post)."""
        for uid in user_ids:
            await self.send_to_user(uid, message)


# Instance unique partagée par toute l'application (importée par app.py).
manager = ConnectionManager()
