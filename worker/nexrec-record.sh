#!/bin/bash
# systemd ExecStart helper (optional). Units call python3 directly.
exec /usr/bin/python3 /opt/NexClip-Recorder/worker/nexrec-record.py "$@"
