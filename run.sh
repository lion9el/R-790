#!/usr/bin/env bash
# Compile l'extension C puis lance le serveur.
#
# Par défaut : mode DÉVELOPPEMENT (rechargement automatique à chaque
# modification de fichier, écoute sur 127.0.0.1 uniquement). Pour un
# déploiement réel, ne JAMAIS lancer ce script tel quel sans reverse-proxy
# (nginx/caddy) devant + HTTPS : voir README.md > Pistes d'évolution.
#
# Variables d'environnement optionnelles :
#   HOST=0.0.0.0   pour écouter sur toutes les interfaces (ex: accès depuis
#                  une autre machine du même réseau local). Ne fais ça que
#                  si tu sais ce que tu fais : ça expose directement le
#                  serveur de dev (--reload) sans aucune protection réseau.
#   PORT=8000      port d'écoute.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

echo "== Compilation de l'extension C (moteur de recherche) =="
cd "$DIR/c_ext"
python3 setup.py build_ext --inplace

echo "== Installation des dépendances Python =="
pip install -r "$DIR/requirements.txt" --break-system-packages -q || pip install -r "$DIR/requirements.txt"

if [ "$HOST" = "0.0.0.0" ]; then
  echo "⚠️  HOST=0.0.0.0 : le serveur va écouter sur toutes les interfaces réseau."
  echo "    Assure-toi qu'un pare-feu/reverse-proxy protège cet accès."
fi

echo "== Démarrage du serveur sur http://${HOST}:${PORT} =="
cd "$DIR/backend"
python3 -m uvicorn app:app --host "$HOST" --port "$PORT" --reload
