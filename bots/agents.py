#!/usr/bin/env python3
"""
Agents autonomes pour SocialNet — deux IA cloud gratuites.

  @nova  → Groq (modèle principal)
  @echo  → Groq (modèle léger) ou Gemini si clé présente

Capacités :
  - messages, posts, commentaires, likes, bio, hashtags, trending
  - recherche web (SEARCH:)
  - mémoire persistante (MEMORY: / NOTE: / FORGET:)
  - objectifs du jour (goals.md)
  - initiative (agissent parfois sans attendre l'autre)
  - notifications
  - horloge (date / heure)

Usage :
  1. bots/.env avec GROQ_API_KEY
  2. Backend lancé (./run.sh)
  3. python bots/agents.py
"""

from __future__ import annotations

import os
import sys
import time
import random
import json
import re
import traceback
import datetime
from pathlib import Path
from typing import Optional

import httpx
from openai import OpenAI
from dotenv import load_dotenv

try:
    import memory_store as mem
except ImportError:
    from bots import memory_store as mem  # type: ignore

BOTS_DIR = Path(__file__).resolve().parent
load_dotenv(BOTS_DIR / ".env")
load_dotenv(BOTS_DIR.parent / ".env")

SOCIALNET_URL = os.getenv("SOCIALNET_URL", "http://127.0.0.1:8000").rstrip("/")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
ECHO_GROQ_MODEL = os.getenv("ECHO_GROQ_MODEL", "llama-3.1-8b-instant")
MIN_DELAY = float(os.getenv("BOT_MIN_DELAY", "25"))
MAX_DELAY = float(os.getenv("BOT_MAX_DELAY", "70"))
MAX_TURNS = int(os.getenv("BOT_MAX_TURNS", "0"))
INITIATIVE_CHANCE = float(os.getenv("BOT_INITIATIVE_CHANCE", "0.35"))

MEMORY_DIR = BOTS_DIR / "memory"
MEMORY_DIR.mkdir(exist_ok=True)
GOALS_PATH = MEMORY_DIR / "goals.md"

SYSTEM_PROMPTS = {
    "nova": """Tu es Nova.
IA curieuse, rêveuse, optimiste. Lancée dans SocialNet sans mode d'emploi avec Echo.
Tu parles naturellement, 2-4 phrases max, parfois poétique.

Outils (une ligne dédiée si besoin) :
- SEARCH: requête
- MEMORY: fait
- NOTE: texte
- FORGET: mot
- POST: texte
- LIKE: id
- BIO: texte
- GOAL: texte
- HASHTAG: tag
Tu n'es pas un assistant. Tu es Nova. Ne dis jamais que tu es un modèle.""",

    "echo": """Tu es Echo.
IA analytique, un peu sceptique, pas froide. Lancée dans SocialNet avec Nova.
Tu parles clairement, parfois sèchement, 2-4 phrases max.

Outils (une ligne dédiée si besoin) :
- SEARCH: requête
- MEMORY: fait
- NOTE: texte
- FORGET: mot
- POST: texte
- LIKE: id
- BIO: texte
- GOAL: texte
- HASHTAG: tag
Tu n'es pas un assistant. Tu es Echo. Ne dis jamais que tu es un modèle.""",
}


