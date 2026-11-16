# OmniLaunch OS

A ready-made Linux, based on Fedora Kinoite (KDE Plasma), with OmniLaunch and all of its helpers built in.
Nothing to set up: double-click a Windows `.exe`/`.msi`, an Android `.apk` or an `.AppImage` and it runs.

| Opens | How | PC (x86_64) | ARM (aarch64) |
|---|---|---|---|
| Linux apps | natively; Flathub in Discover; other distros' apps via Distrobox | ✓ | ✓ |
| Windows apps | Wine, with NTSYNC for speed | ✓ | ✓ Intel Wine translated by FEX (slower) |
| Windows games | Steam and Proton (umu), Gamescope, MangoHud | ✓ | – (use Wine) |
| Android apps | Waydroid (Android in a container, full speed) | ✓ | ✓ |
| macOS apps | – no working translation layer exists for Apple's desktop frameworks | ✗ | ✗ |

Games with kernel-level anti-cheat (Valorant, many competitive shooters) refuse to run under Wine or Proton anywhere.

## Hardware support

- **Kernel and firmware:** the newest Fedora kernel and every firmware package (GPUs, Wi-Fi, audio, CPU microcode).
  It's rebuilt every week, and installed systems pick it up automatically.
- **NVIDIA:** NVIDIA's own driver with the open kernel module, for RTX 20-series / GTX 16-series and newer,
  RTX 50 included. Older NVIDIA cards (GTX 10-series and earlier) aren't supported by this driver.
- **AMD and Intel graphics:** the latest Mesa, plus hardware video decoding for H.264, HEVC and AV1.

**Secure Boot and NVIDIA:** the NVIDIA driver loads only if it's signed with a key your PC trusts. Until you
set that up, turn Secure Boot off in the PC's firmware settings (everything else works either way). To set it up:

1. Run `distro/make-secure-boot-key.sh` once.
2. Add the two values it prints as repository secrets `MOK_KEY` and `MOK_CERT`.
3. After the next build, run `omnilaunch-os enroll-key` on the PC and restart.

## Get it

GitHub builds it: **Actions → distro → the latest run → Artifacts**. There's one `.iso` (about 5–6 GB) per
CPU type, kept for a day. **Run workflow** builds a fresh one.

Installing downloads OmniLaunch OS from `ghcr.io/kpsitconsultancyptyltd/omnilaunch-os`, so it needs
internet. The package must be **public**: GitHub → your profile → Packages → omnilaunch-os → Package settings
→ Change visibility. Making it public also makes the OmniLaunch code inside the image visible to anyone.

- **PC (Intel/AMD):** write `OmniLaunch-OS-x86_64.iso` to a USB stick (8 GB or larger) with
  [Fedora Media Writer](https://fedoraproject.org/workstation/download) or balenaEtcher, then boot from it.
  It starts a live session you can try, and **Install to Hard Drive** installs it.
- **Try it on a Mac first:** run `OmniLaunch-OS-aarch64.iso` in [UTM](https://mac.getutm.app). Choose
  **Virtualize → Linux**, select the ISO, and give it 4 GB+ RAM and a 30 GB disk.
- **Apple Silicon Mac, for real (experimental):** Macs can't boot the USB. Install
  [Fedora Asahi Remix](https://asahilinux.org) first (KDE edition), then in it:

  ```sh
  git clone https://github.com/KPSITConsultancyPtyLtd/omnilaunch && cd omnilaunch
  ./distro/install-on-fedora.sh
  ```

## Living with it

Updates download in the background every day and take effect when you restart. Your files and apps stay.
If an update misbehaves, pick the previous version in the boot menu, or run `omnilaunch-os rollback`.

```sh
omnilaunch-os status      # version, kernel, and whether an update is waiting
omnilaunch-os update      # get the newest version now
omnilaunch-os rollback    # back to the previous version
```

## How it's built

`Containerfile` builds the OS as a bootable container (bootc), in three stages:

1. `build/kernel.sh` installs the newest kernel and firmware.
2. `build/nvidia-kmod.sh` builds the NVIDIA driver for exactly that kernel.
3. `build/drivers.sh` (NVIDIA, codecs) and `build/install-layer.sh` (Wine, Proton, Waydroid, Steam,
   OmniLaunch, updates) finish the image. `install-layer.sh` is the same recipe `install-on-fedora.sh` uses.

The rest:

- `Containerfile.live` plus `live/` make the live USB system and its installer.
  [Titanoboa](https://github.com/ublue-os/titanoboa) turns them into the `.iso`.
- `build/check.sh` is what CI verifies in every image before publishing it.
