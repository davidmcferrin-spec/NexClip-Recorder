#!/usr/bin/env python3
"""Publish a proxy encode of one input to MediaMTX (RTSP → WHEP)."""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from nexrec_db import connect, fetchone, migrate, overlay_app_settings  # noqa: E402
from nexrec_ffmpeg import preview_argv, preview_publish_url, preview_unit_allowed  # noqa: E402
from nexrec_util import data_paths, load_env_file, valid_input_id  # noqa: E402

# systemd RestartPreventExitStatus — do not spin if a DeckLink unit is started anyway.
EX_DECKLINK = 78


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env", default="")
    p.add_argument("--input-env", default="")
    p.add_argument("--input-id", required=True)
    p.add_argument("--print-cmd", action="store_true")
    p.add_argument(
        "--check-eligible",
        action="store_true",
        help="exit 0 if this input may run nexrec-preview; exit 1 for DeckLink (systemd ExecCondition)",
    )
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
    env = overlay_app_settings(conn, env)
    paths = data_paths(env)
    row = fetchone(conn, "SELECT * FROM inputs WHERE id=?", (args.input_id,))
    if not row:
        row = {
            "source_type": (env.get("SOURCE_TYPE") or "testsrc").lower(),
            "url": env.get("SOURCE_URL") or "",
            "decklink_device": env.get("DECKLINK_DEVICE") or "",
            "preview_path": env.get("PREVIEW_PATH") or "in0",
        }
    if not preview_unit_allowed(row):
        print(
            "decklink preview is teed inside nexrec-record; not opening a second capture",
            file=sys.stderr,
        )
        return 1 if args.check_eligible else EX_DECKLINK
    if args.check_eligible:
        return 0
    path = row.get("preview_path") or env.get("PREVIEW_PATH") or "in0"
    rtsp = preview_publish_url(str(path), env)
    cmd = preview_argv(row, rtsp, env=env, ffmpeg=paths["ffmpeg"])
    if args.print_cmd:
        print(" ".join(cmd))
        return 0
    os.execvp(cmd[0], cmd)
    return 1


if __name__ == "__main__":
    sys.exit(main())
