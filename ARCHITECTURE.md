# NexCLIP Recorder architecture (v0)

Product name **NexCLIP Recorder**; repo `NexClip-Recorder`. This document is the
contract for deployment modes, the FFmpeg pipeline, on-disk chunks, WebRTC
preview, auth, retention, and the NexClip **Mode 2** export-request client.

Private NexAPP / NexClip GitHub 404 is expected. Local-tree excerpts:
`docs/references/`. Assumptions: `docs/ASSUMPTIONS.md`.

**Host hardware** (Z4/Z6-class, 128 GB RAM, NVENC, 10GbE, ZFS RAIDZ2 media
pool) is owner-decided — see README **Hardware recommendations**. Do not treat
this file as a BOM.

**Per-input intelligence** (SCTE, A/V anomalies, captions/FTS, optional ASR,
Nielsen presence log, live analyzers, export LKFS) is owner-required scope — see
`docs/FEATURES.md`. Sidecars only; the native record encode stays edit-friendly.
Nielsen logging is best-effort presence on the recording timeline, not an
audit-grade watermark decode.

## 1. Deployment modes

```
                    ┌──────────────────┐
   operators ──────►│  Recorder UI     │  PHP/Apache on the recorder host
                    │  (this box)      │
                    └────────┬─────────┘
                             │ SQLite WAL
                             ▼
                    ┌──────────────────┐     FFmpeg segment
                    │  record@input    │◄──── RTSP/SRT/UDP/RTP/TCP
                    │  preview@input   │◄──── DeckLink (next; exclusive-open)
                    │  export worker   │
                    │  cleanup.timer   │
                    └────────┬─────────┘
           MediaMTX WHEP     │    optional outbound
              preview        ▼
                    ┌──────────────────┐
                    │ NexAPP (SSO)     │  WAN launch.php → redeem ticket
                    │ NexClip (MAM)    │  Mode 2 export-requests/next
                    └──────────────────┘
```

| Mode | How it runs | Identity |
| --- | --- | --- |
| **Standalone** | Own host, own UI, local (and optional **local-only** LDAP) accounts | `NEXREC_DEPLOY_MODE=standalone` |
| **NexAPP WAN-only** | Still own host (not an Apache Alias unless you choose to). NexAPP is IdP. | Unique `NEXAPP_SERVICE_ID` per host. Same-host: JWT + `access.php`. WAN: `launch.php` ticket redeem. |
| **NexClip worker** | Same host records continuously (Mode 2). Hub export requests drive **exports**, never start/stop of ingest. | Enrollment secret + per-node bearer. |

Modes compose: a box can be standalone for local ops, NexAPP-authenticated for
the WAN, and a NexClip worker at the same time.

### Multi-host NexAPP (one `service_id` per machine)

This is the **general NexAPP WAN pattern**, not Recorder-only. Recorder
hosts **and** standalone NexClip hosts each get their own catalog row.
Two NexClip boxes must not share `service_id=nexclip`. This repo does not
change NexClip code.

| Host (example) | `service_id` |
| --- | --- |
| Recorder ctl1 | `nexclip-recorder-ctl1` |
| Recorder ctl2 | `nexclip-recorder-ctl2` |
| Standalone NexClip ctl1 | `nexclip-ctl1` |
| Standalone NexClip ctl2 | `nexclip-ctl2` |

```
NEXAPP_SERVICE_ID=nexclip-recorder-ctl1
GET {NEXAPP_ACCESS_URL}?service_id=nexclip-recorder-ctl1
```

WAN landing (same path on a NexClip host with `service_id=nexclip-ctl1`):

```
{NEXAPP_ISSUER}/launch.php?service_id=nexclip-recorder-ctl1&next=/live
POST {NEXAPP_ISSUER}/api/launch/redeem.php
  X-NexApp-Launch-Secret: {NEXAPP_LAUNCH_SECRET}
  { "ticket": "…", "service_id": "nexclip-recorder-ctl1" }
```

Register **each** host in NexAPP Admin: unique `service_id`, HTTPS launch
URL, `launch.redeem_secrets.<id>`, Access grants, manifest. Do **not**
share a `service_id` across machines. Do **not** invent an `instance_id`
grant gate. `NEXREC_INSTANCE_ID` is hostname/display only. Do not share
one SQLite file across hosts.

