"""Windows apps on Apple Silicon without Rosetta: Box64 + Wine in an ARM64 Linux container."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omnilaunch import cli, provision, runners  # noqa: E402
from omnilaunch.detect import detect  # noqa: E402
from test_detect import TmpCase, _write, fake_pe  # noqa: E402
from test_setup_vm import quiet  # noqa: E402


class Box64Tests(TmpCase):
    def setUp(self):
        super().setUp()
        self.host("macos", "arm64")
        self.with_tools("docker", "wine")
        for name, value in (("box64_image_ready", True), ("xquartz_installed", True)):
            p = mock.patch.object(runners, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def mac(self, major):
        p = mock.patch.object(runners, "mac_major", return_value=major)
        p.start()
        self.addCleanup(p.stop)

    def test_macos_27_keeps_wine_with_rosetta(self):
        self.mac(27)
        with mock.patch.object(runners, "rosetta_installed", return_value=True):
            lp = runners.plan(detect(_write(self.d, "a.exe", fake_pe())))
        self.assertEqual(lp.runner, "wine")

    def test_macos_28_switches_to_box64_automatically(self):
        self.mac(28)
        app = _write(self.d, "PuTTY.exe", fake_pe())
        lp = runners.plan(detect(app), ["-ssh"])
        self.assertEqual(lp.runner, "box64")
        self.assertIn("--platform", lp.argv)
        self.assertEqual(lp.argv[lp.argv.index("--platform") + 1], "linux/arm64")
        self.assertIn(runners.BOX64_IMAGE, lp.argv)
        self.assertIn("box64 /opt/wine/bin/wine", " ".join(lp.argv))
        self.assertIn("wineserver -w", " ".join(lp.argv))      # waits for installers' child processes
        self.assertEqual(lp.argv[-2:], [app, "-ssh"])
        self.assertIn(f"WINEPREFIX={lp.prefix}", lp.argv)
        self.assertIn(os.path.join("prefixes", "box64"), lp.prefix)
        self.assertIn("DISPLAY=host.docker.internal:0", lp.argv)
        self.assertEqual(lp.pre[0][0], runners.ensure_xquartz_network)   # windows can reach XQuartz
        self.assertEqual(lp.pre[1][0], ["/usr/bin/open", "-a", "XQuartz"])

    def test_files_on_this_computer_are_visible_inside(self):
        self.mac(28)
        home = os.path.expanduser("~")
        lp = runners.plan(detect(_write(self.d, "a.exe", fake_pe())))
        self.assertIn(f"{home}:{home}", lp.argv)

    def test_needs_setup_when_image_missing(self):
        self.mac(28)
        with mock.patch.object(runners, "box64_image_ready", return_value=False):
            with self.assertRaises(runners.UnsupportedApp) as cm:
                runners.plan(detect(_write(self.d, "a.exe", fake_pe())))
        self.assertEqual(cm.exception.needs, "box64")

    def test_can_be_chosen_before_macos_28(self):
        self.mac(27)
        with mock.patch.dict(os.environ, {"OMNILAUNCH_WINDOWS_RUNNER": "box64"}):
            self.assertEqual(runners.plan(detect(_write(self.d, "a.exe", fake_pe()))).runner, "box64")
        self.assertEqual(runners.plan(detect(_write(self.d, "b.exe", fake_pe())), runner="box64").runner, "box64")

    def test_installed_program_reuses_its_box64_environment(self):
        self.mac(28)
        root = os.path.join(runners.data_dir(), "prefixes", "box64", "setup-1")
        app_dir = os.path.join(root, "drive_c", "Program Files", "Foo")
        os.makedirs(app_dir)
        exe = _write(app_dir, "foo.exe", fake_pe())
        lp = runners.plan(detect(exe))
        self.assertEqual((lp.runner, lp.prefix), ("box64", root))

    def test_setup_builds_arm64_image_with_wine(self):
        releases = [{"assets": [
            {"name": "wine-11.14-staging-amd64-wow64.tar.xz", "browser_download_url": "u-staging"},
            {"name": "wine-11.14-amd64-wow64.tar.xz", "browser_download_url": "u-1114"},
            {"name": "wine-11.14-amd64.tar.xz", "browser_download_url": "u-plain"}]}]
        box64_releases = [{"tag_name": "v9.9.9-rc", "prerelease": True}, {"tag_name": "v9.9.8"}]

        def fake_fetch(url, dest=None):
            data = box64_releases if "ptitSeb" in url else releases
            return __import__("json").dumps(data).encode()

        with mock.patch.object(provision, "_fetch", fake_fetch), \
                mock.patch.object(provision.subprocess, "run", return_value=mock.Mock(returncode=0)) as run, \
                mock.patch.object(runners, "box64_image_ready", return_value=False):
            quiet(provision.install, "box64", _seen={"container", "xquartz"})
        argv, kw = run.call_args[0][0], run.call_args[1]
        self.assertEqual(argv[1:5], ["build", "--platform", "linux/arm64", "-t"])
        self.assertIn("WINE_URL=u-1114", argv)
        self.assertIn("BOX64_TAG=v9.9.8", argv)                 # newest stable Box64, built from source
        self.assertIn(b"box64", kw["input"])

    def test_doctor_warns_about_macos_28(self):
        self.mac(27)
        with mock.patch.object(runners, "rosetta_installed", return_value=True):
            _, out = quiet(cli.main, ["doctor"])
        self.assertIn("from macOS 28", out)
        self.assertIn("Box64", out)


if __name__ == "__main__":
    unittest.main()
