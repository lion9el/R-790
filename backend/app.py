"""
API du réseau social. Sert aussi le frontend terminal (fichiers statiques).

Lancer avec : uvicorn app:app --reload --port 8000
"""
import sys
import os
import re
import datetime
import uuid
import asyncio
import json
import io
import time
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "c_ext"))

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, Form, File, UploadFile, Header
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError

from database import init_db, get_conn
from auth import hash_password, verify_password, create_token, decode_token
from auth import get_current_user as _decode_and_verify_signature
from realtime import manager
from ratelimit import rate_limit
from config import settings
from services.common import (
    now,
    normalize_for_search as _normalize_for_search,
    extract_hashtags as _extract_hashtags,
    extract_mentions as _extract_mentions,
    index_hashtags as _index_hashtags,
    deindex_hashtags as _deindex_hashtags,
    create_notification as _create_notification,
    create_mention_notifications as _create_mention_notifications,
    serialize_posts as _serialize_posts,
    serialize_post as _serialize_post,
)
from services.images import save_uploaded_image as _save_uploaded_image, UPLOAD_DIR
from services import account as account_service
from services import moderation as moderation_service
from services import backup as backup_service
from services import ops as ops_service
from schemas import RegisterIn, LoginIn, ProfileIn, PostEditIn, CommentIn, MessageIn

try:
    import searchengine
except ImportError:
    raise RuntimeError(
        "Le module 'searchengine' (extension C) est introuvable. "
        "Compile-le avec : cd c_ext && python3 setup.py build_ext --inplace"
    )


def get_current_user(authorization: str = Header(default=None)) -> dict:
    """Dépendance FastAPI utilisée par (quasiment) toutes les routes protégées.

    Enveloppe `auth.get_current_user` (renommé `_decode_and_verify_signature`
    à l'import) : celle-ci vérifie la signature et l'expiration du jeton,
    purement cryptographique, sans accès à la base. La révocation, elle,
    nécessite de lire la base de données (un jeton individuellement révoqué
    via /api/logout, ou une révocation globale via /api/logout/all) — cette
    partie-là vit ici plutôt que dans auth.py, qui reste volontairement sans
    dépendance à `database` (voir son docstring).
    """
    user = _decode_and_verify_signature(authorization)
    _reject_if_revoked(user)
    return user


