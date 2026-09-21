#!/usr/bin/env python3
"""NexClip Mode 2 export-request client."""

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


class TestNexclipMode2(unittest.TestCase):
    def test_type_map_ip_to_srt(self):
        self.assertEqual(nc.map_recorder_type({}, [{"source_type": "rtsp"}]), "srt")
        self.assertEqual(nc.map_recorder_type({}, [{"source_type": "decklink"}]), "decklink")
        self.assertEqual(nc.map_recorder_type({"NEXCLIP_RECORDER_TYPE": "ndi"}, []), "ndi")

    def test_slots_clamp_and_assign(self):
        self.assertEqual(nc.clamp_slots(2), 4)
        self.assertEqual(nc.clamp_slots(10), 8)
        rows = [
            {"id": "a", "enabled": 1, "nexclip_slot": 2, "name": "A"},
            {"id": "b", "enabled": 1, "nexclip_slot": None, "name": "B"},
            {"id": "c", "enabled": 0, "nexclip_slot": 3, "name": "C"},
        ]
        slots = nc.assigned_slots(rows)
        nums = [s for s, _ in slots]
        self.assertEqual(nums, [2])
        self.assertTrue(all(1 <= s <= 8 for s in nums))

    def test_client_has_no_mode1_schedule_path(self):
        src = open(os.path.join(os.path.dirname(HERE), "worker", "nexrec-nexclip.py"), encoding="utf-8").read()
        self.assertNotIn("/schedule", src)
        self.assertNotIn("safety_net", src)
        self.assertIn("export-requests/next", src)

    def test_stub_next_enqueues_once(self):
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
                "nexclip_slot": 1,
                "created_at": iso_z(now),
                "updated_at": iso_z(now),
            },
        )
        stub = os.path.join(tmp.name, "next.json")
        with open(stub, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "export_request_id": "er_1",
                    "slot": 1,
                    "title": "Show",
                    "range_start": iso_z(now - timedelta(hours=2)),
                    "range_end": iso_z(now - timedelta(hours=1)),
                    "relative_dir": "_EXPORT/a",
                    "filename": "SHOW.mp4",
                },
                fh,
            )
        env = {
            "NEXCLIP_MODE2_STUB": stub,
            "NEXREC_EXPORT_RETENTION_DAYS": "15",
        }
        stats = nc.run_once(conn, env)
        self.assertEqual(stats["enqueued"], 1)
        jobs = fetchall(conn, "SELECT * FROM exports")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["nexclip_schedule_id"], "er_1")
        self.assertTrue(jobs[0]["nexclip_capture_id"])
        stats2 = nc.run_once(conn, env)
        self.assertEqual(stats2["enqueued"], 0)

    def test_stub_null_is_idle(self):
        tmp = tempfile.NamedTemporaryFile("w", delete=False)
        tmp.write("null")
        tmp.close()
        env = {"NEXCLIP_MODE2_STUB": tmp.name}
        self.assertIsNone(nc.next_export(env, "id", "tok", 1))
        os.unlink(tmp.name)


if __name__ == "__main__":
    unittest.main()
