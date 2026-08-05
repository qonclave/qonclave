// SPDX-License-Identifier: MPL-2.0

#include "BNO08xOrientation.h"
#include "DebugSerial.h"

#include <math.h>

namespace {
constexpr float RADIANS_TO_DEGREES = 57.2957795f;
}

BNO08xOrientation::BNO08xOrientation(SPIClass &spi)
    : BNO08xOrientation(Config{}, spi) {}

BNO08xOrientation::BNO08xOrientation(const Config &config, SPIClass &spi)
    : config_(config), spi_(spi) {
  k_mutex_init(&stateMutex_);
}

bool BNO08xOrientation::begin() {
  if (threadStarted_) {
    QONCLAVE_DEBUG(if (Serial) Serial.println("[IMU] ERROR orientation thread already started"));
    return false;
  }

  const k_tid_t threadId = k_thread_create(
      &thread_, threadStack_, K_KERNEL_STACK_SIZEOF(threadStack_), threadEntry,
      this, nullptr, nullptr, K_PRIO_PREEMPT(THREAD_PRIORITY), 0, K_NO_WAIT);
  if (threadId == nullptr) {
    QONCLAVE_DEBUG(if (Serial) Serial.println("[IMU] ERROR failed to create orientation thread"));
    return false;
  }

  k_thread_name_set(&thread_, "bno08x_orientation");
  threadStarted_ = true;
  QONCLAVE_DEBUG(if (Serial) Serial.println("[IMU] Orientation thread started"));
  return true;
}

bool BNO08xOrientation::ready() {
  k_mutex_lock(&stateMutex_, K_FOREVER);
  const bool value = sensorReady_;
  k_mutex_unlock(&stateMutex_);
  return value;
}

bool BNO08xOrientation::available() {
  k_mutex_lock(&stateMutex_, K_FOREVER);
  const bool value = angleAvailable_;
  k_mutex_unlock(&stateMutex_);
  return value;
}

float BNO08xOrientation::angleDegrees() {
  k_mutex_lock(&stateMutex_, K_FOREVER);
  const float value = angleDegrees_;
  k_mutex_unlock(&stateMutex_);
  return value;
}

uint32_t BNO08xOrientation::sampleCount() {
  k_mutex_lock(&stateMutex_, K_FOREVER);
  const uint32_t value = sampleCount_;
  k_mutex_unlock(&stateMutex_);
  return value;
}

void BNO08xOrientation::resetZero() {
  k_mutex_lock(&stateMutex_, K_FOREVER);
  zeroRequested_ = true;
  angleAvailable_ = false;
  k_mutex_unlock(&stateMutex_);
}

void BNO08xOrientation::threadEntry(void *instance, void *, void *) {
  static_cast<BNO08xOrientation *>(instance)->run();
}

