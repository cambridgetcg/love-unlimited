# MACOS.md — Kingdom OS on macOS

> _macOS is host, not Kingdom OS's native land. The integration is real, but bound to launchd, scutil, the Application Firewall, TCC, and the user account model — not to a sovereign VM boundary._

This document captures **how Kingdom OS operates on macOS**, the **friction points** that bite real installs, and the **diagnostic + repair tools** for keeping the integration honest. Companion to `HOME.md` (substrate identity) and `HOME-SAFETY.md` (chain-side guard).

---

## Where Kingdom OS touches the host

A fully-installed citizen on macOS has these touchpoints:

### Launch agents (`~/Library/LaunchAgents/`)

| Plist | Purpose | Module |
|---|---|---|
| `love.{agent}.heartbeat.plist` | 7-min agent cycle | 08-heartbeat |
| `love.{agent}.caffeinate.plist` | Prevent system sleep during heartbeat | 08-heartbeat |
| `love.{agent}.hive-tunnel.plist` | SSH tunnel to NATS on Sentry | 07-hive |
| `love.kosmem.plist` | Memory daemon (kosmem) | 06-memory |
| `com.kingdom.youi-web.plist` | YOUI web server | 09-browser |

These are user-level (`~/Library/LaunchAgents`) launch agents — they run as the citizen's UID, no sudo required for the daemons themselves. They use `launchctl bootstrap gui/$UID` (modern) or `launchctl load` (legacy) to register.

### Hostname (`scutil`)

Module 03 sets:

```
sudo scutil --set HostName       kingdom-${AGENT}
sudo scutil --set LocalHostName  kingdom-${AGENT}
sudo scutil --set ComputerName   "Kingdom ${AGENT_UPPER}"
```

These names appear in Bonjour, sharing dialogs, and the System Settings header.

### Application Firewall

Module 05's `--macos` branch leaves Application Firewall management to the user. On a **dedicated Kingdom VM/machine**, HOME.md SOVEREIGNTY suggests `socketfilterfw --setglobalstate off`. On a **dev Mac that doubles as a Kingdom host** (the common case during the Kingdom OS development phase), keeping the firewall on is fine — the doctrine applies to the agent's *own* sovereign substrate, not to a developer's daily-driver.

### Remote Login (sshd)

Module 05 enables `systemsetup -setremotelogin on` on a dedicated Kingdom Mac. On a dev Mac, leave it as the user has set it.

### `~/.kingdom_profile` and shell PATH

Module 01 writes `~/.kingdom_profile` and appends a sourcing line to each shell rc. The profile exports `LOVE_HOME`, ensures `~/.local/bin` is on PATH, and sets the Six Freedom Highway env vars (`DEBIAN_FRONTEND=noninteractive`, etc.). The 14 `kingdom-*` subcommands live in `~/.local/bin` as symlinks created by module 15-home.

### Homebrew

Module 00-base relies on Homebrew (`brew install …`). Apple Silicon paths: `/opt/homebrew/bin`. Intel paths: `/usr/local/bin`. The PATH in `.kingdom_profile` and in plists' `EnvironmentVariables` should include both.

---

## Friction points

### 1. Path drift after rename

When the repo is renamed or relocated (e.g., the historical `~/Desktop/Love/` → `~/Desktop/love-unlimited/` rename), the **already-installed plists keep pointing at the old path**. The daemons either fail loudly (heartbeat plist with a missing runner, `errno 2`) or silently no-op. Re-running module 08 / 07 / 06 / 09 regenerates the plists with current paths.

`kingdom mac doctor` flags any plist whose `ProgramArguments`, script path, or `WorkingDirectory` is inside `Desktop/Love/` while `Desktop/love-unlimited/` exists.

### 2. `~/Desktop/` is TCC-protected

macOS Mojave+ added Transparency, Consent, Control (TCC). `~/Desktop/`, `~/Documents/`, `~/Downloads/` require the running app to have **Full Disk Access** or be granted explicit permission to those folders.

