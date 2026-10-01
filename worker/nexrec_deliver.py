#!/usr/bin/env python3
"""Send a finished export to one or more rclone destinations.

A delivery stays waiting until the export row is done. A re-run parks those
rows again and the next successful encode queues the same remote path, which
rclone sync --ignore-times replaces.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
from typing import Any, Callable

from nexrec_secret import decrypt_secret, dest_key
from nexrec_util import iso_z


def _fetchone(conn, sql: str, args: tuple = ()):
    from nexrec_db import fetchone

    return fetchone(conn, sql, args)


def _fetchall(conn, sql: str, args: tuple = ()):
    from nexrec_db import fetchall

    return fetchall(conn, sql, args)

_EXPORT_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_INPUT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SAFE_SEG = re.compile(r"^[A-Za-z0-9._@-]{1,200}$")


def _extra(dest: dict[str, Any]) -> dict[str, Any]:
    raw = dest.get("extra") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _prefix_parts(prefix: str) -> list[str]:
    parts: list[str] = []
    for part in str(prefix or "").replace("\\", "/").split("/"):
        part = part.strip()
        if part in ("", "."):
            continue
        if part == ".." or not _SAFE_SEG.match(part):
            raise RuntimeError("remote prefix is not allowed")
        parts.append(part)
    return parts


def export_media_files(job: dict[str, Any]) -> list[tuple[str, str]]:
    """Basename and absolute path for each MP4 this export produced."""
    eid = str(job.get("id") or "")
    if not _EXPORT_ID.match(eid):
        raise RuntimeError("bad export id")
    raw = job.get("input_ids") or "[]"
    if isinstance(raw, str):
        ids = json.loads(raw)
    else:
        ids = list(raw)
    if not isinstance(ids, list):
        ids = []
    stored = str(job.get("path") or "")
    directory = os.path.dirname(stored) if stored else ""
    if len(ids) <= 1:
        path = stored or (os.path.join(directory, eid + ".mp4") if directory else "")
        if path == "":
            raise RuntimeError("export file missing")
        return [(os.path.basename(path), path)]
    out: list[tuple[str, str]] = []
    for iid in ids:
        iid_s = str(iid)
        if not _INPUT_ID.match(iid_s):
            continue
        base = f"{eid}_{iid_s}.mp4"
        out.append((base, os.path.join(directory, base)))
    if not out:
        raise RuntimeError("no export files")
    return out


def remote_target(dest: dict[str, Any], export_id: str) -> tuple[str, str]:
    """rclone remote and the path shown in the queue. Both end at the export id."""
    if not _EXPORT_ID.match(export_id):
        raise RuntimeError("bad export id")
    proto = str(dest.get("protocol") or "")
    extra = _extra(dest)
    bits: list[str] = []
    if proto == "s3":
        bucket = str(extra.get("bucket") or "")
        if not _SAFE_SEG.match(bucket):
            raise RuntimeError("s3 bucket is not allowed")
        bits.append(bucket)
    elif proto == "smb":
        share = str(extra.get("share") or "")
        if not _SAFE_SEG.match(share):
            raise RuntimeError("smb share is not allowed")
        bits.append(share)
    elif proto not in ("sftp", "ftp"):
        raise RuntimeError("unknown protocol")
    bits.extend(_prefix_parts(str(dest.get("remote_prefix") or "")))
    bits.append(export_id)
    remote = "dest:" + "/".join(bits)
    host = str(dest.get("host") or "")
    tail = "/".join(bits) + "/"
    if proto == "s3":
        shown = "s3://" + tail
    elif host:
        shown = f"{proto}://{host}/{tail}"
    else:
        shown = f"{proto}://{tail}"
    return remote, shown


def ini_value(value: str) -> str:
    if value == "":
        return '""'
    if any(ch in value for ch in ' #"\'\\'):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return '"' + escaped + '"'
    return value


def config_text(dest: dict[str, Any], obscured: str, key_obscured: str = "") -> str:
    """rclone config. The password is the obscured form, never the plaintext."""
    proto = str(dest.get("protocol") or "")
    extra = _extra(dest)
    lines = ["[dest]", f"type = {proto}"]
    host = str(dest.get("host") or "")
    user = str(dest.get("username") or "")
    port = dest.get("port")
    if proto == "sftp":
        lines.append(f"host = {ini_value(host)}")
        if user:
            lines.append(f"user = {ini_value(user)}")
        if port:
            lines.append(f"port = {int(port)}")
        if obscured:
            lines.append(f"pass = {ini_value(obscured)}")
        key_path = str(extra.get("key_path") or "")
        if key_path:
            lines.append(f"key_file = {ini_value(key_path)}")
        if key_obscured:
            lines.append(f"key_file_pass = {ini_value(key_obscured)}")
    elif proto == "ftp":
        lines.append(f"host = {ini_value(host)}")
        if user:
            lines.append(f"user = {ini_value(user)}")
        if port:
            lines.append(f"port = {int(port)}")
        if obscured:
            lines.append(f"pass = {ini_value(obscured)}")
        if int(extra.get("explicit_tls") or 0) == 1:
            lines.append("explicit_tls = true")
    elif proto == "s3":
        provider = "Other" if host else "AWS"
        lines.append(f"provider = {provider}")
        if user:
            lines.append(f"access_key_id = {ini_value(user)}")
        if obscured:
            lines.append(f"secret_access_key = {ini_value(obscured)}")
        region = str(extra.get("region") or "us-east-1")
        lines.append(f"region = {ini_value(region)}")
        if host:
            lines.append(f"endpoint = {ini_value(host)}")
        if int(extra.get("path_style") or 0) == 1 or host:
            lines.append("force_path_style = true")
    elif proto == "smb":
        lines.append(f"host = {ini_value(host)}")
        domain = str(extra.get("domain") or "")
        smb_user = (domain + "\\" + user) if domain and user else user
        if smb_user:
            lines.append(f"user = {ini_value(smb_user)}")
        if obscured:
            lines.append(f"pass = {ini_value(obscured)}")
        if port:
            lines.append(f"port = {int(port)}")
    else:
        raise RuntimeError("unknown protocol")
    return "\n".join(lines) + "\n"


def sync_argv(rclone: str, config: str, source: str, remote: str, files_from: str | None) -> list[str]:
    cmd = [
        rclone,
        "sync",
        source,
        remote,
        "--config",
        config,
        "--ignore-times",
        "--transfers",
        "1",
        "--retries",
        "2",
        "--stats",
        "2s",
        "--stats-one-line",
    ]
    if files_from:
        cmd.extend(["--files-from", files_from])
    return cmd


def rclone_bin(env: dict[str, str]) -> str:
    return str(env.get("NEXREC_RCLONE") or "rclone")


def obscure_password(rclone: str, password: str) -> str:
    """rclone obscure reads stdin so the password is not on the process list."""
    proc = subprocess.run(
        [rclone, "obscure", "-"],
        input=password.encode("utf-8"),
        capture_output=True,
        check=False,
        timeout=20,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()[:300]
        raise RuntimeError(err or "rclone obscure failed")
    token = proc.stdout.decode("utf-8", "replace").strip()
    if token == "" or "\n" in token:
        raise RuntimeError("rclone obscure returned nothing")
    return token


def prepare_source(files: list[tuple[str, str]], stage: str) -> tuple[str, str | None]:
    """A directory that contains only this export, or a files-from list."""
    os.makedirs(stage, 0o700, exist_ok=True)
    linked = True
    for base, src in files:
        if not _SAFE_SEG.match(base) or not base.endswith(".mp4"):
            raise RuntimeError("bad export filename")
        if not os.path.isfile(src):
            raise RuntimeError(f"export file missing: {base}")
        dest = os.path.join(stage, base)
        try:
            if os.path.lexists(dest):
                os.unlink(dest)
            os.link(src, dest)
        except OSError:
            linked = False
            break
    if linked:
        return stage, None
    for base, _src in files:
        path = os.path.join(stage, base)
        if os.path.lexists(path):
            os.unlink(path)
    directories = {os.path.dirname(src) for _base, src in files}
    if len(directories) != 1:
        raise RuntimeError("export files are not in one directory")
    list_path = stage + ".txt"
    with open(list_path, "w", encoding="utf-8") as fh:
        for base, _src in files:
            fh.write(base + "\n")
    return next(iter(directories)), list_path


def promote_deliveries(conn, export_id: str) -> int:
    """Move waiting rows onto the copy queue. Only call this after status=done."""
    cur = conn.execute(
        """UPDATE deliveries
           SET status='queued', queued_at=?, cancel_requested=0, error=NULL,
               started_at=NULL, finished_at=NULL, progress_pct=NULL
           WHERE export_id=? AND status='waiting'""",
        (iso_z(), export_id),
    )
    conn.commit()
    return int(cur.rowcount or 0)


def sweep_ready(conn) -> int:
    """Queue copies whose export finished if the export worker did not."""
    rows = _fetchall(
        conn,
        """SELECT d.export_id
           FROM deliveries d
           JOIN exports e ON e.id = d.export_id
           WHERE d.status='waiting' AND e.status='done'""",
    )
    n = 0
    seen: set[str] = set()
    for row in rows:
        eid = str(row["export_id"])
        if eid in seen:
            continue
        seen.add(eid)
        n += promote_deliveries(conn, eid)
    return n


def export_transfer_busy(conn, export_id: str) -> bool:
    row = _fetchone(
        conn,
        "SELECT id FROM deliveries WHERE export_id=? AND status='running' LIMIT 1",
        (export_id,),
    )
    return row is not None


def recover_running(conn) -> None:
    """A dead worker left rows running. Resume only when the export is still done."""
    rows = _fetchall(conn, "SELECT id, export_id FROM deliveries WHERE status='running'")
    now = iso_z()
    for row in rows:
        export = _fetchone(conn, "SELECT status FROM exports WHERE id=?", (row["export_id"],))
        status = "" if export is None else str(export.get("status") or "")
        if status == "done":
            conn.execute(
                """UPDATE deliveries
                   SET status='queued', queued_at=?, cancel_requested=0, started_at=NULL, error=NULL
                   WHERE id=? AND status='running'""",
                (now, row["id"]),
            )
        else:
            conn.execute(
                """UPDATE deliveries
                   SET status='waiting', cancel_requested=0, started_at=NULL, finished_at=NULL, error=NULL
                   WHERE id=? AND status='running'""",
                (row["id"],),
            )
    conn.commit()


def release_export(conn, export_id: str) -> bool:
    """Drop delivery rows when nothing is copying. False keeps the export on disk."""
    busy = _fetchone(
        conn,
        "SELECT id FROM deliveries WHERE export_id=? AND status IN ('queued','running') LIMIT 1",
        (export_id,),
    )
    if busy is not None:
        return False
    conn.execute("DELETE FROM deliveries WHERE export_id=?", (export_id,))
    return True


def claim_next(conn) -> dict[str, Any] | None:
    row = _fetchone(
        conn,
        """SELECT * FROM deliveries
           WHERE status='queued' AND cancel_requested=0
           ORDER BY queued_at ASC NULLS LAST, created_at ASC
           LIMIT 1""",
    )
    if row is None:
        return None
    now = iso_z()
    cur = conn.execute(
        """UPDATE deliveries
           SET status='running', started_at=?, progress_pct=0, error=NULL, finished_at=NULL
           WHERE id=? AND status='queued' AND cancel_requested=0""",
        (now, row["id"]),
    )
    conn.commit()
    if int(cur.rowcount or 0) == 0:
        return None
    fresh = _fetchone(conn, "SELECT * FROM deliveries WHERE id=?", (row["id"],))
    return fresh


def _stop_reason(conn, delivery_id: str, export_id: str) -> str | None:
    delivery = _fetchone(conn, "SELECT status, cancel_requested FROM deliveries WHERE id=?", (delivery_id,))
    export = _fetchone(conn, "SELECT status FROM exports WHERE id=?", (export_id,))
    if delivery is None:
        return "missing"
    if int(delivery.get("cancel_requested") or 0) == 1:
        return "cancel"
    if str(delivery.get("status") or "") != "running":
        return "status"
    if export is None or str(export.get("status") or "") != "done":
        return "export"
    return None


def _park_after_stop(conn, delivery_id: str, export_id: str) -> None:
    export = _fetchone(conn, "SELECT status FROM exports WHERE id=?", (export_id,))
    status = "" if export is None else str(export.get("status") or "")
    if status in ("queued", "running", ""):
        conn.execute(
            """UPDATE deliveries
               SET status='waiting', cancel_requested=0, started_at=NULL, finished_at=NULL,
                   progress_pct=NULL, error=NULL
               WHERE id=? AND status='running'""",
            (delivery_id,),
        )
    else:
        conn.execute(
            """UPDATE deliveries
               SET status='cancelled', cancel_requested=0, finished_at=?, error=NULL
               WHERE id=? AND status='running'""",
            (iso_z(), delivery_id),
        )
    conn.commit()


def _redact(text: str, *secrets: str) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text[-2000:]


def _parse_pct(line: str) -> float | None:
    match = re.search(r"(\d{1,3})%", line)
    if not match:
        return None
    pct = float(match.group(1))
    if pct > 100:
        return None
    return pct


def _kill_rclone(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def run_rclone(conn, delivery_id: str, export_id: str, cmd: list[str], secret: str, key_secret: str = "") -> None:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    tail: list[str] = []
    while True:
        reason = _stop_reason(conn, delivery_id, export_id)
        if reason is not None:
            _kill_rclone(proc)
            _park_after_stop(conn, delivery_id, export_id)
            return
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue
        if line is None:
            break
        tail.append(line.rstrip())
        tail = tail[-20:]
        pct = _parse_pct(line)
        if pct is not None:
            conn.execute(
                "UPDATE deliveries SET progress_pct=? WHERE id=? AND status='running'",
                (pct, delivery_id),
            )
            conn.commit()
    code = proc.wait()
    if _stop_reason(conn, delivery_id, export_id) is not None:
        _park_after_stop(conn, delivery_id, export_id)
        return
    if code != 0:
        err = _redact("\n".join(tail), secret, key_secret) or f"rclone exited {code}"
        conn.execute(
            """UPDATE deliveries SET status='error', finished_at=?, error=?
               WHERE id=? AND status='running' AND cancel_requested=0""",
            (iso_z(), err, delivery_id),
        )
        conn.commit()
        print(f"deliver {delivery_id} error", file=sys.stderr, flush=True)
        return
    conn.execute(
        """UPDATE deliveries SET status='done', progress_pct=100, finished_at=?, error=NULL
           WHERE id=? AND status='running' AND cancel_requested=0""",
        (iso_z(), delivery_id),
    )
    conn.commit()
    print(f"deliver {delivery_id} done", flush=True)


def write_config(directory: str, text: str) -> str:
    fd, path = tempfile.mkstemp(prefix="nexrec-rclone-", suffix=".conf", dir=directory)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def perform_delivery(
    conn,
    env: dict[str, str],
    delivery: dict[str, Any],
    *,
    obscure: Callable[[str, str], str] | None = None,
) -> None:
    dest = _fetchone(conn, "SELECT * FROM destinations WHERE id=?", (delivery["destination_id"],))
    job = _fetchone(conn, "SELECT * FROM exports WHERE id=?", (delivery["export_id"],))
    if dest is None or int(dest.get("enabled") or 0) != 1:
        raise RuntimeError("destination is not available")
    if job is None or str(job.get("status") or "") != "done":
        _park_after_stop(conn, str(delivery["id"]), str(delivery["export_id"]))
        return
    cipher = str(dest.get("secret_cipher") or "")
    secret = decrypt_secret(cipher, dest_key(env)) if cipher else ""
    key_cipher = str(_extra(dest).get("key_pass_cipher") or "")
    key_secret = decrypt_secret(key_cipher, dest_key(env)) if key_cipher else ""
    files = export_media_files(job)
    remote, shown = remote_target(dest, str(job["id"]))
    conn.execute(
        "UPDATE deliveries SET remote_path=? WHERE id=?",
        (shown, delivery["id"]),
    )
    conn.commit()
    scratch = env.get("NEXREC_SCRATCH_DIR") or os.path.join(
        env.get("NEXREC_STORAGE_DIR") or os.path.join(env.get("NEXREC_DATA_DIR") or ".", "storage"),
        "tmp",
    )
    os.makedirs(scratch, 0o700, exist_ok=True)
    stage = tempfile.mkdtemp(prefix=f"deliver-{delivery['id']}-", dir=scratch)
    config = ""
    files_from = None
    try:
        if _stop_reason(conn, str(delivery["id"]), str(job["id"])) is not None:
            _park_after_stop(conn, str(delivery["id"]), str(job["id"]))
            return
        source, files_from = prepare_source(files, stage)
        rclone = rclone_bin(env)
        obscurer = obscure or obscure_password
        obscured = obscurer(rclone, secret) if secret else ""
        key_obscured = obscurer(rclone, key_secret) if key_secret else ""
        if secret and len(secret) >= 8 and secret in obscured:
            raise RuntimeError("refusing to store a plaintext password in the rclone config")
        if key_secret and len(key_secret) >= 8 and key_secret in key_obscured:
            raise RuntimeError("refusing to store a plaintext key passphrase in the rclone config")
        config = write_config(scratch, config_text(dest, obscured, key_obscured))
        cmd = sync_argv(rclone, config, source, remote, files_from)
        print(f"deliver {delivery['id']} {shown}", flush=True)
        run_rclone(conn, str(delivery["id"]), str(job["id"]), cmd, secret, key_secret)
    finally:
        if config and os.path.isfile(config):
            try:
                os.remove(config)
            except OSError:
                pass
        if files_from and os.path.isfile(files_from):
            try:
                os.remove(files_from)
            except OSError:
                pass
        if os.path.isdir(stage):
            for name in os.listdir(stage):
                try:
                    os.unlink(os.path.join(stage, name))
                except OSError:
                    pass
            try:
                os.rmdir(stage)
            except OSError:
                pass


def process_one(conn, env: dict[str, str]) -> bool:
    sweep_ready(conn)
    delivery = claim_next(conn)
    if delivery is None:
        return False
    try:
        perform_delivery(conn, env, delivery)
    except Exception as exc:  # noqa: BLE001 — the row must record the failure
        current = _fetchone(conn, "SELECT status FROM deliveries WHERE id=?", (delivery["id"],))
        if current is not None and str(current.get("status") or "") == "running":
            conn.execute(
                """UPDATE deliveries SET status='error', finished_at=?, error=?
                   WHERE id=? AND status='running'""",
                (iso_z(), str(exc)[:2000], delivery["id"]),
            )
            conn.commit()
        print(f"deliver {delivery['id']} error: {exc}", file=sys.stderr, flush=True)
    return True
