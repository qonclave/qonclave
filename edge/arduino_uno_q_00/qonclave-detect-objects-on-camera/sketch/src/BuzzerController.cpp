// SPDX-License-Identifier: MPL-2.0

#include "BuzzerController.h"

BuzzerController::BuzzerController() : config_(Config{}) {}

BuzzerController::BuzzerController(const Config &config) : config_(config) {}

void BuzzerController::begin() {
  Modulino.begin();
  buzzer_.begin();
}

void BuzzerController::update() {
  if (isBuzzing_ && toneDuration_ > 0) {
    if (millis() - toneStartTime_ >= toneDuration_) {
      noTone();
    }
  }
}

void BuzzerController::tone(int frequency, unsigned long duration) {
  if (frequency <= 0) {
    frequency = config_.defaultFrequency;
  }
  if (duration > 0) {
    buzzer_.tone(frequency, duration);
    toneStartTime_ = millis();
    toneDuration_ = duration;
    isBuzzing_ = true;
  } else {
    buzzer_.tone(frequency,1);
    isBuzzing_ = true;
    toneDuration_ = 0;
  }
}

void BuzzerController::noTone() {
  buzzer_.noTone();
  isBuzzing_ = false;
  toneDuration_ = 0;
}

void BuzzerController::stop() {
  noTone();
}

bool BuzzerController::isBuzzing() const {
  return isBuzzing_;
}
