#!/usr/bin/bash
# The newest Fedora kernel and all hardware firmware, so OmniLaunch OS supports the latest hardware.
# Runs first in the image build; later stages build drivers (NVIDIA) against this exact kernel.
set -euo pipefail
ARCH=$(uname -m)
log() { printf '\n==> %s\n' "$*"; }

log "Updating to the newest Fedora packages, kernel included"
dnf -y upgrade --refresh

firmware=(
    linux-firmware kernel-modules-extra
    amd-gpu-firmware intel-gpu-firmware nvidia-gpu-firmware
    iwlwifi-mvm-firmware iwlwifi-dvm-firmware intel-audio-firmware intel-vsc-firmware
    realtek-firmware mt7xxx-firmware atheros-firmware brcmfmac-firmware libertas-firmware
    cirrus-audio-firmware alsa-sof-firmware
)
case $ARCH in
    x86_64)  firmware+=(amd-ucode-firmware microcode_ctl) ;;
    aarch64) firmware+=(qcom-firmware) ;;
esac
log "Installing firmware and extra drivers"
dnf -y install --skip-unavailable "${firmware[@]}"
for p in linux-firmware kernel-modules-extra; do rpm -q "$p" >/dev/null; done

# Exactly one kernel: the newest. (dnf keeps old kernels; a bootable image must have just one.)
ver() { rpm -q --qf '%{VERSION}-%{RELEASE}\n' kernel-core | sort -V; }
newest=$(ver | tail -1)
for old in $(ver | grep -vxF "$newest" || true); do
    log "Removing the older kernel $old"
    mapfile -t pkgs < <(rpm -qa --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n' 'kernel*' | grep -F -- "-$old.")
    rpm -e --nodeps --noscripts "${pkgs[@]}"
    rm -rf "/usr/lib/modules/$old.$ARCH"
done
KVER="$newest.$ARCH"
[[ $(ls /usr/lib/modules | wc -l) == 1 && -d /usr/lib/modules/$KVER ]] || {
    echo "Expected only /usr/lib/modules/$KVER, found: $(ls /usr/lib/modules)" >&2; exit 1; }

# NTSYNC: Windows-style synchronisation in the kernel. Wine and Proton use it to run games faster.
if grep -Eq '^CONFIG_NTSYNC=(y|m)' "/usr/lib/modules/$KVER/config"; then
    echo ntsync > /usr/lib/modules-load.d/ntsync.conf
else
    echo "Note: this kernel has no NTSYNC; Wine will use its slower fallback"
fi

log "Building the start-up image (initramfs) for $KVER with the new firmware"
DRACUT_NO_XATTR=1 dracut --no-hostonly --kver "$KVER" --reproducible --zstd --add ostree \
    -f "/usr/lib/modules/$KVER/initramfs.img"

echo "$KVER" > /usr/lib/modules/.omnilaunch-kernel
dnf clean all
rm -rf /var/cache/* /var/log/* /var/lib/dnf 2>/dev/null || true
log "Kernel $KVER ready"
