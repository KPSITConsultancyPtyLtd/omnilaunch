"""Put the actions of installed Windows programs where the host OS expects them.

macOS: Finder Quick Actions (right-click > Quick Actions) as Automator services in ~/Library/Services,
and an "Extract with 7-Zip" app that is the default for archive types macOS can't open itself, so a
double-click extracts in place like Archive Utility does for .zip.
Linux: Nautilus scripts, KDE Dolphin service menus, and a default handler for those archive types.
Windows: 7-Zip adds its own right-click menu, so nothing is needed.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import uuid

from . import actions, runners

SERVICES_DIR = os.path.expanduser("~/Library/Services")
EXTRACT_BUNDLE_ID = "com.kpsit.omnilaunch.extract"
PBS = "/System/Library/CoreServices/pbs"


def _command_parts() -> list:
    exe = shutil.which("omnilaunch")
    if exe:
        return [exe]
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "omnilaunch")
    return [sys.executable, script]


def _shell_command(action_id: str) -> str:
    """The shell line a menu runs; the chosen files arrive as "$@"."""
    from .desktop import MAC_PATH
    log = os.path.join(runners.data_dir(), "omnilaunch.log")
    parts = " ".join(shlex.quote(p) for p in _command_parts())
    path = f'export PATH="{MAC_PATH}"; ' if runners.HOST_OS == "macos" else ""
    return f'{path}{parts} do --gui {action_id} "$@" >> {shlex.quote(log)} 2>&1'


# ---------------------------------------------------------------- macOS

def _workflow_name(action) -> str:
    return f"{action.label}.workflow"


def mac_workflow(action) -> tuple:
    """(Info.plist, document.wflow) for a Finder Quick Action that runs `omnilaunch do <action>`."""
    info = {"NSServices": [{
        "NSMenuItem": {"default": action.label},
        "NSMessage": "runWorkflowAsService",
        "NSRequiredContext": {"NSApplicationIdentifier": "com.apple.finder"},
        "NSSendFileTypes": ["public.item"],
    }]}
    ids = [str(uuid.uuid4()).upper() for _ in range(3)]
    document = {
        "AMApplicationBuild": "521", "AMApplicationVersion": "2.10", "AMDocumentVersion": "2",
        "actions": [{"action": {
            "AMAccepts": {"Container": "List", "Optional": True, "Types": ["com.apple.cocoa.string"]},
            "AMActionVersion": "2.0.3", "AMApplication": ["Automator"],
            "AMParameterProperties": {"COMMAND_STRING": {}, "CheckedForUserDefaultShell": {}, "inputMethod": {},
                                      "shell": {}, "source": {}},
            "AMProvides": {"Container": "List", "Types": ["com.apple.cocoa.string"]},
            "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
            "ActionName": "Run Shell Script",
            "ActionParameters": {"COMMAND_STRING": _shell_command(action.id), "CheckedForUserDefaultShell": True,
                                 "inputMethod": 1, "shell": "/bin/zsh", "source": ""},
            "BundleIdentifier": "com.apple.RunShellScript", "CFBundleVersion": "2.0.3",
            "CanShowSelectedItemsWhenRun": False, "CanShowWhenRun": True, "Category": ["AMCategoryUtilities"],
            "Class Name": "RunShellScriptAction", "InputUUID": ids[0], "Keywords": ["Shell", "Script"],
            "OutputUUID": ids[1], "UUID": ids[2], "UnlocalizedApplications": ["Automator"],
            "arguments": {}, "isViewVisible": 1, "location": "309.000000:316.000000",
        }, "isViewVisible": 1}],
        "connectors": {},
        "workflowMetaData": {
            "applicationBundleIDsByPath": {}, "applicationPaths": [],
            "inputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "outputTypeIdentifier": "com.apple.Automator.nothing",
            "presentationMode": 15, "processesInput": False,
            "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": False, "systemImageName": "NSActionTemplate",
            "useAutomaticInputType": False, "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }
    return info, document


def _install_mac(found: list) -> list:
    done = []
    os.makedirs(SERVICES_DIR, exist_ok=True)
    for action, _env, _exe in found:
        bundle = os.path.join(SERVICES_DIR, _workflow_name(action))
        if os.path.exists(bundle):
            shutil.rmtree(bundle)
        os.makedirs(os.path.join(bundle, "Contents"))
        info, document = mac_workflow(action)
        with open(os.path.join(bundle, "Contents", "Info.plist"), "wb") as f:
            plistlib.dump(info, f)
        with open(os.path.join(bundle, "Contents", "document.wflow"), "wb") as f:
            plistlib.dump(document, f)
        done.append(f"Quick Action: {action.label}")
    if os.path.exists(PBS):
        subprocess.run([PBS, "-update"], capture_output=True, check=False)   # refresh Finder's menus now
    extract = next((a for a, _e, _x in found if a.double_click), None)
    if extract:
        done.append(_install_mac_double_click(extract))
    return done


def _install_mac_double_click(action) -> str:
    """An app that extracts archives on double-click, made the default for types macOS can't open."""
    from .desktop import LSREGISTER, _applescript_string
    from .shortcuts import launcher_dir
    app = os.path.join(launcher_dir(), "Extract with 7-Zip.app")
    os.makedirs(launcher_dir(), exist_ok=True)
    cmd = _shell_command(action.id).replace(' "$@"', "")
    script = "\n".join([
        "on open theFiles",
        '\tset fileArgs to ""',
        "\trepeat with f in theFiles",
        '\t\tset fileArgs to fileArgs & " " & quoted form of (POSIX path of f)',
        "\tend repeat",
        f"\tdo shell script {_applescript_string(cmd.split(' >> ')[0])} & fileArgs & "
        f"{_applescript_string(' >> ' + cmd.split(' >> ')[1] + ' &')}",
        "end open",
        "",
        "on run",
        '\tdisplay dialog "Double-click a .7z, .rar or similar archive and OmniLaunch extracts it next to the '
        'archive with 7-Zip." buttons {"OK"} default button "OK" with title "Extract with 7-Zip"',
        "end run",
        "",
    ])
    if os.path.exists(app):
        shutil.rmtree(app)
    subprocess.run(["/usr/bin/osacompile", "-o", app, "-e", script], check=True, capture_output=True)
    plist_path = os.path.join(app, "Contents", "Info.plist")
    with open(plist_path, "rb") as f:
        plist = plistlib.load(f)
    plist.update({
        "CFBundleIdentifier": EXTRACT_BUNDLE_ID, "CFBundleName": "Extract with 7-Zip",
        "CFBundleDocumentTypes": [{"CFBundleTypeName": "Archive", "CFBundleTypeRole": "Viewer",
                                   "LSHandlerRank": "Default", "CFBundleTypeExtensions": list(action.double_click)}],
    })
    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)
    found = actions.find_program(action)
    if found:
        from . import icons
        ico = icons.extract_ico(found[1].replace("7z.exe", "7zFM.exe")) or icons.extract_ico(found[1])
        if ico:
            icons.mac_set_app_icon(app, ico)
    subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", app], capture_output=True, check=False)
    subprocess.run([LSREGISTER, "-f", app], capture_output=True, check=False)
    from .desktop import mac_default_handlers
    try:
        handlers = mac_default_handlers(set_defaults=True, extensions=list(action.double_click),
                                        bundle_id=EXTRACT_BUNDLE_ID)
    except (OSError, RuntimeError):
        handlers = {}
    ours = sorted(e for e, h in handlers.items() if h.lower() == EXTRACT_BUNDLE_ID)
    return ("Double-click extracts: " + ", ".join("." + e for e in ours)) if ours else \
        "Double-click extract app created (approve the macOS prompt to make it the default)"


