"""Configuration centralisée (pydantic-settings style, sans dépendance lourde).

Charge les variables d'environnement + fichier .env si présent.
Toutes les valeurs sensibles et réglages de production passent par ici.
"""
from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass


_PLACEHOLDER = "change-moi-avant-la-production"


@dataclass(frozen=True)
class Settings:
    secret_key: str
    token_lifetime_seconds: int = 60 * 60 * 24 * 7  # 7 jours
    jwt_algorithm: str = "HS256"
    max_image_size: int = 5 * 1024 * 1024
    max_image_dimension: int = 4096
    auth_timeout_seconds: float = 5.0
    rate_limit_register: tuple[int, int] = (5, 60)   # 5 / min
    rate_limit_login: tuple[int, int] = (10, 60)      # 10 / min
    debug: bool = False

    # --- Agent IA (backend/services/agent.py) ---
    # Comptes considérés admin dès le démarrage (en plus de is_admin en DB).
    admin_usernames: tuple[str, ...] = ()
    # Réutilise la même clé Groq que les bots (bots/.env.example) : un seul
    # provider LLM à configurer pour tout le projet.
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    # Rate limit dédié : l'agent appelle un LLM (coût, latence) et peut
    # proposer des actions sensibles, donc plus strict que les routes classiques.
    rate_limit_agent: tuple[int, int] = (10, 60)      # 10 / min


def _load_secret() -> str:
    env_secret = os.environ.get("SOCIALNET_SECRET")
    if env_secret and env_secret != _PLACEHOLDER:
        return env_secret

    # Développement : secret aléatoire fort (change à chaque restart)
    secret = secrets.token_hex(32)
    reason = (
        "SOCIALNET_SECRET vaut encore la valeur d'exemple de .env.example"
        if env_secret == _PLACEHOLDER
        else "SOCIALNET_SECRET n'est pas défini"
    )
    print(
        f"\n⚠️  {reason} : un secret aléatoire temporaire a été généré.\n"
        "    Toutes les sessions seront invalidées au prochain redémarrage.\n"
        "    Ne JAMAIS lancer en production sans un vrai SOCIALNET_SECRET.\n",
        file=sys.stderr,
    )
    return secret


def _load_admin_usernames() -> tuple[str, ...]:
    raw = os.environ.get("ADMIN_USERNAMES", "")
    return tuple(u.strip() for u in raw.split(",") if u.strip())


settings = Settings(
    secret_key=_load_secret(),
    debug=os.environ.get("SOCIALNET_DEBUG", "").lower() in ("1", "true", "yes"),
    admin_usernames=_load_admin_usernames(),
    groq_api_key=os.environ.get("GROQ_API_KEY", ""),
    groq_model=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
)
