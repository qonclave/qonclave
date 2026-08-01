/*
 * sketch.ino — Arduino UNO Q 12x8 LED Matrix Object Display & Knob Control
 * 
 * Displays bitmap icons on the onboard 12x8 LED Matrix when objects are detected
 * by camera (e.g. Smiley face for person, phone icon for cell phone, etc.).
 * Reads Potentiometer Knob on A0 to control camera confidence threshold.
 */

#include "Arduino_LED_Matrix.h"
#include "Arduino_RouterBridge.h"

ArduinoLEDMatrix matrix;
const int KNOB_PIN = A0;

// Dual H-bridge motor inputs.
const uint8_t L1_PIN = D2;
const uint8_t L2_PIN = D3;
const uint8_t R1_PIN = D4;
const uint8_t R2_PIN = D5;

int smoothedKnob = 500;
int lastSentPercentage = -1;
String currentObject = "clear";
bool movementActive = false;
unsigned long movementStopAt = 0;

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

void set_motor_pins(bool l1, bool l2, bool r1, bool r2) {
  digitalWrite(L1_PIN, l1 ? HIGH : LOW);
  digitalWrite(L2_PIN, l2 ? HIGH : LOW);
  digitalWrite(R1_PIN, r1 ? HIGH : LOW);
  digitalWrite(R2_PIN, r2 ? HIGH : LOW);
}

void stop_robot() {
  set_motor_pins(false, false, false, false);
  movementActive = false;
}

// Starts a movement without blocking Bridge processing. Magnitude is currently
// interpreted as a duration in seconds and is constrained to 1..360.
void move_robot(String direction, int magnitude) {
  direction.trim();
  direction.toUpperCase();
  magnitude = constrain(magnitude, 1, 360);

  stop_robot();

  if (direction == "FORWARD") {
    set_motor_pins(true, false, true, false);
  } else if (direction == "BACKWARD") {
    set_motor_pins(false, true, false, true);
  } else if (direction == "RIGHT") {
    set_motor_pins(false, true, true, false);
  } else if (direction == "LEFT") {
    set_motor_pins(true, false, false, true);
  } else {
    return;
  }

  movementStopAt = millis() + (static_cast<unsigned long>(magnitude) * 1000UL);
  movementActive = true;
}

void setup() {
  pinMode(L1_PIN, OUTPUT);
  pinMode(L2_PIN, OUTPUT);
  pinMode(R1_PIN, OUTPUT);
  pinMode(R2_PIN, OUTPUT);
  stop_robot();

  matrix.begin();
  matrix.renderBitmap(icon_clear, 8, 13);

  Bridge.begin();
  Bridge.provide("set_led_state", set_led_state_handler);
  Bridge.provide("set_custom_led_array", set_custom_led_array_handler);
  Bridge.provide("move_robot", move_robot);
  Bridge.provide("stop_robot", stop_robot);

  smoothedKnob = analogRead(KNOB_PIN);
}

void loop() {
  Bridge.update();

  if (movementActive && static_cast<long>(millis() - movementStopAt) >= 0) {
    stop_robot();
  }

  int rawKnob = analogRead(KNOB_PIN);
  smoothedKnob = (0.1 * rawKnob) + (0.9 * smoothedKnob);
  
  int percentage = map(smoothedKnob, 0, 1023, 0, 100);
  percentage = constrain(percentage, 0, 100);

  if (abs(percentage - lastSentPercentage) >= 2) {
    lastSentPercentage = percentage;
    Bridge.call("on_knob_change", String(percentage));
  }

  delay(15);
}
