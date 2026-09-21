# NexClip Mode 2 — continuous_24x7 export-request contract

This **is** NexCLIP Recorder. Replaces the invented
`/api/studio/recorder-schedule` poll + inbound webhook from the first
scaffold. Sources: local `NexClip/api/app/recorders/{schemas,routes}.py`
and ADR 0020 (`docs/references/`).

NexClip **never** starts or stops capture. This box records 24/7 in
5-minute MP4 chunks. Calendar and operators create **export requests**;
the node stitches the buffer and reports `delivered_path`.

**Mode 1 `scheduled_with_safety_net` is out of scope** (older simpler
system). Not a runtime mode and not a later switch. Do not call
`GET .../schedule`. Mode 1 looks ahead to *start capture*; Mode 2 looks at
*completed* windows to *export from the already-running buffer*. Export-request
poll only.

Node auth is **not** NexAPP SSO. Enrollment secret + per-node bearer.
NexClip does not open inbound holes to the recorder (node polls out).

## Register

```
POST {NEXCLIP_BASE_URL}{NEXCLIP_API_PREFIX}/recorders/register
X-Enrollment-Secret: {NEXCLIP_ENROLLMENT_SECRET}
{ "hostname": "dcwasof2nexrec01", "recorder_type": "srt", "num_slots": 8 }
→ 201 { "recorder_id", "token" }
```

`recorder_type`: `decklink` | `srt` | `ndi` only. Mapping on this node:

| Local `source_type` | Sent as |
| --- | --- |
| `decklink` | `decklink` |
| `srt` | `srt` |
| `ndi` (future) | `ndi` |
| `rtsp` / `udp` / `tcp` / `rtp` / `testsrc` | `srt` (gap — document until NexClip extends the enum) |

Station override: `NEXCLIP_RECORDER_TYPE`. `num_slots` is clamped to 4–8.

Token is stored in SQLite `settings` (`nexclip_recorder_id`,
`nexclip_node_token`) or `NEXCLIP_RECORDER_ID` / `NEXCLIP_NODE_TOKEN`.

## Check-in

```
POST .../recorders/{id}/checkin
Authorization: Bearer {token}
{ "status": "online"|"error",
  "inputs": [{ "slot": 1, "reported_label": "studio-a", "is_present": true,
               "buffer_earliest_at": "2026-09-01T12:00:00Z" }] }
→ 204
```

`buffer_earliest_at` is MIN(ready native chunk `start_at`) for that slot
(how far the local buffer reaches). Mode 2 always sends it when known.

Local inputs map via `inputs.nexclip_slot` (1–8). Enabled inputs **without**
an assigned slot do not appear on the hub.

## Export-request poll (Mode 2)

```
GET .../recorders/{id}/inputs/{slot}/export-requests/next
→ 200 NextExportRequestResponse
→ 204 idle
→ 409 input_not_assigned_to_a_channel
```

200 body:

```json
{
  "export_request_id": "…",
  "title": "Morning Show",
  "range_start": "2026-09-21T14:00:00Z",
  "range_end": "2026-09-21T15:00:00Z",
  "library_id": null,
  "relative_dir": "Show/2026/09/CLEAN",
  "filename": "SHOW_20260921_1400_CLEAN.mp4"
}
```

## Claim + deliver

```
POST .../export-requests/{request_id}/start  → 201 { "capture_id" }
```

Recorder enqueues a local concat/trim export for `[range_start, range_end]`
on the input mapped to that slot (same worker as the UI export editor).
When the MP4 is `done`:

```
POST .../captures/{capture_id}/complete  { "delivered_path": "/var/lib/nexrec/storage/exports/….mp4" }
→ 204
```

On failure:

```
POST .../captures/{capture_id}/fail  { "error_detail": "…" }  → 204
```

`relative_dir` / `filename` / `library_id` are hub pathing — v0 writes
our usual export path and reports that as `delivered_path`. Copying into
the MAM library tree on a shared mount is NEXT if the station NAS layout
requires it.

## Worker

`nexrec-nexclip.py` (systemd timer ~60s): register if needed → check-in →
poll each enrolled slot → start+enqueue → complete/fail finished jobs.

Fixture: `NEXCLIP_MODE2_STUB` JSON (next-export body or `null` for 204).

## What v0 does not do

- Mode 1 start/stop capture, `GET .../schedule`, or hourly safety_net
  (**out of scope** — older simpler system)
- Inbound NexClip webhooks (hub never dials in)
- NexClip `recorder_type` values beyond decklink/srt/ndi
- Placing the file at `relative_dir/filename` on the MAM volume
