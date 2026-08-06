#!/usr/bin/env bash
# Snapshot manuel de la base + uploads
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export SOCIALNET_BACKUP_DIR="${SOCIALNET_BACKUP_DIR:-$ROOT/backups}"
cd "$ROOT/backend"
python3 -c "from services.backup import run_backup; import json; print(json.dumps(run_backup(), indent=2))"
