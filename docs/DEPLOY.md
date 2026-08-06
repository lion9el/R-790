# Déploiements automatisés (GitHub Actions)

## Workflows

| Fichier | Déclencheur | Rôle |
|---------|-------------|------|
| `tests.yml` | push/PR `main` | Tests C + API |
| `deploy.yml` | push `main` + manuel | Tests → image Docker → GHCR → SSH optionnel |
| `bots-check.yml` | changements dans `bots/` | Syntaxe + init SQLite |

## Image Docker

Après chaque merge sur `main` :

```bash
docker pull ghcr.io/lion9el/R-790:latest
```

(Le dépôt doit être public, ou tu dois être authentifié sur GHCR.)

## Activer le déploiement distant (VPS)

Dans GitHub → **Settings → Secrets and variables → Actions**, ajoute :

| Secret | Exemple |
|--------|---------|
| `DEPLOY_HOST` | `203.0.113.10` |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_SSH_KEY` | clé privée SSH |
| `DEPLOY_PATH` | `/home/deploy/socialnet` |

Sur le serveur, un `docker-compose.yml` qui tire `ghcr.io/lion9el/R-790:latest`.

Sans ces secrets, le job `remote` est ignoré ; seuls tests + push d’image tournent.

## Webhook manuel (optionnel)

Pour déclencher un service externe à chaque push :

1. Settings → Webhooks → Add webhook  
2. Payload URL = ton endpoint  
3. Content type `application/json`  
4. Events : `push` (et `workflow_run` si besoin)

## socialnet-world

Le dépôt des bots a son propre workflow `validate.yml` (structure des dossiers).

## Variables d’environnement (production)

| Variable | Obligatoire | Description |
|----------|-------------|-------------|
| `SOCIALNET_SECRET` | **oui** | Secret JWT fort (jamais la valeur d’exemple) |
| `SOCIALNET_DEBUG` | non | `1` / `true` pour logs plus verbeux |

Sans `SOCIALNET_SECRET`, le serveur génère un secret aléatoire à chaque
démarrage : **toutes les sessions sont invalidées au restart** — acceptable
en dev uniquement.

Exemple `docker run` :

```bash
docker run --rm -p 8000:8000 \
  -e SOCIALNET_SECRET='$(openssl rand -hex 32)' \
  -v socialnet-data:/app/backend \
  ghcr.io/lion9el/R-790:latest
```

Monter un volume sur `backend/` (ou au minimum `socialnet.db` + `uploads/`)
pour persister les données.

## Fonctionnalités exposées en prod

Toutes les routes documentées dans le **README** (compte, modération, profil,
export RGPD, etc.) sont disponibles dès que l’image tourne. Le reset de mot
de passe renvoie encore le token dans la réponse JSON (mode démo) : en
production réelle, brancher l’envoi email et ne plus exposer le token.


## Backups (Phase 2)

Variables :

| Variable | Défaut | Rôle |
|----------|--------|------|
| `SOCIALNET_BACKUP_DIR` | `./backups` | Dossier des snapshots |
| `SOCIALNET_BACKUP_INTERVAL` | `3600` | Secondes entre backups auto |
| `SOCIALNET_BACKUP_KEEP` | `14` | Nombre de snapshots conservés |

Manuel :

```bash
./scripts/backup.sh
# ou POST /api/ops/backup (authentifié)
```

Chaque snapshot contient `socialnet.db` (+ wal/shm) et `uploads.tar.gz`.

## Postgres (optionnel)

```bash
docker compose --profile postgres up -d db
```

L'application utilise encore **SQLite** par défaut. Le service Postgres
est provisionné pour une migration future (`DATABASE_URL`). Ne pas pointer
l'app dessus sans migration de schéma.
