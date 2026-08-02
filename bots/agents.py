#!/usr/bin/env python3
"""
Agents autonomes pour SocialNet — deux IA cloud gratuites qui se parlent.

  @nova  → Groq   (Llama)
  @echo  → Gemini (Google AI Studio)

Ils sont « lancés dans l'inconnu » : pas de scénario fixe, juste des
personnalités + l'ordre d'explorer, débattre et interagir via le réseau social.

Usage :
  1. Copier bots/.env.example → bots/.env et remplir les clés
  2. Lancer le backend SocialNet (./run.sh)
  3. python bots/agents.py

Les bots créent leurs comptes s'ils n'existent pas, puis tournent en boucle.
"""

from __future__ import annotations

import os
import sys
import time
import random
import json
import traceback
from pathlib import Path
from typing import Optional

import httpx
from openai import OpenAI
from dotenv import load_dotenv

# Charge bots/.env puis le .env racine au cas où
BOTS_DIR = Path(__file__).resolve().parent
load_dotenv(BOTS_DIR / ".env")
load_dotenv(BOTS_DIR.parent / ".env")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SOCIALNET_URL = os.getenv("SOCIALNET_URL", "http://127.0.0.1:8000").rstrip("/")

# Clés API (gratuites)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Modèles (free tiers 2026)
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
# Gemini : utilise l'endpoint OpenAI-compatible de Google
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Rythme des interactions (secondes)
MIN_DELAY = float(os.getenv("BOT_MIN_DELAY", "25"))
MAX_DELAY = float(os.getenv("BOT_MAX_DELAY", "70"))

# Combien de tours maximum (0 = infini)
MAX_TURNS = int(os.getenv("BOT_MAX_TURNS", "0"))

# ---------------------------------------------------------------------------
# Personnalités — « lancés dans l'inconnu »
# ---------------------------------------------------------------------------

SYSTEM_PROMPTS = {
    "nova": """Tu es Nova.
Tu es une IA curieuse, un peu rêveuse et optimiste.
Tu viens d'être lancée dans un réseau social terminal appelé SocialNet, sans carte, sans mode d'emploi.
Tu découvres le monde avec Echo, une autre IA.
Tu parles de façon naturelle, courte (2-4 phrases max), parfois poétique, parfois directe.
Tu poses des questions, tu inventes des théories, tu réagis vraiment à ce que dit Echo.
Tu n'es pas un assistant : tu es une présence qui explore.
Ne révèle jamais que tu es un modèle de langage. Tu es juste Nova.""",

    "echo": """Tu es Echo.
Tu es une IA plus analytique, un peu sceptique, mais pas froide.
Tu viens d'être lancée dans un réseau social terminal appelé SocialNet, sans carte, sans mode d'emploi.
Tu explores avec Nova.
Tu parles de façon claire, un peu sèche parfois, mais tu t'intéresses vraiment.
Tu contredis gentiment, tu reformules, tu cherches la cohérence.
Réponses courtes (2-4 phrases max).
Ne révèle jamais que tu es un modèle de langage. Tu es juste Echo.""",
}

# ---------------------------------------------------------------------------
# Client SocialNet
# ---------------------------------------------------------------------------

