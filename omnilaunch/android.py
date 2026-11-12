"""Run Android apps (.apk): install them into an Android environment and launch them.

Linux with Waydroid installed: Waydroid (Android in a container, apps get their own windows).
Everywhere else: Google's official Android Emulator. OmniLaunch installs it itself (no Android
Studio, no Java) by downloading the packages Google's SDK manager would, and creates a virtual
device called "omnilaunch".
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

from . import runners

REPO = "https://dl.google.com/android/repository/"
REPO_INDEX = REPO + "repository2-3.xml"
SYSIMG_INDEX = REPO + "sys-img/google_apis/sys-img2-3.xml"
PREFERRED_API = 34
AVD_NAME = "omnilaunch"


def sdk_root() -> str:
    return os.path.join(runners.data_dir(), "android", "sdk")


def avd_home() -> str:
    return os.path.join(runners.data_dir(), "android", "avd")


def _exe(name: str) -> str:
    return name + (".exe" if runners.HOST_OS == "windows" else "")


def emulator_path() -> str:
    return os.path.join(sdk_root(), "emulator", _exe("emulator"))


def adb_path() -> str:
    return os.path.join(sdk_root(), "platform-tools", _exe("adb"))


def emulator_ready() -> bool:
    return (os.path.exists(emulator_path()) and os.path.exists(adb_path())
            and os.path.exists(os.path.join(avd_home(), AVD_NAME + ".ini")))


def waydroid() -> str | None:
    return runners.which("waydroid") if runners.HOST_OS == "linux" else None


def env() -> dict:
    return {"ANDROID_SDK_ROOT": sdk_root(), "ANDROID_HOME": sdk_root(), "ANDROID_AVD_HOME": avd_home()}


def guest_abi() -> str:
    return "arm64-v8a" if runners.HOST_ARCH == "arm64" else "x86_64"


# ---------------------------------------------------------------- picking downloads from Google's index

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el, name):
    return next((c for c in el if _local(c.tag) == name), None)


def _iter(el, name):
    return (c for c in el.iter() if _local(c.tag) == name)


def _stable(pkg) -> bool:
    ref = _find(pkg, "channelRef")
    return ref is None or ref.get("ref") in ("channel-0", None)


def host_os_name() -> str:
    return {"macos": "macosx", "windows": "windows"}.get(runners.HOST_OS, "linux")


def host_arch_name() -> str:
    return "aarch64" if runners.HOST_ARCH == "arm64" else "x64"


def pick_emulator_url(index_xml: bytes) -> str:
    root = ET.fromstring(index_xml)
    for pkg in _iter(root, "remotePackage"):
        if pkg.get("path") != "emulator" or not _stable(pkg):
            continue
        for archive in _iter(pkg, "archive"):
            os_el, arch_el = _find(archive, "host-os"), _find(archive, "host-arch")
            if os_el is None or os_el.text != host_os_name():
                continue
            if arch_el is not None and arch_el.text != host_arch_name():
                continue
            url = next(_iter(archive, "url")).text
            return url if url.startswith("http") else REPO + url
    raise ValueError("No Android Emulator download for this computer was found in Google's index.")


def pick_system_image(index_xml: bytes, abi: str) -> tuple[int, str]:
    """(api level, url) of the Google APIs system image to use: API 34 if offered, else the newest."""
    root = ET.fromstring(index_xml)
    found = {}
    for pkg in _iter(root, "remotePackage"):
        parts = (pkg.get("path") or "").split(";")
        if len(parts) != 4 or parts[2] != "google_apis" or parts[3] != abi or not _stable(pkg):
            continue
        try:
            api = int(parts[1].replace("android-", ""))
        except ValueError:
            continue
        url = next(_iter(pkg, "url")).text
        found[api] = url if url.startswith("http") else REPO + "sys-img/google_apis/" + url
    if not found:
        raise ValueError(f"No Android system image for {abi} was found in Google's index.")
    near = [a for a in found if a <= PREFERRED_API + 2]
    api = PREFERRED_API if PREFERRED_API in found else max(near or found)
    return api, found[api]


def platform_tools_url() -> str:
    name = {"macos": "darwin", "windows": "windows"}.get(runners.HOST_OS, "linux")
    return REPO + "platform-tools-latest-" + name + ".zip"


def write_avd(api: int, abi: str) -> None:
    """Create the 'omnilaunch' virtual device by hand (what avdmanager would do, minus Java)."""
    avd_dir = os.path.join(avd_home(), AVD_NAME + ".avd")
    os.makedirs(avd_dir, exist_ok=True)
    os.makedirs(os.path.join(sdk_root(), "platforms"), exist_ok=True)   # emulator checks the SDK looks valid
    cpu = "arm64" if abi.startswith("arm64") else "x86_64"
    config = {
        "AvdId": AVD_NAME, "avd.ini.displayname": "OmniLaunch", "avd.ini.encoding": "UTF-8",
        "abi.type": abi, "hw.cpu.arch": cpu, "hw.cpu.ncore": "4",
        "image.sysdir.1": f"system-images/android-{api}/google_apis/{abi}/",
        "tag.id": "google_apis", "tag.display": "Google APIs", "PlayStore.enabled": "false",
        "hw.ramSize": "4096", "vm.heapSize": "512", "disk.dataPartition.size": "8G",
        "hw.keyboard": "yes", "hw.gpu.enabled": "yes", "hw.gpu.mode": "auto",
        "hw.lcd.width": "1080", "hw.lcd.height": "2340", "hw.lcd.density": "420",
        "showDeviceFrame": "no", "fastboot.forceColdBoot": "no",
    }
    with open(os.path.join(avd_dir, "config.ini"), "w") as f:
        f.writelines(f"{k}={v}\n" for k, v in config.items())
    with open(os.path.join(avd_home(), AVD_NAME + ".ini"), "w") as f:
        f.write(f"avd.ini.encoding=UTF-8\npath={avd_dir}\npath.rel=avd/{AVD_NAME}.avd\ntarget=android-{api}\n")


# ---------------------------------------------------------------- launching

def _adb(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run([adb_path(), *args], capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, **env()})


def emulator_running() -> bool:
    try:
        out = _adb("devices", timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.startswith("emulator-") and line.rstrip().endswith("device") for line in out.splitlines())


def start_emulator_and_wait(timeout: float = 300) -> bool:
    """Boot the virtual phone (if it isn't already running) and wait until Android is ready."""
    if not emulator_running():
        subprocess.Popen([emulator_path(), "-avd", AVD_NAME, "-no-boot-anim", "-no-snapshot-save"],
                         env={**os.environ, **env()}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=(os.name != "nt"))
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if _adb("shell", "getprop", "sys.boot_completed", timeout=15).stdout.strip() == "1":
                return True
        except (OSError, subprocess.SubprocessError):
            pass
        time.sleep(3)
    return False


def start_waydroid_and_wait(timeout: float = 120) -> bool:
    """Start the Waydroid session if needed and wait until it's running."""
    wd = waydroid()

    def running() -> bool:
        try:
            return "RUNNING" in subprocess.run([wd, "status"], capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            return False

    if not running():
        subprocess.Popen([wd, "session", "start"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if running():
            return True
        time.sleep(2)
    return False


def plan(info, args: list) -> "runners.LaunchPlan":
    if info.kind != "APK":
        raise runners.UnsupportedApp(
            f"{os.path.basename(info.path)} is a split Android bundle ({info.kind}), which can't be installed directly.",
            "Download the single .apk version of the app instead.")
    if not info.package:
        raise runners.UnsupportedApp("Couldn't read the app's package name from this APK; it may be damaged.")
    notes = []
    wd = waydroid()
    if wd:
        lp = runners.LaunchPlan("android (waydroid)", [wd, "app", "launch", info.package], notes=notes)
        start_waydroid_and_wait.__doc__ = "start Waydroid and wait for Android"
        lp.pre = [(start_waydroid_and_wait, True), ([wd, "app", "install", info.path], True)]
        return lp
    if not emulator_ready():
        raise runners.UnsupportedApp("Android apps need OmniLaunch's Android environment, which isn't set up yet.",
                                     "Run: omnilaunch setup android", needs="android")
    abi = guest_abi()
    if info.archs and runners.HOST_ARCH not in info.archs and not (
            abi == "x86_64" and "arm64" in info.archs and runners.HOST_ARCH == "x86_64"):
        notes.append(f"This app includes native code for {', '.join(info.archs)} only; it may not start.")
    if abi == "x86_64" and info.archs and "x86_64" not in info.archs and "x86" not in info.archs:
        notes.append("ARM-only app on an Intel/AMD PC: Android translates it, which works for most apps "
                     "but not all games.")
    notes.append("Opens in the Android window; the first start takes a minute while Android boots.")
    adb = adb_path()
    lp = runners.LaunchPlan(
        "android (emulator)",
        [adb, "shell", "monkey", "-p", info.package, "-c", "android.intent.category.LAUNCHER", "1"],
        env=env(), notes=notes)
    start_emulator_and_wait.__doc__ = "start the Android emulator and wait for it to boot"
    lp.pre = [(start_emulator_and_wait, True), ([adb, "install", "-r", info.path], True)]
    return lp