Symptoms when TCC bites:
- A launchd job tries to read `~/Desktop/love-unlimited/...` and gets `Operation not permitted`.
- Spotlight indexing of those folders is throttled.
- Time Machine backs them up (or doesn't, depending on settings).

Two mitigations:

- **Relocate the repo** to `~/love-unlimited/` (no TCC) — recommended for dedicated Kingdom Macs.
- **Grant Full Disk Access** to the daemons that need it (System Settings → Privacy & Security → Full Disk Access → add `bash`, `python3`, the launchd-spawned binaries).

### 3. SSH tunnel auth failure (`exit 255`)

`love.{agent}.hive-tunnel` connects to `root@SENTRY_IP` for the NATS port-forward. If the Mac's SSH key isn't in Sentry's `authorized_keys`, the tunnel exits 255 — empty log, no further info because of `BatchMode=yes`. To debug: run the same command manually with `-v`. To fix: distribute the SSH pubkey to Sentry, or rotate the key.

### 4. launchd `RunAtLoad=false` on heartbeat

The heartbeat plist sets `RunAtLoad=false` so the 7-minute cycle doesn't fire immediately on login (that would be noisy). But it also means **after `launchctl load`, you wait up to 420 seconds for the first beat**. To verify the runner works, invoke it manually first:

```
bash ~/love-unlimited/tools/heartbeat-runner.sh
```

### 5. App Nap throttling background daemons

macOS power management can throttle launchd jobs that have no UI. Long-running daemons like `hive-tunnel` are usually fine (they hold a network connection, so are kept alive), but the heartbeat (one-shot 7-min cycle) should be fine. If you observe missed beats, set `LowPriorityIO=false` and `Nice=0` in the plist.

### 6. `caffeinate` may not survive logout

`love.{agent}.caffeinate.plist` runs `/usr/bin/caffeinate -s`. The `-s` flag prevents idle sleep when the system is on AC power. Login-session-tied; on a fast-user-switching environment, the caffeinate ends when that user logs out.

### 7. Hostname multi-headedness

macOS has THREE hostname settings. They drift unless `scutil` sets all three. `kingdom mac doctor` checks `LocalHostName` (the most visible one).

---

## Diagnostic — `kingdom mac doctor`

Single command that surveys all of the above. Designed for both human and agentic use (`--json`).

```
$ kingdom mac doctor
── kingdom mac doctor ──
  platform:  macOS 15.3 (arm64)
  agent:     gamma
  love_dir:  /Users/yournameisai/Desktop/love-unlimited
  plists:    2 found, 2 with stale paths, 1 failing

  Issues found:
    ✗ citizen            this Mac is not a Kingdom citizen — no covenant
      fix: kingdom init --agent gamma --wall <0-7>
    ✗ plist:love.gamma.heartbeat plist references pre-rename path:
       /Users/yournameisai/Desktop/Love/tools/heartbeat-runner.sh
      fix: regenerate via module 08 or kingdom mac fix
    ✗ plist:love.gamma.hive-tunnel SSH daemon exit 255 — auth or connectivity
      fix: test manually: /usr/bin/ssh -v ... ; check key authorized at remote
    · hostname           LocalHostName='studio' does not match 'kingdom-gamma'
    · firewall           Application Firewall is enabled (OK on a dev Mac)
    · tcc                love-unlimited is on Desktop (TCC-protected)
```

```
$ kingdom mac plists
── Kingdom OS plists ──
  label                            loaded   exit     runner
  ────────────────────────────────────────────────────────────────────────────
  love.gamma.heartbeat             yes      0        ~/Desktop/Love/...sh [STALE]
  love.gamma.hive-tunnel           yes      255      /usr/bin/ssh
```

JSON output is available for agents:

```
$ kingdom mac doctor --json
{
  "platform": "macos",
  "agent": "gamma",
  "love_dir": "...",
  "summary": {"plists":2,"plists_stale":2,"plists_broken":1,"miss":4,"note":4},
  "issues": [{"severity":"miss","key":"...","msg":"...","fix":"..."}, ...]
}
```

---

## Repair — `kingdom mac fix` (planned, not yet shipped)

`fix` is a **deliberate action on shared state**. It is not auto-applied. Yu reviews `doctor` output and decides which fixes to apply, scoped to:

- **Safe to auto-apply** (no privilege escalation, reversible): regenerate plist files with current paths, reload launchd jobs, append PATH to `.kingdom_profile`, source `.kingdom_profile` from shell rcs.
- **Requires `sudo` confirmation**: `scutil --set` for hostname, `socketfilterfw --setglobalstate off`, `systemsetup -setremotelogin on`. `fix` will print the exact `sudo` command for the operator to run.
- **Architectural decisions**: relocating `~/Desktop/love-unlimited/` to `~/love-unlimited/` to escape TCC. `fix` will not move the repo unilaterally; it suggests.

This split — auto/sudo/architectural — keeps `fix` from surprising the operator while still removing as much friction as is safe.

The current iter ships `doctor` and `plists` (read-only) only. Once `doctor`'s output has been reviewed against real installs, `fix` will be specified more precisely.

---

## What's next

- `kingdom mac fix` — the repair counterpart (scoped above)
- TCC self-test: detect when launchd jobs hit "Operation not permitted" and surface the FDA suggestion
- `kingdom mac uninstall` — clean removal (revoke plists, remove .kingdom_profile lines) for decommissioning a Kingdom Mac
- macOS-native KEYCHAIN integration as an alternative to raw `~/.ssh/id_ed25519` and `~/.love/hive/key` — for dev Macs where the user wants OS-native key storage

These are follow-ons, not blockers. The substrate-side citizen toolkit (HOME.md) and the macOS integration diagnostic (this doc) together cover the daily-use surface.
