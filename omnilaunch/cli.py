"""omnilaunch: run Linux, Windows and macOS apps through one command."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

from . import __version__, desktop, envs, provision, runners, vms
from .detect import detect
from .runners import RUNNERS, UnsupportedApp, plan, which

RUN_FLAGS = {"--dry-run", "--verbose", "-v", "--notify", "--gui", "--yes", "-y"}


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
    run.add_argument("--gui", action="store_true",
                     help="no terminal: ask and report with dialogs (used when an app is double-clicked)")
    run.add_argument("--notify", action="store_true", help=argparse.SUPPRESS)   # old name for --gui
    run.add_argument("--env", help="run inside an existing environment (see `omnilaunch envs list`)")
    run.add_argument("-y", "--yes", action="store_true", help="install missing helpers without asking")
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

    ap = sub.add_parser("apps", help="Windows programs installed through OmniLaunch, as apps on this computer")
    asub = ap.add_subparsers(dest="apps_cmd")
    asub.add_parser("list", help="list the launchers OmniLaunch has created")
    asc = asub.add_parser("scan", help="create launchers (and Desktop shortcuts) for installed programs")
    asc.add_argument("env", nargs="?", help="one environment (see `envs list`); default: all of them")
    asc.add_argument("--no-desktop", action="store_true", help="don't put shortcuts on the Desktop")

    st = sub.add_parser("setup", help="install the helpers OmniLaunch uses (Wine, WSL, containers, ...)")
    st.add_argument("component", nargs="?", help="what to set up, or 'all'; leave out to see the list")
    st.add_argument("--dry-run", action="store_true", help="show the steps without doing them")

    vm = sub.add_parser("vm", help="use a virtual machine for apps nothing else can run")
    vsub = vm.add_subparsers(dest="vm_cmd")
    vset = vsub.add_parser("set", help="tell OmniLaunch which VM to use")
    vset.add_argument("os", choices=["windows"])
    vset.add_argument("--tool", required=True, choices=list(vms.TOOLS))
    vset.add_argument("--name", required=True, help="the VM's name in Parallels/VirtualBox")
    vset.add_argument("--user", default="", help="VirtualBox: Windows account to run apps as")
    vset.add_argument("--password-file", default="", help="VirtualBox: file containing that account's password")
    vsub.add_parser("show", help="show the configured VMs")
    vrm = vsub.add_parser("remove", help="stop using a VM")
    vrm.add_argument("os", choices=["windows"])

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
    if ns.cmd == "setup":
        return _setup(ns)
    if ns.cmd == "apps":
        return _apps(ns, ap)
    if ns.cmd == "vm":
        return _vm(ns, vm)
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
            setattr(ns, {"-v": "verbose", "-y": "yes"}.get(a, a.lstrip("-").replace("-", "_")), True)
        elif a == "--runner" and i + 1 < len(args) and args[i + 1] in RUNNERS:
            ns.runner = args[i + 1]
            i += 1
        elif a == "--env" and i + 1 < len(args):
            ns.env = args[i + 1]
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
    if i.package:
        print(f"Package:   {i.package}")
    if i.platform.value == "linux":
        print(f"GUI:       {'yes (opens windows)' if i.gui else 'no (command-line app)'}")
    try:
        print(f"Runner:    {plan(i).runner}")
    except UnsupportedApp as e:
        print(f"Runner:    none available: {e}")
        if e.hint:
            print(f"           {e.hint}")
    return 0


def _fail(msg: str, hint: str, gui: bool, code: int) -> int:
    print(f"Can't run this app: {msg}", file=sys.stderr)
    if hint:
        print(f"  -> {hint}", file=sys.stderr)
    if gui:
        desktop.alert("OmniLaunch can't open this app", f"{msg}\n\n{hint}".strip())
    return code


def _run(ns: argparse.Namespace) -> int:
    gui = ns.gui or ns.notify
    if gui:
        print(f"[omnilaunch] {time.strftime('%Y-%m-%d %H:%M:%S')} opening {ns.app}", file=sys.stderr, flush=True)
    offered: set = set()
    while True:
        try:
            info = detect(ns.app)
            lp = plan(info, ns.args, runner=ns.runner, verbose=ns.verbose, env_name=ns.env)
            break
        except FileNotFoundError:
            return _fail(f"no such file: {ns.app}", "", gui, 2)
        except UnsupportedApp as e:
            # A helper is missing: offer to install it, then try again (once per helper).
            if e.needs and e.needs not in offered and not ns.dry_run:
                offered.add(e.needs)
                if provision.offer(e.needs, assume_yes=ns.yes, gui=gui):
                    continue
            hint = e.hint
            if e.needs and e.needs in provision.components():
                hint = f"Run: omnilaunch setup {e.needs}"
            return _fail(str(e), hint, gui, 3)

    print(f"[omnilaunch] {info.platform.value} app ({info.kind}, {info.arch}) -> {lp.runner}", file=sys.stderr)
    for n in lp.notes:
        print(f"[omnilaunch] {n}", file=sys.stderr)
    if ns.dry_run:
        for step, _required in lp.pre:
            print("before: " + (step.__doc__ or step.__name__ if callable(step) else _quote(step)))
        if lp.cwd:
            print(f"cd {_quote([lp.cwd])}")
        for k, v in lp.env.items():
            print(f"{k}={_quote([v])}")
        print(_quote(lp.argv))
        return 0

    if lp.prefix:
        envs.record_launch(lp.prefix, info.path)
    for step, required in lp.pre:
        try:
            if callable(step):
                if gui and step.__doc__:
                    desktop.notify("OmniLaunch", step.__doc__[0].upper() + step.__doc__[1:] + "...")
                ok = bool(step())
            else:
                ok = subprocess.run(step, capture_output=not required).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        if required and not ok:
            what = step.__doc__ if callable(step) else " ".join(step)
            return _fail(f"preparing the launch failed: {what}", "", gui, 4)
    env = {**os.environ, **lp.env}
    try:
        if lp.prefix and lp.runner.startswith(("wine", "proton")):
            return _run_windows_app(lp, env, gui)
        if os.name == "nt":
            # Windows has no real exec(): run the app as a child and pass its exit code through.
            return subprocess.call(lp.argv, cwd=lp.cwd or None, env=env)
        if lp.cwd:
            os.chdir(lp.cwd)
        sys.stdout.flush()
        sys.stderr.flush()
        os.execvpe(lp.argv[0], lp.argv, env)  # replace ourselves with the app (unflushed output would be lost)
    except OSError as e:
        return _fail(f"launch failed: {e.strerror or e} ({lp.argv[0]})", "", gui, 4)
    except KeyboardInterrupt:
        return 130
    return 0  # unreachable


def _run_windows_app(lp, env: dict, gui: bool) -> int:
    """Run a Windows app and wait, so programs it installs can be added to this computer's app list."""
    from . import shortcuts
    before = shortcuts.find_shortcuts(lp.prefix)
    code = subprocess.call(lp.argv, cwd=lp.cwd or None, env=env)
    # Installers often hand off to a child process and exit early: wait for everything in the environment.
    wineserver = os.path.join(os.path.dirname(lp.argv[0]), "wineserver")
    if "WINEPREFIX" in env and os.path.exists(wineserver):
        try:
            subprocess.run([wineserver, "-w"], env=env, timeout=3600)
        except (OSError, subprocess.SubprocessError):
            pass
    env_name = os.path.relpath(lp.prefix, os.path.join(runners.data_dir(), "prefixes")).replace(os.sep, "/")
    added = shortcuts.add_new_programs(lp.prefix, env_name, before)
    if added:
        where = "Applications > OmniLaunch Apps" if runners.HOST_OS == "macos" else "your app menu"
        if shortcuts.desktop_shortcuts_enabled() and shortcuts.desktop_dir():
            where += " and on your Desktop"
        msg = f"Installed {', '.join(added)}. Find it in {where}."
        print(f"[omnilaunch] {msg}", file=sys.stderr)
        if gui:
            desktop.notify("OmniLaunch", msg)
    return code


