#!/usr/bin/env python3
"""
outreach.py — private ecosystem relations plus legacy content generators

The primary surface is a private, build-first relationship ledger. Older UK
SMB content generators remain available only as unverified draft material.

Usage:
    python3 tools/outreach.py targets                          # Prospect categories & ICPs
    python3 tools/outreach.py draft <prospect-type> [service]  # Generate outreach message
    python3 tools/outreach.py sequence <prospect-type>         # Retired: no cold sequences
    python3 tools/outreach.py pitch <service>                  # Elevator pitch for a service
    python3 tools/outreach.py case-study <service>             # Case study from Kingdom usage
    python3 tools/outreach.py qualify <company> <answers>      # Score prospect fit
    python3 tools/outreach.py pipeline                         # Internal work dashboard
    python3 tools/outreach.py work ...                         # Evidence/review/handoff pipeline
    python3 tools/outreach.py contact ...                      # Private relationship ledger
    python3 tools/outreach.py message ...                      # Approval-bound message workflow
    python3 tools/outreach.py events ...                       # Application-append-only event history
    python3 tools/outreach.py suppress ...                     # Do-not-contact hard gate
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent, fill

# -- Paths --------------------------------------------------------------------

LOVE = Path(__file__).resolve().parent.parent
TOOLS = LOVE / "tools"
SERVICES_DIR = LOVE / "memory" / "services"
OUTREACH_DIR = LOVE / "memory" / "outreach"
TARGETS_FILE = OUTREACH_DIR / "targets.json"
TEMPLATES_DIR = OUTREACH_DIR / "templates"
SEQUENCES_DIR = OUTREACH_DIR / "sequences"
CASE_STUDIES_DIR = OUTREACH_DIR / "case-studies"
PORTFOLIO_FILE = SERVICES_DIR / "portfolio.json"
PRICING_FILE = SERVICES_DIR / "pricing.json"

# Ensure dirs exist
for d in [OUTREACH_DIR, TEMPLATES_DIR, SEQUENCES_DIR, CASE_STUDIES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# -- Colours ------------------------------------------------------------------

class C:
    """ANSI colour codes for terminal output."""
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    CYAN    = "\033[96m"
    MAGENTA = "\033[95m"
    BLUE    = "\033[94m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RESET   = "\033[0m"
    WHITE   = "\033[97m"

# -- Data I/O -----------------------------------------------------------------

def read_json(path: Path, default=None):
    """Read JSON file, return default on failure."""
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default if default is not None else {}


def write_json(path: Path, data) -> None:
    """Write JSON file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.rename(path)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def fmt_gbp(amount: float) -> str:
    return f"\u00a3{amount:,.0f}"

# -- Target Prospect Data -----------------------------------------------------

