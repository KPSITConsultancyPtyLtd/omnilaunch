# Testing OmniLaunch on a Mac

Steps 1–5 take about 20 minutes, most of it waiting for Wine to download the first time.
When something doesn't match "Expect", copy what you see, plus the log (step 7).

## 1. Update OmniLaunch

Already have it from earlier testing:

```bash
cd ~/omnilaunch
git pull          # one last time by hand; after this, ./bin/omnilaunch update does it
./bin/omnilaunch update
```

Expect: `OmniLaunch is up to date (…)`.

First time? `xcode-select --install`, then
`git clone https://github.com/KPSITConsultancyPtyLtd/omnilaunch ~/omnilaunch`.

## 2. Turn on double-click and check health

```bash
./bin/omnilaunch integrate
./bin/omnilaunch doctor
```

Expect from `integrate`: `.exe: opens with OmniLaunch` (and `.msi`, `.apk`, `.AppImage`).
If macOS asks whether to use OmniLaunch for a file type, choose **Use "OmniLaunch"**.

Expect from `doctor`: `[OK ] Double-click .exe/.msi/.apk/.AppImage`, and a note about macOS 28
and Box64 (on Apple Silicon).

## 3. Double-click a Windows program

1. Download **PuTTY** (`putty.exe`, 64-bit x86) from
   https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html.
2. Double-click it in Finder.
3. If Wine isn't installed yet: a dialog offers to install it. Click **Install**, and enter your
   Mac password if asked (that's Rosetta 2). Wait for the "is ready" notification.
4. Expect a notification: *Setting up a Windows environment for putty.exe…*, then PuTTY's
   window within a minute or two. Later double-clicks open it straight away.

## 4. Install a Windows program like a Mac app

1. Download the **7-Zip** installer (64-bit x64 `.exe`) from https://www.7-zip.org and
   double-click it. Finish the installer.
2. Expect: **7-Zip File Manager** appears on your **Desktop** and in
   **Applications › OmniLaunch Apps** (also in Launchpad and Spotlight), **with 7-Zip's own
   icon**, not a script icon.
3. Double-click it: 7-Zip opens.
4. Right-click any `.zip` › **Open With** › **7-Zip File Manager**: 7-Zip opens that zip.

Installed 7-Zip during earlier testing? Run `./bin/omnilaunch apps scan` once: it recreates
the app with its icon and Desktop shortcut.

## 5. Remove a program cleanly

```bash
./bin/omnilaunch apps list
./bin/omnilaunch apps remove "7-Zip File Manager"
```

Expect: the app disappears from the Desktop and from OmniLaunch Apps, and you're told its
Windows environment was kept. `apps remove "…" --with-data` also deletes that environment.
(Reinstall with step 4 if you want to keep using 7-Zip.)

Also from earlier testing: if `./bin/omnilaunch envs list` still shows `wine/7zFM-…`, remove
that leftover duplicate with `./bin/omnilaunch envs remove wine/7zFM-…` (use the full name shown).

## 6. Optional: Windows apps without Rosetta (what macOS 28 will use)

Needs Homebrew (for Podman) or OrbStack, and about 30 minutes the first time.

```bash
./bin/omnilaunch setup box64
OMNILAUNCH_WINDOWS_RUNNER=box64 ./bin/omnilaunch run ~/Downloads/putty.exe
```

Expect: `setup box64` installs what's missing and builds an image (10–15 minutes). Then
PuTTY's window opens through XQuartz. OmniLaunch now switches on XQuartz's "allow network
clients" setting itself and restarts XQuartz if needed; if no window appears the very first
time, log out and back in once and try again.

## 7. If something goes wrong

The log of double-clicked apps:

```bash
tail -60 ~/Library/Application\ Support/OmniLaunch/omnilaunch.log | grep -v "VK_\|mvk-info\|^\s"
```

## What to send back

- The output of `./bin/omnilaunch doctor`
- Which steps matched "Expect" and which didn't, plus the log lines for anything that failed
