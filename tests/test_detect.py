import contextlib
import io
import os
import plistlib
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omnilaunch import cli, desktop, envs, runners  # noqa: E402
from omnilaunch.detect import ELF_MACHINES, Platform, detect  # noqa: E402


def _write(d, name, data, mode=0o755):
    p = os.path.join(d, name)
    with open(p, "wb") as f:
        f.write(data)
    os.chmod(p, mode)
    return p


def fake_elf(machine=0x3E, appimage=False):
    h = bytearray(64)
    h[:4] = b"\x7fELF"
    h[4], h[5] = 2, 1
    if appimage:
        h[8:11] = b"AI\x02"
    struct.pack_into("<HH", h, 16, 2, machine)
    return bytes(h)


def fake_pe(machine=0x8664, subsystem=2, dll=False):
    h = bytearray(512)
    h[:2] = b"MZ"
    struct.pack_into("<I", h, 0x3C, 0x80)
    h[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", h, 0x84, machine)
    struct.pack_into("<H", h, 0x80 + 22, 0x2000 if dll else 0x0102)
    struct.pack_into("<H", h, 0x98, 0x20B)
    struct.pack_into("<H", h, 0x98 + 68, subsystem)
    return bytes(h)


def fake_macho(cpu=0x0100000C):
    return b"\xcf\xfa\xed\xfe" + struct.pack("<i", cpu) + bytes(24)


def fake_fat(*cpus):
    body = b"".join(struct.pack(">IIIII", c, 0, 0, 0, 0) for c in cpus)
    return b"\xca\xfe\xba\xbe" + struct.pack(">I", len(cpus)) + body


HOST_ELF = {v: k for k, v in ELF_MACHINES.items()}[runners.HOST_ARCH]


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        patcher = mock.patch.dict(os.environ, {"OMNILAUNCH_HOME": os.path.join(self.d, "home"),
                                               "XDG_DATA_HOME": os.path.join(self.d, "xdg")})
        patcher.start()
        os.environ.pop("OMNILAUNCH_PROTON", None)
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def with_tools(self, *names):
        """Pretend only these compatibility tools are installed."""
        p = mock.patch.object(runners, "which",
                              lambda *n: next((f"/usr/bin/{x}" for x in n if x in names), None))
        p.start()
        self.addCleanup(p.stop)


class DetectTests(TmpCase):
    def test_elf(self):
        i = detect(_write(self.d, "app", fake_elf()))
        self.assertEqual((i.platform, i.arch), (Platform.LINUX, "x86_64"))

    def test_appimage(self):
        self.assertEqual(detect(_write(self.d, "a.AppImage", fake_elf(appimage=True))).kind, "AppImage")

    def test_truncated_files_do_not_crash(self):
        for name, data in (("e", b"\x7fELF"), ("m", b"MZ"), ("f", b"\xca\xfe\xba\xbe")):
            detect(_write(self.d, name, data))

    def test_pe_gui(self):
        i = detect(_write(self.d, "app.exe", fake_pe()))
        self.assertEqual((i.platform, i.arch, i.kind), (Platform.WINDOWS, "x86_64", "PE32+ (GUI)"))

    def test_renamed_exe_still_windows(self):
        self.assertEqual(detect(_write(self.d, "notes.txt", fake_pe())).platform, Platform.WINDOWS)

    def test_dll(self):
        self.assertIn("DLL", detect(_write(self.d, "x.dll", fake_pe(dll=True))).kind)

    def test_macho(self):
        i = detect(_write(self.d, "tool", fake_macho()))
        self.assertEqual((i.platform, i.arch), (Platform.MACOS, "arm64"))

    def test_universal_lists_archs(self):
        i = detect(_write(self.d, "uni", fake_fat(0x01000007, 0x0100000C)))
        self.assertEqual(i.archs, ("x86_64", "arm64"))

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
        i = detect(_write(self.d, "run.sh", b"#!/usr/bin/env python3\nprint(1)\n"))
        self.assertEqual((i.platform, i.interpreter), (Platform.SCRIPT, "/usr/bin/env python3"))

    def test_java_class_not_macho(self):
        i = detect(_write(self.d, "A.class", b"\xca\xfe\xba\xbe\x00\x00\x00\x34" + bytes(8)))
        self.assertEqual(i.platform, Platform.UNKNOWN)

    def test_archives(self):
        self.assertEqual(detect(_write(self.d, "x.dmg", b"koly")).platform, Platform.ARCHIVE)
        self.assertEqual(detect(_write(self.d, "x.bin", b"PK\x03\x04data")).platform, Platform.ARCHIVE)


class PlanTests(TmpCase):
    def test_native_linux_runs_in_app_folder(self):
        lp = runners.plan(detect(_write(self.d, "app", fake_elf(HOST_ELF))))
        self.assertEqual((lp.runner, lp.cwd), ("native", self.d))

    @unittest.skipUnless(runners.HOST_ARCH == "x86_64", "x86_64 host only")
    def test_32bit_runs_natively_on_64bit(self):
        self.assertEqual(runners.plan(detect(_write(self.d, "app", fake_elf(0x03)))).runner, "native")

    def test_not_executable_gives_chmod_hint(self):
        with self.assertRaises(runners.UnsupportedApp) as cm:
            runners.plan(detect(_write(self.d, "app", fake_elf(HOST_ELF), mode=0o644)))
        self.assertIn("chmod +x", cm.exception.hint)

    def test_non_executable_script_uses_interpreter(self):
        lp = runners.plan(detect(_write(self.d, "s.sh", b"#!/bin/sh\n", mode=0o644)))
        self.assertEqual(lp.argv[0], "/bin/sh")

    def test_windows_uses_wine_with_isolated_prefix(self):
        self.with_tools("wine64")
        lp = runners.plan(detect(_write(self.d, "app.exe", fake_pe())))
        self.assertEqual(lp.runner, "wine")
        self.assertIn(os.path.join("prefixes", "wine", "app-"), lp.env["WINEPREFIX"])
        self.assertEqual(lp.prefix, lp.env["WINEPREFIX"])

    def test_bat_and_msi(self):
        self.with_tools("wine")
        self.assertEqual(runners.plan(detect(_write(self.d, "a.bat", b"echo"))).argv[1:3], ["cmd", "/c"])
        self.assertEqual(runners.plan(detect(_write(self.d, "a.msi", b"\xd0\xcf"))).argv[1], "msiexec")

    def test_proton_used_when_configured_and_separate_prefix(self):
        proton = _write(self.d, "proton", b"#!/bin/sh\n")
        with mock.patch.dict(os.environ, {"OMNILAUNCH_PROTON": proton}):
            lp = runners.plan(detect(_write(self.d, "game.exe", fake_pe())))
        self.assertEqual(lp.runner, "proton")
        self.assertIn(os.path.join("prefixes", "proton"), lp.env["STEAM_COMPAT_DATA_PATH"])

    def test_runner_override(self):
        self.with_tools("wine")
        lp = runners.plan(detect(_write(self.d, "mystery", b"??")), runner="wine")
        self.assertEqual(lp.runner, "wine")

    def test_verbose_keeps_wine_debug(self):
        self.with_tools("wine")
        lp = runners.plan(detect(_write(self.d, "a.exe", fake_pe())), verbose=True)
        self.assertNotIn("WINEDEBUG", lp.env)

    def test_windows_without_wine_explains(self):
        self.with_tools()
        with self.assertRaises(runners.UnsupportedApp) as cm:
            runners.plan(detect(_write(self.d, "app.exe", fake_pe())))
        self.assertIn("Wine", str(cm.exception))

    def test_dll_rejected(self):
        self.with_tools("wine")
        with self.assertRaises(runners.UnsupportedApp):
            runners.plan(detect(_write(self.d, "x.dll", fake_pe(dll=True))))

    def test_macos_without_darling_explains(self):
        self.with_tools()
        with self.assertRaises(runners.UnsupportedApp):
            runners.plan(detect(_write(self.d, "tool", fake_macho())))

    def test_apple_silicon_only_rejected(self):
        self.with_tools("darling")
        with self.assertRaises(runners.UnsupportedApp) as cm:
            runners.plan(detect(_write(self.d, "tool", fake_macho())))
        self.assertIn("Intel", cm.exception.hint)

    @unittest.skipUnless(runners.HOST_ARCH == "x86_64", "Darling is x86_64-only")
    def test_universal_mac_binary_runs_in_darling(self):
        self.with_tools("darling")
        lp = runners.plan(detect(_write(self.d, "uni", fake_fat(0x01000007, 0x0100000C))))
        self.assertEqual(lp.argv[1:3], ["shell", "/Volumes/SystemRoot" + os.path.join(self.d, "uni")])

    def test_archive_explains(self):
        with self.assertRaises(runners.UnsupportedApp) as cm:
            runners.plan(detect(_write(self.d, "x.dmg", b"")))
        self.assertIn("7z", cm.exception.hint)


class CliTests(TmpCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_after_app_is_not_passed_to_app(self):
        app = _write(self.d, "app", fake_elf(HOST_ELF))
        code, out, _ = self.run_cli("run", app, "--dry-run", "x")
        self.assertEqual(code, 0)
        self.assertTrue(out.strip().endswith(f"{app} x"))

    def test_double_dash_passes_flags_to_app(self):
        app = _write(self.d, "app", fake_elf(HOST_ELF))
        _, out, _ = self.run_cli("run", "--dry-run", app, "--", "--dry-run")
        self.assertTrue(out.strip().endswith(f"{app} --dry-run"))

    def test_missing_file(self):
        self.assertEqual(self.run_cli("run", os.path.join(self.d, "nope"))[0], 2)

    def test_envs_list_and_remove(self):
        envs.record_launch(os.path.join(self.d, "home", "prefixes", "wine", "app-1"), "/x/app.exe")
        _, out, _ = self.run_cli("envs", "list")
        self.assertIn("wine/app-1", out)
        self.assertEqual(self.run_cli("envs", "remove", "../../..")[0], 2)
        self.assertEqual(self.run_cli("envs", "remove", "wine")[0], 2)
        self.assertEqual(self.run_cli("envs", "remove", "wine/app-1")[0], 0)
        self.assertEqual(envs.list_envs(), [])

    def test_integrate_writes_desktop_file(self):
        with mock.patch.object(desktop.shutil, "which", return_value=None):
            self.run_cli("integrate")
            path = os.path.join(self.d, "xdg", "applications", "omnilaunch.desktop")
            with open(path) as f:
                text = f.read()
            self.assertIn("run --notify %f", text)
            self.assertIn("application/x-ms-dos-executable", text)
            self.run_cli("integrate", "--remove")
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
