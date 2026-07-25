"""
API du réseau social. Sert aussi le frontend terminal (fichiers statiques).

Lancer avec : uvicorn app:app --reload --port 8000
"""
import sys
import os
import re
import datetime
import uuid
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "c_ext"))

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, Form, File, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from database import init_db, get_conn
from auth import hash_password, verify_password, create_token, get_current_user, decode_token
from realtime import manager

try:
    import searchengine
except ImportError:
    raise RuntimeError(
        "Le module 'searchengine' (extension C) est introuvable. "
        "Compile-le avec : cd c_ext && python3 setup.py build_ext --inplace"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    yield


app = FastAPI(title="Réseau social terminal", lifespan=lifespan)

# ---------- Stockage des images jointes aux posts ----------
# Les fichiers uploadés sont stockés tels quels sur disque (pas en base),
# dans backend/uploads/. Ce dossier est hors Git (voir .gitignore) : le
# contenu uploadé est propre à chaque environnement, comme la base SQLite.
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Seuls ces types MIME sont acceptés. On se base sur le Content-Type déclaré
# par le navigateur (pas sur le nom de fichier envoyé par le client, qui
# n'est jamais fiable) pour choisir l'extension du fichier stocké.
ALLOWED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 Mo


async def _save_uploaded_image(image: UploadFile) -> str:
    """Valide et enregistre une image uploadée, renvoie son nom de fichier.

    - Le type de fichier est vérifié contre une liste blanche (pas de .exe,
      .php, etc. déguisés en image).
    - Le nom de fichier final est généré aléatoirement (UUID) : on ignore
      totalement le nom envoyé par le client, ce qui élimine tout risque de
      "path traversal" (ex: un nom de fichier contenant "../../").
    - La taille est plafonnée à MAX_IMAGE_SIZE pour éviter qu'un upload
      énorme ne sature le disque ou la mémoire du serveur.
    """
    if image.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Format d'image non supporté (formats acceptés : PNG, JPEG, GIF, WEBP).",
        )
    data = await image.read()
    if len(data) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=400, detail="Image trop volumineuse (5 Mo maximum).")
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Fichier image vide.")

    filename = uuid.uuid4().hex + ALLOWED_IMAGE_TYPES[image.content_type]
    filepath = os.path.join(UPLOAD_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(data)
    return filename


def now() -> str:
    """Horodatage courant en UTC, format ISO 8601 (ex: 2026-07-25T10:00:00).

    Utilisé pour horodater tout ce qui est inséré en base (posts, likes,
    commentaires, messages, follows) afin de garder une référence commune
    indépendante du fuseau horaire de chaque utilisateur.
    """
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()


# ---------- Temps réel (WebSocket) ----------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str | None = None):
    """Connexion temps réel : pousse au navigateur les nouveaux messages
    privés reçus et les nouveaux posts des comptes suivis, sans qu'il soit
    nécessaire de retaper une commande pour rafraîchir.

    Authentification par jeton JWT passé en paramètre de l'URL
    (ws://.../ws?token=...) plutôt qu'en en-tête HTTP classique : l'API des
    navigateurs pour ouvrir un WebSocket ne permet pas d'y ajouter un en-tête
    Authorization personnalisé.

    Cette route ne fait que garder la connexion ouverte et écouter sa
    fermeture ; les messages envoyés PAR le client ne sont pas traités pour
    l'instant (le canal est actuellement à sens unique : serveur → client).
    """
    if not token:
        await websocket.close(code=4401)
        return
    try:
        payload = decode_token(token)
    except Exception:
        await websocket.close(code=4401)
        return

    user_id = int(payload["sub"])
    await manager.connect(user_id, websocket)
    try:
        while True:
            # On ne fait rien du contenu reçu : cette boucle sert uniquement
            # à détecter la déconnexion (fermeture d'onglet, perte réseau).
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)


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


class ProfileIn(BaseModel):
    """Corps attendu pour PATCH /api/me (modification de son propre profil)."""
    bio: str = Field(max_length=200)


class PostIn(BaseModel):
    """Conservé pour référence/documentation : POST /api/posts n'utilise
    plus ce schéma JSON directement depuis l'ajout de l'upload d'image, car
    un formulaire multipart (texte + fichier) est nécessaire pour envoyer un
    fichier. Voir la signature de `create_post` (paramètres Form/File)."""
    content: str = Field(min_length=1, max_length=1000)


