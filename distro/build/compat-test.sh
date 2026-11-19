#!/usr/bin/bash
# Runs inside a built OmniLaunch OS image (as a privileged container) to prove it runs real apps from
# other systems. The repository is mounted at /src. Test-only tools are added to this throwaway
# container, never to the image.
#   sudo podman run --rm --privileged --device /dev/fuse -v "$PWD:/src:ro" localhost/omnilaunch-os bash /src/distro/build/compat-test.sh
set -euo pipefail
dnf -y -q install xorg-x11-server-Xvfb xdotool procps-ng >/dev/null

# ARM: register the kernel hook that hands Intel programs to FEX (on an installed system,
# systemd-binfmt does this at boot). Not every build machine's kernel allows it.
if [[ $(uname -m) == aarch64 ]]; then
    mountpoint -q /proc/sys/fs/binfmt_misc || mount -t binfmt_misc binfmt_misc /proc/sys/fs/binfmt_misc || true
    /usr/lib/systemd/systemd-binfmt 2>/dev/null || true
    ls /proc/sys/fs/binfmt_misc 2>/dev/null | grep -iE 'fex|x86|dispatcher' || echo "note: no Intel-program hook registered"
    # Diagnostics for Intel Wine under FEX: each step on its own, to see which one fails.
    echo "--- FEX diagnostics"
    cat /usr/share/fex-emu/Config.json 2>&1 | head -5
    ls -l /usr/lib/omnilaunch-wine/bin/ 2>&1 | head -12
    for cmd in "/usr/lib/omnilaunch-wine/bin/wineserver --version" "/usr/lib/omnilaunch-wine/bin/wine --version" \
               "FEXBash -c /usr/lib/omnilaunch-wine/bin/wineserver\ --version" "FEXInterpreter /usr/lib/omnilaunch-wine/bin/wineserver --version"; do
        echo "\$ $cmd"; timeout 120 bash -c "$cmd" 2>&1 | tail -4 || true
    done
    echo "--- end of FEX diagnostics"
fi

export HOME=/tmp/compat-home XDG_DATA_HOME=/tmp/compat-home/.local/share XDG_DESKTOP_DIR=/tmp/compat-home/Desktop
mkdir -p "$XDG_DATA_HOME" "$XDG_DESKTOP_DIR"
[[ -s /run/github-token ]] && export GITHUB_TOKEN="$(cat /run/github-token)"
export OMNILAUNCH_NO_DIALOGS=1 WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml="
Xvfb :99 -screen 0 1280x800x24 >/dev/null 2>&1 &
export DISPLAY=:99
sleep 2
python3 /src/distro/build/compat-test.py
