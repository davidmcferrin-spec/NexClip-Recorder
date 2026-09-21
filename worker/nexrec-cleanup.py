#!/usr/bin/env python3
"""Retention + free-space cleanup. Safe to run twice daily or on demand."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from nexrec_db import connect, fetchall, migrate, delete_chunk_side_data  # noqa: E402
from nexrec_util import (  # noqa: E402
    data_paths,
    iso_z,
    load_env_file,
    parse_bytes,
    parse_iso,
    utcnow,
)


def fs_free(path: str) -> int:
    os.makedirs(path, exist_ok=True)
    st = os.statvfs(path)
    return int(st.f_bavail * st.f_frsize)


def unlink_quiet(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        print(f"unlink {path}: {exc}", file=sys.stderr)
        return False


def expire_exports(conn, now_iso: str) -> int:
    rows = fetchall(
        conn,
        """
        SELECT * FROM exports
        WHERE protected = 0
          AND status = 'done'
          AND expires_at IS NOT NULL
          AND expires_at < ?
        """,
        (now_iso,),
    )
    n = 0
    for row in rows:
        if row.get("path"):
            unlink_quiet(row["path"])
        conn.execute("DELETE FROM exports WHERE id=?", (row["id"],))
        n += 1
    conn.commit()
    return n


def expire_chunks(conn, now) -> int:
    inputs = fetchall(conn, "SELECT id, retention_days FROM inputs")
    n = 0
    for inp in inputs:
        days = int(inp["retention_days"] or 28)
        cutoff = iso_z(now - timedelta(days=days))
        rows = fetchall(
            conn,
            "SELECT * FROM chunks WHERE input_id=? AND start_at < ?",
            (inp["id"], cutoff),
        )
        for row in rows:
            unlink_quiet(row["path"])
            delete_chunk_side_data(conn, row["id"])
            conn.execute("DELETE FROM chunks WHERE id=?", (row["id"],))
            n += 1
    conn.commit()
    return n


def orphans(conn, storage: str) -> int:
    """Files on disk with no row, and rows whose file is gone."""
    n = 0
    rows = fetchall(conn, "SELECT id, path FROM chunks")
    seen = set()
    for row in rows:
        seen.add(os.path.abspath(row["path"]))
        if not os.path.isfile(row["path"]):
            conn.execute("UPDATE chunks SET orphan=1 WHERE id=?", (row["id"],))
            delete_chunk_side_data(conn, row["id"])
            conn.execute("DELETE FROM chunks WHERE id=?", (row["id"],))
            n += 1
    native_root = os.path.join(storage, "inputs")
    if os.path.isdir(native_root):
        for dirpath, _d, files in os.walk(native_root):
            for name in files:
                if not name.endswith(".mp4"):
                    continue
                path = os.path.abspath(os.path.join(dirpath, name))
                if path not in seen:
                    unlink_quiet(path)
                    n += 1
    conn.commit()
    return n


def free_space_pass(conn, storage: str, floor: int) -> int:
    n = 0
    while fs_free(storage) < floor:
        # Oldest unprotected export first.
        row = conn.execute(
            """
            SELECT * FROM exports
            WHERE protected = 0 AND status = 'done' AND path IS NOT NULL
            ORDER BY created_at ASC LIMIT 1
            """
        ).fetchone()
        if row:
            unlink_quiet(row["path"])
            conn.execute("DELETE FROM exports WHERE id=?", (row["id"],))
            conn.commit()
            n += 1
            continue
        crow = conn.execute(
            """
            SELECT * FROM chunks WHERE ready=1 AND orphan=0
            ORDER BY start_at ASC LIMIT 1
            """
        ).fetchone()
        if not crow:
            break
        unlink_quiet(crow["path"])
        delete_chunk_side_data(conn, crow["id"])
        conn.execute("DELETE FROM chunks WHERE id=?", (crow["id"],))
        conn.commit()
        n += 1
    return n


def run(env: dict) -> dict:
    paths = data_paths(env)
    os.makedirs(paths["storage"], exist_ok=True)
    conn = connect(paths["db"])
    migrate(conn)
    now = utcnow()
    now_iso = iso_z(now)
    stats = {
        "exports_expired": expire_exports(conn, now_iso),
        "chunks_expired": expire_chunks(conn, now),
        "orphans": orphans(conn, paths["storage"]),
        "freed_for_floor": 0,
        "free_bytes": fs_free(paths["storage"]),
    }
    floor = parse_bytes(env.get("NEXREC_FREE_SPACE_FLOOR") or "0")
    if floor > 0:
        stats["freed_for_floor"] = free_space_pass(conn, paths["storage"], floor)
        stats["free_bytes"] = fs_free(paths["storage"])
    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env", default="")
    args = p.parse_args(argv)
    env = load_env_file(args.env) if args.env else dict(os.environ)
    stats = run(env)
    print(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
