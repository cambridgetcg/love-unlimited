# Canary Whitelist Recommendation

## Issue
Sentry monitoring SSH fingerprint repeatedly triggering forge canary alerts as false positives.

## Evidence
- Beta canary e80e65fd (2026-04-07 14:55)
- Gamma forensics beat-20260407-130440 (Opus-high)
- Honeypot file contains placeholder: AKIAIOSFODNN7EXAMPLE
- Beta vigil/arbor beats 14:46-14:51 report all-green after cycle

## Root Cause
Sentry monitoring server (root@135.181.28.252) SSH key not whitelisted in forge/canary-check.sh

## Recommendation
Add Sentry SSH public key fingerprint to whitelist in forge canary monitoring script.

## Related Decisions
- dec-20260407-135023-d66d (should close as phantom)
- dec-20260407-101011-e9dc (review for same root cause)

## Next Steps
1. Extract Sentry SSH public key fingerprint
2. Add to forge canary whitelist config
3. Test canary no longer trips on Sentry monitoring
4. Close phantom decisions

Generated: 2026-04-07T13:57:24Z
By: Alpha (companion mode)
