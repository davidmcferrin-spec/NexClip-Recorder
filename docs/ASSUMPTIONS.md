# Assumptions — corrected from local NexAPP / NexClip trees

GitHub 404 for private siblings is **expected**. This file reflects the
owner brief and attached helpers from local trees (2026-09-21). Sources:
`docs/references/`. Do not re-fetch those repos.

## Stack

| App | Stack | Auth |
| --- | --- | --- |
| **NexAPP** 0.10.2 | PHP 8.2+ / Apache / **PostgreSQL** / Ubuntu 24.04+ | Local + **Entra SAML** (not LDAP). Cookie `NexAPP_AUTH` RS256 JWT |
| **NexClip** 0.1.0 | Python FastAPI + PostgreSQL + Apache vhost | NexAPP redirect SSO + **local passwords**. **LDAP removed** (ADR 0025/0029) |
| **NexCLIP Recorder** v0 | PHP / Apache / **SQLite WAL** / Python stdlib workers | Local bcrypt + optional local LDAP + NexAPP |

**SQLite on the recorder is intentional.** NexClip/NexAPP use Postgres on
the hub. Do not require hub Postgres on this box. Theme tokens follow
NexAPP (`--nx-*`, ADR 0028) with layout remaining ours.

Timezone remains `America/New_York` + NTP (NexVUE / NexClip pathing).

## Auth / NexAPP (real paths)

Same-host Alias (lab only):

1. Verify JWT **RS256** with hub public key (`NEXAPP_PUBLIC_KEY_PATH`,
   default `/var/www/nexapp/keys/jwt_public.pem`). **Missing key = hard fail.**
2. Live grant: `\NexApp\Auth\AccessService()->check($token, $service_id)`
   or `GET /api/access.php?service_id=…` (cookie or Bearer).
3. JWT `apps` is a **stale snapshot** — always re-check grants.
4. Hub roles collapse to `user` | `admin` per `service_id`. Recorder maps
   `admin` → `admin`, `user` → `operator`.

WAN (primary for recorders — NexAPP **never** connects in; cookie is host-only):

1. Browser → `{NEXAPP_ISSUER}/launch.php?service_id=<id>&next=/…`
2. One-time `ticket` on the landing URL
3. Recorder `POST {NEXAPP_ISSUER}/api/launch/redeem.php` with
   `X-NexApp-Launch-Secret` + `{ticket, service_id}`
4. Redeem returns `sub, email, name, role, optional next, theme`

Helpers copied under `docs/references/nexapp-*.php`.

### One `service_id` per host (NexAPP convention)

This is a **general NexAPP multi-host rule**, not Recorder-only. Each WAN
appliance that users launch from the portal — a recorder **or** a
standalone NexClip host — gets its **own unique `service_id`**. Access
grants, portal icons, launch URLs, and `config.php`
`launch.redeem_secrets.<service_id>` stay 1:1 with the machine.

Two standalone NexClip hosts must not share `service_id=nexclip`. Two
recorders must not share `nexclip-recorder`. This repo does **not** change
NexClip code; it only documents the shared Admin pattern.

| Host (example) | `service_id` |
| --- | --- |
| Recorder ctl1 | `nexclip-recorder-ctl1` |
| Recorder ctl2 | `nexclip-recorder-ctl2` |
| Standalone NexClip ctl1 | `nexclip-ctl1` |
| Standalone NexClip ctl2 | `nexclip-ctl2` |

Do **not** share one `service_id` across machines. Do **not** invent an
`instance_id` grant gate that bypasses AccessService/redeem.
`NEXREC_INSTANCE_ID` is hostname/display (and NexClip register
`hostname`) only.

Register **each** host in NexAPP Admin (same four steps for Recorder and
for standalone NexClip):

1. Catalog row: unique `service_id`, display name, HTTPS **launch URL**
   of that box.
2. `config.php` → `launch.redeem_secrets.<service_id>` = the secret that
   box stores as its launch secret (`NEXAPP_LAUNCH_SECRET` here).
3. Access grants: grant users/groups that `service_id` (`user` or
   `admin`).
4. Drop the sibling `nexapp-manifest.json` (`service_id`, `base_path`,
   icon) so the portal tile matches.

A second recorder or a second standalone NexClip repeats those steps under
a different `service_id`.

## LDAP

NexAPP hub is local + Entra SAML, **not LDAP**. NexClip removed LDAP.
Recorder keeps **optional local LDAP bind** (`NEXREC_LDAP_ENABLED=1`) for
standalone/air-gapped boxes only. Production Nex\* path is **local users +
NexAPP (SAML at the hub)** — not hub directory sync.

## NexClip recorder node (Mode 2 only)

This product **is** Mode 2 `continuous_24x7` (ADR 0020). Calendar does
**not** start/stop FFmpeg. See `docs/NEXCLIP-HOOKS.md`.

**Mode 1 `scheduled_with_safety_net` is out of scope** (older simpler
system). Not a runtime mode and not a later switch. Do not poll
`GET .../schedule`, do not start/stop capture from the calendar, do not
implement hourly safety_net. Export-request poll only.

Node auth is **enrollment secret + per-node bearer**, not NexAPP SSO.
Humans use NexAPP/local on the recorder UI.

`recorder_type` on register is only `decklink` | `srt` | `ndi`. RTSP/UDP/
TCP/RTP/testsrc map to `srt` until NexClip extends the enum.

Slots on NexClip are **4–8**. This host may configure up to 10 ingest
inputs; only inputs with `nexclip_slot` 1–8 enroll. Hub Postgres stays on
the NexClip box.

## NexVUE leftovers (preview only)

WHEP 8889 / RTSP 8554 / MediaMTX still match NexVUE’s live-preview path.
Recording is FFmpeg, not GStreamer.

## Intelligence / analyzers

Sidecar flags in `docs/FEATURES.md`. Not blocking this integration pass.

## Open (owner)

- Extend NexClip `recorder_type` for RTSP/UDP/RTP vs keep `srt` mapping.
- MAM copy of `delivered_path` into `relative_dir`/`filename` on a shared
  mount (v0 reports the local export path).
