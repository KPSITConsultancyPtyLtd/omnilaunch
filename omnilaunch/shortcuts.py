"""Make Windows programs installed through OmniLaunch show up like native apps.

After a Windows installer runs inside an environment, the Start Menu shortcuts it created are read,
and each program gets a launcher in the host's own app list: an app in ~/Applications/OmniLaunch Apps
on a Mac, or an entry in the app menu on Linux. Opening it runs the program in that same environment.
"""

from __future__ import annotations

import glob
import os
import re
import shlex
import struct
import subprocess
import sys

from . import runners

SKIP = re.compile(r"uninstall|readme|read me|help|manual|website|license|release notes|changelog", re.I)
SHORTCUT_DIRS = [
    "ProgramData/Microsoft/Windows/Start Menu/Programs",
    "users/*/AppData/Roaming/Microsoft/Windows/Start Menu/Programs",
    "users/*/Start Menu/Programs",          # older Wine prefixes
    "users/*/Desktop",
    "users/Public/Desktop",
]


def drive_c(prefix: str) -> str | None:
    for candidate in (os.path.join(prefix, "drive_c"), os.path.join(prefix, "pfx", "drive_c")):
        if os.path.isdir(candidate):
            return candidate
    return None


def find_shortcuts(prefix: str) -> dict:
    """All .lnk files in the environment's Start Menu and desktops: {path: modification time}."""
    root = drive_c(prefix)
    if not root:
        return {}
    found = {}
    for pattern in SHORTCUT_DIRS:
        for folder in glob.glob(os.path.join(root, pattern)):
            for path in glob.glob(os.path.join(folder, "**", "*.lnk"), recursive=True):
                try:
                    found[path] = os.path.getmtime(path)
                except OSError:
                    pass
    return found


# ---------------------------------------------------------------- reading .lnk files (MS-SHLLINK)

def parse_lnk(data: bytes) -> dict:
    """Target path, arguments and working folder of a Windows shortcut."""
    if len(data) < 0x4C or struct.unpack_from("<I", data, 0)[0] != 0x4C:
        raise ValueError("not a Windows shortcut")
    flags = struct.unpack_from("<I", data, 0x14)[0]
    unicode = bool(flags & 0x80)
    pos = 0x4C
    if flags & 0x01:                                   # HasLinkTargetIDList
        pos += 2 + struct.unpack_from("<H", data, pos)[0]
    target = ""
    if flags & 0x02:                                   # HasLinkInfo
        info_size, header_size, info_flags = struct.unpack_from("<III", data, pos)
        if info_flags & 0x01:                          # VolumeIDAndLocalBasePath
            base_off = struct.unpack_from("<I", data, pos + 16)[0]
            if header_size >= 0x24:
                uoff = struct.unpack_from("<I", data, pos + 28)[0]
                target = _read_wstr(data, pos + uoff)
            else:
                target = _read_cstr(data, pos + base_off)
        pos += info_size
    strings = {}
    for bit, key in ((0x04, "name"), (0x08, "relative"), (0x10, "workdir"), (0x20, "args"), (0x40, "icon")):
        if flags & bit:
            count = struct.unpack_from("<H", data, pos)[0]
            pos += 2
            size = count * 2 if unicode else count
            raw = data[pos:pos + size]
            strings[key] = raw.decode("utf-16-le" if unicode else "cp1252", errors="replace")
            pos += size
    if not target and strings.get("relative"):
        target = strings["relative"]
    return {"target": target, "args": strings.get("args", ""), "workdir": strings.get("workdir", "")}


def _read_cstr(data: bytes, pos: int) -> str:
    end = data.index(b"\0", pos)
    return data[pos:end].decode("cp1252", errors="replace")


def _read_wstr(data: bytes, pos: int) -> str:
    end = pos
    while data[end:end + 2] != b"\0\0":
        end += 2
    return data[pos:end].decode("utf-16-le", errors="replace")


def to_prefix_path(prefix: str, windows_path: str) -> str | None:
    """C:\\Program Files\\App\\app.exe -> <prefix>/drive_c/Program Files/App/app.exe (matching case on disk)."""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", windows_path)
    if not m or m.group(1).lower() != "c":
        return None
    path = drive_c(prefix)
    if not path:
        return None
    for part in [p for p in re.split(r"[\\/]", m.group(2)) if p]:
        try:
            entries = os.listdir(path)
        except OSError:
            return None
        match = next((e for e in entries if e == part), None) or next(
            (e for e in entries if e.lower() == part.lower()), None)
        if match is None:
            return None
        path = os.path.join(path, match)
    return path


# ---------------------------------------------------------------- launchers in the host's app list

def _launcher_parts() -> list:
    exe = runners.which("omnilaunch")
    if exe:
        return [exe]
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "omnilaunch")
    return [sys.executable, script]


def launcher_dir() -> str:
    if runners.HOST_OS == "macos":
        return os.path.expanduser("~/Applications/OmniLaunch Apps")
    base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return os.path.join(base, "applications")


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "App"


def create_launcher(name: str, env_name: str, target: str, args: str = "") -> str:
    argv = [*_launcher_parts(), "run", "--gui", "--env", env_name, target]
    extra = shlex.split(args, posix=False) if args else []
    if runners.HOST_OS == "macos":
        from .desktop import MAC_PATH, _applescript_string
        app = os.path.join(launcher_dir(), _safe_name(name) + ".app")
        os.makedirs(launcher_dir(), exist_ok=True)
        cmd = 'export PATH="' + MAC_PATH + '"; ' + " ".join(shlex.quote(a) for a in argv + extra) + " >/dev/null 2>&1 &"
        script = f"on run\n\tdo shell script {_applescript_string(cmd)}\nend run\n"
        subprocess.run(["/usr/bin/osacompile", "-o", app, "-e", script], check=True, capture_output=True)
        return app
    from .desktop import _exec_quote
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "app"
    path = os.path.join(launcher_dir(), f"omnilaunch-{slug}.desktop")
    os.makedirs(launcher_dir(), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join([
            "[Desktop Entry]", "Type=Application", f"Name={name}",
            "Comment=Windows app, opened with OmniLaunch",
            "Exec=" + " ".join(_exec_quote(a) for a in argv + extra),
            "Icon=application-x-ms-dos-executable", "Terminal=false", "Categories=Wine;", "",
        ]))
    return path


def add_new_programs(prefix: str, env_name: str, before: dict) -> list:
    """Create launchers for shortcuts that appeared since `before`. Returns the program names added."""
    added = []
    for path, mtime in sorted(find_shortcuts(prefix).items()):
        if before.get(path) == mtime:
            continue
        name = os.path.splitext(os.path.basename(path))[0]
        if SKIP.search(name):
            continue
        try:
            with open(path, "rb") as f:
                link = parse_lnk(f.read())
        except (OSError, ValueError, struct.error, IndexError):
            continue
        target = to_prefix_path(prefix, link["target"])
        if not target or not target.lower().endswith((".exe", ".bat", ".msi")):
            continue
        try:
            create_launcher(name, env_name, target, link["args"])
        except (OSError, subprocess.CalledProcessError):
            continue
        added.append(name)
    return added
