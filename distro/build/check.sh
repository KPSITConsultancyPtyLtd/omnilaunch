#!/usr/bin/bash
# Checks a built OmniLaunch OS image. Run inside it:
#   sudo podman run --rm -v ./distro/build/check.sh:/check.sh:ro localhost/omnilaunch-os bash /check.sh
set -uo pipefail
ARCH=$(uname -m)
fails=0
check() {  # check "what" command...
    local what=$1; shift
    if out=$("$@" 2>&1); then echo "PASS  $what${out:+: $(echo "$out" | head -1 | cut -c1-150)}"
    else echo "FAIL  $what: $(echo "$out" | tail -3 | tr '\n' ' ' | cut -c1-300)"; fails=$((fails + 1)); fi
}

check "branded as OmniLaunch OS" grep -h '^PRETTY_NAME=.*OmniLaunch OS' /usr/lib/os-release
check "omnilaunch is installed" omnilaunch --version
check "omnilaunch doctor runs" omnilaunch doctor
check "default app for .exe files" grep -h '^application/x-ms-dos-executable=omnilaunch.desktop' /etc/xdg/mimeapps.list
check "default app for .apk files" grep -h '^application/vnd.android.package-archive=omnilaunch.desktop' /etc/xdg/mimeapps.list
check "OmniLaunch desktop entry" grep -h '^Exec=/usr/bin/omnilaunch run --gui %f' /usr/share/applications/omnilaunch.desktop
check "update says it comes with the system" bash -c 'omnilaunch update | grep -q "comes with OmniLaunch OS"'
KVER=$(cat /usr/lib/modules/.omnilaunch-kernel 2>/dev/null)
check "exactly one kernel, the newest ($KVER)" bash -c '[ "$(ls /usr/lib/modules)" = "'"$KVER"'" ]'
check "start-up image built for it" test -s "/usr/lib/modules/$KVER/initramfs.img"
check "all firmware and extra drivers" rpm -q linux-firmware kernel-modules-extra amd-gpu-firmware intel-gpu-firmware
check "NTSYNC (faster Wine/Proton games)" bash -c 'grep -Eq "^CONFIG_NTSYNC=(y|m)" /usr/lib/modules/'"$KVER"'/config && grep -qx ntsync /usr/lib/modules-load.d/ntsync.conf'
check "hardware video decoding for all codecs" rpm -q mesa-va-drivers-freeworld
check "automatic updates" systemctl is-enabled omnilaunch-os-update.timer
check "omnilaunch-os helper" bash -c 'omnilaunch-os help | grep -q rollback'
check "Flathub in Discover" grep -q "dl.flathub.org" /etc/flatpak/remotes.d/flathub.flatpakrepo
check "Distrobox (apps from other Linux distros)" rpm -q distrobox
check "apps install from Flathub on first start" bash -c 'systemctl is-enabled omnilaunch-os-apps.service && grep -q "^org.gimp.GIMP" /usr/share/omnilaunch-os/apps.list'
check "welcome message at login" test -x /usr/libexec/omnilaunch-os-welcome
check "default browser" grep -Eq "^x-scheme-handler/https=(org.mozilla.firefox|firefox).desktop" /etc/xdg/kde-mimeapps.list
check "default apps for documents, PDFs, photos, video" bash -c 'for d in "application/pdf=org.kde.okular" "application/vnd.openxmlformats-officedocument.wordprocessingml.document=org.libreoffice.LibreOffice.writer" "image/jpeg=org.kde.gwenview" "video/mp4=org.videolan.VLC" "image/vnd.adobe.photoshop=org.gimp.GIMP"; do grep -q "^$d.desktop" /etc/xdg/kde-mimeapps.list /etc/xdg/mimeapps.list || exit 1; done'
check "Windows/Android files still open with OmniLaunch" grep -q "^application/x-ms-dos-executable=omnilaunch.desktop" /etc/xdg/kde-mimeapps.list
check "Default Apps in the app menu" test -f /usr/share/applications/omnilaunch-os-default-apps.desktop
check "printers and scanners" rpm -q hplip gutenprint-cups sane-airscan ipp-usb
check "GPU compute (OpenCL) and Office-compatible fonts" rpm -q mesa-libOpenCL google-carlito-fonts
check "Waydroid (Android apps)" rpm -q waydroid
check "Podman (other Linux apps)" rpm -q podman

python3 - > /tmp/app.exe <<'EOF'
import struct, sys
h = bytearray(512); h[:2] = b"MZ"; struct.pack_into("<I", h, 0x3C, 0x80); h[0x80:0x84] = b"PE\0\0"
struct.pack_into("<H", h, 0x84, 0x8664); struct.pack_into("<H", h, 0x96, 0x0102)
struct.pack_into("<H", h, 0x98, 0x20B); struct.pack_into("<H", h, 0x98 + 68, 2)
sys.stdout.buffer.write(bytes(h))
EOF

if [[ $ARCH == x86_64 ]]; then
    check "NVIDIA open driver for this kernel" bash -c 'modinfo -k '"$KVER"' -F license nvidia | grep -q MIT'
    check "NVIDIA driver and 32-bit game libraries" rpm -q xorg-x11-drv-nvidia xorg-x11-drv-nvidia-libs.i686 libva-nvidia-driver
    check "NVIDIA chosen over nouveau at boot" grep -q "nvidia-drm.modeset=1" /usr/lib/bootc/kargs.d/00-nvidia.toml
    check "no driver rebuilds on a read-only system" bash -c '! rpm -q akmods'
    check "Steam, Gamescope, MangoHud" rpm -q steam gamescope mangohud
    check "Intel video decoding" rpm -q intel-media-driver
    check "Wine" wine --version
    check "Proton launcher (umu)" command -v umu-run
    check "a Windows app is planned with Wine/Proton" bash -c 'omnilaunch run --dry-run /tmp/app.exe 2>&1 | grep -Ei "wine|proton"'
else
    check "FEX (Intel programs on ARM)" rpm -q fex-emu
    check "FEX x86 libraries" rpm -q fex-emu-rootfs-fedora
    check "Intel Wine installed" bash -c 'cat /usr/lib/omnilaunch-wine/OMNILAUNCH-SOURCE && test -x "$(readlink -f /usr/bin/wine)"'
    check "Intel Wine is an x86-64 program (for FEX)" python3 -c '
import os, sys
data = open(os.path.realpath("/usr/bin/wine"), "rb").read(20)
sys.exit(0 if data[:4] == b"\x7fELF" and int.from_bytes(data[18:20], "little") == 62 else 1)'
    check "a Windows app is planned with Wine" bash -c 'omnilaunch run --dry-run /tmp/app.exe 2>&1 | grep -i "wine"'
fi

echo
if ((fails)); then echo "$fails check(s) failed"; exit 1; fi
echo "all OmniLaunch OS checks passed ($ARCH)"
