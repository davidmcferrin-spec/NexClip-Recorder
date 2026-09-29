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

# OS zone is station local so date, journalctl, and the cleanup timer follow
# America/New_York (DST included). Recorder units pin TZ=UTC; timecode is UTC.
configure_clock() {
  if timedatectl set-timezone America/New_York; then
    log "OS timezone America/New_York"
  else
    ln -sfn /usr/share/zoneinfo/America/New_York /etc/localtime
    printf 'America/New_York\n' > /etc/timezone
    warn "timedatectl set-timezone failed; linked /etc/localtime to America/New_York"
  fi

  if ! command -v chronyd >/dev/null 2>&1 && ! command -v chronyc >/dev/null 2>&1; then
    apt-get install -y -qq chrony || { warn "chrony is not installed; OS clock will not be disciplined"; return 0; }
  fi

  # timesyncd and chrony must not both step the clock.
  systemctl disable --now systemd-timesyncd >/dev/null 2>&1 || true

  mkdir -p /etc/chrony/sources.d
  cat > /etc/chrony/sources.d/nexrec.sources <<'EOF'
# NexCLIP Recorder NTP. minpoll and maxpoll are log2(seconds).
# 11 = 2048s (~34 min), 12 = 4096s (~68 min): about twice an hour,
# backing off to about once an hour when the offset is stable.
# iburst still samples quickly the first time chronyd starts.
pool ntp.ubuntu.com iburst minpoll 11 maxpoll 12 maxsources 4
EOF

  local conf=/etc/chrony/chrony.conf
  if [[ -f "$conf" ]]; then
    if ! grep -qE '^[[:space:]]*sourcedir[[:space:]]+/etc/chrony/sources.d[[:space:]]*$' "$conf"; then
      printf '\n# NexCLIP Recorder NTP sources\nsourcedir /etc/chrony/sources.d\n' >> "$conf"
    fi
    # Distro pool/server lines poll about once a minute. Comment them so
    # only nexrec.sources disciplines the clock. Idempotent: already-commented
    # lines do not match.
    sed -i -E 's/^(pool|server)[[:space:]]+/# nexrec: /' "$conf"
  else
    warn "missing $conf; wrote /etc/chrony/sources.d/nexrec.sources only"
  fi

  systemctl enable chrony >/dev/null 2>&1 || systemctl enable chronyd >/dev/null 2>&1 || true
  systemctl restart chrony >/dev/null 2>&1 || systemctl restart chronyd >/dev/null 2>&1 || true
  if command -v chronyc >/dev/null 2>&1; then
    chronyc makestep >/dev/null 2>&1 || true
  fi
  log "chrony corrects the OS clock about once or twice an hour"
}

if [[ "${1:-}" == "--check" ]]; then
  [[ -f "$ETC/nexrec.env" ]] || { echo "missing $ETC/nexrec.env"; exit 1; }
  set -a
  # shellcheck disable=SC1090
  source "$ETC/nexrec.env"
  set +a
  PGPASSWORD="${NEXREC_PGPASSWORD:-}" psql \
    -h "${NEXREC_PGHOST:-127.0.0.1}" \
    -p "${NEXREC_PGPORT:-5432}" \
    -U "${NEXREC_PGUSER:-nexrec}" \
    -d "${NEXREC_PGDATABASE:-nexrec}" \
    -v ON_ERROR_STOP=1 -c 'SELECT 1' >/dev/null \
    || { echo "postgres not reachable"; exit 1; }
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
  php-cli php-pgsql php-mbstring php-ldap php-curl php-xml \
  apache2 libapache2-mod-php python3 python3-psycopg2 \
  postgresql postgresql-contrib \
  chrony 2>/dev/null || apt-get install -y \
  php-cli php-pgsql php-mbstring php-ldap php-curl php-xml \
  apache2 libapache2-mod-php python3 python3-psycopg2 \
  postgresql postgresql-contrib

