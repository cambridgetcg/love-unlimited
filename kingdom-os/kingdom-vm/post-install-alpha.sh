#!/bin/sh
# Post-install: download and run Kingdom OS installer
apk add curl
curl -sL https://codeberg.org/zerone-dev/love-unlimited/raw/branch/main/kingdom-os/install.sh -o /tmp/install.sh
chmod +x /tmp/install.sh
/tmp/install.sh --agent alpha --wall 2
