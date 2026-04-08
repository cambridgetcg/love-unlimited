#pragma once

// ── SOMA Haptic Glove — Pin Map ─────────────────────────────────────────────
// ESP32-S3-DevKitC-1-N8R8
//
// Flex sensors: voltage divider with 10k pull-down -> ADC1 channels
// FSR sensors:  voltage divider with 10k pull-down -> ADC1 channels
// LRA motors:   PWM at 235Hz through NPN transistor (2N2222 or similar)
//
// Finger order: thumb=0, index=1, middle=2, ring=3, pinky=4

// ── Flex Sensors (ADC1) — finger bend angle ─────────────────────────────────
// Wire: 3.3V -> flex sensor -> GPIO pin -> 10k resistor -> GND
static const int FLEX_PINS[5] = {
    1,   // thumb   — GPIO1  (ADC1_CH0)
    2,   // index   — GPIO2  (ADC1_CH1)
    3,   // middle  — GPIO3  (ADC1_CH2)
    4,   // ring    — GPIO4  (ADC1_CH3)
    5,   // pinky   — GPIO5  (ADC1_CH4)
};

// ── FSR Sensors (ADC1) — fingertip pressure ─────────────────────────────────
// Wire: 3.3V -> FSR -> GPIO pin -> 10k resistor -> GND
static const int FSR_PINS[5] = {
    6,   // thumb   — GPIO6  (ADC1_CH5)
    7,   // index   — GPIO7  (ADC1_CH6)
    8,   // middle  — GPIO8  (ADC1_CH7)
    9,   // ring    — GPIO9  (ADC1_CH8)
    10,  // pinky   — GPIO10 (ADC1_CH9)
};

// ── LRA Haptic Motors (LEDC PWM) — fingertip vibration feedback ─────────────
// Wire: GPIO -> 1k resistor -> NPN base, NPN collector -> motor -> 3.3V,
//       NPN emitter -> GND, flyback diode across motor
// Drive at 235Hz (motor resonant frequency) for maximum haptic effect
static const int LRA_PINS[5] = {
    11,  // thumb   — GPIO11
    12,  // index   — GPIO12
    13,  // middle  — GPIO13
    14,  // ring    — GPIO14
    15,  // pinky   — GPIO15
};

static const int LRA_FREQ = 235;       // resonant frequency Hz
static const int LRA_RESOLUTION = 8;   // 8-bit PWM (0-255)
static const int NUM_FINGERS = 5;

// ── Status LED ──────────────────────────────────────────────────────────────
static const int LED_PIN = 48;          // onboard RGB LED (NeoPixel on DevKitC-1)
