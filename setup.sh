#!/usr/bin/env bash
# NexCLIP Recorder installer. Canonical (NexVUE-style): keep in sync with new
# units/files. Idempotent. Does not overwrite a live /etc/nexrec/nexrec.env.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP_ROOT="${NEXREC_APP_ROOT:-/opt/NexClip-Recorder}"
ETC=/etc/nexrec
VAR=/var/lib/nexrec

log() { printf '[nexrec-setup] %s\n' "$*"; }
warn() { printf '[nexrec-setup] WARN %s\n' "$*" >&2; }

if [[ "${1:-}" == "--check" ]]; then
  [[ -f "$ETC/nexrec.env" ]] || { echo "missing $ETC/nexrec.env"; exit 1; }
  [[ -f "$VAR/nexrec.db" ]] || echo "db not yet created"
  php "$ROOT/web/nexrec-auth-bootstrap.php"
  exit 0
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  php-cli php-sqlite3 php-mbstring php-ldap php-curl php-xml \
  apache2 libapache2-mod-php ffmpeg python3 sqlite3 \
  chrony 2>/dev/null || apt-get install -y \
  php-cli php-sqlite3 php-mbstring php-ldap php-curl php-xml \
  apache2 libapache2-mod-php ffmpeg python3 sqlite3

timedatectl set-timezone America/New_York 2>/dev/null || true
systemctl enable --now chrony 2>/dev/null || systemctl enable --now systemd-timesyncd 2>/dev/null || true

mkdir -p "$ETC/inputs" "$VAR/storage" "$VAR/auth" "$VAR/sessions"
chown -R www-data:www-data "$VAR"
chmod 750 "$VAR" "$VAR/auth"

if [[ ! -f "$ETC/nexrec.env" ]]; then
  sed "s|^NEXREC_DATA_DIR=.*|NEXREC_DATA_DIR=$VAR|;s|^NEXREC_STORAGE_DIR=.*|NEXREC_STORAGE_DIR=$VAR/storage|;s|^NEXREC_DB=.*|NEXREC_DB=$VAR/nexrec.db|" \
    "$ROOT/nexrec-example.env" > "$ETC/nexrec.env"
  chmod 640 "$ETC/nexrec.env"
  log "wrote $ETC/nexrec.env (edit secrets locally; file is not in git)"
else
  log "keeping existing $ETC/nexrec.env"
fi

if [[ ! -f "$ETC/inputs/demo.env" ]]; then
  cp "$ROOT/inputs-example.env" "$ETC/inputs/demo.env"
fi

# Deploy tree (copy, do not clobber a git clone if APP_ROOT is the clone).
if [[ "$ROOT" != "$APP_ROOT" ]]; then
  mkdir -p "$APP_ROOT"
  rsync -a --exclude '.git' --exclude 'data' --exclude 'demo-out' "$ROOT/" "$APP_ROOT/"
fi

install -m 644 "$ROOT/systemd/"*.service "$ROOT/systemd/"*.timer /etc/systemd/system/ 2>/dev/null || {
  cp "$ROOT/systemd/"*.service /etc/systemd/system/
  cp "$ROOT/systemd/"*.timer /etc/systemd/system/
}

# Point units at this clone if not using /opt.
if [[ "$ROOT" != "/opt/NexClip-Recorder" ]]; then
  for u in /etc/systemd/system/nexrec-*.service /etc/systemd/system/nexrec-*.timer; do
    [[ -f "$u" ]] || continue
    sed -i "s|/opt/NexClip-Recorder|$ROOT|g" "$u"
  done
fi

systemctl daemon-reload
systemctl enable --now nexrec-export.service nexrec-cleanup.timer || warn "enable units failed"

export NEXREC_ENV_FILE="$ETC/nexrec.env"
export NEXREC_DATA_DIR="$VAR"
export NEXREC_DB="$VAR/nexrec.db"
php "$ROOT/web/nexrec-auth-bootstrap.php"

CONF=/etc/apache2/conf-available/nexrec-web.conf
sed "s|@@APP_ROOT@@|$ROOT/web|g" "$ROOT/apache/nexrec-web-apache.conf" > "$CONF"
a2enmod rewrite >/dev/null
a2enconf nexrec-web >/dev/null || true
# DocumentRoot hint — do not blindly rewrite every vhost (NexVUE does this carefully).
log "enable Apache DocumentRoot $ROOT/web/public (see apache/nexrec-web-apache.conf)"
systemctl reload apache2 2>/dev/null || warn "apache2 reload skipped"

echo "$ROOT" > "$ETC/repo.path"
log "done. login admin / password (must change). VERSION=$(cat "$ROOT/VERSION")"
