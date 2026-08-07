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
| `DEPLOY_PORT` | non | `9000` | Port publié |
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
3. Sur le serveur : `docker compose ps` et ouvrir `http://IP:9000`

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
| App inaccessible | Firewall port 9000 (ou DEPLOY_PORT), `docker compose ps`, logs `docker compose logs` |

## Visibilité du package GHCR

GitHub → **Packages** → `r-790` → Package settings →  
Change visibility to **Public** si tu veux `docker pull` sans token.

## Mises à jour automatiques

Trois niveaux, sans intervention manuelle si les secrets sont en place :

### A. Dépendances (Dependabot)

Fichier : `.github/dependabot.yml`

| Ecosystem | Fréquence | Effet |
|-----------|-----------|--------|
| `pip` (racine + bots) | chaque lundi | PR de montée de version |
| `github-actions` | chaque lundi | PR actions CI |
| `docker` | chaque lundi | PR image de base `python:*-slim` |

Les PR passent par **Tests** + **Deploy**.  
Tu merges → push `main` → nouvelle image + redeploy VPS.

### B. Image hebdomadaire (patches OS)

Workflow : `Weekly image refresh` (lundi 09:00 UTC)

1. Rebuild **sans cache** de l’image Docker  
2. Push `latest` + tag `weekly` sur GHCR  
3. Si `DEPLOY_HOST` est défini : pull + restart sur le VPS  

Déclenchement manuel : **Actions → Weekly image refresh → Run workflow**.

### C. Chaque merge sur main

Workflow **Deploy** : tests → build → push GHCR → SSH redeploy.

### Sur le VPS (optionnel, filet de sécurité)

```bash
# une fois
chmod +x scripts/update-server.sh
# cron quotidien 4h
crontab -e
# 0 4 * * * GITHUB_TOKEN=ghp_xxx /home/deploy/socialnet/scripts/update-server.sh >> /var/log/socialnet-update.log 2>&1
```

Ou **Watchtower** (pull auto des images) :

```bash
docker run -d --name watchtower \
  -v /var/run/docker.sock:/var/run/docker.sock \
  containrrr/watchtower socialnet --interval 3600
```

(remplace `socialnet` par le nom du conteneur).

### Résumé

| Événement | Qui agit | Résultat |
|-----------|----------|----------|
| CVE / nouvelle version lib | Dependabot PR | CI + review + merge |
| Patch image de base | Weekly workflow | Image + VPS à jour |
| Ton code sur `main` | Deploy workflow | Image + VPS à jour |
| Cron / Watchtower | VPS | Filet si Actions indispo |


## Sécurité ops & reset mdp

| Variable | Effet |
|----------|--------|
| `SOCIALNET_EXPOSE_RESET_TOKEN=1` | Renvoie le token de reset dans le JSON (démo terminal uniquement) |
| `SOCIALNET_OPS_KEY=...` | Exige l'en-tête `X-Ops-Key` pour `POST /api/ops/backup` |

Sans ces variables : reset ne divulgue pas le token ; backup reste accessible à tout utilisateur authentifié (pratique en dev).
