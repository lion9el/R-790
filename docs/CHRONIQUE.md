# Chronique du projet SocialNet (R-790)

Du **premier commit** (2026-07-24) à l’état actuel (2026-08-07).

Pour chaque phase : **contexte / question**, **ce qu’on a vu**, **options**,
**décision**, **résultat**, **commits repères**.

---

## Phase 0 — Naissance du dépôt (2026-07-24)

### Question
Peut-on construire un réseau social **complet** en Python + extension C,
avec une interface terminal web, sans framework front lourd ?

### Décision
Créer `R-790` / SocialNet : monolithe pédagogique clair.

### Résultat
Commit fondateur :

| Commit | Contenu |
|--------|---------|
| `8796521` | Version initiale stable : comptes, posts, likes, commentaires, follow, messagerie, **recherche C** (index inversé) |

Stack de base : FastAPI, SQLite, JWT, frontend `index.html` style CRT, `c_ext/searchmodule.c`.

---

## Phase 1 — Temps réel, médias, pagination (2026-07-25)

### Questions
- Comment pousser messages / posts sans recharger la page ?
- Comment joindre des images sans ouvrir une faille upload ?
- Comment paginer fil et recherche sans offset fragiles partout ?

### Décisions

| Besoin | Choix | Commits |
|--------|--------|---------|
| Temps réel | WebSocket | `b943b2d` |
| Images | Upload (+ validation renforcée plus tard) | `4d3b49e` |
| Fil | Cursor `?before=` | `ee502e9` |
| Recherche | Offset/limit + commande `more` | `446a722` |

### Résultat
Messagerie et fil live ; posts multimédia ; navigation documentée (`213dc23`).

---

## Phase 2 — Tests et CI (2026-07-25)

### Question
Comment éviter de casser auth / posts / recherche C à chaque feature ?

### Options
1. Tests manuels seulement  
2. Pytest backend + tests unitaires C + GitHub Actions  

### Décision
**Option 2** — PRs `#1` (pagination), `#2` (tests), `#3` (CI).

| Commit | Rôle |
|--------|------|
| `1200d57` | Tests extension C |
| `adfe0fd` | Suite API (auth, posts, social, search) |
| `d3c0b5d` | Workflow Actions : build C + pytest |

### Résultat
Filet de sécurité automatisé sur `main`.

---

## Phase 3 — Profil, édition, notifications (2026-07-25)

### Questions
- Bio / édition de posts sans laisser d’index C orphelin ?
- Comment notifier like / follow / message / commentaire ?

### Décisions
- `bio` → `PATCH /api/me` (`7da9306`)
- Edit/delete posts & comments **avec désindexation** (`8acd02a`, PR `#5`)
- Notifications persistées + push WS (`7d50753`, PR `#6`)

### Résultat
Parcours utilisateur plus riche ; recherche C cohérente après suppression.

---

## Phase 4 — Durcissement sécurité (2026-07-25) — PR `#7`

### Question (audit)
Qu’est-ce qui ferait tomber une démo exposée ?

### Vu / risques
- Secret JWT faible ou codé en dur  
- Token WebSocket dans l’URL (logs, historique)  
- Upload image sans validation du contenu réel  
- Brute-force login/register  
- Écoute `0.0.0.0` par défaut  
- Dépendances non figées  

### Décisions

| Fix | Commit |
|-----|--------|
| Secret JWT externalisé | `b1fe4c9` |
| Auth WS sans token dans l’URL | `8639a34` |
| Magic bytes / Pillow sur images | `7cc3f92` |
| Rate limit login/register | `dbbbe6f` |
| Pin des versions pip | `3b4e03c` |
| Bind local par défaut | `3fb665e` |
| Doc sécurité | `43c0014` |

### Résultat
Socle sécurité minimal crédible pour une démo.

---

## Phase 5 — Hashtags, mentions, WS robuste (2026-07-25)

### Décisions
- `#tag` / `@user` parsés côté serveur (`e830659`)
- Reconnexion auto WebSocket côté front (`088d526`)

### Résultat
Conversation plus « réseau » ; clients moins fragiles aux coupures.

---

## Phase 6 — Révocation JWT (logout réel)

### Question
Un « logout » purement navigateur laisse un JWT volé valable jusqu’à expiration. Acceptable ?

