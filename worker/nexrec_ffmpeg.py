#!/usr/bin/env python3
"""Assemble FFmpeg argv for ingest, preview, and export.

Argv builders do not spawn FFmpeg. ``pin_video_encoder`` is the one place
that probes the GPU and the encoder list, and only the workers call it.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from typing import Any, Callable, NamedTuple

from nexrec_util import as_utc, iso_z, wallclock_timecode

SOURCE_TYPES = ("rtsp", "srt", "udp", "tcp", "rtp", "decklink", "testsrc")

# Proxy preview — monitoring quality, not the recorded mezzanine.
PREVIEW_SIZE = "960x540"
PREVIEW_VBITRATE = "1500k"
PREVIEW_ABITRATE = "96k"

# x264 speed names from Setup map onto NVENC p1 (fast) … p7 (slow).
_X264_TO_NVENC = {
    "ultrafast": "p1",
    "superfast": "p1",
    "veryfast": "p3",
    "faster": "p4",
    "fast": "p5",
    "medium": "p6",
    "slow": "p7",
    "slower": "p7",
    "veryslow": "p7",
}
_NVENC_PRESETS = {f"p{i}" for i in range(1, 8)}
_CPU_ENCODER = {"libx264", "x264", "cpu", "off", "0"}
_GPU_ENCODER = {"nvenc", "h264_nvenc", "nvidia", "gpu", "on", "1"}
_OFF = {"0", "false", "no", "off"}

# Broadcast floor. 1080p60 and upconverted 1080i step up from here.
_H264_LEVELS = (
    ("4.1", 245760, 8192),
    ("4.2", 522240, 8704),
    ("5.0", 589824, 22080),
    ("5.1", 983040, 36864),
    ("5.2", 2073600, 36864),
)

_STATUS_RE = re.compile(
    r"(?P<size>525|625|720|1080|2160)\s*(?P<scan>PsF|i|p)\s*(?P<rate>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_STATUS_SIZE = {
    "525": (720, 486),
    "625": (720, 576),
    "720": (1280, 720),
    "1080": (1920, 1080),
    "2160": (3840, 2160),
}


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
        # Unset audio input follows Desktop Video, which is often analog.
        # SDI program audio is the embedded pairs. Channel count stays at
        # FFmpeg's default of 2 (pair 1) until multi-pair metering exists.
        args += ["-f", "decklink", "-audio_input", "embedded"]
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


def _explicit_flag(source: dict[str, Any], key: str) -> bool | None:
    """None when the key was omitted. False for 0/false. True otherwise."""
    if key not in source:
        return None
    v = source.get(key)
    if v is None or v == "":
        return None
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "off")
    if isinstance(v, (int, float)):
        return bool(int(v))
    return bool(v)


class SignalMode(NamedTuple):
    width: int
    height: int
    frame_fps: float
    field_fps: float
    interlaced: bool
    field: str | None


def _nominal_rate(value: float) -> float:
    """Map a printed Blackmagic rate onto the exact NTSC fraction."""
    if abs(value - 59.94) < 0.02:
        return 60000 / 1001
    if abs(value - 29.97) < 0.02:
        return 30000 / 1001
    if abs(value - 23.98) < 0.02:
        return 24000 / 1001
    return value


def _mode(
    width: int,
    height: int,
    frame_num: int,
    frame_den: int,
    interlaced: bool,
    field: str | None,
) -> SignalMode:
    frame = frame_num / frame_den
    fields = frame * 2 if interlaced else frame
    return SignalMode(width, height, frame, fields, interlaced, field)


# FFmpeg DeckLink format_code → raster. Names are case-sensitive (Hp60 ≠ hp60).
# Interlaced frame_fps is pictures per second; field_fps is the nameplate field rate.
_FORMAT_CODE: dict[str, SignalMode] = {
    "ntsc": _mode(720, 486, 30000, 1001, True, "bff"),
    "nt23": _mode(720, 486, 24000, 1001, True, "bff"),
    "ntsp": _mode(720, 486, 30000, 1001, False, None),
    "pal": _mode(720, 576, 25, 1, True, "tff"),
    "palp": _mode(720, 576, 25, 1, False, None),
    "23ps": _mode(1920, 1080, 24000, 1001, False, None),
    "24ps": _mode(1920, 1080, 24, 1, False, None),
    "Hp25": _mode(1920, 1080, 25, 1, False, None),
    "Hp29": _mode(1920, 1080, 30000, 1001, False, None),
    "Hp30": _mode(1920, 1080, 30, 1, False, None),
    "Hp50": _mode(1920, 1080, 50, 1, False, None),
    "Hp59": _mode(1920, 1080, 60000, 1001, False, None),
    "Hp60": _mode(1920, 1080, 60, 1, False, None),
    "Hi50": _mode(1920, 1080, 25, 1, True, "tff"),
    "Hi59": _mode(1920, 1080, 30000, 1001, True, "tff"),
    "Hi60": _mode(1920, 1080, 30, 1, True, "tff"),
    "hp50": _mode(1280, 720, 50, 1, False, None),
    "hp59": _mode(1280, 720, 60000, 1001, False, None),
    "hp60": _mode(1280, 720, 60, 1, False, None),
    "4k23": _mode(3840, 2160, 24000, 1001, False, None),
    "4k24": _mode(3840, 2160, 24, 1, False, None),
    "4k25": _mode(3840, 2160, 25, 1, False, None),
    "4k29": _mode(3840, 2160, 30000, 1001, False, None),
    "4k30": _mode(3840, 2160, 30, 1, False, None),
    "4k50": _mode(3840, 2160, 50, 1, False, None),
    "4k59": _mode(3840, 2160, 60000, 1001, False, None),
    "4k60": _mode(3840, 2160, 60, 1, False, None),
}


def parse_signal_mode(text: str) -> SignalMode | None:
    """Parse a DeckLink status name or an FFmpeg format code.

    Status names use the field rate for interlaced modes (`1080i59.94` is
    29.97 frames). PsF is progressive. 1080i is top field first; 525i is bottom.
    """
    raw = (text or "").strip()
    if not raw or raw.lower() == "unknown":
        return None
    coded = _FORMAT_CODE.get(raw)
    if coded is not None:
        return coded
    match = _STATUS_RE.search(raw)
    if not match:
        return None
    width, height = _STATUS_SIZE[match.group("size")]
    rate = _nominal_rate(float(match.group("rate")))
    scan = match.group("scan").lower()
    if scan == "i":
        field = "bff" if match.group("size") == "525" else "tff"
        return SignalMode(width, height, rate / 2.0, rate, True, field)
    return SignalMode(width, height, rate, rate, False, None)


def source_signal_mode(source: dict[str, Any]) -> SignalMode | None:
    """Format code wins when set, because that is the raster FFmpeg opens.

    Auto-detect uses signal_mode from the DeckLink status probe.
    """
    fmt = str(source.get("decklink_format") or source.get("DECKLINK_FORMAT") or "").strip()
    if fmt:
        parsed = parse_signal_mode(fmt)
        if parsed is not None:
            return parsed
    mode = str(source.get("signal_mode") or "").strip()
    if mode:
        return parse_signal_mode(mode)
    return None


def record_raster(source: dict[str, Any]) -> SignalMode | None:
    """Raster written to the file. Upconvert turns 1080i into progressive at field rate."""
    mode = source_signal_mode(source)
    if mode is None:
        return None
    if bool(int(source.get("upconvert_1080i") or 0)) and mode.interlaced:
        return SignalMode(mode.width, mode.height, mode.field_fps, mode.field_fps, False, None)
    return mode


def h264_level(width: int, height: int, fps: float) -> str:
    """Lowest broadcast level whose macroblock rate covers this raster. Floor is 4.1."""
    mb = ((width + 15) // 16) * ((height + 15) // 16)
    rate = mb * fps
    for level, mbps, max_fs in _H264_LEVELS:
        if mb <= max_fs and rate <= mbps:
            return level
    return _H264_LEVELS[-1][0]


def preview_unit_allowed(source: dict[str, Any]) -> bool:
    """DeckLink sub-devices are exclusive-open. Preview is teed in the record process."""
    t = (source.get("source_type") or source.get("type") or "").lower()
    return t != "decklink"


def preview_publish_url(preview_path: str, env: dict[str, str] | None = None) -> str:
    env = env or {}
    path = (preview_path or "in0").strip() or "in0"
    jwt = env.get("NEXREC_PUBLISH_JWT") or ""
    base = (env.get("NEXREC_MEDIAMTX_RTSP") or "rtsp://127.0.0.1:8554").rstrip("/")
    rtsp = f"{base}/{path}"
    if jwt:
        rtsp += ("&" if "?" in rtsp else "?") + "jwt=" + jwt
    return rtsp


def decklink_filter_complex(source: dict[str, Any]) -> str:
    """One DeckLink input, two outputs: record raster + proxy preview."""
    prev = (
        f"yadif=mode=0:parity=-1:deint=interlaced,"
        f"scale={PREVIEW_SIZE}:force_original_aspect_ratio=decrease,"
        f"fps=30,format=yuv420p"
    )
    if _explicit_flag(source, "upconvert_1080i"):
        return (
            "[0:v]split=2[vr0][vp0];"
            "[vr0]yadif=mode=1:parity=-1:deint=interlaced[vrec];"
            f"[vp0]{prev}[vprev];"
            "[0:a]asplit=2[arec][aprev]"
        )
    return (
        "[0:v]split=2[vrec][vp0];"
        f"[vp0]{prev}[vprev];"
        "[0:a]asplit=2[arec][aprev]"
    )


def _encoder_choice(env: dict[str, str] | None) -> str:
    return str((env or {}).get("NEXREC_VIDEO_ENCODER") or "").strip().lower()


def use_nvenc(env: dict[str, str] | None = None) -> bool:
    """True only after a worker has pinned the encoder to NVENC."""
    return _encoder_choice(env) in _GPU_ENCODER


def nvenc_preset(name: str) -> str:
    preset = (name or "veryfast").strip().lower()
    if preset in _NVENC_PRESETS:
        return preset
    return _X264_TO_NVENC.get(preset, "p3")


def nvidia_device() -> str | None:
    for node in ("/dev/nvidiactl", "/dev/nvidia0"):
        if os.path.exists(node):
            return node
    return None


def nvenc_blocked(ffmpeg: str = "ffmpeg") -> str | None:
    """None when this process can encode with h264_nvenc."""
    node = nvidia_device()
    if node is None:
        return "no NVIDIA device"
    if not os.access(node, os.R_OK | os.W_OK):
        return f"{node} is not writable by this user"
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"could not list ffmpeg encoders ({exc})"
    blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if "h264_nvenc" not in blob:
        return "ffmpeg has no h264_nvenc encoder"
    return None


def pin_video_encoder(
    env: dict[str, str],
    ffmpeg: str = "ffmpeg",
    probe: Callable[[str], str | None] | None = None,
) -> tuple[dict[str, str], str]:
    """Choose libx264 or nvenc for this process.

    Unset or ``auto`` uses the GPU when the device is writable and FFmpeg
    lists ``h264_nvenc``. ``libx264`` and ``nvenc`` are explicit.
    ``NEXREC_ENABLE_NVENC=0`` keeps the CPU encoder unless NVENC is explicit.
    """
    out = dict(env)
    choice = _encoder_choice(out) or "auto"
    if choice in _CPU_ENCODER:
        out["NEXREC_VIDEO_ENCODER"] = "libx264"
        return out, "libx264"
    if choice in _GPU_ENCODER:
        out["NEXREC_VIDEO_ENCODER"] = "nvenc"
        return out, "h264_nvenc"
    if str(out.get("NEXREC_ENABLE_NVENC") or "").strip().lower() in _OFF:
        out["NEXREC_VIDEO_ENCODER"] = "libx264"
        return out, "libx264 (NEXREC_ENABLE_NVENC=0)"
    reason = (probe or nvenc_blocked)(ffmpeg)
    if reason:
        out["NEXREC_VIDEO_ENCODER"] = "libx264"
        if reason == "no NVIDIA device":
            return out, "libx264"
        return out, f"libx264 ({reason})"
    out["NEXREC_VIDEO_ENCODER"] = "nvenc"
    return out, "h264_nvenc"


def _h264_video_args(
    env: dict[str, str],
    *,
    preset: str,
    profile: str,
    level: str,
    gop: str,
    bitrate: str,
    maxrate: str | None = None,
    bufsize: str | None = None,
    bf: str = "2",
    interlaced: str | None = None,
    low_latency: bool = False,
) -> list[str]:
    if use_nvenc(env):
        args = ["-c:v", "h264_nvenc", "-preset", "p1" if low_latency else nvenc_preset(preset)]
        if low_latency:
            args += ["-tune", "ull", "-rc", "cbr", "-bf", "0"]
        else:
            args += ["-rc", "cbr", "-bf", bf]
        args += ["-profile:v", profile]
        if level:
            args += ["-level", level]
        args += [
            "-pix_fmt", "yuv420p",
            "-g", gop,
            "-b:v", bitrate,
        ]
        if maxrate:
            args += ["-maxrate", maxrate]
        if bufsize:
            args += ["-bufsize", bufsize]
        # +ildct is honored by h264_nvenc. x264-params and +ilme are not.
        if interlaced == "tff":
            args += ["-flags", "+ildct", "-field_order", "tt"]
        elif interlaced == "bff":
            args += ["-flags", "+ildct", "-field_order", "bb"]
        return args
    args = [
        "-c:v", "libx264",
        "-preset", "ultrafast" if low_latency else preset,
    ]
    if low_latency:
        args += ["-tune", "zerolatency"]
    args += ["-profile:v", profile]
    if level:
        args += ["-level", level]
    args += ["-pix_fmt", "yuv420p"]
    if not low_latency:
        args += ["-g", gop, "-bf", bf]
    else:
        args += ["-g", gop]
    args += ["-b:v", bitrate]
    if maxrate and not low_latency:
        args += ["-maxrate", maxrate]
    if bufsize and not low_latency:
        args += ["-bufsize", bufsize]
    if not low_latency and interlaced == "tff":
        args += ["-flags", "+ildct+ilme", "-x264-params", "tff=1"]
    elif not low_latency and interlaced == "bff":
        args += ["-flags", "+ildct+ilme", "-x264-params", "bff=1"]
    return args


def encode_args(
    source: dict[str, Any],
    env: dict[str, str] | None = None,
    include_vf: bool = True,
) -> list[str]:
    env = env or {}
    live_tx = bool(int(source.get("live_transcode") or 0))
    copy_native = bool(int(source.get("copy_native") or 0))
    up = bool(int(source.get("upconvert_1080i") or 0))
    # SDI is uncompressed. Bitstream copy is not a recording.
    if (source.get("source_type") or "").lower() == "decklink":
        live_tx = True
        copy_native = False
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
    if vf and include_vf:
        args += ["-vf", ",".join(vf)]
    raster = record_raster(source)
    if raster is not None:
        level = h264_level(raster.width, raster.height, raster.frame_fps)
    elif (source.get("source_type") or "").lower() == "decklink":
        # Unknown lock (no status helper). 4.2 covers 1080p60; 1080i still fits.
        level = "4.2"
    else:
        level = "4.1"
    field = None
    # Field flags only for a locked interlaced raster. Progressive 1080p must
    # stay progressive; keep_interlace does not override a known mode.
    if raster is not None and raster.interlaced and raster.field and not up:
        field = raster.field
    args += _h264_video_args(
        env,
        preset=preset,
        profile="high",
        level=level,
        gop=gop,
        bitrate=vbr,
        maxrate=vbr,
        bufsize="24M",
        interlaced=field,
    )
    args += ["-c:a", "aac", "-b:a", abr, "-ar", "48000", "-ac", "2"]
    return args


def metadata_args(when: datetime | None = None, fps: float = 30.0) -> list[str]:
    utc = as_utc(when)
    tc = wallclock_timecode(utc, fps=fps)
    # Stream metadata is what the MP4 carries. -timecode maps to gop_timecode,
    # which libx264 does not consume.
    return [
        "-metadata", f"creation_time={iso_z(utc)}",
        "-metadata:s:v:0", f"timecode={tc}",
    ]


def _metadata_for(source: dict[str, Any], when: datetime | None) -> list[str]:
    raster = record_raster(source)
    fps = raster.frame_fps if raster is not None else 30.0
    return metadata_args(when=when, fps=fps)


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


def _proxy_video_args(env: dict[str, str]) -> list[str]:
    return _h264_video_args(
        env,
        preset="ultrafast",
        profile="baseline",
        level="",
        gop="30",
        bitrate=PREVIEW_VBITRATE,
        low_latency=True,
    )


def _preview_audio_args() -> list[str]:
    """Opus for WHEP. MediaMTX does not transcode, and browsers cannot play AAC over WebRTC.

    Recordings stay AAC. This is only the proxy publish.
    """
    return [
        "-c:a", "libopus",
        "-b:a", PREVIEW_ABITRATE,
        "-ar", "48000",
        "-ac", "2",
        "-application", "lowdelay",
    ]


def _preview_output_args(rtsp_url: str, env: dict[str, str] | None = None) -> list[str]:
    return [
        "-map", "[vprev]",
        "-map", "[aprev]",
        *_proxy_video_args(env or {}),
        *_preview_audio_args(),
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        rtsp_url,
    ]


def _decklink_tee_argv(
    source: dict[str, Any],
    out_pattern: str,
    preview_rtsp: str,
    env: dict[str, str],
    ffmpeg: str,
    segment_seconds: int,
    when: datetime | None,
) -> list[str]:
    """Single process: native clocked segments + proxy RTSP. One DeckLink open."""
    at_clock = str(env.get("NEXREC_SEGMENT_AT_CLOCK", "1")).strip() not in ("0", "false", "no")
    argv = [ffmpeg]
    argv += input_args(source)
    argv += ["-filter_complex", decklink_filter_complex(source)]
    argv += ["-map", "[vrec]", "-map", "[arec]"]
    argv += _metadata_for(source, when)
    # yadif for upconvert lives in the filter graph, not a second -vf.
    argv += encode_args(source, env, include_vf=False)
    argv += segment_args(out_pattern, segment_seconds=segment_seconds, at_clock=at_clock)
    argv += _preview_output_args(preview_rtsp, env)
    return argv


def record_argv(
    source: dict[str, Any],
    out_pattern: str,
    env: dict[str, str] | None = None,
    ffmpeg: str = "ffmpeg",
    segment_seconds: int = 300,
    when: datetime | None = None,
    preview_rtsp: str | None = None,
) -> list[str]:
    env = env or {}
    if preview_rtsp and (source.get("source_type") or "").lower() == "decklink":
        return _decklink_tee_argv(
            source, out_pattern, preview_rtsp, env, ffmpeg, segment_seconds, when,
        )
    argv = [ffmpeg]
    argv += input_args(source)
    argv += ["-map", "0:v:0?", "-map", "0:a:0?"]
    if (source.get("source_type") or "") == "testsrc":
        # testsrc uses two lavfi inputs.
        argv = [ffmpeg] + input_args(source) + ["-map", "0:v:0", "-map", "1:a:0"]
    argv += _metadata_for(source, when)
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
    """Proxy encode to MediaMTX. IP sources only — DeckLink must not open a second capture."""
    env = env or {}
    argv = [ffmpeg]
    argv += input_args(source)
    if (source.get("source_type") or "") == "testsrc":
        argv += ["-map", "0:v:0", "-map", "1:a:0"]
    else:
        argv += ["-map", "0:v:0?", "-map", "0:a:0?"]
    argv += [
        "-vf", f"scale={PREVIEW_SIZE}:force_original_aspect_ratio=decrease,fps=30",
        *_proxy_video_args(env),
        *_preview_audio_args(),
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        rtsp_url,
    ]
    return argv


_OUT_US = re.compile(r"^out_time_us=(\d+)\s*$")
_OUT_CLOCK = re.compile(r"^out_time=(\d+):(\d+):(\d+(?:\.\d+)?)\s*$")
_STAT_TIME = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def _clock_seconds(hours: str, minutes: str, seconds: str) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_export_progress(line: str) -> float | None:
    """Seconds encoded so far, from an FFmpeg -progress line or a stats time= field."""
    text = line.strip()
    us = _OUT_US.match(text)
    if us:
        return int(us.group(1)) / 1_000_000.0
    clock = _OUT_CLOCK.match(text)
    if clock:
        return _clock_seconds(clock.group(1), clock.group(2), clock.group(3))
    if text.startswith("out_time"):
        return None
    stat = _STAT_TIME.search(text)
    if stat:
        return _clock_seconds(stat.group(1), stat.group(2), stat.group(3))
    return None


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
        argv += _h264_video_args(
            env,
            preset=env.get("NEXREC_X264_PRESET") or "veryfast",
            profile="high",
            level="4.1",
            gop=str(env.get("NEXREC_GOP_FRAMES") or "60"),
            bitrate=vbr,
            maxrate=vbr,
            bufsize="24M",
        )
        argv += ["-c:a", "aac", "-b:a", abr, "-ar", "48000"]
    argv += ["-movflags", "+faststart", dest_path]
    return argv


def faststart_argv(src: str, dest: str, ffmpeg: str = "ffmpeg") -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-i", src, "-c", "copy", "-movflags", "+faststart", dest,
    ]


def detect_argv(
    path: str,
    freeze_s: float = 2.0,
    black_s: float = 2.0,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    """Sidecar freeze/black detect. Does not touch the record/export encode path."""
    vf = (
        f"scale=320:-2,"
        f"blackdetect=d={float(black_s):g}:pix_th=0.10,"
        f"freezedetect=n=0.003:d={float(freeze_s):g}"
    )
    return [
        ffmpeg, "-hide_banner", "-nostdin",
        "-i", path, "-an", "-vf", vf, "-f", "null", "-",
    ]


def ebur128_argv(
    path: str,
    ffmpeg: str = "ffmpeg",
    ss: float | None = None,
    duration: float | None = None,
) -> list[str]:
    """ITU-R BS.1770 loudness via FFmpeg ebur128 (ATSC A/85 / CALM review)."""
    argv = [ffmpeg, "-hide_banner", "-nostdin"]
    if ss is not None:
        argv += ["-ss", f"{float(ss):.3f}"]
    argv += ["-i", path]
    if duration is not None:
        argv += ["-t", f"{float(duration):.3f}"]
    argv += ["-vn", "-af", "ebur128=peak=true:framelog=verbose", "-f", "null", "-"]
    return argv


def ebur128_concat_argv(
    concat_path: str,
    ss: float,
    duration: float,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-nostdin",
        "-f", "concat", "-safe", "0",
        "-i", concat_path,
        "-ss", f"{float(ss):.3f}",
        "-t", f"{float(duration):.3f}",
        "-vn", "-af", "ebur128=peak=true:framelog=verbose",
        "-f", "null", "-",
    ]


def extract_srt_argv(path: str, dest: str, ffmpeg: str = "ffmpeg") -> list[str]:
    """Copy in-band subtitle streams to SRT (CEA-608/708 often need subcc lavfi)."""
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-i", path, "-map", "0:s:0?", "-c:s", "srt", dest,
    ]


def extract_subcc_argv(path: str, dest: str, ffmpeg: str = "ffmpeg") -> list[str]:
    """Pull 608/708 from video VANC via lavfi movie=…[out0+subcc]."""
    # Quotes: lavfi movie filter needs the path escaped for ':' in Windows; Linux paths are fine.
    spec = f"movie={path}[out0+subcc]"
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-f", "lavfi", "-i", spec,
        "-map", "0:s:0?", "-c:s", "srt", dest,
    ]


def scte_probe_argv(path: str, ffprobe: str = "ffprobe") -> list[str]:
    return [
        ffprobe, "-hide_banner", "-loglevel", "error",
        "-show_packets", "-show_streams", "-print_format", "json",
        path,
    ]
