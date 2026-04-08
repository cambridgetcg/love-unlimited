#!/usr/bin/env python3
"""
SOMA Haptic Glove — Sensor Data Logger
Receives UDP sensor broadcasts and logs to CSV file
Useful for collecting training data or analyzing gesture patterns
"""

import socket
import time
import csv
import sys
from datetime import datetime

UDP_SENSOR_PORT = 9001

def main():
    # Generate timestamped filename
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"glove_data_{timestamp}.csv"

    print(f"SOMA Haptic Glove — Sensor Logger")
    print(f"Listening on UDP port {UDP_SENSOR_PORT}")
    print(f"Writing to: {filename}")
    print("Press Ctrl+C to stop\n")

    # Setup UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', UDP_SENSOR_PORT))

    # CSV file setup
    csv_file = open(filename, 'w', newline='')
    writer = csv.writer(csv_file)

    # Write header
    header = ['timestamp', 'flex_thumb', 'flex_index', 'flex_middle',
              'flex_ring', 'flex_pinky', 'fsr_thumb', 'fsr_index',
              'fsr_middle', 'fsr_ring', 'fsr_pinky']
    writer.writerow(header)

    packet_count = 0
    start_time = time.time()

    try:
        while True:
            # Receive sensor data
            data, addr = sock.recvfrom(1024)
            msg = data.decode('utf-8').strip()

            # Parse CSV format: DATA:F0,F1,F2,F3,F4,P0,P1,P2,P3,P4
            if msg.startswith("DATA:"):
                values = msg[5:].split(',')
                if len(values) == 10:
                    timestamp = time.time() - start_time
                    row = [f"{timestamp:.3f}"] + values
                    writer.writerow(row)
                    csv_file.flush()  # Ensure data is written immediately

                    packet_count += 1
                    if packet_count % 100 == 0:
                        elapsed = time.time() - start_time
                        rate = packet_count / elapsed
                        print(f"Logged {packet_count} packets ({rate:.1f} Hz)")

    except KeyboardInterrupt:
        print(f"\n\nStopping logger...")
        print(f"Total packets logged: {packet_count}")
        elapsed = time.time() - start_time
        print(f"Duration: {elapsed:.1f} seconds")
        print(f"Average rate: {packet_count/elapsed:.1f} Hz")
        print(f"Data saved to: {filename}")
        csv_file.close()
        sys.exit(0)

if __name__ == "__main__":
    main()
