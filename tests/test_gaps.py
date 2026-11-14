"""Icons for launchers, removing apps cleanly, XQuartz's network setting, first-launch notice, update."""

import os
import struct
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omnilaunch import cli, desktop, envs, icons, runners, shortcuts  # noqa: E402
from omnilaunch.detect import detect  # noqa: E402
from test_detect import TmpCase, _write  # noqa: E402
from test_native_feel import make_lnk  # noqa: E402
from test_setup_vm import quiet  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + bytes(40)


def pe_with_icon(icon_bytes=PNG):
    """A minimal 64-bit Windows program whose resources hold one icon group with one icon."""
    def directory(entries):          # entries: [(id, offset, is_dir)]
        out = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, len(entries))
        for eid, off, is_dir in entries:
            out += struct.pack("<II", eid, off | (0x80000000 if is_dir else 0))
        return out
    va = 0x1000
    root = directory([(3, 32, True), (14, 56, True)])           # 32 bytes
    t_icon = directory([(1, 80, True)])                          # @32
    t_group = directory([(1, 104, True)])                        # @56
    l_icon = directory([(1033, 128, False)])                     # @80
    l_group = directory([(1033, 144, False)])                    # @104
    icon_off = 160
    group = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHIH", 0, 0, 0, 0, 1, 32, len(icon_bytes), 1)
    group_off = icon_off + len(icon_bytes) + (-len(icon_bytes) % 4)
    d_icon = struct.pack("<IIII", va + icon_off, len(icon_bytes), 0, 0)
    d_group = struct.pack("<IIII", va + group_off, len(group), 0, 0)
    rsrc = root + t_icon + t_group + l_icon + l_group + d_icon + d_group
    rsrc += icon_bytes + bytes(group_off - icon_off - len(icon_bytes)) + group
    opt = bytearray(240)
    struct.pack_into("<H", opt, 0, 0x20B)
    struct.pack_into("<II", opt, 112 + 16, va, len(rsrc))
    coff = struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, 240, 0x22)
    section = b".rsrc\0\0\0" + struct.pack("<IIIIIIHHI", len(rsrc), va, len(rsrc), 0x200, 0, 0, 0, 0, 0x40000040)
    head = bytearray(0x40)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x40)
    pe = bytes(head) + b"PE\0\0" + coff + bytes(opt) + section
    return pe + bytes(0x200 - len(pe)) + rsrc


class IconTests(TmpCase):
    def test_icon_extracted_as_ico_with_png(self):
        exe = _write(self.d, "app.exe", pe_with_icon())
        ico = icons.extract_ico(exe)
        self.assertEqual(ico[:6], struct.pack("<HHH", 0, 1, 1))
        self.assertEqual(icons.largest_png(ico), PNG)

    def test_program_without_icon(self):
        from test_detect import fake_pe
        self.assertIsNone(icons.extract_ico(_write(self.d, "plain.exe", fake_pe())))
        self.assertIsNone(icons.extract_ico(_write(self.d, "junk.exe", b"MZ")))

    def test_linux_launcher_uses_the_programs_icon(self):
        exe = _write(self.d, "app.exe", pe_with_icon())
        path = shortcuts.create_launcher("Foo", "wine/foo-1", exe)
        text = open(path).read()
        icon_line = [ln for ln in text.splitlines() if ln.startswith("Icon=")][0]
        self.assertTrue(icon_line.endswith("foo.png"), icon_line)
        self.assertTrue(os.path.exists(icon_line[5:]))
        self.assertIn("X-OmniLaunch-Env=wine/foo-1", text)


class DesktopDirTests(TmpCase):
    def test_missing_desktop_folder_is_skipped(self):
        with mock.patch.dict(os.environ, {"XDG_DESKTOP_DIR": os.path.join(self.d, "nope")}):
            self.assertIsNone(shortcuts.desktop_dir())


class RemoveTests(TmpCase):
    def install(self, env, name):
        root = os.path.join(runners.data_dir(), "prefixes", *env.split("/"))
        app_dir = os.path.join(root, "drive_c", "Program Files", name)
        os.makedirs(app_dir)
        _write(app_dir, "app.exe", pe_with_icon())
        menu = os.path.join(root, "drive_c", "ProgramData", "Microsoft", "Windows", "Start Menu", "Programs")
        os.makedirs(menu, exist_ok=True)
        _write(menu, f"{name}.lnk", make_lnk(f"C:\\Program Files\\{name}\\app.exe"))
        shortcuts.scan(root, env)
        return root

    def test_envs_remove_takes_launchers_and_desktop_shortcuts_away(self):
        self.install("wine/7z-1", "7-Zip File Manager")
        self.install("wine/putty-2", "PuTTY")
        envs.remove_env("wine/7z-1")
        names = [shortcuts.launcher_name(p) for p in shortcuts.list_launchers()]
        self.assertEqual(names, ["PuTTY"])
        self.assertEqual(os.listdir(os.path.join(self.d, "Desktop")), ["omnilaunch-putty.desktop"])

    def test_apps_remove_keeps_data_unless_asked(self):
        root = self.install("wine/putty-2", "PuTTY")
        code, out = quiet(cli.main, ["apps", "remove", "putty"])
        self.assertEqual(code, 0)
        self.assertEqual(shortcuts.list_launchers(), [])
        self.assertTrue(os.path.isdir(root))
        self.assertIn("--with-data", out)

    def test_apps_remove_with_data(self):
        root = self.install("wine/putty-2", "PuTTY")
        quiet(cli.main, ["apps", "remove", "PuTTY", "--with-data"])
        self.assertFalse(os.path.exists(root))

    def test_apps_remove_unknown(self):
        self.assertEqual(quiet(cli.main, ["apps", "remove", "Nope"])[0], 2)


class XQuartzTests(TmpCase):
    def test_turns_on_network_clients_and_restarts_xquartz(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return mock.Mock(returncode=0, stdout="1\n")      # currently off
        with mock.patch.object(runners.subprocess, "run", side_effect=fake_run), \
                mock.patch("time.sleep"):
            self.assertTrue(runners.ensure_xquartz_network())
        self.assertIn(["/usr/bin/defaults", "write", "org.xquartz.X11", "nolisten_tcp", "-bool", "false"], calls)
        self.assertTrue(any("quit application" in " ".join(c) for c in calls))

    def test_leaves_xquartz_alone_when_already_on(self):
        with mock.patch.object(runners.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="0\n")) as run:
            self.assertTrue(runners.ensure_xquartz_network())
        self.assertEqual(run.call_count, 1)


class FirstLaunchTests(TmpCase):
    def test_gui_mode_says_first_launch_takes_a_minute(self):
        self.with_tools("wine")
        exe = _write(self.d, "PuTTY.exe", pe_with_icon())
        with mock.patch.object(desktop, "notify") as notify, \
                mock.patch.object(cli.subprocess, "call", return_value=0):
            quiet(cli.main, ["run", "--gui", exe])
        self.assertIn("first time only", notify.call_args[0][1])
        self.assertEqual(detect(exe).platform.value, "windows")


class UpdateTests(TmpCase):
    def test_update_outside_git_explains(self):
        with mock.patch.object(cli.os.path, "isdir", return_value=False):
            code, out = quiet(cli.main, ["update"])
        self.assertEqual(code, 1)
        self.assertIn("pip install --upgrade", out)


if __name__ == "__main__":
    unittest.main()
