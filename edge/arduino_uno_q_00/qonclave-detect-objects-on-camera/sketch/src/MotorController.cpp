// SPDX-License-Identifier: MPL-2.0

#include "MotorController.h"

MotorController::MotorController(const Config &config,
                                 BNO08xOrientation &orientation)
    : config_(config), orientation_(orientation) {}

void MotorController::begin() {
  pinMode(config_.leftInput1Pin, OUTPUT);
  pinMode(config_.leftInput2Pin, OUTPUT);
  pinMode(config_.rightInput1Pin, OUTPUT);
  pinMode(config_.rightInput2Pin, OUTPUT);
  stop();
}

void MotorController::update() {
  if (!movementActive_) return;

  if (turningByAngle_) {
    updateTurnController();
    updateTurnPwm();
  } else if (static_cast<long>(millis() - movementStopAt_) >= 0) {
    stop();
  }
}

bool MotorController::move(String direction, int magnitude) {
  direction.trim();
  direction.toUpperCase();
  magnitude = constrain(magnitude, 1, 360);

  stop();

  if (direction == "FORWARD") {
    setMotorPins(true, false, true, false);
  } else if (direction == "BACKWARD") {
    setMotorPins(false, true, false, true);
  } else if (direction != "LEFT" && direction != "RIGHT") {
    return false;
  }

  if (direction == "LEFT" || direction == "RIGHT") {
    turnCommandDirection_ = direction == "LEFT" ? TURN_LEFT : TURN_RIGHT;
    if (!orientation_.available()) {
      startTimedTurnFallback(turnCommandDirection_,
                             static_cast<float>(magnitude));
      return true;
    }

    turningByAngle_ = true;
    turnTargetDegrees_ = static_cast<float>(magnitude);
    turnProgressDegrees_ = 0.0f;
    turnLastAngle_ = orientation_.angleDegrees();
    turnLastSample_ = orientation_.sampleCount();
    turnLastSampleAt_ = millis();
    turnLastMovementAt_ = millis();
    turnStartedAt_ = millis();
    turnDeadlineAt_ =
        millis() + max(5000UL, static_cast<unsigned long>(magnitude) * 150UL);
    turnDriveDirection_ = turnCommandDirection_;
    turnDuty_ = TURN_MAX_DUTY;
    turnSettledSamples_ = 0;
    turnOutputOn_ = false;
  } else {
    movementStopAt_ =
        millis() + (static_cast<unsigned long>(magnitude) * 1000UL);
  }

  movementActive_ = true;
  return true;
}

void MotorController::stop() {
  setMotorPins(false, false, false, false);
  movementActive_ = false;
  turningByAngle_ = false;
  turnDriveDirection_ = 0;
  turnDuty_ = 0;
  turnOutputOn_ = false;
}

bool MotorController::isMoving() const {
  return movementActive_;
}

void MotorController::setMotorPins(bool left1, bool left2, bool right1,
                                   bool right2) {
  digitalWrite(config_.leftInput1Pin, left1 ? HIGH : LOW);
  digitalWrite(config_.leftInput2Pin, left2 ? HIGH : LOW);
  digitalWrite(config_.rightInput1Pin, right1 ? HIGH : LOW);
  digitalWrite(config_.rightInput2Pin, right2 ? HIGH : LOW);
}

float MotorController::shortestAngularDelta(float from, float to) {
  float delta = to - from;
  if (delta > 180.0f) {
    delta -= 360.0f;
  } else if (delta < -180.0f) {
    delta += 360.0f;
  }
  return delta;
}

void MotorController::setTurnOutput(int direction, bool enabled) {
  if (!enabled || direction == 0) {
    setMotorPins(false, false, false, false);
  } else if (direction == TURN_LEFT) {
    setMotorPins(true, false, false, true);
  } else {
    setMotorPins(false, true, true, false);
  }
}

