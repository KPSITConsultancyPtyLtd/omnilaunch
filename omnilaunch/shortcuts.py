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
    """Where launchers go: the Mac's main Applications folder (what Finder's sidebar, Launchpad and
    Spotlight show), or the Linux app menu."""
    if runners.HOST_OS == "macos":
        if os.access("/Applications", os.W_OK):
            return "/Applications/OmniLaunch Apps"
        return os.path.expanduser("~/Applications/OmniLaunch Apps")
    base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return os.path.join(base, "applications")


def desktop_dir() -> str | None:
    if os.environ.get("XDG_DESKTOP_DIR"):
        path = os.path.expanduser(os.environ["XDG_DESKTOP_DIR"])
        return path if os.path.isdir(path) else None
    if runners.HOST_OS == "linux" and runners.which("xdg-user-dir"):
        try:
            out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5).stdout.strip()
            if out and out != os.path.expanduser("~"):
                return out
        except (OSError, subprocess.SubprocessError):
            pass
    desktop = os.path.expanduser("~/Desktop")
    return desktop if os.path.isdir(desktop) else None


def desktop_shortcuts_enabled() -> bool:
    return os.environ.get("OMNILAUNCH_DESKTOP_SHORTCUTS", "1") != "0"


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "App"


def _put_on_desktop(launcher: str, name: str) -> str | None:
    """Add a Desktop shortcut pointing at the launcher. Returns its path, or None if not possible."""
    desk = desktop_dir()
    if not desk:
        return None
    try:
        if runners.HOST_OS == "macos":
            link = os.path.join(desk, _safe_name(name) + ".app")
            if os.path.islink(link):
                os.unlink(link)
            elif os.path.exists(link):
                return link   # something real with that name is already there: leave it alone
            os.symlink(launcher, link)   # Finder shows it like an alias; double-click opens the app
            return link
        dest = os.path.join(desk, os.path.basename(launcher))
        with open(launcher) as src, open(dest, "w") as out:
            out.write(src.read())
        os.chmod(dest, 0o755)   # desktops only run launchers marked executable (and trusted)
        if runners.which("gio"):
            subprocess.run(["gio", "set", dest, "metadata::trusted", "true"], capture_output=True, check=False)
        return dest
    except OSError:
        return None


def create_launcher(name: str, env_name: str, target: str, args: str = "", desktop: bool = True) -> str:
    argv = [*_launcher_parts(), "run", "--gui", "--env", env_name, target]
    extra = shlex.split(args, posix=False) if args else []
    if runners.HOST_OS == "macos":
        from .desktop import MAC_PATH, _applescript_string
        app = os.path.join(launcher_dir(), _safe_name(name) + ".app")
        os.makedirs(launcher_dir(), exist_ok=True)
        cmd = 'export PATH="' + MAC_PATH + '"; ' + " ".join(shlex.quote(a) for a in argv + extra)
        quiet = " >/dev/null 2>&1 &"
        # 'on open' makes the app usable with Finder's Open With: the chosen files are passed to the program.
        script = "\n".join([
            "on run",
            f"\tdo shell script {_applescript_string(cmd + quiet)}",
            "end run",
            "",
            "on open theFiles",
            "\tset fileArgs to \"\"",
            "\trepeat with f in theFiles",
            "\t\tset fileArgs to fileArgs & \" \" & quoted form of (POSIX path of f)",
            "\tend repeat",
            f"\tdo shell script {_applescript_string(cmd)} & fileArgs & {_applescript_string(quiet)}",
            "end open",
            "",
        ])
        if os.path.exists(app):
            import shutil
            shutil.rmtree(app)
        subprocess.run(["/usr/bin/osacompile", "-o", app, "-e", script], check=True, capture_output=True)
        from . import icons
        ico = icons.extract_ico(target)
        if ico:
            icons.mac_set_app_icon(app, ico)      # before signing: the icon is part of the signed app
        _make_open_with_target(app, name, env_name)
        path = app
    else:
        from . import icons
        from .desktop import _exec_quote
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "app"
        path = os.path.join(launcher_dir(), f"omnilaunch-{slug}.desktop")
        os.makedirs(launcher_dir(), exist_ok=True)
        ico = icons.extract_ico(target)
        icon = (icons.save_icon_file(ico, os.path.join(os.path.dirname(launcher_dir()), "icons", "omnilaunch"),
                                     slug) if ico else None) or "application-x-ms-dos-executable"
        with open(path, "w") as f:
            f.write("\n".join([
                "[Desktop Entry]", "Type=Application", f"Name={name}",
                "Comment=Windows app, opened with OmniLaunch",
                "Exec=" + " ".join(_exec_quote(a) for a in argv + extra) + " %F",
                "MimeType=application/octet-stream;application/zip;application/x-7z-compressed;"
                "application/x-rar;text/plain;",
                f"Icon={icon}", "Terminal=false", "Categories=Wine;", f"X-OmniLaunch-Env={env_name}", "",
            ]))
    if desktop and desktop_shortcuts_enabled():
        _put_on_desktop(path, name)
    return path


