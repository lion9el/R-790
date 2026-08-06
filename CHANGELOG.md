# Changelog — SocialNet

Toutes les versions notables du projet sont listées ici. Chaque entrée
correspond à un tag Git.

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/).

## [Non publié] — fonctionnalités indispensables + documentation

### Ajouté
- **Compte & sécurité**
  - Changer de mot de passe (`POST /api/account/change-password`, commande `passwd`)
  - Reset mot de passe par token 1h (`reset-request` / `reset-confirm`)
  - Suppression définitive du compte avec cascade + fichiers (`delete-account`)
  - Export RGPD (`GET /api/account/export`, commande `export`)
- **Profil enrichi**
  - `display_name`, `avatar_path`, `is_private` sur `users`
  - Commandes `setname`, `privacy on|off`
  - `PATCH /api/me/profile` (multipart avatar possible)
- **Modération**
  - Blocage / déblocage (`block` / `unblock` / `blocks`) — coupe follow + DM
  - Signalements (`report post|user|comment`)
  - Bookmarks (`bookmark` / `unbookmark` / `bookmarks`)
  - Comptes privés + demandes de follow (`requests` / `accept` / `reject`)
- Tables : `blocks`, `reports`, `bookmarks`, `follow_requests`,
  `password_reset_tokens`, `refresh_tokens`
- Services `account.py` et `moderation.py`
- 8 tests dédiés (`test_account_moderation.py`) — **149 tests** au total

### Documentation
- `README.md` entièrement réécrit (toutes les commandes et API)
- `ARCHITECTURE.md` à jour (services, schéma, flux critiques)
- Ce CHANGELOG

### Vérifié
- Suite backend complète verte
- Extension C inchangée côté API publique

## [Non publié] — modularisation services + Docker prod

### Ajouté / Amélioré
- **Modularisation backend** :
  - `backend/services/common.py` : helpers métier (serialize, hashtags, mentions, notifications, normalize)
  - `backend/services/images.py` : uploads sécurisés
  - `backend/schemas.py` : modèles Pydantic centralisés
  - `backend/deps.py` : dépendance auth + révocation
  - `app.py` allégé (~1300 → ~1030 lignes) en s'appuyant sur les services
- **Dockerfile multi-stage** production :
  - build séparé (gcc + extension C)
  - image runtime non-root (`appuser`)
  - plus légère et plus sûre
- Test secret key adapté au nouveau message de config

### Vérifié
- 141 tests backend (auth, posts, search, social, notifications, rate-limit, websocket…)
- Extension C : 16 tests

## [Non publié] — amélioration expert (qualité, performance, maintenabilité)

### Amélioré (niveau expert)
- **Moteur C de recherche** (`searchmodule.c`) :
  - TABLE_SIZE 4096 → 8192 (moins de collisions)
  - Libération des entrées vides après `index_remove` (anti-fuite mémoire long terme)
  - Vérification systématique des `malloc` (échec → RuntimeError propre)
  - Ignore les tokens d'1 caractère (bruit)
  - Garde-fou sur `limit` de recherche
- **Normalisation Unicode** côté Python avant indexation/recherche :
  NFD + suppression des diacritiques → "café" / "Cafe" / "CAFÉ" matchent correctement
- **database.py** :
  - Mode WAL + busy_timeout + pragmas de performance (cache 64 Mo, temp_store MEMORY)
  - Helper `transaction()` et helpers `execute` / `execute_one`
- **Configuration centralisée** (`config.py`) :
  - Tous les réglages (secret, rate limits, timeouts, limites images) au même endroit
  - Chargement .env + génération de secret fort en dev
- **auth.py** et **app.py** branchés sur `config.settings`

### Vérifié
- Compilation de l'extension C + imports auth/config
- API publique inchangée

## [Non publié] — audit sécurité + notifications commentaires + Docker

