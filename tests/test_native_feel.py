"""Tests for the 'feels native' features: Android apps, Proton games, installers, double-click and dialogs."""

import os
import struct
import sys
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omnilaunch import android, cli, desktop, provision, runners, shortcuts  # noqa: E402
from omnilaunch.detect import Platform, detect  # noqa: E402
from test_detect import TmpCase, _write, fake_pe  # noqa: E402
from test_setup_vm import quiet  # noqa: E402


# ---------------------------------------------------------------- helpers to build realistic files

def axml(strings, package_idx):
    enc = [struct.pack("<H", len(x)) + x.encode("utf-16-le") + b"\0\0" for x in strings]
    offs, o = [], 0
    for e in enc:
        offs.append(o)
        o += len(e)
    body = b"".join(enc)
    body += b"\0" * ((4 - len(body) % 4) % 4)
    hs, ss = 28, 28 + 4 * len(strings)
    pool = struct.pack("<HHIIIIII", 1, hs, ss + len(body), len(strings), 0, 0, ss, 0) + \
        struct.pack(f"<{len(strings)}I", *offs) + body
    attr = struct.pack("<IIIHBBI", 0xFFFFFFFF, strings.index("package"), package_idx, 8, 0, 3, package_idx)
    ext = struct.pack("<IIHHHHHH", 0xFFFFFFFF, strings.index("manifest"), 20, 20, 1, 0, 0, 0)
    elem_body = struct.pack("<II", 1, 0xFFFFFFFF) + ext + attr
    elem = struct.pack("<HHI", 0x0102, 16, 8 + len(elem_body)) + elem_body
    return struct.pack("<HHI", 3, 8, 8 + len(pool) + len(elem)) + pool + elem


def make_apk(path, package="com.example.hello", abis=("arm64-v8a",)):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("AndroidManifest.xml", axml(["versionCode", "package", "manifest", package], 3))
        z.writestr("classes.dex", b"dex\n035\0")
        for abi in abis:
            z.writestr(f"lib/{abi}/libnative.so", b"\x7fELF")
    return path


def make_lnk(target, args=""):
    """A minimal but valid Windows shortcut (MS-SHLLINK) with a local target path and arguments."""
    flags = 0x02 | 0x80 | (0x20 if args else 0)        # HasLinkInfo | IsUnicode | HasArguments
    header = struct.pack("<I", 0x4C) + bytes(16) + struct.pack("<I", flags) + bytes(0x4C - 24)
    volume = struct.pack("<IIII", 0x11, 3, 0, 0x10) + b"\0"
    base = target.encode("cp1252") + b"\0"
    info_header = 0x1C
    vol_off = info_header
    base_off = vol_off + len(volume)
    suffix_off = base_off + len(base)
    total = suffix_off + 1
    info = struct.pack("<IIIIIII", total, info_header, 1, vol_off, base_off, 0, suffix_off) + volume + base + b"\0"
    data = header + info
    if args:
        data += struct.pack("<H", len(args)) + args.encode("utf-16-le")
    return data


# ---------------------------------------------------------------- Android

