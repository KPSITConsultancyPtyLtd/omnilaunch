"""Hook OmniLaunch into the Linux desktop so double-clicking an app runs it."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys

DESKTOP_ID = "omnilaunch.desktop"

# File types OmniLaunch should open when double-clicked.
MIME_TYPES = [
    "application/x-ms-dos-executable",
    "application/x-msdownload",
    "application/x-msdos-program",
    "application/x-ms-ne-executable",
    "application/vnd.microsoft.portable-executable",
    "application/x-msi",
    "application/x-ms-shortcut",
    "application/x-bat",
    "application/x-mach-binary",
    "application/x-appimage",
    "application/vnd.appimage",
]


def applications_dir() -> str:
    base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return os.path.join(base, "applications")


def launcher_command() -> str:
    exe = shutil.which("omnilaunch")
    if exe:
        return shlex.quote(exe)
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Fall back to running the package from where it lives.
    return f"env PYTHONPATH={shlex.quote(pkg_parent)} {shlex.quote(sys.executable)} -m omnilaunch"


def desktop_entry() -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=OmniLaunch",
        "Comment=Run Windows, macOS and Linux apps",
        f"Exec={launcher_command()} run --notify %f",
        "Icon=application-x-executable",
        "Terminal=false",
        "NoDisplay=true",
        "Categories=System;Emulator;",
        f"MimeType={';'.join(MIME_TYPES)};",
        "",
    ])


def integrate(dry_run: bool = False) -> list[str]:
    path = os.path.join(applications_dir(), DESKTOP_ID)
    steps = [f"write {path}"]
    if not dry_run:
        os.makedirs(applications_dir(), exist_ok=True)
        with open(path, "w") as f:
            f.write(desktop_entry())
    if shutil.which("xdg-mime"):
        steps.append(f"xdg-mime default {DESKTOP_ID} <{len(MIME_TYPES)} file types>")
        if not dry_run:
            subprocess.run(["xdg-mime", "default", DESKTOP_ID, *MIME_TYPES], check=False)
    else:
        steps.append("xdg-mime not found: set OmniLaunch as the default app manually in your file manager")
    if shutil.which("update-desktop-database"):
        steps.append("update-desktop-database")
        if not dry_run:
            subprocess.run(["update-desktop-database", applications_dir()], check=False)
    return steps


def unintegrate() -> bool:
    path = os.path.join(applications_dir(), DESKTOP_ID)
    if not os.path.exists(path):
        return False
    os.remove(path)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", applications_dir()], check=False)
    return True


def notify(title: str, body: str) -> None:
    """Show a desktop notification (used when launched by double-click, with no terminal)."""
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "OmniLaunch", title, body], check=False)
