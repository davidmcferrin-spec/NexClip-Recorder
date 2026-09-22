# nexrec-decklink-status

Signal helper for NexCLIP Recorder. It does **not** capture video. Recording stays `ffmpeg -f decklink` inside `nexrec-record`. This binary only answers “is this SDI input locked, and in what mode?”

Blackmagic’s status flags report lock only while an input stream is already running. An idle connector would otherwise look unlocked. The tool therefore:

1. Opens the input briefly with format detection when nothing else holds it.
2. If the open fails because `nexrec-record` already has the sub-device (DeckLink inputs are exclusive-open), reads `IDeckLinkStatus` instead. Lock and mode are still valid in that case (`busy: true`).

## JSON

Same shape operators see from NexVUE’s status tool:

```json
{"devices":[{"index":0,"name":"DeckLink Quad 2 (1)","input_locked":true,"input_mode":"1080i59.94","reference_locked":false,"reference_mode":"unknown","busy":false}]}
```

`index` follows `IDeckLinkIterator`, which is the same order as `ffmpeg -f decklink -list_devices 1`. Duo and Quad 2 names look like `DeckLink Duo (1)` and `DeckLink Quad 2 (1)`.

Optional filter (Recorder passes the configured device so a probe does not open every idle connector):

```bash
nexrec-decklink-status "DeckLink Quad 2 (1)"
nexrec-decklink-status 0
```

No arguments prints every sub-device. Key/value stdout (`signal=`, `lock=`, `format=`) is still accepted by Services if you point `NEXREC_DECKLINK_STATUS_BIN` at an older helper.

## Build (Ubuntu 24.04)

Install the current Blackmagic Desktop Video driver for 24.04 (check Blackmagic’s Linux support matrix). The DeckLink SDK headers ship with that package or with the SDK download. They are not in this repo.

```bash
# Directory that contains DeckLinkAPI.h and DeckLinkAPIDispatch.cpp
`sudo ./setup.sh` builds and installs this when `DECKLINK_SDK` or
`NEXREC_DECKLINK_SDK` (or a conventional SDK path) contains `DeckLinkAPI.h`.
Desktop Video drivers are separate and are not downloaded by setup.

```bash
cd tools/decklink-status
make SDK=/path/to/decklink-sdk/include
sudo make install          # /usr/local/bin/nexrec-decklink-status
```
```

Then either leave Setup’s “DeckLink status helper” blank (Recorder also looks in `/usr/local/bin`) or set:

```text
NEXREC_DECKLINK_STATUS_BIN=/usr/local/bin/nexrec-decklink-status
```

`www-data` must be able to open the devices (Desktop Video udev rules, or membership in the `video` group). `setup.sh` adds `www-data` to `video` when that group exists.

Confirm:

```bash
nexrec-decklink-status
ffmpeg -hide_banner -f decklink -list_devices 1 -i dummy
```
