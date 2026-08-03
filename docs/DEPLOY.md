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
