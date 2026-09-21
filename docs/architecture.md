# NexCLIP Recorder — Architecture Plan

**Status:** Implemented in this repo (v0 scaffold). Local-tree contracts in
`docs/references/` and `docs/ASSUMPTIONS.md`. GitHub 404 for private siblings
is expected.

This product is the Mode 2 recorder NexClip already specified and never built: always-on 5-minute chunks, local scrub/export, NexClip export-requests as the delivery trigger. Treat NexCLIP Recorder as an appliance that can run alone, and optionally speak NexAPP (WAN SSO) and NexClip (enrollment + export-requests). NexClip and NexAPP are never required to record.

**House stack on this box:** Ubuntu 24.04, Apache, PHP 8, vanilla HTML/CSS/JS, **SQLite WAL**, Python stdlib workers + FFmpeg. Hub NexClip/NexAPP keep Postgres. No npm, no Composer, no pip.

---

## 1. Product shape

Three independent integrations, all optional, all combinable:

| Mode | What it means |
|------|----------------|
| **Standalone** | Local UI + local users (optional **local-only** LDAP). Records, previews, exports, retention. No NexAPP, no NexClip. |
| **NexAPP WAN** | Same appliance. Login uses NexAPP `launch.php` ticket SSO. NexAPP never connects into the recorder. **One unique `service_id` per host** (general NexAPP convention — Recorder and standalone NexClip hosts alike). |
| **NexClip worker** | Same appliance. Enrolls with `RECORDER_ENROLLMENT_SECRET`, check-in, poll `export-requests/next`, deliver stitched files. NexClip still does **not** start/stop capture. |

Recording is always local and always Mode 2 (24/7 segmented). NexClip’s Mode 1 (`scheduled_with_safety_net`, calendar start/stop) is **out of scope** — older simpler system, not a runtime option here.

---

## 2. FFmpeg is the capture engine — with one companion

Use **FFmpeg** for ingest, encode, 5-minute segments, concat, and trim. That matches NexClip, DeckLink (`-f decklink`), SRT, RTSP, RTP, UDP/TCP MPEG-TS.

Do **not** ask FFmpeg to speak WebRTC to the browser. That path is brittle. Pair it with **MediaMTX**:

1. One FFmpeg per input **tee**s three outputs from a single decode (or copy):
   - **Hi-res MP4 segments** (the archive)
   - **Proxy MP4 segments** (editor + proxy export)
   - **Preview publish** to MediaMTX (RTSP or WHIP)
2. The web UI plays **WebRTC from MediaMTX** for live.
3. The export editor should **not** use WebRTC. Scrubbing wants HTTP byte-range on the proxy MP4s (or a short HLS window). WebRTC is live-only.

GStreamer is only a fallback if DeckLink + FFmpeg field order or clocking misbehaves on a specific card. Default is FFmpeg + MediaMTX + Blackmagic Desktop Video.

---

## 3. Capture model (10 inputs, mixed sources)

Each **slot** is independently wired. The node is not “a DeckLink recorder” or “an SRT recorder.” NexClip still thinks that way; this product should not.

**Slot config:** type, URI or DeckLink device/index, expected identity (sticky, same as NexClip ADR 0020), encode policy, retention, optional 1080i→1080p, enabled/disabled.

| Type | Ingest |
|------|--------|
| DeckLink Duo / Quad 2 | `-f decklink -i 'Device@N'` — auto-detect cards and connectors in Admin |
| SRT | listener or caller per slot |
| RTSP | TCP preferred, UDP fallback |
| MPEG-TS UDP/TCP multicast | `-f mpegts` + multicast iface |
| RTP / RTP multicast | SDP or `rtp://` |
| Other IP | anything FFmpeg can open; NDI later if parity with NexClip’s enum is wanted |

Capacity: **10 concurrent captures**. Live and editor grids show **1–6** at a time; the other slots keep recording. A Quad 2 (8) + Duo (2) is the natural 10-SDI box. IP-only boxes skip the SDK.

