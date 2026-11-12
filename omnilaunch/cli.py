"""omnilaunch: run Linux, Windows and macOS apps through one command."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

from . import __version__, desktop, envs, runners
from .detect import detect
from .runners import RUNNERS, UnsupportedApp, plan, which

RUN_FLAGS = {"--dry-run", "--verbose", "-v", "--notify"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="omnilaunch", description=__doc__)
    p.add_argument("--version", action="version", version=f"omnilaunch {__version__}")
    sub = p.add_subparsers(dest="cmd")

    run = sub.add_parser("run", help="detect an app and run it with the right compatibility layer",
                         description="Options may go before or after the app. "
                                     "Use -- to pass options to the app itself.")
    run.add_argument("--dry-run", action="store_true", help="show what would run, don't run it")
    run.add_argument("--runner", choices=RUNNERS, help="force a compatibility layer")
    run.add_argument("-v", "--verbose", action="store_true", help="show compatibility-layer debug output")
    run.add_argument("--notify", action="store_true", help="report errors as desktop notifications")
    run.add_argument("app")
    run.add_argument("args", nargs=argparse.REMAINDER)

    info = sub.add_parser("info", help="show what platform an app was built for")
    info.add_argument("app")

    sub.add_parser("doctor", help="check which compatibility layers are installed")

    e = sub.add_parser("envs", help="manage the isolated per-app environments")
    esub = e.add_subparsers(dest="envs_cmd")
    esub.add_parser("list", help="list environments")
    rm = esub.add_parser("remove", help="delete an environment (the app's settings and data inside it)")
    rm.add_argument("name", help="name as shown by 'envs list', e.g. wine/notepad-1a2b3c4d")

    integ = sub.add_parser("integrate", help="make double-clicking Windows/Mac apps open with OmniLaunch")
    integ.add_argument("--dry-run", action="store_true")
    integ.add_argument("--remove", action="store_true", help="undo the desktop integration")

    # Split off everything after `--` ourselves: argparse swallows the separator,
    # which would let our own flags steal arguments meant for the app.
    argv = list(sys.argv[1:] if argv is None else argv)
    passthrough: list[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, passthrough = argv[:cut], argv[cut + 1:]

    ns = p.parse_args(argv)
    if ns.cmd == "info":
        return _info(ns.app)
    if ns.cmd == "run":
        _absorb_trailing_flags(ns)
        ns.args += passthrough
        return _run(ns)
    if ns.cmd == "doctor":
        return _doctor()
    if ns.cmd == "envs":
        return _envs(ns, e)
    if ns.cmd == "integrate":
        return _integrate(ns)
    p.print_help()
    return 0


def _absorb_trailing_flags(ns: argparse.Namespace) -> None:
    """Let `run app --dry-run` work like `run --dry-run app`; anything after `--` goes to the app."""
    app_args, i, args = [], 0, ns.args
    while i < len(args):
        a = args[i]
        if a == "--":
            app_args.extend(args[i + 1:])
            break
        if a in RUN_FLAGS:
            setattr(ns, {"-v": "verbose"}.get(a, a.lstrip("-").replace("-", "_")), True)
        elif a == "--runner" and i + 1 < len(args) and args[i + 1] in RUNNERS:
            ns.runner = args[i + 1]
            i += 1
        elif a.startswith("--runner=") and a.split("=", 1)[1] in RUNNERS:
            ns.runner = a.split("=", 1)[1]
        else:
            app_args.append(a)
        i += 1
    ns.args = app_args


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
    arch = i.arch if not (i.arch == "universal" and i.archs) else f"universal ({', '.join(i.archs)})"
    print(f"Arch:      {arch}")
    print(f"Format:    {i.kind}")
    try:
        print(f"Runner:    {plan(i).runner}")
    except UnsupportedApp as e:
        print(f"Runner:    none available: {e}")
        if e.hint:
            print(f"           {e.hint}")
    return 0


def _fail(msg: str, hint: str, notify: bool, code: int) -> int:
    print(f"Can't run this app: {msg}", file=sys.stderr)
    if hint:
        print(f"  -> {hint}", file=sys.stderr)
    if notify:
        desktop.notify("Can't run this app", f"{msg}\n{hint}".strip())
    return code


def _run(ns: argparse.Namespace) -> int:
    try:
        info = detect(ns.app)
        lp = plan(info, ns.args, runner=ns.runner, verbose=ns.verbose)
    except FileNotFoundError:
        return _fail(f"no such file: {ns.app}", "", ns.notify, 2)
    except UnsupportedApp as e:
        return _fail(str(e), e.hint, ns.notify, 3)

    print(f"[omnilaunch] {info.platform.value} app ({info.kind}, {info.arch}) -> {lp.runner}", file=sys.stderr)
    for n in lp.notes:
        print(f"[omnilaunch] {n}", file=sys.stderr)
    if ns.dry_run:
        if lp.cwd:
            print(f"cd {lp.cwd}")
        for k, v in lp.env.items():
            print(f"{k}={v}")
        print(" ".join(lp.argv))
        return 0

    if lp.prefix:
        envs.record_launch(lp.prefix, info.path)
    env = {**os.environ, **lp.env}
    try:
        if os.name == "nt":
            # Windows has no real exec(): run the app as a child and pass its exit code through.
            return subprocess.call(lp.argv, cwd=lp.cwd or None, env=env)
        if lp.cwd:
            os.chdir(lp.cwd)
        os.execvpe(lp.argv[0], lp.argv, env)  # replace ourselves with the app
    except OSError as e:
        return _fail(f"launch failed: {e.strerror or e} ({lp.argv[0]})", "", ns.notify, 4)
    except KeyboardInterrupt:
        return 130
    return 0  # unreachable


def _doctor() -> int:
    os_name = {"macos": "macOS"}.get(runners.HOST_OS, runners.HOST_OS.capitalize())
    print(f"OmniLaunch {__version__} - {os_name} on {runners.HOST_ARCH}, Python {sys.version.split()[0]}\n")
    wine = runners.find_wine()
    engine = runners.find_container_engine()
    rows = []
    if runners.HOST_OS == "windows":
        distros = runners.wsl_distros()
        rows.append(("Windows apps", True, ""))
        rows.append(("Linux apps (WSL)", bool(distros),
                     "run `wsl --install` in an admin terminal" if not runners.find_wsl()
                     else "WSL is present but has no Linux installed: run `wsl --install -d Ubuntu`"))
        rows.append(("Linux apps (container, alternative to WSL)", bool(engine),
                     "optional: Docker Desktop or Podman Desktop"))
        rows.append(("Mac apps", False, "not possible on Windows"))
        rows.append(("Double-click .AppImage files",
                     desktop.windows_integrated(), "run: omnilaunch integrate"))
        if distros:
            print("  WSL Linux: " + ", ".join(distros) + "\n")
    elif runners.HOST_OS == "macos":
        rows.append(("Mac apps", True, ""))
        if runners.HOST_ARCH == "arm64":
            rows.append(("Intel Mac apps (Rosetta 2)", runners.rosetta_installed(),
                         "softwareupdate --install-rosetta --agree-to-license"))
        rows.append(("Windows apps (Wine)", bool(wine),
                     "download Wine Stable from github.com/Gcenx/macOS_Wine_builds"))
        rows.append(("Linux command-line apps (container)", bool(engine),
                     "install OrbStack, Docker Desktop or Podman, and start it"))
    else:
        proton = os.environ.get("OMNILAUNCH_PROTON")
        rows.append(("Linux apps", True, ""))
        rows.append(("Windows apps (Wine)", bool(wine), "install the 'wine' package"))
        rows.append(("Windows games (Proton)", bool(proton and os.path.exists(proton)),
                     "set OMNILAUNCH_PROTON=/path/to/proton"))
        rows.append(("macOS apps (Darling)", bool(which("darling")), "see https://www.darlinghq.org"))
        if runners.HOST_ARCH not in ("x86_64", "x86"):
            rows.append(("x86 apps on this CPU", bool(which("box64", "FEXInterpreter", "qemu-x86_64")),
                         "install box64 or FEX-Emu"))
        rows.append(("Double-click integration",
                     os.path.exists(os.path.join(desktop.applications_dir(), desktop.DESKTOP_ID)),
                     "run: omnilaunch integrate"))
    for label, ok, fix in rows:
        print(f"  [{'OK ' if ok else '-- '}] {label}" + ("" if ok else f"  ({fix})"))
    if wine:
        print(f"\n  Wine: {wine}")
    if engine:
        print(f"  Container engine: {engine}")
    print(f"  Environments stored in: {runners.data_dir()}")
    return 0


def _envs(ns: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if ns.envs_cmd == "remove":
        try:
            path = envs.remove_env(ns.name)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"Removed {path}")
        return 0
    if ns.envs_cmd != "list":
        parser.print_help()
        return 0
    items = envs.list_envs()
    if not items:
        print("No environments yet. They're created the first time you run a Windows or Mac app.")
        return 0
    for e in items:
        used = time.strftime("%Y-%m-%d", time.localtime(e.last_used)) if e.last_used else "never"
        print(f"{e.name:45} {envs.human(e.size):>8}  last used {used}  {e.app}")
    return 0


def _integrate(ns: argparse.Namespace) -> int:
    if ns.remove:
        print("Removed desktop integration." if desktop.unintegrate() else "Desktop integration wasn't installed.")
        return 0
    try:
        steps = desktop.integrate(dry_run=ns.dry_run)
    except desktop.NotSupportedHere as e:
        print(str(e))
        return 1
    for step in steps:
        print(("would " if ns.dry_run else "") + step)
    if not ns.dry_run:
        what = ".AppImage files" if runners.HOST_OS == "windows" else ".exe, .msi and Mac binaries"
        print(f"Done. Double-clicking {what} now opens them with OmniLaunch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
