# NexClip studio recorder schedule — v0 contract

NexClip (MAM) was not readable in this environment. This is the **hook
surface** the recorder implements so NexClip can create exports from
already-recorded 5-minute chunks for scheduled studio inputs.

Update field names when the real NexClip API is available; keep the
semantics.

## Mapping

| NexClip idea | Recorder |
| --- | --- |
| Studio input / recorder source | `inputs.id` (slug) |
| Scheduled show window | `[start_at, end_at]` UTC ISO-8601 |
| “Create recording for this airing” | **Export job** over native chunks (not a new capture session) |
| File back in the MAM | POST callback with `export_id`, HTTPS URL or local path NexClip can pull |

Continuous ingest stays on. Schedule does **not** start/stop FFmpeg in v0
(that can be a later optimization for dark hours).

## Outbound poll (preferred for a WAN/DMZ recorder)

`nexrec-nexclip.py` every `NEXCLIP_SCHEDULE_POLL_S` seconds (systemd timer):

```http
GET {NEXCLIP_BASE_URL}/api/studio/recorder-schedule
    ?instance_id={NEXREC_INSTANCE_ID}
    &from={iso}
    &to={iso}
Authorization: Bearer {NEXCLIP_API_KEY}
```

Expected JSON (illustrative):

```json
{
  "ok": true,
  "events": [
    {
      "id": "sched_9f2c",
      "input_id": "studio-a",
      "title": "Morning Show",
      "start_at": "2026-09-21T14:00:00Z",
      "end_at": "2026-09-21T15:00:00Z",
      "protect_export": true,
      "quality": "full"
    }
  ]
}
```

When `end_at` is in the past and no `exports.nexclip_schedule_id` exists,
enqueue an export (`scope=one`, `quality` from the event). After the file
is `done`:

```http
POST {NEXCLIP_BASE_URL}/api/studio/recorder-exports
Authorization: Bearer {NEXCLIP_API_KEY}
Content-Type: application/json

{
  "schedule_id": "sched_9f2c",
  "instance_id": "recorder-01",
  "input_id": "studio-a",
  "export_id": "exp_…",
  "t_in": "2026-09-21T14:00:00Z",
  "t_out": "2026-09-21T15:00:00Z",
  "status": "done",
  "path": "/var/lib/nexrec/storage/exports/exp_….mp4",
  "url": "https://recorder.example.internal/api/exports/exp_…/file",
  "size_bytes": 123456
}
```

## Inbound webhook (if NexClip can reach the recorder)

```http
POST /api/nexclip?action=schedule_push
Authorization: Bearer {NEXCLIP_API_KEY}  or  X-NexClip-Key
```

Body: same `events[]` object (one event or a list). Used on trusted LAN.

`POST /api/nexclip?action=export_status` is available for NexClip to poll a
job without waiting for the callback.

## Input id agreement

NexClip `input_id` **is** the recorder slug. Configure the same string in
both UIs (e.g. `studio-a`). v0 does not include a separate mapping table;
add one if NexClip uses numeric channel ids.

## What v0 actually runs

- JSON parse + DB cache (`nexclip_events`)
- Auto-enqueue export when window ended
- Callback POST (skipped if `NEXCLIP_BASE_URL` empty)
- Fixture mode for tests (`NEXCLIP_SCHEDULE_STUB` file path)

It does **not** yet understand NexClip’s internal show-clock, rundown, or
asset metadata. Those belong in a follow-up once the MAM schema is visible.
