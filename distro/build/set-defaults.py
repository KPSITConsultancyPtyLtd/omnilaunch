#!/usr/bin/env python3
"""Sets system-wide default apps (users can still change them).

usage: set-defaults.py <file with type=app.desktop lines> [...]

Writes /etc/xdg/mimeapps.list and /etc/xdg/kde-mimeapps.list (KDE reads the second first), keeping any
other defaults already there. In a value, alternatives separated by | pick the first app installed.
Paths can be redirected for tests with OMNILAUNCH_DEFAULTS_ROOT."""
import os
import sys

ROOT = os.environ.get("OMNILAUNCH_DEFAULTS_ROOT", "/")
APP_DIRS = [os.path.join(ROOT, "usr/share/applications")]
TARGETS = [os.path.join(ROOT, "etc/xdg/mimeapps.list"), os.path.join(ROOT, "etc/xdg/kde-mimeapps.list")]


def read_wanted(paths):
    wanted = {}
    for path in paths:
        with open(path) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if "=" in line:
                    mime, apps = (s.strip() for s in line.split("=", 1))
                    wanted[mime] = choose(apps.split("|"))
    return wanted


def choose(apps):
    for app in apps:
        if any(os.path.exists(os.path.join(d, app)) for d in APP_DIRS):
            return app
    return apps[0]


def merge(path, wanted):
    lines = []
    if os.path.exists(path):
        with open(path) as f:
            lines = f.read().splitlines()
    out, section = [], None
    for line in lines:
        if line.startswith("["):
            section = line.strip()
        if section == "[Default Applications]" and line.split("=", 1)[0].strip() in wanted:
            continue                                    # replaced below
        out.append(line)
    if "[Default Applications]" not in [l.strip() for l in out]:
        out.append("[Default Applications]")
    i = [l.strip() for l in out].index("[Default Applications]") + 1
    out[i:i] = [f"{m}={a}" for m, a in sorted(wanted.items())]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    wanted = read_wanted(argv)
    for target in TARGETS:
        merge(target, wanted)
    print(f"{len(wanted)} file types have default apps")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
