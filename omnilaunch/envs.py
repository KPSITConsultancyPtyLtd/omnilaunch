"""List and remove the isolated per-app environments OmniLaunch creates."""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass

from .runners import data_dir

META = "omnilaunch.json"


@dataclass
class Env:
    name: str        # e.g. "wine/notepad-1a2b3c4d"
    path: str
    app: str
    last_used: float
    size: int


def record_launch(prefix: str, app_path: str) -> None:
    os.makedirs(prefix, exist_ok=True)
    try:
        with open(os.path.join(prefix, META), "w") as f:
            json.dump({"app": app_path, "last_used": time.time()}, f)
    except OSError:
        pass  # bookkeeping only; never block a launch


def list_envs() -> list[Env]:
    root = os.path.join(data_dir(), "prefixes")
    out: list[Env] = []
    if not os.path.isdir(root):
        return out
    for runner in sorted(os.listdir(root)):
        rdir = os.path.join(root, runner)
        if not os.path.isdir(rdir):
            continue
        for name in sorted(os.listdir(rdir)):
            path = os.path.join(rdir, name)
            if not os.path.isdir(path):
                continue
            meta = {}
            try:
                with open(os.path.join(path, META)) as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                pass
            out.append(Env(f"{runner}/{name}", path, meta.get("app", "?"),
                           meta.get("last_used", 0), _du(path)))
    return out


def remove_env(name: str) -> str:
    root = os.path.realpath(os.path.join(data_dir(), "prefixes"))
    path = os.path.realpath(os.path.join(root, name))
    # Only ever delete a direct runner/app folder inside our own data directory.
    if os.path.dirname(os.path.dirname(path)) != root or not os.path.isdir(path):
        raise ValueError(f"No environment named '{name}'. See: omnilaunch envs list")
    shutil.rmtree(path)
    return path


def _du(path: str) -> int:
    total = 0
    for dirpath, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
