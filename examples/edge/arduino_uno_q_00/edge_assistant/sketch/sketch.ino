/*
 * sketch.ino — Qonclave Edge Assistant LED state display
 *
 * Receives assistant state names from Python over Bridge and renders them on the
 * UNO Q onboard LED matrix. THINKING is animated locally as a three-dot spinner.
 * The 13th column is the UNO Q alignment pad.
 */

#include "Arduino_LED_Matrix.h"
#include "Arduino_RouterBridge.h"

ArduinoLEDMatrix matrix;

String currentState = "idle";
unsigned long lastThinkingFrameAt = 0;
int thinkingFrame = 0;
const unsigned long THINKING_FRAME_MS = 160;

byte icon_clear[8][13] = {
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 }
};

byte icon_listening[8][13] = {
  { 0,0,0,0,0,1,1,0,0,0,0,0,0 },
  { 0,0,0,0,1,1,1,1,0,0,0,0,0 },
  { 0,0,0,0,1,1,1,1,0,0,0,0,0 },
  { 0,0,0,0,1,1,1,1,0,0,0,0,0 },
  { 0,0,0,0,0,1,1,0,0,0,0,0,0 },
  { 0,0,0,1,1,1,1,1,1,0,0,0,0 },
  { 0,0,0,0,0,1,1,0,0,0,0,0,0 },
  { 0,0,0,1,1,1,1,1,1,0,0,0,0 }
};

byte icon_speaking[8][13] = {
  { 0,0,1,1,0,0,0,0,0,1,0,0,0 },
  { 0,0,1,1,0,0,0,0,1,0,1,0,0 },
  { 0,0,1,1,0,1,0,1,0,0,1,0,0 },
  { 0,0,1,1,0,1,0,1,0,0,1,0,0 },
  { 0,0,1,1,0,1,0,1,0,0,1,0,0 },
  { 0,0,1,1,0,0,0,0,1,0,1,0,0 },
  { 0,0,1,1,0,0,0,0,0,1,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 }
};

const byte spinnerDots[8][2] = {
  { 1, 5 },
  { 1, 7 },
  { 3, 9 },
  { 5, 9 },
  { 6, 7 },
  { 6, 5 },
  { 5, 3 },
  { 3, 3 }
};

void renderIcon(byte icon[8][13]) {
  matrix.renderBitmap(icon, 8, 13);
}

void clearFrame(byte frame[8][13]) {
  for (int row = 0; row < 8; row++) {
    for (int col = 0; col < 13; col++) {
      frame[row][col] = 0;
    }
  }
}

void renderThinkingFrame() {
  byte frame[8][13];
  clearFrame(frame);

  for (int dot = 0; dot < 3; dot++) {
    int dotIndex = (thinkingFrame + dot * 3) % 8;
    byte row = spinnerDots[dotIndex][0];
    byte col = spinnerDots[dotIndex][1];
    frame[row][col] = 1;
  }

  matrix.renderBitmap(frame, 8, 13);
  thinkingFrame = (thinkingFrame + 1) % 8;
  lastThinkingFrameAt = millis();
}

void set_led_state_handler(String state) {
  state.trim();
  state.toLowerCase();
  currentState = state;

  if (state == "listening") {
    renderIcon(icon_listening);
  } else if (state == "thinking") {
    thinkingFrame = 0;
    renderThinkingFrame();
  } else if (state == "speaking") {
    renderIcon(icon_speaking);
  } else {
    currentState = "idle";
    renderIcon(icon_clear);
  }
}

void set_custom_led_array_handler(String bitstring) {
  if (bitstring.length() < 96) return;
  currentState = "custom";

  byte frame[8][13];
  int idx = 0;
  for (int row = 0; row < 8; row++) {
    for (int col = 0; col < 12; col++) {
      frame[row][col] = (bitstring.charAt(idx) == '1') ? 1 : 0;
      idx++;
    }
    frame[row][12] = 0;
  }

  matrix.renderBitmap(frame, 8, 13);
}

void setup() {
  matrix.begin();
  matrix.renderBitmap(icon_clear, 8, 13);

  Bridge.begin();
  Bridge.provide("set_led_state", set_led_state_handler);
  Bridge.provide("set_custom_led_array", set_custom_led_array_handler);
}

void loop() {
  Bridge.update();

  if (currentState == "thinking" && millis() - lastThinkingFrameAt >= THINKING_FRAME_MS) {
    renderThinkingFrame();
  }

  delay(1);
}
