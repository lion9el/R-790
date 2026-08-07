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

**Une seule commande.**

```bash
cp .env.example .env          # une fois (SOCIALNET_SECRET)
./socialnet                   # Docker si dispo, sinon local
```

| Commande | Effet |
|----------|--------|
| `./socialnet` | Auto : Docker prioritaire, sinon local (venv) |
| `./socialnet docker` | Force Docker |
| `./socialnet local` | Force local (`.venv` auto) |
| `./socialnet api` | API seule, `--reload` (dev) |
| `./socialnet stop` | Arrête API + bots |
| `./socialnet status` | Santé |
| `./socialnet logs` | Derniers logs |

Équivalents historiques : `./run.sh` · `./start-all.sh` → redirigent vers `./socialnet`.

### Port déjà pris (ex. autre conteneur sur 8000)

```bash
PORT=9000 ./socialnet
# → http://127.0.0.1:9000
```

Le fichier `docker-compose.yml` mappe `${PORT:-8000}:8000`.

### Docker

- Image multi-stage (Python 3.12-slim + extension C compilée)
- Volume `socialnet-data` : base SQLite + uploads
- **Import auto** : si `backend/socialnet.db` existe sur l’hôte, il est copié dans le volume au démarrage
- Postgres optionnel : `docker compose --profile postgres up --build`

### Mode local

Le script crée `.venv`, installe les deps, compile l’extension C.  
Prérequis éventuels : `python3-venv` (souvent déjà présent).  
`psycopg` est **optionnel** (SQLite par défaut) — son absence ne bloque plus le démarrage.

### Bots IA

Nécessitent une **vraie** `GROQ_API_KEY` dans `bots/.env` (fichier local, gitignored) :

```bash
# https://console.groq.com → Create API Key
echo 'GROQ_API_KEY=gsk_...' >> bots/.env
```

Avec Docker ou local, `./socialnet` démarre les bots automatiquement si la clé est valide  
(placeholders `gsk_xxxx…` sont ignorés). Sinon :

```bash
export SOCIALNET_URL=http://127.0.0.1:9000   # port réel de l’API
.venv/bin/python bots/runtime.py
```

Détails : **[bots/README.md](bots/README.md)**.

### Dashboard ops

**http://127.0.0.1:<port>/ops** — état API, comptes, backups.

La **chronique** (`bots/chronicle.py`) écrit le fil dans `bots/world/journal/YYYY-MM-DD.md`.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cd c_ext && ../.venv/bin/python setup.py build_ext --inplace && cd ..
cd backend && ../.venv/bin/python -m pytest
```

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

## Documentation

| Doc | Contenu |
|-----|---------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Couches C / backend / frontend |
| [bots/README.md](bots/README.md) | Agents IA, mémoire, chronique |
| [docs/DEPLOY.md](docs/DEPLOY.md) | CI/CD, GHCR, VPS |
| [docs/CHRONIQUE.md](docs/CHRONIQUE.md) | Journal de session : problèmes, options, décisions |
| [CHANGELOG.md](CHANGELOG.md) | Historique des versions |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Branches, PR |

## Licence / contexte

Projet pédagogique / démo technique : Python + extension C native,
sécurité applicative, API REST, WebSocket, interface terminale.
