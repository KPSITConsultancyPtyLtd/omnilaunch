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
OMNILAUNCH_IMAGE=${OMNILAUNCH_IMAGE:-ghcr.io/kpsitconsultancyptyltd/omnilaunch-os}
. /etc/os-release
log() { printf '\n==> %s\n' "$*"; }

# Downloads the newest release file matching OmniLaunch's patterns into a folder and prints its path.
# Refuses a file without a published SHA-256, or one that doesn't match it.
verified_download() {  # verified_download <API constant> <patterns constant> <folder>
    PYTHONPATH="$SRC" python3 -c '
import json, sys
from omnilaunch import provision as p
api, patterns, folder = sys.argv[1:]
try:
    releases = json.loads(p._fetch(getattr(p, api)))
    print(p.download_release_asset(releases, getattr(p, patterns), folder, require_digest=True))
except p.SetupError as e:
    sys.exit(f"Refusing to install: {e}")' "$@"
}

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

if [[ $ARCH == x86_64 ]]; then
    log "Enabling RPM Fusion (Steam), signatures checked"
    bash "$SRC/distro/build/rpmfusion.sh"
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
    log "Installing Intel Wine (translated by FEX), checked against its published SHA-256"
    tmp=$(mktemp -d)
    archive=$(verified_download WINE_X64_RELEASES_API WINE_X64_PATTERNS "$tmp")
    name=$(basename "$archive")
    echo "using $name"
    rm -rf /usr/lib/omnilaunch-wine && mkdir -p /usr/lib/omnilaunch-wine
    tar -xJf "$archive" -C /usr/lib/omnilaunch-wine --strip-components=1
    rm -rf "$tmp"
    for tool in wine wineserver wineboot winecfg msiexec regedit; do
        [[ -e /usr/lib/omnilaunch-wine/bin/$tool ]] && ln -sfn "/usr/lib/omnilaunch-wine/bin/$tool" "/usr/bin/$tool"
    done
    echo "$name" > /usr/lib/omnilaunch-wine/OMNILAUNCH-SOURCE

    # FEX needs a set of Intel system files (its "RootFS"). Fedora ships one but doesn't point FEX at it,
    # and FEX only recognises .ero/.sqsh names, so link it under a name FEX accepts and set it as the
    # system-wide default. (Apple Silicon is different: muvm sets the RootFS up itself, so a Fedora Asahi
    # install keeps Fedora's own setup.)
    if $IMAGE; then
        rootfs=$(find /usr/share/fex-emu/RootFS -maxdepth 1 -name '*.erofs' | head -1)
        [[ -n $rootfs ]] || { echo "No FEX RootFS image in /usr/share/fex-emu/RootFS" >&2; exit 1; }
        ln -sfn "$(basename "$rootfs")" /usr/share/fex-emu/RootFS/omnilaunch.ero
        python3 - <<'EOF'
import json, os
path = "/usr/share/fex-emu/Config.json"
config = {}
if os.path.exists(path):
    with open(path) as f:
        config = json.load(f)
config.setdefault("Config", {})["RootFS"] = "/usr/share/fex-emu/RootFS/omnilaunch.ero"
with open(path, "w") as f:
    json.dump(config, f, indent=2)
print("FEX RootFS:", config["Config"]["RootFS"])
EOF
    fi
fi

# ---------------------------------------------------------------- Proton launcher, when Fedora lacks it
if [[ $ARCH == x86_64 ]] && ! command -v umu-run >/dev/null; then
    log "Installing umu-launcher (Proton) from its releases, checked against its published SHA-256"
    tmp=$(mktemp -d)
    archive=$(verified_download UMU_RELEASES_API UMU_ZIPAPP_PATTERNS "$tmp")
    name=$(basename "$archive")
    echo "using $name"
    rm -rf /usr/lib/umu && mkdir -p /usr/lib/umu
    tar -xf "$archive" -C /usr/lib/umu
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
ProtectHome=yes
PrivateTmp=yes

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

    log "Security: signed updates (if a signing key is set up), firewall, automatic app updates"
    if [[ -f $SRC/distro/signing/cosign.pub ]]; then
        install -D -m 0644 "$SRC/distro/signing/cosign.pub" /etc/pki/containers/omnilaunch-os.pub
        mkdir -p /etc/containers/registries.d
        cat > /etc/containers/registries.d/omnilaunch-os.yaml <<EOF
