// SPDX-License-Identifier: MPL-2.0

#pragma once

#include <Arduino.h>
#include <Modulino.h>

class BuzzerController {
 public:
  struct Config {
    int defaultFrequency = 440;
  };

  BuzzerController();
  explicit BuzzerController(const Config &config);

  void begin();
  void update();
  void tone(int frequency, unsigned long duration = 0);
  void noTone();
  void stop();
  bool isBuzzing() const;

 private:
  Config config_;
  ModulinoBuzzer buzzer_;
  unsigned long toneStartTime_ = 0;
  unsigned long toneDuration_ = 0;
  bool isBuzzing_ = false;
};
