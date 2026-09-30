#!/usr/bin/env python3
"""Pane assignment and Export share-link contract."""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _node() -> str | None:
    env = os.environ.get("NODE")
    for cand in (env, shutil.which("node"), "/exec-daemon/node"):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


class TestPanes(unittest.TestCase):
    def test_js_pane_and_share_link(self):
        node = _node()
        if not node:
            self.skipTest("node not available")
        script = os.path.join(HERE, "test_nexrec_panes.js")
        subprocess.check_call([node, script], cwd=ROOT)