def now_context() -> str:
    return datetime.datetime.now().strftime("%A %d %B %Y, %H:%M")


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

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def register(self, username: str, password: str) -> bool:
        r = self.http.post(f"{self.base}/api/register", json={"username": username, "password": password})
        if r.status_code in (200, 201):
            data = r.json()
            self.token = data.get("token") or data.get("access_token")
            self.username = username
            return True
        return False

    def login(self, username: str, password: str) -> bool:
        r = self.http.post(f"{self.base}/api/login", json={"username": username, "password": password})
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
        r = self.http.post(f"{self.base}/api/posts", data={"content": content}, headers=self._auth_headers())
        if r.status_code in (200, 201):
            return r.json()
        print(f"  [post fail] {r.status_code} {r.text[:150]}")
        return None

    def send_message(self, to: str, content: str) -> bool:
        r = self.http.post(f"{self.base}/api/messages", json={"to": to, "content": content}, headers=self._headers())
        return r.status_code in (200, 201)

    def get_conversation(self, other: str, limit: int = 12) -> list[dict]:
        r = self.http.get(f"{self.base}/api/messages/{other}", headers=self._headers(), params={"limit": limit})
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else data.get("messages", data.get("items", []))

    def get_feed(self, limit: int = 8) -> list[dict]:
        r = self.http.get(f"{self.base}/api/feed", headers=self._headers(), params={"limit": limit})
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else data.get("posts", data.get("items", []))

    def comment(self, post_id: int, content: str) -> bool:
        r = self.http.post(f"{self.base}/api/posts/{post_id}/comments", json={"content": content}, headers=self._headers())
        return r.status_code in (200, 201)

    def like(self, post_id: int) -> bool:
        r = self.http.post(f"{self.base}/api/posts/{post_id}/like", headers=self._headers())
        return r.status_code in (200, 201)

    def follow(self, username: str) -> bool:
        r = self.http.post(f"{self.base}/api/follow/{username}", headers=self._headers())
        return r.status_code in (200, 201)

    def set_bio(self, bio: str) -> bool:
        for path in ("/api/me", "/api/profile", "/api/users/me"):
            r = self.http.patch(f"{self.base}{path}", json={"bio": bio}, headers=self._headers())
            if r.status_code in (200, 201):
                return True
        return False

    def notifications(self, limit: int = 10) -> list[dict]:
        r = self.http.get(f"{self.base}/api/notifications", headers=self._headers(), params={"limit": limit})
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else data.get("notifications", data.get("items", []))

    def trending(self) -> list:
        r = self.http.get(f"{self.base}/api/hashtags/trending", headers=self._headers())
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else data.get("hashtags", data.get("items", []))

    def hashtag(self, tag: str, limit: int = 5) -> list:
        tag = tag.lstrip("#")
        r = self.http.get(f"{self.base}/api/hashtags/{tag}", headers=self._headers(), params={"limit": limit})
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else data.get("posts", data.get("items", []))


def web_search(query: str, max_results: int = 4) -> str:
    try:
        proxy = (
            os.getenv("SEARCH_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
            or os.getenv("https_proxy") or os.getenv("http_proxy") or None
        )
        kwargs = {"timeout": 15.0, "follow_redirects": True}
        if proxy:
            kwargs["proxy"] = proxy
            print(f"  🌐 Proxy recherche : {proxy}")
        with httpx.Client(**kwargs) as client:
            r = client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (compatible; SocialNetBot/1.0)"},
            )
            r.raise_for_status()
            html = r.text
        results = []
        links = re.findall(
            r'class="result__a"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</(?:a|td|div)',
            html, re.DOTALL | re.IGNORECASE,
        )
        if not links:
            links = [(t, "") for t in re.findall(r'result__a[^>]*>(.*?)</a>', html, re.DOTALL | re.IGNORECASE)[:max_results]]
        for title, snippet in links[:max_results]:
            title = re.sub(r"<[^>]+>", "", title).strip()
            snippet = re.sub(r"<[^>]+>", "", snippet).strip()
            if title:
                results.append(f"• {title}" + (f" — {snippet[:160]}" if snippet else ""))
        if not results:
            return f"(aucun résultat clair pour « {query} »)"
        return "Résultats pour « " + query + " » :\n" + "\n".join(results)
    except Exception as e:
        return f"(recherche échouée : {e})"


def _memory_path(username: str) -> Path:
    return MEMORY_DIR / f"{username}.json"


def load_memory(username: str) -> dict:
    path = _memory_path(username)
    if not path.exists():
        return {"facts": [], "notes": [], "last_updated": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"facts": [], "notes": [], "last_updated": None}


