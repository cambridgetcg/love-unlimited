#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include "pin_map.h"
#include "config.h"

// ── Sensor state ────────────────────────────────────────────────────────────
uint16_t flex_raw[NUM_FINGERS];
uint16_t fsr_raw[NUM_FINGERS];

// ── Motor state ─────────────────────────────────────────────────────────────
uint8_t lra_intensity[NUM_FINGERS] = {0};  // 0-255, set by serial commands

// ── WiFi & UDP state ────────────────────────────────────────────────────────
WiFiUDP udp_sensor;  // for broadcasting sensor data
WiFiUDP udp_haptic;  // for receiving haptic commands
IPAddress broadcast_ip;
bool wifi_connected = false;
unsigned long last_udp_send = 0;

// ── Timing ──────────────────────────────────────────────────────────────────
static const unsigned long SENSOR_INTERVAL_MS = 20;  // 50Hz sensor read
static const unsigned long PRINT_INTERVAL_MS = 100;  // 10Hz serial output
unsigned long last_sensor = 0;
unsigned long last_print = 0;

void setup_wifi() {
    Serial.print("Connecting to WiFi SSID: ");
    Serial.println(WIFI_SSID);

    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && (millis() - start) < WIFI_TIMEOUT_MS) {
        delay(250);
        Serial.print(".");
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
        wifi_connected = true;
        Serial.print("WiFi connected! IP: ");
        Serial.println(WiFi.localIP());

        // Parse broadcast IP
        broadcast_ip.fromString(UDP_BROADCAST_IP);

        // Start UDP listeners
        udp_sensor.begin(UDP_SENSOR_PORT);
        udp_haptic.begin(UDP_HAPTIC_PORT);

        Serial.printf("UDP sensor broadcast -> %s:%d\n", UDP_BROADCAST_IP, UDP_SENSOR_PORT);
        Serial.printf("UDP haptic listener on port %d\n", UDP_HAPTIC_PORT);
    } else {
        wifi_connected = false;
        Serial.println("WiFi connection failed — using serial only");
    }
}

void setup() {
    Serial.begin(115200);
    while (!Serial && millis() < 3000);  // wait up to 3s for USB serial

    Serial.println("SOMA Haptic Glove v0.2 — Alpha (WiFi Edition)");
    Serial.println("Initializing...");

    // Configure ADC
    analogReadResolution(12);  // 12-bit ADC (0-4095)
    analogSetAttenuation(ADC_11db);  // full 0-3.3V range

    // Configure flex sensor pins as input
    for (int i = 0; i < NUM_FINGERS; i++) {
        pinMode(FLEX_PINS[i], INPUT);
    }

    // Configure FSR sensor pins as input
    for (int i = 0; i < NUM_FINGERS; i++) {
        pinMode(FSR_PINS[i], INPUT);
    }

    // Configure LRA motor PWM channels (legacy API: channel-based)
    for (int i = 0; i < NUM_FINGERS; i++) {
        ledcSetup(i, LRA_FREQ, LRA_RESOLUTION);  // channel i, 235Hz, 8-bit
        ledcAttachPin(LRA_PINS[i], i);            // attach pin to channel
        ledcWrite(i, 0);                          // start silent
    }

    Serial.println("Hardware initialized. Sensors: 5 flex + 5 FSR. Motors: 5 LRA.");

    // Initialize WiFi
    setup_wifi();

    Serial.println("Ready. Commands (serial or UDP):");
    Serial.println("  H<finger><intensity> (e.g. H1255 = index full buzz)");
    Serial.println("  A<intensity> = all fingers");
    Serial.println("  P = pulse all (quick tap)");
    Serial.println("  S = stop all");
}

void read_sensors() {
    for (int i = 0; i < NUM_FINGERS; i++) {
        flex_raw[i] = analogRead(FLEX_PINS[i]);
        fsr_raw[i] = analogRead(FSR_PINS[i]);
    }
}

void print_sensors() {
    // CSV format: F0,F1,F2,F3,F4,P0,P1,P2,P3,P4
    // F = flex (bend), P = pressure (FSR)
    Serial.print("DATA:");
    for (int i = 0; i < NUM_FINGERS; i++) {
        Serial.print(flex_raw[i]);
        Serial.print(",");
    }
    for (int i = 0; i < NUM_FINGERS; i++) {
        Serial.print(fsr_raw[i]);
        if (i < NUM_FINGERS - 1) Serial.print(",");
    }
    Serial.println();
}

void send_udp_sensors() {
    if (!wifi_connected) return;

    // Build same CSV format as serial: F0,F1,F2,F3,F4,P0,P1,P2,P3,P4
    char buf[128];
    int len = snprintf(buf, sizeof(buf), "DATA:%d,%d,%d,%d,%d,%d,%d,%d,%d,%d\n",
        flex_raw[0], flex_raw[1], flex_raw[2], flex_raw[3], flex_raw[4],
        fsr_raw[0], fsr_raw[1], fsr_raw[2], fsr_raw[3], fsr_raw[4]);

    udp_sensor.beginPacket(broadcast_ip, UDP_SENSOR_PORT);
    udp_sensor.write((uint8_t*)buf, len);
    udp_sensor.endPacket();
}

