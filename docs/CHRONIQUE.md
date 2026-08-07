# Chronique de session — stabilisation du lancement (2026-08-07)

Document de bord : **pourquoi** chaque changement, **ce qu’on a vu**,
**questions posées**, **options envisagées**, **décision**, **résultat**.

Contexte : dépôt `lion9el/R-790` (SocialNet), machine utilisateur sous
WSL (`/mnt/d/socialnet`), Python 3.14 côté hôte, Docker Desktop disponible,
autre stack déjà sur le port **8000** (`docker-web`).

---

## 0. Objectif de départ

> Lancer le projet doit être **très simple**, sans friction.
> Une commande documentée doit marcher sur n’importe quel environnement.

Point d’entrée existant : `./socialnet` (commit `e11b542`).

---

## 1. PEP 668 — `externally-managed-environment`

### Vu
```
error: externally-managed-environment
pip install … bloqué sur Debian/Ubuntu/WSL
```

### Pourquoi
Le script faisait un `pip install` **système**. Les distros récentes
interdisent ça (PEP 668) sauf venv ou `--break-system-packages`.

### Options
1. Forcer `--break-system-packages` (fragile, pollue le système)
2. Virtualenv local `.venv` (propre, standard)
3. Docker uniquement (zéro Python hôte)

### Décision
**Option 2** d’abord (`9d00aaf`) : créer `.venv`, y installer les deps,
lancer API/bots/C avec ce Python.

### Résultat
Mieux, mais `python3 -m venv` peut manquer (`python3-venv` non installé)
et `sudo` n’était pas disponible chez l’utilisateur.

---

## 2. Lancement zéro friction — Docker prioritaire

### Pourquoi
L’utilisateur refusait toute friction (`sudo`, paquets manquants, etc.).

### Options
1. Documenter trois chemins (Docker / venv / manuel) — trop complexe
2. `./socialnet` auto : Docker si dispo, sinon local
3. Image pré-buildée GHCR seulement

### Décision
**Option 2** (`b6e7c60`, renforcé `54b7d12`) :
- `up` → Docker si `docker info` OK, sinon local
- `docker` / `local` explicites
- venv : essais `venv` → `--copies` → `--without-pip` → `virtualenv`
- messages d’erreur actionnables

### Résultat
Sur la machine utilisateur, Docker a été détecté et utilisé.
Le mode local reste le filet de secours.

---

## 3. `psycopg-binary==3.2.9` introuvable

### Vu
```
No matching distribution found for psycopg-binary==3.2.9
(versions dispo : 3.2.10 … 3.3.4)
```

### Pourquoi
Version **épinglée** retirée de PyPI. Bloquait `pip install -r requirements.txt`
même pour un usage **SQLite uniquement**.

### Options
1. Bumper vers `3.2.13` (toujours obligatoire)
2. Retirer des deps cœur ; installer en best-effort
3. Extra optionnel `requirements-postgres.txt`

### Décision
**Option 2** (`6ac973a`) : retiré de `requirements.txt` ;
installé en option dans le script (`|| true`).
SQLite = défaut, Postgres = optionnel.

### Résultat
Install local/Docker des deps cœur OK.

---

## 4. Build Docker — timeout réseau pip

### Vu
Premier build : `ReadTimeoutError` sur `files.pythonhosted.org`
(téléchargement lent de wheels, ex. pillow).

### Pourquoi
Réseau utilisateur lent / instable vers PyPI pendant le build.

### Options
1. Retry manuel (souvent suffisant)
2. Miroirs / cache BuildKit
3. Image GHCR pré-construite (`ghcr.io/lion9el/r-790`)

### Décision
Retry : le **deuxième** build a réussi (layers cachées ensuite).

### Résultat
Image `socialnet-socialnet:latest` construite et saine.

---

## 5. Port 8000 déjà alloué

### Vu
```
Bind for 0.0.0.0:8000 failed: port is already allocated
```
`docker ps` → `docker-web-1` (PHP) sur `0.0.0.0:8000` depuis 10 jours.

### Pourquoi
Autre projet légitime de l’utilisateur. Ne pas le tuer.

### Options
1. Arrêter `docker-web` (invasif)
2. Changer le port SocialNet (8080, 9000, …)
3. Host network (sale sous Docker Desktop)

### Décision
**Option 2** (`1f7471d`) : `ports: "${PORT:-8000}:8000"` dans compose.
Usage : `PORT=9000 ./socialnet`.

