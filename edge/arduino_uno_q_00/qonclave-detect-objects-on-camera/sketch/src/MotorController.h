// SPDX-License-Identifier: MPL-2.0

#pragma once

#include <Arduino.h>

#include "BNO08xOrientation.h"

class MotorController {
 public:
  struct Config {
    uint8_t leftInput1Pin;
    uint8_t leftInput2Pin;
    uint8_t rightInput1Pin;
    uint8_t rightInput2Pin;
    unsigned long fallbackMsPerDegree = 12;
  };

  MotorController(const Config &config, BNO08xOrientation &orientation);

  void begin();
  void update();
  bool move(String direction, int magnitude);
  void stop();

 private:
  static constexpr int TURN_LEFT = 1;
  static constexpr int TURN_RIGHT = -1;
  static constexpr float TURN_TOLERANCE_DEGREES = 1.0f;
  static constexpr float YAW_DIRECTION_DETECTION_DEGREES = 0.25f;
  static constexpr float TURN_MOVEMENT_DETECTION_DEGREES = 0.15f;
  static constexpr uint8_t TURN_SETTLE_SAMPLES = 5;
  static constexpr uint8_t TURN_MIN_DUTY = 145;
  static constexpr uint8_t TURN_MAX_DUTY = 255;
  static constexpr unsigned long TURN_PWM_PERIOD_US = 10000;
  static constexpr unsigned long ORIENTATION_STALE_TIMEOUT_MS = 500;
  static constexpr unsigned long TURN_STALL_TIMEOUT_MS = 600;

  void setMotorPins(bool left1, bool left2, bool right1, bool right2);
  void setTurnOutput(int direction, bool enabled);
  void updateTurnPwm();
  void updateTurnController();
  void startTimedTurnFallback(int direction, float degrees);
  static float shortestAngularDelta(float from, float to);

  Config config_;
  BNO08xOrientation &orientation_;
  bool movementActive_ = false;
  unsigned long movementStopAt_ = 0;
  bool turningByAngle_ = false;
  float turnTargetDegrees_ = 0.0f;
  float turnProgressDegrees_ = 0.0f;
  float turnLastAngle_ = 0.0f;
  float yawSignForLeft_ = 0.0f;
  uint32_t turnLastSample_ = 0;
  unsigned long turnLastSampleAt_ = 0;
  unsigned long turnLastMovementAt_ = 0;
  unsigned long turnStartedAt_ = 0;
  unsigned long turnDeadlineAt_ = 0;
  int turnCommandDirection_ = 0;
  int turnDriveDirection_ = 0;
  uint8_t turnDuty_ = 0;
  uint8_t turnSettledSamples_ = 0;
  bool turnOutputOn_ = false;
};