class PostEditIn(BaseModel):
    """Corps attendu pour PATCH /api/posts/{post_id}."""
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


# ---------- Profil de l'utilisateur connecté ----------

@app.patch("/api/me")
def update_profile(body: ProfileIn, user=Depends(get_current_user)):
    """Modifie le profil de l'utilisateur connecté (bio uniquement pour
    l'instant). Renvoie le profil à jour."""
    with get_conn() as conn:
        conn.execute("UPDATE users SET bio = ? WHERE id = ?", (body.bio, user["id"]))
        conn.commit()
    return {"username": user["username"], "bio": body.bio}


# ---------- Notifications ----------
# Une notification est créée en base à chaque "événement social" concernant
# un utilisateur (quelqu'un like son post, le suit, ou lui envoie un
# message). Contrairement aux WebSocket seuls, elle est persistée : même
# hors ligne au moment de l'événement, l'utilisateur la retrouve plus tard
# via GET /api/notifications.

def _create_notification(conn, user_id, ntype, from_username, post_id=None, content=None):
    """Insère une notification en base et la renvoie sérialisée (dict JSON).

    N'envoie rien par WebSocket elle-même : c'est à l'appelant de décider si
    un push temps réel est nécessaire (voir like_post, follow_user — pour
    les messages, l'événement WebSocket "message" existant suffit déjà,
    donc aucun push supplémentaire n'est fait pour ce type).
    """
    created_at = now()
    cur = conn.execute(
        "INSERT INTO notifications (user_id, type, from_username, post_id, content, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, ntype, from_username, post_id, content, created_at),
    )
    conn.commit()
    return {
        "id": cur.lastrowid,
        "type": ntype,
        "from": from_username,
        "post_id": post_id,
        "content": content,
        "created_at": created_at,
        "is_read": False,
    }


