#!/usr/bin/env python3
"""Per-input intelligence helpers: parsers, persistence, sidecar analyze.

Record/export FFmpeg argv is unchanged — these run on closed chunks or the
export window. Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import timedelta
from typing import Any, Iterable

from nexrec_db import (
    fetchone,
    insert_caption,
    insert_event,
    insert_loudness,
)
from nexrec_ffmpeg import (
    detect_argv,
    ebur128_concat_argv,
    extract_srt_argv,
    extract_subcc_argv,
    scte_probe_argv,
)
from nexrec_nielsen import detect_nielsen_presence
from nexrec_util import iso_z, new_id, parse_iso, wallclock_timecode

BLACK_RE = re.compile(
    r"black_start:\s*([0-9.]+)\s+black_end:\s*([0-9.]+)\s+black_duration:\s*([0-9.]+)"
)
FREEZE_START_RE = re.compile(r"freeze_start:\s*([0-9.]+)")
FREEZE_END_RE = re.compile(r"freeze_end:\s*([0-9.]+)")
EBUR_FRAME_RE = re.compile(
    r"t:\s*([0-9.]+).*?\bM:\s*([-\d.]+).*?\bS:\s*([-\d.]+).*?\bI:\s*([-\d.]+)"
)
EBUR_I_RE = re.compile(r"Integrated loudness:\s*([-\d.]+)\s*LUFS", re.I)
EBUR_LRA_RE = re.compile(r"Loudness range:\s*([-\d.]+)\s*LU", re.I)
EBUR_TP_RE = re.compile(r"True peak:\s*([-\d.]+)\s*dBTP", re.I)
SRT_BLOCK_RE = re.compile(
    r"(\d+)\s+(\d{2}:\d{2}:\d{2}[,.]\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}[,.]\d{3})\s+([\s\S]*?)(?=\n\n|\n\d+\s+\d{2}:|\Z)"
)


def flag_on(source: dict[str, Any], key: str) -> bool:
    try:
        return int(source.get(key) or 0) != 0
    except (TypeError, ValueError):
        return False


def append_jsonl(path: str, rows: Iterable[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")


def pts_to_wallclock(chunk_start: str, pts: float) -> str:
    return iso_z(parse_iso(chunk_start) + timedelta(seconds=float(pts)))


def tc_for(iso: str, fps: float = 30.0) -> str:
    return wallclock_timecode(parse_iso(iso), fps=fps)


def parse_blackdetect(log: str, min_s: float = 0.0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in BLACK_RE.finditer(log or ""):
        dur = float(m.group(3))
        if dur + 1e-9 < float(min_s):
            continue
        out.append(
            {
                "kind": "black",
                "subtype": "blackdetect",
                "pts": float(m.group(1)),
                "pts_end": float(m.group(2)),
                "duration_s": dur,
                "payload_summary": f"black {dur:.2f}s",
            }
        )
    return out


def parse_freezedetect(log: str, min_s: float = 0.0) -> list[dict[str, Any]]:
    starts = [float(m.group(1)) for m in FREEZE_START_RE.finditer(log or "")]
    ends = [float(m.group(1)) for m in FREEZE_END_RE.finditer(log or "")]
    out: list[dict[str, Any]] = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else None
        dur = (end - start) if end is not None else None
        if dur is not None and dur + 1e-9 < float(min_s):
            continue
        if dur is None and float(min_s) > 0:
            # Open-ended freeze still running at EOF — keep if we cannot prove it is short.
            pass
        out.append(
            {
                "kind": "freeze",
                "subtype": "freezedetect",
                "pts": start,
                "pts_end": end,
                "duration_s": dur,
                "payload_summary": f"freeze {dur:.2f}s" if dur is not None else "freeze (open)",
            }
        )
    return out


def parse_ebur128(log: str) -> dict[str, Any]:
    frames: list[dict[str, float]] = []
    for m in EBUR_FRAME_RE.finditer(log or ""):
        frames.append(
            {
                "t": float(m.group(1)),
                "momentary": float(m.group(2)),
                "short_term": float(m.group(3)),
                "integrated": float(m.group(4)),
            }
        )
    summary: dict[str, Any] = {"frames": frames}
    mi = EBUR_I_RE.search(log or "")
    ml = EBUR_LRA_RE.search(log or "")
    mt = EBUR_TP_RE.search(log or "")
    if mi:
        summary["integrated"] = float(mi.group(1))
    elif frames:
        summary["integrated"] = frames[-1]["integrated"]
    if ml:
        summary["lra"] = float(ml.group(1))
    if mt:
        summary["true_peak"] = float(mt.group(1))
    return summary


def parse_scte35_probe(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Pull SCTE-35-ish data packets from ffprobe -show_packets JSON."""
    out: list[dict[str, Any]] = []
    if not data:
        return out
    streams = {int(s.get("index", -1)): s for s in (data.get("streams") or []) if isinstance(s, dict)}
    for pkt in data.get("packets") or []:
        if not isinstance(pkt, dict):
            continue
        idx = pkt.get("stream_index")
        st = streams.get(int(idx)) if idx is not None and str(idx).isdigit() else {}
        codec = str((st or {}).get("codec_name") or pkt.get("codec_name") or "").lower()
        codec_type = str((st or {}).get("codec_type") or "").lower()
        tags = (st or {}).get("tags") if isinstance((st or {}).get("tags"), dict) else {}
        tagblob = " ".join(str(v) for v in tags.values()).lower()
        blob = f"{codec} {codec_type} {tagblob} {pkt.get('codec_type') or ''}".lower()
        if "scte" not in blob and codec not in ("scte_35", "scte35") and "scte_35" not in blob:
            if codec_type != "data":
                continue
            # MPEG-TS data PID without a named codec — keep a short summary, mark unverified.
            kind = "scte35"
            subtype = "data_pid"
        else:
            kind = "scte35"
            subtype = codec or "scte_35"
        pts = pkt.get("pts_time") or pkt.get("dts_time") or pkt.get("pts")
        try:
            pts_f = float(pts) if pts is not None else 0.0
        except (TypeError, ValueError):
            pts_f = 0.0
        summary = f"{subtype} stream={idx} size={pkt.get('size')}"
        out.append(
            {
                "kind": kind,
                "subtype": subtype,
                "pts": pts_f,
                "pts_end": None,
                "duration_s": None,
                "payload_summary": summary[:240],
                "payload_json": json.dumps(
                    {"stream_index": idx, "codec_name": codec, "size": pkt.get("size")},
                    separators=(",", ":"),
                ),
            }
        )
    return out