void MotorController::updateTurnPwm() {
  if (!movementActive_ || !turningByAngle_ || turnDriveDirection_ == 0 ||
      turnDuty_ == 0) {
    if (turnOutputOn_) {
      setTurnOutput(0, false);
      turnOutputOn_ = false;
    }
    return;
  }

  const unsigned long phase = micros() % TURN_PWM_PERIOD_US;
  const bool outputOn =
      (phase * 255UL) <
      (static_cast<unsigned long>(turnDuty_) * TURN_PWM_PERIOD_US);
  if (outputOn != turnOutputOn_) {
    setTurnOutput(turnDriveDirection_, outputOn);
    turnOutputOn_ = outputOn;
  }
}

void MotorController::startTimedTurnFallback(int direction, float degrees) {
  const unsigned long duration = max(
      1UL, static_cast<unsigned long>(ceilf(
               fabsf(degrees) * static_cast<float>(config_.fallbackMsPerDegree))));
  turningByAngle_ = false;
  turnDriveDirection_ = direction;
  setTurnOutput(direction, true);
  turnOutputOn_ = true;
  movementStopAt_ = millis() + duration;
  movementActive_ = true;
}

void MotorController::updateTurnController() {
  const uint32_t sample = orientation_.sampleCount();
  if (sample == turnLastSample_ || !orientation_.available()) {
    if (static_cast<long>(millis() - turnDeadlineAt_) >= 0) {
      stop();
    } else if (millis() - turnLastSampleAt_ >=
               ORIENTATION_STALE_TIMEOUT_MS) {
      const float sensorRemainingDegrees =
          max(0.0f, turnTargetDegrees_ - turnProgressDegrees_);
      const float elapsedEstimateDegrees =
          static_cast<float>(millis() - turnStartedAt_) /
          static_cast<float>(config_.fallbackMsPerDegree);
      const float timedRemainingDegrees =
          max(0.0f, turnTargetDegrees_ - elapsedEstimateDegrees);
      const float remainingDegrees =
          min(sensorRemainingDegrees, timedRemainingDegrees);
      const int fallbackDirection = turnDriveDirection_ != 0
                                        ? turnDriveDirection_
                                        : turnCommandDirection_;
      startTimedTurnFallback(fallbackDirection, remainingDegrees);
    }
    return;
  }

  const float angle = orientation_.angleDegrees();
  const float yawDelta = shortestAngularDelta(turnLastAngle_, angle);
  turnLastAngle_ = angle;
  turnLastSample_ = sample;
  turnLastSampleAt_ = millis();

  if (fabsf(yawDelta) >= TURN_MOVEMENT_DETECTION_DEGREES) {
    turnLastMovementAt_ = millis();
  }

  if (yawSignForLeft_ == 0.0f &&
      fabsf(yawDelta) >= YAW_DIRECTION_DETECTION_DEGREES &&
      turnDriveDirection_ != 0) {
    yawSignForLeft_ = (yawDelta > 0.0f ? 1.0f : -1.0f) *
                      static_cast<float>(turnDriveDirection_);
  }

  if (yawSignForLeft_ != 0.0f) {
    const float leftPositiveDelta = yawDelta * yawSignForLeft_;
    turnProgressDegrees_ +=
        leftPositiveDelta * static_cast<float>(turnCommandDirection_);
  }

  const float error = turnTargetDegrees_ - turnProgressDegrees_;
  const float absoluteError = fabsf(error);

  if (turnDriveDirection_ != 0 &&
      millis() - turnLastMovementAt_ >= TURN_STALL_TIMEOUT_MS) {
    stop();
    return;
  }

  if (absoluteError <= TURN_TOLERANCE_DEGREES) {
    turnDriveDirection_ = 0;
    turnDuty_ = 0;
    ++turnSettledSamples_;
    if (turnSettledSamples_ >= TURN_SETTLE_SAMPLES) stop();
    return;
  }

  turnSettledSamples_ = 0;
  turnDriveDirection_ =
      error > 0.0f ? turnCommandDirection_ : -turnCommandDirection_;
  const int requestedDuty =
      TURN_MIN_DUTY + static_cast<int>(absoluteError * 5.0f);
  turnDuty_ = static_cast<uint8_t>(
      constrain(requestedDuty, TURN_MIN_DUTY, TURN_MAX_DUTY));
}
