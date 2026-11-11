"""Turn a detected app into the command that runs it on this machine."""

from __future__ import annotations

import hashlib
import os
import platform as host_platform
import shutil
from dataclasses import dataclass, field

from .detect import AppInfo, Platform

DATA_DIR = os.path.expanduser(os.environ.get("OMNILAUNCH_HOME", "~/.local/share/omnilaunch"))

HOST_ARCH = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64", "arm64": "arm64",
             "i686": "x86", "i386": "x86", "armv7l": "arm", "riscv64": "riscv"}.get(
    host_platform.machine().lower(), host_platform.machine().lower())

# Translators that run a foreign-CPU Linux binary on this host.
ARCH_TRANSLATORS = {"x86_64": ["box64", "FEXInterpreter", "qemu-x86_64"],
                    "x86": ["box86", "qemu-i386"],
                    "arm64": ["qemu-aarch64"]}


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


def which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def app_id(info: AppInfo) -> str:
    """Stable per-app id, so each app gets its own isolated prefix."""
    base = os.path.splitext(os.path.basename(info.path.rstrip("/")))[0] or "app"
    digest = hashlib.sha1(info.path.encode()).hexdigest()[:8]
    return f"{base}-{digest}"


def plan(info: AppInfo, args: list[str] | None = None) -> LaunchPlan:
    args = args or []
    if info.platform == Platform.LINUX:
        return _linux(info, args)
    if info.platform == Platform.WINDOWS:
        return _windows(info, args)
    if info.platform == Platform.MACOS:
        return _macos(info, args)
    if info.platform == Platform.SCRIPT:
        return LaunchPlan("script", [info.executable, *args])
    raise UnsupportedApp(f"Don't know how to run {info.path} ({info.kind}).")


def _linux(info: AppInfo, args: list[str]) -> LaunchPlan:
    if info.arch in (HOST_ARCH, "unknown"):
        return LaunchPlan("native", [info.executable, *args])
    tr = which(*ARCH_TRANSLATORS.get(info.arch, []))
    if not tr:
        raise UnsupportedApp(
            f"{os.path.basename(info.path)} is built for {info.arch}, but this machine is {HOST_ARCH}.",
            "Install box64/FEX-Emu (x86 apps on ARM) or qemu-user to translate it.")
    return LaunchPlan(f"native via {os.path.basename(tr)}", [tr, info.executable, *args],
                      notes=[f"CPU translation {info.arch} -> {HOST_ARCH}; expect slower performance."])


def _windows(info: AppInfo, args: list[str]) -> LaunchPlan:
    prefix = os.path.join(DATA_DIR, "prefixes", "windows", app_id(info))
    env = {"WINEPREFIX": prefix, "WINEDEBUG": "-all"}
    notes = [f"Isolated Windows environment: {prefix}"]

    if info.executable.lower().endswith(".msi"):
        wine = which("wine")
        if not wine:
            raise UnsupportedApp("Windows installers need Wine.", _install_hint("wine"))
        return LaunchPlan("wine (msiexec)", [wine, "msiexec", "/i", info.executable, *args], env, notes)

    # Prefer Proton (better game/DirectX support) when the user points us at it.
    proton = os.environ.get("OMNILAUNCH_PROTON")
    if proton and os.path.exists(proton):
        env.update({"STEAM_COMPAT_DATA_PATH": prefix,
                    "STEAM_COMPAT_CLIENT_INSTALL_PATH": os.path.expanduser("~/.steam/steam")})
        return LaunchPlan("proton", [proton, "run", info.executable, *args], env, notes)

    wine = which("wine64", "wine") if info.arch == "x86_64" else which("wine", "wine64")
    if not wine:
        raise UnsupportedApp("This is a Windows app and Wine isn't installed.", _install_hint("wine"))
    if info.arch == "arm64" and HOST_ARCH != "arm64":
        notes.append("Windows-on-ARM binary on a non-ARM host: Wine support is limited.")
    return LaunchPlan("wine", [wine, info.executable, *args], env, notes)


def _macos(info: AppInfo, args: list[str]) -> LaunchPlan:
    darling = which("darling")
    if not darling:
        raise UnsupportedApp(
            "This is a macOS app. Mac apps can only run via Darling, which isn't installed.",
            "See https://www.darlinghq.org. Note: Darling mainly supports command-line Mac tools; "
            "most graphical Mac apps (Cocoa/Metal) won't run yet.")
    prefix = os.path.join(DATA_DIR, "prefixes", "macos", "default")
    notes = ["Darling is experimental: GUI apps are unlikely to work."]
    if info.path.endswith(".app"):
        notes.append("Graphical .app bundle detected: launching anyway, but expect failure.")
    # Darling sees the host filesystem under /Volumes/SystemRoot.
    guest_path = "/Volumes/SystemRoot" + info.executable
    return LaunchPlan("darling", [darling, "shell", guest_path, *args], {"DPREFIX": prefix}, notes)


def _install_hint(pkg: str) -> str:
    if which("apt"):
        return f"Install it with: sudo apt install {pkg}"
    if which("dnf"):
        return f"Install it with: sudo dnf install {pkg}"
    if which("pacman"):
        return f"Install it with: sudo pacman -S {pkg}"
    if which("brew"):
        return f"Install it with: brew install --cask {pkg}-stable"
    return f"Install '{pkg}' with your system package manager."
