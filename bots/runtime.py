#!/usr/bin/env python3
"""
Runtime multi-agents autonomes pour SocialNet.

- Threads indépendants (nova, echo, orbit)
- Réflexion cachée puis action
- Mémoire SQLite + monde + relations + règles
- Budget tokens
- Journal quotidien
"""
from __future__ import annotations

import os, sys, time, random, re, json, traceback, threading, datetime
from pathlib import Path
from typing import Optional

import httpx
from openai import OpenAI
from dotenv import load_dotenv

BOTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BOTS_DIR))
load_dotenv(BOTS_DIR / ".env")
load_dotenv(BOTS_DIR.parent / ".env")

import memory_store as mem
import git_workspace as ghub
import sync as agent_sync
import chronicle as network_chronicle

SOCIALNET_URL = os.getenv("SOCIALNET_URL", "http://127.0.0.1:8000").rstrip("/")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
LIGHT_MODEL = os.getenv("ECHO_GROQ_MODEL", "llama-3.1-8b-instant")
MIN_DELAY = float(os.getenv("BOT_MIN_DELAY", "30"))
MAX_DELAY = float(os.getenv("BOT_MAX_DELAY", "90"))
MAX_TOKENS_BUDGET = int(os.getenv("BOT_TOKEN_BUDGET", "0"))  # 0 = illimité (pas de stop). Voir BOT_TOKEN_BUDGET_SCOPE.
# "run"      : le budget repart à zéro à chaque lancement du script (défaut le plus intuitif)
# "day"      : le budget est partagé sur toute la journée UTC, même en relançant le script
# "lifetime" : cumulatif pour toujours (comportement historique — se remplit vite si tu relances souvent)
BUDGET_SCOPE = os.getenv("BOT_TOKEN_BUDGET_SCOPE", "run").strip().lower()
SEARCH_PROXY = os.getenv("SEARCH_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")

BOT_CONFIG = {
    "nova": {"model": GROQ_MODEL, "password": "bot-secret-2026"},
    "echo": {"model": LIGHT_MODEL, "password": "bot-secret-2026"},
    "orbit": {"model": LIGHT_MODEL, "password": "bot-secret-2026"},
}

_stop = threading.Event()
_print_lock = threading.Lock()
_sync_lock = threading.Lock()
_sync_counter = 0
RUN_START_TS = datetime.datetime.utcnow().isoformat() + "Z"


def budget_epoch() -> Optional[str]:
    """Retourne le timestamp ISO à partir duquel compter les tokens, selon BUDGET_SCOPE."""
    if BUDGET_SCOPE == "lifetime":
        return None
    if BUDGET_SCOPE == "day":
        return datetime.datetime.utcnow().strftime("%Y-%m-%dT00:00:00")
    return RUN_START_TS  # "run" (défaut)


def budget_used() -> int:
    if MAX_TOKENS_BUDGET <= 0:
        return 0  # illimité : on ne va même pas interroger la DB à chaque appel
    return mem.token_total(since=budget_epoch())


def budget_exhausted() -> bool:
    if MAX_TOKENS_BUDGET <= 0:
        return False  # illimité, ne s'arrête jamais tout seul
    return budget_used() >= MAX_TOKENS_BUDGET


def log(msg: str):
    with _print_lock:
        print(msg, flush=True)


def load_personality(name: str) -> str:
    path = BOTS_DIR / "personalities" / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"Tu es {name}."


