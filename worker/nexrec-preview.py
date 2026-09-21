#!/usr/bin/env python3
"""Publish a proxy encode of one input to MediaMTX (RTSP → WHEP)."""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from nexrec_db import connect, fetchone, migrate  # noqa: E402
from nexrec_ffmpeg import preview_argv  # noqa: E402
from nexrec_util import data_paths, load_env_file, valid_input_id  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env", default="")
    p.add_argument("--input-env", default="")
    p.add_argument("--input-id", required=True)
    p.add_argument("--print-cmd", action="store_true")
    args = p.parse_args(argv)
    if not valid_input_id(args.input_id):
        print("invalid --input-id", file=sys.stderr)
        return 2
    env = load_env_file(args.env) if args.env else dict(os.environ)
    if args.input_env:
        env = load_env_file(args.input_env, env)
    paths = data_paths(env)
    conn = connect(paths["db"])
    migrate(conn)
    row = fetchone(conn, "SELECT * FROM inputs WHERE id=?", (args.input_id,))
    if not row:
        row = {
            "source_type": (env.get("SOURCE_TYPE") or "testsrc").lower(),
            "url": env.get("SOURCE_URL") or "",
            "decklink_device": env.get("DECKLINK_DEVICE") or "",
            "preview_path": env.get("PREVIEW_PATH") or "in0",
        }
    path = row.get("preview_path") or env.get("PREVIEW_PATH") or "in0"
    jwt = env.get("NEXREC_PUBLISH_JWT") or ""
    base = (env.get("NEXREC_MEDIAMTX_RTSP") or "rtsp://127.0.0.1:8554").rstrip("/")
    rtsp = f"{base}/{path}"
    if jwt:
        rtsp += ("&" if "?" in rtsp else "?") + "jwt=" + jwt
    cmd = preview_argv(row, rtsp, env=env, ffmpeg=paths["ffmpeg"])
    if args.print_cmd:
        print(" ".join(cmd))
        return 0
    os.execvp(cmd[0], cmd)
    return 1


if __name__ == "__main__":
    sys.exit(main())
