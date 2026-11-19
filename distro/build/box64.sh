#!/usr/bin/bash
# Builds the newest Box64 release for ARM, in a throwaway build stage. OmniLaunch OS runs Intel Windows
# apps on ARM as "box64 wine": Box64 copes with Wine's preloader, which FEX doesn't. Fedora's own Box64
# lags behind and older releases miss instructions real Windows programs use, so it's built from source,
# the same way OmniLaunch's Box64 path on Macs is (and that path is tested with real programs in CI).
# The result goes in /out (box64, plus its x86 helper libraries in /out/lib).
set -euo pipefail
mkdir -p /out/lib
if [[ $(uname -m) != aarch64 ]]; then echo "Box64: ARM only, skipping"; exit 0; fi
SRC=${1:?usage: box64.sh <omnilaunch source folder>}

dnf -y install cmake make gcc gcc-c++ python3 git
tag=$(PYTHONPATH="$SRC" python3 -c '
import json
from omnilaunch import provision as p
print(p.newest_release_tag(json.loads(p._fetch(p.BOX64_RELEASES_API))))')
echo "Building Box64 $tag"
work=$(mktemp -d)
git clone -q --depth 1 --branch "$tag" https://github.com/ptitSeb/box64 "$work/src"
cmake -S "$work/src" -B "$work/build" -DARM_DYNAREC=ON -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build "$work/build" -j"$(nproc)"
install -m 0755 "$work/build/box64" /out/box64
cp -r "$work/src/x64lib/." /out/lib/ 2>/dev/null || true
echo "$tag" > /out/VERSION
/out/box64 --version