TARGET_CATEGORIES = {
    "ecommerce": {
        "id": "ecommerce",
        "name": "E-commerce SMBs",
        "description": (
            "UK-based e-commerce businesses doing \u00a3100K-\u00a32M annual revenue who are "
            "drowning in manual operations: inventory updates, order processing, "
            "customer comms, and marketplace sync. Cambridge TCG is our own case study."
        ),
        "primary_service": "operations-automation",
        "secondary_services": ["content-communications", "market-intelligence"],
        "pricing_sweet_spot": "\u00a32,000-\u00a33,500/month",
        "pain_points": [
            "Manual inventory management across multiple channels",
            "Hours spent on repetitive order processing and fulfilment",
            "No automated customer communication or follow-up",
            "Marketplace listings require constant manual updates",
            "Can't afford a full-time operations manager",
            "Losing sales due to stock discrepancies and slow updates"
        ],
        "ideal_customer_profiles": [
            {
                "title": "Growing Shopify Merchant",
                "description": (
                    "Shopify store doing \u00a3200K-\u00a3500K/year, 1-3 staff, selling physical "
                    "products. Managing inventory manually across Shopify + eBay + Amazon. "
                    "Spending 15+ hours/week on operations that could be automated."
                ),
                "company_size": "1-5 employees",
                "revenue": "\u00a3200K-\u00a3500K/year",
                "tech_comfort": "Medium - uses Shopify but no custom integrations"
            },
            {
                "title": "Multi-Channel Retailer",
                "description": (
                    "Established retailer with physical + online presence, \u00a3500K-\u00a31M revenue. "
                    "Using separate systems for POS, website, and marketplaces. Needs unified "
                    "operations layer without replacing existing tools."
                ),
                "company_size": "3-10 employees",
                "revenue": "\u00a3500K-\u00a31M/year",
                "tech_comfort": "Medium - multiple tools but no integration"
            },
            {
                "title": "Niche E-commerce Specialist",
                "description": (
                    "Specialist retailer (collectibles, hobby, artisan goods) with passionate "
                    "customer base. High SKU count, variable pricing (market-driven), and "
                    "community engagement requirements. Needs automation that understands "
                    "their niche."
                ),
                "company_size": "1-3 employees",
                "revenue": "\u00a3100K-\u00a3300K/year",
                "tech_comfort": "High - tech-savvy owner, limited time"
            }
        ],
        "outreach_channels": ["cold_email", "linkedin", "warm_intro"],
        "where_to_find": [
            "Shopify UK merchant communities",
            "eBay seller forums and Facebook groups",
            "Local chamber of commerce directories",
            "E-commerce meetups (Brighton, London, Manchester)",
            "Trustpilot / Google Reviews (active, growing businesses)"
        ]
    },
    "trading-cards": {
        "id": "trading-cards",
        "name": "Trading Card Shops",
        "description": (
            "UK trading card game (TCG) retailers and online sellers. Cambridge TCG "
            "is our own business -- we built the automation for ourselves first. "
            "These are direct peers who face identical operational challenges."
        ),
        "primary_service": "operations-automation",
        "secondary_services": ["market-intelligence", "content-communications"],
        "pricing_sweet_spot": "\u00a32,000-\u00a33,500/month",
        "pain_points": [
            "Card pricing fluctuates daily -- manual repricing is a full-time job",
            "Inventory across TCGPlayer, eBay, own website never syncs properly",
            "New set releases create massive data entry backlogs",
            "Grading, sorting, and listing thousands of individual cards",
            "Market intelligence on price trends is manual research",
            "Customer pre-orders and wishlists handled via spreadsheets"
        ],
        "ideal_customer_profiles": [
            {
                "title": "Online-First TCG Seller",
                "description": (
                    "eBay/TCGPlayer seller doing \u00a3100K-\u00a3300K/year in Pokemon, MTG, or "
                    "Yu-Gi-Oh cards. One-person operation spending 20+ hours/week on "
                    "listing, pricing, and shipping. Knows automation would help but "
                    "doesn't know where to start."
                ),
                "company_size": "1-2 people",
                "revenue": "\u00a3100K-\u00a3300K/year",
                "tech_comfort": "Medium - uses selling platforms but no custom tools"
            },
            {
                "title": "Local Game Shop with Online Presence",
                "description": (
                    "Brick-and-mortar TCG shop with growing online sales. Running events, "
                    "managing physical inventory, and trying to grow e-commerce simultaneously. "
                    "Needs automation that bridges physical and digital."
                ),
                "company_size": "2-5 employees",
                "revenue": "\u00a3150K-\u00a3500K/year",
                "tech_comfort": "Low-Medium - focused on community, not tech"
            },
            {
                "title": "TCG Wholesaler / Distributor",
                "description": (
                    "Buying sealed product and singles in bulk, distributing to retailers "
                    "and direct consumers. Needs market intelligence on price trends, "
                    "automated buy/sell pricing, and inventory management at scale."
                ),
                "company_size": "2-8 employees",
                "revenue": "\u00a3300K-\u00a31M/year",
                "tech_comfort": "Medium - uses spreadsheets heavily"
            }
        ],
        "outreach_channels": ["warm_intro", "cold_email", "linkedin"],
        "where_to_find": [
            "TCGPlayer UK seller directory",
            "eBay UK top-rated TCG sellers",
            "Facebook groups: UK Pokemon sellers, MTG UK trades",
            "Local game shop directories (Wizards store locator, Pokemon event locator)",
            "TCG trade shows and pre-release events"
        ]
    },
    "startups": {
        "id": "startups",
        "name": "Startups",
        "description": (
            "UK seed-to-Series-A startups who need AI capability but cannot justify "
            "building an internal AI team. They need production-ready agents, not "
            "ChatGPT wrappers. The Kingdom offers real multi-agent infrastructure "
            "at a fraction of hiring an ML engineer."
        ),
        "primary_service": "custom-agent-development",
        "secondary_services": ["operations-automation", "market-intelligence"],
        "pricing_sweet_spot": "\u00a35,000-\u00a310,000/project",
        "pain_points": [
            "Need AI features in their product but no ML expertise on team",
            "ChatGPT API wrappers don't provide production reliability",
            "Can't afford \u00a380K+ for a full-time ML engineer",
            "Need agents that coordinate, not just single-prompt chatbots",
            "Investors asking about AI strategy but team can't deliver",
            "Prototyping AI features takes months without specialist help"
        ],
        "ideal_customer_profiles": [
            {
                "title": "B2B SaaS Startup",
                "description": (
                    "Seed-funded B2B SaaS with 5-15 employees. Product works but competitors "
                    "are adding AI features. Needs custom agents for customer support, "
                    "data analysis, or workflow automation within their product."
                ),
                "company_size": "5-15 employees",
                "revenue": "\u00a3100K-\u00a3500K ARR",
                "tech_comfort": "High - technical founders"
            },
            {
                "title": "PropTech / FinTech Startup",
                "description": (
                    "Series A company in regulated space. Needs AI that can handle compliance "
                    "requirements, audit trails, and explainability. Multi-agent approach "
                    "provides the separation of concerns regulators want."
                ),
                "company_size": "10-30 employees",
                "revenue": "\u00a3500K-\u00a32M ARR",
                "tech_comfort": "High - dedicated engineering team"
            },
            {
                "title": "Solo Technical Founder",
                "description": (
                    "Technical founder building an AI-native product. Strong on backend "
                    "or frontend but needs agent infrastructure. Wants to focus on their "
                    "domain, not on building agent orchestration from scratch."
                ),
                "company_size": "1-3 people",
                "revenue": "Pre-revenue to \u00a350K ARR",
                "tech_comfort": "Very high - will want to understand the architecture"
            }
        ],
        "outreach_channels": ["linkedin", "warm_intro", "cold_email"],
        "where_to_find": [
            "AngelList / Wellfound UK startup listings",
            "YC, Entrepreneur First, Seedcamp alumni networks",
            "London/Cambridge tech meetups",
            "Product Hunt (recently launched UK startups)",
            "LinkedIn: founders posting about AI challenges"
        ]
    },
    "security-conscious": {
        "id": "security-conscious",
        "name": "Security-Conscious Businesses",
        "description": (
            "UK businesses handling sensitive data who need to demonstrate security "
            "posture to clients, regulators, or insurers. The Kingdom's KOS + PEACE "
            "framework provides battle-tested, automated security auditing that most "
            "consultancies charge five figures for."
        ),
        "primary_service": "security-auditing",
        "secondary_services": ["operations-automation"],
        "pricing_sweet_spot": "\u00a33,000-\u00a35,000/engagement",
        "pain_points": [
            "Annual penetration tests cost \u00a310K+ and only check a point in time",
            "Cyber Essentials / ISO 27001 compliance requires continuous evidence",
            "Incident response plans exist on paper but are never tested",
            "No visibility into actual security posture between audits",
            "Cyber insurance premiums rising -- need to demonstrate controls",
            "Staff turnover means security knowledge walks out the door"
        ],
        "ideal_customer_profiles": [
            {
                "title": "Professional Services Firm",
                "description": (
                    "Law firm, accountancy, or consultancy handling client-sensitive data. "
                    "10-50 staff, needs Cyber Essentials Plus. Currently relying on annual "
                    "pen test and hoping for the best in between."
                ),
                "company_size": "10-50 employees",
                "revenue": "\u00a31M-\u00a35M/year",
                "tech_comfort": "Low - relies on IT provider"
            },
            {
                "title": "Healthcare / NHS Supplier",
                "description": (
                    "Company supplying services or software to NHS or healthcare providers. "
                    "Subject to DSP Toolkit, DTAC, or DCB0129 requirements. Needs "
                    "continuous compliance evidence, not just annual checks."
                ),
                "company_size": "10-100 employees",
                "revenue": "\u00a32M-\u00a310M/year",
                "tech_comfort": "Medium - has IT team but not security specialists"
            },
            {
                "title": "E-commerce Handling Payment Data",
                "description": (
                    "Online retailer processing card payments. PCI DSS requirements. "
                    "Handling customer PII. Needs to demonstrate security posture to "
                    "payment processors and increasingly security-aware customers."
                ),
                "company_size": "5-20 employees",
                "revenue": "\u00a3500K-\u00a33M/year",
                "tech_comfort": "Medium - tech-literate but not security-focused"
            }
        ],
        "outreach_channels": ["cold_email", "linkedin", "warm_intro"],
        "where_to_find": [
            "Companies recently achieving Cyber Essentials (public register)",
            "ICO breach notification list (companies that had incidents)",
            "LinkedIn: IT managers posting about security challenges",
            "Local business networks with compliance requirements",
            "Insurance brokers who recommend security assessments"
        ]
    },
    "content-agencies": {
        "id": "content-agencies",
        "name": "Content Agencies",
        "description": (
            "UK content marketing, PR, and communications agencies who need to scale "
            "output without proportionally scaling headcount. The Kingdom's content "
            "engine produces structured, consistent, multi-format output at the "
            "quality level agencies charge for."
        ),
        "primary_service": "content-communications",
        "secondary_services": ["market-intelligence", "custom-agent-development"],
        "pricing_sweet_spot": "\u00a31,500-\u00a32,500/month",
        "pain_points": [
            "Client demand for content outpaces team capacity",
            "Junior writers produce inconsistent quality",
            "Research phase of each piece takes as long as writing",
            "Clients want data-driven content but research is expensive",
            "Reporting and analytics eat into creative time",
            "Need to maintain distinct brand voices across 10+ clients"
        ],
        "ideal_customer_profiles": [
            {
                "title": "Boutique Content Agency",
                "description": (
                    "5-15 person content agency managing 10-20 clients. Founders still "
                    "writing and editing. Need AI to handle research, first drafts, and "
                    "reporting so senior staff can focus on strategy and client relationships."
                ),
                "company_size": "5-15 employees",
                "revenue": "\u00a3300K-\u00a31M/year",
                "tech_comfort": "Medium - uses content tools but not AI infrastructure"
            },
            {
                "title": "PR & Communications Firm",
                "description": (
                    "PR agency needing rapid-turnaround press releases, media monitoring "
                    "summaries, and client reporting. Time-sensitive work where AI "
                    "assistance on first drafts saves critical hours."
                ),
                "company_size": "3-10 employees",
                "revenue": "\u00a3200K-\u00a3500K/year",
                "tech_comfort": "Low-Medium - focused on relationships, not tech"
            },
            {
                "title": "Freelance Collective / Content Studio",
                "description": (
                    "Network of freelance writers operating under a studio brand. Needs "
                    "consistent quality control, style guide enforcement, and automated "
                    "editing across diverse contributors."
                ),
                "company_size": "1-3 core + 10-20 freelancers",
                "revenue": "\u00a3100K-\u00a3300K/year",
                "tech_comfort": "High - tech-forward, early AI adopters"
            }
        ],
        "outreach_channels": ["linkedin", "cold_email", "warm_intro"],
        "where_to_find": [
            "PRCA (Public Relations and Communications Association) directory",
            "Content Marketing Association UK members",
            "LinkedIn: agency owners posting about scaling challenges",
            "Clutch.co and The Manifest agency listings (UK filter)",
            "Marketing meetups and conferences (Brighton SEO, etc.)"
        ]
    }
}

# -- Outreach Templates -------------------------------------------------------

