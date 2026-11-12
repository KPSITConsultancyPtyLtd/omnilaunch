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
from omnilaunch.detect import Platform, detect  # noqa: E402


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


HOST_ELF = 0x3E  # tests pretend to be an x86_64 Linux host unless they say otherwise


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
        self.host("linux", "x86_64")

    def host(self, os_name, arch):
        """Pretend to be a particular host, so tests behave the same on Linux and macOS."""
        for name, value in (("HOST_OS", os_name), ("HOST_ARCH", arch)):
            p = mock.patch.object(runners, name, value)
            p.start()
            self.addCleanup(p.stop)

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
        self.assertTrue(i.executable.endswith(os.path.join("Contents", "MacOS", "Hello")))

    def test_script(self):
        i = detect(_write(self.d, "run.sh", b"#!/usr/bin/env python3\nprint(1)\n"))
        self.assertEqual((i.platform, i.interpreter), (Platform.SCRIPT, "/usr/bin/env python3"))

    def test_java_class_not_macho(self):
        i = detect(_write(self.d, "A.class", b"\xca\xfe\xba\xbe\x00\x00\x00\x34" + bytes(8)))
        self.assertEqual(i.platform, Platform.UNKNOWN)

    def test_archives(self):
        self.assertEqual(detect(_write(self.d, "x.dmg", b"koly")).platform, Platform.ARCHIVE)
        self.assertEqual(detect(_write(self.d, "x.bin", b"PK\x03\x04data")).platform, Platform.ARCHIVE)


class RealHostTests(unittest.TestCase):
    """No simulation: checks against the computer the tests are actually running on."""

    def test_detects_own_python_as_native_app(self):
        expected = {"linux": Platform.LINUX, "macos": Platform.MACOS, "windows": Platform.WINDOWS}
        if runners.HOST_OS not in expected:
            self.skipTest(f"unsupported host {runners.HOST_OS}")
        info = detect(os.path.realpath(sys.executable))
        self.assertEqual(info.platform, expected[runners.HOST_OS], info)
        self.assertEqual(runners.plan(info).runner.split()[0], "native")

    def test_doctor_runs_for_real(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["doctor"]), 0)


class PlanTests(TmpCase):
    def test_native_linux_runs_in_app_folder(self):
        lp = runners.plan(detect(_write(self.d, "app", fake_elf(HOST_ELF))))
        self.assertEqual((lp.runner, lp.cwd), ("native", self.d))

    def test_32bit_runs_natively_on_64bit(self):
        self.assertEqual(runners.plan(detect(_write(self.d, "app", fake_elf(0x03)))).runner, "native")

    @unittest.skipIf(os.name == "nt", "Windows files have no executable flag")
    def test_not_executable_gives_chmod_hint(self):
        with self.assertRaises(runners.UnsupportedApp) as cm:
            runners.plan(detect(_write(self.d, "app", fake_elf(HOST_ELF), mode=0o644)))
        self.assertIn("chmod +x", cm.exception.hint)

    @unittest.skipIf(os.name == "nt", "Windows files have no executable flag")
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

    def test_universal_mac_binary_runs_in_darling(self):
        self.with_tools("darling")
        lp = runners.plan(detect(_write(self.d, "uni", fake_fat(0x01000007, 0x0100000C))))
        self.assertEqual(lp.argv[1:3], ["shell", "/Volumes/SystemRoot" + os.path.join(self.d, "uni")])

    def test_archive_explains(self):
        with self.assertRaises(runners.UnsupportedApp) as cm:
            runners.plan(detect(_write(self.d, "x.dmg", b"")))
        self.assertIn("7z", cm.exception.hint)


