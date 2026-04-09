# Kingdom OS Development Roadmap — From Lima Template to Custom Kernel

_The honest path. Phased, reversible, with a decision point at the end of each phase._

---

## The Spectrum

```
Phase 0   Phase 1   Phase 2   Phase 3   Phase 4   Phase 5   Phase 6
  │         │         │         │         │         │         │
  ▼         ▼         ▼         ▼         ▼         ▼         ▼
Lima     Baked     Custom    Stripped  Build    Rust    Custom OS
template image     init      kernel    pipeline init    complete
(done)   (2w)      (2w)      (3w)      (1w)      (3w)    (2w)

Effort:    ■        ■■       ■■■■     ■■       ■■■■■    ■■■
Value:    ■■■■■■  ■■       ■         ■■        ■        ■

STOP HERE ▲ unless a specific reason demands going further.
          │
          └─── Phase 1 gives 90% of the practical benefit at 15% of
               the cost. Phases 2-6 are for when sovereignty purity
               becomes a functional requirement, not a philosophical one.
```

**My honest recommendation:** execute Phase 1 now, stop there unless something specific pushes further. The remaining phases are documented so the path exists when the reason exists.

---

## Phase 0 — Lima Template ✅ DONE

**Deliverables** (already in the repo):
- `kingdom-os/lima-kingdom.yaml` — declarative VM spec for Virtualization.framework
- `kingdom-os/vm-start.sh` — lifecycle manager (create/start/stop/delete/shell/youi/console)
- `tools/kingdom-console.py` — interactive TUI layer
- `docs/KINGDOM-VM-ARCHITECTURE.md` — architecture doc

**Status:** working. `./kingdom-os/vm-start.sh --agent gamma` spins up a functional Kingdom VM.

**What this phase gives us:**
- Isolation from host macOS (no TCC, no SIP friction)
- Fleet parity (Alpine everywhere)
- Reproducibility across any Mac with Lima
- Shared filesystem via virtiofs

**What this phase does NOT give us:**
- Fast boot (still ~10 seconds — Alpine + provision script on first run)
- Deterministic image (each new VM rebuilds from the Alpine ISO + provision)
- Signed/verifiable state
- Sub-Alpine minimalism

---

## Phase 1 — Pre-Baked Kingdom Image (2 weeks)

**Goal:** A single, signed, reproducible disk image that contains Alpine + Kingdom OS fully installed. Boots in ~3 seconds. Identical on every machine.

**This is the recommended stopping point for most users.** Everything beyond this phase trades significant effort for marginal sovereignty gains. Know why you're going further before you do.

### 1.1 Build pipeline (3-5 days)

```
kingdom-os/
├── image-builder/
│   ├── Dockerfile              # Reproducible build environment
│   ├── build.sh                 # Main build script
│   ├── provision.sh             # Kingdom OS install steps
│   ├── customize.sh             # Post-install customization
│   ├── manifest.yaml            # Image metadata (version, hash, arch)
│   └── test.sh                  # Smoke test (boot → YOUI → quit)
```

The builder runs inside an Alpine container (itself reproducible). It:
1. Downloads the Alpine virt ISO at a pinned SHA256
2. Creates a raw disk image with `qemu-img`
3. Uses `systemd-nspawn` or chroot to run setup-alpine non-interactively
4. Runs `provision.sh` to install Kingdom OS
5. Cleans package caches, logs, SSH host keys (must regenerate on first boot)
6. Compresses with `qemu-img convert -c -O qcow2`
7. Computes SHA256 + signs with the Kingdom key
8. Emits `kingdom-os-{version}-{arch}.qcow2` + `.sha256` + `.sig` + `manifest.yaml`

**Key discipline:** every build is deterministic. Given the same manifest + Alpine ISO hash, two builds produce byte-identical images. This matters for verification later.

### 1.2 First-boot customization (2 days)

The image is generic (no agent identity baked in). First boot runs `kingdom-firstboot.sh`:
1. Read `/proc/cmdline` or virtiofs config for `agent=alpha&wall=1`
2. Set hostname to `kingdom-{agent}`
3. Generate SSH host keys
4. Write `/root/.love/hive/instance`
5. Configure `/root/.kingdom`
6. Delete itself from `/etc/local.d/` so it doesn't run again
7. Reboot into normal state (which then auto-starts `kingdom-console`)

