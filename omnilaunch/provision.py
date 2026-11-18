"""Install the helpers OmniLaunch needs (Wine, WSL, containers, ...) so users don't have to.

Each component knows whether it's installed and how to install it on this kind of computer.
`omnilaunch run` offers to install a missing component the first time an app needs it.
"""

from __future__ import annotations

import hashlib
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

UMU_ZIPAPP_PATTERNS = [r"zipapp.*\.tar(\.gz|\.xz)?$"]
UMU_RELEASES_API = "https://api.github.com/repos/Open-Wine-Components/umu-launcher/releases?per_page=5"
WINE_X64_RELEASES_API = "https://api.github.com/repos/Kron4ek/Wine-Builds/releases?per_page=50"
BOX64_RELEASES_API = "https://api.github.com/repos/ptitSeb/box64/releases?per_page=5"
# Box64 translates Intel code on ARM; Wine's "wow64" Intel build runs 32- and 64-bit Windows apps.
BOX64_DOCKERFILE = r"""# Stage 1: build the newest Box64 release (Debian's package lags behind and misses instructions
# that real Windows programs use). Built on the machine's own CPU and cross-compiled to ARM64 when
# that CPU isn't ARM, so it's fast either way.
FROM --platform=$BUILDPLATFORM debian:trixie-slim AS box64
ARG BOX64_TAG
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl cmake make python3 git \
      $( [ "$(dpkg --print-architecture)" = arm64 ] && echo gcc g++ || echo gcc-aarch64-linux-gnu g++-aarch64-linux-gnu ) \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /src /out/lib && curl -fsSL "https://github.com/ptitSeb/box64/archive/refs/tags/${BOX64_TAG}.tar.gz" \
      | tar -xz -C /src --strip-components=1 \
    && CC=$( [ "$(dpkg --print-architecture)" = arm64 ] && echo gcc || echo aarch64-linux-gnu-gcc ) \
    && cmake -S /src -B /build -DARM_DYNAREC=ON -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_C_COMPILER=$CC \
         -DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR=aarch64 \
    && cmake --build /build -j"$(nproc)" \
    && cp /build/box64 /out/box64 && (cp -r /src/x64lib/. /out/lib/ 2>/dev/null || true)

# Stage 2: the runtime: ARM64 Debian with Box64 and Intel (WoW64) Wine.
FROM debian:trixie-slim
ARG WINE_URL
ARG WINE_SHA256
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl xz-utils \
      libx11-6 libxext6 libxrender1 libxrandr2 libxi6 libxcursor1 libxinerama1 libxcomposite1 \
      libxfixes3 libxkbcommon0 libfreetype6 libfontconfig1 fonts-liberation libgl1 libegl1 \
      libvulkan1 libgnutls30t64 libasound2t64 libdbus-1-3 libunwind8 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=box64 /out/box64 /usr/local/bin/box64
COPY --from=box64 /out/lib/ /usr/lib/box64-x86_64-linux-gnu/
RUN curl -fsSL "$WINE_URL" -o /tmp/wine.tar.xz \
    && if [ -n "$WINE_SHA256" ]; then echo "$WINE_SHA256  /tmp/wine.tar.xz" | sha256sum -c -; fi \
    && mkdir -p /opt/wine && tar -xJf /tmp/wine.tar.xz -C /opt/wine --strip-components=1 \
    && rm /tmp/wine.tar.xz
ENV PATH=/opt/wine/bin:$PATH BOX64_NOBANNER=1 BOX64_LOG=0
"""
WINE_X64_PATTERNS = [r"^wine-\d+\.0-amd64-wow64\.tar\.xz$", r"^wine-[\d.]+-amd64-wow64\.tar\.xz$"]

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
    size: str = ""   # rough download size, shown before installing


