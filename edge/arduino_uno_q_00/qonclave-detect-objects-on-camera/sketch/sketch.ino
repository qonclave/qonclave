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

int smoothedKnob = 500;
int lastSentPercentage = -1;
String currentObject = "clear";

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

void setup() {
  matrix.begin();
  matrix.renderBitmap(icon_clear, 8, 13);

  Bridge.begin();
  Bridge.provide("set_led_state", set_led_state_handler);
  Bridge.provide("set_custom_led_array", set_custom_led_array_handler);

  smoothedKnob = analogRead(KNOB_PIN);
}

void loop() {
  Bridge.update();

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
