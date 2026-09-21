#!/usr/bin/env python3
"""Sidecar intelligence worker: chunk analyze + CALM/LKFS jobs.

Does not remux recordings. PHP enqueues analyze_jobs; this process drains them
and record.py also calls analyze_chunk() after a file is indexed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from nexrec_db import (  # noqa: E402
    chunks_overlapping,
    connect,
    fetchall,
    fetchone,
    migrate,
)
from nexrec_features import analyze_chunk, measure_concat_loudness  # noqa: E402
from nexrec_util import data_paths, iso_z, load_env_file  # noqa: E402

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "nexrec_export_mod", os.path.join(HERE, "nexrec-export.py")
)
_exp = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_exp)
write_concat = _exp.write_concat
trim_offsets = _exp.trim_offsets


def analyze_indexed_chunk(conn, env: dict, chunk_id: str) -> dict:
    chunk = fetchone(conn, "SELECT * FROM chunks WHERE id=?", (chunk_id,))
    if not chunk:
        return {"ok": False, "error": "chunk not found"}
    source = fetchone(conn, "SELECT * FROM inputs WHERE id=?", (chunk["input_id"],)) or {
        "id": chunk["input_id"]
    }
    paths = data_paths(env)
    stats = analyze_chunk(
        conn,
        env,
        source,
        chunk,
        ffmpeg=paths["ffmpeg"],
        ffprobe=paths["ffprobe"],
        storage=paths["storage"],
    )
    return {"ok": True, **stats}


def drain_loudness_job(conn, env: dict, job: dict) -> None:
    paths = data_paths(env)
    iid = job.get("input_id") or ""
    chunks = chunks_overlapping(conn, iid, job["t_in"], job["t_out"], kind="native")
    if not chunks:
        raise RuntimeError(f"no chunks for {iid} in window")
    tmp = os.path.join(paths["storage"], "tmp")
    os.makedirs(tmp, exist_ok=True)
    concat_path = os.path.join(tmp, f"{job['id']}_{iid}.loudness.concat.txt")
    write_concat([c["path"] for c in chunks], concat_path)
    ss, dur = trim_offsets(chunks, job["t_in"], job["t_out"])
    summary = measure_concat_loudness(
        conn,
        env,
        concat_path,
        ss,
        dur,
        iid,
        job.get("export_id"),
        job["t_in"],
        ffmpeg=paths["ffmpeg"],
    )
    conn.execute(
        """
        UPDATE analyze_jobs SET status=?, result_json=?, error=?, updated_at=?
        WHERE id=?
        """,
        (
            "done" if summary.get("ok") else "error",
            json.dumps(summary, separators=(",", ":")),
            summary.get("error"),
            iso_z(),
            job["id"],
        ),
    )
    conn.commit()


def drain_once(conn, env: dict, job_id: str = "") -> int:
    if job_id:
        jobs = fetchall(conn, "SELECT * FROM analyze_jobs WHERE id=?", (job_id,))
    else:
        jobs = fetchall(
            conn,
            "SELECT * FROM analyze_jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 8",
        )
    n = 0
    for job in jobs:
        conn.execute(
            "UPDATE analyze_jobs SET status='running', updated_at=? WHERE id=?",
            (iso_z(), job["id"]),
        )
        conn.commit()
        try:
            if job["kind"] == "loudness":
                drain_loudness_job(conn, env, job)
            elif job["kind"] == "chunk" and job.get("path"):
                row = fetchone(conn, "SELECT * FROM chunks WHERE path=?", (job["path"],))
                if not row:
                    raise RuntimeError("chunk path not indexed")
                analyze_indexed_chunk(conn, env, row["id"])
                conn.execute(
                    "UPDATE analyze_jobs SET status='done', updated_at=? WHERE id=?",
                    (iso_z(), job["id"]),
                )
                conn.commit()
            else:
                raise RuntimeError(f"unknown analyze kind {job['kind']}")
            n += 1
        except Exception as exc:  # noqa: BLE001 — job must not kill the worker
            conn.execute(
                "UPDATE analyze_jobs SET status='error', error=?, updated_at=? WHERE id=?",
                (str(exc)[:500], iso_z(), job["id"]),
            )
            conn.commit()
    return n


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="NexCLIP Recorder sidecar analyze")
    p.add_argument("--env", default="")
    p.add_argument("--once", action="store_true")
    p.add_argument("--job-id", default="")
    p.add_argument("--chunk-id", default="")
    p.add_argument("--loop", action="store_true")
    args = p.parse_args(argv)
    env = load_env_file(args.env) if args.env else dict(os.environ)
    paths = data_paths(env)
    conn = connect(paths["db"])
    migrate(conn)
    if args.chunk_id:
        print(analyze_indexed_chunk(conn, env, args.chunk_id), flush=True)
        return 0
    if args.once or args.job_id:
        n = drain_once(conn, env, job_id=args.job_id)
        print({"drained": n}, flush=True)
        return 0
    while True:
        drain_once(conn, env)
        if not args.loop:
            break
        time.sleep(2.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
