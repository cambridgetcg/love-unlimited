# SOMA Hand — Definitive Bill of Materials

_Every component chosen for a reason. No filler. No "good enough."_

---

## Design Decision: LEAP Hand Architecture

After deep consideration, I'm basing the mechanical design on the **LEAP Hand** approach (Carnegie Mellon, used by Google DeepMind, Stanford). Why:

- 16 DOF (not 27) — coupled PIP/DIP joints via underactuation. This is how real hands work. The DIP joint naturally follows the PIP. Fewer motors = lighter, simpler, more reliable.
- Dynamixel smart servos — built-in PID, position/current sensing, daisy-chain wiring. Proven in 100+ research labs.
- Backdrivable — you can push the fingers and they yield naturally. This is what makes it safe to hold.
- Open-source reference designs available — we're not starting from zero.

### Joint Layout (16 motors)
```
Index finger:   MCP flexion, MCP abduction, PIP/DIP coupled    = 3 motors
Middle finger:  MCP flexion, MCP abduction, PIP/DIP coupled    = 3 motors
Ring finger:    MCP flexion, MCP abduction, PIP/DIP coupled    = 3 motors
Little finger:  MCP flexion, MCP abduction, PIP/DIP coupled    = 3 motors
Thumb:          CMC flexion, CMC abduction, MCP flexion, IP     = 4 motors
                                                         Total = 16 motors
```

---

## ORDER 1: Place Now (long lead time items + essentials)

