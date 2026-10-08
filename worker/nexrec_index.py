#!/usr/bin/env python3
"""Probe closed MP4 chunks and upsert them into the chunk index.

The caller passes the open segment as skip_basename (latest filename
timestamp, not mtime). A ready row is left alone until the file's size
changes, which refreshes duration_s. A file ffprobe cannot read because
the moov atom is missing is left for a later pass instead of failing the
scan. Stills are a later pass so a filmstrip cannot delay the chunk row.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
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


# One timeline frame this many seconds. The recording chunk stays 5 minutes.
THUMB_STEP_S = 10.0
THUMB_LEAD_S = 1.0


def thumb_path_for(mp4: str) -> str:
    """JPEG filmstrip stored beside the recording. Removed with the MP4."""
    return mp4 + ".jpg"


def thumb_count_path_for(mp4: str) -> str:
    """How many 10-second frames are in the filmstrip. Missing means one legacy still."""
    return mp4 + ".jpg.n"


def thumb_sidecar_paths(mp4: str) -> list[str]:
    """Still, frame count, and the in-progress JPEG. All go away with the MP4."""
    jpg = thumb_path_for(mp4)
    return [jpg, thumb_count_path_for(mp4), jpg + ".tmp.jpg", thumb_count_path_for(mp4) + ".tmp"]


def recording_for_sidecar(name: str) -> str | None:
    """Recording basename for a timeline still, or None when ``name`` is not one.

    Longer suffixes are checked first so ``file.mp4.jpg.tmp.jpg`` is not
    treated as the finished JPEG.
    """
    for suffix in (".jpg.tmp.jpg", ".jpg.n.tmp", ".jpg.n", ".jpg"):
        tail = ".mp4" + suffix
        if name.endswith(tail):
            return name[: -len(suffix)]
    return None


def orphan_thumb_paths(dirpath: str, names: list[str], seen: set[str], now: float | None = None) -> list[str]:
    """Timeline files in this directory that no indexed recording owns.

    An in-progress JPEG younger than 10 minutes stays, so a still being
    written is not removed out from under ffmpeg.
    """
    when = time.time() if now is None else now
    drop: list[str] = []
    for name in names:
        owner_name = recording_for_sidecar(name)
        if owner_name is None:
            continue
        path = os.path.abspath(os.path.join(dirpath, name))
        owner = os.path.abspath(os.path.join(dirpath, owner_name))
        if name.endswith(".mp4.jpg.tmp.jpg") or name.endswith(".mp4.jpg.n.tmp"):
            try:
                age = when - os.path.getmtime(path)
            except OSError:
                age = 99999
            if age >= 600:
                drop.append(path)
            continue
        if owner not in seen:
            drop.append(path)
    return drop


def thumb_frame_count(duration_s: float | None) -> int:
    """Frames at 1s, 11s, 21s, … while the timestamp is inside the file."""
    try:
        duration = float(duration_s) if duration_s is not None else 0.0
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= THUMB_LEAD_S:
        return 1
    n = 0
    t = THUMB_LEAD_S
    while t < duration - 0.05 and n < 360:
        n += 1
        t += THUMB_STEP_S
    return n if n else 1


def _thumb_count_value(count_path: str) -> int | None:
    try:
        with open(count_path, encoding="ascii") as fh:
            raw = fh.read().strip()
    except OSError:
        return None
    try:
        n = int(raw)
    except ValueError:
        return None
    if n < 1:
        return None
    return n


def _thumb_current(dest: str, count_path: str, n: int) -> bool:
    if not (os.path.isfile(dest) and os.path.getsize(dest) > 64):
        return False
    got = _thumb_count_value(count_path)
    if got is None:
        return n <= 1
    return got == n


def _write_thumb_count(count_path: str, n: int) -> None:
    tmp = count_path + ".tmp"
    with open(tmp, "w", encoding="ascii") as fh:
        fh.write(str(int(n)) + "\n")
    os.replace(tmp, count_path)


_thumb_failed: set[str] = set()


def write_chunk_thumb(mp4: str, ffmpeg: str = "ffmpeg", duration_s: float | None = None) -> bool:
    """Filmstrip of one frame every 10 seconds. Safe to call again.

    A chunk that already has a still and no frame-count file is a legacy
    single picture. Callers that pass upgrade replace it. A short file
    keeps the one frame at 1 second.
    """
    dest = thumb_path_for(mp4)
    count_path = thumb_count_path_for(mp4)
    n = thumb_frame_count(duration_s)
    if _thumb_current(dest, count_path, n):
        return True
    if mp4 in _thumb_failed or not os.path.isfile(mp4):
        return False
    # image2 on this FFmpeg refuses a single still unless the name ends in
    # .jpg and -update 1 is set. A .part suffix makes it skip the file.
    tmp = dest + ".tmp.jpg"
    if n <= 1:
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-ss", "1", "-i", mp4,
            "-frames:v", "1", "-vf", "scale=320:-2",
            "-q:v", "5", "-update", "1", "-y", tmp,
        ]
        timeout = 30
    else:
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-i", mp4,
            "-vf", f"fps=fps=1/{int(THUMB_STEP_S)}:start_time={int(THUMB_LEAD_S)},scale=320:-2,tile={n}x1",
            "-frames:v", "1", "-q:v", "5", "-update", "1", "-y", tmp,
        ]
        timeout = 120
    try:
        proc = subprocess.run(cmd, check=False, timeout=timeout, capture_output=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _thumb_failed.add(mp4)
        print(f"thumb {mp4}: {exc}", file=sys.stderr)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    if not os.path.isfile(tmp) or os.path.getsize(tmp) < 64:
        _thumb_failed.add(mp4)
        err = getattr(proc, "stderr", None) or b""
        text = err.decode("utf-8", "replace").strip()
        if text:
            print(f"thumb {mp4}: {text}", file=sys.stderr)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    os.replace(tmp, dest)
    try:
        _write_thumb_count(count_path, n)
    except OSError as exc:
        print(f"thumb count {mp4}: {exc}", file=sys.stderr)
        return False
    return True


def backfill_thumbs(
    conn,
    ffmpeg: str,
    limit: int | None = 40,
    input_id: str | None = None,
    scan_limit: int | None = 500,
    on_result=None,
    upgrade: bool = False,
) -> int:
    """Write stills for closed chunks that do not have one yet. Newest first.

    ``limit`` is how many new stills to write. ``scan_limit`` is how many
    chunk rows to consider. None on either means no cap, which is the
    archive pass. ``upgrade`` replaces a legacy one-frame JPEG with a
    10-second filmstrip. The record loop leaves upgrade off so it does not
    re-encode the archive while a channel is recording.
    """
    sql = "SELECT path, duration_s FROM chunks WHERE ready=1 AND orphan=0"
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
        try:
            duration = row["duration_s"]
        except (KeyError, IndexError):
            duration = None
        dest = thumb_path_for(path)
        count_path = thumb_count_path_for(path)
        if upgrade:
            if _thumb_current(dest, count_path, thumb_frame_count(duration)):
                continue
        elif os.path.isfile(dest) and os.path.getsize(dest) > 64:
            continue
        ok = write_chunk_thumb(path, ffmpeg, duration_s=duration)
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


def ready_chunk_sizes(conn, input_id: str, kind: str) -> dict[str, int]:
    """Stored size_bytes for ready chunks, keyed by absolute path."""
    rows = conn.execute(
        "SELECT path, size_bytes FROM chunks WHERE input_id=? AND kind=? AND ready=1",
        (input_id, kind),
    ).fetchall()
    sizes: dict[str, int] = {}
    for row in rows:
        stored = str(row["path"] or "")
        if not stored:
            continue
        try:
            size = int(row["size_bytes"])
        except (TypeError, ValueError, KeyError):
            continue
        sizes[os.path.abspath(stored)] = size
    return sizes


def file_stat_sig(path: str) -> tuple[int, int] | None:
    """Size and mtime. None when the file disappeared between listing and stat."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (int(st.st_size), int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))))


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
    known: dict[str, tuple[int, int]] | None = None,
) -> list[dict[str, Any]]:
    """Upsert closed MP4s. Returns as soon as the chunk rows are committed.

    ``known`` remembers size and mtime already checked in this process. A
    ready row whose file size changed is probed again so duration_s matches
    the file, including a segment shorter than five minutes. Stills are not
    written here.
    """
    found: list[dict[str, Any]] = []
    if not os.path.isdir(root):
        return found
    ready = ready_chunk_paths(conn, input_id, kind)
    sizes: dict[str, int] = {}
    sizes_ok = known is None
    if known is not None:
        try:
            sizes = ready_chunk_sizes(conn, input_id, kind)
            sizes_ok = True
        except Exception:  # noqa: BLE001 — a size lookup must not skip new files
            sizes_ok = False
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".mp4"):
                continue
            if skip_basename and name == skip_basename:
                continue
            path = os.path.abspath(os.path.join(dirpath, name))
            sig = file_stat_sig(path)
            if sig is None:
                continue
            if path in ready:
                if known is None or not sizes_ok:
                    continue
                prev = known.get(path)
                if prev == sig:
                    continue
                stored = sizes.get(path)
                if prev is None and stored is not None and stored == sig[0]:
                    known[path] = sig
                    continue
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
            if known is not None:
                known[path] = file_stat_sig(path) or sig
            if rec:
                found.append(rec)
                ready.add(path)
                ready.add(os.path.abspath(rec["path"]))
                try:
                    sizes[path] = int(rec["size_bytes"])
                except (TypeError, ValueError, KeyError):
                    pass
    return found
