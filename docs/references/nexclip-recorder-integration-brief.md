# NexCLIP Recorder ↔ NexAPP / NexClip integration brief

Source: local trees on DESKTOP-5M42MED (2026-09-21), not GitHub (private repos 404 to cloud agent).

## Stack facts
| App | Stack | Auth |
|-----|-------|------|
| **NexAPP** 0.10.2 | PHP 8.2+/Apache/PostgreSQL/Ubuntu 24.04+ | Local + **Entra SAML** (not LDAP). Cookie `NexAPP_AUTH` RS256 JWT |
| **NexClip** 0.1.0 | Python FastAPI + PostgreSQL + Apache vhost | NexAPP redirect SSO + **local passwords**. **LDAP removed** (ADR 0025/0029) |
| **NexCLIP Recorder** v0 | PHP/Apache/SQLite (NexVUE-shaped) | Must align below |

## NexAPP — how sibling apps authenticate

### Same-host Alias
1. Verify JWT RS256 with hub public key (`/var/www/nexapp/keys/jwt_public.pem`). Missing key = hard fail.
2. Live grant: `NexApp\Auth\AccessService` (same VM) or `GET /api/access.php?service_id=…` (cookie or Bearer).
3. JWT `apps` is a **stale snapshot**; always re-check grants. Roles collapse to `user`|`admin` per service_id.
4. Manifest: `nexapp-manifest.json` with `service_id`, `base_path`, etc. Theme kit: `/assets/nexapp-theme.css`.

Helpers (copy verbatim patterns):
- `NexAPP/examples/nexapp-access-client.php`
- `NexAPP/examples/example-service/nexapp-manifest.json`
- Docs: `NexAPP/docs/nexapp-architecture-spec.md`, README

### WAN-only apps (NexCLIP Recorder on its own machine — preferred for multi-recorder)
NexApp **never** connects into the WAN. Cookie is host-only so it will not follow to the recorder host.

Flow:
1. User → `https://nexapp.nexstar.tv/launch.php?service_id=<id>&next=/…`
2. One-time ticket in browser redirect
3. WAN app `POST /api/launch/redeem.php` with `X-NexApp-Launch-Secret` + `{ticket, service_id}`
4. Redeem returns `sub, email, name, role (user|admin), optional next, theme`

Helper: `NexAPP/examples/nexapp-launch-redeem.php` (`nexapp_sso_url`, `nexapp_redeem_launch`).

Config on hub: Admin → service https launch URL; `config.php` `launch.redeem_secrets.<service_id>`.

**Multi-recorder:** each recorder host is its own WAN service_id (or one service_id with distinct launch URLs — prefer **one service_id per recorder instance** so Access grants and redeem secrets stay clear). Do **not** invent a separate `instance_id` gate that bypasses AccessService/redeem.

### LDAP note (product conflict)
Owner asked for local + LDAP + NexAPP on Recorder. **NexAPP hub auth is local + Entra SAML, not LDAP. NexClip explicitly removed LDAP.**  
Recommendation: keep **optional LDAP bind on Recorder** for standalone air-gapped use, but document that Nex\* production path is **local + NexAPP (SAML at hub)** — LDAP is Recorder-local only, not hub directory sync.

## NexClip — recorder node contract (Mode 2 = NexCLIP Recorder)

ADR: `NexClip/docs/decisions/0020-recording-and-scheduling.md`  
Code: `NexClip/api/app/recorders/{routes,schemas,service}.py`  
Auth for nodes: **enrollment secret** + per-node bearer token (not NexAPP SSO). Humans use NexAPP/local on portals.

### Modes
- **Mode 1 `scheduled_with_safety_net`:** node polls `GET /recorders/{id}/inputs/{slot}/schedule` → exact event or hourly safety_net; then start/complete capture. (Not the continuous product.)
- **Mode 2 `continuous_24x7`:** node records continuously (VELA replacement); calendar does **not** start/stop capture. Schedule creates **export requests**; node polls exports. **This is NexCLIP Recorder.**

### Mode 2 node API (replace invented NEXCLIP-HOOKS webhook)
Enrollment header: `X-Enrollment-Secret: $RECORDER_ENROLLMENT_SECRET`

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/recorders/register` | body: `hostname`, `recorder_type` (`decklink`\|`srt`\|`ndi`), `num_slots` 4–8 → `{recorder_id, token}` |
| POST | `/recorders/{id}/checkin` | status + per-slot `is_present`, `reported_label`, Mode2 `buffer_earliest_at` |
| GET | `/recorders/{id}/inputs/{slot}/export-requests/next` | next pending export or **204** |
| POST | `/recorders/{id}/inputs/{slot}/export-requests/{request_id}/start` | claim → `capture_id` |
| POST | `/recorders/{id}/captures/{capture_id}/complete` | `{delivered_path}` |
| POST | `/recorders/{id}/captures/{capture_id}/fail` | `{error_detail}` |

Export request fields: `range_start`, `range_end`, `title`, `library_id`, `relative_dir`, `filename`, `event_id` or `requested_by`.

Register types today: decklink/srt/ndi only — Recorder should extend negotiation or map RTSP/UDP/etc. to closest type + document gap with NexClip.

### NexClip human auth (for UI styling / optional deep link)
Standalone host ADR 0029: redirect SSO via `/api/v1/auth/sso/start` → NexAPP `login.php?return=…&service_id=nexclip` with JWT in query; live `access.php`; local PBKDF2 passwords. Alias leftover still uses cookie.

## Required Recorder PR corrections
1. Replace invented schedule poll/webhook with **Mode 2 export-request client** matching schemas above.
2. Implement **WAN launch redeem** path from `nexapp-launch-redeem.php` for multi-host; keep Alias cookie path for same-VM lab.
3. Update `docs/ASSUMPTIONS.md` and `docs/NEXCLIP-HOOKS.md` (rename to NexClip recorder Mode 2 contract).
4. Theme: consume NexAPP theme kit tokens (already aimed); cite ADR 0028.
5. Note Postgres vs SQLite: edge recorder SQLite OK; do not require hub Postgres on the recorder box.
6. Clarify LDAP = optional local only.

## Open questions for owner
- One NexAPP `service_id` per recorder host vs shared id?
- Extend NexClip `recorder_type` enum for RTSP/UDP/RTP/multicast or keep mapping?
- Default Mode 2 only, or also speak Mode 1 schedule API?