class AndroidTests(TmpCase):
    def test_apk_detected_with_package_and_abis(self):
        info = detect(make_apk(os.path.join(self.d, "hello.apk"), abis=("arm64-v8a", "x86_64")))
        self.assertEqual((info.platform, info.kind, info.package), (Platform.ANDROID, "APK", "com.example.hello"))
        self.assertEqual(info.archs, ("arm64", "x86_64"))

    def test_plain_zip_is_not_android(self):
        p = os.path.join(self.d, "x.zip")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("readme.txt", "hi")
        self.assertEqual(detect(p).platform, Platform.ARCHIVE)

    def test_split_bundle_explained(self):
        p = os.path.join(self.d, "game.xapk")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("manifest.json", "{}")
        with self.assertRaises(runners.UnsupportedApp) as cm:
            runners.plan(detect(p))
        self.assertIn("single .apk", cm.exception.hint)

    def test_needs_android_setup(self):
        self.with_tools()
        with self.assertRaises(runners.UnsupportedApp) as cm:
            runners.plan(detect(make_apk(os.path.join(self.d, "a.apk"))))
        self.assertEqual(cm.exception.needs, "android")

    def test_emulator_plan_installs_then_launches(self):
        self.host("windows", "x86_64")
        apk = make_apk(os.path.join(self.d, "a.apk"))
        with mock.patch.object(android, "emulator_ready", return_value=True):
            lp = runners.plan(detect(apk))
        self.assertEqual(lp.runner, "android (emulator)")
        self.assertEqual(lp.pre[0][0], android.start_emulator_and_wait)
        self.assertEqual(lp.pre[1][0][1:], ["install", "-r", apk])
        self.assertEqual(lp.argv[1:5], ["shell", "monkey", "-p", "com.example.hello"])
        self.assertIn("ANDROID_AVD_HOME", lp.env)
        self.assertTrue(any("ARM-only" in n for n in lp.notes))

    def test_waydroid_preferred_on_linux(self):
        self.with_tools("waydroid")
        lp = runners.plan(detect(make_apk(os.path.join(self.d, "a.apk"))))
        self.assertEqual(lp.argv[1:], ["app", "launch", "com.example.hello"])
        self.assertEqual(lp.pre[1][0][1:3], ["app", "install"])
        self.assertNotIn("android", provision.components())

    def test_pick_emulator_and_system_image_from_google_index(self):
        self.host("macos", "arm64")
        repo = b"""<sdk:sdk-repository xmlns:sdk="http://schemas.android.com/sdk/android/repo/repository2/03">
          <channel id="channel-0">stable</channel><channel id="channel-3">canary</channel>
          <remotePackage path="emulator"><channelRef ref="channel-3"/><archives><archive>
            <complete><url>emulator-canary.zip</url></complete><host-os>macosx</host-os><host-arch>aarch64</host-arch>
          </archive></archives></remotePackage>
          <remotePackage path="emulator"><channelRef ref="channel-0"/><archives>
            <archive><complete><url>emulator-darwin_x64-1.zip</url></complete><host-os>macosx</host-os><host-arch>x64</host-arch></archive>
            <archive><complete><url>emulator-darwin_aarch64-1.zip</url></complete><host-os>macosx</host-os><host-arch>aarch64</host-arch></archive>
          </archives></remotePackage></sdk:sdk-repository>"""
        self.assertEqual(android.pick_emulator_url(repo), android.REPO + "emulator-darwin_aarch64-1.zip")
        images = b"""<sys-img:sdk-sys-img xmlns:sys-img="http://schemas.android.com/sdk/android/repo/sys-img2/03">
          <remotePackage path="system-images;android-33;google_apis;arm64-v8a"><channelRef ref="channel-0"/>
            <archives><archive><complete><url>arm64-33.zip</url></complete></archive></archives></remotePackage>
          <remotePackage path="system-images;android-34;google_apis;arm64-v8a"><channelRef ref="channel-0"/>
            <archives><archive><complete><url>arm64-34.zip</url></complete></archive></archives></remotePackage>
          <remotePackage path="system-images;android-34;google_apis;x86_64"><channelRef ref="channel-0"/>
            <archives><archive><complete><url>x86-34.zip</url></complete></archive></archives></remotePackage>
        </sys-img:sdk-sys-img>"""
        api, url = android.pick_system_image(images, "arm64-v8a")
        self.assertEqual((api, url), (34, android.REPO + "sys-img/google_apis/arm64-34.zip"))

    def test_avd_written(self):
        android.write_avd(34, "x86_64")
        with open(os.path.join(android.avd_home(), "omnilaunch.avd", "config.ini")) as f:
            config = f.read()
        self.assertIn("image.sysdir.1=system-images/android-34/google_apis/x86_64/", config)
        self.assertTrue(os.path.exists(os.path.join(android.avd_home(), "omnilaunch.ini")))


# ---------------------------------------------------------------- Proton for games on Linux

class ProtonTests(TmpCase):
    def test_umu_used_for_exe_on_linux(self):
        self.with_tools("umu-run", "wine")
        lp = runners.plan(detect(_write(self.d, "Game Launcher.exe", fake_pe())), ["-windowed"])
        self.assertEqual(lp.runner, "proton (umu)")
        self.assertEqual(lp.argv, ["/usr/bin/umu-run", os.path.join(self.d, "Game Launcher.exe"), "-windowed"])
        self.assertEqual(lp.env["GAMEID"], "0")
        self.assertIn(os.path.join("prefixes", "proton"), lp.env["WINEPREFIX"])

    def test_umu_found_in_omnilaunch_folder(self):
        self.with_tools()
        exe = os.path.join(runners.data_dir(), "umu", "umu-launcher", "umu-run")
        os.makedirs(os.path.dirname(exe))
        _write(os.path.dirname(exe), "umu-run", b"#!/bin/sh\n")
        self.assertEqual(runners.find_umu(), exe)

    def test_setup_proton_uses_distro_package(self):
        self.with_tools("pacman")
        inst, _ = quiet(provision.install, "proton", dry_run=True)
        self.assertIn("sudo pacman -S --needed --noconfirm umu-launcher", inst.steps)


# ---------------------------------------------------------------- installers feel native

