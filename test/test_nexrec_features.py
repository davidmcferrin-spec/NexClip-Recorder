#!/usr/bin/env python3
"""Parsers, FTS, and sidecar argv — record path must stay untouched."""

from __future__ import annotations

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
    parse_blackdetect,
    parse_ebur128,
    parse_freezedetect,
    parse_scte35_probe,
    parse_srt,
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


if __name__ == "__main__":
    unittest.main()
