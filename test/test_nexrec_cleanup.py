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

    def test_purge_when_used_percent_reaches_cap(self) -> None:
        self.assertFalse(mod.needs_space_purge(800, 1000, 0, 90))
        self.assertTrue(mod.needs_space_purge(100, 1000, 0, 90))
        self.assertTrue(mod.needs_space_purge(100, 1000, 0, 90.0))
        self.assertTrue(mod.needs_space_purge(40, 1000, 50, 0))
        self.assertFalse(mod.needs_space_purge(50, 1000, 50, 0))
        self.assertFalse(mod.needs_space_purge(800, 1000, 0, 0))

    def test_unset_max_percent_does_not_purge(self) -> None:
        path = os.path.join(self.storage, "inputs", "cam", "native", "cam_keep.mp4")
        open(path, "wb").write(b"k" * 16)
        now = utcnow()
        insert_chunk(
            self.conn,
            {
                "id": "chk_keep",
                "input_id": "cam",
                "path": path,
                "kind": "native",
                "start_at": iso_z(now),
                "end_at": iso_z(now + timedelta(seconds=300)),
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
        self.assertEqual(stats["max_used_percent"], 0)
        self.assertEqual(stats["freed_for_floor"], 0)
        self.assertTrue(os.path.isfile(path))

    def _chunk(self, cid: str, name: str, start, ready: int = 1) -> str:
        path = os.path.join(self.storage, "inputs", "cam", "native", name)
        with open(path, "wb") as fh:
            fh.write(b"c" * 32)
        now = utcnow()
        insert_chunk(
            self.conn,
            {
                "id": cid,
                "input_id": "cam",
                "path": path,
                "kind": "native",
                "start_at": iso_z(start),
                "end_at": iso_z(start + timedelta(seconds=300)),
                "duration_s": 300,
                "size_bytes": 32,
                "width": 1280,
                "height": 720,
                "fps": 30,
                "interlaced": 0,
                "codec": "h264",
                "timecode_start": None,
                "ready": ready,
                "orphan": 0,
                "created_at": iso_z(now),
            },
        )
        return path

    def _export(self, eid: str, name: str, protected: int = 0) -> str:
        path = os.path.join(self.storage, "exports", name)
        with open(path, "wb") as fh:
            fh.write(b"e" * 32)
        now = utcnow()
        enqueue_export(
            self.conn,
            {
                "id": eid,
                "status": "done",
                "input_ids": '["cam"]',
                "t_in": iso_z(now),
                "t_out": iso_z(now),
                "quality": "full",
                "scope": "one",
                "path": path,
                "size_bytes": 32,
                "protected": protected,
                "error": None,
                "created_by": "t",
                "created_at": iso_z(now - timedelta(days=30)),
                "expires_at": iso_z(now + timedelta(days=10)),
                "nexclip_schedule_id": None,
            },
        )
        return path

    def _with_space(self, readings, fn):
        pending = list(readings)

        def fake(_path):
            if pending:
                return pending.pop(0)
            return readings[-1]

        orig = mod.fs_space
        mod.fs_space = fake
        try:
            return fn()
        finally:
            mod.fs_space = orig

    def test_hard_limit_drops_oldest_clip_then_stops(self):
        now = utcnow()
        older = self._chunk("chk_older", "older.mp4", now - timedelta(hours=5))
        newer = self._chunk("chk_newer", "newer.mp4", now - timedelta(hours=1))
        export = self._export("exp_keep_disk", "fresh.mp4")
        # 95% used, then 85% after one clip. Cap is 90%.
        n = self._with_space([(50, 1000), (150, 1000)], lambda: mod.free_space_pass(self.conn, self.storage, 0, 90))
        self.assertEqual(n, 1)
        self.assertFalse(os.path.isfile(older))
        self.assertTrue(os.path.isfile(newer))
        self.assertTrue(os.path.isfile(export))

    def test_hard_limit_skips_open_clip_then_export(self):
        now = utcnow()
        open_clip = self._chunk("chk_open", "open.mp4", now - timedelta(hours=2), ready=0)
        export = self._export("exp_old_disk", "old-export.mp4")
        n = self._with_space([(40, 1000), (200, 1000)], lambda: mod.free_space_pass(self.conn, self.storage, 0, 90))
        self.assertEqual(n, 1)
        self.assertTrue(os.path.isfile(open_clip))
        self.assertFalse(os.path.isfile(export))

    def test_hard_limit_keeps_protected_export(self):
        export = self._export("exp_prot_disk", "protected.mp4", protected=1)
        n = self._with_space([(40, 1000), (40, 1000)], lambda: mod.free_space_pass(self.conn, self.storage, 0, 90))
        self.assertEqual(n, 0)
        self.assertTrue(os.path.isfile(export))


class TestThumbSidecars(unittest.TestCase):
    def test_sidecar_names_point_at_the_recording(self):
        base = "cam_20261008T150000Z.mp4"
        self.assertEqual(mod.recording_for_sidecar(base + ".jpg"), base)
        self.assertEqual(mod.recording_for_sidecar(base + ".jpg.n"), base)
        self.assertEqual(mod.recording_for_sidecar(base + ".jpg.tmp.jpg"), base)
        self.assertEqual(mod.recording_for_sidecar(base + ".jpg.n.tmp"), base)
        self.assertIsNone(mod.recording_for_sidecar(base))
        self.assertIsNone(mod.recording_for_sidecar("notes.txt"))

    def test_unlink_chunk_removes_the_filmstrip(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mp4 = os.path.join(tmp.name, "cam_20261008T150000Z.mp4")
        with open(mp4, "wb") as fh:
            fh.write(b"m" * 16)
        for name in (mp4 + ".jpg", mp4 + ".jpg.tmp.jpg"):
            with open(name, "wb") as fh:
                fh.write(b"j" * 80)
        with open(mp4 + ".jpg.n", "w", encoding="ascii") as fh:
            fh.write("30\n")
        with open(mp4 + ".jpg.n.tmp", "w", encoding="ascii") as fh:
            fh.write("30\n")
        mod.unlink_chunk(mp4)
        self.assertFalse(os.path.exists(mp4))
        self.assertFalse(os.path.exists(mp4 + ".jpg"))
        self.assertFalse(os.path.exists(mp4 + ".jpg.n"))
        self.assertFalse(os.path.exists(mp4 + ".jpg.tmp.jpg"))
        self.assertFalse(os.path.exists(mp4 + ".jpg.n.tmp"))

    def test_orphans_drop_stray_strips_and_keep_indexed_ones(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        native = os.path.join(tmp.name, "storage", "inputs", "cam", "native")
        os.makedirs(native)
        live = os.path.join(native, "cam_live.mp4")
        extra = os.path.join(native, "cam_extra.mp4")
        stray = os.path.join(native, "cam_gone.mp4")
        missing = os.path.join(native, "cam_missing.mp4")
        for path in (live, extra):
            with open(path, "wb") as fh:
                fh.write(b"m" * 16)
        for path in (live, extra, stray, missing):
            with open(path + ".jpg", "wb") as fh:
                fh.write(b"j" * 80)
            with open(path + ".jpg.n", "w", encoding="ascii") as fh:
                fh.write("30\n")
        fresh = live + ".jpg.tmp.jpg"
        stale = live + ".jpg.n.tmp"
        with open(fresh, "wb") as fh:
            fh.write(b"t" * 8)
        with open(stale, "w", encoding="ascii") as fh:
            fh.write("30\n")
        os.utime(stale, (1, 1))

        class Conn:
            def execute(self, *_a, **_k):
                return None

            def commit(self):
                return None

        orig = mod.fetchall
        mod.fetchall = lambda _conn, _sql, _args=(): [
            {"id": "chk_live", "path": live},
            {"id": "chk_missing", "path": missing},
        ]
        try:
            n = mod.orphans(Conn(), os.path.join(tmp.name, "storage"))
        finally:
            mod.fetchall = orig
        self.assertGreaterEqual(n, 1)
        self.assertTrue(os.path.isfile(live))
        self.assertTrue(os.path.isfile(live + ".jpg"))
        self.assertTrue(os.path.isfile(live + ".jpg.n"))
        self.assertTrue(os.path.isfile(fresh))
        self.assertFalse(os.path.exists(stale))
        self.assertFalse(os.path.exists(extra))
        self.assertFalse(os.path.exists(extra + ".jpg"))
        self.assertFalse(os.path.exists(extra + ".jpg.n"))
        self.assertFalse(os.path.exists(stray + ".jpg"))
        self.assertFalse(os.path.exists(stray + ".jpg.n"))
        self.assertFalse(os.path.exists(missing + ".jpg"))
        self.assertFalse(os.path.exists(missing + ".jpg.n"))


if __name__ == "__main__":
    unittest.main()
