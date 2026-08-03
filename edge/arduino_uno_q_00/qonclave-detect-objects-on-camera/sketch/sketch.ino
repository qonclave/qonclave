/*
 * sketch.ino — Arduino UNO Q 12x8 LED Matrix Object Display & Knob Control
 * 
 * Displays bitmap icons on the onboard 12x8 LED Matrix when objects are detected
 * by camera (e.g. Smiley face for person, phone icon for cell phone, etc.).
 * Reads Potentiometer Knob on A0 to control camera confidence threshold.
 */

#include "Arduino_LED_Matrix.h"
#include "Arduino_RouterBridge.h"
#include "src/BNO08xOrientation.h"

ArduinoLEDMatrix matrix;
BNO08xOrientation::Config orientationConfig = {10, 9, 8, 1000000, 20000};
BNO08xOrientation orientation(orientationConfig);
const int KNOB_PIN = A0;

// Dual H-bridge motor inputs.
const uint8_t L1_PIN = D2;
const uint8_t L2_PIN = D3;
const uint8_t R1_PIN = D4;
const uint8_t R2_PIN = D5;

int smoothedKnob = 500;
int lastSentPercentage = -1;
String currentObject = "clear";
bool movementActive = false;
unsigned long movementStopAt = 0;
bool turningByAngle = false;
float turnTargetDegrees = 0.0f;
float turnProgressDegrees = 0.0f;
float turnLastAngle = 0.0f;
float yawSignForLeft = 0.0f;
uint32_t turnLastSample = 0;
unsigned long turnLastSampleAt = 0;
unsigned long turnLastMovementAt = 0;
unsigned long turnStartedAt = 0;
unsigned long turnDeadlineAt = 0;
int turnCommandDirection = 0;
int turnDriveDirection = 0;
uint8_t turnDuty = 0;
uint8_t turnSettledSamples = 0;
bool turnOutputOn = false;
unsigned long lastKnobReadAt = 0;

constexpr int TURN_LEFT = 1;
constexpr int TURN_RIGHT = -1;
constexpr float TURN_TOLERANCE_DEGREES = 1.0f;
constexpr float YAW_DIRECTION_DETECTION_DEGREES = 0.25f;
constexpr float TURN_MOVEMENT_DETECTION_DEGREES = 0.15f;
constexpr uint8_t TURN_SETTLE_SAMPLES = 5;
constexpr uint8_t TURN_MIN_DUTY = 145;
constexpr uint8_t TURN_MAX_DUTY = 255;
constexpr unsigned long TURN_PWM_PERIOD_US = 10000;  // 100 Hz
constexpr unsigned long ORIENTATION_STALE_TIMEOUT_MS = 500;
constexpr unsigned long TURN_STALL_TIMEOUT_MS = 600;
// Open-loop fallback calibration. A turn magnitude is always expressed in
// degrees; it is converted to motor run time only when orientation is absent.
// Tune this value against the actual robot, battery, and floor surface.
constexpr unsigned long TURN_FALLBACK_MS_PER_DEGREE = 12;

// 12x8 Bitmap Icons (1 = LED ON, 0 = LED OFF; 13th column is hardware alignment padding)

byte icon_clear[8][13] = {
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,1,0,0,0 },
  { 0,0,0,0,0,0,0,0,1,0,0,0,0 },
  { 0,0,0,0,0,0,0,1,0,0,0,0,0 },
  { 0,1,0,0,0,0,1,0,0,0,0,0,0 },
  { 0,0,1,0,0,1,0,0,0,0,0,0,0 },
  { 0,0,0,1,1,0,0,0,0,0,0,0,0 },
  { 0,0,0,0,0,0,0,0,0,0,0,0,0 }
};

void set_led_state_handler(String state) {
  currentObject = state;
  matrix.renderBitmap(icon_clear, 8, 13);
}

void set_custom_led_array_handler(String bitstring) {
  if (bitstring.length() < 96) return;
  byte frame[8][13];
  int idx = 0;
  for (int r = 0; r < 8; r++) {
    for (int c = 0; c < 12; c++) {
      frame[r][c] = (bitstring.charAt(idx) == '1') ? 1 : 0;
      idx++;
    }
    frame[r][12] = 0; // 13th column alignment pad for UNO Q Zephyr
  }
  matrix.renderBitmap(frame, 8, 13);
}

