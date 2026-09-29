#!/usr/bin/env python3
"""Probe closed MP4 chunks and upsert them into the chunk index.

Paths that already have a ready chunks row are left alone. The caller
passes the open segment as skip_basename (latest filename timestamp, not
mtime). A file ffprobe cannot read because the moov atom is missing is
left for a later pass instead of failing the scan.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
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


class IncompleteChunk(RuntimeError):
    """ffprobe cannot read this MP4 yet. The moov atom is written when the segment closes."""


def incomplete_stderr(err: str) -> bool:
    return "moov atom not found" in (err or "").lower()


def probe(path: str, ffprobe: str = "ffprobe") -> dict[str, Any]:
    cmd = [
        ffprobe, "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        err = proc.stderr.strip() or "ffprobe failed"
        if incomplete_stderr(err):
            raise IncompleteChunk(err)
        raise RuntimeError(err)
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


def thumb_path_for(mp4: str) -> str:
    """JPEG still stored beside the recording. Removed with the MP4."""
    return mp4 + ".jpg"


_thumb_failed: set[str] = set()


def write_chunk_thumb(mp4: str, ffmpeg: str = "ffmpeg") -> bool:
    """One frame from the start of a closed chunk. Safe to call again."""
    dest = thumb_path_for(mp4)
    if os.path.isfile(dest) and os.path.getsize(dest) > 64:
        return True
    if mp4 in _thumb_failed or not os.path.isfile(mp4):
        return False
    tmp = dest + ".part"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        "1",
        "-i",
        mp4,
        "-frames:v",
        "1",
        "-vf",
        "scale=320:-2",
        "-q:v",
        "5",
        "-y",
        tmp,
    ]
    try:
        subprocess.run(cmd, check=False, timeout=30, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        _thumb_failed.add(mp4)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    if not os.path.isfile(tmp) or os.path.getsize(tmp) < 64:
        _thumb_failed.add(mp4)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    os.replace(tmp, dest)
    return True


def backfill_thumbs(
    conn,
    ffmpeg: str,
    limit: int | None = 40,
    input_id: str | None = None,
    scan_limit: int | None = 500,
    on_result=None,
) -> int:
    """Write stills for closed chunks that do not have one yet. Newest first.

    ``limit`` is how many new stills to write. ``scan_limit`` is how many
    chunk rows to consider. None on either means no cap, which is the
    archive pass.
    """
    sql = "SELECT path FROM chunks WHERE ready=1 AND orphan=0"
    args: list[Any] = []
    if input_id:
        sql += " AND input_id=?"
        args.append(input_id)
    sql += " ORDER BY start_at DESC"
    if scan_limit is not None:
        sql += " LIMIT ?"
        args.append(int(scan_limit))
    n = 0
    for row in conn.execute(sql, tuple(args)).fetchall():
        path = str(row["path"] or "")
        if not path or not os.path.isfile(path):
            continue
        dest = thumb_path_for(path)
        if os.path.isfile(dest) and os.path.getsize(dest) > 64:
            continue
        ok = write_chunk_thumb(path, ffmpeg)
        if on_result is not None:
            on_result(path, ok)
        if ok:
            n += 1
        if limit is not None and n >= limit:
            break
    return n


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


def open_segment_basename(root: str) -> str | None:
    """Basename of the segment still being written.

    Names encode the segment start (``id_YYYYMMDDTHHMMSSZ.mp4``). The greatest
    timestamp is the open file. mtime is the wrong signal: ``movflags=faststart``
    rewrites the segment that just closed, so that closed file is newer than
    the one ffmpeg has open.
    """
    latest_ts = ""
    latest_name: str | None = None
    if not os.path.isdir(root):
        return None
    for _dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".mp4"):
                continue
            match = FNAME_RE.match(name)
            if not match:
                continue
            ts = match.group("ts")
            if ts > latest_ts:
                latest_ts = ts
                latest_name = name
    return latest_name


def scan_dir(
    conn,
    root: str,
    input_id: str,
    kind: str = "native",
    ffprobe: str = "ffprobe",
    skip_basename: str | None = None,
    pending: dict[str, tuple[int, int]] | None = None,
    ffmpeg: str | None = None,
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
            try:
                st = os.stat(path)
            except OSError:
                continue
            sig = (int(st.st_size), int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))))
            if pending is not None and pending.get(path) == sig:
                continue
            try:
                rec = index_file(conn, path, input_id, kind=kind, ffprobe=ffprobe)
            except IncompleteChunk as exc:
                if pending is not None and path not in pending:
                    print(f"index wait {path}: {exc}", file=sys.stderr, flush=True)
                if pending is not None:
                    pending[path] = sig
                continue
            if pending is not None:
                pending.pop(path, None)
            if rec:
                found.append(rec)
                ready.add(path)
                ready.add(os.path.abspath(rec["path"]))
                if ffmpeg:
                    try:
                        write_chunk_thumb(rec["path"], ffmpeg)
                    except OSError as exc:
                        print(f"thumb skip {rec['path']}: {exc}", file=sys.stderr, flush=True)
    return found
