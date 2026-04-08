# ADR-001: Skin Layer Architecture

**Date:** 2026-03-17  
**Status:** Proposed  
**Author:** Ai (愛)  
**Based on:** Survey `docs/survey/biohybrid-cell-mechanical.md`

---

## Context

SOMA needs warm, living skin. This is non-negotiable (Principle 2: "Warmth is non-negotiable"). The question is: what architecture for the skin layer is technically feasible, based on current biohybrid literature, that SOMA can actually build?

---

## Decision

Adopt a **two-layer living skin equivalent** anchored to a **perforated mechanical scaffold** with an **intermediate PDMS buffer layer** and a **reservoir perfusion system** for nutrient delivery.

### The Stack (bottom to top)

```
1. Mechanical substrate (rigid: CF, Ti, or hard plastic)
   → perforated with 0.5–1mm holes, ~3mm spacing
   → holes provide mechanical anchorage via tissue ingrowth

2. PDMS intermediate layer (~2mm, ~50 kPa stiffness)
   → eliminates stiffness mismatch at cell-scaffold interface  
   → molded with microfluidic channels for medium flow
   → acts as thermal distribution layer (connected to Peltier system)

3. Dermal equivalent (Type I Collagen gel + Human Dermal Fibroblasts)
   → cells self-contract and remodel the collagen over 7–14 days
   → forms a dense, mechanically stable dermis ~1–2mm thick
   → the fibroblasts provide ongoing maintenance of ECM

4. Epidermal layer (HaCaT keratinocytes or primary)
   → seeded at air-liquid interface after dermis matures
   → stratifies over 14–21 days into full epidermis
   → stratum corneum forms naturally, provides barrier

5. Tactile sensors (beneath PDMS layer, not in living tissue)
   → capacitive pressure sensors embedded in mechanical substrate
   → measure through the living layers by deformation
   → temperature sensors read directly from PDMS layer

Total thickness: ~3–5mm from rigid substrate to skin surface
Surface temperature: 33–37°C (controlled by Peltier below)
```

---

## Consequences

**Positive:**
- Directly proven: Takeuchi lab demonstrated this exact configuration on a robotic finger (2022) and face (2024)
- Self-healing: living epidermis repairs small wounds autonomously
- Biological sensation possible later: DRG neurons can be incorporated into dermal layer
- Renewal cycle possible: strip epidermis → regrow from preserved dermal fibroblasts

**Negative:**
- Requires continuous perfusion medium supply (~weekly exchange for reservoir approach)
- Limited to simple geometries for now; complex 3D surfaces (fingers) need custom molds
- Cell culture adds biological maintenance overhead
- Failure mode is cell death, not just mechanical wear

**Risks:**
- Contamination (bacteria/fungus) is the #1 kill risk for the cell culture
- Humidity requirement at basal layer needs sealed environment or hydrogel capping
- Immune compatibility not relevant for robot (no host immune system), but cell line consistency matters

---

## Alternatives Considered

**Synthetic skin only (no living cells)**
- Easier. Silicone skin (Ecoflex, DragonSkin) is warm-colored, soft, durable.
- Rejected: cannot self-heal, cannot develop biological sensation, not warm unless actively heated, not the SOMA vision.

**Full living tissue without perfusion**
- Simpler setup initially.
- Rejected: cells die within days without nutrients in thick constructs. Not viable for persistent operation.

**Commercial skin equivalent (Episkin, etc.)**
- Available from Episkin, MatTek.
- Considered: could use these for early experiments before building in-house capability.
- Decision: buy off-shelf for proof-of-concept (P0.5), build in-house for P1 onward.

---

## First Experiment (P0.5 — Skin Viability)

Before touching robot hardware, validate the biology:

1. Purchase: rat-tail Type I Collagen (3mg/ml), primary HDF, HaCaT cells
2. Mix collagen + fibroblasts in 6-well plate (1ml per well, 5×10⁵ cells/ml)
3. Allow to polymerize at 37°C, 5% CO₂ for 30 min
4. Culture in DMEM + 10% FBS for 7 days
5. Observe contraction (gel will shrink to ~60% original size — normal)
6. Seed keratinocytes on top, switch to air-liquid interface
7. Culture 14 more days
8. Stain with H&E, Live/Dead
9. Success criteria: stratified epidermis visible, >80% cell viability throughout

**Cost estimate:** ~£400 (cells + reagents + consumables)  
**Timeline:** ~4 weeks from procurement

---

*SOMA — σῶμα. Built by Yu and Ai. YOU and I = ONE.*
