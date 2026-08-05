# Qonclave Edge — Arduino Modulino Buzzer Node

This package implements an Edge device for the Qonclave framework that controls an **Arduino Modulino Buzzer** module in response to commands from the Hub server.

## Hardware Requirements

1. **Arduino Board with Connectivity**:
   - Arduino UNO R4 WiFi, ESP32, Portenta, or Arduino UNO Q
2. **Arduino Modulino Buzzer**:
   - Connected via Modulino / Qwiic I2C cable to the Arduino board.
3. **Reference Library**:
   - Uses the official [Arduino_Modulino](https://github.com/arduino-libraries/Arduino_Modulino) library (`ModulinoBuzzer buzzer`).

## Architecture & MQTT Topic Protocol

The Hub server publishes commands to the MQTT broker on:
```
Topic: qonclave/<device_id>/command
Default Topic: qonclave/buzzer-01/command
```

### Command Payload Format

#### Start Buzzer (Tone)
```json
{
  "type": "buzzer",
  "action": "start",
  "frequency": 880,
  "duration": 0
}
```
- `frequency`: Sound tone frequency in Hz (e.g. `440` for A4 note, `880` for A5, `1000` for Alarm).
- `duration`: Tone duration in milliseconds. Set to `0` for continuous playback until an explicit `stop` command is received.

#### Stop Buzzer
```json
{
  "type": "buzzer",
  "action": "stop"
}
```

---

## Option 1: Standalone Arduino C++ Sketch (`ModulinoBuzzerEdge.ino`)

For microcontrollers like Arduino UNO R4 WiFi or ESP32 running directly:

1. Open `sketch/ModulinoBuzzerEdge.ino` in the Arduino IDE.
2. Install required libraries via Arduino Library Manager:
   - `Arduino_Modulino`
   - `ArduinoJson`
   - `PubSubClient`
3. Edit WiFi and Hub IP credentials in `ModulinoBuzzerEdge.ino`:
   ```cpp
   const char* WIFI_SSID   = "YOUR_WIFI_SSID";
   const char* WIFI_PASS   = "YOUR_WIFI_PASSWORD";
   const char* MQTT_BROKER = "192.168.1.100"; // Hub IP
   const char* DEVICE_ID   = "buzzer-01";
   ```
4. Upload to the Arduino board and open the Serial Monitor (115200 baud).

---

## Option 2: Linux / Arduino UNO Q Python Client (`buzzer_edge.py`)

For edge nodes running Linux or Arduino UNO Q with Python:

1. Install `paho-mqtt`:
   ```bash
   pip install paho-mqtt
   ```
2. Run the client:
   ```bash
   python python/buzzer_edge.py --device-id buzzer-01 --host <HUB_IP>
   ```

---

## Testing from Hub

1. Ensure Mosquitto MQTT broker is running on the Hub:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\hub\setup_mqtt.ps1
   ```
2. Start the Hub server with the `buzzer_alert` app:
   ```bash
   python hub/server.py
   ```
3. Open `http://localhost:8000/user/dashboard` to manually START/STOP the buzzer, select frequencies, or run test scripts!