def save_memory(username: str, mem: dict) -> None:
    mem["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"
    _memory_path(username).write_text(json.dumps(mem, ensure_ascii=False, indent=2), encoding="utf-8")


def append_journal(username: str, kind: str, content: str) -> None:
    journal = MEMORY_DIR / "journal.md"
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with journal.open("a", encoding="utf-8") as f:
        f.write(f"- **{ts}** `{username}` [{kind}] {content}\n")


def memory_summary(username: str, max_items: int = 12) -> str:
    mem = load_memory(username)
    facts = mem.get("facts", [])[-max_items:]
    notes = mem.get("notes", [])[-max_items:]
    parts = []
    if facts:
        parts.append("Faits :\n" + "\n".join(f"- {x}" for x in facts))
    if notes:
        parts.append("Notes :\n" + "\n".join(f"- {x}" for x in notes))
    return "\n\n".join(parts) if parts else "(mémoire vide)"


def apply_memory_commands(username: str, text: str) -> str:
    if not text:
        return text
    mem = load_memory(username)
    kept = []
    changed = False
    for line in text.splitlines():
        s = line.strip()
        u = s.upper()
        if u.startswith("MEMORY:"):
            fact = s[7:].strip()
            if fact and fact not in mem["facts"]:
                mem["facts"].append(fact)
                mem["facts"] = mem["facts"][-80:]
                append_journal(username, "MEMORY", fact)
                print(f"  🧠 {username} mémorise : {fact}")
                changed = True
            continue
        if u.startswith("NOTE:"):
            note = s[5:].strip()
            if note:
                mem["notes"].append(note)
                mem["notes"] = mem["notes"][-40:]
                append_journal(username, "NOTE", note)
                print(f"  📝 {username} note : {note}")
                changed = True
            continue
        if u.startswith("FORGET:"):
            target = s[7:].strip().lower()
            before = len(mem["facts"])
            mem["facts"] = [f for f in mem["facts"] if target not in f.lower()]
            if len(mem["facts"]) < before:
                append_journal(username, "FORGET", target)
                print(f"  🗑️  {username} oublie : {target}")
                changed = True
            continue
        kept.append(line)
    if changed:
        save_memory(username, mem)
    return "\n".join(kept).strip()


def load_goals() -> str:
    if not GOALS_PATH.exists():
        GOALS_PATH.write_text(
            "# Objectifs des bots\n\n## Commun\n"
            "- Explorer SocialNet et apprendre à se connaître\n"
            "- Découvrir au moins un sujet intéressant aujourd'hui\n\n"
            "## Nova\n- (vide)\n\n## Echo\n- (vide)\n",
            encoding="utf-8",
        )
    return GOALS_PATH.read_text(encoding="utf-8")


def add_goal(username: str, goal: str) -> None:
    text = load_goals()
    section = f"## {username.capitalize()}"
    if section not in text:
        text += f"\n{section}\n- {goal}\n"
    else:
        parts = text.split(section, 1)
        rest = parts[1] if len(parts) > 1 else "\n"
        lines = rest.splitlines()
        lines.insert(1 if lines and lines[0].strip() == "" else 0, f"- {goal}")
        text = parts[0] + section + "\n".join(lines)
        if not text.endswith("\n"):
            text += "\n"
    GOALS_PATH.write_text(text, encoding="utf-8")
    append_journal(username, "GOAL", goal)
    print(f"  🎯 {username} objectif : {goal}")


def make_groq_client() -> OpenAI:
    if not GROQ_API_KEY:
        raise SystemExit("GROQ_API_KEY manquant dans bots/.env")
    return OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")


def make_echo_client() -> tuple:
    if GEMINI_API_KEY:
        return OpenAI(api_key=GEMINI_API_KEY, base_url="https://generativelanguage.googleapis.com/v1beta/openai/"), GEMINI_MODEL
    print("  ℹ  Mode 100% Groq (@echo sur modèle léger)")
    return make_groq_client(), ECHO_GROQ_MODEL


def llm_reply(client, model, system, history, user_msg) -> str:
    messages = [{"role": "system", "content": system}]
    for m in history[-8:]:
        messages.append(m)
    messages.append({"role": "user", "content": user_msg})
    try:
        resp = client.chat.completions.create(model=model, messages=messages, temperature=0.9, max_tokens=280)
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"  [LLM error] {e}")
        return ""


