# Demo path — ingest → chunks → export trim

## Automated (`make demo`)

`test/test_demo_pipeline.sh`:

1. Creates a temp data dir and SQLite DB
2. Inserts input `demo` (`SOURCE_TYPE=testsrc`, transcode on)
3. Runs `nexrec-record.py` with `NEXREC_SEGMENT_SECONDS=5` for ~12 s
4. Indexes closed MP4 chunks under `storage/inputs/demo/native/…`
5. Enqueues an export with in/out inside the recorded span
6. Runs `nexrec-export.py --once`
7. `ffprobe`s the file (H.264 + AAC in MP4, non-zero duration)

## UI

```bash
./bin/nexrec-demo-ui.sh
```

Login `admin` / `password`. Pages: Live, Export, Inputs, Settings, Users.

Live includes WFM / vectorscope / VU / 64-ch RTA **placeholders** (preview-path
analyzers are NEXT). Export includes a CALM/LKFS panel; measure enqueues
`nexrec-analyze.py`. Inputs has per-input intelligence toggles, a NexClip Mode 2 slot (1–8), and FTS search.

Without MediaMTX, Live panes are labeled placeholders (WHEP URL still shown).
Export editor scrubs indexed chunks (run `make demo` first, or click
**Seed demo chunks** on Inputs as admin).

## Real DeckLink (Duo / Quad 2)

Needs Ubuntu 24.04 and Blackmagic Desktop Video. Put the DeckLink SDK
headers on the host (`DECKLINK_SDK` or `NEXREC_DECKLINK_SDK`), then
`sudo ./setup.sh`. That builds FFmpeg 9.0.2 with `--enable-decklink` into
`/usr/local` and installs `nexrec-decklink-status`. Drivers and SDK headers
are different packages: Desktop Video creates `/dev/blackmagic`; the headers
are only needed at compile time. Without headers, setup still installs an
IP FFmpeg and says DeckLink was skipped. Confirm with
`/usr/local/bin/ffmpeg -hide_banner -f decklink -list_devices 1 -i dummy`.

1. Inputs → type DeckLink SDI. Refresh devices. Pick a name such as
   `DeckLink Quad 2 (1)` (or type the index). Optional format code `Hi59`.
   Leave **Keep 1080i interlaced** on unless you want the only upconvert
   (1080i → 1080p).
2. Enable `nexrec-record@<id>` only. Do **not** enable `nexrec-preview@<id>`.
   Services shows that preview unit as skipped. The record log should contain
   one `-f decklink`, `split=2`, and an RTSP URL on the same command.
3. Services → Signal should show locked and a mode (for example `1080i59.94`)
   while the input is recording (`busy` / “in use” is normal). With the helper
   missing, the row says it needs DeckLink tools and keeps the last stored format.
4. Live uses the same preview path (WHEP). Chunks under
   `storage/inputs/<id>/native/` are 5-minute (or your segment length) MP4,
   H.264 + AAC, clock-aligned names.

## Real IP source

Set on Inputs (or `/etc/nexrec/inputs/cam1.env`):

- Type `rtsp` / `srt` / `udp` / `tcp` / `rtp`
- URL, e.g. `rtsp://192.0.2.10:554/stream`
- Enable live transcode if the encoder is not already H.264+AAC

```bash
python3 worker/nexrec-record.py --env /etc/nexrec/nexrec.env --input-id cam1
```
