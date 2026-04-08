#!/bin/sh
# Post-install: download and run Kingdom OS installer
apk add curl
curl -sL https://raw.githubusercontent.com/cambridgetcg/Claude-unlimited/main/kingdom-os/install.sh -o /tmp/install.sh
chmod +x /tmp/install.sh
/tmp/install.sh --agent alpha --wall 2
