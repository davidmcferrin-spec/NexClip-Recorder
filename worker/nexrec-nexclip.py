#!/usr/bin/env python3
"""Poll NexClip studio recorder schedule and enqueue exports when windows end."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from nexrec_db import connect, enqueue_export, fetchone, migrate  # noqa: E402
from nexrec_util import (  # noqa: E402
    data_paths,
    env_bool,
    env_int,
    iso_z,
    load_env_file,
    new_id,
    parse_iso,
    utcnow,
)


def http_json(method: str, url: str, api_key: str, body: dict | None = None, timeout: int = 10) -> dict:
    data = None
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} {url}") from exc


def cache_event(conn, ev: dict) -> None:
    conn.execute(
        """
        INSERT INTO nexclip_events (id, input_id, title, start_at, end_at, payload, export_id, status, fetched_at)
        VALUES (:id, :input_id, :title, :start_at, :end_at, :payload, :export_id, :status, :fetched_at)
        ON CONFLICT(id) DO UPDATE SET
          input_id=excluded.input_id,
          title=excluded.title,
          start_at=excluded.start_at,
          end_at=excluded.end_at,
          payload=excluded.payload,
          fetched_at=excluded.fetched_at
        """,
        {
            "id": ev["id"],
            "input_id": ev.get("input_id"),
            "title": ev.get("title"),
            "start_at": ev["start_at"],
            "end_at": ev["end_at"],
            "payload": json.dumps(ev),
            "export_id": None,
            "status": "cached",
            "fetched_at": iso_z(),
        },
    )
    conn.commit()


def maybe_enqueue(conn, env: dict, ev: dict) -> str | None:
    end = parse_iso(ev["end_at"])
    if end > utcnow():
        return None
    existing = fetchone(
        conn,
        "SELECT id FROM exports WHERE nexclip_schedule_id=?",
        (ev["id"],),
    )
    if existing:
        return None
    iid = ev.get("input_id")
    if not iid:
        return None
    inp = fetchone(conn, "SELECT id FROM inputs WHERE id=?", (iid,))
    if not inp:
        print(f"skip schedule {ev['id']}: unknown input {iid}", file=sys.stderr)
        return None
    days = env_int(env, "NEXREC_EXPORT_RETENTION_DAYS", 15)
    exp_id = new_id("exp")
    enqueue_export(
        conn,
        {
            "id": exp_id,
            "status": "queued",
            "input_ids": json.dumps([iid]),
            "t_in": ev["start_at"],
            "t_out": ev["end_at"],
            "quality": ev.get("quality") or "full",
            "scope": "one",
            "path": None,
            "size_bytes": None,
            "protected": 1 if ev.get("protect_export") else 0,
            "error": None,
            "created_by": "nexclip",
            "created_at": iso_z(),
            "expires_at": None if ev.get("protect_export") else iso_z(utcnow() + timedelta(days=days)),
            "nexclip_schedule_id": ev["id"],
        },
    )
    conn.execute(
        "UPDATE nexclip_events SET export_id=?, status='queued' WHERE id=?",
        (exp_id, ev["id"]),
    )
    conn.commit()
    return exp_id


def callback_done(env: dict, conn, ev: dict, export_id: str) -> None:
    base = (env.get("NEXCLIP_BASE_URL") or "").rstrip("/")
    key = env.get("NEXCLIP_API_KEY") or ""
    path = env.get("NEXCLIP_EXPORT_CALLBACK_PATH") or "/api/studio/recorder-exports"
    if not base or not key:
        return
    job = fetchone(conn, "SELECT * FROM exports WHERE id=?", (export_id,))
    if not job or job.get("status") != "done":
        return
    pub = (env.get("NEXREC_PUBLIC_URL") or "").rstrip("/")
    url = f"{pub}/api/exports/{export_id}/file" if pub else None
    body = {
        "schedule_id": ev["id"],
        "instance_id": env.get("NEXREC_INSTANCE_ID") or "",
        "input_id": ev.get("input_id"),
        "export_id": export_id,
        "t_in": job["t_in"],
        "t_out": job["t_out"],
        "status": "done",
        "path": job.get("path"),
        "url": url,
        "size_bytes": job.get("size_bytes"),
    }
    http_json("POST", base + path, key, body)


def ingest_events(conn, env: dict, events: list) -> int:
    n = 0
    for ev in events:
        if not isinstance(ev, dict) or not ev.get("id"):
            continue
        cache_event(conn, ev)
        exp_id = maybe_enqueue(conn, env, ev)
        if exp_id:
            n += 1
            callback_done(env, conn, ev, exp_id)
    return n


def fetch_schedule(env: dict) -> list:
    stub = env.get("NEXCLIP_SCHEDULE_STUB") or ""
    if stub and os.path.isfile(stub):
        with open(stub, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return list(data.get("events") or data if isinstance(data, list) else [])
    base = (env.get("NEXCLIP_BASE_URL") or "").rstrip("/")
    key = env.get("NEXCLIP_API_KEY") or ""
    if not base or not key:
        return []
    path = env.get("NEXCLIP_SCHEDULE_PATH") or "/api/studio/recorder-schedule"
    now = utcnow()
    url = (
        f"{base}{path}?instance_id={env.get('NEXREC_INSTANCE_ID') or ''}"
        f"&from={iso_z(now - timedelta(days=2))}&to={iso_z(now + timedelta(days=1))}"
    )
    data = http_json("GET", url, key)
    return list(data.get("events") or [])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env", default="")
    args = p.parse_args(argv)
    env = load_env_file(args.env) if args.env else dict(os.environ)
    if not env_bool(env, "NEXREC_NEXCLIP_ENABLED", False) and not env.get("NEXCLIP_SCHEDULE_STUB"):
        print("nexclip disabled")
        return 0
    paths = data_paths(env)
    conn = connect(paths["db"])
    migrate(conn)
    events = fetch_schedule(env)
    n = ingest_events(conn, env, events)
    print({"events": len(events), "enqueued": n})
    return 0


if __name__ == "__main__":
    sys.exit(main())
