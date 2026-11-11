import os
import plistlib
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omnilaunch import runners  # noqa: E402
from omnilaunch.detect import Platform, detect  # noqa: E402


def _write(d, name, data, mode=0o755):
    p = os.path.join(d, name)
    with open(p, "wb") as f:
        f.write(data)
    os.chmod(p, mode)
    return p


def fake_elf(machine=0x3E):
    h = bytearray(64)
    h[:4] = b"\x7fELF"
    h[4], h[5] = 2, 1
    struct.pack_into("<HH", h, 16, 2, machine)
    return bytes(h)


def fake_pe(machine=0x8664, subsystem=2):
    h = bytearray(512)
    h[:2] = b"MZ"
    struct.pack_into("<I", h, 0x3C, 0x80)
    h[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", h, 0x84, machine)
    struct.pack_into("<H", h, 0x98, 0x20B)
    struct.pack_into("<H", h, 0x98 + 68, subsystem)
    return bytes(h)


def fake_macho(cpu=0x0100000C):
    return b"\xcf\xfa\xed\xfe" + struct.pack("<i", cpu) + bytes(24)


class DetectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_elf(self):
        i = detect(_write(self.d, "app", fake_elf()))
        self.assertEqual((i.platform, i.arch), (Platform.LINUX, "x86_64"))

    def test_pe_gui(self):
        i = detect(_write(self.d, "app.exe", fake_pe()))
        self.assertEqual((i.platform, i.arch, i.kind), (Platform.WINDOWS, "x86_64", "PE32+ (GUI)"))

    def test_macho(self):
        i = detect(_write(self.d, "tool", fake_macho()))
        self.assertEqual((i.platform, i.arch), (Platform.MACOS, "arm64"))

    def test_app_bundle(self):
        bundle = os.path.join(self.d, "Hello.app")
        os.makedirs(os.path.join(bundle, "Contents", "MacOS"))
        with open(os.path.join(bundle, "Contents", "Info.plist"), "wb") as f:
            plistlib.dump({"CFBundleExecutable": "Hello"}, f)
        _write(os.path.join(bundle, "Contents", "MacOS"), "Hello", fake_macho(0x01000007))
        i = detect(bundle)
        self.assertEqual((i.platform, i.arch), (Platform.MACOS, "x86_64"))
        self.assertTrue(i.executable.endswith("Contents/MacOS/Hello"))

    def test_script(self):
        i = detect(_write(self.d, "run.sh", b"#!/bin/sh\necho hi\n"))
        self.assertEqual(i.platform, Platform.SCRIPT)

    def test_java_class_not_macho(self):
        i = detect(_write(self.d, "A.class", b"\xca\xfe\xba\xbe\x00\x00\x00\x34" + bytes(8)))
        self.assertEqual(i.platform, Platform.UNKNOWN)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self._which = runners.which

    def tearDown(self):
        runners.which = self._which
        self.tmp.cleanup()

    def test_windows_uses_wine_with_isolated_prefix(self):
        runners.which = lambda *n: "/usr/bin/" + n[0]
        lp = runners.plan(detect(_write(self.d, "app.exe", fake_pe())))
        self.assertEqual(lp.runner, "wine")
        self.assertIn("prefixes/windows/app-", lp.env["WINEPREFIX"])

    def test_windows_without_wine_explains(self):
        runners.which = lambda *n: None
        with self.assertRaises(runners.UnsupportedApp) as cm:
            runners.plan(detect(_write(self.d, "app.exe", fake_pe())))
        self.assertIn("Wine", str(cm.exception))

    def test_macos_without_darling_explains(self):
        runners.which = lambda *n: None
        with self.assertRaises(runners.UnsupportedApp):
            runners.plan(detect(_write(self.d, "tool", fake_macho())))

    def test_native_linux(self):
        arch_code = {v: k for k, v in __import__("omnilaunch.detect").detect.ELF_MACHINES.items()}
        lp = runners.plan(detect(_write(self.d, "app", fake_elf(arch_code[runners.HOST_ARCH]))))
        self.assertEqual(lp.runner, "native")


if __name__ == "__main__":
    unittest.main()