### Ajouté
- **En-têtes de sécurité HTTP** (dernier point ouvert de l'audit v1.4) :
  `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Permissions-Policy`, `Content-Security-Policy` stricte, et
  `Strict-Transport-Security` quand la requête arrive en HTTPS.
- **Notification de commentaire** : quand quelqu'un commente ton post,
  tu reçois une notification de type `comment` (persistée + push temps
  réel). Auto-commentaire exclu. Aperçu du texte tronqué à 120 caractères.
- Commande d'affichage frontend mise à jour pour le type `comment`.
- **Dockerfile** + **docker-compose.yml** + `.dockerignore` pour un
  démarrage conteneurisé simple (`docker compose up --build`).
- Limite de dimensions d'image uploadée (max 4096 px de côté) en plus
  de la limite de taille déjà existante.
- 3 tests unitaires pour les notifications de commentaires.

### Corrigé
- (rien de cassé)

### Vérifié
- Suite complète de tests (backend + extension C) après modifications.

## [Non publié] — révocation de jeton

Dernier point non traité de l'audit sécurité (v1.4) : un jeton JWT restait
valable jusqu'à son expiration naturelle (7 jours) même après un "logout"
côté client, qui ne faisait qu'oublier le jeton dans le navigateur — un
jeton volé (poste partagé, XSS, etc.) restait pleinement utilisable.

### Ajouté
- `POST /api/logout` : révoque le jeton actuellement utilisé (une session/
  un appareil), via une nouvelle table `revoked_tokens` (le jeton y reste
  jusqu'à sa date d'expiration naturelle, purgé automatiquement ensuite).
- `POST /api/logout/all` : révoque tous les jetons émis jusqu'ici pour ce
  compte (tous les appareils), y compris celui qui sert à l'appel — utile
  si on soupçonne qu'un jeton a fuité sans savoir lequel précisément.
  Implémenté par une colonne `users.tokens_valid_after` plutôt qu'une liste
  de jetons (impossible à énumérer, seuls les jetons révoqués individuellement
  sont jamais stockés).
- Chaque JWT porte désormais un `jti` (identifiant de session) et un `iat`/
  `iat_ms` (émission), nécessaires à ces deux mécanismes.
- Commandes terminal `logout` (désormais réellement côté serveur, plus
  seulement local) et `logout-all`.
- 12 tests (`test_token_revocation.py`) : révocation individuelle,
  révocation globale, non-affection des autres sessions/utilisateurs,
  interaction avec l'authentification WebSocket, purge des entrées expirées.

### Corrigé pendant le développement
- Bug découvert par un test qui enchaînait `login → logout/all → login` :
  la comparaison de révocation globale, à la résolution de la seconde,
  révoquait par erreur un jeton émis APRÈS la déconnexion globale s'il
  arrivait dans la même seconde Unix (ce qu'un test automatisé rapide
  déclenche facilement). Corrigé en ajoutant un claim `iat_ms` (résolution
  milliseconde) utilisé uniquement pour cette comparaison, sans toucher au
  format standard de `exp`/`iat`. Revérifié stable sur 5 exécutions
  consécutives de la suite avant de considérer le correctif fiable.

### Non traité
- Pas d'en-têtes de sécurité (CSP, X-Frame-Options) — dernier point de
  l'audit v1.4 encore ouvert, à faire avant toute exposition publique.

## [Non publié] — reconnexion automatique du WebSocket

### Ajouté
- Reconnexion automatique après toute coupure non voulue (réseau, veille,
  redémarrage du serveur), avec délai croissant (1s, 2s, 4s... plafonné à
  30s), tant que l'utilisateur reste connecté.
- Messages visibles dans le terminal : 🟡 à la perte de connexion, 🟢 à la
  reconnexion réussie, 🔴 en cas de jeton expiré/invalide (pas de boucle
  infinie dans ce cas : il faut retaper `login`).
- Une déconnexion volontaire (`logout`) ne déclenche jamais de reconnexion.

### Corrigé
- Bug découvert par le test Playwright du scénario "jeton invalide" :
  fermer l'ancienne connexion pour en ouvrir une nouvelle (dans
  `connectRealtime()`) déclenchait à tort l'`onclose` de l'ancienne
  connexion, qui programmait une reconnexion pour un socket déjà remplacé.
  Corrigé en vérifiant, dans chaque `onclose`, que la variable globale
  `socket` référence toujours CE socket précis avant d'agir.

