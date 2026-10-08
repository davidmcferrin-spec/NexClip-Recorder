#!/usr/bin/env python3
"""Write timeline filmstrips for closed recordings.

One frame every 10 seconds. Newest first, then the whole archive.
A JPEG with no frame-count file is a legacy single still and is replaced.
Safe to stop and run again.

    sudo -u www-data python3 /opt/NexClip-Recorder/worker/nexrec-thumbs.py --env /etc/nexrec/nexrec.env
    sudo -u www-data python3 /opt/NexClip-Recorder/worker/nexrec-thumbs.py --env /etc/nexrec/nexrec.env --input studio-a
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from nexrec_db import connect, migrate, overlay_app_settings  # noqa: E402
from nexrec_index import backfill_thumbs  # noqa: E402
from nexrec_util import data_paths, load_env_file  # noqa: E402


def run(env: dict, input_id: str | None) -> dict:
    conn = connect(env)
    migrate(conn)
    env = overlay_app_settings(conn, env)
    paths = data_paths(env)

    def report(path: str, ok: bool) -> None:
        print(("thumb " if ok else "thumb failed ") + path, flush=True)

    n = backfill_thumbs(
        conn,
        paths["ffmpeg"],
        limit=None,
        input_id=input_id,
        scan_limit=None,
        upgrade=True,
        on_result=report,
    )
    return {"thumbs": n, "input_id": input_id or ""}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Backfill timeline stills for existing recordings")
    p.add_argument("--env", default="")
    p.add_argument("--input", default="", help="One input id. Blank walks every input.")
    args = p.parse_args(argv)
    env = load_env_file(args.env) if args.env else dict(os.environ)
    stats = run(env, args.input.strip() or None)
    print(stats, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
