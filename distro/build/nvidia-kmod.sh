#!/usr/bin/bash
# Builds NVIDIA's open kernel driver (RTX 20-series / GTX 16-series and newer, including RTX 50
# "Blackwell", which only works with the open driver) for the image's kernel, from RPM Fusion.
# Runs in a throwaway build stage; only the finished driver package is copied into the image, in /out.
#
# Secure Boot: if a signing key is supplied (build secrets mok_key and mok_cert, made with
# distro/make-secure-boot-key.sh), the driver is signed with it and the certificate is shipped so it
# can be enrolled. Without one, the driver loads only with Secure Boot turned off.
set -euo pipefail
mkdir -p /out
if [[ $(uname -m) != x86_64 ]]; then echo "NVIDIA desktop drivers: x86_64 only, skipping"; exit 0; fi
KVER=$(cat /usr/lib/modules/.omnilaunch-kernel)
fedora=$(rpm -E %fedora)

dnf -y install \
    "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-${fedora}.noarch.rpm" \
    "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-${fedora}.noarch.rpm"
echo "%_with_kmod_nvidia_open 1" > /etc/rpm/macros.nvidia-kmod
dnf -y install "kernel-devel-${KVER}" akmods akmod-nvidia elfutils-libelf-devel cpio

if [[ -s /run/secrets/mok_key && -s /run/secrets/mok_cert ]]; then
    echo "Signing the driver for Secure Boot"
    install -D -m 0640 -o root -g akmods /run/secrets/mok_key /etc/pki/akmods/private/private_key.priv
    install -D -m 0644 /run/secrets/mok_cert /etc/pki/akmods/certs/public_key.der
    cp /etc/pki/akmods/certs/public_key.der /out/secure-boot.der
else
    echo "No signing key supplied: the NVIDIA driver will need Secure Boot turned off"
    rm -rf /etc/pki/akmods/private /etc/pki/akmods/certs     # don't sign with a throwaway key
fi

# akmods refuses to build as root, so build with its own builder as the unprivileged akmods user.
srpm=$(readlink -f /usr/src/akmods/nvidia-kmod.latest)
[[ -f $srpm ]] || { echo "No NVIDIA driver source package: $(ls /usr/src/akmods)" >&2; exit 1; }
work=$(mktemp -d) && chown akmods:akmods "$work" && chmod 755 "$work"
if ! runuser -u akmods -- env HOME="$work" TMPDIR="$work" \
        akmodsbuild --kernels "$KVER" --outputdir "$work" "$srpm"; then
    find "$work" -name '*.log' -exec tail -80 {} +
    exit 1
fi
rpms=$(find "$work" -name "kmod-nvidia-${KVER}-*.rpm")
[[ -n $rpms ]] || { echo "akmodsbuild made no driver package for $KVER" >&2; ls -R "$work"; exit 1; }
cp $rpms /out/
# Prove it's the open driver: its licence is "Dual MIT/GPL"; the closed one says "NVIDIA".
rpm2cpio /out/kmod-nvidia-*.rpm | (cd /tmp && cpio -idm --quiet)
ko=$(find /tmp/usr/lib/modules -name 'nvidia.ko*' | head -1)
modinfo -F license "$ko" | tee /out/license
grep -q "MIT" /out/license
ls -l /out
