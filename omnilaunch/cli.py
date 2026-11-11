"""omnilaunch: run Linux, Windows and macOS apps through one command."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .detect import detect
from .runners import HOST_ARCH, UnsupportedApp, plan, which


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="omnilaunch", description=__doc__)
    p.add_argument("--version", action="version", version=f"omnilaunch {__version__}")
    sub = p.add_subparsers(dest="cmd")

    run = sub.add_parser("run", help="detect an app and run it with the right compatibility layer")
    run.add_argument("app")
    run.add_argument("--dry-run", action="store_true", help="show what would run, don't run it")
    run.add_argument("args", nargs=argparse.REMAINDER)

    info = sub.add_parser("info", help="show what platform an app was built for")
    info.add_argument("app")

    sub.add_parser("doctor", help="check which compatibility layers are installed")

    ns = p.parse_args(argv)
    if ns.cmd == "info":
        return _info(ns.app)
    if ns.cmd == "run":
        return _run(ns.app, ns.args, ns.dry_run)
    if ns.cmd == "doctor":
        return _doctor()
    p.print_help()
    return 0


def _info(app: str) -> int:
    try:
        i = detect(app)
    except FileNotFoundError:
        print(f"error: no such file: {app}", file=sys.stderr)
        return 2
    print(f"App:       {i.path}")
    if i.executable != i.path:
        print(f"Binary:    {i.executable}")
    print(f"Platform:  {i.platform.value}")
    print(f"Arch:      {i.arch}")
    print(f"Format:    {i.kind}")
    try:
        print(f"Runner:    {plan(i).runner}")
    except UnsupportedApp as e:
        print(f"Runner:    none available ({e})")
    return 0


def _run(app: str, args: list[str], dry_run: bool) -> int:
    try:
        info = detect(app)
        lp = plan(info, args)
    except FileNotFoundError:
        print(f"error: no such file: {app}", file=sys.stderr)
        return 2
    except UnsupportedApp as e:
        print(f"Can't run this app: {e}", file=sys.stderr)
        if e.hint:
            print(f"  -> {e.hint}", file=sys.stderr)
        return 3

    print(f"[omnilaunch] {info.platform.value} app ({info.kind}, {info.arch}) -> {lp.runner}", file=sys.stderr)
    for n in lp.notes:
        print(f"[omnilaunch] {n}", file=sys.stderr)
    if dry_run:
        for k, v in lp.env.items():
            print(f"{k}={v}")
        print(" ".join(lp.argv))
        return 0

    for k in ("WINEPREFIX", "DPREFIX", "STEAM_COMPAT_DATA_PATH"):
        if k in lp.env:
            os.makedirs(lp.env[k], exist_ok=True)
    env = {**os.environ, **lp.env}
    os.execvpe(lp.argv[0], lp.argv, env)  # replace ourselves with the app
    return 0  # unreachable


def _doctor() -> int:
    print(f"Host CPU: {HOST_ARCH}\n")
    checks = [
        ("Linux apps", ["(native)"], True),
        ("Windows apps (Wine)", ["wine", "wine64"], None),
        ("Windows games (Proton)", [], bool(os.environ.get("OMNILAUNCH_PROTON"))),
        ("macOS apps (Darling)", ["darling"], None),
        ("x86 apps on ARM", ["box64", "FEXInterpreter", "qemu-x86_64"], None),
    ]
    if HOST_ARCH in ("x86_64", "x86"):
        checks.pop()  # CPU translation only matters on ARM/RISC-V hosts
    for label, tools, forced in checks:
        found = forced if forced is not None else bool(which(*tools))
        mark = "OK " if found else "-- "
        extra = "" if found or not tools else f"  (install one of: {', '.join(tools)})"
        if label.startswith("Windows games") and not found:
            extra = "  (set OMNILAUNCH_PROTON=/path/to/proton)"
        print(f"  [{mark}] {label}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