class ShortcutTests(TmpCase):
    def prefix(self):
        root = os.path.join(runners.data_dir(), "prefixes", "wine", "setup-1")
        app_dir = os.path.join(root, "drive_c", "Program Files", "Foo App")
        os.makedirs(app_dir)
        _write(app_dir, "foo.exe", fake_pe())
        os.makedirs(os.path.join(root, "drive_c", "ProgramData", "Microsoft", "Windows", "Start Menu", "Programs",
                                 "Foo"))
        return root

    def test_parse_lnk(self):
        link = shortcuts.parse_lnk(make_lnk("C:\\Program Files\\Foo App\\foo.exe", "--fast"))
        self.assertEqual(link["target"], "C:\\Program Files\\Foo App\\foo.exe")
        self.assertEqual(link["args"], "--fast")

    def test_prefix_path_is_case_insensitive(self):
        root = self.prefix()
        self.assertEqual(shortcuts.to_prefix_path(root, "c:\\PROGRAM FILES\\foo app\\FOO.EXE"),
                         os.path.join(root, "drive_c", "Program Files", "Foo App", "foo.exe"))
        self.assertIsNone(shortcuts.to_prefix_path(root, "D:\\x.exe"))

    def test_new_programs_get_app_menu_entries(self):
        root = self.prefix()
        before = shortcuts.find_shortcuts(root)
        menu = os.path.join(root, "drive_c", "ProgramData", "Microsoft", "Windows", "Start Menu", "Programs", "Foo")
        _write(menu, "Foo App.lnk", make_lnk("C:\\Program Files\\Foo App\\foo.exe"))
        _write(menu, "Uninstall Foo.lnk", make_lnk("C:\\Program Files\\Foo App\\foo.exe"))
        added = shortcuts.add_new_programs(root, "wine/setup-1", before)
        self.assertEqual(added, ["Foo App"])
        entry = os.path.join(os.environ["XDG_DATA_HOME"], "applications", "omnilaunch-foo-app.desktop")
        with open(entry) as f:
            text = f.read()
        self.assertIn("Name=Foo App", text)
        self.assertIn("run --gui --env wine/setup-1", text)

    def test_run_with_env_reuses_environment(self):
        root = self.prefix()
        self.with_tools("wine")
        exe = os.path.join(root, "drive_c", "Program Files", "Foo App", "foo.exe")
        lp = runners.plan(detect(exe), env_name="wine/setup-1")
        self.assertEqual(lp.env["WINEPREFIX"], root)
        with self.assertRaises(runners.UnsupportedApp):
            runners.plan(detect(exe), env_name="wine/nope")

    def test_installer_run_adds_programs_afterwards(self):
        root = self.prefix()
        self.with_tools("wine")
        installer = _write(self.d, "setup.exe", fake_pe())
        menu = os.path.join(root, "drive_c", "ProgramData", "Microsoft", "Windows", "Start Menu", "Programs", "Foo")

        def fake_install(argv, **kw):   # the "installer" creates a Start Menu shortcut
            _write(menu, "Foo App.lnk", make_lnk("C:\\Program Files\\Foo App\\foo.exe"))
            return 0

        with mock.patch.object(cli.subprocess, "call", side_effect=fake_install):
            code, out = quiet(cli.main, ["run", "--env", "wine/setup-1", installer])
        self.assertEqual(code, 0)
        self.assertIn("Installed Foo App", out)

    def test_dmg_on_windows_explained(self):
        self.host("windows", "x86_64")
        with self.assertRaises(runners.UnsupportedApp) as cm:
            runners.plan(detect(_write(self.d, "App.dmg", b"koly")))
        self.assertIn("can't be installed on Windows", str(cm.exception))

    def test_dmg_on_mac_installs_then_opens(self):
        self.host("macos", "arm64")
        lp = runners.plan(detect(_write(self.d, "App.dmg", b"koly")))
        self.assertEqual(lp.runner, "install (disk image)")
        step, required = lp.pre[0]
        self.assertTrue(callable(step) and required)
        self.assertIn("mount the disk image", step.__doc__)

    def test_pkg_on_mac_opens_installer(self):
        self.host("macos", "arm64")
        lp = runners.plan(detect(_write(self.d, "App.pkg", b"xar!")))
        self.assertEqual(lp.argv[:2], ["/usr/bin/open", "-W"])


# ---------------------------------------------------------------- dialogs instead of a terminal

class GuiModeTests(TmpCase):
    def test_gui_offer_asks_in_dialog_and_installs(self):
        self.with_tools("apt-get")
        with mock.patch.object(desktop, "ask", return_value=True) as ask, \
                mock.patch.object(desktop, "notify"), \
                mock.patch.object(provision, "install") as install, \
                mock.patch.object(runners, "find_wine", return_value="/usr/bin/wine"):
            self.assertTrue(provision.offer("wine", assume_yes=False, gui=True))
        self.assertIn("about 400 MB", ask.call_args[0][1])
        self.assertTrue(install.call_args[1]["gui"])

    def test_gui_offer_declined(self):
        with mock.patch.object(desktop, "ask", return_value=False), \
                mock.patch.object(provision, "install") as install:
            self.assertFalse(provision.offer("wine", assume_yes=False, gui=True))
        install.assert_not_called()

    def test_gui_installer_uses_password_prompt_not_sudo(self):
        self.host("macos", "arm64")
        inst = provision.Installer(gui=True, log=lambda m: None)
        with mock.patch.object(provision.subprocess, "run") as run:
            inst.run(["sudo", "softwareupdate", "--install-rosetta"])
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "/usr/bin/osascript")
        self.assertIn("with administrator privileges", " ".join(argv))

    def test_gui_errors_shown_as_alert(self):
        with mock.patch.object(desktop, "alert") as alert:
            code, _ = quiet(cli.main, ["run", "--gui", os.path.join(self.d, "missing.exe")])
        self.assertEqual(code, 2)
        alert.assert_called_once()


if __name__ == "__main__":
    unittest.main()
