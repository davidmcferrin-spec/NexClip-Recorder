#!/usr/bin/env python3
"""Shared helpers for NexCLIP Recorder workers. Stdlib only."""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

INPUT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
SIZE_RE = re.compile(r"^(\d+)\s*([KMGkmg])?B?$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime | None = None) -> str:
    dt = dt or utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def valid_input_id(value: str) -> bool:
    return bool(INPUT_ID_RE.match(value or ""))


def parse_bytes(value: str | int) -> int:
    """Parse '50G', '512M', '1048576' into bytes."""
    if isinstance(value, int):
        return value
    raw = str(value).strip().replace(",", "")
    m = SIZE_RE.match(raw)
    if not m:
        raise ValueError(f"invalid size: {value!r}")
    n = int(m.group(1))
    suf = (m.group(2) or "").upper()
    mul = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}[suf]
    return n * mul


def load_env_file(path: str, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Parse KEY=value env files (bash-style quotes). Does not execute."""
    out = dict(environ if environ is not None else os.environ)
    if not path or not os.path.isfile(path):
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                val = val[1:-1]
            out[key] = val
    return out


def env_bool(env: dict[str, str], key: str, default: bool = False) -> bool:
    raw = str(env.get(key, "1" if default else "0")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def env_int(env: dict[str, str], key: str, default: int) -> int:
    try:
        return int(str(env.get(key, default)).strip())
    except (TypeError, ValueError):
        return default


def data_paths(env: dict[str, str]) -> dict[str, str]:
    data = env.get("NEXREC_DATA_DIR") or os.path.abspath("./data")
    storage = env.get("NEXREC_STORAGE_DIR") or os.path.join(data, "storage")
    return {
        "data": data,
        "storage": storage,
        "database": env.get("NEXREC_PGDATABASE") or "nexrec",
        "ffmpeg": env.get("NEXREC_FFMPEG") or "ffmpeg",
        "ffprobe": env.get("NEXREC_FFPROBE") or "ffprobe",
    }


def chunk_dir(storage: str, input_id: str, kind: str, when: datetime | None = None) -> str:
    when = when or utcnow()
    return os.path.join(
        storage,
        "inputs",
        input_id,
        kind,
        f"{when.year:04d}",
        f"{when.month:02d}",
        f"{when.day:02d}",
    )


def chunk_pattern(storage: str, input_id: str, kind: str = "native") -> str:
    """strftime pattern for FFmpeg -strftime 1. Date dirs from *start* of run;
    FFmpeg will not create nested date dirs per segment, so we use a flat
    day directory and start a new process at midnight via systemd Restart —
    plus the watcher mkdir's today's dir if the process crossed UTC midnight.
    """
    base = os.path.join(storage, "inputs", input_id, kind)
    # One directory per calendar day (UTC). Recorder creates it before exec.
    return os.path.join(base, "%Y", "%m", "%d", f"{input_id}_%Y%m%dT%H%M%SZ.mp4")


def expand_chunk_pattern(storage: str, input_id: str, kind: str, when: datetime) -> str:
    return os.path.join(
        chunk_dir(storage, input_id, kind, when),
        f"{input_id}_{when.strftime('%Y%m%dT%H%M%SZ')}.mp4",
    )


def wallclock_timecode(when: datetime | None = None, fps: float = 30.0) -> str:
    """HH:MM:SS:FF from the system clock (NTP), not a free-running source TC."""
    when = when or datetime.now()  # local wall clock for studio TC
    ff = int((when.microsecond / 1_000_000.0) * fps)
    if ff >= int(round(fps)):
        ff = int(round(fps)) - 1
    return f"{when.hour:02d}:{when.minute:02d}:{when.second:02d}:{ff:02d}"


def json_ready(obj: Any) -> Any:
    return obj
