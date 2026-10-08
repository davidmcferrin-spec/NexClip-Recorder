#!/usr/bin/env python3
"""FFmpeg segment recorder: IP and DeckLink → 5-minute MP4 chunks.

Watches the output directory and indexes closed files. The in-progress
segment is skipped (newest filename timestamp while ffmpeg is alive).
The chunk row is written before stills or analyze, so a slow filmstrip
cannot leave the closed file unrecorded.
"""

from __future__ import annotations

import argparse
import os
import queue
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from nexrec_db import connect, fetchone, migrate, overlay_app_settings, upsert_input  # noqa: E402
from nexrec_decklink import (  # noqa: E402
    apply_decklink_probe,
    list_ffmpeg_devices,
    resolve_decklink_spec,
    resolve_status_bin,
)
from nexrec_features import analyze_chunk  # noqa: E402
from nexrec_ffmpeg import is_live_only, pin_video_encoder, preview_publish_url, record_argv  # noqa: E402
from nexrec_heartbeat import probe_decklink, write_heartbeat  # noqa: E402
from nexrec_index import backfill_thumbs, open_segment_basename, scan_dir, write_chunk_thumb  # noqa: E402
from nexrec_util import (  # noqa: E402
    chunk_dir,
    data_paths,
    env_int,
    iso_z,
    load_env_file,
    pin_process_utc,
    utcnow,
    valid_input_id,
)

STOP = False


def _stop(_signum=None, _frame=None) -> None:
    global STOP
    STOP = True


def idle_until_stop(message: str) -> int:
    """Stay up until SIGTERM. Restart=always would flap if this process exited."""
    print(message, flush=True)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while not STOP:
        time.sleep(1.0)
    print("stopped", flush=True)
    return 0


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
        "video_bitrate": env.get("VIDEO_BITRATE") or None,
        "audio_bitrate": env.get("AUDIO_BITRATE") or None,
        "retention_days": int(env.get("RETENTION_DAYS") or 28),
        "preview_path": env.get("PREVIEW_PATH") or "in0",
        "preview_enabled": 0 if env.get("PREVIEW_ENABLED", "1") in ("0", "false", "no") else 1,
        "live_only": 1 if env.get("LIVE_ONLY", "0") in ("1", "true", "yes") else 0,
        "keep_interlace": None if "KEEP_INTERLACE" not in env else (
            1 if env.get("KEEP_INTERLACE", "0") in ("1", "true", "yes") else 0
        ),
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


def wait_for_decklink_lock(source: dict, env: dict[str, str]) -> dict:
    """Block until the sub-device reports a lock, then store signal_mode.

    FFmpeg's DeckLink demuxer errors out when autodetect runs with no signal.
    The status helper reads lock without opening the input.
    """
    device = str(source.get("decklink_device") or "").strip()
    if resolve_status_bin(env) is None:
        print("decklink status helper unavailable; encode follows the format code only", flush=True)
        return source
    while not STOP:
        probed = probe_decklink(device, env, now=time.time(), fresh=True)
        updated, action = apply_decklink_probe(source, probed)
        if action == "ready":
            mode = str(updated.get("signal_mode") or "").strip()
            if mode:
                print(f"decklink locked {mode}", flush=True)
            elif str(probed.get("probe") or "") == "unavailable":
                print("decklink status probe unavailable; encode follows the format code only", flush=True)
            else:
                print("decklink locked", flush=True)
            return updated
        print("decklink no signal; waiting", flush=True)
        for _ in range(8):
            if STOP:
                return source
            time.sleep(0.25)
    return source


def newest_mp4(root: str) -> str | None:
    return open_segment_basename(root)