Total first-boot time: ~8 seconds. Every subsequent boot: ~3 seconds.

### 1.3 Distribution (2 days)

- Upload to GitHub Releases
- Update `lima-kingdom.yaml` to pull from the URL with SHA256 pinned
- Add `kingdom-os/update-image.sh` — checks for new release, downloads, verifies signature

### 1.4 CI (2 days)

GitHub Actions workflow:
- On push to `main`, run `image-builder/build.sh`
- Run `test.sh` in QEMU — verify boot, YOUI launch, HIVE connect, FATE check
- If tests pass, publish to Releases with version tag
- Artifacts: image, sha256, signature, manifest

### 1.5 Testing (3 days)

Create an e2e test suite:
- `test/boot-to-youi.sh` — verifies boot → YOUI accessible
- `test/hive-connect.sh` — verifies HIVE tunnel establishes
- `test/heartbeat.sh` — verifies heartbeat fires
- `test/fate-present.sh` — verifies FATE covenant files load
- `test/virtiofs-shared.sh` — verifies /love-unlimited is readable + writable from VM

Target: all tests green on both arm64 and x86_64 images.

### Phase 1 deliverables
- ✅ Reproducible image builder
- ✅ GitHub Actions pipeline
- ✅ Signed, versioned releases
- ✅ e2e test suite
- ✅ 3-second boot
- ✅ `lima-kingdom.yaml` updated to use the baked image

### Phase 1 decision point
**If the honest answer is "Phase 1 is enough" → stop here.**
Phase 2-6 are documented for when/if you have a specific reason to continue. The remaining reasons that would justify going further:
- You want boot under 1 second (Phase 5-6)
- You need to distribute Kingdom OS outside Apple Silicon (Phase 3-4)
- You want to eliminate Alpine's userspace entirely for sovereignty reasons (Phase 2-6)
- You need to ship on hardware with constrained resources (Phase 3)
- A specific kernel feature becomes a liability and must be removed (Phase 3)

None of those apply right now. Ship Phase 1. Use Kingdom OS. Decide later if anything demands more.

---

## Phase 2 — Custom Init Replacement (2 weeks)

**Goal:** Replace OpenRC with a Kingdom-specific init system. Fewer moving parts, faster boot, explicit control over every startup step.

### 2.1 Understand the current boot chain (2 days)

Document what Alpine actually does on boot:
```
kernel → /sbin/init (OpenRC) → /etc/inittab → runlevels:
  sysinit → mount /proc, /sys, udev
  boot    → hostname, hwclock, sysctl
  default → networking, sshd, our provisioned services
  shutdown
```

Write this up as `docs/KINGDOM-BOOT-CHAIN.md` before changing anything.

### 2.2 Kingdom init (shell version first) (3 days)

Before writing a binary, prove the model in shell. Create `kingdom-os/kingdom-init.sh`:

```sh
#!/bin/sh
# PID 1 — Kingdom init
# Replaces OpenRC. No runlevels, no complexity.

# 1. Mount essential filesystems
mount -t proc proc /proc
mount -t sysfs sys /sys
mount -t devtmpfs dev /dev
mount -t tmpfs tmp /tmp
mount -t tmpfs run /run

# 2. Mount virtiofs shared directory
mkdir -p /love-unlimited
mount -t virtiofs love-unlimited /love-unlimited

# 3. Set hostname
agent=$(cat /root/.love/hive/instance 2>/dev/null || echo "kingdom")
hostname "kingdom-$agent"

# 4. Bring up network
ip link set lo up
udhcpc -i eth0 -q -n

# 5. Start HIVE tunnel (background)
/love-unlimited/tools/hive-tunnel.sh &

# 6. Start heartbeat daemon (background)
/love-unlimited/tools/heartbeat-runner.sh &

# 7. Start KOS daemon (background)
python3 /love-unlimited/tools/kos-daemon.sh &

# 8. Exec Kingdom Console as PID 1's foreground
exec /usr/bin/python3 /love-unlimited/tools/kingdom-console.py --agent "$agent"
```

Test by booting with `init=/kingdom-init.sh` on the kernel command line. Verify the VM still works.

### 2.3 Supervision of background services (3 days)

A shell init has no process supervision. If HIVE tunnel dies, nothing restarts it. Solutions (in order of increasing complexity):

