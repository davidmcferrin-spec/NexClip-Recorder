#!/usr/bin/env python3
"""Record-worker heartbeat and IP / DeckLink signal classification.

DeckLink lock needs a status tool (NEXREC_DECKLINK_STATUS_BIN). Without it
we store an honest unknown and still record last-chunk time.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from typing import Any

from nexrec_util import iso_z

IP_TYPES = {"rtsp", "srt", "udp", "tcp", "rtp"}
DECKLINK_UNKNOWN = "unknown — needs DeckLink tools"
_PROBE_AT: dict[str, float] = {}
_PROBE_CACHE: dict[str, dict[str, Any]] = {}


def classify_transport(source_type: str) -> str:
    t = (source_type or "").lower()
    if t == "decklink":
        return "sdi"
    if t in IP_TYPES:
        return "ip"
    if t == "testsrc":
        return "demo"
    return "other"


def classify_ip(
    proc_alive: bool,
    proc_age_s: float | None,
    chunk_age_s: float | None,
    segment_s: int,
) -> str:
    """receiving / stalled / down from worker liveness and last chunk age."""
    window = float(max(int(segment_s) * 2, 30))
    if not proc_alive:
        return "down"
    if chunk_age_s is None:
        if proc_age_s is not None and proc_age_s < window:
            return "receiving"
        return "stalled"
    if chunk_age_s <= window:
        return "receiving"
    return "stalled"


def _safe_status_bin(path: str) -> str | None:
    if not path or not path.startswith("/") or not os.path.isfile(path):
        return None
    if any(c in path for c in " \t;|&$`\n\r"):
        return None
    if not os.access(path, os.X_OK):
        return None
    return path


def parse_status_text(text: str) -> dict[str, Any]:
    signal = ""
    lock: int | None = None
    fmt = ""
    for raw in text.replace(",", "\n").splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().lower()
        v = v.strip()
        if k in ("signal", "state"):
            signal = v.lower()
        elif k in ("lock", "sdi_lock", "locked"):
            lock = 1 if v.lower() in ("1", "true", "yes", "locked", "present") else 0
        elif k in ("format", "format_code", "video_format"):
            fmt = v
    if signal not in ("present", "no_signal", "unknown"):
        if lock == 1:
            signal = "present"
        elif lock == 0:
            signal = "no_signal"
        else:
            signal = "unknown"
    return {"signal": signal, "sdi_lock": lock, "format": fmt[:80], "detail": "", "probe": "tool"}


def probe_decklink(device: str, env: dict[str, str] | None = None, now: float | None = None) -> dict[str, Any]:
    """Best-effort SDI status. Never uses a shell."""
    env = env or {}
    unknown = {
        "signal": "unknown",
        "sdi_lock": None,
        "format": "",
        "detail": DECKLINK_UNKNOWN,
        "probe": "unavailable",
    }
    bin_path = _safe_status_bin(env.get("NEXREC_DECKLINK_STATUS_BIN") or "")
    if bin_path is None or not device:
        return unknown
    import time

    stamp = now if now is not None else time.time()
    cache_key = device
    last = _PROBE_AT.get(cache_key, 0.0)
    if cache_key in _PROBE_CACHE and (stamp - last) < 15:
        return dict(_PROBE_CACHE[cache_key])
    try:
        proc = subprocess.run(
            [bin_path, device],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return unknown
    parsed = parse_status_text((proc.stdout or "") + "\n" + (proc.stderr or ""))
    if parsed["signal"] == "unknown" and not parsed["format"] and proc.returncode != 0:
        parsed["detail"] = DECKLINK_UNKNOWN
        parsed["probe"] = "unavailable"
    else:
        parsed["detail"] = ""
        parsed["probe"] = "tool"
    _PROBE_AT[cache_key] = stamp
    _PROBE_CACHE[cache_key] = parsed
    return dict(parsed)


def newest_mp4_mtime(root: str) -> float | None:
    newest = None
    if not root or not os.path.isdir(root):
        return None
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".mp4"):
                continue
            path = os.path.join(dirpath, name)
            try:
                m = os.path.getmtime(path)
            except OSError:
                continue
            if newest is None or m > newest:
                newest = m
    return newest


def write_heartbeat(
    conn,
    *,
    input_id: str,
    source_type: str,
    proc_alive: bool,
    started_at: float,
    segment_s: int,
    media_root: str,
    device: str = "",
    env: dict[str, str] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    import time

    env = env or {}
    stamp = now if now is not None else time.time()
    transport = classify_transport(source_type)
    chunk_m = newest_mp4_mtime(media_root)
    chunk_age = None if chunk_m is None else max(0.0, stamp - chunk_m)
    proc_age = max(0.0, stamp - started_at)
    last_chunk_at = None
    if chunk_m is not None:
        last_chunk_at = iso_z(datetime.fromtimestamp(chunk_m, tz=timezone.utc))

    sdi_lock: int | None = None
    fmt = ""
    detail = ""
    if transport == "sdi":
        probed = probe_decklink(device, env, now=stamp)
        signal = str(probed.get("signal") or "unknown")
        sdi_lock = probed.get("sdi_lock")
        fmt = str(probed.get("format") or "")
        detail = str(probed.get("detail") or "")
        if not proc_alive and signal == "present":
            signal = "no_signal"
            detail = (detail + " " if detail else "") + "record worker stopped"
    elif transport in ("ip", "demo"):
        signal = classify_ip(proc_alive, proc_age, chunk_age, segment_s)
        detail = ""
    else:
        signal = "receiving" if proc_alive else "down"
        detail = ""

    seen = iso_z(datetime.fromtimestamp(stamp, tz=timezone.utc))
    conn.execute(
        """
        INSERT INTO input_heartbeats (
          input_id, source_type, transport, signal, sdi_lock, format, detail, last_chunk_at, seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(input_id) DO UPDATE SET
          source_type=excluded.source_type,
          transport=excluded.transport,
          signal=excluded.signal,
          sdi_lock=excluded.sdi_lock,
          format=CASE WHEN excluded.format != '' THEN excluded.format ELSE input_heartbeats.format END,
          detail=excluded.detail,
          last_chunk_at=COALESCE(excluded.last_chunk_at, input_heartbeats.last_chunk_at),
          seen_at=excluded.seen_at
        """,
        (
            input_id,
            (source_type or "")[:32],
            transport,
            signal,
            sdi_lock,
            fmt,
            detail[:300],
            last_chunk_at,
            seen,
        ),
    )
    conn.commit()
    return {
        "input_id": input_id,
        "transport": transport,
        "signal": signal,
        "sdi_lock": sdi_lock,
        "format": fmt,
        "detail": detail,
        "last_chunk_at": last_chunk_at,
        "seen_at": seen,
    }
