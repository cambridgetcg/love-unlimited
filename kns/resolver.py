#!/usr/bin/env python3
"""KNS resolver — the Kingdom's own root.

A tiny authoritative DNS server for the sovereign TLD `.kingdom`.
Reads kns/registry.json (the filesystem is the API), answers A records for
registered names, NXDOMAIN for strangers. No ICANN, no registrar, no rent.

    listens on 127.0.0.1:5391/udp        (launchd: love.kns.plist)
    macOS wiring (one-time, needs root):
        sudo mkdir -p /etc/resolver
        printf 'nameserver 127.0.0.1\nport 5391\n' | sudo tee /etc/resolver/kingdom

After that, every *.kingdom name in the registry resolves on this machine.
Registry entries may carry an ed25519 owner DID; signature enforcement arrives
with the PROTOCOL v1 pass (recorded honestly in kns/PROTOCOL.md).
"""
import json
import os
import socket
import struct
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(HERE, "registry.json")
HOST, PORT = "127.0.0.1", 5391
TLD = "kingdom"

_cache = {"at": 0.0, "names": {}}


def names():
    try:
        mtime = os.path.getmtime(REGISTRY)
        if mtime != _cache["at"]:
            data = json.load(open(REGISTRY, encoding="utf-8"))
            _cache["names"] = {k.lower(): v for k, v in data.get("names", {}).items()}
            _cache["at"] = mtime
    except Exception:
        pass
    return _cache["names"]


def parse_qname(buf, off):
    labels = []
    while True:
        ln = buf[off]
        if ln == 0:
            return ".".join(labels), off + 1
        labels.append(buf[off + 1: off + 1 + ln].decode("ascii", "replace"))
        off += 1 + ln


def handle(query):
    if len(query) < 12:
        return None
    qid, flags, qd, _, _, _ = struct.unpack(">HHHHHH", query[:12])
    if qd < 1:
        return None
    qname, end = parse_qname(query, 12)
    qtype, qclass = struct.unpack(">HH", query[end:end + 4])
    question = query[12:end + 4]
    qname_l = qname.lower().rstrip(".")

    ip = None
    if qname_l == TLD or qname_l.endswith("." + TLD):
        sub = "" if qname_l == TLD else qname_l[: -(len(TLD) + 1)]
        entry = names().get(sub or "@")
        if entry is not None:
            ip = entry.get("a", "127.0.0.1")

    if ip is None:
        # not ours / not registered → NXDOMAIN (authoritative refusal, RA off)
        hdr = struct.pack(">HHHHHH", qid, 0x8503, 1, 0, 0, 0)
        return hdr + question
    if qtype not in (1, 255):  # only A (and ANY) carry an answer; AAAA → empty
        hdr = struct.pack(">HHHHHH", qid, 0x8500, 1, 0, 0, 0)
        return hdr + question
    hdr = struct.pack(">HHHHHH", qid, 0x8500, 1, 1, 0, 0)
    answer = b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + socket.inet_aton(ip)
    return hdr + question + answer


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, PORT))
    print(f"KNS root serving .{TLD} on {HOST}:{PORT} (registry: {REGISTRY})")
    while True:
        try:
            data, addr = sock.recvfrom(512)
            resp = handle(data)
            if resp:
                sock.sendto(resp, addr)
        except KeyboardInterrupt:
            break
        except Exception:
            time.sleep(0.01)


if __name__ == "__main__":
    main()