## 2. Media engine: FFmpeg (not a custom muxer)

**Recording, segmenting, concat, trim, optional live transcode, and proxy
preview encode are all FFmpeg.** We do not invent a muxer.

| Path | Tool | Why |
| --- | --- | --- |
| IP ingest + MP4 segments | FFmpeg `segment` muxer, `segment_atclocktime=1` | Wall-clock 5-minute files, NTP-aligned names/timecode |
| Concat + trim export | FFmpeg concat demuxer + `-ss`/`-t`, then `+faststart` | Premiere / FCPX |
| DeckLink | FFmpeg `-f decklink` **when** FFmpeg is built `--enable-decklink` | Matches “don’t invent a muxer”. If a box only has the Blackmagic SDK (NexVUE’s `decklinksrc`), a thin helper may feed FFmpeg via rawvideo pipe — documented below, not required for v0 IP demo. |
| WebRTC preview | FFmpeg publishes **proxy** H.264+AAC to MediaMTX RTSP; browsers use **WHEP** | Same egress idea as NexVUE. MediaMTX does **not** transcode. |

NexVUE uses GStreamer + Quick Sync because it is a live return-feed with a
sub-250 ms budget and exclusive DeckLink opens. Recorder priorities are
**edit-compatible files** and **clock-aligned chunks**, so FFmpeg is the
better default. Preview latency may be 1–3 s; that is acceptable for
monitoring.

### Premiere Pro + Final Cut Pro X

Exports and each closed 5-minute chunk aim for:

- Container: MP4 (`isom`/`mp42`), `moov` atom at the front (`movflags=faststart`)
- Video: **H.264 High@L4.1**, `yuv420p`, CABAC, closed GOP (~2 s), B-frames 2
- Audio: **AAC-LC**, 48 kHz, stereo (or source channel count when copying)
- Timecode: QuickTime `timecode` / `creation_time` from the **system clock**
  (assume chrony/NTP; `setup.sh` enables NTP like NexVUE)

Interlace: **1080i stays 1080i** (`+ildct+ilme`, field flags). The **only**
optional upconvert is **1080i → 1080p** (`yadif`) per input. No 720→1080, no
SD→HD.

Bitrate: **copy** compressed IP when `COPY_NATIVE=1` and `LIVE_TRANSCODE=0`
and the bitstream is already H.264 4:2:0 + AAC. Otherwise encode at
`NEXREC_BROADCAST_VIDEO_BITRATE` (default 12 Mbps) — quality vs storage for
“best broadcast” MP4, not a mezzanine.

## 3. Chunk layout on disk

```
{NEXREC_STORAGE_DIR}/
  inputs/
    {input_id}/
      native/
        {YYYY}/{MM}/{DD}/
          {input_id}_{YYYYMMDD}T{HHMMSS}Z.mp4
      proxy/                    # optional later; same tree
        ...
  exports/
    {export_id}.mp4
  tmp/
    {export_id}.concat.txt
```

- Segment length: `NEXREC_SEGMENT_SECONDS` (production **300**).
- Split on the **NTP wall clock**, not on “300 s after process start”:
  FFmpeg `-segment_atclocktime 1 -segment_time 300 -strftime 1`.
- Filename timestamp is the **chunk start** in UTC.
- SQLite `chunks` row: `start_at`, `end_at`, `duration_s`, `timecode_start`,
  probe fields, `ready=1` once FFmpeg closes the file (the in-progress file
  is not indexed).
- Orphans: files on disk with no row, or rows whose file is missing.
  Cleanup removes both when unprotected.

Native recordings retain **4 weeks** by default, **per input**
(`RETENTION_DAYS`). Exports retain **15 days** unless `protected=1`.

On a production box, put `NEXREC_STORAGE_DIR` on the ZFS RAIDZ2 HDD pool and
export concat/trim scratch on NVMe (README hardware section). The app’s
free-space floor still applies on that media filesystem.

## 4. FFmpeg record command (IP)

Sketch (assembled in `worker/nexrec_ffmpeg.py`):

