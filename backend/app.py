"""
API du réseau social. Sert aussi le frontend terminal (fichiers statiques).

Lancer avec : uvicorn app:app --reload --port 8000
"""
import sys
import os
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "c_ext"))

from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from database import init_db, get_conn
from auth import hash_password, verify_password, create_token, get_current_user

try:
    import searchengine
except ImportError:
    raise RuntimeError(
        "Le module 'searchengine' (extension C) est introuvable. "
        "Compile-le avec : cd c_ext && python3 setup.py build_ext --inplace"
    )

app = FastAPI(title="Réseau social terminal")


def now() -> str:
    """Horodatage courant en UTC, format ISO 8601 (ex: 2026-07-25T10:00:00).

    Utilisé pour horodater tout ce qui est inséré en base (posts, likes,
    commentaires, messages, follows) afin de garder une référence commune
    indépendante du fuseau horaire de chaque utilisateur.
    """
    return datetime.datetime.utcnow().isoformat()


@app.on_event("startup")
def on_startup():
    """Exécuté une fois au démarrage du serveur (avant la première requête).

    Deux choses à faire avant que l'API soit utilisable :
    1. Créer les tables SQLite si elles n'existent pas encore (init_db).
    2. Reconstruire l'index de recherche en mémoire (l'extension C ne
       persiste rien sur disque : à chaque redémarrage du serveur, on relit
       tous les posts existants et on les réindexe un par un).
    """
    init_db()
    searchengine.clear()
    with get_conn() as conn:
        for row in conn.execute("SELECT id, content FROM posts"):
            searchengine.index_add(row["id"], row["content"])


# ---------- Schémas ----------
# Chaque classe décrit la forme attendue du JSON envoyé par le frontend pour
# une route donnée. FastAPI valide automatiquement la requête entrante contre
# ce schéma et renvoie une erreur 422 claire si un champ est invalide.

class RegisterIn(BaseModel):
    """Corps attendu pour POST /api/register.

    Le nom d'utilisateur est limité aux lettres/chiffres/underscore pour
    éviter les soucis d'affichage ou d'URL (ex: /api/users/{username}).
    """
    username: str = Field(min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=6, max_length=100)


class LoginIn(BaseModel):
    """Corps attendu pour POST /api/login."""
    username: str
    password: str


class PostIn(BaseModel):
    """Corps attendu pour POST /api/posts (création d'une publication)."""
    content: str = Field(min_length=1, max_length=1000)


class CommentIn(BaseModel):
    """Corps attendu pour POST /api/posts/{post_id}/comments."""
    content: str = Field(min_length=1, max_length=500)


class MessageIn(BaseModel):
    """Corps attendu pour POST /api/messages (message privé).

    `to` est le nom d'utilisateur du destinataire (pas son id), pour rester
    lisible côté commande terminal : `msg alice Salut !`.
    """
    to: str
    content: str = Field(min_length=1, max_length=2000)


# ---------- Auth ----------

@app.post("/api/register")
def register(body: RegisterIn):
    """Crée un nouveau compte utilisateur et renvoie directement un jeton de
    session (l'utilisateur est connecté automatiquement après inscription).

    Échoue avec 400 si le nom d'utilisateur est déjà pris.
    """
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (body.username,)).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Ce nom d'utilisateur existe déjà.")
        pw_hash, salt = hash_password(body.password)
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
            (body.username, pw_hash, salt, now()),
        )
        conn.commit()
        user_id = cur.lastrowid
    token = create_token(user_id, body.username)
    return {"token": token, "username": body.username}


@app.post("/api/login")
def login(body: LoginIn):
    """Vérifie les identifiants et renvoie un jeton JWT valable 7 jours.

    Échoue avec 401 (identifiants incorrects) sans préciser si c'est le nom
    d'utilisateur ou le mot de passe qui est faux, pour ne pas aider à
    deviner les comptes existants.
    """
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (body.username,)).fetchone()
    if not row or not verify_password(body.password, row["salt"], row["password_hash"]):
        raise HTTPException(status_code=401, detail="Identifiants incorrects.")
    token = create_token(row["id"], row["username"])
    return {"token": token, "username": row["username"]}


# ---------- Posts / fil d'actualité ----------

def _serialize_post(conn, row, current_user_id):
    """Transforme une ligne SQL `posts` en dict JSON prêt à renvoyer à l'API.

    Fait 3 requêtes supplémentaires (nombre de likes, si l'utilisateur
    courant a liké, nombre de commentaires) : acceptable au vu du volume
    actuel, mais à revoir avec des JOIN/sous-requêtes si le fil devient lent
    à grande échelle. Le préfixe `_` indique que cette fonction est un détail
    d'implémentation interne à ce fichier, pas une route API.
    """
    likes = conn.execute("SELECT COUNT(*) c FROM likes WHERE post_id = ?", (row["id"],)).fetchone()["c"]
    liked = conn.execute(
        "SELECT 1 FROM likes WHERE post_id = ? AND user_id = ?", (row["id"], current_user_id)
    ).fetchone() is not None
    comment_count = conn.execute("SELECT COUNT(*) c FROM comments WHERE post_id = ?", (row["id"],)).fetchone()["c"]
    author = conn.execute("SELECT username FROM users WHERE id = ?", (row["user_id"],)).fetchone()["username"]
    return {
        "id": row["id"],
        "author": author,
        "content": row["content"],
        "created_at": row["created_at"],
        "likes": likes,
        "liked_by_me": liked,
        "comments": comment_count,
    }


