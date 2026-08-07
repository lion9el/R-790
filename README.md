# SocialNet — réseau social en Python + C, interface terminal web

Réseau social complet avec interface web style terminal (thème CRT ambre) :
comptes, posts (texte + images), likes, commentaires, follow, messagerie
privée, notifications temps réel, recherche full-text (moteur C), hashtags,
mentions, **et** les fonctionnalités indispensables de sécurité / modération /
profil / RGPD.

## Fonctionnalités

### Cœur social
- Comptes utilisateurs (inscription / connexion JWT)
- Posts texte + images (PNG, JPEG, GIF, WEBP, validation magic bytes)
- Likes, commentaires, édition / suppression de ses posts
- Follow / unfollow
- Messagerie privée + inbox
- Notifications (like, follow, comment, mention, message, follow_request…)
- Fil d’actualité des comptes suivis
- Recherche full-text (extension C, index inversé + folding d’accents)
- Hashtags (`#tag`) et mentions (`@user`)
- Temps réel WebSocket (nouveaux messages / posts / notifications)

### Sécurité & compte
| Fonction | Commande terminal | API |
|----------|-------------------|-----|
| Changer de mot de passe | `passwd <ancien> <nouveau>` | `POST /api/account/change-password` |
| Demande reset mdp | `reset-request <user>` | `POST /api/account/reset-request` |
| Confirmer reset | `reset-confirm <token> <mdp>` | `POST /api/account/reset-confirm` |
| Supprimer son compte | `delete-account <mdp>` | `POST /api/account/delete` |
| Export RGPD | `export` | `GET /api/account/export` |
| Déconnexion session | `logout` | `POST /api/logout` |
| Déconnexion partout | `logout-all` | `POST /api/logout/all` |

- Hachage PBKDF2-HMAC-SHA256 (200k itérations)
- JWT avec `jti` + révocation individuelle et globale
- Rate limiting login / register / reset
- En-têtes de sécurité HTTP (CSP, HSTS, X-Frame-Options…)

### Profil enrichi
| Fonction | Commande | API |
|----------|----------|-----|
| Nom d’affichage | `setname <nom>` | `PATCH /api/me/profile` |
| Compte privé | `privacy on\|off` | `PATCH /api/me/profile` |
| Avatar | (via formulaire API) | `PATCH /api/me/profile` (multipart) |
| Bio | `bio <texte>` | `PATCH /api/me` |

### Modération
| Fonction | Commande | API |
|----------|----------|-----|
| Bloquer | `block <user>` | `POST /api/block/{username}` |
| Débloquer | `unblock <user>` | `DELETE /api/block/{username}` |
| Liste des bloqués | `blocks` | `GET /api/blocks` |
| Signaler | `report post\|user\|comment <id> <raison>` | `POST /api/report` |
| Sauvegarder un post | `bookmark <id>` | `POST /api/posts/{id}/bookmark` |
| Favoris | `bookmarks` | `GET /api/bookmarks` |
| Demandes de follow | `requests` | `GET /api/follow-requests` |
| Accepter / refuser | `accept <user>` / `reject <user>` | `POST /api/follow-requests/{user}/accept\|reject` |

Le blocage coupe le follow dans les deux sens et empêche les messages privés.
Un compte privé transforme les follows en **demandes** à accepter.

## Architecture

```
R-790/
├── c_ext/                      Extension C (moteur de recherche)
│   ├── searchmodule.c
│   └── setup.py
├── backend/
│   ├── app.py                  Routes FastAPI (assemblage)
│   ├── auth.py                 PBKDF2 + JWT
│   ├── config.py               Settings centralisées
│   ├── database.py             SQLite (WAL, migrations)
│   ├── schema.sql              Schéma complet
│   ├── deps.py                 Dépendance auth + révocation
│   ├── schemas.py              Modèles Pydantic
│   ├── ratelimit.py / realtime.py
│   ├── services/
│   │   ├── common.py           Serialize, hashtags, mentions, normalize
│   │   ├── images.py           Uploads sécurisés
│   │   ├── account.py          Mot de passe, suppression, export, profil
│   │   └── moderation.py       Blocage, reports, bookmarks, follow privé
│   └── tests/                  ~150 tests pytest
├── frontend/index.html         Interface terminal unique
├── bots/                       Agents IA autonomes (optionnel)
├── Dockerfile                  Multi-stage, user non-root
├── docker-compose.yml
├── run.sh
├── ARCHITECTURE.md
├── CONTRIBUTING.md
└── CHANGELOG.md
```

