"""CI only: build the Box64 + Wine ARM64 image the way `omnilaunch setup box64` does, then run real
64-bit and 32-bit Windows programs through OmniLaunch's box64 runner (ARM64 emulated by QEMU on CI)."""

import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omnilaunch import provision, runners  # noqa: E402
from omnilaunch.detect import detect  # noqa: E402

work = tempfile.mkdtemp(prefix="omni-box64-")
src = os.path.join(work, "hello.c")
with open(src, "w") as f:
    f.write('#include <stdio.h>\nint main(void){printf("hello from windows, %d-bit\\n",'
            ' (int)(sizeof(void*)*8)); return 0;}\n')
programs = {}
for cc, name in (("x86_64-w64-mingw32-gcc", "hello64.exe"), ("i686-w64-mingw32-gcc", "hello32.exe")):
    out = os.path.join(work, name)
    subprocess.run([cc, "-O1", "-static", "-o", out, src], check=True)
    programs[name] = out
    print(f"built {name}: {detect(out).kind}, {detect(out).arch}", flush=True)

# A real-world program too: PuTTY's 64-bit plink (built like most Windows software, with MSVC/clang-cl).
plink = os.path.join(work, "plink.exe")
provision._fetch("https://the.earth.li/~sgtatham/putty/latest/w64/plink.exe", plink)
print(f"downloaded plink.exe: {detect(plink).kind}, {detect(plink).arch}", flush=True)

t = time.time()
provision._install_box64(provision.Installer(log=lambda m: print(m, flush=True)))
assert runners.box64_image_ready(), "image wasn't built"
print(f"image built in {time.time() - t:.0f}s", flush=True)

failures = 0
checks = [(name, exe, [], "hello from windows") for name, exe in programs.items()]
checks.append(("plink.exe (PuTTY, 64-bit)", plink, ["-V"], "plink: Release"))
for name, exe, args, expect in checks:
    lp = runners.plan(detect(exe), args, runner="box64", env_name=None)
    t = time.time()
    r = subprocess.run(lp.argv, capture_output=True, text=True, timeout=2700)
    tail = (r.stdout + r.stderr)[-1500:]
    ok = expect in r.stdout
    print(f"{'PASS' if ok else 'FAIL'}  {name} via Box64 + Wine on ARM64 ({time.time() - t:.0f}s): "
          f"{r.stdout.strip() or '(no output)'}", flush=True)
    if not ok:
        print(f"      exit code {r.returncode}; retrying with Wine/Box64 diagnostics on", flush=True)
        lp = runners.plan(detect(exe), runner="box64", verbose=True)
        r = subprocess.run(lp.argv, capture_output=True, text=True, timeout=2700)
        ok = expect in r.stdout
        found = [ln for ln in r.stdout.splitlines() if "Unimplemented" in ln or expect in ln]
        print(f"{'PASS' if ok else 'FAIL'}  {name} (second run): exit {r.returncode}, {found[-3:]}", flush=True)
        if not ok:
            failures += 1
            lines = [ln for ln in (r.stderr or "").splitlines() if ln.strip()]
            print("\n".join(lines[-80:]), flush=True)
sys.exit(1 if failures else 0)
