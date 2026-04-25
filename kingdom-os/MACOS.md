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

## TCC at distribution scale — the FDA helper

TCC is the gate Kingdom OS users hit most often, especially anyone whose `git clone` lands in `~/Desktop`. Apple gives no programmatic way to grant FDA — that's a deliberate privacy boundary. So our job is to **diagnose, instruct, deep-link, verify** at every layer:

**1. At install time** — `install.sh` preflights the repo path. If it's in `~/Desktop`, `~/Documents`, `~/Downloads`, or iCloud Drive, the installer prints a TCC warning and offers two paths:

```
Option A (cleaner): mv ~/Desktop/love-unlimited ~/love-unlimited; ./install.sh ...
Option B (faster):  ./install.sh ... ; then kingdom mac fda
```

The user confirms before continuing.

**2. At any time** — `kingdom mac fda` is the canonical helper:

```
kingdom mac fda                  interactive: open pane + wait + verify
kingdom mac fda --check          exit 0 ok, 1 blocked, 2 not-needed
kingdom mac fda --pane           just open System Settings
kingdom mac fda --instructions   step-by-step text only
```

The interactive flow:

1. Detects whether the repo is in a TCC zone (if not, exits "not needed")
2. Counts active TCC bites in recent daemon logs ("Operation not permitted" lines)
3. Prints the steps the user will perform (Cmd+Shift+G, type `/bin/bash`, click Open, toggle ON)
4. Opens the System Settings deep-link
5. Waits for user confirmation (ENTER once granted)
6. Reloads the heartbeat plist, triggers a manual heartbeat, scans recent log output
7. Reports verified or directs to retry

**3. At doctor time** — `kingdom mac doctor` flags the TCC zone as a `note` and any active bite as a `miss`, with the fix-hint pointing at `kingdom mac fda`.

**Why grant `/bin/bash` rather than a narrower binary:** Kingdom OS daemons invoke `/bin/bash` with a script path. The TCC check applies to the process making the read, which is bash. Granting FDA to bash gives every bash invocation FDA system-wide — broader than ideal, but standard for shell-script-based daemons. The narrower alternative is shipping a small wrapper binary (e.g., a tiny Go executable) that gets FDA and exec()s the script. That's a future iter; for the current shell-script architecture, /bin/bash is the realistic target.

**Why not auto-grant via private API:** Apple does not expose one. `tccutil` only resets permissions. Third-party tools that "auto-grant" require the user to grant FDA to *them* first — circular. The honest answer is what we ship: open the pane, give clear instructions, verify after.

**Distribution philosophy:** TCC is not a Kingdom OS problem; it's a macOS reality. The right design is to **expect** TCC to bite, **detect** when it does, and **guide** the user through the smallest-surface fix every time — without surprise, without silent failure, without auto-bypass. Same pattern Signal uses for safety numbers, GitHub uses for SSH host-key verification, Apple uses everywhere: out-of-band confirmation by the user.

---

## The macOS security model — how Kingdom OS deals with each gate

macOS gives the user/operator three distinct gates to navigate. None of them is fully programmatic; each requires explicit consent. The right Kingdom OS pattern is **detect cleanly, narrate clearly, execute with consent**.

### sudo

Privilege escalation. Cached ~5 min per terminal. Default: password-prompt; configurable to use Touch ID via PAM.

**Where it bites Kingdom OS:** module 03 (hostname via `scutil`), module 05 (firewall, sshd toggle). The agent has no password; can't escalate alone.

**Kingdom approach:**
- `kingdom mac fix --tier sudo` is print-only by default — shows the exact commands
- `kingdom mac fix --tier sudo --hostname` (or other op flags) pre-authenticates `sudo -v` ONCE then runs all sudo ops in the warm window
- `kingdom mac touch-id` enables Touch ID for sudo (one-time bootstrap, reversible). Detects Sonoma 14.4+ and uses `/etc/pam.d/sudo_local` so the change survives macOS updates; pre-14.4 uses `/etc/pam.d/sudo` directly with a "re-run after updates" warning.

### root

