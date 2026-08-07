# Déploiements automatisés (GitHub Actions)

## Vue d’ensemble

À chaque push sur `main` (ou déclenchement manuel) :

| Job | Rôle | Condition |
|-----|------|-----------|
| **test** | pytest C + API | toujours |
| **docker** | Build image → push **ghcr.io** | après tests OK |
| **remote** | SSH sur le VPS → `docker compose up` | si `DEPLOY_HOST` est défini |

Image produite :

```text
ghcr.io/lion9el/r-790:latest
ghcr.io/lion9el/r-790:<sha>
```

(Les noms GHCR sont en **minuscules**.)

## 1. Image Docker seule (déjà actif)

Rien à configurer : `GITHUB_TOKEN` suffit pour pousser vers GHCR.

Après un push sur `main` :

```bash
docker pull ghcr.io/lion9el/r-790:latest
```

Si le dépôt est **privé**, se connecter d’abord :

```bash
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin
```

## 2. Déploiement remote (VPS)

### Secrets GitHub

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Obligatoire | Exemple | Description |
|--------|-------------|---------|-------------|
| `DEPLOY_HOST` | **oui** | `203.0.113.10` | IP ou hostname du serveur |
| `DEPLOY_USER` | **oui** | `deploy` | Utilisateur SSH |
| `DEPLOY_SSH_KEY` | **oui** | `-----BEGIN OPENSSH...` | Clé **privée** (ed25519 ou RSA) |
| `DEPLOY_PATH` | non | `/home/deploy/socialnet` | Dossier distant (défaut `~/socialnet`) |
| `DEPLOY_PORT` | non | `8000` | Port publié |
| `DEPLOY_SSH_PORT` | non | `22` | Port SSH |
| `SOCIALNET_SECRET` | recommandé | `openssl rand -hex 32` | Secret JWT prod |

Sans `DEPLOY_HOST`, le job **remote** est **ignoré** (pas d’échec).

### Préparer le serveur (une fois)

```bash
# Sur le VPS
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER   # puis reconnecte-toi

# Clé SSH : la clé PUBLIQUE doit être dans ~/.ssh/authorized_keys
# la PRIVÉE va dans le secret GitHub DEPLOY_SSH_KEY
```

Option A — laisser Actions créer `docker-compose.yml` au premier deploy.

Option B — cloner et utiliser le fichier du dépôt :

```bash
mkdir -p ~/socialnet && cd ~/socialnet
# copier docker-compose.prod.yml depuis le repo, le renommer :
curl -sL https://raw.githubusercontent.com/lion9el/R-790/main/docker-compose.prod.yml \
  -o docker-compose.yml
# Corriger l’image si besoin (minuscules) :
# image: ghcr.io/lion9el/r-790:latest

echo "SOCIALNET_SECRET=$(openssl rand -hex 32)" > .env
chmod 600 .env
```

### Vérifier

1. Push sur `main` ou **Actions → Deploy → Run workflow**
2. Job `remote` doit être vert
3. Sur le serveur : `docker compose ps` et ouvrir `http://IP:8000`

## 3. Déclenchement manuel

Actions → **Deploy** → **Run workflow**

- `skip_remote=true` : build GHCR uniquement, pas de SSH

## 4. Postgres en production

Sur le VPS, utiliser le profile ou un compose étendu avec `DATABASE_URL`.  
Voir aussi `docker-compose.yml` (profile `postgres`) dans le dépôt.

## 5. Backups

Variables optionnelles sur le conteneur :

| Variable | Défaut |
|----------|--------|
| `SOCIALNET_BACKUP_DIR` | `/app/backups` |
| `SOCIALNET_BACKUP_INTERVAL` | `3600` |
| `SOCIALNET_BACKUP_KEEP` | `14` |

Volume Docker `socialnet-backups` recommandé.

## Dépannage

| Symptôme | Piste |
|----------|--------|
| `docker` job rouge | Logs build — souvent deps manquantes (`setuptools`, libs C) |
| `remote` skipped | `DEPLOY_HOST` non défini → normal |
| `Permission denied` SSH | Mauvaise clé, mauvais user, `authorized_keys` |
| `pull access denied` GHCR | `docker login ghcr.io` ; package visibility (public/private) |
| App inaccessible | Firewall port 8000, `docker compose ps`, logs `docker compose logs` |

## Visibilité du package GHCR

GitHub → **Packages** → `r-790` → Package settings →  
Change visibility to **Public** si tu veux `docker pull` sans token.
