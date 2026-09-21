# ADR 0020 excerpt — Mode 2 (`continuous_24x7`)

Source: `NexClip/docs/decisions/0020-recording-and-scheduling.md` (local tree).
The full ADR also covers calendar, Mode 1, and admin UI.

## What NexCLIP Recorder is

Mode 2: **24/7 continuous, 5-minute chunks** (VELA replacement). The node is
always recording. The calendar does **not** start/stop capture. Schedule-driven
and manual **export requests** stitch the buffer and deliver into the MAM.

Mode 1 (`scheduled_with_safety_net`) is a different node: it starts/stops
around events plus hourly safety chunks. **Not this product’s core.**

## Node contract (implemented on NexClip; node software is this repo)

- Separate `RECORDER_ENROLLMENT_SECRET` (not the worker fleet secret).
- Register → check-in with per-slot presence + Mode 2 `buffer_earliest_at`
  (one timestamp per input, not a row per chunk).
- Poll `GET .../export-requests/next` (204 = idle). Lazy `_sync_scheduled_export_requests`
  on the hub when the node asks.
- `start` claims a pending request → `capture_id`; `complete`/`fail` are the
  same capture endpoints Mode 1 uses (`capture_type='export'`).
- Extraction/stitch runs **on the recorder node**. NexClip receives
  `delivered_path` and ingests.

## Recorder-type enum today

`decklink` | `srt` | `ndi` only. IP flavors (RTSP/UDP/RTP/TCP) are a
documented mapping gap until NexClip extends the enum.

## Non-goal

“The node-side capture software itself … separate applications outside this
[NexClip] repo.” That software is NexCLIP Recorder.
