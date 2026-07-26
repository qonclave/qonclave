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
byte icon_person[8][13] = {
  { 0,0,1,1,1,1,1,1,1,1,0,0,0 },
  { 0,1,0,0,0,0,0,0,0,0,1,0,0 },
  { 1,0,0,1,0,0,0,0,1,0,0,1,0 },
  { 1,0,0,1,0,0,0,0,1,0,0,1,0 },
  { 1,0,0,0,0,0,0,0,0,0,0,1,0 },
  { 1,0,1,0,0,0,0,0,0,1,0,1,0 },
  { 0,1,0,1,1,1,1,1,1,0,1,0,0 },
  { 0,0,1,1,1,1,1,1,1,1,0,0,0 }
};

byte icon_phone[8][13] = {
  { 0,0,0,1,1,1,1,1,1,0,0,0,0 },
  { 0,0,0,1,0,0,0,0,1,0,0,0,0 },
  { 0,0,0,1,0,1,1,0,1,0,0,0,0 },
  { 0,0,0,1,0,1,1,0,1,0,0,0,0 },
  { 0,0,0,1,0,0,0,0,1,0,0,0,0 },
  { 0,0,0,1,0,0,0,0,1,0,0,0,0 },
  { 0,0,0,1,0,1,1,0,1,0,0,0,0 },
  { 0,0,0,1,1,1,1,1,1,0,0,0,0 }
};

byte icon_cat[8][13] = {
  { 1,0,0,0,0,0,0,0,0,0,0,1,0 },
  { 1,1,0,0,0,0,0,0,0,0,1,1,0 },
  { 1,0,1,1,1,1,1,1,1,1,0,1,0 },
  { 1,0,1,0,0,0,0,0,0,1,0,1,0 },
  { 1,0,0,1,0,0,0,0,1,0,0,1,0 },
  { 1,0,0,0,0,1,1,0,0,0,0,1,0 },
  { 0,1,0,0,1,0,0,1,0,0,1,0,0 },
  { 0,0,1,1,1,1,1,1,1,1,0,0,0 }
};

byte icon_dog[8][13] = {
  { 0,0,1,1,1,1,1,1,1,1,0,0,0 },
  { 0,1,1,0,0,0,0,0,0,1,1,0,0 },
  { 1,1,0,1,0,0,0,0,1,0,1,1,0 },
  { 1,1,0,1,0,0,0,0,1,0,1,1,0 },
  { 1,1,0,0,0,1,1,0,0,0,1,1,0 },
  { 1,1,0,0,1,1,1,1,0,0,1,1,0 },
  { 0,1,0,0,0,0,0,0,0,0,1,0,0 },
  { 0,0,1,1,1,1,1,1,1,1,0,0,0 }
};

byte icon_clock[8][13] = {
  { 0,0,0,1,1,1,1,1,1,0,0,0,0 },
  { 0,0,1,0,0,1,0,0,0,1,0,0,0 },
  { 0,1,0,0,0,1,0,0,0,0,1,0,0 },
  { 1,0,0,0,0,1,0,0,0,0,0,1,0 },
  { 1,0,0,0,0,1,1,1,1,0,0,1,0 },
  { 0,1,0,0,0,0,0,0,0,0,1,0,0 },
  { 0,0,1,0,0,0,0,0,0,1,0,0,0 },
  { 0,0,0,1,1,1,1,1,1,0,0,0,0 }
};

byte icon_cup[8][13] = {
  { 0,0,1,0,0,1,0,0,1,0,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,1,1,1,1,1,1,1,1,0,0,0,0 },
  { 0,1,0,0,0,0,0,0,1,1,1,0,0 },
  { 0,1,0,0,0,0,0,0,1,0,1,0,0 },
  { 0,1,0,0,0,0,0,0,1,1,1,0,0 },
  { 0,0,1,0,0,0,0,1,0,0,0,0,0 },
  { 0,0,0,1,1,1,1,0,0,0,0,0,0 }
};

byte icon_plant[8][13] = {
  { 0,0,0,0,1,0,0,1,0,0,0,0,0 },
  { 0,0,0,1,1,1,1,1,1,0,0,0,0 },
  { 0,0,1,1,0,1,1,0,1,1,0,0,0 },
  { 0,0,0,0,0,1,1,0,0,0,0,0,0 },
  { 0,0,1,1,1,1,1,1,1,1,0,0,0 },
  { 0,0,0,1,0,0,0,0,1,0,0,0,0 },
  { 0,0,0,1,0,0,0,0,1,0,0,0,0 },
  { 0,0,0,0,1,1,1,1,0,0,0,0,0 }
};

byte icon_clear[8][13] = {
  { 0,0,0,0,0,0,0,0,0,0,0,1,0 },
  { 0,0,0,0,0,0,0,0,0,0,1,0,0 },
  { 0,0,0,0,0,0,0,0,0,1,0,0,0 },
  { 1,0,0,0,0,0,0,0,1,0,0,0,0 },
  { 0,1,0,0,0,0,0,1,0,0,0,0,0 },
  { 0,0,1,0,0,0,1,0,0,0,0,0,0 },
  { 0,0,0,1,0,1,0,0,0,0,0,0,0 },
  { 0,0,0,0,1,0,0,0,0,0,0,0,0 }
};

void set_led_state_handler(String state) {
  currentObject = state;
  if (state == "person") {
    matrix.renderBitmap(icon_person, 8, 13);
  } else if (state == "cell phone") {
    matrix.renderBitmap(icon_phone, 8, 13);
  } else if (state == "cat") {
    matrix.renderBitmap(icon_cat, 8, 13);
  } else if (state == "dog") {
    matrix.renderBitmap(icon_dog, 8, 13);
  } else if (state == "clock") {
    matrix.renderBitmap(icon_clock, 8, 13);
  } else if (state == "cup") {
    matrix.renderBitmap(icon_cup, 8, 13);
  } else if (state == "potted plant") {
    matrix.renderBitmap(icon_plant, 8, 13);
  } else {
    // Clear / safe checkmark
    matrix.renderBitmap(icon_clear, 8, 13);
  }
}

void setup() {
  matrix.begin();
  matrix.renderBitmap(icon_clear, 8, 13);

  Bridge.begin();
  Bridge.provide("set_led_state", set_led_state_handler);

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
