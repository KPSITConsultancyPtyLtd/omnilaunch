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

Expect: `OK` at the end (about 50 tests).

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

## 5. Windows apps (optional, needs Wine)

1. **Apple Silicon only:** install Rosetta 2:
   `softwareupdate --install-rosetta --agree-to-license`
2. Download the latest **wine-stable** `.tar.xz` from
   https://github.com/Gcenx/macOS_Wine_builds/releases, double-click it to unpack, and drag
   **Wine Stable.app** into Applications.
3. It isn't notarised, so clear the quarantine flag once:
   `xattr -dr com.apple.quarantine "/Applications/Wine Stable.app"`
   (If Wine later complains about GStreamer, install the GStreamer runtime from gstreamer.freedesktop.org.)
4. `./bin/omnilaunch doctor` should now show `[OK ] Windows apps (Wine)`.
5. Get a small Windows program, for example PuTTY's 64-bit `putty.exe` from
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

1. Install and start **OrbStack** (https://orbstack.dev) or **Docker Desktop**.
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

## What to send back

- The output of `./bin/omnilaunch doctor`
- Which steps matched "Expect" and which didn't, with the output of any that didn't
