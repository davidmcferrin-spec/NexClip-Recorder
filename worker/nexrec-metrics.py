#!/usr/bin/env python3
"""Sample CPU, memory, disk, GPU, and SDI lock. Safe to run every minute."""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from nexrec_db import connect, fetchall, migrate, overlay_app_settings  # noqa: E402
from nexrec_metrics import collect_host, record_sample, sdi_rows  # noqa: E402
from nexrec_util import data_paths, iso_z, load_env_file  # noqa: E402


def run(env: dict) -> dict:
    conn = connect(env)
    migrate(conn)
    env = overlay_app_settings(conn, env)
    paths = data_paths(env)
    sampled_at = iso_z()
    host = collect_host(paths["storage"], sampled_at)
    beats = fetchall(
        conn,
        "SELECT input_id, sdi_lock, signal, format FROM input_heartbeats ORDER BY input_id",
    )
    locks = sdi_rows(beats, sampled_at)
    record_sample(conn, host, locks)
    return {"sampled_at": sampled_at, "inputs": len(locks), "cpu_pct": host.get("cpu_pct")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default="")
    args = parser.parse_args(argv)
    env = load_env_file(args.env) if args.env else dict(os.environ)
    try:
        print(run(env))
    except Exception as exc:
        print(f"metrics sample failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
