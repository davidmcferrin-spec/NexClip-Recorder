#!/usr/bin/env python3
"""Assemble FFmpeg argv for ingest, preview, and export. No subprocess here."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from nexrec_util import iso_z, wallclock_timecode

SOURCE_TYPES = ("rtsp", "srt", "udp", "tcp", "rtp", "decklink", "testsrc")

# Proxy preview — monitoring quality, not the recorded mezzanine.
PREVIEW_SIZE = "960x540"
PREVIEW_VBITRATE = "1500k"
PREVIEW_ABITRATE = "96k"


def input_args(source: dict[str, Any]) -> list[str]:
    t = (source.get("source_type") or source.get("type") or "").lower()
    url = source.get("url") or source.get("SOURCE_URL") or ""
    args: list[str] = ["-hide_banner", "-nostdin"]
    # Wall-clock PTS for real inputs so files track NTP. lavfi testsrc has
    # its own synthetic clock — applying wallclock there drops thousands of frames.
    if t != "testsrc":
        args += ["-use_wallclock_as_timestamps", "1"]

    if t == "rtsp":
        args += ["-rtsp_transport", "tcp", "-i", url]
    elif t == "srt":
        args += ["-i", url]
    elif t == "udp":
        # MPEG-TS multicast/unicast. Overrun buffer helps bursty LAN.
        args += ["-fifo_size", "1000000", "-overrun_nonfatal", "1", "-i", url]
    elif t == "tcp":
        args += ["-i", url]
    elif t == "rtp":
        args += ["-i", url]
    elif t == "decklink":
        device = source.get("decklink_device") or source.get("DECKLINK_DEVICE") or "DeckLink Quad 2 (1)"
        fmt = source.get("decklink_format") or source.get("DECKLINK_FORMAT") or ""
        args += ["-f", "decklink"]
        if fmt:
            args += ["-format_code", fmt]
        args += ["-i", device]
    elif t == "testsrc":
        size = source.get("test_size") or "1280x720"
        rate = str(source.get("test_rate") or "30")
        args += [
            "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}:decimals=2",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000",
        ]
    else:
        raise ValueError(f"unsupported source_type: {t!r}")
    return args


def _keep_interlace(source: dict[str, Any]) -> bool:
    """1080i stays 1080i only when the operator (or DeckLink) says so."""
    if int(source.get("upconvert_1080i") or 0):
        return False
    if int(source.get("keep_interlace") or 0):
        return True
    t = (source.get("source_type") or "").lower()
    return t == "decklink"


def encode_args(source: dict[str, Any], env: dict[str, str] | None = None) -> list[str]:
    env = env or {}
    live_tx = bool(int(source.get("live_transcode") or 0))
    copy_native = bool(int(source.get("copy_native") or 0))
    up = bool(int(source.get("upconvert_1080i") or 0))
    vbr = source.get("video_bitrate") or env.get("NEXREC_BROADCAST_VIDEO_BITRATE") or "12M"
    abr = source.get("audio_bitrate") or env.get("NEXREC_BROADCAST_AUDIO_BITRATE") or "192k"
    gop = str(env.get("NEXREC_GOP_FRAMES") or "60")
    preset = env.get("NEXREC_X264_PRESET") or "veryfast"

    # Copy only when the operator asked for native compressed IP and is not
    # forcing a transcode or the 1080i→1080p upconvert.
    if copy_native and not live_tx and not up:
        return ["-c", "copy"]

    vf: list[str] = []
    if up:
        # Sole allowed upconvert: 1080i → 1080p. yadif; scale is identity on 1920x1080.
        vf.append("yadif=mode=1:parity=-1:deint=interlaced")

    args: list[str] = []
    if vf:
        args += ["-vf", ",".join(vf)]
    args += [
        "-c:v", "libx264",
        "-preset", preset,
        "-profile:v", "high",
        "-level", "4.1",
        "-pix_fmt", "yuv420p",
        "-g", gop,
        "-bf", "2",
        "-b:v", vbr,
        "-maxrate", vbr,
        "-bufsize", "24M",
    ]
    if _keep_interlace(source) and not up:
        # Preserve interlaced raster when the source is 1080i. Harmless on progressive.
        args += ["-flags", "+ildct+ilme", "-x264-params", "tff=1"]
    args += ["-c:a", "aac", "-b:a", abr, "-ar", "48000", "-ac", "2"]
    return args


def metadata_args(when: datetime | None = None, fps: float = 30.0) -> list[str]:
    when = when or datetime.now()
    utc = when.astimezone(timezone.utc) if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return [
        "-metadata", f"creation_time={iso_z(utc)}",
        "-timecode", wallclock_timecode(when, fps=fps),
        "-metadata:s:v:0", f"timecode={wallclock_timecode(when, fps=fps)}",
    ]


def segment_args(
    out_pattern: str,
    segment_seconds: int = 300,
    at_clock: bool = True,
) -> list[str]:
    args = [
        "-f", "segment",
        "-segment_time", str(int(segment_seconds)),
    ]
    if at_clock:
        args += ["-segment_atclocktime", "1"]
    args += [
        "-reset_timestamps", "1",
        "-strftime", "1",
        "-segment_format", "mp4",
        "-segment_format_options", "movflags=faststart",
        out_pattern,
    ]
    return args


def record_argv(
    source: dict[str, Any],
    out_pattern: str,
    env: dict[str, str] | None = None,
    ffmpeg: str = "ffmpeg",
    segment_seconds: int = 300,
    when: datetime | None = None,
) -> list[str]:
    env = env or {}
    argv = [ffmpeg]
    argv += input_args(source)
    argv += ["-map", "0:v:0?", "-map", "0:a:0?"]
    if (source.get("source_type") or "") == "testsrc":
        # testsrc uses two lavfi inputs.
        argv = [ffmpeg] + input_args(source) + ["-map", "0:v:0", "-map", "1:a:0"]
    argv += metadata_args(when=when)
    argv += encode_args(source, env)
    at_clock = str(env.get("NEXREC_SEGMENT_AT_CLOCK", "1")).strip() not in ("0", "false", "no")
    argv += segment_args(out_pattern, segment_seconds=segment_seconds, at_clock=at_clock)
    return argv


def preview_argv(
    source: dict[str, Any],
    rtsp_url: str,
    env: dict[str, str] | None = None,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    """Proxy encode to MediaMTX. Separate process for IP; DeckLink should tee (next)."""
    env = env or {}
    argv = [ffmpeg]
    argv += input_args(source)
    if (source.get("source_type") or "") == "testsrc":
        argv += ["-map", "0:v:0", "-map", "1:a:0"]
    else:
        argv += ["-map", "0:v:0?", "-map", "0:a:0?"]
    argv += [
        "-vf", f"scale={PREVIEW_SIZE}:force_original_aspect_ratio=decrease,fps=30",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-profile:v", "baseline",
        "-pix_fmt", "yuv420p",
        "-b:v", PREVIEW_VBITRATE,
        "-g", "30",
        "-c:a", "aac",
        "-b:a", PREVIEW_ABITRATE,
        "-ar", "48000",
        "-ac", "2",
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        rtsp_url,
    ]
    return argv


def export_concat_argv(
    concat_path: str,
    dest_path: str,
    ss: float,
    duration: float,
    copy: bool,
    ffmpeg: str = "ffmpeg",
    env: dict[str, str] | None = None,
) -> list[str]:
    """Trim a concat list to [ss, ss+duration] relative to the first chunk."""
    env = env or {}
    argv = [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_path,
        "-ss", f"{ss:.3f}",
        "-t", f"{duration:.3f}",
    ]
    if copy:
        argv += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        vbr = env.get("NEXREC_BROADCAST_VIDEO_BITRATE") or "12M"
        abr = env.get("NEXREC_BROADCAST_AUDIO_BITRATE") or "192k"
        argv += [
            "-c:v", "libx264", "-preset", env.get("NEXREC_X264_PRESET") or "veryfast",
            "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
            "-b:v", vbr, "-c:a", "aac", "-b:a", abr, "-ar", "48000",
        ]
    argv += ["-movflags", "+faststart", dest_path]
    return argv


def faststart_argv(src: str, dest: str, ffmpeg: str = "ffmpeg") -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-i", src, "-c", "copy", "-movflags", "+faststart", dest,
    ]
