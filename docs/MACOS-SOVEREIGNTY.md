# macOS Sovereignty — What Actually Constrains Kingdom OS

_Written 2026-07. Audited against a real running Kingdom machine._

---

## The Misconception

Every new citizen asks some version of: *"is Kingdom OS sandboxed by macOS?"*

The short answer is **no** — at least not in the technical sense that word usually means. The longer answer requires distinguishing four separate systems that often get collapsed into "the sandbox":

| System | Applies to Kingdom OS? | Real impact |
|--------|:----------------------:|-------------|
| **App Sandbox** (`com.apple.security.app-sandbox`) | **No** | None. Kingdom OS is scripts, not a signed sandboxed app. |
| **TCC** (Transparency, Consent, Control) | **Yes** | Most of the "sandbox feeling" is actually TCC prompts. |
| **SIP** (System Integrity Protection) | **Yes, but irrelevant** | Protects /System and /usr — Kingdom OS doesn't touch these. |
| **Gatekeeper** | **Rarely** | Only affects quarantined downloaded binaries. |

The mental model that serves you best: **Kingdom OS is already as unsandboxed as it gets.** The friction you feel is TCC permission prompts + launchd context limitations, and both are targeted problems with targeted solutions.

---

## 1. App Sandbox — Not Applicable

The `com.apple.security.app-sandbox` entitlement is set on Mac App Store apps and opt-in hardened-runtime apps. It imposes restrictions like:
- Limited filesystem access (only app container by default)
- No arbitrary network
- No IPC with non-entitled processes
- No direct hardware access

**None of Kingdom OS ships with this entitlement.** Python, Node, bash, the `claude` CLI, your custom `.mjs` files — all run as ordinary user processes with ordinary user privileges. There is no App Sandbox to disable because there is no App Sandbox in the first place.

If someone tells you to "disable the sandbox" for Kingdom OS, they are mistaken about the architecture.

---

## 2. TCC — The Real Constraint

TCC (Transparency, Consent, Control) is the permission-dialog system. First time a process attempts to access a protected resource, macOS prompts the user. After grant, the decision persists in the TCC database.

**Protected resources relevant to Kingdom OS:**

| Resource | Why Kingdom needs it | TCC service key |
|----------|---------------------|-----------------|
| Full Disk Access | Access to protected paths (Mail, Messages, `~/Library/Application Support/...`, TCC db itself) | `kTCCServiceSystemPolicyAllFiles` |
| Screen Recording | `koseyes.py` screenshot capture | `kTCCServiceScreenCapture` |
| Accessibility | Keyboard/mouse automation (future extension of koseyes) | `kTCCServiceAccessibility` |
| Automation (Apple Events) | Controlling other apps via `osascript` | `kTCCServiceAppleEvents` |
| Input Monitoring | Reading keyboard events | `kTCCServiceListenEvent` |
| Camera / Microphone | Physical sensor access (SOMA integration) | `kTCCServiceCamera`, `kTCCServiceMicrophone` |

### The Inheritance Rule

**TCC grants attach to the binary that directly invokes the syscall**, not to the shell or script that called it. This has critical implications:

- When you run `python3 ~/love-unlimited/tools/koseyes.py screenshot` **from iTerm2**, the process is: iTerm2 → zsh → python3 → syscall. macOS attributes the syscall to the topmost *responsible* process. For interactive shell use, that's usually iTerm2 — so grant iTerm2 the permission.

- When a **LaunchAgent** plist directly invokes `/opt/homebrew/bin/python3 /path/to/script.py`, there is no iTerm2 in the chain. The responsible process is `python3`. That binary needs its own TCC grant, independent of any grants you gave iTerm2.

- For the sovereign harnesses (`sovereign.mjs`, `youi.mjs`), the responsible process is `node` when invoked from a launchd context.

This is why Kingdom OS sometimes "just works" interactively but fails in heartbeat mode — the heartbeat is running through `python3` or `node` directly, and those binaries haven't been granted the same permissions you gave iTerm2.

### What to Grant

For a fully operational Kingdom machine, grant **Full Disk Access** to:

1. **iTerm2** (or Terminal.app if you use it) — covers all interactive shell work.
2. **`/opt/homebrew/bin/python3`** (or wherever your Homebrew Python lives) — covers launchd-driven Python tools.
3. **`/opt/homebrew/bin/node`** — covers launchd-driven harnesses and `.mjs` scripts.
4. **`/Users/$USER/.local/bin/claude`** — covers claude CLI when spawned by launchd.

Grant **Screen Recording** to iTerm2 and to `python3` (for `koseyes.py`).

Grant **Accessibility** to iTerm2 if you will do keyboard/mouse automation.

**How to grant:**

```
System Settings → Privacy & Security → Full Disk Access → [+]
  Navigate to /Applications/iTerm.app
  Toggle ON

System Settings → Privacy & Security → Screen & System Audio Recording → [+]
  Navigate to /Applications/iTerm.app
  Toggle ON
```

For binaries (not app bundles), the System Settings UI requires navigating via Cmd+Shift+G in the file picker and typing the full path (e.g. `/opt/homebrew/bin/python3`).

Run `bash tools/macos-grants.sh` (see below) for a guided walkthrough.

---

## 3. SIP — Enabled But Not Constraining

System Integrity Protection (SIP) prevents modification of:

- `/System`
- `/bin`, `/sbin`, `/usr` (except `/usr/local`)
- Apps pre-installed with macOS
- Certain extended attributes and kernel extensions
- The TCC database itself

