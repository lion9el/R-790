"""Dépendances FastAPI partagées (auth + révocation)."""
from __future__ import annotations

from fastapi import Header, HTTPException

from auth import get_current_user as _decode_and_verify_signature
from database import get_conn


def get_current_user(authorization: str = Header(default=None)) -> dict:
    """Dépendance FastAPI pour les routes protégées.

    Vérifie signature + expiration (auth.py) puis révocation DB.
    """
    user = _decode_and_verify_signature(authorization)
    _reject_if_revoked(user)
    return user


def _reject_if_revoked(user: dict) -> None:
    """Lève 401 si le jeton a été révoqué (individuellement ou globalement)."""
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
