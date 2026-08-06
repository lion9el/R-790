# Architecture — SocialNet

Comment les couches s’articulent. À lire avant de modifier le code.

## Vue d’ensemble

```
┌─────────────────────────────────────────────────────────────┐
│  Navigateur (frontend/index.html)                            │
│  Terminal web CRT — commandes texte → JSON                   │
└───────────────────────┬─────────────────────────────────────┘
                        │  HTTP/JSON + WebSocket
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Backend FastAPI                                             │
│  app.py          routes + middleware sécurité + lifespan     │
│  services/       logique métier découpée                     │
│  auth / config   JWT, PBKDF2, settings                       │
│  deps / schemas  dépendance auth, modèles Pydantic           │
└──────────┬───────────────────────────────┬──────────────────┘
           │                               │
           ▼                               ▼
┌────────────────────────┐    ┌───────────────────────────────┐
│ SQLite (schema.sql)     │    │ Extension C (searchmodule.c)   │
│ users, posts, likes,    │    │ Index inversé en mémoire       │
│ comments, follows,      │    │ index_add / search / clear     │
│ messages, notifications │    │ + normalisation Unicode Python │
│ blocks, reports,        │    └───────────────────────────────┘
│ bookmarks, follow_req,  │
│ password_reset_tokens…  │
└────────────────────────┘
```

## Principes

1. **Frontend = présentation uniquement** — HTTP/JSON, aucune logique métier.
2. **Backend = seule source de vérité métier** — validation, sécurité, règles.
3. **Services = logique réutilisable** — pas de routes HTTP dedans.
4. **Extension C = une responsabilité** — indexer / chercher du texte, rien d’autre.
5. **Migrations idempotentes** — `schema.sql` + `database._apply_migrations`.

## Détail des modules backend

| Module | Rôle |
|--------|------|
| `app.py` | Assemblage FastAPI : lifespan, middleware CSP/HSTS, routes, static |
| `auth.py` | PBKDF2-HMAC-SHA256, création/décodage JWT (`jti`, `iat_ms`) |
| `config.py` | `SOCIALNET_SECRET`, rate limits, timeouts, limites images |
| `database.py` | Connexions SQLite (WAL, busy_timeout), migrations, helpers |
| `deps.py` | `get_current_user` + vérification révocation |
| `schemas.py` | Modèles Pydantic partagés |
| `ratelimit.py` | Limitation de débit en mémoire par IP + chemin |
| `realtime.py` | `ConnectionManager` WebSocket |
| `services/common.py` | `now`, normalize search, hashtags, mentions, serialize posts |
| `services/images.py` | Upload sécurisé (MIME + magic bytes + dimensions) |
| `services/account.py` | Change/reset password, delete account, export RGPD, profil |
| `services/moderation.py` | Blocks, reports, bookmarks, follow privé / demandes |

## Schéma de données (tables principales)

| Table | Usage |
|-------|--------|
| `users` | Comptes + `display_name`, `avatar_path`, `is_private`, `tokens_valid_after` |
| `posts` / `likes` / `comments` | Contenu social |
| `follows` | Abonnements acceptés |
| `follow_requests` | Demandes vers comptes privés |
| `messages` | DM (`is_read`) |
| `notifications` | like, follow, comment, mention, message, follow_request… |
| `post_hashtags` | Index hashtags (indépendant du moteur C) |
| `revoked_tokens` | Révocation session individuelle |
| `blocks` | Blocages mutuellement exclusifs avec follow/messages |
| `reports` | Signalements (`user` \| `post` \| `comment`) |
| `bookmarks` | Posts sauvegardés |
| `password_reset_tokens` | Reset mdp (usage unique, 1h) |
| `refresh_tokens` | Préparé pour sessions longue durée |

## Flux critiques

### Authentification
1. `register` / `login` → JWT (7j) avec `jti` + `iat_ms`
2. Chaque requête protégée : signature → expiration → révocation DB
3. `logout` = révoque ce `jti` ; `logout/all` = `tokens_valid_after`
4. Changement / reset de mdp → invalide **toutes** les sessions

### Follow & compte privé
1. Cible publique → insert `follows` + notification
2. Cible privée → insert `follow_requests` (pending) + notification
3. Owner : `accept` / `reject` → passe en `follows` ou refuse
4. Blocage → coupe follows + requests + refuse les DM

### Recherche
1. À l’indexation : `normalize_for_search` (NFD, sans diacritiques) puis `searchengine.index_add`
2. À la requête : même normalisation → scores par fréquence de termes
3. Index en mémoire : reconstruit au démarrage (`lifespan`)

### Images
1. Lecture par chunks (plafond taille)
2. Vérification MIME déclaré + format Pillow réel + dimensions max
3. Nom de fichier = UUID (pas de path traversal)

## Frontend

Une page `frontend/index.html` :
- Objet `COMMANDS` : chaque commande terminal → appel API
- WebSocket `/ws` authentifié par premier message `{token}` (jamais en query string)
- Reconnexion exponentielle sauf logout volontaire ou 4401

**Ajouter une commande** : entrée dans `COMMANDS` + route API si besoin.

## Où modifier quoi

| Objectif | Fichiers |
|---------|----------|
| Règle métier / validation | `schemas.py`, route dans `app.py`, éventuellement `services/*` |
| Nouvelle table / colonne | `schema.sql` + `database._apply_migrations` |
| Compte / mdp / export | `services/account.py` + routes account |
| Blocage / report / bookmark | `services/moderation.py` + routes associées |
| Recherche / scoring | `c_ext/searchmodule.c` (+ recompile) + `normalize_for_search` |
| Commande terminal | `frontend/index.html` |
| Style CRT | bloc `<style>` de `index.html` |
| Config runtime | `config.py` + `.env` |
| Tests | `backend/tests/`, `c_ext/tests/` |

## CI / Docker

- `.github/workflows/tests.yml` — compile C + pytest backend + C
- `Dockerfile` multi-stage, user `appuser` non-root
- Voir `docs/DEPLOY.md` pour GHCR / déploiement distant

## Vérifier que rien n’est cassé

```bash
cd c_ext && python3 setup.py build_ext --inplace && python3 -m pytest -q
cd ../backend && python3 -m pytest -q
```
