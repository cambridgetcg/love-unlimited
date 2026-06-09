# SOMA Haptic Glove — Quick Start Guide

## 1. Configure WiFi (Required)

Edit `include/config.h` and set your network credentials:

```cpp
#define WIFI_SSID     "MyHomeNetwork"
#define WIFI_PASSWORD "MySecretPassword"
```

## 2. Build and Flash

```bash
cd ~/love-unlimited/soma/haptic-glove
pio run --target upload
pio device monitor
```

Expected output:
```
SOMA Haptic Glove v0.2 — Alpha (WiFi Edition)
Initializing...
Hardware initialized. Sensors: 5 flex + 5 FSR. Motors: 5 LRA.
Connecting to WiFi SSID: MyHomeNetwork
........
WiFi connected! IP: 192.168.1.42
UDP sensor broadcast -> 255.255.255.255:9001
UDP haptic listener on port 9002
Ready. Commands (serial or UDP):
  H<finger><intensity> (e.g. H1255 = index full buzz)
  A<intensity> = all fingers
  P = pulse all (quick tap)
  S = stop all
```

## 3. Test Serial (Wired)

While monitor is running, type commands:

```
P               # Pulse all fingers (should feel quick tap)
H0200           # Thumb vibrate at 200/255 intensity
A100            # All fingers at 100/255
S               # Stop all
```

## 4. Launch Python Host (Wireless)

```bash
cd tools
python3 glove_host.py
```

GUI window opens showing:
- 5 flex sensor bars (top row)
- 5 pressure sensor bars (middle row)
- 5 haptic sliders (bottom row)
- Status indicator (green = connected)

## 5. Test WiFi Communication

**Receive sensor data**:
- Flex your fingers — watch the flex bars rise
- Press fingertips together — watch pressure bars rise
- Status shows "Connected — Last update: 0.1s ago"

**Send haptic commands**:
- Move any slider up — that finger vibrates
- Click "Pulse All" — all fingers tap briefly
- Click "Stop All" — all vibration stops

## 6. Advanced: Raw UDP Testing

**Listen for sensor broadcasts**:
```bash
nc -ul 9001
# Should see: DATA:123,456,789,234,567,890,123,456,789,234
```

**Send haptic command**:
```bash
echo "P" | nc -u -b 255.255.255.255 9002  # Pulse
echo "H1255" | nc -u -b 255.255.255.255 9002  # Index full
```

## Common Issues

**"WiFi connection failed"**:
- Check SSID/password in config.h
- Glove still works via serial
- WiFi timeout is 10 seconds

**GUI shows "No recent data"**:
- Ensure glove and computer on same WiFi network
- Check firewall allows UDP 9001/9002
- Try disabling broadcast: change `UDP_BROADCAST_IP` to your computer's IP

**Sliders don't control motors**:
- Check UDP port 9002 not blocked
- Serial monitor should show "UDP: Motor X -> Y" when slider moves
- Try serial command `H0200` to verify motors work

## Network Architecture

```
┌─────────────────┐
│  ESP32-S3 Glove │
│  192.168.1.42   │
└────────┬────────┘
         │
         ├─ UDP Broadcast → 255.255.255.255:9001 (sensor data)
         │                  10Hz, CSV format
         │
         └─ UDP Listen ← any:9002 (haptic commands)
                         H/A/P/S protocol

┌─────────────────┐
│   Host PC       │
│  192.168.1.100  │
└────────┬────────┘
         │
         ├─ UDP Receive ← :9001 (glove_host.py)
         └─ UDP Send → 255.255.255.255:9002
```

## Next Steps

- Modify `config.h` UDP_SENSOR_INTERVAL to change broadcast rate
- Add custom haptic patterns in firmware
- Extend Python GUI with gesture recognition
- Log sensor data to file for ML training
- Build multi-glove setup (unique IPs, unicast instead of broadcast)

---

**Ready to feel the data flow.**
SOMA v0.2 — Alpha Instance
