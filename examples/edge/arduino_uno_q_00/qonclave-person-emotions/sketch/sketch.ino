/*
 * sketch.ino — Arduino UNO Q 12x8 LED Matrix Emotion Display & Knob Control
 * 
 * Renders 12x8 facial expressions when emotions are received from MPU over RouterBridge.
 * Reads Potentiometer Knob on A0 to sync sensitivity threshold with Python.
 */

#include "Arduino_LED_Matrix.h"
#include "Arduino_RouterBridge.h"
#include "gallery.h"

ArduinoLEDMatrix matrix;
const int KNOB_PIN = A0;

int smoothedKnob = 500;
int lastSentPercentage = -1;
String currentEmotion = "clear";

// Custom 12x8 Bitmaps for Surprise, Angry, Neutral, Fear (13th column is hardware alignment padding)
byte face_surprise[8][13] = {
  { 0,0,1,1,0,0,0,0,1,1,0,0,0 },
  { 0,1,0,0,1,0,0,1,0,0,1,0,0 },
  { 0,1,1,1,1,0,0,1,1,1,1,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,0,0,0,1,1,1,1,0,0,0,0,0 },
  { 0,0,0,1,0,0,0,0,1,0,0,0,0 },
  { 0,0,0,1,0,0,0,0,1,0,0,0,0 },
  { 0,0,0,0,1,1,1,1,0,0,0,0,0 }
};

byte face_angry[8][13] = {
  { 1,0,0,0,0,0,0,0,0,0,0,1,0 },
  { 0,1,1,0,0,0,0,0,0,1,1,0,0 },
  { 0,0,0,1,1,0,0,1,1,0,0,0,0 },
  { 0,0,1,1,0,0,0,0,1,1,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,0,1,1,1,1,1,1,1,1,0,0,0 },
  { 0,1,0,0,0,0,0,0,0,0,1,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 }
};

byte face_neutral[8][13] = {
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,0,1,1,0,0,0,0,1,1,0,0,0 },
  { 0,0,1,1,0,0,0,0,1,1,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,1,1,1,1,1,1,1,1,1,1,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 }
};

byte face_fear[8][13] = {
  { 0,0,0,1,1,0,0,1,1,0,0,0,0 },
  { 0,0,1,0,0,0,0,0,0,1,0,0,0 },
  { 0,1,1,1,0,0,0,0,1,1,1,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,1,1,0,0,1,1,0,0,1,1,0,0 },
  { 0,0,0,1,1,0,0,1,1,0,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 }
};

void set_emotion_handler(String emotion) {
  currentEmotion = emotion;
  if (emotion == "happy") {
    matrix.loadFrame(LEDMATRIX_SMILE);
  } else if (emotion == "sad") {
    matrix.loadFrame(LEDMATRIX_FROWN);
  } else if (emotion == "surprise") {
    matrix.renderBitmap(face_surprise, 8, 13);
  } else if (emotion == "angry") {
    matrix.renderBitmap(face_angry, 8, 13);
  } else if (emotion == "neutral") {
    matrix.renderBitmap(face_neutral, 8, 13);
  } else if (emotion == "fear") {
    matrix.renderBitmap(face_fear, 8, 13);
  } else {
    matrix.loadFrame(LEDMATRIX_CHECKMARK);
  }
}

void setup() {
  matrix.begin();
  matrix.loadFrame(LEDMATRIX_CHECKMARK);

  Bridge.begin();
  Bridge.provide("set_emotion", set_emotion_handler);

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
