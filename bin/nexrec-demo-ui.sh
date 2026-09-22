#!/usr/bin/env bash
# Local UI without Apache: php -S on :8080
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${NEXREC_DEMO_DATA:-$ROOT/data}"
mkdir -p "$DATA/storage" "$DATA/sessions"
export NEXREC_DATA_DIR="$DATA"
# Path is a schema isolation key inside local Postgres, not a database file.
export NEXREC_DB="$DATA/nexrec.db"
export NEXREC_STORAGE_DIR="$DATA/storage"
export NEXREC_ALLOW_HTTP=1
export NEXREC_PGHOST="${NEXREC_PGHOST:-127.0.0.1}"
export NEXREC_PGPORT="${NEXREC_PGPORT:-5432}"
export NEXREC_PGUSER="${NEXREC_PGUSER:-nexrec_test}"
export NEXREC_PGPASSWORD="${NEXREC_PGPASSWORD:-nexrec_test}"
export NEXREC_PGDATABASE="${NEXREC_PGDATABASE:-nexrec_test}"
export NEXREC_ENV_FILE="${NEXREC_ENV_FILE:-$ROOT/nexrec-example.env}"
"$ROOT/bin/nexrec-test-db.sh"
php "$ROOT/web/nexrec-auth-bootstrap.php"
echo "NexCLIP Recorder UI  http://127.0.0.1:8080   (admin / password)"
exec php -S 127.0.0.1:8080 -t "$ROOT/web/public" "$ROOT/web/public/router-dev.php"
