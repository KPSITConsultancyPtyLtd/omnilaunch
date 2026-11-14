"""CI only (Linux with Wine installed): the 7-Zip scenario end to end, with the real 7-Zip.

1. Download today's 64-bit 7-Zip installer from 7-zip.org and run it silently through OmniLaunch.
2. Check 7-Zip became an app (launcher + Desktop shortcut) and its right-click menus were set up.
3. Compress a file with `omnilaunch do compress-7z`, extract it with `do extract`, and compare.
4. Time two extractions back to back: the second should be faster (the Wine server is kept warm).
"""

import filecmp
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omnilaunch import actions, cli, provision, runners, shortcuts  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail else ""), flush=True)
    if not ok:
        FAILS.append(name)


work = tempfile.mkdtemp(prefix="omni-7z-")
page = provision._fetch("https://www.7-zip.org/download.html").decode("utf-8", "replace")
rel = re.search(r'href="(a/7z\d+-x64\.exe)"', page).group(1)
installer = os.path.join(work, os.path.basename(rel))
provision._fetch("https://www.7-zip.org/" + rel, installer)
print(f"downloaded {os.path.basename(installer)} ({os.path.getsize(installer) // 1024} KB)", flush=True)

# 1. install it the way a double-click would, but silently (/S is the 7-Zip installer's silent switch)
t = time.time()
code = cli.main(["run", installer, "/S"])
print(f"installer finished in {time.time() - t:.0f}s (exit {code})", flush=True)
found = actions.find_program(actions.get("extract"))
check("7-Zip installed into its own environment", bool(found), found[1] if found else "7z.exe not found")
if not found:
    sys.exit(1)

# 2. it became an app, and its jobs are in the file manager's menus
names = [shortcuts.launcher_name(p) for p in shortcuts.list_launchers()]
check("7-Zip File Manager added to the app menu (and nothing else)", names == ["7-Zip File Manager"],
      ", ".join(names))
desk = os.listdir(os.environ["XDG_DESKTOP_DIR"]) if os.path.isdir(os.environ["XDG_DESKTOP_DIR"]) else []
check("Desktop shortcut created", any("7-zip" in d for d in desk), ", ".join(desk))
scripts = os.path.join(os.environ["XDG_DATA_HOME"], "nautilus", "scripts")
check("right-click actions set up", os.path.exists(os.path.join(scripts, "Extract Here with 7-Zip")),
      ", ".join(os.listdir(scripts)) if os.path.isdir(scripts) else "no scripts folder")

# 3. compress and extract round trip, real 7-Zip under Wine
data = os.path.join(work, "report.txt")
with open(data, "w") as f:
    f.write("OmniLaunch round trip\n" * 2000)
t = time.time()
code = cli.main(["do", "compress-7z", data])
archive = os.path.join(work, "report.7z")
check("compress to .7z", code == 0 and os.path.exists(archive), f"{time.time() - t:.1f}s")
# Stop the Wine server so the first extract below starts cold, as after a reboot.
server = runners.find_wineserver(runners.find_wine())
prefix = os.path.join(runners.data_dir(), "prefixes", *found[0].split("/"))
subprocess.run([server, "-k"], env={**os.environ, "WINEPREFIX": prefix}, capture_output=True)
time.sleep(2)
timings = []
for attempt in (1, 2):
    t = time.time()
    code = cli.main(["do", "extract", archive])
    timings.append(time.time() - t)
    out = os.path.join(work, "report" if attempt == 1 else "report 2", "report.txt")
    check(f"extract .7z (run {attempt})", code == 0 and os.path.exists(out) and filecmp.cmp(data, out, shallow=False),
          f"{timings[-1]:.1f}s")
check("second extract is faster (Wine kept warm)", timings[1] <= timings[0],
      f"{timings[0]:.1f}s then {timings[1]:.1f}s")
code = cli.main(["do", "compress-zip", data])
check("compress to .zip", code == 0 and os.path.exists(os.path.join(work, "report.zip")))

print(f"\n{len(FAILS)} failed" if FAILS else "\nall 7-Zip checks passed")
sys.exit(1 if FAILS else 0)
