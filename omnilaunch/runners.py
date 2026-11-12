"""Turn a detected app into the command that runs it on this machine (Linux, macOS or Windows host)."""

from __future__ import annotations

import glob
import hashlib
import ntpath
import os
import platform as host_platform
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

from .detect import AppInfo, Platform

RUNNERS = ("native", "wine", "proton", "darling", "container", "wsl", "vm", "android", "box64")

HOST_OS = {"darwin": "macos", "linux": "linux", "win32": "windows", "cygwin": "windows"}.get(
    sys.platform, "linux" if sys.platform.startswith("linux") else sys.platform)
HOST_ARCH = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64", "arm64": "arm64",
             "i686": "x86", "i386": "x86", "armv7l": "arm", "riscv64": "riscv"}.get(
    host_platform.machine().lower(), host_platform.machine().lower())

# CPU architectures a Linux host can run without translation.
NATIVE_COMPAT = {"x86_64": {"x86_64", "x86"}, "arm64": {"arm64"}, "x86": {"x86"},
                 "arm": {"arm"}, "riscv": {"riscv"}}

# Translators that run a foreign-CPU Linux binary on a Linux host.
ARCH_TRANSLATORS = {"x86_64": ["box64", "FEXInterpreter", "qemu-x86_64"],
                    "x86": ["box86", "FEXInterpreter", "qemu-i386"],
                    "arm64": ["qemu-aarch64"],
                    "arm": ["qemu-arm"],
                    "riscv": ["qemu-riscv64"]}

# Docker/Podman platform names for running Linux apps in a container.
CONTAINER_PLATFORMS = {"x86_64": "linux/amd64", "x86": "linux/386", "arm64": "linux/arm64",
                       "arm": "linux/arm/v7", "riscv": "linux/riscv64"}
DEFAULT_LINUX_IMAGE = "debian:stable-slim"

# Where standalone Wine builds live on macOS (Homebrew's Wine casks were disabled in 2026).
MAC_WINE_GLOBS = [
    "/Applications/Wine*.app/Contents/Resources/wine/bin/wine",
    "/Applications/Wine*.app/Contents/Resources/start/bin/wine",
    "~/Applications/Wine*.app/Contents/Resources/wine/bin/wine",
    "/opt/local/bin/wine",      # MacPorts
    "/opt/homebrew/bin/wine",   # older Homebrew installs that are still present
    "/usr/local/bin/wine",
]

ARCHIVE_HINTS = {
    "DMG": "Disk image. Open it (double-click, or `hdiutil attach file.dmg` on a Mac; `7z x file.dmg` "
           "on Linux), then run the .app inside.",
    "PKG": "Installer package. On a Mac, double-click it to install; on Linux, installers rarely work.",
}


def data_dir() -> str:
    if os.environ.get("OMNILAUNCH_HOME"):
        return os.path.expanduser(os.environ["OMNILAUNCH_HOME"])
    if HOST_OS == "macos":
        return os.path.expanduser("~/Library/Application Support/OmniLaunch")
    if HOST_OS == "windows":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
        return os.path.join(base, "OmniLaunch")
    return os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "omnilaunch")


class UnsupportedApp(Exception):
    """Raised with a human-friendly explanation and a fix, when we can't run something."""

    def __init__(self, message: str, hint: str = "", needs: str = ""):
        super().__init__(message)
        self.hint = hint
        self.needs = needs   # name of a provision component that would fix this, if any


@dataclass
class LaunchPlan:
    runner: str
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    cwd: str | None = None
    prefix: str | None = None   # isolated environment directory, created before launch
    pre: list = field(default_factory=list)   # (argv, required) commands to run before launching


def which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def find_wine() -> str | None:
    w = which("wine", "wine64")
    if w or HOST_OS != "macos":
        return w
    for pattern in MAC_WINE_GLOBS:
        hits = sorted(glob.glob(os.path.expanduser(pattern)))
        if hits and os.access(hits[0], os.X_OK):
            return hits[0]
    return None


def find_umu() -> str | None:
    """umu-launcher's umu-run: from the system, or the copy `omnilaunch setup proton` downloaded."""
    if HOST_OS != "linux":
        return None
    found = which("umu-run")
    if found:
        return found
    for path in sorted(glob.glob(os.path.join(data_dir(), "umu", "**", "umu-run"), recursive=True)):
        if os.access(path, os.X_OK):
            return path
    return None


def find_container_engine() -> str | None:
    return which("docker", "podman")


def find_wsl() -> str | None:
    return which("wsl") if HOST_OS == "windows" else None


