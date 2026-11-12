# OmniLaunch

One launcher for Linux, Windows and macOS apps. Point it at any app and it works out
what platform the app was built for, picks the right compatibility layer, gives the app
its own isolated environment, and runs it.

It's the first building block of a Linux-based OS where apps "just run" no matter
which platform they were built for.

## How it works

```
             omnilaunch run <app>
                     |
           detect (reads file bytes)
       ┌─────────────┼──────────────┐
     ELF            PE/MZ         Mach-O / .app
   (Linux)        (Windows)        (macOS)
       |              |               |
    native       Wine / Proton      Darling
 (box64/FEX if   per-app prefix   (CLI tools only,
  CPU differs)                     GUI unlikely)
```

Detection uses the file's magic bytes, not its extension, so a renamed `.exe` still
runs through Wine and a Java `.class` file isn't mistaken for a Mac binary.

## Install

Requires Python 3.9+ on Linux. No dependencies.

```bash
pip install --user .        # gives you the `omnilaunch` command
omnilaunch integrate        # optional: double-click .exe/.msi/Mac binaries to open them
```

(Or run without installing: `python3 -m omnilaunch ...` from this folder.)

## Usage

```bash
omnilaunch doctor                          # which compatibility layers are installed
omnilaunch info ~/Downloads/setup.exe      # what platform/CPU an app was built for
omnilaunch run  ~/Apps/notepad++.exe       # run it
omnilaunch run  ./tool --dry-run           # show what would run, without running it
omnilaunch run  --runner wine ./mystery    # force a specific layer
omnilaunch run  ./app.exe -v               # show Wine/Proton debug output
omnilaunch run  ./app.exe -- --dry-run     # everything after -- goes to the app
omnilaunch envs list                       # per-app environments, size, last used
omnilaunch envs remove wine/notepad-1a2b3c4d
omnilaunch integrate --remove              # undo double-click integration
```

What it handles:

| Input | What happens |
|---|---|
| Linux binary / AppImage | Runs natively. 32-bit apps run on 64-bit hosts; foreign CPUs go through box64/FEX/qemu |
| Linux script | Runs directly, or via its `#!` interpreter if it isn't marked executable |
| Windows `.exe` | Wine (or Proton if configured), in its own isolated environment |
| Windows `.msi` / `.bat` / `.cmd` / `.lnk` | `msiexec` / `cmd /c` / `start` under Wine |
| Windows `.dll` | Explains it's a library, not an app |
| Mac binary / `.app` bundle | Darling (Intel builds only), with a clear message for Apple Silicon-only apps |
| `.dmg` / `.pkg` / `.zip` | Explains how to extract it first |

Apps run with their own folder as the working directory (many Windows apps need this).
When launched by double-click, errors appear as desktop notifications.

Settings (environment variables): `OMNILAUNCH_PROTON=/path/to/proton` uses Proton for
Windows apps; `OMNILAUNCH_HOME` changes where per-app environments live
(default `~/.local/share/omnilaunch`).

Install the layers you want:

| Apps    | Layer   | Install                                   |
|---------|---------|-------------------------------------------|
| Windows | Wine    | `sudo apt install wine` (or dnf/pacman)   |
| Windows games | Proton | via Steam, or GE-Proton                |
| macOS   | Darling | https://www.darlinghq.org (experimental)  |
| x86 on ARM | box64 / FEX-Emu | distro packages               |

## Honest limits

- **macOS apps** are the weak spot. Darling handles many command-line tools, but most
  graphical Mac apps (Cocoa, Metal) won't run. Apple's licence also restricts running
  macOS components on non-Apple hardware.
- **Windows apps** depend on Wine compatibility: many work well, while anti-cheat games
  and some apps with kernel drivers don't.

## Run the tests

```bash
python3 -m unittest discover -s tests -v
```

## Roadmap

1. ~~CLI launcher with detection and per-app isolation~~ (v0.1)
2. ~~Desktop integration, env management, runner override, installable package~~ (v0.2)
3. Graphical app library (icons, per-app settings, Wine/Proton version per app)
4. Automatic installation of missing layers on first use
5. Package as a custom Linux distro (e.g. Fedora/Ubuntu base, OmniLaunch preinstalled)

## Licence

Apache 2.0. See [LICENSE](LICENSE).
