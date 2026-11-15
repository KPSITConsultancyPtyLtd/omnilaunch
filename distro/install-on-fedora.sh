#!/usr/bin/bash
# Turns an existing Fedora install into OmniLaunch OS. Made for Fedora Asahi Remix on Apple Silicon
# Macs (where the OmniLaunch OS USB can't boot), and works on any regular Fedora 43 or newer.
#
#   git clone https://github.com/KPSITConsultancyPtyLtd/omnilaunch && cd omnilaunch
#   ./distro/install-on-fedora.sh
set -euo pipefail
here=$(dirname "$(realpath "$0")")
repo=$(dirname "$here")

. /etc/os-release
if [[ ${ID:-} != fedora ]]; then
    echo "This is for Fedora (including Fedora Asahi Remix); this system is ${PRETTY_NAME:-unknown}." >&2
    exit 1
fi
if [[ -e /run/ostree-booted ]]; then
    echo "This is an image-based Fedora (Atomic/bootc). Switch to OmniLaunch OS instead:" >&2
    echo "  boot the OmniLaunch OS USB and install, or rebase with bootc to an OmniLaunch OS image." >&2
    exit 1
fi
if [[ $(getconf PAGESIZE) != 4096 ]]; then
    echo "Note: this Mac uses 16K memory pages, so Intel programs (including Windows apps) run through"
    echo "FEX inside a small virtual machine (muvm). It's automatic, but start-up takes a little longer."
fi

sudo bash "$here/build/install-layer.sh" "$repo"
echo
echo "Done. Double-click any .exe, .msi, .apk or .AppImage file to run it with OmniLaunch."
echo "Check what's ready with:  omnilaunch doctor"
