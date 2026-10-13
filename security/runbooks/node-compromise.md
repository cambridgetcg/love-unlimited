# Node Compromise Runbook

> **Retired 2026-10-13.** This runbook covered a compromised node in the Hetzner
> VPS fleet (forge, lark, sentry, patch, sage): detection over root SSH,
> firewalling, evidence capture, reimage through the Hetzner console and
> redeploy with `fleet.py` / `fleet-agent-deploy.sh`. The fleet was
> decommissioned and its IPs recycled, so there are no nodes to isolate or
> reimage, and those tools were retired. For a compromised local device or
> credential, use `credential-leak.md`, `integrity-violation.md` and
> `emergency-halt.md`. The old procedure is in git history before this commit.
