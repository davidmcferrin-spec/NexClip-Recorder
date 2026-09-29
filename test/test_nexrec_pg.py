#!/usr/bin/env python3
"""Local Postgres connections stay off TLS and survive a dropped session."""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "worker"))

import psycopg2  # noqa: E402

import nexrec_db  # noqa: E402
from nexrec_db import PgConn, _pg_params  # noqa: E402


class FakeCursor:
    def __init__(self, raw: "FakeRaw") -> None:
        self.raw = raw

    def execute(self, sql: str, params=None) -> None:
        del sql, params
        self.raw.calls += 1
        if self.raw.fail_next:
            self.raw.fail_next = False
            raise psycopg2.OperationalError(
                "SSL connection has been closed unexpectedly"
            )


class FakeRaw:
    def __init__(self) -> None:
        self.closed = 0
        self.fail_next = False
        self.calls = 0
        self.rollbacks = 0

    def cursor(self, cursor_factory=None):
        del cursor_factory
        return FakeCursor(self)

    def rollback(self) -> None:
        self.rollbacks += 1
        raise psycopg2.InterfaceError("connection already closed")

    def close(self) -> None:
        self.closed = 1


class TestPg(unittest.TestCase):
    def test_sslmode_defaults_off(self):
        params = _pg_params(
            {
                "NEXREC_PGHOST": "127.0.0.1",
                "NEXREC_PGDATABASE": "nexrec",
                "NEXREC_PGUSER": "nexrec",
                "NEXREC_PGPASSWORD": "secret",
            }
        )
        self.assertEqual(params["sslmode"], "disable")
        forced = _pg_params(
            {
                "NEXREC_PGDATABASE": "nexrec",
                "NEXREC_PGUSER": "nexrec",
                "NEXREC_PGPASSWORD": "secret",
                "NEXREC_PGSSLMODE": "require",
            }
        )
        self.assertEqual(forced["sslmode"], "require")

    def test_dropped_connection_reconnects_once(self):
        dead = FakeRaw()
        dead.fail_next = True
        fresh = FakeRaw()
        opened: list[FakeRaw] = []

        def fake_open(params, schema):
            del params, schema
            opened.append(fresh)
            return fresh

        orig = nexrec_db._open_raw
        self.addCleanup(lambda: setattr(nexrec_db, "_open_raw", orig))
        nexrec_db._open_raw = fake_open

        conn = PgConn(dead, "public", {"host": "127.0.0.1"})
        cur = conn.execute("SELECT 1")
        self.assertIsInstance(cur, FakeCursor)
        self.assertEqual(dead.rollbacks, 1)
        self.assertEqual(opened, [fresh])
        self.assertIs(conn.raw, fresh)
        self.assertEqual(fresh.calls, 1)
        self.assertEqual(dead.calls, 1)


if __name__ == "__main__":
    unittest.main()