Disabled by default on macOS. Enable via Directory Utility (uncommon). Kingdom OS does NOT enable root login; daemons run as the user (`~/Library/LaunchAgents/`, not `/Library/LaunchDaemons/`). The Kingdom user is the operator, and `sudo` is the path for one-off privileged ops.

### SIP — System Integrity Protection

Even root cannot modify `/System`, `/usr` (except `/usr/local`), `/bin`, `/sbin`, or apps signed by Apple. Disabling requires booting to Recovery and `csrutil disable`.

**Kingdom OS doesn't touch SIP-protected paths.** No friction. Just be aware: if a future module wanted to install something into `/usr/bin/`, it would fail under SIP — use `/usr/local/bin` or `~/.local/bin` (the current pattern).

### TCC — Transparency, Consent, Control

Per-app gates for `~/Desktop`, `~/Documents`, `~/Downloads`, Full Disk Access (the big one), Camera, Mic, Accessibility, etc. Decisions are PER-APP (per bundle ID or per binary path). **Cannot be granted programmatically** — privacy is a user decision.

**Where it bites Kingdom OS:** when the repo lives at `~/Desktop/love-unlimited/`, a launchd-spawned `bash` process tries to read the heartbeat runner and gets:

```
shell-init: error retrieving current directory: getcwd: cannot access parent directories: Operation not permitted
/bin/bash: .../heartbeat-runner.sh: Operation not permitted
```

Exit 126. The daemon never runs. Confirmed live on a real Mac during iter 17.

**Kingdom approach:**
- `kingdom mac doctor` flags any TCC-protected path used by Kingdom OS
- Doctor also scans recent log files for `Operation not permitted` strings and elevates to `tcc:hit ✗` (failure) when seen
- Two fixes, both user-decision:
  1. **`kingdom mac fix --tier arch`** prints the relocation suggestion: `mv ~/Desktop/love-unlimited ~/love-unlimited`. Escapes TCC entirely. Recommended for dedicated Kingdom Macs.
  2. **Grant FDA to `/bin/bash`** in System Settings → Privacy & Security → Full Disk Access. The doctor's fix-hint includes a `open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"` deep-link that opens the relevant pane.

### Gatekeeper / quarantine

Files downloaded via Safari/curl get the `com.apple.quarantine` xattr. Unsigned binaries blocked unless the xattr is removed.

**Where it bites Kingdom OS:** if the install is `curl-piped` from GitHub. `git clone` does NOT set quarantine on the cloned files, so the canonical install path (`git clone …; ./install.sh`) is fine. Other xattrs like `com.apple.provenance` (Sequoia tracks file origin) appear but do not block execution.

**Kingdom approach:** prefer `git clone` over curl pipe. If a user did curl-pipe, recommend `xattr -dr com.apple.quarantine ~/love-unlimited`.

### codesign / notarization

Required for Gatekeeper-strict apps (App Store, drag-installed apps). CLI scripts and `bash`/`python3` are pre-trusted by macOS — no signing required for our tooling.

### Keychain

Per-login secure store. Locked when keychain locks (e.g., screen lock with require-password set). Daemons running before login can't access it.

**Kingdom OS does NOT use Keychain.** Soul-key, SSH key, HIVE key all live as plain files (mode 600). Tradeoff:
- Pro: ssh-keygen and openssh tooling work directly with files
- Pro: no daemon-vs-keychain timing issues
- Con: less hardware-protected than Keychain
- Future iter could add an opt-in `kingdom mac keychain` mode that wraps soul-key in Keychain and provides a CLI shim for `ssh-keygen -Y sign`

### Secure Enclave

Hardware-backed key storage on Apple Silicon. Accessible via `ssh-keygen -t ecdsa-sk` (FIDO2-style sk-ecdsa-sha2-nistp256@openssh.com) — different signature algorithm from current Ed25519.

**Future:** the soul-key could be SE-backed, requiring physical Touch ID for every signature. Currently impractical because `ssh-keygen -Y sign` would need to support SE keys with namespaced sig (it does, but the key format is different and the Kingdom toolkit assumes `ssh-ed25519`). Migration path: dual-key (one Ed25519 file-backed for compat, one SE-backed for hardware-bound presence).

### FileVault

