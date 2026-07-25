"""Authentification : hachage de mot de passe (PBKDF2) et jetons JWT."""
import hashlib
import os
import secrets
import sys
import time
import jwt
from fastapi import Header, HTTPException

# Charge backend/.env s'il existe, pour que SOCIALNET_SECRET (voir
# .env.example) soit réellement pris en compte. Avant ce correctif, .env
# n'était lu par personne : le fichier ne servait à rien, et la valeur par
# défaut ci-dessous (un secret FAIBLE, car visible dans le code source
# public) était utilisée silencieusement même quand un développeur avait
# suivi les instructions et rempli son .env.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass  # python-dotenv absent : on retombe sur les vraies variables d'environnement.

_PLACEHOLDER_SECRET = "change-moi-avant-la-production"
_env_secret = os.environ.get("SOCIALNET_SECRET")

if _env_secret and _env_secret != _PLACEHOLDER_SECRET:
    SECRET_KEY = _env_secret
else:
    # Pas de SOCIALNET_SECRET configuré : on génère un secret aléatoire fort
    # (32 octets, jamais prévisible ni codé en dur) plutôt que de retomber
    # sur une valeur fixe connue de quiconque lit le code source. La
    # contrepartie assumée : ce secret change à chaque redémarrage du
    # process, donc tous les jetons émis avant un restart deviennent
    # invalides — acceptable en développement, PAS en production (d'où
    # l'avertissement explicite ci-dessous, qui doit alerter quiconque
    # démarre le serveur sans avoir configuré .env).
    SECRET_KEY = secrets.token_hex(32)
    _reason = (
        "SOCIALNET_SECRET vaut encore la valeur d'exemple de .env.example (jamais remplacée)"
        if _env_secret == _PLACEHOLDER_SECRET
        else "SOCIALNET_SECRET n'est pas défini"
    )
    print(
        "\n"
        f"⚠️  {_reason} : un secret aléatoire temporaire a été généré pour cette exécution.\n"
        "    Toutes les sessions seront invalidées au prochain redémarrage du serveur.\n"
        "    Ne JAMAIS lancer en production sans définir un vrai SOCIALNET_SECRET (voir .env.example).\n",
        file=sys.stderr,
    )

JWT_ALGO = "HS256"
TOKEN_LIFETIME_SECONDS = 60 * 60 * 24 * 7  # 7 jours


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Retourne (hash, salt). PBKDF2-HMAC-SHA256, 200k itérations."""
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return digest.hex(), salt


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    computed, _ = hash_password(password, salt)
    return secrets.compare_digest(computed, expected_hash)


def create_token(user_id: int, username: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "exp": int(time.time()) + TOKEN_LIFETIME_SECONDS,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGO)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expirée, reconnecte-toi.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Jeton invalide.")


def get_current_user(authorization: str = Header(default=None)) -> dict:
    """Dépendance FastAPI : extrait l'utilisateur depuis l'en-tête Authorization: Bearer <token>."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Non authentifié. Connecte-toi d'abord (commande: login).")
    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_token(token)
    return {"id": int(payload["sub"]), "username": payload["username"]}
