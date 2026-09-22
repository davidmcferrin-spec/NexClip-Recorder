#!/usr/bin/env python3
"""IP signal classification and app_settings overlay."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "worker"))

from nexrec_db import connect, migrate, overlay_app_settings  # noqa: E402
from nexrec_heartbeat import classify_ip, classify_transport, parse_status_text  # noqa: E402


class TestHeartbeat(unittest.TestCase):
    def test_ip_states(self):
        self.assertEqual(classify_ip(False, 5, 1, 300), "down")
        self.assertEqual(classify_ip(True, 2, None, 300), "receiving")
        self.assertEqual(classify_ip(True, 900, None, 300), "stalled")
        self.assertEqual(classify_ip(True, 10, 10, 300), "receiving")
        self.assertEqual(classify_ip(True, 10, 900, 300), "stalled")

    def test_transport(self):
        self.assertEqual(classify_transport("decklink"), "sdi")
        self.assertEqual(classify_transport("srt"), "ip")
        self.assertEqual(classify_transport("testsrc"), "demo")

    def test_decklink_kv(self):
        parsed = parse_status_text("signal=present\nlock=1\nformat=1080i59.94\n")
        self.assertEqual(parsed["signal"], "present")
        self.assertEqual(parsed["sdi_lock"], 1)
        self.assertEqual(parsed["format"], "1080i59.94")

    def test_overlay_prefers_db(self):
        tmp = tempfile.TemporaryDirectory()
        db = os.path.join(tmp.name, "nexrec.db")
        conn = connect(db)
        migrate(conn)
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?,?,?,?)",
            ("storage.free_space_floor", "4G", "t", "test"),
        )
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?,?,?,?)",
            ("nexapp.mode", "wan", "t", "test"),
        )
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?,?,?,?)",
            ("intelligence.nielsen_cmd", "", "t", "test"),
        )
        conn.commit()
        env = {"NEXREC_FREE_SPACE_FLOOR": "50G", "NEXREC_NIELSEN_PRESENCE_CMD": "/opt/nielsen/wrap"}
        out = overlay_app_settings(conn, env)
        self.assertEqual(out["NEXREC_FREE_SPACE_FLOOR"], "4G")
        self.assertEqual(out["NEXREC_DEPLOY_MODE"], "nexapp-wan")
        # A stored empty Nielsen command clears the process env (presence-only stub).
        self.assertEqual(out["NEXREC_NIELSEN_PRESENCE_CMD"], "")
        forced = overlay_app_settings(conn, {**env, "NEXREC_ENV_OVERRIDES": "1"})
        self.assertEqual(forced["NEXREC_FREE_SPACE_FLOOR"], "50G")
        conn.close()
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