configure_clock

mkdir -p "$ETC/inputs" "$VAR/storage" "$VAR/auth" "$VAR/sessions"
chown -R www-data:www-data "$VAR"
if getent group video >/dev/null 2>&1; then
  usermod -aG video www-data || warn "could not add www-data to group video (DeckLink device nodes)"
fi
chmod 750 "$VAR" "$VAR/auth"

if [[ ! -f "$ETC/nexrec.env" ]]; then
  sed "s|^NEXREC_DATA_DIR=.*|NEXREC_DATA_DIR=$VAR|" \
    "$ROOT/nexrec-example.env" > "$ETC/nexrec.env"
  log "wrote $ETC/nexrec.env (bootstrap and secrets only — day-to-day settings are the Setup UI)"
else
  log "keeping existing $ETC/nexrec.env"
fi
chgrp www-data "$ETC/nexrec.env"
chmod 640 "$ETC/nexrec.env"
bash "$ROOT/bin/nexrec-install-postgres.sh" "$ETC/nexrec.env"

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
systemctl enable --now nexrec-export.service nexrec-analyze.service nexrec-cleanup.timer nexrec-metrics.timer || warn "enable units failed"

# Pinned FFmpeg (source build) + MediaMTX release + optional decklink-status.
# Distro ffmpeg is not the DeckLink capture binary.
bash "$ROOT/bin/nexrec-install-media.sh" all