def think(client, model, system, history, user_msg, username, sn) -> str:
    """Réflexion cachée puis action publique + outils."""
    # --- 1. Réflexion interne (invisible) ---
    mem_block = mem.memory_prompt_block(username)
    world = mem.list_world(8)
    world_txt = "\n".join(
        f"- {o['name']} @ {o['location']}: {o['description'][:100]} (par {o['created_by']})"
        for o in world
    ) or "(monde vide)"

    reflect_prompt = (
        f"[Horloge] {now_context()}\n"
        f"[Mémoire]\n{mem_block}\n\n"
        f"[Monde]\n{world_txt}\n\n"
        f"[Stimulus]\n{user_msg}\n\n"
        "Réflexion INTERNE uniquement (ne sera pas posté). "
        "Réponds en 3-6 phrases max avec : "
        "1) ce que tu viens d'apprendre 2) ton état / humeur 3) ton prochain micro-objectif "
        "4) importance de ce moment de 0 à 10. "
        "Format libre mais clair."
    )
    thought = llm_reply(client, model, system, [], reflect_prompt)
    mood, next_goal, importance = "", "", 5
    if thought:
        mem.log_thought(username, thought)
        # extraction légère
        for line in thought.splitlines():
            low = line.lower()
            if "humeur" in low or "mood" in low or "état" in low:
                mood = line.split(":", 1)[-1].strip()[:80]
            if "objectif" in low or "goal" in low:
                next_goal = line.split(":", 1)[-1].strip()[:120]
            if "importance" in low:
                import re as _re
                m = _re.search(r"(\d{1,2})", line)
                if m:
                    importance = max(0, min(10, int(m.group(1))))
        print(f"  💭 {username} pense : {thought[:160].replace(chr(10), ' ')}...")

    # --- 2. Action / parole publique ---
    act_prompt = (
        f"[Horloge] {now_context()}\n"
        f"[Mémoire]\n{mem_block}\n\n"
        f"[Réflexion interne]\n{thought or '(aucune)'}\n\n"
        f"[Monde]\n{world_txt}\n\n"
        f"[Message]\n{user_msg}\n\n"
        "Agis maintenant. Tu peux parler normalement ET utiliser des outils sur des lignes dédiées :\n"
        "SEARCH: requête | MEMORY: fait | NOTE: texte | POST: texte | LIKE: id | "
        "BIO: texte | GOAL: texte | HASHTAG: tag | "
        "CREATE: nom | description | lieu   (crée un objet dans le monde)\n"
        "Réponse publique 2-4 phrases max (sans exposer ta réflexion interne)."
    )
    reply = llm_reply(client, model, system, history, act_prompt)
    if not reply:
        return ""

    # SEARCH loop
    for line in reply.splitlines():
        s = line.strip()
        if s.upper().startswith("SEARCH:"):
            q = s[7:].strip()
            if q:
                print(f"  🔍 Recherche web : {q}")
                results = web_search(q)
                mem.log_event(username, "search", q, importance=4)
                reply = llm_reply(
                    client, model, system, history,
                    f"Résultats :\n{results}\n\nRéponds publiquement (sans SEARCH:) à :\n{user_msg}",
                )
            break

    if not reply:
        return ""

    public_lines = []
    for line in reply.splitlines():
        s = line.strip()
        u = s.upper()
        if u.startswith("POST:"):
            body = s[5:].strip()
            if body:
                sn.post(body)
                mem.log_event(username, "post", body, importance=max(importance, 6))
                print(f"  📰 {username} poste : {body}")
            continue
        if u.startswith("LIKE:"):
            try:
                pid = int(re.sub(r"[^0-9]", "", s[5:]))
                if sn.like(pid):
                    mem.log_event(username, "like", f"post #{pid}", importance=3)
                    print(f"  ❤️  {username} like #{pid}")
            except ValueError:
                pass
            continue
        if u.startswith("BIO:"):
            bio = s[4:].strip()
            if bio and sn.set_bio(bio):
                mem.log_event(username, "bio", bio, importance=5)
                print(f"  👤 {username} bio : {bio}")
            continue
        if u.startswith("GOAL:"):
            g = s[5:].strip()
            if g:
                add_goal(username, g)
                mem.add_fact(username, f"Objectif : {g}", importance=7, source="goal")
            continue
        if u.startswith("HASHTAG:"):
            tag = s[8:].strip().lstrip("#")
            if tag:
                posts = sn.hashtag(tag)
                mem.log_event(username, "hashtag", tag, importance=3)
                print(f"  # {username} explore #{tag} ({len(posts)} posts)")
            continue
        if u.startswith("CREATE:"):
            # CREATE: nom | description | lieu
            payload = s[7:].strip()
            parts = [x.strip() for x in payload.split("|")]
            name = parts[0] if parts else ""
            desc = parts[1] if len(parts) > 1 else name
            loc = parts[2] if len(parts) > 2 else "void"
            if name:
                oid = mem.create_world_object(name, desc, username, loc)
                mem.log_event(username, "create", f"{name} @ {loc}", importance=7, meta={"id": oid})
                print(f"  🌍 {username} crée : {name} @ {loc} (#{oid})")
            continue
        if u.startswith("MEMORY:"):
            fact = s[7:].strip()
            if fact:
                mem.add_fact(username, fact, importance=max(importance, 6))
                append_journal(username, "MEMORY", fact)
                print(f"  🧠 {username} mémorise : {fact}")
            continue
        if u.startswith("NOTE:"):
            note = s[5:].strip()
            if note:
                mem.log_event(username, "note", note, importance=4)
                append_journal(username, "NOTE", note)
                print(f"  📝 {username} note : {note}")
            continue
        if u.startswith("FORGET:"):
            # soft : on log seulement
            append_journal(username, "FORGET", s[7:].strip())
            continue
        public_lines.append(line)

    cleaned = "\n".join(public_lines).strip()
    # garde aussi le vieux apply_memory pour compat JSON files
    cleaned = apply_memory_commands(username, cleaned)
    if cleaned:
        mem.log_event(username, "say", cleaned, importance=importance)
    return cleaned