void set_motor_pins(bool l1, bool l2, bool r1, bool r2) {
  digitalWrite(L1_PIN, l1 ? HIGH : LOW);
  digitalWrite(L2_PIN, l2 ? HIGH : LOW);
  digitalWrite(R1_PIN, r1 ? HIGH : LOW);
  digitalWrite(R2_PIN, r2 ? HIGH : LOW);
}

void stop_robot() {
  set_motor_pins(false, false, false, false);
  movementActive = false;
  turningByAngle = false;
  turnDriveDirection = 0;
  turnDuty = 0;
  turnOutputOn = false;
}

float shortestAngularDelta(float from, float to) {
  float delta = to - from;
  if (delta > 180.0f) {
    delta -= 360.0f;
  } else if (delta < -180.0f) {
    delta += 360.0f;
  }
  return delta;
}

void set_turn_output(int direction, bool enabled) {
  if (!enabled || direction == 0) {
    set_motor_pins(false, false, false, false);
  } else if (direction == TURN_LEFT) {
    set_motor_pins(true, false, false, true);
  } else {
    set_motor_pins(false, true, true, false);
  }
}

void update_turn_pwm() {
  if (!movementActive || !turningByAngle || turnDriveDirection == 0 ||
      turnDuty == 0) {
    if (turnOutputOn) {
      set_turn_output(0, false);
      turnOutputOn = false;
    }
    return;
  }

  const unsigned long phase = micros() % TURN_PWM_PERIOD_US;
  const bool outputOn =
      (phase * 255UL) < (static_cast<unsigned long>(turnDuty) *
                         TURN_PWM_PERIOD_US);
  if (outputOn != turnOutputOn) {
    set_turn_output(turnDriveDirection, outputOn);
    turnOutputOn = outputOn;
  }
}

void start_timed_turn_fallback(int direction, float degrees) {
  const unsigned long duration = max(
      1UL, static_cast<unsigned long>(ceilf(fabsf(degrees) *
                                           TURN_FALLBACK_MS_PER_DEGREE)));
  turningByAngle = false;
  turnDriveDirection = direction;
  set_turn_output(direction, true);
  turnOutputOn = true;
  movementStopAt = millis() + duration;
  movementActive = true;
}

void update_turn_controller() {
  const uint32_t sample = orientation.sampleCount();
  if (sample == turnLastSample || !orientation.available()) {
    if (static_cast<long>(millis() - turnDeadlineAt) >= 0) {
      stop_robot();
    } else if (millis() - turnLastSampleAt >=
               ORIENTATION_STALE_TIMEOUT_MS) {
      const float sensorRemainingDegrees =
          max(0.0f, turnTargetDegrees - turnProgressDegrees);
      const float elapsedEstimateDegrees =
          static_cast<float>(millis() - turnStartedAt) /
          static_cast<float>(TURN_FALLBACK_MS_PER_DEGREE);
      const float timedRemainingDegrees =
          max(0.0f, turnTargetDegrees - elapsedEstimateDegrees);
      const float remainingDegrees =
          min(sensorRemainingDegrees, timedRemainingDegrees);
      const int fallbackDirection =
          turnDriveDirection != 0 ? turnDriveDirection : turnCommandDirection;
      start_timed_turn_fallback(fallbackDirection, remainingDegrees);
    }
    return;
  }

  const float angle = orientation.angleDegrees();
  const float yawDelta = shortestAngularDelta(turnLastAngle, angle);
  turnLastAngle = angle;
  turnLastSample = sample;
  turnLastSampleAt = millis();

  if (fabsf(yawDelta) >= TURN_MOVEMENT_DETECTION_DEGREES) {
    turnLastMovementAt = millis();
  }

  // Learn the installed sensor's yaw polarity from real robot motion. This
  // makes the controller independent of sensor mounting orientation.
  if (yawSignForLeft == 0.0f &&
      fabsf(yawDelta) >= YAW_DIRECTION_DETECTION_DEGREES &&
      turnDriveDirection != 0) {
    yawSignForLeft = (yawDelta > 0.0f ? 1.0f : -1.0f) *
                     static_cast<float>(turnDriveDirection);
  }

  if (yawSignForLeft != 0.0f) {
    const float leftPositiveDelta = yawDelta * yawSignForLeft;
    turnProgressDegrees +=
        leftPositiveDelta * static_cast<float>(turnCommandDirection);
  }

  const float error = turnTargetDegrees - turnProgressDegrees;
  const float absoluteError = fabsf(error);

  // A stationary, energized motor only heats and hums. Stop safely when the
  // sensor confirms that commanded correction is not producing motion.
  if (turnDriveDirection != 0 &&
      millis() - turnLastMovementAt >= TURN_STALL_TIMEOUT_MS) {
    stop_robot();
    return;
  }

  if (absoluteError <= TURN_TOLERANCE_DEGREES) {
    turnDriveDirection = 0;
    turnDuty = 0;
    ++turnSettledSamples;
    if (turnSettledSamples >= TURN_SETTLE_SAMPLES) {
      stop_robot();
    }
    return;
  }

  turnSettledSamples = 0;
  turnDriveDirection =
      error > 0.0f ? turnCommandDirection : -turnCommandDirection;

  // Proportional speed control: approach quickly when far away, then use
  // short low-power pulses near the target for fine correction.
  const int requestedDuty =
      TURN_MIN_DUTY + static_cast<int>(absoluteError * 5.0f);
  turnDuty = static_cast<uint8_t>(
      constrain(requestedDuty, TURN_MIN_DUTY, TURN_MAX_DUTY));
}

