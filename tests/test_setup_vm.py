import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omnilaunch import cli, provision, runners, vms  # noqa: E402
from omnilaunch.detect import detect  # noqa: E402
from test_detect import TmpCase, _write, fake_elf, fake_pe  # noqa: E402


def quiet(fn, *a, **kw):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        result = fn(*a, **kw)
    return result, out.getvalue() + err.getvalue()


class ProvisionTests(TmpCase):
    def test_linux_wine_uses_package_manager(self):
        self.with_tools("apt-get")
        inst, _ = quiet(provision.install, "wine", dry_run=True)
        self.assertIn("sudo apt-get install -y wine", inst.steps)

    def test_linux_without_package_manager_is_manual(self):
        self.with_tools()
        with self.assertRaises(provision.ManualStep):
            quiet(provision.install, "wine", dry_run=True)

    def test_unknown_component(self):
        with self.assertRaises(provision.SetupError):
            provision.install("nope", dry_run=True)

    def test_components_per_host(self):
        self.assertEqual(set(provision.components()), {"wine", "container", "proton", "darling", "android"})
        self.host("linux", "arm64")
        self.assertNotIn("proton", provision.components())      # umu-launcher is x86_64-only
        self.host("windows", "x86_64")
        self.assertEqual(set(provision.components()), {"android", "wsl"})
        self.host("macos", "arm64")
        self.assertEqual(set(provision.components()),
                         {"wine", "container", "rosetta", "xquartz", "linux-gui", "android"})
        self.host("macos", "x86_64")
        self.assertNotIn("rosetta", provision.components())

    def test_mac_wine_installs_rosetta_first_and_only_once(self):
        self.host("macos", "arm64")
        self.with_tools("docker")
        with mock.patch.object(runners, "rosetta_installed", return_value=False), \
                mock.patch.object(runners, "MAC_WINE_GLOBS", []):
            _, out = quiet(cli.main, ["setup", "all", "--dry-run"])
        self.assertEqual(out.count("install-rosetta"), 1)
        self.assertLess(out.index("Rosetta"), out.index("Wine"))
        self.assertEqual(out.count("Setting up XQuartz"), 1)

    def test_mac_wine_real_install_steps(self):
        """Non-dry-run: picks the stable build from the release list, unpacks it, clears quarantine."""
        self.host("macos", "arm64")
        apps = os.path.join(self.d, "Applications")
        releases = [{"assets": [{"name": "wine-devel-11.16-osx64.tar.xz", "browser_download_url": "u-devel"}]},
                    {"assets": [{"name": "wine-stable-11.0-osx64.tar.xz", "browser_download_url": "u-stable"}]}]
        ran, fetched = [], []

        def fake_fetch(url, dest=None):
            fetched.append(url)
            return json.dumps(releases).encode() if dest is None else b""

        def fake_run(argv, **kw):
            ran.append(argv)
            if argv[0] == "tar":
                os.makedirs(os.path.join(apps, "Wine Stable.app"), exist_ok=True)
            return mock.Mock(returncode=0)

        with mock.patch.object(provision, "_fetch", fake_fetch), \
                mock.patch.object(provision.subprocess, "run", fake_run), \
                mock.patch.object(provision.os.path, "expanduser",
                                  lambda p: p.replace("~", self.d)), \
                mock.patch.object(runners, "rosetta_installed", return_value=True), \
                mock.patch.object(runners, "find_wine", return_value=None):
            quiet(provision.install, "wine")
        self.assertEqual(fetched[1], "u-stable")
        self.assertEqual(ran[0][:2], ["tar", "-xJf"])
        self.assertEqual(ran[1][:3], ["xattr", "-dr", "com.apple.quarantine"])

    def test_pick_release_asset_falls_back(self):
        rel = [{"assets": [{"name": "wine-devel-9.osx64.tar.xz", "browser_download_url": "d"}]}]
        self.assertEqual(provision.pick_release_asset(rel, [r"^wine-stable", r"^wine-devel"])[1], "d")
        with self.assertRaises(provision.SetupError):
            provision.pick_release_asset(rel, [r"^XQuartz"])

    def test_linux_gui_image_build_sends_dockerfile(self):
        self.host("macos", "arm64")
        self.with_tools("docker")
        with mock.patch.object(provision.subprocess, "run", return_value=mock.Mock(returncode=0)) as run, \
                mock.patch.object(runners, "linux_gui_image_ready", return_value=False):
            quiet(provision.install, "linux-gui", _seen={"container", "xquartz"})
        argv, kw = run.call_args[0][0], run.call_args[1]
        self.assertEqual(argv[1:], ["build", "-t", provision.LINUX_GUI_IMAGE, "-"])
        self.assertIn(b"libgtk-3-0", kw["input"])

    def test_run_offers_install_then_retries(self):
        self.host("linux", "arm64")   # Wine is the default here (Proton is x86-only)
        self.with_tools()   # no wine yet
        app = _write(self.d, "a.exe", fake_pe())
        state = {"installed": False}

        def fake_offer(name, assume_yes, gui=False):
            self.assertEqual((name, assume_yes, gui), ("wine", True, False))
            state["installed"] = True
            return True

        def fake_find_wine():
            return "/usr/bin/wine" if state["installed"] else None

        with mock.patch.object(provision, "offer", fake_offer), \
                mock.patch.object(runners, "find_wine", fake_find_wine):
            code, out = quiet(cli.main, ["run", "--yes", "--dry-run", app])
        # dry-run never installs, so it must explain instead
        self.assertEqual(code, 3)
        self.assertIn("omnilaunch setup wine", out)
        with mock.patch.object(provision, "offer", fake_offer), \
                mock.patch.object(runners, "find_wine", fake_find_wine), \
                mock.patch.object(cli.subprocess, "call", return_value=0) as call:
            code, _ = quiet(cli.main, ["run", "-y", app])
        self.assertEqual(code, 0)
        self.assertEqual(call.call_args[0][0][:2], ["/usr/bin/wine", app])

    def test_offer_without_terminal_declines(self):
        with mock.patch.object(provision.sys.stdin, "isatty", return_value=False):
            self.assertFalse(provision.offer("wine", assume_yes=False))