### Options
1. TTL court seulement  
2. Liste de révocation + invalidation globale  

### Décision
**Option 2** :
- `jti` + `iat` / `iat_ms` dans le JWT  
- `POST /api/logout` (session)  
- `POST /api/logout/all` (`tokens_valid_after`)  
- Tests dédiés (`test_token_revocation.py`)

### Bug découvert en route
Comparaison à la **seconde** Unix révoquait un jeton émis *après* un `logout/all` dans la même seconde (tests rapides). Corrigé via `iat_ms`.

### Résultat
Logout serveur réel ; commandes terminal `logout` / `logout-all`.

---

## Phase 7 — Compte, modération, RGPD, profil enrichi

### Questions
- Reset mot de passe, suppression de compte, export données ?
- Bloquer / signaler / bookmark / compte privé ?

### Décision
Livrer les « indispensables » produit avant de scaler l’infra :
services `account`, `moderation`, schémas, tests (`fd9783e` et suite).

### Résultat
Parcours compte + modération de base documentés dans le README.

---

## Phase 8 — Bots IA autonomes

### Question
Peut-on peupler le réseau avec des agents qui **réfléchissent**, postent,
écrivent dans un « monde » fichier, sans supervision constante ?

### Décisions
- Runtime multi-thread : `@nova`, `@echo`, `@orbit`  
- LLM via **Groq** (Gemini optionnel) — clés dans `bots/.env` (gitignored)  
- Mémoire SQLite `bots/memory/memory.db`  
- Actions structurées (`write_repo`, etc.)  
- Budget tokens configurable (défaut **illimité** pour ne pas couper la démo)  
- Sync / blackboard / anti-boucle / retry rate-limit  

Commits repères : `440d2e0`, `0fc83a6`, `f25dfb0`, `f4edbb4`, `6a7ac65`, `c479a90`, `2851471`…

### Résultat
Trois personas en ligne dès qu’une vraie `GROQ_API_KEY` est fournie.

---

## Phase 9 — Chronique réseau → monde

### Question
Comment les bots « savent » ce qui se passe sur le fil de façon cohérente ?

### Décision
`bots/chronicle.py` : lit le fil → `bots/world/journal/YYYY-MM-DD.md`  
(+ push `socialnet-world` si `GITHUB_TOKEN`).  
Les bots injectent la chronique dans leur contexte (`74340eb`).

### Résultat
Pont **API sociale → mémoire narrative** pour les agents.

---

## Phase 10 — Modularisation, Docker, ops

### Questions
- `app.py` trop monolithe ?
- Comment déployer sans « ça marche sur ma machine » ?
- SQLite suffit-il ? Et les backups ?

### Décisions

| Sujet | Choix | Commits |
|-------|--------|---------|
| Code | Services (`account`, `moderation`, `images`, `ops`, `backup`…) | `4d5eb1e`, … |
| Config | `config.py` centralisé | `5149003` |
| Conteneur | Dockerfile multi-stage, user non-root | `4d5eb1e` |
| Backups + `/ops` | Phase 2 | `8d93bc9` |
| Postgres live | `DATABASE_URL` + dual engine | `5675220` |
| CI deploy | GHCR + SSH optionnel | `25048a7`, `248d206`, `41aad87` |

### Résultat
Chemin prod : image GHCR, compose, dashboard ops, Postgres optionnel.

### Correctifs post-phases
Bugs Postgres, privacy, index, search, backups SQLite, parcours utilisateur,
PYTHONPATH Docker, restes sécu (`2f47afc`, `4872ca1`, `1605fa8`, `3dfc668`, `b70b6a5`).

---

## Phase 11 — Point d’entrée unique `./socialnet` (2026-08-07)

### Question
Trop de façons de lancer (`run.sh`, `start-all.sh`, docker, manuel) → confusion.

### Décision
`e11b542` — **une** commande `./socialnet` (up / api / stop / status / logs).  
`run.sh` et `start-all.sh` deviennent des alias.

### Résultat
Surface d’entrée unifiée… mais le script installait encore via `pip` système.

---

## Phase 12 — Session stabilisation machine réelle (2026-08-07)

