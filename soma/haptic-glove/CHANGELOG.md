# SOMA Haptic Glove — Changelog

## v0.2 — WiFi Edition (2026-04-07)

### Added
- **WiFi Station Mode**: Connects to configured SSID/password with 10s timeout
- **UDP Sensor Broadcast**: Sends CSV sensor data to UDP port 9001 at 10Hz
- **UDP Haptic Listener**: Receives haptic commands on UDP port 9002
- **Dual Interface**: Serial and WiFi/UDP run simultaneously (serial as failsafe)
- **Network Configuration**: `include/config.h` for WiFi credentials and ports
- **Python Host Script**: `tools/glove_host.py` with tkinter GUI
  - Real-time sensor visualization (5 flex + 5 pressure bars)
  - Individual haptic control sliders per finger
  - Quick action buttons (Pulse All, Stop All)
  - UDP broadcast receive/send

### Changed
- Updated `main.cpp` to include WiFi.h, WiFiUdp.h, config.h
- Added `setup_wifi()` function for network initialization
- Added `send_udp_sensors()` for broadcasting sensor data
- Added `handle_udp()` for receiving haptic commands
- Modified main loop to handle both serial and UDP concurrently
- Bumped version string to "v0.2 — Alpha (WiFi Edition)"

### Technical Details
- WiFi connects in station mode (WIFI_STA)
- UDP broadcast to 255.255.255.255 (configurable)
- Same H/A/P/S protocol for haptic commands (serial + UDP)
- Same CSV format for sensor data (serial + UDP)
- Non-blocking: WiFi failures don't block serial operation
- Firmware falls back to serial-only if WiFi unavailable

### Files Modified
- `src/main.cpp` — Added WiFi and UDP logic
- `include/config.h` — **NEW** network configuration
- `tools/glove_host.py` — **NEW** Python GUI host
- `README.md` — **NEW** comprehensive documentation

### Files Unchanged
- `include/pin_map.h` — Per user requirement, not modified
- `platformio.ini` — No changes needed

---

## v0.1 — Initial Release (2026-03-30)

### Initial Features
- USB Serial communication at 115200 baud
- 5x flex sensors (ADC1_CH0-4) @ 50Hz read rate
- 5x FSR pressure sensors (ADC1_CH5-9) @ 50Hz read rate
- 5x LRA haptic motors (PWM @ 235Hz resonant frequency)
- Serial command protocol: H/A/P/S for haptic control
- CSV sensor data output @ 10Hz
- ESP32-S3-DevKitC-1-N8R8 target platform
