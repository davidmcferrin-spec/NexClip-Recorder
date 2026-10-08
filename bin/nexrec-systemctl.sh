#!/usr/bin/env bash
# Allowlisted systemctl / journalctl for the NexCLIP Recorder ops console.
#
# The web UI must not interpolate unit names into a shell. This script accepts
# only known verbs and unit-name patterns, then execs systemctl/journalctl
# with a fixed argument vector.
#
# Install (setup.sh writes this drop-in; root-owned script, mode 755):
#
#   /etc/sudoers.d/nexrec-systemctl
#   www-data ALL=(root) NOPASSWD: /opt/NexClip-Recorder/bin/nexrec-systemctl.sh
#
# Invoke: sudo -n nexrec-systemctl.sh <verb> <unit> [lines|window]
# Verbs: start stop restart enable disable is-active is-enabled show journal journal-since
# journal-since window is 24, 48, 72, or all. all is whatever journald still has.
#
# Tests may point NEXREC_SYSTEMCTL_BIN / NEXREC_JOURNALCTL_BIN at fakes.
set -euo pipefail

SYSTEMCTL="${NEXREC_SYSTEMCTL_BIN:-}"
JOURNALCTL="${NEXREC_JOURNALCTL_BIN:-}"

if [[ -z "$SYSTEMCTL" ]]; then
  if [[ -x /bin/systemctl ]]; then
    SYSTEMCTL=/bin/systemctl
  elif [[ -x /usr/bin/systemctl ]]; then
    SYSTEMCTL=/usr/bin/systemctl
  else
    SYSTEMCTL=/bin/systemctl
  fi
fi
if [[ -z "$JOURNALCTL" ]]; then
  if [[ -x /bin/journalctl ]]; then
    JOURNALCTL=/bin/journalctl
  elif [[ -x /usr/bin/journalctl ]]; then
    JOURNALCTL=/usr/bin/journalctl
  else
    JOURNALCTL=/bin/journalctl
  fi
fi

usage() {
  echo "usage: nexrec-systemctl.sh <verb> <unit> [lines|window]" >&2
  exit 2
}

[[ $# -ge 2 && $# -le 3 ]] || usage

VERB="$1"
UNIT="$2"
LINES="${3:-80}"

case "$VERB" in
  start|stop|restart|enable|disable|is-active|is-enabled|show)
    [[ $# -eq 2 ]] || usage
    ;;
  journal)
    ;;
  journal-since)
    [[ $# -eq 3 ]] || usage
    ;;
  *)
    echo "verb not allowed" >&2
    exit 2
    ;;
esac

# Fixed units plus per-input record/preview instances.
# Input ids match the app: [a-z0-9][a-z0-9-]{0,31}
UNIT_RE='^(mediamtx\.service|nexrec-export\.service|nexrec-deliver\.service|nexrec-cleanup\.(service|timer)|nexrec-analyze\.service|nexrec-nexclip\.(service|timer)|nexrec-decklink-configure\.service|nexrec-(record|preview)@[a-z0-9][a-z0-9-]{0,31}\.service)$'
# Support packs also include metrics and Apache. Those stay off the start/stop list.
LOG_UNIT_RE='^(mediamtx\.service|nexrec-export\.service|nexrec-deliver\.service|nexrec-cleanup\.(service|timer)|nexrec-analyze\.service|nexrec-nexclip\.(service|timer)|nexrec-decklink-configure\.service|nexrec-metrics\.service|apache2\.service|nexrec-(record|preview)@[a-z0-9][a-z0-9-]{0,31}\.service)$'
if [[ "$VERB" == "journal-since" ]]; then
  if [[ ! "$UNIT" =~ $LOG_UNIT_RE ]]; then
    echo "unit not allowed" >&2
    exit 2
  fi
elif [[ ! "$UNIT" =~ $UNIT_RE ]]; then
  echo "unit not allowed" >&2
  exit 2
fi

case "$VERB" in
  start|stop|restart|enable|disable)
    exec "$SYSTEMCTL" "$VERB" "$UNIT"
    ;;
  is-active|is-enabled)
    set +e
    "$SYSTEMCTL" "$VERB" "$UNIT"
    exit $?
    ;;
  show)
    exec "$SYSTEMCTL" show "$UNIT" \
      -p Id -p ActiveState -p SubState -p UnitFileState \
      -p ActiveEnterTimestamp -p LoadState \
      --no-pager
    ;;
  journal)
    if [[ ! "$LINES" =~ ^[0-9]+$ ]] || [[ "$LINES" -lt 1 || "$LINES" -gt 200 ]]; then
      echo "bad line count" >&2
      exit 2
    fi
    exec "$JOURNALCTL" -u "$UNIT" -n "$LINES" --no-pager -o short-iso
    ;;
  journal-since)
    case "$LINES" in
      24|48|72)
        exec "$JOURNALCTL" -u "$UNIT" --since "${LINES} hours ago" --no-pager -o short-iso
        ;;
      all)
        exec "$JOURNALCTL" -u "$UNIT" --no-pager -o short-iso
        ;;
      *)
        echo "bad window" >&2
        exit 2
        ;;
    esac
    ;;
esac