@dataclass
class Installer:
    """Runs (or, in dry-run mode, just prints) the steps of an installation."""
    dry_run: bool = False
    log: Callable[[str], None] = print
    steps: list[str] = field(default_factory=list)
    gui: bool = False   # no terminal: ask for passwords with the system prompt, log output to a file

    def run(self, argv: list[str], **kw) -> None:
        self.steps.append(" ".join(argv))
        self.log(("would run: " if self.dry_run else "$ ") + " ".join(argv))
        if self.dry_run:
            return
        if argv and argv[0] == "sudo" and (self.gui or not sys.stdin.isatty()):
            from .desktop import run_as_admin
            argv = run_as_admin(argv[1:])
        log = None
        if self.gui and "stdout" not in kw:
            os.makedirs(runners.data_dir(), exist_ok=True)
            log = open(os.path.join(runners.data_dir(), "setup.log"), "a")
            kw.update(stdout=log, stderr=subprocess.STDOUT)
        try:
            subprocess.run(argv, check=True, **kw)
        except FileNotFoundError:
            raise SetupError(f"'{argv[0]}' isn't available on this computer.")
        except subprocess.CalledProcessError as e:
            raise SetupError(f"'{' '.join(argv)}' failed (exit code {e.returncode}).")
        finally:
            if log:
                log.close()

    def note(self, text: str) -> None:
        self.steps.append(f"# {text}")
        self.log(("would " if self.dry_run else "") + text)


# ---------------------------------------------------------------- downloads

def _fetch(url: str, dest: str | None = None, attempts: int = 3) -> bytes:
    """Download, retrying a couple of times: networks (and GitHub's API) drop connections now and then."""
    import time
    for attempt in range(attempts):
        try:
            return _fetch_once(url, dest)
        except (subprocess.CalledProcessError, OSError):
            if attempt == attempts - 1:
                raise
            time.sleep(2 * (attempt + 1))
    return b""


def _fetch_once(url: str, dest: str | None = None) -> bytes:
    """Download with curl when available (it uses the system's certificates), else urllib."""
    curl = shutil.which("curl")
    headers = ["User-Agent: omnilaunch"]
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers.append(f"Authorization: Bearer {token}")   # avoids GitHub's low anonymous rate limit
    if curl:
        argv = [curl, "-fsSL"]
        for h in headers:
            argv += ["-H", h]
        argv.append(url)
        if dest:
            subprocess.run([*argv, "-o", dest], check=True)
            return b""
        return subprocess.run(argv, check=True, capture_output=True).stdout
    req = urllib.request.Request(url, headers=dict(h.split(": ", 1) for h in headers))
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    if dest:
        with open(dest, "wb") as f:
            f.write(data)
        return b""
    return data


def _find_asset(releases: list[dict], patterns: list[str]) -> dict:
    for pattern in patterns:
        rx = re.compile(pattern, re.IGNORECASE)
        for rel in releases:
            if rel.get("draft"):
                continue
            for asset in rel.get("assets", []):
                if rx.search(asset.get("name", "")):
                    return asset
    raise SetupError("Couldn't find a matching download. Check your internet connection and try again.")


def pick_release_asset(releases: list[dict], patterns: list[str]) -> tuple[str, str]:
    """First asset (newest release first) matching the first pattern that matches anything."""
    asset = _find_asset(releases, patterns)
    return asset["name"], asset["browser_download_url"]


def asset_sha256(releases: list[dict], patterns: list[str]) -> str | None:
    """The SHA-256 GitHub records for that asset ("digest": "sha256:..."), or None for old uploads."""
    digest = _find_asset(releases, patterns).get("digest") or ""
    if re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        return digest.split(":", 1)[1]
    return None


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download_release_asset(releases: list[dict], patterns: list[str], folder: str,
                           require_digest: bool = False) -> str:
    """Downloads the newest matching release file into folder and checks it against the SHA-256 that
    GitHub publishes for it. A mismatch is always an error; a missing checksum is one if require_digest.
    Returns the file's path."""
    name, url = pick_release_asset(releases, patterns)
    expected = asset_sha256(releases, patterns)
    if expected is None and require_digest:
        raise SetupError(f"{name} has no published checksum, so it can't be verified. Not installing it.")
    path = os.path.join(folder, name)
    _fetch(url, path)
    if expected is not None:
        actual = sha256_file(path)
        if actual != expected:
            os.remove(path)
            raise SetupError(f"{name} didn't match its published checksum (expected {expected}, got {actual}). "
                             "The download was damaged or tampered with, so it was deleted.")
    return path


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
    patterns = [r"^wine-stable.*\.tar\.xz$", r"^wine-devel.*\.tar\.xz$"]
    tmp = tempfile.mkdtemp(prefix="omnilaunch-")
    inst.log(f"downloading {pick_release_asset(releases, patterns)[0]} ...")
    archive = download_release_asset(releases, patterns, tmp)
    os.makedirs(apps, exist_ok=True)
    inst.run(["tar", "-xJf", archive, "-C", apps])
    for app in os.listdir(apps):
        if app.startswith("Wine") and app.endswith(".app"):
            # Not notarised by Apple: clear the quarantine flag so macOS lets it run.
            inst.run(["xattr", "-dr", "com.apple.quarantine", os.path.join(apps, app)])
    shutil.rmtree(tmp, ignore_errors=True)


