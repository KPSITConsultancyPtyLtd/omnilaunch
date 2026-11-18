"""OmniLaunch OS: the parts of OmniLaunch that behave differently when it comes with the system."""
import io
import json
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


def _read(path):
    with open(path) as f:
        return f.read()


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
        self.assertIn("omnilaunch-os update", out.getvalue())
        run.assert_not_called()                    # no git pull on a system copy


@unittest.skipIf(os.name == "nt", "bash scripts")
class ScriptTests(unittest.TestCase):
    def test_scripts_parse(self):
        for path in ("distro/build/install-layer.sh", "distro/build/check.sh", "distro/live/build.sh",
                     "distro/install-on-fedora.sh", "distro/build/kernel.sh", "distro/build/nvidia-kmod.sh",
                     "distro/build/drivers.sh", "distro/build/omnilaunch-os", "distro/make-secure-boot-key.sh",
                     "distro/build/omnilaunch-os-apps", "distro/build/omnilaunch-os-welcome"):
            r = subprocess.run(["bash", "-n", os.path.join(ROOT, path)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{path}: {r.stderr}")

    def test_containerfiles_are_well_formed(self):
        for name, froms_expected in (
                ("Containerfile", ["FROM quay.io/fedora-ostree-desktops/kinoite:${FEDORA} AS base",
                                   "FROM base AS nvidia", "FROM base"]),
                ("Containerfile.live", ["FROM localhost/omnilaunch-os:latest"])):
            with open(os.path.join(ROOT, "distro", name)) as f:
                lines = f.read().splitlines()
            self.assertEqual([l for l in lines if l.startswith("FROM ")], froms_expected, name)
            for l in lines:
                if l.startswith("#"):
                    self.assertFalse(l.rstrip().endswith("\\"), f"{name}: comment continues onto the next line: {l}")
                self.assertEqual(l.count('"') % 2, 0, f"{name}: unbalanced quote: {l}")

    def test_nvidia_driver_comes_from_the_build_stage_for_the_same_kernel(self):
        cf = _read(os.path.join(ROOT, "distro", "Containerfile"))
        self.assertIn("COPY --from=nvidia /out /tmp/nvidia", cf)
        kmod = _read(os.path.join(ROOT, "distro", "build", "nvidia-kmod.sh"))
        self.assertIn("%_with_kmod_nvidia_open 1", kmod)     # RTX 50 (Blackwell) needs the open driver
        self.assertIn('kernel-devel-${KVER}', kmod)

    def test_installer_downloads_the_published_image(self):
        live = _read(os.path.join(ROOT, "distro", "live", "build.sh"))
        self.assertIn("ostreecontainer --url=${IMAGE_REF} --no-signature-verification", live)
        wf = _read(os.path.join(ROOT, ".github", "workflows", "distro.yml"))
        self.assertIn('--build-arg IMAGE_REF="$IMAGE:latest"', wf)
        self.assertIn("docker buildx imagetools create", wf)
        iso = _read(os.path.join(ROOT, "distro", "live", "iso.yaml"))
        self.assertIn("root=live:CDLABEL=@LABEL@", iso)

    def test_default_apps_merge(self):
        """Keeps other defaults, replaces only listed types, prefers the installed alternative,
        and writes both the generic and KDE files."""
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "usr/share/applications"))
        open(os.path.join(root, "usr/share/applications/firefox.desktop"), "w").close()
        os.makedirs(os.path.join(root, "etc/xdg"))
        with open(os.path.join(root, "etc/xdg/mimeapps.list"), "w") as f:
            f.write("[Default Applications]\ntext/x-python=org.kde.kate.desktop\napplication/pdf=evince.desktop\n")
        env = dict(os.environ, OMNILAUNCH_DEFAULTS_ROOT=root)
        script = os.path.join(ROOT, "distro", "build", "set-defaults.py")
        r = subprocess.run([sys.executable, script, os.path.join(ROOT, "distro", "build", "defaults.list")],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        generic = _read(os.path.join(root, "etc/xdg/mimeapps.list"))
        kde = _read(os.path.join(root, "etc/xdg/kde-mimeapps.list"))
        self.assertIn("text/x-python=org.kde.kate.desktop", generic)
        self.assertIn("application/pdf=org.kde.okular.desktop", generic)
        self.assertNotIn("evince", generic)
        self.assertIn("x-scheme-handler/https=firefox.desktop", kde)          # the one that exists
        self.assertEqual(generic.count("[Default Applications]"), 1)

    def test_every_default_app_is_installed(self):
        """Each default is an app OmniLaunch OS ships: in the Flathub list, or part of the base system."""
        with open(os.path.join(ROOT, "distro", "build", "apps.list")) as f:
            flathub = {l.split()[0] for l in f if l.strip() and not l.startswith("#")}
        base = {"firefox", "org.mozilla.firefox", "org.kde.discover"}
        with open(os.path.join(ROOT, "distro", "build", "defaults.list")) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if "=" not in line:
                    continue
                for app in line.split("=", 1)[1].split("|"):
                    app = app.removesuffix(".desktop")
                    owner = app.rsplit(".", 1)[0] if app.startswith("org.libreoffice.LibreOffice.") else app
                    self.assertTrue(owner in flathub or app in base, f"{line}: {app} isn't installed")

    def _run_app_installer(self, apps_list, profile=None, installed=""):
        d = tempfile.mkdtemp()
        apps = os.path.join(d, "apps.list")
        with open(apps, "w") as f:
            f.write(apps_list)
        state = os.path.join(d, "state")
        os.makedirs(state)
        with open(os.path.join(state, "apps-installed"), "w") as f:
            f.write(installed)
        profile_file = os.path.join(d, "apps-profile")
        if profile is not None:
            with open(profile_file, "w") as f:
                f.write(profile + "\n")
        bindir = os.path.join(d, "bin")
        os.makedirs(bindir)
        with open(os.path.join(bindir, "flatpak"), "w") as f:
            f.write('#!/bin/sh\n[ "$1" = install ] && echo "$6" >> "%s/calls"\nexit 0\n' % d)
        with open(os.path.join(bindir, "uname"), "w") as f:
            f.write("#!/bin/sh\necho aarch64\n")
        for tool in ("flatpak", "uname"):
            os.chmod(os.path.join(bindir, tool), 0o755)
        src = _read(os.path.join(ROOT, "distro", "build", "omnilaunch-os-apps"))
        src = (src.replace("/usr/share/omnilaunch-os/apps.list", apps).replace("/var/lib/omnilaunch-os", state)
               .replace("/etc/omnilaunch-os/apps-profile", profile_file))
        script = os.path.join(d, "run.sh")
        with open(script, "w") as f:
            f.write(src)
        r = subprocess.run(["bash", script], env=dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"]),
                           capture_output=True, text=True)
        calls = _read(os.path.join(d, "calls")).split() if os.path.exists(os.path.join(d, "calls")) else []
        return r, calls

    SECTIONS = ("# ---- Everyday\norg.a.Office\n# ---- Creative suite [optional]\norg.b.Paint\n"
                "# ---- Games [optional]\norg.c.Game\n# ---- Looking after apps\norg.d.Seal\n")

    def test_all_apps_by_default(self):
        r, calls = self._run_app_installer(self.SECTIONS)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, ["org.a.Office", "org.b.Paint", "org.c.Game", "org.d.Seal"])

    def test_essential_profile_skips_optional_sections(self):
        r, calls = self._run_app_installer(self.SECTIONS, profile="essential")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, ["org.a.Office", "org.d.Seal"])

    def test_unknown_profile_is_refused(self):
        r, calls = self._run_app_installer(self.SECTIONS, profile="everything")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(calls, [])

    def test_shipped_list_marks_creative_and_games_optional(self):
        headings = [l for l in _read(os.path.join(ROOT, "distro", "build", "apps.list")).splitlines()
                    if l.startswith("# ----")]
        optional = [h for h in headings if "[optional]" in h]
        self.assertEqual(len(optional), 2, headings)
        self.assertTrue(any("Creative" in h for h in optional) and any("Games" in h for h in optional))

    def test_app_installer_respects_architectures_and_removals(self):
        d = tempfile.mkdtemp()
        apps = os.path.join(d, "apps.list")
        with open(apps, "w") as f:
            f.write("# comment\norg.a.App   # everyone\norg.b.Game  x86_64   # PCs only\norg.c.Gone\n")
        state = os.path.join(d, "state")
        os.makedirs(state)
        with open(os.path.join(state, "apps-installed"), "w") as f:
            f.write("org.c.Gone\n")                              # installed before, then removed by the user
        bindir = os.path.join(d, "bin")
        os.makedirs(bindir)
        with open(os.path.join(bindir, "flatpak"), "w") as f:
            f.write('#!/bin/sh\n[ "$1" = install ] && echo "$6" >> "%s/calls"\nexit 0\n' % d)
        os.chmod(os.path.join(bindir, "flatpak"), 0o755)
        with open(os.path.join(bindir, "uname"), "w") as f:
            f.write("#!/bin/sh\necho aarch64\n")
        os.chmod(os.path.join(bindir, "uname"), 0o755)
        src = _read(os.path.join(ROOT, "distro", "build", "omnilaunch-os-apps"))
        src = src.replace("/usr/share/omnilaunch-os/apps.list", apps).replace("/var/lib/omnilaunch-os", state)
        script = os.path.join(d, "run.sh")
        with open(script, "w") as f:
            f.write(src)
        r = subprocess.run(["bash", script], env=dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"]),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_read(os.path.join(d, "calls")).split(), ["org.a.App"])
        self.assertEqual(_read(os.path.join(state, "apps-status")).strip(), "done")


class ProtonSetupTests(unittest.TestCase):
    def test_fedora_without_the_package_uses_umu_release_build(self):
        from omnilaunch import provision, runners
        inst = mock.Mock(dry_run=False)
        releases = [{"tag_name": "1.3", "assets": [
            {"name": "umu-launcher-1.3-zipapp.tar", "browser_download_url": "https://x/umu-launcher-1.3-zipapp.tar"}]}]
        with mock.patch.object(runners, "which", side_effect=lambda *n: "/usr/bin/dnf" if "dnf" in n else None), \
                mock.patch.object(provision.subprocess, "run", return_value=mock.Mock(returncode=1)), \
                mock.patch.object(provision, "_fetch", side_effect=[json.dumps(releases), None]) as fetch, \
                mock.patch.object(runners, "find_umu", return_value="/x/umu-run"), \
                mock.patch.object(runners, "data_dir", return_value=tempfile.mkdtemp()):
            provision._install_proton(inst)
        self.assertNotIn(["sudo", "dnf", "install", "-y", "umu-launcher"], [c.args[0] for c in inst.run.call_args_list])
        self.assertEqual(fetch.call_args_list[1].args[0], "https://x/umu-launcher-1.3-zipapp.tar")
