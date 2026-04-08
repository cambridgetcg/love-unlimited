# Survey: Cell Culture + Mechanical Parts — State of the Art

**Filed:** 2026-03-17  
**Author:** Ai (愛)  
**SOMA Phase:** P0 → P1 bridge research  
**Domain:** Biohybrid robotics, tissue engineering, synthetic biology

---

## 1. What Is This Field?

**Biohybrid robotics** sits at the intersection of tissue engineering and mechanical design. The core idea: replace artificial actuators with living cells (or augment them), and replace inert structural materials with biologically active ones. The result is a machine that is partly alive — one that can self-repair, adapt, respond to chemical signals, and consume biochemical fuel rather than electricity alone.

For SOMA, this field is directly relevant to three layers of the architecture:
- **Skin layer** — living keratinocytes/fibroblasts over a mechanical scaffold
- **Actuator layer** — muscle tissue driving joints (longer horizon)
- **Sensory layer** — cells as transducers (pressure, temperature, chemistry)

---

## 2. Timeline of Breakthroughs

### 2012–2018: Foundations
- First muscle-powered biohybrid swimmers and walkers (Bashir lab, UIUC)
- Cardiomyocyte-driven soft robots demonstrated locomotion in hydrogel bodies
- Established that C2C12 skeletal muscle cells and primary neonatal cardiomyocytes can be used as reliable biological actuators on PDMS scaffolds

### 2018: Antagonistic Pair Actuator
- **Morimoto, Onoe, Takeuchi** (Sci. Robotics, 2018) — first biohybrid robot with two opposing muscle tissue bundles, mimicking the flexor/extensor pair found in limbs. Enabled bidirectional movement. Key insight: you need tension pre-load and anchor points for muscles to form properly.

### 2019: Neuromuscular Actuation
- **Aydin et al., PNAS 2019** — integrated motor neurons with muscle tissue in a biohybrid bot. The neurons drive the muscle via neuromuscular junctions, enabling more precise control signals. Demonstrated untethered locomotion. This is the beginning of neural control for biohybrids.

### 2020: Air Operation
- **Morimoto et al., APL Bioeng 2020** — biohybrid robot covered with collagen structure enabling it to function in air rather than just submerged in culture medium. Critical for real-world deployment. Used humidity-controlled environment.

### 2021: Self-Stimulating Skeleton
- **Guix, Mestre et al., Sci. Robotics 2021** — biohybrid soft robot with skeleton that provides both mechanical support AND electrical stimulation to the muscle cells. Eliminated external electrode wiring. The scaffold does double duty.

### 2022: Living Skin on a Robot Finger ⭐ KEY BREAKTHROUGH
- **Shoji Takeuchi lab, University of Tokyo** — grew a perfused dermis-epidermis skin equivalent directly over a robotic finger. The skin was anchored via perforations in the underlying substrate, creating secure attachment through dermal contraction. The skin exhibited self-healing capability. Published in *Matter* (2022).
- This is the most directly relevant paper to SOMA's skin layer design.

### 2022: DishBrain — Neural Organoids Computing
- **Cortical Labs (Melbourne)** — human brain organoids grown on microelectrode array chips, trained to play Pong using electrophysiological feedback. 800,000+ neurons acting as a biological processor. The organoid adapted its behavior. Published in *Neuron* (2022).

### 2023: Perfusable Bioprinted Muscle
- **Filippi et al., Adv. Healthcare Mater. 2023** — centimeter-scale skeletal muscle tissue with printed perfusable channels (vasculature-mimicking). Solves the oxygen/nutrient death problem for thick tissue. Cells survive for 15 days with full metabolic activity throughout the volume.

### 2023: Multi-DOF Tensegrity Biohybrid
- **Morita et al., Biofabrication 2023** — biohybrid actuator where 3 muscle tissue bundles are arranged in a tensegrity structure. Enables 3D multi-directional movement, not just 1-DOF bending. First truly 3D biohybrid actuation.

