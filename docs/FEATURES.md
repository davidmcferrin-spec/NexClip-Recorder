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
| Nielsen watermark | `FEAT_NIELSEN` | Honest stub event: **no FFmpeg decoder** | Licensed Nielsen Audio Decoder SDK |
| Live monitors | `FEAT_MONITORS` | UI panes: WFM, vectorscope, VU, 64-ch RTA placeholders | Decode from preview/proxy; 64-ch from SDI/AES not stereo AAC |
| CALM / LKFS | (export editor) | Line chart + ebur128 job on marked I/O. ITU-R BS.1770 / ATSC A/85 **−24 LKFS** | Faster framed logs, true-peak alerts |

Duration thresholds (seconds, per input):

- `THRESH_FREEZE_S` default **2**
- `THRESH_BLACK_S` default **2**
- `THRESH_BARS_S` default **5** (reserved for the bars detector)

## Storage

- SQLite `events` — SCTE, freeze, black, bars stub, CC presence, Nielsen stub
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
                                                     ffprobe SCTE, optional CC/ASR)

preview@input →  proxy 960×540 → MediaMTX WHEP
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

## Nielsen (honest)

FFmpeg has **no** Nielsen NAES2 / NW / CBET decoder. Nielsen’s Audio Decoder
SDK is proprietary, license-file gated (CBET L1), and historically Linux
CentOS — not redistributable. Open-source TS tools (`nielsen_inspector`)
**require that SDK**. v0 stores a one-shot `nielsen/sdk_missing` event when
the input flag is on. Optional `NEXREC_NIELSEN_CMD` (Setup field
`intelligence.nielsen_cmd`) is the licensed wrapper if a station has the SDK.
Blank still records `sdk_missing` only.

## NEXT (not blocking v0 merge)

- DeckLink-side waveform/vector/audio meters and SCTE-104 VANC (Blackmagic SDK)
- Live MPEG-TS SCTE-35 tap (MP4 remux drops data PIDs)
- Real SMPTE bars detector (histogram / template); duration gate already stored
- 64-channel RTA performance on SDI embed / AES67 (proxy AAC is stereo)
- Transcription GPU pool separate from NVENC record/preview
- Nielsen SDK integration behind `NEXREC_NIELSEN_CMD`
- UI filter/search for SCTE events (API + table exist; timeline overlay later)
