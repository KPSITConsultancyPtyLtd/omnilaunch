# OmniLaunch

One app that runs on **Windows**, **macOS** and **Linux**, and opens apps made for any of
them. Point it at an app and it works out what platform the app was built for, installs the
helper it needs if it's missing (Wine, WSL, a container engine, ...), and runs the app,
each in its own isolated environment.

It's the first building block of an OS where apps "just run" no matter which platform
they were built for.

## How it works

```
                         omnilaunch run <app>
                                 |
                     detect (reads the file's bytes)
           ┌─────────────────────┼──────────────────────┐
      Linux binary          Windows .exe           Mac binary / .app
           |                     |                      |
 Linux:   native           Windows: native         Mac:     native (Rosetta 2
 Windows: WSL              Linux:   Wine/Proton             for Intel apps)
 Mac:     container        Mac:     Wine           Linux:   Darling (CLI only)
          (Docker/Podman)  (own isolated           Windows: not possible
                            environment per app)
```

Detection uses the file's contents, not its name, so a renamed `.exe` still runs through
Wine and a Java `.class` file isn't mistaken for a Mac binary.

## What runs where

| App type | On a Windows PC | On a Mac | On a Linux computer |
|---|---|---|---|
| Windows app | Native | Wine, or a Windows VM as fallback | Wine or Proton, or a Windows VM as fallback |
| Linux command-line app | WSL (or a Docker container) | Container (hidden Linux VM) | Native |
| Linux graphical app | WSL on Windows 11 (WSLg) | Container + XQuartz: windows appear on the Mac | Native |
| Mac command-line tool | Not possible | Native | Darling (Intel builds only) |
| Mac graphical app | Not possible | Native | Unlikely to work (Darling) |

Mac apps can't run on Windows: nothing exists that does it, and Apple's licence only allows
macOS on Apple hardware. That's the one combination OmniLaunch can't cover.

ChromeOS works through its built-in Linux environment (treat it as Linux). Phones and
tablets (iOS, Android) can't run other platforms' desktop apps, so they're out of scope.

Also handled: AppImages, scripts (run via their `#!` interpreter if not executable),
Windows `.msi` / `.bat` / `.cmd` / `.lnk`, and clear explanations for `.dll`, `.dmg`,
`.pkg` and `.zip` files.

## Quick start (no install)

Requires Python 3.9+ and git. No other dependencies. (Mac: `xcode-select --install`;
Windows: install Python from python.org or the Microsoft Store, and Git for Windows.)

```bash
git clone https://github.com/KPSITConsultancyPtyLtd/omnilaunch
cd omnilaunch
./bin/omnilaunch doctor          # Mac/Linux: what's installed (Windows: bin\omnilaunch doctor)
./bin/omnilaunch setup all       # install every helper this computer is missing
./bin/omnilaunch run <any app>   # or just run an app: it offers to install what it needs
```

Step-by-step test guides: **[TESTING-MAC.md](TESTING-MAC.md)** and **[TESTING-WINDOWS.md](TESTING-WINDOWS.md)**.

To install the `omnilaunch` command instead: `pip3 install --user .`

## Usage

```bash
omnilaunch doctor                          # what's installed, where environments live
omnilaunch info ~/Downloads/setup.exe      # what platform/CPU an app was built for
omnilaunch run  ~/Apps/notepad++.exe       # run it
omnilaunch run  ./tool --dry-run           # show what would run, without running it
omnilaunch run  --runner container ./tool  # force a layer: native, wine, proton, darling, container, wsl, vm
omnilaunch run  ./app.exe -v               # show Wine/Proton debug output
omnilaunch run  ./app.exe -- --dry-run     # everything after -- goes to the app
omnilaunch run  -y ./app.exe               # install any missing helper without asking
omnilaunch setup                           # list helpers and whether they're installed
omnilaunch setup wine                      # install one (or `all`); add --dry-run to preview
omnilaunch vm set windows --tool parallels --name "Windows 11"   # VM fallback for Windows apps
omnilaunch run  --runner vm ./app.exe      # force an app into the VM
omnilaunch envs list                       # per-app environments, size, last used
omnilaunch envs remove wine/notepad-1a2b3c4d
omnilaunch integrate                       # double-click to open: .exe/.msi on Linux, .AppImage on Windows
omnilaunch integrate --remove
```