**Sticky assignment:** unplug/replug SDI, drop SRT, or a multicast blip does not create a new input. Presence (`signal present` vs `expected source`) is separate from the assignment, same as NexClip’s green/amber/gray dots.

### 3.1 Encode policy

- Detect incoming format (ffprobe / DeckLink mode).
- Record **native geometry and frame rate**. 720p stays 720p. 1080i stays 1080i unless that slot’s **only** upconvert option is on: `bwdif` → 1080p at the same field rate (59.94i → 59.94p, 50i → 50p).
- **Copy** when the IP stream is already H.264/AAC in an MP4-safe bitstream **and** keyframes are regular enough to cut. Otherwise transcode.
- Transcode target (broadcast-balance H.264 High, `yuv420p`, `bt709`, AAC-LC 48 kHz):

| Format | Video |
|--------|--------|
| 1080i / 1080p | ~12–15 Mbps |
| 720p | ~8–10 Mbps |
| Proxy (always transcode) | 960×540 (or 1280×720 for 720p sources), ~2.5 Mbps, 1s GOP |

SDI is uncompressed, so SDI **always** encodes. 10×1080i H.264 wants **NVENC or QSV**, not libx264 on CPU, if this box is doing 10 encodes + proxies + previews. Plan NVIDIA NVENC (one session per hi-res + one per proxy, or proxy on CPU).

### 3.2 5-minute chunks + NTP timecode

- Time source: **chrony** (NTP). Never the DeckLink RS-422 clock unless that is added later.
- Each finished chunk is a **complete MP4** with `moov` at the front (`+faststart`). Not fragmented MP4. Premiere and FCPX both hate living fMP4; they import closed H.264/AAC MP4s.
- Segment muxer, 300s, reset timestamps per file, **force a keyframe at each cut** when transcoding (`-force_key_frames` / closed GOP). Copy-mode cuts only on keyframes — those files may not be exactly 300.000s. The editor must use **indexed start/end**, not “chunk N × 300.”
- SMPTE timecode in the file = wall clock at chunk start, matching detected frame rate (drop-frame for 29.97/59.94). Also store `creation_time` and a DB row. Filename is wall clock. Pick Eastern or UTC and stick; NexClip pathing is Eastern.
- Interlaced H.264 in MP4: `tff`/`bff` + `ildct`/`ilme`, and keep the optional 1080p upconvert. If a shop’s FCPX rejects interlaced MP4, that option is the compatibility path.

Keep a local **chunk index** (do not make NexClip store per-chunk rows; ADR 0020 already rejected that):

```
input_id, t_start, t_end, path_hires, path_proxy, bytes,
width, height, field_order, fps, video_codec, protected
```

Live preview timestamps and editor playhead are the **same NTP timeline**.

---

## 4. UI (NexAPP look, recorder layout)

Same pattern as NexClip ADR 0028: **tokens and theme from NexAPP, layout is ours.** Vendor `nexapp-tokens.css` + a small `NexAppTheme` fallback so the box still looks right when `nexapp.nexstar.tv` assets are unreachable. WAN redeem already returns `theme` (`light`/`dark`/`system`) — apply it before first paint, as NexNOC does.

Do not wrap this in NexAPP’s PHP `Layout`. Do not load hub `app.css`.

**Screens:**

1. **Live** — 1 / 2 / 3 / 4 / 6 mosaic. Pick which slots. Shared “now” clock. WebRTC per pane. Signal/record/disk badges.
2. **Export editor** — same 1–6 mosaic, **one shared playhead** in wall-clock time. Range pick, scrub, mark in/out. All selected inputs stay locked; a 3-input show is three players + one timeline.
3. **Exports** — job list, protect/unprotect, download, age.
4. **Admin** — slots/sources, encode/retention per input, storage waterline, users, LDAP, NexAPP, NexClip enrollment, DeckLink probe.

