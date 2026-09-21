#!/usr/bin/env bash
# Local UI without Apache: php -S on :8080
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${NEXREC_DEMO_DATA:-$ROOT/data}"
mkdir -p "$DATA/storage" "$DATA/sessions"
export NEXREC_DATA_DIR="$DATA"
export NEXREC_DB="$DATA/nexrec.db"
export NEXREC_STORAGE_DIR="$DATA/storage"
export NEXREC_ALLOW_HTTP=1
export NEXREC_ENV_FILE="${NEXREC_ENV_FILE:-$ROOT/nexrec-example.env}"
php "$ROOT/web/nexrec-auth-bootstrap.php"
echo "NexCLIP Recorder UI  http://127.0.0.1:8080   (admin / password)"
exec php -S 127.0.0.1:8080 -t "$ROOT/web/public" "$ROOT/web/public/router-dev.php"
