# SPDX-License-Identifier: MPL-2.0

from arduino.app_utils import App


# The sensor is read by the MCU sketch. Keep the MPU side alive so the sketch
# can run as a normal Arduino App.
App.run()

