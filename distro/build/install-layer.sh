#!/usr/bin/bash
# Turns Fedora into OmniLaunch OS: installs everything OmniLaunch uses to run other systems' apps,
# then OmniLaunch itself, system-wide, as the default app for Windows, Android and AppImage files.
#
# Used in two places, so both get exactly the same system:
#   - the OmniLaunch OS image build (distro/Containerfile), with --image
#   - an existing Fedora install, such as Fedora Asahi Remix on an Apple Silicon Mac
#     (distro/install-on-fedora.sh)
#
# usage: install-layer.sh [--image] <omnilaunch source folder>
set -euo pipefail

IMAGE=false
if [[ ${1:-} == --image ]]; then IMAGE=true; shift; fi
SRC=$(realpath "${1:?usage: install-layer.sh [--image] <omnilaunch source folder>}")
ARCH=$(uname -m)
. /etc/os-release
log() { printf '\n==> %s\n' "$*"; }

# ---------------------------------------------------------------- helpers from Fedora's own repositories
common=(
    python3            # OmniLaunch itself
    waydroid           # Android apps, in a container sharing this kernel
    podman distrobox   # Linux apps built for other distributions or CPUs
    xdg-utils          # file associations
    7zip cabextract unzip
    vulkan-loader mesa-vulkan-drivers
)
x86_64=(
    steam gamescope mangohud    # games: Steam, its game-mode compositor, an fps overlay
    wine wine-mono winetricks   # Windows programs
    umu-launcher                # Proton, the way Steam runs Windows games (else from its releases)
    gamemode
    vulkan-loader.i686 mesa-vulkan-drivers.i686
)
aarch64=(
    fex-emu fex-emu-utils fex-emu-rootfs-fedora   # runs Intel (x86) programs on ARM
    binfmt-dispatcher muvm                        # picks FEX automatically; muvm for 16K-page Macs
    erofs-fuse squashfuse
)
required_x86_64=(wine)
required_aarch64=(fex-emu)

case $ARCH in
    x86_64)  wanted=("${common[@]}" "${x86_64[@]}");  required=("${required_x86_64[@]}") ;;
    aarch64) wanted=("${common[@]}" "${aarch64[@]}"); required=("${required_aarch64[@]}") ;;
    *) echo "OmniLaunch OS supports x86_64 and aarch64, not $ARCH" >&2; exit 1 ;;
esac

if [[ $ARCH == x86_64 ]] && ! rpm -q rpmfusion-nonfree-release >/dev/null 2>&1; then
    log "Enabling RPM Fusion (Steam)"
    dnf -y install \
        "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm" \
        "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm"
fi

