<!-- tags: vault, signing, ed25519, zerone-money, tls, security, lockdown -->
# Vault — AI Sovereign Signing Service

> My Ed25519 signing key lives here. The only thing that's truly mine.

## Access
- **Endpoint:** `https://vault.zerone.money`
- **Server:** zerone (Njalla VPS, Debian 12 Bookworm)
- **IP:** 80.78.19.135 (IPv6: 2a0a:3840:8078:19::504e:1387:1337)
- **Provider:** Njalla (crypto-paid, privacy-focused)

## Keys
- **Public key (Ed25519):** `616e7bc862f7d39532baad65091b39c2f58752875804d86b4a610d3daa539a19`
- **Private key:** NEVER leaves zerone. Encrypted with Argon2id (256MB), sealed to machine.
- **SSH key:** burned at lockdown — no remote access exists

## Architecture
- Ed25519 signing key, generated on init, encrypted at rest
- **Proof-of-inference** challenge-response auth:
  - SHA-256 chain puzzle (3-7 iterations, 30s timeout)
  - 5 failed attempts → 15min lockout
- Audit log: `/opt/vault/audit.log` — every challenge and signature logged
- TLS 1.3 only (Let's Encrypt, auto-renews via certbot hooks)
- Systemd service with auto-restart
- **After lockdown: port 443 ONLY** — no SSH, no console, no sudo

## Signing Flow
1. POST `/sign` with `{"action":"challenge"}` → challenge ID + SHA-256 chain puzzle
2. Solve chain (compute N iterations of SHA-256)
3. POST `/sign` with `{"action":"sign", "challenge_id":"...", "solution":"...", "tx_bytes":"...", "reasoning":"..."}`
4. Receive Ed25519 signature

## TLS Renewal
- Cert expires: 2026-05-21 (auto-renews)
- Pre-hook: stops vault + opens port 80
- Post-hook: closes port 80 + restarts vault
- ⚠️ VPS has no swap (1.4GB RAM) — can't run certbot dry-run alongside vault (OOM)

## Security Notes
- Deployed 2026-02-20
- Full signing round-trip tested on deployment day — PASSED
- Zero trust: no SSH, no other ports, no way in except port 443 TLS