def _install_proton(inst: Installer) -> None:
    # Arch and Fedora package umu-launcher; elsewhere use the self-contained zipapp from its releases.
    # Fedora's repositories don't always carry it (Fedora 44 doesn't), so ask dnf before relying on it.
    for mgr, probe, cmd in (
            ("pacman", None, ["sudo", "pacman", "-S", "--needed", "--noconfirm", "umu-launcher"]),
            ("dnf", ["dnf", "-q", "info", "umu-launcher"], ["sudo", "dnf", "install", "-y", "umu-launcher"])):
        if runners.which(mgr) and (probe is None or subprocess.run(probe, capture_output=True).returncode == 0):
            inst.run(cmd)
            return
    dest = os.path.join(runners.data_dir(), "umu")
    inst.note(f"download umu-launcher's self-contained build (github.com/Open-Wine-Components/umu-launcher) "
              f"into {dest}")
    if inst.dry_run:
        return
    releases = json.loads(_fetch(UMU_RELEASES_API))
    tmp = tempfile.mkdtemp(prefix="omnilaunch-")
    inst.log(f"downloading {pick_release_asset(releases, UMU_ZIPAPP_PATTERNS)[0]} ...")
    archive = download_release_asset(releases, UMU_ZIPAPP_PATTERNS, tmp)
    os.makedirs(dest, exist_ok=True)
    inst.run(["tar", "-xf", archive, "-C", dest])
    shutil.rmtree(tmp, ignore_errors=True)
    if not runners.find_umu():
        raise SetupError("umu-launcher downloaded, but its umu-run program wasn't found inside.")


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
        patterns = [r"^XQuartz-[\d.]+\.pkg$"]
        inst.log(f"downloading {pick_release_asset(releases, patterns)[0]} ...")
        pkg = download_release_asset(releases, patterns, tempfile.mkdtemp(prefix="omnilaunch-"))
        inst.run(["open", "-W", pkg])   # Apple's installer window; waits until it closes
    # Let containers connect to the Mac's display over the local network.
    inst.run(["defaults", "write", "org.xquartz.X11", "nolisten_tcp", "-bool", "false"])


def _install_linux_gui(inst: Installer) -> None:
    engine = runners.find_container_engine()
    if not engine and not inst.dry_run:
        raise SetupError("Needs a container engine first: run `omnilaunch setup container`.")
    inst.run([engine or "docker", "build", "-t", LINUX_GUI_IMAGE, "-"], input=LINUX_GUI_DOCKERFILE.encode())


def _unzip(inst: Installer, archive: str, dest: str) -> None:
    os.makedirs(dest, exist_ok=True)
    if runners.HOST_OS != "windows" and runners.which("unzip"):
        inst.run(["unzip", "-q", "-o", archive, "-d", dest])      # keeps the executable flags
        return
    inst.log(f"$ unzip {archive} -> {dest}")
    if not inst.dry_run:
        import zipfile
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)