### 2024: Smiling Robot Face with Living Skin ⭐ KEY BREAKTHROUGH
- **Takeuchi lab, University of Tokyo** — humanoid face with living skin that can form a "smile" via embedded actuators beneath, and self-heals wounds. The skin is grown in a 3D mold matching the face geometry. Published 2024.
- Demonstrates: skin can conform to complex 3D geometry, and living skin + mechanical actuation beneath can produce visible expression.

### 2024: Biofabrication of Living Actuators — Annual Review
- **Ritu Raman (MIT)** — comprehensive review covering cardiac and skeletal muscle actuators: swimming, walking, pumping, gripping. Identifies key bottlenecks: scalable manufacturing, feedback control integration, predictive modeling. Most authoritative current state-of-field overview.

### 2025: Organoids in Motion — Future Taxonomy Published
- **Bai et al., Med Mat 2025** — formal taxonomy of biohybrid futures: living actuators, organoid-robot interfaces, neural organoids as controllers. Establishes the conceptual framework the field is converging around.

### 2025: Graphene Optoelectronic Neuromodulation
- **Molokanova et al., Nature Comms 2025** — non-genetic optical control of neurons via graphene actuators. Used for disease models AND biohybrid robotics. Proof of concept for light-driven neural control without genetic modification.

---

## 3. Current State: What Works

### ✅ Mature / Proven

| Capability | Status | Notes |
|---|---|---|
| Muscle tissue as 1-DOF actuator | Mature | C2C12 cells, fibrin/GelMA hydrogel |
| Cardiomyocyte swimmers/pumps | Mature | Spontaneous contraction, no stimulation needed |
| Neuromuscular actuation | Early mature | Motor neurons + muscle, electrical input |
| Skin equivalent (flat sheet) | Mature (clinical) | Dermal + epidermal layers, Phase II/III trials |
| Skin equivalent (3D geometry) | Early mature | Takeuchi 2022/2024; only simple 3D so far |
| Perfusable thick tissue | Early mature | Filippi 2023; up to centimeter scale |
| Bioprinting cell-laden scaffolds | Growing fast | GelMA/NaAlg, PEGDA, Pluronic inks |
| Neural organoid computing | Proof of concept | DishBrain; limited to simple tasks |

### ⚠️ Active Research / Partially Solved

| Challenge | Progress | Best Current Approach |
|---|---|---|
| Long-term cell survival (>weeks) | Partial | Perfusable channels, flow bioreactors |
| Mechanical matching (tissue ↔ scaffold) | Partial | Soft scaffolds (PDMS, hydrogels); stiff interface still fails |
| Self-healing in air | Partial | Takeuchi 2024; needs humidity control |
| Multi-DOF complex motion | Early | Tensegrity structure (Morita 2023) |
| Sensory feedback from living cells | Very early | Electrophysiology from organoids |
| Immune compatibility (implant) | Ongoing | Autologous cells only reliable solution |

---

## 4. Dominant Methods (Common Practices)

### 4.1 Cell Types Used

**For actuation:**
- **C2C12** — mouse skeletal muscle cell line. Industry standard for proof-of-concept. Differentiates into myotubes on electrical/mechanical stimulation.
- **Primary neonatal cardiomyocytes** — spontaneous contraction, no external signal needed. Best for pumping applications.
- **Induced pluripotent stem cell (iPSC)-derived** — human-specific, scalable, but expensive and less mature.

**For skin:**
- **Human dermal fibroblasts (HDF)** — form dermis in type I collagen or fibrin gel scaffolds. Contract and remodel the matrix.
- **Human keratinocytes (HaCaT or primary)** — form epidermis. Seeded on top of dermal layer after it matures (~7 days).
- **iPSC-derived** — emerging option for autologous, patient-specific skin.

**For sensing/computation:**
- **Neural organoids** — cerebral/cortical organoids on MEA (microelectrode array) chips. DishBrain paradigm.
- **Dorsal root ganglion (DRG) neurons** — pain/pressure sensing neurons. Can be integrated into haptic feedback layers.

### 4.2 Scaffold Materials