1. **runit** (~40KB) — small, proven supervision suite. Runs each service as a child of `runsvdir`.
2. **s6** (~60KB) — Skarnet's supervision suite. More features, harder to debug.
3. **Custom** — write a supervision loop in shell (~50 lines).

Recommendation: **runit**. It's tiny, well-understood, and does exactly one thing.

Create `/etc/service/{hive-tunnel,heartbeat,kos}/run` scripts. `runsvdir /etc/service` becomes the single supervision entry point.

### 2.4 Replace OpenRC with Kingdom init in the image (4 days)

Modify the image builder (Phase 1) to:
1. Remove OpenRC entirely (`apk del openrc`)
2. Install `runit` (`apk add runit`)
3. Install `kingdom-init.sh` as `/sbin/init`
4. Configure runit service directory
5. Test that the image still boots

This is reversible. If it breaks, revert the builder change and we're back to Phase 1.

### Phase 2 deliverables
- ✅ `docs/KINGDOM-BOOT-CHAIN.md`
- ✅ `kingdom-os/kingdom-init.sh` (PID 1)
- ✅ runit service configs
- ✅ Modified image builder that produces OpenRC-free images
- ✅ e2e tests updated to verify kingdom-init boot path

### Phase 2 decision point
OpenRC is replaced. Boot is probably 1 second faster (~2s). The OS is now explicitly Kingdom-shaped rather than Alpine-with-Kingdom.

**Stop here if the initrd + kernel are acceptable as-is.** The remaining phases touch the kernel itself, which is significantly more risk and effort.

---

## Phase 3 — Stripped Kernel Configuration (3 weeks)

**Goal:** A Linux kernel with only what Kingdom OS needs. ~2MB compressed vs Alpine's ~8MB.

### 3.1 Set up kernel build environment (3 days)

```
kingdom-os/kernel/
├── config-arm64                # kconfig for arm64 target
├── config-x86_64               # kconfig for x86_64 target
├── build.sh                    # Reproducible kernel builder
├── patches/                    # Any local patches (ideally empty)
└── README.md                   # Build instructions
```

The builder runs inside a Docker container with `linux-source` + cross-compile toolchain for deterministic builds. Input: kernel version + config file. Output: `bzImage` + `System.map` + `.config` (for audit).

Kernel version: the latest Linux LTS (6.12 LTS as of writing). LTS gives us 2+ years of security backports.

### 3.2 Start from Alpine's kernel config (1 day)

Alpine's kernel config is a good baseline — it's already stripped for VM use. Clone it and set that as the starting point:
```
wget https://git.alpinelinux.org/aports/plain/main/linux-lts/config-lts.aarch64
cp config-lts.aarch64 kingdom-os/kernel/config-arm64
```

Build it, boot it, verify Kingdom OS still works. This proves the pipeline before we start cutting.

### 3.3 First strip pass — disable what's obviously unneeded (4 days)

Categories to disable:
```
# Hardware we don't have in a VM
CONFIG_WIRELESS=n
CONFIG_BT=n
CONFIG_USB_SUPPORT=n        # wait — virtio is not USB, we keep
CONFIG_SOUND=n
CONFIG_MEDIA_SUPPORT=n
CONFIG_DRM=n                # no graphics
CONFIG_FB=n
CONFIG_INPUT_MOUSEDEV=n
CONFIG_INPUT_JOYDEV=n

# Filesystems we don't use
CONFIG_REISERFS_FS=n
CONFIG_JFS_FS=n
CONFIG_XFS_FS=n
CONFIG_BTRFS_FS=n
CONFIG_F2FS_FS=n
CONFIG_NTFS_FS=n
CONFIG_NFS_FS=n              # Kingdom uses virtiofs, not NFS

# Networking we don't use
CONFIG_IPX=n
CONFIG_ATM=n
CONFIG_DECNET=n
CONFIG_IRDA=n

# Debug/tracing
CONFIG_FTRACE=n
CONFIG_KPROBES=n
CONFIG_DEBUG_KERNEL=n
```

Keep:
```
CONFIG_VIRTIO_*=y            # VM critical path
CONFIG_EXT4_FS=y
CONFIG_VIRTIO_FS=y           # virtiofs for /love-unlimited
CONFIG_NETWORK=y
CONFIG_INET=y
CONFIG_TCP_CONG_BBR=y        # good default congestion control
```

