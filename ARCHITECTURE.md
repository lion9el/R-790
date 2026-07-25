# Architecture — SocialNet

Ce document explique comment les différentes parties du projet s'articulent.
À lire avant de modifier le code.

## Vue d'ensemble

```
┌─────────────────────────────────────────────────────────────┐
│  Navigateur (frontend/index.html)                            │
│  Terminal web : tape des commandes, affiche le résultat      │
└───────────────────────┬────────────────────────────────────┘
                         │  fetch() en JSON, HTTP
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Backend Python — FastAPI (backend/app.py)                   │
│  Routes API : /api/register, /api/posts, /api/feed, ...      │
│  Logique métier, authentification, validation des données    │
└──────────┬───────────────────────────────┬────────────────┘
           │                                │
           ▼                                ▼
┌────────────────────────┐     ┌───────────────────────────┐
│ Base de données SQLite  │     │ Extension C (c_ext/)       │
│ (backend/database.py)   │     │ Index inversé en mémoire   │
│ Comptes, posts, likes,  │     │ pour la recherche full-text│
│ commentaires, messages  │     │ (searchmodule.c)           │
└────────────────────────┘     └───────────────────────────┘
```

## Pourquoi cette séparation

- **Le frontend ne parle qu'en HTTP/JSON.** Il ne connaît rien de la base de
  données ni du C. On peut donc le remplacer entièrement (par une vraie appli
  mobile, par exemple) sans toucher au reste.
- **Le backend Python concentre toute la logique métier** (règles, validation,
  sécurité). C'est la seule couche qui doit changer si une règle métier change
  (ex : longueur max d'un post).
- **L'extension C ne fait qu'une seule chose** : indexer du texte et répondre
  à des recherches, le plus vite possible. Elle ne connaît rien de la base de
  données, des utilisateurs ni de HTTP — elle reçoit un `(id, texte)` et
  renvoie des `(id, score)`. C'est volontairement isolé pour rester simple à
  tester et remplacer.

## Détail par dossier

### `c_ext/` — moteur de recherche en C

| Fichier | Rôle |
|---|---|
| `searchmodule.c` | Table de hachage + index inversé. Expose `index_add`, `index_remove`, `search`, `clear` à Python via l'API `Python.h`. |
| `setup.py` | Script de compilation (`python3 setup.py build_ext --inplace`) qui produit un fichier `.so` (Linux/WSL) ou `.pyd` (Windows natif). |

**Important** : le `.so` compilé n'est jamais versionné dans Git (voir
`.gitignore`). Chaque personne le recompile localement avec `run.sh` — le
binaire dépend de l'OS et de la version de Python.

### `backend/` — API Python (FastAPI)

| Fichier | Rôle |
|---|---|
| `app.py` | Toutes les routes HTTP + la route WebSocket `/ws`. Point d'entrée de l'application. |
| `auth.py` | Hachage de mot de passe (PBKDF2) et jetons de session (JWT). |
| `database.py` | Ouverture de connexion SQLite, création des tables. |
| `schema.sql` | Définition des tables (users, posts, likes, comments, follows, messages). |
| `realtime.py` | Registre des connexions WebSocket actives (`ConnectionManager`) ; pousse les événements "nouveau message" et "nouveau post" aux utilisateurs connectés. |

**Flux d'une requête type** (ex: publier un post) :
1. Le frontend envoie `POST /api/posts` avec le texte et le jeton JWT
2. `app.py` vérifie le jeton via `auth.get_current_user`
3. `app.py` insère le post en base via `database.get_conn()`
4. `app.py` appelle `searchengine.index_add()` pour indexer le nouveau texte
5. `app.py` récupère la liste des abonnés (followers) et appelle
   `realtime.manager.broadcast_to_users()` pour les notifier en direct
6. La réponse JSON est renvoyée au frontend qui a fait la requête

**Flux temps réel (WebSocket)** :
1. Après login/register, le frontend ouvre `ws://.../ws?token=<jwt>`
2. `app.py` vérifie le jeton (`auth.decode_token`) et enregistre la
   connexion dans `realtime.manager`
3. Quand quelqu'un publie un post ou envoie un message privé, la route HTTP
   correspondante appelle `manager.send_to_user()` / `broadcast_to_users()`
4. Si le destinataire a un onglet ouvert, il reçoit l'événement JSON
   instantanément ; sinon, rien ne se perd — il le verra en rafraîchissant
   (`feed`, `conv <user>`) comme avant. Le WebSocket est un "plus", pas un
   remplacement du fonctionnement classique par requêtes HTTP.

### `frontend/` — interface terminal

Une seule page HTML autonome (`index.html`), sans framework ni étape de
build. Le "moteur" de commandes est l'objet JS `COMMANDS` : chaque commande
tapée par l'utilisateur (`post`, `feed`, `search`...) correspond à une
fonction qui appelle l'API puis affiche le résultat.

**Pour ajouter une nouvelle commande** : ajouter une entrée dans l'objet
`COMMANDS` de `index.html`, et si besoin la route correspondante côté
`backend/app.py`.

## Où toucher pour une modification courante

| Je veux... | Je modifie... |
|---|---|
| Changer une règle métier (ex: longueur max d'un post) | `backend/app.py` (les classes `PostIn`, etc.) |
| Ajouter un champ à la base de données | `backend/schema.sql` + la route concernée dans `app.py` |
| Ajouter une commande terminal | `frontend/index.html` (objet `COMMANDS`) + route API si besoin |
| Changer l'algorithme de recherche/scoring | `c_ext/searchmodule.c` (recompiler ensuite) |
| Changer le style visuel du terminal | `frontend/index.html` (bloc `<style>`) |