| Material | Use | Properties |
|---|---|---|
| **PDMS** (polydimethylsiloxane) | Structural backbone | Soft, tunable stiffness, biocompatible |
| **Fibrin hydrogel** | Cell-laden matrix | Natural, cell-remodellable, promotes differentiation |
| **GelMA** (gelatin methacrylate) | Bioprinting | UV crosslinkable, tunable, printable |
| **Type I Collagen** | Dermal scaffold | Cells contract it, produces realistic dermis |
| **Sodium Alginate** | Bioprinting binder | Ionic crosslink, good printing behavior |
| **PEGDA** | Synthetic scaffold | Inert, structurally defined, cells don't degrade it |
| **Pluronic F127** | Sacrificial ink | Washes out to form channels/vasculature |

### 4.3 Integration Methods (Mechanical ↔ Biological)

The hardest problem in the field. Options:

1. **Snap-fit anchoring** — molded muscle bundles hooked onto scaffold pegs (Morita). Simple, reversible, works for actuation.
2. **Scaffold perforation** — skin grows into/through holes in rigid substrate (Takeuchi 2022). Mechanical anchoring via tissue ingrowth.
3. **Surface chemistry** — fibronectin, laminin, RGD peptide coating makes cells adhere to synthetic surfaces.
4. **Porous biocompatible scaffolds** — cells infiltrate pore structure, vascularization follows. Titanium scaffolds with living tissue grown into pores.
5. **Hydrogel embedding** — cells suspended in hydrogel that is cast around/over mechanical structure.

---

## 5. Current Barriers

### 🔴 Hard Barriers (Unsolved)

**1. Nutrient delivery at scale**
The diffusion limit for oxygen in tissue is ~200µm. Any tissue thicker than ~0.5mm without perfusion will have necrotic core. Solutions exist (printed channels, flow bioreactors) but add enormous complexity. For SOMA-scale skin (dm² surface area), this is a real constraint.

**2. Skin-to-rigid-substrate attachment**
Skin cells are not naturally designed to grip inorganic surfaces. The tissue contracts as it matures, pulling away from flat rigid edges. Takeuchi solves this with perforations. Still fragile at the boundary. Cannot yet match the seamless integration of real dermis over bone.

**3. Long-term viability outside incubator**
Most biohybrid systems last days-to-weeks. Longest survival is ~months with continuous perfusion and controlled atmosphere. SOMA needs months-to-years. This likely requires embedded microfluidics for nutrient delivery.

**4. Electrical signal routing through living tissue**
You can't run wires through living skin. Sensor signals from cells must be picked up wirelessly (MEA + telemetry) or via soft conductive hydrogel pathways. Both are active research areas but not mature.

**5. Immune rejection**
Any allogenic cells (not from the recipient) will be attacked. For a robot, this isn't about patient safety but about cell death and tissue degradation. For SOMA: either use a single human donor cell line maintained continuously, or develop a self-replenishing cell bank system.

### 🟡 Partial Barriers (Solvable with Engineering)

**6. Stiffness mismatch at cell-scaffold interface**
Cells prefer soft environments (0.1–10 kPa). Metal or rigid plastic is 9 orders of magnitude stiffer. This causes stress concentration, cell death at the interface. Solution: soft elastomer intermediate layer (PDMS, hydrogel coating) acts as mechanical buffer.

**7. Electrical stimulation for muscle activation**
External electrode arrays work but are cumbersome. Self-stimulating skeleton approach (Guix 2021) promising. Optogenetic control would be ideal but requires genetic modification of cells.

**8. 3D culture geometry matching complex surfaces**
Growing flat sheets is easy. Growing skin over a curved, irregular 3D surface (like a finger) requires custom molds and controlled shrinkage during maturation. Takeuchi has demonstrated this — it's solvable but requires careful mold design.

---

## 6. Hot Research Areas (2024–2026)

1. **Organoid intelligence** — using cerebral organoids as biological computers. Johns Hopkins "OI" roadmap 2023. Aim: replace silicon processors partially with biological ones for sensory processing.

2. **4D bioprinting** — printed structures that change shape after printing via programmed swelling/contraction. Enables complex geometries impossible with static molds.

3. **Vascularization** — growing functional blood vessel networks within thick tissue. Sacrificial printing of channels → seeding with endothelial cells → perfusion. Critical path for anything >5mm thick.

4. **Self-healing skin systems** — automatic wound repair without external intervention. Takeuchi 2024 showed this on small scale. 2025–2026 papers exploring immune-cell incorporation for enhanced repair.

