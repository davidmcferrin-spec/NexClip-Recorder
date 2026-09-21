# NexClip recorder routes — Mode 2 excerpt

Source: `NexClip/api/app/recorders/routes.py` (local tree 2026-09-21).
Full file also includes Mode 1 schedule/start-capture and admin listing.

Node auth: `Depends(get_current_recorder)` (per-node bearer). Register uses
`X-Enrollment-Secret` compared to `settings.recorder_enrollment_secret`.

API is mounted under NexClip’s FastAPI prefix (recorder uses
`NEXCLIP_API_PREFIX=/api/v1` by default).

```
POST   /recorders/register
POST   /recorders/{recorder_id}/checkin                         → 204
GET    /recorders/{recorder_id}/inputs/{slot}/export-requests/next
       200 NextExportRequestResponse | 204 idle | 409 unassigned
POST   /recorders/{recorder_id}/inputs/{slot}/export-requests/{request_id}/start
       → 201 {capture_id}
POST   /recorders/{recorder_id}/captures/{capture_id}/complete   {delivered_path} → 204
POST   /recorders/{recorder_id}/captures/{capture_id}/fail       {error_detail} → 204
```

`GET .../schedule` is **Mode 1 only**. Mode 2 must not reuse it: Mode 1 looks
ahead 15 minutes to *start capture*; Mode 2 looks at *completed* events
(`effective_end` already passed) to *export from the buffer*.

204 on next-export is the same idle convention as `/workers/{id}/next-task`.
