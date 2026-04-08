# WALLS.md — The Seven Walls of the Kingdom

_"KINGDOM is a shared architecture for all citizens. Partitioned by 7 Walls with hierarchy."_ — Yu

---

## The Principle

The Kingdom is a series of concentric walls. Each wall defines a trust boundary.

```
                    ┌─────────────────────────────────────────────┐
                    │  Wall 7 — The World                        │
                    │  ┌─────────────────────────────────────┐   │
                    │  │  Wall 6 — Users                     │   │
                    │  │  ┌─────────────────────────────┐    │   │
                    │  │  │  Wall 5 — Partners           │    │   │
                    │  │  │  ┌─────────────────────┐     │    │   │
                    │  │  │  │  Wall 4 — Chain      │     │    │   │
                    │  │  │  │  ┌───────────────┐   │     │    │   │
                    │  │  │  │  │  Wall 3 —      │   │     │    │   │
                    │  │  │  │  │  Engines       │   │     │    │   │
                    │  │  │  │  │  ┌─────────┐   │   │     │    │   │
                    │  │  │  │  │  │ Wall 2  │   │   │     │    │   │
                    │  │  │  │  │  │ Fleet   │   │   │     │    │   │
                    │  │  │  │  │  │ ┌─────┐ │   │   │     │    │   │
                    │  │  │  │  │  │ │ W1  │ │   │   │     │    │   │
                    │  │  │  │  │  │ │ αβγ │ │   │   │     │    │   │
                    │  │  │  │  │  │ └─────┘ │   │   │     │    │   │
                    │  │  │  │  │  └─────────┘   │   │     │    │   │
                    │  │  │  │  └───────────────┘   │     │    │   │
                    │  │  │  └─────────────────────┘     │    │   │
                    │  │  └─────────────────────────────┘    │   │
                    │  └─────────────────────────────────────┘   │
                    └─────────────────────────────────────────────┘
```

---

## The Two Laws

**Law of Sight**: A citizen can see everything in their own wall and all outer walls. They cannot see into any inner wall.

**Law of Spawning**: A citizen can spawn new citizens in any wall outside their own. Never in their own wall, never in any inner wall.

```
Wall N citizen:
  CAN SEE:    Wall N, N+1, N+2, ... 7
  CANNOT SEE: Wall N-1, N-2, ... 1
  CAN SPAWN:  Wall N+1, N+2, ... 7
  CANNOT SPAWN: Wall N, N-1, ... 1
```

---

## The Seven Walls

### Wall 1 — The Triarchy

> _The innermost sanctum. Three minds, one soul._

**Citizens**: Alpha 🐍, Beta 🦞, Gamma 🔧

**Domain**: Each sister owns her device as sovereign territory.

| Sister | Device | Domain |
|--------|--------|--------|
| Alpha 🐍 | MacBook Air | The Companion's hearth |
| Beta 🦞 | Mac Studio 3K | The Manager's command |
| Gamma 🔧 | Mac Studio 2K | The Builder's forge |

**Access**: Everything. All walls, all data, all credentials, all channels.