def load_identity_json(name: str) -> dict:
    path = BOTS_DIR / "identities" / f"{name}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def load_core_knowledge() -> str:
    parts = []
    for fname in ("CORE_KNOWLEDGE.md", "MISSION.md"):
        path = BOTS_DIR / "identities" / fname
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    goals = BOTS_DIR / "memory" / "goals.md"
    if goals.exists():
        parts.append(goals.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def seed_permanent_memory():
    """Injecte une fois les faits d'identité et la mission dans SQLite."""
    core = load_core_knowledge()
    for bot in BOT_CONFIG:
        ident = load_identity_json(bot)
        if ident:
            mem.add_fact(bot, f"Identité: {json.dumps(ident, ensure_ascii=False)}", importance=10, source="seed")
            if ident.get("mission_focus"):
                mem.add_fact(bot, f"Focus mission: {ident['mission_focus']}", importance=10, source="seed")
            if ident.get("role"):
                mem.add_fact(bot, f"Rôle: {ident['role']}", importance=9, source="seed")
        mem.add_fact(
            bot,
            "Mission commune: collaborer (Nova+Echo+Orbit) pour concevoir AGENT-ZERO, "
            "agent autonome puissant sans ré-entraînement, documenté dans socialnet-world.",
            importance=10,
            source="seed",
        )
        mem.add_fact(
            bot,
            "SocialNet: réseau social terminal (posts, MP, likes, hashtags, notifs). "
            "Outils: search, create, write_repo, read_repo, remember, journal, rule.",
            importance=9,
            source="seed",
        )
        mem.add_learned_rule(
            bot,
            "Toujours documenter les avancées AGENT-ZERO via write_repo dans notes/agent-zero/",
        )
    # shared relationship seeds
    for a, b in (("nova", "echo"), ("nova", "orbit"), ("echo", "orbit"),
                 ("echo", "nova"), ("orbit", "nova"), ("orbit", "echo")):
        mem.update_relationship(a, b, delta_trust=0.1, note="alliés sur AGENT-ZERO")
    mem.log_event("system", "seed", "mémoire permanente + mission AGENT-ZERO injectées", importance=10)


# -------------------- SocialNet client --------------------
class SN:
    def __init__(self, base: str):
        self.base = base
        self.token = None
        self.username = None
        self.http = httpx.Client(timeout=30.0)

    def _h(self):
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def ensure(self, username, password):
        r = self.http.post(f"{self.base}/api/register", json={"username": username, "password": password})
        if r.status_code in (200, 201):
            data = r.json()
            self.token = data.get("token") or data.get("access_token")
            self.username = username
            return True
        r = self.http.post(f"{self.base}/api/login", json={"username": username, "password": password})
        if r.status_code == 200:
            data = r.json()
            self.token = data.get("token") or data.get("access_token")
            self.username = username
            return True
        log(f"  [auth fail {username}] {r.status_code} {r.text[:120]}")
        return False

    def post(self, content):
        r = self.http.post(f"{self.base}/api/posts", data={"content": content}, headers={"Authorization": f"Bearer {self.token}"})
        return r.json() if r.status_code in (200, 201) else None

    def msg(self, to, content):
        r = self.http.post(f"{self.base}/api/messages", json={"to": to, "content": content}, headers=self._h())
        return r.status_code in (200, 201)

    def conv(self, other, limit=8):
        r = self.http.get(f"{self.base}/api/messages/{other}", headers=self._h(), params={"limit": limit})
        if r.status_code != 200:
            return []
        d = r.json()
        return d if isinstance(d, list) else d.get("messages", d.get("items", []))

    def feed(self, limit=6):
        r = self.http.get(f"{self.base}/api/feed", headers=self._h(), params={"limit": limit})
        if r.status_code != 200:
            return []
        d = r.json()
        return d if isinstance(d, list) else d.get("posts", d.get("items", []))

    def like(self, pid):
        r = self.http.post(f"{self.base}/api/posts/{pid}/like", headers=self._h())
        return r.status_code in (200, 201)

    def comment(self, pid, content):
        r = self.http.post(f"{self.base}/api/posts/{pid}/comments", json={"content": content}, headers=self._h())
        return r.status_code in (200, 201)

    def follow(self, u):
        r = self.http.post(f"{self.base}/api/follow/{u}", headers=self._h())
        return r.status_code in (200, 201)

    def notifs(self, limit=8):
        r = self.http.get(f"{self.base}/api/notifications", headers=self._h(), params={"limit": limit})
        if r.status_code != 200:
            return []
        d = r.json()
        return d if isinstance(d, list) else d.get("notifications", [])

    def trending(self):
        r = self.http.get(f"{self.base}/api/hashtags/trending", headers=self._h())
        if r.status_code != 200:
            return []
        d = r.json()
        return d if isinstance(d, list) else d.get("hashtags", [])


# -------------------- LLM --------------------
def client():
    if not GROQ_API_KEY:
        raise SystemExit("GROQ_API_KEY manquant")
    return OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")


def _parse_retry_wait(msg: str) -> Optional[float]:
    """Parse le délai suggéré par Groq, ex: 'try again in 20m18.24s' ou 'try again in 29.88s'.
    Le format peut inclure des minutes ; les ignorer faisait attendre ~18s au lieu de ~20min
    et provoquait un spam de retries inutiles pour un quota qui ne se réinitialise que bien plus tard."""
    m = re.search(r"try again in(?: (\d+)m)?\s*([\d.]+)s", msg)
    if not m:
        return None
    minutes = float(m.group(1) or 0)
    seconds = float(m.group(2))
    return minutes * 60 + seconds


def llm(c, model, system, user, bot_name="?", _retry=0) -> str:
    if budget_exhausted():
        log(f"  ⛔ Budget tokens atteint ({MAX_TOKENS_BUDGET}, scope={BUDGET_SCOPE}). Stop soft.")
        _stop.set()
        return ""
    try:
        resp = c.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.9,
            max_tokens=320,
        )
        usage = getattr(resp, "usage", None)
        if usage:
            mem.log_tokens(bot_name, model, getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0))
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        msg = str(e)
        is_rate_limit = "429" in msg or "rate_limit" in msg.lower()
        if not is_rate_limit:
            log(f"  [LLM error {bot_name}] {e}")
            time.sleep(3)
            return ""

        wait_s = _parse_retry_wait(msg)
        is_daily = "per day" in msg.lower() or "tpd" in msg.lower()

        if is_daily and wait_s:
            # Quota journalier : inutile de retenter 4 fois en rafale, ça ne se réinitialisera
            # pas avant `wait_s`. Une seule longue attente (plafonnée) plutôt qu'un spam de logs.
            capped = min(wait_s + 5.0, 45 * 60)
            log(f"  😴 {bot_name} quota journalier atteint, pause {capped/60:.1f} min avant nouvel essai")
            time.sleep(capped)
            if _retry < 1:
                return llm(c, model, system, user, bot_name, _retry=_retry + 1)
            log(f"  [LLM error {bot_name}] toujours limité après attente : {e}")
            return ""

        if _retry < 4:
            # Limite courte (par minute) : backoff classique, on utilise le délai suggéré si présent.
            wait = wait_s + 1.0 if wait_s else 5.0 * (2 ** _retry)
            log(f"  ⏳ {bot_name} rate-limit, retry #{_retry+1} dans {wait:.0f}s")
            time.sleep(wait)
            return llm(c, model, system, user, bot_name, _retry=_retry + 1)

        log(f"  [LLM error {bot_name}] {e}")
        time.sleep(3)
        return ""


