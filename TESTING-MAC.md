# Testing OmniLaunch on a Mac

The goal: **double-click a Windows `.exe` and it runs.** Steps 1–3 take about 10 minutes;
step 4 downloads Wine (about 400 MB). When something doesn't match "Expect", copy what you see.

## 1. Get OmniLaunch and turn on double-click

In **Terminal**:

```bash
xcode-select --install        # only if git/python3 aren't installed yet (a dialog appears)
cd ~
git clone https://github.com/KPSITConsultancyPtyLtd/omnilaunch
cd omnilaunch
./bin/omnilaunch integrate
```

Expect: lines ending `.exe: opens with OmniLaunch`, and the same for `.msi`, `.apk` and
`.AppImage`. Recent macOS versions show a prompt for each file type asking whether to use
OmniLaunch: choose **Use "OmniLaunch"**. If a line says "not set yet", do it once by hand:
right-click any `.exe` > Get Info > Open with > OmniLaunch > **Change All**.

## 2. Health check

```bash
./bin/omnilaunch doctor
```

Expect `[OK ] Mac apps` and `[OK ] Double-click .exe/.msi/.apk/.AppImage`. The others show
`--` until they're needed. You don't have to install them yourself.

## 3. A Mac `.dmg` (works with nothing extra)

`.dmg` files still open with Finder as usual when double-clicked. To have OmniLaunch install
one for you, download any app's `.dmg` (for example VLC from videolan.org) and run:

```bash
./bin/omnilaunch run ~/Downloads/vlc-*.dmg
```

Expect: VLC is copied into Applications and opens.

## 4. The main test: double-click a Windows `.exe`

1. Download a Windows program, for example **PuTTY** (64-bit `putty.exe` from
   https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html) or the **7-Zip** installer
   from https://www.7-zip.org.
2. **Double-click it in Finder.**
3. Expect a dialog saying OmniLaunch needs Wine (about 400 MB) and asking whether to install
   it now. Click **Install**. On Apple Silicon you'll be asked for your Mac password once
   (that's Rosetta 2, which Wine needs).
4. Wait a few minutes (a notification says when Wine is ready). The first launch then sets
   up the app's Windows environment, which takes another minute.
5. Expect: the program opens in its own window.

Double-click it again later: it opens straight away.

**If you used an installer (like 7-Zip):** finish the installer. The installed program then
appears on your **Desktop** and in **Applications > OmniLaunch Apps** (also in Launchpad and
Spotlight), and opens like a Mac app. Installed something before this feature existed? Run
`./bin/omnilaunch apps scan` once.

If anything goes wrong, OmniLaunch shows an alert. Its log is at
`~/Library/Application Support/OmniLaunch/omnilaunch.log`, which is useful to send me.

## 5. Optional: an Android app

```bash
./bin/omnilaunch setup android     # about 2 GB, takes a while
```

Then download an `.apk` (for example F-Droid from https://f-droid.org/F-Droid.apk) and
double-click it. Expect: an Android phone window starts (about a minute the first time), then
the app installs and opens in it.

## 6. Optional extras

- **Linux apps:** `./bin/omnilaunch setup container` (needs Homebrew, or install OrbStack),
  then `./bin/omnilaunch run <linux program>`.
- **Linux apps with windows:** `./bin/omnilaunch setup linux-gui` (installs XQuartz; log out
  and back in once afterwards).
- **Windows VM fallback** (only if you have Parallels Desktop):
  `./bin/omnilaunch vm set windows --tool parallels --name "Windows 11"`, then
  `./bin/omnilaunch run --runner vm ~/Downloads/putty.exe`.

## Undo

```bash
./bin/omnilaunch integrate --remove
```

## What to send back

- The output of `./bin/omnilaunch doctor`
- Which steps matched "Expect" and which didn't, plus the log file if something failed
