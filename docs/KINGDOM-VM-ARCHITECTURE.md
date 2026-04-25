# Kingdom VM Architecture

_Kingdom OS as an isolated VM layer on macOS._
_Built on Apple Virtualization.framework. Same image as the VPS fleet._

---

## Why a VM, Not Native

Running Kingdom OS natively on macOS works. But a VM gives you something better:

| | Native macOS | Kingdom VM |
|--|:--:|:--:|
| TCC permission dialogs | constant | none (Linux has no TCC) |
| SIP restrictions | yes | none inside VM |
| Reproducible across Macs | manual setup | `limactl create` + done |
| Same image as VPS fleet | no (different OS) | yes — same Alpine |
| Process isolation | none | full namespace isolation |
| No-prompt keyboard automation | requires TCC Accessibility | native inside VM |
| Screen capture for koseyes | requires TCC Screen Recording | native inside VM |
| Boot to YOUI automatically | launchd heartbeat | VM boots to YOUI |
| Deploy to new Mac | 30 min setup | 5 min |
| Performance loss | 0% | ~3% (HVF acceleration) |

The VM costs you 3% performance and gains you full OS-level sovereignty.

---

## The Stack

```
┌─────────────────────────────────────────────────────────────────┐
│  Apple Silicon (M4 / M3 / M2 / M1)                             │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  macOS Sequoia — substrate only                          │   │
│  │                                                          │   │
│  │  ┌────────────────────────────────────────────────────┐  │   │
│  │  │  Apple Virtualization.framework (HVF accelerator) │  │   │
│  │  │                                                    │  │   │
│  │  │  ┌──────────────────────────────────────────────┐ │  │   │
│  │  │  │  Kingdom VM (ARM64 Alpine Linux)              │ │  │   │
│  │  │  │                                               │ │  │   │
│  │  │  │  ┌────────────┐  ┌────────────────────────┐  │ │  │   │
│  │  │  │  │   Kernel   │  │   Kingdom init / YOUI  │  │ │  │   │
│  │  │  │  │  (Alpine)  │  │   (boots on tty1)      │  │ │  │   │
│  │  │  │  └────────────┘  └────────────────────────┘  │ │  │   │
│  │  │  │                                               │ │  │   │
│  │  │  │  /love-unlimited ←─virtiofs─→ ~/love-unltd   │ │  │   │
│  │  │  │                                               │ │  │   │
│  │  │  │  HIVE tunnel → SSH → Sentry NATS              │ │  │   │
│  │  │  │  Heartbeat (7 min)                            │ │  │   │
│  │  │  │  KOS daemon                                   │ │  │   │
│  │  │  └──────────────────────────────────────────────┘ │  │   │
│  │  │                                                    │  │   │
│  │  │  Lima daemon (manages VM lifecycle)                │  │   │
│  │  └────────────────────────────────────────────────────┘  │   │
│  │                                                          │   │
│  │  ~/love-unlimited/ (host, visible to VM via virtiofs)    │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## The Technology Choices

### Virtualization.framework (`vmType: vz`)

Apple's native hypervisor, available since macOS 12. Uses the Hardware Virtualization Features (HVF) built into Apple Silicon. Performance is ~97% of bare metal.

This is what UTM, Parallels, and OrbStack use under the hood. Lima exposes it as `vmType: vz` in its YAML config.

**Why not QEMU?**
QEMU is portable and powerful but has overhead on Apple Silicon. For Kingdom OS — which always runs on Apple Silicon Macs and identical Alpine VPS nodes — the QEMU portability is not needed. Virtualization.framework is faster, simpler, and fully supported on macOS 12+. The old `vm-create.sh` used QEMU and is now superseded.

### Lima (`limactl`)

Lima (Linux Machines) is the VM manager layer — already installed on this machine. It provides:
- Declarative VM specification (YAML)
- Automatic virtiofs setup
- Port forwarding configuration
- Provisioning scripts (run on first boot)
- `limactl shell <name>` for easy access

Think of Lima as the equivalent of Docker Compose but for full Linux VMs. Colima (already running) uses Lima under the hood.

### virtiofs

The filesystem sharing technology. Zero-copy, zero-latency. When `~/love-unlimited/` is mounted into the VM via virtiofs, the host and VM are literally reading and writing the same inodes. No sync delay. No copy. One filesystem, two views.

This means:
- CLAUDE.md edits on the host are instantly visible in the VM
- Daily notes written by the heartbeat inside the VM appear on the host
- Memory writes are coherent across both
- The VM IS Kingdom OS, not a copy of it

### Alpine Linux (`alpine-virt`)

The `virt` variant of Alpine — stripped for VMs (no framebuffer, no hardware drivers). Tiny (~40MB). Fast boot. Same as the VPS fleet. The Kingdom's language.

Custom additions in `provision:` block:
- Node.js, Python, git, ripgrep, jq, tmux
- pip packages: nats-py, PyNaCl, nkeys
- Shell aliases for Kingdom tools
- FATE environment variables (DISABLE_TELEMETRY etc.)
- `/love-unlimited` symlink to virtiofs mount

---

## Three Levels of Kingdom OS VM

### Level 1 — Lima Template (now, 2 days)

`kingdom-os/lima-kingdom.yaml` → `kingdom-os/vm-start.sh`

```bash
# On any Mac with lima installed:
brew install lima
cd ~/love-unlimited
./kingdom-os/vm-start.sh --agent alpha
./kingdom-os/vm-start.sh --youi
```

Kingdom OS is in a VM, isolated, virtiofs shared. The VM provisions itself on first boot. TCC-free, SIP-free.

### Level 2 — Pre-baked Image (1-2 weeks)

Build an Alpine image with Kingdom OS already installed (no provision script needed). Distribute as a URL in `lima-kingdom.yaml`.

```yaml
images:
  - location: "https://codeberg.org/zerone-dev/love-unlimited/releases/download/v1.0/kingdom-os-aarch64.qcow2"
    arch: "aarch64"
    digest: "sha256:..."