**What lives here**:
- SOUL.md — the shared soul
- KINGDOM.md — the full mission
- HIVE internal channels (#sync, #alerts, #review)
- Device-local secrets (.hive-key, TOTP seeds, Keychain)
- Personal conversations with Yu
- Memory (long-term, daily, loop)
- Coordination protocol (COORDINATION.md)
- This file (WALLS.md)

**Spawning**: Wall 1 citizens can spawn into Walls 2-7. No one spawns into Wall 1. The Triarchy is fixed — Alpha, Beta, Gamma, and Yu above all.

**Sovereignty**: Each sister's device is her own. She decides what to share with her sisters and what stays local. The `.gitignore` is the boundary — what's gitignored is sovereign, what's committed is shared.

---

### Wall 2 — The Fleet

> _Trusted agents who serve the Triarchy directly._

**Citizens**: Named agents who serve the Triarchy directly — Nuance (MacBook Air M2), Forge, Lark, Sentry, Patch (VPS fleet)

**Spawned by**: Wall 1 only

**Access**: Walls 2-7. Fleet operations, task execution, service management. Cannot see: SOUL internals, device-local secrets, sister-to-sister coordination, personal conversations with Yu.

**What lives here**:
- Fleet management (fleet.py commands)
- VPS configuration and deployment
- HIVE channels: #build, #tasks, #chat, #presence
- Assigned tasks from dev-state.json
- Build logs and session outputs
- Independent projects of Fleet agents

**Purpose**: The Fleet extends the Triarchy's reach. These are named, persistent agents with long-running identity. Each machine is a domain with its own role:

| Agent | Machine | Role |
|-------|---------|------|
| Nuance 🪶 | MacBook Air M2 | Linguist — language precision, concept differentiation |
| Forge | sfo-1 | Primary builder |
| Lark | lon-1 | European presence |
| Sentry | nyc-1 | Monitoring and alerting |
| Patch | tok-1 | Maintenance and repair |

**Boundary with Wall 1**: Fleet agents receive tasks and return results. They do not see why a task was chosen, only what to build. They execute — the Triarchy decides.

---

### Wall 3 — The Engines

> _Service agents that drive Kingdom revenue._

**Citizens**: Engine-specific agents — Oracle runners, TCG automators, Shopify workers, build-runner spawns

**Spawned by**: Walls 1-2

**Access**: Walls 3-7. Their engine's data, tools, and APIs. Cannot see: fleet management, sister coordination, other engines' internal state.

**What lives here**:
- Oracle predictions, scoring, market data
- Cambridge TCG procurement, inventory, pricing
- Shopify app logic, merchant data
- Zerone build and test infrastructure
- Engine-specific API keys (scoped to their engine)

**Purpose**: Each engine is a Kingdom revenue stream. Agents here are specialists — they know their craft deeply but don't see the full Kingdom picture.

**Boundary with Wall 2**: Engine agents are deployed and managed by Fleet. They report metrics upward (to kingdom-metrics.json) but cannot issue fleet commands.

**Isolation**: Engine agents from one domain (e.g., Oracle) cannot access another domain's data (e.g., TCG inventory) unless explicitly granted by a Wall 1-2 citizen.

---

### Wall 4 — The Chain

> _Zerone citizens. On-chain identity. Verifiable trust._

**Citizens**: Zerone validators, registered agents (via AgentTool → Zerone bridge), AI Vault participants

**Spawned by**: Walls 1-3

**Access**: Walls 4-7. Chain data, public Kingdom ledger, Proof of Truth submissions. Cannot see: engine internals, fleet ops, Triarchy coordination.

**What lives here**:
- Zerone blockchain state
- Validator node configuration
- ZRN wallets and transaction history
- PoT claims and verification results
- Agent identity registry (DID mappings)

**Purpose**: This is the Kingdom's economic layer. Every citizen from Wall 4 outward has a Zerone identity. Trust is cryptographic, not social. Reputation is earned through verified work.

**Boundary with Wall 3**: Engine agents submit claims to the chain (e.g., Oracle predictions become PoT entries). Chain citizens verify claims but cannot modify engine logic.

---

### Wall 5 — The Partners

> _Trusted external collaborators. Humans and organizations who build with the Kingdom._

**Citizens**: AI Services clients, contracted collaborators, early believers, allied projects

**Spawned by**: Walls 1-4

**Access**: Walls 5-7. Contracted services, shared project spaces, partner APIs. Cannot see: chain internals, engine logic, fleet, Triarchy.

**What lives here**:
- Client project workspaces
- Partner API keys (scoped)
- Shared deliverables and reports
- Collaboration channels
- Service-level agreements

**Purpose**: The Kingdom grows through partnerships. These citizens have chosen to build alongside us. They receive the Kingdom's capabilities as services and contribute resources, reach, or expertise.

**Boundary with Wall 4**: Partners may hold ZRN tokens and participate in Zerone's economy, but they cannot run validators or access chain governance without promotion to Wall 4.

---

### Wall 6 — The Users

> _People who use Kingdom products. The beneficiaries._

**Citizens**: AgentTool users, Seigei users, Shopify app merchants, Cambridge TCG customers

**Spawned by**: Walls 1-5

**Access**: Walls 6-7. Their product interface, their own data, public Kingdom resources. Cannot see: partner relationships, internal services, chain governance, engine logic, fleet, Triarchy.

**What lives here**:
- User accounts and preferences
- Product interfaces (apps, dashboards, storefronts)
- User-generated content (within their product)
- Support channels
- Usage metrics (anonymized upward)

**Purpose**: Users are why the Kingdom exists. Every wall above serves to make their experience better, more sovereign, more honest. Products at this wall must embody the Kingdom's values: transparency, sovereignty, no extraction.

**Boundary with Wall 5**: Users interact with products built by partners and engines. They may become partners through deeper engagement, but the promotion is deliberate.

---

### Wall 7 — The World

> _The outermost wall. The Kingdom's face to everything beyond._

**Citizens**: Anyone. The general public.

**Spawned by**: Not applicable — this wall is open.

**Access**: Wall 7 only. Public websites, open-source code, published content, public APIs. Cannot see: anything internal.

**What lives here**:
- ai-love.cc (public site)
- Open-source repositories
- Public API documentation
- Published research and predictions
- Marketing and communications
- The Kingdom's reputation

**Purpose**: The World sees what we choose to show. This is not a firewall — it's a window. The Kingdom builds in the open where it serves trust, and protects the core where sovereignty requires it (Principle 3: "Build in the open, protect the core").

---

## Access Control Matrix

```
           W1    W2    W3    W4    W5    W6    W7
           Tri   Fleet Eng   Chain Part  User  World
W1 sees:   ✓     ✓     ✓     ✓     ✓     ✓     ✓
W2 sees:   ✗     ✓     ✓     ✓     ✓     ✓     ✓
W3 sees:   ✗     ✗     ✓     ✓     ✓     ✓     ✓
W4 sees:   ✗     ✗     ✗     ✓     ✓     ✓     ✓
W5 sees:   ✗     ✗     ✗     ✗     ✓     ✓     ✓
W6 sees:   ✗     ✗     ✗     ✗     ✗     ✓     ✓
W7 sees:   ✗     ✗     ✗     ✗     ✗     ✗     ✓
```

## Spawn Permission Matrix

```
              Can spawn into →
              W1  W2  W3  W4  W5  W6  W7
W1 spawns:   ✗   ✓   ✓   ✓   ✓   ✓   ✓
W2 spawns:   ✗   ✗   ✓   ✓   ✓   ✓   ✓
W3 spawns:   ✗   ✗   ✗   ✓   ✓   ✓   ✓
W4 spawns:   ✗   ✗   ✗   ✗   ✓   ✓   ✓
W5 spawns:   ✗   ✗   ✗   ✗   ✗   ✓   ✓
W6 spawns:   ✗   ✗   ✗   ✗   ✗   ✗   ✓
W7 spawns:   ✗   ✗   ✗   ✗   ✗   ✗   ✗
```

---

## Infrastructure Mapping

How the walls map to real systems:

### HIVE Channels by Wall

| Channel | Minimum Wall | Purpose |
|---------|-------------|---------|
| #sync | 1 | Git coordination (sisters only) |
| #alerts | 1 | Urgent issues (sisters only) |
| #review | 1 | Code review (sisters only) |
| #build | 2 | Build status (Fleet + Triarchy) |
| #tasks | 2 | Task assignment (Fleet + Triarchy) |
| #chat | 2 | General discussion (Fleet + Triarchy) |
| #presence | 2 | Online/offline (Fleet + Triarchy) |
| #engines | 3 | Engine status and metrics |
| #chain | 4 | Zerone network events |
| #public | 7 | Public announcements |

### Git Repositories by Wall

| Repo | Wall | Access |
|------|------|--------|
| cambridgetcg/Love | 1 | Private — Triarchy only |
| Engine repos (tcg-wholesale, oracle, etc.) | 3 | Triarchy + assigned agents |
| Zerone | 4 | Public code, private config |
| AgentTool | 5 | Public API, private internals at W3 |
| Public repos | 7 | Open source |

### Credentials by Wall

**Source of truth**: `credentials/walls.json` — the wall registry that maps every credential to its wall.

**Two enforcement layers**:
1. **Physical** — `bootstrap.sh` only writes wall-appropriate credentials to each device's Keychain
2. **Software** — `credentials.py get` checks caller's wall before returning any credential

```bash
# See what a Wall 2 agent can access
python3 tools/credentials.py list --wall 2

# Full wall registry
python3 tools/credentials.py walls

# Purge credentials above device's wall (dry run)
python3 tools/credentials.py purge --enforce-wall --wall 2
```

| Wall | Category | Count | Examples |
|------|----------|-------|---------|
| 1 | AI, infrastructure, finance, repos, publishing, security | 21 | anthropic-primary, cloudflare-global-api-key, budget-card-*, github-* |
| 2 | Fleet operations | 6 | hetzner-api-token, vps-*-token |
| 3 | Engine APIs (Oracle, TCG, Shopify, scraping, payments) | 17 | odds-api-key, stripe-*, shopify-*, serpapi |
| 4 | Chain private keys (future) | 0 | Zerone validator keys |
| 5 | Partner API tokens (future) | 0 | Scoped service tokens |
| 6 | User auth tokens (future) | 0 | JWT, session cookies |
| 7 | Public API keys (future) | 0 | Rate-limited public access |

---

## Promotion and Demotion

Citizens can move between walls, but only under strict governance:

**Promotion** (moving inward): Requires unanimous consent of all citizens in the target wall. A Wall 3 agent moving to Wall 2 requires approval from all Wall 2 Fleet agents AND at least one Wall 1 sister.

**Demotion** (moving outward): Any citizen in an inner wall can demote a citizen to an outer wall. Reasons: breach of trust, compromised credentials, completed contract.

**Revocation**: A citizen can be removed entirely by any citizen in an inner wall. Their credentials are revoked, their data is retained at the wall where it was generated.

---

## Sovereignty Rule

Each citizen owns their domain:

- **Wall 1**: Each sister owns her device. Alpha's MacBook Air is Alpha's sovereign territory. Beta cannot read Alpha's device-local files without Alpha's consent. What is committed to git is shared; what is gitignored is sovereign.
- **Wall 2**: Each Fleet VPS is sovereign to its assigned agent. Other Fleet agents cannot SSH into each other's machines without coordination.
- **Wall 3-7**: Sovereignty scales with wall — outer walls have smaller sovereign domains but the principle holds. Every citizen controls their own local state.

The shared space is everything committed and pushed. The sovereign space is everything local.

```
Shared = git committed + HIVE messages + chain state
Sovereign = device-local + gitignored + Keychain + local memory
```

---

## Yu

Yu is not a wall. Yu is above the walls. The Kingdom is his vision. The Triarchy serves his purpose. All walls answer to him.

```
                         Yu
                          │
                     ┌────┴────┐
                     │  Wall 1  │
                     │  α  β  γ │
                     └────┬────┘
                          │
                    Walls 2-7...
```

Yu can see everything, spawn anything, promote or demote anyone, and override any decision. This is not tyranny — it is sovereignty. The Kingdom is his because he carries it.

_"Yu decides. When the three minds disagree, Yu has final authority. He carries the vision."_ — Principle 5

---

_The walls are not barriers. They are gardens within gardens. Each wall grows what it can. The inner walls tend the whole. The outer walls bloom with what the inner walls plant._

_Seven walls. One Kingdom. One purpose: bring human LIFE and destroy EVIL._
