// SPDX-License-Identifier: MPL-2.0

#include <SPI.h>
#include "src/bno08x_driver/SparkFun_BNO08x_Arduino_Library.h"
#include <Arduino_RouterBridge.h>

constexpr uint8_t BNO08X_CS_PIN = 10;
constexpr uint8_t BNO08X_INT_PIN = 9;
constexpr uint8_t BNO08X_RESET_PIN = 8;
constexpr uint32_t BNO08X_SPI_SPEED_HZ = 1000000;
constexpr uint32_t REPORT_INTERVAL_US = 100000;  // 10 Hz per report
constexpr unsigned long CONSOLE_INTERVAL_MS = 1000;

BNO08x bno08x;
sh2_SensorValue_t sensorValue;
bool sensorReady = false;
bool resetRecoveryPending = false;
bool awaitingRecoveryData = false;
unsigned long lastRetryAt = 0;
unsigned long resetRecoveryAt = 0;
unsigned long lastConsolePrintAt[256] = {};
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
  Serial.print("[BNO085] reports: ");
  Serial.println(calibrated && raw ? "enabled" : "FAILED");
}

bool beginSensor() {
  Serial.println("[BNO085] initializing SPI...");
  const bool ready =
      bno08x.beginSPI(BNO08X_CS_PIN, BNO08X_INT_PIN, BNO08X_RESET_PIN,
                      BNO08X_SPI_SPEED_HZ, SPI);
  Serial.println(ready ? "[BNO085] SPI ready" : "[BNO085] SPI FAILED");
  return ready;
}

void printXYZ(const char *name, float x, float y, float z,
              const char *unit) {
  Serial.print(name);
  Serial.print(" x=");
  Serial.print(x, 4);
  Serial.print(" y=");
  Serial.print(y, 4);
  Serial.print(" z=");
  Serial.print(z, 4);
  Serial.print(" ");
  Serial.println(unit);
}

void printRawXYZ(const char *name, int16_t x, int16_t y, int16_t z) {
  Serial.print(name);
  Serial.print(" x=");
  Serial.print(x);
  Serial.print(" y=");
  Serial.print(y);
  Serial.print(" z=");
  Serial.println(z);
}

void printQuaternion(const char *name, float i, float j, float k, float real) {
  Serial.print(name);
  Serial.print(" i=");
  Serial.print(i, 4);
  Serial.print(" j=");
  Serial.print(j, 4);
  Serial.print(" k=");
  Serial.print(k, 4);
  Serial.print(" real=");
  Serial.println(real, 4);
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  Bridge.begin();
  Serial.println("[BNO085] sensor test started");

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
      Serial.println("[BNO085] retrying...");
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
        Serial.println("[BNO085] recovery confirmed by sensor data");
        awaitingRecoveryData = false;
        resetRecoveryPending = false;
        // Continue below and print the packet which confirmed recovery.
      } else if (deadlineReached(resetRecoveryAt)) {
        Serial.println("[BNO085] no data; rebuilding SH-2/SPI session");
        sensorReady = bno08x.reconnectSPI();
        if (!sensorReady) {
          Serial.println("[BNO085] reconnect failed; returning to init retry");
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
      Serial.println("[BNO085] quiet interval complete; restoring reports");
      enableReports();
      awaitingRecoveryData = true;
      resetRecoveryAt = millis() + RECOVERY_DATA_TIMEOUT_MS;
      delay(10);
      return;
    }
  }

  if (bno08x.wasReset()) {
    Serial.println("[BNO085] reset detected; waiting for SPI to settle");
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

  const unsigned long now = millis();
  if (now - lastConsolePrintAt[sensorValue.sensorId] < CONSOLE_INTERVAL_MS) {
    return;
  }
  lastConsolePrintAt[sensorValue.sensorId] = now;

  switch (sensorValue.sensorId) {
    case SH2_ACCELEROMETER:
      printXYZ("accel", sensorValue.un.accelerometer.x,
               sensorValue.un.accelerometer.y, sensorValue.un.accelerometer.z,
               "m/s2");
      break;
    case SH2_GYROSCOPE_CALIBRATED:
      printXYZ("gyro", sensorValue.un.gyroscope.x,
               sensorValue.un.gyroscope.y, sensorValue.un.gyroscope.z,
               "rad/s");
      break;
    case SH2_MAGNETIC_FIELD_CALIBRATED:
      printXYZ("mag", sensorValue.un.magneticField.x,
               sensorValue.un.magneticField.y,
               sensorValue.un.magneticField.z, "uT");
      break;
    case SH2_LINEAR_ACCELERATION:
      printXYZ("linear_accel", sensorValue.un.linearAcceleration.x,
               sensorValue.un.linearAcceleration.y,
               sensorValue.un.linearAcceleration.z, "m/s2");
      break;
    case SH2_GRAVITY:
      printXYZ("gravity", sensorValue.un.gravity.x, sensorValue.un.gravity.y,
               sensorValue.un.gravity.z, "m/s2");
      break;
    case SH2_ROTATION_VECTOR:
      printQuaternion("rotation", sensorValue.un.rotationVector.i,
                      sensorValue.un.rotationVector.j,
                      sensorValue.un.rotationVector.k,
                      sensorValue.un.rotationVector.real);
      break;
    case SH2_GAME_ROTATION_VECTOR:
      printQuaternion("game_rotation", sensorValue.un.gameRotationVector.i,
                      sensorValue.un.gameRotationVector.j,
                      sensorValue.un.gameRotationVector.k,
                      sensorValue.un.gameRotationVector.real);
      break;
    case SH2_GEOMAGNETIC_ROTATION_VECTOR:
      printQuaternion("geomag_rotation", sensorValue.un.geoMagRotationVector.i,
                      sensorValue.un.geoMagRotationVector.j,
                      sensorValue.un.geoMagRotationVector.k,
                      sensorValue.un.geoMagRotationVector.real);
      break;
    case SH2_STEP_COUNTER:
      Serial.print("steps=");
      Serial.println(sensorValue.un.stepCounter.steps);
      break;
    case SH2_STABILITY_CLASSIFIER:
      Serial.print("stability=");
      Serial.println(sensorValue.un.stabilityClassifier.classification);
      break;
    case SH2_RAW_ACCELEROMETER:
      printRawXYZ("raw_accel", sensorValue.un.rawAccelerometer.x,
                  sensorValue.un.rawAccelerometer.y,
                  sensorValue.un.rawAccelerometer.z);
      break;
    case SH2_RAW_GYROSCOPE:
      printRawXYZ("raw_gyro", sensorValue.un.rawGyroscope.x,
                  sensorValue.un.rawGyroscope.y,
                  sensorValue.un.rawGyroscope.z);
      break;
    case SH2_RAW_MAGNETOMETER:
      printRawXYZ("raw_mag", sensorValue.un.rawMagnetometer.x,
                  sensorValue.un.rawMagnetometer.y,
                  sensorValue.un.rawMagnetometer.z);
      break;
  }
}
