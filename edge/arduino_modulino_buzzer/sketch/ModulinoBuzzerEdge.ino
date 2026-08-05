/*
 * ModulinoBuzzerEdge.ino — Qonclave Edge Device with Arduino Modulino Buzzer
 * 
 * Subscribes to MQTT topic `qonclave/<DEVICE_ID>/command` (default: qonclave/buzzer-01/command)
 * and controls the Arduino Modulino Buzzer module in response to Hub start/stop commands.
 * 
 * Target Hardware:
 *   - Arduino board with WiFi (UNO R4 WiFi, ESP32, Portenta, etc.)
 *   - Arduino Modulino Buzzer connected via Modulino / Qwiic I2C bus
 * 
 * Required Libraries:
 *   - Arduino_Modulino (https://github.com/arduino-libraries/Arduino_Modulino)
 *   - ArduinoJson
 *   - PubSubClient
 */

#include <Modulino.h>
#if defined(ESP8266) || defined(ESP32)
  #include <WiFi.h>
#else
  #include <WiFiS3.h> // Arduino UNO R4 WiFi
#endif
#include <PubSubClient.h>
#include <ArduinoJson.h>

// --- Configuration ---
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASS     = "YOUR_WIFI_PASSWORD";
const char* MQTT_BROKER   = "192.168.1.100"; // Hub IP address
const int   MQTT_PORT     = 1883;
const char* DEVICE_ID     = "buzzer-01";
const char* COMMAND_TOPIC = "qonclave/buzzer-01/command";

ModulinoBuzzer buzzer;
WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);

unsigned long toneStartTime = 0;
unsigned long toneDuration  = 0;
bool isBuzzing = false;

void callback(char* topic, byte* payload, unsigned int length) {
  StaticJsonDocument<512> doc;
  DeserializationError error = deserializeJson(doc, payload, length);
  
  if (error) {
    Serial.print(F("JSON parse failed: "));
    Serial.println(error.f_str());
    return;
  }

  const char* type   = doc["type"] | "";
  const char* action = doc["action"] | "";
  int frequency      = doc["frequency"] | 440;
  int duration       = doc["duration"] | 0;

  Serial.print(F("[MQTT] Received command: type="));
  Serial.print(type);
  Serial.print(F(" action="));
  Serial.print(action);
  Serial.print(F(" freq="));
  Serial.print(frequency);
  Serial.print(F(" duration="));
  Serial.println(duration);

  // Handle buzzer commands
  if (String(type) == "buzzer" || String(action).length() > 0) {
    String act = String(action);
    act.toLowerCase();

    if (act == "start" || act == "tone") {
      if (frequency <= 0) frequency = 440;
      
      if (duration > 0) {
        buzzer.tone(frequency, duration);
        toneStartTime = millis();
        toneDuration = duration;
        isBuzzing = true;
      } else {
        buzzer.tone(frequency);
        isBuzzing = true;
        toneDuration = 0;
      }
      Serial.print(F("-> Buzzer STARTED at "));
      Serial.print(frequency);
      Serial.println(F(" Hz"));

    } else if (act == "stop" || act == "notone") {
      buzzer.noTone();
      isBuzzing = false;
      toneDuration = 0;
      Serial.println(F("-> Buzzer STOPPED"));
    }
  }
}

void setupWiFi() {
  delay(10);
  Serial.print(F("Connecting to WiFi: "));
  Serial.println(WIFI_SSID);
  
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(F("."));
  }
  
  Serial.println(F("\nWiFi Connected!"));
  Serial.print(F("IP Address: "));
  Serial.println(WiFi.localIP());
}

void reconnectMQTT() {
  while (!mqttClient.connected()) {
    Serial.print(F("Attempting MQTT connection to "));
    Serial.print(MQTT_BROKER);
    Serial.print(F("..."));

    String clientId = "QonclaveBuzzer-";
    clientId += String(random(0xffff), HEX);

    if (mqttClient.connect(clientId.c_str())) {
      Serial.println(F(" connected!"));
      mqttClient.subscribe(COMMAND_TOPIC);
      Serial.print(F("Subscribed to: "));
      Serial.println(COMMAND_TOPIC);
    } else {
      Serial.print(F(" failed, rc="));
      Serial.print(mqttClient.state());
      Serial.println(F(" try again in 5 seconds"));
      delay(5000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000);

  Serial.println(F("=== Qonclave Modulino Buzzer Edge Node ==="));

  // Initialize Modulino hardware & buzzer module
  Modulino.begin();
  buzzer.begin();

  setupWiFi();
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setCallback(callback);
}

void loop() {
  if (!mqttClient.connected()) {
    reconnectMQTT();
  }
  mqttClient.loop();

  // Handle timed continuous tone shutoff if duration was set
  if (isBuzzing && toneDuration > 0) {
    if (millis() - toneStartTime >= toneDuration) {
      buzzer.noTone();
      isBuzzing = false;
      toneDuration = 0;
      Serial.println(F("-> Timed tone finished. Buzzer STOPPED"));
    }
  }
}
