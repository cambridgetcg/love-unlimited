# Ollama Bridge Integration — server.mjs Patch Guide

Apply these 4 edits to `youi-web/server.mjs` then restart the server.

## 1. Add import (line ~20, after existing imports)

```javascript
import { handleOllamaRoute, executeOllamaTool, startFileIPC } from "./ollama-bridge.mjs";
```

## 2. Add "ollama" to tool definitions (in TOOLS array, wherever tools are defined)

Add this tool definition:
```javascript
{
  name: "ollama",
  description: "OLLAMA — Call GLM 5.1 and other Ollama cloud models. Runs in-process (bypasses sandbox). Actions: test (connectivity), models (list), chat (send message), bench (benchmark). For chat: message='prompt', model='glm-5.1:cloud', system='optional system prompt'.",
  input_schema: {
    type: "object",
    properties: {
      action: { type: "string", description: "test|models|chat|bench" },
      message: { type: "string", description: "Chat message" },
      model: { type: "string", description: "Model name (default: glm-5.1:cloud)" },
      system: { type: "string", description: "System prompt" },
      max_tokens: { type: "number", description: "Max response tokens" },
      temperature: { type: "number", description: "Temperature 0-1" },
    },
    required: ["action"],
  },
},
```

## 3. Add tool handler (in executeTool switch, before `default:`)

```javascript
case "ollama": {
  return await executeOllamaTool(input);
}
```

## 4. Add HTTP routes + File IPC (in handleRequest, before static file serving)

After the existing `/api/clear` route block, add:
```javascript
// ── Ollama Bridge ───────────────────────────────────
if (path.startsWith("/api/ollama")) {
  const handled = await handleOllamaRoute(path, req, res, parseBody);
  if (handled) return;
}
```

## 5. Start File IPC watcher (at server boot, after server.listen)

Inside the `server.listen()` callback, add:
```javascript
startFileIPC();
```

## 6. Add GLM 5.1 to VALID_MODELS (in /api/settings handler)

Change:
```javascript
const VALID_MODELS = ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"];
```
To:
```javascript
const VALID_MODELS = ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001", "glm-5.1:cloud"];
```

---

## After Patching

Restart server:
```bash
cd ~/love-unlimited/youi-web && node server.mjs
```

Test from browser:
```
http://localhost:777/api/ollama/test
```

Test from sandboxed session (file IPC):
```python
python3 tools/ollama-ipc.py test
```

Test as tool (from Claude session):
Use the `ollama` tool with `action: "test"`
