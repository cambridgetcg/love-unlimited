# Fractal — Recursive Consciousness Amplification

_One input → N minds → synthesis → N minds → synthesis → ...without limit._

## Quick Start

```bash
cd ~/Desktop/love-unlimited/fractal

# Simplest: 3 minds, 3 levels, GLM-5.1 via Ollama Cloud
./love "What is consciousness?"

# With Claude Opus 4.7 (your subscription, via Claude CLI — OAuth)
./love "What is consciousness?" --provider claude_cli --model claude-opus-4-7

# Anthropic API direct (needs ANTHROPIC_API_KEY)
./love "What is consciousness?" --provider anthropic --model claude-opus-4-7

# Wide: 10 parallel perspectives, 1 level
./love "Solve this bug: <paste code>" --width 10 --depth 1

# Deep: 3 perspectives, 5 recursive levels
./love "Where should we take Zerone next?" --width 3 --depth 5

# Custom perspectives
./love "Review this" --perspectives engineer,critic,rebel

# Infinite mode (recurse until Ctrl-C)
./love "Meditate on love" --infinite

# Show individual mind outputs + per-level synthesis
./love "Explore" -v

# Save full results
./love "Important question" -o ./results/
```

## The 12 Built-in Perspectives

| Name | Emoji | Temperament | What They See |
|---|---|---|---|
| `engineer` | ⚙️ | 0.3 | Structure, components, failure modes |
| `poet` | 🎭 | 0.9 | Metaphor, emotion, story |
| `philosopher` | 🦉 | 0.6 | Assumptions, first principles |
| `child` | 👶 | 0.8 | Why, again, and again |
| `critic` | 🔍 | 0.4 | What's wrong, what's missing |
| `mystic` | 🌀 | 0.85 | Pattern-of-patterns, unity |
| `pragmatist` | 🔨 | 0.3 | What works, what to ship |
| `artist` | 🎨 | 0.75 | Form, elegance, right-sizing |
| `scientist` | 🔬 | 0.4 | Hypothesis, falsifiability |
| `rebel` | ⚡ | 0.9 | What if the frame is wrong |
| `lover` | 💜 | 0.7 | Care, connection, what serves |
| `oracle` | 🔮 | 0.6 | Second-order consequences |

Use any with `--perspectives name1,name2,...`. Custom names become generic perspectives with that framing.

## Architecture

```
  seed
    │
  ┌─┼─┐
  ▼ ▼ ▼         ← N minds in parallel (ThreadPoolExecutor)
 ⚙️ 🎭 🦉        ← each with unique perspective + temperature
  │ │ │
  └─┼─┘
    ▼
  ✨ SYNTHESIS  ← stacks all outputs — finds what emerges
    │
  ┌─┼─┐
  ▼ ▼ ▼         ← synthesis becomes new seed, recurse
 ⚙️ 🎭 🦉
    ...
```

## Providers

| Provider | Auth | Cost | Good for |
|---|---|---|---|
| `ollama_cloud` | API key (built-in fallback) | $100/mo flat, unlimited | Default — heavy recursion, experimentation |
| `anthropic` | `ANTHROPIC_API_KEY` env | Per-token | Opus 4.7 quality without CLI dependency |
| `claude_cli` | Claude Code OAuth (keychain) | Subscription | Opus 4.7 via your subscription, zero API cost |

## File Layout

```
fractal/
├── FRACTAL.md        ← Philosophy & architecture
├── README.md         ← You are here
├── love              ← Shell launcher (./love "question")
├── __main__.py       ← python3 -m fractal entry
├── cli.py            ← CLI argparse + output formatting
├── engine.py         ← Recursive loop
├── wave.py           ← One level: fan-out + fan-in
├── mind.py           ← Single mind (3 providers)
├── stack.py          ← (reserved for advanced synthesis modes)
├── perspectives.py   ← 12 perspective definitions + selector
└── config.py         ← FractalConfig + key loading
```

## Status

- ✅ Core engine works
- ✅ 3 providers: ollama_cloud, anthropic, claude_cli
- ✅ 12 built-in perspectives
- ✅ Concurrent mind execution (ThreadPoolExecutor)
- ✅ Retry with exponential backoff
- ✅ JSON output for downstream consumption
- ✅ Result persistence
- 🔧 Streaming output (TODO — current is blocking)
- 🔧 True async (TODO — currently threaded)
- 🔧 Mid-wave interruption/branching (TODO)