Export dialog:

- This stream **or** all streams currently on the editor
- **Full** (hi-res concat+trim) or **proxy**
- Optional **protect** (skip 15-day purge)
- Optional title (feeds NexClip delivery name when connected)

Export job: concat demuxer of covering chunks → `atrim`/`trim` to in/out (re-encode a few GOPs at the edges if the in-point is not a keyframe; stream-copy the middle when possible) → one Premiere/FCPX MP4 per selected input. “All” means N files, not a mixer.

---

## 5. Auth

Local bcrypt users always. Optional LDAP bind is **recorder-local only**
(`NEXREC_LDAP_ENABLED=1`) for air-gapped standalone. NexAPP hub auth is
**local + Entra SAML, not LDAP.** NexClip removed LDAP. Production Nex\*
path is local users + NexAPP (SAML at the hub).

Login page offers whatever is enabled:

- **Local** (bcrypt)
- **LDAP/AD** (standalone / portal down — not hub directory sync)
- **NexAPP** (WAN `launch.php` ticket)

NexAPP catalog role is only a **ceiling** (`user` | `admin`). Map portal
Admin → local admin, portal User → operator.

### 5.1 One `service_id` per host (settled)

NexAPP: **one `service_id` → one `launch_url`.** The cookie never leaves
the hub host. Each WAN appliance redeems a ticket and mints **its own**
session cookie.

**Settled pattern (general NexAPP, not Recorder-only):** one catalog row
per machine. If two standalone NexClip hosts are deployed, they need
`nexclip-ctl1` and `nexclip-ctl2` (not a shared `nexclip`), just as two
recorders need `nexclip-recorder-ctl1` and `nexclip-recorder-ctl2`. This
repo does not change NexClip code. Each host gets a unique `service_id`,
launch URL, redeem secret, Access grants, and manifest. Portal shows one
tile per machine. Do not invent `instance_id` as a grant gate.

Unauthenticated bookmark on a recorder:

```
https://nexapp.nexstar.tv/launch.php?service_id=nexclip-recorder-ctl1&next=/export
```

Same pattern on a standalone NexClip host:

```
https://nexapp.nexstar.tv/launch.php?service_id=nexclip-ctl1&next=/
```

Then `POST /api/launch/redeem.php` with `X-NexApp-Launch-Secret`. Never put
`sub`/`role` on the query string. Admin registration steps:
`docs/ASSUMPTIONS.md`.

---

## 6. NexClip integration (do not fight ADR 0020)

When connected, this box **is** the missing Mode 2 node:

1. `POST /recorders/register` with enrollment secret
2. `POST /recorders/{id}/checkin` — presence + `buffer_earliest_at`
3. Poll `GET .../export-requests/next`
4. Concat+trim local buffer to NexClip’s `relative_dir`/`filename`
5. `complete` with `delivered_path` (or `fail`)

NexClip still never says “start recording.” Calendar events become **export requests after `effective_end`**. Manual exports from **this** UI stay local; optional later: also push a copy into a NexClip library.

**Mode 1 is out of scope.** Do not poll `GET .../schedule`.

### 6.1 Contract gaps this product will hit

NexClip’s register schema is tighter than this product:

- `recorder_type` is node-level: `decklink` | `srt` | `ndi`
- `num_slots` is **4–8**, not 10
- Types are not per-slot; the mix-and-match IP list is not in the enum

**Do not fake a single type.** Plan a small NexClip bump: `recorder_type: "mixed"`, `num_slots` 1–10, and per-slot `source_kind` on check-in. Until that lands, a 10-input mixed box cannot enroll honestly.

Local UI owns hardware config. NexClip only displays reported inputs and maps slot → studio/channel (`expected_source`). That split is already settled; keep it.

---

## 7. Storage and purge

