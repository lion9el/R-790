# Changelog — SocialNet

Toutes les versions notables du projet sont listées ici. Chaque entrée
correspond à un tag Git.

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/).

## [Non publié] — notifications (likes, follows, messages)

### Ajouté
- Nouvelle table `notifications` (like / follow / message).
- `GET /api/notifications` : liste les notifications de l'utilisateur
  courant, les marque comme lues à la consultation (même principe que
  `GET /api/messages/{username}` pour les conversations).
- Un like, un follow ou un message génère une notification persistée en
  base, ET un push temps réel (WebSocket, événement `{"type":
  "notification", ...}`) si le destinataire est connecté au moment de
  l'action. Pour les messages, pas de push dupliqué : l'événement `message`
  déjà existant sert de signal temps réel.
- Pas de notification en cas d'auto-like (aimer son propre post) ni de
  double notification si on like/suit déjà.
- Nouvelle commande terminal `notifications`.
- Suite pytest dédiée (`backend/tests/test_notifications.py`).

### Modifié
- La suppression d'un post supprime aussi les notifications qui le
  référencent, pour ne pas laisser de notification pointant dans le vide.

## [Non publié] — tests automatisés

### Ajouté
- `backend/tests/` : suite pytest (55 tests) couvrant auth, posts, likes,
  commentaires, follow/unfollow, profils, messagerie privée, et la
  pagination du fil et de la recherche introduite précédemment. Chaque test
  s'exécute sur une base SQLite temporaire isolée (voir `conftest.py`).
- `c_ext/tests/` : suite pytest (16 tests) pour le moteur de recherche C
  (`searchengine`), indépendante du backend — indexation, scoring, tri,
  pagination, suppression, remise à zéro, insensibilité à la casse.
- `requirements-dev.txt` : dépendances de test (`pytest`, `httpx`), séparées
  des dépendances de production.
- Un test dédié (`test_search_isolation.py`) vérifie explicitement que
  l'index de recherche C, qui vit dans une mémoire globale au process, est
  bien réinitialisé entre deux tests — sans quoi des résultats d'un test
  "fuiteraient" silencieusement vers le suivant.
- `README.md` et `CONTRIBUTING.md` mis à jour avec les instructions pour
  lancer les tests, et l'étape rendue explicite dans le workflow avant
  toute pull request.

## [Non publié] — pagination du fil et de la recherche

### Ajouté
- `GET /api/feed` accepte `before=<post_id>` : pagination par curseur (id
  décroissant), robuste aux nouveaux posts publiés entre deux pages,
  contrairement à un offset classique. Réponse désormais sous la forme
  `{"posts": [...], "has_more": bool, "next_cursor": id|null}`.
- `GET /api/search` accepte `limit` (≤ 50) et `offset` : pagination classique,
  car l'extension C trie par score et non par id. Réponse sous la forme
  `{"results": [...], "has_more": bool, "next_offset": int|null}`.
- Nouvelle commande terminal `more` : continue la pagination du dernier
  `feed` ou `search` lancé.

### Modifié
- Réponses de `/api/feed` et `/api/search` : ce ne sont plus des tableaux
  bruts mais des objets enveloppant les résultats (voir ci-dessus). Rupture
  de compatibilité pour tout client externe qui consommerait ces routes
  directement (le frontend fourni est mis à jour en conséquence).

## [v1.2] — upload d'images sur les posts

### Ajouté
- `POST /api/posts` accepte maintenant une image optionnelle (`multipart/form-data`
  au lieu de JSON) : formats acceptés PNG/JPEG/GIF/WEBP, 5 Mo maximum.
- Les images sont stockées dans `backend/uploads/` (hors Git) et servies via
  `/uploads/<fichier>`.
- Nouvelle commande terminal `postimg <texte>` : ouvre un sélecteur de
  fichier puis publie le post avec l'image choisie.
- Migration automatique de la base existante (ajout de la colonne
  `posts.image_path`) au démarrage du serveur — aucune perte de données pour
  les bases déjà créées avec une version antérieure.

### Sécurité
- Le type de fichier est vérifié par Content-Type (liste blanche), pas par
  l'extension fournie par le client.
- Le nom de fichier stocké est généré aléatoirement (UUID), jamais dérivé du
  nom envoyé par le client — élimine tout risque de traversée de chemin.

## [v1.1] — messagerie et fil en temps réel

### Ajouté
- Connexion WebSocket (`GET /ws?token=...`) authentifiée par jeton JWT.
- `backend/realtime.py` : registre des connexions actives (`ConnectionManager`).
- Les messages privés sont poussés instantanément au destinataire s'il est
  connecté (événement `{"type": "message", ...}`).
- Les nouveaux posts sont poussés instantanément aux abonnés connectés
  (événement `{"type": "new_post", ...}`).
- Le frontend ouvre automatiquement la connexion temps réel après
  login/register, et l'affiche dans le terminal avec le préfixe
  `[temps réel]`.

### Connu — limitation
- Pas de reconnexion automatique du WebSocket en cas de coupure (il faut
  refaire `login`). À traiter dans une prochaine évolution si besoin.

## [v1.0] — 2026-07-25

Première version stable et fonctionnelle du projet.

### Ajouté
- Extension C (`c_ext/`) : moteur de recherche full-text à index inversé
  (table de hachage), exposé à Python.
- Backend FastAPI (`backend/`) :
  - Comptes utilisateurs (inscription, connexion par mot de passe hashé
    PBKDF2, sessions par jeton JWT)
  - Publications (créer, lister le fil d'actualité)
  - Likes / retrait de like
  - Commentaires
  - Abonnements (follow / unfollow)
  - Profils utilisateurs
  - Messagerie privée (envoi, conversation, boîte de réception)
  - Recherche full-text (via l'extension C)
  - Base de données SQLite
- Frontend (`frontend/index.html`) : interface terminal web (thème CRT ambre)
  avec historique de commandes, consommant l'API en JSON.
- Organisation du projet :
  - Dépôt Git initialisé, `.gitignore`
  - `README.md` (installation et lancement)
  - `ARCHITECTURE.md` (fonctionnement des trois couches)
  - `CONTRIBUTING.md` (workflow de collaboration à plusieurs)
  - `.env.example` (modèle de configuration sans secrets)

### Connu — limitations de cette version
- Pas de temps réel (la messagerie et le fil nécessitent de retaper une
  commande pour se rafraîchir ; pas de WebSocket)
- Pas de pagination sur le fil / la recherche
- Pas d'upload d'image
- Pas de tests automatisés
- SQLite (adapté au développement, pas à une forte charge en production)

---

## Modèle pour les prochaines entrées

```
## [vX.Y] — AAAA-MM-JJ

### Ajouté
- ...

### Modifié
- ...

### Corrigé
- ...

### Supprimé
- ...
```