export NEXREC_ENV_FILE="$ETC/nexrec.env"
export NEXREC_DATA_DIR="$VAR"
unset NEXREC_DB || true
php "$ROOT/web/nexrec-auth-bootstrap.php"
FFPREFIX="${NEXREC_FFMPEG_PREFIX:-/usr/local}"
if [[ "$FFPREFIX" =~ ^/[A-Za-z0-9/_.-]+$ && -x "$FFPREFIX/bin/ffmpeg" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ETC/nexrec.env"
  set +a
  PGPASSWORD="${NEXREC_PGPASSWORD}" psql \
    -h "${NEXREC_PGHOST:-127.0.0.1}" \
    -p "${NEXREC_PGPORT:-5432}" \
    -U "${NEXREC_PGUSER}" \
    -d "${NEXREC_PGDATABASE}" \
    -v ON_ERROR_STOP=1 <<SQL
UPDATE app_settings SET value='${FFPREFIX}/bin/ffmpeg' WHERE key='ffmpeg.path' AND value='/usr/bin/ffmpeg';
UPDATE app_settings SET value='${FFPREFIX}/bin/ffprobe' WHERE key='ffmpeg.probe' AND value='/usr/bin/ffprobe';
SQL
fi

# The live tree is the checkout this script ran from (systemd units are rewritten
# to ROOT). The /opt rsync is a copy and is not the Directory grant.
PUBLIC="$ROOT/web/public"
CONF=/etc/apache2/conf-available/nexrec-web.conf
sed "s|@@APP_ROOT@@|$ROOT/web|g" "$ROOT/apache/nexrec-web-apache.conf" > "$CONF"
chmod 644 "$CONF"
a2enmod rewrite >/dev/null
a2enconf nexrec-web >/dev/null || warn "a2enconf nexrec-web failed"
if [[ -e /etc/apache2/sites-available/000-default.conf ]]; then
  a2ensite 000-default >/dev/null || warn "a2ensite 000-default failed"
fi

# www-data must traverse every parent. A clone under /home/<user> (mode 750)
# is otherwise AH01630 or (13) Permission denied after DocumentRoot is set.
nexrec_open_parents() {
  local dir="$1" mode other
  while [[ "$dir" != "/" ]]; do
    mode="$(stat -c %a "$dir" 2>/dev/null || echo "")"
    other="${mode: -1}"
    if [[ -n "$other" && $((other & 1)) -eq 0 ]]; then
      chmod o+x "$dir" || warn "could not add other-execute on $dir"
      log "traverse for www-data: chmod o+x $dir"
    fi
    dir="$(dirname "$dir")"
  done
}
nexrec_open_parents "$PUBLIC"

# Rewrite only the Ubuntu default docroot and a previous /opt copy. Leave any
# other vhost (a co-hosted app) alone.
if command -v python3 >/dev/null 2>&1; then
  python3 - "$PUBLIC" "$APP_ROOT/web/public" <<'PY' || warn "DocumentRoot patch failed"
import pathlib, re, sys
public, opt_public = sys.argv[1], sys.argv[2]
targets = {"/var/www/html", "/var/www/html/"}
if opt_public != public:
    targets.add(opt_public)
    targets.add(opt_public.rstrip("/") + "/")
pat = re.compile(
    r'^(?P<prefix>\s*DocumentRoot\s+)"?(?P<path>/[^"\s#]+)"?(?P<suffix>\s*(?:#.*)?)$',
    re.M,
)
seen = set()
patched = 0
for dname in ("sites-available", "sites-enabled"):
    d = pathlib.Path("/etc/apache2") / dname
    if not d.is_dir():
        continue
    for p in sorted(d.iterdir()):
        try:
            real = p.resolve()
        except OSError:
            continue
        if real in seen or not real.is_file():
            continue
        seen.add(real)
        try:
            text = real.read_text(encoding="utf-8")
        except OSError:
            continue
        def repl(m, _text=text):
            path = m.group("path")
            if path in targets or path.rstrip("/") in {t.rstrip("/") for t in targets}:
                quote = '"' if '"' in m.group(0) else ""
                return f'{m.group("prefix")}{quote}{public}{quote}{m.group("suffix")}'
            return m.group(0)
        new, n = pat.subn(repl, text)
        if new != text:
            real.write_text(new, encoding="utf-8")
            patched += n
            print(f"[nexrec-setup] DocumentRoot → {public} in {real}")
if patched == 0 and not any(
    re.search(r'^\s*DocumentRoot\s+"?' + re.escape(public) + r'/?"?', p.read_text(encoding="utf-8", errors="replace"), re.M)
    for p in seen
):
    print(f"[nexrec-setup] WARN no DocumentRoot updated — set DocumentRoot {public} in the site vhost", file=sys.stderr)
PY
else
  warn "python3 missing — set DocumentRoot $PUBLIC in 000-default.conf manually"
fi

systemctl enable apache2 >/dev/null 2>&1 || true
if apache2ctl configtest >/dev/null 2>&1; then
  systemctl reload apache2 2>/dev/null || warn "apache2 reload skipped"
  log "Apache DocumentRoot $PUBLIC"
else
  warn "apache2ctl configtest failed — DocumentRoot should be $PUBLIC"
fi

echo "$ROOT" > "$ETC/repo.path"
log "Local PostgreSQL role and database are configured. The password is only in $ETC/nexrec.env."
log "FFmpeg ${NEXREC_FFMPEG_VERSION:-9.0.2} is built into ${NEXREC_FFMPEG_PREFIX:-/usr/local} (libx264, openssl, optional fdk-aac/srt/zvbi, and nvenc/nvdec when an NVIDIA GPU is present)."
log "DeckLink (--enable-decklink, nexrec-decklink-status, nexrec-decklink-configure) is included only when SDK headers are present. Configure sets Quad 2 / Duo 2 to one input per BNC. Desktop Video drivers are a separate Blackmagic package."
log "MediaMTX v1.21.1 serves WHEP. Do not enable nexrec-preview@ for DeckLink inputs."
log "Rebuild: NEXREC_FORCE_FFMPEG_BUILD=1. Replace MediaMTX: NEXREC_FORCE_MEDIAMTX=1."
log "done. login admin / password (must change). VERSION=$(cat "$ROOT/VERSION")"
