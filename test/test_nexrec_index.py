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
from nexrec_index import (  # noqa: E402
    open_segment_basename,
    orphan_thumb_paths,
    recording_for_sidecar,
    scan_dir,
    start_from_filename,
    thumb_count_path_for,
    thumb_frame_count,
    thumb_path_for,
    write_chunk_thumb,
)
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

    def test_open_segment_is_latest_name_not_mtime(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        day = os.path.join(tmp.name, "2026", "09", "29")
        os.makedirs(day)
        closed = os.path.join(day, "in1_20260929T031500Z.mp4")
        opened = os.path.join(day, "in1_20260929T032000Z.mp4")
        for path in (opened, closed):
            with open(path, "wb") as fh:
                fh.write(b"\x00" * 128)
        # faststart rewrites the closed segment after the new one is created.
        os.utime(opened, (1_000, 1_000))
        os.utime(closed, (2_000, 2_000))
        self.assertEqual(open_segment_basename(tmp.name), "in1_20260929T032000Z.mp4")

    def test_missing_moov_does_not_fail_the_scan(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        day = os.path.join(tmp.name, "native", "2026", "09", "29")
        os.makedirs(day)
        good = os.path.join(day, "in1_20260929T031500Z.mp4")
        broken = os.path.join(day, "in1_20260929T032000Z.mp4")
        for path in (good, broken):
            with open(path, "wb") as fh:
                fh.write(b"\x00" * 128)
        log = os.path.join(tmp.name, "probed.txt")
        probe = os.path.join(tmp.name, "ffprobe")
        broken_abs = os.path.abspath(broken)
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                f"open({log!r}, 'a', encoding='utf-8').write(sys.argv[-1] + '\\n')\n"
                f"if sys.argv[-1] == {broken_abs!r}:\n"
                "    sys.stderr.write('[mov,mp4] moov atom not found\\n')\n"
                "    raise SystemExit(1)\n"
                "json.dump({'format': {'duration': '300.0', 'size': '128', 'tags': {}}, "
                "'streams': [{'codec_type': 'video', 'codec_name': 'h264', "
                "'width': 16, 'height': 16, 'avg_frame_rate': '30/1', "
                "'field_order': 'progressive'}]}, sys.stdout)\n"
            )
        os.chmod(probe, os.stat(probe).st_mode | stat.S_IEXEC)
        orig_ready = nexrec_index.ready_chunk_paths
        orig_insert = nexrec_index.insert_chunk
        self.addCleanup(lambda: setattr(nexrec_index, "ready_chunk_paths", orig_ready))
        self.addCleanup(lambda: setattr(nexrec_index, "insert_chunk", orig_insert))
        nexrec_index.ready_chunk_paths = lambda *_a, **_k: set()
        nexrec_index.insert_chunk = lambda _conn, rec: inserted.append(rec)
        inserted: list[dict] = []
        pending: dict[str, tuple[int, int]] = {}
        found = scan_dir(
            object(),
            os.path.join(tmp.name, "native"),
            "in1",
            ffprobe=probe,
            pending=pending,
        )
        self.assertEqual([os.path.basename(rec["path"]) for rec in found], [os.path.basename(good)])
        self.assertIn(os.path.abspath(broken), pending)
        with open(log, encoding="utf-8") as fh:
            first = fh.read().splitlines()
        self.assertEqual(len(first), 2)
        os.remove(log)
        again = scan_dir(
            object(),
            os.path.join(tmp.name, "native"),
            "in1",
            ffprobe=probe,
            pending=pending,
        )
        self.assertTrue(all(os.path.basename(rec["path"]) != os.path.basename(broken) for rec in again))
        with open(log, encoding="utf-8") as fh:
            second = fh.read()
        self.assertNotIn(broken_abs, second)
        self.assertIn(os.path.abspath(good), second)

    def test_scan_refreshes_duration_when_the_file_grew(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        day = os.path.join(tmp.name, "native")
        os.makedirs(day)
        path = os.path.join(day, "demo_20260921T150000Z.mp4")
        with open(path, "wb") as fh:
            fh.write(b"\x00" * 200)
        abs_path = os.path.abspath(path)
        probed: list[str] = []
        thumbs: list[tuple] = []

        def fake_index(_conn, probe_path, _input_id, kind="native", ffprobe="ffprobe"):
            del _conn, _input_id, kind, ffprobe
            probed.append(probe_path)
            return {"path": probe_path, "duration_s": 12.5, "size_bytes": 200}

        orig_ready = nexrec_index.ready_chunk_paths
        orig_sizes = nexrec_index.ready_chunk_sizes
        orig_index = nexrec_index.index_file
        orig_thumb = nexrec_index.write_chunk_thumb
        self.addCleanup(lambda: setattr(nexrec_index, "ready_chunk_paths", orig_ready))
        self.addCleanup(lambda: setattr(nexrec_index, "ready_chunk_sizes", orig_sizes))
        self.addCleanup(lambda: setattr(nexrec_index, "index_file", orig_index))
        self.addCleanup(lambda: setattr(nexrec_index, "write_chunk_thumb", orig_thumb))
        nexrec_index.ready_chunk_paths = lambda *_a, **_k: {abs_path}
        nexrec_index.ready_chunk_sizes = lambda *_a, **_k: {abs_path: 128}
        nexrec_index.index_file = fake_index
        nexrec_index.write_chunk_thumb = lambda *a, **k: thumbs.append(a)

        known: dict[str, tuple[int, int]] = {}
        found = scan_dir(object(), day, "demo", known=known)
        self.assertEqual([rec["duration_s"] for rec in found], [12.5])
        self.assertEqual(probed, [abs_path])
        self.assertEqual(thumbs, [])
        again = scan_dir(object(), day, "demo", known=known)
        self.assertEqual(again, [])
        self.assertEqual(probed, [abs_path])

    def test_scan_skips_a_ready_file_whose_size_matches(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        day = os.path.join(tmp.name, "native")
        os.makedirs(day)
        path = os.path.join(day, "demo_20260921T150000Z.mp4")
        with open(path, "wb") as fh:
            fh.write(b"\x00" * 200)
        abs_path = os.path.abspath(path)
        probed: list[str] = []
        orig_ready = nexrec_index.ready_chunk_paths
        orig_sizes = nexrec_index.ready_chunk_sizes
        orig_index = nexrec_index.index_file
        self.addCleanup(lambda: setattr(nexrec_index, "ready_chunk_paths", orig_ready))
        self.addCleanup(lambda: setattr(nexrec_index, "ready_chunk_sizes", orig_sizes))
        self.addCleanup(lambda: setattr(nexrec_index, "index_file", orig_index))
        nexrec_index.ready_chunk_paths = lambda *_a, **_k: {abs_path}
        nexrec_index.ready_chunk_sizes = lambda *_a, **_k: {abs_path: 200}
        nexrec_index.index_file = lambda *_a, **_k: probed.append("called")

        known: dict[str, tuple[int, int]] = {}
        found = scan_dir(object(), day, "demo", known=known)
        self.assertEqual(found, [])
        self.assertEqual(probed, [])
        self.assertIn(abs_path, known)


class TestThumbs(unittest.TestCase):
    def test_still_is_written_beside_the_mp4(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mp4 = os.path.join(tmp.name, "demo_20260921T150000Z.mp4")
        with open(mp4, "wb") as fh:
            fh.write(b"x" * 80)
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            with open(cmd[-1], "wb") as fh:
                fh.write(b"j" * 80)

            class Result:
                returncode = 0

            return Result()

        orig = nexrec_index.subprocess.run
        nexrec_index.subprocess.run = fake_run
        nexrec_index._thumb_failed.discard(mp4)
        try:
            self.assertEqual(thumb_path_for(mp4), mp4 + ".jpg")
            self.assertTrue(write_chunk_thumb(mp4, "ffmpeg"))
            self.assertIn("-update", calls[0])
            self.assertTrue(calls[0][-1].endswith(".jpg"))
            self.assertNotIn(".part", calls[0][-1])
            self.assertTrue(os.path.isfile(mp4 + ".jpg"))
            self.assertTrue(write_chunk_thumb(mp4, "ffmpeg"))
            self.assertEqual(len(calls), 1)
        finally:
            nexrec_index.subprocess.run = orig

    def test_failed_still_is_not_retried(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mp4 = os.path.join(tmp.name, "demo_20260921T150500Z.mp4")
        with open(mp4, "wb") as fh:
            fh.write(b"x" * 80)
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)

            class Result:
                returncode = 1

            return Result()

        orig = nexrec_index.subprocess.run
        nexrec_index.subprocess.run = fake_run
        nexrec_index._thumb_failed.discard(mp4)
        try:
            self.assertFalse(write_chunk_thumb(mp4, "ffmpeg"))
            self.assertFalse(os.path.isfile(mp4 + ".jpg"))
            self.assertFalse(os.path.isfile(thumb_count_path_for(mp4)))
            self.assertFalse(write_chunk_thumb(mp4, "ffmpeg"))
            self.assertEqual(len(calls), 1)
        finally:
            nexrec_index.subprocess.run = orig
            nexrec_index._thumb_failed.discard(mp4)

    def test_filmstrip_is_one_frame_every_ten_seconds(self):
        self.assertEqual(thumb_frame_count(None), 1)
        self.assertEqual(thumb_frame_count(10), 1)
        self.assertEqual(thumb_frame_count(12), 2)
        self.assertEqual(thumb_frame_count(300), 30)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mp4 = os.path.join(tmp.name, "demo_20260921T150000Z.mp4")
        with open(mp4, "wb") as fh:
            fh.write(b"x" * 80)
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            with open(cmd[-1], "wb") as fh:
                fh.write(b"j" * 80)

            class Result:
                returncode = 0

            return Result()

        orig = nexrec_index.subprocess.run
        nexrec_index.subprocess.run = fake_run
        nexrec_index._thumb_failed.discard(mp4)
        try:
            self.assertTrue(write_chunk_thumb(mp4, "ffmpeg", duration_s=300))
            vf = calls[0][calls[0].index("-vf") + 1]
            self.assertIn("tile=30x1", vf)
            self.assertIn("fps=fps=1/10:start_time=1", vf)
            with open(thumb_count_path_for(mp4), encoding="ascii") as fh:
                self.assertEqual(fh.read().strip(), "30")
            self.assertTrue(write_chunk_thumb(mp4, "ffmpeg", duration_s=300))
            self.assertEqual(len(calls), 1)
        finally:
            nexrec_index.subprocess.run = orig
            nexrec_index._thumb_failed.discard(mp4)

    def test_legacy_still_is_replaced_only_when_a_strip_is_due(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mp4 = os.path.join(tmp.name, "demo_20260921T151000Z.mp4")
        with open(mp4, "wb") as fh:
            fh.write(b"x" * 80)
        with open(mp4 + ".jpg", "wb") as fh:
            fh.write(b"j" * 80)
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            with open(cmd[-1], "wb") as fh:
                fh.write(b"j" * 80)

            class Result:
                returncode = 0

            return Result()

        orig = nexrec_index.subprocess.run
        nexrec_index.subprocess.run = fake_run
        nexrec_index._thumb_failed.discard(mp4)
        try:
            self.assertTrue(write_chunk_thumb(mp4, "ffmpeg", duration_s=8))
            self.assertEqual(calls, [])
            self.assertTrue(write_chunk_thumb(mp4, "ffmpeg", duration_s=300))
            self.assertEqual(len(calls), 1)
            with open(thumb_count_path_for(mp4), encoding="ascii") as fh:
                self.assertEqual(fh.read().strip(), "30")
        finally:
            nexrec_index.subprocess.run = orig
            nexrec_index._thumb_failed.discard(mp4)


class _Rows:
    def __init__(self, rows, sql, args):
        self._rows = rows
        self.sql = sql
        self.args = args

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.args = ()

    def execute(self, sql, args=()):
        self.sql = sql
        self.args = args
        return _Rows(self.rows, sql, args)


class TestBackfill(unittest.TestCase):
    def test_archive_pass_has_no_row_cap(self):
        conn = _Conn([])
        n = nexrec_index.backfill_thumbs(conn, "ffmpeg", limit=None, scan_limit=None)
        self.assertEqual(n, 0)
        self.assertNotIn("LIMIT", conn.sql)
        self.assertEqual(conn.args, ())

    def test_steady_pass_keeps_the_newest_window(self):
        conn = _Conn([])
        nexrec_index.backfill_thumbs(conn, "ffmpeg", limit=40, input_id="studio-a")
        self.assertIn("LIMIT ?", conn.sql)
        self.assertEqual(conn.args, ("studio-a", 500))

    def test_upgrade_rewrites_a_legacy_still_and_the_steady_pass_does_not(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mp4 = os.path.join(tmp.name, "demo_20260921T150000Z.mp4")
        with open(mp4, "wb") as fh:
            fh.write(b"x" * 80)
        with open(mp4 + ".jpg", "wb") as fh:
            fh.write(b"j" * 80)
        calls = []
        orig = nexrec_index.write_chunk_thumb

        def fake_write(path, ffmpeg="ffmpeg", duration_s=None):
            calls.append((path, duration_s))
            return True

        nexrec_index.write_chunk_thumb = fake_write
        try:
            conn = _Conn([{"path": mp4, "duration_s": 300}])
            n = nexrec_index.backfill_thumbs(conn, "ffmpeg", limit=None, scan_limit=None)
            self.assertEqual(n, 0)
            self.assertEqual(calls, [])
            n = nexrec_index.backfill_thumbs(
                conn, "ffmpeg", limit=None, scan_limit=None, upgrade=True
            )
            self.assertEqual(n, 1)
            self.assertEqual(calls, [(mp4, 300)])
        finally:
            nexrec_index.write_chunk_thumb = orig


class TestOrphanThumbs(unittest.TestCase):
    def test_names_point_at_the_recording(self):
        base = "cam_20261008T150000Z.mp4"
        self.assertEqual(recording_for_sidecar(base + ".jpg"), base)
        self.assertEqual(recording_for_sidecar(base + ".jpg.n"), base)
        self.assertEqual(recording_for_sidecar(base + ".jpg.tmp.jpg"), base)
        self.assertEqual(recording_for_sidecar(base + ".jpg.n.tmp"), base)
        self.assertIsNone(recording_for_sidecar(base))

    def test_indexed_strip_stays_and_strays_go(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        live = os.path.join(tmp.name, "cam_live.mp4")
        stray = os.path.join(tmp.name, "cam_gone.mp4")
        with open(live, "wb") as fh:
            fh.write(b"m" * 16)
        for path in (live, stray):
            with open(path + ".jpg", "wb") as fh:
                fh.write(b"j" * 80)
            with open(path + ".jpg.n", "w", encoding="ascii") as fh:
                fh.write("30\n")
        fresh = live + ".jpg.tmp.jpg"
        stale = live + ".jpg.n.tmp"
        with open(fresh, "wb") as fh:
            fh.write(b"t" * 8)
        with open(stale, "w", encoding="ascii") as fh:
            fh.write("1\n")
        os.utime(stale, (1, 1))
        names = os.listdir(tmp.name)
        drop = set(orphan_thumb_paths(tmp.name, names, {os.path.abspath(live)}))
        self.assertNotIn(os.path.abspath(live + ".jpg"), drop)
        self.assertNotIn(os.path.abspath(live + ".jpg.n"), drop)
        self.assertNotIn(os.path.abspath(fresh), drop)
        self.assertIn(os.path.abspath(stale), drop)
        self.assertIn(os.path.abspath(stray + ".jpg"), drop)
        self.assertIn(os.path.abspath(stray + ".jpg.n"), drop)


if __name__ == "__main__":
    unittest.main()