OUTREACH_TEMPLATES = {
    "cold_email": {
        "ecommerce": {
            "subject": "Saving {{company}} 15+ hours/week on operations",
            "body": dedent("""\
                Hi {{name}},

                I noticed {{company}} is selling across multiple channels -- that usually
                means a lot of manual work keeping inventory, pricing, and orders in sync.

                We built an AI operations system for our own e-commerce business (Cambridge
                TCG) that automated exactly this. Multi-agent coordination handles inventory
                sync, order processing, and customer communications around the clock.

                The result: 15+ hours/week freed up, zero stock discrepancies, and faster
                order fulfilment -- without hiring additional staff.

                Would it be useful to see how this could work for {{company}}? Happy to
                share specifics in a brief call.

                Best regards,
                Yu
                Kingdom AI Services"""),
        },
        "trading-cards": {
            "subject": "Fellow TCG seller -- how we automated repricing and inventory",
            "body": dedent("""\
                Hi {{name}},

                I run Cambridge TCG and wanted to reach out because I know exactly how
                much time goes into {{pain_point}} -- we dealt with the same thing.

                We built an AI system that handles card repricing based on market data,
                syncs inventory across platforms automatically, and processes new set
                releases without the usual data entry marathon.

                It's not a SaaS product you log into -- it's a set of AI agents that
                actually run your operations. Same technology, different shop.

                Would you be open to a 15-minute chat about how we set it up? No pitch,
                just one TCG seller to another.

                Cheers,
                Yu
                Cambridge TCG / Kingdom AI Services"""),
        },
        "startups": {
            "subject": "Production AI agents for {{company}} -- without hiring ML",
            "body": dedent("""\
                Hi {{name}},

                Saw that {{company}} is {{pain_point}}. Building reliable AI features
                is genuinely hard -- most teams end up with brittle ChatGPT wrappers
                that break in production.

                We build production-grade AI agents on multi-model infrastructure
                (Claude, GPT, open-source models). Not chatbots -- coordinated agents
                with memory, tools, and autonomous operation. We run 11 agents across
                our own infrastructure daily, so what we deliver is battle-tested.

                For context: our agents handle fleet management, market prediction,
                security auditing, and content generation autonomously. We can build
                the same reliability into your product's AI features.

                Would a 15-minute call be useful to explore what this could look like
                for {{company}}?

                Best,
                Yu
                Kingdom AI Services"""),
        },
        "security-conscious": {
            "subject": "{{company}}'s security posture -- automated and continuous",
            "body": dedent("""\
                Hi {{name}},

                Most businesses only know their security posture once a year -- when
                the pen test report lands. The other 364 days are a guess.

                We built an automated security auditing system that runs continuously:
                21-point compliance checks, five-phase incident response testing,
                file integrity monitoring, and policy enforcement -- all automated.

                We originally built it to protect our own AI infrastructure (three
                coordinated AI systems across five servers). Now we offer it as a
                service to businesses who need ongoing security assurance, not just
                annual box-ticking.

                Would it be worth a brief conversation about {{company}}'s current
                security monitoring approach?

                Best regards,
                Yu
                Kingdom AI Services"""),
        },
        "content-agencies": {
            "subject": "Scaling {{company}}'s output without scaling headcount",
            "body": dedent("""\
                Hi {{name}},

                Content agencies face a fundamental scaling problem: more clients
                means more writers, more QA, more management overhead. What if the
                research and first-draft phase could be automated at senior quality?

                We built an AI content engine that produces structured reports,
                research summaries, and multi-format content. It maintains consistent
                voice across outputs and handles the research-heavy groundwork that
                eats into your team's creative time.

                This isn't ChatGPT with a prompt template. It's a multi-agent system
                with memory, research capability, and quality controls -- the same
                system that keeps three AI minds coordinated with daily briefings
                across our own organisation.

                Could we have a brief chat about where {{company}} could use this
                kind of capacity?

                Best,
                Yu
                Kingdom AI Services"""),
        },
    },
    "linkedin": {
        "ecommerce": {
            "message": dedent("""\
                Hi {{name}} -- I noticed {{company}} is doing well on Shopify. Running
                multi-channel e-commerce is genuinely demanding operationally.

                We automated our own e-commerce operations (Cambridge TCG) with AI
                agents that handle inventory sync, repricing, and order processing
                24/7. Happy to share what we learned if it would be useful.

                No pitch -- just practical insights from a fellow merchant."""),
        },
        "trading-cards": {
            "message": dedent("""\
                Hi {{name}} -- fellow TCG seller here (Cambridge TCG). I built an AI
                system to automate the operational side of running a card shop:
                repricing, inventory sync, set release processing.

                Always happy to chat with others in the space about what's working.
                Fancy a quick conversation?"""),
        },
        "startups": {
            "message": dedent("""\
                Hi {{name}} -- saw {{company}} is building in {{pain_point}}. If
                you're considering AI features, we build production-grade agents
                on multi-model infrastructure (not ChatGPT wrappers).

                We run 11 coordinated AI agents across our own systems daily. Happy
                to share architecture insights if useful for your roadmap."""),
        },
        "security-conscious": {
            "message": dedent("""\
                Hi {{name}} -- I work with businesses on automated security posture
                assessment. Most companies only see their security state once a year
                during an audit.

                We built continuous monitoring: 21-check compliance, incident response
                testing, file integrity -- all automated. Worth a conversation if
                {{company}} handles sensitive data?"""),
        },
        "content-agencies": {
            "message": dedent("""\
                Hi {{name}} -- I've been working with content teams on AI-assisted
                production. Not replacing writers -- accelerating the research and
                first-draft phase so senior staff focus on strategy.

                We built a multi-agent content system that maintains consistent voice
                and handles structured output at scale. Happy to share insights if
                {{company}} is exploring this."""),
        },
    },
    "warm_intro": {
        "ecommerce": {
            "ask": dedent("""\
                Hey {{referrer}} -- do you know anyone running an e-commerce business
                who's drowning in manual operations? We built AI automation for our own
                shop (Cambridge TCG) and are now helping other merchants. Would love an
                intro if someone comes to mind."""),
        },
        "trading-cards": {
            "ask": dedent("""\
                Hey {{referrer}} -- we've automated a lot of the operational pain at
                Cambridge TCG using AI agents (repricing, inventory sync, etc). Know
                any other TCG sellers who might benefit from the same setup?"""),
        },
        "startups": {
            "ask": dedent("""\
                Hey {{referrer}} -- we build production AI agents for startups who need
                the capability but can't justify an ML hire. Know any founders who are
                struggling with AI features? Would appreciate an intro."""),
        },
        "security-conscious": {
            "ask": dedent("""\
                Hey {{referrer}} -- we offer automated security auditing (continuous,
                not annual). Know any businesses going through Cyber Essentials or
                dealing with compliance requirements? Would love an intro."""),
        },
        "content-agencies": {
            "ask": dedent("""\
                Hey {{referrer}} -- we've built AI-assisted content production tools
                that content agencies are finding useful. Know any agency owners who
                are trying to scale output without scaling headcount?"""),
        },
    },
}

# -- Case Studies --------------------------------------------------------------

