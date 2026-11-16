#!/usr/bin/env bash
# Makes the key that signs OmniLaunch OS's NVIDIA driver, so it loads with Secure Boot on.
# Run once (macOS or Linux, needs openssl), then add the two printed values as repository secrets:
#   GitHub → the omnilaunch repo → Settings → Secrets and variables → Actions → New repository secret
#     MOK_KEY   (the private key, base64)
#     MOK_CERT  (the certificate, base64)
# Keep omnilaunch-mok.priv somewhere safe and private. Delete it from this folder once it's saved.
# After the next build, on the installed system run:  omnilaunch-os enroll-key
set -euo pipefail
openssl req -new -x509 -newkey rsa:2048 -nodes -days 36500 -sha256 \
    -subj "/CN=OmniLaunch OS driver signing/" \
    -keyout omnilaunch-mok.priv -outform DER -out omnilaunch-mok.der
chmod 600 omnilaunch-mok.priv
echo
echo "MOK_KEY:";  base64 < omnilaunch-mok.priv | tr -d '\n'; echo; echo
echo "MOK_CERT:"; base64 < omnilaunch-mok.der  | tr -d '\n'; echo