@app.post("/api/posts")
def create_post(body: PostIn, user=Depends(get_current_user)):
    """Publie un nouveau post pour l'utilisateur connecté.

    Insère en base ET indexe le texte dans le moteur de recherche C dans la
    même opération : un post créé est immédiatement trouvable par `search`.
    """
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO posts (user_id, content, created_at) VALUES (?, ?, ?)",
            (user["id"], body.content, now()),
        )
        conn.commit()
        post_id = cur.lastrowid
        searchengine.index_add(post_id, body.content)
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        return _serialize_post(conn, row, user["id"])


@app.get("/api/feed")
def feed(limit: int = 30, user=Depends(get_current_user)):
    """Fil d'actualité : posts de l'utilisateur courant + posts des comptes
    qu'il suit, triés du plus récent au plus ancien.

    `limit` plafonne le nombre de posts renvoyés (pas de pagination réelle
    pour l'instant, voir CHANGELOG.md pour les limitations connues).
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT p.* FROM posts p
            WHERE p.user_id = ?
               OR p.user_id IN (SELECT followed_id FROM follows WHERE follower_id = ?)
            ORDER BY p.created_at DESC
            LIMIT ?
            """,
            (user["id"], user["id"], limit),
        ).fetchall()
        return [_serialize_post(conn, r, user["id"]) for r in rows]


@app.post("/api/posts/{post_id}/like")
def like_post(post_id: int, user=Depends(get_current_user)):
    """Ajoute un like de l'utilisateur courant sur un post.

    Idempotent : si le post est déjà liké par cet utilisateur, l'insertion
    échoue silencieusement (contrainte PRIMARY KEY (post_id, user_id) dans
    schema.sql) et on répond quand même 200 — reliker n'est pas une erreur
    du point de vue de l'utilisateur.
    """
    with get_conn() as conn:
        post = conn.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone()
        if not post:
            raise HTTPException(status_code=404, detail="Post introuvable.")
        try:
            conn.execute(
                "INSERT INTO likes (post_id, user_id, created_at) VALUES (?, ?, ?)",
                (post_id, user["id"], now()),
            )
            conn.commit()
        except Exception:
            pass  # déjà liké, on ignore
        return {"ok": True}


@app.delete("/api/posts/{post_id}/like")
def unlike_post(post_id: int, user=Depends(get_current_user)):
    """Retire le like de l'utilisateur courant sur un post (sans erreur si
    le post n'était pas liké)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM likes WHERE post_id = ? AND user_id = ?", (post_id, user["id"]))
        conn.commit()
        return {"ok": True}


@app.post("/api/posts/{post_id}/comments")
def add_comment(post_id: int, body: CommentIn, user=Depends(get_current_user)):
    """Ajoute un commentaire de l'utilisateur courant sur un post existant."""
    with get_conn() as conn:
        post = conn.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone()
        if not post:
            raise HTTPException(status_code=404, detail="Post introuvable.")
        conn.execute(
            "INSERT INTO comments (post_id, user_id, content, created_at) VALUES (?, ?, ?, ?)",
            (post_id, user["id"], body.content, now()),
        )
        conn.commit()
        return {"ok": True}


@app.get("/api/posts/{post_id}/comments")
def list_comments(post_id: int, user=Depends(get_current_user)):
    """Liste les commentaires d'un post, du plus ancien au plus récent."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.*, u.username FROM comments c
            JOIN users u ON u.id = c.user_id
            WHERE c.post_id = ?
            ORDER BY c.created_at ASC
            """,
            (post_id,),
        ).fetchall()
        return [
            {"author": r["username"], "content": r["content"], "created_at": r["created_at"]}
            for r in rows
        ]


# ---------- Follow ----------

@app.post("/api/follow/{username}")
def follow_user(username: str, user=Depends(get_current_user)):
    """Fait suivre `username` par l'utilisateur courant.

    Refuse de se suivre soi-même (400). Idempotent comme les likes : suivre
    deux fois quelqu'un ne provoque pas d'erreur.
    """
    with get_conn() as conn:
        target = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
        if target["id"] == user["id"]:
            raise HTTPException(status_code=400, detail="Tu ne peux pas te suivre toi-même.")
        try:
            conn.execute(
                "INSERT INTO follows (follower_id, followed_id, created_at) VALUES (?, ?, ?)",
                (user["id"], target["id"], now()),
            )
            conn.commit()
        except Exception:
            pass
        return {"ok": True}


