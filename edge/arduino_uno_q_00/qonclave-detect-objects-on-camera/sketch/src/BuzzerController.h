// SPDX-License-Identifier: MPL-2.0

#pragma once

#include <Arduino.h>
#include <Modulino.h>

struct Note {
  uint16_t frequency;
  uint16_t durationMs;
};

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
  void playBeliever();
  void noTone();
  void stop();
  bool isBuzzing() const;

 private:
  void playCurrentNote();

  Config config_;
  ModulinoBuzzer buzzer_;
  unsigned long toneStartTime_ = 0;
  unsigned long toneDuration_ = 0;
  unsigned long currentNoteDuration_ = 0;
  bool isBuzzing_ = false;

  bool isPlayingMelody_ = false;
  const Note *currentMelody_ = nullptr;
  size_t melodyIndex_ = 0;
  size_t melodyLength_ = 0;
};
