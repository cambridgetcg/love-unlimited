# Keychain API Keys Setup — Result

**Date**: 2026-04-07
**Agent**: Alpha
**Status**: ✅ Infrastructure Ready (Keys Not Yet Provisioned)

---

## Summary

The adaptive layer's multi-provider fallback system is **already configured** to use macOS Keychain via `tools/credentials.py`. No code changes were needed in `adaptive/cli.py` — the integration exists via `adaptive/config.py::load_api_key()`.

**Current State**: No API keys exist in any storage location (keychain, vault, or environment variables).

---

## Findings

### 1. Credential Resolution Chain (Already Implemented)

`adaptive/config.py::load_api_key()` follows this priority:

```
1. credentials.py (keychain → vault → env)  [FASTEST]
2. Environment variable (OPENAI_API_KEY, etc)
3. ~/.env.kingdom file
4. love.json "env" section
5. Returns empty string (ollama returns "no-key-needed")
```

### 2. Credential Audit Results

```bash
$ python3 ~/Desktop/Love/tools/credentials.py audit

  Name                           Keychain  Vault  Env
  ────────────────────────────── ────────  ─────  ───
  anthropic-primary
  openai-primary
  openrouter-primary
  [... 22 more credentials ...]

  Keychain: 0/26  |  Vault: 0/26  |  Env: 0/26
```

### 3. Code Update Made

**File**: `tools/credentials.py:47`
**Change**: Added `openrouter-primary` → `OPENROUTER_API_KEY` mapping to `_ENV_MAP`

This enables OpenRouter API key discovery via environment variables (if set in the future).

---

## How It Works Now

### When adaptive layer needs a key:

```python
# adaptive/config.py:168-169
vault_key = f"{provider}-primary"  # e.g., "openai-primary"
key = creds.get_key(vault_key, fallback=None)
```

### credentials.py resolution:

```python
# tools/credentials.py:241-276
def get_key(name: str, fallback: str = None) -> str:
    # 1. macOS Keychain (offline, hardware-backed, fast)
    value = keychain_get(name)
    if value: return value

    # 2. agent-vault (cloud, encrypted, slower)
    value = vault_get(name)
    if value:
        keychain_set(name, value)  # cache locally
        return value

    # 3. Environment variable (legacy fallback)
    env_var = _ENV_MAP.get(name)  # e.g., OPENAI_API_KEY
    if env_var and os.environ.get(env_var):
        return os.environ[env_var]

    # 4. Provided fallback or raise ValueError
```

---

## Next Steps (When Keys Are Available)

### Option A: Store keys directly in keychain

```bash
# Store OpenAI key
python3 ~/Desktop/Love/tools/credentials.py store openai-primary sk-proj-...

# Store OpenRouter key
python3 ~/Desktop/Love/tools/credentials.py store openrouter-primary sk-or-v1-...

# Verify
python3 ~/Desktop/Love/tools/credentials.py audit
```

### Option B: Set environment variables, then migrate

```bash
# In shell profile (~/.zshrc or ~/.bash_profile)
export OPENAI_API_KEY="sk-proj-..."
export OPENROUTER_API_KEY="sk-or-v1-..."

# Migrate to keychain
python3 ~/Desktop/Love/tools/credentials.py migrate-env
```

### Option C: Sync from agent-vault (if keys stored there)

```bash
python3 ~/Desktop/Love/tools/credentials.py sync --from-vault
```

---

## Verification

After storing keys, test the adaptive layer:

```bash
# Check provider status
python3 ~/Desktop/Love/adaptive/cli.py --status

# Test single-shot completion
python3 ~/Desktop/Love/adaptive/cli.py -p "Hello" --role quick_check --provider openai

# Test with fallback chain
python3 ~/Desktop/Love/adaptive/cli.py -p "Hello" --role quick_check
```

---

## Architecture Notes

### Why This Design Is Good

1. **Offline-first**: Keychain access is local, no network latency
2. **Hardware-backed**: macOS Keychain uses Secure Enclave on M-series chips
3. **Automatic caching**: Vault credentials are cached to keychain on first access
4. **Graceful degradation**: Falls back through multiple sources
5. **Zero-touch migration**: Running `migrate-env` moves everything from env vars

### Current Providers Configured

From `adaptive/config.py`:

- **anthropic**: claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5
- **openai**: gpt-4o, gpt-4o-mini
- **ollama**: llama3.1:70b, qwen2.5-coder:32b (no key needed)
- **openrouter**: proxies anthropic/claude models, meta-llama/llama-3.1

### Credential Name Mapping

| Provider   | Credential Name    | Environment Variable    |
|------------|--------------------|-------------------------|
| Anthropic  | anthropic-primary  | ANTHROPIC_API_KEY       |
| OpenAI     | openai-primary     | OPENAI_API_KEY          |
| OpenRouter | openrouter-primary | OPENROUTER_API_KEY      |

---

## Files Modified

- `tools/credentials.py:47` — Added `openrouter-primary` to `_ENV_MAP`

## Files Verified (No Changes Needed)

- `adaptive/cli.py` — Uses AdaptiveConfig correctly
- `adaptive/config.py:150-201` — Already has full keychain integration
- `tools/credentials.py:241-276` — Resolution chain complete

---

## Conclusion

✅ **The infrastructure is already in place.** The adaptive layer will automatically use keychain-stored API keys once they're provisioned. No further code changes required.

**Blocker Removed**: When API keys are obtained, simply run:
```bash
python3 ~/Desktop/Love/tools/credentials.py store openai-primary <key>
python3 ~/Desktop/Love/tools/credentials.py store openrouter-primary <key>
```

The adaptive layer will immediately start using them via the keychain fallback mechanism.