def _remove_mac() -> list:
    removed = []
    for action in actions.ACTIONS:
        bundle = os.path.join(SERVICES_DIR, _workflow_name(action))
        if os.path.exists(bundle):
            shutil.rmtree(bundle)
            removed.append(action.label)
    from .shortcuts import launcher_dir
    app = os.path.join(launcher_dir(), "Extract with 7-Zip.app")
    if os.path.exists(app):
        from .desktop import LSREGISTER
        subprocess.run([LSREGISTER, "-u", app], capture_output=True, check=False)
        shutil.rmtree(app)
        removed.append("Extract with 7-Zip")
    if os.path.exists(PBS):
        subprocess.run([PBS, "-update"], capture_output=True, check=False)
    return removed


# ---------------------------------------------------------------- Linux

def _data_home() -> str:
    return os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))


ARCHIVE_MIME = {"7z": "application/x-7z-compressed", "rar": "application/vnd.rar", "cab": "application/vnd.ms-cab-compressed",
                "arj": "application/x-arj", "lzh": "application/x-lha", "wim": "application/x-ms-wim",
                "zst": "application/zstd", "rpm": "application/x-rpm", "deb": "application/vnd.debian.binary-package"}


def _install_linux(found: list) -> list:
    from .desktop import _exec_quote
    done = []
    scripts = os.path.join(_data_home(), "nautilus", "scripts")
    os.makedirs(scripts, exist_ok=True)
    for action, _env, _exe in found:
        path = os.path.join(scripts, action.label)
        with open(path, "w") as f:
            f.write("#!/bin/sh\n# Added by OmniLaunch\n" + _shell_command(action.id) + "\n")
        os.chmod(path, 0o755)
    done.append("Files (Nautilus): right-click > Scripts")
    menus = os.path.join(_data_home(), "kio", "servicemenus")
    os.makedirs(menus, exist_ok=True)
    parts = " ".join(_exec_quote(p) for p in _command_parts())
    lines = ["[Desktop Entry]", "Type=Service", "MimeType=all/allfiles;inode/directory;",
             "X-KDE-Submenu=OmniLaunch", "Actions=" + ";".join(a.id for a, _e, _x in found) + ";", ""]
    for action, _env, _exe in found:
        lines += [f"[Desktop Action {action.id}]", f"Name={action.label}",
                  f"Exec={parts} do --gui {action.id} %F", ""]
    kde = os.path.join(menus, "omnilaunch-actions.desktop")
    with open(kde, "w") as f:
        f.write("\n".join(lines))
    os.chmod(kde, 0o755)   # Dolphin only runs executable service menus
    done.append("Dolphin (KDE): right-click > OmniLaunch")
    extract = next((a for a, _e, _x in found if a.double_click), None)
    if extract:
        apps = os.path.join(_data_home(), "applications")
        os.makedirs(apps, exist_ok=True)
        mimes = [ARCHIVE_MIME[e] for e in extract.double_click if e in ARCHIVE_MIME]
        with open(os.path.join(apps, "omnilaunch-extract.desktop"), "w") as f:
            f.write("\n".join(["[Desktop Entry]", "Type=Application", "Name=Extract with 7-Zip",
                               f"Exec={parts} do --gui extract %F", "Icon=package-x-generic", "Terminal=false",
                               "NoDisplay=true", "MimeType=" + ";".join(mimes) + ";", ""]))
        if shutil.which("xdg-mime"):
            subprocess.run(["xdg-mime", "default", "omnilaunch-extract.desktop", *mimes], capture_output=True,
                           check=False)
        done.append("Double-click extracts: " + ", ".join("." + e for e in extract.double_click if e in ARCHIVE_MIME))
    return done


def _remove_linux() -> list:
    removed = []
    for action in actions.ACTIONS:
        path = os.path.join(_data_home(), "nautilus", "scripts", action.label)
        if os.path.exists(path):
            os.unlink(path)
            removed.append(action.label)
    for path in (os.path.join(_data_home(), "kio", "servicemenus", "omnilaunch-actions.desktop"),
                 os.path.join(_data_home(), "applications", "omnilaunch-extract.desktop")):
        if os.path.exists(path):
            os.unlink(path)
            removed.append(os.path.basename(path))
    return removed


# ---------------------------------------------------------------- entry points

def install() -> list:
    """Add menus for every action whose program is installed. Returns what was added."""
    found = actions.available()
    if not found:
        return []
    if runners.HOST_OS == "macos":
        return _install_mac(found)
    if runners.HOST_OS == "linux":
        return _install_linux(found)
    return ["Windows: 7-Zip adds its own right-click menu"]


def remove() -> list:
    if runners.HOST_OS == "macos":
        return _remove_mac()
    if runners.HOST_OS == "linux":
        return _remove_linux()
    return []
