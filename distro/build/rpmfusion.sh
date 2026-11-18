#!/usr/bin/bash
# Enables RPM Fusion (free and nonfree) with its packages' signatures checked. RPM Fusion's release
# packages are downloaded by web address, so first import RPM Fusion's signing keys from Fedora's own
# distribution-gpg-keys package (itself signed by Fedora) and refuse release packages not signed by them.
set -euo pipefail
fedora=$(rpm -E %fedora)
if rpm -q rpmfusion-free-release rpmfusion-nonfree-release >/dev/null 2>&1; then exit 0; fi

dnf -y install distribution-gpg-keys
keys=/usr/share/distribution-gpg-keys/rpmfusion
for repo in free nonfree; do
    key="$keys/RPM-GPG-KEY-rpmfusion-$repo-fedora-$fedora"
    [[ -f $key ]] || { echo "No RPM Fusion $repo signing key for Fedora $fedora in distribution-gpg-keys" >&2; exit 1; }
    rpm --import "$key"
done
dnf -y install --setopt=localpkg_gpgcheck=1 \
    "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-${fedora}.noarch.rpm" \
    "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-${fedora}.noarch.rpm"
# Every package from RPM Fusion's repositories is signature-checked from here on.
grep -q '^gpgcheck=1' /etc/yum.repos.d/rpmfusion-free.repo /etc/yum.repos.d/rpmfusion-nonfree.repo