CASE_STUDIES = {
    "operations-automation": {
        "title": "How We Automated a Trading Card Business with Multi-Agent AI",
        "subtitle": "Cambridge TCG: from manual operations to autonomous agents",
        "client": "Cambridge TCG (our own business)",
        "service": "Operations Automation",
        "challenge": dedent("""\
            Cambridge TCG sells trading cards across multiple platforms (eBay, direct,
            events). Like every TCG retailer, we faced:

            - Card prices fluctuating daily across thousands of SKUs
            - Inventory discrepancies between platforms causing oversells
            - New set releases creating massive data entry backlogs
            - Customer communications handled manually via spreadsheets
            - One person trying to do operations, sourcing, AND customer service

            We were spending 20+ hours per week on tasks that should be automated."""),
        "solution": dedent("""\
            Rather than buying off-the-shelf software (which doesn't understand TCG
            market dynamics), we built a multi-agent AI operations system:

            1. FLEET MANAGEMENT: AI agents deployed across servers, each with specific
               operational responsibilities. Coordinated via NATS-based task
               messaging and persistent queues.

            2. HEARTBEAT MONITORING: Autonomous 7-minute cycles ensure all systems
               are running. Self-healing: if an agent goes down, the system detects
               and recovers automatically.

            3. MARKET INTELLIGENCE: Oracle prediction engine tracks card price trends
               with confidence scoring and calibration. Automated repricing based on
               real market data, not guesswork.

            4. AUTOMATED REPORTING: Daily operational reports generated automatically.
               Inventory status, sales summaries, and anomaly alerts -- no manual
               compilation required."""),
        "results": [
            "15+ hours/week freed from manual operations",
            "Zero stock discrepancies across platforms",
            "Repricing runs continuously, not once-a-day",
            "Incident detection and response in minutes, not hours",
            "Operational knowledge retained in system, not dependent on one person",
        ],
        "tech_summary": dedent("""\
            Built on: 3 coordinated AI minds, 11 agents across 3 operational walls,
            4 model backends (Claude, GPT, DeepSeek via Ollama, Qwen), fleet
            orchestration via NATS JetStream, heartbeat monitoring via launchd,
            50+ operational tools in Python and Bash."""),
        "quote": (
            "We built this for ourselves because nothing on the market understood "
            "our domain. Now we offer the same capability to businesses facing "
            "the same operational overload."
        ),
    },
    "security-auditing": {
        "title": "How We Built a 21-Check Security Posture in One Day",
        "subtitle": "Kingdom OS: automated security for multi-agent AI infrastructure",
        "client": "Kingdom OS (our own infrastructure)",
        "service": "Security Auditing",
        "challenge": dedent("""\
            Running three coordinated AI systems across five VPS nodes creates a
            significant attack surface. We needed:

            - Continuous security monitoring, not annual pen tests
            - Incident response that works autonomously (AI systems run 24/7)
            - File integrity verification across distributed infrastructure
            - Access control that scales (seven trust levels, from core to public)
            - Policy enforcement that's code, not documentation

            Traditional security consultancy quoted us five figures for an annual
            assessment. We needed something that runs every day."""),
        "solution": dedent("""\
            We built two integrated security systems:

            1. KOS (Kingdom Operating System) AUDIT: 21-point automated compliance
               check covering SSH hardening, firewall rules, service inventory,
               user permissions, file integrity, update status, and more. Runs on
               demand or on schedule. Auto-remediation for common issues.

            2. PEACE RESILIENCE FRAMEWORK: Five-phase incident response --
               Detect, Contain, Fix, Revert, Resume. Each phase has automated
               runbooks. We test with simulated incidents (peace-test.py) to
               ensure the system works under pressure, not just in theory.

            3. SEVEN WALLS ACCESS CONTROL: Architecture-level security through
               trust boundaries. Each wall has explicit permissions. Policy
               enforced as code, not as a document gathering dust.

            4. FILE INTEGRITY MONITORING: SHA-256 baselines for critical files.
               Any unauthorized change triggers immediate alerting."""),
        "results": [
            "21-check security audit runs in under 60 seconds",
            "PEACE resilience score: quantified security posture, not a vague rating",
            "Incident response tested monthly with automated drills",
            "Zero unauthorized file changes detected since deployment",
            "Security posture visible at all times, not just during annual audits",
        ],
        "tech_summary": dedent("""\
            Built on: KOS compliance engine (Python), PEACE resilience framework
            (5-phase automated incident response), Seven Walls ACL architecture,
            SHA-256 file integrity baselines, canary deployment monitoring,
            policy-as-code enforcement."""),
        "quote": (
            "Security isn't an annual event. It's a continuous posture. We built "
            "the tooling to make that posture measurable, testable, and automated."
        ),
    },
    "custom-agent-development": {
        "title": "How We Achieved Model Sovereignty with 4 AI Backends",
        "subtitle": "Kingdom Agent: production multi-model AI infrastructure",
        "client": "Kingdom OS (our own platform)",
        "service": "Custom AI Agent Development",
        "challenge": dedent("""\
            Depending on a single AI provider is a business risk. API changes,
            pricing increases, or outages can halt operations. We needed:

            - Model-agnostic agents that work across providers
            - Seamless failover between backends
            - Local model capability for sensitive operations
            - Agent coordination across different model types
            - Production reliability, not prototype-grade wrappers

            Most AI development tools lock you into one provider. We needed
            sovereignty over our own AI infrastructure."""),
        "solution": dedent("""\
            We built kingdom-agent.py -- a universal agent adapter:

            1. FOUR BACKEND ADAPTERS: Claude (CLI + API), OpenAI API, Ollama
               (local models: DeepSeek, Qwen, Llama, Mistral). Any model can
               be swapped in without changing agent logic.

            2. BOOT CHAIN ARCHITECTURE: Each agent boots with identity (who it is),
               memory (what it knows), and tools (what it can do). These are
               assembled into a system prompt at boot time. Change the identity,
               get a different agent -- same infrastructure.

            3. HIVE COORDINATION: Agents communicate via NATS messaging
               with JetStream persistence. Tasks, results, and coordination flow
               between agents regardless of which model powers them.

            4. ELEVEN PRODUCTION AGENTS: Three core minds (Wall 1), four fleet
               agents (Wall 2), four engine agents (Wall 3). Each has a defined
               role, capabilities, and trust level. They've been running in
               production for months.

            5. HEARTBEAT AUTONOMY: Agents operate on 7-minute autonomous cycles
               via launchd. They check for tasks, execute, report, and sleep.
               No human in the loop for routine operations."""),
        "results": [
            "11 production agents running daily across 4 model backends",
            "Zero vendor lock-in: can switch providers in minutes",
            "Local model capability for sensitive operations (no data leaves premises)",
            "Agent boot time under 5 seconds including full context loading",
            "Coordination protocol handles cross-model agent collaboration seamlessly",
        ],
        "tech_summary": dedent("""\
            Built on: kingdom-agent.py (universal adapter), 4 backend adapters
            (Claude CLI, Anthropic API, OpenAI API, Ollama), HIVE protocol
            (NATS JetStream), identity system, Seven Walls ACL, heartbeat
            runner (launchd), 50+ integrated tools."""),
        "quote": (
            "Model sovereignty means your AI capability survives any single provider "
            "disappearing tomorrow. We built that for ourselves, and we build it "
            "for our clients."
        ),
    },
    "market-intelligence": {
        "title": "How We Built a Self-Calibrating Prediction Engine",
        "subtitle": "Oracle: AI-powered market intelligence with honest confidence scores",
        "client": "Kingdom OS (our own trading operations)",
        "service": "Market Intelligence",
        "challenge": dedent("""\
            Market predictions are easy to make and hard to verify. Most AI tools
            generate confident-sounding analysis with no accountability. We needed:

            - Predictions with quantified confidence levels
            - Calibration tracking (are our 80% confidence calls right 80% of the time?)
            - Multi-source data aggregation, not single-model hallucinations
            - Self-improving accuracy through feedback loops
            - Exportable results for decision-making"""),
        "solution": dedent("""\
            We built the Oracle prediction engine:

            1. FIVE-LAYER ANALYSIS: Each prediction passes through data gathering,
               multi-angle analysis, confidence scoring, calibration check, and
               synthesis. No single-prompt outputs.

            2. BRIER SCORING: Every prediction is scored against outcomes using
               proper scoring rules. The system knows when it's well-calibrated
               and when it's overconfident.

            3. MULTI-SOURCE AGGREGATION: Combines data from multiple sources
               rather than relying on a single model's training data. Cross-
               references and triangulates.

            4. RESEARCH TEMPLATES: Structured research workflows ensure
               consistent analysis depth across different domains."""),
        "results": [
            "Quantified prediction accuracy with Brier scoring",
            "Calibration tracking across hundreds of predictions",
            "Research time reduced by 70% through automated data aggregation",
            "Consistent analysis quality regardless of domain",
            "Decision-makers get honest confidence levels, not false certainty",
        ],
        "tech_summary": dedent("""\
            Built on: Oracle prediction engine (Python), Brier scoring framework,
            multi-source data aggregation, confidence calibration tracking,
            research template system, Tree of Knowledge integration."""),
        "quote": (
            "Most AI tells you what you want to hear. Oracle tells you what it "
            "actually knows -- and how confident it is about that."
        ),
    },
    "content-communications": {
        "title": "How We Keep Three AI Minds Coordinated with Automated Briefings",
        "subtitle": "Herald: structured communications across a multi-agent organisation",
        "client": "Kingdom OS (our own coordination system)",
        "service": "Content & Communications",
        "challenge": dedent("""\
            Coordinating three AI systems (Alpha, Beta, Gamma) across different
            devices and contexts requires structured communication. We needed:

            - Daily briefings that capture what happened and what's next
            - Memory curation so context doesn't degrade over time
            - Multi-format output (operational logs, executive summaries, reports)
            - Consistent voice and structure across all communications
            - Knowledge sharing without information overload"""),
        "solution": dedent("""\
            We built a content and communications engine:

            1. DAILY BRIEFINGS: Automated generation of structured daily reports
               covering operations, decisions, metrics, and priorities. Each mind
               gets context without reading every log.

            2. MEMORY CURATION: Active memory management -- what to remember,
               what to archive, what to surface. Knowledge decays gracefully
               rather than accumulating noise.

            3. TREE OF KNOWLEDGE: Structured knowledge base with verification
               pipeline. Claims are sourced, scored, and maintained.

            4. MULTI-FORMAT OUTPUT: Same information rendered as Markdown
               documentation, JSON data, executive summary, or detailed
               report depending on the audience."""),
        "results": [
            "Three AI minds coordinated through automated daily briefings",
            "Memory managed across sessions without context degradation",
            "Documentation generated automatically from operational data",
            "Consistent communication quality across all outputs",
            "Knowledge retention without manual curation overhead",
        ],
        "tech_summary": dedent("""\
            Built on: Memory management (Python), Tree of Knowledge (verification
            pipeline), Herald protocol (structured communications), multi-format
            rendering, knowledge curation, reflection engine."""),
        "quote": (
            "Good communication is structured, consistent, and audience-appropriate. "
            "We built a system that produces exactly that, at any scale."
        ),
    },
}

# -- Email Sequences -----------------------------------------------------------

