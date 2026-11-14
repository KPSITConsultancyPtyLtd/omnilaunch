"""Checks against the real world, run by CI on real Windows, macOS and Linux machines.

Unlike the unit tests, these download real files, read live download indexes and change real
desktop settings (on throwaway CI machines). Not meant for running on your own computer.
"""

import json
import os
import plistlib
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest import mock  # noqa: E402

from omnilaunch import android, cli, desktop, provision, runners  # noqa: E402
from omnilaunch.detect import Platform, detect  # noqa: E402

FAILURES = []


def check(name, fn):
    try:
        result = fn()
        print(f"PASS  {name}" + (f": {result}" if result else ""), flush=True)
    except Exception as e:  # noqa: BLE001 - report everything
        FAILURES.append(name)
        print(f"FAIL  {name}: {type(e).__name__}: {e}", flush=True)


def real_apk():
    path = os.path.join(tempfile.mkdtemp(), "F-Droid.apk")
    provision._fetch("https://f-droid.org/F-Droid.apk", path)
    info = detect(path)
    assert info.platform == Platform.ANDROID and info.kind == "APK", info
    assert info.package == "org.fdroid.fdroid", info.package
    return info.package


def android_index():
    emu = android.pick_emulator_url(provision._fetch(android.REPO_INDEX))
    api, img = android.pick_system_image(provision._fetch(android.SYSIMG_INDEX), android.guest_abi())
    tools = android.platform_tools_url()
    for url in (emu, img, tools):   # make sure each download really exists (headers only)
        subprocess.run(["curl", "-fsSIL", "-o", os.devnull, url], check=True)
    return f"{os.path.basename(emu)}, android-{api} {os.path.basename(img)}"


def github_asset(api_url, patterns):
    releases = json.loads(provision._fetch(api_url))
    name, url = provision.pick_release_asset(releases, patterns)
    subprocess.run(["curl", "-fsSIL", "-o", os.devnull, url], check=True)
    return name


def mac_double_click():
    steps = desktop.integrate()
    app = desktop.MAC_APP
    assert os.path.isdir(app), "OmniLaunch.app was not created"
    with open(os.path.join(app, "Contents", "Info.plist"), "rb") as f:
        plist = plistlib.load(f)
    exts = {e for d in plist["CFBundleDocumentTypes"] for e in d["CFBundleTypeExtensions"]}
    assert {"exe", "msi", "apk", "AppImage"} <= exts, exts
    subprocess.run(["/usr/bin/codesign", "--verify", app], check=True)
    print("      " + "\n      ".join(steps), flush=True)
    handlers = desktop.mac_default_handlers()
    ours = [ext for ext, h in handlers.items() if h.lower() == desktop.MAC_BUNDLE_ID]
    assert set(ours) == set(desktop.MAC_EXTENSIONS), f"default apps now: {handlers}"
    return f"all of {', '.join('.' + e for e in ours)} open with OmniLaunch"


def mac_double_click_opens_omnilaunch():
    """Open a .exe exactly like a double-click in Finder does, and check OmniLaunch received it."""
    import time
    exe = os.path.join(tempfile.mkdtemp(), "hello.exe")
    with open(exe, "wb") as f:
        f.write(b"MZ" + bytes(510))
    log = os.path.join(runners.data_dir(), "omnilaunch.log")
    subprocess.run(["/usr/bin/open", exe], check=True)
    deadline = time.time() + 60
    while time.time() < deadline:
        # macOS reports temp files under /private/var, the real path of /var
        if os.path.exists(log) and f"opening {os.path.realpath(exe)}" in open(log).read():
            return "Finder -> OmniLaunch.app -> omnilaunch run --gui " + exe
        time.sleep(2)
    raise AssertionError("OmniLaunch didn't receive the file. Log: " +
                         (open(log).read()[-500:] if os.path.exists(log) else "(no log)"))


PUTTY = os.path.join(tempfile.mkdtemp(), "putty.exe")


def real_exe_icon():
    from omnilaunch import icons
    provision._fetch("https://the.earth.li/~sgtatham/putty/latest/w64/putty.exe", PUTTY)
    ico = icons.extract_ico(PUTTY)
    assert ico and ico[:4] == b"\0\0\1\0", "no icon found in putty.exe"
    count = int.from_bytes(ico[4:6], "little")
    return f"{count} icon sizes, {len(ico)} bytes"