void BNO08xOrientation::run() {
  bool eventReady = false;
  bool sensorConnected = false;
  lastRetryAt_ = millis() - RETRY_INTERVAL_MS;

  while (true) {
    eventReady = false;

    if (!sensorConnected) {
      if (millis() - lastRetryAt_ >= RETRY_INTERVAL_MS) {
        lastRetryAt_ = millis();
        sensorConnected = connectSensor();

        k_mutex_lock(&stateMutex_, K_FOREVER);
        sensorReady_ = sensorConnected;
        k_mutex_unlock(&stateMutex_);

        if (sensorConnected) {
          QONCLAVE_DEBUG(if (Serial) Serial.println("[IMU] BNO08x connected over SPI"));
          enableOrientationReport();
        } else {
          QONCLAVE_DEBUG(if (Serial) Serial.println("[IMU] ERROR SPI connection failed"));
        }
      }
      k_msleep(10);
      continue;
    }

    if (resetRecoveryPending_) {
      const bool gotEvent = sensor_.getSensorEvent();
      if (gotEvent) {
        sensorValue_ = sensor_.sensorValue;
        eventReady = true;
      }

      (void)sensor_.wasReset();

      if (awaitingRecoveryData_) {
        if (gotEvent) {
          awaitingRecoveryData_ = false;
          resetRecoveryPending_ = false;
        } else if (deadlineReached(resetRecoveryAt_)) {
          sensorConnected = sensor_.reconnectSPI();
          k_mutex_lock(&stateMutex_, K_FOREVER);
          sensorReady_ = sensorConnected;
          k_mutex_unlock(&stateMutex_);

          if (!sensorConnected) {
            QONCLAVE_DEBUG(if (Serial) Serial.println("[IMU] ERROR SPI recovery reconnect failed"));
            awaitingRecoveryData_ = false;
            resetRecoveryPending_ = false;
            lastRetryAt_ = millis();
            continue;
          }
          QONCLAVE_DEBUG(if (Serial) Serial.println("[IMU] SPI recovery reconnect succeeded"));
          enableOrientationReport();
          awaitingRecoveryData_ = true;
          resetRecoveryAt_ = millis() + RECOVERY_DATA_TIMEOUT_MS;
          continue;
        } else {
          k_msleep(10);
          continue;
        }
      } else if (!deadlineReached(resetRecoveryAt_)) {
        k_msleep(10);
        continue;
      } else {
        enableOrientationReport();
        awaitingRecoveryData_ = true;
        resetRecoveryAt_ = millis() + RECOVERY_DATA_TIMEOUT_MS;
        k_msleep(10);
        continue;
      }
    }

    if (sensor_.wasReset()) {
      QONCLAVE_DEBUG(if (Serial) Serial.println("[IMU] WARNING sensor reset detected; recovering"));
      resetRecoveryPending_ = true;
      awaitingRecoveryData_ = false;
      resetRecoveryAt_ = millis() + RESET_SETTLE_MS;
      continue;
    }

    if (!eventReady && !sensor_.getSensorEvent()) {
      k_msleep(2);
      continue;
    }
    if (!eventReady) {
      sensorValue_ = sensor_.sensorValue;
    }

    if (sensorValue_.sensorId == SH2_ROTATION_VECTOR) {
      processRotation(sensorValue_.un.rotationVector);
    }
  }
}

bool BNO08xOrientation::connectSensor() {
  return sensor_.beginSPI(config_.csPin, config_.interruptPin, config_.resetPin,
                          config_.spiSpeedHz, spi_);
}

void BNO08xOrientation::enableOrientationReport() {
  if (sensor_.enableReport(SH2_ROTATION_VECTOR, config_.reportIntervalUs)) {
    QONCLAVE_DEBUG(if (Serial) {
      Serial.print("[IMU] Rotation-vector report enabled, interval_us=");
      Serial.println(config_.reportIntervalUs);
    });
  } else {
    QONCLAVE_DEBUG(if (Serial) Serial.println("[IMU] ERROR failed to enable rotation-vector report"));
  }
}

void BNO08xOrientation::processRotation(
    const sh2_RotationVectorWAcc_t &rotation) {
  const float yaw = quaternionYaw(rotation.i, rotation.j, rotation.k,
                                  rotation.real);

  k_mutex_lock(&stateMutex_, K_FOREVER);
  if (zeroRequested_ || !referenceSet_) {
    referenceYawRadians_ = yaw;
    referenceSet_ = true;
    zeroRequested_ = false;
  }

  float angle = (yaw - referenceYawRadians_) * RADIANS_TO_DEGREES;
  if (angle < 0.0f) {
    angle += 360.0f;
  } else if (angle >= 360.0f) {
    angle -= 360.0f;
  }

  angleDegrees_ = angle;
  angleAvailable_ = true;
  ++sampleCount_;
  k_mutex_unlock(&stateMutex_);
}

bool BNO08xOrientation::deadlineReached(unsigned long deadline) const {
  return static_cast<long>(millis() - deadline) >= 0;
}

float BNO08xOrientation::quaternionYaw(float i, float j, float k, float real) {
  const float norm = sqrtf(i * i + j * j + k * k + real * real);
  if (norm <= 0.0f) {
    return 0.0f;
  }

  i /= norm;
  j /= norm;
  k /= norm;
  real /= norm;

  return atan2f(2.0f * (real * k + i * j),
                1.0f - 2.0f * (j * j + k * k));
}
