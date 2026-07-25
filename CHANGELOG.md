# Changelog — SocialNet

Toutes les versions notables du projet sont listées ici. Chaque entrée
correspond à un tag Git.

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/).

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
