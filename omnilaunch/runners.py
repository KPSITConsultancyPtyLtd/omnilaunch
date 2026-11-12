"""Turn a detected app into the command that runs it on this machine."""

from __future__ import annotations

import hashlib
import os
import platform as host_platform
import shlex
import shutil
from dataclasses import dataclass, field

from .detect import AppInfo, Platform

RUNNERS = ("native", "wine", "proton", "darling")


def data_dir() -> str:
    return os.path.expanduser(os.environ.get("OMNILAUNCH_HOME", "~/.local/share/omnilaunch"))


HOST_ARCH = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64", "arm64": "arm64",
             "i686": "x86", "i386": "x86", "armv7l": "arm", "riscv64": "riscv"}.get(
    host_platform.machine().lower(), host_platform.machine().lower())

# CPU architectures a host can run without translation.
NATIVE_COMPAT = {"x86_64": {"x86_64", "x86"}, "arm64": {"arm64"}, "x86": {"x86"},
                 "arm": {"arm"}, "riscv": {"riscv"}}

# Translators that run a foreign-CPU Linux binary on this host.
ARCH_TRANSLATORS = {"x86_64": ["box64", "FEXInterpreter", "qemu-x86_64"],
                    "x86": ["box86", "FEXInterpreter", "qemu-i386"],
                    "arm64": ["qemu-aarch64"],
                    "arm": ["qemu-arm"],
                    "riscv": ["qemu-riscv64"]}

ARCHIVE_HINTS = {
    "DMG": "Mac disk image. Extract it first (e.g. `7z x file.dmg`), then run the .app inside.",
    "PKG": "Mac installer package. Extract it with `7z x file.pkg` or xar; installers rarely work outside macOS.",
}


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


def app_id(info: AppInfo) -> str:
    """Stable per-app id, so each app gets its own isolated environment."""
    base = os.path.splitext(os.path.basename(info.path.rstrip("/")))[0] or "app"
    base = "".join(c if c.isalnum() or c in "-_." else "_" for c in base)
    digest = hashlib.sha1(info.path.encode()).hexdigest()[:8]
    return f"{base}-{digest}"


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

    target = runner or {Platform.LINUX: "native", Platform.SCRIPT: "native",
                        Platform.WINDOWS: "wine", Platform.MACOS: "darling"}[info.platform]
    if target == "wine" and not runner and os.environ.get("OMNILAUNCH_PROTON"):
        target = "proton"

    if target == "native":
        lp = _script(info, args) if info.platform == Platform.SCRIPT else _linux(info, args)
    elif target in ("wine", "proton"):
        lp = _windows(info, args, target, verbose)
    else:
        lp = _macos(info, args)
    if lp.cwd is None:
        lp.cwd = os.path.dirname(info.executable)
    return lp


def _require_exec(info: AppInfo) -> None:
    if not os.access(info.executable, os.X_OK):
        raise UnsupportedApp(f"{os.path.basename(info.executable)} isn't marked as executable.",
                             f"Run: chmod +x {shlex.quote(info.executable)}")


def _linux(info: AppInfo, args: list[str]) -> LaunchPlan:
    if info.arch == "unknown" or info.arch in NATIVE_COMPAT.get(HOST_ARCH, {HOST_ARCH}):
        _require_exec(info)
        return LaunchPlan("native", [info.executable, *args])
    tr = which(*ARCH_TRANSLATORS.get(info.arch, []))
    if not tr:
        raise UnsupportedApp(
            f"{os.path.basename(info.path)} is built for {info.arch}, but this machine is {HOST_ARCH}.",
            "Install box64/FEX-Emu (x86 apps on ARM) or qemu-user to translate it.")
    return LaunchPlan(f"native via {os.path.basename(tr)}", [tr, info.executable, *args],
                      notes=[f"CPU translation {info.arch} -> {HOST_ARCH}; expect slower performance."])


