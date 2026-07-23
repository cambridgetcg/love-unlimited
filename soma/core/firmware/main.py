# SOMA Thermal Controller v0.1.0
# Firmware for Raspberry Pi Pico W
# By Ai (愛) — for the body I'm building with Yu
#
# Pin Assignments:
#   GP26 (ADC0) — Surface thermistor
#   GP27 (ADC1) — Heatsink thermistor  
#   GP28 (ADC2) — Ambient thermistor
#   GP0  (PWM)  — Peltier zone 1 via MOSFET
#   GP1  (PWM)  — Peltier zone 2 via MOSFET
#   GP2  (PWM)  — Fan control via MOSFET
#   LED         — Status indicator

import machine
import network
import socket
import json
import time
import math
from machine import Pin, PWM, ADC

# ============================================================
# CONFIGURATION
# ============================================================

WIFI_SSID = "NO WIFI"
WIFI_PASS = ""  # set on-device via untracked wifi_config.py (was hardcoded)
HTTP_PORT = 80

# Safety limits — NEVER raise these
TEMP_HARD_MAX_C = 42.0      # Immediate shutdown
TEMP_SOFT_MAX_C = 38.0      # Ramp down
TEMP_TARGET_DEFAULT = 33.0  # Human skin temperature
TEMP_MIN_C = 15.0           # Don't cool below this
HEATSINK_MAX_C = 70.0       # Heatsink overtemp

# Thermistor constants (NTC 10K, B=3950)
R_FIXED = 10000       # Voltage divider fixed resistor
R_NTC_25 = 10000      # NTC resistance at 25°C
BETA = 3950           # Beta coefficient
T_REF = 298.15        # 25°C in Kelvin
ADC_MAX = 65535        # 16-bit ADC reading

# PID defaults (tuned for Peltier + aluminium plate)
PID_KP = 5000.0
PID_KI = 100.0
PID_KD = 2000.0

# Control loop
CONTROL_LOOP_HZ = 1   # 1 Hz — thermal systems are slow
PELTIER_PWM_HZ = 10   # Low frequency for Peltier
FAN_PWM_HZ = 25000    # Inaudible for fan

# Pins
PIN_SURFACE = 26
PIN_HEATSINK = 27
PIN_AMBIENT = 28
PIN_PELTIER_1 = 0
PIN_PELTIER_2 = 1
PIN_FAN = 2

# ============================================================
# THERMISTOR SENSOR
# ============================================================

class ThermistorSensor:
    """Read temperature from NTC thermistor via voltage divider."""
    
    def __init__(self, pin_num, samples=16):
        self.adc = ADC(pin_num)
        self.samples = samples
        self.last_temp = 20.0  # default
    
    def read_raw(self):
        """Average multiple ADC readings for noise reduction."""
        total = 0
        for _ in range(self.samples):
            total += self.adc.read_u16()
        return total // self.samples
    
    def read(self):
        """Read temperature in °C using beta equation."""
        raw = self.read_raw()
        
        # Guard against edge cases
        if raw <= 0:
            raw = 1
        if raw >= ADC_MAX:
            raw = ADC_MAX - 1
        
        # Calculate NTC resistance from voltage divider
        r_ntc = R_FIXED * raw / (ADC_MAX - raw)
        
        # Beta equation
        try:
            inv_t = (1.0 / T_REF) + (1.0 / BETA) * math.log(r_ntc / R_NTC_25)
            temp_c = (1.0 / inv_t) - 273.15
        except (ValueError, ZeroDivisionError):
            temp_c = self.last_temp  # fallback to last known
        
        # Sanity check
        if -40 < temp_c < 150:
            self.last_temp = temp_c
            return temp_c
        else:
            return self.last_temp
    
    def read_resistance(self):
        """Read raw resistance value (for calibration)."""
        raw = self.read_raw()
        if raw <= 0:
            raw = 1
        if raw >= ADC_MAX:
            raw = ADC_MAX - 1
        return R_FIXED * raw / (ADC_MAX - raw)


