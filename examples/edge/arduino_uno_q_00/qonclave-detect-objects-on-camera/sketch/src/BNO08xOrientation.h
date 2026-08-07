// SPDX-License-Identifier: MPL-2.0

#pragma once

#include <Arduino.h>
#include <SPI.h>
#include <zephyr/kernel.h>

#include "bno08x_driver/SparkFun_BNO08x_Arduino_Library.h"

class BNO08xOrientation {
 public:
  struct Config {
    uint8_t csPin = 10;
    uint8_t interruptPin = 9;
    uint8_t resetPin = 8;
    uint32_t spiSpeedHz = 1000000;
    uint32_t reportIntervalUs = 100000;  // 10 Hz
  };

  explicit BNO08xOrientation(SPIClass &spi = SPI);
  BNO08xOrientation(const Config &config, SPIClass &spi = SPI);

  // Starts the background sensor thread. Returns false if already started or
  // if the thread could not be created. Sensor connection retries happen in
  // the background, so use ready() to observe connection state.
  bool begin();

  bool ready();
  bool available();
  float angleDegrees();
  uint32_t sampleCount();

  // Makes the next fused rotation-vector sample the new zero reference.
  void resetZero();

 private:
  static constexpr size_t THREAD_STACK_SIZE = 4096;
  static constexpr int THREAD_PRIORITY = 10;
  static constexpr unsigned long RETRY_INTERVAL_MS = 2000;
  static constexpr unsigned long RESET_SETTLE_MS = 1500;
  static constexpr unsigned long RECOVERY_DATA_TIMEOUT_MS = 2000;

  static void threadEntry(void *instance, void *, void *);
  void run();
  bool connectSensor();
  void enableOrientationReport();
  void processRotation(const sh2_RotationVectorWAcc_t &rotation);
  bool deadlineReached(unsigned long deadline) const;
  static float quaternionYaw(float i, float j, float k, float real);

  Config config_;
  SPIClass &spi_;
  BNO08x sensor_;
  sh2_SensorValue_t sensorValue_{};

  struct k_thread thread_;
  K_KERNEL_STACK_MEMBER(threadStack_, THREAD_STACK_SIZE);
  struct k_mutex stateMutex_;
  bool threadStarted_ = false;

  bool sensorReady_ = false;
  bool angleAvailable_ = false;
  bool zeroRequested_ = true;
  float angleDegrees_ = 0.0f;
  uint32_t sampleCount_ = 0;

  bool referenceSet_ = false;
  float referenceYawRadians_ = 0.0f;
  bool resetRecoveryPending_ = false;
  bool awaitingRecoveryData_ = false;
  unsigned long lastRetryAt_ = 0;
  unsigned long resetRecoveryAt_ = 0;
};