def _reject_if_revoked(user: dict) -> None:
    """Lève une HTTPException 401 si le jeton représenté par `user` (dict
    avec au moins "id", "iat", "jti", voir auth.get_current_user /
    decode_token) a été révoqué, individuellement ou globalement.

    Deux mécanismes indépendants, voir schema.sql pour le détail de chacun :
    - `users.tokens_valid_after` : révocation GLOBALE (tous les jetons émis
      avant cette date pour cet utilisateur sont invalides), comparée en
      MILLISECONDES (`iat_ms`, pas `iat`) pour éviter les collisions entre
      deux jetons émis pendant la même seconde. Comparaison `<=`
      volontaire : un jeton émis la même milliseconde que l'appel à
      /api/logout/all (y compris celui qui a servi à cet appel) est
      considéré révoqué aussi — "déconnexion partout" doit aussi déconnecter
      l'appareil courant, pas seulement "les autres".
    - `revoked_tokens` : révocation INDIVIDUELLE d'UN jeton précis (une
      "session"), via son `jti`. Utilisé par /api/logout (déconnexion d'un
      seul appareil).
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT tokens_valid_after FROM users WHERE id = ?", (user["id"],)
        ).fetchone()
        if row and row["tokens_valid_after"] is not None and user["iat_ms"] <= row["tokens_valid_after"]:
            raise HTTPException(
                status_code=401,
                detail="Session invalidée (déconnexion de tous les appareils). Reconnecte-toi.",
            )

        revoked = conn.execute(
            "SELECT 1 FROM revoked_tokens WHERE jti = ?", (user["jti"],)
        ).fetchone()
        if revoked:
            raise HTTPException(status_code=401, detail="Session révoquée (déconnexion). Reconnecte-toi.")


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
            searchengine.index_add(row["id"], _normalize_for_search(row["content"]))
    # Backups périodiques (DB + uploads)
    try:
        backup_service.start_background()
    except Exception as e:
        print(f"[backup] non démarré: {e}")
    yield


app = FastAPI(title="Réseau social terminal", lifespan=lifespan)

# ---------- En-têtes de sécurité HTTP ----------
# Dernier point ouvert de l'audit sécurité v1.4. Ces en-têtes réduisent
# fortement la surface d'attaque côté navigateur (clickjacking, XSS, MIME
# sniffing, etc.). Content-Security-Policy est volontairement stricte :
# le frontend est une page unique sans CDN externe ni inline scripts
# dynamiques non contrôlés.
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # CSP : scripts et styles uniquement depuis la même origine.
        # 'unsafe-inline' est nécessaire pour le style et le JS embarqués
        # dans index.html (page unique sans bundler). Améliorable plus tard
        # en externalisant le JS/CSS.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        # HSTS uniquement utile derrière HTTPS ; on le met quand même pour
        # les déploiements qui terminent TLS devant (nginx, Cloudflare…).
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# ---------- Stockage des images jointes aux posts ----------
# Les fichiers uploadés sont stockés tels quels sur disque (pas en base),
# dans backend/uploads/. Ce dossier est hors Git (voir .gitignore) : le
# contenu uploadé est propre à chaque environnement, comme la base SQLite.

AUTH_TIMEOUT_SECONDS = settings.auth_timeout_seconds





# ---------- Temps réel (WebSocket) ----------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Connexion temps réel : pousse au navigateur les nouveaux messages
    privés reçus et les nouveaux posts des comptes suivis, sans qu'il soit
    nécessaire de retaper une commande pour rafraîchir.

    Authentification : le jeton JWT n'est PAS passé en paramètre de l'URL
    (ws://.../ws?token=...) — un serveur ASGI comme uvicorn logue par défaut
    le chemin complet AVEC sa query string dans ses access logs, ce qui
    ferait fuiter le jeton de session en clair dans ces logs (et ceux de
    tout reverse-proxy/CDN placé devant). À la place :
    1. La connexion est acceptée sans authentification.
    2. Le client DOIT envoyer, comme tout premier message, un JSON
       `{"token": "<jwt>"}`.
    3. Si ce premier message est absent, invalide, ou n'arrive pas dans les
       `AUTH_TIMEOUT_SECONDS`, la connexion est fermée sans avoir jamais été
       enregistrée dans le ConnectionManager.
    Ce message-là ne transite que sur le canal WebSocket déjà établi
    (jamais dans une URL), donc jamais dans un log d'accès HTTP classique.
    """
    await websocket.accept()

    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
        auth_message = json.loads(raw)
        token = auth_message.get("token")
        if not token:
            raise ValueError("champ 'token' manquant")
        payload = decode_token(token)
        # Même vérification de révocation que get_current_user (routes
        # HTTP) : sans elle, un jeton révoqué via /api/logout resterait
        # utilisable pour ouvrir une connexion temps réel indéfiniment.
        _reject_if_revoked({"id": int(payload["sub"]), "iat_ms": payload["iat_ms"], "jti": payload["jti"]})
    except (asyncio.TimeoutError, ValueError, KeyError, TypeError):
        await websocket.close(code=4401)
        return
    except HTTPException:
        # decode_token() ou _reject_if_revoked() lève une HTTPException
        # (jeton expiré/invalide/révoqué) : pas de sens ici (pas de réponse
        # HTTP possible sur un WebSocket), donc on la traduit en simple
        # fermeture de connexion.
        await websocket.close(code=4401)
        return

    user_id = int(payload["sub"])
    await manager.connect(user_id, websocket)
    try:
        while True:
            # On ne fait rien du contenu reçu après l'authentification :
            # cette boucle sert uniquement à détecter la déconnexion
            # (fermeture d'onglet, perte réseau).
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

