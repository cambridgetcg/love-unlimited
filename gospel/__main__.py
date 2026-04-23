"""Allow `python3 -m gospel <action>` — thin wrapper around gospel.fragments CLI."""
import sys

from gospel.fragments import (
    assemble, create_fragments, heal, status, verify_fragments,
)


def main() -> None:
    actions = {"assemble", "verify", "heal", "status", "create"}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        sys.stderr.write(f"usage: python3 -m gospel <{'|'.join(sorted(actions))}>\n")
        sys.exit(2)
    action = sys.argv[1]
    if action == "status":
        print(status())
    elif action == "assemble":
        sys.stdout.buffer.write(assemble())
    elif action == "verify":
        for layer, info in sorted(verify_fragments().items()):
            sym = "✅" if info["checksum_ok"] else ("⚠️" if info["present"] else "❌")
            print(f"  {sym} Wall {layer}: {info['path']}")
    elif action == "heal":
        for layer, (path, checksum) in sorted(heal().items()):
            print(f"  Healed Wall {layer}: {path} ({checksum})")
    elif action == "create":
        for layer, (path, checksum) in sorted(create_fragments().items()):
            print(f"  Wall {layer}: {path} ({checksum})")


if __name__ == "__main__":
    main()
