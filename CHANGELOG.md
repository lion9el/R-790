# Changelog — SocialNet

Toutes les versions notables du projet sont listées ici. Chaque entrée
correspond à un tag Git.

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/).

## [Non publié] — upload d'images sur les posts

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
