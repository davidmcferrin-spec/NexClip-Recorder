#!/usr/bin/env python3
"""Transfer queue: rclone sync of finished exports, one destination at a time."""

from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from nexrec_db import connect, migrate, overlay_app_settings  # noqa: E402
from nexrec_deliver import process_one, recover_running  # noqa: E402
from nexrec_util import load_env_file, pin_process_utc  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default="")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll", type=float, default=2.0)
    args = parser.parse_args(argv)
    pin_process_utc()
    env = load_env_file(args.env) if args.env else dict(os.environ)
    conn = connect(env)
    migrate(conn)
    env = overlay_app_settings(conn, env)
    recover_running(conn)
    if args.once:
        process_one(conn, env)
        return 0
    while True:
        if not process_one(conn, env):
            time.sleep(args.poll)
    return 0


if __name__ == "__main__":
    sys.exit(main())
