#!/usr/bin/env python3
"""Cleanup: expire unprotected exports, honor per-input retention, orphans, floor."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "worker"))

from nexrec_db import connect, enqueue_export, insert_chunk, migrate, upsert_input  # noqa: E402
from nexrec_util import iso_z, utcnow  # noqa: E402

import importlib.util

spec = importlib.util.spec_from_file_location(
    "nexrec_cleanup",
    os.path.join(os.path.dirname(HERE), "worker", "nexrec-cleanup.py"),
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TestCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = os.path.join(self.tmp.name, "storage")
        os.makedirs(os.path.join(self.storage, "exports"), exist_ok=True)
        os.makedirs(os.path.join(self.storage, "inputs", "cam", "native"), exist_ok=True)
        self.db = os.path.join(self.tmp.name, "nexrec.db")
        self.conn = connect(self.db)
        migrate(self.conn)
        now = utcnow()
        upsert_input(
            self.conn,
            {
                "id": "cam",
                "name": "cam",
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
                "retention_days": 1,
                "preview_path": "in0",
                "preview_enabled": 1,
                "created_at": iso_z(now),
                "updated_at": iso_z(now),
            },
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_expires_unprotected_export(self):
        path = os.path.join(self.storage, "exports", "old.mp4")
        open(path, "wb").write(b"x" * 16)
        now = utcnow()
        enqueue_export(
            self.conn,
            {
                "id": "exp_old",
                "status": "done",
                "input_ids": '["cam"]',
                "t_in": iso_z(now),
                "t_out": iso_z(now),
                "quality": "full",
                "scope": "one",
                "path": path,
                "size_bytes": 16,
                "protected": 0,
                "error": None,
                "created_by": "t",
                "created_at": iso_z(now - timedelta(days=20)),
                "expires_at": iso_z(now - timedelta(days=1)),
                "nexclip_schedule_id": None,
            },
        )
        env = {
            "NEXREC_DATA_DIR": self.tmp.name,
            "NEXREC_STORAGE_DIR": self.storage,
            "NEXREC_DB": self.db,
            "NEXREC_FREE_SPACE_FLOOR": "0",
        }
        stats = mod.run(env)
        self.assertGreaterEqual(stats["exports_expired"], 1)
        self.assertFalse(os.path.isfile(path))

    def test_keeps_protected_export(self):
        path = os.path.join(self.storage, "exports", "keep.mp4")
        open(path, "wb").write(b"y" * 16)
        now = utcnow()
        enqueue_export(
            self.conn,
            {
                "id": "exp_keep",
                "status": "done",
                "input_ids": '["cam"]',
                "t_in": iso_z(now),
                "t_out": iso_z(now),
                "quality": "full",
                "scope": "one",
                "path": path,
                "size_bytes": 16,
                "protected": 1,
                "error": None,
                "created_by": "t",
                "created_at": iso_z(now - timedelta(days=20)),
                "expires_at": iso_z(now - timedelta(days=1)),
                "nexclip_schedule_id": None,
            },
        )
        env = {
            "NEXREC_DATA_DIR": self.tmp.name,
            "NEXREC_STORAGE_DIR": self.storage,
            "NEXREC_DB": self.db,
            "NEXREC_FREE_SPACE_FLOOR": "0",
        }
        mod.run(env)
        self.assertTrue(os.path.isfile(path))

    def test_retention_deletes_old_chunks(self):
        path = os.path.join(self.storage, "inputs", "cam", "native", "cam_old.mp4")
        open(path, "wb").write(b"z" * 16)
        now = utcnow()
        insert_chunk(
            self.conn,
            {
                "id": "chk_old",
                "input_id": "cam",
                "path": path,
                "kind": "native",
                "start_at": iso_z(now - timedelta(days=10)),
                "end_at": iso_z(now - timedelta(days=10, seconds=-300)),
                "duration_s": 300,
                "size_bytes": 16,
                "width": 1280,
                "height": 720,
                "fps": 30,
                "interlaced": 0,
                "codec": "h264",
                "timecode_start": None,
                "ready": 1,
                "orphan": 0,
                "created_at": iso_z(now),
            },
        )
        env = {
            "NEXREC_DATA_DIR": self.tmp.name,
            "NEXREC_STORAGE_DIR": self.storage,
            "NEXREC_DB": self.db,
            "NEXREC_FREE_SPACE_FLOOR": "0",
        }
        stats = mod.run(env)
        self.assertGreaterEqual(stats["chunks_expired"], 1)
        self.assertFalse(os.path.isfile(path))


if __name__ == "__main__":
    unittest.main()
