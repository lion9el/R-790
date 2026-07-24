#!/usr/bin/env bash
# Compile l'extension C puis lance le serveur.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== Compilation de l'extension C (moteur de recherche) =="
cd "$DIR/c_ext"
python3 setup.py build_ext --inplace

echo "== Installation des dépendances Python =="
pip install -r "$DIR/requirements.txt" --break-system-packages -q || pip install -r "$DIR/requirements.txt"

echo "== Démarrage du serveur sur http://localhost:8000 =="
cd "$DIR/backend"
python3 -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