```text
ffmpeg -hide_banner -nostdin \
  -use_wallclock_as_timestamps 1 \
  -rtsp_transport tcp \            # RTSP only
  -i {url} \
  -metadata creation_time={iso8601} \
  -timecode {HH:MM:SS:FF} \        # from local clock at launch / midnight wrap
  -c:v libx264 -preset veryfast -profile:v high -level 4.1 -pix_fmt yuv420p \
  -g 60 -bf 2 -b:v 12M -maxrate 12M -bufsize 24M \
  -c:a aac -b:a 192k -ar 48000 -ac 2 \
  -f segment -segment_time 300 -segment_atclocktime 1 \
  -reset_timestamps 1 -strftime 1 \
  -segment_format mp4 \
  -segment_format_options movflags=faststart \
  {dir}/{id}_%Y%m%dT%H%M%SZ.mp4
```

Copy mode replaces the encode pair with `-c copy` (still MP4 segment +
faststart on close). Some IP flavors need `LIVE_TRANSCODE=1`.

DeckLink (designed):

```text
ffmpeg -f decklink -i "DeckLink Quad 2 (1)" ...same encode/segment...
```

DeckLink sub-devices are **exclusive-open** (NexVUE). Preview must **tee**
inside the same FFmpeg process, not a second capture. IP sources may run a
second FFmpeg for preview.

## 5. WebRTC preview

Every recordable input has a MediaMTX path `in0`…`in9`:

1. `nexrec-preview@id` (or the DeckLink tee) publishes
   `rtsp://127.0.0.1:8554/{preview_path}` at **proxy** size (default 960×540,
   ~1.5 Mbps H.264 + AAC).
2. MediaMTX restamps to WHEP at `https://{host}:8889/{preview_path}/whep`.
3. The live UI requests `/api/auth?action=whep_jwt` then POSTs SDP (NexVUE
   player pattern).

v0 UI includes a WHEP client. If MediaMTX is not installed, panes show a
placeholder so layout and time-lock chrome still work.

`mediamtx.yml` in this repo is a **stripped** cousin of NexVUE’s (RTSP ingest
loopback, WHEP egress). Production TLS/JWT/ICE should copy NexVUE’s
`setup.sh` patches rather than diverging.

## 6. Multi-stream UI (live + export)

Layouts: **1, 2, 3, 4, 6** panes. A shared playhead (wall-clock) time-locks
panes:

- **Live:** all WHEP players are “now”; clock overlay is NTP time.
- **Export editor:** playhead maps to `t` on the timeline; each pane seeks
  the chunk covering `t` (HTML5 `<video>` of indexed MP4s). Mark **I** / **O**.
  Export asks:
  - this stream vs **all visible** streams (one file per input, same in/out)
  - **full** (native chunks, copy+trim) vs **proxy** (transcode on demand in v0)

## 7. Auth

| Method | When |
| --- | --- |
| Local bcrypt users | Always. Roles `admin`, `operator`, `viewer`. |
| LDAP | Optional **recorder-local** only (`NEXREC_LDAP_ENABLED=1`). NexAPP is local + Entra SAML; NexClip removed LDAP. Production Nex\* is not hub LDAP. |
| NexAPP SSO | Same-host: JWT RS256 (missing PEM = hard fail) + live `AccessService` / `GET /api/access.php?service_id=…`. WAN (primary): `{issuer}/launch.php?service_id=…` then `POST /api/launch/redeem.php` with `X-NexApp-Launch-Secret`. One unique `service_id` per host. |

Helpers: `docs/references/nexapp-access-client.php`,
`docs/references/nexapp-launch-redeem.php`. See `docs/ASSUMPTIONS.md`.

## 8. Retention and free-space floor

`nexrec-cleanup.py` runs **twice daily** (`nexrec-cleanup.timer`, 06:00 and
18:00 America/New_York):

1. Delete unprotected exports with `expires_at < now` (default now+15d at
   create; `protected=1` skips).
2. Delete native chunks older than the **input’s** `retention_days`.
3. Remove orphan files under `inputs/` not referenced by `chunks`.
4. If free space on the recordings path is still below the free-space floor
   (`storage.free_space_floor` in Setup, seeded from `NEXREC_FREE_SPACE_FLOOR`),
   delete oldest unprotected exports, then oldest
   unprotected native chunks, until the floor is met or nothing remains.