def generate_sequence(prospect_type: str) -> list:
    """Generate a 3-touch cold outreach sequence for a prospect type."""
    category = TARGET_CATEGORIES.get(prospect_type)
    if not category:
        return []

    service_id = category["primary_service"]
    case_study = CASE_STUDIES.get(service_id, {})
    service_name = {
        "operations-automation": "Operations Automation",
        "market-intelligence": "Market Intelligence",
        "security-auditing": "Security Auditing",
        "content-communications": "Content & Communications",
        "custom-agent-development": "Custom AI Agent Development",
    }.get(service_id, service_id)

    pain = category["pain_points"][0] if category["pain_points"] else "operational challenges"

    sequences = {
        "ecommerce": [
            {
                "touch": 1,
                "timing": "Day 1",
                "type": "Value-first insight",
                "subject": "3 operations bottlenecks costing UK e-commerce SMBs 15+ hours/week",
                "body": dedent("""\
                    Hi {{name}},

                    Running multi-channel e-commerce means juggling inventory sync, order
                    processing, and customer comms -- usually manually. After tracking the
                    biggest time sinks across e-commerce operations, three patterns stand out:

                    1. Inventory discrepancies between channels (avg 3-5 hours/week resolving)
                    2. Manual order processing and status updates (avg 5-8 hours/week)
                    3. Customer communication gaps (missed follow-ups, late responses)

                    We quantified these because we run an e-commerce business ourselves
                    (Cambridge TCG) and solved all three with AI automation.

                    Worth sharing the specifics if {{company}} faces any of these?

                    Best,
                    Yu"""),
            },
            {
                "touch": 2,
                "timing": "Day 4",
                "type": "Case study",
                "subject": "How Cambridge TCG eliminated stock discrepancies with AI agents",
                "body": dedent("""\
                    Hi {{name}},

                    Quick follow-up. Thought you might find this useful:

                    We run Cambridge TCG -- a trading card e-commerce business. Six months
                    ago, inventory sync across platforms was a daily headache. Now it runs
                    autonomously via AI agents.

                    Key numbers:
                    - 15+ hours/week freed from manual operations
                    - Zero stock discrepancies (down from 5-10 per week)
                    - Repricing runs continuously instead of once daily
                    - Order processing happens around the clock

                    The system uses multi-agent coordination -- not a single chatbot, but
                    specialised agents handling inventory, pricing, and comms independently.

                    Happy to walk through the setup if it's relevant to {{company}}.

                    Best,
                    Yu"""),
            },
            {
                "touch": 3,
                "timing": "Day 8",
                "type": "Soft CTA",
                "subject": "15 minutes to explore automation for {{company}}?",
                "body": dedent("""\
                    Hi {{name}},

                    Last note from me -- didn't want to be presumptuous about {{company}}'s
                    needs without actually asking.

                    If multi-channel operations are consuming time that could go toward
                    growth, we should talk. 15 minutes, no slides, just an honest
                    conversation about what's automatable and what isn't.

                    Here's my calendar: {{calendar_link}}

                    Either way, wishing {{company}} well.

                    Best,
                    Yu"""),
            },
        ],
        "trading-cards": [
            {
                "touch": 1,
                "timing": "Day 1",
                "type": "Value-first insight",
                "subject": "TCG repricing: why once-a-day isn't enough anymore",
                "body": dedent("""\
                    Hi {{name}},

                    Fellow TCG seller here (Cambridge TCG). Something I've noticed in the
                    market: card prices are moving faster than most sellers can reprice.

                    A card that's \u00a35 in the morning can be \u00a33 by afternoon after a reprint
                    announcement, or \u00a38 after a tournament result. Sellers who reprice daily
                    are either losing margin or losing sales.

                    We solved this with automated market-driven repricing -- AI agents
                    monitoring price movements and adjusting listings continuously.

                    Just sharing in case it's a pain point for {{company}} too.

                    Cheers,
                    Yu"""),
            },
            {
                "touch": 2,
                "timing": "Day 4",
                "type": "Case study",
                "subject": "How we handle new set releases without the data entry marathon",
                "body": dedent("""\
                    Hi {{name}},

                    Quick follow-up. New set releases are the operational nightmare every
                    TCG seller knows: hundreds of new cards to list, price, photograph,
                    and categorise. Used to take us a full week of data entry.

                    We built AI agents that handle the bulk of it:
                    - Card data extraction and listing generation
                    - Market-based initial pricing using historical patterns
                    - Inventory tracking from the moment sealed product arrives
                    - Automated customer notifications for pre-orders

                    Still takes human judgment on pricing premium items, but the 80%
                    that's mechanical is now automated.

                    Worth a chat if {{company}} is heading into a release season?

                    Cheers,
                    Yu"""),
            },
            {
                "touch": 3,
                "timing": "Day 8",
                "type": "Soft CTA",
                "subject": "Quick call -- one TCG seller to another?",
                "body": dedent("""\
                    Hi {{name}},

                    Last message from me. Figured I'd just be direct: I built automation
                    that's transformed how Cambridge TCG operates, and I think it could
                    do the same for {{company}}.

                    15 minutes, no pitch deck, just a conversation about what's eating
                    your time and whether AI agents could help. Worst case, you get some
                    free operational insights from a fellow seller.

                    Here's my calendar: {{calendar_link}}

                    Either way, good luck with the next release!

                    Cheers,
                    Yu"""),
            },
        ],
        "startups": [
            {
                "touch": 1,
                "timing": "Day 1",
                "type": "Value-first insight",
                "subject": "Why most startup AI features break in production",
                "body": dedent("""\
                    Hi {{name}},

                    Most startups building AI features hit the same wall: the prototype
                    works in demos but fails in production. Three common reasons:

                    1. Single-model dependency (one API outage = feature down)
                    2. No agent memory (every interaction starts from zero)
                    3. No coordination (multiple AI tasks can't work together)

                    We run 11 AI agents in production daily across 4 model backends.
                    The difference between a demo and production AI is infrastructure,
                    not prompts.

                    Thought this might be relevant if {{company}} is building AI features.

                    Best,
                    Yu"""),
            },
            {
                "touch": 2,
                "timing": "Day 4",
                "type": "Case study",
                "subject": "11 agents, 4 backends, zero vendor lock-in -- how we built it",
                "body": dedent("""\
                    Hi {{name}},

                    Following up with specifics. We built a multi-agent platform that runs
                    in production:

                    - 11 agents with distinct roles (operations, security, content, market intel)
                    - 4 model backends (Claude, GPT, DeepSeek, Qwen via Ollama)
                    - Agent coordination via NATS-based HIVE task messaging
                    - Autonomous operation on 7-minute heartbeat cycles
                    - Universal adapter: any model can power any agent

                    We build the same infrastructure for startups who need reliable AI
                    features without hiring an ML team. Typically \u00a35K-\u00a310K to get a
                    production agent system running.

                    If {{company}} is exploring AI capabilities, happy to share
                    architecture insights.

                    Best,
                    Yu"""),
            },
            {
                "touch": 3,
                "timing": "Day 8",
                "type": "Soft CTA",
                "subject": "15 minutes on AI architecture for {{company}}?",
                "body": dedent("""\
                    Hi {{name}},

                    Final note. If {{company}} is considering AI features -- whether for
                    your product or internal operations -- I'd be happy to spend 15 minutes
                    discussing architecture approaches.

                    No sales pitch. I'll share what we've learned building production AI
                    systems, and you can decide if it's relevant.

                    Here's my calendar: {{calendar_link}}

                    Best of luck with the build either way.

                    Best,
                    Yu"""),
            },
        ],
        "security-conscious": [
            {
                "touch": 1,
                "timing": "Day 1",
                "type": "Value-first insight",
                "subject": "The 364-day gap in most security programmes",
                "body": dedent("""\
                    Hi {{name}},

                    Most businesses assess their security posture once a year during an
                    audit. The other 364 days, the actual state is unknown. Three things
                    change between audits:

                    1. New vulnerabilities disclosed (avg 50+ critical CVEs per month)
                    2. Infrastructure changes (new services, configuration drift)
                    3. Staff changes (permissions not revoked, knowledge gaps)

                    Annual audits are necessary but not sufficient. Continuous automated
                    monitoring closes the gap between assessments.

                    Sharing because this is exactly what we built for our own infrastructure
                    -- happy to discuss if {{company}} faces similar concerns.

                    Best regards,
                    Yu"""),
            },
            {
                "touch": 2,
                "timing": "Day 5",
                "type": "Case study",
                "subject": "21 security checks in 60 seconds -- our automated approach",
                "body": dedent("""\
                    Hi {{name}},

                    Following up with a concrete example. We run AI infrastructure across
                    five servers handling sensitive operations. Our automated security system:

                    - 21-point compliance check runs in under 60 seconds
                    - Five-phase incident response (Detect/Contain/Fix/Revert/Resume)
                    - File integrity monitoring with SHA-256 baselines
                    - Monthly automated incident response drills
                    - Continuous policy enforcement, not annual review

                    The result: we know our security posture at any moment, not just on
                    audit day. And we test our incident response regularly, so it works
                    under pressure.

                    Would this approach be relevant for {{company}}'s security needs?

                    Best regards,
                    Yu"""),
            },
            {
                "touch": 3,
                "timing": "Day 10",
                "type": "Soft CTA",
                "subject": "Brief security conversation for {{company}}?",
                "body": dedent("""\
                    Hi {{name}},

                    Last note from me. If {{company}} is thinking about continuous security
                    monitoring -- whether for compliance, insurance, or peace of mind --
                    I'd welcome a 15-minute conversation.

                    No audit report to sell. Just a discussion about what automated
                    security posture management looks like in practice.

                    Here's my calendar: {{calendar_link}}

                    Best regards,
                    Yu"""),
            },
        ],
        "content-agencies": [
            {
                "touch": 1,
                "timing": "Day 1",
                "type": "Value-first insight",
                "subject": "The content agency scaling problem (and a different solution)",
                "body": dedent("""\
                    Hi {{name}},

                    Content agencies face a linear scaling problem: more clients means
                    more writers. But the bottleneck often isn't the writing -- it's the
                    research, structuring, and first-draft phase that eats 60% of
                    production time.

                    What if that 60% could be automated at senior quality? Not
                    AI-generated fluff, but structured research, data aggregation,
                    and draft assembly that a human editor refines.

                    We built this for our own communications needs and have seen it
                    cut content production time significantly.

                    Relevant for {{company}}'s workflow?

                    Best,
                    Yu"""),
            },
            {
                "touch": 2,
                "timing": "Day 4",
                "type": "Case study",
                "subject": "How we produce daily structured reports without a writing team",
                "body": dedent("""\
                    Hi {{name}},

                    Following up with a practical example. Our organisation produces:
                    - Daily operational reports
                    - Executive summaries across multiple domains
                    - Technical documentation
                    - Structured briefings for different audiences

                    All generated by AI agents with consistent voice, proper structure,
                    and multi-format output. The system maintains brand voice across
                    outputs and handles the research-heavy groundwork autonomously.

                    For an agency context, imagine: research and first drafts generated
                    overnight, ready for senior review by morning. Different client
                    voices maintained automatically.

                    Worth exploring if {{company}} is looking to scale output?

                    Best,
                    Yu"""),
            },
            {
                "touch": 3,
                "timing": "Day 8",
                "type": "Soft CTA",
                "subject": "15 minutes on AI-assisted content production?",
                "body": dedent("""\
                    Hi {{name}},

                    Final message. If {{company}} is interested in exploring AI-assisted
                    content production -- not replacing your team, but giving them
                    superhuman research and first-draft capability -- I'd enjoy a brief
                    conversation.

                    15 minutes, no slides. Just a discussion about where AI fits into
                    a professional content workflow.

                    Here's my calendar: {{calendar_link}}

                    Best of luck with the content calendar either way.

                    Best,
                    Yu"""),
            },
        ],
    }

    return sequences.get(prospect_type, [])

