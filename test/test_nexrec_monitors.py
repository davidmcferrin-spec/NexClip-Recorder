#!/usr/bin/env python3
"""Confidence-monitor contract: preview decode only, record argv untouched."""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _node() -> str | None:
    env = os.environ.get("NODE")
    for cand in (env, shutil.which("node"), "/exec-daemon/node"):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


class TestMonitors(unittest.TestCase):
    def test_js_scopes_smoke(self):
        node = _node()
        if not node:
            self.skipTest("node not available")
        script = os.path.join(HERE, "test_nexrec_monitors.js")
        subprocess.check_call([node, script], cwd=ROOT)

    def test_record_and_preview_argv_unchanged_by_monitors(self):
        import sys

        sys.path.insert(0, os.path.join(ROOT, "worker"))
        from nexrec_ffmpeg import preview_argv, record_argv

        ip = {"source_type": "rtsp", "url": "rtsp://cam/stream"}
        rec = record_argv(ip, "/tmp/out_%Y%m%dT%H%M%SZ.mp4")
        prev = preview_argv(ip, "rtsp://127.0.0.1:8554/in0")
        deck = record_argv(
            {"source_type": "decklink", "decklink_device": "DeckLink Duo (1)"},
            "/tmp/sdi_%Y%m%dT%H%M%SZ.mp4",
            preview_rtsp="rtsp://127.0.0.1:8554/in0",
        )
        for argv in (rec, prev, deck):
            blob = " ".join(argv).lower()
            self.assertNotIn("waveform", blob)
            self.assertNotIn("vectorscope", blob)
            self.assertNotIn("ebur128", blob)
            acs = [argv[i + 1] for i, a in enumerate(argv) if a == "-ac"]
            self.assertTrue(acs)
            self.assertTrue(all(a == "2" for a in acs))
        joined = " ".join(deck)
        self.assertIn("split=2", joined)
        self.assertIn("asplit=2", joined)


if __name__ == "__main__":
    unittest.main()
