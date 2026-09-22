#!/usr/bin/env python3
"""Chunk filename → start_at parsing and indexer probe skipping."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "worker"))

import nexrec_index  # noqa: E402
from nexrec_db import connect, insert_chunk, migrate, upsert_input  # noqa: E402
from nexrec_index import scan_dir, start_from_filename  # noqa: E402
from nexrec_util import iso_z, utcnow, valid_input_id  # noqa: E402


class TestIndex(unittest.TestCase):
    def test_filename(self):
        self.assertEqual(
            start_from_filename("/data/inputs/demo/native/2026/09/21/demo_20260921T150000Z.mp4"),
            "2026-09-21T15:00:00Z",
        )
        self.assertIsNone(start_from_filename("nope.mp4"))

    def test_input_id(self):
        self.assertTrue(valid_input_id("studio-a"))
        self.assertFalse(valid_input_id("../etc"))
        self.assertFalse(valid_input_id("A"))

    def test_scan_probes_only_new_chunks(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        day = os.path.join(tmp.name, "native", "2026", "09", "21")
        os.makedirs(day)
        ready_name = "demo_20260921T150000Z.mp4"
        pending_name = "demo_20260921T150500Z.mp4"
        new_name = "demo_20260921T151000Z.mp4"
        open_name = "demo_20260921T151500Z.mp4"
        paths = {}
        for name in (ready_name, pending_name, new_name, open_name):
            path = os.path.join(day, name)
            with open(path, "wb") as fh:
                fh.write(b"\x00" * 128)
            paths[name] = os.path.abspath(path)

        conn = connect(os.path.join(tmp.name, "nexrec.db"))
        self.addCleanup(conn.close)
        migrate(conn)
        now = iso_z(utcnow())
        upsert_input(
            conn,
            {
                "id": "demo",
                "name": "demo",
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
                "created_at": now,
                "updated_at": now,
            },
        )
        insert_chunk(
            conn,
            {
                "id": "chk_ready",
                "input_id": "demo",
                "path": paths[ready_name],
                "kind": "native",
                "start_at": "2026-09-21T15:00:00Z",
                "end_at": "2026-09-21T15:05:00Z",
                "duration_s": 300,
                "size_bytes": 128,
                "width": 16,
                "height": 16,
                "fps": 30,
                "interlaced": 0,
                "codec": "h264",
                "timecode_start": None,
                "ready": 1,
                "orphan": 0,
                "created_at": now,
            },
        )
        insert_chunk(
            conn,
            {
                "id": "chk_pending",
                "input_id": "demo",
                "path": paths[pending_name],
                "kind": "native",
                "start_at": "2026-09-21T15:05:00Z",
                "end_at": None,
                "duration_s": None,
                "size_bytes": 128,
                "width": None,
                "height": None,
                "fps": None,
                "interlaced": 0,
                "codec": None,
                "timecode_start": None,
                "ready": 0,
                "orphan": 0,
                "created_at": now,
            },
        )

        log = os.path.join(tmp.name, "probed.txt")
        probe = os.path.join(tmp.name, "ffprobe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                f"open({log!r}, 'a', encoding='utf-8').write(sys.argv[-1] + '\\n')\n"
                "json.dump({'format': {'duration': '5.0', 'size': '128', 'tags': {}}, "
                "'streams': [{'codec_type': 'video', 'codec_name': 'h264', "
                "'width': 16, 'height': 16, 'avg_frame_rate': '30/1', "
                "'field_order': 'progressive'}]}, sys.stdout)\n"
            )
        os.chmod(probe, os.stat(probe).st_mode | stat.S_IEXEC)

        found = scan_dir(
            conn,
            os.path.join(tmp.name, "native"),
            "demo",
            kind="native",
            ffprobe=probe,
            skip_basename=open_name,
        )
        with open(log, encoding="utf-8") as fh:
            probed = fh.read().splitlines()
        self.assertEqual(
            sorted(os.path.basename(p) for p in probed),
            sorted([new_name, pending_name]),
        )
        self.assertEqual(
            sorted(os.path.basename(rec["path"]) for rec in found),
            sorted([new_name, pending_name]),
        )
        self.assertNotIn(ready_name, "\n".join(probed))
        self.assertNotIn(open_name, "\n".join(probed))

        os.remove(log)
        again = scan_dir(
            conn,
            os.path.join(tmp.name, "native"),
            "demo",
            kind="native",
            ffprobe=probe,
            skip_basename=open_name,
        )
        self.assertEqual(again, [])
        self.assertFalse(os.path.exists(log))

    def test_scan_dedupes_same_absolute_path_with_relative_record_path(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        day = os.path.join(tmp.name, "native", "2026", "09", "21")
        os.makedirs(day)
        path = os.path.join(day, "demo_20260921T151000Z.mp4")
        with open(path, "wb") as fh:
            fh.write(b"\x00" * 128)
        abs_path = os.path.abspath(path)

        seen = []
        orig_walk = nexrec_index.os.walk
        orig_index_file = nexrec_index.index_file
        orig_ready = nexrec_index.ready_chunk_paths
        self.addCleanup(lambda: setattr(nexrec_index.os, "walk", orig_walk))
        self.addCleanup(lambda: setattr(nexrec_index, "index_file", orig_index_file))
        self.addCleanup(lambda: setattr(nexrec_index, "ready_chunk_paths", orig_ready))

        def fake_walk(_root):
            yield day, [], [os.path.basename(path), os.path.basename(path)]

        def fake_index_file(_conn, probe_path, _input_id, *, kind="native", ffprobe="ffprobe"):
            del _conn, _input_id, kind, ffprobe
            seen.append(probe_path)
            return {"path": os.path.relpath(probe_path, tmp.name)}

        nexrec_index.os.walk = fake_walk
        nexrec_index.index_file = fake_index_file
        nexrec_index.ready_chunk_paths = lambda _conn, _input_id, _kind: set()

        found = scan_dir(object(), day, "demo")
        self.assertEqual(seen, [abs_path])
        self.assertEqual(found, [{"path": os.path.relpath(abs_path, tmp.name)}])


if __name__ == "__main__":
    unittest.main()