def mac_installed_program_launcher():
    """A program installed in a Windows environment gets a real app in /Applications and on the Desktop,
    with the program's own icon."""
    from omnilaunch import shortcuts
    os.makedirs(os.path.expanduser("~/Desktop"), exist_ok=True)
    app = shortcuts.create_launcher("OmniLaunch CI Test", "wine/ci-test", PUTTY)
    plain = os.path.join(tempfile.mkdtemp(), "Plain.app")
    subprocess.run(["/usr/bin/osacompile", "-o", plain, "-e", "on run\nend run"], check=True)
    icon = open(os.path.join(app, "Contents", "Resources", "applet.icns"), "rb").read()
    default = open(os.path.join(plain, "Contents", "Resources", "applet.icns"), "rb").read()
    assert icon[:4] == b"icns" and icon != default, "launcher still has the default script icon"
    assert app.startswith(shortcuts.launcher_dir()) and os.path.isdir(app), app
    subprocess.run(["/usr/bin/plutil", "-lint", os.path.join(app, "Contents", "Info.plist")], check=True)
    with open(os.path.join(app, "Contents", "Info.plist"), "rb") as f:
        plist = plistlib.load(f)
    assert plist["CFBundleDocumentTypes"][0]["LSItemContentTypes"], "not offered under Open With"
    subprocess.run(["/usr/bin/codesign", "--verify", app], check=True)
    link = os.path.expanduser("~/Desktop/OmniLaunch CI Test.app")
    assert os.path.islink(link) and os.readlink(link) == app, link
    return f"{app} (+ Desktop shortcut)"


def mac_dmg_install():
    work = tempfile.mkdtemp()
    app = os.path.join(work, "src", "OmniTest.app")
    os.makedirs(os.path.dirname(app))
    subprocess.run(["/usr/bin/osacompile", "-o", app, "-e", "on run\nend run"], check=True)
    dmg = os.path.join(work, "OmniTest.dmg")
    subprocess.run(["/usr/bin/hdiutil", "create", "-quiet", "-srcfolder", os.path.dirname(app),
                    "-volname", "OmniTest", dmg], check=True)
    # Don't let OmniLaunch hand this process over to the installed app; just record what it would open.
    with mock.patch.object(cli.os, "execvpe") as execvpe:
        code = cli.main(["run", dmg])
    installed = [p for p in ("/Applications/OmniTest.app", os.path.expanduser("~/Applications/OmniTest.app"))
                 if os.path.isdir(p)]
    assert code == 0 and installed, f"exit {code}, installed={installed}"
    opened = execvpe.call_args[0][1]
    assert opened == ["/usr/bin/open", installed[0]], opened
    return f"installed and opened {installed[0]}"


def windows_double_click():
    steps = desktop.integrate()
    assert desktop.windows_integrated(), "registry entries missing"
    out = subprocess.run(["reg", "query", r"HKCU\Software\Classes\.apk", "/ve"], capture_output=True, text=True).stdout
    assert "OmniLaunch.App" in out, out
    desktop.unintegrate()
    assert not desktop.windows_integrated()
    return "; ".join(steps)


def linux_double_click():
    os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
    desktop.integrate()
    path = os.path.join(desktop.applications_dir(), desktop.DESKTOP_ID)
    with open(path) as f:
        assert "application/vnd.android.package-archive" in f.read()
    return path


check("reads a real APK (F-Droid)", real_apk)
check("reads the icon out of a real Windows program (PuTTY)", real_exe_icon)
check("finds Android Emulator + system image in Google's live index", android_index)
if runners.HOST_OS == "macos":
    check("finds Wine Stable for macOS", lambda: github_asset(
        provision.WINE_RELEASES_API, [r"^wine-stable.*\.tar\.xz$", r"^wine-devel.*\.tar\.xz$"]))
    check("finds the XQuartz installer", lambda: github_asset(provision.XQUARTZ_RELEASES_API, [r"^XQuartz-[\d.]+\.pkg$"]))
    check("double-click: builds and registers OmniLaunch.app", mac_double_click)
    check("double-click: opening a .exe launches OmniLaunch", mac_double_click_opens_omnilaunch)
    check("installed programs get an app in Applications and on the Desktop", mac_installed_program_launcher)
    check("installs an app from a .dmg", mac_dmg_install)
if runners.HOST_OS == "linux":
    check("finds umu-launcher (Proton) download", lambda: github_asset(
        provision.UMU_RELEASES_API, [r"zipapp.*\.tar(\.gz|\.xz)?$"]))
    check("double-click: Linux desktop entry", linux_double_click)
if runners.HOST_OS == "windows":
    check("double-click: registers .apk/.AppImage, then removes it", windows_double_click)

print(f"\n{len(FAILURES)} failed" if FAILURES else "\nall real-world checks passed")
sys.exit(1 if FAILURES else 0)
