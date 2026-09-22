#!/usr/bin/env bash
# Create the local nexrec_test role and database used by make test.
# Idempotent. Does not touch the station database.
set -euo pipefail

if ! pg_isready -q 2>/dev/null; then
  if command -v pg_lsclusters >/dev/null 2>&1; then
    ver="$(pg_lsclusters --no-header | awk 'NR==1 {print $1}')"
    name="$(pg_lsclusters --no-header | awk 'NR==1 {print $2}')"
    sudo pg_ctlcluster "$ver" "$name" start
  else
    echo "PostgreSQL is not running" >&2
    exit 1
  fi
fi

sudo -u postgres psql -v ON_ERROR_STOP=1 -c "DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'nexrec_test') THEN
    CREATE ROLE nexrec_test LOGIN PASSWORD 'nexrec_test';
  ELSE
    ALTER ROLE nexrec_test WITH LOGIN PASSWORD 'nexrec_test';
  END IF;
END
\$\$;"

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='nexrec_test'" | grep -q 1; then
  sudo -u postgres createdb -O nexrec_test nexrec_test
fi
sudo -u postgres psql -d nexrec_test -v ON_ERROR_STOP=1 -c "GRANT ALL ON SCHEMA public TO nexrec_test;"
PGPASSWORD=nexrec_test psql -h 127.0.0.1 -U nexrec_test -d nexrec_test -c 'SELECT 1' >/dev/null
echo "nexrec_test ready"
