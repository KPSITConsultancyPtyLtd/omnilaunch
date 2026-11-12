# OmniLaunch

One launcher for Linux, Windows and macOS apps. Point it at any app and it works out
what platform the app was built for, picks the right compatibility layer, gives the app
its own isolated environment, and runs it. Works on **Linux** and **macOS** computers.

It's the first building block of an OS where apps "just run" no matter which platform
they were built for.

## How it works

```
                      omnilaunch run <app>
                              |
                  detect (reads the file's bytes)
          ┌───────────────────┼────────────────────┐
     Linux binary        Windows .exe          Mac binary / .app
          |                   |                    |
 on Linux:  native       Wine (or Proton       on Mac:   native (Rosetta 2
            (box64/FEX    on Linux), one                 for Intel apps)
            if CPU        isolated Windows     on Linux: Darling
            differs)      environment per app            (CLI tools only)
 on Mac:    container
            (Docker/Podman)
```

Detection uses the file's contents, not its name, so a renamed `.exe` still runs through
Wine and a Java `.class` file isn't mistaken for a Mac binary.

## What runs where

| App type | On a Linux computer | On a Mac |
|---|---|---|
| Linux command-line app | Native | Docker/Podman/OrbStack container |
| Linux graphical app | Native | Not yet (needs an X11 setup) |
| Windows app | Wine or Proton | Wine (standalone "Wine Stable" app or MacPorts) |
| Mac command-line tool | Darling (Intel builds only) | Native |
| Mac graphical app | Unlikely to work (Darling) | Native |

Also handled: AppImages, scripts (run via their `#!` interpreter if not executable),
Windows `.msi` / `.bat` / `.cmd` / `.lnk`, and clear explanations for `.dll`, `.dmg`,
`.pkg` and `.zip` files.

## Quick start (no install)

Requires Python 3.9+ (on a Mac, `xcode-select --install` provides it). No dependencies.

```bash
git clone https://github.com/KPSITConsultancyPtyLtd/omnilaunch
cd omnilaunch
./bin/omnilaunch doctor          # which compatibility layers are installed
```

Testing on a Mac? Follow **[TESTING-MAC.md](TESTING-MAC.md)**.

To install the `omnilaunch` command instead: `pip3 install --user .`

## Usage

```bash
omnilaunch doctor                          # what's installed, where environments live
omnilaunch info ~/Downloads/setup.exe      # what platform/CPU an app was built for
omnilaunch run  ~/Apps/notepad++.exe       # run it
omnilaunch run  ./tool --dry-run           # show what would run, without running it
omnilaunch run  --runner container ./tool  # force a layer: native, wine, proton, darling, container
omnilaunch run  ./app.exe -v               # show Wine/Proton debug output
omnilaunch run  ./app.exe -- --dry-run     # everything after -- goes to the app
omnilaunch envs list                       # per-app environments, size, last used
omnilaunch envs remove wine/notepad-1a2b3c4d
omnilaunch integrate                       # Linux: double-click .exe/.msi to open them
omnilaunch integrate --remove
```

Apps start in their own folder (many Windows apps need this). When launched by
double-click, errors appear as desktop notifications.

### Settings (environment variables)

| Variable | Effect |
|---|---|
| `OMNILAUNCH_HOME` | Where per-app environments live (default `~/.local/share/omnilaunch` on Linux, `~/Library/Application Support/OmniLaunch` on Mac) |
| `OMNILAUNCH_PROTON` | Path to a Proton `proton` script; Windows apps then use Proton (Linux only) |
| `OMNILAUNCH_LINUX_IMAGE` | Container image for Linux apps on a Mac (default `debian:stable-slim`) |

### Installing the compatibility layers

| Layer | Linux | Mac |
|---|---|---|
| Wine | `sudo apt install wine` (or dnf/pacman) | Download *Wine Stable* from [Gcenx/macOS_Wine_builds](https://github.com/Gcenx/macOS_Wine_builds/releases) into Applications, or use MacPorts. Homebrew's Wine casks were disabled in September 2026. Needs Rosetta 2 on Apple Silicon |
| Proton | via Steam, or GE-Proton | n/a |
| Containers | `docker` or `podman` | [OrbStack](https://orbstack.dev), Docker Desktop or Podman |
| Darling | [darlinghq.org](https://www.darlinghq.org) (experimental) | n/a (Mac apps run natively) |
| Rosetta 2 | n/a | `softwareupdate --install-rosetta --agree-to-license` |

## Honest limits

- **Mac apps on Linux** are the weak spot: Darling handles many command-line tools, but
  most graphical Mac apps (Cocoa, Metal) won't run. Apple's licence also restricts running
  macOS components on non-Apple hardware.
- **Windows apps** depend on Wine compatibility: many work well, while anti-cheat games
  and apps with kernel drivers don't.
- **Linux apps on a Mac** run in a container that can only see the app's own folder, and
  only command-line apps work for now.

## Run the tests

```bash
python3 -m unittest discover -s tests -v
```

The tests simulate both Linux and Mac hosts, so they pass on either.

## Roadmap

1. ~~CLI launcher with detection and per-app isolation~~ (v0.1)
2. ~~Desktop integration, env management, runner override, installable package~~ (v0.2)
3. ~~macOS host support: native Mac apps, Rosetta, Wine on Mac, Linux apps in containers~~ (v0.3)
4. Graphical app library (icons, per-app settings, Wine/Proton version per app)
5. Linux GUI apps on Mac (XQuartz) and double-click integration on Mac
6. Automatic installation of missing layers on first use
7. Package as a custom Linux distro (e.g. Fedora/Ubuntu base, OmniLaunch preinstalled)

## Licence

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
Copyright 2026 KPS IT Consultancy Pty Ltd.