def _make_open_with_target(app: str, name: str, env_name: str = "") -> None:
    """Let Finder offer this app under Open With for any file, like a native app (macOS)."""
    import plistlib
    plist_path = os.path.join(app, "Contents", "Info.plist")
    try:
        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException):
        return
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "app"
    plist.update({
        "CFBundleIdentifier": f"com.kpsit.omnilaunch.app.{slug}",
        "CFBundleName": name,
        "CFBundleDisplayName": name,
        "OmniLaunchEnv": env_name,          # lets `envs remove` find and remove this launcher
        "CFBundleDocumentTypes": [{
            "CFBundleTypeName": "Any file",
            "CFBundleTypeRole": "Editor",
            "LSHandlerRank": "Alternate",
            "LSItemContentTypes": ["public.data", "public.content", "public.archive"],
        }],
    })
    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)
    subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", app], capture_output=True, check=False)
    from .desktop import LSREGISTER
    subprocess.run([LSREGISTER, "-f", app], capture_output=True, check=False)


def scan(prefix: str, env_name: str, before: dict | None = None, desktop: bool = True):
    """Create launchers for the programs in an environment's Start Menu (only new ones if `before`
    is given). Returns (added names, [(shortcut name, reason skipped)])."""
    added, skipped, seen = [], [], set()
    for path, mtime in sorted(find_shortcuts(prefix).items()):
        if before is not None and before.get(path) == mtime:
            continue
        name = os.path.splitext(os.path.basename(path))[0]
        if name.lower() in seen:
            continue
        if SKIP.search(name):
            skipped.append((name, "looks like an uninstaller/readme/web link"))
            continue
        try:
            with open(path, "rb") as f:
                link = parse_lnk(f.read())
        except (OSError, ValueError, struct.error, IndexError) as e:
            skipped.append((name, f"couldn't read the shortcut ({e})"))
            continue
        target = to_prefix_path(prefix, link["target"])
        if not target:
            skipped.append((name, f"its target {link['target'] or '(empty)'} isn't on this environment's C: drive"))
            continue
        if not target.lower().endswith((".exe", ".bat", ".msi")):
            skipped.append((name, "it doesn't point at a program"))
            continue
        try:
            create_launcher(name, env_name, target, link["args"], desktop=desktop)
        except (OSError, subprocess.CalledProcessError) as e:
            skipped.append((name, f"couldn't create the launcher ({e})"))
            continue
        seen.add(name.lower())
        added.append(name)
    return added, skipped


def add_new_programs(prefix: str, env_name: str, before: dict) -> list:
    """Create launchers for shortcuts that appeared since `before`. Returns the program names added."""
    return scan(prefix, env_name, before)[0]


def list_launchers() -> list:
    """Launchers for installed programs (not OmniLaunch's own helpers such as Extract with 7-Zip)."""
    folder = launcher_dir()
    if not os.path.isdir(folder):
        return []
    if runners.HOST_OS == "macos":
        found = [os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".app")]
    else:
        found = [os.path.join(folder, n) for n in os.listdir(folder)
                 if n.startswith("omnilaunch-") and n.endswith(".desktop")]
    return sorted(p for p in found if launcher_env(p))   # every program launcher names its environment


def launcher_env(path: str) -> str:
    """Which environment a launcher runs its program in ('' if unknown)."""
    try:
        if path.endswith(".app"):
            import plistlib
            with open(os.path.join(path, "Contents", "Info.plist"), "rb") as f:
                return plistlib.load(f).get("OmniLaunchEnv", "")
        with open(path) as f:
            for line in f:
                if line.startswith("X-OmniLaunch-Env="):
                    return line.split("=", 1)[1].strip()
                if line.startswith("Exec=") and " --env " in line:
                    return line.split(" --env ", 1)[1].split()[0].strip('"')
    except (OSError, ValueError):
        pass
    return ""


def remove_launcher(path: str) -> None:
    """Remove a launcher, its Desktop shortcut and its saved icon."""
    import shutil
    name = os.path.basename(path)
    desk = desktop_dir()
    if desk:
        shortcut = os.path.join(desk, name)
        if os.path.islink(shortcut) or (os.path.isfile(shortcut) and name.startswith("omnilaunch-")):
            os.unlink(shortcut)
    if os.path.isdir(path):
        from .desktop import LSREGISTER
        if runners.HOST_OS == "macos":
            subprocess.run([LSREGISTER, "-u", path], capture_output=True, check=False)
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.unlink(path)
        slug = name[len("omnilaunch-"):-len(".desktop")] if name.startswith("omnilaunch-") else ""
        for ext in (".png", ".ico"):
            icon = os.path.join(os.path.dirname(launcher_dir()), "icons", "omnilaunch", slug + ext)
            if slug and os.path.exists(icon):
                os.unlink(icon)


def remove_launchers_for_env(env_name: str) -> list:
    """Remove every launcher that runs in this environment. Returns their names."""
    removed = []
    for path in list_launchers():
        if launcher_env(path) == env_name:
            remove_launcher(path)
            removed.append(os.path.splitext(os.path.basename(path))[0].replace("omnilaunch-", ""))
    return removed


def launcher_name(path: str) -> str:
    """The app's name as the user sees it."""
    if path.endswith(".desktop"):
        try:
            with open(path) as f:
                for line in f:
                    if line.startswith("Name="):
                        return line[5:].strip()
        except OSError:
            pass
    return os.path.splitext(os.path.basename(path))[0]