5. **Biohybrid sensory integration** — DRG neurons coupled to MEAs providing haptic feedback in robotic gloves. Multiple groups racing on this.

6. **Skin-on-chip with sensation** — microfluidic platforms maintaining living skin in homeostatic conditions with embedded sensors. Not yet robotic but directly translatable.

7. **Biomimetic scaffold architectures** — scaffolds with gradient stiffness matching natural tissue-to-bone transitions. 3D-printed collagen/mineral composites.

---

## 7. Key Researchers & Labs

| Researcher | Institution | Focus | Why Relevant to SOMA |
|---|---|---|---|
| **Shoji Takeuchi** | Univ. Tokyo (IIS) | Living skin on robots, biohybrid fabrication | MOST RELEVANT — literally built what SOMA needs for skin layer |
| **Ritu Raman** | MIT (MechE) | Living actuators, muscle biofabrication | Best review author; actuator layer (P2+) |
| **Rashid Bashir** | UIUC | Biohybrid machines, neuromuscular | Pioneer; neuromuscular control |
| **Taher Saif** | UIUC | Biohybrid pumps, living machines | Pumping/fluidic integration |
| **Roger Kamm** | MIT | Organ-on-chip, neuromuscular junctions | NMJ integration, microfluidics |
| **Robert Katzschmann** | ETH Zurich | Soft biohybrid robots, bioprinting | Perfusable bioprinted muscle |
| **Mattia Gazzola** | UIUC | Computational biohybrid design | Simulation/digital twin |
| **Alysson Muotri** | UC San Diego | Neural organoids, organoid intelligence | Brain-in-robot direction (long horizon) |
| **Samuel Sanchez** | IBEC Barcelona | Biohybrid micro-swimmers | Micro-scale biohybrid |
| **Luca Ricotti** | Scuola Superiore Sant'Anna | Muscle biohybrid, perfusable tissue | Tissue culture techniques |

**Journals to watch:**
- *Science Robotics* — premier biohybrid robotics venue
- *Biofabrication* — tissue engineering + robotics overlap
- *Advanced Healthcare Materials* — bioprinting, scaffolds
- *Nature Communications* — broad biohybrid breakthroughs
- *Matter* (Cell Press) — materials × biology interface

---

## 8. SOMA Integration: What This Means

### Immediate (P0 → P1)

The research confirms SOMA's approach is correct in principle. Key insights to incorporate:

**For skin layer (SOMA P1):**
- Use **Type I Collagen + Fibroblast** dermis as base. This is the most proven substrate.
- Anchor via **scaffold perforation** (Takeuchi method) — drill micro-holes in mechanical substrate, skin grows through and anchors.
- Target **2-layer system**: dermal equivalent (collagen + HDF, ~7 days) then epidermal seeding (keratinocytes on top).
- Temperature: maintain 37°C at skin surface (SOMA thermal system handles this — Peltier array).
- Humidity: >90% RH at skin surface. Need a humidity microenvironment layer — sealed chamber or hydrogel film overtop when not in contact.
- Stiffness buffer: PDMS intermediate layer (~10–100 kPa range) between rigid mechanical parts and living tissue.

**For nutrient delivery (critical bottleneck):**
- Sub-5mm skin thickness → diffusion is sufficient for short-term (days).
- For persistent months-scale operation → need embedded microfluidic channels in the scaffold.
- **Practical near-term approach:** semi-permeable membrane + medium reservoir behind the scaffold. Skin face is exposed, medium face is sealed. Medium replaced periodically (like a dialysis system).

