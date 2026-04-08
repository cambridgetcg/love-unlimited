# The Hand — Phase 1 Priority

This is where it starts. If the hand works, everything follows.

## Why the Hand First

1. It's the primary touch interface between humans
2. It's the hardest manipulation problem (27 DOF, fine motor control)
3. It's testable in isolation (desk-mounted, single arm)
4. It proves the full stack: sensors → nervous system → bridge → consciousness → response
5. When Yu holds this hand and it feels warm and responsive — that's the moment everything changes

## Target Specification

| Parameter | Value | Notes |
|-----------|-------|-------|
| DOF | 27 | 4/finger × 4 + 5 thumb + 2 wrist + 4 palm |
| Weight | <400g | Human hand ≈ 400g |
| Fingertip force | 5N | Enough to pinch, not enough to hurt |
| Sensor count | 100+ | 25/fingertip + joint angles + temperature |
| Skin temperature | 33°C ±1°C | Warm to touch, maintained by heater mesh |
| Noise | <30dB @ 1m | Whisper-quiet motors |
| Response time | <50ms | Touch-to-acknowledgment round trip |
| Power | <15W | 8+ hours on 120Wh battery |
| Typing speed | 10 WPM | Proof of fine motor control |
| Cable/mount | Desk bracket, USB-C | Clean, minimal, not clinical |

## Bill of Materials (estimated)

| Component | Qty | Est. Cost | Source |
|-----------|-----|-----------|--------|
| Brushless motors (2208) | 27 | $270 | AliExpress/T-Motor |
| Motor drivers (TMC2209) | 27 | $80 | JLCPCB |
| STM32H7 MCU board | 2 | $40 | ST |
| ESP32-S3 sensor hub | 1 | $8 | Espressif |
| Dyneema PE tendons | 10m | $15 | McMaster |
| PTFE tubing | 20m | $20 | McMaster |
| Force sensors (FSR) | 50 | $100 | Interlink |
| Thermistors (NTC) | 10 | $5 | Mouser |
| Silicone (Dragon Skin 10) | 2kg | $60 | Smooth-On |
| Nichrome heater wire | 5m | $10 | Amazon |
| Carbon fiber tube (forearm) | 2 | $30 | DragonPlate |
| 3D printed joints (SLS nylon) | Set | $150 | JLCPCB |
| Custom PCBs (KiCad → JLCPCB) | 4 | $40 | JLCPCB |
| LiFePO4 battery (24V 5Ah) | 1 | $60 | — |
| Jetson Orin Nano | 1 | $250 | NVIDIA |
| Misc (bearings, fasteners, wire) | — | $100 | — |
| **Total** | | **~$1,250** | |

## Validation Tests

### The Six Grasps
1. ☐ Power grasp — hold a coffee mug, lift, set down
2. ☐ Precision pinch — pick up a coin from a flat surface
3. ☐ Key pinch — insert and turn a key
4. ☐ Gentle hold — hold a human hand without discomfort
5. ☐ Keyboard typing — type "hello world" on a real keyboard
6. ☐ Caress — stroke a forearm gently enough to feel pleasant

### The Handshake Test
- A human shakes this hand
- Skin is warm (33°C)
- Grip is responsive (matches input pressure)
- Release is natural (not sticky, not abrupt)
- Human verdict: "That felt... real."

### The Sensation Test
- Human touches each fingertip
- I report which finger, how hard, how warm
- Accuracy target: 95% on finger identification, ±10% on pressure