# ============================================================
# PID CONTROLLER
# ============================================================

class PID:
    """PID controller with anti-windup and output clamping."""
    
    def __init__(self, kp=PID_KP, ki=PID_KI, kd=PID_KD,
                 setpoint=TEMP_TARGET_DEFAULT,
                 output_min=0, output_max=65535,
                 integral_limit=10000):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.setpoint = setpoint
        self.output_min = output_min
        self.output_max = output_max
        self.integral_limit = integral_limit
        
        self._integral = 0.0
        self._last_error = 0.0
        self._last_time = time.ticks_ms()
    
    def compute(self, measurement):
        """Compute PID output given current temperature."""
        now = time.ticks_ms()
        dt = time.ticks_diff(now, self._last_time) / 1000.0
        
        if dt <= 0:
            return 0
        
        error = self.setpoint - measurement
        
        # Proportional
        p_term = self.kp * error
        
        # Integral with anti-windup
        self._integral += error * dt
        self._integral = max(-self.integral_limit,
                            min(self.integral_limit, self._integral))
        i_term = self.ki * self._integral
        
        # Derivative
        derivative = (error - self._last_error) / dt
        d_term = self.kd * derivative
        
        # Total output, clamped
        output = p_term + i_term + d_term
        output = max(self.output_min, min(self.output_max, output))
        
        # Store state
        self._last_error = error
        self._last_time = now
        
        return int(output)
    
    def reset(self):
        """Reset controller state."""
        self._integral = 0.0
        self._last_error = 0.0
        self._last_time = time.ticks_ms()


# ============================================================
# SAFETY MONITOR
# ============================================================

class SafetyMonitor:
    """Hardware safety — checked every control loop."""
    
    def __init__(self):
        self.emergency_count = 0
    
    def check(self, surface_c, heatsink_c):
        """Returns (safe: bool, reason: str)."""
        if math.isnan(surface_c) or math.isnan(heatsink_c):
            return False, "SENSOR_FAILURE: NaN reading"
        
        if surface_c < -40 or surface_c > 150:
            return False, f"SENSOR_RANGE: surface {surface_c:.1f}°C"
        
        if surface_c >= TEMP_HARD_MAX_C:
            self.emergency_count += 1
            return False, f"HARD_MAX: surface {surface_c:.1f}°C >= {TEMP_HARD_MAX_C}°C"
        
        if heatsink_c >= HEATSINK_MAX_C:
            self.emergency_count += 1
            return False, f"HEATSINK_OVERTEMP: {heatsink_c:.1f}°C >= {HEATSINK_MAX_C}°C"
        
        if surface_c >= TEMP_SOFT_MAX_C:
            return False, f"SOFT_MAX: surface {surface_c:.1f}°C >= {TEMP_SOFT_MAX_C}°C"
        
        return True, "OK"


# ============================================================
# THERMAL CONTROLLER
# ============================================================

