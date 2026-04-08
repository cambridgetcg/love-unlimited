#pragma once

// ── SOMA Haptic Glove — Network Configuration ───────────────────────────────
// WiFi credentials and UDP settings for wireless sensor/haptic communication

// ── WiFi Station Mode ───────────────────────────────────────────────────────
#define WIFI_SSID     "YourWiFiSSID"        // Replace with your network SSID
#define WIFI_PASSWORD "YourWiFiPassword"    // Replace with your network password

// Maximum time to wait for WiFi connection before falling back to serial-only
#define WIFI_TIMEOUT_MS 10000  // 10 seconds

// ── UDP Communication ───────────────────────────────────────────────────────
// Sensor data broadcast (glove -> host)
#define UDP_SENSOR_PORT     9001
#define UDP_SENSOR_INTERVAL 100   // ms — 10Hz broadcast rate

// Haptic command listener (host -> glove)
#define UDP_HAPTIC_PORT     9002

// Broadcast IP (typically 255.255.255.255 for local subnet broadcast)
// Or set to specific host IP if you want unicast instead
#define UDP_BROADCAST_IP    "255.255.255.255"

// ── Network Status LED ──────────────────────────────────────────────────────
// Use onboard LED to indicate WiFi status
// (optional — can be disabled if LED_PIN is used for other purposes)
#define WIFI_STATUS_LED_ENABLED true