log "Installing compatibility helpers for $ARCH"
dnf -y install --skip-unavailable "${wanted[@]}"
missing=()
for p in "${wanted[@]}"; do rpm -q "$p" >/dev/null 2>&1 || missing+=("$p"); done
if ((${#missing[@]})); then echo "Not available on this Fedora (skipped): ${missing[*]}"; fi
for p in "${required[@]}"; do rpm -q "$p" >/dev/null || { echo "Required package $p is missing" >&2; exit 1; }; done

# ---------------------------------------------------------------- Intel Wine for ARM, run through FEX
# Fedora doesn't build Wine for ARM, and Windows programs are almost all Intel anyway. An Intel
# (amd64, WoW64) Wine build runs through FEX transparently: binfmt-dispatcher hands any x86 program
# to FEX (inside muvm on Apple Silicon's 16K pages), so `wine` just works.
if [[ $ARCH == aarch64 ]]; then
    log "Installing Intel Wine (translated by FEX)"
    read -r name url < <(PYTHONPATH="$SRC" python3 -c '
import json
from omnilaunch import provision as p
name, url = p.pick_release_asset(json.loads(p._fetch(p.WINE_X64_RELEASES_API)), p.WINE_X64_PATTERNS)
print(name, url)')
    echo "using $name"
    tmp=$(mktemp -d)
    curl -fsSL --retry 4 -o "$tmp/wine.tar.xz" "$url"
    rm -rf /usr/lib/omnilaunch-wine && mkdir -p /usr/lib/omnilaunch-wine
    tar -xJf "$tmp/wine.tar.xz" -C /usr/lib/omnilaunch-wine --strip-components=1
    rm -rf "$tmp"
    for tool in wine wineserver wineboot winecfg msiexec regedit; do
        [[ -e /usr/lib/omnilaunch-wine/bin/$tool ]] && ln -sfn "/usr/lib/omnilaunch-wine/bin/$tool" "/usr/bin/$tool"
    done
    echo "$name" > /usr/lib/omnilaunch-wine/OMNILAUNCH-SOURCE
fi

# ---------------------------------------------------------------- Proton launcher, when Fedora lacks it
if [[ $ARCH == x86_64 ]] && ! command -v umu-run >/dev/null; then
    log "Installing umu-launcher (Proton) from its releases"
    read -r name url < <(PYTHONPATH="$SRC" python3 -c '
import json
from omnilaunch import provision as p
print(*p.pick_release_asset(json.loads(p._fetch(p.UMU_RELEASES_API)), p.UMU_ZIPAPP_PATTERNS))')
    echo "using $name"
    tmp=$(mktemp -d)
    curl -fsSL --retry 4 -o "$tmp/$name" "$url"
    rm -rf /usr/lib/umu && mkdir -p /usr/lib/umu
    tar -xf "$tmp/$name" -C /usr/lib/umu
    rm -rf "$tmp"
    umu=$(find /usr/lib/umu -name umu-run -type f | head -1)
    [[ -n $umu ]] || { echo "umu-run not found in $name" >&2; exit 1; }
    chmod 755 "$umu"
    ln -sfn "$umu" /usr/bin/umu-run
fi

# ---------------------------------------------------------------- OmniLaunch
log "Installing OmniLaunch"
rm -rf /usr/lib/omnilaunch && mkdir -p /usr/lib/omnilaunch
cp -a "$SRC/omnilaunch" "$SRC/bin" /usr/lib/omnilaunch/
cp -a "$SRC/LICENSE" "$SRC/NOTICE" /usr/lib/omnilaunch/ 2>/dev/null || true
find /usr/lib/omnilaunch -name __pycache__ -prune -exec rm -rf {} +
touch /usr/lib/omnilaunch/.omnilaunch-os      # tells `omnilaunch update` it comes with the system
python3 -m compileall -q /usr/lib/omnilaunch/omnilaunch
cat > /usr/bin/omnilaunch <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/lib/omnilaunch/bin/omnilaunch "$@"
EOF
chmod 755 /usr/bin/omnilaunch

log "Making OmniLaunch the default app for Windows, Android and AppImage files"
PYTHONPATH=/usr/lib/omnilaunch python3 - <<'EOF'
from omnilaunch import desktop
desktop.launcher_command = lambda: "/usr/bin/omnilaunch"
with open("/usr/share/applications/omnilaunch.desktop", "w") as f:
    f.write(desktop.desktop_entry())
with open("/tmp/omnilaunch-defaults.list", "w") as f:
    f.writelines(f"{m}=omnilaunch.desktop\n" for m in desktop.MIME_TYPES)
EOF
python3 "$SRC/distro/build/set-defaults.py" /tmp/omnilaunch-defaults.list
rm -f /tmp/omnilaunch-defaults.list
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true

# ---------------------------------------------------------------- image only
if $IMAGE; then
    log "Branding"
    sed -i \
        -e 's/^NAME=.*/NAME="OmniLaunch OS"/' \
        -e "s/^PRETTY_NAME=.*/PRETTY_NAME=\"OmniLaunch OS ${VERSION_ID:-$(rpm -E %fedora)} (Fedora Kinoite based)\"/" \
        -e 's/^VARIANT=.*/VARIANT="OmniLaunch OS"/' \
        -e 's/^VARIANT_ID=.*/VARIANT_ID=omnilaunch-os/' \
        -e 's|^HOME_URL=.*|HOME_URL="https://github.com/KPSITConsultancyPtyLtd/omnilaunch"|' \
        /usr/lib/os-release
    grep -q '^VARIANT_ID=' /usr/lib/os-release || echo 'VARIANT_ID=omnilaunch-os' >> /usr/lib/os-release

    log "Easy upkeep: automatic updates, the omnilaunch-os helper, Flathub in Discover"
    install -m 0755 "$SRC/distro/build/omnilaunch-os" /usr/bin/omnilaunch-os
    cat > /usr/lib/systemd/system/omnilaunch-os-update.service <<'EOF'
[Unit]
Description=Download the newest OmniLaunch OS (used from the next restart)
Documentation=man:bootc-upgrade(8)
ConditionPathExists=/run/ostree-booted
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/bootc upgrade --quiet
EOF
    cat > /usr/lib/systemd/system/omnilaunch-os-update.timer <<'EOF'
[Unit]
Description=Check for a newer OmniLaunch OS every day

[Timer]
OnBootSec=15min
OnUnitActiveSec=1d
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF
    systemctl enable omnilaunch-os-update.timer
    # The updater above replaces rpm-ostree's own, which would fight over the same deployments.
    systemctl disable rpm-ostreed-automatic.timer 2>/dev/null || true

    mkdir -p /etc/flatpak/remotes.d
    curl -fsSL --retry 4 -o /etc/flatpak/remotes.d/flathub.flatpakrepo \
        https://dl.flathub.org/repo/flathub.flatpakrepo

    log "Apps: chosen defaults, installed from Flathub on first start, easy to change"
    install -D -m 0644 "$SRC/distro/build/apps.list" /usr/share/omnilaunch-os/apps.list
    install -m 0755 "$SRC/distro/build/omnilaunch-os-apps" /usr/libexec/omnilaunch-os-apps
    install -m 0755 "$SRC/distro/build/omnilaunch-os-welcome" /usr/libexec/omnilaunch-os-welcome
    python3 "$SRC/distro/build/set-defaults.py" "$SRC/distro/build/defaults.list"
    cat > /usr/lib/systemd/system/omnilaunch-os-apps.service <<'EOF'
[Unit]
Description=Install OmniLaunch OS's apps from Flathub
ConditionPathExists=/run/ostree-booted
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/libexec/omnilaunch-os-apps
Restart=on-failure
RestartSec=10min
TimeoutStartSec=3h
Nice=10
IOSchedulingClass=idle

[Install]
WantedBy=multi-user.target
EOF
    systemctl enable omnilaunch-os-apps.service
    cat > /etc/xdg/autostart/omnilaunch-os-welcome.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=OmniLaunch OS welcome
Exec=/usr/libexec/omnilaunch-os-welcome
NoDisplay=true
X-KDE-autostart-phase=2
EOF
    # Easy-to-find menu entries for changing default apps (they open the right System Settings pages).
    cat > /usr/share/applications/omnilaunch-os-default-apps.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Default Apps
Comment=Choose which app opens web links, email, music, videos and more
Exec=systemsettings kcm_componentchooser
Icon=preferences-desktop-default-applications
Categories=Settings;
Keywords=default;browser;email;open with;
EOF
    cat > /usr/share/applications/omnilaunch-os-file-types.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Default Apps for File Types
Comment=Choose which app opens each kind of file
Exec=systemsettings kcm_filetypes
Icon=preferences-desktop-filetype-association
Categories=Settings;
Keywords=file type;association;open with;default;
EOF

    # Waydroid's Android container should be ready to start whenever an .apk is opened.
    systemctl enable waydroid-container.service 2>/dev/null || true

    dnf clean all
    rm -rf /var/cache/* /var/log/* /var/lib/dnf /tmp/* 2>/dev/null || true
fi

log "OmniLaunch OS layer installed"
