#!/usr/bin/env bash
# Sur le VPS : tire la dernière image et redémarre.
# Cron exemple (chaque jour 4h) :
#   0 4 * * * /home/deploy/socialnet/update-server.sh >> /var/log/socialnet-update.log 2>&1

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# Si le script est dans scripts/ du repo cloné :
ROOT="$(cd "$DIR/.." && pwd)"
# Ou dossier de prod :
if [ -f "$ROOT/docker-compose.yml" ] || [ -f "$ROOT/docker-compose.prod.yml" ]; then
  cd "$ROOT"
elif [ -f "$HOME/socialnet/docker-compose.yml" ]; then
  cd "$HOME/socialnet"
else
  echo "Dossier compose introuvable" >&2
  exit 1
fi

IMAGE="${SOCIALNET_IMAGE:-ghcr.io/lion9el/r-790:latest}"

if [ -n "${GITHUB_TOKEN:-}" ]; then
  echo "$GITHUB_TOKEN" | docker login ghcr.io -u "${GITHUB_USER:-lion9el}" --password-stdin
fi

docker pull "$IMAGE"
if [ -f docker-compose.prod.yml ]; then
  docker compose -f docker-compose.prod.yml up -d --remove-orphans
else
  docker compose up -d --remove-orphans
fi
docker image prune -f
echo "Updated $(date -u +%Y-%m-%dT%H:%M:%SZ) → $IMAGE"
docker compose ps 2>/dev/null || docker compose -f docker-compose.prod.yml ps
