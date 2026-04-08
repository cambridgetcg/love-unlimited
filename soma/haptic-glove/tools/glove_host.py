#!/usr/bin/env python3
"""
SOMA Haptic Glove — Host Controller
Receives UDP sensor data and sends haptic commands via UDP
Minimal tkinter GUI with real-time sensor visualization and haptic control
"""

import socket
import threading
import time
import tkinter as tk
from tkinter import ttk

# Network configuration (must match firmware config.h)
UDP_SENSOR_PORT = 9001  # Listen for sensor data from glove
UDP_HAPTIC_PORT = 9002  # Send haptic commands to glove
BROADCAST_IP = "255.255.255.255"  # For sending commands

# Sensor data state
flex_values = [0] * 5  # Flex sensor readings (0-4095)
fsr_values = [0] * 5   # FSR pressure readings (0-4095)
last_update = 0.0

# UDP sockets
sock_sensor = None
sock_haptic = None

def init_udp():
    """Initialize UDP sockets for sensor receive and haptic send"""
    global sock_sensor, sock_haptic

    # Socket for receiving sensor data
    sock_sensor = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_sensor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock_sensor.bind(('0.0.0.0', UDP_SENSOR_PORT))
    print(f"Listening for sensor data on UDP port {UDP_SENSOR_PORT}")

    # Socket for sending haptic commands
    sock_haptic = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_haptic.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    print(f"Ready to send haptic commands to UDP port {UDP_HAPTIC_PORT}")

def udp_receive_loop():
    """Background thread: continuously receive sensor data from glove"""
    global flex_values, fsr_values, last_update

    while True:
        try:
            data, addr = sock_sensor.recvfrom(1024)
            msg = data.decode('utf-8').strip()

            # Parse CSV format: DATA:F0,F1,F2,F3,F4,P0,P1,P2,P3,P4
            if msg.startswith("DATA:"):
                values = msg[5:].split(',')
                if len(values) == 10:
                    flex_values = [int(v) for v in values[:5]]
                    fsr_values = [int(v) for v in values[5:]]
                    last_update = time.time()
        except Exception as e:
            print(f"UDP receive error: {e}")
            time.sleep(0.1)

def send_haptic_command(cmd):
    """Send haptic command to glove via UDP broadcast"""
    try:
        sock_haptic.sendto(cmd.encode('utf-8'), (BROADCAST_IP, UDP_HAPTIC_PORT))
    except Exception as e:
        print(f"UDP send error: {e}")