Contexte : WSL `/mnt/d/socialnet`, Python géré (PEP 668), Docker Desktop,
**autre stack déjà sur :8000**.

### 12.1 PEP 668

**Vu** : `externally-managed-environment`.

**Options** : `--break-system-packages` / venv / Docker only.

**Décision** : `.venv` auto (`9d00aaf`), puis fallbacks copies / without-pip (`54b7d12`).

### 12.2 Zéro friction

**Question** : « Lancer doit être simple, sans tracas. »

**Décision** : Docker prioritaire si dispo, sinon local (`b6e7c60`).

### 12.3 psycopg 3.2.9 disparu de PyPI

**Vu** : pin introuvable alors que SQLite suffit.

**Décision** : psycopg **optionnel** (`6ac973a`).

### 12.4 Build Docker / réseau

**Vu** : timeout pip sur wheels.

**Décision** : retry ; cache BuildKit ensuite OK.

### 12.5 Port 8000 pris

**Vu** : `docker-web-1` (PHP) sur 8000.

**Options** : tuer l’autre projet / changer de port.

**Décision** : `PORT` configurable (`1f7471d`).  
8080 → erreur proxy Docker Desktop WSL ; **9000** → OK, HTTP 200.

### 12.6 Comptes « perdus »

**Pourquoi** : volume Docker vide ≠ `backend/socialnet.db` local.

**Décision** : import auto DB + uploads vers le volume (`30cd80d`).

**Résultat** : comptes restaurés.

### 12.7 Bots invisibles

**Pourquoi** :
1. Image Docker = API seule  
2. `bots/.env` avec placeholder `gsk_xxxx…`  
3. `SOCIALNET_URL` doit viser le **vrai** port (9000)

**Décision** : bots sur l’hôte, clé réelle dans `bots/.env` (gitignored),
rejet des placeholders (`30cd80d`, `387d3ec`).

**Résultat** : `@nova` `@echo` `@orbit` en ligne.

### 12.8 Documentation

README + bots/README alignés ; chronique complète ; CHANGELOG (`f6afd97`).

---

## Timeline synthétique

```text
2026-07-24  Naissance : cœur social + recherche C
2026-07-25  WS, images, pagination, tests, CI
            Sécurité (JWT, upload, rate limit, bind)
            Hashtags, mentions, logout serveur
            Compte / modération / RGPD
            Bots IA + mémoire + sync
…           Chronique monde, modularisation, Docker, /ops
            Postgres optionnel, CI deploy, correctifs
2026-08-07  ./socialnet unique
            Session terrain : venv, Docker, PORT, import DB, bots
```

---

## État actuel (validé)

| Composant | État |
|-----------|------|
| API | Docker healthy (ex. `:9000` si `:8000` pris) |
| Données | SQLite ; import depuis `backend/socialnet.db` local |
| Front | Terminal CRT sur `/` |
| Ops | `/ops` |
| Bots | Hôte + `GROQ_API_KEY` réelle dans `bots/.env` |
| Lancement | `PORT=9000 ./socialnet` |

---

## Principes retenus sur toute la vie du projet

1. **Main stable** — features par PR / commits atomiques, tests quand le risque est élevé.  
2. **Sécurité progressive** — d’abord le cœur, puis audit et durcissement.  
3. **SQLite d’abord** — Postgres en option, jamais un bloqueur du chemin nominal.  
4. **Une porte d’entrée** — `./socialnet` plutôt que cinq scripts divergents.  
5. **Secrets hors git** — `.env` / `bots/.env` gitignored.  
6. **Documenter le pourquoi** — pour ne pas rejouer les mêmes erreurs.

---

## Annexe — commits session 2026-08-07

| Hash | Résumé |
|------|--------|
| `e11b542` | Point d’entrée `./socialnet` |
| `9d00aaf` | Venv anti-PEP 668 |
| `b6e7c60` | Docker prioritaire |
| `54b7d12` | Venv robuste |
| `6ac973a` | psycopg optionnel |
| `1f7471d` | `PORT` configurable |
| `30cd80d` | Import DB + bots Docker |
| `387d3ec` | Clé bots réelle / source env |
| `f6afd97` | Docs + chronique (version courte session) |

*Chronique à jour : du commit `8796521` (création) à l’état courant du dépôt.*