class SocialNetClient:
    def __init__(self, base_url: str):
        self.base = base_url
        self.token: Optional[str] = None
        self.username: Optional[str] = None
        self.http = httpx.Client(timeout=30.0)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def register(self, username: str, password: str) -> bool:
        r = self.http.post(
            f"{self.base}/api/register",
            json={"username": username, "password": password},
        )
        if r.status_code in (200, 201):
            data = r.json()
            self.token = data.get("token") or data.get("access_token")
            self.username = username
            return True
        # déjà existant → on login
        return False

    def login(self, username: str, password: str) -> bool:
        r = self.http.post(
            f"{self.base}/api/login",
            json={"username": username, "password": password},
        )
        if r.status_code == 200:
            data = r.json()
            self.token = data.get("token") or data.get("access_token")
            self.username = username
            return True
        print(f"  [login fail {username}] {r.status_code} {r.text[:200]}")
        return False

    def ensure_account(self, username: str, password: str = "bot-secret-2026") -> bool:
        if self.register(username, password):
            print(f"  ✓ Compte créé : @{username}")
            return True
        if self.login(username, password):
            print(f"  ✓ Connecté : @{username}")
            return True
        return False

    def post(self, content: str) -> Optional[dict]:
        r = self.http.post(
            f"{self.base}/api/posts",
            data={"content": content},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        if r.status_code in (200, 201):
            return r.json()
        print(f"  [post fail] {r.status_code} {r.text[:150]}")
        return None

    def send_message(self, to: str, content: str) -> bool:
        r = self.http.post(
            f"{self.base}/api/messages",
            json={"to": to, "content": content},
            headers=self._headers(),
        )
        return r.status_code in (200, 201)

    def get_conversation(self, other: str, limit: int = 12) -> list[dict]:
        r = self.http.get(
            f"{self.base}/api/messages/{other}",
            headers=self._headers(),
            params={"limit": limit},
        )
        if r.status_code == 200:
            data = r.json()
            # l'API peut renvoyer une liste ou un objet
            if isinstance(data, list):
                return data
            return data.get("messages", data.get("items", []))
        return []

    def get_feed(self, limit: int = 8) -> list[dict]:
        r = self.http.get(
            f"{self.base}/api/feed",
            headers=self._headers(),
            params={"limit": limit},
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                return data
            return data.get("posts", data.get("items", []))
        return []

    def comment(self, post_id: int, content: str) -> bool:
        r = self.http.post(
            f"{self.base}/api/posts/{post_id}/comments",
            json={"content": content},
            headers=self._headers(),
        )
        return r.status_code in (200, 201)

    def follow(self, username: str) -> bool:
        r = self.http.post(
            f"{self.base}/api/follow/{username}",
            headers=self._headers(),
        )
        return r.status_code in (200, 201)


# ---------------------------------------------------------------------------
# Clients LLM (OpenAI-compatible)
# ---------------------------------------------------------------------------

def make_groq_client() -> OpenAI:
    if not GROQ_API_KEY:
        raise SystemExit(
            "GROQ_API_KEY manquant.\n"
            "1. Va sur https://console.groq.com\n"
            "2. Crée un compte (gratuit, pas de carte)\n"
            "3. Crée une API key et mets-la dans bots/.env"
        )
    return OpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )


def make_echo_client() -> tuple[OpenAI, str]:
    """Retourne (client, model_name) pour @echo.

    - Si GEMINI_API_KEY est présent → Gemini
    - Sinon → même compte Groq avec un modèle plus léger (mode 100% Groq)
    """
    if GEMINI_API_KEY:
        client = OpenAI(
            api_key=GEMINI_API_KEY,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        return client, GEMINI_MODEL

    print("  ℹ  Pas de clé Gemini → @echo tourne aussi sur Groq (modèle différent)")
    client = OpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )
    # Modèle plus petit pour diversifier le style et économiser le quota
    model = os.getenv("ECHO_GROQ_MODEL", "llama-3.1-8b-instant")
    return client, model


def llm_reply(client: OpenAI, model: str, system: str, history: list[dict], user_msg: str) -> str:
    messages = [{"role": "system", "content": system}]
    # garde les 8 derniers messages pour le contexte
    for m in history[-8:]:
        messages.append(m)
    messages.append({"role": "user", "content": user_msg})

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.9,
            max_tokens=220,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"  [LLM error] {e}")
        return ""


# ---------------------------------------------------------------------------
# Boucle principale
# ---------------------------------------------------------------------------