def _install_android(inst: Installer) -> None:
    from . import android
    sdk = android.sdk_root()
    abi = android.guest_abi()
    inst.note(f"download Google's Android Emulator, platform tools and an Android {android.PREFERRED_API} "
              f"({abi}) system image into {sdk} (about 2 GB)")
    if inst.dry_run:
        inst.note(f"create the '{android.AVD_NAME}' virtual device")
        return
    tmp = tempfile.mkdtemp(prefix="omnilaunch-android-")
    try:
        emulator_url = android.pick_emulator_url(_fetch(android.REPO_INDEX))
        api, image_url = android.pick_system_image(_fetch(android.SYSIMG_INDEX), abi)
        for label, url, dest in (
                ("platform tools", android.platform_tools_url(), sdk),
                ("emulator", emulator_url, sdk),
                (f"Android {api} system image", image_url,
                 os.path.join(sdk, "system-images", f"android-{api}", "google_apis"))):
            archive = os.path.join(tmp, os.path.basename(url))
            inst.log(f"downloading {label} ...")
            _fetch(url, archive)
            _unzip(inst, archive, dest)
        android.write_avd(api, abi)
        inst.log(f"created the '{android.AVD_NAME}' virtual device (Android {api})")
    except ValueError as e:
        raise SetupError(str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _install_box64(inst: Installer) -> None:
    engine = runners.find_container_engine()
    if not engine and not inst.dry_run:
        raise SetupError("Needs a container engine first: run `omnilaunch setup container`.")
    if inst.dry_run:
        inst.note("find the newest Box64 release (github.com/ptitSeb/box64) and Intel (amd64-wow64) Wine "
                  "build (github.com/Kron4ek/Wine-Builds)")
        url, tag, sha = "<wine-amd64-wow64.tar.xz>", "<box64 release>", ""
    else:
        wine_releases = json.loads(_fetch(WINE_X64_RELEASES_API))
        name, url = pick_release_asset(wine_releases, WINE_X64_PATTERNS)
        sha = asset_sha256(wine_releases, WINE_X64_PATTERNS) or ""
        tag = newest_release_tag(json.loads(_fetch(BOX64_RELEASES_API)))
        inst.log(f"using {name} and Box64 {tag}")
    inst.run([engine or "docker", "build", "--platform", "linux/arm64", "-t", runners.BOX64_IMAGE,
              "--build-arg", f"WINE_URL={url}", "--build-arg", f"WINE_SHA256={sha}",
              "--build-arg", f"BOX64_TAG={tag}", "-"],
             input=BOX64_DOCKERFILE.encode())


def newest_release_tag(releases: list) -> str:
    for rel in releases:
        if not rel.get("draft") and not rel.get("prerelease") and rel.get("tag_name"):
            return rel["tag_name"]
    raise SetupError("Couldn't find a Box64 release. Check your internet connection and try again.")


def _install_darling(inst: Installer) -> None:
    raise ManualStep("Darling has no ready-made packages for most Linux distributions. Follow "
                     "https://docs.darlinghq.org/build-instructions.html (about an hour to build).")


# ---------------------------------------------------------------- registry

def components() -> dict[str, Component]:
    host, arch = runners.HOST_OS, runners.HOST_ARCH
    out: list[Component] = []
    if host in ("linux", "macos"):
        out.append(Component("wine", "Wine (runs Windows apps)", lambda: bool(runners.find_wine()), _install_wine,
                             requires=["rosetta"] if host == "macos" and arch == "arm64" else [], size="about 400 MB"))
        out.append(Component("container", "Container engine (runs Linux apps)" if host == "macos"
                             else "Container engine (runs Linux apps in a sandbox)",
                             lambda: bool(runners.find_container_engine()), _install_container))
    if host == "macos":
        if arch == "arm64":
            out.append(Component("rosetta", "Rosetta 2 (runs Intel apps)", runners.rosetta_installed,
                                 _install_rosetta))
        out.append(Component("xquartz", "XQuartz (shows Linux app windows on the Mac)",
                             runners.xquartz_installed, _install_xquartz,
                             note="If Linux or Windows app windows don't appear the first time, log out and "
                                  "back in once so XQuartz finishes setting itself up."))
        out.append(Component("linux-gui", "Linux graphical app support", runners.linux_gui_image_ready,
                             _install_linux_gui, requires=["container", "xquartz"], size="about 600 MB"))
    if host == "linux" and arch == "x86_64":
        out.append(Component("proton", "Proton via umu-launcher (best for Windows games)",
                             lambda: bool(runners.find_umu()), _install_proton, size="about 1 GB with the first game"))
    if arch == "arm64" and host in ("macos", "linux"):
        out.append(Component("box64", "Windows apps without Rosetta (Box64 + Wine in a Linux VM)",
                             runners.box64_image_ready, _install_box64,
                             requires=["container", "xquartz"] if host == "macos" else ["container"],
                             size="about 1 GB"))
    if host == "linux":
        out.append(Component("darling", "Darling (runs Mac command-line tools)", lambda: bool(runners.which("darling")),
                             _install_darling))
    if host == "linux" and runners.which("waydroid"):
        pass   # Waydroid already provides Android apps
    else:
        from . import android
        notes = {"windows": "If the emulator won't start, turn on 'Windows Hypervisor Platform' in "
                            "Windows Features and restart.",
                 "linux": "The emulator needs KVM: make sure virtualization is on in your BIOS and your user "
                          "is in the 'kvm' group."}
        out.append(Component("android", "Android apps (Google's Android Emulator)", android.emulator_ready,
                             _install_android, note=notes.get(host, ""), size="about 2 GB"))
    if host == "windows":
        out.append(Component("wsl", "WSL (runs Linux apps)", lambda: bool(runners.wsl_distros()), _install_wsl,
                             note="Restart Windows, then finish the Ubuntu setup window that opens."))
    return {c.name: c for c in out}


def install(name: str, dry_run: bool = False, log: Callable[[str], None] = print,
            _seen: set | None = None, gui: bool = False) -> Installer:
    comps = components()
    if name not in comps:
        available = ", ".join(comps) or "none"
        raise SetupError(f"'{name}' isn't something OmniLaunch can set up on this computer. Available: {available}")
    inst = Installer(dry_run=dry_run, log=log, gui=gui)
    _seen = _seen if _seen is not None else set()
    _seen.add(name)
    comp = comps[name]
    for dep in comp.requires:
        if dep not in _seen and dep in comps and not comps[dep].is_installed():
            _seen.add(dep)
            sub = install(dep, dry_run, log, _seen, gui)
            inst.steps += sub.steps
    if comp.is_installed() and not dry_run:
        log(f"{comp.label}: already installed.")
        return inst
    log(f"Setting up {comp.label} ...")
    comp.install(inst)
    if comp.note:
        log(comp.note)
    return inst


def offer(name: str, assume_yes: bool, gui: bool = False) -> bool:
    """Ask whether to install a missing component (in the terminal, or in a dialog when `gui`).
    Returns True if it got installed."""
    from . import desktop
    comps = components()
    if name not in comps:
        return False
    comp = comps[name]
    size = f" ({comp.size})" if comp.size else ""
    if not assume_yes:
        if gui:
            if not desktop.ask("OmniLaunch", f"To open this app, OmniLaunch needs {comp.label}{size}, "
                                              "which isn't installed yet.\n\nInstall it now?"):
                return False
        else:
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                return False
            try:
                answer = input(f"{comp.label}{size} is needed and isn't installed. Install it now? [Y/n] ")
            except EOFError:
                return False
            if answer.strip().lower() not in ("", "y", "yes"):
                return False
    if gui:
        desktop.notify("OmniLaunch", f"Installing {comp.label}. This can take a few minutes...")
    try:
        install(name, gui=gui, log=(lambda _m: None) if gui else print)
    except (ManualStep, SetupError) as e:
        msg = ("Can't install automatically: " if isinstance(e, ManualStep) else "Setup failed: ") + str(e)
        desktop.alert("OmniLaunch", msg) if gui else print(msg, file=sys.stderr)
        return False
    if comp.note:
        desktop.alert("OmniLaunch", comp.note) if gui else None
    ok = comp.is_installed()
    if gui and ok:
        desktop.notify("OmniLaunch", f"{comp.label} is ready.")
    return ok