### Vérifié
- Suite pytest inchangée (142/142, changement purement frontend).
- 5 scénarios testés avec un vrai navigateur headless (Playwright) contre
  un vrai serveur : connexion normale sans message parasite, coupure
  serveur détectée, reconnexion automatique après redémarrage du serveur,
  logout sans tentative de reconnexion, jeton invalide sans boucle infinie.

## [v1.5] — hashtags et mentions

### Ajouté
- `#hashtag` dans un post : indexé dans une nouvelle table `post_hashtags`,
  consultable via `GET /api/hashtags/{tag}` (pagination par curseur, comme
  `/api/feed`) et `GET /api/hashtags/trending` (les plus utilisés). Reste à
  jour à l'édition et à la suppression d'un post.
- `@mention` valide (compte existant, hors auto-mention) dans un post ou un
  commentaire : déclenche une notification de type `mention` (persistée +
  push temps réel, même mécanisme que like/follow). Éditer un post ne
  renotifie que les mentions nouvellement ajoutées.
- Commandes terminal `hashtag <tag>` et `trending`. Les hashtags affichés
  dans un post sont cliquables (lancent `hashtag <tag>`) ; hashtags et
  mentions sont mis en évidence visuellement dans le contenu.
- 16 tests (`test_hashtags_mentions.py`) : indexation, casse, dédoublonnage,
  pagination, trending, réindexation à l'édition, désindexation à la
  suppression, notifications de mention (post et commentaire), exclusion
  auto-mention et mention d'un compte inexistant, non-répétition à l'édition.

