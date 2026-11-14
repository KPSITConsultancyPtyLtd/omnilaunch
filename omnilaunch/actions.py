"""Context-aware actions: do a Windows program's job on a file without opening its window.

A profile says which installed Windows program can do what to which files ("7-Zip can extract
.7z/.rar/... and compress anything"), and how to ask its command-line tool to do it. OmniLaunch
finds the environment the program was installed in, runs the job there, and reports back with a
notification. Native hooks (Finder Quick Actions, double-click, Linux file-manager menus) call
`omnilaunch do <action> <files>`.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Callable

from . import runners

ARCHIVES = ("7z", "rar", "iso", "cab", "arj", "lzh", "lha", "wim", "xz", "bz2", "gz", "tgz", "tar", "zst",
            "zip", "dmg", "z", "lzma", "cpio", "rpm", "deb", "squashfs", "001")
# macOS opens these itself (Archive Utility); OmniLaunch leaves their double-click alone.
MAC_NATIVE_ARCHIVES = ("zip", "gz", "tgz", "tar", "bz2", "xz", "cpio", "dmg", "iso")


@dataclass
class Action:
    id: str                                # used on the command line: omnilaunch do <id> <files>
    label: str                             # shown in menus
    program: str                           # display name of the Windows program
    exe_candidates: tuple                  # paths under C:\ where the program's command-line tool lives
    accepts: tuple                         # file extensions (lower case), or () for any file or folder
    build: Callable                        # (exe, files) -> (args for the tool, output path, done message)
    double_click: tuple = ()               # extensions a double-click should run this action for


def _win(path: str) -> str:
    """A path on this computer as the Windows program sees it (drive Z: is this computer's filesystem)."""
    if runners.HOST_OS == "windows":
        return path
    return "Z:" + os.path.abspath(path).replace("/", "\\")


def _unique(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{base} {n}{ext}"):
        n += 1
    return f"{base} {n}{ext}"


def _archive_stem(path: str) -> str:
    name = os.path.basename(path)
    lower = name.lower()
    for double in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".7z.001", ".zip.001"):
        if lower.endswith(double):
            return name[:-len(double)]
    return os.path.splitext(name)[0]


def _extract(exe: str, files: list):
    archive = files[0]
    dest = _unique(os.path.join(os.path.dirname(os.path.abspath(archive)), _archive_stem(archive)))
    return (["x", "-y", "-o" + _win(dest), _win(archive)], dest,
            f"Extracted {os.path.basename(archive)} to {os.path.basename(dest)}")


def _compress(kind: str):
    def build(exe: str, files: list):
        folder = os.path.dirname(os.path.abspath(files[0]))
        stem = os.path.splitext(os.path.basename(files[0].rstrip("/\\")))[0] if len(files) == 1 else "Archive"
        if len(files) == 1 and os.path.isdir(files[0]):
            stem = os.path.basename(files[0].rstrip("/\\"))
        out = _unique(os.path.join(folder, f"{stem}.{kind}"))
        return (["a", f"-t{kind}", "-y", _win(out), *[_win(f) for f in files]], out,
                f"Compressed {len(files)} item{'s' if len(files) > 1 else ''} into {os.path.basename(out)}")
    return build


SEVEN_ZIP = ("Program Files\\7-Zip\\7z.exe", "Program Files (x86)\\7-Zip\\7z.exe",
             "Program Files\\7-Zip\\7za.exe", "Program Files\\7-Zip\\7zr.exe")
ACTIONS = [
    Action("extract", "Extract Here with 7-Zip", "7-Zip", SEVEN_ZIP, ARCHIVES, _extract,
           double_click=tuple(e for e in ARCHIVES if e not in MAC_NATIVE_ARCHIVES)),
    Action("compress-7z", "Compress to .7z with 7-Zip", "7-Zip", SEVEN_ZIP, (), _compress("7z")),
    Action("compress-zip", "Compress to .zip with 7-Zip", "7-Zip", SEVEN_ZIP, (), _compress("zip")),
]


def get(action_id: str) -> Action | None:
    return next((a for a in ACTIONS if a.id == action_id), None)


def find_program(action: Action) -> tuple | None:
    """(environment name, path to the program's tool) for the first environment that has it."""
    if runners.HOST_OS == "windows":
        for cand in action.exe_candidates:
            for root in (os.environ.get("SystemDrive", "C:") + "\\",):
                path = os.path.join(root, cand)
                if os.path.exists(path):
                    return ("", path)
        return None
    from . import envs
    for env in sorted(envs.list_envs(), key=lambda e: -e.last_used):
        if env.name.split("/")[0] not in ("wine", "proton", "box64"):
            continue
        from .shortcuts import to_prefix_path
        for cand in action.exe_candidates:
            path = to_prefix_path(env.path, "C:\\" + cand)
            if path and os.path.isfile(path):
                return (env.name, path)
    return None


def available() -> list:
    """Actions whose program is installed, as (action, env name, tool path)."""
    out = []
    for action in ACTIONS:
        found = find_program(action)
        if found:
            out.append((action, *found))
    return out


def run(action_id: str, files: list, gui: bool = False) -> int:
    """Do the action on the files. Returns an exit code; reports with notifications when `gui`."""
    import sys
    from . import desktop
    from .detect import detect
    action = get(action_id)

    def fail(msg: str, code: int) -> int:
        print(f"omnilaunch do {action_id}: {msg}", file=sys.stderr)
        if gui:
            desktop.alert("OmniLaunch", msg)
        return code

    if not action:
        return fail(f"unknown action. Available: {', '.join(a.id for a in ACTIONS)}", 2)
    files = [os.path.abspath(f) for f in files]
    print(f"[omnilaunch] do {action_id}: {' '.join(files)}", file=sys.stderr, flush=True)
    missing = [f for f in files if not os.path.exists(f)]
    if not files or missing:
        return fail(f"no such file: {missing[0] if missing else '(none given)'}", 2)
    if action.accepts:
        wrong = [f for f in files if not any(f.lower().endswith("." + e) for e in action.accepts)]
        if wrong:
            return fail(f"{os.path.basename(wrong[0])} isn't something {action.program} can extract.", 2)
    found = find_program(action)
    if not found:
        return fail(f"{action.program} isn't installed in OmniLaunch yet. Install it by double-clicking its "
                    f"Windows installer, then try again.", 3)
    env_name, exe = found
    print(f"[omnilaunch] action {action_id}: {' '.join(files)} (with {exe})", file=sys.stderr, flush=True)
    results = []
    jobs = [[f] for f in files] if action_id == "extract" else [files]   # one archive at a time
    for job in jobs:
        args, output, message = action.build(exe, job)
        try:
            if runners.HOST_OS == "windows":
                argv, env, cwd = [exe, *args], None, os.path.dirname(job[0])
            else:
                lp = runners.plan(detect(exe), args, env_name=env_name)
                for step, _required in lp.pre:
                    if callable(step):
                        step()
                    else:
                        runners.run_collect(step, check=False)
                argv, env, cwd = lp.argv, {**os.environ, **lp.env}, os.path.dirname(job[0])
            r = runners.run_collect(argv, env=env, cwd=cwd)
        except (OSError, runners.UnsupportedApp) as e:
            return fail(f"couldn't run {action.program}: {e}", 4)
        if r.returncode != 0 or not os.path.exists(output):
            detail = [ln for ln in (r.stdout + r.stderr).splitlines() if "ERROR" in ln.upper()][-1:] or \
                     (r.stdout + r.stderr).strip().splitlines()[-1:]
            return fail(f"{action.program} couldn't do that: {' '.join(detail) or 'exit code ' + str(r.returncode)}", 5)
        results.append(message)
        print(f"[omnilaunch] {message}", file=sys.stderr, flush=True)
    if runners.HOST_OS != "windows" and lp.runner.startswith("wine"):
        runners.warm_wineserver(lp.argv[0], lp.prefix)   # the next action starts faster
    if gui:
        desktop.notify("OmniLaunch", results[0] if len(results) == 1 else f"Extracted {len(results)} archives")
    return 0
