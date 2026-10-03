#!/usr/bin/env python3
"""WHEP client reconnect / close contract."""

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


class TestWhep(unittest.TestCase):
    def test_js_whep_reconnect(self):
        node = _node()
        if not node:
            self.skipTest("node not available")
        script = os.path.join(HERE, "test_nexrec_whep.js")
        subprocess.check_call([node, script], cwd=ROOT)