### Corrigé
- Bug de commit manquant découvert pendant le développement de cette
  fonctionnalité : `_index_hashtags` insérait sans `conn.commit()` explicite,
  ce qui rendait l'insertion silencieusement perdue à la fermeture de la
  connexion dans certains cas (voir l'encart dans ARCHITECTURE.md). Corrigé
  dans `create_post` et `edit_post`.

### Vérifié
- Suite complète après fusion avec le durcissement sécurité (v1.4) :
  142/142 tests (16 c_ext + 126 backend).
- Scénario temps réel manuel rejoué avec la nouvelle authentification
  WebSocket (jeton envoyé comme premier message, plus dans l'URL) : mention
  reçue en direct, hashtag et trending retrouvés via l'API.

## [v1.4] — durcissement sécurité

Correction de 7 points relevés lors d'un audit sécurité du code existant.

### Corrigé (impact élevé)
- **Secret JWT** : suppression de la valeur par défaut faible codée en dur.
  Si `SOCIALNET_SECRET` est absent, ou vaut encore le placeholder de
  `.env.example` jamais remplacé, le serveur génère désormais un secret
  aléatoire fort à chaque démarrage et affiche un avertissement explicite,
  au lieu de retomber silencieusement sur une valeur connue de quiconque
  lit le code source. `.env` est de plus réellement chargé maintenant
  (ajout de `python-dotenv` — il ne servait à rien auparavant).
- **Jeton WebSocket** : n'est plus passé dans l'URL (`/ws?token=...`, qui
  finissait en clair dans les logs d'accès du serveur et de tout
  reverse-proxy devant), mais envoyé comme premier message une fois la
  connexion ouverte. A permis de découvrir et corriger au passage un bug
  de double `accept()` de la connexion dans `realtime.py`.
- **Upload d'images** : le contenu réel du fichier est désormais vérifié
  avec Pillow (magic bytes + cohérence format/déclaration), le Content-Type
  annoncé par le client ne suffit plus à lui seul (il est entièrement
  falsifiable). Lecture par blocs avec abandon anticipé au-delà de la
  limite de taille, plutôt que de tout charger en mémoire avant de vérifier.

### Corrigé (impact moyen)
- **Rate limiting** : `POST /api/register` (5/min/IP) et `POST /api/login`
  (10/min/IP) sont désormais protégés contre les tentatives automatisées
  (`backend/ratelimit.py`, limiteur en mémoire par fenêtre glissante).
- **Dépendances figées** : `requirements.txt` et `requirements-dev.txt`
  utilisent maintenant des versions exactes (`==`) plutôt que des bornes
  ouvertes (`>=`), pour empêcher qu'un `pip install` futur ne tire
  silencieusement une version différente.

### Corrigé (impact faible, défense en profondeur)
- `escapeHtml()` (frontend) échappe désormais aussi les guillemets, pas
  seulement `&`/`<`/`>`.
- `run.sh` : écoute sur `127.0.0.1` par défaut (au lieu de `0.0.0.0`),
  configurable via `HOST=`/`PORT=`, avec avertissement explicite si on
  choisit d'écouter sur toutes les interfaces.

### Ajouté
- 23 nouveaux tests couvrant chacun de ces points : `test_secret_key.py`,
  `test_websocket_auth.py`, `test_image_upload.py`, `test_rate_limit.py`.

### Non traité dans cette série (limitations documentées, pas de fix rapide)
- Pas de révocation de jeton côté serveur (JWT valable 7 jours, "logout"
  ne fait que l'oublier côté navigateur) : nécessiterait une liste noire ou
  des jetons de rafraîchissement, hors périmètre de ce correctif.
- Pas d'en-têtes de sécurité (CSP, X-Frame-Options) : à ajouter avant toute
  exposition publique.

## [v1.3] — 2026-07-25 — édition de profil, édition/suppression de posts, notifications, pagination, tests, CI

Version consolidant plusieurs évolutions développées en parallèle par
l'équipe (dont une contribution de Devin) et fusionnées sur `main`.

### Ajouté
- **Notifications** (like / follow / message) : nouvelle table
  `notifications`, `GET /api/notifications`, push temps réel en plus de la
  persistance, commande terminal `notifications`. Aucune notification pour
  un auto-like ni de doublon si déjà liké/suivi.
- **Édition et suppression de posts et commentaires** :
  `PATCH /api/posts/{id}`, `DELETE /api/posts/{id}`,
  `DELETE /api/comments/{id}`. La suppression d'un post désindexe aussi son
  contenu du moteur de recherche C et supprime les notifications qui le
  référençaient, pour ne rien laisser pointer dans le vide.
- **Profil modifiable** : `PATCH /api/me` (bio, 200 caractères max).
- **Pagination** :
  - `GET /api/feed?before=<post_id>` — pagination par curseur (id
    décroissant), robuste aux nouveaux posts publiés entre deux pages.
  - `GET /api/search?limit=&offset=` — pagination classique (l'extension C
    trie par score, pas par id).
  - Nouvelle commande terminal `more` pour continuer la pagination du
    dernier `feed`/`search`.
- **Tests automatisés** :
  - `backend/tests/` (83 tests pytest) : auth, posts, likes, commentaires,
    follow/unfollow, profils, messagerie, notifications, pagination.
  - `c_ext/tests/` (16 tests pytest) : moteur de recherche C — indexation,
    scoring, tri, pagination, suppression, isolation entre tests.
  - `requirements-dev.txt` séparé des dépendances de production.
- **Intégration continue** : `.github/workflows/tests.yml` — compile
  l'extension C et lance toute la suite de tests à chaque push/pull request
  sur `main`.

### Modifié
- Réponses de `/api/feed` et `/api/search` : ne sont plus des tableaux
  bruts mais des objets `{"posts"|"results": [...], "has_more": bool,
  "next_cursor"|"next_offset": ...}`. Rupture de compatibilité pour tout
  client externe consommant ces routes directement (le frontend fourni est
  à jour).

### Vérifié
- Suite complète (99 tests) exécutée avec succès avant publication de cette
  version : `16 passed` (c_ext) + `83 passed` (backend).

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
