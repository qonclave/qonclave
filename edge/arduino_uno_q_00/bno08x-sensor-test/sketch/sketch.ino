// SPDX-License-Identifier: MPL-2.0

#include <Arduino_RouterBridge.h>

#include "src/BNO08xOrientation.h"

BNO08xOrientation orientation;

void setup() {
  Serial.begin(115200);
  delay(1500);

  Bridge.begin();
  orientation.begin();
}

void loop() {
  Bridge.update();

  static uint32_t lastSample = 0;
  const uint32_t sample = orientation.sampleCount();
  if (sample != lastSample && orientation.available()) {
    lastSample = sample;
    Serial.println(orientation.angleDegrees(), 2);
  }

  delay(2);
}
