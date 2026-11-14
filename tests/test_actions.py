"""The actions layer: doing a Windows program's job (extract, compress) without opening its window."""

import os
import plistlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omnilaunch import actions, cli, contextmenu, desktop, envs, runners  # noqa: E402
from test_detect import TmpCase, _write, fake_pe  # noqa: E402
from test_native_feel import make_lnk  # noqa: E402
from test_setup_vm import quiet  # noqa: E402


class Base(TmpCase):
    def setUp(self):
        super().setUp()
        self.with_tools("wine")

    def install_7zip(self, env="wine/7z2606-x64-1", folder="7-Zip", used=1):
        root = os.path.join(runners.data_dir(), "prefixes", *env.split("/"))
        d = os.path.join(root, "drive_c", "Program Files", folder)
        os.makedirs(d)
        exe = _write(d, "7z.exe", fake_pe(subsystem=3))
        envs.record_launch(root, "/x/setup.exe")
        os.utime(os.path.join(root, "omnilaunch.json"), (used, used))
        with open(os.path.join(root, "omnilaunch.json"), "w") as f:
            f.write(f'{{"app": "/x/setup.exe", "last_used": {used}}}')
        return root, exe

    def run_action(self, action, files, creates):
        """Run an action with Wine faked: the 'tool' just creates the output it was asked for."""
        calls = []

        def fake_run(argv, **kw):
            calls.append((argv, kw))
            if creates:
                path = creates(argv)
                os.makedirs(path) if not os.path.splitext(path)[1] else open(path, "w").close()
            return mock.Mock(returncode=0, stdout="Everything is Ok", stderr="")
        with mock.patch.object(actions.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(runners, "warm_wineserver") as warm, \
                mock.patch.object(desktop, "notify") as notify, mock.patch.object(desktop, "alert") as alert:
            code = actions.run(action, files, gui=True)
        return code, calls, notify, alert, warm

    @staticmethod
    def host_path(z):
        return z[2:].replace("\\", "/")



class ActionTests(Base):
    def test_finds_7zip_in_most_recently_used_environment(self):
        self.install_7zip("wine/old-1", used=10)
        _, newer = self.install_7zip("wine/new-2", used=20)
        found = actions.find_program(actions.get("extract"))
        self.assertEqual(found, ("wine/new-2", newer))

    def test_finds_7zip_with_different_case(self):
        _, exe = self.install_7zip(folder="7-zip")
        self.assertEqual(actions.find_program(actions.get("extract"))[1], exe)

    def test_extract_runs_7z_headless_into_a_folder_next_to_the_archive(self):
        root, exe = self.install_7zip()
        archive = _write(self.d, "photos.7z", b"7z\xbc\xaf\x27\x1c")
        os.makedirs(os.path.join(self.d, "photos"))       # a folder with that name exists already
        code, calls, notify, alert, warm = self.run_action(
            "extract", [archive], lambda argv: self.host_path(next(a for a in argv if a.startswith("-o"))[2:]))
        self.assertEqual(code, 0)
        argv, kw = calls[0]
        self.assertEqual(argv[:4], ["/usr/bin/wine", exe, "x", "-y"])
        self.assertEqual(argv[4], "-oZ:" + os.path.join(self.d, "photos 2").replace("/", "\\"))
        self.assertEqual(argv[5], "Z:" + archive.replace("/", "\\"))
        self.assertEqual(kw["env"]["WINEPREFIX"], root)
        self.assertIn("Extracted photos.7z to photos 2", notify.call_args[0][1])
        alert.assert_not_called()
        warm.assert_called_once()                         # next action starts faster

    def test_compress_one_file_and_several(self):
        self.install_7zip()
        a = _write(self.d, "report.docx", b"x")
        b = _write(self.d, "notes.txt", b"y")
        code, calls, notify, *_ = self.run_action("compress-7z", [a], lambda argv: self.host_path(argv[5]))
        self.assertEqual((code, calls[0][0][2:5]), (0, ["a", "-t7z", "-y"]))
        self.assertTrue(calls[0][0][5].endswith("report.7z"))
        code, calls, notify, *_ = self.run_action("compress-zip", [a, b], lambda argv: self.host_path(argv[5]))
        self.assertTrue(calls[0][0][5].endswith("Archive.zip"))
        self.assertEqual(len(calls[0][0]), 8)              # both files passed

    def test_compress_folder_named_after_it(self):
        self.install_7zip()
        folder = os.path.join(self.d, "Holiday")
        os.makedirs(folder)
        _, calls, *_ = self.run_action("compress-7z", [folder], lambda argv: self.host_path(argv[5]))
        self.assertTrue(calls[0][0][5].endswith("Holiday.7z"))

    def test_not_an_archive(self):
        self.install_7zip()
        code, calls, notify, alert, _ = self.run_action("extract", [_write(self.d, "a.txt", b"x")], None)
        self.assertEqual((code, calls), (2, []))
        self.assertIn("isn't something 7-Zip can extract", alert.call_args[0][1])

    def test_7zip_not_installed(self):
        code, _, _, alert, _ = self.run_action("extract", [_write(self.d, "a.7z", b"7z")], None)
        self.assertEqual(code, 3)
        self.assertIn("isn't installed", alert.call_args[0][1])

    def test_tool_failure_reported(self):
        self.install_7zip()
        archive = _write(self.d, "broken.7z", b"x")
        with mock.patch.object(actions.subprocess, "run",
                               return_value=mock.Mock(returncode=2, stdout="ERROR: broken.7z : Can not open the file "
                                                                           "as archive", stderr="")), \
                mock.patch.object(desktop, "alert") as alert:
            self.assertEqual(actions.run("extract", [archive], gui=True), 5)
        self.assertIn("Can not open the file as archive", alert.call_args[0][1])

    def test_do_list(self):
        self.install_7zip()
        _, out = quiet(cli.main, ["do", "--list"])
        self.assertIn("ready (wine/7z2606-x64-1)", out)


class MenuTests(Base):
    def test_mac_quick_action_workflow(self):
        info, doc = contextmenu.mac_workflow(actions.get("extract"))
        svc = info["NSServices"][0]
        self.assertEqual(svc["NSMenuItem"]["default"], "Extract Here with 7-Zip")
        self.assertEqual(svc["NSSendFileTypes"], ["public.item"])
        params = doc["actions"][0]["action"]["ActionParameters"]
        self.assertEqual(params["inputMethod"], 1)                     # files arrive as arguments
        self.assertIn('do --gui extract "$@"', params["COMMAND_STRING"])
        self.assertEqual(doc["workflowMetaData"]["workflowTypeIdentifier"], "com.apple.Automator.servicesMenu")
        plistlib.dumps(doc)                                            # serialises cleanly

    def test_mac_install_writes_workflows_and_double_click_app(self):
        self.host("macos", "arm64")
        services = os.path.join(self.d, "Services")
        apps = os.path.join(self.d, "Apps")

        def fake_run(argv, **kw):
            if argv[0].endswith("osacompile"):
                out = argv[argv.index("-o") + 1]
                os.makedirs(os.path.join(out, "Contents"))
                with open(os.path.join(out, "Contents", "Info.plist"), "wb") as f:
                    plistlib.dump({}, f)
            return mock.Mock(returncode=0)
        self.install_7zip()
        with mock.patch.object(contextmenu, "SERVICES_DIR", services), \
                mock.patch("omnilaunch.shortcuts.launcher_dir", return_value=apps), \
                mock.patch.object(contextmenu.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(desktop, "mac_default_handlers",
                                  return_value={"7z": contextmenu.EXTRACT_BUNDLE_ID, "rar": "other"}):
            done = contextmenu.install()
        self.assertEqual(sorted(os.listdir(services)),
                         ["Compress to .7z with 7-Zip.workflow", "Compress to .zip with 7-Zip.workflow",
                          "Extract Here with 7-Zip.workflow"])
        with open(os.path.join(apps, "Extract with 7-Zip.app", "Contents", "Info.plist"), "rb") as f:
            plist = plistlib.load(f)
        exts = plist["CFBundleDocumentTypes"][0]["CFBundleTypeExtensions"]
        self.assertIn("7z", exts)
        self.assertNotIn("zip", exts)                                   # macOS handles .zip itself
        self.assertIn("Double-click extracts: .7z", done)

    def test_linux_menus(self):
        self.install_7zip()
        done = contextmenu.install()
        scripts = os.path.join(os.environ["XDG_DATA_HOME"], "nautilus", "scripts")
        self.assertEqual(len(os.listdir(scripts)), 3)
        script = os.path.join(scripts, "Extract Here with 7-Zip")
        self.assertTrue(os.access(script, os.X_OK) or os.name == "nt")
        self.assertIn('do --gui extract "$@"', open(script).read())
        kde = open(os.path.join(os.environ["XDG_DATA_HOME"], "kio", "servicemenus", "omnilaunch-actions.desktop")).read()
        self.assertIn("Actions=extract;compress-7z;compress-zip;", kde)
        extract = open(os.path.join(os.environ["XDG_DATA_HOME"], "applications", "omnilaunch-extract.desktop")).read()
        self.assertIn("application/x-7z-compressed", extract)
        self.assertTrue(any(d.startswith("Double-click extracts") for d in done))
        self.assertEqual(len(contextmenu.remove()), 5)

    def test_apps_scan_sets_up_menus_when_7zip_is_installed(self):
        root, _ = self.install_7zip()
        menu = os.path.join(root, "drive_c", "ProgramData", "Microsoft", "Windows", "Start Menu", "Programs", "7-Zip")
        os.makedirs(menu)
        _write(menu, "7-Zip File Manager.lnk", make_lnk("C:\\Program Files\\7-Zip\\7z.exe"))
        _, out = quiet(cli.main, ["apps", "scan"])
        self.assertIn("Right-click actions:", out)


class WarmServerTests(TmpCase):
    def test_warm_only_once_environment_exists(self):
        wine = _write(self.d, "wine", b"#!/bin/sh\n")
        _write(self.d, "wineserver", b"#!/bin/sh\n")
        prefix = os.path.join(self.d, "p")
        with mock.patch.object(runners.subprocess, "Popen") as popen:
            self.assertFalse(runners.warm_wineserver(wine, prefix))        # not set up yet
            os.makedirs(os.path.join(prefix, "drive_c"))
            self.assertTrue(runners.warm_wineserver(wine, prefix))
        argv, kw = popen.call_args[0][0], popen.call_args[1]
        self.assertEqual(argv[1], "-p900")
        self.assertEqual(kw["env"]["WINEPREFIX"], prefix)


if __name__ == "__main__":
    unittest.main()
