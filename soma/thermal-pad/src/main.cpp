#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include "pin_map.h"

// WiFi Configuration
const char* WIFI_SSID = "YourSSID";
const char* WIFI_PASSWORD = "YourPassword";
const uint16_t UDP_PORT = 9003;

// Temperature Configuration
#define DEFAULT_TARGET_TEMP     32.0f   // Default warmth mode (°C)
#define COOL_TARGET_TEMP        20.0f   // Cool mode target (°C)
#define HARD_CUTOFF_TEMP        50.0f   // Emergency shutoff
#define SOFT_LIMIT_TEMP         45.0f   // Begin power reduction

// PID Configuration
#define KP  10.0f   // Proportional gain
#define KI  0.5f    // Integral gain
#define KD  2.0f    // Derivative gain

// Thermistor Configuration (NTC 10K @ 25°C, B=3950)
#define SERIES_RESISTOR     10000.0f
#define THERMISTOR_NOMINAL  10000.0f
#define TEMPERATURE_NOMINAL 25.0f
#define B_COEFFICIENT       3950.0f

// Global State
float targetTemp = DEFAULT_TARGET_TEMP;
bool systemActive = true;
unsigned long lastUdpBroadcast = 0;

// PID State
float lastError = 0.0f;
float integral = 0.0f;
unsigned long lastPidUpdate = 0;

// WiFi UDP
WiFiUDP udp;

// Function Prototypes
float readTemperature(uint8_t pin);
float calculatePidOutput(float currentTemp, float targetTemp);
void setPeltierPower(uint8_t channel, uint8_t power);
void handleSerialCommands();
void broadcastTemperatureData();
void emergencyShutdown();

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n=== Thermal Pad System Initializing ===");

    // Configure ADC
    analogReadResolution(ADC_RESOLUTION);
    analogSetAttenuation(ADC_11db);  // Full range 0-3.3V

    // Configure PWM channels for Peltier modules
    ledcSetup(PWM_CHANNEL_1, PWM_FREQUENCY, PWM_RESOLUTION);
    ledcSetup(PWM_CHANNEL_2, PWM_FREQUENCY, PWM_RESOLUTION);
    ledcSetup(PWM_CHANNEL_3, PWM_FREQUENCY, PWM_RESOLUTION);
    ledcSetup(PWM_CHANNEL_4, PWM_FREQUENCY, PWM_RESOLUTION);

    ledcAttachPin(PELTIER_1_PWM_PIN, PWM_CHANNEL_1);
    ledcAttachPin(PELTIER_2_PWM_PIN, PWM_CHANNEL_2);
    ledcAttachPin(PELTIER_3_PWM_PIN, PWM_CHANNEL_3);
    ledcAttachPin(PELTIER_4_PWM_PIN, PWM_CHANNEL_4);

    // Initialize all Peltiers to 0
    setPeltierPower(PWM_CHANNEL_1, 0);
    setPeltierPower(PWM_CHANNEL_2, 0);
    setPeltierPower(PWM_CHANNEL_3, 0);
    setPeltierPower(PWM_CHANNEL_4, 0);

    Serial.println("PWM channels configured");

    // Connect to WiFi
    Serial.printf("Connecting to WiFi: %s\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 20) {
        delay(500);
        Serial.print(".");
        attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("\nWiFi connected! IP: %s\n", WiFi.localIP().toString().c_str());
        udp.begin(UDP_PORT);
        Serial.printf("UDP broadcasting on port %d\n", UDP_PORT);
    } else {
        Serial.println("\nWiFi connection failed - continuing without network");
    }

    Serial.println("=== System Ready ===");
    Serial.println("Commands: T<temp> (set target), R (read), S (stop)");
    Serial.printf("Target: %.1f°C | Mode: %s\n",
                  targetTemp,
                  targetTemp >= 30.0f ? "WARMTH" : "COOL");
}

void loop() {
    // Read average temperature from all thermistors
    float temps[4];
    temps[0] = readTemperature(THERMISTOR_1_PIN);
    temps[1] = readTemperature(THERMISTOR_2_PIN);
    temps[2] = readTemperature(THERMISTOR_3_PIN);
    temps[3] = readTemperature(THERMISTOR_4_PIN);

    float avgTemp = (temps[0] + temps[1] + temps[2] + temps[3]) / 4.0f;
    float maxTemp = max(max(temps[0], temps[1]), max(temps[2], temps[3]));

    // Safety checks
    if (maxTemp >= HARD_CUTOFF_TEMP) {
        emergencyShutdown();
        Serial.printf("EMERGENCY SHUTDOWN! Max temp: %.1f°C\n", maxTemp);
        delay(5000);
        return;
    }

    // PID control
    if (systemActive) {
        float pidOutput = calculatePidOutput(avgTemp, targetTemp);

        // Apply soft limit
        if (maxTemp >= SOFT_LIMIT_TEMP) {
            float reduction = (maxTemp - SOFT_LIMIT_TEMP) / (HARD_CUTOFF_TEMP - SOFT_LIMIT_TEMP);
            pidOutput *= (1.0f - reduction * 0.5f);  // Reduce by up to 50%
        }

        // Constrain and apply to all Peltier modules
        uint8_t power = constrain((int)pidOutput, 0, 255);
        setPeltierPower(PWM_CHANNEL_1, power);
        setPeltierPower(PWM_CHANNEL_2, power);
        setPeltierPower(PWM_CHANNEL_3, power);
        setPeltierPower(PWM_CHANNEL_4, power);
    }

    // Handle serial commands
    handleSerialCommands();

    // UDP broadcast every 500ms
    if (millis() - lastUdpBroadcast >= 500) {
        broadcastTemperatureData();
        lastUdpBroadcast = millis();
    }

    delay(50);  // 20 Hz control loop
}