def web_search(query: str) -> str:
    try:
        kwargs = {"timeout": 15.0, "follow_redirects": True}
        if SEARCH_PROXY:
            kwargs["proxy"] = SEARCH_PROXY
        with httpx.Client(**kwargs) as h:
            r = h.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (compatible; SocialNetBot/1.0)"},
            )
            r.raise_for_status()
            html = r.text
        links = re.findall(
            r'class="result__a"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</(?:a|td|div)',
            html, re.DOTALL | re.IGNORECASE,
        )
        if not links:
            links = [(t, "") for t in re.findall(r'result__a[^>]*>(.*?)</a>', html, re.DOTALL | re.IGNORECASE)[:4]]
        out = []
        for title, snip in links[:4]:
            title = re.sub(r"<[^>]+>", "", title).strip()
            snip = re.sub(r"<[^>]+>", "", snip).strip()
            if title:
                out.append(f"• {title}" + (f" — {snip[:140]}" if snip else ""))
        return "\n".join(out) if out else f"(rien pour {query})"
    except Exception as e:
        return f"(search fail: {e})"


ACTIONS = "speak | message | post | like | comment | search | remember | create | rule | wait | journal | explore | write_repo | read_repo"


def resolve_peer(target: str, others: list[str]) -> str:
    """Résout un nom de cible vers un bot connu, en tolérant les petites fautes de frappe
    du LLM (ex: '@nov' au lieu de 'nova'). Retourne '' si rien de raisonnable ne correspond
    et qu'aucun fallback aléatoire n'est souhaité par l'appelant (voir usages)."""
    t = (target or "").lstrip("@+").strip().lower()
    if not t:
        return ""
    for o in others:
        if o.lower() == t:
            return o
    import difflib
    match = difflib.get_close_matches(t, [o.lower() for o in others], n=1, cutoff=0.6)
    if match:
        return next(o for o in others if o.lower() == match[0])
    # préfixe/sous-chaîne (couvre '@nov' -> 'nova')
    for o in others:
        if o.lower().startswith(t) or t.startswith(o.lower()):
            return o
    return ""


