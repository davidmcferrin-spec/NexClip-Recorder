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
`nexrec-analyze.py`. Inputs has per-input intelligence toggles and FTS search.

Without MediaMTX, Live panes are labeled placeholders (WHEP URL still shown).
Export editor scrubs indexed chunks (run `make demo` first, or click
**Seed demo chunks** on Inputs as admin).

## Real IP source

Set on Inputs (or `/etc/nexrec/inputs/cam1.env`):

- Type `rtsp` / `srt` / `udp` / `tcp` / `rtp`
- URL, e.g. `rtsp://192.0.2.10:554/stream`
- Enable live transcode if the encoder is not already H.264+AAC

```bash
python3 worker/nexrec-record.py --env /etc/nexrec/nexrec.env --input-id cam1
```
