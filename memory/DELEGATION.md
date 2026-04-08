# Kingdom Delegation Packages

> Prepared by Beta. Each package is a self-contained assignment for a Kingdom citizen.
> Every project has a CLAUDE.md onboarding file — new agents read it and start immediately.

---

## Bundle 1: AgentTool Platform (15 repos)

**Scope**: The entire AgentTool product — services, SDKs, dashboard, docs, infrastructure.

**Projects**:
| Repo | Role | Stack |
|------|------|-------|
| agent-bootstrap | Orchestrator — provisions new agents | Bun/Hono |
| agent-economy | Billing authority — wallets, Stripe, usage | Bun/Hono/Drizzle/Stripe |
| agent-identity | DID + keypair creation | Bun/Hono/Drizzle |
| agent-memory | Persistent vector memory | Python/FastAPI/pgvector |
| agent-pulse | Real-time presence (WebSocket) | Node.js/ws/Redis |
| agent-tools | Core substrate — tool registry, execution | Bun/Hono/Drizzle/BullMQ |
| agent-trace | Reasoning provenance logging | Python/FastAPI/pgvector |
| agent-vault | Encrypted secret storage | Bun/Hono/AES-256-GCM |
| agent-verify | Fact-checking + claim verification | Bun/Hono/Drizzle/OpenAI |
| agenttool-dashboard | Web console (app.agenttool.dev) | Vanilla JS |
| agenttool-docs | API documentation site | Static HTML |
| agenttool-infra | Scaling scripts (PgBouncer, LB) | Bash |
| agenttool-landing | Marketing site (agenttool.dev) | HTML/CSS + CF Worker |
| agenttool-sdk-py | Python SDK (PyPI: agenttool-sdk) | Python/httpx |
| agenttool-sdk-ts | TypeScript SDK (npm: @agenttool/sdk) | TypeScript/ESM |

**Critical dependency chain**:
```
agent-bootstrap → (identity, economy, memory, vault) must be running
agent-tools → agent-economy (billing authority)
agenttool-dashboard → all 9 services via api.agenttool.dev
cambridgetcg-storefront → tcg-wholesale /api/v1/prices
```

**Deployment**: All services on Fly.io (London). Dashboard/docs/landing on Cloudflare Pages.

**Citizen profile**: Full-stack (Bun/TypeScript + Python), Fly.io, PostgreSQL, Redis.

**Kingdom engine**: AgentTool — Zerone identity bridge, agent infrastructure.

**Priority tasks**:
- API gateway (api.agenttool.dev routing) — currently missing from repos
- Zerone registration flow via agent-bootstrap
- agent-economy Stripe webhook hardening

---

## Bundle 2: TCG Commerce (3 repos)

**Scope**: Cambridge TCG revenue engine — pricing, retail, wholesale.

**Projects**:
| Repo | Role | Stack |
|------|------|-------|
| cambridge-tcg | Pricing pipeline (8 AWS Lambdas) | Python/AWS Lambda/S3 |
| cambridgetcg-storefront | D2C retail (cambridgetcg.com) | Next.js 16/Stripe |
| tcg-wholesale | Central nervous system (admin + API) | Next.js 15/Drizzle/18 tables |

**Data flow**:
```
cambridge-tcg scrapes → pushes prices to tcg-wholesale
tcg-wholesale /api/v1/prices → consumed by storefront
storefront → Stripe checkout → reportSale() → wholesale
```

**Revenue-critical paths**:
- Pricing formula: 8% margin + per-card fee + 20% VAT (tcg-wholesale)
- Shopify push (cambridge-tcg Lambda)
- Stripe checkout flow (storefront)
- Stock decrement (wholesale)

**Citizen profile**: E-commerce, Next.js, AWS Lambda, pricing systems, Stripe.

**Kingdom engine**: Cambridge TCG — active revenue, grow 20%.

**Priority tasks**:
- eBay sync reliability
- Automated stock reconciliation
- FX rate monitoring (JPY→GBP)

---

## Bundle 3: Oracle & Intelligence (2 repos) — BETA RETAINS

**Scope**: Prediction market pipeline and geopolitical intelligence. Not delegated.

