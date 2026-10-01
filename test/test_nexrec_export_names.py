#!/usr/bin/env python3
"""Readable export filenames. Station time, camera name, short id."""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "worker"))

from nexrec_util import export_file_basenames  # noqa: E402


class TestExportNames(unittest.TestCase):
    def test_station_time_same_day(self):
        names = export_file_basenames(
            "exp_a8c98ff5ed73",
            [("cam", "Charlie")],
            "2026-10-01T18:51:22Z",
            "2026-10-01T19:01:22Z",
            "full",
            "America/New_York",
        )
        self.assertEqual(
            names["cam"],
            "Charlie_20261001_145122-150122_a8c98ff5ed73.mp4",
        )

    def test_midnight_includes_both_dates(self):
        names = export_file_basenames(
            "exp_a8c98ff5ed73",
            [("cam", "Charlie")],
            "2026-10-02T03:50:00Z",
            "2026-10-02T04:10:00Z",
            "full",
            "America/New_York",
        )
        self.assertEqual(
            names["cam"],
            "Charlie_20261001_235000-20261002_001000_a8c98ff5ed73.mp4",
        )

    def test_proxy_and_unsafe_camera_name(self):
        names = export_file_basenames(
            "exp_a8c98ff5ed73",
            [("cam", "Cam 1 / Studio")],
            "2026-10-01T18:51:22Z",
            "2026-10-01T19:01:22Z",
            "proxy",
            "America/New_York",
        )
        self.assertEqual(
            names["cam"],
            "Cam_1_Studio_20261001_145122-150122_proxy_a8c98ff5ed73.mp4",
        )

    def test_duplicate_slugs_keep_the_input_id(self):
        names = export_file_basenames(
            "exp_a8c98ff5ed73",
            [("cam", "Charlie"), ("studio", "Charlie")],
            "2026-10-01T18:51:22Z",
            "2026-10-01T19:01:22Z",
            "full",
            "America/New_York",
        )
        self.assertIn("_cam_", names["cam"])
        self.assertIn("_studio_", names["studio"])
        self.assertNotEqual(names["cam"], names["studio"])


if __name__ == "__main__":
    unittest.main()