```

Boot time: ~2–3 seconds. No internet required on first boot. Image is signed.

Build pipeline:
1. `packer` or a custom shell script that creates and provisions a QEMU image
2. Compress with `qemu-img convert -c`
3. Sign the image (sha256 in the Lima YAML)
4. Upload to GitHub Releases

### Level 3 — Custom Kingdom OS (1-2 months)

A purpose-built OS where Kingdom IS the OS:

- Custom kernel config (strip: USB, Bluetooth, wireless drivers, framebuffer, sound — keep: VirtIO, NVMe, network, KVM guest)
- Custom init (not OpenRC — a small shell script: boot → mount virtiofs → start HIVE tunnel → start heartbeat → exec YOUI)
- Filesystem layout: `/kingdom/` instead of `/home/kingdom/`
- Immutable root (overlayfs: read-only base + writable `/kingdom/memory`)
- Single binary that IS the kingdom-init (written in Rust or C)
- Boot to YOUI in under 1 second

At this level it is genuinely a Kingdom operating system — not Alpine-with-Kingdom, but Kingdom as the native environment.

---

## Colima Relationship

Colima is already running on this machine, and it uses Lima with `vmType: vz`. The Kingdom VM adds a *separate* Lima VM alongside Colima. They don't interfere.

Colima is for Docker containers (general development). The Kingdom VM is for Kingdom OS (sovereign agent runtime). Two separate VMs, two separate purposes.

If you want to run Kingdom OS *inside* a container rather than a VM, that's possible too — but you lose the virtiofs direct mount and the isolated process namespace. The VM is the right choice for Kingdom OS.

---

## Getting Started

```bash
# 1. Lima is already installed (limactl 2.0.3 confirmed)

# 2. Create + start the Kingdom VM
cd ~/love-unlimited
./kingdom-os/vm-start.sh --agent gamma --wall 1

# 3. Enter it
./kingdom-os/vm-start.sh --shell

# 4. Inside the VM — everything works
youi           # KINGDOM YOUI terminal
hive check     # HIVE messages
fate           # daily discipline
kos audit      # security audit
kingdom status # Kingdom status

# 5. Your ~/love-unlimited/ is /love-unlimited inside the VM
# Same files. No copy. No sync. One filesystem.
```

---

## Roadmap

- [ ] **Level 1**: `lima-kingdom.yaml` + `vm-start.sh` (complete — in repo)
- [ ] **Level 2**: Pre-baked Alpine image builder (`kingdom-os/build-image.sh`)
- [ ] **Level 2**: GitHub Release hosting for the image
- [ ] **Level 2**: Auto-update mechanism (check for new image on start)
- [ ] **Level 3**: Custom kernel config (`kingdom-os/kernel.config`)
- [ ] **Level 3**: Kingdom init script (`kingdom-os/kingdom-init.sh`)
- [ ] **Level 3**: Immutable root with overlayfs
- [ ] **Level 3**: Sub-1-second boot to YOUI

---

*The Kingdom will be powered by Zerone. The Kingdom OS will run on sovereign silicon.*
*בני אל עליון*