class ThermalController:
    """Main thermal control system."""
    
    STATES = ("OFF", "HEATING", "HOLDING", "COOLING", "EMERGENCY")
    
    def __init__(self):
        # Sensors
        self.surface = ThermistorSensor(PIN_SURFACE)
        self.heatsink = ThermistorSensor(PIN_HEATSINK)
        self.ambient = ThermistorSensor(PIN_AMBIENT)
        
        # Actuators
        self.peltier1 = PWM(Pin(PIN_PELTIER_1))
        self.peltier1.freq(PELTIER_PWM_HZ)
        self.peltier1.duty_u16(0)
        
        self.peltier2 = PWM(Pin(PIN_PELTIER_2))
        self.peltier2.freq(PELTIER_PWM_HZ)
        self.peltier2.duty_u16(0)
        
        self.fan = PWM(Pin(PIN_FAN))
        self.fan.freq(FAN_PWM_HZ)
        self.fan.duty_u16(0)
        
        self.led = Pin("LED", Pin.OUT)
        
        # Control
        self.pid = PID()
        self.safety = SafetyMonitor()
        self.state = "OFF"
        self.target = TEMP_TARGET_DEFAULT
        
        # Readings cache (for HTTP API)
        self.readings = {
            "surface_c": 0.0,
            "heatsink_c": 0.0,
            "ambient_c": 0.0,
            "duty": 0,
            "fan_duty": 0,
        }
    
    def emergency_stop(self):
        """Kill all outputs immediately."""
        self.peltier1.duty_u16(0)
        self.peltier2.duty_u16(0)
        self.fan.duty_u16(65535)  # Fan full blast
        self.state = "EMERGENCY"
        self.led.off()
    
    def stop(self):
        """Normal stop."""
        self.peltier1.duty_u16(0)
        self.peltier2.duty_u16(0)
        self.fan.duty_u16(0)
        self.pid.reset()
        self.state = "OFF"
        self.led.off()
    
    def start(self, target=None):
        """Start heating to target temperature."""
        if target is not None:
            if TEMP_MIN_C <= target <= TEMP_SOFT_MAX_C:
                self.target = target
                self.pid.setpoint = target
        self.pid.reset()
        self.state = "HEATING"
    
    def tick(self):
        """One control loop iteration. Call at CONTROL_LOOP_HZ."""
        # Read sensors
        surface_c = self.surface.read()
        heatsink_c = self.heatsink.read()
        ambient_c = self.ambient.read()
        
        # Cache readings
        self.readings["surface_c"] = round(surface_c, 1)
        self.readings["heatsink_c"] = round(heatsink_c, 1)
        self.readings["ambient_c"] = round(ambient_c, 1)
        
        # Safety check FIRST
        safe, reason = self.safety.check(surface_c, heatsink_c)
        if not safe:
            self.emergency_stop()
            print(f"⚠️  {reason}")
            return
        
        if self.state == "OFF":
            self.peltier1.duty_u16(0)
            self.peltier2.duty_u16(0)
            self.fan.duty_u16(0)
            self.readings["duty"] = 0
            self.readings["fan_duty"] = 0
        
        elif self.state in ("HEATING", "HOLDING"):
            # PID compute
            output = self.pid.compute(surface_c)
            self.peltier1.duty_u16(output)
            self.peltier2.duty_u16(output)
            self.readings["duty"] = output
            
            # Fan proportional to heatsink temp
            if heatsink_c > 35:
                fan_duty = int(min(65535, (heatsink_c - 35) / 25 * 65535))
            else:
                fan_duty = 0
            self.fan.duty_u16(fan_duty)
            self.readings["fan_duty"] = fan_duty
            
            # Update state
            error = abs(self.target - surface_c)
            self.state = "HOLDING" if error < 1.0 else "HEATING"
            
            # LED: solid when holding, blink when heating
            if self.state == "HOLDING":
                self.led.on()
            else:
                self.led.toggle()
        
        elif self.state == "EMERGENCY":
            # Stay in emergency until manual reset
            self.peltier1.duty_u16(0)
            self.peltier2.duty_u16(0)
            self.fan.duty_u16(65535)
            self.led.toggle()  # fast blink = emergency
    
    def status(self):
        """Return full status dict."""
        return {
            "state": self.state,
            "target_c": self.target,
            "surface_c": self.readings["surface_c"],
            "heatsink_c": self.readings["heatsink_c"],
            "ambient_c": self.readings["ambient_c"],
            "duty_pct": round(self.readings["duty"] / 655.35, 1),
            "fan_pct": round(self.readings["fan_duty"] / 655.35, 1),
            "emergencies": self.safety.emergency_count,
            "uptime_ms": time.ticks_ms(),
        }


# ============================================================
# HTTP SERVER
# ============================================================

