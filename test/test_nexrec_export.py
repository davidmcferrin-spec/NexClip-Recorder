#!/usr/bin/env python3
"""Export concat list + in/out offsets from chunk index."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "worker"))

from nexrec_db import chunks_overlapping, connect, insert_chunk, migrate, upsert_input  # noqa: E402
from nexrec_util import iso_z, utcnow  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "nexrec_export",
    os.path.join(os.path.dirname(HERE), "worker", "nexrec-export.py"),
)
exp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exp)


class TestExport(unittest.TestCase):
    def test_overlapping_and_offsets(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = os.path.join(tmp.name, "nexrec.db")
        conn = connect(db)
        migrate(conn)
        now = utcnow().replace(minute=0, second=0, microsecond=0)
        upsert_input(
            conn,
            {
                "id": "a",
                "name": "a",
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
        t0 = now
        for i in range(3):
            start = t0 + timedelta(minutes=5 * i)
            end = start + timedelta(minutes=5)
            path = os.path.join(tmp.name, f"a_{i}.mp4")
            open(path, "wb").write(b"0")
            insert_chunk(
                conn,
                {
                    "id": f"chk{i}",
                    "input_id": "a",
                    "path": path,
                    "kind": "native",
                    "start_at": iso_z(start),
                    "end_at": iso_z(end),
                    "duration_s": 300,
                    "size_bytes": 1,
                    "width": 1920,
                    "height": 1080,
                    "fps": 30,
                    "interlaced": 1,
                    "codec": "h264",
                    "timecode_start": "15:00:00:00",
                    "ready": 1,
                    "orphan": 0,
                    "created_at": iso_z(now),
                },
            )
        tin = iso_z(t0 + timedelta(minutes=7))
        tout = iso_z(t0 + timedelta(minutes=12))
        rows = chunks_overlapping(conn, "a", tin, tout)
        self.assertEqual(len(rows), 2)
        ss, dur = exp.trim_offsets(rows, tin, tout)
        self.assertAlmostEqual(ss, 120.0, places=1)  # 2 min into first overlapping chunk (which starts at +5 min)
        self.assertAlmostEqual(dur, 300.0, places=1)

    def test_concat_escaping(self):
        tmp = tempfile.NamedTemporaryFile("w+", delete=False)
        exp.write_concat(["/tmp/it's.mp4"], tmp.name)
        tmp.close()
        text = open(tmp.name, encoding="utf-8").read()
        os.unlink(tmp.name)
        self.assertIn("file '", text)


if __name__ == "__main__":
    unittest.main()