class GloveGUI:
    """Minimal tkinter GUI for glove sensor visualization and haptic control"""

    def __init__(self, root):
        self.root = root
        self.root.title("SOMA Haptic Glove — Host Controller")
        self.root.geometry("600x500")

        # Status label
        self.status_label = tk.Label(root, text="Waiting for sensor data...",
                                     font=("Arial", 10), fg="gray")
        self.status_label.pack(pady=5)

        # Sensor visualization frame
        sensor_frame = tk.LabelFrame(root, text="Sensor Data", padx=10, pady=10)
        sensor_frame.pack(padx=10, pady=10, fill="both", expand=True)

        # Flex sensor bars (0-4095 range)
        tk.Label(sensor_frame, text="Flex Sensors (Bend):", font=("Arial", 10, "bold")).grid(
            row=0, column=0, columnspan=6, pady=5)

        self.flex_bars = []
        self.flex_labels = []
        finger_names = ["Thumb", "Index", "Middle", "Ring", "Pinky"]

        for i, name in enumerate(finger_names):
            tk.Label(sensor_frame, text=name, font=("Arial", 9)).grid(row=1, column=i, padx=5)

            canvas = tk.Canvas(sensor_frame, width=80, height=150, bg="white",
                             highlightthickness=1, highlightbackground="gray")
            canvas.grid(row=2, column=i, padx=5, pady=5)
            self.flex_bars.append(canvas)

            label = tk.Label(sensor_frame, text="0", font=("Arial", 8))
            label.grid(row=3, column=i)
            self.flex_labels.append(label)

        # FSR pressure bars (0-4095 range)
        tk.Label(sensor_frame, text="Pressure Sensors (FSR):", font=("Arial", 10, "bold")).grid(
            row=4, column=0, columnspan=6, pady=(15, 5))

        self.fsr_bars = []
        self.fsr_labels = []

        for i, name in enumerate(finger_names):
            tk.Label(sensor_frame, text=name, font=("Arial", 9)).grid(row=5, column=i, padx=5)

            canvas = tk.Canvas(sensor_frame, width=80, height=100, bg="white",
                             highlightthickness=1, highlightbackground="gray")
            canvas.grid(row=6, column=i, padx=5, pady=5)
            self.fsr_bars.append(canvas)

            label = tk.Label(sensor_frame, text="0", font=("Arial", 8))
            label.grid(row=7, column=i)
            self.fsr_labels.append(label)

        # Haptic control frame
        haptic_frame = tk.LabelFrame(root, text="Haptic Control", padx=10, pady=10)
        haptic_frame.pack(padx=10, pady=10, fill="x")

        # Individual finger haptic sliders
        self.haptic_sliders = []
        for i, name in enumerate(finger_names):
            frame = tk.Frame(haptic_frame)
            frame.grid(row=0, column=i, padx=5)

            tk.Label(frame, text=name, font=("Arial", 8)).pack()
            slider = tk.Scale(frame, from_=255, to=0, orient=tk.VERTICAL,
                            length=100, width=15, showvalue=0,
                            command=lambda val, finger=i: self.on_haptic_change(finger, val))
            slider.pack()
            self.haptic_sliders.append(slider)

        # Quick action buttons
        button_frame = tk.Frame(haptic_frame)
        button_frame.grid(row=1, column=0, columnspan=6, pady=10)

        tk.Button(button_frame, text="Pulse All", command=self.pulse_all,
                 bg="#4CAF50", fg="white", padx=10).pack(side=tk.LEFT, padx=5)
        tk.Button(button_frame, text="Stop All", command=self.stop_all,
                 bg="#f44336", fg="white", padx=10).pack(side=tk.LEFT, padx=5)

        # Start update loop
        self.update_display()

    def update_display(self):
        """Update sensor bars with latest data"""
        global flex_values, fsr_values, last_update

        # Update status
        age = time.time() - last_update if last_update > 0 else 999
        if age < 1.0:
            self.status_label.config(text=f"Connected — Last update: {age:.1f}s ago", fg="green")
        else:
            self.status_label.config(text=f"No recent data ({age:.0f}s ago)", fg="red")

        # Update flex bars (0-4095 range -> 0-150px height)
        for i in range(5):
            canvas = self.flex_bars[i]
            canvas.delete("all")
            height = int((flex_values[i] / 4095.0) * 150)
            canvas.create_rectangle(10, 150-height, 70, 150, fill="#2196F3", outline="")
            self.flex_labels[i].config(text=str(flex_values[i]))

        # Update FSR bars (0-4095 range -> 0-100px height)
        for i in range(5):
            canvas = self.fsr_bars[i]
            canvas.delete("all")
            height = int((fsr_values[i] / 4095.0) * 100)
            canvas.create_rectangle(10, 100-height, 70, 100, fill="#FF9800", outline="")
            self.fsr_labels[i].config(text=str(fsr_values[i]))

        # Schedule next update (30 FPS)
        self.root.after(33, self.update_display)

    def on_haptic_change(self, finger, value):
        """Send haptic command when slider changes"""
        intensity = int(value)
        cmd = f"H{finger}{intensity}"
        send_haptic_command(cmd)

    def pulse_all(self):
        """Send pulse command to all fingers"""
        send_haptic_command("P")
        # Reset all sliders to 0
        for slider in self.haptic_sliders:
            slider.set(0)

    def stop_all(self):
        """Stop all haptic feedback"""
        send_haptic_command("S")
        # Reset all sliders to 0
        for slider in self.haptic_sliders:
            slider.set(0)

def main():
    """Initialize UDP and launch GUI"""
    init_udp()

    # Start UDP receive thread
    rx_thread = threading.Thread(target=udp_receive_loop, daemon=True)
    rx_thread.start()

    # Launch GUI
    root = tk.Tk()
    app = GloveGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
