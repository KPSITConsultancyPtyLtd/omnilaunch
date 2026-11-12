"""Hook OmniLaunch into the desktop (Windows, macOS, Linux) so double-clicking an app runs it,
and talk to the user with dialogs when there is no terminal."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from . import runners

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
    "application/vnd.android.package-archive",
]


def applications_dir() -> str:
    base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return os.path.join(base, "applications")


def _exec_quote(arg: str) -> str:
    """Quote one argument for a .desktop Exec line (the spec requires double quotes, not single)."""
    if arg and all(c.isalnum() or c in "/-_.=+:," for c in arg):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return '"' + escaped.replace("%", "%%") + '"'


def launcher_command() -> str:
    exe = shutil.which("omnilaunch")
    if exe:
        return _exec_quote(exe)
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Fall back to running the package from where it lives.
    return " ".join(_exec_quote(a) for a in ("env", f"PYTHONPATH={pkg_parent}", sys.executable, "-m", "omnilaunch"))


def desktop_entry() -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=OmniLaunch",
        "Comment=Run Windows, macOS and Linux apps",
        f"Exec={launcher_command()} run --gui %f",
        "Icon=application-x-executable",
        "Terminal=false",
        "NoDisplay=true",
        "Categories=System;Emulator;",
        f"MimeType={';'.join(MIME_TYPES)};",
        "",
    ])


class NotSupportedHere(Exception):
    pass


# ---------------------------------------------------------------- Windows: file association

WIN_PROGID = "OmniLaunch.App"
WIN_EXTENSIONS = [".AppImage", ".apk"]   # Linux and Android app files; Windows opens .exe/.msi itself


def windows_command() -> str:
    """The command Windows Explorer runs for an associated file (Windows command-line quoting)."""
    exe = shutil.which("omnilaunch")
    if exe:
        parts = [exe]
    else:
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "omnilaunch")
        parts = [sys.executable, script]
    return subprocess.list2cmdline([*parts, "run", "--gui"]) + ' "%1"'


def _winreg():
    import winreg  # only exists on Windows
    return winreg


def windows_integrated() -> bool:
    if runners.HOST_OS != "windows":
        return False
    try:
        reg = _winreg()
        with reg.OpenKey(reg.HKEY_CURRENT_USER, rf"Software\Classes\{WIN_PROGID}\shell\open\command"):
            return True
    except OSError:
        return False


def _integrate_windows(dry_run: bool) -> list[str]:
    cmd = windows_command()
    steps = [f"register {WIN_PROGID} (current user only) -> {cmd}"]
    steps += [f"associate {ext} with {WIN_PROGID}" for ext in WIN_EXTENSIONS]
    if dry_run:
        return steps
    reg = _winreg()
    base = r"Software\Classes"
    with reg.CreateKey(reg.HKEY_CURRENT_USER, rf"{base}\{WIN_PROGID}") as k:
        reg.SetValueEx(k, "", 0, reg.REG_SZ, "App opened with OmniLaunch")
    with reg.CreateKey(reg.HKEY_CURRENT_USER, rf"{base}\{WIN_PROGID}\shell\open\command") as k:
        reg.SetValueEx(k, "", 0, reg.REG_SZ, cmd)
    for ext in WIN_EXTENSIONS:
        with reg.CreateKey(reg.HKEY_CURRENT_USER, rf"{base}\{ext}\OpenWithProgids") as k:
            reg.SetValueEx(k, WIN_PROGID, 0, reg.REG_NONE, b"")
        with reg.CreateKey(reg.HKEY_CURRENT_USER, rf"{base}\{ext}") as k:
            reg.SetValueEx(k, "", 0, reg.REG_SZ, WIN_PROGID)
    _notify_shell_of_change()
    return steps


def _unintegrate_windows() -> bool:
    if not windows_integrated():
        return False
    reg = _winreg()
    base = r"Software\Classes"
    for sub in ("shell\\open\\command", "shell\\open", "shell", ""):
        try:
            reg.DeleteKey(reg.HKEY_CURRENT_USER, rf"{base}\{WIN_PROGID}" + (f"\\{sub}" if sub else ""))
        except OSError:
            pass
    for ext in WIN_EXTENSIONS:
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, rf"{base}\{ext}\OpenWithProgids", 0, reg.KEY_SET_VALUE) as k:
                reg.DeleteValue(k, WIN_PROGID)
        except OSError:
            pass
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, rf"{base}\{ext}", 0, reg.KEY_ALL_ACCESS) as k:
                if reg.QueryValueEx(k, "")[0] == WIN_PROGID:
                    reg.DeleteValue(k, "")
        except OSError:
            pass
    _notify_shell_of_change()
    return True


def _notify_shell_of_change() -> None:
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        pass


# ---------------------------------------------------------------- Linux: .desktop file + MIME types

def integrate(dry_run: bool = False) -> list[str]:
    if runners.HOST_OS == "windows":
        return _integrate_windows(dry_run)
    if runners.HOST_OS == "macos":
        return _integrate_macos(dry_run)
    if runners.HOST_OS != "linux":
        raise NotSupportedHere(f"Double-click integration isn't available on {runners.HOST_OS}.")
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
    if runners.HOST_OS == "windows":
        return _unintegrate_windows()
    if runners.HOST_OS == "macos":
        return _unintegrate_macos()
    path = os.path.join(applications_dir(), DESKTOP_ID)
    if not os.path.exists(path):
        return False
    os.remove(path)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", applications_dir()], check=False)
    return True


# ---------------------------------------------------------------- macOS: an app that opens app files

MAC_APP = os.path.expanduser("~/Applications/OmniLaunch.app")
MAC_BUNDLE_ID = "com.kpsit.omnilaunch"
LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/"
              "Support/lsregister")
MAC_EXTENSIONS = ["exe", "msi", "apk", "AppImage"]
MAC_TYPES = [   # declared so macOS knows these file types even if nothing else on the Mac does
    ("com.microsoft.windows-executable", "Windows app", ["exe"], ["public.data", "public.executable"]),
    ("com.kpsit.omnilaunch.msi", "Windows installer", ["msi"], ["public.data"]),
    ("com.android.package-archive", "Android app", ["apk"], ["public.data", "public.archive"]),
    ("com.kpsit.omnilaunch.appimage", "Linux app", ["AppImage", "appimage"], ["public.data", "public.executable"]),
]
# Finder starts apps with a minimal PATH; add the usual places Homebrew, OrbStack and XQuartz live.
MAC_PATH = "/opt/homebrew/bin:/usr/local/bin:$HOME/.orbstack/bin:/opt/X11/bin:$PATH"

# JavaScript for Automation: make OmniLaunch.app the default for each extension, then report the result.
SET_DEFAULTS_JXA = r"""
ObjC.import('AppKit');
ObjC.import('UniformTypeIdentifiers');
function run(argv) {
  var app = $.NSURL.fileURLWithPath(argv[0]);
  var ws = $.NSWorkspace.sharedWorkspace;
  var setDefaults = argv[1] === "set";
  for (var i = 2; setDefaults && i < argv.length; i++) {
    var t = $.UTType.typeWithFilenameExtension(argv[i]);
    if (!t || t.isNil()) continue;
    ws.setDefaultApplicationAtURLToOpenContentTypeCompletionHandler(app, t, null);
  }
  if (setDefaults) delay(3);
  var out = [];
  for (var j = 2; j < argv.length; j++) {
    var u = $.UTType.typeWithFilenameExtension(argv[j]);
    var h = (u && !u.isNil()) ? ws.URLForApplicationToOpenContentType(u) : null;
    out.push(argv[j] + "=" + ((h && !h.isNil()) ? h.path.js : "none"));
  }
  return out.join("\n");
}
"""


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def mac_shell_command() -> str:
    """The shell command OmniLaunch.app runs for each opened file (the file is appended, quoted)."""
    import shlex
    exe = shutil.which("omnilaunch")
    if exe:
        parts = [exe]
    else:
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "omnilaunch")
        parts = [sys.executable, script]
    return 'export PATH="' + MAC_PATH + '"; ' + " ".join(shlex.quote(p) for p in parts) + " run --gui"


def mac_applescript() -> str:
    import shlex
    log = os.path.join(runners.data_dir(), "omnilaunch.log")
    cmd = _applescript_string(mac_shell_command() + " ")
    tail = _applescript_string(" >> " + shlex.quote(log) + " 2>&1 &")
    return "\n".join([
        "on open theFiles",
        "\trepeat with f in theFiles",
        f"\t\tdo shell script {cmd} & quoted form of (POSIX path of f) & {tail}",
        "\tend repeat",
        "end open",
        "",
        "on run",
        '\tdisplay dialog "OmniLaunch opens Windows, Linux and Android apps on your Mac. Just double-click '
        'an .exe, .msi, .apk or .AppImage file." buttons {"OK"} default button "OK" with title "OmniLaunch"',
        "end run",
        "",
    ])


def mac_info_plist_updates(plist: dict) -> dict:
    plist.update({
        "CFBundleIdentifier": MAC_BUNDLE_ID,
        "CFBundleName": "OmniLaunch",
        "CFBundleDisplayName": "OmniLaunch",
        "CFBundleDocumentTypes": [{
            "CFBundleTypeName": label,
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Default",
            "LSItemContentTypes": [uti],
            "CFBundleTypeExtensions": exts,
        } for uti, label, exts, _ in MAC_TYPES],
        "UTImportedTypeDeclarations": [{
            "UTTypeIdentifier": uti,
            "UTTypeDescription": label,
            "UTTypeConformsTo": conforms,
            "UTTypeTagSpecification": {"public.filename-extension": exts},
        } for uti, label, exts, conforms in MAC_TYPES],
    })
    return plist


def mac_default_handlers(set_defaults: bool = False) -> dict:
    """Which app opens each file type OmniLaunch handles; optionally make OmniLaunch the default first."""
    r = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", SET_DEFAULTS_JXA, MAC_APP,
                        "set" if set_defaults else "get", *MAC_EXTENSIONS], capture_output=True, text=True)
    return dict(line.partition("=")[::2] for line in (r.stdout or "").strip().splitlines() if "=" in line)


def _integrate_macos(dry_run: bool) -> list:
    import plistlib
    import tempfile
    steps = [f"build {MAC_APP} (opens .exe, .msi, .apk and .AppImage files)",
             "register it with macOS and make it the default app for those file types"]
    if dry_run:
        return steps
    os.makedirs(os.path.dirname(MAC_APP), exist_ok=True)
    os.makedirs(runners.data_dir(), exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".applescript", delete=False) as f:
        f.write(mac_applescript())
        src = f.name
    try:
        if os.path.exists(MAC_APP):
            shutil.rmtree(MAC_APP)
        subprocess.run(["/usr/bin/osacompile", "-o", MAC_APP, src], check=True)
    finally:
        os.unlink(src)
    plist_path = os.path.join(MAC_APP, "Contents", "Info.plist")
    with open(plist_path, "rb") as f:
        plist = plistlib.load(f)
    with open(plist_path, "wb") as f:
        plistlib.dump(mac_info_plist_updates(plist), f)
    # Editing Info.plist breaks the applet's signature; re-sign it locally so macOS accepts it.
    subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", MAC_APP], check=False)
    subprocess.run([LSREGISTER, "-f", MAC_APP], check=False)
    for ext, handler in mac_default_handlers(set_defaults=True).items():
        ok = handler.rstrip("/") == MAC_APP.rstrip("/")
        steps.append(f".{ext}: " + ("opens with OmniLaunch" if ok else
                                    "not set yet: approve the macOS prompt, or use Get Info > Open with > Change All"))
    return steps


def mac_integrated() -> bool:
    return runners.HOST_OS == "macos" and os.path.isdir(MAC_APP)


def _unintegrate_macos() -> bool:
    if not os.path.isdir(MAC_APP):
        return False
    subprocess.run([LSREGISTER, "-u", MAC_APP], check=False)
    shutil.rmtree(MAC_APP)
    return True


# ---------------------------------------------------------------- dialogs (no terminal needed)

def _osascript(*lines: str, args: tuple = ()) -> subprocess.CompletedProcess:
    argv = ["/usr/bin/osascript"]
    for line in lines:
        argv += ["-e", line]
    return subprocess.run([*argv, *args], capture_output=True, text=True)


def ask(title: str, message: str, yes: str = "Install", no: str = "Not Now") -> bool:
    """Ask a yes/no question in a dialog. Returns False if no dialog could be shown."""
    try:
        if runners.HOST_OS == "macos":
            r = _osascript("on run argv",
                           "display dialog (item 2 of argv) with title (item 1 of argv) buttons "
                           "{(item 4 of argv), (item 3 of argv)} default button (item 3 of argv) with icon note",
                           "end run", args=(title, message, yes, no))
            return r.returncode == 0 and ("button returned:" + yes) in r.stdout
        if runners.HOST_OS == "windows":
            import ctypes
            return ctypes.windll.user32.MessageBoxW(None, message, title, 0x4 | 0x20) == 6   # YESNO, ? icon
        if shutil.which("zenity"):
            return subprocess.run(["zenity", "--question", "--title", title, "--text", message,
                                   "--ok-label", yes, "--cancel-label", no]).returncode == 0
        if shutil.which("kdialog"):
            return subprocess.run(["kdialog", "--title", title, "--yesno", message,
                                   "--yes-label", yes, "--no-label", no]).returncode == 0
    except Exception:
        pass
    return False


def alert(title: str, message: str) -> None:
    """Show an error the user must see (they double-clicked something and nothing else will tell them)."""
    try:
        if runners.HOST_OS == "macos":
            _osascript("on run argv", "display alert (item 1 of argv) message (item 2 of argv) as critical",
                       "end run", args=(title, message))
            return
        if runners.HOST_OS == "windows":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)   # MB_ICONERROR
            return
        for tool, argv in (("zenity", ["zenity", "--error", "--title", title, "--text", message]),
                           ("kdialog", ["kdialog", "--title", title, "--error", message])):
            if shutil.which(tool):
                subprocess.run(argv)
                return
    except Exception:
        pass
    notify(title, message)


def notify(title: str, body: str) -> None:
    """Show a desktop notification (progress messages when there's no terminal)."""
    try:
        _notify(title, body)
    except Exception:
        pass   # a notification failing must never hide the real error


def _notify(title: str, body: str) -> None:
    if runners.HOST_OS == "macos":
        _osascript("on run argv", "display notification (item 2 of argv) with title (item 1 of argv)", "end run",
                   args=(title, body))
    elif runners.HOST_OS == "windows":
        pass   # Windows toasts need a registered app identity; OmniLaunch uses dialogs there instead
    elif shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "OmniLaunch", title, body], check=False)


def run_as_admin(argv: list) -> list:
    """Turn a command that needs admin rights into one that asks via the system's own password prompt."""
    import shlex
    if runners.HOST_OS == "macos":
        return ["/usr/bin/osascript", "-e", "on run argv", "-e",
                "do shell script (item 1 of argv) with administrator privileges", "-e", "end run",
                " ".join(shlex.quote(a) for a in argv)]
    if runners.HOST_OS == "linux" and shutil.which("pkexec"):
        return ["pkexec", *argv]
    return ["sudo", *argv]
