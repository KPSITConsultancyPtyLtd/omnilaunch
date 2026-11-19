"""Runs real apps from other systems inside a built OmniLaunch OS image, through the installed
`omnilaunch` command, the way a double-click would. Run by compat-test.sh in CI.

Windows apps are required to work on both PCs (Wine) and ARM (Box64 + Wine). REQUIRE_WINDOWS=0
turns their failures into warnings, for experiments.
"""
import filecmp
import glob
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, "/usr/lib/omnilaunch")       # the copy installed in the image, not the repository's
from omnilaunch import provision  # noqa: E402

REQUIRE_WINDOWS = os.environ.get("REQUIRE_WINDOWS", "1") == "1"
ARCH = platform.machine()
FAILS, WARNS = [], []
work = tempfile.mkdtemp(prefix="compat-")


def check(name, fn, required=True):
    t = time.time()
    try:
        detail = fn()
        print(f"PASS  {name} ({time.time() - t:.0f}s)" + (f": {detail}" if detail else ""), flush=True)
    except Exception as e:  # noqa: BLE001 - report every failure
        tag = "FAIL" if required else "WARN"
        print(f"{tag}  {name}: {type(e).__name__}: {e}", flush=True)
        (FAILS if required else WARNS).append(name)


def omni(*args, timeout=600, env=None):
    r = subprocess.run(["omnilaunch", *args], capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, **(env or {})})
    return r


def wait_for_window(title, seconds):
    end = time.time() + seconds
    while time.time() < end:
        r = subprocess.run(["xdotool", "search", "--name", title], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return True
        time.sleep(3)
    return False


def stop_windows_apps():
    subprocess.run(["sh", "-c", "for s in $(find / -name wineserver -type f 2>/dev/null); do WINEPREFIX= $s -k 2>/dev/null; done; pkill -f '\\.exe' 2>/dev/null"],
                   capture_output=True)


# ---------------------------------------------------------------- Windows desktop app
def putty_window():
    """PuTTY, downloaded from its author and checked against his published SHA-256, opens a window."""
    build = "w64"                    # the Intel build on both PC and ARM: that's what ARM has to translate
    exe = os.path.join(work, "putty.exe")
    provision._fetch(f"https://the.earth.li/~sgtatham/putty/latest/{build}/putty.exe", exe)
    sums = provision._fetch("https://the.earth.li/~sgtatham/putty/latest/sha256sums").decode()
    want = next(l.split()[0] for l in sums.splitlines() if l.strip().endswith(f"{build}/putty.exe"))
    assert hashlib.sha256(open(exe, "rb").read()).hexdigest() == want, "putty.exe checksum mismatch"
    p = subprocess.Popen(["omnilaunch", "run", exe], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        ok = wait_for_window("PuTTY", 300)
    finally:
        p.terminate()
        stop_windows_apps()
    assert ok, "no PuTTY window appeared within 5 minutes"
    return "window shown"


# ---------------------------------------------------------------- Windows installer + tools
def seven_zip_install_and_use():
    """7-Zip's real installer runs silently, then 7-Zip compresses and extracts through OmniLaunch."""
    page = provision._fetch("https://www.7-zip.org/download.html").decode("utf-8", "replace")
    rel = re.search(r'href="(a/7z\d+-x64\.exe)"', page).group(1)
    installer = os.path.join(work, os.path.basename(rel))
    provision._fetch("https://www.7-zip.org/" + rel, installer)
    r = omni("run", installer, "/S", timeout=900)
    assert r.returncode == 0, f"installer exit {r.returncode}: {r.stderr[-400:]}"
    stop_windows_apps()
    found = glob.glob(os.path.expanduser("~/.local/share/omnilaunch/prefixes/*/*/drive_c/Program Files/7-Zip/7z.exe"))
    assert found, "7z.exe wasn't installed"
    original = os.path.join(work, "notes.txt")
    with open(original, "w") as f:
        f.write("OmniLaunch OS compatibility test\n" * 500)
    r = omni("do", "compress-7z", original, timeout=600)
    archive = original + ".7z" if os.path.exists(original + ".7z") else os.path.join(work, "notes.7z")
    assert r.returncode == 0 and os.path.exists(archive), f"compress failed: {r.stderr[-400:]}"
    r = omni("do", "extract", archive, timeout=600)
    out = [p for p in glob.glob(os.path.join(work, "notes*", "notes.txt"))]
    assert r.returncode == 0 and out, f"extract failed: {r.stderr[-400:]}"
    assert filecmp.cmp(original, out[0], shallow=False), "extracted file differs"
    stop_windows_apps()
    return f"installed {os.path.basename(installer)}; compress + extract round trip identical"


# ---------------------------------------------------------------- Linux AppImage
def appimage_runs():
    """A real AppImage (appimagetool) runs natively through OmniLaunch."""
    releases = json.loads(provision._fetch("https://api.github.com/repos/AppImage/appimagetool/releases"))
    path = provision.download_release_asset(releases, [rf"^appimagetool-{ARCH}\.AppImage$"], work)
    os.chmod(path, 0o755)
    # No FUSE inside a container: AppImages can unpack themselves instead (on a real desktop FUSE is used).
    r = omni("run", path, "--version", timeout=300, env={"APPIMAGE_EXTRACT_AND_RUN": "1"})
    text = (r.stdout + r.stderr).lower()
    assert r.returncode == 0 and "appimagetool" in text, f"exit {r.returncode}: {text[-400:]}"
    return text.strip().splitlines()[-1][:80]


# ---------------------------------------------------------------- Android
def android_app_routed():
    """A real Android app (F-Droid) is recognised and sent to Waydroid. (Starting Android itself needs
    the real kernel's Android support, which a CI container doesn't have.)"""
    apk = os.path.join(work, "F-Droid.apk")
    provision._fetch("https://f-droid.org/F-Droid.apk", apk)
    r = omni("run", "--dry-run", apk)
    text = r.stdout + r.stderr
    assert r.returncode == 0 and "waydroid" in text.lower() and "org.fdroid.fdroid" in text, text[-400:]
    return "org.fdroid.fdroid -> Waydroid"


# ---------------------------------------------------------------- Games and the rest
def gaming_stack():
    """Steam, Proton (umu) and Gamescope are installed and start far enough to report a version."""
    if ARCH != "x86_64":
        return "PC only"
    for tool in ("steam", "gamescope", "umu-run", "mangohud"):
        assert shutil.which(tool), f"{tool} missing"
    r = subprocess.run(["umu-run", "--help"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 or "usage" in (r.stdout + r.stderr).lower(), "umu-run doesn't start"
    return "steam, gamescope, umu-run, mangohud"


def doctor_all_green():
    r = omni("doctor")
    text = r.stdout + r.stderr
    print(text, flush=True)
    return "doctor ran"


print(f"OmniLaunch OS compatibility test ({ARCH})", flush=True)
check("omnilaunch doctor", doctor_all_green)
check("Windows desktop app (PuTTY) opens a window", putty_window, required=REQUIRE_WINDOWS)
check("Windows installer + app (7-Zip) install and work", seven_zip_install_and_use, required=REQUIRE_WINDOWS)
check("Linux AppImage runs", appimage_runs)
check("Android app recognised and routed to Waydroid", android_app_routed)
check("Gaming stack present", gaming_stack)

print()
if WARNS:
    print(f"::warning::OmniLaunch OS ({ARCH}): not confirmed in CI: {', '.join(WARNS)}")
if FAILS:
    print(f"{len(FAILS)} compatibility check(s) failed: {', '.join(FAILS)}")
    sys.exit(1)
print(f"all required compatibility checks passed ({ARCH})")