Apps start in their own folder (many Windows apps need this). When launched by
double-click, errors appear as desktop notifications.

### Settings (environment variables)

| Variable | Effect |
|---|---|
| `OMNILAUNCH_HOME` | Where per-app environments live (default `~/.local/share/omnilaunch` on Linux, `~/Library/Application Support/OmniLaunch` on Mac, `%LOCALAPPDATA%\OmniLaunch` on Windows) |
| `OMNILAUNCH_PROTON` | Path to a Proton `proton` script; Windows apps then use Proton (Linux only) |
| `OMNILAUNCH_LINUX_IMAGE` | Container image for Linux apps run in a container (default `debian:stable-slim`) |

### Helpers, and how they get installed

`omnilaunch setup <name>` (or the prompt during `run`) installs these for you; the table
shows what it does, in case you'd rather do it by hand.

| Helper | Windows | Mac | Linux |
|---|---|---|---|
| WSL | `wsl --install` in an admin terminal, then restart | n/a | n/a |
| Wine | n/a (Windows apps are native) | Download *Wine Stable* from [Gcenx/macOS_Wine_builds](https://github.com/Gcenx/macOS_Wine_builds/releases) into Applications, or use MacPorts. Homebrew's Wine casks were disabled in September 2026. Needs Rosetta 2 on Apple Silicon | `sudo apt install wine` (or dnf/pacman) |
| Proton | n/a | n/a | via Steam, or GE-Proton |
| Containers | Docker Desktop or Podman Desktop (optional; WSL is preferred) | [OrbStack](https://orbstack.dev), Docker Desktop or Podman | `docker` or `podman` |
| Darling | n/a | n/a (Mac apps run natively) | [darlinghq.org](https://www.darlinghq.org) (experimental) |
| Rosetta 2 | n/a | `softwareupdate --install-rosetta --agree-to-license` | n/a |
| XQuartz + GUI image (`linux-gui`) | n/a | Downloads and opens the XQuartz installer, then builds an `omnilaunch/linux-gui` container image with common GUI libraries | n/a (Linux GUI apps are native) |
| Windows VM | n/a | Bring your own VM in Parallels Desktop or VirtualBox, then `omnilaunch vm set ...` | VirtualBox VM, then `omnilaunch vm set ...` |

**About the VM fallback:** OmniLaunch drives a Windows VM you already have rather than
building one (Windows needs your own licence). With Parallels, apps under your home folder
run straight from the shared `\\Mac\Home`. With VirtualBox, give a Windows account and a
password file (`--user me --password-file ~/.winpass`); the app is copied into the VM at
`C:\OmniLaunch`.

## Honest limits

- **Mac apps on Windows** can't run at all: nothing exists that does it, and Apple's
  licence only allows macOS on Apple hardware.
- **Mac apps on Linux** are the weak spot: Darling handles many command-line tools, but
  most graphical Mac apps (Cocoa, Metal) won't run. Apple's licence also restricts running
  macOS components on non-Apple hardware.
- **Windows apps** depend on Wine compatibility: many work well, while anti-cheat games
  and apps with kernel drivers don't.
- **Linux apps on a Mac** run in a container that can only see the app's own folder.
  Graphical ones show their windows through XQuartz: fine for most tools, but not for
  games or apps that need fast 3D graphics.

## Run the tests

```bash
python3 -m unittest discover -s tests -v
```

The tests simulate Windows, Mac and Linux hosts, so they pass on any of them. GitHub Actions
also runs them on real Windows, macOS and Linux machines on every push (see the Actions tab).

## Roadmap

1. ~~CLI launcher with detection and per-app isolation~~ (v0.1)
2. ~~Desktop integration, env management, runner override, installable package~~ (v0.2)
3. ~~macOS host support: native Mac apps, Rosetta, Wine on Mac, Linux apps in containers~~ (v0.3)
4. ~~Windows host support: native Windows apps, Linux apps via WSL, .AppImage double-click~~ (v0.4)
5. ~~Automatic setup of helpers; Linux GUI apps on Mac; Windows VM fallback~~ (v0.5)
6. Graphical app library (icons, per-app settings, Wine/Proton version per app)
7. Double-click integration on Mac
8. Package as a custom Linux distro (e.g. Fedora/Ubuntu base, OmniLaunch preinstalled)

## Licence

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
Copyright 2026 KPS IT Consultancy Pty Ltd.
