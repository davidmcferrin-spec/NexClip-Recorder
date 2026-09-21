# Assumptions to verify against NexAPP / NexClip / NexVUE

This v0 was built with **NexVUE public** as the only sibling source tree.
`github.com/davidmcferrin-spec/NexAPP` and `.../NexClip` returned 404 from
this environment (including authenticated git). Please walk these against
the live repos and correct the recorder — do not treat this list as final
product law.

## Stack

| Assumption | Basis | If wrong |
| --- | --- | --- |
| PHP + Apache + vanilla JS, no Node/Composer/Docker/pip | NexVUE `CLAUDE.md` conventions | Follow NexAPP/NexClip if they diverged |
| SQLite WAL, not Postgres | NexVUE auth.db / metrics.db / portal.db | Add Postgres DSN if NexClip MAM is already PG; keep SQLite for local demo |
| systemd timers instead of crontab | NexVUE heartbeat/tls-renew timers | Add `/etc/cron.d/nexrec` if that is the house style |
| Timezone `America/New_York` + NTP | NexVUE `setup.sh` | Honor station TZ |

## Auth / NexAPP

Copied from `NexVUE/web-portal/nexvue-portal-nexapp.php`:

- Cookie name `NexAPP_AUTH`
- JWT `alg=RS256`, `iss` match, `exp` required
- Same-VM `\NexApp\Auth\AccessService()->check($token, $service_id)`
- Fallback `GET /api/access.php?service_id=…` with Bearer token
- Login/logout URLs `login.php` / `logout.php`
- Catalog roles collapse to `admin` vs `user` on the hub; we map hub admin → recorder `admin`, else `operator` (not viewer) so producers can export

**Unverified (please confirm in NexAPP):**

- WAN-only **ticket** endpoint path (`NEXAPP_TICKET_URL`, default `/api/ticket.php`) and query names (`return`, `nexapp_ticket`, `instance_id`)
- Whether AccessService already has an `instance_id` / device dimension for multi-host apps
- LDAP bind/search attributes and group → role mapping (implemented as generic uid/mail/cn)
- Exact WAN-only “resource” registration (NexAPP Alias vs ticket) and any CSRF/ticket TTL

NexVUE Entra is **at NexAPP**, not an OIDC client on the edge. Recorder follows that: no Entra SDK here.

## NexClip studio recorder schedule

See `NEXCLIP-HOOKS.md`. Field names (`input_id`, `start_at`, `end_at`, callback
path) are **invented from the product brief**, not from NexClip source. Swap
the JSON to match the MAM when the repo is available. The important split is
fixed: **this box records continuously**; NexClip schedule creates **exports**.

## NexVUE / DeckLink / WebRTC

- WHEP port **8889**, media **8189**, RTSP loopback **8554**, MediaMTX API loopback **9997** — copied from NexVUE `mediamtx.yml`
- Preview is proxy quality; NexVUE’s HI/LO ladder is **not** cloned
- DeckLink exclusive-open → preview must tee in-process (documented; IP preview is a second FFmpeg in v0)
- NexVUE encodes with GStreamer/QSV; recorder uses **FFmpeg libx264** so files are edit-friendly. Hardware encode (`h264_qsv`) can be a per-input later option, not v0.

## Intelligence / analyzers

| Assumption | Basis | If wrong |
| --- | --- | --- |
| SCTE-35 in recorded **MP4** is usually absent | FFmpeg MP4 remux drops MPEG-TS data PIDs | Keep a parallel TS tap or copy SCTE into `emsg`/ID3 |
| SCTE-104 is SDI VANC, not in IP files | SMPTE 2010 / DeckLink ancillary | Blackmagic SDK VANC reader (NexVUE `decklinksrc` pattern) |
| SCTE-224 is ESAM/HTTP | SCTE 224 standard | Wire to the station’s ESAM URL; `scte224_ingest` is the stub |
| No FFmpeg Nielsen decoder | Nielsen Audio Decoder SDK is licensed; Linux notes cite CentOS + license file for CBET L1. `nielsen_inspector` requires that SDK | Station provides `NEXREC_NIELSEN_CMD` |
| No FFmpeg SMPTE bars filter | `blackdetect`/`freezedetect` exist; bars do not | Histogram/template match; duration threshold already stored |
| 64-ch RTA cannot come from AAC proxy | Preview is stereo 96k | DeckLink embed / AES67 capture for meters |
| CALM chart uses ebur128 LUFS as LKFS | ATSC A/85 / BS.1770; LKFS ≡ LUFS for this measurement | If legal wants gated dialog-gated loudness, switch to a BS.1770-4 mode later |
| whisper.cpp / faster-whisper are optional binaries | No pip in Nex* | Operators install the engine; GPU recommended |

## UI

Standalone pages use NexAPP `--nx-*` tokens (`nexapp-tokens.css` fallback copied from NexVUE portal) plus a NexVUE-style top nav. If NexClip’s MAM chrome is different, restyle after we can see it — layout (Live / Export / Inputs / Settings) should stay.