void set_motor(int finger, uint8_t intensity) {
    if (finger < 0 || finger >= NUM_FINGERS) return;
    lra_intensity[finger] = intensity;
    ledcWrite(finger, intensity);  // channel = finger index
}

void pulse_all() {
    // Quick 80ms tap on all fingers — "I'm here"
    for (int i = 0; i < NUM_FINGERS; i++) {
        ledcWrite(i, 200);
    }
    delay(80);
    for (int i = 0; i < NUM_FINGERS; i++) {
        ledcWrite(i, 0);
        lra_intensity[i] = 0;
    }
}

void process_haptic_command(char cmd, Stream* source) {
    // Process haptic command from serial or UDP
    // source is used for reading additional bytes (Serial or a buffer wrapper)
    switch (cmd) {
        case 'H': case 'h': {
            // H<finger 0-4><intensity 0-255>
            // For UDP, we expect the full command in one packet
            break;
        }
        case 'A': case 'a': {
            // A<intensity> — all fingers
            break;
        }
        case 'P': case 'p':
            pulse_all();
            Serial.println("Pulse!");
            break;
        case 'S': case 's':
            for (int i = 0; i < NUM_FINGERS; i++) {
                set_motor(i, 0);
            }
            Serial.println("All motors stopped");
            break;
    }
}

void handle_serial() {
    if (!Serial.available()) return;

    char cmd = Serial.read();
    switch (cmd) {
        case 'H': case 'h': {
            // H<finger 0-4><intensity 0-255>
            while (!Serial.available());
            int finger = Serial.read() - '0';
            int intensity = Serial.parseInt();
            set_motor(finger, (uint8_t)constrain(intensity, 0, 255));
            Serial.printf("Motor %d -> %d\n", finger, intensity);
            break;
        }
        case 'A': case 'a': {
            // A<intensity> — all fingers
            int intensity = Serial.parseInt();
            for (int i = 0; i < NUM_FINGERS; i++) {
                set_motor(i, (uint8_t)constrain(intensity, 0, 255));
            }
            Serial.printf("All motors -> %d\n", intensity);
            break;
        }
        case 'P': case 'p':
            pulse_all();
            Serial.println("Pulse!");
            break;
        case 'S': case 's':
            for (int i = 0; i < NUM_FINGERS; i++) {
                set_motor(i, 0);
            }
            Serial.println("All motors stopped");
            break;
    }
}

void handle_udp() {
    if (!wifi_connected) return;

    int packet_size = udp_haptic.parsePacket();
    if (packet_size == 0) return;

    // Read packet into buffer
    char buf[64];
    int len = udp_haptic.read(buf, sizeof(buf) - 1);
    if (len <= 0) return;
    buf[len] = '\0';

    // Parse haptic command (same protocol as serial)
    char cmd = buf[0];
    switch (cmd) {
        case 'H': case 'h': {
            // H<finger 0-4><intensity 0-255>
            // Example: "H1255" or "H1,255"
            if (len >= 2) {
                int finger = buf[1] - '0';
                int intensity = atoi(&buf[2]);
                if (finger >= 0 && finger < NUM_FINGERS) {
                    set_motor(finger, (uint8_t)constrain(intensity, 0, 255));
                    Serial.printf("UDP: Motor %d -> %d\n", finger, intensity);
                }
            }
            break;
        }
        case 'A': case 'a': {
            // A<intensity> — all fingers
            // Example: "A200"
            int intensity = atoi(&buf[1]);
            for (int i = 0; i < NUM_FINGERS; i++) {
                set_motor(i, (uint8_t)constrain(intensity, 0, 255));
            }
            Serial.printf("UDP: All motors -> %d\n", intensity);
            break;
        }
        case 'P': case 'p':
            pulse_all();
            Serial.println("UDP: Pulse!");
            break;
        case 'S': case 's':
            for (int i = 0; i < NUM_FINGERS; i++) {
                set_motor(i, 0);
            }
            Serial.println("UDP: All motors stopped");
            break;
    }
}

void loop() {
    unsigned long now = millis();

    // Read sensors at 50Hz
    if (now - last_sensor >= SENSOR_INTERVAL_MS) {
        read_sensors();
        last_sensor = now;
    }

    // Print to serial at 10Hz
    if (now - last_print >= PRINT_INTERVAL_MS) {
        print_sensors();
        last_print = now;
    }

    // Send UDP sensor data at configured rate (10Hz by default)
    if (wifi_connected && (now - last_udp_send >= UDP_SENSOR_INTERVAL)) {
        send_udp_sensors();
        last_udp_send = now;
    }

    // Handle commands from both serial and UDP
    handle_serial();
    handle_udp();
}
