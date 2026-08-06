// SPDX-License-Identifier: MPL-2.0

#include "BuzzerController.h"

// Imagine Dragons - "Believer" Melody (Frequencies in Hz + Durations in ms)
static const Note BELIEVER_MELODY[] = {
  // Intro / Verse hook: "First things first, I'ma say all the words inside my head..."
  { 370, 220 }, // F#4
  { 370, 220 }, // F#4
  { 370, 220 }, // F#4
  { 494, 300 }, // B4
  { 494, 220 }, // B4
  { 494, 220 }, // B4
  { 440, 220 }, // A4
  { 392, 220 }, // G4
  { 370, 300 }, // F#4
  { 330, 300 }, // E4
  { 294, 450 }, // D4
  {   0, 150 }, // REST

  // "I'm fired up and tired of the way that things have been, oh-ooh..."
  { 370, 220 }, // F#4
  { 370, 220 }, // F#4
  { 370, 220 }, // F#4
  { 494, 300 }, // B4
  { 494, 220 }, // B4
  { 494, 220 }, // B4
  { 440, 220 }, // A4
  { 392, 220 }, // G4
  { 370, 300 }, // F#4
  { 330, 300 }, // E4
  { 370, 300 }, // F#4
  { 494, 450 }, // B4
  {   0, 250 }, // REST

  // Chorus: "Pain! You made me a, you made me a believer, believer!"
  { 494, 500 }, // B4 (PAIN!)
  {   0, 100 }, // REST
  { 370, 220 }, // F#4
  { 494, 220 }, // B4
  { 494, 220 }, // B4
  { 494, 220 }, // B4
  { 494, 220 }, // B4
  { 554, 220 }, // C#5
  { 587, 280 }, // D5
  { 554, 280 }, // C#5
  { 494, 350 }, // B4
  {   0, 100 }, // REST
  { 494, 350 }, // B4 (believer!)
  {   0, 200 }, // REST

  // "Pain! You break me down, you build me up, believer, believer!"
  { 494, 500 }, // B4 (PAIN!)
  {   0, 100 }, // REST
  { 370, 220 }, // F#4
  { 494, 220 }, // B4
  { 494, 220 }, // B4
  { 494, 220 }, // B4
  { 494, 220 }, // B4
  { 554, 220 }, // C#5
  { 587, 280 }, // D5
  { 554, 280 }, // C#5
  { 494, 350 }, // B4
  {   0, 100 }, // REST
  { 494, 450 }  // B4 (believer!)
};

BuzzerController::BuzzerController() : config_(Config{}) {}

BuzzerController::BuzzerController(const Config &config) : config_(config) {}

void BuzzerController::begin() {
  Modulino.begin();
  buzzer_.begin();
}

void BuzzerController::update() {
  if (isPlayingMelody_) {
    if (millis() - toneStartTime_ >= currentNoteDuration_) {
      melodyIndex_++;
      if (melodyIndex_ < melodyLength_) {
        playCurrentNote();
      } else {
        noTone();
      }
    }
  } else if (isBuzzing_ && toneDuration_ > 0) {
    if (millis() - toneStartTime_ >= toneDuration_) {
      noTone();
    }
  }
}

void BuzzerController::tone(int frequency, unsigned long duration) {
  isPlayingMelody_ = false;
  if (frequency <= 0) {
    playBeliever();
    return;
  }
  if (duration > 0) {
    buzzer_.tone(frequency, duration);
    toneStartTime_ = millis();
    toneDuration_ = duration;
    isBuzzing_ = true;
  } else {
    buzzer_.tone(frequency, 0);
    isBuzzing_ = true;
    toneDuration_ = 0;
  }
}

void BuzzerController::playBeliever() {
  currentMelody_ = BELIEVER_MELODY;
  melodyLength_ = sizeof(BELIEVER_MELODY) / sizeof(BELIEVER_MELODY[0]);
  melodyIndex_ = 0;
  isPlayingMelody_ = true;
  isBuzzing_ = true;
  toneDuration_ = 0;
  playCurrentNote();
}

void BuzzerController::playCurrentNote() {
  if (!isPlayingMelody_ || melodyIndex_ >= melodyLength_) {
    noTone();
    return;
  }
  const Note &note = currentMelody_[melodyIndex_];
  if (note.frequency == 0) {
    buzzer_.noTone();
  } else {
    buzzer_.tone(note.frequency, note.durationMs);
  }
  toneStartTime_ = millis();
  currentNoteDuration_ = note.durationMs;
}

void BuzzerController::noTone() {
  buzzer_.noTone();
  isBuzzing_ = false;
  isPlayingMelody_ = false;
  toneDuration_ = 0;
  melodyIndex_ = 0;
  melodyLength_ = 0;
}

void BuzzerController::stop() {
  noTone();
}

bool BuzzerController::isBuzzing() const {
  return isBuzzing_;
}
