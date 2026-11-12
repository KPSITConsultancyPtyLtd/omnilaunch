"""Install the helpers OmniLaunch needs (Wine, WSL, containers, ...) so users don't have to.

Each component knows whether it's installed and how to install it on this kind of computer.
`omnilaunch run` offers to install a missing component the first time an app needs it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from . import runners

LINUX_GUI_IMAGE = runners.LINUX_GUI_IMAGE
# Common libraries graphical Linux apps expect, so most of them start without extra setup.
LINUX_GUI_DOCKERFILE = """\
FROM debian:stable-slim
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \\
      libx11-6 libx11-xcb1 libxext6 libxrender1 libxrandr2 libxi6 libxcursor1 libxinerama1 \\
      libxkbcommon-x11-0 libxcomposite1 libxdamage1 libxfixes3 libxtst6 libxss1 \\
      libgl1 libegl1 libgtk-3-0 libnss3 libasound2 libdrm2 libgbm1 libdbus-1-3 \\
      libfontconfig1 libfreetype6 fonts-dejavu-core ca-certificates libfuse2 \\
    && rm -rf /var/lib/apt/lists/*
"""

WINE_RELEASES_API = "https://api.github.com/repos/Gcenx/macOS_Wine_builds/releases?per_page=30"
XQUARTZ_RELEASES_API = "https://api.github.com/repos/XQuartz/XQuartz/releases?per_page=5"


class SetupError(Exception):
    pass


class ManualStep(SetupError):
    """The component can't be installed automatically; the message says how to do it by hand."""


@dataclass
class Component:
    name: str
    label: str
    is_installed: Callable[[], bool]
    install: Callable[["Installer"], None]
    requires: list[str] = field(default_factory=list)
    note: str = ""   # shown after installing, e.g. "restart your PC"


@dataclass
class Installer:
    """Runs (or, in dry-run mode, just prints) the steps of an installation."""
    dry_run: bool = False
    log: Callable[[str], None] = print
    steps: list[str] = field(default_factory=list)

    def run(self, argv: list[str], **kw) -> None:
        self.steps.append(" ".join(argv))
        self.log(("would run: " if self.dry_run else "$ ") + " ".join(argv))
        if self.dry_run:
            return
        try:
            subprocess.run(argv, check=True, **kw)
        except FileNotFoundError:
            raise SetupError(f"'{argv[0]}' isn't available on this computer.")
        except subprocess.CalledProcessError as e:
            raise SetupError(f"'{' '.join(argv)}' failed (exit code {e.returncode}).")

    def note(self, text: str) -> None:
        self.steps.append(f"# {text}")
        self.log(("would " if self.dry_run else "") + text)


# ---------------------------------------------------------------- downloads

def _fetch(url: str, dest: str | None = None) -> bytes:
    """Download with curl when available (it uses the system's certificates), else urllib."""
    curl = shutil.which("curl")
    if curl:
        argv = [curl, "-fsSL", "-H", "User-Agent: omnilaunch", url]
        if dest:
            subprocess.run([*argv, "-o", dest], check=True)
            return b""
        return subprocess.run(argv, check=True, capture_output=True).stdout
    req = urllib.request.Request(url, headers={"User-Agent": "omnilaunch"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    if dest:
        with open(dest, "wb") as f:
            f.write(data)
        return b""
    return data


def pick_release_asset(releases: list[dict], patterns: list[str]) -> tuple[str, str]:
    """First asset (newest release first) matching the first pattern that matches anything."""
    for pattern in patterns:
        rx = re.compile(pattern, re.IGNORECASE)
        for rel in releases:
            if rel.get("draft"):
                continue
            for asset in rel.get("assets", []):
                if rx.search(asset.get("name", "")):
                    return asset["name"], asset["browser_download_url"]
    raise SetupError("Couldn't find a matching download. Check your internet connection and try again.")


# ---------------------------------------------------------------- package managers

def _linux_install(inst: Installer, packages: list[str]) -> None:
    managers = [
        ("apt-get", ["sudo", "apt-get", "install", "-y"]),
        ("dnf", ["sudo", "dnf", "install", "-y"]),
        ("pacman", ["sudo", "pacman", "-S", "--needed", "--noconfirm"]),
        ("zypper", ["sudo", "zypper", "--non-interactive", "install"]),
    ]
    for mgr, cmd in managers:
        if runners.which(mgr):
            if mgr == "apt-get":
                inst.run(["sudo", "apt-get", "update"])
            inst.run([*cmd, *packages])
            return
    raise ManualStep(f"Install {', '.join(packages)} with your system's package manager.")


# ---------------------------------------------------------------- installers

def _install_wine(inst: Installer) -> None:
    if runners.HOST_OS == "linux":
        _linux_install(inst, ["wine"])
        return
    if runners.HOST_OS != "macos":
        raise ManualStep("Windows apps already run natively here.")
    # Homebrew's Wine casks were disabled in Sept 2026, so fetch the standalone WineHQ build.
    apps = os.path.expanduser("~/Applications")
    inst.note("find the newest Wine Stable build for macOS (github.com/Gcenx/macOS_Wine_builds)")
    if inst.dry_run:
        inst.note(f"download it and unpack 'Wine Stable.app' into {apps}")
        return
    releases = json.loads(_fetch(WINE_RELEASES_API))
    name, url = pick_release_asset(releases, [r"^wine-stable.*\.tar\.xz$", r"^wine-devel.*\.tar\.xz$"])
    tmp = tempfile.mkdtemp(prefix="omnilaunch-")
    archive = os.path.join(tmp, name)
    inst.log(f"downloading {name} ...")
    _fetch(url, archive)
    os.makedirs(apps, exist_ok=True)
    inst.run(["tar", "-xJf", archive, "-C", apps])
    for app in os.listdir(apps):
        if app.startswith("Wine") and app.endswith(".app"):
            # Not notarised by Apple: clear the quarantine flag so macOS lets it run.
            inst.run(["xattr", "-dr", "com.apple.quarantine", os.path.join(apps, app)])
    shutil.rmtree(tmp, ignore_errors=True)


def _install_rosetta(inst: Installer) -> None:
    inst.run(["sudo", "softwareupdate", "--install-rosetta", "--agree-to-license"])


def _install_container(inst: Installer) -> None:
    if runners.HOST_OS == "linux":
        _linux_install(inst, ["podman"])
        return
    if runners.HOST_OS == "macos":
        if not runners.which("brew"):
            raise ManualStep("Install OrbStack from https://orbstack.dev (or Docker Desktop), open it once, "
                             "then try again.")
        inst.run(["brew", "install", "podman"])
        inst.run(["podman", "machine", "init"])
        inst.run(["podman", "machine", "start"])
        return
    raise ManualStep("On Windows, Linux apps use WSL instead: run `omnilaunch setup wsl`.")


def _install_wsl(inst: Installer) -> None:
    # wsl --install asks for administrator rights itself and installs Ubuntu.
    inst.run(["wsl", "--install"])


def _install_xquartz(inst: Installer) -> None:
    if inst.dry_run:
        inst.note("download the newest XQuartz installer (github.com/XQuartz/XQuartz) and open it")
    else:
        releases = json.loads(_fetch(XQUARTZ_RELEASES_API))
        name, url = pick_release_asset(releases, [r"^XQuartz-[\d.]+\.pkg$"])
        pkg = os.path.join(tempfile.mkdtemp(prefix="omnilaunch-"), name)
        inst.log(f"downloading {name} ...")
        _fetch(url, pkg)
        inst.run(["open", "-W", pkg])   # Apple's installer window; waits until it closes
    # Let containers connect to the Mac's display over the local network.
    inst.run(["defaults", "write", "org.xquartz.X11", "nolisten_tcp", "-bool", "false"])


def _install_linux_gui(inst: Installer) -> None:
    engine = runners.find_container_engine()
    if not engine and not inst.dry_run:
        raise SetupError("Needs a container engine first: run `omnilaunch setup container`.")
    inst.run([engine or "docker", "build", "-t", LINUX_GUI_IMAGE, "-"], input=LINUX_GUI_DOCKERFILE.encode())


def _install_darling(inst: Installer) -> None:
    raise ManualStep("Darling has no ready-made packages for most Linux distributions. Follow "
                     "https://docs.darlinghq.org/build-instructions.html (about an hour to build).")


# ---------------------------------------------------------------- registry

def components() -> dict[str, Component]:
    host, arch = runners.HOST_OS, runners.HOST_ARCH
    out: list[Component] = []
    if host in ("linux", "macos"):
        out.append(Component("wine", "Wine (runs Windows apps)", lambda: bool(runners.find_wine()), _install_wine,
                             requires=["rosetta"] if host == "macos" and arch == "arm64" else []))
        out.append(Component("container", "Container engine (runs Linux apps)" if host == "macos"
                             else "Container engine (runs Linux apps in a sandbox)",
                             lambda: bool(runners.find_container_engine()), _install_container))
    if host == "macos":
        if arch == "arm64":
            out.append(Component("rosetta", "Rosetta 2 (runs Intel apps)", runners.rosetta_installed,
                                 _install_rosetta))
        out.append(Component("xquartz", "XQuartz (shows Linux app windows on the Mac)",
                             runners.xquartz_installed, _install_xquartz,
                             note="Log out and back in once so XQuartz picks up its new settings."))
        out.append(Component("linux-gui", "Linux graphical app support", runners.linux_gui_image_ready,
                             _install_linux_gui, requires=["container", "xquartz"]))
    if host == "linux":
        out.append(Component("darling", "Darling (runs Mac command-line tools)", lambda: bool(runners.which("darling")),
                             _install_darling))
    if host == "windows":
        out.append(Component("wsl", "WSL (runs Linux apps)", lambda: bool(runners.wsl_distros()), _install_wsl,
                             note="Restart Windows, then finish the Ubuntu setup window that opens."))
    return {c.name: c for c in out}


def install(name: str, dry_run: bool = False, log: Callable[[str], None] = print,
            _seen: set | None = None) -> Installer:
    comps = components()
    if name not in comps:
        available = ", ".join(comps) or "none"
        raise SetupError(f"'{name}' isn't something OmniLaunch can set up on this computer. Available: {available}")
    inst = Installer(dry_run=dry_run, log=log)
    _seen = _seen if _seen is not None else set()
    _seen.add(name)
    comp = comps[name]
    for dep in comp.requires:
        if dep not in _seen and dep in comps and not comps[dep].is_installed():
            _seen.add(dep)
            sub = install(dep, dry_run, log, _seen)
            inst.steps += sub.steps
    if comp.is_installed() and not dry_run:
        log(f"{comp.label}: already installed.")
        return inst
    log(f"Setting up {comp.label} ...")
    comp.install(inst)
    if comp.note:
        log(comp.note)
    return inst


def offer(name: str, assume_yes: bool) -> bool:
    """Ask (in a terminal) whether to install a missing component. Returns True if it got installed."""
    comps = components()
    if name not in comps:
        return False
    comp = comps[name]
    if not assume_yes:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
        try:
            answer = input(f"{comp.label} is needed and isn't installed. Install it now? [Y/n] ").strip().lower()
        except EOFError:
            return False
        if answer not in ("", "y", "yes"):
            return False
    try:
        install(name)
    except ManualStep as e:
        print(f"Can't install automatically: {e}", file=sys.stderr)
        return False
    except SetupError as e:
        print(f"Setup failed: {e}", file=sys.stderr)
        return False
    return comp.is_installed()
