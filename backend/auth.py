"""Authentification : hachage de mot de passe (PBKDF2) et jetons JWT."""
import hashlib
import os
import secrets
import time
import jwt
from fastapi import Header, HTTPException

SECRET_KEY = os.environ.get("SOCIALNET_SECRET", "dev-secret-change-en-production")
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
