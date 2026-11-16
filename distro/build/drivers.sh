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
for pair in "mesa-va-drivers mesa-va-drivers-freeworld" "mesa-vdpau-drivers mesa-vdpau-drivers-freeworld"; do
    set -- $pair
    if rpm -q "$1" >/dev/null 2>&1; then dnf -y swap "$1" "$2" --allowerasing; else dnf -y install "$2"; fi
done
dnf -y install --skip-unavailable libavcodec-freeworld gstreamer1-plugins-bad-freeworld gstreamer1-plugins-ugly
[[ $ARCH == x86_64 ]] && dnf -y install --skip-unavailable \
    mesa-va-drivers-freeworld.i686 mesa-vdpau-drivers-freeworld.i686
rpm -q mesa-va-drivers-freeworld >/dev/null

if [[ $ARCH == x86_64 ]]; then
    shopt -s nullglob
    kmods=(/tmp/nvidia/kmod-nvidia-*.rpm)
    [[ ${#kmods[@]} -gt 0 ]] || { echo "The NVIDIA driver wasn't built" >&2; exit 1; }
    log "Installing NVIDIA's driver (open kernel module) and its 32-bit libraries for games"
    echo "%_with_kmod_nvidia_open 1" > /etc/rpm/macros.nvidia-kmod
    dnf -y install "${kmods[@]}" \
        xorg-x11-drv-nvidia xorg-x11-drv-nvidia-libs xorg-x11-drv-nvidia-libs.i686 \
        xorg-x11-drv-nvidia-cuda-libs xorg-x11-drv-nvidia-power nvidia-settings libva-nvidia-driver
    # akmods would try to rebuild the driver on a read-only system; the image already has it.
    rpm -q akmod-nvidia >/dev/null 2>&1 && rpm -e --nodeps akmod-nvidia
    rpm -q akmods >/dev/null 2>&1 && rpm -e --nodeps akmods
    systemctl enable nvidia-suspend.service nvidia-resume.service nvidia-hibernate.service 2>/dev/null || true

    # Use NVIDIA's driver instead of nouveau, with modesetting (needed for Wayland).
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