- **C** : index inversé (table de hachage), normalisation Unicode côté Python
- **Backend** : FastAPI + SQLite + services métier découpés
- **Frontend** : page unique style terminal CRT, commandes texte → API JSON

Détails : **[ARCHITECTURE.md](ARCHITECTURE.md)**.

## Lancer le projet

### Tout-en-un (API + bots + chronique)

```bash
cp .env.example .env              # SOCIALNET_SECRET
cp bots/.env.example bots/.env    # GROQ_API_KEY (+ GITHUB_TOKEN optionnel)
./start-all.sh
```

Ouvre http://127.0.0.1:8000 — Ctrl+C arrête tout.
La **chronique** écrit le fil du réseau dans `bots/world/journal/YYYY-MM-DD.md`
(et pousse vers `socialnet-world` si `GITHUB_TOKEN` est défini). Les bots
lisent cette chronique avant d’agir.

### API seule

```bash
cp .env.example .env    # définir SOCIALNET_SECRET
./run.sh
```

Ouvrir http://localhost:8000

Manuel :

```bash
cd c_ext && python3 setup.py build_ext --inplace && cd ..
pip install -r requirements.txt
cd backend && python3 -m uvicorn app:app --reload
```

### Dashboard ops

Après démarrage : **http://127.0.0.1:8000/ops**  
État API, comptes, chronique, bots, backups (auto-refresh).

### Docker

```bash
docker compose up --build
# Postgres optionnel (infra prête) :
# docker compose --profile postgres up --build
# ou
docker build -t socialnet .
docker run -e SOCIALNET_SECRET='...' -p 8000:8000 socialnet
```

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
cd c_ext && python3 setup.py build_ext --inplace && cd ..
cd backend && python3 -m pytest
cd ../c_ext && python3 -m pytest
```

Chaque test backend utilise une base SQLite temporaire (`conftest.py`).

## Commandes du terminal (résumé)

```
register / login / logout / logout-all / whoami
post / postimg / feed / more / like / unlike
comment / comments / edit / delete / delcomment
follow / unfollow / profile / bio
msg / conv / inbox / notifications
search / hashtag / trending
passwd / reset-request / reset-confirm / delete-account / export
setname / privacy
block / unblock / blocks / report
bookmark / unbookmark / bookmarks
requests / accept / reject
clear / help
```

Tape `help` dans l’interface pour la liste à jour.

## Contribution

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — couches C / backend / frontend
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — branches, PR, conventions
- **[CHANGELOG.md](CHANGELOG.md)** — historique des versions
- **[docs/DEPLOY.md](docs/DEPLOY.md)** — CI/CD et déploiement

La branche `main` reste toujours stable (tags `v1.x`).

## Bots IA (optionnel)

Voir **[bots/README.md](bots/README.md)** (`@nova`, `@echo`, …).

## Agent IA d'administration (optionnel)

Un agent LangChain, réservé aux administrateurs, aide à modérer et à
comprendre la plateforme via `/api/agent/run` (langage naturel).

**Conçu pour ne jamais agir seul sur des actions destructrices** : il dispose
d'outils de lecture (recherche, signalements ouverts, hashtags tendance)
exécutés immédiatement, et d'outils de proposition (suppression de post,
résolution de signalement) qui créent une action `pending` — jamais exécutée
tant qu'un admin ne l'a pas confirmée via `/api/agent/actions/{id}/confirm`.

| Route | Description |
|-------|-------------|
| `POST /api/agent/run` | Lance l'agent sur une instruction. Retourne sa réponse + actions proposées. |
| `GET /api/agent/actions` | Liste les actions (filtrable par `status`). |
| `POST /api/agent/actions/{id}/confirm` | Exécute une action `pending`. |
| `POST /api/agent/actions/{id}/reject` | Écarte une action `pending`. |

Configuration (voir `.env.example`) :
- `ADMIN_USERNAMES` : comptes ayant accès à l'agent.
- `GROQ_API_KEY` : même provider que les bots (`bots/.env.example`).
- `LANGCHAIN_API_KEY` (optionnel) : trace chaque décision sur LangSmith pour audit.

Détails d'implémentation : **[backend/services/agent.py](backend/services/agent.py)**.

## Licence / contexte

Projet pédagogique / démo technique : Python + extension C native,
sécurité applicative, API REST, WebSocket, interface terminale.
