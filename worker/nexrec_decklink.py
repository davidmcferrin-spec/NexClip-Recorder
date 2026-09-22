#!/usr/bin/env python3
"""DeckLink device lists and status-helper JSON. No hardware required to parse."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

DECKLINK_UNKNOWN = "unknown — needs DeckLink tools"

_DEVICE_LINE = re.compile(r"""['"]([^'"]+)['"]""")
_FORMAT_CODE = re.compile(r"'([A-Za-z0-9]{2,8})'")


def parse_list_devices(text: str) -> list[dict[str, Any]]:
    """Parse `ffmpeg -f decklink -list_devices 1` stderr. Index follows list order."""
    devices: list[dict[str, Any]] = []
    header = False
    for line in text.splitlines():
        if "DeckLink" in line and "devices" in line.lower():
            header = True
            continue
        if not header:
            continue
        if "Immediate exit" in line or line.strip().startswith("dummy:"):
            break
        m = _DEVICE_LINE.search(line)
        if not m:
            continue
        name = m.group(1).strip()
        if not name or name.lower() == "dummy":
            continue
        devices.append({"index": len(devices), "name": name})
    return devices


def parse_list_formats(text: str) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        if "Supported formats" in line:
            continue
        m = _FORMAT_CODE.search(line)
        if not m:
            continue
        code = m.group(1)
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def ffmpeg_has_decklink(text: str) -> bool:
    low = text.lower()
    if "unknown input format" in low and "decklink" in low:
        return False
    if "unrecognized option" in low and "list_devices" in low:
        return False
    return "decklink" in low


def resolve_decklink_spec(spec: str, devices: list[dict[str, Any]]) -> str | None:
    """Map a sub-device index or display name to the FFmpeg device name."""
    raw = (spec or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        idx = int(raw)
        for dev in devices:
            if int(dev.get("index") or 0) == idx:
                name = str(dev.get("name") or "").strip()
                return name or None
        return None
    for dev in devices:
        if str(dev.get("name") or "") == raw:
            return raw
    return raw


def list_ffmpeg_devices(ffmpeg: str, timeout: float = 8.0) -> tuple[list[dict[str, Any]], str]:
    """Run ffmpeg -list_devices. Returns (devices, combined log). Never uses a shell."""
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-f", "decklink", "-list_devices", "1", "-i", "dummy"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], str(exc)
    blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if not ffmpeg_has_decklink(blob):
        return [], blob
    return parse_list_devices(blob), blob


def parse_status_json(text: str, device: str) -> dict[str, Any]:
    """Map a NexVUE-shaped status dump onto the Services signal row.

    Busy sub-devices still carry input_locked / input_mode from IDeckLinkStatus.
    """
    unknown = {
        "signal": "unknown",
        "sdi_lock": None,
        "format": "",
        "detail": DECKLINK_UNKNOWN,
        "probe": "unavailable",
        "busy": None,
        "reference_locked": None,
        "reference_mode": "",
        "name": "",
        "index": None,
    }
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        unknown["detail"] = "status helper did not return JSON"
        return unknown
    if not isinstance(data, dict):
        unknown["detail"] = "status helper did not return JSON"
        return unknown
    err = data.get("error")
    devices = data.get("devices")
    if not isinstance(devices, list):
        devices = []
    if err and not devices:
        unknown["detail"] = "DeckLink drivers are not available" if err == "no_decklink_api" else str(err)[:200]
        unknown["probe"] = "unavailable"
        return unknown
    match = _match_device(device, devices)
    if match is None:
        return {
            "signal": "unknown",
            "sdi_lock": None,
            "format": "",
            "detail": "status helper did not report this sub-device",
            "probe": "tool",
            "busy": None,
            "reference_locked": None,
            "reference_mode": "",
            "name": "",
            "index": None,
        }
    locked = bool(match.get("input_locked"))
    mode = str(match.get("input_mode") or "")
    if mode.lower() == "unknown":
        mode = ""
    busy = bool(match.get("busy"))
    ref_locked = bool(match.get("reference_locked"))
    ref_mode = str(match.get("reference_mode") or "")
    if ref_mode.lower() == "unknown":
        ref_mode = ""
    detail_bits: list[str] = []
    if busy:
        detail_bits.append("sub-device busy (record process holds it); lock read from DeckLink status")
    if ref_locked and ref_mode:
        detail_bits.append(f"reference {ref_mode}")
    return {
        "signal": "present" if locked else "no_signal",
        "sdi_lock": 1 if locked else 0,
        "format": mode if locked else "",
        "detail": "; ".join(detail_bits),
        "probe": "tool",
        "busy": 1 if busy else 0,
        "reference_locked": 1 if ref_locked else 0,
        "reference_mode": ref_mode,
        "name": str(match.get("name") or ""),
        "index": int(match.get("index") or 0),
    }


def _match_device(spec: str, devices: list[Any]) -> dict[str, Any] | None:
    raw = (spec or "").strip()
    rows = [d for d in devices if isinstance(d, dict)]
    if raw.isdigit():
        idx = int(raw)
        for dev in rows:
            try:
                if int(dev.get("index")) == idx:
                    return dev
            except (TypeError, ValueError):
                continue
    for dev in rows:
        if str(dev.get("name") or "") == raw:
            return dev
    low = raw.lower()
    for dev in rows:
        if str(dev.get("name") or "").lower() == low:
            return dev
    return None


def default_status_bins() -> list[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    return [
        "/usr/local/bin/nexrec-decklink-status",
        "/usr/bin/nexrec-decklink-status",
        os.path.join(root, "tools", "decklink-status", "nexrec-decklink-status"),
    ]


def safe_status_bin(path: str) -> str | None:
    if not path or not path.startswith("/") or not os.path.isfile(path):
        return None
    if any(c in path for c in " \t;|&$`\n\r"):
        return None
    if not os.access(path, os.X_OK):
        return None
    return path


def resolve_status_bin(env: dict[str, str] | None = None) -> str | None:
    env = env or {}
    explicit = (env.get("NEXREC_DECKLINK_STATUS_BIN") or "").strip()
    if explicit:
        return safe_status_bin(explicit)
    for cand in default_status_bins():
        got = safe_status_bin(cand)
        if got:
            return got
    return None
