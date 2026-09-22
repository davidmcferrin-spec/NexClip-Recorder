#!/usr/bin/env python3
"""Parsers, FTS, and sidecar argv — record path must stay untouched."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "worker"))

from nexrec_db import (  # noqa: E402
    connect,
    insert_caption,
    migrate,
    search_captions,
    upsert_input,
)
from nexrec_features import (  # noqa: E402
    analyze_chunk,
    parse_blackdetect,
    parse_ebur128,
    parse_freezedetect,
    parse_scte35_probe,
    parse_srt,
)
from nexrec_nielsen import (  # noqa: E402
    detect_nielsen_presence,
    normalize_presence_payload,
)
from nexrec_ffmpeg import detect_argv, ebur128_argv, record_argv  # noqa: E402
from nexrec_util import iso_z  # noqa: E402


BLACK_LOG = """
[blackdetect @ 0x1] black_start:1.000 black_end:4.500 black_duration:3.500
[blackdetect @ 0x1] black_start:10.0 black_end:10.4 black_duration:0.400
"""
FREEZE_LOG = """
[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 0.5
[freezedetect @ 0x2] lavfi.freezedetect.freeze_end: 3.5
[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 9.0
[freezedetect @ 0x2] lavfi.freezedetect.freeze_end: 9.2
"""
EBUR_LOG = """
[Parsed_ebur128_0 @ 0x3] t: 0.500  TARGET:-23 LUFS    M: -22.1 S: -23.0     I: -23.2 LUFS
[Parsed_ebur128_0 @ 0x3] t: 1.000  TARGET:-23 LUFS    M: -21.0 S: -22.5     I: -22.8 LUFS
[Parsed_ebur128_0 @ 0x3] Summary:
  Integrated loudness:    -23.1 LUFS
  Loudness range:           4.2 LU
  True peak:               -1.5 dBTP
"""
SRT = """1
00:00:01,000 --> 00:00:02,500
Hello studio

2
00:00:03,000 --> 00:00:04,000
Lower third
"""


class TestFeatures(unittest.TestCase):
    def test_black_respects_threshold(self):
        hits = parse_blackdetect(BLACK_LOG, min_s=2.0)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["kind"], "black")
        self.assertAlmostEqual(hits[0]["duration_s"], 3.5)

    def test_freeze_respects_threshold(self):
        hits = parse_freezedetect(FREEZE_LOG, min_s=2.0)
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0]["pts"], 0.5)
        self.assertAlmostEqual(hits[0]["duration_s"], 3.0)

    def test_ebur128_frames_and_summary(self):
        s = parse_ebur128(EBUR_LOG)
        self.assertEqual(len(s["frames"]), 2)
        self.assertAlmostEqual(s["integrated"], -23.1)
        self.assertAlmostEqual(s["lra"], 4.2)
        self.assertAlmostEqual(s["true_peak"], -1.5)

    def test_srt(self):
        cues = parse_srt(SRT)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["text"], "Hello studio")
        self.assertAlmostEqual(cues[0]["pts"], 1.0)

    def test_scte35_from_probe_json(self):
        data = {
            "streams": [{"index": 2, "codec_name": "scte_35", "codec_type": "data"}],
            "packets": [{"stream_index": 2, "pts_time": "12.5", "size": 48}],
        }
        hits = parse_scte35_probe(data)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["kind"], "scte35")
        self.assertAlmostEqual(hits[0]["pts"], 12.5)

    def test_record_argv_unchanged_by_detect(self):
        rec = record_argv(
            {"source_type": "testsrc", "live_transcode": 1, "copy_native": 0},
            "/tmp/%Y.mp4",
            env={"NEXREC_SEGMENT_AT_CLOCK": "0"},
            segment_seconds=5,
        )
        blob = " ".join(rec)
        self.assertNotIn("blackdetect", blob)
        self.assertNotIn("freezedetect", blob)
        self.assertNotIn("ebur128", blob)
        det = detect_argv("/tmp/x.mp4", freeze_s=2, black_s=3)
        self.assertIn("blackdetect=d=3", " ".join(det))
        self.assertIn("freezedetect", " ".join(det))
        self.assertIn("ebur128", " ".join(ebur128_argv("/tmp/x.mp4")))

    def test_fts_caption_search(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = connect(os.path.join(tmp.name, "nexrec.db"))
        migrate(conn)
        upsert_input(
            conn,
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
                "retention_days": 28,
                "preview_path": "in0",
                "preview_enabled": 1,
                "created_at": iso_z(),
                "updated_at": iso_z(),
            },
        )
        insert_caption(
            conn,
            {
                "id": "cap_1",
                "input_id": "cam",
                "chunk_id": None,
                "kind": "caption",
                "service": "608/708",
                "speaker": "",
                "t_start": "2026-09-21T15:00:01Z",
                "t_end": "2026-09-21T15:00:02Z",
                "pts": 1.0,
                "timecode": "15:00:01:00",
                "text": "weather alert downtown",
                "created_at": iso_z(),
            },
        )
        hits = search_captions(conn, "weather")
        self.assertTrue(hits)
        self.assertIn("weather", hits[0]["text"])
        cols = {r[1] for r in conn.execute("PRAGMA table_info(inputs)").fetchall()}
        self.assertIn("feat_scte", cols)
        self.assertIn("thresh_freeze_s", cols)

    def test_nielsen_stub_covers_timeline_without_decode_fields(self):
        hits = detect_nielsen_presence("/tmp/no-such.mp4", 90, window_s=30)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["kind"], "nielsen")
        self.assertEqual(hits[0]["subtype"], "absent")
        self.assertAlmostEqual(hits[0]["pts"], 0.0)
        self.assertAlmostEqual(hits[0]["pts_end"], 90.0)
        payload = json.loads(hits[0]["payload_json"])
        self.assertFalse(payload["present"])
        self.assertFalse(payload["audit_grade"])
        self.assertFalse(payload["decoded"])
        self.assertEqual(payload["method"], "stub")
        for banned in ("sid", "layer", "timestamp", "layers"):
            self.assertNotIn(banned, payload)

    def test_nielsen_detector_swap_splits_present_and_absent(self):
        class Flip:
            method = "test"

            def sample(self, path, pts, pts_end):
                del path, pts_end
                return pts < 30

        hits = detect_nielsen_presence("/tmp/x.mp4", 90, window_s=30, detector=Flip())
        self.assertEqual([h["subtype"] for h in hits], ["present", "absent"])
        self.assertAlmostEqual(hits[0]["pts_end"], 30.0)
        self.assertAlmostEqual(hits[1]["pts"], 30.0)
        self.assertAlmostEqual(hits[1]["pts_end"], 90.0)
        self.assertNotIn("sid", json.loads(hits[0]["payload_json"]))

    def test_nielsen_command_json_drops_decode_fields(self):
        samples = normalize_presence_payload(
            [
                {
                    "pts": 0,
                    "pts_end": 10,
                    "present": True,
                    "sid": "ABC",
                    "layer": 1,
                    "timestamp": "12:00:00",
                }
            ]
        )
        self.assertEqual(samples, [{"pts": 0.0, "pts_end": 10.0, "present": True}])

    def test_nielsen_presence_command_swap(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        script = os.path.join(tmp.name, "presence.py")
        media = os.path.join(tmp.name, "chunk.mp4")
        with open(media, "wb"):
            pass
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(
                "import json,sys\n"
                "json.dump(["
                "{'pts':0,'pts_end':15,'present':True,'sid':'NOPE','layer':2},"
                "{'pts':15,'pts_end':30,'present':False,'timestamp':'01:02:03'}"
                "], open(sys.argv[2],'w'))\n"
            )
        hits = detect_nielsen_presence(
            media,
            30,
            env={
                "NEXREC_NIELSEN_PRESENCE_CMD": f"{sys.executable} {script} {{input}} {{output}}",
            },
        )
        self.assertEqual([h["subtype"] for h in hits], ["present", "absent"])
        payload = json.loads(hits[0]["payload_json"])
        self.assertEqual(payload["method"], "command")
        self.assertFalse(payload["audit_grade"])
        self.assertFalse(payload["decoded"])
        self.assertNotIn("sid", payload)
        self.assertNotIn("NOPE", hits[0]["payload_json"])
        self.assertFalse(os.path.exists(media + ".nielsen-presence.json"))

    def test_nielsen_presence_command_allows_empty_results(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        script = os.path.join(tmp.name, "presence-empty.py")
        media = os.path.join(tmp.name, "chunk.mp4")
        with open(media, "wb"):
            pass
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("import json,sys\njson.dump([], open(sys.argv[2],'w'))\n")
        hits = detect_nielsen_presence(
            media,
            30,
            env={
                "NEXREC_NIELSEN_PRESENCE_CMD": f"{sys.executable} {script} {{input}} {{output}}",
            },
        )
        self.assertEqual(hits, [])

    def test_nielsen_presence_command_error_uses_stub_with_error(self):
        hits = detect_nielsen_presence(
            "/tmp/no-such.mp4",
            30,
            env={"NEXREC_NIELSEN_PRESENCE_CMD": "/definitely/missing/bin {input} {output}"},
        )
        self.assertEqual([h["subtype"] for h in hits], ["absent"])
        payload = json.loads(hits[0]["payload_json"])
        self.assertEqual(payload["method"], "stub")
        self.assertIn("command_error", payload)

    def test_nielsen_presence_persists_wallclock_every_chunk(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "chunk.mp4")
        with open(path, "wb"):
            pass
        conn = connect(os.path.join(tmp.name, "nexrec.db"))
        migrate(conn)
        now = iso_z()
        source = {
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
            "retention_days": 28,
            "preview_path": "in0",
            "preview_enabled": 1,
            "feat_nielsen": 1,
            "created_at": now,
            "updated_at": now,
        }
        upsert_input(conn, source)
        chunk = {
            "id": "chk1",
            "input_id": "cam",
            "path": path,
            "start_at": "2026-09-21T15:00:00Z",
            "end_at": "2026-09-21T15:01:30Z",
            "duration_s": 90,
            "fps": 30,
        }
        stats = analyze_chunk(conn, {"NEXREC_NIELSEN_WINDOW_S": "30"}, source, chunk, storage=tmp.name)
        self.assertGreaterEqual(stats["events"], 1)
        row = dict(conn.execute("SELECT * FROM events WHERE kind='nielsen'").fetchone())
        self.assertEqual(row["subtype"], "absent")
        self.assertEqual(row["t_start"], "2026-09-21T15:00:00Z")
        self.assertEqual(row["t_end"], "2026-09-21T15:01:30Z")
        self.assertEqual(row["timecode"], "15:00:00:00")
        payload = json.loads(row["payload_json"])
        self.assertFalse(payload["audit_grade"])
        self.assertNotIn("sid", payload)
        chunk2 = dict(chunk, id="chk2", start_at="2026-09-21T15:01:30Z", end_at="2026-09-21T15:03:00Z")
        analyze_chunk(conn, {"NEXREC_NIELSEN_WINDOW_S": "30"}, source, chunk2, storage=tmp.name)
        n = conn.execute("SELECT COUNT(*) FROM events WHERE kind='nielsen'").fetchone()[0]
        self.assertEqual(n, 2)


if __name__ == "__main__":
    unittest.main()
