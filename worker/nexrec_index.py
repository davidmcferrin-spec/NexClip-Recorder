#!/usr/bin/env python3
"""Probe closed MP4 chunks and upsert them into the chunk index.

Paths that already have a ready chunks row are left alone. The caller
still passes the newest open segment as skip_basename.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import timedelta
from typing import Any

from nexrec_db import insert_chunk
from nexrec_util import iso_z, new_id, parse_iso, utcnow

FNAME_RE = re.compile(
    r"^(?P<id>[a-z0-9-]+)_(?P<ts>\d{8}T\d{6}Z)\.mp4$"
)


def start_from_filename(path: str) -> str | None:
    base = os.path.basename(path)
    m = FNAME_RE.match(base)
    if not m:
        return None
    ts = m.group("ts")  # YYYYMMDDTHHMMSSZ
    return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T{ts[9:11]}:{ts[11:13]}:{ts[13:15]}Z"


def probe(path: str, ffprobe: str = "ffprobe") -> dict[str, Any]:
    cmd = [
        ffprobe, "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffprobe failed")
    data = json.loads(proc.stdout or "{}")
    fmt = data.get("format") or {}
    duration = float(fmt.get("duration") or 0)
    size = int(float(fmt.get("size") or 0))
    v = next((s for s in data.get("streams") or [] if s.get("codec_type") == "video"), {})
    a = next((s for s in data.get("streams") or [] if s.get("codec_type") == "audio"), {})
    fps = 0.0
    rate = v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1"
    if isinstance(rate, str) and "/" in rate:
        num, den = rate.split("/", 1)
        try:
            fps = float(num) / float(den) if float(den) else 0.0
        except ValueError:
            fps = 0.0
    field = str(v.get("field_order") or "")
    interlaced = 1 if field and field not in ("progressive", "unknown", "") else 0
    tc = ""
    tags = fmt.get("tags") or {}
    tc = str(tags.get("timecode") or tags.get("TIMECODE") or "")
    if not tc:
        tc = str((v.get("tags") or {}).get("timecode") or "")
    return {
        "duration_s": duration,
        "size_bytes": size,
        "width": int(v.get("width") or 0) or None,
        "height": int(v.get("height") or 0) or None,
        "fps": fps or None,
        "interlaced": interlaced,
        "codec": v.get("codec_name") or None,
        "audio_codec": a.get("codec_name") or None,
        "timecode_start": tc or None,
    }


def index_file(
    conn,
    path: str,
    input_id: str,
    kind: str = "native",
    ffprobe: str = "ffprobe",
) -> dict[str, Any] | None:
    if not os.path.isfile(path) or os.path.getsize(path) < 64:
        return None
    start = start_from_filename(path)
    if not start:
        return None
    info = probe(path, ffprobe=ffprobe)
    start_dt = parse_iso(start)
    end = None
    if info["duration_s"]:
        end = iso_z(start_dt + timedelta(seconds=float(info["duration_s"])))
    rec = {
        "id": new_id("chk"),
        "input_id": input_id,
        "path": os.path.abspath(path),
        "kind": kind,
        "start_at": start,
        "end_at": end,
        "duration_s": info["duration_s"] or None,
        "size_bytes": info["size_bytes"] or None,
        "width": info["width"],
        "height": info["height"],
        "fps": info["fps"],
        "interlaced": info["interlaced"],
        "codec": info["codec"],
        "timecode_start": info["timecode_start"],
        "ready": 1,
        "orphan": 0,
        "created_at": iso_z(utcnow()),
    }
    insert_chunk(conn, rec)
    return rec


def ready_chunk_paths(conn, input_id: str, kind: str) -> set[str]:
    """Absolute paths already stored as ready chunks for this input."""
    rows = conn.execute(
        "SELECT path FROM chunks WHERE input_id=? AND kind=? AND ready=1",
        (input_id, kind),
    ).fetchall()
    ready: set[str] = set()
    for row in rows:
        stored = str(row["path"] or "")
        if not stored:
            continue
        ready.add(stored)
        ready.add(os.path.abspath(stored))
    return ready


def scan_dir(
    conn,
    root: str,
    input_id: str,
    kind: str = "native",
    ffprobe: str = "ffprobe",
    skip_basename: str | None = None,
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if not os.path.isdir(root):
        return found
    ready = ready_chunk_paths(conn, input_id, kind)
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".mp4"):
                continue
            if skip_basename and name == skip_basename:
                continue
            path = os.path.abspath(os.path.join(dirpath, name))
            if path in ready:
                continue
            rec = index_file(conn, path, input_id, kind=kind, ffprobe=ffprobe)
            if rec:
                found.append(rec)
                ready.add(path)
                ready.add(os.path.abspath(rec["path"]))
    return found
