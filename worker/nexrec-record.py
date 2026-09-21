#!/usr/bin/env python3
"""FFmpeg segment recorder: IP (and designed DeckLink) → 5-minute MP4 chunks.

Watches the output directory and indexes closed files. The in-progress
segment is skipped (newest mtime while ffmpeg is alive).
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from nexrec_db import connect, fetchone, migrate, upsert_input  # noqa: E402
from nexrec_features import analyze_chunk  # noqa: E402
from nexrec_ffmpeg import record_argv  # noqa: E402
from nexrec_index import scan_dir  # noqa: E402
from nexrec_util import (  # noqa: E402
    chunk_dir,
    data_paths,
    env_int,
    iso_z,
    load_env_file,
    utcnow,
    valid_input_id,
)

STOP = False


def _stop(_signum=None, _frame=None) -> None:
    global STOP
    STOP = True


def input_from_env(env: dict[str, str], input_id: str) -> dict:
    # Prefer DB row; fall back to INPUT_* keys (systemd EnvironmentFile).
    return {
        "id": input_id,
        "name": env.get("INPUT_NAME") or input_id,
        "source_type": (env.get("SOURCE_TYPE") or "rtsp").lower(),
        "url": env.get("SOURCE_URL") or "",
        "decklink_device": env.get("DECKLINK_DEVICE") or "",
        "decklink_format": env.get("DECKLINK_FORMAT") or "",
        "enabled": 1 if env.get("ENABLED", "1") not in ("0", "false") else 0,
        "live_transcode": 1 if env.get("LIVE_TRANSCODE", "0") in ("1", "true") else 0,
        "copy_native": 1 if env.get("COPY_NATIVE", "1") not in ("0", "false") else 0,
        "upconvert_1080i": 1 if env.get("UPCONVERT_1080I", "0") in ("1", "true") else 0,
        "keep_interlace": 1 if env.get("KEEP_INTERLACE", "0") in ("1", "true") else 0,
        "video_bitrate": env.get("VIDEO_BITRATE") or None,
        "audio_bitrate": env.get("AUDIO_BITRATE") or None,
        "retention_days": int(env.get("RETENTION_DAYS") or 28),
        "preview_path": env.get("PREVIEW_PATH") or "in0",
        "preview_enabled": 1,
        "feat_scte": 1 if env.get("FEAT_SCTE", "0") in ("1", "true") else 0,
        "feat_av_anomaly": 1 if env.get("FEAT_AV_ANOMALY", "0") in ("1", "true") else 0,
        "feat_captions": 1 if env.get("FEAT_CAPTIONS", "0") in ("1", "true") else 0,
        "feat_transcribe": 1 if env.get("FEAT_TRANSCRIBE", "0") in ("1", "true") else 0,
        "feat_nielsen": 1 if env.get("FEAT_NIELSEN", "0") in ("1", "true") else 0,
        "feat_monitors": 1 if env.get("FEAT_MONITORS", "0") in ("1", "true") else 0,
        "thresh_freeze_s": float(env.get("THRESH_FREEZE_S") or 2),
        "thresh_black_s": float(env.get("THRESH_BLACK_S") or 2),
        "thresh_bars_s": float(env.get("THRESH_BARS_S") or 5),
        "transcribe_engine": env.get("TRANSCRIBE_ENGINE") or "",
        "created_at": iso_z(),
        "updated_at": iso_z(),
    }


def load_input(conn, env: dict[str, str], input_id: str) -> dict:
    row = fetchone(conn, "SELECT * FROM inputs WHERE id = ?", (input_id,))
    if row:
        # Env file overrides (systemd) win when set.
        if env.get("SOURCE_TYPE"):
            row["source_type"] = env["SOURCE_TYPE"].lower()
        if env.get("SOURCE_URL") is not None and env.get("SOURCE_URL") != "":
            row["url"] = env["SOURCE_URL"]
        return row
    rec = input_from_env(env, input_id)
    upsert_input(conn, rec)
    return rec


def newest_mp4(root: str) -> str | None:
    newest = None
    newest_m = -1.0
    if not os.path.isdir(root):
        return None
    for dirpath, _d, files in os.walk(root):
        for name in files:
            if not name.endswith(".mp4"):
                continue
            path = os.path.join(dirpath, name)
            try:
                m = os.path.getmtime(path)
            except OSError:
                continue
            if m > newest_m:
                newest_m = m
                newest = os.path.basename(path)
    return newest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="NexCLIP Recorder segment ingest")
    p.add_argument("--env", default="", help="path to nexrec.env")
    p.add_argument("--input-env", default="", help="path to inputs/<id>.env")
    p.add_argument("--input-id", required=True)
    p.add_argument("--once-seconds", type=float, default=0, help="exit after N seconds (demo/tests)")
    p.add_argument("--segment-seconds", type=int, default=0)
    args = p.parse_args(argv)

    if not valid_input_id(args.input_id):
        print("invalid --input-id", file=sys.stderr)
        return 2

    env = load_env_file(args.env) if args.env else dict(os.environ)
    if args.input_env:
        env = load_env_file(args.input_env, env)

    paths = data_paths(env)
    os.makedirs(paths["storage"], exist_ok=True)
    conn = connect(paths["db"])
    migrate(conn)
    source = load_input(conn, env, args.input_id)
    if not int(source.get("enabled") or 0):
        print(f"input {args.input_id} disabled", file=sys.stderr)
        return 0

    seg = args.segment_seconds or env_int(env, "NEXREC_SEGMENT_SECONDS", 300)
    today = utcnow()
    out_dir = chunk_dir(paths["storage"], args.input_id, "native", today)
    os.makedirs(out_dir, exist_ok=True)
    out_pattern = os.path.join(
        paths["storage"],
        "inputs",
        args.input_id,
        "native",
        "%Y",
        "%m",
        "%d",
        f"{args.input_id}_%Y%m%dT%H%M%SZ.mp4",
    )
    # FFmpeg strftime does not mkdir nested %Y/%m/%d itself on all builds —
    # pre-create today; a long-running process crossing midnight needs the watcher.
    os.makedirs(out_dir, exist_ok=True)

    cmd = record_argv(
        source,
        out_pattern,
        env=env,
        ffmpeg=paths["ffmpeg"],
        segment_seconds=seg,
    )
    print("exec:", " ".join(cmd), flush=True)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    proc = subprocess.Popen(cmd)
    t0 = time.time()
    native_root = os.path.join(paths["storage"], "inputs", args.input_id, "native")
    indexed: set[str] = set()

    try:
        while not STOP:
            if args.once_seconds and (time.time() - t0) >= args.once_seconds:
                break
            rc = proc.poll()
            # Pre-create tomorrow's UTC dir near midnight so strftime can open files.
            os.makedirs(chunk_dir(paths["storage"], args.input_id, "native", utcnow()), exist_ok=True)
            skip = newest_mp4(native_root) if proc.poll() is None else None
            for rec in scan_dir(
                conn,
                native_root,
                args.input_id,
                kind="native",
                ffprobe=paths["ffprobe"],
                skip_basename=skip,
            ):
                if rec["path"] not in indexed:
                    indexed.add(rec["path"])
                    print(f"indexed {rec['path']} duration={rec.get('duration_s')}", flush=True)
                    try:
                        st = analyze_chunk(
                            conn,
                            env,
                            source,
                            rec,
                            ffmpeg=paths["ffmpeg"],
                            ffprobe=paths["ffprobe"],
                            storage=paths["storage"],
                        )
                        if st.get("events") or st.get("captions"):
                            print(f"analyze {rec['path']} {st}", flush=True)
                    except Exception as exc:  # noqa: BLE001 — never fail ingest
                        print(f"analyze skip: {exc}", file=sys.stderr, flush=True)
            if rc is not None:
                break
            time.sleep(1.0)
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        # Final index including last file.
        scan_dir(conn, native_root, args.input_id, kind="native", ffprobe=paths["ffprobe"])
    return 0 if proc.returncode in (0, None, 255, -2, -15) else (proc.returncode or 1)


if __name__ == "__main__":
    sys.exit(main())
