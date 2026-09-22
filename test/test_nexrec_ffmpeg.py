#!/usr/bin/env python3
"""Unit tests for FFmpeg argv assembly and wall-clock timecode."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "worker"))

from nexrec_ffmpeg import (  # noqa: E402
    encode_args,
    export_concat_argv,
    input_args,
    preview_unit_allowed,
    record_argv,
    segment_args,
)
from nexrec_util import parse_bytes, wallclock_timecode  # noqa: E402


class TestFfmpeg(unittest.TestCase):
    def test_rtsp_uses_tcp(self):
        args = input_args({"source_type": "rtsp", "url": "rtsp://cam/stream"})
        self.assertIn("-rtsp_transport", args)
        self.assertIn("rtsp://cam/stream", args)

    def test_srt_udp_tcp_rtp(self):
        for t, url in (
            ("srt", "srt://enc:9000"),
            ("udp", "udp://239.1.1.1:5000"),
            ("tcp", "tcp://10.0.0.1:5000"),
            ("rtp", "rtp://239.1.1.2:5004"),
        ):
            args = input_args({"source_type": t, "url": url})
            self.assertIn("-i", args)
            self.assertIn(url, args)

    def test_decklink_designed(self):
        args = input_args({"source_type": "decklink", "decklink_device": "DeckLink Duo (1)"})
        self.assertIn("-f", args)
        self.assertIn("decklink", args)
        self.assertIn("DeckLink Duo (1)", args)

    def test_copy_native_no_upconvert(self):
        args = encode_args({"copy_native": 1, "live_transcode": 0, "upconvert_1080i": 0})
        self.assertEqual(args, ["-c", "copy"])

    def test_transcode_broadcast_profile(self):
        args = encode_args({"live_transcode": 1, "copy_native": 0}, {"NEXREC_BROADCAST_VIDEO_BITRATE": "12M"})
        self.assertIn("libx264", args)
        self.assertIn("high", args)
        self.assertIn("aac", args)
        self.assertIn("12M", args)
        self.assertNotIn("+ildct+ilme", args)

    def test_decklink_keeps_interlace_flags(self):
        args = encode_args({"source_type": "decklink", "live_transcode": 1, "keep_interlace": 1})
        self.assertIn("+ildct+ilme", args)

    def test_decklink_explicit_progressive(self):
        args = encode_args({"source_type": "decklink", "keep_interlace": 0, "upconvert_1080i": 0})
        self.assertNotIn("+ildct+ilme", args)
        self.assertIn("libx264", args)

    def test_decklink_default_interlace_and_no_copy(self):
        args = encode_args({"source_type": "decklink", "copy_native": 1, "live_transcode": 0})
        self.assertIn("libx264", args)
        self.assertIn("+ildct+ilme", args)
        self.assertNotEqual(args, ["-c", "copy"])

    def test_decklink_upconvert_skips_field_flags(self):
        args = encode_args({"source_type": "decklink", "upconvert_1080i": 1, "keep_interlace": 1})
        self.assertNotIn("+ildct+ilme", args)
        self.assertTrue(any("yadif" in str(a) for a in args))

    def test_decklink_record_is_clocked_h264(self):
        cmd = record_argv(
            {
                "source_type": "decklink",
                "decklink_device": "DeckLink Quad 2 (1)",
                "decklink_format": "Hi59",
                "keep_interlace": 1,
            },
            "/data/in_%Y%m%dT%H%M%SZ.mp4",
            segment_seconds=300,
        )
        self.assertEqual(cmd.count("decklink"), 1)
        i = cmd.index("-i")
        fmt = cmd.index("-format_code")
        self.assertLess(fmt, i)
        self.assertEqual(cmd[fmt + 1], "Hi59")
        self.assertEqual(cmd[i + 1], "DeckLink Quad 2 (1)")
        self.assertIn("-segment_atclocktime", cmd)
        self.assertIn("300", cmd)
        self.assertIn("libx264", cmd)
        self.assertIn("aac", cmd)
        self.assertIn("+ildct+ilme", cmd)
        self.assertNotIn("split=2", " ".join(cmd))

    def test_decklink_tee_is_one_open(self):
        rtsp = "rtsp://127.0.0.1:8554/in0"
        cmd = record_argv(
            {
                "source_type": "decklink",
                "decklink_device": "DeckLink Duo (1)",
                "keep_interlace": 1,
                "preview_enabled": 1,
            },
            "/data/in_%Y%m%dT%H%M%SZ.mp4",
            segment_seconds=300,
            preview_rtsp=rtsp,
        )
        joined = " ".join(cmd)
        self.assertEqual(cmd.count("decklink"), 1)
        self.assertIn("split=2", joined)
        self.assertIn("[vrec]", cmd)
        self.assertIn("[vprev]", cmd)
        self.assertIn("-segment_atclocktime", cmd)
        self.assertIn("libx264", cmd)
        self.assertIn("aac", cmd)
        self.assertIn("+ildct+ilme", cmd)
        self.assertIn(rtsp, cmd)
        self.assertLess(cmd.index("/data/in_%Y%m%dT%H%M%SZ.mp4"), cmd.index(rtsp))
        self.assertNotIn("-vf", cmd)
        self.assertFalse(preview_unit_allowed({"source_type": "decklink"}))
        self.assertTrue(preview_unit_allowed({"source_type": "rtsp"}))

    def test_decklink_tee_upconvert_deinterlaces_record_only_in_graph(self):
        cmd = record_argv(
            {
                "source_type": "decklink",
                "decklink_device": "DeckLink Quad 2 (2)",
                "upconvert_1080i": 1,
                "keep_interlace": 1,
            },
            "/data/out.mp4",
            segment_seconds=300,
            preview_rtsp="rtsp://127.0.0.1:8554/in2",
        )
        joined = " ".join(cmd)
        self.assertIn("yadif=mode=1", joined)
        self.assertNotIn("+ildct+ilme", cmd)
        self.assertEqual(cmd.count("decklink"), 1)

    def test_ip_record_ignores_preview_url(self):
        cmd = record_argv(
            {"source_type": "rtsp", "url": "rtsp://cam/stream", "live_transcode": 1, "copy_native": 0},
            "/tmp/x.mp4",
            segment_seconds=300,
            preview_rtsp="rtsp://127.0.0.1:8554/in0",
        )
        self.assertNotIn("rtsp://127.0.0.1:8554/in0", cmd)
        self.assertNotIn("split=2", " ".join(cmd))

    def test_only_1080i_upconvert_uses_yadif(self):
        args = encode_args({"upconvert_1080i": 1, "live_transcode": 1})
        self.assertTrue(any("yadif" in str(a) for a in args))

    def test_segment_clock_optional(self):
        on = segment_args("/tmp/x_%Y.mp4", 300, at_clock=True)
        off = segment_args("/tmp/x_%Y.mp4", 5, at_clock=False)
        self.assertIn("-segment_atclocktime", on)
        self.assertNotIn("-segment_atclocktime", off)
        self.assertIn("movflags=faststart", " ".join(on))

    def test_record_testsrc_maps_two_inputs(self):
        cmd = record_argv(
            {"source_type": "testsrc", "live_transcode": 1, "copy_native": 0},
            "/tmp/%Y.mp4",
            env={"NEXREC_SEGMENT_AT_CLOCK": "0"},
            segment_seconds=5,
        )
        self.assertIn("testsrc=", " ".join(cmd))
        self.assertIn("-map", cmd)

    def test_export_has_faststart(self):
        cmd = export_concat_argv("/tmp/c.txt", "/tmp/o.mp4", 1.5, 10.0, copy=True)
        self.assertIn("+faststart", cmd)
        self.assertIn("-ss", cmd)

    def test_wallclock_timecode_format(self):
        tc = wallclock_timecode(datetime(2026, 9, 21, 15, 4, 5, 0), fps=30)
        self.assertRegex(tc, r"^\d{2}:\d{2}:\d{2}:\d{2}$")
        self.assertTrue(tc.startswith("15:04:05"))

    def test_parse_bytes(self):
        self.assertEqual(parse_bytes("50G"), 50 * 1024**3)
        self.assertEqual(parse_bytes("512M"), 512 * 1024**2)
        self.assertEqual(parse_bytes("100"), 100)


if __name__ == "__main__":
    unittest.main()
