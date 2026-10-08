#!/usr/bin/env python3
"""Unit tests for FFmpeg argv assembly and wall-clock timecode."""

from __future__ import annotations

import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "worker"))

from nexrec_ffmpeg import (  # noqa: E402
    encode_args,
    export_concat_argv,
    input_args,
    metadata_args,
    parse_export_progress,
    pin_video_encoder,
    preview_argv,
    preview_unit_allowed,
    record_argv,
    segment_args,
)
from nexrec_util import parse_bytes, pin_process_utc, wallclock_timecode  # noqa: E402


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
        self.assertEqual(args[args.index("-audio_input") + 1], "embedded")
        self.assertEqual(args[args.index("-raw_format") + 1], "yuv422p10")
        self.assertLess(args.index("-raw_format"), args.index("-i"))
        self.assertLess(args.index("-audio_input"), args.index("-i"))

    def test_copy_native_no_upconvert(self):
        args = encode_args({"copy_native": 1, "live_transcode": 0, "upconvert_1080i": 0})
        self.assertEqual(args, ["-c", "copy"])

    def test_transcode_broadcast_profile(self):
        args = encode_args({"live_transcode": 1, "copy_native": 0}, {"NEXREC_BROADCAST_VIDEO_BITRATE": "12M"})
        self.assertIn("libx264", args)
        self.assertIn("high", args)
        self.assertIn("aac", args)
        self.assertIn("12M", args)
        self.assertIn("4.1", args)
        self.assertNotIn("+ildct+ilme", args)

    def test_keep_interlace_does_not_force_field_coding(self):
        args = encode_args({"source_type": "decklink", "live_transcode": 1, "keep_interlace": 1})
        self.assertNotIn("+ildct+ilme", args)
        self.assertIn("4.2", args)

    def test_locked_1080i_stays_interlaced(self):
        args = encode_args({
            "source_type": "decklink",
            "signal_mode": "1080i59.94",
            "keep_interlace": 0,
        })
        self.assertIn("+ildct+ilme", args)
        self.assertIn("tff=1", args)
        self.assertIn("4.1", args)
        self.assertNotIn("4.2", args)

    def test_locked_1080p60_stays_progressive(self):
        args = encode_args({
            "source_type": "decklink",
            "signal_mode": "1080p60",
            "keep_interlace": 1,
            "upconvert_1080i": 0,
        })
        self.assertNotIn("+ildct+ilme", args)
        self.assertNotIn("tff=1", args)
        self.assertIn("4.2", args)
        self.assertIn("libx264", args)

    def test_ntsc_is_bottom_field(self):
        args = encode_args({"source_type": "decklink", "signal_mode": "525i59.94 (NTSC)"})
        self.assertIn("bff=1", args)
        self.assertNotIn("tff=1", args)

    def test_decklink_without_a_mode_does_not_copy(self):
        args = encode_args({"source_type": "decklink", "copy_native": 1, "live_transcode": 0})
        self.assertIn("libx264", args)
        self.assertNotIn("+ildct+ilme", args)
        self.assertIn("4.2", args)
        self.assertNotEqual(args, ["-c", "copy"])

    def test_upconvert_1080i_is_progressive_field_rate(self):
        args = encode_args({
            "source_type": "decklink",
            "signal_mode": "1080i59.94",
            "upconvert_1080i": 1,
            "keep_interlace": 1,
        })
        self.assertNotIn("+ildct+ilme", args)
        self.assertTrue(any("yadif" in str(a) for a in args))
        self.assertIn("4.2", args)

    def test_format_code_wins_over_a_different_status_mode(self):
        args = encode_args({
            "source_type": "decklink",
            "decklink_format": "Hi59",
            "signal_mode": "1080p60",
        })
        self.assertIn("+ildct+ilme", args)
        self.assertIn("tff=1", args)
        self.assertIn("4.1", args)

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
        self.assertEqual(cmd[cmd.index("-raw_format") + 1], "yuv422p10")
        self.assertLess(cmd.index("-raw_format"), i)
        self.assertIn("libx264", cmd)
        self.assertEqual(cmd[cmd.index("-a53cc") + 1], "1")
        self.assertIn("aac", cmd)
        self.assertIn("+ildct+ilme", cmd)
        self.assertIn("tff=1", cmd)
        self.assertIn("4.1", cmd)
        self.assertNotIn("-timecode", cmd)
        self.assertNotIn("split=2", " ".join(cmd))

    def test_decklink_tee_is_one_open(self):
        rtsp = "rtsp://127.0.0.1:8554/in0"
        cmd = record_argv(
            {
                "source_type": "decklink",
                "decklink_device": "DeckLink Duo (1)",
                "signal_mode": "1080p60",
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
        self.assertEqual(cmd[cmd.index("-raw_format") + 1], "yuv422p10")
        self.assertIn("libx264", cmd)
        self.assertEqual(cmd[cmd.index("-a53cc") + 1], "1")
        self.assertIn("aac", cmd)
        self.assertNotIn("+ildct+ilme", cmd)
        self.assertIn("4.2", cmd)
        self.assertIn(rtsp, cmd)
        mp4 = cmd.index("/data/in_%Y%m%dT%H%M%SZ.mp4")
        self.assertLess(mp4, cmd.index(rtsp))
        record_out = cmd[:mp4]
        preview_out = cmd[mp4:]
        self.assertIn("aac", record_out)
        self.assertNotIn("libopus", record_out)
        self.assertIn("libopus", preview_out)
        self.assertNotIn("aac", preview_out)
        self.assertNotIn("-vf", cmd)
        self.assertFalse(preview_unit_allowed({"source_type": "decklink"}))
        self.assertTrue(preview_unit_allowed({"source_type": "rtsp"}))

    def test_decklink_live_only_publishes_preview_without_segments(self):
        rtsp = "rtsp://127.0.0.1:8554/in0"
        cmd = record_argv(
            {
                "source_type": "decklink",
                "decklink_device": "DeckLink Duo (1)",
                "signal_mode": "1080i59.94",
                "live_only": 1,
                "preview_enabled": 1,
            },
            "/data/in_%Y%m%dT%H%M%SZ.mp4",
            segment_seconds=300,
            preview_rtsp=rtsp,
        )
        joined = " ".join(cmd)
        self.assertEqual(cmd.count("decklink"), 1)
        self.assertIn(rtsp, cmd)
        self.assertIn("libopus", cmd)
        self.assertIn("[vprev]", cmd)
        self.assertNotIn("segment", joined)
        self.assertNotIn("/data/in_%Y%m%dT%H%M%SZ.mp4", cmd)
        self.assertNotIn("aac", joined)
        self.assertNotIn("[vrec]", joined)
        at = cmd.index(rtsp)
        self.assertEqual(cmd[at - 4:at - 2], ["-f", "rtsp"])

    def test_decklink_tee_upconvert_deinterlaces_record_only_in_graph(self):
        cmd = record_argv(
            {
                "source_type": "decklink",
                "decklink_device": "DeckLink Quad 2 (2)",
                "signal_mode": "1080i59.94",
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
        self.assertIn("4.2", cmd)
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

    def test_export_progress_clock(self):
        self.assertAlmostEqual(parse_export_progress("out_time_us=2500000"), 2.5)
        self.assertAlmostEqual(parse_export_progress("out_time=00:01:02.500000"), 62.5)
        self.assertAlmostEqual(parse_export_progress("frame=1 fps=30 time=00:00:03.00 bitrate=1k"), 3.0)
        self.assertIsNone(parse_export_progress("progress=continue"))

    def test_nvenc_record_and_preview(self):
        env = {"NEXREC_VIDEO_ENCODER": "nvenc"}
        args = encode_args({
            "source_type": "decklink",
            "signal_mode": "1080i59.94",
        }, env)
        self.assertIn("h264_nvenc", args)
        self.assertEqual(args[args.index("-a53cc") + 1], "1")
        self.assertNotIn("libx264", args)
        self.assertNotIn("tff=1", args)
        self.assertIn("+ildct", args)
        self.assertIn("tt", args)
        self.assertIn("p3", args)
        self.assertIn("cbr", args)
        preview = preview_argv(
            {"source_type": "rtsp", "url": "rtsp://cam/stream"},
            "rtsp://127.0.0.1:8554/in0",
            env=env,
        )
        self.assertIn("h264_nvenc", preview)
        self.assertIn("ull", preview)
        self.assertIn("p1", preview)
        self.assertNotIn("zerolatency", preview)
        self.assertIn("libopus", preview)
        self.assertNotIn("aac", preview)
        tee = record_argv(
            {"source_type": "decklink", "decklink_device": "DeckLink Quad (1)", "signal_mode": "1080p60"},
            "/data/out.mp4",
            env=env,
            preview_rtsp="rtsp://127.0.0.1:8554/in1",
        )
        self.assertEqual(tee.count("h264_nvenc"), 2)
        self.assertNotIn("libx264", tee)
        exported = export_concat_argv("/tmp/c.txt", "/tmp/o.mp4", 1.0, 5.0, copy=False, env=env)
        self.assertIn("h264_nvenc", exported)
        self.assertEqual(exported[exported.index("-level") + 1], "4.2")
        copied = export_concat_argv("/tmp/c.txt", "/tmp/o.mp4", 1.0, 5.0, copy=True, env=env)
        self.assertNotIn("h264_nvenc", copied)

    def test_proxy_export_scales_1080p60(self):
        env = {"NEXREC_VIDEO_ENCODER": "nvenc"}
        cmd = export_concat_argv(
            "/tmp/c.txt", "/tmp/o.mp4", 1.0, 5.0, copy=False, env=env, proxy=True,
        )
        vf = cmd[cmd.index("-vf") + 1]
        self.assertIn("scale=960x540", vf)
        self.assertIn("fps=30", vf)
        self.assertEqual(cmd[cmd.index("-level") + 1], "4.1")
        self.assertIn("1500k", cmd)
        self.assertNotIn("12M", cmd)
        full = export_concat_argv(
            "/tmp/c.txt", "/tmp/o.mp4", 1.0, 5.0, copy=False, env=env,
            width=1920, height=1080, fps=60,
        )
        self.assertNotIn("-vf", full)
        self.assertEqual(full[full.index("-level") + 1], "4.2")

    def test_cpu_preview_stays_zerolatency(self):
        preview = preview_argv(
            {"source_type": "rtsp", "url": "rtsp://cam/stream"},
            "rtsp://127.0.0.1:8554/in0",
        )
        self.assertIn("libx264", preview)
        self.assertIn("ultrafast", preview)
        self.assertIn("zerolatency", preview)
        self.assertNotIn("h264_nvenc", preview)
        self.assertIn("libopus", preview)
        self.assertNotIn("aac", preview)

    def test_pin_video_encoder(self):
        cpu, note = pin_video_encoder({}, probe=lambda _ff: "no NVIDIA device")
        self.assertEqual(cpu["NEXREC_VIDEO_ENCODER"], "libx264")
        self.assertEqual(note, "libx264")
        blocked, why = pin_video_encoder({}, probe=lambda _ff: "/dev/nvidiactl is not writable by this user")
        self.assertEqual(blocked["NEXREC_VIDEO_ENCODER"], "libx264")
        self.assertIn("not writable", why)
        gpu, using = pin_video_encoder({}, probe=lambda _ff: None)
        self.assertEqual(gpu["NEXREC_VIDEO_ENCODER"], "nvenc")
        self.assertEqual(using, "h264_nvenc")
        forced, _ = pin_video_encoder(
            {"NEXREC_VIDEO_ENCODER": "libx264"},
            probe=lambda _ff: None,
        )
        self.assertEqual(forced["NEXREC_VIDEO_ENCODER"], "libx264")
        off, off_note = pin_video_encoder(
            {"NEXREC_ENABLE_NVENC": "0"},
            probe=lambda _ff: None,
        )
        self.assertEqual(off["NEXREC_VIDEO_ENCODER"], "libx264")
        self.assertIn("NEXREC_ENABLE_NVENC=0", off_note)

    def test_export_has_faststart(self):
        cmd = export_concat_argv("/tmp/c.txt", "/tmp/o.mp4", 1.5, 10.0, copy=True)
        self.assertIn("+faststart", cmd)
        self.assertIn("-ss", cmd)

    def test_wallclock_timecode_format(self):
        tc = wallclock_timecode(datetime(2026, 9, 21, 15, 4, 5, 0), fps=30)
        self.assertRegex(tc, r"^\d{2}:\d{2}:\d{2}:\d{2}$")
        self.assertTrue(tc.startswith("15:04:05"))

    def test_wallclock_timecode_converts_to_utc(self):
        edt = timezone(timedelta(hours=-4))
        est = timezone(timedelta(hours=-5))
        summer = wallclock_timecode(datetime(2026, 7, 15, 15, 4, 5, tzinfo=edt), fps=30)
        winter = wallclock_timecode(datetime(2026, 1, 15, 15, 4, 5, tzinfo=est), fps=30)
        self.assertTrue(summer.startswith("19:04:05"))
        self.assertTrue(winter.startswith("20:04:05"))

    def test_metadata_timecode_matches_utc_creation_time(self):
        edt = timezone(timedelta(hours=-4))
        when = datetime(2026, 7, 15, 15, 0, 0, tzinfo=edt)
        cmd = metadata_args(when, fps=30)
        self.assertIn("creation_time=2026-07-15T19:00:00Z", cmd)
        self.assertIn("timecode=19:00:00:00", cmd)
        self.assertNotIn("15:00:00:00", " ".join(cmd))
        self.assertNotIn("-timecode", cmd)

    def test_progressive_timecode_uses_frame_rate(self):
        when = datetime(2026, 9, 29, 3, 15, 5, 200000, tzinfo=timezone.utc)
        cmd = record_argv(
            {
                "source_type": "decklink",
                "decklink_device": "DeckLink Quad (1)",
                "signal_mode": "1080p60",
            },
            "/data/out.mp4",
            segment_seconds=300,
            when=when,
        )
        self.assertIn("timecode=03:15:05:12", cmd)
        self.assertNotIn("-timecode", cmd)
        self.assertIn("4.2", cmd)

    def test_pin_process_utc(self):
        old = os.environ.get("TZ")
        try:
            pin_process_utc()
            self.assertEqual(os.environ.get("TZ"), "UTC")
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            if hasattr(time, "tzset"):
                time.tzset()

    def test_parse_bytes(self):
        self.assertEqual(parse_bytes("50G"), 50 * 1024**3)
        self.assertEqual(parse_bytes("512M"), 512 * 1024**2)
        self.assertEqual(parse_bytes("100"), 100)


if __name__ == "__main__":
    unittest.main()