def _quote(argv: list) -> str:
    """Show a command the way this computer's shell would need it typed."""
    import shlex
    return subprocess.list2cmdline(argv) if os.name == "nt" else " ".join(shlex.quote(a) for a in argv)


def _doctor() -> int:
    os_name = {"macos": "macOS"}.get(runners.HOST_OS, runners.HOST_OS.capitalize())
    print(f"OmniLaunch {__version__} - {os_name} on {runners.HOST_ARCH}, Python {sys.version.split()[0]}\n")
    wine = runners.find_wine()
    engine = runners.find_container_engine()
    rows = []
    if runners.HOST_OS == "windows":
        distros = runners.wsl_distros()
        rows.append(("Windows apps", True, ""))
        rows.append(("Linux apps (WSL)", bool(distros), "omnilaunch setup wsl"))
        rows.append(("Linux apps (container, alternative to WSL)", bool(engine),
                     "optional: Docker Desktop or Podman Desktop"))
        rows.append(("Mac apps", False, "not possible on Windows"))
        rows.append(("Double-click .apk/.AppImage files",
                     desktop.windows_integrated(), "omnilaunch integrate"))
        if distros:
            print("  WSL Linux: " + ", ".join(distros) + "\n")
    elif runners.HOST_OS == "macos":
        rows.append(("Mac apps", True, ""))
        if runners.HOST_ARCH == "arm64":
            rows.append(("Intel Mac apps (Rosetta 2)", runners.rosetta_installed(), "omnilaunch setup rosetta"))
        rows.append(("Windows apps (Wine)", bool(wine), "omnilaunch setup wine"))
        rows.append(("Linux command-line apps (container)", bool(engine),
                     "omnilaunch setup container"))
        rows.append(("Linux graphical apps (XQuartz + GUI image)",
                     runners.xquartz_installed() and runners.linux_gui_image_ready(),
                     "omnilaunch setup linux-gui"))
    else:
        proton = os.environ.get("OMNILAUNCH_PROTON")
        rows.append(("Linux apps", True, ""))
        rows.append(("Windows apps (Wine)", bool(wine), "omnilaunch setup wine"))
        rows.append(("Windows games (Proton via umu-launcher)",
                     bool(runners.find_umu() or (proton and os.path.exists(proton))), "omnilaunch setup proton"))
        rows.append(("macOS apps (Darling)", bool(which("darling")), "see https://www.darlinghq.org"))
        if runners.HOST_ARCH not in ("x86_64", "x86"):
            rows.append(("x86 apps on this CPU", bool(which("box64", "FEXInterpreter", "qemu-x86_64")),
                         "install box64 or FEX-Emu"))
        rows.append(("Double-click integration",
                     os.path.exists(os.path.join(desktop.applications_dir(), desktop.DESKTOP_ID)),
                     "run: omnilaunch integrate"))
    from . import android
    rows.append(("Android apps (.apk)", bool(android.waydroid()) or android.emulator_ready(),
                 "omnilaunch setup android"))
    if runners.HOST_OS == "macos":
        rows.append(("Double-click .exe/.msi/.apk/.AppImage", desktop.mac_integrated(), "omnilaunch integrate"))
    for label, ok, fix in rows:
        print(f"  [{'OK ' if ok else '-- '}] {label}" + ("" if ok else f"  ({fix})"))
    cfg = vms.get("windows")
    if runners.HOST_OS != "windows":
        rows_vm = ("Windows VM fallback", bool(cfg and vms.find_tool(cfg.tool)),
                   "optional: omnilaunch vm set windows --tool parallels|virtualbox --name ...")
        print(f"  [{'OK ' if rows_vm[1] else '-- '}] {rows_vm[0]}" + ("" if rows_vm[1] else f"  ({rows_vm[2]})"))
    missing = [c.name for c in provision.components().values() if not c.is_installed()]
    if missing:
        print(f"\n  To install what's missing: omnilaunch setup all   (or one of: {', '.join(missing)})")
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