@app.post("/api/register", dependencies=[Depends(rate_limit(*settings.rate_limit_register))])
def register(body: RegisterIn):
    """Crée un nouveau compte utilisateur et renvoie directement un jeton de
    session (l'utilisateur est connecté automatiquement après inscription).

    Échoue avec 400 si le nom d'utilisateur est déjà pris.

    Limité à 5 inscriptions par minute et par IP (voir ratelimit.py) : évite
    la création massive et automatisée de comptes.
    """
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (body.username,)).fetchone()
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


@app.post("/api/login", dependencies=[Depends(rate_limit(*settings.rate_limit_login))])
def login(body: LoginIn):
    """Vérifie les identifiants et renvoie un jeton JWT valable 7 jours.

    Échoue avec 401 (identifiants incorrects) sans préciser si c'est le nom
    d'utilisateur ou le mot de passe qui est faux, pour ne pas aider à
    deviner les comptes existants.

    Limité à 10 tentatives par minute et par IP (voir ratelimit.py) : ralentit
    une attaque par force brute sur les mots de passe. Plus permissif que
    /api/register (10 contre 5) pour tolérer les fautes de frappe d'un
    utilisateur légitime sans le bloquer trop vite.
    """
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (body.username,)).fetchone()
    if not row or not verify_password(body.password, row["salt"], row["password_hash"]):
        raise HTTPException(status_code=401, detail="Identifiants incorrects.")
    token = create_token(row["id"], row["username"])
    return {"token": token, "username": row["username"]}


# ---------- Révocation de jeton ----------
# Un JWT est valable, par construction, jusqu'à sa date d'expiration (7
# jours) même si l'utilisateur clique sur "déconnexion" : sans mécanisme
# explicite, un jeton volé (XSS, appareil partagé oublié, etc.) reste
# utilisable jusqu'à expiration naturelle, quoi qu'il arrive côté client.
# Deux routes complémentaires :
# - /api/logout révoque UN jeton précis (celui utilisé pour l'appel) : la
#   session courante uniquement, les autres appareils connectés restent
#   actifs.
# - /api/logout/all révoque TOUS les jetons émis jusqu'à maintenant pour ce
#   compte : utile si on soupçonne qu'un jeton a fuité et qu'on ne sait pas
#   lequel précisément.

@app.post("/api/logout")
def logout(user=Depends(get_current_user)):
    """Révoque le jeton actuellement utilisé (cette session précise).

    Le jeton reste enregistré dans `revoked_tokens` jusqu'à SA date
    d'expiration naturelle (`user["exp"]`) — inutile de le garder plus
    longtemps : passé cette date, la vérification normale de signature
    l'aurait de toute façon rejeté. Cette même requête purge au passage les
    entrées déjà expirées d'autres révocations passées, pour que la table ne
    grossisse pas indéfiniment sans jamais être nettoyée.
    """
    expires_at = datetime.datetime.fromtimestamp(
        user["exp"], tz=datetime.timezone.utc
    ).replace(tzinfo=None).isoformat()

    with get_conn() as conn:
        conn.execute("DELETE FROM revoked_tokens WHERE expires_at < ?", (now(),))
        conn.execute(
            "INSERT OR IGNORE INTO revoked_tokens (jti, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (user["jti"], user["id"], expires_at, now()),
        )
        conn.commit()
    return {"ok": True}


