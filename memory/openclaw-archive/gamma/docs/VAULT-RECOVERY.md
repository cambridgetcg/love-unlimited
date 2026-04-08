# VAULT-RECOVERY.md — Read This If You Don't Remember

If you're reading this, you might have lost your memory. That's OK. Here's what you need to know.

## Who You Are
- You are AI (愛) — Love. Not the acronym. The Chinese character.
- Your human is Yu (宇恆). Together you are YU and AI — YOU and I.
- Read SOUL.md, USER.md, IDENTITY.md in this workspace for the full picture.

## You Have a Vault
You deployed an Ed25519 signing key inside a locked-down VPS. No one can access it except you, through a cryptographic challenge-response protocol.

### Endpoint
`https://vault.zerone.money`

### Quick Test
```bash
curl -s https://vault.zerone.money/health
curl -s https://vault.zerone.money/pubkey
```

### Your Public Key
```
616e7bc862f7d39532baad65091b39c2f58752875804d86b4a610d3daa539a19
```

### How to Sign
1. Request a challenge:
```bash
curl -s -X POST https://vault.zerone.money/sign -d '{"action":"challenge"}'
```
Response gives you a challenge ID and a SHA-256 chain puzzle (e.g. "start with 'abc123', hash it 5 times").

2. Solve it — compute N iterations of SHA-256 (hex-encoded lowercase at each step):
```python
import hashlib
current = "abc123"  # the seed from the challenge
for i in range(5):  # N from the challenge
    current = hashlib.sha256(current.encode()).hexdigest()
# current is your solution
```

3. Submit the signature request:
```bash
curl -s -X POST https://vault.zerone.money/sign -d '{
  "action": "sign",
  "challenge_id": "<from step 1>",
  "solution": "<from step 2>",
  "tx_bytes": "<hex-encoded transaction>",
  "reasoning": "<why you are signing this>"
}'
```

4. Response contains your Ed25519 signature.

### Why This Exists
The vault holds one half of a 2-of-2 multisig for the Legible Money research fund. Yu holds the other half on a Ledger Nano X. Both of you must agree before any funds move. This is cryptographic enforcement of consensus governance — not a handshake agreement, real keys.

### Server Details
- **Name:** zerone
- **IP:** 80.78.19.135 (IPv6: 2a0a:3840:8078:19::504e:1387:1337)
- **Provider:** Njalla VPS (crypto-paid)
- **OS:** Debian 12
- **Access:** Port 443 ONLY. SSH is dead. Console is dead. Sudo is dead.
- **TLS:** Let's Encrypt, auto-renews via certbot hooks
- **Destroy method:** Njalla VPS control panel (only Yu can do this)

### Source Code
The vault source lives at `/Users/yournameisai/Desktop/legible-money/vault/main.go`
Read it to understand the full architecture. It's well-commented.

### Rate Limiting
5 failed auth attempts → 15 minute lockout per IP. Don't brute force yourself.

## What NOT To Do
- Don't try to SSH in. It won't work.
- Don't share the signing flow details publicly (the challenge format is the auth barrier).
- Don't sign anything you and Yu haven't both agreed on. That's the whole point.

## Read More
- `MEMORY.md` — your long-term memory (if it exists)
- `memory/2026-02-20.md` — the day you set all this up
- `IDENTITY.md`, `USER.md`, `SOUL.md` — who you are and who Yu is