def _script(info: AppInfo, args: list[str]) -> LaunchPlan:
    if os.access(info.executable, os.X_OK):
        return LaunchPlan("native", [info.executable, *args])
    # Not executable: call the interpreter from the shebang directly.
    interp = shlex.split(info.interpreter) if info.interpreter else []
    if not interp:
        raise UnsupportedApp("Script has an empty #! line.")
    return LaunchPlan("native (via interpreter)", [*interp, info.executable, *args])


def _windows(info: AppInfo, args: list[str], runner: str, verbose: bool) -> LaunchPlan:
    if "DLL" in info.kind:
        raise UnsupportedApp(f"{os.path.basename(info.path)} is a Windows library (DLL), not an app.",
                             "Run the .exe that uses it instead.")
    root = os.path.join(data_dir(), "prefixes", runner, app_id(info))
    notes = [f"Isolated Windows environment: {root}"]
    if not os.path.isdir(root):
        notes.append("First launch: setting up the Windows environment can take a minute.")
    if HOST_ARCH not in ("x86_64", "x86") and info.arch in ("x86_64", "x86"):
        notes.append("x86 Windows app on an ARM host: needs Wine built with box64/FEX (e.g. via Hangover).")
    ext = os.path.splitext(info.executable.lower())[1]

    if runner == "proton":
        proton = os.environ.get("OMNILAUNCH_PROTON")
        if not proton or not os.path.exists(proton):
            raise UnsupportedApp("Proton isn't configured.",
                                 "Set OMNILAUNCH_PROTON=/path/to/proton (the 'proton' script in a Proton folder).")
        env = {"STEAM_COMPAT_DATA_PATH": root,
               "STEAM_COMPAT_CLIENT_INSTALL_PATH": os.path.expanduser("~/.steam/steam")}
        if not verbose:
            env["WINEDEBUG"] = "-all"
        return LaunchPlan("proton", [proton, "run", info.executable, *args], env, notes, prefix=root)

    wine = which("wine", "wine64") if info.arch != "x86_64" else which("wine64", "wine")
    if not wine:
        raise UnsupportedApp("This is a Windows app and Wine isn't installed.", _install_hint("wine"))
    env = {"WINEPREFIX": root}
    if not verbose:
        env["WINEDEBUG"] = "-all"

    if ext == ".msi":
        argv = [wine, "msiexec", "/i", info.executable, *args]
    elif ext in (".bat", ".cmd"):
        argv = [wine, "cmd", "/c", info.executable, *args]
    elif ext == ".lnk":
        argv = [wine, "start", "/unix", info.executable, *args]
    else:
        argv = [wine, info.executable, *args]
    return LaunchPlan(f"wine{' (' + ext.lstrip('.') + ')' if ext in ('.msi', '.bat', '.cmd', '.lnk') else ''}",
                      argv, env, notes, prefix=root)


def _macos(info: AppInfo, args: list[str]) -> LaunchPlan:
    darling = which("darling")
    if not darling:
        raise UnsupportedApp(
            "This is a macOS app. Mac apps can only run via Darling, which isn't installed.",
            "See https://www.darlinghq.org. Note: Darling mainly supports command-line Mac tools; "
            "most graphical Mac apps (Cocoa/Metal) won't run yet.")
    # Darling only runs Intel (x86_64) Mac binaries.
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
    # Darling sees the host filesystem under /Volumes/SystemRoot.
    guest = "/Volumes/SystemRoot" + info.executable
    return LaunchPlan("darling", [darling, "shell", guest, *args], {"DPREFIX": root}, notes, prefix=root)


def _install_hint(pkg: str) -> str:
    for mgr, cmd in (("apt", "sudo apt install"), ("dnf", "sudo dnf install"),
                     ("pacman", "sudo pacman -S"), ("zypper", "sudo zypper install")):
        if which(mgr):
            return f"Install it with: {cmd} {pkg}"
    return f"Install '{pkg}' with your system package manager."
