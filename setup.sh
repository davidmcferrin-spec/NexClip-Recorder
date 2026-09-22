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
  apache2 libapache2-mod-php python3 sqlite3 \
  chrony 2>/dev/null || apt-get install -y \
  php-cli php-sqlite3 php-mbstring php-ldap php-curl php-xml \
  apache2 libapache2-mod-php python3 sqlite3

timedatectl set-timezone America/New_York 2>/dev/null || true
systemctl enable --now chrony 2>/dev/null || systemctl enable --now systemd-timesyncd 2>/dev/null || true

mkdir -p "$ETC/inputs" "$VAR/storage" "$VAR/auth" "$VAR/sessions"
chown -R www-data:www-data "$VAR"
if getent group video >/dev/null 2>&1; then
  usermod -aG video www-data || warn "could not add www-data to group video (DeckLink device nodes)"
fi
chmod 750 "$VAR" "$VAR/auth"

if [[ ! -f "$ETC/nexrec.env" ]]; then
  sed "s|^NEXREC_DATA_DIR=.*|NEXREC_DATA_DIR=$VAR|;s|^NEXREC_DB=.*|NEXREC_DB=$VAR/nexrec.db|" \
    "$ROOT/nexrec-example.env" > "$ETC/nexrec.env"
  chmod 640 "$ETC/nexrec.env"
  log "wrote $ETC/nexrec.env (bootstrap and secrets only — day-to-day settings are the Setup UI)"
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

# mediamtx.service is installed by bin/nexrec-install-media.sh so a foreign
# unit is not overwritten on every run.
for unit in "$ROOT"/systemd/*.service "$ROOT"/systemd/*.timer; do
  [[ -f "$unit" ]] || continue
  base="$(basename "$unit")"
  [[ "$base" == "mediamtx.service" ]] && continue
  install -m 644 "$unit" /etc/systemd/system/"$base"
done

# Point units at this clone if not using /opt.
if [[ "$ROOT" != "/opt/NexClip-Recorder" ]]; then
  for u in /etc/systemd/system/nexrec-*.service /etc/systemd/system/nexrec-*.timer; do
    [[ -f "$u" ]] || continue
    sed -i "s|/opt/NexClip-Recorder|$ROOT|g" "$u"
  done
fi

HELPER="$ROOT/bin/nexrec-systemctl.sh"
if [[ -f "$HELPER" ]]; then
  chown root:root "$HELPER"
  chmod 755 "$HELPER"
  SUDOERS=/etc/sudoers.d/nexrec-systemctl
  cat > "$SUDOERS" <<EOF
# NexCLIP Recorder ops console. The script rejects any unit or verb outside
# its allowlist. Do not grant www-data a general systemctl.
www-data ALL=(root) NOPASSWD: $HELPER
EOF
  chmod 440 "$SUDOERS"
  if command -v visudo >/dev/null 2>&1; then
    visudo -cf "$SUDOERS" || { rm -f "$SUDOERS"; warn "sudoers drop-in rejected"; }
  fi
  log "sudoers: www-data NOPASSWD $HELPER"
fi

systemctl daemon-reload
systemctl enable --now nexrec-export.service nexrec-analyze.service nexrec-cleanup.timer || warn "enable units failed"

# Pinned FFmpeg (source build) + MediaMTX release + optional decklink-status.
# Distro ffmpeg is not the DeckLink capture binary.
bash "$ROOT/bin/nexrec-install-media.sh" all

export NEXREC_ENV_FILE="$ETC/nexrec.env"
export NEXREC_DATA_DIR="$VAR"
export NEXREC_DB="$VAR/nexrec.db"
php "$ROOT/web/nexrec-auth-bootstrap.php"
FFPREFIX="${NEXREC_FFMPEG_PREFIX:-/usr/local}"
if [[ "$FFPREFIX" =~ ^/[A-Za-z0-9/_.-]+$ && -x "$FFPREFIX/bin/ffmpeg" && -f "$VAR/nexrec.db" ]]; then
  sqlite3 "$VAR/nexrec.db" "UPDATE app_settings SET value='$FFPREFIX/bin/ffmpeg' WHERE key='ffmpeg.path' AND value='/usr/bin/ffmpeg';"
  sqlite3 "$VAR/nexrec.db" "UPDATE app_settings SET value='$FFPREFIX/bin/ffprobe' WHERE key='ffmpeg.probe' AND value='/usr/bin/ffprobe';"
fi

CONF=/etc/apache2/conf-available/nexrec-web.conf
sed "s|@@APP_ROOT@@|$ROOT/web|g" "$ROOT/apache/nexrec-web-apache.conf" > "$CONF"
a2enmod rewrite >/dev/null
a2enconf nexrec-web >/dev/null || true
# DocumentRoot hint — do not blindly rewrite every vhost (NexVUE does this carefully).
log "enable Apache DocumentRoot $ROOT/web/public (see apache/nexrec-web-apache.conf)"
systemctl reload apache2 2>/dev/null || warn "apache2 reload skipped"

echo "$ROOT" > "$ETC/repo.path"
log "FFmpeg ${NEXREC_FFMPEG_VERSION:-9.0.2} is built into ${NEXREC_FFMPEG_PREFIX:-/usr/local} (libx264, openssl, optional fdk-aac/srt/zvbi/nvenc)."
log "DeckLink (--enable-decklink and nexrec-decklink-status) is included only when SDK headers are present. Desktop Video drivers are a separate Blackmagic package."
log "MediaMTX v1.21.1 serves WHEP. Do not enable nexrec-preview@ for DeckLink inputs."
log "Rebuild: NEXREC_FORCE_FFMPEG_BUILD=1. Replace MediaMTX: NEXREC_FORCE_MEDIAMTX=1."
log "done. login admin / password (must change). VERSION=$(cat "$ROOT/VERSION")"
