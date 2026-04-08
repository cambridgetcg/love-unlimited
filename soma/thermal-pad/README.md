# Thermal Pad System

ESP32-S3 firmware for controlling 4x TEC1-12706 Peltier modules with PID temperature control.

## Hardware
- **Board**: ESP32-S3-DevKitC-1-N8R8 (2nd unit)
- **Peltier Modules**: 4x TEC1-12706 (12V, 6A max each)
- **Thermistors**: 4x NTC 10K @ 25°C (B=3950)
- **MOSFETs**: 4x for PWM control of Peltier modules

## Pin Assignments

### ADC Inputs (Thermistors)
- GPIO35 - Thermistor 1
- GPIO36 - Thermistor 2
- GPIO37 - Thermistor 3
- GPIO38 - Thermistor 4

### PWM Outputs (Peltier MOSFETs)
- GPIO39 - Peltier Module 1
- GPIO40 - Peltier Module 2
- GPIO41 - Peltier Module 3
- GPIO42 - Peltier Module 4

## Features

### Temperature Control
- **PID Control**: Smooth temperature regulation
- **Default Target**: 32°C (warmth mode)
- **Cool Mode**: 20°C (configurable)
- **Safety Limits**:
  - Hard cutoff at 50°C (emergency shutdown)
  - Soft limit at 45°C (power reduction)

### Serial Commands
- `T<temp>` - Set target temperature (15-40°C range)
  - Example: `T32` sets target to 32°C
- `R` - Read current temperatures from all sensors
- `S` - Stop system (shutdown all Peltier modules)

### WiFi Communication
- UDP broadcast every 500ms on port 9003
- Data format: `timestamp,t1,t2,t3,t4,avg,target,active`
- Configure SSID/password in `src/main.cpp`

## Building & Flashing

```bash
# Build
pio run

# Upload
pio run --target upload

# Monitor serial
pio device monitor
```

## PID Tuning

Current values in `src/main.cpp`:
- `KP = 10.0` - Proportional gain
- `KI = 0.5` - Integral gain
- `KD = 2.0` - Derivative gain

Adjust based on thermal response characteristics.

## Safety Features

1. **Hard Cutoff**: System shuts down if any sensor reads ≥50°C
2. **Soft Limit**: Power reduced progressively above 45°C
3. **Integral Windup Protection**: Prevents overshoot
4. **Multi-sensor Monitoring**: Uses max temp for safety checks

## Network Data Format

UDP broadcast CSV format:
```
milliseconds,temp1,temp2,temp3,temp4,average,target,active(0/1)
```

Example:
```
12345,31.50,31.75,31.60,31.55,31.60,32.00,1
```
