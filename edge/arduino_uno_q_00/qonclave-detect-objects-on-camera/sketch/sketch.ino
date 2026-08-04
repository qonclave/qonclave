/*
 * sketch.ino — Arduino UNO Q 12x8 LED Matrix Object Display & Knob Control
 * 
 * Displays bitmap icons on the onboard 12x8 LED Matrix when objects are detected
 * by camera (e.g. Smiley face for person, phone icon for cell phone, etc.).
 * Reads Potentiometer Knob on A0 to control camera confidence threshold.
 */

#include "Arduino_LED_Matrix.h"
#include "Arduino_RouterBridge.h"
#include "src/BNO08xOrientation.h"
#include "src/MotorController.h"

ArduinoLEDMatrix matrix;
BNO08xOrientation::Config orientationConfig = {10, 9, 8, 1000000, 20000};
BNO08xOrientation orientation(orientationConfig);
MotorController::Config motorConfig = {D2, D3, D4, D5, 12};
MotorController motors(motorConfig, orientation);
const int KNOB_PIN = A0;

int smoothedKnob = 500;
int lastSentPercentage = -1;
String currentObject = "clear";
unsigned long lastKnobReadAt = 0;
unsigned long lastImuStatusAt = 0;
uint32_t lastImuSample = 0;

// 12x8 Bitmap Icons (1 = LED ON, 0 = LED OFF; 13th column is hardware alignment padding)

byte icon_clear[8][13] = {
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,1,0,0,0 },
  { 0,0,0,0,0,0,0,0,1,0,0,0,0 },
  { 0,0,0,0,0,0,0,1,0,0,0,0,0 },
  { 0,1,0,0,0,0,1,0,0,0,0,0,0 },
  { 0,0,1,0,0,1,0,0,0,0,0,0,0 },
  { 0,0,0,1,1,0,0,0,0,0,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 }
};

void set_led_state_handler(String state) {
  currentObject = state;
  matrix.renderBitmap(icon_clear, 8, 13);
}

void set_custom_led_array_handler(String bitstring) {
  if (bitstring.length() < 96) return;
  byte frame[8][13];
  int idx = 0;
  for (int r = 0; r < 8; r++) {
    for (int c = 0; c < 12; c++) {
      frame[r][c] = (bitstring.charAt(idx) == '1') ? 1 : 0;
      idx++;
    }
    frame[r][12] = 0; // 13th column alignment pad for UNO Q Zephyr
  }
  matrix.renderBitmap(frame, 8, 13);
}

void stop_robot() {
  motors.stop();
}

bool move_robot(String direction, int magnitude) {
  return motors.move(direction, magnitude);
}

bool robot_motion_active() {
  return motors.isMoving();
}

void setup() {
  Serial.begin(115200);
  if (Serial) Serial.println("[IMU] Starting BNO08x diagnostics");

  motors.begin();

  matrix.begin();
  matrix.renderBitmap(icon_clear, 8, 13);

  Bridge.begin();
  orientation.begin();
  Bridge.provide("set_led_state", set_led_state_handler);
  Bridge.provide("set_custom_led_array", set_custom_led_array_handler);
  Bridge.provide("move_robot", move_robot);
  Bridge.provide("stop_robot", stop_robot);
  Bridge.provide("robot_motion_active", robot_motion_active);

  smoothedKnob = analogRead(KNOB_PIN);
}

void loop() {
  Bridge.update();
  motors.update();

  const uint32_t imuSample = orientation.sampleCount();
  if (Serial && imuSample != lastImuSample && orientation.available() &&
      millis() - lastImuStatusAt >= 250) {
    lastImuSample = imuSample;
    lastImuStatusAt = millis();
    Serial.print("[IMU] angle_deg=");
    Serial.print(orientation.angleDegrees(), 2);
    Serial.print(" sample=");
    Serial.println(imuSample);
  } else if (Serial && !orientation.ready() &&
             millis() - lastImuStatusAt >= 2000) {
    lastImuStatusAt = millis();
    Serial.println("[IMU] ERROR sensor not connected; retrying");
  }

  if (millis() - lastKnobReadAt >= 15) {
    lastKnobReadAt = millis();
    int rawKnob = analogRead(KNOB_PIN);
    smoothedKnob = (0.1 * rawKnob) + (0.9 * smoothedKnob);

    int percentage = map(smoothedKnob, 0, 1023, 0, 100);
    percentage = constrain(percentage, 0, 100);

    if (abs(percentage - lastSentPercentage) >= 2) {
      lastSentPercentage = percentage;
      Bridge.call("on_knob_change", String(percentage));
    }
  }

  delay(1);
}