docker:
  ${OMNILAUNCH_IMAGE}:
    use-sigstore-attachments: true
EOF
        # Accept images from OmniLaunch OS's registry only if signed with its key.
        OMNILAUNCH_IMAGE="$OMNILAUNCH_IMAGE" python3 - <<'EOF'
import json, os
path = "/etc/containers/policy.json"
with open(path) as f:
    policy = json.load(f)
policy.setdefault("transports", {}).setdefault("docker", {})[os.environ["OMNILAUNCH_IMAGE"]] = [{
    "type": "sigstoreSigned",
    "keyPath": "/etc/pki/containers/omnilaunch-os.pub",
    "signedIdentity": {"type": "matchRepository"},
}]
with open(path, "w") as f:
    json.dump(policy, f, indent=2)
EOF
        install -m 0755 "$SRC/distro/build/omnilaunch-os-verify-updates" /usr/libexec/omnilaunch-os-verify-updates
        cat > /usr/lib/systemd/system/omnilaunch-os-verify-updates.service <<'EOF'
[Unit]
Description=Accept only signed OmniLaunch OS updates from now on
ConditionPathExists=/run/ostree-booted
ConditionPathExists=!/var/lib/omnilaunch-os/updates-verified

[Service]
Type=oneshot
ExecStart=/usr/libexec/omnilaunch-os-verify-updates

[Install]
WantedBy=multi-user.target
EOF
        systemctl enable omnilaunch-os-verify-updates.service
    else
        echo "No distro/signing/cosign.pub: images aren't signed yet (see distro/make-signing-key.sh)"
    fi

    # Firewall: nothing on this computer is reachable from the network except what's listed (Fedora's
    # desktop default opens every port from 1025 up).
    cat > /etc/firewalld/zones/omnilaunch.xml <<'EOF'
<?xml version="1.0" encoding="utf-8"?>
<zone>
  <short>OmniLaunch OS</short>
  <description>Incoming connections are refused, except: finding printers and devices on the network,
  file sharing you start, KDE Connect (phones), LocalSend (nearby file sending) and Steam Remote Play.</description>
  <service name="dhcpv6-client"/>
  <service name="mdns"/>
  <service name="samba-client"/>
  <service name="kdeconnect"/>
  <service name="steam-streaming"/>
  <port protocol="tcp" port="53317"/>
  <port protocol="udp" port="53317"/>
</zone>
EOF
    sed -i 's/^DefaultZone=.*/DefaultZone=omnilaunch/' /etc/firewalld/firewalld.conf
    grep -q '^DefaultZone=omnilaunch' /etc/firewalld/firewalld.conf || echo 'DefaultZone=omnilaunch' >> /etc/firewalld/firewalld.conf
    systemctl enable firewalld.service

    # Security fixes for apps: Flathub apps update every day, not only when someone opens Discover.
    cat > /usr/lib/systemd/system/omnilaunch-os-app-updates.service <<'EOF'
[Unit]
Description=Update Flathub apps
ConditionPathExists=/run/ostree-booted
After=network-online.target omnilaunch-os-apps.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/flatpak update --system --noninteractive --assumeyes
ExecStart=/usr/bin/flatpak uninstall --system --unused --noninteractive --assumeyes
Nice=10
IOSchedulingClass=idle
ProtectHome=yes
PrivateTmp=yes
EOF
    cat > /usr/lib/systemd/system/omnilaunch-os-app-updates.timer <<'EOF'
[Unit]
Description=Update Flathub apps every day

[Timer]
OnBootSec=30min
OnUnitActiveSec=1d
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF
    systemctl enable omnilaunch-os-app-updates.timer

    # Waydroid's Android container should be ready to start whenever an .apk is opened.
    systemctl enable waydroid-container.service 2>/dev/null || true

    dnf clean all
    rm -rf /var/cache/* /var/log/* /var/lib/dnf /tmp/* 2>/dev/null || true
fi

log "OmniLaunch OS layer installed"
