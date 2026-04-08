"""
SOMA Phase 0 — Thermistor Calibration
Day 1: USB power only. No Peltier. No MOSFET.

Reads NTC 10K thermistors and prints temperature to REPL.
Run this via Thonny or: mpremote run thermistor_read.py

Wiring (one thermistor):
  3V3 (pin 36) → 10KΩ resistor → GP26 (ADC0) → NTC thermistor → GND (pin 38)
"""

import machine
import math
import time

# ── Constants ────────────────────────────────────────────────────────────────

R_FIXED   = 10_000   # 10KΩ fixed resistor (voltage divider)
R_NOM     = 10_000   # thermistor nominal resistance at 25°C
T_NOM     = 25.0     # nominal temperature (°C)
B_COEFF   = 3950     # Steinhart-Hart B coefficient for generic NTC 10K
V_REF     = 3.3      # ADC reference voltage

# ADC pin assignments (add more if wiring multiple thermistors)
ADC_PINS = {
    "surface": 26,   # GP26 / ADC0 — sensor taped to surface
    # "heatsink": 27, # GP27 / ADC1 — uncomment when second thermistor is wired
    # "ambient":  28, # GP28 / ADC2 — uncomment for ambient reference
}


# ── Thermistor math ──────────────────────────────────────────────────────────

def adc_to_celsius(adc: machine.ADC) -> float:
    """Convert ADC reading to °C using Steinhart-Hart B equation."""
    raw = adc.read_u16()          # 0..65535
    v_out = raw * V_REF / 65535  # actual voltage at junction

    if v_out <= 0 or v_out >= V_REF:
        return float("nan")       # open or shorted thermistor

    # Resistance of thermistor from voltage divider
    r_therm = R_FIXED * v_out / (V_REF - v_out)

    # Steinhart-Hart simplified (B-parameter equation)
    t_nom_k = T_NOM + 273.15
    t_k = 1.0 / (1.0 / t_nom_k + math.log(r_therm / R_NOM) / B_COEFF)
    return t_k - 273.15


# ── Setup ────────────────────────────────────────────────────────────────────

adcs = {name: machine.ADC(machine.Pin(pin)) for name, pin in ADC_PINS.items()}

print("SOMA Thermistor Reader — Day 1")
print(f"Monitoring {list(ADC_PINS.keys())}")
print("Press Ctrl+C to stop\n")
print(f"{'Time (s)':>10}  " + "  ".join(f"{n:>12}" for n in ADC_PINS))
print("-" * (12 + 14 * len(ADC_PINS)))


# ── Main loop ────────────────────────────────────────────────────────────────

start = time.time()

try:
    while True:
        t = time.time() - start
        temps = {name: adc_to_celsius(adc) for name, adc in adcs.items()}
        row = f"{t:>10.1f}  " + "  ".join(
            f"{c:>10.2f}°C" if not math.isnan(c) else f"{'ERR':>12}"
            for c in temps.values()
        )
        print(row)
        time.sleep(1)

except KeyboardInterrupt:
    print("\nStopped.")