Protected exports and in-progress recordings are never deleted by step 4.

## 9. NexClip Mode 2 (continuous_24x7)

This box **is** the Mode 2 node ADR 0020 specified. Contract:
`docs/NEXCLIP-HOOKS.md`. Calendar never starts or stops FFmpeg.

1. `POST /recorders/register` (`X-Enrollment-Secret`)
2. `POST /recorders/{id}/checkin` — per-slot `buffer_earliest_at`
3. `GET .../inputs/{slot}/export-requests/next` — 200 claim window or **204 idle**
4. `POST .../export-requests/{id}/start` → enqueue local concat/trim
5. `POST .../captures/{id}/complete` `{delivered_path}` or `fail`

**Mode 1 is out of scope** (no `GET .../schedule`, no start/stop capture).
NexClip never dials into this host; `nexrec-nexclip.timer` polls out.

## 10. Process model

| Unit | Role |
| --- | --- |
| `apache2` + `web/` | UI + JSON API |
| `nexrec-record@id` | FFmpeg segment recorder |
| `nexrec-preview@id` | FFmpeg proxy → MediaMTX (IP only) |
| `nexrec-export.service` | Drain `exports` queue |
| `nexrec-cleanup.timer` | Twice-daily retention |
| `nexrec-nexclip.timer` | Mode 2 register / check-in / export-request poll |
| `nexrec-analyze.service` | Sidecar: chunk intelligence + CALM ebur128 jobs |
| `mediamtx.service` | WHEP |

Workers are Python stdlib. PHP never shells FFmpeg with unsanitized input;
it writes DB rows. Input ids are `[a-z0-9-]{1,32}`.

Unit start/stop/restart/enable/disable from the Services page goes through
`bin/nexrec-systemctl.sh` under `sudo -n`. The script allowlists verbs and
unit names (`mediamtx`, `nexrec-export`, `nexrec-cleanup` service/timer,
`nexrec-analyze`, `nexrec-nexclip` service/timer, `nexrec-record@<id>`,
`nexrec-preview@<id>`). `setup.sh` installs
`/etc/sudoers.d/nexrec-systemctl` for that path only.

## 11. Monitoring / intelligence (sidecar)

See `docs/FEATURES.md`. Summary:

- Per-input SQLite flags on `inputs` (`feat_scte`, `feat_av_anomaly`,
  `feat_captions`, `feat_transcribe`, `feat_nielsen`, `feat_monitors`) plus
  freeze/black/bars duration thresholds.
- After a native chunk is indexed, `nexrec-record` calls `analyze_chunk()`
  **without** modifying `record_argv`. Detect uses a second FFmpeg
  (`blackdetect`/`freezedetect` at 320px).
- Events land in `events` + JSONL (Nielsen rows are presence spans with NTP
  wall-clock `t_start`/`timecode`, not SID/layer decode). Caption/transcript
  text in `captions` and FTS5 `captions_fts`.
- Export editor LKFS: `analyze_jobs` kind `loudness` → `ebur128=peak=true`
  on the concat/trim window (ITU-R BS.1770 / ATSC A/85 −24 LKFS).
- Live WFM/vectorscope/VU/64-ch RTA are UI placeholders fed later from the
  **preview/proxy** decode, not the mezzanine record.

## 12. Configuration surface

`nexrec.env` is **bootstrap and secrets**: data dir, DB path, HTTP port,
the initial admin password, API keys, the NexAPP launch secret, the NexClip
enrollment secret, and the node bearer. Day-to-day values (storage paths,
free-space floor, retention, FFmpeg profile, segment length, MediaMTX/WHEP,
NexAPP mode/issuer/service_id, Mode 2 API base and recorder id, feature
defaults, the optional Nielsen command path) live in SQLite `app_settings`
and are edited on **Setup** (`/settings`).

First boot seeds missing keys from the environment, then the database wins.
`NEXREC_ENV_OVERRIDES=1` is the break-glass switch. Workers call
`overlay_app_settings()` at start. The Nielsen path stays presence-only:
an empty `intelligence.nielsen_cmd` keeps the builtin stub.
