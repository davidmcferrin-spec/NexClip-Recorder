# Per-input monitoring and intelligence

Owner-required product scope. Flags are **per input**, default **off**.
The Premiere/FCPX **record path is unchanged** — analyzers run as sidecars on
closed chunks or the export window (FFmpeg filters, not a custom muxer).

Canonical host sizing remains README **Hardware recommendations**.

## Flags (Inputs UI + `inputs-example.env`)

| Flag | Env | v0 | Later |
| --- | --- | --- | --- |
| SCTE-35 / 104 / 224 | `FEAT_SCTE` | Persist structured `events` (type, summary, pts/timecode). SCTE-35 from `ffprobe` packets when present. SCTE-224 HTTP ingest stub. | Live MPEG-TS tap so SCTE-35 is not lost on MP4 remux; DeckLink VANC for SCTE-104 |
| Freeze / bars / black | `FEAT_AV_ANOMALY` | FFmpeg `blackdetect` + `freezedetect` after chunk close; log only if duration ≥ threshold | SMPTE color-bar detector (no FFmpeg filter today); DeckLink-side analyzers |
| Captions 608/708 | `FEAT_CAPTIONS` | Presence log + SRT extract (`0:s` then lavfi `subcc`) into `captions` + **FTS5** | Full 708 service map, burn-in optional |
| Transcription + diarization | `FEAT_TRANSCRIBE` | Off unless `NEXREC_TRANSCRIBE_ENGINE` + `NEXREC_TRANSCRIBE_CMD`. Pluggable JSON ingest | whisper.cpp / faster-whisper + diarization on GPU |
| Nielsen watermark presence | `FEAT_NIELSEN` | Best-effort **presence** log (appears present or absent) on the chunk timeline. **Not** audit-grade decode. No SID, watermark time, or layer | Swap the stub (`NEXREC_NIELSEN_PRESENCE_CMD` or `NielsenPresenceDetector`). Decoder SDK is **not** integrated |
| Live monitors | `FEAT_MONITORS` | UI panes: WFM, vectorscope, VU, 64-ch RTA placeholders | Decode from preview/proxy; 64-ch from SDI/AES not stereo AAC |
| CALM / LKFS | (export editor) | Line chart + ebur128 job on marked I/O. ITU-R BS.1770 / ATSC A/85 **−24 LKFS** | Faster framed logs, true-peak alerts |

Duration thresholds (seconds, per input):

- `THRESH_FREEZE_S` default **2**
- `THRESH_BLACK_S` default **2**
- `THRESH_BARS_S` default **5** (reserved for the bars detector)

## Storage

- SQLite `events` — SCTE, freeze, black, bars stub, CC presence, Nielsen presence (not a decode)
- JSONL sidecar `storage/inputs/<id>/events/<chunk>.jsonl`
- SQLite `captions` + virtual `captions_fts` (FTS5) for caption **and** transcript text
- SQLite `loudness_samples` — momentary/integrated LKFS vs wall-clock
- `analyze_jobs` — PHP enqueues; `nexrec-analyze.py` drains (PHP never shells FFmpeg)

Timecodes are NTP wall-clock ISO-8601 Z plus `HH:MM:SS:FF`, offset from the
chunk `start_at` by filter PTS.

Search API: `POST /api/recorder?action=search_text` `{q, input_id?}`.
Events: `action=events_list`. SCTE-224: `action=scte224_ingest`.
LKFS: `action=loudness_chart` / `loudness_enqueue`.

## Signal path

```
record@input  →  native MP4 segments (unchanged, H.264+AAC faststart)
              →  index chunk
              →  analyze_chunk() if any FEAT_* on   (scale=320 blackdetect/freezedetect,
                                                     ffprobe SCTE, optional CC/ASR,
                                                     Nielsen presence stub — not a decode)

preview@input →  proxy 960×540 → MediaMTX WHEP   (IP sources)
record@input  →  DeckLink tee of the same proxy   (exclusive-open; no preview@ unit)
              →  Live WFM / vector / VU / RTA  (v0: graticule placeholders;
                                                NEXT: WebAudio / canvas from this decode)

export editor →  mark I/O → loudness_enqueue → ebur128 on concat+trim window
```

## Engine options (transcription)

Do not bake API keys. Station env:

- `NEXREC_TRANSCRIBE_ENGINE=none|whisper.cpp|faster-whisper`
- `NEXREC_TRANSCRIBE_CMD` with `{input}` `{output}` — write JSON
  `[{t_start,t_end,speaker,text},...]`

**GPU:** always-on transcribe on ~8×1080 plus NVENC preview/export will contend
with the hardware recommendation (NVIDIA + 12–16 cores). Keep ASR off unless
the box has spare NVENC/CUDA (whisper.cpp CUDA or faster-whisper).

## Nielsen presence (not a decode)

Owner decision: **do not** integrate the Nielsen Decoder SDK. **Do not** log
SID, watermark timestamps, or code layers. FFmpeg has no NAES2 / NW / CBET
decoder, and this repo does not add a licensed SDK (or a wrapper around one).

When `FEAT_NIELSEN` is on, each closed chunk is passed to a presence detector.
The builtin detector is a **stub** (`worker/nexrec_nielsen.py`,
`NielsenPresenceDetector` / `StubNielsenPresenceDetector`) meant to be swapped
later. It does not read a Nielsen code. It records whether a watermark
**appears** present or absent in time windows (default **30 s**,
`NEXREC_NIELSEN_WINDOW_S`), coalesced into spans, aligned to the chunk
`start_at`:

- `t_start` / `t_end` — NTP wall-clock ISO-8601 Z
- `timecode` — `HH:MM:SS:FF` from that wall clock
- `kind=nielsen`, `subtype=present|absent`

Every event sets `audit_grade: false` and `decoded: false`. A stub `absent`
means **this stub did not detect a watermark**. It is not proof of absence
and **not** an audit-grade decode.

Optional swap without editing code: `NEXREC_NIELSEN_PRESENCE_CMD` (Setup field
`intelligence.nielsen_cmd`) with `{input}` and `{output}` (and optional
`{duration}`). The command writes a
JSON array of `{pts, pts_end, present}` only. `sid`, `layer`, and watermark
`timestamp` fields are ignored. Those logs are still best-effort presence,
not an SDK decode. Leave the command blank to keep the builtin stub.

## NEXT (not blocking v0 merge)

DeckLink **capture**, the in-process preview tee, and SDI lock/format via
`tools/decklink-status` are in the product. Still later:

- DeckLink-side waveform/vector/audio meters and SCTE-104 VANC (Blackmagic SDK)
- Live MPEG-TS SCTE-35 tap (MP4 remux drops data PIDs)
- Real SMPTE bars detector (histogram / template); duration gate already stored
- 64-channel RTA performance on SDI embed / AES67 (proxy AAC is stereo)
- Transcription GPU pool separate from NVENC record/preview
- UI filter/search for SCTE events (API + table exist; timeline overlay later)