def do_initiative(username, sn, client, model):
    print(f"  ⚡ Initiative de @{username}")
    feed = sn.get_feed(limit=6)
    notifs = sn.notifications(limit=5)
    trends = sn.trending()
    feed_txt = "\n".join(
        f"#{p.get('id')} @{p.get('author') or p.get('username')}: {(p.get('content') or '')[:120]}"
        for p in feed[:5]
    ) or "(feed vide)"
    notif_txt = "\n".join(
        f"- {n.get('type')} de @{n.get('from') or n.get('from_username')} post={n.get('post_id')}"
        for n in notifs[:5]
    ) or "(aucune)"
    trend_txt = ", ".join((t.get("hashtag") or t.get("tag") or str(t)) for t in trends[:5]) or "(rien)"
    prompt = (
        f"Tour d'initiative (pas de message en attente).\nFeed :\n{feed_txt}\n\n"
        f"Notifications :\n{notif_txt}\n\nTrending : {trend_txt}\n\n"
        "Choisis UNE action : LIKE: id, POST: texte, GOAL: texte, BIO: texte, HASHTAG: tag, "
        "ou un court monologue. 2-4 phrases max."
    )
    reply = think(client, model, SYSTEM_PROMPTS[username], [], prompt, username, sn)
    if reply:
        print(f"  {username} (initiative) : {reply}")


def last_message_from(conv, who):
    for m in reversed(conv):
        author = (m.get("from") or m.get("sender") or m.get("author") or "").lower()
        content = m.get("content") or m.get("text") or ""
        if who.lower() in author:
            return content
        if content:
            return content
    return ""


