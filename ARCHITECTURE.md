# NexCLIP Recorder architecture (v0)

Product name **NexCLIP Recorder**; repo `NexClip-Recorder`. This document is the
contract for deployment modes, the FFmpeg pipeline, on-disk chunks, WebRTC
preview, auth, retention, and NexClip hooks.

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
                    │ NexAPP (SSO)     │  WAN-only resource / ticket
                    │ NexClip (MAM)    │  studio recorder schedule
                    └──────────────────┘
```

| Mode | How it runs | Identity |
| --- | --- | --- |
| **Standalone** | Own host, own UI, local (and optional LDAP) accounts | `NEXREC_DEPLOY_MODE=standalone` |
| **NexAPP WAN-only** | Still own host (not an Apache Alias unless you choose to). NexAPP is IdP. | JWT / `NexAPP_AUTH` cookie **or** ticket round-trip. `NEXREC_INSTANCE_ID` distinguishes multiple recorders. |
| **NexClip worker** | Same host records continuously; NexClip schedule drives **export** windows (not necessarily start/stop of ingest). | Shared API key + outbound poll (edge-initiated, like NexVUE heartbeats). |

Modes compose: a box can be standalone for local ops, NexAPP-authenticated for
the WAN, and a NexClip worker at the same time.

### Multi-instance NexAPP

Each recorder host **must** set a stable `NEXREC_INSTANCE_ID` (e.g.
`dcwasof2nexrec01`). NexAPP access checks use:

```
GET {NEXAPP_ACCESS_URL}?service_id=nexclip-recorder&instance_id={NEXREC_INSTANCE_ID}
Authorization: Bearer {token}
```

If the grant is for a different instance, the UI returns 403 and lists
`allowed_instances` when the hub provides them. Do not share one SQLite file
across hosts.

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
| LDAP | `NEXREC_LDAP_ENABLED=1`. Search + user bind. |
| NexAPP SSO | JWT RS256 with hub public key; live grant via `AccessService` on same VM **or** `GET /api/access.php`. WAN-only: redirect to NexAPP login with `return=`, accept `nexapp_ticket` or `#nexapp_sso=` (same hash hop NexVUE uses for portal SSO). |

NexAPP was private here. The implementation follows
`web-portal/nexvue-portal-nexapp.php` in NexVUE (cookie `NexAPP_AUTH`,
`service_id`, issuer, PEM). See `docs/ASSUMPTIONS.md`.

## 8. Retention and free-space floor

`nexrec-cleanup.py` runs **twice daily** (`nexrec-cleanup.timer`, 06:00 and
18:00 America/New_York):

1. Delete unprotected exports with `expires_at < now` (default now+15d at
   create; `protected=1` skips).
2. Delete native chunks older than the **input’s** `retention_days`.
3. Remove orphan files under `inputs/` not referenced by `chunks`.
4. If free space on `NEXREC_STORAGE_DIR` is still below
   `NEXREC_FREE_SPACE_FLOOR`, delete oldest unprotected exports, then oldest
   unprotected native chunks, until the floor is met or nothing remains.

Protected exports and in-progress recordings are never deleted by step 4.

## 9. NexClip studio recorder schedule

NexClip’s repo was not readable. The v0 **contract** (implemented as poll +
webhook) is in `docs/NEXCLIP-HOOKS.md`. Idea: NexClip already schedules
studio recorders; this box **already records** 24/7 in 5-minute chunks.
When an event ends (or on webhook), the recorder enqueues an export for
`[start_at, end_at]` on the mapped `input_id` and POSTs the file URL back.

Inbound webhook (API key) exists so NexClip can push; outbound poll exists
so a DMZ recorder does not need an inbound hole (NexVUE heartbeat pattern).

## 10. Process model

| Unit | Role |
| --- | --- |
| `apache2` + `web/` | UI + JSON API |
| `nexrec-record@id` | FFmpeg segment recorder |
| `nexrec-preview@id` | FFmpeg proxy → MediaMTX (IP only) |
| `nexrec-export.service` | Drain `exports` queue |
| `nexrec-cleanup.timer` | Twice-daily retention |
| `nexrec-nexclip.timer` | Optional schedule poll |
| `mediamtx.service` | WHEP |

Workers are Python stdlib. PHP never shells FFmpeg with unsanitized input;
it writes DB rows. Input ids are `[a-z0-9-]{1,32}`.
