"""Asks before opening an app that was downloaded from the internet, the way each system does for its
own apps. macOS's Gatekeeper and Windows SmartScreen don't check Windows .exe files on a Mac, Android
apps, or Linux AppImages, so without this, double-clicking a downloaded one would run it silently.

Browsers mark downloads: macOS with the com.apple.quarantine attribute, Linux with user.xdg.origin.url,
Windows with a Zone.Identifier stream. Once the user says yes, that file isn't asked about again."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from . import runners


def downloaded_from(path: str) -> str | None:
    """Where the file came from ("the internet", or its web address), or None if it isn't marked."""
    try:
        if runners.HOST_OS == "macos":
            r = subprocess.run(["/usr/bin/xattr", "-p", "com.apple.quarantine", path],
                               capture_output=True, text=True, timeout=10)
            return "the internet" if r.returncode == 0 else None
        if runners.HOST_OS == "windows":
            with open(path + ":Zone.Identifier", encoding="utf-8", errors="replace") as f:
                text = f.read()
            zone = next((line.split("=", 1)[1].strip() for line in text.splitlines()
                         if line.startswith("ZoneId=")), "")
            if zone in ("3", "4"):                  # Internet, Restricted
                url = next((line.split("=", 1)[1].strip() for line in text.splitlines()
                            if line.startswith("HostUrl=")), "")
                return url or "the internet"
            return None
        url = os.getxattr(path, "user.xdg.origin.url").decode("utf-8", errors="replace")
        return url or "the internet"
    except (OSError, AttributeError, subprocess.SubprocessError):
        return None


def _store() -> str:
    return os.path.join(runners.data_dir(), "trusted-downloads.json")


def _key(path: str) -> str:
    st = os.stat(path)
    return f"{os.path.realpath(path)}|{st.st_size}|{st.st_mtime_ns}"   # a changed file is asked about again


def _load() -> dict:
    try:
        with open(_store()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def is_trusted(path: str) -> bool:
    return _key(path) in _load()


def trust(path: str) -> None:
    data = _load()
    data[_key(path)] = True
    os.makedirs(runners.data_dir(), exist_ok=True)
    tmp = _store() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, _store())


def confirm_open(path: str, gui: bool) -> bool:
    """True if the app may be opened. Asks only for double-clicked downloads not approved before;
    running a file from a terminal is already a deliberate choice."""
    origin = downloaded_from(path)
    if not origin or not gui or is_trusted(path):
        return True
    from . import desktop
    name = os.path.basename(path)
    if desktop._no_dialogs():
        print(f"[omnilaunch] note: {name} was downloaded from {origin}", file=sys.stderr)
        return True
    where = "the internet" if origin == "the internet" else origin
    ok = desktop.ask("Open an app from the internet?",
                     f"“{name}” was downloaded from {where}.\n\n"
                     "It will run with access to your files, like any app you install. "
                     "Open it only if you trust where it came from.",
                     yes="Open", no="Cancel")
    if ok:
        trust(path)
    return ok