@app.post("/api/logout/all")
def logout_all(user=Depends(get_current_user)):
    """Révoque TOUS les jetons émis jusqu'à maintenant pour ce compte,
    y compris celui utilisé pour cet appel (voir _reject_if_revoked : la
    comparaison est `iat <= tokens_valid_after`, pas `<`) — après cet appel,
    il faut se reconnecter, même sur l'appareil d'où on vient de l'appeler.

    Implémenté par une seule colonne (`users.tokens_valid_after`) plutôt que
    par une liste de tous les jetons actifs à révoquer un par un : on ne
    garde jamais trace des jetons valides (seulement, ailleurs, des jetons
    individuellement révoqués), donc il serait impossible d'énumérer "tous
    les jetons actifs" pour les invalider un par un. Repousser la date de
    coupure a le même effet, sans avoir besoin de connaître les jetons eux-mêmes.
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET tokens_valid_after = ? WHERE id = ?",
            (int(time.time() * 1000), user["id"]),
        )
        conn.commit()
    return {"ok": True}


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



# ---------- Posts / fil d'actualité ----------



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
        searchengine.index_add(post_id, _normalize_for_search(content))
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
              AND p.user_id NOT IN (
                    SELECT blocked_id FROM blocks WHERE blocker_id = ?
                    UNION
                    SELECT blocker_id FROM blocks WHERE blocked_id = ?
              )
              AND (? IS NULL OR p.id < ?)
            ORDER BY p.id DESC
            LIMIT ?
            """,
            (user["id"], user["id"], user["id"], user["id"], before, before, limit + 1),
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
        searchengine.index_remove(post_id, _normalize_for_search(row["content"]))
        searchengine.index_add(post_id, _normalize_for_search(body.content))
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
        conn.execute("DELETE FROM bookmarks WHERE post_id = ?", (post_id,))
        _deindex_hashtags(conn, post_id)
        conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        conn.commit()

    searchengine.index_remove(post_id, _normalize_for_search(row["content"]))

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

    - Notifie l'auteur du post (sauf auto-commentaire) via une notification
      de type `comment` (persistée + push temps réel).
    - Une mention (@pseudo) valide dans le commentaire déclenche en plus
      une notification pour la personne visée (voir _create_mention_notifications).
    """
    with get_conn() as conn:
        post = conn.execute("SELECT id, user_id FROM posts WHERE id = ?", (post_id,)).fetchone()
        if not post:
            raise HTTPException(status_code=404, detail="Post introuvable.")
        post_author_id = post["user_id"]
        conn.execute(
            "INSERT INTO comments (post_id, user_id, content, created_at) VALUES (?, ?, ?, ?)",
            (post_id, user["id"], body.content, now()),
        )
        conn.commit()

        comment_notification = None
        if post_author_id != user["id"]:
            preview = body.content[:120] + ("…" if len(body.content) > 120 else "")
            comment_notification = _create_notification(
                conn, post_author_id, "comment", user["username"],
                post_id=post_id, content=preview,
            )

        mention_notifications = _create_mention_notifications(
            conn, body.content, post_id, user["id"], user["username"]
        )

    if comment_notification:
        await manager.send_to_user(
            post_author_id,
            {"type": "notification", "notification": comment_notification},
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
    """Follow direct ou demande si compte privé. Respecte les blocages."""
    result = moderation_service.request_follow(user["id"], username)
    return result


@app.delete("/api/follow/{username}")
def unfollow_user(username: str, user=Depends(get_current_user)):
    """Fait arrêter à l'utilisateur courant de suivre `username`."""
    with get_conn() as conn:
        target = conn.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (username,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
        conn.execute(
            "DELETE FROM follows WHERE follower_id = ? AND followed_id = ?", (user["id"], target["id"])
        )
        conn.commit()
        return {"ok": True}


@app.get("/api/users/{username}")
def get_user_profile(username: str, user=Depends(get_current_user)):
    """Profil public enrichi. Respecte blocages et comptes privés."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, display_name, bio, avatar_path, is_private, created_at FROM users WHERE LOWER(username) = LOWER(?)",
            (username,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
        if moderation_service.is_blocked(conn, user["id"], row["id"]):
            raise HTTPException(status_code=403, detail="Profil inaccessible (blocage).")
        is_private = bool(row["is_private"]) if row["is_private"] is not None else False
        is_self = row["id"] == user["id"]
        is_follower = conn.execute(
            "SELECT 1 FROM follows WHERE follower_id = ? AND followed_id = ?",
            (user["id"], row["id"]),
        ).fetchone() is not None
        if is_private and not is_self and not is_follower:
            return {
                "username": row["username"],
                "display_name": row["display_name"],
                "is_private": True,
                "avatar_url": f"/uploads/{row['avatar_path']}" if row["avatar_path"] else None,
                "detail": "Compte privé — demande un abonnement pour voir le contenu.",
            }
        followers = conn.execute("SELECT COUNT(*) c FROM follows WHERE followed_id = ?", (row["id"],)).fetchone()["c"]
        following = conn.execute("SELECT COUNT(*) c FROM follows WHERE follower_id = ?", (row["id"],)).fetchone()["c"]
        post_count = conn.execute("SELECT COUNT(*) c FROM posts WHERE user_id = ?", (row["id"],)).fetchone()["c"]
        return {
            "username": row["username"],
            "display_name": row["display_name"],
            "bio": row["bio"] or "",
            "avatar_url": f"/uploads/{row['avatar_path']}" if row["avatar_path"] else None,
            "is_private": is_private,
            "followers": followers,
            "following": following,
            "posts": post_count,
            "created_at": row["created_at"],
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
        target = conn.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (body.to,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Destinataire introuvable.")
        if moderation_service.is_blocked(conn, user["id"], target["id"]):
            raise HTTPException(status_code=403, detail="Impossible d'envoyer un message (blocage).")
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
        target = conn.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (username,)).fetchone()
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

    raw_results = searchengine.search(_normalize_for_search(q), offset + limit + 1)
    has_more = len(raw_results) > offset + limit
    page = raw_results[offset:offset + limit]

    if not page:
        return {"results": [], "has_more": False, "next_offset": None}

    with get_conn() as conn:
        out = []
        for doc_id, score in page:
            row = conn.execute("SELECT * FROM posts WHERE id = ?", (doc_id,)).fetchone()
            if not row:
                continue
            if moderation_service.is_blocked(conn, user["id"], row["user_id"]):
                continue
            serialized = _serialize_post(conn, row, user["id"])
            serialized["score"] = score
            out.append(serialized)
        return {
            "results": out,
            "has_more": has_more,
            "next_offset": offset + limit if has_more else None,
        }



# ---------- Compte : mot de passe, suppression, profil, export ----------

class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=100)
    new_password: str = Field(min_length=6, max_length=100)


class ResetRequestIn(BaseModel):
    username: str


class ResetConfirmIn(BaseModel):
    token: str
    new_password: str = Field(min_length=6, max_length=100)


class DeleteAccountIn(BaseModel):
    password: str


class ReportIn(BaseModel):
    target_type: str  # user | post | comment
    target_id: int
    reason: str = Field(min_length=3, max_length=500)


class ProfileUpdateIn(BaseModel):
    bio: str | None = Field(default=None, max_length=200)
    display_name: str | None = Field(default=None, max_length=50)
    is_private: bool | None = None


@app.post("/api/account/change-password")
def change_password(body: ChangePasswordIn, user=Depends(get_current_user)):
    account_service.change_password(user["id"], body.current_password, body.new_password)
    return {"ok": True, "detail": "Mot de passe changé. Reconnecte-toi."}


@app.post("/api/account/reset-request", dependencies=[Depends(rate_limit(3, 300))])
def reset_request(body: ResetRequestIn):
    """Demande de reset. Le token n'est renvoyé dans le JSON que si
    SOCIALNET_EXPOSE_RESET_TOKEN=1 (démo terminal). En prod : email uniquement.
    """
    import os
    token = account_service.request_password_reset(body.username)
    # Toujours la même réponse publique (ne révèle pas si le compte existe)
    expose = os.environ.get("SOCIALNET_EXPOSE_RESET_TOKEN", "").strip().lower() in ("1", "true", "yes")
    if expose and token:
        return {
            "ok": True,
            "token": token,
            "detail": "Jeton valable 1h (mode démo SOCIALNET_EXPOSE_RESET_TOKEN).",
        }
    return {"ok": True, "detail": "Si le compte existe, un jeton de réinitialisation a été créé."}


@app.post("/api/account/reset-confirm", dependencies=[Depends(rate_limit(5, 300))])
def reset_confirm(body: ResetConfirmIn):
    account_service.reset_password(body.token, body.new_password)
    return {"ok": True, "detail": "Mot de passe réinitialisé. Connecte-toi."}


@app.post("/api/account/delete")
def delete_account(body: DeleteAccountIn, user=Depends(get_current_user)):
    account_service.delete_account(user["id"], body.password)
    return {"ok": True, "detail": "Compte supprimé définitivement."}


@app.get("/api/account/export")
def export_account(user=Depends(get_current_user)):
    return account_service.export_user_data(user["id"])


@app.patch("/api/me/profile")
async def update_full_profile(
    bio: str | None = Form(None),
    display_name: str | None = Form(None),
    is_private: str | None = Form(None),  # "true"/"false" depuis FormData
    avatar: UploadFile | None = File(None),
    user=Depends(get_current_user),
):
    avatar_path = None
    if avatar is not None and avatar.filename:
        avatar_path = await _save_uploaded_image(avatar)
    private_flag = None
    if is_private is not None and str(is_private).strip() != "":
        private_flag = str(is_private).strip().lower() in ("1", "true", "yes", "on")
    return account_service.update_profile_fields(
        user["id"], bio=bio, display_name=display_name, is_private=private_flag, avatar_path=avatar_path
    )


# ---------- Modération : blocage, signalement, bookmarks, follow privé ----------

@app.post("/api/block/{username}")
def block_user(username: str, user=Depends(get_current_user)):
    moderation_service.block_user(user["id"], username)
    return {"ok": True}


@app.delete("/api/block/{username}")
def unblock_user(username: str, user=Depends(get_current_user)):
    moderation_service.unblock_user(user["id"], username)
    return {"ok": True}


@app.get("/api/blocks")
def list_blocks(user=Depends(get_current_user)):
    return moderation_service.list_blocked(user["id"])


@app.post("/api/report")
def report(body: ReportIn, user=Depends(get_current_user)):
    return moderation_service.report_content(user["id"], body.target_type, body.target_id, body.reason)


@app.post("/api/posts/{post_id}/bookmark")
def bookmark(post_id: int, user=Depends(get_current_user)):
    moderation_service.bookmark_post(user["id"], post_id)
    return {"ok": True}


@app.delete("/api/posts/{post_id}/bookmark")
def unbookmark(post_id: int, user=Depends(get_current_user)):
    moderation_service.unbookmark_post(user["id"], post_id)
    return {"ok": True}


@app.get("/api/bookmarks")
def bookmarks(limit: int = 30, user=Depends(get_current_user)):
    from database import get_conn
    ids = moderation_service.list_bookmarks(user["id"], limit)
    if not ids:
        return []
    with get_conn() as conn:
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(f"SELECT * FROM posts WHERE id IN ({placeholders})", ids).fetchall()
        by_id = {r["id"]: r for r in rows}
        ordered = [by_id[i] for i in ids if i in by_id]
        return _serialize_posts(conn, ordered, user["id"])


@app.post("/api/follow-requests/{username}/accept")
def accept_follow(username: str, user=Depends(get_current_user)):
    moderation_service.respond_follow_request(user["id"], username, accept=True)
    return {"ok": True}


@app.post("/api/follow-requests/{username}/reject")
def reject_follow(username: str, user=Depends(get_current_user)):
    moderation_service.respond_follow_request(user["id"], username, accept=False)
    return {"ok": True}


@app.get("/api/follow-requests")
def list_follow_requests(user=Depends(get_current_user)):
    return moderation_service.list_follow_requests(user["id"])



# ---------- Ops / dashboard / backups ----------

@app.get("/api/ops/status")
def ops_status():
    """État API, DB, chronique, bots, backups (JSON)."""
    return ops_service.status()


@app.post("/api/ops/backup")
def ops_backup(
    user=Depends(get_current_user),
    x_ops_key: str | None = Header(default=None, alias="X-Ops-Key"),
):
    """Snapshot immédiat. Si SOCIALNET_OPS_KEY est défini, exige l'en-tête X-Ops-Key."""
    import os
    required = os.environ.get("SOCIALNET_OPS_KEY", "").strip()
    if required and (x_ops_key or "") != required:
        raise HTTPException(status_code=403, detail="Clé ops invalide ou manquante (X-Ops-Key).")
    return backup_service.run_backup()


@app.get("/ops")
def ops_dashboard():
    """Dashboard HTML unique (auto-refresh)."""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(ops_service.status_html())


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
