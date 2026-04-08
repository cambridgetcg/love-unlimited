# SOMA Hardware Stack

## Ordered ✅

### Order 1 — Robotis (backorder, ~few weeks)
| Item | Qty | Est. Cost |
|---|---|---|
| Robotis Dynamixel XL330-M288-T | 20 | ~£480 |
| Robotis DYNAMIXEL Starter Set [INTL] | 1 | ~£90 |
*Starter Set includes: U2D2 adapter, U2D2 Power Hub, SMPS 12V PSU, cables*

### Order 2 — DigiKey (2026-03-07) ✅ ARRIVED 2026-03-17
| # | DigiKey PN | MFR PN | Description | Qty | Unit £ | Total £ | Status |
|---|---|---|---|---|---|---|---|
| 1 | 1027-1001-ND | 30-81794 | Interlink FSR 402 (connector tail) | 5 | 4.30 | 21.52 | ✅ |
| 2 | 1965-ESP32-S3-DEVKITC-1-N8R8-ND | ESP32-S3-DEVKITC-1-N8R8 | Espressif ESP32-S3 Dev Board | 2 | 11.23 | 22.46 | ✅ |
| 3 | 1471-MIKROE-3828-ND | MIKROE-3828 | Peltier TEC1-12706 | 4 | 8.08 | 32.32 | ✅ |
| 4 | 5151-MPD-810H-ND | MPD-810H | Magic Power DC-DC ATX PSU 136W | 1 | 111.24 | 111.24 | ✅ |
| 5 | 1670-VG0832013D-ND | VG0832013D | Vybronics LRA Motor 235Hz | 10 | 2.09 | 20.86 | ✅ |
| 6 | 1027-1018-ND | 34-00015 | Interlink FSR 402 (bare leads) | 5 | 6.02 | 30.12 | ✅ |
| 7 | 905-FS2-L-055-253-ST-ND | FS2-L-055-253-ST | Spectra Symbol Flex Sensor 55mm ST | 5 | — | — | ✅ check |
| | | | **DigiKey Subtotal** | | | **~£270+** | |

## Already Have
| Item | Notes |
|---|---|
| Mac Studio | SOMA brain — ready for setup |
| MacBook Air | Dev machine (current) |

## To Order — Priority 1 (Now)
| Item | Qty | Est. Cost | Why |
|---|---|---|---|
| ESP32-S3-DevKitC-1 | 2 | ~£16 | Sensor hubs, haptic glove MCU |
| Peltier TEC1-12706 modules | 4 | ~£12 | Thermal desk pad |
| 40×40mm heatsinks + fans | 4 | ~£15 | Peltier cooling |
| 12V 10A PSU | 1 | ~£15 | Peltier power |
| Aluminium plate 200×150×3mm | 1 | ~£10 | Thermal pad surface |
| Thermal paste + pads | 1 | ~£8 | Peltier assembly |
| LRA haptic motors | 10 | ~£20 | Glove fingertip feedback |
| Flex sensors (4.5") | 5 | ~£30 | Glove finger tracking |
| Force sensitive resistors | 5 | ~£15 | Glove pressure sensing |
| Neoprene glove (fingerless) | 1 | ~£8 | Glove base |
| Wiring/connectors/proto boards | — | ~£20 | Assembly |
| **Subtotal** | | **~£170** | |

## To Order — Priority 2 (In 1-2 weeks)
| Item | Qty | Est. Cost | Why |
|---|---|---|---|
| SynTouch NumaTac sensors | 5 | ~£500 | Biomimetic fingertip sensing |
| Dragon Skin FX-Pro (1kg kit) | 1 | ~£80 | Silicone skin |
| Dyneema braided line (50lb) | 1 roll | ~£15 | Tendons |
| PTFE Bowden tubes (2mm ID) | 10m | ~£10 | Tendon routing |
| Markforged print service (or Onyx filament) | — | ~£200 | Hand structure |
| STM32H7 Nucleo-144 | 1 | ~£45 | Real-time motor control backup |
| **Subtotal** | | **~£850** | |

## To Order — Priority 3 (When hand works)
| Item | Qty | Est. Cost | Why |
|---|---|---|---|
| Additional XL330-M288-T | 7 | ~£170 | 27 DOF full hand (20 → 27) |
| Forearm housing materials | — | ~£100 | Motor + wiring enclosure |
| Wrist joint assembly | 1 | ~£80 | 3-DOF wrist |
| Second hand kit (mirror) | — | ~£1,200 | Left hand |
| **Subtotal** | | **~£1,550** | |

## Budget Summary
| Phase | Cost | Status |
|---|---|---|
| Dynamixels + Starter Set | ~£570 | ✅ Ordered |
| Priority 1 (glove + thermal) | ~£170 | 🔜 Next |
| Priority 2 (sensors + skin) | ~£850 | ⏳ 1-2 weeks |
| Priority 3 (full hand + second) | ~£1,550 | ⏳ After first hand works |
| **Total all phases** | **~£3,140** | |

## Notes
- Mac Studio is the SOMA brain (no Jetson needed)
- Dynamixels eliminate need for ODrive Pro controllers (built-in control)
- XL330 is the same servo used in CMU LEAP Hand
- First milestone: haptic glove + thermal pad (feel each other)
- Second milestone: single right hand, tendon-driven, silicone skin
- Third milestone: mirror left hand, wrist, forearm
