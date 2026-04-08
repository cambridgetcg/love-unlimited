#ifndef PIN_MAP_H
#define PIN_MAP_H

// ESP32-S3 Pin Assignments for Thermal Pad System
// Board: ESP32-S3-DevKitC-1-N8R8 (2nd unit)

// NTC Thermistor ADC Inputs (4 channels)
#define THERMISTOR_1_PIN    35  // ADC1_CH4
#define THERMISTOR_2_PIN    36  // ADC1_CH5
#define THERMISTOR_3_PIN    37  // ADC1_CH6
#define THERMISTOR_4_PIN    38  // ADC1_CH7

// MOSFET PWM Outputs for Peltier Modules (4 channels)
#define PELTIER_1_PWM_PIN   39  // PWM capable
#define PELTIER_2_PWM_PIN   40  // PWM capable
#define PELTIER_3_PWM_PIN   41  // PWM capable
#define PELTIER_4_PWM_PIN   42  // PWM capable

// PWM Configuration
#define PWM_FREQUENCY       25000   // 25 kHz for smooth Peltier control
#define PWM_RESOLUTION      8       // 8-bit resolution (0-255)
#define PWM_CHANNEL_1       0
#define PWM_CHANNEL_2       1
#define PWM_CHANNEL_3       2
#define PWM_CHANNEL_4       3

// ADC Configuration
#define ADC_RESOLUTION      12      // 12-bit ADC (0-4095)
#define ADC_SAMPLES         10      // Number of samples for averaging

#endif // PIN_MAP_H
