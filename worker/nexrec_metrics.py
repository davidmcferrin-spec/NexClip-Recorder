"""Host and SDI samples. One row a minute. Never part of the record process."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import timedelta
from typing import Any

from nexrec_util import iso_z, parse_iso

KEEP_DAYS = 7
NVIDIA_QUERY = "utilization.gpu,utilization.encoder,utilization.decoder,memory.used,memory.total,name"


def parse_proc_stat(line: str) -> tuple[int, int]:
    parts = line.split()
    if len(parts) < 5 or parts[0] != "cpu":
        raise ValueError("not an aggregate cpu line")
    nums = [int(x) for x in parts[1:]]
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
    return idle, sum(nums)


def cpu_percent(first: tuple[int, int], second: tuple[int, int]) -> float | None:
    idle_a, total_a = first
    idle_b, total_b = second
    delta = total_b - total_a
    if delta <= 0:
        return None
    busy = delta - (idle_b - idle_a)
    return round(100.0 * busy / delta, 1)


def parse_meminfo(text: str) -> tuple[int, int] | None:
    total = avail = None
    for line in text.splitlines():
        bits = line.split()
        if len(bits) < 2:
            continue
        if bits[0] == "MemTotal:":
            total = int(bits[1]) * 1024
        elif bits[0] == "MemAvailable:":
            avail = int(bits[1]) * 1024
    if total is None or avail is None or total <= 0:
        return None
    return max(0, total - avail), total


def parse_nvidia_csv(text: str) -> dict[str, Any] | None:
    line = ""
    for raw in text.splitlines():
        raw = raw.strip()
        if raw and not raw.lower().startswith("utilization"):
            line = raw
            break
    if not line:
        return None
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 6:
        return None

    def num(value: str) -> float | None:
        if value in ("", "[N/A]", "N/A", "[Not Supported]"):
            return None
        return float(value)

    util = num(parts[0])
    enc = num(parts[1])
    dec = num(parts[2])
    used = num(parts[3])
    total = num(parts[4])
    return {
        "gpu_util_pct": util,
        "gpu_enc_pct": enc,
        "gpu_dec_pct": dec,
        "gpu_mem_used_mib": None if used is None else int(used),
        "gpu_mem_total_mib": None if total is None else int(total),
        "gpu_name": ",".join(parts[5:]).strip(),
    }


def empty_gpu() -> dict[str, Any]:
    return {
        "gpu_util_pct": None,
        "gpu_enc_pct": None,
        "gpu_dec_pct": None,
        "gpu_mem_used_mib": None,
        "gpu_mem_total_mib": None,
        "gpu_name": "",
    }


def read_cpu_percent() -> float | None:
    path = "/proc/stat"
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            first = parse_proc_stat(fh.readline())
        # A short gap is enough for a percent; the timer itself is one minute.
        import time

        time.sleep(0.2)
        with open(path, "r", encoding="utf-8") as fh:
            second = parse_proc_stat(fh.readline())
        return cpu_percent(first, second)
    except (OSError, ValueError):
        return None


def read_mem() -> tuple[int, int] | None:
    path = "/proc/meminfo"
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return parse_meminfo(fh.read())
    except (OSError, ValueError):
        return None


def read_disk(storage: str) -> tuple[int, int] | None:
    try:
        os.makedirs(storage, exist_ok=True)
        usage = shutil.disk_usage(storage)
    except OSError:
        return None
    return int(usage.used), int(usage.total)


def read_gpu() -> dict[str, Any]:
    binary = shutil.which("nvidia-smi")
    if not binary:
        return empty_gpu()
    try:
        proc = subprocess.run(
            [
                binary,
                f"--query-gpu={NVIDIA_QUERY}",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return empty_gpu()
    if proc.returncode != 0:
        return empty_gpu()
    parsed = parse_nvidia_csv(proc.stdout or "")
    return parsed if parsed is not None else empty_gpu()


def read_load1() -> float | None:
    try:
        return round(float(os.getloadavg()[0]), 2)
    except (OSError, AttributeError):
        return None


def collect_host(storage: str, sampled_at: str) -> dict[str, Any]:
    mem = read_mem()
    disk = read_disk(storage)
    gpu = read_gpu()
    row = {
        "sampled_at": sampled_at,
        "cpu_pct": read_cpu_percent(),
        "load1": read_load1(),
        "mem_used_bytes": None if mem is None else mem[0],
        "mem_total_bytes": None if mem is None else mem[1],
        "disk_used_bytes": None if disk is None else disk[0],
        "disk_total_bytes": None if disk is None else disk[1],
        "disk_path": storage,
    }
    row.update(gpu)
    return row


def sdi_rows(heartbeats: list[dict[str, Any]], sampled_at: str) -> list[dict[str, Any]]:
    out = []
    for beat in heartbeats:
        iid = str(beat.get("input_id") or "")
        if not iid:
            continue
        lock = beat.get("sdi_lock")
        if lock is not None and lock != "":
            try:
                lock = int(lock)
            except (TypeError, ValueError):
                lock = None
        else:
            lock = None
        out.append(
            {
                "sampled_at": sampled_at,
                "input_id": iid,
                "sdi_lock": lock,
                "signal": str(beat.get("signal") or ""),
                "format": str(beat.get("format") or ""),
            }
        )
    return out


def record_sample(conn: Any, host: dict[str, Any], locks: list[dict[str, Any]]) -> None:
    conn.execute(
        """
        INSERT INTO host_metrics (
          sampled_at, cpu_pct, load1, mem_used_bytes, mem_total_bytes,
          disk_used_bytes, disk_total_bytes, disk_path,
          gpu_util_pct, gpu_enc_pct, gpu_dec_pct,
          gpu_mem_used_mib, gpu_mem_total_mib, gpu_name
        ) VALUES (
          :sampled_at, :cpu_pct, :load1, :mem_used_bytes, :mem_total_bytes,
          :disk_used_bytes, :disk_total_bytes, :disk_path,
          :gpu_util_pct, :gpu_enc_pct, :gpu_dec_pct,
          :gpu_mem_used_mib, :gpu_mem_total_mib, :gpu_name
        )
        ON CONFLICT (sampled_at) DO UPDATE SET
          cpu_pct = EXCLUDED.cpu_pct,
          load1 = EXCLUDED.load1,
          mem_used_bytes = EXCLUDED.mem_used_bytes,
          mem_total_bytes = EXCLUDED.mem_total_bytes,
          disk_used_bytes = EXCLUDED.disk_used_bytes,
          disk_total_bytes = EXCLUDED.disk_total_bytes,
          disk_path = EXCLUDED.disk_path,
          gpu_util_pct = EXCLUDED.gpu_util_pct,
          gpu_enc_pct = EXCLUDED.gpu_enc_pct,
          gpu_dec_pct = EXCLUDED.gpu_dec_pct,
          gpu_mem_used_mib = EXCLUDED.gpu_mem_used_mib,
          gpu_mem_total_mib = EXCLUDED.gpu_mem_total_mib,
          gpu_name = EXCLUDED.gpu_name
        """,
        host,
    )
    for row in locks:
        conn.execute(
            """
            INSERT INTO sdi_lock_log (sampled_at, input_id, sdi_lock, signal, format)
            VALUES (:sampled_at, :input_id, :sdi_lock, :signal, :format)
            ON CONFLICT (sampled_at, input_id) DO UPDATE SET
              sdi_lock = EXCLUDED.sdi_lock,
              signal = EXCLUDED.signal,
              format = EXCLUDED.format
            """,
            row,
        )
    cutoff = iso_z(parse_iso(str(host["sampled_at"])) - timedelta(days=KEEP_DAYS))
    conn.execute("DELETE FROM host_metrics WHERE sampled_at < :c", {"c": cutoff})
    conn.execute("DELETE FROM sdi_lock_log WHERE sampled_at < :c", {"c": cutoff})
    conn.commit()
