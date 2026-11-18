#!/usr/bin/bash
# Graphics drivers and media support for the image: NVIDIA (from the build stage), full hardware
# video decoding, and the kernel settings NVIDIA needs. Runs during the image build only.
set -euo pipefail
ARCH=$(uname -m)
fedora=$(rpm -E %fedora)
log() { printf '\n==> %s\n' "$*"; }

log "Enabling RPM Fusion (full video codecs, NVIDIA)"
dnf -y install \
    "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-${fedora}.noarch.rpm" \
    "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-${fedora}.noarch.rpm"

log "Hardware video decoding for every codec (H.264, HEVC, AV1)"
if rpm -q mesa-va-drivers >/dev/null 2>&1; then
    dnf -y swap mesa-va-drivers mesa-va-drivers-freeworld --allowerasing
else
    dnf -y install mesa-va-drivers-freeworld
fi
# VDPAU is an older interface that newer Mesa dropped; only swap it where it still exists.
if rpm -q mesa-vdpau-drivers >/dev/null 2>&1 && dnf -q info mesa-vdpau-drivers-freeworld >/dev/null 2>&1; then
    dnf -y swap mesa-vdpau-drivers mesa-vdpau-drivers-freeworld --allowerasing
fi
dnf -y install --skip-unavailable libavcodec-freeworld gstreamer1-plugins-bad-freeworld gstreamer1-plugins-ugly \
    gstreamer1-plugin-openh264 ffmpegthumbnailer
if [[ $ARCH == x86_64 ]]; then
    dnf -y install --skip-unavailable mesa-va-drivers-freeworld.i686 \
        intel-media-driver libva-intel-driver            # Intel graphics video decoding (new and old)
fi
rpm -q mesa-va-drivers-freeworld >/dev/null

log "Printers, scanners, GPU compute, game controllers, fingerprint readers, Office-compatible fonts"
dnf -y install --skip-unavailable \
    hplip gutenprint-cups foomatic-db-ppds epson-inkjet-printer-escpr epson-inkjet-printer-escpr2 \
    brlaser splix c2esp ipp-usb sane-backends-drivers-scanners sane-airscan \
    mesa-libOpenCL \
    game-devices-udev steam-devices \
    fprintd fprintd-pam \
    google-carlito-fonts google-crosextra-caladea-fonts liberation-fonts \
    google-noto-sans-cjk-fonts google-noto-emoji-fonts fira-code-fonts jetbrains-mono-fonts
for p in hplip gutenprint-cups sane-airscan mesa-libOpenCL google-carlito-fonts; do
    rpm -q "$p" >/dev/null || { echo "Expected $p" >&2; exit 1; }
done

if [[ $ARCH == x86_64 ]]; then
    shopt -s nullglob
    kmods=(/tmp/nvidia/kmod-nvidia-*.rpm)
    [[ ${#kmods[@]} -gt 0 ]] || { echo "The NVIDIA driver wasn't built" >&2; exit 1; }
    log "Installing NVIDIA's driver (open kernel module) and its 32-bit libraries for games"
    echo "%_with_kmod_nvidia_open 1" > /etc/rpm/macros.nvidia-kmod
    dnf -y install --exclude=akmod-nvidia,akmods "${kmods[@]}" \
        xorg-x11-drv-nvidia xorg-x11-drv-nvidia-libs xorg-x11-drv-nvidia-libs.i686 \
        xorg-x11-drv-nvidia-cuda-libs xorg-x11-drv-nvidia-power nvidia-settings libva-nvidia-driver
    # akmods would try to rebuild the driver on a read-only system; the image already has it.
    rpm -q akmod-nvidia >/dev/null 2>&1 && rpm -e --nodeps akmod-nvidia
    rpm -q akmods >/dev/null 2>&1 && rpm -e --nodeps akmods
    systemctl enable nvidia-suspend.service nvidia-resume.service nvidia-hibernate.service 2>/dev/null || true

    # Use NVIDIA's driver instead of nouveau. If nouveau stays enabled it claims the card first (it's in
    # the start-up image) and NVIDIA's driver can't load, so RTX cards lose acceleration. The cost:
    # NVIDIA cards older than RTX 20 / GTX 16 (not supported by this driver), and any NVIDIA card while
    # Secure Boot blocks the unsigned driver, run on basic graphics. distro/README.md explains both.
    mkdir -p /usr/lib/bootc/kargs.d
    cat > /usr/lib/bootc/kargs.d/00-nvidia.toml <<'EOF'
kargs = ["rd.driver.blacklist=nouveau", "modprobe.blacklist=nouveau", "nvidia-drm.modeset=1", "nvidia-drm.fbdev=1"]
match-architectures = ["x86_64"]
EOF
    cat > /usr/lib/modprobe.d/omnilaunch-nvidia.conf <<'EOF'
blacklist nouveau
options nouveau modeset=0
options nvidia NVreg_PreserveVideoMemoryAllocations=1
EOF

    if [[ -s /tmp/nvidia/secure-boot.der ]]; then
        install -D -m 0644 /tmp/nvidia/secure-boot.der /usr/share/omnilaunch-os/secure-boot.der
    fi
fi

dnf clean all
