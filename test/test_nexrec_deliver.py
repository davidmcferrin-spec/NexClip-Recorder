#!/usr/bin/env python3
"""Send-to: cipher, remote names, queue rules. rclone itself is not required."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "worker"))

from nexrec_deliver import (  # noqa: E402
    config_text,
    export_media_files,
    export_transfer_busy,
    prepare_source,
    promote_deliveries,
    release_export,
    remote_target,
    sweep_ready,
    sync_argv,
)
from nexrec_secret import decrypt_secret, encrypt_secret  # noqa: E402
from nexrec_util import iso_z, utcnow  # noqa: E402

VECTOR = "AAECAwQFBgcICQoLDA0OD8BTQqVNcWF7m5nzQgMo6olAONGYcCjaYW+bwfwhT2EOxKa6dI5D"


class TestSecret(unittest.TestCase):
    def test_vector_matches_php(self):
        blob = encrypt_secret("s3cret", "test-key", bytes(range(16)))
        self.assertEqual(blob, VECTOR)
        self.assertEqual(decrypt_secret(blob, "test-key"), "s3cret")
        self.assertNotIn("s3cret", blob)

    def test_wrong_key_rejected(self):
        blob = encrypt_secret("s3cret", "test-key", bytes(range(16)))
        with self.assertRaises(RuntimeError):
            decrypt_secret(blob, "other-key")


class TestRemote(unittest.TestCase):
    def test_same_export_id_is_the_overwrite_path(self):
        dest = {
            "protocol": "s3",
            "host": "minio.local",
            "remote_prefix": "incoming",
            "extra": json.dumps({"bucket": "media", "region": "us-east-1", "path_style": 1}),
        }
        remote, shown = remote_target(dest, "exp_abc")
        self.assertEqual(remote, "dest:media/incoming/exp_abc")
        self.assertEqual(shown, "s3://media/incoming/exp_abc/")
        again, _shown = remote_target(dest, "exp_abc")
        self.assertEqual(again, remote)

    def test_prefix_cannot_escape(self):
        dest = {"protocol": "sftp", "host": "files", "remote_prefix": "a/../../etc", "extra": "{}"}
        with self.assertRaises(RuntimeError):
            remote_target(dest, "exp_abc")

    def test_config_holds_obscured_secret_only(self):
        dest = {
            "protocol": "smb",
            "host": "nas",
            "username": "edit",
            "port": 445,
            "extra": json.dumps({"share": "media", "domain": "NEWS"}),
        }
        text = config_text(dest, "OBSCURED-TOKEN")
        self.assertIn("pass = OBSCURED-TOKEN", text)
        self.assertIn('user = "NEWS\\\\edit"', text)
        self.assertNotIn("hunter2", text)
        argv = sync_argv("rclone", "/tmp/c.conf", "/tmp/stage", "dest:media/exp_abc", None)
        self.assertIn("--ignore-times", argv)
        self.assertNotIn("hunter2", " ".join(argv))

    def test_ftp_config_can_require_tls(self):
        dest = {
            "protocol": "ftp",
            "host": "ftp.example",
            "username": "drop",
            "port": 21,
            "extra": json.dumps({"explicit_tls": 1}),
        }
        text = config_text(dest, "OBSCURED-TOKEN")
        self.assertIn("type = ftp", text)
        self.assertIn("explicit_tls = true", text)
        self.assertNotIn("hunter2", text)
        remote, shown = remote_target(
            {"protocol": "ftp", "host": "ftp.example", "remote_prefix": "in", "extra": "{}"},
            "exp_abc",
        )
        self.assertEqual(remote, "dest:in/exp_abc")
        self.assertEqual(shown, "ftp://ftp.example/in/exp_abc/")

    def test_multi_file_names_follow_the_export_id(self):
        job = {
            "id": "exp_abc",
            "input_ids": json.dumps(["cam", "studio"]),
            "path": "/var/lib/nexrec/storage/exports/exp_abc_cam.mp4",
        }
        files = export_media_files(job)
        self.assertEqual(
            [base for base, _path in files],
            ["exp_abc_cam.mp4", "exp_abc_studio.mp4"],
        )

    def test_stored_file_names_are_what_gets_copied(self):
        cam = "Charlie_20261001_145122-150122_a8c98ff5ed73.mp4"
        studio = "Delta_20261001_145122-150122_a8c98ff5ed73.mp4"
        job = {
            "id": "exp_a8c98ff5ed73",
            "input_ids": json.dumps(["cam", "studio"]),
            "path": "/var/lib/nexrec/storage/exports/" + cam,
            "file_names": json.dumps({"cam": cam, "studio": studio}),
        }
        files = export_media_files(job)
        self.assertEqual([base for base, _path in files], [cam, studio])
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        src = os.path.join(tmp.name, "src.mp4")
        with open(src, "wb") as fh:
            fh.write(b"media")
        stage = os.path.join(tmp.name, "stage")
        source, files_from = prepare_source([(cam, src)], stage)
        if files_from is None:
            self.assertEqual(os.listdir(source), [cam])
        else:
            listed = open(files_from, encoding="utf-8").read().split()
            self.assertEqual(listed, [cam])

    def test_stage_contains_only_this_export(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        src = os.path.join(tmp.name, "exp_abc.mp4")
        with open(src, "wb") as fh:
            fh.write(b"media")
        with open(os.path.join(tmp.name, "other.mp4"), "wb") as fh:
            fh.write(b"nope")
        stage = os.path.join(tmp.name, "stage")
        source, files_from = prepare_source([("exp_abc.mp4", src)], stage)
        if files_from is None:
            self.assertEqual(os.listdir(source), ["exp_abc.mp4"])
        else:
            listed = open(files_from, encoding="utf-8").read().split()
            self.assertEqual(listed, ["exp_abc.mp4"])
            self.assertNotEqual(source, stage)


class TestQueue(unittest.TestCase):
    def setUp(self):
        try:
            from nexrec_db import connect, migrate
        except ImportError as exc:
            self.skipTest(str(exc))
        self.connect = connect
        self.migrate = migrate
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        try:
            self.conn = connect(os.path.join(self.tmp.name, "nexrec.db"))
        except Exception as exc:  # noqa: BLE001 — this workstation may not have Postgres
            self.skipTest(str(exc))
        self.migrate(self.conn)
        from nexrec_db import fetchall, fetchone

        self.fetchall = fetchall
        self.fetchone = fetchone
        now = iso_z(utcnow())
        self.conn.execute(
            """INSERT INTO exports (id, status, input_ids, t_in, t_out, created_at, path)
               VALUES ('exp_abc', 'done', '["cam"]', ?, ?, ?, ?)""",
            (now, now, now, os.path.join(self.tmp.name, "exp_abc.mp4")),
        )
        self.conn.execute(
            """INSERT INTO destinations
               (id, name, protocol, host, remote_prefix, username, secret_cipher, extra, enabled, created_at, updated_at)
               VALUES ('dst_aabbccddeeff', 'Edit bay', 'sftp', 'files', '', 'edit', '', '{}', 1, ?, ?)""",
            (now, now),
        )
        self.conn.execute(
            """INSERT INTO deliveries (id, export_id, destination_id, status, created_at)
               VALUES ('dlv_001122334455', 'exp_abc', 'dst_aabbccddeeff', 'waiting', ?)""",
            (now,),
        )
        self.conn.commit()

    def test_promote_only_after_success_and_rerun_waits(self):
        self.assertEqual(promote_deliveries(self.conn, "exp_abc"), 1)
        row = self.fetchone(self.conn, "SELECT status FROM deliveries WHERE id='dlv_001122334455'")
        self.assertEqual(row["status"], "queued")
        self.conn.execute(
            "UPDATE exports SET status='queued', path=NULL WHERE id='exp_abc'"
        )
        self.conn.execute(
            """UPDATE deliveries
               SET status='waiting', cancel_requested=0, queued_at=NULL
               WHERE export_id='exp_abc' AND status IN ('queued','done','error','cancelled','waiting')"""
        )
        self.conn.commit()
        self.assertEqual(sweep_ready(self.conn), 0)
        self.conn.execute("UPDATE exports SET status='done' WHERE id='exp_abc'")
        self.conn.commit()
        self.assertEqual(sweep_ready(self.conn), 1)
        row = self.fetchone(self.conn, "SELECT status FROM deliveries WHERE export_id='exp_abc'")
        self.assertEqual(row["status"], "queued")

    def test_running_transfer_blocks_the_reencode(self):
        self.conn.execute("UPDATE deliveries SET status='running' WHERE export_id='exp_abc'")
        self.conn.commit()
        self.assertTrue(export_transfer_busy(self.conn, "exp_abc"))
        self.assertFalse(release_export(self.conn, "exp_abc"))
        self.assertEqual(len(self.fetchall(self.conn, "SELECT id FROM deliveries")), 1)


if __name__ == "__main__":
    unittest.main()
