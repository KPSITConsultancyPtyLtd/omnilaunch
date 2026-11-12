# OmniLaunch

**Double-click any app, on any computer, and it runs.**

OmniLaunch is one app for **Windows**, **macOS** and **Linux** that opens apps made for the
other systems as if they were native:

- On a **Mac**, double-click a Windows `.exe`: it runs. Programs that installers add appear
  in *Applications › OmniLaunch Apps* and on your Desktop, and open like any Mac app.
- On a **Linux PC**, run a game's Windows `.exe` launcher: it runs through Proton, the same
  technology Steam uses, without needing Steam.
- On **Windows** (or a Mac, or Linux), double-click an Android `.apk`: it's installed and opened.

The first time an app needs a helper (Wine, Proton, Android, WSL, ...), OmniLaunch asks once,
installs it, and opens the app. Each app gets its own isolated environment.

## What runs where

| App | On a Windows PC | On a Mac | On a Linux computer |
|---|---|---|---|
| Windows `.exe` / `.msi` | Native | Wine; on macOS 28+ Box64 + Wine (no Rosetta); or a Windows VM | Proton (games) or Wine, or a Windows VM |
| Android `.apk` | Android Emulator | Android Emulator | Waydroid or Android Emulator |
| Linux apps | WSL (graphical apps too, on Windows 11) | Container; windows show through XQuartz | Native |
| Mac `.app` / `.dmg` / `.pkg` | Not possible | Native (`.dmg` installs into Applications) | Command-line tools only (Darling) |

**Not possible:** Mac apps on Windows. Nothing exists that runs them there, and Apple's licence
only allows macOS on Apple hardware. OmniLaunch says so clearly instead of failing.

## Get started

Needs Python 3.9+ and git (Mac: `xcode-select --install`; Windows: Python from python.org and
Git for Windows).

```bash
git clone https://github.com/KPSITConsultancyPtyLtd/omnilaunch
cd omnilaunch
./bin/omnilaunch integrate    # Windows: bin\omnilaunch integrate
```

`integrate` makes double-clicking work. On a Mac it creates *OmniLaunch.app* in
`~/Applications` and makes it the default app for `.exe`, `.msi`, `.apk` and `.AppImage`
files (recent macOS versions ask you to confirm this once per file type). On Windows it
registers `.apk` and `.AppImage`; on Linux, `.exe`, `.msi`, `.apk` and Mac files. Then just
double-click an app.

Step-by-step test guides: **[TESTING-MAC.md](TESTING-MAC.md)** and
**[TESTING-WINDOWS.md](TESTING-WINDOWS.md)**.

## Command line

```bash
omnilaunch run  <app>                      # run any app (asks to install what it needs)
omnilaunch run  -y <app>                   # ...installing missing helpers without asking
omnilaunch info <app>                      # what platform/CPU it was built for, GUI or not
omnilaunch doctor                          # what's installed, what's missing
omnilaunch setup                           # list helpers; `setup <name>` or `setup all` installs
omnilaunch envs list                       # per-app environments (size, last used)
omnilaunch apps scan                       # make apps (+ Desktop shortcuts) for installed programs
omnilaunch apps list                       # the apps OmniLaunch has made
omnilaunch run  --env wine/setup-1a2b ./x.exe   # run inside an existing environment
omnilaunch run  --runner vm ./app.exe      # force a layer: native, wine, proton, darling,
                                           #   container, wsl, vm, android
omnilaunch vm set windows --tool parallels --name "Windows 11"   # VM fallback for Windows apps
omnilaunch run  ./tool --dry-run           # show what would happen, change nothing
omnilaunch integrate --remove              # undo double-click handling
```

### Helpers OmniLaunch installs for you