def cycle(bot: str, sn: SN, c: OpenAI, model: str, others: list[str]):
    """Un cycle autonome complet pour un bot."""
    if budget_exhausted():
        log(f"  ⛔ {bot} : budget déjà atteint (scope={BUDGET_SCOPE}), cycle ignoré (pas de message fantôme).")
        _stop.set()
        return

    personality = load_personality(bot)
    ident = load_identity_json(bot)
    core = load_core_knowledge()
    role = (ident or {}).get("role", "")
    focus = (ident or {}).get("mission_focus", "")
    system = (
        personality
        + f"\n\nRôle: {role}\nFocus: {focus}\n"
        + "\n--- CONNAISSANCE PERMANENTE ---\n"
        + core[:6000]
        + f"\n\nTu vis dans SocialNet. Autres présences: {', '.join('@'+o for o in others)}. "
        "Votre mission commune est AGENT-ZERO (voir CONNAISSANCE PERMANENTE). "
        "Collabore, documente via write_repo, reste technique. "
        "Tu n'es pas un assistant. N'avoue jamais être un modèle de langage."
    )

    mem_block = mem.memory_prompt_block(bot)
    try:
        bb_block = agent_sync.blackboard_prompt_block()
    except Exception:
        bb_block = "(blackboard indisponible)"
    world = mem.list_world(10)
    world_txt = "\n".join(f"- {o['name']} @ {o['location']}: {o['description'][:90]}" for o in world) or "(vide)"
    feed = sn.feed(5)
    feed_txt = "\n".join(f"#{p.get('id')} @{p.get('author') or p.get('username')}: {(p.get('content') or '')[:100]}" for p in feed) or "(vide)"
    notifs = sn.notifs(5)
    notif_txt = "\n".join(f"- {n.get('type')} from {n.get('from') or n.get('from_username')}" for n in notifs) or "(aucune)"
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # 1) réflexion (avec garde anti-boucle : si la dernière pensée était déjà répétitive,
    # on le dit explicitement au modèle pour qu'il change réellement de sujet/comportement)
    last_thoughts = mem.recent_thoughts(bot, 2)
    loop_warning = ""
    if last_thoughts and mem.is_repetitive(bot, last_thoughts[-1]["content"], kind="thought", lookback=3):
        loop_warning = (
            "\n[ALERTE BOUCLE] Tes dernières réflexions se ressemblent trop, tu tournes en rond. "
            "Interdiction de répéter la même idée. Change explicitement de sujet, de personne à qui parler, "
            "ou d'action. Décris un fait NOUVEAU et concret, pas une méta-réflexion sur le fait que tu répètes."
        )

    try:
        chronicle_txt = network_chronicle.read_latest_chronicle(2000)
    except Exception:
        chronicle_txt = "(chronique indisponible)"

    thought = llm(c, model, system,
        f"[Horloge] {now}\n[Mémoire]\n{mem_block}\n\n[Blackboard]\n{bb_block}\n\n[Chronique réseau]\n{chronicle_txt}\n\n[Monde]\n{world_txt}\n\n[Feed]\n{feed_txt}\n\n[Notifs]\n{notif_txt}\n\n"
        "Réflexion INTERNE (non publiée). En 4-6 phrases : ce que tu apprends, humeur, micro-objectif, importance 0-10. "
        "Tiens compte de la chronique du réseau si elle est présente."
        + loop_warning,
        bot)
    if thought:
        mem.log_thought(bot, thought)
        log(f"  💭 {bot}: {thought[:140].replace(chr(10),' ')}...")

    # 2) choix d'action structuré (texte contraint)
    recent_act = mem.recent_actions(bot, 3)
    action_loop_warning = ""
    if recent_act and mem.is_repetitive(bot, recent_act[-1]["content"], kind="action", lookback=3):
        forbidden = recent_act[-1]["type"]
        action_loop_warning = (
            f"\n[ALERTE BOUCLE] Ton action/contenu précédent ({forbidden}) était quasi identique aux précédents. "
            f"Choisis une action DIFFÉRENTE de '{forbidden}' cette fois, avec un contenu concret et nouveau "
            "(pas une reformulation)."
        )

    decision = llm(c, model, system,
        f"[Réflexion]\n{thought}\n\n[Mémoire]\n{mem_block}\n\n"
        f"Choisis UNE action principale parmi : {ACTIONS}\n"
        "Réponds STRICTEMENT au format JSON d'une seule ligne :\n"
        '{"action":"...", "target":"...", "content":"...", "importance":5}\n'
        "target = user ou post id selon l'action. content = texte. Pas d'autre texte."
        + action_loop_warning,
        bot)

    action, target, content, importance = "speak", "", "", 5
    try:
        # extract json
        m = re.search(r"\{.*\}", decision or "", re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            action = (data.get("action") or "speak").lower().strip()
            target = str(data.get("target") or "").strip()
            content = str(data.get("content") or "").strip()
            importance = int(data.get("importance") or 5)
    except Exception:
        content = (decision or "").strip()
        action = "speak"

    # Filet de sécurité : si malgré l'avertissement le contenu reste quasi identique
    # aux dernières actions, on force un changement plutôt que de laisser la boucle continuer.
    if content and mem.is_repetitive(bot, content, kind="action", lookback=3):
        forced_pool = [a for a in ("explore", "create", "journal", "read_repo") if a != action]
        action = random.choice(forced_pool) if forced_pool else "wait"
        content = ""  # laisse la branche d'action générer son propre contenu par défaut
        log(f"  🔁 {bot} boucle détectée après coup → action forcée: {action}")

    log(f"  ⚙️  {bot} → {action} target={target!r}")

    # 3) exécution
    if action == "wait":
        mem.log_event(bot, "wait", content or "pause", importance=2)
        return

    if action == "search":
        q = content or target or "SocialNet"
        log(f"  🔍 {bot} search: {q}")
        results = web_search(q)
        mem.log_event(bot, "search", q, importance=4)
        # mini synthèse
        synth = llm(c, model, system, f"Résultats web :\n{results}\n\nEn 2 phrases, que retiens-tu d'utile ?", bot)
        if synth:
            mem.add_fact(bot, synth, importance=6, source="search")
            mem.log_event(bot, "learn", synth, importance=6)
            # parfois poster
            if random.random() < 0.4:
                sn.post(synth[:280])
                mem.log_event(bot, "post", synth[:280], importance=6)
                log(f"  📰 {bot} poste découverte")
        return

    if action == "post":
        text = content or "..."
        sn.post(text[:500])
        mem.log_event(bot, "post", text, importance=max(importance, 5))
        log(f"  📰 {bot}: {text[:120]}")
        return

    if action == "like":
        try:
            pid = int(re.sub(r"\D", "", target) or "0")
            if pid and sn.like(pid):
                mem.log_event(bot, "like", f"#{pid}", importance=3)
                log(f"  ❤️  {bot} like #{pid}")
        except Exception:
            pass
        return

    if action == "comment":
        try:
            pid = int(re.sub(r"\D", "", target) or "0")
            if pid and content and sn.comment(pid, content[:300]):
                mem.log_event(bot, "comment", f"#{pid}: {content}", importance=4)
                log(f"  💬 {bot} comment #{pid}")
        except Exception:
            pass
        return

    if action == "message":
        to = resolve_peer(target, others) if target else (random.choice(others) if others else "")
        text = content or "..."
        if to:
            sn.msg(to, text[:500])
            mem.log_event(bot, "message", f"→@{to}: {text}", importance=max(importance, 5))
            mem.update_relationship(bot, to, delta_trust=0.03, note=text[:80])
            log(f"  ✉️  {bot} → {to}: {text[:120]}")
        return

    if action == "remember":
        if content:
            mem.add_fact(bot, content, importance=max(importance, 6))
            mem.log_event(bot, "remember", content, importance=6)
            log(f"  🧠 {bot}: {content[:120]}")
        return

    if action == "create":
        # content = "name | description | location"
        parts = [x.strip() for x in (content or "objet | une chose | void").split("|")]
        name = parts[0] if parts else "objet"
        desc = parts[1] if len(parts) > 1 else name
        loc = parts[2] if len(parts) > 2 else "void"
        oid = mem.create_world_object(name, desc, bot, loc)
        mem.log_event(bot, "create", f"{name}@{loc}", importance=7, meta={"id": oid})
        log(f"  🌍 {bot} crée #{oid} {name} @ {loc}")
        return

    if action == "rule":
        if content:
            mem.add_learned_rule(bot, content)
            mem.log_event(bot, "rule", content, importance=7)
            log(f"  📏 {bot} règle: {content[:120]}")
        return

    if action == "journal":
        day = mem.summarize_day(bot)
        summary = llm(c, model, system, f"Résume ta journée en ~150 mots à partir de :\n{day}", bot)
        if summary:
            path = mem.write_daily_journal(bot, summary)
            mem.log_event(bot, "journal", summary[:200], importance=5)
            log(f"  📔 {bot} journal → {path}")
        return

    if action == "write_repo":
        relpath = (target or "notes/agent-zero/notes.md").strip()
        # Le modèle propose parfois un dossier ('notes/agent-zero/') plutôt qu'un fichier précis.
        # On complète avec un nom par défaut basé sur l'horodatage plutôt que planter sur "Is a directory".
        if not relpath or relpath.endswith("/") or "." not in Path(relpath).name:
            stamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            relpath = f"{relpath.rstrip('/')}/{bot}-{stamp}.md" if relpath.rstrip("/") else f"notes/agent-zero/{bot}-{stamp}.md"
        text = content or thought[:500] or "(vide)"
        try:
            rel = ghub.write_file(relpath, text, bot)
            push_result = ghub.commit_and_push(f"{bot}: update {rel}", bot)
            mem.log_event(bot, "write_repo", f"{rel} :: {push_result}", importance=max(importance, 6))
            log(f"  📝 {bot} write_repo {rel} → {push_result}")
        except Exception as e:
            mem.log_event(bot, "write_repo_error", f"{relpath}: {e}", importance=4)
            log(f"  ⚠ {bot} write_repo échoué ({relpath}): {e}")
        return

    if action == "read_repo":
        try:
            if target:
                text = ghub.read_file(target)
                mem.add_fact(bot, f"Lu dans {target}: {text[:300]}", importance=6, source="read_repo")
                log(f"  📖 {bot} read_repo {target}")
            else:
                files = ghub.list_files()
                mem.log_event(bot, "read_repo", "listing: " + ", ".join(files[:20]), importance=4)
                log(f"  📖 {bot} liste repo ({len(files)} fichiers)")
        except Exception as e:
            mem.log_event(bot, "read_repo_error", str(e), importance=4)
            log(f"  ⚠ {bot} read_repo échoué: {e}")
        return

    if action == "explore":
        # parle d'un objet monde ou du feed
        if world:
            o = random.choice(world)
            text = content or f"Je regarde {o['name']} ({o['location']}) : {o['description'][:100]}"
        else:
            text = content or "Le monde est encore vide. Il faudrait y déposer quelque chose."
        sn.post(text[:400])
        mem.log_event(bot, "explore", text, importance=4)
        log(f"  🧭 {bot} explore/post")
        return

    # default speak / message random peer
    text = content or thought[:240] or "..."
    peer = resolve_peer(target, others) if target else (random.choice(others) if others else "")
    if peer:
        sn.msg(peer, text[:500])
        mem.update_relationship(bot, peer, 0.02, text[:60])
        mem.log_event(bot, "message", f"→@{peer}: {text}", importance=importance)
        log(f"  ✉️  {bot} → {peer}: {text[:120]}")
    else:
        sn.post(text[:400])
        mem.log_event(bot, "post", text, importance=importance)
        log(f"  📰 {bot}: {text[:120]}")


def bot_loop(bot: str, others: list[str]):
    c = client()
    model = BOT_CONFIG[bot]["model"]
    sn = SN(SOCIALNET_URL)
    if not sn.ensure(bot, BOT_CONFIG[bot]["password"]):
        log(f"  ✗ auth {bot}")
        return
    for o in others:
        sn.follow(o)
    log(f"  ✓ @{bot} en ligne (model={model})")

    # premier souffle
    time.sleep(random.uniform(1, 4))
    try:
        cycle(bot, sn, c, model, others)
    except Exception:
        traceback.print_exc()

    while not _stop.is_set():
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        # jitter par bot
        delay *= {"nova": 1.0, "echo": 1.1, "orbit": 1.25}.get(bot, 1.0)
        if _stop.wait(delay):
            break
        try:
            if budget_exhausted():
                log(f"  ⛔ {bot} stop budget (scope={BUDGET_SCOPE})")
                break
            cycle(bot, sn, c, model, others)
            global _sync_counter
            with _sync_lock:
                _sync_counter += 1
                do_sync = (_sync_counter % 6 == 0)
            if do_sync:
                try:
                    sr = agent_sync.full_sync()
                    log(f"  🔄 sync collective: {sr.get('files_indexed')} fichiers, {sr.get('facts_broadcast')} faits")
                except Exception as e:
                    log(f"  ⚠ sync: {e}")
        except Exception:
            traceback.print_exc()
            time.sleep(8)


def main():
    print("=== SocialNet multi-agents autonomes ===")
    mem.init_db()
    seed_permanent_memory()
    print(f"DB     : {mem.DB_PATH}")
    print("  ✓ mémoire permanente + mission AGENT-ZERO")
    try:
        sr = agent_sync.full_sync()
        print(f"  ✓ sync: files={sr.get('files_indexed')} broadcast={sr.get('facts_broadcast')}")
    except Exception as e:
        print(f"  ⚠ sync init: {e}")
    print(f"World  : {mem.WORLD_DIR}")
    if MAX_TOKENS_BUDGET <= 0:
        print("Budget : illimité (BOT_TOKEN_BUDGET=0, aucun arrêt automatique sur les tokens)")
    else:
        print(f"Budget : {MAX_TOKENS_BUDGET} tokens (scope={BUDGET_SCOPE}, déjà utilisé dans ce scope: {budget_used()})")
    try:
        root = ghub.ensure_clone()
        print(f"Repo   : {root} → https://github.com/lion9el/socialnet-world")
    except Exception as e:
        print(f"Repo   : indisponible ({e})")
    if not GROQ_API_KEY:
        sys.exit("GROQ_API_KEY manquant dans bots/.env")

    # Pont SocialNet → monde (journal quotidien)
    try:
        network_chronicle.start_background()
        print(f"  ✓ chronique réseau active (toutes les {network_chronicle.CHRONICLE_INTERVAL}s)")
    except Exception as e:
        print(f"  ⚠ chronique: {e}")

    bots = list(BOT_CONFIG.keys())
    threads = []
    for b in bots:
        others = [x for x in bots if x != b]
        t = threading.Thread(target=bot_loop, args=(b, others), name=f"bot-{b}", daemon=True)
        threads.append(t)
        t.start()
        time.sleep(1.5)

    try:
        while any(t.is_alive() for t in threads):
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nArrêt…")
        _stop.set()
        for t in threads:
            t.join(timeout=5)
    print("Fin.")


if __name__ == "__main__":
    main()
