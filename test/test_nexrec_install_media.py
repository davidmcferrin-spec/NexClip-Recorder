#!/usr/bin/env python3
"""Decision tests for bin/nexrec-install-media.sh. No compile, no root."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "nexrec-install-media.sh"


def bash(body: str, env: dict | None = None) -> str:
    script = (
        "set -euo pipefail\n"
        "NEXREC_INSTALL_SOURCE_ONLY=1\n"
        f"source '{SCRIPT}'\n"
        + body
    )
    merged = os.environ.copy()
    merged["NEXREC_INSTALL_SOURCE_ONLY"] = "1"
    # Keep the host's SDK/toolkit from changing assertions.
    merged["NEXREC_DECKLINK_SCAN_SYSTEM"] = "0"
    merged.pop("NEXREC_DECKLINK_SDK", None)
    merged.pop("DECKLINK_SDK", None)
    merged.pop("NEXREC_ENABLE_NVENC", None)
    merged["NEXREC_NVENC_PROBE"] = "0"
    if env:
        merged.update(env)
    proc = subprocess.run(
        ["bash", "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=merged,
    )
    return proc.stdout.strip()


class InstallMediaTests(unittest.TestCase):
    def test_script_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_pins(self):
        out = bash(
            "printf '%s\\n' \"$NEXREC_FFMPEG_VERSION\" \"$NEXREC_FFMPEG_SHA256\" "
            "\"$NEXREC_MEDIAMTX_VERSION\" \"$(nexrec_mediamtx_sha256 amd64)\" "
            "\"$(nexrec_mediamtx_url amd64)\""
        )
        version, ffmpeg_sha, mtx, mtx_sha, url = out.splitlines()
        self.assertEqual(version, "9.0.2")
        self.assertEqual(len(ffmpeg_sha), 64)
        self.assertEqual(mtx, "v1.21.1")
        self.assertEqual(len(mtx_sha), 64)
        self.assertIn("mediamtx_v1.21.1_linux_amd64.tar.gz", url)

    def test_arch(self):
        self.assertEqual(bash('nexrec_mediamtx_arch_of x86_64'), "amd64")
        self.assertEqual(bash('nexrec_mediamtx_arch_of aarch64'), "arm64")
        self.assertEqual(bash('nexrec_mediamtx_arch_of armv7l'), "armv7")

    def test_ffmpeg_decide(self):
        desired = bash(
            'nexrec_ffmpeg_desired_stamp 1 0 1 1 1 /usr/local'
        )
        conf = " ".join([
            "--enable-gpl", "--enable-nonfree", "--enable-libx264",
            "--enable-openssl", "--enable-decklink", "--enable-libfdk-aac",
            "--enable-libsrt", "--enable-libzvbi",
        ])
        ver = "ffmpeg version 9.0.2 Copyright"
        self.assertEqual(
            bash(f'nexrec_ffmpeg_decide 0 "" "" "" "{desired}" 0'),
            "build",
        )
        self.assertEqual(
            bash(f'nexrec_ffmpeg_decide 1 "ffmpeg version 8.0" "{conf}" "{desired}" "{desired}" 0'),
            "build",
        )
        self.assertEqual(
            bash(f'nexrec_ffmpeg_decide 1 "{ver}" "--enable-gpl" "{desired}" "{desired}" 0'),
            "build",
        )
        nodeck = conf.replace("--enable-decklink", "")
        self.assertEqual(
            bash(f'nexrec_ffmpeg_decide 1 "{ver}" "{nodeck}" "{desired}" "{desired}" 0'),
            "build",
        )
        self.assertEqual(
            bash(f'nexrec_ffmpeg_decide 1 "{ver}" "{conf}" "{desired}" "{desired}" 0'),
            "skip",
        )
        self.assertEqual(
            bash(f'nexrec_ffmpeg_decide 1 "{ver}" "{conf}" "" "{desired}" 0'),
            "skip",
        )
        self.assertEqual(
            bash(f'nexrec_ffmpeg_decide 1 "{ver}" "{conf}" "version=8.0.0" "{desired}" 0'),
            "build",
        )
        self.assertEqual(
            bash(f'nexrec_ffmpeg_decide 1 "{ver}" "{conf}" "{desired}" "{desired}" 1'),
            "build",
        )

    def test_mediamtx_decide(self):
        self.assertEqual(bash('nexrec_mediamtx_decide 0 "" v1.21.1 0'), "install")
        self.assertEqual(bash('nexrec_mediamtx_decide 1 v1.21.1 v1.21.1 0'), "skip")
        self.assertEqual(bash('nexrec_mediamtx_decide 1 "" v1.21.1 0'), "skip")
        self.assertEqual(bash('nexrec_mediamtx_decide 1 v1.20.0 v1.21.1 0'), "install")
        self.assertEqual(bash('nexrec_mediamtx_decide 1 v1.21.1 v1.21.1 1'), "install")

    def test_config_and_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.yml"
            present = Path(tmp) / "mediamtx.yml"
            present.write_text("custom: yes\n", encoding="utf-8")
            foreign = Path(tmp) / "foreign.service"
            foreign.write_text("[Service]\nExecStart=/opt/mediamtx\n", encoding="utf-8")
            managed = Path(tmp) / "ours.service"
            managed.write_text("# nexrec-managed\n[Service]\n", encoding="utf-8")
            self.assertEqual(bash(f'nexrec_config_action "{missing}" 0'), "write")
            self.assertEqual(bash(f'nexrec_config_action "{present}" 0'), "keep")
            self.assertEqual(bash(f'nexrec_config_action "{present}" 1'), "write")
            self.assertEqual(bash(f'nexrec_unit_action "{missing}" 0'), "write")
            self.assertEqual(bash(f'nexrec_unit_action "{foreign}" 0'), "keep")
            self.assertEqual(bash(f'nexrec_unit_action "{foreign}" 1'), "write")
            self.assertEqual(bash(f'nexrec_unit_action "{managed}" 0'), "write")

    def test_sdk_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            include = Path(tmp) / "Linux" / "include"
            include.mkdir(parents=True)
            (include / "DeckLinkAPI.h").write_text("/* h */\n", encoding="utf-8")
            (include / "DeckLinkAPIDispatch.cpp").write_text("// cpp\n", encoding="utf-8")
            found = bash(
                'nexrec_find_decklink_include',
                env={"NEXREC_DECKLINK_SDK": tmp, "NEXREC_DECKLINK_SCAN_SYSTEM": "0"},
            )
            self.assertEqual(found, str(include))
            nested = Path(tmp) / "Blackmagic DeckLink SDK 14.4"
            nested_inc = nested / "Linux" / "include"
            nested_inc.mkdir(parents=True)
            (nested_inc / "DeckLinkAPI.h").write_text("/* h */\n", encoding="utf-8")
            (nested_inc / "DeckLinkAPIDispatch.cpp").write_text("// cpp\n", encoding="utf-8")
            parent = Path(tmp) / "sdks"
            parent.mkdir()
            # Point the finder at the parent that holds the vendor folder name.
            vendor = parent / "Blackmagic DeckLink SDK 14.4"
            vendor_inc = vendor / "Linux" / "include"
            vendor_inc.mkdir(parents=True)
            (vendor_inc / "DeckLinkAPI.h").write_text("/* h */\n", encoding="utf-8")
            (vendor_inc / "DeckLinkAPIDispatch.cpp").write_text("// cpp\n", encoding="utf-8")
            found_vendor = bash(
                'nexrec_find_decklink_include',
                env={"NEXREC_DECKLINK_SDK": str(parent), "NEXREC_DECKLINK_SCAN_SYSTEM": "0"},
            )
            self.assertEqual(found_vendor, str(vendor_inc))
            self.assertEqual(
                bash(
                    'nexrec_find_decklink_include || true',
                    env={"NEXREC_DECKLINK_SCAN_SYSTEM": "0"},
                ),
                "",
            )

    def test_nvenc_switch(self):
        self.assertEqual(
            bash('nexrec_nvenc_wanted && echo yes || echo no', env={"NEXREC_ENABLE_NVENC": "0"}),
            "no",
        )
        self.assertEqual(
            bash('nexrec_nvenc_wanted && echo yes || echo no', env={"NEXREC_ENABLE_NVENC": "1", "NEXREC_NVENC_PROBE": "0"}),
            "yes",
        )
        self.assertEqual(
            bash('nexrec_nvenc_wanted && echo yes || echo no', env={"NEXREC_NVENC_PROBE": "0"}),
            "no",
        )

    def test_setup_calls_real_installer(self):
        setup = (ROOT / "setup.sh").read_text(encoding="utf-8")
        self.assertIn('bash "$ROOT/bin/nexrec-install-media.sh" all', setup)
        self.assertIn('bash "$ROOT/bin/nexrec-install-postgres.sh"', setup)
        self.assertNotIn("php-sqlite3", setup)
        self.assertNotIn("sqlite3", setup)
        apt = setup.split("apt-get install", 2)[1]
        self.assertNotIn(" ffmpeg ", " " + apt.split("chrony", 1)[0] + " ")
        unit = (ROOT / "systemd" / "mediamtx.service").read_text(encoding="utf-8")
        self.assertIn("nexrec-managed", unit)
        self.assertIn("/etc/nexrec/mediamtx.yml", unit)
        self.assertIn("8554", (ROOT / "mediamtx.yml").read_text(encoding="utf-8"))
        self.assertIn("--enable-decklink", (ROOT / "bin" / "nexrec-install-media.sh").read_text(encoding="utf-8"))
        self.assertIn("--enable-libx264", (ROOT / "bin" / "nexrec-install-media.sh").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