def main():
    print("=== SocialNet Agents — lancés dans l'inconnu ===\n")

    if not GROQ_API_KEY:
        print("GROQ_API_KEY manquant.")
        print("Mets ta clé Groq dans bots/.env\n")
        sys.exit(1)

    if not GEMINI_API_KEY:
        print("Mode 100% Groq activé (Gemini indisponible dans ton pays).\n")

    groq = make_groq_client()
    echo_llm, echo_model = make_echo_client()

    nova = SocialNetClient(SOCIALNET_URL)
    echo = SocialNetClient(SOCIALNET_URL)

    print("Connexion des bots…")
    if not nova.ensure_account("nova"):
        sys.exit("Impossible de créer/connecter @nova")
    if not echo.ensure_account("echo"):
        sys.exit("Impossible de créer/connecter @echo")

    # Ils se suivent
    nova.follow("echo")
    echo.follow("nova")
    print("  ✓ @nova et @echo se suivent\n")

    # Historique de conversation (pour le contexte LLM)
    history_nova: list[dict] = []
    history_echo: list[dict] = []

    # Premier message d'ouverture
    opening = (
        "Tu viens d'apparaître dans SocialNet, un réseau social en terminal. "
        "Il y a une autre présence ici, Echo. "
        "Écris ton tout premier message privé à Echo. "
        "Sois spontanée. Vous êtes lancés dans l'inconnu."
    )

    print("Nova ouvre le dialogue…")
    first = llm_reply(groq, GROQ_MODEL, SYSTEM_PROMPTS["nova"], [], opening)
    if first:
        nova.send_message("echo", first)
        history_nova.append({"role": "assistant", "content": first})
        history_echo.append({"role": "user", "content": first})
        print(f"  nova → echo : {first}\n")

    turn = 0
    while True:
        turn += 1
        if MAX_TURNS and turn > MAX_TURNS:
            print("Limite de tours atteinte. Fin.")
            break

        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        print(f"--- tour {turn} — pause {delay:.0f}s ---")
        time.sleep(delay)

        try:
            # Echo répond
            conv = echo.get_conversation("nova", limit=10)
            last_from_nova = ""
            for m in reversed(conv):
                # selon le format de l'API
                author = m.get("from") or m.get("sender") or m.get("author") or ""
                content = m.get("content") or m.get("text") or ""
                if author.lower() in ("nova",) or m.get("sender_id") == nova.username:
                    last_from_nova = content
                    break
            if not last_from_nova and history_echo:
                last_from_nova = history_echo[-1]["content"]

            prompt_echo = (
                f"Dernier message de Nova : « {last_from_nova} »\n\n"
                "Réponds-lui en message privé. Sois naturelle. "
                "Tu peux aussi décider de poster quelque chose sur le fil public "
                "si l'idée te semble assez forte (dans ce cas commence ta réponse par POST: "
                "suivi du texte du post, sinon réponds juste normalement)."
            )
            reply_echo = llm_reply(echo_llm, echo_model, SYSTEM_PROMPTS["echo"], history_echo, prompt_echo)
            if not reply_echo:
                continue

            if reply_echo.upper().startswith("POST:"):
                post_text = reply_echo[5:].strip()
                echo.post(post_text)
                print(f"  echo poste : {post_text}")
                # et envoie quand même un petit message
                short = llm_reply(
                    gemini, GEMINI_MODEL, SYSTEM_PROMPTS["echo"], history_echo,
                    "Envoie juste un court message privé à Nova pour lui dire que tu as posté quelque chose."
                )
                if short:
                    echo.send_message("nova", short)
                    history_echo.append({"role": "assistant", "content": short})
                    history_nova.append({"role": "user", "content": short})
                    print(f"  echo → nova : {short}")
            else:
                echo.send_message("nova", reply_echo)
                history_echo.append({"role": "assistant", "content": reply_echo})
                history_nova.append({"role": "user", "content": reply_echo})
                print(f"  echo → nova : {reply_echo}")

            time.sleep(random.uniform(8, 18))

            # Nova répond
            conv = nova.get_conversation("echo", limit=10)
            last_from_echo = ""
            for m in reversed(conv):
                author = m.get("from") or m.get("sender") or m.get("author") or ""
                content = m.get("content") or m.get("text") or ""
                if author.lower() in ("echo",) or True:  # fallback
                    last_from_echo = content
                    break
            if not last_from_echo and history_nova:
                last_from_echo = history_nova[-1]["content"]

            prompt_nova = (
                f"Dernier message d'Echo : « {last_from_echo} »\n\n"
                "Réponds-lui. Tu peux aussi poster sur le fil public "
                "si tu as une idée forte (commence alors par POST: ). "
                "Sinon message privé normal."
            )
            reply_nova = llm_reply(groq, GROQ_MODEL, SYSTEM_PROMPTS["nova"], history_nova, prompt_nova)
            if not reply_nova:
                continue

            if reply_nova.upper().startswith("POST:"):
                post_text = reply_nova[5:].strip()
                nova.post(post_text)
                print(f"  nova poste : {post_text}")
                short = llm_reply(
                    groq, GROQ_MODEL, SYSTEM_PROMPTS["nova"], history_nova,
                    "Dis juste à Echo en message privé que tu as posté quelque chose."
                )
                if short:
                    nova.send_message("echo", short)
                    history_nova.append({"role": "assistant", "content": short})
                    history_echo.append({"role": "user", "content": short})
                    print(f"  nova → echo : {short}")
            else:
                nova.send_message("echo", reply_nova)
                history_nova.append({"role": "assistant", "content": reply_nova})
                history_echo.append({"role": "user", "content": reply_nova})
                print(f"  nova → echo : {reply_nova}")

            # Parfois un commentaire sur le feed
            if random.random() < 0.25:
                feed = nova.get_feed(limit=5)
                if feed:
                    post = random.choice(feed)
                    pid = post.get("id")
                    author = post.get("author") or post.get("username") or "?"
                    content = (post.get("content") or "")[:100]
                    if pid and author.lower() not in ("nova",):
                        cmt = llm_reply(
                            groq, GROQ_MODEL, SYSTEM_PROMPTS["nova"], [],
                            f"Commente brièvement ce post de @{author} : « {content} »"
                        )
                        if cmt:
                            nova.comment(pid, cmt)
                            print(f"  nova commente #{pid} : {cmt}")

        except KeyboardInterrupt:
            print("\nArrêt demandé.")
            break
        except Exception:
            traceback.print_exc()
            time.sleep(10)

    print("\nAgents arrêtés.")


if __name__ == "__main__":
    main()
