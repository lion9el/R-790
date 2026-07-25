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
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError

from database import init_db, get_conn
from auth import hash_password, verify_password, create_token, decode_token
from auth import get_current_user as _decode_and_verify_signature
from realtime import manager
from ratelimit import rate_limit

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
            searchengine.index_add(row["id"], row["content"])
    yield


app = FastAPI(title="Réseau social terminal", lifespan=lifespan)

# ---------- Stockage des images jointes aux posts ----------
# Les fichiers uploadés sont stockés tels quels sur disque (pas en base),
# dans backend/uploads/. Ce dossier est hors Git (voir .gitignore) : le
# contenu uploadé est propre à chaque environnement, comme la base SQLite.
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Seuls ces types MIME sont acceptés. Le Content-Type déclaré par le client
# sert de filtre initial ET détermine l'extension du fichier stocké, mais
# n'est PAS la seule vérification : ce header est entièrement falsifiable
# (n'importe qui peut envoyer n'importe quels octets avec l'en-tête de son
# choix via curl/Postman, pas besoin d'un navigateur). Le contenu réel est
# donc revérifié plus bas avec Pillow (magic bytes), voir _save_uploaded_image.
ALLOWED_IMAGE_TYPES = {
    "image/png": (".png", "PNG"),
    "image/jpeg": (".jpg", "JPEG"),
    "image/gif": (".gif", "GIF"),
    "image/webp": (".webp", "WEBP"),
}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 Mo
UPLOAD_CHUNK_SIZE = 64 * 1024  # taille des blocs lus depuis le flux réseau

# Délai laissé au client pour envoyer son message d'authentification une
# fois le WebSocket accepté (voir websocket_endpoint). Assez court pour ne
# pas laisser une connexion non authentifiée traîner indéfiniment, assez
# long pour tolérer une connexion réseau lente.
AUTH_TIMEOUT_SECONDS = 5


async def _read_upload_with_limit(upload: UploadFile, max_size: int) -> bytes:
    """Lit un fichier uploadé par blocs, en abandonnant dès que `max_size`
    est dépassé, plutôt que de lire tout le corps de la requête en mémoire
    avant de vérifier sa taille. Sans ça, un client (pas forcément un
    navigateur — curl, script) pourrait envoyer un fichier de plusieurs Go
    et faire lire l'intégralité en RAM avant que la vérification de taille
    n'ait la moindre chance de s'exécuter.
    """
    chunks = []
    total = 0
    while True:
        chunk = await upload.read(UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise HTTPException(status_code=400, detail="Image trop volumineuse (5 Mo maximum).")
        chunks.append(chunk)
    return b"".join(chunks)


async def _save_uploaded_image(image: UploadFile) -> str:
    """Valide et enregistre une image uploadée, renvoie son nom de fichier.

    - Le type déclaré est vérifié contre une liste blanche (pas de .exe,
      .php, etc. déguisés en image via l'en-tête Content-Type).
    - Le CONTENU est ensuite revérifié avec Pillow (`Image.open(...).verify()`
      puis contrôle du format détecté) : un fichier dont les octets ne sont
      pas réellement une image du format annoncé est rejeté, même si son
      Content-Type déclaré était dans la liste blanche. Sans cette étape,
      n'importe quel fichier passerait la validation en se contentant de
      mentir sur son Content-Type.
    - Le nom de fichier final est généré aléatoirement (UUID) : on ignore
      totalement le nom envoyé par le client, ce qui élimine tout risque de
      "path traversal" (ex: un nom de fichier contenant "../../").
    - La taille est plafonnée à MAX_IMAGE_SIZE, vérifiée au fil de la
      lecture (voir _read_upload_with_limit) plutôt qu'après coup.
    """
    if image.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Format d'image non supporté (formats acceptés : PNG, JPEG, GIF, WEBP).",
        )
    extension, expected_format = ALLOWED_IMAGE_TYPES[image.content_type]

    data = await _read_upload_with_limit(image, MAX_IMAGE_SIZE)
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Fichier image vide.")

    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()  # lève une exception si ce ne sont pas des octets d'image valides
        # verify() consomme/invalide l'objet ouvert : il faut rouvrir pour
        # pouvoir encore lire son format détecté juste après.
        with Image.open(io.BytesIO(data)) as reopened:
            actual_format = reopened.format
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(status_code=400, detail="Le fichier envoyé n'est pas une image valide.")

    if actual_format != expected_format:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Le contenu du fichier ({actual_format or 'format inconnu'}) ne correspond pas "
                f"au type déclaré ({expected_format})."
            ),
        )

    filename = uuid.uuid4().hex + extension
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

@app.post("/api/register", dependencies=[Depends(rate_limit(5, 60))])
def register(body: RegisterIn):
    """Crée un nouveau compte utilisateur et renvoie directement un jeton de
    session (l'utilisateur est connecté automatiquement après inscription).

    Échoue avec 400 si le nom d'utilisateur est déjà pris.

    Limité à 5 inscriptions par minute et par IP (voir ratelimit.py) : évite
    la création massive et automatisée de comptes.
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


@app.post("/api/login", dependencies=[Depends(rate_limit(10, 60))])
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
        row = conn.execute("SELECT * FROM users WHERE username = ?", (body.username,)).fetchone()
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
