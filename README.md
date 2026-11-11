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

## Usage

Requires Python 3.9+. No dependencies.

```bash
python3 -m omnilaunch doctor                 # which compatibility layers are installed
python3 -m omnilaunch info  ~/Downloads/setup.exe
python3 -m omnilaunch run   ~/Apps/notepad++.exe
python3 -m omnilaunch run   --dry-run ./SomeTool.app
```

Optional: `OMNILAUNCH_PROTON=/path/to/proton` to use Proton for Windows games, and
`OMNILAUNCH_HOME` to change where per-app environments are stored
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
2. Desktop integration: `.desktop` file + MIME handlers so double-clicking any app uses OmniLaunch
3. Graphical app library (icons, per-app settings, Wine/Proton version per app)
4. Automatic installation of missing layers on first use
5. Package as a custom Linux distro (e.g. Fedora/Ubuntu base, OmniLaunch preinstalled)