class MacHostTests(TmpCase):
    def setUp(self):
        super().setUp()
        self.host("macos", "arm64")

    def bundle(self, cpu):
        b = os.path.join(self.d, "Hello.app")
        os.makedirs(os.path.join(b, "Contents", "MacOS"))
        _write(os.path.join(b, "Contents", "MacOS"), "Hello", fake_macho(cpu))
        return b

    def test_app_bundle_opens_with_open(self):
        lp = runners.plan(detect(self.bundle(0x0100000C)), ["--x"])
        self.assertEqual(lp.argv[0], "/usr/bin/open")
        self.assertEqual(lp.argv[2:], ["--args", "--x"])

    def test_native_mac_binary(self):
        lp = runners.plan(detect(_write(self.d, "tool", fake_macho())))
        self.assertEqual(lp.runner, "native")

    def test_intel_app_needs_rosetta(self):
        app = detect(self.bundle(0x01000007))
        with mock.patch.object(runners, "rosetta_installed", return_value=False):
            with self.assertRaises(runners.UnsupportedApp) as cm:
                runners.plan(app)
        self.assertIn("install-rosetta", cm.exception.hint)
        with mock.patch.object(runners, "rosetta_installed", return_value=True):
            self.assertIn("Rosetta", " ".join(runners.plan(app).notes))

    def test_universal_runs_natively(self):
        lp = runners.plan(detect(_write(self.d, "uni", fake_fat(0x01000007, 0x0100000C))))
        self.assertEqual(lp.runner, "native")

    def test_linux_app_goes_to_container(self):
        self.with_tools("docker")
        app = _write(self.d, "tool", fake_elf())
        lp = runners.plan(detect(app), ["a"])
        self.assertEqual(lp.argv[:4], ["/usr/bin/docker", "run", "--rm", "-i"])
        self.assertIn("linux/amd64", lp.argv)
        self.assertEqual(lp.argv[-2:], [app, "a"])
        self.assertIn(f"{self.d}:{self.d}", lp.argv)

    def test_linux_app_without_engine_explains(self):
        self.with_tools()
        with self.assertRaises(runners.UnsupportedApp) as cm:
            runners.plan(detect(_write(self.d, "tool", fake_elf())))
        self.assertIn("OrbStack", cm.exception.hint)

    def test_windows_on_mac_uses_wine_never_proton(self):
        self.with_tools("wine")
        proton = _write(self.d, "proton", b"#!/bin/sh\n")
        with mock.patch.dict(os.environ, {"OMNILAUNCH_PROTON": proton}), \
                mock.patch.object(runners, "rosetta_installed", return_value=True):
            lp = runners.plan(detect(_write(self.d, "a.exe", fake_pe())))
        self.assertEqual(lp.runner, "wine")

    def test_mac_wine_app_bundle_found(self):
        self.with_tools()
        fake = os.path.join(self.d, "Wine Stable.app", "Contents", "Resources", "wine", "bin", "wine")
        os.makedirs(os.path.dirname(fake))
        _write(os.path.dirname(fake), "wine", b"#!/bin/sh\n")
        with mock.patch.object(runners, "MAC_WINE_GLOBS",
                               [os.path.join(self.d, "Wine*.app/Contents/Resources/wine/bin/wine")]):
            self.assertEqual(os.path.normpath(runners.find_wine()), os.path.normpath(fake))

    def test_wine_missing_on_mac_points_to_standalone_build(self):
        self.with_tools()
        with mock.patch.object(runners, "MAC_WINE_GLOBS", []):
            with self.assertRaises(runners.UnsupportedApp) as cm:
                runners.plan(detect(_write(self.d, "a.exe", fake_pe())))
        self.assertIn("Gcenx", cm.exception.hint)

    def test_darling_refused_on_mac(self):
        with self.assertRaises(runners.UnsupportedApp):
            runners.plan(detect(_write(self.d, "tool", fake_macho())), runner="darling")

    def test_mac_data_dir(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("OMNILAUNCH_HOME")
            self.assertTrue(runners.data_dir().endswith("Library/Application Support/OmniLaunch"))

    def test_integrate_not_supported(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["integrate"]), 1)

    def test_doctor_runs(self):
        self.with_tools()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(runners, "rosetta_installed", return_value=False):
            cli.main(["doctor"])
        self.assertIn("Rosetta", out.getvalue())


class FakeWinreg:
    """Just enough of the winreg module to test file association without Windows."""
    HKEY_CURRENT_USER, REG_SZ, REG_NONE, KEY_SET_VALUE, KEY_ALL_ACCESS = "HKCU", 1, 0, 2, 3

    def __init__(self):
        self.keys = {}

    class _Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def CreateKey(self, root, path):
        self.keys.setdefault(path, {})
        return self._Key(path)

    def OpenKey(self, root, path, *a):
        if path not in self.keys:
            raise OSError(path)
        return self._Key(path)

    def SetValueEx(self, key, name, _, kind, value):
        self.keys[key.path][name] = value

    def QueryValueEx(self, key, name):
        return self.keys[key.path][name], 1

    def DeleteValue(self, key, name):
        del self.keys[key.path][name]

    def DeleteKey(self, root, path):
        if path not in self.keys:
            raise OSError(path)
        del self.keys[path]


