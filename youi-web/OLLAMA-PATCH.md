# Ollama Bridge Integration Notes

Ollama access is already integrated into `youi-web/server.mjs`. There are two
supported paths:

1. Authenticated `/api/ollama/*` routes for the YOUI browser.
2. The `ollama` model tool when the active server capability policy permits it.

Both paths stay inside the YOUI server's authentication and authorization
boundary. They do not make Ollama public, bypass the session policy, or grant
provider access without the relevant scoped credential.

The browser route contract is method-specific:

- `POST /api/ollama/test` requires `models:diagnose`.
- `GET /api/ollama/models` and `POST /api/ollama/chat` require `models:use`.
- The model-facing `ollama` tool requires `models:use` to be advertised.
  Within that tool, `test` and `bench` additionally require
  `models:diagnose`; other actions require `models:use`.

Local and cloud are separate authority and privacy routes:

- A locally available model is labelled `LOCAL_MODEL:ollama`.
- Direct Ollama Cloud routing is labelled `REMOTE_MODEL:ollama-cloud` and
  requires `models:ollama-cloud`.
- Local-to-cloud fallback requires both `models:ollama-cloud` and
  `models:cloud-fallback`; neither the `safe` nor `developer` web profile
  includes those grants.
- Without the cloud-route grant, an unavailable local model is reported as
  `BLOCKED:ollama-cloud-capability-required` and no cloud request is made.
- A missing cloud credential fails before a provider request. Credentials are
  not returned by the Ollama routes.

The bridge propagates request cancellation through local detection, retry,
streaming, and provider calls. Cancellation stops retry; it is not a promise
that a remote provider has deleted data already received.

## Removed filesystem bridge

The former shared-temporary-directory request watcher was unauthenticated and
has been removed. A local filename is not proof of caller identity, and a
filesystem watcher cannot safely substitute for an authenticated protocol.

`tools/ollama-ipc.py` remains only as a fail-closed compatibility stub so old
automation receives a clear error instead of silently waiting or recreating
unsafe request files.

Do not add a shared-directory watcher back to the server. For local automation,
use an authenticated YOUI client that carries the browser session protections,
or use an Ollama/provider client with a narrowly scoped credential.
