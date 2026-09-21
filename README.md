# NexCLIP Recorder

**NexCLIP Recorder** is a multi-input video recorder for broadcast studios.
The GitHub repository is `NexClip-Recorder`. It can run:

1. **Standalone** on its own machine (configure + use in the web UI)
2. As a **NexAPP WAN-only resource** (own host; identity via NexAPP SSO — **one unique `service_id` per host**)
3. As a **NexClip Mode 2 worker** (always-on record; hub export-requests → local concat/trim)

This is **v0.1.0**: a shippable scaffold with a working IP ingest → 5-minute (or demo 5-second) MP4 chunk → concat/trim export path, plus architecture for DeckLink, WebRTC preview, auth, retention, and the Mode 2 NexClip contract.

## Stack (matched to siblings)

Private NexAPP / NexClip GitHub 404 is **expected**. The owner’s local trees
(2026-09-21) plus public **[NexVUE](https://github.com/davidmcferrin-spec/NexVUE)**
are the source of truth. Excerpts: [`docs/references/`](docs/references/). Contract:
[`docs/ASSUMPTIONS.md`](docs/ASSUMPTIONS.md), [`docs/NEXCLIP-HOOKS.md`](docs/NEXCLIP-HOOKS.md).

| Choice | Why |
| --- | --- |
| PHP 8 + Apache + vanilla JS | NexAPP / NexVUE edge. No Node, no frontend framework, no Composer. |
| **SQLite WAL** (not Postgres) | Edge recorder. Hub NexClip/NexAPP keep Postgres. Do not require hub Postgres on this box. |
| Python 3 **stdlib only** + FFmpeg | Workers. No pip. GNU C++ only if/when DeckLink SDK helpers are required (NexVUE pattern). |
| systemd units + timers | NexVUE `nexvue-encode@N`, heartbeat timers. Twice-daily cleanup is a systemd timer (cron-equivalent). |
| Dark UI, NexAPP `--nx-*` tokens | Theme kit from NexAPP (ADR 0028); layout stays ours. |
| MediaMTX WHEP for preview | Same WebRTC path as NexVUE. Recording itself is **FFmpeg**, not GStreamer. |

## What works in this PR vs next

### Works now (demo / standalone)

- Local auth (bcrypt users, sessions, roles: admin / operator / viewer)
- Optional **local LDAP bind** (`NEXREC_LDAP_ENABLED=1`) — standalone/air-gap only. Production Nex\* is local users + NexAPP (SAML at the hub), not hub LDAP.
- NexAPP SSO: same-host **RS256** + live `AccessService` / `GET /api/access.php`; WAN **`/launch.php` → POST `/api/launch/redeem.php`**. One `service_id` per host.
- Config schema (`nexrec-example.env`, `inputs-example.env`)
- Input CRUD in the UI (up to 10; NexClip slots 1–8)
- FFmpeg segment recorder for **RTSP / SRT / UDP / TCP / RTP / testsrc**
- 5-minute (configurable) MP4 chunks, **wall-clock aligned**, NTP/system timecode metadata
- Chunk index in SQLite
- Export job: concat overlapping chunks + trim in/out → one Premiere/FCPX-friendly MP4
- Live multi-viewer **1 / 2 / 3 / 4 / 6** (time-lock chrome; WHEP player wired, placeholder if MediaMTX is down)
- Export editor: shared timeline, mark in/out, one-stream vs all-visible, full vs proxy
- Retention cleanup worker + **twice-daily systemd timer**
- NexClip **Mode 2** client: register, check-in (`buffer_earliest_at`), poll `export-requests/next` (204 = idle), start/complete/fail. Calendar does **not** start/stop record. Mode 1 is out of scope.
- `setup.sh`, Apache conf, systemd units, MediaMTX example config
- Per-input **monitoring/intelligence flags** (SCTE, freeze/black/bars, CC 608/708, ASR, Nielsen stub, live analyzer panes) + FTS caption search + export LKFS chart (see `docs/FEATURES.md`)
- `make test` and `make demo`

### Next (called out, not blocking)

- Live **DeckLink Duo / Quad 2** ingest (FFmpeg decklink input is assembled; needs drivers + `--enable-decklink` on the box)
- Production MediaMTX TLS / JWT / ICE the way NexVUE does on-station
- Copy Mode 2 `delivered_path` into NexClip `relative_dir`/`filename` on a shared MAM volume
- WAN redeem round-trip verified on a live hub box
- Proxy rendition written alongside native (export “proxy” currently transcodes on demand)
- Apache/mod_php production hardening, Let’s Encrypt, ufw (copy from NexVUE `setup.sh` as needed)
- DeckLink-side analyzers, Nielsen SDK, 64-ch RTA from SDI/AES, transcription GPU, live SCTE-35 tap — `docs/FEATURES.md`

## NexAPP: register each host

NexAPP never connects into the recorder. Cookie `NexAPP_AUTH` is host-only.

**Convention (all NexAPP WAN apps, including Recorder and standalone NexClip):**
one unique `service_id` per machine.

In NexAPP Admin, for **this** box:

1. Catalog: `service_id` (example `nexclip-recorder-ctl1`), display name, HTTPS launch URL.
2. `config.php` `launch.redeem_secrets.<service_id>` — same value as `NEXAPP_LAUNCH_SECRET` here.
3. Access grants for that `service_id` (`user` | `admin`).
4. Manifest: `web/nexapp-manifest.json` (`service_id`, `base_path`, icon).

A second recorder — or a second standalone NexClip host — gets a **different**
`service_id`. Do not share ids. Do not invent `instance_id` as a grant gate.

Operators sign in at `{NEXAPP_ISSUER}/launch.php?service_id=<id>&next=/…`.
The recorder redeems the ticket at `/api/launch/redeem.php`.

## NexClip: Mode 2 only

This box is `continuous_24x7`. See [`docs/NEXCLIP-HOOKS.md`](docs/NEXCLIP-HOOKS.md).
Mode 1 `scheduled_with_safety_net` (calendar starts/stops capture) is **not**
implemented.

## Demo path (no root, no DeckLink)

Requires `python3`, `ffmpeg`, `ffprobe`, `php`.

```bash
# Unit tests
make test

# Vertical slice: testsrc → 5s MP4 chunks → trim export
make demo
```

Then the UI (local PHP server):

```bash
./bin/nexrec-demo-ui.sh
# open http://127.0.0.1:8080
# login admin / password  (must change on first login)
```

Default demo input `demo` uses FFmpeg `testsrc`. To record a real IP source, set `SOURCE_TYPE=rtsp` (or `srt` / `udp`) and `SOURCE_URL` on Inputs, then:

```bash
python3 worker/nexrec-record.py --env ./nexrec.env --input-id demo
```

## Production install (Ubuntu)

Target OS is **Ubuntu 24.04 LTS** on the host class in **Hardware recommendations**.

```bash
sudo ./setup.sh
# edits /etc/nexrec/nexrec.env
# enable an input:
sudo cp inputs-example.env /etc/nexrec/inputs/demo.env
sudo systemctl enable --now nexrec-record@demo nexrec-preview@demo
sudo systemctl enable --now nexrec-cleanup.timer nexrec-export.service
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for pipelines, disk layout, auth, and the Mode 2 NexClip contract. Host sizing is **Hardware recommendations** (this README). Per-input intelligence flags: [docs/FEATURES.md](docs/FEATURES.md).

## Hardware recommendations

Owner-decided production guidance. Not a quote, not SKUs.

### Target host

| | Recommendation |
| --- | --- |
| **OS** | Ubuntu **24.04 LTS** (DeckLink Linux driver/SDK support — confirm against the current Blackmagic Linux matrix) |
| **Class** | **HP Z4/Z6 workstation** (or equivalent) with full PCIe for DeckLink + GPU. Not a thin desktop when using SDI capture or heavy transcode. |
| **CPU** | **12–16+ cores** (always-on record + concurrent export/transcode/viewers) |
| **RAM** | **128 GB**. Cap **ZFS ARC ~32–48 GB** so FFmpeg / NVENC / UI keep headroom. |
| **GPU** | **NVIDIA with NVENC** when IP ingest needs live transcode, WebRTC/proxy encodes for ~10 multiviewer clients, and occasional export transcode — while ~8 recorders keep writing. |
| **NIC** | **10GbE always** (even with local DAS — future NAS, bulk copy, other Nex* hosts). |
| **Boot / app / export scratch** | **NVMe** (ext4 or XFS). Keep export concat/trim scratch off the HDD pool when possible. |

### Workload (for sizing)

- Up to **~8 always-on 1080-ish** streams (not 10).
- Concurrent: live transcode path, **1–2 exports**, ~**10** WebRTC multiviewer viewers.
- Aggregate record write is only ~**15–25 MB/s** — bandwidth is easy. **Scrub/editor IOPS** and rebuild risk drive disk count.

### Storage capacity

- Rough raw rate: ~**35 TB/month** for 8× continuous 1080 @ ~12 Mbps.
- Product defaults (raw ~**4 weeks**, exports **15 days**) are smaller; operators may keep **2–3 months** online.
- Target usable media pool: about **100–135 TB** (plus the free-space floor the app enforces).

### Local storage (preferred) — ZFS

**Filesystem:** OpenZFS on the media pool. Do **not** use RAIDZ1 at this capacity. Avoid **USB** for the media pool. Workstation chassis rarely holds 8–12 large drives — attach via **SAS HBA + DAS/disk shelf**. Use **CMR enterprise** drives, **not SMR**.

| | Layout | Notes |
| --- | --- | --- |
| **Default (owner preference)** | **8–12× 16–22 TB** CMR in **RAIDZ2** | More spindles for **export-editor scrub IOPS**. |
| Fewer bays | **6× ~30 TB** CMR **RAIDZ2** | **Fine for record write bandwidth**; slower scrubs and longer resilvers. Not the default. |

Suggested split:

- Boot / app: NVMe
- Recordings: RAIDZ2 HDD pool (`ashift=12`, large `recordsize` e.g. **1M** on recording datasets)
- Export concat/trim scratch: NVMe

### NAS option

If media lives on a NAS: still keep **10GbE on the recorder box**. Size the NAS similarly (CMR, RAID6 or RAIDZ2, enough spindles for scrub).

### DeckLink

Need PCIe slots for **Duo / Quad 2**. Install Blackmagic drivers on 24.04; **verify against the current Blackmagic Linux support matrix** before buying a card/box pair.

## Per-input intelligence

Selectable sidecar features (SCTE, freeze/black/bars, captions, optional ASR, Nielsen stub, live WFM/vector/VU/RTA, export LKFS) are documented in **[docs/FEATURES.md](docs/FEATURES.md)**. They do not change the recorded MP4.

## License

Internal Nex* family tool. Do not publish secrets; env files are examples only.