// LEFT/RIGHT magnitudes are degrees relative to the current sensor angle.
// FORWARD/BACKWARD magnitudes remain durations in seconds.
bool move_robot(String direction, int magnitude) {
  direction.trim();
  direction.toUpperCase();
  magnitude = constrain(magnitude, 1, 360);

  stop_robot();

  if (direction == "FORWARD") {
    set_motor_pins(true, false, true, false);
  } else if (direction == "BACKWARD") {
    set_motor_pins(false, true, false, true);
  } else if (direction == "RIGHT" || direction == "LEFT") {
    // The turn output is selected below after choosing closed-loop sensor
    // control or the time-based fallback.
  } else {
    return false;
  }

  if (direction == "LEFT" || direction == "RIGHT") {
    turnCommandDirection = direction == "LEFT" ? TURN_LEFT : TURN_RIGHT;
    if (!orientation.available()) {
      start_timed_turn_fallback(turnCommandDirection,
                                static_cast<float>(magnitude));
      return true;
    }

    turningByAngle = true;
    turnTargetDegrees = static_cast<float>(magnitude);
    turnProgressDegrees = 0.0f;
    turnLastAngle = orientation.angleDegrees();
    turnLastSample = orientation.sampleCount();
    turnLastSampleAt = millis();
    turnLastMovementAt = millis();
    turnStartedAt = millis();
    turnDeadlineAt =
        millis() + max(5000UL, static_cast<unsigned long>(magnitude) * 150UL);
    turnDriveDirection = turnCommandDirection;
    turnDuty = TURN_MAX_DUTY;
    turnSettledSamples = 0;
    turnOutputOn = false;
  } else {
    turningByAngle = false;
    movementStopAt =
        millis() + (static_cast<unsigned long>(magnitude) * 1000UL);
  }
  movementActive = true;
  return true;
}

void setup() {
  pinMode(L1_PIN, OUTPUT);
  pinMode(L2_PIN, OUTPUT);
  pinMode(R1_PIN, OUTPUT);
  pinMode(R2_PIN, OUTPUT);
  stop_robot();

  matrix.begin();
  matrix.renderBitmap(icon_clear, 8, 13);

  Bridge.begin();
  orientation.begin();
  Bridge.provide("set_led_state", set_led_state_handler);
  Bridge.provide("set_custom_led_array", set_custom_led_array_handler);
  Bridge.provide("move_robot", move_robot);
  Bridge.provide("stop_robot", stop_robot);

  smoothedKnob = analogRead(KNOB_PIN);
}

void loop() {
  Bridge.update();

  if (movementActive) {
    if (turningByAngle) {
      update_turn_controller();
      update_turn_pwm();
    } else if (static_cast<long>(millis() - movementStopAt) >= 0) {
      stop_robot();
    }
  }

  if (millis() - lastKnobReadAt >= 15) {
    lastKnobReadAt = millis();
    int rawKnob = analogRead(KNOB_PIN);
    smoothedKnob = (0.1 * rawKnob) + (0.9 * smoothedKnob);

    int percentage = map(smoothedKnob, 0, 1023, 0, 100);
    percentage = constrain(percentage, 0, 100);

    if (abs(percentage - lastSentPercentage) >= 2) {
      lastSentPercentage = percentage;
      Bridge.call("on_knob_change", String(percentage));
    }
  }

  delay(1);
}