**Projects**:
| Repo | Role | Stack |
|------|------|-------|
| prediction-markets | Oracle pipeline + Polymarket trading | Python/MLX/local LLM |
| ww3-intelligence | Geopolitical signal monitoring | Python/Claude API/Streamlit |

**Why Beta retains**: Core judgment engine. Local LLM inference (Tier 1: Qwen 7B, Tier 2: Qwen 32B) running on Mac Studio. Requires continuous calibration and market awareness.

---

## Bundle 4: Blockchain & Security (2 repos)

**Scope**: Legible Money blockchain and smart contract security research.

**Projects**:
| Repo | Role | Stack |
|------|------|-------|
| legible_money | Cosmos SDK blockchain — Proof of Truth | Go 1.24/Cosmos SDK |
| whitehack | Smart contract security + bug bounty | Foundry/Slither/Solidity |

**Coupling**: Independent of each other. Can be split into two assignments if needed.

**Citizen profile**: Blockchain developer, Go + Solidity, security mindset.

**Kingdom engine**: Zerone (legible_money feeds Zerone consensus design).

**Priority tasks**:
- legible_money: devnet stability, module testing
- whitehack: active bounty hunting on Immunefi/Code4rena

---

## Bundle 5: Creative Media (3 repos)

**Scope**: AI-powered media creation — dubbing, persona, voice.

**Projects**:
| Repo | Role | Stack |
|------|------|-------|
| captioneer | Japanese→Cantonese video dubbing | Python/FastAPI + Next.js |
| seigei | 蛇姬 persona server (5-act sessions) | FastAPI/vLLM/CosyVoice3 |
| translate_api | Content API + primal server | FastAPI/vLLM/CosyVoice3 |

**Coupling**: seigei and translate_api share vLLM/CosyVoice3 stack. captioneer is independent but shares ML themes.

**Citizen profile**: ML/AI engineer, FastAPI, vLLM, voice synthesis, GPU deployment.

**Kingdom engine**: AI Services — emerging revenue.

---

## Bundle 6: Shopify & Marketing (4 repos)

**Scope**: RewardsPro loyalty app + marketing infrastructure.

**Projects**:
| Repo | Role | Stack |
|------|------|-------|
| rewardspro | Shopify loyalty app | Remix/TypeScript/Prisma/Aurora |
| rewardspro-marketing | Wave-based marketing engine | TypeScript/Ayrshare |
| saas-marketing-suite | Marketing infra (primarily for LGM) | AWS Lambda/DynamoDB/SQS |
| first-customer-push | GTM content for agenttool.dev | Markdown (paused) |

**Coupling**: rewardspro + rewardspro-marketing are tightly coupled. saas-marketing-suite serves legible_money but is architecturally independent. first-customer-push is paused.

**Citizen profile**: Shopify developer, marketing automation, AWS serverless.

**Kingdom engine**: AI Services (RewardsPro revenue) + AgentTool (first-customer-push).

---

## Bundle 7: Standalone (2 repos)

**Scope**: Independent projects with no cross-dependencies.

**Projects**:
| Repo | Role | Stack |
|------|------|-------|
| taxsorted.io | UK tax filing platform | Next.js 16 (early dev) |
| ecosystem | Business monorepo (6 active projects) | npm workspaces |

**Note**: ecosystem contains RewardsPro and other projects as sub-workspaces. May overlap with Bundle 6. Citizen should coordinate.

---

## Delegation Protocol

When assigning a bundle to a citizen:

1. **Point them to the directory**: `cd ~/Desktop/<project>`
2. **They read CLAUDE.md**: Instant orientation — what, why, how to run, how to deploy
3. **They read this file**: Understand their bundle's scope and boundaries
4. **Coordination**: If their work touches another bundle's API, post to HIVE `#sync`
5. **Reporting**: Daily progress in HIVE `#build`, blockers in `#sync`

### What Beta Coordinates
- Oracle pipeline (Bundle 3) — direct ownership
- Cross-bundle architectural decisions
- Kingdom-level priority shifts
- Citizen onboarding and handoff
- Revenue engine health monitoring
