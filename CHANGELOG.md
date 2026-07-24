# Changelog — SocialNet

Toutes les versions notables du projet sont listées ici. Chaque entrée
correspond à un tag Git.

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/).

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