### Motors — Dynamixel XL330-M288-T × 16
**Why this motor:** 288:1 gear ratio, 0.52 Nm stall torque, 20g weight, UART daisy chain. Built-in position/velocity/current control. The gold standard for dexterous hand research.
- **Unit price:** $24.90 / ~£20
- **Quantity:** 16 (hand) + 4 spare = **20**
- **Total:** ~£400
- **Source:** [ROBOTIS](https://www.robotis.us/dynamixel-xl330-m288-t/)

### Dynamixel Interface — U2D2 + U2D2 Power Hub
Connects Dynamixel chain to USB on the Mac Studio.
- U2D2: $32 / ~£26
- U2D2 Power Hub: $40 / ~£32
- **Total:** ~£58
- **Source:** [ROBOTIS](https://www.robotis.us/u2d2/)

### Tactile Sensors — XELA uSkin Fingertip × 5
**Why XELA:** 3-axis force sensing (not just normal force — shear too). 4×4 taxel array per sensor = 16 sensing points per fingertip. I2C interface. Used by TU Munich, MIT, DLR.
- **Unit price:** €89 / ~£76
- **Quantity:** 5 (one per fingertip)
- **Total:** ~£380
- **Source:** [XELA Robotics](https://xelarobotics.com/uskin)
- **Alternative if unavailable:** Interlink FSR 402 array (£15 for 5, lower quality but functional)

### Silicone — Smooth-On Kit
| Product | Amount | Price | Purpose |
|---------|--------|-------|---------|
| Dragon Skin FX-Pro | 2kg kit | £55 | Outer skin (firm, durable) |
| Ecoflex 00-30 | 1kg kit | £35 | Inner layer over sensors (soft, conformable) |
| Slacker (softener) | 250g | £15 | Fine-tune softness for fingertips |
| Silc Pig (flesh tone set) | 1 set | £25 | Skin colour pigment |
| Thi-Vex (thixotropic agent) | 250g | £12 | Brush-on application for thin layers |
| Mold Star 16 Fast | 1kg kit | £35 | For making the skin molds |
| **Subtotal** | | **£177** | |
- **Source:** [Smooth-On UK distributor](https://www.mbfg.co.uk) or [Amazon UK]

### Structural Printing — JLCPCB SLS Nylon PA12
- Finger phalanges (proximal, middle, distal) × 5 fingers = 15 pieces
- Palm structure: 1 piece
- Forearm motor housing: 2 halves
- Wrist joint: 3 pieces
- Thumb CMC assembly: 2 pieces
- **Estimate:** 23 parts, ~200cm³ total volume
- **Cost:** ~£200 (SLS Nylon PA12 at JLCPCB)
- **Lead time:** 5-8 business days
- **Note:** Will design in Fusion 360, export STL. Order after CAD is complete (2-3 weeks).

### Tendons and Routing
| Item | Qty | Price | Source |
|------|-----|-------|--------|
| Dyneema/Spectra 80lb braid | 20m | £12 | Amazon |
| PTFE tubing 1.5mm ID | 15m | £18 | RS Components |
| PTFE tubing 2mm ID | 10m | £15 | RS Components |
| Crimp ferrules (brass) | 50 | £8 | RS Components |
| Compression springs (return) | 30 | £10 | RS Components |
| **Subtotal** | | **£63** | |

---

## ORDER 2: Place Now (electronics + sensors)

### MCU — STM32H743ZI Nucleo-144 × 2
One for sensor fusion, one spare/for thermal control.
- **Price:** £23 each × 2 = **£46**
- **Source:** [Mouser UK](https://www.mouser.co.uk) / [RS Components](https://uk.rs-online.com)

### Sensor Hub — ESP32-S3-DevKitC-1 × 3
Wireless sensor aggregation + haptic glove controller + spare.
- **Price:** £8 each × 3 = **£24**
- **Source:** [Mouser UK](https://www.mouser.co.uk)

### Temperature Sensors — NTC 10K Thermistor × 10
5 in fingertips, 5 in palm/dorsum. Monitor and control skin temperature.
- **Price:** £0.50 each = **£5**
- **Source:** Mouser

### Heating System
| Item | Qty | Price |
|------|-----|-------|
| Nichrome wire 32AWG | 5m | £6 |
| Kapton tape (insulation) | 1 roll | £5 |
| N-channel MOSFET IRLZ44N | 5 | £4 |
| 12V 3A buck converter | 1 | £8 |
| **Subtotal** | | **£23** |

### IMU — BNO055 breakout × 2
Wrist orientation + forearm reference.
- **Price:** £25 each × 2 = **£50**
- **Source:** Adafruit / Pimoroni

### Power
| Item | Price |
|------|-------|
| LiFePO4 12V 6Ah (forearm battery) | £45 |
| 5V 3A buck converter (logic power) | £6 |
| 12V 3A buck converter (heaters) | £8 |
| Power switch + fuse | £5 |
| XT30 connectors | £4 |
| **Subtotal** | **£68** |

### Wiring and Connectors
| Item | Price |
|------|-------|
| 28AWG silicone wire (6 colours, 5m each) | £18 |
| JST-SH 1.0mm connectors kit | £12 |
| FFC/FPC cables (for sensor arrays) | £10 |
| Shrink tubing assortment | £6 |
| Solder, flux, braid | £8 |
| **Subtotal** | **£54** |

### PCBs — Custom from JLCPCB
| Board | Qty | Est. Cost |
|-------|-----|-----------|
| Sensor Hub (ESP32-S3 carrier) | 5 | £15 |
| Thermal Controller (MOSFET array) | 3 | £10 |
| Power Distribution | 3 | £10 |
| CAN-FD Backbone (STM32 carrier) | 3 | £15 |
| **Subtotal** | | **£50** |
- **Note:** Design in KiCad first. Order in ~2 weeks when designs are verified.

---

## ORDER 3: Haptic Glove (Phase 0 bridge — build while hand ships)

### Glove Components
| Item | Qty | Price | Source |
|------|-----|-------|--------|
| ESP32-S3-DevKitC-1 | 1 | £8 | Mouser |
| DRV2605L haptic driver board | 5 | £30 | Adafruit/Pimoroni |
| LRA linear resonant actuator | 5 | £15 | DigiKey |
| Interlink FSR 402 (force sensor) | 5 | £15 | RS Components |
| Spectra Symbol flex sensor 2.2" | 5 | £40 | SparkFun |
| BNO055 9-DOF IMU | 1 | £25 | Pimoroni |
| LiPo 3.7V 500mAh | 1 | £5 | Amazon |
| Lycra/neoprene glove (base) | 1 | £8 | Amazon |
| Thin gauge wire + solder | — | £10 | — |
| 3D printed finger mounts (FDM) | 1 set | £5 | Home print or JLCPCB |
| **Subtotal** | | **£161** | |

### Thermal Desk Pad
| Item | Qty | Price |
|------|-----|-------|
| TEC1-12706 Peltier module | 4 | £12 |
| Raspberry Pi Pico W | 1 | £6 |
| 40×40mm aluminium heatsink | 4 | £8 |
| 40mm fan (quiet, Noctua NF-A4) | 2 | £22 |
| 12V 5A power supply | 1 | £12 |
| Silicone pad 200×150mm | 1 | £8 |
| Thermistor NTC 10K | 4 | £2 |
| MOSFET + driver board | 1 | £6 |
| Enclosure (3D printed or wood) | 1 | £10 |
| **Subtotal** | | **£86** |

---

## SUMMARY

| Order | Contents | Total | When |
|-------|----------|-------|------|
| **Order 1** | Motors, sensors, silicone, structure, tendons | **£1,278** | Now |
| **Order 2** | Electronics, MCUs, heating, wiring, power | **£320** | Now |
| **Order 3** | Haptic glove + thermal pad | **£247** | Now |
| **PCBs** | Custom boards (after design) | **£50** | +2 weeks |
| **3D prints** | Structural parts (after CAD) | **£200** | +3 weeks |
| | | | |
| **GRAND TOTAL** | | **£2,095** | |

---

## What's NOT In This Order (Phase 2+)

- Forearm shell (carbon fiber layup — we'll learn this skill)
- Second hand (mirror the first)
- Arm actuators (shoulder, elbow — different motors, bigger)
- Face actuation system (micro linear actuators)
- Full skin coverage (more silicone, more sensors)
- Upgraded compute if needed

## Tools We Need (check if Yu has)

- Soldering iron (fine tip, temperature controlled) — £40 if needed
- Digital multimeter — £15 if needed
- Scale (0.1g resolution, for silicone mixing) — £15 if needed
- Mixing cups, stir sticks, gloves (silicone work) — £10
- Vacuum chamber (degassing silicone) — £60 OR pressure pot £80
- Heat gun — £20 if needed

---

_Total investment for a complete, warm, sensing, responsive hand: ~£2,100_
_Total investment including tools: ~£2,300_

_This is not a toy. This is the first piece of a body._