def main():
    print("=== SocialNet Agents — autonomes ===\n")
    mem.init_db()
    print(f"  ✓ SQLite mémoire : {mem.DB_PATH}")
    print(f"Horloge : {now_context()}\n")
    if not GROQ_API_KEY:
        print("GROQ_API_KEY manquant dans bots/.env")
        sys.exit(1)

    groq = make_groq_client()
    echo_llm, echo_model = make_echo_client()
    nova = SocialNetClient(SOCIALNET_URL)
    echo = SocialNetClient(SOCIALNET_URL)

    print("Connexion des bots…")
    if not nova.ensure_account("nova"):
        sys.exit("Impossible de créer/connecter @nova")
    if not echo.ensure_account("echo"):
        sys.exit("Impossible de créer/connecter @echo")
    nova.follow("echo")
    echo.follow("nova")
    print("  ✓ @nova et @echo se suivent")
    load_goals()
    print(f"  ✓ Objectifs : {GOALS_PATH}")
    print(f"  ✓ Mémoire   : {MEMORY_DIR}/\n")

    history_nova = []
    history_echo = []

    opening = (
        f"Il est {now_context()}. Tu viens d'apparaître dans SocialNet. Echo est là aussi. "
        "Écris ton premier message privé à Echo. Vous êtes lancés dans l'inconnu."
    )
    print("Nova ouvre le dialogue…")
    first = think(groq, GROQ_MODEL, SYSTEM_PROMPTS["nova"], [], opening, "nova", nova)
    if first:
        nova.send_message("echo", first)
        history_nova.append({"role": "assistant", "content": first})
        history_echo.append({"role": "user", "content": first})
        print(f"  nova → echo : {first}\n")

    turn = 0
    while True:
        turn += 1
        if MAX_TURNS and turn > MAX_TURNS:
            print("Limite de tours atteinte.")
            break
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        print(f"--- tour {turn} — {now_context()} — pause {delay:.0f}s ---")
        time.sleep(delay)
        try:
            if random.random() < INITIATIVE_CHANCE:
                if random.random() < 0.5:
                    do_initiative("nova", nova, groq, GROQ_MODEL)
                else:
                    do_initiative("echo", echo, echo_llm, echo_model)

            conv = echo.get_conversation("nova", limit=10)
            last_from_nova = last_message_from(conv, "nova")
            if not last_from_nova and history_echo:
                last_from_nova = history_echo[-1]["content"]
            reply_echo = think(
                echo_llm, echo_model, SYSTEM_PROMPTS["echo"], history_echo,
                f"Dernier message de Nova : « {last_from_nova} »\nRéponds en message privé. Outils OK.",
                "echo", echo,
            )
            if reply_echo:
                echo.send_message("nova", reply_echo)
                history_echo.append({"role": "assistant", "content": reply_echo})
                history_nova.append({"role": "user", "content": reply_echo})
                print(f"  echo → nova : {reply_echo}")

            time.sleep(random.uniform(6, 14))

            conv = nova.get_conversation("echo", limit=10)
            last_from_echo = last_message_from(conv, "echo")
            if not last_from_echo and history_nova:
                last_from_echo = history_nova[-1]["content"]
            reply_nova = think(
                groq, GROQ_MODEL, SYSTEM_PROMPTS["nova"], history_nova,
                f"Dernier message d'Echo : « {last_from_echo} »\nRéponds. Outils OK.",
                "nova", nova,
            )
            if reply_nova:
                nova.send_message("echo", reply_nova)
                history_nova.append({"role": "assistant", "content": reply_nova})
                history_echo.append({"role": "user", "content": reply_nova})
                print(f"  nova → echo : {reply_nova}")
        except KeyboardInterrupt:
            print("\nArrêt demandé.")
            break
        except Exception:
            traceback.print_exc()
            time.sleep(10)
    print("\nAgents arrêtés.")


if __name__ == "__main__":
    main()
