"""Security regressions: crafted files must not hang or exhaust OmniLaunch, and text from them must not
change what a launcher runs."""
import os
import struct
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnilaunch import detect, icons, runners, shortcuts  # noqa: E402
from test_detect import TmpCase  # noqa: E402


class CraftedFileTests(unittest.TestCase):
    def test_unterminated_shortcut_text_is_an_error_not_a_hang(self):
        data = "Program".encode("utf-16-le")                # no 00 00 terminator
        start = time.time()
        with self.assertRaises(ValueError):
            shortcuts._read_wstr(data, 0)
        self.assertLess(time.time() - start, 1)

    def test_icon_resource_loop_is_an_error_not_a_hang(self):
        directory = bytearray(16 + 8)
        struct.pack_into("<HH", directory, 12, 0, 1)            # one entry...
        struct.pack_into("<II", directory, 16, 3, 0x80000000)   # ...that points back at itself
        with self.assertRaises(ValueError):
            icons._leaf(bytes(directory), 0, 0, [])

    def test_zip_bomb_manifest_is_not_expanded(self):
        d = tempfile.mkdtemp()
        apk = os.path.join(d, "bomb.apk")
        with zipfile.ZipFile(apk, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("AndroidManifest.xml", b"\0" * (detect.MAX_MANIFEST_BYTES + 1))
        info = detect.detect(apk)
        self.assertEqual(info.platform, detect.Platform.ANDROID)
        self.assertFalse(getattr(info, "package", None))

    def test_string_pool_bigger_than_the_file_is_refused(self):
        chunk = bytearray(28)
        struct.pack_into("<HHI", chunk, 0, 0x0001, 28, 28)
        struct.pack_into("<IIII", chunk, 8, 0xFFFFFFF0, 0, 0, 28)
        with self.assertRaises(ValueError):
            detect._axml_strings(bytes(chunk), 0)


class LauncherTextTests(TmpCase):
    def test_line_breaks_in_shortcut_text_cannot_add_launcher_lines(self):
        exe = os.path.join(self.d, "app.exe")
        open(exe, "wb").close()
        with mock.patch.object(runners, "HOST_OS", "linux"):
            path = shortcuts.create_launcher("Good App\nExec=/bin/evil", "wine/app-1", exe,
                                             args="/x\nExec=/bin/also-evil")
        with open(path) as f:
            lines = f.read().splitlines()
        execs = [l for l in lines if l.startswith("Exec=")]
        self.assertEqual(len(execs), 1)                                # nothing added on new lines
        self.assertFalse(any(l.startswith("Exec=/bin/") for l in lines))
        self.assertIn("Name=Good AppExec=/bin/evil", lines)          # harmless text, same line


if __name__ == "__main__":
    unittest.main()


class DownloadedAppTests(TmpCase):
    def setUp(self):
        super().setUp()
        from omnilaunch import trust
        self.trust = trust
        self.exe = os.path.join(self.d, "setup.exe")
        with open(self.exe, "wb") as f:
            f.write(b"MZ")

    def test_not_downloaded_opens_without_asking(self):
        with mock.patch.object(self.trust, "downloaded_from", return_value=None), \
                mock.patch("omnilaunch.desktop.ask") as ask:
            self.assertTrue(self.trust.confirm_open(self.exe, gui=True))
        ask.assert_not_called()

    def test_downloaded_app_asks_once_and_remembers_yes(self):
        with mock.patch.object(self.trust, "downloaded_from", return_value="https://example.com/setup.exe"), \
                mock.patch("omnilaunch.desktop._no_dialogs", return_value=False), \
                mock.patch("omnilaunch.desktop.ask", return_value=True) as ask:
            self.assertTrue(self.trust.confirm_open(self.exe, gui=True))
            self.assertTrue(self.trust.confirm_open(self.exe, gui=True))
        self.assertEqual(ask.call_count, 1)
        self.assertIn("example.com", ask.call_args.args[1])

    def test_downloaded_app_not_opened_when_user_says_no(self):
        with mock.patch.object(self.trust, "downloaded_from", return_value="the internet"), \
                mock.patch("omnilaunch.desktop._no_dialogs", return_value=False), \
                mock.patch("omnilaunch.desktop.ask", return_value=False):
            self.assertFalse(self.trust.confirm_open(self.exe, gui=True))
            self.assertFalse(self.trust.is_trusted(self.exe))

    def test_changed_file_is_asked_about_again(self):
        with mock.patch.object(self.trust, "downloaded_from", return_value="the internet"), \
                mock.patch("omnilaunch.desktop._no_dialogs", return_value=False), \
                mock.patch("omnilaunch.desktop.ask", return_value=True) as ask:
            self.trust.confirm_open(self.exe, gui=True)
            with open(self.exe, "ab") as f:
                f.write(b"changed")
            self.trust.confirm_open(self.exe, gui=True)
        self.assertEqual(ask.call_count, 2)

    def test_terminal_runs_are_not_interrupted(self):
        with mock.patch.object(self.trust, "downloaded_from", return_value="the internet"), \
                mock.patch("omnilaunch.desktop.ask") as ask:
            self.assertTrue(self.trust.confirm_open(self.exe, gui=False))
        ask.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux download marker")
    def test_linux_browser_download_marker_is_read(self):
        try:
            os.setxattr(self.exe, "user.xdg.origin.url", b"https://example.com/setup.exe")
        except OSError:
            self.skipTest("this filesystem has no user attributes")
        with mock.patch.object(runners, "HOST_OS", "linux"):
            self.assertEqual(self.trust.downloaded_from(self.exe), "https://example.com/setup.exe")


class AndroidChecksumTests(unittest.TestCase):
    INDEX = b"""<sdk:sdk-repository xmlns:sdk="http://schemas.android.com/sdk/android/repo/repository2/03">
      <channel id="channel-0">stable</channel>
      <remotePackage path="platform-tools"><channelRef ref="channel-0"/><archives>
        <archive><complete><size>1</size><checksum type="sha1">aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa</checksum>
          <url>platform-tools_r36-darwin.zip</url></complete><host-os>macosx</host-os></archive>
        <archive><complete><size>1</size><checksum type="sha1">bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb</checksum>
          <url>platform-tools_r36-linux.zip</url></complete><host-os>linux</host-os></archive>
      </archives></remotePackage></sdk:sdk-repository>"""

    def test_platform_tools_come_from_the_index_with_their_checksum(self):
        from omnilaunch import android
        with mock.patch.object(runners, "HOST_OS", "linux"):
            url = android.pick_platform_tools_url(self.INDEX)
        self.assertTrue(url.endswith("platform-tools_r36-linux.zip"))
        self.assertEqual(android.archive_sha1(self.INDEX, url), "b" * 40)

    def test_unlisted_archive_has_no_checksum(self):
        from omnilaunch import android
        self.assertIsNone(android.archive_sha1(self.INDEX, "https://dl.google.com/other.zip"))


class PrivacyTests(TmpCase):
    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_data_folder_is_made_private(self):
        folder = os.path.join(self.d, "omni")
        os.makedirs(folder, mode=0o755)
        os.chmod(folder, 0o755)
        with mock.patch.dict(os.environ, {"OMNILAUNCH_HOME": folder}), mock.patch.object(runners, "HOST_OS", "linux"):
            runners.ensure_private_data_dir()
        self.assertEqual(os.stat(folder).st_mode & 0o777, 0o700)

    def test_token_is_not_on_the_command_line(self):
        from omnilaunch import provision
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "secret-token"}), \
                mock.patch.object(provision.shutil, "which", return_value="/usr/bin/curl"), \
                mock.patch.object(provision.subprocess, "run", return_value=mock.Mock(stdout=b"[]")) as run:
            provision._fetch_once("https://api.github.com/repos/x/y/releases")
        argv, kwargs = run.call_args.args[0], run.call_args.kwargs
        self.assertFalse(any("secret-token" in a for a in argv))
        self.assertIn(b"secret-token", kwargs["input"])
        self.assertIn("=https", argv)                                    # never falls back to plain http
