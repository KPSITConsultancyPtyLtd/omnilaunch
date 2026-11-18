#!/usr/bin/bash
# Turns the OmniLaunch OS image into a live system for a USB stick, with an installer.
# Follows the container-native ISO contract that titanoboa builds from; adapted from titanoboa's
# examples (github.com/ublue-os/titanoboa, Apache-2.0).
set -exo pipefail
SRC=$(dirname "$(realpath "$0")")
ARCH=$(uname -m)
LABEL=OmniLaunch-OS

mkdir -p "$(realpath /root)"

# ---------------------------------------------------------------- what the installer installs
# The installer downloads OmniLaunch OS from its online registry, so the installed system gets
# automatic updates from there and the USB stays small.
IMAGE_REF=${IMAGE_REF:?the online image to install, e.g. ghcr.io/owner/omnilaunch-os:latest}
# A tag, not a fixed digest: the installed system follows it, which is how it gets updates.
[[ $IMAGE_REF =~ ^[a-z0-9.-]+/[a-z0-9._/-]+:[A-Za-z0-9._-]+$ ]] || { echo "IMAGE_REF must be registry/name:tag" >&2; exit 1; }

# ---------------------------------------------------------------- boot from USB
dnf install -y dracut-live livesys-scripts
kernel=$(ls /usr/lib/modules | sort -V | tail -1)
DRACUT_NO_XATTR=1 dracut -v --force --zstd --reproducible --no-hostonly \
    --add "dmsquash-live dmsquash-live-autooverlay" \
    "/usr/lib/modules/${kernel}/initramfs.img" "${kernel}"
sed -i "s/^livesys_session=.*/livesys_session=kde/" /etc/sysconfig/livesys
systemctl enable livesys.service livesys-late.service

# ---------------------------------------------------------------- installer
dnf install -y anaconda-live libblockdev-btrfs libblockdev-lvm libblockdev-dm
mkdir -p /var/lib/rpm-state /etc/anaconda/profile.d
cat > /etc/anaconda/profile.d/omnilaunch-os.conf <<'EOF'
[Profile]
profile_id = omnilaunch-os
base_profile = fedora-kinoite

[Profile Detection]
os_id = fedora
variant_id = omnilaunch-os

[Storage]
default_scheme = BTRFS
btrfs_compression = zstd:1

[Localization]
use_geolocation = False
EOF
cat >> /usr/share/anaconda/interactive-defaults.ks <<EOF

# Install OmniLaunch OS from its online registry (needs internet during installation).
# Images aren't signed yet, so signature checks are skipped (see distro/README.md, Security).
ostreecontainer --url=${IMAGE_REF} --no-signature-verification
EOF
echo "${IMAGE_REF}" > /usr/share/omnilaunch-os-installs

# ---------------------------------------------------------------- what titanoboa needs
case $ARCH in
    x86_64)  dnf install -y grub2-efi-x64-cdboot ;;
    aarch64) dnf install -y grub2-efi-aa64-modules ;;
esac
mkdir -p /boot/efi
cp -av /usr/lib/efi/*/*/EFI /boot/efi/
case $ARCH in
    x86_64)  cp -v /boot/efi/EFI/fedora/grubx64.efi /boot/efi/EFI/BOOT/fbx64.efi ;;
    aarch64) cp -v /boot/efi/EFI/fedora/grubaa64.efi /boot/efi/EFI/BOOT/fbaa64.efi ;;
esac

rm -f /etc/localtime
systemd-firstboot --timezone UTC

# The live system's / is an overlay in RAM with a small /run; give /var/tmp room to install.
rm -rf /var/tmp && mkdir -p /var/tmp
cat > /etc/systemd/system/var-tmp.mount <<'EOF'
[Unit]
Description=Larger tmpfs for /var/tmp on the live system

[Mount]
What=tmpfs
Where=/var/tmp
Type=tmpfs
Options=size=50%%,nr_inodes=1m

[Install]
WantedBy=local-fs.target
EOF
systemctl enable var-tmp.mount

mkdir -p /usr/lib/bootc-image-builder
sed "s/@LABEL@/$LABEL/g" "$SRC/iso.yaml" > /usr/lib/bootc-image-builder/iso.yaml

dnf clean all