| Class | Default | Override |
|-------|---------|----------|
| Raw/proxy chunks | 4 weeks | Per input |
| Exports | 15 days | Per export **protect** (until unmarked) |
| Waterline | System-wide “always keep X MB/GB free” on the storage path | Admin |

Twice daily (**systemd timer** wrapping the same job as cron, 06:00 and 18:00 Eastern is enough):

1. Delete expired **unprotected** exports
2. Delete raw/proxy chunks past that input’s TTL, **except** chunks still needed by an in-flight or protected export
3. Delete **orphans** (files on disk with no index row, or index rows whose file is gone)
4. If free space &lt; waterline, delete oldest **unprotected** raw first, then unprotected exports, never protected, never in-use recording files
5. Recalc `buffer_earliest_at` for NexClip check-in

Record into a spool (`.../partial/`) and atomically move into the indexed tree, same idea as studio-calendar `.partial` → `mv`.

---

## 8. Internals

```
Apache :443
  /            vanilla UI (NexAPP tokens)
  /api/        → uvicorn 127.0.0.1
  /preview/    → MediaMTX WebRTC/WHEP
  /media/      → chunk + export files (auth-gated)

recorder-api     config, auth, jobs, index
recorder-engine  one supervised FFmpeg (+ tee) per slot
mediamtx         live WebRTC only
cleanup          timer twice a day
chrony           wall clock = timecode
```

Roles: **operator** (live + editor + export), **admin** (inputs, auth, storage, integrations). NexAPP `user`/`admin` only sets the ceiling.

---

## 9. Build order

1. Appliance skeleton: `setup.sh`, Apache, API, vanilla shell, local auth, theme tokens
2. Slot config + FFmpeg capture + 5-min index + NTP timecode (1 SRT or UDP input first, DeckLink second)
3. Proxy tee + MediaMTX live view (1, then 6)
4. Editor: time-locked 1–6, in/out, concat+trim, full vs proxy
5. Retention + waterline + twice-daily cleanup
6. NexAPP WAN redeem (one unique `service_id` per host; LDAP local-only)
7. NexClip Mode 2 enroll/check-in/export-requests (slots 1–8; IP maps to `srt`)
8. 1080i option, NVENC, 10-slot soak

---

## 10. Remaining product gaps

Settled: one NexAPP `service_id` per host (Recorder and standalone NexClip);
Mode 2 only (Mode 1 out of scope).

Still open with NexClip:

1. Bump `num_slots` to 10 and add `mixed` + per-slot type before a 10-input mixed box can enroll honestly (until then, map IP → `srt`, enroll slots 1–8).
2. **GPU:** NVENC on the recorder host, or CPU-only for a 2–4 input lab image.
3. **Audio:** stereo downmix always, or keep SDI 8-channel in hi-res and stereo on proxy.
4. **Time zone in filenames:** Eastern to match NexClip pathing, or UTC internally and convert on export.

---

## 11. References

| Source | Why |
|--------|-----|
| NexClip `docs/decisions/0020-recording-and-scheduling.md` | Mode 2 continuous + export-requests (Mode 1 out of scope here) |
| NexClip `docs/decisions/0025-nexapp-integration.md` | Catalog ceiling vs in-app roles; workers/recorders use enrollment secrets |
| NexClip `docs/decisions/0028-nexapp-visual-identity.md` | Tokens from NexAPP, layout stays the app |
| NexClip `docs/decisions/0029-standalone-host.md` | Standalone vhost, TCP 443; LDAP not coming back |
| NexClip `api/app/recorders/routes.py` | Register / check-in / export-requests (excerpts in `docs/references/`) |
| NexAPP `examples/nexapp-launch-redeem.php` | WAN launch ticket + redeem; NexAPP never connects in |
| NexAPP `examples/nexapp-access-client.php` | Same-host RS256 + AccessService / access.php |
| This repo `docs/references/` | Local-tree copies (GitHub private 404 expected) |