def _setup(ns: argparse.Namespace) -> int:
    comps = provision.components()
    if not ns.component:
        if not comps:
            print("Nothing to set up on this computer.")
            return 0
        print("Helpers OmniLaunch can set up here:\n")
        for c in comps.values():
            print(f"  [{'OK ' if c.is_installed() else '-- '}] {c.name:10} {c.label}")
        print("\nInstall one with `omnilaunch setup <name>`, or everything with `omnilaunch setup all`.")
        return 0
    names = [n for n, c in comps.items() if not c.is_installed()] if ns.component == "all" else [ns.component]
    if not names:
        print("Everything is already set up.")
        return 0
    code, done = 0, set()   # `done` stops shared dependencies being installed twice
    for name in names:
        if name in done:
            continue
        try:
            provision.install(name, dry_run=ns.dry_run, _seen=done)
        except provision.ManualStep as e:
            print(f"{name}: needs a manual step: {e}", file=sys.stderr)
            code = 1
        except provision.SetupError as e:
            print(f"{name}: setup failed: {e}", file=sys.stderr)
            code = 1
    return code


def _apps(ns: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from . import shortcuts
    if ns.apps_cmd == "list":
        items = shortcuts.list_launchers()
        if not items:
            print("No apps yet. Programs you install with OmniLaunch appear here.")
        for path in items:
            print(path)
        return 0
    if ns.apps_cmd != "scan":
        parser.print_help()
        return 0
    windows_envs = [e for e in envs.list_envs() if e.name.split("/")[0] in ("wine", "proton")]
    if ns.env:
        windows_envs = [e for e in windows_envs if e.name == ns.env.strip("/")]
        if not windows_envs:
            print(f"error: no Windows environment called '{ns.env}'. See: omnilaunch envs list", file=sys.stderr)
            return 2
    total = 0
    for e in windows_envs:
        added, skipped = shortcuts.scan(e.path, e.name, desktop=not ns.no_desktop)
        if not added and not skipped:
            continue
        print(f"{e.name}:")
        for name in added:
            print(f"  + {name}")
        for name, why in skipped:
            print(f"  - skipped {name}: {why}")
        total += len(added)
    if total:
        print(f"\nAdded {total} app(s) to {shortcuts.launcher_dir()}" +
              ("" if ns.no_desktop or not shortcuts.desktop_shortcuts_enabled() else " and your Desktop") + ".")
    else:
        print("No installed programs found in the Start Menu of any environment.")
    return 0


def _vm(ns: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if ns.vm_cmd == "set":
        try:
            cfg = vms.configure(ns.os, ns.tool, ns.name, ns.user, ns.password_file)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"Windows apps that Wine can't run will use the '{cfg.name}' VM ({cfg.tool}).")
        print("Use it any time with: omnilaunch run --runner vm <app.exe>")
        if not vms.find_tool(cfg.tool):
            print(f"Note: {cfg.tool} doesn't seem to be installed yet.")
        return 0
    if ns.vm_cmd == "remove":
        print("Removed." if vms.remove(ns.os) else "No VM was set up for that.")
        return 0
    if ns.vm_cmd == "show":
        configs = vms.load()
        if not configs:
            print("No VMs set up. See: omnilaunch vm set --help")
        for key, cfg in configs.items():
            print(f"{key}: '{cfg.name}' via {cfg.tool}" + (f" as user {cfg.user}" if cfg.user else ""))
        return 0
    parser.print_help()
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
        what = {"windows": ".apk and .AppImage files",
                "macos": ".exe, .msi, .apk and .AppImage files"}.get(runners.HOST_OS, ".exe, .msi, .apk and Mac files")
        print(f"Done. Double-clicking {what} now opens them with OmniLaunch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