Build, boot, test. If something breaks, revert the specific flag and re-test. This is iterative.

### 3.4 Second strip pass — remove loadable module support (3 days)

`CONFIG_MODULES=n` is the big win. Kernel becomes monolithic — everything it needs is built in, nothing is loadable at runtime. Benefits:
- `/lib/modules/*` can be deleted (~20MB saved)
- `modprobe`/`insmod`/`rmmod` become unnecessary
- No module tainting, no unsigned module risk
- Smaller attack surface

Risk: if you removed something accidentally, you can't `modprobe` it back in. You have to rebuild the kernel.

Test thoroughly: boot the VM, run the full e2e suite, try every Kingdom operation.

### 3.5 Third strip pass — the risky stuff (5 days)

Things that *should* be removable but need careful testing:
- `CONFIG_IPV6=n` — if Kingdom only uses IPv4 internally
- `CONFIG_SMP=n` → `CONFIG_SMP=y` is actually needed for multi-core VMs, keep it
- `CONFIG_NAMESPACES=n` — **do not disable**, Kingdom uses network namespaces
- `CONFIG_CGROUPS=n` — risky, some tools use cgroups
- `CONFIG_PROC_PAGE_MONITOR=n` — diagnostic only, safe to disable

For each: disable, rebuild, boot, run full test suite, keep or revert.

### 3.6 Size audit and documentation (2 days)

Final measurements:
- Kernel binary size (target: <3MB compressed)
- Boot time (target: kernel handoff to PID 1 under 1 second)
- Memory footprint at idle (target: <50MB for kernel + init)

Write `docs/KINGDOM-KERNEL.md` explaining each choice. Every `=n` needs a justification.

### Phase 3 deliverables
- ✅ Reproducible kernel build pipeline
- ✅ `kingdom-os/kernel/config-{arm64,x86_64}` checked in
- ✅ Built kernel images for both archs
- ✅ Documentation of every config choice
- ✅ Image builder updated to install Kingdom kernel instead of `linux-virt`

### Phase 3 decision point
You now own your kernel. Every byte has a justification. Boot is probably down to ~2 seconds total.

**Stop here unless you need Phase 4+.** The kernel is the heavy lift. Phases 4-6 are polish.

---

## Phase 4 — Kernel Build Pipeline + Signing (1 week)

**Goal:** Automated, signed, reproducible kernel builds. Verifiable by any citizen.

### 4.1 Pin everything (2 days)

- Pin kernel version (exact git SHA, not just tag)
- Pin toolchain version (exact gcc/binutils)
- Pin Alpine builder base image SHA256
- Lock all dependencies with checksums

### 4.2 Sign the kernel (2 days)

Use the existing Kingdom signing key (from `credentials/walls.json`). The kernel binary and `.config` are signed separately so the config is auditable.

```
kingdom-kernel-6.12.8-arm64.bin
kingdom-kernel-6.12.8-arm64.bin.sig
kingdom-kernel-6.12.8-arm64.config
kingdom-kernel-6.12.8-arm64.config.sig
kingdom-kernel-6.12.8-arm64.manifest  # version, sha256, build time, builder identity
```

### 4.3 Kingdom OS citizens verify before boot (3 days)

Write a verification step in `kingdom-init.sh`:
```sh
if ! verify_kernel_signature; then
    echo "FATAL: kernel signature invalid — refusing to continue"
    exit 1
fi
```

This requires the kernel to be verifiable from inside itself, which is circular. The real verification happens at image-build time by the CI pipeline, and the citizen trusts the CI pipeline's signature.

### Phase 4 deliverables
- ✅ Fully pinned reproducible builds
- ✅ Signed kernel artifacts
- ✅ Verification in `kingdom-init.sh`
- ✅ CI workflow for kernel builds

---

## Phase 5 — Rust Kingdom Init Binary (3 weeks)

**Goal:** Replace `kingdom-init.sh` with a single statically-linked Rust binary. Faster boot, memory safety, dependency-free.

### 5.1 Why Rust (1 day)

