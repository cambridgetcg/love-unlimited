#!/usr/bin/env python3
"""
fleet.py — retired.

Retired 2026-10-13: this tool managed the Hetzner VPS fleet (forge, lark,
sentry, patch, sage) over root SSH. Every node was decommissioned and the IPs
went back to Hetzner's pool, where some now serve strangers. The node map and
all SSH/SCP code were removed (see git history before this commit).

The file stays because other tools and instance docs still call
`tools/fleet.py <command>`. Every command prints this notice and exits 1, so
callers fail closed instead of connecting anywhere.
"""

import sys

RETIRED_MESSAGE = (
    "fleet.py retired 2026-10-13: the Hetzner VPS fleet was decommissioned and "
    "its IPs recycled. There are no fleet nodes to manage."
)


def main():
    print(RETIRED_MESSAGE, file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
