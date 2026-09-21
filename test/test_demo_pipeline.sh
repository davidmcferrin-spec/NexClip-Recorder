#!/usr/bin/env bash
# Vertical slice: testsrc → 5s MP4 chunks → concat/trim export.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/nexrec-demo.XXXXXX")"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

export PYTHONPATH="$ROOT/worker"
export NEXREC_DATA_DIR="$WORKDIR"
export NEXREC_STORAGE_DIR="$WORKDIR/storage"
export NEXREC_DB="$WORKDIR/nexrec.db"
export NEXREC_SEGMENT_SECONDS=5
export NEXREC_SEGMENT_AT_CLOCK=0
export NEXREC_BROADCAST_VIDEO_BITRATE=1M
export NEXREC_X264_PRESET=ultrafast
mkdir -p "$WORKDIR/storage"

python3 - <<'PY'
import os, sys
sys.path.insert(0, os.environ.get("PYTHONPATH", "."))
from nexrec_db import connect, migrate, upsert_input
from nexrec_util import iso_z
conn = connect(os.environ["NEXREC_DB"])
migrate(conn)
upsert_input(conn, {
    "id": "demo", "name": "Demo", "source_type": "testsrc", "url": "",
    "decklink_device": "", "decklink_format": "",
    "enabled": 1, "live_transcode": 1, "copy_native": 0, "upconvert_1080i": 0,
    "video_bitrate": "1M", "audio_bitrate": "64k", "retention_days": 28,
    "preview_path": "in0", "preview_enabled": 1,
    "created_at": iso_z(), "updated_at": iso_z(),
})
print("seeded input demo")
PY

python3 "$ROOT/worker/nexrec-record.py" \
  --input-id demo \
  --once-seconds 14 \
  --segment-seconds 5

python3 - <<'PY'
import os, sys, json
sys.path.insert(0, os.environ.get("PYTHONPATH", "."))
from nexrec_db import connect, fetchall, enqueue_export
from nexrec_util import iso_z, parse_iso, new_id, utcnow
from datetime import timedelta
conn = connect(os.environ["NEXREC_DB"])
rows = fetchall(conn, "SELECT * FROM chunks WHERE ready=1 ORDER BY start_at")
print("chunks", len(rows))
if len(rows) < 1:
    raise SystemExit("expected at least one indexed chunk")
t_in = rows[0]["start_at"]
# Trim 2 seconds starting 0.5s into the first chunk (or across if longer).
start = parse_iso(t_in)
t_out = iso_z(start + timedelta(seconds=2.5))
enqueue_export(conn, {
    "id": "exp_demo",
    "status": "queued",
    "input_ids": json.dumps(["demo"]),
    "t_in": iso_z(start + timedelta(seconds=0.4)),
    "t_out": t_out,
    "quality": "full",
    "scope": "one",
    "path": None,
    "size_bytes": None,
    "protected": 0,
    "error": None,
    "created_by": "demo",
    "created_at": iso_z(),
    "expires_at": None,
    "nexclip_schedule_id": None,
})
print("queued exp_demo", t_in, "->", t_out)
PY

python3 "$ROOT/worker/nexrec-export.py" --once --job-id exp_demo

python3 - <<'PY'
import os, sys, json, subprocess
sys.path.insert(0, os.environ.get("PYTHONPATH", "."))
from nexrec_db import connect, fetchone
conn = connect(os.environ["NEXREC_DB"])
job = fetchone(conn, "SELECT * FROM exports WHERE id='exp_demo'")
print(job)
if not job or job["status"] != "done" or not job.get("path"):
    raise SystemExit("export not done: " + str(job))
path = job["path"]
if not os.path.isfile(path) or os.path.getsize(path) < 1000:
    raise SystemExit("export file missing/small")
probe = subprocess.check_output([
    "ffprobe", "-v", "error", "-print_format", "json",
    "-show_format", "-show_streams", path,
], text=True)
data = json.loads(probe)
fmt = data.get("format") or {}
dur = float(fmt.get("duration") or 0)
if dur < 1.0:
    raise SystemExit(f"duration too short: {dur}")
codecs = {s.get("codec_name") for s in data.get("streams") or []}
if "h264" not in codecs or "aac" not in codecs:
    raise SystemExit(f"expected h264+aac, got {codecs}")
print("DEMO OK", path, "duration", dur, "size", os.path.getsize(path))
PY