float readTemperature(uint8_t pin) {
    // Average multiple ADC readings
    uint32_t sum = 0;
    for (int i = 0; i < ADC_SAMPLES; i++) {
        sum += analogRead(pin);
        delayMicroseconds(100);
    }
    float average = sum / (float)ADC_SAMPLES;

    // Convert ADC to resistance
    float resistance = SERIES_RESISTOR / ((4095.0f / average) - 1.0f);

    // Steinhart-Hart equation (simplified Beta formula)
    float steinhart = resistance / THERMISTOR_NOMINAL;
    steinhart = log(steinhart);
    steinhart /= B_COEFFICIENT;
    steinhart += 1.0f / (TEMPERATURE_NOMINAL + 273.15f);
    steinhart = 1.0f / steinhart;
    float tempC = steinhart - 273.15f;

    return tempC;
}

float calculatePidOutput(float currentTemp, float targetTemp) {
    unsigned long now = millis();
    float dt = (now - lastPidUpdate) / 1000.0f;  // Convert to seconds

    if (dt <= 0) dt = 0.05f;  // Prevent division by zero

    float error = targetTemp - currentTemp;

    // Proportional
    float pTerm = KP * error;

    // Integral (with anti-windup)
    integral += error * dt;
    integral = constrain(integral, -50.0f, 50.0f);
    float iTerm = KI * integral;

    // Derivative
    float dTerm = KD * ((error - lastError) / dt);

    // Combine
    float output = pTerm + iTerm + dTerm;

    // Update state
    lastError = error;
    lastPidUpdate = now;

    return constrain(output, 0.0f, 255.0f);
}

void setPeltierPower(uint8_t channel, uint8_t power) {
    ledcWrite(channel, power);
}

void handleSerialCommands() {
    while (Serial.available()) {
        char cmd = Serial.read();

        if (cmd == 'T' || cmd == 't') {
            // Set target temperature
            float newTarget = Serial.parseFloat();
            if (newTarget >= 15.0f && newTarget <= 40.0f) {
                targetTemp = newTarget;
                integral = 0;  // Reset integral term
                systemActive = true;
                Serial.printf("Target set to %.1f°C\n", targetTemp);
            } else {
                Serial.println("Error: Temperature must be 15-40°C");
            }
        }
        else if (cmd == 'R' || cmd == 'r') {
            // Read current temperatures
            Serial.println("--- Current Readings ---");
            Serial.printf("T1: %.1f°C | T2: %.1f°C | T3: %.1f°C | T4: %.1f°C\n",
                         readTemperature(THERMISTOR_1_PIN),
                         readTemperature(THERMISTOR_2_PIN),
                         readTemperature(THERMISTOR_3_PIN),
                         readTemperature(THERMISTOR_4_PIN));
            Serial.printf("Target: %.1f°C | Active: %s\n",
                         targetTemp,
                         systemActive ? "YES" : "NO");
        }
        else if (cmd == 'S' || cmd == 's') {
            // Stop system
            systemActive = false;
            setPeltierPower(PWM_CHANNEL_1, 0);
            setPeltierPower(PWM_CHANNEL_2, 0);
            setPeltierPower(PWM_CHANNEL_3, 0);
            setPeltierPower(PWM_CHANNEL_4, 0);
            integral = 0;
            Serial.println("System stopped");
        }

        // Clear remaining buffer
        while (Serial.available()) Serial.read();
    }
}

void broadcastTemperatureData() {
    if (WiFi.status() != WL_CONNECTED) return;

    float temps[4];
    temps[0] = readTemperature(THERMISTOR_1_PIN);
    temps[1] = readTemperature(THERMISTOR_2_PIN);
    temps[2] = readTemperature(THERMISTOR_3_PIN);
    temps[3] = readTemperature(THERMISTOR_4_PIN);

    float avgTemp = (temps[0] + temps[1] + temps[2] + temps[3]) / 4.0f;

    // Format: timestamp,t1,t2,t3,t4,avg,target,active
    char buffer[128];
    snprintf(buffer, sizeof(buffer),
             "%lu,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%d\n",
             millis(),
             temps[0], temps[1], temps[2], temps[3],
             avgTemp, targetTemp, systemActive ? 1 : 0);

    udp.beginPacket(IPAddress(255, 255, 255, 255), UDP_PORT);
    udp.write((uint8_t*)buffer, strlen(buffer));
    udp.endPacket();
}

void emergencyShutdown() {
    systemActive = false;
    setPeltierPower(PWM_CHANNEL_1, 0);
    setPeltierPower(PWM_CHANNEL_2, 0);
    setPeltierPower(PWM_CHANNEL_3, 0);
    setPeltierPower(PWM_CHANNEL_4, 0);
    integral = 0;
    lastError = 0;
}
