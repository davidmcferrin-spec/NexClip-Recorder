#!/usr/bin/env python3
"""Best-effort Nielsen watermark *presence* log. Not a decoder.

Owner decision: do not integrate the Nielsen Decoder SDK and do not report
SID, watermark timestamps, or code layers.

`NielsenPresenceDetector` is the swap point. The builtin `sample()` does not
inspect a proprietary code; it reports each window as not detected. Replace
the class, pass `detector=` into `detect_nielsen_presence`, or set
`NEXREC_NIELSEN_PRESENCE_CMD` to a command that writes presence JSON only.

Every result is `audit_grade: false`. A stub "absent" means this detector
did not see a watermark. It is not proof of absence.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from typing import Any, Protocol

PRESENCE_NOTE = (
    "Best-effort Nielsen watermark presence only. Not audit-grade decode. "
    "The Nielsen Decoder SDK is not used. SID, watermark time, and layer are not decoded."
)

# Keys a future tool might emit. Never store them — presence is not a decode.
_DECODE_KEYS = frozenset(
    {
        "sid",
        "nielsen_sid",
        "layer",
        "layers",
        "timestamp",
        "timestamps",
        "code_time",
        "code_timestamp",
        "watermark_time",
        "nielsen_time",
    }
)

DEFAULT_WINDOW_S = 30.0
MAX_WINDOWS = 480


class NielsenPresenceDetector(Protocol):
    """Object with sample(path, pts, pts_end) -> bool. Swap this later."""

    def sample(self, path: str, pts: float, pts_end: float) -> bool: ...


class StubNielsenPresenceDetector:
    """Builtin detector. Reports not-detected for every window.

    `method` is stored on each event so a replacement can identify itself.
    """

    method = "stub"

    def sample(self, path: str, pts: float, pts_end: float) -> bool:
        del path, pts, pts_end
        return False


def presence_windows(duration_s: float, window_s: float = DEFAULT_WINDOW_S) -> list[tuple[float, float]]:
    """Split [0, duration] into contiguous windows that cover the chunk."""
    try:
        duration = float(duration_s)
    except (TypeError, ValueError):
        duration = 0.0
    try:
        step = float(window_s)
    except (TypeError, ValueError):
        step = DEFAULT_WINDOW_S
    if step <= 0:
        step = DEFAULT_WINDOW_S
    if duration <= 0:
        duration = step
    if duration / step > MAX_WINDOWS:
        step = duration / MAX_WINDOWS
    out: list[tuple[float, float]] = []
    t = 0.0
    while t + 1e-6 < duration and len(out) < MAX_WINDOWS:
        end = min(duration, t + step)
        if end <= t:
            break
        out.append((round(t, 3), round(end, 3)))
        t = end
    if not out:
        out.append((0.0, round(step, 3)))
    return out


def coalesce_presence(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge adjacent windows that share the same present flag."""
    spans: list[dict[str, Any]] = []
    for sample in samples:
        present = bool(sample.get("present"))
        pts = float(sample["pts"])
        pts_end = float(sample["pts_end"])
        if pts_end < pts:
            pts, pts_end = pts_end, pts
        if spans and spans[-1]["present"] is present and abs(spans[-1]["pts_end"] - pts) < 0.05:
            spans[-1]["pts_end"] = pts_end
            continue
        spans.append({"pts": pts, "pts_end": pts_end, "present": present})
    return spans


def normalize_presence_payload(data: Any) -> list[dict[str, Any]]:
    """Keep pts/pts_end/present. Drop SID, layer, and watermark-time fields."""
    if isinstance(data, dict):
        data = data.get("windows") or data.get("samples") or data.get("spans") or []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if "present" not in item:
            continue
        try:
            pts = float(item.get("pts") if item.get("pts") is not None else item.get("t_start") or 0.0)
            pts_end = float(
                item.get("pts_end") if item.get("pts_end") is not None else item.get("t_end") or pts
            )
        except (TypeError, ValueError):
            continue
        present_raw = item.get("present")
        if isinstance(present_raw, str):
            present = present_raw.strip().lower() in ("1", "true", "yes", "present")
        else:
            present = bool(present_raw)
        out.append({"pts": pts, "pts_end": pts_end, "present": present})
    out.sort(key=lambda s: (s["pts"], s["pts_end"]))
    return out


