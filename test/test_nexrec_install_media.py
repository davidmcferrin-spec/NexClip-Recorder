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
            (include / "DeckLinkAPIVersion.h").write_text("/* v */\n", encoding="utf-8")
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
            (nested_inc / "DeckLinkAPIVersion.h").write_text("/* v */\n", encoding="utf-8")
            parent = Path(tmp) / "sdks"
            parent.mkdir()
            # Point the finder at the parent that holds the vendor folder name.
            vendor = parent / "Blackmagic DeckLink SDK 14.4"
            vendor_inc = vendor / "Linux" / "include"
            vendor_inc.mkdir(parents=True)
            (vendor_inc / "DeckLinkAPI.h").write_text("/* h */\n", encoding="utf-8")
            (vendor_inc / "DeckLinkAPIDispatch.cpp").write_text("// cpp\n", encoding="utf-8")
            (vendor_inc / "DeckLinkAPIVersion.h").write_text("/* v */\n", encoding="utf-8")
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

    def test_sdk_is_staged_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vendor = root / "Blackmagic DeckLink SDK 14.4" / "Linux" / "include"
            vendor.mkdir(parents=True)
            (vendor / "DeckLinkAPI.h").write_text("/* from vendor */\n", encoding="utf-8")
            (vendor / "DeckLinkAPIDispatch.cpp").write_text("// from vendor\n", encoding="utf-8")
            (vendor / "DeckLinkAPIVersion.h").write_text("/* ver */\n", encoding="utf-8")
            (vendor / "DeckLinkAPIModes.h").write_text("/* modes */\n", encoding="utf-8")
            stage = root / "decklink-sdk"
            found = bash(
                "nexrec_prepare_decklink_sdk",
                env={
                    "NEXREC_DECKLINK_SDK": str(root),
                    "NEXREC_DECKLINK_SCAN_SYSTEM": "0",
                    "NEXREC_DECKLINK_SDK_DIR": str(stage),
                },
            )
            self.assertEqual(found, str(stage))
            self.assertEqual((stage / "DeckLinkAPI.h").read_text(encoding="utf-8"), "/* from vendor */\n")
            self.assertEqual((stage / "DeckLinkAPIModes.h").read_text(encoding="utf-8"), "/* modes */\n")
            self.assertFalse(" " in found)
            other = root / "other-sdk"
            other.mkdir()
            (other / "DeckLinkAPI.h").write_text("/* other */\n", encoding="utf-8")
            (other / "DeckLinkAPIDispatch.cpp").write_text("// other\n", encoding="utf-8")
            (other / "DeckLinkAPIVersion.h").write_text("/* other */\n", encoding="utf-8")
            again = bash(
                "nexrec_prepare_decklink_sdk",
                env={
                    "NEXREC_DECKLINK_SDK": str(other),
                    "NEXREC_DECKLINK_SCAN_SYSTEM": "0",
                    "NEXREC_DECKLINK_SDK_DIR": str(stage),
                },
            )
            self.assertEqual(again, str(stage))
            self.assertEqual((stage / "DeckLinkAPI.h").read_text(encoding="utf-8"), "/* from vendor */\n")

    def test_sdk_16_zip_installs_full_include(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            home.mkdir()
            prefix = "Blackmagic DeckLink SDK 16.0/Linux/include/"
            zpath = home / "Blackmagic_DeckLink_SDK_16.0.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.writestr(prefix + "DeckLinkAPI.h", "/* zip */\n")
                zf.writestr(prefix + "DeckLinkAPIDispatch.cpp", "// zip\n")
                zf.writestr(prefix + "DeckLinkAPIVersion.h", "/* 16.0 */\n")
                zf.writestr(prefix + "DeckLinkAPIModes.h", "/* modes */\n")
            stage = root / "opt-decklink-sdk"
            found = bash(
                "nexrec_prepare_decklink_sdk",
                env={
                    "NEXREC_DECKLINK_SDK_HOME": str(home),
                    "NEXREC_DECKLINK_SDK_DIR": str(stage),
                    "NEXREC_DECKLINK_UNZIP_DIR": str(root / "unzip"),
                    "NEXREC_DECKLINK_SCAN_SYSTEM": "0",
                },
            )
            self.assertEqual(found, str(stage))
            self.assertEqual((stage / "DeckLinkAPI.h").read_text(encoding="utf-8"), "/* zip */\n")
            self.assertEqual((stage / "DeckLinkAPIModes.h").read_text(encoding="utf-8"), "/* modes */\n")
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.writestr(prefix + "DeckLinkAPI.h", "/* replaced */\n")
                zf.writestr(prefix + "DeckLinkAPIDispatch.cpp", "// zip\n")
                zf.writestr(prefix + "DeckLinkAPIVersion.h", "/* 16.0 */\n")
            again = bash(
                "nexrec_prepare_decklink_sdk",
                env={
                    "NEXREC_DECKLINK_SDK_HOME": str(home),
                    "NEXREC_DECKLINK_SDK_DIR": str(stage),
                    "NEXREC_DECKLINK_UNZIP_DIR": str(root / "unzip"),
                    "NEXREC_DECKLINK_SCAN_SYSTEM": "0",
                },
            )
            self.assertEqual(again, str(stage))
            self.assertEqual((stage / "DeckLinkAPI.h").read_text(encoding="utf-8"), "/* zip */\n")

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

    def test_nvdec_required_when_nvenc(self):
        desired = bash('nexrec_ffmpeg_desired_stamp 0 1 0 0 0 /usr/local')
        ver = "ffmpeg version 9.0.2 Copyright"
        enc_only = " ".join([
            "--enable-gpl", "--enable-nonfree", "--enable-libx264",
            "--enable-openssl", "--enable-ffnvcodec", "--enable-nvenc",
        ])
        both = enc_only + " --enable-nvdec --enable-cuvid"
        self.assertEqual(
            bash(f'nexrec_ffmpeg_decide 1 "{ver}" "{enc_only}" "{desired}" "{desired}" 0'),
            "build",
        )
        self.assertEqual(
            bash(f'nexrec_ffmpeg_decide 1 "{ver}" "{both}" "{desired}" "{desired}" 0'),
            "skip",
        )

    def test_nvidia_gpu_and_driver_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gpu = root / "0000:01:00.0"
            gpu.mkdir()
            (gpu / "vendor").write_text("0x10DE\n", encoding="utf-8")
            (gpu / "class").write_text("0x030200\n", encoding="utf-8")
            audio = root / "0000:01:00.1"
            audio.mkdir()
            (audio / "vendor").write_text("0x10de\n", encoding="utf-8")
            (audio / "class").write_text("0x040300\n", encoding="utf-8")
            intel = root / "0000:00:02.0"
            intel.mkdir()
            (intel / "vendor").write_text("0x8086\n", encoding="utf-8")
            (intel / "class").write_text("0x030000\n", encoding="utf-8")
            self.assertEqual(
                bash(
                    'nexrec_nvidia_gpu_present && echo yes || echo no',
                    env={"NEXREC_PCI_SYSFS": tmp},
                ),
                "yes",
            )
            self.assertEqual(
                bash(
                    'nexrec_nvenc_wanted && echo yes || echo no',
                    env={
                        "NEXREC_NVENC_PROBE": "",
                        "NEXREC_NVENC_HOST_PROBE": "0",
                        "NEXREC_PCI_SYSFS": tmp,
                    },
                ),
                "yes",
            )
            empty = root / "empty"
            empty.mkdir()
            self.assertEqual(
                bash(
                    'nexrec_nvidia_gpu_present && echo yes || echo no',
                    env={"NEXREC_PCI_SYSFS": str(empty)},
                ),
                "no",
            )
            self.assertEqual(
                bash(
                    'nexrec_nvenc_wanted && echo yes || echo no',
                    env={
                        "NEXREC_NVENC_PROBE": "",
                        "NEXREC_NVENC_HOST_PROBE": "0",
                        "NEXREC_PCI_SYSFS": str(empty),
                    },
                ),
                "no",
            )
        devices = """== /sys/devices/pci0000:46/0000:46:01.0/0000:47:00.0 ==
vendor   : NVIDIA Corporation
driver   : nvidia-driver-595-open - distro non-free recommended
driver   : nvidia-driver-610 - distro non-free
driver   : nvidia-driver-610-open - distro non-free
driver   : xserver-xorg-video-nouveau - distro free builtin
"""
        quoted = devices.replace("'", "'\\''")
        self.assertEqual(
            bash(f"nexrec_nvidia_driver_select_package '{quoted}'"),
            "nvidia-driver-610-open",
        )
        proprietary = devices.replace("driver   : nvidia-driver-610-open - distro non-free\n", "")
        quoted_prop = proprietary.replace("'", "'\\''")
        self.assertEqual(
            bash(f"nexrec_nvidia_driver_select_package '{quoted_prop}'"),
            "nvidia-driver-610",
        )
        only_old = """driver   : nvidia-driver-595-open - distro non-free recommended
"""
        self.assertEqual(
            bash(f"nexrec_nvidia_driver_select_package '{only_old}' || echo missing"),
            "missing",
        )
        self.assertEqual(bash("nexrec_nvidia_driver_select_package ''"), "nvidia-driver-610-open")
        self.assertEqual(bash("nexrec_nvidia_driver_major '595.91.07'"), "595")
        self.assertEqual(
            bash("nexrec_nvidia_driver_major 'NVIDIA RTX 2000 Ada Generation, 595.91.07'"),
            "595",
        )
        self.assertEqual(
            bash("nexrec_nvidia_driver_is_current '595.91.07' && echo yes || echo no"),
            "no",
        )
        self.assertEqual(
            bash("nexrec_nvidia_driver_is_current '610.57.01' && echo yes || echo no"),
            "yes",
        )
        self.assertEqual(
            bash("nexrec_nvidia_driver_is_current '580.95.05' && echo yes || echo no"),
            "no",
        )
        self.assertEqual(
            bash('nexrec_nvidia_install_wanted && echo yes || echo no'),
            "yes",
        )
        self.assertEqual(
            bash(
                'nexrec_nvidia_install_wanted && echo yes || echo no',
                env={"NEXREC_INSTALL_NVIDIA": "0"},
            ),
            "no",
        )

    def test_setup_calls_real_installer(self):
        setup = (ROOT / "setup.sh").read_text(encoding="utf-8")
        self.assertIn('bash "$ROOT/bin/nexrec-install-media.sh" all', setup)
        self.assertIn('bash "$ROOT/bin/nexrec-install-postgres.sh"', setup)
        self.assertIn("timedatectl set-timezone America/New_York", setup)
        self.assertIn("minpoll 11 maxpoll 12", setup)
        self.assertIn("/etc/chrony/sources.d/nexrec.sources", setup)
        self.assertNotIn("php-sqlite3", setup)
        self.assertNotIn("sqlite3", setup)
        apt = setup.split("apt-get install", 2)[1]
        self.assertNotIn(" ffmpeg ", " " + apt.split("chrony", 1)[0] + " ")
        unit = (ROOT / "systemd" / "mediamtx.service").read_text(encoding="utf-8")
        self.assertIn("nexrec-managed", unit)
        self.assertIn("/etc/nexrec/mediamtx.yml", unit)
        self.assertIn("8554", (ROOT / "mediamtx.yml").read_text(encoding="utf-8"))
        installer = (ROOT / "bin" / "nexrec-install-media.sh").read_text(encoding="utf-8")
        self.assertIn("--enable-decklink", installer)
        self.assertIn('--extra-cxxflags="-I${sdk}"', installer)
        self.assertIn("--apply-inputs", installer)
        self.assertIn("nexrec-decklink-configure", installer)
        self.assertIn("--enable-libx264", (ROOT / "bin" / "nexrec-install-media.sh").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
