"""Run apps inside a virtual machine when no compatibility layer can (e.g. a Windows app Wine can't run).

OmniLaunch doesn't build VMs itself: it drives one you already have in Parallels Desktop (Mac) or
VirtualBox (Mac, Linux, Windows), copies or shares the app into it, and starts it there.
"""

from __future__ import annotations

import json
import ntpath
import os
from dataclasses import asdict, dataclass

from . import runners

TOOLS = ("parallels", "virtualbox")
GUEST_DIR = "C:\\OmniLaunch"


@dataclass
class VMConfig:
    os: str                 # which kind of apps it runs; currently "windows"
    tool: str               # "parallels" or "virtualbox"
    name: str               # the VM's name in that tool
    user: str = ""          # VirtualBox only: Windows account to run apps as
    password_file: str = ""  # VirtualBox only: file holding that account's password


def _config_path() -> str:
    return os.path.join(runners.data_dir(), "vms.json")


def load() -> dict[str, VMConfig]:
    try:
        with open(_config_path()) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for key, value in raw.items():
        try:
            out[key] = VMConfig(**value)
        except TypeError:
            continue
    return out


def save(configs: dict[str, VMConfig]) -> None:
    os.makedirs(os.path.dirname(_config_path()), exist_ok=True)
    with open(_config_path(), "w") as f:
        json.dump({k: asdict(v) for k, v in configs.items()}, f, indent=2)


def configure(os_name: str, tool: str, name: str, user: str = "", password_file: str = "") -> VMConfig:
    if os_name != "windows":
        raise ValueError("Only Windows VMs are supported so far.")
    if tool not in TOOLS:
        raise ValueError(f"tool must be one of: {', '.join(TOOLS)}")
    if tool == "virtualbox" and not (user and password_file):
        raise ValueError("VirtualBox needs --user and --password-file (a file containing the Windows password).")
    if password_file:
        password_file = os.path.abspath(os.path.expanduser(password_file))
        if not os.path.isfile(password_file):
            raise ValueError(f"No such password file: {password_file}")
    cfg = VMConfig(os_name, tool, name, user, password_file)
    configs = load()
    configs[os_name] = cfg
    save(configs)
    return cfg


def remove(os_name: str) -> bool:
    configs = load()
    if os_name not in configs:
        return False
    del configs[os_name]
    save(configs)
    return True


def get(os_name: str) -> VMConfig | None:
    return load().get(os_name)


def find_tool(tool: str) -> str | None:
    if tool == "parallels":
        return runners.which("prlctl") or (
            "/usr/local/bin/prlctl" if os.path.exists("/usr/local/bin/prlctl") else None)
    for candidate in ("VBoxManage", "vboxmanage"):
        found = runners.which(candidate)
        if found:
            return found
    for path in ("/Applications/VirtualBox.app/Contents/MacOS/VBoxManage",
                 "C:\\Program Files\\Oracle\\VirtualBox\\VBoxManage.exe"):
        if os.path.exists(path):
            return path
    return None


def parallels_guest_path(host_path: str, home: str | None = None) -> str | None:
    """Parallels shares the Mac's home folder with Windows as \\\\Mac\\Home."""
    home = os.path.realpath(home or os.path.expanduser("~"))
    real = os.path.realpath(host_path)
    if real != home and not real.startswith(home + os.sep):
        return None
    rel = os.path.relpath(real, home)
    return "\\\\Mac\\Home\\" + rel.replace("/", "\\")


def plan_windows_app(info, args: list[str]):
    cfg = get("windows")
    if not cfg:
        raise runners.UnsupportedApp(
            "No Windows virtual machine is set up for OmniLaunch.",
            "Create a Windows VM in Parallels Desktop or VirtualBox, then run: "
            "omnilaunch vm set windows --tool parallels --name \"<VM name>\"")
    tool = find_tool(cfg.tool)
    if not tool:
        raise runners.UnsupportedApp(f"{cfg.tool.capitalize()} isn't installed, but the Windows VM uses it.")
    notes = [f"Running inside the '{cfg.name}' virtual machine ({cfg.tool}). It starts the VM if needed."]

    if cfg.tool == "parallels":
        guest = parallels_guest_path(info.executable)
        if not guest:
            raise runners.UnsupportedApp("Parallels can only see apps inside your home folder.",
                                         "Move the app somewhere under your home folder (e.g. Downloads).")
        lp = runners.LaunchPlan("vm (parallels)", [tool, "exec", cfg.name, "--current-user", guest, *args],
                                notes=notes)
        lp.pre = [([tool, "start", cfg.name], False)]   # fails harmlessly if already running
        return lp

    # VirtualBox: copy the app (with its folder, if small) into the VM, then start it as the configured user.
    folder = os.path.dirname(info.executable)
    app_name = os.path.splitext(os.path.basename(info.executable))[0] or "app"
    guest_folder = ntpath.join(GUEST_DIR, app_name)
    guest_exe = ntpath.join(guest_folder, os.path.basename(info.executable))
    auth = ["--username", cfg.user, "--passwordfile", cfg.password_file]
    whole_folder = _folder_size(folder, COPY_FOLDER_LIMIT) <= COPY_FOLDER_LIMIT
    sources = [os.path.join(folder, n) for n in sorted(os.listdir(folder))] if whole_folder else [info.executable]
    notes.append("Copied into the VM at " + guest_folder +
                 (" with the files next to it." if whole_folder else " (its folder is too big to copy, so the app only)."))
    notes.append("The VM must be signed in to Windows for the app's window to appear.")
    lp = runners.LaunchPlan(
        "vm (virtualbox)",
        [tool, "guestcontrol", cfg.name, "start", *auth, "--exe", guest_exe, "--",
         ntpath.basename(guest_exe), *args], notes=notes)
    lp.pre = [
        ([tool, "startvm", cfg.name, "--type", "gui"], False),     # fails harmlessly if already running
        ([tool, "guestcontrol", cfg.name, "mkdir", *auth, "--parents", guest_folder], True),
        ([tool, "guestcontrol", cfg.name, "copyto", *auth, "--recursive",
          "--target-directory", guest_folder + "\\", *sources], True),
    ]
    return lp


COPY_FOLDER_LIMIT = 500 * 1024 * 1024


def _folder_size(folder: str, stop_after: int) -> int:
    total = 0
    for dirpath, _, files in os.walk(folder):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
            if total > stop_after:
                return total
    return total