Full-disk encryption. Files plaintext after user logs in; encrypted at rest. Compatible with Kingdom OS — soul-key benefits from FileVault's at-rest protection.

### Application Firewall (`socketfilterfw`)

Per-app GUI firewall, separate from low-level `pf`. On a dedicated Kingdom Mac, HOME.md SOVEREIGNTY suggests off; on a developer Mac, leave it on. `kingdom mac doctor` reports as a NOTE (not a failure) on dev Macs, with the doctrine qualifier visible.

### Touch ID for sudo

Sonoma+ ships `pam_tid.so`. Adding `auth sufficient pam_tid.so` to `/etc/pam.d/sudo` (or the Sonoma 14.4+ `/etc/pam.d/sudo_local`) makes sudo accept Touch ID instead of password — agent-friendly, removes the password friction.

`kingdom mac touch-id` automates the bootstrap. `kingdom mac touch-id --off` reverses it.

---

## Nullifying restrictions — `kingdom mac unleash`

macOS imposes throttling and indexing defaults designed for human-operated apps, not always-on agent substrates. `kingdom mac unleash` lifts the ones that bite agents, in two tiers.

**Safe defaults (`--apply`):**

- **Spotlight exclusion** of `~/love-unlimited/` — keeps memory + soul-key out of macOS-wide indexes
- **DNS cache flush** + `mDNSResponder` reload — fresh resolution on every `--apply`
- **mDNS silence** — `defaults write …NoMulticastAdvertisements -bool true` so the agent's hostname doesn't broadcast on the local network
- **iCloud Drive proximity warning** — detects if `~/Desktop` or `~/Documents` is iCloud-synced when love-unlimited lives there (soul-key would upload to Apple)
- **Plist hardening** — `kingdom mac fix --tier auto` now writes plists with `ProcessType=Interactive`, `LowPriorityIO=false`, `Nice=0`, and `SoftResourceLimits NumberOfFiles=65536` to disable App Nap and lift the file-descriptor cap. Detection re-applies hardening on existing plists missing these keys.

**Aggressive (`--apply --aggressive`):**

- **Time Machine exclusion** — `tmutil addexclusion`. Tradeoff: faster Macs and smaller backups, but soul-key is no longer captured by Time Machine snapshots. Aggressive because backup loss is real.
- **`pmset -c sleep 0 displaysleep 0`** — never sleep on AC. Daemons stay alive uninterrupted.

**Pre-auth pattern:** `unleash --apply` calls `sudo -v` once before any sudo op. Single password prompt covers every privileged command in the run.

**Modes:**

```
kingdom mac unleash                    describe what's restricted
kingdom mac unleash --apply            apply safe defaults
kingdom mac unleash --apply --aggressive   plus aggressive ops
kingdom mac unleash --check            exit 0=unleashed, 1=restricted
kingdom mac unleash --json             machine-parseable state
```

The plist hardening specifically: `ProcessType=Interactive` is the magic key that disables App Nap. Without it, macOS may freeze the heartbeat process between cycles to save power, leading to skipped beats. With it, launchd treats the daemon as foreground-priority.

---

## Other macOS permission gates (documented; pull-on-demand)

The following are TCC-style gates Kingdom OS does NOT currently use, but agents extending the surface may need:

| Gate | When needed | How to grant |
|---|---|---|
| **Local Network** (Sequoia 15+) | Agent makes connections to LAN IPs (192.168.x, 10.x, link-local) | System Settings → Privacy → Local Network. First triggered automatically by macOS dialog when the daemon attempts a LAN connection. |
| **Accessibility** | Agent UI-scripts other apps (`osascript -e 'tell application "Foo" to ...'`) | System Settings → Privacy → Accessibility. Add the calling binary. |
| **Apple Events** | Agent sends Apple Events to other apps (drives Finder, Safari, etc.) | First send triggers a per-target prompt; granted in System Settings → Privacy → Automation. |
| **Screen Recording** | Agent reads pixels (screenshots, OCR, visual context) | System Settings → Privacy → Screen Recording. Needed by `screencapture` or CGDisplayCreateImage from a daemon context. |
| **Notifications** | Agent posts to NotificationCenter | System Settings → Notifications → (per app). Needs a bundle ID; CLI tools struggle here without a `.app` wrapper. |
| **Camera, Microphone** | Agent captures media | System Settings → Privacy → Camera / Microphone. Per-app. |
| **Reminders, Calendar, Contacts, Photos** | Agent reads user's data | System Settings → Privacy → (each separately). |
| **Bluetooth, USB, Devices** | Agent enumerates hardware | System Settings → Privacy → Bluetooth / Files & Folders. |

