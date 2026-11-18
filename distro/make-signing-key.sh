#!/usr/bin/env bash
# Makes the key that signs OmniLaunch OS images, so installed systems accept only updates you built.
# Run once on your Mac (needs cosign: brew install cosign), from the repository folder:
#   ./distro/make-signing-key.sh
# Then:
#   1. Commit distro/signing/cosign.pub (the public half; safe to share).
#   2. GitHub → the omnilaunch repo → Settings → Secrets and variables → Actions → add:
#        COSIGN_PRIVATE_KEY  = the contents of omnilaunch-cosign.key (printed below)
#        COSIGN_PASSWORD     = the password you choose now
#   3. Keep omnilaunch-cosign.key somewhere private (a password manager), then delete it here.
# From the next build on, every image is signed, the build refuses to publish an unsigned one, and
# installed systems refuse updates that aren't signed with this key.
set -euo pipefail
command -v cosign >/dev/null || { echo "Install cosign first:  brew install cosign" >&2; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$here/signing"
cosign generate-key-pair --output-key-prefix omnilaunch-cosign
mv omnilaunch-cosign.pub "$here/signing/cosign.pub"
chmod 600 omnilaunch-cosign.key
echo
echo "Public key saved to distro/signing/cosign.pub (commit it)."
echo "COSIGN_PRIVATE_KEY secret:"
cat omnilaunch-cosign.key
