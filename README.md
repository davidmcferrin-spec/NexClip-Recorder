# NexCLIP Recorder

**NexCLIP Recorder** is a multi-input video recorder for broadcast studios.
The GitHub repository is `NexClip-Recorder`. It can run:

1. **Standalone** on its own machine (configure + use in the web UI)
2. As a **NexAPP WAN-only resource** (own host; identity via NexAPP SSO, including **multiple recorder instances**)
3. As a **recorder worker for NexClip** (studio recorder schedule → exports)

This is **v0.1.0**: a shippable scaffold with a working IP ingest → 5-minute (or demo 5-second) MP4 chunk → concat/trim export path, plus architecture for DeckLink, WebRTC preview, auth, retention, and NexClip hooks.

## Stack (matched to siblings)

NexAPP and NexClip were **not readable** from this environment (private / 404). The public sibling **[NexVUE](https://github.com/davidmcferrin-spec/NexVUE)** plus NexVUE’s NexAPP portal integration were used as the source of truth:

| Choice | Why |
| --- | --- |
| PHP 8 + Apache + vanilla JS | NexVUE edge + portal. No Node, no frontend framework, no Composer. |
| **SQLite WAL** (not Postgres) | NexVUE auth, metrics, and portal all use SQLite. A single-host recorder does not need a separate DB server. Switch to Postgres later if NexClip’s MAM already runs one — see `docs/ASSUMPTIONS.md`. |
| Python 3 **stdlib only** + FFmpeg | Workers. No pip. GNU C++ only if/when DeckLink SDK helpers are required (NexVUE pattern). |
| systemd units + timers | NexVUE `nexvue-encode@N`, heartbeat timers. Twice-daily cleanup is a systemd timer (cron-equivalent). |
| Dark UI, NexAPP `--nx-*` tokens | Portal uses IBM Plex + NexAPP tokens; edge uses monospace. Recorder UI follows the **NexAPP token set** with NexVUE-style top nav. |
| MediaMTX WHEP for preview | Same WebRTC path as NexVUE. Recording itself is **FFmpeg**, not GStreamer. |

Please re-open this PR against live NexAPP / NexClip trees if LDAP bind details, WAN ticket paths, or the studio recorder schedule JSON differ.

## What works in this PR vs next

### Works now (demo / standalone)

- Local auth (bcrypt users, sessions, roles: admin / operator / viewer)
- LDAP bind **implemented** (disabled until `NEXREC_LDAP_ENABLED=1`)
- NexAPP SSO **stubs that run**: JWT RS256 verify, AccessService / `/api/access.php`, WAN ticket query, multi-instance id
- Config schema (`nexrec-example.env`, `inputs-example.env`)
- Input CRUD in the UI (up to 10)
- FFmpeg segment recorder for **RTSP / SRT / UDP / TCP / RTP / testsrc**
- 5-minute (configurable) MP4 chunks, **wall-clock aligned**, NTP/system timecode metadata
- Chunk index in SQLite
- Export job: concat overlapping chunks + trim in/out → one Premiere/FCPX-friendly MP4
- Live multi-viewer **1 / 2 / 3 / 4 / 6** (time-lock chrome; WHEP player wired, placeholder if MediaMTX is down)
- Export editor: shared timeline, mark in/out, one-stream vs all-visible, full vs proxy
- Retention cleanup worker + **twice-daily systemd timer**
- NexClip schedule **API contract + poll/webhook scaffold**
- `setup.sh`, Apache conf, systemd units, MediaMTX example config
- `make test` and `make demo`

### Next (called out, not blocking)

- Live **DeckLink Duo / Quad 2** ingest (FFmpeg decklink input is assembled; needs drivers + `--enable-decklink` on the box)
- Production MediaMTX TLS / JWT / ICE the way NexVUE does on-station
- Real NexClip schedule payload once that repo is available
- NexAPP Alias `/nexclip-recorder` + WAN ticket round-trip verified on a hub box
- Proxy rendition written alongside native (export “proxy” currently transcodes on demand)
- Apache/mod_php production hardening, Let’s Encrypt, ufw (copy from NexVUE `setup.sh` as needed)

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

See [ARCHITECTURE.md](ARCHITECTURE.md) for pipelines, disk layout, auth, and NexClip hooks. Host sizing is **Hardware recommendations** (this README).

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

## License

Internal Nex* family tool. Do not publish secrets; env files are examples only.
