#!/usr/bin/env python3
"""Export worker: concat overlapping 5-min chunks and trim to marked in/out."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from nexrec_db import chunks_overlapping, connect, fetchall, fetchone, migrate, overlay_app_settings  # noqa: E402
from nexrec_ffmpeg import export_concat_argv  # noqa: E402
from nexrec_util import data_paths, iso_z, load_env_file, parse_iso, utcnow  # noqa: E402


def write_concat(paths: list[str], dest: str) -> None:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        for p in paths:
            # concat demuxer: single quotes, escape ' as '\''
            esc = p.replace("'", r"'\''")
            fh.write(f"file '{esc}'\n")


def trim_offsets(chunks: list[dict], t_in: str, t_out: str) -> tuple[float, float]:
    """ss relative to first chunk start; duration of the requested window."""
    start = parse_iso(t_in)
    end = parse_iso(t_out)
    first = parse_iso(chunks[0]["start_at"])
    ss = max(0.0, (start - first).total_seconds())
    duration = max(0.001, (end - start).total_seconds())
    return ss, duration


def run_export(conn, env: dict, job: dict, copy: bool = True) -> None:
    paths = data_paths(env)
    input_ids = json.loads(job["input_ids"])
    quality = job.get("quality") or "full"
    kind = "proxy" if quality == "proxy" else "native"
    # v0: proxy requested but no proxy files → transcode from native.
    force_tx = quality == "proxy"

    dest_dir = env.get("NEXREC_EXPORTS_DIR") or os.path.join(paths["storage"], "exports")
    os.makedirs(dest_dir, exist_ok=True)
    tmp_dir = env.get("NEXREC_SCRATCH_DIR") or os.path.join(paths["storage"], "tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    produced: list[str] = []
    for iid in input_ids:
        chunks = chunks_overlapping(conn, iid, job["t_in"], job["t_out"], kind=kind)
        if not chunks and kind == "proxy":
            chunks = chunks_overlapping(conn, iid, job["t_in"], job["t_out"], kind="native")
            force_tx = True
        if not chunks:
            raise RuntimeError(f"no chunks for input {iid} in window")
        concat_path = os.path.join(tmp_dir, f"{job['id']}_{iid}.concat.txt")
        write_concat([c["path"] for c in chunks], concat_path)
        ss, dur = trim_offsets(chunks, job["t_in"], job["t_out"])
        suffix = f"_{iid}" if len(input_ids) > 1 else ""
        dest = os.path.join(dest_dir, f"{job['id']}{suffix}.mp4")
        cmd = export_concat_argv(
            concat_path,
            dest,
            ss,
            dur,
            copy=copy and not force_tx,
            ffmpeg=paths["ffmpeg"],
            env=env,
        )
        print("exec:", " ".join(cmd), flush=True)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            # Copy can fail on timestamp discontinuities — retry with encode.
            if copy and not force_tx:
                cmd = export_concat_argv(
                    concat_path, dest, ss, dur, copy=False,
                    ffmpeg=paths["ffmpeg"], env=env,
                )
                print("retry encode:", " ".join(cmd), flush=True)
                proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr[-2000:] or "ffmpeg export failed")
        produced.append(dest)

    primary = produced[0]
    size = os.path.getsize(primary) if os.path.isfile(primary) else 0
    conn.execute(
        """UPDATE exports SET status='done', path=?, size_bytes=?, error=NULL
           WHERE id=?""",
        (primary, size, job["id"]),
    )
    conn.commit()


def process_one(conn, env: dict, job_id: str | None = None) -> bool:
    if job_id:
        job = fetchone(conn, "SELECT * FROM exports WHERE id=?", (job_id,))
    else:
        job = fetchone(
            conn,
            "SELECT * FROM exports WHERE status='queued' ORDER BY created_at ASC LIMIT 1",
        )
    if not job:
        return False
    conn.execute(
        "UPDATE exports SET status='running' WHERE id=?",
        (job["id"],),
    )
    conn.commit()
    try:
        run_export(conn, env, job)
    except Exception as exc:  # noqa: BLE001 — worker must mark the row
        conn.execute(
            "UPDATE exports SET status='error', error=? WHERE id=?",
            (str(exc)[:2000], job["id"]),
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
    env = load_env_file(args.env) if args.env else dict(os.environ)
    paths = data_paths(env)
    conn = connect(env)
    migrate(conn)
    env = overlay_app_settings(conn, env)
    paths = data_paths(env)
    if args.once or args.job_id:
        process_one(conn, env, args.job_id or None)
        return 0
    while True:
        if not process_one(conn, env, None):
            time.sleep(args.poll)
    return 0


if __name__ == "__main__":
    sys.exit(main())
