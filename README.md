# SocialNet — réseau social en Python + C, interface terminal web

Un réseau social complet : comptes utilisateurs, posts, likes, commentaires,
follow, messagerie privée, fil d'actualité et recherche full-text — avec une
interface web qui ressemble à une invite de commandes.

## Architecture

```
socialnet/
├── c_ext/                 Extension C exposée à Python
│   ├── searchmodule.c      moteur de recherche full-text (index inversé, table de hachage)
│   └── setup.py            script de compilation
├── backend/                API Python (FastAPI)
│   ├── app.py               toutes les routes (auth, posts, follow, messages, recherche)
│   ├── auth.py               hachage PBKDF2 + JWT
│   ├── database.py            accès SQLite
│   └── schema.sql              schéma de la base
├── frontend/                interface "terminal" (HTML/CSS/JS, sans framework)
│   └── index.html
├── requirements.txt
└── run.sh                  compile + installe + lance tout
```

- **La partie C** est un vrai moteur de recherche : un index inversé (mot →
  liste de documents avec fréquence) implémenté avec une table de hachage
  écrite en C pur, compilé en extension Python via `Python.h`. C'est lui qui
  répond à la commande `search`.
- **Le backend Python** (FastAPI) gère toute la logique métier : comptes,
  authentification par jeton JWT, posts, likes, commentaires, abonnements,
  messagerie privée, fil d'actualité. Base de données SQLite.
- **Le frontend** est une page web unique qui simule un terminal (thème CRT
  ambre, curseur clignotant, historique de commandes avec les flèches
  haut/bas) et communique avec l'API en JSON.

## Lancer le projet

```bash
cd socialnet
./run.sh
```

Puis ouvrir http://localhost:8000 dans un navigateur.

Ou manuellement :

```bash
cd c_ext && python3 setup.py build_ext --inplace && cd ..
pip install -r requirements.txt
cd backend && python3 -m uvicorn app:app --reload
```

## Commandes du terminal

Une fois la page ouverte, tape `help` pour voir la liste. Résumé :

| Commande | Effet |
|---|---|
| `register <user> <mdp>` | créer un compte |
| `login <user> <mdp>` | se connecter |
| `post <texte>` | publier |
| `postimg <texte>` | publier avec une image jointe (choix du fichier via une fenêtre) |
| `feed [n]` | afficher le fil |
| `like` / `unlike <id>` | liker / retirer un like |
| `comment <id> <texte>` | commenter |
| `comments <id>` | lire les commentaires |
| `follow` / `unfollow <user>` | s'abonner / se désabonner |
| `profile <user>` | voir un profil |
| `msg <user> <texte>` | message privé |
| `conv <user>` | lire une conversation |
| `inbox` | liste des conversations |
| `search <mots>` | recherche full-text (moteur C) |

## Travailler à plusieurs sur ce projet

Ce dépôt est sous Git. Avant de contribuer, lire :

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — comment les 3 couches (C, backend,
  frontend) s'articulent, et où toucher pour chaque type de modification.
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — workflow Git (branches, commits,
  pull requests), pour ne pas s'écraser le travail mutuellement.
- **[CHANGELOG.md](CHANGELOG.md)** — historique des versions.

La branche `main` contient toujours la dernière version stable et
fonctionnelle (chaque état stable est aussi marqué par un tag Git, ex: `v1.0`).

## Pistes d'évolution

- Pagination du fil et de la recherche
- Notifications (nouveaux followers, likes, messages)
- Déploiement : Postgres au lieu de SQLite pour la production,
  reverse-proxy (nginx) devant uvicorn, HTTPS
- Tests automatisés (pytest pour l'API, tests unitaires pour l'extension C)
