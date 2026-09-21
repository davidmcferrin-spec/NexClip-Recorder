# Local-tree references

These files were copied from **local NexAPP / NexClip trees** on
`DESKTOP-5M42MED` (2026-09-21) and attached to the cloud agent. GitHub
returned 404 for the private repos — do not treat GitHub as the source.

| File | Origin |
| --- | --- |
| `nexclip-recorder-integration-brief.md` | Owner brief (authoritative for this PR) |
| `nexapp-access-client.php` | `NexAPP/examples/nexapp-access-client.php` |
| `nexapp-launch-redeem.php` | `NexAPP/examples/nexapp-launch-redeem.php` |
| `nexclip-recorders-schemas.py` | `NexClip/api/app/recorders/schemas.py` |
| `nexclip-recorders-routes-mode2.md` | Excerpt of `NexClip/api/app/recorders/routes.py` |
| `adr-0020-mode2-excerpt.md` | `NexClip/docs/decisions/0020-recording-and-scheduling.md` |
| `adr-0029-excerpt.md` | `NexClip/docs/decisions/0029-standalone-host.md` |

Recorder implementation lives in this repo. NexClip still does **not**
include node-side capture software (ADR 0020 non-goal).

Owner decisions baked into this PR (after the brief):

- One unique NexAPP `service_id` per host (general NexAPP rule: Recorder
  **and** standalone NexClip — e.g. `nexclip-ctl1` / `nexclip-ctl2`). This
  repo does not change NexClip code.
- Mode 2 `continuous_24x7` only; Mode 1 is out of scope.
