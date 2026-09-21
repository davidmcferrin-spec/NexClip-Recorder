#!/usr/bin/env python3
"""NexClip schedule stub → export enqueue."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "worker"))

from nexrec_db import connect, fetchall, migrate, upsert_input  # noqa: E402
from nexrec_util import iso_z, utcnow  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "nexrec_nexclip",
    os.path.join(os.path.dirname(HERE), "worker", "nexrec-nexclip.py"),
)
nc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nc)


class TestNexclip(unittest.TestCase):
    def test_ended_window_enqueues(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = os.path.join(tmp.name, "nexrec.db")
        conn = connect(db)
        migrate(conn)
        now = utcnow()
        upsert_input(
            conn,
            {
                "id": "studio-a",
                "name": "A",
                "source_type": "rtsp",
                "url": "",
                "decklink_device": "",
                "decklink_format": "",
                "enabled": 1,
                "live_transcode": 0,
                "copy_native": 1,
                "upconvert_1080i": 0,
                "video_bitrate": None,
                "audio_bitrate": None,
                "retention_days": 28,
                "preview_path": "in0",
                "preview_enabled": 1,
                "created_at": iso_z(now),
                "updated_at": iso_z(now),
            },
        )
        ev = {
            "id": "sched_1",
            "input_id": "studio-a",
            "title": "Show",
            "start_at": iso_z(now - timedelta(hours=2)),
            "end_at": iso_z(now - timedelta(hours=1)),
            "protect_export": True,
            "quality": "full",
        }
        env = {"NEXREC_EXPORT_RETENTION_DAYS": "15"}
        n = nc.ingest_events(conn, env, [ev])
        self.assertEqual(n, 1)
        jobs = fetchall(conn, "SELECT * FROM exports")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["nexclip_schedule_id"], "sched_1")
        self.assertEqual(jobs[0]["protected"], 1)
        # Idempotent
        n2 = nc.ingest_events(conn, env, [ev])
        self.assertEqual(n2, 0)

    def test_stub_file(self):
        tmp = tempfile.NamedTemporaryFile("w", delete=False)
        json.dump({"events": []}, tmp)
        tmp.close()
        env = {"NEXCLIP_SCHEDULE_STUB": tmp.name}
        self.assertEqual(nc.fetch_schedule(env), [])
        os.unlink(tmp.name)


if __name__ == "__main__":
    unittest.main()
