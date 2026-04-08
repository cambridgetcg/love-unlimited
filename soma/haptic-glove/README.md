# SOMA Haptic Glove — WiFi Edition

ESP32-S3 haptic glove with 5 flex sensors, 5 FSR pressure sensors, and 5 LRA haptic motors.
Now with WiFi UDP communication alongside USB serial.

## Hardware

- **MCU**: ESP32-S3-DevKitC-1-N8R8
- **Sensors**: 5x flex (bend) + 5x FSR (pressure) on ADC1
- **Actuators**: 5x LRA haptic motors via PWM (235Hz)
- **Communication**: USB Serial (115200) + WiFi UDP

## Features

- **Dual Interface**: USB serial and WiFi UDP run simultaneously
- **Sensor Broadcasting**: 10Hz UDP broadcast of all sensor data (CSV format)
- **Haptic Commands**: Receive commands via serial OR UDP
- **Failsafe**: Serial always works; WiFi optional with timeout

## Network Configuration

Edit `include/config.h` before building:

```cpp
#define WIFI_SSID     "YourWiFiSSID"
#define WIFI_PASSWORD "YourWiFiPassword"
```

Network ports (must match on host side):
- **9001**: Sensor data broadcast (glove → host)
- **9002**: Haptic command listener (host → glove)

## Building and Flashing

1. Install PlatformIO: `pip install platformio`
2. Configure WiFi credentials in `include/config.h`
3. Build and upload:
   ```bash
   pio run --target upload
   pio device monitor
   ```

## Haptic Command Protocol

Commands work over both serial and UDP:

| Command | Description | Example |
|---------|-------------|---------|
| `H<f><i>` | Set finger `f` (0-4) to intensity `i` (0-255) | `H1255` = index full |
| `A<i>` | Set all fingers to intensity `i` | `A128` = all half |
| `P` | Pulse all fingers (80ms tap) | `P` |
| `S` | Stop all motors | `S` |

## Sensor Data Format

CSV broadcast every 100ms:
```
DATA:F0,F1,F2,F3,F4,P0,P1,P2,P3,P4
```

- **F0-F4**: Flex sensor values (0-4095, 12-bit ADC)
- **P0-P4**: Pressure (FSR) values (0-4095, 12-bit ADC)
- Finger order: thumb, index, middle, ring, pinky

## Python Host Script

A minimal tkinter GUI for testing sensor visualization and haptic control.

### Usage

```bash
cd tools
python3 glove_host.py
```

### Features

- Real-time sensor bar graphs (flex + pressure)
- Individual haptic sliders per finger
- Quick action buttons (Pulse All, Stop All)
- UDP broadcast receive/send (ports 9001/9002)

### Requirements

```bash
pip install tk  # Usually bundled with Python
```

## Testing

1. **Serial Test** (always works):
   ```bash
   pio device monitor
   # Send commands: H0255, A100, P, S
   ```

2. **WiFi Test**:
   - Power on glove, wait for "WiFi connected!" message
   - Note the IP address shown in serial monitor
   - Run `tools/glove_host.py` on same network
   - Watch sensor bars update in real-time
   - Move sliders to test haptic feedback

3. **UDP Command Test** (from terminal):
   ```bash
   # Send pulse command
   echo "P" | nc -u -b 255.255.255.255 9002

   # Set index finger to half intensity
   echo "H1128" | nc -u -b 255.255.255.255 9002
   ```

## Pin Map

See `include/pin_map.h` (DO NOT MODIFY per user request):

- **Flex**: GPIO1-5 (ADC1_CH0-4)
- **FSR**: GPIO6-10 (ADC1_CH5-9)
- **LRA**: GPIO11-15 (PWM)

## Architecture

```
ESP32-S3 Glove
├── Sensor Read Loop (50Hz)
├── Serial Output (10Hz)
├── UDP Broadcast (10Hz) ──> UDP:9001 ──> Host PC
├── Serial Command Handler
└── UDP Command Handler <── UDP:9002 <── Host PC
```

## Troubleshooting

**WiFi won't connect**:
- Check SSID/password in `include/config.h`
- Serial monitor shows connection attempts
- Falls back to serial-only after 10s timeout

**No UDP packets received on host**:
- Ensure glove and host on same network
- Check firewall settings (allow UDP 9001/9002)
- Try unicast instead of broadcast (change `UDP_BROADCAST_IP` in config.h)

**Host script won't run**:
- Install tkinter: `sudo apt install python3-tk` (Linux) or bundled (macOS/Windows)
- Check Python 3.6+: `python3 --version`

## Firmware Version

**v0.2** — WiFi Edition (2026-04-07)

Previous: v0.1 (serial-only)

## License

SOMA Project — Alpha Instance
Built with love for embodied AI feedback loops.