@app.delete("/api/follow/{username}")
def unfollow_user(username: str, user=Depends(get_current_user)):
    """Fait arrêter à l'utilisateur courant de suivre `username`."""
    with get_conn() as conn:
        target = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
        conn.execute(
            "DELETE FROM follows WHERE follower_id = ? AND followed_id = ?", (user["id"], target["id"])
        )
        conn.commit()
        return {"ok": True}


@app.get("/api/users/{username}")
def get_user_profile(username: str, user=Depends(get_current_user)):
    """Profil public d'un utilisateur : bio + compteurs (posts, followers,
    abonnements). Ne renvoie jamais le mot de passe ni son hash."""
    with get_conn() as conn:
        row = conn.execute("SELECT id, username, bio, created_at FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
        followers = conn.execute("SELECT COUNT(*) c FROM follows WHERE followed_id = ?", (row["id"],)).fetchone()["c"]
        following = conn.execute("SELECT COUNT(*) c FROM follows WHERE follower_id = ?", (row["id"],)).fetchone()["c"]
        post_count = conn.execute("SELECT COUNT(*) c FROM posts WHERE user_id = ?", (row["id"],)).fetchone()["c"]
        return {
            "username": row["username"],
            "bio": row["bio"],
            "followers": followers,
            "following": following,
            "posts": post_count,
        }


# ---------- Messagerie privée ----------

@app.post("/api/messages")
def send_message(body: MessageIn, user=Depends(get_current_user)):
    """Envoie un message privé de l'utilisateur courant vers `body.to`."""
    with get_conn() as conn:
        target = conn.execute("SELECT id FROM users WHERE username = ?", (body.to,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Destinataire introuvable.")
        conn.execute(
            "INSERT INTO messages (sender_id, receiver_id, content, created_at) VALUES (?, ?, ?, ?)",
            (user["id"], target["id"], body.content, now()),
        )
        conn.commit()
        return {"ok": True}


@app.get("/api/messages/{username}")
def get_conversation(username: str, user=Depends(get_current_user)):
    """Historique complet des messages échangés entre l'utilisateur courant
    et `username`, du plus ancien au plus récent.

    Effet de bord : marque comme lus tous les messages reçus de `username`
    (comme ouvrir une conversation dans une vraie messagerie).
    """
    with get_conn() as conn:
        target = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
        rows = conn.execute(
            """
            SELECT m.*, u.username as sender_name FROM messages m
            JOIN users u ON u.id = m.sender_id
            WHERE (m.sender_id = ? AND m.receiver_id = ?)
               OR (m.sender_id = ? AND m.receiver_id = ?)
            ORDER BY m.created_at ASC
            """,
            (user["id"], target["id"], target["id"], user["id"]),
        ).fetchall()
        conn.execute(
            "UPDATE messages SET is_read = 1 WHERE sender_id = ? AND receiver_id = ?",
            (target["id"], user["id"]),
        )
        conn.commit()
        return [
            {"from": r["sender_name"], "content": r["content"], "created_at": r["created_at"]}
            for r in rows
        ]


@app.get("/api/inbox")
def inbox(user=Depends(get_current_user)):
    """Liste les conversations avec le dernier message et le nombre de non-lus."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT u.username,
                   MAX(m.created_at) as last_at,
                   SUM(CASE WHEN m.receiver_id = ? AND m.is_read = 0 THEN 1 ELSE 0 END) as unread
            FROM messages m
            JOIN users u ON u.id = (CASE WHEN m.sender_id = ? THEN m.receiver_id ELSE m.sender_id END)
            WHERE m.sender_id = ? OR m.receiver_id = ?
            GROUP BY u.username
            ORDER BY last_at DESC
            """,
            (user["id"], user["id"], user["id"], user["id"]),
        ).fetchall()
        return [{"with": r["username"], "unread": r["unread"], "last_at": r["last_at"]} for r in rows]


# ---------- Recherche (utilise l'extension C) ----------

@app.get("/api/search")
def search(q: str, user=Depends(get_current_user)):
    """Recherche full-text dans les posts via l'extension C (searchengine).

    `searchengine.search` renvoie des `(doc_id, score)` triés par score
    décroissant (score = nombre total d'occurrences des mots recherchés
    dans le post). On récupère ensuite chaque post correspondant en base et
    on ajoute le score dans la réponse pour que le frontend puisse l'afficher.
    """
    results = searchengine.search(q, 20)
    if not results:
        return []
    with get_conn() as conn:
        out = []
        for doc_id, score in results:
            row = conn.execute("SELECT * FROM posts WHERE id = ?", (doc_id,)).fetchone()
            if row:
                serialized = _serialize_post(conn, row, user["id"])
                serialized["score"] = score
                out.append(serialized)
        return out


# ---------- Frontend statique (interface terminal) ----------
# Le backend sert aussi le frontend : pas besoin d'un serveur web séparé en
# développement. `/static/...` expose les fichiers bruts (utile si on ajoute
# des CSS/JS séparés plus tard), et `/` renvoie directement index.html.

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def serve_index():
    """Sert la page terminal (frontend/index.html) à la racine du site."""
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
