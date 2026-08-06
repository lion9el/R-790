#!/usr/bin/env bash
# Démarre SocialNet (API) + bots + santé minimale.
# Usage : ./start-all.sh
# Arrêt  : Ctrl+C (tue API + bots)

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
URL="http://${HOST}:${PORT}"
API_LOG="${ROOT}/.run-api.log"
BOTS_LOG="${ROOT}/.run-bots.log"
API_PID=""
BOTS_PID=""

cleanup() {
  echo ""
  echo "== Arrêt =="
  if [[ -n "${BOTS_PID}" ]] && kill -0 "$BOTS_PID" 2>/dev/null; then
    kill "$BOTS_PID" 2>/dev/null || true
    wait "$BOTS_PID" 2>/dev/null || true
  fi
  if [[ -n "${API_PID}" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
  echo "OK."
}
trap cleanup EXIT INT TERM

echo "=== SocialNet — start-all ==="
echo "Root : $ROOT"

# --- dépendances minimales ---
if [[ ! -f "$ROOT/.env" && -f "$ROOT/.env.example" ]]; then
  echo "⚠  Pas de .env — copie .env.example (pense à SOCIALNET_SECRET)"
  cp "$ROOT/.env.example" "$ROOT/.env"
fi

echo "== Compilation extension C =="
(cd "$ROOT/c_ext" && python3 setup.py build_ext --inplace) >>"$API_LOG" 2>&1 || {
  echo "Échec compilation C — voir $API_LOG"
  exit 1
}

echo "== Installation deps Python (quiet) =="
pip install -r "$ROOT/requirements.txt" -q --break-system-packages 2>/dev/null \
  || pip install -r "$ROOT/requirements.txt" -q
if [[ -f "$ROOT/bots/requirements-bots.txt" ]]; then
  pip install -r "$ROOT/bots/requirements-bots.txt" -q --break-system-packages 2>/dev/null \
    || pip install -r "$ROOT/bots/requirements-bots.txt" -q || true
fi

echo "== Démarrage API sur $URL =="
(
  cd "$ROOT/backend"
  python3 -m uvicorn app:app --host "$HOST" --port "$PORT"
) >>"$API_LOG" 2>&1 &
API_PID=$!

# healthcheck
echo -n "Attente API"
ok=0
for i in $(seq 1 40); do
  if curl -sf "$URL/" >/dev/null 2>&1; then
    ok=1
    break
  fi
  echo -n "."
  sleep 0.5
done
echo ""
if [[ "$ok" -ne 1 ]]; then
  echo "API non joignable — logs : $API_LOG"
  tail -30 "$API_LOG" || true
  exit 1
fi
echo "✓ API OK  → $URL"

# première chronique (si possible)
if [[ -f "$ROOT/bots/chronicle.py" ]]; then
  echo "== Chronique initiale =="
  python3 "$ROOT/bots/chronicle.py" || echo "⚠ chronique (API ok, compte chronicle à créer au 1er run bots)"
fi

# bots (optionnel si pas de GROQ key)
if [[ -f "$ROOT/bots/.env" ]] || grep -q "GROQ_API_KEY=.\+" "$ROOT/bots/.env" 2>/dev/null; then
  :
fi
if [[ -f "$ROOT/bots/.env" ]] && grep -qE '^GROQ_API_KEY=.+' "$ROOT/bots/.env" 2>/dev/null; then
  echo "== Démarrage bots =="
  (
    cd "$ROOT"
    python3 bots/runtime.py
  ) >>"$BOTS_LOG" 2>&1 &
  BOTS_PID=$!
  echo "✓ Bots PID $BOTS_PID  (logs: $BOTS_LOG)"
else
  echo "⚠ bots/.env sans GROQ_API_KEY — bots non démarrés"
  echo "  → cp bots/.env.example bots/.env && éditer GROQ_API_KEY"
fi

echo ""
echo "=============================="
echo "  Interface : $URL"
echo "  API logs  : $API_LOG"
echo "  Bots logs : $BOTS_LOG"
echo "  Chronique : bots/world/journal/"
echo "  Ctrl+C pour tout arrêter"
echo "=============================="

# rester vivant tant que l'API tourne
while kill -0 "$API_PID" 2>/dev/null; do
  sleep 2
done
echo "API arrêtée."