def _drain_side(
    stop: threading.Event,
    jobs: queue.Queue,
    env: dict[str, str],
    source: dict,
    ffmpeg: str,
    ffprobe: str,
    storage: str,
    input_id: str,
) -> None:
    """Stills and analyze off the index loop. A slow still must not delay the next chunk row."""
    try:
        side = connect(env)
    except Exception as exc:  # noqa: BLE001
        print(f"side index connect: {exc}", file=sys.stderr, flush=True)
        return
    while not stop.is_set() or not jobs.empty():
        try:
            job = jobs.get(timeout=0.5)
        except queue.Empty:
            if stop.is_set():
                break
            continue
        if job is None:
            break
        kind, rec = job
        try:
            if kind == "analyze":
                st = analyze_chunk(
                    side,
                    env,
                    source,
                    rec,
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                    storage=storage,
                )
                if st.get("events") or st.get("captions"):
                    print(f"analyze {rec['path']} {st}", flush=True)
            elif kind == "thumb":
                write_chunk_thumb(rec["path"], ffmpeg, duration_s=rec.get("duration_s"))
            elif kind == "backfill":
                backfill_thumbs(side, ffmpeg, limit=1, input_id=input_id)
        except Exception as exc:  # noqa: BLE001 — sidecar work must not stop ingest
            print(f"{kind} skip: {exc}", file=sys.stderr, flush=True)
        finally:
            jobs.task_done()


