"""Host metric parsers. No database and no /proc required."""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "worker"))

from nexrec_metrics import (  # noqa: E402
    cpu_percent,
    parse_meminfo,
    parse_nvidia_csv,
    parse_proc_stat,
    sdi_rows,
)


class MetricsParseTest(unittest.TestCase):
    def test_cpu_percent_ignores_idle(self) -> None:
        first = parse_proc_stat("cpu 100 0 100 800 0 0 0 0")
        second = parse_proc_stat("cpu 150 0 150 850 0 0 0 0")
        self.assertEqual(cpu_percent(first, second), 66.7)

    def test_meminfo_uses_available(self) -> None:
        used, total = parse_meminfo("MemTotal: 1000 kB\nMemAvailable: 250 kB\n")
        self.assertEqual(total, 1000 * 1024)
        self.assertEqual(used, 750 * 1024)

    def test_nvidia_csv_first_gpu(self) -> None:
        row = parse_nvidia_csv("12, 40, 0, 512, 8192, NVIDIA GeForce RTX 3090\n")
        assert row is not None
        self.assertEqual(row["gpu_util_pct"], 12.0)
        self.assertEqual(row["gpu_enc_pct"], 40.0)
        self.assertEqual(row["gpu_mem_used_mib"], 512)
        self.assertEqual(row["gpu_name"], "NVIDIA GeForce RTX 3090")

    def test_nvidia_na_is_empty(self) -> None:
        self.assertIsNone(parse_nvidia_csv(""))
        row = parse_nvidia_csv("[N/A], [N/A], [N/A], [N/A], [N/A], \n")
        assert row is not None
        self.assertIsNone(row["gpu_util_pct"])

    def test_sdi_rows_keep_lock(self) -> None:
        rows = sdi_rows(
            [{"input_id": "in1", "sdi_lock": 1, "signal": "present", "format": "1080p60"}],
            "2026-09-29T00:00:00Z",
        )
        self.assertEqual(rows[0]["sdi_lock"], 1)
        self.assertEqual(rows[0]["input_id"], "in1")


if __name__ == "__main__":
    unittest.main()
