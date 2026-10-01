#!/usr/bin/env python3
"""Export worker: concat overlapping 5-min chunks and trim to marked in/out."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from nexrec_db import chunks_overlapping, connect, fetchall, fetchone, migrate, overlay_app_settings  # noqa: E402
from nexrec_deliver import export_transfer_busy, promote_deliveries  # noqa: E402
from nexrec_ffmpeg import export_concat_argv, parse_export_progress, pin_video_encoder  # noqa: E402
from nexrec_util import (  # noqa: E402
    data_paths,
    export_file_basenames,
    iso_z,
    load_env_file,
    parse_iso,
    pin_process_utc,
    utcnow,
)


def write_concat(paths: list[str], dest: str) -> None:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        for p in paths:
            # concat demuxer: single quotes, escape ' as '\''
            esc = p.replace("'", r"'\''")
            fh.write(f"file '{esc}'\n")


class ExportCancelled(Exception):
    pass


def trim_offsets(chunks: list[dict], t_in: str, t_out: str) -> tuple[float, float]:
    """ss relative to first chunk start; duration of the requested window."""
    start = parse_iso(t_in)
    end = parse_iso(t_out)
    first = parse_iso(chunks[0]["start_at"])
    ss = max(0.0, (start - first).total_seconds())
    duration = max(0.001, (end - start).total_seconds())
    return ss, duration


def cancel_requested(conn, job_id: str) -> bool:
    row = fetchone(conn, "SELECT cancel_requested, status FROM exports WHERE id=?", (job_id,))
    if row is None:
        return True
    if str(row.get("status") or "") == "cancelled":
        return True
    try:
        return int(row.get("cancel_requested") or 0) == 1
    except (TypeError, ValueError):
        return False


def note_progress(conn, job_id: str, pct: float, encode_mode: str) -> None:
    try:
        conn.execute(
            """UPDATE exports SET progress_pct=?, progress_at=?, encode_mode=?
               WHERE id=? AND status='running'""",
            (round(max(0.0, min(99.0, pct)), 1), iso_z(), encode_mode, job_id),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — progress must not fail the trim
        print(f"progress {job_id}: {exc}", file=sys.stderr)
        rollback = getattr(conn, "_rollback_quiet", None)
        if rollback is not None:
            rollback()


def run_ffmpeg(conn, job_id: str, cmd: list[str], duration: float, encode_mode: str, span: tuple[float, float]) -> None:
    """Run one trim. Progress lines update the row. A cancel flag kills FFmpeg."""
    if cmd:
        cmd = cmd[:-1] + ["-progress", "pipe:1", "-nostats", cmd[-1]]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    err_parts: list[str] = []

    def read_err() -> None:
        if proc.stderr is None:
            return
        err_parts.append(proc.stderr.read())

    threading.Thread(target=read_err, daemon=True).start()
    start_pct, end_pct = span
    latest: float | None = None
    last_check = 0.0

    def stop_for_cancel() -> None:
        if proc.poll() is None:
            proc.terminate()
        raise ExportCancelled()

    try:
        stream = proc.stdout
        if stream is not None:
            for line in stream:
                seen = parse_export_progress(line)
                if seen is not None:
                    latest = seen
                now = time.monotonic()
                if now - last_check < 1.0:
                    continue
                last_check = now
                if cancel_requested(conn, job_id):
                    stop_for_cancel()
                if latest is not None and duration > 0:
                    frac = max(0.0, min(1.0, latest / duration))
                    note_progress(conn, job_id, start_pct + frac * (end_pct - start_pct), encode_mode)
        while proc.poll() is None:
            if cancel_requested(conn, job_id):
                stop_for_cancel()
            time.sleep(0.4)
        code = proc.returncode if proc.returncode is not None else 1
    except ExportCancelled:
        if proc.poll() is None:
            proc.kill()
        raise
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    if cancel_requested(conn, job_id):
        raise ExportCancelled()
    if code != 0:
        err = "".join(err_parts)[-2000:]
        raise RuntimeError(err or "ffmpeg export failed")


def chunk_raster(chunks: list) -> tuple[int, int, float]:
    """Width, height, and fps from the first chunk that recorded them."""
    for chunk in chunks:
        try:
            width = int(chunk.get("width") or 0)
            height = int(chunk.get("height") or 0)
            fps = float(chunk.get("fps") or 0)
        except (TypeError, ValueError):
            continue
        if width > 0 and height > 0 and fps > 1:
            return width, height, fps
    return 0, 0, 0.0


def input_label(conn, input_id: str) -> str:
    row = fetchone(conn, "SELECT name FROM inputs WHERE id=?", (input_id,))
    if row and str(row.get("name") or "").strip():
        return str(row["name"]).strip()
    return input_id


def _under_dir(dest_dir: str, path: str) -> bool:
    if not path or not os.path.isfile(path):
        return False
    root = os.path.realpath(dest_dir)
    candidate = os.path.realpath(path)
    return candidate == root or candidate.startswith(root + os.sep)


def previous_export_files(job: dict, dest_dir: str, input_ids: list) -> list[str]:
    """Files this export may already have written, including the old id-based names."""
    found: list[str] = []
    stored = str(job.get("path") or "")
    if stored:
        found.append(stored)
    raw = job.get("file_names") or ""
    names: dict = {}
    if isinstance(raw, dict):
        names = raw
    elif str(raw).strip():
        try:
            parsed = json.loads(str(raw))
            if isinstance(parsed, dict):
                names = parsed
        except json.JSONDecodeError:
            names = {}
    for value in names.values():
        base = os.path.basename(str(value))
        if base:
            found.append(os.path.join(dest_dir, base))
    eid = str(job.get("id") or "")
    if eid:
        found.append(os.path.join(dest_dir, eid + ".mp4"))
        for iid in input_ids:
            found.append(os.path.join(dest_dir, f"{eid}_{iid}.mp4"))
    kept: list[str] = []
    seen: set[str] = set()
    for path in found:
        if not _under_dir(dest_dir, path):
            continue
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        kept.append(path)
    return kept


def run_export(conn, env: dict, job: dict, copy: bool = True) -> None:
    paths = data_paths(env)
    input_ids = json.loads(job["input_ids"])
    quality = job.get("quality") or "full"
    kind = "proxy" if quality == "proxy" else "native"
    # Proxy is a transcode of the native chunks down to 960×540 at 30 fps.
    force_tx = quality == "proxy"

    dest_dir = env.get("NEXREC_EXPORTS_DIR") or os.path.join(paths["storage"], "exports")
    os.makedirs(dest_dir, exist_ok=True)
    tmp_dir = env.get("NEXREC_SCRATCH_DIR") or os.path.join(paths["storage"], "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    labels = [(str(iid), input_label(conn, str(iid))) for iid in input_ids]
    names = export_file_basenames(
        str(job["id"]),
        labels,
        str(job["t_in"]),
        str(job["t_out"]),
        quality,
        str(env.get("NEXREC_TIMEZONE") or ""),
    )
    stale = previous_export_files(job, dest_dir, input_ids)

    produced: list[str] = []
    total = max(1, len(input_ids))
    for index, iid in enumerate(input_ids):
        chunks = chunks_overlapping(conn, iid, job["t_in"], job["t_out"], kind=kind)
        if not chunks and kind == "proxy":
            chunks = chunks_overlapping(conn, iid, job["t_in"], job["t_out"], kind="native")
            force_tx = True
        if not chunks:
            raise RuntimeError(f"no chunks for input {iid} in window")
        concat_path = os.path.join(tmp_dir, f"{job['id']}_{iid}.concat.txt")
        write_concat([c["path"] for c in chunks], concat_path)
        ss, dur = trim_offsets(chunks, job["t_in"], job["t_out"])
        dest = os.path.join(dest_dir, names[str(iid)])
        use_copy = copy and not force_tx
        width, height, fps = chunk_raster(chunks)
        proxy = quality == "proxy"
        cmd = export_concat_argv(
            concat_path, dest, ss, dur, copy=use_copy,
            ffmpeg=paths["ffmpeg"], env=env,
            proxy=proxy, width=width, height=height, fps=fps,
        )
        span = (100.0 * index / total, 100.0 * (index + 1) / total)
        print("exec:", " ".join(cmd), flush=True)
        try:
            run_ffmpeg(conn, job["id"], cmd, dur, "copy" if use_copy else "encode", span)
        except RuntimeError:
            if not use_copy:
                raise
            cmd = export_concat_argv(
                concat_path, dest, ss, dur, copy=False,
                ffmpeg=paths["ffmpeg"], env=env,
                width=width, height=height, fps=fps,
            )
            print("retry encode:", " ".join(cmd), flush=True)
            run_ffmpeg(conn, job["id"], cmd, dur, "encode", span)
        produced.append(dest)

    primary = produced[0]
    size = os.path.getsize(primary) if os.path.isfile(primary) else 0
    done = conn.execute(
        """UPDATE exports SET status='done', progress_pct=100, finished_at=?, path=?, file_names=?, size_bytes=?, error=NULL
           WHERE id=? AND status='running'""",
        (iso_z(), primary, json.dumps(names, separators=(",", ":")), size, job["id"]),
    )
    conn.commit()
    if int(done.rowcount or 0) > 0:
        produced_real = {os.path.realpath(path) for path in produced if os.path.isfile(path)}
        for old in stale:
            if os.path.realpath(old) in produced_real:
                continue
            try:
                os.unlink(old)
            except OSError as exc:
                print(f"export {job['id']} stale file: {exc}", file=sys.stderr, flush=True)
    if int(done.rowcount or 0) > 0:
        try:
            queued = promote_deliveries(conn, job["id"])
        except Exception as exc:  # noqa: BLE001 — the file is already done; the transfer worker can sweep
            print(f"export {job['id']} transfer queue: {exc}", file=sys.stderr, flush=True)
            queued = 0
        if queued:
            print(f"export {job['id']} queued {queued} transfer(s)", flush=True)


def process_one(conn, env: dict, job_id: str | None = None) -> bool:
    if job_id:
        job = fetchone(conn, "SELECT * FROM exports WHERE id=?", (job_id,))
        if job and export_transfer_busy(conn, str(job["id"])):
            return False
    else:
        job = None
        queued = fetchall(
            conn,
            "SELECT * FROM exports WHERE status='queued' ORDER BY created_at ASC LIMIT 8",
        )
        for candidate in queued:
            if not export_transfer_busy(conn, str(candidate["id"])):
                job = candidate
                break
    if not job or str(job.get("status") or "") != "queued":
        return False
    started = conn.execute(
        """UPDATE exports
           SET status='running', started_at=?, progress_pct=0, progress_at=?,
               cancel_requested=0, error=NULL, finished_at=NULL
           WHERE id=? AND status='queued'""",
        (iso_z(), iso_z(), job["id"]),
    )
    conn.commit()
    if getattr(started, "rowcount", 1) == 0:
        return False
    try:
        run_export(conn, env, job)
    except ExportCancelled:
        conn.execute(
            """UPDATE exports SET status='cancelled', finished_at=?, error=NULL
               WHERE id=? AND status IN ('running','queued')""",
            (iso_z(), job["id"]),
        )
        conn.execute(
            """UPDATE deliveries SET status='cancelled', finished_at=?, error=NULL, cancel_requested=0
               WHERE export_id=? AND status='waiting'""",
            (iso_z(), job["id"]),
        )
        conn.commit()
        print(f"export {job['id']} cancelled", flush=True)
    except Exception as exc:  # noqa: BLE001 — worker must mark the row
        current = fetchone(conn, "SELECT status FROM exports WHERE id=?", (job["id"],))
        if current is not None and str(current.get("status") or "") == "cancelled":
            return True
        conn.execute(
            "UPDATE exports SET status='error', finished_at=?, error=? WHERE id=? AND status='running'",
            (iso_z(), str(exc)[:2000], job["id"]),
        )
        conn.commit()
        print(f"export {job['id']} error: {exc}", file=sys.stderr)
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env", default="")
    p.add_argument("--once", action="store_true")
    p.add_argument("--job-id", default="")
    p.add_argument("--poll", type=float, default=2.0)
    args = p.parse_args(argv)
    pin_process_utc()
    env = load_env_file(args.env) if args.env else dict(os.environ)
    paths = data_paths(env)
    conn = connect(env)
    migrate(conn)
    env = overlay_app_settings(conn, env)
    paths = data_paths(env)
    env, encoder = pin_video_encoder(env, paths["ffmpeg"])
    print(f"video encoder {encoder}", flush=True)
    if args.once or args.job_id:
        process_one(conn, env, args.job_id or None)
        return 0
    while True:
        if not process_one(conn, env, None):
            time.sleep(args.poll)
    return 0


if __name__ == "__main__":
    sys.exit(main())
