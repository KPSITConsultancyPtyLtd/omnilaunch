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

- **Also included:** printer drivers (HP, Epson, Brother, Samsung and more) and network scanners, GPU
  compute (OpenCL), game-controller support, fingerprint readers, and fonts that match Microsoft Office's,
  so documents keep their layout.

## Apps, ready to use

After the first start, OmniLaunch OS installs these from Flathub in the background (a notification says
when they're ready). Each updates itself and can be removed in Discover. An app you remove stays removed.
The full set is a few GB; to skip the creative suite and games, run `omnilaunch-os apps essential`
(`omnilaunch-os apps full` brings them back; switching never removes anything).

| For | App | Instead of |
|---|---|---|
| Web, email | Firefox, Thunderbird | Chrome, Outlook |
| Office | LibreOffice (opens Word, Excel, PowerPoint files) | Microsoft Office |
| PDFs | Okular (read, fill in forms, sign, annotate) | Acrobat Reader |
| Photo editing | GIMP | Photoshop |
| Painting | Krita | Photoshop / Fresco |
| Vector graphics | Inkscape | Illustrator |
| RAW photos | darktable | Lightroom |
| Video editing | Kdenlive | Premiere Pro |
| 3D and motion | Blender | After Effects, Dimension |
| Page layout | Scribus | InDesign |
| Audio editing | Audacity | Audition |
| Recording and streaming | OBS Studio | – |
| Video conversion | HandBrake | Media Encoder |
| Everyday | VLC (videos), Elisa (music), Gwenview (photos), Kate (text), Ark (archives), KCalc, KeePassXC (passwords), LocalSend (like AirDrop) | |
| Games (PC) | Steam, Heroic (Epic, GOG, Amazon), ProtonUp-Qt | |

**Default apps** are set for every common file type: Firefox for links, Thunderbird for email, LibreOffice
for Office files, Okular for PDFs, Gwenview for photos, VLC for video and music, GIMP for `.psd`,
Inkscape for `.svg` and `.ai`, darktable for camera RAW files, Ark for archives, and OmniLaunch for
Windows and Android apps. To change them:

- **App menu → Default Apps** sets the browser, email, file manager, terminal and so on.
- **App menu → Default Apps for File Types** sets any kind of file. Or right-click a file → **Open With →
  Other Application**, pick one, and tick **Remember**.

**More apps:** open **Discover**, the app store, and search. It installs Linux apps from Flathub, and
updates them. Windows `.exe` and Android `.apk` files just open with a double-click. From a terminal,
`omnilaunch-os install <name>` does the same as Discover.

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
omnilaunch-os apps        # app store, and how app setup is going
omnilaunch-os defaults    # choose default apps
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

## Security

**What's protected:**

- **Updates:**
  - The OS updates itself daily, and you can roll back from the boot menu.
  - Flathub apps get security updates daily too, not only when you open Discover.
  - Once you've set up a signing key (below), installed systems accept only OS updates signed with it.
- **Firewall:** on, refusing incoming connections, except for finding printers and devices, file sharing,
  KDE Connect, LocalSend and Steam Remote Play. Fedora's desktop default allows every port above 1024.
- **Downloaded apps:** OmniLaunch asks before opening a Windows, Android or Linux app downloaded from the
  internet, the way macOS and Windows do for their own apps, and remembers your answer.
- **Where software comes from:**
  - Fedora and RPM Fusion packages are signature-checked. RPM Fusion's own setup packages are checked
    against its keys from Fedora's `distribution-gpg-keys`.
  - Wine and umu-launcher must match the SHA-256 GitHub publishes for them.
  - Android SDK files must match the checksums in Google's index.
- **Build pipeline:**
  - Every GitHub Action is pinned to an exact commit, and so is Titanoboa, the ISO builder.
  - Each job's token has only the permissions it needs, and isn't left on the build machine.
  - Every script is syntax-checked, and every image passes `build/check.sh`, before anything is published.

**Signing your images (recommended):**

1. Run `./distro/make-signing-key.sh` on your Mac (needs `brew install cosign`).
2. Commit `distro/signing/cosign.pub`.
3. Add the `COSIGN_PRIVATE_KEY` and `COSIGN_PASSWORD` repository secrets.

From then on, every image is signed and verified in CI, and the build refuses to publish an unsigned one.
Installed systems switch to signature-checked updates on their first start. (The installer itself can't
check signatures, so the switch happens just after installing.)

**Known limits:**

- **Wine isn't a sandbox.** A Windows app can read and change your files, like an app you install
  normally. Only open apps you trust.
- **NVIDIA and Secure Boot.** The driver loads only with Secure Boot off, or after enrolling a signing key
  (see Hardware support). Nouveau stays disabled, because NVIDIA's driver can't load if nouveau takes the
  card first. So NVIDIA cards older than RTX 20 / GTX 16, and any NVIDIA card while Secure Boot blocks
  the driver, run on basic graphics.
- **The live USB runs SELinux in permissive mode,** because files in the image carry no SELinux labels.
  The installed system enforces SELinux.
