"""Authentification : hachage de mot de passe (PBKDF2) et jetons JWT.

Ce module reste volontairement sans dépendance à `database` pour rester
testable isolément. La vérification de révocation (DB) est appliquée
par-dessus dans app.py / les routers.
"""
from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from typing import Any

import jwt
from fastapi import Header, HTTPException

from config import settings

SECRET_KEY = settings.secret_key
JWT_ALGO = settings.jwt_algorithm
TOKEN_LIFETIME_SECONDS = settings.token_lifetime_seconds


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
    t = time.time()
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": int(t),
        # iat_ms : résolution milliseconde pour la révocation globale fiable
        "iat_ms": int(t * 1000),
        "exp": int(t) + TOKEN_LIFETIME_SECONDS,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGO)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expirée, reconnecte-toi.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Jeton invalide.")


def get_current_user(authorization: str = Header(default=None)) -> dict[str, Any]:
    """Extrait l'utilisateur depuis Authorization: Bearer <token>.

    Ne vérifie QUE la signature et l'expiration — pas la révocation DB.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Non authentifié. Connecte-toi d'abord (commande: login).",
        )
    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_token(token)
    return {
        "id": int(payload["sub"]),
        "username": payload["username"],
        "jti": payload["jti"],
        "iat": payload["iat"],
        "iat_ms": payload["iat_ms"],
        "exp": payload["exp"],
    }