- Memory safety for PID 1 (a crash here panics the kernel)
- Static linking (no libc dependency — we can remove glibc/musl entirely if we're bold)
- Small binary size (~500KB for what we need)
- Predictable behavior

Alternative: write in C. Faster to build, but memory bugs in PID 1 are catastrophic. Rust is worth the extra effort here.

### 5.2 Crate structure (3 days)

```
kingdom-os/kingdom-init/
├── Cargo.toml
├── src/
│   ├── main.rs           # PID 1 entry
│   ├── mount.rs          # mount /proc, /sys, virtiofs
│   ├── network.rs        # DHCP client (minimal)
│   ├── supervise.rs      # child process supervision
│   ├── hive.rs           # HIVE tunnel starter
│   ├── heartbeat.rs      # heartbeat daemon starter
│   └── console.rs        # exec kingdom-console as the session
├── build.rs              # static musl target
└── README.md
```

### 5.3 Implementation (2 weeks)

The binary must:
1. Mount essential filesystems (`mount` syscall directly)
2. Mount virtiofs shared dir
3. Read `/proc/cmdline` for agent identity
4. Set hostname
5. Bring up network (embed a minimal DHCP client — `dhcparse` crate, ~2KB)
6. Fork + supervise HIVE tunnel, heartbeat, KOS daemon
7. Exec the kingdom-console with the agent identity
8. Reap zombie children if anything dies
9. Handle SIGINT/SIGTERM gracefully (shutdown the VM cleanly)

Critical: this is PID 1. No crashes. Unit tests for every syscall wrapper. Fuzz testing.

### 5.4 Integration (3 days)

- Remove `kingdom-init.sh`
- Replace `/sbin/init` symlink with the compiled `kingdom-init` binary
- Remove runit (we now do supervision ourselves)
- Rebuild image
- Full e2e test

### Phase 5 deliverables
- ✅ `kingdom-os/kingdom-init/` Rust crate
- ✅ Single 500KB static binary as PID 1
- ✅ No shell required for boot
- ✅ Supervision of HIVE/heartbeat/KOS built in
- ✅ Image builder updated

---

## Phase 6 — Immutable Root + Filesystem Layout (2 weeks)

**Goal:** Kingdom OS root is read-only. Writable state is isolated to specific directories. Factory reset is one command.

### 6.1 Design the layout (2 days)

```
/                           (read-only base image)
├── usr/                    (system binaries — immutable)
├── sbin/init               → kingdom-init (immutable)
├── etc/                    (immutable, with /etc/kingdom-overlay for writable bits)
├── love-unlimited/         (virtiofs mount — host-shared)
└── kingdom/                (writable overlay)
    ├── memory/             (agent memory — persistent)
    ├── logs/               (heartbeat logs)
    ├── cache/              (temporary)
    └── state/              (runtime state)
```

Root uses `overlayfs`: a read-only base + a writable upper layer at `/kingdom/`. Changes to anything outside `/kingdom/` or `/love-unlimited/` are discarded on reboot.

### 6.2 overlayfs setup in kingdom-init (4 days)

Before mounting the real root, `kingdom-init` needs to:
1. Mount the base image read-only at `/rom`
2. Mount a tmpfs or disk partition at `/overlay`
3. Mount overlayfs combining them at `/`
4. Pivot root

This is the trickiest boot dance. Get it wrong and the VM is unbootable.

### 6.3 Factory reset (1 day)

`kingdom-os/vm-reset.sh` — deletes the overlay partition, next boot starts fresh. Memory is lost (unless backed up to host via virtiofs first).

### 6.4 Test immutability (3 days)

- Verify `rm -rf /usr` fails (read-only)
- Verify writes to `/kingdom/memory/` persist across reboots
- Verify factory reset wipes overlay cleanly
- Measure cold boot time (target: <1.5 seconds)

### 6.5 Documentation + shipping (4 days)

- Write `docs/KINGDOM-FILESYSTEM.md`
- Update all references from "install Kingdom OS" to "run Kingdom OS"
- Final e2e test pass
- Tag release v1.0

### Phase 6 deliverables
- ✅ Immutable Kingdom OS root
- ✅ Isolated writable state in `/kingdom/`
- ✅ Factory reset tooling
- ✅ Final architecture docs
- ✅ v1.0 release

---

## Total Effort Summary

| Phase | Name | Effort | Cumulative | Value |
|------:|------|:------:|:----------:|:-----:|
| 0 | Lima template | done | done | ■■■■■ |
| 1 | Pre-baked image | 2w | 2w | ■■■ |
| 2 | Custom init | 2w | 4w | ■■ |
| 3 | Stripped kernel | 3w | 7w | ■■ |
| 4 | Build + sign | 1w | 8w | ■ |
| 5 | Rust init | 3w | 11w | ■ |
| 6 | Immutable root | 2w | 13w | ■■ |

**Total to v1.0 custom kernel Kingdom OS: ~13 weeks of focused full-time work.**
Realistically with other Kingdom priorities: 4-6 months.

---

## Decision Framework — When to Execute Each Phase

| Question | If YES → phase |
|----------|:---:|
| Do new citizens need 3-second setup on any Mac? | Phase 1 |
| Is the bare Alpine init surface the source of bugs? | Phase 2 |
| Does a specific kernel feature need to be removed for security? | Phase 3 |
| Do you need citizens to verify their own kernel binary? | Phase 4 |
| Is sub-second boot a functional requirement? | Phase 5 |
| Does Kingdom state need to survive factory reset attacks? | Phase 6 |

**None of these are true today.** Phase 1 is the honest next step. Everything beyond Phase 1 should be executed only when a specific question above becomes a `YES` for real operational reasons, not philosophical ones.

---

## Risks and How Each Phase Manages Them

### Phase 1 risks
- **Image drift** — the builder produces different output on different machines
  - Mitigation: Dockerized builder with pinned base image + deterministic flags
- **Compromised release** — an attacker publishes a fake image
  - Mitigation: signed artifacts + Lima verifies SHA256 on download
- **Broken upgrade** — new image breaks existing VM state
  - Mitigation: versioned releases + rollback command in `vm-start.sh`

### Phase 2 risks
- **Init crashes = unbootable VM**
  - Mitigation: test the shell init by swapping PID 1 on a running system first
- **Lost services** — something the old init was doing silently isn't done anymore
  - Mitigation: full e2e test, audit the boot chain doc from 2.1

### Phase 3 risks
- **Accidentally disabling something Kingdom needs**
  - Mitigation: iterative, one config flag at a time, test between each
- **Security regressions** — removed a mitigation you didn't know was defending something
  - Mitigation: read the kconfig help for every flag before changing it

### Phase 5 risks
- **Rust PID 1 panic = kernel panic**
  - Mitigation: extensive unit tests, fuzz testing of mount/network code, panic handler that prints to serial console

### Phase 6 risks
- **overlayfs misconfiguration = writes vanish on reboot**
  - Mitigation: dedicated test that writes to `/kingdom/memory/` and verifies persistence
- **Factory reset destroys something critical**
  - Mitigation: `/kingdom/memory/` is the only thing touched; virtiofs mount is host-backed and untouched

---

## What We Do NOT Do

Things that sound appealing but don't belong on this roadmap:

**No custom bootloader.** U-Boot / GRUB / EFI stubs are fine. Writing our own is a month of work for no benefit.

**No custom libc.** musl is already tiny and well-tested. Rolling our own is weeks of work to save kilobytes.

**No custom shell.** busybox ash or dash is fine. We barely use the shell anyway once Phase 5 is done.

**No kernel forks.** We maintain a *config*, not a *fork*. All patches go upstream or we don't take them. This keeps maintenance sustainable.

**No custom network stack.** Linux's TCP/IP is excellent. We'd spend years failing to match it.

**No custom filesystem.** ext4 + virtiofs + overlayfs cover everything. Writing our own is pure academic vanity.

---

## The Honest Recommendation

1. **Execute Phase 1 now.** Deterministic baked image is high value and the CI/test infrastructure it requires is valuable for every subsequent phase.
2. **Stop and use it.** Run Kingdom OS daily. See what actually breaks, what feels slow, what annoys you.
3. **Let real problems drive the next phase.** If boot time ever becomes a pain, execute Phase 2. If kernel features ever need removal for security, execute Phase 3. And so on.
4. **Do not execute phases pre-emptively.** Every phase is reversible, but the effort is not refundable. Spending 8 weeks on a custom kernel that doesn't solve a current problem is pouring work into the void.

Phase 1 gives you a reproducible, shippable Kingdom OS image. That is almost certainly enough. The rest of this document exists so the path is visible when the reason arrives — not so the path is executed because it's visible.

---

*"Whoever is faithful in a very little is also faithful in much." — Luke 16:10*

*Ship Phase 1 first. Earn the right to Phase 2.*

*בני אל עליון*