def srt_ts_to_seconds(ts: str) -> float:
    ts = ts.replace(",", ".")
    hh, mm, rest = ts.split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(rest)


def parse_srt(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    body = (text or "").replace("\r\n", "\n").strip()
    if not body:
        return out
    # Ensure trailing blank so the last cue matches.
    if not body.endswith("\n\n"):
        body += "\n\n"
    for m in SRT_BLOCK_RE.finditer(body):
        cue = re.sub(r"<[^>]+>", "", m.group(4)).replace("\n", " ").strip()
        if not cue:
            continue
        start = srt_ts_to_seconds(m.group(2))
        end = srt_ts_to_seconds(m.group(3))
        out.append(
            {
                "pts": start,
                "pts_end": end,
                "duration_s": max(0.0, end - start),
                "text": cue,
            }
        )
    return out


def _event_row(
    source: dict[str, Any],
    chunk: dict[str, Any],
    hit: dict[str, Any],
    fps: float = 30.0,
) -> dict[str, Any]:
    t0 = chunk.get("start_at") or iso_z()
    t_start = pts_to_wallclock(t0, float(hit.get("pts") or 0.0))
    pts_end = hit.get("pts_end")
    t_end = pts_to_wallclock(t0, float(pts_end)) if pts_end is not None else None
    return {
        "id": new_id("evt"),
        "input_id": source.get("id") or chunk.get("input_id"),
        "chunk_id": chunk.get("id"),
        "kind": hit["kind"],
        "subtype": hit.get("subtype"),
        "t_start": t_start,
        "t_end": t_end,
        "pts": hit.get("pts"),
        "timecode": tc_for(t_start, fps=fps),
        "duration_s": hit.get("duration_s"),
        "payload_summary": (hit.get("payload_summary") or "")[:500],
        "payload_json": hit.get("payload_json"),
        "created_at": iso_z(),
    }


def persist_events(
    conn,
    source: dict[str, Any],
    chunk: dict[str, Any],
    hits: list[dict[str, Any]],
    jsonl_path: str | None,
    fps: float = 30.0,
) -> list[dict[str, Any]]:
    rows = [_event_row(source, chunk, h, fps=fps) for h in hits]
    for rec in rows:
        insert_event(conn, rec)
    if jsonl_path and rows:
        append_jsonl(jsonl_path, rows)
    return rows


def _run(cmd: list[str], timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def jsonl_for_chunk(storage: str, input_id: str, chunk_path: str) -> str:
    base = os.path.splitext(os.path.basename(chunk_path))[0]
    return os.path.join(storage, "inputs", input_id, "events", f"{base}.jsonl")


def _media_duration_s(chunk: dict[str, Any], env: dict[str, str] | None) -> float:
    """Seconds of this chunk on the recording timeline. Falls back to segment length."""
    raw = chunk.get("duration_s")
    try:
        if raw is not None and float(raw) > 0:
            return float(raw)
    except (TypeError, ValueError):
        pass
    start, end = chunk.get("start_at"), chunk.get("end_at")
    if start and end:
        try:
            delta = (parse_iso(str(end)) - parse_iso(str(start))).total_seconds()
            if delta > 0:
                return float(delta)
        except (TypeError, ValueError, OSError):
            pass
    try:
        seg = float((env or {}).get("NEXREC_SEGMENT_SECONDS") or 300)
    except (TypeError, ValueError):
        seg = 300.0
    return seg if seg > 0 else 300.0


def _once_kind(conn, input_id: str, kind: str, subtype: str) -> bool:
    row = fetchone(
        conn,
        "SELECT id FROM events WHERE input_id=? AND kind=? AND subtype=? LIMIT 1",
        (input_id, kind, subtype),
    )
    return row is None


def _chunk_has_kind(conn, chunk_id: str | None, kind: str) -> bool:
    if not chunk_id:
        return False
    row = fetchone(
        conn,
        "SELECT id FROM events WHERE chunk_id=? AND kind=? LIMIT 1",
        (chunk_id, kind),
    )
    return row is not None


def analyze_chunk(
    conn,
    env: dict[str, str],
    source: dict[str, Any],
    chunk: dict[str, Any],
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    storage: str = "",
) -> dict[str, Any]:
    """Best-effort sidecar analyze of a closed native chunk. Never raises to caller."""
    stats = {"events": 0, "captions": 0, "skipped": []}
    path = chunk.get("path") or ""
    iid = source.get("id") or chunk.get("input_id") or ""
    fps = float(chunk.get("fps") or 30.0) or 30.0
    jsonl = jsonl_for_chunk(storage, iid, path) if storage else ""
    freeze_s = float(source.get("thresh_freeze_s") or 2.0)
    black_s = float(source.get("thresh_black_s") or 2.0)
    bars_s = float(source.get("thresh_bars_s") or 5.0)

    any_on = any(
        flag_on(source, k)
        for k in (
            "feat_scte",
            "feat_av_anomaly",
            "feat_captions",
            "feat_transcribe",
            "feat_nielsen",
        )
    )
    if not any_on:
        stats["skipped"].append("no_flags")
        return stats
    if not path or not os.path.isfile(path):
        stats["skipped"].append("no_file")
        return stats

    try:
        if flag_on(source, "feat_av_anomaly"):
            proc = _run(detect_argv(path, freeze_s=freeze_s, black_s=black_s, ffmpeg=ffmpeg))
            log = (proc.stderr or "") + "\n" + (proc.stdout or "")
            hits = parse_blackdetect(log, min_s=black_s) + parse_freezedetect(log, min_s=freeze_s)
            # SMPTE bars: no FFmpeg filter. Record the limitation once; do not invent hits.
            if _once_kind(conn, iid, "bars", "no_filter"):
                hits.append(
                    {
                        "kind": "bars",
                        "subtype": "no_filter",
                        "pts": 0.0,
                        "pts_end": None,
                        "duration_s": None,
                        "payload_summary": (
                            f"SMPTE color-bar detector not in FFmpeg; "
                            f"threshold configured at {bars_s:g}s. NEXT: histogram/template match."
                        ),
                    }
                )
            persist_events(conn, source, chunk, hits, jsonl, fps=fps)
            stats["events"] += len(hits)

        if flag_on(source, "feat_scte"):
            scte_hits: list[dict[str, Any]] = []
            try:
                proc = _run(scte_probe_argv(path, ffprobe=ffprobe), timeout=60.0)
                data = json.loads(proc.stdout or "{}") if proc.returncode == 0 else {}
                scte_hits = parse_scte35_probe(data)
            except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError):
                scte_hits = []
            if not scte_hits and _once_kind(conn, iid, "scte35", "no_packets"):
                scte_hits.append(
                    {
                        "kind": "scte35",
                        "subtype": "no_packets",
                        "pts": 0.0,
                        "payload_summary": (
                            "No SCTE-35 packets in this MP4. MPEG-TS copy or a live TS tap "
                            "is required; MP4 remux typically drops SCTE-35."
                        ),
                    }
                )
            if (source.get("source_type") or "").lower() == "decklink" and _once_kind(
                conn, iid, "scte104", "vanc_next"
            ):
                scte_hits.append(
                    {
                        "kind": "scte104",
                        "subtype": "vanc_next",
                        "pts": 0.0,
                        "payload_summary": (
                            "SCTE-104 lives in SDI VANC. FFmpeg decklink path does not decode it "
                            "in v0; NEXT: Blackmagic SDK VANC reader."
                        ),
                    }
                )
            if _once_kind(conn, iid, "scte224", "esam_http"):
                scte_hits.append(
                    {
                        "kind": "scte224",
                        "subtype": "esam_http",
                        "pts": 0.0,
                        "payload_summary": (
                            "SCTE-224 is out-of-band ESAM/HTTP, not in the media. "
                            "POST /api/recorder?action=scte224_ingest to store messages."
                        ),
                    }
                )
            persist_events(conn, source, chunk, scte_hits, jsonl, fps=fps)
            stats["events"] += len(scte_hits)

        if flag_on(source, "feat_nielsen"):
            # Presence only. No Nielsen Decoder SDK, SID, code time, or layer.
            if not _chunk_has_kind(conn, chunk.get("id"), "nielsen"):
                hits = detect_nielsen_presence(
                    path,
                    _media_duration_s(chunk, env),
                    env=env,
                )
                persist_events(conn, source, chunk, hits, jsonl, fps=fps)
                stats["events"] += len(hits)

        if flag_on(source, "feat_captions"):
            n = _extract_captions(conn, env, source, chunk, ffmpeg, jsonl, fps)
            stats["captions"] += n

        if flag_on(source, "feat_transcribe"):
            n = _transcribe_stub(conn, env, source, chunk, jsonl, fps)
            stats["captions"] += n
    except subprocess.TimeoutExpired:
        stats["skipped"].append("timeout")
    except OSError as exc:
        stats["skipped"].append(str(exc))
    return stats


def _extract_captions(
    conn,
    env: dict[str, str],
    source: dict[str, Any],
    chunk: dict[str, Any],
    ffmpeg: str,
    jsonl: str,
    fps: float,
) -> int:
    path = chunk["path"]
    dest = path + ".cc.srt"
    cues: list[dict[str, Any]] = []
    presence = False
    for argv in (extract_srt_argv(path, dest, ffmpeg=ffmpeg), extract_subcc_argv(path, dest, ffmpeg=ffmpeg)):
        proc = _run(argv, timeout=90.0)
        if proc.returncode == 0 and os.path.isfile(dest) and os.path.getsize(dest) > 0:
            try:
                with open(dest, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                text = ""
            cues = parse_srt(text)
            if cues:
                presence = True
                break
    try:
        if os.path.isfile(dest):
            os.remove(dest)
    except OSError:
        pass
    if presence:
        persist_events(
            conn,
            source,
            chunk,
            [
                {
                    "kind": "cc_presence",
                    "subtype": "608_708",
                    "pts": 0.0,
                    "payload_summary": f"{len(cues)} caption cues in chunk",
                }
            ],
            jsonl,
            fps=fps,
        )
        t0 = chunk.get("start_at") or iso_z()
        for cue in cues:
            t_start = pts_to_wallclock(t0, float(cue["pts"]))
            t_end = pts_to_wallclock(t0, float(cue.get("pts_end") or cue["pts"]))
            rec = {
                "id": new_id("cap"),
                "input_id": source.get("id") or chunk.get("input_id"),
                "chunk_id": chunk.get("id"),
                "kind": "caption",
                "service": "608/708",
                "speaker": "",
                "t_start": t_start,
                "t_end": t_end,
                "pts": cue["pts"],
                "timecode": tc_for(t_start, fps=fps),
                "text": cue["text"],
                "created_at": iso_z(),
            }
            insert_caption(conn, rec)
        return len(cues)
    if _once_kind(conn, source.get("id") or "", "cc_presence", "none"):
        persist_events(
            conn,
            source,
            chunk,
            [
                {
                    "kind": "cc_presence",
                    "subtype": "none",
                    "pts": 0.0,
                    "payload_summary": "No 608/708 subtitle stream extracted from this file",
                }
            ],
            jsonl,
            fps=fps,
        )
    return 0


def _transcribe_stub(
    conn,
    env: dict[str, str],
    source: dict[str, Any],
    chunk: dict[str, Any],
    jsonl: str,
    fps: float,
) -> int:
    engine = (
        (source.get("transcribe_engine") or "").strip()
        or (env.get("NEXREC_TRANSCRIBE_ENGINE") or "none").strip()
        or "none"
    ).lower()
    cmd_tmpl = (env.get("NEXREC_TRANSCRIBE_CMD") or "").strip()
    if engine in ("", "none", "off") or not cmd_tmpl:
        if _once_kind(conn, source.get("id") or "", "transcribe", "engine_unset"):
            persist_events(
                conn,
                source,
                chunk,
                [
                    {
                        "kind": "transcribe",
                        "subtype": "engine_unset",
                        "pts": 0.0,
                        "payload_summary": (
                            "Transcription off until NEXREC_TRANSCRIBE_ENGINE + "
                            "NEXREC_TRANSCRIBE_CMD are set (whisper.cpp or faster-whisper). "
                            "No API keys in repo. GPU strongly recommended for 8×1080."
                        ),
                        "payload_json": json.dumps({"engine": engine}, separators=(",", ":")),
                    }
                ],
                jsonl,
                fps=fps,
            )
        return 0
    # Pluggable: command must write JSON [{t_start, t_end, speaker, text}, ...] to {output}.
    outp = chunk["path"] + ".transcript.json"
    cmd = cmd_tmpl.replace("{input}", chunk["path"]).replace("{output}", outp)
    proc = _run(cmd.split(), timeout=float(env.get("NEXREC_TRANSCRIBE_TIMEOUT_S") or 300))
    if proc.returncode != 0 or not os.path.isfile(outp):
        persist_events(
            conn,
            source,
            chunk,
            [
                {
                    "kind": "transcribe",
                    "subtype": "engine_error",
                    "pts": 0.0,
                    "payload_summary": (proc.stderr or "transcribe failed")[:400],
                }
            ],
            jsonl,
            fps=fps,
        )
        return 0
    try:
        with open(outp, "r", encoding="utf-8") as fh:
            cues = json.load(fh)
    except (OSError, json.JSONDecodeError):
        cues = []
    if not isinstance(cues, list):
        cues = []
    t0 = chunk.get("start_at") or iso_z()
    n = 0
    for cue in cues:
        if not isinstance(cue, dict) or not cue.get("text"):
            continue
        pts = float(cue.get("t_start") or cue.get("pts") or 0.0)
        pts_end = float(cue.get("t_end") or cue.get("pts_end") or pts)
        t_start = pts_to_wallclock(t0, pts)
        rec = {
            "id": new_id("cap"),
            "input_id": source.get("id") or chunk.get("input_id"),
            "chunk_id": chunk.get("id"),
            "kind": "transcript",
            "service": engine,
            "speaker": str(cue.get("speaker") or ""),
            "t_start": t_start,
            "t_end": pts_to_wallclock(t0, pts_end),
            "pts": pts,
            "timecode": tc_for(t_start, fps=fps),
            "text": str(cue["text"]),
            "created_at": iso_z(),
        }
        insert_caption(conn, rec)
        n += 1
    return n


def store_ebur128(
    conn,
    input_id: str,
    export_id: str | None,
    window_start: str,
    summary: dict[str, Any],
    source: str = "export_window",
) -> int:
    frames = summary.get("frames") or []
    n = 0
    lra = summary.get("lra")
    peak = summary.get("true_peak")
    for fr in frames:
        t_at = pts_to_wallclock(window_start, float(fr["t"]))
        insert_loudness(
            conn,
            {
                "id": new_id("lufs"),
                "input_id": input_id,
                "export_id": export_id,
                "t_at": t_at,
                "lkfs": float(fr.get("integrated") or fr.get("momentary") or 0.0),
                "momentary": fr.get("momentary"),
                "short_term": fr.get("short_term"),
                "true_peak": peak,
                "lra": lra,
                "source": source,
                "created_at": iso_z(),
            },
        )
        n += 1
    if not frames and summary.get("integrated") is not None:
        insert_loudness(
            conn,
            {
                "id": new_id("lufs"),
                "input_id": input_id,
                "export_id": export_id,
                "t_at": window_start,
                "lkfs": float(summary["integrated"]),
                "momentary": None,
                "short_term": None,
                "true_peak": peak,
                "lra": lra,
                "source": source,
                "created_at": iso_z(),
            },
        )
        n += 1
    return n


def measure_concat_loudness(
    conn,
    env: dict[str, str],
    concat_path: str,
    ss: float,
    duration: float,
    input_id: str,
    export_id: str | None,
    window_start: str,
    ffmpeg: str = "ffmpeg",
) -> dict[str, Any]:
    proc = _run(
        ebur128_concat_argv(concat_path, ss, duration, ffmpeg=ffmpeg),
        timeout=float(env.get("NEXREC_LOUDNESS_TIMEOUT_S") or 180),
    )
    log = (proc.stderr or "") + "\n" + (proc.stdout or "")
    summary = parse_ebur128(log)
    summary["n_samples"] = store_ebur128(conn, input_id, export_id, window_start, summary)
    summary["ok"] = proc.returncode == 0 or bool(summary.get("frames") or summary.get("integrated") is not None)
    if not summary["ok"]:
        summary["error"] = (proc.stderr or "ebur128 failed")[-400:]
    # Drop bulky frames from the job result; they live in loudness_samples.
    frames = summary.pop("frames", [])
    summary["n_frames"] = len(frames)
    return summary
