# Testing OmniLaunch on a Mac

About 15 minutes for steps 1–4. Steps 5 and 6 need extra software and are optional.
Run everything in **Terminal**. When something doesn't match "Expect", copy the output.

## 1. Get the code

```bash
xcode-select --install        # only if git/python3 aren't installed yet (a dialog appears)
cd ~
git clone https://github.com/KPSITConsultancyPtyLtd/omnilaunch
cd omnilaunch
python3 --version             # Expect: 3.9 or newer
```

## 2. Run the automated tests

```bash
python3 -m unittest discover -s tests
```

Expect: `OK` at the end (about 85 tests).

## 3. Health check

```bash
./bin/omnilaunch doctor
```

Expect: `macOS on arm64` (Apple Silicon) or `macOS on x86_64` (Intel), then `[OK ] Mac apps`.
Wine, Rosetta and containers will show `--` until you install them; that's fine.

## 4. Mac apps (works with nothing extra installed)

```bash
./bin/omnilaunch info /bin/ls
```
Expect: `Platform: macos`, `Arch: universal (x86_64, arm64)` (or similar), `Runner: native`.

```bash
./bin/omnilaunch run /bin/echo hello from omnilaunch
```
Expect: the `[omnilaunch] ... -> native` line, then `hello from omnilaunch`.

```bash
./bin/omnilaunch run /System/Applications/Calculator.app
```
Expect: Calculator opens.

```bash
./bin/omnilaunch run /System/Applications/Calculator.app --dry-run
```
Expect: prints `/usr/bin/open /System/Applications/Calculator.app` and opens nothing.

Error handling (no crashes, a plain explanation each time):

```bash
echo hi > /tmp/fake.dmg && ./bin/omnilaunch run /tmp/fake.dmg     # Expect: "is an archive (DMG)"
./bin/omnilaunch run /tmp/does-not-exist                           # Expect: "no such file"
./bin/omnilaunch integrate                                         # Expect: "Linux-only for now"
```

## 5. Windows apps (optional; OmniLaunch installs Wine for you)

1. See what OmniLaunch can install, and preview the Wine install without doing it:

```bash
./bin/omnilaunch setup
./bin/omnilaunch setup wine --dry-run
```

2. Install it for real. On Apple Silicon it installs Rosetta 2 first (asks for your Mac
   password), then downloads Wine Stable into `~/Applications`:

```bash
./bin/omnilaunch setup wine
```

   (If Wine later complains about GStreamer, install the GStreamer runtime from gstreamer.freedesktop.org.)
3. `./bin/omnilaunch doctor` should now show `[OK ] Windows apps (Wine)`.
4. Get a small Windows program, for example PuTTY's 64-bit `putty.exe` from
   https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html, then:

```bash
./bin/omnilaunch info ~/Downloads/putty.exe     # Expect: windows, x86_64, PE32+ (GUI)
./bin/omnilaunch run  ~/Downloads/putty.exe
```

Expect: "First launch: setting up the Windows environment" (can take a minute or two), then
the PuTTY window appears. Then:

```bash
./bin/omnilaunch envs list      # Expect: a wine/putty-xxxxxxxx line with its size
```

If it fails, run it again with `-v` and copy the output.

## 6. Linux apps (optional, needs a container engine)

1. Run `./bin/omnilaunch setup container`. With Homebrew it installs Podman and starts its
   Linux VM; without Homebrew it tells you to install **OrbStack** (https://orbstack.dev) or
   **Docker Desktop**. Using OrbStack or Docker Desktop? Install and open it, and the
   commands below work as written. If you ended up with Podman, type `podman` wherever
   these steps say `docker`.
2. Make a real Linux program to test with (copies Debian's `ls` into the current folder):

```bash
mkdir -p ~/omni-test && cd ~/omni-test
docker run --rm -v "$PWD":/out debian:stable-slim cp /bin/ls /out/ls-linux
~/omnilaunch/bin/omnilaunch info ./ls-linux       # Expect: linux, arm64 or x86_64, ELF
~/omnilaunch/bin/omnilaunch run  ./ls-linux -la   # Expect: a directory listing of ~/omni-test
```

Optional, to test running an Intel Linux app on Apple Silicon:

```bash
docker run --rm --platform linux/amd64 -v "$PWD":/out debian:stable-slim cp /bin/ls /out/ls-x86
~/omnilaunch/bin/omnilaunch run ./ls-x86 -la      # Expect: listing, plus an "emulated" note
```

Note: keep test files under your home folder; Docker on a Mac may not see `/tmp` by default.

## 7. Linux apps with windows (optional, after step 6)

```bash
./bin/omnilaunch setup linux-gui
```

Expect: the XQuartz installer opens (finish it), then a container image builds for a few
minutes. **Log out of your Mac and back in once** afterwards. Then make a graphical Linux app
to test with:

```bash
cd ~/omni-test
docker run --rm -v "$PWD":/out omnilaunch/linux-gui sh -c \
  "apt-get update -qq && apt-get install -y -qq x11-apps >/dev/null && cp /usr/bin/xeyes /out/xeyes"
~/omnilaunch/bin/omnilaunch info ./xeyes      # Expect: Platform: linux, GUI: yes
~/omnilaunch/bin/omnilaunch run ./xeyes       # Expect: an XQuartz window with eyes following the mouse
```

## 8. Windows VM fallback (optional, only if you have Parallels Desktop)

```bash
./bin/omnilaunch vm set windows --tool parallels --name "Windows 11"   # your VM's exact name
./bin/omnilaunch run --runner vm ~/Downloads/putty.exe
```

Expect: the VM starts if needed and PuTTY opens inside Windows.

## What to send back

- The output of `./bin/omnilaunch doctor`
- Which steps matched "Expect" and which didn't, with the output of any that didn't