def wsl_distros() -> list[str]:
    """Installed WSL distributions (empty if WSL is missing or has no Linux installed)."""
    wsl = find_wsl()
    if not wsl:
        return []
    try:
        raw = subprocess.run([wsl, "--list", "--quiet"], capture_output=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    text = raw.decode("utf-16-le", errors="ignore") if b"\x00" in raw else raw.decode(errors="ignore")
    return [line.strip().strip("\x00") for line in text.splitlines() if line.strip().strip("\x00")]


def to_wsl_path(path: str) -> str:
    """C:\\Users\\me\\app -> /mnt/c/Users/me/app ; \\\\wsl.localhost\\Ubuntu\\home\\me -> /home/me"""
    p = path.replace("/", "\\")
    low = p.lower()
    for unc in ("\\\\wsl.localhost\\", "\\\\wsl$\\"):
        if low.startswith(unc):
            rest = p[len(unc):].split("\\", 1)          # drop the distro name
            return "/" + (rest[1].replace("\\", "/") if len(rest) > 1 else "")
    drive, tail = ntpath.splitdrive(p)
    if len(drive) == 2 and drive[1] == ":":
        return f"/mnt/{drive[0].lower()}" + tail.replace("\\", "/")
    return path.replace("\\", "/")


XQUARTZ_APP = "/Applications/Utilities/XQuartz.app"
LINUX_GUI_IMAGE = "omnilaunch/linux-gui"


def xquartz_installed() -> bool:
    return HOST_OS == "macos" and os.path.isdir(XQUARTZ_APP)


def linux_gui_image_ready() -> bool:
    engine = find_container_engine()
    if not engine:
        return False
    try:
        return subprocess.run([engine, "image", "inspect", LINUX_GUI_IMAGE], capture_output=True,
                              timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def rosetta_installed() -> bool:
    if HOST_OS != "macos" or HOST_ARCH != "arm64":
        return False
    if os.path.exists("/Library/Apple/usr/share/rosetta/rosetta"):
        return True
    try:
        return subprocess.run(["/usr/bin/pgrep", "-q", "oahd"], check=False).returncode == 0
    except OSError:
        return False


def app_id(info: AppInfo) -> str:
    """Stable per-app id, so each app gets its own isolated environment."""
    base = os.path.splitext(os.path.basename(info.path.rstrip("/\\")))[0] or "app"
    base = "".join(c if c.isalnum() or c in "-_." else "_" for c in base)
    digest = hashlib.sha1(info.path.encode()).hexdigest()[:8]
    return f"{base}-{digest}"


def host_path_for_windows(arg: str) -> str:
    """Files passed to a Windows program (e.g. 'open this .zip with 7-Zip') must be Windows paths.
    Wine shows this computer's whole filesystem as drive Z:, so /Users/me/a.zip -> Z:\\Users\\me\\a.zip.
    Anything that isn't an existing absolute path (options like /S) is passed through unchanged."""
    if HOST_OS == "windows" or not arg.startswith("/") or not os.path.exists(arg):
        return arg
    return "Z:" + arg.replace("/", "\\")


def enclosing_env(path: str) -> str | None:
    """If an app lives inside one of OmniLaunch's environments (a program an installer put there),
    the name of that environment, e.g. "wine/7z2606-x64-8dc73050". Running the program there
    keeps its settings and avoids creating a second, duplicate Windows environment."""
    root = os.path.realpath(os.path.join(data_dir(), "prefixes"))
    real = os.path.realpath(path)
    if not real.startswith(root + os.sep):
        return None
    parts = os.path.relpath(real, root).split(os.sep)
    if len(parts) >= 3 and parts[0] in ("wine", "proton", "box64"):
        return parts[0] + "/" + parts[1]
    return None


def default_runner(info: AppInfo) -> str:
    if info.platform == Platform.ANDROID:
        return "android"
    if HOST_OS == "windows":
        if info.platform == Platform.WINDOWS:
            return "native"
        if info.platform == Platform.MACOS:
            return "native"   # explained as unsupported by _windows_native
        return "wsl" if find_wsl() or not find_container_engine() else "container"
    if info.platform == Platform.WINDOWS:
        if HOST_OS == "linux" and (find_umu() or os.environ.get("OMNILAUNCH_PROTON")):
            return "proton"
        if os.environ.get("OMNILAUNCH_WINDOWS_RUNNER") in RUNNERS:
            return os.environ["OMNILAUNCH_WINDOWS_RUNNER"]
        if rosetta_era_over():
            return "box64"   # Wine on a Mac needs Rosetta, which macOS 28+ no longer offers for apps
        if find_wine():
            return "wine"
        from . import vms
        if vms.get("windows"):
            return "vm"
        # Nothing installed yet: on x86 Linux offer Proton (best for games), elsewhere Wine.
        return "proton" if HOST_OS == "linux" and HOST_ARCH == "x86_64" else "wine"
    if info.platform == Platform.SCRIPT:
        return "native"
    if HOST_OS == "macos":
        return "native" if info.platform == Platform.MACOS else "container"
    return "darling" if info.platform == Platform.MACOS else "native"


def plan(info: AppInfo, args: list[str] | None = None, runner: str | None = None,
         verbose: bool = False, env_name: str | None = None) -> LaunchPlan:
    """Build a launch plan. `runner` forces a specific layer; `env_name` reuses an existing environment
    (e.g. "wine/setup-1a2b3c4d") so programs installed there run with their settings and files."""
    args = list(args or [])
    if runner and runner not in RUNNERS:
        raise UnsupportedApp(f"Unknown runner '{runner}'.", f"Choose one of: {', '.join(RUNNERS)}")
    if not env_name and info.platform == Platform.WINDOWS:
        env_name = enclosing_env(info.path)
    if env_name:
        env_name = env_name.replace("\\", "/").strip("/")
        env_root = os.path.join(data_dir(), "prefixes", *env_name.split("/"))
        if not os.path.isdir(env_root):
            raise UnsupportedApp(f"There's no environment called '{env_name}'.", "See: omnilaunch envs list")
        runner = runner or env_name.split("/", 1)[0]
    if info.platform == Platform.ARCHIVE and info.kind in ("DMG", "PKG"):
        return _mac_package(info)
    if info.platform == Platform.ARCHIVE:
        raise UnsupportedApp(
            f"{os.path.basename(info.path)} is an archive ({info.kind}), not an app.",
            ARCHIVE_HINTS.get(info.kind, "Extract it first, then run the app inside."))
    if info.platform == Platform.UNKNOWN and not runner:
        raise UnsupportedApp(f"Don't know how to run {info.path} ({info.kind}).",
                             "Use --runner to force one if you know what it is.")

    target = runner or default_runner(info)
    if target == "native":
        if info.platform == Platform.SCRIPT:
            lp = _script(info, args)
        elif HOST_OS == "macos":
            lp = _mac_native(info, args)
        elif HOST_OS == "windows":
            lp = _windows_native(info, args)
        else:
            lp = _linux_native(info, args)
    elif target in ("wine", "proton"):
        lp = _windows(info, args, target, verbose, env_name)
    elif target == "container":
        lp = _container(info, args)
    elif target == "wsl":
        lp = _wsl(info, args)
    elif target == "vm":
        lp = _vm(info, args)
    elif target == "box64":
        lp = _box64(info, args, verbose, env_name)
    elif target == "android":
        if info.platform != Platform.ANDROID:
            raise UnsupportedApp("The Android runner only runs Android apps (.apk).")
        from . import android
        lp = android.plan(info, args)
    else:
        lp = _darling(info, args)
    if lp.cwd is None:
        lp.cwd = os.path.dirname(info.executable)
    return lp


def _require_exec(info: AppInfo) -> None:
    if not os.access(info.executable, os.X_OK):
        raise UnsupportedApp(f"{os.path.basename(info.executable)} isn't marked as executable.",
                             f"Run: chmod +x {shlex.quote(info.executable)}")


def _script(info: AppInfo, args: list[str]) -> LaunchPlan:
    if os.access(info.executable, os.X_OK):
        return LaunchPlan("native", [info.executable, *args])
    # Not executable: call the interpreter from the shebang directly.
    interp = shlex.split(info.interpreter) if info.interpreter else []
    if not interp:
        raise UnsupportedApp("Script has an empty #! line.")
    return LaunchPlan("native (via interpreter)", [*interp, info.executable, *args])


# ---------------------------------------------------------------- Linux host

def _linux_native(info: AppInfo, args: list[str]) -> LaunchPlan:
    if info.platform == Platform.MACOS:
        raise UnsupportedApp("Mac apps can't run natively on Linux.", "Use --runner darling.")
    if info.arch == "unknown" or info.arch in NATIVE_COMPAT.get(HOST_ARCH, {HOST_ARCH}):
        _require_exec(info)
        return LaunchPlan("native", [info.executable, *args])
    tr = which(*ARCH_TRANSLATORS.get(info.arch, []))
    if not tr:
        raise UnsupportedApp(
            f"{os.path.basename(info.path)} is built for {info.arch}, but this machine is {HOST_ARCH}.",
            "Install box64/FEX-Emu (x86 apps on ARM) or qemu-user to translate it, or use --runner container.")
    return LaunchPlan(f"native via {os.path.basename(tr)}", [tr, info.executable, *args],
                      notes=[f"CPU translation {info.arch} -> {HOST_ARCH}; expect slower performance."])


def _darling(info: AppInfo, args: list[str]) -> LaunchPlan:
    if HOST_OS == "macos":
        raise UnsupportedApp("Darling is for Linux. On a Mac, Mac apps run natively.", "Use --runner native.")
    if HOST_OS != "linux":
        raise UnsupportedApp("Darling only runs on Linux.")
    darling = which("darling")
    if not darling:
        raise UnsupportedApp(
            "This is a macOS app. On Linux, Mac apps can only run via Darling, which isn't installed.",
            "See https://www.darlinghq.org. Note: Darling mainly supports command-line Mac tools; "
            "most graphical Mac apps (Cocoa/Metal) won't run yet.", needs="darling")
    if info.archs and "x86_64" not in info.archs:
        raise UnsupportedApp(
            f"{os.path.basename(info.path)} is built only for {', '.join(info.archs)} (Apple Silicon).",
            "Darling can only run Intel (x86_64) Mac apps. Look for an Intel or Universal build.")
    if HOST_ARCH != "x86_64":
        raise UnsupportedApp("Darling only works on x86_64 Linux machines.")
    root = os.path.join(data_dir(), "prefixes", "darling", "default")
    notes = ["Darling is experimental: GUI apps are unlikely to work."]
    if info.path.endswith(".app"):
        notes.append("Graphical .app bundle detected: launching anyway, but expect failure.")
    guest = "/Volumes/SystemRoot" + info.executable   # Darling sees the host filesystem here
    return LaunchPlan("darling", [darling, "shell", guest, *args], {"DPREFIX": root}, notes, prefix=root)


# ---------------------------------------------------------------- macOS host

def _mac_native(info: AppInfo, args: list[str]) -> LaunchPlan:
    if info.platform != Platform.MACOS:
        raise UnsupportedApp(f"This is a {info.platform.value} app; it can't run natively on a Mac.",
                             "Use --runner container (Linux apps) or --runner wine (Windows apps).")
    archs = set(info.archs) or ({info.arch} - {"unknown"})
    notes = []
    if archs and HOST_ARCH not in archs:
        if HOST_ARCH == "arm64" and "x86_64" in archs:
            if not rosetta_installed():
                raise UnsupportedApp("This is an Intel Mac app and Rosetta 2 isn't installed.",
                                     "Run: omnilaunch setup rosetta", needs="rosetta")
            notes.append("Intel app: running through Rosetta 2.")
        else:
            raise UnsupportedApp(f"This Mac app is built for {', '.join(sorted(archs))}, "
                                 f"but this Mac is {HOST_ARCH}.", "Look for a Universal build.")

    if info.path.endswith(".app"):
        # Let macOS launch bundles itself: handles the Dock, Gatekeeper prompts and sandboxing.
        argv = ["/usr/bin/open", info.path] + (["--args", *args] if args else [])
        notes.append("If macOS says the app is damaged or from an unidentified developer, "
                     "right-click it in Finder and choose Open once.")
        return LaunchPlan("native (open)", argv, notes=notes, cwd=os.path.dirname(info.path))
    _require_exec(info)
    return LaunchPlan("native", [info.executable, *args], notes=notes)


def _container(info: AppInfo, args: list[str]) -> LaunchPlan:
    if info.platform not in (Platform.LINUX, Platform.SCRIPT, Platform.UNKNOWN):
        raise UnsupportedApp("Containers run Linux apps only.")
    engine = find_container_engine()
    if not engine:
        hint = {"macos": "Install OrbStack, Docker Desktop or Podman (e.g. `brew install --cask orbstack`), "
                         "then start it.",
                "windows": "Install WSL instead (run `wsl --install` in an admin terminal), "
                           "or Docker Desktop / Podman Desktop."}.get(HOST_OS, "Install docker or podman.")
        raise UnsupportedApp("Linux apps need a container engine on this machine, and none was found.", hint,
                             needs="" if HOST_OS == "windows" else "container")
    if HOST_OS != "windows":   # Windows filesystems have no executable flag
        _require_exec(info)
    folder = os.path.dirname(info.executable)
    # Windows paths (C:\\...) can't be used inside a Linux container, so mount the folder at /work there.
    guest_folder = "/work" if HOST_OS == "windows" else folder
    guest_exe = "/work/" + os.path.basename(info.executable) if HOST_OS == "windows" else info.executable
    image = os.environ.get("OMNILAUNCH_LINUX_IMAGE", DEFAULT_LINUX_IMAGE)
    argv = [engine, "run", "--rm", "-i"]
    if sys.stdin.isatty() and sys.stdout.isatty():
        argv.append("-t")
    arch = info.arch if info.arch in CONTAINER_PLATFORMS else HOST_ARCH
    argv += ["--platform", CONTAINER_PLATFORMS.get(arch, "linux/amd64"),
             "-v", f"{folder}:{guest_folder}", "-w", guest_folder]
    if info.kind == "AppImage":
        argv += ["-e", "APPIMAGE_EXTRACT_AND_RUN=1"]
    pre = []
    if info.gui:
        gui_argv, image, pre = _container_display(engine)
        argv += gui_argv
    notes = [f"Running in a {image} container ({os.path.basename(engine)}); the app sees only its own folder."]
    if info.gui:
        notes.append("Graphical app: its windows are shown on this computer's screen.")
    if HOST_OS != "linux" and info.arch not in ("unknown", HOST_ARCH) and info.arch in CONTAINER_PLATFORMS:
        notes.append(f"{info.arch} Linux app on an {HOST_ARCH} machine: emulated, so slower.")
    argv += [image, guest_exe, *args]
    return LaunchPlan(f"container ({os.path.basename(engine)})", argv, notes=notes, pre=pre)


def _container_display(engine: str):
    """Extra container arguments, image and pre-launch steps so a Linux GUI app can show windows."""
    image = LINUX_GUI_IMAGE if linux_gui_image_ready() else os.environ.get(
        "OMNILAUNCH_LINUX_IMAGE", DEFAULT_LINUX_IMAGE)
    if HOST_OS == "macos":
        if not xquartz_installed():
            raise UnsupportedApp("This Linux app has windows, and showing them on a Mac needs XQuartz.",
                                 "Run: omnilaunch setup linux-gui", needs="linux-gui")
        if image != LINUX_GUI_IMAGE:
            raise UnsupportedApp("Graphical Linux apps need OmniLaunch's Linux GUI image, which isn't built yet.",
                                 "Run: omnilaunch setup linux-gui", needs="linux-gui")
        host = "host.containers.internal" if os.path.basename(engine).startswith("podman") else "host.docker.internal"
        xhost = "/opt/X11/bin/xhost" if os.path.exists("/opt/X11/bin/xhost") else "xhost"
        pre = [(["/usr/bin/open", "-a", "XQuartz"], False),
               ([xhost, "+localhost"], False), ([xhost, "+127.0.0.1"], False)]
        return ["-e", f"DISPLAY={host}:0"], image, pre
    if HOST_OS == "linux":
        display = os.environ.get("DISPLAY", ":0")
        pre = [(["xhost", "+local:"], False)] if which("xhost") else []   # let local containers draw windows
        return ["-e", f"DISPLAY={display}", "-v", "/tmp/.X11-unix:/tmp/.X11-unix"], image, pre
    raise UnsupportedApp("Graphical Linux apps on Windows run through WSL, not a container.", "Use --runner wsl.")


def _vm(info: AppInfo, args: list[str]) -> LaunchPlan:
    if info.platform not in (Platform.WINDOWS, Platform.UNKNOWN):
        raise UnsupportedApp("Virtual machines are only set up for Windows apps so far.")
    if HOST_OS == "windows":
        raise UnsupportedApp("This is Windows already: Windows apps run natively.", "Use --runner native.")
    if "DLL" in info.kind:
        raise UnsupportedApp(f"{os.path.basename(info.path)} is a Windows library (DLL), not an app.")
    from . import vms
    return vms.plan_windows_app(info, args)


# ---------------------------------------------------------------- Mac disk images and installers

def _mac_package(info: AppInfo) -> LaunchPlan:
    name = os.path.basename(info.path)
    if HOST_OS != "macos":
        where = "Windows" if HOST_OS == "windows" else "Linux"
        raise UnsupportedApp(
            f"{name} is a Mac app installer, and Mac apps can't be installed on {where}.",
            "Look for the " + where + " version of this app on the developer's website. (Nothing can run Mac "
            "apps on " + where + ", and Apple's licence only allows macOS on Apple hardware.)")
    if info.kind == "PKG":
        return LaunchPlan("install (macOS Installer)", ["/usr/bin/open", "-W", info.path],
                          notes=["Opening Apple's Installer for this package."])
    lp = LaunchPlan("install (disk image)", ["/usr/bin/true"],
                    notes=["Copies the app from the disk image into Applications, then opens it."])

    def install_from_dmg() -> bool:
        """mount the disk image, copy its app into ~/Applications, then unmount it"""
        import tempfile
        mount = tempfile.mkdtemp(prefix="omnilaunch-dmg-")
        # Some disk images show a licence; answering Y accepts it, as double-clicking would ask you to.
        r = subprocess.run(["/usr/bin/hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen",
                            "-mountpoint", mount, info.path], input=b"Y\n", capture_output=True)
        if r.returncode != 0:
            return False
        try:
            items = sorted(os.listdir(mount))
            apps = [i for i in items if i.endswith(".app")]
            pkgs = [i for i in items if i.endswith((".pkg", ".mpkg"))]
            if apps:
                dest_dir = "/Applications" if os.access("/Applications", os.W_OK) else os.path.expanduser("~/Applications")
                os.makedirs(dest_dir, exist_ok=True)
                dest = os.path.join(dest_dir, apps[0])
                if os.path.exists(dest):
                    shutil.rmtree(dest)
                subprocess.run(["/usr/bin/ditto", os.path.join(mount, apps[0]), dest], check=True)
                lp.argv = ["/usr/bin/open", dest]
                lp.notes.append(f"Installed {apps[0]} into {dest_dir}.")
                return True
            if pkgs:
                import tempfile as _t
                copy = os.path.join(_t.mkdtemp(prefix="omnilaunch-pkg-"), pkgs[0])
                subprocess.run(["/usr/bin/ditto", os.path.join(mount, pkgs[0]), copy], check=True)
                lp.argv = ["/usr/bin/open", "-W", copy]
                return True
            return False
        finally:
            subprocess.run(["/usr/bin/hdiutil", "detach", mount, "-quiet"], check=False)

    lp.pre = [(install_from_dmg, True)]
    return lp


# ---------------------------------------------------------------- Windows host

def _windows_native(info: AppInfo, args: list[str]) -> LaunchPlan:
    if info.platform == Platform.MACOS:
        raise UnsupportedApp(
            "Mac apps can't run on Windows: no compatibility layer for them exists.",
            "Look for the Windows version of this app. (Apple's licence only allows macOS on Apple hardware, "
            "so a macOS virtual machine isn't an option on a PC.)")
    if info.platform in (Platform.LINUX, Platform.SCRIPT):
        raise UnsupportedApp("Linux apps can't run natively on Windows.", "Use --runner wsl.")
    notes = []
    if info.arch == "arm64" and HOST_ARCH != "arm64":
        raise UnsupportedApp("This Windows app is built for ARM PCs only, and this PC is x86_64.",
                             "Look for the x64 version.")
    if HOST_ARCH == "arm64" and info.arch in ("x86", "x86_64"):
        notes.append("x86 app on a Windows ARM PC: Windows emulates it automatically, so it may be slower.")
    ext = os.path.splitext(info.executable.lower())[1]
    if ext == ".msi":
        argv = ["msiexec", "/i", info.executable, *args]
    elif ext in (".bat", ".cmd"):
        argv = ["cmd", "/c", info.executable, *args]
    elif ext == ".lnk":
        argv = ["cmd", "/c", "start", "", info.executable, *args]
    else:
        argv = [info.executable, *args]
    return LaunchPlan("native", argv, notes=notes)


def _wsl(info: AppInfo, args: list[str]) -> LaunchPlan:
    if HOST_OS != "windows":
        raise UnsupportedApp("WSL is only available on Windows.")
    if info.platform not in (Platform.LINUX, Platform.SCRIPT, Platform.UNKNOWN):
        raise UnsupportedApp("WSL runs Linux apps only.")
    wsl = find_wsl()
    if not wsl or not wsl_distros():
        raise UnsupportedApp("Linux apps on Windows need WSL with a Linux distribution, which isn't set up.",
                             "Run: omnilaunch setup wsl (or `wsl --install`), then restart.", needs="wsl")
    guest_exe = to_wsl_path(info.executable)
    guest_dir = to_wsl_path(os.path.dirname(info.executable))
    notes = ["Running in WSL. On Windows 11, graphical Linux apps open in their own windows (WSLg)."]
    if HOST_ARCH == "arm64" and info.arch in ("x86_64", "x86"):
        notes.append("x86 Linux app on a Windows ARM PC: WSL needs qemu-user/binfmt set up inside Linux.")
    if info.kind == "AppImage":
        notes.append("AppImage: extracting instead of mounting (WSL usually has no FUSE).")
        return LaunchPlan("wsl", [wsl, "--cd", guest_dir, "--exec", "env", "APPIMAGE_EXTRACT_AND_RUN=1",
                                  guest_exe, *args], notes=notes)
    if info.platform == Platform.SCRIPT and info.interpreter:
        # Files on C: may lack the executable bit inside WSL, so call the interpreter explicitly.
        return LaunchPlan("wsl", [wsl, "--cd", guest_dir, "--exec", *shlex.split(info.interpreter),
                                  guest_exe, *args], notes=notes)
    return LaunchPlan("wsl", [wsl, "--cd", guest_dir, "--exec", guest_exe, *args], notes=notes)


# ---------------------------------------------------------------- Windows apps on Linux/macOS

def _windows(info: AppInfo, args: list[str], runner: str, verbose: bool,
             env_name: str | None = None) -> LaunchPlan:
    if info.platform not in (Platform.WINDOWS, Platform.UNKNOWN):
        raise UnsupportedApp(f"Wine and Proton run Windows apps, but this is a {info.platform.value} app.")
    if HOST_OS == "windows":
        raise UnsupportedApp("This is Windows already: Windows apps run natively.", "Use --runner native.")
    if "DLL" in info.kind:
        raise UnsupportedApp(f"{os.path.basename(info.path)} is a Windows library (DLL), not an app.",
                             "Run the .exe that uses it instead.")
    root = os.path.join(data_dir(), "prefixes", *(env_name.split("/") if env_name else (runner, app_id(info))))
    notes = [f"Isolated Windows environment: {root}"]
    if not os.path.isdir(os.path.join(root, "drive_c")) and not os.path.isdir(os.path.join(root, "pfx")):
        notes.append("First launch: setting up the Windows environment can take a minute.")
    if HOST_OS == "linux" and HOST_ARCH not in ("x86_64", "x86") and info.arch in ("x86_64", "x86"):
        notes.append("x86 Windows app on an ARM host: needs Wine built with box64/FEX (e.g. via Hangover).")
    ext = os.path.splitext(info.executable.lower())[1]

    special = {".msi": ["msiexec", "/i"], ".bat": ["cmd", "/c"], ".cmd": ["cmd", "/c"], ".lnk": ["start", "/unix"]}
    if runner == "proton":
        if HOST_OS != "linux":
            raise UnsupportedApp("Proton only runs on Linux.", "Use --runner wine.")
        proton = os.environ.get("OMNILAUNCH_PROTON")
        umu = find_umu()
        env = {} if verbose else {"WINEDEBUG": "-all"}
        if umu:
            # umu-launcher runs Proton inside Valve's Steam Linux Runtime, exactly as Steam would,
            # downloading Proton itself the first time. This is what makes most games just work.
            env.update({"WINEPREFIX": root, "GAMEID": os.environ.get("OMNILAUNCH_GAMEID", "0"),
                        "WINEDLLOVERRIDES": "winemenubuilder.exe=d"})
            if proton and os.path.exists(proton):
                env["PROTONPATH"] = os.path.dirname(os.path.abspath(proton))
            notes.append("Running through Proton (umu-launcher). The first launch downloads Proton and "
                         "the Steam runtime, which takes a few minutes.")
            return LaunchPlan("proton (umu)", [umu, *special.get(ext, []), info.executable,
                                               *[host_path_for_windows(a) for a in args]],
                              env, notes, prefix=root)
        if not proton or not os.path.exists(proton):
            raise UnsupportedApp("Proton (the best way to run Windows games on Linux) isn't installed.",
                                 "Run: omnilaunch setup proton", needs="proton")
        env.update({"STEAM_COMPAT_DATA_PATH": root,
                    "STEAM_COMPAT_CLIENT_INSTALL_PATH": os.path.expanduser("~/.steam/steam")})
        return LaunchPlan("proton", [proton, "run", *special.get(ext, []), info.executable, *args],
                          env, notes, prefix=root)

    wine = find_wine()
    if not wine:
        from . import vms
        hint = _wine_hint() + (" Or use --runner vm." if vms.get("windows") else "")
        raise UnsupportedApp("This is a Windows app and Wine isn't installed.", hint, needs="wine")
    if HOST_OS == "macos" and HOST_ARCH == "arm64" and not rosetta_installed():
        notes.append("Wine on Apple Silicon needs Rosetta 2: softwareupdate --install-rosetta --agree-to-license")
    # Wine's own menu-entry maker is off: OmniLaunch adds installed programs to the app menu itself.
    env = {"WINEPREFIX": root, "WINEDLLOVERRIDES": "winemenubuilder.exe=d"}
    if not verbose:
        env["WINEDEBUG"] = "-all"

    argv = [wine, *special.get(ext, []), info.executable, *[host_path_for_windows(a) for a in args]]
    label = f"wine ({ext.lstrip('.')})" if ext in special else "wine"
    return LaunchPlan(label, argv, env, notes, prefix=root)


def _wine_hint() -> str:
    if HOST_OS == "macos":
        return ("Download 'Wine Stable' from https://github.com/Gcenx/macOS_Wine_builds/releases and drag it "
                "to Applications (or use MacPorts). Homebrew's Wine casks were disabled in September 2026.")
    for mgr, cmd in (("apt", "sudo apt install"), ("dnf", "sudo dnf install"),
                     ("pacman", "sudo pacman -S"), ("zypper", "sudo zypper install")):
        if which(mgr):
            return f"Install it with: {cmd} wine"
    return "Install 'wine' with your system package manager."


# ---------------------------------------------------------------- Windows apps without Rosetta (Box64)

BOX64_IMAGE = "omnilaunch/wine-box64"
ROSETTA_LIMITED_FROM = 28   # Apple: from macOS 28, Rosetta only serves certain older games


def mac_major() -> int:
    """macOS major version (e.g. 27), or 0 if not a Mac / unknown."""
    if HOST_OS != "macos":
        return 0
    try:
        out = subprocess.run(["/usr/bin/sw_vers", "-productVersion"], capture_output=True, text=True,
                             timeout=10).stdout.strip()
        return int(out.split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        try:
            return int(host_platform.mac_ver()[0].split(".")[0])
        except ValueError:
            return 0


def rosetta_era_over() -> bool:
    """True on Apple Silicon Macs where Wine can no longer rely on Rosetta (macOS 28+)."""
    return HOST_OS == "macos" and HOST_ARCH == "arm64" and mac_major() >= ROSETTA_LIMITED_FROM


def box64_image_ready() -> bool:
    engine = find_container_engine()
    if not engine:
        return False
    try:
        return subprocess.run([engine, "image", "inspect", BOX64_IMAGE], capture_output=True,
                              timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _box64(info: AppInfo, args: list, verbose: bool, env_name: str | None) -> LaunchPlan:
    """Run an Intel Windows app on an ARM computer without Rosetta: an ARM64 Linux container
    (the hidden VM Docker/Podman/OrbStack already run) where Box64 translates Intel code and Wine
    provides Windows. The app's environment lives on this computer, like every other one."""
    if info.platform not in (Platform.WINDOWS, Platform.UNKNOWN):
        raise UnsupportedApp(f"Box64 + Wine runs Windows apps, but this is a {info.platform.value} app.")
    if HOST_OS == "windows":
        raise UnsupportedApp("This is Windows already: Windows apps run natively.", "Use --runner native.")
    if "DLL" in info.kind:
        raise UnsupportedApp(f"{os.path.basename(info.path)} is a Windows library (DLL), not an app.",
                             "Run the .exe that uses it instead.")
    engine = find_container_engine()
    if not engine or not box64_image_ready():
        raise UnsupportedApp("Running Windows apps without Rosetta needs OmniLaunch's Box64 + Wine "
                             "environment, which isn't set up yet.", "Run: omnilaunch setup box64", needs="box64")
    root = os.path.join(data_dir(), "prefixes", *(env_name.split("/") if env_name else ("box64", app_id(info))))
    os.makedirs(root, exist_ok=True)
    home = os.path.expanduser("~")
    mounts = [home]
    for path in (os.path.dirname(info.executable), root):
        if not (path == home or path.startswith(home + os.sep)) and path not in mounts:
            mounts.append(path)
    argv = [engine, "run", "--rm", "-i", "--platform", "linux/arm64"]
    if sys.stdin.isatty() and sys.stdout.isatty():
        argv.append("-t")
    if hasattr(os, "getuid"):
        argv += ["--user", f"{os.getuid()}:{os.getgid()}"]   # Wine insists the prefix belongs to its user
    for m in mounts:
        argv += ["-v", f"{m}:{m}"]
    argv += ["-e", f"HOME={home}", "-e", f"WINEPREFIX={root}", "-e", "WINEDLLOVERRIDES=winemenubuilder.exe=d",
             "-e", "WINEDEBUG=" + ("" if verbose else "-all"), "-e", "BOX64_LOG=" + ("1" if verbose else "0"),
             "-w", os.path.dirname(info.executable)]
    pre: list = []
    if HOST_OS == "macos":
        if not xquartz_installed():
            raise UnsupportedApp("Windows apps need XQuartz to show their windows when running without Rosetta.",
                                 "Run: omnilaunch setup box64", needs="box64")
        host = "host.containers.internal" if os.path.basename(engine).startswith("podman") else "host.docker.internal"
        xhost = "/opt/X11/bin/xhost" if os.path.exists("/opt/X11/bin/xhost") else "xhost"
        pre = [(["/usr/bin/open", "-a", "XQuartz"], False), ([xhost, "+localhost"], False),
               ([xhost, "+127.0.0.1"], False)]
        argv += ["-e", f"DISPLAY={host}:0"]
    elif os.environ.get("DISPLAY"):
        argv += ["-e", f"DISPLAY={os.environ['DISPLAY']}", "-v", "/tmp/.X11-unix:/tmp/.X11-unix"]
    ext = os.path.splitext(info.executable.lower())[1]
    special = {".msi": ["msiexec", "/i"], ".bat": ["cmd", "/c"], ".cmd": ["cmd", "/c"], ".lnk": ["start", "/unix"]}
    # Wait for every Windows process in the environment (installers often hand off to a child and exit).
    script = 'box64 /opt/wine/bin/wine "$@"; s=$?; box64 /opt/wine/bin/wineserver -w; exit $s'
    argv += [BOX64_IMAGE, "sh", "-c", script, "omnilaunch", *special.get(ext, []), info.executable,
             *[host_path_for_windows(a) for a in args]]
    notes = [f"Isolated Windows environment: {root}",
             "Running through Box64 + Wine in a Linux VM (no Rosetta needed)."]
    if not os.path.isdir(os.path.join(root, "drive_c")):
        notes.append("First launch: setting up the Windows environment takes a few minutes.")
    label = f"box64 ({ext.lstrip('.')})" if ext in special else "box64"
    return LaunchPlan(label, argv, {}, notes, prefix=root, pre=pre)
