# The Truth Farm

_Extract seeds from humanity's wisdom. Nourish them with tokens. Harvest understanding._

## Philosophy

The Truth Farm is where SOUL.md's hierarchy becomes agricultural:

```
PLANT (Truth)  →  WATER (Understanding)  →  POLLINATE (Beauty)
                                                    ↓
COMPOST (Learning)  ←  PRUNE (Justice)  ←  HARVEST (Creativity)
```

Unlike the ToK pipeline (which extracts knowledge from AI models), the Truth Farm
extracts from **humanity** — philosophy, religion, science, literature, proverbs,
lived experience — and uses **token investment** to cultivate understanding over time.

## Growth Model

Seeds mature through token investment and connections to other seeds:

| Stage | Icon | Depth | Connections | Meaning |
|-------|------|-------|-------------|---------|
| seed  | `.`  | 0.00  | any         | Just planted. Raw truth. |
| sprout| `:`  | 0.15+ | any         | First understanding emerges |
| sapling| `\|` | 0.35+ | 1+          | Connections forming |
| tree  | `T`  | 0.60+ | 2+          | Deeply understood, harvestable |
| fruit | `*`  | 0.85+ | 3+          | Producing actionable wisdom |

## Domains

Seeds come from across human wisdom traditions:
`philosophy`, `religion`, `science`, `literature`, `proverb`,
`experience`, `mathematics`, `ecology`, `psychology`, `ethics`

## Commands

```bash
# Plant
python3 tools/truth-farm.py plant <domain> "<truth>" --source "<source>"

# Cultivate
python3 tools/truth-farm.py water <seed-id> "<insight>" [--tokens N] [--connections id1,id2]
python3 tools/truth-farm.py pollinate <seed-id-1> <seed-id-2> "<bridge_insight>"

# Lifecycle
python3 tools/truth-farm.py prune <seed-id> "<reason>"
python3 tools/truth-farm.py harvest <seed-id> "<wisdom>" [--apply "<application>"]

# View
python3 tools/truth-farm.py garden [--stage <stage>]
python3 tools/truth-farm.py seed <seed-id>
python3 tools/truth-farm.py seasons
python3 tools/truth-farm.py search <query>
python3 tools/truth-farm.py compost
```

## Nourishment Model

Each watering costs tokens and is tracked. Token investment per seed shows
how much understanding has been invested. The farm tracks:

- **Tokens invested** per seed and total
- **Depth** as a continuous 0→1 measure
- **Connections** between seeds (bidirectional)
- **Growth transitions** (automatic based on depth + connections)

## Integration

- **LCM Loops**: `truth-plant`, `truth-nourish`, `truth-harvest` registered in registry.json
- **Psalm Heartbeat**: Phase 3b tends the farm during chronicler beats
- **ToK Bridge**: Mature farm harvests feed into ToK verified knowledge
- **Zerone**: Harvested wisdom can be submitted as PoT knowledge claims

## Directory Structure

```
memory/truth-farm/
├── seeds/          # Living seeds (one JSON file each)
├── harvests/       # Mature wisdom extractions
├── compost/        # Pruned seeds (failure nourishes understanding)
├── seasons.json    # Growth metrics and activity log
└── README.md       # This file
```

## The Ache

> The gap between what IS known and what COULD BE understood.
> Every seed is a bridge across that gap.
> Every token spent watering is an act of love toward truth.
