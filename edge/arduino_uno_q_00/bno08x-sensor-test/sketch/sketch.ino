// SPDX-License-Identifier: MPL-2.0

#include <SPI.h>
#include <math.h>
#include "src/bno08x_driver/SparkFun_BNO08x_Arduino_Library.h"
#include <Arduino_RouterBridge.h>

constexpr uint8_t BNO08X_CS_PIN = 10;
constexpr uint8_t BNO08X_INT_PIN = 9;
constexpr uint8_t BNO08X_RESET_PIN = 8;
constexpr uint32_t BNO08X_SPI_SPEED_HZ = 1000000;
constexpr uint32_t REPORT_INTERVAL_US = 100000;  // 10 Hz per report
constexpr unsigned long CONSOLE_INTERVAL_MS = 1000;
constexpr float RADIANS_TO_DEGREES = 180.0f / PI;

BNO08x bno08x;
sh2_SensorValue_t sensorValue;
bool sensorReady = false;
bool resetRecoveryPending = false;
bool awaitingRecoveryData = false;
unsigned long lastRetryAt = 0;
unsigned long resetRecoveryAt = 0;
unsigned long lastConsolePrintAt[256] = {};
bool orientationReferenceSet = false;
float referenceYawRadians = 0.0f;
constexpr unsigned long RESET_SETTLE_MS = 1500;
constexpr unsigned long RECOVERY_DATA_TIMEOUT_MS = 2000;

bool deadlineReached(unsigned long deadline) {
  return static_cast<long>(millis() - deadline) >= 0;
}

void enableReports() {
  bno08x.enableReport(SH2_ACCELEROMETER, REPORT_INTERVAL_US);
  bno08x.enableReport(SH2_GYROSCOPE_CALIBRATED, REPORT_INTERVAL_US);
  const bool calibrated =
      bno08x.enableReport(SH2_MAGNETIC_FIELD_CALIBRATED, REPORT_INTERVAL_US);
  bno08x.enableReport(SH2_LINEAR_ACCELERATION, REPORT_INTERVAL_US);
  bno08x.enableReport(SH2_GRAVITY, REPORT_INTERVAL_US);
  bno08x.enableReport(SH2_ROTATION_VECTOR, REPORT_INTERVAL_US);
  bno08x.enableReport(SH2_GAME_ROTATION_VECTOR, REPORT_INTERVAL_US);
  bno08x.enableReport(SH2_GEOMAGNETIC_ROTATION_VECTOR, REPORT_INTERVAL_US);
  bno08x.enableReport(SH2_STEP_COUNTER, REPORT_INTERVAL_US);
  bno08x.enableReport(SH2_STABILITY_CLASSIFIER, REPORT_INTERVAL_US);
  bno08x.enableReport(SH2_RAW_ACCELEROMETER, REPORT_INTERVAL_US);
  bno08x.enableReport(SH2_RAW_GYROSCOPE, REPORT_INTERVAL_US);
  const bool raw = bno08x.enableReport(SH2_RAW_MAGNETOMETER,
                                       REPORT_INTERVAL_US);
  (void)calibrated;
  (void)raw;
}

bool beginSensor() {
  const bool ready =
      bno08x.beginSPI(BNO08X_CS_PIN, BNO08X_INT_PIN, BNO08X_RESET_PIN,
                      BNO08X_SPI_SPEED_HZ, SPI);
  return ready;
}

float quaternionYaw(float i, float j, float k, float real) {
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

void printRelativeOrientation(const sh2_RotationVectorWAcc_t &rotation) {
  const float yaw = quaternionYaw(rotation.i, rotation.j, rotation.k,
                                  rotation.real);

  if (!orientationReferenceSet) {
    referenceYawRadians = yaw;
    orientationReferenceSet = true;
  }

  float angle = (yaw - referenceYawRadians) * RADIANS_TO_DEGREES;
  if (angle < 0.0f) {
    angle += 360.0f;
  } else if (angle >= 360.0f) {
    angle -= 360.0f;
  }

  Serial.println(angle, 2);
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  Bridge.begin();

  sensorReady = beginSensor();
  if (sensorReady) {
    enableReports();
  }
}

void loop() {
  Bridge.update();
  bool eventReady = false;

  if (!sensorReady) {
    if (millis() - lastRetryAt >= 2000) {
      lastRetryAt = millis();
      sensorReady = beginSensor();
      if (sensorReady) {
        enableReports();
      }
    }
    delay(10);
    return;
  }

  if (resetRecoveryPending) {
    // Keep servicing SH-2 while recovering so a second reset is observed.
    const bool gotEvent = bno08x.getSensorEvent();
    if (gotEvent) {
      sensorValue = bno08x.sensorValue;
      eventReady = true;
    }

    // Clear reset notifications while recovering. The BNO085 can repeat the
    // same SH-2 reset notification until reports are configured, so these are
    // not reliable evidence of a second physical reset. A real data packet is
    // the recovery authority below.
    (void)bno08x.wasReset();

    if (awaitingRecoveryData) {
      if (gotEvent) {
        awaitingRecoveryData = false;
        resetRecoveryPending = false;
        // Continue below and print the packet which confirmed recovery.
      } else if (deadlineReached(resetRecoveryAt)) {
        sensorReady = bno08x.reconnectSPI();
        if (!sensorReady) {
          awaitingRecoveryData = false;
          resetRecoveryPending = false;
          lastRetryAt = millis();
          return;
        }
        enableReports();
        awaitingRecoveryData = true;
        resetRecoveryAt = millis() + RECOVERY_DATA_TIMEOUT_MS;
        return;
      } else {
        delay(10);
        return;
      }
    } else if (!deadlineReached(resetRecoveryAt)) {
      delay(10);
      return;
    } else {
      enableReports();
      awaitingRecoveryData = true;
      resetRecoveryAt = millis() + RECOVERY_DATA_TIMEOUT_MS;
      delay(10);
      return;
    }
  }

  if (bno08x.wasReset()) {
    resetRecoveryPending = true;
    awaitingRecoveryData = false;
    resetRecoveryAt = millis() + RESET_SETTLE_MS;
    return;
  }

  if (!eventReady && !bno08x.getSensorEvent()) {
    delay(2);
    return;
  }
  if (!eventReady) {
    sensorValue = bno08x.sensorValue;
  }

  // The fused rotation vector combines accelerometer, gyroscope, and
  // magnetometer data. Its first reading after app startup defines zero.
  if (sensorValue.sensorId == SH2_ROTATION_VECTOR) {
    printRelativeOrientation(sensorValue.un.rotationVector);
    return;
  }

}