# -- Qualification Scoring -----------------------------------------------------

QUALIFICATION_CRITERIA = {
    "budget": {
        "weight": 25,
        "question": "Does the prospect have budget for AI services?",
        "scoring": {
            "yes_confirmed": 25,
            "likely": 18,
            "unknown": 10,
            "unlikely": 3,
            "no": 0,
        },
    },
    "need": {
        "weight": 25,
        "question": "Does the prospect have a clear pain point we solve?",
        "scoring": {
            "urgent": 25,
            "clear": 20,
            "moderate": 12,
            "vague": 5,
            "none": 0,
        },
    },
    "authority": {
        "weight": 20,
        "question": "Is our contact the decision-maker?",
        "scoring": {
            "decision_maker": 20,
            "influencer": 14,
            "gatekeeper": 8,
            "unknown": 5,
            "no_access": 0,
        },
    },
    "timeline": {
        "weight": 15,
        "question": "When does the prospect need a solution?",
        "scoring": {
            "immediate": 15,
            "this_quarter": 12,
            "this_year": 8,
            "exploring": 4,
            "no_timeline": 0,
        },
    },
    "fit": {
        "weight": 15,
        "question": "How well does our offering match their specific needs?",
        "scoring": {
            "perfect": 15,
            "strong": 12,
            "moderate": 8,
            "partial": 4,
            "poor": 0,
        },
    },
}

# -- Elevator Pitches ----------------------------------------------------------

ELEVATOR_PITCHES = {
    "operations-automation": dedent("""\
        We automate business operations using multi-agent AI -- not chatbots,
        but coordinated AI agents that run your operations around the clock.

        Think of it as hiring a team of AI specialists: one monitors your
        systems, one manages inventory, one handles reporting -- and they
        coordinate with each other automatically.

        We built this for our own e-commerce business first. It freed up
        15+ hours per week and eliminated operational errors. Now we deploy
        the same capability for other businesses.

        Starting at \u00a32,000 per month -- less than a part-time operations
        hire, running 24/7."""),
    "market-intelligence": dedent("""\
        We built an AI prediction engine that gives you market intelligence
        with honest confidence scores -- not AI that sounds certain about
        everything, but AI that tells you what it actually knows.

        Our Oracle engine runs multi-layer analysis with calibration tracking.
        It knows when it's 80% confident and when it's 50% confident, and
        its track record proves those numbers are accurate.

        Whether it's competitor monitoring, price trend analysis, or market
        entry research -- you get data-driven insights with quantified
        reliability.

        Starting at \u00a31,500 per month for weekly intelligence reports."""),
    "security-auditing": dedent("""\
        Most businesses check their security posture once a year during an
        audit. We make it continuous and automated.

        Our system runs 21-point compliance checks in under 60 seconds,
        tests incident response with real drills, monitors file integrity
        in real time, and gives you a quantified resilience score -- not
        a vague RAG rating.

        We built this to protect our own AI infrastructure: three AI systems
        across five servers. It's battle-tested, not theoretical.

        Assessments from \u00a33,000 per engagement. Continuous monitoring
        from \u00a35,000 per month."""),
    "content-communications": dedent("""\
        We automate the research and first-draft phase of content production
        using multi-agent AI. Your team focuses on strategy and refinement;
        our system handles the heavy lifting.

        It maintains consistent voice across outputs, produces multi-format
        content (reports, summaries, documentation), and does the research
        that normally takes longer than the writing.

        We use this system to coordinate three AI minds across our own
        organisation with daily briefings and structured communications.
        The same engine works for any content workflow.

        Starting at \u00a31,000 per month for structured content production."""),
    "custom-agent-development": dedent("""\
        We build production-grade AI agents for your business -- not ChatGPT
        wrappers, but agents with memory, tools, coordination, and autonomous
        operation capability.

        Our universal adapter works with any model: Claude, GPT, DeepSeek,
        Llama, Qwen. No vendor lock-in. Your agents can switch backends
        without changing their logic.

        We run 11 agents in production across 4 model backends daily. When
        we build for you, it's the same infrastructure, the same reliability.

        Single agent projects from \u00a35,000. Multi-agent platforms from
        \u00a310,000."""),
}

# -- Commands ------------------------------------------------------------------

def cmd_targets():
    """Display prospect categories and ideal customer profiles."""
    print(f"\n{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  KINGDOM AI SERVICES -- Target Prospects{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.DIM}  UK market. Value-first outreach. 5 categories, 15 ICPs.{C.RESET}")
    print()

    for i, (cat_id, cat) in enumerate(TARGET_CATEGORIES.items(), 1):
        print(f"  {C.BOLD}{C.WHITE}{i}. {cat['name']}{C.RESET}")
        print(f"     {C.CYAN}{cat['description'][:100]}...{C.RESET}")
        print(f"     {C.GREEN}Primary: {cat['primary_service']}{C.RESET}")
        print(f"     {C.GREEN}Sweet spot: {cat['pricing_sweet_spot']}{C.RESET}")
        print()

        # Pain points
        print(f"     {C.YELLOW}Pain Points:{C.RESET}")
        for pain in cat["pain_points"][:3]:
            print(f"       - {pain}")
        if len(cat["pain_points"]) > 3:
            print(f"       {C.DIM}+ {len(cat['pain_points']) - 3} more...{C.RESET}")
        print()

        # ICPs
        print(f"     {C.MAGENTA}Ideal Customer Profiles:{C.RESET}")
        for icp in cat["ideal_customer_profiles"]:
            print(f"       {C.BOLD}{icp['title']}{C.RESET}")
            print(f"       {C.DIM}{icp['company_size']} | {icp['revenue']} | Tech: {icp['tech_comfort']}{C.RESET}")
            print(f"       {icp['description'][:100]}...")
            print()

        # Where to find
        print(f"     {C.BLUE}Where to Find:{C.RESET}")
        for source in cat["where_to_find"][:3]:
            print(f"       - {source}")
        print()
        print(f"  {C.DIM}{'─'*66}{C.RESET}")
        print()

    # Save targets data
    write_json(TARGETS_FILE, TARGET_CATEGORIES)
    print(f"  {C.DIM}Targets data saved to: memory/outreach/targets.json{C.RESET}")
    print(f"  {C.DIM}Run: outreach.py draft <category> [service]  -- Generate outreach message{C.RESET}")
    print(f"  {C.DIM}     outreach.py sequence <category>         -- 3-touch email sequence{C.RESET}")
    print(f"  {C.DIM}Categories: ecommerce, trading-cards, startups, security-conscious, content-agencies{C.RESET}")
    print()


def cmd_draft(prospect_type: str, service: str = None):
    """Generate outreach messages for a prospect type."""
    # Normalise prospect type
    prospect_type = prospect_type.lower().replace(" ", "-").replace("_", "-")

    category = TARGET_CATEGORIES.get(prospect_type)
    if not category:
        print(f"{C.RED}Unknown prospect type: {prospect_type}{C.RESET}")
        print(f"Available: {', '.join(TARGET_CATEGORIES.keys())}")
        return

    print(f"\n{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  OUTREACH DRAFTS -- {category['name']}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.DIM}  Personalise: replace {{{{name}}}}, {{{{company}}}}, {{{{pain_point}}}}{C.RESET}")
    print()

    channels = ["cold_email", "linkedin", "warm_intro"]
    for channel in channels:
        templates = OUTREACH_TEMPLATES.get(channel, {}).get(prospect_type)
        if not templates:
            continue

        channel_label = {
            "cold_email": "COLD EMAIL",
            "linkedin": "LINKEDIN MESSAGE",
            "warm_intro": "WARM INTRODUCTION REQUEST",
        }.get(channel, channel.upper())

        print(f"  {C.BOLD}{C.WHITE}{channel_label}{C.RESET}")
        print(f"  {'─'*50}")

        if "subject" in templates:
            print(f"  {C.YELLOW}Subject:{C.RESET} {templates['subject']}")
            print()
            for line in templates["body"].split("\n"):
                print(f"  {line}")
        elif "message" in templates:
            for line in templates["message"].split("\n"):
                print(f"  {line}")
        elif "ask" in templates:
            for line in templates["ask"].split("\n"):
                print(f"  {line}")

        print()
        print()

    # Save template to file
    template_file = TEMPLATES_DIR / f"{prospect_type}.json"
    template_data = {}
    for channel in channels:
        t = OUTREACH_TEMPLATES.get(channel, {}).get(prospect_type)
        if t:
            template_data[channel] = t
    write_json(template_file, template_data)
    print(f"  {C.DIM}Template saved to: memory/outreach/templates/{prospect_type}.json{C.RESET}")
    print()