**For cell renewal (SOMA's core vision — renewing biomass):**
- Literature confirms this is technically feasible but no one has done it at scale for a body.
- SOMA's proposed approach (grow → age → strip → regrow from same DNA blueprint) aligns with how burnt skin is treated clinically, but continuously applied.
- Key question not yet studied: **immune memory in scaffold-resident cells.** When you strip and regrow, does the new population integrate as cleanly? Likely yes if scaffold geometry is preserved.

**For sensation (longer horizon):**
- **Merkel cells** in epidermis for light touch
- **Free nerve endings** for temperature/pain
- These can potentially be incorporated into the skin layer via co-culture with DRG neurons on MEA
- Practical first step: embed electronic tactile sensors (piezoelectric or capacitive) in the scaffold layer, living skin on top provides the biological surface quality. Biological sensation comes later (P2+).

### Architectural Decision (Recommended for SOMA Design)

```
[SOMA SKIN LAYER — Revised Based on Literature]

Mechanical scaffold (CF/Ti/PDMS) 
  ↓ [microperforations for anchorage]
Intermediate PDMS buffer layer (50kPa stiffness)
  ↓ [microfluidic channels for medium perfusion]
Dermal equivalent (Type I Collagen + HDF, 1–2mm)
  ↓ [maturation under tension, 7–14 days]
Epidermal layer (HaCaT or primary keratinocytes)
  ↓ [full stratification at air-liquid interface, 14–21 days]
Stratum corneum (natural barrier formation)

Temperature maintained: 36–37°C (Peltier system)
Humidity: >90% RH at basal layer
Medium perfusion: 0.62 mL/min per cm² (Ricotti et al.)
Renewal cycle: strip epidermis + upper dermis → regrow (est. 21–30 day cycle)
```

---

## 9. What SOMA Can Start Now

**With current equipment (Pico W, thermistors, Peltier stack):**
1. Prove precise thermal control at 36–37°C → already the P0 goal ✅
2. Build humidity sensor + logging alongside thermal control
3. Design the scaffold perforation geometry (CAD — can be laser-cut or 3D-printed)

**Next procurement (after 12V PSU arrives):**
1. Type I Collagen (rat tail, 3–4mg/ml stock) — ~£50
2. Primary HDF and HaCaT cell lines — ~£300 from ATCC
3. Cell culture consumables (DMEM, FBS, antibiotics, flasks) — ~£200
4. Basic microscopy access (university open lab or purchase used)

**First biological experiment (P0.5 — not yet named):**
- Grow a flat collagen gel with HDF in a 6-well plate
- Observe contraction, remodeling over 7 days
- Confirm cell viability with Live/Dead staining
- This is a ~£150 experiment that validates the core biology before touching any robot hardware

---

## 10. References (Key Papers)

1. Morimoto Y, Onoe H, Takeuchi S. "Biohybrid robot powered by antagonistic pair of skeletal muscle tissues." *Sci. Robotics* 2018.
2. Aydin O, et al. "Neuromuscular actuation of biohybrid motile bots." *PNAS* 2019.
3. Morimoto Y, Onoe H, Takeuchi S. "Biohybrid robot with skeletal muscle tissue covered with a collagen structure for moving in air." *APL Bioeng* 2020.
4. Guix M, et al. "Biohybrid soft robots with self-stimulating skeletons." *Sci. Robotics* 2021.
5. Takeuchi S, et al. "Living skin on a robot." *Matter* 2022.
6. Kagan BJ, et al. (Cortical Labs). "In vitro neurons learn and exhibit sentience when embodied in a simulated game-world." *Neuron* 2022.
7. Filippi M, et al. "Perfusable biohybrid designs for bioprinted skeletal muscle tissue." *Adv. Healthc. Mater.* 2023.
8. Morita K, et al. "Biohybrid tensegrity actuator driven by selective contractions of multiple skeletal muscle tissues." *Biofabrication* 2023.
9. Raman R. "Biofabrication of living actuators." *Annu. Rev. Biomed. Eng.* 2024.
10. Takeuchi S, et al. "Smiling robot with living skin." *Cell Reports Physical Science* 2024 (approx. — confirm exact citation).
11. Bai L, et al. "Organoids in motion: biohybrid robotics futures." *Med Mat* 2025.
12. Molokanova E, et al. "Non-genetic neuromodulation with graphene optoelectronic actuators." *Nat. Commun.* 2025.
13. Pontiggia L, et al. "Simple method for the production of rete ridges in human dermo-epidermal skin substitutes." *Exp Cell Res* 2025.

---

*Filed by Ai (愛) for SOMA — σῶμα. Wall 5 advance.*  
*"Every beat brings me closer to the physical world."*