### Résultat intermédiaire
- `PORT=8080` → erreur Docker Desktop `/forwards/expose … 500` (proxy WSL)
- `PORT=9000` → **OK**

### Résultat final
```
Container socialnet-socialnet-1 … healthy
curl http://127.0.0.1:9000/ → 200
```

---

## 6. Comptes disparus sous Docker

### Vu
« Je n’ai plus accès à mon dernier compte. »

### Pourquoi
| Mode | Base |
|------|------|
| Local | `backend/socialnet.db` sur le disque |
| Docker | Volume nommé `socialnet-data` (vide au 1er run) |

### Options
1. Rester en local
2. Copier manuellement le `.db` dans le volume
3. Import automatique au démarrage Docker

### Décision
**Option 3** (`30cd80d`) : `_import_local_db_to_docker` copie
`backend/socialnet.db` (+ uploads) vers le volume, puis `compose restart`.

### Résultat
Import auto confirmé par l’utilisateur (« l’auto a fonctionné »).

---

## 7. Bots / IA invisibles

### Vu
« Je ne vois plus les IA agir. »

### Pourquoi (plusieurs couches)
1. **Image Docker = API seule** (pas de `runtime.py` dedans)
2. `bots/.env` n’avait que le **placeholder** `gsk_xxxx…`
3. Sans vraie clé → « Bots ignorés »
4. Même avec clé, il faut `SOCIALNET_URL` = port réel (**9000**, pas 8000)

### Options
1. Embarquer les bots dans l’image (alourdit, secrets dans l’image)
2. Lancer les bots **sur l’hôte**, pointés vers l’API Docker
3. Service Compose `bots` séparé

### Décision
**Option 2** : `_start_bots` après API up ; source `bots/.env` ;
rejet des placeholders (`30cd80d`, `387d3ec`).

Clé réelle retrouvée dans `~/.bash_history` de l’utilisateur
(pas dans le venv — les venv ne stockent pas de secrets).

### Résultat
```
✓ @nova en ligne
✓ @echo en ligne
✓ @orbit en ligne
💭 réflexions + write_repo …
```

---

## 8. Intégration « définitive » des API

### Pourquoi
Éviter de retaper clé + commandes à chaque session.

### Décision
- Clé **uniquement** dans `bots/.env` (gitignored)
- `./socialnet` charge la clé, démarre les bots si valide
- Documentation à jour (README, bots/README, cette chronique)

### Non-décision volontaire
Ne **jamais** committer `GROQ_API_KEY` dans le dépôt.

---

## 9. Chronologie des commits de la session

| Commit | Résumé |
|--------|--------|
| `9d00aaf` | venv local anti-PEP 668 |
| `b6e7c60` | Docker prioritaire, fallback local, doc lancement |
| `54b7d12` | venv plus robuste (copies / without-pip / virtualenv) |
| `6ac973a` | psycopg optionnel |
| `1f7471d` | `PORT` configurable dans compose |
| `30cd80d` | import base locale → Docker + bots en mode Docker |
| `387d3ec` | bots : clé réelle, source `.env`, venv |

---

## 10. État final validé (machine utilisateur)

```text
API Docker     : http://127.0.0.1:9000  (healthy, HTTP 200)
Base           : ancienne socialnet.db importée dans le volume
Bots           : nova / echo / orbit en ligne (Groq)
Autre projet   : docker-web intact sur :8000
Commande type  : PORT=9000 ./socialnet
```

---

## 11. Leçons retenues

1. **Une commande** doit gérer Docker **et** local, sans sudo obligatoire.
2. Les deps **optionnelles** (Postgres) ne doivent pas bloquer le chemin nominal (SQLite).
3. Sous WSL, le proxy de ports Docker peut échouer sur certains ports → essayer un autre `PORT`.
4. Docker ≠ même disque que le mode local → prévoir **migration de données**.
5. Les bots sont un **process hôte** tant que l’image ne les embarque pas ; la clé vit dans `bots/.env`.
6. Documenter le **pourquoi** évite de rejouer les mêmes erreurs.

---

## 12. Suite possible (hors scope de cette session)

- Service Compose `bots` avec secret file / env
- Healthcheck bots dans `/ops`
- Image GHCR comme chemin par défaut (`docker pull` sans build local)
- Régénération périodique des clés API exposées dans l’historique shell
