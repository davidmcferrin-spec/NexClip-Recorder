#!/usr/bin/env python3
"""NexClip Mode 2 node client: register, check-in, poll export-requests.

Calendar does not start/stop capture. This box records continuously; the hub
hands us export windows. Node auth is enrollment secret + bearer token.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from datetime import timedelta
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from nexrec_db import (  # noqa: E402
    connect,
    enqueue_export,
    fetchall,
    fetchone,
    get_setting,
    migrate,
    set_setting,
)
from nexrec_util import (  # noqa: E402
    data_paths,
    env_bool,
    env_int,
    iso_z,
    load_env_file,
    new_id,
    utcnow,
)

TYPE_MAP = {
    "decklink": "decklink",
    "srt": "srt",
    "ndi": "ndi",
    "rtsp": "srt",
    "udp": "srt",
    "tcp": "srt",
    "rtp": "srt",
    "testsrc": "srt",
}


class Http204(Exception):
    """Idle poll."""


def http_json(
    method: str,
    url: str,
    token: str = "",
    body: dict | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout: int = 10,
) -> dict | None:
    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 204:
                return None
            raw = resp.read().decode("utf-8")
            if not raw.strip():
                return None
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        if exc.code == 204:
            return None
        raise RuntimeError(f"HTTP {exc.code} {url} {exc.read()[:300]!r}") from exc


def api_prefix(env: dict) -> str:
    return (env.get("NEXCLIP_API_PREFIX") or "/api/v1").rstrip("/")


def base_url(env: dict) -> str:
    return (env.get("NEXCLIP_BASE_URL") or "").rstrip("/")


def enrollment_secret(env: dict) -> str:
    return env.get("NEXCLIP_ENROLLMENT_SECRET") or env.get("RECORDER_ENROLLMENT_SECRET") or ""


def map_recorder_type(env: dict, inputs: list[dict]) -> str:
    forced = (env.get("NEXCLIP_RECORDER_TYPE") or "").strip().lower()
    if forced in ("decklink", "srt", "ndi"):
        return forced
    types = {(inp.get("source_type") or "").lower() for inp in inputs}
    if "decklink" in types:
        return "decklink"
    if "ndi" in types:
        return "ndi"
    return "srt"


def clamp_slots(n: int) -> int:
    return max(4, min(8, int(n)))


def assigned_slots(inputs: list[dict]) -> list[tuple[int, dict]]:
    """(slot 1-8, input row) for inputs with an explicit nexclip_slot.

    Unmapped enabled inputs stay local-only and do not appear on the hub.
    Duplicate slots: first enabled input in id order wins.
    """
    out: list[tuple[int, dict]] = []
    used: set[int] = set()
    enabled = [i for i in inputs if int(i.get("enabled") or 0)]
    enabled.sort(key=lambda r: (r.get("id") or ""))
    for inp in enabled:
        raw = inp.get("nexclip_slot")
        try:
            slot = int(raw) if raw not in (None, "", 0, "0") else 0
        except (TypeError, ValueError):
            slot = 0
        if slot < 1 or slot > 8 or slot in used:
            continue
        used.add(slot)
        out.append((slot, inp))
    out.sort(key=lambda pair: pair[0])
    return out


def buffer_earliest(conn, input_id: str) -> str | None:
    row = fetchone(
        conn,
        """
        SELECT MIN(start_at) AS t FROM chunks
        WHERE input_id=? AND ready=1 AND orphan=0 AND kind='native'
        """,
        (input_id,),
    )
    t = (row or {}).get("t")
    return str(t) if t else None


def node_creds(conn, env: dict) -> tuple[str, str]:
    rid = env.get("NEXCLIP_RECORDER_ID") or get_setting(conn, "nexclip_recorder_id")
    tok = env.get("NEXCLIP_NODE_TOKEN") or get_setting(conn, "nexclip_node_token")
    return rid, tok


def save_creds(conn, recorder_id: str, token: str) -> None:
    set_setting(conn, "nexclip_recorder_id", recorder_id)
    set_setting(conn, "nexclip_node_token", token)


def ensure_registered(conn, env: dict, inputs: list[dict]) -> tuple[str, str]:
    rid, tok = node_creds(conn, env)
    if rid and tok:
        return rid, tok
    stub = env.get("NEXCLIP_MODE2_STUB") or ""
    if stub:
        save_creds(conn, "rec_stub", "tok_stub")
        return "rec_stub", "tok_stub"
    secret = enrollment_secret(env)
    base = base_url(env)
    if not secret or not base:
        raise RuntimeError("NEXCLIP_ENROLLMENT_SECRET and NEXCLIP_BASE_URL required to register")
    n_slots = clamp_slots(env_int(env, "NEXCLIP_NUM_SLOTS", max(len(assigned_slots(inputs)), 4)))
    host = env.get("NEXREC_INSTANCE_ID") or socket.gethostname()
    body = {
        "hostname": host,
        "recorder_type": map_recorder_type(env, inputs),
        "num_slots": n_slots,
    }
    url = f"{base}{api_prefix(env)}/recorders/register"
    data = http_json(
        "POST",
        url,
        extra_headers={"X-Enrollment-Secret": secret},
        body=body,
    ) or {}
    recorder_id = str(data.get("recorder_id") or "")
    token = str(data.get("token") or "")
    if not recorder_id or not token:
        raise RuntimeError(f"register missing id/token: {data}")
    save_creds(conn, recorder_id, token)
    return recorder_id, token


def checkin(conn, env: dict, recorder_id: str, token: str, inputs: list[dict]) -> None:
    reports = []
    for slot, inp in assigned_slots(inputs):
        reports.append(
            {
                "slot": slot,
                "reported_label": inp.get("name") or inp.get("id"),
                "is_present": bool(int(inp.get("enabled") or 0)),
                "buffer_earliest_at": buffer_earliest(conn, inp["id"]),
            }
        )
    body = {"status": "online", "inputs": reports}
    stub = env.get("NEXCLIP_MODE2_STUB") or ""
    if stub:
        return
    url = f"{base_url(env)}{api_prefix(env)}/recorders/{recorder_id}/checkin"
    http_json("POST", url, token=token, body=body)


def next_export(env: dict, recorder_id: str, token: str, slot: int) -> dict | None:
    stub = env.get("NEXCLIP_MODE2_STUB") or ""
    if stub and os.path.isfile(stub):
        with open(stub, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data is None:
            return None
        if isinstance(data, dict) and data.get("slot") not in (None, slot):
            return None
        return data if isinstance(data, dict) else None
    url = (
        f"{base_url(env)}{api_prefix(env)}/recorders/{recorder_id}"
        f"/inputs/{slot}/export-requests/next"
    )
    return http_json("GET", url, token=token)


def start_export_request(
    env: dict, recorder_id: str, token: str, slot: int, request_id: str
) -> str:
    stub = env.get("NEXCLIP_MODE2_STUB") or ""
    if stub:
        return "cap_stub_" + request_id[:8]
    url = (
        f"{base_url(env)}{api_prefix(env)}/recorders/{recorder_id}"
        f"/inputs/{slot}/export-requests/{request_id}/start"
    )
    data = http_json("POST", url, token=token, body={}) or {}
    cap = str(data.get("capture_id") or "")
    if not cap:
        raise RuntimeError(f"start export missing capture_id: {data}")
    return cap


def complete_capture(env: dict, recorder_id: str, token: str, capture_id: str, path: str) -> None:
    if env.get("NEXCLIP_MODE2_STUB"):
        return
    url = f"{base_url(env)}{api_prefix(env)}/recorders/{recorder_id}/captures/{capture_id}/complete"
    http_json("POST", url, token=token, body={"delivered_path": path})


def fail_capture(env: dict, recorder_id: str, token: str, capture_id: str, detail: str) -> None:
    if env.get("NEXCLIP_MODE2_STUB"):
        return
    url = f"{base_url(env)}{api_prefix(env)}/recorders/{recorder_id}/captures/{capture_id}/fail"
    http_json("POST", url, token=token, body={"error_detail": detail[:2000]})


def iso_from_hub(value: Any) -> str:
    if isinstance(value, str) and value:
        if value.endswith("Z") or "+" in value[10:]:
            return value.replace("+00:00", "Z") if value.endswith("+00:00") else value
        return value + ("Z" if "T" in value else "")
    return iso_z()


def enqueue_from_request(conn, env: dict, inp: dict, req: dict, capture_id: str) -> str | None:
    rid = str(req.get("export_request_id") or req.get("id") or "")
    if not rid:
        return None
    existing = fetchone(conn, "SELECT id FROM exports WHERE nexclip_schedule_id=?", (rid,))
    if existing:
        conn.execute(
            "UPDATE exports SET nexclip_capture_id=? WHERE id=? AND (nexclip_capture_id IS NULL OR nexclip_capture_id='')",
            (capture_id, existing["id"]),
        )
        conn.commit()
        return None
    t_in = iso_from_hub(req.get("range_start"))
    t_out = iso_from_hub(req.get("range_end"))
    days = env_int(env, "NEXREC_EXPORT_RETENTION_DAYS", 15)
    exp_id = new_id("exp")
    enqueue_export(
        conn,
        {
            "id": exp_id,
            "status": "queued",
            "input_ids": json.dumps([inp["id"]]),
            "t_in": t_in,
            "t_out": t_out,
            "quality": "full",
            "scope": "one",
            "path": None,
            "size_bytes": None,
            "protected": 1,
            "error": None,
            "created_by": "nexclip",
            "created_at": iso_z(),
            "expires_at": iso_z(utcnow() + timedelta(days=days)),
            "nexclip_schedule_id": rid,
            "nexclip_capture_id": capture_id,
        },
    )
    conn.execute(
        """
        INSERT INTO nexclip_events (id, input_id, title, start_at, end_at, payload, export_id, status, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)
        ON CONFLICT(id) DO UPDATE SET export_id=excluded.export_id, status='queued', fetched_at=excluded.fetched_at
        """,
        (
            rid,
            inp["id"],
            req.get("title"),
            t_in,
            t_out,
            json.dumps(req),
            exp_id,
            iso_z(),
        ),
    )
    conn.commit()
    return exp_id


def report_finished_jobs(conn, env: dict, recorder_id: str, token: str) -> int:
    rows = fetchall(
        conn,
        """
        SELECT * FROM exports
        WHERE nexclip_capture_id IS NOT NULL AND nexclip_capture_id != ''
          AND status IN ('done', 'error')
        """,
    )
    n = 0
    for job in rows:
        cap = job["nexclip_capture_id"]
        marker = f"nexclip_reported:{cap}"
        if get_setting(conn, marker):
            continue
        try:
            if job["status"] == "done" and job.get("path"):
                complete_capture(env, recorder_id, token, cap, job["path"])
            else:
                fail_capture(env, recorder_id, token, cap, job.get("error") or "export failed")
            set_setting(conn, marker, iso_z())
            n += 1
        except Exception as exc:  # noqa: BLE001
            print(f"nexclip report {cap}: {exc}", file=sys.stderr)
    return n


def run_once(conn, env: dict) -> dict[str, Any]:
    inputs = fetchall(conn, "SELECT * FROM inputs ORDER BY id")
    recorder_id, token = ensure_registered(conn, env, inputs)
    checkin(conn, env, recorder_id, token, inputs)
    enqueued = 0
    idle = 0
    for slot, inp in assigned_slots(inputs):
        req = next_export(env, recorder_id, token, slot)
        if not req:
            idle += 1
            continue
        request_id = str(req.get("export_request_id") or req.get("id") or "")
        if not request_id:
            continue
        if fetchone(conn, "SELECT id FROM exports WHERE nexclip_schedule_id=?", (request_id,)):
            continue
        capture_id = start_export_request(env, recorder_id, token, slot, request_id)
        if enqueue_from_request(conn, env, inp, req, capture_id):
            enqueued += 1
    reported = report_finished_jobs(conn, env, recorder_id, token)
    return {
        "recorder_id": recorder_id,
        "enqueued": enqueued,
        "idle_slots": idle,
        "reported": reported,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env", default="")
    args = p.parse_args(argv)
    env = load_env_file(args.env) if args.env else dict(os.environ)
    if not env_bool(env, "NEXREC_NEXCLIP_ENABLED", False) and not env.get("NEXCLIP_MODE2_STUB"):
        print("nexclip disabled")
        return 0
    paths = data_paths(env)
    conn = connect(paths["db"])
    migrate(conn)
    stats = run_once(conn, env)
    print(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