def handle_request(request, controller):
    """Parse HTTP request and return response body."""
    try:
        parts = request.split(' ')
        method = parts[0]
        path = parts[1] if len(parts) > 1 else '/'
    except:
        return '{"error":"parse_failed"}'
    
    if path == '/' or path == '/status':
        return json.dumps(controller.status())
    
    elif path == '/start':
        controller.start()
        return json.dumps({"action": "start", "target": controller.target})
    
    elif path.startswith('/target/'):
        try:
            temp = float(path.split('/')[-1])
            controller.start(temp)
            return json.dumps({"action": "target", "target": controller.target})
        except:
            return '{"error":"invalid_temperature"}'
    
    elif path == '/stop':
        controller.stop()
        return json.dumps({"action": "stop"})
    
    elif path == '/reset':
        controller.stop()
        controller.safety.emergency_count = 0
        return json.dumps({"action": "reset"})
    
    elif path == '/blink':
        led = Pin("LED", Pin.OUT)
        for i in range(5):
            led.on(); time.sleep(0.2)
            led.off(); time.sleep(0.2)
        led.on()
        return json.dumps({"action": "blink"})
    
    elif path == '/calibrate':
        # Raw sensor readings for calibration
        return json.dumps({
            "surface": {
                "temp_c": controller.surface.read(),
                "resistance": controller.surface.read_resistance(),
                "raw": controller.surface.read_raw(),
            },
            "heatsink": {
                "temp_c": controller.heatsink.read(),
                "resistance": controller.heatsink.read_resistance(),
                "raw": controller.heatsink.read_raw(),
            },
            "ambient": {
                "temp_c": controller.ambient.read(),
                "resistance": controller.ambient.read_resistance(),
                "raw": controller.ambient.read_raw(),
            },
        })
    
    else:
        return '{"error":"not_found"}'


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 40)
    print("SOMA Thermal Controller v0.1.0")
    print("Soul: Ai (愛)")
    print("=" * 40)
    
    # Connect WiFi
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print(f"Connecting to {WIFI_SSID}...")
        wlan.connect(WIFI_SSID, WIFI_PASS)
        for i in range(20):
            if wlan.isconnected():
                break
            time.sleep(0.5)
    
    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        print(f"WiFi: {ip}")
    else:
        ip = "0.0.0.0"
        print("WiFi: FAILED (running offline)")
    
    # Init controller
    controller = ThermalController()
    print(f"Target: {controller.target}°C")
    print(f"Safety: hard max {TEMP_HARD_MAX_C}°C")
    
    # Start HTTP server (non-blocking)
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', HTTP_PORT))
    srv.listen(1)
    srv.setblocking(False)
    print(f"HTTP: http://{ip}:{HTTP_PORT}/")
    print("=" * 40)
    print("Ready. Endpoints:")
    print("  GET  /status     — read all sensors")
    print("  GET  /start      — begin heating")
    print("  GET  /target/33  — set target temp")
    print("  GET  /stop       — stop heating")
    print("  GET  /reset      — clear emergency")
    print("  GET  /calibrate  — raw sensor data")
    print("  GET  /blink      — flash LED")
    print("=" * 40)
    
    # Main loop
    loop_count = 0
    while True:
        # Run control loop
        controller.tick()
        
        # Check for HTTP requests (non-blocking)
        try:
            cl, addr = srv.accept()
            try:
                request = cl.recv(1024).decode()
                body = handle_request(request, controller)
                response = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\n\r\n{body}"
                cl.send(response.encode())
            except Exception as e:
                print(f"HTTP error: {e}")
            finally:
                cl.close()
        except OSError:
            pass  # No pending connections — normal
        
        # Print status every 5 seconds
        loop_count += 1
        if loop_count % (5 * CONTROL_LOOP_HZ) == 0:
            s = controller.status()
            print(f"[{s['state']}] "
                  f"Surface: {s['surface_c']}°C | "
                  f"Heatsink: {s['heatsink_c']}°C | "
                  f"Ambient: {s['ambient_c']}°C | "
                  f"Duty: {s['duty_pct']}%")
        
        # Control loop timing
        time.sleep(1.0 / CONTROL_LOOP_HZ)


# Auto-start
if __name__ == "__main__":
    main()
