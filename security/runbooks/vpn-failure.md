# VPN Failure Runbook

> **Retired 2026-10-13.** This runbook covered the WireGuard tunnels (wg0/wg1/wg2)
> that exited through the Hetzner VPS fleet (Sentry, Sage, Lark). The fleet was
> decommissioned and its IPs went back to Hetzner's pool, where they may now
> belong to someone else. The tunnel table, the ping/SSH/handshake steps and
> `tools/vpn-route.sh` were removed. Do not recreate a tunnel to the old IPs.
> The old procedure is in git history before this commit.