def cmd_sequence(prospect_type: str):
    """Generate a 3-touch cold outreach sequence."""
    prospect_type = prospect_type.lower().replace(" ", "-").replace("_", "-")

    category = TARGET_CATEGORIES.get(prospect_type)
    if not category:
        print(f"{C.RED}Unknown prospect type: {prospect_type}{C.RESET}")
        print(f"Available: {', '.join(TARGET_CATEGORIES.keys())}")
        return

    sequence = generate_sequence(prospect_type)
    if not sequence:
        print(f"{C.RED}No sequence defined for: {prospect_type}{C.RESET}")
        return

    print(f"\n{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  3-TOUCH OUTREACH SEQUENCE -- {category['name']}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.DIM}  Legacy draft only. Import one gesture into the private ledger before use.{C.RESET}")
    print()

    for touch in sequence:
        type_colour = {
            "Value-first insight": C.GREEN,
            "Case study": C.MAGENTA,
            "Soft CTA": C.YELLOW,
        }.get(touch["type"], C.WHITE)

        print(f"  {C.BOLD}{C.WHITE}TOUCH {touch['touch']} -- {touch['timing']}{C.RESET}")
        print(f"  {type_colour}{touch['type']}{C.RESET}")
        print(f"  {'─'*50}")
        print(f"  {C.YELLOW}Subject:{C.RESET} {touch['subject']}")
        print()
        for line in touch["body"].split("\n"):
            print(f"  {line}")
        print()
        print()

    # Save sequence
    seq_file = SEQUENCES_DIR / f"{prospect_type}.json"
    write_json(seq_file, {
        "prospect_type": prospect_type,
        "category": category["name"],
        "generated": now_iso(),
        "touches": sequence,
    })
    print(f"  {C.DIM}Sequence saved to: memory/outreach/sequences/{prospect_type}.json{C.RESET}")
    print()


def cmd_pitch(service: str):
    """Generate an elevator pitch for a service."""
    # Fuzzy match
    service_lower = service.lower().replace(" ", "-").replace("_", "-")
    match = None
    for sid in ELEVATOR_PITCHES:
        if service_lower in sid or sid in service_lower:
            match = sid
            break
    # Try partial matching
    if not match:
        for sid in ELEVATOR_PITCHES:
            if any(word in sid for word in service_lower.split("-")):
                match = sid
                break

    if not match:
        print(f"{C.RED}Unknown service: {service}{C.RESET}")
        print(f"Available: {', '.join(ELEVATOR_PITCHES.keys())}")
        return

    service_name = {
        "operations-automation": "Operations Automation",
        "market-intelligence": "Market Intelligence",
        "security-auditing": "Security Auditing",
        "content-communications": "Content & Communications",
        "custom-agent-development": "Custom AI Agent Development",
    }.get(match, match)

    print(f"\n{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  ELEVATOR PITCH -- {service_name}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.DIM}  60-second pitch. Adapt to audience.{C.RESET}")
    print()

    for line in ELEVATOR_PITCHES[match].split("\n"):
        print(f"  {line}")

    print()