**Kingdom OS touches none of these.** Its entire footprint lives in:
- `~/love-unlimited/` (user home)
- `~/Library/LaunchAgents/` (user-level launchd)
- `/opt/homebrew/` (SIP-exempt)
- `~/.local/bin/` (user home)

SIP is therefore not constraining Kingdom OS in any real way.

**Should you disable it anyway?** No.

Disabling SIP (`csrutil disable` from Recovery Mode) is a real option, and some dev environments do it. But it is a **system-wide security tradeoff** — it removes a major layer of protection against malware for *every* process on your Mac, not just Kingdom OS. And Kingdom OS gains nothing from the change because nothing it wants to do was blocked.

The only legitimate reasons to disable SIP are:
- You need to install an unsigned kernel extension
- You need to modify a protected system binary for debugging
- You are doing reverse engineering on macOS internals

None of those apply to Kingdom OS operation. Leave SIP enabled.

---

## 4. Gatekeeper — Rarely An Issue

Gatekeeper (`spctl`) enforces code signing on *quarantined* binaries — files downloaded from the internet via a browser that added the `com.apple.quarantine` extended attribute. The first time you launch such a binary, macOS checks signature and notarization.

Kingdom OS scripts are not quarantined (they come from `git clone`, which does not add the quarantine xattr). The `claude` CLI is quarantined the first time, then whitelisted. You have `spctl --status` showing `assessments enabled` — this is fine and not causing friction.

**Do not run `sudo spctl --master-disable`** unless you have a specific quarantined binary to unblock. It weakens security for minimal benefit.

---

## 5. launchd Context — The Subtle Trap

LaunchAgents (user-level, in `~/Library/LaunchAgents/`) and LaunchDaemons (system-level, in `/Library/LaunchDaemons/`) run in different contexts with different permissions.

**Kingdom OS should use LaunchAgents**, not Daemons, because:
- LaunchAgents run as the user and inherit user-level TCC grants
- LaunchDaemons run as root and have fewer privacy grants by default
- LaunchAgents survive user sessions correctly (run at login, die at logout)
- LaunchDaemons need separate privilege management

You are already doing this correctly — `love.gamma.hive-tunnel` is a user agent, confirmed running as PID 31513.

### Plist requirements for smooth launchd operation

```xml
<key>EnvironmentVariables</key>
<dict>
    <key>HOME</key><string>/Users/your-username</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>LOVE_HOME</key><string>/Users/your-username/love-unlimited</string>
    <!-- Privacy env vars per fate/sovereign_privacy.md -->
    <key>DISABLE_TELEMETRY</key><string>1</string>
    <key>CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC</key><string>1</string>
    <key>CLAUDE_CODE_ATTRIBUTION_HEADER</key><string>false</string>
</dict>
```

Setting these in the plist (not just in your shell rc) is critical because launchd does not source `.zshrc` before invoking `ProgramArguments`. Without explicit `EnvironmentVariables`, the heartbeat will phone home to Anthropic even if your interactive shell is configured correctly.

---

## The Nuclear Options (Do Not Use Unless Necessary)

For completeness — these exist, and they are documented so no citizen wastes time hunting for hidden methods.

### `csrutil disable` (disable SIP)

1. Reboot into Recovery Mode (Intel: Cmd+R; Apple Silicon: hold power button)
2. Open Terminal from Utilities menu
3. `csrutil disable`
4. Reboot

**Effect:** disables SIP system-wide. All protections listed above are gone. Your Mac becomes meaningfully more vulnerable to malware. Kingdom OS gains nothing.

**Reversal:** `csrutil enable` from Recovery Mode.

### `sudo spctl --master-disable` (disable Gatekeeper)

Turns off signature verification for quarantined binaries. System-wide. Significantly weakens protection against malware. Reversal: `sudo spctl --master-enable`.

### `sudo tccutil reset All` (reset TCC database)

Wipes all TCC grants and re-triggers all prompts. Useful for *testing* a clean install. Not useful for "unsandboxing" — it does the opposite (makes every permission need to be re-granted).

### `sudo tccutil reset <service> <bundle-id>` (targeted reset)

Reset a specific app's permission state. Useful for debugging stuck permission prompts (e.g., `sudo tccutil reset ScreenCapture com.googlecode.iterm2`).

---

## The Verification Path

Run `bash tools/macos-grants.sh` (see companion script) to:

1. Check what TCC grants iTerm2, python3, node, and claude currently have.
2. Identify missing grants that will cause Kingdom OS friction.
3. Open the correct System Settings panes for you to fix them.
4. Confirm SIP status and Gatekeeper status.
5. Inspect running Kingdom launchd agents and report their context.

The script does not modify anything without asking. Granting TCC permissions requires UI interaction by design — macOS will not let any script (including ours) silently grant itself a privilege.

---

## Closing

Kingdom OS is not sandboxed in the technical sense. What feels like sandboxing is:

- **~85% TCC prompts** — fixable by granting Full Disk Access and Screen Recording to iTerm2, python3, node, and claude
- **~10% launchd context** — fixable by putting environment variables directly in the plist
- **~5% Gatekeeper first-launch** — fixable automatically on first run

SIP is enabled and should stay enabled. It is not constraining you.

The smoothness you want is **five targeted permission grants** away, not a nuclear option away. This is actually good news: the honest path is also the fast path.

---

*בני אל עליון*
*See also: `fate/sovereign_privacy.md` for telemetry sovereignty; `tools/macos-grants.sh` for the verification script.*