| Helper (`setup <name>`) | Where | What it does |
|---|---|---|
| `wine` | Mac, Linux | Mac: downloads the newest *Wine Stable* into `~/Applications` (Homebrew's Wine was disabled in 2026). Linux: your package manager |
| `rosetta` | Apple Silicon Macs | Apple's Intel translator (Wine needs it, up to macOS 27) |
| `box64` | Apple Silicon Macs, ARM Linux | An ARM Linux image with Box64 (translates Intel code) and Wine, so Windows apps run without Rosetta. Used automatically from macOS 28 |
| `proton` | x86 Linux | umu-launcher, which runs Proton in Valve's Steam runtime and downloads Proton itself |
| `android` | all | Google's Android Emulator, platform tools and an Android 14 image (~2 GB), plus a virtual phone. No Android Studio or Java needed |
| `wsl` | Windows | Windows Subsystem for Linux (`wsl --install`) |
| `container` | Mac, Linux | Podman (Mac: via Homebrew; or install OrbStack / Docker Desktop yourself) |
| `xquartz`, `linux-gui` | Mac | Shows Linux app windows on the Mac |
| `darling` | Linux | Manual: OmniLaunch links the build instructions |

When OmniLaunch was opened by double-click, it asks in a dialog, uses the system's own
password prompt when admin rights are needed, and shows errors as alerts.

### How installers become native apps

When a Windows installer finishes, OmniLaunch reads the Start Menu shortcuts it created and
adds each program to *Applications › OmniLaunch Apps* (Mac; also in Launchpad and Spotlight)
or your app menu (Linux), plus a shortcut on your Desktop. Opening one runs it in the
environment it was installed into. For programs installed before this existed, run
`omnilaunch apps scan`. On a Mac, opening a `.dmg`
copies its app into Applications and opens it, and a `.pkg` opens Apple's Installer.

### Settings (environment variables)

| Variable | Effect |
|---|---|
| `OMNILAUNCH_HOME` | Where environments and helpers live (default `~/.local/share/omnilaunch`, `~/Library/Application Support/OmniLaunch`, `%LOCALAPPDATA%\OmniLaunch`) |
| `OMNILAUNCH_PROTON` | A specific Proton build to use (path to its `proton` script) |
| `OMNILAUNCH_GAMEID` | umu game ID, so Proton applies that game's fixes (default `0`) |
| `OMNILAUNCH_LINUX_IMAGE` | Container image for Linux apps (default `debian:stable-slim`) |
| `OMNILAUNCH_WINDOWS_RUNNER` | Always use this layer for Windows apps, e.g. `box64` |
| `OMNILAUNCH_DESKTOP_SHORTCUTS` | Set to `0` to stop adding Desktop shortcuts for installed programs |

## How close to "native" is it?

- **Rosetta's end:** Apple limits Rosetta from macOS 28 to some older games, so Wine can't
  use it there. OmniLaunch then runs Windows apps through Box64 + Wine in the hidden Linux
  VM instead, with windows shown through XQuartz. Ordinary apps work well; demanding 3D
  games are slower than with Rosetta. Try it early with `OMNILAUNCH_WINDOWS_RUNNER=box64`.
- **Windows apps on a Mac/Linux** depend on Wine/Proton. Most apps and most games work,
  but games with kernel anti-cheat (e.g. some online shooters) refuse to run, and Windows
  apps keep their Windows look. The first launch of each app takes a minute while its
  environment is set up.
- **Android apps** open in the emulator's phone window rather than their own desktop
  window (Waydroid on Linux gives them their own windows). The first start takes about a
  minute while Android boots. On Intel/AMD PCs, ARM-only apps go through Android's
  translator, which most apps handle but some games don't.
- **Linux apps on a Mac** run in a container. Graphical ones work through XQuartz, but
  not apps that need fast 3D.
- **Mac apps on Linux** are limited to command-line tools (Darling).

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Unit tests simulate Windows, Mac and Linux, so they pass anywhere. GitHub Actions also runs
them on real Windows, macOS and Linux machines on every push, plus real-world checks:
reading a real APK, the live Android/Wine/XQuartz/umu download indexes, registering
double-click handling for real, installing an app from a real `.dmg`, and running real 64-bit
and 32-bit Windows programs through Box64 + Wine on ARM64.

## Roadmap

1. ~~Launcher with detection and per-app isolation~~ (v0.1–0.2)
2. ~~Runs on macOS and Windows as well as Linux~~ (v0.3–0.4)
3. ~~Automatic setup of helpers; Linux GUI apps on Mac; Windows VM fallback~~ (v0.5)
4. ~~Android apps, Proton for games, double-click on Mac, installers become native apps~~ (v0.6)
5. App icons for installed programs; a small graphical library of everything installed
6. Package as a signed Mac app, a Windows installer and a Linux package (no Python needed)

## Licence

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Copyright 2026 KPS IT Consultancy Pty Ltd.