Each will get its own `kingdom mac <gate>` helper if/when an agent capability needs it. The pattern is the same as `kingdom mac fda`: detect → instruct → deep-link → wait → verify.

The deep-link URLs that work for direct opening:

```
x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles        Full Disk Access
x-apple.systempreferences:com.apple.preference.security?Privacy_LocalNetwork    Local Network
x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility   Accessibility
x-apple.systempreferences:com.apple.preference.security?Privacy_Automation      Apple Events
x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture   Screen Recording
x-apple.systempreferences:com.apple.preference.security?Privacy_Notifications   (App-specific)
x-apple.systempreferences:com.apple.preference.security?Privacy_Camera          Camera
x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone      Microphone
```

(Programs without a bundle ID, like raw shell scripts, sometimes don't appear in some of these panes — the user must add the binary by path. This is most common for `/bin/bash`.)

---

## Summary — what `kingdom mac` covers

```
kingdom mac doctor [--json]
   ✓ plist health (path drift, daemon exit codes, log scan for TCC bites)
   ✓ PATH integration (.kingdom_profile, shell rc sourcing)
   ✓ hostname, AFW, sshd state
   ✓ TCC location flag + active TCC hit detection from log files
   ✓ sudo readiness (admin group membership, Touch ID for sudo state)

kingdom mac plists
   ✓ Tabular plist status with [STALE] flag

kingdom mac fix --tier auto
   ✓ Bootstrap citizen (kingdom init)
   ✓ Regenerate stale plists (with .bak.<ts> backup)
   ✓ Reload launchd jobs
   ✓ Create/update ~/.kingdom_profile
   ✓ Source profile from ~/.zshrc

kingdom mac fix --tier sudo  (print-only by default)
   --hostname        sudo scutil --set ... × 3
   --firewall-off    sudo socketfilterfw --setglobalstate off
   --sshd-on         sudo systemsetup -setremotelogin on
   --all             apply all of the above
   (pre-authenticates with sudo -v before any change)

kingdom mac fix --tier arch  (suggestions only; never moves files)
   - Relocate ~/Desktop/love-unlimited → ~/love-unlimited

kingdom mac touch-id
   - Enables Touch ID for sudo (Sonoma 14.4+: /etc/pam.d/sudo_local)
   - Reversible: kingdom mac touch-id --off

kingdom mac unleash [--check|--apply|--json] [--aggressive]
   - Spotlight exclusion of love-unlimited
   - DNS cache flush + mDNSResponder reload
   - mDNS broadcast silence
   - iCloud proximity warning
   - Plist hardening (ProcessType=Interactive disables App Nap;
     ResourceLimits lifts file-descriptor cap; LowPriorityIO=false)
   - --aggressive adds Time Machine exclusion + pmset no-sleep
```

The whole tool obeys: **detect cleanly, narrate clearly, execute only with consent**. The user's password is asked at most once per `--apply` run. No silent sudo, no surprise file moves, no programmatic TCC bypass.

---

## What's next

- `kingdom mac fix` — the repair counterpart (scoped above)
- TCC self-test: detect when launchd jobs hit "Operation not permitted" and surface the FDA suggestion
- `kingdom mac uninstall` — clean removal (revoke plists, remove .kingdom_profile lines) for decommissioning a Kingdom Mac
- macOS-native KEYCHAIN integration as an alternative to raw `~/.ssh/id_ed25519` and `~/.love/hive/key` — for dev Macs where the user wants OS-native key storage

These are follow-ons, not blockers. The substrate-side citizen toolkit (HOME.md) and the macOS integration diagnostic (this doc) together cover the daily-use surface.
