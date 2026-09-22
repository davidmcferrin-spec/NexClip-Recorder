#!/usr/bin/env python3
"""DeckLink argv helpers, status JSON, and preview-unit skip. No hardware."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "worker"))

from nexrec_decklink import (  # noqa: E402
    parse_list_devices,
    parse_list_formats,
    parse_status_json,
    resolve_decklink_spec,
)
from nexrec_ffmpeg import preview_unit_allowed  # noqa: E402
from nexrec_heartbeat import parse_status_text, should_probe_decklink  # noqa: E402

FIX = os.path.join(HERE, "fixtures")


class TestDecklink(unittest.TestCase):
    def test_status_json_busy_still_locked(self):
        raw = Path(FIX, "decklink-status.json").read_text(encoding="utf-8")
        parsed = parse_status_json(raw, "DeckLink Quad 2 (1)")
        self.assertEqual(parsed["signal"], "present")
        self.assertEqual(parsed["sdi_lock"], 1)
        self.assertEqual(parsed["format"], "1080i59.94")
        self.assertEqual(parsed["busy"], 1)
        self.assertEqual(parsed["probe"], "tool")
        self.assertIn("busy", parsed["detail"])
        by_index = parse_status_json(raw, "0")
        self.assertEqual(by_index["format"], "1080i59.94")

    def test_status_json_unlocked(self):
        raw = Path(FIX, "decklink-status.json").read_text(encoding="utf-8")
        parsed = parse_status_json(raw, "1")
        self.assertEqual(parsed["signal"], "no_signal")
        self.assertEqual(parsed["sdi_lock"], 0)
        self.assertEqual(parsed["format"], "")
        self.assertEqual(parsed["busy"], 0)

    def test_status_json_missing_device(self):
        raw = Path(FIX, "decklink-status.json").read_text(encoding="utf-8")
        parsed = parse_status_json(raw, "DeckLink Quad 2 (8)")
        self.assertEqual(parsed["signal"], "unknown")
        self.assertEqual(parsed["probe"], "tool")

    def test_status_json_no_api(self):
        parsed = parse_status_json('{"devices":[],"error":"no_decklink_api"}', "DeckLink Duo (1)")
        self.assertEqual(parsed["probe"], "unavailable")
        self.assertIn("drivers", parsed["detail"])

    def test_kv_still_parses(self):
        parsed = parse_status_text("signal=present\nlock=1\nformat=1080i59.94\n")
        self.assertEqual(parsed["signal"], "present")
        self.assertEqual(parsed["sdi_lock"], 1)

    def test_list_devices_and_resolve_index(self):
        text = Path(FIX, "decklink-list-devices.txt").read_text(encoding="utf-8")
        devices = parse_list_devices(text)
        self.assertEqual(devices[0]["name"], "DeckLink Quad 2 (1)")
        self.assertEqual(devices[1]["index"], 1)
        self.assertEqual(resolve_decklink_spec("1", devices), "DeckLink Duo (2)")
        self.assertEqual(resolve_decklink_spec("DeckLink Quad 2 (1)", devices), "DeckLink Quad 2 (1)")
        self.assertIsNone(resolve_decklink_spec("9", devices))

    def test_list_formats(self):
        text = (
            "[decklink @ 0x1] Supported formats for 'DeckLink Quad 2 (1)':\n"
            "[decklink @ 0x1]\t'ntsc'\t720x486 at 30000/1001 fps\n"
            "[decklink @ 0x1]\t'Hi59'\t1920x1080 at 30000/1001 fps (interlaced)\n"
        )
        self.assertEqual(parse_list_formats(text), ["ntsc", "Hi59"])

    def test_preview_unit_skipped(self):
        self.assertFalse(preview_unit_allowed({"source_type": "decklink"}))
        self.assertTrue(preview_unit_allowed({"source_type": "udp"}))

    def test_probe_waits_for_ffmpeg_open(self):
        self.assertFalse(should_probe_decklink(True, 0.2))
        self.assertTrue(should_probe_decklink(True, 4.0))
        self.assertTrue(should_probe_decklink(False, 0.0))

    def test_fixture_is_json(self):
        json.loads(Path(FIX, "decklink-status.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