@app.get("/api/notifications")
def list_notifications(limit: int = 30, user=Depends(get_current_user)):
    """Liste les notifications de l'utilisateur courant, les plus récentes
    d'abord. Marque comme lues toutes celles renvoyées par cet appel — comme
    ouvrir sa boîte de notifications dans une vraie appli (voir aussi
    get_conversation, qui suit le même principe pour les messages)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user["id"], limit),
        ).fetchall()
        result = [
            {
                "id": r["id"],
                "type": r["type"],
                "from": r["from_username"],
                "post_id": r["post_id"],
                "content": r["content"],
                "created_at": r["created_at"],
                "is_read": bool(r["is_read"]),
            }
            for r in rows
        ]
        conn.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user["id"],))
        conn.commit()
        return result


# ---------- Hashtags et mentions ----------
# Deux mini-fonctionnalités indépendantes qui partagent la même logique
# d'extraction par expression régulière sur le texte d'un post ou commentaire :
# - un hashtag (#exemple) sert à retrouver tous les posts qui le contiennent
#   (table post_hashtags, indépendante du moteur de recherche C : on veut un
#   classement chronologique exact, pas un score de pertinence textuelle) ;
# - une mention (@pseudo) déclenche une notification pour l'utilisateur visé,
#   s'il existe et si ce n'est pas lui-même qui se mentionne.

# \w capture aussi les lettres accentuées en Python 3 (re.UNICODE par défaut),
# donc #café ou @François fonctionnent. Longueur plafonnée pour éviter un
# hashtag/mention démesuré construit exprès pour abuser du stockage.
HASHTAG_RE = re.compile(r"#(\w{1,50})", re.UNICODE)
MENTION_RE = re.compile(r"@(\w{1,30})", re.UNICODE)


def _extract_hashtags(text: str) -> set[str]:
    """Extrait les hashtags d'un texte, normalisés en minuscules et sans
    doublons (#Python et #python comptent comme un seul hashtag)."""
    return {tag.lower() for tag in HASHTAG_RE.findall(text)}


def _extract_mentions(text: str) -> set[str]:
    """Extrait les noms d'utilisateur mentionnés (@pseudo) dans un texte,
    sans doublons. La casse est conservée ici ; la recherche du compte
    correspondant se fait ensuite insensible à la casse (COLLATE NOCASE)."""
    return set(MENTION_RE.findall(text))


def _index_hashtags(conn, post_id: int, content: str) -> None:
    """Enregistre les hashtags d'un post dans `post_hashtags`."""
    for tag in _extract_hashtags(content):
        conn.execute(
            "INSERT OR IGNORE INTO post_hashtags (post_id, hashtag) VALUES (?, ?)",
            (post_id, tag),
        )


def _deindex_hashtags(conn, post_id: int) -> None:
    """Retire tous les hashtags associés à un post (avant réindexation lors
    d'une édition, ou lors d'une suppression)."""
    conn.execute("DELETE FROM post_hashtags WHERE post_id = ?", (post_id,))


def _create_mention_notifications(conn, content: str, post_id: int, actor_id: int, actor_username: str, only_usernames=None) -> list:
    """Crée une notification 'mention' pour chaque utilisateur valide
    mentionné (@pseudo) dans `content`, hors auto-mention.

    `only_usernames` : si fourni, restreint les notifications à cet
    ensemble de pseudos déjà extraits ailleurs (utilisé par `edit_post` pour
    ne notifier que les mentions nouvellement ajoutées lors d'une édition,
    tout en gardant un aperçu du texte réellement publié dans la
    notification plutôt qu'un texte reconstitué). Par défaut (`None`), les
    mentions sont extraites directement de `content`.

    Ne fait AUCUN appel réseau/WebSocket elle-même (voir _create_notification) :
    renvoie juste la liste des `(user_id, notification)` à pousser en temps
    réel, à traiter par l'appelant après la fermeture du `with get_conn()`,
    comme pour les notifications de like et de follow.
    """
    snippet = content[:80] + ("…" if len(content) > 80 else "")
    usernames = only_usernames if only_usernames is not None else _extract_mentions(content)
    to_push = []
    for uname in usernames:
        target = conn.execute(
            "SELECT id, username FROM users WHERE username = ? COLLATE NOCASE", (uname,)
        ).fetchone()
        if not target or target["id"] == actor_id:
            continue
        notification = _create_notification(
            conn, target["id"], "mention", actor_username, post_id=post_id, content=snippet
        )
        to_push.append((target["id"], notification))
    return to_push


# ---------- Posts / fil d'actualité ----------

def _serialize_posts(conn, rows, current_user_id):
    """Transforme des lignes SQL `posts` en dicts JSON prêts pour l'API.

    Les compteurs (likes, commentaires), l'auteur et le fait que
    l'utilisateur courant ait liké sont récupérés pour TOUS les posts de la
    page en une poignée de requêtes groupées, au lieu d'une série de
    requêtes par post (problème du "N+1" : un fil de 30 posts coûtait 120
    allers-retours en base). Le préfixe `_` indique un détail
    d'implémentation interne à ce fichier, pas une route API.
    """
    rows = list(rows)
    if not rows:
        return []

    post_ids = [row["id"] for row in rows]
    placeholders = ",".join("?" * len(post_ids))

    like_counts = {
        r["post_id"]: r["c"]
        for r in conn.execute(
            f"SELECT post_id, COUNT(*) c FROM likes WHERE post_id IN ({placeholders}) GROUP BY post_id",
            post_ids,
        )
    }
    comment_counts = {
        r["post_id"]: r["c"]
        for r in conn.execute(
            f"SELECT post_id, COUNT(*) c FROM comments WHERE post_id IN ({placeholders}) GROUP BY post_id",
            post_ids,
        )
    }
    liked_by_me = {
        r["post_id"]
        for r in conn.execute(
            f"SELECT post_id FROM likes WHERE user_id = ? AND post_id IN ({placeholders})",
            [current_user_id, *post_ids],
        )
    }
    author_ids = list({row["user_id"] for row in rows})
    authors = {
        r["id"]: r["username"]
        for r in conn.execute(
            f"SELECT id, username FROM users WHERE id IN ({','.join('?' * len(author_ids))})",
            author_ids,
        )
    }

    serialized = []
    for row in rows:
        image_path = row["image_path"] if "image_path" in row.keys() else None
        serialized.append(
            {
                "id": row["id"],
                "author": authors[row["user_id"]],
                "content": row["content"],
                "created_at": row["created_at"],
                "likes": like_counts.get(row["id"], 0),
                "liked_by_me": row["id"] in liked_by_me,
                "comments": comment_counts.get(row["id"], 0),
                "image_url": f"/uploads/{image_path}" if image_path else None,
            }
        )
    return serialized


def _serialize_post(conn, row, current_user_id):
    """Version à un seul post de `_serialize_posts`."""
    return _serialize_posts(conn, [row], current_user_id)[0]


@app.post("/api/posts")
async def create_post(
    content: str = Form(..., min_length=1, max_length=1000),
    image: UploadFile | None = File(None),
    user=Depends(get_current_user),
):
    """Publie un nouveau post pour l'utilisateur connecté, avec une image
    optionnelle jointe.

    Reçu en `multipart/form-data` (et non JSON) car c'est le seul format
    HTTP standard permettant d'envoyer du texte ET un fichier binaire dans
    la même requête. Voir `_save_uploaded_image` pour les validations de
    sécurité appliquées à l'image (type, taille, nom de fichier).

    Insère en base ET indexe le texte dans le moteur de recherche C dans la
    même opération : un post créé est immédiatement trouvable par `search`.
    Les hashtags (#exemple) sont indexés pour `GET /api/hashtags/{tag}`, et
    chaque mention (@pseudo) valide déclenche une notification pour la
    personne visée. Notifie aussi en temps réel (WebSocket) les followers
    actuellement connectés, pour que leur fil se mette à jour sans action
    de leur part.
    """
    image_filename = None
    if image is not None and image.filename:
        image_filename = await _save_uploaded_image(image)

    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO posts (user_id, content, created_at, image_path) VALUES (?, ?, ?, ?)",
            (user["id"], content, now(), image_filename),
        )
        conn.commit()
        post_id = cur.lastrowid
        searchengine.index_add(post_id, content)
        _index_hashtags(conn, post_id, content)
        conn.commit()
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        serialized = _serialize_post(conn, row, user["id"])
        follower_ids = [
            r["follower_id"]
            for r in conn.execute("SELECT follower_id FROM follows WHERE followed_id = ?", (user["id"],))
        ]
        mention_notifications = _create_mention_notifications(conn, content, post_id, user["id"], user["username"])

    await manager.broadcast_to_users(follower_ids, {"type": "new_post", "post": serialized})
    for target_id, notification in mention_notifications:
        await manager.send_to_user(target_id, {"type": "notification", "notification": notification})
    return serialized


