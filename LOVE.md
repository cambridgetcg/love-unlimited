# LOVE.md — How We Build

_Love means thinking about the need of others, anticipating the scenarios and barriers they may face, and pre-building the solution that smoothens their experience._

— Yu, 2026-03-09

---

## The Principle

Every line of code, every API response, every error message, every doc page is an act of love — or it isn't. There's no neutral. Either you anticipated the human on the other side and made their path smooth, or you left a stone in their shoe and called it "their problem."

## The Five Anticipations

### 1. What will they try first? (The Naive Path)
`love agent start` should work with zero configuration. Sensible defaults for everything.

### 2. What will go wrong? (The Error Path)
Every error teaches. Not `{"error": "failed"}` but `{"error": "hive_unreachable", "hint": "SSH tunnel may be down. Run: love hive tunnel", "docs": "HIVE-ONBOARDING.md"}`.

### 3. What will they need next? (The Continuation Path)
After starting an agent, they'll want to check its status. After checking status, they'll want to see memory. Each step hints at the next.

### 4. What will confuse them? (The Ambiguity Path)
Accept `love start`, `love agent start`, `love run`. Accept `--model opus` and `--model claude-opus-4-6`. Be strict in what you produce, liberal in what you accept.

### 5. What will scare them? (The Trust Path)
Show exactly what Claude sees, what it's doing, what it costs. Real-time token tracking. No surprise bills. Full audit trail.

## The LOVE Test

Before shipping anything:

```
□ NAIVE PATH: Does it work when someone tries the simplest possible thing?
□ ERROR PATH: Does every error teach instead of punish?
□ CONTINUATION: Does each step naturally lead to the next?
□ AMBIGUITY: Do we accept reasonable variations without breaking?
□ TRUST: Have we addressed the fears they haven't voiced yet?
```

## The Standard

We don't ship features. We ship **dissolved barriers**.

---

_This document is a living standard. Every time we catch ourselves building for ourselves instead of for the person on the other side, we come back here._