class GuiTests(TmpCase):
    def gui_app(self):
        return _write(self.d, "viewer", fake_elf() + b"\0libX11.so.6\0")

    def test_gui_detected_from_linked_libraries(self):
        self.assertTrue(detect(self.gui_app()).gui)
        self.assertFalse(detect(_write(self.d, "cli", fake_elf())).gui)
        self.assertTrue(detect(_write(self.d, "a.AppImage", fake_elf(appimage=True))).gui)

    def test_mac_gui_needs_xquartz(self):
        self.host("macos", "arm64")
        self.with_tools("docker")
        with mock.patch.object(runners, "xquartz_installed", return_value=False), \
                mock.patch.object(runners, "linux_gui_image_ready", return_value=False):
            with self.assertRaises(runners.UnsupportedApp) as cm:
                runners.plan(detect(self.gui_app()))
        self.assertEqual(cm.exception.needs, "linux-gui")

    def test_mac_gui_forwards_display_to_xquartz(self):
        self.host("macos", "arm64")
        self.with_tools("podman")
        with mock.patch.object(runners, "xquartz_installed", return_value=True), \
                mock.patch.object(runners, "linux_gui_image_ready", return_value=True):
            lp = runners.plan(detect(self.gui_app()))
        self.assertIn("DISPLAY=host.containers.internal:0", lp.argv)
        self.assertIn(runners.LINUX_GUI_IMAGE, lp.argv)
        self.assertEqual(lp.pre[0], (["/usr/bin/open", "-a", "XQuartz"], False))

    def test_linux_container_gui_mounts_x11_socket(self):
        self.with_tools("docker", "xhost")
        with mock.patch.object(runners, "linux_gui_image_ready", return_value=False):
            lp = runners.plan(detect(self.gui_app()), runner="container")
        self.assertIn("/tmp/.X11-unix:/tmp/.X11-unix", lp.argv)
        self.assertEqual(lp.pre, [(["xhost", "+local:"], False)])