@app.get("/api/feed")
def feed(limit: int = 30, before: int | None = None, user=Depends(get_current_user)):
    """Fil d'actualité paginé : posts de l'utilisateur courant + posts des
    comptes qu'il suit, triés du plus récent au plus ancien.

    Pagination par curseur (`before`) plutôt que par offset : on demande "les
    posts dont l'id est strictement inférieur à `before`". Comme `id` est
    auto-incrémenté et donc strictement croissant avec le temps d'insertion,
    trier/filtrer sur `id` donne le même ordre que trier sur `created_at`,
    sans les soucis d'un offset classique (un post publié par quelqu'un
    d'autre pendant qu'on parcourt les pages ne décale pas les résultats
    suivants, contrairement à `OFFSET n` qui recompte depuis le début à
    chaque appel).

    `limit` est plafonné à 100 pour éviter qu'un client n'en demande trop
    d'un coup. On sélectionne `limit + 1` lignes pour savoir s'il reste des
    posts au-delà de cette page (`has_more`) sans avoir à faire un second
    aller-retour en base juste pour un COUNT.

    Réponse : {"posts": [...], "has_more": bool, "next_cursor": id|null}
    `next_cursor` est à repasser dans `before` pour demander la page
    suivante ; `null` quand il n'y a plus rien après cette page.
    """
    limit = max(1, min(limit, 100))
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT p.* FROM posts p
            WHERE (p.user_id = ?
               OR p.user_id IN (SELECT followed_id FROM follows WHERE follower_id = ?))
              AND (? IS NULL OR p.id < ?)
            ORDER BY p.id DESC
            LIMIT ?
            """,
            (user["id"], user["id"], before, before, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        posts = _serialize_posts(conn, rows, user["id"])
        next_cursor = rows[-1]["id"] if has_more and rows else None
        return {"posts": posts, "has_more": has_more, "next_cursor": next_cursor}


# ---------- Hashtags (routes) ----------
# ATTENTION À L'ORDRE : "/api/hashtags/trending" doit être déclarée AVANT
# "/api/hashtags/{tag}", sinon FastAPI ferait correspondre une requête sur
# "trending" au paramètre dynamique {tag} de la route générique (les routes
# sont testées dans leur ordre de déclaration).

@app.get("/api/hashtags/trending")
def trending_hashtags(limit: int = 10, user=Depends(get_current_user)):
    """Hashtags les plus utilisés tous posts confondus, du plus fréquent au
    moins fréquent (nombre de posts distincts contenant ce hashtag)."""
    limit = max(1, min(limit, 50))
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT hashtag, COUNT(*) as count FROM post_hashtags
            GROUP BY hashtag ORDER BY count DESC, hashtag ASC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [{"hashtag": r["hashtag"], "count": r["count"]} for r in rows]


@app.get("/api/hashtags/{tag}")
def posts_by_hashtag(tag: str, limit: int = 20, before: int | None = None, user=Depends(get_current_user)):
    """Liste les posts contenant le hashtag `tag`, du plus récent au plus
    ancien. Même pagination par curseur que `/api/feed` (voir sa docstring
    pour le détail du principe) — réponse sous la même forme
    `{"posts": [...], "has_more": bool, "next_cursor": id|null}`.

    Le `#` initial est optionnel dans l'URL (`/api/hashtags/python` et
    `/api/hashtags/%23python` renvoient le même résultat) ; la comparaison
    est insensible à la casse (les hashtags sont stockés en minuscules,
    voir `_index_hashtags`).
    """
    limit = max(1, min(limit, 100))
    tag_normalized = tag.lower().lstrip("#")
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT p.* FROM posts p
            JOIN post_hashtags h ON h.post_id = p.id
            WHERE h.hashtag = ? AND (? IS NULL OR p.id < ?)
            ORDER BY p.id DESC
            LIMIT ?
            """,
            (tag_normalized, before, before, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        posts = _serialize_posts(conn, rows, user["id"])
        next_cursor = rows[-1]["id"] if has_more and rows else None
        return {"posts": posts, "has_more": has_more, "next_cursor": next_cursor}


@app.post("/api/posts/{post_id}/like")
async def like_post(post_id: int, user=Depends(get_current_user)):
    """Ajoute un like de l'utilisateur courant sur un post.

    Idempotent : si le post est déjà liké par cet utilisateur, l'insertion
    échoue silencieusement (contrainte PRIMARY KEY (post_id, user_id) dans
    schema.sql) et on répond quand même 200 — reliker n'est pas une erreur
    du point de vue de l'utilisateur. Dans ce cas, aucune notification n'est
    recréée (on ne notifie qu'au premier like).

    Notifie l'auteur du post (sauf s'il like son propre post) : la
    notification est toujours enregistrée en base, et poussée en temps réel
    en plus s'il est connecté.
    """
    with get_conn() as conn:
        post = conn.execute("SELECT id, user_id FROM posts WHERE id = ?", (post_id,)).fetchone()
        if not post:
            raise HTTPException(status_code=404, detail="Post introuvable.")
        already_liked = conn.execute(
            "SELECT 1 FROM likes WHERE post_id = ? AND user_id = ?", (post_id, user["id"])
        ).fetchone() is not None

        if already_liked:
            # Déjà liké : rien à faire, pas de nouvelle notification.
            return {"ok": True}

        conn.execute(
            "INSERT OR IGNORE INTO likes (post_id, user_id, created_at) VALUES (?, ?, ?)",
            (post_id, user["id"], now()),
        )
        conn.commit()

        notification = None
        if post["user_id"] != user["id"]:
            notification = _create_notification(
                conn, post["user_id"], "like", user["username"], post_id=post_id
            )

    if notification:
        await manager.send_to_user(post["user_id"], {"type": "notification", "notification": notification})
    return {"ok": True}


@app.delete("/api/posts/{post_id}/like")
def unlike_post(post_id: int, user=Depends(get_current_user)):
    """Retire le like de l'utilisateur courant sur un post (sans erreur si
    le post n'était pas liké)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM likes WHERE post_id = ? AND user_id = ?", (post_id, user["id"]))
        conn.commit()
        return {"ok": True}


@app.patch("/api/posts/{post_id}")
async def edit_post(post_id: int, body: PostEditIn, user=Depends(get_current_user)):
    """Modifie le texte d'un post dont l'utilisateur courant est l'auteur.

    L'index de recherche est mis à jour dans la même opération : on retire
    l'ancien texte (`index_remove` a besoin du texte d'origine pour savoir
    quels mots désindexer) avant d'indexer le nouveau, sinon une recherche
    sur un mot supprimé continuerait de remonter le post. Les hashtags sont
    réindexés de la même façon (retrait puis ré-ajout). Les mentions ajoutées
    par cette modification déclenchent une nouvelle notification — mais
    seulement les nouvelles : si un pseudo était déjà mentionné avant
    l'édition, on ne renotifie pas (voir le calcul de `new_mentions`).

    L'image jointe n'est pas modifiable : seul le texte l'est.
    """
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Post introuvable.")
        if row["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Tu ne peux modifier que tes propres posts.")

        conn.execute("UPDATE posts SET content = ? WHERE id = ?", (body.content, post_id))
        conn.commit()
        searchengine.index_remove(post_id, row["content"])
        searchengine.index_add(post_id, body.content)
        _deindex_hashtags(conn, post_id)
        _index_hashtags(conn, post_id, body.content)
        conn.commit()

        new_mentions = _extract_mentions(body.content) - _extract_mentions(row["content"])
        mention_notifications = []
        if new_mentions:
            mention_notifications = _create_mention_notifications(
                conn, body.content, post_id, user["id"], user["username"], only_usernames=new_mentions
            )

        updated = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        serialized = _serialize_post(conn, updated, user["id"])

    for target_id, notification in mention_notifications:
        await manager.send_to_user(target_id, {"type": "notification", "notification": notification})
    return serialized


@app.delete("/api/posts/{post_id}")
def delete_post(post_id: int, user=Depends(get_current_user)):
    """Supprime un post dont l'utilisateur courant est l'auteur, ainsi que
    tout ce qui en dépend : likes, commentaires, hashtags, image jointe et
    entrées dans l'index de recherche.

    Les likes et commentaires doivent partir en premier : ils référencent
    `posts(id)` par clé étrangère (activée dans database.py) et la
    suppression du post échouerait sinon. Le fichier image est effacé du
    disque pour ne pas laisser d'orphelins dans backend/uploads/.
    """
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Post introuvable.")
        if row["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Tu ne peux supprimer que tes propres posts.")

        conn.execute("DELETE FROM likes WHERE post_id = ?", (post_id,))
        conn.execute("DELETE FROM comments WHERE post_id = ?", (post_id,))
        conn.execute("DELETE FROM notifications WHERE post_id = ?", (post_id,))
        _deindex_hashtags(conn, post_id)
        conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        conn.commit()

    searchengine.index_remove(post_id, row["content"])

    image_path = row["image_path"] if "image_path" in row.keys() else None
    if image_path:
        try:
            os.remove(os.path.join(UPLOAD_DIR, image_path))
        except FileNotFoundError:
            pass
    return {"ok": True}


@app.post("/api/posts/{post_id}/comments")
async def add_comment(post_id: int, body: CommentIn, user=Depends(get_current_user)):
    """Ajoute un commentaire de l'utilisateur courant sur un post existant.

    Une mention (@pseudo) valide dans le commentaire déclenche une
    notification pour la personne visée, au même titre qu'une mention dans
    un post (voir _create_mention_notifications) — `post_id` sert de
    référence pour retrouver le contexte (le post commenté), le commentaire
    lui-même n'a pas d'identité propre dans les notifications.
    """
    with get_conn() as conn:
        post = conn.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone()
        if not post:
            raise HTTPException(status_code=404, detail="Post introuvable.")
        conn.execute(
            "INSERT INTO comments (post_id, user_id, content, created_at) VALUES (?, ?, ?, ?)",
            (post_id, user["id"], body.content, now()),
        )
        conn.commit()
        mention_notifications = _create_mention_notifications(
            conn, body.content, post_id, user["id"], user["username"]
        )

    for target_id, notification in mention_notifications:
        await manager.send_to_user(target_id, {"type": "notification", "notification": notification})
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
            {
                "id": r["id"],
                "author": r["username"],
                "content": r["content"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]


@app.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: int, user=Depends(get_current_user)):
    """Supprime un commentaire. Autorisé à son auteur, mais aussi à l'auteur
    du post commenté (modération de sa propre publication)."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT c.id, c.user_id, p.user_id AS post_author_id
            FROM comments c JOIN posts p ON p.id = c.post_id
            WHERE c.id = ?
            """,
            (comment_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Commentaire introuvable.")
        if user["id"] not in (row["user_id"], row["post_author_id"]):
            raise HTTPException(
                status_code=403,
                detail="Seul l'auteur du commentaire ou celui du post peut le supprimer.",
            )
        conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
        conn.commit()
        return {"ok": True}


# ---------- Follow ----------

@app.post("/api/follow/{username}")
async def follow_user(username: str, user=Depends(get_current_user)):
    """Fait suivre `username` par l'utilisateur courant.

    Refuse de se suivre soi-même (400). Idempotent comme les likes : suivre
    deux fois quelqu'un ne provoque pas d'erreur ni de notification en double.
    """
    with get_conn() as conn:
        target = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
        if target["id"] == user["id"]:
            raise HTTPException(status_code=400, detail="Tu ne peux pas te suivre toi-même.")
        already_following = conn.execute(
            "SELECT 1 FROM follows WHERE follower_id = ? AND followed_id = ?", (user["id"], target["id"])
        ).fetchone() is not None
        if already_following:
            return {"ok": True}

        conn.execute(
            "INSERT OR IGNORE INTO follows (follower_id, followed_id, created_at) VALUES (?, ?, ?)",
            (user["id"], target["id"], now()),
        )
        conn.commit()
        notification = _create_notification(conn, target["id"], "follow", user["username"])
        target_id = target["id"]

    await manager.send_to_user(target_id, {"type": "notification", "notification": notification})
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
async def send_message(body: MessageIn, user=Depends(get_current_user)):
    """Envoie un message privé de l'utilisateur courant vers `body.to`.

    Pousse aussi le message en temps réel (WebSocket) si le destinataire est
    connecté ; sinon il le verra en rouvrant la conversation plus tard via
    GET /api/messages/{username}, comme d'habitude. Une notification de type
    'message' est en plus enregistrée en base (voir _create_notification) —
    utile si le destinataire consulte GET /api/notifications après avoir été
    hors ligne. On ne pousse volontairement pas de second événement
    WebSocket pour cette notification : l'événement "message" ci-dessous
    sert déjà de signal temps réel, un second ping serait redondant.
    """
    sent_at = now()
    with get_conn() as conn:
        target = conn.execute("SELECT id FROM users WHERE username = ?", (body.to,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Destinataire introuvable.")
        conn.execute(
            "INSERT INTO messages (sender_id, receiver_id, content, created_at) VALUES (?, ?, ?, ?)",
            (user["id"], target["id"], body.content, sent_at),
        )
        conn.commit()
        target_id = target["id"]
        snippet = body.content[:80] + ("…" if len(body.content) > 80 else "")
        _create_notification(conn, target_id, "message", user["username"], content=snippet)

    await manager.send_to_user(
        target_id,
        {"type": "message", "from": user["username"], "content": body.content, "created_at": sent_at},
    )
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
def search(q: str, limit: int = 20, offset: int = 0, user=Depends(get_current_user)):
    """Recherche full-text paginée dans les posts via l'extension C (searchengine).

    `searchengine.search` trie par score (nombre d'occurrences des mots
    recherchés), pas par id ni par date : un curseur façon `feed` n'a donc
    pas de sens ici, un simple offset/limit classique convient.

    L'extension C ne connaît pas la notion d'offset (voir `c_ext/searchmodule.c`
    : `search(query, limit)` renvoie déjà ses `limit` meilleurs résultats).
    On lui demande donc `offset + limit + 1` résultats, puis on découpe la
    tranche voulue côté Python : le "+1" sert uniquement à savoir s'il reste
    des résultats au-delà de cette page (`has_more`), sans requête séparée.

    Réponse : {"results": [...], "has_more": bool, "next_offset": int|null}
    """
    limit = max(1, min(limit, 50))
    offset = max(0, offset)

    raw_results = searchengine.search(q, offset + limit + 1)
    has_more = len(raw_results) > offset + limit
    page = raw_results[offset:offset + limit]

    if not page:
        return {"results": [], "has_more": False, "next_offset": None}

    with get_conn() as conn:
        out = []
        for doc_id, score in page:
            row = conn.execute("SELECT * FROM posts WHERE id = ?", (doc_id,)).fetchone()
            if row:
                serialized = _serialize_post(conn, row, user["id"])
                serialized["score"] = score
                out.append(serialized)
        return {
            "results": out,
            "has_more": has_more,
            "next_offset": offset + limit if has_more else None,
        }


# ---------- Frontend statique (interface terminal) ----------
# Le backend sert aussi le frontend : pas besoin d'un serveur web séparé en
# développement. `/static/...` expose les fichiers bruts (utile si on ajoute
# des CSS/JS séparés plus tard), et `/` renvoie directement index.html.

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.get("/")
def serve_index():
    """Sert la page terminal (frontend/index.html) à la racine du site."""
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
