#!/usr/bin/env bash
# Install and configure local PostgreSQL for NexCLIP Recorder.
# Idempotent. Password stays in the env file (never printed).
# Usage: sudo ./bin/nexrec-install-postgres.sh /etc/nexrec/nexrec.env
set -euo pipefail

ENV_FILE="${1:-/etc/nexrec/nexrec.env}"

log() { printf '[nexrec-postgres] %s\n' "$*"; }
warn() { printf '[nexrec-postgres] WARN %s\n' "$*" >&2; }

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq postgresql postgresql-contrib php-pgsql python3-psycopg2

if command -v pg_lsclusters >/dev/null 2>&1; then
  ver="$(pg_lsclusters --no-header | awk 'NR==1 {print $1}')"
  name="$(pg_lsclusters --no-header | awk 'NR==1 {print $2}')"
  state="$(pg_lsclusters --no-header | awk 'NR==1 {print $4}')"
  if [[ -n "$ver" && "$state" != "online" ]]; then
    pg_ctlcluster "$ver" "$name" start
  fi
fi
if ! pg_isready -q; then
  warn "PostgreSQL is not accepting connections"
  exit 1
fi

[[ -f "$ENV_FILE" ]] || { warn "missing $ENV_FILE"; exit 1; }

upsert_line() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    return 0
  fi
  printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
}

read_line() {
  local key="$1"
  grep -E "^${key}=" "$ENV_FILE" | head -n 1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

if grep -q '^NEXREC_DB=' "$ENV_FILE"; then
  sed -i 's|^NEXREC_DB=|# NEXREC_DB=|' "$ENV_FILE"
  log "retired NEXREC_DB in $ENV_FILE (the runtime database is local PostgreSQL)"
fi

upsert_line NEXREC_PGHOST 127.0.0.1
upsert_line NEXREC_PGPORT 5432
upsert_line NEXREC_PGDATABASE nexrec
upsert_line NEXREC_PGUSER nexrec
upsert_line NEXREC_PGPASSWORD ""

pass="$(read_line NEXREC_PGPASSWORD)"
if [[ -z "$pass" || "$pass" == "change-me" ]]; then
  pass="$(openssl rand -hex 24)"
  if grep -q '^NEXREC_PGPASSWORD=' "$ENV_FILE"; then
    sed -i "s|^NEXREC_PGPASSWORD=.*|NEXREC_PGPASSWORD=${pass}|" "$ENV_FILE"
  else
    printf 'NEXREC_PGPASSWORD=%s\n' "$pass" >> "$ENV_FILE"
  fi
  log "wrote a new database password into $ENV_FILE"
fi

db="$(read_line NEXREC_PGDATABASE)"
user="$(read_line NEXREC_PGUSER)"
[[ -n "$db" && -n "$user" ]] || { warn "NEXREC_PGDATABASE and NEXREC_PGUSER are required"; exit 1; }
if [[ ! "$db" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ || ! "$user" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]]; then
  warn "database name and user must be simple identifiers"
  exit 1
fi

sql_pass="${pass//\'/\'\'}"
sudo -u postgres psql -v ON_ERROR_STOP=1 -c "DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${user}') THEN
    CREATE ROLE ${user} LOGIN PASSWORD '${sql_pass}';
  ELSE
    ALTER ROLE ${user} WITH LOGIN PASSWORD '${sql_pass}';
  END IF;
END
\$\$;"

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${db}'" | grep -q 1; then
  sudo -u postgres createdb -O "$user" "$db"
  log "created database $db"
else
  log "database $db already exists"
fi
sudo -u postgres psql -d "$db" -v ON_ERROR_STOP=1 -c "GRANT ALL ON SCHEMA public TO ${user};"

# Ubuntu keeps pg_hba.conf under /etc/postgresql, not in the data directory.
hba="$(sudo -u postgres psql -tAc 'SHOW hba_file;' | tr -d '[:space:]')"
if [[ -f "$hba" ]] && ! grep -q 'nexrec-local' "$hba"; then
  printf 'host %s %s 127.0.0.1/32 scram-sha-256  # nexrec-local\n' "$db" "$user" >> "$hba"
  if [[ -n "${ver:-}" && -n "${name:-}" ]]; then
    pg_ctlcluster "$ver" "$name" reload || true
  fi
  log "allowed ${user} on 127.0.0.1 in pg_hba.conf"
fi

PGPASSWORD="$pass" psql -h 127.0.0.1 -p "$(read_line NEXREC_PGPORT)" -U "$user" -d "$db" -v ON_ERROR_STOP=1 -c 'SELECT 1' >/dev/null
log "local PostgreSQL is ready for ${user}@${db} (password is only in $ENV_FILE)"
