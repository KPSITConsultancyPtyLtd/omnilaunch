"""OmniLaunch OS: the parts of OmniLaunch that behave differently when it comes with the system."""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnilaunch import cli  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class UpdateTests(unittest.TestCase):
    def test_update_points_to_the_system_updater_on_omnilaunch_os(self):
        real_exists = os.path.exists
        with mock.patch.object(cli.os.path, "exists",
                               side_effect=lambda p: p.endswith(".omnilaunch-os") or real_exists(p)), \
                mock.patch.object(cli.subprocess, "run") as run:
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(cli.main(["update"]), 0)
        self.assertIn("comes with OmniLaunch OS", out.getvalue())
        self.assertIn("bootc upgrade", out.getvalue())
        run.assert_not_called()                    # no git pull on a system copy


@unittest.skipIf(os.name == "nt", "bash scripts")
class ScriptTests(unittest.TestCase):
    def test_scripts_parse(self):
        for path in ("distro/build/install-layer.sh", "distro/build/check.sh", "distro/live/build.sh",
                     "distro/install-on-fedora.sh"):
            r = subprocess.run(["bash", "-n", os.path.join(ROOT, path)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{path}: {r.stderr}")

    def test_installer_installs_the_image_shipped_on_the_usb(self):
        live = open(os.path.join(ROOT, "distro", "live", "build.sh")).read()
        self.assertIn("ostreecontainer --url=/usr/share/omnilaunch-os/payload:latest --transport=oci", live)
        iso = open(os.path.join(ROOT, "distro", "live", "iso.yaml")).read()
        self.assertIn("root=live:CDLABEL=@LABEL@", iso)

    def test_mimeapps_merge(self):
        """The defaults step keeps other apps' defaults and replaces only OmniLaunch's file types."""
        layer = open(os.path.join(ROOT, "distro", "build", "install-layer.sh")).read()
        code = layer.split("PYTHONPATH=/usr/lib/omnilaunch python3 - <<'EOF'\n", 1)[1].split("\nEOF\n", 1)[0]
        d = tempfile.mkdtemp()
        apps = os.path.join(d, "applications")
        os.makedirs(apps)
        mimeapps = os.path.join(d, "mimeapps.list")
        with open(mimeapps, "w") as f:
            f.write("[Default Applications]\ntext/plain=org.kde.kate.desktop\n"
                    "application/x-ms-dos-executable=wine.desktop\n")
        code = code.replace("/usr/share/applications/omnilaunch.desktop", os.path.join(apps, "omnilaunch.desktop"))
        code = code.replace("/etc/xdg/mimeapps.list", mimeapps)
        with redirect_stdout(io.StringIO()):
            exec(compile(code, "mimeapps", "exec"), {"__name__": "__main__"})
        text = open(mimeapps).read()
        self.assertIn("text/plain=org.kde.kate.desktop", text)
        self.assertIn("application/x-ms-dos-executable=omnilaunch.desktop", text)
        self.assertNotIn("wine.desktop", text)
        self.assertEqual(text.count("[Default Applications]"), 1)


if __name__ == "__main__":
    unittest.main()
