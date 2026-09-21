#!/usr/bin/env python3
"""Chunk filename → start_at parsing."""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "worker"))

from nexrec_index import start_from_filename  # noqa: E402
from nexrec_util import valid_input_id  # noqa: E402


class TestIndex(unittest.TestCase):
    def test_filename(self):
        self.assertEqual(
            start_from_filename("/data/inputs/demo/native/2026/09/21/demo_20260921T150000Z.mp4"),
            "2026-09-21T15:00:00Z",
        )
        self.assertIsNone(start_from_filename("nope.mp4"))

    def test_input_id(self):
        self.assertTrue(valid_input_id("studio-a"))
        self.assertFalse(valid_input_id("../etc"))
        self.assertFalse(valid_input_id("A"))


if __name__ == "__main__":
    unittest.main()
