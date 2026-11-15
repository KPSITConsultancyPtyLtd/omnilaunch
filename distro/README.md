# OmniLaunch OS

A ready-made Linux, based on Fedora Kinoite (KDE Plasma), with OmniLaunch and all of its helpers built in.
Nothing to set up: double-click a Windows `.exe`/`.msi`, an Android `.apk` or an `.AppImage` and it runs.

| Opens | How | PC (x86_64) | ARM (aarch64) |
|---|---|---|---|
| Linux apps | natively; other distros' apps in Podman | ✓ | ✓ |
| Windows apps | Wine | ✓ | ✓ Intel Wine translated by FEX (slower) |
| Windows games | Proton via umu-launcher, like Steam | ✓ | – (use Wine) |
| Android apps | Waydroid (Android in a container, full speed) | ✓ | ✓ |
| macOS apps | – no working translation layer exists for Apple's desktop frameworks | ✗ | ✗ |

Games with kernel-level anti-cheat (Valorant, many competitive shooters) refuse to run under Wine or Proton anywhere.

## Get it

GitHub builds it: **Actions → distro → the latest run → Artifacts**. There's one `.iso` per CPU type, and
each is kept for a day. To build a fresh one, use **Run workflow**.

- **PC (Intel/AMD):** `OmniLaunch-OS-x86_64.iso`. Write it to a USB stick (8 GB or larger) with
  [Fedora Media Writer](https://fedoraproject.org/workstation/download) or balenaEtcher, then boot from it.
  It starts a live session you can try. The **Install to Hard Drive** icon installs it.
- **Try it on a Mac first:** `OmniLaunch-OS-aarch64.iso` in [UTM](https://mac.getutm.app). Create a new
  **Virtualize → Linux** VM, select the ISO, give it 4 GB+ RAM and 30 GB disk, and boot it.
- **Apple Silicon Mac, for real (experimental):** Macs can't boot the USB. Install
  [Fedora Asahi Remix](https://asahilinux.org) first (KDE edition), then in it:

  ```sh
  git clone https://github.com/KPSITConsultancyPtyLtd/omnilaunch && cd omnilaunch
  ./distro/install-on-fedora.sh
  ```

  The same script works on any regular Fedora 43 or newer.

## How it's built

- `Containerfile`: Fedora Kinoite + `build/install-layer.sh` → the OS, as a bootable container (bootc).
- `build/install-layer.sh`: the one recipe for what OmniLaunch OS adds (also used on Fedora Asahi):
  - Wine, umu-launcher (Proton), Waydroid and Podman
  - on ARM: FEX plus an Intel Wine build
  - OmniLaunch in `/usr/lib/omnilaunch`, set as the system default for app files
- `Containerfile.live` + `live/`: the live USB system and installer. The installer installs the OS
  image copied onto the stick, so it needs no network. [Titanoboa](https://github.com/ublue-os/titanoboa)
  makes the `.iso`.
- `build/check.sh`: what CI verifies in every image.
