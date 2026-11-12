"""Turn a detected app into the command that runs it on this machine (Linux or macOS host)."""

from __future__ import annotations

import glob
import hashlib
import os
import platform as host_platform
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

from .detect import AppInfo, Platform

RUNNERS = ("native", "wine", "proton", "darling", "container")

HOST_OS = {"darwin": "macos", "linux": "linux"}.get(sys.platform, sys.platform)
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
    return os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "omnilaunch")


class UnsupportedApp(Exception):
    """Raised with a human-friendly explanation and a fix, when we can't run something."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


@dataclass
class LaunchPlan:
    runner: str
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    cwd: str | None = None
    prefix: str | None = None   # isolated environment directory, created before launch


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


def find_container_engine() -> str | None:
    return which("docker", "podman")


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
    base = os.path.splitext(os.path.basename(info.path.rstrip("/")))[0] or "app"
    base = "".join(c if c.isalnum() or c in "-_." else "_" for c in base)
    digest = hashlib.sha1(info.path.encode()).hexdigest()[:8]
    return f"{base}-{digest}"


def default_runner(info: AppInfo) -> str:
    if info.platform == Platform.WINDOWS:
        return "proton" if os.environ.get("OMNILAUNCH_PROTON") and HOST_OS == "linux" else "wine"
    if info.platform == Platform.SCRIPT:
        return "native"
    if HOST_OS == "macos":
        return "native" if info.platform == Platform.MACOS else "container"
    return "darling" if info.platform == Platform.MACOS else "native"


def plan(info: AppInfo, args: list[str] | None = None, runner: str | None = None,
         verbose: bool = False) -> LaunchPlan:
    """Build a launch plan. `runner` forces a specific layer instead of auto-picking."""
    args = list(args or [])
    if runner and runner not in RUNNERS:
        raise UnsupportedApp(f"Unknown runner '{runner}'.", f"Choose one of: {', '.join(RUNNERS)}")
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
        else:
            lp = _linux_native(info, args)
    elif target in ("wine", "proton"):
        lp = _windows(info, args, target, verbose)
    elif target == "container":
        lp = _container(info, args)
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
    darling = which("darling")
    if not darling:
        raise UnsupportedApp(
            "This is a macOS app. On Linux, Mac apps can only run via Darling, which isn't installed.",
            "See https://www.darlinghq.org. Note: Darling mainly supports command-line Mac tools; "
            "most graphical Mac apps (Cocoa/Metal) won't run yet.")
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
                                     "Run: softwareupdate --install-rosetta --agree-to-license")
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
        hint = ("Install Docker Desktop, OrbStack or Podman (e.g. `brew install --cask orbstack`), "
                "then start it." if HOST_OS == "macos" else "Install docker or podman.")
        raise UnsupportedApp("Linux apps need a container engine on this machine, and none was found.", hint)
    _require_exec(info)
    folder = os.path.dirname(info.executable)
    image = os.environ.get("OMNILAUNCH_LINUX_IMAGE", DEFAULT_LINUX_IMAGE)
    argv = [engine, "run", "--rm", "-i"]
    if sys.stdin.isatty() and sys.stdout.isatty():
        argv.append("-t")
    arch = info.arch if info.arch in CONTAINER_PLATFORMS else HOST_ARCH
    argv += ["--platform", CONTAINER_PLATFORMS.get(arch, "linux/amd64"),
             "-v", f"{folder}:{folder}", "-w", folder]
    notes = [f"Running in a {image} container ({os.path.basename(engine)}); the app sees only its own folder.",
             "Command-line Linux apps only: graphical Linux apps need an X11/Wayland setup."]
    if info.kind == "AppImage":
        argv += ["-e", "APPIMAGE_EXTRACT_AND_RUN=1"]
    if HOST_OS == "macos" and info.arch not in ("unknown", HOST_ARCH) and info.arch in CONTAINER_PLATFORMS:
        notes.append(f"{info.arch} Linux app on an {HOST_ARCH} Mac: emulated, so slower.")
    argv += [image, info.executable, *args]
    return LaunchPlan(f"container ({os.path.basename(engine)})", argv, notes=notes)


# ---------------------------------------------------------------- Windows apps (either host)

def _windows(info: AppInfo, args: list[str], runner: str, verbose: bool) -> LaunchPlan:
    if info.platform not in (Platform.WINDOWS, Platform.UNKNOWN):
        raise UnsupportedApp(f"Wine and Proton run Windows apps, but this is a {info.platform.value} app.")
    if "DLL" in info.kind:
        raise UnsupportedApp(f"{os.path.basename(info.path)} is a Windows library (DLL), not an app.",
                             "Run the .exe that uses it instead.")
    root = os.path.join(data_dir(), "prefixes", runner, app_id(info))
    notes = [f"Isolated Windows environment: {root}"]
    if not os.path.isdir(os.path.join(root, "drive_c")) and not os.path.isdir(os.path.join(root, "pfx")):
        notes.append("First launch: setting up the Windows environment can take a minute.")
    if HOST_OS == "linux" and HOST_ARCH not in ("x86_64", "x86") and info.arch in ("x86_64", "x86"):
        notes.append("x86 Windows app on an ARM host: needs Wine built with box64/FEX (e.g. via Hangover).")
    ext = os.path.splitext(info.executable.lower())[1]

    if runner == "proton":
        if HOST_OS != "linux":
            raise UnsupportedApp("Proton only runs on Linux.", "Use --runner wine.")
        proton = os.environ.get("OMNILAUNCH_PROTON")
        if not proton or not os.path.exists(proton):
            raise UnsupportedApp("Proton isn't configured.",
                                 "Set OMNILAUNCH_PROTON=/path/to/proton (the 'proton' script in a Proton folder).")
        env = {"STEAM_COMPAT_DATA_PATH": root,
               "STEAM_COMPAT_CLIENT_INSTALL_PATH": os.path.expanduser("~/.steam/steam")}
        if not verbose:
            env["WINEDEBUG"] = "-all"
        return LaunchPlan("proton", [proton, "run", info.executable, *args], env, notes, prefix=root)

    wine = find_wine()
    if not wine:
        raise UnsupportedApp("This is a Windows app and Wine isn't installed.", _wine_hint())
    if HOST_OS == "macos" and HOST_ARCH == "arm64" and not rosetta_installed():
        notes.append("Wine on Apple Silicon needs Rosetta 2: softwareupdate --install-rosetta --agree-to-license")
    env = {"WINEPREFIX": root}
    if not verbose:
        env["WINEDEBUG"] = "-all"

    special = {".msi": ["msiexec", "/i"], ".bat": ["cmd", "/c"], ".cmd": ["cmd", "/c"], ".lnk": ["start", "/unix"]}
    argv = [wine, *special.get(ext, []), info.executable, *args]
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