def _note_indexed(jobs: queue.Queue, indexed: set[str], recs: list[dict], source: dict) -> None:
    for rec in recs:
        path = str(rec.get("path") or "")
        if not path:
            continue
        first = path not in indexed
        indexed.add(path)
        print(f"indexed {path} duration={rec.get('duration_s')}", flush=True)
        jobs.put(("thumb", rec))
        if first and not is_live_only(source):
            jobs.put(("analyze", rec))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="NexCLIP Recorder segment ingest")
    p.add_argument("--env", default="", help="path to nexrec.env")
    p.add_argument("--input-env", default="", help="path to inputs/<id>.env")
    p.add_argument("--input-id", required=True)
    p.add_argument("--once-seconds", type=float, default=0, help="exit after N seconds (demo/tests)")
    p.add_argument("--segment-seconds", type=int, default=0)
    args = p.parse_args(argv)
    pin_process_utc()

    if not valid_input_id(args.input_id):
        print("invalid --input-id", file=sys.stderr)
        return 2

    env = load_env_file(args.env) if args.env else dict(os.environ)
    if args.input_env:
        env = load_env_file(args.input_env, env)

    paths = data_paths(env)
    conn = connect(env)
    migrate(conn)
    env = overlay_app_settings(conn, env)
    paths = data_paths(env)
    os.makedirs(paths["storage"], exist_ok=True)
    source = load_input(conn, env, args.input_id)
    if not int(source.get("enabled") or 0):
        print(f"input {args.input_id} disabled", file=sys.stderr)
        return 0
    if is_live_only(source) and (source.get("source_type") or "").lower() != "decklink":
        return idle_until_stop(
            f"live only: {args.input_id} is not recorded; nexrec-preview serves the Live page"
        )

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

    if (source.get("source_type") or "").lower() == "decklink":
        spec = str(source.get("decklink_device") or "").strip()
        if not spec:
            print("decklink input has no device name or index", file=sys.stderr)
            return 1
        if spec.isdigit():
            devices, log = list_ffmpeg_devices(paths["ffmpeg"])
            name = resolve_decklink_spec(spec, devices)
            if not name:
                print(f"decklink index {spec} was not in ffmpeg -list_devices", file=sys.stderr)
                if log.strip():
                    print(log.strip().splitlines()[-1], file=sys.stderr)
                return 1
            source = dict(source)
            source["decklink_device"] = name
            print(f"decklink index {spec} → {name}", flush=True)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    if (source.get("source_type") or "").lower() == "decklink":
        source = wait_for_decklink_lock(source, env)
        if STOP:
            print("stopped before ingest", flush=True)
            return 0

    preview_rtsp = None
    if (source.get("source_type") or "").lower() == "decklink":
        station_on = str(env.get("NEXREC_PREVIEW_ENABLED", "1")).strip().lower() not in ("0", "false", "no")
        per = source.get("preview_enabled")
        per_on = True if per is None or per == "" else str(per).strip().lower() not in ("0", "false", "no")
        if station_on and per_on:
            preview_rtsp = preview_publish_url(str(source.get("preview_path") or "in0"), env)
            if is_live_only(source):
                print(f"decklink live only preview {preview_rtsp}", flush=True)
            else:
                print(f"decklink preview tee {preview_rtsp}", flush=True)
        elif is_live_only(source):
            return idle_until_stop(
                f"live only: {args.input_id} has preview off, so the DeckLink card stays closed"
            )

    env, encoder = pin_video_encoder(env, paths["ffmpeg"])
    print(f"video encoder {encoder}", flush=True)
    cmd = record_argv(
        source,
        out_pattern,
        env=env,
        ffmpeg=paths["ffmpeg"],
        segment_seconds=seg,
        preview_rtsp=preview_rtsp,
    )
    print("exec:", " ".join(cmd), flush=True)

    proc = subprocess.Popen(cmd)
    t0 = time.time()
    native_root = os.path.join(paths["storage"], "inputs", args.input_id, "native")
    indexed: set[str] = set()
    pending: dict[str, tuple[int, int]] = {}
    known: dict[str, tuple[int, int]] = {}
    jobs: queue.Queue = queue.Queue()
    stop_side = threading.Event()
    side = threading.Thread(
        target=_drain_side,
        args=(
            stop_side,
            jobs,
            env,
            source,
            paths["ffmpeg"],
            paths["ffprobe"],
            paths["storage"],
            args.input_id,
        ),
        name="nexrec-record-side",
        daemon=True,
    )
    side.start()

    try:
        while not STOP:
            if args.once_seconds and (time.time() - t0) >= args.once_seconds:
                break
            rc = proc.poll()
            # Pre-create tomorrow's UTC dir near midnight so strftime can open files.
            os.makedirs(chunk_dir(paths["storage"], args.input_id, "native", utcnow()), exist_ok=True)
            skip = newest_mp4(native_root) if proc.poll() is None else None
            try:
                indexed_now = scan_dir(
                    conn,
                    native_root,
                    args.input_id,
                    kind="native",
                    ffprobe=paths["ffprobe"],
                    skip_basename=skip,
                    pending=pending,
                    known=known,
                )
            except Exception as exc:  # noqa: BLE001 — never fail ingest
                print(f"index skip: {exc}", file=sys.stderr, flush=True)
                indexed_now = []
            _note_indexed(jobs, indexed, indexed_now, source)
            if side.is_alive() and jobs.empty():
                jobs.put(("backfill", {}))
            try:
                write_heartbeat(
                    conn,
                    input_id=args.input_id,
                    source_type=str(source.get("source_type") or ""),
                    proc_alive=proc.poll() is None,
                    started_at=t0,
                    segment_s=seg,
                    media_root=native_root,
                    device=str(source.get("decklink_device") or ""),
                    env=env,
                )
            except Exception as exc:  # noqa: BLE001 — heartbeat must not stop ingest
                print(f"heartbeat skip: {exc}", file=sys.stderr, flush=True)
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
        # Final index including last file. A fragment with no moov must not
        # replace the process exit.
        try:
            final = scan_dir(
                conn,
                native_root,
                args.input_id,
                kind="native",
                ffprobe=paths["ffprobe"],
                pending=pending,
                known=known,
            )
            _note_indexed(jobs, indexed, final, source)
        except Exception as exc:  # noqa: BLE001
            print(f"index skip: {exc}", file=sys.stderr, flush=True)
        stop_side.set()
        jobs.put(None)
        side.join(timeout=2)
        try:
            write_heartbeat(
                conn,
                input_id=args.input_id,
                source_type=str(source.get("source_type") or ""),
                proc_alive=False,
                started_at=t0,
                segment_s=seg,
                media_root=native_root,
                device=str(source.get("decklink_device") or ""),
                env=env,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"heartbeat skip: {exc}", file=sys.stderr, flush=True)
    return 0 if proc.returncode in (0, None, 255, -2, -15) else (proc.returncode or 1)


if __name__ == "__main__":
    sys.exit(main())
