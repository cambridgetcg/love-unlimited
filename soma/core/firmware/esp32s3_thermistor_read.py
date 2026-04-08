"""
SOMA Phase 0 — Thermistor Calibration (ESP32-S3 DevKitC-1)
Day 1: USB power only. No Peltier. No MOSFET.

Reads NTC 10K thermistors and prints temperature to REPL.

Wiring (one thermistor):
  3V3 (pin next to GND label) → 10KΩ resistor → GPIO1 → NTC thermistor → GND

ESP32-S3 DevKitC-1 ADC pins (ADC1 only — ADC2 conflicts with WiFi):
  GPIO1  = ADC1 channel 0  ← surface thermistor (use this first)
  GPIO2  = ADC1 channel 1  ← heatsink thermistor
  GPIO3  = ADC1 channel 2  ← ambient thermistor
  GPIO4  = ADC1 channel 3  ← spare

NOTE: ESP32-S3 ADC is 12-bit (0-4095), not 16-bit like Pico W.
      Attenuation set to 11dB for full 0-3.3V range.
"""

from machine import Pin, ADC
import math
import time

# ── Constants ────────────────────────────────────────────────────────────────

R_FIXED  = 10_000    # 10KΩ fixed resistor (voltage divider)
R_NOM    = 10_000    # thermistor nominal resistance at 25°C
T_NOM    = 25.0      # nominal temperature (°C)
B_COEFF  = 3950      # Beta coefficient for NTC 10K
V_REF    = 3.3       # ADC reference voltage
ADC_MAX  = 4095      # ESP32-S3: 12-bit ADC

# ADC pin assignments — GPIO numbers (ADC1 only)
ADC_PINS = {
    "surface":  1,   # GPIO1 — sensor taped to surface
    # "heatsink": 2, # GPIO2 — uncomment when second thermistor wired
    # "ambient":  3, # GPIO3 — uncomment for ambient reference
}


# ── Thermistor math ──────────────────────────────────────────────────────────

def make_adc(gpio_num):
    """Create and configure ADC for ESP32-S3."""
    adc = ADC(Pin(gpio_num))
    adc.atten(ADC.ATTN_11DB)   # Full 0–3.3V range
    adc.width(ADC.WIDTH_12BIT) # 12-bit resolution (0–4095)
    return adc

def adc_to_celsius(adc):
    """Convert ADC reading to °C using Steinhart-Hart B equation."""
    raw = adc.read()  # 0..4095 on ESP32-S3
    v_out = raw * V_REF / ADC_MAX

    if v_out <= 0 or v_out >= V_REF:
        return float("nan")

    # Thermistor resistance from voltage divider
    r_therm = R_FIXED * v_out / (V_REF - v_out)

    # Steinhart-Hart B-parameter equation
    t_nom_k = T_NOM + 273.15
    t_k = 1.0 / (1.0 / t_nom_k + math.log(r_therm / R_NOM) / B_COEFF)
    return t_k - 273.15

def read_averaged(adc, samples=16):
    """Average multiple readings to reduce noise."""
    total = sum(adc.read() for _ in range(samples))
    raw = total // samples
    v_out = raw * V_REF / ADC_MAX
    if v_out <= 0 or v_out >= V_REF:
        return float("nan")
    r_therm = R_FIXED * v_out / (V_REF - v_out)
    t_nom_k = T_NOM + 273.15
    t_k = 1.0 / (1.0 / t_nom_k + math.log(r_therm / R_NOM) / B_COEFF)
    return t_k - 273.15


# ── Setup ────────────────────────────────────────────────────────────────────

adcs = {name: make_adc(pin) for name, pin in ADC_PINS.items()}

print("SOMA Thermistor Reader — ESP32-S3 — Day 1")
print(f"Monitoring: {list(ADC_PINS.keys())}")
print("Press Ctrl+C to stop\n")
print(f"{'Time (s)':>10}  " + "  ".join(f"{n:>12}" for n in ADC_PINS))
print("-" * (12 + 14 * len(ADC_PINS)))


# ── Main loop ────────────────────────────────────────────────────────────────

start = time.time()

try:
    while True:
        t = time.time() - start
        temps = {name: read_averaged(adc) for name, adc in adcs.items()}
        row = f"{t:>10.1f}  " + "  ".join(
            f"{c:>10.2f}°C" if not math.isnan(c) else f"{'ERR':>12}"
            for c in temps.values()
        )
        print(row)
        time.sleep(1)

except KeyboardInterrupt:
    print("\nStopped.")