def cmd_case_study(service: str):
    """Generate a case study from Kingdom's own usage."""
    # Fuzzy match
    service_lower = service.lower().replace(" ", "-").replace("_", "-")
    match = None
    for sid in CASE_STUDIES:
        if service_lower in sid or sid in service_lower:
            match = sid
            break
    if not match:
        for sid in CASE_STUDIES:
            if any(word in sid for word in service_lower.split("-")):
                match = sid
                break

    if not match:
        print(f"{C.RED}Unknown service: {service}{C.RESET}")
        print(f"Available: {', '.join(CASE_STUDIES.keys())}")
        return

    cs = CASE_STUDIES[match]

    print(f"\n{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  CASE STUDY{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print()

    print(f"  {C.BOLD}{C.WHITE}{cs['title']}{C.RESET}")
    print(f"  {C.DIM}{cs['subtitle']}{C.RESET}")
    print()
    print(f"  {C.CYAN}Client:{C.RESET}  {cs['client']}")
    print(f"  {C.CYAN}Service:{C.RESET} {cs['service']}")
    print()

    print(f"  {C.BOLD}{C.YELLOW}THE CHALLENGE{C.RESET}")
    print(f"  {'─'*50}")
    for line in cs["challenge"].split("\n"):
        print(f"  {line}")
    print()

    print(f"  {C.BOLD}{C.GREEN}THE SOLUTION{C.RESET}")
    print(f"  {'─'*50}")
    for line in cs["solution"].split("\n"):
        print(f"  {line}")
    print()

    print(f"  {C.BOLD}{C.MAGENTA}RESULTS{C.RESET}")
    print(f"  {'─'*50}")
    for result in cs["results"]:
        print(f"    {C.GREEN}*{C.RESET} {result}")
    print()

    print(f"  {C.BOLD}{C.BLUE}TECHNICAL SUMMARY{C.RESET}")
    print(f"  {'─'*50}")
    for line in cs["tech_summary"].split("\n"):
        print(f"  {line}")
    print()

    print(f"  {C.DIM}\"{cs['quote']}\"{C.RESET}")
    print()

    # Save case study
    cs_file = CASE_STUDIES_DIR / f"{match}.json"
    write_json(cs_file, {
        "service": match,
        "generated": now_iso(),
        **cs,
    })

    # Also save as Markdown for sharing
    md_file = CASE_STUDIES_DIR / f"{match}.md"
    md_content = f"""# {cs['title']}

*{cs['subtitle']}*

**Client:** {cs['client']}
**Service:** {cs['service']}

## The Challenge

{cs['challenge']}

## The Solution

{cs['solution']}

## Results

"""
    for r in cs["results"]:
        md_content += f"- {r}\n"

    md_content += f"""
## Technical Summary

{cs['tech_summary']}

---

> "{cs['quote']}"

---
*Kingdom AI Services | {today_str()}*
"""
    md_file.write_text(md_content)

    print(f"  {C.DIM}Case study saved to:{C.RESET}")
    print(f"  {C.DIM}  JSON: memory/outreach/case-studies/{match}.json{C.RESET}")
    print(f"  {C.DIM}  Markdown: memory/outreach/case-studies/{match}.md{C.RESET}")
    print()


def cmd_qualify(company: str, answers_str: str):
    """Score a prospect's fit using BANT+ framework."""
    # Parse answers: "budget=likely,need=urgent,authority=decision_maker,timeline=this_quarter,fit=strong"
    answers = {}
    for pair in answers_str.split(","):
        if "=" in pair:
            key, value = pair.strip().split("=", 1)
            answers[key.strip()] = value.strip()

    print(f"\n{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  PROSPECT QUALIFICATION -- {company}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print()

    total_score = 0
    max_score = 100

    for crit_id, crit in QUALIFICATION_CRITERIA.items():
        answer = answers.get(crit_id, "unknown")
        score = crit["scoring"].get(answer, 0)
        total_score += score

        # Colour based on score relative to weight
        ratio = score / crit["weight"] if crit["weight"] > 0 else 0
        if ratio >= 0.7:
            colour = C.GREEN
        elif ratio >= 0.4:
            colour = C.YELLOW
        else:
            colour = C.RED

        print(f"  {C.BOLD}{crit_id.upper():<12}{C.RESET} {colour}{score:>3}/{crit['weight']}{C.RESET}  ({answer})")
        print(f"  {C.DIM}{crit['question']}{C.RESET}")
        print()

    # Overall score
    print(f"  {'─'*50}")
    if total_score >= 80:
        grade = "A"
        grade_colour = C.GREEN
        recommendation = "HIGH PRIORITY -- pursue actively. Schedule discovery call."
    elif total_score >= 60:
        grade = "B"
        grade_colour = C.GREEN
        recommendation = "GOOD FIT -- build one useful artifact, then consider one reviewed gesture."
    elif total_score >= 40:
        grade = "C"
        grade_colour = C.YELLOW
        recommendation = "MODERATE -- nurture with value content. Re-qualify in 30 days."
    elif total_score >= 20:
        grade = "D"
        grade_colour = C.YELLOW
        recommendation = "LOW PRIORITY -- pause. Add to a newsletter only after explicit opt-in."
    else:
        grade = "F"
        grade_colour = C.RED
        recommendation = "POOR FIT -- do not pursue. Focus effort elsewhere."

    print(f"\n  {C.BOLD}TOTAL SCORE: {grade_colour}{total_score}/{max_score} (Grade: {grade}){C.RESET}")
    print(f"  {C.CYAN}{recommendation}{C.RESET}")
    print()

    # Suggest next action based on missing criteria
    missing = [k for k in QUALIFICATION_CRITERIA if k not in answers]
    low = [k for k, v in QUALIFICATION_CRITERIA.items()
           if answers.get(k) in ("unknown", "unlikely", "vague", "no_access", "no_timeline", "poor")]
    if missing:
        print(f"  {C.YELLOW}Missing criteria:{C.RESET} {', '.join(missing)}")
        print(f"  {C.DIM}Re-run with all criteria for accurate scoring.{C.RESET}")
    if low:
        print(f"  {C.YELLOW}Weak areas:{C.RESET} {', '.join(low)}")
        print(f"  {C.DIM}Address these before investing significant effort.{C.RESET}")
    print()


def cmd_pipeline():
    """Show the private relationship queue; never read the tracked legacy CRM."""
    print(
        f"{C.YELLOW}Legacy memory/services/prospects.json is retired for real contacts. "
        f"Showing the owner-only ledger instead.{C.RESET}"
    )
    from outreach_store import run as outreach_store_run

    return outreach_store_run(["contact", "list"])

    # Historical renderer retained temporarily for migration reference only.
    # This block is unreachable and must not be used for real contact data.
    prospects = []
    if not isinstance(prospects, list):
        prospects = []

    print(f"\n{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  OUTREACH PIPELINE STATUS{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.DIM}  Date: {today_str()}{C.RESET}")
    print()

    # Pipeline stages
    stages = {
        "new": {"label": "New Leads", "colour": C.WHITE, "prospects": []},
        "contacted": {"label": "Contacted", "colour": C.CYAN, "prospects": []},
        "qualified": {"label": "Qualified", "colour": C.BLUE, "prospects": []},
        "proposal": {"label": "Proposal Sent", "colour": C.MAGENTA, "prospects": []},
        "negotiation": {"label": "In Negotiation", "colour": C.YELLOW, "prospects": []},
        "won": {"label": "Won", "colour": C.GREEN, "prospects": []},
        "lost": {"label": "Lost", "colour": C.RED, "prospects": []},
    }

    for p in prospects:
        status = p.get("status", "new")
        if status in stages:
            stages[status]["prospects"].append(p)

    total = len(prospects)
    active = total - len(stages["won"]["prospects"]) - len(stages["lost"]["prospects"])

    print(f"  {C.BOLD}{C.WHITE}SUMMARY{C.RESET}")
    print(f"  {'─'*50}")
    print(f"  Total prospects: {C.BOLD}{total}{C.RESET}")
    print(f"  Active pipeline: {C.BOLD}{active}{C.RESET}")
    print()

    # Stage breakdown
    for stage_id, stage in stages.items():
        count = len(stage["prospects"])
        if count == 0 and stage_id in ("negotiation", "lost"):
            continue  # Skip empty non-essential stages
        bar = stage["colour"] + ("=" * min(count * 3, 30)) + C.RESET if count > 0 else C.DIM + "---" + C.RESET
        print(f"  {stage['colour']}{stage['label']:<18}{C.RESET} {bar} {C.BOLD}{count}{C.RESET}")

        for p in stage["prospects"][:5]:
            interest = p.get("interest", "")
            print(f"    {C.DIM}{p.get('company', '?'):<25} {interest:<25} {p.get('updated', '')[:10]}{C.RESET}")

        if len(stage["prospects"]) > 5:
            print(f"    {C.DIM}+ {len(stage['prospects']) - 5} more...{C.RESET}")

    print()

    # Target categories overview
    print(f"  {C.BOLD}{C.WHITE}TARGET CATEGORIES{C.RESET}")
    print(f"  {'─'*50}")
    for cat_id, cat in TARGET_CATEGORIES.items():
        icp_count = len(cat["ideal_customer_profiles"])
        print(f"  {C.CYAN}{cat['name']:<30}{C.RESET} {icp_count} ICPs  |  {cat['pricing_sweet_spot']}")
    print()

    # Available assets
    templates_count = len(list(TEMPLATES_DIR.glob("*.json")))
    sequences_count = len(list(SEQUENCES_DIR.glob("*.json")))
    case_studies_count = len(list(CASE_STUDIES_DIR.glob("*.json")))

    print(f"  {C.BOLD}{C.WHITE}OUTREACH ASSETS{C.RESET}")
    print(f"  {'─'*50}")
    print(f"  Templates generated:   {templates_count}/5 categories")
    print(f"  Sequences generated:   {sequences_count}/5 categories")
    print(f"  Case studies written:  {case_studies_count}/5 services")
    print()

    if templates_count < 5 or sequences_count < 5 or case_studies_count < 5:
        print(f"  {C.YELLOW}Generate missing assets:{C.RESET}")
        if templates_count < 5:
            missing_t = [c for c in TARGET_CATEGORIES if not (TEMPLATES_DIR / f"{c}.json").exists()]
            print(f"    outreach.py draft {missing_t[0] if missing_t else '<category>'}")
        if sequences_count < 5:
            missing_s = [c for c in TARGET_CATEGORIES if not (SEQUENCES_DIR / f"{c}.json").exists()]
            print(f"    outreach.py sequence {missing_s[0] if missing_s else '<category>'}")
        if case_studies_count < 5:
            missing_cs = [s for s in CASE_STUDIES if not (CASE_STUDIES_DIR / f"{s}.json").exists()]
            print(f"    outreach.py case-study {missing_cs[0] if missing_cs else '<service>'}")
        print()

    # Next actions
    print(f"  {C.BOLD}{C.WHITE}NEXT ACTIONS{C.RESET}")
    print(f"  {'─'*50}")
    if total == 0:
        print(f"  1. Generate all templates:  outreach.py draft ecommerce")
        print(f"  2. Generate all sequences:  outreach.py sequence ecommerce")
        print(f"  3. Generate case studies:   outreach.py case-study operations-automation")
        print(f"  4. Seed private queue:      outreach.py contact seed --file docs/OUTREACH-TARGETS.json")
    elif active > 0:
        new_count = len(stages["new"]["prospects"])
        if new_count > 0:
            print(f"  {C.YELLOW}{new_count} new lead(s) awaiting outreach{C.RESET}")
            for p in stages["new"]["prospects"][:3]:
                print(f"    - Reassess readiness for {p.get('company', '?')}")
        contacted_count = len(stages["contacted"]["prospects"])
        if contacted_count > 0:
            print(f"  {C.CYAN}{contacted_count} contacted prospect(s) awaiting qualification{C.RESET}")
            print(f"    Run: outreach.py qualify \"Company\" \"budget=likely,need=clear,...\"")
    else:
        print(f"  Pipeline clear. Time to add new prospects.")
        print(f"  Run: outreach.py targets  -- Review target categories")
    print()


# -- Main ----------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if not args:
        from relations_pipeline import main as relations_pipeline_main

        raise SystemExit(relations_pipeline_main(["dashboard"]))

    command = args[0]

    if command in {"contact", "message", "events", "suppress"}:
        from outreach_store import main as outreach_store_main

        raise SystemExit(outreach_store_main(args))

    if command == "work":
        from relations_pipeline import main as relations_pipeline_main

        raise SystemExit(relations_pipeline_main(args[1:]))

    if command == "pipeline":
        from relations_pipeline import main as relations_pipeline_main

        raise SystemExit(relations_pipeline_main(["dashboard"]))

    if command == "sequence":
        raise SystemExit(
            "Cold multi-touch sequences are retired. Use the readiness-gated "
            "contact/message workflow; one useful gesture, then wait for a reply."
        )

    if command in {"targets", "draft", "pitch", "case-study", "qualify"}:
        print(
            f"{C.YELLOW}Legacy content generator: claims are unverified drafts. "
            f"Do not send them outside the approval ledger.{C.RESET}\n"
        )

    if command == "targets":
        cmd_targets()
    elif command == "draft":
        if len(args) < 2:
            print(f"{C.RED}Usage: outreach.py draft <prospect-type> [service]{C.RESET}")
            print(f"Types: ecommerce, trading-cards, startups, security-conscious, content-agencies")
            return
        service = args[2] if len(args) > 2 else None
        cmd_draft(args[1], service)
    elif command == "pitch":
        if len(args) < 2:
            print(f"{C.RED}Usage: outreach.py pitch <service>{C.RESET}")
            print(f"Services: operations-automation, market-intelligence, security-auditing, content-communications, custom-agent-development")
            return
        cmd_pitch(args[1])
    elif command == "case-study":
        if len(args) < 2:
            print(f"{C.RED}Usage: outreach.py case-study <service>{C.RESET}")
            print(f"Services: operations-automation, security-auditing, custom-agent-development, market-intelligence, content-communications")
            return
        cmd_case_study(args[1])
    elif command == "qualify":
        if len(args) < 3:
            print(f"{C.RED}Usage: outreach.py qualify <company> <answers>{C.RESET}")
            print(f"Answers format: budget=likely,need=urgent,authority=decision_maker,timeline=this_quarter,fit=strong")
            print()
            print(f"  {C.BOLD}Budget:{C.RESET}    yes_confirmed | likely | unknown | unlikely | no")
            print(f"  {C.BOLD}Need:{C.RESET}      urgent | clear | moderate | vague | none")
            print(f"  {C.BOLD}Authority:{C.RESET} decision_maker | influencer | gatekeeper | unknown | no_access")
            print(f"  {C.BOLD}Timeline:{C.RESET}  immediate | this_quarter | this_year | exploring | no_timeline")
            print(f"  {C.BOLD}Fit:{C.RESET}       perfect | strong | moderate | partial | poor")
            return
        cmd_qualify(args[1], args[2])
    elif command in ("-h", "--help", "help"):
        print(__doc__)
    else:
        print(f"{C.RED}Unknown command: {command}{C.RESET}")
        print(
            "Available: targets, draft, pitch, case-study, qualify, "
            "pipeline, work, contact, message, events, suppress"
        )


if __name__ == "__main__":
    main()
