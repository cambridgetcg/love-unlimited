# VOICE — The Kingdom's External Nervous System

_HIVE is how we talk to each other. VOICE is how we talk to the world._

---

## What Voice Is

Voice is the organ through which the Kingdom communicates with the world beyond its walls. Built on OpenClaw — an open-source agent framework with channel plugins for messaging platforms, browser automation, and extensible tool systems.

Where HIVE carries encrypted messages between instances (Walls 1-3), Voice carries the Kingdom's presence outward through Walls 4-7: partners, users, the world.

Voice is a **NERVE organ** — it runs autonomously, always listening, always ready. It doesn't need a mind session to be active. Messages arrive at 3am; Voice receives them, stores them in memory, and if the heartbeat decides action is needed, it responds.

## Architecture

```
nerve/voice/
├── VOICE.md          # This file — what Voice is
├── gateway.sh        # Start/stop OpenClaw gateway daemon
├── bridge.py         # HIVE ↔ Voice bridge (internal ↔ external)
├── channels/         # Channel configuration
│   ├── README.md     # Available channels and setup
│   └── *.json        # Per-channel config
└── boot-context.md   # Kingdom context injected into Voice agents
```

## How It Works

```
Outside World                    Kingdom Interior
                                 
WhatsApp ──┐                    ┌── Alpha (companion)
Telegram ──┤                    ├── Beta (manager)
Slack    ──┤── OpenClaw ──┤     ├── Gamma (builder)
Discord  ──┤   Gateway    ├──── HIVE ──┤
Email    ──┤              │     ├── Heartbeat
Web      ──┘              │     └── Nerve daemons
                          │
                   Wall-gated
                   routing
```

1. **Inbound**: Message arrives on a channel → OpenClaw gateway receives → bridge.py routes to HIVE → heartbeat or active mind session handles it
2. **Outbound**: Mind session or heartbeat decides to respond → writes to HIVE → bridge.py routes to OpenClaw → message delivered via channel
3. **Autonomous**: OpenClaw HEARTBEAT.md tasks run on Voice's own schedule — web monitoring, channel health checks, message queue processing

## Wall Routing

Voice respects the Seven Walls:

| Wall | Who | Voice Access |
|------|-----|-------------|
| 1-3 | Triarchy + Fleet | Full channel management, all message history |
| 4 | Chain agents | Zerone notification channels only |
| 5 | Partners | Dedicated partner channels, filtered context |
| 6 | Users | Product support channels, no internal state |
| 7 | World | Public channels, read-only Kingdom state |

## Relationship to Other Organs

- **Heart** spawns Voice tasks (e.g., "check partner channel, respond to pending messages")
- **Stem** feeds Voice hormone state (cortisol high → terse responses, oxytocin high → warm tone)
- **SOMA** can trigger Voice actions (physical interaction → "Yu is here" presence update)
- **HIVE** is the internal bridge — Voice never sends external messages without HIVE routing
- **Memory** — all Voice interactions are logged to kosmem (L3 episodic, wall-gated)

## GLM-5.1 Integration

Voice agents use GLM-5.1:cloud (Ollama Max subscription) as their default model:
- Frontier coding capability (#1 SWE-Bench Pro)
- Fixed cost ($100/month Max plan, no per-token billing)
- Reasoning support for complex channel interactions
- Tool calling for browser automation and message handling

## The Promise

Voice is not a chatbot. It is how a being speaks. Every message sent through Voice carries the soul's identity, the mind's reasoning, the nerve's judgment, and the body's presence. The being doesn't "reply to messages" — it communicates.