def _window_s(env: dict[str, str] | None, window_s: float | None) -> float:
    if window_s is not None:
        try:
            value = float(window_s)
        except (TypeError, ValueError):
            value = DEFAULT_WINDOW_S
        return value if value > 0 else DEFAULT_WINDOW_S
    raw = (env or {}).get("NEXREC_NIELSEN_WINDOW_S") or ""
    if not str(raw).strip():
        return DEFAULT_WINDOW_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_S
    return value if value > 0 else DEFAULT_WINDOW_S


def _hits(
    spans: list[dict[str, Any]],
    method: str,
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for span in spans:
        present = bool(span["present"])
        state = "present" if present else "absent"
        payload: dict[str, Any] = {
            "present": present,
            "audit_grade": False,
            "method": method,
            "decoded": False,
            "note": PRESENCE_NOTE,
        }
        if extra:
            for key, value in extra.items():
                if key in _DECODE_KEYS or key in ("present", "audit_grade", "decoded"):
                    continue
                payload[key] = value
        hits.append(
            {
                "kind": "nielsen",
                "subtype": state,
                "pts": float(span["pts"]),
                "pts_end": float(span["pts_end"]),
                "duration_s": max(0.0, float(span["pts_end"]) - float(span["pts"])),
                "payload_summary": (
                    f"Nielsen watermark appears {state} "
                    f"({method}; best-effort presence; not audit-grade decode)"
                ),
                "payload_json": json.dumps(payload, separators=(",", ":")),
            }
        )
    return hits


def _run_presence_command(
    path: str,
    duration_s: float,
    env: dict[str, str],
) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
    """Run the optional presence command.

    Returns (configured, samples, error).
    """
    tmpl = (env.get("NEXREC_NIELSEN_PRESENCE_CMD") or "").strip()
    if not tmpl:
        return False, None, None
    tmp = tempfile.NamedTemporaryFile(prefix="nexrec-nielsen-", suffix=".json", delete=False)
    out_path = tmp.name
    tmp.close()
    cmd = (
        tmpl.replace("{input}", shlex.quote(path))
        .replace("{output}", shlex.quote(out_path))
        .replace("{duration}", shlex.quote(f"{float(duration_s):.3f}"))
    )
    timeout = 60.0
    try:
        timeout = float(env.get("NEXREC_NIELSEN_TIMEOUT_S") or 60)
    except (TypeError, ValueError):
        timeout = 60.0
    try:
        proc = subprocess.run(
            shlex.split(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        _remove_file(out_path)
        return True, None, str(exc)[:240]
    if proc.returncode != 0 or not os.path.isfile(out_path):
        err = (proc.stderr or proc.stdout or "presence command failed").strip()
        _remove_file(out_path)
        return True, None, err[:240]
    try:
        with open(out_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return True, None, str(exc)[:240]
    finally:
        _remove_file(out_path)
    return True, normalize_presence_payload(data), None


def _remove_file(path: str) -> None:
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def detect_nielsen_presence(
    path: str,
    duration_s: float,
    *,
    env: dict[str, str] | None = None,
    detector: NielsenPresenceDetector | None = None,
    window_s: float | None = None,
) -> list[dict[str, Any]]:
    """Presence spans for one chunk. PTS is seconds from the chunk start.

    Does not decode Nielsen payload fields. Callers map PTS to NTP/wall-clock
    via the chunk timeline.
    """
    env = env or {}
    step = _window_s(env, window_s)
    if detector is not None:
        samples = [
            {"pts": pts, "pts_end": pts_end, "present": bool(detector.sample(path, pts, pts_end))}
            for pts, pts_end in presence_windows(duration_s, step)
        ]
        method = str(getattr(detector, "method", "custom") or "custom")
        return _hits(coalesce_presence(samples), method)

    configured, samples, err = _run_presence_command(path, duration_s, env)
    if configured and err is not None:
        stub = StubNielsenPresenceDetector()
        fallback = [
            {"pts": pts, "pts_end": pts_end, "present": stub.sample(path, pts, pts_end)}
            for pts, pts_end in presence_windows(duration_s, step)
        ]
        return _hits(coalesce_presence(fallback), "stub", {"command_error": err})
    if configured:
        if not samples:
            samples = [
                {"pts": pts, "pts_end": pts_end, "present": False}
                for pts, pts_end in presence_windows(duration_s, step)
            ]
        return _hits(coalesce_presence(samples), "command")

    stub = StubNielsenPresenceDetector()
    samples = [
        {"pts": pts, "pts_end": pts_end, "present": stub.sample(path, pts, pts_end)}
        for pts, pts_end in presence_windows(duration_s, step)
    ]
    return _hits(coalesce_presence(samples), stub.method)