class VMTests(TmpCase):
    def setUp(self):
        super().setUp()
        self.host("macos", "arm64")
        p = mock.patch.object(runners, "MAC_WINE_GLOBS", [])
        p.start()
        self.addCleanup(p.stop)

    def test_configure_validates(self):
        with self.assertRaises(ValueError):
            vms.configure("windows", "virtualbox", "Win")          # needs user + password file
        with self.assertRaises(ValueError):
            vms.configure("windows", "vmware", "Win")
        with self.assertRaises(ValueError):
            vms.configure("linux", "parallels", "Ubuntu")

    def test_parallels_path_translation(self):
        home = os.path.join(self.d, "home")
        os.makedirs(os.path.join(home, "Downloads"))
        p = _write(os.path.join(home, "Downloads"), "My App.exe", b"MZ")
        self.assertEqual(vms.parallels_guest_path(p, home), "\\\\Mac\\Home\\Downloads\\My App.exe")
        self.assertIsNone(vms.parallels_guest_path(_write(self.d, "x.exe", b"MZ"), home))

    def test_windows_app_falls_back_to_vm_when_no_wine(self):
        self.with_tools("prlctl")
        vms.configure("windows", "parallels", "Windows 11")
        home = os.path.join(self.d, "userhome")
        os.makedirs(home)
        app = _write(home, "a.exe", fake_pe())
        with mock.patch.object(vms.os.path, "expanduser", lambda p: home if p == "~" else p):
            lp = runners.plan(detect(app), ["/S"])
        self.assertEqual(lp.runner, "vm (parallels)")
        self.assertEqual(lp.argv[1:4], ["exec", "Windows 11", "--current-user"])
        self.assertEqual(lp.argv[-1], "/S")
        self.assertEqual(lp.pre[0][0][1:], ["start", "Windows 11"])

    def test_wine_preferred_over_vm_when_installed(self):
        self.with_tools("prlctl", "wine")
        vms.configure("windows", "parallels", "Windows 11")
        with mock.patch.object(runners, "rosetta_installed", return_value=True):
            lp = runners.plan(detect(_write(self.d, "a.exe", fake_pe())))
        self.assertEqual(lp.runner, "wine")

    def test_virtualbox_copies_then_starts(self):
        self.host("linux", "x86_64")
        self.with_tools("VBoxManage")
        pw = _write(self.d, "pw.txt", b"secret")
        vms.configure("windows", "virtualbox", "Win10", user="me", password_file=pw)
        folder = os.path.join(self.d, "Tool")
        os.makedirs(folder)
        app = _write(folder, "tool.exe", fake_pe())
        _write(folder, "data.dll", b"MZ")
        lp = runners.plan(detect(app), runner="vm")
        self.assertIn("C:\\OmniLaunch\\tool\\tool.exe", lp.argv)
        self.assertEqual([p[1] for p in lp.pre], [False, True, True])
        copy = lp.pre[2][0]
        self.assertIn("copyto", copy)
        self.assertIn(os.path.join(folder, "data.dll"), copy)   # small folder: copied whole

    def test_cli_vm_commands(self):
        code, out = quiet(cli.main, ["vm", "set", "windows", "--tool", "parallels", "--name", "Win"])
        self.assertEqual(code, 0)
        self.assertIn("'Win' via parallels", quiet(cli.main, ["vm", "show"])[1])
        quiet(cli.main, ["vm", "remove", "windows"])
        self.assertIsNone(vms.get("windows"))

    def test_vm_refused_on_windows_host(self):
        self.host("windows", "x86_64")
        with self.assertRaises(runners.UnsupportedApp):
            runners.plan(detect(_write(self.d, "a.exe", fake_pe())), runner="vm")


if __name__ == "__main__":
    unittest.main()