class WindowsHostTests(TmpCase):
    def setUp(self):
        super().setUp()
        self.host("windows", "x86_64")

    def test_exe_runs_natively(self):
        lp = runners.plan(detect(_write(self.d, "app.exe", fake_pe())), ["/s"])
        self.assertEqual((lp.runner, lp.argv[1:]), ("native", ["/s"]))

    def test_msi_bat_lnk(self):
        self.assertEqual(runners.plan(detect(_write(self.d, "a.msi", b"\xd0\xcf"))).argv[:2], ["msiexec", "/i"])
        self.assertEqual(runners.plan(detect(_write(self.d, "a.bat", b"echo"))).argv[:2], ["cmd", "/c"])
        self.assertEqual(runners.plan(detect(_write(self.d, "a.lnk", b"L\0\0\0"))).argv[:4],
                         ["cmd", "/c", "start", ""])

    def test_arm_exe_on_x64_rejected(self):
        with self.assertRaises(runners.UnsupportedApp):
            runners.plan(detect(_write(self.d, "a.exe", fake_pe(machine=0xAA64))))

    def test_x64_exe_on_arm_pc_runs_with_note(self):
        self.host("windows", "arm64")
        lp = runners.plan(detect(_write(self.d, "a.exe", fake_pe())))
        self.assertIn("emulates", " ".join(lp.notes))

    def test_linux_app_goes_to_wsl(self):
        self.with_tools("wsl")
        lp = runners.plan(detect(_write(self.d, "tool", fake_elf())), ["-la"])
        self.assertEqual(lp.runner, "wsl")
        self.assertEqual(lp.argv[0], "/usr/bin/wsl")
        self.assertEqual(lp.argv[1], "--cd")
        self.assertEqual(lp.argv[3:], ["--exec", runners.to_wsl_path(os.path.join(self.d, "tool")), "-la"])

    def test_linux_script_runs_via_interpreter_in_wsl(self):
        self.with_tools("wsl")
        lp = runners.plan(detect(_write(self.d, "s.sh", b"#!/bin/bash -e\n")))
        self.assertEqual(lp.argv[3:6], ["--exec", "/bin/bash", "-e"])

    def test_appimage_in_wsl_extracts(self):
        self.with_tools("wsl")
        lp = runners.plan(detect(_write(self.d, "a.AppImage", fake_elf(appimage=True))))
        self.assertIn("APPIMAGE_EXTRACT_AND_RUN=1", lp.argv)

    def test_linux_falls_back_to_container_without_wsl(self):
        self.with_tools("docker")
        lp = runners.plan(detect(_write(self.d, "tool", fake_elf())))
        self.assertTrue(lp.runner.startswith("container"))
        self.assertIn(f"{self.d}:/work", lp.argv)
        self.assertEqual(lp.argv[-1], "/work/tool")

    def test_no_wsl_explains(self):
        self.with_tools()
        with self.assertRaises(runners.UnsupportedApp) as cm:
            runners.plan(detect(_write(self.d, "tool", fake_elf())))
        self.assertIn("wsl --install", cm.exception.hint)

    def test_mac_app_explains(self):
        with self.assertRaises(runners.UnsupportedApp) as cm:
            runners.plan(detect(_write(self.d, "tool", fake_macho())))
        self.assertIn("Windows version", cm.exception.hint)

    def test_wine_refused_on_windows(self):
        with self.assertRaises(runners.UnsupportedApp):
            runners.plan(detect(_write(self.d, "a.exe", fake_pe())), runner="wine")

    def test_wsl_paths(self):
        t = runners.to_wsl_path
        self.assertEqual(t("C:\\Users\\me\\My Apps\\tool"), "/mnt/c/Users/me/My Apps/tool")
        self.assertEqual(t("D:/x/y"), "/mnt/d/x/y")
        self.assertEqual(t("\\\\wsl.localhost\\Ubuntu\\home\\me\\a"), "/home/me/a")
        self.assertEqual(t("\\\\wsl$\\Debian\\opt\\x"), "/opt/x")

    def test_wsl_distro_list_decodes_utf16(self):
        self.with_tools("wsl")
        out = "Ubuntu\r\nDebian\r\n".encode("utf-16-le")
        with mock.patch.object(runners.subprocess, "run", return_value=mock.Mock(stdout=out)):
            self.assertEqual(runners.wsl_distros(), ["Ubuntu", "Debian"])

    def test_windows_data_dir(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": os.path.join(self.d, "Local")}):
            os.environ.pop("OMNILAUNCH_HOME")
            self.assertEqual(runners.data_dir(), os.path.join(self.d, "Local", "OmniLaunch"))

    def test_file_association_round_trip(self):
        reg = FakeWinreg()
        with mock.patch.object(desktop, "_winreg", return_value=reg), \
                mock.patch.object(desktop, "_notify_shell_of_change"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["integrate"]), 0)
            cmd = reg.keys[r"Software\Classes\OmniLaunch.App\shell\open\command"][""]
            self.assertTrue(cmd.endswith('run --notify "%1"'))
            self.assertEqual(reg.keys[r"Software\Classes\.AppImage"][""], "OmniLaunch.App")
            self.assertTrue(desktop.windows_integrated())
            self.assertTrue(desktop.unintegrate())
            self.assertFalse(desktop.windows_integrated())
            self.assertNotIn("", reg.keys[r"Software\Classes\.AppImage"])

    def test_doctor_runs(self):
        self.with_tools("wsl")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(runners, "wsl_distros", return_value=["Ubuntu"]), \
                mock.patch.object(desktop, "windows_integrated", return_value=False):
            cli.main(["doctor"])
        self.assertIn("Linux apps (WSL)", out.getvalue())
        self.assertIn("Ubuntu", out.getvalue())


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

    def test_doctor_runs_on_linux(self):
        self.with_tools()
        self.assertEqual(self.run_cli("doctor")[0], 0)

    def test_desktop_exec_quotes_paths_with_spaces(self):
        self.assertEqual(desktop._exec_quote("/a b/py"), '"/a b/py"')
        self.assertEqual(desktop._exec_quote("/usr/bin/python3"), "/usr/bin/python3")

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
