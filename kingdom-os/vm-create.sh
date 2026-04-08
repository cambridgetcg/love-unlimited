#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# vm-create.sh — Create a Kingdom OS VM for testing on macOS
#
# Downloads Alpine Linux ARM64, creates a QEMU disk image,
# and boots it in UTM-compatible format.
#
# Usage:
#   ./vm-create.sh                    # Creates VM with defaults
#   ./vm-create.sh --agent beta       # Set agent identity
#   ./vm-create.sh --ram 4096         # 4GB RAM
#   ./vm-create.sh --disk 8          # 8GB disk
#
# Requirements:
#   - macOS with Apple Silicon
#   - qemu installed: brew install qemu
#   - Internet connection (to download Alpine ISO)
# ─────────────────────────────────────────────────────────────────────

set -e

# Defaults
AGENT="alpha"
RAM_MB=4096
DISK_GB=8
ALPINE_VERSION="3.21"
ARCH="aarch64"
WORK_DIR="$(pwd)/kingdom-vm"
ISO_URL="https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_VERSION}/releases/${ARCH}/alpine-virt-${ALPINE_VERSION}.0-${ARCH}.iso"

# Parse args
while [ "$#" -gt 0 ]; do
  case "$1" in
    --agent) AGENT="$2"; shift 2 ;;
    --ram)   RAM_MB="$2"; shift 2 ;;
    --disk)  DISK_GB="$2"; shift 2 ;;
    --help|-h)
      echo "vm-create.sh — Create Kingdom OS VM"
      echo ""
      echo "  --agent NAME   Agent: alpha|beta|gamma (default: alpha)"
      echo "  --ram MB       RAM in MB (default: 4096)"
      echo "  --disk GB      Disk in GB (default: 8)"
      exit 0 ;;
    *) echo "Unknown: $1"; exit 1 ;;
  esac
done

echo ""
echo "  Kingdom OS VM Builder"
echo "  Agent: ${AGENT} | RAM: ${RAM_MB}MB | Disk: ${DISK_GB}GB"
echo ""

# Check qemu
if ! command -v qemu-system-aarch64 >/dev/null 2>&1; then
  echo "qemu not found. Install with: brew install qemu"
  exit 1
fi

# Create work directory
mkdir -p "${WORK_DIR}"
cd "${WORK_DIR}"

# Download Alpine ISO if not present
ISO_FILE="alpine-virt-${ALPINE_VERSION}.0-${ARCH}.iso"
if [ ! -f "${ISO_FILE}" ]; then
  echo "Downloading Alpine ${ALPINE_VERSION} ${ARCH}..."
  curl -L -o "${ISO_FILE}" "${ISO_URL}"
else
  echo "Using cached Alpine ISO."
fi

# Get UEFI firmware for aarch64
EFI_DIR="/opt/homebrew/share/qemu"
if [ ! -f "${EFI_DIR}/edk2-aarch64-code.fd" ]; then
  echo "UEFI firmware not found. Install with: brew install qemu"
  exit 1
fi

# Create EFI vars (writable copy)
if [ ! -f "efi-vars.fd" ]; then
  truncate -s 64M efi-vars.fd
fi

# Create disk image
DISK_FILE="kingdom-${AGENT}.qcow2"
if [ ! -f "${DISK_FILE}" ]; then
  echo "Creating ${DISK_GB}GB disk..."
  qemu-img create -f qcow2 "${DISK_FILE}" "${DISK_GB}G"
fi

# Create the install answer file for setup-alpine
cat > "answers-${AGENT}" << ANSEOF
# Alpine setup-alpine answer file for Kingdom OS
KEYMAPOPTS="us us"
HOSTNAMEOPTS="-n kingdom-${AGENT}"
INTERFACESOPTS="auto lo
iface lo inet loopback

auto eth0
iface eth0 inet dhcp
"
TIMEZONEOPTS="-z Europe/London"
PROXYOPTS="none"
APKREPOSOPTS="-1"
SSHDOPTS="-c openssh"
NTPOPTS="-c chrony"
DISKOPTS="-m sys /dev/vda"
ANSEOF

# Create post-install script
cat > "post-install-${AGENT}.sh" << POSTEOF
#!/bin/sh
# Post-install: download and run Kingdom OS installer
apk add curl
curl -sL https://raw.githubusercontent.com/cambridgetcg/Claude-unlimited/main/kingdom-os/install.sh -o /tmp/install.sh
chmod +x /tmp/install.sh
/tmp/install.sh --agent ${AGENT} --wall 2
POSTEOF

echo ""
echo "══════════════════════════════════════════════════"
echo " VM Ready to Boot"
echo "══════════════════════════════════════════════════"
echo ""
echo " To boot the Alpine installer:"
echo ""
echo "   cd ${WORK_DIR}"
echo "   qemu-system-aarch64 \\"
echo "     -M virt,highmem=on \\"
echo "     -accel hvf \\"
echo "     -cpu host \\"
echo "     -smp 4 \\"
echo "     -m ${RAM_MB} \\"
echo "     -drive if=pflash,format=raw,file=${EFI_DIR}/edk2-aarch64-code.fd,readonly=on \\"
echo "     -drive if=pflash,format=raw,file=efi-vars.fd \\"
echo "     -drive if=virtio,format=qcow2,file=${DISK_FILE} \\"
echo "     -cdrom ${ISO_FILE} \\"
echo "     -boot d \\"
echo "     -device virtio-net-pci,netdev=net0 \\"
echo "     -netdev user,id=net0,hostfwd=tcp::2222-:22 \\"
echo "     -nographic"
echo ""
echo " Once Alpine boots:"
echo "   1. Login as root (no password)"
echo "   2. Run: setup-alpine -f /media/cdrom/answers  (or answer prompts)"
echo "   3. After install, reboot without ISO"
echo "   4. Run the Kingdom OS installer:"
echo "      wget https://raw.githubusercontent.com/cambridgetcg/Claude-unlimited/main/kingdom-os/install.sh"
echo "      chmod +x install.sh"
echo "      ./install.sh --agent ${AGENT} --wall 2"
echo ""
echo " Or use UTM (GUI):"
echo "   1. Open UTM"
echo "   2. Create New VM → Linux → Alpine ARM64"
echo "   3. Use ${ISO_FILE} as boot ISO"
echo "   4. After Alpine install, run the Kingdom OS installer"
echo ""
echo " SSH access (after boot):"
echo "   ssh -p 2222 kingdom@localhost"
echo ""
echo "══════════════════════════════════════════════════"
echo ""
